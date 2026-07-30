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
import math
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
# Same two entry tiers, for players who arrive from the deep archive rather than
# from a Kalshi market. Sackmann's tourney_level codes: 15/25 are ITF futures
# prize tiers, C is Challenger, S is satellite, Q is qualifying -- all lower-tier
# entries. G/M/A/D/F (slams, Masters, ATP, Davis Cup, finals) are main tour.
_TOUR_START, _ITF_START = 1600.0, 1400.0
_LOW_TIERS = {"15", "25", "C", "S", "Q", "ITF", "FUTURES"}
# Elo K by EXPERIENCE, fitted (tests/tennis_elo_fit.py):
#
#     K(n) = K_LATE + (K_EARLY - K_LATE) * exp(-n / K_TAU)
#
# A single K cannot serve this pool, because the pool is a mixture. Since the deep
# archive was wired in (tennis_history), ATP players carry hundreds of rated
# matches and want a SMALL K -- one result should barely move a well-established
# rating. The ITF players who make up most of a Kalshi board still carry a handful
# and want a LARGE one, or the rating never says anything at all. Fitting a
# constant to that mixture splits the difference and serves neither: measured, the
# deep pool's best constant is 24 and the shallow pool's is 48.
#
# The ramp gets both. Fitted jointly over ~55k deep ATP matches and ~19k settled
# Kalshi results, scored on each dataset separately, it MATCHES the deep pool's
# best constant (0.6099 vs 0.6095) and BEATS the shallow pool's (0.6540 vs 0.6572)
# -- and improves on what was shipped in both (deep 0.6167 -> 0.6099, Kalshi
# 0.6572 -> 0.6540). In practice K runs ~100 for a debut, ~51 at ten matches, ~33
# at twenty and settles near 22 by fifty.
#
# This SUBSUMES the old flat 48 with a 1.6x boost under 10 matches, which was the
# same idea expressed as a step function -- and which was itself fitted before
# there was any deep history to be established against.
K_EARLY, K_LATE, K_TAU = 100.0, 22.0, 10.0
_K = K_LATE          # kept for callers/tests that reference a nominal K


def k_for(n):
    """Elo K for a player with `n` matches already rated."""
    return K_LATE + (K_EARLY - K_LATE) * math.exp(-max(0, n) / K_TAU)


# Time decay stays OFF, re-measured on the deep pool: a 10-year half-life was the
# best of a bad set (+0.0012 rolling, split 3/2 across folds) and everything
# shorter was clearly harmful. `last` stays a display field.
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
# Surface rating shrunk toward the player's overall by this many prior matches.
# FITTED (tests/tennis_elo_fit.py). Rejected twice before on thinner data and now
# established on the 55k-match deep archive: rolling origin gives +0.0035 mean
# with 5 of 5 folds positive, and every fold independently chose 50.
K_SURFACE = 50.0
_SURFACES = ("Hard", "Clay", "Grass")


def _tourney(title):
    """'Will X win the A vs B: M25 Koszalin Round of 16 match?' -> 'M25 Koszalin'."""
    import re
    t = (title or "").split(":", 1)[-1].strip()
    t = re.sub(r"\s+(Round of \d+|Quarterfinal|Semifinal|Final|R\d+)\b.*$", "", t, flags=re.I)
    return re.sub(r"\s+match\?*$", "", t, flags=re.I).strip() or None


def _surface_of(tournament):
    """Surface for a tournament name, from the keyword map and the cached search
    lookups. Cache-only -- building ratings must never spend search quota."""
    if not tournament:
        return None
    try:
        import tennis_live
        s = tennis_live.surface_of(tournament)
        if s:
            return s
    except Exception:
        pass
    try:
        import deep_cache
        cache = deep_cache.load("tennis_surface_lookups")[0] or {}
        v = cache.get(" ".join(tournament.lower().split()))
        return v if isinstance(v, str) and v else None
    except Exception:
        return None


def _harvest(series, gender, tier_start):
    """Recently-settled matches for a series -> {event: [date, winner, loser,
    gender, tier_start, tournament]}. Pairs the two per-player markets by event and
    reads the 'yes' result as the winner. Event-keyed so callers can dedup into a
    store.

    The tournament comes along because surface does: a settled market's title
    carries "M25 Koszalin", and the surface Elo below needs to know which court
    the result happened on. Stored as the NAME rather than the surface so it
    resolves against whatever the surface map knows at build time -- a stop that
    is unidentified today still gets its history classified once it is."""
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
            e = by_event.setdefault(ev, {"date": (m.get("close_time") or "")[:10],
                                         "p": [], "t": _tourney(m.get("title"))})
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
            out[ev] = [info["date"], n1, n2, gender, tier_start, info.get("t")]
        elif r2 == "yes" and r1 == "no":
            out[ev] = [info["date"], n2, n1, gender, tier_start, info.get("t")]
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
    seen = set()
    for rec in store.values():
        # 6th element (tournament) was added later; older rows are 5 long.
        date, win, los, g, tier_start = rec[:5]
        surf = _surface_of(rec[5]) if len(rec) > 5 else None
        by_gender.get(g, by_gender["m"]).append((date, win, los, tier_start, surf))
        seen.add((date.replace("-", ""), g, _norm(win), _norm(los)))

    # Deep history ahead of the Kalshi results. Kalshi only reaches back to when
    # it started listing tennis, which leaves nearly every rating provisional; the
    # archive gives those players a real baseline before the recent results land
    # on top. Optional by construction -- if it is disabled or unreachable this is
    # an empty list and the pools are exactly what they were before.
    try:
        import tennis_history
        deep = tennis_history.results()
    except Exception:
        deep = []
    for date, g, win, los, surf, level in deep:
        key = (date, g, _norm(win), _norm(los))
        if key in seen:
            continue                       # already have it from settled markets
        seen.add(key)
        # Archive players enter at the tier their level implies: futures/ITF and
        # qualifying start where our ITF pool starts, main-draw tour where the
        # tour pool does, so the two populations stay on one comparable scale.
        tier = _ITF_START if (level or "").upper() in _LOW_TIERS else _TOUR_START
        by_gender.get(g, by_gender["m"]).append((date, win, los, tier, surf))
    pools = {"m": {}, "w": {}}
    for g, matches in by_gender.items():
        matches.sort(key=lambda x: x[0])             # global chronological order
        pool = pools[g]
        for date, win, los, tier_start, surf in matches:
            wn, ln = _norm(win), _norm(los)
            if not wn or not ln or wn == ln:
                continue
            # first appearance starts at the tier of that match (tour vs ITF)
            W = pool.setdefault(wn, {"elo": tier_start, "n": 0, "name": win,
                                     "last": date, "res": [], "surf": {}})
            L = pool.setdefault(ln, {"elo": tier_start, "n": 0, "name": los,
                                     "last": date, "res": [], "surf": {}})
            # A surface rating starts from the player's overall, so a first match
            # on clay is not treated as a debut.
            if surf:
                for P in (W, L):
                    P.setdefault("surf", {}).setdefault(surf, {"elo": P["elo"], "n": 0})
            ew = 1.0 / (1.0 + 10 ** ((L["elo"] - W["elo"]) / 400.0))
            kw = k_for(W["n"])          # ramps down as a rating establishes
            kl = k_for(L["n"])
            W["elo"] += kw * (1.0 - ew)
            L["elo"] -= kl * (1.0 - ew)
            if surf:
                ws, ls = W["surf"][surf], L["surf"][surf]
                ews = 1.0 / (1.0 + 10 ** ((ls["elo"] - ws["elo"]) / 400.0))
                ws["elo"] += k_for(ws["n"]) * (1.0 - ews)
                ls["elo"] -= k_for(ls["n"]) * (1.0 - ews)
                ws["n"] += 1; ls["n"] += 1
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


def elo_on(rec, surface=None):
    """A player's rating for a given surface: their surface Elo shrunk toward
    their overall by K_SURFACE prior matches on it.

    Shrinkage rather than a straight swap is the whole point. A pure surface
    rating throws away everything the player did elsewhere and is worse than
    pooling at every sample size we have measured; blending keeps the overall as
    the prior and lets clay evidence move it as clay evidence accumulates."""
    if not rec:
        return None
    if not surface:
        return rec["elo"]
    s = (rec.get("surf") or {}).get(surface)
    if not s or not s.get("n"):
        return rec["elo"]
    return (s["n"] * s["elo"] + K_SURFACE * rec["elo"]) / (s["n"] + K_SURFACE)


def rate(name, gender="m", surface=None):
    """{elo, n, name} for a player, or None. With `surface`, `elo` is the
    surface-blended rating and `elo_overall` carries the unblended one."""
    rec = pools().get(gender, {}).get(_norm(name))
    if not rec or not surface:
        return rec
    out = dict(rec)
    out["elo_overall"] = rec["elo"]
    out["elo"] = elo_on(rec, surface)
    out["surf_n"] = ((rec.get("surf") or {}).get(surface) or {}).get("n", 0)
    return out


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
