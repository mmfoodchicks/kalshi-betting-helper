# Both teams to score: tested, recalibrated, shown with a caveat

`research/soccer_btts_validation.py` → `research/data/soccer_btts_validation.json`.
Run 2026-10-17 on the cached ESPN results (85 competitions, 2023-2026),
weekly walk-forward in production's own fitting window.

## Rule A, written first: the raw figure. Failed.

The card has always shown P(both teams score) read off the goals model's
scoreline grid. The three-way validation never scored it. Against each
competition's own decayed BTTS frequency (the benchmark), held out:

| | matches | model log loss | league rate | difference |
|---|---|---|---|---|
| 2025 | 20,625 | 0.69157 | 0.68775 | +0.00381 ± 0.00125 (worse) |
| 2026 | 17,503 | 0.69302 | 0.68670 | +0.00632 ± 0.00140 (worse) |

It is too extreme in both directions: said 26%, happened 36%; said 73%,
happened 66%. Treating the two sides' goals as independent Poisson counts
overstates how much BTTS varies between matches.

## Rule B, written after A failed: recalibrated. Passed both years.

p = logistic(a + b·logit(raw) + c·logit(league rate)), with (a, b, c)
fitted on the 2024 walk-forward alone: (0.0356, 0.4293, 0.4834). Both
2025's and 2026's calibration had been read by then, so it had to pass
both years, not just one.

| | recalibrated | league rate | difference |
|---|---|---|---|
| 2025 | 0.68505 | 0.68775 | −0.00270 ± 0.00053 |
| 2026 | 0.68463 | 0.68670 | −0.00207 ± 0.00060 |

Calibrated within about a point: 2026 said 54.4% and 55.5% happened;
said 46.3%, 48.3%.

## What the board does

Kalshi lists a full-match BTTS market (KX<league>BTTS, the same event tail
as the win/draw event, settled on 90 minutes plus stoppage) beside 452 of
968 matches on 2026-10-17. They are fetched by name, 100 tickers a call.
A book counts only when it is two-sided within 10¢; 386 were. The card
shows Kalshi's YES and NO and the recalibrated figure. Pre-game, on a
readable book, it shows YES and NO edges net of the fee. Every figure goes
on the record in bucket `soccer_btts`, against Kalshi's mid.

## The caveat the tab prints

The bar passed is "beats the league average", **not** "beats Kalshi".
Beside Kalshi's books on 2026-10-17 the recalibrated figure is flatter:
SD 6.1 points against Kalshi's 9.1 over 284 matches, correlation 0.66,
mean 53.9 against 55.6. So its biggest edges sit where Kalshi is most
extreme (Man City v AEK: model 52%, Kalshi 35%; PSG v Barcelona: 65%
against 79%). That is where a flatter model is most likely the one that
is wrong. The tab measures the spread on every build and says this beside
the edges. The live record settles it.

1st-half and 2nd-half BTTS markets (Kalshi lists both) are not priced:
the model has no timing for goals.
