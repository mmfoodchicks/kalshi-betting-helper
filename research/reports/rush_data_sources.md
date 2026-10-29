# NFL rushing matchup: data sources for RB and QB rushing

Status: research only. No model was fitted, no validation or held-out split was scored, no shared file was touched and no git command was run. This report extends `research/reports/matchup_data_sources.md` (read it for the licence and terms reads on Sleeper, PFF, Kalshi's developer agreement and NBA sources; those reads stand and are not repeated here).

Written 2026-10-23. Downloads and Kalshi pulls ran at about 20:20-20:50 UTC on the container clock. Raw copies were kept in session scratch (not committed). The author's `rush_checks.py` (also in scratch) reproduces only some figures: the section 8.1 flag counts, section 8.2, the first section 9.2 table, the section 3 box agreement, the section 5 PFR yards and the section 6 NGS week-0 check. Every other figure is the author's one-off check or a reviewer's recount, as cited (the reviewers recomputed about 70 figures with their own code).

**Corrected 2026-10-24 after two independent reviews** (`research/reviews/rush_data_sources_sonnet.md`, `research/reviews/rush_data_sources_opus.md`; both AGREE AFTER FIXES, no blocker). The orchestrator made the corrections:
- the week-numbering alarm withdrawn;
- the Kalshi inventory completed with RB counts and the missed series;
- aborted snaps and two-point tries added to the coding;
- the contract text that settles kneels and two-point tries quoted (sacks rest on the settled brackets);
- the settlement test widened (sacks, 2026, attempts);
- the run-concept and front claims narrowed;
- the season-level PFR files listed;
- the licence section completed (the Sports Reference AI / machine-learning clause, NFL.com's terms, snap counts as PFR data, the pre-2023 participation credit);
- the UNVERIFIED list pruned.

Each correction cites the review that found it.

Verification labels: "checked" means I opened the file or page and the result is in this report. "UNVERIFIED" means I could not confirm it from a primary source; a label like "secondary" means only a search snippet or article supports it.

---

## 1. What the orchestrator needs first

1. **The open Kalshi rushing markets are week 7; there is one numbering.**
   - nflverse's 2026 schedule puts 22-26 Oct in week 7: NE at CHI on 22 Oct, 12 games on 25 Oct, DAL at PHI on 26 Oct. Byes: BUF, JAX, LAC, WAS.
   - Kalshi's own weekly series is `-26W7` and its rules say "Week 7".
   - Week 8 (29 Oct to 2 Nov) is simply not listed yet. A prospective window opens in the future by design.
   - The first version raised a numbering question here; it was a false alarm (Opus F1).
2. **Kalshi lists QB and RB rushing markets** (section 9 has the full table).
   - KXNFLRSHYDS: 149 open rungs for 24 QBs and 365 for 38 RBs across 13 events.
   - KXNFLRSHATT: 20 QB rungs and 66 RB rungs.
   - KXNFLLONGRSH: 17 QB and 31 RB.
   - KXNFLTD: 45 QB rungs (passing touchdowns do not count) and 133 RB.
   - KXNFLRRYDS (rushing plus receiving yards): no QB rungs, 78 RB.
   - Also open: game-level "most rushing yards" (KXNFLMOSTRSHYDS), season-long rushing yards and TDs (KXNFLSEASONRSHYDS, KXNFLSEASONRSHTD), first TD, and protected, ladder and escalator variants. None of these is priced by the model.
3. **Kneels and scrambles settle as rushing on Kalshi. Sacks do not. Aborted snaps count as attempts.**
   - **The contract text.** Kalshi's scalar contract (`FOOTBALLENTITYSCALARSTAT`) says yardage is "net of negative-yardage plays (including kneel-downs)". It says sack yardage is deducted from a club's net passing yards, not a player's. And "No rushing, passing, or receiving statistics are credited on an extra-point or two-point conversion attempt" (section 8.3).
   - **The binary contracts.** These say only "official attribution", so the settled brackets are the evidence there.
   - **QB yardage ladders, Oct-Dec 2025.** The bracket contains nflverse QB rushing yards including kneels and scrambles in 239 of 239 ladders with a rush play (253 of 253 counting QB-games with none). Netting sack yards into rushing breaks 111 of 253; the sacks-excluded total is right in all 111.
   - **2026 weeks 1-6.** The same holds on 178 of 178 (101 sack-discriminating).
   - **Attempts.** On 1,000 settled 2026 KXNFLRSHATT markets (291 ladders matched), counting kneels, scrambles and aborted snaps fits 291 of 291. Dropping aborted snaps falsifies 6.
   - This is still an inference from brackets, not a settlement record; section 9.2 says what it cannot prove (both reviews).
4. **Run concepts and fronts: no free LIVE or redistributable source, and two leads.**
   - The usable live proxies are pbp run direction and gap, the personnel labels and the box count.
   - **Run-concept lead.** The NFL Big Data Bowl 2025 `plays.csv` carries PFF-charted `pff_runConceptPrimary` / `Secondary`, `pff_runPassOption` and coverage fields. It covers part of one 2022 season (weeks UNVERIFIED), and its Kaggle terms were not readable here. It is a research lead only, not a production source (both reviews).
   - **Front lead.** nflverse `depth_charts` (2025 and 2026 files) list each team's nominal base defense as `pos_grp` "Base 3-4 D" or "Base 4-3 D". The latest 2026 snapshot has 19 teams 3-4 and 13 teams 4-3; 2 of 32 changed across 253 snapshots. It is ESPN's listed package, not the alignment on a play: a weak, labelled proxy at best (Sonnet F2). It exists for 2025-2026 only: the full 2024 file has no base label, and its `formation` holds only Defense, Offense or Special Teams (Opus re-check R6). So under "fit 2023-2024, choose on 2025" it can only be a display field, or one judged on 2025 onward.
5. **Four traps in the files.**
   - (a) NGS rushing has no QB rows. Its week-0 rows are FULL-SEASON totals (equal to nflverse regular-season carries for 50 of 51 players), while weekly rows exist only for games with 10+ attempts. So the weekly rows do not sum to week 0 (Opus m2).
   - (b) In nflverse pbp a QB scramble is coded `play_type = run` with `rush_attempt = 1`, so any "run" filter silently includes QBs.
   - (c) An aborted snap (a fumbled exchange, "FUMBLES (Aborted)") is coded `run` with `rush_attempt = 1` and the QB as rusher. That is 79 of the 705 2025 "QB designed runs".
   - (d) Two-point tries also carry `rush_attempt = 1` (10 of the 705), yet no rushing statistics are credited on them (both reviews).
6. **2026 inputs stop at week 6** for FTN charting and PFR advanced stats; snap counts have only a partial week 7. Participation has no 2026 file (HTTP 404) and will not until after the postseason.

---

## 2. Item 1: run concepts

| Source (checked) | Fields that bear on the run | What it supports | Verdict |
|---|---|---|---|
| nflverse pbp 2016-2026 (2025 header checked, 2026 file checked) | `run_location` (left / middle / right), `run_gap` (end / tackle / guard) | Where the ball went, which gap it went through | Available, free. Gap-direction proxy only |
| nflverse participation 2016-2025 (2023 and 2025 headers checked) | `offense_formation` (SHOTGUN 22,227; UNDER CENTER 12,145; PISTOL 1,704; blank 9,108 in 2025), `offense_personnel`, `offense_positions` | Formation and personnel | No concept field |
| FTN charting 2022-2026 (2025 and 2026 headers checked, identical 29 columns) | `is_motion`, `is_play_action`, `is_rpo`, `is_qb_sneak`, `qb_location`, `starting_hash`; no run-concept column | Motion and formation flags | No concept field |
| PFR advanced rush 2018-2026 | yards before and after contact, broken tackles | Contact-based run quality, not scheme | No concept field |
| NGS rushing 2016-2026 | efficiency, average time to line of scrimmage, percent of attempts with 8+ defenders | Run context | No concept field |
| NFL Big Data Bowl 2025 `plays.csv` (Kaggle) | `pff_runConceptPrimary`, `pff_runConceptSecondary`, `pff_runPassOption`, `pff_passCoverage`, `pff_manZone` (column names confirmed in public code; coverage part of 2022, terms UNVERIFIED) | PFF's run concept per play, for one partial historical season | Research lead only: not live, terms unread |
| Razzball zone-vs-gap tool | Team rushing allowed split by zone and gap; display only, no download | A display of concepts | Not a data source |
| Paid / cheap leads (names only, prices and terms not checked) | Fantasy Points (2021-24 concept splits), Reception Perception (RB man/gap vs zone charting), PFF (API terms restrict betting use, earlier report) | Concept splits | Not used |

Checked values in pbp 2025: `run_location` is blank on the 452 kneels and 128 non-kneel runs. `run_gap` is blank on all 3,907 middle runs. Top combinations (runs): right guard 1,906, left guard 1,847, right tackle 1,830, right end 1,779, left end 1,775, left tackle 1,721.

What the proxy measures: direction and gap, so "edge runs" = `run_gap = end` and "interior" = guard or tackle. What it does not measure: the blocking scheme. An inside zone and a power run through the same guard look identical here.

The participation fields `defense_man_zone_type` and `defense_coverage_type` are pass coverage. On designed runs they are filled on only 6 of 13,746 joined non-scramble runs. On SCRAMBLES (which are run plays in pbp) coverage is filled on 1,147 of 1,149 and `was_pressure` on all 1,149 (TRUE 426): exactly what the scramble-vs-coverage and scramble-vs-pressure splits need (Opus F5; the first version said "blank on every run play"). These fields exist for 2016-2025 only. 2026 has no participation file, so for 2026 what remains is FTN's `is_qb_out_of_pocket`, `n_blitzers`, `n_pass_rushers` and `read_thrown = SD`.

Zone versus gap: not coded in any free live file. The one free historical source is the Big Data Bowl 2025 file above.

---

## 3. Item 2: defensive fronts and alignment

| Element | Free field (checked) | What it measures | What it does NOT measure |
|---|---|---|---|
| Personnel grouping | `defense_personnel`, e.g. "3 CB, 2 DE, 2 DT, 1 FS, 1 MLB, 1 OLB, 1 SS" (participation 2016-2025) | Players on the field, counted by their listed position | Alignment, technique, or whether an OLB is a standing edge. The file has no alignment or technique column. 3-4 versus 4-3 cannot be read from these labels |
| Nominal base front | `depth_charts` `pos_grp` "Base 3-4 D" / "Base 4-3 D" (2025 and 2026 files; one label per team per snapshot) | The team's LISTED base package (ESPN) | What it lines up in on a play; sub-packages; an odd front played from a "4-3" listing |
| Box count | `defenders_in_box` (participation); `n_defense_box` (FTN) | Defenders charted in the box at the snap. Exact depth definition UNVERIFIED | Gap assignments, which lineman is in which gap |
| Pass rushers | `number_of_pass_rushers` (participation); `n_pass_rushers`, `n_blitzers` (FTN) | Rushers and blitzers on pass plays | Who rushed; stunts or spy |
| Pass coverage | `defense_coverage_type`, `defense_man_zone_type` (blank on 23,129 of 45,184 rows) | Pass coverage shell and man or zone | Run fronts |
| Over / under, even / odd, nose or shade | No field found | - | - |
| QB spy | No flag found | - | UNVERIFIED that no free source has one |
| Edge or alignment from tracking | None free and verified | - | - |

Agreement of the two box counts (checked, 2025): participation `defenders_in_box` equals FTN `n_defense_box` on 45,086 of 45,176 joined plays (99.8%). Pass-rusher counts agree on 45,173 (99.98%). This is NOT corroboration. From 2023 the participation file IS FTN's charting ("Participation data from 2023 onwards is courtesy of FTN", `load_participation`), so for 2025 it is one source counted twice (Opus m1). Both define the box as defenders "in the box at the snap" (nflreadr dictionaries); the depth is not defined. A box count of 0 marks non-scrimmage rows.

Label groups, 2025 participation (DE, DT, NT counted as DL; MLB, ILB, OLB, LB as LB; CB, FS, SS, S, DB as DB), most frequent: (DL, LB, DB) = (2,4,5) 9,244 plays; (4,2,5) 8,432; (3,4,4) 5,705; (4,3,4) 5,110; (3,3,5) 3,961.

Usable proxy: the DL-label count, labelled "personnel group by listed position". It is not a front. A 4-3 team's OLB who rushes from the line carries an LB label, and a 3-4 team's DE carries a DE label. The proxy is unusable for scheme identification without outside alignment data.

---

## 4. Item 3: offensive line run blocking

| Metric | Computable from free pbp? | Definition status | Notes |
|---|---|---|---|
| Line yards (Football Outsiders method) | Approximately, yes. Needs `yards_gained` and the run direction fields plus a credit schedule | Secondary only. Football Outsiders page returned HTTP 404 from this sandbox. Secondary summaries disagree: one gives losses 120%, 0-4 yards 100%, 5-10 yards 50%, 11+ yards 0%; another gives full line credit through 3 yards, half for 4-8, and all beyond 8 to the runner | UNVERIFIED weights |
| Adjusted line yards | No. Needs FO's opponent and situation regression, whose coefficients were not reached | UNVERIFIED | Not reproducible from this data |
| Stuff rate | Yes. Share of designed RB runs with `yards_gained` <= 0 (exclude scrambles and kneels) | Secondary definition | - |
| Power success | Approximately. Third or fourth down, distance 2 or less, and `first_down_rush` or `rush_touchdown`; goal-to-go from the 2 or closer | Secondary definition | UNVERIFIED that it matches FO's |
| Second-level and open-field yards | No from pbp | - | Closest free measure: PFR yards before and after contact (section 5) |
| OL availability | Partly. `snap_counts` (2025 rows by label, checked by the orchestrator: OL 1,457, T 1,363, G 925, C 668; PFR data, see section 12 for its terms), injuries and depth charts (section 7) | - | Who actually blocks is not in pbp |
| A published free table of ALY, power or stuff | Not found. FTN has no run-blocking field. FO page not reachable | - | UNVERIFIED whether FO publishes these free today |

---

## 5. Item 4: PFR advanced stats (nflverse `pfr_advstats`)

Verified by downloading headers and reading rows. Seasons from HTTP checks and the nflreadr page (which says data begins 2018, checked).

| File | Seasons checked | Rows 2025 / 2026 | Columns (checked) | Use |
|---|---|---|---|---|
| `advstats_week_rush_<Y>.csv` | 2018-2020 HTTP 200; 2023, 2024, 2025, 2026 headers read; 2026 weeks 1-6 | 2,355 / 736 | `carries`, `rushing_yards_before_contact` (+ `_avg`), `rushing_yards_after_contact` (+ `_avg`), `rushing_broken_tackles`, `receiving_broken_tackles` | Run quality; contact split |
| `advstats_week_def_<Y>.csv` | 2023, 2024, 2025, 2026 (headers or HTTP 200); 2026 weeks 1-6 | 7,926 / 2,668 | `def_targets`, `def_sacks`, `def_pressures`, `def_times_blitzed`, `def_times_hurried`, `def_times_hitqb`, `def_missed_tackles`, `def_missed_tackle_pct`, `def_tackles_combined`, `def_ints`, `def_yards_allowed`, `def_adot` and others | Defensive pressure and missed tackles per defender |
| `advstats_week_pass_<Y>.csv` | 2018-2026 (2018 HTTP 200 per the Opus review; 2025-26 headers read); 2026 weeks 1-6 | 684 / 213 | `passing_drops`, `passing_bad_throws`, `times_sacked`, `times_blitzed`, `times_hurried`, `times_hit`, `times_pressured` and rates | QB pressure. No scramble field in the WEEKLY file |
| `advstats_week_rec_<Y>.csv` | exists (earlier report) | - | receiving broken tackles, drops | Not read for rushing |
| Season-level `advstats_season_{rush,pass,def}.csv` (no year in the name) | HTTP 200; the pass file 2018-2025, the rush file 2018-2026 (176 rows of 2026). The first version tried a year-suffixed path, which is 404 | - | The pass file carries `scrambles` (correlation 0.9988 with pbp scrambles over the 47 QBs of 2025 with 100+ attempts), `rpo_rush_att`, `rpo_rush_yards` | Season totals: never usable inside a season (a cutoff would read the season's future). Prior-season check only (both reviews) |

Keys: `pfr_player_id` (for example `ShipWi00`), `pfr_player_name`, `team`, `opponent`, `game_id` (nflverse style, for example `2025_01_DAL_PHI`) and `pfr_game_id`. Joining to nflverse: `players.csv` carries `pfr_id` for 22,696 of 24,861 players. 2,353 of 2,355 PFR 2025 rush rows (99.9%) match a `pfr_id`.

Reconciliation (checked, 2025 regular season, 334 matched players): PFR before-contact plus after-contact yards equal nflverse season `rushing_yards` for 330 of 334 players, and carries equal for 332 of 334. The contact split is internally consistent.

Definitions: the PFR glossary returned HTTP 403 from this sandbox (a Cloudflare challenge), so the exact definitions of "yards before contact" and "broken tackle" are UNVERIFIED. Search results describe the concepts only.

Licence: see section 12. The Sports Reference terms bar using its content for machine-learning prediction, which governs how these fields may be used.

---

## 6. Item 5: NFL Next Gen Stats (nflverse `nextgen_stats`)

| File | Seasons and rows (checked) | Keys | Notes |
|---|---|---|---|
| `ngs_rushing.csv.gz` | 2016-2026, 6,290 rows. 2026 has weeks 0-7 (231 rows; week 7 has one row) | `player_gsis_id`, `player_display_name`, `team_abbr` | Positions RB, HB, FB only (0 QB rows). Week 0 is the full-season row, for 51 players with 92+ attempts. Weekly rows exist only for games with 10+ attempts |
| `ngs_passing.csv.gz` | 2016-2026, 6,161 rows, weeks 0-7 for 2026 | `player_gsis_id` | QB passing table; not used for rushing |

Columns of `ngs_rushing` (checked header): season, season_type, week, player_display_name, player_position, team_abbr, efficiency, percent_attempts_gte_eight_defenders, avg_time_to_los, rush_attempts, rush_yards, avg_rush_yards, rush_touchdowns, player_gsis_id, player_first_name, player_last_name, player_jersey_number, player_short_name, expected_rush_yards, rush_yards_over_expected, rush_yards_over_expected_per_att, rush_pct_over_expected.

Checked: 2025 week-0 rows equal the sum of the weekly rows for 3 of 51 players. The other 48 differ (for example Kareem Hunt: 163 attempts in week 0 against 103 in weekly rows, Chuba Hubbard 134 against 96).

The mechanism (Opus m2): week 0 equals nflverse regular-season carries for 50 of 51 players. The weekly rows omit every game under 10 attempts. Do not rebuild season totals by summing weeks, and read any weekly NGS figure as "in his 10+ carry games".

Definitions (nflreadr `dictionary_nextgen_stats`, reachable; both reviews):
- `efficiency`: "the total distance a player traveled on rushing plays as a ball carrier ... per rushing yards gained" (lower = more north-south);
- `percent_attempts_gte_eight_defenders`: "how often does a rusher see 8 or more defenders in the box";
- `avg_time_to_los`: the average time behind the line of scrimmage on his rushes;
- `expected_rush_yards`: from NGS's Big Data Bowl model.

Freshness: `ngs_rushing` and `ngs_passing` last modified 2026-10-23 12:54 GMT (HTTP headers). Week 7 is one row, so treat 2026 week 7 as not yet loaded.

---

## 7. Item 6: injuries, depth charts, snap counts

| File | Seasons and rows (checked) | Keys and fields | Notes |
|---|---|---|---|
| `injuries_<Y>.csv` | 2009-2026 (nflreadr; 2022-2024 HTTP 200). 2025: 6,068 rows (REG 5,783; POST 285). 2026: 2,124 rows, weeks 1-7 | `gsis_id`, `position`, `team`, `week`, `report_status`, `practice_status`, primary and secondary injuries | 2025 `report_status`: Out 1,382, Questionable 1,283, Doubtful 120, blank 3,283. `practice_status`: Full 3,024, Did Not Participate 1,620, Limited 1,379, blank 45. 2026 file modified 2026-10-23 12:34 GMT |
| `depth_charts_<Y>.csv` | 2001 onward (nflreadr); 2025 HTTP 200; 2026 checked: 689,308 rows, 253 snapshots from 2026-03-22 06:38 UTC to 2026-10-23 12:34 UTC | `dt`, `team`, `player_name`, `espn_id`, `gsis_id`, `pos_grp`, `pos_abb`, `pos_slot`, `pos_rank` | `pos_slot` is "a number assigned to each position in a formation" and `pos_rank` the depth within it (nflreadr dictionary). `pos_grp` on defense is the nominal base front ("Base 3-4 D" / "Base 4-3 D"). The pre-2025 files have a different layout |
| `snap_counts_<Y>.csv` | 2012 onward (nflreadr); 2026 weeks 1-6 complete (1,301-1,502 rows per week); week 7 partial (92 rows) | `pfr_player_id`, `position`, `team`, `offense_snaps`, `offense_pct`, `defense_snaps`, `defense_pct`, `st_snaps`, `st_pct` | PFR data ("provided by Pro Football Reference", nflreadr): Sports Reference terms apply (section 12). Keyed by PFR id, so join through `players.csv`. Team snaps are the most any player took (a 100% player exists in 543 of 544 2024 regular-season team-games); `snaps / pct` over-reads it |

---

## 8. Item 7: scrambles, designed runs, kneels, sacks, RPO

### 8.1 nflverse coding (2025 pbp, all season types unless stated; checked)

| Play | Flags | `play_type` | `rush_attempt` | `pass_attempt` | `qb_dropback` | Rusher id | Passer id | 2025 count |
|---|---|---|---|---|---|---|---|---|
| QB "designed" run (as first counted) | `qb_scramble` = 0 and QB is rusher | run | 1 | 0 | 0 | QB | none | 705 (regular season) = 616 true designed runs + 79 aborted snaps (`aborted_play` = 1, "FUMBLES (Aborted)", 76 of them for 0 yards) + 10 two-point tries |
| QB scramble | `qb_scramble` = 1 | run (1,149), no_play (72) | 1 on the 1,149 run rows (the 72 no_play rows: 0 or blank, no dropback, no rusher) | 0 | 1 (run rows) | QB (run rows) | none | 1,221; regular-season scramble runs 1,089, of which 2 by players not listed as QBs |
| QB kneel | `qb_kneel` = 1 | qb_kneel (452), no_play (1) | 1 | 0 | 0 | QB | none | 453; yards mostly -1 (385) and -2 (32) |
| QB spike | `qb_spike` = 1 | qb_spike (80), no_play (2) | 0 | 1 | 0 | none | QB | 82 |
| Sack | `sack` = 1 | pass | 0 | 1 | 1 | none | QB | 1,352 |
| RPO | No pbp column (no field containing "rpo" in the 2025 header). FTN `is_rpo` | run or pass | - | - | - | - | - | FTN TRUE 2,148 (run 1,657; pass 436; no_play 55) |
| QB sneak | No pbp flag. No play description contains "sneak" (0 matches). FTN `is_qb_sneak` | - | - | - | - | - | - | FTN TRUE 341; 262 on regular-season QB designed runs |

Regular-season QB rushing (checked): 1,792 QB run plays = 1,087 scrambles + 705 "designed", where the 705 hold the 79 aborted snaps and 10 two-point tries above.

### 8.2 Reconciliation with nflverse player stats (2025 regular season)

| Check | Result |
|---|---|
| nflverse `stats_player_week` QB carries and rushing yards (81 QBs) | 2,215 carries; 9,686 yards |
| pbp QB rush plays (run, scramble and kneel) | 2,225 carries; 9,683 yards |
| QBs whose carries and yards match exactly, kneels included | 71 of 81 |
| QBs matching exactly with kneels removed | 13 of 81 |
| Remaining 10 QBs (corrected, both reviews) | 10 two-point runs coded `rush_attempt = 1` with blank `rushing_yards`: the stats file puts them in `rushing_2pt_conversions`, not `carries`. Excluding `two_point_attempt` gives 2,215 carries and 80 of 81 QBs exact. The last one (Daniel Jones, week 7) is an aborted snap: pbp `rushing_yards` 0 against `yards_gained` 3, which the stats file credits |

So nflverse's player-level QB rushing includes kneels, scrambles and aborted snaps, and excludes two-point tries. Kalshi's contract text and settlement brackets agree (sections 8.3 and 9).

### 8.3 Rules status

Kalshi's scalar contract (`FOOTBALLENTITYSCALARSTAT`, held in scratch since the first pull; quoted by the Opus review, F3) states the NFL scoring convention:
> "Each statistic has the meaning, and is credited in the manner, prescribed by the NFL's official scoring rules for statisticians"
> "Yardage statistics are net of negative-yardage plays (including kneel-downs) and may be negative."
> "Sack yardage is deducted from a club's net passing yards and total net yards. It is not deducted from a player's passing yards, which are the player's individual gross passing yards."
> "No rushing, passing, or receiving statistics are credited on an extra-point or two-point conversion attempt."

What that settles:
- **Kneels are rushing (negative yards).** Kalshi's contract text for the scalar series. For the binary series it is the settled brackets: section 9.2 has 12 kneel-discriminating 2025 ladders and 14 in 2026, all right with kneels in. The NFL Statistics Manual itself was not reached.
- **Sacks are not rushing.** The contract text says only that sack yardage comes off the club's net passing and not off a player's passing yards; it never mentions rushing. The evidence that sacks are not netted into a QB's rushing is the brackets: netting them breaks 111 of 253 2025 ladders and 101 of 178 2026 ladders (Opus re-check R1).
- **Scrambles are rushing.** Brackets: 145 scramble-discriminating 2025 ladders and 119 in 2026, all right with scrambles in.
- **Two-point tries credit no rushing.** The same contract. nflverse sets `rush_attempt = 1` on them anyway (section 8.2).
- **Aborted snaps are rushing attempts.** Settled 2026 KXNFLRSHATT ladders: dropping them falsifies 6 of 291 (Sonnet review). Their yardage counts too: one 2026 ladder (Caleb Williams, MIN@CHI 2026-09-20, bracket 39.5 to 49.5) fits his 42 only with a 17-yard aborted-snap play included (Sonnet review); no 2025 ladder discriminates.
- **Stat corrections.** The two contracts differ (Sonnet re-check):
  - The binary contract (`FOOTBALLENTITYSTAT`) says "Official stat corrections made by the league after the game will not affect the value of the Contract".
  - The scalar contract (`FOOTBALLENTITYSCALARSTAT`) adds: "Changes to official statistics published before Expiration are included. Changes published after Expiration, including scoring changes issued following the post-game review by the NFL and its official statistician, are not accounted for". So a correction published between the final whistle and expiration counts there. (Both contracts also carry the generic "Revisions to the Underlying made after Expiration will not be accounted for", so that sentence is not the difference; Sonnet confirmation.)
  - nflverse may carry corrections published even later, so a rare gap between nflverse and the settled value is possible (Opus m3).
- **RPO.** An RPO that becomes a pass is `play_type = pass`; an RPO keep is a run with the QB as rusher. No free read-option flag exists. Proxy: a designed QB run with FTN `is_rpo` TRUE (74 of 705 regular-season designed runs in 2025; 12 of 1,087 scrambles). This flags an RPO design, not the outcome of the read, and is labelled a proxy. PFR's season file counts 310 QB RPO rush attempts in 2025, against FTN's 86 flagged QB runs (74 designed + 12 scrambles). The definitions differ and PFR's is UNVERIFIED; neither is a read-option outcome (Opus re-check R5).
- **Sneaks.** Only FTN `is_qb_sneak` (341 in 2025). Play descriptions in nflverse do not say "sneak".

### 8.4 Other FTN fields (values checked; meanings from the nflreadr FTN dictionary)

- `read_thrown` values: 0, 1, 2, SD, CHK, DES. SD appears on runs (1,022) and passes (1,419). CHK on passes (2,447). DES on passes (1,788).
  - The nflreadr FTN dictionary (reachable; both reviews) defines "0" = first read, "1" = second, "2" = third or later, "CHK" = checkdown, "DES" = designed read, and "SD" = "scramble drill (throw made after the play broke down)".
  - SD sits on 1,022 run plays, every one an nflverse scramble, and on no designed run (Opus re-check R2).
  - The rushing data layer (`research/reports/nfl_rush_data.md`) reads CHK as a checkdown; that is FTN's own definition.
  - `is_rpo` is defined only as "play is considered run-pass option".
- `n_defense_box` is "number of defenders in the box at the snap". It is 0 on about 24% of rows: the non-scrimmage rows.

---

## 9. Item 8: Kalshi NFL rushing and QB markets

Pulled 2026-10-23 with `status=open`, paginated. Series list from `/series?category=Sports` (4,046 series; 388 with NFL in the ticker). Names matched to `players.csv` display name and position (19 KXNFLRSHYDS rungs and 3 KXNFLRSHATT rungs did not match, for example "Aaron Jones Sr." and "Pierre Strong Jr."; those rungs are excluded from the QB counts).

RB counts and the series the first version missed are the Opus review's recount from the same pull, plus its own public-API pulls (F2). A QB rung is a rung whose normalised player name matches a `players.csv` QB. An RB rung matches RB, FB or HB.

That match is by NAME, and names collide in `players.csv`. Josh Allen (C / QB), Lamar Jackson (CB / QB), Aaron Jones Sr. (DE / RB) and Mike Washington Jr. (DB / RB) each match more than one position (Sonnet confirmation). On the season-long pulls, three counts of the same saved files disagree by a few rungs:
- the Opus review gives 4 QB / 99 RB and 19 QB / 181 RB;
- the Sonnet confirmation gives 105 RB and 188 RB;
- the orchestrator, counting any position the name matches, gives 8 QB / 107 RB and 26 QB / 195 RB.

So those two rows give ranges. Market and event counts agree across all three. (The board prices a per-game rung by normalised name within that game's own Kalshi event (`nfl_matchup._kalshi_lines`), so a league-wide name collision cannot cross games.)

| Series | Open markets | Events | QB rungs (QBs) | RB rungs (RBs) | Example ticker (checked) | Note |
|---|---|---|---|---|---|---|
| KXNFLRSHYDS | 534 | 13 | 149 (24) | 365 (38) | KXNFLRSHYDS-26OCT25LARLV-LARMSTAFFORD9-25 (Matthew Stafford 25+ rushing yards) | Threshold ladder. Binary per rung |
| KXNFLRSHYDSPROT | 534 | 13 | same players | same | KXNFLRSHYDSPROT-26OCT25KCSEA-SEAEWILSON23-80 (Emanuel Wilson 80+) | "Protected" variant (covered settlement, below). Not priced |
| KXNFLRSHATT | 89 | 12 | 20 | 66 (22) | KXNFLRSHATT-26OCT25INDMIN-MINKMURRAY1-9 (Kyler Murray 9+ attempts) | Rushing attempts ladder |
| KXNFLRRYDS | 81 | 12 | 0 | 78 (26) | KXNFLRRYDS-26OCT25DENARI-ARITALLGEIER22-90 | Rushing plus receiving yards combined |
| KXNFLRECYDS | 1,199 | 13 | 0 | 193 (30) | KXNFLRECYDS-26OCT25KCSEA-KCJROYALS11-70 | Receiving yards |
| KXNFLREC | 831 | 12 | 0 | 172 (28) | KXNFLREC-26OCT25KCSEA-KCTKELCE87-10 | Receptions |
| KXNFLTD | 576 | 13 | 45 | 133 (58) | KXNFLTD-26OCT26DALPHI-PHITMCKEE16-1 (Tanner McKee 1+) | Player touchdowns. The series' `product_metadata` text (not the rules PDF) says passing touchdowns do not count; settlement agrees (130 of 130, Sonnet review). The anytime touchdown market in practice |
| KXNFLTDPROT, KXNFLRECPROT | 576, 831 | | TDPROT 45 (24 QBs) | TDPROT 58 RBs, RECPROT 28 RBs | - | Protected variants; pulled but left out of the first version. Not priced |
| KXNFLRECYDSPROT | over 1,000 | | | 26 RBs on the first page | - | Protected. Not priced |
| KXNFLANYTD | 0 | 0 | - | - | - | Series exists (cites `FOOTBALLARCHIVED.pdf`); no open markets, as expected |
| KXNFLPASSYDS | 211 | 13 | 211 | - | KXNFLPASSYDS-26OCT26DALPHI-PHITMCKEE16-350 | Passing yards |
| KXNFLPASSATT | 66 | 12 | 66 | - | KXNFLPASSATT-26OCT25KCSEA-SEASDARNOLD14-36 | Passing attempts |
| KXNFLLADDERRSHYDS | 26 | 12 | 8 | 17 | KXNFLLADDERRSHYDS-26OCT25KCSEA-KCPMAHOMES15 | SCALAR payout, not rungs: "$0.0025 per rushing yard ... cap at 400 yards"; zero yards pay $0.00. Not priced |
| KXNFLESCALATORRSHYDS | 26 | 12 | 8 | 17 | same players | SCALAR: "floor(10000 x (y / 200)^3) / 10000; y = min(200, max(0, 10 x floor(s / 10)))"; negative totals pay $0.00. Not priced |
| KXNFLLADDERREC / RECYDS, KXNFLESCALATORREC / RECYDS | 58 each | | - | 17 each | - | Scalar receiving series. Not priced |
| KXNFLLONGRSH | 49 | 12 | 17 | 31 | KXNFLLONGRSH-26OCT25INDMIN-MINKMURRAY1-12 (Kyler Murray over 11.5) | Longest rush |
| KXNFLMOSTRSHYDS | 33 | 9 | 9 | 23 | - | Most rushing yards in a GAME (`FOOTBALLPLAYERCAT.pdf`, read by the Sonnet re-check: the same "official attribution" wording; strictly greater than every other member; ties pay 1/N; a non-participant resolves No; no kneel, sack or two-point text). Not priced |
| KXNFLWEEKMOSTRSHYDS | 20 | 1 (26W7) | 1 | 19 | KXNFLWEEKMOSTRSHYDS-26W7-MINKMURRAY1 | Most rushing yards across week 7 ("the most rushing yards across all Pro Football games in Week 7"); `FOOTBALLPLAYERCAT.pdf`. Not priced |
| KXNFLFIRSTTD | 337 | 13 | 24 | 68 | - | First touchdown scorer. Not priced |
| KXNFLFFPTSLADDER | 59 | 12 | 8 | 17 | - | Fantasy points ladder. Not priced |
| KXNFLSEASONRSHYDS | 116 | 4 | 4-8 | 99-107 | - | Season-long rushing yards (`FOOTBALLENTITYSTAT`). Not priced |
| KXNFLSEASONRSHTD | 224 | 6 | 19-26 | 181-195 | - | Season-long rushing TDs. Not priced |
| KXNFLPASSTDS, KXNFLPASSTDSPROT | 100 each | | QB | - | - | Passing TDs. Not priced (Sonnet re-check) |
| KXNFLPASSYDSPROT | 211 | | QB | - | - | Protected passing yards. Not priced |
| KXNFLFFPLAYERHIGH | 36 | | | | - | Fantasy high scorer. Not priced |
| KXNFLRSHYDSH2H, KXNFLNONQBPASS, KXNFL1HTD, KXNFL2HTD, KXNFL2TD, KXNFLRECYDSH2H | 0 | 0 | - | - | - | Exist, nothing open |

The model prices seven series (`nfl_rush.SERIES`): KXNFLRSHYDS, KXNFLRSHATT, KXNFLLONGRSH and KXNFLTD for RBs and QBs, plus KXNFLRRYDS, KXNFLREC and KXNFLRECYDS for RBs. Everything else above is not priced. KXNFLTD covers ALL touchdowns, D/ST selections included ("PHI Eagles D/ST"): 215 receiving-TD-only player-games settled YES at 1+. No per-game rushing-only touchdown series exists (Sonnet review and re-check; Opus re-check R4).

Week check (section 1, item 1): every open rushing and receiving event is dated 25 or 26 Oct, nflverse week 7. The 13 events cover 26 teams (CIN, BAL, CLE, TEN, DEN, ARI, GB, DET, IND, MIN, KC, SEA, LAR, LV, MIA, NYJ, NYG, HOU, PIT, NO, SF, ATL, TB, CAR, DAL, PHI). NE at CHI was played on 22 Oct; BUF, JAX, LAC and WAS are on bye (nflverse schedule; both reviews).

### 9.1 Contract terms (read this session)

- `FOOTBALLENTITYSTAT.pdf` (used by RSHYDS, RSHATT, RRYDS, TD, LONGRSH): the Underlying is "the official attribution of <stat> in <time period> of <game>", where <stat> "refers to an official football statistic as recorded in the league's box score and/or play-by-play for the relevant competition". Source agencies in order: the governing league, ESPN, Fox Sports, NFL.com, broadcaster. A player must take one play to settle on stats; a player who is active but never plays settles at the last fair price. A play nullified by penalty counts as participation. For stats credited to several players, the final box score governs.
- Product text for RSHYDS (the series' `product_metadata.important_info`): "Once a player takes at least one snap, even if nullified by penalty, the market settles based on yards recorded." For KXNFLTD: "Passing Touchdowns do not count for this market."
- `NFLENTITYSTATCOVERED.pdf` (the "Protected" variant): same underlying, source agencies NFL first. Covered settlement: a player who plays in the first half and not after it resolves to the last fair price before kickoff, unless the stat meets the payout criterion. It applies only to "above" or "at least" full-game contracts.
- `FOOTBALLENTITYSCALARSTAT.pdf` (ladders and escalators, which are SCALAR payouts, not rungs): "Above X" means strictly greater than X; "At least X" means X or more. It also states the NFL scoring convention quoted in section 8.3: kneels are net negative yardage, sack yardage is deducted from the club's net passing and not from a player's passing yards (the text never mentions rushing), and two-point tries credit no rushing. (The floor-strike reading, 19.5 meaning 20 or more, is how the BINARY rungs read: Opus review claim 43.)
- Corrections: the binary contract says "Official stat corrections made by the league after the game will not affect the value of the Contract". The scalar one says "Changes to official statistics published before Expiration are included. Changes published after Expiration, including scoring changes issued following the post-game review by the NFL and its official statistician, are not accounted for".

### 9.2 Settlement check (inference, not an official record)

Method: for each QB ladder in the settled KXNFLRSHYDS markets held in session scratch (never committed), the bracket runs from the highest YES floor strike to the lowest NO floor strike. A convention "fits" a ladder when the nflverse game total (two-point tries excluded) lies inside the bracket. The window is Oct-Dec 2025: the settled file starts with 25OCT, and the first version's regex dropped January-February 2026.

First version (QB-games with a rush play):

| Convention | Ladders consistent (of 239) | Discriminating cases | Consistent in discriminating cases |
|---|---|---|---|
| Kneels and scrambles included | 239 | 12 (kneels), 145 (scrambles) | all |
| Kneels excluded | 227 | 12 | 0 |
| Scrambles excluded | 94 | 145 | 0 |

Both reviews re-derived it with their own code and widened it:

Columns after "Kneels + scrambles in" count the ladders each alternative convention FALSIFIES.

| Period | QB ladders (incl. QB-games with no rush) | Two-sided / one-sided | Kneels + scrambles in | Kneels out | Scrambles out | Net of sack yards | Aborted-snap yards out |
|---|---|---|---|---|---|---|---|
| Oct-Dec 2025 | 253 | 124 / 129 | 253 fit | 12 falsified | 145 falsified | 111 falsified | 0 falsified |
| incl. Jan-Feb 2026 (Opus) | 290 | | 290 fit | 15 | 169 | 131 | |
| 2026 weeks 1-6 (Sonnet / Opus) | 178 | 118 / 60 | 178 fit | 14 | 119 | 101 | 1 falsified |

**Attempts** (Sonnet; the Opus re-check matched 304 and found the same falsifications). 1,000 settled 2026 KXNFLRSHATT markets give 291 ladders matched to a pbp rusher: 205 RB, 86 QB. Counting every rush attempt (kneels, scrambles, aborted snaps) fits 291 of 291. Dropping kneels fits 271, dropping aborted snaps 285 (6 falsified: 5 QB, 1 RB), dropping scrambles 234.

**What this can and cannot prove** (both reviews).
- It reads brackets, not values (`expiration_value` is empty). With 129 of 253 ladders one-sided, it constrains less than "239 of 239" suggests; the median bracket is 10 yards in 2025 and 5 in 2026.
- It covers QB yardage ladders and QB and RB attempt ladders. RB yardage ladders, laterals and fumbles are not tested, nor is longest rush.
- It cannot separate nflverse from the NFL's feed, which are the same data.
- Two-point tries cannot be told apart on yardage (3 ladders in 2025, 1 in 2026; both choices fit).
- It is a 2026 settlement-convention check on bracket containment only. No rate, residual or model figure was computed.

Treat it as strong evidence of the convention, not as a settlement record.

---

## 10. Item 9: other free or cheap sources

| Source | Status | Use for RB or QB rushing | Verified? |
|---|---|---|---|
| Sleeper projections and logs | Read in the earlier report: non-commercial terms, no archive | Projected attempts and carries | Earlier report only |
| nflverse `snap_counts`, `depth_charts`, `injuries` | Read this session (section 7) | RB role and usage, OL availability, committee order | Yes |
| nflverse `stats_player_week` | Read this session (2025 and 2026). Keys: `player_id` (gsis), `position`, `carries`, `rushing_yards`, `rushing_tds`, `fantasy_points_ppr` | Usage and rushing totals | Yes |
| NFL Big Data Bowl (Kaggle) | BDB 2025 `plays.csv` carries PFF's run concept, run-pass option, coverage and man/zone fields (column names confirmed in public code). It covers part of 2022 (weeks UNVERIFIED). Kaggle pages render by script, so the terms were not read. The ninth edition (announced 2026-10-08) "combines NFL Scouting Combine data with in-game player tracking data" (sportsvideo.org; the first version said Combine only, Opus m5) | Historical concept research only | Columns yes; terms and coverage UNVERIFIED |
| nflverse `advstats_season_*` | Season totals (pass file 2018-2025, rush file 2018-2026): scrambles, RPO rush attempts and yards | Scrambles: a prior-season check of the pbp count (r 0.9988 over the 47 QBs of 2025 with 100+ attempts). RPO: NOT a check of FTN's flag. Definitions differ and the counts disagree: 192 vs 72 on those 47 QBs (Sonnet), 310 vs 86 across all QBs (Opus) | Yes (both reviews) |
| FTN paid CSV or API | Earlier snippet: $599 CSV tier, $5,000 API tier, unverified | Could add run concepts if FTN publishes them | UNVERIFIED |
| PFF grades, Synergy, SIS | Paid. Earlier report: PFF API restricted for betting use | Would add run-blocking grades | Not used |

---

## 11. Coverage by season and week (as of the 2026-10-23 pull)

| Source | 2026 coverage | Note |
|---|---|---|
| nflverse play-by-play (remote 2026 file, checked) | Weeks 1-6 complete (16, 16, 16, 16, 15, 14 games). Week 7: 1 game (NE at CHI, 22 Oct) | Last game date in file 2026-10-22 |
| `stats_player_week` 2026 | Weeks 1-6 complete; week 7: 1 game | Modified 2026-10-23 14:36 GMT |
| FTN charting 2026 | Weeks 1-6, 15,904 rows | Modified 2026-10-22 03:12 GMT |
| Participation 2026 | None (HTTP 404) | nflverse loads it once after the postseason |
| PFR advanced 2026 (rush, def, pass) | Weeks 1-6: 736, 2,668 and 213 rows | Week 7 absent |
| NGS rushing 2026 | Weeks 1-6 (27-34 rows each); week 7 one row | Week 0 = season summary |
| NGS passing 2026 | Weeks 0-7 | - |
| Injuries 2026 | Weeks 1-7 (REG) | Modified 2026-10-23 12:34 GMT |
| Snap counts 2026 | Weeks 1-6 complete; week 7 partial (92 rows) | - |
| Depth charts 2026 | Snapshots to 2026-10-23 12:34 UTC | - |
| `players.csv` | Modified 2026-10-23 14:17 GMT | Crosswalk: 24,861 players; 22,696 with `pfr_id` |

2025 is complete for every source checked. FTN 2022-2025 and participation 2016-2025 are available as earlier reported.

---

## 12. Licences and terms

| Source | Licence or terms | How verified |
|---|---|---|
| nflverse release files (pbp, stats_player, injuries, depth charts, players) | Repository LICENSE is CC BY 4.0. nflreadr's home page says NFL data "belong to their respective owners, and are governed by their terms of use" | Earlier report (LICENSE text) and nflreadr home page |
| Participation | The whole file "is released under the CC-BY-SA 4.0" licence (`load_participation`). The credit differs by year: "FTN Data via nflverse" from 2023, "NFL NextGenStats via nflverse" for 2022 and earlier (the first version named only the FTN credit, Opus F8; the licence scope corrected per the Sonnet re-check) | nflreadr `load_participation` page |
| FTN-derived files (`ftn_charting`, participation from 2023) | CC BY-SA 4.0, credit "FTN Data via nflverse". Committed derived tables are adaptations (ShareAlike) | nflreadr `load_ftn_charting` page, fetched this session |
| PFR advanced stats AND snap counts (via nflverse; snap counts are "provided by Pro Football Reference", nflreadr) | Sports Reference Terms of Use, "Last Updated: May 19, 2023" (sports-reference.com/termsofuse.html; quoted by the Opus review, F8). Reuse with credit is welcomed. Automated access needs permission only "in a manner that adversely impacts site performance or access". Section 5 bars using Content "to create any database, archive, or other data store that competes with or constitutes a material substitute for the services or data stores offered on the Site". It ALSO bars using Content "for purposes of training, fine-tuning, prompting, or instructing artificial intelligence models or technologies in any manner, including without limitation for purposes of (i) generating answers, text, scores, statistics, notes, graphics, images, or any other output; or (ii) supporting machine learning methods used to predict, classify, label, or score inputs into the models" | Terms page fetched in full by the Opus review. Whether the clauses follow the nflverse-hosted copy is not settled by the text |
| NGS (NFL-owned) | NFL.com Terms and Conditions, "Updated: May 16, 2024" (nfl.com/legal/terms/; robots.txt does not disallow /legal/). Section 1.3: "You may use the Services solely for your own individual non-commercial and informational purposes only", and "Systematic retrieval of data or other content from the Services, whether to create or compile, directly or indirectly, a collection, compilation, database, or directory, is prohibited absent our express prior written consent". Elsewhere it prohibits "using the Services to gamble or wager on the result of any NFL game" | Fetched in full by the Opus review. Whether it follows the nflverse-hosted NGS and pbp copies is not settled by the text |
| Kalshi market data | Developer Agreement s.3.1: storage only for the member's own trading (earlier report). Data Terms s.I: no software development without consent (earlier report). Contract PDFs (section 9.1) | Earlier report plus contract PDFs this session |
| NFL Official Playing Rules 2026 | NFL-hosted PDF; no statistics content | Text search this session |
| NFL Big Data Bowl data | UNVERIFIED | - |
| Football Outsiders ALY and definitions | UNVERIFIED (page not reachable) | - |

Owner decisions open:
1. From the earlier report: whether Kalshi-derived data may be stored in a committed table (Developer Agreement s.3.1 and Data Terms s.I). The repo commits no raw Kalshi price.
2. **PFR-derived features in a predictive model** (the Sports Reference machine-learning clause above).
   - The orchestrator's DEFAULT until the owner decides: PFR advanced stats and PFR snap counts are DISPLAYED on the board with credit, and no fitted model feature reads them (`nfl_rush.py` marks them display-only).
   - A later use would be a new pre-registered group, judged prospectively.
   - The same clause also names "prompting, or instructing" AI models. The research agents themselves read PFR files, which falls under the same owner decision. The default does not cover it (Opus re-check R6).
3. **NFL-sourced data for wagering** (NFL.com terms).
   - The default also keeps NGS out of fitted features, but only as a conservative choice. It does not by itself answer the NFL.com wagering clause.
   - If that clause applies to nflverse's copies, it reaches showing NGS on a betting board as much as fitting it, and it reaches pbp and pre-2023 participation ("NFL NextGenStats via nflverse"), the foundation of the whole NFL stack already shipped.
   - NGS-sourced participation (2016-2022) is ALREADY a fitted source. The shipped first model fitted on it, and the rushing model's 2023 rows read 2021-2022 participation as their prior seasons. So the NGS exclusion covers the NGS rushing file, not NGS-charted participation (Sonnet re-check).
   - That is the owner's call (Opus re-check R6).

---

## 13. UNVERIFIED list

Corrected 2026-10-24. Items the reviews resolved are struck from the first version's list:
- kneels, sacks, scrambles, two-point tries and aborted snaps (section 8.3);
- the NGS, FTN, box and `pos_slot` definitions (sections 6, 8.4, 7);
- the cause of the 10-QB gap (section 8.2);
- the 2026 settlement and sacks (section 9.2);
- the week numbering and byes (section 1).

Still UNVERIFIED:
1. The NFL Official Statistics Manual itself (not reached; the convention rests on Kalshi's contract text and the brackets).
2. Football Outsiders' line-yard weights, power success and stuff-rate definitions, and whether FO still publishes them (403).
3. PFR's definitions of "yards before contact", "after contact" and "broken tackle" (glossary behind a Cloudflare challenge).
4. The box's depth definition (both dictionaries say only "in the box at the snap").
5. `is_rpo` beyond "play is considered run-pass option" (FTN and PFR disagree on which plays are RPOs; see the data layer's report).
6. Big Data Bowl 2025 coverage (which 2022 weeks) and its Kaggle terms.
7. Whether the Sports Reference and NFL.com clauses (section 12) reach the nflverse-hosted copies. This is an owner decision.
8. ~~`FOOTBALLPLAYERCAT.pdf`~~: read by the Sonnet re-check. It has the same "official attribution" wording, ties pay 1/N, and no kneel, sack or two-point text.

---

## 14. Method and limits

- Commands: header and row checks with `python3 -I` (no packages installed); HTTP probes with `curl -I -L`; downloads into session scratch; Kalshi `/series` and `/markets` pulled with paging (429 responses retried); settled-ladder test from the local settled-markets file (scratch, never committed).
- The 2026-10-24 corrections rest on the two reviews' own recounts. Their scripts and fetched pages are in session scratch (`review_rush_sources_sonnet/`, `review_rush_sources_opus/`).
- No validation or held-out split was scored. The 2025 checks are reconciliations of sources with each other and with settled market brackets; they are not model results. The 2026 files were read only for headers, week counts and game lists. No 2026 rate or residual was computed, and no 2026 weeks are used for any figure here.
- The remote 2026 play-by-play was downloaded to count games per week. The copy in scratch was older (weeks 1-6 only).
- WebFetch and WebSearch were used for documentation. Pages returned 403 or 404 are listed as UNVERIFIED.
- Earlier report facts for 2025 participation (columns, coverage, route) were checked against the file and hold.
