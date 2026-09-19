"""S7, third artifact: projection plus ONE linear salary-left feature, pre-registered.

The beta-only family failed in a useful way (research/s7_beta.py): it matches
its one sufficient statistic and still gets the width, the concentration, the
salary behaviour, the captain mix and the ordering wrong, and no beta
transfers to the highest-integrity board. Salary was the largest consistent
out-of-objective miss ($1,280 to $1,640 too much left unspent; 65-72% of the
model's mass leaving $1,000 or more against 20-39% of the real fields), it is
observable before the game, exact on every board (DraftKings salaries do not
change after lock, so the reconstruction caveat does not touch it), one
scalar, and not derived from the contest's outcome.

The model, exactly and nothing else (the reviewer's authorisation of
2026-09-19):

    log w_i = beta * x_i - gamma * s_i

x_i = the lineup's projected points (1.5x on the captain), s_i = salary left
in thousands of dollars. gamma is fitted FREELY: a positive value penalises
leaving salary, and the data are allowed to contradict that expectation. No
captain coefficient, no stack coefficient, no ownership input, no duplication
feature, no salary buckets (choosing cut points after seeing the distributions
would be several degrees of freedom at once; a linear term asks the clean
question first).

Three nested arms on identical support and the same exact-lineup-count
likelihood: projection only (the beta-only model), salary only, projection
plus salary.

PRE-REGISTERED PRINCIPAL CRITERION: the held-out change in per-entry NLL of
projection+salary relative to projection-only, on a board whose counts did not
fit gamma -- the six single-board directions and leave-one-contest-out with
entry-weighted and contest-balanced pooling. Not evidence: the sign of gamma,
the salary mean matching (both sufficient statistics match at each board's
own MLE by construction; those are checksums), and the in-sample likelihood
gain (a nested model cannot lose in sample).

Over-identifying diagnostics, graded after the fit: the variance of x, the
variance of s, their covariance, the whole salary-left distribution (share at
$0, under $200, under $500, $1,000 or more, $2,000 or more, the quantiles),
duplication, captain position mix, team split, K/DST slots. The Hessian at
the MLE (the model covariance of the sufficient statistics) gives the
parameter correlation and condition number: projection and salary are related
through DraftKings' pricing, and the question is whether beta and gamma are
separately identified or trading off. Nominal standard errors are reported
and flagged optimistic (entries are not independent draws: one user can hold
150).

Ordering may now move: the utility beta*x - gamma*s reorders lineups, so the
Spearman, the real chalk's rank, the winner's rank (diagnostic only), the
observed mass in the model's top 10 / 100 / 1,000, and the composition of the
model's top 1,000 are computed per fit, not once.

Two additions to the reviewer's design, stated before the run: a user-cluster
bootstrap standard error on the held-out delta-NLL (entries grouped by the
DraftKings user who submitted them, users resampled), because the naive
per-entry standard error ignores multi-entry users; and the salary-left
profile of the entries OUTSIDE the representable support, so the exclusion's
effect on the salary feature is visible.

Nothing here changes production. Three contests can identify a defect and
compare simple families; they cannot certify money EV. What a winning arm
earns is "preferred research field family for prospective validation":
capture upcoming Showdown contests before lock and score the frozen model
afterward.

    python3 -m research.s7_salary
"""
import collections
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import s7_beta as SB     # noqa: E402  (the lifted universe, the support, the parsers)
from research import s7_field as F     # noqa: E402

DATA = F.DATA
CONTESTS = F.CONTESTS
SEED = F.SEED
CAP, CPT_MULT = F.CAP, F.CPT_MULT
INTEGRITY = SB.INTEGRITY
ARMS = {"projection_only": (True, False), "salary_only": (False, True), "projection_plus_salary": (True, True)}
PRINCIPAL = ("held-out delta-NLL per entry of projection+salary relative to projection-only, on a board whose counts did "
             "not fit gamma: six single-board directions and leave-one-contest-out with entry-weighted and contest-balanced pooling")
NOT_EVIDENCE = ("the sign of gamma", "the salary mean matching at a board's own MLE (a checksum: both sufficient statistics match by construction)",
                "the in-sample likelihood gain (a nested model cannot lose in sample)")
BOOT = 1000


# ---- the board with two sufficient statistics --------------------------------
class Board2:
    """One contest's representable support with t = (x, -s): the natural
    parameter is theta = (beta, gamma) and log w = theta . t."""

    def __init__(self, cid, x, s, rows, n, N_active):
        self.cid = cid
        self.x = x - x.max()
        self.x_max = float(x.max())
        self.s = s
        self.rows, self.n = rows, n
        self.N = float(n.sum())
        self.N_active = N_active
        w = n / self.N
        self.xbar = float((self.x[rows] * w).sum())
        self.sbar = float((self.s[rows] * w).sum())
        self.tbar = np.array([self.xbar, -self.sbar])
        xo, so = self.x[rows], self.s[rows]
        self.obs_var_x = float((w * xo ** 2).sum() - self.xbar ** 2)
        self.obs_var_s = float((w * so ** 2).sum() - self.sbar ** 2)
        self.obs_cov_xs = float((w * xo * so).sum() - self.xbar * self.sbar)

    def logw(self, theta):
        return theta[0] * self.x - theta[1] * self.s

    def probs(self, theta):
        lw = self.logw(theta)
        lw = lw - lw.max()
        w = np.exp(lw)
        return w / w.sum()

    def logp(self, theta):
        lw = self.logw(theta)
        m = lw.max()
        return lw - (m + math.log(float(np.exp(lw - m).sum())))

    def moments(self, theta):
        """Mean and covariance of t = (x, -s) under the model."""
        p = self.probs(theta)
        mx, ms = float((p * self.x).sum()), float((p * self.s).sum())
        vx = float((p * self.x ** 2).sum()) - mx * mx
        vs = float((p * self.s ** 2).sum()) - ms * ms
        cxs = float((p * self.x * self.s).sum()) - mx * ms
        mean = np.array([mx, -ms])
        cov = np.array([[vx, -cxs], [-cxs, vs]])
        return mean, cov

    def nll(self, theta):
        return float(-(self.n * self.logp(theta)[self.rows]).sum() / self.N)

    def saturated(self):
        q = self.n / self.N
        return float(-(q * np.log(q)).sum())


def fit(boards, weights, free, tol=1e-9, max_iter=300):
    """Newton with backtracking on the weighted per-entry NLL, which is convex
    in theta (a log-partition function). Gradient = weighted sum of moment
    residuals E_theta[t] - tbar; Hessian = weighted sum of model covariances.
    `free` masks the parameters an arm fits; the other stays at zero."""
    theta = np.zeros(2)
    fi = [i for i, f in enumerate(free) if f]
    total_w = float(sum(weights))

    def objective(th):
        return sum(w * B.nll(th) for B, w in zip(boards, weights)) / total_w
    f0 = objective(theta)
    it = 0
    gmax = None
    for it in range(1, max_iter + 1):
        g = np.zeros(2); H = np.zeros((2, 2))
        for B, w in zip(boards, weights):
            m, C = B.moments(theta)
            g += (w / total_w) * (m - B.tbar); H += (w / total_w) * C
        gf, Hf = g[fi], H[np.ix_(fi, fi)]
        gmax = float(np.abs(gf).max())
        if gmax < tol:
            break
        step = np.linalg.solve(Hf, gf)
        t = 1.0
        while True:
            th = theta.copy(); th[fi] -= t * step
            f1 = objective(th)
            if f1 <= f0 - 1e-4 * t * float(gf @ step) or t < 1e-8:
                break
            t *= 0.5
        theta, f0 = th, f1
    return theta, {"iterations": it, "max_abs_score": gmax, "converged": bool(gmax is not None and gmax < tol)}


def hessian_report(B, theta, free):
    """The observed information at the MLE is N x Cov_theta[t]; its inverse
    is the nominal parameter covariance. Reported: the correlation of
    (beta_hat, gamma_hat), the condition number of the covariance of the
    sufficient statistics, and the nominal standard errors -- optimistic,
    because entries are not independent draws."""
    _, C = B.moments(theta)
    fi = [i for i, f in enumerate(free) if f]
    Cf = C[np.ix_(fi, fi)]
    ev = np.linalg.eigvalsh(Cf)
    info = B.N * Cf
    cov = np.linalg.inv(info)
    se = np.sqrt(np.diag(cov))
    out = {"sufficient_statistic_covariance": {"var_x": round(float(C[0, 0]), 5), "var_s": round(float(C[1, 1]), 5), "cov_x_minus_s": round(float(C[0, 1]), 5)},
           "condition_number": round(float(ev.max() / ev.min()), 3) if ev.min() > 0 else None,
           "nominal_se": {name: round(float(se[k]), 6) for k, name in enumerate([n for n, f in zip(("beta", "gamma"), free) if f])},
           "caveat": "nominal: treats every entry as an independent draw; one user can hold 150 entries, so these understate the uncertainty"}
    if len(fi) == 2:
        out["parameter_correlation"] = round(float(cov[0, 1] / (se[0] * se[1])), 5)
        out["model_correlation_x_s"] = round(float(-C[0, 1] / math.sqrt(C[0, 0] * C[1, 1])), 5)
    return out


# ---- support and entries --------------------------------------------------------
def salary_left(players, idx):
    sal = np.asarray([int(p["salary"]) for p in players]); csal = np.asarray([int(p["cpt_salary"]) for p in players])
    return (CAP - (csal[idx[:, 0]] + sal[idx[:, 1:]].sum(axis=1))) / 1000.0


def entry_rows(std, players, idx):
    """(universe row, user) for every representable active entry, in export
    order; the user-cluster bootstrap needs the entry level, not the counts."""
    P = len(players)
    name_idx = {p["name"]: i for i, p in enumerate(players)}
    keys = SB._encode(idx, P)
    order = np.argsort(keys); sk = keys[order]
    rows, users = [], []
    for e in std["entries"]:
        if e["lineup"] is None:
            continue
        c, fl = e["lineup"]
        if c not in name_idx or any(nm not in name_idx for nm in fl):
            continue
        k = name_idx[c]
        for v in sorted(name_idx[nm] for nm in fl):
            k = k * P + v
        pos = int(np.searchsorted(sk, k))
        if pos < len(sk) and sk[pos] == k:
            rows.append(int(order[pos])); users.append(e["user"])
    return np.asarray(rows, dtype=np.int64), users


def excluded_salary_profile(std, pool, players):
    """Salary left for the entries outside the representable support (every
    DraftKings pool player has a salary, projected or not) beside the
    representable ones: does the exclusion bias the salary feature?"""
    names = {p["name"] for p in players}
    inside, outside = [], []
    for e in std["entries"]:
        if e["lineup"] is None:
            continue
        c, fl = e["lineup"]
        if any(nm not in pool for nm in [c] + fl):
            continue
        left = (CAP - pool[c]["cpt_salary"] - sum(pool[nm]["salary"] for nm in fl)) / 1000.0
        (inside if (c in names and all(nm in names for nm in fl)) else outside).append(left)

    def prof(v):
        v = np.asarray(v, dtype=np.float64)
        if not len(v):
            return None
        return {"entries": int(len(v)), "mean_k": round(float(v.mean()), 4), "median_k": round(float(np.median(v)), 4),
                "share_ge_1000_pct": round(100 * float((v >= 1.0).mean()), 2), "share_at_zero_pct": round(100 * float((v == 0).mean()), 2)}
    return {"representable": prof(inside), "excluded": prof(outside)}


# ---- grading ----------------------------------------------------------------------
SAL_EDGES = ((0.0, 0.0), (0.0, 0.2), (0.2, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 1e9))


def _wq(values, weights, qs):
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cw = np.cumsum(w) / w.sum()
    return [round(float(v[min(len(v) - 1, int(np.searchsorted(cw, q)))]), 4) for q in qs]


def _dist(keys, w):
    d = collections.defaultdict(float)
    for k_, v in zip(keys, w):
        d[str(k_)] += float(v)
    return {k_: round(100 * v, 2) for k_, v in sorted(d.items())}


def _l1(a, b):
    return round(sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b)), 2)


def salary_distribution(s, w):
    out = {}
    for lo, hi in SAL_EDGES:
        m = (s == 0.0) if (lo == 0.0 and hi == 0.0) else ((s > lo) & (s <= hi))
        key = "exactly_0" if (lo == 0.0 and hi == 0.0) else f"{int(lo * 1000)}-{int(hi * 1000) if hi < 1e9 else 'up'}"
        out[key] = round(100 * float(w[m].sum()), 2)
    out["ge_1000_pct"] = round(100 * float(w[s >= 1.0].sum()), 2)
    out["ge_2000_pct"] = round(100 * float(w[s >= 2.0].sum()), 2)
    out["mean_dollars"] = round(1000 * float((w * s).sum()), 1)
    out["median_and_p10_p25_p75_p90_dollars"] = [round(1000 * q, 0) for q in _wq(s, w, [0.5, 0.1, 0.25, 0.75, 0.9])]
    return out


def composition(rows_sel, players, idx, s, x):
    pos = np.asarray([q["pos"] for q in players]); team = np.asarray([q["team"] for q in players])
    teams = sorted(set(team))
    a = (team[idx[rows_sel]] == teams[0]).sum(axis=1)
    struct = ["-".join(str(v) for v in sorted((int(k), 6 - int(k)), reverse=True)) for k in a]
    kd = np.isin(pos[idx[rows_sel]], ("K", "DST")).sum(axis=1)
    n = len(rows_sel)
    return {"lineups": int(n),
            "cpt_position_mix_pct": {k: round(100 * v / n, 2) for k, v in sorted(collections.Counter(pos[idx[rows_sel, 0]].tolist()).items())},
            "structure_pct": {k: round(100 * v / n, 2) for k, v in sorted(collections.Counter(struct).items())},
            "k_dst_slots_pct": {str(k): round(100 * v / n, 2) for k, v in sorted(collections.Counter(kd.tolist()).items())},
            "mean_salary_left_dollars": round(1000 * float(s[rows_sel].mean()), 1),
            "mean_projection": round(float(x[rows_sel].mean()), 3)}


def grade(B, theta, arm, players, idx, pool, std, full=False):
    p = B.probs(theta)
    mean, C = B.moments(theta)
    N = B.N
    logp = B.logp(theta)
    nll = float(-(B.n * logp[B.rows]).sum() / N)
    lam = N * p
    order = np.argsort(-p, kind="stable")
    cum = np.cumsum(p[order])
    at = {int(r): i for i, r in enumerate(B.rows.tolist())}
    obs_w = np.zeros(len(p)); obs_w[B.rows] = B.n / N
    top = {k: set(order[:k].tolist()) for k in (10, 100, 1000, 10000)}
    obs_in = {str(k): round(100 * float(sum(B.n[at[r]] for r in top[k] if r in at) / N), 3) for k in top}
    model_in = {str(k): round(100 * float(cum[k - 1]), 3) for k in top}
    exp_dup = SB._duplication_expectation(lam)
    dup = {}
    for lo, hi in F.DUP_BUCKETS:
        key = f"{lo}-{hi if hi < 10 ** 9 else 'plus'}"
        m = (B.n >= lo) & (B.n <= hi)
        dup[key] = {"model_entry_share_pct": round(100 * exp_dup[key][1] / N, 2), "observed_entry_share_pct": round(100 * float(B.n[m].sum()) / N, 2)}
    # ordering under THIS utility (it moves with gamma)
    u = B.logw(theta)
    rk = np.argsort(np.argsort(-u, kind="stable"))
    obs_rank = np.argsort(np.argsort(-B.n))
    top_obs = int(B.rows[int(np.argmax(B.n))])
    sp = float(np.corrcoef(obs_rank, rk[B.rows])[0, 1]) if len(B.rows) > 2 else None
    out = {"arm": arm, "theta": {"beta": round(float(theta[0]), 5), "gamma": round(float(theta[1]), 5)},
           "likelihood": {"nll_per_entry_nats": round(nll, 5),
                          "moment_residual_x": round(float(mean[0] - B.tbar[0]), 6),
                          "moment_residual_s": round(float(-mean[1] - B.sbar), 6),
                          "model_mean_x": round(float(mean[0]) + B.x_max, 4), "observed_mean_x": round(B.xbar + B.x_max, 4),
                          "model_mean_salary_left_dollars": round(-1000 * float(mean[1]), 1), "observed_mean_salary_left_dollars": round(1000 * B.sbar, 1)},
           "second_moments": {"var_x": {"model": round(float(C[0, 0]), 4), "observed": round(B.obs_var_x, 4),
                                        "ratio_observed_over_model": round(B.obs_var_x / float(C[0, 0]), 4) if C[0, 0] > 0 else None},
                              "var_s": {"model": round(float(C[1, 1]), 5), "observed": round(B.obs_var_s, 5),
                                        "ratio_observed_over_model": round(B.obs_var_s / float(C[1, 1]), 4) if C[1, 1] > 0 else None},
                              "cov_x_s": {"model": round(float(-C[0, 1]), 5), "observed": round(B.obs_cov_xs, 5)},
                              "corr_x_s": {"model": round(float(-C[0, 1] / math.sqrt(C[0, 0] * C[1, 1])), 4) if C[0, 0] > 0 and C[1, 1] > 0 else None,
                                           "observed": round(B.obs_cov_xs / math.sqrt(B.obs_var_x * B.obs_var_s), 4) if B.obs_var_x > 0 and B.obs_var_s > 0 else None}},
           "shape": {"max_share_pct": {"model": round(100 * float(p.max()), 4), "observed": round(100 * float(B.n.max()) / N, 4)},
                     "effective_lineups_inverse_sum_p2": {"model": round(float(1.0 / (p ** 2).sum()), 1), "observed": round(float(1.0 / ((B.n / N) ** 2).sum()), 1)},
                     "distinct_lineups": {"model_expected": round(float((1.0 - np.exp(-lam)).sum()), 1), "observed": int(len(B.rows))},
                     "duplication": dup},
           "salary_left": {"model": salary_distribution(B.s, p), "observed": salary_distribution(B.s, obs_w)},
           "ordering": {"spearman_observed_count_vs_utility": round(sp, 4) if sp is not None else None,
                        "real_chalk_model_rank": int(rk[top_obs]) + 1,
                        "model_top_lineup_observed_copies": int(B.n[at[int(order[0])]]) if int(order[0]) in at else 0,
                        "observed_mass_in_model_top_k_pct": obs_in, "model_mass_in_top_k_pct": model_in}}
    if full:
        pos = np.asarray([q["pos"] for q in players]); team = np.asarray([q["team"] for q in players])
        teams = sorted(set(team))
        a = (team[idx] == teams[0]).sum(axis=1)
        struct_key = np.asarray(["-".join(str(v) for v in sorted((k, 6 - k), reverse=True)) for k in range(7)])[a]
        kd = np.isin(pos[idx], ("K", "DST")).sum(axis=1)
        cpos = pos[idx[:, 0]]
        mc, oc = _dist(cpos, p), _dist(cpos, obs_w)
        ms, os_ = _dist(struct_key, p), _dist(struct_key, obs_w)
        mk, ok = _dist(kd, p), _dist(kd, obs_w)
        top_obs_rows = B.rows[np.argsort(-B.n, kind="stable")[:1000]]
        best = max(e["points"] for e in std["entries"])
        w_ = next(e for e in std["entries"] if e["points"] == best and e["lineup"])
        name_idx = {q["name"]: i for i, q in enumerate(players)}
        win = None
        if all(nm in name_idx for nm in [w_["lineup"][0]] + w_["lineup"][1]):
            key = (name_idx[w_["lineup"][0]], tuple(sorted(name_idx[nm] for nm in w_["lineup"][1])))
            hit = [r for r in np.where(idx[:, 0] == key[0])[0] if tuple(sorted(int(v) for v in idx[r, 1:])) == key[1]]
            if hit:
                win = {"model_rank": int(rk[int(hit[0])]) + 1, "points": best, "diagnostic_only": True}
        out["out_of_objective"] = {"cpt_position_mix_pct": {"model": mc, "observed": oc, "l1_pp": _l1(mc, oc)},
                                   "structure_pct": {"model": ms, "observed": os_, "l1_pp": _l1(ms, os_)},
                                   "k_dst_slots_pct": {"model": mk, "observed": ok, "l1_pp": _l1(mk, ok)},
                                   "salary_left_share_ge_1000_pct": {"model": out["salary_left"]["model"]["ge_1000_pct"], "observed": out["salary_left"]["observed"]["ge_1000_pct"]}}
        out["top_1000_composition"] = {"model_top_1000_by_utility": composition(order[:1000], players, idx, B.s, B.x + B.x_max),
                                       "observed_1000_most_duplicated": composition(top_obs_rows, players, idx, B.s, B.x + B.x_max)}
        out["ordering"]["winner"] = win
    return out


def rank_bins(B, theta):
    """Fixed bins of THIS utility's order (the order moves with gamma, so the
    bins are per fit): observed mass and model mass per bin."""
    u = B.logw(theta)
    order = np.argsort(-u, kind="stable")
    rank = np.empty(len(u), dtype=np.int64); rank[order] = np.arange(1, len(u) + 1)
    obs = np.zeros(len(u)); obs[B.rows] = B.n / B.N
    p = B.probs(theta)
    out = []
    for lo, hi in SB.RANK_EDGES:
        m = (rank >= lo) & ((rank <= hi) if hi else np.ones(len(u), dtype=bool))
        if m.any():
            out.append({"rank": f"{lo}-{hi or 'end'}", "observed_pct": round(100 * float(obs[m].sum()), 3), "model_pct": round(100 * float(p[m].sum()), 3)})
    return out


# ---- the run -------------------------------------------------------------------------
def build(cid, spec, log=print):
    bd = SB.build(cid, spec, log)
    s = salary_left(bd["players"], bd["idx"])
    B = bd["B"]
    B2 = Board2(cid, B.x + B.x_max, s, B.rows, B.n, bd["support"]["active_entries"])
    erows, users = entry_rows(bd["std"], bd["players"], bd["idx"])
    assert len(erows) == int(B2.N), (len(erows), B2.N)
    bd.update({"B2": B2, "s": s, "entry_rows": erows, "entry_users": users,
               "excluded_salary": excluded_salary_profile(bd["std"], bd["pool"], bd["players"])})
    log(f"[S7s] {cid}: salary left observed mean ${1000 * B2.sbar:,.0f}, universe mean ${1000 * float(s.mean()):,.0f}; "
        f"excluded entries {bd['excluded_salary']['excluded']}")
    return bd


def cluster_bootstrap(delta, users, rng, reps=BOOT):
    """SE of the mean of per-entry deltas with users resampled (each user's
    entries move together), beside the naive per-entry SE."""
    by = collections.defaultdict(lambda: [0.0, 0])
    for d, u in zip(delta.tolist(), users):
        by[u][0] += d; by[u][1] += 1
    sums = np.asarray([v[0] for v in by.values()]); cnts = np.asarray([v[1] for v in by.values()], dtype=np.float64)
    U = len(sums)
    draws = np.empty(reps)
    for r in range(reps):
        pick = rng.integers(0, U, U)
        draws[r] = sums[pick].sum() / cnts[pick].sum()
    return {"mean": round(float(delta.mean()), 5), "se_user_cluster": round(float(draws.std(ddof=1)), 5),
            "se_naive_per_entry": round(float(delta.std(ddof=1) / math.sqrt(len(delta))), 5),
            "users": int(U), "entries": int(len(delta)), "replicates": reps,
            "ci95_user_cluster": [round(float(np.percentile(draws, 2.5)), 5), round(float(np.percentile(draws, 97.5)), 5)]}


def run(log=print):
    from research import provenance, s6_capture
    rng = np.random.default_rng(SEED)
    boards = {cid: build(cid, spec, log) for cid, spec in CONTESTS.items()}
    ids = list(CONTESTS)
    fits = {arm: {} for arm in ARMS}
    for arm, free in ARMS.items():
        for cid in ids:
            th, info = fit([boards[cid]["B2"]], [1.0], free)
            fits[arm][("own", cid)] = (th, info)
            log(f"[S7s] {cid} {arm}: beta {th[0]:.4f} gamma {th[1]:.4f} ({info['iterations']} it, score {info['max_abs_score']:.1e})")
        for cid in ids:
            others = [c for c in ids if c != cid]
            for wk, wts in (("entry_weighted", [boards[c]["B2"].N for c in others]), ("contest_balanced", [1.0] * len(others))):
                th, info = fit([boards[c]["B2"] for c in others], wts, free)
                fits[arm][("loo", cid, wk)] = (th, info, others)
        for wk, wts in (("entry_weighted", [boards[c]["B2"].N for c in ids]), ("contest_balanced", [1.0] * len(ids))):
            th, info = fit([boards[c]["B2"] for c in ids], wts, free)
            fits[arm][("pooled", wk)] = (th, info)
    contests = {}
    principal = []
    for cid in ids:
        bd = boards[cid]; B = bd["B2"]
        graded = {}
        for arm in ARMS:
            cand = {"own_mle": fits[arm][("own", cid)][0],
                    **{f"transfer_from_{c}": fits[arm][("own", c)][0] for c in ids if c != cid},
                    "loo_entry_weighted": fits[arm][("loo", cid, "entry_weighted")][0],
                    "loo_contest_balanced": fits[arm][("loo", cid, "contest_balanced")][0],
                    "pooled_all_entry_weighted": fits[arm][("pooled", "entry_weighted")][0],
                    "pooled_all_contest_balanced": fits[arm][("pooled", "contest_balanced")][0]}
            graded[arm] = {k: grade(B, th, arm, bd["players"], bd["idx"], bd["pool"], bd["std"], full=(k in ("own_mle", "loo_entry_weighted")))
                           for k, th in cand.items()}
            graded[arm]["own_mle"]["hessian"] = hessian_report(B, cand["own_mle"], ARMS[arm])
            graded[arm]["own_mle"]["fit"] = fits[arm][("own", cid)][1]
            graded[arm]["own_mle"]["rank_bins_by_this_utility"] = rank_bins(B, cand["own_mle"])
            graded[arm]["loo_entry_weighted"]["rank_bins_by_this_utility"] = rank_bins(B, cand["loo_entry_weighted"])
        # the principal criterion: held-out delta-NLL, projection+salary minus projection-only, same theta source
        held = {}
        for k in ("transfer_from_%s" % c for c in ids if c != cid):
            held[k] = round(graded["projection_plus_salary"][k]["likelihood"]["nll_per_entry_nats"] - graded["projection_only"][k]["likelihood"]["nll_per_entry_nats"], 5)
        for k in ("loo_entry_weighted", "loo_contest_balanced"):
            held[k] = round(graded["projection_plus_salary"][k]["likelihood"]["nll_per_entry_nats"] - graded["projection_only"][k]["likelihood"]["nll_per_entry_nats"], 5)
        th_ps = fits["projection_plus_salary"][("loo", cid, "entry_weighted")][0]
        th_p = fits["projection_only"][("loo", cid, "entry_weighted")][0]
        delta = (B.logp(th_ps) - B.logp(th_p))[bd["entry_rows"]]        # log p gain per entry: positive = salary helps
        boot = cluster_bootstrap(delta, bd["entry_users"], rng)
        th_s = fits["salary_only"][("loo", cid, "entry_weighted")][0]
        in_sample = round(graded["projection_plus_salary"]["own_mle"]["likelihood"]["nll_per_entry_nats"] - graded["projection_only"]["own_mle"]["likelihood"]["nll_per_entry_nats"], 5)
        principal.append({"held_out": cid, "label": CONTESTS[cid]["label"], "integrity": INTEGRITY[cid],
                          "representable_pct": bd["support"]["representable_pct"],
                          "delta_nll_projection_plus_salary_minus_projection_only": held,
                          "in_sample_delta_at_own_mle_not_evidence": in_sample,
                          "loo_entry_weighted_user_cluster_bootstrap": {**boot, "sign": "positive = projection+salary assigns higher probability to the held-out entries"},
                          "salary_only_minus_projection_only_loo_entry_weighted": round(graded["salary_only"]["loo_entry_weighted"]["likelihood"]["nll_per_entry_nats"] - graded["projection_only"]["loo_entry_weighted"]["likelihood"]["nll_per_entry_nats"], 5),
                          "nll_loo_entry_weighted": {arm: graded[arm]["loo_entry_weighted"]["likelihood"]["nll_per_entry_nats"] for arm in ARMS},
                          "theta_loo_entry_weighted": {"projection_only": [round(float(v), 5) for v in th_p], "salary_only": [round(float(v), 5) for v in th_s],
                                                       "projection_plus_salary": [round(float(v), 5) for v in th_ps]}})
        contests[str(cid)] = {"label": CONTESTS[cid]["label"], "integrity": INTEGRITY[cid], "support": bd["support"],
                              "excluded_entries_salary_profile": bd["excluded_salary"],
                              "universe": {"players": len(bd["players"]), "legal_lineups": int(len(bd["idx"])),
                                           "salary_left_universe_mean_dollars": round(1000 * float(bd["s"].mean()), 1),
                                           "salary_left_observed_mean_dollars": round(1000 * B.sbar, 1)},
                              "saturated_nll_per_entry_nats": round(B.saturated(), 5), "uniform_nll_per_entry_nats": round(math.log(len(bd["idx"])), 5),
                              "inputs": {"draftkings": s6_capture.dk_hashes(CONTESTS[cid]["dg"])},
                              "graded": graded}
    fits_out = {arm: {"own": {str(c): {"theta": [round(float(v), 5) for v in fits[arm][("own", c)][0]], **fits[arm][("own", c)][1]} for c in ids},
                      "leave_one_out": {str(c): {wk: {"theta": [round(float(v), 5) for v in fits[arm][("loo", c, wk)][0]], "fitted_on": fits[arm][("loo", c, wk)][2], **fits[arm][("loo", c, wk)][1]}
                                                 for wk in ("entry_weighted", "contest_balanced")} for c in ids},
                      "pooled_all": {wk: {"theta": [round(float(v), 5) for v in fits[arm][("pooled", wk)][0]], **fits[arm][("pooled", wk)][1]} for wk in ("entry_weighted", "contest_balanced")}}
                for arm in ARMS}
    out = {"meta": {"stage": "S7 third artifact: projection plus one linear salary-left feature, three nested arms, pre-registered",
                    "model": "log w = beta * x - gamma * s; x = projected points (1.5x captain), s = salary left / 1000; gamma free in sign; nothing else",
                    "arms": {arm: {"beta_free": f[0], "gamma_free": f[1]} for arm, f in ARMS.items()},
                    "principal_criterion": PRINCIPAL, "not_evidence": list(NOT_EVIDENCE),
                    "additions_stated_before_the_run": ["user-cluster bootstrap SE on the held-out delta-NLL (users resampled, entries move with their user)",
                                                        "salary-left profile of the entries outside the representable support"],
                    "contests": {str(cid): spec["label"] for cid, spec in CONTESTS.items()},
                    "inputs": {"sleeper_feeds": s6_capture.sleeper_hashes(F.FEEDS), "beta_artifact": "research/data/s7_beta.json"},
                    "production_unchanged": True,
                    "provenance": provenance.stamp(worlds=None, model="softmax(beta x - gamma s) over the lifted universe; theta by maximum likelihood (Newton)", seed=SEED)},
           "principal": principal, "fits": fits_out, "contests": contests}
    with open(os.path.join(DATA, "s7_salary.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    run()
    print("S7 salary artifact written")
