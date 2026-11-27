# The crypto terminal's model against held-out history (2026-11-27)

Script and rule: `research/crypto_validation.py`. The rule was committed (6eb9b2f) before the scoring run.
Frame: `research/data/crypto/frame_15m.csv.gz`. Figures: `research/data/crypto/validation.json`. Guard
CX-VAL recomputes the figures from the frame and the production code.

## What was tested

- **Contracts.** Kalshi's fifteen-minute "up or down" contracts (KX<COIN>15M) on BTC, ETH, SOL, XRP and
  DOGE. The data is every settled market closing 2026-11-13 to 2026-11-26 UTC: 6,574 markets, 26,061
  market-look rows.
- **Settlement.** Each market settles on the simple average of the 60 seconds of CF Benchmarks' index before
  its close, against the same average before its open. Kalshi publishes both averages: the open's is
  `floor_strike`, the close's is `expiration_value`.
- **The model.** A driftless random walk on log price, `crypto_terminal.p_yes` on the rule
  `crypto_terminal.parse_rule` reads from each market's own text:
  - the settlement average's variance is handled exactly: tau - 2W/3, not tau;
  - the volatility is an EWMA of one-minute returns;
  - the proxy's distance from the index is added in quadrature.
- **Looks.** 600, 300, 120 and 60 s before the close. The level is Coinbase's last trade before the look.
  Kalshi's book is the closing quote of its latest one-minute candlestick at or before the look. A book is
  used only when that candle ended within 120 s of the look and is two-sided.
- **Split.** Development is the closes of 2026-11-13 to 19. Held-out is 2026-11-20 to 26.

## Chosen on the development week, then frozen

| Parameter | Value | How it was set |
| --- | --- | --- |
| Volatility half-life | 60 min | Pooled log loss at the 300 s look over a 5 x 5 grid. It read 0.3830 nats, the runner-up (120 min) 0.3834. The surface is flat. |
| Volatility multiplier | 0.9 | Same grid. One-minute closes carry trade noise that a 60-second average does not. |
| `PROXY_SD` | 0.000276 (2.76 bp) | Median over coins of the RMS of log(official average / Coinbase's (O+H+L+C)/4 of the same minute): BTC 1.85, ETH 2.48, SOL 2.90, XRP 2.76, DOGE 3.65 bp. |

## Held-out week: the model against Kalshi's price on the same rows

The difference is model log loss minus Kalshi log loss, a row's mean. A positive figure means Kalshi
forecast better. SE is over rows, one per market at one look.

| Look | Rows | Model LL | Kalshi LL | Difference | SE | z |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 600 s | 3,262 | 0.5850 | 0.5751 | +0.0098 | 0.0024 | 4.18 |
| 300 s | 3,266 | 0.3977 | 0.3920 | +0.0057 | 0.0025 | 2.32 |
| 120 s | 2,989 | 0.2517 | 0.2350 | +0.0167 | 0.0034 | 4.95 |
| 60 s | 1,810 | 0.2423 | 0.2094 | +0.0329 | 0.0069 | 4.80 |

At 300 s, coin by coin, the model was never better past 1 SE in its own favour:

| Coin | Difference | SE |
| --- | ---: | ---: |
| BTC | +0.0057 | 0.0041 |
| ETH | +0.0159 | 0.0055 |
| SOL | -0.0028 | 0.0040 |
| XRP | +0.0013 | 0.0054 |
| DOGE | +0.0086 | 0.0080 |

Kalshi's price beat the model at every look, past 2 SE, and by more as the close nears. Near the close,
Kalshi's traders see the live index, and the model sees a proxy a minute stale.

The model's reliability at 300 s on the held-out week:

- In every bin from 35% up, YES hit more often than the model said. Where it said 55%, YES hit 61%; where
  it said 97%, YES hit 99%.
- Below 30%, YES hit close to what the model said.

That is descriptive: nothing was refitted.

## The paper rule

The rule buys one contract at the ask, fee in, on the side whose edge is 3c or more at the 300 s look.

| Week | Bets | Mean P/L a bet | SE |
| --- | ---: | ---: | ---: |
| Development (the week the volatility was chosen on) | 1,034 | +4.04c | 1.22 |
| Held-out | 1,261 | +0.56c | 1.09 |

The development figure is in-sample and shrank to noise on contact with the held-out week. Not
profitable past 2 SE.

## Decision, by the rule as written

- **Edges.** Unproven: Kalshi was the better forecaster. The board labels every edge "unproven". Only the
  live record can lift that label. It decides once it has 500 markets over 10 days at the decision look,
  with the difference past 2 SE in the model's favour.
- **Paper rule.** Not profitable past 2 SE.

## One change after the scoring run, disclosed

The registered parser did not read a rule variant on 111 of the 26,061 rows (0.4%). Those markets state
the opening average as a number ("is at least 60838.40") instead of "is at least the simple average ...".
`parse_rule` now reads it, and the figures above are with the fix. Under the code as registered, the
conclusions are the same:

- at 300 s the difference was +0.0057 ± 0.0025 over 3,252 rows;
- the paper rule made +0.56c ± 1.09 over 1,257 bets;
- the development choice was the same (60 min, 0.9).

The same run also changed one output format: the grid's top five print as lists, so the guard's JSON
round trip compares equal.

## Limits (small samples flagged)

- **One held-out week**, about 650 markets a coin. That is too small to find any edge of the size that would
  matter after fees. The paired difference has an SE of about 0.0025 nats pooled, 0.004-0.008 a coin.
- **Coinbase alone** stands in for the board's four-exchange median. The other exchanges' minute history is
  not kept. The live record's `proxy_rms_bps` measures the production proxy.
- **Kalshi's book** is a candlestick's closing quote, not the quote at the exact second. Markets with no
  candle within 120 s of the look are left out, which is why the 60 s look has 1,810 rows. Fees use the
  per-contract quadratic curve without per-order rounding.
- **The hourly above/below ladder (KX<COIN>D) was not validated.** The same model prices it on the board,
  under the same "unproven" label. Its record accrues in the live paper record (kind `hourly`).
- **Range markets (KX<COIN> "between")** and one-touch markets are refused by the parser and not priced.

## The live terminal's memory and storage (measured, tests/guards_auto_crypto.py)

- **Recorder memory.** 400 simulated passes, with every coin, the 188-strike hourly ladder, grading and the
  record each ninth pass, grew the process's RSS by 0.0 MB while the record gained about 3,900 rows.
- **Record reads.** Reading the record over 200,000 rows: +0.4 MB, about 2 s of SQLite time. A fetchall
  at the removed backtest's ~1.39 KB a row would be about 280 MB. So pages never run that read: the
  background owner writes a summary file after each grading pass, and the routes read the file.
- **Storage.** 3,720 rows a day at 229 bytes a row is 0.85 MB a day. Retention keeps every look for 14 days,
  then the decision look to 180 days: 231,360 rows, about 53 MB at steady state, under a 300,000-row cap. The
  Render disk is 1 GB and every store shares it.
