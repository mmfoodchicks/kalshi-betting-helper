# NFL deep engine -- out-of-sample walk-forward validation
Built 2026-10-16 12:58 UTC by research/nfl_deep_validate.py. Each season is scored with tables REFIT ON THE SEASONS BEFORE IT; profiles from that season's games before each week only, the previous season's units as the prior; 400 simulated games per real game per arm, weeks 2-18. 2024: 256 games, tables fit on [2023]; 2025: 256 games, tables fit on [2023, 2024].

PD = the points-differential ratings, standing in for Kalshi's win totals (which have no history here).

**Note (2026-10-29).** The engine's sack clamp (`nfl_deep_sim._snap`: a sack that nets yards stops at the 1, found by the game viewer, research/reports/nfl_viewer.md 6.1) postdates these figures. Its measured effect: -0.056 points a game, 1.8% of final scores, home-win rates within +/-0.3 points at 8,000 seeds -- inside this report's own noise. The fourth-down refit (6.2 there) will move them and is re-validated on its own.

## All seasons
| model | n | log loss | Brier | accuracy | margin MAE | slope actual~pred |
|---|---|---|---|---|---|---|
| deep engine alone (no anchor) | 512 | 0.6596 | 0.2332 | 0.6133 | 10.609 | 1.87 |
| deep engine, PD-anchored at 6/(6+g) | 512 | 0.6418 | 0.2251 | 0.6211 | 10.402 | 1.385 |
| deep engine, PD-anchored at weight 1 | 512 | 0.6317 | 0.221 | 0.623 | 10.498 | 0.878 |
| points-differential ratings (nfl_season._wp) | 512 | 0.6331 | 0.2217 | 0.6172 | - | - |
| constant home 0.54 | 512 | 0.6915 | 0.2487 | 0.5312 | - | - |
| 0.30 engine alone + 0.70 PD | 512 | 0.6356 | 0.2224 | 0.625 | - | - |
| 0.30 anchored 6/(6+g) + 0.70 PD | 512 | 0.6336 | 0.2218 | 0.627 | - | - |
| 0.30 anchored weight 1 + 0.70 PD | 512 | 0.6316 | 0.2211 | 0.6152 | - | - |

## 2024
| model | n | log loss | Brier | accuracy | margin MAE | slope actual~pred |
|---|---|---|---|---|---|---|
| deep engine alone (no anchor) | 256 | 0.6508 | 0.2294 | 0.6523 | 10.418 | 2.375 |
| deep engine, PD-anchored at 6/(6+g) | 256 | 0.6327 | 0.2213 | 0.6602 | 10.18 | 1.48 |
| deep engine, PD-anchored at weight 1 | 256 | 0.6216 | 0.2165 | 0.6523 | 10.366 | 0.835 |
| points-differential ratings (nfl_season._wp) | 256 | 0.6294 | 0.2201 | 0.6445 | - | - |
| constant home 0.54 | 256 | 0.6926 | 0.2497 | 0.5234 | - | - |
| 0.30 engine alone + 0.70 PD | 256 | 0.6303 | 0.2202 | 0.6484 | - | - |
| 0.30 anchored 6/(6+g) + 0.70 PD | 256 | 0.6284 | 0.2196 | 0.6562 | - | - |
| 0.30 anchored weight 1 + 0.70 PD | 256 | 0.626 | 0.2186 | 0.6406 | - | - |

## 2025
| model | n | log loss | Brier | accuracy | margin MAE | slope actual~pred |
|---|---|---|---|---|---|---|
| deep engine alone (no anchor) | 256 | 0.6684 | 0.237 | 0.5742 | 10.799 | 1.525 |
| deep engine, PD-anchored at 6/(6+g) | 256 | 0.6509 | 0.229 | 0.582 | 10.624 | 1.278 |
| deep engine, PD-anchored at weight 1 | 256 | 0.6418 | 0.2256 | 0.5938 | 10.629 | 0.948 |
| points-differential ratings (nfl_season._wp) | 256 | 0.6368 | 0.2234 | 0.5898 | - | - |
| constant home 0.54 | 256 | 0.6904 | 0.2477 | 0.5391 | - | - |
| 0.30 engine alone + 0.70 PD | 256 | 0.6408 | 0.2247 | 0.6016 | - | - |
| 0.30 anchored 6/(6+g) + 0.70 PD | 256 | 0.6387 | 0.2239 | 0.5977 | - | - |
| 0.30 anchored weight 1 + 0.70 PD | 256 | 0.6372 | 0.2236 | 0.5898 | - | - |

## Paired log-loss differences, all seasons (per game; negative = the first is better; +/- one SE)
- deep engine alone (no anchor) minus points-differential ratings (nfl_season._wp): +0.0265 +/- 0.0100
- deep engine, PD-anchored at 6/(6+g) minus points-differential ratings (nfl_season._wp): +0.0087 +/- 0.0063
- deep engine, PD-anchored at weight 1 minus points-differential ratings (nfl_season._wp): -0.0014 +/- 0.0045
- 0.30 engine alone + 0.70 PD minus points-differential ratings (nfl_season._wp): +0.0025 +/- 0.0032
- 0.30 anchored 6/(6+g) + 0.70 PD minus points-differential ratings (nfl_season._wp): +0.0005 +/- 0.0020
- 0.30 anchored weight 1 + 0.70 PD minus points-differential ratings (nfl_season._wp): -0.0015 +/- 0.0013
- deep engine, PD-anchored at weight 1 minus deep engine, PD-anchored at 6/(6+g): -0.0101 +/- 0.0073

## Calibration (favourite's side), all seasons
| arm | predicted | n | mean predicted | realized |
|---|---|---|---|---|
| deep engine alone (no anchor) | 0.5-0.6 | 372 | 0.547 | 0.585 |
| deep engine alone (no anchor) | 0.6-0.7 | 117 | 0.64 | 0.684 |
| deep engine alone (no anchor) | 0.7-0.8 | 23 | 0.729 | 0.739 |
| deep engine, PD-anchored at weight 1 | 0.5-0.6 | 227 | 0.545 | 0.518 |
| deep engine, PD-anchored at weight 1 | 0.6-0.7 | 131 | 0.65 | 0.649 |
| deep engine, PD-anchored at weight 1 | 0.7-0.8 | 94 | 0.748 | 0.691 |
| deep engine, PD-anchored at weight 1 | 0.8-0.9 | 53 | 0.834 | 0.849 |
| deep engine, PD-anchored at weight 1 | 0.9-1.0 | 7 | 0.927 | 1.0 |

Totals (engine alone): predicted mean 44.53, real 46.07; total MAE 10.35.
