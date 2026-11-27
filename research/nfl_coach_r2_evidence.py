"""The evidence audit for the coaching engine's second revision
(research/reports/nfl_coach_r2_evidence.md). RESEARCH: reads nflverse
play-by-play (CC BY 4.0) through research/nfl_coach_r2_data.py, which keeps R1's
seal -- 2026 week 13 on is never read here. Writes only aggregates
(research/data/nfl_coach/r2_evidence.json).

    python3 -m research.nfl_coach_r2_evidence <pbp dir> <players.csv> [<ftn dir>]

One section a mechanism, each a function returning plain numbers. Every
tendency is measured against its own league-season mean (the league's pass rate
and fourth-down aggressiveness moved over 2016-2025), every correlation carries
its n, and a "no evidence" is said where the interval holds zero.
"""
import csv
import json
import math
import os
import random
import sys
from collections import defaultdict

import nfl_coach_sim_r2 as E2
from research import nfl_coach_r2_data as R

DEV_SEASONS = tuple(range(2016, 2026))       # 2016-2025; 2026 weeks 1-12 apart
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r2_evidence.json")
PASSK = (R.K_SCR, R.K_PASS, R.K_SACK)


# ---- small statistics ----------------------------------------------------------------
def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else float("nan")


def r_se(r, n):
    return (1 - r * r) / math.sqrt(max(n - 2, 1)) if r == r else float("nan")


def slope(xs, ys):
    mx, my = mean(xs), mean(ys)
    sxx = sum((a - mx) ** 2 for a in xs)
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sxx if sxx > 0 else float("nan")


def corr_rec(xs, ys):
    r = pearson(xs, ys)
    return {"r": round(r, 3), "se": round(r_se(r, len(xs)), 3), "n": len(xs),
            "slope": round(slope(xs, ys), 3) if len(xs) > 2 else None}


def sb(r):
    """Spearman-Brown: a split-half r to the full sample's reliability."""
    return 2 * r / (1 + r) if r == r and r > -1 else float("nan")


def ols(X, y, w=None, cluster=None):
    """Weighted least squares with cluster-robust standard errors (numpy)."""
    import numpy as np
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    w = np.ones(len(y)) if w is None else np.asarray(w, dtype=float)
    Xw = X * w[:, None]
    A = np.linalg.inv(X.T @ Xw)
    b = A @ (Xw.T @ y)
    e = y - X @ b
    if cluster is None:
        cluster = np.arange(len(y))
    cl = defaultdict(lambda: np.zeros(X.shape[1]))
    for xi, ei, wi, c in zip(X, e, w, cluster):
        cl[c] += xi * ei * wi
    M = sum(np.outer(v, v) for v in cl.values())
    V = A @ M @ A
    return b.tolist(), np.sqrt(np.diag(V)).tolist()


def neutral(p):
    return (p[R.Q_KIND] in R.SCRIMMAGE and 1 <= p[R.Q_DOWN] <= 3 and p[R.Q_XP] >= 0
            and 200 <= p[R.Q_WP] <= 800 and p[R.Q_HSR] > 120 and p[R.Q_PER] <= 4)


def is_pass(p):
    return p[R.Q_KIND] in PASSK


def side_plays(g, side):
    """The plays this side (0 home, 1 away) had the ball on, in order."""
    h = 1 if side == 0 else 0
    return [p for p in g["plays"] if p[R.Q_OFFH] == h]


def coach(g, side):
    return g["home_coach"] if side == 0 else g["away_coach"]


def team(g, side):
    return g["home"] if side == 0 else g["away"]


# ---- 1. head-coach tendencies, 2. regime changes, 9. the defence's side -----------------
def _fourth_cells(data):
    """League go rate per season x (distance, field, score) cell."""
    cnt = defaultdict(lambda: [0, 0])
    for s, d in data.items():
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for p in g["plays"]:
                k = _fourth_key(p)
                if k is not None:
                    c = cnt[(s,) + k]
                    c[0] += 1
                    c[1] += p[R.Q_KIND] in R.SCRIMMAGE
    return {k: v[1] / v[0] for k, v in cnt.items()}


def _fourth_key(p):
    if p[R.Q_DOWN] != 4 or p[R.Q_KIND] not in R.SCRIMMAGE + (R.K_PUNT, R.K_FG):
        return None
    if not (50 <= p[R.Q_WP] <= 950) or p[R.Q_HSR] <= 120 or p[R.Q_PER] > 4 or p[R.Q_YL] < 1:
        return None
    dist = p[R.Q_DIST]
    db = 1 if dist <= 1 else 2 if dist <= 2 else 3 if dist <= 3 else 4 if dist <= 5 else 5 if dist <= 10 else 6
    yb = min(9, (p[R.Q_YL] - 1) // 10)
    sd = p[R.Q_SD]
    sb_ = 0 if sd <= -9 else 1 if sd < 0 else 2 if sd == 0 else 3 if sd <= 8 else 4
    return (db, yb, sb_)


def team_games(data, cells):
    """One record a team-game (regular season): its offence's tendencies and
    what its defence allowed."""
    out = []
    for s, d in data.items():
        for g in d["games"]:
            if g["st"] != 2:
                continue
            per_side = []
            for side in (0, 1):
                pl = side_plays(g, side)
                r = {"season": s, "week": g["week"], "gid": g["id"], "team": team(g, side),
                     "coach": coach(g, side), "opp": team(g, 1 - side), "opp_coach": coach(g, 1 - side),
                     "proe": [0.0, 0], "nh": [0, 0], "pace": [0, 0], "go": [0.0, 0], "two": [0, 0],
                     "epa": [0.0, 0], "rush_epa": [0.0, 0], "pass_epa": [0.0, 0]}
                prev = None
                for p in pl:
                    k = p[R.Q_KIND]
                    if neutral(p):
                        r["proe"][0] += is_pass(p) - p[R.Q_XP] / 1000.0
                        r["proe"][1] += 1
                        r["nh"][0] += p[R.Q_NH]
                        r["nh"][1] += 1
                        r["epa"][0] += p[R.Q_EPA] / 1000.0
                        r["epa"][1] += 1
                        if k == R.K_RUN:
                            r["rush_epa"][0] += p[R.Q_EPA] / 1000.0
                            r["rush_epa"][1] += 1
                        else:
                            r["pass_epa"][0] += p[R.Q_EPA] / 1000.0
                            r["pass_epa"][1] += 1
                    fk = _fourth_key(p)
                    if fk is not None:
                        r["go"][0] += (k in R.SCRIMMAGE) - cells[(s,) + fk]
                        r["go"][1] += 1
                    if k in R.SCRIMMAGE and p[R.Q_TD]:
                        r["two"][1] += 1
                    if k in (R.K_TWO_RUN, R.K_TWO_PASS):
                        r["two"][0] += 1
                    # pace: the seconds from a neutral in-bounds run to the next snap of the same drive
                    if (prev is not None and prev[R.Q_KIND] == R.K_RUN and k in R.SCRIMMAGE
                            and prev[R.Q_DRIVE] == p[R.Q_DRIVE] and prev[R.Q_PER] == p[R.Q_PER]
                            and not prev[R.Q_TD] and not prev[R.Q_FUM] and neutral(prev)):
                        gap = prev[R.Q_GSR] - p[R.Q_GSR]
                        if 0 < gap <= 60:
                            r["pace"][0] += gap
                            r["pace"][1] += 1
                    if k in R.SCRIMMAGE:
                        prev = p
                per_side.append(r)
            # what each defence allowed is the other side's offence
            for side in (0, 1):
                o = per_side[1 - side]
                per_side[side]["d_proe"] = list(o["proe"])
                per_side[side]["d_rush_epa"] = list(o["rush_epa"])
                per_side[side]["d_pass_epa"] = list(o["pass_epa"])
            out.extend(per_side)
    return out


STATS = ("proe", "go", "nh", "pace", "two", "d_proe")


def _rate(recs, stat):
    a = sum(r[stat][0] for r in recs)
    n = sum(r[stat][1] for r in recs)
    return (a / n, n) if n else (None, 0)


def units(tg):
    """(season, team) -> {"coach": the coach of most games, "games": [...]}."""
    u = defaultdict(list)
    for r in tg:
        u[(r["season"], r["team"])].append(r)
    out = {}
    for k, recs in u.items():
        recs.sort(key=lambda r: r["week"])
        cs = defaultdict(int)
        for r in recs:
            cs[r["coach"]] += 1
        c, n = max(cs.items(), key=lambda kv: kv[1])
        out[k] = {"coach": c, "pure": n == len(recs), "games": recs,
                  "last_coach": recs[-1]["coach"], "first_coach": recs[0]["coach"]}
    return out


def league_means(tg):
    m = {}
    for stat in STATS:
        by = defaultdict(list)
        for r in tg:
            by[r["season"]].append(r)
        m[stat] = {s: _rate(v, stat)[0] for s, v in by.items()}
    return m


def tendencies(tg, U, LM):
    """Split-half reliability, the shrinkage K, year-to-year persistence with the
    same head coach, with a new one, and for a coach who changed teams."""
    res = {}
    for stat in STATS:
        dev = {}
        halves = ([], [])
        within, nper = [], []
        for k, u in U.items():
            recs = [r for r in u["games"] if r["coach"] == u["coach"]]
            v, n = _rate(recs, stat)
            if v is None or len(recs) < 8:
                continue
            dev[k] = (v - LM[stat][k[0]], n, len(recs))
            a, _ = _rate(recs[0::2], stat)
            b, _ = _rate(recs[1::2], stat)
            if a is not None and b is not None:
                halves[0].append(a - LM[stat][k[0]])
                halves[1].append(b - LM[stat][k[0]])
            # per-game sampling variance of this unit's rate
            gv = [(r[stat][0] / r[stat][1], r[stat][1]) for r in recs if r[stat][1]]
            if len(gv) >= 4:
                mu = sum(x * w for x, w in gv) / sum(w for _, w in gv)
                var_g = sum((x - mu) ** 2 for x, _ in gv) / (len(gv) - 1)
                within.append(var_g)
                nper.append(len(gv))
        rh = pearson(*halves)
        xs = [v[0] for v in dev.values()]
        var_obs = sum((x - mean(xs)) ** 2 for x in xs) / (len(xs) - 1)
        noise = mean([w / n for w, n in zip(within, nper)])
        tau2 = max(var_obs - noise, 1e-12)
        k_games = mean(within) / tau2
        same, new, mover = ([], []), ([], []), ([], [])
        for (s, t), v in dev.items():
            p = dev.get((s - 1, t))
            if p is None:
                continue
            prev_u, cur_u = U[(s - 1, t)], U[(s, t)]
            if prev_u["last_coach"] == cur_u["first_coach"] == cur_u["coach"] == prev_u["coach"]:
                same[0].append(p[0])
                same[1].append(v[0])
            elif cur_u["first_coach"] != prev_u["last_coach"]:
                new[0].append(p[0])
                new[1].append(v[0])
        # movers: a coach's last full season at one team, his first at the next
        by_coach = defaultdict(list)
        for (s, t), v in dev.items():
            by_coach[U[(s, t)]["coach"]].append((s, t, v[0]))
        for c, lst in by_coach.items():
            lst.sort()
            for (s0, t0, v0), (s1, t1, v1) in zip(lst, lst[1:]):
                if t1 != t0:
                    mover[0].append(v0)
                    mover[1].append(v1)
        res[stat] = {"units": len(dev), "split_half": corr_rec(*halves), "reliability_full": round(sb(rh), 3),
                     "sd_obs": round(math.sqrt(var_obs), 4), "sd_true": round(math.sqrt(tau2), 4),
                     "k_games": round(k_games, 1), "yoy_same_coach": corr_rec(*same),
                     "yoy_new_coach": corr_rec(*new), "coach_moved": corr_rec(*mover)}
    return res


def regime_prior(U, LM, stat, weeks=(1, 6)):
    """Predict a team's weeks 1-6 rate (its deviation from the league season)
    from last season's deviation: one pooled persistence (production's form)
    against a persistence by regime (same head coach / new). Each persistence is
    fitted leaving the predicted season out. Errors weighted by the plays read."""
    rows = []
    for (s, t), u in U.items():
        pu = U.get((s - 1, t))
        if pu is None:
            continue
        prev, _ = _rate([r for r in pu["games"]], stat)
        early = [r for r in u["games"] if weeks[0] <= r["week"] <= weeks[1]]
        cur, n = _rate(early, stat)
        if prev is None or cur is None or n == 0:
            continue
        same = u["first_coach"] == pu["last_coach"]
        rows.append((s, prev - LM[stat][s - 1], cur - LM[stat][s], n, same))
    seasons = sorted({r[0] for r in rows})

    def fit(rs):
        sxx = sum(r[1] * r[1] * r[3] for r in rs)
        return sum(r[1] * r[2] * r[3] for r in rs) / sxx if sxx else 0.0

    err = {"league": [], "pooled": [], "regime": [], "regime_same0": []}
    paired = []
    for s in seasons:
        tr = [r for r in rows if r[0] != s]
        rp = fit(tr)
        rs_ = fit([r for r in tr if r[4]])
        rn = fit([r for r in tr if not r[4]])
        for r in rows:
            if r[0] != s:
                continue
            e0 = (r[2]) ** 2 * r[3]
            e1 = (r[2] - rp * r[1]) ** 2 * r[3]
            e2 = (r[2] - (rs_ if r[4] else rn) * r[1]) ** 2 * r[3]
            e3 = (r[2] - (rs_ * r[1] if r[4] else 0.0)) ** 2 * r[3]
            err["league"].append(e0)
            err["pooled"].append(e1)
            err["regime"].append(e2)
            err["regime_same0"].append(e3)
            paired.append(((e1 - e2) / r[3], r[4]))
    W = sum(r[3] for r in rows)
    out = {"pairs": len(rows), "new_coach_pairs": sum(1 for r in rows if not r[4]),
           "persist_pooled": round(fit(rows), 3), "persist_same": round(fit([r for r in rows if r[4]]), 3),
           "persist_new": round(fit([r for r in rows if not r[4]]), 3)}
    for k, v in err.items():
        out["mse_" + k] = sum(v) / W
    d = [x for x, _ in paired]
    dn = [x for x, s_ in paired if not s_]
    out["gain_regime_vs_pooled"] = {"mean": mean(d), "se": math.sqrt(sum((x - mean(d)) ** 2 for x in d)) / len(d)}
    out["gain_new_coach_only"] = {"mean": mean(dn), "se": math.sqrt(sum((x - mean(dn)) ** 2 for x in dn)) / len(dn),
                                  "n": len(dn)}
    return out


# ---- 3-5. who gets the ball ------------------------------------------------------------
# One definition of the situations and the roles: the engine's own (the audit
# measured them through these functions, and R2 deals through them).
BUCKETS, CARRY_ROLES, TGT_ROLES, bucket, grp = E2.BUCKETS, E2.CARRY_ROLES, E2.TGT_ROLES, E2.bucket, E2.grp


def pbucket(p):
    return bucket(p[R.Q_PER], p[R.Q_HSR], p[R.Q_SD], p[R.Q_DOWN], p[R.Q_DIST], p[R.Q_YL])


def usage_games(data, pos, min_prior=3):
    """Per team-game: the as-of (season-to-date, strictly earlier games) carries
    and targets of every player who touched the ball in it, their roles, and the
    game's carries and targets with their buckets."""
    out = []
    for s, d in data.items():
        ids = d["players"]
        bt = defaultdict(list)
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for side in (0, 1):
                bt[(team(g, side))].append((g["week"], g, side))
        for t, lst in bt.items():
            lst.sort(key=lambda x: x[0])
            car, tgt, drop = defaultdict(int), defaultdict(int), defaultdict(int)
            car_b = defaultdict(lambda: [0] * len(BUCKETS))
            ngame = 0
            for wk, g, side in lst:
                pl = side_plays(g, side)
                runs, tgts = [], []
                active = set()
                for p in pl:
                    k = p[R.Q_KIND]
                    if k in (R.K_RUN, R.K_SCR) and p[R.Q_RUSH] >= 0:
                        runs.append((p[R.Q_RUSH], pbucket(p), p[R.Q_TD], p[R.Q_YDS], k))
                        active.add(p[R.Q_RUSH])
                    elif k == R.K_PASS and p[R.Q_RECV] >= 0:
                        tgts.append((p[R.Q_RECV], pbucket(p), p[R.Q_TD], p[R.Q_YDS] if p[R.Q_CMP] else 0, k))
                        active.add(p[R.Q_RECV])
                    if k in (R.K_PASS, R.K_SACK, R.K_SCR, R.K_SPIKE) and p[R.Q_PASSER] >= 0:
                        active.add(p[R.Q_PASSER])
                if ngame >= min_prior:
                    out.append({"season": s, "week": wk, "team": t, "gid": g["id"], "coach": coach(g, side),
                                "car": {i: car[i] for i in active if car[i] > 0},
                                "car_b": {i: list(car_b[i]) for i in active if car[i] > 0},
                                "tgt": {i: tgt[i] for i in active if tgt[i] > 0},
                                "pos": {i: grp(pos.get(ids[i], "")) for i in active},
                                "qb1": max(drop, key=drop.get) if drop else None,
                                "ngame": ngame, "runs": runs, "tgts": tgts,
                                "ids": {i: ids[i] for i in active}})
                for p in pl:
                    k = p[R.Q_KIND]
                    if k in (R.K_RUN, R.K_SCR) and p[R.Q_RUSH] >= 0:
                        car[p[R.Q_RUSH]] += 1
                        car_b[p[R.Q_RUSH]][pbucket(p)] += 1
                    elif k == R.K_PASS and p[R.Q_RECV] >= 0:
                        tgt[p[R.Q_RECV]] += 1
                    if k in (R.K_PASS, R.K_SACK, R.K_SCR) and p[R.Q_PASSER] >= 0:
                        drop[p[R.Q_PASSER]] += 1
                ngame += 1
    return out


def roles(ug, kind):
    """Pre-game roles from as-of usage among the players active in the game."""
    return E2.roles(ug["car"], ug["tgt"], ug["pos"], kind)


def fit_mult(ugs, kind, iters=25, pooled=False):
    """Role x bucket multipliers on the as-of share (iterative scaling: the
    maximum-likelihood fit of a conditional logit with one effect per role and
    bucket). Returns {role: [m per bucket]}. `pooled`: one effect per role, the
    same in every bucket (the role-only baseline)."""
    RL = CARRY_ROLES if kind == "car" else TGT_ROLES
    M = {r: [1.0] * len(BUCKETS) for r in RL}
    plays = []
    for ug in ugs:
        base = ug[kind]
        if not base:
            continue
        rl = roles(ug, kind)
        cands = [(i, base[i], rl[i]) for i in base]
        for (i, b, *_rest) in (ug["runs"] if kind == "car" else ug["tgts"]):
            if i in base:
                plays.append((cands, rl[i], 0 if pooled else b))
    for _ in range(iters):
        obs = {r: [0.0] * len(BUCKETS) for r in RL}
        exp = {r: [0.0] * len(BUCKETS) for r in RL}
        for cands, ra, b in plays:
            ws = [(w * M[r][b], r) for _, w, r in cands]
            tot = sum(x for x, _ in ws)
            for x, r in ws:
                exp[r][b] += x / tot
            obs[ra][b] += 1
        # half a snap of pseudo-count in every cell: a role never seen in a bucket
        # (a wide receiver's carry inside the 2) keeps a small multiplier instead
        # of 0 -- the first fit drove those cells to 0 and a 2026 snap scored log(0)
        for r in RL:
            for b in range(len(BUCKETS)):
                if exp[r][b] > 0:
                    M[r][b] *= (obs[r][b] + 0.5) / (exp[r][b] + 0.5)
        # identify: RB1 (carries) / WR1 (targets) fixed at 1 in each bucket
        ref = "RB1" if kind == "car" else "WR1"
        for b in range(len(BUCKETS)):
            z = M[ref][b]
            for r in RL:
                M[r][b] /= z
    if pooled:
        M = {r: [v[0]] * len(BUCKETS) for r, v in M.items()}
    return M, len(plays)


def score_usage(ugs, kind, M=None, K=None, M0=None):
    """Log loss of who got the ball, summed per team-game, for the static as-of
    share and (M) the role x bucket multipliers, (K) plus each player's own
    shrunk bucket deviation (carries only: his as-of carries in the bucket
    against what the role model expected of his as-of share)."""
    per_game = []
    for ug in ugs:
        base = ug[kind]
        if not base:
            continue
        rl = roles(ug, kind)
        tot0 = sum(base.values())
        ll0 = ll1 = 0.0
        n = 0
        for (i, b, *_r) in (ug["runs"] if kind == "car" else ug["tgts"]):
            if i not in base:
                continue
            if M0 is None:
                ll0 -= math.log(base[i] / tot0)
            else:
                w0 = {j: base[j] * M0[rl[j]][b] for j in base}
                ll0 -= math.log(w0[i] / sum(w0.values()))
            if M is not None:
                ws = {j: base[j] * M[rl[j]][b] * (_dev(ug, j, b, K, M, rl) if K is not None else 1.0)
                      for j in base}
                ll1 -= math.log(ws[i] / sum(ws.values()))
            n += 1
        if n:
            per_game.append((ll0, ll1, n, ug["season"], ug["week"]))
    return per_game


def _dev(ug, j, b, K, M, rl):
    """A player's own bucket multiplier, shrunk: (obs + K) / (exp + K), with exp
    his as-of carries x the role model's bucket share ratio. Only the scoring
    zones (inside 10) carry one; elsewhere 1."""
    if b not in (1, 2, 3):
        return 1.0
    cb = ug["car_b"].get(j)
    if cb is None:
        return 1.0
    zone = (1, 2) if b in (1, 2) else (3,)
    o = sum(cb[z] for z in zone)
    # expected: his share of the team's as-of carries in those zones under the role model
    team_zone = sum(sum(ug["car_b"][i][z] for z in zone) for i in ug["car_b"])
    if team_zone == 0:
        return 1.0
    ws = {i: ug["car"][i] * M[rl[i]][zone[0]] for i in ug["car"]}
    e = team_zone * ws[j] / sum(ws.values())
    return (o + K) / (e + K)


def zone_dfs(ugs, M0, M1):
    """What the bucket multipliers move for DFS: the lead back's, the second
    back's and the quarterback's expected carries inside the 5 and in the
    blowout buckets, dealt over the team's ACTUAL plays by the role-only and by
    the bucket model, against what he got (squared error a player-game), and
    the mean absolute difference between the two models in carries."""
    acc = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    for ug in ugs:
        base = ug["car"]
        if not base:
            continue
        rl = roles(ug, "car")
        for role in ("RB1", "RB2", "QB"):
            who = [i for i in base if rl[i] == role]
            if not who:
                continue
            i = who[0]
            for zname, zs in (("inside5", (1, 2)), ("blowout", (5, 6))):
                e0 = e1 = obs = 0.0
                for (j, b, *_r) in ug["runs"]:
                    if b not in zs or j not in base:
                        continue
                    w0 = {k: base[k] * M0[rl[k]][b] for k in base}
                    w1 = {k: base[k] * M1[rl[k]][b] for k in base}
                    e0 += w0[i] / sum(w0.values())
                    e1 += w1[i] / sum(w1.values())
                    obs += j == i
                a = acc[(role, zname)]
                a[0] += (obs - e0) ** 2
                a[1] += (obs - e1) ** 2
                a[2] += abs(e1 - e0)
                a[3] += 1
    return {f"{r} {z}": {"player_games": v[3], "mse_role_only": round(v[0] / v[3], 4),
                         "mse_bucket": round(v[1] / v[3], 4), "mean_abs_shift_carries": round(v[2] / v[3], 4)}
            for (r, z), v in sorted(acc.items())}


def coach_pull(data, pos, U):
    """Does a head coach's habit of resting his lead back with a big lead carry
    from season to season? Per team-season: the season's lead back's share of
    the backs' carries leading by 14+ in the second half minus his share within
    8 points; year to year, same coach and new coach."""
    val = {}
    for s, d in data.items():
        ids = d["players"]
        bt = defaultdict(list)
        for g in d["games"]:
            if g["st"] == 2:
                for side in (0, 1):
                    bt[team(g, side)].append((g, side))
        for t, lst in bt.items():
            car = defaultdict(int)
            runs = []
            for g, side in lst:
                for p in side_plays(g, side):
                    if (p[R.Q_KIND] == R.K_RUN and p[R.Q_RUSH] >= 0
                            and grp(pos.get(ids[p[R.Q_RUSH]], "")) == "RB"):
                        car[p[R.Q_RUSH]] += 1
                        runs.append((p[R.Q_RUSH], p[R.Q_SD], p[R.Q_PER]))
            if not car:
                continue
            lead = max(car, key=car.get)
            big = [i == lead for i, sd, per in runs if sd >= 14 and per >= 3]
            neu = [i == lead for i, sd, per in runs if abs(sd) <= 8]
            if len(big) >= 15 and len(neu) >= 100:
                val[(s, t)] = (mean(big) - mean(neu), len(big))
    same, new = ([], []), ([], [])
    for (s, t), v in val.items():
        pv = val.get((s - 1, t))
        if pv is None or (s, t) not in U or (s - 1, t) not in U:
            continue
        c_same = U[(s, t)]["first_coach"] == U[(s - 1, t)]["last_coach"]
        (same if c_same else new)[0].append(pv[0])
        (same if c_same else new)[1].append(v[0])
    xs = [v[0] for v in val.values()]
    return {"team_seasons": len(val), "mean_drop": round(mean(xs), 4),
            "sd_obs": round(math.sqrt(sum((x - mean(xs)) ** 2 for x in xs) / (len(xs) - 1)), 4),
            "sampling_sd": round(math.sqrt(mean([0.25 / v[1] for v in val.values()])), 4),
            "yoy_same_coach": corr_rec(*same), "yoy_new_coach": corr_rec(*new)}


def in_game_adapt_plan(data, TS):
    """The in-game lean again, with the game's own plan held: each neutral snap
    after the offence's 10th, its pass-over-expected against the shrunk success
    gap so far AND the offence's own pass rate over expected so far this game
    (what the plan, the matchup and the opponent already put in the calls)."""
    X, y, cl = [], [], []
    for s, d in data.items():
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for side in (0, 1):
                ts = TS.get((s, team(g, side)))
                if ts is None:
                    continue
                rs, ps, pr = [0, 0], [0, 0], [0.0, 0]
                n = 0
                for p in side_plays(g, side):
                    if p[R.Q_KIND] not in R.SCRIMMAGE:
                        continue
                    if n >= 10 and neutral(p) and pr[1] >= 5:
                        sr = (rs[0] - 0.42 * rs[1]) / (rs[1] + 10.0)
                        sp = (ps[0] - 0.46 * ps[1]) / (ps[1] + 10.0)
                        X.append([1.0, sr - sp, ts["proe"], pr[0] / (pr[1] + 10.0)])
                        y.append(is_pass(p) - p[R.Q_XP] / 1000.0)
                        cl.append(g["id"])
                    n += 1
                    if neutral(p):
                        pr[0] += is_pass(p) - p[R.Q_XP] / 1000.0
                        pr[1] += 1
                    if p[R.Q_KIND] == R.K_RUN:
                        rs[0] += p[R.Q_SUCC]
                        rs[1] += 1
                    else:
                        ps[0] += p[R.Q_SUCC]
                        ps[1] += 1
    b, se = ols(X, y, cluster=cl)
    xs = [x[1] for x in X]
    sdx = math.sqrt(sum((v - mean(xs)) ** 2 for v in xs) / (len(xs) - 1))
    return {"plays": len(y), "coef_run_minus_pass_success": round(b[1], 4), "se": round(se[1], 4),
            "z": round(b[1] / se[1], 2), "proe_points_per_sd": round(100 * b[1] * sdx, 2),
            "coef_plan_so_far": round(b[3], 3), "se_plan": round(se[3], 3)}


def in_game_placebo(data, TS):
    """The placebo for the in-game lean: the same snaps, with the success gap
    over the REST of the game (plays after this one) beside the gap so far. A
    call cannot respond to the future, so a future coefficient as large as the
    past one says the lean is a game-level common factor (weather, the
    matchup's plan, the opponent's front), not adaptation."""
    X, y, cl = [], [], []
    for s, d in data.items():
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for side in (0, 1):
                ts = TS.get((s, team(g, side)))
                if ts is None:
                    continue
                pl = [p for p in side_plays(g, side) if p[R.Q_KIND] in R.SCRIMMAGE]
                tot_r = [sum(p[R.Q_SUCC] for p in pl if p[R.Q_KIND] == R.K_RUN),
                         sum(1 for p in pl if p[R.Q_KIND] == R.K_RUN)]
                tot_p = [sum(p[R.Q_SUCC] for p in pl if p[R.Q_KIND] != R.K_RUN),
                         sum(1 for p in pl if p[R.Q_KIND] != R.K_RUN)]
                rs, ps = [0, 0], [0, 0]
                for n, p in enumerate(pl):
                    fr = [tot_r[0] - rs[0] - (p[R.Q_SUCC] if p[R.Q_KIND] == R.K_RUN else 0),
                          tot_r[1] - rs[1] - (p[R.Q_KIND] == R.K_RUN)]
                    fp = [tot_p[0] - ps[0] - (p[R.Q_SUCC] if p[R.Q_KIND] != R.K_RUN else 0),
                          tot_p[1] - ps[1] - (p[R.Q_KIND] != R.K_RUN)]
                    if n >= 10 and neutral(p) and fr[1] + fp[1] >= 10:
                        past = ((rs[0] - 0.42 * rs[1]) / (rs[1] + 10.0)
                                - (ps[0] - 0.46 * ps[1]) / (ps[1] + 10.0))
                        fut = ((fr[0] - 0.42 * fr[1]) / (fr[1] + 10.0)
                               - (fp[0] - 0.46 * fp[1]) / (fp[1] + 10.0))
                        X.append([1.0, past, fut, ts["proe"]])
                        y.append(is_pass(p) - p[R.Q_XP] / 1000.0)
                        cl.append(g["id"])
                    if p[R.Q_KIND] == R.K_RUN:
                        rs[0] += p[R.Q_SUCC]
                        rs[1] += 1
                    else:
                        ps[0] += p[R.Q_SUCC]
                        ps[1] += 1
    b, se = ols(X, y, cluster=cl)
    return {"plays": len(y), "coef_past": round(b[1], 4), "se_past": round(se[1], 4),
            "coef_future": round(b[2], 4), "se_future": round(se[2], 4)}


def summarize_ll(pg, label):
    d = [(a - b) for a, b, n, *_ in pg]
    m = mean(d)
    se = math.sqrt(sum((x - m) ** 2 for x in d) / (len(d) - 1) / len(d))
    return {"label": label, "team_games": len(pg), "plays": sum(x[2] for x in pg),
            "ll_static_per_play": round(sum(x[0] for x in pg) / sum(x[2] for x in pg), 5),
            "ll_model_per_play": round(sum(x[1] for x in pg) / sum(x[2] for x in pg), 5),
            "gain_per_team_game": round(m, 4), "se": round(se, 4), "z": round(m / se, 2) if se else None}


def share_table(ugs, kind):
    """Realized share of the team's carries (targets) by role and bucket."""
    RL = CARRY_ROLES if kind == "car" else TGT_ROLES
    c = {r: [0] * len(BUCKETS) for r in RL}
    tot = [0] * len(BUCKETS)
    for ug in ugs:
        rl = roles(ug, kind)
        for (i, b, *_r) in (ug["runs"] if kind == "car" else ug["tgts"]):
            tot[b] += 1
            c[rl[i] if i in rl else "OT"][b] += 1
    return {"plays": tot, "share": {r: [round(c[r][b] / tot[b], 4) if tot[b] else None
                                        for b in range(len(BUCKETS))] for r in RL}}


def gl_reliability(data, pos):
    """The goal-line specialist: a back's share of his team's carries inside the
    5 minus his share of all its carries, odd games against even games, by
    team-season (backs with 40+ carries)."""
    xs, ys, ks = [], [], []
    for s, d in data.items():
        ids = d["players"]
        bt = defaultdict(list)
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for side in (0, 1):
                bt[team(g, side)].append((g["week"], g, side))
        for t, lst in bt.items():
            lst.sort(key=lambda x: x[0])
            halves = [[defaultdict(int), defaultdict(int), [0], [0]] for _ in range(2)]
            for j, (wk, g, side) in enumerate(lst):
                h = halves[j % 2]
                for p in side_plays(g, side):
                    if p[R.Q_KIND] in (R.K_RUN, R.K_SCR) and p[R.Q_RUSH] >= 0:
                        i = p[R.Q_RUSH]
                        h[0][i] += 1
                        h[2][0] += 1
                        if 1 <= p[R.Q_YL] <= 5:
                            h[1][i] += 1
                            h[3][0] += 1
            for i in set(halves[0][0]) | set(halves[1][0]):
                if grp(pos.get(ids[i], "")) != "RB":
                    continue
                if halves[0][0][i] + halves[1][0][i] < 40:
                    continue
                if min(halves[0][3][0], halves[1][3][0]) < 4:
                    continue
                v = []
                for h in halves:
                    v.append(h[1][i] / h[3][0] - h[0][i] / h[2][0])
                xs.append(v[0])
                ys.append(v[1])
                ks.append(halves[0][3][0] + halves[1][3][0])
    r = pearson(xs, ys)
    var_obs = sum((x - mean(xs + ys)) ** 2 for x in xs + ys) / (len(xs + ys) - 1)
    cov = sum((a - mean(xs)) * (b - mean(ys)) for a, b in zip(xs, ys)) / (len(xs) - 1)
    return {"backs": len(xs), "split_half": corr_rec(xs, ys), "reliability_full": round(sb(r), 3),
            "sd_obs_half": round(math.sqrt(var_obs), 4), "sd_true": round(math.sqrt(max(cov, 0)), 4),
            "inside5_carries_per_team_season": round(mean(ks), 1)}


def qb_share(data):
    """The team's as-of starting quarterback (most dropbacks so far this season)
    and the share of the team's dropbacks he took, by score state in the second
    half (games with 3+ earlier games)."""
    st = defaultdict(lambda: [0, 0])
    for s, d in data.items():
        bt = defaultdict(list)
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for side in (0, 1):
                bt[team(g, side)].append((g["week"], g, side))
        for t, lst in bt.items():
            lst.sort(key=lambda x: x[0])
            drop = defaultdict(int)
            for j, (wk, g, side) in enumerate(lst):
                pl = side_plays(g, side)
                if j >= 3 and drop:
                    q1 = max(drop, key=drop.get)
                    # only games the starter started (he threw one of the first ten dropbacks)
                    first = [p[R.Q_PASSER] for p in pl if p[R.Q_KIND] in PASSK and p[R.Q_PASSER] >= 0][:10]
                    if q1 in first:
                        for p in pl:
                            if p[R.Q_KIND] in PASSK and p[R.Q_PASSER] >= 0:
                                sd, per = p[R.Q_SD], p[R.Q_PER]
                                key = ("Q1-Q3" if per <= 3 else "Q4") + (
                                    " lead 17+" if sd >= 17 else " lead 9-16" if sd >= 9 else
                                    " trail 17+" if sd <= -17 else " trail 9-16" if sd <= -9 else " within 8")
                                st[key][0] += p[R.Q_PASSER] == q1
                                st[key][1] += 1
                for p in pl:
                    if p[R.Q_KIND] in PASSK and p[R.Q_PASSER] >= 0:
                        drop[p[R.Q_PASSER]] += 1
    return {k: {"share": round(v[0] / v[1], 4), "dropbacks": v[1]} for k, v in sorted(st.items())}


# ---- 6. fatigue and workload -----------------------------------------------------------
def fatigue(data, pos):
    """A back's success and yards on his k-th carry of the game against the
    league's rate for the carry's down, distance and field, among player-games
    of 18+ carries (so every bin reads the same games), Q1-Q3, score within 8."""
    cell = defaultdict(lambda: [0, 0, 0.0])
    rows = []
    for s, d in data.items():
        ids = d["players"]
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for side in (0, 1):
                cnt = defaultdict(int)
                tot = defaultdict(int)
                pl = side_plays(g, side)
                for p in pl:
                    if p[R.Q_KIND] == R.K_RUN and p[R.Q_RUSH] >= 0:
                        tot[p[R.Q_RUSH]] += 1
                for p in pl:
                    if p[R.Q_KIND] != R.K_RUN or p[R.Q_RUSH] < 0:
                        continue
                    i = p[R.Q_RUSH]
                    cnt[i] += 1
                    key = (p[R.Q_DOWN], min(p[R.Q_DIST], 11), min(9, max(0, (p[R.Q_YL] - 1) // 10)))
                    c = cell[key]
                    c[0] += 1
                    c[1] += p[R.Q_SUCC]
                    c[2] += p[R.Q_YDS]
                    if (tot[i] >= 18 and p[R.Q_PER] <= 3 and abs(p[R.Q_SD]) <= 8
                            and grp(pos.get(ids[i], "")) == "RB"):
                        rows.append((cnt[i], p[R.Q_SUCC], p[R.Q_YDS], key, (g["id"], i)))
    bins = ((1, 6), (7, 12), (13, 18), (19, 99))
    out = {}
    for lo, hi in bins:
        rs = [r for r in rows if lo <= r[0] <= hi]
        res = defaultdict(lambda: [0.0, 0.0, 0])
        for k, sc, yd, key, pg in rs:
            c = cell[key]
            res[pg][0] += sc - c[1] / c[0]
            res[pg][1] += yd - c[2] / c[0]
            res[pg][2] += 1
        su = [v[0] / v[2] for v in res.values()]
        ys = [v[1] / v[2] for v in res.values()]
        n = len(rs)
        out[f"{lo}-{hi}"] = {"carries": n, "player_games": len(res),
                             "succ_resid": round(sum(v[0] for v in res.values()) / max(n, 1), 4),
                             "succ_se": round(math.sqrt(sum((x - mean(su)) ** 2 for x in su) / max(len(su) - 1, 1)
                                                        / max(len(su), 1)), 4) if su else None,
                             "ypc_resid": round(sum(v[1] for v in res.values()) / max(n, 1), 3),
                             "ypc_se": round(math.sqrt(sum((x - mean(ys)) ** 2 for x in ys) / max(len(ys) - 1, 1)
                                                       / max(len(ys), 1)), 3) if ys else None}
    return out


def rotation(data, pos):
    """Within a game: after the lead back (of that game) has carried k times in a
    row, is the next team carry his at his game rate? Neutral states."""
    st = defaultdict(lambda: [0, 0])
    for s, d in data.items():
        ids = d["players"]
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for side in (0, 1):
                runs = [p for p in side_plays(g, side) if p[R.Q_KIND] == R.K_RUN and p[R.Q_RUSH] >= 0
                        and grp(pos.get(ids[p[R.Q_RUSH]], "")) == "RB"]
                if len(runs) < 15:
                    continue
                cnt = defaultdict(int)
                for p in runs:
                    cnt[p[R.Q_RUSH]] += 1
                lead = max(cnt, key=cnt.get)
                base = cnt[lead] / len(runs)
                streak = 0
                for p in runs:
                    if abs(p[R.Q_SD]) <= 8 and p[R.Q_PER] <= 3:
                        k = min(streak, 4)
                        st[k][0] += (p[R.Q_RUSH] == lead) - base
                        st[k][1] += 1
                    streak = streak + 1 if p[R.Q_RUSH] == lead else 0
    return {f"after {k}{'+' if k == 4 else ''} in a row": {"excess_share": round(v[0] / v[1], 4), "carries": v[1]}
            for k, v in sorted(st.items())}


# ---- 7. halftime, 8. adaptation --------------------------------------------------------
def quarter_corr(data, TS):
    """Offensive EPA a play (and pass rate over expected) by quarter, each
    team-game minus its team-season mean; the correlation of adjacent quarters.
    Halftime sits between Q2 and Q3 only."""
    rec = defaultdict(lambda: {q: [0.0, 0, 0.0] for q in (1, 2, 3, 4)})
    for s, d in data.items():
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for side in (0, 1):
                t = team(g, side)
                rr = rec[(s, g["id"], t)]
                for p in side_plays(g, side):
                    if p[R.Q_KIND] in R.SCRIMMAGE and 1 <= p[R.Q_PER] <= 4 and abs(p[R.Q_SD]) <= 14 \
                            and p[R.Q_HSR] > 120:
                        q = rr[p[R.Q_PER]]
                        q[0] += p[R.Q_EPA] / 1000.0
                        q[1] += 1
                        q[2] += (is_pass(p) - p[R.Q_XP] / 1000.0) if p[R.Q_XP] >= 0 else 0.0
    out = {}
    rng = random.Random(7)
    for metric, j in (("epa", 0), ("proe", 2)):
        pairs = {(1, 2): ([], []), (2, 3): ([], []), (3, 4): ([], []), (1, 3): ([], [])}
        keys = []
        for (s, gid, t), qq in rec.items():
            ts = TS.get((s, t))
            if ts is None:
                continue
            m = ts[metric]
            keys.append((s, gid, t))
            for a, b in pairs:
                if qq[a][1] >= 6 and qq[b][1] >= 6:
                    pairs[(a, b)][0].append(qq[a][j] / qq[a][1] - m)
                    pairs[(a, b)][1].append(qq[b][j] / qq[b][1] - m)
        res = {}
        for ab, (xs, ys) in pairs.items():
            r = pearson(xs, ys)
            # bootstrap SE over team-games
            bs = []
            idx = list(range(len(xs)))
            for _ in range(200):
                smp = [rng.choice(idx) for _ in idx]
                bs.append(pearson([xs[k] for k in smp], [ys[k] for k in smp]))
            res[f"Q{ab[0]}-Q{ab[1]}"] = {"r": round(r, 4), "se": round(math.sqrt(sum((x - mean(bs)) ** 2
                                                                              for x in bs) / 199), 4),
                                         "n": len(xs)}
        out[metric] = res
    return out


def team_season_means(tg):
    TS = {}
    by = defaultdict(list)
    for r in tg:
        by[(r["season"], r["team"])].append(r)
    for k, recs in by.items():
        e, n = _rate(recs, "epa")
        p, _ = _rate(recs, "proe")
        TS[k] = {"epa": e, "proe": p}
    return TS


def in_game_adapt(data, TS):
    """Does the next call lean toward what has worked THIS game, beyond score,
    clock, down and distance (nflverse's xpass knows those) and the team's own
    season lean? One row a neutral snap after the offence's 10th; x is the
    shrunk in-game success of its runs minus its passes so far."""
    X, y, cl = [], [], []
    for s, d in data.items():
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for side in (0, 1):
                ts = TS.get((s, team(g, side)))
                if ts is None:
                    continue
                rs = [0, 0]
                ps = [0, 0]
                n = 0
                for p in side_plays(g, side):
                    if p[R.Q_KIND] not in R.SCRIMMAGE:
                        continue
                    if n >= 10 and neutral(p):
                        sr = (rs[0] - 0.42 * rs[1]) / (rs[1] + 10.0)
                        sp = (ps[0] - 0.46 * ps[1]) / (ps[1] + 10.0)
                        X.append([1.0, sr - sp, ts["proe"]])
                        y.append(is_pass(p) - p[R.Q_XP] / 1000.0)
                        cl.append(g["id"])
                    n += 1
                    if p[R.Q_KIND] == R.K_RUN:
                        rs[0] += p[R.Q_SUCC]
                        rs[1] += 1
                    else:
                        ps[0] += p[R.Q_SUCC]
                        ps[1] += 1
    b, se = ols(X, y, cluster=cl)
    xs = [x[1] for x in X]
    sdx = math.sqrt(sum((v - mean(xs)) ** 2 for v in xs) / (len(xs) - 1))
    return {"plays": len(y), "coef_run_minus_pass_success": round(b[1], 4), "se": round(se[1], 4),
            "z": round(b[1] / se[1], 2), "sd_x": round(sdx, 4),
            "proe_points_per_sd": round(100 * b[1] * sdx, 2), "coef_season_proe": round(b[2], 3)}


def opponent_adapt(tg):
    """A team-game's neutral pass rate over expected against its season lean
    (this game left out) and the opponent defence's season figures (this game
    left out): the pass rate it allows over expected, and its rush minus pass
    EPA allowed (positive: runs work better against it)."""
    by_off = defaultdict(list)
    by_def = defaultdict(list)
    for r in tg:
        by_off[(r["season"], r["team"])].append(r)
        by_def[(r["season"], r["opp"])].append(r)

    def loo(lst, r, stat, sub=None):
        a = sum(x[stat][0] for x in lst if x is not r)
        n = sum(x[stat][1] for x in lst if x is not r)
        return a / n if n else None

    X, y, w = [], [], []
    for r in tg:
        if r["proe"][1] < 15:
            continue
        o = loo(by_off[(r["season"], r["team"])], r, "proe")
        dl = by_def[(r["season"], r["opp"])]
        dp = loo(dl, r, "proe")
        dr = loo(dl, r, "rush_epa")
        dpa = loo(dl, r, "pass_epa")
        if None in (o, dp, dr, dpa):
            continue
        X.append([1.0, o, dp, dr - dpa])
        y.append(r["proe"][0] / r["proe"][1])
        w.append(r["proe"][1])
    b, se = ols(X, y, w=w)
    xs = [x[3] for x in X]
    sdx = math.sqrt(sum((v - mean(xs)) ** 2 for v in xs) / (len(xs) - 1))
    return {"team_games": len(y), "coef_own_lean": round(b[1], 3), "se_own": round(se[1], 3),
            "coef_def_allowed_lean": round(b[2], 3), "se_def": round(se[2], 3),
            "coef_def_rush_minus_pass_epa": round(b[3], 4), "se_weak": round(se[3], 4),
            "z_weak": round(b[3] / se[3], 2), "proe_points_per_sd_weak": round(100 * b[3] * sdx, 2)}


def halftime_adapt(data, TS):
    """Team-game: second-half neutral pass rate over expected minus the first
    half's, against the first half's rush-minus-pass EPA a play (shrunk)."""
    X, y, w = [], [], []
    for s, d in data.items():
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for side in (0, 1):
                h = {1: [0.0, 0], 2: [0.0, 0]}
                re_, pe = [0.0, 0], [0.0, 0]
                for p in side_plays(g, side):
                    if not neutral(p):
                        continue
                    half = 1 if p[R.Q_PER] <= 2 else 2
                    h[half][0] += is_pass(p) - p[R.Q_XP] / 1000.0
                    h[half][1] += 1
                    if half == 1:
                        tgt = re_ if p[R.Q_KIND] == R.K_RUN else pe
                        tgt[0] += p[R.Q_EPA] / 1000.0
                        tgt[1] += 1
                if h[1][1] < 12 or h[2][1] < 8 or re_[1] < 4 or pe[1] < 4:
                    continue
                x = re_[0] / (re_[1] + 8) - pe[0] / (pe[1] + 8)
                X.append([1.0, x])
                y.append(h[2][0] / h[2][1] - h[1][0] / h[1][1])
                w.append(1.0)
    b, se = ols(X, y, w=w)
    xs = [x[1] for x in X]
    sdx = math.sqrt(sum((v - mean(xs)) ** 2 for v in xs) / (len(xs) - 1))
    return {"team_games": len(y), "coef": round(b[1], 4), "se": round(se[1], 4), "z": round(b[1] / se[1], 2),
            "proe_points_per_sd": round(100 * b[1] * sdx, 2)}


# ---- 10. strategic interaction ---------------------------------------------------------
def interaction(tg, stat="proe", iters=30):
    """A season's team-game rates fitted as offence + defence (alternating
    weighted means); does a pairing's residual repeat when the same two teams
    meet again that season (division rematches)? Against the same offence's
    residual correlation with OTHER opponents at a like distance in weeks."""
    out_same, out_other = ([], []), ([], [])
    by_s = defaultdict(list)
    for r in tg:
        if r[stat][1] >= 15:
            by_s[r["season"]].append(r)
    for s, recs in by_s.items():
        off = defaultdict(float)
        de = defaultdict(float)
        mu = sum(r[stat][0] for r in recs) / sum(r[stat][1] for r in recs)
        for _ in range(iters):
            acc = defaultdict(lambda: [0.0, 0])
            for r in recs:
                v = r[stat][0] / r[stat][1] - mu - de[r["opp"]]
                acc[r["team"]][0] += v * r[stat][1]
                acc[r["team"]][1] += r[stat][1]
            off = {k: a / n for k, (a, n) in acc.items()}
            acc = defaultdict(lambda: [0.0, 0])
            for r in recs:
                v = r[stat][0] / r[stat][1] - mu - off[r["team"]]
                acc[r["opp"]][0] += v * r[stat][1]
                acc[r["opp"]][1] += r[stat][1]
            de = defaultdict(float, {k: a / n for k, (a, n) in acc.items()})
        res = {}
        for r in recs:
            res[(r["team"], r["opp"], r["week"])] = r[stat][0] / r[stat][1] - mu - off[r["team"]] - de[r["opp"]]
        byteam = defaultdict(list)
        for (t, o, wk), v in res.items():
            byteam[t].append((wk, o, v))
        for t, lst in byteam.items():
            lst.sort()
            for j, (wk, o, v) in enumerate(lst):
                for wk2, o2, v2 in lst[j + 1:]:
                    if o2 == o:
                        out_same[0].append(v)
                        out_same[1].append(v2)
                        # a control: the next game against someone else at a like gap
                        ctrl = [x for x in lst if x[1] != o and x[0] > wk and abs((x[0] - wk) - (wk2 - wk)) <= 2]
                        if ctrl:
                            out_other[0].append(v)
                            out_other[1].append(ctrl[0][2])
    return {"rematch": corr_rec(*out_same), "control_other_opponent": corr_rec(*out_other)}


# ---- FTN: the defence's blitz rate (2022-2025) -----------------------------------------
def ftn_blitz(ftn_dir, data):
    """Defence blitz rate on dropbacks (FTN n_blitzers > 0), by team-season:
    split-half and year to year with the same head coach / a new one."""
    if not ftn_dir or not os.path.isdir(ftn_dir):
        return None
    out = {}
    unit = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    coaches = {}
    for s in (2022, 2023, 2024, 2025):
        fp = os.path.join(ftn_dir, f"ftn_charting_{s}.csv")
        if not os.path.exists(fp) or s not in data:
            continue
        drop_def = {}
        for g in data[s]["games"]:
            if g["st"] != 2:
                continue
            for p in g["plays"]:
                if p[R.Q_KIND] in (R.K_PASS, R.K_SACK, R.K_SCR):
                    dteam = g["away"] if p[R.Q_OFFH] else g["home"]
                    dcoach = g["away_coach"] if p[R.Q_OFFH] else g["home_coach"]
                    drop_def[(g["id"], p[R.Q_PLAYID])] = (dteam, dcoach, g["week"])
        with open(fp, newline="") as fh:
            for r in csv.DictReader(fh):
                k = (r["nflverse_game_id"], R._i(r["nflverse_play_id"]))
                v = drop_def.get(k)
                if v is None or r["n_blitzers"] in ("", "NA"):
                    continue
                c = unit[(s, v[0])][v[2]]
                c[0] += R._i(r["n_blitzers"]) > 0
                c[1] += 1
                coaches.setdefault((s, v[0]), defaultdict(int))[v[1]] += 1
    lm = defaultdict(lambda: [0, 0])
    for (s, t), wk in unit.items():
        for a, n in wk.values():
            lm[s][0] += a
            lm[s][1] += n
    halves, dev = ([], []), {}
    for (s, t), wk in unit.items():
        weeks = sorted(wk)
        a = [wk[w] for w in weeks[0::2]]
        b = [wk[w] for w in weeks[1::2]]
        L = lm[s][0] / lm[s][1]
        ra = sum(x[0] for x in a) / sum(x[1] for x in a) - L
        rb = sum(x[0] for x in b) / sum(x[1] for x in b) - L
        halves[0].append(ra)
        halves[1].append(rb)
        dev[(s, t)] = sum(x[0] for x in wk.values()) / sum(x[1] for x in wk.values()) - L
    same, new = ([], []), ([], [])
    for (s, t), v in dev.items():
        if (s - 1, t) in dev:
            c0 = max(coaches[(s - 1, t)].items(), key=lambda kv: kv[1])[0]
            c1 = max(coaches[(s, t)].items(), key=lambda kv: kv[1])[0]
            (same if c0 == c1 else new)[0].append(dev[(s - 1, t)])
            (same if c0 == c1 else new)[1].append(v)
    r = pearson(*halves)
    out = {"team_seasons": len(dev), "league_rate": {s: round(v[0] / v[1], 4) for s, v in lm.items()},
           "split_half": corr_rec(*halves), "reliability_full": round(sb(r), 3),
           "sd_obs": round(math.sqrt(sum(x * x for x in dev.values()) / len(dev)), 4),
           "yoy_same_coach": corr_rec(*same), "yoy_new_coach": corr_rec(*new)}
    return out


# ---- the run ---------------------------------------------------------------------------
def main(pbp_dir, players_csv, ftn_dir=None):
    data = {s: R.load_season(pbp_dir, s) for s in DEV_SEASONS}
    dev26 = R.load_season(pbp_dir, 2026)               # weeks 1-12 only: the loader drops the rest
    assert max(g["week"] for g in dev26["games"]) <= 12
    pos = R.positions(players_csv)
    out = {"seasons": list(DEV_SEASONS), "dev_2026_weeks": [1, 12],
           "credit": "nflverse-data play-by-play and players (CC BY 4.0); FTN Data via nflverse (CC BY-SA 4.0)"}
    cells = _fourth_cells(data)
    tg = team_games(data, cells)
    U = units(tg)
    LM = league_means(tg)
    out["league_means"] = {k: {str(s): round(v, 4) for s, v in d.items()} for k, d in LM.items()}
    out["tendencies"] = tendencies(tg, U, LM)
    out["regime_prior"] = {st: regime_prior(U, LM, st) for st in ("proe", "pace", "nh", "go")}
    TS = team_season_means(tg)
    out["halftime_quarters"] = quarter_corr(data, TS)
    out["halftime_adapt"] = halftime_adapt(data, TS)
    out["in_game_adapt"] = in_game_adapt(data, TS)
    out["opponent_adapt"] = opponent_adapt(tg)
    out["interaction"] = {st: interaction(tg, st) for st in ("proe", "epa")}
    out["ftn_blitz"] = ftn_blitz(ftn_dir, data)
    # who gets the ball
    fit = {s: data[s] for s in range(2016, 2024)}
    ug_fit = usage_games(fit, pos)
    ug = {s: usage_games({s: data[s]}, pos) for s in (2024, 2025)}
    ug[2026] = usage_games({2026: dev26}, pos)
    use = {}
    for kind in ("car", "tgt"):
        M, n = fit_mult(ug_fit, kind)
        M0, _ = fit_mult(ug_fit, kind, pooled=True)
        use[kind] = {"fit_plays": n, "mult": {r: [round(x, 3) for x in v] for r, v in M.items()},
                     "mult_role_only": {r: round(v[0], 3) for r, v in M0.items()},
                     "share_fit": share_table(ug_fit, kind)}
        for s in (2024, 2025, 2026):
            use[kind][str(s)] = summarize_ll(score_usage(ug[s], kind, M), f"{kind} role x bucket vs static")
            use[kind][f"role_only_{s}"] = summarize_ll(score_usage(ug[s], kind, M0),
                                                       f"{kind} role only vs static")
            use[kind][f"situational_{s}"] = summarize_ll(score_usage(ug[s], kind, M, M0=M0),
                                                         f"{kind} role x bucket vs role only")
        if kind == "car":
            for s in (2024, 2025, 2026):
                use[kind][f"dfs_{s}"] = zone_dfs(ug[s], M0, M)
            Ks = {}
            for K in (2.0, 5.0, 10.0, 20.0, 40.0):
                Ks[str(K)] = summarize_ll(score_usage(ug[2024], kind, M, K=K, M0=M), f"car +dev K={K} vs bucket")
            use[kind]["dev_select_2024"] = Ks
            kbest = min(Ks, key=lambda k: Ks[k]["ll_model_per_play"])
            use[kind]["dev_K"] = float(kbest)
            for s in (2025, 2026):
                use[kind][f"dev_{s}"] = summarize_ll(score_usage(ug[s], kind, M, K=float(kbest), M0=M),
                                                     f"car +dev K={kbest} vs bucket")
    out["usage"] = use
    out["buckets"] = list(BUCKETS)
    out["gl_specialist"] = gl_reliability(data, pos)
    out["qb_share"] = qb_share(data)
    out["fatigue"] = fatigue(data, pos)
    out["rotation"] = rotation(data, pos)
    out["coach_pull"] = coach_pull(data, pos, U)
    out["in_game_adapt_plan"] = in_game_adapt_plan(data, TS)
    out["in_game_placebo"] = in_game_placebo(data, TS)
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
