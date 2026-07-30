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
# Elo K factor -- FITTED, not guessed (tests/tennis_elo_fit.py). Rolling-origin
# validation over two independent datasets -- ~19k settled Kalshi results
# (ATP/WTA/ITF, the population this board actually runs on) and ~11.6k Match
# Charting matches (tour level, decades deep) -- puts the minimum at 48 on BOTH,
# with the curve flat from about 40 to 56. Held-out log loss improves 0.6560 ->
# 0.6489 on Kalshi and 0.5557 -> 0.5477 on charting, and 7 of 8 rolling folds beat
# the old 24. Accuracy barely moves; what improves is CALIBRATION, which is what
# the fair-win blend and the edge calculation consume.
#
# Why so high: both populations turn over fast and are shallow per player (median
# 4 Kalshi matches), so a rating has to move quickly to track a player at all. The
# old 24 was tuned for a deep, stable pool that tennis at this tier does not have.
_K = 48.0
# Left ALONE, also measured: the 1.6x provisional boost under 10 matches (refitting
# to 2.0/20 made the held-out tail worse), and time decay -- regressing an idle
# player's rating toward the pool mean was monotonically harmful at every half-life
# from 90 to 720 days on both datasets. `last` stays a display field.
_MAX_PAGES = 22                      # bound the ITF firehose per series
_FORM_WIN = 8                        # recent results kept per player


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
            W = pool.setdefault(wn, {"elo": tier_start, "n": 0, "name": win,
                                     "last": date, "res": []})
            L = pool.setdefault(ln, {"elo": tier_start, "n": 0, "name": los,
                                     "last": date, "res": []})
            ew = 1.0 / (1.0 + 10 ** ((L["elo"] - W["elo"]) / 400.0))
            kw = _K * (1.6 if W["n"] < 10 else 1.0)    # provisional boost
            kl = _K * (1.6 if L["n"] < 10 else 1.0)
            W["elo"] += kw * (1.0 - ew)
            L["elo"] -= kl * (1.0 - ew)
            W["n"] += 1; L["n"] += 1
            W["last"] = L["last"] = date
            W["name"], L["name"] = win, los
            # Recent results, with the pre-match expectation that produced them.
            # This is the only form signal that reaches ITF -- the ESPN crawl in
            # tennis_prices covers ATP/WTA only, which is under a tenth of the
            # board. Kept to the last few matches per player.
            W["res"].append((date, 1, round(ew, 4)))
            L["res"].append((date, 0, round(1.0 - ew, 4)))
            if len(W["res"]) > _FORM_WIN:
                del W["res"][0]
            if len(L["res"]) > _FORM_WIN:
                del L["res"][0]
    return pools


def pools():
    """Cached Elo pools, refreshed daily (settled results accrue continuously)."""
    return racing._cached(("tennis_elo",), 24 * 3600, _build) or {"m": {}, "w": {}}


def rate(name, gender="m"):
    """{elo, n, name} for a player in the men's/women's pool, or None."""
    return pools().get(gender, {}).get(_norm(name))


def form(name, gender="m"):
    """Recent-results form for a player, or None.

    {w, l, streak, delta, n, last} where `streak` is signed consecutive results
    (newest first) and `delta` is the recency-weighted (actual - expected) run:
    0 means performing exactly to rating, negative means losing more than the
    rating implies -- a slump in the only sense that is measurable.

    REPORTED, NOT MODELLED. Adding this to the rating was backtested over 19,278
    settled matches (tests/tennis_form_check.py): a linear form term, a
    threshold-on-extremes term and a streak term all failed to beat the plain Elo
    out of sample. The bucket tables that look convincing in-sample turn out to be
    draw depth (a win streak means you have advanced to a harder opponent) and
    rating immaturity, not momentum. It is surfaced so a human can see the context
    behind a price; it does not move our number."""
    r = pools().get(gender, {}).get(_norm(name))
    if not r:
        return None
    res = r.get("res") or []
    if not res:
        return None
    newest = list(reversed(res))                 # newest first
    w = sum(1 for _, won, _ in newest if won)
    streak = 0
    for _, won, _ in newest:
        if streak and won != newest[0][1]:
            break
        streak += 1
    num = den = 0.0
    for i, (_, won, exp) in enumerate(newest):
        wt = 0.5 ** (i / 4.0)
        num += wt * (won - exp)
        den += wt
    return {"w": w, "l": len(newest) - w,
            "streak": streak if newest[0][1] else -streak,
            "delta": round(num / den, 3) if den else 0.0,
            "n": len(newest), "last": newest[0][0]}


def win_prob(elo_a, elo_b):
    """Standard Elo logistic: P(A beats B)."""
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))
