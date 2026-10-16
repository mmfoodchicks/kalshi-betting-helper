# The game-engine DFS worlds: the deep engine plays every world

**Corrected 2026-10-16.** The first version of this report scored worlds drawn from the wrong tables (see the correction below). Every number here is from the re-run; the flawed artifacts are kept in research/data/superseded/.

Built 2026-10-16 17:51 UTC by research/nfl_dfs_engine_eval.py. Development season 2024 (256 games, weeks 2-18, tables fit on [2023]); held-out test 2025 (256 games, tables fit on [2023, 2024]), 400 worlds per game per model, base seed 20261016. 2025 provenance: commit 2577f9e, dirty False; engine sources 8d6505d48e28fc49 (dirty False).

## What the engine model is

Every DFS world is one football game played snap by snap by the deep engine (nfl_deep_sim): possessions, the clock, fourth downs, the script, both offences against both defences. Each team's scoring level is the Sleeper drive model's expected points (nfl_game_sim, on this week's projections) and the deep engine's own, blended at the slate's deep weight, 0.30; the deep engine's offences are re-levelled until it plays at that target. The world's box score is dealt to the players by Sleeper's projected usage with the constrained model's fitted competition noise: every completion a catch by someone, receiving yards the quarterback's to the yard, every touchdown a catch or a carry. The defense is scored off what the OTHER offence did in the same game (points it scored with the ball, sacks, takeaways) plus its own return touchdowns, safeties and blocks; the kicker off his own field goals by distance and his tries. Player means are what the game deals, not pinned to Sleeper's number; Sleeper's projection stays the `proj` the field reads.

One term was added to the engine for the DFS worlds: the game's shared passing environment (one draw per game moving both offences' yards per completion and completion rate). Across the two teams the engine already matched real games (2023-2025, 816 games) on points (-0.055 real, -0.045 engine), plays (-0.53, -0.53), rushes (-0.56, -0.59), drives (+0.85, +0.85) and attempts against the opponent's points (+0.29, +0.32), and missed exactly one thing: the two teams' passing yards (+0.18 real, +0.03 engine). The term is off for every other caller (the slate's deep layer, the deep season).

## Correction: the first runs drew their worlds from the wrong tables

The replay is out of sample by construction: for each season it refits the engine's tables on the seasons before it, levels every team on those tables, and scores the worlds that come out. The worlds did not come out of those tables. `nfl_dfs_engine.play_worlds_parallel`, which plays a game's final worlds, did not pass the caller's tables on, so every scored world was played on the SHIPPED tables -- fit on 2023-2026, the season being scored included -- while the anchor had levelled the teams on the refit ones. Production was never affected (it passes the shipped tables); every number in the first version of this report was.

How it showed: on 2024 the scored worlds sat +1.29 points a team above the target the anchor had levelled them to (mean over 512 team-games; 2025 +0.14, its refit tables being closer to the shipped ones). Corrected, the same check reads +0.00 on 2024 and +0.01 on 2025 -- the anchor's own measuring noise.

What it touched: the choice of ENV_SD, the 2025 promotion test, and all three runs of the passing-volume anchor. The fix carries the tables into every slice; a guard plays a table where no field goal is good and requires none in the slices and none in simulate_game's final worlds (the old code made 1.8 a team). Everything was re-run in the order and under the rules written before the first runs, with that order written into the harness before the re-run started (commit b292fad), the choices frozen in commit 2577f9e before 2025 and 2026 were read again. Those two had each been read once by a flawed run; the rules leave nothing to choose on them. The flawed artifacts are kept in research/data/superseded/ with their final worlds summarised after the fact.

| | first runs (flawed) | corrected |
|---|---|---|
| ENV_SD, the 2024 rule's choice | 0.14 | **0.14** |
| 2025 engine offence CRPS (constrained's) | 3.8335 (3.8181) | 3.8388 (3.8181) |
| 2025 engine PIT KS | 0.0478 | 0.0496 |
| 2025 engine pair RMSE (constrained's) | 0.0475 (0.0857) | 0.0480 (0.0857) |
| 2025 engine DST / K CRPS | 3.0889 / 2.5630 | 3.0996 / 2.5488 |
| 2025 promotion gate | PROMOTED | **PROMOTED** |
| passing-volume weight, the 2024 rule's choice | 0.5 | **1** |
| 2026 anchor gate | SHIPPED | **SHIPPED** |

## The rules, written before 2025 was run

ENV_SD: the 2024 grid value (0, 0.05, 0.08, 0.11, 0.14) with the smallest cross-team pair RMSE among those whose offensive CRPS is within 0.5% of env 0's. Chosen: **0.14** (frozen in nfl_dfs_engine.ENV_SD and committed before the 2025 run; the run refuses any other value or a dirty tree).

Promotion to the boards' model on 2025: (a) offence CRPS <= 1.01 x the constrained model's; (b) pair-correlation RMSE below both legacy's and constrained's; (c) pooled PIT KS <= max(0.05, constrained's + 0.01); (d) defense and kicker CRPS each <= 1.02 x legacy's. Any link failing keeps the boards on legacy.

## 2025, held out (re-run after the correction)

| model | offence CRPS (7 roles) | PIT KS | pair RMSE | cross-team pair RMSE | DST CRPS | DST mean sim / real | K CRPS | K mean sim / real |
|---|---|---|---|---|---|---|---|---|
| legacy (production) | 3.9535 | 0.1772 | 0.2567 | 0.1516 | 3.1106 | 6.41 / 6.21 | 2.5937 | 8.50 / 8.73 |
| constrained (alternate) | 3.8181 | 0.0251 | 0.0857 | 0.1347 | 3.1110 | 6.41 / 6.21 | 2.5922 | 8.50 / 8.73 |
| engine, env 0.14 | 3.8388 | 0.0496 | 0.0480 | 0.0559 | 3.0996 | 5.86 / 6.21 | 2.5488 | 8.77 / 8.72 |

CRPS by role (lower is better) and 80% interval coverage:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 4.490 (79%) | 4.793 (63%) | 3.043 (48%) | 4.780 (63%) | 3.670 (54%) | 3.201 (41%) | 3.697 (57%) |
| constrained (alternate) | 4.548 (77%) | 4.702 (82%) | 2.856 (78%) | 4.696 (82%) | 3.498 (87%) | 2.872 (85%) | 3.554 (81%) |
| engine, env 0.14 | 4.522 (80%) | 4.690 (82%) | 2.858 (76%) | 4.779 (80%) | 3.543 (81%) | 2.893 (83%) | 3.586 (80%) |

Role-pair correlations of projection residuals, observed against each model:

| pair | observed | legacy (production) | constrained (alternate) | engine, env 0.14 |
|---|---|---|---|---|
| QB1/WR1 | 0.387 | 0.558 | 0.390 | 0.405 |
| QB1/WR2 | 0.369 | 0.489 | 0.327 | 0.336 |
| QB1/WR3 | 0.214 | 0.403 | 0.247 | 0.265 |
| QB1/TE1 | 0.342 | 0.482 | 0.291 | 0.329 |
| QB1/RB1 | 0.079 | 0.311 | 0.072 | 0.132 |
| QB1/RB2 | 0.065 | 0.229 | 0.075 | 0.120 |
| WR1/WR2 | 0.039 | 0.542 | -0.008 | -0.034 |
| WR1/TE1 | -0.001 | 0.516 | -0.007 | -0.009 |
| WR1/RB1 | 0.045 | 0.293 | -0.017 | 0.010 |
| WR2/TE1 | 0.057 | 0.451 | -0.000 | -0.001 |
| RB1/RB2 | -0.069 | 0.186 | -0.039 | -0.052 |
| QB1/opp QB1 | 0.224 | 0.183 | 0.027 | 0.250 |
| QB1/opp WR1 | 0.094 | 0.161 | 0.020 | 0.151 |
| WR1/opp WR1 | 0.105 | 0.124 | 0.013 | 0.079 |
| RB1/opp RB1 | -0.147 | 0.190 | -0.003 | -0.058 |
| QB1/opp RB1 | 0.101 | 0.220 | 0.032 | 0.022 |
| team offense/opp offense | 0.261 | 0.325 | 0.087 | 0.256 |

Mean DraftKings points by role -- each model's simulated mean, Sleeper's projection, what happened:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 17.50 / 17.50 / 17.15 | 14.94 / 14.94 / 15.10 | 6.28 / 6.28 / 6.24 | 14.04 / 14.04 / 13.65 | 9.46 / 9.46 / 8.70 | 5.42 / 5.42 / 5.38 | 9.04 / 9.04 / 9.28 |
| constrained (alternate) | 18.27 / 17.50 / 17.15 | 14.85 / 14.94 / 15.10 | 6.53 / 6.28 / 6.24 | 14.39 / 14.04 / 13.65 | 9.59 / 9.46 / 8.70 | 5.42 / 5.42 / 5.38 | 9.14 / 9.04 / 9.28 |
| engine, env 0.14 | 18.35 / 17.50 / 17.15 | 15.25 / 14.94 / 15.10 | 6.76 / 6.28 / 6.24 | 15.06 / 14.04 / 13.65 | 9.96 / 9.46 / 8.70 | 5.69 / 5.42 / 5.38 | 9.67 / 9.04 / 9.28 |

### The gate

- a_crps: PASS -- engine 3.8388 vs constrained 3.8181 (limit x1.01)
- b_pairs: PASS -- engine 0.0480 vs constrained 0.0857, legacy 0.2567
- c_pit: PASS -- engine 0.0496 vs limit 0.0500
- d_dst: PASS -- engine 3.0996 vs legacy 3.1106 (limit x1.02)
- d_k: PASS -- engine 2.5488 vs legacy 2.5937 (limit x1.02)
- **verdict: PROMOTED -- the boards are built on the engine model**

## 2024, development (where ENV_SD was chosen)

| model | offence CRPS (7 roles) | PIT KS | pair RMSE | cross-team pair RMSE | DST CRPS | DST mean sim / real | K CRPS | K mean sim / real |
|---|---|---|---|---|---|---|---|---|
| legacy (production) | 4.0758 | 0.1587 | 0.2715 | 0.1405 | 2.9698 | 6.57 / 6.17 | 2.6097 | 7.99 / 8.54 |
| constrained (alternate) | 3.9018 | 0.0413 | 0.0686 | 0.0996 | 2.9654 | 6.57 / 6.17 | 2.6047 | 7.99 / 8.54 |
| engine, env 0 | 3.9292 | 0.0348 | 0.0946 | 0.1396 | 2.8973 | 6.48 / 6.17 | 2.5252 | 8.48 / 8.52 |
| engine, env 0.05 | 3.9339 | 0.0343 | 0.0842 | 0.1193 | 2.8990 | 6.51 / 6.17 | 2.5293 | 8.46 / 8.52 |
| engine, env 0.08 | 3.9291 | 0.0321 | 0.0698 | 0.0887 | 2.8983 | 6.52 / 6.17 | 2.5245 | 8.44 / 8.52 |
| engine, env 0.11 | 3.9279 | 0.0328 | 0.0534 | 0.0499 | 2.8918 | 6.53 / 6.17 | 2.5206 | 8.47 / 8.52 |
| engine, env 0.14 | 3.9252 | 0.0291 | 0.0515 | 0.0259 | 2.9011 | 6.56 / 6.17 | 2.5220 | 8.44 / 8.52 |

CRPS by role (lower is better) and 80% interval coverage:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 4.579 (78%) | 4.784 (58%) | 2.837 (46%) | 4.911 (62%) | 4.628 (53%) | 3.143 (44%) | 3.649 (50%) |
| constrained (alternate) | 4.530 (77%) | 4.683 (81%) | 2.641 (76%) | 4.819 (82%) | 4.362 (79%) | 2.827 (85%) | 3.451 (84%) |
| engine, env 0 | 4.580 (76%) | 4.664 (80%) | 2.678 (75%) | 4.850 (78%) | 4.418 (75%) | 2.873 (83%) | 3.442 (79%) |
| engine, env 0.05 | 4.603 (75%) | 4.678 (79%) | 2.682 (75%) | 4.838 (78%) | 4.417 (74%) | 2.869 (82%) | 3.451 (79%) |
| engine, env 0.08 | 4.596 (78%) | 4.650 (81%) | 2.678 (76%) | 4.857 (78%) | 4.401 (74%) | 2.868 (84%) | 3.453 (80%) |
| engine, env 0.11 | 4.597 (80%) | 4.659 (80%) | 2.675 (74%) | 4.855 (77%) | 4.396 (74%) | 2.864 (83%) | 3.448 (79%) |
| engine, env 0.14 | 4.590 (80%) | 4.653 (81%) | 2.681 (75%) | 4.849 (79%) | 4.404 (75%) | 2.856 (83%) | 3.443 (81%) |

Role-pair correlations of projection residuals, observed against each model:

| pair | observed | legacy (production) | constrained (alternate) | engine, env 0 | engine, env 0.05 | engine, env 0.08 | engine, env 0.11 | engine, env 0.14 |
|---|---|---|---|---|---|---|---|---|
| QB1/WR1 | 0.364 | 0.560 | 0.386 | 0.349 | 0.359 | 0.369 | 0.386 | 0.404 |
| QB1/WR2 | 0.391 | 0.500 | 0.326 | 0.298 | 0.306 | 0.310 | 0.332 | 0.349 |
| QB1/WR3 | 0.213 | 0.421 | 0.262 | 0.236 | 0.242 | 0.253 | 0.264 | 0.281 |
| QB1/TE1 | 0.240 | 0.469 | 0.288 | 0.279 | 0.288 | 0.300 | 0.310 | 0.326 |
| QB1/RB1 | 0.024 | 0.304 | 0.054 | 0.091 | 0.099 | 0.104 | 0.111 | 0.127 |
| QB1/RB2 | 0.089 | 0.237 | 0.078 | 0.108 | 0.111 | 0.114 | 0.116 | 0.129 |
| WR1/WR2 | 0.051 | 0.563 | -0.004 | -0.056 | -0.049 | -0.042 | -0.026 | -0.008 |
| WR1/TE1 | -0.058 | 0.507 | -0.002 | -0.038 | -0.030 | -0.023 | -0.013 | 0.004 |
| WR1/RB1 | -0.042 | 0.294 | -0.007 | 0.003 | 0.007 | 0.009 | 0.014 | 0.028 |
| WR2/TE1 | 0.059 | 0.445 | 0.006 | -0.013 | -0.013 | -0.007 | 0.009 | 0.020 |
| RB1/RB2 | -0.027 | 0.187 | -0.033 | -0.053 | -0.050 | -0.046 | -0.048 | -0.042 |
| QB1/opp QB1 | 0.176 | 0.171 | 0.029 | 0.003 | 0.032 | 0.079 | 0.144 | 0.206 |
| QB1/opp WR1 | 0.144 | 0.151 | 0.021 | 0.020 | 0.037 | 0.059 | 0.093 | 0.132 |
| WR1/opp WR1 | 0.050 | 0.116 | 0.013 | 0.006 | 0.014 | 0.028 | 0.045 | 0.069 |
| RB1/opp RB1 | -0.083 | 0.184 | -0.000 | -0.083 | -0.083 | -0.076 | -0.075 | -0.073 |
| QB1/opp RB1 | 0.041 | 0.219 | 0.032 | -0.061 | -0.051 | -0.040 | -0.024 | -0.004 |
| team offense/opp offense | 0.204 | 0.308 | 0.084 | -0.039 | -0.004 | 0.052 | 0.120 | 0.183 |

Mean DraftKings points by role -- each model's simulated mean, Sleeper's projection, what happened:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 16.59 / 16.59 / 17.70 | 13.70 / 13.70 / 15.10 | 5.52 / 5.52 / 5.79 | 13.64 / 13.64 / 14.69 | 9.59 / 9.59 / 10.82 | 5.88 / 5.88 / 5.47 | 8.11 / 8.11 / 8.87 |
| constrained (alternate) | 17.18 / 16.59 / 17.70 | 13.66 / 13.70 / 15.10 | 5.82 / 5.52 / 5.79 | 14.10 / 13.64 / 14.69 | 9.81 / 9.59 / 10.82 | 5.94 / 5.88 / 5.47 | 8.27 / 8.11 / 8.87 |
| engine, env 0 | 16.92 / 16.59 / 17.70 | 14.76 / 13.70 / 15.10 | 6.31 / 5.52 / 5.79 | 14.61 / 13.64 / 14.69 | 10.11 / 9.59 / 10.82 | 6.16 / 5.88 / 5.47 | 8.60 / 8.11 / 8.87 |
| engine, env 0.05 | 16.93 / 16.59 / 17.70 | 14.77 / 13.70 / 15.10 | 6.30 / 5.52 / 5.79 | 14.64 / 13.64 / 14.69 | 10.13 / 9.59 / 10.82 | 6.14 / 5.88 / 5.47 | 8.61 / 8.11 / 8.87 |
| engine, env 0.08 | 16.99 / 16.59 / 17.70 | 14.73 / 13.70 / 15.10 | 6.30 / 5.52 / 5.79 | 14.63 / 13.64 / 14.69 | 10.14 / 9.59 / 10.82 | 6.18 / 5.88 / 5.47 | 8.63 / 8.11 / 8.87 |
| engine, env 0.11 | 17.07 / 16.59 / 17.70 | 14.72 / 13.70 / 15.10 | 6.29 / 5.52 / 5.79 | 14.67 / 13.64 / 14.69 | 10.15 / 9.59 / 10.82 | 6.18 / 5.88 / 5.47 | 8.65 / 8.11 / 8.87 |
| engine, env 0.14 | 17.09 / 16.59 / 17.70 | 14.71 / 13.70 / 15.10 | 6.28 / 5.52 / 5.79 | 14.69 / 13.64 / 14.69 | 10.16 / 9.59 / 10.82 | 6.19 / 5.88 / 5.47 | 8.65 / 8.11 / 8.87 |

## Limits, stated

- One held-out season is a measurement, not a proof. The 2025 outcomes had been opened before (Stage 2E, for the legacy-vs-constrained test, and once by this replay's flawed first run -- see the correction); nothing was tuned on them.
- The drive model that sets 70% of every team's points target is NOT out of sample on 2025: its level trims rest on Sleeper's 2025 optimism (team points 0.969 of projection over 544 team-games, touchdowns 0.939) and its per-stat pins (_PROJ_BIAS) are 2025's actual-over-projected ratios (nfl_game_sim). On 2025 the engine is partly scored against a level fitted to that season. 2024 shares only the home-field split (fitted on 2023-2025 home win rates); 2026 weeks 2-5 share none of it.
- The deep engine's two global constants (_FORM_SD, _HFA_YDS) were calibrated on 2023-2025 margins and home win rates: game-level, and 2025 is inside that fit.
- The replay anchors the deep layer to the points-differential ratings (Kalshi's win totals have no history) and leaves out the weekly roster factor; production uses both.
- The allocation noise (share_sd, ypr_sd, rshare_sd, ypc_sd) is the constrained model's 2022-2024 fit, taken as is; the engine replaces that model's team-level draws with a played game.
- Two-point conversions and offensive return touchdowns are not dealt to players (no model here deals them).
- The showdown money columns stay experimental: the field model is uncalibrated whatever simulator made the worlds.
- The engine's receivers on 2025 sat above what happened: WR1 15.06 simulated against 13.65 real (14.04 Sleeper), WR2 9.96 against 8.70, QB1 18.35 against 17.15; 13% of real outcomes fell in the bottom tenth of its distribution (10% for a calibrated model). On 2024: WR1 14.69 against 14.69.
- The environment term buys the yardage coupling at a measured cost in points independence: inside the engine's worlds the two teams' points in one game correlate +0.055 (2024) and +0.129 (2025) at the frozen value, against -0.106 with it off on 2024 (median over the games, world_summary). Real games pooled across matchups show -0.055, a figure that mixes in the spread's negative pull, so the real within-game value is near zero.
- The engine over-couples a quarterback with his own backs (QB1/RB1 0.132 against 0.079 real on 2025): the played game ties a team's rushing and passing to the same scoring.
- The lineup SHEET in the app stays on legacy-discrete: the server has no numpy and must not play thousands of snap-by-snap games inside a one-core web worker. The tournament boards, built on the PC, are where the engine runs.

## The passing-volume anchor (2026-10-16)

The owner: "anchor passing volume". The lever is a per-team pass-rate shift on the logit of every play call (`call_shift`), iterated with the points anchor, so each team's mean attempts land on (1 - w) x the engine's own + w x Sleeper's projected team attempts. The rules were written before any anchored run: w is chosen on 2024 alone (lowest offence CRPS among grid values whose pair and cross-team RMSE stay within 0.01 of w = 0's); 2025 is reported but cannot judge the fix, because the defect was found on it; the test is 2026 weeks 2-5.

The defect as first measured, on the flawed runs: attempts a team-game, engine / Sleeper / real, 2024 34.15 / 31.89 / 32.81, 2025 33.24 / 31.59 / 31.99. Corrected, the engine alone throws 2024 33.64 / 31.89 / 32.81, 2025 33.08 / 31.59 / 31.99, 2026 weeks 2-5 32.86 / 31.11 / 33.56.

### 2024, development (256 games): w = **1** chosen

| model | offence CRPS (7 roles) | PIT KS | pair RMSE | cross-team pair RMSE | DST CRPS | DST mean sim / real | K CRPS | K mean sim / real |
|---|---|---|---|---|---|---|---|---|
| constrained (alternate) | 3.9018 | 0.0413 | 0.0686 | 0.0996 | 2.9654 | 6.57 / 6.17 | 2.6047 | 7.99 / 8.54 |
| engine, env 0.14 | 3.9252 | 0.0291 | 0.0515 | 0.0259 | 2.9011 | 6.56 / 6.17 | 2.5220 | 8.44 / 8.52 |
| engine, env 0.14, attempts anchored at w 0.5 | 3.9288 | 0.0299 | 0.0502 | 0.0250 | 2.9016 | 6.43 / 6.17 | 2.5248 | 8.43 / 8.52 |
| engine, env 0.14, attempts anchored at w 1 | 3.9242 | 0.0337 | 0.0482 | 0.0250 | 2.9026 | 6.30 / 6.17 | 2.5285 | 8.38 / 8.52 |

CRPS by role (lower is better) and 80% interval coverage:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| constrained (alternate) | 4.530 (77%) | 4.683 (81%) | 2.641 (76%) | 4.819 (82%) | 4.362 (79%) | 2.827 (85%) | 3.451 (84%) |
| engine, env 0.14 | 4.590 (80%) | 4.653 (81%) | 2.681 (75%) | 4.849 (79%) | 4.404 (75%) | 2.856 (83%) | 3.443 (81%) |
| engine, env 0.14, attempts anchored at w 0.5 | 4.598 (81%) | 4.675 (81%) | 2.683 (75%) | 4.845 (79%) | 4.391 (76%) | 2.857 (83%) | 3.452 (80%) |
| engine, env 0.14, attempts anchored at w 1 | 4.608 (81%) | 4.657 (82%) | 2.697 (74%) | 4.793 (80%) | 4.386 (76%) | 2.849 (85%) | 3.479 (81%) |

Role-pair correlations of projection residuals, observed against each model:

| pair | observed | constrained (alternate) | engine, env 0.14 | engine, env 0.14, attempts anchored at w 0.5 | engine, env 0.14, attempts anchored at w 1 |
|---|---|---|---|---|---|
| QB1/WR1 | 0.364 | 0.386 | 0.404 | 0.403 | 0.391 |
| QB1/WR2 | 0.391 | 0.326 | 0.349 | 0.340 | 0.337 |
| QB1/WR3 | 0.213 | 0.262 | 0.281 | 0.271 | 0.272 |
| QB1/TE1 | 0.240 | 0.288 | 0.326 | 0.318 | 0.316 |
| QB1/RB1 | 0.024 | 0.054 | 0.127 | 0.120 | 0.106 |
| QB1/RB2 | 0.089 | 0.078 | 0.129 | 0.124 | 0.120 |
| WR1/WR2 | 0.051 | -0.004 | -0.008 | -0.017 | -0.021 |
| WR1/TE1 | -0.058 | -0.002 | 0.004 | -0.002 | -0.008 |
| WR1/RB1 | -0.042 | -0.007 | 0.028 | 0.028 | 0.029 |
| WR2/TE1 | 0.059 | 0.006 | 0.020 | 0.011 | 0.010 |
| RB1/RB2 | -0.027 | -0.033 | -0.042 | -0.047 | -0.052 |
| QB1/opp QB1 | 0.176 | 0.029 | 0.206 | 0.197 | 0.193 |
| QB1/opp WR1 | 0.144 | 0.021 | 0.132 | 0.130 | 0.126 |
| WR1/opp WR1 | 0.050 | 0.013 | 0.069 | 0.067 | 0.071 |
| RB1/opp RB1 | -0.083 | -0.000 | -0.073 | -0.068 | -0.070 |
| QB1/opp RB1 | 0.041 | 0.032 | -0.004 | -0.006 | -0.004 |
| team offense/opp offense | 0.204 | 0.084 | 0.183 | 0.184 | 0.184 |

Mean DraftKings points by role -- each model's simulated mean, Sleeper's projection, what happened:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| constrained (alternate) | 17.18 / 16.59 / 17.70 | 13.66 / 13.70 / 15.10 | 5.82 / 5.52 / 5.79 | 14.10 / 13.64 / 14.69 | 9.81 / 9.59 / 10.82 | 5.94 / 5.88 / 5.47 | 8.27 / 8.11 / 8.87 |
| engine, env 0.14 | 17.09 / 16.59 / 17.70 | 14.71 / 13.70 / 15.10 | 6.28 / 5.52 / 5.79 | 14.69 / 13.64 / 14.69 | 10.16 / 9.59 / 10.82 | 6.19 / 5.88 / 5.47 | 8.65 / 8.11 / 8.87 |
| engine, env 0.14, attempts anchored at w 0.5 | 16.94 / 16.59 / 17.70 | 14.91 / 13.70 / 15.10 | 6.33 / 5.52 / 5.79 | 14.35 / 13.64 / 14.69 | 9.95 / 9.59 / 10.82 | 6.06 / 5.88 / 5.47 | 8.45 / 8.11 / 8.87 |
| engine, env 0.14, attempts anchored at w 1 | 16.84 / 16.59 / 17.70 | 15.16 / 13.70 / 15.10 | 6.40 / 5.52 / 5.79 | 14.03 / 13.64 / 14.69 | 9.77 / 9.59 / 10.82 | 5.95 / 5.88 / 5.47 | 8.26 / 8.11 / 8.87 |
- engine, env 0.14, attempts anchored at w 0.5: attempts land 0.291 from target on average, points 0.429; mean pass-rate shift -0.0667 on the logit
- engine, env 0.14, attempts anchored at w 1: attempts land 0.263 from target on average, points 0.402; mean pass-rate shift -0.1309 on the logit
- engine, env 0.14, attempts anchored at w 0.5 minus w 0, offence CRPS: +0.0037 +/- 0.0070 (SE clustered by game)
- engine, env 0.14, attempts anchored at w 1 minus w 0, offence CRPS: -0.0009 +/- 0.0104 (SE clustered by game)

### 2025, SEEN (the season the defect was found on; it cannot judge the fix): 256 games

| model | offence CRPS (7 roles) | PIT KS | pair RMSE | cross-team pair RMSE | DST CRPS | DST mean sim / real | K CRPS | K mean sim / real |
|---|---|---|---|---|---|---|---|---|
| legacy (production) | 3.9535 | 0.1772 | 0.2567 | 0.1516 | 3.1106 | 6.41 / 6.21 | 2.5937 | 8.50 / 8.73 |
| constrained (alternate) | 3.8181 | 0.0251 | 0.0857 | 0.1347 | 3.1110 | 6.41 / 6.21 | 2.5922 | 8.50 / 8.73 |
| engine, env 0.14 | 3.8388 | 0.0496 | 0.0480 | 0.0559 | 3.0996 | 5.86 / 6.21 | 2.5488 | 8.77 / 8.72 |
| engine, env 0.14, attempts anchored at w 1 | 3.8217 | 0.0401 | 0.0458 | 0.0550 | 3.0897 | 5.65 / 6.21 | 2.5606 | 8.71 / 8.72 |

CRPS by role (lower is better) and 80% interval coverage:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 4.490 (79%) | 4.793 (63%) | 3.043 (48%) | 4.780 (63%) | 3.670 (54%) | 3.201 (41%) | 3.697 (57%) |
| constrained (alternate) | 4.548 (77%) | 4.702 (82%) | 2.856 (78%) | 4.696 (82%) | 3.498 (87%) | 2.872 (85%) | 3.554 (81%) |
| engine, env 0.14 | 4.522 (80%) | 4.690 (82%) | 2.858 (76%) | 4.779 (80%) | 3.543 (81%) | 2.893 (83%) | 3.586 (80%) |
| engine, env 0.14, attempts anchored at w 1 | 4.513 (80%) | 4.722 (81%) | 2.870 (77%) | 4.706 (81%) | 3.499 (83%) | 2.877 (83%) | 3.566 (80%) |

Role-pair correlations of projection residuals, observed against each model:

| pair | observed | legacy (production) | constrained (alternate) | engine, env 0.14 | engine, env 0.14, attempts anchored at w 1 |
|---|---|---|---|---|---|
| QB1/WR1 | 0.387 | 0.558 | 0.390 | 0.405 | 0.397 |
| QB1/WR2 | 0.369 | 0.489 | 0.327 | 0.336 | 0.333 |
| QB1/WR3 | 0.214 | 0.403 | 0.247 | 0.265 | 0.253 |
| QB1/TE1 | 0.342 | 0.482 | 0.291 | 0.329 | 0.327 |
| QB1/RB1 | 0.079 | 0.311 | 0.072 | 0.132 | 0.114 |
| QB1/RB2 | 0.065 | 0.229 | 0.075 | 0.120 | 0.108 |
| WR1/WR2 | 0.039 | 0.542 | -0.008 | -0.034 | -0.036 |
| WR1/TE1 | -0.001 | 0.516 | -0.007 | -0.009 | -0.011 |
| WR1/RB1 | 0.045 | 0.293 | -0.017 | 0.010 | 0.008 |
| WR2/TE1 | 0.057 | 0.451 | -0.000 | -0.001 | -0.008 |
| RB1/RB2 | -0.069 | 0.186 | -0.039 | -0.052 | -0.066 |
| QB1/opp QB1 | 0.224 | 0.183 | 0.027 | 0.250 | 0.234 |
| QB1/opp WR1 | 0.094 | 0.161 | 0.020 | 0.151 | 0.143 |
| WR1/opp WR1 | 0.105 | 0.124 | 0.013 | 0.079 | 0.075 |
| RB1/opp RB1 | -0.147 | 0.190 | -0.003 | -0.058 | -0.064 |
| QB1/opp RB1 | 0.101 | 0.220 | 0.032 | 0.022 | 0.015 |
| team offense/opp offense | 0.261 | 0.325 | 0.087 | 0.256 | 0.241 |

Mean DraftKings points by role -- each model's simulated mean, Sleeper's projection, what happened:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 17.50 / 17.50 / 17.15 | 14.94 / 14.94 / 15.10 | 6.28 / 6.28 / 6.24 | 14.04 / 14.04 / 13.65 | 9.46 / 9.46 / 8.70 | 5.42 / 5.42 / 5.38 | 9.04 / 9.04 / 9.28 |
| constrained (alternate) | 18.27 / 17.50 / 17.15 | 14.85 / 14.94 / 15.10 | 6.53 / 6.28 / 6.24 | 14.39 / 14.04 / 13.65 | 9.59 / 9.46 / 8.70 | 5.42 / 5.42 / 5.38 | 9.14 / 9.04 / 9.28 |
| engine, env 0.14 | 18.35 / 17.50 / 17.15 | 15.25 / 14.94 / 15.10 | 6.76 / 6.28 / 6.24 | 15.06 / 14.04 / 13.65 | 9.96 / 9.46 / 8.70 | 5.69 / 5.42 / 5.38 | 9.67 / 9.04 / 9.28 |
| engine, env 0.14, attempts anchored at w 1 | 17.99 / 17.50 / 17.15 | 15.58 / 14.94 / 15.10 | 6.92 / 6.28 / 6.24 | 14.48 / 14.04 / 13.65 | 9.61 / 9.46 / 8.70 | 5.47 / 5.42 / 5.38 | 9.25 / 9.04 / 9.28 |
- CRPS anchored minus unanchored, offence (roled player-weeks): -0.0172 +/- 0.0096 (SE clustered by game; n 3576, 256 games)
- CRPS anchored minus unanchored, defenses: -0.0099 +/- 0.0145 (SE clustered by game; n 512, 256 games)
- CRPS anchored minus unanchored, kickers: +0.0118 +/- 0.0093 (SE clustered by game; n 499, 256 games)

### 2026 weeks 2-5, the test: 63 games

| model | offence CRPS (7 roles) | PIT KS | pair RMSE | cross-team pair RMSE | DST CRPS | DST mean sim / real | K CRPS | K mean sim / real |
|---|---|---|---|---|---|---|---|---|
| legacy (production) | 4.0138 | 0.1767 | 0.3358 | 0.2302 | 2.8970 | 6.78 / 5.66 | 2.6431 | 7.87 / 8.81 |
| constrained (alternate) | 3.8523 | 0.0229 | 0.1252 | 0.1098 | 2.9059 | 6.78 / 5.66 | 2.6379 | 7.87 / 8.81 |
| engine, env 0.14 | 3.8595 | 0.0293 | 0.1395 | 0.1436 | 2.8644 | 6.32 / 5.66 | 2.5678 | 8.56 / 8.81 |
| engine, env 0.14, attempts anchored at w 1 | 3.8535 | 0.0360 | 0.1359 | 0.1410 | 2.8344 | 6.10 / 5.66 | 2.5687 | 8.48 / 8.81 |

CRPS by role (lower is better) and 80% interval coverage:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 4.520 (81%) | 4.434 (64%) | 2.771 (43%) | 5.606 (58%) | 3.463 (58%) | 3.176 (40%) | 4.126 (54%) |
| constrained (alternate) | 4.446 (77%) | 4.341 (84%) | 2.560 (85%) | 5.432 (75%) | 3.373 (83%) | 2.922 (83%) | 3.893 (77%) |
| engine, env 0.14 | 4.529 (80%) | 4.351 (83%) | 2.561 (79%) | 5.413 (75%) | 3.359 (83%) | 2.895 (79%) | 3.909 (76%) |
| engine, env 0.14, attempts anchored at w 1 | 4.541 (82%) | 4.354 (85%) | 2.570 (80%) | 5.449 (72%) | 3.323 (85%) | 2.880 (81%) | 3.857 (78%) |

Role-pair correlations of projection residuals, observed against each model:

| pair | observed | legacy (production) | constrained (alternate) | engine, env 0.14 | engine, env 0.14, attempts anchored at w 1 |
|---|---|---|---|---|---|
| QB1/WR1 | 0.391 | 0.554 | 0.392 | 0.407 | 0.394 |
| QB1/WR2 | 0.052 | 0.504 | 0.328 | 0.338 | 0.330 |
| QB1/WR3 | 0.308 | 0.414 | 0.250 | 0.259 | 0.238 |
| QB1/TE1 | 0.411 | 0.489 | 0.307 | 0.349 | 0.336 |
| QB1/RB1 | 0.093 | 0.301 | 0.066 | 0.149 | 0.123 |
| QB1/RB2 | -0.125 | 0.247 | 0.081 | 0.135 | 0.124 |
| WR1/WR2 | -0.131 | 0.542 | 0.006 | -0.030 | -0.029 |
| WR1/TE1 | 0.068 | 0.524 | 0.003 | -0.019 | -0.020 |
| WR1/RB1 | 0.039 | 0.276 | -0.010 | 0.018 | 0.011 |
| WR2/TE1 | -0.085 | 0.469 | 0.022 | -0.003 | -0.003 |
| RB1/RB2 | -0.199 | 0.184 | -0.044 | -0.046 | -0.061 |
| QB1/opp QB1 | 0.143 | 0.178 | 0.030 | 0.243 | 0.218 |
| QB1/opp WR1 | -0.041 | 0.149 | 0.016 | 0.147 | 0.137 |
| WR1/opp WR1 | -0.124 | 0.109 | 0.012 | 0.062 | 0.072 |
| RB1/opp RB1 | -0.184 | 0.168 | 0.008 | -0.059 | -0.057 |
| QB1/opp RB1 | 0.034 | 0.223 | 0.038 | 0.007 | 0.010 |
| team offense/opp offense | 0.054 | 0.311 | 0.082 | 0.220 | 0.218 |

Mean DraftKings points by role -- each model's simulated mean, Sleeper's projection, what happened:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 16.98 / 16.98 / 18.11 | 14.89 / 14.89 / 14.73 | 6.15 / 6.15 / 5.36 | 13.63 / 13.63 / 15.31 | 9.24 / 9.24 / 8.94 | 5.25 / 5.25 / 5.45 | 9.53 / 9.53 / 9.75 |
| constrained (alternate) | 17.76 / 16.98 / 18.11 | 14.74 / 14.89 / 14.73 | 6.45 / 6.15 / 5.36 | 13.87 / 13.63 / 15.31 | 9.30 / 9.24 / 8.94 | 5.22 / 5.25 / 5.45 | 9.50 / 9.53 / 9.75 |
| engine, env 0.14 | 17.00 / 16.98 / 18.11 | 14.57 / 14.89 / 14.73 | 6.46 / 6.15 / 5.36 | 14.07 / 13.63 / 15.31 | 9.50 / 9.24 / 8.94 | 5.33 / 5.25 / 5.45 | 9.86 / 9.53 / 9.75 |
| engine, env 0.14, attempts anchored at w 1 | 16.76 / 16.98 / 18.11 | 14.86 / 14.89 / 14.73 | 6.53 / 6.15 / 5.36 | 13.56 / 13.63 / 15.31 | 9.11 / 9.24 / 8.94 | 5.09 / 5.25 / 5.45 | 9.46 / 9.53 / 9.75 |
- CRPS anchored minus unanchored, offence (roled player-weeks): -0.0060 +/- 0.0150 (SE clustered by game; n 881, 63 games)
- CRPS anchored minus unanchored, defenses: -0.0300 +/- 0.0258 (SE clustered by game; n 126, 63 games)
- CRPS anchored minus unanchored, kickers: +0.0009 +/- 0.0215 (SE clustered by game; n 126, 63 games)

### The 2026 rule

- a_crps: PASS -- anchored 3.8535 vs shipped 3.8595
- b_pairs: PASS -- anchored 0.1359 vs shipped 0.1395 (+0.01 allowed)
- c_dst: PASS -- anchored 2.8344 vs shipped 2.8644 (x1.01)
- c_k: PASS -- anchored 2.5687 vs shipped 2.5678 (x1.01)
- **verdict: SHIPPED -- the boards anchor passing volume at w 1**

How to read it. The paired offence differences, anchored minus unanchored: 2024 (chosen there) -0.0009 +/- 0.0104; 2025 (seen) -0.0172 +/- 0.0096; 2026 (the test) -0.0060 +/- 0.0150. On 63 games each observed role-pair correlation carries bootstrap noise of 0.06 to 0.22, so every model's 2026 pair RMSE mostly measures that noise; 2024's and 2025's 256 games each are the better read of structure. Volume on the test season: teams threw 33.56 attempts a game against Sleeper's 31.11; the anchor moved the engine from 32.86 to 31.21, AWAY from what happened -- the gain above came despite that, and a season where Sleeper under-projects passing is where the anchor costs.

## The DFS audit that preceded this (live week 6, 2026-10-16)

Every NFL DFS path was run end to end against DraftKings' and Sleeper's live week-6 feeds: the showdown and classic tournament builds (CHI @ ATL dg 154949, the 10-game main slate dg 154941, small n), the app's lineup builder through the test client on auto-loaded slates, the tournament routes serving both boards, name matching in both directions, and the pool's worlds against the observed 2025 correlations. Confirmed bugs, each measured on the shipped code, fixed and guarded:

| # | bug | measured | fix |
|---|---|---|---|
| 1 | The PC's pre-lock refreshes fired four hours early (five after Nov 1) | DraftKings' lobby `StartDateEst` is Eastern with no zone and `iso_ts` read it as UTC: SNF dg 154959 read 20:20 UTC against its contest's 00:20Z, so the "last look ~25 minutes before kickoff" ran at T-4h25m, before the inactives | `iso_ts` honours Z and offsets and reads a zoneless DraftKings string as Eastern |
| 2 | The PC's slate windows compared that Eastern string with its own Mountain clock | a showdown stayed buildable 2h30 after kickoff, the main slate 6h after | epoch on both sides |
| 3 | An auto-loaded NFL slate in the app's builder was built on WEEK 1 projections | dg 154949 and 154941: "Week 1 regular season (from the toggle - this CSV carries no game dates)"; the live CSV carries no dates and the week picker defaulted to Week 1 | the week comes from the slate's kickoff (`slate_for` now carries it), the picker defaults to auto, the route passes "the slate's own week" |
| 4 | The look-back would have graded such a lineup against week-1 box scores | `event_of` keyed football builds by the request's week | keyed by the week the build ran on |
| 5 | The classic board's lock-relative roster allowance never applied, nor the sheet's | `slate.get("starts")` was always None; the route hands `contest` over as the string "gpp" | the slate's kickoff, else the export's Game Info, else a contest dict |
| 6 | Game labels read backwards for half the week | "CLE @ BAL", "LV @ BUF", "NYG @ NO", "PHI @ CAR", "TB @ PIT" against BAL @ CLE, BUF @ LV, NO @ NYG, CAR @ PHI, PIT @ TB | oriented from the ESPN schedule (home, state, kickoff, neutral site); unknown reads "A vs B" |
| 7 | The classic board simulated every game of the week | 14 games at 60,000 worlds for a 10-game slate, Thursday's finished game included | the slate's own games only |

Checked and clean: pool invariants under both models (lengths, NaN, floors, means against Sleeper), every lineup on both boards (cap, roster shape, both teams, captain price, the construction rules, no field-only player in our entries, win <= win-any, top 1% <= cash), the boards' strict-JSON serving with no numpy leaves, and name matching both ways (every unmatched DraftKings player above minimum salary is one Sleeper itself does not project this week: Hurts Out, Mayfield and Hall Doubtful, Etienne IR, ...).

