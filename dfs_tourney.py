"""The DFS tournament engine: every legal lineup, every simulated world, one
weighted field, ranked by how often each lineup actually wins.

The lineup builders answer "which lineup has the best distribution?" with a
summary of that lineup's own totals. A top-heavy contest asks a different
question: in how many of the possible games does THIS lineup finish first
(or top 1%) against the people who entered? That is what this module
counts, and it counts it without shortcuts:

  1. the game is simulated N times (the per-player point arrays the sims
     already produce, one column per simulated game);
  2. EVERY legal lineup is enumerated -- a showdown pool of twenty players
     is a few hundred thousand lineups, and this is a matrix multiply, not
     a search;
  3. the field is a probability over those same lineups -- a softmax over
     projected lineup points, sharpened until the single most popular
     lineup holds FIELD_TOP_SHARE of the field -- so an entry's rank in a
     world is the field mass scoring above it, and a lineup's duplication
     risk is its own mass. The players the depth gate keeps us off (a WR4,
     a third back) stay in the FIELD's pool: the public plays them, and a
     world where one of them booms is a world we lose;
  4. per world, every lineup's win / top-1% / cash chance and payout are
     read off that mass (first place exactly, the payout curve through a
     normal rank approximation), and the tallies over worlds are the
     answer -- plus which lineup was the hindsight-optimal one in each
     world, tallied per lineup and per player;
  5. a portfolio of K entries is chosen greedily to cover the most worlds:
     the next entry is the one that adds the most top-1% probability in
     the worlds the entries so far do not already cover.

Needs numpy. Built for the owner's PC (pc_worker ships the result as a
board the tab serves); the server never runs this -- 200,000 lineups
across 60,000 worlds is a desktop's afternoon, not a one-core web
worker's request. Owner, 2026-09-09: "We can make this as cpu intensive as
possible ... I want like Skyrim at full max render distance."
"""

import itertools
import math
import time

import errlog

try:
    import numpy as np
except ImportError:                      # the server: no numpy on purpose
    np = None

VERSION = 3                              # 3: every leaf a plain Python value (no numpy)
# What the board's numbers MEAN, as opposed to how they are stored (VERSION):
# bumped when the rules, the field or the evaluation change, so pc_worker
# rebuilds a board that was computed under the old semantics instead of
# re-saving it. 2: opponents parsed from the live feed (the defense rule
# and the field's bring-backs active for the first time), the per-game cap
# an explicit knob, candidates scored on worlds they were not found in.
# 3: the field sampler reserves the salary of a defense the row may still
# take, so no draw dies at the last slot (the drops were cheap-QB, spend-out
# builds, 0.37% of the field).
# 4: the sampler's completion is stamped and a short field fails the build;
# fixed probe lineups ride on every board as diagnostic passengers; the
# simulator's model, version and constants are stamped.
# 5: ties are paid the way DraftKings pays them (the mass LEVEL with a
# lineup, not only the mass above it), so win_pct means first OUTRIGHT and
# the payout splits every tied position; the count of entries above is
# Poisson where its mean is small; score buckets are a hundredth of a point;
# the money columns' experimental label is built by money_gate; the board
# records which simulator model made its worlds.
ENGINE = 6
# Showdown carries its OWN semantic version. The two engines change for
# different reasons -- a classic field-model fix has nothing to say about a
# single-game board -- and each build is expensive enough (an hour for
# classic, twenty minutes for showdown) that sharing one number would spend
# the PC's evening rebuilding boards whose meaning did not move. Bump this
# when a showdown board's NUMBERS mean something new.
#
# 1: the first exhaustive showdown boards (through 2026-09-11) -- no engine
#    stamp at all, so they read as 0 and rebuild.
# 2: engine stamped, a salary- and status-sensitive pool signature, and
#    pre-lock refreshes.
# 3: the worlds are scored on the DraftKings lattice. Every player score in a
#    showdown pool -- offense, kicker and defense -- now recomputes exactly
#    from an integer stat line through DK's own scorer, so a captain's 1.5x
#    lands on the 0.01 bucket the tie arithmetic counts on instead of half a
#    hundredth away from it. The numbers mean something different: expected
#    payout falls 7-14% on the live boards (against 0.03% when the same board
#    is merely reseeded), because field mass that used to miss a score now
#    lands level with it. Strategy did NOT move -- the top twenty reorders
#    less than two legacy runs do. nfl_dfs_sim.SD_DISCRETE, engine 3.
SD_ENGINE = 3


def available():
    return np is not None


def plain(obj):
    """`obj` with every numpy leaf replaced by its Python value (scalars to
    float/int/bool, arrays to lists), recursively; dicts, lists and tuples keep
    their shape. Every artifact leaves through here. The server has no numpy
    on purpose, so ONE numpy scalar anywhere in the pickle makes the whole
    board unreadable there: the first classic build (2026-09-10, 72 minutes)
    carried np.float64 in the ev/roi_pct of every row (round() keeps the
    numpy type, and a json.dump check hides it because np.float64 subclasses
    float); the server ledgered BOARD-read x41, the tab said "not built yet"
    and the queued request looked like it never fired."""
    if isinstance(obj, dict):
        return {plain(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [plain(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(plain(v) for v in obj)
    if np is not None:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.generic):
            return obj.item()
    return obj


# ---- lineups ---------------------------------------------------------------
def enumerate_showdown(players, cap, cpt_mult=1.5, min_teams=2, entry_ok=None,
                       cpt_ok=None):
    """Every legal 1 CPT + 5 FLEX lineup: (idx (L, 6) int32 with the captain
    first, weights (L, P) float32 with cpt_mult on the captain, allowed (L,)
    bool -- which lineups WE may enter (entry_ok(cap_p, picked) -> bool),
    the field may hold any of them). Legal = under the cap, both teams."""
    P = len(players)
    sal = [int(p["salary"]) for p in players]
    csal = [int(p["cpt_salary"]) for p in players]
    team = [p.get("team") or "" for p in players]
    rows, allowed = [], []
    for c in range(P):
        if cpt_ok is not None and not cpt_ok(players[c]):
            cap_allowed = False
        else:
            cap_allowed = True
        budget = cap - csal[c]
        if budget < 0:
            continue
        others = [i for i in range(P) if i != c and sal[i] <= budget]
        for combo in itertools.combinations(others, 5):
            s = sal[combo[0]] + sal[combo[1]] + sal[combo[2]] + sal[combo[3]] + sal[combo[4]]
            if s > budget:
                continue
            if len({team[c], team[combo[0]], team[combo[1]], team[combo[2]],
                    team[combo[3]], team[combo[4]]} - {""}) < min_teams:
                continue
            rows.append((c,) + combo)
            ok = cap_allowed
            if ok and entry_ok is not None:
                ok = entry_ok(players[c], [players[i] for i in combo])
            allowed.append(ok)
    if not rows:
        return None, None, None
    idx = np.asarray(rows, dtype=np.int32)
    W = np.zeros((len(rows), P), dtype=np.float32)
    ar = np.arange(len(rows))
    W[ar, idx[:, 0]] = cpt_mult
    for k in range(1, 6):
        W[ar, idx[:, k]] = 1.0
    return idx, W, np.asarray(allowed, dtype=bool)


FIELD_TOP_SHARE = 0.002
# How concentrated the public is. The one hard number: DraftKings' 2021-09-20
# GB-DET showdown Millionaire paid 231 identical winning lineups (about 0.1%
# of that field), and the winner is rarely the MOST popular build, so the
# most popular one is taken as 0.2% -- roughly 260 copies in a 132,000-entry
# contest. A placeholder until the app can grade a real results file: the v0
# field (a product of the app's ownership guesses, nine players pinned at a
# 45% cap) was so flat that the best lineup showed a 25% top-1% chance and a
# +10,000% ROI against a 132,000-entry field. Measured 2026-09-09.


def field_weights(players, idx, cpt_mult=1.5, top_share=FIELD_TOP_SHARE):
    """The field as a probability over lineups: softmax(beta * projected
    points), beta found by bisection so the most popular lineup holds
    `top_share` of the field. Returns (f, beta). One knob, and it is the
    knob that decides duplication and the mass ahead of a chalk build."""
    proj = np.asarray([float(p.get("proj") or 0.0) for p in players], dtype=np.float64)
    mu = proj[idx[:, 0]] * cpt_mult + proj[idx[:, 1:]].sum(axis=1)
    mu = mu - mu.max()                      # the top lineup's weight is exp(0) = 1
    lo, hi = 0.0, 12.0
    for _ in range(60):
        beta = 0.5 * (lo + hi)
        share = 1.0 / np.exp(beta * mu).sum()
        if share < top_share:
            lo = beta
        else:
            hi = beta
    beta = 0.5 * (lo + hi)
    f = np.exp(beta * mu)
    return f / f.sum(), float(beta)


def field_ownership(players, idx, f):
    """What the field model implies per player: (captain %, flex %)."""
    P = len(players)
    oc = np.bincount(idx[:, 0], weights=f, minlength=P)
    of = np.zeros(P, dtype=np.float64)
    for k in range(1, idx.shape[1]):
        of += np.bincount(idx[:, k], weights=f, minlength=P)
    return 100.0 * oc, 100.0 * of


# ---- the payout curve as a function of the field mass above you -----------
def _ncdf_arr(x):
    # numpy has no erf; a rational approximation good to ~1e-7 (Abramowitz
    # and Stegun 7.1.26), vectorized.
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-z * z)
    return np.where(x >= 0, 0.5 * (1.0 + y), 0.5 * (1.0 - y))


def _npdf_arr(x):
    return np.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_loss(a):
    """E[(Z - a)+] for a standard normal Z, the integral of the upper tail:
    phi(a) - a(1 - Phi(a)). Used to average a prize schedule over a band of
    finishing positions in closed form."""
    return _npdf_arr(a) - a * (1.0 - _ncdf_arr(a))


def cum_prize(payouts, n):
    """Prize money paid to positions 1..n in total. `n` may be an array and
    may be fractional -- a position part-way through a bracket is prorated,
    which is what keeps the average over a band of positions continuous."""
    n = np.asarray(n, dtype=np.float64)
    out = np.zeros(n.shape, dtype=np.float64)
    for row in payouts or []:
        lo, hi, p = int(row["from"]), int(row["to"]), float(row["prize"])
        if p <= 0 or hi < lo:
            continue
        out += p * np.clip(n - (lo - 1), 0.0, float(hi - lo + 1))
    return out


def tie_payout(payouts, above, tied):
    """What DraftKings pays ONE entry that has `above` entries strictly ahead
    of it and `tied` other entries on its exact score.

    DK does not break ties: the tied entries occupy positions above+1 through
    above+1+tied and split the prizes for those positions equally. So a
    two-way tie for first in a contest paying 1,000,000 and 500,000 pays each
    750,000, and a tie that straddles the cash line pays everyone in it the
    average -- including the entries that would have missed the money."""
    above = np.asarray(above, dtype=np.float64)
    tied = np.asarray(tied, dtype=np.float64)
    return (cum_prize(payouts, above + 1.0 + tied) - cum_prize(payouts, above)) / (tied + 1.0)


def _band_expect(rows, m, s, x):
    """E[ cum_prize(R + x) ] for the finishing rank R ~ Normal(m, s), with the
    prize schedule prorated inside a bracket. Closed form: each bracket
    contributes p * E[clip(R + x - (lo-1), 0, width)], and E[clip(Z, 0, W)]
    for a normal Z is s times the difference of two normal loss functions."""
    out = np.zeros(np.shape(m), dtype=np.float64)
    for lo, hi, p in rows:
        w = float(hi - lo + 1)
        mu = m + x - (lo - 1)
        out += p * s * (_norm_loss(-mu / s) - _norm_loss((w - mu) / s))
    return out


TIE_T_MAX = 128         # tie counts carried one by one; beyond this the count's
TIE_LAM_MAX = 64.0      # own spread (sqrt(lambda)) is small beside the band
TIE_E_MAX = 0.05        # tie masses above this are read as this (the prize
                        # schedule is flat wherever a twentieth of the field
                        # shares a score, so the cap costs nothing)
# How many entries finish above us is Binomial(C-1, F). Where that mean is
# small -- which is exactly where the money is, one entry in 832,000 -- the
# normal approximation is badly wrong: at C=5,000 and F=1e-4 it valued a
# 1,000,000 top prize at 825,000 against a true 767,000, 7.6% high, because
# a Binomial with mean 0.5 is nothing like a bell curve. Below this mean the
# count is taken as Poisson term by term instead (exact to the third figure
# against a direct multinomial draw), with lambda chosen so that P(nobody
# above) is (1-F)^(C-1) exactly; above it the normal is used, and there the
# prize schedule is flat enough that it costs nothing.
POIS_LAM_MAX = 60.0
POIS_K_MAX = 260


# Log factorials, built on first use. At module level this was a numpy call,
# and this module has to import where numpy does not exist: the guards run in
# CI installs none, so the suite died on its own import and the commit went
# red. (It did NOT take down a route -- nothing the web app serves imports
# this module; that overstatement is corrected in limitation 9 of the audit
# report.) Nothing at module scope may touch np.
_LOG_FACT = None


def _log_fact(n=POIS_K_MAX + 1):
    global _LOG_FACT
    if _LOG_FACT is None or len(_LOG_FACT) < n + 1:
        _LOG_FACT = np.cumsum(np.concatenate([[0.0], np.log(np.arange(1, n + 1, dtype=np.float64))]))
    return _LOG_FACT


def _pois_cdf_table(lam, kmax=POIS_K_MAX):
    """(len(lam), kmax+1) Poisson CDF, built from the log pmf."""
    lam = np.asarray(lam, dtype=np.float64)[:, None]
    k = np.arange(0, kmax + 1, dtype=np.float64)[None, :]
    logpmf = -lam + k * np.log(np.maximum(lam, 1e-300)) - _log_fact()[None, : kmax + 1]
    return np.clip(np.cumsum(np.exp(logpmf), axis=1), 0.0, 1.0)


def _tie_nodes(lam):
    """Quadrature over T, the number of OTHER entries on our exact score:
    T ~ Poisson(lambda) term by term while lambda is small, and lambda
    itself when it is not -- a tie group of hundreds spans a stretch of the
    prize schedule so flat that the group's own size fluctuation cannot
    matter."""
    if lam <= 0:
        return np.array([0.0]), np.array([1.0])
    if lam > TIE_LAM_MAX:
        return np.array([float(lam)]), np.array([1.0])
    t = np.arange(0, TIE_T_MAX + 1, dtype=np.float64)
    w = np.exp(-lam + t * math.log(lam) - _log_fact()[: TIE_T_MAX + 1])
    return t, w / w.sum()


def payout_grid(C, payouts, entry_fee, places, n=6000, n_tie=160):
    """The payout curves as a function of F = the field mass scoring strictly
    ABOVE an entry and E = the field mass scoring EXACTLY LEVEL with it, both
    on grids dense near zero.

    F only: P(first or a share of it), (1-F)^(C-1) exactly; P(top 1%),
    P(top 0.1%), P(cash). Those three are right under ties as they stand,
    because a tie that straddles a line still pays everyone in it and the
    rank is the number strictly above plus one.

    F and E: P(sole first), (1-F-E)^(C-1), and the expected payout, which
    ties change a great deal. One sampled field lineup on our score in a
    300,000-lineup sample stands for 2.8 entries of an 832,000 contest, so
    the top prize splits about four ways; assuming no tie overstates
    first-place money by that factor. DraftKings does not break ties: the
    tied entries take positions above+1 .. above+1+tied and split those
    positions' prizes equally (tie_payout), so the payout here is that rule
    averaged over the distribution of both counts."""
    C = max(2, int(C))
    F = np.concatenate([np.array([0.0]), np.logspace(-9, 0, n - 1)])
    F = np.minimum(F, 1.0)
    E = np.concatenate([np.array([0.0]), np.logspace(-9, math.log10(TIE_E_MAX), n_tie - 1)])
    m = 1.0 + (C - 1) * F
    s = np.sqrt(np.maximum(1e-9, (C - 1) * F * (1.0 - F)))
    first = np.power(1.0 - F, C - 1)
    # lambda from the exact P(nobody above), so the Poisson branch keeps the
    # one probability the whole top of the schedule turns on
    lamA = -(C - 1) * np.log(np.maximum(1.0 - F, 1e-300))
    pois = lamA <= POIS_LAM_MAX
    cdfA = _pois_cdf_table(lamA[pois]) if pois.any() else np.zeros((0, POIS_K_MAX + 1))

    def p_at_most(k):
        """P(A <= k) on the whole F grid: Poisson where the mean is small,
        the normal approximation of the rank where it is not."""
        out = _ncdf_arr((k + 1.5 - m) / s)
        if pois.any():
            kk = np.clip(np.asarray(k, dtype=np.int64), -1, POIS_K_MAX)
            head = np.where(kk < 0, 0.0, cdfA[np.arange(cdfA.shape[0]), np.maximum(kk, 0)])
            if np.ndim(k) == 0 and k > POIS_K_MAX:
                head = np.ones(cdfA.shape[0])
            out = out.copy()
            out[pois] = head
        return out

    top1 = p_at_most(max(1, int(0.01 * C)) - 1)
    # top 0.1%: in an 832,000-entry Millionaire first place is too rare to
    # rank on and the top 1% is 8,300 places; the top 832 is the money.
    top01 = p_at_most(max(1, int(0.001 * C)) - 1)
    cash = p_at_most(int(places) - 1)
    rows = [(int(r["from"]), int(r["to"]), float(r["prize"]))
            for r in (payouts or []) if float(r["prize"]) > 0 and int(r["to"]) >= int(r["from"])]

    def prize_at_shift(i):
        """E[prize paid for finishing position A + 1 + i]."""
        out = np.zeros(n, dtype=np.float64)
        for lo, hi, p in rows:
            out += p * (p_at_most(hi - 1 - i) - p_at_most(lo - 2 - i))
        return out

    R = np.stack([prize_at_shift(i) for i in range(TIE_T_MAX + 1)])          # (T+1, n)
    S = np.cumsum(R, axis=0)
    B = S / (np.arange(1, TIE_T_MAX + 2, dtype=np.float64)[:, None])         # band average per tie count
    base = _band_expect(rows, m, s, -1.0)
    ev2 = np.empty((n, n_tie), dtype=np.float64)
    win_sole = np.empty((n, n_tie), dtype=np.float64)
    share1 = np.empty((n, n_tie), dtype=np.float64)
    prize1 = float(cum_prize(payouts, np.array([1.0]))[0])
    for j, e in enumerate(E):
        lam = (C - 1) * e
        if lam > TIE_LAM_MAX:
            # a tie group of tens or hundreds: the closed-form band average,
            # which needs no term-by-term count. (Reading the deterministic
            # node as an index into the term-by-term table instead would have
            # read tie count zero and handed back the no-tie payout: caught
            # by the guard that the payout never rises with the tie mass.)
            ev2[:, j] = (_band_expect(rows, m, s, lam) - base) / (lam + 1.0)
        else:
            tj, wj = _tie_nodes(lam)
            ev2[:, j] = wj @ B[: len(tj)]
        # given nobody is above us, each of the others ties us with
        # probability e/(1-F); E[1/(T+1)] for a Poisson count is
        # (1 - exp(-lambda))/lambda
        lam0 = np.minimum((C - 1) * e / np.maximum(1.0 - F, 1e-12), 1e6)
        g0 = np.where(lam0 > 1e-9, (1.0 - np.exp(-lam0)) / np.maximum(lam0, 1e-12), 1.0)
        share1[:, j] = first * g0
        win_sole[:, j] = np.power(np.clip(1.0 - F - e, 0.0, 1.0), C - 1)
    # More entries level with us can only lower the payout: the band widens
    # into smaller prizes and is divided among more people. The two branches
    # (term by term, then the closed-form band) meet with a step of about 8%
    # where they cross, at a tie group of 64 -- by then the payout is a
    # thousandth of the top prize, so the step is worth a hundred dollars of
    # a million-dollar schedule. Clamped to the shape the mathematics
    # guarantees, and the largest clamp is reported rather than hidden.
    clamp = float(np.max(np.diff(ev2, axis=1), initial=0.0))
    ev2 = np.minimum.accumulate(ev2, axis=1)
    return {"F": F, "E": E, "first": first, "top1": top1, "top01": top01, "cash": cash,
            "ev": ev2[:, 0], "ev2": ev2, "win_sole": win_sole, "first_share": share1,
            "monotone_clamp": clamp,
            "C": C, "places": places, "entry_fee": float(entry_fee), "prize1": prize1}


# The curves a world reads by the field mass ABOVE the entry alone, and the
# two it reads by that mass together with the mass LEVEL with it.
# ---- Stage 3D: when may the money columns be published as fact? ----------
# Three links have to hold before a first-place probability, an expected
# payout or an ROI is worth reading as a number rather than as a ranking.
#
#   1. RESOLUTION. The field is a SAMPLE. With M sampled lineups the
#      smallest mass it can report above an entry is 1/M, while the contest
#      asks about 1/C. At M = 300,000 against C = 832,000 one sampled
#      lineup stands for 2.8 entries, so the first-place column is a count
#      of clean sweeps of the sample, and its LEVEL is a property of the
#      sample size. Measured in Stage 3A: the truth is below the sample's
#      resolution in most (candidate, world) pairs at the top.
#   2. TIES. DraftKings splits the tied positions' prizes. Stage 3B pays
#      that rule (tie_payout) instead of assuming the entry stands alone.
#      This link holds.
#   3. DUPLICATES. How many entries are our exact lineup is read off the
#      same sample's collisions -- zero, one or two lineups at the top.
#      Stage 3C measured what it converges to on a field ten times larger
#      and could not replace the estimator with an exact one: the sampler
#      has no closed-form density (the salary reserve makes a pick depend
#      on the money already spent), so there is no dynamic programme and
#      no importance weight. This link is a measurement, not a proof.
#
# Link 1 fails by arithmetic on every board this engine builds, so the
# money columns stay experimental and the board says why. Raising the
# field to the contest's size would fix the arithmetic; it would not fix
# the field MODEL, which is a guess at what the public builds.
MONEY_TIES_PAID = True                   # Stage 3B
MONEY_DUP_EXACT = False                  # Stage 3C: measured, not derived


def money_gate(field_n, contest_C):
    """Whether the win, payout and ROI columns may be published as
    authoritative. Returns the block a board carries."""
    per_lineup = float(contest_C) / max(1, int(field_n))
    resolves = per_lineup <= 1.0
    ok = bool(resolves and MONEY_TIES_PAID and MONEY_DUP_EXACT)
    why = ("the first-place estimate rests on the field sample's extreme tail: with "
           f"{int(field_n):,} sampled lineups against {int(contest_C):,} entries, one sampled lineup "
           f"stands for {per_lineup:.1f} entries and the smallest mass the sample can report is "
           f"1 in {int(field_n):,}. Ties are paid the way DraftKings pays them (Stage 3B). The "
           "duplicate count is still measured rather than derived: an exact path-summed "
           "construction probability now exists for a fixed lineup (dfs_pathprob), but against "
           "three million draws it runs about 5% light at the median with one lineup 2.8 standard "
           "errors low, so the undercount is neither resolved nor bounded and this link stays shut. "
           "Resolution and duplication are what keep these columns experimental. Rank on top 1% "
           "and top 0.1%.")
    return {"columns": ["win_pct", "win_any_pct", "ev", "ev_notie", "roi_pct"],
            "authoritative": ok,
            "links": {"sample_resolves_the_contest": bool(resolves),
                      "ties_paid_as_the_house_pays_them": bool(MONEY_TIES_PAID),
                      "duplicates_exact": bool(MONEY_DUP_EXACT)},
            "entries_per_sampled_lineup": round(per_lineup, 2),
            "why": why}


# Showdown's public field is EXHAUSTIVE, not sampled: every legal lineup is
# enumerated and carries an explicit weight, so the resolution problem that
# keeps classic's money columns shut does not exist here. What does exist is
# worse for the same columns -- those weights come from one uncalibrated knob.
SD_MONEY_FIELD_CALIBRATED = False
# FIELD_TOP_SHARE is set from a single published contest (the 2021 GB-DET
# Millionaire's 231-way tie) and has never been fitted against real showdown
# ownership: not captain rates, not flex rates, not 5-1/4-2/3-3 shares, not
# the duplication distribution. Flip this only when a held-out comparison
# against historical contests supports it, and never because a board looks
# sensible.
SD_MONEY_TIES_PAID = True       # the same tie-aware payout classic uses
# Measured on the live pool, 1,952,000 simulated scores: 48.7% of them cannot
# occur under DraftKings scoring at all (49.8% of quarterbacks, 50.1% of
# receivers, 48.3% of kickers, 40.6% of defenses; the residual is exactly half
# a lattice step, so they sit on odd hundredths). The legacy simulator pins
# each player's mean by MULTIPLYING his whole point array by proj/raw, shifts
# a defense by a floating amount, and rounds to two decimals -- so the scores
# land on a 0.01 grid, and DraftKings' offensive scoring lives on 0.02
# (0.04/passing yard, 0.1/rushing and receiving yard, whole receptions and
# touchdowns, whole bonuses).
#
# That matters here more than anywhere else in the app: showdown's first-place
# column is a SOLE-win probability, which is decided by how often scores tie,
# and a score grid finer than the legal one biases exact-tie probability
# DOWNWARD. Measured on the same game rebuilt with integer components
# (research/sd_discrete, 20,000 worlds): the exact-tie rate between two player
# scores goes up 6.1x, and between two six-player LINEUP TOTALS 2.36x, which is
# the one a split payout turns on. Both had to be measured; the grid spacing
# implies neither.
#
# CLOSED, 2026-09-12, and the severity is smaller than this comment used to
# imply. The fix was neither of the two options listed here before. The legacy
# simulator already drew components and already scored them with DK's own
# scorer, so only the yards and catches were continuous and the real damage was
# a multiply afterwards; nfl_dfs_sim.SD_DISCRETE draws them whole and pins the
# mean upstream. Showdown serves it, Classic does not, and the Classic path is
# byte-identical on a seeded digest of the whole pool. Validated end to end in
# research/sd_support: 26 of 26 offensive scores recompute from their own
# integer stat lines to 7.1e-15, every kicker and defense score is a whole
# number, and the engine's 0.01 bucket reproduces the exact six-player total for
# every sampled lineup in every world.
#
# What the LIVE boards said about severity -- and, corrected 2026-09-12 after an
# independent audit read the artifact instead of this comment, what they did NOT
# say. The numbers below were always right; an earlier version of this comment
# attached the wrong EVENT to two of them.
#
# research/sd_live_ab measures ONE tie statistic: for each build's own top-200
# lineups by top-1% rank, the mean probability mass sitting on a SHARED first
# place, win_any - win_sole. That candidate-level mean moved x1.07 (DEN@KC) and
# x0.94 (DAL@NYG) -- opposite directions, inside the noise of merely reseeding
# the same board, which moved it x1.04. That is the whole of what the ratio
# says, and it says it about 200 hand-picked lineups, not about the contest.
#
# It is NOT the probability that the contest's first place is shared. Nothing in
# this tree measures that: it needs an estimator over the whole 88,000-entry
# field, and none exists. This comment used to report 84.2% and 96.8% as that
# incidence. Those two values are real but they are the LEGACY BEST LINEUP's
# split_haircut_pct = 100 * (1 - ev / ev_notie) -- the share of its untied
# expected payout that tie-splitting removes. A payout haircut is not an
# incidence, and the substitution was not harmless: it was the whole argument
# for "the field already ties, so the lattice was cheap."
#
# The money evidence runs the other way. Between the two modes the best lineup's
# tie-aware expected payout moved -7.4% and -14.0%, against -0.03% for reseeding
# the same board. The tie correction landed in the payout column, not in the
# candidate shared-first one, so NO claim that the lattice was economically
# negligible survives -- under this field model it looks expensive. What does
# survive is the strategy result (top-20 overlap 17 and 19 of 20), which is why
# the promotion was argued on correctness and never on money.
#
# This link closing does NOT open the gate. Both of the remaining open links
# outrank it: tie-splitting removing 84-97% of the best lineup's untied payout
# is itself an output of the uncalibrated field model, so that model drives
# payout economics here more than the scoring grid ever did, and a legal
# football support says nothing about whether the public is modelled correctly.
SD_MONEY_LATTICE_OK = True
# And the link that was missing from this gate entirely, which closing the
# lattice one exposed. A legal support is a statement about the SCORING rules,
# not about the football. Showdown serves legacy-latent, whose joint structure
# has known, measured limitations -- teammate covariance and opposing-side /
# game covariance are exactly what Stage 2B-2F found it getting wrong, and a
# showdown lineup is MORE exposed to them than a classic one because all six
# roster spots come out of a single game's covariance.
#
# Without this link, calibrating public ownership alone would flip the gate to
# authoritative and the app would announce authoritative EV while the
# game-outcome joint distribution remained a known research limitation. That
# would be too strong a claim, so the gate requires this too. Nothing in this
# pass validated the legacy correlation model, and promoting the discrete
# scorer explicitly did not: it is a more correct IMPLEMENTATION of that model,
# not evidence for it.
# WHEN THIS EVENTUALLY FLIPS, it must not flip as a timeless boolean. A
# validation is always OF something: a simulator name and version, a data split,
# a date. Validate model-v2, later serve model-v3, and a bare True silently
# inherits the old evidence -- which is the same class of mistake as a board that
# could not be told its engine had moved. The same applies to
# SD_MONEY_FIELD_CALIBRATED: "calibrated" is only meaningful against the
# ownership data and season it was fitted on. Both belong in the S7/S9
# provenance work as (model, version, split) records that the gate compares
# against nfl_dfs_sim.sim_stamp(), not as flags. Recorded here so the next pass
# cannot miss it; deliberately NOT built now, because guessing at that shape
# before S7 has any calibration data would be inventing a contract.
SD_MONEY_JOINT_MODEL_OK = False



def sd_money_gate(n_legal, contest_C, field_calibrated=None, joint_validated=None):
    """Whether a showdown board's money columns may be published as
    authoritative. Same shape as `money_gate` so the tab can read either.

    The links differ from classic on purpose. Showdown enumerates the whole
    lineup universe, so nothing is lost to sampling and expected copies are
    exact GIVEN the weights -- which is precisely the problem, because the
    weights are a placeholder. Duplication and first place are therefore only
    as good as an uncalibrated field model, and that link is the one that
    keeps the gate shut."""
    cal = SD_MONEY_FIELD_CALIBRATED if field_calibrated is None else bool(field_calibrated)
    joint = SD_MONEY_JOINT_MODEL_OK if joint_validated is None else bool(joint_validated)
    exhaustive = int(n_legal) > 0
    ok = bool(cal and exhaustive and SD_MONEY_TIES_PAID and SD_MONEY_LATTICE_OK and joint)
    why = ("the whole lineup universe is enumerated -- "
           f"{int(n_legal):,} legal lineups against {int(contest_C):,} entries -- so unlike the "
           "classic board nothing here is lost to sampling, and expected copies are exact given "
           "the field's weights. The weights are the larger of the two open problems: "
           "the public is modelled as softmax(beta x projected points) with beta solved so the "
           f"most popular build holds {100 * FIELD_TOP_SHARE:.1f}% of the field, a figure taken "
           "from ONE published contest and never fitted to real showdown ownership -- not captain "
           "rates, not flex rates, not team structures, not the duplication distribution. First "
           "place, payout and ROI inherit that uncertainty whole, and this board shows how much "
           "that matters: under that model tie-splitting removes 84-97% of the BEST LINEUP's "
           "untied expected payout on a live primetime slate, so the field model drives the "
           "payout economics here more than anything else does. That 84-97% is a payout haircut, "
           "NOT the chance first place is shared -- contest-wide shared-first incidence is not "
           "measured anywhere in this tree. The lattice link, which used to be open beside it, "
           "is closed: every score in a showdown pool now recomputes from an integer stat line "
           "through DraftKings' own scorer, kickers and defenses included (research/sd_support). "
           "That was fixed because it was WRONG; whether it was also costly is unresolved. "
           "Across each build's top-200 candidate lineups the mean shared-first mass moved "
           "x1.07 and x0.94, in opposite directions, inside the noise of reseeding -- while the "
           "best lineup's expected payout moved -7.4% and -14.0% against -0.03% for a reseed. "
           "And fixing the SCORING rules is not validating the FOOTBALL: "
           "showdown serves legacy-latent, whose teammate and opposing-side covariance are the "
           "known limitations Stage 2B-2F measured, and a six-man single-game roster is more "
           "exposed to them than a classic lineup is. That is a separate link and it is also "
           "open, so calibrating ownership alone could never make these columns authoritative. "
           "Rank on top 1% and top 0.1%.")
    return {"columns": ["win_pct", "win_any_pct", "ev", "ev_notie", "roi_pct", "expected_copies"],
            "authoritative": ok,
            "links": {"lineup_universe_exhaustive": bool(exhaustive),
                      "showdown_joint_model_validated": bool(joint),
                      "ties_paid_as_the_house_pays_them": bool(SD_MONEY_TIES_PAID),
                      "scores_on_the_dk_lattice": bool(SD_MONEY_LATTICE_OK),
                      "field_model_calibrated": bool(cal)},
            "field_model": {"kind": "softmax over projected points", "calibrated": bool(cal),
                            "top_share_pct": round(100.0 * FIELD_TOP_SHARE, 3),
                            "source": "one published contest (2021-09-20 GB-DET Millionaire)"},
            "legal_lineups": int(n_legal),
            "why": why}


_GRID_COLS = ("first", "top1", "top01", "cash", "ev")
_TIE_COLS = ("win_sole", "ev2")


def _grid_table(grids):
    """Every contest's above-only curves side by side, (n_grid, 5 x contests),
    so a world costs one gather however many contests share the slate."""
    return np.concatenate([np.stack([g[c] for c in _GRID_COLS], axis=1) for g in grids], axis=1)


def _tie_table(grids):
    """The tie-aware curves flattened to one row per (above, level) pair:
    (n_grid x n_tie, 2 x contests), float32 to keep a six-contest slate near
    30 MB. A world gathers this by the same trick, one index per bucket."""
    n, nE = grids[0]["ev2"].shape
    cols = []
    for g in grids:
        cols.append(np.stack([g["win_sole"].reshape(-1), g["ev2"].reshape(-1)], axis=1))
    return np.concatenate(cols, axis=1).astype(np.float32), nE


# ---- the tournament ---------------------------------------------------------
# Score buckets of 0.01 DK points. They were 0.1 while the only question was
# how much field finished ABOVE a lineup; once ties are paid by splitting
# (Stage 3B) a bucket has to mean "the same score", and DraftKings scores
# land on a fine lattice (0.04 a passing yard, 0.1 a receiving yard), so a
# tenth of a point fused genuinely different scores into a tie. Measured
# cost of the finer bucket: 1.1 ms a world on a 300,000 field, about 45
# seconds of a 45-minute build.
_RES = 100.0
_MAXPTS = 400.0
_NB = int(_MAXPTS * _RES) + 1
_CHUNK_ELEMS = 1.5e8  # scores per chunk: (c x L) float32 + int32 ~ 1.2 GB


def chunk_for(L, chunk):
    """Worlds per chunk that keep the (c x L) score and bucket matrices
    inside _CHUNK_ELEMS -- a 225,000-lineup pool takes 666 worlds a chunk,
    a 1.2-million one 125."""
    return max(16, min(int(chunk), int(_CHUNK_ELEMS // max(1, L))))


def _buckets(St):
    """Scores (c, L) float32 -> 0.01-point buckets, in place (St is spent)."""
    np.multiply(St, _RES, out=St)
    St += 0.5
    np.clip(St, 0, _NB - 1, out=St)
    return St.astype(np.int32)


def _log_index(grid_log, v):
    """Nearest grid point in LOG space (the tie grid is geometric, so the
    upper neighbour alone would read a mass up to 10% high)."""
    lv = np.log(np.maximum(v, 1e-300))
    i = np.searchsorted(grid_log, lv)
    i = np.minimum(i, len(grid_log) - 1)
    lo = np.maximum(i - 1, 0)
    take_lo = np.abs(grid_log[lo] - lv) < np.abs(grid_log[i] - lv)
    return np.where(take_lo, lo, i)


def _world_tables(b, f, F_grid, E_log=None, nE=0):
    """One world: the field mass strictly above each score bucket as an index
    into the payout grid, and (when a tie grid is given) the combined index
    into the tie-aware table, the mass LEVEL with the bucket being the
    bucket's own mass. Everything per-lineup in a world is then a gather by
    bucket -- the old per-(lineup, world) binary search was 90% of the run
    (measured: 20s of a 33s chunk on 225,000 lineups)."""
    hist = np.bincount(b, weights=f, minlength=_NB)
    strictly = np.cumsum(hist[::-1])[::-1] - hist
    pos = np.searchsorted(F_grid, np.clip(strictly, 0.0, 1.0))
    iF = np.minimum(pos, len(F_grid) - 1)
    if E_log is None:
        return iF
    iE = _log_index(E_log, np.clip(hist, 0.0, TIE_E_MAX))
    return iF, iF * nE + iE


def run_vs_field(Wc, Wf, wf, X, grids, chunk=500, progress=None):
    """Score candidates against a field in every world. Wc (K, P) candidate
    lineup weights, Wf (M, P) the field's lineups with probability wf (M,)
    summing to one (showdown: every legal lineup and the field model;
    classic: a sample from the field generator, 1/M each), X (P, N) player
    points per world, grids one payout_grid per contest. Returns
    ([{win, win_sole, top1, top01, cash, ev, ev_notie} per contest], opt (K,), N):
    means over worlds, and how often each candidate scored highest of the
    candidates. `win` is a share of first or better; `win_sole` is first
    outright; `ev` splits every tied position the way DraftKings does.

    World-major: each chunk's scores come out as (worlds, lineups) so a
    world is a contiguous row -- the (lineups, worlds) layout paid 25x on
    the per-world pass (9.9s vs 0.4s a chunk, measured)."""
    K, M, N = Wc.shape[0], Wf.shape[0], X.shape[1]
    F_grid = grids[0]["F"]
    E_log = np.log(np.maximum(grids[0]["E"], 1e-300))
    G = _grid_table(grids)
    G2, nE = _tie_table(grids)
    acc = np.zeros((K, G.shape[1]), dtype=np.float64)
    acc2 = np.zeros((K, G2.shape[1]), dtype=np.float64)
    opt = np.zeros(K, dtype=np.int64)
    same = Wc is Wf
    WcT = np.ascontiguousarray(Wc.T)
    WfT = WcT if same else np.ascontiguousarray(Wf.T)
    chunk = chunk_for(max(K, M), chunk)
    t0 = time.time()
    for start in range(0, N, chunk):
        Xc = np.ascontiguousarray(X[:, start:start + chunk].T)   # (c, P)
        Sc = Xc @ WcT                                             # (c, K)
        opt += np.bincount(np.argmax(Sc, axis=1), minlength=K)
        Bc = _buckets(Sc)
        Bf = Bc if same else _buckets(Xc @ WfT)
        for j in range(Bc.shape[0]):
            iF, iFE = _world_tables(Bf[j], wf, F_grid, E_log, nE)
            bj = Bc[j]
            acc += G[iF][bj]
            acc2 += G2[iFE][bj]
        if progress:
            progress(min(N, start + chunk), N, time.time() - t0)
    out = []
    for gi in range(len(grids)):
        cols = acc[:, gi * 5:(gi + 1) * 5] / N
        tie = acc2[:, gi * 2:(gi + 1) * 2] / N
        out.append({"win": cols[:, 0], "top1": cols[:, 1], "top01": cols[:, 2],
                    "cash": cols[:, 3], "ev_notie": cols[:, 4],
                    "win_sole": tie[:, 0], "ev": tie[:, 1]})
    return out, opt, N


def run(W, X, f, grid, chunk=500, progress=None):
    """Showdown form: every legal lineup is both the candidate set and the
    field. Returns per-lineup means over worlds: win, top1, top01, cash, ev
    (fees not yet netted), plus the hindsight-optimal tallies."""
    res, opt, N = run_vs_field(W, W, f, X, [grid], chunk=chunk, progress=progress)
    r = res[0]
    return {"win": r["win"], "win_sole": r["win_sole"], "top1": r["top1"], "top01": r["top01"],
            "cash": r["cash"], "ev": r["ev"], "ev_notie": r["ev_notie"], "opt": opt, "n_worlds": N}


def greedy_cover(P, k):
    """Greedy shared-field cover over a (candidates, worlds) matrix of
    per-world make-it probabilities. Returns (chosen rows best first,
    P(at least one chosen makes it) after each pick).

    One contest, one realised set of opponents, so within a world our entries
    are NESTED, not independent: our best-scoring entry carries our best
    rank, and no other entry of ours can clear a cut that one missed.
    Per world the union is therefore the best entry's probability, which is
    the row-wise maximum (the grid's top1/top01/cash columns are monotone in
    the field mass above us, so the largest probability belongs to the
    highest score -- guarded). The gain from adding a candidate is what it
    lifts that maximum, summed over worlds:

        gain_c = sum_w max(0, P[c, w] - covered_w)

    Two identical entries therefore gain nothing from the second copy, which
    is the whole point: the old product-of-misses form paid 19% for a pair of
    10% twins, and paid most where the entries were most alike."""
    P = np.asarray(P)
    K, N = P.shape
    if not K or not N:
        return [], []
    # rows held at once in the gain pass: 4M floats keeps the temporary near
    # 30MB instead of the whole (candidates, worlds) block
    rows_at_once = max(1, int(4_000_000 // max(N, 1)))
    chosen = []
    cov = np.zeros(N, dtype=np.float64)
    p_any = []
    for _ in range(min(k, K)):
        gain = np.empty(K, dtype=np.float64)
        for a in range(0, K, rows_at_once):
            blk = P[a:a + rows_at_once].astype(np.float64, copy=False)
            gain[a:a + rows_at_once] = np.maximum(blk - cov[None, :], 0.0).sum(axis=1)
        if chosen:
            gain[chosen] = -1.0
        j = int(np.argmax(gain))
        chosen.append(j)
        cov = np.maximum(cov, P[j].astype(np.float64))
        p_any.append(float(cov.mean()))
    return chosen, p_any


def portfolio_vs_field(Wc, Wf, wf, X, grids, cand_idx, k, chunk=500, rank="top1"):
    """Greedy cover over candidate lineups, per contest: each pick adds the
    most `rank` probability (top1 or top01) in the worlds the picks so far
    leave uncovered. Returns, per contest, (chosen candidate positions best
    first, P(at least one entry makes it) for every prefix length).

    Our entries all sit in ONE contest against ONE realised set of opponents,
    so inside a single simulated world they are not independent draws. This
    used to multiply (1 - P_j) across the picks, which is the answer for a
    world where each entry meets its own fresh field; two identical lineups
    with a 10% chance each came back as 19% instead of 10%, and twenty
    correlated entries were overstated far worse. What is true is that the
    events are NESTED: our highest-scoring entry has our best rank, so if it
    misses the cut none of the others can make it, and

        P(any of ours makes it | world) = P(our best-scoring one makes it)

    which is max_j P_jw because the grid's top1/top01/cash columns are
    monotone in the field mass above us (guarded). Diversification then comes
    from which entry is our best in DIFFERENT worlds, which is what the
    greedy step now buys: sum_w max(0, P_cand,w - covered_w).

    The one approximation left is that each P_jw treats all C-1 opponents as
    public-field draws when a handful of them are our own, known, lower
    lineups; at twenty entries in an 832,342-entry contest that is 2.4e-5 of
    the field and below the grid's own resolution, but it is not nothing in a
    small contest, so `entries` is reported beside the numbers."""
    if not len(cand_idx):
        return [([], []) for _ in grids]
    K, M, N = Wc.shape[0], Wf.shape[0], X.shape[1]
    F_grid = grids[0]["F"]
    same = Wc is Wf
    WfT = np.ascontiguousarray(Wf.T)
    WkT = np.ascontiguousarray(Wc[cand_idx].T)
    chunk = chunk_for(max(K, M), chunk)
    # per-world make-it probability for every candidate and contest,
    # (contests, K', N) float32, built once; the field pass is repeated (a
    # second matmul and bucket count) rather than kept from the scoring
    # pass: 60,000 worlds x 4,001 buckets would be a gigabyte of tables.
    P = np.zeros((len(grids), len(cand_idx), N), dtype=np.float32)
    tabs = [g[rank] for g in grids]
    for start in range(0, N, chunk):
        Xc = np.ascontiguousarray(X[:, start:start + chunk].T)
        Bf = _buckets(Xc @ WfT)
        Bk = _buckets(Xc @ WkT)
        for j in range(Bf.shape[0]):
            pos = _world_tables(Bf[j], wf, F_grid)
            for gi, tab in enumerate(tabs):
                P[gi, :, start + j] = tab[pos][Bk[j]]
    del same
    return [greedy_cover(P[gi], k) for gi in range(len(grids))]


def portfolio(W, X, f, grid, cand_idx, k, chunk=500):
    """Showdown form of portfolio_vs_field: one contest, the field is every
    legal lineup. Returns (chosen, p_any per prefix)."""
    return portfolio_vs_field(W, W, f, X, [grid], cand_idx, k, chunk=chunk)[0]


# ---- NFL showdown adapter ----------------------------------------------------
_FIELD_EXTRA_MAX = 8      # depth-gated players the FIELD still plays (WR4, RB3...)
_FIELD_EXTRA_MIN_PROJ = 2.0    # a WR4 projected at 3 is in thousands of their lineups


def build_nfl_showdown(dg, contest_id=None, n_sims=60000, n_worlds=None, chunk=500,
                       week=None, preseason=False, top=60, portfolio_sizes=(5, 10, 20),
                       candidates=1200, log=print):
    """The tournament for one DraftKings NFL showdown draft group, against the
    biggest GPP on it (or `contest_id`). Returns the artifact the tab serves,
    or None when the pool or the contest cannot be read."""
    if np is None:
        raise RuntimeError("numpy is required for the tournament")
    import dk
    import nfl_dfs
    import nfl_dfs_sim
    import simulate
    t0 = time.time()
    slate = dk.slate_for("nfl", draft_group_id=int(dg))
    if not slate:
        return None
    contests = slate.get("contests") or []
    contest = None
    if contest_id:
        contest = dk.contest_detail(int(contest_id))
    if not contest and contests:
        gpp = [c for c in contests if (c.get("entries") or 0) >= 100]
        big = max(gpp or contests, key=lambda c: (c.get("prize_pool") or 0))
        contest = dk.contest_detail(int(big["id"]))
    if not contest:
        return None
    csv_players = simulate.parse_dk_csv(slate["csv"])
    ents = nfl_dfs.showdown_pool(csv_players)
    teams = {e.get("team") for e in ents if e.get("team")}
    if week is None:
        try:
            import nfl_game_sim
            import nfl_preseason
            preseason = nfl_preseason.is_preseason()
            week = nfl_game_sim.current_week(preseason)
        except Exception as e:
            errlog.note("TOURN-week", e)
            week = 1
    log(f"[tourney] {slate.get('n_players')} players, {contest['name']}, week {week}: "
        f"simulating {n_sims:,} games of {' vs '.join(sorted(teams))}...")
    pool = nfl_dfs_sim.player_pool(week, n=int(n_sims), preseason=preseason, teams=teams,
                                   discrete=nfl_dfs_sim.SD_DISCRETE) or {}
    nidx, norm = nfl_dfs._norm_index(pool)
    excluded = []
    for e in ents:
        sim = nfl_dfs._pool_match(pool, e["name"], e["pos"], e.get("team"), nidx, norm)
        if sim and sim.get("arr"):
            e["proj"], e["ceiling"], e["floor"], e["arr"] = (
                round(sim["proj"], 1), sim["ceiling"], sim["floor"], sim["arr"])
        else:
            excluded.append({"name": e["name"], "pos": e["pos"], "team": e.get("team"),
                             "why": "no projection this week"})
            e["_drop"] = True
    ents = [e for e in ents if not e.get("_drop")]
    by_name = {e["name"]: e for e in ents}
    ents, dx = nfl_dfs._apply_depth(ents, preseason)
    # The depth gate is OUR rule, not the public's: a WR4 with a real
    # projection is in thousands of their lineups, so he stays in the field's
    # pool (never in ours) and his boom worlds count against us honestly.
    extra = []
    for d in dx:
        e = by_name.get(d["name"])
        if e is not None and "depth chart" in (d.get("why") or "") \
                and (e.get("proj") or 0) >= _FIELD_EXTRA_MIN_PROJ:
            e["_field_only"] = True
            e["depth"] = (d["why"].split(" ") or [""])[0]
            extra.append(e)
    extra.sort(key=lambda e: -e["proj"])
    extra = extra[:_FIELD_EXTRA_MAX]
    field_only = {e["name"] for e in extra}
    for d in dx:
        d["field"] = d["name"] in field_only
    excluded += dx
    if len(ents) < 6:
        return None
    ents = ents + extra
    t1 = time.time()
    N = min(len(e["arr"]) for e in ents)
    if n_worlds:
        N = min(N, int(n_worlds))
    X = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)   # (P, N)
    log(f"[tourney] pool {len(ents)} players ({len(extra)} field-only) x {N:,} worlds "
        f"({t1 - t0:.0f}s); enumerating lineups...")

    def entry_ok(cap_p, picked):
        got = []
        for p in picked:
            if p.get("_field_only") or not nfl_dfs._sd_allowed(p, cap_p, got):
                return False
            got.append(p)
        return True

    rich = sum(1 for p in ents if p.get("pos") in nfl_dfs._SD_GPP_CPT_POS
               and not p.get("_field_only")
               and p["cpt_salary"] >= nfl_dfs._SD_MIN_CPT_SALARY) >= 3

    def cpt_ok(p):
        if p.get("_field_only") or p.get("pos") not in nfl_dfs._SD_GPP_CPT_POS:
            return False
        return (not rich) or p["cpt_salary"] >= nfl_dfs._SD_MIN_CPT_SALARY

    idx, W, allowed = enumerate_showdown(ents, nfl_dfs.CAP, cpt_mult=nfl_dfs.CPT_MULT,
                                         entry_ok=entry_ok, cpt_ok=cpt_ok)
    if idx is None:
        return None
    f, beta = field_weights(ents, idx, cpt_mult=nfl_dfs.CPT_MULT)
    own_c, own_f = field_ownership(ents, idx, f)
    for i, e in enumerate(ents):
        e["own"] = round(float(own_c[i] + own_f[i]), 1)
    C = int(contest.get("max_entries") or contest.get("entered") or 0) or 10000
    t2 = time.time()
    chunk = chunk_for(len(idx), chunk)
    log(f"[tourney] {len(idx):,} legal lineups, {int(allowed.sum()):,} we may enter "
        f"({t2 - t1:.0f}s); field beta {beta:.3f}, top build {100 * f.max():.2f}% "
        f"(~{C * f.max():.0f} copies); scoring every lineup in every world, "
        f"{chunk} worlds a chunk...")
    grid = payout_grid(C, contest.get("payouts") or [], float(contest.get("entry_fee") or 1.0),
                       int(contest.get("places_paid") or max(1, C // 5)))
    every = max(chunk, (N // 6 // chunk) * chunk)
    res = run(W, X, f, grid, chunk=chunk,
              progress=lambda done, n, dt: log(f"[tourney]   {done:,}/{n:,} worlds, {dt:.0f}s")
              if done % every == 0 or done == n else None)
    t3 = time.time()
    fee = float(contest.get("entry_fee") or 1.0)
    copies = C * f                                         # expected duplicates in the field
    # `ev` already splits every tied position the way DraftKings does, and the
    # field mass on our exact score includes our own duplicates -- the old
    # ev_dup correction for first place alone would now count them twice.
    roi = (res["ev"] - fee) / fee
    ok = np.where(allowed)[0]

    def row(i, rank_by):
        cap = ents[idx[i, 0]]
        legs = [ents[idx[i, k]] for k in range(1, 6)]
        return {"captain": cap["name"], "captain_pos": cap["pos"], "captain_team": cap["team"],
                "flex": [p["name"] for p in legs],
                "lineup": ([{"slot": "CPT", "name": cap["name"], "pos": cap["pos"], "team": cap["team"],
                             "salary": cap["cpt_salary"], "depth": cap.get("depth"),
                             "own": round(float(own_c[idx[i, 0]]), 1),
                             "proj": round(1.5 * cap["proj"], 1)}]
                           + [{"slot": "FLEX", "name": p["name"], "pos": p["pos"], "team": p["team"],
                               "salary": p["salary"], "depth": p.get("depth"),
                               "own": round(float(own_f[idx[i, k]]), 1),
                               "proj": p["proj"]} for k, p in zip(range(1, 6), legs)]),
                "salary": int(cap["cpt_salary"] + sum(p["salary"] for p in legs)),
                "proj": round(1.5 * cap["proj"] + sum(p["proj"] for p in legs), 1),
                "win_pct": round(100.0 * float(res["win_sole"][i]), 4),
                "win_any_pct": round(100.0 * float(res["win"][i]), 4),
                "top1_pct": round(100.0 * float(res["top1"][i]), 2),
                "cash_pct": round(100.0 * float(res["cash"][i]), 1),
                "ev": round(float(res["ev"][i]), 2), "ev_notie": round(float(res["ev_notie"][i]), 2),
                "roi_pct": round(100.0 * float(roi[i]), 1),
                "expected_copies": round(float(copies[i]), 1),
                "opt_worlds": int(res["opt"][i]),
                "opt_pct": round(100.0 * float(res["opt"][i]) / res["n_worlds"], 2),
                "rank_by": rank_by}
    by_win = ok[np.argsort(-res["win_sole"][ok])][:top]
    by_ev = ok[np.argsort(-res["ev"][ok])][:top]
    by_top1 = ok[np.argsort(-res["top1"][ok])][:top]
    # the portfolio: greedy cover over the strongest allowed lineups by top-1%
    cand = ok[np.argsort(-res["top1"][ok])][:int(candidates)]
    kmax = max(portfolio_sizes)
    log(f"[tourney] scored ({t3 - t2:.0f}s); covering worlds with a portfolio of {kmax} "
        f"from the {len(cand):,} strongest...")
    chosen, p_any = portfolio(W, X, f, grid, cand, kmax, chunk=chunk)
    ports = {}
    for k in portfolio_sizes:
        kk = min(k, len(chosen))
        if not kk:
            continue
        ports[str(k)] = {"p_any_top1_pct": round(100.0 * p_any[kk - 1], 1),
                         # What that number IS, because it is not an unbiased
                         # forecast and used to look like one. The shortlist, the
                         # greedy cover and this figure all read the SAME worlds,
                         # so it is an in-sample selection score. S4 measured the
                         # optimism on held-out worlds across eight cross-fit
                         # cells: +2.7% on average and +7.4% at worst for this
                         # top-1% metric (and +8.5% / +14.5% for top 0.1%, which
                         # is why the tail metric is not served as coverage at
                         # all). The honest fix is a disjoint selection/scoring
                         # split in the builder, which costs half the worlds per
                         # step or twice the build; until that is done the number
                         # is labelled rather than quietly trusted.
                         "p_any_basis": {
                             "in_sample": True,
                             "why": ("selection and evaluation share one world set; "
                                     "this is a selection score, not a held-out "
                                     "probability"),
                             "measured_optimism_pct": {"mean": 2.7, "worst": 7.4},
                             "measured_in": "research/s4_crossfit.py (8 cross-fit cells)",
                             "fix": "disjoint selection/scoring worlds in the builder"},
                         "entries": [row(int(cand[j]), "cover") for j in chosen[:kk]]}
    t4 = time.time()
    # per-player: share of hindsight-optimal worlds at captain and at flex
    P = len(ents)
    opt_c = np.bincount(idx[:, 0], weights=res["opt"], minlength=P)
    opt_f = np.zeros(P, dtype=np.float64)
    for k in range(1, 6):
        opt_f += np.bincount(idx[:, k], weights=res["opt"], minlength=P)
    # the public's most popular build, so the sheet can say what the chalk is
    chalk_i = int(np.argmax(f))
    players_out = []
    for i, e in enumerate(ents):
        players_out.append({"name": e["name"], "pos": e["pos"], "team": e["team"],
                            "salary": e["salary"], "cpt_salary": e["cpt_salary"],
                            "depth": e.get("depth"), "field_only": bool(e.get("_field_only")),
                            "proj": e["proj"], "floor": e["floor"], "ceiling": e["ceiling"],
                            "field_cpt_pct": round(float(own_c[i]), 1),
                            "field_flex_pct": round(float(own_f[i]), 1),
                            "opt_cpt_pct": round(100.0 * float(opt_c[i]) / res["n_worlds"], 2),
                            "opt_flex_pct": round(100.0 * float(opt_f[i]) / res["n_worlds"], 2)})
    players_out.sort(key=lambda p: -(p["opt_cpt_pct"] + p["opt_flex_pct"]))
    try:
        st_sig, st_cls = status_sig([e["name"] for e in ents])
    except Exception as e:
        errlog.note("TOURN-status", e)
        st_sig, st_cls = None, {}
    _starts_ts = _iso_ts(contest.get("starts") or (slate.get("starts") if isinstance(slate, dict) else None))
    _built = int(time.time())
    return plain({"version": VERSION, "engine": SD_ENGINE,
            "kind": "showdown", "sport": "nfl", "draft_group_id": int(dg),
            "built_ts": _built, "status_sig": st_sig, "status": st_cls,
            # freshness, so a board can be audited for WHEN it was built
            # rather than only for what it says: a board made on Thursday and
            # served on Sunday used to look identical to one made at lock.
            "kickoff_ts": _starts_ts,
            "mins_before_lock": (None if not _starts_ts else int(round((_starts_ts - _built) / 60.0))),
            "simulator": nfl_dfs_sim.sim_stamp(n=int(n_sims), preseason=preseason,
                                               discrete=nfl_dfs_sim.SD_DISCRETE),
            "pool_sig": pool_sig_rich(slate["csv"]), "pool_sig_kind": "rich",
            "contest": {k: contest.get(k) for k in ("id", "name", "entry_fee", "prize_pool",
                                                    "first_prize", "places_paid", "max_entries",
                                                    "entered", "max_entries_per_user", "starts")},
            "slate": {"n_players": slate.get("n_players"), "dropped": slate.get("dropped"),
                      "teams": sorted(teams), "week": week, "preseason": bool(preseason),
                      "field_only": sorted(field_only)},
            "sims": int(n_sims), "worlds": int(res["n_worlds"]),
            "lineups_legal": int(len(idx)), "lineups_allowed": int(allowed.sum()),
            "field_model": {"kind": "softmax over projected points",
                            "beta": round(beta, 4), "top_share_pct": round(100.0 * FIELD_TOP_SHARE, 2),
                            "top_copies": round(float(C * f.max()), 1),
                            "chalk": row(chalk_i, "chalk"),
                            "note": ("one knob: the most popular build holds "
                                     f"{100 * FIELD_TOP_SHARE:.1f}% of the field (a 231-way tie "
                                     "won DraftKings' 2021 GB-DET showdown Millionaire). "
                                     "Dollar figures assume the sim is the truth; read the ranks.")},
            "experimental": sd_money_gate(len(idx), int(contest.get("max_entries")
                                                          or contest.get("entered") or 0)),
            "rules": list(nfl_dfs._SD_RULES),
            "top_win": [row(int(i), "win") for i in by_win],
            "top_ev": [row(int(i), "ev") for i in by_ev],
            "top_top1": [row(int(i), "top1") for i in by_top1],
            "portfolio": ports,
            "players": players_out,
            "excluded": excluded[:40],
            "timings": {"sims_s": round(t1 - t0, 1), "enumerate_s": round(t2 - t1, 1),
                        "score_s": round(t3 - t2, 1), "portfolio_s": round(t4 - t3, 1),
                        "total_s": round(t4 - t0, 1)}})


# ---- classic (nine slots) ------------------------------------------------------
# A 12-game main slate has ~400 projected players and 9 slots: trillions of
# legal lineups, so two things change from showdown and everything else
# stays. The FIELD is a sample (classic_sample, the generator's knobs pinned
# to published numbers), and OUR candidates are built: the exact best
# lineup of every simulated world (optimal_lineups -- the lineup that wins
# that world, by construction) plus rule-abiding draws from a sharper
# generator. Scoring, the payout curves, duplication, the hindsight tallies
# and the portfolio are the showdown machinery unchanged.
CL_SLOTS = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST")
_SLOT_LABELS = ("QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE", "FLEX", "DST")
_CL_POS = ("QB", "RB", "WR", "TE", "DST")
_CL_CODE = {p: i for i, p in enumerate(_CL_POS)}
CL_FIELD_MAX_OWN = 0.38
# How sharp the public is: the most-owned player's share of Milly Maker
# lineups runs 30-45% in a normal week (a mega-chalk back at 45% is the
# top of it). Smooth, huge sample support, and it is the number the DFS
# sites publish every week, so it can be checked against the real sheet
# after lock. The first cut bisected on the sample's collision rate and
# chased noise: eight duplicate pairs among 8,000 draws is not a signal.
CL_FIELD_COLLISION = 1.2e-7    # reported, not fitted: P(two random entries hold the same lineup)
CL_STACK_DIST = (0.174, 0.490, 0.289, 0.047)
# Establish The Run (Levitan, Milly Maker trends): entries with a naked
# QB 17.4%, one teammate 49.0%, two 28.9%, three-plus 4.2% (the remainder
# folded into three-plus).
CL_BRING_BACK = 0.35        # a pass-catcher from the QB's opponent; assumed, not published
CL_SALARY_USED = 49400.0    # mean salary the field spends; assumed near the cap
CL_DST_VS_OWN_QB = 0.08     # share of the field that plays a defense against its own QB
_CL_KEEP = {"QB": 16, "RB": 40, "WR": 60, "TE": 20, "DST": 32}   # the solver's pool, by 90th pct
_CL_FIELD_KEEP = 60         # depth-gated players the field still plays (WR4s, third backs)
_CL_PUNT_SALARY = 3000
_CL_PUNT_ROLES = {"RB1", "RB2", "WR1", "WR2", "WR3", "TE1"}
# Roster concentration, two knobs. The team cap is the rule the owner set
# (QB plus two teammates at most). The per-game cap was written as four,
# but the opponent field it needs was blank on every live board (see
# nfl_dfs._matchup), so the optimizer has only ever run with the team cap
# binding -- six from one game is legal under it -- and the lineups the
# owner entered were built that way. None keeps that freedom on purpose:
# the cap is a policy decision for after the simulator work, not a side
# effect of fixing a parser. Measured 2026-09-10 on holdout worlds: a cap of
# four costs the top-40 about 5% of top 1%, 13% of top 0.1% and 25% of
# first place, and excludes both entered lineups.
CL_MAX_PER_TEAM = 3
CL_MAX_PER_GAME = None
# A receiving back as a stack partner. The rule as it stands counts only a
# receiver or a tight end from the quarterback's team, so a lineup of
# quarterback plus his pass-catching back is "not stacked". The training
# seasons put a back's residual co-movement with his quarterback at 0.066
# pooled -- 0.10 for backs projected four to five targets against 0.04 for
# backs under two, real but small beside a receiver's 0.39. None keeps the
# rule as it is; a number lets a back count once his projected targets
# reach it. Chosen in Stage 4B against the fitted simulator, not here.
RB_STACK_TARGETS = None
_KNOB = object()                # "use the module knob" for classic_allowed


def _cap_rule():
    if CL_MAX_PER_GAME is None:
        return (f"at most {CL_MAX_PER_TEAM} from one team; no cap on players from one game "
                f"(CL_MAX_PER_GAME, a knob, decided after the simulator work)")
    return f"at most {CL_MAX_PER_GAME} players from one game and {CL_MAX_PER_TEAM} from one team"


def _stack_rule():
    if RB_STACK_TARGETS is None:
        return ("the quarterback is stacked with at least one of his receivers or tight ends "
                "(a back does not count; RB_STACK_TARGETS, a knob, decided after the simulator work)")
    return ("the quarterback is stacked with at least one of his receivers or tight ends, or a back "
            f"projected for {RB_STACK_TARGETS:g} or more targets")


CL_RULES = (
    _stack_rule(),
    "no defense against our quarterback's team or our running backs' teams",
    _cap_rule(),
    "at most one punt under $3,000, and he must hold a role (RB1-2, WR1-3, TE1)",
    "roster gate: QB1, RB1-2, WR1-3, TE1-2 by Sleeper's depth chart; OUT/IR/doubtful out",
)


def _cl_state(q, r, w, t, d):
    return (((q * 4 + r) * 5 + w) * 2 + t) * 2 + d


_CL_S = 2 * 4 * 5 * 2 * 2
_CL_FINALS = (_cl_state(1, 2, 4, 1, 1), _cl_state(1, 3, 3, 1, 1))   # FLEX a WR, FLEX an RB


def _cl_transitions():
    """Per position, (from_states, to_states): roster counts (q, r, w, t, d)
    with r + w <= 6 (two backs, three receivers, one FLEX of either). A
    bijection per position, so adding a player is one fancy-indexed max."""
    out = {}
    for p in _CL_POS:
        fr, to = [], []
        for q in range(2):
            for r in range(4):
                for w in range(5):
                    for t in range(2):
                        for d in range(2):
                            if r + w > 6:
                                continue
                            nq, nr, nw, nt, nd = (q + (p == "QB"), r + (p == "RB"), w + (p == "WR"),
                                                  t + (p == "TE"), d + (p == "DST"))
                            if nq > 1 or nr > 3 or nw > 4 or nt > 1 or nd > 1 or nr + nw > 6:
                                continue
                            fr.append(_cl_state(q, r, w, t, d))
                            to.append(_cl_state(nq, nr, nw, nt, nd))
        out[p] = (np.asarray(fr, dtype=np.int64), np.asarray(to, dtype=np.int64))
    return out


def optimal_lineups(X, sal, pos, cap=50000, chunk=64, progress=None):
    """The best legal nine-man lineup of EVERY world: an exact 0/1 knapsack
    over players with the roster counts as state, vectorized across a chunk
    of worlds, backpointers kept per chunk and walked back per world. X (P,
    N) points, sal (P,) dollars, pos (P,) position strings. Returns (one
    sorted tuple of player indices per world -- empty when no legal lineup
    exists -- and the values (N,)). Measured: 3.6s per 64 worlds on a
    160-player pool here; the PC does the 20,000 worlds it is asked for in
    a coffee break."""
    P, N = X.shape
    s100 = np.asarray([int(v) // 100 for v in sal], dtype=np.int64)
    CAP = int(cap) // 100
    trans = _cl_transitions()
    pred = {p: dict(zip(to.tolist(), fr.tolist())) for p, (fr, to) in trans.items()}
    NEG = -1.0e6
    lineups, values = [], np.full(N, NEG, dtype=np.float64)
    take = None
    t0 = time.time()
    for start in range(0, N, chunk):
        Xc = np.ascontiguousarray(X[:, start:start + chunk], dtype=np.float32)
        c = Xc.shape[1]
        V = np.full((_CL_S, CAP + 1, c), NEG, dtype=np.float32)
        V[0, 0, :] = 0.0
        if take is None or take.shape[3] != c:
            take = np.zeros((P, _CL_S, CAP + 1, c), dtype=bool)
        else:
            take[:] = False
        for i in range(P):
            si = int(s100[i])
            if si > CAP:
                continue
            fr, to = trans[pos[i]]
            src = V[fr, :CAP + 1 - si, :]                 # gathered BEFORE the write: 0/1
            cand = src + Xc[i][None, None, :]
            cur = V[to, si:, :]
            better = (cand > cur) & (src > NEG / 2)
            V[to, si:, :] = np.where(better, cand, cur)
            take[i, to, si:, :] = better
        flat = V[list(_CL_FINALS)].reshape(-1, c)
        best = np.argmax(flat, axis=0)
        vals = flat[best, np.arange(c)]
        for j in range(c):
            f_, s = divmod(int(best[j]), CAP + 1)
            st = _CL_FINALS[f_]
            picks = []
            if vals[j] > NEG / 2:
                for i in range(P - 1, -1, -1):
                    if take[i, st, s, j]:
                        picks.append(i)
                        st = pred[pos[i]][st]
                        s -= int(s100[i])
                        if st == 0:
                            break
            ok = len(picks) == 9
            lineups.append(tuple(sorted(picks)) if ok else ())
            values[start + j] = float(vals[j]) if ok else NEG
        if progress:
            progress(min(N, start + chunk), N, time.time() - t0)
    return lineups, values


def _gumbel_pick(logits, elig, rng):
    """One player per row by Gumbel-max over the eligible ones: a softmax
    draw for every row at once. -1 where a row has nobody eligible."""
    g = rng.gumbel(size=elig.shape).astype(np.float32)
    z = np.where(elig, logits[None, :] + g, -np.inf)
    pick = np.argmax(z, axis=1)
    pick[~elig.any(axis=1)] = -1
    return pick


def classic_sample(players, n, rng, beta, kappa, cap=50000, stack_dist=CL_STACK_DIST,
                   bring_p=CL_BRING_BACK, dst_own_p=CL_DST_VS_OWN_QB, rules=False, exclude=None,
                   report=None):
    """`n` classic lineups the way the public builds them, vectorized. A
    player's pull is beta * projection - kappa * salary / 1000 (beta the
    sharpness, kappa the price sensitivity; both calibrated by the caller).
    The quarterback first; then a stack count drawn from `stack_dist`
    (teammates at WR/TE by the same pull); a bring-back from his opponent
    with `bring_p`; then the remaining slots, cap-aware, DST last. With
    `rules` the stack is at least one and the defense never faces our QB
    or backs (the other rules are checked by classic_allowed). Returns idx
    (rows, 9) int32 -- QB, RB, RB, WR, WR, WR, TE, FLEX, DST. The salary of
    the cheapest defense the row may still take is reserved at every pick,
    so every draw comes out under the cap with its defense; a row is dropped
    only when it is genuinely impossible. `report`, a dict, is filled with
    the completion receipt: requested, completed, rejected, unfilled slots
    by slot, over-cap rows -- the build stamps it and fails on a shortfall
    (check_completion)."""
    P = len(players)
    proj = np.asarray([float(p.get("proj") or 0.0) for p in players], dtype=np.float32)
    sal = np.asarray([int(p["salary"]) for p in players], dtype=np.int64)
    pc = np.asarray([_CL_CODE.get(p["pos"], -1) for p in players], dtype=np.int64)
    teams = sorted({p.get("team") or "" for p in players} | {p.get("opp") or "" for p in players})
    tcode = {t: i for i, t in enumerate(teams)}
    team = np.asarray([tcode[p.get("team") or ""] for p in players], dtype=np.int64)
    opp = np.asarray([tcode[p.get("opp") or ""] for p in players], dtype=np.int64)
    # kappa is points per $1,000 (the public's price of a projected point);
    # beta scales the whole thing, so sharpness and price sensitivity are
    # separate knobs and the two calibrations do not fight.
    logits = (beta * (proj - kappa * sal / 1000.0)).astype(np.float32)
    avail = np.ones(P, dtype=bool)
    if exclude is not None:
        avail &= ~np.asarray(exclude, dtype=bool)
    mins = {k: int(sal[(pc == _CL_CODE[k]) & avail].min()) if ((pc == _CL_CODE[k]) & avail).any() else 10 ** 9
            for k in _CL_POS}
    idx = np.full((n, 9), -1, dtype=np.int32)
    used = np.zeros((n, P), dtype=bool)
    used[:, ~avail] = True
    T = len(teams)
    # The defense's salary is reserved throughout -- not the cheapest defense
    # on the slate but the cheapest one the row may still take under its
    # partial roster. With the global floor, 370 of 100,000 draws
    # (2026-09-10) reached the last slot with eight filled and no legal
    # defense affordable, and those rows were not random: cheap quarterbacks,
    # lighter stacks, more bring-backs, spend-out builds, so dropping them
    # tilted the field by that much. The reserve is per row (the defense
    # facing the row's quarterback is off limits for the careful 92% of the
    # field and for every row under `rules`) and, under `rules`, a back being
    # considered is priced as if his team were rostered, since the defense
    # may not face our backs either.
    dst_ids = np.where((pc == 4) & avail)[0]
    dst_ids = dst_ids[np.argsort(sal[dst_ids], kind="stable")]

    def dst_floor(blocked):
        """(n,) the cheapest defense whose opponent is not blocked (n, T)."""
        res = np.full(blocked.shape[0], 10 ** 9, dtype=np.int64)
        for d in dst_ids:
            res = np.where((res == 10 ** 9) & ~blocked[:, opp[d]], sal[d], res)
        return res

    state = {"reserve": np.full(n, mins["DST"], dtype=np.int64), "by_team": None}
    # The other floors are per row too: the cheapest players of each
    # position NOT already used in the row, one per remaining need, and
    # for the flex the next unused back or receiver after those. A global
    # minimum let a row spend to the point where the one $3,000 receiver
    # it was counting on for the flex was already in its WR3 slot (45 of
    # 30,000 rule-abiding draws, 2026-09-10). Pass-catchers from the
    # quarterback's own game do not count either: the fill never takes
    # them (the stack and the bring-back are drawn, not filled), so a cheap
    # receiver on the QB's opponent is no floor for the row's flex.
    cheap = {}
    for k in ("RB", "WR", "TE"):
        ids = np.where((pc == _CL_CODE[k]) & avail)[0]
        cheap[k] = ids[np.argsort(sal[ids], kind="stable")][:10]

    def floors():
        """(total (n,), {kind: (n,) this kind's charge}) from the row's unused players."""
        rel = {"QB": np.full(n, mins["QB"], dtype=np.int64), "DST": state["reserve"]}
        cost = need["QB"] * rel["QB"] + need["DST"] * rel["DST"]
        nxt = {}
        for k in ("RB", "WR", "TE"):
            ids = cheap[k]
            if not len(ids):
                rel[k] = np.full(n, 10 ** 9, dtype=np.int64)
                nxt[k] = rel[k]
                cost = cost + need[k] * rel[k]
                continue
            un = ~used[:, ids]
            if k in ("WR", "TE"):
                tid = team[ids][None, :]
                un &= (tid != qteam[:, None]) & (tid != qopp[:, None])
            rank = np.cumsum(un, axis=1)
            csal = sal[ids][None, :]
            take = un & (rank <= need[k][:, None])
            cost = cost + (take * csal).sum(axis=1)
            first = un & (rank == 1)
            rel[k] = np.where(first.any(axis=1), (first * csal).sum(axis=1), int(sal[ids].max()))
            nx = un & (rank == need[k][:, None] + 1)
            nxt[k] = np.where(nx.any(axis=1), (nx * csal).sum(axis=1), int(sal[ids].max()))
        rel["FLEX"] = np.minimum(nxt["RB"], nxt["WR"])
        cost = cost + need["FLEX"] * rel["FLEX"]
        return cost, rel

    def reserve_for(blocked):
        state["reserve"] = dst_floor(blocked)
        if rules:
            bt = np.empty((n, T), dtype=np.int64)
            for t in range(T):
                b2 = blocked.copy()
                b2[:, t] = True
                bt[:, t] = dst_floor(b2)
            state["by_team"] = bt
    spent = np.zeros(n, dtype=np.int64)
    need = {"QB": np.full(n, 1), "RB": np.full(n, 2), "WR": np.full(n, 3), "TE": np.full(n, 1),
            "DST": np.full(n, 1), "FLEX": np.full(n, 1)}
    slot_of = {"RB": (1, 2), "WR": (3, 4, 5), "TE": (6,), "DST": (8,), "FLEX": (7,)}

    def place(rows, pick):
        """Record a pick for `rows` (pick (n,), -1 = none) into the right slot."""
        ok = rows & (pick >= 0)
        r = np.where(ok)[0]
        if not len(r):
            return
        p = pick[r]
        used[r, p] = True
        spent[r] += sal[p]
        code = pc[p]
        for kind, cd in (("RB", 1), ("WR", 2), ("TE", 3), ("DST", 4)):
            sel = r[code == cd]
            if not len(sel):
                continue
            main = need[kind][sel] > 0
            for k_, arr_ in ((kind, main), ("FLEX", ~main)):
                rows_k = sel[arr_]
                for row in rows_k:
                    for s_ in slot_of[k_]:
                        if idx[row, s_] < 0:
                            idx[row, s_] = pick[row]
                            break
                need[k_][rows_k] -= 1

    def elig_for(kinds, rows, extra=None):
        """(n, P) eligibility: kinds a set of position names, cap-aware."""
        e = np.zeros((n, P), dtype=bool)
        floor, rel = floors()
        budget = int(cap) - spent - floor
        for kind in kinds:
            if kind == "FLEX":
                cols = (pc == 1) | (pc == 2)
                room = need["FLEX"] > 0
                relief = rel["FLEX"]
            else:
                cols = pc == _CL_CODE[kind]
                room = need[kind] > 0
                relief = rel[kind]
            # a WR/RB may also take the FLEX slot once its own slots are full
            if kind in ("RB", "WR"):
                room = room | (need["FLEX"] > 0)
                relief = np.where(need[kind] > 0, relief, rel["FLEX"])
            fit = sal[None, :] <= (budget + relief)[:, None]
            if rules and state["by_team"] is not None and kind in ("RB", "FLEX"):
                # a back on the roster puts the defense facing his team off
                # limits: he fits only if the defense still affordable WITH
                # him rostered fits too
                rb = np.where(cols & (pc == 1))[0]
                if len(rb):
                    more = state["by_team"][:, team[rb]] - state["reserve"][:, None]
                    fit[:, rb] &= sal[rb][None, :] <= (budget + relief)[:, None] - more
            e |= (cols[None, :] & fit & room[:, None] & rows[:, None])
        e &= ~used
        if extra is not None:
            e &= extra
        return e

    allr = np.ones(n, dtype=bool)
    qteam = np.full(n, -1, dtype=np.int64)          # known after the QB pick; the floors read them
    qopp = np.full(n, -1, dtype=np.int64)
    # 1. the quarterback
    qb = _gumbel_pick(logits, elig_for(("QB",), allr), rng)
    idx[:, 0] = qb
    ok = qb >= 0
    used[np.arange(n)[ok], qb[ok]] = True
    spent[ok] += sal[qb[ok]]
    need["QB"][ok] = 0
    qteam = np.where(ok, team[np.maximum(qb, 0)], -1)
    qopp = np.where(ok, opp[np.maximum(qb, 0)], -1)
    # the public mostly avoids a defense against its own quarterback; drawn
    # here so the salary reserved for the defense knows who may take which
    careful = rng.random(n) >= dst_own_p
    blocked = np.zeros((n, T), dtype=bool)
    r_ = np.where(ok & (careful | rules))[0]
    blocked[r_, qteam[r_]] = True
    reserve_for(blocked)
    # 2. the stack: how many of his pass-catchers
    dist = np.asarray(stack_dist, dtype=np.float64)
    if rules:
        dist = dist.copy()
        dist[0] = 0.0
    dist = dist / dist.sum()
    k_stack = rng.choice(len(dist), size=n, p=dist)
    for step in range(1, len(dist)):
        rows = ok & (k_stack >= step)
        same_team = team[None, :] == qteam[:, None]
        pick = _gumbel_pick(logits, elig_for(("WR", "TE"), rows, extra=same_team), rng)
        place(rows, pick)
    # 3. the bring-back
    rows = ok & (rng.random(n) < bring_p)
    from_opp = team[None, :] == qopp[:, None]
    place(rows, _gumbel_pick(logits, elig_for(("WR", "TE"), rows, extra=from_opp), rng))
    # 4. the rest: backs, tight end, receivers, FLEX, defense last. The
    # stack count and the bring-back are OUTCOMES in the published numbers,
    # so the fill never adds a pass-catcher from either side of the QB's
    # game: what was drawn is what the lineup ends with.
    catcher = np.isin(pc, (2, 3))
    no_more = ~((team[None, :] == qteam[:, None]) | (team[None, :] == qopp[:, None])) | ~catcher[None, :]
    for kind, times in (("RB", 3), ("TE", 1), ("WR", 4), ("FLEX", 1)):
        for _ in range(times):
            if kind == "FLEX":
                rows = ok & (need["FLEX"] > 0)
                pick = _gumbel_pick(logits, elig_for(("FLEX",), rows, extra=no_more), rng)
            else:
                rows = ok & (need[kind] > 0)
                pick = _gumbel_pick(logits, elig_for((kind,), rows, extra=no_more), rng)
            place(rows, pick)
            if rules and kind in ("RB", "FLEX"):
                blocked = np.zeros((n, T), dtype=bool)
                for s_ in (0, 1, 2):
                    r_ = np.where(idx[:, s_] >= 0)[0]
                    blocked[r_, team[idx[r_, s_]]] = True
                r_ = np.where((idx[:, 7] >= 0) & (pc[np.maximum(idx[:, 7], 0)] == 1))[0]
                blocked[r_, team[idx[r_, 7]]] = True
                reserve_for(blocked)
    if rules:
        off_teams = np.zeros((n, T), dtype=bool)
        for s_ in (0, 1, 2):
            r = np.where(idx[:, s_] >= 0)[0]
            off_teams[r, team[idx[r, s_]]] = True
        r = np.where((idx[:, 7] >= 0) & (pc[np.maximum(idx[:, 7], 0)] == 1))[0]   # the flex only when he is a back, as classic_allowed
        off_teams[r, team[idx[r, 7]]] = True
        avoid = off_teams[:, opp]                                   # (n, P): DST facing our QB / backs
    else:
        avoid = (opp[None, :] == qteam[:, None]) & careful[:, None]
    rows = ok & (need["DST"] > 0)
    place(rows, _gumbel_pick(logits, elig_for(("DST",), rows, extra=~avoid), rng))
    good = (idx >= 0).all(axis=1) & (spent <= int(cap))
    if report is not None:
        unfilled = idx < 0
        report.update({"requested": int(n), "completed": int(good.sum()), "rejected": int((~good).sum()),
                       "unfilled_by_slot": {_SLOT_LABELS[k]: int(unfilled[:, k].sum())
                                            for k in range(9) if unfilled[:, k].any()},
                       "no_qb": int(unfilled[:, 0].sum()),
                       "over_cap": int(((idx >= 0).all(axis=1) & (spent > int(cap))).sum())})
    return idx[good]


def check_completion(report, what):
    """The sampler's invariant, enforced: every requested draw completes.
    A short field is a build failure, ledgered (TOURN-field-short), never a
    quietly conditioned field -- the 370 of 100,000 the old floors dropped
    were one shape of lineup. The floors are a lower-bound heuristic, not a
    proof, so this is what catches the next interaction they miss."""
    req, done = int(report.get("requested") or 0), int(report.get("completed") or 0)
    if done < req:
        msg = (f"{what}: {done:,} of {req:,} draws completed; unfilled {report.get('unfilled_by_slot')}, "
               f"over cap {report.get('over_cap')}, no QB {report.get('no_qb')}")
        errlog.note("TOURN-field-short", msg=msg)
        raise RuntimeError(msg)
    return report


def classic_allowed(idx, players, max_per_game=_KNOB, max_per_team=_KNOB, rb_stack_targets=_KNOB):
    """Our rulebook on a batch of lineups (rows, 9): stacked QB, no defense
    against our QB's or backs' teams, the team cap and (when set) the game
    cap, at most one sub-$3,000 punt and he holds a role. Field-only players
    (the depth gate's exclusions) fail it. The caps default to the module
    knobs; max_per_game=None means no per-game cap, and rb_stack_targets a
    number lets a back with that many projected targets count as the
    quarterback's stack partner."""
    if max_per_game is _KNOB:
        max_per_game = CL_MAX_PER_GAME
    if max_per_team is _KNOB:
        max_per_team = CL_MAX_PER_TEAM
    if rb_stack_targets is _KNOB:
        rb_stack_targets = RB_STACK_TARGETS
    teams = sorted({p.get("team") or "" for p in players} | {p.get("opp") or "" for p in players})
    tcode = {t: i for i, t in enumerate(teams)}
    team = np.asarray([tcode[p.get("team") or ""] for p in players])
    opp = np.asarray([tcode[p.get("opp") or ""] for p in players])
    game = np.minimum(team, opp) * len(teams) + np.maximum(team, opp)
    sal = np.asarray([int(p["salary"]) for p in players])
    pc = np.asarray([_CL_CODE.get(p["pos"], -1) for p in players])
    role = np.asarray([str(p.get("depth") or "").split("·")[0] in _CL_PUNT_ROLES for p in players])
    field_only = np.asarray([bool(p.get("_field_only")) for p in players])
    T = team[idx]                                              # (rows, 9)
    qb_team = T[:, 0]
    catchers = idx[:, [3, 4, 5, 6, 7]]
    stacked = ((team[catchers] == qb_team[:, None]) & np.isin(pc[catchers], (2, 3))).any(axis=1)
    if rb_stack_targets is not None:
        tgt = np.asarray([float(p.get("rec_tgt") or 0.0) for p in players])
        backs = idx[:, [1, 2, 7]]
        stacked |= ((team[backs] == qb_team[:, None]) & (pc[backs] == 1)
                    & (tgt[backs] >= float(rb_stack_targets))).any(axis=1)
    dst_opp = opp[idx[:, 8]]
    dst_ok = (dst_opp != qb_team) & (dst_opp != T[:, 1]) & (dst_opp != T[:, 2]) \
        & ~((dst_opp == T[:, 7]) & (pc[idx[:, 7]] == 1))
    Gm = game[idx]
    per_game = np.max(np.stack([(Gm == Gm[:, [k]]).sum(axis=1) for k in range(9)], axis=1), axis=1)
    per_team = np.max(np.stack([(T == T[:, [k]]).sum(axis=1) for k in range(9)], axis=1), axis=1)
    punt = (sal[idx] < _CL_PUNT_SALARY) & (pc[idx] != 4)
    punts_ok = (punt.sum(axis=1) <= 1) & ~(punt & ~role[idx]).any(axis=1)
    game_ok = (per_game <= max_per_game) if max_per_game is not None else np.ones(len(idx), dtype=bool)
    return (stacked & dst_ok & game_ok & (per_team <= max_per_team)
            & punts_ok & ~field_only[idx].any(axis=1))


def _dup_share(idx):
    """The most common lineup's share of a sample (rows sorted by player)."""
    if not len(idx):
        return 0.0
    rows = np.sort(idx, axis=1)
    _u, counts = np.unique(rows, axis=0, return_counts=True)
    return float(counts.max()) / len(rows)


def _collision(idx):
    """Unbiased estimate of P(two random field entries are the same lineup)
    from a sample: colliding pairs over all pairs."""
    n = len(idx)
    if n < 2:
        return 0.0
    rows = np.sort(idx, axis=1)
    _u, counts = np.unique(rows, axis=0, return_counts=True)
    return float((counts * (counts - 1)).sum()) / (n * (n - 1))


def field_stats(idx, players):
    """What a sampled field actually does, measured on the sample: the
    most-owned player's share, the mean salary, the share of lineups with a
    bring-back (a pass-catcher from the QB's opponent), the share whose
    defense faces its own QB, and the stack-count distribution. The
    calibrator's targets are claims; these are the receipts, stamped on the
    board and checked by the guards. With a blank opponent field (the live
    boards before 2026-09-10) bring-backs read 0% and the defense share 0%."""
    idx = np.asarray(idx)
    if not len(idx):
        return {"n": 0}
    P = len(players)
    team = np.asarray([p.get("team") or "" for p in players])
    opp = np.asarray([p.get("opp") or "" for p in players])
    sal = np.asarray([int(p["salary"]) for p in players])
    pc = np.asarray([_CL_CODE.get(p["pos"], -1) for p in players])
    own = np.bincount(idx.ravel(), minlength=P) / len(idx)
    qb = idx[:, 0]
    catchers = idx[:, 3:8]
    is_catcher = np.isin(pc[catchers], (2, 3))
    stack = ((team[catchers] == team[qb][:, None]) & is_catcher).sum(axis=1)
    has_opp = opp[qb] != ""
    bring = ((team[catchers] == opp[qb][:, None]) & is_catcher).any(axis=1) & has_opp
    dst_own = (opp[idx[:, 8]] == team[qb]) & (opp[idx[:, 8]] != "")
    dist = np.bincount(np.minimum(stack, 3), minlength=4) / len(idx)
    return {"n": int(len(idx)), "max_own": float(own.max()),
            "mean_salary": float(sal[idx].sum(axis=1).mean()),
            "bring_back": float(bring.mean()), "dst_vs_own_qb": float(dst_own.mean()),
            "stack_dist": [float(x) for x in dist]}


def calibrate_field(players, rng, n=40000, max_own=CL_FIELD_MAX_OWN, salary_used=CL_SALARY_USED,
                    exclude=None, rounds=4, own_tol=0.01, salary_tol=100.0):
    """(beta, kappa) for classic_sample: kappa (points per $1,000) so the
    mean salary spent lands on `salary_used`, beta so the most-owned player
    holds `max_own` of the sample; the two bisections alternated until both
    land inside the tolerances (at most `rounds` times), because each moves
    the other. Returns (beta, kappa, top ownership, mean salary, collision,
    top build share), the middle two measured on a final fresh draw.

    Why the tolerances: the first cut stopped after two rounds with an
    eight-step beta bisection over [0, 3], a resolution of 0.047 in beta
    while ownership moves about 1.3 points per 0.01 of beta -- so a 38%
    target landed at 41% -- and its kappa had been fitted to the PREVIOUS
    beta, so a $49,400 target landed at $48,958 (measured 2026-09-10).
    Now: ten beta steps (0.003), kappa refitted after beta before the check,
    and the loop runs until the draw is within a point of ownership and $100
    of spend. The first round searches the full brackets (22 draws of `n`);
    later rounds bisect a narrow bracket round the last answer (17 draws),
    so the worst case at four rounds is 73 draws against the old cut's 30,
    and the usual case, one or two rounds, costs about what it did. When the
    two targets cannot both be met on a pool, a pattern search (at most 12
    more draws) lands the least combined miss instead of leaving the whole
    miss on whichever target the last bisection did not touch."""
    sal = np.asarray([int(p["salary"]) for p in players])
    P = len(players)

    def draw(beta, kappa):
        idx = classic_sample(players, n, rng, beta, kappa, exclude=exclude)
        if not len(idx):
            return idx, 0.0, 0.0
        own = np.bincount(idx.ravel(), minlength=P) / len(idx)
        return idx, float(sal[idx].sum(axis=1).mean()), float(own.max())

    def kappa_for(beta, lo, hi, steps):
        for _ in range(steps):
            kappa = 0.5 * (lo + hi)
            _idx, mean, _own = draw(beta, kappa)
            if mean > salary_used:
                lo = kappa                     # spending too much: charge more per dollar
            else:
                hi = kappa
        return 0.5 * (lo + hi)

    def beta_for(kappa, lo, hi, steps):
        for _ in range(steps):
            beta = 0.5 * (lo + hi)
            _idx, _mean, own = draw(beta, kappa)
            if own < max_own:
                lo = beta
            else:
                hi = beta
        return 0.5 * (lo + hi)
    beta, kappa = 0.5, 2.0
    idx, mean, own = None, 0.0, 0.0
    for _r in range(max(1, int(rounds))):
        if _r == 0:
            kappa = kappa_for(beta, -4.0, 6.0, 7)      # negative = the public pays up for stars
            beta = beta_for(kappa, 0.0, 3.0, 10)
        else:
            kappa = kappa_for(beta, kappa - 0.75, kappa + 0.75, 5)
            beta = beta_for(kappa, max(0.0, beta - 0.3), beta + 0.3, 7)
        kappa = kappa_for(beta, kappa - 0.75, kappa + 0.75, 5)   # kappa was fitted to the previous beta
        idx, mean, own = draw(beta, kappa)
        if abs(own - max_own) <= own_tol and abs(mean - salary_used) <= salary_tol:
            break
    if not (abs(own - max_own) <= own_tol and abs(mean - salary_used) <= salary_tol):
        # The two targets can conflict: on the 2026-09-10 pool every (beta,
        # kappa) that spends $49,400 holds Gibbs at 42% or more, so 38% and
        # $49,400 are not on the curve, and the alternation ends wherever its
        # last bisection left it (42.9% and $49,410, the whole miss on one
        # target). Finish with a pattern search on the combined miss in units
        # of the tolerances, so the miss is shared the way the tolerances
        # weigh it and the receipts on the board say what was reached.
        def miss(o, m):
            return ((o - max_own) / own_tol) ** 2 + ((m - salary_used) / salary_tol) ** 2
        best = (miss(own, mean), beta, kappa, idx, mean, own)
        db, dk = 0.02, 0.5
        for _ in range(3):
            improved = False
            for b2, k2 in ((best[1] + db, best[2]), (best[1] - db, best[2]),
                           (best[1], best[2] + dk), (best[1], best[2] - dk)):
                if b2 <= 0.0:
                    continue
                i2, m2, o2 = draw(b2, k2)
                if miss(o2, m2) < best[0]:
                    best = (miss(o2, m2), b2, k2, i2, m2, o2)
                    improved = True
            if not improved:
                db, dk = db / 2.0, dk / 2.0
        _m, beta, kappa, idx, mean, own = best
    return beta, kappa, own, mean, _collision(idx), _dup_share(idx)


def _iso_ts(v):
    """DraftKings' start time as epoch seconds, or None. Their strings carry a
    trailing Z and sometimes seven fractional digits, which datetime refuses."""
    import datetime
    t = str(v or "")[:19]
    if not t:
        return None
    try:
        return int(datetime.datetime.fromisoformat(t).replace(
            tzinfo=datetime.timezone.utc).timestamp())
    except ValueError:
        return None


def pool_sig_rich(csv_text):
    """Everything about a DraftKings pool that can change what a board MEANS:
    the stable id where DraftKings gives one, name, team, game, position, the
    roster slot (a showdown export lists every man twice, CPT and FLEX, so
    both prices are rows of their own), the salary and the reported status.

    `pool_sig` deliberately hashes playable names only, so that a price tweak
    never costs the PC an hour of classic rebuild. On showdown that tradeoff
    is wrong twice over. The build is twenty minutes, not sixty; and the price
    IS the board -- a captain's price decides which of ~480,000 lineups are
    affordable at all, and a late $200 punt appearing changes the legal
    universe without changing anybody's name. A board built on Thursday's
    prices was being served on Sunday as though nothing had moved.

    Every field here is one the CSV reader actually produces: hashing a key
    the parser never sets would look sensitive and be blind, which is the
    failure this function exists to end.
    """
    import hashlib
    import nfl_dfs
    import simulate
    rows = []
    for c in simulate.parse_dk_csv(csv_text):
        if not nfl_dfs._playable(c):
            continue
        rows.append("|".join(str(x) for x in (
            c.get("dk_id") or "",
            c.get("name") or "",
            c.get("team") or "",
            c.get("game") or "",
            (c.get("pos") or "").upper(),
            (c.get("roster_pos") or "").upper(),
            int(float(c.get("salary") or 0)),
            (c.get("status") or "").strip().upper(),
        )))
    return hashlib.sha1("\n".join(sorted(rows)).encode()).hexdigest()[:16]


def pool_sig(csv_text):
    """Who is in a DraftKings pool -- the playable names only, so a salary
    tweak never triggers an hour of the PC and a player dropped (OUT) does.
    The adapters stamp it on the board; pc_worker compares it."""
    import hashlib
    import nfl_dfs
    import simulate
    names = sorted(c["name"] for c in simulate.parse_dk_csv(csv_text) if nfl_dfs._playable(c))
    return hashlib.sha1("\n".join(names).encode()).hexdigest()[:16]


def lineup_matrix(idx, P, cpt_mult=None):
    """(rows, P) float32 weights from (rows, k) player indices."""
    W = np.zeros((len(idx), P), dtype=np.float32)
    ar = np.arange(len(idx))
    for k in range(idx.shape[1]):
        W[ar, idx[:, k]] = 1.0
    if cpt_mult is not None:
        W[ar, idx[:, 0]] = cpt_mult
    return W


def status_classes(names):
    """{name: in | q | out} from Sleeper's roster (the depth-chart source):
    OUT / IR / doubtful / not Active -> out, Questionable -> q, else in. The
    rebuild trigger compares these across builds: a projection drifting is
    not a reason to spend the PC's hour, a starter turning doubtful is."""
    import nfl_dfs
    recs, norm = nfl_dfs._depth_records()
    out = {}
    for nm in names:
        cls = "in"
        if norm is not None and recs:
            key = norm(nm or "")
            rec = recs.get(key) or recs.get(key.replace(" ", ""))
            if rec is not None:
                inj = (rec.get("injury") or "").upper()
                if (rec.get("status") or "Active") != "Active" or rec.get("active") is False \
                        or inj in nfl_dfs._INJ_OUT:
                    cls = "out"
                elif inj == "QUESTIONABLE":
                    cls = "q"
        out[nm] = cls
    return out


def status_sig(names):
    """A stable digest of status_classes over `names` (sorted)."""
    import hashlib
    cls = status_classes(names)
    body = "\n".join(f"{nm}:{cls[nm]}" for nm in sorted(cls))
    return hashlib.sha1(body.encode()).hexdigest()[:16], cls


def _slot_rows(lineups, pos):
    """Sorted player-index tuples -> (rows, 9) in CL_SLOTS order: the third
    back or fourth receiver takes FLEX."""
    out = np.full((len(lineups), 9), -1, dtype=np.int32)
    for r, L in enumerate(lineups):
        rb, wr = [], []
        for i in L:
            p = pos[i]
            if p == "QB":
                out[r, 0] = i
            elif p == "TE":
                out[r, 6] = i
            elif p == "DST":
                out[r, 8] = i
            elif p == "RB":
                rb.append(i)
            else:
                wr.append(i)
        for k, i in enumerate(rb[:2]):
            out[r, 1 + k] = i
        for k, i in enumerate(wr[:3]):
            out[r, 3 + k] = i
        extra = rb[2:] + wr[3:]
        if extra:
            out[r, 7] = extra[0]
    return out


def _solver_pool(X, pos, gen, keep=_CL_KEEP):
    """The players the optimal-lineup solver may use: per position, the top
    `keep` by 90th-percentile points over the GENERATION worlds only. The
    first cut ranked on every world, so the evaluation worlds had a say in
    which players could form a candidate even though the solver itself
    never saw them; candidate construction must see only generation-world
    outputs (Sleeper's projections, salaries, positions and the depth chart
    are pre-simulation inputs and fine)."""
    Xg = X[:, gen.start:gen.stop] if len(gen) else X[:, :0]
    out = []
    for p in _CL_POS:
        ids = [i for i in range(len(pos)) if pos[i] == p]
        if Xg.shape[1]:
            ids.sort(key=lambda i: -float(np.percentile(Xg[i], 90)))
        out += ids[:keep[p]]
    return np.asarray(sorted(out), dtype=np.int64)


# Fixed probe lineups: exact nine-man lineups evaluated against every
# completed board whether or not this build's candidates happened to hold
# them, as diagnostic passengers only -- they never enter candidate
# generation, filtering, calibration, ranking or the portfolio. The two the
# owner entered on 2026-09-10 are permanent for that slate so every
# simulator version is compared on identical lineups: a fresh build's
# 144,708 candidates did not contain either.
PROBES = {
    ("nfl", 151307): [
        ["Jared Goff", "Jahmyr Gibbs", "Travis Etienne Jr.", "Amon-Ra St. Brown", "Chris Olave",
         "Devaughn Vele", "Tyler Warren", "Tre Tucker", "Steelers"],
        ["Geno Smith", "Jonathan Taylor", "Derrick Henry", "Zay Flowers", "Garrett Wilson",
         "Josh Downs", "Tyler Warren", "Breece Hall", "Raiders"],
    ],
}


def _norm_name(s):
    s = str(s or "").lower().replace(".", "").replace("'", "")
    for suf in (" jr", " sr", " iii", " ii", " iv"):
        if s.endswith(suf):
            s = s[:-len(suf)]
    return " ".join(s.split())


def probe_index(names, players):
    """(indices in slot order, missing names) for one probe against a pool:
    by normalised name, a defense by nickname or abbreviation."""
    by = {}
    for i, p in enumerate(players):
        by.setdefault(_norm_name(p.get("name")), []).append(i)
    out, missing = [], []
    for nm in names:
        c = by.get(_norm_name(nm)) or []
        if not c:
            key = _norm_name(nm)
            c = [i for i, p in enumerate(players) if p.get("pos") == "DST"
                 and (key in _norm_name(p.get("name")) or _norm_name(p.get("team")) == key)]
        if not c:
            missing.append(nm)
        else:
            out.append(int(c[0]))
    if missing or len(out) != 9 or len(set(out)) != 9:
        return None, missing or ["not nine distinct players"]
    return out, []


def probe_rows(probes, ents, Wf, wf, Xe, grids, contests, res, allowed, f_count, M, chunk=500):
    """Per contest, one row per probe: legality, salary, projection, the
    score distribution on the evaluation worlds, the tail metrics, the
    experimental first-place figures and the probe's rank among the
    evaluated candidates. Read-only over everything the build computed."""
    P = len(ents)
    pos = [e["pos"] for e in ents]
    out = {str(c["id"]): [] for c in contests}
    if not probes:
        return out
    rows, meta = [], []
    for names in probes:
        ix, missing = probe_index(names, ents)
        if ix is None:
            meta.append({"names": list(names), "available": False, "missing": missing})
            continue
        try:
            slot = _slot_rows([tuple(sorted(ix))], pos)[0]
        except Exception as e:                       # a shape the slots cannot hold
            errlog.note("TOURN-probe-shape", e)
            meta.append({"names": list(names), "available": False, "missing": ["roster shape"]})
            continue
        if (slot < 0).any():
            meta.append({"names": list(names), "available": False, "missing": ["roster shape"]})
            continue
        rows.append(slot)
        meta.append({"names": list(names), "available": True, "idx": slot})
    if not rows:
        for cid in out:
            out[cid] = [dict(m) for m in meta]
        return out
    idx_p = np.asarray(rows, dtype=np.int64)
    Wp = lineup_matrix(idx_p, P)
    resp, _o, _n = run_vs_field(Wp, Wf, wf, Xe, grids, chunk=chunk)
    legal = classic_allowed(idx_p, ents)
    legal4 = classic_allowed(idx_p, ents, max_per_game=4)
    keys_p = [tuple(r.tolist()) for r in np.sort(idx_p, axis=1)]
    ok = np.where(allowed)[0]
    S = np.stack([Xe[r].sum(axis=0) for r in idx_p])          # (probes, evaluation worlds)
    for gi, c in enumerate(contests):
        r, rp = res[gi], resp[gi]
        C = int(c.get("max_entries") or c.get("entered") or 10000)
        fee = float(c.get("entry_fee") or 1.0)
        rows_out = []
        j = 0
        for m in meta:
            if not m["available"]:
                rows_out.append({k: v for k, v in m.items() if k != "idx"})
                continue
            copies = float(C * f_count.get(keys_p[j], 0) / M) if M else 0.0

            def rank(v_all, v):
                return int((v_all[ok] > v).sum()) + 1 if len(ok) else None
            q = np.quantile(S[j], [0.1, 0.5, 0.9, 0.95, 0.99])
            lineup = [{"slot": CL_SLOTS[k], "name": ents[idx_p[j, k]]["name"], "pos": ents[idx_p[j, k]]["pos"],
                       "team": ents[idx_p[j, k]]["team"], "salary": ents[idx_p[j, k]]["salary"],
                       "depth": ents[idx_p[j, k]].get("depth"), "proj": ents[idx_p[j, k]]["proj"]} for k in range(9)]
            rows_out.append({
                "names": m["names"], "available": True, "lineup": lineup,
                "legal": bool(legal[j]), "legal_under_cap4": bool(legal4[j]),
                "salary": int(sum(p["salary"] for p in lineup)),
                "proj": round(float(sum(p["proj"] for p in lineup)), 1),
                "mean": round(float(S[j].mean()), 1), "median": round(float(q[1]), 1),
                "p10": round(float(q[0]), 1), "p90": round(float(q[2]), 1),
                "p95": round(float(q[3]), 1), "p99": round(float(q[4]), 1),
                "top1_pct": round(100.0 * float(rp["top1"][j]), 2),
                "top01_pct": round(100.0 * float(rp["top01"][j]), 3),
                "cash_pct": round(100.0 * float(rp["cash"][j]), 1),
                "win_pct": round(100.0 * float(rp["win_sole"][j]), 4),
                "win_any_pct": round(100.0 * float(rp["win"][j]), 4),
                "ev": round(float(rp["ev"][j]), 2), "ev_notie": round(float(rp["ev_notie"][j]), 2),
                "roi_pct": round(100.0 * (float(rp["ev"][j]) - fee) / fee, 1),
                "expected_copies": round(copies, 1),
                "rank": {"top1": rank(r["top1"], float(rp["top1"][j])),
                         "top01": rank(r["top01"], float(rp["top01"][j])),
                         "win": rank(r["win_sole"], float(rp["win_sole"][j])),
                         "of": int(len(ok))},
            })
            j += 1
        out[str(c["id"])] = rows_out
    return out


def world_split(n_worlds, opt_worlds):
    """(generation, evaluation) world ranges, disjoint: the first `opt_worlds`
    worlds -- at most a third of them -- find the hindsight-optimal
    candidates, and only the rest score every candidate. A lineup chosen
    because it won world j must not be paid for world j: with the two sets
    overlapping (the first build) optimal-world lineups' first-place rate
    read 1.9x their holdout rate while top 1% and top 0.1% did not move
    (measured 2026-09-10, 4,000 generation worlds of 20,000). 60,000 worlds
    at 20,000 requested: 20,000 generate, 40,000 evaluate."""
    n = max(0, int(n_worlds))
    gen = max(0, min(int(opt_worlds), n // 3))
    return range(0, gen), range(gen, n)


def simulator_stamp(model, n, seed=None, preseason=False):
    """The stamp of whichever game simulator made a board's worlds."""
    import nfl_dfs_sim
    if model == "constrained":
        import nfl_dfs_csim
        return nfl_dfs_csim.sim_stamp(n=n, seed=seed, preseason=preseason)
    return nfl_dfs_sim.sim_stamp(n=n, preseason=preseason)


def build_nfl_classic(dg, min_pool=1_000_000, contest_ids=None, n_sims=60000, n_worlds=None,
                      opt_worlds=20000, field_n=300000, cand_n=150000, cal_n=20000, chunk=500,
                      week=None, preseason=False, top=40, k_port=20, candidates=1200,
                      seed=None, log=print, probes=None, model="legacy"):
    """The tournament for a DraftKings classic slate, against every contest
    on it whose pool clears `min_pool` (or `contest_ids`). Returns the
    artifact the tab serves, or None when the slate, the contests or the
    pool cannot be read. `model` names the game simulator (nfl_dfs_sim
    .MODELS): "legacy" is production; "constrained" is the alternate mode
    under validation, and a board built on it says so in its stamp and its
    `model` field. `seed` seeds the field, the candidate draws and, under
    the constrained model, the worlds themselves."""
    if np is None:
        raise RuntimeError("numpy is required for the tournament")
    import dk
    import nfl_dfs
    import nfl_dfs_sim
    import simulate
    rng = np.random.default_rng(seed)
    t0 = time.time()
    slate = dk.slate_for("nfl", draft_group_id=int(dg))
    if not slate:
        return None
    rows = slate.get("contests") or []
    if contest_ids:
        want = [int(c) for c in contest_ids]
    else:
        want = [int(c["id"]) for c in rows if float(c.get("prize_pool") or 0) >= float(min_pool)]
    contests = []
    for cid in want[:6]:
        c = dk.contest_detail(cid)
        if c and c.get("payouts"):
            contests.append(c)
    if not contests:
        return None
    contests.sort(key=lambda c: -float(c.get("prize_pool") or 0))
    csv_players = [c for c in simulate.parse_dk_csv(slate["csv"]) if nfl_dfs._playable(c)]
    if week is None:
        try:
            import nfl_game_sim
            import nfl_preseason
            preseason = nfl_preseason.is_preseason()
            week = nfl_game_sim.current_week(preseason)
        except Exception as e:
            errlog.note("TOURN-week", e)
            week = 1
    log(f"[tourney] classic {dg}: {len(csv_players)} players, {len(contests)} contest(s) "
        f"{', '.join(c['name'][:40] for c in contests)}; simulating week {week} x {n_sims:,} ({model})...")
    pool = nfl_dfs_sim.player_pool(week, n=int(n_sims), preseason=preseason, model=model, seed=seed) or {}
    if not pool:
        return None
    nidx, norm = nfl_dfs._norm_index(pool)
    ents, excluded = [], []
    for c in csv_players:
        pos = (c.get("pos") or "").upper().split("/")[0]
        if pos not in _CL_POS:
            continue
        sim = nfl_dfs._pool_match(pool, c["name"], pos, c.get("team"), nidx, norm)
        if not (sim and sim.get("arr")):
            excluded.append({"name": c["name"], "pos": pos, "team": c.get("team"),
                             "why": "no projection this week"})
            continue
        ents.append({"name": c["name"], "pos": pos, "team": c.get("team"),
                     "opp": nfl_dfs._opp_of(c.get("team"), c.get("game")),
                     "game": nfl_dfs.game_key(c.get("game")),
                     "salary": int(c["salary"]), "proj": round(float(sim["proj"]), 1),
                     "ceiling": sim.get("ceiling"), "floor": sim.get("floor"), "arr": sim["arr"],
                     "rec_tgt": float(sim.get("rec_tgt") or 0.0)})
    by_name = {e["name"]: e for e in ents}
    ents, dx = nfl_dfs._apply_depth(ents, preseason)
    extra = []
    for d in dx:
        e = by_name.get(d["name"])
        if e is not None and "depth chart" in (d.get("why") or "") \
                and (e.get("proj") or 0) >= _FIELD_EXTRA_MIN_PROJ:
            e["_field_only"] = True
            e["depth"] = (d["why"].split(" ") or [""])[0]
            extra.append(e)
    extra.sort(key=lambda e: -e["proj"])
    extra = extra[:_CL_FIELD_KEEP]
    field_only = {e["name"] for e in extra}
    for d in dx:
        d["field"] = d["name"] in field_only
    excluded += dx
    ents = ents + extra
    have = {p: sum(1 for e in ents if e["pos"] == p and not e.get("_field_only")) for p in _CL_POS}
    if have["QB"] < 2 or have["RB"] < 4 or have["WR"] < 6 or have["TE"] < 2 or have["DST"] < 2:
        return None
    P = len(ents)
    N = min(len(e["arr"]) for e in ents)
    if n_worlds:
        N = min(N, int(n_worlds))
    X = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    sal = np.asarray([e["salary"] for e in ents])
    pos = [e["pos"] for e in ents]
    fo = np.asarray([bool(e.get("_field_only")) for e in ents])
    t1 = time.time()
    log(f"[tourney] classic {dg}: pool {P} players ({len(extra)} field-only) x {N:,} worlds "
        f"({t1 - t0:.0f}s); the best lineup of each of {min(N, opt_worlds):,} worlds...")
    # --- the hindsight-optimal lineup of every world (a pruned pool) ---
    gen, ev = world_split(N, opt_worlds)
    keep = _solver_pool(X, pos, gen)
    n_opt = len(gen)
    every = max(1, (n_opt // 4 // 64) * 64)
    opt_L, opt_v = optimal_lineups(
        X[keep][:, gen.start:gen.stop], sal[keep], [pos[i] for i in keep], chunk=64,
        progress=lambda done, n, dt: log(f"[tourney]   {done:,}/{n:,} worlds solved, {dt:.0f}s")
        if done % every == 0 or done == n else None)
    opt_L = [tuple(int(keep[i]) for i in L) for L in opt_L]
    opt_count = {}
    for L in opt_L:
        if L:
            opt_count[L] = opt_count.get(L, 0) + 1
    opt_player = np.zeros(P, dtype=np.int64)
    for L, k in opt_count.items():
        for i in L:
            opt_player[i] += k
    t2 = time.time()
    log(f"[tourney] classic {dg}: {len(opt_count):,} distinct optimal lineups over {n_opt:,} worlds "
        f"({t2 - t1:.0f}s); calibrating the field...")
    # --- the field ---
    beta, kappa, max_own, mean_sal, coll, top_share = calibrate_field(ents, rng, n=int(cal_n))
    rep_f = {}
    idx_f = classic_sample(ents, int(field_n), rng, beta, kappa, report=rep_f)
    check_completion(rep_f, "field")
    M = len(idx_f)
    Wf = lineup_matrix(idx_f, P)
    wf = np.full(M, 1.0 / M)
    field_pct = 100.0 * Wf.mean(axis=0)
    rows_f = np.sort(idx_f, axis=1)
    _uf, first_f, counts_f = np.unique(rows_f, axis=0, return_index=True, return_counts=True)
    f_count = {tuple(rows_f[i].tolist()): int(k) for i, k in zip(first_f, counts_f)}
    achieved = field_stats(idx_f, ents)
    t3 = time.time()
    log(f"[tourney] classic {dg}: field of {M:,} (beta {beta:.3f}, kappa {kappa:.3f}; achieved: top ownership "
        f"{100 * achieved['max_own']:.1f}% of {100 * CL_FIELD_MAX_OWN:.0f}%, mean salary ${achieved['mean_salary']:,.0f} "
        f"of ${CL_SALARY_USED:,.0f}, bring-back {100 * achieved['bring_back']:.1f}% of {100 * CL_BRING_BACK:.0f}%, "
        f"defense vs own QB {100 * achieved['dst_vs_own_qb']:.2f}% ({100 * CL_DST_VS_OWN_QB:.0f}% of the field does not avoid it); "
        f"collision {coll:.2e}, top build {100 * top_share:.3f}%) ({t3 - t2:.0f}s); our candidates...")
    # --- our candidates: every world's optimal (allowed or not) + rule-abiding draws ---
    idx_o = _slot_rows(list(opt_count.keys()), pos)
    rep_c = {}
    idx_c = classic_sample(ents, int(cand_n), rng, beta, kappa, rules=True, exclude=fo, report=rep_c)
    check_completion(rep_c, "candidate draws")
    idx_c = idx_c[classic_allowed(idx_c, ents)]
    # the public's three most common builds ride along (allowed or not) so
    # the chalk row on the sheet is the real chalk
    top_f = np.argsort(-counts_f)[:3]
    idx_chalk = _slot_rows([tuple(int(v) for v in _uf[i]) for i in top_f], pos)
    both = np.concatenate([idx_chalk, idx_o, idx_c], axis=0) if len(idx_o) else np.concatenate([idx_chalk, idx_c], axis=0)
    srt = np.sort(both, axis=1)
    _u, first = np.unique(srt, axis=0, return_index=True)
    idx_k = both[np.sort(first)]
    K = len(idx_k)
    allowed = classic_allowed(idx_k, ents)
    keys_k = [tuple(r.tolist()) for r in np.sort(idx_k, axis=1)]
    is_opt = np.asarray([opt_count.get(k, 0) for k in keys_k])
    copies_share = np.asarray([f_count.get(k, 0) / M for k in keys_k])
    Wc = lineup_matrix(idx_k, P)
    grids = [payout_grid(int(c.get("max_entries") or c.get("entered") or 10000),
                         c.get("payouts") or [], float(c.get("entry_fee") or 1.0),
                         int(c.get("places_paid") or 1)) for c in contests]
    t4 = time.time()
    log(f"[tourney] classic {dg}: {K:,} candidates ({int(allowed.sum()):,} we may enter, "
        f"{len(idx_o):,} hindsight-optimal) ({t4 - t3:.0f}s); scoring against the field in every world...")
    ch = chunk_for(max(K, M), chunk)
    Xe = np.ascontiguousarray(X[:, ev.start:ev.stop])      # the evaluation worlds only (world_split)
    every = max(ch, (len(ev) // 6 // ch) * ch)
    res, opt_c, _n = run_vs_field(
        Wc, Wf, wf, Xe, grids, chunk=ch,
        progress=lambda done, n, dt: log(f"[tourney]   {done:,}/{n:,} worlds, {dt:.0f}s")
        if done % every == 0 or done == n else None)
    t5 = time.time()
    ok = np.where(allowed)[0]
    cand = ok[np.argsort(-res[0]["top1"][ok])][:int(candidates)] if len(ok) else ok
    log(f"[tourney] classic {dg}: scored ({t5 - t4:.0f}s); portfolios of {k_port} per contest from "
        f"the {len(cand):,} strongest...")
    ports = portfolio_vs_field(Wc, Wf, wf, Xe, grids, cand, k_port, chunk=ch) if len(cand) else []
    t6 = time.time()

    def row(i, gi, rank_by):
        r = res[gi]
        c = contests[gi]
        C = int(c.get("max_entries") or c.get("entered") or 10000)
        fee = float(c.get("entry_fee") or 1.0)
        copies = float(C * copies_share[i])                 # np.float64 here leaked into the pickle
        lineup = [{"slot": CL_SLOTS[k], "name": ents[idx_k[i, k]]["name"], "pos": ents[idx_k[i, k]]["pos"],
                   "team": ents[idx_k[i, k]]["team"], "salary": ents[idx_k[i, k]]["salary"],
                   "depth": ents[idx_k[i, k]].get("depth"), "proj": ents[idx_k[i, k]]["proj"],
                   "field_pct": round(float(field_pct[idx_k[i, k]]), 1)} for k in range(9)]
        return {"lineup": lineup, "names": [p["name"] for p in lineup],
                "salary": int(sum(p["salary"] for p in lineup)),
                "proj": round(float(sum(p["proj"] for p in lineup)), 1),
                "win_pct": round(100.0 * float(r["win_sole"][i]), 4),
                "win_any_pct": round(100.0 * float(r["win"][i]), 4),
                "top1_pct": round(100.0 * float(r["top1"][i]), 2),
                "top01_pct": round(100.0 * float(r["top01"][i]), 3),
                "cash_pct": round(100.0 * float(r["cash"][i]), 1),
                "ev": round(float(r["ev"][i]), 2), "ev_notie": round(float(r["ev_notie"][i]), 2),
                "roi_pct": round(100.0 * (float(r["ev"][i]) - fee) / fee, 1),
                "expected_copies": round(float(copies), 1),
                "opt_worlds": int(is_opt[i]), "opt_pct": round(100.0 * float(is_opt[i]) / n_opt, 2),
                "allowed": bool(allowed[i]), "rank_by": rank_by}
    results = {}
    chalk_i = int(np.argmax(copies_share)) if K else None
    for gi, c in enumerate(contests):
        r = res[gi]
        chosen, p_any = ports[gi] if ports else ([], [])
        results[str(c["id"])] = {
            "contest": {k: c.get(k) for k in ("id", "name", "entry_fee", "prize_pool", "first_prize",
                                              "places_paid", "max_entries", "entered",
                                              "max_entries_per_user", "starts")},
            "top_win": [row(int(i), gi, "win") for i in ok[np.argsort(-r["win_sole"][ok])][:top]],
            "top_ev": [row(int(i), gi, "ev") for i in ok[np.argsort(-r["ev"][ok])][:top]],
            "top_top1": [row(int(i), gi, "top1") for i in ok[np.argsort(-r["top1"][ok])][:top]],
            "top_top01": [row(int(i), gi, "top01") for i in ok[np.argsort(-r["top01"][ok])][:top]],
            "portfolio": {"entries": [row(int(cand[j]), gi, "cover") for j in chosen],
                          "p_any_top1_pct": [round(100.0 * v, 1) for v in p_any]},
            "chalk": row(chalk_i, gi, "chalk") if chalk_i is not None else None,
        }
    # The probes come last and read only: the candidates, their ranking and
    # the portfolio above are finished before a probe is even resolved.
    if probes is None:
        probes = PROBES.get(("nfl", int(dg)), [])
    for cid, rows_p in probe_rows(probes, ents, Wf, wf, Xe, grids, contests, res, allowed,
                                  f_count, M, chunk=ch).items():
        results[cid]["probes"] = rows_p
    players_out = []
    for i, e in enumerate(ents):
        players_out.append({"name": e["name"], "pos": e["pos"], "team": e["team"], "opp": e.get("opp"),
                            "game": e.get("game"),
                            "salary": e["salary"], "depth": e.get("depth"),
                            "field_only": bool(e.get("_field_only")),
                            "proj": e["proj"], "floor": e.get("floor"), "ceiling": e.get("ceiling"),
                            "field_pct": round(float(field_pct[i]), 1),
                            "opt_pct": round(100.0 * float(opt_player[i]) / n_opt, 2)})
    players_out.sort(key=lambda p: -p["opt_pct"])
    pivotal = {e["name"] for e in sorted(ents, key=lambda e: -e["proj"])[:60]}
    for rs in results.values():
        for lst in ("top_win", "top_ev", "top_top1", "top_top01"):
            for rw in rs[lst][:10]:
                pivotal.update(rw["names"])
        for rw in rs["portfolio"]["entries"]:
            pivotal.update(rw["names"])
    try:
        st_sig, st_cls = status_sig(sorted(pivotal))
    except Exception as e:
        errlog.note("TOURN-status", e)
        st_sig, st_cls = None, {}
    return plain({"version": VERSION, "engine": ENGINE, "kind": "classic", "sport": "nfl", "draft_group_id": int(dg),
            "built_ts": int(time.time()), "status_sig": st_sig, "status": st_cls,
            "pool_sig": pool_sig(slate["csv"]),
            "contests": [results[str(c["id"])]["contest"] for c in contests],
            "slate": {"n_players": slate.get("n_players"), "dropped": slate.get("dropped"),
                      "games": len({(e["team"], e.get("opp")) for e in ents}) // 2,
                      "week": week, "preseason": bool(preseason), "field_only": sorted(field_only)},
            "sims": int(n_sims), "worlds": int(N), "opt_worlds": int(n_opt), "eval_worlds": int(len(ev)),
            "model": model, "seed": (int(seed) if seed is not None else None),
            "simulator": simulator_stamp(model, int(n_sims), seed, preseason),
            "probes_configured": int(len(probes)),
            "candidate_draws": rep_c,
            "max_per_game": CL_MAX_PER_GAME, "max_per_team": CL_MAX_PER_TEAM,
            # The first-place column is the share of evaluation worlds in
            # which the lineup beats every one of the field SAMPLE's
            # lineups: with 300,000 sampled against 832,000 real entries one
            # sampled exceedance takes a world from 1.0 to 0.06, so the
            # column is a count of clean sweeps of the sample, its level is
            # a property of the sample size, and two field seeds rank the
            # top 300 by it at Spearman 0.51 (measured 2026-09-10). EV and
            # ROI inherit it through the first prize. Kept for comparison
            # while the tail estimator is validated; the tab labels them.
            "experimental": money_gate(M, max((int(c.get("max_entries") or c.get("entered") or 0)) for c in contests)),
            "payout": {"ties": "DraftKings splits the tied positions' prizes equally; ev pays that rule "
                               "over the distribution of how many finish above and level (dfs_tourney.tie_payout)",
                       "win_pct": "first outright", "win_any_pct": "first or a share of it",
                       "ev_notie": "the same expected payout with ties ignored, for the size of the difference",
                       "bucket_pts": round(1.0 / _RES, 3), "tie_grid": len(grids[0]["E"]) if grids else None},
            "candidates": int(K), "candidates_allowed": int(allowed.sum()),
            "optimal_distinct": int(len(opt_count)),
            "field_model": {"kind": "sampled: softmax on projection and price, stack and bring-back "
                                    "rates from the public record",
                            "n": int(M), "beta": round(beta, 4), "kappa": round(kappa, 4),
                            "max_own_pct": round(100.0 * max_own, 1),
                            # dst_vs_own_qb_not_avoiding is the share of the field that does
                            # not steer round a defense facing its own QB; the achieved share
                            # that actually holds one is that times the softmax's pick rate
                            # (about 0.3% on today's pool), so the two are not compared 1:1.
                            "targets": {"max_own": CL_FIELD_MAX_OWN, "mean_salary": CL_SALARY_USED,
                                        "bring_back": CL_BRING_BACK, "dst_vs_own_qb_not_avoiding": CL_DST_VS_OWN_QB},
                            "achieved": achieved,
                            "completion": rep_f,
                            "collision": coll, "mean_salary": round(mean_sal),
                            "top_share_pct": round(100.0 * top_share, 4),
                            "stack_dist": list(CL_STACK_DIST), "bring_back": CL_BRING_BACK,
                            "note": ("the field is a sample of the public's builds: stack rates from "
                                     "Establish The Run's Milly Maker record, sharpness set so the "
                                     f"most-owned player sits at {100 * CL_FIELD_MAX_OWN:.0f}%. Dollar "
                                     "figures assume the sim is the truth; read the ranks.")},
            "rules": list(CL_RULES),
            "results": results,
            "players": players_out,
            "excluded": excluded[:60],
            "timings": {"sims_s": round(t1 - t0, 1), "optimal_s": round(t2 - t1, 1),
                        "field_s": round(t3 - t2, 1), "candidates_s": round(t4 - t3, 1),
                        "score_s": round(t5 - t4, 1), "portfolio_s": round(t6 - t5, 1),
                        "total_s": round(t6 - t0, 1)}})
