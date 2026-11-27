"""The deep engine's market anchor: the points-per-win that gives the market's wins back, MEASURED (2026-11-27).

The owner's decision on the C3 follow-up (research/reports/c3_season_replay.md, addendum) was: "Fix the
deep-engine market anchor immediately after the registered 100-game read, preserving its original result and
model version." This script measures the constant the fix ships. No outcome of any registered window is read:
this is an engine property, measured on seasons 2002-2025.

THE CRITERION is nfl_season._PTS_PER_WIN's own: "the season sim seeded with the market's wins has to give those
wins back".

A market's win totals are SCHEDULE-INCLUSIVE: an easy schedule is already in them. Production converts them to a
margin ignoring the schedule, and the engine then plays that same schedule. So the joint structure of wins and
schedule is part of what is measured, as it was when 2.25 was fitted on the real 2026 ladder and schedule.

A first design permuted one season's wins over the teams to get replicates, and was stopped. Permuting destroys
that structure: at the analytic resolution's 1.842, every permutation of 2025 returned slopes of 0.78-0.98, mean
0.90, against 1.00 for the real assignment.

THE REPLICATES are real seasons: every season from 2002 to 2025 (24; 32 teams each), each with its own schedule
and its own real wins (ties half), shrunk halfway to the season's mean as a market-like vector (SD about 1.6
wins).
- Margins are (W - G/2) x K x 17 / G, so K is per win of a 17-game season whatever G is (16 before 2021).
- League-average deep units are anchored through production's own apply_anchor, levels set from those margins.
- Every game is played n times at its real home side (the deep engine's _HFA_YDS), with the same game seeds at
  every K (common random numbers).
- Returned expected wins are regressed on seeded wins, one slope per season. The slope is the mean over
  seasons, with SE = SD / sqrt(24).

K* is solved by linear interpolation between K = 1.80 and 1.90 (the C3 follow-up bracketed it at 1.81-1.87),
with a 95% interval from 2,000 bootstrap draws over seasons.

THE TOLERANCE the shipped value must meet, stated before any run:
- **Slope.** A verification run at K* rounded to 0.01, on FRESH game seeds, returns a mean slope within
  1 +/- 0.03, with SE <= 0.01.
- **Win error.** The mean absolute error of returned against seeded wins is at most the exact analytic
  resolution's own error plus 0.10 wins. The analytic resolution is Phi(./13.2) at its own slope-1 K on the
  same seasons; its error is the schedule's alone, which no single conversion removes. The 0.10 is the Monte
  Carlo allowance at n = 300 (per-team noise about 0.12 wins).

usage: VIGIL_NO_BG=1 python3 research/deep_anchor_scale.py fetch --cache <dir>   # ESPN -> deep_anchor_seasons.json
       VIGIL_NO_BG=1 python3 research/deep_anchor_scale.py measure [--n 300] [--procs 4] -> deep_anchor_scale.json
"""
import json
import math
import os
import random
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

import research.c3_margin_scale as M                # noqa: E402

SEASONS_PATH = os.path.join(ROOT, "research", "data", "deep_anchor_seasons.json")
OUT = os.path.join(ROOT, "research", "data", "deep_anchor_scale.json")
SEASONS = tuple(range(2002, 2026))
K_LO, K_HI = 1.80, 1.90
SHRINK = 0.5
SLOPE_TOL, SE_MAX, MAE_ALLOW = 0.03, 0.01, 0.10


def fetch(cache):
    """Every regular-season game 2002-2025 off ESPN's scoreboards (c3_season_replay's parse)."""
    import research.c3_season_replay as C
    get = C._install_cache(cache)
    out = {}
    for s in SEASONS:
        ni = C.fetch_nfl(get, s)
        out[str(s)] = [[g["home"], g["away"], g["hs"], g["as"]] for g in ni["games"]]
        print(s, len(out[str(s)]), flush=True)
    with open(SEASONS_PATH, "w") as fh:
        json.dump({"source": "ESPN scoreboards, seasontype 2, weeks 1-18", "seasons": out}, fh,
                  separators=(",", ":"), sort_keys=True)


def season_setup(rows):
    """(seed wins, games, G) for one season: real wins (ties half) shrunk SHRINK toward G/2."""
    wins, gp = {}, {}
    for h, a, hs, as_ in rows:
        for t in (h, a):
            wins.setdefault(t, 0.0)
            gp[t] = gp.get(t, 0) + 1
        if hs > as_:
            wins[h] += 1
        elif as_ > hs:
            wins[a] += 1
        else:
            wins[h] += 0.5
            wins[a] += 0.5
    G = round(sum(gp.values()) / len(gp))
    seed = {t: G / 2.0 + SHRINK * (w - G / 2.0) for t, w in wins.items()}
    return seed, [(h, a) for h, a, _x, _y in rows], G


def margins(seed, G, k):
    return {t: (w - G / 2.0) * k * 17.0 / G for t, w in seed.items()}


def expected_wins(teams, games, p_home):
    ew = {t: 0.0 for t in teams}
    for (h, a), p in zip(games, p_home):
        ew[h] += p
        ew[a] += 1.0 - p
    return ew


def slope(seed, ew):
    ks = sorted(seed)
    x, y = [seed[k] for k in ks], [ew[k] for k in ks]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    return sum((a - mx) * (c - my) for a, c in zip(x, y)) / sum((a - mx) ** 2 for a in x)


def profiles(mm):
    """League-average deep units, levels set by production's apply_anchor from margins `mm`."""
    import nfl_deep_sim as E
    import nfl_deep_data as D
    T = E.tables()
    L = E.league_profile(T)
    profs = {t: json.loads(json.dumps(dict(L, abbr=t, g=0))) for t in mm}
    orig = D.market_margins
    try:
        D.market_margins = lambda _s, recs=None: (dict(mm), "deep_anchor_scale")
        D.apply_anchor(profs, 2025, T)
    finally:
        D.market_margins = orig
    return profs


def run_k(data, k, n, procs, tag):
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    res = []
    for s in SEASONS:
        seed, games, G = season_setup(data[str(s)])
        profs = profiles(margins(seed, G, k))
        specs = [(h, a, n, zlib.crc32(f"{tag}|{s}|{i}".encode())) for i, (h, a) in enumerate(games)]
        with ctx.Pool(procs, initializer=M._deep_init, initargs=(profs,)) as pool:
            ps = [x[0] for x in pool.map(M._deep_game, specs, chunksize=4)]
        ew = expected_wins(seed, games, ps)
        res.append({"season": s, "slope": slope(seed, ew),
                    "mae": sum(abs(ew[t] - seed[t]) for t in seed) / len(seed)})
        print(f"  k {k} {s}: slope {res[-1]['slope']:.4f}", flush=True)
    return res


def mean_se(xs):
    m = sum(xs) / len(xs)
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1) / len(xs))


def solve(lo, hi):
    def k_of(idx):
        s1 = sum(lo[i]["slope"] for i in idx) / len(idx)
        s2 = sum(hi[i]["slope"] for i in idx) / len(idx)
        return K_LO + (1.0 - s1) * (K_HI - K_LO) / (s2 - s1)
    R = len(lo)
    rng = random.Random(20261127)
    boots = sorted(k_of([rng.randrange(R) for _ in range(R)]) for _ in range(2000))
    return k_of(range(R)), boots[49], boots[1949]


def analytic_reference(data):
    """Phi(./13.2) at its own slope-1 K over the same seasons (exact expectations): K and the win error, the
    schedule's alone."""
    sets = [season_setup(data[str(s)]) for s in SEASONS]

    def ew_of(seed, games, G, k):
        mm = margins(seed, G, k)
        return expected_wins(seed, games, [M._phi((mm[h] - mm[a] + 2.2) / 13.2) for h, a in games])
    lo, hi = 1.0, 3.0
    for _ in range(40):
        mid = (lo + hi) / 2
        s = sum(slope(sd, ew_of(sd, g, G, mid)) for sd, g, G in sets) / len(sets)
        lo, hi = (mid, hi) if s < 1 else (lo, mid)
    k = (lo + hi) / 2
    mae = sum(sum(abs(ew - sd[t]) for t, ew in ew_of(sd, g, G, k).items()) / len(sd) for sd, g, G in sets) / len(sets)
    return k, mae


def measure(n, procs):
    data = json.load(open(SEASONS_PATH))["seasons"]
    lo = run_k(data, K_LO, n, procs, "solve")
    hi = run_k(data, K_HI, n, procs, "solve")
    k_star, k95a, k95b = solve(lo, hi)
    k_ship = round(k_star, 2)
    ver = run_k(data, k_ship, n, procs, "verify")
    s_m, s_se = mean_se([x["slope"] for x in ver])
    mae_m, _ = mean_se([x["mae"] for x in ver])
    k_an, mae_an = analytic_reference(data)
    ok = abs(s_m - 1.0) <= SLOPE_TOL and s_se <= SE_MAX and mae_m <= mae_an + MAE_ALLOW
    out = {"criterion": "returned expected wins regressed on seeded wins, slope 1 (nfl_season._PTS_PER_WIN's own)",
           "replicates": "seasons 2002-2025, each its own schedule and real wins shrunk %.1f to the mean" % SHRINK,
           "n_per_game": n, "k_lo_hi": [K_LO, K_HI],
           "slope_at": {str(K_LO): mean_se([x["slope"] for x in lo]), str(K_HI): mean_se([x["slope"] for x in hi])},
           "k_star": round(k_star, 4), "k_star_95": [round(k95a, 4), round(k95b, 4)], "k_ship": k_ship,
           "verify": {"slope": round(s_m, 4), "se": round(s_se, 4), "mae_wins": round(mae_m, 4)},
           "analytic_reference": {"k_slope1": round(k_an, 4), "mae_wins": round(mae_an, 4)},
           "tolerance": {"slope": [1 - SLOPE_TOL, 1 + SLOPE_TOL], "se_max": SE_MAX, "mae_allow": MAE_ALLOW},
           "passes": ok, "per_season": {"lo": lo, "hi": hi, "verify": ver}}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps({k: v for k, v in out.items() if k != "per_season"}, indent=1))


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("fetch", "measure"))
    ap.add_argument("--cache")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args(argv)
    if a.stage == "fetch":
        fetch(a.cache)
    else:
        measure(a.n, a.procs)


if __name__ == "__main__":
    main(sys.argv[1:])
