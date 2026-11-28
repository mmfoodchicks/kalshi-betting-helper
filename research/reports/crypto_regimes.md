# Crypto terminal v2, stage 3: more final-reversal models, execution delay, and the live records (2026-11-27)

- **Rule:** `research/crypto_regimes.py`, committed (a1057e7) before any figure below was computed.
- **Figures:** `research/data/crypto/regimes.json`. Guards CXP, CXB and CXR pin the code and the verdicts.
- **The owner:** "Continue crypto Final Reversal Detection research. Test realistic execution delays of 5, 15, 30
  and 60 seconds where recorded data supports them... Keep crypto paper-only." Forecasting (R3) and money (R4)
  are reported apart: predicting another dip is not the same as trading on it at a profit.

## R3. Forecasting the final reversal: four models

The comparison uses 80,640 held-out looks (2026-11-13 to 26) and the same windows and labels as R1. Figures are
held-out log loss; lower is better.

| Question | Walk null | Regime-switching null | Path simulation | R1 challenger |
| --- | ---: | ---: | ---: | ---: |
| The low so far is the final low | 0.5352 | 0.5312 | 0.5132 | **0.5113** |
| The high so far is the final high | 0.5338 | 0.5248 | 0.5125 | **0.5042** |
| The opening level is touched again | 0.5084 | 0.5073 | 0.5061 | **0.5010** |

**The regime-switching null** uses a sigma factor per volatility-regime tercile, fitted on development.

- The factors were 0.7-1.0: the walk's sigma overstates how far a minute-sampled price travels.
- It beat the null for the final high only (+0.0090 ± 0.0028, all five coins).

**The path simulation** has 400 empirical paths a look, resampled from the trailing 300 minutes, and no fitted
parameters.

- It improved on the null by 0.002-0.022 nats.
- It failed the rule's coin test: 2, 3 and 1 of 5 coins.

**Neither beat the challenger on any question,** so the challenger stays on the panel. Its gains over the null
stand (R1): z 3.5, 7.1 and 3.8.

## R4. Money: how much does the delay cost?

R2's strategies with their choices frozen (take profit 15c, theta 4c, the p_hat weights), $10 a position, every
fee included. Held-out results, 3,320 windows:

| Strategy | Zero delay (the quote decided on) | One-minute delay (R2) |
| --- | ---: | ---: |
| S1 settlement only | -$491, -14.8c ± 12.8 | -$638, -19.2c ± 14.2 |
| S2 trading only | -$1,154, -34.8c ± 6.7 | -$814, -24.5c ± 9.1 |
| S3 take profit | -$605, -18.2c ± 9.2 | -$712, -21.5c ± 9.6 |
| S4 hybrid | **+$1,361, +41.0c ± 17.1** | +$141, +4.2c ± 17.8 |

**At zero delay the hybrid passes the rule's money test** (z 2.4), and it did on development too (+36.2c ±
13.2). A minute later the edge is gone.

**Zero delay is a CEILING, not a result.**

- It assumes a fill at the exact book that existed at the decision instant, the same moment as the Coinbase
  close the model read.
- The likeliest mechanism is Kalshi's book lagging the exchanges by seconds, which an order placed seconds later
  may or may not still find.

**Minute candles cannot say where between 0 and 60 seconds the edge disappears.** That is exactly the 5, 15 and
30 s question. The minute data shows only two things:

- the edge is not there at a minute;
- it may be there at an instant.

## The two live records added to the terminal

**The forecasts (`cx_preds`).**

- Every 15-minute look logs the direction (1/3/5 min) and final-reversal forecasts.
- Each forecast comes from the last complete minute, at least a settlement window before the close.
- Each is graded after the close off Coinbase's candles, the same source the forecasts read.
- They are scored as SQL sums: direction against a coin flip, and each final question as model against the walk's
  null.
- Retention is 180 days or 300,000 rows; 1,920 rows a day.

**Kalshi's book (`cx_book`).** It is recorded ON THE PC, not the server (changed 2026-11-28).

- **Why not the server:** a 5-second recorder there would make ~720 Kalshi reads an hour from the app's IP. That
  budget is shared with every board, so a climbing 429 rate would cost real features. The server's recorder
  is therefore OFF unless `VIGIL_CRYPTO_BOOK=on`.
- **On the PC:** `pc_worker.py --book`, a lane `pc_loop` keeps running (niced), makes one batched read of the five
  open 15-minute markets every 5 s from the PC's own IP.
  - The spacing doubles after each 429, up to 60 s, and eases back on success.
  - A row is written only when the book moved, or on a minute's heartbeat. Rows are 30 bytes, measured.
  - The local SQLite lives under `pc-simcache` with the server's caps: 10 days or 500,000 rows, about 15 MB.
- **Upload:** every three minutes through the token door (`POST /api/crypto/book`), whole moments only, at most
  5,000 rows and 512 KB a call. Every row is checked before it enters the server's `cx_book`.
- **With the PC off, nothing is recorded.** The tab says so, and shows how old the newest snapshot is.
- **The probe:** from this sandbox's egress, about one read in ten came back 429 at every spacing tried (5 s, 1 s,
  0.2 s). That looks like the shared IP's budget, not the rate. The PC has its own.

**The 5/15/30/60 s re-score** (`rescore_delays`) reads `cx_book` once, after 14 full days. It uses the frozen R2
strategies with each window's rule and result from the live record. Until then, the minute bracket above is all
the data supports.
