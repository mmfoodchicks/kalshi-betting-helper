# Stage 2E, part two: the blind 2025 validation, legacy against constrained

Commit fc10b3e, 272 games of 2025, 400 worlds per game per model, constrained seeds 20250901 and 20250902, frozen parameters df906d36b9effb01 (fitted at commit fc10b3e). Built 2026-09-10 22:36 UTC (72.0s). This is the first read of any 2025 outcome. Legacy: nfl_dfs_sim.simulate_game, unseeded, points rescaled to the Sleeper mean (production). Nothing was tuned here.

## The gate (written before the numbers)

- CRPS, mean over the seven roles (lower is better): constrained 3.804, legacy 3.937 -> pass (no worse than 1%)
- Pair-correlation RMSE against observed 2025: constrained 0.0859, legacy 0.2565 -> pass (lower)
- PIT uniformity, pooled roles (Kolmogorov distance, lower is better): constrained 0.0280, legacy 0.1776 -> pass (no worse)

**Verdict: the constrained model is promoted to the live board as the alternate (not the default).**

## Individual calibration by role

| Role | n | Model | PIT histogram (10 bins) | PIT KS | 80% coverage | 90% coverage | CRPS |
|---|---|---|---|---|---|---|---|
| QB1 | 544 | legacy | 0.13 0.07 0.10 0.09 0.07 0.13 0.11 0.10 0.10 0.09 | 0.043 | 0.783 | 0.868 | 4.514 |
| QB1 | 544 | constrained | 0.15 0.08 0.11 0.06 0.09 0.12 0.11 0.09 0.10 0.07 | 0.065 | 0.772 | 0.842 | 4.554 |
| RB1 | 544 | legacy | 0.20 0.09 0.09 0.09 0.07 0.07 0.06 0.10 0.07 0.17 | 0.108 | 0.640 | 0.741 | 4.722 |
| RB1 | 544 | constrained | 0.08 0.08 0.10 0.12 0.11 0.10 0.10 0.12 0.08 0.11 | 0.055 | 0.814 | 0.912 | 4.613 |
| RB2 | 540 | legacy | 0.36 0.06 0.05 0.03 0.04 0.05 0.07 0.09 0.11 0.15 | 0.280 | 0.487 | 0.567 | 2.988 |
| RB2 | 540 | constrained | 0.14 0.09 0.13 0.08 0.10 0.09 0.09 0.11 0.07 0.11 | 0.054 | 0.767 | 0.881 | 2.809 |
| WR1 | 543 | legacy | 0.23 0.09 0.09 0.08 0.07 0.07 0.06 0.08 0.10 0.14 | 0.139 | 0.624 | 0.740 | 4.764 |
| WR1 | 543 | constrained | 0.10 0.10 0.13 0.09 0.11 0.11 0.09 0.08 0.11 0.08 | 0.050 | 0.827 | 0.902 | 4.697 |
| WR2 | 543 | legacy | 0.32 0.08 0.06 0.05 0.05 0.09 0.06 0.07 0.08 0.14 | 0.226 | 0.534 | 0.646 | 3.711 |
| WR2 | 543 | constrained | 0.11 0.11 0.13 0.10 0.11 0.10 0.10 0.09 0.08 0.08 | 0.064 | 0.858 | 0.945 | 3.543 |
| WR3 | 542 | legacy | 0.40 0.04 0.04 0.03 0.03 0.03 0.04 0.07 0.13 0.19 | 0.314 | 0.415 | 0.513 | 3.215 |
| WR3 | 542 | constrained | 0.12 0.15 0.10 0.07 0.08 0.07 0.10 0.11 0.10 0.11 | 0.073 | 0.852 | 0.935 | 2.903 |
| TE1 | 544 | legacy | 0.26 0.08 0.06 0.06 0.06 0.06 0.07 0.09 0.09 0.17 | 0.175 | 0.572 | 0.658 | 3.645 |
| TE1 | 544 | constrained | 0.10 0.10 0.10 0.10 0.10 0.10 0.11 0.08 0.10 0.11 | 0.019 | 0.820 | 0.928 | 3.510 |

Pooled legacy: n 3800, PIT histogram 0.27 0.07 0.07 0.06 0.06 0.07 0.07 0.09 0.10 0.15, KS 0.1776, mean CRPS 3.937.

Pooled constrained: n 3800, PIT histogram 0.11 0.10 0.11 0.09 0.10 0.10 0.10 0.10 0.09 0.10, KS 0.0280, mean CRPS 3.804.

## Role-pair correlations on 2025

| Pair | Observed 2025 | Bootstrap sd | Legacy | Constrained | Constrained, seed 2 |
|---|---|---|---|---|---|
| QB1/WR1 | 0.379 | 0.040 | 0.559 | 0.391 | 0.387 |
| QB1/WR2 | 0.380 | 0.037 | 0.487 | 0.327 | 0.326 |
| QB1/WR3 | 0.224 | 0.042 | 0.404 | 0.245 | 0.247 |
| QB1/TE1 | 0.338 | 0.038 | 0.483 | 0.294 | 0.295 |
| QB1/RB1 | 0.083 | 0.040 | 0.308 | 0.069 | 0.068 |
| QB1/RB2 | 0.055 | 0.044 | 0.225 | 0.077 | 0.079 |
| WR1/WR2 | 0.024 | 0.045 | 0.544 | -0.006 | -0.010 |
| WR1/TE1 | -0.006 | 0.047 | 0.520 | -0.003 | -0.004 |
| WR1/RB1 | 0.058 | 0.042 | 0.293 | -0.019 | -0.024 |
| WR2/TE1 | 0.064 | 0.040 | 0.453 | 0.004 | 0.005 |
| RB1/RB2 | -0.071 | 0.037 | 0.187 | -0.039 | -0.037 |
| QB1/opp QB1 | 0.235 | 0.076 | 0.181 | 0.033 | 0.039 |
| QB1/opp WR1 | 0.094 | 0.055 | 0.160 | 0.024 | 0.023 |
| WR1/opp WR1 | 0.100 | 0.077 | 0.124 | 0.015 | 0.020 |
| RB1/opp RB1 | -0.130 | 0.072 | 0.184 | -0.001 | -0.002 |
| QB1/opp RB1 | 0.108 | 0.064 | 0.220 | 0.028 | 0.029 |
| team offense/opp offense | 0.262 | 0.063 | 0.323 | 0.082 | 0.087 |

RMSE against the observed pairs: legacy 0.2565, constrained 0.0859 (seed 2: 0.0846).

## Marginal tails by role (projection ratio; floored on a 3-point projection)

| Role | Statistic | Observed | Legacy | Constrained |
|---|---|---|---|---|
| QB1 | ratio_sd | 0.471 | 0.470 | 0.467 |
| QB1 | p_over_1_5 | 0.132 | 0.146 | 0.158 |
| QB1 | p_over_2 | 0.029 | 0.033 | 0.036 |
| QB1 | p_under_half | 0.158 | 0.131 | 0.102 |
| RB1 | ratio_sd | 0.590 | 0.423 | 0.613 |
| RB1 | p_over_1_5 | 0.176 | 0.123 | 0.181 |
| RB1 | p_over_2 | 0.075 | 0.025 | 0.069 |
| RB1 | p_under_half | 0.180 | 0.075 | 0.214 |
| RB2 | ratio_sd | 0.886 | 0.570 | 0.909 |
| RB2 | p_over_1_5 | 0.209 | 0.174 | 0.233 |
| RB2 | p_over_2 | 0.113 | 0.071 | 0.131 |
| RB2 | p_under_half | 0.338 | 0.096 | 0.315 |
| WR1 | ratio_sd | 0.610 | 0.459 | 0.669 |
| WR1 | p_over_1_5 | 0.190 | 0.139 | 0.216 |
| WR1 | p_over_2 | 0.063 | 0.033 | 0.090 |
| WR1 | p_under_half | 0.236 | 0.101 | 0.228 |
| WR2 | ratio_sd | 0.705 | 0.497 | 0.794 |
| WR2 | p_over_1_5 | 0.174 | 0.151 | 0.222 |
| WR2 | p_over_2 | 0.076 | 0.046 | 0.112 |
| WR2 | p_under_half | 0.327 | 0.106 | 0.293 |
| WR3 | ratio_sd | 1.090 | 0.578 | 0.966 |
| WR3 | p_over_1_5 | 0.236 | 0.154 | 0.237 |
| WR3 | p_over_2 | 0.140 | 0.077 | 0.138 |
| WR3 | p_under_half | 0.407 | 0.113 | 0.365 |
| TE1 | ratio_sd | 0.800 | 0.497 | 0.761 |
| TE1 | p_over_1_5 | 0.213 | 0.148 | 0.214 |
| TE1 | p_over_2 | 0.106 | 0.047 | 0.102 |
| TE1 | p_under_half | 0.259 | 0.103 | 0.278 |

## Stacks

| Structure | Statistic | Observed | Legacy | Constrained |
|---|---|---|---|---|
| QB+WR1 | ratio_var | 0.195 | 0.168 | 0.213 |
| QB+WR1 | p_over_1_5 | 0.114 | 0.120 | 0.156 |
| QB+WR1 | p_over_2 | 0.031 | 0.019 | 0.035 |
| QB+WR1 | p_under_0_5 | 0.142 | 0.087 | 0.099 |
| QB+WR1+WR2 | ratio_var | 0.167 | 0.153 | 0.182 |
| QB+WR1+WR2 | p_over_1_5 | 0.085 | 0.111 | 0.138 |
| QB+WR1+WR2 | p_over_2 | 0.017 | 0.016 | 0.025 |
| QB+WR1+WR2 | p_under_0_5 | 0.133 | 0.076 | 0.083 |
| QB+WR1+TE1 | ratio_var | 0.169 | 0.153 | 0.176 |
| QB+WR1+TE1 | p_over_1_5 | 0.094 | 0.111 | 0.134 |
| QB+WR1+TE1 | p_over_2 | 0.022 | 0.015 | 0.023 |
| QB+WR1+TE1 | p_under_0_5 | 0.120 | 0.076 | 0.079 |
| game 5: QB+WR1+WR2+oppWR1+oppWR2 | ratio_var | 0.105 | 0.099 | 0.110 |
| game 5: QB+WR1+WR2+oppWR1+oppWR2 | p_over_1_5 | 0.069 | 0.070 | 0.088 |
| game 5: QB+WR1+WR2+oppWR1+oppWR2 | p_over_2 | 0.004 | 0.004 | 0.007 |
| game 5: QB+WR1+WR2+oppWR1+oppWR2 | p_under_0_5 | 0.061 | 0.033 | 0.034 |
| game 6: +RB1 | ratio_var | 0.087 | 0.087 | 0.088 |
| game 6: +RB1 | p_over_1_5 | 0.059 | 0.059 | 0.065 |
| game 6: +RB1 | p_over_2 | 0.000 | 0.002 | 0.003 |
| game 6: +RB1 | p_under_0_5 | 0.039 | 0.025 | 0.021 |

Stack PIT (the actual stack total's percentile in each model's distribution; share above the 90th and below the 10th should be 0.10 each):

| Structure | Model | n | PIT KS | Share above p90 | Share below p10 |
|---|---|---|---|---|---|
| QB+WR1 | legacy | 543 | 0.062 | 0.096 | 0.153 |
| QB+WR1 | constrained | 543 | 0.053 | 0.066 | 0.142 |
| QB+WR1+WR2 | legacy | 542 | 0.063 | 0.079 | 0.155 |
| QB+WR1+WR2 | constrained | 542 | 0.062 | 0.061 | 0.149 |
| QB+WR1+TE1 | legacy | 543 | 0.046 | 0.087 | 0.138 |
| QB+WR1+TE1 | constrained | 543 | 0.052 | 0.074 | 0.129 |
| game 5: QB+WR1+WR2+oppWR1+oppWR2 | legacy | 540 | 0.075 | 0.085 | 0.152 |
| game 5: QB+WR1+WR2+oppWR1+oppWR2 | constrained | 540 | 0.101 | 0.074 | 0.150 |
| game 6: +RB1 | legacy | 540 | 0.061 | 0.096 | 0.119 |
| game 6: +RB1 | constrained | 540 | 0.099 | 0.085 | 0.139 |

## Seed robustness (constrained, two seeds)

CRPS differs by 0.0053, pair RMSE by 0.0013, PIT KS by 0.0011.
