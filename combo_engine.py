"""Parlay selection that can see the price.

The old selector maximized ONE thing: the parlay's simulated probability. Kalshi
prices were looked up afterwards, purely to decorate the finished slip. So when
two slips were equally likely, it had no way to prefer the cheaper one, and it
would happily build a 70% leg that costs 78c over a 68% leg that costs 55c. The
first is a guaranteed long-run loser and the second is the bet you want. Measured
on a live 11-game slate: 3,442 legs carried a real Kalshi quote and 1,255 of them
(36%) were +EV after the taker fee. Which ones you got was luck.

This module puts the price INSIDE the search. The dynamic program tracks cost as
well as probability, so the frontier it produces spans both, and the objective
picks a point on that frontier:

  safe      max P(win). What the old selector did. Still right when you just want
            the likeliest slip and you accept paying up for it.
  value     max EV. The bet that makes money in the long run, which is often not
            the likeliest one.
  balanced  max P(win) among slips that are not -EV. The default: the highest
            chance of cashing that isn't quietly paying a premium to get there.

WHAT THIS DOES NOT DO. It does not treat cross-game legs as correlated, because
they measurably are not. Day-level clustering of MLB outcomes over 2023-25
(7,287 games, 543 slate-days) is indistinguishable from zero -- ICC -0.005,
+0.016, -0.008 for game totals and -0.004, -0.006, +0.000 for home wins. There is
no shared "hot day" to exploit or defend against, so the product across games is
right and this module leaves it alone. Correlation WITHIN a game is real, large,
and already handled upstream by reading joint hit-rates off the sim masks
(mlb_sim.game_bundles).

Fees are charged on the way in, per leg, because that is how Kalshi charges them:
a 5-leg slip pays five taker fees, and a parlay that looks +2% gross can be -3%
net. `net=True` (the default) prices every leg at ask+fee.
"""

import math
import os

# Kalshi's taker fee, as a fraction of the contract, is fee_rate * p * (1-p)
# rounded up to the cent. baseball._kalshi_fee owns the exact formula; this
# module asks it rather than keeping a second copy that can drift.
_MIN_PRICE_C = 1
_MAX_PRICE_C = 99

# The ceiling on what a Kalshi combo pays. A combo settles at $1.00 per contract,
# so the multiple you collect is 1/price and the ceiling is set by the cheapest
# price a combo can be quoted at.
#
# This number is NOT in the API. /multivariate_event_collections publishes
# size_min, size_max and the resolution rules and nothing about price or payout,
# and the help centre only says a combo "pays out a maximum of $1.00 per contract"
# -- which is the per-contract settlement, not the multiple.
#
# 435 is measured, not documented: building combos out of the cheapest legs the
# exchange offers tops out at 435x every time, however the legs are chosen. That
# implies a minimum combo price of about 0.23c, which is not a round number in
# cents and is why it cannot be inferred from the tick size either. It stays an
# empirical constant in one named place, movable by env var if the exchange
# changes it.
MAX_PAYOUT_X = float(os.getenv("VIGIL_MAX_PAYOUT_X") or 435)

OBJECTIVES = ("balanced", "safe", "value")

# An EV-driven objective must be mostly BETTABLE. Unpriced legs are charged at
# fair value (EV-neutral), so without this a slip could post a healthy EV earned
# by one priced leg while the rest have no Kalshi market to place at all.
MIN_PRICED_FRAC = 0.75


def spread_label(team, by, unit="runs"):
    """A spread/run-line leg named the way the BOOK names it.

    Kalshi's ticker carries an integer and its title carries the line, and the
    two are not the same number:

        KXMLBSPREAD-...-PIT4   "Pittsburgh wins by over 3.5 runs"   floor 3.5
        KXNFLSPREAD-...-ARI10  "Arizona wins by over 9.5 points"    floor 9.5

    The slip was built off the ticker, so it read "PIT win by 4+" against a board
    showing 3.5. Identical events -- a margin of 4+ IS a margin over 3.5 when
    runs are whole numbers -- but the reader has to do that conversion on every
    leg, and it makes the top of the ladder look like a line that does not
    exist: pre-game Kalshi books 1.5/2.5/3.5 and nothing above, so "win by 4+"
    reads like a 4.5 that is not on the board until the game goes live.

    `by` accepts either form. An integer is a ticker and becomes ticker - 0.5;
    a half number is already a line and is left alone."""
    b = float(by)
    line = b - 0.5 if abs(b - round(b)) < 1e-9 else b
    txt = f"{line:g}"
    return f"{team} by over {txt} {unit}".rstrip()


def _fee_cents(c):
    try:
        import baseball
        return baseball._kalshi_fee(c)
    except Exception:
        return 0.0


def leg_cost(price_cents, net=True):
    """What one leg actually costs, as a fraction of its $1 payout, or None if the
    leg has no usable quote.

    A quote at or above 100c is Kalshi saying there is no offer -- counted as a
    price it would cost a full stake and pay 1.0x, which is a strictly dominated
    leg, so it is treated as unpriced rather than as a 100c fill."""
    if price_cents is None:
        return None
    c = float(price_cents)
    if not (_MIN_PRICE_C <= c <= _MAX_PRICE_C):
        return None
    if net:
        c = min(99.9, c + _fee_cents(c))
    return c / 100.0


def bundle_cost(legs, net=True):
    """(cost, n_priced, n_total) for a bundle of legs.

    Unpriced legs are charged at their FAIR value (1/prob), which makes them
    exactly EV-neutral. That is the honest handling of "we could not find a
    market": it neither rewards a leg for being unpriced nor punishes it, so the
    objective ranks on the part of the slip we can actually see. `n_priced` rides
    along so a slip built mostly out of unpriced legs can say so."""
    cost, priced = 1.0, 0
    for l in legs:
        # A quote nobody can fill is not a price. `fillable` is set alongside the
        # blend (see blend_candidates); legs without it fall through to fair value
        # exactly as if no market existed.
        c = (leg_cost(l.get("price_cents", l.get("market_cents")), net=net)
             if l.get("fillable", True) else None)
        if c is None:
            p = l.get("marg") or l.get("prob") or 0.0
            if not (0 < p < 1):
                return None, priced, len(legs)
            # An unfillable leg that still shows an ASK is charged at the WORSE
            # of that ask and fair value. Charging plain fair was EV-neutral --
            # which stopped a thin book from manufacturing edge, but it also
            # advertised a payout the asks would not give: a live slip showed
            # 1.79x / +1.4% EV while filling its one thin leg at the visible ask
            # returned 1.75x / -0.7%. The max() keeps both protections at once:
            # a thin leg can never ADD edge (its EV contribution is min(0, real))
            # and can never inflate the payout past what a fill would pay.
            thin_ask = (leg_cost(l.get("price_cents", l.get("market_cents")),
                                 net=net)
                        if not l.get("fillable", True) else None)
            if thin_ask is not None:
                cost *= max(thin_ask, p)    # pessimistic: worst of ask and fair
                continue
            cost *= p                       # no ask at all -> fair, EV neutral
        else:
            cost *= c
            priced += 1
    return cost, priced, len(legs)


# --- market blending ---------------------------------------------------------
# Chasing the biggest model-vs-market gap selects for the legs where the MODEL is
# most wrong, not the legs where the market is. That is the winner's curse, and
# on this model it is not hypothetical -- it was measured twice:
#
#   * Totals. On an 11-game slate the sim was above the market's implied Over
#     probability in 10 of 10 games, median +9.9pp (p ~ 0.002 under no bias).
#     The market's median total tracked the 2023-25 league mean of 8.97 runs; the
#     sim averaged ~10.0. The sim scores too many runs.
#   * Batter props. 1,738 graded outcomes: legs quoted .55-.85 hit .583 against a
#     .666 average quote, CI [.530, .633]. Same direction, same cause -- more
#     simulated runs means more simulated hits, bases and H+R+RBI.
#
# The moneyline is nearly unbiased (median +0.6pp vs market), which fits: a
# run-level error largely cancels in a margin but not in a level. So a raw
# "biggest edge" search would have loaded up on Overs and hitter props, priced
# them off a number that is 9pp too high, and called the result value.
#
# The market is the corrective. It is not always right, but it is a competing
# estimator that demonstrably tracked reality where ours did not, so a leg's
# working probability is a PRECISION-WEIGHTED blend of the two in log-odds space.
# How much the market gets is set by how good that market is -- a penny-wide
# moneyline with real volume is a strong opinion, an untraded prop with a 14c
# spread is barely one -- so a genuinely stale line still leaves room for an edge,
# while a liquid market mostly overrules us.
# THESE WEIGHTS WERE SET FOR A MODEL THAT WAS BIASED, AND THE BIAS IS NOW FIXED.
# _MARKET_K was chosen when the sim ran ~9pp hot on totals and props (a one-sided
# home-field multiplier inflating every run total by 4%; see baseball.HOME_RUNS_MULT).
# Re-measured after that fix, across 857 legs with a usable two-sided quote:
#
#     model - market, pre-blend    median -0.075pp   mean -0.041pp
#     per market: Total +0.1, Run line +0.1, HRR +0.1, Hit -0.2, ML +0.7, Ks -2.0
#     how far the blend then moves it   median +0.06pp
#
# The model now agrees with the market on average, so the blend is close to a
# no-op in aggregate and is left exactly as it is. But note what it still does
# per leg: it keeps ~29% of the weight on us, which shrinks a GENUINE disagreement
# by about 70% along with a spurious one. That is the right trade only while we
# have no evidence that our disagreements pay.
#
# Getting that evidence is now possible and was not before: store.MODEL_VERSION
# retires calibration data from the old model, so graded outcomes accruing under
# version 2 measure THIS model. Once there are enough of them, check whether legs
# where we disagreed with the market beat it, and raise the model's weight if they
# did. Do not raise it on the strength of the bias fix alone -- unbiased on average
# is not the same as informative leg by leg.
_MARKET_K = 3.0          # precision of an ideal (zero-spread, deep) market
_SPREAD_WIDE = 20.0      # cents of spread at which a market says nothing
_DEPTH_HALF = 40.0       # contracts of volume for half the depth credit
_ONE_SIDED_QUALITY = 0.20  # an ask with no bid is weak evidence, not none
_MIN_ASK_SIZE = 10.0       # contracts at the ask before a leg counts as fillable
_MIN_TRADED = 25.0         # or this much lifetime volume + open interest

# Hard ceiling on how far a leg may end up from the market's own midpoint.
#
# Quality-weighting alone leaves a hole: on a THIN market the model keeps most of
# the weight, and the model is the estimator with the measured bias. A live slate
# produced "Max Clark 2+ H+R+RBI" at 82.8% against a 54c market -- a 29pp claim,
# on one untraded prop, that by itself pushed a 4-leg slip to +40% EV. No exchange
# leaves 40% lying on a baseball prop; that number was our error wearing an edge's
# clothes.
#
# 10pp is set at roughly the size of the bias we have actually measured in this
# model (~8-9pp on both props and totals). The reasoning: we will believe we have
# found an edge up to about as large as our own known error, and past that the
# more likely explanation is that we are the ones who are wrong. Genuine stale
# lines still get through -- 10pp on a 54c market is an enormous edge -- while the
# fantasies get clipped.
_MAX_EDGE = 0.10


def _clamp_to_market(p, mid):
    """Keep a blended probability within _MAX_EDGE of the market's midpoint."""
    return max(mid - _MAX_EDGE, min(mid + _MAX_EDGE, p))

# How much this model is trusted per market. The table below is the PRIOR --
# hand-set from where each model leans on stable team/pitcher rates vs
# low-base-rate props -- and it is now confronted with the graded record:
# calibrate.blend_weight fits, per market type, the weight that would have
# minimised log-loss over that model's own graded picks against the de-vigged
# close it was logged next to. Where the floors are met (150 graded across 10
# days) the fitted weight takes over, shrunk toward the prior by sample size.
#
# This cuts both ways, which is the point. The raw totals model BEAT the close
# over hundreds of graded picks and was being flattened to a ~0.19 weight by
# the prior anyway -- every real edge it found displayed as "+1". Ks kept a
# 0.7-trust prior through a measured losing stretch. A fixed table cannot be
# right in both directions; a fitted one is whatever the record says.
_MODEL_TRUST = {"ML": 1.0, "Total": 0.7, "Ks": 0.7, "Run line": 0.7,
                "RFI": 0.5, "Hit": 0.5, "Bases": 0.5, "SB": 0.4,
                "HR": 0.35, "HRR": 0.35, "RBI": 0.35}
# candidate type -> the predlog bucket its graded record lives under
_TRUST_BUCKET = {"ML": "mlb", "Total": "mlb_total", "Ks": "mlb_ks",
                 "Run line": "mlb_runline", "RFI": "mlb_rfi", "Hit": "mlb_hit",
                 "Bases": "mlb_bases", "SB": "mlb_sb", "HR": "mlb_hr",
                 "HRR": "mlb_hrr", "RBI": "mlb_rbi", "Extras": "mlb_extras"}
_Q_REF = 0.6            # the market quality the fitted weight is anchored at
_BLEND_SHRINK_N = 300   # graded rows for the fit to fully displace the prior
_tau_cache = {}


def _effective_tau(typ):
    """The trust the blend actually uses: the fitted weight where earned, the
    hand-set prior where not, converted back into the tau the quality-weighted
    formula expects (w = tau / (tau + K*qual))."""
    import time as _t2
    hit = _tau_cache.get(typ)
    if hit and _t2.time() - hit[1] < 21600:
        return hit[0]
    tau_prior = _MODEL_TRUST.get(typ, 0.6)
    tau = tau_prior
    try:
        bucket = _TRUST_BUCKET.get(typ)
        fit = None
        if bucket:
            import calibrate
            fit = calibrate.blend_weight(bucket)
        if fit:
            w_fit, n = fit
            w_prior = tau_prior / (tau_prior + _MARKET_K * _Q_REF)
            lam = min(1.0, n / float(_BLEND_SHRINK_N))
            w = lam * w_fit + (1.0 - lam) * w_prior
            w = min(max(w, 0.02), 0.90)          # never all-market, never all-model
            tau = _MARKET_K * _Q_REF * w / (1.0 - w)
            tau = min(max(tau, 0.05), 4.0)
    except Exception:
        tau = tau_prior
    _tau_cache[typ] = (tau, _t2.time())
    return tau


def market_reference(q):
    """(reference probability 0-1, quality 0-1) for a quote, or (None, 0).

    Two-sided, the reference is the MID and quality comes from the spread and
    depth. One-sided -- an ask with nobody bidding -- it is the ASK at a low fixed
    quality. That case used to return quality 0, which meant the model kept ALL
    the weight and skipped the edge clamp entirely, and it was exactly where the
    worst claims lived: a live slate offered "Max Clark 2+ H+R+RBI" at 54c with no
    bid, and the model's 83% sailed through untouched. But someone standing there
    willing to SELL at 54c is still saying something about fair value. Not much,
    which is why the quality is low -- but not nothing, which is why it is no
    longer zero."""
    if not q:
        return None, 0.0
    ask = q.get("ask")
    if ask is None or not (0 < ask < 100):
        return None, 0.0
    spread = q.get("spread")
    if q.get("bid") is None or spread is None:
        return ask / 100.0, _ONE_SIDED_QUALITY
    if spread > _SPREAD_WIDE:
        # A lopsided book's midpoint is not a fair value. Live example: bid 4c,
        # ask 54c on a prop with one contract of depth and no volume -- the "mid"
        # of 29c is an artifact of two unrelated resting orders. The ASK is the
        # one real number there (it is what a buy would pay), so it becomes the
        # reference, at zero quality: it does not pull the blend, but it still
        # caps how large an edge the leg may claim.
        return ask / 100.0, 0.0
    tight = max(0.0, 1.0 - spread / _SPREAD_WIDE)
    depth = (q.get("vol") or 0.0) + (q.get("oi") or 0.0)
    deep = depth / (depth + _DEPTH_HALF)
    # A tight market with no volume still counts for something (someone is
    # quoting it two-sided), so depth scales the credit rather than gating it.
    return (q["mid"] / 100.0), max(0.0, min(1.0, tight * (0.45 + 0.55 * deep)))


def tradeable(q):
    """Can this leg actually be filled in a size worth betting?

    An edge you cannot transact is not an edge. The 54c prop above offered ONE
    contract at the ask against zero lifetime volume: buying "it" moves the price
    into your own order and there is no second contract behind it. Legs that fail
    this are charged at fair value in the EV math -- treated exactly like a leg
    with no market at all -- so they can still appear in a slip but can never be
    the reason a slip claims to be +EV."""
    if not q:
        return False
    if (q.get("size") or 0) >= _MIN_ASK_SIZE:
        return True
    # A market with real trading history counts even if the book is thin right
    # now -- depth comes and goes, a traded market is still a market.
    return ((q.get("vol") or 0) + (q.get("oi") or 0)) >= _MIN_TRADED


def market_quality(q):
    """0..1 — how much this quote deserves to be believed."""
    return market_reference(q)[1]


def blend_prob(p_model, q, typ):
    """Precision-weighted blend of the model and the market mid, in log-odds.

    Returns (p_used, weight_on_model, quality). With no usable quote this is the
    model unchanged and a weight of 1.0."""
    if p_model is None or not (0 < p_model < 1):
        return p_model, 1.0, 0.0
    mid, qual = market_reference(q)
    if mid is None or not (0 < mid < 1):
        return p_model, 1.0, 0.0
    # NOTE the missing `qual <= 0` bail. Zero quality means the market does not
    # get to PULL the number -- w below comes out at 1.0 on its own, leaving the
    # model intact -- but it must still CAP it. Returning early here (as this did)
    # skipped the clamp precisely on the worthless markets where the model runs
    # furthest, which is how an 85% claim against a 54c ask survived untouched.
    tau_m = _effective_tau(typ)
    w = tau_m / (tau_m + _MARKET_K * qual)
    z = w * math.log(p_model / (1 - p_model)) + (1 - w) * math.log(mid / (1 - mid))
    z = max(-12.0, min(12.0, z))
    return _clamp_to_market(1.0 / (1.0 + math.exp(-z)), mid), w, qual


def blend_candidates(cands, quotes):
    """Replace each candidate's marginal with its market-blended one, in place.

    `quotes` maps id(cand) -> quote dict. The ORIGINAL model number is kept as
    `marg_model` so the UI can show both, and `marg` becomes the number every
    downstream consumer already uses -- which means the existing joint-rescaling
    in mlb_sim.game_bundles (joint * prod(marg/raw), capped by the smallest
    marginal) carries the blend into the bundle's joint probability with no
    changes there. That machinery was built for calibration and is exactly the
    right shape for this."""
    for c in cands:
        p = c.get("marg")
        q = quotes.get(id(c))
        used, w, qual = blend_prob(p, q, c.get("type"))
        c["marg_model"] = p
        c["marg"] = used
        c["model_weight"] = round(w, 3)
        c["market_quality"] = round(qual, 3)
        c["fillable"] = tradeable(q)
    return cands


def ev(prob, cost):
    """Expected return per $1 staked. 0.0 = break even, +0.10 = +10%."""
    if not cost or cost <= 0:
        return None
    return prob / cost - 1.0


def kelly(prob, cost):
    """Kelly fraction of bankroll for a binary bet at this price.

    Included because it is the honest answer to "how much" once EV is known, and
    because it separates two slips with the same EV: the one with the higher win
    probability is the one you can bet more of your roll on. Never negative --
    a -EV slip's Kelly is zero, not a short."""
    if not cost or cost <= 0 or not (0 < prob < 1):
        return 0.0
    b = 1.0 / cost - 1.0                    # net odds received on a win
    if b <= 0:
        return 0.0
    f = (prob * b - (1 - prob)) / b
    return max(0.0, min(1.0, f))


# --- the frontier ------------------------------------------------------------
# dp[(n_legs, cost_bucket)] = [(log_prob, log_cost, selection), ...]
#
# Keying on COST (not probability, as the old DP did) is the whole change. The
# old key could only answer "what is the likeliest slip at n legs"; this one
# answers "what is the likeliest slip at n legs FOR THIS PRICE", which is the
# question an objective needs to trade the two off.
#
# Each cell holds the Pareto set over (prob, cost), not a single max-prob
# state. A cell is ~5% wide in cost, and near break-even that width straddles
# the EV=0 line: a slip at 78.8% / 79.8c (-1.3% EV) lands in the same cell as
# one at 77.7% / 77.6c (+0.1% EV), and keeping only the higher-prob state
# deletes the only +EV slip in the region -- the balanced objective then
# skips the whole neighbourhood and settles for a far less likely slip.
# (Brute-force enumeration on a live slate caught exactly this: 29% chosen,
# 78% available.) Dropping a state only when a cellmate is at least as likely
# AND at least as cheap AND at least as priced can never lose the optimum for
# any objective that wants probability high, cost low and the slip fillable --
# which every objective here does. The priced count is part of the dominance
# test because an unpriced leg is charged fair value: a slip carrying one can
# look identical on (prob, cost) to an all-priced slip yet fail the
# priced_frac gate the EV objectives apply, so it must not eclipse one.
_COST_RES = 0.05


def frontier(games_bundles, max_total_legs=8, net=True):
    """Every efficient (legs, cost) combination and the likeliest slip at each.

    games_bundles: [(label, [bundle, ...], suffix), ...] as built by
    mlb_sim.game_bundles -- one entry per game, at most one bundle taken from
    each. Each bundle is {"size": k, "prob": joint, "legs": [cand, ...]}.
    """
    dp = {(0, 0): [(0.0, 0.0, 0, 0, [])]}
    for gi, entry in enumerate(games_bundles):
        bundles = entry[1]
        priced = []
        for b in bundles:
            nl0 = b["size"]
            p = b.get("prob") or 0.0
            if not (0 < p <= 1):
                continue
            c, bpr, btt = bundle_cost(b["legs"], net=net)
            if c is None or c <= 0:
                continue
            priced.append((nl0, math.log(p), math.log(c), bpr, btt, b))
        if not priced:
            continue
        nd = {k: list(v) for k, v in dp.items()}
        for (legs, _bk), cell in dp.items():
            for (lp, lc, pr, tt, sel) in cell:
                for nl0, blp, blc, bpr, btt, b in priced:
                    nl = legs + nl0
                    if nl > max_total_legs:
                        continue
                    nlp, nlc = lp + blp, lc + blc
                    key = (nl, int(round(nlc / _COST_RES)))
                    tgt = nd.get(key)
                    if tgt is None:
                        nd[key] = [(nlp, nlc, pr + bpr, tt + btt,
                                    sel + [(gi, b)])]
                        continue
                    npr = pr + bpr
                    keep, dominated = [], False
                    for st in tgt:
                        if st[0] >= nlp and st[1] <= nlc and st[2] >= npr:
                            dominated = True
                            break
                        if not (nlp >= st[0] and nlc <= st[1]
                                and npr >= st[2]):
                            keep.append(st)
                    if not dominated:
                        keep.append((nlp, nlc, npr, tt + btt,
                                     sel + [(gi, b)]))
                        nd[key] = keep
        dp = nd
    out = []
    for (legs, _bk), cell in dp.items():
        if legs < 2:
            continue
        for (lp, lc, pr, tt, sel) in cell:
            if not sel:
                continue
            prob, cost = math.exp(lp), math.exp(lc)
            out.append({"legs": legs, "prob": prob, "cost": cost,
                        "payout": (1.0 / cost) if cost > 0 else None,
                        "fair_payout": (1.0 / prob) if prob > 0 else None,
                        "ev": ev(prob, cost),
                        # Unpriced legs are charged at fair value, so they move
                        # EV by exactly nothing -- which means a slip can post a
                        # healthy EV earned entirely by one priced leg while
                        # three others have no Kalshi market at all. That is not
                        # a bet you can place, so the fraction that IS priced
                        # travels with the state.
                        "priced": pr, "n_quotes": tt,
                        "priced_frac": (pr / tt) if tt else 0.0,
                        "sel": sel})
    return out


def choose(states, objective="balanced", legs_target=None, payout_target=None,
           legs_mode="prefer", payout_mode="off", conn="or", min_ev=0.0):
    """Pick one point off the frontier.

    The leg-count and payout targets keep their existing three-way semantics --
    "require" is a hard filter, "prefer" only breaks ties, "off" is ignored --
    and `conn` says whether two hard requirements are AND or OR. The objective
    then orders whatever survives.
    """
    if not states:
        return None, {}
    want_legs = legs_mode in ("require", "prefer") and bool(legs_target)
    want_payout = (payout_mode in ("require", "prefer")
                   and bool(payout_target and payout_target > 1))
    X, Y = legs_target or 0, payout_target or 0

    meets_legs = lambda s: s["legs"] == X
    # Judged on the FAIR payout, matching the number the slip displays as its
    # target. The market payout is what you are actually paid and is reported
    # separately; targeting it instead would silently chase mispriced legs.
    meets_payout = lambda s: (s["fair_payout"] or 0) >= Y

    # Hard requirements, in the order they are given up. A leg count is an exact
    # structural target a slate can nearly always hit; a payout is a ">=" that may
    # be physically unreachable (three legs at a 55% floor cannot pay 20x -- the
    # arithmetic caps at about 6x). So when the two cannot both be satisfied, the
    # leg count is the one that survives.
    reqs = []
    if legs_mode == "require" and legs_target:
        reqs.append(("legs", meets_legs))
    if payout_mode == "require" and want_payout:
        reqs.append(("payout", meets_payout))
    feasible, hard_ok, unmet = states, True, []
    if reqs:
        combine = all if conn == "and" else any
        got = [s for s in states if combine(fn(s) for _n, fn in reqs)]
        if got:
            feasible = got
        else:
            # Unsatisfiable TOGETHER is not a licence to ignore both. Dropping
            # straight to "best effort over everything" is what turned a hard
            # "3 legs AND 20x payout" into an 8-leg slip: neither requirement
            # bound any more, and the ranking tier then preferred the 8-legger
            # because it was the one that reached the payout. Narrow by each
            # requirement that IS satisfiable instead, in priority order, so the
            # ones we can honour still bind and only the impossible one is
            # reported as missed.
            hard_ok = False
            for name, fn in reqs:
                sub = [s for s in feasible if fn(s)]
                if sub:
                    feasible = sub
                else:
                    unmet.append(name)

    pool, ev_ok = feasible, None
    if objective in ("balanced", "value"):
        # An EV claim is only worth acting on if most of the slip has a real
        # market behind it, so both EV-driven objectives require that too.
        # Highest chance of cashing among slips that are not -EV. If the whole
        # slate is priced against us, say so rather than pretending: fall back to
        # the safest slip and flag it, so "no +EV slip exists today" is visible
        # instead of being silently rounded into a recommendation.
        ev_ok = True
        keep = [s for s in feasible
                if s["ev"] is not None and s["priced_frac"] >= MIN_PRICED_FRAC
                and (objective == "value" or s["ev"] >= min_ev)]
        if keep:
            pool = keep
        else:
            ev_ok = False

    def rank(s):
        tier = ((1 if want_payout and meets_payout(s) else 0)
                + (1 if want_legs and meets_legs(s) else 0))
        # When nothing on the slate is fillable enough to make an EV claim, the
        # EV ordering is ranking noise -- and ranking BY it surfaces the worst of
        # it, because the largest "edges" are the legs with no real market at all.
        # A live slate did exactly this: the value fallback proudly returned a
        # slip at +138% EV built entirely from unquoted legs. So when the gate
        # fails, every objective falls back to the likeliest slip and says so.
        if objective == "value" and ev_ok:
            main = s["ev"] if s["ev"] is not None else -9.9
        else:
            main = s["prob"]
        # Chasing an unmet payout target, reach for the bigger payout first.
        if want_payout and not meets_payout(s):
            return (tier, s["fair_payout"] or 0, main)
        return (tier, main, s["ev"] if s["ev"] is not None else 0.0)

    best = max(pool, key=rank)
    return best, {"hard_ok": hard_ok, "ev_ok": ev_ok, "objective": objective,
                  "legs_met": meets_legs(best) if want_legs else None,
                  "payout_reached": meets_payout(best) if want_payout else None,
                  # Which hard requirements could not be met at all, so the UI can
                  # name the one that failed instead of shrugging at both.
                  "unmet": unmet,
                  # The best payout actually available at the leg count we were
                  # held to -- the honest answer to "20x wasn't possible, so what
                  # was?" without making the user re-run with a lower target.
                  # Measured over `pool`, not `feasible`: under `balanced` the
                  # -EV slips were never selectable, so quoting their payout here
                  # would advertise a number this objective refused to pick.
                  "best_payout_at_legs": (
                      round(max((s["fair_payout"] or 0) for s in pool), 2)
                      if pool else None),
                  "n_states": len(states), "n_feasible": len(feasible)}


def max_bet(states, cap=None):
    """The slip most likely to cash among those that still collect the full cap.

    A different question from `choose`, and it needs a different answer. Kalshi
    pays a combo at most `cap`x, so payout past the ceiling is thrown away: a
    900x slip and a 435x slip pay you exactly the same money, and the 435x one
    can be several times likelier to get there. "Biggest payout on the board" is
    therefore the wrong target. The right one is: of the slips that reach the
    ceiling, take the one that most often pays.

    Two things this insists on that `choose` does not:

    * The MARKET payout (1/cost), not the fair payout. `choose` deliberately
      judges its payout target on the fair number so it isn't dragged around by
      mispriced legs. Here the cap is a property of what Kalshi actually hands
      over, so the market number is the only one that means anything.
    * Every leg priced. Unpriced legs are charged at fair value upstream, which
      makes them EV-neutral and invisible in the cost -- so a slip can "reach
      cap" through legs that have no market to buy. That is a number, not a bet.

    Returns (state, meta) like `choose`, so callers reuse the same plumbing.
    """
    cap = float(cap or MAX_PAYOUT_X)
    if not states:
        return None, {}
    priced = [s for s in states
              if s.get("payout") and s.get("priced_frac", 0.0) >= 1.0]
    # Falling back to partly-unpriced slips is worth doing -- an empty answer is
    # useless -- but it changes what the number means, so it travels in the meta
    # and the slip is expected to say so.
    pool, all_priced = (priced, True) if priced else (
        [s for s in states if s.get("payout")], False)
    if not pool:
        return None, {}
    # Bound what the slip as a whole may claim over the market. Kept as a
    # fallback rather than a hard filter: if literally nothing clears it, the
    # honest move is to return the least-inflated slip and flag it, not to
    # pretend the board is empty.
    sane = [s for s in pool
            if s["prob"] * s["payout"] <= MAX_BET_TOTAL_OPTIMISM]
    optimism_ok = bool(sane)
    if sane:
        pool = sane

    at_cap = [s for s in pool if s["payout"] >= cap]
    if at_cap:
        # Among slips that all collect the same capped payout, probability is the
        # only thing left to want. Ties break toward the smaller overshoot, which
        # is the slip wasting least of its theoretical payout on the ceiling.
        best = max(at_cap, key=lambda s: (s["prob"], -s["payout"]))
    else:
        # Nothing on the board reaches it. "This slate tops out at 47x" is a real
        # answer; returning None and letting the UI say "no combo" is not.
        best = max(pool, key=lambda s: (s["payout"], s["prob"]))

    collected = min(best["payout"], cap)
    return best, {
        "max_bet": True,
        "cap_x": round(cap, 2),
        "cap_reached": bool(at_cap),
        "collected_x": round(collected, 2),
        # What the slip would pay with no ceiling, and how much of that the
        # ceiling eats. Both only interesting when there IS an overshoot.
        "uncapped_payout_x": round(best["payout"], 2),
        "overshoot_x": (round(best["payout"] - cap, 2)
                        if best["payout"] > cap else None),
        # EV against the payout you are actually handed, not the one the
        # multiplication implies. Above the cap these diverge sharply, and the
        # capped number is the true one.
        "capped_ev_pct": round((best["prob"] * collected - 1.0) * 100, 1),
        "best_payout_x": round(max(s["payout"] for s in pool), 2),
        "all_legs_priced": all_priced,
        # What the MARKET thinks this slip's chance is -- the product of the
        # prices you pay. Shown next to our own number because on a capped
        # ticket the two can differ by a lot, and the user is entitled to see the
        # disagreement rather than only the half of it we happen to believe.
        "market_prob_pct": round(best["cost"] * 100, 3) if best.get("cost") else None,
        "optimism_x": round(best["prob"] * best["payout"], 2),
        "optimism_ok": optimism_ok,
        "n_states": len(states), "n_feasible": len(pool),
    }


# Per-leg floors a max bet is tried at. Reaching the MARKET payout cap needs
# either many legs or unlikely ones, so a 55% floor usually cannot get there at
# all -- but simply dropping the floor to the basement is not the answer either,
# because the candidate pipeline is lossy by design: _pool trims to ~22 legs a
# game and game_bundles keeps only the safest few, the longest few and the best
# correlating few. Which legs survive that trim depends on the floor, so the
# reachable payout is NOT monotone in it. Measured on an 8-game slate:
#
#     floor 50%, <=12 legs -> best payout 8.39x
#     floor 40%, <=16 legs -> best payout 3.07x     <- lower floor, worse reach
#     floor 25%, <=20 legs -> 327.9x
#
# A single floor therefore makes the button a coin flip. Sweeping a few and
# keeping the best is what makes "the best slip that reaches the cap" true.
MAX_BET_FLOORS = (45, 35, 25, 15)


# How much more likely than the market a leg may be and still be stacked into a
# max bet. A RATIO, not a gap in points, because a max bet multiplies prices and
# it is relative optimism that compounds. Measured on a live tennis board: the
# model/market gap is small in absolute terms (median 2.4pp, max 22.1pp over 84
# priced legs) and looks harmless -- but the cheapest leg was 2c against a model
# probability of 16.6%, an eight-fold overstatement, and two of those multiplied
# into a "384x at 4.5%, +1334% EV" slip. A points threshold cannot see that; 16.6
# vs 2.0 is a 14.6pp gap, which is unremarkable. A ratio can.
MAX_BET_OPTIMISM = 2.0

# Above this a leg cannot earn its place in a max bet. Every leg you add
# multiplies the payout by 1/price and the probability by p, and since the payout
# is capped the only thing left to protect is probability -- so a leg is worth
# adding only if it buys meaningfully more payout than it costs in chance. A 99c
# leg multiplies the payout by 1.001 and the probability by 0.834, which is a
# terrible trade in every direction, and one turned up in a live tennis slip
# because the cost-bucketed DP is coarse enough to lose the state that dominates
# it. Cheaper to exclude the useless legs than to fight the bucketing.
MAX_BET_LEG_CENTS = 95


def stackable(prob, price_cents, k=MAX_BET_OPTIMISM):
    """May this leg go into a max bet? One-sided on purpose.

    A leg the model likes far MORE than the market is the one a payout-seeking
    search reaches for -- cheap and, according to us, likely -- and it is exactly
    where the model is least trustworthy, because deep longshots are the hardest
    region to calibrate. A leg the model likes LESS than the market needs no
    guard: it lowers the slip's probability, so the search discards it anyway.
    """
    if not price_cents or not (0 < price_cents <= MAX_BET_LEG_CENTS):
        return False
    return prob <= k * (price_cents / 100.0)


# A per-leg bound cannot see compounding, and compounding is the whole problem.
# With MAX_BET_OPTIMISM alone, a live tennis board returned six legs that each
# sat just inside it -- 45.8% against 25c, 38.1% against 22c, 36.6% against 22c,
# 36.2% against 27c -- and the slip claimed +451% EV, because four legs at ~1.7x
# the market's probability multiply to roughly 8x. Every leg was individually
# defensible and the product was fantasy.
#
# So the SLIP is bounded too. prob/cost is exactly the total optimism ratio (and
# exactly EV+1), so one number covers it: a max bet may claim at most this much
# more than the market thinks it is worth.
MAX_BET_TOTAL_OPTIMISM = 3.0


def _mb_key(it):
    """Order two max-bet slips. Reaching the ceiling beats not reaching it; among
    those that reach it the likelier one wins; among those that don't, the one
    that got closest."""
    reached = bool(it.get("cap_reached"))
    return (1 if reached else 0,
            (it.get("combined_prob_pct") or 0.0) if reached
            else (it.get("uncapped_payout_x") or 0.0))


def best_max_bet(build, floors=MAX_BET_FLOORS):
    """Best max bet across several per-leg floors. `build(floor_pct)` returns a
    finished slip or None; see MAX_BET_FLOORS for why one floor is not enough.

    The floors tried travel back on the slip, because "we looked at four and this
    was the best" is a materially different claim from "this is what 25% gave us"
    and the UI should be able to tell the truth about which one it is."""
    best, tried = None, []
    for f in floors:
        try:
            it = build(f)
        except Exception:
            continue
        if not it or not isinstance(it, dict) or not it.get("n_legs"):
            continue
        tried.append({"floor_pct": f, "reached": bool(it.get("cap_reached")),
                      "payout_x": it.get("uncapped_payout_x"),
                      "prob_pct": it.get("combined_prob_pct")})
        if best is None or _mb_key(it) > _mb_key(best):
            best = it
    if best is not None:
        best["max_bet_floors_tried"] = tried
    return best


# One user input -- "the number I want to win" -- and everything else is the
# optimizer's problem: the leg count, the per-leg confidence, which games. The
# per-leg floor still exists mechanically (it trims the candidate pool before
# the frontier), so the honest way to free it is to SWEEP it: a 3x target lives
# among 70-90% legs, a 100x target needs 25% legs, and no single floor serves
# both. Three floors cover the range; the frontier + EV gate decide the rest.
OPTIMAL_FLOORS = (55, 35, 15)


def _opt_key(it):
    """Order two optimal-mode slips. Reaching the payout target beats not;
    among those that reach it, EV-viable beats EV-gated-out, then the likelier
    slip wins; among those that miss, the one whose payout got closest."""
    reached = bool(it.get("payout_reached"))
    return (1 if reached else 0,
            1 if it.get("ev_ok") is not False else 0,
            (it.get("combined_prob_pct") or 0.0) if reached
            else (it.get("fair_payout_x") or 0.0),
            it.get("ev_pct") or 0.0)


def best_target(build, floors=OPTIMAL_FLOORS):
    """Best slip for a hard payout target across several per-leg floors.
    `build(floor_pct)` returns a finished slip or None. Mirrors best_max_bet:
    the floors tried travel back on the slip so the UI can say "looked at
    three pools and kept this one" rather than implying one pass decided."""
    best, tried = None, []
    for f in floors:
        try:
            it = build(f)
        except Exception:
            continue
        if not it or not isinstance(it, dict) or not it.get("n_legs"):
            continue
        tried.append({"floor_pct": f, "reached": bool(it.get("payout_reached")),
                      "payout_x": it.get("fair_payout_x"),
                      "prob_pct": it.get("combined_prob_pct")})
        if best is None or _opt_key(it) > _opt_key(best):
            best = it
    if best is not None:
        best["optimal_floors_tried"] = tried
    return best


def compare(states, chosen, **kw):
    """What the other objectives would have picked, for the slip's own diagnostics.

    This is the honest way to show the price mattered: the same frontier, ranked
    three ways. If `safe` and `balanced` land on the same slip, the user learns
    the likeliest slip was also fairly priced today -- which is worth knowing.

    `kw` MUST carry the same targets the real pick used. Called without them the
    comparison silently answers a different question -- an unconstrained frontier
    versus a 4-leg one -- and then reports "safe would have picked something else"
    when the only difference was the constraint.
    """
    out = {}
    kw.pop("objective", None)
    for obj in OBJECTIVES:
        s, _m = choose(states, objective=obj, **kw)
        if not s:
            continue
        out[obj] = {"legs": s["legs"],
                    "prob_pct": round(s["prob"] * 100, 1),
                    "payout_x": round(s["payout"], 2) if s["payout"] else None,
                    "ev_pct": round(s["ev"] * 100, 1) if s["ev"] is not None else None,
                    "priced_frac": round(s["priced_frac"], 2),
                    "same_as_chosen": bool(chosen and s["sel"] == chosen["sel"])}
    return out
