"""EXPERIMENTAL, RESEARCH ONLY: DEN @ PIT played by the production deep engine
(nfl_deep_sim.play_game) and by the coaching engine R5 (nfl_coach_sim_r5, which
carries R1's clock, R2's situational deal, R3/R5's quarterback pull, R4's
personnel calls), on the SAME team profiles -- the anchored profiles the
board's DFS worlds were played on (pool_boxes.py's profiles.pkl) -- at
production's form tilt, home edge and passing environment (ENV_SD), as
research/nfl_coach_dfs.py sets R1 up. Nothing here is validated: R1-R5's
registered reads are in 2027-2029. Nothing reads the game's result.

The two engines draw from their generators in different orders, so their
games are NOT common random numbers: every difference below carries the
UNPAIRED standard error (sqrt(se1^2 + se2^2)).

    python3 research/den_pit_audit/r5_compare.py --boxes <pool_boxes out dir> --n 20000 --out <json>
"""
import argparse
import json
import math
import os
import pickle
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--boxes", required=True)
ap.add_argument("--n", type=int, default=20000)
ap.add_argument("--seed", type=int, default=common.SEED)
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--out", required=True)
ap.add_argument("--base", choices=("r5", "deep"), default="r5",
                help="r5: R5's own registered tables (nflverse 2016-2023 base); deep: R5's coaching tables on "
                     "production's deep-engine base tables, as research/nfl_coach_dfs.py sets R1 up")
a = ap.parse_args()
sys.path.insert(0, common.ROOT)
sys.path.insert(0, os.path.join(common.ROOT, "research"))

import multiprocessing as mp  # noqa: E402
import numpy as np  # noqa: E402
import nfl_coach_sim_r2 as E2  # noqa: E402
import nfl_coach_sim_r3 as E3  # noqa: E402
import nfl_coach_sim_r4 as E4  # noqa: E402
import nfl_coach_sim_r5 as E5  # noqa: E402
import nfl_deep_sim as D  # noqa: E402
import nfl_dfs_engine as NE  # noqa: E402
from nfl_dfs_csim import _line  # noqa: E402

PR = pickle.load(open(os.path.join(a.boxes, "profiles.pkl"), "rb"))
HP, AP = PR["anchored"]
HOME, AWAY = PR["home"], PR["away"]
NEUTRAL = bool(PR["play"]["neutral"])
ENV = NE.ENV_SD if PR["play"]["env_sd"] is None else float(PR["play"]["env_sd"])   # play_worlds' default
SNAPS = E2._SNAPS
if a.base == "deep":
    import nfl_coach_dfs
    R5_T = nfl_coach_dfs.coach_tables(D.tables())
else:
    R5_T = None                                  # play_game's default: R5's own tables
DROP = ("cmp", "inc", "int", "sack", "spike")


def r2_players(team):
    """Sleeper's lines for `team` in R2's player format, the quarterback with
    the most projected attempts first."""
    out = []
    for p in PR["players"]:
        if p.get("team") != team or p.get("actual_only"):
            continue
        ln = _line(p)
        pos = (p.get("pos") or "").upper()
        if pos not in ("QB", "RB", "WR", "TE"):
            continue
        car, tgt = ln.get("rush_att", 0.0), ln.get("rec_tgt", 0.0)
        if pos == "QB" and ln.get("pass_att", 0.0) <= 0 and car <= 0:
            continue
        if pos != "QB" and car <= 0 and tgt <= 0:
            continue
        rec = ln.get("rec", 0.0)
        out.append({"id": p["name"], "pos": pos, "car": car, "tgt": tgt,
                    "ypc": (ln.get("rush_yd", 0.0) / car) if car > 0 else 4.0,
                    "ypr": (ln.get("rec_yd", 0.0) / rec) if rec > 0 else 10.0,
                    "cr": (rec / tgt) if tgt > 0 else 0.6, "_att": ln.get("pass_att", 0.0)})
    qbs = sorted([p for p in out if p["pos"] == "QB"], key=lambda p: -p["_att"])
    rest = [p for p in out if p["pos"] != "QB"]
    return qbs + rest


def log_stats(log):
    """Per side: plays, dropbacks, runs, time of possession (s), Q2/Q4 snaps
    inside 2:00, Q4 carries and rush yards, dropbacks inside the last 5:00."""
    st = [dict(plays=0, drop=0, run=0, top=0.0, q2_2m=0, q4_2m=0, q4_car=0, q4_ryd=0, l5_drop=0, kneel=0)
          for _ in (0, 1)]
    for i, e in enumerate(log):
        k = e.get("k")
        if k not in SNAPS or "o" not in e:
            continue
        s = st[e["o"]]
        q, c = e["q"], e["c"]
        nxt = log[i + 1] if i + 1 < len(log) else None
        if nxt is not None and nxt.get("q") == q:
            s["top"] += max(0.0, c - nxt["c"])
        if k == "kneel":
            s["kneel"] += 1
        s["plays"] += 1
        if k in DROP:
            s["drop"] += 1
            if q == 4 and c <= 300:
                s["l5_drop"] += 1
        elif k == "run":
            s["run"] += 1
            if q == 4:
                s["q4_car"] += 1
                s["q4_ryd"] += int(e.get("yd", 0))
        if q == 2 and c <= 120:
            s["q2_2m"] += 1
        if q == 4 and c <= 120:
            s["q4_2m"] += 1
    return st


def play_block(job):
    eng, seed, n = job
    rng = random.Random(seed)
    prng = random.Random((seed * 6007 + 31337) & 0x7FFFFFFF)
    out = []
    for _ in range(n):
        log = []
        if eng == "prod":
            r = D.play_game(HP, AP, rng, neutral=NEUTRAL, env_sd=ENV, log=log)
            sn = None
        else:
            r = E5.play_game(HP, AP, rng, prng, T=R5_T, neutral=NEUTRAL, env_sd=ENV, form_sd=D._FORM_SD,
                             hfa=D._HFA_YDS)
            log = r.pop("log")
            sn = E3.snaps(log, (0, 0))
        out.append({"h": r["h"], "a": r["a"], "qb": r.get("qb"), "st": log_stats(log), "sn": sn,
                    "per": r.get("per"), "ot": r.get("ot")})
    return out


def se_mean(x):
    x = np.asarray(x, dtype=np.float64)
    return float(x.std(ddof=1) / math.sqrt(len(x)))


def q_se(x, q, B=200, seed=7):
    x = np.asarray(x, dtype=np.float64)
    r = np.random.default_rng(seed)
    v = [np.quantile(x[r.integers(0, len(x), len(x))], q) for _ in range(B)]
    return float(np.std(v, ddof=1))


def dist(x):
    x = np.asarray(x, dtype=np.float64)
    return {"mean": round(float(x.mean()), 3), "se": round(se_mean(x), 3), "sd": round(float(x.std(ddof=1)), 3),
            **{f"p{int(q * 100)}": round(float(np.quantile(x, q)), 2) for q in (0.1, 0.25, 0.5, 0.75, 0.9)},
            "p90_se": round(q_se(x, 0.9), 3), "p10_se": round(q_se(x, 0.1), 3)}


def prop(b):
    b = np.asarray(b, dtype=np.float64)
    p = float(b.mean())
    return {"p": round(p, 4), "se": round(math.sqrt(max(p * (1 - p), 1e-12) / len(b)), 4)}


def corr(x, y):
    r = float(np.corrcoef(x, y)[0, 1])
    return {"r": round(r, 3), "se": round((1 - r * r) / math.sqrt(len(x) - 1), 3)}


def box_arrays(games, side):
    keys = NE.BOX_KEYS
    out = {k: np.asarray([g[side][k] for g in games]) for k in keys}
    out["safety_conceded"] = np.asarray([g[side]["ends"]["safety"] for g in games])
    return out


def summarize(eng, games, lines=None):
    n = len(games)
    H, A = box_arrays(games, "h"), box_arrays(games, "a")
    margin = H["pts"] - A["pts"]                 # home minus away
    res = {"engine": eng, "n": n, "home": HOME, "away": AWAY}
    res["pts"] = {HOME: dist(H["pts"]), AWAY: dist(A["pts"]), "total": dist(H["pts"] + A["pts"]),
                  "margin_home": dist(margin)}
    am = np.abs(margin)
    res["close"] = {"le3": prop(am <= 3), "le7": prop(am <= 7), "ge17": prop(am >= 17), "ge24": prop(am >= 24),
                    "home_win": prop(margin > 0), "tie": prop(margin == 0)}
    for side, t in ((0, HOME), (1, AWAY)):
        st = [g["st"][side] for g in games]
        g_ = {k: np.asarray([s[k] for s in st], dtype=np.float64) for k in st[0]}
        plays_ex = g_["plays"] - g_["kneel"]
        res[f"flow_{t}"] = {"plays": dist(g_["plays"]), "pass_rate": dist(g_["drop"] / np.maximum(1, plays_ex)),
                            "top_min": dist(g_["top"] / 60.0), "q2_inside2": dist(g_["q2_2m"]),
                            "q4_inside2": dist(g_["q4_2m"]), "q4_carries": dist(g_["q4_car"]),
                            "q4_rush_yd": dist(g_["q4_ryd"]), "last5_dropbacks": dist(g_["l5_drop"])}
    res["dst"] = {HOME: dist(NE.dk_dst(H, A)), AWAY: dist(NE.dk_dst(A, H))}
    res["k"] = {HOME: dist(NE.dk_kicker(H)), AWAY: dist(NE.dk_kicker(A))}
    if games[0].get("qb"):
        for side, t in ((0, HOME), (1, AWAY)):
            qs = [g["qb"][side] for g in games]
            pulled = np.array([q.get("pull") is not None for q in qs])
            kinds = [q.get("kind") for q in qs if q.get("pull") is not None]
            when = []
            for g, q in zip(games, qs):
                if q.get("pull") is None:
                    continue
                first = next((s for s in g["sn"] if s[0] == side and s[6] >= q["pull"]), None)
                if first is not None:
                    when.append((first[7], first[8], first[9]))
            res[f"qb_pull_{t}"] = {"rate": prop(pulled), "strategic": sum(1 for k in kinds if k == "strategic"),
                                   "injury": sum(1 for k in kinds if k == "injury"),
                                   "period_counts": {str(q): sum(1 for w in when if w[0] == q) for q in (1, 2, 3, 4, 5)},
                                   "median_clock_s": float(np.median([w[1] for w in when])) if when else None,
                                   "median_margin_at_pull": float(np.median([w[2] for w in when])) if when else None}
    return res, H, A


def dk_pts(ln_list):
    keys = {"pass_yd": "pass_yd", "pass_td": "pass_td", "int": "int", "rush_yd": "rush_yd", "rush_td": "rush_td",
            "rec": "rec", "rec_yd": "rec_yd", "rec_td": "rec_td", "fum": "fum"}
    c = {k: np.asarray([ln.get(v, 0) for ln in ln_list], dtype=np.int64) for k, v in keys.items()}
    return NE.dk_offense(c, len(ln_list))


if __name__ == "__main__":
    w = max(1, a.workers)
    per = int(math.ceil(a.n / (4 * w)))
    blocks = []
    for eng in ("prod", "r5"):
        for b in range(4 * w):
            blocks.append((eng, (a.seed * 7919 + b * 104729 + (0 if eng == "prod" else 1)) & 0x7FFFFFFF,
                           min(per, a.n - b * per)))
    blocks = [b for b in blocks if b[2] > 0]
    with mp.get_context("fork").Pool(w) as pool:
        parts = pool.map(play_block, blocks)
    G = {"prod": [], "r5": []}
    for b, p in zip(blocks, parts):
        G[b[0]].extend(p)
    out = {"n": a.n, "base": a.base, "profiles": "anchored (the board's DFS worlds)", "env_sd": ENV, "home": HOME, "away": AWAY,
           "engines": {"prod": "nfl_deep_sim.play_game (production)", "r5": E5.VERSION}}
    sp, Hp, Ap = summarize("prod", G["prod"])
    s5, H5, A5 = summarize("r5", G["r5"])
    # R5's deal (R2's situational deal + R5's second unit) on Sleeper's usage lines
    U, X, Q = E2.tables(), E4.tables(), E5.tables()
    players = {HOME: r2_players(HOME), AWAY: r2_players(AWAY)}
    logs = [g["sn"] for g in G["r5"]]
    mixes = E2.mix_of([[x[:6] for x in sn] for sn in logs])
    teams = [E2.TeamDeal(list(players[t]), U, mix=mixes[s_], situational=True) for s_, t in ((0, HOME), (1, AWAY))]
    bks = [E3.backup_of(list(players[t]))["id"] for t in (HOME, AWAY)]
    lines = {HOME: {}, AWAY: {}}
    for j, (g, sn) in enumerate(zip(G["r5"], logs)):
        for side, t in ((0, HOME), (1, AWAY)):
            drng = random.Random((a.seed * 1000003 + 2 * j + side) & 0x7FFFFFFF)
            ln = E5.deal([x for x in sn if x[0] == side], g["h" if side == 0 else "a"], teams[side], drng,
                         bks[side], g["qb"][side], X, U, Q)
            for pid, v in ln.items():
                lines[t].setdefault(pid, []).append(v)
    r5_pts = {pid: dk_pts(v) for t in lines for pid, v in lines[t].items() if len(v) == len(G["r5"])}
    r5_pts[f"DST_{HOME}"] = NE.dk_dst(H5, A5)
    r5_pts[f"DST_{AWAY}"] = NE.dk_dst(A5, H5)
    r5_pts[f"K_{HOME}"] = NE.dk_kicker(H5)
    r5_pts[f"K_{AWAY}"] = NE.dk_kicker(A5)
    usage = {}
    for t in (HOME, AWAY):
        for pid, v in lines[t].items():
            car = np.asarray([x["car"] for x in v], dtype=np.float64)
            tgt = np.asarray([x["tgt"] for x in v], dtype=np.float64)
            usage[pid] = {"team": t, "car": dist(car), "tgt": dist(tgt)}
    out["prod"], out["r5"] = sp, s5
    out["r5_players"] = {k: dist(v) for k, v in r5_pts.items()}
    out["r5_usage"] = usage
    out["r2_players"] = players
    np.savez_compressed(os.path.splitext(a.out)[0] + "_r5pts.npz", **{k: np.asarray(v) for k, v in r5_pts.items()},
                        prod_home_pts=Hp["pts"], prod_away_pts=Ap["pts"], r5_home_pts=H5["pts"], r5_away_pts=A5["pts"])
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("DONE", a.out)
