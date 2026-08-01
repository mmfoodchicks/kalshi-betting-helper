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

# Kalshi's taker fee, as a fraction of the contract, is fee_rate * p * (1-p)
# rounded up to the cent. baseball._kalshi_fee owns the exact formula; this
# module asks it rather than keeping a second copy that can drift.
_MIN_PRICE_C = 1
_MAX_PRICE_C = 99

OBJECTIVES = ("balanced", "safe", "value")

# An EV-driven objective must be mostly BETTABLE. Unpriced legs are charged at
# fair value (EV-neutral), so without this a slip could post a healthy EV earned
# by one priced leg while the rest have no Kalshi market to place at all.
MIN_PRICED_FRAC = 0.75


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
            cost *= p                       # fair -> EV neutral
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

# How much this model is trusted per market, from where it leans on stable
# team/pitcher rates vs low-base-rate props. Mirrors baseball._edge_confidence.
_MODEL_TRUST = {"ML": 1.0, "Total": 0.7, "Ks": 0.7, "Run line": 0.7,
                "RFI": 0.5, "Hit": 0.5, "Bases": 0.5, "SB": 0.4,
                "HR": 0.35, "HRR": 0.35}


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
    tau_m = _MODEL_TRUST.get(typ, 0.6)
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
# dp[(n_legs, cost_bucket)] = (log_prob, log_cost, selection)
#
# Keying on COST (not probability, as the old DP did) is the whole change. The
# old key could only answer "what is the likeliest slip at n legs"; this one
# answers "what is the likeliest slip at n legs FOR THIS PRICE", which is the
# question an objective needs to trade the two off.
_COST_RES = 0.05


def frontier(games_bundles, max_total_legs=8, net=True):
    """Every efficient (legs, cost) combination and the likeliest slip at each.

    games_bundles: [(label, [bundle, ...], suffix), ...] as built by
    mlb_sim.game_bundles -- one entry per game, at most one bundle taken from
    each. Each bundle is {"size": k, "prob": joint, "legs": [cand, ...]}.
    """
    dp = {(0, 0): (0.0, 0.0, 0, 0, [])}
    for gi, entry in enumerate(games_bundles):
        bundles = entry[1]
        nd = dict(dp)
        for (legs, _bk), (lp, lc, pr, tt, sel) in dp.items():
            for b in bundles:
                nl = legs + b["size"]
                if nl > max_total_legs:
                    continue
                p = b.get("prob") or 0.0
                if not (0 < p <= 1):
                    continue
                c, bpr, btt = bundle_cost(b["legs"], net=net)
                if c is None or c <= 0:
                    continue
                nlp, nlc = lp + math.log(p), lc + math.log(c)
                key = (nl, int(round(nlc / _COST_RES)))
                cur = nd.get(key)
                if cur is None or nlp > cur[0]:
                    nd[key] = (nlp, nlc, pr + bpr, tt + btt, sel + [(gi, b)])
        dp = nd
    out = []
    for (legs, _bk), (lp, lc, pr, tt, sel) in dp.items():
        if legs < 2 or not sel:
            continue
        prob, cost = math.exp(lp), math.exp(lc)
        out.append({"legs": legs, "prob": prob, "cost": cost,
                    "payout": (1.0 / cost) if cost > 0 else None,
                    "fair_payout": (1.0 / prob) if prob > 0 else None,
                    "ev": ev(prob, cost),
                    # Unpriced legs are charged at fair value, so they move EV by
                    # exactly nothing -- which means a slip can post a healthy EV
                    # earned entirely by one priced leg while three others have no
                    # Kalshi market at all. That is not a bet you can place, so
                    # the fraction that IS priced travels with the state.
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

    reqs = []
    if legs_mode == "require" and legs_target:
        reqs.append(meets_legs)
    if payout_mode == "require" and want_payout:
        reqs.append(meets_payout)
    feasible, hard_ok = states, True
    if reqs:
        combine = all if conn == "and" else any
        got = [s for s in states if combine(r(s) for r in reqs)]
        if got:
            feasible = got
        else:
            hard_ok = False                 # unsatisfiable -> best effort over all

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
                  "n_states": len(states), "n_feasible": len(feasible)}


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
