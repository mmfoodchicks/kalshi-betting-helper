"""Backtest the crypto odds model against real historical prices.

The core question: does the model's probability actually predict what happens?
We replay history minute-by-minute. At each point we estimate the model using
*only* the data available up to that moment, ask "will the price be higher
`horizon` minutes from now?", then check what really happened.

Metrics produced:
  - n: number of decisions tested
  - accuracy: how often the model's favored side was right (overall + on the
    confident subset, where it leaned clearly away from 50/50)
  - brier: mean squared error of the probabilities (lower = sharper; 0.25 is the
    coin-flip baseline)
  - calibration: for each probability band, predicted vs. actual up-rate (a
    well-calibrated model's bars line up)
  - roi_vs_50: an illustrative profit test -- if a market priced every contract
    at a naive 50c, betting the model's side captures the model's directional
    edge. Positive ROI means the model's drift/momentum read beats a coin flip.
"""

import statistics

import prices
import odds


def run(coin, horizon_min=15, lookback=120, count=1500, conf=0.04):
    granularity = 60
    candles = prices.get_history(coin, granularity, count)
    closes = [c["close"] for c in candles]
    n = len(closes)
    H = horizon_min  # one candle == one minute at granularity 60
    if n < lookback + H + 30:
        return {"error": f"not enough history for {coin} (got {n} candles)"}

    rows = []  # (prob_up, actual_up)
    for i in range(lookback, n - H):
        window = candles[i - lookback:i + 1]
        mu, sigma, k = odds.estimate_params(window)
        spot = closes[i]
        prob_up = odds.probability_yes(spot, spot, "above", H, mu, sigma)
        actual_up = 1 if closes[i + H] > spot else 0
        rows.append((prob_up, actual_up))

    n_dec = len(rows)
    # Brier score over all decisions.
    brier = statistics.fmean((p - a) ** 2 for p, a in rows)
    # Overall directional accuracy (treat exactly 0.5 as a non-pick).
    picks = [(p, a) for p, a in rows if abs(p - 0.5) > 1e-9]
    acc = statistics.fmean(1.0 if (p > 0.5) == bool(a) else 0.0 for p, a in picks) if picks else None
    # Confident subset: model leaned clearly.
    conf_rows = [(p, a) for p, a in rows if abs(p - 0.5) >= conf]
    acc_conf = (statistics.fmean(1.0 if (p > 0.5) == bool(a) else 0.0 for p, a in conf_rows)
                if conf_rows else None)

    # Calibration bands.
    bands = []
    edges = [0.0, 0.35, 0.45, 0.55, 0.65, 1.01]
    for lo, hi in zip(edges, edges[1:]):
        b = [(p, a) for p, a in rows if lo <= p < hi]
        if b:
            bands.append({
                "range": f"{int(lo * 100)}–{int(min(hi, 1) * 100)}%",
                "n": len(b),
                "predicted": round(100 * statistics.fmean(p for p, _ in b), 1),
                "actual": round(100 * statistics.fmean(a for _, a in b), 1),
            })

    # Illustrative ROI vs a naive 50c market, betting only confident calls.
    staked = pnl = wins = bets = 0
    for p, a in conf_rows:
        side_up = p > 0.5
        win = (a == 1) if side_up else (a == 0)
        staked += 50
        pnl += (100 if win else 0) - 50
        wins += 1 if win else 0
        bets += 1
    roi = round(100 * pnl / staked, 1) if staked else None

    base_up_rate = round(100 * statistics.fmean(a for _, a in rows), 1)
    return {
        "coin": coin, "horizon_min": horizon_min, "n": n_dec,
        "candles_used": n,
        "accuracy_pct": round(acc * 100, 1) if acc is not None else None,
        "accuracy_confident_pct": round(acc_conf * 100, 1) if acc_conf is not None else None,
        "confident_n": len(conf_rows),
        "brier": round(brier, 4),
        "brier_baseline": 0.25,
        "calibration": bands,
        "base_up_rate_pct": base_up_rate,
        "roi_vs_50_pct": roi,
        "roi_bets": bets,
        "roi_win_pct": round(100 * wins / bets, 1) if bets else None,
    }
