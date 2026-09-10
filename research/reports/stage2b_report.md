# Stage 2B: historical evidence, 2022 to 2024 fitting seasons, 2025 held out

Commit 3006cc9, base seed 20220901, 400 clustered bootstraps. Built 2026-09-10 21:16 UTC. 2025 is loaded and coverage-counted only; no 2025 outcome enters any number below except the coverage table. Residual method: actual minus projected DraftKings points (components, no bonuses on the projection side), standardised by the role's residual sd on the training seasons. Roles: pregame only: QB1 by projected pass attempts; RB1-3 by projected carries plus targets; WR1-4 and TE1-2 by projected targets, receiving yards as the tie-break. Bootstrap: clusters are team-seasons (the repeated weeks of one offense resample together); for opponent pairs the cluster is the first-named side's team-season; 90% intervals.

## Coverage

| Season | Weeks proj / act | Games | Real projected player-weeks | Matched with a box score | Projected, did not play | Played, no projection | Kept | Excluded (reasons) | Feed placeholders |
|---|---|---|---|---|---|---|---|---|---|
| 2022 | 18 / 18 | 272 | 6,370 | 6,319 | 51 | 747 | 6,917 | 200 (negative_stat 2, pos_out_of_scope 196, team_conflict 2) | 48,956 |
| 2023 | 18 / 18 | 272 | 5,989 | 5,945 | 44 | 1,107 | 6,899 | 197 (negative_stat 4, pos_out_of_scope 193) | 48,974 |
| 2024 | 18 / 18 | 272 | 5,990 | 5,940 | 50 | 1,064 | 6,927 | 127 (negative_stat 3, pos_out_of_scope 124) | 49,017 |
| 2025 (holdout) | 18 / 18 | 272 | 6,165 | 6,097 | 68 | 934 | 6,988 | 111 (negative_stat 1, pos_out_of_scope 107, team_conflict 3) | 48,971 |

Every row keeps its flags; a hard flag (position out of scope, no team, no opponent, team conflict between the feeds on a real projection, inconsistent opponent pair, receptions above targets, a negative counting stat) excludes the row with the reason counted. Soft flags (no real projection, did not play) keep the row and only bar it from residual statistics. Stable Sleeper player ids join the feeds; no name joins. Feed placeholder rows (no projection and no game) are counted, not stored.

## Role-pair co-movement (standardised projection residuals)

| Pair | n | Distinct pairs | Team-seasons | Pooled r | Within-pair demeaned r (pairs 6+) | 90% clustered bootstrap | 2022 / 2023 / 2024 |
|---|---|---|---|---|---|---|---|
| QB1/WR1 | 1,627 | 263 | 96 | 0.387 | 0.399 (87) | [0.348, 0.422] | 0.37 / 0.42 / 0.36 |
| QB1/WR2 | 1,628 | 361 | 96 | 0.355 | 0.418 (90) | [0.307, 0.401] | 0.35 / 0.33 / 0.38 |
| QB1/WR3 | 1,617 | 435 | 96 | 0.213 | 0.201 (95) | [0.175, 0.251] | 0.19 / 0.26 / 0.21 |
| QB1/TE1 | 1,627 | 242 | 96 | 0.284 | 0.305 (87) | [0.242, 0.321] | 0.28 / 0.33 / 0.24 |
| QB1/RB1 | 1,628 | 268 | 96 | 0.066 | 0.043 (87) | [0.030, 0.099] | 0.04 / 0.11 / 0.02 |
| QB1/RB2 | 1,614 | 344 | 96 | 0.066 | 0.079 (98) | [0.025, 0.106] | 0.07 / 0.04 / 0.09 |
| WR1/WR2 | 1,627 | 333 | 96 | 0.017 | 0.054 (92) | [-0.027, 0.060] | 0.04 / -0.04 / 0.05 |
| WR1/TE1 | 1,625 | 261 | 96 | -0.007 | -0.017 (86) | [-0.046, 0.027] | 0.01 / 0.01 / -0.05 |
| WR1/RB1 | 1,627 | 285 | 96 | -0.015 | -0.019 (92) | [-0.055, 0.023] | -0.00 / -0.01 / -0.04 |
| WR2/TE1 | 1,626 | 360 | 96 | 0.004 | 0.030 (95) | [-0.035, 0.043] | -0.03 / -0.03 / 0.05 |
| RB1/RB2 | 1,614 | 313 | 96 | 0.000 | 0.012 (98) | [-0.056, 0.060] | -0.00 / 0.05 / -0.04 |
| QB1/opp QB1 | 814 | 651 | 91 | 0.202 | - (1) | [0.136, 0.270] | 0.25 / 0.15 / 0.19 |
| QB1/opp WR1 | 1,627 | 1308 | 96 | 0.102 | 0.474 (2) | [0.053, 0.150] | 0.18 / -0.02 / 0.15 |
| WR1/opp WR1 | 813 | 671 | 90 | 0.035 | 0.423 (2) | [-0.027, 0.096] | 0.08 / -0.03 / 0.07 |
| RB1/opp RB1 | 814 | 692 | 91 | -0.078 | - (1) | [-0.129, -0.023] | -0.07 / -0.08 / -0.10 |
| QB1/opp RB1 | 1,628 | 1308 | 96 | 0.050 | - (0) | [0.007, 0.095] | 0.05 / 0.05 / 0.03 |
| team offense/opp offense | 816 | 448 | 91 | 0.207 | 0.216 (48) | [0.152, 0.264] | 0.28 / 0.11 / 0.22 |

## Marginal distribution around the projection, by role

| Role | n | Proj mean | Actual mean | Bias pts | Resid sd | CV | Skew | Ratio p10 / p50 / p90 / p95 / p99 | P under 0.5x | P over 1.5x | P over 2x |
|---|---|---|---|---|---|---|---|---|---|---|---|
| QB1 | 1,630 | 16.9 | 17.2 | +0.25 | 7.87 | 0.49 | 0.42 | 0.45 / 0.98 / 1.67 / 1.88 / 2.34 | 0.120 | 0.162 | 0.031 |
| RB1 | 1,630 | 13.6 | 14.5 | +0.99 | 8.27 | 0.60 | 0.80 | 0.37 / 0.98 / 1.92 / 2.27 / 3.04 | 0.182 | 0.235 | 0.085 |
| RB2 | 1,616 | 5.9 | 6.1 | +0.20 | 5.44 | 0.99 | 1.81 | 0.07 / 0.78 / 2.33 / 3.14 / 5.81 | 0.329 | 0.229 | 0.142 |
| WR1 | 1,629 | 13.9 | 14.7 | +0.81 | 8.92 | 0.65 | 0.86 | 0.32 / 0.94 / 1.95 / 2.30 / 2.91 | 0.198 | 0.228 | 0.092 |
| WR2 | 1,630 | 9.8 | 10.0 | +0.20 | 7.54 | 0.79 | 1.09 | 0.18 / 0.88 / 2.08 / 2.56 / 3.73 | 0.282 | 0.231 | 0.113 |
| WR3 | 1,618 | 5.9 | 5.7 | -0.23 | 5.77 | 1.05 | 1.55 | 0.00 / 0.68 / 2.35 / 3.19 / 5.84 | 0.395 | 0.224 | 0.141 |
| TE1 | 1,628 | 8.1 | 8.4 | +0.30 | 6.25 | 0.82 | 1.05 | 0.11 / 0.90 / 2.10 / 2.71 / 3.87 | 0.287 | 0.248 | 0.120 |

## Components

QB1 passing yards: ratio sd 0.332, residual sd 73.9 yards, p10/p50/p90/p99 0.58 / 0.97 / 1.41 / 1.77. Passing TDs: projected mean 1.48, distribution {"0": 0.244, "1": 0.35, "2": 0.258, "3": 0.101, "4": 0.038, "5+": 0.008}. Interceptions: projected mean 0.79, distribution {"0": 0.504, "1": 0.328, "2": 0.129, "3+": 0.04}. QB rushing yards ratio sd 1.550.

RB1: rush_att: proj 14.3, ratio sd 0.44, p10/p90 0.52/1.53; rec_tgt: proj 3.2, ratio sd 0.79, p10/p90 0.00/2.00; rec: proj 2.5, ratio sd 0.92, p10/p90 0.00/2.26; rush_yd: proj 60.5, ratio sd 0.63, p10/p90 0.35/1.89; rec_yd: proj 18.6, ratio sd 1.51, p10/p90 0.00/2.54; rush_td_dist {"0": 0.628, "1": 0.294, "2": 0.067, "3+": 0.011}; rec_td_dist {"0": 0.905, "1": 0.089, "2": 0.006, "3+": 0.0}

RB2: rush_att: proj 5.6, ratio sd 1.60, p10/p90 0.14/2.08; rec_tgt: proj 1.7, ratio sd 1.50, p10/p90 0.00/2.67; rec: proj 1.3, ratio sd 1.82, p10/p90 0.00/2.86; rush_yd: proj 23.3, ratio sd 2.65, p10/p90 0.00/2.56; rec_yd: proj 9.8, ratio sd 2.49, p10/p90 0.00/3.09; rush_td_dist {"0": 0.865, "1": 0.118, "2": 0.017, "3+": 0.001}; rec_td_dist {"0": 0.96, "1": 0.037, "2": 0.002, "3+": 0.001}

WR1: rec_tgt: proj 7.8, ratio sd 0.43, p10/p90 0.49/1.56; rec: proj 5.0, ratio sd 0.52, p10/p90 0.39/1.70; rec_yd: proj 63.1, ratio sd 0.63, p10/p90 0.31/1.88; rec_td_dist {"0": 0.669, "1": 0.279, "2": 0.047, "3+": 0.006}

WR2: rec_tgt: proj 5.6, ratio sd 0.53, p10/p90 0.35/1.71; rec: proj 3.5, ratio sd 0.65, p10/p90 0.26/1.86; rec_yd: proj 43.9, ratio sd 0.80, p10/p90 0.14/2.05; rec_td_dist {"0": 0.767, "1": 0.201, "2": 0.028, "3+": 0.004}

WR3: rec_tgt: proj 3.5, ratio sd 0.82, p10/p90 0.19/2.00; rec: proj 2.2, ratio sd 1.04, p10/p90 0.00/2.20; rec_yd: proj 26.1, ratio sd 1.25, p10/p90 0.00/2.47; rec_td_dist {"0": 0.865, "1": 0.12, "2": 0.015, "3+": 0.0}

TE1: rec_tgt: proj 4.7, ratio sd 0.64, p10/p90 0.29/1.79; rec: proj 3.2, ratio sd 0.78, p10/p90 0.18/2.08; rec_yd: proj 34.0, ratio sd 0.87, p10/p90 0.00/2.25; rec_td_dist {"0": 0.803, "1": 0.168, "2": 0.026, "3+": 0.002}

## Target competition

1,604 team-weeks with WR1, WR2, WR3, TE1 and RB1 all present. Team targets actual over projected: mean 1.009, sd 0.250. Mean actual share by role: {"WR1": 0.262, "WR2": 0.185, "WR3": 0.113, "TE1": 0.16, "RB1": 0.11}. Share residual sd (actual share minus projected share): {"WR1": 0.094, "WR2": 0.085, "WR3": 0.073, "TE1": 0.08, "RB1": 0.069}. Week-to-week share sd of one player, by role: {"WR1": 0.088, "RB1": 0.061, "TE1": 0.073, "WR2": 0.078, "WR3": 0.067}.

Correlation of share residuals across roles (the competition targets for the team-budget model):

| Pair | r |
|---|---|
| WR1/WR2 | -0.219 |
| WR1/WR3 | -0.193 |
| WR1/TE1 | -0.255 |
| WR1/RB1 | -0.231 |
| WR2/WR3 | -0.223 |
| WR2/TE1 | -0.194 |
| WR2/RB1 | -0.182 |
| WR3/TE1 | -0.184 |
| WR3/RB1 | -0.094 |
| TE1/RB1 | -0.103 |

When WR1 beats his projected targets by three or more (and when he misses by three or more), the others' target residual, and the slope of each on WR1's residual:

| Role | Mean residual when WR1 hot | When WR1 cold | Slope on WR1 |
|---|---|---|---|
| WR2 | 0.22 | -0.30 | 0.081 |
| WR3 | 0.14 | -0.24 | 0.035 |
| TE1 | 0.35 | -0.10 | 0.056 |
| RB1 | 0.21 | -0.03 | 0.033 |

## Touchdowns

1,632 team-weeks. Team passing TDs: mean 1.42, distribution {"0": 0.218, "1": 0.36, "2": 0.267, "3": 0.107, "4": 0.039, "5+": 0.009}. Team rushing TDs: mean 0.89, distribution {"0": 0.417, "1": 0.352, "2": 0.165, "3": 0.056, "4+": 0.01}. Passing TDs equal the sum of teammates' receiving TDs in 98.9% of team-weeks (the rest are laterals, receivers outside the four positions, or feed gaps). Two or more teammates catch a TD in 0.849 of team-weeks with two or more passing TDs. Receiving TD share by role: {"WR1": 0.276, "WR2": 0.191, "TE1": 0.162, "WR3": 0.105, "RB1": 0.072, "TE2": 0.047, "WR4": 0.041, "RB2": 0.03, "other TE": 0.03, "other WR": 0.029, "RB3": 0.011, "other RB": 0.004, "QB1": 0.002}. Rushing TD share by role: {"RB1": 0.517, "QB1": 0.207, "RB2": 0.17, "RB3": 0.035, "WR2": 0.016, "other TE": 0.01, "WR1": 0.01, "other QB": 0.009, "WR3": 0.008, "WR4": 0.006, "other RB": 0.005, "TE2": 0.003, "other WR": 0.002, "TE1": 0.001}.

## Sleeper projection consistency audit (projections only)

| Identity | Team-weeks | Relative gap (sum minus QB, over QB): median | mean | p10 | p90 | p95 of abs | Share where the sum exceeds the QB | By season |
|---|---|---|---|---|---|---|---|---|
| pass_yd vs sum rec_yd | 1,630 | -0.016 | -0.017 | -0.149 | +0.112 | 0.228 | 0.44 | {"2022": -0.023, "2023": -0.022, "2024": -0.008} |
| pass_td vs sum rec_td | 1,630 | -0.017 | +0.009 | -0.200 | +0.253 | 0.390 | 0.45 | {"2022": -0.056, "2023": 0.043, "2024": 0.039} |
| pass_cmp vs sum rec | 1,631 | +0.001 | +0.003 | -0.138 | +0.144 | 0.231 | 0.50 | {"2022": 0.008, "2023": 0.009, "2024": -0.007} |

Worst cases: pass_yd vs sum rec_yd: [2022, 18, 'LAR'] QB 242.8 vs sum 143.3, [2024, 7, 'LAC'] QB 230.6 vs sum 133.6, [2024, 16, 'NO'] QB 195.2 vs sum 98.4; pass_td vs sum rec_td: [2022, 3, 'LAC'] QB 2.4 vs sum 1.5, [2022, 9, 'DET'] QB 1.8 vs sum 1.0, [2023, 18, 'CIN'] QB 0.8 vs sum 1.7; pass_cmp vs sum rec: [2023, 3, 'ARI'] QB 42.9 vs sum 17.1, [2023, 13, 'PIT'] QB 34.9 vs sum 19.9, [2024, 7, 'LAC'] QB 21.3 vs sum 12.8.

## Stack totals against their summed projection

| Structure | n | Mean ratio | Variance | p10 / p50 / p90 / p95 / p99 | P over 1.5x | P over 2x | P under 0.5x |
|---|---|---|---|---|---|---|---|
| QB+WR1 | 1,627 | 1.036 | 0.211 | 0.51 / 0.98 / 1.66 / 1.86 / 2.37 | 0.153 | 0.034 | 0.097 |
| QB+WR2 | 1,628 | 1.023 | 0.231 | 0.47 / 0.96 / 1.67 / 1.89 / 2.50 | 0.145 | 0.037 | 0.118 |
| QB+TE1 | 1,627 | 1.028 | 0.215 | 0.48 / 0.96 / 1.65 / 1.89 / 2.36 | 0.142 | 0.038 | 0.110 |
| QB+RB1 | 438 | 1.018 | 0.143 | 0.57 / 0.97 / 1.53 / 1.66 / 2.05 | 0.105 | 0.014 | 0.066 |
| QB+WR1+WR2 | 1,625 | 1.035 | 0.183 | 0.54 / 0.98 / 1.63 / 1.82 / 2.24 | 0.145 | 0.025 | 0.076 |
| QB+WR1+TE1 | 1,624 | 1.039 | 0.179 | 0.54 / 0.99 / 1.58 / 1.81 / 2.28 | 0.132 | 0.028 | 0.074 |
| QB+WR1+RB1 | 1,625 | 1.048 | 0.141 | 0.60 / 1.00 / 1.56 / 1.74 / 2.02 | 0.122 | 0.012 | 0.050 |
| QB+WR1+oppWR1 | 1,624 | 1.040 | 0.147 | 0.58 / 1.00 / 1.55 / 1.75 / 2.09 | 0.120 | 0.016 | 0.050 |
| QB+WR1+WR2+oppWR1 (bring-back) | 1,622 | 1.038 | 0.136 | 0.60 / 1.00 / 1.53 / 1.71 / 2.01 | 0.114 | 0.011 | 0.046 |
| game 5: QB+WR1+WR2+oppWR1+oppWR2 | 1,620 | 1.035 | 0.119 | 0.62 / 1.00 / 1.49 / 1.65 / 1.96 | 0.095 | 0.009 | 0.043 |
| game 6: +RB1 | 1,618 | 1.042 | 0.092 | 0.68 / 1.02 / 1.44 / 1.58 / 1.87 | 0.076 | 0.003 | 0.023 |

## Receiving backs as stack partners (training seasons only, no policy chosen)

| Bucket | n | QB/RB residual r | Joint top decile (1% if independent) | Joint 1.5x boom | P QB boom | P RB boom |
|---|---|---|---|---|---|---|
| targets 0-2 | 2468 | 0.040 | 0.011 | 0.046 | 0.167 | 0.254 |
| targets 2-3 | 885 | 0.061 | 0.015 | 0.041 | 0.154 | 0.234 |
| targets 3-4 | 650 | 0.092 | 0.012 | 0.040 | 0.152 | 0.215 |
| targets 4-5 | 316 | 0.104 | 0.003 | 0.025 | 0.171 | 0.199 |
| targets 5-+ | 181 | 0.029 | 0.011 | 0.017 | 0.160 | 0.193 |
| 20-35% | 1330 | 0.059 | 0.011 | 0.042 | 0.174 | 0.240 |
| 50%+ | 1313 | 0.099 | 0.011 | 0.046 | 0.155 | 0.239 |
| 35-50% | 1539 | 0.029 | 0.011 | 0.036 | 0.152 | 0.227 |
| recv share <20% | 318 | 0.037 | 0.009 | 0.047 | 0.192 | 0.280 |

## What this says for the simulator (no parameters chosen here)

The fitting stage reads research/data/stage2b_stats.json. The within-team pair correlations, the share-residual correlations and volatilities, the marginal and component dispersions, the touchdown distributions and the stack ratio distributions are the moments the constrained model will be fitted to on these three seasons; 2025 stays closed until the parameters are frozen.
