"""Our own tennis Elo, built from match results -- the same idea as the UFC engine.

The serve/return model (tennis_data) only covers players in the Match Charting
Project, which is ATP/WTA level. The huge live ITF / lower-tier slate has no
charting, so those matches were market-only. But Elo needs nothing but RESULTS,
and Kalshi's settled markets carry the winner of every match it lists -- across
ATP, WTA and the ITF firehose. So we harvest settled results, run an Elo, and get
an independent model for players the charting never sees.

Men and women are rated in separate pools (they never play each other). Ratings
start at 1500, use a provisional (larger) K until a player has a real sample, and
the win probability is the standard Elo logistic. As more matches settle, the
ratings sharpen -- recursive and dynamic, like the rest of the engines.
"""

import datetime
import json
import unicodedata
import urllib.request

import racing

_BASE = "https://api.elections.kalshi.com/trade-api/v2"
# (series, gender pool, tier start Elo). Tour players start well above ITF players
# so the weakly-connected pools stay on a sane common scale: a player's initial
# rating reflects the level they FIRST appear at (tour vs ITF), and play between
# the pools (qualifiers, lucky losers) recalibrates from there.
_SERIES = [("KXATPMATCH", "m", 1600.0), ("KXWTAMATCH", "w", 1600.0),
           ("KXITFMATCH", "m", 1400.0), ("KXITFWMATCH", "w", 1400.0)]
_K = 24.0
_MAX_PAGES = 22                      # bound the ITF firehose per series


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "vigil/1.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


_STORE_KEY = "tennis_elo_results"     # persistent accumulating match store


def _harvest(series, gender, tier_start):
    """Recently-settled matches for a series -> {event: [date, winner, loser,
    gender, tier_start]}. Pairs the two per-player markets by event and reads the
    'yes' result as the winner. Event-keyed so callers can dedup into a store."""
    by_event = {}
    cursor, pages = "", 0
    while pages < _MAX_PAGES:
        url = f"{_BASE}/markets?series_ticker={series}&status=settled&limit=1000"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            d = _get(url)
        except Exception:
            break
        for m in d.get("markets", []):
            res = m.get("result")
            nm = m.get("yes_sub_title")
            if not nm or res not in ("yes", "no"):
                continue
            tk = m.get("ticker") or ""
            ev = tk.rsplit("-", 1)[0]
            e = by_event.setdefault(ev, {"date": (m.get("close_time") or "")[:10], "p": []})
            e["p"].append((nm, res))
        cursor = d.get("cursor") or ""
        pages += 1
        if not cursor:
            break
    out = {}
    for ev, info in by_event.items():
        ps = info["p"]
        if len(ps) != 2:
            continue
        (n1, r1), (n2, r2) = ps
        if r1 == "yes" and r2 == "no":
            out[ev] = [info["date"], n1, n2, gender, tier_start]
        elif r2 == "yes" and r1 == "no":
            out[ev] = [info["date"], n2, n1, gender, tier_start]
    return out


def _build():
    """{'m': {...}, 'w': {...}} Elo pools. Harvests recently-settled matches, MERGES
    them into a persistent store keyed by event (so history accumulates across runs
    instead of a rolling window), then runs the Elo over the full accumulated set in
    true chronological order."""
    import deep_cache
    store = deep_cache.load(_STORE_KEY)[0] or {}     # {event: [date,w,l,gender,tier]}
    for series, g, tier_start in _SERIES:
        try:
            store.update(_harvest(series, g, tier_start))
        except Exception:
            pass
    try:
        deep_cache.save(_STORE_KEY, store)
    except Exception:
        pass

    by_gender = {"m": [], "w": []}
    for rec in store.values():
        date, win, los, g, tier_start = rec
        by_gender.get(g, by_gender["m"]).append((date, win, los, tier_start))
    pools = {"m": {}, "w": {}}
    for g, matches in by_gender.items():
        matches.sort(key=lambda x: x[0])             # global chronological order
        pool = pools[g]
        for date, win, los, tier_start in matches:
            wn, ln = _norm(win), _norm(los)
            if not wn or not ln or wn == ln:
                continue
            # first appearance starts at the tier of that match (tour vs ITF)
            W = pool.setdefault(wn, {"elo": tier_start, "n": 0, "name": win, "last": date})
            L = pool.setdefault(ln, {"elo": tier_start, "n": 0, "name": los, "last": date})
            ew = 1.0 / (1.0 + 10 ** ((L["elo"] - W["elo"]) / 400.0))
            kw = _K * (1.6 if W["n"] < 10 else 1.0)    # provisional boost
            kl = _K * (1.6 if L["n"] < 10 else 1.0)
            W["elo"] += kw * (1.0 - ew)
            L["elo"] -= kl * (1.0 - ew)
            W["n"] += 1; L["n"] += 1
            W["last"] = L["last"] = date
            W["name"], L["name"] = win, los
    return pools


def pools():
    """Cached Elo pools, refreshed daily (settled results accrue continuously)."""
    return racing._cached(("tennis_elo",), 24 * 3600, _build) or {"m": {}, "w": {}}


def rate(name, gender="m"):
    """{elo, n, name} for a player in the men's/women's pool, or None."""
    return pools().get(gender, {}).get(_norm(name))


def win_prob(elo_a, elo_b):
    """Standard Elo logistic: P(A beats B)."""
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))
