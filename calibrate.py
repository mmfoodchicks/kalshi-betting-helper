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


def scale(p, t, q0=0.5):
    """Calibrate one probability.

    Overconfident models (t>1) are reined in with an ANCHORED temperature: the
    calibrated region [1-q0, q0] passes through untouched, and only the excess
    BEYOND the anchor is shrunk. This is the key fix over a plain temperature —
    a global shrink toward 0.5 crushes the moderate favorites (52-62%) where every
    moneyline lives, manufacturing negative edges against a fair market. Anchoring
    leaves that core alone and only pulls the genuinely-high (and noisier) picks.

    Underconfident models (t<1) are sharpened with a plain temperature toward 0.5
    (no dead zone — we want to stretch the whole range outward)."""
    if p is None or t is None or t == 1.0:
        return p
    if t < 1.0 or q0 is None or q0 <= 0.5:      # sharpen, or no anchor
        return _sigmoid(_logit(p) / t)
    if (1.0 - q0) <= p <= q0:                    # calibrated core — untouched
        return p
    q = q0 if p > q0 else (1.0 - q0)
    return _sigmoid(_logit(q) + (_logit(p) - _logit(q)) / t)


def _fit(pairs, n_floor):
    """Fit the calibration from graded (prob, outcome) pairs. Returns (t, q0, n).

    Grid-search a temperature (min Brier). If the model is OVERconfident (t>1) we
    anchor the shrink at the calibrated boundary — the mean predicted probability,
    clamped to [0.5, 0.62] — and refit t under that anchor, so the correction bites
    only above the region the model already gets right. Everything is regularized
    toward no-op (t=1) by sample size so a thin/noisy history can't distort it."""
    pairs = [(float(p), 1.0 if o else 0.0) for p, o in pairs
             if p is not None and o is not None and 0.0 < float(p) < 1.0]
    n = len(pairs)
    if n < 40:                       # too little to say anything — leave it alone
        return 1.0, 0.5, n
    w = min(1.0, n / float(n_floor))

    def fit_t(q0):
        best_t, best_b = 1.0, None
        t = 0.80
        while t <= 4.0001:
            b = sum((scale(p, t, q0) - o) ** 2 for p, o in pairs) / n
            if best_b is None or b < best_b:
                best_b, best_t = b, t
            t = round(t + 0.05, 2)
        return best_t

    t_pure = fit_t(0.5)
    if t_pure <= 1.02:               # calibrated or underconfident -> plain temp
        return round(1.0 + (t_pure - 1.0) * w, 3), 0.5, n
    # Overconfident: anchor at the calibrated boundary and refit under the anchor.
    mean_p = sum(p for p, _ in pairs) / n
    q0 = min(0.62, max(0.5, mean_p))
    t_anch = fit_t(q0)
    return round(1.0 + (t_anch - 1.0) * w, 3), round(q0, 3), n



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

_cache = {}          # model -> (t, q0, fitted_at, n)
_TTL = 6 * 3600      # refit a few times a day; graded history changes slowly


def _params(model):
    """Cached (t, q0, n) for a registered model. (1.0, 0.5, 0) = no correction."""
    hit = _cache.get(model)
    if hit and _t.time() - hit[2] < _TTL:
        return hit[0], hit[1], hit[3]
    t, q0, n = 1.0, 0.5, 0
    spec = _MODELS.get(model)
    if spec:
        try:
            t, q0, n = _fit(spec[0](), spec[1])
        except Exception:
            t, q0, n = 1.0, 0.5, 0
    _cache[model] = (t, q0, _t.time(), n)
    return t, q0, n


def temperature(model):
    """Cached temperature for a registered model (1.0 = no correction / no data)."""
    return _params(model)[0]


def apply(model, p):
    t, q0, _n = _params(model)
    return scale(p, t, q0)


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
    """Back-compat: win/prop temperatures + sample sizes for the MLB record header,
    plus a concrete example (what an 80% pick becomes) so the header can show that
    the correction hits the high end, not moderate favorites."""
    tw, qw, nw = _params("win")
    tp, qp, np_ = _params("prop")
    return {"win_t": tw, "win_n": nw, "prop_t": tp, "prop_n": np_,
            "win_ex80": round(scale(0.80, tw, qw) * 100, 1),
            "prop_ex80": round(scale(0.80, tp, qp) * 100, 1)}


def report():
    """Per-model calibration audit for the UI: temperature, anchor, graded sample
    size, how many predictions are logged-but-not-settled, a plain-English read of
    the direction, and an example (what an 80% pick becomes) so the correction is
    concrete — anchoring means moderate favorites are barely touched."""
    try:
        import predlog
        logged = predlog.status()
    except Exception:
        logged = {}
    out = {}
    for m in _MODELS:
        t, q0, n = _params(m)
        if n < 40:
            direction = "accruing" if (n or logged.get(m, {}).get("logged")) else "no data"
        elif t > 1.03:
            direction = "reined in (was overconfident)"
        elif t < 0.97:
            direction = "sharpened (was underconfident)"
        else:
            direction = "well-calibrated"
        row = {"t": t, "q0": q0, "n": n, "direction": direction,
               "ex80": round(scale(0.80, t, q0) * 100, 1),
               "ex55": round(scale(0.55, t, q0) * 100, 1)}
        if m in logged:
            row["logged"] = logged[m]["logged"]
        out[m] = row
    return out
