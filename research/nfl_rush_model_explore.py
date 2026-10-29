"""Design measurements for the RB / QB rushing model, on the FITTING seasons
only (2023-2024; no 2025 or 2026 row is read). RESEARCH (numpy). Its numbers
are quoted in the draft rule's "what was seen before writing it", so they
are reproducible:

    export DEEP_CACHE_DIR=<dir with nfl_matchup_2023/2024.pkl + nfl_matchup_ids.pkl> VIGIL_NO_BG=1
    python3 -m research.nfl_rush_model_explore > explore.json

A. per-attempt yards by role (RB run, QB designed run, QB sneak, QB scramble,
   kneel, WR run): moments, the mass at or below 0, the tail;
B. a QB's game in components: designed runs, scrambles, kneels, dropbacks;
C. kneels per team-game by the pre-game spread (the game-script term);
D. whether Sleeper's box counts kneels as rushing attempts (official
   scoring does);
E. the shape question on RBs, fit 2023 -> checked on 2024 (inside the
   fitting seasons, like the first model's traincheck): the shipped
   point-mass + single-CV gamma against a compound (carries NegBin x a
   per-carry kernel), and the carries' over-dispersion by line decile.
"""
import collections
import csv
import gzip
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
os.environ.setdefault("VIGIL_NO_BG", "1")

import nfl_matchup_data as D            # noqa: E402

SEASONS = (2023, 2024)
PLAYER_WEEKS = os.path.join(HERE, "data", "player_weeks_2022_2025.csv.gz")
KIND = {k: i for i, k in enumerate(D.KIND)}


def positions():
    ids = D.load_ids() or {}
    return {g: (r.get("pos") or "") for g, r in (ids.get("ids") or {}).items()}


def moments(y):
    y = np.asarray(y, float)
    if len(y) < 3:
        return {"n": int(len(y))}
    m, s = float(np.mean(y)), float(np.std(y))
    sk = float(np.mean(((y - m) / s) ** 3)) if s > 0 else 0.0
    q = np.quantile(y, [0.1, 0.25, 0.5, 0.75, 0.9, 0.99])
    return {"n": int(len(y)), "mean": round(m, 3), "sd": round(s, 3), "skew": round(sk, 3),
            "p_le0": round(float(np.mean(y <= 0)), 4), "p_ge10": round(float(np.mean(y >= 10)), 4),
            "p_ge20": round(float(np.mean(y >= 20)), 4), "q": [round(float(x), 1) for x in q]}


def plays(season, pos):
    st = D.load_season(season)
    c = st["cols"]
    n = st["n"]
    kind = np.asarray(c["kind"])
    rusher = np.asarray(c["rusher"])
    yds = np.asarray(c["yds"], float)
    sneak = np.asarray(c["sneak"])
    rpo = np.asarray(c["rpo"])
    gi = np.asarray(c["gi"])
    off = np.asarray(c["off"])
    passer = np.asarray(c["passer"])
    db = np.asarray(c["db"])
    rpos = np.array([pos.get(int(r), "") if r > 0 else "" for r in rusher], dtype=object)
    return st, {"n": n, "kind": kind, "rusher": rusher, "yds": yds, "sneak": sneak, "rpo": rpo, "gi": gi, "off": off,
                "passer": passer, "db": db, "rpos": rpos}


def section_a(P):
    out = collections.defaultdict(list)
    for p in P:
        k, rp = p["kind"], p["rpos"]
        out["RB run"].append(p["yds"][(k == KIND["run"]) & (rp == "RB")])
        out["WR run"].append(p["yds"][(k == KIND["run"]) & (rp == "WR")])
        qbrun = (k == KIND["run"]) & (rp == "QB")
        out["QB designed run (not a sneak)"].append(p["yds"][qbrun & (p["sneak"] != 1)])
        out["QB sneak (FTN)"].append(p["yds"][qbrun & (p["sneak"] == 1)])
        out["QB RPO keep (FTN)"].append(p["yds"][qbrun & (p["rpo"] == 1)])
        out["QB scramble"].append(p["yds"][k == KIND["scramble"]])
        out["kneel"].append(p["yds"][k == KIND["kneel"]])
    return {lab: moments(np.concatenate(v)) for lab, v in out.items()}


def section_b(P, pos):
    """Per (game, QB): designed runs, sneaks, scrambles, kneels, dropbacks
    (pass attempts + sacks + scrambles), for QBs with 10+ dropbacks."""
    rows = []
    for p in P:
        k = p["kind"]
        per = collections.defaultdict(lambda: [0, 0, 0, 0, 0, 0.0, 0.0])
        for i in np.where((k == KIND["run"]) | (k == KIND["scramble"]) | (k == KIND["kneel"]))[0]:
            r = int(p["rusher"][i])
            if pos.get(r) != "QB":
                continue
            key = (int(p["gi"][i]), r)
            if k[i] == KIND["scramble"]:
                per[key][2] += 1
                per[key][6] += p["yds"][i]
            elif k[i] == KIND["kneel"]:
                per[key][3] += 1
            elif p["sneak"][i] == 1:
                per[key][1] += 1
            else:
                per[key][0] += 1
                per[key][5] += p["yds"][i]
        for i in np.where((k == KIND["attempt"]) | (k == KIND["sack"]) | (k == KIND["scramble"]))[0]:
            q = int(p["passer"][i]) if k[i] != KIND["scramble"] else int(p["rusher"][i])
            if pos.get(q) == "QB" and q > 0:
                per[(int(p["gi"][i]), q)][4] += 1
        rows += [v for v in per.values() if v[4] >= 10]
    a = np.array(rows, float)
    if not len(a):
        return {}
    des, snk, scr, kn, dbk = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
    def cnt(x):
        m, v = float(np.mean(x)), float(np.var(x))
        return {"mean": round(m, 3), "var": round(v, 3), "p0": round(float(np.mean(x == 0)), 3)}
    return {"qb_games": int(len(a)), "designed": cnt(des), "sneaks": cnt(snk), "scrambles": cnt(scr), "kneels": cnt(kn),
            "dropbacks": cnt(dbk), "scramble_rate": round(float(scr.sum() / dbk.sum()), 4),
            "yds_per_designed": round(float(a[:, 5].sum() / max(1, des.sum())), 3),
            "yds_per_scramble": round(float(a[:, 6].sum() / max(1, scr.sum())), 3),
            "share_of_qb_rush_att": {"designed": round(float(des.sum() / (des + snk + scr + kn).sum()), 3),
                                     "sneak": round(float(snk.sum() / (des + snk + scr + kn).sum()), 3),
                                     "scramble": round(float(scr.sum() / (des + snk + scr + kn).sum()), 3),
                                     "kneel": round(float(kn.sum() / (des + snk + scr + kn).sum()), 3)}}


def section_c(stores, P):
    """Kneels per team-game by the team-side pre-game spread (nflverse
    spread_line, home-positive) and by the result."""
    by_spread, by_result = collections.defaultdict(list), collections.defaultdict(list)
    for st, p in zip(stores, P):
        kn = collections.Counter()
        for i in np.where(p["kind"] == KIND["kneel"])[0]:
            kn[(int(p["gi"][i]), int(p["off"][i]))] += 1
        for gi, g in enumerate(st["games"]):
            for side in ("home", "away"):
                t = st["teams"].index(g[side])
                sp = g.get("spread")
                if sp is None:
                    continue
                fav = sp if side == "home" else -sp       # team-side expected margin
                b = "fav 7+" if fav >= 7 else "fav 3-6.5" if fav >= 3 else "pick 0-2.5" if fav >= 0 else \
                    "dog 0.5-2.5" if fav > -3 else "dog 3-6.5" if fav > -7 else "dog 7+"
                won = (g["hs"] > g["as"]) == (side == "home") and g["hs"] != g["as"]
                by_spread[b].append(kn[(gi, t)])
                by_result["won" if won else "lost or tied"].append(kn[(gi, t)])
    f = lambda v: {"n": len(v), "mean": round(float(np.mean(v)), 3), "p0": round(float(np.mean(np.array(v) == 0)), 3)}
    return {"by_spread": {k: f(v) for k, v in sorted(by_spread.items())}, "by_result": {k: f(v) for k, v in by_result.items()}}


def section_d(P, pos, ids_sleeper):
    """Sleeper's box rush_att vs the pbp's QB carries with and without kneels."""
    pbp = collections.defaultdict(lambda: [0, 0])
    for season, p in P.items():
        st = D.load_season(season)
        for i in np.where((p["kind"] == KIND["run"]) | (p["kind"] == KIND["scramble"]) | (p["kind"] == KIND["kneel"]))[0]:
            r = int(p["rusher"][i])
            if pos.get(r) != "QB":
                continue
            wk = st["games"][int(p["gi"][i])]["week"]
            sid = ids_sleeper.get(r)
            if not sid:
                continue
            key = (season, wk, sid)
            pbp[key][0] += 1
            if p["kind"][i] != KIND["kneel"]:
                pbp[key][1] += 1
    box = {}
    with gzip.open(PLAYER_WEEKS, "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            if int(r["season"]) in SEASONS and r["pos"] == "QB" and r["has_act"] == "1":
                box[(int(r["season"]), int(r["week"]), r["player_id"])] = float(r["act_rush_att"] or 0)
    eq_with, eq_without, n = 0, 0, 0
    for k, (w, wo) in pbp.items():
        if k in box and w != wo:            # only games with a kneel can tell the two apart
            n += 1
            eq_with += int(box[k] == w)
            eq_without += int(box[k] == wo)
    return {"qb_games_with_a_kneel_matched": n, "box_equals_pbp_with_kneels": eq_with, "box_equals_pbp_without_kneels": eq_without}


# ---- E: the shape question on RBs, 2023 -> 2024 ------------------------------------------
def nb_pmf(mu, alpha, kmax):
    """NegBin pmf 0..kmax (alpha 0 -> Poisson)."""
    out = np.zeros(kmax + 1)
    if alpha < 1e-9:
        out[0] = math.exp(-mu)
        for k in range(1, kmax + 1):
            out[k] = out[k - 1] * mu / k
        return out
    r = 1.0 / alpha
    p = r / (r + mu)
    out[0] = p ** r
    for k in range(1, kmax + 1):
        out[k] = out[k - 1] * (k - 1 + r) / k * (1 - p)
    return out


def compound_sf(cpmf, kernel, lo, ks):
    """Exact P(S >= k) for S = sum of C iid kernel draws (kernel over yards lo..)."""
    dist = np.ones(1)
    off = 0
    sf = {k: 0.0 for k in ks}
    for c, pc in enumerate(cpmf):
        if c > 0:
            dist = np.convolve(dist, kernel)
            off += lo
        if pc < 1e-9:
            continue
        xs = np.arange(len(dist)) + off
        for k in ks:
            sf[k] += pc * float(dist[xs >= k].sum())
    return sf


def section_e(P23):
    """RB rows with a Sleeper carries line >= 5 (the priced universe)."""
    def rb_rows(season):
        out = []
        with gzip.open(PLAYER_WEEKS, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                if int(r["season"]) != season or r["pos"] != "RB" or r["has_proj"] != "1" or r["has_act"] != "1":
                    continue
                c, y = float(r["proj_rush_att"] or 0), float(r["proj_rush_yd"] or 0)
                if c >= 5 and y > 0:
                    out.append((c, y, float(r["act_rush_att"] or 0), float(r["act_rush_yd"] or 0), r["game_key"]))
        return np.array([x[:4] for x in out], float), [x[4] for x in out]
    A23, _g23 = rb_rows(2023)
    A24, g24 = rb_rows(2024)
    # carries' alpha by moments, overall and by line decile (2023)
    c, yc = A23[:, 0], A23[:, 2]
    alpha_all = max(0.0, float(np.sum((yc - c) ** 2 - c) / np.sum(c * c)))
    q = np.quantile(c, np.linspace(0, 1, 6))
    dec = []
    for lo, hi in zip(q[:-1], q[1:]):
        b = (c >= lo) & (c <= hi)
        dec.append({"carry_line": [round(float(lo), 1), round(float(hi), 1)], "n": int(b.sum()),
                    "alpha": round(max(0.0, float(np.sum((yc[b] - c[b]) ** 2 - c[b]) / np.sum(c[b] ** 2))), 4),
                    "p0_obs": round(float(np.mean(yc[b] == 0)), 4)})
    # the per-carry kernel (2023 RB runs), yards -15..99 clipped
    y = np.clip(P23["yds"][(P23["kind"] == KIND["run"]) & (P23["rpos"] == "RB")], -15, 99)
    lo = -15
    kern = np.bincount((y - lo).astype(int), minlength=99 - lo + 1).astype(float)
    kern /= kern.sum()
    m0 = float(np.sum(kern * (np.arange(len(kern)) + lo)))
    # the shipped single-CV family on 2023 (the first model's moments, recomputed here on these rows)
    cv_num = cv_den = 0.0
    for cl, yl, _ac, ay in A23:
        pz = nb_pmf(cl, alpha_all, 0)[0]
        mpos = yl / (1 - pz)
        if ay > 0:
            cv_num += (ay - mpos) ** 2
            cv_den += mpos ** 2
    cv = math.sqrt(cv_num / cv_den)
    ks = (10, 15, 25, 40, 50, 60, 75, 100)
    from research.nfl_matchup_fit import gammaq, clustered
    cells = {"20-30": [], "50+": [], "all": []}
    ll_a, ll_b = [], []
    for (cl, yl, _ac, ay) in A24:
        pz = nb_pmf(cl, alpha_all, 0)[0]
        mpos = yl / (1 - pz)
        sh = 1 / cv ** 2
        pa = {k: (1 - pz) * float(gammaq(np.array([sh]), np.array([(k - 0.5) / (mpos * cv * cv)]))[0]) for k in ks}
        # compound: carries NegBin(line, alpha), per-carry kernel scaled to the line's ypc (multiplicative)
        s = (yl / cl) / m0
        xs = np.arange(len(kern)) + lo
        ys = np.round(xs * s).astype(int)
        lo2 = int(ys.min())
        k2 = np.bincount(ys - lo2, weights=kern, minlength=int(ys.max() - lo2 + 1))
        pb = compound_sf(nb_pmf(cl, alpha_all, int(cl * 3 + 15)), k2, lo2, ks)
        la = lb = 0.0
        for k in ks:
            hit = ay >= k
            a_ = min(1 - 1e-6, max(1e-6, pa[k]))
            b_ = min(1 - 1e-6, max(1e-6, pb[k]))
            la -= math.log(a_) if hit else math.log(1 - a_)
            lb -= math.log(b_) if hit else math.log(1 - b_)
        ll_a.append(la)
        ll_b.append(lb)
        for lab, ok in (("20-30", 20 <= yl < 30), ("50+", yl >= 50), ("all", True)):
            if ok:
                cells[lab].append((ay, pa, pb))
    d, se = clustered(np.array(ll_b) - np.array(ll_a), np.array(g24, dtype=object))
    cal = {}
    for lab, rows in cells.items():
        cal[lab] = {"n": len(rows)}
        for k in (10, 15, 25, 40):
            cal[lab][f"{k}+"] = {"hit": round(float(np.mean([r[0] >= k for r in rows])), 3),
                                 "single_cv": round(float(np.mean([r[1][k] for r in rows])), 3),
                                 "compound": round(float(np.mean([r[2][k] for r in rows])), 3)}
    return {"rows_2023": int(len(A23)), "rows_2024": int(len(A24)), "carries_alpha_2023": round(alpha_all, 4),
            "carries_alpha_by_line_quintile_2023": dec, "single_cv_2023": round(cv, 4),
            "kernel_mean_2023": round(m0, 3),
            "ll_2024_compound_minus_single": {"d": d, "se": se, "n": len(ll_a)}, "calibration_2024": cal}


# ---- F / G / H -----------------------------------------------------------------------------
def _box_rows(seasons, pos_set):
    out = []
    with gzip.open(PLAYER_WEEKS, "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            if int(r["season"]) in seasons and r["pos"] in pos_set and r["has_proj"] == "1" and r["has_act"] == "1":
                out.append(r)
    return out


def _team_spread(stores):
    """{(season, week, team): team-side pre-game spread (nflverse, + = favoured)}."""
    out = {}
    for st in stores:
        for g in st["games"]:
            if g.get("spread") is None:
                continue
            out[(st["season"], g["week"], g["home"])] = g["spread"]
            out[(st["season"], g["week"], g["away"])] = -g["spread"]
    return out


SL2NV = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX"}


def section_f(stores):
    """Does Sleeper's QB rush_att projection carry the kneels? Actual minus
    projected attempts for starters (projected pass_att >= 20), by spread."""
    sp = _team_spread(stores)
    cells = collections.defaultdict(list)
    for r in _box_rows(SEASONS, {"QB"}):
        if float(r["proj_pass_att"] or 0) < 20:
            continue
        t = SL2NV.get(r["team"], r["team"])
        v = sp.get((int(r["season"]), int(r["week"]), t))
        if v is None:
            continue
        b = "fav 7+" if v >= 7 else "fav 3-6.5" if v >= 3 else "pick" if v > -3 else "dog 3-6.5" if v > -7 else "dog 7+"
        cells[b].append((float(r["act_rush_att"] or 0) - float(r["proj_rush_att"] or 0),
                         float(r["act_rush_yd"] or 0) - float(r["proj_rush_yd"] or 0), float(r["proj_rush_att"] or 0)))
    return {b: {"n": len(v), "att_actual_minus_proj": round(float(np.mean([x[0] for x in v])), 3),
                "yds_actual_minus_proj": round(float(np.mean([x[1] for x in v])), 3),
                "proj_att": round(float(np.mean([x[2] for x in v])), 3)} for b, v in sorted(cells.items())}


def section_g(stores):
    """Within-team residual correlations (actual minus Sleeper's revised line),
    2023-2024: the QB's rushing attempts vs his RB1's carries, the QB's rush
    TDs vs the RBs' rush TDs, an RB's rushing vs receiving yards."""
    rows = _box_rows(SEASONS, {"QB", "RB"})
    by_tg = collections.defaultdict(list)
    for r in rows:
        by_tg[(r["season"], r["week"], r["team"])].append(r)
    f = lambda r, k: float(r["act_" + k] or 0) - float(r["proj_" + k] or 0)
    qa, rc, qt, rt, ry, ryc = [], [], [], [], [], []
    for _k, rs in by_tg.items():
        qbs = [r for r in rs if r["pos"] == "QB" and float(r["proj_pass_att"] or 0) >= 20]
        rbs = sorted([r for r in rs if r["pos"] == "RB"], key=lambda r: -float(r["proj_rush_att"] or 0))
        if len(qbs) != 1 or not rbs:
            continue
        q = qbs[0]
        qa.append(f(q, "rush_att"))
        rc.append(f(rbs[0], "rush_att"))
        qt.append(f(q, "rush_td"))
        rt.append(sum(f(r, "rush_td") for r in rbs))
    for r in rows:
        if r["pos"] == "RB" and float(r["proj_rush_att"] or 0) >= 5:
            ry.append(f(r, "rush_yd"))
            ryc.append(f(r, "rec_yd"))
    c = lambda a, b: round(float(np.corrcoef(a, b)[0, 1]), 4)
    return {"team_games": len(qa), "qb_rush_att_vs_rb1_carries": c(qa, rc), "qb_rush_td_vs_rbs_rush_td": c(qt, rt),
            "rb_games": len(ry), "rb_rush_yd_vs_rec_yd_same_player": c(ry, ryc)}


def _qb_components(P, stores, pos, sl):
    """{(season, week, sleeper id): [designed, sneaks, scrambles, kneels, yds per component...]}."""
    out = collections.defaultdict(lambda: [0, 0, 0, 0])
    for (season, p), st in zip(P.items(), stores):
        k = p["kind"]
        for i in np.where((k == KIND["run"]) | (k == KIND["scramble"]) | (k == KIND["kneel"]))[0]:
            r = int(p["rusher"][i])
            if pos.get(r) != "QB" or not sl.get(r):
                continue
            j = 2 if k[i] == KIND["scramble"] else 3 if k[i] == KIND["kneel"] else 1 if p["sneak"][i] == 1 else 0
            out[(season, st["games"][int(p["gi"][i])]["week"], sl[r])][j] += 1
    return out


def _kern(y, lo=-15, hi=99):
    y = np.clip(np.asarray(y, float), lo, hi).astype(int)
    k = np.bincount(y - lo, minlength=hi - lo + 1).astype(float)
    return k / k.sum(), lo


def _scale(kern, lo, s):
    """Mass-splitting rescale x -> s x (the mean scales exactly)."""
    xs = (np.arange(len(kern)) + lo) * s
    fl = np.floor(xs).astype(int)
    fr = xs - fl
    lo2 = int(fl.min())
    out = np.zeros(int(fl.max()) - lo2 + 2)
    np.add.at(out, fl - lo2, kern * (1 - fr))
    np.add.at(out, fl - lo2 + 1, kern * fr)
    return out, lo2


def _compound_pmf(cpmf, kern, lo):
    dist, off, tot = np.ones(1), 0, {}
    acc = np.zeros(1)
    acc_lo = 0
    for c, pc in enumerate(cpmf):
        if c > 0:
            dist = np.convolve(dist, kern)
            off += lo
        if pc < 1e-10:
            continue
        lo_new = min(acc_lo, off)
        hi_new = max(acc_lo + len(acc), off + len(dist))
        a2 = np.zeros(hi_new - lo_new)
        a2[acc_lo - lo_new:acc_lo - lo_new + len(acc)] += acc
        a2[off - lo_new:off - lo_new + len(dist)] += pc * dist
        acc, acc_lo = a2, lo_new
    del tot
    return acc, acc_lo


def _conv(a, alo, b, blo):
    return np.convolve(a, b), alo + blo


def section_h(P, stores, pos, sl):
    """QB rushing yards, fit 2023 -> checked on 2024 (starters, projected
    pass_att >= 20 and rush_att >= 2): the shipped point-mass + single-CV
    gamma against a compound of designed runs, sneaks, scrambles and kneels
    with POPULATION shares (the player-specific shares are the data layer's).
    Kneels: the 2023 mean by team-side spread bucket; the rest of Sleeper's
    attempts split by the 2023 shares; per-attempt kernels 2023, scaled so the
    total mean is Sleeper's yards line."""
    from research.nfl_matchup_fit import gammaq, clustered
    comp = _qb_components(P, stores, pos, sl)
    sp = _team_spread(stores)
    kern = {}
    p23 = P[2023]
    k, rp = p23["kind"], p23["rpos"]
    qbrun = (k == KIND["run"]) & (rp == "QB")
    kern["designed"] = _kern(p23["yds"][qbrun & (p23["sneak"] != 1)])
    kern["sneak"] = _kern(p23["yds"][qbrun & (p23["sneak"] == 1)])
    kern["scramble"] = _kern(p23["yds"][k == KIND["scramble"]])
    kern["kneel"] = _kern(p23["yds"][k == KIND["kneel"]])
    kmean = {j: float(np.sum(v[0] * (np.arange(len(v[0])) + v[1]))) for j, v in kern.items()}
    bucket = lambda v: "fav7" if v >= 7 else "fav3" if v >= 3 else "pick" if v > -3 else "dog3" if v > -7 else "dog7"
    rows = {2023: [], 2024: []}
    for r in _box_rows(SEASONS, {"QB"}):
        if float(r["proj_pass_att"] or 0) < 20 or float(r["proj_rush_att"] or 0) < 2:
            continue
        v = sp.get((int(r["season"]), int(r["week"]), SL2NV.get(r["team"], r["team"])))
        c = comp.get((int(r["season"]), int(r["week"]), r["player_id"]))
        if v is None or c is None:
            continue
        rows[int(r["season"])].append((float(r["proj_rush_att"]), float(r["proj_rush_yd"] or 0), float(r["act_rush_yd"] or 0),
                                       bucket(v), c, r["game_key"], float(r["act_rush_att"] or 0)))
    kn = collections.defaultdict(list)
    for a, y, ay, b, c, g, aa in rows[2023]:
        kn[b].append(c[3])
    kn_mean = {b: float(np.mean(v)) for b, v in kn.items()}
    tot = np.sum([c[:3] for *_x, c, _g, _aa in rows[2023]], axis=0)
    share = tot / tot.sum()
    # per-component alpha by moments against the apportioned expectation (2023)
    alpha = {}
    for j in range(3):
        e = np.array([max(0.05, a - kn_mean[b]) * share[j] for a, y, ay, b, c, g, aa in rows[2023]])
        o = np.array([c[j] for a, y, ay, b, c, g, aa in rows[2023]], float)
        alpha[j] = max(0.0, float(np.sum((o - e) ** 2 - e) / np.sum(e * e)))
    ek = np.array([kn_mean[b] for a, y, ay, b, c, g, aa in rows[2023]])
    ok_ = np.array([c[3] for a, y, ay, b, c, g, aa in rows[2023]], float)
    alpha[3] = max(0.0, float(np.sum((ok_ - ek) ** 2 - ek) / np.sum(ek * ek)))
    # the shipped family's parameters on 2023 (carries NegBin alpha on the attempts line, single CV)
    A = np.array([(a, aa) for a, y, ay, b, c, g, aa in rows[2023]])
    alpha_att = max(0.0, float(np.sum((A[:, 1] - A[:, 0]) ** 2 - A[:, 0]) / np.sum(A[:, 0] ** 2)))
    num = den = 0.0
    for a, y, ay, b, c, g, aa in rows[2023]:
        pz = nb_pmf(a, alpha_att, 0)[0]
        mp = max(y, 0.5) / (1 - pz)
        if ay > 0:
            num += (ay - mp) ** 2
            den += mp * mp
    cv = math.sqrt(num / den)
    ks = (10, 15, 25, 40, 50, 60)
    names = ("designed", "sneak", "scramble")
    ll_a, ll_b, gs, cells = [], [], [], {"10-20": [], "20-30": [], "all": []}
    for a, y, ay, b, c, g, aa in rows[2024]:
        K = kn_mean[b]
        rest = max(0.05, a - K)
        cnts = [rest * share[j] for j in range(3)]
        base = sum(cnts[j] * kmean[names[j]] for j in range(3))
        target = y - K * kmean["kneel"]
        s = max(0.05, target / base) if base > 0 else 1.0
        pmf, lo = np.ones(1), 0
        for j in range(3):
            kj, lj = _scale(*kern[names[j]], s)
            cp, cl = _compound_pmf(nb_pmf(cnts[j], alpha[j], int(cnts[j] * 4 + 12)), kj, lj)
            pmf, lo = _conv(pmf, lo, cp, cl)
        cp, cl = _compound_pmf(nb_pmf(K, alpha[3], int(K * 4 + 10)), *kern["kneel"])
        pmf, lo = _conv(pmf, lo, cp, cl)
        xs = np.arange(len(pmf)) + lo
        pb = {k_: float(pmf[xs >= k_].sum()) for k_ in ks}
        pz = nb_pmf(a, alpha_att, 0)[0]
        mp = max(y, 0.5) / (1 - pz)
        pa = {k_: (1 - pz) * float(gammaq(np.array([1 / cv ** 2]), np.array([(k_ - 0.5) / (mp * cv * cv)]))[0]) for k_ in ks}
        la = lb = 0.0
        for k_ in ks:
            hit = ay >= k_
            a_, b_ = min(1 - 1e-6, max(1e-6, pa[k_])), min(1 - 1e-6, max(1e-6, pb[k_]))
            la -= math.log(a_) if hit else math.log(1 - a_)
            lb -= math.log(b_) if hit else math.log(1 - b_)
        ll_a.append(la)
        ll_b.append(lb)
        gs.append(g)
        for lab, ok in (("10-20", 10 <= y < 20), ("20-30", 20 <= y < 30), ("all", True)):
            if ok:
                cells[lab].append((ay, pa, pb))
    d, se = clustered(np.array(ll_b) - np.array(ll_a), np.array(gs, dtype=object))
    cal = {}
    for lab, rr in cells.items():
        cal[lab] = {"n": len(rr)}
        for k_ in (10, 15, 25, 40):
            cal[lab][f"{k_}+"] = {"hit": round(float(np.mean([x[0] >= k_ for x in rr])), 3),
                                  "single_cv": round(float(np.mean([x[1][k_] for x in rr])), 3),
                                  "compound": round(float(np.mean([x[2][k_] for x in rr])), 3)}
    return {"rows_2023": len(rows[2023]), "rows_2024": len(rows[2024]), "kneels_by_spread_2023": kn_mean,
            "shares_2023": {n_: round(float(x), 4) for n_, x in zip(names, share)},
            "alpha_2023": {("designed", "sneak", "scramble", "kneel")[j]: round(v, 4) for j, v in alpha.items()},
            "single_cv_2023": round(cv, 4), "ll_2024_compound_minus_single": {"d": d, "se": se, "n": len(ll_a)},
            "calibration_2024": cal}


def main():
    pos = positions()
    ids = D.load_ids() or {}
    sl = {g: r.get("sleeper") for g, r in (ids.get("ids") or {}).items() if r.get("sleeper")}
    stores, P = [], {}
    for s in SEASONS:
        st, p = plays(s, pos)
        stores.append(st)
        P[s] = p
    out = {"seasons": list(SEASONS), "A_per_attempt": section_a(P.values()), "B_qb_game": section_b(P.values(), pos),
           "C_kneels": section_c(stores, list(P.values())), "D_box_kneels": section_d(P, pos, sl),
           "E_rb_shape": section_e(P[2023]), "F_sleeper_qb_kneels": section_f(stores), "G_correlations": section_g(stores),
           "H_qb_shape": section_h(P, stores, pos, sl)}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
