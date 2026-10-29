# Review of `research/reports/rush_data_sources.md` (reviewer: sonnet)

Reviewed 2026-10-23 (UTC clock of the container, about 21:00-23:00). I read the report and then checked its claims against the
raw nflverse files in scratch, Kalshi's public API and contract PDFs, nflreadr's pages, and the nflverse release URLs. My scripts
are in `$SP/review_rush_sources_sonnet/` (`load.py`, `ladder.py`; `$SP` is the session scratchpad). I wrote no other repo file and
ran no git command that writes.

## 1. Verdict

**AGREE AFTER FIXES.** Every number I recomputed from the raw files reproduced: the pbp coding counts, the stats-file
reconciliation, the PFR, NGS, FTN, participation, injuries, snap and depth-chart cells, the Kalshi series and rung counts, the
contract-term quotes, and the settlement result (239 of 239). No BLOCKER: nothing in the report would make a downstream model wrong
if it were read as written. There are five MAJOR findings, all about what the report says is absent or defines loosely:

1. The headline "no free source gives run concepts" is wrong as an absolute (§3, F1).
2. A free team-level 3-4 / 4-3 label exists in `depth_charts` and the report says none can be read (F2).
3. The "QB designed run" count of 705 contains 79 aborted snaps and 10 two-point tries; aborted snaps are never mentioned (F3).
4. The PFR season-level files were dismissed through a wrong URL and hold QB scrambles and RPO rush counts (F4).
5. The Kalshi series table leaves out a game-level most-rushing-yards market and two season-long rushing series (F5).

Nothing in the report tells anyone to commit raw Kalshi prices. Its only mention is the owner decision still open (§12 last line).

## 2. Claims table

AGREE = I reproduced it. DISAGREE = I got a different answer. CANNOT VERIFY = no primary source reachable.

### 2.1 Existence and fields

| # | Report claim | Result | Evidence |
|---|---|---|---|
| 1 | `pfr_advstats` weekly rush / def / pass exist, 2018 to 2026; columns as listed | AGREE | HEAD on `releases/download/pfr_advstats/advstats_week_{rush,pass,def}_2018.csv` all 200; 2017 is 404. Headers of the 2025 files read: rush has `carries`, `rushing_yards_before_contact(_avg)`, `rushing_yards_after_contact(_avg)`, `rushing_broken_tackles`, `receiving_broken_tackles`; def and pass columns match the report's lists. |
| 2 | Rows 2025 / 2026: rush 2,355 / 736; def 7,926 / 2,668; pass 684 / 213; 2026 weeks 1-6 | AGREE | Counted from `data/nflverse/advstats_week_*`. Week lists run 1-6 for 2026. |
| 3 | "Season-level `advstats_season_*`: 404 for 2025 at the tried path... Not used" | DISAGREE | The season files are one file for all seasons: `advstats_season_rush.csv`, `_pass.csv`, `_def.csv`, all HTTP 200, last-modified 2026-10-23. Per-year paths (`advstats_season_rush_2025.csv`) are 404. nflreadr says `summary_level` is "week (default) or season - some data is only available at the season level". See F4. |
| 4 | PFR 2025 rush rows: 2,353 of 2,355 join to `players.csv` `pfr_id` | AGREE | 2,353. |
| 5 | PFR before + after contact yards = nflverse season rushing yards for 330 of 334 players; carries equal for 332 of 334 | AGREE | 334 compared, 332 carries equal, 330 yards equal. The report does not say the PFR rush file also covers QBs (609 of 2,355 rows). PFR total carries (14,601) equal the stats-file total, kneels included. |
| 6 | `ngs_rushing`: 2016-2026, 6,290 rows, no QB rows, positions RB / HB / FB | AGREE | 6,290 rows; RB 6,235, HB 45, FB 10, QB 0. The columns match the report's list. |
| 7 | NGS weekly rows only at 10+ attempts | AGREE | Minimum weekly attempts is 10 in every season; 10 and 11 are the modal values (584 and 587 rows). |
| 8 | NGS week-0 rows are season summaries; equal the weekly sum for 3 of 51 players in 2025 | AGREE, with the cause added | 3 equal, 48 differ (Hunt 163 vs 103, Hubbard 134 vs 96). The cause is a **truncated sum**: week-0 attempts equal the nflverse stats-file season carries for all 51 (Hunt 163 = 163), and the weekly rows leave out every game under 10 attempts. The report says only "do not sum" and gives no cause. |
| 9 | NGS 2026: weeks 0-7, 231 rows, 27-34 per week, week 7 one row | AGREE | 231 rows; weekly counts 34, 33, 31, 30, 30, 27; week 7 = D'Andre Swift, 22 attempts. `ngs_passing`: 6,161 rows, 2026 weeks 0-7. |
| 10 | Injuries 2025: 6,068 rows (REG 5,783); status counts; 2026: 2,124 rows, weeks 1-7 | AGREE | All counts identical (Out 1,382, Questionable 1,283, Doubtful 120, blank 3,283; practice-status counts too). 2026 weeks 1-7. |
| 11 | Injuries "2022-2026" and snap counts "2021-2025 files exist" | AGREE but understated | `injuries_2009.csv` and `snap_counts_2012.csv` return 200. The report's span is a floor, not the range. |
| 12 | Snap counts 2026 weeks 1-6 complete (1,301-1,502 rows), week 7 partial (92 rows) | AGREE | Weekly rows 1,492, 1,502, 1,494, 1,482, 1,400, 1,301; week 7 has 92 rows in the fresh copy (`rush_src/raw/snap26.csv`). Position labels include C (240 rows); the report says "C not checked". |
| 13 | Depth charts 2026: 689,308 rows, 253 snapshots, 2026-03-22 06:38 to 2026-10-23 12:34 UTC | AGREE | All three reproduce. See F2 for what the report did not read in this file. |
| 14 | FTN charting 2022-2026, 29 identical columns; fields `is_rpo`, `is_qb_sneak`, `n_defense_box`, `starting_hash`, `qb_location`; **no run-concept field** | AGREE | The 29 names equal the nflreadr dictionary list (https://nflreadr.nflverse.com/articles/dictionary_ftn_charting.html). None is a run concept. 2022 returns 200, 2021 is 404. §11's closing line says "FTN 2023-2025", which contradicts §2 (2022-2026). The 2022 file has 41,643 rows and the same 29 columns. |
| 15 | FTN 2026: weeks 1-6, 15,904 rows | AGREE | 15,904 rows; weekly counts 2,675, 2,649, 2,741, 2,764, 2,609, 2,466. |
| 16 | FTN `n_defense_box` is 0 on about 24% of rows, meaning UNVERIFIED | AGREE, and the meaning is now checkable | 11,235 of 47,316 = 23.7%. Joined to play type, the zeros fall on kickoffs (2,927), punts (2,042), extra points (1,330), field goals (1,140), `no_play` (1,072), kneels (452) and spikes (80): 9,043 of the 9,095 zero rows in the 45,176 joined; participation's `defenders_in_box` is also 0 on 9,092 of them. The dictionary defines it as "number of defenders in the box at the snap". Zero means "no box on this play", not missing data. |
| 17 | Participation 2025: formation counts, `defense_man_zone_type` blank on 23,129 of 45,184 | AGREE | SHOTGUN 22,227, UNDER CENTER 12,145, blank 9,108, PISTOL 1,704; 23,129 blank. Label groups (2,4,5) 9,244; (4,2,5) 8,432; (3,4,4) 5,705; (4,3,4) 5,110; (3,3,5) 3,961. |
| 18 | `defense_man_zone_type` "is blank on every run play" | DISAGREE (small) | Of 14,895 `play_type = run` rows joined, 1,153 carry a man/zone value: 1,147 are QB scrambles and 6 are designed runs. For designed runs the claim holds (6 of 13,746). For scrambles the coverage shell is charted, which is a QB-rushing input the report does not mention (MINOR). |
| 19 | Participation `defenders_in_box` equals FTN `n_defense_box` on 45,086 of 45,176 joined; pass rushers on 45,173 | AGREE | 45,086; and 35,994 of 36,081 (99.76%) on the plays where FTN's box is non-zero. |
| 20 | Participation 2016-2025 exists; 2026 is HTTP 404 | AGREE | 2016 and 2025 return 200; 2015 and 2026 return 404. |
| 21 | `ff_opportunity` and other free expected-rushing sources | CANNOT VERIFY | The report does not mention it. I tried `ff_opportunity_2025.csv`, `.parquet` and `ep_weekly_2025.csv`; all 404. I do not know the current asset name. |
| 22 | FTN dictionary "not reached" so `read_thrown` SD / CHK / DES are UNVERIFIED | Resolvable | https://nflreadr.nflverse.com/articles/dictionary_ftn_charting.html: SD = "scramble drill (throw made after the play broke down)"; CHK = "checkdown"; DES = "designed read (throws with no progression, e.g. screens and many RPOs)"; `is_rpo` = "play is considered run-pass option"; `is_qb_sneak` = "quarterback sneak". |
| 23 | NGS definitions "not reached" | Resolvable | https://nflreadr.nflverse.com/articles/dictionary_nextgen_stats.html defines `efficiency` (distance travelled per rushing yard; lower is more north-south), `percent_attempts_gte_eight_defenders` ("how often does a rusher see 8 or more defenders in the box"), `avg_time_to_los` and `expected_rush_yards` ("based on Nextgenstats' Big Data Bowl model"). |

### 2.2 Run-concept and front claims

| # | Report claim | Result | Evidence |
|---|---|---|---|
| 24 | "No free source gives run concepts or defensive fronts" (§1.4) | DISAGREE | See F1 and F2. |
| 25 | FTN, PFR, NGS and participation carry no run-concept field | AGREE | Headers of all four checked. |
| 26 | Over/under, even/odd, nose/shade: "no field found" | AGREE | None in pbp, participation or FTN. |
| 27 | 3-4 vs 4-3 "cannot be read from these labels" (participation) | AGREE for participation; DISAGREE for the file set | `depth_charts_2026.csv` carries `pos_grp` = "Base 3-4 D" or "Base 4-3 D". See F2. |
| 28 | Run direction and gap fields in pbp: counts in §2 | AGREE | `run_location` blank on 128 non-kneel runs plus the 452 kneels (453 flagged `qb_kneel`); `run_gap` blank on all 3,907 middle runs; right guard 1,906, left guard 1,847, right tackle 1,830, right end 1,779, left end 1,775, left tackle 1,721. |
| 29 | FO line-yard weights UNVERIFIED; two competing versions | CANNOT VERIFY at the primary | The FTN-hosted FO page (https://ftnfantasy.com/?p=34536) returned 403. A search (secondary) lists the first version: losses 120%, 0-4 yards 100%, 5-10 yards 50%, 11+ yards 0%. I found no source for the report's second version. |

### 2.3 nflverse coding (§8)

All counts are 2025 pbp (`play_by_play_2025.csv.gz`), recomputed independently of `rush_checks.py`.

| # | Report claim | Result | Evidence |
|---|---|---|---|
| 30 | `qb_kneel` 453 (452 `qb_kneel`, 1 `no_play`); yards mostly -1 (385) and -2 (32) | AGREE | 385 / 32 / zero-yard 27. Rusher id on 452. |
| 31 | `qb_scramble` 1,221 (1,149 run, 72 no_play); `qb_dropback = 1` | AGREE | 1,149 run, 67 no_play with `rush_attempt` 0, 5 with it blank. Passer id on none. |
| 32 | `qb_spike` 82; sack 1,352, all pass, no rusher id, passer id on all | AGREE | Reproduced. |
| 33 | Regular-season QB rushing: 1,792 run plays = 1,087 scrambles + 705 "designed" | AGREE as counted; DISAGREE as a definition | See F3: 79 of the 705 are aborted snaps and 10 are two-point tries. |
| 34 | FTN RPO 2,148 (run 1,657 / pass 436 / no_play 55); sneaks 341; 262 on REG QB designed runs; RPO on 74 of 705 designed and 12 of 1,087 scrambles | AGREE | All reproduce. Sneak on scrambles: 0. |
| 35 | No pbp RPO or sneak flag; "sneak" appears in no description | AGREE | No column containing rpo, option or sneak; 0 descriptions. (PFR's season pass file has an RPO rush count, F4.) |
| 36 | stats file vs pbp QB rushing (81 QBs): 2,215 / 9,686 vs 2,225 / 9,683; 71 of 81 exact incl. kneels; 13 of 81 excl. kneels | AGREE | Same numbers. |
| 37 | The other 10 QBs: "pbp has exactly one more carry... cause not identified" | DISAGREE, and the cause is findable | Nine of the ten are QBs with one two-point-conversion rush each (QB 00-0036945 has two). That is 10 extra carries, matching 2,225 - 2,215. The stats file carries two-point rushes in `rushing_2pt_conversions`, not `carries`. QB 00-0035710 (Daniel Jones, week 7) has equal carries but pbp yards 161 vs 164: an aborted snap (`rushing_yards` 0, `yards_gained` 3) that the stats file credits 3 yards. So "exactly one more for each" is false for two of the ten. Closes UNVERIFIED item 9. |
| 38 | **Aborted plays** (named in the brief) | NOT IN THE REPORT | See F3. |

### 2.4 Kalshi (§9)

| # | Report claim | Result | Evidence |
|---|---|---|---|
| 39 | `/series?category=Sports` gives 4,046 series, 388 with NFL in the ticker | AGREE | Saved `series_sports.json`: 4,046 and 388. |
| 40 | Open-market, event and QB-rung counts for RSHYDS, RSHATT, RRYDS, TD, LONGRSH, LADDER, ESCALATOR, PASSYDS, PASSATT, REC, RECYDS | AGREE | I recomputed 534 / 13 / 149 (24 QBs); 89 / 12 / 20; 81 / 12 / 0; 576 / 13 / 45; 49 / 12 / 17; 26 / 12 / 8; 26 / 12 / 8. Open counts have moved since the pull (live now: RSHYDS 549, TD 595); RSHYDS events are still only 26OCT25 (526 markets) and 26OCT26 (23). |
| 41 | Every open rushing event is dated 25 or 26 Oct; this is nflverse week 7; no open event 29 Oct to 2 Nov | AGREE | `nflverse/nfldata/data/games.csv`: week 7 = NE@CHI 22 Oct, twelve games on 25 Oct, DAL@PHI 26 Oct; week 8 runs 29 Oct to 2 Nov. |
| 42 | BUF, CHI, JAX, LAC, NE, WAS have no open event; reason UNVERIFIED for all but NE | Resolvable | Same file: BUF, JAX, LAC, WAS are on **bye** in week 7. NE and CHI played the Thursday game. Closes UNVERIFIED item 12. |
| 43 | KXNFLWEEKMOSTRSHYDS "26W7" label "does not match either numbering" | DISAGREE | W7 is nflverse week 7 (the event's player, Tony Pollard, plays CLE@TEN on 25 Oct). Only the close time is odd: 2026-11-10 00:15Z is four hours after week 9's Monday-night kickoff. |
| 44 | The series list in the table is the rushing / QB set | DISAGREE (incomplete) | See F5. |
| 45 | Contract PDFs: FOOTBALLENTITYSTAT underlying, source agencies, participation, nullified-play, post-expiry, multi-player text | AGREE | `rush_src/kalshi/FOOTBALLENTITYSTAT.txt`. The report's first quote joins two sentences with an ellipsis ("the official attribution of <stat> ... recorded in the league's box score and/or play-by-play"): the underlying says "official attribution of <stat> in <time period> of <game>", and `<stat>` says "official football statistic as recorded in the league's box score and/or play-by-play". Not misleading; flag as a composite. |
| 46 | NFLENTITYSTATCOVERED (protected): first-half-only rule, "above"/"at least" only, NFL first in the agency list | AGREE | Lines 6 and 80-91 of the PDF text. |
| 47 | FOOTBALLENTITYSCALARSTAT: "Above X" strictly greater; "At least X" is X or more | AGREE | Lines 84-85. The sentence "a floor strike of 19.5 therefore means 20 or more" is the report's inference; it is also what the market text says (`floor_strike` 79.5 / "80+"). |
| 48 | Product text for RSHYDS: "Once a player takes at least one snap, even if nullified by penalty, the market settles based on yards recorded." | AGREE as to the source; wording differs from the market text | Verbatim from the series object, `GET /series/KXNFLRSHYDS` -> `product_metadata.important_info.markdown`. The per-market `rules_secondary` says "...settles based on rushing yards recorded". The report does not name the field. |
| 49 | KXNFLTD: "Passing Touchdowns do not count for this market." | AGREE as to the source; supported by settlement | Verbatim in `GET /series/KXNFLTD` -> `product_metadata.important_info.markdown`, **not** in any KXNFLTD market's `rules_primary` or `rules_secondary` (0 of 595 live markets mention "passing"). Only KXNFLTDPROT's market text has "Passing touchdowns do not count as touchdowns scored by the passer." Settlement agrees: in 2026 QB-games with passing TDs and no rushing or receiving TD, 130 of 130 "1+ touchdowns" markets settled NO (and 78 of 78 at higher rungs). |
| 50 | Settlement inference: 239 of 239 settled 2025 QB RSHYDS ladders bracket nflverse QB rushing yards incl. kneels and scrambles; 227 without kneels; 94 without scrambles; 12 and 145 discriminating cases | AGREE (reproduces), with the population stated differently | See 2.5. |

### 2.5 My re-derivation of the settlement test (`ladder.py`)

Method: for each (event, QB) ladder, the bracket is lo = highest YES floor strike and hi = lowest NO floor strike; a convention
"fits" if lo < total < hi. A convention that falls outside the bracket for any ladder is falsified for that ladder. Game dates come
from the event ticker; yards come from nflverse pbp `rushing_yards`, two-point tries excluded.

| Season | Ladders for QBs in the file | Matched to a QB-game with a pbp row | Two-sided / one-sided | Incl. kneels + scrambles | Excl. kneels | Excl. scrambles | Net of sack yards | Excl. aborted-snap yards |
|---|---|---|---|---|---|---|---|---|
| 2025 (report's) | 256 | 253 (the report's 239 plus 14 QB-games with zero rush plays) | 124 / 129 | 253 fit | 241 fit (12 falsified) | 108 fit (145 falsified) | 142 fit (111 falsified) | 253 fit (0 falsified) |
| 2026 weeks 1-6 (not in the report) | 217 | 178 | 118 / 60 | 178 fit | 164 fit (14 falsified) | 59 fit (119 falsified) | 77 fit (101 falsified) | 177 fit (1 falsified) |

What the test **can** prove: kneels and scrambles count in settled rushing yards (both seasons, zero ladders against). Sacks are **not**
netted into the QB's rushing yards: 111 of 253 (2025) and 101 of 178 (2026) ladders reject a "net of sack yards" total. The report
lists sacks as unverifiable (items 2 and 10); the inputs it already had decide it. One 2026 ladder (Caleb Williams, MIN@CHI 2026-09-20,
bracket 39.5 to 49.5) shows an aborted snap's yardage counts: the 17-yard play ("FUMBLES (Aborted)... to MIN 45 for 19 yards") must be
included for his total of 42 to fit.

What it **cannot** prove: aborted snaps as *attempts* (yardage on 2025 aborted snaps is almost always 0, so no 2025 ladder discriminates;
24 contain one); two-point tries (3 ladders in 2025, 1 in 2026; both choices fit); any official NFL rule (a settlement bracket is not a
rule); and, with 129 of 253 ladders one-sided, it constrains less than "239 of 239" suggests. Bracket width is 10 yards (median) in 2025.

Attempts check (not in the report): I pulled 1,000 settled KXNFLRSHATT markets from the live API (`status=settled`, 2026-09 and 2026-10 games);
291 ladders match a pbp rusher. Counting every rush attempt (kneels, scrambles, aborted snaps) fits 291 of 291. Dropping kneels fits 271,
dropping aborted snaps 285 (6 falsified, 14 ladders contain one), dropping scrambles 234. So aborted snaps **do** count as attempts on Kalshi.

## 3. Findings

### MAJOR

**F1. The run-concept headline is wrong as an absolute (§1.4, §2, §10).**
`plays.csv` of the NFL Big Data Bowl 2025 carries PFF-charted `pff_runConceptPrimary`, `pff_runConceptSecondary`, `pff_runPassOption`,
`pff_passCoverage` and `pff_manZone`. I confirmed the column names in five unrelated public repositories (GitHub code search for
`pff_runConceptPrimary`; for example `miguelmendesduarte/big-data-bowl-2025` `src/data_processing/cleaning/plays.py` and
`ChrisKornaros/NFL_Big_Data_Bowl_2025` `notebooks/rf.ipynb`). I could **not** open Kaggle (JavaScript-rendered; WebFetch returned only the
title), so I cannot verify the weeks and season it covers, its row count, or its licence. It is a one-off historical competition file, not a
2026 feed, so it cannot drive a live feature; it could serve as a prior or as a training label for a concept classifier. Separately,
Razzball publishes a free zone-vs-gap tool (https://football.razzball.com/new-zone-vs-gap-rushing-tool/): team rushing yards allowed to zone
and gap schemes, and per-RB zone/gap attempts. The page names no data source, offers no download and says "Copyright... All rights reserved",
so it is display-only.
*Fix:* reword §1.4 to "no free **live or 2026-covering, redistributable** source"; add BDB 2025 `plays.csv` (columns verified, coverage and terms
UNVERIFIED) and the Razzball tool as leads; keep the "not downloadable / not licensed" caveats.

**F2. A free team-level base-front label exists and the report says none can be read (§3, §7, §1.4).**
`depth_charts_2026.csv` (also 2025, which returns 200) has `pos_grp` values "Base 3-4 D" and "Base 4-3 D" per team, next to "3WR 1TE" and
"Special Teams". In the latest snapshot (2026-10-23 12:34Z) 19 teams list "Base 3-4 D" and 13 list "Base 4-3 D", one label each. Across
253 snapshots two of 32 teams change label. The report's §7 lists `pos_grp` among the depth-chart keys without reading its values, and §3
says 3-4 vs 4-3 "cannot be read from these labels" (true only of participation).
*Caveat to state:* it is ESPN's nominal depth-chart base package, not a per-snap front, and many teams spend most snaps in nickel. A weak
team-level proxy, but it exists and is free (CC BY 4.0 repository licence; the underlying ESPN terms were not checked).
*Fix:* add it to §3's table with that caveat, and soften the "cannot be read" line.

**F3. "QB designed run: 705" mixes in aborted snaps and two-point tries; aborted plays are never discussed (§8.1, §8.3).**
In 2025 regular season, QB-rusher runs with `qb_scramble = 0`, `play_type = run`, `rush_attempt = 1` number 705. Of those, 79 have "Aborted"
in the description (fumbled snaps; 76 with `rushing_yards` 0, 75 with `yards_gained` 0), 10 are two-point conversion tries
(`two_point_attempt = 1`, no `rushing_yards`), and 616 remain. Across all positions there are 87 aborted `run` plays (79 QB, 5 RB, 2 TE, 1 WR)
plus 2 in the postseason. The one yardage mismatch is Daniel Jones, 2025_07_IND_LAC: `rushing_yards` 0, `yards_gained` 3; the stats file credits
3. The brief names aborted plays as a coding claim to check; the report has no row for them. Consequences: every "designed QB run" rate built from
the report's definition is inflated by 12.6%; and the stats file and Kalshi both count aborted snaps (2.5), so a feature that drops them
misses attempts that settle.
*Fix:* add an "Aborted snap" row to §8.1 (counts above, how to detect: description contains "Aborted"; 76 of 79 QB cases have `rushing_yards` 0),
split the 705 into 616 / 79 / 10, state that the stats file counts aborted snaps as carries and excludes two-point tries, and cite 2.5 for the
Kalshi side. Replace "cause not identified" in §8.2 and §13.9 with the two causes.

**F4. PFR season-level files were dismissed through a wrong path and hold QB scramble and RPO rush counts (§5, §8.3).**
`advstats_season_pass.csv` (848 rows, 2018-2025) has `scrambles`, `scramble_yards_per_attempt`, `rpo_plays`, `rpo_yards`, `rpo_pass_att`,
`rpo_pass_yards`, `rpo_rush_att`, `rpo_rush_yards`, `pa_pass_att`. For the 47 QBs of 2025 with 100+ attempts, PFR `scrambles` sum to 1,028 against
1,029 `qb_scramble` runs in pbp (correlation 0.9988), so it is a usable check on the pbp flag. PFR `rpo_rush_att` sums to 192 against 72 for the
FTN-flagged QB RPO runs the report proposes as its proxy (correlation 0.68; the definitions differ and PFR's is UNVERIFIED). The proxy in §8.3 may
undercount QB RPO keeps by roughly a factor of 2.7. `advstats_season_rush.csv` (2,996 rows, 2018-2026) has `ybc`, `yac`, `brk_tkl`, `att_br`;
`advstats_week_rec_<Y>.csv` also exists and is not mentioned. §5 also says the weekly PFR pass file has "no scramble field": true weekly, false
for the season file.
*Fix:* correct the path (single file per stat type, all seasons), list the season columns, change "No free read-option flag exists" to "no play-level
flag; PFR publishes season QB RPO rush counts", and note PFR's RPO count is 2.7x the FTN proxy on 2025.

**F5. The Kalshi series table leaves out rushing-relevant series (§9; brief item 4).**
Live `status=open` counts (2026-10-23 evening) for series the report omits:
- `KXNFLMOSTRSHYDS`: 33 markets, 9 events. Game-level "most rushing yards among all players in the game"; includes QBs (Mahomes, Murray);
  ties pay 1/N; a non-participant resolves No. Contract PDF is FOOTBALLENTITYSTAT.
- `KXNFLSEASONRSHYDS`: 116 markets; `KXNFLSEASONRSHTD`: 224 markets. Season-long, QBs included (Lamar Jackson 750+, Josh Allen, Murray, Maye).
- `KXNFLFIRSTTD`: 337 markets across 13 events (first touchdown scorer).
- `KXNFLPASSTDS` / `KXNFLPASSTDSPROT`: 100 each; `KXNFLPASSYDSPROT` 211; `KXNFLRECYDSPROT` 1,000+; `KXNFLLADDERREC` 58.
- `KXNFLTDPROT` (576) and `KXNFLRECPROT` (831) were in the report's own `open_nfl_rush_series.json` but are missing from its table.
Also `KXNFLTD` is *all* touchdowns (rushing, receiving, returns, D/ST: "ARI Cardinals D/ST" is a selection), not a rushing-touchdown market. The report's
"anytime touchdown market in practice" is right, but the table does not say it has no rushing-only touchdown series per game.
*Fix:* add the rows above (or state they were not pulled), and say that no per-game rushing-touchdown series exists.

### MINOR

- **M1. §9.2 population.** "239 ladders" is the QB ladders whose QB had at least one rush play. The file holds 256 QB ladders; 14 zero-carry games and 3
  non-plays drop out silently. The 129 one-sided ladders are not disclosed. *Fix:* state both, and add the sack result and the 2026 and attempts replications (2.5).
- **M2. §8.3 and §13: rules.** The scalar contract (FOOTBALLENTITYSCALARSTAT, "Underlying" and "Source Agency") says games are "scored under the NFL's official
  scoring rules for statisticians" and names "the official game books, box scores, play-by-play, and statistics published by the NFL at NFL.com". That is the
  document to cite, and the report did not quote it. The forum repost below calls the statistics document the NFL's "Guide for Statisticians" rather than "Statistics Manual".
  I found only that repost (https://mail.profootballresearchers.org/forum/viewtopic.php?p=6373, thread "Sack rules question", attributed to the Guide): a play is a sack only if the
  passer "makes an apparent attempt to pass"; otherwise "the play shall be considered a rushing play". Secondary; the Guide itself was not found. The 2026 rulebook
  text has no "statistic" (0 matches in `rush_src/rulebook/rb.txt`), and its knee text (lines 1917 and 3195 of that extraction: a quarterback who "immediately drops to his knee"; a
  runner who has "taken a knee, or... declared himself down") is dead-ball rules, as the report says.
- **M3. §12 participation licence.** nflreadr's `load_participation` page (https://nflreadr.nflverse.com/reference/load_participation.html): the dataset is
  "released under the CC-BY-SA 4.0" licence, with credit "FTN Data via nflverse" for 2023 onward **and** "NFL NextGenStats via nflverse" for 2022 and earlier. The
  report's row says "participation from 2023" and gives one credit. Add the second credit if any pre-2023 participation-derived table is ever committed.
- **M4. §12 sources I confirmed.** nflverse-data `LICENSE.md` is CC BY 4.0. FTN: nflreadr says "CC-BY-SA 4.0" and credit "FTN Data via nflverse"; data "from the 2022 season
  onwards". Sports Reference (https://www.sports-reference.com/termsofuse.html): s.5 item 9 bars automated access "without our express written permission"; s.5 also says
  "sharing, using, modifying, repackaging, or publishing data found on individual SRL webpages is welcomed, whether for commercial or non-commercial purposes" with credit;
  item 10 bars a database "that competes with or constitutes a material substitute". The report's "reuse with credit is welcomed" omits item 10. NFL.com terms s.1.3: "Any other use,
  including for any commercial purposes, is strictly prohibited without our express prior written consent"; s.11(f) bars spiders and robots "to harvest... for any commercial
  purpose". Kalshi Developer Agreement s.3.1 and s.3.5 and Data Terms s.I and II are as the report paraphrases; it does not cite Data Terms s.II ("compiling... databases", "creating
  derivative works") or Developer Agreement s.3.5 ("benchmarking or competitive purposes"). nflreadr's own page says NFL data "belong to their respective owners, and are governed
  by their terms of use". The licence of the nflverse-hosted PFR and NGS copies remains open, as the report says.
- **M5. §13.6 and §8.4.** FTN `n_defense_box` zero means "no box" on non-scrimmage plays (#16); `read_thrown` and NGS definitions are published (#22, #23). Remove them from the
  UNVERIFIED list.
- **M6. Internal inconsistencies.** §11 closes with "FTN 2023-2025" (FTN starts 2022). Snap-count "C not checked" (C is present). The "10 QBs one more carry each" sentence (#37). The
  W7 label (#43).
- **M7. Scrambles and coverage.** Scrambles carry `defense_coverage_type` / `defense_man_zone_type` (1,147 of 1,153 non-blank run-play values). That is a charted input for QB scramble
  modelling the report does not mention.

### NIT

- Cite which Kalshi field holds each quoted sentence (series `product_metadata.important_info.markdown` vs market `rules_secondary`).
- The report calls the FTN price tier "$599 / $5,000 unverified"; I could not reach ftndata.com either (403 from the proxy), so it stays unverified.
- `players.csv` modified time and similar freshness stamps are fine; I spot-checked FTN 2026 (Last-Modified 22 Oct 03:12 GMT), injuries 2026 (23 Oct 12:34 GMT), `depth_charts_2026`
  (23 Oct 12:34 GMT) and `stats_player_week_2026` (23 Oct 14:36 GMT): all match.

## 4. Coverage table (§11): five-plus cells spot-checked against files

| Cell | Report | Checked |
|---|---|---|
| pbp 2026 games per week | 16, 16, 16, 16, 15, 14; week 7 = 1 (NE@CHI) | AGREE (fresh copy: weeks 1-7 = 16, 16, 16, 16, 15, 14, 1; latest `game_date` 2026-10-22). The older scratch copy has no week 7. |
| FTN 2026 | weeks 1-6, 15,904 rows | AGREE |
| PFR advanced 2026 | 736 / 2,668 / 213 rows, weeks 1-6 | AGREE |
| NGS rushing 2026 | weeks 1-6 at 27-34 rows, week 7 one row | AGREE |
| Snap counts 2026 | weeks 1-6 complete, week 7 partial (92 rows) | AGREE |
| Injuries 2026 | weeks 1-7 (REG) | AGREE (182, 251, 301, 318, 361, 376, 335) |
| Participation 2026 | none, HTTP 404 | AGREE |
| `players.csv` | 24,861 players, 22,696 with `pfr_id` | AGREE |
| `stats_player_week` 2026 | weeks 1-6 complete, week 7 one game | AGREE (week 7 has two teams) |

## 5. What I could not check

- **NFL Big Data Bowl 2025 data and terms.** Kaggle's pages render by JavaScript; I read only the title. I know the columns exist (code search) but not the season coverage,
  row counts, or the competition rules on reuse, redistribution or commercial use.
- **NFL "Guide for Statisticians"** (the primary source for kneels, aborted snaps, sacks and scrambles). Not found; only a forum repost of the sack passage.
- **Football Outsiders line-yard and power-success definitions at the primary** (ftnfantasy.com returned 403); **FTN's price list and data catalogue** (ftndata.com returned 403);
  **Pro-Football-Reference** pages and glossary (Cloudflare challenge on robots.txt; I did not attempt to get around it).
- **Sports Reference's "Bot Traffic" page**, **Kalshi's rulebook outside the contract PDFs**, and **NFL Next Gen Stats terms** (the NFL.com terms do not mention it).
- **Whether the PFR season-pass `rpo_rush_att` and FTN `is_rpo` measure the same thing.** Neither definition was found; I report only the count gap.
- **`ff_opportunity`** or any other expected-rushing release (asset names I tried returned 404).
- **The KXNFLRSHATT settled sample** is whatever the live API returned (1,000 markets, September and October 2026); I did not reach 2025 attempt ladders.
- **ESPN's licence for the depth-chart base-package label**, and how stable the label is across a season beyond the 253 snapshots seen (2 of 32 teams changed).

---

## RE-CHECK (2026-10-24)

**Verdict: AGREE AFTER FIXES.** The orchestrator's rewrite fixes all five of my MAJOR findings in substance, and every figure and quotation I re-ran
reproduced. What is left is six wrong or loose statements, all small and all checkable in a few minutes (section R2). None changes a conclusion, so
no BLOCKER and no MAJOR remains. Items 1 and 3 misstate contract text and should be fixed before it ships; 2, 4, 5, 6 and 7 are accuracy and completeness fixes. I read the new `research/reports/rush_data_sources.md` (382 lines) and `research/reviews/rush_data_sources_opus.md`
(461 lines) in full.

### R1. My findings, one by one

| My finding | Fixed? | What I checked |
|---|---|---|
| F1 run-concept headline | **Fixed.** §1.4 now reads "no free LIVE or redistributable source, and two leads"; §2, §10 and §12 carry BDB 2025 as a research lead with terms UNVERIFIED, and Razzball as display-only. | The column names are as I found them. The report says "coverage part of 2022 (weeks UNVERIFIED)": neither review had opened Kaggle, so the year was unproven. A book repo (`joshuawjulian/reading-the-game`, chapters 03-02 and 12-08) says "2025 Big Data Bowl release (2022 season, weeks 1-9)" and that `plays.csv` carries a PFF run-concept label (secondary; their own fact sheet marks the weeks "LIKELY"). Keep UNVERIFIED, optionally cite that. |
| F2 base-front label | **Fixed.** §1.4, §3 table row, §7 row. | 19 / 13 teams and 2 of 32 changed over 253 snapshots: reproduced (2026 file). The "2025 and 2026 files" claim: reproduced. I downloaded the full 2025 file: 221 daily snapshots, 2025-08-03 to 2026-03-14, `pos_grp` has the same four values, 20 teams 3-4 and 12 teams 4-3 at the last snapshot. **9 of 32 teams carry both labels during 2025** (against 2 of 32 in 2026), which strengthens the report's "weak proxy" wording; worth one clause. "Pre-2025 files have a different layout": the 2024 file's header (`formation`, `depth_position`, `club_code`) confirms it. |
| F3 designed runs and aborted snaps | **Fixed.** §1.5(c)(d), §8.1 first row, §8.2 last row, §8.3. | `aborted_play` is a real column; among the 705: 79 flagged, 76 with `rushing_yards` 0, 10 two-point tries, 616 left. Excluding `two_point_attempt`: 2,215 carries, 80 of 81 QBs exact. Williams, bracket 39.5 to 49.5, 17-yard aborted play: as I found it. Scramble row: 1,089 regular-season scramble runs, 2 by WRs: reproduced. |
| F4 PFR season files | **Fixed, one residue.** §5 row 126 names the path; §10 lists it. | Correlation 0.9988 is mine. It is for the 47 QBs with 100+ pass attempts (sums 1,028 vs 1,029); the report does not give the sample. The row says "2018-2025", but `advstats_season_rush.csv` also holds 176 partial 2026 rows (pass has none). See R2-4 for the RPO residue. |
| F5 Kalshi inventory | **Mostly fixed, one gap.** MOSTRSHYDS, FIRSTTD, FFPTSLADDER, the receiving ladders and escalators, the two PROT series and RECYDSPROT are in the table. | All counts below reproduced. Not added: `KXNFLSEASONRSHYDS` and `KXNFLSEASONRSHTD` (R2-5). |
| M1 population of "239" | **Fixed.** §9.2 now shows 239 / 253 and two-sided / one-sided. | 124 / 129: reproduced. |
| M2 contract wording for stats | **Fixed in substance** (§8.3 quotes the scalar contract's statistician clause), with the sack paraphrase problem in R2-3. | The four quoted sentences match `FOOTBALLENTITYSCALARSTAT.txt` character for character. |
| M3 participation credit | **Fixed.** §12 has both credits. | Matches `load_participation`. A wording point in R3. |
| M4 licence quotes | **Fixed.** The Sports Reference and NFL.com rows are correct (and better than mine). | See R4: my own M4 paraphrase of the automated-access clause omitted the "adversely impacts" qualifier; the report's version is right. |
| M5, M6, M7 | M5 fixed; M7 fixed (§2 now has the scramble data); **M6 not fixed**: §11 still ends "FTN 2023-2025" (R2-6). | |

### R2. What is still wrong or loose in the report

1. **§8.3 stat-corrections bullet: "the scalar contract has the same rule" is false.**
   The binary contracts (`FOOTBALLENTITYSTAT`, `NFLENTITYSTATCOVERED`, `FOOTBALLPLAYERCAT`) say: "Official stat corrections made by the league after the game will
   not affect the value of the Contract". The scalar contract says the opposite for the window up to expiry: "Changes to official statistics published before
   Expiration are included. Changes published after Expiration, including scoring changes issued following the post-game review by the NFL and its official statistician,
   are not accounted for". So a correction issued after the game but before 10:00 ET expiry counts on the ladders and not on the binary rungs. §9.1's own last bullet
   states the two clauses correctly, so §8.3 contradicts §9.1. (The error came from Opus m3, which the report copied.) *Fix:* say "different cut-offs", cite both quotes.
2. **§9.2 "What this can and cannot prove": "RB ladders ... are not tested" is wrong for attempts.**
   Of the 291 KXNFLRSHATT ladders, 205 are RBs and 86 are QBs, and one of the six aborted-snap falsifications is an RB (five QB). Only the *yardage* ladders are QB-only.
   §1.3's attempts bullet gives 291 without the split. *Fix:* "yardage: QB ladders only (253 / 178); attempts: 291 ladders, 205 RB and 86 QB; 6 aborted-snap falsifications, 5 QB and 1 RB".
3. **Sack paraphrase in §8.3 and §9.1 puts words in the contract.**
   §9.1 says "sacks come off team passing and not a player's rushing"; §8.3 says "Sacks are not rushing. The same contract." The text says: "Sack yardage is deducted from a
   club's net passing yards and total net yards. It is not deducted from a player's passing yards, which are the player's individual gross passing yards." It says nothing about
   rushing. (§1.3 words it correctly.) The rushing exclusion is established by the brackets (111 of 253 and 101 of 178 reject a sack-netted total), not by the contract.
   *Fix:* use §1.3's wording in §8.3 and §9.1 and credit the brackets for the rushing exclusion.
4. **RPO: the PFR season count is presented as a check on the FTN proxy, and it is not one.**
   §10 says the season files are "a prior-season check of the pbp scramble and FTN RPO counts". The scramble check holds (1,028 vs 1,029 on my 47-QB sample; Opus 1,091 vs 1,087 over
   all QBs). The RPO one does not: PFR `rpo_rush_att` is 192 against the 72 FTN-flagged QB RPO runs on my 47 QBs (Opus: 310 against 74 + 12 = 86 over all QBs, about 3.6 times), correlation
   0.68. Both reviews asked for this caveat (my F4, Opus F6); it is absent from §5, §8.3 and §10. *Fix:* say the two counts cannot both be "QB keeps", that neither definition is
   known, and that §8.3's 74 of 705 is probably an undercount of QB RPO rushes.
5. **Kalshi inventory (§9 table), four gaps.**
   - Season-long rushing series are missing. Live `status=open`, 2026-10-23: `KXNFLSEASONRSHYDS` 116 markets, 4 events (750 / 1,000 / 1,250 / 1,500 yards), 4 QB rungs (4 QBs) and 105 RB rungs (41);
     `KXNFLSEASONRSHTD` 224 markets, 6 events, 19 QB rungs (5) and 188 RB rungs (42). Also `KXNFLPASSTDS` and `KXNFLPASSTDSPROT` (100 each, 24 QBs), `KXNFLPASSYDSPROT` (211), `KXNFLFFPLAYERHIGH` (36).
     *Fix:* add rows, or say these were not pulled.
   - `KXNFLTD` is every touchdown type. In 2026 weeks 1-6, 215 player-games with a receiving touchdown and no rushing touchdown settled YES at 1+ (213 by WR / TE / RB, 2 by other positions), and the event record says
     "Anytime Touchdowns". There is no per-game rushing-only touchdown series. §1.2 and §9 never say so, and the RB count of 133 therefore counts receiving touchdowns too.
   - The RB definition is looser than stated. The counts (and Opus's script) take RB, FB and HB labels and strip punctuation and suffixes ("Jr.", "Sr.", "III"); `KXNFLTD` also leaves 33 rungs
     unmatched (D/ST selections, rookies). With the report's stated rule (exact name, position RB) I get 366 / 38 for RSHYDS and 130 / 56 for TD. *Fix:* state the rule.
   - "Priced by the model" appears on RSHYDS only. `nfl_rush.py` (untracked) prices seven series: RSHYDS, RSHATT, RRYDS, LONGRSH, TD, REC, RECYDS. §1.2's "None of these is priced" is right for the
     list it follows; the table is silent on the other six.
6. **§11, last line: "FTN 2023-2025"** contradicts §2 and the FTN row of §12 (2022 onward; the 2022 file has 41,643 rows and the same 29 columns). Change to 2022-2025.
7. **§12 owner decision 3 ("NFL-sourced data for wagering ... The same default covers NGS").**
   The NFL.com clause is about using the Services "to gamble or wager on the result of any NFL game", not about fitting a model; a display-only default does not address it. The same sentence
   also leaves out that NGS-sourced participation (2016-2022) is already a fitted source in the shipped model (`FIT_SOURCES` includes participation). The report does say that pbp and participation are
   the shipped foundation, so the disclosure is there; the default's wording should say what it does not cover. *Fix:* "this default limits PFR exposure under the machine-learning clause; it does
   not mitigate the NFL.com wagering clause, which the owner must decide for the whole stack."

Smaller points (NIT unless the owner wants them):
- §9.1 still prints the spliced underlying quote ("the official attribution of <stat> ... recorded in the league's box score and/or play-by-play") that both reviews flagged, and still does not name the
  field the two product-text sentences come from (`GET /series/<ticker>` -> `product_metadata.important_info.markdown`; the §9 TD row does name it).
- §9.2's second table: only the 2025 row says "falsified"; the other rows carry bare numbers. The "(253 of 253 counting QB-games with none)" wording: 253 includes games with a recorded sack and no
  carry (14); 3 of the 256 QB ladders (a QB with neither a carry nor a sack that game) are not testable.
- §3: "A box count of 0 marks non-scrimmage rows" is almost right; 36 of the zeros are on pass plays.
- §4 still says FO "returned HTTP 404" and keeps a second line-yard weighting I could not find in any source; the FO page now lives at `ftnfantasy.com/?p=34536` (403 for me). Unchanged, still UNVERIFIED.
- §12 participation: the page says the whole file "is released under the CC-BY-SA 4.0" licence, with the credit differing by year. The report's row reads as if CC BY-SA covered 2023 on and 2022-and-earlier had only a credit.

### R3. Verified as correct (the parts you asked me to attack)

- **Settlement tables (§9.2, §1.3).** My code, own inputs: Oct-Dec 2025 253 ladders, 124 / 129, 253 fit, kneels-out 12, scrambles-out 145, sack-netted 111, aborted-yards-out 0; adding Jan-Feb 2026 (events `26JAN`, `26FEB`,
  2025 pbp): **290 ladders, 290 fit, 15, 169, 131** (Opus's row, exactly); 2026 weeks 1-6: 178 ladders, 118 / 60, 178 fit, 14, 119, 101, aborted-yards-out 1; attempts: 291 ladders, 291 fit, kneels-out 271, aborted-out 285,
  scrambles-out 234. All as printed.
- **Kalshi inventory counts.** From `open_nfl_rush_series.json` and the Opus pulls, same normalisation as Opus: RSHYDS 365 RBs (38), RSHATT 66 (22), RRYDS 78 (26), LONGRSH 31, TD 133 (58; my exact-name rule gives 130 / 56),
  RECYDS 193 (30), REC 172 (28), LADDER and ESCALATOR 17 each, WEEKMOST 19, TDPROT 24 QB and 58 RB, RECPROT 28 RB; MOSTRSHYDS 33 / 9 events / 9 QB / 23 RB; FIRSTTD 337 / 13 / 24 / 68; FFPTSLADDER 59 / 12 / 8 / 17;
  four receiving ladder and escalator series 58 / 12 / 0 / 17 each; RECYDSPROT 1,000 on the first page, 26 RBs. Zero-open series (RSHYDSH2H, NONQBPASS, 1HTD, 2HTD, 2TD, RECYDSH2H): confirmed.
- **Contract quotes.** The four scalar quotes: verbatim. Binary corrections clause: verbatim. `FOOTBALLENTITYSTAT` underlying: spliced (known). I also read `FOOTBALLPLAYERCAT.pdf` (the report's UNVERIFIED item 8): same "official attribution ... box score and/or play-by-play"
  wording as the binary contract, "strictly greater than" every other member, ties pay 1/N, a non-participant resolves No, and no kneel, sack or two-point text. So the MOSTRSHYDS and WEEKMOST conventions are *not* stated in their contract; the
  binary brackets are the only evidence, and no settled MOSTRSHYDS history was tested. Item 8 can be struck and replaced by that sentence.
- **Licence quotes.** Sports Reference ("Last Updated: May 19, 2023"; "in a manner that adversely impacts site performance or access"; the database clause; the full AI / machine-learning clause), NFL.com (Updated May 16, 2024; s.1.3 both sentences; "from using the Services to
  gamble or wager on the result of any NFL game"; robots.txt does not disallow `/legal/`) are verbatim in the Opus review's saved pages (`review_rush_sources_opus/web/sr_terms.html`, `nfl_terms.html`), and my own live fetches of both pages earlier agreed on the clauses they covered. nflreadr:
  "provided by Pro Football Reference starting with the 2012 season", "Participation data from 2023 onwards is courtesy of FTN", both credit lines, "for each week back to 2001", `pos_slot` definition: verbatim. The ninth Big Data Bowl (sportsvideo.org, 2026-10-08, Combine plus in-game tracking): confirmed by search.
- **Other new numbers.** NGS week 0 equals regular-season carries for **50 of 51** (Breece Hall 242 vs 243 is the exception); 2025 snap-count labels OL 1,457, T 1,363, G 925, C 668 (all game types); a 100% player in 543 of 544 regular-season 2024 team-games; scramble coverage filled on 1,147 of 1,149 and `was_pressure` TRUE on 426 of 1,149;
  `read_thrown` and `n_defense_box` dictionary quotes; the week-7 schedule and byes; the `W7` reading.
- **"Owner decisions" default.** It matches the code, not just the prose: `nfl_rush.py` sets `FIT_SOURCES = {pbp, participation, ftn, injuries, lines}`, labels PFR and NGS facts "display only" and says a later use is "a NEW pre-registered group ... with the owner's yes". The report labels it the orchestrator's default, not the owner's.
  "The repo commits no raw Kalshi price": checked tracked files (`research/data/nfl_matchup_model/kalshi.json` holds log-loss aggregates only) and the new untracked `research/data/nfl_rush/` files (no price column).

### R4. Corrections to my own review

- My F5 said `KXNFLMOSTRSHYDS` uses `FOOTBALLENTITYSTAT`. It cites `FOOTBALLPLAYERCAT.pdf`, as the report says.
- My M4 said Sports Reference "bars automated access without our express written permission". The clause continues "in a manner that adversely impacts site performance or access"; the report's wording is the accurate one.

### R5. Not checked in the re-check

- Kaggle's Big Data Bowl 2025 page and rules (still unreadable); whether the Sports Reference or NFL.com clauses reach nflverse's copies (a legal question; the report leaves it to the owner).
- Whether Kalshi has listed week 8 markets since the pull. Counts are as of 2026-10-23 evening.
- I did not re-fetch the live Sports Reference and NFL.com pages for this pass; the verbatim check is against the Opus review's saved copies, which I opened.

---

## CONFIRMATION (2026-10-24)

**Not yet: three mechanical fixes remain; everything else I asked for is confirmed.** I re-read only the passages my re-check items 1-7 and the nits touch, in the 403-line report.

Confirmed, against the sources and my own recounts:
- **Item 2** (§9.2 limits and the attempts bullet): 205 RB / 86 QB and 5 QB / 1 RB falsifications are correct, and the limits bullet now says QB yardage ladders plus QB and RB attempt ladders.
- **Item 3**: §8.3 and §9.1 now say the contract never mentions rushing and the brackets carry it; the quoted sack sentence is verbatim.
- **Item 4**: §10 labels the RPO counts "NOT a check" (192 vs 72, 310 vs 86); §5 and §8.3 give the 0.9988 its 47-QB population and the 310 vs 86 line.
- **Item 6**: §11 now reads FTN 2022-2025.
- **Item 7**: owner decision 3 now says display does not answer the NFL.com wagering clause and that NGS-charted participation 2016-2022 is already fitted. I checked the extra claim that the rushing model's 2023 rows read 2021-2022 participation: `nfl_rush_profiles.BACK = 2`, so it holds.
- **Item 5, rest**: the seven priced series match `nfl_rush.SERIES`; the TDS/PASSTDS/PASSYDSPROT/FFPLAYERHIGH rows, the 215 receiving-TD-only line, and the RB/FB/HB normalised-name rule are right.
- **Nits**: both §9.1 quotes are verbatim; `product_metadata.important_info` is named; the §9.2 "falsified" header is added; the participation licence now reads CC-BY-SA for the whole file with per-year credits; UNVERIFIED item 8 is struck.

Still wrong:
1. **Corrections bullet, §8.3 line 215 and §9.1 line 276.** The scalar contract does not "say only" that "Revisions to the Underlying made after Expiration will not be accounted for". That sentence is in **both** contracts (I checked `FOOTBALLENTITYSTAT.txt` and `FOOTBALLENTITYSCALARSTAT.txt`), so it cannot be what makes them differ. The sentence that does is in the scalar one only: "Changes to official statistics published before Expiration are included. Changes published after Expiration, including scoring changes issued following the post-game review by the NFL and its official statistician, are not accounted for". *Fix:* quote that in both places.
2. **Season-long rows, lines 259 and 260.** The RB counts 99 and 181 do not reproduce under the report's own rule (normalised name, RB / FB / HB). On my pull and on Opus's saved pull (`review_rush_sources_opus/kalshi_raw/KXNFLSEASONRSHYDS.json`, `..._RSHTD.json`) both give **105 RB rungs (41 RBs)** for RSHYDS and **188 (42)** for RSHTD; the QB counts (4; 19) and the market and event counts are right. *Fix:* 105 and 188.
3. **Line 255**: the KXNFLMOSTRSHYDS note still says `FOOTBALLPLAYERCAT.pdf` was "not read", which contradicts struck item 8 in §13. *Fix:* "read by the Sonnet re-check: same `official attribution` wording, strictly greater than every other member, ties pay 1/N, a non-participant resolves No, no kneel, sack or two-point text".

I did not check the Opus re-check's "matched 304" attempts figure in line 300; it is not one of my items. With fixes 1-3 I would write AGREE TO SHIP.

---

## CONFIRMATION 2 (2026-10-24)

**AGREE TO SHIP.** I re-read the three passages you changed and checked each against the sources.

1. **Corrections (§8.3 line 215, §9.1 line 283).** The scalar-only sentence is quoted verbatim ("Changes to official statistics published before Expiration are included. Changes published after Expiration, including scoring changes issued following the post-game review by the NFL and its official statistician, are not accounted for"), and the note that the generic "Revisions to the Underlying..." sentence is in both contracts is true (checked in both PDFs' text). The binary quote on line 283 is verbatim.
2. **Season-long rows (lines 266-267 and the paragraph at 236-242).** The ranges contain the right numbers. On the saved files, by the rule the report states, QB is 4 (RSHYDS) and 19 (RSHTD) and RB is 105 and 188; counting any position a name matches gives QB 8 and 26 and RB 107 and 195 (I reproduced your 8/107 and 26/195). Market and event counts (116 / 4 and 224 / 6) agree. The collision explanation is real: among these rungs, Josh Allen (C and QB), Lamar Jackson (CB and QB), Aaron Jones Sr. (DE and RB) and Mike Washington Jr. (DB and RB) each match more than one position. The parenthetical about `nfl_matchup._kalshi_lines` is accurate: it is keyed by the game's own suffix and `(stat, norm name)`, so a league-wide collision cannot cross games.
3. **KXNFLMOSTRSHYDS note (line 262).** It matches what I read in `FOOTBALLPLAYERCAT.pdf`: the same "official attribution" wording, strictly greater than every other member, ties pay 1/N, a non-participant resolves No, no kneel, sack or two-point text.

Optional tidy, not a condition: the lower bounds 99 and 181 are Opus's counts, and I could not reproduce them on the saved files under any rule I tried (RB/FB/HB, RB only, any match, with and without retired players); the reproducible bounds are 105-107 and 188-195. Also "a QB and a linebacker can share one" is illustrative; the actual collisions here are with C, CB, DE and DB.
