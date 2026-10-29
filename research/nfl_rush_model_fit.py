"""The RB / QB rushing model's research side (design and draft rule:
research/reports/nfl_rush_model.md). RESEARCH (numpy).

So far it holds the DISTRIBUTION fit, the part that needs no data-layer
feature:
- the per-attempt kernels by role;
- the count over-dispersion curves alpha(mean), by the passing-yards decile
  template;
- the kneel table by pre-game margin;
- the QB position priors;
- the within-team residual correlations.

It also holds `traincheck`: fit on 2023, score 2024 (inside the fitting
seasons, like the first model's traincheck), with production's own
nfl_rush.Compound against the shipped point-mass + single-CV family. The
ladder, the frame and the validate / test commands join when the rushing
data layer is agreed. No 2025 or 2026 row is read by anything here.

    export DEEP_CACHE_DIR=<nfl_matchup_<season>.pkl + ids> VIGIL_NO_BG=1
    python3 -m research.nfl_rush_model_fit traincheck --out <work>/traincheck.json
"""
import argparse
import collections
import csv
import gzip
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
os.environ.setdefault("VIGIL_NO_BG", "1")

import nfl_matchup_data as D            # noqa: E402
import nfl_rush as NR                   # noqa: E402
import prop_dist as PD                  # noqa: E402

PLAYER_WEEKS = os.path.join(HERE, "data", "player_weeks_2022_2025.csv.gz")
KIND = {k: i for i, k in enumerate(D.KIND)}
KLO, KHI = -15, 99                      # kernel support (yards); 2023-24 had no run below -15 that mattered
SL2NV = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX"}
HELD = (2025, 2026)                     # read only once the rule is registered and every registered file still hashes


def prereg_status():
    """(ok, why): research/data/nfl_rush_model/prereg.json exists, the rule block and EVERY
    registered code and data file still hash to it, and the registered sets are this module's.
    The ONLY way a held season's outcomes become readable (no constant to edit)."""
    path = os.path.join(MODEL_OUT, "prereg.json")
    if not os.path.exists(path):
        return False, "the rule is not registered"
    try:
        with open(path) as fh:
            rec = json.load(fh)
        import hashlib
        if hashlib.sha256(rule_block().encode("utf-8")).hexdigest() != rec.get("sha256"):
            return False, "the rule block no longer hashes to the registration"
        if set(rec.get("code") or {}) != set(REG_CODE) or set(rec.get("data") or {}) != set(REG_DATA):
            return False, "the registered file sets differ from this module's"
        for f, h in list((rec.get("code") or {}).items()) + list((rec.get("data") or {}).items()):
            if _sha(os.path.join(REPO, f)) != h:
                return False, f"{f} no longer hashes to the registration"
    except (OSError, ValueError, KeyError, SystemExit) as e:
        return False, f"the registration cannot be read: {e!r}"
    return True, "registered"


def _guard(seasons):
    bad = [s for s in seasons if s in HELD]
    if bad:
        ok, why = prereg_status()
        if not ok:
            raise SystemExit(f"season(s) {bad} are held: {why} (research/reports/nfl_rush_model.md, Q9)")


def positions():
    ids = D.load_ids() or {}
    return {g: (r.get("pos") or "") for g, r in (ids.get("ids") or {}).items()}, \
        {g: r.get("sleeper") for g, r in (ids.get("ids") or {}).items() if r.get("sleeper")}


def positions_map(ids_payload):
    """{gsis (str): [position, Sleeper id or None]} for every player of an ids pickle's payload: what the
    distribution fit reads from it (`positions`), recorded at registration (Fable's m7)."""
    return {str(g): [r.get("pos") or "", r.get("sleeper") or None] for g, r in ((ids_payload or {}).get("ids") or {}).items()}


def registered_positions(rec):
    """(positions, Sleeper ids) as `positions()` returns them, from the registration's record of the ids
    pickle it was made with: every registered command's distribution parts read these, never a later ids
    pickle (build-stores writes a fresh one for the 2026 rows, in which nflverse may relist a player).
    None when the registration recorded none (a plumbing run reads the ids pickle)."""
    m = ((rec or {}).get("ids_positions") or {}).get("map")
    if m is None:
        return None
    return {int(g): v[0] for g, v in m.items()}, {int(g): v[1] for g, v in m.items() if v[1]}


def season_plays(season, pos):
    _guard([season])
    st = D.load_season(season)
    if st is None:
        raise SystemExit(f"no compact store for {season} in DEEP_CACHE_DIR")
    c = st["cols"]
    p = {k: np.asarray(c[k]) for k in ("kind", "rusher", "sneak", "rpo", "gi", "off", "passer", "receiver", "comp")}
    p["yds"] = np.asarray(c["yds"], float)
    p["rpos"] = np.array([pos.get(int(r), "") if r > 0 else "" for r in p["rusher"]], dtype=object)
    p["recpos"] = np.array([pos.get(int(r), "") if r > 0 else "" for r in p["receiver"]], dtype=object)
    return st, p


# ---- kernels --------------------------------------------------------------------------------
def kernel(y):
    y = np.clip(np.asarray(y, float), KLO, KHI).astype(int)
    k = np.bincount(y - KLO, minlength=KHI - KLO + 1).astype(float)
    k /= k.sum()
    # trim the empty ends so the committed file stays small
    nz = np.nonzero(k)[0]
    return {"lo": int(KLO + nz[0]), "p": [round(float(x), 8) for x in k[nz[0]:nz[-1] + 1]], "n": int(len(y))}


RUNNER_DPG = 1.5      # research only: the QB-type kernel arm of traincheck_frame (it did not help; not in production)


def fit_kernels(P, by_type=False):
    """Per-attempt yards by role, pooled over the fitting seasons' plays.
    Designed QB runs exclude sneaks (FTN flag; their own kernel) and include
    RPO keeps (an RPO that becomes a pass is a pass, not here). A scramble is
    nflverse's qb_scramble; a kneel its qb_kneel. Sacks never enter."""
    cat = lambda f: np.concatenate([f(p) for p in P])
    run = KIND["run"]
    out = {
        "rb_run": kernel(cat(lambda p: p["yds"][(p["kind"] == run) & (p["rpos"] == "RB")])),
        "rb_catch": kernel(cat(lambda p: p["yds"][(p["kind"] == KIND["attempt"]) & (p["comp"] == 1) & (p["recpos"] == "RB")])),
        "qb_designed": kernel(cat(lambda p: p["yds"][(p["kind"] == run) & (p["rpos"] == "QB") & (p["sneak"] != 1)])),
        "qb_sneak": kernel(cat(lambda p: p["yds"][(p["kind"] == run) & (p["rpos"] == "QB") & (p["sneak"] == 1)])),
        "qb_scramble": kernel(cat(lambda p: p["yds"][p["kind"] == KIND["scramble"]])),
        "kneel": kernel(cat(lambda p: p["yds"][p["kind"] == KIND["kneel"]])),
    }
    if by_type:
        runner = _runner_mask(P)
        qd = lambda p: (p["kind"] == run) & (p["rpos"] == "QB") & (p["sneak"] != 1)
        out["qb_designed_runner"] = kernel(cat(lambda p: p["yds"][qd(p) & runner[id(p)]]))
        out["qb_designed_other"] = kernel(cat(lambda p: p["yds"][qd(p) & ~runner[id(p)]]))
    return out


def _runner_mask(P):
    """{id(plays): bool per play} -- the rusher is a RUNNER QB that season
    (RUNNER_DPG+ designed runs, sneaks apart, per game he dropped back in):
    the season-level split the research arm's two designed-run kernels are
    fitted on. Production has ONE pooled designed-run kernel (section 1:
    the split did not help)."""
    out = {}
    for p in P:
        des = collections.Counter()
        games = collections.defaultdict(set)
        k = p["kind"]
        for i in np.where((k == KIND["run"]) & (p["rpos"] == "QB") & (p["sneak"] != 1))[0]:
            des[int(p["rusher"][i])] += 1
        for i in np.where((k == KIND["attempt"]) | (k == KIND["sack"]) | (k == KIND["scramble"]))[0]:
            q = int(p["passer"][i]) if k[i] != KIND["scramble"] else int(p["rusher"][i])
            games[q].add(int(p["gi"][i]))
        run_q = {q for q, n in des.items() if n / max(1, len(games.get(q, ()))) >= RUNNER_DPG}
        out[id(p)] = np.array([int(r) in run_q for r in p["rusher"]], bool)
    return out


def fit_qb_alpha_frame(rows, kneels, prior):
    """RESEARCH ARM (traincheck_frame's own-share arms; production's split is
    the position's, nfl_rush.QB_SHARES). The QB components' alpha curves
    against the own-share expectation: Sleeper's attempts minus the expected
    kneels, split by the QB's OWN as-of shares (the frame's qp_*; the
    position's where he has none). The question it answered: does the
    position-share alpha (fit_qb) carry the share error? Re-fitting it
    against his own split did not rescue the own-share arm (section 1)."""
    co = {"kneels": kneels}
    mu = {"qb_designed": [], "qb_sneak": [], "qb_scramble": []}
    y = {"qb_designed": [], "qb_sneak": [], "qb_scramble": []}
    ek, ok = [], []
    for r in rows:
        A = _x(r, "sl_rush_att") or 0.0
        if A <= 0:
            continue
        K = min(NR.expected_kneels(co, _x(r, "margin")), 0.8 * A)
        rest = A - K
        sh = {p: _x(r, f"qp_{p}_share") for p in ("designed", "sneak", "scramble")}
        if any(v is None for v in sh.values()):
            sh = {p: prior[f"{p}_share"] for p in ("designed", "sneak", "scramble")}
        for p, col in (("designed", "y_des"), ("sneak", "y_sneak"), ("scramble", "y_scr")):
            mu["qb_" + p].append(max(1e-3, rest * sh[p]))
            y["qb_" + p].append(_x(r, col) or 0.0)
        ek.append(K)
        ok.append(_x(r, "y_kneel") or 0.0)
    out = {k: alpha_curve(mu[k], y[k]) for k in mu}
    kc = alpha_curve(ek, ok, bins=1, min_rows=10)
    out["kneel"] = {"a": kc["a"], "b": 0.0, "cap": kc["cap"], "bins": kc["bins"]}
    return out


# ---- count over-dispersion by the line: the decile template ----------------------------------------
def alpha_curve(mu, y, bins=5, min_rows=40, cap=1.5):
    """alpha(mean) = a x mean^b: rows binned at the quantiles of their
    expected count, each bin's alpha by moments (sum((y - mu)^2 - mu) /
    sum(mu^2), floored at 0), least squares of log alpha on log(mean expected
    count). A bin whose alpha is 0 enters at 1e-3 (log). Returns the curve
    and its bins."""
    mu, y = np.asarray(mu, float), np.asarray(y, float)
    q = np.quantile(mu, np.linspace(0, 1, bins + 1))
    xs, ys, tab = [], [], []
    for lo, hi in zip(q[:-1], q[1:]):
        b = (mu >= lo) & (mu <= hi)
        if b.sum() < min_rows:
            continue
        a = max(0.0, float(np.sum((y[b] - mu[b]) ** 2 - mu[b]) / np.sum(mu[b] ** 2)))
        tab.append({"mean_lo": round(float(lo), 3), "mean_hi": round(float(hi), 3), "n": int(b.sum()), "alpha": round(a, 4)})
        xs.append(math.log(float(np.mean(mu[b]))))
        ys.append(math.log(max(a, 1e-3)))
    if len(xs) < 2:
        a = max(0.0, float(np.sum((y - mu) ** 2 - mu) / np.sum(mu ** 2)))
        return {"a": a, "b": 0.0, "cap": cap, "bins": tab}
    b_, la = np.polyfit(xs, ys, 1)
    return {"a": float(math.exp(la)), "b": float(b_), "cap": cap, "bins": tab}


# ---- Sleeper lines and box (player_weeks) ---------------------------------------------------
def box_rows(seasons, pos_set):
    _guard(seasons)
    out = []
    with gzip.open(PLAYER_WEEKS, "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            # Q1's "played": Sleeper's box shows gp >= 1 (has_act alone is the same rows on 2023-24, but
            # the rule names gp, never a snap count)
            if int(r["season"]) in seasons and r["pos"] in pos_set and r["has_proj"] == "1" and r["has_act"] == "1" \
                    and f(r, "act_gp") >= 1:
                out.append(r)
    return out


def f(r, k):
    try:
        return float(r[k] or 0.0)
    except (TypeError, ValueError):
        return 0.0


def team_margin(stores):
    """{(season, week, nflverse team): pre-game expected margin, + = favoured}
    (nflverse spread_line is home-positive)."""
    out = {}
    for st in stores:
        for g in st["games"]:
            if g.get("spread") is None:
                continue
            out[(st["season"], g["week"], g["home"])] = float(g["spread"])
            out[(st["season"], g["week"], g["away"])] = -float(g["spread"])
    return out


def qb_components(stores, P, pos, sl):
    """{(season, week, sleeper id): [designed, sneaks, scrambles, kneels]} from the pbp."""
    out = collections.defaultdict(lambda: [0, 0, 0, 0])
    for st, p in zip(stores, P):
        k = p["kind"]
        for i in np.where((k == KIND["run"]) | (k == KIND["scramble"]) | (k == KIND["kneel"]))[0]:
            r = int(p["rusher"][i])
            if pos.get(r) != "QB" or not sl.get(r):
                continue
            j = 2 if k[i] == KIND["scramble"] else 3 if k[i] == KIND["kneel"] else 1 if p["sneak"][i] == 1 else 0
            out[(st["season"], st["games"][int(p["gi"][i])]["week"], sl[r])][j] += 1
    return out


def fit_kneels(stores, P):
    """Expected kneels per team-game at knots of the pre-game margin: the mean
    of the buckets <= -7, (-7, -3], (-3, 3), [3, 7), >= 7 placed at -10, -5,
    0, 5, 10 (production interpolates linearly between them)."""
    kn = collections.Counter()
    for st, p in zip(stores, P):
        for i in np.where(p["kind"] == KIND["kneel"])[0]:
            kn[(st["season"], int(p["gi"][i]), st["teams"][int(p["off"][i])])] += 1
    cells = collections.defaultdict(list)
    for st in stores:
        for gi, g in enumerate(st["games"]):
            if g.get("spread") is None:
                continue
            for side, m in ((g["home"], float(g["spread"])), (g["away"], -float(g["spread"]))):
                b = -10 if m <= -7 else -5 if m <= -3 else 0 if m < 3 else 5 if m < 7 else 10
                cells[b].append(kn[(st["season"], gi, side)])
    allv = [x for v in cells.values() for x in v]
    return {"by_margin": [[b, round(float(np.mean(v)), 4)] for b, v in sorted(cells.items())],
            "n": {str(b): len(v) for b, v in sorted(cells.items())}, "mean": round(float(np.mean(allv)), 4)}


def fit_qb(stores, P, pos, sl, kneels, seasons):
    """The QB position priors (shares of NON-kneel rushing attempts, yards per
    attempt by component) and the components' alpha curves against the
    anchored expectation: Sleeper's attempts minus the expected kneels, split
    by the position shares."""
    comp = qb_components(stores, P, pos, sl)
    mg = team_margin(stores)
    rows = []
    for r in box_rows(seasons, {"QB"}):
        if f(r, "proj_pass_att") < 20 or f(r, "proj_rush_att") <= 0:
            continue
        # a QB who played (Sleeper's box, gp >= 1) with no rush at all is a row of zeros, never dropped:
        # dropping him (82 of 1,087 QB-games in 2023-24) selected the curves' rows on the outcome
        c = list(comp.get((int(r["season"]), int(r["week"]), r["player_id"])) or [0, 0, 0, 0])
        m = mg.get((int(r["season"]), int(r["week"]), SL2NV.get(r["team"], r["team"])))
        if m is None:
            continue
        rows.append((f(r, "proj_rush_att"), m, c))
    tot = np.sum([c[:3] for _a, _m, c in rows], axis=0).astype(float)
    shares = tot / tot.sum()
    co = {"kneels": kneels}
    ek = np.array([NR.expected_kneels(co, m) for _a, m, _c in rows])
    rest = np.maximum(0.05, np.array([a for a, _m, _c in rows]) - ek)
    curves = {}
    for j, nm in enumerate(("qb_designed", "qb_sneak", "qb_scramble")):
        curves[nm] = alpha_curve(rest * shares[j], np.array([c[j] for _a, _m, c in rows], float))
    kc = alpha_curve(ek, np.array([c[3] for _a, _m, c in rows], float), bins=1, min_rows=10)
    curves["kneel"] = {"a": kc["a"], "b": 0.0, "cap": kc["cap"], "bins": kc["bins"]}
    prior = {"designed_share": float(shares[0]), "sneak_share": float(shares[1]), "scramble_share": float(shares[2]),
             "rows": len(rows)}
    return prior, curves


def fit_rb_curves(seasons):
    rows = box_rows(seasons, {"RB"})
    c = [(f(r, "proj_rush_att"), f(r, "act_rush_att")) for r in rows if f(r, "proj_rush_att") > 0 and f(r, "proj_rush_yd") > 0]
    rc = [(f(r, "proj_rec"), f(r, "act_rec")) for r in rows if f(r, "proj_rec") > 0 and f(r, "proj_rec_yd") > 0]
    return {"rb_carries": alpha_curve([x for x, _y in c], [y for _x, y in c]),
            "rb_receptions": alpha_curve([x for x, _y in rc], [y for _x, y in rc])}


def fit_distributions(seasons, by_type=False, pos_sl=None):
    """The distribution half of the committed model, fit on `seasons`. `pos_sl`: the (positions,
    Sleeper ids) to read instead of the ids pickle's -- the registered commands pass the
    registration's (`registered_positions`), so a player nflverse relists later moves no kernel."""
    _guard(seasons)
    pos, sl = pos_sl if pos_sl is not None else positions()
    stores, P = [], []
    for s in seasons:
        st, p = season_plays(s, pos)
        stores.append(st)
        P.append(p)
    kern = fit_kernels(P, by_type)
    kneels = fit_kneels(stores, P)
    prior, qb_curves = fit_qb(stores, P, pos, sl, kneels, seasons)
    curves = dict(fit_rb_curves(seasons), **qb_curves)
    return {"schema": NR.SCHEMA, "fit_seasons": list(seasons),
            "kernels": {k: {"lo": v["lo"], "p": v["p"]} for k, v in kern.items()},
            "kernel_n": {k: v["n"] for k, v in kern.items()},
            "alpha": curves, "kneels": kneels, "qb_prior": prior, "shipped": {}}


# ---- traincheck: fit 2023, score 2024 with production's engine ---------------------------------
def _single_cv(rows, alpha_c, use_kneel_floor=False):
    num = den = 0.0
    for c, y, _ay in rows:
        pz = PD.NegBin(c, alpha_c).pmf(0) if alpha_c > 0 else math.exp(-c)
        mp = max(y, 0.5) / (1 - pz)
        if _ay > 0:
            num += (_ay - mp) ** 2
            den += mp * mp
    return math.sqrt(num / den)


def _ll(p, hit):
    p = min(1 - 1e-6, max(1e-6, p))
    return -math.log(p) if hit else -math.log(1 - p)


def cmd_traincheck(a):
    fit = fit_distributions((2023,))
    co = dict(fit)
    from research.nfl_matchup_fit import clustered
    out = {"note": "fit 2023, scored on 2024 -- inside the fitting seasons, for design only", "fit": {
        "alpha": {k: {kk: v[kk] for kk in ("a", "b")} for k, v in fit["alpha"].items()}, "kneels": fit["kneels"],
        "qb_prior": fit["qb_prior"], "kernel_n": fit["kernel_n"]}}
    # RBs: 5+ projected carries
    r23 = [(f(r, "proj_rush_att"), f(r, "proj_rush_yd"), f(r, "act_rush_yd")) for r in box_rows((2023,), {"RB"})
           if f(r, "proj_rush_att") >= 5 and f(r, "proj_rush_yd") > 0]
    a_c = max(0.0, float(sum((f(r, "act_rush_att") - f(r, "proj_rush_att")) ** 2 - f(r, "proj_rush_att")
                             for r in box_rows((2023,), {"RB"}) if f(r, "proj_rush_att") >= 5)
                         / sum(f(r, "proj_rush_att") ** 2 for r in box_rows((2023,), {"RB"}) if f(r, "proj_rush_att") >= 5)))
    cv = _single_cv(r23, a_c)
    ks = NR.THRESHOLDS["RB"]["rush_yd"]
    ll_s, ll_c, gs, cells = [], [], [], collections.defaultdict(list)
    for r in box_rows((2024,), {"RB"}):
        c, y, ay = f(r, "proj_rush_att"), f(r, "proj_rush_yd"), f(r, "act_rush_yd")
        if c < 5 or y <= 0:
            continue
        pz = PD.NegBin(c, a_c).pmf(0)
        ys = PD.Yards(min(0.999, pz), y / (1 - pz), cv * y / (1 - pz), "gamma")
        dc, _p = NR.rb_dists({"rush_att": c, "rush_yd": y}, co)
        comp = dc["rush_yd"]
        ll_s.append(sum(_ll(ys.sf_ge(k), ay >= k) for k in ks))
        ll_c.append(sum(_ll(comp.sf_ge(k), ay >= k) for k in ks))
        gs.append(r["game_key"])
        for lab, ok in (("lines 20-30", 20 <= y < 30), ("lines 50+", y >= 50), ("all", True)):
            if ok:
                cells[lab].append((ay, [ys.sf_ge(k) for k in (10, 15, 25, 40)], [comp.sf_ge(k) for k in (10, 15, 25, 40)]))
    d, se = clustered(np.array(ll_c) - np.array(ll_s), np.array(gs, dtype=object))
    out["rb_rush_yd"] = {"rows": len(ll_c), "carries_alpha_single": a_c, "single_cv": cv,
                         "compound_minus_single": {"d": d, "se": se}, "calibration": _cal(cells)}
    # QBs: starters with 2+ projected attempts, position priors (the data layer's shares come later)
    pos, sl = positions()
    stores, P = [], []
    for s in (2023, 2024):
        st, p = season_plays(s, pos)
        stores.append(st)
        P.append(p)
    mg = team_margin(stores)
    q23 = [(f(r, "proj_rush_att"), f(r, "proj_rush_yd"), f(r, "act_rush_yd")) for r in box_rows((2023,), {"QB"})
           if f(r, "proj_pass_att") >= 20 and f(r, "proj_rush_att") >= 2]
    qa = [(f(r, "proj_rush_att"), f(r, "act_rush_att")) for r in box_rows((2023,), {"QB"})
          if f(r, "proj_pass_att") >= 20 and f(r, "proj_rush_att") >= 2]
    a_q = max(0.0, float(sum((y - x) ** 2 - x for x, y in qa) / sum(x * x for x, _y in qa)))
    cvq = _single_cv(q23, a_q)
    ksq = NR.THRESHOLDS["QB"]["rush_yd"]
    ll_s, ll_c, gs, cells = [], [], [], collections.defaultdict(list)
    for r in box_rows((2024,), {"QB"}):
        if f(r, "proj_pass_att") < 20 or f(r, "proj_rush_att") < 2:
            continue
        m = mg.get((2024, int(r["week"]), SL2NV.get(r["team"], r["team"])))
        if m is None:
            continue
        A, Y, ay = f(r, "proj_rush_att"), f(r, "proj_rush_yd"), f(r, "act_rush_yd")
        pz = PD.NegBin(A, a_q).pmf(0)
        ys = PD.Yards(min(0.999, pz), max(Y, 0.5) / (1 - pz), cvq * max(Y, 0.5) / (1 - pz), "gamma")
        dq, _pq = NR.qb_dists({"rush_att": A, "rush_yd": Y}, None, co, team_margin=m)
        comp = dq["rush_yd"]
        ll_s.append(sum(_ll(ys.sf_ge(k), ay >= k) for k in ksq))
        ll_c.append(sum(_ll(comp.sf_ge(k), ay >= k) for k in ksq))
        gs.append(r["game_key"])
        for lab, ok in (("lines 10-20", 10 <= Y < 20), ("lines 20-30", 20 <= Y < 30), ("all", True)):
            if ok:
                cells[lab].append((ay, [ys.sf_ge(k) for k in (10, 15, 25, 40)], [comp.sf_ge(k) for k in (10, 15, 25, 40)]))
    d, se = clustered(np.array(ll_c) - np.array(ll_s), np.array(gs, dtype=object))
    out["qb_rush_yd"] = {"rows": len(ll_c), "attempts_alpha_single": a_q, "single_cv": cvq,
                         "compound_minus_single": {"d": d, "se": se}, "calibration": _cal(cells)}
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps({k: out[k]["compound_minus_single"] for k in ("rb_rush_yd", "qb_rush_yd")}))


# ---- the frame (research/nfl_rush_model_frame.py) ---------------------------------------------
def read_frame(path, seasons, outcomes=False):
    """The frame's rows for `seasons` as dicts. Outcome columns (every y_*) are
    DROPPED unless `outcomes`, and a held season's (HELD) outcomes are readable
    only through the registration check (prereg_status): row counts and NaN
    audits on 2025 / 2026 read identities, lines and features only."""
    seasons = set(int(x) for x in seasons)
    if outcomes:
        _guard(seasons)
    out = []
    with gzip.open(path, "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            if int(r["season"]) not in seasons:
                continue
            if not outcomes:
                r = {k: v for k, v in r.items() if not k.startswith("y_")}
            out.append(r)
    return out


def _x(r, k):
    v = r.get(k)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def universe(r, market, kind="sl"):
    """The draft rule's Q1 row universes, on Sleeper's line (`kind` "sl"
    revised, "fz" frozen): production's own definition (nfl_rush.in_universe,
    which the board reads too) over the frame's columns."""
    base = {f: (_x(r, f"{kind}_{f}") or 0.0) for f in ("rush_att", "rush_yd", "rec_tgt", "rec", "rush_td", "rec_td",
                                                      "pass_att")}
    return NR.in_universe(r["pos"], market, base)

def cmd_traincheck_frame(a):
    """Fit 2023, score 2024, through the FRAME: RB rushing yards (compound vs
    the single-CV family) and QB rushing yards in three arms -- the single
    CV, the compound on POSITION shares, the compound on the QB's OWN as-of
    shares and yards per attempt (the data layer's decomposition)."""
    rows = read_frame(a.frame, (2023, 2024), outcomes=True)
    co = fit_distributions((2023,), by_type=True)
    from research.nfl_matchup_fit import clustered
    out = {"note": "fit 2023, scored on 2024 -- inside the fitting seasons, through the frame", "frame": a.frame}
    for pos_ in ("RB", "QB"):
        tr = [r for r in rows if r["pos"] == pos_ and int(r["season"]) == 2023 and universe(r, "rush_yd")]
        te = [r for r in rows if r["pos"] == pos_ and int(r["season"]) == 2024 and universe(r, "rush_yd")]
        A = [(_x(r, "sl_rush_att"), _x(r, "y_rush_att")) for r in tr]
        a_c = max(0.0, sum((y - x) ** 2 - x for x, y in A) / sum(x * x for x, _y in A))
        cv = _single_cv([(_x(r, "sl_rush_att"), _x(r, "sl_rush_yd"), _x(r, "y_rush_yd")) for r in tr], a_c)
        ks = NR.THRESHOLDS[pos_]["rush_yd"]
        arms = ("single_cv", "compound") if pos_ == "RB" else (
            "single_cv", "compound_position_shares", "compound_own_shares", "own_shares_own_alpha",
            "own_shares_own_alpha_type_kernels")
        if pos_ == "QB":
            # the own-share arms say so (nfl_rush.QB_SHARES: production reads the position's split)
            co_own = dict(co, qb_shares="own",
                          alpha=dict(co["alpha"], **fit_qb_alpha_frame(tr, co["kneels"], co["qb_prior"])))
            co_pooled_kern = dict(co_own, kernels={k: v for k, v in co["kernels"].items()
                                                   if k not in ("qb_designed_runner", "qb_designed_other")})
            co_noown = dict(co, kernels=co_pooled_kern["kernels"])
            # the type-kernel arm swaps the designed kernel per row (production has one pooled kernel)
            co_runner = dict(co_own, kernels=dict(co_pooled_kern["kernels"], qb_designed=co["kernels"]["qb_designed_runner"]))
            co_other = dict(co_own, kernels=dict(co_pooled_kern["kernels"], qb_designed=co["kernels"]["qb_designed_other"]))
        ll = {k: [] for k in arms}
        gs, cells = [], collections.defaultdict(list)
        own_n = 0
        for r in te:
            c, y, ay = _x(r, "sl_rush_att"), _x(r, "sl_rush_yd"), _x(r, "y_rush_yd")
            pz = PD.NegBin(c, a_c).pmf(0)
            ys = PD.Yards(min(0.999, pz), max(y, 0.5) / (1 - pz), cv * max(y, 0.5) / (1 - pz), "gamma")
            d = {"single_cv": ys}
            if pos_ == "RB":
                d["compound"] = NR.rb_dists({"rush_att": c, "rush_yd": y}, co)[0]["rush_yd"]
            else:
                base = {"rush_att": c, "rush_yd": y, "pass_att": _x(r, "sl_pass_att")}
                m = _x(r, "margin")
                d["compound_position_shares"] = NR.qb_dists(base, None, co_noown, team_margin=m)[0]["rush_yd"]
                prof = None
                if _x(r, "qp_designed_share") is not None:
                    prof = {k: _x(r, f"qp_{k}") for k in ("designed_share", "sneak_share", "scramble_share",
                                                         "ypa_designed", "ypa_scramble")}
                    own_n += 1
                runner = r.get("qp_runner") in ("True", "1", "true")
                d["compound_own_shares"] = NR.qb_dists(base, prof, dict(co_noown, qb_shares="own"),
                                                       team_margin=m)[0]["rush_yd"]
                d["own_shares_own_alpha"] = NR.qb_dists(base, prof, co_pooled_kern, team_margin=m)[0]["rush_yd"]
                d["own_shares_own_alpha_type_kernels"] = NR.qb_dists(base, prof, co_runner if runner else co_other,
                                                                     team_margin=m)[0]["rush_yd"]
            for k in arms:
                ll[k].append(sum(_ll(d[k].sf_ge(t), ay >= t) for t in ks))
            gs.append(r["game_id"])
            lines = ((("lines 20-30", 20 <= y < 30), ("lines 50+", y >= 50)) if pos_ == "RB" else
                     (("lines 10-20", 10 <= y < 20), ("lines 20-30", 20 <= y < 30)))
            for lab, ok in lines + (("all", True),):
                if ok:
                    cells[lab].append((ay, {k: [d[k].sf_ge(t) for t in (10, 15, 25, 40)] for k in arms}))
        g = np.array(gs, dtype=object)
        res = {"rows": len(te), "fit_rows_2023": len(tr), "own_shares_rows": own_n if pos_ == "QB" else None,
               "mean_ll": {k: float(np.mean(v)) for k, v in ll.items()}}
        for k in arms[1:]:
            dd, se = clustered(np.array(ll[k]) - np.array(ll["single_cv"]), g)
            res[f"{k}_minus_single_cv"] = {"d": dd, "se": se}
        if pos_ == "QB":
            for k in arms[2:]:
                dd, se = clustered(np.array(ll[k]) - np.array(ll["compound_position_shares"]), g)
                res[f"{k}_minus_position_shares"] = {"d": dd, "se": se}
            res["p_le0"] = {"hit": round(float(np.mean([x[0] <= 0 for x in cells["all"]])), 3)}
        cal = {}
        for lab, rr in cells.items():
            cal[lab] = {"n": len(rr)}
            for i, t in enumerate((10, 15, 25, 40)):
                cal[lab][f"{t}+"] = dict({"hit": round(float(np.mean([x[0] >= t for x in rr])), 3)},
                                         **{k: round(float(np.mean([x[1][k][i] for x in rr])), 3) for k in arms})
        res["calibration"] = cal
        out[f"{pos_.lower()}_rush_yd"] = res
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps({k: {kk: v for kk, v in out[k].items() if kk != "calibration"} for k in ("rb_rush_yd", "qb_rush_yd")},
                     indent=1))


MARKETS_BY_POS = {"RB": ("rush_att", "rush_yd", "rec", "rec_yd", "rush_rec_yd", "td", "long_rush"),
                  "QB": ("rush_att", "rush_yd", "td", "long_rush")}
FAMILY_OF = {("RB", "rush_att"): "rb_rush", ("RB", "rush_yd"): "rb_rush", ("RB", "rush_rec_yd"): "rb_rush",
             ("RB", "long_rush"): "rb_rush", ("RB", "rec"): "rb_rec", ("RB", "rec_yd"): "rb_rec", ("RB", "td"): "rb_td",
             ("QB", "rush_att"): "qb_rush", ("QB", "rush_yd"): "qb_rush", ("QB", "long_rush"): "qb_rush",
             ("QB", "td"): "qb_td"}


def cmd_audit(a):
    """Row counts per market per season (on Sleeper's REVISED line for
    2023-2025 and the FROZEN capture for 2026) and NaN rates per feature group
    on every season's rows. Reads identities, lines and features only: no
    outcome column is loaded (read_frame drops y_*)."""
    seasons = tuple(a.seasons)
    rows = read_frame(a.frame, seasons, outcomes=False)
    assert not any(k.startswith("y_") for r in rows[:1] for k in r), "an outcome column reached the audit"
    out = {"frame": a.frame, "counts": {}, "nan": {}}
    for s in seasons:
        rs = [r for r in rows if int(r["season"]) == s]
        kind = "fz" if s == 2026 else "sl"
        cnt = {}
        for pos_, mkts in MARKETS_BY_POS.items():
            for m in mkts:
                sel = [r for r in rs if r["pos"] == pos_ and universe(r, m, kind)]
                cnt[f"{pos_} {m}"] = {"rows": len(sel), "games": len({r["game_id"] for r in sel})}
        if s == 2026:
            cnt["frozen rows (any)"] = sum(1 for r in rs if r.get("fz_src"))
            cnt["rows (any)"] = len(rs)
        out["counts"][str(s)] = cnt
        nan = {}
        for (pos_, m), fam in FAMILY_OF.items():
            sel = [r for r in rs if r["pos"] == pos_ and universe(r, m, kind)]
            if not sel:
                continue
            for g, fs in NR.GROUPS[fam].items():
                for f, _acts in fs:
                    key = f"{pos_} {fam} {g} {f}"
                    if key in nan:
                        continue
                    miss = sum(1 for r in sel if r.get(f"f_{f}") in ("", None))
                    nan[key] = {"rows": len(sel), "missing": miss, "rate": round(miss / len(sel), 4)}
        out["nan"][str(s)] = nan
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out["counts"], indent=1))


# ---- the decisions and the registration --------------------------------------------------------
REPORT = os.path.join(REPO, "research", "reports", "nfl_rush_model.md")
RULE_A, RULE_B = "<!-- RUSH-PREREG-START -->", "<!-- RUSH-PREREG-END -->"
REGISTERED_MARK = ".nfl_rush_registered_tree"


def resolve_model_out(repo, env):
    """The decision records' folder. In a registered tree (its marker present) it is the marker's
    `model_out` -- the repository's folder, outside the tree -- and an NFLR_MODEL_OUT naming another
    folder is refused: a run in the tree that read and wrote the tree's own copies would lose the
    once-lock (a second fresh tree would hold no lock: the re-check's C-M2). Elsewhere it is
    NFLR_MODEL_OUT or the repository's research/data/nfl_rush_model."""
    mark = os.path.join(repo, REGISTERED_MARK)
    if os.path.exists(mark):
        with open(mark, encoding="utf-8") as fh:
            mo = os.path.abspath(json.load(fh)["model_out"])
        if env and os.path.abspath(env) != mo:
            raise SystemExit(f"refused: NFLR_MODEL_OUT ({env}) is not the registered tree's folder ({mo})")
        return mo
    return os.path.abspath(env) if env else os.path.join(repo, "research", "data", "nfl_rush_model")


MODEL_OUT = resolve_model_out(REPO, os.environ.get("NFLR_MODEL_OUT"))
REG_CODE = ("nfl_rush.py", "prop_dist.py", "research/nfl_rush_model_frame.py", "research/nfl_rush_model_fit.py",
            "research/nfl_rush_model_rule.py",
            "nfl_rush_data.py", "nfl_rush_profiles.py", "research/nfl_rush_dataset.py",
            # what the frame and the fit also execute: the store loader, positions, the first model's
            # defense block (m1_*), its frozen-capture reader (and the name normaliser it calls), its
            # clustered SE; deep_cache resolves and loads every store pickle, and kalshi_nfl imports kalshi
            # (the re-check's audit hook: errlog and research/__init__ are the only other repo modules
            # loaded, the ledger and an empty package, behaviour-neutral)
            "nfl_matchup_data.py", "nfl_matchup_profiles.py", "nfl_matchup.py", "research/nfl_matchup_frame.py",
            "research/nfl_matchup_fit.py", "kalshi_nfl.py", "deep_cache.py", "kalshi.py",
            # the prospective dataset's Sleeper parser (load_sleeper: the box, the decisive test's outcomes),
            # the one module the dataset build loads beyond these (the confirmation's C2 audit)
            "research/nfl_matchup_dataset.py")
# loaded by the runs, behaviour-neutral: the ledger and the empty package
NEUTRAL = ("errlog.py", "research/__init__.py")
REG_DATA = ("research/data/nfl_rush/rb_games_2023_2026.csv.gz", "research/data/nfl_rush/qb_games_2023_2026.csv.gz",
            "research/data/nfl_rush/reliability.json", "research/data/nfl_rush/schema.json",
            # the first model's k tables (its defense deviations, m1_*: fit-only for the validation frame)
            # and the box rows the RB count curves are fitted on
            "research/data/nfl_matchup/reliability.json", "research/data/nfl_matchup_model/k_fit_only.json",
            "research/data/player_weeks_2022_2025.csv.gz")
GO = "data-layer-agreed"


def _sha(path):
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def versions():
    """The interpreter and numpy the runs execute on (recorded at registration and in every record)."""
    return {"python": sys.version.split()[0], "numpy": np.__version__}


def repo_modules():
    """Every repository module file this process has loaded, by repo-relative path."""
    out = set()
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if f and os.path.abspath(f).startswith(REPO + os.sep):
            out.add(os.path.relpath(os.path.abspath(f), REPO).replace(os.sep, "/"))
    return sorted(out)


def unregistered_code(code, rec):
    """The executed files of a build record ({file: sha256}) that are not the registered bytes: a
    registered file at other bytes, or a file outside the registered set that is not
    behaviour-neutral. Empty means the build ran on the registered code."""
    reg = rec.get("code") or {}
    return sorted(f for f, h in (code or {}).items() if (f in reg and reg[f] != h) or (f not in reg and f not in NEUTRAL))


# ---- content digests (the durability round: Opus's F2, Sonnet's F1) ------------------------------------
# The registration pins 17 store / aux / ids pickles that exist in one place. deep_cache.save stamps
# `generated_at` beside each payload and the builders stamp their own clocks inside it, so even a faithful
# rebuild from the same nflverse files never hashes the same: a lost pickle could never be matched again.
# The CONTENT digest leaves out exactly the parts no registered computation reads -- the save clocks
# (`built`, `seconds`, the aux's `store_built`), the download bookkeeping (`sources`) and the aux build's join
# diagnostics (`joins`, read only by the dataset's `checks` report) -- and hashes everything else
# canonically: dict keys and sets sorted, floats by their exact hex, arrays by type code and bytes. A
# re-saved or rebuilt store holding the same data digests the same, and one changed play does not: 2023's
# store and aux rebuilt from a fresh download on 2026-10-28 matched the registration-time pair exactly,
# the aux's crosswalk statistics (`joins.pfr_crosswalk`, players.csv having grown) the one part that moved.
# Each top-level part is digested on its own (and a small dict part key by key), so a mismatch names what
# differs ("cols.yds"). An unknown type raises: a payload the encoder cannot pin is never waved through.
NOT_CONTENT = ("built", "seconds", "store_built", "sources", "joins")
DIGEST_SPLIT = 100


def _canon(buf, x):
    import array
    if x is None:
        buf += b"N"
    elif isinstance(x, bool):
        buf += b"T" if x else b"F"
    elif isinstance(x, int):
        buf += b"i%d;" % x
    elif isinstance(x, float):
        buf += b"f" + x.hex().encode() + b";"
    elif isinstance(x, str):
        e = x.encode("utf-8")
        buf += b"s%d:" % len(e)
        buf += e
    elif isinstance(x, bytes):
        buf += b"b%d:" % len(x)
        buf += x
    elif isinstance(x, array.array):
        e = x.tobytes()
        buf += b"a" + x.typecode.encode() + b"%d:" % len(e)
        buf += e
    elif isinstance(x, (list, tuple)):
        buf += (b"l%d[" if isinstance(x, list) else b"t%d[") % len(x)
        for v in x:
            _canon(buf, v)
        buf += b"]"
    elif isinstance(x, dict):
        buf += b"d%d{" % len(x)
        for kb, v in sorted(((_key(k), v) for k, v in x.items()), key=lambda kv: kv[0]):
            buf += kb
            _canon(buf, v)
        buf += b"}"
    elif isinstance(x, (set, frozenset)):
        buf += b"S%d{" % len(x)
        for kb in sorted(_key(v) for v in x):
            buf += kb
        buf += b"}"
    else:
        raise TypeError(f"content digest: no canonical form for {type(x).__name__}")


def _key(k):
    b = bytearray()
    _canon(b, k)
    return bytes(b)


def _hex(x):
    import hashlib
    b = bytearray()
    _canon(b, x)
    return hashlib.sha256(b).hexdigest()


def content_digest(payload):
    """{"digest", "parts"} of a deep-store payload, the parts no registered computation reads (NOT_CONTENT)
    left out."""
    import hashlib
    parts = {}
    for k in sorted((k for k in (payload or {}) if k not in NOT_CONTENT), key=str):
        v = payload[k]
        parts[str(k)] = _hex(v)
        if isinstance(v, dict) and len(v) <= DIGEST_SPLIT:
            for kk, vv in v.items():
                parts[f"{k}.{kk}"] = _hex(vv)
    top = "".join(f"{k}={h};" for k, h in sorted(parts.items()) if "." not in k)
    return {"digest": hashlib.sha256(top.encode()).hexdigest(), "parts": parts}


def deep_digest(key, folder=None):
    """{"sha256", "digest", "parts"} of the deep-store pickle `key` (DEEP_CACHE_DIR, or `folder`), or None
    when it is missing or cannot be read."""
    import deep_cache
    import hashlib
    import pickle
    p = os.path.join(folder or deep_cache.CACHE_DIR, f"{key}.pkl")
    try:
        with open(p, "rb") as fh:
            raw = fh.read()
        rec = pickle.loads(raw)
    except OSError:
        return None
    except Exception as e:      # an unreadable pickle is reported missing, and ledgered
        import errlog
        errlog.note("NFLR-digest", e, path=key)
        return None
    payload = rec.get("payload") if isinstance(rec, dict) else None
    return dict({"sha256": hashlib.sha256(raw).hexdigest()}, **content_digest(payload))


def digest_diff(want, got):
    """The parts whose content differs between two digests, finest first recorded (`cols.yds`, never
    `cols` beside it); [] when the digests agree."""
    if (want or {}).get("digest") == (got or {}).get("digest"):
        return []
    wp, gp = (want or {}).get("parts") or {}, (got or {}).get("parts") or {}
    names = sorted(k for k in set(wp) | set(gp) if wp.get(k) != gp.get(k))
    fine = [k for k in names if "." in k]
    coarse = [k for k in names if "." not in k and not any(f.startswith(k + ".") for f in fine)]
    return coarse + fine or ["(the digest)"]


def store_refusal(key, rec, folder=None, sha=None):
    """Why the deep-store pickle `key` (as "deep:<key>") is not the registration's, or None: its sha256
    is the registered one, or -- a re-saved or rebuilt pickle -- its CONTENT digest is. A mismatch names
    the parts that differ. `sha`: the sha256 a frame recorded reading it (the file must still be that one)."""
    want = ((rec or {}).get("stores") or {}).get(key)
    if not want:
        return f"{key[5:]} has no record in prereg.json"
    got = deep_digest(key[5:], folder)
    if got is None:
        return f"{key[5:]} is missing"
    if sha is not None and got["sha256"] != sha:
        return f"{key[5:]} is not the file the frame read"
    if got["sha256"] == want["sha256"]:
        return None
    diff = digest_diff(want, got)
    if diff:
        return f"{key[5:]} is not the registration's: its content differs in {diff[:8]}"
    return None


STORES_KEY = "nfl_rush_prospective_stores"


def build_stores(season, older=()):
    """The prospective window's season store and its rushing aux, built in the REGISTERED tree from a
    fresh download into the deep folder DEEP_CACHE_DIR names (a scratch folder holding the registration's
    older pickles -- copied from the archive by its manifest, or, the archive lost, rebuilt here with
    `older`), never by production's live job and never in a live folder or a copy of one (the
    confirmation's C2d). It records the pickles' sha256, every repository module that ran and the versions
    under STORES_KEY; the prospective frame records that record, and `prospective` refuses a 2026 store,
    aux or ids it does not vouch for.
    - It vouches only for what its OWN refresh built (Opus's F1a): each season's store summary must
      carry a play count ("n") and its aux "built": True, and the ids must have been rebuilt; a store or
      aux the refresh kept ("skipped": unchanged upstream; "error": the download failed) was made by
      something else, and it refuses.
    - `older` (seasons the registered frames read, before `season`) are rebuilt too, and each store and
      aux must match the registration's CONTENT digest (store_refusal); a mismatch refuses, naming the
      parts that differ (nflverse revised the season, or the ids it was built with moved)."""
    if not in_registered_tree():
        raise SystemExit("refused: the prospective stores are built in the registered tree")
    import deep_cache
    import nfl_rush_data as RD
    from research.nfl_rush_model_frame import input_shas
    with open(os.path.join(MODEL_OUT, "prereg.json")) as fh:
        rec = json.load(fh)
    season, older = int(season), sorted({int(s_) for s_ in older})
    unknown = [s_ for s_ in older if s_ >= season or f"deep:nfl_matchup_{s_}" not in (rec.get("stores") or {})]
    if unknown:
        raise SystemExit(f"refused: {unknown} are not older seasons the registered frames read")
    seasons = older + [season]
    # the seasons' store and aux already in the folder are moved aside (<key>.pkl.replaced), so the refresh
    # BUILDS them: a re-run after a refusal past the refresh would otherwise find them current and keep them
    # (and be refused by the check below forever); an --older season's archive copy waits there
    for s_ in seasons:
        for k in (f"nfl_matchup_{s_}", f"nfl_rush_aux_{s_}"):
            p_ = os.path.join(deep_cache.CACHE_DIR, f"{k}.pkl")
            if os.path.exists(p_):
                os.replace(p_, p_ + ".replaced")
                print(f"[build-stores] {k}.pkl moved aside to {k}.pkl.replaced: this refresh builds it")
    sd = D.refresh(seasons=seasons, log=print) or {}
    sr = RD.refresh(seasons=seasons, log=print) or {}
    for s_ in seasons:
        a_, b_ = (sd.get("seasons") or {}).get(s_) or {}, (sr.get("seasons") or {}).get(s_) or {}
        if "n" not in a_ or b_.get("built") is not True:
            raise SystemExit(f"refused: the refresh did not BUILD season {s_}'s store and aux (store {a_}, aux {b_}): "
                             "what the folder holds was made by something else, and nothing is vouched for")
    if not sd.get("ids"):
        raise SystemExit("refused: the refresh did not rebuild the ids pickle (players.csv did not come down)")
    keys = [f"nfl_matchup_{season}", f"nfl_rush_aux_{season}", "nfl_matchup_ids"]
    out = {"season": season, "prereg_sha256": rec["sha256"], "deep_dir": deep_cache.CACHE_DIR,
           "stores": input_shas([f"deep:{k}" for k in keys]), "older": {},
           "code": {f: _sha(os.path.join(REPO, f)) for f in repo_modules()}, "versions": versions(),
           "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for s_ in older:
        for k in (f"deep:nfl_matchup_{s_}", f"deep:nfl_rush_aux_{s_}"):
            why = store_refusal(k, rec)
            if why:
                raise SystemExit(f"refused: {why} (nflverse revised the season, or the ids it was built with moved; "
                                 f"the archive's copy is {k[5:]}.pkl.replaced)")
            out["older"][k] = input_shas([k])[k]
    bad = unregistered_code(out["code"], rec)
    if bad or any(v is None for v in out["stores"].values()):
        raise SystemExit(f"refused: the stores were not built by the registered code or are missing ({bad})")
    deep_cache.save(STORES_KEY, out)
    return out


def procedure(dest):
    """The commands that make the prospective test's inputs in the registered tree `dest`, with the
    records' folder filled in by ABSOLUTE path (Q1; the reviews' F2 / F3: run from the tree's root, a
    relative --out writes the tree's own copy, which `prospective` rightly refuses)."""
    mo = os.path.abspath(MODEL_OUT)
    ds = os.path.join(mo, "prospective_dataset")
    return [f"cd {dest}",
            "export VIGIL_NO_BG=1 DEEP_CACHE_DIR=<an EMPTY scratch folder, then the registration's 2019-2025 pickles "
            f"copied into it from the archive, checked by {os.path.join(mo, 'prereg_manifest.sha256')}>",
            "python3 -m research.nfl_rush_model_fit build-stores        # the archive lost: add --older 2019 ... 2025",
            f"python3 -m research.nfl_rush_dataset build --all-weeks --sleeper <the box dir> --out {ds}",
            "python3 -m research.nfl_rush_model_frame --k pooled --seasons 2026 --weeks <the window's> --frozen-only "
            f"--dataset {ds} --sleeper <the box dir> --props <every window week's props log> --out <prospective frame>",
            "python3 -m research.nfl_rush_model_rule prospective --frame <the registered test frame> "
            "--frame-prosp <prospective frame>"]


_TREE_SKIP = (".git", "__pycache__", "pc-simcache", "pc-deepcache", "pc-standings", "vigil-pc.cfg")


def registered_tree(dest, src=None):
    """A copy of the repository whose REGISTERED code files are the bytes stored at registration
    (prereg_code/), not the live ones: the prospective dataset, frame and test run here weeks after
    the hash, while others edit production files (nfl_matchup.py, kalshi_nfl.py, nfl_rush.py). Every
    stored copy must hash to prereg.json. The marker it writes is what `prospective` requires; runs
    inside it write their records to the repository's folder (NFLR_MODEL_OUT)."""
    import shutil
    path = os.path.join(MODEL_OUT, "prereg.json")
    if not os.path.exists(path):
        raise SystemExit("refused: the rule is not registered (no prereg.json)")
    with open(path) as fh:
        rec = json.load(fh)
    if os.path.commonpath([os.path.abspath(MODEL_OUT), os.path.abspath(dest)]) == os.path.abspath(dest):
        raise SystemExit("refused: the records' folder may not lie inside the registered tree")
    if os.path.exists(dest) and os.listdir(dest):
        raise SystemExit(f"refused: {dest} is not empty")
    code_dir = os.path.join(MODEL_OUT, "prereg_code")
    stored = {f: os.path.join(code_dir, f.replace("/", "__") + ".txt") for f in rec.get("code") or {}}
    bad = [f for f, p_ in stored.items() if not os.path.exists(p_) or _sha(p_) != rec["code"][f]]
    if bad or set(stored) != set(REG_CODE):
        raise SystemExit(f"refused: the stored registered code does not hash to prereg.json ({bad})")
    shutil.copytree(src or REPO, dest, dirs_exist_ok=True,
                    ignore=lambda d, names: [n for n in names if n in _TREE_SKIP or n.endswith((".db", ".pyc"))])
    for f, p_ in stored.items():
        os.makedirs(os.path.dirname(os.path.join(dest, f)), exist_ok=True)
        shutil.copyfile(p_, os.path.join(dest, f))
    # the tree's report carries the rule block STORED at registration: an edit inside the live report's block
    # after the hash would otherwise make every command in the tree refuse (Sonnet's F5e)
    rp = os.path.join(dest, os.path.relpath(REPORT, REPO))
    with open(os.path.join(code_dir, "rule_block.md.txt"), encoding="utf-8") as fh:
        blk = fh.read()
    import hashlib
    if hashlib.sha256(blk.encode("utf-8")).hexdigest() != rec["sha256"]:
        raise SystemExit("refused: the stored rule block does not hash to prereg.json")
    with open(rp, encoding="utf-8") as fh:
        txt = fh.read()
    if txt.count(RULE_A) != 1 or txt.count(RULE_B) != 1:
        raise SystemExit(f"refused: the tree's report ({rp}) has no single rule block to restore")
    with open(rp, "w", encoding="utf-8") as fh:
        fh.write(txt[:txt.find(RULE_A)] + blk + txt[txt.find(RULE_B) + len(RULE_B):])
    mo = os.path.abspath(MODEL_OUT)
    with open(os.path.join(dest, REGISTERED_MARK), "w", encoding="utf-8") as fh:
        json.dump({"prereg_sha256": rec["sha256"], "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "model_out": mo}, fh, indent=1)
    return dest


def in_registered_tree(repo=None, model_out=None):
    """The registered tree's marker, or None: the marker must name THIS registration, the records'
    folder must be the marker's `model_out`, and that folder must lie OUTSIDE the tree (the lock is
    the repository's, never a tree's copy)."""
    repo = os.path.abspath(repo or REPO)
    mo = os.path.abspath(model_out or MODEL_OUT)
    try:
        with open(os.path.join(repo, REGISTERED_MARK), encoding="utf-8") as fh:
            mark = json.load(fh)
        with open(os.path.join(mo, "prereg.json")) as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if os.path.abspath(mark.get("model_out") or "") != mo or os.path.commonpath([mo, repo]) == repo:
        return None
    return mark if mark.get("prereg_sha256") == rec.get("sha256") else None


def _meta(frame):
    """A frame's build record (k table, seasons, rows, missing counts), stored beside its hash."""
    try:
        with open(frame + ".meta.json", encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError) as e:
        return {"error": repr(e)}
    return {k: m.get(k) for k in ("rows", "k", "seasons", "participation", "code", "paths_absent", "inputs",
                                  "weeks", "frozen_only", "built_utc", "box_files", "dataset", "versions")}


def blocked_inputs(frames_meta):
    """Fitted inputs that may not be fitted yet: the ones the data layer has
    announced but not landed (nfl_rush.PENDING_PATHS, while a fitted feature
    reads them) and the ones a frame found on no row. Registration refuses
    while any remain."""
    from research.nfl_rush_model_frame import fitted_paths
    out = {"pending": sorted(set(NR.PENDING_PATHS) & fitted_paths())}
    for k, m in frames_meta.items():
        ab = m.get("paths_absent")
        out[f"absent_{k}"] = ["(no record: rebuild the frame)"] if ab is None else sorted(tuple(x) for x in ab)
    return {k: v for k, v in out.items() if v}


def stale_frames(frames_meta):
    """{frame: [files]} whose code OR input data changed since the frame was built: every
    registered code file, the dataset, the k tables and every aux / store pickle the build read
    (its meta records their sha256); a frame without that record is stale."""
    from research.nfl_rush_model_frame import code_shas, input_shas
    now = code_shas()
    out = {}
    for k, m in frames_meta.items():
        if not m.get("code") or not m.get("participation") or not m.get("inputs"):
            out[k] = ["(no build record: rebuild the frame)"]
            continue
        bad = [f for f, h in now.items() if m["code"].get(f) != h]
        bad += [f for f, h in input_shas(list(m["inputs"])).items() if m["inputs"].get(f) != h]
        if bad:
            out[k] = bad
    return out


def rule_block():
    with open(REPORT, encoding="utf-8") as fh:
        txt = fh.read()
    if txt.count(RULE_A) != 1 or txt.count(RULE_B) != 1:
        raise SystemExit("the report has no single RUSH-PREREG block")
    a, b = txt.find(RULE_A), txt.find(RULE_B)
    return txt[a:b + len(RULE_B)]


def cmd_register(a):
    """Hash the rule and store the bytes it runs on (the first model's F6).
    Refuses without --go data-layer-agreed: the orchestrator says when the
    data layer is agreed (C5: the stored data-layer bytes must be the agreed
    ones). --dry-run prints what it would write and writes nothing."""
    import hashlib
    import shutil
    if not a.dry_run and a.go != GO:
        raise SystemExit(f"not registered: the orchestrator's go (--go {GO}) is required")
    block = rule_block()
    rec = {"sha256": hashlib.sha256(block.encode("utf-8")).hexdigest(),
           "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "versions": versions(),
           "code": {f: _sha(os.path.join(REPO, f)) for f in REG_CODE},
           "data": {f: _sha(os.path.join(REPO, f)) for f in REG_DATA},
           "frames": {k: _sha(v) for k, v in (("val", a.frame_val), ("test", a.frame)) if v},
           "frames_meta": {k: _meta(v) for k, v in (("val", a.frame_val), ("test", a.frame)) if v}}
    stale = stale_frames(rec["frames_meta"])
    blocked = blocked_inputs(rec["frames_meta"])
    if not a.dry_run and (stale or set(rec["frames"]) != {"val", "test"}):
        raise SystemExit(f"not registered: both frames, built on the code being registered, are required ({stale})")
    if not a.dry_run and blocked:
        raise SystemExit(f"not registered: fitted inputs not landed or absent from the frames ({blocked})")
    # durability (Opus's F2, Sonnet's F1): every deep pickle the frames read, by sha256 AND by content digest
    # (a re-saved or rebuilt pickle holding the same data is still the registration's), and the positions the
    # distribution fit reads from the ids pickle (Fable's m7: later commands read these, never a fresh ids)
    t0 = time.time()
    deep_keys = sorted({k for m in rec["frames_meta"].values() for k in (m.get("inputs") or {})
                        if k.startswith("deep:") and k != f"deep:{STORES_KEY}"})
    rec["stores"] = {k: deep_digest(k[5:]) for k in deep_keys}
    ids_payload = _deep_payload("nfl_matchup_ids")
    pmap = positions_map(ids_payload)
    rec["ids_positions"] = {"digest": _hex(pmap), "n": len(pmap), "from": "deep:nfl_matchup_ids",
                            "ids_sha256": (rec["stores"].get("deep:nfl_matchup_ids") or {}).get("sha256"), "map": pmap}
    digest_s = round(time.time() - t0, 1)
    if a.frame_val and a.frame:
        # the dead features (zero training SD in either frame) and the decision count the walk will make
        import research.nfl_rush_model_rule as RULE
        Fs = [RULE.Frame(read_frame(p_, (2023, 2024, 2025), outcomes=False), RULE.kind_revised)
              for p_ in (a.frame_val, a.frame)]
        dead = RULE.dead_features(Fs, {0: (2023, 2024), 1: (2023, 2024, 2025)})
        rec["dead_features"] = {p_: sorted(v) for p_, v in dead.items()}
        rec["decisions"] = len(RULE.decisions(dead))
    if a.dry_run:
        rec["stale_frames"] = stale
        rec["blocked_inputs"] = {k: [list(x) if isinstance(x, tuple) else x for x in v] for k, v in blocked.items()}
        rec["digest_seconds"] = digest_s
        shown = {k: v for k, v in rec.items() if k not in ("frames_meta", "stores", "ids_positions")}
        shown["stores"] = {k: None if v is None else {"sha256": v["sha256"], "digest": v["digest"]} for k, v in rec["stores"].items()}
        shown["ids_positions"] = {k: v for k, v in rec["ids_positions"].items() if k != "map"}
        shown["frames_meta"] = {k: {kk: vv for kk, vv in m.items() if kk != "code"} for k, m in rec["frames_meta"].items()}
        print(json.dumps(shown, indent=1))
        return
    missing = [k for k, v in rec["stores"].items() if v is None]
    if missing:
        raise SystemExit(f"not registered: the frames' deep pickles {missing} cannot be read from DEEP_CACHE_DIR")
    path = os.path.join(MODEL_OUT, "prereg.json")
    if os.path.exists(path):
        raise SystemExit("already registered (research/data/nfl_rush_model/prereg.json)")
    code_dir = os.path.join(MODEL_OUT, "prereg_code")
    os.makedirs(code_dir, exist_ok=True)
    for f in REG_CODE:
        shutil.copyfile(os.path.join(REPO, f), os.path.join(code_dir, f.replace("/", "__") + ".txt"))
    with open(os.path.join(code_dir, "rule_block.md.txt"), "w", encoding="utf-8") as fh:
        fh.write(block)
    with open(path, "w") as fh:
        json.dump(rec, fh, indent=1)
    write_manifest(rec, {"val": a.frame_val, "test": a.frame})
    print(f"registered {rec['sha256']} at {rec['utc']}")


def _deep_payload(key):
    """A deep-store pickle's payload read straight from its file (None when it is missing)."""
    import deep_cache
    import pickle
    try:
        with open(os.path.join(deep_cache.CACHE_DIR, f"{key}.pkl"), "rb") as fh:
            return (pickle.load(fh) or {}).get("payload")
    except OSError:
        return None


def write_manifest(rec, frames):
    """prereg_manifest.json and prereg_manifest.sha256 beside prereg.json: the 19 files every later command
    verifies (the two frames by sha256; the 17 store, aux and ids pickles by sha256, or by the content digest
    beside it once re-saved or rebuilt), plus the frames' build records. Archive exactly these files, check
    them with `sha256sum -c prereg_manifest.sha256` in the archive's folder, and copy the pickles into the
    registered tree's scratch deep folder from there (Q1)."""
    import deep_cache
    files = {}
    for which, p_ in sorted(frames.items()):
        files[os.path.basename(p_)] = {"sha256": rec["frames"][which], "role": f"the registered {which} frame",
                                       "verified": True, "from": os.path.abspath(p_)}
        files[os.path.basename(p_) + ".meta.json"] = {"sha256": _sha(p_ + ".meta.json"), "verified": False,
                                                      "role": f"the {which} frame's build record (prereg.json holds it)",
                                                      "from": os.path.abspath(p_ + ".meta.json")}
    for k, v in sorted(rec["stores"].items()):
        files[f"{k[5:]}.pkl"] = {"sha256": v["sha256"], "digest": v["digest"], "role": "a store, aux or ids pickle",
                                 "verified": True, "from": os.path.join(deep_cache.CACHE_DIR, f"{k[5:]}.pkl")}
    man = {"prereg_sha256": rec["sha256"], "utc": rec["utc"], "files": files,
           "note": "the frames are refused unless they hash to these; a pickle is accepted by sha256 or, re-saved or "
                   "rebuilt, by its content digest (prereg.json `stores`, every part)"}
    with open(os.path.join(MODEL_OUT, "prereg_manifest.json"), "w") as fh:
        json.dump(man, fh, indent=1)
    with open(os.path.join(MODEL_OUT, "prereg_manifest.sha256"), "w") as fh:
        fh.write("".join(f"{v['sha256']}  {n}\n" for n, v in sorted(files.items())))
    return man


def _cal(cells):
    out = {}
    for lab, rows in cells.items():
        out[lab] = {"n": len(rows)}
        for i, k in enumerate((10, 15, 25, 40)):
            out[lab][f"{k}+"] = {"hit": round(float(np.mean([r[0] >= k for r in rows])), 3),
                                 "single_cv": round(float(np.mean([r[1][i] for r in rows])), 3),
                                 "compound": round(float(np.mean([r[2][i] for r in rows])), 3)}
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("traincheck", "traincheck_frame", "audit", "register", "registered-tree",
                                    "build-stores"))
    ap.add_argument("--out")
    ap.add_argument("--dest", help="registered-tree: the empty directory to build it in")
    ap.add_argument("--frame")
    ap.add_argument("--frame-val")
    ap.add_argument("--go")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seasons", nargs="*", type=int, default=[2023, 2024, 2025, 2026])
    ap.add_argument("--older", nargs="*", type=int, default=[],
                    help="build-stores: older seasons to rebuild as well (the archive lost); each must match the "
                         "registration's content digest")
    a = ap.parse_args(argv)
    {"traincheck": cmd_traincheck, "traincheck_frame": cmd_traincheck_frame, "audit": cmd_audit,
     "register": cmd_register,
     "registered-tree": lambda a_: print("\n".join([registered_tree(a_.dest), ""] + procedure(os.path.abspath(a_.dest)))),
     "build-stores": lambda a_: print(json.dumps(build_stores(2026, a_.older), indent=1))}[a.cmd](a)


if __name__ == "__main__":
    main(sys.argv[1:])
