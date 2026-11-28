"""The D/ST tail, real against the deep engine with and without the fumble-return TD (2026-11-28).

research/reports/fumble_td_fix.md section C. DESCRIPTIVE: nothing here chooses a number the patch ships.

REAL. Every regular-season game of 2025 and of 2026 weeks 1-12 (the coaching seal: week 13 on is never read),
nflverse play-by-play. Each team-game's DraftKings D/ST points under the rule production scores by
(nfl_dfs_engine.dk_dst, dk_scoring.NFL_DST / NFL_DST_PA):
- points allowed = the opponent's final score less six for each of the opponent DEFENSE's pick-sixes and
  fumble-return touchdowns (the owner's verified rule, 2026-11-26: their tries count) and less the safeties the
  opponent scored (production's convention: the owner's table does not list safeties, an open question);
- sacks 1, interceptions 2, fumble recoveries 2, every return touchdown 6 (pick-six, fumble return, kickoff,
  punt, blocked or missed kick), safeties 2, blocked punts and field goals 2.
"Engine scope" drops what the engine cannot play at all (special-teams fumbles and their touchdowns, blocked
and missed kick returns), so the two can be compared like for like.

ENGINE. The same games, each played `n` times by the deep engine on league-average units whose OFFENSE levels
are anchored to the closing line's implied team totals ((total +/- spread) / 2, nflverse's spread_line, home
positive) -- one analytic step at the engine's points elasticity (nfl_deep_data._ENGINE_PTS_ELASTICITY) from
the engine's own league-vs-league points, home and away measured apart, the DFS worlds' shared passing
environment on (nfl_dfs_engine.ENV_SD). The closest reproducible inputs: the real games' own unit profiles are
built from ESPN play-by-play this session does not hold. Three arms, every simulated game on the same seed:
- A: production (the live nfl_deep_sim);
- B: the patched engine on A's anchored profiles (the mechanism alone);
- C: the patched engine re-anchored on its own points (what production would play after the flip: the anchor
  levels each offense so that ALL its team's points, return scores included, meet the line).
Reported: the mean, P(>=10/15/20/25), the share of team-games with a D/ST touchdown, each with its SE (real:
clustered by game; engine: per simulated game, paired between arms), and the engine's touchdown mix per team-game
from a logged run (pick-sixes, fumble-return TDs, kick and punt return TDs) beside the real seasons'.

usage: VIGIL_NO_BG=1 python3 research/fumble_td_dst.py <nflverse dir> [--n 200] [--procs 4]
"""
import csv
import gzip
import json
import math
import os
import random
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

OUT = os.path.join(ROOT, "research", "data", "fumble_td_dst.json")
WINDOWS = ((2025, 99), (2026, 13))
THRESH = (10, 15, 20, 25)
_ST = ("punt", "kickoff", "field_goal", "extra_point")


# ---------------------------------------------------------------- real
def _team():
    return {"sacks": 0, "int": 0, "fr_scrim": 0, "fr_st": 0, "safety": 0, "blk": 0,
            "td_pick6": 0, "td_fum_scrim": 0, "td_fum_st": 0, "td_kick_punt": 0, "td_other": 0}


def real_games(d):
    games = {}
    for season, stop in WINDOWS:
        if season == 2026 and stop > 13:
            raise SystemExit("the coaching seal: 2026 week 13 on is never read")
        with gzip.open(os.path.join(d, f"play_by_play_{season}.csv.gz"), "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                if r["season_type"] != "REG" or int(r["week"] or 0) >= stop:
                    continue
                g = games.get(r["game_id"])
                if g is None:
                    g = games[r["game_id"]] = {
                        "season": season, "week": int(r["week"]), "home": r["home_team"], "away": r["away_team"],
                        "home_score": int(float(r["home_score"])), "away_score": int(float(r["away_score"])),
                        "spread": float(r["spread_line"]), "total": float(r["total_line"]),
                        "t": {r["home_team"]: _team(), r["away_team"]: _team()}}
                de = r["defteam"]
                st = r["special_teams_play"] == "1" or r["play_type"] in _ST
                if de in g["t"]:
                    t = g["t"][de]
                    t["sacks"] += r["sack"] == "1"
                    t["int"] += r["interception"] == "1"
                    t["safety"] += r["safety"] == "1"
                    t["blk"] += (r["punt_blocked"] == "1") + (r["field_goal_result"] == "blocked")
                if r["fumble_lost"] == "1":
                    rec = r["fumble_recovery_1_team"] or de
                    if rec in g["t"]:
                        g["t"][rec]["fr_st" if st else "fr_scrim"] += 1
                if r["return_touchdown"] == "1" and r["td_team"] in g["t"]:
                    t = g["t"][r["td_team"]]
                    if r["interception"] == "1":
                        t["td_pick6"] += 1
                    elif r["fumble_lost"] == "1":
                        t["td_fum_st" if st else "td_fum_scrim"] += 1
                    elif r["play_type"] in ("kickoff", "punt"):
                        t["td_kick_punt"] += 1
                    else:
                        t["td_other"] += 1
    return list(games.values())


def _tier(pa):
    import dk_scoring
    for lo, hi, p in dk_scoring.NFL_DST_PA:
        if lo <= pa <= hi:
            return p
    return -4


def real_dst(g, side, scope):
    """(DK points, had a TD) of `side`'s D/ST; scope 'dk' or 'engine'."""
    me, op = (g["home"], g["away"]) if side == 0 else (g["away"], g["home"])
    t, o = g["t"][me], g["t"][op]
    op_pts = g["away_score"] if side == 0 else g["home_score"]
    pa = op_pts - 6 * (o["td_pick6"] + o["td_fum_scrim"] + o["td_fum_st"]) - 2 * o["safety"]
    tds = t["td_pick6"] + t["td_fum_scrim"] + t["td_kick_punt"]
    fr = t["fr_scrim"]
    if scope == "dk":
        tds += t["td_fum_st"] + t["td_other"]
        fr += t["fr_st"]
    pts = _tier(pa) + t["sacks"] + 2 * t["int"] + 2 * fr + 6 * tds + 2 * t["safety"] + 2 * t["blk"]
    return pts, int(tds > 0)


def summarize_clustered(rows):
    """rows: [(game, [x_side0, x_side1])] -> mean and SE clustered by game."""
    G = len(rows)
    s = [a + b for a, b in rows]
    m = sum(s) / (2 * G)
    v = sum((x - 2 * m) ** 2 for x in s) / (G - 1)
    return m, math.sqrt(v) / (2 * math.sqrt(G))


def describe_real(games, scope):
    out = {}
    pts = [[real_dst(g, s, scope)[0] for s in (0, 1)] for g in games]
    td = [[real_dst(g, s, scope)[1] for s in (0, 1)] for g in games]
    out["mean"] = summarize_clustered(pts)
    for k in THRESH:
        out[f"p_ge_{k}"] = summarize_clustered([[float(x >= k) for x in p] for p in pts])
    out["td_game"] = summarize_clustered(td)
    return {k: [round(v[0], 4), round(v[1], 4)] for k, v in out.items()}


def real_td_mix(games):
    tg = 2 * len(games)
    keys = ("td_pick6", "td_fum_scrim", "td_fum_st", "td_kick_punt", "td_other", "fr_scrim")
    return {k: round(sum(g["t"][tm][k] for g in games for tm in g["t"]) / tg, 4) for k in keys} | {"team_games": tg}


# ---------------------------------------------------------------- engine
def _engine(arm):
    import nfl_deep_sim as E
    if arm == "A":
        return E
    from research import fumble_td_patch as Pt
    return Pt.load(True)


def _play(M, hp, ap, seed, n, T, env):
    import nfl_dfs_engine as G
    cols = [{k: [0] * n for k in G.BOX_KEYS + ("safety_conceded",)} for _ in (0, 1)]
    for i in range(n):
        g = M.play_game(hp, ap, random.Random(seed * 100003 + i), T, env_sd=env)
        for side, b in ((0, g["h"]), (1, g["a"])):
            for k in G.BOX_KEYS:
                cols[side][k][i] = b[k]
            cols[side]["safety_conceded"][i] = b["ends"]["safety"]
    import numpy as np
    return tuple({k: np.asarray(v, dtype=np.int64) for k, v in c.items()} for c in cols)


def _base_job(args):
    arm, n = args
    import nfl_deep_sim as E
    import nfl_dfs_engine as G
    T = E.tables()
    L = E.league_profile(T)
    bh, ba = _play(_engine(arm), L, L, 7, n, T, G.ENV_SD)
    return arm, float(bh["pts"].mean()), float(ba["pts"].mean())


def _profiles(L, targets, base, el):
    hp, ap = dict(L), dict(L)
    for prof, tgt, b in ((hp, targets[0], base[0]), (ap, targets[1], base[1])):
        prof["level_off"] = round((max(3.0, tgt) / b) ** (1.0 / el), 5)
        prof["level_def"] = 1.0
    return hp, ap


def _game_job(args):
    gi, targets, bases, n = args
    import nfl_deep_data as DD
    import nfl_deep_sim as E
    import nfl_dfs_engine as G
    T = E.tables()
    L = E.league_profile(T)
    el = float(DD._ENGINE_PTS_ELASTICITY)
    seed = zlib.crc32(f"fumtd|{gi}".encode())
    out = {}
    for arm, base_arm in (("A", "A"), ("B", "A"), ("C", "C")):
        hp, ap = _profiles(L, targets, bases[base_arm], el)
        bh, ba = _play(_engine(arm), hp, ap, seed, n, T, G.ENV_SD)
        d0, d1 = G.dk_dst(bh, ba), G.dk_dst(ba, bh)
        out[arm] = {"dst": [d0.tolist(), d1.tolist()],
                    "td": [(bh["td_ret"] > 0).astype(int).tolist(), (ba["td_ret"] > 0).astype(int).tolist()],
                    "pts": [float(bh["pts"].mean()), float(ba["pts"].mean())]}
    return gi, out


def engine_mix(n, arm):
    """Touchdown mix per team-game on league-average units, from the passive log (pick-six, fumble return,
    kick/punt return), with the lost-fumble rate."""
    import nfl_deep_sim as E
    import nfl_dfs_engine as G
    T = E.tables()
    L = E.league_profile(T)
    M = _engine(arm)
    c = {"pick6": 0, "fum_td": 0, "kick_punt_td": 0, "fum_lost": 0, "int": 0}
    for i in range(n):
        log = []
        g = M.play_game(L, L, random.Random(zlib.crc32(f"mix|{i}".encode())), T, env_sd=G.ENV_SD, log=log)
        for e in log:
            if e.get("six") and e["k"] == "int":
                c["pick6"] += 1
            elif e.get("six") and e.get("to") == "fum":
                c["fum_td"] += 1
            elif e["k"] in ("ko", "punt") and e.get("res") == "td":
                c["kick_punt_td"] += 1
        for s in ("h", "a"):
            c["fum_lost"] += g[s]["fum_lost"]
            c["int"] += g[s]["int"]
    return {k: round(v / (2 * n), 4) for k, v in c.items()} | {"games": n}


def _mean_se(x):
    n = len(x)
    m = sum(x) / n
    return m, math.sqrt(sum((v - m) ** 2 for v in x) / (n - 1) / n)


def describe_engine(res, arm, ref=None):
    """Per simulated team-game; with `ref`, the paired difference arm - ref too (same seeds)."""
    xs = [v for gi in res for side in (0, 1) for v in res[gi][arm]["dst"][side]]
    ts = [v for gi in res for side in (0, 1) for v in res[gi][arm]["td"][side]]
    out = {"mean": _mean_se(xs)}
    for k in THRESH:
        out[f"p_ge_{k}"] = _mean_se([float(v >= k) for v in xs])
    out["td_game"] = _mean_se([float(v) for v in ts])
    rnd = {k: [round(v[0], 4), round(v[1], 5)] for k, v in out.items()}
    if ref:
        xr = [v for gi in res for side in (0, 1) for v in res[gi][ref]["dst"][side]]
        tr = [v for gi in res for side in (0, 1) for v in res[gi][ref]["td"][side]]
        dif = {"mean": _mean_se([a - b for a, b in zip(xs, xr)])}
        for k in THRESH:
            dif[f"p_ge_{k}"] = _mean_se([float(a >= k) - float(b >= k) for a, b in zip(xs, xr)])
        dif["td_game"] = _mean_se([float(a) - float(b) for a, b in zip(ts, tr)])
        rnd["minus_" + ref] = {k: [round(v[0], 4), round(v[1], 5)] for k, v in dif.items()}
    return rnd


def main(argv):
    d = argv[0]
    n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 200
    procs = int(argv[argv.index("--procs") + 1]) if "--procs" in argv else 4
    games = real_games(d)
    games.sort(key=lambda g: (g["season"], g["week"], g["home"]))
    import multiprocessing as mp
    with mp.Pool(procs) as pool:
        bases = {arm: (h, a) for arm, h, a in pool.map(_base_job, [("A", 20000), ("C", 20000)])}
        jobs = [(gi, ((g["total"] + g["spread"]) / 2, (g["total"] - g["spread"]) / 2), bases, n)
                for gi, g in enumerate(games)]
        res = dict(pool.map(_game_job, jobs, chunksize=4))
    tgt = [t for _, ts, _, _ in jobs for t in ts]
    landed = {arm: round(sum(p for gi in res for p in res[gi][arm]["pts"]) / (2 * len(res)), 3) for arm in "ABC"}
    out = {"games": len(games), "seasons": "2025 + 2026 weeks 1-12", "n_per_game": n,
           "bases_league_vs_league": {k: [round(v[0], 3), round(v[1], 3)] for k, v in bases.items()},
           "mean_target_pts": round(sum(tgt) / len(tgt), 3), "engine_mean_pts": landed,
           "real_dk": describe_real(games, "dk"), "real_engine_scope": describe_real(games, "engine"),
           "real_td_mix": real_td_mix(games),
           "engine": {"A": describe_engine(res, "A"), "B": describe_engine(res, "B", "A"),
                      "C": describe_engine(res, "C", "A")},
           "engine_mix": {"A": engine_mix(20000, "A"), "C": engine_mix(20000, "C")}}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
