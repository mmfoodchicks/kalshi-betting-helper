"""The RB / QB rushing model's pre-registered DECISION MACHINERY (the rule:
research/reports/nfl_rush_model.md, RUSH-PREREG block). RESEARCH (numpy).

Every command here runs the rule as written and nothing else:

    validate     the 2025 walk (Q4): fit 2023-2024, score 2025, a group accepted
                 iff d < -2 SE and d < -0.0005 x the number of the market's thresholds;
    check2026    the secondary walk (Q5) on 2026 weeks 1-6's frozen rows over the
                 2025-accepted groups, refit on 2023-2025: kept iff d <= 0;
    export       the provisional model (refit 2023-2025) into coefs.json, each group with
                 its status and the sha256 of the records it passed;
    prospective  once, after the window (Q6): over the provisional groups, kept iff
                 d < -1 SE; writes prospective_lock.json and the final coefs.json.

Each reads held (2025 / 2026) outcomes ONLY through research.nfl_rush_model_fit's
read_frame, which refuses them unless prereg.json exists and the rule block and every
registered file still hash to it (`prereg_status`). `--plumb` replaces every held
outcome by a random 2023 row's outcome WITHOUT reading the held one (the first model's
R6 plumbing run), so the whole chain can be exercised before the hash.

The model the board prices is built by production's own code (nfl_rush.plan /
family_dist), so what a walk scores is exactly what the board would show: the replay
guard checks it.
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
os.environ.setdefault("VIGIL_NO_BG", "1")

import nfl_rush as NR                                   # noqa: E402
import research.nfl_rush_model_fit as FIT               # noqa: E402

LAMBDAS = (1.0, 10.0, 100.0, 300.0, 1e3, 3e3, 1e4, 3e4, 1e5, 1e6)
MIN_GAIN = 0.0005          # nats per threshold: the practical floor (Q4, the first model's R3)
GATE_SE = 2.0              # 2025: d < -2 SE
KEEP_SE = 1.0              # the prospective window: d < -1 SE
CLAMP = 1.0                # the log factor's clamp (Q2)
CLIP = 1e-6                # probabilities clipped to [1e-6, 1 - 1e-6] (Q4)
MIN_WINDOW_GAMES = 40      # the prospective window's floor (Q1 / Q6)
LAST_WEEK, EXT_LAST_WEEK = 11, 13

# the walk order (Q3b): a count market's walk completes before its yards market's, whose compound
# carries the count market's FINAL accepted factor
WALK = {"RB": ("rush_att", "rush_yd", "rec", "rec_yd", "td"), "QB": ("rush_att", "rush_yd", "td")}
FAMILY = {("RB", "rush_att"): "rb_rush", ("RB", "rush_yd"): "rb_rush", ("RB", "rec"): "rb_rec",
          ("RB", "rec_yd"): "rb_rec", ("RB", "td"): "rb_td", ("QB", "rush_att"): "qb_rush",
          ("QB", "rush_yd"): "qb_rush", ("QB", "td"): "qb_td"}
COUNT_OF = {("RB", "rush_yd"): "rush_att", ("RB", "rec_yd"): "rec", ("QB", "rush_yd"): "rush_att"}
YARDS_MARKETS = ("rush_yd", "rec_yd")
DERIVED = {"RB": ("rush_rec_yd", "long_rush"), "QB": ("long_rush",)}
# targets each market's fitted GLMs write (nfl_rush.TARGETS), by form
TARGET = {("RB", "rush_att"): ("carries", "mean_rush_att"), ("RB", "rush_yd"): ("ypc", "mean_rush_yd"),
          ("RB", "rec"): ("rec", "mean_rec"), ("RB", "rec_yd"): ("ypr", "mean_rec_yd")}
QB_COUNT = (("designed", "y_des"), ("scramble", "y_scr"))
QB_EFF = (("designed_eff", "y_des_yds", "qb_designed"), ("scramble_eff", "y_scr_yds", "qb_scramble"))


def log(msg):
    print(msg, flush=True)


# ---- the statistic and the gates (guarded line by line) -------------------------------------------
def clustered(delta, clusters):
    """(mean, SE) of a per-row difference, rows clustered: G / (G - 1) x sum of the clusters'
    centred sums squared / n^2 (the first model's statistic)."""
    delta = np.asarray(delta, float)
    n = len(delta)
    if n == 0:
        return float("nan"), float("nan")
    mean = float(np.mean(delta))
    ug, inv = np.unique(np.asarray(clusters, dtype=object).astype(str), return_inverse=True)
    sums = np.bincount(inv, weights=delta - mean)
    G = len(ug)
    if G < 2:
        return mean, float("nan")
    var = (G / (G - 1)) * float(np.sum(sums ** 2)) / (n * n)
    return mean, math.sqrt(var)


def paired(delta, games, players):
    """(d, SE, se_game, se_player): SE is the LARGER of the game- and the player-clustered SE
    (Q4: on the 2024 traincheck the player-clustered SE was 1.31x the game one for RBs)."""
    d, sg = clustered(delta, games)
    _d, sp = clustered(delta, players)
    se = max(sg, sp) if (math.isfinite(sg) and math.isfinite(sp)) else float("nan")
    return d, se, sg, sp


def accept_2025(d, se, n_thr):
    """Q4: accepted iff d < -2 SE AND d < -0.0005 x the NUMBER of the market's thresholds (strict;
    a non-finite d or SE is a reject)."""
    if not (math.isfinite(d) and math.isfinite(se)):
        return False
    return bool(d < -GATE_SE * se and d < -MIN_GAIN * n_thr)


def keep_2026(d):
    """Q5: a 2025-accepted group stays provisional iff its d on the frozen 2026 rows is not worse
    (point estimate, d <= 0); a non-finite d drops it."""
    return bool(math.isfinite(d) and d <= 0.0)


def keep_prospective(d, se):
    """Q6: a provisional group is KEPT iff d < -1 SE (one-sided); otherwise reverted."""
    if not (math.isfinite(d) and math.isfinite(se)):
        return False
    return bool(d < -KEEP_SE * se)


def error_rates(n_decisions, p_gate=None, p_2026=0.5):
    """Q3b's computed false-ship figures for a NULL group, on the normal approximation: the 2025
    gate alone passes Phi(-GATE_SE) (the floor ignored, so an upper figure), the 2026 check about
    a coin flip (d <= 0), the prospective KEEP Phi(-KEEP_SE). Returns per-decision rates, the
    expected false ships over `n_decisions` and P(at least one), provisional and permanent.
    `p_gate` replaces the normal gate figure with a simulated one (Opus's null simulation)."""
    phi = lambda z: 0.5 * math.erfc(-z / math.sqrt(2.0))
    g = phi(-GATE_SE) if p_gate is None else float(p_gate)
    prov = g * p_2026
    perm = prov * phi(-KEEP_SE)
    one = lambda p: 1.0 - (1.0 - p) ** n_decisions
    return {"gate": g, "provisional": prov, "provisional_expected": prov * n_decisions,
            "provisional_any": one(prov), "permanent": perm, "permanent_expected": perm * n_decisions,
            "permanent_any": one(perm)}


def keep_power(games_2025, games_window, mult=1.0):
    """Q6's stated POWER, on the normal approximation (Fable's m2): the probability that the prospective
    KEEP (d < -KEEP_SE x SE) keeps a group whose TRUE gain is `mult` times the 2025 gate's edge (d =
    -GATE_SE x the 2025 SE), the window's SE being the 2025 SE x sqrt(games_2025 / games_window) -- d's
    SE scales as 1 / sqrt(games), the clustered SE's clusters being games and players."""
    phi = lambda z: 0.5 * math.erfc(-z / math.sqrt(2.0))
    r = math.sqrt(float(games_2025) / float(games_window))
    return phi(mult * GATE_SE / r - KEEP_SE)


def walk(groups, evaluate, decide):
    """The SEQUENTIAL walk (Q4-Q6): each group, in the order given, against the model with the
    groups already kept; `evaluate(kept, g)` -> (d, se, extra), `decide(d, se)` -> bool. A dropped
    group is never retried and later groups are judged on top of what was kept."""
    kept, steps = [], []
    for g in groups:
        d, se, extra = evaluate(list(kept), g)
        ok = decide(d, se)
        steps.append(dict({"group": g, "d": d, "se": se, "kept": ok}, **(extra or {})))
        if ok:
            kept.append(g)
    return kept, steps


def window_games(game_kicks, reg_ts, week_of, last_week=LAST_WEEK, ext_last=EXT_LAST_WEEK, floor=MIN_WINDOW_GAMES):
    """The prospective window (Q1): every game kicking off strictly after the registration time
    through `last_week`, extended a week at a time to `ext_last` while fewer than `floor` games
    hold a universe row -- counted from pre-game information only (kickoffs, frozen lines),
    never an outcome. Returns (last week used, games, note)."""
    lw = last_week
    while True:
        games = sorted(g for g, k in game_kicks.items() if k is not None and k > reg_ts and week_of[g] <= lw)
        if len(games) >= floor or lw >= ext_last:
            note = None if len(games) >= floor else (f"only {len(games)} games by week {lw}: the test runs on them "
                                                     f"and says it is under the {floor}-game floor")
            return lw, games, note
        lw += 1


# ---- frames -------------------------------------------------------------------------------------
class Frame:
    """A frame's rows by position, as arrays; outcomes only where read_frame allowed them."""

    def __init__(self, rows, kind_of):
        self.rows = rows
        self.kind_of = kind_of        # season -> "sl" (Sleeper's revised line) or "fz" (frozen capture)

    def select(self, pos, market, seasons, played_only=True):
        """Row indices in the market's universe (nfl_rush.in_universe on the season's baseline) and,
        for scoring and fitting alike, CONDITIONAL ON PLAYING (Q1): Sleeper's box shows gp >= 1, or
        the pbp credits him a rush, a target or a dropback. Never PFR snaps."""
        out = []
        for i, r in enumerate(self.rows):
            if r["pos"] != pos or int(r["season"]) not in seasons:
                continue
            if not NR.in_universe(pos, market, self.base(i)):
                continue
            if played_only and not played(r):
                continue
            out.append(i)
        return out

    def base(self, i):
        r = self.rows[i]
        k = self.kind_of(int(r["season"]))
        b = {f: (_x(r, f"{k}_{f}") or 0.0) for f in ("rush_att", "rush_yd", "rush_td", "rec_tgt", "rec", "rec_yd",
                                                      "rec_td", "pass_att")}
        return b

    def feats(self, i):
        r = self.rows[i]
        return {n: _x(r, f"f_{n}") for n in NR.FEATURES}


def _x(r, k):
    v = r.get(k)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def played(r):
    """Q1: he played iff Sleeper's box shows gp >= 1 or the pbp credits him a rush, a target or a
    dropback (y_gp / y_pbp_play, written by the frame; PFR snaps never enter)."""
    if r.get("y_cond_played") not in (None, ""):
        return r["y_cond_played"] in ("1", "1.0", "True", 1, True)
    gp, pb = _x(r, "y_gp"), _x(r, "y_pbp_play")
    return bool((gp is not None and gp >= 1) or (pb is not None and pb > 0))


def load(path, seasons, kind_of, plumb=False, seed=7):
    """The frame for `seasons` with outcomes; held seasons' outcomes come through the registration
    check, or with --plumb from random 2023 rows (the held ones are never read)."""
    held = [s for s in seasons if s in FIT.HELD]
    if not plumb or not held:
        return Frame(FIT.read_frame(path, seasons, outcomes=True), kind_of)
    open_s = [s for s in seasons if s not in FIT.HELD]
    rows = FIT.read_frame(path, open_s, outcomes=True) if open_s else []
    donors = [r for r in FIT.read_frame(path, (2023,), outcomes=True)]
    ycols = sorted({k for r in donors for k in r if k.startswith("y_")})
    rng = np.random.default_rng(seed)
    for r in FIT.read_frame(path, held, outcomes=False):
        pool = [d for d in donors if d["pos"] == r["pos"]]
        dn = pool[int(rng.integers(len(pool)))]
        r = dict(r, **{k: dn.get(k) for k in ycols})
        r["plumb"] = "1"
        rows.append(r)
    return Frame(rows, kind_of)


def kind_revised(season):
    return "sl"


def kind_test(season):
    return "fz" if int(season) == 2026 else "sl"


# ---- terms ----------------------------------------------------------------------------------------
def dead_features(frames, train):
    """{pos: set of features}: a feature whose SD over a position's training rows is zero in EITHER
    frame is dropped from every fit (Q2). `train` {frame index: seasons}."""
    out = {"RB": set(), "QB": set()}
    for k, F in enumerate(frames):
        for pos in out:
            idx = [i for i, r in enumerate(F.rows) if r["pos"] == pos and int(r["season"]) in train[k]]
            for n in NR.FEATURES:
                v = fill_column(F, idx, n)
                if len(v) < 2 or float(np.std(v)) < 1e-12:
                    out[pos].add(n)
    return out


def group_terms(pos, market, group, shape, dead):
    """The live terms a group contributes to a market (Q3b): before SHAPE a count market reads the
    group's count terms and a yards market all of them; from SHAPE on a count market reads count
    terms (QB: each mapped to its component) and a yards market eff terms; TD markets their own."""
    fam = FAMILY[(pos, market)]
    fs = [(f, a) for f, a in (NR.GROUPS[fam].get(group) or []) if f not in dead.get(pos, ())]
    if market in ("rush_att", "rec"):
        return [f for f, a in fs if a == "count"]
    if market in YARDS_MARKETS:
        return [f for f, a in fs if a == "eff"] if shape else [f for f, _a in fs]
    return [f for f, _a in fs]


def market_groups(pos, market, dead):
    """The groups the walk visits for a market, in the owner's order: SHAPE where the compound
    prices the market, every other group with at least one live term (a group left with none is
    not evaluated and cannot ship)."""
    out = []
    for g in NR.LADDER:
        if g == "SHAPE":
            if market in NR.SHAPE_MARKETS.get(pos, ()):
                out.append(g)
            continue
        if g not in NR.GROUPS[FAMILY[(pos, market)]]:
            continue
        if group_terms(pos, market, g, False, dead) or group_terms(pos, market, g, True, dead):
            out.append(g)
    return out


def decisions(dead):
    """Every (pos, market, group) decision of the 2025 walk."""
    return [(p, m, g) for p, ms in WALK.items() for m in ms for g in market_groups(p, m, dead)]


# ---- fitting --------------------------------------------------------------------------------------
def standardise(X, names):
    """Columns centred and scaled by the fit rows' own mean and SD; a column with zero SD in these
    rows is dropped (recorded)."""
    if X.shape[0] == 0:                    # no fit rows: nothing can be fitted (every column dropped)
        return np.zeros((0, 0)), [], np.zeros(0), np.zeros(0), list(names)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    keep = [j for j in range(X.shape[1]) if sd[j] > 1e-12]
    dropped = [names[j] for j in range(X.shape[1]) if sd[j] <= 1e-12]
    Z = (X[:, keep] - mu[keep]) / sd[keep] if keep else np.zeros((X.shape[0], 0))
    return Z, [names[j] for j in keep], mu[keep], sd[keep], dropped


def fit_ridge(Z, y, off, seasons):
    """Ridge quasi-Poisson, NO intercept (the level is Sleeper's: Q2), lambda from LAMBDAS by
    leave-one-season-out deviance over the fitting seasons only. Returns (b, cov, lambda)."""
    from research.nfl_matchup_fit import deviance, fit_glm
    y = np.maximum(np.asarray(y, float), 0.0)
    off = np.asarray(off, float)
    p = Z.shape[1]
    if p == 0 or len(y) == 0:
        return np.zeros(p), np.zeros((p, p)), None
    pen = np.ones(p)
    useasons = sorted(set(seasons.tolist()))
    best = None
    for lam in LAMBDAS:
        dev = 0.0
        for s in useasons:
            tr, te = seasons != s, seasons == s
            if tr.sum() == 0 or te.sum() == 0:
                continue
            b, _H = fit_glm(Z[tr], y[tr], off[tr], lam, pen)
            dev += deviance(y[te], np.exp(np.clip(off[te] + Z[te] @ b, -30, 30)))
        if best is None or dev < best[0] - 1e-12:
            best = (dev, lam)
    lam = best[1]
    b, H = fit_glm(Z, y, off, lam, pen)
    cov = np.linalg.inv(H)
    return b, cov, lam


def model_json(names, mu, sd, b, cov):
    return {"terms": [{"feature": n, "b": float(bb), "mean": float(m), "sd": float(s)}
                      for n, bb, m, s in zip(names, b, mu, sd)],
            "cov": [[float(x) for x in row] for row in cov] if len(names) else None, "clamp": CLAMP}


def eta_of(model, F, idx):
    """The clamped log factor of a fitted target model on rows `idx` (production's linear())."""
    return np.array([NR.linear(model, F.feats(i))[0] for i in idx], float)


def fill_column(F, idx, name):
    """One feature over rows `idx` as the fit reads it: a missing value is 0 (a deviation's
    "nothing known"), or for a nfl_rush.MEAN_FILL count the mean of the rows that have it -- the
    column's mean is then that same figure, so the board's missing read (the term's mean) adds
    nothing, exactly as here."""
    v = [_x(F.rows[i], f"f_{name}") for i in idx]
    fill = 0.0
    if name in NR.MEAN_FILL:
        got = [x for x in v if x is not None]
        fill = float(np.mean(got)) if got else 0.0
    return np.array([fill if x is None else x for x in v], float)


def fit_target(F, idx, terms, y, off, seasons):
    X = np.array([fill_column(F, idx, t) for t in terms], float).T.reshape(len(idx), len(terms))
    Z, names, mu, sd, dropped = standardise(X, terms)
    b, cov, lam = fit_ridge(Z, y, off, seasons)
    return model_json(names, mu, sd, b, cov), {"lambda": lam, "dropped_zero_sd": dropped, "n": len(idx)}


def _y(F, idx, col):
    return np.array([(_x(F.rows[i], col) or 0.0) for i in idx], float)


def _season(F, idx):
    return np.array([int(F.rows[i]["season"]) for i in idx])


def fit_position(F, pos, states, train, parts, disp, dead):
    """The coefs fragment for one position: {"shipped", "models", "fit"} from the accepted groups of
    each market (`states` {market: [groups]}), fit on the `train` seasons' rows in each market's
    universe (played). The walk order's coupling: a yards market's compound offset carries its
    count market's fitted factor; TD markets their own."""
    shipped, models, info = {}, {}, {}
    base_co = dict(parts, dispersion=disp, shipped={}, models={})
    for m in WALK[pos]:
        groups = list(states.get(m) or [])
        shape = "SHAPE" in groups
        key = NR.market_key(pos, m)
        shipped[key] = [{"group": g, "status": "provisional"} for g in groups]
        terms = sorted({t for g in groups if g != "SHAPE" for t in group_terms(pos, m, g, shape, dead)})
        idx = F.select(pos, m, train)
        seas = _season(F, idx)
        co_sofar = dict(base_co, shipped=dict(shipped), models=dict(models))
        if m == "td":
            y = _y(F, idx, "y_rush_td") + _y(F, idx, "y_rec_td")
            off = np.log(np.maximum([F.base(i)["rush_td"] + F.base(i)["rec_td"] for i in idx], 1e-6))
            models[NR.market_key(pos, "td")], info[(m, "td")] = fit_target(F, idx, terms, y, off, seas)
            continue
        if pos == "RB":
            tgt = TARGET[(pos, m)][0 if shape else 1]
            ycol = {"rush_att": "y_rush_att", "rush_yd": "y_rush_yd", "rec": "y_rec", "rec_yd": "y_rec_yd"}[m]
            y = _y(F, idx, ycol)
            off = np.log(np.maximum([F.base(i)[m] for i in idx], 1e-6))
            if shape and m in YARDS_MARKETS:
                cm = COUNT_OF[(pos, m)]
                cmodel = models.get(NR.market_key(pos, TARGET[(pos, cm)][0])) or \
                    models.get(NR.market_key(pos, TARGET[(pos, cm)][1]))
                off = off + (eta_of(cmodel, F, idx) if cmodel else 0.0)
            models[NR.market_key(pos, tgt)], info[(m, tgt)] = fit_target(F, idx, terms, y, off, seas)
            continue
        # quarterbacks
        if not shape:
            ycol = "y_rush_att" if m == "rush_att" else "y_rush_yd"
            y = _y(F, idx, ycol)
            off = np.log(np.maximum([F.base(i)[m] for i in idx], 1e-6))
            tgt = "mean_rush_att" if m == "rush_att" else "mean_rush_yd"
            models[NR.market_key(pos, tgt)], info[(m, tgt)] = fit_target(F, idx, terms, y, off, seas)
            continue
        comp_terms = lambda c: [t for t in terms if NR.QB_COMPONENT.get(t) in (c, "both")]
        if m == "rush_att":
            exp_ = [_qb_expected(co_sofar, F, i) for i in idx]
            for tgt, ycol in QB_COUNT:
                c = tgt
                off = np.log(np.maximum([e[c]["count"] for e in exp_], 1e-6))
                models[NR.market_key(pos, tgt)], info[(m, tgt)] = fit_target(F, idx, comp_terms(c), _y(F, idx, ycol),
                                                                             off, seas)
        else:
            exp_ = [_qb_expected(co_sofar, F, i) for i in idx]
            for tgt, ycol, role in QB_EFF:
                c = tgt.replace("_eff", "")
                off = np.log(np.maximum([e[c]["yards"] for e in exp_], 1e-6))
                models[NR.market_key(pos, tgt)], info[(m, tgt)] = fit_target(F, idx, comp_terms(c), _y(F, idx, ycol),
                                                                             off, seas)
    return {"shipped": shipped, "models": models, "fit": {f"{a}|{b}": v for (a, b), v in info.items()}}


def _qb_expected(co, F, i):
    """A QB row's expected designed runs / scrambles and their yards under the model so far (the
    count factors fitted already, no efficiency term): production's qb_parts through plan's own
    input mapping, so the offsets are what the board's compound starts from."""
    pl = NR.plan(co, "QB", F.base(i), F.feats(i), None, team_margin=_x(F.rows[i], "margin"))
    out = {}
    for c in (pl.get("parts") or {}).get("components") or []:
        role = c["role"].replace("qb_", "")
        out[role] = {"count": c["count_mean"], "yards": c["count_mean"] * c["ypa"]}
    for r in ("designed", "scramble"):
        out.setdefault(r, {"count": 0.0, "yards": 0.0})
    return out


# ---- scoring --------------------------------------------------------------------------------------
def market_dist(co, F, i, pos, market):
    """The distribution the board would price for one row under coefs `co` (nfl_rush.plan; the
    rule's own rung-1 family where nothing is accepted). None where the market has none."""
    base, feats = F.base(i), F.feats(i)
    pl = NR.plan(co, pos, base, feats, None, team_margin=_x(F.rows[i], "margin"))
    info = pl["markets"].get(market)
    if info is None:
        return None
    if info["form"] == "rung1":
        mu = {"td": base["rush_td"] + base["rec_td"]}.get(market, base.get(market))
        return NR.family_dist(co, pos, market, mu, base)
    if info["form"] == "none":
        return None
    if info["form"] == "mean":
        mu = (base.get(market) or 0.0) * info["mult"]
        return NR.family_dist(co, pos, market, mu, base)
    return info["dist_fn"](1.0)


def outcome(F, i, market):
    r = F.rows[i]
    if market == "td":
        return (_x(r, "y_rush_td") or 0.0) + (_x(r, "y_rec_td") or 0.0)
    if market == "rush_rec_yd":
        return (_x(r, "y_rush_yd") or 0.0) + (_x(r, "y_rec_yd") or 0.0)
    if market == "long_rush":
        # no rush is a longest of 0 (every threshold a miss, as Kalshi settles it), never a dropped row
        v = _x(r, "y_long")
        return 0.0 if v is None and _x(r, "y_rush_att") == 0.0 else v
    return _x(r, {"rush_att": "y_rush_att", "rush_yd": "y_rush_yd", "rec": "y_rec", "rec_yd": "y_rec_yd"}[market])


def row_ll(dist, y, ks):
    if dist is None or y is None:
        return None
    tot = 0.0
    for k in ks:
        p = min(1.0 - CLIP, max(CLIP, float(dist.sf_ge(k))))
        tot -= math.log(p) if y >= k else math.log(1.0 - p)
    return tot


def score(co, F, idx, pos, market, detail=None):
    """Per-row threshold log loss (Q4) of the board's distribution for a market; `detail` (a list)
    collects (mean, {k: p}, y) per row for the descriptive MAE and ECE."""
    ks = NR.THRESHOLDS[pos][market]
    out = []
    for i in idx:
        d, y = market_dist(co, F, i, pos, market), outcome(F, i, market)
        out.append(row_ll(d, y, ks))
        if detail is not None:
            mu = None
            if d is not None:
                mu = NR.expected_longest(d) if market == "long_rush" else d.mean()
            detail.append((mu, {k: (float(d.sf_ge(k)) if d is not None else None) for k in ks}, y))
    return out


def describe(detail, keep, ks):
    """Reported per rung, deciding nothing (Q4): the MAE of the mean and the 10-bin ECE per threshold."""
    rows = [detail[j] for j in keep]
    mae = [abs(m - y) for m, _p, y in rows if m is not None and y is not None]
    ece = {}
    for k in ks:
        pr = [(p[k], 1.0 if y >= k else 0.0) for _m, p, y in rows if p.get(k) is not None and y is not None]
        if not pr:
            continue
        bins = {}
        for p_, h in pr:
            bins.setdefault(min(9, int(p_ * 10)), []).append((p_, h))
        ece[str(k)] = sum(len(b) / len(pr) * abs(sum(x for x, _ in b) / len(b) - sum(h for _, h in b) / len(b))
                          for b in bins.values())
    return {"mae": float(np.mean(mae)) if mae else None, "ece": ece}


def compare(co_a, co_b, F, idx, pos, market):
    """d = candidate (co_b) minus accepted (co_a), per row; rows either side cannot price drop out
    (counted)."""
    da_, db_ = [], []
    la, lb = score(co_a, F, idx, pos, market, da_), score(co_b, F, idx, pos, market, db_)
    keep = [j for j, (a, b) in enumerate(zip(la, lb)) if a is not None and b is not None]
    delta = [lb[j] - la[j] for j in keep]
    g = [F.rows[idx[j]]["game_id"] for j in keep]
    pl = [F.rows[idx[j]]["gsis"] for j in keep]
    d, se, sg, sp = paired(delta, g, pl)
    ks = NR.THRESHOLDS[pos][market]
    lead = sorted(_x(F.rows[idx[j]], "fz_lead_h") for j in keep if _x(F.rows[idx[j]], "fz_lead_h") is not None)
    return d, se, {"n": len(keep), "n_dropped": len(idx) - len(keep), "games": len(set(g)),
                   "se_game": sg, "se_player": sp, "ll_accepted": float(np.mean([la[j] for j in keep])) if keep else None,
                   "ll_candidate": float(np.mean([lb[j] for j in keep])) if keep else None,
                   "accepted_describe": describe(da_, keep, ks), "candidate_describe": describe(db_, keep, ks),
                   "capture_lead_h": ({"min": lead[0], "median": lead[len(lead) // 2], "max": lead[-1]} if lead else None)}


# ---- the fitted parts each step reads ------------------------------------------------------------
def dispersion(F, train):
    """The rung-1 families' parameters on the training rows (Q2): NegBin alpha by moments for
    carries / attempts / receptions; the yards' positive-part CV by moments, p0 from the count
    line at the count alpha (the first model's estimators, this rule's universes, played rows)."""
    out = {}
    for pos, pairs in (("RB", (("rush_att", "rush_yd"), ("rec", "rec_yd"))), ("QB", (("rush_att", "rush_yd"),))):
        out[pos] = {}
        for cm, ym in pairs:
            idx = F.select(pos, cm, train)
            y = np.array([outcome(F, i, cm) or 0.0 for i in idx])
            mu = np.array([F.base(i)[cm] for i in idx])
            a = float(np.sum((y - mu) ** 2 - mu) / np.sum(mu * mu)) if len(idx) else 0.0
            out[pos][cm] = {"alpha": max(0.0, a), "n": len(idx)}
            idx = F.select(pos, ym, train)
            yy = np.array([outcome(F, i, ym) or 0.0 for i in idx])
            by = np.array([F.base(i)[ym] for i in idx])
            bc = np.array([F.base(i)[cm] for i in idx])
            import prop_dist as PD
            p0 = np.array([PD.NegBin(c, out[pos][cm]["alpha"]).pmf(0) for c in bc])
            m_pos = by / (1.0 - np.clip(p0, 0, 0.999))
            ok = yy > 0
            cv = float(math.sqrt(np.sum((yy[ok] - m_pos[ok]) ** 2) / np.sum(m_pos[ok] ** 2))) if ok.any() else 1.0
            out[pos][ym] = {"cv": cv, "n": len(idx)}
    return out


def parts_for(train, plumb=False, rec=None):
    """The compound's parts (kernels, alpha curves, kneel table, QB priors) fit on `train`. In the
    plumbing run (`plumb`, from the command's --plumb) they are fit on the open seasons of `train`
    only: they read the pbp and Sleeper's box directly, and a held season's are never read before
    the registration. A registered command (`rec`) reads the positions and Sleeper ids the
    registration recorded (fit.registered_positions), never the ids pickle in the folder: the
    prospective test's folder holds a FRESH one (build-stores), in which nflverse may have relisted a
    player since the export (Fable's m7)."""
    pos_sl = None
    if plumb:
        train = tuple(s for s in train if s not in FIT.HELD)
    elif rec is not None:
        pos_sl = FIT.registered_positions(rec)
        if pos_sl is None:
            raise SystemExit("refused: prereg.json records no ids positions (register on the current code)")
    co = FIT.fit_distributions(tuple(train), pos_sl=pos_sl)
    return {k: co[k] for k in ("schema", "kernels", "alpha", "kneels", "qb_prior")}


def coefs_for(parts, disp, frag):
    return dict(parts, dispersion=disp, shipped=frag["shipped"], models=frag["models"])


# ---- the walks ------------------------------------------------------------------------------------
def walk_markets(F_fit, train, F_eval, eval_rows, parts, disp, dead, groups_for, decide, label, report_all=False):
    """The sequential walk for every market, a position's count market before its yards market:
    `groups_for(pos, market)` lists the groups visited in the owner's order, each judged against
    the model with the groups already kept for that market (and the count market's kept model
    in the yards market's offsets). Every candidate and every baseline is fit on `train` by
    fit_position -- exactly the model that would ship. Returns (kept, steps)."""
    states = {"RB": {}, "QB": {}}
    out = {"RB": {}, "QB": {}}
    for pos in WALK:
        for m in WALK[pos]:
            idx = eval_rows(F_eval, pos, m)
            idx_all = eval_rows(F_eval, pos, m, played_only=False) if report_all else None

            def evaluate(kept, g, pos=pos, m=m, idx=idx, idx_all=idx_all):
                co_a = coefs_for(parts, disp, fit_position(F_fit, pos, dict(states[pos], **{m: kept}), train,
                                                           parts, disp, dead))
                co_b = coefs_for(parts, disp, fit_position(F_fit, pos, dict(states[pos], **{m: kept + [g]}), train,
                                                           parts, disp, dead))
                d, se, extra = compare(co_a, co_b, F_eval, idx, pos, m)
                if idx_all is not None:
                    da, sa, ea = compare(co_a, co_b, F_eval, idx_all, pos, m)
                    extra["all_rows"] = {"d": da, "se": sa, "n": ea["n"]}       # reported, deciding nothing
                extra["derived"] = derived_report(co_a, co_b, F_eval, pos, m, idx_rows=eval_rows)
                return d, se, extra
            kept, st = walk(groups_for(pos, m), evaluate, decide(pos, m))
            states[pos][m] = kept
            out[pos][m] = st
            log(f"[rule] {label} {pos} {m}: kept {kept}")
    return states, out


def derived_report(co_a, co_b, F, pos, m, idx_rows):
    """The derived markets (rush + rec, the longest rush), reported, deciding nothing (Q3/Q4): the d
    where both sides price them, and -- where only the candidate does, its FIRST priced form, the one
    that would ship on KXNFLRRYDS / KXNFLLONGRSH -- its own log loss, MAE and 10-bin ECE."""
    rep = {}
    if m not in YARDS_MARKETS:
        return rep
    for dm in DERIVED[pos]:
        idx = idx_rows(F, pos, dm)
        da_, db_ = [], []
        la, lb = score(co_a, F, idx, pos, dm, da_), score(co_b, F, idx, pos, dm, db_)
        keep = [j for j, (a, b) in enumerate(zip(la, lb)) if a is not None and b is not None]
        first = [j for j, (a, b) in enumerate(zip(la, lb)) if a is None and b is not None]
        if keep:
            d, se, _sg, _sp = paired([lb[j] - la[j] for j in keep], [F.rows[idx[j]]["game_id"] for j in keep],
                                     [F.rows[idx[j]]["gsis"] for j in keep])
            rep[dm] = {"n": len(keep), "d": d, "se": se}
        if first:
            rep.setdefault(dm, {})["first_priced"] = dict(
                {"n": len(first), "ll": float(np.mean([lb[j] for j in first]))},
                **describe(db_, first, NR.THRESHOLDS[pos][dm]))
    return rep


def gate_2025(pos, m):
    n_thr = len(NR.THRESHOLDS[pos][m])
    return lambda d, se: accept_2025(d, se, n_thr)


# ---- registration, records, locks -------------------------------------------------------------------
def require_registered(plumb):
    """Every command: the registration must hold (plumbing reads no held outcome and writes only
    under plumb/), and the store pickles the step reads must still hash to the registered frames'
    record (verify_stores)."""
    if plumb:
        return {"plumb": True}
    ok, why = FIT.prereg_status()
    if not ok:
        raise SystemExit(f"refused: {why} (research/data/nfl_rush_model/prereg.json)")
    with open(os.path.join(FIT.MODEL_OUT, "prereg.json")) as fh:
        return json.load(fh)


def verify_stores(rec, seasons, plumb):
    """The season stores the distribution parts read (fit_distributions: the pbp of `seasons`) must be
    the registration's: they are read directly, not through a frame. By sha256, or -- re-saved, or
    rebuilt after the archive was lost -- by CONTENT digest (fit.store_refusal); a mismatch refuses,
    naming the parts that differ. The positions those parts read are the registration's record
    (parts_for), so the ids pickle, rebuilt fresh for the prospective rows, is not compared here:
    build-stores vouches for it, and provenance_refusal holds the frame to that."""
    if plumb:
        return
    bad = [why for why in (FIT.store_refusal(f"deep:nfl_matchup_{s_}", rec) for s_ in seasons) if why]
    if bad:
        raise SystemExit(f"refused: the store pickles are not the registration's: {bad}")


def require_frame(path, which, rec, plumb):
    """A frame the rule reads must be the one registered (validation / test), by sha256."""
    if plumb:
        return
    want = (rec.get("frames") or {}).get(which)
    if not want or FIT._sha(path) != want:
        raise SystemExit(f"refused: the {which} frame is not the registered one ({path})")


def out_dir(plumb):
    d = os.path.join(FIT.MODEL_OUT, "plumb") if plumb else FIT.MODEL_OUT
    os.makedirs(d, exist_ok=True)
    return d


def write_once(path, obj, force):
    """A decision record is written ONCE; a re-run needs --force, and the record says so."""
    if os.path.exists(path) and not force:
        raise SystemExit(f"refused: {os.path.basename(path)} exists (the rule runs once; --force re-runs and says so)")
    if force and os.path.exists(path):
        obj = dict(obj, forced_rerun=True, previous_sha256=FIT._sha(path))
    with open(path, "w") as fh:
        json.dump(_clean(obj), fh, indent=1)
    return FIT._sha(path)


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return float(o) if math.isfinite(float(o)) else None
    if isinstance(o, np.integer):
        return int(o)
    return o


def run_record(rec, plumb):
    """What every decision record carries: the registration it ran under (its block sha256 and time)
    and the code it ran on (every registered file's sha256 at run time) with the interpreter's
    versions, so a later command can check the record it reads (read_record)."""
    return {"prereg_sha256": rec.get("sha256"), "prereg_utc": rec.get("utc"),
            "code": {f: FIT._sha(os.path.join(FIT.REPO, f)) for f in FIT.REG_CODE}, "versions": FIT.versions(),
            "plumb": bool(plumb), "written_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def read_record(name, plumb, rec, chain=()):
    """A decision record the next command reads: it must exist (the commands run in order), have been
    written under THIS registration on the registered code, and every record it names in `chain`
    ((key, file) pairs) must still hash to what it recorded."""
    d = out_dir(plumb)
    p = os.path.join(d, name)
    if not os.path.exists(p):
        raise SystemExit(f"refused: {name} does not exist yet (the commands run in order)")
    with open(p) as fh:
        got = json.load(fh)
    if not plumb and (got.get("prereg_sha256") != rec.get("sha256") or got.get("code") != rec.get("code")):
        raise SystemExit(f"refused: {name} was not written under this registration on its registered code")
    for key, other in chain:
        q = os.path.join(d, other)
        if not os.path.exists(q) or got.get(key) != FIT._sha(q):
            raise SystemExit(f"refused: {name}'s {key} does not match {other} (the record chain is broken)")
    return got, FIT._sha(p)


# ---- the commands ---------------------------------------------------------------------------------
def rows_2025(F, pos, m, played_only=True):
    return F.select(pos, m, (2025,), played_only=played_only)


def rows_2026_frozen(F, pos, m, played_only=True):
    """2026 rows with a frozen pre-kickoff capture (weeks 1-3 and 6 today)."""
    return [i for i in F.select(pos, m, (2026,), played_only=played_only) if F.rows[i].get("fz_src")]


def rows_window(gset):
    def sel(F, pos, m, played_only=True):
        return [i for i in F.select(pos, m, (2026,), played_only=played_only)
                if F.rows[i]["game_id"] in gset and F.rows[i].get("fz_src")]
    return sel


def cmd_validate(a):
    rec = require_registered(a.plumb)
    require_frame(a.frame_val, "val", rec, a.plumb)
    require_frame(a.frame, "test", rec, a.plumb)
    verify_stores(rec, (2023, 2024), a.plumb)
    t0 = time.time()
    Fv = load(a.frame_val, (2023, 2024, 2025), kind_revised, plumb=a.plumb)
    Ft = load(a.frame, (2023, 2024, 2025), kind_revised, plumb=a.plumb)
    dead = dead_features([Fv, Ft], {0: (2023, 2024), 1: (2023, 2024, 2025)})
    parts = parts_for((2023, 2024), a.plumb, rec)
    disp = dispersion(Fv, (2023, 2024))
    states, steps = walk_markets(Fv, (2023, 2024), Fv, rows_2025, parts, disp, dead,
                                 lambda p, m: market_groups(p, m, dead), gate_2025, "2025", report_all=True)
    res = dict(run_record(rec, a.plumb), rule="Q4", fit=[2023, 2024], eval=2025, baseline="sl (Sleeper's revised line)",
               frame_val_sha256=FIT._sha(a.frame_val), dead={p: sorted(v) for p, v in dead.items()},
               decisions=len(decisions(dead)), dispersion=disp, accepted=states, steps=steps,
               seconds=round(time.time() - t0, 1))
    sha = write_once(os.path.join(out_dir(a.plumb), "validate.json"), res, a.force)
    log(f"[rule] validate -> {sha}")


def _accepted_order(groups):
    """A walk visits groups in the owner's ladder order whatever order a record lists them in."""
    return [g for g in NR.LADDER if g in (groups or [])]


def cmd_check2026(a):
    rec = require_registered(a.plumb)
    require_frame(a.frame, "test", rec, a.plumb)
    verify_stores(rec, (2023, 2024, 2025), a.plumb)
    val, val_sha = read_record("validate.json", a.plumb, rec)
    F = load(a.frame, (2023, 2024, 2025, 2026), kind_test, plumb=a.plumb)
    dead = {p: set(v) for p, v in val["dead"].items()}
    parts = parts_for((2023, 2024, 2025), a.plumb, rec)
    disp = dispersion(F, (2023, 2024, 2025))
    states, steps = walk_markets(F, (2023, 2024, 2025), F, rows_2026_frozen, parts, disp, dead,
                                 lambda p, m: _accepted_order(val["accepted"][p].get(m)),
                                 lambda p, m: (lambda d, se: keep_2026(d)), "2026")
    res = dict(run_record(rec, a.plumb), rule="Q5", fit=[2023, 2024, 2025], eval="2026 weeks 1-6, frozen rows",
               baseline="fz (the frozen pre-kickoff capture) for 2026", validate_sha256=val_sha, provisional=states,
               steps=steps, dispersion=disp)
    sha = write_once(os.path.join(out_dir(a.plumb), "check2026.json"), res, a.force)
    log(f"[rule] check2026 -> {sha}")


def correlations(F, train):
    """The within-team residual correlations (actual minus Sleeper's line) on the training rows, for
    pricing two props together: a QB's rushing attempts vs his RB1's carries, and an RB's rushing vs
    receiving yards."""
    out = []
    by = {}
    for i, r in enumerate(F.rows):
        if int(r["season"]) in train and played(r):
            by.setdefault((r["game_id"], r["team"]), []).append(i)
    xs, ys, xr, yr = [], [], [], []
    for _k, idx in by.items():
        qbs = [i for i in idx if F.rows[i]["pos"] == "QB" and NR.in_universe("QB", "rush_att", F.base(i))]
        rbs = sorted([i for i in idx if F.rows[i]["pos"] == "RB" and NR.in_universe("RB", "rush_att", F.base(i))],
                     key=lambda i: -F.base(i)["rush_att"])
        if qbs and rbs:
            q, b = qbs[0], rbs[0]
            xs.append((outcome(F, q, "rush_att") or 0.0) - F.base(q)["rush_att"])
            ys.append((outcome(F, b, "rush_att") or 0.0) - F.base(b)["rush_att"])
        for b in rbs:
            if NR.in_universe("RB", "rec", F.base(b)):
                xr.append((outcome(F, b, "rush_yd") or 0.0) - F.base(b)["rush_yd"])
                yr.append((outcome(F, b, "rec_yd") or 0.0) - F.base(b)["rec_yd"])
    for a_, b_, x, y, basis in (("qb_rush_att", "rb1_rush_att", xs, ys, "his team's RB1 by projected carries"),
                                ("rb_rush_yd", "rb_rec_yd", xr, yr, "the same back")):
        if len(x) > 30:
            out.append({"a": a_, "b": b_, "rho": float(np.corrcoef(x, y)[0, 1]), "n": len(x),
                        "basis": f"actual minus Sleeper's revised line, {basis}, {train[0]}-{train[-1]} played rows"})
    return out


def build_coefs(F, states, status, train, dead, records, plumb=False, rec=None):
    """coefs.json: the model refit on `train` with exactly `states`' groups, each carrying `status`
    and the records it passed (their sha256). Nothing else is ever written as shipped."""
    if status not in NR.SHIP_STATUSES:
        raise SystemExit(f"refused: {status!r} is not a shipping status")
    parts = parts_for(train, plumb, rec)
    disp = dispersion(F, train)
    shipped, models, fit = {}, {}, {}
    for pos in WALK:
        frag = fit_position(F, pos, states.get(pos) or {}, train, parts, disp, dead)
        for k, groups in frag["shipped"].items():
            if groups:
                shipped[k] = [{"group": g["group"], "status": status} for g in groups]
        models.update(frag["models"])
        fit[pos] = frag["fit"]
    return dict(parts, schema=NR.SCHEMA, fit_seasons=list(train), dispersion=disp, shipped=shipped, models=models,
                fit=fit, correlations=correlations(F, train), records=records, stage=status,
                prereg_sha256=records.get("prereg_sha256"),        # the board's model.rushing reads it here
                built_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def _records(rec, plumb, **shas):
    """The records a shipped group carries: the registered block's sha256, prereg.json's own
    sha256, and each decision record's (nfl_rush.record_chain checks them beside coefs.json)."""
    p = os.path.join(FIT.MODEL_OUT, "prereg.json")
    return dict({"prereg_sha256": rec.get("sha256"),
                 "prereg_json_sha256": None if plumb or not os.path.exists(p) else FIT._sha(p)}, **shas)


def cmd_export(a):
    rec = require_registered(a.plumb)
    require_frame(a.frame, "test", rec, a.plumb)
    verify_stores(rec, (2023, 2024, 2025), a.plumb)
    d = out_dir(a.plumb)
    if os.path.exists(os.path.join(d, "prospective_lock.json")):
        # after the lock, the only coefs.json is the prospective test's: an export would re-ship the whole
        # provisional set, the reverted groups included (the re-check's R4)
        raise SystemExit("refused: the prospective test has run (prospective_lock.json); export cannot follow it")
    val, val_sha = read_record("validate.json", a.plumb, rec)
    chk, chk_sha = read_record("check2026.json", a.plumb, rec, chain=(("validate_sha256", "validate.json"),))
    F = load(a.frame, (2023, 2024, 2025), kind_revised, plumb=a.plumb)
    dead = {p: set(v) for p, v in val["dead"].items()}
    co = build_coefs(F, chk["provisional"], "provisional", (2023, 2024, 2025), dead,
                     _records(rec, a.plumb, validate_sha256=val_sha, check2026_sha256=chk_sha), a.plumb, rec)
    sha = write_once(os.path.join(d, "coefs.json"), co, a.force)        # written once at this stage
    log(f"[rule] export -> {os.path.join(d, 'coefs.json')} ({sha})")


def _reg_ts(rec, a):
    if a.plumb:
        return float(a.plumb_reg_ts or 0)
    return _utc_ts(rec["utc"])


def _utc_ts(s_):
    import datetime
    return datetime.datetime.strptime(s_, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()


def prospective_frame_refusal(meta, rec):
    """Why a prospective frame may not be read (Q1 / Q6), or None: it must have been built by EVERY
    registered code file at its registered bytes, on the pooled k, for 2026 only, from frozen rows
    only, after the registration, from a dataset OUTSIDE the registered research/data/nfl_rush/."""
    built = meta.get("code") or {}
    reg = rec.get("code") or {}
    if not built or set(reg) - set(built) or any(built.get(f) != h for f, h in reg.items()):
        return "the prospective frame was not built by the registered code"
    if (meta.get("k") or {}).get("mode") != "pooled" or list(meta.get("seasons") or []) != [2026] \
            or not meta.get("frozen_only"):
        return "the prospective frame is not the registered build (--k pooled --seasons 2026 --frozen-only)"
    if not meta.get("built_utc") or str(meta["built_utc"]) <= str(rec.get("utc") or ""):
        return "the prospective frame was built before the registration"
    return None


def provenance_refusal(meta, rec, model_out, ds_meta, ds_meta_sha, stores_rec, stores_sha, older=None, weeks=None):
    """Why the prospective frame's DATA was not made by the registered bytes, or None (the
    confirmation's C2):
    - the dataset it read is, by realpath, `<records folder>/prospective_dataset` -- never the
      registered research/data/nfl_rush, by any path -- and its meta.json, still hashing to what the
      frame recorded, says every code file that built it is registered (or behaviour-neutral);
    - that meta.json names the store, aux and ids pickles the dataset build read, the 2026 ones
      among them, and every one the frame read too is the frame's: the frame's OUTCOMES are the
      dataset's rows (Opus's F1b);
    - with `weeks` (the window's), the dataset holds every one of them (its 2026 `weeks`: Fable's B1,
      a dataset capped short of the window);
    - the record of the 2026 store and aux (fit.build_stores, run in the registered tree) still hashes
      to what the frame recorded, ran on the registered code under THIS registration, and vouches for
      the 2026 store, aux and ids the frame read;
    - every older store and aux the frame read is the registered test frame's -- by sha256, or, re-saved
      or rebuilt, by CONTENT digest (`older`: {key: why not, or None}, from _provenance)."""
    want_ds = os.path.realpath(os.path.join(model_out, "prospective_dataset"))
    if not meta.get("dataset_realpath") or os.path.realpath(meta["dataset_realpath"]) != want_ds:
        return f"the prospective dataset is not {want_ds} (by realpath)"
    if not ds_meta or not ds_meta_sha or ds_meta_sha != meta.get("dataset_meta_sha256"):
        return "the prospective dataset's meta.json is missing or is not the one the frame read"
    if not ds_meta.get("code") or FIT.unregistered_code(ds_meta["code"], rec):
        return f"the prospective dataset was not built by the registered code ({FIT.unregistered_code(ds_meta.get('code'), rec)})"
    ins = meta.get("inputs") or {}
    dsi = ds_meta.get("inputs") or {}
    lack = [k[5:] for k in VOUCHED if not dsi.get(k)]
    if lack:
        return f"the prospective dataset's meta.json does not record the stores it read ({lack})"
    other = [k[5:] for k in sorted(dsi) if k in ins and dsi[k] != ins[k]]
    if other:
        return f"the prospective dataset read other stores than the frame ({other})"
    if weeks is not None:
        bs = ds_meta.get("by_season") or {}
        held = set(int(w) for w in ((bs.get("2026") or bs.get(2026) or {}).get("weeks") or []))
        short = sorted(set(int(w) for w in weeks) - held)
        if short:
            return f"the prospective dataset holds no rows of window week(s) {short} (built without --all-weeks?)"
    key = f"deep:{FIT.STORES_KEY}"
    if not stores_rec or not stores_sha or stores_sha != ins.get(key):
        return "the 2026 stores' build record is missing or is not the one the frame read"
    if stores_rec.get("prereg_sha256") != rec.get("sha256") or not stores_rec.get("code") \
            or FIT.unregistered_code(stores_rec["code"], rec):
        return "the 2026 store and aux were not built by the registered code under this registration"
    for k in ("deep:nfl_matchup_2026", "deep:nfl_rush_aux_2026", "deep:nfl_matchup_ids"):
        if not ins.get(k) or (stores_rec.get("stores") or {}).get(k) != ins.get(k):
            return f"{k[5:]} is not the store the registered build made"
    reg_in = ((rec.get("frames_meta") or {}).get("test") or {}).get("inputs") or {}
    old = [k for k in ins if k.startswith(("deep:nfl_matchup_20", "deep:nfl_rush_aux_20")) and int(k[-4:]) <= 2025]
    bad = [k for k in old if ins[k] != reg_in.get(k)]
    # a re-saved or rebuilt older pickle (the archive lost) is the registration's when its CONTENT is
    bad = [k for k in bad if (older or {}).get(k, f"{k[5:]} was not checked by content") is not None]
    if bad:
        return f"the stores {bad} are not the registered frames' ({[(older or {}).get(k) for k in bad]})"
    return None


VOUCHED = ("deep:nfl_matchup_2026", "deep:nfl_rush_aux_2026", "deep:nfl_matchup_ids")


def _provenance(meta, rec=None):
    """(dataset meta, its sha256, the 2026 stores' build record, its sha256, older) as they are NOW.
    `older`: for every older store or aux the frame read whose sha256 is not the registered frame's,
    why the file (still the one the frame read) is not the registration's by CONTENT digest, or None
    (fit.store_refusal)."""
    import deep_cache
    from research.nfl_rush_model_frame import input_shas
    ds_meta, ds_sha = None, None
    p = os.path.join(meta.get("dataset_realpath") or "/nonexistent", "meta.json")
    if os.path.exists(p):
        ds_sha = FIT._sha(p)
        with open(p) as fh:
            ds_meta = json.load(fh)
    key = f"deep:{FIT.STORES_KEY}"
    ins = meta.get("inputs") or {}
    reg_in = (((rec or {}).get("frames_meta") or {}).get("test") or {}).get("inputs") or {}
    older = {k: FIT.store_refusal(k, rec or {}, sha=ins[k]) for k in ins
             if k.startswith(("deep:nfl_matchup_20", "deep:nfl_rush_aux_20")) and int(k[-4:]) <= 2025
             and ins[k] != reg_in.get(k)}
    return ds_meta, ds_sha, deep_cache.load(FIT.STORES_KEY)[0], input_shas([key])[key], older


BOX_LAG_S = 48 * 3600        # Q1: Sleeper's box fetched 48+ hours after the window's last game


def schedule(season):
    """{game id: (week, kickoff UTC)} from the season's store: the pbp's recorded start time (the
    store's Eastern `kick`). Pre-game information only."""
    import nfl_matchup_data as D
    from research.nfl_rush_model_frame import kick_ts
    st = D.load_season(season) or {}
    return {str(g["id"]): (int(g["week"]), kick_ts(g)) for g in st.get("games") or []}


def window_weeks(sched, reg_ts, lw):
    """(weeks, last kickoff, why not) for the prospective window, from the season's schedule as the
    store holds it -- PLAYED games only, so it can never say a week is complete by leaving it out (the
    re-check's C-M1). The weeks are EVERY week from the week of the first game kicking off after the
    registration through `lw`, a range; each must hold a game in the store, and week `lw` must be
    CLOSED: the store must hold a game of week `lw` + 1, so every week-`lw` game has kicked off. The
    last kickoff is the last week-`lw` game's."""
    after = [(int(w), k) for w, k in sched.values() if k is not None and k > reg_ts]
    if not after:
        return [], None, "the store holds no game kicking off after the registration"
    first = min(w for w, _k in after)
    weeks = list(range(first, int(lw) + 1))
    if not weeks:
        return [], None, f"no window week between week {first} and week {lw}"
    played = {w for w, _k in after}
    lack = [w for w in weeks if w not in played]
    if lack:
        return weeks, None, f"window week(s) {lack} have not been played in the store"
    if not any(int(w) == int(lw) + 1 for w, _k in sched.values()):
        return weeks, None, f"week {lw} is not closed (the store holds no week-{int(lw) + 1} game yet)"
    return weeks, max(k for w, k in after if w == int(lw)), None


def coverage_refusal(meta, weeks_expected, last_kick, now, missing, frozen_weeks=None):
    """Why the prospective frame does not cover the window (Q1 / Q6), or None: every expected week is
    in its build (--weeks), each has a props-log input or a "capture missing" entry recorded before
    the box was fetched, each has a box file, and the box (and this run) come 48+ hours after the
    window's last game. With `frozen_weeks` (the weeks holding a frozen universe row: pre-game
    information), a week whose props log IS an input but which holds no such row refuses -- the
    dataset or the captures are short of it (Fable's m5); a week recorded "capture missing" may hold
    none."""
    if not weeks_expected:
        return "the window holds no week"
    if last_kick is None:
        return "the window's last kickoff is unknown"
    have = set(int(w) for w in (meta.get("weeks") or []))
    lack = sorted(set(weeks_expected) - have)
    if lack:
        return f"the prospective frame omits window week(s) {lack}"
    names = [os.path.basename(k) for k in (meta.get("inputs") or {})]
    box = {int(b["week"]): b for b in meta.get("box_files") or []}
    first_box = min((_utc_ts(b["mtime_utc"]) for b in box.values()), default=None)
    for w in weeks_expected:
        if w not in box:
            return f"no Sleeper box file for week {w}"
        if _utc_ts(box[w]["mtime_utc"]) < last_kick + BOX_LAG_S:
            return f"week {w}'s box was fetched under 48 hours after the window's last game"
        if not any(n.startswith(f"nfl_props_2026_w{w:02d}") for n in names):
            ent = (missing or {}).get(str(w))
            if not ent or first_box is None or _utc_ts(ent["utc"]) >= first_box:
                return f"week {w} has no props log and no 'capture missing' entry recorded before the box"
        elif frozen_weeks is not None and w not in frozen_weeks:
            return (f"window week {w} holds no frozen universe row though its props log is an input: the dataset "
                    "or the captures are short of the week")
    if now < last_kick + BOX_LAG_S:
        return "the window closed under 48 hours ago"
    return None


def cmd_capture_missing(a):
    """Records, ONCE per week and before the box is fetched, that a window week has no props log in
    the sim-history listing (Q1): the one way `prospective` accepts a week without one."""
    rec = require_registered(False)
    if not FIT.in_registered_tree():
        raise SystemExit("refused: capture-missing runs in the registered tree, as prospective does")
    p = os.path.join(FIT.MODEL_OUT, "capture_missing.json")
    got = {"prereg_sha256": rec.get("sha256"), "weeks": {}}
    if os.path.exists(p):
        with open(p) as fh:
            got = json.load(fh)
        if got.get("prereg_sha256") != rec.get("sha256"):
            raise SystemExit("refused: capture_missing.json belongs to another registration")
    if str(a.week) in got["weeks"]:
        raise SystemExit(f"refused: week {a.week} is already recorded")
    got["weeks"][str(a.week)] = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "note": a.note or ""}
    with open(p, "w") as fh:
        json.dump(got, fh, indent=1)


def cmd_prospective(a):
    rec = require_registered(a.plumb)
    if not a.plumb and not FIT.in_registered_tree():
        raise SystemExit("refused: the prospective test runs from the registered code's stored copies "
                         "(python3 -m research.nfl_rush_model_fit registered-tree --dest DIR; run it there)")
    require_frame(a.frame, "test", rec, a.plumb)
    verify_stores(rec, (2023, 2024, 2025), a.plumb)
    d = out_dir(a.plumb)
    val, val_sha = read_record("validate.json", a.plumb, rec)
    chk, chk_sha = read_record("check2026.json", a.plumb, rec, chain=(("validate_sha256", "validate.json"),))
    cp = os.path.join(d, "coefs.json")
    if not a.force:
        try:
            with open(cp) as fh:
                exp = json.load(fh)
        except (OSError, ValueError):
            exp = None
        if not exp or exp.get("stage") != "provisional" or (exp.get("records") or {}).get("check2026_sha256") != chk_sha:
            raise SystemExit("refused: coefs.json is not this check2026's export (run export first, once)")
    with open(a.frame_prosp + ".meta.json") as fh:
        meta = json.load(fh)
    if not a.plumb:
        why = prospective_frame_refusal(meta, rec)
        if why:
            raise SystemExit(f"refused: {why}")
    Ft = load(a.frame, (2023, 2024, 2025), kind_revised, plumb=a.plumb)
    Fp = load(a.frame_prosp, (2026,), kind_test, plumb=a.plumb)
    # the window's game count reads pre-game information only: a game counts when it holds a row
    # in some market's universe (Sleeper's frozen line) with a frozen capture -- never an outcome
    uni = {r["game_id"] for i, r in enumerate(Fp.rows) if r.get("fz_src") and r["pos"] in WALK
           and any(NR.in_universe(r["pos"], m, Fp.base(i)) for m in WALK[r["pos"]])}
    kicks = {r["game_id"]: _x(r, "kick_ts") for r in Fp.rows if r["game_id"] in uni}
    weeks = {r["game_id"]: int(r["week"]) for r in Fp.rows}
    reg_ts = _reg_ts(rec, a)
    lw, games, note = window_games(kicks, reg_ts, weeks)
    if not a.plumb:
        # the expected weeks are a RANGE from the first window week through lw, each played and lw closed in
        # the store (window_weeks), never the set the store or the frame happens to hold (C-M1 / R3)
        wk_exp, last_kick, why = window_weeks(schedule(2026), reg_ts, lw)
        if why:
            raise SystemExit(f"refused: {why}")
        # the frame's DATA (C2, Opus's F1b, Fable's B1): its dataset (and the weeks it holds) and stores
        why = provenance_refusal(meta, rec, FIT.MODEL_OUT, *_provenance(meta, rec), weeks=wk_exp)
        if why:
            raise SystemExit(f"refused: {why}")
        missing = {}
        mp = os.path.join(FIT.MODEL_OUT, "capture_missing.json")
        if os.path.exists(mp):
            with open(mp) as fh:
                cm = json.load(fh) or {}
            if cm.get("prereg_sha256") != rec.get("sha256"):
                raise SystemExit("refused: capture_missing.json was not recorded under this registration")
            missing = cm.get("weeks") or {}
        why = coverage_refusal(meta, wk_exp, last_kick, time.time(), missing, frozen_weeks={weeks[g] for g in uni})
        if why:
            raise SystemExit(f"refused: {why}")
    sel = rows_window(set(g for g in games if weeks[g] <= lw))
    counts = {f"{p}:{m}": {"rows": len(sel(Fp, p, m)), "games": len({Fp.rows[i]["game_id"] for i in sel(Fp, p, m)})}
              for p in WALK for m in WALK[p]}
    dead = {p: set(v) for p, v in val["dead"].items()}
    parts = parts_for((2023, 2024, 2025), a.plumb, rec)
    disp = dispersion(Ft, (2023, 2024, 2025))
    states, steps = walk_markets(Ft, (2023, 2024, 2025), Fp, sel, parts, disp, dead,
                                 lambda p, m: _accepted_order(chk["provisional"][p].get(m)),
                                 lambda p, m: keep_prospective, "prospective", report_all=True)
    res = dict(run_record(rec, a.plumb), rule="Q6", window_last_week=lw, window_games=len(games), window_note=note,
               counts=counts, kept=states, steps=steps, prospective_frame_sha256=FIT._sha(a.frame_prosp),
               prospective_frame_meta=meta, check2026_sha256=chk_sha, validate_sha256=val_sha,
               baseline="fz (the frozen pre-kickoff capture)")
    lock_sha = write_once(os.path.join(d, "prospective_lock.json"), res, a.force)
    co = build_coefs(Ft, states, "shipped", (2023, 2024, 2025), dead,
                     _records(rec, a.plumb, validate_sha256=val_sha, check2026_sha256=chk_sha,
                              prospective_sha256=lock_sha), a.plumb, rec)
    with open(cp, "w") as fh:          # the second (and last) stage: guarded by the lock's write-once
        json.dump(_clean(co), fh, indent=1)
    log(f"[rule] prospective -> {lock_sha}")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("validate", "check2026", "export", "prospective", "capture-missing"))
    ap.add_argument("--frame", help="the registered test frame (pooled k, 2023-2026)")
    ap.add_argument("--week", type=int, help="capture-missing: the window week with no props log")
    ap.add_argument("--note", help="capture-missing: what the sim-history listing showed")
    ap.add_argument("--frame-val", help="the registered validation frame (k_fit_only, 2023-2025)")
    ap.add_argument("--frame-prosp", help="the prospective frame, built after the window by the registered code")
    ap.add_argument("--plumb", action="store_true", help="held outcomes replaced by random 2023 rows (never read)")
    ap.add_argument("--plumb-reg-ts", type=float, default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd != "capture-missing" and not a.frame:
        ap.error("--frame is required")
    {"validate": cmd_validate, "check2026": cmd_check2026, "export": cmd_export, "prospective": cmd_prospective,
     "capture-missing": cmd_capture_missing}[a.cmd](a)


if __name__ == "__main__":
    main(sys.argv[1:])
