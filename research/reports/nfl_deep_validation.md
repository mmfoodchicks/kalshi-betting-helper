# NFL deep engine -- walk-forward validation, 2025 regular season
Built 2026-10-16 09:51 UTC. Profiles from the games before each week only (prior: 2024 units), no market anchor; 300 simulated games per real game; weeks 2-18; 256 games.

## Whole span
| model | n | log loss | Brier | accuracy | margin MAE | margin RMSE | slope actual~pred |
|---|---|---|---|---|---|---|---|
| deep engine | 256 | 0.673 | 0.239 | 0.5781 | 10.973 | 14.066 | 1.14 |
| points-differential ratings (nfl_season._wp) | 256 | 0.6368 | 0.2234 | 0.5898 | - | - | - |
| constant home 0.54 | 256 | 0.6904 | 0.2477 | 0.5391 | - | - | - |

## By stretch of the season
| weeks | n | deep log loss | deep Brier | deep acc | PD log loss | PD Brier | deep margin MAE | slope |
|---|---|---|---|---|---|---|---|---|
| 2-6 | 77 | 0.683 | 0.2416 | 0.5844 | 0.6667 | 0.2347 | 10.553 | 0.395 |
| 7-12 | 85 | 0.6469 | 0.2273 | 0.6588 | 0.5902 | 0.2026 | 10.905 | 1.533 |
| 13-18 | 94 | 0.6882 | 0.2476 | 0.5 | 0.6544 | 0.2329 | 11.377 | 1.309 |

## Calibration (favourite's side), deep engine
| predicted | n | mean predicted | realized |
|---|---|---|---|
| 0.5-0.6 | 179 | 0.546 | 0.528 |
| 0.6-0.7 | 67 | 0.63 | 0.701 |
| 0.7-0.8 | 10 | 0.727 | 0.7 |

Totals: predicted mean 45.99, real 46.32; total MAE 10.42.

Reading: log loss below the ratings baseline is the engine earning its matchup structure; a margin slope near 1.0 means its spreads are on scale (below 1 = too confident, above = too timid). The calibration table is the honesty check: a bin's realized rate should sit near its mean prediction.
