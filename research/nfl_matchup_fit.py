"""The NFL matchup model: the pre-registered ladder, its fit (2023-2024),
validation (2025) and one-shot test (2026 weeks 1-6), and the committed
coefficients production reads. RESEARCH (numpy).

    export DEEP_CACHE_DIR=<store dir> VIGIL_NO_BG=1 W=<work dir>
    python3 -m research.nfl_matchup_frame --out $W/frame.csv.gz --defhist $W/def_history.json \\
        --sleeper <sleeper dir> --props <props_w06.json>
    python3 -m research.nfl_matchup_fit traincheck --frame $W/frame.csv.gz     # 2023-24 only (CV), no 2025 row read
    python3 -m research.nfl_matchup_fit prereg                                 # hash the rule block of the report
    python3 -m research.nfl_matchup_fit validate --frame $W/frame.csv.gz       # the ladder on 2025, decisions
    python3 -m research.nfl_matchup_fit test --frame $W/frame.csv.gz           # once: 2026 frozen + revised
    python3 -m research.nfl_matchup_fit export --frame $W/frame.csv.gz --defhist $W/def_history.json

Every number in research/reports/nfl_matchup_model.md comes out of these
commands into research/data/nfl_matchup_model/*.json. The rule they follow
is the block between the PREREG markers in the report; `validate` and `test`
refuse to run when that block's sha256 differs from prereg.json, and `test`
refuses a second run (test_lock.json).

THE MODEL per market m: y ~ quasi-Poisson(mean = base_m x exp(X b)), base_m
Sleeper's line, X the market's features (nfl_matchup.MARKET_FEATURES) for
the groups accepted so far, scaled by their training SD, ridge penalty
lambda chosen by leave-one-season-out CV on the fitting seasons (Poisson
deviance), the position intercepts (group R) unpenalised. Dispersion is fit
ONCE per (market, position) on the Sleeper baseline's training residuals and
held fixed across rungs, so a rung's score moves only through its mean.
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("VIGIL_NO_BG", "1")

import numpy as np                       # noqa: E402

import nfl_matchup as M                  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "data", "nfl_matchup_model")
REPORT = os.path.join(HERE, "reports", "nfl_matchup_model.md")
PRE_A, PRE_B = "<!-- PREREG-START -->", "<!-- PREREG-END -->"
LAMBDAS = (1.0, 10.0, 100.0, 300.0, 1000.0, 3000.0, 1e4, 3e4, 1e5, 1e6)
EPS = 1e-6
FIT_SEASONS, VAL_SEASON, TEST_SEASON = (2023, 2024), 2025, 2026

# market -> (outcome field, Sleeper field, positions, family)
SPEC = {
    "tgt": ("rec_tgt", "rec_tgt", ("WR", "TE", "RB"), "negbin"),
    "rec": ("rec", "rec", ("WR", "TE", "RB"), "negbin"),
    "rec_yd": ("rec_yd", "rec_yd", ("WR", "TE", "RB"), "yards"),
    "car": ("rush_att", "rush_att", ("RB", "QB", "WR"), "negbin"),
    "rush_yd": ("rush_yd", "rush_yd", ("RB", "QB", "WR"), "yards"),
    "patt": ("pass_att", "pass_att", ("QB",), "negbin"),
    "pcmp": ("pass_cmp", "pass_cmp", ("QB",), "negbin"),
    "pass_yd": ("pass_yd", "pass_yd", ("QB",), "normal"),
    "ptd": ("pass_td", "pass_td", ("QB",), "negbin"),
    "int": ("pass_int", "pass_int", ("QB",), "negbin"),
}
# the count market whose zero probability a yards market's point mass reads
P0_OF = {"rec_yd": "rec", "rush_yd": "car"}
INTERACT_POS = {"tgt": ("TE", "RB"), "rec": ("TE", "RB"), "rec_yd": ("TE", "RB"), "car": ("QB", "WR"),
                "rush_yd": ("QB", "WR")}


def log(msg):
    print(msg, flush=True)


# ---- the frame ---------------------------------------------------------------------
STR_COLS = {"game_id", "team", "opp", "pos", "name", "sleeper_id", "fz_src", "y_src", "qb_last", "qb_sleeper_rev",
            "qb_frozen", "qb_used"}


class Frame:
    def __init__(self, path, seasons):
        with gzip.open(path, "rt", newline="") as fh:
            rd = csv.DictReader(fh)
            cols = rd.fieldnames
            rows = [r for r in rd if int(r["season"]) in seasons]
        self.n = len(rows)
        self.cols = cols
        self.s = {}
        self.x = {}
        for c in cols:
            if c in STR_COLS:
                self.s[c] = np.array([r[c] for r in rows], dtype=object)
            else:
                self.x[c] = np.array([float(r[c]) if r[c] not in ("", None) else np.nan for r in rows])
        self.seasons = sorted(set(self.x["season"].astype(int).tolist())) if self.n else []

    def __getitem__(self, c):
        return self.s[c] if c in self.s else self.x[c]

    def has(self, c):
        return c in self.s or c in self.x


def base_line(F, field, kind):
    """Sleeper's line: 'rev' (the post-week revised feed, every season) or
    'fz' (the frozen pre-kickoff capture, 2026)."""
    c = ("sl_" if kind == "rev" else "fz_") + field
    if not F.has(c):
        return np.full(F.n, np.nan)
    return F[c].copy()


def y_of(F, field):
    return np.nan_to_num(F["y_" + field].copy(), nan=0.0)


def fit_mask(F, m, kind="rev"):
    _y, bf, poss, _fam = SPEC[m]
    b = base_line(F, bf, kind)
    ok = np.isin(F["pos"], poss) & (b > 0)
    if m in M.PASS:
        ok &= base_line(F, "pass_att", kind) >= 10
    return ok


def eval_mask(F, m, kind="rev"):
    """The priced universe the ladder scores (pre-registered)."""
    pos = F["pos"]
    tg = base_line(F, "rec_tgt", kind)
    car = base_line(F, "rush_att", kind)
    att = base_line(F, "pass_att", kind)
    if m in ("tgt", "rec", "rec_yd"):
        ok = np.isin(pos, ("WR", "TE", "RB")) & (tg >= 2)
    elif m in ("car", "rush_yd"):
        ok = ((pos == "RB") & (car >= 5)) | ((pos == "QB") & (car >= 2))
    elif m in M.PASS:
        ok = (pos == "QB") & (att >= 20)
    elif m == "td":
        rt, ut = base_line(F, "rec_td", kind), base_line(F, "rush_td", kind)
        ok = np.isin(pos, M.POS) & ((np.nan_to_num(rt) + np.nan_to_num(ut)) >= 0.15)
    else:
        raise KeyError(m)
    if m in SPEC:
        ok &= base_line(F, SPEC[m][1], kind) > 0
    return ok


# ---- design matrices --------------------------------------------------------------
def group_features(m, g):
    return tuple(M.MARKET_FEATURES.get(g, {}).get(m, ()))


def all_features(m):
    out = []
    for g in ("V", "D2", "D3", "D4", "D5", "D6"):
        for f in group_features(m, g):
            if f not in out:
                out.append(f)
    return out


def terms_for(m, groups):
    """The design's terms for a market given a set of groups: ("int", pos) for
    the R intercepts, ("f", name), ("x", name, pos) for a position
    interaction, ("xm", name) for a x margin interaction (passing D7),
    ("xt", name) for x target share (receiving D7)."""
    terms = []
    poss = SPEC[m][2]
    if "R" in groups:
        terms += [("int", p) for p in poss]
    base = []
    for g in ("V", "D2", "D3", "D4", "D5", "D6"):
        if g in groups:
            base += [f for f in group_features(m, g) if f not in base]
    if "D7" in groups:
        base = all_features(m)
    terms += [("f", f) for f in base]
    if "D7" in groups:
        if m in INTERACT_POS:
            for f in base:
                for p in INTERACT_POS[m]:
                    terms.append(("x", f, p))
            if m in M.RECV:
                terms.append(("xt", "cov_mz"))
        else:
            for f in base:
                if f != "margin":
                    terms.append(("xm", f))
    return terms


def term_name(t):
    if t[0] == "int":
        return f"int[{t[1]}]"
    if t[0] == "f":
        return t[1]
    if t[0] == "x":
        return f"{t[1]}x{t[2]}"
    if t[0] == "xm":
        return f"{t[1]}xmargin"
    return f"{t[1]}xtshare"


def design(F, idx, terms, qb_variant="used"):
    cols = []
    pos = F["pos"][idx]
    for t in terms:
        if t[0] == "int":
            cols.append((pos == t[1]).astype(float))
            continue
        nm = t[1]
        src = "fs_" + nm if (qb_variant == "sleeper_rev" and F.has("fs_" + nm)) else "f_" + nm
        v = np.nan_to_num(F[src][idx], nan=0.0)
        if t[0] == "f":
            cols.append(v)
        elif t[0] == "x":
            cols.append(v * (pos == t[2]))
        elif t[0] == "xm":
            cols.append(v * np.nan_to_num(F["f_margin"][idx], nan=0.0))
        else:
            ts = np.nan_to_num(F["p_tshare"][idx], nan=0.15)
            cols.append(v * (ts - 0.15) / 0.1)
    return np.column_stack(cols) if cols else np.zeros((len(idx), 0))


# ---- the GLM ------------------------------------------------------------------------
def fit_glm(X, y, off, lam, pen, iters=60):
    """Ridge-penalised Poisson (quasi-likelihood) with an offset, by Newton.
    Returns (b, H) with H the penalised information X'WX + lam P."""
    n, p = X.shape
    b = np.zeros(p)
    y = np.maximum(y, 0.0)
    P = np.diag(lam * pen + 1e-8)
    H = P
    for _ in range(iters):
        eta = np.clip(off + X @ b, -30, 30)
        mu = np.exp(eta)
        g = X.T @ (y - mu) - P @ b
        H = (X.T * mu) @ X + P
        step = np.linalg.solve(H, g)
        b = b + step
        if np.max(np.abs(step)) < 1e-9:
            break
    return b, H


def deviance(y, mu):
    y = np.maximum(y, 0.0)
    mu = np.maximum(mu, 1e-9)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(y > 0, y * np.log(y / mu), 0.0)
    return 2.0 * np.sum(t - (y - mu))


class Model:
    """One market's fitted mean model."""

    def __init__(self, m, terms):
        self.m, self.terms = m, terms
        self.b = np.zeros(len(terms))
        self.scale = np.ones(len(terms))
        self.cov = np.zeros((len(terms), len(terms)))
        self.lam = None
        self.cv = {}
        self.phi = 1.0

    def pen(self):
        return np.array([0.0 if t[0] == "int" else 1.0 for t in self.terms])

    def fit(self, F, idx, kind="rev", qb_variant="used"):
        y = y_of(F, SPEC[self.m][0])[idx]
        off = np.log(base_line(F, SPEC[self.m][1], kind)[idx])
        if not self.terms:
            return self
        X = design(F, idx, self.terms, qb_variant)
        sd = X.std(axis=0)
        sd[sd < 1e-12] = 1.0
        sd[[i for i, t in enumerate(self.terms) if t[0] == "int"]] = 1.0
        self.scale = sd
        Xs = X / sd
        seasons = F["season"][idx].astype(int)
        us = sorted(set(seasons.tolist()))
        if len(us) >= 2:
            folds = seasons                      # leave one season out
        else:
            folds = (F["week"][idx].astype(int) % 2)   # one season (traincheck only): odd vs even weeks
            us = [0, 1]
        best = None
        for lam in LAMBDAS:
            dev = 0.0
            for s in us:
                tr, te = folds != s, folds == s
                if tr.sum() < 50 or te.sum() < 10:
                    continue
                b, _ = fit_glm(Xs[tr], y[tr], off[tr], lam, self.pen())
                dev += deviance(y[te], np.exp(off[te] + Xs[te] @ b))
            self.cv[lam] = dev
            if best is None or dev < best[1]:
                best = (lam, dev)
        self.lam = best[0]
        b, H = fit_glm(Xs, y, off, self.lam, self.pen())
        mu = np.exp(off + Xs @ b)
        dfree = max(1, len(y) - len(b))
        self.phi = float(np.sum((y - mu) ** 2 / np.maximum(mu, 1e-9)) / dfree)
        Hi = np.linalg.inv(H)
        self.b = b / sd
        self.cov = self.phi * Hi / np.outer(sd, sd)
        return self

    def predict(self, F, idx, kind="rev", qb_variant="used"):
        base = base_line(F, SPEC[self.m][1], kind)[idx]
        if not self.terms:
            return base.copy(), np.zeros(len(idx))
        X = design(F, idx, self.terms, qb_variant)
        eta = np.clip(X @ self.b, -1.0, 1.0)      # production's guard rail (nfl_matchup.market_adjust)
        se = np.sqrt(np.maximum(0.0, np.einsum("ij,jk,ik->i", X, self.cov, X)))
        return base * np.exp(eta), se

    @classmethod
    def from_json(cls, m, js):
        mod = cls(m, [tuple(t) for t in js.get("terms") or []])
        mod.b = np.array(js.get("b") or [], float)
        mod.cov = np.array(js.get("cov") or [], float).reshape(len(mod.terms), len(mod.terms)) if mod.terms else \
            np.zeros((0, 0))
        mod.lam = js.get("lambda")
        return mod

    def to_json(self):
        return {"terms": [list(t) for t in self.terms], "names": [term_name(t) for t in self.terms],
                "b": [float(v) for v in self.b], "cov": [[float(v) for v in r] for r in self.cov],
                "lambda": self.lam, "cv_deviance": {str(k): float(v) for k, v in self.cv.items()},
                "phi": self.phi, "train_sd": [float(v) for v in self.scale]}


# ---- distributions (numpy twins of the production families; the guard
# ---- `distribution_agreement` checks them against prop_dist) -----------------------
_erfc = np.frompyfunc(math.erfc, 1, 1)


def norm_sf(z):
    return 0.5 * _erfc(np.asarray(z, float) / math.sqrt(2.0)).astype(float)


def nb_sf(mu, alpha, k):
    """P(X >= k) for NegBin(mean mu, var mu + alpha mu^2) (Poisson at alpha 0)."""
    mu = np.maximum(np.asarray(mu, float), 1e-12)
    if k <= 0:
        return np.ones_like(mu)
    if alpha < 1e-6:
        p = np.exp(-mu)
        cdf = p.copy()
        for j in range(k - 1):
            p = p * mu / (j + 1)
            cdf += p
    else:
        r = 1.0 / alpha
        q = mu / (r + mu)
        p = np.exp(r * np.log(r / (r + mu)))
        cdf = p.copy()
        for j in range(k - 1):
            p = p * (j + r) / (j + 1) * q
            cdf += p
    return np.clip(1.0 - cdf, 0.0, 1.0)


def nb_p0(mu, alpha):
    mu = np.maximum(np.asarray(mu, float), 1e-12)
    if alpha < 1e-6:
        return np.exp(-mu)
    r = 1.0 / alpha
    return np.exp(r * np.log(r / (r + mu)))


def gammaq(a, x):
    """Regularised upper incomplete gamma Q(a, x), vectorised (series below
    a + 1, Lentz continued fraction above)."""
    a = np.broadcast_to(np.asarray(a, float), np.broadcast(np.asarray(a), np.asarray(x)).shape).copy()
    x = np.broadcast_to(np.asarray(x, float), a.shape).copy()
    out = np.ones_like(x)
    pos = x > 0
    lg = np.zeros_like(x)
    lgam = np.frompyfunc(math.lgamma, 1, 1)
    lg[pos] = -x[pos] + a[pos] * np.log(x[pos]) - lgam(a[pos]).astype(float)
    ser = pos & (x < a + 1)
    if ser.any():
        aa, xx = a[ser], x[ser]
        ap = aa.copy()
        term = 1.0 / aa
        tot = term.copy()
        for _ in range(500):
            ap += 1.0
            term = term * xx / ap
            tot += term
            if np.all(np.abs(term) < np.abs(tot) * 1e-15):
                break
        out[ser] = 1.0 - np.clip(tot * np.exp(lg[ser]), 0.0, 1.0)
    cf = pos & ~ser
    if cf.any():
        aa, xx = a[cf], x[cf]
        b = xx + 1.0 - aa
        c = np.full_like(xx, 1e300)
        d = 1.0 / b
        h = d.copy()
        for i in range(1, 500):
            an = -i * (i - aa)
            b = b + 2.0
            d = an * d + b
            d = np.where(np.abs(d) < 1e-300, 1e-300, d)
            c = b + an / c
            c = np.where(np.abs(c) < 1e-300, 1e-300, c)
            d = 1.0 / d
            de = d * c
            h = h * de
            if np.all(np.abs(de - 1.0) < 1e-15):
                break
        out[cf] = np.clip(np.exp(lg[cf]) * h, 0.0, 1.0)
    return out


def yards_sf(p0, mean_all, cv, k, family="gamma"):
    """P(Y >= k) for the point-mass-at-0 + positive-part yards family with the
    positive part's coefficient of variation cv (production: nfl_matchup.yards_dist)."""
    p0 = np.clip(p0, 0.0, 0.999)
    m_pos = np.maximum(mean_all, 1e-6) / (1.0 - p0)
    x = k - 0.5
    if family == "gamma":
        shape = 1.0 / (cv * cv)
        scale = m_pos * cv * cv
        s = gammaq(np.full_like(m_pos, shape), x / scale)
    else:
        s2 = math.log1p(cv * cv)
        s = norm_sf((np.log(x) - (np.log(m_pos) - 0.5 * s2)) / math.sqrt(s2))
    return (1.0 - p0) * s


def normal_sf(mu, sd, k):
    """NormalCC(mu, sd, lower=0): the latent normal left-truncated at -0.5."""
    if k <= 0:
        return np.ones_like(np.asarray(mu, float))
    return np.minimum(1.0, norm_sf((k - 0.5 - mu) / sd) / norm_sf((-0.5 - mu) / sd))


def fp_sf(mean, var, k, shift):
    """DK points: (X + shift) ~ Gamma with the given mean and variance."""
    m = np.maximum(mean + shift, 1e-3)
    v = np.maximum(var, 1e-3)
    shape = m * m / v
    scale = v / m
    return gammaq(shape, np.maximum(k + shift, 0.0) / scale)


# ---- dispersion (fit once on the Sleeper baseline, training seasons) -----------------
YARDS_FAMILIES = ("gamma", "lognormal")


def choose_family(ll_by_family):
    """The family with the lowest mean threshold log loss over the rows EVERY
    family scores (finite in all), ties and an empty comparison going to the
    first family named. Review F1 (2026-10-23): the first version took
    np.mean over the whole universe, where rows with a yards line but no count
    line score NaN (rush_yd 2023-2025: 62 such rows, 60 of them WRs), so both
    means were NaN, `nan < nan` was False and the first family in the loop
    (gamma) won whatever the data said."""
    fams = list(ll_by_family)
    arrs = [np.asarray(ll_by_family[f], float) for f in fams]
    ok = np.ones(len(arrs[0]), bool) if arrs else np.zeros(0, bool)
    for a in arrs:
        ok &= np.isfinite(a)
    if not fams or not ok.any():
        return (fams[0] if fams else "gamma"), {f: None for f in fams}, 0
    means = {f: float(np.mean(a[ok])) for f, a in zip(fams, arrs)}
    best = fams[0]
    for f in fams[1:]:
        if means[f] < means[best]:
            best = f
    return best, means, int(ok.sum())


def fit_dispersion(F, idx_by_market, kind="rev", family_rows=None, family=None):
    """{market: {pos: params}} on the baseline's training residuals, plus each
    yards market's positive-part family under "family" (a key strip() keeps,
    so it reaches coefs.json and production's market_dist; it used to sit
    under "_family", which every JSON this module writes dropped).

    The family (R2 as amended 2026-10-23, report section 2a): `family`
    {market: name} carries a choice already made (the test and the export
    carry the validation's); else it is chosen by choose_family on
    `family_rows` {market: row indices} -- the VALIDATION season's priced rows
    in cmd_validate -- and only by default on the fitting rows themselves.
    On the fitting rows the written rule picks lognormal for rushing yards
    (1.366 vs 1.392 nats, 2023-2024), and lognormal is WORSE out of sample
    on 2025 and on 2026's frozen lines; that is why the amendment chooses
    on held-out rows."""
    disp = {}
    for m, (yf, bf, poss, fam) in SPEC.items():
        disp[m] = {}
        idx = idx_by_market[m]
        y = y_of(F, yf)[idx]
        mu = base_line(F, bf, kind)[idx]
        pos = F["pos"][idx]
        for p in poss:
            sel = pos == p
            if sel.sum() < 30:
                continue
            yy, mm = y[sel], mu[sel]
            if fam == "negbin":
                a = float(np.sum((yy - mm) ** 2 - mm) / np.sum(mm * mm))
                disp[m][p] = {"alpha": max(0.0, a), "n": int(sel.sum())}
            elif fam == "normal":
                # sd = c x mu^d over deciles of mu (binned RMS, log-log least squares)
                q = np.quantile(mm, np.linspace(0, 1, 11))
                xs, ys = [], []
                for lo, hi in zip(q[:-1], q[1:]):
                    b = (mm >= lo) & (mm <= hi)
                    if b.sum() >= 10:
                        xs.append(np.log(np.mean(mm[b])))
                        ys.append(np.log(np.sqrt(np.mean((yy[b] - mm[b]) ** 2))))
                d, c = np.polyfit(xs, ys, 1)
                disp[m][p] = {"sd_c": float(math.exp(c)), "sd_d": float(d), "n": int(sel.sum())}
            else:
                disp[m][p] = {"n": int(sel.sum())}
    # yards: positive-part CV by MoM, p0 from the count market at the scaled mean
    for m in ("rec_yd", "rush_yd"):
        cm = P0_OF[m]
        idx = idx_by_market[m]
        y = y_of(F, SPEC[m][0])[idx]
        mu = base_line(F, SPEC[m][1], kind)[idx]
        cnt = base_line(F, SPEC[cm][1], kind)[idx]
        pos = F["pos"][idx]
        for p in SPEC[m][2]:
            sel = (pos == p) & (cnt > 0)
            if sel.sum() < 30 or p not in disp[cm]:
                continue
            p0 = nb_p0(cnt[sel], disp[cm][p]["alpha"])
            m_pos = mu[sel] / (1.0 - np.clip(p0, 0, 0.999))
            yy = y[sel]
            ok = yy > 0
            cv = float(math.sqrt(np.sum((yy[ok] - m_pos[ok]) ** 2) / np.sum(m_pos[ok] ** 2)))
            disp[m][p] = {"cv": cv, "n": int(sel.sum())}
    # the yards family: carried, or chosen on the given rows (the baseline's threshold log loss)
    for m in ("rec_yd", "rush_yd"):
        if family and family.get(m):
            if family[m] not in YARDS_FAMILIES:
                raise SystemExit(f"{m}: no yards family {family[m]!r}")
            disp[m]["family"] = family[m]
            continue
        rows = (family_rows or {}).get(m)
        rows = idx_by_market[m] if rows is None else rows
        mu = base_line(F, SPEC[m][1], kind)[rows]
        best, _means, _n = choose_family({fam: market_ll(F, rows, m, mu, disp, kind, family=fam)
                                          for fam in YARDS_FAMILIES})
        disp[m]["family"] = best
    return disp


def market_probs(F, idx, m, mu, disp, kind="rev", family=None, count_mu=None, ks=None):
    """{k: P(stat >= k)} arrays for one market at means mu."""
    pos = F["pos"][idx]
    out = {}
    fam = SPEC[m][3] if m in SPEC else "td"
    for k in (ks or M.THRESHOLDS[m]):
        p = np.full(len(idx), np.nan)
        for ps in np.unique(pos):
            sel = pos == ps
            prm = disp.get(m, {}).get(ps)
            if fam == "negbin":
                if prm is None:
                    continue
                p[sel] = nb_sf(mu[sel], prm["alpha"], k)
            elif fam == "normal":
                if prm is None:
                    continue
                sd = prm["sd_c"] * np.maximum(mu[sel], 1.0) ** prm["sd_d"]
                p[sel] = normal_sf(mu[sel], sd, k)
            elif fam == "yards":
                cm = P0_OF[m]
                cp = disp.get(cm, {}).get(ps)
                if prm is None or cp is None:
                    continue
                base_cnt = base_line(F, SPEC[cm][1], kind)[idx][sel]
                base_y = base_line(F, SPEC[m][1], kind)[idx][sel]
                cmu = count_mu[sel] if count_mu is not None else base_cnt * mu[sel] / np.maximum(base_y, 1e-6)
                p0 = nb_p0(np.maximum(cmu, 1e-6), cp["alpha"])
                p[sel] = yards_sf(p0, mu[sel], prm["cv"], k, family or disp[m].get("family", "gamma"))
        out[k] = np.clip(p, EPS, 1.0 - EPS)
    return out


def td_probs(mu):
    return {k: np.clip(nb_sf(mu, 0.0, k), EPS, 1 - EPS) for k in M.THRESHOLDS["td"]}


def ll_from_probs(probs, y):
    tot = np.zeros(len(y))
    for k, p in probs.items():
        hit = (y >= k).astype(float)
        tot -= hit * np.log(p) + (1 - hit) * np.log(1 - p)
    return tot


def brier_from_probs(probs, y):
    tot = np.zeros(len(y))
    for k, p in probs.items():
        hit = (y >= k).astype(float)
        tot += (p - hit) ** 2
    return tot / max(1, len(probs))


def market_ll(F, idx, m, mu, disp, kind="rev", family=None):
    y = y_of(F, SPEC[m][0])[idx]
    return ll_from_probs(market_probs(F, idx, m, mu, disp, kind, family), y)


# ---- paired comparison, clustered by game ------------------------------------------
def clustered(delta, games):
    """(mean, SE) of a per-row difference with rows clustered by game."""
    n = len(delta)
    if n == 0:
        return float("nan"), float("nan")
    mean = float(np.mean(delta))
    ug, inv = np.unique(games, return_inverse=True)
    sums = np.bincount(inv, weights=delta - mean)
    G = len(ug)
    var = (G / max(1, G - 1)) * np.sum(sums ** 2) / (n * n)
    return mean, float(math.sqrt(var))


def metrics(F, idx, m, mu, disp, kind):
    y = y_of(F, SPEC[m][0])[idx] if m in SPEC else None
    probs = market_probs(F, idx, m, mu, disp, kind)
    ll = ll_from_probs(probs, y)
    if not np.all(np.isfinite(ll)) or not np.all(np.isfinite(mu)):
        raise SystemExit(f"non-finite score in {m} ({kind}): a scored row has no fitted family -- fix the universe")
    br = brier_from_probs(probs, y)
    pos = F["pos"][idx]
    by_pos = {}
    for p in np.unique(pos):
        s_ = pos == p
        by_pos[p] = {"n": int(s_.sum()), "mae": float(np.mean(np.abs(y[s_] - mu[s_]))),
                     "rmse": float(np.sqrt(np.mean((y[s_] - mu[s_]) ** 2))), "ll": float(np.mean(ll[s_])),
                     "brier": float(np.mean(br[s_]))}
    return {"n": int(len(idx)), "mae": float(np.mean(np.abs(y - mu))), "rmse": float(np.sqrt(np.mean((y - mu) ** 2))),
            "dev": float(deviance(y, mu) / max(1, len(idx))), "ll": float(np.mean(ll)),
            "brier": float(np.mean(br)), "by_pos": by_pos, "_ll": ll, "_probs": probs, "_y": y}


# ---- the ladder ---------------------------------------------------------------------
def fit_idx(F, m, seasons, kind="rev"):
    return np.where(fit_mask(F, m, kind) & np.isin(F["season"].astype(int), seasons))[0]


def run_ladder(F, fit_seasons, eval_season, disp, qb_variant="used", log_each=True):
    """The pre-registered sequential ladder for every market. Returns
    {market: {"rungs": [...], "accepted": [...]}}."""
    out = {}
    for m in SPEC:
        fi = fit_idx(F, m, fit_seasons)
        ei = np.where(eval_mask(F, m) & (F["season"].astype(int) == eval_season))[0]
        games = F["game_id"][ei]
        accepted = []
        base_model = Model(m, [])
        mu0, _ = base_model.predict(F, ei)
        cur = metrics(F, ei, m, mu0, disp, "rev")
        rungs = [{"rung": "1", "group": "Sleeper baseline", **{k: v for k, v in cur.items() if not k.startswith("_")}}]
        for g in M.GROUPS:
            terms = terms_for(m, accepted + [g])
            if g != "R" and len(terms) == len(terms_for(m, accepted)):
                rungs.append({"rung": g, "group": M.GROUP_LABEL[g], "skipped": "no features for this market"})
                continue
            cand = Model(m, terms).fit(F, fi, "rev", qb_variant)
            mu, _ = cand.predict(F, ei, "rev", qb_variant)
            met = metrics(F, ei, m, mu, disp, "rev")
            d, se = clustered(met["_ll"] - cur["_ll"], games)
            ok = accept(d, se, m)
            rungs.append({"rung": g, "group": M.GROUP_LABEL[g], "terms": [term_name(t) for t in terms],
                          "lambda": cand.lam, **{k: v for k, v in met.items() if not k.startswith("_")},
                          "d_ll": d, "se": se, "z": d / se if se > 0 else None,
                          "d_mae": met["mae"] - cur["mae"], "accepted": ok})
            if log_each:
                log(f"  {m:8s} {g:3s} n={met['n']:5d} ll={met['ll']:.5f} d={d:+.5f} se={se:.5f} "
                    f"{'ACCEPT' if ok else 'reject'} lam={cand.lam}")
            if ok:
                if g == "D7":
                    accepted = accepted + ["D7"]
                else:
                    accepted.append(g)
                cur = met
        out[m] = {"rungs": rungs, "accepted": accepted}
    return out


MIN_GAIN = 0.0005     # nats per threshold: the practical floor (pre-registered)


def accept(d, se, m):
    """The pre-registered rule: better by more than two clustered SE AND by
    at least MIN_GAIN nats per threshold of the market."""
    return bool(d < -2.0 * se and d < -MIN_GAIN * len(M.THRESHOLDS[m]))


def code_hashes():
    return {f: sha_file(os.path.join(REPO, f)) for f in ("nfl_matchup.py", "research/nfl_matchup_frame.py",
                                                          "research/nfl_matchup_fit.py")}


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, list):
        return [strip(v) for v in o]
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


# ---- pre-registration -----------------------------------------------------------------
def prereg_block():
    with open(REPORT, encoding="utf-8") as fh:
        txt = fh.read()
    a, b = txt.find(PRE_A), txt.find(PRE_B)
    if a < 0 or b < 0 or b <= a:
        raise SystemExit("the report has no PREREG block")
    return txt[a:b + len(PRE_B)]


def prereg_hash():
    return hashlib.sha256(prereg_block().encode("utf-8")).hexdigest()


def check_prereg():
    p = os.path.join(OUT, "prereg.json")
    if not os.path.exists(p):
        raise SystemExit("no prereg.json: run `prereg` after writing the rule")
    with open(p) as fh:
        rec = json.load(fh)
    h = prereg_hash()
    if h != rec["sha256"]:
        raise SystemExit(f"the PREREG block changed since it was registered ({h} != {rec['sha256']})")
    return rec


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(name, obj):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name)
    with open(p + ".tmp", "w") as fh:
        json.dump(strip(obj), fh, indent=1, sort_keys=False)
    os.replace(p + ".tmp", p)
    return p


# ---- commands ---------------------------------------------------------------------------
def cmd_traincheck(a):
    """2023-2024 ONLY: fit 2023 -> score 2024 (no 2025/2026 row is loaded)."""
    F = Frame(a.frame, set(FIT_SEASONS))
    idx = {m: fit_idx(F, m, (2023,)) for m in SPEC}
    disp = fit_dispersion(F, idx)
    log(json.dumps(strip(disp))[:2000])
    res = run_ladder(F, (2023,), 2024, disp)
    models = final_models(F, res, (2023,))
    cpos = fit_dk_scale(F, dk_eval_idx(F, 2023, "rev", disp), disp)
    dk = dk_ladder(F, 2024, "rev", models, disp, cpos)
    dk.pop("_res")
    td = td_eval(F, 2024, "rev", models, disp)
    cal = calibration(F, 2024, "rev", models, disp)
    lowr = low_reliability(F, res, (2023,), 2024, disp)
    agree = agreement(F, models, disp)
    log("agreement " + json.dumps(agree))
    log("dk " + json.dumps(strip(dk))[:1500])
    log("td " + json.dumps(strip(td)))
    write_json("traincheck.json", {"note": "fit 2023, score 2024 -- inside the fitting seasons, for debugging only",
                                   "ladder": res, "dispersion": disp, "dk_scale": cpos, "dk": dk, "td": td,
                                   "calibration": cal, "low_reliability": lowr, "agreement": agree})


def cmd_prereg(a):
    h = prereg_hash()
    rec = {"sha256": h, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "file": os.path.relpath(REPORT, REPO),
           "block": "between " + PRE_A + " and " + PRE_B, "code_at_prereg": code_hashes()}
    write_json("prereg.json", rec)
    log(json.dumps(rec))


def plumbing_scramble(F):
    """--plumbing: every outcome of a 2025 / 2026 row replaced by a random
    2023 row's outcome of the same position (seeded), so the validate / test
    code paths run end to end while carrying NO held-out information."""
    rng = np.random.default_rng(7)
    seas = F["season"].astype(int)
    for c in [c for c in F.x if c.startswith("y_")]:
        col = F.x[c]
        for p in M.POS:
            src = np.where((seas == 2023) & (F["pos"] == p))[0]
            dst = np.where((seas >= 2025) & (F["pos"] == p))[0]
            if len(src) and len(dst):
                col[dst] = col[rng.choice(src, size=len(dst))]


def _out_dir(a):
    global OUT
    if getattr(a, "plumbing", False):
        OUT = os.path.join(os.environ.get("NFLM_WORK", "/tmp"), "plumbing_out")
        os.makedirs(OUT, exist_ok=True)


def cmd_validate(a):
    _out_dir(a)
    rec = check_prereg() if not a.plumbing else {"plumbing": True}
    F = Frame(a.frame, set(FIT_SEASONS) | {VAL_SEASON})
    if a.plumbing:
        plumbing_scramble(F)
    idx = {m: fit_idx(F, m, FIT_SEASONS) for m in SPEC}
    # R2 as amended 2026-10-23 (report section 2a): the yards family is chosen on the
    # validation season's priced rows (the baseline's threshold log loss), not on the
    # fitting rows; the 2026-10-23 run predates this and scored gamma for both markets
    val_rows = {m: np.where(eval_mask(F, m) & (F["season"].astype(int) == VAL_SEASON))[0] for m in ("rec_yd", "rush_yd")}
    disp = fit_dispersion(F, idx, family_rows=val_rows)
    log("[validate] yards family: " + json.dumps({m: disp[m]["family"] for m in val_rows}))
    log("[validate] ladder, fit 2023-2024, score 2025")
    res = run_ladder(F, FIT_SEASONS, VAL_SEASON, disp)
    sens = {}
    for m in M.PASS + ("rec", "rec_yd"):
        sens[m] = qb_sensitivity(F, m, res[m], disp)
    models = final_models(F, res, FIT_SEASONS)
    cpos = fit_dk_scale(F, np.concatenate([dk_eval_idx(F, s_, "rev", disp) for s_ in FIT_SEASONS]), disp)
    dk = dk_ladder(F, VAL_SEASON, "rev", models, disp, cpos)
    dk.pop("_res")
    dk["by_rung"] = dk_by_rung(F, res, FIT_SEASONS, VAL_SEASON, "rev", disp, cpos)
    td = td_eval(F, VAL_SEASON, "rev", models, disp)
    cal = calibration(F, VAL_SEASON, "rev", models, disp)
    lowr = low_reliability(F, res, FIT_SEASONS, VAL_SEASON, disp)
    extra = validation_extras(F, res, disp)
    dfs_rule = {"hook_ships": bool(dk["SUxM_team_vs_SU"]["d_ll"] <= 0.0),
                "free_beats_team": bool(dk["SUxM_free_vs_SUxM_team"]["d_ll"] < -2.0 * dk["SUxM_free_vs_SUxM_team"]["se"])}
    sens_k = None      # the k sensitivity (R6)
    if getattr(a, "frame_sens", None):
        # O2 sensitivity (reported, deciding nothing): the same ladder on the
        # frame built with the 2018-2025 k (production's), its 2023-2025 rows
        Fs = Frame(a.frame_sens, set(FIT_SEASONS) | {VAL_SEASON})
        if a.plumbing:
            plumbing_scramble(Fs)
        ids_ = {m: fit_idx(Fs, m, FIT_SEASONS) for m in SPEC}
        rs = run_ladder(Fs, FIT_SEASONS, VAL_SEASON, fit_dispersion(Fs, ids_, family={m: disp[m]["family"] for m in val_rows}),
                        log_each=False)
        sens_k = {"frame_sha256": sha_file(a.frame_sens), "decisions": {m: rs[m]["accepted"] for m in rs},
                  "rungs": {m: [{k: r.get(k) for k in ("rung", "d_ll", "se", "accepted")} for r in rs[m]["rungs"]] for m in rs}}
        log("[validate] 2018-2025-k sensitivity: " + json.dumps(sens_k["decisions"]))
    write_json("validate.json", {"prereg": rec, "frame_sha256": sha_file(a.frame), "code": code_hashes(),
                                 "k_pooled_sensitivity": sens_k,
                                 "ladder": res, "dispersion": disp, "dk_scale": cpos, "dk_fp": dk, "td": td,
                                 "calibration": cal, "low_reliability": lowr, "qb_sensitivity": sens, "extras": extra,
                                 "dfs_rule_2025": dfs_rule, "agreement": agreement(F, models, disp),
                                 "decisions": {m: res[m]["accepted"] for m in res},
                                 "models": {m: mod.to_json() for m, mod in models.items()}})
    log("[validate] accepted: " + json.dumps({m: res[m]["accepted"] for m in res}))
    log("[validate] dk_fp: " + json.dumps(strip({k: v for k, v in dk.items() if "_vs_" in k})))


def qb_sensitivity(F, m, lad, disp):
    """O1: the D5 rung refit and rescored with the passing features built on
    the REVISED Sleeper QB1 instead of the last starter (the frame's fs_*)."""
    acc_before = []
    for r in lad["rungs"]:
        if r["rung"] == "D5":
            break
        if r.get("accepted"):
            acc_before.append(r["rung"])
    fi = fit_idx(F, m, FIT_SEASONS)
    ei = np.where(eval_mask(F, m) & (F["season"].astype(int) == VAL_SEASON))[0]
    base = Model(m, terms_for(m, acc_before)).fit(F, fi) if acc_before else Model(m, [])
    mb, _ = base.predict(F, ei)
    mb_met = metrics(F, ei, m, mb, disp, "rev")
    out = {}
    for var in ("used", "sleeper_rev"):
        cand = Model(m, terms_for(m, acc_before + ["D5"])).fit(F, fi, "rev", var)
        mu, _ = cand.predict(F, ei, "rev", var)
        met = metrics(F, ei, m, mu, disp, "rev")
        d, se = clustered(met["_ll"] - mb_met["_ll"], F["game_id"][ei])
        out[var] = {"d_ll": d, "se": se, "accepted_by_rule": accept(d, se, m)}
    return out


def validation_extras(F, res, disp):
    """Per position and by history depth for every market's accepted model
    (descriptive; no decision reads them)."""
    out = {}
    for m, r in res.items():
        acc = r["accepted"]
        if not acc:
            continue
        fi = fit_idx(F, m, FIT_SEASONS)
        ei = np.where(eval_mask(F, m) & (F["season"].astype(int) == VAL_SEASON))[0]
        mod = Model(m, terms_for(m, acc)).fit(F, fi)
        mu, _ = mod.predict(F, ei)
        base, _ = Model(m, []).predict(F, ei)
        dl = market_ll(F, ei, m, mu, disp) - market_ll(F, ei, m, base, disp)
        pos = F["pos"][ei]
        o = {"by_pos": {}, "by_history": {}}
        for p in np.unique(pos):
            sl = pos == p
            d, se = clustered(dl[sl], F["game_id"][ei][sl])
            o["by_pos"][p] = {"d_ll": d, "se": se, "n": int(sl.sum())}
        g = np.nan_to_num(F["p_games"][ei], nan=0.0)
        for lab, sl in (("under 4 weighted games", g < 4), ("4-10", (g >= 4) & (g < 10)), ("10+", g >= 10)):
            if sl.sum():
                d, se = clustered(dl[sl], F["game_id"][ei][sl])
                o["by_history"][lab] = {"d_ll": d, "se": se, "n": int(sl.sum())}
        out[m] = o
    return out


def final_models(F, res, seasons, qb_variant="used"):
    models = {}
    for m in SPEC:
        acc = res[m]["accepted"]
        fi = fit_idx(F, m, seasons)
        models[m] = Model(m, terms_for(m, acc)).fit(F, fi, "rev", qb_variant) if acc else Model(m, [])
    return models


def _test_idx(F, m, kind):
    ei = np.where(eval_mask(F, m, kind) & (F["season"].astype(int) == TEST_SEASON))[0]
    if kind == "fz":
        ei = ei[[bool(x) for x in F["fz_src"][ei]]]
    return ei


def cmd_test(a):
    _out_dir(a)
    rec = check_prereg() if not a.plumbing else {"plumbing": True}
    lock = os.path.join(OUT, "test_lock.json")
    if os.path.exists(lock) and not a.force and not a.plumbing:
        raise SystemExit("the 2026 test has already run once (test_lock.json); --force re-runs it and the report must say so")
    with open(os.path.join(OUT, "validate.json")) as fh:
        val = json.load(fh)
    F = Frame(a.frame, set(FIT_SEASONS) | {VAL_SEASON, TEST_SEASON})
    if a.plumbing:
        plumbing_scramble(F)
    fit3 = FIT_SEASONS + (VAL_SEASON,)
    idx = {m: fit_idx(F, m, fit3) for m in SPEC}
    # the yards family is the validation's (R2 as amended 2026-10-23); a validate.json
    # without one predates the amendment and its run scored gamma
    fam = {m: (val["dispersion"].get(m) or {}).get("family") for m in ("rec_yd", "rush_yd")}
    if not all(fam.values()):
        raise SystemExit("validate.json names no yards family (it predates the 2026-10-23 amendment): re-run validate")
    disp = fit_dispersion(F, idx, family=fam)
    cpos = fit_dk_scale(F, np.concatenate([dk_eval_idx(F, s_, "rev", disp) for s_ in fit3]), disp)
    out = {"prereg": rec, "frame_sha256": sha_file(a.frame), "code": code_hashes(), "markets": {},
           "dispersion": disp, "dk_scale": cpos}
    shipped = {}
    for m in SPEC:
        accepted = val["decisions"][m]
        mk = {}
        for kind in ("fz", "rev"):
            ei = _test_idx(F, m, kind)
            games = F["game_id"][ei]
            b0, _ = Model(m, []).predict(F, ei, kind)
            base = metrics(F, ei, m, b0, disp, kind)
            rows = [{"rung": "1", **{k: v for k, v in base.items() if not k.startswith("_")}}]
            acc = []
            for r in val["ladder"][m]["rungs"]:
                g = r["rung"]
                if g == "1" or r.get("skipped"):
                    continue
                mod = Model(m, terms_for(m, acc + [g])).fit(F, idx[m])
                mu, _ = mod.predict(F, ei, kind)
                met = metrics(F, ei, m, mu, disp, kind)
                d, se = clustered(met["_ll"] - base["_ll"], games)
                rows.append({"rung": g, "accepted_on_2025": r.get("accepted"),
                             **{k: v for k, v in met.items() if not k.startswith("_")},
                             "d_ll_vs_sleeper": d, "se": se})
                if r.get("accepted"):
                    acc.append(g)
            mk[kind] = {"ladder": rows}
        # the pre-registered shipping walk on the FROZEN rows
        ei = _test_idx(F, m, "fz")
        games = F["game_id"][ei]
        ship = []
        cur = metrics(F, ei, m, Model(m, []).predict(F, ei, "fz")[0], disp, "fz")
        walk = []
        for g in accepted:
            cand = Model(m, terms_for(m, ship + [g])).fit(F, idx[m])
            met = metrics(F, ei, m, cand.predict(F, ei, "fz")[0], disp, "fz")
            d, se = clustered(met["_ll"] - cur["_ll"], games)
            keep = bool(d < 0.0)
            walk.append({"group": g, "n": int(len(ei)), "d_ll": d, "se": se, "ships": keep})
            if keep:
                ship.append(g)
                cur = met
        mk["shipping_walk_frozen"] = walk
        mk["ships"] = ship
        shipped[m] = ship
        out["markets"][m] = mk
    models = final_models(F, {m: {"accepted": shipped[m]} for m in SPEC}, fit3)
    for m in SPEC:
        for kind in ("fz", "rev"):
            ei = _test_idx(F, m, kind)
            b0, _ = Model(m, []).predict(F, ei, kind)
            base = metrics(F, ei, m, b0, disp, kind)
            mu, _ = models[m].predict(F, ei, kind)
            fin = metrics(F, ei, m, mu, disp, kind)
            d, se = clustered(fin["_ll"] - base["_ll"], F["game_id"][ei])
            out["markets"][m][kind]["shipped_model"] = {**{k: v for k, v in fin.items() if not k.startswith("_")},
                                                        "d_ll_vs_sleeper": d, "se": se,
                                                        "sleeper": {k: v for k, v in base.items() if not k.startswith("_")}}
    out["dk_fp"], out["td"], out["calibration"] = {}, {}, {}
    for kind in ("fz", "rev"):
        dk = dk_ladder(F, TEST_SEASON, kind, models, disp, cpos)
        dk.pop("_res")
        out["dk_fp"][kind] = dk
        out["td"][kind] = td_eval(F, TEST_SEASON, kind, models, disp)
        out["calibration"][kind] = calibration(F, TEST_SEASON, kind, models, disp)
    vr = val["dfs_rule_2025"]
    fz = out["dk_fp"]["fz"]
    out["dfs_hook"] = {"ships": bool(vr["hook_ships"] and fz["SUxM_team_vs_SU"]["d_ll"] <= 0.0),
                       "team_totals_free": bool(vr["free_beats_team"] and fz["SUxM_free_vs_SUxM_team"]["d_ll"] < 0.0)}
    out["revision_effect"] = revision_effect(F, disp)
    out["frozen_coverage"] = frozen_coverage(F)
    out["shipped"] = shipped
    out["models"] = {m: mod.to_json() for m, mod in models.items()}
    out["agreement"] = agreement(F, models, disp)
    write_json("test.json", out)
    write_json("coefs.json", export_payload(models, shipped, disp, cpos, out["dfs_hook"], val, rec))
    if not a.plumbing:
        write_json("test_lock.json", {"ran_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                      "forced": bool(a.force)})
    log("[test] shipped: " + json.dumps(shipped))
    log("[test] frozen d_ll: " + json.dumps({m: out["markets"][m]["fz"]["shipped_model"]["d_ll_vs_sleeper"] for m in SPEC}))
    log("[test] dfs hook: " + json.dumps(out["dfs_hook"]))


def export_payload(models, shipped, disp, cpos, hook, val, rec):
    """What production reads (nfl_matchup.load_coefs): the shipped models refit
    on 2023-2025, the dispersion, the DK scale, the decisions and their
    provenance. A market whose groups all failed carries no terms (weight 0)."""
    return {"schema": M.SCHEMA, "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fit_seasons": list(FIT_SEASONS) + [VAL_SEASON], "prereg_sha256": rec.get("sha256"),
            "validation_accepted": val["decisions"], "shipped": shipped, "dfs_hook": hook,
            "models": {m: mod.to_json() for m, mod in models.items()},
            "dispersion": disp, "dk_scale": cpos, "fp_shift": FP_SHIFT,
            "source": "research/nfl_matchup_fit.py test (research/reports/nfl_matchup_model.md)"}


def frozen_coverage(F):
    """Which 2026 rows have a frozen baseline: by week, the source and how
    many hours before kickoff the capture's content was stamped."""
    out = {}
    seas = F["season"].astype(int)
    for w in range(1, 7):
        sel = np.where((seas == TEST_SEASON) & (F["week"].astype(int) == w))[0]
        fz = [i for i in sel if F["fz_src"][i]]
        lead = np.array([F["fz_lead_h"][i] for i in fz], float)
        srcs = {}
        for i in fz:
            srcs[F["fz_src"][i]] = srcs.get(F["fz_src"][i], 0) + 1
        out[w] = {"rows": int(len(sel)), "frozen": len(fz), "sources": srcs,
                  "lead_h_median": float(np.median(lead)) if len(lead) else None,
                  "lead_h_max": float(np.max(lead)) if len(lead) else None}
    return out


def revision_effect(F, disp):
    """Rule C(3): how much the post-week revision alone moves the baseline's
    accuracy, on the 2026 rows that have BOTH a frozen and a revised line."""
    out = {}
    for m in SPEC:
        e_fz = eval_mask(F, m, "fz") & (F["season"].astype(int) == TEST_SEASON)
        e_rv = eval_mask(F, m, "rev") & (F["season"].astype(int) == TEST_SEASON)
        ei = np.where(e_fz & e_rv & np.array([bool(s) for s in F["fz_src"]]))[0]
        if len(ei) < 20:
            continue
        fz, _ = Model(m, []).predict(F, ei, "fz")
        rv, _ = Model(m, []).predict(F, ei, "rev")
        a = metrics(F, ei, m, fz, disp, "fz")
        b = metrics(F, ei, m, rv, disp, "rev")
        d, se = clustered(b["_ll"] - a["_ll"], F["game_id"][ei])
        out[m] = {"n": int(len(ei)), "frozen": {k: a[k] for k in ("mae", "rmse", "ll")},
                  "revised": {k: b[k] for k in ("mae", "rmse", "ll")},
                  "d_ll_revised_minus_frozen": d, "se": se,
                  "share_changed": float(np.mean(np.abs(fz - rv) > 1e-9)),
                  "mean_abs_change": float(np.mean(np.abs(fz - rv)))}
    return out


# ---- derived markets: anytime TD and DraftKings points ------------------------------
COMP_OF = {"rec": "rec", "rec_yd": "rec_yd", "rec_td": "tgt", "rec_tgt": "tgt", "rush_att": "car", "rush_yd": "rush_yd",
           "rush_td": "car", "pass_att": "patt", "pass_cmp": "pcmp", "pass_yd": "pass_yd", "pass_td": "ptd",
           "pass_int": "int"}
USAGE_FIELDS = ("rec_tgt", "rec", "rec_yd", "rec_td", "rush_att", "rush_yd", "rush_td")
DK_FIELDS = ("rec", "rec_yd", "rec_td", "rush_yd", "rush_td", "pass_yd", "pass_td", "pass_int", "fum_lost")
DK_W = {"rec": M.DK["rec"], "rec_yd": M.DK["rec_yd"], "rec_td": M.DK["rec_td"], "rush_yd": M.DK["rush_yd"],
        "rush_td": M.DK["rush_td"], "pass_yd": M.DK["pass_yd"], "pass_td": M.DK["pass_td"], "pass_int": M.DK["int"],
        "fum_lost": M.DK["fum_lost"]}
FP_SHIFT = 3.0


def market_factors(F, idx, models, kind):
    """{market: mean / base} per row (1 where the row is outside the market's universe)."""
    out = {}
    for m, mod in models.items():
        f = np.ones(len(idx))
        ok = fit_mask(F, m, kind)[idx]
        if mod.terms and ok.any():
            sub = idx[ok]
            mu, _ = mod.predict(F, sub, kind)
            f[ok] = mu / base_line(F, SPEC[m][1], kind)[sub]
        out[m] = f
    return out


def comp_lines(F, idx, kind, usage=False, models=None, preserve=False):
    """{field: array} of a variant's component means for rows idx: Sleeper
    (`kind`), optionally with nfl_usage's moved lines, optionally x the
    matchup factors, optionally renormalised to Sleeper's team totals."""
    comps = {}
    for fld in COMP_OF.keys() | {"fum_lost"}:
        b = np.nan_to_num(base_line(F, fld, kind)[idx], nan=0.0)
        if usage and fld in USAGE_FIELDS:
            u = F[("su_" if kind == "rev" else "fzsu_") + fld][idx]
            b = np.where(np.isnan(u), b, u)
        comps[fld] = b
    if models:
        fac = market_factors(F, idx, models, kind)
        base = {k: v.copy() for k, v in comps.items()}
        for fld, m in COMP_OF.items():
            comps[fld] = comps[fld] * fac[m]
        if preserve:
            key = np.array([f"{int(a)}|{int(b)}|{c}" for a, b, c in zip(F["season"][idx], F["week"][idx], F["team"][idx])])
            uk, inv = np.unique(key, return_inverse=True)
            for fld in COMP_OF:
                tb = np.bincount(inv, weights=base[fld])
                ta = np.bincount(inv, weights=comps[fld])
                ratio = np.where(ta > 0, tb / np.maximum(ta, 1e-12), 1.0)
                comps[fld] = comps[fld] * ratio[inv]
    return comps


def _yards_var(p0, mean, cv):
    m_pos = np.maximum(mean, 1e-6) / (1 - np.clip(p0, 0, 0.999))
    second = (1 - p0) * m_pos * m_pos * (1 + cv * cv)
    return np.maximum(second - mean * mean, 0.0)


def dk_moments(F, idx, comps, disp):
    """DK points mean (bonuses at their probabilities) and the component
    variance sum V (independent components; the fitted c_pos carries the
    covariance)."""
    pos = F["pos"][idx]
    mean = sum(DK_W[k] * comps[k] for k in DK_FIELDS)
    V = np.zeros(len(idx))
    for p in np.unique(pos):
        s = pos == p
        def nbv(m, mu):
            a = disp.get(m, {}).get(p, {}).get("alpha")
            return mu + (a or 0.0) * mu * mu
        V[s] += nbv("rec", comps["rec"][s])
        V[s] += 36.0 * comps["rec_td"][s] + 36.0 * comps["rush_td"][s] + 16.0 * comps["pass_td"][s] + comps["pass_int"][s]
        for ym, cm, cf, w, thr in (("rec_yd", "rec", "rec", 0.01, 100), ("rush_yd", "car", "rush_att", 0.01, 100)):
            prm, cp = disp.get(ym, {}).get(p), disp.get(cm, {}).get(p)
            mu = comps[ym][s]
            if prm is None or cp is None or not (mu > 0).any():
                continue
            p0 = nb_p0(np.maximum(comps[cf][s], 1e-6), cp["alpha"])
            V[s] += w * _yards_var(p0, mu, prm["cv"])
            pb = yards_sf(p0, mu, prm["cv"], thr, disp[ym].get("family", "gamma"))
            mean[s] += 3.0 * np.where(mu > 0, pb, 0.0)
        prm = disp.get("pass_yd", {}).get(p)
        if prm is not None and (comps["pass_yd"][s] > 0).any():
            mu = comps["pass_yd"][s]
            sd = prm["sd_c"] * np.maximum(mu, 1.0) ** prm["sd_d"]
            V[s] += 0.0016 * np.where(mu > 0, sd * sd, 0.0)
            mean[s] += 3.0 * np.where(mu > 0, normal_sf(mu, sd, 300), 0.0)
    return mean, V


def dk_actual(F, idx):
    y = {k: y_of(F, k)[idx] for k in DK_FIELDS + ("two_pt", "ret_td")}
    pts = sum(DK_W[k] * y[k] for k in DK_FIELDS) + M.DK["two_pt"] * y["two_pt"] + 6.0 * y["ret_td"]
    pts = pts + 3.0 * (y["rec_yd"] >= 100) + 3.0 * (y["rush_yd"] >= 100) + 3.0 * (y["pass_yd"] >= 300)
    return pts


def dk_eval_idx(F, season, kind, disp):
    base = comp_lines(F, np.arange(F.n), kind)
    mean, _ = dk_moments(F, np.arange(F.n), base, disp)
    ok = (F["season"].astype(int) == season) & np.isin(F["pos"], M.POS) & (mean >= 4.0)
    if kind == "fz":
        ok &= np.array([bool(x) for x in F["fz_src"]])
        # the props log keeps no interception line: a QB without one is not scored (R4)
        ok &= ~((F["pos"] == "QB") & np.isnan(F["fz_pass_int"]))
    return np.where(ok)[0]


def fit_dk_scale(F, idx, disp, kind="rev"):
    comps = comp_lines(F, idx, kind)
    mean, V = dk_moments(F, idx, comps, disp)
    y = dk_actual(F, idx)
    pos = F["pos"][idx]
    return {p: float(np.sum((y[pos == p] - mean[pos == p]) ** 2) / np.sum(V[pos == p])) for p in np.unique(pos)}


def dk_metrics(F, idx, comps, disp, cpos):
    mean, V = dk_moments(F, idx, comps, disp)
    pos = F["pos"][idx]
    var = V * np.array([cpos.get(p, 1.0) for p in pos])
    y = dk_actual(F, idx)
    probs = {k: np.clip(fp_sf(mean, var, k, FP_SHIFT), EPS, 1 - EPS) for k in M.THRESHOLDS["dk_fp"]}
    ll = ll_from_probs(probs, y)
    return {"n": int(len(idx)), "mae": float(np.mean(np.abs(y - mean))), "rmse": float(np.sqrt(np.mean((y - mean) ** 2))),
            "ll": float(np.mean(ll)), "brier": float(np.mean(brier_from_probs(probs, y))), "_ll": ll, "_probs": probs,
            "_y": y, "_mean": mean}


def dk_ladder(F, season, kind, models, disp, cpos):
    """S, SU, S x M, SU x M (free), SU x M (team totals preserved)."""
    idx = dk_eval_idx(F, season, kind, disp)
    allx = np.where((F["season"].astype(int) == season))[0]
    if kind == "fz":
        allx = allx[[bool(x) for x in F["fz_src"][allx]]]
    pos_in = {v: i for i, v in enumerate(allx)}
    sub = np.array([pos_in[i] for i in idx], dtype=int)
    variants = {"S": dict(usage=False, models=None), "SU": dict(usage=True, models=None),
                "SxM": dict(usage=False, models=models), "SUxM_free": dict(usage=True, models=models),
                "SUxM_team": dict(usage=True, models=models, preserve=True)}
    res = {}
    for name, kw in variants.items():
        comps_all = comp_lines(F, allx, kind, **kw)
        comps = {k: v[sub] for k, v in comps_all.items()}
        res[name] = dk_metrics(F, idx, comps, disp, cpos)
    games = F["game_id"][idx]
    out = {}
    for name, met in res.items():
        out[name] = {k: v for k, v in met.items() if not k.startswith("_")}
    for a, b in (("SU", "S"), ("SxM", "S"), ("SUxM_free", "SU"), ("SUxM_team", "SU"), ("SUxM_free", "SUxM_team")):
        d, se = clustered(res[a]["_ll"] - res[b]["_ll"], games)
        dm, sem = clustered(np.abs(res[a]["_y"] - res[a]["_mean"]) - np.abs(res[b]["_y"] - res[b]["_mean"]), games)
        out[f"{a}_vs_{b}"] = {"d_ll": d, "se": se, "d_mae": dm, "se_mae": sem}
    pos = F["pos"][idx]
    out["by_pos"] = {}
    for p in np.unique(pos):
        s = pos == p
        d, se = clustered(res["SUxM_team"]["_ll"][s] - res["SU"]["_ll"][s], games[s])
        out["by_pos"][p] = {"n": int(s.sum()), "SU_mae": float(np.mean(np.abs(res["SU"]["_y"][s] - res["SU"]["_mean"][s]))),
                            "SUxM_team_mae": float(np.mean(np.abs(res["SUxM_team"]["_y"][s] - res["SUxM_team"]["_mean"][s]))),
                            "d_ll": d, "se": se}
    out["_res"] = res
    return out


def dk_by_rung(F, res, fit_seasons, season, kind, disp, cpos):
    """DK points for each rung ACROSS markets (descriptive): every market's
    model = the groups it accepted before g, plus g; scored as S x M and
    SU x M (team totals preserved) against S and SU."""
    out = {}
    for g in M.GROUPS:
        models = {}
        for m in SPEC:
            acc = []
            for r in res[m]["rungs"]:
                if r["rung"] == g:
                    break
                if r.get("accepted"):
                    acc.append(r["rung"])
            terms = terms_for(m, acc + [g])
            models[m] = Model(m, terms).fit(F, fit_idx(F, m, fit_seasons)) if terms else Model(m, [])
        dk = dk_ladder(F, season, kind, models, disp, cpos)
        dk.pop("_res")
        out[g] = {k: dk[k] for k in ("SxM_vs_S", "SUxM_team_vs_SU", "SUxM_free_vs_SU")}
    return out


def td_eval(F, season, kind, models, disp):
    """Anytime TD (rushing or receiving) at the volume factors."""
    ei = np.where(eval_mask(F, "td", kind) & (F["season"].astype(int) == season))[0]
    if kind == "fz":
        ei = ei[[bool(x) for x in F["fz_src"][ei]]]
    y = y_of(F, "rec_td")[ei] + y_of(F, "rush_td")[ei]
    rt = np.nan_to_num(base_line(F, "rec_td", kind)[ei], nan=0.0)
    ut = np.nan_to_num(base_line(F, "rush_td", kind)[ei], nan=0.0)
    out = {}
    fac = market_factors(F, ei, models, kind)
    res = {}
    for name, mu in (("S", rt + ut), ("SxM", rt * fac["tgt"] + ut * fac["car"])):
        probs = td_probs(np.maximum(mu, 1e-6))
        ll = ll_from_probs(probs, y)
        res[name] = ll
        out[name] = {"n": int(len(ei)), "ll": float(np.mean(ll)), "brier": float(np.mean(brier_from_probs(probs, y))),
                     "mae": float(np.mean(np.abs(y - mu)))}
    d, se = clustered(res["SxM"] - res["S"], F["game_id"][ei])
    out["SxM_vs_S"] = {"d_ll": d, "se": se}
    return out


CALIB = (("rec", 4), ("rec_yd", 50), ("rush_yd", 50), ("pass_yd", 250), ("tgt", 5))


def calibration(F, season, kind, models, disp):
    """Reliability by probability bin (prop_dist.reliability_table, 10 equal
    bins) for the baseline and the final model at a few headline rungs."""
    import prop_dist as PD
    out = {}
    for m, k in CALIB:
        ei = np.where(eval_mask(F, m, kind) & (F["season"].astype(int) == season))[0]
        if kind == "fz":
            ei = ei[[bool(x) for x in F["fz_src"][ei]]]
        if len(ei) < 30:
            continue
        y = y_of(F, SPEC[m][0])[ei]
        blk = {}
        for name, mod in (("sleeper", Model(m, [])), ("model", models[m])):
            mu, _ = mod.predict(F, ei, kind)
            p = market_probs(F, ei, m, mu, disp, kind)[k]
            blk[name] = {"table": PD.reliability_table([float(v) for v in p], [int(v) for v in (y >= k)], bins=10),
                         "ece": PD.ece([float(v) for v in p], [int(v) for v in (y >= k)], bins=10)}
        out[f"{m}>={k}"] = blk
    return out


LEAD = {"D2": {"rush": "rel_run_ypc", "other": "rel_pass_epa"}, "D3": {"recv": "rel_tgt_rate_pos"},
        "D5": {"recv": "rel_cov_mz", "pass": "rel_press_qb"}}


def low_reliability(F, res, fit_seasons, season, disp):
    """For each market and rung, the rung's gain split at the median
    reliability of its lead feature (descriptive)."""
    out = {}
    for m in SPEC:
        fam = "recv" if m in M.RECV else "rush" if m in M.RUSH else "pass"
        fi = fit_idx(F, m, fit_seasons)
        ei = np.where(eval_mask(F, m) & (F["season"].astype(int) == season))[0]
        acc = []
        cur_mod = Model(m, [])
        for r in res[m]["rungs"]:
            g = r["rung"]
            if g in ("1",) or r.get("skipped"):
                continue
            col = None
            if g in LEAD:
                col = LEAD[g].get(fam) or LEAD[g].get("other")
            elif g == "D6":
                col = "neff_" + m
            if col and F.has(col):
                cand = Model(m, terms_for(m, acc + [g])).fit(F, fi)
                mu, _ = cand.predict(F, ei)
                mb, _ = cur_mod.predict(F, ei)
                dl = market_ll(F, ei, m, mu, disp) - market_ll(F, ei, m, mb, disp)
                rel = np.nan_to_num(F[col][ei], nan=0.0)
                med = float(np.median(rel))
                o = {"lead": col, "median": med}
                for lab, sel in (("low", rel <= med), ("high", rel > med)):
                    if sel.sum() >= 20:
                        d, se = clustered(dl[sel], F["game_id"][ei][sel])
                        o[lab] = {"n": int(sel.sum()), "d_ll": d, "se": se}
                out.setdefault(m, {})[g] = o
            if r.get("accepted"):
                acc.append(g)
                cur_mod = Model(m, terms_for(m, acc)).fit(F, fi)
    return out


# ---- Kalshi (descriptive, R5) ---------------------------------------------------------
KSTAT = {"KXNFLRECYDS": "rec_yd", "KXNFLREC": "rec", "KXNFLRSHYDS": "rush_yd", "KXNFLPASSYDS": "pass_yd", "KXNFLTD": "td"}


def _event_probs(F, i, stat, k, models, disp, kind):
    """(model p, Sleeper p) that the row's stat reaches k."""
    idx = np.array([i])
    if stat == "td":
        rt = np.nan_to_num(base_line(F, "rec_td", kind)[idx], nan=0.0)
        ut = np.nan_to_num(base_line(F, "rush_td", kind)[idx], nan=0.0)
        if rt[0] + ut[0] <= 0:
            return None
        fac = market_factors(F, idx, models, kind)
        mu = rt * fac["tgt"] + ut * fac["car"]
        return float(nb_sf(np.maximum(mu, 1e-6), 0.0, k)[0]), float(nb_sf(rt + ut, 0.0, k)[0])
    if not (fit_mask(F, stat, kind)[i]):
        return None
    mu, _ = models[stat].predict(F, idx, kind)
    b, _ = Model(stat, []).predict(F, idx, kind)
    pm = market_probs(F, idx, stat, mu, disp, kind, ks=[k])[k][0]
    pb = market_probs(F, idx, stat, b, disp, kind, ks=[k])[k][0]
    if not (np.isfinite(pm) and np.isfinite(pb)):
        return None
    return float(pm), float(pb)


def _score(ev):
    """Log loss and Brier for model / Sleeper / market on the same events."""
    out = {"n": len(ev)}
    if not ev:
        return out
    y = np.array([e["y"] for e in ev], float)
    for nm in ("model", "sleeper", "market"):
        p = np.clip(np.array([e[nm] for e in ev], float), EPS, 1 - EPS)
        out[nm] = {"ll": float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p)))),
                   "brier": float(np.mean((p - y) ** 2)), "mean_p": float(np.mean(p))}
    out["hit_rate"] = float(np.mean(y))
    return out


def _roi(ev, key, rng, draws=2000):
    """One contract of the positive-EV side at the pre-kickoff ask, fee in."""
    import prop_value as PV
    bets = []
    for e in ev:
        p = e[key]
        yes = PV.ev_buy(p, e["ask"], contracts=1)
        no = PV.ev_buy(1.0 - p, 100.0 - e["bid"], contracts=1)
        best = None
        for side, r in (("yes", yes), ("no", no)):
            if r and r["ev_c"] > 0 and (best is None or r["ev_c"] > best[1]["ev_c"]):
                best = (side, r)
        if best is None:
            continue
        side, r = best
        win = e["y"] == 1 if side == "yes" else e["y"] == 0
        bets.append((e["game"], r["cost_c"], (100.0 if win else 0.0) - r["cost_c"]))
    if not bets:
        return {"bets": 0}
    cost = np.array([b[1] for b in bets])
    prof = np.array([b[2] for b in bets])
    games = np.array([b[0] for b in bets])
    ug, inv = np.unique(games, return_inverse=True)
    gc = np.bincount(inv, weights=cost)
    gp = np.bincount(inv, weights=prof)
    boots = []
    for _ in range(draws):
        pick = rng.integers(0, len(ug), len(ug))
        boots.append(gp[pick].sum() / gc[pick].sum())
    return {"bets": len(bets), "games": int(len(ug)), "staked_c": float(cost.sum()), "profit_c": float(prof.sum()),
            "roi": float(prof.sum() / cost.sum()), "roi_95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]}


def cmd_kalshi(a):
    """R5. Under --plumbing every outcome (frame and Kalshi settlement) is a
    coin flip, so the code path runs without reading a held-out result."""
    import glob
    _out_dir(a)
    coin = np.random.default_rng(11) if a.plumbing else None
    import kalshi_nfl
    sys.path.insert(0, HERE)
    import nfl_matchup_kalshi as KH
    with open(os.path.join(OUT, "validate.json")) as fh:
        val = json.load(fh)
    with open(os.path.join(OUT, "test.json")) as fh:
        tst = json.load(fh)
    # each season's rows from the frame its model was fit on: 2025 from the
    # validation frame (2018-2024 k), 2026 from the test frame
    frames = {2025: Frame(getattr(a, "frame2025", None) or a.frame, {2025}), 2026: Frame(a.frame, {2026})}
    mods = {2025: {m: Model.from_json(m, js) for m, js in val["models"].items()},
            2026: {m: Model.from_json(m, js) for m, js in tst["models"].items()}}
    disp = {2025: val["dispersion"], 2026: tst["dispersion"]}
    by_name = {}
    for season, Fx in frames.items():
        for i, nm in enumerate(kalshi_nfl._norm(n) for n in Fx["name"]):
            by_name.setdefault((season, nm), []).append(i)
    events = {2025: [], 2026: []}
    stats = {"files": 0, "priced": 0, "matched": 0, "outcome_agrees": 0, "wide_or_missing": 0, "no_frozen": 0}
    for p in sorted(glob.glob(os.path.join(a.raw, "kalshi_nfl_hist", "candles", "*.json"))):
        with open(p) as fh:
            rec = json.load(fh)
        stats["files"] += 1
        mk = rec["market"]
        pk = KH.prekick(mk, rec.get("candles"))
        if not pk or not (0 < pk[0] <= pk[1] < 100) or pk[1] - pk[0] > 20:
            stats["wide_or_missing"] += 1
            continue
        stats["priced"] += 1
        series = mk["ticker"].split("-")[0]
        stat = KSTAT.get(series)
        nm = kalshi_nfl._norm(KH.market_name(mk))
        season = 2025 if mk["kick_ts"] < 1785000000 else 2026          # 2026-07-25: between the two seasons
        F = frames[season]
        kick = F["kick_ts"]
        cand = [i for i in by_name.get((season, nm), []) if np.isfinite(kick[i]) and abs(kick[i] - mk["kick_ts"]) < 6 * 3600]
        if len(cand) != 1:
            continue
        i = cand[0]
        kind = "fz" if season == 2026 else "rev"
        if kind == "fz" and not F["fz_src"][i]:
            stats["no_frozen"] += 1
            continue
        k = int(math.ceil(float(mk["floor_strike"])))
        pr = _event_probs(F, i, stat, k, mods[season], disp[season], kind)
        if pr is None:
            continue
        stats["matched"] += 1
        yv = (y_of(F, "rec_td")[i] + y_of(F, "rush_td")[i]) if stat == "td" else y_of(F, SPEC[stat][0])[i]
        y = 1 if mk["result"] == "yes" else 0
        if coin is not None:
            y, yv = int(coin.integers(0, 2)), float(coin.integers(0, 2)) * k
        stats["outcome_agrees"] += int((yv >= k) == (y == 1))
        events[season].append({"stat": stat, "k": k, "y": y, "model": pr[0], "sleeper": pr[1],
                               "market": (pk[0] + pk[1]) / 200.0, "bid": pk[0], "ask": pk[1], "game": F["game_id"][i],
                               "pos": F["pos"][i]})
    rng = np.random.default_rng(20261023)
    out = {"rule": "R5 (descriptive)", "stats": stats, "by_season": {}}
    for season, ev in events.items():
        blk = {"all": _score(ev), "by_stat": {}}
        for st_ in sorted({e["stat"] for e in ev}):
            blk["by_stat"][st_] = _score([e for e in ev if e["stat"] == st_])
        # review F3 (2026-10-23): where the model loses to the market, by position and,
        # for rushing yards, by threshold (descriptive; no RNG, so the ROI draws are unchanged)
        blk["by_stat_pos"] = {f"{st_}/{p_}": _score([e for e in ev if e["stat"] == st_ and e["pos"] == p_])
                              for st_, p_ in sorted({(e["stat"], e["pos"]) for e in ev})}
        blk["rush_yd_by_k"] = {lab: _score([e for e in ev if e["stat"] == "rush_yd" and lo <= e["k"] < hi])
                               for lab, lo, hi in (("under 20", 0, 20), ("20-39", 20, 40), ("40-59", 40, 60),
                                                   ("60+", 60, 10 ** 6))}
        blk["roi_model"] = _roi(ev, "model", rng)
        blk["roi_sleeper"] = _roi(ev, "sleeper", rng)
        out["by_season"][season] = blk
    out["props_log_w06"] = props_log_compare(frames[2026], a.props, mods[2026], disp[2026], coin) if a.props else None
    out["frames"] = {str(s_): Fx.n for s_, Fx in frames.items()}
    write_json("kalshi.json", out)
    log(json.dumps(strip({s_: {k: v for k, v in b.items() if k != "by_stat"} for s_, b in out["by_season"].items()}))[:3000])


def props_log_compare(F, path, models, disp, coin=None):
    """The app's own frozen de-vigged mids (sim-history props log, 2026 week
    6) against the shipped model on the frozen baseline."""
    import kalshi_nfl
    with open(path) as fh:
        lg = json.load(fh)
    wk = int(lg["week"])
    norm = np.array([kalshi_nfl._norm(n) for n in F["name"]])
    sel = np.where((F["season"].astype(int) == 2026) & (F["week"].astype(int) == wk))[0]
    ev = []
    for gk, g in (lg.get("games") or {}).items():
        for nm, pl in (g.get("players") or {}).items():
            cand = [i for i in sel if norm[i] == nm and abs(F["kick_ts"][i] - g["kickoff"]) < 6 * 3600]
            if len(cand) != 1 or not F["fz_src"][cand[0]]:
                continue
            i = cand[0]
            for stat, blk in (pl.get("stats") or {}).items():
                if stat not in ("rec", "rec_yd", "rush_yd", "pass_yd", "td"):
                    continue
                for line, pmid in blk.get("rungs") or []:
                    k = int(math.ceil(float(line)))
                    pr = _event_probs(F, i, stat, k, models, disp, "fz")
                    if pr is None:
                        continue
                    yv = (y_of(F, "rec_td")[i] + y_of(F, "rush_td")[i]) if stat == "td" else y_of(F, SPEC[stat][0])[i]
                    yy = int(coin.integers(0, 2)) if coin is not None else int(yv >= k)
                    ev.append({"stat": stat, "k": k, "y": yy, "model": pr[0], "sleeper": pr[1],
                               "market": float(pmid), "game": F["game_id"][i]})
    return {"all": _score(ev), "by_stat": {s_: _score([e for e in ev if e["stat"] == s_]) for s_ in sorted({e["stat"] for e in ev})}}


# ---- research == production ------------------------------------------------------------
def agreement(F, models, disp, n=400, seed=5):
    """The numpy twins against production on real rows: (1) every market's
    factor from research design() x b against nfl_matchup.market_adjust on the
    frame's own feature columns; (2) P(stat >= k) from the numpy families
    against prop_dist through nfl_matchup.market_dist; (3) DK points' gamma.
    Returns the max absolute differences (recorded in the outputs)."""
    rng = np.random.default_rng(seed)
    # through the JSON round trip production reads (review F1: handed the in-memory
    # dict, this check could not see that the family never reached coefs.json)
    coefs = json.loads(json.dumps(strip({"models": {m: mod.to_json() for m, mod in models.items()}, "dispersion": disp})))
    out = {"factor_max_abs_diff": 0.0, "prob_max_abs_diff": 0.0, "rows": 0}
    feat_cols = [c for c in F.cols if c.startswith("f_")]
    for m in SPEC:
        fi = np.where(fit_mask(F, m))[0]
        if not len(fi):
            continue
        take = rng.choice(fi, size=min(n, len(fi)), replace=False)
        mu, _ = models[m].predict(F, take)
        base = base_line(F, SPEC[m][1], "rev")[take]
        for j, i in enumerate(take):
            f = {c[2:]: float(np.nan_to_num(F[c][i], nan=0.0)) for c in feat_cols}
            f["_pb"] = {"tshare": None if not np.isfinite(F["p_tshare"][i]) else float(F["p_tshare"][i])}
            pos = F["pos"][i]
            fac = M.market_adjust(coefs, m, f, pos)["factor"]
            out["factor_max_abs_diff"] = max(out["factor_max_abs_diff"], abs(fac - mu[j] / base[j]))
            bl = {fld: float(np.nan_to_num(base_line(F, fld, "rev")[i], nan=0.0)) for fld in
                  ("rec_tgt", "rec", "rec_yd", "rec_td", "rush_att", "rush_yd", "rush_td", "pass_att", "pass_cmp", "pass_yd",
                   "pass_td", "pass_int", "fum_lost")}
            d = M.market_dist(coefs, m, float(mu[j]), pos, bl)
            if d is None:
                continue
            probs = market_probs(F, np.array([i]), m, np.array([mu[j]]), disp, "rev")
            for k, p in probs.items():
                if np.isfinite(p[0]) and EPS < p[0] < 1 - EPS:
                    out["prob_max_abs_diff"] = max(out["prob_max_abs_diff"], abs(d.sf_ge(k) - float(p[0])))
            out["rows"] += 1
    gs = []
    for mean, var in ((4.0, 9.0), (12.0, 40.0), (25.0, 120.0)):
        g = M.ShiftedGamma(mean, var, FP_SHIFT)
        for k in M.THRESHOLDS["dk_fp"]:
            gs.append(abs(g.sf_ge(k) - float(fp_sf(np.array([mean]), np.array([var]), k, FP_SHIFT)[0])))
    out["dk_gamma_max_abs_diff"] = max(gs)
    return out


# ---- the report's tables, rendered from the outputs (never typed by hand) ---------------
def cmd_tables(a):
    """Markdown tables for research/reports/nfl_matchup_model.md from
    validate.json / test.json / kalshi.json (whichever exist)."""
    _out_dir(a)
    lines = []
    vp = os.path.join(OUT, "validate.json")
    if os.path.exists(vp):
        with open(vp) as fh:
            val = json.load(fh)
        lines.append("### Validation ladder (fit 2023-2024, scored on 2025; d = candidate minus accepted, "
                     "threshold log loss per row, SE clustered by game)\n")
        lines.append("| market | rung | n | MAE | RMSE | log loss | Brier | d log loss | SE | z | lambda | decision |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for m, r in val["ladder"].items():
            for g in r["rungs"]:
                if g.get("skipped"):
                    lines.append(f"| {m} | {g['rung']} | | | | | | | | | | skipped (no feature) |")
                    continue
                dec = "baseline" if g["rung"] == "1" else ("ACCEPT" if g.get("accepted") else "weight 0")
                z = g.get("z")
                lines.append(f"| {m} | {g['rung']} | {g['n']} | {g['mae']:.3f} | {g['rmse']:.3f} | {g['ll']:.5f} | {g['brier']:.5f} | "
                             f"{'' if g.get('d_ll') is None else format(g['d_ll'], '+.5f')} | "
                             f"{'' if g.get('se') is None else format(g['se'], '.5f')} | "
                             f"{'' if z is None else format(z, '+.2f')} | {g.get('lambda') or ''} | {dec} |")
        lines.append("")
        dk = val.get("dk_fp") or {}
        if dk:
            lines.append("### DraftKings points, 2025 (validation models)\n")
            lines.append("| variant | n | MAE | RMSE | log loss | Brier |")
            lines.append("|---|---|---|---|---|---|")
            for v in ("S", "SU", "SxM", "SUxM_free", "SUxM_team"):
                x = dk.get(v) or {}
                if x:
                    lines.append(f"| {v} | {x['n']} | {x['mae']:.3f} | {x['rmse']:.3f} | {x['ll']:.5f} | {x['brier']:.5f} |")
            for k in ("SU_vs_S", "SxM_vs_S", "SUxM_free_vs_SU", "SUxM_team_vs_SU", "SUxM_free_vs_SUxM_team"):
                x = dk.get(k) or {}
                if x:
                    lines.append(f"\n- {k}: d log loss {x['d_ll']:+.5f} (SE {x['se']:.5f}); d MAE {x['d_mae']:+.4f} (SE {x['se_mae']:.4f})")
            lines.append("")
    tp = os.path.join(OUT, "test.json")
    if os.path.exists(tp):
        with open(tp) as fh:
            tst = json.load(fh)
        lines.append("### 2026 weeks 1-6 test (models refit 2023-2025; d vs Sleeper on the same rows)\n")
        lines.append("| market | baseline | rung | n | MAE | RMSE | log loss | Brier | d log loss vs Sleeper | SE | accepted on 2025 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for m, mk in tst["markets"].items():
            for kind in ("fz", "rev"):
                for g in mk[kind]["ladder"]:
                    lines.append(f"| {m} | {'frozen' if kind == 'fz' else 'revised'} | {g['rung']} | {g['n']} | {g['mae']:.3f} | "
                                 f"{g['rmse']:.3f} | {g['ll']:.5f} | {g['brier']:.5f} | "
                                 f"{'' if g.get('d_ll_vs_sleeper') is None else format(g['d_ll_vs_sleeper'], '+.5f')} | "
                                 f"{'' if g.get('se') is None else format(g['se'], '.5f')} | {g.get('accepted_on_2025', '')} |")
        lines.append("")
        lines.append("Shipping walk (frozen rows): " + "; ".join(
            f"{m}: " + (", ".join(f"{w['group']} d {w['d_ll']:+.5f} ({'ships' if w['ships'] else 'weight 0'})"
                                  for w in mk["shipping_walk_frozen"]) or "nothing accepted on 2025")
            for m, mk in tst["markets"].items()))
        lines.append("")
        lines.append("DFS hook: " + json.dumps(tst.get("dfs_hook")))
    kp = os.path.join(OUT, "kalshi.json")
    if os.path.exists(kp):
        with open(kp) as fh:
            kj = json.load(fh)
        lines.append("\n### Kalshi, pre-kickoff (descriptive, R5)\n")
        lines.append("| season | stat | n | hit rate | model LL | Sleeper LL | Kalshi LL | model Brier | Kalshi Brier |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for season, blk in kj["by_season"].items():
            for st_, x in [("all", blk["all"])] + sorted(blk["by_stat"].items()):
                if not x.get("n"):
                    continue
                lines.append(f"| {season} | {st_} | {x['n']} | {x['hit_rate']:.3f} | {x['model']['ll']:.4f} | {x['sleeper']['ll']:.4f} | "
                             f"{x['market']['ll']:.4f} | {x['model']['brier']:.4f} | {x['market']['brier']:.4f} |")
            for who in ("roi_model", "roi_sleeper"):
                r = blk.get(who) or {}
                if r.get("bets"):
                    lines.append(f"\n- {season} {who}: {r['bets']} bets over {r['games']} games, staked {r['staked_c'] / 100:.2f} $, "
                                 f"profit {r['profit_c'] / 100:+.2f} $, ROI {100 * r['roi']:+.1f}% "
                                 f"(95% {100 * r['roi_95'][0]:+.1f}% to {100 * r['roi_95'][1]:+.1f}%)")
    print("\n".join(lines))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("traincheck", "prereg", "validate", "test", "export", "kalshi", "tables"))
    ap.add_argument("--raw", help="kalshi: the dir holding kalshi_nfl_hist/ (research/nfl_matchup_kalshi.py)")
    ap.add_argument("--props", help="kalshi: the sim-history props log for 2026 week 6")
    ap.add_argument("--frame2025", help="kalshi: the validation frame (2025 rows, 2018-2024 k)")
    ap.add_argument("--frame")
    ap.add_argument("--frame-sens", dest="frame_sens", help="validate: the 2018-2025-k frame (the k sensitivity, R6)")
    ap.add_argument("--defhist")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--plumbing", action="store_true", help="scramble every 2025/2026 outcome (code-path check only)")
    a = ap.parse_args(argv)
    {"traincheck": cmd_traincheck, "prereg": cmd_prereg, "validate": cmd_validate, "test": cmd_test,
     "export": cmd_export, "kalshi": cmd_kalshi, "tables": cmd_tables}[a.cmd](a)
    return 0


def cmd_export(a):
    """The compact defense-vector history production's comparables read
    (research/data/nfl_matchup_model/def_history.json): every (season, week,
    team) vector as of that week, 2021 through the last completed week."""
    with open(a.defhist) as fh:
        dh = json.load(fh)
    rows = {k: [round(float(x), 3) for x in v] for k, v in dh["rows"].items()}
    write_json("def_history.json", {"schema": M.SCHEMA, "dims": dh["dims"], "rows": rows,
                                    "source": "research/nfl_matchup_frame.py (nfl_matchup.def_vectors as of each week)",
                                    "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
