"""RB and QB rushing: the second phase of the NFL matchup engine
(research/reports/nfl_rush_model.md holds the design and the pre-registered
rule). Pure Python: the web app imports it, so no numpy.

What it builds, per running back and quarterback, on top of Sleeper's line:

- OPPORTUNITIES separately from EFFICIENCY. A running back's rushing yards are
  a compound: carries ~ NegBin(mean, alpha(mean)) times per-carry yards drawn
  from a measured kernel rescaled to his yards per carry. A quarterback's are
  four compounds added: designed runs, sneaks, scrambles and kneels, each a
  count times its own kernel. Kneels are official rushing attempts at about
  -1 yard each (Sleeper's box counted them on 504 of 504 QB-games with a kneel,
  2023-24); sacks are not rushing. An RB's receiving yards are receptions x a
  per-catch kernel; rush + rec adds the two.
- The distribution is EXACT for the model (no normal, no moment match): the
  components are convolved through a radix-2 FFT on an integer-yard grid, so
  the explosive tails, the kneel mass below zero and a backup's real chance of
  a dud all come from the kernels. On 2023 -> 2024 (inside the fitting
  seasons) this shape beat the shipped point-mass + single-CV gamma by -0.090
  +/- 0.015 nats a row for RBs and -0.167 +/- 0.040 for QBs
  (research/nfl_rush_model_explore.py); whether it ships is the rule's
  question, decided on 2025 and kept or reverted on 2026 weeks 8-11.
- A per-player `breakdown` (expected carries, designed runs, scrambles,
  kneels, yards per attempt by component, ...) for the tab.
- The within-team correlations the combo maker can price with.

Nothing here moves a probability on the board until the committed model
(research/data/nfl_rush_model/coefs.json, written only by the
pre-registered rule's export) says the market's group shipped; with no file
the module is inert and nfl_matchup's board is unchanged.
"""
import cmath
import json
import math
import os

import errlog
import prop_dist as PD

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(HERE, "research", "data", "nfl_rush_model")
COEF_PATH = os.path.join(MODEL_DIR, "coefs.json")
SCHEMA = 1

# the integer-yard grid every compound lives on: values LO .. LO + N - 1. A
# value outside wraps around (the transform is circular), so the grid must
# hold every plausible game: the most rushing + receiving yards an RB has had
# in 2016-2025 is under 400, and the lowest a QB's rushing under -15.
GRID_N = 1024
GRID_LO = -128

# the markets this module prices, under the stat names the board already prints
# (nfl_matchup.STAT_NAME; Kalshi's KXNFLRSHYDS and KXNFLRSHATT list RBs and QBs
# alike), with each position's scored thresholds (the draft rule's Q4)
RB_MARKETS = ("rush_att", "rush_yd", "rec", "rec_yd", "rush_rec_yd", "td", "long_rush")
# a QB's "td" is Kalshi's KXNFLTD: passing TDs do not count (its contract text), so it is
# his rushing TDs plus the odd receiving one
QB_MARKETS = ("rush_att", "rush_yd", "td", "long_rush")
THRESHOLDS = {
    "RB": {"rush_att": (5, 10, 15, 20), "rush_yd": (10, 15, 25, 40, 50, 60, 75, 100), "rec": (1, 2, 3, 4, 5),
           "rec_yd": (10, 15, 25, 40, 50), "rush_rec_yd": (25, 40, 50, 60, 75, 100, 125), "td": (1, 2),
           "long_rush": (10, 15, 20, 25, 30, 40)},
    "QB": {"rush_att": (2, 4, 6, 8), "rush_yd": (10, 15, 25, 40, 50, 60), "td": (1,),
           "long_rush": (10, 15, 20, 25, 30, 40)}}
# Every Kalshi series this module prices for RBs and QBs (research/reports/rush_data_sources.md 9,
# read 2026-10-23): series -> (market, positions it lists). Each rung of floor strike X pays on
# stat >= ceil(X) (19.5 -> 20+). Left UNPRICED on purpose (the orchestrator agreed, 2026-10-23):
# - the PROTECTED variants (KXNFL...PROT): a player who leaves after the first half resolves at
#   the last fair price, so their YES is not P(stat >= k) -- pricing it needs an early-exit model;
# - the ladders and escalators (KXNFLLADDERRSHYDS, KXNFLESCALATORRSHYDS; the receiving ones too):
#   SCALAR payouts, not rungs (FOOTBALLENTITYSCALARSTAT; rush_data_sources.md 9) -- the ladder pays
#   $0.0025 a rushing yard capped at 400 yards, the escalator floor(10000 x (y / 200)^3) / 10000 on
#   y = min(200, max(0, 10 x floor(yards / 10))), so pricing one is an expectation over the whole
#   distribution, not a P(stat >= k).
# KXNFLANYTD and KXNFLRSHYDSH2H were listed with nothing open.
SERIES = {
    "KXNFLRSHYDS": ("rush_yd", ("RB", "QB")),
    "KXNFLRSHATT": ("rush_att", ("RB", "QB")),
    "KXNFLRRYDS": ("rush_rec_yd", ("RB",)),       # no QB rungs; WRs are the first model's
    "KXNFLLONGRSH": ("long_rush", ("RB", "QB")),
    "KXNFLTD": ("td", ("RB", "QB")),              # passing TDs do not count
    "KXNFLREC": ("rec", ("RB",)),
    "KXNFLRECYDS": ("rec_yd", ("RB",)),
}


def strike_k(strike):
    """The integer k with YES <=> stat >= k for an "at least" rung's floor strike."""
    return int(math.ceil(float(strike)))


# the kernels a coefficient file carries, by role
ROLES = ("rb_run", "rb_catch", "qb_designed", "qb_sneak", "qb_scramble", "kneel")
QB_PARTS = ("designed", "sneak", "scramble", "kneel")


# ---- the transform ------------------------------------------------------------------------
_FFT = {}


def _plan(n):
    p = _FFT.get(n)
    if p is None:
        bits = n.bit_length() - 1
        if 1 << bits != n:
            raise ValueError("FFT size must be a power of two")
        rev = [int(format(i, f"0{bits}b")[::-1], 2) for i in range(n)]
        tw = [cmath.exp(-2j * math.pi * k / n) for k in range(n // 2)]
        p = _FFT[n] = (rev, tw)
    return p


def fft(a, inverse=False):
    """Iterative radix-2 DFT (inverse scaled by 1/n). 1024 points ~1.7 ms."""
    n = len(a)
    rev, tw = _plan(n)
    a = [a[r] for r in rev]
    size = 2
    while size <= n:
        half, step = size // 2, n // size
        for st in range(0, n, size):
            k = 0
            for j in range(st, st + half):
                w = tw[k].conjugate() if inverse else tw[k]
                t = w * a[j + half]
                u = a[j]
                a[j] = u + t
                a[j + half] = u - t
                k += step
        size *= 2
    if inverse:
        a = [x / n for x in a]
    return a


# ---- kernels ------------------------------------------------------------------------------
def kernel_mean(kern):
    lo, p = kern["lo"], kern["p"]
    return sum((lo + i) * q for i, q in enumerate(p))


def kernel_var(kern):
    m = kernel_mean(kern)
    lo, p = kern["lo"], kern["p"]
    return sum((lo + i - m) ** 2 * q for i, q in enumerate(p))


def rescale(kern, target_mean, floor=0.05):
    """The kernel with every yard value x moved to s x, s = target / mean,
    each value's mass split between the two integers around s x in the
    proportion that keeps the mean EXACT (a rounded rescale shifts it). The
    efficiency terms act here: a better matchup stretches the whole
    per-attempt distribution, the explosive tail with it -- the shape that
    measured 2023 RB runs (skew 4.2) keeps. A kernel whose mean is not
    positive (kneels) is returned unchanged; s is floored at `floor`."""
    m0 = kernel_mean(kern)
    if m0 <= 0 or target_mean is None:
        return kern
    s = max(floor, float(target_mean) / m0)
    if abs(s - 1.0) < 1e-12:
        return kern
    lo, p = kern["lo"], kern["p"]
    xs = [(lo + i) * s for i in range(len(p))]
    nlo = int(math.floor(min(xs)))
    nhi = int(math.floor(max(xs))) + 1
    out = [0.0] * (nhi - nlo + 1)
    for x, q in zip(xs, p):
        if q <= 0:
            continue
        f = math.floor(x)
        fr = x - f
        out[int(f) - nlo] += q * (1.0 - fr)
        out[int(f) - nlo + 1] += q * fr
    return {"lo": nlo, "p": out}


def unit_kernel():
    """Every attempt counts 1: the compound of counts is the total count."""
    return {"lo": 1, "p": [1.0]}


def alpha_of(curve, mu):
    """A count's over-dispersion at its mean: alpha = a x max(mu, 0.5)^b,
    clamped to [0, cap]. Measured falling with the line (2023 RB carries:
    0.29 at 5-7 projected, 0.06 at 15+), so one alpha a position was too thin
    for backups -- the review's F3 cell."""
    if not curve:
        return 0.0
    a = curve.get("a", 0.0) * max(float(mu), 0.5) ** curve.get("b", 0.0)
    return max(0.0, min(curve.get("cap", 2.0), a))


def _nb_pgf(z, mu, alpha):
    """E[z^N] for N ~ NegBin(mu, alpha) (alpha 0 -> Poisson). For |z| <= 1
    the base 1 + alpha mu (1 - z) has a positive real part, so the principal
    power is the continuous branch."""
    if mu <= 0:
        return 1.0 + 0j
    if alpha < 1e-9:
        return cmath.exp(mu * (z - 1.0))
    return (1.0 + alpha * mu * (1.0 - z)) ** (-1.0 / alpha)


# ---- the compound distribution ------------------------------------------------------------
# A compound whose grid wrapped is refused (`_unwrapped`). The mass a wrap folds onto the bottom is what
# a priced probability can move by, and it runs 1-2x the mass in the grid's top WRAP_TOP values (measured
# against an 8,192-value grid on the 2023-24 RB kernel: 30 carries / 150 yards at ypc x e holds 1.9e-3 in the
# top 32 and 3.9e-3 past 895, every ladder off by up to 3.8e-3; 12 carries / 66 yards x e, 2.5e-5 and
# 4.1e-5). WRAP_TAIL bounds that error near 2e-5. Lines of 5-30 carries at 2.5-5.5 yards a carry and a
# factor up to 1.25 hold at most 1.6e-8 there: no realistic market is refused.
WRAP_TOP = 32
WRAP_TAIL = 1e-5


def wrap_flag(grid_mean, mean, top_mass):
    """True when a compound's grid wrapped: its grid mean is off the analytic one by more than 0.5 yards
    or 1% of the mean (the larger), or the grid's top WRAP_TOP values hold more than WRAP_TAIL."""
    return abs(grid_mean - mean) > max(0.5, 0.01 * abs(mean)) or top_mass > WRAP_TAIL


class Compound(PD.Dist):
    """S = sum over components j of the sum of N_j draws from kernel_j, with
    N_j ~ NegBin(count_mean_j, alpha_j), plus any independent `extra` pmfs
    (e.g. receiving yards beside rushing), every part independent of the
    others. The pmf is exact on the integer grid up to floating point: one
    transform per part, the NegBin generating function applied pointwise,
    one inverse transform.

    components: [{"role", "count_mean", "alpha", "kernel": {"lo", "p"}}]
    extra:      [{"role", "pmf": {"lo", "p"}, "mean", "var"}]"""
    family = "compound"

    def __init__(self, components, extra=(), n=GRID_N, lo=GRID_LO):
        self.components = [dict(c) for c in components]
        self.extra = [dict(e) for e in extra]
        self.n, self.lo = int(n), int(lo)
        F = [1.0 + 0j] * self.n
        for c in self.components:
            mu = float(c["count_mean"])
            if mu <= 0:
                continue
            X = fft(self._place(c["kernel"]))
            a = float(c.get("alpha") or 0.0)
            F = [f * _nb_pgf(x, mu, a) for f, x in zip(F, X)]
        for e in self.extra:
            X = fft(self._place(e["pmf"]))
            F = [f * x for f, x in zip(F, X)]
        raw = fft(F, inverse=True)
        pm = [0.0] * self.n            # by VALUE: pm[v - lo]
        for v in range(self.lo, self.lo + self.n):
            pm[v - self.lo] = max(0.0, raw[v % self.n].real)
        tot = sum(pm)
        self.p = [q / tot for q in pm] if tot > 0 else pm
        self.mass_raw = tot            # ~1 ALWAYS: a circular convolution keeps its mass, so this cannot see a wrap
        suf, s = [0.0] * (self.n + 1), 0.0
        for i in range(self.n - 1, -1, -1):
            s += self.p[i]
            suf[i] = s
        self._suf = suf
        # the wrap detector (review m3 / m5; Fable's m1): mass that ran past the grid's top re-enters at
        # the bottom. Two reads, either refuses (wrap_flag): the grid's mean against the analytic one (0.5
        # yards or 1% of the mean, the larger: far above floating point, 1e-12 on every real line), and
        # the mass in the grid's top WRAP_TOP values, read directly -- the mean test alone missed 30
        # carries / 150 yards at ypc x e (3.9e-3 past the top, the mean 3.9 yards low, under its 4.1)
        gm, am = self.grid_mean(), self.mean()
        self.top_mass = sum(self.p[-WRAP_TOP:])
        self.wrapped = wrap_flag(gm, am, self.top_mass)
        if self.wrapped:
            errlog.note("NFLR-grid-wrap", msg=f"analytic mean {am:.2f} vs grid mean {gm:.2f}, top-of-grid mass "
                                              f"{self.top_mass:.2e}: refused")

    def _place(self, kern):
        x = [0j] * self.n
        for i, q in enumerate(kern["p"]):
            if q:
                x[(kern["lo"] + i) % self.n] += q
        return x

    def sf_ge(self, k):
        i = int(math.ceil(k)) - self.lo
        if i <= 0:
            return 1.0
        if i >= self.n:
            return 0.0
        return min(1.0, self._suf[i])

    def pmf(self, k):
        i = int(k) - self.lo
        return self.p[i] if 0 <= i < self.n else 0.0

    def mean(self):
        """The model's mean, analytic (not the grid's)."""
        m = 0.0
        for c in self.components:
            m += float(c["count_mean"]) * kernel_mean(c["kernel"])
        for e in self.extra:
            m += float(e["mean"])
        return m

    def var(self):
        v = 0.0
        for c in self.components:
            mu, a = float(c["count_mean"]), float(c.get("alpha") or 0.0)
            km, kv = kernel_mean(c["kernel"]), kernel_var(c["kernel"])
            v += mu * kv + km * km * (mu + a * mu * mu)
        for e in self.extra:
            v += float(e["var"])
        return v

    def grid_mean(self):
        return sum((self.lo + i) * q for i, q in enumerate(self.p))

    def to_dict(self):
        return {"family": self.family,
                "params": {"components": [{"role": c.get("role"), "count_mean": round(float(c["count_mean"]), 4),
                                           "count_alpha": round(float(c.get("alpha") or 0.0), 4),
                                           "per_attempt_mean": round(kernel_mean(c["kernel"]), 4)}
                                          for c in self.components],
                           "extra": [{"role": e.get("role"), "mean": round(float(e["mean"]), 4)} for e in self.extra],
                           "grid": {"lo": self.lo, "n": self.n}}}


class Longest(PD.Dist):
    """The longest single attempt among a player's attempts (Kalshi lists
    KXNFLLONGRSH): with N_j attempts of component j drawn from kernel_j,
    P(longest < k) = prod_j G_j(P(X_j < k)), G_j the count's generating
    function -- the same parts the yards compound uses, so the two can never
    disagree about a player's attempts. A player with no attempt has no
    longest run: P(longest >= k) counts it as a miss for every k >= 1."""
    family = "longest_of_compound"

    def __init__(self, components):
        self.components = [dict(c) for c in components]

    def _cdf_lt(self, k):
        out = 1.0
        for c in self.components:
            mu = float(c["count_mean"])
            if mu <= 0:
                continue
            lo, p = c["kernel"]["lo"], c["kernel"]["p"]
            below = sum(q for i, q in enumerate(p) if lo + i < k)
            out *= _nb_pgf(complex(below), mu, float(c.get("alpha") or 0.0)).real
        return out

    def sf_ge(self, k):
        return max(0.0, min(1.0, 1.0 - self._cdf_lt(k)))

    def pmf(self, k):
        return max(0.0, self.sf_ge(k) - self.sf_ge(k + 1))

    def mean(self):
        return None

    def var(self):
        return None

    def to_dict(self):
        return {"family": self.family, "params": {"components": [
            {"role": c.get("role"), "count_mean": round(float(c["count_mean"]), 4)} for c in self.components]}}


def pmf_of(dist, lo=0, hi=400):
    """An independent part for Compound's `extra` from any prop_dist family
    on the integers lo..hi (receiving yards beside rushing)."""
    sf = [dist.sf_ge(k) for k in range(lo, hi + 2)]
    p = [max(0.0, sf[i] - sf[i + 1]) for i in range(hi - lo + 1)]
    return {"lo": lo, "p": p}


# ---- the committed model ------------------------------------------------------------------
_COEFS = {"mtime": None, "data": None}


def load_coefs(path=None):
    """research/data/nfl_rush_model/coefs.json, or None (missing, unreadable
    or another schema): with None nothing here touches the board."""
    p = path or COEF_PATH
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return None
    if path is None and _COEFS["mtime"] == mt:
        return _COEFS["data"]
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        errlog.note("NFLR-coefs", e)
        return None
    if data.get("schema") != SCHEMA:
        errlog.note("NFLR-coefs", msg=f"schema {data.get('schema')} != {SCHEMA}")
        return None
    why = record_chain(data, os.path.dirname(os.path.abspath(p)))
    if why:
        errlog.note("NFLR-coefs", msg=f"coefs.json refused: {why}")
        return None
    if path is None:
        _COEFS.update(mtime=mt, data=data)
    return data


def record_chain(data, folder):
    """Why a coefs.json's shipping statuses do not trace to the decision records beside it, or None
    (the re-check's R4: the board read only the status strings, so a re-export after the
    prospective lock would have priced the reverted groups). Every status must be one stage's:
    "provisional" needs prereg.json, validate.json and check2026.json to hash to the records;
    "shipped" needs prospective_lock.json as well. A file that ships nothing needs nothing."""
    import hashlib
    sts = {g.get("status") for gs in (data.get("shipped") or {}).values() for g in (gs or []) if isinstance(g, dict)}
    sts &= set(SHIP_STATUSES)
    if not sts:
        return None
    if len(sts) > 1:
        return "it mixes provisional and shipped statuses"
    if "provisional" in sts and os.path.exists(os.path.join(folder, "prospective_lock.json")):
        # the prospective test has run: a provisional file beside its lock is an older stage restored (one
        # revert on an auto-deploying branch), whose groups the test may have reverted (the confirmation's C3)
        return "it is a provisional stage beside the prospective lock"
    need = [("prereg_json_sha256", "prereg.json"), ("validate_sha256", "validate.json"),
            ("check2026_sha256", "check2026.json")]
    if "shipped" in sts:
        need.append(("prospective_sha256", "prospective_lock.json"))
    rec = data.get("records") or {}
    for key, name in need:
        want = rec.get(key)
        if not want:
            return f"its records carry no {key}"
        try:
            with open(os.path.join(folder, name), "rb") as fh:
                got = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            return f"{name} is not beside it"
        if got != want:
            return f"{name} does not hash to records.{key}"
    # the CONTENT too: the shipped groups must be exactly the decision record's set -- check2026's
    # provisional set at that stage, the lock's kept set once shipped -- or a hand-built file citing
    # the genuine records' hashes could list groups none of them kept (the confirmation's C-m1)
    try:
        with open(os.path.join(folder, "prospective_lock.json" if "shipped" in sts else "check2026.json"),
                  encoding="utf-8") as fh:
            states = json.load(fh).get("kept" if "shipped" in sts else "provisional") or {}
    except (OSError, ValueError) as e:
        return f"its decision record cannot be read ({e!r})"
    want_sets = {f"{p_}:{m}": sorted(gs) for p_, ms in states.items() for m, gs in (ms or {}).items() if gs}
    got_sets = {k: sorted(g.get("group") for g in gs if isinstance(g, dict))
                for k, gs in (data.get("shipped") or {}).items() if gs}
    if got_sets != want_sets:
        return "its shipped groups are not the decision record's set"
    return None


# The only statuses that ship (the rule's Q5 / Q6): "provisional" (passed 2025 and the 2026 weeks
# 1-6 check, awaiting the prospective window) and "shipped" (kept by the one prospective test). The
# gate fails CLOSED: any other status -- "reverted", "failed", None, a typo -- reads as not shipped
# and is ledgered (it used to read "shipped": review M6 / M7).
SHIP_STATUSES = ("shipped", "provisional")
_BAD_STATUS = set()


def shipped_groups(coefs, market):
    """The market's shipped group entries whose status is one the rule writes; the rest dropped."""
    out = []
    for g in (((coefs or {}).get("shipped") or {}).get(market) or []):
        st = g.get("status") if isinstance(g, dict) else None
        if st in SHIP_STATUSES and isinstance(g.get("group"), str):
            out.append(g)
            continue
        key = (market, repr(g)[:80])
        if key not in _BAD_STATUS:
            _BAD_STATUS.add(key)
            errlog.note("NFLR-coefs", msg=f"{market}: shipped entry {g!r} has no shipping status: read as not shipped")
    return out


def ship_status(coefs, market):
    """"shipped" (kept by the prospective test), "provisional" (passed 2025
    and the 2026 weeks 1-6 check, awaiting the prospective window) or "0"."""
    sh = shipped_groups(coefs, market)
    if not sh:
        return "0"
    return "provisional" if any(g["status"] == "provisional" for g in sh) else "shipped"


def uses_compound(coefs, market):
    """Whether the market's probabilities come from the compound: its SHAPE
    group (the decomposition with no new term, Q4) shipped."""
    return any(g["group"] == "SHAPE" for g in shipped_groups(coefs, market))


# ---- running backs ------------------------------------------------------------------------
def _pos(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) and x > 0 else 0.0


def rb_parts(base, coefs, mult=None):
    """The RB's components from Sleeper's line `base` {rush_att, rush_yd, rec,
    rec_yd, rush_td, rec_td} and the matchup multipliers `mult` (exp of each
    shipped term's contribution; 1 at weight 0):
      carries   = Sleeper's carries x mult["carries"]       (opportunity)
      ypc       = Sleeper's yds / carries x mult["ypc"]      (efficiency)
      receptions, yards per catch likewise ("rec", "ypr").
    At weight 0 the means ARE Sleeper's (a guard pins it). None when there
    is no carries line."""
    mult = mult or {}
    k = (coefs or {}).get("kernels") or {}
    cur = (coefs or {}).get("alpha") or {}
    c, y = _pos(base.get("rush_att")), _pos(base.get("rush_yd"))
    r, ry = _pos(base.get("rec")), _pos(base.get("rec_yd"))
    out = {"rush": None, "rec": None}
    if c > 0 and y > 0 and "rb_run" in k:
        mu = c * mult.get("carries", 1.0)
        ypc = y / c * mult.get("ypc", 1.0)
        out["rush"] = {"role": "rb_run", "count_mean": mu, "alpha": alpha_of(cur.get("rb_carries"), mu),
                       "kernel": rescale(k["rb_run"], ypc), "ypc": ypc}
    if r > 0 and ry > 0 and "rb_catch" in k:
        mu = r * mult.get("rec", 1.0)
        ypr = ry / r * mult.get("ypr", 1.0)
        out["rec"] = {"role": "rb_catch", "count_mean": mu, "alpha": alpha_of(cur.get("rb_receptions"), mu),
                      "kernel": rescale(k["rb_catch"], ypr), "ypr": ypr}
    return out


def rb_dists(base, coefs, mult=None):
    """{market: dist} for an RB: carries, rushing yards, receptions, receiving
    yards, rushing + receiving yards (the two independent: their residuals
    correlated -0.023 on 2023-24 RB games, research/nfl_rush_model_explore.py
    G), anytime TD (Poisson)."""
    parts = rb_parts(base, coefs, mult)
    mult = mult or {}
    out = {}
    ru, rc = parts["rush"], parts["rec"]
    if ru:
        out["rush_att"] = PD.NegBin(ru["count_mean"], ru["alpha"])
        out["rush_yd"] = Compound([ru])
    if rc:
        out["rec"] = PD.NegBin(rc["count_mean"], rc["alpha"])
        out["rec_yd"] = Compound([rc])
    if ru or rc:
        out["rush_rec_yd"] = Compound([x for x in (ru, rc) if x])
    if ru:
        out["long_rush"] = Longest([ru])
    td = _pos(base.get("rush_td")) * mult.get("rush_td", 1.0) + _pos(base.get("rec_td")) * mult.get("rec_td", 1.0)
    if td > 0:
        out["td"] = PD.Poisson(td)
    return out, parts


# ---- quarterbacks -------------------------------------------------------------------------
def expected_kneels(coefs, team_margin):
    """A team's expected kneels from its pre-game expected margin (+ =
    favoured): the fitted table (2023-24: 1.06 a game as a 7+ point
    favourite, 0.48 as a 7+ point underdog), linear between bucket centres."""
    tab = (coefs or {}).get("kneels") or {}
    pts = sorted((float(x), float(v)) for x, v in (tab.get("by_margin") or []))
    if not pts:
        return float(tab.get("mean", 0.0))
    m = 0.0 if team_margin is None else float(team_margin)
    if m <= pts[0][0]:
        return pts[0][1]
    if m >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= m <= x1:
            return y0 + (y1 - y0) * (m - x0) / (x1 - x0) if x1 > x0 else y0
    return pts[-1][1]


# The split the QB DISTRIBUTION reads. Measured through the frame (fit 2023, scored 2024, inside
# the fitting seasons; research/reports/nfl_rush_model.md section 1): the compound on the
# POSITION's shares beat the one on each QB's own as-of shares by 0.021 +/- 0.009 nats a row (429
# QB-games), the loss concentrated in the designed runners, and every blend toward his own split
# was worse (weight 0.25 +0.004 ... 1.0 +0.021). His own split stays on the board as information
# (qb_breakdown's `own`); coefs {"qb_shares": "own"} is the research arm, never production's.
QB_SHARES = "position"


def qb_parts(base, prof, coefs, team_margin=None, mult=None):
    """A QB's four rushing components, anchored to Sleeper's line.

    base: Sleeper {rush_att, rush_yd, pass_att, rush_td} (official scoring:
          kneels are rushing attempts, sacks are not -- and Sleeper's lines
          carry the kneels: favourites are projected more attempts and their
          actual-minus-projected sits at +0.05 to +0.16, 2023-24).
    prof: the QB's as-of shares of his NON-kneel rushing attempts and his
          yards per attempt by component (qb_profile). READ ONLY when coefs
          say {"qb_shares": "own"} (a research arm): the shipped split is the
          position's (QB_SHARES, measured above), so production's call gives
          the same distribution with or without it.
    team_margin: the pre-game expected margin, for the kneels.
    mult: exp of the shipped terms by component -- "designed", "scramble"
          (counts) and "designed_eff", "scramble_eff" (yards per attempt).

    The anchoring: kneels K from the game script; the other A - K attempts
    split by the shares; yards per designed run and per scramble scaled by
    one common factor so the four components' mean is Sleeper's yards line
    (sneaks and kneels keep their own). At weight 0 the attempts AND the
    yards mean are Sleeper's exactly (a guard pins both)."""
    mult = mult or {}
    k = (coefs or {}).get("kernels") or {}
    cur = (coefs or {}).get("alpha") or {}
    pri = (coefs or {}).get("qb_prior") or {}
    src = (prof or {}) if (coefs or {}).get("qb_shares", QB_SHARES) == "own" else {}
    A, Y = _pos(base.get("rush_att")), float(base.get("rush_yd") or 0.0)
    if A <= 0 or not all(r in k for r in ("qb_designed", "qb_sneak", "qb_scramble", "kneel")):
        return None
    sh = {p: float(src.get(f"{p}_share", pri.get(f"{p}_share", 0.0)) or 0.0) for p in ("designed", "sneak", "scramble")}
    tot = sum(sh.values())
    sh = {p: (v / tot if tot > 0 else 1.0 / 3) for p, v in sh.items()}
    kd = k["qb_designed"]
    K = min(expected_kneels(coefs, team_margin), 0.8 * A)   # a kneel is one of his attempts
    rest = A - K
    cnt = {p: rest * sh[p] for p in sh}
    kern_of = {"designed": kd, "sneak": k["qb_sneak"], "scramble": k["qb_scramble"]}
    ypa = {p: float(src.get(f"ypa_{p}") or kernel_mean(kern_of[p])) for p in sh}
    m_k = kernel_mean(k["kneel"])
    # one factor on designed + scramble yards so the total mean is Sleeper's
    fixed = cnt["sneak"] * ypa["sneak"] + K * m_k
    flex = cnt["designed"] * ypa["designed"] + cnt["scramble"] * ypa["scramble"]
    phi = (Y - fixed) / flex if flex > 0 else 1.0
    anchored = phi > 0.05
    phi = max(0.05, phi)
    ypa["designed"] *= phi
    ypa["scramble"] *= phi
    # the matchup terms (1 at weight 0)
    cnt["designed"] *= mult.get("designed", 1.0)
    cnt["scramble"] *= mult.get("scramble", 1.0)
    ypa["designed"] *= mult.get("designed_eff", 1.0)
    ypa["scramble"] *= mult.get("scramble_eff", 1.0)
    comps = []
    for p in ("designed", "sneak", "scramble"):
        comps.append({"role": "qb_" + p, "count_mean": cnt[p], "alpha": alpha_of(cur.get("qb_" + p), cnt[p]),
                      "kernel": rescale(kern_of[p], ypa[p]), "ypa": ypa[p]})
    comps.append({"role": "kneel", "count_mean": K, "alpha": alpha_of(cur.get("kneel"), K), "kernel": k["kneel"],
                  "ypa": m_k})
    return {"components": comps, "phi": phi, "anchored": anchored, "kneels": K, "shares": sh}


def qb_dists(base, prof, coefs, team_margin=None, mult=None):
    """{market: dist} for a QB: rushing attempts (the four counts added),
    rushing yards (the four compounds added), anytime TD (Poisson of his
    rushing + receiving TDs; passing TDs never count), the longest rush
    (kneels and sneaks included: they are rushes). Kalshi's rushing ladders
    settle WITH kneels and scrambles (239 of 239 settled 2025 QB ladders
    bracket the total that includes them, research/reports/rush_data_sources.md
    9.2)."""
    parts = qb_parts(base, prof, coefs, team_margin, mult)
    out = {}
    if parts:
        comps = parts["components"]
        out["rush_att"] = Compound([dict(c, kernel=unit_kernel()) for c in comps])
        out["rush_yd"] = Compound(comps)
        out["long_rush"] = Longest(comps)
    td = _pos(base.get("rush_td")) * (mult or {}).get("rush_td", 1.0) + _pos(base.get("rec_td"))
    if td > 0:
        out["td"] = PD.Poisson(td)
    return out, parts


# ---- the payload pieces -------------------------------------------------------------------
def _r(x, n=3):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if math.isfinite(x) else None


def item(label, value, unit, kind, basis, lo=None, hi=None):
    """One breakdown entry (the contract the tab draws): {label, value,
    unit, kind, basis, lo, hi}; lo / hi stay None unless a shipped
    coefficient's uncertainty sets them (no zero-width bands)."""
    return {"label": label, "value": _r(value), "unit": unit, "kind": kind, "basis": basis,
            "lo": _r(lo), "hi": _r(hi)}


def rb_breakdown(parts, base, prof=None, status="0"):
    """The RB's components for the tab. `prof` may carry the data layer's
    as-of yards before / after contact per carry (pfr advstats)."""
    prof = prof or {}
    out = []
    moved = status != "0"
    how = ("Sleeper's line x the shipped terms" + (" (PROVISIONAL)" if status == "provisional" else "")
           if moved else "Sleeper's line (no rushing term has passed its test)")
    ru, rc = (parts or {}).get("rush"), (parts or {}).get("rec")
    if ru:
        out.append(item("Expected carries", ru["count_mean"], "per_game", "modelled", how))
        out.append(item("Yards per carry", ru["ypc"], "yds", "modelled", how))
        ybc, yac = prof.get("ybc_per_att"), prof.get("yac_per_att")
        if ybc is not None and yac is not None:
            cb = f"{CREDIT['pfr']}, his prior games; {DISPLAY_ONLY}"
            out.append(item("Yards before contact per carry (his own, as of this week)", ybc, "yds", "observed", cb))
            out.append(item("Yards after contact per carry (his own, as of this week)", yac, "yds", "observed", cb))
    if rc:
        out.append(item("Expected receptions", rc["count_mean"], "per_game", "modelled", how))
        out.append(item("Yards per catch", rc["ypr"], "yds", "modelled", how))
    t = _pos(base.get("rec_tgt"))
    if t:
        out.append(item("Expected targets", t, "per_game", "observed", "Sleeper's line"))
    rt, ct = _pos(base.get("rush_td")), _pos(base.get("rec_td"))
    if rt or ct:
        out.append(item("Expected rushing TDs", rt, "per_game", "observed", "Sleeper's line"))
        out.append(item("Expected receiving TDs", ct, "per_game", "observed", "Sleeper's line"))
    return out


def qb_breakdown(parts, base, status="0", own=None):
    """The QB's components: expected designed runs, sneaks, scrambles,
    kneels, yards per attempt of each, expected dropbacks and the scramble
    rate per dropback they imply. `own` (qb_profile) adds HIS as-of split of
    his non-kneel rushes, labelled as information: the components above use
    the position's split (QB_SHARES)."""
    if not parts:
        return []
    moved = status != "0"
    how = ("Sleeper's line split by the position's shares (fitted 2023-24) and the game script x the shipped terms"
           + (" (PROVISIONAL)" if status == "provisional" else "") if moved
           else "Sleeper's line split by the position's shares (fitted 2023-24) and the game script (no term has "
                "passed its test; the split does not move a probability)")
    lab = {"qb_designed": "designed runs", "qb_sneak": "sneaks", "qb_scramble": "scrambles", "kneel": "kneels"}
    out = []
    # the labels say "position split": on 2024 QBs whose own designed share was 0.40+ the position's
    # split read 2.09 designed runs a game against 3.83 actual (the rule review's m5), so the
    # figure must never read as his
    for c in parts["components"]:
        basis = ("the position's split of Sleeper's attempts, the same for every QB: " + how) if c["role"] != "kneel" \
            else ("the team's expected kneels at its pre-game margin (fitted 2023-24 table; kneels are rushing "
                  "attempts at about -1 yard each in official scoring)")
        tag = "" if c["role"] == "kneel" else " (position split)"
        out.append(item(f"Expected {lab[c['role']]}{tag}", c["count_mean"], "per_game", "modelled", basis))
        out.append(item(f"Yards per {lab[c['role']][:-1]}{tag}", c["ypa"], "yds", "modelled", basis))
    pa = _pos(base.get("pass_att"))
    scr = next((c["count_mean"] for c in parts["components"] if c["role"] == "qb_scramble"), 0.0)
    if pa:
        db = pa + scr
        out.append(item("Expected dropbacks (pass attempts + scrambles; sacks not counted)", db, "per_game",
                        "modelled", "Sleeper's pass attempts plus the expected scrambles"))
        out.append(item("Scrambles per dropback", scr / db if db else None, "share", "modelled",
                        "expected scrambles over expected dropbacks"))
    lab_own = {"designed": "designed runs", "sneak": "sneaks", "scramble": "scrambles"}
    non_kneel = sum(c["count_mean"] for c in parts["components"] if c["role"] != "kneel")
    own_basis = ("information only: his as-of rates x his exposure (the rushing data layer, shrunk toward the "
                 "position); the model splits by the position's, which priced his rushing yards better on 2024 "
                 "(fit 2023)")
    for p in ("designed", "sneak", "scramble"):
        v = _num_or_none((own or {}).get(f"{p}_share"))
        if v is not None:
            out.append(item(f"His own share of his non-kneel rushes: {lab_own[p]}", v, "share", "modelled", own_basis))
            out.append(item(f"His own split of the same attempts: expected {lab_own[p]}", v * non_kneel, "per_game",
                            "modelled", own_basis))
    if not parts.get("anchored"):
        out.append(item("Anchoring note", None, "none", "assumption",
                        "Sleeper's yards line sits below what his sneaks and kneels alone carry; designed-run and "
                        "scramble yards were floored at 5% of his own"))
    return out


def correlations(coefs):
    """The measured within-team residual correlations (actual minus Sleeper's
    line), for pricing two props together; every entry says what it is."""
    out = []
    for c in ((coefs or {}).get("correlations") or []):
        out.append({"a": c["a"], "b": c["b"], "rho": _r(c["rho"], 4), "n": c.get("n"), "kind": "observed",
                    "basis": c.get("basis", "")})
    return out


def probs(dist_fn, scale_se, ks):
    """{k: (p, lo, hi)} from a builder `dist_fn(scale)` (scale 1 = the model;
    the moved part's mean x scale). lo / hi are the dist at exp(-/+1.96 se)
    when a shipped coefficient carries uncertainty, else None -- never a
    zero-width band."""
    d = dist_fn(1.0)
    if d is None:
        return {}
    ks = sorted(set(int(k) for k in ks))
    lad = d.ladder(ks)
    if not (scale_se and scale_se > 0):
        return {k: (lad[k], None, None) for k in ks}
    a, b = dist_fn(math.exp(-1.96 * scale_se)).ladder(ks), dist_fn(math.exp(1.96 * scale_se)).ladder(ks)
    lo = PD.enforce_monotone(ks, [min(a[k], b[k], lad[k]) for k in ks])
    hi = PD.enforce_monotone(ks, [max(a[k], b[k], lad[k]) for k in ks])
    return {k: (lad[k], lo[i], hi[i]) for i, k in enumerate(ks)}


# ---- the data layer adapter -------------------------------------------------------------------
# The ONE place the rushing model reads the rushing data layer (nfl_rush_profiles; API in
# research/reports/nfl_rush_data.md §0): the research frame and the production board both call
# snapshot_blocks() and features(), so a renamed field breaks one table, not two code paths.
# A feature is a DEVIATION from what the data layer shrinks toward, so 0 means "league / his own
# baseline" and a missing field reads 0 (and is counted missing):
#   ("dev", block, path)    value - prior (a shrunk field; a split's prior is his own overall
#                           moved by the league's offset, so a split dev is his departure)
#   ("log", block, path)    log(value) for an observed / expected ratio (prior 1)
#   ("lg", block, path)     value - the league mean of that field over every team at the cutoff
#                           (observed per-game counts carry no prior)
#   ("val", block, path)    the value as it is (a count, a share already relative)
#   ("diff", a, b)          spec a - spec b (his split contrasts: vs pressure minus not)
#   ("prod", a, b)          a x b (an interaction of his trait and the defense's)
#   ("sum", [s, ...])       the sum of specs (his direction shares x the defense by side)
#   ("ext", key)            a game-level input the caller supplies (game lines, the first model's
#                           defense deviations, the room out, comparables)
# Blocks: p rb(pid, team) | q qb(pid, team) | d defense_run(opp) | dq defense_qb(opp) |
#         ol offense_line(team) | tc team_context(team).
SIDES = ("edge", "tackle", "interior")
_d = lambda b, p: ("dev", b, p)
FEATURES = {
    # overall defense
    "ovd_run_epa": _d("d", "vs_rb.epa"),
    "ovd_run_success": _d("d", "vs_rb.success"),
    # pressure is the pbp's in-season sacks + QB hits per opponent dropback, observed over what the
    # QBs faced do elsewhere (a proxy; r 0.40, k 67). Participation's rate is prior seasons only on
    # every live board, and PFR's count is licence-barred from fitting (the licence default below).
    "ovd_pressure": ("log", "dq", "hit_sack_rate.oe_ratio"),
    "ovd_blitz": _d("dq", "blitz"),
    "ovd_pass_epa": ("ext", "m1_pass_epa"),          # the first model's defense_block deviations
    "ovd_catch": ("ext", "m1_catch_all"),
    # position-specific defense
    "spd_rb_ypc": _d("d", "vs_rb.ypc"),
    "spd_rb_stuff": _d("d", "vs_rb.stuff"),
    "spd_rb_expl10": _d("d", "vs_rb.expl10"),
    "spd_rb_yds_pg": ("lg", "d", "per_game.rb_rush_yds"),
    "spd_rb_car_pg": ("lg", "d", "per_game.rb_carries"),
    "spd_qb_dsg_oe": ("log", "dq", "designed_runs.oe_ratio"),
    "spd_qb_dsgyds_oe": ("log", "dq", "designed_yds.oe_ratio"),
    "spd_qb_scr_oe": ("log", "dq", "scrambles.oe_ratio"),
    "spd_qb_scryds_oe": ("log", "dq", "scramble_yds.oe_ratio"),
    "spd_qb_expl_oe": ("log", "dq", "explosive_runs.oe_ratio"),
    "spd_qb_td_oe": ("log", "dq", "rush_tds.oe_ratio"),
    "spd_rbrec_tgt": _d("d", "vs_rb_receiving.tgt_per_db"),
    "spd_rbrec_ypt": _d("d", "vs_rb_receiving.ypt"),
    "spd_rbrec_catch": _d("d", "vs_rb_receiving.catch"),
    "spd_rbrec_yac": _d("d", "vs_rb_receiving.yac_rec"),
    # concept / front PROXY (labelled: no free source charts concepts or fronts)
    "cfp_direction": ("sum", [("prod", ("val", "p", f"rushing.by_side.{s}.share"), _d("d", f"vs_rb.by_side.{s}.ypc"))
                              for s in SIDES]),
    "cfp_box_split": ("sum", [("prod", _d("p", f"rushing.by_box.{b}.ypc"), _d("d", f"box.{b}"))
                              for b in ("light", "heavy")]),
    "cfp_def_light": _d("d", "box.light"),
    "cfp_def_heavy": _d("d", "box.heavy"),
    "cfp_pers_split": ("sum", [("prod", _d("p", f"rushing.by_personnel_faced.{x}.ypc"), _d("d", f"personnel.{x}"))
                               for x in ("base", "nickel", "dime")]),
    "cfp_ol_line": _d("ol", "line_yds"),
    "cfp_ol_stuff": _d("ol", "stuff"),
    "cfp_def_line": _d("d", "vs_rb.line_yds"),
    "cfp_ol_x_def_line": ("prod", _d("ol", "line_yds"), _d("d", "vs_rb.line_yds")),
    "cfp_ol_out": ("val", "ol", "ol_listed_out"),     # his OL listed Out / Doubtful on the week's report (injuries)
    "cfp_front_dl4": _d("d", "front_proxy.dl4"),
    # (the contact terms -- his yards after contact and broken tackles x the defense's missed tackles,
    # the line's yards before contact -- were PFR's and left with the licence default; line quality
    # stays through the pbp's line yards and stuff rate above)
    "cfp_scr_press": ("prod", ("diff", ("val", "q", "scramble.vs.pressure"), ("val", "q", "scramble.vs.no_pressure")),
                      ("log", "dq", "hit_sack_rate.oe_ratio")),
    "cfp_scr_blitz": ("prod", ("diff", ("val", "q", "scramble.vs.blitz"), ("val", "q", "scramble.vs.no_blitz")),
                      _d("dq", "blitz")),
    "cfp_scr_man": ("prod", ("diff", ("val", "q", "scramble.vs.man"), ("val", "q", "scramble.vs.zone")), ("ext", "m1_man")),
    "cfp_scr_shell": ("prod", ("diff", ("val", "q", "scramble.vs.single_high"), ("val", "q", "scramble.vs.two_high")),
                      ("ext", "m1_sh_frac")),
    "cfp_contain": ("log", "dq", "containment_proxy.oe_ratio"),
    "cfp_dsg_edge": ("prod", ("val", "q", "designed.by_side.edge"), _d("d", "vs_rb.by_side.edge.ypc")),
    "cfp_screen_x_blitz": ("prod", _d("p", "receiving.screen_share"), _d("dq", "blitz")),
    "cfp_chk_x_press": ("prod", _d("p", "receiving.checkdown_share"), ("log", "dq", "hit_sack_rate.oe_ratio")),
    "cfp_rec_mz": ("prod", ("diff", ("val", "p", "usage.tgt_per_db_vs_man"), ("val", "p", "usage.tgt_per_db_vs_zone")),
                   ("ext", "m1_man")),
    # decomposition: the opportunity terms (counts) -- game script from the PRE-GAME line only
    "dec_margin": ("ext", "margin7"),               # team-side expected margin / 7
    "dec_total": ("ext", "total_c"),                # (game total - 44) / 10
    "dec_neutral_run": _d("tc", "neutral_run_rate"),
    "dec_trend": ("diff", ("val", "p", "recent.carry_share"), ("val", "p", "usage.carry_share")),
    "dec_room_out": ("ext", "room_out"),            # carry share of his room's backs listed Out / Doubtful
    # full model
    "full_comp_rush_yd": ("ext", "comp_rush_yd"),
    "full_comp_rec_yd": ("ext", "comp_rec_yd"),
    "full_comp_qb_rush_yd": ("ext", "comp_qb_rush_yd"),
    "full_qb_keep": _d("tc", "qb_keep_share"),
    "full_ypc_x_margin": ("prod", _d("d", "vs_rb.ypc"), ("ext", "margin7")),
    "full_scr_x_margin": ("prod", ("diff", ("val", "q", "scramble.vs.pressure"), ("val", "q", "scramble.vs.no_pressure")),
                          ("ext", "margin7")),
    "full_rz_run": _d("tc", "red_zone_run_rate"),
    "full_qb_gl": _d("q", "scoring.gl_rush_rate"),
}
# the ladder (the owner's order) per market family: group -> [(feature, what it moves)]; before
# SHAPE is accepted every term acts on its market's mean; from SHAPE on "count" / "eff" act on the
# component (opportunity vs efficiency) and "mean" on the TD mean
GROUPS = {
    "rb_rush": {
        "OVD": [("ovd_run_epa", "eff"), ("ovd_run_success", "eff")],
        "SPD": [("spd_rb_ypc", "eff"), ("spd_rb_stuff", "eff"), ("spd_rb_expl10", "eff"), ("spd_rb_yds_pg", "eff"),
                ("spd_rb_car_pg", "count")],
        "CFP": [("cfp_direction", "eff"), ("cfp_box_split", "eff"), ("cfp_def_light", "eff"), ("cfp_def_heavy", "eff"),
                ("cfp_pers_split", "eff"), ("cfp_ol_line", "eff"), ("cfp_ol_stuff", "eff"), ("cfp_def_line", "eff"),
                ("cfp_ol_x_def_line", "eff"), ("cfp_ol_out", "count"), ("cfp_front_dl4", "eff")],
        "SHAPE": [],
        "DEC": [("dec_margin", "count"), ("dec_total", "count"), ("dec_neutral_run", "count"), ("dec_trend", "count"),
                ("dec_room_out", "count")],
        "FULL": [("full_comp_rush_yd", "eff"), ("full_qb_keep", "count"), ("full_ypc_x_margin", "eff")],
    },
    "qb_rush": {
        "OVD": [("ovd_run_epa", "eff"), ("ovd_pressure", "count"), ("ovd_blitz", "count")],
        "SPD": [("spd_qb_dsg_oe", "count"), ("spd_qb_dsgyds_oe", "eff"), ("spd_qb_scr_oe", "count"),
                ("spd_qb_scryds_oe", "eff"), ("spd_qb_expl_oe", "eff")],
        "CFP": [("cfp_scr_press", "count"), ("cfp_scr_blitz", "count"), ("cfp_scr_man", "count"),
                ("cfp_scr_shell", "count"), ("cfp_contain", "eff"), ("cfp_dsg_edge", "eff")],
        "SHAPE": [],
        "DEC": [("dec_margin", "count"), ("dec_total", "count")],
        "FULL": [("full_comp_qb_rush_yd", "eff"), ("full_scr_x_margin", "count")],
    },
    "rb_rec": {
        "OVD": [("ovd_pass_epa", "eff"), ("ovd_catch", "eff")],
        "SPD": [("spd_rbrec_tgt", "count"), ("spd_rbrec_ypt", "eff"), ("spd_rbrec_catch", "count"),
                ("spd_rbrec_yac", "eff")],
        "CFP": [("cfp_screen_x_blitz", "count"), ("cfp_chk_x_press", "count"), ("cfp_rec_mz", "count")],
        "SHAPE": [],
        "DEC": [("dec_margin", "count"), ("dec_total", "count")],
        "FULL": [("full_comp_rec_yd", "eff")],
    },
    # anytime TD: its OWN groups only, a GLM on rush + rec TDs with offset log(Sleeper's TDs); no
    # carries or receptions factor reaches it (the TD link, Q3b)
    "rb_td": {"FULL": [("full_rz_run", "mean"), ("full_qb_keep", "mean")]},
    "qb_td": {"SPD": [("spd_qb_td_oe", "mean")], "FULL": [("full_qb_gl", "mean")]},
}
LADDER = ("OVD", "SPD", "CFP", "SHAPE", "DEC", "FULL")
# SHAPE: the decomposition with NO new term -- the compound distribution (carries x a per-carry kernel,
# the line-dependent alpha, the QB's four parts) in place of the first model's family, the terms
# already accepted re-expressed on components (Q4). It decides the markets the compound prices:
SHAPE_MARKETS = {"RB": ("rush_att", "rush_yd", "rec", "rec_yd"), "QB": ("rush_att", "rush_yd")}
# The QB component each QB term moves (Q3b): a count term moves designed runs, scrambles or both
# (each its own coefficient in that component's GLM), an efficiency term yards per designed run, per
# scramble or both. Sneaks and kneels are never moved.
QB_COMPONENT = {
    "ovd_pressure": "scramble", "ovd_blitz": "scramble", "ovd_run_epa": "designed",
    "spd_qb_dsg_oe": "designed", "spd_qb_scr_oe": "scramble", "spd_qb_dsgyds_oe": "designed",
    "spd_qb_scryds_oe": "scramble", "spd_qb_expl_oe": "both",
    "cfp_scr_press": "scramble", "cfp_scr_blitz": "scramble", "cfp_scr_man": "scramble", "cfp_scr_shell": "scramble",
    "cfp_contain": "scramble", "cfp_dsg_edge": "designed",
    "dec_margin": "both", "dec_total": "both",
    "full_comp_qb_rush_yd": "both", "full_scr_x_margin": "scramble"}

# ---- the data licence default (orchestrator, 2026-10-24; research/reviews/rush_data_sources_opus.md F8) ----
# Sports Reference's terms (May 19, 2023, section 5) bar using its Content for "supporting machine
# learning methods used to predict, classify, label, or score inputs into the models": PFR advanced
# stats AND PFR snap counts are its Content, even via nflverse. NGS is the NFL's, whose terms bar
# using the Services to gamble. So NO fitted feature reads a PFR-, PFR-snap- or NGS-derived field;
# they may appear on the board as information, credited, saying they do not move a probability.
# Participation keeps the shipped first model's status (NGS-sourced up to 2022, FTN from 2023).
# Adding PFR / NGS later is a NEW pre-registered group, judged prospectively, with the owner's yes.
FIT_SOURCES = frozenset(("pbp", "participation", "ftn", "injuries", "lines"))
CREDIT = {"pbp": "nflverse play-by-play (nflfastR)",
          "participation": "NFL Next Gen Stats via nflverse (2022 and earlier); FTN Data via nflverse (2023 on)",
          "ftn": "FTN Data via nflverse",
          "injuries": "nflverse injury reports",
          "lines": "the pre-game line (research: nflverse schedules' closing line; production: Kalshi's de-vigged price)",
          "pfr": "Pro Football Reference via nflverse",
          "ngs": "NFL Next Gen Stats via nflverse"}
DISPLAY_ONLY = "display only: does not move a probability (data licence: PFR / NGS are never fitted)"
# every input a fitted feature reads, by (block, path), with its source(s) -- a path not listed here
# fails the guard, so a new input has to declare where it comes from, and the guard holds each
# declaration EQUAL to the data layer's own tag (nfl_rush_profiles.SOURCE_OF, "derived:a+b" -> (a, b))
PATH_SOURCE = {
    ("d", "vs_rb.epa"): ("pbp",), ("d", "vs_rb.success"): ("pbp",), ("d", "vs_rb.ypc"): ("pbp",),
    ("d", "vs_rb.stuff"): ("pbp",), ("d", "vs_rb.expl10"): ("pbp",), ("d", "vs_rb.line_yds"): ("pbp",),
    ("d", "per_game.rb_rush_yds"): ("pbp",), ("d", "per_game.rb_carries"): ("pbp",),
    **{("d", f"vs_rb.by_side.{x}.ypc"): ("pbp",) for x in SIDES},
    **{("p", f"rushing.by_side.{x}.share"): ("pbp",) for x in SIDES},
    **{("p", f"rushing.by_box.{b}.ypc"): ("participation", "ftn", "pbp") for b in ("light", "heavy")},
    **{("d", f"box.{b}"): ("participation", "ftn", "pbp") for b in ("light", "heavy")},
    **{("p", f"rushing.by_personnel_faced.{x}.ypc"): ("participation", "pbp") for x in ("base", "nickel", "dime")},
    **{("d", f"personnel.{x}"): ("participation",) for x in ("base", "nickel", "dime")},
    ("d", "front_proxy.dl4"): ("participation",),
    ("d", "vs_rb_receiving.tgt_per_db"): ("pbp",), ("d", "vs_rb_receiving.ypt"): ("pbp",),
    ("d", "vs_rb_receiving.catch"): ("pbp",), ("d", "vs_rb_receiving.yac_rec"): ("pbp",),
    ("dq", "hit_sack_rate.oe_ratio"): ("pbp",), ("dq", "blitz"): ("ftn", "pbp"),
    ("dq", "designed_runs.oe_ratio"): ("pbp",), ("dq", "designed_yds.oe_ratio"): ("pbp",),
    ("dq", "scrambles.oe_ratio"): ("pbp",), ("dq", "scramble_yds.oe_ratio"): ("pbp",),
    ("dq", "explosive_runs.oe_ratio"): ("pbp",), ("dq", "rush_tds.oe_ratio"): ("pbp",),
    ("dq", "containment_proxy.oe_ratio"): ("participation", "pbp"),
    ("ol", "line_yds"): ("pbp",), ("ol", "stuff"): ("pbp",), ("ol", "ol_listed_out"): ("injuries",),
    ("p", "receiving.screen_share"): ("ftn", "pbp"), ("p", "receiving.checkdown_share"): ("ftn", "pbp"),
    ("p", "usage.tgt_per_db_vs_man"): ("participation", "pbp"), ("p", "usage.tgt_per_db_vs_zone"): ("participation", "pbp"),
    # the carry shares count pbp carries over the games participation's on-field snaps also decide (the
    # layer's appearance rule, tagged so on 2026-10-24 05:38 UTC); both parts unrestricted
    ("p", "recent.carry_share"): ("participation", "pbp"), ("p", "usage.carry_share"): ("participation", "pbp"),
    ("tc", "neutral_run_rate"): ("pbp",), ("tc", "qb_keep_share"): ("pbp",), ("tc", "red_zone_run_rate"): ("pbp",),
    **{("q", f"scramble.vs.{x}"): ("participation", "pbp")
       for x in ("pressure", "no_pressure", "man", "zone", "single_high", "two_high")},
    ("q", "scramble.vs.blitz"): ("ftn", "pbp"), ("q", "scramble.vs.no_blitz"): ("ftn", "pbp"),
    ("q", "designed.by_side.edge"): ("ftn", "pbp"),        # designed = not a sneak, FTN's flag (round-3 tag)
    ("q", "scoring.gl_rush_rate"): ("pbp",),
}
EXT_SOURCE = {"m1_pass_epa": ("pbp",), "m1_catch_all": ("pbp",), "m1_man": ("participation",),
              "m1_sh_frac": ("participation",), "margin7": ("lines",), "total_c": ("lines",),
              "room_out": ("injuries", "pbp"),
              # the comparables: his pbp game logs, weighted by the defense vector (the frame's VEC)
              "comp_rush_yd": ("pbp", "participation", "ftn"), "comp_rec_yd": ("pbp", "participation", "ftn"),
              "comp_qb_rush_yd": ("pbp", "participation", "ftn")}
# inputs the data layer has announced but not yet landed: a frame records them absent and the
# registration refuses while any fitted feature reads one (dq hit_sack_rate and ol_listed_out landed
# with the agreed rebuild, nfl_rush_profiles 6da5fa7d, 2026-10-24)
PENDING_PATHS = frozenset()


def spec_leaves(spec):
    """Every (kind, block-or-key, path) leaf a feature spec reads."""
    k = spec[0]
    if k in ("dev", "log", "lg", "val"):
        return [(k, spec[1], spec[2])]
    if k == "ext":
        return [("ext", spec[1], None)]
    if k in ("diff", "prod"):
        return spec_leaves(spec[1]) + spec_leaves(spec[2])
    if k == "sum":
        return [x for s in spec[1] for x in spec_leaves(s)]
    raise ValueError(f"unknown feature spec {k}")


def fitted_features():
    """The features some group of some market family fits."""
    return sorted({f for fam in GROUPS.values() for fs in fam.values() for f, _a in fs})


def feature_sources(name):
    """{leaf: sources} for one feature; a leaf with no declared source maps to ("UNDECLARED",)."""
    out = {}
    for k, b, path in spec_leaves(FEATURES[name]):
        if k == "ext":
            out[("ext", b)] = EXT_SOURCE.get(b, ("UNDECLARED",))
        else:
            out[(b, path)] = PATH_SOURCE.get((b, path), ("UNDECLARED",))
    return out
# the "lg" fields: league means over every team at the cutoff
LG_FIELDS = (("d", "per_game.rb_rush_yds"), ("d", "per_game.rb_carries"))


def _field(blocks, b, path):
    f = (blocks.get(b) or {}).get(path)
    return f if isinstance(f, dict) else None


def _num_or_none(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def evaluate(spec, blocks, lg=None, ext=None):
    """One spec's value, or None when an input is missing (the caller reads
    None as 0, the deviation's 'nothing known', and counts it missing)."""
    kind = spec[0]
    if kind in ("dev", "log", "lg", "val"):
        f = _field(blocks, spec[1], spec[2])
        v = _num_or_none((f or {}).get("value"))
        if v is None:
            return None
        if kind == "val":
            return v
        if kind == "dev":
            p = _num_or_none(f.get("prior"))
            return None if p is None else v - p
        if kind == "log":
            return math.log(v) if v > 0 else None
        m = _num_or_none((lg or {}).get((spec[1], spec[2])))
        return None if m is None else v - m
    if kind == "ext":
        return _num_or_none((ext or {}).get(spec[1]))
    if kind == "diff":
        a, b = evaluate(spec[1], blocks, lg, ext), evaluate(spec[2], blocks, lg, ext)
        return None if a is None or b is None else a - b
    if kind == "prod":
        a, b = evaluate(spec[1], blocks, lg, ext), evaluate(spec[2], blocks, lg, ext)
        return None if a is None or b is None else a * b
    if kind == "sum":
        parts = [evaluate(s, blocks, lg, ext) for s in spec[1]]
        parts = [x for x in parts if x is not None]
        return sum(parts) if parts else None
    raise ValueError(f"unknown feature spec {kind}")


def features(blocks, lg=None, ext=None, names=None):
    """{feature: value or None} for the named features (all by default)."""
    return {n: evaluate(FEATURES[n], blocks, lg, ext) for n in (names or FEATURES)}


def league_means(def_blocks):
    """{(block, path): mean value over the teams} for LG_FIELDS, from
    {team: {"d": flat defense_run, ...}} of EVERY team at the cutoff."""
    out = {}
    for b, path in LG_FIELDS:
        vals = [_num_or_none((((blk.get(b) or {}).get(path)) or {}).get("value")) for blk in def_blocks.values()]
        vals = [v for v in vals if v is not None]
        out[(b, path)] = sum(vals) / len(vals) if vals else None
    return out


def snapshot_blocks(S, pid, team, opp, pos):
    """The flattened data-layer blocks a row needs (nfl_rush_profiles.RushSnapshot,
    as of the row's week). RB: his profile team-scoped and the room out; QB: his
    decomposition and its totals (exposure, dropbacks, the partition) for the
    shares."""
    import nfl_rush_profiles as RP
    out = {"d": RP.flatten(S.defense_run(opp)), "dq": RP.flatten(S.defense_qb(opp)),
           "ol": RP.flatten(S.offense_line(team)), "tc": RP.flatten(S.team_context(team))}
    if pos == "RB":
        out["p"] = RP.flatten(S.rb(pid, team))
        out["room_out"] = S.rb_room_out(team, exclude=pid)
    elif pos == "QB":
        q = S.qb(pid, team)
        out["q"] = RP.flatten(q)
        out["q_totals"] = q.get("totals") or {}
        out["q_games"] = q.get("games") or {}
    return out


def qb_profile(blocks):
    """qb_parts' `prof` from the data layer's QB decomposition: his EXPECTED
    designed runs, sneaks and scrambles per game -- each shrunk rate x his own
    per-game exposure (team plays while he passed; dropbacks for scrambles)
    -- as shares of his non-kneel attempts, and his shrunk yards per designed
    run and per scramble. None when the profile is empty. (Not the first
    model's QB profile -- its "scramble" entry reads 0 for every QB; the
    rushing layer's qb() is the decomposition.)"""
    q, tot, g = blocks.get("q") or {}, blocks.get("q_totals") or {}, blocks.get("q_games") or {}
    gw = _num_or_none(g.get("weighted"))
    ex, db = _num_or_none(tot.get("exposure")), _num_or_none(tot.get("dropbacks"))
    rate = lambda p: _num_or_none(((q.get(p)) or {}).get("value"))
    rd, rs, rc = rate("designed.rate"), rate("sneak.rate"), rate("scramble.rate")
    if not gw or not ex or db is None or None in (rd, rs, rc):
        return None
    e_pg, db_pg = ex / gw, db / gw
    cnt = {"designed": rd * e_pg, "sneak": rs * e_pg, "scramble": rc * db_pg}
    tot_c = sum(cnt.values())
    if tot_c <= 0:
        return None
    out = {f"{p}_share": c / tot_c for p, c in cnt.items()}
    out["ypa_designed"] = rate("designed.ypc")
    out["ypa_scramble"] = rate("scramble.ypa")
    out["expected_per_game"] = cnt
    out["n"] = gw
    return out


# ---- role / opponent facts for the board (passed through from the data layer) --------------------
# (key, block, path, label) -- each data-layer field already carries the contract's {value, unit,
# kind, n, basis}; a fact adds the label and keeps everything else as the layer measured it
ROLE_FACTS = {
    "RB": (("carry_share", "p", "usage.carry_share", "share of the team's carries"),
           ("snap_share", "p", "usage.snap_share", "snap share (PFR snap counts)"),
           ("rz_carry_share", "p", "usage.rz_carry_share", "red-zone carry share"),
           ("gl_carry_share", "p", "usage.gl_carry_share", "goal-line carry share"),
           ("edge_share", "p", "rushing.by_side.edge.share", "share of his runs to the edge (run_gap end: a PROXY)"),
           ("ypc", "p", "rushing.ypc", "yards per carry"),
           ("ybc", "p", "rushing.line.ybc", "yards before contact per carry (the line's; PFR)"),
           ("yac", "p", "rushing.back.yac", "yards after contact per carry (his; PFR)"),
           ("broken_tackles", "p", "rushing.back.broken_tackles", "broken tackles per carry (PFR)"),
           ("ryoe", "p", "rushing.back.ryoe", "rush yards over expected per carry (NGS, 10+ carry games)"),
           ("tgt_per_db", "p", "usage.tgt_per_db", "targets per on-field dropback (prior seasons; per-route PROXY)"),
           ("screen_share", "p", "receiving.screen_share", "share of his targets that are screens")),
    "QB": (("designed_pg", "q", "designed.per_game", "designed runs per game (sneaks apart)"),
           ("sneak_pg", "q", "sneak.per_game", "sneaks per game"),
           ("scramble_rate", "q", "scramble.rate", "scrambles per dropback"),
           ("scramble_vs_pressure", "q", "scramble.vs.pressure", "scramble rate on pressured dropbacks"),
           ("scramble_vs_man", "q", "scramble.vs.man", "scramble rate against man coverage"),
           ("scramble_vs_zone", "q", "scramble.vs.zone", "scramble rate against zone coverage"),
           ("ypc_designed", "q", "designed.ypc", "yards per designed run"),
           ("ypa_scramble", "q", "scramble.ypa", "yards per scramble"),
           ("kneel_pg", "q", "kneel.per_game", "kneels per game (official rushes at about -1)"),
           ("hit_sack_rate", "q", "hit_sack_rate", "sacks + QB hits per dropback (pbp; a pressure PROXY)"),
           ("gl_rush_rate", "q", "scoring.gl_rush_rate", "goal-line rushes per play")),
}
OPP_FACTS = {
    "RB": (("rb_ypc_allowed", "d", "vs_rb.ypc", "yards per RB carry allowed"),
           ("rb_stuff_allowed", "d", "vs_rb.stuff", "RB runs stuffed (at or behind the line)"),
           ("rb_expl10_allowed", "d", "vs_rb.expl10", "RB runs of 10+ allowed"),
           ("light_box", "d", "box.light", "light boxes"),
           ("heavy_box", "d", "box.heavy", "heavy boxes"),
           ("line_yds_allowed", "d", "vs_rb.line_yds", "line yards allowed (this layer's convention, not FO's)"),
           ("missed_tackle_rate", "d", "contact.missed_tackle_rate", "missed-tackle rate (PFR)"),
           ("ol_listed_out", "ol", "ol_listed_out", "his offensive linemen listed Out / Doubtful this week"),
           ("rb_tgt_per_db_allowed", "d", "vs_rb_receiving.tgt_per_db", "RB targets per dropback allowed")),
    "QB": (("designed_oe", "dq", "designed_runs.oe_ratio", "QB designed runs allowed, observed / expected"),
           ("scramble_oe", "dq", "scrambles.oe_ratio", "QB scrambles allowed, observed / expected"),
           ("scramble_yds_oe", "dq", "scramble_yds.oe_ratio", "QB scramble yards allowed, observed / expected"),
           ("containment_proxy", "dq", "containment_proxy.oe_ratio", "containment PROXY: scramble yards per pressured dropback, o / e"),
           ("hit_sack_rate", "dq", "hit_sack_rate.oe_ratio",
            "sacks + QB hits allowed, observed / expected for the QBs faced (pbp; a pressure PROXY)"),
           ("pressure", "dq", "pressure_pfr", "pressure rate (PFR's in-season count, a PROXY)"),
           ("blitz", "dq", "blitz", "blitz rate")),
}


# where each fact comes from; PFR / NGS facts are shown credited and never fitted (the licence default)
FACT_SOURCE = {
    # RB role
    "carry_share": "pbp", "snap_share": "pfr", "rz_carry_share": "pbp", "gl_carry_share": "pbp", "edge_share": "pbp",
    "ypc": "pbp", "ybc": "pfr", "yac": "pfr", "broken_tackles": "pfr", "ryoe": "ngs", "tgt_per_db": "participation",
    "screen_share": "ftn",
    # QB role
    "designed_pg": "pbp", "sneak_pg": "pbp", "scramble_rate": "pbp", "scramble_vs_pressure": "participation",
    "scramble_vs_man": "participation", "scramble_vs_zone": "participation", "ypc_designed": "pbp",
    "ypa_scramble": "pbp", "kneel_pg": "pbp", "gl_rush_rate": "pbp", "hit_sack_rate": "pbp",
    # the opponent
    "rb_ypc_allowed": "pbp", "rb_stuff_allowed": "pbp", "rb_expl10_allowed": "pbp", "light_box": "participation",
    "heavy_box": "participation", "line_yds_allowed": "pbp", "missed_tackle_rate": "pfr", "ol_listed_out": "injuries",
    "rb_tgt_per_db_allowed": "pbp", "designed_oe": "pbp", "scramble_oe": "pbp", "scramble_yds_oe": "pbp",
    "containment_proxy": "participation", "pressure": "pfr", "blitz": "ftn"}


def facts(blocks, table, pos):
    """{key: fact} for a position from the data layer's fields: the field's own
    {value, unit, kind, n, basis} plus a label, its source and credit; a PFR /
    NGS fact's basis says it does not move a probability. A field the layer
    did not return is left out (never a fact without a unit)."""
    out = {}
    for key, b, path, label in table.get(pos, ()):
        f = _field(blocks, b, path)
        v = _num_or_none((f or {}).get("value"))
        if f is None or v is None or not f.get("unit"):
            continue
        src = FACT_SOURCE[key]
        basis = f.get("basis") or ""
        if src not in FIT_SOURCES:
            basis = f"{basis}; {DISPLAY_ONLY}" if basis else DISPLAY_ONLY
        out[key] = {"value": round(v, 4), "unit": f["unit"], "kind": f.get("kind") or "observed",
                    "n": _r(f.get("n"), 1), "basis": basis, "label": label,
                    "reliability": _r(f.get("reliability"), 3), "source": src, "credit": CREDIT[src]}
    return out


# ---- what the research frame and the board share (one definition each) -------------------------
def availability_view(stores, auxes, season):
    """(stores, auxes) as PRODUCTION could have read them during `season`:
    the season's own participation removed, every prior season whole.

    nflverse publishes participation after the postseason, so the live board
    never has the current season's (2026 has no file at all), and the first
    model's defense_block already reads participation from prior seasons only.
    The rushing layer reads whatever the store holds, so a 2023-2025 snapshot
    would otherwise see in-season box counts, personnel, DL counts, pressure,
    coverage and on-field shares that no live board ever had -- a validation
    that production cannot reproduce. Masking the flag the layer gates every
    participation column on (`part`), the on-field table and the situational
    counts reproduces 2026's position exactly: the box falls back to FTN's
    count in season, the rest reads prior seasons. The store's `built` / `n`
    are kept, so the aux still aligns (official rushing yards, aborted snaps).
    Nothing but these three participation inputs changes. The board applies
    it too: a no-op for a season with no participation file, and the same
    view the frame's past weeks were measured through when the comparables
    read a past week whose season has one now."""
    import array
    st = stores.get(int(season))
    if not st:
        return stores, auxes
    cols = dict(st["cols"])
    cols["part"] = array.array(st["cols"]["part"].typecode, bytes(st["cols"]["part"].itemsize * len(st["cols"]["part"])))
    view = dict(st)
    view["cols"] = cols
    view["onfield"] = {}
    out_s = dict(stores)
    out_s[int(season)] = view
    out_a = dict(auxes)
    ax = auxes.get(int(season))
    if ax:
        ax = dict(ax)
        ax["sit"] = {"players": {}, "teams": {}, "plays": 0}
        out_a[int(season)] = ax
    return out_s, out_a


def team_blocks(S, teams):
    """{team: {"d", "dq", "ol", "tc"}} flattened, every team at the snapshot's cutoff."""
    import nfl_rush_profiles as RP
    return {t: {"d": RP.flatten(S.defense_run(t)), "dq": RP.flatten(S.defense_qb(t)),
                "ol": RP.flatten(S.offense_line(t)), "tc": RP.flatten(S.team_context(t))} for t in teams}


# the comparables' defense vector (as of each game): what a run defense and a QB-rush defense allow
VEC = (("d", "vs_rb.ypc", "v"), ("d", "vs_rb.stuff", "v"), ("d", "vs_rb.expl10", "v"), ("d", "box.light", "v"),
       ("d", "box.heavy", "v"), ("dq", "hit_sack_rate.oe_ratio", "log"), ("dq", "scrambles.oe_ratio", "log"),
       ("dq", "designed_runs.oe_ratio", "log"))
COMP_C = 5.0          # yards added to both means of the comparables' log ratio (a dud week is not -infinity)
COMP_K = 10.0         # n_eff / (n_eff + 10), the first model's convention


def vec_of(blk):
    out = []
    for b, path, how in VEC:
        v = _num_or_none(((blk.get(b) or {}).get(path) or {}).get("value"))
        if v is not None and how == "log":
            v = math.log(v) if v > 0 else None
        out.append(v)
    return out


def standardize(vecs):
    """{team: vec} -> {team: z-scored vec} over the teams (a missing value -> 0)."""
    n = len(VEC)
    cols = [[v[j] for v in vecs.values() if v[j] is not None] for j in range(n)]
    mu = [sum(c) / len(c) if c else 0.0 for c in cols]
    sd = [math.sqrt(sum((x - m) ** 2 for x in c) / len(c)) if len(c) > 1 else 0.0 for c, m in zip(cols, mu)]
    return {t: [((v[j] - mu[j]) / sd[j]) if (v[j] is not None and sd[j] > 0) else 0.0 for j in range(n)]
            for t, v in vecs.items()}


def game_logs(stores):
    """{gsis: {(season, week): {"opp", "rush_yd", "rec_yd"}}} from the compact
    stores: every game a player had a rush (runs, scrambles, kneels: the
    official rushes) or a catch, and the defense he faced."""
    import collections
    import nfl_matchup_data as D
    kind = {k: i for i, k in enumerate(D.KIND)}
    out = collections.defaultdict(dict)
    for s, st in stores.items():
        c = st["cols"]
        wk = [g["week"] for g in st["games"]]
        teams = st["teams"]
        for i in range(st["n"]):
            k = c["kind"][i]
            if k in (kind["run"], kind["scramble"], kind["kneel"]) and c["rusher"][i] > 0:
                pid, fld = int(c["rusher"][i]), "rush_yd"
            elif k == kind["attempt"] and c["comp"][i] == 1 and c["receiver"][i] > 0:
                pid, fld = int(c["receiver"][i]), "rec_yd"
            else:
                continue
            key = (s, wk[c["gi"][i]])
            g = out[pid].get(key)
            if g is None:
                g = out[pid][key] = {"opp": teams[c["dfn"][i]], "rush_yd": 0.0, "rec_yd": 0.0}
            g[fld] += c["yds"][i]
    return out


def comparables(hist, logs, gsis, season, week, opp_vec, key):
    """The first model's comparables on rushing: his games from the data
    layer's BACK seasons (2) back to the week before, each weighted by
    recency (its DECAY a season) and by exp(-d^2 / 8), d the distance
    between THIS opponent's standardized vector and THAT game's opponent's,
    both as of their own weeks. Returns (feature, n_eff): n_eff / (n_eff +
    10) x log((similar-weighted mean + 5) / (recency-weighted mean + 5));
    (None, 0) without a game."""
    if opp_vec is None:
        return None, 0.0
    import nfl_rush_profiles as RP
    decay, back = RP.DECAY, RP.BACK        # the data layer's season weight and reach (its rebuild re-measures DECAY)
    sw = sy = rw = ry = sw2 = 0.0
    for (s, w), v in (logs.get(gsis) or {}).items():
        if (s, w) >= (season, week) or s < season - back:    # strictly earlier games, the layer's reach back
            continue
        opp_vec_at = hist.get((s, w, v["opp"]))
        if opp_vec_at is None:
            continue
        y = v[key]
        r = decay ** (season - s)
        d2 = sum((a - b) ** 2 for a, b in zip(opp_vec, opp_vec_at))
        wsim = r * math.exp(-d2 / 8.0)
        sw += wsim
        sy += wsim * y
        sw2 += wsim * wsim
        rw += r
        ry += r * y
    if sw <= 0 or rw <= 0:
        return None, 0.0
    n_eff = sw * sw / sw2
    return n_eff / (n_eff + COMP_K) * math.log((sy / sw + COMP_C) / (ry / rw + COMP_C)), n_eff


VECHIST_KEY = "nfl_rush_vechist_{season}_w{week:02d}"


_COMP_TERMS = {"RB:rush_yd": "full_comp_rush_yd", "RB:rec_yd": "full_comp_rec_yd", "QB:rush_yd": "full_comp_qb_rush_yd"}


def comparables_shipped(coefs):
    """Whether a SHIPPED group carries a comparables term: its market's FULL group shipped and a
    fitted model holds the term. Only then does anything compute the weekly vector history."""
    terms = {t.get("feature") for md in ((coefs or {}).get("models") or {}).values() for t in (md.get("terms") or [])}
    return any(f in terms and any(g["group"] == "FULL" for g in shipped_groups(coefs, mk))
               for mk, f in _COMP_TERMS.items())


def precompute_vectors(log=None):
    """The nfl_matchup deep job's step (PC first; on the server its niced child): the comparables'
    weekly vector history through the current week, computed and stored ONLY when a shipped group
    carries a comparables term, so a board never pays the first build (103-136 s measured, against
    the board child's 240 s timeout on Render's one core). None when nothing needs it."""
    co = load_coefs()
    if not comparables_shipped(co):
        return None
    import nfl_matchup as M
    import nfl_matchup_data as D
    import nfl_rush_data as RD
    season = D._current_season()
    ctx = M.production_ctx(season)
    if ctx is None or season not in ctx.stores:
        return None
    auxes = {s: RD.load_aux(s) for s in ctx.stores}
    week = max(g["week"] for g in ctx.stores[season]["games"]) + 1
    n = len(vector_history(ctx.stores, auxes, ctx.pos, season, week, save=True))
    if log:
        log(f"[nfl_rush] comparables vectors through {season} week {week - 1}: {n}")
    return {"season": season, "through_week": week - 1, "vectors": n}


def vector_history(stores, auxes, positions, season, week, save=True, compute=True, missing=None):
    """{(s, w, team): standardized vector} for every week the comparables can
    read -- the layer's BACK seasons back to the week before `week` -- each as
    of its OWN week through availability_view, exactly as the research frame
    builds them. A week is computed once (about 1.5 s: one snapshot) and kept
    in the deep store, stamped with its store and aux build times so a
    rebuilt layer recomputes it. Read only when a shipped term needs a
    comparable. With `compute` False (the board) a week not stored is
    skipped and listed in `missing`, never computed: the deep job
    (precompute_vectors) computes them."""
    import deep_cache
    import nfl_rush_profiles as RP
    out = {}
    for s in range(int(season) - RP.BACK, int(season) + 1):
        st = stores.get(s)
        if not st:
            continue
        stamp = [st.get("built"), (auxes.get(s) or {}).get("built")]
        for w in sorted({g["week"] for g in st["games"]}):
            if s == int(season) and w >= int(week):
                break
            key = VECHIST_KEY.format(season=s, week=w)
            got = deep_cache.load(key)[0] if (save or not compute) else None
            if not got or got.get("stamp") != stamp:
                if not compute:
                    if missing is not None:
                        missing.append((s, w))
                    continue
                vs, va = availability_view(stores, auxes, s)
                S = RP.build(vs, va, s, w, positions)
                z = standardize({t: vec_of(b) for t, b in team_blocks(S, st["teams"]).items()})
                got = {"stamp": stamp, "vec": z}
                if save:
                    deep_cache.save(key, got)
            for t, v in (got.get("vec") or {}).items():
                out[(s, w, t)] = v
    return out


# ---- the committed model, as the board reads it ---------------------------------------------------
# coefs.json (written by research/nfl_rush_model_fit.py's export, never by hand):
#   "shipped": {"<POS>:<market>": [{"group": "OVD" | ... | "FULL", "status": "shipped" | "provisional"}]}
#              for the decision markets (DECISION_MARKETS); a market absent or [] shipped nothing.
#   "models":  {"<POS>:<target>": {"terms": [{"feature": f, "b": b, "mean": m, "sd": sd}],
#                                  "cov": [[...]] (the terms, in that order) | null, "clamp": 1.0}}
#              one fitted GLM per target; NO intercept (Q2: the level is Sleeper's line, fixed): its
#              multiplier is exp(clamped sum b (x - mean) / sd), x the feature (a missing one reads 0
#              before centring, as in the fit, or the mean for a MEAN_FILL count), mean / sd the
#              training rows'; its band the delta-method SE of that predictor.
#   "dispersion": {"<POS>": {"rush_att": {"alpha"}, "rush_yd": {"cv"}, "rec": {"alpha"}, "rec_yd": {"cv"}}}
#              the rung-1 families' parameters, refit by the rule on its own seasons (Q2), read by a
#              market priced in the "mean" form (the first model's family shape, this model's fit).
#   plus the distribution parts: "kernels", "alpha", "kneels", "qb_prior", "correlations".
# What each target multiplies (Q2 / Q3b): before the decomposition (SHAPE) is accepted a market's
# terms act on its MEAN ("mean_*", the rung-1 family carries it); from SHAPE on, on a component --
# an RB's carries / yards per carry / receptions / yards per catch, a QB's designed runs /
# scrambles and yards per designed run / per scramble; anytime TD on the TD mean.
DECISION_MARKETS = {"RB": ("rush_att", "rush_yd", "rec", "rec_yd", "td"), "QB": ("rush_att", "rush_yd", "td")}
TARGETS = {"RB": ("carries", "ypc", "rec", "ypr", "td", "mean_rush_att", "mean_rush_yd", "mean_rec", "mean_rec_yd"),
           "QB": ("designed", "scramble", "designed_eff", "scramble_eff", "td", "mean_rush_att", "mean_rush_yd")}
# the markets the first model already prices for RBs / QBs (its own blocks stay unless ours ship)
FIRST_MODEL_MARKETS = ("rush_yd", "rec", "rec_yd", "td")


def in_universe(pos, market, base):
    """The rule's row universes (Q1), on Sleeper's line: RB rushing (carries, rushing yards, rush +
    rec, the longest rush) 5+ projected carries with a yards line; RB receiving 2+ projected
    targets with a receptions line; RB anytime TD 0.15+ rush + rec TDs with projected carries or
    targets; QB rushing 20+ projected pass attempts and 2+ rushing attempts. The research frame's
    row selection and the board's both read THIS, so the model is never shown where it was never
    tested. Every RB universe needs projected carries or targets because that is what gives a
    back a dataset row whatever PFR says (research/nfl_rush_dataset.week_rows' Sleeper set): a
    back can also get a row from PFR offensive snaps alone (48 of 618 RB rows in 2026), and a
    universe that admitted such a row would let PFR choose training and scoring rows."""
    g = lambda f: _pos(base.get(f))
    if pos == "RB":
        if market in ("rush_att", "rush_yd", "rush_rec_yd", "long_rush"):
            return g("rush_att") >= 5 and g("rush_yd") > 0
        if market in ("rec", "rec_yd"):
            return g("rec_tgt") >= 2 and g("rec") > 0
        if market == "td":
            return g("rush_td") + g("rec_td") >= 0.15 and (g("rush_att") > 0 or g("rec_tgt") > 0)
    if pos == "QB" and market in ("rush_att", "rush_yd", "td", "long_rush"):
        return g("pass_att") >= 20 and g("rush_att") >= 2
    return False


def market_key(pos, market):
    return f"{pos}:{market}"


def is_matchup(feature):
    """True when a feature reads what the OPPONENT concedes (its defense blocks, the first model's
    defense deviations, the comparables) -- the contract's strong_fit counts only these, never
    game script, the team's own line or role, or teammates out."""
    for k, b, _p in spec_leaves(FEATURES[feature]):
        if (k != "ext" and b in ("d", "dq")) or (k == "ext" and (b.startswith("m1_") or b.startswith("comp_"))):
            return True
    return False


# The two non-negative counts read a MISSING value as the term's training mean (no effect), not 0:
# 0 is their minimum ("nobody out"), and a board built before the week's injury report posts reads
# None for every team, which as 0 would move every such row by -b x the mean count (the rule
# review's m4). Every other feature is a deviation, whose 0 is already "nothing known". The fit
# fills the same way (research/nfl_rush_model_rule.py fill_column), so train and serve agree.
MEAN_FILL = frozenset({"cfp_ol_out", "dec_room_out"})


def design_row(terms, feats):
    """The standardised predictors of one row: (x - mean) / sd per term, a missing x reading 0
    before centring (the fit's own fill), or the term's mean for a MEAN_FILL count. A zero or
    missing sd reads 1 (the fit never keeps a dead column: Q2)."""
    z = []
    for t in terms:
        sd = _num_or_none(t.get("sd")) or 1.0
        m = _num_or_none(t.get("mean")) or 0.0
        x = _num_or_none(feats.get(t["feature"]))
        if x is None:
            x = m if t["feature"] in MEAN_FILL else 0.0
        z.append((x - m) / (sd if sd > 0 else 1.0))
    return z


def linear(model, feats):
    """(eta, se, matchup part) of one fitted target model at a row's features:
    eta = sum b (x - mean) / sd with NO intercept (the level is Sleeper's
    line), clamped to +/- clamp (Q2: [-1, 1]); se the delta-method SE from
    its covariance (0 without one: then no band); the matchup part the
    opponent terms' share of eta, for strong_fit."""
    if not model:
        return 0.0, 0.0, 0.0
    terms = model.get("terms") or []
    z = design_row(terms, feats)
    b = [float(t.get("b") or 0.0) for t in terms]
    cap = float(model.get("clamp", 1.0))
    eta = max(-cap, min(cap, sum(x * y for x, y in zip(z, b))))
    cov = model.get("cov")
    se = 0.0
    if cov and len(cov) == len(z) and z:
        v = sum(z[i] * z[j] * float(cov[i][j]) for i in range(len(z)) for j in range(len(z)))
        se = math.sqrt(v) if v > 0 else 0.0
    mpart = sum(z[i] * b[i] for i, t in enumerate(terms) if t.get("feature") in FEATURES and is_matchup(t["feature"]))
    return eta, se, mpart


def family_dist(coefs, pos, market, mu, base):
    """The rung-1 family -- the first model's shape (NegBin counts; a point mass from the count line
    plus a gamma positive part for yards) -- at mean `mu`, with THIS rule's own refit dispersion
    (coefs["dispersion"], Q2). None without its parameter or a positive mean."""
    prm = (((coefs or {}).get("dispersion") or {}).get(pos) or {})
    if not (mu and mu > 0):
        return None
    if market in ("rush_att", "rec"):
        a = (prm.get(market) or {}).get("alpha")
        return None if a is None else PD.NegBin(mu, float(a))
    if market in ("rush_yd", "rec_yd"):
        cm, cf, yf = ("rec", "rec", "rec_yd") if market == "rec_yd" else ("rush_att", "rush_att", "rush_yd")
        a, cv = (prm.get(cm) or {}).get("alpha"), (prm.get(market) or {}).get("cv")
        by, bc = _pos(base.get(yf)), _pos(base.get(cf))
        if a is None or cv is None or by <= 0 or bc <= 0:
            return None
        p0 = min(0.999, max(0.0, PD.NegBin(bc * mu / by, float(a)).pmf(0)))
        m_pos = mu / (1.0 - p0)
        return PD.Yards(p0, m_pos, float(cv) * m_pos, "gamma")
    if market == "td":
        return PD.Poisson(mu)
    return None


def multipliers(coefs, pos, feats):
    """{target: {"m", "se", "shift"}} for every target the committed model fits at this position."""
    out = {}
    models = (coefs or {}).get("models") or {}
    for t in TARGETS.get(pos, ()):
        md = models.get(market_key(pos, t))
        if md:
            eta, se, mp = linear(md, feats)
            out[t] = {"m": math.exp(eta), "se": se, "shift": mp}
    return out


def market_form(coefs, pos, market):
    """How a decision market is priced: "compound" (its DEC group shipped), "mean" (only mean groups
    shipped: the first model's family at Sleeper x the mean factor) or "rung1" (nothing shipped)."""
    key = market_key(pos, market)
    if uses_compound(coefs, key):
        return "compound"
    return "mean" if ship_status(coefs, key) != "0" else "rung1"


def _status_of(*statuses):
    s = set(statuses)
    return "provisional" if "provisional" in s else ("shipped" if "shipped" in s else "0")


def plan(coefs, pos, base, feats, prof=None, team_margin=None):
    """Everything the board needs to price one RB / QB from the committed model: per market its
    form, status, distribution builder (`dist_fn(scale)`: scale 1 is the model, the band moves the
    priced part's mean by exp(-/+1.96 se)), band SE, matchup shift and the parts for the breakdown.

    Forms: "compound" / "poisson" (this module's distributions), "mean" (family_dist, the rung-1
    family with this rule's dispersion, at mean x `mult`), "rung1" (nothing shipped: the caller
    keeps the first model's market byte for byte, and prices no market it does not already have),
    "derived" (rush + rec yards, the longest rush: the compound through components whose SHAPE
    shipped, their status) and "none" (a derived market whose components did not ship: unpriced)."""
    mt = multipliers(coefs, pos, feats)
    g = lambda t, f="m", d=1.0: (mt.get(t) or {}).get(f, d)
    se2 = lambda *ts: math.sqrt(sum(g(t, "se", 0.0) ** 2 for t in ts))
    sh = lambda *ts: sum(g(t, "shift", 0.0) for t in ts)
    forms = {m: market_form(coefs, pos, m) for m in DECISION_MARKETS[pos]}
    stat = {m: ship_status(coefs, market_key(pos, m)) for m in DECISION_MARKETS[pos]}
    out = {"forms": forms, "status": stat, "markets": {}, "parts": None}

    def put(m, form, status, fn=None, se=0.0, shift=0.0, mult=None, note=None):
        if fn is not None:
            fn = _unwrapped(fn)
        out["markets"][m] = {"form": form, "status": status, "dist_fn": fn, "se": se, "shift": shift,
                             "mult": mult, "note": note}
    if pos == "RB":
        # the count-level and yards-level factors each component carries (Q3b: yards per attempt is
        # fitted with the count's ACCEPTED log factor in its offset)
        def levels(cm, ym, ct, et, mc, my):
            c_t = (ct,) if forms[cm] == "compound" else (mc,) if forms[cm] == "mean" else ()
            C = g(ct) if forms[cm] == "compound" else g(mc) if forms[cm] == "mean" else 1.0
            if forms[ym] == "compound":
                return C, C * g(et), c_t, c_t + (et,)
            if forms[ym] == "mean":
                return C, g(my), c_t, (my,)
            return C, 1.0, c_t, ()
        Cr, Yr, ctr, ytr = levels("rush_att", "rush_yd", "carries", "ypc", "mean_rush_att", "mean_rush_yd")
        Cc, Yc, ctc, ytc = levels("rec", "rec_yd", "rec", "ypr", "mean_rec", "mean_rec_yd")

        def rb(scale_c=1.0, scale_y=1.0, which="rush"):
            mult = {"carries": Cr, "ypc": Yr / Cr, "rec": Cc, "ypr": Yc / Cc}
            if which == "rush":
                mult["carries"] *= scale_c
                mult["ypc"] *= scale_y / scale_c
            elif which == "rec":
                mult["rec"] *= scale_c
                mult["ypr"] *= scale_y / scale_c
            else:      # rush + rec: the yards level of both
                mult["ypc"] *= scale_y
                mult["ypr"] *= scale_y
            return rb_dists(base, coefs, mult)[0]
        out["parts"] = rb_parts(base, coefs, {"carries": Cr, "ypc": Yr / Cr, "rec": Cc, "ypr": Yc / Cc})
        for m, cm, which, ct, yt in (("rush_att", "rush_att", "rush", ctr, None), ("rush_yd", "rush_yd", "rush", ctr, ytr),
                                     ("rec", "rec", "rec", ctc, None), ("rec_yd", "rec_yd", "rec", ctc, ytc)):
            f = forms[cm]
            count = m in ("rush_att", "rec")
            if f == "compound":
                fn = ((lambda s, w=which: rb(scale_c=s, which=w).get("rush_att" if w == "rush" else "rec")) if count else
                      (lambda s, w=which, mm=m: rb(scale_y=s, which=w).get(mm)))
                put(m, "compound", stat[cm], fn, se2(*(ct if count else yt)), sh(*(ct if count else yt)))
            elif f == "mean":
                t = ("mean_" + m)
                put(m, "mean", stat[cm], None, g(t, "se", 0.0), g(t, "shift", 0.0), mult=g(t))
            else:
                put(m, "rung1", "0")
        # derived markets (Q3): priced ONLY through components whose compound shipped -- rush + rec
        # needs both yards markets' SHAPE, the longest rush the rushing yards' SHAPE; else unpriced
        if forms["rush_yd"] == "compound" and forms["rec_yd"] == "compound":
            put("rush_rec_yd", "derived", _status_of(stat["rush_yd"], stat["rec_yd"]),
                lambda s: rb(scale_y=s, which="both").get("rush_rec_yd"), se2(*(ytr + ytc)), sh(*(ytr + ytc)))
        else:
            put("rush_rec_yd", "none", "0")
        if forms["rush_yd"] == "compound":
            put("long_rush", "derived", stat["rush_yd"], lambda s: rb(scale_y=s, which="rush").get("long_rush"),
                se2(*ytr), sh(*ytr))
        else:
            put("long_rush", "none", "0")
        if stat["td"] != "0":
            td0 = _pos(base.get("rush_td")) + _pos(base.get("rec_td"))
            put("td", "poisson", stat["td"], (lambda s: PD.Poisson(td0 * g("td") * s) if td0 > 0 else None),
                g("td", "se", 0.0), g("td", "shift", 0.0))
        else:
            put("td", "rung1", "0")
        return out
    # quarterbacks: the components' count and yards-per-attempt factors
    fa, fy = forms["rush_att"], forms["rush_yd"]
    if fa == "compound":
        cmult = {"designed": g("designed"), "scramble": g("scramble")}
        ct = ("designed", "scramble")
    else:
        cmult = {}
        ct = ("mean_rush_att",) if fa == "mean" else ()
    A0, Y0 = _pos(base.get("rush_att")), float(base.get("rush_yd") or 0.0)
    m_att = g("mean_rush_att") if fa == "mean" else 1.0      # a mean-level attempts factor (rungs 2-4)

    def qb_in(scale_c=1.0, scale_y=1.0):
        """(base, mult) for qb_dists. The anchoring sets the yards level from base rush_yd, so the
        yards level carries the count's accepted factor when only a MEAN attempts model shipped
        (a compound attempts model moves the counts after the anchoring, which carries it)."""
        bb = dict(base, rush_att=A0 * m_att * (scale_c if fa != "compound" else 1.0))
        mult = {k: v * (scale_c if fa == "compound" else 1.0) for k, v in cmult.items()}
        if fy == "compound":
            mult["designed_eff"] = g("designed_eff") * scale_y
            mult["scramble_eff"] = g("scramble_eff") * scale_y
            bb["rush_yd"] = Y0 * m_att
        elif fy == "mean":
            bb["rush_yd"] = Y0 * g("mean_rush_yd") * scale_y
        else:
            bb["rush_yd"] = Y0 * scale_y
        return bb, mult

    def qb(scale_c=1.0, scale_y=1.0):
        bb, mult = qb_in(scale_c, scale_y)
        return qb_dists(bb, prof, coefs, team_margin, mult)[0]
    yt = ct + (("designed_eff", "scramble_eff") if fy == "compound" else ("mean_rush_yd",) if fy == "mean" else ())
    out["parts"] = qb_parts(*qb_in()[:1], prof, coefs, team_margin, qb_in()[1])
    if fa == "compound":
        put("rush_att", "compound", stat["rush_att"], lambda s: qb(scale_c=s).get("rush_att"), se2(*ct), sh(*ct))
    elif fa == "mean":
        put("rush_att", "mean", stat["rush_att"], None, g("mean_rush_att", "se", 0.0), g("mean_rush_att", "shift", 0.0),
            mult=g("mean_rush_att"))
    else:
        put("rush_att", "rung1", "0")
    if fy == "compound":
        put("rush_yd", "compound", stat["rush_yd"], lambda s: qb(scale_y=s).get("rush_yd"), se2(*yt), sh(*yt))
    elif fy == "mean":
        put("rush_yd", "mean", stat["rush_yd"], None, g("mean_rush_yd", "se", 0.0), g("mean_rush_yd", "shift", 0.0),
            mult=g("mean_rush_yd"))
    else:
        put("rush_yd", "rung1", "0")
    if fy == "compound":
        put("long_rush", "derived", stat["rush_yd"], lambda s: qb(scale_y=s).get("long_rush"), se2(*yt), sh(*yt))
    else:
        put("long_rush", "none", "0")
    if stat["td"] != "0":
        td0 = _pos(base.get("rush_td")) + _pos(base.get("rec_td"))
        put("td", "poisson", stat["td"], (lambda s: PD.Poisson(td0 * g("td") * s) if td0 > 0 else None),
            g("td", "se", 0.0), g("td", "shift", 0.0))
    else:
        put("td", "rung1", "0")
    return out


def _unwrapped(fn):
    """A distribution builder that returns None for a compound whose grid wrapped (Compound.wrapped,
    ledgered NFLR-grid-wrap): the market is then unpriced, never priced from a wrapped grid."""
    def g(scale):
        d = fn(scale)
        return None if getattr(d, "wrapped", False) else d
    return g


def expected_longest(dist, kmax=150):
    """E[the longest attempt, counted 0 when it is not positive] = sum over k >= 1 of P(longest >= k)."""
    try:
        return sum(dist.sf_ge(k) for k in range(1, kmax + 1))
    except (TypeError, ValueError):
        return None
