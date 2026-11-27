"""Where nfl_season._PTS_PER_WIN (2.25) is applied, and whether each resolution gives the market's wins back.
RESEARCH, descriptive: an engine property, no outcome is read (the owner, 2026-11-27, on C3's NFL finding:
"investigate the suspected double margin-compression correction. Distinguish a demonstrable implementation error
from a proposed modeling improvement").

The constant's own contract (nfl_season.py, above _PTS_PER_WIN): "the season sim seeded with the market's wins
has to give those wins back: at 1.66 it returned them compressed (slope 0.74 of sim wins on market wins, 300
seasons, 2026-09-09 ...), so 1.66/0.74". So the test of any reader of the constant is the same one it was fitted
by: seed 32 teams with a market-like win vector, play a full 17-game schedule, regress the returned expected wins
on the seeded wins. Slope 1 keeps the contract; above 1 over-spreads the market; below 1 compresses it.

Readers measured (every reader of the constant in the repo, grep 2026-11-27):
  drive    nfl_season._matchup_pts -> _drive_rates -> _play       production: the season sim (run_season)
  analytic Phi(((Ra - Rb) x K + 2.2) / 13.2)                       nfl_season._wp's shape; no production caller,
                                                                   the C3 arm and the research PD arms
  deep     nfl_deep_data.market_margins: (W - 8.5) x K ->         production: the deep engine's level anchor
           apply_anchor's level -> nfl_deep_sim.play_game          (slate blend 0.30, deep season, DFS worlds)

Inputs: 2025's real 272-game schedule (research/data/c3_season/inputs.json) and a market-like seed vector,
2025's real wins shrunk halfway to 8.5 (SD ~1.6 wins: the spread of an opening ladder). Each deep team is the
league-average unit profile, so the anchor alone sets its level (production's _ANCHOR_PRIOR_GAMES None: the market
at full weight). Every game at its real home side (the deep engine's _HFA_YDS; the drive sim's +1 host edge).

usage: VIGIL_NO_BG=1 python3 research/c3_margin_scale.py [--n-drive 4000] [--n-deep 500] [--procs 4]
       -> research/data/c3_margin_scale.json
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

import nfl_season                  # noqa: E402

OUT = os.path.join(ROOT, "research", "data", "c3_margin_scale.json")
SHRINK = 0.5


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def setup():
    import research.c3_season_replay as C
    inp = C.load_inputs()["nfl"]["2025"]
    real = C.real_nfl(inp)
    seed = {ab: 8.5 + SHRINK * (real[ab]["wins"] - 8.5) for ab in nfl_season._DIV_OF}
    games = [(g["home"], g["away"]) for g in inp["games"]]
    return seed, games


def slope(seed, ew):
    ks = sorted(seed)
    x = [seed[k] for k in ks]
    y = [ew[k] for k in ks]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sum((a - mx) ** 2 for a in x)
    res = [c - my - b * (a - mx) for a, c in zip(x, y)]
    se = math.sqrt(sum(r * r for r in res) / (len(x) - 2) / sum((a - mx) ** 2 for a in x))
    return b, se


def expected_wins(games, p_home):
    ew = {ab: 0.0 for ab in nfl_season._DIV_OF}
    for (h, a), p in zip(games, p_home):
        ew[h] += p
        ew[a] += 1.0 - p
    return ew


def analytic(seed, games, k):
    return expected_wins(games, [_phi(((seed[h] - seed[a]) * k + nfl_season._HFA_MARGIN) / 13.2) for h, a in games])


def _drive_game(spec):
    h, a, rh, ra, n, s = spec
    rng = random.Random(s)
    ea, eb = nfl_season._matchup_pts(rh, ra, +1) if k_override[0] is None else _pts(rh, ra, k_override[0])
    r1, r2 = nfl_season._drive_rates(ea), nfl_season._drive_rates(eb)
    w = 0
    for _ in range(n):
        ph, pa = nfl_season._play(r1, r2, rng)
        w += ph > pa
    return w / n


k_override = [None]


def _pts(ra, rb, k):
    margin = (ra - rb) * k + nfl_season._HFA_MARGIN
    return nfl_season._BASE_PPG + margin / 2.0, nfl_season._BASE_PPG - margin / 2.0


_DEEP = {}


def _deep_init(profiles):
    import nfl_deep_sim as E
    _DEEP["T"] = E.tables()
    _DEEP["P"] = profiles


def _deep_game(spec):
    import nfl_deep_sim as E
    h, a, n, s = spec
    rng = random.Random(s)
    w = mg = 0.0
    for _ in range(n):
        g = E.play_game(_DEEP["P"][h], _DEEP["P"][a], rng, _DEEP["T"])
        d = g["h"]["pts"] - g["a"]["pts"]
        w += 1.0 if d > 0 else (0.5 if d == 0 else 0.0)
        mg += d
    return w / n, mg / n


def deep_profiles(seed, k):
    """League-average units for every team, levels set by production's apply_anchor with market_margins
    returning exactly what it computes from a ladder: (W - 8.5) x k."""
    import nfl_deep_sim as E
    import nfl_deep_data as D
    T = E.tables()
    L = E.league_profile(T)
    profs = {ab: json.loads(json.dumps(dict(L, abbr=ab, g=0))) for ab in nfl_season._DIV_OF}
    orig = D.market_margins
    try:
        D.market_margins = lambda _s, recs=None: ({ab: (w - 8.5) * k for ab, w in seed.items()}, "c3_margin_scale")
        D.apply_anchor(profs, 2025, T)
    finally:
        D.market_margins = orig
    return profs


def board_effect(k_new, n=2000, procs=4, season="2026"):
    """The current week's deep figures with the live ladder anchored at 2.25 against k_new (league-average units,
    so this is the LEVEL's effect alone; production's units shape each game further). Reads Kalshi's ladder and
    ESPN's schedule live; nothing is written anywhere but the returned dict."""
    import multiprocessing as mp
    recs = nfl_season._banked(season)
    pace = nfl_season.market_pace(nfl_season._market_wins(), recs)
    week = next(w for w in range(1, 19) if any(not g["final"] for g in nfl_season._week_games(season, w)))
    games = [(g["home"], g["away"]) for g in nfl_season._week_games(season, week) if not g["final"]]
    ctx = mp.get_context("fork")
    out = {"season": season, "week": week, "k_new": k_new, "n_per_game": n, "games": []}
    p = {}
    for k in (nfl_season._PTS_PER_WIN, k_new):
        profs = deep_profiles(pace, k)
        specs = [(h, a, n, zlib.crc32(f"board|{h}|{a}".encode())) for h, a in games]   # same seeds at both k
        with ctx.Pool(procs, initializer=_deep_init, initargs=(profs,)) as pool:
            p[k] = [r[0] for r in pool.map(_deep_game, specs)]
    for i, (h, a) in enumerate(games):
        p0, p1 = p[nfl_season._PTS_PER_WIN][i], p[k_new][i]
        out["games"].append({"game": f"{a} @ {h}", "pace_home": pace.get(h), "pace_away": pace.get(a),
                             "deep_p_home_2.25": round(p0, 3), f"deep_p_home_{k_new}": round(p1, 3),
                             "headline_shift_at_0.30": round(0.30 * (p1 - p0), 4)})
    return out


def main(argv):
    import argparse
    import multiprocessing as mp
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-drive", type=int, default=4000)
    ap.add_argument("--n-deep", type=int, default=500)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--board", type=float, default=None, help="only the current week's effect at this k")
    a = ap.parse_args(argv)
    if a.board:
        eff = board_effect(a.board, procs=a.procs)
        path = os.path.join(ROOT, "research", "data", "c3_margin_scale_board.json")
        with open(path, "w") as fh:
            json.dump(eff, fh, indent=1, sort_keys=True)
        print(json.dumps(eff, indent=1))
        return
    seed, games = setup()
    out = {"seed_vector": "2025 real wins shrunk %.1f toward 8.5" % SHRINK, "schedule": "2025, 272 games",
           "seed_sd": round(math.sqrt(sum((w - 8.5) ** 2 for w in seed.values()) / 32), 3), "readers": {}}
    # analytic: exact expectations; the K that returns slope 1 by bisection
    for k in (2.25, 1.66):
        b, se = slope(seed, analytic(seed, games, k))
        out["readers"][f"analytic_k{k}"] = {"slope": round(b, 4), "se": round(se, 4)}
    lo, hi = 1.0, 3.0
    for _ in range(50):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if slope(seed, analytic(seed, games, mid))[0] < 1 else (lo, mid)
    out["analytic_k_for_slope_1"] = round((lo + hi) / 2, 3)
    ctx = mp.get_context("fork")
    for k in (2.25, 1.66):
        k_override[0] = None if k == nfl_season._PTS_PER_WIN else k
        specs = [(h, aw, seed[h], seed[aw], a.n_drive, zlib.crc32(f"drive|{k}|{i}".encode()))
                 for i, (h, aw) in enumerate(games)]
        with ctx.Pool(a.procs) as pool:
            ps = pool.map(_drive_game, specs, chunksize=8)
        b, se = slope(seed, expected_wins(games, ps))
        out["readers"][f"drive_k{k}"] = {"slope": round(b, 4), "se": round(se, 4), "n_per_game": a.n_drive}
        print(f"drive k {k}: slope {b:.3f} +/- {se:.3f}", flush=True)
    for k in (2.25, 1.95):
        profs = deep_profiles(seed, k)
        specs = [(h, aw, a.n_deep, zlib.crc32(f"deep|{k}|{i}".encode())) for i, (h, aw) in enumerate(games)]
        with ctx.Pool(a.procs, initializer=_deep_init, initargs=(profs,)) as pool:
            res = pool.map(_deep_game, specs, chunksize=4)
        b, se = slope(seed, expected_wins(games, [r[0] for r in res]))
        # anchor fidelity: the realized mean margin per game against the anchored one
        tgt = [((seed[h] - seed[aw]) * k) for h, aw in games]
        rl = [r[1] for r in res]
        mt, mr = sum(tgt) / len(tgt), sum(rl) / len(rl)
        fid = sum((t - mt) * (r - mr) for t, r in zip(tgt, rl)) / sum((t - mt) ** 2 for t in tgt)
        out["readers"][f"deep_k{k}"] = {"slope": round(b, 4), "se": round(se, 4), "n_per_game": a.n_deep,
                                        "realized_margin_on_anchored": round(fid, 3),
                                        "levels": {ab: profs[ab]["level"] for ab in ("KC", "TEN")}}
        print(f"deep k {k}: slope {b:.3f} +/- {se:.3f}  realized/anchored margin {fid:.3f}", flush=True)
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
