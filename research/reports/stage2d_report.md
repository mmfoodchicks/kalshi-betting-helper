# Stage 2D: the constrained team simulator as an alternate mode

Commit 3006cc9, base seed 20220903, 816 training games (2022-2024), 200 worlds per game for the invariants, 60 for the moments. Built 2026-09-10 21:19 UTC. Simulator constrained-team v1, parameters PROVISIONAL (unfitted), hash 59845131ac9e3fea. No 2025 outcome is read.

## The moment harness reproduces Stage 2B

Same table, two code paths: pair correlations agree to 0.0013, marginals (QB1, WR1) to 0.0000, component ratio sds to 0.0000, stack variances to 0.0000. Target competition: team target ratio sd 0.248 vs 0.250, WR1 share residual sd 0.091 vs 0.094, WR1/WR2 share correlation -0.191 vs -0.219 (the harness drops the catchers who did not play from both totals; 2B drops them from the actual total only). Team passing touchdowns {"0": 0.218, "1": 0.36, "2": 0.267, "3": 0.107, "4": 0.039, "5+": 0.009} vs {"0": 0.218, "1": 0.36, "2": 0.267, "3": 0.107, "4": 0.039, "5+": 0.009}; two scorers given two or more 0.849 vs 0.849. Check passes. The harness floors every ratio on a projection of at least 3 points (Sleeper once gave a starting quarterback 26.5 attempts and 0.3 points; at 200 worlds one such row would own a role's ratio sd), applied to both sides alike; 2B did not floor, so the depth roles differ by construction, harness vs 2B: RB1 0.639 vs 0.651, RB2 0.987 vs 1.423, WR2 0.804 vs 0.826, WR3 1.018 vs 1.185, TE1 0.824 vs 0.853.

## Per-world invariants on every training game

816 games x 200 worlds = 163,200 team-game-worlds in 6.1s: identity failures 0, bound failures 0, negative values 0, points that do not recompute from their components 0. All hold.

## Reproducibility and speed

Same seed, same worlds: True. Different seed, different worlds: True. One game at 60,000 worlds: 0.88s (a sixteen-game slate in well under a minute on the PC).

## The provisional parameters against the observed moments (the fit's starting point)

Objective (mean squared standardised mismatch over 97 moments): 7.63. The worst terms:

| Moment | Provisional simulator | Observed 2022-2024 | Scale | z |
|---|---|---|---|---|
| pair QB1/opp QB1 | 0.038 | 0.202 | 0.020 | -8.2 |
| pair team offense/opp offense | 0.080 | 0.208 | 0.020 | -6.4 |
| comp RB1 rec_yd ratio_sd | 0.933 | 1.511 | 0.091 | -6.4 |
| pair QB1/WR3 | 0.335 | 0.213 | 0.020 | +6.1 |
| comp RB1 rush_yd ratio_sd | 0.418 | 0.633 | 0.038 | -5.7 |
| pair RB1/opp RB1 | 0.025 | -0.078 | 0.020 | +5.2 |
| pair QB1/WR1 | 0.487 | 0.387 | 0.020 | +5.0 |
| pair QB1/TE1 | 0.384 | 0.284 | 0.020 | +5.0 |
| pair QB1/RB1 | 0.161 | 0.066 | 0.020 | +4.8 |
| marg RB2 ratio_sd | 0.752 | 0.987 | 0.049 | -4.8 |
| marg RB1 p_over_1_5 | 0.166 | 0.234 | 0.015 | -4.6 |
| tgt team_ratio_sd | 0.304 | 0.248 | 0.012 | +4.5 |
| pair QB1/RB2 | 0.150 | 0.066 | 0.020 | +4.2 |
| tgt share_corr WR1/WR2 | -0.314 | -0.191 | 0.030 | -4.1 |
| marg RB2 p_under_half | 0.260 | 0.321 | 0.015 | -4.1 |
| pair QB1/opp WR1 | 0.023 | 0.102 | 0.020 | -3.9 |
| td pass 0 | 0.263 | 0.217 | 0.012 | +3.8 |
| comp WR1 rec_tgt ratio_sd | 0.524 | 0.427 | 0.026 | +3.8 |
| td pass 2 | 0.223 | 0.267 | 0.012 | -3.7 |
| stack QB+WR1+TE1 ratio_var | 0.228 | 0.179 | 0.014 | +3.4 |
| td rush 0 | 0.457 | 0.417 | 0.012 | +3.4 |
| tgt share_resid_sd WR1 | 0.107 | 0.091 | 0.005 | +3.3 |
| marg RB1 ratio_sd | 0.533 | 0.639 | 0.032 | -3.3 |
| pair QB1/WR2 | 0.421 | 0.355 | 0.020 | +3.3 |
| pair WR1/TE1 | 0.058 | -0.007 | 0.020 | +3.3 |

These are the distances Stage 2E must close on the training seasons; the provisional values are a starting point and nothing is claimed for them. Not in production: the classic build accepts model="constrained" and stamps it, the PC does not request it, and the tab serves legacy boards.
