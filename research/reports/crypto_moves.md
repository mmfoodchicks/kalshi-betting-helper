# Crypto terminal v2: do indicators forecast short moves? (2026-11-27)

- **Rule:** `research/crypto_moves.py`, committed (eceaf12) before any held-out figure was computed.
- **Figures:** `research/data/crypto/moves.json`. Guards CXM pin the harness's causality and the verdicts.
- **The owner's question:** "when it's gonna change direction so I can sell the over and buy the under".

## A. Direction at 1, 3 and 5 minutes

**Data.** Coinbase one-minute candles for BTC, ETH, SOL, XRP and DOGE.

- Development: 28 days, 2026-10-16 to 11-12.
- Held out: the 14 days from 2026-11-13 to 26, about 95,000 minutes in all.

**Benchmarks.** The random walk (50%), and persistence (the last h minutes continue).

**Candidates.** Momentum, acceleration, RSI, MACD, EMA gaps, the volatility regime, volume, the position in the
60-minute range, and all of them together. The all-feature logistic won on development at every horizon.

**Results.** Gain is in millinats a minute over the random walk; the SE is by day.

| Horizon | Chosen | Held-out gain | SE | z | Coins better | Persistence gain |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 min | all features, L2 10 | 0.71 | 0.25 | 2.8 | 5 of 5 | 0.02 |
| 3 min | all features, L2 100 | 1.43 | 0.38 | 3.8 | 5 of 5 | 0.68 |
| 5 min | all features, L2 100 | 2.04 | 0.57 | 3.6 | 5 of 5 | 0.74 |

**Verdict.** It passed by the rule's letter at every horizon. What that means:

- **Not the period's drift.** The held-out fortnight fell, with YES rates of 49.5%, 48.9% and 48.6%. An
  intercept-only model accounts for 0.05, 0.21 and 0.36 millinats. The features add 0.66 ± 0.26, 1.22 ± 0.37 and
  1.68 ± 0.59 beyond it. This decomposition was run after the scoring and is descriptive.
- **It is short-horizon mean reversion.** The momentum weights are negative at 3 and 5 minutes. A trailing
  5-minute move reversed over the next 1, 3 and 5 minutes 51.1%, 51.3% and 51.4% of the time.
- **It is a tilt, not a call.** Held-out probabilities sat at 48-53%. Calibration where n > 500, said against hit:
  - 1 min: 48.9 vs 49.4, 51.8 vs 51.9
  - 3 min: 48.4 vs 48.1, 52.6 vs 52.6
  - 5 min: 47.9 vs 48.0, 52.9 vs 53.0, and 61.8 vs 58.7 at 60%+ (n 1,012; a little over-confident in that tail)
- **Caveats.**
  - One held-out fortnight, in a falling market.
  - Coinbase closes are trades, so the bid-ask bounce is in the target. That is negligible for BTC (a 1c spread)
    and larger for DOGE, which had the biggest 1-minute gain.
- **Untested** (no history held):
  - the 30-second horizon;
  - order-book imbalance (the panel shows the live top-of-book imbalance, marked untested);
  - cross-exchange confirmation.

## B. A drift in the settlement model: does not ship

The momentum term chosen on development was beta = +0.1, the trailing 5-minute log return.

- Against v1 on the held-out week (300 s look, 3,266 markets): -0.0010 ± 0.0009 nats. That is not past 2 SE.
- Against Kalshi on the same rows: +0.0047 ± 0.0026. Kalshi is still the better forecaster.

The minute-scale reversal does not become a 15-minute settlement edge. The term development chose was a small
momentum term, not a reversal, and it was not significant held out.

## C. Trading inside the window: loses

The rule enters at the 600 s look's ask on the side with a 3c+ fee-in edge. It sells at the 300 s look's bid for
that side, with Kalshi's fee on both legs.

| Week | Trades | Per trade | SE | Total | Worst drawdown |
| --- | ---: | ---: | ---: | ---: | ---: |
| Development | 856 | -2.96c | 0.80 | -2,534c | 3,353c |
| Held-out | 1,058 | -4.59c | 0.75 | -4,857c | 4,913c |

Two spreads and two fees cost more than any edge the model has. "Sell the over, buy the under" on these signals
lost money on real quotes.

## D. The proxy against the official sixty-second average (descriptive)

RMS in bp on the held-out week, measured over the same minute.

| Coin | Coinbase (O+H+L+C)/4 | Coinbase close | Bitstamp typical | Mean of the two |
| --- | ---: | ---: | ---: | ---: |
| BTC | 1.70 | 4.17 | 1.65 | 1.60 |
| ETH | 2.91 | 5.52 | 3.05 | 2.82 |
| SOL | 2.79 | 5.68 | 3.15 | 2.78 |
| XRP | 2.93 | 5.21 | 3.50 | 3.01 |
| DOGE | 3.71 | 6.38 | 3.74 | 3.32 |

**The live record's 20.8 bp is not proxy error.**

- It compares the level a minute or more before the close with the average of the final minute, pooled over coins
  and the hourly 300 s look.
- Most of it is the walk's own movement over that gap. BTC alone moves about 3 bp in 20 effective seconds, and the
  hourly look's 260 seconds are worth about 11 bp.
- The proxy's own error, minute against minute, is 1.6-3.7 bp. For BTC at $61k that is about $10, not $124.
- Averaging two exchanges shaves about 5% off it on four coins, but not on XRP.
- No sub-minute lag can be measured with minute data. CF Benchmarks' constituent weighting would need its
  order-book feeds, so it was not attempted.

## What ships

**The momentum panel.**

- It shows the passed direction model's probabilities, labelled "a 1-3 point tilt, mostly mean reversion".
- Its "possible reversal" markers fire at 60%+ on the 5-minute model. Held out, that tail said 61.8% and hit 58.7%.
- It is kept apart from the OBSERVED EMA crosses.
- It says in the same place that the tilt did not become a settlement edge and lost money inside the window.

**Nothing changes the settlement model or the paper rule.**
