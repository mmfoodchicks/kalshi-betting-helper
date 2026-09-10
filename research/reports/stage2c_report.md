# Stage 2C: reconciling Sleeper's component means, weights fitted on 2022 to 2024

Commit 395c3af, base seed 20220902, 400 clustered bootstraps, 1,628 training team-weeks. Built 2026-09-10 20:58 UTC. Weights artifact research/data/reconcile_weights.json, SHA-256 500fb0dcfd4a30f2. No 2025 outcome is read; 2025 projections are not touched until Stage 2E. Model wls-identity v1.

## Method

Lambda: least-squares weight on the quarterback's figure in the blend that predicts the actual team quantity; clipped to [0, 1]; 90% clustered bootstrap by team-season. Variance: squared residual regressed on the projection level per position and component (a + b * m), a floored at 0 and b at 1e-6; shares a side's change among its players. Target factor: actual team targets over actual team attempts, pooled over the training team-weeks. Allocation: minimise sum(delta^2 / variance) subject to the sum of the deltas, bounds by an active set. Order: attempts, completions (bounded by the reconciled attempts and targets), yards, touchdowns (bounded by the reconciled receptions). The originals stay on every player as `means`; the reconciled line is `recon`. The legacy simulator keeps reading `means`.

## The weight on the quarterback's figure (lambda), per identity

| Identity | Team-weeks | lambda | 90% bootstrap | 2022 / 2023 / 2024 | MSE quarterback alone | MSE receivers' sum alone | MSE plain average | MSE blend | Bias quarterback | Bias sum | Actual mean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pass_att | 1,628 | 0.732 | [0.640, 0.841] | 0.55 / 0.66 / 0.98 | 55.00 | 59.70 | 54.81 | 54.27 | -0.68 | -0.79 | 31.6 |
| pass_cmp | 1,628 | 0.551 | [0.442, 0.700] | 0.36 / 0.44 / 0.91 | 32.80 | 33.39 | 31.67 | 31.66 | -0.95 | -1.00 | 21.5 |
| pass_yd | 1,628 | 0.780 | [0.674, 0.901] | 0.52 / 0.90 / 0.97 | 4555.56 | 4915.70 | 4574.58 | 4524.24 | -5.10 | -9.67 | 234.8 |
| pass_td | 1,628 | 0.433 | [0.246, 0.625] | 0.17 / 0.58 / 0.64 | 1.19 | 1.18 | 1.17 | 1.17 | +0.06 | +0.03 | 1.4 |

Target factor (actual team targets over actual attempts): 0.9504, 90% bootstrap [0.9475, 0.9530]. The quarterback side of the attempts identity is attempts times this factor.

## Residual variance by projection level (the allocation weights)

| Position | Component | n | a | b | Residual sd by projection quartile (observed / fitted) |
|---|---|---|---|---|---|
| QB | pass_att | 1,651 | 48.859 | 0.8892 | [0.3-30.3] 8.49 / 8.53; [30.3-32.5] 8.95 / 8.77; [32.5-34.5] 8.82 / 8.87; [34.5-42.9] 8.96 / 9.01 |
| QB | pass_cmp | 1,650 | 13.498 | 1.2468 | [0.16-18.38] 6.22 / 5.76; [18.38-20.49] 6.13 / 6.14; [20.49-22.48] 6.08 / 6.34; [22.48-42.89] 6.48 / 6.64 |
| QB | pass_yd | 1,651 | 4306.900 | 5.0059 | [1.69-204.47] 69.80 / 72.12; [204.47-228.51] 76.02 / 73.43; [228.51-252.77] 76.93 / 74.22; [252.77-329.38] 72.07 / 75.30 |
| QB | pass_td | 1,651 | 0.438 | 0.5076 | [0.01-1.18] 0.96 / 0.97; [1.18-1.42] 1.03 / 1.05; [1.42-1.73] 1.17 / 1.11; [1.73-2.56] 1.18 / 1.20 |
| RB | rec_tgt | 4,669 | 0.450 | 1.3319 | [0.02-0.7] 0.97 / 0.97; [0.7-1.6] 1.43 / 1.39; [1.6-2.9] 1.91 / 1.86; [2.9-9] 2.35 / 2.39 |
| RB | rec | 4,651 | 0.395 | 1.2156 | [0.01-0.49] 0.80 / 0.84; [0.49-1.21] 1.21 / 1.18; [1.21-2.21] 1.61 / 1.57; [2.21-7.13] 2.02 / 2.04 |
| RB | rec_yd | 4,652 | 38.332 | 15.6029 | [0.06-3.59] 7.77 / 8.22; [3.59-8.95] 11.08 / 11.56; [8.95-16.44] 16.10 / 15.37; [16.44-56.34] 19.93 / 20.06 |
| RB | rec_td | 3,569 | 0.007 | 0.9754 | [0.01-0.03] 0.17 / 0.16; [0.03-0.06] 0.24 / 0.23; [0.06-0.09] 0.27 / 0.27; [0.09-0.4] 0.36 / 0.37 |
| WR | rec_tgt | 7,415 | 1.114 | 1.2365 | [0.1-1.5] 1.44 / 1.46; [1.5-3.9] 2.08 / 2.08; [3.9-6.3] 2.76 / 2.72; [6.3-13.4] 3.26 / 3.29 |
| WR | rec | 7,414 | 0.437 | 1.2006 | [0.05-0.95] 1.05 / 1.02; [0.95-2.39] 1.53 / 1.55; [2.39-3.98] 2.08 / 2.06; [3.98-9.12] 2.55 / 2.57 |
| WR | rec_yd | 7,415 | 77.674 | 22.7302 | [0.3-11.1] 15.14 / 14.43; [11.1-29] 22.67 / 22.84; [29-49.49] 30.96 / 30.99; [49.49-117.4] 39.40 / 39.54 |
| WR | rec_td | 7,027 | 0.018 | 0.8260 | [0.01-0.09] 0.25 / 0.24; [0.09-0.2] 0.38 / 0.37; [0.2-0.32] 0.47 / 0.48; [0.32-0.83] 0.61 / 0.61 |
| TE | rec_tgt | 4,137 | 0.482 | 1.3654 | [0.1-0.8] 1.06 / 1.07; [0.8-1.9] 1.44 / 1.48; [1.9-4.1] 2.15 / 2.12; [4.1-9.6] 2.84 / 2.85 |
| TE | rec | 4,136 | 0.324 | 1.3106 | [0.03-0.53] 0.90 / 0.85; [0.53-1.24] 1.11 / 1.20; [1.24-2.72] 1.71 / 1.69; [2.72-7.25] 2.32 / 2.32 |
| TE | rec_yd | 4,137 | 34.816 | 18.7484 | [0.17-5.2] 9.95 / 9.40; [5.2-12.66] 13.06 / 13.86; [12.66-28.1] 20.39 / 20.16; [28.1-88.57] 28.61 / 28.57 |
| TE | rec_td | 3,651 | 0.009 | 0.9143 | [0.01-0.06] 0.25 / 0.21; [0.06-0.12] 0.29 / 0.29; [0.12-0.23] 0.40 / 0.41; [0.23-0.96] 0.53 / 0.55 |

## Applied to the training seasons' projections (1,632 team-weeks)

| Identity | Gap before, abs median | abs p95 | max | Gap after, max abs |
|---|---|---|---|---|
| pass_att | 0.063 | 0.209 | 0.534 | 0.00e+00 |
| pass_cmp | 0.068 | 0.231 | 0.651 | 0.00e+00 |
| pass_yd | 0.072 | 0.228 | 0.593 | 0.00e+00 |
| pass_td | 0.111 | 0.390 | 1.062 | 0.00e+00 |

Flags: {"gap_pass_cmp": 34, "gap_pass_td": 35, "gap_pass_att": 18, "gap_pass_yd": 21, "no_qb_pass_yd": 1, "no_qb_pass_td": 1, "no_qb_pass_cmp": 2, "no_qb": 1, "qb_bound_pass_cmp": 4}. Tripwires fired on 71 team-weeks (4.4%); the first forty: [2022, 6, 'MIA'] {"pass_cmp": 0.3073, "pass_td": 0.5596}; [2022, 6, 'NE'] {"pass_cmp": 0.3556}; [2022, 8, 'IND'] {"pass_cmp": 0.3559}; [2022, 9, 'BAL'] {"pass_att": -0.3631, "pass_cmp": -0.3421, "pass_yd": -0.3646}; [2022, 9, 'TEN'] {"pass_att": 0.3047, "pass_cmp": 0.4062}; [2022, 10, 'ARI'] {"pass_cmp": 0.3031}; [2022, 10, 'CHI'] {"pass_cmp": 0.3017}; [2022, 11, 'ARI'] {"pass_cmp": 0.3182}; [2022, 11, 'DEN'] {"pass_cmp": -0.3209, "pass_yd": -0.3207}; [2022, 12, 'LAR'] {"pass_att": 0.3002, "pass_cmp": 0.6507}; [2022, 14, 'SF'] {"pass_att": 0.3445, "pass_cmp": 0.3848, "pass_yd": 0.4071}; [2022, 16, 'ARI'] {"pass_cmp": 0.366}; [2022, 16, 'LAR'] {"pass_yd": -0.3029}; [2022, 16, 'NO'] {}; [2022, 16, 'TEN'] {"pass_att": 0.3921, "pass_cmp": 0.4492, "pass_yd": 0.3469, "pass_td": 0.6267}; [2022, 17, 'LAR'] {"pass_att": -0.3304, "pass_cmp": -0.3173, "pass_yd": -0.3672}; [2022, 18, 'BAL'] {"pass_td": 0.5844}; [2022, 18, 'LAR'] {"pass_att": -0.3274, "pass_cmp": -0.3258, "pass_yd": -0.4101}; [2022, 18, 'MIA'] {"pass_cmp": 0.3394, "pass_yd": 0.5925}; [2022, 18, 'SF'] {"pass_yd": 0.3322}; [2023, 1, 'CHI'] {"pass_cmp": 0.3037}; [2023, 2, 'ARI'] {"pass_td": 0.5125}; [2023, 3, 'ARI'] {"pass_cmp": -0.6013}; [2023, 4, 'CLE'] {"pass_cmp": 0.4483, "pass_yd": 0.3959, "pass_td": 0.5673}; [2023, 6, 'CLE'] {"pass_td": 0.5253}; [2023, 8, 'SEA'] {"pass_td": 0.54}; [2023, 9, 'CLE'] {"pass_td": 0.5778}; [2023, 9, 'MIN'] {"pass_cmp": 0.4486, "pass_td": 0.5949}; [2023, 9, 'NYJ'] {"pass_att": -0.3291}; [2023, 10, 'NYG'] {"pass_cmp": 0.4493}; [2023, 11, 'CLE'] {"pass_td": 0.5412}; [2023, 11, 'LV'] {"pass_td": 0.5435}; [2023, 12, 'ATL'] {"pass_td": 0.5412}; [2023, 12, 'CIN'] {"pass_td": 0.6}; [2023, 12, 'CLE'] {"pass_td": 0.6292}; [2023, 12, 'LV'] {"pass_td": 0.631}; [2023, 12, 'NYG'] {"pass_cmp": 0.45, "pass_td": 0.6029}; [2023, 12, 'TEN'] {"pass_td": 0.6753}; [2023, 13, 'CAR'] {"pass_att": -0.3146}; [2023, 14, 'MIN'] {"pass_td": 0.5268}.

### Size of the changes by position and component

| Position / component | n | abs median | abs p90 | abs max | mean (signed) | relative abs median | relative abs p90 | relative abs max | share moved over 25% |
|---|---|---|---|---|---|---|---|---|---|
| QB/pass_att | 1,659 | 0.548 | 1.460 | 3.82 | -0.029 | 0.017 | 0.048 | 2.01 | 0.005 |
| QB/pass_cmp | 1,658 | 0.615 | 1.666 | 25.79 | -0.037 | 0.031 | 0.087 | 1.78 | 0.008 |
| QB/pass_td | 1,658 | 0.091 | 0.244 | 0.52 | -0.014 | 0.063 | 0.175 | 8.80 | 0.042 |
| QB/pass_yd | 1,658 | 3.485 | 8.782 | 21.96 | -1.012 | 0.016 | 0.043 | 1.32 | 0.007 |
| RB/rec | 4,698 | 0.043 | 0.174 | 1.87 | +0.002 | 0.044 | 0.145 | 1.06 | 0.031 |
| RB/rec_td | 3,598 | 0.003 | 0.011 | 0.08 | +0.001 | 0.060 | 0.157 | 0.49 | 0.017 |
| RB/rec_tgt | 4,717 | 0.069 | 0.286 | 1.55 | +0.008 | 0.054 | 0.166 | 1.31 | 0.043 |
| RB/rec_yd | 4,699 | 0.368 | 1.504 | 8.99 | +0.183 | 0.052 | 0.170 | 2.60 | 0.043 |
| TE/rec | 4,162 | 0.046 | 0.207 | 1.03 | +0.001 | 0.045 | 0.135 | 1.27 | 0.021 |
| TE/rec_td | 3,671 | 0.005 | 0.024 | 0.10 | +0.001 | 0.053 | 0.141 | 0.40 | 0.012 |
| TE/rec_tgt | 4,163 | 0.086 | 0.377 | 2.42 | +0.005 | 0.054 | 0.153 | 2.09 | 0.032 |
| TE/rec_yd | 4,163 | 0.577 | 2.744 | 21.41 | +0.287 | 0.055 | 0.165 | 2.54 | 0.037 |
| WR/rec | 7,474 | 0.069 | 0.283 | 3.00 | +0.003 | 0.039 | 0.120 | 1.16 | 0.017 |
| WR/rec_td | 7,083 | 0.008 | 0.030 | 0.19 | +0.002 | 0.050 | 0.139 | 1.00 | 0.017 |
| WR/rec_tgt | 7,475 | 0.149 | 0.560 | 2.84 | +0.009 | 0.052 | 0.157 | 2.32 | 0.042 |
| WR/rec_yd | 7,475 | 1.382 | 5.799 | 28.89 | +0.519 | 0.065 | 0.189 | 1.95 | 0.050 |

### DraftKings-point shift of the projected mean

| Position | n | mean (signed) | abs median | abs p90 | abs max | share over 1 point |
|---|---|---|---|---|---|---|
| QB | 1,660 | -0.098 | 0.447 | 1.176 | 2.77 | 0.161 |
| RB | 4,795 | +0.023 | 0.079 | 0.340 | 2.37 | 0.005 |
| TE | 4,166 | +0.036 | 0.114 | 0.535 | 3.42 | 0.025 |
| WR | 7,475 | +0.065 | 0.222 | 0.929 | 4.75 | 0.086 |

### In-sample accuracy check (training seasons; lambda was fitted on these same seasons, so this is not validation)

| Position / component | n | MAE original | MAE reconciled | change |
|---|---|---|---|---|
| QB/pass_att | 1,643 | 6.735 | 6.702 | -0.50% |
| QB/pass_cmp | 1,637 | 4.809 | 4.756 | -1.11% |
| QB/pass_td | 1,234 | 0.732 | 0.730 | -0.30% |
| QB/pass_yd | 1,638 | 57.603 | 57.256 | -0.60% |
| RB/rec | 2,830 | 1.244 | 1.239 | -0.38% |
| RB/rec_td | 233 | 0.976 | 0.974 | -0.23% |
| RB/rec_tgt | 3,087 | 1.429 | 1.424 | -0.34% |
| RB/rec_yd | 2,778 | 12.269 | 12.257 | -0.10% |
| TE/rec | 2,682 | 1.331 | 1.330 | -0.07% |
| TE/rec_td | 457 | 0.907 | 0.905 | -0.26% |
| TE/rec_tgt | 3,010 | 1.580 | 1.577 | -0.19% |
| TE/rec_yd | 2,673 | 16.334 | 16.209 | -0.77% |
| WR/rec | 5,516 | 1.507 | 1.503 | -0.29% |
| WR/rec_td | 1,259 | 0.828 | 0.824 | -0.48% |
| WR/rec_tgt | 6,118 | 1.967 | 1.959 | -0.41% |
| WR/rec_yd | 5,491 | 23.017 | 23.002 | -0.06% |

## Invariants (synthetic teams)

- identities_hold_exactly: pass
- originals_preserved: pass
- nothing_negative: pass
- receptions_within_targets: pass
- touchdowns_within_receptions: pass
- completions_within_attempts: pass
- target_between_sides: pass
- largest_projection_moves_most: pass
- idempotent: pass
- balanced_team_unchanged: pass
- no_qb_untouched_and_flagged: pass
- tripwire_fires_on_large_gap: pass
- two_quarterbacks_reconcile: pass
- bound_respected_when_binding: pass

## What this says

The reconciliation exists so that a team-budget simulator can hand the quarterback's day out by shares that sum to one; it is not a projection improvement and the in-sample table above is reported so that nobody mistakes it for one. The weights are frozen in the artifact and hashed; the constrained simulator (Stage 2D) reads `recon`, the legacy simulator keeps `means`, and a board built on either says which.
