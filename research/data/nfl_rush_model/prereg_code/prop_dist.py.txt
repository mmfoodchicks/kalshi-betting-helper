"""Alternate-line probabilities for player props -- pure stdlib, exact formulas.

A Kalshi player prop is a ladder of "stat >= k" markets (KXNBAPTS ... 25+, 30+,
35+). One fitted distribution has to price every rung, and the rungs have to
agree with each other: a higher threshold is never more likely. This module is
that machinery and nothing else: it holds no data, makes no network call and
imports nothing heavy, because the web server imports it (CLAUDE.md: the server
has no numpy, and nothing CPU-bound belongs in a gunicorn process).

CONVENTIONS (every function below follows them)

  * Probabilities are fractions in [0, 1].
  * "sf_ge(k)" is P(X >= k), the event Kalshi's "k+" market pays on. Thresholds
    are integers in practice (floor_strike 24.5 <-> "25+"); a non-integer k is
    read as P(X >= ceil(k)).
  * Count families are parameterised by their MEAN. NegBin dispersion `alpha`
    is the variance excess: Var = mu + alpha * mu^2 (alpha -> 0 is Poisson;
    r = 1/alpha is the "size" in R / scipy's `n`). Below ALPHA_POISSON the
    family IS a Poisson, because the incomplete-beta continued fraction with
    r > 1e6 converges slowly and the two agree to ~1e-9.
  * Zero inflation mixes a point mass at 0 (probability `pi`) with the base
    family: P(X = 0) = pi + (1 - pi) P_base(0). `mu` is the BASE mean; the
    overall mean is (1 - pi) * mu.
  * The normal family is discretised with a continuity correction:
    P(X >= k) = P(N(mu, sd) >= k - 0.5), optionally left-truncated at 0.
  * "Yards" is a point mass at 0 (no catch / no carry) plus a continuous
    positive part (gamma or lognormal fitted by moments), rounded to whole
    yards with the same k - 0.5 correction.

THE CLOSED FORMS (checked against direct summation in tests/guards_prop_math.py)

  Poisson:  P(X >= k) = P(k, mu), the regularised LOWER incomplete gamma.
  NegBin:   P(X >= k) = I_{mu/(r+mu)}(k, r), the regularised incomplete beta
            (P(X <= k-1) = I_p(r, k) with p = r/(r+mu); the complement identity
            I_x(a, b) = 1 - I_{1-x}(b, a) gives the form above).
  Gamma:    S(x) = Q(shape, x/scale), the regularised UPPER incomplete gamma.

Both incomplete functions are Numerical-Recipes style: a power series where it
converges fast, a modified-Lentz continued fraction where it does not, with
lgamma in the prefactor so nothing overflows at k in the hundreds.

WHAT THIS DOES NOT DO. It does not know a player. Whether a mean is right, or
a dispersion is trustworthy, is the model's job; the fitters below only turn a
sample into a number and say plainly when the sample is too small or degenerate
to do even that (`ok: False` and a reason, never an invented value).
"""
import bisect
import math
import random

import errlog

ALPHA_POISSON = 1e-6          # NegBin with alpha below this is evaluated as Poisson
_EPS = 3e-16
_FPMIN = 1e-300
_MAX_IT = 5000


# ---------------------------------------------------------------------------
# Special functions
# ---------------------------------------------------------------------------
def norm_sf(z):
    """P(Z >= z) for a standard normal, via erfc (accurate in the far tail)."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def norm_cdf(z):
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def gammainc_pq(a, x):
    """(P, Q) = regularised lower / upper incomplete gamma at shape a > 0, x >= 0.

    Series for x < a + 1 (fast there), modified-Lentz continued fraction
    otherwise. The prefactor exp(-x + a ln x - lgamma(a)) is formed in logs."""
    if a <= 0.0:
        raise ValueError("gammainc: shape must be > 0")
    if x <= 0.0:
        return 0.0, 1.0
    lg = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1.0:
        ap, term = a, 1.0 / a
        total = term
        for _ in range(_MAX_IT):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * _EPS:
                break
        else:
            errlog.note("PDIST-gammainc", msg=f"series did not converge a={a} x={x}")
        p = min(1.0, max(0.0, total * math.exp(lg)))
        return p, 1.0 - p
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, _MAX_IT):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    else:
        errlog.note("PDIST-gammainc", msg=f"cf did not converge a={a} x={x}")
    q = min(1.0, max(0.0, math.exp(lg) * h))
    return 1.0 - q, q


def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _MAX_IT):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            return h
    errlog.note("PDIST-betainc", msg=f"cf did not converge a={a} b={b} x={x}")
    return h


def betainc_reg(a, b, x):
    """Regularised incomplete beta I_x(a, b), a, b > 0, 0 <= x <= 1."""
    if a <= 0.0 or b <= 0.0:
        raise ValueError("betainc: a and b must be > 0")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
           + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return min(1.0, max(0.0, bt * _betacf(a, b, x) / a))
    return min(1.0, max(0.0, 1.0 - bt * _betacf(b, a, 1.0 - x) / b))


# ---------------------------------------------------------------------------
# Distribution families
# ---------------------------------------------------------------------------
def _kint(k):
    """The integer threshold a (possibly fractional) k means: P(X >= ceil(k))."""
    return int(math.ceil(k - 1e-12))


class Dist:
    """Interface: sf_ge(k), pmf(k), mean(), var(), to_dict()."""
    family = "?"

    def sf_ge(self, k):
        raise NotImplementedError

    def pmf(self, k):
        raise NotImplementedError

    def mean(self):
        raise NotImplementedError

    def var(self):
        raise NotImplementedError

    def to_dict(self):
        raise NotImplementedError

    def ladder(self, ks):
        """{k: P(X >= k)} for every k, clamped to [0, 1] and made monotone."""
        ks = list(ks)
        ps = [min(1.0, max(0.0, self.sf_ge(k))) for k in ks]
        return dict(zip(ks, enforce_monotone(ks, ps)))

    def cdf_table(self, tol=1e-12, kmax=20000):
        """cum[k] = P(X <= k) for k = 0.. until the remaining tail is below
        `tol`; the last entry is forced to 1.0, so the tail beyond it (at most
        `tol` of probability) lands on the last value. Built for sampling.

        If `kmax` is exhausted with 1 - cum >= tol the family has mass this
        table cannot represent (a support that is unbounded below, or a mean so
        far out that 20,000 steps do not reach it), and forcing the last entry
        to 1.0 would silently hand ALL of that mass to k = kmax: an untruncated
        NormalCC(1, 3) put 30.6% of its copula draws on k = 20,000. It is a
        ValueError, ledgered PDIST-cdf-table, never a quiet pile-up."""
        cum, c = [], 0.0
        for k in range(kmax + 1):
            c += self.pmf(k)
            cum.append(min(c, 1.0))
            if 1.0 - c < tol:
                break
        else:
            errlog.note("PDIST-cdf-table", msg=f"{self.family}: {1.0 - c:.3g} of the mass lies beyond k={kmax}")
            raise ValueError(f"{self.family}: cdf_table cannot represent {1.0 - c:.3g} of the probability "
                             f"within k <= {kmax} (support unbounded below, or the mean is beyond kmax)")
        cum[-1] = 1.0
        return cum


class Poisson(Dist):
    family = "poisson"

    def __init__(self, mu):
        mu = float(mu)
        if not (mu >= 0.0) or math.isinf(mu):
            raise ValueError("Poisson mean must be finite and >= 0")
        self.mu = mu

    def sf_ge(self, k):
        k = _kint(k)
        if k <= 0:
            return 1.0
        if self.mu <= 0.0:
            return 0.0
        return gammainc_pq(float(k), self.mu)[0]

    def pmf(self, k):
        k = int(k)
        if k < 0:
            return 0.0
        if self.mu <= 0.0:
            return 1.0 if k == 0 else 0.0
        return math.exp(k * math.log(self.mu) - self.mu - math.lgamma(k + 1.0))

    def mean(self):
        return self.mu

    def var(self):
        return self.mu

    def to_dict(self):
        return {"family": "poisson", "params": {"mu": self.mu}}


class NegBin(Dist):
    """Negative binomial, mean `mu`, variance mu + alpha * mu^2."""
    family = "negbin"

    def __init__(self, mu, alpha):
        mu, alpha = float(mu), float(alpha)
        if not (mu >= 0.0) or math.isinf(mu):
            raise ValueError("NegBin mean must be finite and >= 0")
        if not (alpha >= 0.0) or math.isinf(alpha):
            raise ValueError("NegBin alpha must be finite and >= 0")
        self.mu, self.alpha = mu, alpha

    @property
    def r(self):
        return math.inf if self.alpha == 0.0 else 1.0 / self.alpha

    def sf_ge(self, k):
        k = _kint(k)
        if k <= 0:
            return 1.0
        if self.mu <= 0.0:
            return 0.0
        if self.alpha < ALPHA_POISSON:
            return gammainc_pq(float(k), self.mu)[0]
        r = 1.0 / self.alpha
        return betainc_reg(float(k), r, self.mu / (r + self.mu))

    def pmf(self, k):
        k = int(k)
        if k < 0:
            return 0.0
        if self.mu <= 0.0:
            return 1.0 if k == 0 else 0.0
        if self.alpha < ALPHA_POISSON:
            return Poisson(self.mu).pmf(k)
        r = 1.0 / self.alpha
        return math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1.0)
                        + r * math.log(r / (r + self.mu)) + k * math.log(self.mu / (r + self.mu)))

    def mean(self):
        return self.mu

    def var(self):
        return self.mu + self.alpha * self.mu * self.mu

    def to_dict(self):
        return {"family": "negbin", "params": {"mu": self.mu, "alpha": self.alpha}}


class ZeroInflated(Dist):
    """A point mass at 0 (probability pi) mixed with a base count family.

    P(X >= k) = (1 - pi) P_base(X >= k) for k >= 1, and 1 for k <= 0."""

    def __init__(self, base, pi):
        pi = float(pi)
        if not (0.0 <= pi < 1.0):
            raise ValueError("zero-inflation pi must be in [0, 1)")
        self.base, self.pi = base, pi
        self.family = "zi_" + base.family

    @classmethod
    def from_overall_mean(cls, family, mean, pi, alpha=None):
        """Zero-inflated `family` ("poisson" | "negbin") whose OVERALL mean is
        `mean`: the base mean is mean / (1 - pi)."""
        if not (0.0 <= pi < 1.0):
            raise ValueError("zero-inflation pi must be in [0, 1)")
        mu = float(mean) / (1.0 - pi)
        if family == "poisson":
            return cls(Poisson(mu), pi)
        if family == "negbin":
            return cls(NegBin(mu, alpha or 0.0), pi)
        raise ValueError("family must be 'poisson' or 'negbin'")

    def sf_ge(self, k):
        if _kint(k) <= 0:
            return 1.0
        return (1.0 - self.pi) * self.base.sf_ge(k)

    def pmf(self, k):
        k = int(k)
        if k < 0:
            return 0.0
        b = (1.0 - self.pi) * self.base.pmf(k)
        return b + self.pi if k == 0 else b

    def mean(self):
        return (1.0 - self.pi) * self.base.mean()

    def var(self):
        m_b = self.base.mean()
        second = (1.0 - self.pi) * (self.base.var() + m_b * m_b)
        return second - self.mean() ** 2

    def to_dict(self):
        d = self.base.to_dict()
        return {"family": self.family, "params": dict(d["params"], pi=self.pi)}


class NormalCC(Dist):
    """Discretised normal: P(X >= k) = P(N(mu, sd) >= k - 0.5).

    `lower=0` (the DEFAULT) left-truncates the latent normal at -0.5 (so X >= 0)
    and renormalises: a box-score count cannot be negative, and an untruncated
    normal cannot be sampled as one (its negative mass has no index to live on --
    cdf_table raises, copula_draws with it raises). The truncation barely bites
    when mu is several sd above 0 (NormalCC(25, 6): 1e-5 of the mass) and bites
    hard when it is not (NormalCC(1, 3): 37% of the latent mass is cut); for a
    low-mean count the family is the wrong one -- use NegBin -- and the
    renormalised shape will show it. `lower=None` is the pure latent
    discretisation, for tail probabilities only (sf_ge / pmf work; sampling
    raises when the negative mass is >= the table tolerance).

    mean() / var() are the moments of the TRUNCATED variable that sf_ge / pmf
    describe (summed from the pmf), not the latent mu and sd^2."""
    family = "normal_cc"

    def __init__(self, mu, sd, lower=0):
        if not (sd > 0.0):
            raise ValueError("NormalCC sd must be > 0")
        self.mu, self.sd, self.lower = float(mu), float(sd), lower
        self._z0 = norm_sf((lower - 0.5 - self.mu) / self.sd) if lower is not None else 1.0
        if self._z0 <= 0.0:
            raise ValueError("NormalCC: truncation leaves no probability")

    def sf_ge(self, k):
        k = _kint(k)
        if self.lower is not None and k <= self.lower:
            return 1.0
        return min(1.0, norm_sf((k - 0.5 - self.mu) / self.sd) / self._z0)

    def pmf(self, k):
        k = int(k)
        if self.lower is not None and k < self.lower:
            return 0.0
        return max(0.0, self.sf_ge(k) - self.sf_ge(k + 1))

    def _moments(self):
        if self.lower is None:
            return self.mu, self.sd * self.sd
        if getattr(self, "_mv", None) is None:
            hi = max(int(self.mu + 14.0 * self.sd) + 2, int(self.lower) + int(14.0 * self.sd) + 2)
            m1 = m2 = 0.0
            for k in range(int(self.lower), hi + 1):
                p = self.pmf(k)
                m1 += k * p
                m2 += k * k * p
            self._mv = (m1, m2 - m1 * m1)
        return self._mv

    def mean(self):
        return self._moments()[0]

    def var(self):
        return self._moments()[1]

    def cdf_table(self, tol=1e-12, kmax=20000):
        # An untruncated normal has mass below index 0 that a table indexed
        # from 0 cannot hold; refuse at once instead of looping to kmax.
        if self.lower is None and norm_cdf((-0.5 - self.mu) / self.sd) >= tol:
            errlog.note("PDIST-cdf-table", msg=f"normal_cc lower=None mu={self.mu} sd={self.sd}: negative mass")
            raise ValueError("NormalCC(lower=None) has probability below 0 that a count table cannot hold; "
                             "use lower=0 (the default) to sample it")
        return Dist.cdf_table(self, tol=tol, kmax=kmax)

    def to_dict(self):
        return {"family": "normal_cc",
                "params": {"mu": self.mu, "sd": self.sd, "lower": self.lower}}


class Yards(Dist):
    """Point mass at 0 plus a continuous positive part, in whole yards.

    p0        probability of exactly nothing (no catch, no carry)
    mean_pos  mean of the NON-ZERO outcomes; sd_pos their standard deviation
    family    "gamma" (default) or "lognormal", both fitted by moments

    P(Y >= k) = (1 - p0) S_pos(k - 0.5) for k >= 1 and 1 for k <= 0, where
    S_pos is the survival function of the positive part. mean() / var() are the
    moments of the CONTINUOUS mixture (what the parameters were fitted to); the
    integer-rounded variable differs from them by under half a yard."""

    def __init__(self, p0, mean_pos, sd_pos, family="gamma"):
        if not (0.0 <= p0 < 1.0):
            raise ValueError("Yards p0 must be in [0, 1)")
        if not (mean_pos > 0.0 and sd_pos > 0.0):
            raise ValueError("Yards mean_pos and sd_pos must be > 0")
        if family not in ("gamma", "lognormal"):
            raise ValueError("Yards family must be 'gamma' or 'lognormal'")
        self.p0, self.mean_pos, self.sd_pos, self.pos_family = float(p0), float(mean_pos), float(sd_pos), family
        self.family = "yards_" + family
        if family == "gamma":
            self.shape = (mean_pos / sd_pos) ** 2
            self.scale = sd_pos * sd_pos / mean_pos
        else:
            s2 = math.log1p((sd_pos / mean_pos) ** 2)
            self.ln_sigma = math.sqrt(s2)
            self.ln_mu = math.log(mean_pos) - 0.5 * s2

    def _s_pos(self, x):
        if x <= 0.0:
            return 1.0
        if self.pos_family == "gamma":
            return gammainc_pq(self.shape, x / self.scale)[1]
        return norm_sf((math.log(x) - self.ln_mu) / self.ln_sigma)

    def sf_ge(self, k):
        k = _kint(k)
        if k <= 0:
            return 1.0
        return (1.0 - self.p0) * self._s_pos(k - 0.5)

    def pmf(self, k):
        k = int(k)
        if k < 0:
            return 0.0
        return max(0.0, self.sf_ge(k) - self.sf_ge(k + 1))

    def mean(self):
        return (1.0 - self.p0) * self.mean_pos

    def var(self):
        second = (1.0 - self.p0) * (self.sd_pos ** 2 + self.mean_pos ** 2)
        return second - self.mean() ** 2

    def to_dict(self):
        return {"family": self.family,
                "params": {"p0": self.p0, "mean_pos": self.mean_pos, "sd_pos": self.sd_pos}}


def yards_from_moments(mean_all, sd_all, p0, family="gamma"):
    """Yards distribution from the OVERALL mean and sd of the stat (zeros in)
    and the zero probability p0.

    E[Y] = (1-p0) m+ and E[Y^2] = (1-p0)(m+^2 + s+^2) give
        m+ = mean_all / (1 - p0),   s+^2 = (sd_all^2 + mean_all^2)/(1 - p0) - m+^2.
    A negative s+^2 means the inputs are inconsistent (a mean and zero rate the
    spread cannot be that small for) -- ValueError, not a quiet clamp."""
    if not (0.0 <= p0 < 1.0) or not (mean_all > 0.0) or not (sd_all > 0.0):
        raise ValueError("yards_from_moments: need 0 <= p0 < 1, mean > 0, sd > 0")
    m_pos = mean_all / (1.0 - p0)
    v_pos = (sd_all * sd_all + mean_all * mean_all) / (1.0 - p0) - m_pos * m_pos
    if not (v_pos > 0.0):
        raise ValueError("yards_from_moments: inconsistent moments (positive-part variance <= 0)")
    return Yards(p0, m_pos, math.sqrt(v_pos), family)


def interval_over_mean(make_dist, mean_lo, mean_hi, ks):
    """P(X >= k) bands from an uncertain MEAN: {k: (lo, hi)}.

    `make_dist(mean)` builds the family at that mean (dispersion etc. fixed).
    Both ends are evaluated and the min / max taken, so the band is right even
    for a family where monotonicity in the mean is not guaranteed. It carries
    mean uncertainty only -- dispersion and model-form error are not in it."""
    d_lo, d_hi = make_dist(mean_lo), make_dist(mean_hi)
    out = {}
    for k in ks:
        a, b = d_lo.sf_ge(k), d_hi.sf_ge(k)
        out[k] = (min(a, b), max(a, b))
    return out


# ---------------------------------------------------------------------------
# Monotonicity
# ---------------------------------------------------------------------------
def enforce_monotone(ks, ps, method="pava", weights=None, clamp=True):
    """Probabilities for "stat >= k" made non-increasing in k, in INPUT order.

    method "pava" (default): weighted isotonic regression (pool adjacent
        violators) -- the least-squares nearest non-increasing sequence, so a
        single noisy rung is averaged with its neighbours instead of one side
        winning.
    method "cummin": running minimum from the lowest threshold up (never raises
        a rung; the conservative fix when the low rungs are the trusted ones).
    Equal thresholds are pooled (weighted mean) first; both outputs are
    clamped to [0, 1] unless clamp=False."""
    ks, ps = list(ks), [float(p) for p in ps]
    if len(ks) != len(ps):
        raise ValueError("ks and ps differ in length")
    if weights is None:
        weights = [1.0] * len(ks)
    elif len(weights) != len(ks) or any(w <= 0 for w in weights):
        raise ValueError("weights must be positive and match ks")
    if not ks:
        return []
    if clamp:
        ps = [min(1.0, max(0.0, p)) for p in ps]
    order = sorted(range(len(ks)), key=lambda i: ks[i])
    # pool equal thresholds
    blocks = []                      # [k, weighted p, weight]
    for i in order:
        if blocks and blocks[-1][0] == ks[i]:
            b = blocks[-1]
            tot = b[2] + weights[i]
            b[1] = (b[1] * b[2] + ps[i] * weights[i]) / tot
            b[2] = tot
        else:
            blocks.append([ks[i], ps[i], float(weights[i])])
    if method == "cummin":
        run = math.inf
        vals = []
        for _, p, _w in blocks:
            run = min(run, p)
            vals.append(run)
    elif method == "pava":
        stack = []                   # each [value, weight, count]
        for _, p, w in blocks:
            stack.append([p, w, 1])
            while len(stack) > 1 and stack[-2][0] < stack[-1][0]:
                v2, w2, c2 = stack.pop()
                v1, w1, c1 = stack.pop()
                tot = w1 + w2
                stack.append([(v1 * w1 + v2 * w2) / tot, tot, c1 + c2])
        vals = []
        for v, _w, c in stack:
            vals.extend([v] * c)
    else:
        raise ValueError("method must be 'pava' or 'cummin'")
    by_k = {b[0]: v for b, v in zip(blocks, vals)}
    out = [by_k[k] for k in ks]
    if clamp:
        out = [min(1.0, max(0.0, p)) for p in out]
    return out


def is_monotone(ks, ps, tol=1e-12):
    """True when p never rises with k (ties in k must agree to `tol`)."""
    pairs = sorted(zip(ks, ps))
    return all(b[1] <= a[1] + tol for a, b in zip(pairs, pairs[1:]))


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------
def _clean(xs, ws):
    xs = [float(x) for x in xs]
    if ws is None:
        ws = [1.0] * len(xs)
    else:
        ws = [float(w) for w in ws]
        if len(ws) != len(xs):
            raise ValueError("weights and sample differ in length")
    return xs, ws


def _wstats(xs, ws):
    """(n_eff, weighted mean, unbiased reliability-weighted variance)."""
    w1 = sum(ws)
    w2 = sum(w * w for w in ws)
    m = sum(w * x for w, x in zip(ws, xs)) / w1
    ss = sum(w * (x - m) ** 2 for w, x in zip(ws, xs))
    denom = w1 - w2 / w1
    var = ss / denom if denom > 0 else 0.0
    return (w1 * w1 / w2), m, var


def _degenerate(xs, ws, min_n, need_int=False):
    """Reason string if the sample cannot support a dispersion fit, else None."""
    if len(xs) < min_n:
        return f"n={len(xs)} < min_n={min_n}"
    if any((not math.isfinite(x)) for x in xs) or any((not math.isfinite(w)) or w <= 0 for w in ws):
        return "non-finite value or non-positive weight"
    if any(x < 0 for x in xs):
        return "negative count"
    if need_int and any(abs(x - round(x)) > 1e-9 for x in xs):
        return "non-integer count"
    n_eff, m, v = _wstats(xs, ws)
    if m <= 0.0:
        return "all zeros"
    if n_eff < 2.0 or len(set(xs)) < 2:
        return "constant sample"
    return None


def negbin_fit_mom(xs, ws=None, min_n=5):
    """NegBin dispersion by method of moments: alpha = (s^2 - m) / m^2.

    Returns {"ok", "alpha", "mu", "var", "n", "n_eff", "underdispersed",
    "method": "mom", "reason"}. A sample with variance at or below its mean
    gives alpha = 0.0 (Poisson) and underdispersed=True -- a negative alpha is
    not a negative binomial. Degenerate samples (too few, constant, all zero,
    negative, non-finite) return ok=False with a reason and alpha None."""
    xs, ws = _clean(xs, ws)
    why = _degenerate(xs, ws, min_n)
    if why:
        return {"ok": False, "alpha": None, "mu": None, "var": None, "n": len(xs), "n_eff": None,
                "underdispersed": None, "method": "mom", "reason": why}
    n_eff, m, v = _wstats(xs, ws)
    under = v <= m
    return {"ok": True, "alpha": 0.0 if under else (v - m) / (m * m), "mu": m, "var": v,
            "n": len(xs), "n_eff": n_eff, "underdispersed": under, "method": "mom", "reason": ""}


def negbin_loglik(xs, mu, alpha, ws=None):
    """Weighted NegBin log-likelihood of integer counts at (mu, alpha)."""
    xs, ws = _clean(xs, ws)
    d = NegBin(mu, alpha)
    total = 0.0
    for x, w in zip(xs, ws):
        p = d.pmf(int(round(x)))
        total += w * (math.log(p) if p > 0 else -1e300)
    return total


def negbin_fit_mle(xs, ws=None, min_n=5, alpha_min=1e-4, alpha_max=25.0, iters=200):
    """NegBin dispersion by maximum likelihood, mean profiled out.

    For fixed alpha the mean MLE is the sample mean m, which leaves one score
    equation in r = 1/alpha:

        s(r) = sum_j T_j / (r + j)  +  W ln(r / (r + m)),   T_j = sum of weights
                                                            of observations > j

    (the digamma differences psi(x+r) - psi(r) collapse to sum_{j<x} 1/(r+j)
    for integer counts, which is exact and stable). The root is bracketed on
    [1/alpha_max, 1/alpha_min] and bisected in log r; if s does not change sign
    the optimum sits on a bound and the result says so (`at_bound`: "alpha_min"
    means indistinguishable from Poisson, "alpha_max" means the sample is more
    dispersed than the allowed range). alpha_min is 1e-4, not smaller, because
    the score is O(1/r^2) there and drowns in rounding.

    Same return shape as negbin_fit_mom plus "loglik", "at_bound". Integer
    counts only (a non-integer sample is degenerate here, not rounded)."""
    xs, ws = _clean(xs, ws)
    why = _degenerate(xs, ws, min_n, need_int=True)
    res = {"ok": False, "alpha": None, "mu": None, "var": None, "n": len(xs), "n_eff": None,
           "underdispersed": None, "method": "mle", "reason": "", "loglik": None, "at_bound": None}
    if why:
        res["reason"] = why
        return res
    n_eff, m, v = _wstats(xs, ws)
    res.update(mu=m, var=v, n_eff=n_eff)
    W = sum(ws)
    top = int(max(xs))
    tail = [0.0] * (top + 1)         # tail[j] = weight of observations with x > j
    for x, w in zip(xs, ws):
        xi = int(round(x))
        if xi >= 1:
            tail[xi - 1] += w
    for j in range(top - 1, -1, -1):
        tail[j] += tail[j + 1] if j + 1 <= top else 0.0

    def score(r):
        s = sum(t / (r + j) for j, t in enumerate(tail))
        return s + W * math.log(r / (r + m))

    r_lo, r_hi = 1.0 / alpha_max, 1.0 / alpha_min
    s_lo, s_hi = score(r_lo), score(r_hi)
    res["ok"] = True
    if s_hi >= 0.0:                  # still climbing at r_hi: Poisson-like
        res.update(alpha=0.0, underdispersed=True, at_bound="alpha_min")
    elif s_lo <= 0.0:                # already falling at r_lo: more dispersed than allowed
        res.update(alpha=alpha_max, underdispersed=False, at_bound="alpha_max")
    else:
        a, b = math.log(r_lo), math.log(r_hi)
        for _ in range(iters):
            mid = 0.5 * (a + b)
            if score(math.exp(mid)) > 0.0:
                a = mid
            else:
                b = mid
            if b - a < 1e-12:
                break
        else:
            errlog.note("PDIST-mle", msg="bisection used all iterations")
        res.update(alpha=1.0 / math.exp(0.5 * (a + b)), underdispersed=False, at_bound=None)
    res["loglik"] = negbin_loglik(xs, m, res["alpha"], ws)
    return res


def shrink_alpha(alpha_hat, n_eff, alpha_prior, k=30.0):
    """Empirical-Bayes style shrinkage of a player's dispersion toward a prior
    (league / position) value: (n_eff * alpha_hat + k * alpha_prior) / (n_eff + k).

    Linear on the alpha scale. `k` is the pseudo-sample weight of the prior and
    MUST come from a measured reliability, not from this docstring -- the
    default 30 is a placeholder that makes a 10-game sample count a quarter of
    its own value, nothing more."""
    if alpha_hat is None:
        return alpha_prior
    return (n_eff * alpha_hat + k * alpha_prior) / (n_eff + k)


# ---------------------------------------------------------------------------
# Correlated sums: Gaussian copula over given marginals
# ---------------------------------------------------------------------------
def _cholesky_psd(corr, tol=1e-10):
    """Lower-triangular L with L L^T = corr for a symmetric PSD matrix. A
    (near) zero pivot zeroes its column, so perfectly correlated pairs
    (rho = 1) factor cleanly; a clearly negative pivot is not PSD."""
    d = len(corr)
    for i in range(d):
        if abs(corr[i][i] - 1.0) > 1e-9:
            raise ValueError("correlation matrix must have a unit diagonal")
        for j in range(i):
            if abs(corr[i][j] - corr[j][i]) > 1e-9:
                raise ValueError("correlation matrix must be symmetric")
            if abs(corr[i][j]) > 1.0 + 1e-9:
                raise ValueError("correlation outside [-1, 1]")
    L = [[0.0] * d for _ in range(d)]
    for i in range(d):
        for j in range(i + 1):
            s = corr[i][j] - sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                if s < -1e-8:
                    raise ValueError("correlation matrix is not positive semi-definite")
                L[i][i] = math.sqrt(s) if s > tol else 0.0
            else:
                L[i][j] = (s / L[j][j]) if L[j][j] > 0.0 else 0.0
    return L


def copula_draws(marginals, corr=None, n=20000, seed=12345, tol=1e-12):
    """n draws of one value per marginal, dependent through a Gaussian copula.

    `marginals`  list of Dist (anything with pmf()), one per component.
    `corr`       d x d correlation of the LATENT normals (default identity =
                 independent). It is NOT the Pearson correlation of the counts:
                 for discrete marginals that is somewhat smaller in magnitude,
                 and the gap grows as the means shrink. Calibrate rho to the
                 count correlation you measured, don't assume they are equal.
    A marginal whose table cannot be built (cdf_table raises: unbounded below or
    out past kmax) makes this raise rather than sample a wrong distribution.
    Seeded (random.Random(seed)): the same call returns the same draws.
    Cost is ~n * d * 2 microseconds-ish; 20,000 x 3 is a fraction of a second --
    still a job for a precompute pass, not a request handler's inner loop."""
    d = len(marginals)
    if d == 0:
        raise ValueError("need at least one marginal")
    if corr is None:
        corr = [[1.0 if i == j else 0.0 for j in range(d)] for i in range(d)]
    if len(corr) != d or any(len(r) != d for r in corr):
        raise ValueError("corr must be d x d")
    L = _cholesky_psd(corr)
    tables = [m.cdf_table(tol=tol) for m in marginals]
    rng = random.Random(seed)
    gauss = rng.gauss
    out = []
    for _ in range(int(n)):
        g = [gauss(0.0, 1.0) for _ in range(d)]
        row = []
        for i in range(d):
            z = 0.0
            Li = L[i]
            for j in range(i + 1):
                z += Li[j] * g[j]
            u = norm_cdf(z)
            row.append(bisect.bisect_left(tables[i], u))
        out.append(tuple(row))
    return out


def _mc_p(hits, n):
    p = hits / n
    # A zero (or full) count has a binomial SE of exactly 0, which would claim
    # certainty from a finite sample; use the add-one proportion for the SE.
    q = (hits + 1.0) / (n + 2.0) if hits in (0, n) else p
    return {"p": p, "se": math.sqrt(q * (1.0 - q) / n), "n": n}


def sum_threshold_probs(draws, ks, weights=None):
    """{k: {"p", "se", "n"}} for P(sum_i w_i X_i >= k) over copula draws.

    `weights` (default all 1) lets one pool build "points + 1.2 * rebounds"
    style indices; for PRA / PR / PA / RA they are 1 and 0. SE is the binomial
    sqrt(p (1-p) / n) of the Monte Carlo itself -- simulation error only, not
    model error."""
    n = len(draws)
    if n == 0:
        raise ValueError("no draws")
    w = list(weights) if weights is not None else [1.0] * len(draws[0])
    sums = sorted(sum(wi * xi for wi, xi in zip(w, row)) for row in draws)
    out = {}
    for k in ks:
        hits = n - bisect.bisect_left(sums, k - 1e-9)
        out[k] = _mc_p(hits, n)
    return out


def joint_threshold_prob(draws, thresholds):
    """P(X_i >= t_i for every i with t_i not None) over copula draws, e.g.
    receptions >= 5 AND yards >= 60. Returns {"p", "se", "n"}."""
    n = len(draws)
    if n == 0:
        raise ValueError("no draws")
    hits = 0
    for row in draws:
        ok = True
        for x, t in zip(row, thresholds):
            if t is not None and x < t:
                ok = False
                break
        hits += ok
    return _mc_p(hits, n)


def copula_sum_probs(marginals, corr, ks, n=20000, seed=12345, weights=None):
    """One call: draw from the copula and return sum_threshold_probs."""
    return sum_threshold_probs(copula_draws(marginals, corr, n=n, seed=seed), ks, weights)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
def _check_py(ps, ys):
    ps, ys = list(ps), list(ys)
    if len(ps) != len(ys) or not ps:
        raise ValueError("probabilities and outcomes must be non-empty and equal length")
    if any((not (0.0 <= p <= 1.0)) for p in ps):
        raise ValueError("probability outside [0, 1]")
    if any(y not in (0, 1) for y in ys):
        raise ValueError("outcomes must be 0 or 1")
    return ps, ys


def brier(ps, ys):
    """Mean squared error of the probabilities (lower is better; always-0.5 = 0.25)."""
    ps, ys = _check_py(ps, ys)
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def log_loss(ps, ys, eps=1e-12):
    """Mean negative log-likelihood in nats, probabilities clipped to [eps, 1-eps]
    so one confident miss costs ~27.6 nats, not infinity (always-0.5 = ln 2)."""
    ps, ys = _check_py(ps, ys)
    tot = 0.0
    for p, y in zip(ps, ys):
        p = min(1.0 - eps, max(eps, p))
        tot -= math.log(p) if y else math.log(1.0 - p)
    return tot / len(ps)


def brier_skill(ps, ys):
    """1 - Brier / Brier(base rate): positive beats predicting the sample's own
    hit rate for everything. None when the outcomes are all one class."""
    ps, ys = _check_py(ps, ys)
    base = sum(ys) / len(ys)
    ref = base * (1.0 - base)
    return None if ref == 0.0 else 1.0 - brier(ps, ys) / ref


def reliability_table(ps, ys, bins=10, strategy="uniform"):
    """Reliability (calibration) table: one row per non-empty bin with
    {"lo", "hi", "n", "mean_p", "freq", "gap"}, gap = freq - mean_p (positive =
    the model was too low). strategy "uniform" cuts [0, 1] into equal-width
    bins (1.0 falls in the last); "quantile" cuts into bins of equal count."""
    ps, ys = _check_py(ps, ys)
    n = len(ps)
    if bins < 1:
        raise ValueError("bins must be >= 1")
    if strategy == "uniform":
        groups = [[] for _ in range(bins)]
        for p, y in zip(ps, ys):
            groups[min(bins - 1, int(p * bins))].append((p, y))
        edges = [(i / bins, (i + 1) / bins) for i in range(bins)]
    elif strategy == "quantile":
        order = sorted(zip(ps, ys))
        groups, edges = [], []
        for b in range(bins):
            seg = order[b * n // bins:(b + 1) * n // bins]
            if seg:
                groups.append(seg)
                edges.append((seg[0][0], seg[-1][0]))
    else:
        raise ValueError("strategy must be 'uniform' or 'quantile'")
    rows = []
    for g, (lo, hi) in zip(groups, edges):
        if not g:
            continue
        mp = sum(p for p, _ in g) / len(g)
        fr = sum(y for _, y in g) / len(g)
        rows.append({"lo": lo, "hi": hi, "n": len(g), "mean_p": mp, "freq": fr, "gap": fr - mp})
    return rows


def ece(ps, ys, bins=10, strategy="uniform"):
    """Expected calibration error: sum_b (n_b / N) |freq_b - mean_p_b|. A
    small-sample ECE is biased upward (each bin's freq is noisy): read it next
    to the bin counts, never alone."""
    rows = reliability_table(ps, ys, bins, strategy)
    n = sum(r["n"] for r in rows)
    return sum(r["n"] * abs(r["gap"]) for r in rows) / n


def wilson_interval(hits, n, z=1.96):
    """Wilson score interval (lo, hi) for a hit rate of `hits` out of `n`;
    (0.0, 1.0) when n == 0 (no information is the full interval)."""
    if n <= 0:
        return 0.0, 1.0
    if not (0 <= hits <= n):
        raise ValueError("hits must be within [0, n]")
    p = hits / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)
