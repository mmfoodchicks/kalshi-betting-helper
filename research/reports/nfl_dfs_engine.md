# The game-engine DFS worlds: the deep engine plays every world

Built 2026-10-16 14:39 UTC by research/nfl_dfs_engine_eval.py. Development season 2024 (256 games, weeks 2-18, tables fit on [2023]); blind test 2025 (256 games, tables fit on [2023, 2024]), 400 worlds per game per model, base seed 20261016. 2025 provenance: commit ecc4475, dirty False; engine sources c414706c6b3f8b1b (dirty False).

## What the engine model is

Every DFS world is one football game played snap by snap by the deep engine (nfl_deep_sim): possessions, the clock, fourth downs, the script, both offences against both defences. Each team's scoring level is the Sleeper drive model's expected points (nfl_game_sim, on this week's projections) and the deep engine's own, blended at the slate's deep weight, 0.30; the deep engine's offences are re-levelled until it plays at that target. The world's box score is dealt to the players by Sleeper's projected usage with the constrained model's fitted competition noise: every completion a catch by someone, receiving yards the quarterback's to the yard, every touchdown a catch or a carry. The defense is scored off what the OTHER offence did in the same game (points it scored with the ball, sacks, takeaways) plus its own return touchdowns, safeties and blocks; the kicker off his own field goals by distance and his tries. Player means are what the game deals, not pinned to Sleeper's number; Sleeper's projection stays the `proj` the field reads.

One term was added to the engine for the DFS worlds: the game's shared passing environment (one draw per game moving both offences' yards per completion and completion rate). Across the two teams the engine already matched real games (2023-2025, 816 games) on points (-0.055 real, -0.045 engine), plays (-0.53, -0.53), rushes (-0.56, -0.59), drives (+0.85, +0.85) and attempts against the opponent's points (+0.29, +0.32), and missed exactly one thing: the two teams' passing yards (+0.18 real, +0.03 engine). The term is off for every other caller (the slate's deep layer, the deep season).

## The rules, written before 2025 was run

ENV_SD: the 2024 grid value (0, 0.05, 0.08, 0.11, 0.14) with the smallest cross-team pair RMSE among those whose offensive CRPS is within 0.5% of env 0's. Chosen: **0.14** (frozen in nfl_dfs_engine.ENV_SD and committed before the 2025 run; the run refuses any other value or a dirty tree).

Promotion to the boards' model on 2025: (a) offence CRPS <= 1.01 x the constrained model's; (b) pair-correlation RMSE below both legacy's and constrained's; (c) pooled PIT KS <= max(0.05, constrained's + 0.01); (d) defense and kicker CRPS each <= 1.02 x legacy's. Any link failing keeps the boards on legacy.

## 2025, blind

| model | offence CRPS (7 roles) | PIT KS | pair RMSE | cross-team pair RMSE | DST CRPS | DST mean sim / real | K CRPS | K mean sim / real |
|---|---|---|---|---|---|---|---|---|
| legacy (production) | 3.9541 | 0.1782 | 0.2560 | 0.1493 | 3.1125 | 6.41 / 6.21 | 2.5934 | 8.50 / 8.73 |
| constrained (alternate) | 3.8181 | 0.0251 | 0.0857 | 0.1347 | 3.1110 | 6.41 / 6.21 | 2.5922 | 8.50 / 8.73 |
| engine, env 0.14 | 3.8335 | 0.0478 | 0.0475 | 0.0562 | 3.0889 | 5.97 / 6.21 | 2.5630 | 8.71 / 8.72 |

CRPS by role (lower is better) and 80% interval coverage:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 4.494 (79%) | 4.795 (63%) | 3.048 (49%) | 4.783 (63%) | 3.661 (54%) | 3.207 (41%) | 3.691 (58%) |
| constrained (alternate) | 4.548 (77%) | 4.702 (82%) | 2.856 (78%) | 4.696 (82%) | 3.498 (87%) | 2.872 (85%) | 3.554 (81%) |
| engine, env 0.14 | 4.503 (80%) | 4.700 (81%) | 2.863 (76%) | 4.757 (80%) | 3.532 (83%) | 2.889 (83%) | 3.590 (80%) |

Role-pair correlations of projection residuals, observed against each model:

| pair | observed | legacy (production) | constrained (alternate) | engine, env 0.14 |
|---|---|---|---|---|
| QB1/WR1 | 0.387 | 0.554 | 0.390 | 0.403 |
| QB1/WR2 | 0.369 | 0.486 | 0.327 | 0.338 |
| QB1/WR3 | 0.214 | 0.401 | 0.247 | 0.265 |
| QB1/TE1 | 0.342 | 0.480 | 0.291 | 0.329 |
| QB1/RB1 | 0.079 | 0.308 | 0.072 | 0.130 |
| QB1/RB2 | 0.065 | 0.228 | 0.075 | 0.113 |
| WR1/WR2 | 0.039 | 0.542 | -0.008 | -0.033 |
| WR1/TE1 | -0.001 | 0.520 | -0.007 | -0.009 |
| WR1/RB1 | 0.045 | 0.289 | -0.017 | 0.006 |
| WR2/TE1 | 0.057 | 0.453 | -0.000 | -0.002 |
| RB1/RB2 | -0.069 | 0.187 | -0.039 | -0.054 |
| QB1/opp QB1 | 0.224 | 0.183 | 0.027 | 0.254 |
| QB1/opp WR1 | 0.094 | 0.159 | 0.020 | 0.152 |
| WR1/opp WR1 | 0.105 | 0.123 | 0.013 | 0.080 |
| RB1/opp RB1 | -0.147 | 0.184 | -0.003 | -0.063 |
| QB1/opp RB1 | 0.101 | 0.219 | 0.032 | 0.017 |
| team offense/opp offense | 0.261 | 0.325 | 0.087 | 0.254 |

Mean DraftKings points by role -- each model's simulated mean, Sleeper's projection, what happened:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 17.50 / 17.50 / 17.15 | 14.94 / 14.94 / 15.10 | 6.28 / 6.28 / 6.24 | 14.04 / 14.04 / 13.65 | 9.46 / 9.46 / 8.70 | 5.42 / 5.42 / 5.38 | 9.04 / 9.04 / 9.28 |
| constrained (alternate) | 18.27 / 17.50 / 17.15 | 14.85 / 14.94 / 15.10 | 6.53 / 6.28 / 6.24 | 14.39 / 14.04 / 13.65 | 9.59 / 9.46 / 8.70 | 5.42 / 5.42 / 5.38 | 9.14 / 9.04 / 9.28 |
| engine, env 0.14 | 18.29 / 17.50 / 17.15 | 15.17 / 14.94 / 15.10 | 6.73 / 6.28 / 6.24 | 14.95 / 14.04 / 13.65 | 9.90 / 9.46 / 8.70 | 5.66 / 5.42 / 5.38 | 9.62 / 9.04 / 9.28 |

### The gate

- a_crps: PASS -- engine 3.8335 vs constrained 3.8181 (limit x1.01)
- b_pairs: PASS -- engine 0.0475 vs constrained 0.0857, legacy 0.2560
- c_pit: PASS -- engine 0.0478 vs limit 0.0500
- d_dst: PASS -- engine 3.0889 vs legacy 3.1125 (limit x1.02)
- d_k: PASS -- engine 2.5630 vs legacy 2.5934 (limit x1.02)
- **verdict: PROMOTED -- the boards are built on the engine model**

## 2024, development (where ENV_SD was chosen)

| model | offence CRPS (7 roles) | PIT KS | pair RMSE | cross-team pair RMSE | DST CRPS | DST mean sim / real | K CRPS | K mean sim / real |
|---|---|---|---|---|---|---|---|---|
| legacy (production) | 4.0758 | 0.1612 | 0.2724 | 0.1408 | 2.9755 | 6.57 / 6.17 | 2.6056 | 7.99 / 8.54 |
| constrained (alternate) | 3.9018 | 0.0413 | 0.0686 | 0.0996 | 2.9654 | 6.57 / 6.17 | 2.6047 | 7.99 / 8.54 |
| engine, env 0 | 3.9311 | 0.0296 | 0.0843 | 0.1192 | 2.9015 | 6.52 / 6.17 | 2.5417 | 8.87 / 8.52 |
| engine, env 0.05 | 3.9311 | 0.0286 | 0.0749 | 0.0991 | 2.9095 | 6.52 / 6.17 | 2.5365 | 8.87 / 8.52 |
| engine, env 0.08 | 3.9241 | 0.0299 | 0.0611 | 0.0697 | 2.9024 | 6.53 / 6.17 | 2.5433 | 8.87 / 8.52 |
| engine, env 0.11 | 3.9313 | 0.0274 | 0.0495 | 0.0343 | 2.8971 | 6.53 / 6.17 | 2.5379 | 8.85 / 8.52 |
| engine, env 0.14 | 3.9328 | 0.0238 | 0.0536 | 0.0332 | 2.8958 | 6.54 / 6.17 | 2.5329 | 8.86 / 8.52 |

CRPS by role (lower is better) and 80% interval coverage:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 4.578 (78%) | 4.782 (58%) | 2.843 (46%) | 4.918 (62%) | 4.621 (52%) | 3.141 (44%) | 3.648 (51%) |
| constrained (alternate) | 4.530 (77%) | 4.683 (81%) | 2.641 (76%) | 4.819 (82%) | 4.362 (79%) | 2.827 (85%) | 3.451 (84%) |
| engine, env 0 | 4.579 (75%) | 4.690 (81%) | 2.679 (76%) | 4.858 (79%) | 4.400 (74%) | 2.872 (83%) | 3.439 (79%) |
| engine, env 0.05 | 4.592 (76%) | 4.688 (79%) | 2.673 (76%) | 4.840 (78%) | 4.417 (75%) | 2.864 (84%) | 3.445 (80%) |
| engine, env 0.08 | 4.565 (78%) | 4.662 (82%) | 2.671 (76%) | 4.858 (78%) | 4.404 (75%) | 2.867 (82%) | 3.442 (81%) |
| engine, env 0.11 | 4.580 (78%) | 4.688 (81%) | 2.679 (76%) | 4.856 (79%) | 4.411 (76%) | 2.860 (83%) | 3.444 (80%) |
| engine, env 0.14 | 4.583 (81%) | 4.695 (80%) | 2.685 (75%) | 4.851 (79%) | 4.410 (76%) | 2.863 (84%) | 3.443 (82%) |

Role-pair correlations of projection residuals, observed against each model:

| pair | observed | legacy (production) | constrained (alternate) | engine, env 0 | engine, env 0.05 | engine, env 0.08 | engine, env 0.11 | engine, env 0.14 |
|---|---|---|---|---|---|---|---|---|
| QB1/WR1 | 0.364 | 0.563 | 0.386 | 0.346 | 0.354 | 0.370 | 0.388 | 0.409 |
| QB1/WR2 | 0.391 | 0.499 | 0.326 | 0.297 | 0.301 | 0.314 | 0.329 | 0.345 |
| QB1/WR3 | 0.213 | 0.422 | 0.262 | 0.232 | 0.240 | 0.250 | 0.260 | 0.279 |
| QB1/TE1 | 0.240 | 0.470 | 0.288 | 0.280 | 0.287 | 0.296 | 0.308 | 0.331 |
| QB1/RB1 | 0.024 | 0.308 | 0.054 | 0.087 | 0.096 | 0.105 | 0.113 | 0.124 |
| QB1/RB2 | 0.089 | 0.243 | 0.078 | 0.100 | 0.105 | 0.112 | 0.118 | 0.130 |
| WR1/WR2 | 0.051 | 0.559 | -0.004 | -0.061 | -0.054 | -0.043 | -0.029 | -0.013 |
| WR1/TE1 | -0.058 | 0.511 | -0.002 | -0.040 | -0.036 | -0.023 | -0.014 | 0.002 |
| WR1/RB1 | -0.042 | 0.296 | -0.007 | -0.001 | 0.004 | 0.006 | 0.014 | 0.024 |
| WR2/TE1 | 0.059 | 0.442 | 0.006 | -0.017 | -0.016 | -0.007 | 0.004 | 0.017 |
| RB1/RB2 | -0.027 | 0.192 | -0.033 | -0.048 | -0.053 | -0.047 | -0.046 | -0.041 |
| QB1/opp QB1 | 0.176 | 0.174 | 0.029 | 0.035 | 0.062 | 0.113 | 0.173 | 0.242 |
| QB1/opp WR1 | 0.144 | 0.153 | 0.021 | 0.034 | 0.052 | 0.074 | 0.109 | 0.147 |
| WR1/opp WR1 | 0.050 | 0.118 | 0.013 | 0.010 | 0.023 | 0.033 | 0.052 | 0.072 |
| RB1/opp RB1 | -0.083 | 0.184 | -0.000 | -0.081 | -0.079 | -0.077 | -0.073 | -0.068 |
| QB1/opp RB1 | 0.041 | 0.220 | 0.032 | -0.052 | -0.042 | -0.031 | -0.013 | 0.007 |
| team offense/opp offense | 0.204 | 0.308 | 0.084 | -0.003 | 0.032 | 0.083 | 0.151 | 0.222 |

Mean DraftKings points by role -- each model's simulated mean, Sleeper's projection, what happened:

| model | QB1 | RB1 | RB2 | WR1 | WR2 | WR3 | TE1 |
|---|---|---|---|---|---|---|---|
| legacy (production) | 16.59 / 16.59 / 17.70 | 13.70 / 13.70 / 15.10 | 5.52 / 5.52 / 5.79 | 13.64 / 13.64 / 14.69 | 9.59 / 9.59 / 10.82 | 5.88 / 5.88 / 5.47 | 8.11 / 8.11 / 8.87 |
| constrained (alternate) | 17.18 / 16.59 / 17.70 | 13.66 / 13.70 / 15.10 | 5.82 / 5.52 / 5.79 | 14.10 / 13.64 / 14.69 | 9.81 / 9.59 / 10.82 | 5.94 / 5.88 / 5.47 | 8.27 / 8.11 / 8.87 |
| engine, env 0 | 17.07 / 16.59 / 17.70 | 14.45 / 13.70 / 15.10 | 6.19 / 5.52 / 5.79 | 14.53 / 13.64 / 14.69 | 10.07 / 9.59 / 10.82 | 6.11 / 5.88 / 5.47 | 8.59 / 8.11 / 8.87 |
| engine, env 0.05 | 17.13 / 16.59 / 17.70 | 14.47 / 13.70 / 15.10 | 6.18 / 5.52 / 5.79 | 14.56 / 13.64 / 14.69 | 10.08 / 9.59 / 10.82 | 6.14 / 5.88 / 5.47 | 8.61 / 8.11 / 8.87 |
| engine, env 0.08 | 17.18 / 16.59 / 17.70 | 14.45 / 13.70 / 15.10 | 6.21 / 5.52 / 5.79 | 14.57 / 13.64 / 14.69 | 10.11 / 9.59 / 10.82 | 6.15 / 5.88 / 5.47 | 8.62 / 8.11 / 8.87 |
| engine, env 0.11 | 17.25 / 16.59 / 17.70 | 14.42 / 13.70 / 15.10 | 6.20 / 5.52 / 5.79 | 14.62 / 13.64 / 14.69 | 10.11 / 9.59 / 10.82 | 6.15 / 5.88 / 5.47 | 8.64 / 8.11 / 8.87 |
| engine, env 0.14 | 17.33 / 16.59 / 17.70 | 14.41 / 13.70 / 15.10 | 6.19 / 5.52 / 5.79 | 14.64 / 13.64 / 14.69 | 10.14 / 9.59 / 10.82 | 6.16 / 5.88 / 5.47 | 8.66 / 8.11 / 8.87 |

## Limits, stated

- One held-out season is a measurement, not a proof. The 2025 outcomes had been opened once before (Stage 2E, for the legacy-vs-constrained test); this is the engine's first contact with them, and nothing was tuned on them.
- The deep engine's two global constants (_FORM_SD, _HFA_YDS) were calibrated on 2023-2025 margins and home win rates: game-level, and 2025 is inside that fit.
- The replay anchors the deep layer to the points-differential ratings (Kalshi's win totals have no history) and leaves out the weekly roster factor; production uses both.
- The allocation noise (share_sd, ypr_sd, rshare_sd, ypc_sd) is the constrained model's 2022-2024 fit, taken as is; the engine replaces that model's team-level draws with a played game.
- Two-point conversions and offensive return touchdowns are not dealt to players (no model here deals them).
- The showdown money columns stay experimental: the field model is uncalibrated whatever simulator made the worlds.
- The engine's passing level ran above both Sleeper's and reality on 2025: WR1 14.95 simulated against 13.65 real (14.04 Sleeper), WR2 9.90 against 8.70, QB1 18.29 against 17.15. The lean shows in its PIT: 13% of real outcomes fell in the bottom tenth of its distribution, against 10% for a calibrated model, and the PIT link passed by 0.002. On 2024 the same level sat close to what happened (WR1 14.64 against 14.69). Anchoring each team's passing volume to Sleeper's projected attempts is the obvious next lever. It was not tried here: it would have to be chosen on 2024 and could only be judged on 2026.
- The environment term buys the yardage coupling at a measured cost in points independence: inside the engine's worlds the two teams' points in one game correlate +0.124 (2024) and +0.128 (2025) at the frozen value, against -0.033 with it off (median over the 256 games; measured from the runs' raw per-game records, which are not committed at 86 MB). Real games pooled across matchups show -0.055, a figure that mixes in the spread's negative pull, so the real within-game value is near zero and the term overshoots it. The defenses' CRPS improved anyway.
- The engine over-couples a quarterback with his own backs (QB1/RB1 0.130 against 0.079 real): the played game ties a team's rushing and passing to the same scoring.
- The lineup SHEET in the app stays on legacy-discrete: the server has no numpy and must not play thousands of snap-by-snap games inside a one-core web worker. The tournament boards, built on the PC, are where the engine runs.

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

