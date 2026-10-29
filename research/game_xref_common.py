"""Shared scoring for the game cross-reference study (research/reports/game_xref.md).

Pure Python + numpy. Every function takes plain lists so the NFL and college
replays can share one definition of each number:

  * win-probability scores: log loss, Brier, straight-up accuracy, with the
    CLUSTERED standard error of a paired difference (clusters = (season, week),
    because one Sunday's games share weather, officiating and whatever the
    league did that day -- counting 16 games as 16 independent draws is how
    calibrate.py's prop fit went wrong, see its docstring);
  * point estimates: bias (pred - actual), MAE, RMSE and the slope of actual on
    predicted (1.0 = the spread of the predictions is right; < 1 = the model is
    too extreme, > 1 = too compressed), with bootstrap-by-cluster SEs;
  * calibration bins on the favourite's side;
  * ridge logistic / linear stacks fit with closed-form or Newton steps, no sklearn.
"""
import math
import random
from collections import defaultdict

import numpy as np

EPS = 1e-6


def logit(p):
    p = min(1 - EPS, max(EPS, float(p)))
    return math.log(p / (1 - p))


def sigmoid(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def american_to_prob(odds):
    """Implied probability of an American moneyline, vig included."""
    o = float(odds)
    return 100.0 / (o + 100.0) if o > 0 else -o / (-o + 100.0)


def devig(p_a, p_b):
    """Proportional de-vig (predlog.devig's convention): own / (own + opp)."""
    if p_a is None or p_b is None or p_a + p_b <= 0:
        return None
    return p_a / (p_a + p_b)


# ---- win-probability scores ---------------------------------------------------
def ll_terms(ps, ys):
    out = []
    for p, y in zip(ps, ys):
        p = min(1 - EPS, max(EPS, p))
        out.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))
    return out


def brier_terms(ps, ys):
    return [(p - y) ** 2 for p, y in zip(ps, ys)]


def acc_terms(ps, ys):
    """1 when the side at >= 0.5 won (a 0.5 counts as a home pick, like the boards)."""
    return [1.0 if ((p >= 0.5) == (y >= 0.5)) else 0.0 for p, y in zip(ps, ys)]


def cluster_se(terms, clusters):
    """Standard error of the mean of `terms` with observations grouped by
    `clusters` (cluster-robust: the between-cluster variance of cluster sums)."""
    n = len(terms)
    if n == 0:
        return float("nan")
    m = sum(terms) / n
    by = defaultdict(float)
    for t, c in zip(terms, clusters):
        by[c] += t - m
    g = len(by)
    if g < 2:
        return float("nan")
    v = sum(s * s for s in by.values()) * g / (g - 1)
    return math.sqrt(v) / n


def score(ps, ys, clusters=None):
    n = len(ps)
    if n == 0:
        return {"n": 0}
    ll = ll_terms(ps, ys)
    br = brier_terms(ps, ys)
    ac = acc_terms(ps, ys)
    out = {"n": n, "log_loss": round(sum(ll) / n, 4), "brier": round(sum(br) / n, 4),
           "accuracy": round(sum(ac) / n, 4)}
    if clusters is not None:
        out["acc_se"] = round(cluster_se(ac, clusters), 4)
    return out


def paired(ps_a, ps_b, ys, clusters, metric="ll"):
    """Mean per-game difference (a - b) of log loss (negative = a better) or
    accuracy (positive = a better), with the clustered SE."""
    if metric == "ll":
        ta, tb = ll_terms(ps_a, ys), ll_terms(ps_b, ys)
    elif metric == "brier":
        ta, tb = brier_terms(ps_a, ys), brier_terms(ps_b, ys)
    else:
        ta, tb = acc_terms(ps_a, ys), acc_terms(ps_b, ys)
    d = [x - y for x, y in zip(ta, tb)]
    m = sum(d) / len(d)
    return m, cluster_se(d, clusters)


def calibration(ps, ys, width=0.1):
    """Bins on the favourite's side: (bin, n, mean predicted, realized)."""
    bins = defaultdict(lambda: [0, 0.0, 0.0])
    for p, y in zip(ps, ys):
        fp, fy = (p, y) if p >= 0.5 else (1 - p, 1 - y)
        b = min(int(fp / width), int(1 / width) - 1)
        c = bins[b]
        c[0] += 1
        c[1] += fp
        c[2] += fy
    return [(f"{b*width:.2f}-{(b+1)*width:.2f}", c[0], round(c[1] / c[0], 3), round(c[2] / c[0], 3))
            for b, c in sorted(bins.items())]


def disagreements(ps_a, ps_b, ys):
    """Games where the two models pick different sides: how many, and how
    often each was right (the McNemar cells)."""
    n = a_right = b_right = 0
    for pa, pb, y in zip(ps_a, ps_b, ys):
        if (pa >= 0.5) != (pb >= 0.5):
            n += 1
            if (pa >= 0.5) == (y >= 0.5):
                a_right += 1
            else:
                b_right += 1
    return {"n_disagree": n, "a_right": a_right, "b_right": b_right}


# ---- point estimates ----------------------------------------------------------
def point_scores(pred, actual, clusters=None, boot=400, seed=7):
    """bias (pred - actual), MAE, RMSE, slope of actual on pred, and the
    intercept; SEs by cluster bootstrap when clusters are given."""
    x = np.asarray(pred, float)
    y = np.asarray(actual, float)
    n = len(x)
    if n == 0:
        return {"n": 0}

    def _one(xx, yy):
        e = xx - yy
        vx = xx.var()
        slope = float(((xx - xx.mean()) * (yy - yy.mean())).mean() / vx) if vx > 0 else float("nan")
        return (float(e.mean()), float(np.abs(e).mean()), float(np.sqrt((e * e).mean())), slope,
                float(yy.mean() - slope * xx.mean()) if vx > 0 else float("nan"))

    bias, mae, rmse, slope, icpt = _one(x, y)
    out = {"n": n, "bias": round(bias, 2), "mae": round(mae, 2), "rmse": round(rmse, 2),
           "slope": round(slope, 3), "intercept": round(icpt, 2),
           "pred_sd": round(float(x.std()), 2), "actual_sd": round(float(y.std()), 2)}
    if clusters is not None and boot:
        rng = random.Random(seed)
        idx_by = defaultdict(list)
        for i, c in enumerate(clusters):
            idx_by[c].append(i)
        keys = list(idx_by)
        bs = []
        for _ in range(boot):
            pick = [rng.choice(keys) for _ in keys]
            ii = [i for k in pick for i in idx_by[k]]
            bs.append(_one(x[ii], y[ii]))
        bs = np.asarray(bs)
        out["bias_se"] = round(float(bs[:, 0].std()), 2)
        out["mae_se"] = round(float(bs[:, 1].std()), 2)
        out["slope_se"] = round(float(bs[:, 3].std()), 3)
    return out


def paired_mae(pred_a, pred_b, actual, clusters):
    """Mean per-game |a - y| - |b - y| (negative = a closer) with clustered SE."""
    d = [abs(a - y) - abs(b - y) for a, b, y in zip(pred_a, pred_b, actual)]
    return sum(d) / len(d), cluster_se(d, clusters)


# ---- stacks -------------------------------------------------------------------
def ridge_logistic(X, y, lam=1.0, iters=50):
    """L2 logistic regression by Newton's method; the intercept is not
    penalized. X: n x k (no intercept column). Returns (b0, w)."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, k = X.shape
    Xa = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(k + 1)
    R = np.eye(k + 1) * lam
    R[0, 0] = 0.0
    for _ in range(iters):
        z = Xa @ w
        p = 1.0 / (1.0 + np.exp(-z))
        g = Xa.T @ (p - y) + R @ w
        W = p * (1 - p)
        H = (Xa * W[:, None]).T @ Xa + R
        step = np.linalg.solve(H, g)
        w = w - step
        if np.abs(step).max() < 1e-9:
            break
    return float(w[0]), w[1:]


def predict_logistic(b0, w, X):
    X = np.asarray(X, float)
    z = b0 + X @ w
    return list(1.0 / (1.0 + np.exp(-z)))


def ridge_linear(X, y, lam=1.0):
    """L2 linear regression, intercept unpenalized. Returns (b0, w)."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, k = X.shape
    Xa = np.hstack([np.ones((n, 1)), X])
    R = np.eye(k + 1) * lam
    R[0, 0] = 0.0
    w = np.linalg.solve(Xa.T @ Xa + R, Xa.T @ y)
    return float(w[0]), w[1:]


def predict_linear(b0, w, X):
    X = np.asarray(X, float)
    return list(b0 + X @ w)


def fit_temperature(ps, ys, grid=None):
    """Plain temperature (logit / t) that minimises log loss. Returns t."""
    grid = grid or [round(0.5 + 0.02 * i, 2) for i in range(126)]
    best, bt = None, 1.0
    for t in grid:
        q = [sigmoid(logit(p) / t) for p in ps]
        ll = sum(ll_terms(q, ys)) / len(q)
        if best is None or ll < best:
            best, bt = ll, t
    return bt


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)
