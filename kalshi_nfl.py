"""Live Kalshi NFL game-market prices for the weekly slate (kalshi_mlb's twin).

Kalshi lists NFL moneylines under KXNFLGAME with the same suffix convention as
MLB (KXNFLGAME-26AUG15DALSEA-SEA). The spread and total series NOW EXIST and are
parsed here -- this file used to carry a probe stub for them ("schema unknown
until they exist") that never did anything, so every spread and total leg the
engine built went unpriced:

    KXNFLSPREAD-26AUG06CARARI-ARI10   "Arizona wins by over 9.5 points?"
    KXNFLTOTAL-26AUG06CARARI-34       "Will there be over 33.5 points scored?"

Both follow the MLB convention: the trailing integer N means "N-0.5", i.e. the
spread market is "wins by N+" and the total is "N+ points". Kalshi books a deep
ladder per game (24 spread markets and 19 totals on the Hall of Fame game), which
is what lets a combo walk to the line that lands in a confidence band.

Games are matched by the PAIR of team abbreviations in the event suffix, so no
date math is needed. All lookups degrade gracefully to None.
"""
import re
import time

import kalshi

_ML_SERIES = "KXNFLGAME"
_SPREAD_SERIES = "KXNFLSPREAD"
_TOTAL_SERIES = "KXNFLTOTAL"

_TTL = 120
_cache = {"ts": 0.0, "data": None}

# Kalshi <-> app abbreviation canon.
_CANON = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR"}


def _canon(ab):
    return _CANON.get((ab or "").upper(), (ab or "").upper())


def _fetch(series):
    out, cursor = [], None
    for _ in range(4):
        url = f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            d = kalshi._get_json(url)
        except Exception:
            break
        out.extend(d.get("markets") or [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def _quote(c):
    """A quote at or beyond the bounds is Kalshi's "no offer" sentinel, not a
    price -- a contract costing 100c to win 100c is not a bet."""
    return c if (c is not None and 0 < c < 100) else None


_SPREAD_RE = re.compile(r"^KXNFLSPREAD-([0-9A-Z]+)-([A-Z]+?)(\d+)$")
_TOTAL_RE = re.compile(r"^KXNFLTOTAL-([0-9A-Z]+)-(\d+)$")


def _build():
    """{suffix: {ml, spread, total, ticker, close, no}} for every open NFL game.

    Keyed by the event suffix (26AUG06CARARI) rather than by team pair, because
    the spread and total series carry no team pair of their own -- and a suffix
    is what a leg's kref is resolved against anyway. `pair` keeps the old
    frozenset lookup working for game_prices."""
    idx = {}

    def ent(suffix):
        return idx.setdefault(suffix, {"ml": {}, "spread": {}, "total": {},
                                       "ticker": {}, "close": None,
                                       "no": {"ml": {}, "spread": {}}})

    for m in _fetch(_ML_SERIES):
        tk = m.get("ticker") or ""
        suffix = (m.get("event_ticker") or "").split("-", 1)[-1]
        if not suffix:
            continue
        team = _canon(tk.rsplit("-", 1)[-1])
        e = ent(suffix)
        e["ml"][team] = _quote(kalshi._cents(m.get("yes_ask_dollars")))
        e["no"]["ml"][team] = _quote(kalshi._cents(m.get("no_ask_dollars")))
        e["ticker"][team] = tk
        e["close"] = kalshi._parse_time(m.get("close_time")) or e["close"]

    for m in _fetch(_SPREAD_SERIES):
        mt = _SPREAD_RE.match(m.get("ticker") or "")
        if not mt:
            continue
        suffix, team, by = mt.group(1), _canon(mt.group(2)), int(mt.group(3))
        e = ent(suffix)
        e["spread"][(team, by)] = _quote(kalshi._cents(m.get("yes_ask_dollars")))
        e["no"]["spread"][(team, by)] = _quote(kalshi._cents(m.get("no_ask_dollars")))

    for m in _fetch(_TOTAL_SERIES):
        mt = _TOTAL_RE.match(m.get("ticker") or "")
        if not mt:
            continue
        suffix, n = mt.group(1), int(mt.group(2))
        e = ent(suffix)
        # One market, two sides: YES is the over, NO is the under.
        e["total"][n] = {"over": _quote(kalshi._cents(m.get("yes_ask_dollars"))),
                         "under": _quote(kalshi._cents(m.get("no_ask_dollars")))}

    for suffix, e in idx.items():
        if len(e["ml"]) >= 2:
            e["pair"] = frozenset(e["ml"].keys())
    return {k: v for k, v in idx.items() if v.get("pair")}


def price_leg(idx, suffix, kref):
    """Live Kalshi ask (cents) for one NFL leg, or None. Mirrors kalshi_mlb so a
    candidate built by nfl_game_sim prices through the same combo engine."""
    if not suffix or not kref:
        return None
    g = idx.get(suffix)
    if not g:
        return None
    t, no = kref.get("t"), bool(kref.get("no"))
    src = g.get("no") if no else g
    if t == "ml":
        return _quote((src.get("ml") or {}).get(kref.get("team")))
    if t == "spread":
        return _quote((src.get("spread") or {}).get(
            (_canon(kref.get("team")), kref.get("by"))))
    if t == "total":
        tot = (g.get("total") or {}).get(kref.get("n"))
        over = bool(kref.get("over")) != no
        return _quote(tot.get("over" if over else "under")) if tot else None
    return None


def ladders(suffix):
    """{'spread': {team: [by, ...]}, 'total': [n, ...]} actually booked for a
    game, so the engine offers the lines Kalshi trades rather than inventing
    them."""
    g = index().get(suffix) or {}
    sp = {}
    for (team, by) in (g.get("spread") or {}):
        sp.setdefault(team, []).append(by)
    for v in sp.values():
        v.sort()
    return {"spread": sp, "total": sorted(g.get("total") or {})}


def index():
    now = time.time()
    if _cache["data"] is None or now - _cache["ts"] > _TTL:
        try:
            _cache["data"] = _build()
            _cache["ts"] = now
        except Exception:
            if _cache["data"] is None:
                _cache["data"] = {}
    return _cache["data"]


def game_prices(home_ab, away_ab):
    """{'home_cents', 'away_cents', 'home_ticker', 'away_ticker', 'close'} for a
    matchup, or None when Kalshi hasn't listed it."""
    h, a = _canon(home_ab), _canon(away_ab)
    want = frozenset({h, a})
    e = next((v for v in index().values() if v.get("pair") == want), None)
    if not e:
        return None
    return {"home_cents": e["ml"].get(h), "away_cents": e["ml"].get(a),
            "home_ticker": e["ticker"].get(h), "away_ticker": e["ticker"].get(a),
            "close": e["close"], "suffix": next(
                (k for k, v in index().items() if v is e), None)}
