"""Probability calibration, learned from Vigil's own graded history.

Both the game win model and the batter-prop model score well up to ~60% but run
OVERCONFIDENT on the high end — measured on the track record: the game model says
89% and hits 67%; batter props say 83% and hit 72%. That overconfidence is money
lost twice over: it inflates the apparent edge on chalk (so you overbet it) and it
makes a "95%" combo leg look far safer than it is.

The fix is TEMPERATURE SCALING: divide the log-odds by T. With T>1 this shrinks a
probability toward 50% — and, because it works in log-odds space, it barely moves
a well-calibrated 0.53 while pulling an overconfident 0.89 down hard. Exactly the
shape of the problem.

We don't hardcode T — we FIT it on the model's own graded results, so it self-
corrects as more games settle. And we REGULARIZE it toward 1.0 (no change) by
sample size, so a thin or noisy history can never distort the model: with little
data T stays near 1; only a large, consistent sample earns a real correction.
Everything is cached and fails safe to a no-op.
"""
import math
import time as _t


def _logit(p):
    p = min(1 - 1e-9, max(1e-9, p))
    return math.log(p / (1 - p))


def _sigmoid(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def scale(p, t):
    """Temperature-scale one probability. t>1 reins it in toward 0.5."""
    if p is None or t is None or t == 1.0:
        return p
    return _sigmoid(_logit(p) / t)


def _fit(pairs, n_floor):
    """Grid-search the temperature that best calibrates (min Brier) the graded
    (prob, outcome) pairs, then shrink it toward 1.0 by how much data backs it.
    Returns T (>=~1 when the model is overconfident, the usual case)."""
    pairs = [(float(p), 1.0 if o else 0.0) for p, o in pairs
             if p is not None and o is not None and 0.0 < float(p) < 1.0]
    n = len(pairs)
    if n < 25:                       # too little to say anything — leave it alone
        return 1.0
    logits = [(_logit(p), o) for p, o in pairs]
    best_t, best_b = 1.0, None
    t = 0.90                         # allow a mild sharpen, but the data decides
    while t <= 3.0001:
        b = 0.0
        for lg, o in logits:
            b += (_sigmoid(lg / t) - o) ** 2
        b /= n
        if best_b is None or b < best_b:
            best_b, best_t = b, t
        t = round(t + 0.05, 2)
    # Regularize toward 1.0 (no-op) until the sample is large enough to trust.
    w = min(1.0, n / float(n_floor))
    return round(1.0 + (best_t - 1.0) * w, 3)


_cache = {}          # key -> (temperature, fitted_at, n)
_TTL = 6 * 3600      # refit a few times a day; graded history changes slowly


def _temp(key, loader, n_floor):
    hit = _cache.get(key)
    if hit and _t.time() - hit[1] < _TTL:
        return hit[0]
    t, n = 1.0, 0
    try:
        pairs = loader()
        n = len(pairs)
        t = _fit(pairs, n_floor)
    except Exception:
        t = 1.0
    _cache[key] = (t, _t.time(), n)
    return t


# Sample floors: the prop model has tens of thousands of graded outcomes, so it
# earns full trust quickly; the game model settles far fewer, so it needs more
# before a big correction (until then it stays gentle).
def win_prob(p):
    import store
    return scale(p, _temp("win", store.win_grade_pairs, 200))


def batter_prop(p):
    import store
    return scale(p, _temp("prop", store.prop_grade_pairs, 800))


def temps():
    """{win_t, win_n, prop_t, prop_n} for the diagnostics header."""
    win_prob(0.5)            # ensure both temperatures are computed + cached
    batter_prop(0.5)
    w = _cache.get("win", (1.0, 0.0, 0))
    pr = _cache.get("prop", (1.0, 0.0, 0))
    return {"win_t": w[0], "win_n": w[2], "prop_t": pr[0], "prop_n": pr[2]}
