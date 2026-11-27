# Crypto terminal v2, stage 2: the final reversal and the hybrid strategies (2026-11-27)

- **Rule:** `research/crypto_hybrid.py`, committed (df45623) before any held-out figure was computed.
- **Figures:** `research/data/crypto/hybrid.json`. Guards CXH pin the harness and the verdicts.
- **The owner's workflow:** $10 Quick Orders on the 15-minute contracts, cash out, maybe buy the other side in
  the same window, and near the close hold the side likely to win. He also said: "I wouldn't risk real money
  testing this yet". The record below agrees with him.

## R1. Was that the last dip?

**Setup.** Every quarter-hour window of the five coins, at 2 to 13 minutes in.

- Development is 2026-10-16 to 11-12; held-out is 11-13 to 26 (80,640 looks).
- The window's later bars are training labels only. The CXH-LEAK guard rewrites them and the earlier looks do
  not change.

**The random walk's analytic answers (the null)**

| Question | Null log loss, held-out | Held-out base rate | Challenger gain | SE (days) | Coins better |
| --- | ---: | ---: | ---: | ---: | ---: |
| The low so far is the final low | 0.5352 | 55.1% | +0.0239 | 0.0069 | 4 of 5 |
| The high so far is the final high | 0.5338 | 57.5% | +0.0296 | 0.0042 | 5 of 5 |
| The opening level is touched again | 0.5084 | 47.0% | +0.0073 | 0.0019 | 4 of 5 |

The challenger gain is in nats a look. It passes for all three questions.

**The null is not calibrated.** For "final low" it said 4% where the low held 13% of the time, and 45% where it
held 52%. A continuous walk makes new extremes more often than minute data with price ticks does, and DOGE's
coarse tick shows this most (its challenger gain is 0.089 nats). For the touch, the null is about 4 points too
high in the upper bins.

**What the panel shows.** The challenger is a logistic on the null and the momentum features. The panel prints
it with the null beside it.

**Remaining swings.** A swing is a move of 2 one-minute SDs. The walk predicts 1.52 more legs a look; minute
closes realized 0.20. The panel's figure is the walk's scaled by that measured 0.132, and it is descriptive.

**From the walk directly** (stated as such on the panel):

- the expected time to the next 2-SD move against the current leg, from the reflected walk's drawdown;
- the chance the current move lasts to the close, from the cosine series. It is exact at 0 and at delta.

## R2. Hold, cash out, switch or stay out, at $10 on Kalshi's real books

**Setup.**

- Every settled 15-minute market from 11-13 to 26: development 11-13 to 19, held-out 11-20 to 26, 3,320 windows
  each.
- Fills at the next minute's candle, a delay of up to a minute.
- Kalshi's per-order fee rounded up, NO bid = 100 - YES ask, and no fee at settlement.

**Held-out results**

| Strategy | Net | Per window | SE | Entries | Fees |
| --- | ---: | ---: | ---: | ---: | ---: |
| S0 no trade | $0 | 0 | 0 | 0 | $0 |
| S1 settlement only (enter 10 min out, hold) | -$638 | -19.2c | 14.2 | 1,093 | $375 |
| S2 trading only (enter 10 min out, sell at 5) | -$814 | -24.5c | 9.1 | 1,093 | $623 |
| S3 take profit (+15c, chosen on dev) | -$712 | -21.5c | 9.6 | 1,093 | $565 |
| S4 hybrid, time-aware (theta 4c, dev) | +$141 | +4.2c | 17.8 | 633 | $332 |

**S4 is not profitable.** It does not beat any baseline past 2 SE; its edge over S1 is +23.5 ± 21.7c a window.

**How S4 works.** Its settlement probability blends Vigil's model and Kalshi's mid. The blend's weights depend on
the time left and were learned on development, not set as phases: logit model 0.42 - 0.13 x (tau/900), logit mid
0.60 + 0.03 x (tau/900). It trusts Kalshi's mid more than the model throughout, and the model a little more as
the close nears. 373 of its 633 held-out entries came in the last five minutes.

**Verdict, by the rule as written.** No strategy made money after fees and spreads on held-out windows. The
least bad is indistinguishable from not trading. Cashing out and re-entering pays two spreads and two fees, and
in every one of these tests that cost more than any edge the model has.

## CF Benchmarks data through Kalshi

Kalshi's help article says its API passes CF Benchmarks index data through. The docs confirm two routes, and
both need credentials:

- REST: `GET https://external-api.kalshi.com/trade-api/v2/cfbenchmarks/values?id=BRTI`
  (`ETHUSD_RTI`, `SOLUSD_RTI`, `XRPUSD_RTI`, `DOGEUSD_RTI`).
- WebSocket: the `cfbenchmarks_value` channel, about one tick a second, carrying the trailing 60-second and
  quarter-hour final-minute averages. The `cfbenchmarks_value_5hz` channel covers BTC, ETH, SOL, XRP and DOGE.

**Access.** Both require a signed Kalshi API request (key id plus RSA private key) and an account entitlement.
Each REST call costs 50 read tokens, about 4 a second on the Basic tier. No credentials were used or requested.

**What the owner would need.**

1. A Kalshi API key, read-only if Kalshi offers one, with the CF Benchmarks entitlement.
2. On the PC (or Render), two environment variables: `KALSHI_KEY_ID`, and the private key as
   `KALSHI_PRIVATE_KEY_PATH` pointing at a file outside the repository. Never commit either.
3. A separate, read-only module to sign those requests. The paper-only guard (CX-PAPER) forbids signing code in
   `crypto_terminal.py` on purpose.

Until then the four-exchange median stays the level. Its own error, measured minute against minute, is 1.6-3.7
bp (`crypto_moves.md`, D).
