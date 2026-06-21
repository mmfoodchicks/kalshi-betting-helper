"""Monte Carlo simulators (read-only — they touch no stored data).

Price simulator: projects a coin/commodity forward with many random GBM paths
and reports the distribution of where it lands — median, best/worst case, the
5–95% range, the chance it's up, and the chance it crosses a threshold. This is
the "run it for an hour and see the range of outcomes" view.
"""

import math
import random

import odds


def price_sim(spot, candles, horizon, n=20000, threshold=None, direction=None):
    """Many random GBM paths over `horizon` (in the candles' time unit).

    Returns distribution stats + a histogram. `horizon` is in minutes for 1-min
    crypto candles, or in days for daily commodity candles (units just have to
    match the candles, which is how the odds engine already works).
    """
    mu, sigma, k = odds.estimate_params(candles)
    if sigma <= 0 or spot <= 0:
        return {"error": "not enough recent data to simulate"}
    drift = (mu - 0.5 * sigma * sigma) * horizon
    vol = sigma * math.sqrt(horizon)
    finals = sorted(spot * math.exp(drift + vol * random.gauss(0, 1)) for _ in range(n))

    def pct(p):
        return finals[min(n - 1, int(p * n))]

    up = sum(1 for f in finals if f > spot) / n
    res = {
        "spot": round(spot, 4), "n": n, "horizon": horizon,
        "median": round(pct(0.50), 4),
        "p1": round(pct(0.01), 4), "p5": round(pct(0.05), 4),
        "p25": round(pct(0.25), 4), "p75": round(pct(0.75), 4),
        "p95": round(pct(0.95), 4), "p99": round(pct(0.99), 4),
        "best": round(finals[-1], 4), "worst": round(finals[0], 4),
        "prob_up": round(up * 100, 1),
        "median_move_pct": round((pct(0.50) / spot - 1) * 100, 2),
        "best_case_move_pct": round((pct(0.95) / spot - 1) * 100, 2),
        "worst_case_move_pct": round((pct(0.05) / spot - 1) * 100, 2),
    }
    if threshold:
        if direction == "below":
            res["prob_threshold"] = round(sum(1 for f in finals if f <= threshold) / n * 100, 1)
        else:
            res["prob_threshold"] = round(sum(1 for f in finals if f >= threshold) / n * 100, 1)
        res["threshold"] = threshold
        res["direction"] = direction or "above"

    lo, hi = pct(0.02), pct(0.98)
    bins = 24
    width = (hi - lo) / bins if hi > lo else 1
    counts = [0] * bins
    for f in finals:
        if lo <= f <= hi:
            counts[min(bins - 1, int((f - lo) / width))] += 1
    res["hist"] = {"lo": round(lo, 4), "hi": round(hi, 4), "counts": counts}
    return res
