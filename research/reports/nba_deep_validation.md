# The NBA deep engine: out-of-sample validation

research/nba_deep_validate.py; 2024-25 (1216 games, tables fit on [2022, 2023]) and 2025-26 (1202 games, tables fit on [2022, 2023, 2024]), 300 engine games a matchup, base seed 20261016. Provenance: 2024-25 commit 5d80b79 (dirty False), 2025-26 commit 5d80b79 (dirty False).

## What it is

ESPN's play-by-play for every game (nba_pbp: each shot's value, distance and family, every free throw as k of n, rebounds, turnovers, fouls, 2022-23 through 2025-26, every stored game reproducing its final score) cut into possessions (nba_poss) and fitted into tables (research/nba_deep_fit.py): possession length by how the ball came and the game situation; the opportunity mix (turnover live or dead, a free-throw trip, a shot) for a first chance and after an offensive rebound; the shot's zone and make rate by situation, chance and how the ball came (a steal's transition rim attempt against a set defence); offensive rebounds by zone; free throws; and-ones; the period's final possession; the pull back toward an even game. The engine (nba_deep_sim) plays a game possession by possession on the real clock, each team a profile of eighteen units measured from its games (nba_deep_data), raised to the engine's lever exponent.

## The engine against the league (shipped tables, two league-average sides, 4,000 games)

| | engine | real (2022-23 to 2025-26) |
|---|---|---|
| points a game | 230.2 | 229.1 |
| home win rate | 0.544 | 0.557 |
| past regulation | 0.034 | 0.051 |
| margin SD | 14.54 | 15.42 |
| possessions a team | 100.3 | 100.2 |
| field goal attempts a team | 89.1 | 89.0 |
| threes attempted a team | 36.0 | 36.1 |
| free throws attempted a team | 21.6 | 22.6 |
| turnovers a team | 14.0 | 14.7 |
| field goal % | 0.473 | 0.471 |
| three-point % | 0.362 | 0.360 |
| free throw % | 0.780 | 0.782 |

The real margin SD is the raw one; net of the two teams' strengths it is smaller, and the engine's sides are identical, so its SD is the one to read against that.

## The rule, written before any deep run on a scored season

The headline blends the deep engine into the production probability at a weight w on the grid 0 / 0.15 / 0.3 / 0.5 / 0.7 / 1, chosen on 2024-25 by log loss. It ships only if, on 2025-26 at that frozen w, the blend's log loss is below production's (the point estimate decides; the paired standard error is reported beside it). Nothing is tuned on 2025-26.

Set before the harness ran: home court (nba_deep_sim._HFA_MAKE 1.012, matched to the 2024-25 league's home edge on league-average sides) and the lever exponent (nba_deep_data.LEVER_EXP 1.15, measured on 2023-24, where the teams' real margins on the engine's have a slope of 1). Neither carries a team's information; 2025-26 was read by neither. ESPN files the All-Star games as regular-season games; every table, unit and score here is on games between two of the thirty franchises (nba_pbp.league_game).

## 2024-25 (where w was chosen): w = **0.3**

| arm | log loss | Brier |
|---|---|---|
| production (basket.py, the board's model) | 0.61655 | 0.21410 |
| deep engine | 0.62063 | 0.21550 |
| home rate alone | 0.68984 | 0.24835 |

| blend w (on the deep engine) | log loss | minus production | SE (paired) |
|---|---|---|---|
| 0 | 0.61655 | +0.00000 | 0.00000 |
| 0.15 | 0.61619 | -0.00036 | 0.00054 |
| 0.3 | 0.61618 | -0.00037 | 0.00108 |
| 0.5 | 0.61670 | +0.00015 | 0.00178 |
| 0.7 | 0.61782 | +0.00128 | 0.00248 |
| 1 | 0.62063 | +0.00408 | 0.00353 |

Total points: ranked probability score (5-point bins) deep 42.7210, production 42.6559; mean total real 227.6, deep 229.7, production 225.5.

Calibration (games, mean forecast, home win rate) by decile of the home probability:

- deep: 3 @ 0.164 -> 0.000; 28 @ 0.255 -> 0.107; 149 @ 0.358 -> 0.248; 259 @ 0.451 -> 0.417; 347 @ 0.548 -> 0.565; 274 @ 0.647 -> 0.697; 128 @ 0.738 -> 0.789; 28 @ 0.827 -> 0.929
- prod: 9 @ 0.174 -> 0.000; 42 @ 0.260 -> 0.119; 137 @ 0.355 -> 0.285; 241 @ 0.456 -> 0.419; 325 @ 0.551 -> 0.545; 266 @ 0.645 -> 0.680; 154 @ 0.744 -> 0.779; 42 @ 0.840 -> 0.929

## 2025-26, the test

| arm | log loss | Brier |
|---|---|---|
| production (basket.py, the board's model) | 0.60867 | 0.20960 |
| deep engine | 0.61560 | 0.21280 |
| home rate alone | 0.68636 | 0.24661 |

| blend w (on the deep engine) | log loss | minus production | SE (paired) |
|---|---|---|---|
| 0 | 0.60867 | +0.00000 | 0.00000 |
| 0.15 | 0.60884 | +0.00017 | 0.00051 |
| 0.3 | 0.60932 | +0.00065 | 0.00102 |
| 0.5 | 0.61044 | +0.00177 | 0.00169 |
| 0.7 | 0.61209 | +0.00343 | 0.00235 |
| 1 | 0.61560 | +0.00693 | 0.00336 |

Total points: ranked probability score (5-point bins) deep 36.7403, production 36.8541; mean total real 231.2, deep 229.7, production 231.5.

Calibration (games, mean forecast, home win rate) by decile of the home probability:

- deep: 4 @ 0.158 -> 0.000; 37 @ 0.265 -> 0.162; 138 @ 0.359 -> 0.283; 272 @ 0.452 -> 0.390; 292 @ 0.549 -> 0.599; 291 @ 0.644 -> 0.708; 134 @ 0.740 -> 0.836; 34 @ 0.828 -> 0.794
- prod: 2 @ 0.158 -> 0.000; 47 @ 0.268 -> 0.191; 130 @ 0.354 -> 0.254; 227 @ 0.454 -> 0.352; 341 @ 0.550 -> 0.581; 262 @ 0.648 -> 0.729; 150 @ 0.746 -> 0.827; 43 @ 0.832 -> 0.837

**Verdict: NOT shipped** -- at the frozen w = 0.3 the blend's log loss is 0.60932 against production's 0.60867 (+0.00065 +/- 0.00102). The deep figure is shown on the card and shadow-graded; basket._DEEP_WEIGHT = 0.

## How it misses

| | calibration slope | intercept | SD of the logit |
|---|---|---|---|
| 2024-25 production | 1.439 | -0.119 | 0.632 |
| 2024-25 deep engine | 1.558 | -0.093 | 0.571 |
| 2025-26 production | 1.563 | -0.095 | 0.617 |
| 2025-26 deep engine | 1.527 | -0.038 | 0.593 |

Both models are UNDER-confident: a calibration slope above 1 means the outcomes are more extreme than the forecasts, here by about half again in logit, every season. The deep engine's forecasts are the more compressed (a narrower logit spread), and the two agree closely -- their logits correlate 0.902 on 2024-25 and 0.912 on 2025-26 -- so the engine adds little that production lacks, and less than it costs in sharpness. The board's live calibration (calibrate.apply on the graded ledger) stretches production's figure; nothing stretches the deep one. Two candidates for the compression, neither tested here: the round shrinkage K (heavy for an 82-game sport's shooting units) and the lever exponent, measured on full-season units the engine never sees unshrunk. Changing either is a model change, judged on 2026-27 games through the shadow bucket; this test does not choose it.

## Limits, stated

- Overtime is too rare: 0.034 of the engine's games against the league's 0.051. Real endings cluster at a tie (the trailing team fouls and shoots threes, the leader protects); the engine's clutch tables carry the rates of those possessions but not the choice to extend a game. A moneyline barely feels it (overtime is close to a coin flip); a total or a spread near zero does.
- The deep engine's totals ran -1.5 points a game on 2025-26 (+2.1 on 2024-25); production's +0.4 and -2.0. The rule covered the moneyline only, so the board's total and spread edges still come from production; the deep total is shown beside it.
- Teams are TEAM units: no player, no injury, no rest day, no lineup. A star's absence is the biggest single-game factor in this sport and neither model sees it.
- The team units run on round shrinkage K and a prior persistence of 0.6 set before anything was measured, not fitted.
- There is no market anchor: the level comes from the play-by-play alone.
- Two seasons is a measurement, not a proof. 2024-25 chose the weight; 2025-26 alone tested it.

