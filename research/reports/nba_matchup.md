# The NBA matchup engine: player props against what a defense concedes

research/nba_matchup_fit.py (the fit and the held-out test), nba_matchup_data.py (the rows), nba_matchup_profiles.py
(the as-of profiles), nba_matchup.py (production inference). Seasons are named by their start year (2024 = 2024-25).

## The rule, written before any validation or test row was scored

<!-- PREREG BEGIN -->
PRE-REGISTRATION (nba_matchup, written before the 2024-25 validation or the 2025-26 test was scored).

Data. ESPN game summaries (box score + play-by-play), regular-season games between two franchises
(nba_pbp.league_game), 2021-22 .. 2025-26, cut by nba_matchup_data.cut_game and kept only when the feed's
points reproduce the box for every player and each side's on-floor seconds are five players' worth of the
clock (entry_ok). 2021-22 is history only (the prior season of 2022-23's as-of profiles).

Splits. Fit 2022-23 + 2023-24; validate 2024-25 (chooses); test 2025-26 (read once, by `test`, with the
selection frozen from validation). Every profile is as of the game's Mountain-time date, built from
strictly earlier dates (nba_matchup_profiles.Book; guarded).

Rows scored. A player-game is scored when the player ENTERED the game (Kalshi settles a player who never
takes the court at a fair price, so the market conditions on the same thing), has 5+ games in the current
and previous season before it, and the rung-1 minutes model projects 20+ minutes (the props population).
dk_fp is scored on the DFS population: 5+ prior games, rung-1 projection 10+ minutes, a season-to-date DK
average available.

Markets and thresholds (the event is stat >= k). pts 10..45 by 5; reb 2..16 by 2; ast 2..14 by 2; fg3m
1..6; pra 15..55 by 5; pr and pa 10..45 by 5; ra 4..20 by 2; dk_fp 10..60 by 10.

Model. Per market (pts, reb, ast, fg3m) a Poisson-family regression with offset ln(as-of per-minute rate x
actual minutes), ridge-penalized, its penalty chosen by two-fold season cross-validation INSIDE the fit
seasons; the coefficients are fitted on the fit seasons only. Rungs (nba_matchup.RUNGS): 1 baseline (pace
ratio, home); 2 overall defense (observed-over-expected for the stat; for points also points allowed per
possession); 3 position allowance (OE against his position group relative to overall); 4 style x
concession interactions (nba_matchup.R4); 5 comparable-opponent history (nba_matchup.comparables, its
bandwidth 0.75, recency half-life 365 days and shrinkage 5 set by rule, not tuned); 6 context (teammates
ruled out: their usage / assist / rebound share; the defense's recent form). M6: the minutes model with
teammates' missing minutes and the expected-margin (blowout) terms, one shared model. Distributions:
negative binomial given minutes, variance phi*mu + kappa*mu^2, (phi, kappa) per market fitted at rung 1
by the ladder log loss on a seeded 8,000-row subsample of the fit population, mixed over the minutes model's twenty equal-mass nodes of actual/projected minutes
(fit seasons). Combos sum the components' adjusted rates with their own fitted dispersion. dk_fp sums
the components' DraftKings-weighted rates (steals, blocks, turnovers at rung 1), adds the double- and
triple-double bonus from each component's P(>= 10) at each minutes node, and uses its own negative
binomial on floor(DK points).

Primary metric: the ladder log loss -- per row the mean over the market's thresholds of the binary log
loss of P(stat >= k) (probabilities clipped to [1e-4, 1 - 1e-4]), averaged over rows. Paired differences
carry a standard error clustered by game. Also reported: Brier, the exact-outcome log score, MAE / RMSE /
bias of the mean, reliability by probability decile (pooled over thresholds), sharpness, and breakdowns
by position (G / F / C) and by primary role.

Validation decision (2024-25), per market, forward in ladder order: start from rung 1; rung k is ADDED
when it lowers the validation ladder log loss of the accepted model by at least one clustered standard
error (gain > 0 and z >= 1). Then M6 is adopted when, with every market's selected rungs, the sum of the
four markets' per-row ladder losses falls by z >= 1. Combos and dk_fp take their components' selections.
The cumulative ladder (rungs 1..k regardless of acceptance, then 6+M6) is reported beside it.

Test decision (2025-26), touched once with the frozen selection: a market ships its selected rungs only
if its test ladder log loss is below rung 1's (point estimate; z reported); otherwise every matchup
coefficient of that market ships at weight 0 (rung 1 only). M6 ships only if the four markets' summed
test loss with M6 is below without it. dk_fp replaces DraftKings' AvgPointsPerGame (Normal, sd 0.55 x
mean -- what the DFS builder uses today) in the builder only if, on the test season's DFS population,
the shipped dk_fp beats that arm on BOTH MAE and the 10..60 ladder log loss.

Market comparison (2025-26 only: Kalshi's player props launched 2025-11-14). Descriptive, decides
nothing: a seeded random sample of settled KXNBAPTS / KXNBAREB / KXNBAAST / KXNBA3PT player-games
(research/nba_matchup_kalshi.py, seed 20261023), each market's PRE-TIP price = the last hourly candle
ending at or before ESPN's tip time (never an in-game candle), kept when that candle has a two-sided
book (yes bid > 0, yes ask < 100c; the subset with spread <= 10c reported apart); the SHIPPED model's
probability (after the test decision) against the mid's log loss and Brier; a hypothetical one-contract
bet on the side whose all-in edge -- model probability minus (ask + Kalshi's quadratic fee) / 100, NO's
ask being 100 - yes bid, fees by prop_value -- is 3 points or more, reported with its ROI, count and a
game-clustered bootstrap interval. No parameter is chosen on it.
<!-- PREREG END -->

PREREG_SHA256: b6f647c9235d923306ad58cf3e603b55198ac3ff02eee51fff8a3ed30a4d0d13
PREREG_UTC: 2026-10-23T01:48:48Z (frozen before `validate` first ran; `validate` and `test` refuse to run when the block's hash differs)

Clarification of the rule's wording (added after both reviews, 2026-10-23; the rule text above is unchanged and
still hashes to the recorded value): its "DraftKings' AvgPointsPerGame" arm is, in the code that scored it
(research/nba_matchup_fit.py add_dk), a RECONSTRUCTION from the box score -- each player's season-to-date DraftKings
points per game played as of the game (last season's when he has none this season) -- not DraftKings' own figure,
which was never read for 2024-25 or 2025-26. Every table below labels it so.

## Run log (UTC)

- 2026-10-23 00:55-01:05 -- raw summaries fetched (research/nba_matchup_fetch.py; 2021-22 .. 2025-26 regular seasons, 6,153 summaries plus
  14 of 2026-27, ~95 s a season at six threads) and cut (research/nba_matchup_build.py).
- 01:16 -- stage 1 (pass 1, reliability, baseline weights, minutes) and 01:47 stage 2 (the features; rows rebuilt once with the
  helpers moved into nba_matchup.py -- 0 of 103,329 rows differed).
- 01:46 -- `fitcheck`: the whole ladder IN-SAMPLE on the fit seasons, a pipeline check only (no 2024-25 or 2025-26 row).
  It showed the exact-likelihood dispersion running wide on the thresholds, so before the rule was frozen the dispersion
  fit was switched to the primary metric (ladder log loss).
- 01:48:48 -- the rule above frozen (sha256 recorded; `validate` / `test` refuse to run on any other text).
- 01:50 and 02:00 -- two `validate` starts died inside the DISPERSION fit on fit-season data (no threshold set for steals /
  blocks / turnovers; then a tuple-unpack error) -- before any 2024-25 row was scored. Fixed, restarted.
- 02:11 -- `validate` finished (selection frozen in work/selection.json).
- 04:14 -- `test` finished, run once (it refuses a second run); 04:16 `freeze` wrote research/data/nba_matchup/.
- After the decisions: `shipped_report` (the shipped combination on both seasons), `dump_preds` + the Kalshi comparison,
  `research/nba_matchup_parity.py` (production against research). None of these chose anything.
- "Run once": `test()` refuses when work/test.json already exists -- a file-existence check on that work directory,
  not a recorded marker (a second work directory could run it again). It ran once here (one test.json, 04:14:19);
  both reviews confirm it.
- Review round (both reviews AGREE AFTER FIXES: research/reviews/nba_matchup.md, nba_matchup_orchestrator.md): code
  fixes to strong_fit, the past-date board, the factor matrix, provenance and evidence kinds, and 20 new guards (65 in
  all); no shipped coefficient, selection or test number changed, and validate / test were not re-run. Details in
  "Review round" at the end.

## What it is

A player-prop engine that answers the owner's question in measured pieces. Every player-game of 2022-23 .. 2025-26 is
rebuilt from ESPN's game summaries (box score + play-by-play, nba_matchup_data): his minutes, counting stats, the
on-court stints (who was on the floor for every second), his team's and the opponent's possessions and rebound chances
while he played, and his shot profile from the league's own shot descriptors. Each player and each defense gets an
AS-OF profile (nba_matchup_profiles.Book: strictly earlier dates). Per market a Poisson-family regression on the fit
seasons multiplies his shrunk per-minute rate by the matchup terms; the minutes model and a negative binomial turn it
into P(stat >= k). Each rung was kept only where it improved held-out log loss.

## Data, and what it can and cannot see

| season | games kept | player-games | stint minutes vs box (mean abs) | share off by > 1.5 min | rebounds feed = box | assists feed = box |
|---|---|---|---|---|---|---|
| 2021-22 (history only) | 1,220 | 25,740 | 0.250 | 0.02% | 99.1% | 99.9% |
| 2022-23 | 1,212 | 25,444 | 0.255 | 0.12% | 99.5% | 99.7% |
| 2023-24 | 1,218 | 26,056 | 0.250 | 0.03% | 99.3% | 99.8% |
| 2024-25 | 1,222 | 26,059 | 0.253 | 0.06% | 99.3% | 99.7% |
| 2025-26 | 1,213 | 26,195 | 0.250 | 0.01% | 99.3% | 99.7% |

A game is dropped when the feed's points do not reproduce the box for every player (an attribution slip) or a side's
on-floor seconds are not five players' worth of the clock -- 10 to 18 a season. Box minutes are whole minutes, so 0.25
is what rounding alone produces: the stint inference (box starters; each later period's five read from each player's
first event) agrees with the box to within its rounding. The 2021-22 feed has no `pointsAttempted`; its shot values are rebuilt from scoreValue / distance
(1,051 of 1,230 games failed the points check before, 3 after).

OBSERVED (the feed's own words / counts): shot zone by distance, made / missed, assisted (the assist flag), the type
text's pull-up / step-back / fade / turnaround / driving / cutting / alley-oop / putback / tip / hook words, rebounds,
turnovers and steals, fouls drawn, substitutions. PROXY (labelled `proxy` everywhere): catch-and-shoot = a plain
"Jump Shot" with no dribble or move word; post-up = hooks and turnarounds inside 14 ft; transition = a possession's
first shot within 7 s of a defensive rebound or steal. NOT OBSERVABLE and never labelled: defensive schemes (drop /
switch / hedge / blitz / zone), tracking (touches, drives as the league counts them, potential assists, contested
rebounds), synergy play types. The defense profile is what the defense CONCEDES -- shot mix, make rates, assisted share,
pull-up and spot-up attempts, free throws, offensive rebounds, early-clock shots, pace: the observable footprint of a
scheme. nba_matchup_profiles.external_profiles is the documented, unbuilt hook for a PC-side play-type fetch; NBA.com's
terms bar using its statistics in connection with gambling (research/reports/matchup_data_sources.md), so it should
stay unbuilt.

## The measured shrinkage (fit seasons only)

Defense / offense channels (each team-season's games split odd / even; K = mean half denominator x (1 - r) / r):

| channel | kind | split-half r (half season, 60 team-seasons) | K (denominator units) | year-over-year slope rho |
|---|---|---|---|---|
| def:pts | observed | 0.622 | 2,507 | 0.562 |
| def:pace | observed | 0.891 | 498 | 0.595 |
| def:rim | observed | 0.863 | 653 | 0.727 |
| def:short | observed | 0.867 | 634 | 0.755 |
| def:mid | observed | 0.749 | 1,382 | 0.557 |
| def:three | observed | 0.78 | 1,160 | 0.394 |
| def:rim_pct | observed | 0.562 | 735 | 0.533 |
| def:short_pct | observed | 0.468 | 1,029 | 0.632 |
| def:mid_pct | observed | 0.236 | 1,094 | 0.0 |
| def:three_pct | observed | 0.266 | 3,874 | 0.196 |
| def:ast_share | observed | 0.812 | 395 | 0.43 |
| def:ast3 | observed | 0.704 | 1,738 | 0.299 |
| def:unast3 | observed | 0.347 | 7,749 | 0.58 |
| def:pull | observed | 0.78 | 1,161 | 0.512 |
| def:spot3 | proxy | 0.827 | 862 | 0.44 |
| def:drive | observed | 0.566 | 3,160 | 0.251 |
| def:cut | observed | 0.634 | 2,382 | 0.438 |
| def:putback | observed | 0.372 | 6,956 | 0.281 |
| def:post | proxy | 0.561 | 3,223 | 0.443 |
| def:fta | observed | 0.754 | 1,346 | 0.54 |
| def:tov | observed | 0.83 | 847 | 0.556 |
| def:oreb | observed | 0.6 | 1,326 | 0.405 |
| def:early | proxy | 0.767 | 525 | 0.667 |
| def:second | observed | 0.46 | 4,833 | 0.335 |
| off:miss | observed | 0.752 | 1,364 | 0.323 |
| off:oreb | observed | 0.827 | 415 | 0.329 |
| off:three | observed | 0.928 | 319 | 0.599 |
| off:pace | observed | 0.897 | 467 | 0.615 |

Three-point percentage allowed (r 0.27), midrange percentage allowed (0.24) and putbacks allowed (0.37) are mostly
noise; the shot MIX a defense allows (rim 0.86, short 0.87, three 0.78, spot-up threes 0.83) and its pace (0.89) are
real and persistent.

Player role features (shrunk toward his position group's league value):

| feature | kind | split-half r (player-seasons with 400+ min) | K | units |
|---|---|---|---|---|
| usage | observed | 0.968 | 56.4 | 770 |
| rim_share | observed | 0.96 | 11.3 | 770 |
| short_share | observed | 0.906 | 28.1 | 770 |
| mid_share | observed | 0.918 | 24.2 | 770 |
| three_share | observed | 0.976 | 6.5 | 770 |
| ft_rate | observed | 0.801 | 67.3 | 770 |
| assisted_share | observed | 0.904 | 13.7 | 770 |
| pullup_share | observed | 0.966 | 9.4 | 770 |
| pull3_share | observed | 0.863 | 17.6 | 733 |
| spot3_share | proxy | 0.852 | 19.3 | 733 |
| assisted3_share | observed | 0.814 | 9.5 | 709 |
| drive_share | observed | 0.915 | 25.0 | 770 |
| cut_share | observed | 0.939 | 17.7 | 770 |
| putback_share | observed | 0.919 | 23.7 | 770 |
| post_share | proxy | 0.924 | 22.4 | 770 |
| second_chance_share | observed | 0.867 | 41.4 | 770 |
| transition_share | proxy | 0.706 | 112.6 | 770 |
| oreb_pct | observed | 0.928 | 57.8 | 770 |
| dreb_pct | observed | 0.93 | 56.4 | 770 |
| ast_pct | observed | 0.956 | 23.6 | 770 |
| ast3_share | observed | 0.446 | 98.4 | 770 |
| ast_rim_share | observed | 0.348 | 148.5 | 770 |
| tov_rate | observed | 0.634 | 196.5 | 770 |
| min_share | observed | 0.983 | 25.7 | 770 |

Observed-over-expected (what a defense's opponents produced over their own as-of rates, pace-neutral; persistence
used for all: 0.28):

| stat : opponents' position | split-half r | K (expected units) | yoy slope |
|---|---|---|---|
| pts:all | 0.642 | 2,585 | 0.584 |
| pts:G | 0.41 | 3,268 | 0.383 |
| pts:F | 0.543 | 1,481 | 0.18 |
| pts:C | 0.396 | 945 | 0.511 |
| reb:all | 0.595 | 1,203 | 0.239 |
| reb:G | 0.349 | 1,142 | 0.281 |
| reb:F | 0.261 | 2,128 | 0.261 |
| reb:C | 0.506 | 392 | 0.093 |
| ast:all | 0.683 | 482 | 0.366 |
| ast:G | 0.471 | 696 | 0.224 |
| ast:F | 0.354 | 587 | 0.32 |
| ast:C | 0.266 | 273 | 0.323 |
| tpm:all | 0.63 | 297 | 0.218 |
| tpm:G | 0.563 | 228 | 0.199 |
| tpm:F | 0.475 | 205 | 0.172 |
| tpm:C | 0.013 | 1,346 | 0.241 |

Position-specific allowances are half as reliable as the overall one, and threes allowed to centers are pure noise
(r 0.01).

Baseline per-minute rates (cur + a prev + b recent EWMA + K minutes of the position's league rate, fitted by Poisson
deviance on the fit seasons): pts a=0.2 b=4.0 K=120, reb a=0.35 b=2.0 K=120, ast a=1.0 b=32.0 K=1000, tpm a=0.35 b=2.0 K=60, stl a=1.0 b=1.0 K=500, blk a=0.75 b=4.0 K=500, tov a=0.5 b=2.0 K=250.
Dispersion (variance phi mu + kappa mu^2 given minutes, fitted by the ladder log loss on the fit population):
pts phi 2.5 kappa 0.0, reb phi 1.0 kappa 0.02, ast phi 1.1 kappa 0.0, tpm phi 1.1 kappa 0.04, stl phi 1.1 kappa 0.0, blk phi 1.1 kappa 0.02, tov phi 1.0 kappa 0.0, pra phi 2.25 kappa 0.0, pr phi 2.25 kappa 0.0, pa phi 2.25 kappa 0.0, ra phi 1.2 kappa 0.0, dk phi 3.0 kappa 0.0.

## Validation, 2024-25 (chooses)

Rows scored: 15,957 player-games, 1,222 games, 409 players (seasons [2024]).

Minutes (projected mean vs actual): rung-1 model MAE 4.478, RMSE 5.871; M6 MAE 4.434, RMSE 5.819.
M6 (four markets' summed ladder loss, with each market's selected rungs): gain +0.001953 +/- 0.000746 (z 2.62) -> ADOPTED.

**pts** -- cumulative ladder (each rung added on top of all before it)

| rungs | ladder log loss | gain vs previous | SE | z | Brier | exact log score | MAE | RMSE | bias |
|---|---|---|---|---|---|---|---|---|---|
| 1..1 | 0.25135 | +0.000000 | 0.000000 |  | 0.07981 | 3.2684 | 5.232 | 6.666 | -0.264 |
| 1..2 | 0.25069 | +0.000654 | 0.000130 | 5.02 | 0.07962 | 3.2663 | 5.219 | 6.651 | -0.270 |
| 1..3 | 0.25065 | +0.000044 | 0.000058 | 0.76 | 0.07961 | 3.2663 | 5.219 | 6.650 | -0.273 |
| 1..4 | 0.25068 | -0.000034 | 0.000074 | -0.46 | 0.07963 | 3.2663 | 5.219 | 6.650 | -0.302 |
| 1..5 | 0.25068 | +0.000002 | 0.000003 | 0.75 | 0.07963 | 3.2663 | 5.218 | 6.650 | -0.301 |
| 1..6 | 0.24923 | +0.001455 | 0.000147 | 9.89 | 0.07921 | 3.2610 | 5.192 | 6.617 | -0.322 |
| 1..6+M6 | 0.24845 | +0.000779 | 0.000287 | 2.72 | 0.07898 | 3.2591 | 5.183 | 6.604 | -0.242 |

Forward selection (rule: gain > 0 and z >= 1): rung 2 +0.000654 (z 5.02) ACCEPT; rung 3 +0.000044 (z 0.76) reject; rung 4 -0.000034 (z -0.44) reject; rung 5 +0.000000 (z 0.54) reject; rung 6 +0.001436 (z 9.93) ACCEPT

Selected rungs [1, 2, 6] with minutes m6: ladder log loss 0.24848, gain vs rung 1 +0.002872 +/- 0.000456 (z 6.3); MAE 5.185.
By position (gain vs rung 1, SE): C +0.002249 (0.000873, n 2238); F +0.001659 (0.000666, n 5552); G +0.003867 (0.000733, n 8167)
By primary role: creator +0.000174 (0.001936, n 1416); offensive rebounder +0.001911 (0.001265, n 1026); post +0.002961 (0.001329, n 1325); pull-up shooter +0.002728 (0.000879, n 4385); rim finisher +0.001112 (0.001274, n 1082); slasher +0.004224 (0.001064, n 3598); spacer +0.003625 (0.000885, n 3125)
Full model (1..6 + M6) by position: C +0.002362 (0.000959); F +0.001616 (0.000676); G +0.003920 (0.000753)
Full model by primary role: creator -0.000032 (0.002026); offensive rebounder +0.002256 (0.001324); post +0.002687 (0.001427); pull-up shooter +0.002536 (0.000900); rim finisher +0.002075 (0.001257); slasher +0.004103 (0.001083); spacer +0.003939 (0.000900)
Reliability, selected (bin, n, mean forecast, observed): 0.0-0.1 77874 0.017->0.016; 0.1-0.2 11041 0.145->0.162; 0.2-0.3 7430 0.247->0.258; 0.3-0.4 5967 0.349->0.385; 0.4-0.5 5199 0.449->0.481; 0.5-0.6 4513 0.549->0.591; 0.6-0.7 4215 0.649->0.670; 0.7-0.8 3546 0.749->0.765; 0.8-0.9 3651 0.852->0.864; 0.9-1.0 4220 0.950->0.948

**reb** -- cumulative ladder (each rung added on top of all before it)

| rungs | ladder log loss | gain vs previous | SE | z | Brier | exact log score | MAE | RMSE | bias |
|---|---|---|---|---|---|---|---|---|---|
| 1..1 | 0.27736 | +0.000000 | 0.000000 |  | 0.08762 | 2.2875 | 2.028 | 2.644 | -0.034 |
| 1..2 | 0.27701 | +0.000347 | 0.000137 | 2.53 | 0.08753 | 2.2865 | 2.031 | 2.640 | +0.026 |
| 1..3 | 0.27695 | +0.000060 | 0.000044 | 1.37 | 0.08751 | 2.2863 | 2.031 | 2.640 | +0.024 |
| 1..4 | 0.27685 | +0.000096 | 0.000037 | 2.62 | 0.08748 | 2.2860 | 2.029 | 2.639 | +0.016 |
| 1..5 | 0.27683 | +0.000019 | 0.000016 | 1.17 | 0.08748 | 2.2859 | 2.029 | 2.639 | +0.010 |
| 1..6 | 0.27640 | +0.000434 | 0.000065 | 6.69 | 0.08733 | 2.2844 | 2.025 | 2.634 | +0.009 |
| 1..6+M6 | 0.27607 | +0.000328 | 0.000311 | 1.06 | 0.08721 | 2.2826 | 2.023 | 2.633 | +0.034 |

Forward selection (rule: gain > 0 and z >= 1): rung 2 +0.000347 (z 2.53) ACCEPT; rung 3 +0.000060 (z 1.37) ACCEPT; rung 4 +0.000096 (z 2.62) ACCEPT; rung 5 +0.000019 (z 1.17) ACCEPT; rung 6 +0.000434 (z 6.69) ACCEPT

Selected rungs [1, 2, 3, 4, 5, 6] with minutes m6: ladder log loss 0.27607, gain vs rung 1 +0.001284 +/- 0.000397 (z 3.24); MAE 2.023.
By position (gain vs rung 1, SE): C +0.001216 (0.001394, n 2238); F +0.001397 (0.000680, n 5552); G +0.001226 (0.000420, n 8167)
By primary role: creator +0.000265 (0.001078, n 1416); offensive rebounder -0.001189 (0.002191, n 1026); post -0.000081 (0.001763, n 1325); pull-up shooter +0.000952 (0.000478, n 4385); rim finisher +0.006815 (0.002056, n 1082); slasher +0.001681 (0.000729, n 3598); spacer +0.001230 (0.000843, n 3125)
Full model (1..6 + M6) by position: C +0.001216 (0.001394); F +0.001397 (0.000680); G +0.001226 (0.000420)
Full model by primary role: creator +0.000265 (0.001078); offensive rebounder -0.001189 (0.002191); post -0.000081 (0.001763); pull-up shooter +0.000952 (0.000478); rim finisher +0.006815 (0.002056); slasher +0.001681 (0.000729); spacer +0.001230 (0.000843)
Reliability, selected (bin, n, mean forecast, observed): 0.0-0.1 63705 0.018->0.018; 0.1-0.2 10324 0.146->0.139; 0.2-0.3 7451 0.247->0.248; 0.3-0.4 5960 0.349->0.348; 0.4-0.5 5189 0.449->0.435; 0.5-0.6 4971 0.549->0.546; 0.6-0.7 4994 0.650->0.658; 0.7-0.8 5741 0.753->0.753; 0.8-0.9 7473 0.853->0.850; 0.9-1.0 11848 0.958->0.957

**ast** -- cumulative ladder (each rung added on top of all before it)

| rungs | ladder log loss | gain vs previous | SE | z | Brier | exact log score | MAE | RMSE | bias |
|---|---|---|---|---|---|---|---|---|---|
| 1..1 | 0.23560 | +0.000000 | 0.000000 |  | 0.07457 | 1.9727 | 1.548 | 2.037 | +0.008 |
| 1..2 | 0.23475 | +0.000849 | 0.000151 | 5.6 | 0.07430 | 1.9697 | 1.541 | 2.029 | -0.014 |
| 1..3 | 0.23461 | +0.000139 | 0.000053 | 2.62 | 0.07427 | 1.9694 | 1.540 | 2.028 | -0.013 |
| 1..4 | 0.23460 | +0.000011 | 0.000056 | 0.2 | 0.07426 | 1.9693 | 1.540 | 2.028 | -0.016 |
| 1..5 | 0.23460 | -0.000000 | 0.000000 | -0.87 | 0.07426 | 1.9693 | 1.540 | 2.028 | -0.016 |
| 1..6 | 0.23346 | +0.001143 | 0.000134 | 8.51 | 0.07396 | 1.9654 | 1.536 | 2.020 | -0.014 |
| 1..6+M6 | 0.23316 | +0.000304 | 0.000225 | 1.35 | 0.07391 | 1.9646 | 1.538 | 2.020 | +0.008 |

Forward selection (rule: gain > 0 and z >= 1): rung 2 +0.000849 (z 5.6) ACCEPT; rung 3 +0.000139 (z 2.62) ACCEPT; rung 4 +0.000011 (z 0.2) reject; rung 5 +0.000001 (z 0.82) reject; rung 6 +0.001148 (z 8.46) ACCEPT

Selected rungs [1, 2, 3, 6] with minutes m6: ladder log loss 0.23319, gain vs rung 1 +0.002414 +/- 0.000379 (z 6.36); MAE 1.539.
By position (gain vs rung 1, SE): C +0.000351 (0.000615, n 2238); F +0.000932 (0.000496, n 5552); G +0.003988 (0.000625, n 8167)
By primary role: creator +0.000176 (0.001823, n 1416); offensive rebounder +0.001059 (0.001050, n 1026); post +0.000973 (0.000990, n 1325); pull-up shooter +0.003366 (0.000723, n 4385); rim finisher +0.002078 (0.000992, n 1082); slasher +0.003832 (0.000939, n 3598); spacer +0.001635 (0.000666, n 3125)
Full model (1..6 + M6) by position: C +0.000388 (0.000647); F +0.000931 (0.000517); G +0.004040 (0.000637)
Full model by primary role: creator +0.000364 (0.001863); offensive rebounder +0.001204 (0.001074); post +0.000911 (0.001022); pull-up shooter +0.003327 (0.000762); rim finisher +0.002263 (0.001036); slasher +0.003907 (0.000971); spacer +0.001592 (0.000686)
Reliability, selected (bin, n, mean forecast, observed): 0.0-0.1 68391 0.014->0.014; 0.1-0.2 8544 0.144->0.134; 0.2-0.3 5170 0.248->0.251; 0.3-0.4 4670 0.350->0.353; 0.4-0.5 4389 0.450->0.448; 0.5-0.6 4205 0.550->0.546; 0.6-0.7 4046 0.649->0.646; 0.7-0.8 3511 0.750->0.757; 0.8-0.9 3528 0.851->0.857; 0.9-1.0 5245 0.954->0.957

**tpm** -- cumulative ladder (each rung added on top of all before it)

| rungs | ladder log loss | gain vs previous | SE | z | Brier | exact log score | MAE | RMSE | bias |
|---|---|---|---|---|---|---|---|---|---|
| 1..1 | 0.36016 | +0.000000 | 0.000000 |  | 0.11550 | 1.5258 | 1.046 | 1.405 | -0.040 |
| 1..2 | 0.35957 | +0.000589 | 0.000167 | 3.52 | 0.11527 | 1.5245 | 1.049 | 1.403 | -0.010 |
| 1..3 | 0.35959 | -0.000017 | 0.000051 | -0.34 | 0.11529 | 1.5245 | 1.048 | 1.403 | -0.012 |
| 1..4 | 0.35957 | +0.000017 | 0.000089 | 0.19 | 0.11528 | 1.5244 | 1.047 | 1.403 | -0.019 |
| 1..5 | 0.35961 | -0.000033 | 0.000021 | -1.61 | 0.11530 | 1.5245 | 1.047 | 1.403 | -0.022 |
| 1..6 | 0.35933 | +0.000278 | 0.000083 | 3.33 | 0.11520 | 1.5239 | 1.046 | 1.402 | -0.027 |
| 1..6+M6 | 0.35873 | +0.000604 | 0.000249 | 2.42 | 0.11502 | 1.5227 | 1.046 | 1.400 | -0.015 |

Forward selection (rule: gain > 0 and z >= 1): rung 2 +0.000589 (z 3.52) ACCEPT; rung 3 -0.000017 (z -0.34) reject; rung 4 +0.000010 (z 0.12) reject; rung 5 -0.000024 (z -0.98) reject; rung 6 +0.000313 (z 3.77) ACCEPT

Selected rungs [1, 2, 6] with minutes m6: ladder log loss 0.35870, gain vs rung 1 +0.001465 +/- 0.000351 (z 4.17); MAE 1.047.
By position (gain vs rung 1, SE): C +0.000256 (0.000428, n 2238); F +0.001265 (0.000545, n 5552); G +0.001931 (0.000563, n 8167)
By primary role: creator +0.002648 (0.001422, n 1416); offensive rebounder +0.000883 (0.000843, n 1026); post +0.000039 (0.000644, n 1325); pull-up shooter +0.000660 (0.000697, n 4385); rim finisher +0.000100 (0.000653, n 1082); slasher +0.001408 (0.000719, n 3598); spacer +0.003391 (0.000991, n 3125)
Full model (1..6 + M6) by position: C +0.000297 (0.000408); F +0.001266 (0.000552); G +0.001865 (0.000550)
Full model by primary role: creator +0.002182 (0.001422); offensive rebounder +0.000913 (0.000806); post -0.000190 (0.000630); pull-up shooter +0.000451 (0.000678); rim finisher +0.000269 (0.000635); slasher +0.001510 (0.000710); spacer +0.003664 (0.001003)
Reliability, selected (bin, n, mean forecast, observed): 0.0-0.1 40403 0.029->0.028; 0.1-0.2 12253 0.146->0.144; 0.2-0.3 8328 0.247->0.242; 0.3-0.4 6337 0.348->0.358; 0.4-0.5 5776 0.449->0.460; 0.5-0.6 4929 0.549->0.563; 0.6-0.7 4832 0.649->0.655; 0.7-0.8 5422 0.752->0.773; 0.8-0.9 5074 0.848->0.850; 0.9-1.0 2388 0.931->0.930

**Combos** (components' selections; dispersion their own)

| market | rung-1 ladder LL | selected LL | gain | SE | full LL | full gain | SE |
|---|---|---|---|---|---|---|---|
| pra | 0.28330 | 0.27945 | +0.003850 | 0.000633 | 0.27937 | +0.003936 | 0.000644 |
| pr | 0.31906 | 0.31528 | +0.003777 | 0.000613 | 0.31525 | +0.003810 | 0.000624 |
| pa | 0.28925 | 0.28526 | +0.003989 | 0.000631 | 0.28524 | +0.004012 | 0.000646 |
| ra | 0.32628 | 0.32325 | +0.003028 | 0.000565 | 0.32315 | +0.003123 | 0.000567 |

**dk_fp** (DFS population: 23,058 player-games)

| arm | MAE | RMSE | bias | ladder LL (10..60) | Brier |
|---|---|---|---|---|---|
| DK average RECONSTRUCTED from the box (season-to-date DK points per game played), Normal sd 0.55 x mean | 8.244 | 10.546 | -0.925 | 0.33566 | 0.09919 |
| rolling baseline (rung 1) | 7.953 | 10.128 | -0.338 | 0.28970 | 0.09120 |
| selected | 7.822 | 9.953 | -0.074 | 0.28401 | 0.08939 |
| full (1..6 + M6) | 7.819 | 9.948 | -0.108 | 0.28393 | 0.08937 |

P(DK >= x), mean forecast vs observed: 10: avg-arm 0.763, selected 0.830, observed 0.825; 20: avg-arm 0.483, selected 0.556, observed 0.562; 30: avg-arm 0.280, selected 0.308, observed 0.320; 40: avg-arm 0.157, selected 0.146, observed 0.152; 50: avg-arm 0.087, selected 0.060, observed 0.060; 60: avg-arm 0.047, selected 0.021, observed 0.019
rolling_vs_avg: ladder LL gain +0.045955 +/- 0.001824 (z 25.2)
selected_vs_avg: ladder LL gain +0.051645 +/- 0.002067 (z 24.99)
selected_vs_rolling: ladder LL gain +0.005690 +/- 0.000603 (z 9.44)


## Test, 2025-26 (read once with the frozen selection)

Rows scored: 16,098 player-games, 1,213 games, 419 players (seasons [2025]).

Minutes (projected mean vs actual): rung-1 model MAE 4.466, RMSE 5.873; M6 MAE 4.436, RMSE 5.832.
M6 (four markets' summed ladder loss, with each market's selected rungs): gain -0.000353 +/- 0.000766 (z -0.46) -> NOT shipped: the rule ships M6 only if the summed test loss falls; the rung-1 minutes model ships.

**pts** -- cumulative ladder (each rung added on top of all before it)

| rungs | ladder log loss | gain vs previous | SE | z | Brier | exact log score | MAE | RMSE | bias |
|---|---|---|---|---|---|---|---|---|---|
| 1..1 | 0.24997 | +0.000000 | 0.000000 |  | 0.07888 | 3.2620 | 5.158 | 6.626 | -0.118 |
| 1..2 | 0.24942 | +0.000550 | 0.000142 | 3.88 | 0.07870 | 3.2599 | 5.151 | 6.613 | -0.079 |
| 1..3 | 0.24937 | +0.000056 | 0.000055 | 1.01 | 0.07868 | 3.2597 | 5.149 | 6.612 | -0.080 |
| 1..4 | 0.24935 | +0.000014 | 0.000070 | 0.2 | 0.07870 | 3.2596 | 5.145 | 6.611 | -0.124 |
| 1..5 | 0.24935 | -0.000002 | 0.000003 | -0.68 | 0.07870 | 3.2596 | 5.145 | 6.611 | -0.122 |
| 1..6 | 0.24789 | +0.001460 | 0.000146 | 9.99 | 0.07826 | 3.2538 | 5.121 | 6.576 | -0.108 |
| 1..6+M6 | 0.24746 | +0.000435 | 0.000285 | 1.53 | 0.07816 | 3.2516 | 5.130 | 6.571 | +0.039 |

Selected rungs [1, 2, 6] with minutes m6: ladder log loss 0.24753, gain vs rung 1 +0.002444 +/- 0.000453 (z 5.4); MAE 5.135.
By position (gain vs rung 1, SE): C +0.002575 (0.000987, n 2308); F +0.001995 (0.000718, n 5687); G +0.002721 (0.000707, n 8103)
By primary role: creator -0.000035 (0.001737, n 1368); offensive rebounder +0.002530 (0.001203, n 1451); post +0.001714 (0.001332, n 1292); pull-up shooter +0.001952 (0.000952, n 4161); rim finisher +0.002559 (0.001406, n 817); slasher +0.003722 (0.000996, n 4275); spacer +0.002700 (0.000979, n 2734)
Full model (1..6 + M6) by position: C +0.003229 (0.001072); F +0.001867 (0.000735); G +0.002763 (0.000707)
Full model by primary role: creator +0.000157 (0.001775); offensive rebounder +0.002539 (0.001245); post +0.001877 (0.001451); pull-up shooter +0.001900 (0.000960); rim finisher +0.003288 (0.001483); slasher +0.003741 (0.000990); spacer +0.002762 (0.000985)
Reliability, selected (bin, n, mean forecast, observed): 0.0-0.1 77609 0.017->0.015; 0.1-0.2 11411 0.145->0.137; 0.2-0.3 7529 0.248->0.253; 0.3-0.4 6106 0.348->0.360; 0.4-0.5 5225 0.448->0.455; 0.5-0.6 4734 0.549->0.573; 0.6-0.7 4333 0.650->0.675; 0.7-0.8 3942 0.749->0.756; 0.8-0.9 3786 0.849->0.859; 0.9-1.0 4109 0.950->0.947

**reb** -- cumulative ladder (each rung added on top of all before it)

| rungs | ladder log loss | gain vs previous | SE | z | Brier | exact log score | MAE | RMSE | bias |
|---|---|---|---|---|---|---|---|---|---|
| 1..1 | 0.27319 | +0.000000 | 0.000000 |  | 0.08589 | 2.2702 | 1.994 | 2.586 | +0.074 |
| 1..2 | 0.27257 | +0.000627 | 0.000134 | 4.68 | 0.08569 | 2.2679 | 1.989 | 2.580 | +0.071 |
| 1..3 | 0.27260 | -0.000033 | 0.000039 | -0.85 | 0.08570 | 2.2680 | 1.989 | 2.580 | +0.073 |
| 1..4 | 0.27258 | +0.000020 | 0.000041 | 0.48 | 0.08569 | 2.2679 | 1.988 | 2.580 | +0.073 |
| 1..5 | 0.27255 | +0.000030 | 0.000016 | 1.92 | 0.08569 | 2.2678 | 1.988 | 2.579 | +0.068 |
| 1..6 | 0.27218 | +0.000371 | 0.000072 | 5.14 | 0.08559 | 2.2667 | 1.987 | 2.576 | +0.078 |
| 1..6+M6 | 0.27211 | +0.000069 | 0.000311 | 0.22 | 0.08569 | 2.2666 | 1.995 | 2.577 | +0.120 |

Selected rungs [1, 2, 3, 4, 5, 6] with minutes m6: ladder log loss 0.27211, gain vs rung 1 +0.001084 +/- 0.000409 (z 2.65); MAE 1.995.
By position (gain vs rung 1, SE): C +0.001285 (0.001300, n 2308); F +0.001168 (0.000655, n 5687); G +0.000968 (0.000530, n 8103)
By primary role: creator -0.001599 (0.001061, n 1368); offensive rebounder +0.004270 (0.001760, n 1451); post +0.001191 (0.001512, n 1292); pull-up shooter -0.000415 (0.000549, n 4161); rim finisher -0.001711 (0.002024, n 817); slasher +0.002095 (0.000803, n 4275); spacer +0.002221 (0.000892, n 2734)
Full model (1..6 + M6) by position: C +0.001285 (0.001300); F +0.001168 (0.000655); G +0.000968 (0.000530)
Full model by primary role: creator -0.001599 (0.001061); offensive rebounder +0.004270 (0.001760); post +0.001191 (0.001512); pull-up shooter -0.000415 (0.000549); rim finisher -0.001711 (0.002024); slasher +0.002095 (0.000803); spacer +0.002221 (0.000892)
Reliability, selected (bin, n, mean forecast, observed): 0.0-0.1 64798 0.018->0.015; 0.1-0.2 10699 0.146->0.131; 0.2-0.3 7349 0.247->0.229; 0.3-0.4 5919 0.349->0.335; 0.4-0.5 5441 0.449->0.428; 0.5-0.6 4914 0.550->0.545; 0.6-0.7 4809 0.650->0.641; 0.7-0.8 6117 0.751->0.740; 0.8-0.9 7069 0.853->0.844; 0.9-1.0 11669 0.956->0.953

**ast** -- cumulative ladder (each rung added on top of all before it)

| rungs | ladder log loss | gain vs previous | SE | z | Brier | exact log score | MAE | RMSE | bias |
|---|---|---|---|---|---|---|---|---|---|
| 1..1 | 0.23704 | +0.000000 | 0.000000 |  | 0.07459 | 1.9727 | 1.548 | 2.042 | -0.000 |
| 1..2 | 0.23633 | +0.000706 | 0.000159 | 4.44 | 0.07440 | 1.9702 | 1.544 | 2.036 | -0.002 |
| 1..3 | 0.23631 | +0.000024 | 0.000048 | 0.5 | 0.07439 | 1.9701 | 1.544 | 2.036 | -0.001 |
| 1..4 | 0.23627 | +0.000034 | 0.000051 | 0.68 | 0.07438 | 1.9700 | 1.542 | 2.035 | -0.011 |
| 1..5 | 0.23627 | -0.000000 | 0.000000 | -0.32 | 0.07438 | 1.9700 | 1.542 | 2.035 | -0.011 |
| 1..6 | 0.23548 | +0.000790 | 0.000142 | 5.58 | 0.07417 | 1.9668 | 1.539 | 2.029 | -0.005 |
| 1..6+M6 | 0.23619 | -0.000711 | 0.000245 | -2.9 | 0.07447 | 1.9691 | 1.549 | 2.037 | +0.034 |

Selected rungs [1, 2, 3, 6] with minutes m6: ladder log loss 0.23625, gain vs rung 1 +0.000788 +/- 0.000432 (z 1.82); MAE 1.551.
By position (gain vs rung 1, SE): C -0.001049 (0.000759, n 2308); F +0.000767 (0.000553, n 5687); G +0.001326 (0.000721, n 8103)
By primary role: creator -0.001385 (0.001877, n 1368); offensive rebounder -0.000290 (0.000931, n 1451); post +0.000242 (0.000988, n 1292); pull-up shooter +0.000383 (0.000907, n 4161); rim finisher +0.001130 (0.000854, n 817); slasher +0.002086 (0.001057, n 4275); spacer +0.001191 (0.000668, n 2734)
Full model (1..6 + M6) by position: C -0.001041 (0.000793); F +0.000774 (0.000571); G +0.001428 (0.000739)
Full model by primary role: creator -0.000928 (0.001956); offensive rebounder -0.000387 (0.000949); post -0.000137 (0.001048); pull-up shooter +0.000512 (0.000932); rim finisher +0.001173 (0.000910); slasher +0.002114 (0.001077); spacer +0.001263 (0.000679)
Reliability, selected (bin, n, mean forecast, observed): 0.0-0.1 69169 0.014->0.014; 0.1-0.2 8654 0.145->0.139; 0.2-0.3 5322 0.247->0.234; 0.3-0.4 4618 0.349->0.348; 0.4-0.5 4473 0.450->0.439; 0.5-0.6 4242 0.549->0.535; 0.6-0.7 3824 0.650->0.642; 0.7-0.8 3709 0.750->0.748; 0.8-0.9 3977 0.851->0.849; 0.9-1.0 4698 0.953->0.948

**tpm** -- cumulative ladder (each rung added on top of all before it)

| rungs | ladder log loss | gain vs previous | SE | z | Brier | exact log score | MAE | RMSE | bias |
|---|---|---|---|---|---|---|---|---|---|
| 1..1 | 0.35106 | +0.000000 | 0.000000 |  | 0.11221 | 1.4940 | 1.026 | 1.376 | +0.011 |
| 1..2 | 0.35061 | +0.000451 | 0.000177 | 2.55 | 0.11206 | 1.4930 | 1.025 | 1.375 | +0.014 |
| 1..3 | 0.35058 | +0.000023 | 0.000050 | 0.46 | 0.11205 | 1.4929 | 1.024 | 1.375 | +0.013 |
| 1..4 | 0.35043 | +0.000156 | 0.000072 | 2.16 | 0.11199 | 1.4926 | 1.025 | 1.374 | +0.021 |
| 1..5 | 0.35042 | +0.000006 | 0.000018 | 0.33 | 0.11199 | 1.4926 | 1.025 | 1.374 | +0.019 |
| 1..6 | 0.35022 | +0.000199 | 0.000075 | 2.67 | 0.11193 | 1.4921 | 1.024 | 1.374 | +0.020 |
| 1..6+M6 | 0.35034 | -0.000122 | 0.000249 | -0.49 | 0.11196 | 1.4922 | 1.028 | 1.375 | +0.039 |

Selected rungs [1, 2, 6] with minutes m6: ladder log loss 0.35052, gain vs rung 1 +0.000536 +/- 0.000349 (z 1.53); MAE 1.027.
By position (gain vs rung 1, SE): C +0.000992 (0.000584, n 2308); F -0.000087 (0.000512, n 5687); G +0.000843 (0.000570, n 8103)
By primary role: creator -0.001465 (0.001085, n 1368); offensive rebounder +0.000405 (0.000941, n 1451); post +0.000648 (0.000817, n 1292); pull-up shooter +0.000438 (0.000798, n 4161); rim finisher +0.000917 (0.000902, n 817); slasher +0.001048 (0.000623, n 4275); spacer +0.000787 (0.000993, n 2734)
Full model (1..6 + M6) by position: C +0.001034 (0.000556); F -0.000109 (0.000493); G +0.001199 (0.000564)
Full model by primary role: creator -0.001393 (0.001030); offensive rebounder +0.000450 (0.000909); post +0.000632 (0.000783); pull-up shooter +0.000800 (0.000774); rim finisher +0.001136 (0.000875); slasher +0.000949 (0.000610); spacer +0.001318 (0.000994)
Reliability, selected (bin, n, mean forecast, observed): 0.0-0.1 41970 0.029->0.027; 0.1-0.2 12396 0.145->0.132; 0.2-0.3 8148 0.249->0.241; 0.3-0.4 6548 0.348->0.340; 0.4-0.5 5478 0.449->0.442; 0.5-0.6 4963 0.550->0.543; 0.6-0.7 4939 0.650->0.649; 0.7-0.8 5208 0.751->0.758; 0.8-0.9 4838 0.847->0.851; 0.9-1.0 2100 0.929->0.930

**Combos** (components' selections; dispersion their own)

| market | rung-1 ladder LL | selected LL | gain | SE | full LL | full gain | SE |
|---|---|---|---|---|---|---|---|
| pra | 0.28126 | 0.27843 | +0.002823 | 0.000678 | 0.27829 | +0.002968 | 0.000684 |
| pr | 0.31482 | 0.31172 | +0.003091 | 0.000634 | 0.31160 | +0.003221 | 0.000641 |
| pa | 0.28914 | 0.28612 | +0.003027 | 0.000643 | 0.28599 | +0.003152 | 0.000653 |
| ra | 0.32612 | 0.32439 | +0.001725 | 0.000630 | 0.32432 | +0.001796 | 0.000632 |

**dk_fp** (DFS population: 23,544 player-games)

| arm | MAE | RMSE | bias | ladder LL (10..60) | Brier |
|---|---|---|---|---|---|
| DK average RECONSTRUCTED from the box (season-to-date DK points per game played), Normal sd 0.55 x mean | 8.164 | 10.503 | -0.663 | 0.33281 | 0.09796 |
| rolling baseline (rung 1) | 7.876 | 10.040 | +0.005 | 0.28549 | 0.08955 |
| selected | 7.779 | 9.882 | +0.434 | 0.28024 | 0.08794 |
| full (1..6 + M6) | 7.772 | 9.877 | +0.390 | 0.28011 | 0.08790 |

P(DK >= x), mean forecast vs observed: 10: avg-arm 0.759, selected 0.832, observed 0.820; 20: avg-arm 0.479, selected 0.559, observed 0.559; 30: avg-arm 0.277, selected 0.308, observed 0.302; 40: avg-arm 0.154, selected 0.144, observed 0.136; 50: avg-arm 0.083, selected 0.058, observed 0.052; 60: avg-arm 0.044, selected 0.021, observed 0.016
rolling_vs_avg: ladder LL gain +0.047312 +/- 0.001784 (z 26.53)
selected_vs_avg: ladder LL gain +0.052567 +/- 0.002180 (z 24.11)
selected_vs_rolling: ladder LL gain +0.005255 +/- 0.000705 (z 7.46)


## What shipped (research/data/nba_matchup/coefs.json)

Every market's selected rungs beat rung 1 on the test, so all four ship them; M6 failed its test (-0.00035 +/- 0.00077),
so the rung-1 minutes model ships. The shipped combination (selected rungs + rung-1 minutes) is not any single arm
the test printed, so it was scored after the decisions (`shipped_report`, no choice made on it):

| market | shipped rungs | 2024-25 gain vs rung 1 (SE, z) | 2025-26 gain vs rung 1 (SE, z) | 2025-26 MAE rung 1 -> shipped | 2025-26 bias |
|---|---|---|---|---|---|
| pts | [1, 2, 6] | +0.00209 (0.00021, 9.83) | +0.00199 (0.00021, 9.29) | 5.158 -> 5.127 | -0.072 |
| reb | [1, 2, 3, 4, 5, 6] | +0.00096 (0.00016, 6.02) | +0.00101 (0.00017, 5.88) | 1.994 -> 1.987 | +0.078 |
| ast | [1, 2, 3, 6] | +0.00214 (0.00021, 10.0) | +0.00153 (0.00024, 6.45) | 1.548 -> 1.540 | +0.005 |
| tpm | [1, 2, 6] | +0.00090 (0.00018, 4.89) | +0.00066 (0.00019, 3.51) | 1.026 -> 1.024 | +0.015 |
| pra | components' | +0.00290 (0.00028, 10.41) | +0.00278 (0.00031, 9.1) | 6.522 -> 6.470 | +0.012 |
| pr | components' | +0.00272 (0.00025, 10.74) | +0.00256 (0.00027, 9.42) | 6.005 -> 5.968 | +0.007 |
| pa | components' | +0.00295 (0.00030, 9.83) | +0.00286 (0.00031, 9.3) | 5.636 -> 5.590 | -0.067 |
| ra | components' | +0.00233 (0.00023, 9.91) | +0.00234 (0.00028, 8.38) | 2.789 -> 2.774 | +0.084 |

Calibration of the shipped model on 2025-26, mean forecast -> observed by decile (all thresholds pooled):

- pts: 0.017->0.016 (n 78,206); 0.145->0.148 (n 11,352); 0.248->0.252 (n 7,574); 0.348->0.366 (n 6,048); 0.449->0.472 (n 5,264); 0.549->0.585 (n 4,651); 0.649->0.682 (n 4,253); 0.749->0.770 (n 3,837); 0.850->0.860 (n 3,555); 0.950->0.948 (n 4,044)
- reb: 0.018->0.016 (n 65,148); 0.146->0.138 (n 10,769); 0.248->0.228 (n 7,298); 0.348->0.335 (n 5,979); 0.449->0.442 (n 5,324); 0.549->0.543 (n 4,912); 0.650->0.652 (n 4,831); 0.751->0.747 (n 6,147); 0.852->0.851 (n 6,936); 0.957->0.954 (n 11,440)
- ast: 0.014->0.014 (n 69,537); 0.145->0.141 (n 8,650); 0.247->0.243 (n 5,265); 0.350->0.353 (n 4,669); 0.450->0.443 (n 4,474); 0.549->0.545 (n 4,273); 0.649->0.655 (n 3,699); 0.750->0.755 (n 3,679); 0.851->0.854 (n 3,979); 0.953->0.954 (n 4,461)
- fg3m: 0.029->0.027 (n 42,440); 0.145->0.135 (n 12,283); 0.248->0.249 (n 8,157); 0.348->0.341 (n 6,518); 0.449->0.452 (n 5,472); 0.550->0.552 (n 4,946); 0.650->0.658 (n 4,833); 0.750->0.759 (n 5,264); 0.847->0.854 (n 4,662); 0.928->0.933 (n 2,013)
- pra: 0.020->0.017 (n 76,146); 0.145->0.135 (n 13,588); 0.248->0.248 (n 9,474); 0.349->0.354 (n 7,671); 0.450->0.469 (n 6,816); 0.549->0.565 (n 6,221); 0.650->0.673 (n 5,791); 0.750->0.773 (n 5,677); 0.851->0.867 (n 5,695); 0.955->0.955 (n 7,803)

Points (and so PRA) are UNDER-confident in the middle: forecasts of 0.55 come in at 0.585, 0.65 at 0.68 -- the real
distribution is narrower around the line than the minutes-mixed negative binomial. Rebounds lean slightly the other
way. Neither was recalibrated (the rule did not provide for it); a per-market recalibration is a change for 2026-27
games to judge.

The shipped coefficients:

| market | coefficient (per unit of the feature) |
|---|---|
| pts | _icpt -0.0351, def_pts +1.1913, home +0.0099, oe_all +0.1939, oe_form +0.1536, pace +0.6125, tm_out +0.2186 |
| reb | _icpt -0.0177, comp +0.0848, home +0.0104, oe_all +0.8030, oe_form +0.9929, oe_pos +0.4839, pace +0.5241, tm_out +0.0952, x:dfrac*off:miss -0.0209, x:dfrac*off:oreb_inv +0.1759, x:dfrac*off:three +0.0614, x:ofrac*def:miss -0.4854, x:ofrac*def:oreb +0.6493 |
| ast | _icpt -0.0261, home +0.0158, oe_all +1.1698, oe_form +0.2659, oe_pos +0.6554, pace +0.6532, tm_out +0.2572 |
| tpm | _icpt -0.0112, home +0.0180, oe_all +0.7925, oe_form +0.8799, pace +0.4476, tm_out +0.1077 |
| stl | _icpt -0.0066, home +0.0022, pace +0.6606 |
| blk | _icpt +0.0057, home +0.0109, pace +0.6521 |
| tov | _icpt -0.0159, home -0.0038, pace +0.2235 |

How big the matchup is (2025-26 props population, the shipped model): the defense terms (rungs 2-5) have an SD of
2.8% of the points mean (5-95%: -3.6% to +4.9%), 2.4% of rebounds, 3.8% of assists, 3.9% of threes; teammates ruled
out add 1-9% to points (SD 2.3% when a regular is out, 78% of rows) and 1-11% to assists. Near a line, a 4% shift of the
mean moves P(stat >= k) by roughly three to four points.

### The answer to the owner's question, as measured

- WHAT THE DEFENSE ALLOWS OVERALL, opponent-adjusted (rung 2: what its opponents produce over their own norms) moves
  every market, held out both seasons (test z 2.6-4.7 for its own increment).
- WHO IS MISSING from his team (rung 6: the usage / assists / rebounds of regulars ruled out) is the largest single
  improvement in points and assists (test z 10 and 5.6 for its increment).
- POSITION ALLOWANCES ("DvP", rung 3) passed validation for rebounds and assists only and add nothing measurable on the
  test (increments z -0.85 and 0.5).
- STYLE x WHAT IS CONCEDED (rung 4: his rim / three / pull-up / spot-up / transition / putback shares against the
  opponent's matching concessions; his rebound fractions against the opponent's misses and offensive rebounding) did
  NOT improve held-out points, assists or threes on 2024-25 and ships at weight 0 for them. It passed validation for
  rebounds (z 2.62) and adds ~nothing there on the test (+0.00002, z 0.48); it ships for rebounds only because the
  market-level rule passed. For threes it was rejected on 2024-25 (z 0.12) though 2025-26 reads z 2.16 -- the rule
  chose on validation; it is a candidate to re-test on 2026-27, nothing more.
- COMPARABLE-OPPONENT HISTORY (rung 5) adds nothing anywhere (increments of at most 3e-5 in ladder log loss): once the opponent's profile is in the model,
  how a player did against similar defenses before carries no extra information. It ships only inside rebounds' set
  (coefficient 0.085, effect < 0.5%); the payload still lists the comparable games with their weights as information.
- By primary role, the gains concentrate in slashers and spacers; creators gain nothing (2025-26 points -0.00004).

## dk_fp (DraftKings classic points)

Vigil has NO NBA lineup builder (the DFS tab lists no NBA; simulate.dfs_build has no NBA roster positions): the dk_fp
distribution reaches the owner on the Matchups tab (market `dk_fp`, lines 10-60), and `nba_matchup.dfs_projections`
stays as a tested hook for a future NBA builder. The comparison arm is what Vigil's generic builder would do with a
DraftKings CSV: the season average with a Normal of sd 0.55 x mean (simulate.py's default cv). Its "season average" is
RECONSTRUCTED from the box -- season-to-date DK points per game played as of the game, last season's when none --
not DraftKings' own AvgPointsPerGame, which was never read for these seasons (it could differ in its game count,
zero-minute games and its opening-night value). On 2025-26's DFS population (23,544 player-games: 5+ prior games,
rung-1 projection 10+ minutes):

| arm | MAE | RMSE | bias | ladder log loss (10..60) |
|---|---|---|---|---|
| DK average reconstructed from the box, Normal sd 0.55 x mean | 8.164 | 10.503 | -0.663 | 0.33281 |
| rolling baseline (rung 1) | 7.876 | 10.040 | +0.005 | 0.28549 |
| shipped | 7.814 | 9.951 | +0.079 | 0.28266 |

(2024-25: 8.244 / 7.953 / 7.884 MAE.) P(DK >= x), mean
forecast vs observed on 2025-26: 10: DK-average arm 0.759, shipped 0.823, observed 0.820; 20: DK-average arm 0.479, shipped 0.548, observed 0.559; 30: DK-average arm 0.277, shipped 0.300, observed 0.302; 40: DK-average arm 0.154, shipped 0.140, observed 0.136; 50: DK-average arm 0.083, shipped 0.056, observed 0.052; 60: DK-average arm 0.044, shipped 0.020, observed 0.016.
The DK-average Normal is badly miscalibrated in both tails (its 0.85 forecasts came in at 0.94, its 0.25 at 0.19); the
shipped dk_fp's deciles sit within two points. By the rule (MAE and log loss both better), dk_fp replaces
the reconstructed average (`shipped.dk_fp_replaces_avg` true) wherever a builder would use it. The DraftKings scoring
table (point 1, three 0.5, rebound 1.25, assist 1.5, steal 2, block 2, turnover -0.5, double-double 1.5,
triple-double 3, a triple-double also earning the double-double bonus) is as UNDERSTOOD from DraftKings' NBA rules; it
was NOT machine-read from DraftKings: its rules pages are script-rendered shells and api.draftkings.com answered 403 to
this host (both reviews tried, 2026-10-23). The triple-double / double-double stacking in particular is to be verified
against DraftKings' own FPTS -- a contest standings export or the draftables feed after a slate settles, which the
owner's PC can read -- on a night with a triple-double. The table is pinned by a guard; a guard is not a source.

## Against Kalshi's own prices (2025-26, descriptive; the rule chose nothing on it)

Kalshi's NBA player props launched 2025-11-14; nothing earlier exists. A seeded sample of 800 settled markets per series
(points, rebounds, assists, threes; PRA had no historical markets), all thresholds of each sampled player-game, priced at
the last hourly candle ending at or before ESPN's tip time. 17 markets could not be matched to a player, 555 had no
two-sided pre-tip book; every matched market's Kalshi result agreed with ESPN's box but one.

- All two-sided books: n 2,628 markets / 478 games: log loss model 0.5523, rung 1 0.5527, Kalshi mid 0.5541; Brier 0.1868 / 0.1868 / 0.1882; bets 505 (215 YES): ROI -13.8%, 90% interval [-24.3%, -2.7%], -30.63 dollars.
- Tight books (spread <= 10c): n 2,278 markets / 445 games: log loss model 0.5562, rung 1 0.5555, Kalshi mid 0.5424; Brier 0.1883 / 0.1879 / 0.1833; bets 485 (197 YES): ROI -14.3%, 90% interval [-24.9%, -3.5%], -30.27 dollars.
- By stat (all books): ast: model 0.5564 vs mid 0.5320, bets 160 ROI -23.6%; pts: model 0.5809 vs mid 0.5733, bets 150 ROI -12.0%; reb: model 0.5497 vs mid 0.5649, bets 118 ROI -19.1%; tpm: model 0.5175 vs mid 0.5436, bets 77 ROI +14.7%.

Uncertainty (the Fable review's game-clustered SEs, scratchpad fable_nba_review/kalshi_check.py): on tight books the
market is better by 0.0138 +/- 0.0041 nats a market (z -3.3); on all two-sided books the difference is +0.0018 +/-
0.0052 in the model's favour (z 0.3: indistinguishable). By stat, the market is better on assists (z -2.2), the model
on threes (z +2.5), points and rebounds within noise. On these Kalshi-priced rows the shipped model against rung 1 is
+0.0004 +/- 0.0020 (all) / -0.0006 +/- 0.0022 (tight): the matchup rungs' gains (0.001-0.002 nats per threshold on
16,000 rows) are below what 2,628 markets can resolve.

The market is the better forecaster where its book is tight (0.542 against 0.556, z -3.3), and betting the model's
disagreements LOST: -14% on 485 one-contract bets, the 90% interval entirely below zero. Only threes' bets came out
ahead (+15% on 77, interval -12% to +42%: noise). The model's figures are a projection and an explanation of the
matchup, not an edge over Kalshi; the board says so on every slate and a `positive_ev` label is only the model
disagreeing with the market.

## Production (nba_matchup.py, pure Python)

- `nba_matchup.run_job()` -- the `nba_matchup` deep job: backfills last season once (nba_matchup_data.update, ~1,230
  summaries serial, ~12 minutes), adds this season's new games, then `rebuild()` replays both seasons through the Book
  with the committed fit into deep key `nba_matchup_book_<season>`. Measured here: 3.2 s for 1,228 games, an 11.7 MB
  pickle, peak RSS ~250 MB.
- `nba_matchup.board(date)` -> the shared payload contract (scratchpad matchup_payload_contract.md): every player of the
  date's games projected 18+ minutes, ruled-out players listed with status out (and counted in their teammates' rung 6),
  per market baseline / adjusted mean / distribution / lines with p, p_lo, p_hi, hit_all, hit_recent, hit_similar_def,
  the Kalshi market block and prop_value's value + labels when kalshi_nba_props' index answers, the factor matrix
  (every shipped feature with its exact multiplier; each rung held at weight 0 as ONE row per market, mult 1, its
  measured channels in the row's detail), comparables with similarity, weight and why, notes. `strong_fit` (after the
  reviews): the product of the shipped DEFENSE terms' multipliers (rungs 2-5 and the defense's recent form -- never
  rung 1, never teammates out) is the market's `fit_multiplier`; at 1.05 or more every line of the market carries
  "strong_fit" with `fit_side` "yes", at 0.95 or less with `fit_side` "no", otherwise neither (`fit_side` null). A
  date the book already holds is not priced (the board says so and returns no players), and the hit tables count only
  games before the date. The replay (`rebuild`) never takes in games of the owner's current MT day, so the book's
  `through` stays before today's board however late the job runs.
  Measured on 2026-10-23's 12 games: ~3 s of compute for 260 players, plus ~3 s of ESPN fetches and ~14 s for the Kalshi
  index; peak RSS ~136 MB; ~12.9 MB of JSON (~1.1 MB a game). `board_isolated()` runs it in a niced child.
- `nba_matchup.run_job_isolated()` -- the same job in a niced child, for the server's registration (under
  deep_cache.HEAVY_BUILD).
- `nba_matchup.dfs_projections(date, players)` -> per DK pool player {proj, sd, q (10/25/50/75/90), p (10..60), minutes,
  status, replaces_avg, source} or None; never raises.
- Parity: research/nba_matchup_parity.py replays the deep store to 2026-03-10 exactly as rebuild does and prices that
  date's 235 players with production code: |production - research| in P(stat >= k) has mean 0.0002-0.0005, 95th
  percentile 0.0007-0.0015, max 0.054 (the comparables' history: production keeps two seasons, research rows saw back
  to 2022-23 -- rows.pkl holds 2022-23 .. 2025-26; 2021-22 was history for the profiles only).

## Limitations, stated

- Pre-game availability in the held-out seasons is read off the box (a regular who did not play was "out");
  production reads ESPN's injury statuses ("Out" only counts as absent; Day-To-Day is 'questionable'). Rung 6's
  measured gain is therefore an UPPER BOUND for production -- see "Rung 6 in production" below.
- Conditioning on the player entering the game is the market's own (a player who never takes the court settles at a
  fair price); the probabilities are NOT P(he plays and reaches k).
- Minutes are the largest uncertainty; the minutes model is linear on his own history (MAE 4.47 on 2025-26). Starting
  lineups, announced ~30 minutes before the tip, are not read.
- Attempts and efficiency for points and threes enter as separate channels (attempts allowed by zone; make rates allowed)
  in one multiplicative model, not a two-stage model; rebounds likewise (the opponent's misses and offensive rebounding
  x his rebound fractions). Teammate effects are one usage pool, not who absorbs it.
- Points' probabilities are under-confident near the line (see calibration); no recalibration ships.
- Early in a season the as-of profiles lean on last season (shrinkage by measured persistence); the ratios are shrunk,
  so a 10-game hot defense moves little.
- The PROTECTED Kalshi contracts (first-half refund) are a different contract; the board prices the standard ones only.

## Reproduce

    export DEEP_CACHE_DIR=<scratch>/deep_nba_matchup VIGIL_NO_BG=1
    python3 research/nba_matchup_fetch.py <raw> --seasons 2021,2022,2023,2024,2025,2026
    python3 research/nba_matchup_build.py <raw> --seasons 2021,2022,2023,2024,2025,2026
    python3 research/nba_matchup_fit.py stage1 $DEEP_CACHE_DIR <work>
    python3 research/nba_matchup_fit.py stage2 $DEEP_CACHE_DIR <work>
    python3 research/nba_matchup_fit.py validate <work>      # refuses unless the rule's hash matches
    python3 research/nba_matchup_fit.py test <work>          # refuses a second run
    python3 research/nba_matchup_fit.py freeze <work>
    python3 research/nba_matchup_fit.py shipped_report <work>
    python3 research/nba_matchup_fit.py render <work> val|test
    python3 research/nba_matchup_kalshi.py list <raw> ; python3 research/nba_matchup_kalshi.py candles <raw> --n 800
    python3 research/nba_matchup_fit.py dump_preds <work>
    python3 research/nba_matchup_kalshi.py compare <raw> --preds <work>/test_preds.pkl --store $DEEP_CACHE_DIR --out <work>/kalshi_compare.json
    python3 research/nba_matchup_fit.py record_market <work>
    python3 research/nba_matchup_parity.py $DEEP_CACHE_DIR <work> 2026-03-10
    python3 tests/guards_nba_matchup.py

## Rung 6 in production: the late-scratch leak and the train/serve skew (from both reviews)

Who the absent regulars were. The orchestrator's composition (research/nba_absent_reasons.py, 2024-25 + 2025-26; a
regular = 15+ minutes in his team's previous game, missing from this one; 3,355 absences):

| ESPN's box says | share | minutes-weighted |
|---|---|---|
| no box line at all (inactive list: not with team, G League, traded, or omitted) | 57.1% | 59.4% |
| inactive with an injury / rest / personal reason | 24.7% | 26.0% |
| COACH'S DECISION (active, did not play: on no injury report) | 18.2% | 14.6% |

The Fable review's count with the model's own regular definition (Book.rotation, 2025-26, 4,187 absences) agrees in
kind: 67.0% not on the box, 17.1% listed with a reason, 15.9% coach's decision. About one absence in six can never be
seen before the tip.

The leak's size (the Fable review's re-score of the SHIPPED model on the 2025-26 props population with only tm_out
recomputed; scratchpad fable_nba_review/recompute.py; gain vs rung 1 in ladder log loss, game-clustered SE):

| absences counted as "out" | pts | ast | reb | fg3m |
|---|---|---|---|---|
| every absence (what research scored) | +0.001994 (0.000215) | +0.001533 (0.000238) | +0.001014 (0.000172) | +0.000663 (0.000189) |
| every absence EXCEPT coach's decision (the most a pre-tip report can show) | +0.001896 (0.000207) | +0.001485 (0.000233) | +0.000983 (0.000170) | +0.000672 (0.000190) |
| the difference (the coach's-decision leak, paired) | +0.000097 (0.000041) | +0.000048 (0.000036) | +0.000031 (0.000017) | -0.000009 (0.000015) |
| tm_out = 0 | -0.000439 (0.000195) | +0.000223 (0.000205) | +0.000779 (0.000164) | +0.000514 (0.000184) |

So the leak itself is small -- about 5% of the points gain, 3% of assists', nil on rebounds and threes -- but rung 6
is essentially the WHOLE points gain (without it the shipped points model is slightly worse than rung 1). The larger,
unmeasured gap is TRAIN/SERVE SKEW: the coefficient (points +0.219 per ln(1 + usage share out)) was fitted on every
box absence, and production feeds it ESPN's "Out" listings only, a subset (a regular rested, G-League-assigned or
"not with team" is on the league's inactive list but not always "Out" on ESPN; a Questionable player scratched late
is "Out" only if ESPN flipped him before the board built). Production's teammates-out adjustments are therefore
systematically smaller than research's, and rung 6's measured gain (test z 10 on points) is an UPPER BOUND for
production. The live figure needs production's own tm_out logged beside each prediction and graded (a shadow
bucket); that is a follow-up, not built here.

## Review round (2026-10-23)

Both reviews AGREE AFTER FIXES (no blocker). Changed, with no shipped coefficient, selection or test number touched:

- strong_fit (O1 / F1): from the shipped defense terms only (rungs 2-5 + the defense's recent form), side-aware
  (`fit_side` on every line; "strong_fit" only on the side the shift favours). On the pre-fix code, a synthetic board
  with an unfavourable defense put "strong_fit" on 88 lines whose defense multiplier was <= 0.95, and a board with only
  a teammate out and no defense term put it on 174 lines; both boards now pass their guards.
- Past date (O2 / F2): `board()` refuses a date on or before the book's `through` (notes only), the DFS hook returns
  None for it, and the hit tables count only games before the date.
- Factor matrix (F11): one row per market per rung held at weight 0 (its channels, compact, in `detail`), None-valued
  evidence keys dropped. On 2026-10-23's slate (12 games, 229 priced players, no Kalshi blocks): factor rows a player
  55.9 -> 37.0; factors 5.38 -> 4.16 MB; per-game piece median 0.99 -> 0.92 MB, max 1.62 -> 1.51 MB (the remaining
  bulk is the markets' lines and the per-player opponent profile).
- Provenance text names both seasons (O3 / F9); the comparables' and teammates-out evidence kind is "modelled" (F12);
  research's game day IS production's function (F10).
- Guards (F3, F13): 20 new, 65 in all. Measured by mutation: the Fable re-check's harness (47 real mutants + 1 control,
  patterns updated to the fixed code) caught 45 of the 47; the two that passed were a decorative `effect.mult` on
  shipped rows and a wrong combo / dk_fp `fit_multiplier` formula (re-check R2, R3).

## Re-check round (Fable, AGREE AFTER ONE FIX)

- R1 (the fix): the replay never takes in the owner's current Mountain-time day. `nba_matchup._entries` drops every
  game whose MT game day is today or later (`mt_today()`, clock.mt_date); the data store keeps ingesting. Before this,
  a nightly job that ran after the early tips were final (22:00 MT = ET midnight, or whenever the PC's 19-hour due
  landed) set the book's `through` to today, and the past-date refusal then blanked the board for the late games
  until midnight. Guard: the data store holds today's finished early game, the book stops at yesterday (28 games, not
  29) and the board for today prices the late game -- on the pre-R1 code it read through = today, 29 games, 0 priced
  and the refusal note.
- R2 / R3 (guards): a combo's `fit_multiplier` is pinned to its adjusted rate / sum(component rate / component
  multiplier) and dk_fp's to the DK-weighted linear rate over the same with steals / blocks / turnovers at 1, read back
  from the payload (aux rates rebuilt from `role.rate_*` and the aux coefficients); a shipped factor row's
  `effect.mult` is pinned to exp(coef x value). Both pass on the current code (nothing was wrong there) and kill their
  mutants.
- After R1-R3: 68 guards. The same harness plus an R1 mutant (the day filter removed) and an R2 mutant (dk_fp's
  multiplier forced to 1): all 49 real mutants caught; only the no-op control passes.
