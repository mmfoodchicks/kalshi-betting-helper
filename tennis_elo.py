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
import os
import math
import time
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
# from a Kalshi market.
#
# Level codes are NUMERIC for ITF prize tiers and lettered for everything else.
# The men's files only ever use 15 and 25, which is why an explicit pair worked
# at first -- but the women's ITF file adds 35, 50, 60, 75 and 100, and listing
# codes by hand silently entered all of those at TOUR level. That inflates the
# ITF women's pool against the tour pool and corrupts every cross-tier comparison.
# Any numeric level is an ITF prize tier; C (Challenger), S (satellite) and Q
# (qualifying) are the lettered lower tiers. G/M/A/D/F/P/I (slams, Masters, ATP,
# Davis Cup, finals, Premier, International) are main tour.
_TOUR_START, _ITF_START = 1600.0, 1400.0
_LOW_LETTERS = {"C", "S", "Q", "ITF", "FUTURES"}


def _is_low_tier(level):
    """True for ITF/Challenger/qualifying entries, which start below the tour."""
    lv = (level or "").strip().upper()
    return bool(lv) and (lv.isdigit() or lv in _LOW_LETTERS)


# Kept for callers/tests that referenced the old set.
_LOW_TIERS = _LOW_LETTERS | {str(n) for n in (15, 25, 35, 50, 60, 75, 100)}
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

# --- surface ratings ---------------------------------------------------------
# A surface rating is a DEVIATION from the player's live overall rating, not a
# parallel Elo chain. The chain version shipped first and was measurably worse
# than not splitting at all -- on the 436k-match archive it lost to pooling on
# every rolling-origin fold, on both tour and ITF. Two structural reasons:
#
#   * it seeded each surface from the player's overall rating and then updated it
#     with k_for(0) == K_EARLY, so a debut-sized K immediately destroyed a seed
#     that was already good;
#   * it went stale. Once the chains forked, a player's improvement showed up in
#     the overall rating and never reached their clay rating except through clay
#     matches, which for most of the board is a handful.
#
# Modelling the deviation fixes both: the overall term is always current, and
# `dev` only ever carries "better on clay than on themselves". Deviations are
# recentred (evidence-weighted) across a player's surfaces after every update, so
# dev cannot quietly restate overall strength and double-count it.
#
# FITTED (tests/tennis_elo_fit.py) on 436k archive matches, rolling origin:
# +0.0022 logloss vs pooling and +0.0044 vs the old chain, 5 of 5 folds positive
# on both comparisons, and positive on tour (+0.0025) and ITF (+0.0019)
# separately. Every fold chose K_DEV=16 out of a grid running to 48, so it is an
# interior optimum rather than a grid edge.
K_DEV = 16.0         # Elo points a deviation moves per match of surface evidence
K_SURFACE = 50.0     # prior matches before a deviation is trusted in full
_SURFACES = ("Hard", "Clay", "Grass")


def _recentre(rec):
    """Force a player's surface deviations to average zero, weighted by how much
    evidence each surface carries. This is what keeps `dev` differential: without
    it a strong player accrues a positive deviation on every surface and their
    strength gets counted twice."""
    sd = rec.get("surf") or {}
    tot = sum(s["n"] for s in sd.values())
    if tot <= 0:
        return
    m = sum(s["n"] * s["dev"] for s in sd.values()) / tot
    for s in sd.values():
        s["dev"] -= m


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
        tier = _ITF_START if _is_low_tier(level) else _TOUR_START
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
            # A surface deviation starts at zero: with no clay evidence a player
            # is exactly their overall rating on clay.
            if surf:
                for P in (W, L):
                    P.setdefault("surf", {}).setdefault(surf, {"dev": 0.0, "n": 0})
            ew = 1.0 / (1.0 + 10 ** ((L["elo"] - W["elo"]) / 400.0))
            # The deviation is driven by the SURFACE-AWARE expectation, so it
            # absorbs only what the court explains beyond the overall rating.
            eb = ew if not surf else 1.0 / (1.0 + 10 ** (
                (elo_on(L, surf) - elo_on(W, surf)) / 400.0))
            kw = k_for(W["n"])          # ramps down as a rating establishes
            kl = k_for(L["n"])
            W["elo"] += kw * (1.0 - ew)
            L["elo"] -= kl * (1.0 - ew)
            if surf:
                W["surf"][surf]["dev"] += K_DEV * (1.0 - eb)
                L["surf"][surf]["dev"] -= K_DEV * (1.0 - eb)
                W["surf"][surf]["n"] += 1
                L["surf"][surf]["n"] += 1
                _recentre(W)
                _recentre(L)
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
    # Materialise each surface deviation as an absolute rating for display and
    # for the specialist insight. This is done once at the end because it is
    # anchored to the FINAL overall rating -- doing it during the walk would
    # freeze each surface against whatever the overall happened to be that day,
    # which is the staleness the deviation form exists to avoid.
    for pool in pools.values():
        for rec in pool.values():
            for s in (rec.get("surf") or {}).values():
                s["elo"] = rec["elo"] + s["dev"]
    return pools


def _build_blob():
    """Entry point for the isolated build: pools as a pickle. Module level so the
    'spawn' start method can import and call it."""
    import pickle
    return pickle.dumps(_build(), protocol=pickle.HIGHEST_PROTOCOL)


def _build_isolated():
    """Build the pools in a SEPARATE PROCESS and bring back just the result.

    Building them costs far more memory than keeping them does: walking 436k
    archive matches peaks near 390 MB, but the pools themselves are about 42 MB.
    In-process that peak never comes back -- the freed rows leave pymalloc arenas
    too fragmented to release (malloc_trim recovers ~4 MB of it), so the app sits
    at ~215 MB afterwards and a 512 MB container has nothing left for the season
    sim. Measured, this keeps the parent at ~75 MB instead: the child does the
    allocating and the OS takes it all back when it exits.

    A plain subprocess, NOT multiprocessing. Both alternatives are traps here:
    'fork' can inherit a lock held by another thread of a threaded server and
    deadlock the child, and 'spawn' re-imports the parent's __main__ -- which for
    any SCRIPT that reaches this (the test suite, a one-off analysis) means the
    child re-runs that script, calls back in here, and spawns again. That was not
    hypothetical; it hung the tennis board check until the recursion was spotted.
    Running a fresh interpreter on a fixed command has neither failure mode.

    Falls back to building in-process if the subprocess cannot run -- heavier,
    but never a dead board."""
    import pickle
    import subprocess
    import sys
    try:
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys, tennis_elo; sys.stdout.buffer.write(tennis_elo._build_blob())"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=_BUILD_TIMEOUT_S)
        if out.returncode == 0 and out.stdout:
            return pickle.loads(out.stdout)
    except Exception:
        pass
    return _build()


_BUILD_TIMEOUT_S = 900


_POOLS_KEY = "tennis_elo_pools"
_POOLS_TTL = 24 * 3600


def _pools_from_disk_or_build():
    """Pools, preferring the persisted copy over a rebuild.

    Rebuilding is the single most expensive thing this app does: walking the
    436k-match archive peaks near 440 MB even isolated in a child, which is more
    than a 512 MB container has. The finished pools are ~5 MB pickled. Keeping
    them only in memory meant every restart paid the full rebuild -- and on a
    small host that rebuild is itself what killed the instance, so it restarted
    and rebuilt again. Persisting them turns the common case into a 5 MB read."""
    try:
        import deep_cache
        cached, ts = deep_cache.load(_POOLS_KEY)
        if cached and ts and (time.time() - ts) < _POOLS_TTL:
            return cached
    except Exception:
        pass
    # Through the same app-wide gate as the MLB slate: both are large
    # out-of-process builds, and overlapping them is what puts a small instance
    # over its limit even though each fits on its own.
    try:
        import deep_cache
        gate = deep_cache.HEAVY_BUILD
    except Exception:
        import contextlib
        gate = contextlib.nullcontext()
    with gate:
        built = _build_isolated()
    if built and (built.get("m") or built.get("w")):
        try:
            import deep_cache
            deep_cache.save(_POOLS_KEY, built)
        except Exception:
            pass
    return built


def pools():
    """Cached Elo pools, refreshed daily (settled results accrue continuously).
    Held in memory for the process and on disk across restarts."""
    return (racing._cached(("tennis_elo",), _POOLS_TTL, _pools_from_disk_or_build)
            or {"m": {}, "w": {}})


def elo_on(rec, surface=None):
    """A player's rating for a given surface: their overall rating plus their
    surface deviation, shrunk by how much evidence that surface carries.

    Shrinkage rather than a straight swap is the whole point. A pure surface
    rating throws away everything the player did elsewhere and is worse than
    pooling at every sample size we have measured; anchoring on the overall lets
    clay evidence move a player as clay evidence accumulates, and a player with
    two clay matches stays essentially their overall self."""
    if not rec:
        return None
    if not surface:
        return rec["elo"]
    s = (rec.get("surf") or {}).get(surface)
    if not s or not s.get("n"):
        return rec["elo"]
    # `dev` is the model quantity; `elo` is the materialised absolute rating.
    # Derive one from the other so a record from either representation works.
    dev = s["dev"] if "dev" in s else s.get("elo", rec["elo"]) - rec["elo"]
    return rec["elo"] + dev * s["n"] / (s["n"] + K_SURFACE)


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
