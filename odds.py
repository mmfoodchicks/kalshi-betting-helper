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
#
# 0.35 was NOT conservative enough, and a flat damping factor was the wrong shape
# of guard. mu is the mean of ~120 one-minute log returns -- two hours of data --
# and it was being extrapolated across the whole horizon, up to 19 hours for a
# daily market. The estimate's own noise grows as sqrt(N/n) relative to the real
# volatility over that horizon:
#
#     horizon    60 min  ->  drift-noise / vol = 0.71x
#     horizon   240 min  ->                      1.41x
#     horizon  1151 min  ->                      3.10x
#
# Measured live on 2026-08-01 with a SOL daily market 19 hours from settlement:
#
#     coin   t(mu)   drift@19h   vol@19h   |drift|/vol
#     BTC    +1.04     +0.83%      0.74%      1.13
#     ETH    +1.37     +1.99%      1.33%      1.49
#     SOL    +2.08     +3.38%      1.49%      2.27
#
# So the trend guess was displacing the centre of the distribution by more than
# twice the entire uncertainty of the outcome. With spot at $72.58 the model
# priced "SOL >= $73" at 96.97% against a market at 34.5c, and "SOL >= $74" at
# 83.24% against 9.5c -- its 50/50 point sat near $75, three percent above spot.
# Those became +61pp and +73pp "edges" at the top of the Best Bets board.
#
# Two guards replace the single constant, because there are two distinct
# questions: is there a trend at all, and even if there is, should it be allowed
# to outweigh the uncertainty?
DRIFT_DAMPING = 0.35

# 1. Is the trend real? Shrink mu toward zero by its own signal-to-noise ratio
#    t = mu / SE(mu), SE = sigma/sqrt(n). Weight t^2/(1+t^2) is ~0 for a trend
#    indistinguishable from noise and approaches 1 only for a genuinely large,
#    well-measured one. At the t values above (1.0-2.1) this keeps 52-81%.
# 2. Even a real trend must not dominate. The total drift is capped at this
#    fraction of the horizon's own volatility.
#
# THE CAP IS ZERO, because the measurement said so. Both guards were first tried
# at a 0.25-sigma cap, and then checked against 25 near-the-money Kalshi crypto
# markets that had a tight two-sided book (spread <= 8c, >= 20 min to close),
# scoring each model against the market's own midpoint:
#
#     model                        median |err|   mean bias   max err
#     old  (0.35 * mu * N)             28.1pp      +17.8pp     73.8pp
#     t-shrunk, capped at 0.25 sigma    4.2pp       +4.0pp     13.7pp
#     ZERO DRIFT                        1.8pp       +0.3pp      7.9pp
#
# A driftless GBM tracks the market to under 2pp with essentially no bias. Every
# amount of trend we added made it worse. That is what an efficient short-horizon
# market looks like: there is no exploitable drift to estimate, only the noise in
# trying. The machinery below stays because it is the correctly-shaped guard IF
# evidence for drift ever appears -- raise VIGIL_DRIFT_VOL_CAP and the shrinkage
# still protects against the noise -- but it ships off.
import os as _os

DRIFT_VOL_CAP = float(_os.environ.get("VIGIL_DRIFT_VOL_CAP") or 0.0)


def damped_drift(mu, sigma, n, minutes):
    """Total log-price drift to apply over `minutes`, after both guards.

    Returns 0.0 whenever the inputs cannot support a trend estimate at all."""
    if minutes <= 0 or sigma <= 0 or n < 5 or not mu:
        return 0.0
    se = sigma / math.sqrt(n)
    if se <= 0:
        return 0.0
    t = mu / se
    signal = (t * t) / (1.0 + t * t)
    drift = DRIFT_DAMPING * signal * mu * minutes
    cap = DRIFT_VOL_CAP * sigma * math.sqrt(minutes)
    return max(-cap, min(cap, drift))

# Recommendation thresholds (in probability terms) when no live market price
# is supplied. Edge-based logic is used instead when a Kalshi YES price is given.
STRONG_PROB = 0.62
LEAN_PROB = 0.55

# Minimum modeled edge (in cents) over the live Kalshi price to call a buy.
MIN_EDGE_CENTS = 5.0


def taker_fee_cents(cents):
    """Expected Kalshi taker fee per contract — see kalshi.taker_fee_cents.

    Rounded to 0.1c: this feeds displayed crypto edges. The rest of the app nets
    the fee out of every displayed edge; crypto must too."""
    import kalshi
    return round(kalshi.taker_fee_cents(cents), 1)


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


def probability_yes(spot, threshold, direction, minutes_to_close, mu, sigma, n=0):
    """Probability the contract resolves YES under GBM.

    direction: 'above' (YES if price >= threshold) or 'below' (YES if price <= threshold).
    Returns a probability in [0, 1].
    """
    if minutes_to_close <= 0 or sigma <= 0 or spot <= 0 or threshold <= 0:
        # No uncertainty / no data: resolve deterministically by current spot.
        if direction == "above":
            return 1.0 if spot >= threshold else 0.0
        return 1.0 if spot <= threshold else 0.0

    drift = damped_drift(mu, sigma, n, minutes_to_close)
    vol = sigma * math.sqrt(minutes_to_close)
    # d = standardized distance of log-threshold from expected log-price.
    d = (math.log(spot / threshold) + drift) / vol
    p_above = _norm_cdf(d)
    return p_above if direction == "above" else (1.0 - p_above)


def _prob_above(spot, strike, minutes_to_close, mu, sigma, n=0):
    """P(price >= strike at close) under GBM. Robust to degenerate inputs."""
    if minutes_to_close <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return 1.0 if spot >= strike else 0.0
    drift = damped_drift(mu, sigma, n, minutes_to_close)
    vol = sigma * math.sqrt(minutes_to_close)
    d = (math.log(spot / strike) + drift) / vol
    return _norm_cdf(d)


def probability_yes_for_strike(spot, strike_type, floor, cap,
                               minutes_to_close, mu, sigma, n=0):
    """Probability a Kalshi market resolves YES, given its strike geometry.

    strike_type:
      'greater' / 'greater_or_equal' -> YES if price >= floor
      'less' / 'less_or_equal'       -> YES if price <= cap
      'between'                      -> YES if floor <= price <= cap
    """
    st = (strike_type or "").lower()
    if st in ("greater", "greater_or_equal"):
        return _prob_above(spot, floor, minutes_to_close, mu, sigma, n)
    if st in ("less", "less_or_equal"):
        return 1.0 - _prob_above(spot, cap, minutes_to_close, mu, sigma, n)
    if st == "between" and floor is not None and cap is not None:
        p_hi = _prob_above(spot, cap, minutes_to_close, mu, sigma, n)
        p_lo = _prob_above(spot, floor, minutes_to_close, mu, sigma, n)
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
    prob_yes = probability_yes(spot, threshold, direction, minutes_to_close, mu, sigma, n)
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
        dip_note = "Model favors YES but price just dipped - YES is likely cheap. Good dip-buy."
    elif prob_yes <= (1 - LEAN_PROB) and mom > 0.0008:
        dip_note = "Model favors NO but price just popped - NO is likely cheap. Good fade."

    # --- Recommendation ------------------------------------------------------
    edge = fee = net_edge = None
    if yes_price_cents is not None:
        # Edge-based: compare our fair value to the live Kalshi YES price.
        edge = round(fair_yes - float(yes_price_cents), 1)  # +ve => YES underpriced
        fee = taker_fee_cents(float(yes_price_cents))
        net_edge = round(edge - fee, 1) if edge >= 0 else round(edge + fee, 1)
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
                         f"market {yes_price_cents}¢ - no clear edge.")
    else:
        # Probability-based when no live market price is provided.
        if prob_yes >= STRONG_PROB:
            rec, strength = "BUY YES", "strong"
            rationale = f"Model gives {fair_yes}% chance of YES - strong lean."
        elif prob_yes >= LEAN_PROB:
            rec, strength = "BUY YES", "lean"
            rationale = f"Model gives {fair_yes}% chance of YES - mild lean."
        elif prob_yes <= (1 - STRONG_PROB):
            rec, strength = "BUY NO", "strong"
            rationale = f"Model gives {fair_no}% chance of NO - strong lean."
        elif prob_yes <= (1 - LEAN_PROB):
            rec, strength = "BUY NO", "lean"
            rationale = f"Model gives {fair_no}% chance of NO - mild lean."
        else:
            rec, strength = "HOLD", "flat"
            rationale = f"Model near a coin-flip ({fair_yes}% YES) - wait for a clearer edge."

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
        "fee_cents": fee,
        "net_edge_cents": net_edge,
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


def _norm_ppf(p):
    """Inverse normal CDF (probit) via Acklam's rational approximation."""
    if p <= 0:
        return -1e9
    if p >= 1:
        return 1e9
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= phigh:
        q = p - 0.5; r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def _mid(m):
    ya, yb = m.get("yes_ask"), m.get("yes_bid")
    if ya is None or yb is None:
        return None
    return (ya + yb) / 200.0  # YES probability, 0..1


def implied_vol(spot, markets, minutes_to_close):
    """Back out the per-minute volatility the market is pricing across the ladder.

    Two ways depending on the ladder shape:
      - 'between' buckets form an implied probability distribution over the
        closing price; we take its standard deviation directly.
      - otherwise we invert the lognormal at each 'greater'/'less' strike (works
        for single-strike 15-min markets too).
    """
    if minutes_to_close <= 0 or spot <= 0:
        return None
    T = minutes_to_close
    rt = math.sqrt(T)

    # Preferred: quantile-based, from the implied distribution of 'between'
    # buckets that have a real two-sided market (yes_bid > 0). Far-OTM strikes
    # sit at a 1c minimum quote that is noise, not probability, so we require a
    # genuine bid and measure the 16th-84th percentile spread (robust to tails).
    buckets = []
    for m in markets:
        st = (m.get("strike_type") or "").lower()
        p = _mid(m)
        if st == "between" and m.get("floor") and m.get("cap") and m.get("yes_bid") and p and p > 0:
            buckets.append((m["floor"], m["cap"], p))
    buckets.sort(key=lambda b: b[0])
    tot = sum(p for _, _, p in buckets)
    if len(buckets) >= 4 and tot > 0:
        pts = []  # (upper_edge, cumulative_fraction)
        cum = 0.0
        for lo, hi, p in buckets:
            cum += p / tot
            pts.append((hi, cum))

        def quantile(q):
            prev_x, prev_c = buckets[0][0], 0.0
            for x, cc in pts:
                if cc >= q:
                    if cc == prev_c:
                        return x
                    return prev_x + (x - prev_x) * (q - prev_c) / (cc - prev_c)
                prev_x, prev_c = x, cc
            return pts[-1][0]

        x16, x84 = quantile(0.16), quantile(0.84)
        sig_frac = ((x84 - x16) / 2.0) / spot
        if sig_frac > 0:
            return sig_frac / rt

    # Fallback: invert each directional strike.
    sigs = []
    for m in markets:
        st = (m.get("strike_type") or "").lower()
        p = _mid(m)
        if p is None:
            continue
        if st in ("greater", "greater_or_equal"):
            K, p_above = m.get("floor"), p
        elif st in ("less", "less_or_equal"):
            K, p_above = m.get("cap"), 1 - p
        else:
            continue
        if not K or K <= 0 or p_above <= 0.03 or p_above >= 0.97:
            continue
        moneyness = math.log(spot / K)
        if abs(moneyness) < 1e-4:
            continue
        z = _norm_ppf(p_above)
        if abs(z) > 1e-6:
            st_total = moneyness / z
            if st_total > 0:
                sigs.append(st_total / rt)
    return statistics.median(sigs) if sigs else None


def vol_edge(spot, candles, markets, minutes_to_close):
    """Compare the market's implied volatility to realized volatility.

    Realized vol always computes; the ladder-implied read may be unavailable when
    the strikes are thin, in which case we still return realized (so the Deribit
    cross-check can attach).
    """
    mu, sigma, n = estimate_params(candles)
    if sigma <= 0 or minutes_to_close <= 0:
        return None
    rt = math.sqrt(minutes_to_close)
    ann = math.sqrt(525600)  # minutes per year, for annualizing
    out = {
        "realized_move_pct": round(sigma * rt * 100, 3),
        "realized_annual_pct": round(sigma * ann * 100, 1),
        "samples": n,
    }
    iv = implied_vol(spot, markets, minutes_to_close)
    if iv:
        ratio = iv / sigma
        if ratio > 1.15:
            verdict = "Market is OVERpricing movement"
            suggestion = ("Real moves are smaller than the market implies - near-the-money "
                          "favorites (and NO on far strikes) look underpriced; fade big-move longshots.")
        elif ratio < 0.87:
            verdict = "Market is UNDERpricing movement"
            suggestion = ("Real volatility is higher than priced - the far/longshot strikes "
                          "(big moves) look underpriced; buy the wings.")
        else:
            verdict = "Volatility fairly priced"
            suggestion = "Implied and realized movement roughly agree - no clear vol edge here."
        out.update({
            "implied_move_pct": round(iv * rt * 100, 3),
            "implied_annual_pct": round(iv * ann * 100, 1),
            "ratio": round(ratio, 2),
            "verdict": verdict, "suggestion": suggestion,
        })
    else:
        out.update({
            "implied_move_pct": None, "implied_annual_pct": None, "ratio": None,
            "verdict": "Implied vol unavailable",
            "suggestion": "Not enough two-sided strikes right now to read the market's implied volatility.",
        })
    return out


def kelly_fraction(prob, cost_cents):
    """Optimal fraction of bankroll to stake on a binary contract.

    A contract costs `cost_cents` and pays 100 if it wins. With model win
    probability `prob`, the Kelly fraction simplifies to:
        f = (100*prob - cost) / (100 - cost)
    i.e. edge-in-cents divided by the profit-if-win. Returns 0 when there's no
    edge (don't bet). Use a fraction of this (e.g. half-Kelly) to cut variance.
    """
    if prob is None or cost_cents is None:
        return 0.0
    if cost_cents <= 0 or cost_cents >= 100:
        return 0.0
    # Size against the EFFECTIVE cost (price + taker fee), so a thin gross edge
    # the fee eats sizes to zero instead of a real bet.
    eff = cost_cents + taker_fee_cents(cost_cents)
    if eff >= 100:
        return 0.0
    f = (100.0 * prob - eff) / (100.0 - eff)
    return max(0.0, f)


def sell_guidance(side, entry_cost, fair_yes_cents, fair_no_cents,
                  yes_bid=None, no_bid=None, yes_ask=None, no_ask=None,
                  minutes_to_close=None):
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
            headline = "Sell - the edge is gone"
        elif pnl >= 0:
            headline = f"Sell now - lock in +{pnl}¢ profit"
        else:
            headline = f"Sell / cut - edge gone, trim the {abs(pnl)}¢ loss"
        detail = (f"You can sell your {side} for ~{sell_price}¢, and the model's fair "
                  f"value is only {fair}¢ - little left to gain by holding.")
    else:
        action = "HOLD"
        upside = round(fair - sell_price, 1)
        if pnl is None:
            headline = "Hold - still underpriced"
        elif pnl >= 0:
            headline = f"Hold - up {pnl}¢, still has edge"
        else:
            headline = f"Hold - down {abs(pnl)}¢, model still likes it"
        detail = (f"Model fair value is {fair}¢ but you'd only get ~{sell_price}¢ selling "
                  f"now ({upside}¢ of upside left). Hold, or sell only if you want to "
                  f"de-risk.")

    # Always show the hold-to-settlement expectation: a contract pays 100¢ if it
    # wins and 0¢ if it loses, so its average hold value is its fair value.
    hold_note = (f"Hold to the close → worth ~{fair}¢ on average "
                 f"(it becomes 100¢ if {side} wins, 0¢ if it loses).")

    # Settlement context when the close is near.
    settle_note = None
    if minutes_to_close is not None and minutes_to_close <= 10:
        if fair >= 75:
            settle_note = f"Close in ~{round(minutes_to_close)}m and {side} is likely winning (≈{fair}¢) - holding to settlement should pay ~100¢."
        elif fair <= 25:
            settle_note = f"Close in ~{round(minutes_to_close)}m and {side} is likely losing (≈{fair}¢) - it may settle at 0¢. Selling now salvages value."

    # Flip signal: the model now favors the *other* side as a fresh +edge buy.
    # That means your position turned the wrong way -- consider selling out and
    # taking the opposite side.
    flip = None
    other_side = "NO" if side == "YES" else "YES"
    other_fair = fair_no_cents if side == "YES" else fair_yes_cents
    other_ask = (no_ask if side == "YES" else yes_ask)
    if other_ask is not None:
        other_edge = round(other_fair - other_ask, 1)
        if other_edge >= MIN_EDGE_CENTS:
            flip = {
                "to_side": other_side, "buy_at": other_ask,
                "fair": other_fair, "edge": other_edge,
                "note": (f"The model now favors {other_side} (fair {other_fair}¢ vs "
                         f"{other_ask}¢ ask, +{other_edge}¢ edge). Consider selling your "
                         f"{side} and flipping to {other_side}."),
            }

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
        "hold_note": hold_note,
        "settle_note": settle_note,
        "flip": flip,
    }


def kalshi_signal(spot, candles, market, minutes_to_close, calibrated=False):
    """Edge signal for a live Kalshi market (from kalshi.get_open_markets).

    Compares the model's fair value to the market's live YES/NO ask prices and
    recommends whichever side (if any) is underpriced by at least MIN_EDGE_CENTS.
    """
    mu, sigma, n = estimate_params(candles)
    prob_yes = max(0.0, min(1.0, probability_yes_for_strike(
        spot, market["strike_type"], market["floor"], market["cap"],
        minutes_to_close, mu, sigma, n)))
    # Reality-calibrate the GBM fair value against resolved crypto markets — but
    # ONLY when asked (calibrated=True). The recorder logs the RAW fair value (it
    # IS the calibration evidence, so calibrating it would double-count and, since
    # it runs inside the recorder's lock, re-enter it). Betting/display callers
    # pass calibrated=True to get the corrected number. No-op until data accrues.
    if calibrated:
        try:
            import calibrate
            prob_yes = max(0.0, min(1.0, calibrate.crypto(prob_yes)))
        except Exception:
            pass
    fair_yes = round(prob_yes * 100, 1)
    fair_no = round((1 - prob_yes) * 100, 1)

    yes_ask = market.get("yes_ask")
    no_ask = market.get("no_ask")
    # Edge = our fair value minus what we'd pay to enter. Positive => underpriced.
    edge_yes = round(fair_yes - yes_ask, 1) if yes_ask is not None else None
    edge_no = round(fair_no - no_ask, 1) if no_ask is not None else None
    # Net of Kalshi's taker fee — the edge that actually lands in the account.
    net_yes = round(edge_yes - taker_fee_cents(yes_ask), 1) if edge_yes is not None else None
    net_no = round(edge_no - taker_fee_cents(no_ask), 1) if edge_no is not None else None

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
        dip_note = ("Price just dipped - YES looks extra cheap here."
                    if prob_yes >= LEAN_PROB and mom < -0.0008 else None)
    elif best_side == "NO":
        rec = "BUY NO"
        strength = "strong" if best_edge >= 2 * MIN_EDGE_CENTS else "lean"
        rationale = f"Fair NO ≈ {fair_no}¢ vs ask {no_ask}¢ → +{best_edge}¢ edge."
        dip_note = ("Price just popped - NO looks extra cheap here."
                    if prob_yes <= (1 - LEAN_PROB) and mom > 0.0008 else None)
    else:
        rec, strength = "HOLD", "flat"
        rationale = "No side offers a clear edge over the live market price."
        dip_note = None

    # Near-settlement convergence: in the final minutes the outcome is nearly
    # decided, but thin books often leave the near-certain side cheap.
    near_settlement = None
    if minutes_to_close is not None and minutes_to_close <= 3:
        if prob_yes >= 0.85 and yes_ask is not None and yes_ask <= 95:
            near_settlement = {"side": "YES", "fair": fair_yes, "ask": yes_ask,
                               "mins": round(minutes_to_close, 1)}
        elif prob_yes <= 0.15 and no_ask is not None and no_ask <= 95:
            near_settlement = {"side": "NO", "fair": fair_no, "ask": no_ask,
                               "mins": round(minutes_to_close, 1)}

    # Confidence = the model's probability that the recommended bet WINS
    # (i.e. the fair value of the side we're buying). Honest and interpretable.
    if rec == "BUY YES":
        confidence = fair_yes
    elif rec == "BUY NO":
        confidence = fair_no
    else:
        confidence = None

    return {
        "prob_yes": round(prob_yes, 4),
        "fair_yes_cents": fair_yes,
        "fair_no_cents": fair_no,
        "edge_yes_cents": edge_yes,
        "edge_no_cents": edge_no,
        "net_edge_yes_cents": net_yes,
        "net_edge_no_cents": net_no,
        "recommendation": rec,
        "strength": strength,
        "confidence": confidence,
        "rationale": rationale,
        "dip_note": dip_note,
        "near_settlement": near_settlement,
        "momentum_pct": round(mom * 100, 3),
        "samples": n,
    }
