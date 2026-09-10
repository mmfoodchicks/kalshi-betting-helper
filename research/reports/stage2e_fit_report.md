# Stage 2E, part one: the constrained simulator fitted on 2022 to 2024 and frozen

Commit fc10b3e, base seed 20220904 (common random numbers), K 40 worlds per game for the search and 120 for the final table, 816 training games, 200 bootstrap replicates for the moment scales. Built 2026-09-10 22:34 UTC (3596.9s). Parameter artifact research/data/csim_params.json, SHA-256 df906d36b9effb01. 2025 is not read in any form. Method: Nelder-Mead on the mean squared standardised moment mismatch, scales from a within-season game bootstrap of the observed moments, common random numbers.

Objective (mean squared standardised mismatch over 97 moments): start 13.44, fitted 4.24 after 475 evaluations; at K=120 with the frozen values 4.70.

## Fitted parameters

| Parameter | Start | Fitted | Profile range (objective within one z-squared unit) | Second fit, other random numbers | Objective without it |
|---|---|---|---|---|---|
| env_sd | 0.120 | 0.1241 | [0.124, 0.124] | 0.1425 (+0.0184) | 6.38 (+2.14) |
| env_rush | 0.500 | 0.1318 | [0.132, 0.132] | 0.3247 (+0.1929) | 4.40 (+0.16) |
| script | 0.100 | 0.1025 | [0.102, 0.102] | 0.0759 (-0.0267) | 4.81 (+0.57) |
| script_rush | 0.120 | 0.1284 | [0.103, 0.128] | 0.1440 (+0.0156) | 4.43 (+0.19) |
| att_sd | 0.160 | 0.0075 | [0.007, 0.007] | 0.0212 (+0.0137) | 4.63 (+0.40) |
| share_sd | 0.350 | 0.2787 | [0.279, 0.279] | 0.2578 (-0.0208) | 8.47 (+4.23) |
| cmp_sd | 0.250 | 0.2269 | [0.227, 0.227] | 0.2019 (-0.0250) | 4.35 (+0.11) |
| eff_sd | 0.150 | 0.0993 | [0.099, 0.099] | 0.0802 (-0.0191) | 4.42 (+0.19) |
| ypr_sd | 0.450 | 0.7055 | [0.706, 0.706] | 0.7045 (-0.0010) | 6.56 (+2.33) |
| td_vol | 1.000 | 0.4248 | [0.425, 0.425] | 0.4722 (+0.0474) | 4.60 (+0.37) |
| td_eff | 0.500 | 0.5486 | [0.549, 0.549] | 0.7246 (+0.1760) | 4.40 (+0.17) |
| rush_sd | 0.200 | 0.3001 | [0.300, 0.300] | 0.2697 (-0.0304) | 5.34 (+1.10) |
| rshare_sd | 0.300 | 0.7468 | [0.747, 0.747] | 0.7413 (-0.0055) | 5.82 (+1.58) |
| ypc_sd | 0.500 | 0.8634 | [0.788, 1.313] | 0.9283 (+0.0649) | 4.53 (+0.29) |
| rtd_vol | 1.000 | 1.2449 | [1.245, 1.245] | 1.1931 (-0.0518) | 4.80 (+0.57) |

The fit's own Monte Carlo: at K=40 with the fitting seed the frozen parameters read 4.24, at K=120 they read 4.70. The difference is the fit reading the noise of its own common random numbers, and 4.70 is the honest figure. Where a profile range below reads as a single point, the search step (half a simplex step) already moved the objective by more than one z-squared unit: the interval is narrower than the step, not zero.

Second fit (seed 20220905) objective 4.14; largest parameter difference 0.1929. Smallest-model pass pinned nothing: every parameter buys at least one unit of z-squared. 

## Every moment at the frozen values (training seasons)

| Moment | Simulated | Observed | Bootstrap scale | z |
|---|---|---|---|---|
| marg RB1 p_over_1_5 | 0.177 | 0.234 | 0.010 | -5.6 |
| comp QB1 pass_att ratio_sd | 0.244 | 0.275 | 0.006 | -5.1 |
| tgt share_corr WR1/WR2 | -0.310 | -0.191 | 0.024 | -5.1 |
| comp WR1 rec_tgt ratio_sd | 0.468 | 0.427 | 0.009 | +4.7 |
| marg WR3 p_under_half | 0.339 | 0.397 | 0.013 | -4.6 |
| comp RB1 rush_att ratio_sd | 0.552 | 0.442 | 0.024 | +4.5 |
| td rush 4+ | 0.023 | 0.010 | 0.003 | +4.5 |
| td rush 0 | 0.470 | 0.417 | 0.013 | +4.2 |
| comp RB1 rec_tgt ratio_sd | 0.700 | 0.794 | 0.024 | -4.0 |
| tgt share_resid_sd WR1 | 0.098 | 0.091 | 0.002 | +3.6 |
| pair QB1/opp QB1 | 0.035 | 0.202 | 0.047 | -3.5 |
| td pass 5+ | 0.019 | 0.009 | 0.003 | +3.5 |
| comp WR3 rec_yd ratio_sd | 1.065 | 1.251 | 0.056 | -3.3 |
| pair team offense/opp offense | 0.083 | 0.208 | 0.039 | -3.2 |
| marg RB1 p_under_half | 0.215 | 0.182 | 0.011 | +3.1 |
| td rush 1 | 0.319 | 0.352 | 0.011 | -3.1 |
| comp WR1 rec_yd ratio_sd | 0.675 | 0.631 | 0.014 | +3.1 |
| tgt share_resid_sd WR3 | 0.066 | 0.070 | 0.001 | -3.0 |
| comp QB1 pass_yd ratio_sd | 0.351 | 0.332 | 0.006 | +3.0 |
| td rush 2 | 0.139 | 0.165 | 0.009 | -2.9 |
| td pass 2 | 0.236 | 0.267 | 0.011 | -2.9 |
| td pass 0 | 0.247 | 0.217 | 0.011 | +2.7 |
| comp WR1 rec ratio_sd | 0.548 | 0.518 | 0.011 | +2.6 |
| marg WR3 ratio_sd | 0.925 | 1.018 | 0.035 | -2.6 |
| marg WR1 p_under_half | 0.223 | 0.198 | 0.010 | +2.5 |
| td pass 1 | 0.335 | 0.360 | 0.011 | -2.4 |
| marg WR3 p_over_1_5 | 0.237 | 0.212 | 0.011 | +2.3 |
| marg RB1 p_over_2 | 0.069 | 0.084 | 0.007 | -2.3 |
| comp RB1 rec_yd ratio_sd | 0.963 | 1.511 | 0.241 | -2.3 |
| comp TE1 rec_tgt ratio_sd | 0.602 | 0.641 | 0.017 | -2.2 |
| tgt share_resid_sd RB1 | 0.064 | 0.067 | 0.001 | -2.2 |
| pair QB1/opp WR1 | 0.021 | 0.102 | 0.037 | -2.2 |
| tgt team_ratio_sd | 0.258 | 0.248 | 0.005 | +2.1 |
| pair QB1/WR3 | 0.265 | 0.213 | 0.026 | +2.0 |
| marg RB2 p_under_half | 0.296 | 0.321 | 0.013 | -1.9 |
| marg RB1 ratio_sd | 0.614 | 0.639 | 0.014 | -1.9 |
| marg RB2 ratio_sd | 0.887 | 0.987 | 0.053 | -1.9 |
| marg TE1 p_over_1_5 | 0.224 | 0.245 | 0.011 | -1.8 |
| tgt share_corr WR2/WR3 | -0.169 | -0.207 | 0.022 | +1.7 |
| marg WR1 ratio_sd | 0.675 | 0.649 | 0.015 | +1.7 |
| pair RB1/opp RB1 | -0.007 | -0.078 | 0.044 | +1.6 |
| tgt share_corr WR3/TE1 | -0.145 | -0.181 | 0.023 | +1.5 |
| tgt share_corr TE1/RB1 | -0.144 | -0.104 | 0.026 | -1.5 |
| stack QB+RB1 ratio_var | 0.149 | 0.158 | 0.006 | -1.5 |
| tgt share_corr WR1/WR3 | -0.213 | -0.176 | 0.025 | -1.5 |
| comp RB1 rush_yd ratio_sd | 0.599 | 0.633 | 0.025 | -1.4 |
| td pass 4 | 0.045 | 0.039 | 0.005 | +1.3 |
| tgt share_corr WR1/TE1 | -0.269 | -0.240 | 0.023 | -1.3 |
| stack game 5: QB+WR1+WR2+oppWR1+oppWR2 ratio_var | 0.110 | 0.118 | 0.007 | -1.2 |
| marg QB1 p_under_half | 0.110 | 0.120 | 0.008 | -1.2 |
| td rush 3 | 0.050 | 0.056 | 0.005 | -1.2 |
| td pass 3 | 0.117 | 0.107 | 0.008 | +1.2 |
| marg TE1 p_under_half | 0.296 | 0.282 | 0.012 | +1.2 |
| marg WR2 ratio_sd | 0.780 | 0.804 | 0.021 | -1.1 |
| comp WR2 rec_tgt ratio_sd | 0.544 | 0.533 | 0.010 | +1.1 |
| pair RB1/RB2 | -0.037 | 0.000 | 0.034 | -1.1 |
| tgt share_resid_sd WR2 | 0.084 | 0.083 | 0.002 | +1.1 |
| marg QB1 p_over_1_5 | 0.152 | 0.162 | 0.010 | -1.0 |
| marg QB1 ratio_sd | 0.468 | 0.478 | 0.010 | -1.0 |
| tgt share_corr WR3/RB1 | -0.116 | -0.090 | 0.027 | -1.0 |
| marg QB1 p_over_2 | 0.035 | 0.031 | 0.004 | +0.8 |
| pair QB1/WR2 | 0.335 | 0.355 | 0.025 | -0.8 |
| marg WR1 p_over_1_5 | 0.219 | 0.228 | 0.011 | -0.8 |
| stack game 6: +RB1 ratio_var | 0.088 | 0.092 | 0.004 | -0.8 |
| tgt share_corr WR2/TE1 | -0.199 | -0.181 | 0.023 | -0.8 |
| stack QB+WR1+TE1 ratio_var | 0.185 | 0.179 | 0.008 | +0.7 |
| stack QB+WR1 ratio_var | 0.218 | 0.211 | 0.010 | +0.7 |
| stack QB+WR2 ratio_var | 0.224 | 0.231 | 0.010 | -0.7 |
| marg RB2 p_over_2 | 0.125 | 0.131 | 0.009 | -0.7 |
| marg WR2 p_over_1_5 | 0.225 | 0.232 | 0.010 | -0.6 |
| td two_scorers_given_2plus | 0.841 | 0.849 | 0.013 | -0.6 |
| pair QB1/RB2 | 0.082 | 0.066 | 0.027 | +0.6 |
| pair QB1/opp RB1 | 0.029 | 0.050 | 0.038 | -0.6 |
| pair WR1/WR2 | 0.002 | 0.017 | 0.029 | -0.5 |
| marg WR2 p_under_half | 0.276 | 0.281 | 0.011 | -0.5 |
| pair QB1/TE1 | 0.295 | 0.284 | 0.023 | +0.5 |
| marg RB2 p_over_1_5 | 0.228 | 0.223 | 0.011 | +0.5 |
| marg WR3 p_over_2 | 0.132 | 0.128 | 0.008 | +0.5 |
| pair QB1/WR1 | 0.396 | 0.387 | 0.023 | +0.4 |
| marg TE1 p_over_2 | 0.115 | 0.118 | 0.009 | -0.4 |
| pair WR1/opp WR1 | 0.016 | 0.035 | 0.048 | -0.4 |
| marg TE1 ratio_sd | 0.816 | 0.824 | 0.022 | -0.3 |
| pair WR1/TE1 | 0.000 | -0.007 | 0.024 | +0.3 |
| stack QB+TE1 ratio_var | 0.218 | 0.215 | 0.010 | +0.3 |
| tgt share_corr WR1/RB1 | -0.212 | -0.218 | 0.026 | +0.2 |
| comp WR2 rec_yd ratio_sd | 0.800 | 0.804 | 0.018 | -0.2 |
| marg WR2 p_over_2 | 0.112 | 0.113 | 0.008 | -0.2 |
| pair QB1/RB1 | 0.060 | 0.066 | 0.027 | -0.2 |
| comp TE1 rec_yd ratio_sd | 0.863 | 0.868 | 0.023 | -0.2 |
| stack QB+WR1+RB1 ratio_var | 0.142 | 0.141 | 0.006 | +0.2 |
| tgt share_corr WR2/RB1 | -0.161 | -0.165 | 0.025 | +0.2 |
| pair WR1/RB1 | -0.010 | -0.015 | 0.025 | +0.2 |
| pair WR2/TE1 | 0.009 | 0.004 | 0.028 | +0.2 |
| stack QB+WR1+WR2 ratio_var | 0.184 | 0.183 | 0.008 | +0.2 |
| stack QB+WR1+oppWR1 ratio_var | 0.148 | 0.147 | 0.007 | +0.1 |
| marg WR1 p_over_2 | 0.093 | 0.092 | 0.008 | +0.1 |
| tgt share_resid_sd TE1 | 0.077 | 0.077 | 0.002 | +0.1 |

The parameters are now frozen and hashed; part two reads 2025 for the first time and judges both simulators on it without touching them.
