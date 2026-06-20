"""The internal odds generator + buy/sell signal logic.

Core idea
---------
A Kalshi crypto market resolves YES if, at the window close, the price is
above (or below) a chosen threshold. We treat the price as following a
Geometric Brownian Motion (GBM) -- the same assumption behind Black-Scholes.

From recent candles we estimate:
  - sigma : per-minute volatility (std-dev of log returns)
  - mu    : per-minute drift (mean log return), damped to avoid overfitting
            noisy short-term trends

Then over a horizon of N minutes to close, log(price) is approximately
Normal( log(S0) + mu*N , sigma^2 * N ). The probability the contract resolves
YES is just the normal CDF of being on the right side of the threshold.

This gives a principled "fair odds" number (a probability 0..1, equivalently
0..100 cents) that we compare against momentum and, optionally, the live Kalshi
YES price to produce a BUY YES / BUY NO / HOLD recommendation.
"""

import math
import statistics

# How much to trust raw short-term drift. Short windows produce wild drift
# estimates; we damp them so the model leans on volatility + threshold distance
# rather than chasing the last few green/red candles. 0 = ignore trend,
# 1 = full trend. 0.35 is a deliberately conservative middle ground.
DRIFT_DAMPING = 0.35

# Recommendation thresholds (in probability terms) when no live market price
# is supplied. Edge-based logic is used instead when a Kalshi YES price is given.
STRONG_PROB = 0.62
LEAN_PROB = 0.55

# Minimum modeled edge (in cents) over the live Kalshi price to call a buy.
MIN_EDGE_CENTS = 5.0


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _log_returns(closes):
    out = []
    for prev, cur in zip(closes, closes[1:]):
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def estimate_params(candles, lookback=120):
    """Estimate per-minute drift (mu) and volatility (sigma) from candles.

    candles: list of dicts with 'close' (oldest first). lookback caps how many
    recent candles we use. Returns (mu_per_min, sigma_per_min, n_used).
    """
    closes = [c["close"] for c in candles][-lookback:]
    rets = _log_returns(closes)
    if len(rets) < 5:
        return 0.0, 0.0, len(rets)
    sigma = statistics.pstdev(rets)
    mu = statistics.fmean(rets)
    return mu, sigma, len(rets)


def probability_yes(spot, threshold, direction, minutes_to_close, mu, sigma):
    """Probability the contract resolves YES under GBM.

    direction: 'above' (YES if price >= threshold) or 'below' (YES if price <= threshold).
    Returns a probability in [0, 1].
    """
    if minutes_to_close <= 0 or sigma <= 0 or spot <= 0 or threshold <= 0:
        # No uncertainty / no data: resolve deterministically by current spot.
        if direction == "above":
            return 1.0 if spot >= threshold else 0.0
        return 1.0 if spot <= threshold else 0.0

    drift = DRIFT_DAMPING * mu * minutes_to_close
    vol = sigma * math.sqrt(minutes_to_close)
    # d = standardized distance of log-threshold from expected log-price.
    d = (math.log(spot / threshold) + drift) / vol
    p_above = _norm_cdf(d)
    return p_above if direction == "above" else (1.0 - p_above)


def _prob_above(spot, strike, minutes_to_close, mu, sigma):
    """P(price >= strike at close) under GBM. Robust to degenerate inputs."""
    if minutes_to_close <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return 1.0 if spot >= strike else 0.0
    drift = DRIFT_DAMPING * mu * minutes_to_close
    vol = sigma * math.sqrt(minutes_to_close)
    d = (math.log(spot / strike) + drift) / vol
    return _norm_cdf(d)


def probability_yes_for_strike(spot, strike_type, floor, cap,
                               minutes_to_close, mu, sigma):
    """Probability a Kalshi market resolves YES, given its strike geometry.

    strike_type:
      'greater' / 'greater_or_equal' -> YES if price >= floor
      'less' / 'less_or_equal'       -> YES if price <= cap
      'between'                      -> YES if floor <= price <= cap
    """
    st = (strike_type or "").lower()
    if st in ("greater", "greater_or_equal"):
        return _prob_above(spot, floor, minutes_to_close, mu, sigma)
    if st in ("less", "less_or_equal"):
        return 1.0 - _prob_above(spot, cap, minutes_to_close, mu, sigma)
    if st == "between" and floor is not None and cap is not None:
        p_hi = _prob_above(spot, cap, minutes_to_close, mu, sigma)
        p_lo = _prob_above(spot, floor, minutes_to_close, mu, sigma)
        return max(0.0, p_lo - p_hi)
    # Unknown geometry: fall back to a coin flip rather than crashing.
    return 0.5


def momentum(candles, window=10):
    """Short-term momentum as a fractional change over the last `window` candles.

    Positive => price rising recently. Returns 0 if not enough data.
    """
    closes = [c["close"] for c in candles]
    if len(closes) < window + 1:
        return 0.0
    past = closes[-window - 1]
    now = closes[-1]
    if past <= 0:
        return 0.0
    return (now - past) / past


def compute_signal(spot, candles, threshold, direction, minutes_to_close,
                   yes_price_cents=None):
    """Produce the full odds + recommendation payload for a market.

    Returns a dict the UI can render directly.
    """
    mu, sigma, n = estimate_params(candles)
    prob_yes = probability_yes(spot, threshold, direction, minutes_to_close, mu, sigma)
    prob_yes = max(0.0, min(1.0, prob_yes))
    fair_yes = round(prob_yes * 100, 1)
    fair_no = round((1.0 - prob_yes) * 100, 1)

    mom = momentum(candles, window=10)
    mom_long = momentum(candles, window=30)

    # Volatility expressed as expected % move over the remaining window -- gives
    # the user intuition for how "live" the contract still is.
    expected_move_pct = sigma * math.sqrt(max(minutes_to_close, 0)) * 100 if sigma else 0.0

    # --- Dip / pump detection ------------------------------------------------
    # "Buy the dip": the model still favors YES, but price just dipped (so the
    # YES contract is likely cheap right now). Mirror for NO on a pump.
    dip_note = None
    if prob_yes >= LEAN_PROB and mom < -0.0008:
        dip_note = "Model favors YES but price just dipped — YES is likely cheap. Good dip-buy."
    elif prob_yes <= (1 - LEAN_PROB) and mom > 0.0008:
        dip_note = "Model favors NO but price just popped — NO is likely cheap. Good fade."

    # --- Recommendation ------------------------------------------------------
    edge = None
    if yes_price_cents is not None:
        # Edge-based: compare our fair value to the live Kalshi YES price.
        edge = round(fair_yes - float(yes_price_cents), 1)  # +ve => YES underpriced
        if edge >= MIN_EDGE_CENTS:
            rec, strength = "BUY YES", "strong" if edge >= 2 * MIN_EDGE_CENTS else "lean"
            rationale = (f"Fair YES ≈ {fair_yes}¢ vs market {yes_price_cents}¢ "
                         f"→ YES underpriced by {edge}¢.")
        elif edge <= -MIN_EDGE_CENTS:
            rec, strength = "BUY NO", "strong" if -edge >= 2 * MIN_EDGE_CENTS else "lean"
            rationale = (f"Fair YES ≈ {fair_yes}¢ vs market {yes_price_cents}¢ "
                         f"→ NO underpriced by {-edge}¢.")
        else:
            rec, strength = "HOLD", "flat"
            rationale = (f"Fair YES ≈ {fair_yes}¢ is within {MIN_EDGE_CENTS}¢ of "
                         f"market {yes_price_cents}¢ — no clear edge.")
    else:
        # Probability-based when no live market price is provided.
        if prob_yes >= STRONG_PROB:
            rec, strength = "BUY YES", "strong"
            rationale = f"Model gives {fair_yes}% chance of YES — strong lean."
        elif prob_yes >= LEAN_PROB:
            rec, strength = "BUY YES", "lean"
            rationale = f"Model gives {fair_yes}% chance of YES — mild lean."
        elif prob_yes <= (1 - STRONG_PROB):
            rec, strength = "BUY NO", "strong"
            rationale = f"Model gives {fair_no}% chance of NO — strong lean."
        elif prob_yes <= (1 - LEAN_PROB):
            rec, strength = "BUY NO", "lean"
            rationale = f"Model gives {fair_no}% chance of NO — mild lean."
        else:
            rec, strength = "HOLD", "flat"
            rationale = f"Model near a coin-flip ({fair_yes}% YES) — wait for a clearer edge."

    # --- Exit / take-profit hint --------------------------------------------
    # Since Kalshi lets you sell anytime, suggest a target to lock profit.
    exit_hint = None
    if rec == "BUY YES":
        target = min(95, round(fair_yes + 8))
        exit_hint = f"If YES rises toward ~{target}¢, consider selling to lock profit."
    elif rec == "BUY NO":
        target = min(95, round(fair_no + 8))
        exit_hint = f"If NO rises toward ~{target}¢, consider selling to lock profit."

    # Confidence: more data + meaningful distance from 50% => more confidence.
    data_conf = min(1.0, n / 60.0)
    edge_conf = abs(prob_yes - 0.5) * 2.0
    confidence = round(100 * (0.4 * data_conf + 0.6 * edge_conf))

    return {
        "spot": round(spot, 2),
        "threshold": threshold,
        "direction": direction,
        "minutes_to_close": round(minutes_to_close, 2),
        "prob_yes": round(prob_yes, 4),
        "fair_yes_cents": fair_yes,
        "fair_no_cents": fair_no,
        "yes_price_cents": yes_price_cents,
        "edge_cents": edge,
        "recommendation": rec,
        "strength": strength,
        "rationale": rationale,
        "dip_note": dip_note,
        "exit_hint": exit_hint,
        "momentum_pct": round(mom * 100, 3),
        "momentum_long_pct": round(mom_long * 100, 3),
        "volatility_per_min_pct": round(sigma * 100, 4),
        "expected_move_pct": round(expected_move_pct, 3),
        "drift_per_min_pct": round(mu * 100, 5),
        "confidence": confidence,
        "samples": n,
    }


def sell_guidance(side, entry_cost, fair_yes_cents, fair_no_cents,
                  yes_bid=None, no_bid=None, minutes_to_close=None):
    """When to sell a held position.

    side: 'YES' or 'NO' you bought. entry_cost: what you paid (cents).
    fair_*_cents: the model's fair value of each side now (= its win probability
    in cents, i.e. the expected payout if you hold to the close).
    yes_bid/no_bid: what you'd actually receive selling right now (cents).

    Rule of thumb: your side settles at 100¢ if it wins, 0¢ if it loses, so its
    "hold value" is its fair value. If you can sell for >= fair value, the market
    is paying you more than it's worth -> take it. If the sale price is below
    fair value, holding is worth more -> keep it.
    """
    side = (side or "").upper()
    fair = fair_yes_cents if side == "YES" else fair_no_cents
    sell_price = (yes_bid if side == "YES" else no_bid)
    estimated = sell_price is None
    if estimated:
        sell_price = fair  # no live bid (e.g. manual market): use fair as a proxy

    pnl = round(sell_price - entry_cost, 1) if entry_cost is not None else None
    pnl_pct = round(100 * pnl / entry_cost, 1) if (pnl is not None and entry_cost) else None

    # Core decision: compare what you can sell for now vs. its hold value (fair).
    if sell_price >= fair - 1:
        action = "SELL"
        if pnl is None:
            headline = "Sell — the edge is gone"
        elif pnl >= 0:
            headline = f"Sell now — lock in +{pnl}¢ profit"
        else:
            headline = f"Sell / cut — edge gone, trim the {abs(pnl)}¢ loss"
        detail = (f"You can sell your {side} for ~{sell_price}¢, and the model's fair "
                  f"value is only {fair}¢ — little left to gain by holding.")
    else:
        action = "HOLD"
        upside = round(fair - sell_price, 1)
        if pnl is None:
            headline = "Hold — still underpriced"
        elif pnl >= 0:
            headline = f"Hold — up {pnl}¢, still has edge"
        else:
            headline = f"Hold — down {abs(pnl)}¢, model still likes it"
        detail = (f"Model fair value is {fair}¢ but you'd only get ~{sell_price}¢ selling "
                  f"now ({upside}¢ of upside left). Hold, or sell only if you want to "
                  f"de-risk.")

    # Settlement context when the close is near.
    settle_note = None
    if minutes_to_close is not None and minutes_to_close <= 10:
        if fair >= 75:
            settle_note = f"Close in ~{round(minutes_to_close)}m and {side} is likely winning (≈{fair}¢) — holding to settlement should pay ~100¢."
        elif fair <= 25:
            settle_note = f"Close in ~{round(minutes_to_close)}m and {side} is likely losing (≈{fair}¢) — it may settle at 0¢. Selling now salvages value."

    return {
        "side": side,
        "entry_cost_cents": entry_cost,
        "fair_value_cents": fair,
        "sell_price_cents": round(sell_price, 1),
        "sell_price_estimated": estimated,
        "pnl_cents": pnl,
        "pnl_pct": pnl_pct,
        "action": action,
        "headline": headline,
        "detail": detail,
        "settle_note": settle_note,
    }


def kalshi_signal(spot, candles, market, minutes_to_close):
    """Edge signal for a live Kalshi market (from kalshi.get_open_markets).

    Compares the model's fair value to the market's live YES/NO ask prices and
    recommends whichever side (if any) is underpriced by at least MIN_EDGE_CENTS.
    """
    mu, sigma, n = estimate_params(candles)
    prob_yes = max(0.0, min(1.0, probability_yes_for_strike(
        spot, market["strike_type"], market["floor"], market["cap"],
        minutes_to_close, mu, sigma)))
    fair_yes = round(prob_yes * 100, 1)
    fair_no = round((1 - prob_yes) * 100, 1)

    yes_ask = market.get("yes_ask")
    no_ask = market.get("no_ask")
    # Edge = our fair value minus what we'd pay to enter. Positive => underpriced.
    edge_yes = round(fair_yes - yes_ask, 1) if yes_ask is not None else None
    edge_no = round(fair_no - no_ask, 1) if no_ask is not None else None

    best_side, best_edge = None, None
    if edge_yes is not None and edge_yes >= MIN_EDGE_CENTS:
        best_side, best_edge = "YES", edge_yes
    if edge_no is not None and edge_no >= MIN_EDGE_CENTS and (best_edge is None or edge_no > best_edge):
        best_side, best_edge = "NO", edge_no

    mom = momentum(candles, window=10)
    if best_side == "YES":
        rec = "BUY YES"
        strength = "strong" if best_edge >= 2 * MIN_EDGE_CENTS else "lean"
        rationale = f"Fair YES ≈ {fair_yes}¢ vs ask {yes_ask}¢ → +{best_edge}¢ edge."
        dip_note = ("Price just dipped — YES looks extra cheap here."
                    if prob_yes >= LEAN_PROB and mom < -0.0008 else None)
    elif best_side == "NO":
        rec = "BUY NO"
        strength = "strong" if best_edge >= 2 * MIN_EDGE_CENTS else "lean"
        rationale = f"Fair NO ≈ {fair_no}¢ vs ask {no_ask}¢ → +{best_edge}¢ edge."
        dip_note = ("Price just popped — NO looks extra cheap here."
                    if prob_yes <= (1 - LEAN_PROB) and mom > 0.0008 else None)
    else:
        rec, strength = "HOLD", "flat"
        rationale = "No side offers a clear edge over the live market price."
        dip_note = None

    return {
        "prob_yes": round(prob_yes, 4),
        "fair_yes_cents": fair_yes,
        "fair_no_cents": fair_no,
        "edge_yes_cents": edge_yes,
        "edge_no_cents": edge_no,
        "recommendation": rec,
        "strength": strength,
        "rationale": rationale,
        "dip_note": dip_note,
        "momentum_pct": round(mom * 100, 3),
        "samples": n,
    }
