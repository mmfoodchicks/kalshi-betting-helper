"""A mixture opponent field for showdown, fitted on development contests only
(2026-11-26, Phase B). The design, the split, the loss and the acceptance
criteria are in research/reports/dk_mech.md section B and were committed
BEFORE this module was run on anything.

The owner: "Investigate a mixture-based opponent-field model representing
different entrant construction behaviors ... These are hypotheses, not
predetermined model weights. Fit using available development contests and
preserve independent contests for validation."

Every component is a distribution over the ENUMERATED legal universe of a
slate (dk_mech.enumerate_universe), log-linear in lineup-level features, so
its normaliser is exact and nothing is sampled to fit it:

  OPT  the optimizer cluster: p ~ rank^-a over the projection-ranked lineups
       (one parameter, the depth's decay). NEW: a lineup-level rank law, not
       a softmax temperature (s7_beta's knob, rejected as the cause).
  TPL  the template builder: S7's frozen projection + salary-left softmax
       (research/s7_salary.py, kept as the base, not re-tested) PLUS the two
       things S7 graded and never fitted -- the point mass at $0 / $100-200
       left, and the construction templates (captain position, team split,
       kickers and defenses, the quarterback captain's stack, a non-QB
       captain with his quarterback, both quarterbacks).
  CON  the contrarian: projection + salary-left minus lambda x the lineup's
       log popularity (its players' ownership under production's field). The
       sign of lambda is free and reported: a negative one is chalk-seeking.
  CAS  the casual builder: projection + salary-left alone, its own (looser)
       coefficients. This IS S7's family; it is here as the residual every
       other component must beat for its weight, never as a finding.

and the entrant, not the entry, draws the component (the hypothesis S7
recorded and did not build: "a mixture of user-level lineup-generation
strategies rather than IID entries from one universal softmax"):

  M0  production's rule, no fit (softmax, top lineup at 0.2%)
  M1  S7's family refit: one softmax on projection and salary left, iid
  M2  M1 + point mass + templates (TPL alone), iid
  M3  OPT + TPL + CON + CAS, one set of weights, iid entries
  M4  M3 with weights by the entrant's size class (1, 2-19, 20-149, 150), iid
  M5  PRIMARY: M4's components, the ENTRANT drawing one component for all his
      entries, his lineups distinct (a portfolio never repeats itself)

The objective for every fitted model is the contest-balanced log-likelihood
of the exact lineups entered (each development contest weighing the same):
a proper score of the whole joint distribution, so ownership, pairs, salary,
stacks and duplication are all functionals of the one thing fitted and none
can be bought with another. Duplication is never in the objective.

    python3 -m research.dk_mix fit <exports...> [--boards DIR]       # development contests only
    python3 -m research.dk_mix validate <exports...> [--boards DIR]  # the validation contests, once
"""
import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import dk_mech as M      # noqa: E402

DEV = (193391013, 195526287, 195677825, 197912905)
VAL = (198005475, 198150093)
FIT = os.path.join(M.OUT, "mix_fit.json")
VALID = os.path.join(M.OUT, "mix_validation.json")
FEATURES = ("x", "left", "left_0", "left_100_200", "cpt_RB", "cpt_WR", "cpt_TE", "cpt_K", "cpt_DST", "split_4_2", "split_5_1",
            "n_k_dst", "qb_cpt_catchers", "cpt_with_own_qb", "both_qbs", "log_popularity", "neg_log_rank")
_C = {n: i for i, n in enumerate(FEATURES)}
COMPONENTS = {"OPT": ("neg_log_rank",),
              "TPL": FEATURES[:15],
              "CON": ("x", "left", "log_popularity"),
              "CAS": ("x", "left")}
INIT = {"OPT": [1.0], "TPL": [0.4, -0.5] + [0.0] * 13, "CON": [0.2, -0.3, -0.3], "CAS": [0.2, -0.2]}
CLASSES = M.USER_CLASSES
EM_ITERS, EM_TOL = 80, 1e-7
#: acceptance (report section B.5), fixed before the fit
ACCEPT = {"collision_factor": 2.0, "copies_factor": 3.0, "copies_min_of_20": 15,
          "margins": {"captain_mae": 0.25, "flex_mae": 0.5, "joint_mae": 0.25, "salary_mean_abs_err": 100.0,
                      "salary_left_l1": 2.0, "team_split_l1": 2.0, "captain_position_l1": 2.0, "score_w1": 0.5,
                      "copies_hist_l1": 0.0}}


def _cls(m):
    out = np.zeros(len(m), dtype=np.int64)
    for i, (lo, hi) in enumerate(CLASSES):
        out[(m >= lo) & (m <= hi)] = i
    return out


def build(cid, path, boards_dir=None, log=print):
    """One contest as the fit sees it: the universe, its features, and the
    representable entries as universe rows with their entrant."""
    c = M.load(cid, path, boards_dir, log=lambda *_a: None)
    P = c.proj_players
    idx, x, s, rank = M.enumerate_universe(P)
    L = len(idx)
    pos = np.asarray([p["pos"] for p in P])
    team = np.asarray([p.get("team") or "" for p in P])
    f0, beta0 = M.production_field(P, idx)
    oc = np.bincount(idx[:, 0], weights=f0, minlength=len(P))
    of = np.zeros(len(P))
    for k in range(1, 6):
        of += np.bincount(idx[:, k], weights=f0, minlength=len(P))
    F = np.zeros((L, len(FEATURES)), dtype=np.float32)
    left = (M.CAP - s) / 1000.0
    cp, fp = pos[idx[:, 0]], pos[idx[:, 1:]]
    ct, ft = team[idx[:, 0]], team[idx[:, 1:]]
    n0 = (team[idx] == sorted(set(team))[0]).sum(axis=1)
    big = np.maximum(n0, 6 - n0)
    F[:, _C["x"]] = x
    F[:, _C["left"]] = left
    F[:, _C["left_0"]] = left == 0
    F[:, _C["left_100_200"]] = (left > 0) & (left <= 0.2)
    for q in ("RB", "WR", "TE", "K", "DST"):
        F[:, _C["cpt_" + q]] = cp == q
    F[:, _C["split_4_2"]] = big == 4
    F[:, _C["split_5_1"]] = big == 5
    F[:, _C["n_k_dst"]] = np.isin(fp, ("K", "DST")).sum(axis=1)
    F[:, _C["qb_cpt_catchers"]] = (cp == "QB") * (np.isin(fp, ("WR", "TE")) & (ft == ct[:, None])).sum(axis=1)
    F[:, _C["cpt_with_own_qb"]] = (cp != "QB") & ((fp == "QB") & (ft == ct[:, None])).any(axis=1)
    F[:, _C["both_qbs"]] = (pos[idx] == "QB").sum(axis=1) == 2
    F[:, _C["log_popularity"]] = np.log(np.maximum(oc[idx[:, 0]], 1e-3)) + np.log(np.maximum(of[idx[:, 1:]], 1e-3)).sum(axis=1)
    F[:, _C["neg_log_rank"]] = -np.log(rank)
    u = M.to_universe(c, P, idx)
    rep = u >= 0
    m_all = np.bincount(c.users)[c.users]              # the entrant's entries in the contest (all of them)
    _uu, ucode = np.unique(c.users[rep], return_inverse=True)
    d = {"cid": cid, "c": c, "players": P, "idx": idx, "x": x, "s": s, "rank": rank, "F": F, "L": L, "f0": f0, "beta0": beta0,
         "u": u[rep], "rep": rep, "user": ucode, "m": m_all[rep], "N": int(rep.sum()), "N_all": c.N,
         "sizes": np.bincount(np.bincount(c.users))}     # sizes[m] = entrants with m entries
    log(f"[mix] {cid} {c.spec['label']}: {len(P)} players, {L:,} lineups, {d['N']:,} of {c.N:,} entries representable "
        f"({100 * d['N'] / c.N:.1f}%)")
    return d


# ---- log-linear components ----------------------------------------------------
def logp(d, cols, theta):
    z = d["F"][:, [_C[n] for n in cols]].astype(np.float64) @ np.asarray(theta, dtype=np.float64)
    z -= z.max()
    return z - np.log(np.exp(z).sum())


def newton(data, cols, theta, G, steps=1):
    """Weighted maximum likelihood of one log-linear component: G[d] is a
    length-L weight per universe row (the entries, weighted). Concave; damped
    Newton with step halving."""
    ci = [_C[n] for n in cols]
    theta = np.asarray(theta, dtype=np.float64)

    def parts(th):
        obj, g, H = 0.0, np.zeros(len(ci)), np.zeros((len(ci), len(ci)))
        for d, w in zip(data, G):
            Fc = d["F"][:, ci].astype(np.float64)
            z = Fc @ th
            zmax = z.max()
            p = np.exp(z - zmax)
            Z = p.sum()
            p /= Z
            tot = w.sum()
            nz = np.nonzero(w)[0]
            obj += float(w[nz] @ z[nz]) - tot * (zmax + np.log(Z))
            mu = p @ Fc
            g += w[nz] @ Fc[nz] - tot * mu
            H += tot * ((Fc * p[:, None]).T @ Fc - np.outer(mu, mu))
        return obj, g, H
    obj, g, H = parts(theta)
    for _ in range(steps):
        step = np.linalg.solve(H + 1e-9 * np.trace(H) / len(ci) * np.eye(len(ci)) + 1e-12 * np.eye(len(ci)), g)
        t = 1.0
        while t > 1e-4:
            o2, g2, H2 = parts(theta + t * step)
            if o2 >= obj - 1e-12:
                theta, obj, g, H = theta + t * step, o2, g2, H2
                break
            t *= 0.5
        else:
            break
    return theta, obj


def fit_single(data, cols, theta0, iters=40, log=print):
    G = [np.bincount(d["u"], minlength=d["L"]) / d["N"] for d in data]
    theta, prev = np.asarray(theta0, dtype=np.float64), -np.inf
    for _ in range(iters):
        theta, obj = newton(data, cols, theta, G)
        if obj - prev < EM_TOL:
            break
        prev = obj
    return theta, -obj / len(data)


def fit_mixture(data, unit, classes, init=None, log=print, tag=""):
    """EM. `unit` 'entry': every entry draws its component; 'user': the
    entrant draws one for all his entries. `classes`: weights by the
    entrant's size class, else one set."""
    names = list(COMPONENTS)
    K = len(names)
    theta = {k: np.asarray((init or {}).get("theta", INIT)[k], dtype=np.float64) for k in names}
    nC = len(CLASSES) if classes else 1
    W = np.asarray((init or {}).get("weights") or [[1.0 / K] * K] * nC, dtype=np.float64)
    if len(W) != nC:
        W = np.repeat(W[:1], nC, axis=0)
    units = []
    for d in data:
        uid = d["user"] if unit == "user" else np.arange(d["N"])
        nu = int(uid.max()) + 1
        cl = np.zeros(nu, dtype=np.int64)
        if classes:
            cl[uid] = _cls(d["m"])
        units.append((uid, nu, cl))
    prev, trace = -np.inf, []
    for it in range(EM_ITERS):
        ll, G = 0.0, {k: [] for k in names}
        Wn = np.zeros_like(W)
        for d, (uid, nu, cl) in zip(data, units):
            S = np.stack([np.bincount(uid, weights=logp(d, COMPONENTS[k], theta[k])[d["u"]], minlength=nu) for k in names], axis=1)
            A = S + np.log(np.maximum(W[cl], 1e-300))
            mx = A.max(axis=1, keepdims=True)
            lse = mx[:, 0] + np.log(np.exp(A - mx).sum(axis=1))
            R = np.exp(A - lse[:, None])
            ll += float(lse.sum()) / d["N"]
            np.add.at(Wn, cl, R / d["N"])
            for j, k in enumerate(names):
                G[k].append(np.bincount(d["u"], weights=R[uid, j] / d["N"], minlength=d["L"]))
        trace.append(round(-ll / len(data), 6))
        if it and ll - prev < EM_TOL * len(data):
            break
        prev = ll
        W = Wn / Wn.sum(axis=1, keepdims=True)
        for k in names:
            theta[k], _o = newton(data, COMPONENTS[k], theta[k], G[k])
        if it % 10 == 0:
            log(f"[mix] {tag} EM {it}: NLL {trace[-1]:.5f} weights {np.round(W, 3).tolist()}")
    return {"theta": {k: theta[k].tolist() for k in names}, "weights": W.tolist(), "unit": unit, "classes": bool(classes),
            "nll_per_entry": trace[-1], "em_iterations": len(trace), "nll_trace_every_10": trace[::10]}


# ---- a model on a contest ------------------------------------------------------
def nll(d, model):
    """Joint negative log-likelihood per representable entry."""
    kind = model["kind"]
    if kind == "production":
        return float(-np.log(d["f0"][d["u"]]).mean())
    if kind == "single":
        return float(-logp(d, model["cols"], model["theta"])[d["u"]].mean())
    names = list(COMPONENTS)
    W = np.asarray(model["weights"])
    uid = d["user"] if model["unit"] == "user" else np.arange(d["N"])
    nu = int(uid.max()) + 1
    cl = np.zeros(nu, dtype=np.int64)
    if model["classes"]:
        cl[uid] = _cls(d["m"])
    S = np.stack([np.bincount(uid, weights=logp(d, COMPONENTS[k], model["theta"][k])[d["u"]], minlength=nu) for k in names], axis=1)
    A = S + np.log(np.maximum(W[cl], 1e-300))
    mx = A.max(axis=1, keepdims=True)
    return float(-(mx[:, 0] + np.log(np.exp(A - mx).sum(axis=1))).sum() / d["N"])


def expected_counts(d, model, size_share):
    """lam[i] = the expected copies of lineup i among N representable
    entries, and D[i] = the sum over entrants of (his chance of holding i)^2,
    so E[n(n-1)] = lam^2 - D. `size_share[m]`: the share of ENTRIES that come
    from entrants with m entries (the development contests' pooled
    distribution: a validation contest's own is not known before lock)."""
    N = d["N"]
    kind = model["kind"]
    if kind == "production":
        p = d["f0"]
        return N * p, N * p * p
    if kind == "single":
        p = np.exp(logp(d, model["cols"], model["theta"]))
        return N * p, N * p * p
    names = list(COMPONENTS)
    W = np.asarray(model["weights"])
    P = [np.exp(logp(d, COMPONENTS[k], model["theta"][k])) for k in names]
    ms = np.nonzero(size_share)[0]
    cls_of = _cls(ms) if model["classes"] else np.zeros(len(ms), dtype=np.int64)
    lam, D = np.zeros(d["L"]), np.zeros(d["L"])
    if model["unit"] == "entry":
        for m, cl in zip(ms, cls_of):
            p = sum(W[cl, j] * P[j] for j in range(len(names)))
            lam += N * size_share[m] * p
            D += N * size_share[m] * p * p
        return lam, D
    l1 = [np.log1p(-np.minimum(p, 1 - 1e-12)) for p in P]
    for m, cl in zip(ms, cls_of):
        q = sum(W[cl, j] * -np.expm1(m * l1[j]) for j in range(len(names)))    # P(the entrant holds i)
        U = N * size_share[m] / m
        lam += U * q
        D += U * q * q
    return lam, D


def _pois_bucket_entries(lam, buckets, bins=6000):
    """Expected ENTRIES sitting in lineups with lo..hi copies, copies Poisson
    around lam: sum_i lam_i P(Pois(lam_i) in [lo-1, hi-1])."""
    lam = lam[lam > 1e-9]
    edges = np.unique(np.quantile(np.log(lam), np.linspace(0, 1, bins + 1)))
    b = np.clip(np.searchsorted(edges, np.log(lam), side="right") - 1, 0, len(edges) - 2)
    tot = np.bincount(b, weights=lam, minlength=len(edges) - 1)
    cnt = np.bincount(b, minlength=len(edges) - 1)
    mu = tot / np.maximum(cnt, 1)
    kmax = 128                                  # the buckets end at 101+
    ks = np.arange(kmax + 1)
    logfact = np.concatenate([[0.0], np.cumsum(np.log(np.arange(1, kmax + 1)))])
    pmf = np.exp(ks[None, :] * np.log(np.maximum(mu[:, None], 1e-300)) - mu[:, None] - logfact[None, :])
    cdf = np.cumsum(pmf, axis=1)

    def upto(k):
        return cdf[:, k] if k >= 0 else np.zeros(len(mu))
    out = {}
    for lo, hi in buckets:
        pr = (upto(hi - 1) if hi < 10 ** 8 else 1.0) - upto(lo - 2)
        out[M._bname(lo, hi)] = float((tot * pr).sum())
    return out, float((cnt * (1 - np.exp(-mu))).sum())


def _l1(a, b):
    return round(float(sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))), 3)


def evaluate(d, model, size_share, seed=M.SEED):
    """Every acceptance measure for one model on one contest, the real field
    restricted to the representable entries (the support the model has)."""
    c = d["c"]
    N = d["N"]
    lam, D = expected_counts(d, model, size_share)
    cnt = np.bincount(d["u"], minlength=d["L"])
    C_real = float((cnt * (cnt - 1.0)).sum() / (N * (N - 1.0)))
    C_pred = float(((lam ** 2).sum() - D.sum()) / (N * (N - 1.0)))
    hist_m, distinct_m = _pois_bucket_entries(lam, M.COPY_BUCKETS)
    hist_m = {k: round(100 * v / N, 3) for k, v in hist_m.items()}
    per = cnt[d["u"]]
    hist_r = {M._bname(lo, hi): round(100 * float(((per >= lo) & (per <= hi)).mean()), 3) for lo, hi in M.COPY_BUCKETS}
    f = ACCEPT["copies_factor"]
    top_r = np.argsort(-cnt, kind="stable")[:20]
    top_m = np.argsort(-lam, kind="stable")[:20]
    ratio_r = lam[top_r] / cnt[top_r]
    ok_m = (cnt[top_m] > 0) & (lam[top_m] / np.maximum(cnt[top_m], 1e-9) <= f) & (lam[top_m] / np.maximum(cnt[top_m], 1e-9) >= 1 / f)
    # ownership, analytic
    P = len(d["players"])
    p = lam / lam.sum()
    oc_m = np.bincount(d["idx"][:, 0], weights=p, minlength=P)
    of_m = sum(np.bincount(d["idx"][:, k], weights=p, minlength=P) for k in range(1, 6))
    pr = cnt / N
    oc_r = np.bincount(d["idx"][:, 0], weights=pr, minlength=P)
    of_r = sum(np.bincount(d["idx"][:, k], weights=pr, minlength=P) for k in range(1, 6))
    # the rest through dk_mech's own measures, on a sample of the model
    Am, _rows = M.sample_universe(c, d["players"], d["idx"], p, N, seed)
    Ar = c.A[d["rep"]]
    o_r, _c = M.ownership(Ar, "showdown", c.P)
    jid = np.argsort(-o_r, kind="stable")[:20].tolist()
    iu = np.triu_indices(20, 1)
    Jm, Jr = M.joint(Am, c.P, jid), M.joint(Ar, c.P, jid)
    sm, sr = M.structure(c, Am), M.structure(c, Ar)
    scm, scr = np.sort(M.scores(c, Am)), np.sort(M.scores(c, Ar))
    order = np.argsort(-d["x"], kind="stable")
    cum_m, cum_r = np.cumsum(p[order]), np.cumsum(pr[order])
    return {
        "nll_per_entry": round(nll(d, model), 5),
        "collision": {"real": C_real, "model": C_pred, "model_over_real": round(C_pred / C_real, 4)},
        "copies_hist_pct": {"real": hist_r, "model": hist_m, "l1": _l1(hist_r, hist_m)},
        "distinct_lineups": {"real": int((cnt > 0).sum()), "model": round(distinct_m)},
        "real_top20_builds": {"real_copies": cnt[top_r].tolist(), "model_expected": np.round(lam[top_r], 1).tolist(),
                              "within_factor": int(((ratio_r <= f) & (ratio_r >= 1 / f)).sum()),
                              "median_abs_log_ratio": round(float(np.median(np.abs(np.log(ratio_r)))), 3),
                              "geo_mean_ratio": round(float(np.exp(np.log(ratio_r).mean())), 3)},
        "model_top20_builds": {"model_expected": np.round(lam[top_m], 1).tolist(), "real_copies": cnt[top_m].tolist(),
                               "within_factor": int(ok_m.sum())},
        "captain_mae": round(100 * float(np.abs(oc_m - oc_r).mean()), 3), "flex_mae": round(100 * float(np.abs(of_m - of_r).mean()), 3),
        "joint_mae": round(100 * float(np.abs(Jm[iu] - Jr[iu]).mean()), 3),
        "salary_mean": {"real": sr["salary_mean"], "model": sm["salary_mean"]},
        "salary_mean_abs_err": round(abs(sr["salary_mean"] - sm["salary_mean"]), 1),
        "salary_left_pct": {"real": sr["salary_left_pct"], "model": sm["salary_left_pct"]},
        "salary_left_l1": _l1(sr["salary_left_pct"], sm["salary_left_pct"]),
        "team_split_pct": {"real": sr["team_split_pct"], "model": sm["team_split_pct"]},
        "team_split_l1": _l1(sr["team_split_pct"], sm["team_split_pct"]),
        "captain_position_pct": {"real": sr["captain_position_pct"], "model": sm["captain_position_pct"]},
        "captain_position_l1": _l1(sr["captain_position_pct"], sm["captain_position_pct"]),
        "score_w1": round(float(np.abs(scm - scr).mean()), 3),
        "score_mean": {"real": round(float(scr.mean()), 2), "model": round(float(scm.mean()), 2)},
        "top_K_mass_pct": {str(K): {"real": round(100 * float(cum_r[min(K, d["L"]) - 1]), 2), "model": round(100 * float(cum_m[min(K, d["L"]) - 1]), 2)}
                           for K in (1, 10, 100, 1000, 10000)}}


def verdict(ev, base):
    """The acceptance criteria of section B.5 for one model on one contest
    against production's rule on the same universe (`base`)."""
    a = ACCEPT
    r = ev["collision"]["model_over_real"]
    out = {"A1_aggregate_collision_within_2x": bool(1 / a["collision_factor"] <= r <= a["collision_factor"]),
           "A2_real_top20_within_3x": bool(ev["real_top20_builds"]["within_factor"] >= a["copies_min_of_20"]),
           "A2_model_top20_within_3x": bool(ev["model_top20_builds"]["within_factor"] >= a["copies_min_of_20"]),
           "A3_copies_histogram_no_worse": bool(ev["copies_hist_pct"]["l1"] <= base["copies_hist_pct"]["l1"] + a["margins"]["copies_hist_l1"])}
    for k, mg in a["margins"].items():
        if k == "copies_hist_l1":
            continue
        out[f"A4_{k}_non_inferior"] = bool(ev[k] <= base[k] + mg)
    out["duplication_calibrated_here"] = bool(all(out.values()))
    return out


def models_from(fit):
    ms = {"M0": {"kind": "production"},
          "M1": {"kind": "single", "cols": list(COMPONENTS["CAS"]), "theta": fit["M1"]["theta"]},
          "M2": {"kind": "single", "cols": list(COMPONENTS["TPL"]), "theta": fit["M2"]["theta"]}}
    for k in ("M3", "M4", "M5"):
        ms[k] = dict(fit[k], kind="mixture")
    return ms


def _exports(args):
    return M.find_exports(args.paths)


def run_fit(args, log=print):
    ex = _exports(args)
    t0 = time.time()
    data = [build(cid, ex[cid], args.boards, log) for cid in DEV]
    fit = {"development": list(DEV), "features": list(FEATURES), "components": {k: list(v) for k, v in COMPONENTS.items()},
           "classes": [M._bname(lo, hi) for lo, hi in CLASSES], "accept": ACCEPT}
    th, n1 = fit_single(data, COMPONENTS["CAS"], INIT["CAS"])
    fit["M1"] = {"theta": th.tolist(), "nll_per_entry": round(n1, 6)}
    log(f"[mix] M1 NLL {n1:.5f} theta {np.round(th, 4).tolist()}")
    th, n2 = fit_single(data, COMPONENTS["TPL"], INIT["TPL"])
    fit["M2"] = {"theta": th.tolist(), "nll_per_entry": round(n2, 6)}
    log(f"[mix] M2 NLL {n2:.5f}")
    init = {"theta": dict(INIT, TPL=fit["M2"]["theta"], CAS=fit["M1"]["theta"])}
    fit["M3"] = fit_mixture(data, "entry", False, init, log, "M3")
    fit["M4"] = fit_mixture(data, "entry", True, {"theta": fit["M3"]["theta"], "weights": fit["M3"]["weights"]}, log, "M4")
    fit["M5"] = fit_mixture(data, "user", True, {"theta": fit["M4"]["theta"], "weights": fit["M4"]["weights"]}, log, "M5")
    # the pooled distribution of entrant sizes: share of ENTRIES by the entrant's entry count, contest-balanced
    share = np.zeros(151)
    for d in data:
        sz = d["sizes"]
        m = np.arange(len(sz))
        e = (sz * m)[:151].astype(np.float64)
        e[150] += float((sz * m)[151:].sum())
        share += e / e.sum() / len(data)
    fit["size_share"] = share.tolist()
    ms = models_from(fit)
    fit["dev_nll_per_entry"] = {k: {str(d["cid"]): round(nll(d, m), 5) for d in data} for k, m in ms.items()}
    fit["dev_evaluation"] = {}
    for d in data:
        evs = {k: evaluate(d, m, share) for k, m in ms.items()}
        fit["dev_evaluation"][str(d["cid"])] = {k: dict(ev, verdict=verdict(ev, evs["M0"])) for k, ev in evs.items()}
        log(f"[mix] dev {d['cid']}: collision model/real " + ", ".join(f"{k} {ev['collision']['model_over_real']}" for k, ev in evs.items()))
    fit["seconds"] = round(time.time() - t0)
    with open(FIT, "w") as f:
        json.dump(fit, f, indent=1)
    log(f"[mix] wrote {FIT}")
    return fit


def run_validate(args, log=print):
    with open(FIT) as f:
        fit = json.load(f)
    if os.path.exists(VALID) and not args.again:
        raise SystemExit(f"{VALID} exists: the validation contests are read once (--again re-runs the SAME frozen fit)")
    ex = _exports(args)
    ms = models_from(fit)
    share = np.asarray(fit["size_share"])
    out = {"validation": list(VAL), "fit_file": os.path.basename(FIT), "contests": {}}
    for cid in VAL:
        d = build(cid, ex[cid], args.boards, log)
        evs = {k: evaluate(d, m, share) for k, m in ms.items()}
        out["contests"][str(cid)] = {"label": d["c"].spec["label"], "representable_pct": round(100 * d["N"] / d["N_all"], 2),
                                     "entries": d["N"], "legal_lineups": d["L"], "players": len(d["players"]),
                                     "models": {k: dict(ev, verdict=verdict(ev, evs["M0"])) for k, ev in evs.items()}}
        log(f"[mix] validation {cid}: collision model/real " + ", ".join(f"{k} {ev['collision']['model_over_real']}" for k, ev in evs.items()))
    out["duplication_calibrated"] = {k: bool(all(out["contests"][str(c)]["models"][k]["verdict"]["duplication_calibrated_here"] for c in VAL))
                                     for k in ms}
    with open(VALID, "w") as f:
        json.dump(out, f, indent=1)
    log(f"[mix] wrote {VALID}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=("fit", "validate"))
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--boards", default=None)
    ap.add_argument("--again", action="store_true")
    args = ap.parse_args()
    (run_fit if args.mode == "fit" else run_validate)(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
