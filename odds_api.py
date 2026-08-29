"""Sharp bookmaker consensus from the-odds-api, used as a BENCHMARK.

WHY. Every model here is scored against Kalshi, which is the only price we can
see. That makes Kalshi both the thing we are trying to beat and the only judge of
whether we beat it -- and on thin markets (ITF tennis especially) it is not a
sharp price at all. This module adds an independent second opinion: the de-vigged
consensus of 7-8 books.

It earns its place immediately. On the first call it made, eight books had Ben
Shelton at ~65% while our tennis model had his opponent at 64%, and had de Minaur
at ~93% where our model said 63.9% -- both cases where the books agreed with
Kalshi and we were the outlier. That is precisely the check tennis_backtest says
it lacks: "no market benchmark exists here, so this can't answer 'do we beat the
price'."

CREDITS ARE THE BINDING CONSTRAINT. The free tier is 500 requests a month, and
this module is written around that rather than treating it as an afterthought:

  * Cost is per REQUEST, not per event -- one call returns every game for a
    sport, so a daily pull of a whole league costs 1. Never poll on a timer.
  * Every response carries x-requests-remaining; that is recorded and enforced.
    Below RESERVE the module refuses to spend anything, so a human always has
    requests left to debug with.
  * Responses are cached, and the default TTL is hours, not minutes.
  * /v4/sports is free (the API does not bill it) and is used for discovery.

Set ODDS_API_KEY in the environment. No key -> every function no-ops and callers
keep their existing behaviour.
"""

import json
import os
import time
import urllib.parse
import urllib.request
import errlog

_BASE = "https://api.the-odds-api.com/v4"
_TIMEOUT = 25
_CACHE_KEY = "odds_api_state"          # {remaining, used, checked}
_SNAP_KEY = "odds_api_snapshots"       # accumulating benchmark series
# Keep this many requests in reserve, always.
RESERVE = 40
# A cached quote older than this is refetched; anything newer is reused. Six hours
# means at most ~4 pulls a day per sport even if the board rebuilds constantly.
TTL_S = 6 * 3600


def key():
    return os.environ.get("ODDS_API_KEY") or ""


def enabled():
    return bool(key())


def _state():
    try:
        import deep_cache
        return deep_cache.load(_CACHE_KEY)[0] or {}
    except Exception:
        return {}


def _save_state(st):
    try:
        import deep_cache
        deep_cache.save(_CACHE_KEY, st)
    except Exception as _e:
        errlog.note("OAPI-save_state", _e)


def remaining():
    """Requests left this month as last reported by the API, or None if unknown.
    Read from response headers -- there is no free endpoint that reports it."""
    return _state().get("remaining")


def _get(path, params, billed=True):
    """GET with credit accounting. Returns (payload, error). Refuses to spend when
    the reserve would be breached."""
    if not enabled():
        return None, "no ODDS_API_KEY"
    st = _state()
    rem = st.get("remaining")
    if billed and rem is not None and rem <= RESERVE:
        return None, f"refusing to spend: {rem} requests left (reserve {RESERVE})"
    p = dict(params or {})
    p["apiKey"] = key()
    url = f"{_BASE}{path}?{urllib.parse.urlencode(p)}"
    req = urllib.request.Request(url, headers={"User-Agent": "vigil/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            body = json.loads(r.read())
            hdrs = {k.lower(): v for k, v in r.headers.items()}
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    if "x-requests-remaining" in hdrs:
        try:
            st["remaining"] = int(float(hdrs["x-requests-remaining"]))
            st["used"] = int(float(hdrs.get("x-requests-used", 0) or 0))
            st["checked"] = time.time()
            _save_state(st)
        except Exception as _e:
            errlog.note("OAPI-get", _e)
    return body, None


def sports(active_only=True):
    """Available sports. FREE -- the API does not bill this endpoint."""
    d, err = _get("/sports/", {}, billed=False)
    if err or not isinstance(d, list):
        return []
    return [s for s in d if s.get("active")] if active_only else d


def tennis_keys():
    """Sport keys for tennis events currently listed (there are usually only the
    tournaments in progress, and never ITF)."""
    return [s["key"] for s in sports()
            if "tennis" in (s.get("key", "") + s.get("group", "")).lower()]


def _devig(outcomes):
    """One book's outcomes -> {name: fair probability}, vig removed by
    normalising the implied probabilities so they sum to 1."""
    imp = {}
    for o in outcomes or []:
        try:
            price = float(o.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if price > 1.0 and o.get("name"):
            imp[o["name"]] = 1.0 / price
    tot = sum(imp.values())
    if tot <= 0:
        return {}
    return {k: v / tot for k, v in imp.items()}


def consensus(event, market="h2h"):
    """{name: probability} averaged across books, each de-vigged FIRST.

    De-vigging per book before averaging matters: books carry different holds, so
    averaging raw prices lets the widest book drag the consensus."""
    per_book = []
    for b in event.get("bookmakers") or []:
        for m in b.get("markets") or []:
            if m.get("key") != market:
                continue
            fair = _devig(m.get("outcomes"))
            if fair:
                per_book.append(fair)
    if not per_book:
        return {}, 0
    names = set()
    for f in per_book:
        names |= set(f)
    out = {n: sum(f.get(n, 0.0) for f in per_book) / len(per_book) for n in names}
    tot = sum(out.values())
    if tot > 0:
        out = {k: v / tot for k, v in out.items()}
    return out, len(per_book)


def odds(sport_key, regions="us", markets="h2h", ttl=TTL_S, force=False):
    """Events with bookmaker prices for one sport. ONE request per call.

    Cached by (sport, regions, markets); a fresh-enough cache costs nothing."""
    # Disabled means OFF, including the cache. Otherwise pulling the key -- the
    # one switch someone reaches for when this misbehaves -- would leave the
    # board quoting cached odds indefinitely, since without a key the refetch can
    # never succeed and the stale fallback below would serve them forever.
    if not enabled():
        return [], "no ODDS_API_KEY"
    ck = f"odds:{sport_key}:{regions}:{markets}"
    st = _state()
    hit = (st.get("cache") or {}).get(ck)
    if hit and not force and (time.time() - hit.get("t", 0)) < ttl:
        return hit.get("d") or [], None
    d, err = _get(f"/sports/{sport_key}/odds/",
                  {"regions": regions, "markets": markets,
                   "oddsFormat": "decimal"})
    if err:
        # Stale beats nothing when we are merely out of credits for the month --
        # but only up to a point. Day-old prices presented as current are worse
        # than no prices at all on something being bet into.
        if hit and (time.time() - hit.get("t", 0)) < 24 * 3600:
            return hit.get("d") or [], err
        return [], err
    st = _state()
    st.setdefault("cache", {})[ck] = {"t": time.time(), "d": d}
    _save_state(st)
    return d, None


def board(sport_key, **kw):
    """[{start, home, away, probs, books}] -- the consensus per event."""
    evs, err = odds(sport_key, **kw)
    out = []
    for e in evs or []:
        probs, n = consensus(e)
        if not probs:
            continue
        out.append({"start": e.get("commence_time"),
                    "home": e.get("home_team"), "away": e.get("away_team"),
                    "probs": {k: round(v * 100, 1) for k, v in probs.items()},
                    "books": n})
    return out, err


def snapshot(sport_keys, note=""):
    """Pull consensus for each sport and append it to a persistent series, so the
    benchmark accumulates for grading instead of being a one-off read."""
    if not enabled():
        return {"error": "no ODDS_API_KEY"}
    try:
        import deep_cache
        series = deep_cache.load(_SNAP_KEY)[0] or []
    except Exception:
        series = []
    added, errs = 0, []
    for sk in sport_keys:
        rows, err = board(sk)
        if err:
            errs.append(f"{sk}: {err}")
        for r in rows:
            series.append({"sport": sk, "t": time.time(), "note": note, **r})
            added += 1
    try:
        import deep_cache as dc
        dc.save(_SNAP_KEY, series[-20000:])
    except Exception as _e:
        errlog.note("OAPI-snapshot", _e)
    return {"added": added, "total": len(series), "remaining": remaining(),
            "errors": errs}
