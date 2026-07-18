"""Probability calibration, learned from Vigil's own graded history — site-wide.

Models can be over- or under-confident. Overconfident: says 89%, hits 67% (chalk
looks safer than it is, edges look bigger than they are). Underconfident: says
72%, hits 80% (real edges hidden). Both cost money. The measured problem across
the app is high-end OVERconfidence, but the fitter below detects and corrects
either direction from the data.

The tool is TEMPERATURE SCALING: divide the log-odds by T. T>1 shrinks toward 50%
(reins in overconfidence); T<1 stretches away (fixes underconfidence). Because it
works in log-odds space it barely moves a calibrated 0.53 while pulling an
overconfident 0.89 hard — exactly the shape of the problem.

Nothing is hardcoded. Each model's T is FIT on that model's own graded outcomes
(predicted prob vs what happened) and REGULARIZED toward 1.0 (no change) by sample
size, so a thin or noisy history can never distort it: with little data T stays
near 1; only a large, consistent sample earns a real correction. A model with no
graded data is simply a no-op until its outcomes accrue. Everything is cached and
fails safe.

Registered models (loader returns [(prob 0-1, outcome 0/1)]):
  win    — MLB game moneyline picks           (store.win_grade_pairs)
  prop   — MLB batter props                    (store.prop_grade_pairs)
  crypto — the GBM crypto fair-value model     (recorder.calibration_pairs)
Others (tennis/ufc/racing/nfl/lol) have no prediction-vs-outcome log yet, so they
are absent here and stay uncalibrated until that data accrues.
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
    """Temperature-scale one probability. t>1 reins it in toward 0.5; t<1 sharpens."""
    if p is None or t is None or t == 1.0:
        return p
    return _sigmoid(_logit(p) / t)


def _fit(pairs, n_floor):
    """Grid-search the temperature that best calibrates (min Brier) the graded
    (prob, outcome) pairs, then shrink it toward 1.0 by how much data backs it."""
    pairs = [(float(p), 1.0 if o else 0.0) for p, o in pairs
             if p is not None and o is not None and 0.0 < float(p) < 1.0]
    n = len(pairs)
    if n < 40:                       # too little to say anything — leave it alone
        return 1.0, n
    logits = [(_logit(p), o) for p, o in pairs]
    best_t, best_b = 1.0, None
    # Search T in [0.80, 3.0]. Sharpening (T<1) is bounded more tightly than
    # reining-in: making the model MORE confident raises bet sizes, so we let the
    # data do it only gently, while overconfidence can be corrected hard.
    t = 0.80
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
    return round(1.0 + (best_t - 1.0) * w, 3), n


# model -> (loader, sample floor). The floor is how many graded outcomes earn a
# FULL correction; below it the fit is proportionally damped toward no-op.
def _win_pairs():
    import store
    return store.win_grade_pairs()


def _prop_pairs():
    import store
    return store.prop_grade_pairs()


def _crypto_pairs():
    import recorder
    return recorder.calibration_pairs()


def _predlog_pairs(model):
    import predlog
    return predlog.pairs(model)


_MODELS = {
    "win":    (_win_pairs,                        200),
    "prop":   (_prop_pairs,                       800),
    "crypto": (_crypto_pairs,                     400),
    "tennis": (lambda: _predlog_pairs("tennis"), 150),
    "ufc":    (lambda: _predlog_pairs("ufc"),    120),
}

_cache = {}          # model -> (temperature, fitted_at, n)
_TTL = 6 * 3600      # refit a few times a day; graded history changes slowly


def temperature(model):
    """Cached temperature for a registered model (1.0 = no correction / no data)."""
    hit = _cache.get(model)
    if hit and _t.time() - hit[1] < _TTL:
        return hit[0]
    t, n = 1.0, 0
    spec = _MODELS.get(model)
    if spec:
        try:
            t, n = _fit(spec[0](), spec[1])
        except Exception:
            t, n = 1.0, 0
    _cache[model] = (t, _t.time(), n)
    return t


def apply(model, p):
    return scale(p, temperature(model))


# Convenience wrappers used at the call sites.
def win_prob(p):
    return apply("win", p)


def batter_prop(p):
    return apply("prop", p)


def crypto(p):
    return apply("crypto", p)


def tennis(p):
    return apply("tennis", p)


def ufc(p):
    return apply("ufc", p)


def temps():
    """Back-compat: win/prop temperatures + sample sizes for the MLB record header."""
    temperature("win")
    temperature("prop")
    w = _cache.get("win", (1.0, 0.0, 0))
    pr = _cache.get("prop", (1.0, 0.0, 0))
    return {"win_t": w[0], "win_n": w[2], "prop_t": pr[0], "prop_n": pr[2]}


def report():
    """Per-model calibration audit for the UI: temperature, graded sample size,
    how many predictions are logged-but-not-yet-settled, and a plain-English read
    of the direction (over/under/well-calibrated/accruing/no-data)."""
    try:
        import predlog
        logged = predlog.status()
    except Exception:
        logged = {}
    out = {}
    for m in _MODELS:
        temperature(m)                       # ensure fitted + cached
        t, _at, n = _cache.get(m, (1.0, 0.0, 0))
        if n < 40:
            direction = "accruing" if (n or logged.get(m, {}).get("logged")) else "no data"
        elif t > 1.03:
            direction = "reined in (was overconfident)"
        elif t < 0.97:
            direction = "sharpened (was underconfident)"
        else:
            direction = "well-calibrated"
        row = {"t": t, "n": n, "direction": direction}
        if m in logged:
            row["logged"] = logged[m]["logged"]
        out[m] = row
    return out
