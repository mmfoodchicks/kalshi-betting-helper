# The NHL deep engine: out-of-sample validation

research/nhl_deep_validate.py; 2024-25 (1312 games, tables fit on [2022, 2023]) and 2025-26 (1312 games, tables fit on [2022, 2023, 2024]), 300 engine games a matchup, base seed 20261016. Provenance: 2024-25 commit c66b423 (dirty False), 2025-26 commit c66b423 (dirty False).

## What it is

The league's own play-by-play (nhl_pbp: every event's strength state, shooter, goalie, shot type and location, 2022-23 through 2025-26, every game reproducing its final score) fitted into tables (research/nhl_deep_fit.py): shot-attempt and penalty-incident rates by strength state, goal difference and the last five minutes; blocked, missed, saved and scored by distance and rebound; delayed penalties as their own phase; the goalie pull's intention and timing; three-on-three overtime and the shootout. The engine (nhl_deep_sim) plays a game as a race of exponential clocks over those tables, each team a profile of ten units measured from its games (nhl_deep_data).

## The engine against the league (shipped tables, league-average sides, 4,000 games)

| | engine | real (2022-23 to 2025-26) |
|---|---|---|
| goals a game | 6.179 | 6.230 |
| home win rate | 0.518 | 0.537 |
| past regulation | 0.212 | 0.223 |
| shootouts | 0.076 | 0.071 |
| empty-net goals | 0.407 | 0.357 |
| power-play goals | 1.154 | 1.188 |
| shorthanded goals | 0.138 | 0.144 |
| goals during a delayed call | 0.078 | 0.086 |
| man-advantage incidents | 5.553 | 5.705 |

## The rule, written before any deep run on a scored season

The headline blends the deep engine into the production probability at a weight w on the grid 0 / 0.15 / 0.3 / 0.5 / 0.7 / 1, chosen on 2024-25 by log loss. It ships only if, on 2025-26 at that frozen w, the blend's log loss is below production's (the point estimate decides; the paired standard error is reported beside it). Nothing is tuned on 2025-26.

## 2024-25 (where w was chosen): w = **0.5**

| arm | log loss | Brier |
|---|---|---|
| production (hockey.py, the board's model) | 0.67591 | 0.24146 |
| deep engine | 0.67459 | 0.24094 |
| home rate alone | 0.68714 | 0.24700 |

| blend w (on the deep engine) | log loss | minus production | SE (paired) |
|---|---|---|---|
| 0 | 0.67591 | +0.00000 | 0.00000 |
| 0.15 | 0.67394 | -0.00197 | 0.00072 |
| 0.3 | 0.67263 | -0.00328 | 0.00142 |
| 0.5 | 0.67186 | -0.00405 | 0.00233 |
| 0.7 | 0.67216 | -0.00375 | 0.00322 |
| 1 | 0.67459 | -0.00132 | 0.00456 |

Total goals: ranked probability score deep 10.4933, production 10.5463; mean total real 6.081, deep 6.096, production 6.338.

Calibration (games, mean forecast, home win rate) by decile of the home probability:

- deep: 4 @ 0.286 -> 0.500; 109 @ 0.366 -> 0.431; 397 @ 0.455 -> 0.499; 499 @ 0.547 -> 0.571; 278 @ 0.637 -> 0.669; 25 @ 0.720 -> 0.800
- prod: 10 @ 0.272 -> 0.300; 137 @ 0.360 -> 0.460; 322 @ 0.454 -> 0.497; 422 @ 0.552 -> 0.555; 311 @ 0.645 -> 0.659; 109 @ 0.734 -> 0.661; 1 @ 0.842 -> 1.000

## 2025-26, the test

| arm | log loss | Brier |
|---|---|---|
| production (hockey.py, the board's model) | 0.69282 | 0.24969 |
| deep engine | 0.68569 | 0.24632 |
| home rate alone | 0.69300 | 0.24992 |

| blend w (on the deep engine) | log loss | minus production | SE (paired) |
|---|---|---|---|
| 0 | 0.69282 | +0.00000 | 0.00000 |
| 0.15 | 0.69025 | -0.00257 | 0.00066 |
| 0.3 | 0.68823 | -0.00458 | 0.00130 |
| 0.5 | 0.68638 | -0.00644 | 0.00214 |
| 0.7 | 0.68543 | -0.00738 | 0.00296 |
| 1 | 0.68569 | -0.00712 | 0.00419 |

Total goals: ranked probability score deep 9.7805, production 9.8640; mean total real 6.254, deep 6.079, production 6.505.

Calibration (games, mean forecast, home win rate) by decile of the home probability:

- deep: 2 @ 0.295 -> 0.500; 55 @ 0.376 -> 0.400; 382 @ 0.458 -> 0.484; 673 @ 0.545 -> 0.522; 186 @ 0.633 -> 0.618; 14 @ 0.725 -> 0.786
- prod: 9 @ 0.274 -> 0.222; 66 @ 0.362 -> 0.394; 324 @ 0.459 -> 0.525; 548 @ 0.549 -> 0.496; 303 @ 0.636 -> 0.571; 56 @ 0.734 -> 0.679; 6 @ 0.822 -> 0.667

**Verdict: SHIPPED** -- at the frozen w = 0.5 the blend's log loss is 0.68638 against production's 0.69282 (-0.00644 +/- 0.00214). hockey._DEEP_WEIGHT = 0.5.

## Limits, stated

- Home ice runs light: the league-average engine wins 0.518 at home against the league's 0.537, and in both seasons the deep engine's calibration shows home teams winning more often than it said, bin after bin. A home-ice lever fitted like the NBA engine's is the obvious next change; it is a model change, judged on 2026-27 games.
- The deep engine's totals ran -0.17 goals a game on 2025-26 (+0.01 on 2024-25); production's +0.25 and +0.26. The rule covered the moneyline only, so the board's total edges still come from production; the deep total is shown beside it.
- The team units run on round K and persistence values set before anything was measured; the measured ones are tabled in nhl_deep_data and differ (attempt generation's K is 3.9 games, not 15). Swapping them is a model change for 2026-27 to judge.
- Goaltending is a TEAM unit; the starting goalie, the biggest single-game factor in hockey, is not modelled. Nor are injuries or line changes. There is no market anchor: Kalshi lists no NHL season win totals to anchor a level to.
- Matching minors at even strength (four-on-four) are not played: incidents that leave nobody short are not drawn, so the engine's four-on-four time is about half the league's.
- Two seasons is a measurement, not a proof. 2024-25 chose the weight; 2025-26 alone tested it.

