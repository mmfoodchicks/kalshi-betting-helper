# Fable review: the NFL matchup data foundation

Reviewer: a fresh Fable instance with no stake in the build (the deliverable was built by a
different Fable instance). 2026-10-23, ~02:00-03:00 UTC. Protocol:
`research/reports/matchup_agreement_log.md`. The orchestrator's independent review
(`research/reviews/nfl_matchup_data_orchestrator.md`) was read only AFTER every check below had
run; §9 says where I agree and disagree with its O1-O4.

Under review: `nfl_matchup_data.py`, `nfl_matchup_profiles.py`, `research/nfl_matchup_dataset.py`,
`research/data/nfl_matchup/*` (8 files), `tests/guards_nfl_matchup_data.py`,
`research/reports/nfl_matchup_data.md`. Nothing in the repo was edited; this file is the only
write. All runs used my own copy of the builder's deep store
(`<scratchpad>/review_nflm/deep`, `VIGIL_NO_BG=1`) and the shared raw nflverse files read-only.
My scripts and their outputs are under `<scratchpad>/review_nflm/work/` (`raw_checks.py` and
`raw_results.json` -- my own parser over the raw CSVs, not the builder's code; `dataset_checks.py`;
`store_checks.py`; `leak_check.py`; `vocab.py`; `mutate.sh`).

## 0. Verdict in one paragraph

**AGREE AFTER FIXES.** The as-of machinery in `nfl_matchup_profiles` is correct and my independent
recomputation from the raw files reproduces the defense columns of the dataset to four decimals and
every reported measurement I re-derived (man persistence all seven pairs, split-half man r 0.868 /
k 44.7, target share r 0.855 / k 41.0, WR YPT r 0.148, FTN-vs-NGS agreement, the MIN blitz flag,
the Sleeper cross-check, build time and memory). But the dataset has one **blocker** the modelling
agent must know about today: for every player whose team changed inside the three-season profile
window (5,319 of 22,978 rows, 23%; 4,537 of the 19,100 played-and-projected rows) the vs-man /
vs-zone / per-shell **target rates divide all-team numerators by new-team denominators** --
1,055 rows read an impossible rate above 1.0 target per dropback, and `x_exp_tgt_per_db`, the
column that embodies the report's headline signal, exceeds the player's own overall rate by 1.5x
or more on 3,287 rows (Diontae Johnson, 2024 wk 10 at BAL: vs man 37.9, `x_exp_tgt_per_db` 25.2).
Five majors: the 2018-2022 participation files use a different ROUTE vocabulary than 2023+, so
66% of pre-2023 targeted routes fall into "other" and the route-mix columns are polluted in the
fit seasons (2023 rows: 30-55% "other"; 2024: 10-18%; 2025+: 0%); the dataset builder mutates the
tallies under a live Snapshot so the opponent-adjusted columns of a game read other games of the
SAME week (1,360 differing fields in 2024 week 10; proven, tiny in magnitude, confined to nine
`d_*_adj_*` columns and `x_pos_adj_ppr`); the profile applies the blitz k (90.7) to pressure,
sack, TTT and EPA-per-dropback allowed (measured 437-1,040) and the personnel k to box shares,
the player k (100.7) to aDOT (measured 4.9), contrary to the report; `refresh()` never
re-downloads an existing raw file, so the proposed daily job is a no-op after its first run; and
the `q_*` columns are labelled "the quarterback expected before the game" while the QB1 is chosen
from Sleeper's post-week-revised projection (the orchestrator's O1 -- I agree). None of the
blockers or majors is leakage of the target game's own outcome into a feature, and none touches
the headline measurements, which stand.

## 1. What I ran (independent evidence)

| check | how | result |
|---|---|---|
| guards standalone | `python3 tests/guards_nfl_matchup_data.py` | 97 passed, exit 0, 0.09 s |
| pyflakes | all four code files | clean |
| stdlib-only import | import both production modules under `VIGIL_NO_BG=1` | numpy / pandas absent from `sys.modules` |
| raw recomputation | my own csv parser over pbp + participation + FTN 2018-2025 (`raw_checks.py`) | see §3-§5 |
| as-of spot check | 2024 week 10: d_man / d_blitz / d_WR_ypt for all 28 defenses and p_tgt_share for 299 player-teams recomputed from raw with weights 1 / 0.5 / 0.25 and strictly weeks < 10 | 28/28, 28/28, 28/28 exact; 272/299 exact, 27 explained (§3.3) |
| within-week leak | reproduce the builder's loop for 2024 week 10 against a frozen snapshot (`leak_check.py`) | 1,360 differing fields, all `fantasy_allowed.*.opp_adjusted.*` |
| vocabulary | distinct `route`, `offense_formation`, `defense_coverage_type` per season from raw | 2018-22 and 2023+ differ (§4.2) |
| guard mutation | 28 single-line bugs applied to copies, guard rerun (`mutate.sh`) | 22 caught, 6 not (§8) |
| build cost | `build_season(2023)` on raw files | 3.4 s, 71.6 MB peak RSS, 3.77 MB pickle |
| snapshot cost | `P.build(stores, 2025, 10)` with three seasons loaded | 1.21 s; defense 69 ms; player 2 ms; **122 MB RSS** |
| Sleeper revision | `research/data/feeds/proj_2026_1.json` (pre-kickoff) vs the post-week fetch | every post row `updated_at` 2026-09-15 04:00:13 UTC; QB pass_yd differs 28/32 (mean 16.8, max 49.0) |
| dataset structure | header == `schema()`, 321 columns, 22,978 rows, counts per season / position / `played` / `in_sleeper` / `snaps_source` / `q_source` | all equal the report and `dataset_meta.json` |

## 2. Leakage (the first question)

**2.1 The as-of mechanism in `nfl_matchup_profiles` -- AGREE, sound.** `season_tallies(max_week=w)`
keeps `week < w` (l.541); priors are whole seasons at `DECAY ** (seasons back)` with `BACK = 2`
(l.1164-1174); the constructor refuses tallies holding `(s, wk)` with `s > season` or
`wk >= week` (l.634-637). In the dataset, `p_games_cur` equals the number of earlier weeks the
player appears for that team on all 22,978 rows and `d_games_cur` equals the opponent's earlier
games on all rows (my `dataset_checks.py`); week-1 2023 rows have `p_games_cur = d_games_cur = 0`
and `d_games` 12.25-12.75 (prior seasons only); 2026 `d_games_cur` max 5. `p_rec_games` and
`d_rec_games` never exceed 4.

**2.2 Spot check against raw data -- AGREE.** For 2024 week 10 I recomputed from the raw files,
with my own definitions of dropback / attempt / target and weights 1.0 (2024 weeks 1-9), 0.5
(2023) and 0.25 (2022): man share of man-or-zone-charted dropbacks faced, blitz share (FTN
`n_pass_rushers` >= 5 where positive, else NGS), yards per target allowed to players.csv WRs, and
each player's target share over the team's targets in games he was on the field for that team.
All 28 defenses match the dataset's `d_man`, `d_blitz` and `d_WR_ypt` to 4 decimals. Of 299
`p_tgt_share` values 272 match to 4 decimals; all 27 others are players with at least one game in
which their only snaps were kneel-downs or spikes (the on-field vector in `build_season`
l.524-545 counts every kept play including kneels, so the builder counts that game's team targets
in the denominator; e.g. 00-0038678 at NO, dataset 0.0427, mine 0.0585). A definitional quirk
(nit N3), not leakage: nothing from week 10 or later enters.

**2.3 Week boundaries.** Thursday, Sunday and Monday games share a `week`, and the stores list a
week's games alphabetically by `game_id` (away team), not by date -- in every week of every season
(`store_checks.py`: "weeks with games out of date order 18/18"). In `P.build` that does not matter
(the whole week is excluded). In the dataset builder it does -- see 2.4. Byes, week 18 and
international games are handled by the week number alone; no date logic exists to get wrong.

**2.4 Within-week leak in the dataset builder -- DISAGREE with "nothing from week >= w".**
`research/nfl_matchup_dataset.py` l.712 builds ONE `Snapshot(season, week, current, priors)` per
week, then at l.773 calls `P.add_game(current, ...)` after each game's rows are written. `current`
is the same object the live Snapshot reads, so the second game of the week sees the first, and the
constructor's leak check (run once, at l.634) never fires again. Proof: after the builder's loop
for 2024 week 10, `S.contains(2024, 10)` is True; against a frozen snapshot the profiles of the
week's 28 team-games differ in 1,360 fields -- every one in `fantasy_allowed.<pos>.opp_adjusted`
(max |diff| 1.14 passing yards, 1.0 receiving yards, 0.20 PPR). The path is
`_offense_avg_excluding` (profiles l.919-934): the defense's past opponents' "average elsewhere"
includes their week-w game when the file order has already processed it -- the Monday game
`2024_10_MIA_LA` is sixth of fourteen, before eight Sunday games. No other profile field differs
(team-own tallies see only the team's one game a week; the league prior is cached at week start).
Dataset columns affected: `d_{WR,TE,RB}_adj_{ppr,tgt,rec_yds}`, `d_RB_adj_rush_yds`,
`d_QB_adj_{pass_yds,ppr}`, `x_pos_adj_ppr`. The leaked games never include the row's own game and
carry no information about its outcome, so this is a contract breach of negligible predictive
consequence -- but it is exactly what the brief's rule 7 forbids and what the report (§7
"Leakage") says cannot happen. **Major (F3).** Fix: add the week's games to `current` only after
the whole week is written (then Thursday does not inform Sunday, which is the conservative
reading of "weeks < w"), and assert `not S.contains(season, week)` at the end of each week; add a
guard that runs the builder's own loop on the fixture against `P.build`.

**2.5 Opponent adjustment and the target game.** `r is exclude` (l.925) is an identity test on the
record object, which is the same object on both the "off" and "def" sides (l.527-528); the
mutation "include the game itself" is caught by the guard. The adjustment never uses the target
game. AGREE (apart from 2.4).

**2.6 Last-4 windows.** `recent = games[-RECENT:]` after sorting by (season, week); prior-season
games enter unweighted. Correct as "the last four games played"; a nit that the report's
"last four games" does not say they are unweighted (N6).

**2.7 QB selection (`q_source`).** For 2026, QB1 = the QB with the most Sleeper-projected pass
attempts (l.504-512); for 2023-2025 the `role` column of `player_weeks_2022_2025.csv.gz`, which
was fetched after the season. Both come from Sleeper's post-week snapshot (§6.2 of the report
proves the snapshot is re-saved Tuesday 04:00 UTC; my check of week 1 2026 confirms every
`updated_at` is 2026-09-15 04:00:13 UTC). What I could establish: for week 1 2026 the QB1 by
projected attempts is the SAME for all 32 teams in the pre-kickoff capture and the post-week
fetch; across 1,818 team-games 2023-2026 the Sleeper QB1 is the game's leading passer 1,768 times
(97.2%) and NOT the leader 50 times -- so revisions do not systematically rewrite the QB to the
in-game outcome; of 159 team-games where the QB1 differs from the previous game's leading passer,
149 match the new game's leader, which is what both legitimate pre-game news and post-hoc revision
would look like. **CANNOT VERIFY pre-kickoff; the label at report §6.4 ("the quarterback expected
before the game") overclaims. Major (F6), same substance as the orchestrator's O1.**

**2.8 Snaps, Sleeper fields, scores.** `sl_proj_*` are labelled "observed (not provably
pre-kickoff)" in the schema -- AGREE, and the only other column derived from them is
`sl_proj_ppr` (derived) and the QB identity (2.7). `sl_act_*` are box scores and labelled so.
Nothing else reads a Sleeper field. Final scores (`hs`/`as`), `wp` and `spread`/`total` sit in the
store but no profile or dataset column reads them (grep). `act_snaps` for 2026 comes from PFR by
normalised name + team + game -- an actual, not a feature. AGREE.

**2.9 2026.** Participation absent (confirmed: no file, store `matched.participation = 0`,
`onfield` empty); coverage columns come from 2025 x 0.5 and 2024 x 0.25 (`d_cov_n` 423-437 on 2026
rows; the report says "~455"); `p_tgt_per_db` blank on 731 of 2,253 rows. AGREE.

## 3. Correctness

**3.1 Joins -- AGREE.** My parser: every pass/run play of 2018-2025 (31,751 ... 32,813) has a
participation row keyed (`game_id`, `play_id`); FTN 100% in 2022-2025 on the kept plays (the
report's "34,160 of 34,254 in 2022" is on a slightly different universe; the store's `matched`
reads 34,136 / 34,136 for 2022 -- both say the FTN gap is ~0.3%, immaterial).

**3.2 Coverage and route blankness -- AGREE.** Coverage charted on 99.52-99.93% of attempts
2018-2025 (report "99.7-99.9%" for 2021-25: mine 0.9978 / 0.9984 / 0.9973 / 0.9988 / 0.9993), on
0% of sacks 2018-22 and 98.5-99.8% from 2023, on 6-20 runs a season (not literally never -- nit).
`route` non-blank on 99.8% (2018-22) / 98.6-99.4% (2023-25) of attempts WITH a receiver and on 0%
/ 52-55% of attempts without one -- so on a targeted attempt it is the target's route and the
report's description ("targeted receiver's route on targeted attempts, NGS 'primary receiver'") is
right. AGREE.

**3.3 Vocabulary -- DISAGREE with silence, major (F2).** The ROUTES tuple (`nfl_matchup_data.py`
l.103-105) is the 2023+ vocabulary (`QUICK OUT, HITCH/CURL, IN/DIG, SHALLOW CROSS/DRAG, DEEP OUT,
SWING, TEXAS/ANGLE, ...`). The 2018-2022 files use `HITCH, OUT, FLAT, CROSS, IN, ANGLE, GO,
SCREEN, SLANT, POST, CORNER, WHEEL`; six of those twelve are not in the tuple and fall to `OTHER`
(l.363). In the stores 66.3% (2021) and 66.1% (2022) of targeted routes read family "other"
against 0% from 2023; "quick" in 2021-22 is SLANT alone (6-7% vs 49-50% in 2023+), "intermediate"
does not exist. The dataset inherits it through the priors: the mean "other" share implied by
`p_route_quick/screen/int/vert` is 0.549 (2023 weeks 1-4), 0.396 (2023 w5-9), 0.304 (2023 w10+),
0.175 / 0.129 / 0.101 (2024), 0.000 (2025, 2026). `d_route_*_epa` and `x_route_epa` carry the
same break. The modelling agent fits on 2023-2024 and validates on 2025: a route feature is a
different variable in the fit split than in the validation split. The same break hits
`offense_formation`: 2018-22 `SHOTGUN / SINGLEBACK / EMPTY / I_FORM / PISTOL / JUMBO / WILDCAT`,
2023+ `SHOTGUN / UNDER CENTER / PISTOL` -- `UNDER CENTER` is not in FORMATION and reads OTHER on
9,247-11,035 snaps a season (profile `formation` block only; no dataset column). And 2018-2022
carry a coverage code `PREVENT` (70-91 dropbacks a season) that l.360 maps to BLOWN (the raw
files have no BLOWN before 2023: the store's 80 BLOWN plays of 2022 ARE the 79 PREVENT plays plus
one). The `unknown` dict that `build_season`'s docstring promises ("strings that fell into an OTHER
bucket, with counts", l.409-410) is created at l.416 and never written to, so none of this
surfaced. Fix: map the old route names to families (HITCH -> quick, FLAT -> quick/screen, OUT ->
quick (the 2023 split quick/deep out is not recoverable), CROSS -> quick, IN -> intermediate,
ANGLE -> intermediate) or treat pre-2023 routes as uncharted for the mixes; add `UNDER CENTER` and
`PREVENT` (as family "other" or its own code); populate `unknown` and guard that it is empty on
every season's vocabulary.

**3.4 Coverage families -- AGREE.** Single-high {C1, C3}, two-high {C2, C4, C6, 2-Man}, zero {C0},
other {C9, COMBO, BLOWN}: a defensible, standard reading (Cover 6 is quarter-quarter-half, a
two-high shell; 2-Man two deep). The guard pins the sets and the mutations C6 -> single-high and
2-Man -> other are caught.

**3.5 Personnel -- AGREE with one note.** Real defense strings are of two shapes: `4 DL, 2 LB, 5 DB`
(2018-2022) and `3 CB, 2 DE, 2 DT, 1 FS, 2 ILB, 1 SS` (2023+); `defense_groups` sums both
spellings (l.214-216). Offense strings `1 RB, 1 TE, 3 WR` (2018-22) vs `1 C, 2 G, 1 QB, 1 RB, 2 T,
1 TE, 3 WR` (2023+): the code reads RB/FB/HB/TE/WR either way; `n_ol` is only readable from 2023
(nit N5). Nickel = 5 DB, dime = 6+, base <= 4, heavy front = base: standard. The inferred nominal
front (4-3 vs 3-4 against 12/21/13/22 with <= 4 DB) is labelled "inferred" in the profile and
"derived" in the schema. AGREE.

**3.6 Box = 0 -- AGREE with the handling, DISAGREE with the sentence.** Report §2.1: "2024 and
2025 participation carry defenders_in_box = 0 on ~9,100 plays a season". The 2024 FILE has 9,219
zeros, but on the kept offensive snaps the count is 23 (2024) and 52 (2025); the ~9,100 are
non-scrimmage rows that never reach the store. The 0 -> NA rule is right and harmless; the
sentence overstates what it protects against (nit N4).

**3.7 Denominators -- AGREE in design, one bug (F1, blocker).** Coverage shares are per
coverage-charted dropback, man/zone per man-or-zone-charted dropback, blitz per dropback with a
positive rusher count, targets per on-field dropback (`tgt_per_db`, labelled proxy): all as
described. The bug: `Snapshot.player(pid, team=)` (profiles l.1040-1079, l.1094-1099) restricts
the per-game lines (`pl[G_TGT]`, `pl[G_DB]`, `pl[G_MAN]`, `pl[G_ZONE]`, `pl[G_COV0+c]`) to games
for `team`, but the target counts by coverage come from `T.pl[pid]["rec"]` (l.1047), which is
keyed by player alone and holds every team and every season in the window. `tgt_per_db` divides
team-filtered by team-filtered and is right; `tgt_per_db_vs_man`, `tgt_per_db_vs_zone` and every
`per_coverage[c]["tgt_per_db"]` divide ALL-team numerators by NEW-team denominators. Dataset
effect (`dataset_checks.py`): 5,319 rows (23.1%) have `p_targets` above the team-filtered games'
targets (`p_tgt_pg x p_games`) -- the player had targets for another team within the window
(offseason moves as well as the 64 in-season trades); 1,055 rows have `p_tgt_per_db_man` or
`p_tgt_per_db_zone` > 1.0, 2,158 > 0.5; `x_exp_tgt_per_db` exceeds `p_tgt_per_db` by 1.5x or
more on 3,287 rows (on uncontaminated rows the two differ by more than 25% on 938 of 13,677);
`p_sh_tgt_per_db` > 1 on 763 rows and `x_exp_tgt_per_db_shell` > 1 on 791. Examples: Davante
Adams 2024 wk 8 at NYJ: `p_tgt_per_db` 0.23, vs man 3.75, vs zone 4.86, `x_exp_tgt_per_db` 4.36;
Diontae Johnson 2024 wk 10 at BAL: vs man 37.9, vs zone 15.5, `x_exp_tgt_per_db` 25.2; Jerry Jeudy
2024 wk 9 at CLE (moved in the offseason): 0.156 overall, 0.307 vs man, 0.441 vs zone. The
reliability measurement of the split (r 0.39) is NOT affected -- `player_metrics` calls
`S.player(pid)` with no team filter, so numerator and denominator agree there. Fix: compute the
three rates over the same game set as their denominators (either drop the team filter for them,
matching the measurement, or add per-game target-vs-man/zone/coverage slots to the player line);
guard it on a fixture with a traded player (the current fixture has none).

**3.8 Fantasy scoring -- AGREE.** `fantasy_points` (l.103-108) is the stated convention; the guard
hand-computes 13.7 / 16.7 / 19.7 and 26.0 and every scoring mutation I tried is caught. Scrambles
count as carries and rushing yards (as box scores do); kneels/spikes excluded, two-point tries
dropped, laterals credit the first man -- all stated in §3 / §6.5 and visible in the cross-check
(§5.4 below).

**3.9 Crosswalk -- AGREE.** On the real ids store: 6,476 skill players, 2,726 with Sleeper ids,
5,647 with ESPN ids; every skill player with 20+ passer/receiver/rusher mentions in 2023-2026 (625
by my count, 628 by the report's -- counting method) has a Sleeper id; 10 DynastyProcess
duplicates (report: ten); 105 normalised-name collisions, none between two players active in 2023
or later, so `resolve_name` is safe for current players (Mike Williams resolves by `last`, Josh
Allen the LB and Michael Carter the CB are excluded by position); FB folded into RB; Travis Hunter
WR from players.csv. The 32 played rows with a blank `sleeper_id` are fringe players (Rod
Williams, Terrell Bynum, Hunter Kampmoyer) with 0-2 touches. In the dataset 23,600 of 23,671
Sleeper rows mapped (99.7%). One player (2025 wk 1, 00-0038496) has rows in two games because
Sleeper listed him on NYJ while he played for NYG -- one row `played=0`, the other `played=1`,
no double-counting of actuals (nit N7).

## 4. Measurements

**4.1 Persistence (§5.1) -- AGREE.** My raw man-share persistence across 32 teams: 0.618 / 0.737 /
0.516 / 0.390 / 0.392 / 0.470 / 0.452 for 2018->19 ... 2024->25 -- identical to `persistence.json`
(the report's table rounds to 0.62 / 0.74 / 0.52 / 0.39 / 0.39 / 0.47 / 0.45). League man share
by season (mine, pooled over plays): 0.350 / 0.372 / 0.344 / 0.303 / 0.288 / 0.412 / 0.483 /
0.308 -- the report's 0.30 / 0.29 / 0.41 / 0.48 / 0.31 for 2021-25 and its "level jumps" reading
hold; Cover 1 share 2023 0.321 and 2025 0.224, Cover 3 2025 0.278 (report 0.32 / 0.22 / 0.28).
The pooled tendency r 0.485 is the mean of the ten named metrics (0.511, 0.538, 0.498, 0.557,
0.496, 0.551, 0.479, 0.375, 0.442, 0.407 -> 0.485) and the pre-written rule gives DECAY 0.5. The
rule text is in the script header and the report; the sha256 quoted (b7832074...) is of the file
as it stands now, which already contains the post-run change to the scheme-change rule (§4.4), so
the hash pins the final file, not the pre-run one (nit N8; the report is candid about the change).

**4.2 Split-half reliability (§5.2) -- AGREE.** Mine, pooled 2018-2025, odd vs even weeks:
defense man share r 0.868, n 293.1, k 44.7 (builder 0.868 / 293.1 / 44.7); player target share
(15+ targets each half) r 0.855, n 241.2, k 41.0 (builder identical, 1,446 player-seasons both);
WR YPT allowed r 0.148, k 918 (builder 0.148 / 914 -- the n-bar differs by 0.1%). Per season the
three match `reliability.json`'s `by_season` to the third decimal (e.g. 2024 WR YPT r -0.273,
"none"). The Spearman-Brown algebra is right: with r the correlation of two halves of n units,
k = n(1 - r)/r makes n/(n + k) = r, and a full season is 2n/(2n + k). The table's
"reliability of a full season" column follows. The k block is the mean of the named signals' k
(e.g. def_allow_target = mean(914, 777, 916, 855) = 865.5 with TE YPT and man EPA "none" and
excluded) -- a pooled kind, which the report says.

**4.3 Headline ("who gets the ball against what, not per-target efficiency vs coverage") --
AGREE as a reliability statement, with two caveats.** The evidence is the table: target-rate
splits (vs man minus vs zone r 0.39; under Cover 1 r 0.55) are measurable, efficiency splits
(YPT vs man minus vs zone r 0.02, EPA -0.01; defense EPA/target in man -0.007) are not, and I
reproduced the analogous defense and player figures independently. Caveats: (i) reliability is
not predictive validity -- the claim that this split helps a projection is for the modelling
agent's pre-registered test; the report phrases it as "the matchup signal that exists in these
files", which is fair; (ii) the dataset column that carries the signal is broken for 23% of rows
(F1), so any early model result using `x_exp_tgt_per_db` is not evidence either way until the
rebuild.

**4.4 2026 scheme flags (§5.4) -- AGREE on the numbers, DISAGREE on the header.** From raw FTN,
MIN blitz 2026 weeks 1-6 0.7039 (n 179) vs 2025 0.4599 (n 524), WAS 0.4571 vs 0.2662, ATL 0.2604
vs 0.3520, league mean +0.0495 -- all equal the JSON. The centred rule is disclosed in the report
and in the JSON's `note`, but the script's header docstring (l.34-36) still states the original
uncentred rule while the code (l.414-430) implements the centred one (nit N8).

**4.5 Charting agreement (§2.1) -- AGREE.** From raw: 2023 box equal 33,747 / 33,758 = 0.9997,
rushers 0.9999; 2022 box 0.5895, rushers 0.7693 (JSON 0.5882 / 0.7693 on a slightly different
play universe).

**4.6 Sleeper cross-check (§4.3) -- AGREE.** From the dataset: receptions exact 1.0000, targets
0.9996, receiving yards 0.9921, carries 0.8889 (pbp lower by 0.188), passing yards 0.9984, PPR
0.8830 (mean -0.121), 19,846 rows -- all as the JSON and the report.

**4.7 Sleeper revision (§6.2) -- AGREE on substance, CANNOT VERIFY exact counts.** Post-week rows
all carry `updated_at` 2026-09-15 04:00 UTC (the Tuesday after week 1); the pre-kickoff capture has
291 distinct timestamps from 2026-09-12. QB passing yards differ on 28 of 32 (mean |diff| 16.8,
max 49.0; report 14.7 / 49.0), no team's QB1-by-attempts changes, 0 skill players present in one
file and not the other. The report's "96 of 249 players' PPR" and "50 of 217 receiving-yard lines"
use a universe it does not define (mine: 153 of 393 and 105 of 365); the maxima agree (17.67).

## 5. Labels

- `tgt_per_db` and its man/zone/shell variants: `kind: proxy` in the profile and the schema --
  AGREE (the guard catches an unlabelled alignment proxy; the mutation test confirms).
- `alignment_proxy` / `p_align_mid_proxy`: labelled proxy, never called alignment -- AGREE.
- `route`: described correctly (§3.2) -- AGREE; the vocabulary break is not disclosed (F2).
- `sl_proj_*`: "observed (not provably pre-kickoff)" -- AGREE. `q_*`: "the quarterback expected
  before the game" -- overclaims (F6).
- `*_eb`: "modelled (EB)" with raw and n beside -- AGREE for the dataset; the profile's `k` stamp
  on each rate is honest about what was applied, but the report's §5.2 sentence ("the profiles
  read these k ... def_pressure 437, def_box 165 ...") is not what the code does (F4).
- Licence: `CREDIT` in every store, `license` in every JSON (FTN Data via nflverse, CC BY-SA
  4.0; share-alike on derived tables) -- AGREE. The CSV itself has no credit line; its
  `dataset_meta.json` does (nit N9).
- `d_base_43`: "derived" (inferred front) -- AGREE.

## 6. Engineering

- **stdlib only** on the server path: `nfl_matchup_data` imports `deep_cache`, `errlog`,
  `kalshi_nfl` (-> `kalshi`, `urllib`) and `clock`; no numpy reaches `sys.modules`. AGREE.
- **Silent excepts**: `fetch` and `refresh` swallow `OSError` only on `os.remove` of a temp file
  (fine); `k_for` falls back to the defaults on a missing or unreadable `reliability.json` and
  records `k_source = "default"` on every profile -- visible in the data, so acceptable, but the
  suite's rule is about `except Exception: pass` and this is `except (OSError, ValueError): pass`
  with no `errlog.note`; a corrupt JSON would be silent (minor M3). `errlog` IDs `NFLM-fetch`,
  `NFLM-refresh-ids`, `NFLM-refresh` are grep-able.
- **`refresh()` never refreshes (F5, major).** `_fresh(path, max_age_s=None)` returns True for any
  existing non-empty file (l.784-791), `refresh()` defaults `max_age_s=None` (l.703), and the
  report's §8 snippet calls `refresh(seasons=[current])` with no `max_age_s`. So the proposed daily
  job downloads the current season once and then rebuilds the same stale file forever. The
  docstring's "Download what is newer than the store ... skipped unless its upstream Last-Modified
  moved" (l.704-707) is not implemented: `Last-Modified` is recorded (l.298) but never compared, and
  `store["sources"]` is never read back. Demonstrated: `D._fresh(<any existing file>, None) ->
  True`. Fix: default `max_age_s` to, say, 6 h for the current season, or compare a HEAD
  `Last-Modified` to `store["sources"][kind]["modified"]`. Also the docstring says `seasons`
  defaults to "the current season (and the previous one before mid-October)" while the code uses
  `[now_season]` only (nit N10).
- **Memory**: `build_season` 71.6 MB peak / 3.4 s on 2023 -- the report's 73 MB / 3.4 s hold.
  A three-season Snapshot sits at **122 MB RSS** in the building process, which the report does
  not state (it gives 1.5 s); the profiles must be built in the PC job or a niced child, never in a
  gunicorn worker (minor M4 -- a note for integration, not a defect in the modules, which are not
  wired).
- **Deep-store keys** `nfl_matchup_<season>` / `nfl_matchup_ids` with a `schema` gate; the §8
  `artifacts.SCHEMA 5 -> 6` note is right. `deep_cache.register(key, run, max_age, cadence)`,
  `request_pc`, `DEFERRED`, `HEAVY_BUILD` exist; the snippet's `pc_on()` does not -- the app's
  pattern is `_deep_pc_first("nfl_matchup")` (app.py l.1455-1467) (nit N11).
- **Dead columns**: FTN `starting_hash` (L/R/M/0) and `qb_location` (S/U/P/0) are parsed with `_i`
  (l.397) and read NA on every charted play (0 where FTN has "0"); two wasted bytes a play and a
  COLUMNS entry that claims a value (nit N12).
- **Guard hook**: `from tests.guards_nfl_matchup_data import run` works from the suite because
  `combo_audit_guards.py` inserts the repo root on `sys.path` (l.15) and `tests` resolves as a
  namespace package (no `__init__.py`), as `guards_nba_matchup` already does.

## 7. The PC job design -- AGREE in shape, blocked by F5

PC-first, `HEAVY_BUILD`, niced child, uploads through `/api/art/upload`, the server self-computes
when the PC is off: the right shape and the same one the repo's other deep jobs use. It cannot
work as written until F5 is fixed (the daily run would never see a new game).

## 8. Guards -- meaningful but with gaps

97 checks, hermetic (no store, no network, 0.09 s), fixture run through the real `build_season` and
profiles. I applied 28 single-line mutations to copies of the modules and reran the guard: 22
caught (including the cutoff off-by-one, the disabled Snapshot leak check, both family-map
changes, five scoring changes, the two-point filter, FB-as-back, shrink-toward-zero, decay as a
product, BACK 3, man counting zone, blitz at 4, nickel at 6, `tgt_per_db` over snaps, the
adjustment including the game itself or needing one other game, sack as attempt, snap counts with
postseason, the unlabelled proxy, the deep bucket). Not caught: box 0 kept as a real box; the
on-field skill filter removed (the fixture has only skill players on the field); `RECENT = 5`;
scramble yards not credited to the rusher; `resolve_name` ignoring the team (no collision in the
fixture); and a doubled default k (reasonable -- the JSON is the pin). Beyond mutations, the
guards never exercise `research/nfl_matchup_dataset.py`, which is where F3 lives, and the fixture
has no traded player (F1) and only the 2023+ vocabulary (F2). Minor M5 for the five gaps; the
guards that would have caught F1-F3 are part of those fixes.

## 9. The orchestrator's review (read after my checks)

- **O1 (q_* chosen from post-week-revised Sleeper projections; major):** AGREE on substance and
  severity, and my checks add evidence both ways (§2.7): the QB1 by projected attempts is identical
  pre- and post-week for all 32 teams in week 1 2026, and the Sleeper QB1 is NOT the game's
  leading passer in 50 of 1,818 team-games, so the revision does not simply write the in-game
  outcome back; but pre-kickoff status cannot be proven for 2023-2025, and the report's label
  overclaims. Same item as my F6.
- **O2 (k and DECAY measured on 2018-2025 including the 2025 validation season; minor):** AGREE,
  and I would add that `reliability.json`'s per-season blocks make a 2018-2024-only k trivial to
  produce if the model report wants a clean validation.
- **O3 (2026 is weeks 1-6):** AGREE (store and dataset both end at week 6; `TEST_MAX_WEEK` 7 will
  take week 7 on the next refresh).
- **O4 (headline supported by the reliability table):** AGREE as a statement about reliability,
  with the caveat in §4.3 that the dataset column embodying it is wrong on 23% of rows (F1), which
  the orchestrator did not find.
- **DISAGREE with the orchestrator's verdict ("AGREE TO SHIP") and with two of its "checked and
  AGREE" lines.** "The as-of mechanism ... Sound by construction" is true of `P.build` and false
  of the dataset builder, which mutates the tallies under a live Snapshot (§2.4, proven). "Opponent
  adjustment ... leave-one-out, as of the cutoff" is as-of in the profiles and not in the dataset
  (same proof). And F1 -- impossible target rates on 1,055 rows and an inflated headline
  interaction on 3,287 -- is a blocker the orchestrator's review does not mention. Neither review
  should be reconciled as "ship" until F1 is fixed and F2-F6 are either fixed or explicitly
  carried into the model's feature list as exclusions.

## 10. Findings

| id | severity | file:line | what | evidence | suggested fix |
|---|---|---|---|---|---|
| F1 | **blocker** | `nfl_matchup_profiles.py:1047,1078-1079,1094-1099`; dataset cols `p_tgt_per_db_man`, `p_tgt_per_db_zone`, `p_sh_tgt_per_db`, `p_th_tgt_per_db`, `x_exp_tgt_per_db`, `x_exp_tgt_per_db_shell` | vs-man / vs-zone / per-coverage target rates divide all-team, all-season target counts (`T.pl[pid]["rec"]`) by team-filtered on-field dropbacks (`pl[G_MAN]` etc.) when `player(pid, team=)` is called with a team, as the dataset does | 5,319 of 22,978 rows have targets from another team in the window; 1,055 rows read a rate > 1.0; `x_exp_tgt_per_db` > 1.5 x `p_tgt_per_db` on 3,287 rows; Adams NYJ 2024 wk 8 vs man 3.75 / `x_exp` 4.36; D. Johnson BAL 2024 wk 10 vs man 37.9 / `x_exp` 25.2 | compute the three rates over the same games as their denominators (drop the team filter for them, as the reliability measurement does, or add per-game target-vs-man/zone/coverage slots to the player line); rebuild the dataset; guard with a traded player in the fixture. Until then the modelling agent must not use these six columns (`p_tgt_per_db` itself is fine) |
| F2 | major | `nfl_matchup_data.py:103-112,360-365,416`; report §2.1/§6.3 | 2018-2022 route vocabulary (HITCH, OUT, FLAT, CROSS, IN, ANGLE) and formation vocabulary (SINGLEBACK, EMPTY, I_FORM, JUMBO, WILDCAT vs 2023+ UNDER CENTER) and coverage code PREVENT are not in the enumerations; routes fall to OTHER, UNDER CENTER to OTHER, PREVENT to BLOWN; `unknown` never populated so nothing reported it | stores: 66% of 2021-22 targeted routes "other" vs 0% from 2023; dataset `p_route_*` "other" share 0.55 / 0.40 / 0.30 in 2023 (w1-4 / 5-9 / 10+), 0.18-0.10 in 2024, 0.00 from 2025; 9,634 OTHER formations in 2024; 80 BLOWN in 2022 = 79 PREVENT | map the old names into the families (or treat pre-2023 routes as uncharted for the mixes), add UNDER CENTER and PREVENT, populate `unknown`, guard that every season's vocabulary decodes; disclose the break in the report. Modelling agent: do not fit `p_route_*`, `d_route_*_epa`, `x_route_epa` on 2023-2024 until rebuilt |
| F3 | major | `research/nfl_matchup_dataset.py:712,773`; `nfl_matchup_profiles.py:634-637,919-934` | one Snapshot per week over a `current` Tallies mutated after each game; the constructor's leak check runs once; `_offense_avg_excluding` then reads same-week games already processed (file order is alphabetical, Monday's MIA_LA before eight Sunday games) | 2024 wk 10: 1,360 fields differ from a frozen snapshot, all `fantasy_allowed.*.opp_adjusted.*`; max |diff| 1.14 pass yds / 0.20 PPR; `S.contains(2024, 10)` True after the loop | add the week's games to `current` after the whole week is written; assert `not S.contains(season, week)` per week; guard the builder's loop on the fixture against `P.build`. Affected columns: `d_{WR,TE,RB}_adj_*`, `d_RB_adj_rush_yds`, `d_QB_adj_*`, `x_pos_adj_ppr` |
| F4 | major | `nfl_matchup_profiles.py:825-833,799-804,1084,604,1010`; report §5.2 last paragraph | pressure, sack, scramble, TTT, EPA/dropback allowed shrunk with the blitz k (90.7; measured 437 / 621 / - / 535 / 1,040); box shares with the personnel k (48.1; measured 165); player aDOT and YAC/reception with the target k (100.7; measured 4.9 / 17.8); QB TTT with qb_rate (261.6; measured 74). `def_pressure` and `def_box` k are computed and never read | profile for BUF as of 2024 wk 10: `pressure.k` 90.7, `box.light.k` 48.1, player `adot.k` 100.7, QB `ttt.k` 261.6 | pass `k_for("def_pressure")` / `("def_box")` and per-metric k for adot / yac / qb ttt (or add `pl_adot`, `qb_ttt` kinds); dataset eb columns are unaffected (they use kc / kb / kt / def_allow_target / def_allow_game correctly) but the report's sentence is wrong as written |
| F5 | major | `nfl_matchup_data.py:703-719,732,754,784-791`; report §8 step 2 | `refresh()` treats any existing raw file as fresh (`max_age_s=None`); no Last-Modified comparison despite the docstring; the proposed daily job rebuilds the same stale download forever | `D._fresh(<existing file>, None) -> True`; `store["sources"]` never read back; `modified` never compared | default `max_age_s` for the current season (hours) or HEAD-compare `Last-Modified` to `store["sources"][kind]["modified"]`; fix the docstring (also `seasons` default) |
| F6 | major (labelling) | `research/nfl_matchup_dataset.py:504-512,740-757`; report §6.4 | `q_*` describe Sleeper's QB1 drawn from the post-week-revised projection (2026) or the post-season `player_weeks` role (2023-25), labelled "the quarterback expected before the game" | week 1 2026: QB1 identical pre/post for all 32 teams; QB1 == leading passer 1,768 / 1,818; 149 of 159 starter changes anticipated -- consistent with news or revision; cannot be proven pre-kickoff | relabel `q_source` / §6.4 as "Sleeper's QB1 as of the Tuesday snapshot, not provably pre-kickoff"; the model decides on the frozen-capture 2026 rows (orchestrator O1) |
| M1 | minor | `research/nfl_matchup_dataset.py:34-36` vs `:414-430`; report l.11-13 | header docstring states the uncentred scheme-change rule; code implements the centred one; the quoted sha256 is of the final file | hash recomputed = b7832074... on the current file | update the header; record pre-run hashes before running next time |
| M2 | minor | `nfl_matchup_profiles.py:512-520`; `nfl_matchup_data.py:524-545` | a game whose only snaps for a player were kneels/spikes adds its team targets to his target-share denominator | 27 of 299 target shares in 2024 wk 10 differ from a kneel-free recomputation (e.g. 0.0427 vs 0.0585) | skip KNEEL/SPIKE plays in the on-field tallies, or document |
| M3 | minor | `nfl_matchup_profiles.py:98-99` | `except (OSError, ValueError): pass` when `reliability.json` is missing or corrupt; `k_source` says "default" but no ledger note | code read | `errlog.note("NFLM-k-table", e)` |
| M4 | minor | report §2.2 | a three-season Snapshot holds ~122 MB RSS; the report gives only the 1.5 s | measured | state it; profiles belong in the PC job / a child, never a web worker |
| M5 | minor | `tests/guards_nfl_matchup_data.py` | uncaught mutations: box 0 -> NA rule, on-field skill filter, RECENT, scramble crediting, `resolve_name` team disambiguation; no coverage of the dataset builder, traded players or the pre-2023 vocabulary | mutation run (§8) | add a non-skill id to an on-field list, a name collision, a scramble by a non-QB, a box-0 row, a RECENT pin, and the F1/F2/F3 guards |
| N1 | nit | report §2.1 | "never on runs": 6-20 runs a season carry a coverage code | raw count | "all but a handful of runs" |
| N3 | nit | report §4 | `played` can be 1 with zero touches when the on-field vector has him (fine) -- say so | dataset | wording |
| N4 | nit | report §2.1 l.83-84 | "defenders_in_box = 0 on ~9,100 plays" are non-scrimmage rows; on kept snaps 23 (2024) / 52 (2025) | raw count | reword |
| N5 | nit | `nfl_matchup_data.py:228` | `n_ol` readable only from 2023 (older strings list skill positions only) | raw shapes | note in the docstring |
| N6 | nit | report §3 | last-4 windows are unweighted across seasons | code | say so |
| N7 | nit | dataset | one player with rows in two games of 2025 wk 1 (Sleeper's team != pbp's) | dataset | drop the Sleeper-only row when the player has a played row that week |
| N8 | nit | — | see M1 | | |
| N9 | nit | `dataset_2023_2026.csv.gz` | no credit line in the CSV itself (meta has it) | file | a `# FTN Data via nflverse (CC BY-SA 4.0)` first line, or leave as is and say the meta is the licence record |
| N10 | nit | `nfl_matchup_data.py:704-707,724-725` | docstring: `seasons` default "and the previous one before mid-October" -- code uses the current season only | code | fix one or the other |
| N11 | nit | report §8 step 2 | snippet calls `pc_on()`, which does not exist; the app's helper is `_deep_pc_first(key)` | app.py l.1455 | use `_deep_pc_first("nfl_matchup")` |
| N12 | nit | `nfl_matchup_data.py:147,397` | `hash` and `qbloc` are parsed with `_i` from L/R/M and S/U/P and read NA on every charted play | store counts | map the letters or drop the columns |

## 11. Verdict

**AGREE AFTER FIXES.**

Blockers (must be fixed and re-reviewed before the data foundation ships or any model figure
built on it is quoted):
- **F1** -- the vs-man / vs-zone / per-shell target rates and the two `x_exp_tgt_per_db*` columns
  for players whose team changed inside the window (23% of rows; impossible values on 1,055).

Majors to fix in the same pass (F2, F3, F4, F5) and one relabel (F6). Everything else in the
report that I could recompute, I reproduced: the joins, the blank rates, the man-share level
shift, every persistence pair, the split-half reliabilities and their k, the FTN-vs-NGS agreement,
the 2026 blitz flags, the Sleeper cross-check and the revision timestamps, the build cost, the
dataset's shape and counts, and the as-of boundary itself for every non-adjusted defense and
player column I tested. The headline measurement stands; the column meant to carry it does not
yet.

For the modelling agent, today: `p_tgt_per_db`, `p_tgt_share`, every `d_*` column except
`d_*_adj_*` and `d_route_*_epa`, every `o_*`, and the 2025+ route columns are clean as far as my
checks reach; do not use `p_tgt_per_db_man/zone`, `p_sh/th_tgt_per_db`, `x_exp_tgt_per_db`,
`x_exp_tgt_per_db_shell` (F1), the route columns on 2023-2024 (F2) or the opponent-adjusted
columns (F3) until the rebuild; treat `q_*` and `sl_proj_*` on 2023-2025 as not provably
pre-kickoff (F6 / O1).

Proposed row for `research/reports/matchup_agreement_log.md`:

| # | Deliverable / finding | Built by | Fable review | Orchestrator | Resolution | Ships? |
|---|---|---|---|---|---|---|
| 1 | NFL matchup data foundation (stores, profiles, dataset, measurements) | Fable | AGREE AFTER FIXES: blocker F1 (team-change denominators in the coverage target rates), majors F2-F6 (`research/reviews/nfl_matchup_data.md`) | AGREE TO SHIP with O1/O2 carried (`..._orchestrator.md`) | disagreement: orchestrator did not find F1-F5; needs the fixes and a re-check by both | **blocked** |

---

# RE-CHECK (2026-10-23, after the builder's fixes)

Same reviewer, same protocol. Re-checked against the code hashes the orchestrator quoted (all
four match: `nfl_matchup_data.py` 8511346956bb4ae2, `nfl_matchup_profiles.py` d2007503a6d85dd5,
`research/nfl_matchup_dataset.py` bad818e815e73be9, `tests/guards_nfl_matchup_data.py`
93441453715624d2; dataset sha256 766a60ed..., 22,825 rows x 338 columns; stores SCHEMA 2,
`artifacts.SCHEMA` 6). Every check below was run against the CODE PATH the dataset takes
(`build_dataset` -> `week_rows` -> `_row` -> `S.player(pid, team=team)` at
`research/nfl_matchup_dataset.py:980`), not the guard's fixture, and the raw-file recomputations
are my own code (`<scratchpad>/review_nflm/work/raw_f1.py`, `dataset_checks2.py`,
`leak_check2.py`, `profile_checks2.py`, `mutate2.sh`, `mutate3.sh`). The stores were read in
place from `scratchpad/deep_nfl_matchup` (loads only; nothing written there).

## R.1 Verdict

**AGREE TO SHIP.** F1 (blocker) and F2-F6 are fixed on the dataset's own code path and I
reproduced the fixed columns from the raw files; the within-week leak is gone on real data and
the builder now raises on it; k applied equals k measured for every shrunk quantity; the
vocabularies decode with an empty `unknown` on all nine seasons; the refresh compares
Last-Modified; the quarterback is labelled honestly and a pre-kickoff pick travels beside it.
What remains is minor (three guard tightenings and a labelling note on the new proxy), listed
in R.9; none of it changes a dataset value or blocks the modelling agent.

## R.2 Guards and hygiene

143 checks pass standalone (exit 0, 0.25 s); pyflakes clean on all four files. The suite hook
is in `tests/combo_audit_guards.py:23264` (`guards_nfl_matchup_data` in the matchup module
loop), `pc_worker.py:496` registers `nfl_matchup`, `artifacts.py:39-42` documents SCHEMA 6 --
the orchestrator's integration, noted here only so the "ships?" column has its wiring.

## R.3 (a) F1 / F7 -- the team-scoped same-plays construction: AGREE, fixed

Code path: the on-field vector now carries, per (game, player), his targets on the plays he
was on the field for -- all, vs man, vs zone, under each coverage code
(`nfl_matchup_data.py:187-191`, filled at `:651` and `:668-675`, a target counted only when
`pid == target` on that play); `add_game` copies them into the per-game line
(`nfl_matchup_profiles.py:541-545`); `player(pid, team=)` reads every per-dropback rate --
overall, vs man, vs zone, by shell, per coverage -- from the team-filtered `pl` sums
(`:1128-1134`, `:1158`), while receiving efficiency stays player-scoped and both scopes are
named in the profile (`:1119`, `:1142`). `_row` calls `S.player(pid, team=team)` (`:980`).

Evidence on the dataset:
- all seven rate columns (`p_tgt_per_db`, `_man`, `_zone`, `p_sh_tgt_per_db`,
  `p_th_tgt_per_db`, `x_exp_tgt_per_db`, `x_exp_tgt_per_db_shell`): **0 rows above 1.0**, max
  exactly 1.000 (a player targeted on his one dropback), 36-94 rows above 0.5 (small n);
- Davante Adams NYJ 2024 wk 8: 0.231 / 0.211 / 0.250, `x_exp` 0.232 (was 0.23 / 3.75 / 4.86 /
  4.36); wk 10: 0.271 / 0.286 / 0.250 on n 96; Diontae Johnson BAL wk 10: 0 on n 6 (he barely
  played; was 37.9); Chase Claypool CHI 2023 wk 1: 0.2417 / 0.25 / 0.2973 on n 60 with
  `n_current` 0 (the coordinator's 0.24 / 0.25 / 0.30); Jerry Jeudy CLE 2024 wk 9: 0.156 /
  0.156 / 0.162;
- the 5,287 rows whose receiving window holds another team's targets (by design: efficiency is
  the player's) now have `x_exp_tgt_per_db` above 1.5 x the overall rate on 32 of 4,644 (was
  3,287 of 5,319), all small n; `x_exp_tgt_per_db` arithmetic holds on 20,464 / 20,464 rows;
- **independent recomputation**: from the raw pbp + participation files, with my own
  definitions (kneels and spikes out, 2024 weeks 1-9 at 1.0, 2023 at 0.5, 2022 at 0.25,
  team-scoped on-field membership), the dataset's 2024 week 10 `p_tgt_per_db` matches on 337 /
  337 rows, `p_tgt_per_db_man` on 332 / 332, `p_tgt_per_db_zone` on 333 / 333, to four
  decimals, with no dataset value lacking a raw counterpart; `p_tgt_share` now matches my
  kneel-free recomputation on 299 / 299 (the 27 kneel-only mismatches of the first review are
  gone -- M2); `d_man`, `d_blitz`, `d_WR_ypt` unchanged, 28 / 28 each;
- on the stores, every on-field vector of 2023-2025 (19,691 player-games) has targets <=
  dropbacks overall, vs man, vs zone and under every code (0 violations).

F7: `n_current` is 0 on all 2,253 2026 rows and positive on 18,171 of the 18,262 2023-25 rows
that have current games and a denominator (the 91 are players whose current games had no
dropback on the field); 2026 `p_tgt_per_db` is flat by week (0.1442, 0.1401, 0.1424, 0.1424,
0.1413, 0.1430), so the prior-only rate no longer drifts as the season adds games; 731 2026
rows stay blank (no 2024-25 on-field record for the team) and `p_tgt_per_db_proxy` fills
1,521. Guards: the exact old numerator (`_agg(rec, k[1]==1)` over team-scoped dropbacks), the
per-coverage variant, the shell variant, a never-counted target and a dropped team filter are
all caught (`f1_traded_player`, 1-7 FAILs each).

## R.4 (b) F3 -- `week_rows` and its frozen Snapshot: AGREE, fixed

Code path: `week_rows` (`research/nfl_matchup_dataset.py:783-849`) builds each game's actuals
in a throwaway `Tallies` (`:804-805`), never touches the snapshot's tallies, and raises if the
snapshot holds the week (`:847-848`); `build_dataset` adds the week's games to `current` only
after the rows are written (`:907-908`). On real data (2024 week 10): `week_rows` over the
builder's live `current` (weeks < 10) and over a snapshot built from `season_tallies(max_week
= 10)` give **338 identical rows across all 338 columns (0 differing fields)** and
`S.contains(2024, 10)` is False afterwards; re-creating the old bug (six week-10 games fed into
`current` before `week_rows`) makes it **raise** "snapshot for 2024 week 10 holds the week's
own games". Guard: the live-feed revert is caught (RuntimeError recorded as a FAIL); removing
only the raise is not caught, because the guard pins the behaviour (rows identical with the
week's other game removed) rather than the assertion -- acceptable, R9 nit.

## R.5 (c) F2 -- the 2023+ route-mix restriction and its blanks: AGREE, fixed and disclosed

Code path: every 2016-2022 route name has its own code (`nfl_matchup_data.py:119-121`),
PREVENT its own coverage code in family "other" (`:97-98`, `:107`), UNDER CENTER a formation
code (`:135`), FTN hash / QB location decoded (`:138-141`); any string outside a vocabulary is
counted in `unknown` (`:429-484`); route families enter the tallies only for `season >=
ROUTE_ERA` = 2023 (`nfl_matchup_profiles.py:484-487`). Stores: `unknown` is `{}` on all nine
seasons; route OTHER 0 and formation OTHER 0 everywhere; PREVENT 95 / 85 / 72 / 82 / 80 and
BLOWN 0 in 2018-22 (the first build's "BLOWN" plays were these); QB location decodes S / U / P
from 2022. Profiles: a 2022-week-10 cutoff and a 2023-week-1 cutoff carry no route mix (n 0,
v None; KC's per-route allowances 0 targets), a 2024-week-1 cutoff carries 2023's. Dataset:
the implied "other" share of `p_route_*` is 0.000 in every season / week band (was 0.55 /
0.40 / 0.30 in 2023); all 404 rows of 2023 week 1 have no `p_route_*`, `d_route_*_epa` or
`x_route_epa`; 2023 week 2 has `d_route_*` on 398 / 398 and `p_route_*` on 235 / 398. The
report's §2.1 now states both vocabularies, the family shift a remap would introduce (quick
62% -> 50%, screen 9% -> 15%, intermediate 10% -> 17%, vertical 18.1% both sides -- the
reason for excluding rather than remapping, which I accept) and the blank rates. Guards: six
reverts caught (legacy names dropped, ROUTE_ERA 2016, `unknown` not recorded, PREVENT or
UNDER CENTER removed, mixes from every season).

## R.6 (d) F4 -- k applied = k measured: AGREE, fixed

For a 2024-week-10 snapshot I read the `k` stamped on twenty profile rates and compared each
with `reliability.json`'s `k` block: def_pressure 437.0, def_sack 621.1, def_ttt 535.1,
def_epa_db 1039.8, def_scramble 10849.2, def_blitz 90.7, def_box 165.3, def_pers 48.1,
def_cov_share 52.7, def_allow_target 865.5, def_allow_game 36.1, def_run 422.5, pl_target
100.7, pl_adot 4.9, pl_yac 17.8, pl_carry 171.0, qb_rate 261.6, qb_ttt 74.2, qb_deep 267.2,
qb_depth 460.5 -- **all twenty equal**; `k_source` names the measured table. `_K_DEFAULT`
(`nfl_matchup_profiles.py:78-85`) carries the same numbers for a missing table and a missing
or corrupt table is ledgered `NFLM-k-table` (`:112-114`; M3). `k_fit_only` (2018-2024) sits
beside the pooled table in `reliability.json` (O2) and differs little (def_cov_share 51.4,
def_allow_game 48.7, pl_target 96.9). Guards: four reverts caught (pressure with the blitz k,
aDOT with the target k, box with the personnel k, QB TTT with qb_rate).

## R.7 F5 -- refresh freshness: AGREE, fixed

`fetch` records `{url, modified, bytes, fetched}` in `<file>.meta.json` (`nfl_matchup_data.py:
403-404`); `_fresh(path, url, max_age_s)` (`:360-379`) treats a file with no record as stale,
asks upstream with one HEAD (`remote_modified`, `:324-334`, failures ledgered `NFLM-head` and
the file on disk kept) unless the file is younger than `max_age_s`, and compares
Last-Modified; `refresh` skips a season none of whose files changed when its store is at the
current SCHEMA (`:912-916`); `default_seasons` reads the previous season too until October
(`:836-840`). Guards: the exact old bug (any existing file fresh) is caught with 2 FAILs, as
are "Last-Modified not compared" and the `default_seasons` revert; the skip path has no guard
(efficiency only -- always rebuilding is correct, just slower; R9 nit). Note for operations:
the first run after deployment re-downloads every raw file once (no `.meta.json` yet) and a
daily run costs one HEAD per file (five for the current season plus ids).

## R.8 (e) F6 -- `qp_*` pre-kickoff by construction: AGREE

`q_source` reads `sleeper_qb1_tuesday` on 22,811 rows and `last_starter` on 14; `q_gsis` is
kind "derived (not provably pre-kickoff)". `qp_gsis` ("derived (pre-kickoff by construction)")
is the frozen capture's QB1 when the capture is dated strictly before the game's date
(`load_frozen_qb`, `:727-763`; the comparison at `:825`), else `last_starter(S, team)`, the
leading passer of the team's most recent game INSIDE the frozen snapshot (`:766-780`) -- by
R.4 that snapshot holds nothing from the week, so the pick is pre-kickoff by construction.
The two captures are committed files, not new fetches: `research/data/feeds/proj_2026_1.json`
(3,115 rows, 291 distinct `updated_at`, newest 2026-09-12 15:50 UTC) and `proj_2026_2.json`
(3,116 rows, newest 2026-09-19 11:30 UTC, a Saturday), both dated Oct 16 in the tree and in
git history. In the dataset `frozen_capture` appears on 713 rows, all 2026 week 1 games of
09-13 / 09-14 and week 2 games of 09-20 / 09-21; the 09-09 opener, Thursday 09-10 and
Thursday 09-17 read `last_starter`, as they must. Over 1,818 team-games the Tuesday QB1 is the
game's leading passer 1,768 times and the pre-kickoff pick 1,568: both right 1,564, only the
Tuesday pick right 204, only the pre-kickoff pick right 4, neither 46 -- so the Tuesday
snapshot anticipates 204 starter changes the last game does not, which is what both legitimate
pre-game news and post-hoc revision would produce. The report (§4) says so and leaves the
choice to the model; agreed, with the instruction that a shipping decision made on `q_*` must
be confirmed on `qp_*`.

## R.9 Remaining findings (none blocking)

| id | severity | file:line | what | evidence | suggested fix |
|---|---|---|---|---|---|
| R1 | minor | `tests/guards_nfl_matchup_data.py:184` (fixture play 14), `m5_mutations` "a lineman on the on-field list is not tallied" | the lineman (300001) and the kneel-only back (100005) are listed only on the KC kneel play, which the on-field build skips regardless of the skill filter -- the lineman check passes vacuously | reverting the skill filter (`if not pid:` at `nfl_matchup_data.py:653`) leaves all 143 checks green | list 300001 on a pass play too |
| R2 | minor | `f7_no_participation_season` | `n_current` is pinned only at 0 (no-participation season); `db_cur += 0` passes | mutation `db_cur += 0` not caught | assert `n_current` == the current-season on-field dropbacks on the base fixture (KC WR as of week 3: 4) |
| R3 | minor (labelling) | `research/nfl_matchup_dataset.py:922-940`, schema `p_tgt_per_db_proxy` | the proxy is an unshrunk ratio; 11 rows exceed 1.0, every one with `p_tgt_per_db_proxy_n` < 2 (0.33-1.88); 364 rows have n < 2, 3,511 n < 20 | the proxy agrees with the charted rate at r 0.948 on 11,447 2023-25 rows with both n >= 50 (means 0.145 vs 0.142), so it is a good stand-in where n is adequate | acceptable as labelled, provided the schema meaning adds "unshrunk; gate on `p_tgt_per_db_proxy_n` (>= 20 removes every value above 1) or shrink toward the league rate with pl_snap's k"; do not cap at 1.0 (it would hide the noise rather than remove it) |
| R4 | nit | `week_rows:847`, `refresh:912`, `k_for:112` | no guard that `week_rows` raises on a contaminated snapshot, that an unchanged season is skipped, or that a corrupt k table is ledgered | mutations not caught | one check each (monkeypatch `errlog.note` for the last) |
| R5 | nit | dataset | the route-mix denominator is not a column (`p_targets` counts every target including pre-era ones), so a 2023 week-2 `p_route_*` built on one week of targets looks like a 2025 week-10 one | 235 of 398 2023-wk-2 rows carry a route mix | add `p_route_n` (charted in-era targets) or say in the schema that `p_route_*` for 2023 weeks 2-5 rests on 1-4 weeks |
| R6 | note | `nfl_matchup_data.py:360-379` | first refresh after deployment re-downloads every file once; one HEAD per file per run | code read | expected; say so in §8 |

## R.10 Measurements after the rebuild

Regenerated `persistence.json` / `reliability.json` / `charting_agreement.json` /
`scheme_change_2026.json` / `sleeper_crosscheck.json`; the report's §5.2 lists what moved
(player targets per on-field dropback r 0.683 -> 0.684, k 91.0 -> 90.8; Cover-1 target rate r
0.546 -> 0.538; vertical-route share now on 2023-25 only, 544 player-seasons, r 0.676) and
says the rest held to the third decimal -- consistent with the k block I compared in R.6 and
with my first-review recomputations (man persistence, man / target-share / WR-YPT split-half),
which the fixes did not touch. `build_season(2023)` at SCHEMA 2: 5.5 s, 72 MB peak, 3.97 MB
pickle; a three-season snapshot with nine stores loaded 2.2 s, 124 MB RSS (the report now
states ~120 MB, M4).

## R.11 The orchestrator's O1-O4

O1 is answered by F6 (`q_*` relabelled "not provably pre-kickoff", `qp_*` pre-kickoff by
construction, both verified in R.8); O2 by `k_fit_only` (R.6); O3 is stated throughout (weeks
1-6; `d_cov_n` on 2026 rows 423.8-522.0, the report's "~455" corrected to "424-522"); O4
stands as a reliability statement and the column that carries it is now bounded and
independently reproduced (R.3). My first-review disagreement with the orchestrator's "sound by
construction" is resolved: the dataset builder is now as-of on its own path, proven on real
data.

Proposed update to the agreement-log row:

| # | Deliverable / finding | Built by | Fable review | Orchestrator | Resolution | Ships? |
|---|---|---|---|---|---|---|
| 1 | NFL matchup data foundation (stores, profiles, dataset, measurements) | Fable | first pass: AGREE AFTER FIXES (F1 blocker, F2-F6); re-check: **AGREE TO SHIP**, minors R1-R6 open (`research/reviews/nfl_matchup_data.md`) | AGREE TO SHIP with O1/O2 carried | fixes verified on the dataset's code path and against raw recomputation; R1-R3 to close in the next guard pass | **yes** |

---

# MINORS CLOSED (2026-10-23, confirmation pass)

Hashes checked: `nfl_matchup_data.py` 8511346956bb4ae2 and `nfl_matchup_profiles.py`
d2007503a6d85dd5 unchanged since the re-check; `research/nfl_matchup_dataset.py`
fae01351c4fde90c; `tests/guards_nfl_matchup_data.py` 03bee43c5b95baa5; dataset 766a60ed...
UNCHANGED; `dataset_schema.json` 400bdeed8af0d97c.

**Text-only where claimed -- AGREE.** Diffed against the pre-fix copy my mutation harness kept
(hash bad818e815e73be9): `research/nfl_matchup_dataset.py` changes in exactly two places, the
`p_tgt_per_db_proxy` meaning string (l.605: "unshrunk: gate on p_tgt_per_db_proxy_n (>= 20
removes every value above 1) or shrink toward the league rate; do not cap") and the
`_proxy_rate` docstring (l.925-928). Loading the old and new modules side by side, `schema()`
has the same 338 names, kinds and sources, one meaning differs, and the committed
`dataset_schema.json` equals the new `schema()`. The dataset itself is untouched (hash), so
no number moved. R5 is stated in report §4.1 (l.349-354: 2023 weeks 2-5 route mixes rest on
1-4 weeks of in-era targets; `p_route_n` deliberately not added so the hash stands) and R6 in
§8 (l.729-730: the first refresh after deployment re-downloads every raw file once; one HEAD
per file per run). Both acceptable.

**New guards kill their reverts -- AGREE.** 149 / 149 pass (exit 0); pyflakes clean. Each
closed minor reverted on a copy of the tree (`<scratchpad>/review_nflm/work/mutate4.sh`):
- R1 skill filter removed (`if not pid:`) -> 1 FAIL ("a lineman on a pass play's on-field
  list is not tallied"; the lineman now rides the week-2 KC dropback, play 31, not only the
  kneel);
- R2 `db_cur += 0` -> 2 FAILs ("n_current counts this season's on-field dropbacks (4 ...)",
  "a prior season adds to n at its weight but never to n_current");
- R4a `week_rows` raise removed -> 1 FAIL ("week_rows raises when the snapshot holds the
  week's own games" -- the old bug re-created on the fixture);
- R4b refresh skip path removed -> 1 FAIL ("the second refresh skips it"; the guard runs
  `refresh` twice on a temp raw dir with `fetch` stubbed to raise and `remote_modified`
  pinned, so no network);
- R4c `NFLM-k-table` ledger removed -> 1 FAIL (`errlog.note` captured; defaults still used);
- control (a whitespace-only change) -> green.
The repo tree is byte-identical before and after the mutation runs (six-file sha list diffed).

**Verdict: AGREE TO SHIP, nothing open.** R1-R6 closed; F1-F7, M1-M5, N1-N12 and the
orchestrator's O1-O4 stand as verified in the RE-CHECK. Agreement-log row: Fable AGREE TO
SHIP (first pass AGREE AFTER FIXES -> re-check AGREE TO SHIP -> minors closed), Ships: yes.
