"""Probability calibration, learned from Vigil's own graded history — site-wide.

Models can be over- or under-confident. Overconfident: says 89%, hits 67% (chalk
looks safer than it is, edges look bigger than they are). Underconfident: says
72%, hits 80% (real edges hidden). Both cost money. The measured problem across
the app is high-end OVERconfidence, but the fitter below detects and corrects
either direction from the data.

The first tool is TEMPERATURE SCALING: divide the log-odds by T. T>1 shrinks toward
50% (reins in overconfidence); T<1 stretches away (fixes underconfidence). Because
it works in log-odds space it barely moves a calibrated 0.53 while pulling an
overconfident 0.89 hard — exactly the shape of that problem.

But a temperature is SYMMETRIC about 50%, and that makes it blind to the error the
MLB prop model actually has. Measured on 1,738 graded props, that model is not
over-spread — it is uniformly too HIGH:

    band        n     says    hits    95% CI on hits
    .00-.20    977    .088    .087    [.071, .106]   consistent
    .20-.50    388    .276    .183    [.148, .225]   TOO HIGH
    .55-.85    345    .666    .583    [.530, .633]   TOO HIGH

No temperature can fix that. Above 50% the fix is to pull DOWN toward 50 (T>1);
below 50% the fix is also to pull DOWN, which is AWAY from 50 (T<1). One knob,
two directions. The Brier grid resolved that contradiction by fitting T=0.95 —
"sharpen" — because 977 of the rows sit in the well-behaved .00-.20 band and
dominate the score. Sharpening pushes the .55-.85 band the WRONG way, and a
parlay multiplies the damage: four legs quoted at 70% came out to 25.3% while the
same legs empirically hit 58.3%, a real 11.5% — the slip was overstated 2.2x.

So the second tool is PLATT SCALING: q = sigmoid(logit(p)/T + B). The intercept B
is the missing knob — a constant log-odds shift that moves every prediction the
same direction, which is exactly the shape of a bias.

Which correction is used is DECIDED BY THE DATA, not by us: identity, temperature
and Platt are each fit on training folds and scored by 5-fold cross-validated log
loss, and a candidate is adopted only if it beats identity out-of-sample by a
clear margin. A correction that only fits noise loses to identity in CV and is
never shipped. Whatever wins is then REGULARIZED toward no-op by sample size, so a
thin history can never earn a big correction. A model with no graded data is a
no-op until its outcomes accrue. Everything is cached and fails safe.

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


def scale(p, t, q0=0.5, b=0.0):
    """Calibrate one probability.

    `b` (a log-odds intercept) selects the family. b == 0 is the anchored
    TEMPERATURE described below. Any other b is PLATT scaling —
    sigmoid(logit(p)/t + b) — where t is the slope and b shifts every prediction
    the same direction. The anchor is meaningless once there is an intercept (a
    bias applies at 50% too), so it is not used on that path.

    Overconfident models (t>1) are reined in with an ANCHORED temperature: the
    calibrated region [1-q0, q0] passes through untouched, and only the excess
    BEYOND the anchor is shrunk. This is the key fix over a plain temperature —
    a global shrink toward 0.5 crushes the moderate favorites (52-62%) where every
    moneyline lives, manufacturing negative edges against a fair market. Anchoring
    leaves that core alone and only pulls the genuinely-high (and noisier) picks.

    Underconfident models (t<1) are sharpened with a plain temperature toward 0.5
    (no dead zone — we want to stretch the whole range outward)."""
    if p is None or t is None:
        return p
    if b:
        return _sigmoid(_logit(p) / t + b)
    if t == 1.0:
        return p
    if t < 1.0 or q0 is None or q0 <= 0.5:      # sharpen, or no anchor
        return _sigmoid(_logit(p) / t)
    if (1.0 - q0) <= p <= q0:                    # calibrated core — untouched
        return p
    q = q0 if p > q0 else (1.0 - q0)
    return _sigmoid(_logit(q) + (_logit(p) - _logit(q)) / t)


def _logloss(pairs, t, q0, b):
    """Mean negative log likelihood of the graded outcomes under one calibration.

    Log loss, not Brier, is the scoring rule here. Brier is a squared error and
    barely notices a confident miss, which is precisely the mistake that ruins a
    parlay: a leg quoted at 85% that hits 60% costs a parlay far more than the
    Brier gap suggests. Log loss is unbounded as a confident prediction fails, so
    it prices that the way a multiplied slip does."""
    s = 0.0
    for p, o in pairs:
        q = min(1 - 1e-12, max(1e-12, scale(p, t, q0, b)))
        s -= math.log(q) if o else math.log(1 - q)
    return s / len(pairs)


def _fit_temp(pairs):
    """Best anchored temperature (the original family). Returns (t, q0)."""
    def grid(q0):
        best, bt = None, 1.0
        t = 0.80
        while t <= 4.0001:
            ll = _logloss(pairs, t, q0, 0.0)
            if best is None or ll < best:
                best, bt = ll, t
            t = round(t + 0.05, 2)
        return bt
    t_pure = grid(0.5)
    if t_pure <= 1.02:
        return t_pure, 0.5
    mean_p = sum(p for p, _ in pairs) / len(pairs)
    q0 = min(0.62, max(0.5, mean_p))
    return grid(q0), round(q0, 3)


def _fit_platt(pairs):
    """Best (slope, intercept) for sigmoid(logit(p)/t + b), by coarse-then-fine
    grid. Log loss is convex in (1/t, b), so a local refine around the coarse
    winner finds the optimum without needing a solver."""
    zs = [(_logit(p), o) for p, o in pairs]
    n = len(zs)

    def ll(t, b):
        s = 0.0
        inv = 1.0 / t
        for z, o in zs:
            q = min(1 - 1e-12, max(1e-12, _sigmoid(z * inv + b)))
            s -= math.log(q) if o else math.log(1 - q)
        return s / n

    best = (None, 1.0, 0.0)
    for ti in range(6, 41):                       # t in 0.6 .. 4.0
        t = ti / 10.0
        for bi in range(-24, 25):                 # b in -2.4 .. 2.4
            b = bi / 10.0
            v = ll(t, b)
            if best[0] is None or v < best[0]:
                best = (v, t, b)
    _v, t0, b0 = best
    for ti in range(-9, 10):                      # refine to 0.01 / 0.02
        t = round(t0 + ti / 100.0, 3)
        if t <= 0.05:
            continue
        for bi in range(-9, 10):
            b = round(b0 + bi / 50.0, 3)
            v = ll(t, b)
            if v < best[0]:
                best = (v, t, b)
    return best[1], best[2]


def _cv_loss(pairs, fitter, k=5):
    """K-fold cross-validated log loss for a fitting strategy.

    This is what stops a richer correction from being adopted just because it can
    bend further. Each fold fits on 4/5 of the graded history and is scored on the
    fifth it never saw, so a family that is only fitting noise scores WORSE than
    identity here and loses. Deterministic folds (stride slicing) so the choice
    doesn't wobble between refits on identical data."""
    if len(pairs) < k * 20:
        return None
    total = 0.0
    for f in range(k):
        val = pairs[f::k]
        tr = [x for i, x in enumerate(pairs) if i % k != f]
        if not val or len(tr) < 40:
            return None
        try:
            t, q0, b = fitter(tr)
        except Exception:
            return None
        total += _logloss(val, t, q0, b)
    return total / k


# How much better than identity (in nats of out-of-sample log loss) a correction
# must be before it is adopted. Cross-validation already penalizes overfitting;
# this is the second guard, against adopting a correction whose benefit is real
# but too small to be worth moving every number on the site for.
_CV_MARGIN = 0.001


def _fit(pairs, n_floor, day_floor=None):
    """Fit the calibration from graded (prob, outcome[, day]) rows.
    Returns (t, q0, b, n).

    Three candidate families — identity, anchored temperature, Platt — are each
    cross-validated, and the winner must beat identity out-of-sample by _CV_MARGIN
    to be used at all. The winner is then refit on ALL the data (CV chooses the
    family; the final parameters should see every row) and REGULARIZED toward
    no-op by sample size, so a thin history can never earn a full correction.

    ROWS ARE NOT INDEPENDENT, AND COUNTING THEM AS IF THEY WERE IS HOW THIS WENT
    WRONG. Every batter prop on one night shares a weather front, a set of
    starting pitchers and whatever the league's offence happened to do that day,
    so a slate contributes something much closer to ONE observation of the bias
    than to four hundred. The prop calibrator was fitted on 1,489 graded rows
    that spanned FOUR DATES, sailed past an 800-ROW floor, and earned a
    full-strength -0.5 log-odds correction: it took a raw 63% chance of a hit,
    against a real 59.7%, and priced it at 48%. When a row carries its day, the
    damping is limited by the number of distinct days as well.
    """
    rows = []
    for row in pairs:
        p, o = row[0], row[1]
        day = row[2] if len(row) > 2 else None
        if p is None or o is None:
            continue
        p = float(p)
        if not (0.0 < p < 1.0):
            continue
        rows.append((p, 1.0 if o else 0.0, day))
    pairs = [(p, o) for p, o, _d in rows]
    n = len(pairs)
    if n < 40:                       # too little to say anything — leave it alone
        return 1.0, 0.5, 0.0, n
    w = min(1.0, n / float(n_floor))
    days = {d for _p, _o, d in rows if d is not None}
    if day_floor and days:
        w = min(w, len(days) / float(day_floor))

    def as_temp(tr):
        t, q0 = _fit_temp(tr)
        return t, q0, 0.0

    def as_platt(tr):
        t, b = _fit_platt(tr)
        return t, 0.5, (b or 1e-9)   # keep b non-zero so `scale` picks the family

    base = _cv_loss(pairs, lambda tr: (1.0, 0.5, 0.0))
    cands = []
    for name, fitter in (("temp", as_temp), ("platt", as_platt)):
        cv = _cv_loss(pairs, fitter)
        if cv is not None and base is not None and cv < base - _CV_MARGIN:
            cands.append((cv, name, fitter))
    if not cands:
        return 1.0, 0.5, 0.0, n
    cands.sort(key=lambda x: x[0])
    t, q0, b = cands[0][2](pairs)          # refit the winning family on everything
    # Damp toward no-op by sample size: t -> 1, b -> 0.
    t = round(1.0 + (t - 1.0) * w, 3)
    b = round(b * w, 4)
    if abs(b) < 1e-4:                      # a vanishing intercept is a temperature
        b = 0.0
    return t, (q0 if not b else 0.5), b, n



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
    # Floor = how many graded outcomes earn a FULL-strength correction; below it the
    # fit is damped toward no-op (1.0). The floor must scale with how NOISY a single
    # outcome is: one binary sports result (game/match/fight) is nearly a coin flip,
    # so its calibration buckets are noisy and a thin sample must NOT earn a big
    # temperature — that over-shrinks the moderate favorites and manufactures edges
    # against the market. Win/prop have deep history, so they carry the full 800.
    # Tennis/UFC are the same noisy-binary shape but accrue graded markets far more
    # slowly, so they sit lower (enough to require a season+ of settled markets for a
    # full correction, without stalling activation forever). Crypto's GBM fair value
    # is a smooth continuous number that settles by the thousands, so 400 is ample.
    #
    # A THIRD entry is the minimum number of distinct DAYS the sample must span
    # (see _fit). It matters wherever many rows settle on the same slate, which
    # is exactly where the row floor is easiest to satisfy and least meaningful.
    # Player props are the extreme case: a full slate grades several hundred
    # rows, so four dates cleared an 800-row floor outright. 30 days is a month
    # of baseball, which is a judgment call, not a measurement -- it is set where
    # day-to-day swings in league offence have had a real chance to average out.
    "win":    (_win_pairs,                        800, 30),
    "prop":   (_prop_pairs,                       800, 30),
    "crypto": (_crypto_pairs,                     400),
    "tennis": (lambda: _predlog_pairs("tennis"), 400),
    "ufc":    (lambda: _predlog_pairs("ufc"),    300),
    # NFL moneylines (drive engine): same noisy-binary shape as tennis/UFC, and a
    # week only settles ~16 games, so a full correction takes about a season.
    "nfl":    (lambda: _predlog_pairs("nfl"),    250),
    # Preseason NFL is a SEPARATE model, not more of the same one. The engine is
    # anchored to Kalshi's de-vigged ladder rather than to a projection source,
    # and the games themselves scatter differently -- margin SD 15.40 against
    # roughly 13.5, home edge +0.78 against roughly 2.5. Registered so its record
    # is measurable in report(); nfl_game_sim deliberately does NOT apply it,
    # because a market-anchored probability has no error of its own to correct.
    # If that record ever shows a real, stable bias, THAT is the evidence for
    # applying it -- and it can only accrue in a bucket of its own. ~65 games a
    # year, so the floor is three seasons rather than one.
    "nfl_pre": (lambda: _predlog_pairs("nfl_pre"), 200),
    # WNBA moneylines (possession engine): ~2-6 games/day settle, so history
    # accrues fast; still a noisy binary, so a real floor before full trust.
    # NBA / NHL (possession & shot engines) — dense schedules settle fast.
    "nba":    (lambda: _predlog_pairs("nba"),    400),
    "nhl":    (lambda: _predlog_pairs("nhl"),    400),
    # College football (drive engine): one Saturday settles ~60 games.
    "cfb":    (lambda: _predlog_pairs("cfb"),    300),
}

_cache = {}          # model -> (params, fitted_at)
_TTL = 6 * 3600      # refit a few times a day; graded history changes slowly


def _params(model):
    """Cached (t, q0, b, n) for a registered model. (1.0, 0.5, 0.0, 0) = no
    correction."""
    hit = _cache.get(model)
    if hit and _t.time() - hit[1] < _TTL:
        return hit[0]
    out = (1.0, 0.5, 0.0, 0)
    spec = _MODELS.get(model)
    if spec:
        try:
            out = _fit(spec[0](), spec[1], spec[2] if len(spec) > 2 else None)
        except Exception:
            out = (1.0, 0.5, 0.0, 0)
    _cache[model] = (out, _t.time())
    return out


def temperature(model):
    """Cached temperature for a registered model (1.0 = no correction / no data).
    With a Platt fit this is the SLOPE; see bias() for the intercept."""
    return _params(model)[0]


def bias(model):
    """Cached log-odds intercept (0.0 = pure temperature / no correction)."""
    return _params(model)[2]


def apply(model, p):
    t, q0, b, _n = _params(model)
    return scale(p, t, q0, b)


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
    tw, qw, bw, nw = _params("win")
    tp, qp, bp, np_ = _params("prop")
    return {"win_t": tw, "win_n": nw, "prop_t": tp, "prop_n": np_,
            "win_b": bw, "prop_b": bp,
            "win_ex80": round(scale(0.80, tw, qw, bw) * 100, 1),
            "prop_ex80": round(scale(0.80, tp, qp, bp) * 100, 1)}


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
        t, q0, b, n = _params(m)
        if n < 40:
            direction = "accruing" if (n or logged.get(m, {}).get("logged")) else "no data"
        elif b < -0.03:
            direction = "pulled down (ran too high across the board)"
        elif b > 0.03:
            direction = "pushed up (ran too low across the board)"
        elif t > 1.03:
            direction = "reined in (was overconfident)"
        elif t < 0.97:
            direction = "sharpened (was underconfident)"
        else:
            direction = "well-calibrated"
        row = {"t": t, "q0": q0, "b": b, "n": n, "direction": direction,
               "family": "platt" if b else ("temperature" if t != 1.0 else "none"),
               "ex80": round(scale(0.80, t, q0, b) * 100, 1),
               "ex55": round(scale(0.55, t, q0, b) * 100, 1)}
        if m in logged:
            row["logged"] = logged[m]["logged"]
        out[m] = row
    return out
