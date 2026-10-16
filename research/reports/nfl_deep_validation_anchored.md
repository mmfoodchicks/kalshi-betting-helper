# NFL deep engine -- walk-forward validation, 2025 regular season (anchored to the points-differential ratings)
Built 2026-10-16 09:57 UTC. Profiles from the games before each week only (prior: 2024 units), levels anchored to the PD ratings at 6/(6+games) as production anchors to Kalshi; 300 simulated games per real game; weeks 2-18; 256 games.

## Whole span
| model | n | log loss | Brier | accuracy | margin MAE | margin RMSE | slope actual~pred |
|---|---|---|---|---|---|---|---|
| deep engine | 256 | 0.6549 | 0.2307 | 0.5938 | 10.732 | 13.671 | 1.257 |
| points-differential ratings (nfl_season._wp) | 256 | 0.6368 | 0.2234 | 0.5898 | - | - | - |
| constant home 0.54 | 256 | 0.6904 | 0.2477 | 0.5391 | - | - | - |

## By stretch of the season
| weeks | n | deep log loss | deep Brier | deep acc | PD log loss | PD Brier | deep margin MAE | slope |
|---|---|---|---|---|---|---|---|---|
| 2-6 | 77 | 0.6698 | 0.2357 | 0.4935 | 0.6667 | 0.2347 | 10.364 | 0.824 |
| 7-12 | 85 | 0.62 | 0.2148 | 0.6941 | 0.5902 | 0.2026 | 10.623 | 1.392 |
| 13-18 | 94 | 0.6742 | 0.241 | 0.5851 | 0.6544 | 0.2329 | 11.132 | 1.482 |

## Calibration (favourite's side), deep engine
| predicted | n | mean predicted | realized |
|---|---|---|---|
| 0.5-0.6 | 146 | 0.541 | 0.521 |
| 0.6-0.7 | 88 | 0.638 | 0.676 |
| 0.7-0.8 | 21 | 0.738 | 0.762 |
| 0.8-0.9 | 1 | 0.802 | 1.0 |

Totals: predicted mean 46.03, real 46.32; total MAE 10.56.

Reading: log loss below the ratings baseline is the engine earning its matchup structure; a margin slope near 1.0 means its spreads are on scale (below 1 = too confident, above = too timid). The calibration table is the honesty check: a bin's realized rate should sit near its mean prediction.
