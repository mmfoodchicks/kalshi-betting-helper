# Opus review: `research/reports/rush_data_sources.md` (RB and QB rushing data sources)

Reviewer: a fresh Opus instance with no stake in the report. 2026-10-23, after 20:50 UTC. Brief:
`<scratchpad>/review_rush_sources_brief.md`. I did not read the Sonnet review (`rush_data_sources_sonnet.md`), which
already existed when I wrote this file.

My only repo write is this file. I made no git writes.

My work is in `<scratchpad>/review_rush_sources_opus/`, called `W` below. `<scratchpad>` is
`/tmp/claude-0/-home-user-kalshi-betting-helper/2a51b1d4-3290-577b-b97b-fbbf27fce813/scratchpad`. `W` holds:
- `pbp_checks.py` and `pbp_checks2.py`: sections 2, 8.1, 8.2 and FTN, recounted from raw pbp, stats and FTN;
- `ladder_check.py`: the section 9.2 settlement test, re-derived with my own code;
- `kalshi_open.py`: the section 9 table, with RB counts;
- `kalshi_raw/`: twelve extra series I pulled from Kalshi's public API, and two `/series/<ticker>` records;
- `web/`: every page I fetched (licences, terms, dictionaries, the 2026 schedule).

I ran everything with `python3 -I` and the csv module. I never used the report's `rush_checks.py` code to produce my
numbers; I only read it.

## 1. Verdict

**AGREE AFTER FIXES.**

Every count I recomputed from the raw files matches the report: about 70 figures, listed in section 2. The settlement
inference is right, and stronger than the report claims.

The fixes are needed for these reasons:
- The headline action item (week numbering) raises an ambiguity that does not exist.
- Several "UNVERIFIED" items are answered by files the author already downloaded, or by public pages that are
  reachable.
- The coding section misses aborted snaps and two-point tries.
- The Kalshi inventory leaves out series that list RBs and QBs.
- The licence section understates the Sports Reference and NFL.com terms.

Fixes required before this ships (details in section 3):
1. **F1** Rewrite section 1, item 1. NFL week 7 is nflverse week 7, and Kalshi's own weekly series calls it "Week 7".
   Week 8 (29 Oct to 2 Nov) is simply not listed yet. No owner decision is needed.
2. **F2** Complete the section 9 inventory:
   - add RB counts;
   - add the pulled but omitted KXNFLRECPROT and KXNFLTDPROT;
   - add the open series that list RBs or QBs and were missed: KXNFLMOSTRSHYDS, KXNFLFIRSTTD, KXNFLFFPTSLADDER,
     the receiving ladder, escalator and protected series.
3. **F3** Use the contract text the author already holds. FOOTBALLENTITYSCALARSTAT states:
   - kneel-downs are negative yardage;
   - sack yardage goes to team net passing;
   - two-point tries credit no rushing statistics.

   Also describe the ladder and escalator series as scalar payouts, not rungs.
4. **F4** Section 8:
   - add aborted snaps: 79 of the 705 "designed runs" are fumbled snaps, and 10 are two-point tries;
   - correct the 10-QB mismatch and give its cause: two-point rows carry `rush_attempt = 1`. Excluding them gives 80
     of 81 exact.
5. **F5** Correct "`defense_man_zone_type` is blank on every run play". Coverage, shell and `was_pressure` are present
   on 1,147 to 1,149 of the 1,149 scramble rows, the exact data the owner's scramble questions need.
6. **F6** List the season-level PFR files (`advstats_season_pass.csv` carries scrambles and RPO rush fields) and the
   weekly `advstats_week_rec`.
7. **F7** Name the free run-concept source (Big Data Bowl 2025 PFF fields) and the cheap charted sources, each with its
   limits and terms status. The claim that no free source exists is not right as stated.
8. **F8** Complete section 12 (licences):
   - quote the Sports Reference AI/ML and database clauses;
   - classify snap counts as PFR data;
   - quote NFL.com's terms (now readable in full);
   - give the pre-2023 participation credit line.

## 2. Claims table

`raw` = `<scratchpad>/rush_src/raw/`, `nv` = `<scratchpad>/data/nflverse/`. The 2025 pbp files in `raw` and `nv`
are the same size (19,105,296 bytes).

### Sections 1-3

| # | Claim (report section) | Verdict | Evidence |
|---|---|---|---|
| 1 | Open rushing events are dated 25 Oct (12) or 26 Oct (DAL-PHI), none 29 Oct to 2 Nov (§1.1, §9) | AGREE | Across the 14 non-empty per-game series in `open_nfl_rush_series.json` (WEEKMOST aside): event date 26OCT25 on 5,391 markets, 26OCT26 on 238, nothing else (`W/kalshi_open.out`) |
| 2 | "Confirm with the owner which numbering is meant" (§1.1) | DISAGREE | nflverse `games.csv` (`W/web/games.csv`, fetched from nflverse/nfldata): 2026 week 7 = NE@CHI 22 Oct, 12 games 25 Oct, DAL@PHI 26 Oct; week 8 = 29 Oct to 2 Nov. Kalshi's `KXNFLWEEKMOSTRSHYDS-26W7` rules: "the most rushing yards across all Pro Football games in Week 7". There is one numbering. See F1 |
| 3 | KXNFLRSHYDS 149 QB rungs, 24 QBs, 13 events; RSHATT 20; LONGRSH 17; TD 45; RRYDS 0 (§1.2, §9) | AGREE | `W/kalshi_open.out`, exact and suffix-normalised name match both give these |
| 4 | Settled brackets: 239/239 consistent including kneels and scrambles; 227 without kneels; 94 without scrambles; 12 and 145 discriminating (§1.3, §9.2) | AGREE, with corrections | My code (`W/ladder_check.out`), 2025-dated events: **253**/253 consistent. The difference is 14 QB-games with no rush play, which the report's code drops because it keys on rush plays only. Removing kneels discriminates in 12 cases and removing scrambles in 145 (reproduced exactly); including both is right in every one. See F3 and m4 for what this can and cannot prove |
| 5 | No free source codes run concepts (§1.4, §2) | DISAGREE | Big Data Bowl 2025 `plays.csv` carries PFF's `pff_runConceptPrimary`. A public tutorial reads it (`run = pla$pff_runConceptPrimary`, `W/web/rpubs_bdb25.html`), and a search snippet of a Kaggle data description names `pff_runConceptSecondary` and `pff_runPassOption`. See F7 |
| 6 | NGS rushing has no QB rows; week-0 rows differ from the weekly sums for 48 of 51 (§1.5a, §6) | AGREE | Positions RB 6,235, HB 45, FB 10. Week 0 equals the weekly sum for 3 of 51. See m2 for the cause |
| 7 | A scramble is `play_type = run`, `rush_attempt = 1` (§1.5b) | AGREE (for 1,149 of 1,221) | The 72 `no_play` scramble rows have `rush_attempt` 0 (67) or blank (5), `qb_dropback` 0 and no rusher id |
| 8 | `run_location` blank on 452 kneels and 128 non-kneel runs; `run_gap` blank on all 3,907 middle runs; top combinations 1,906 / 1,847 / 1,830 / 1,779 / 1,775 / 1,721 (§2) | AGREE | `W/pbp_checks.out` (all six combinations exact) |
| 9 | 2025 participation `offense_formation`: SHOTGUN 22,227, UNDER CENTER 12,145, PISTOL 1,704, blank 9,108 (§2) | AGREE | Recounted from `nv/pbp_participation_2025.csv` |
| 10 | FTN 2025 and 2026 headers identical, 29 columns, no run-concept column (§2) | AGREE | `diff` of the two header lines is empty; the 29 columns are listed in `W/pbp_checks2.out` |
| 11 | `defense_man_zone_type` "is blank on every run play" (§2) | DISAGREE | Filled on 6 of 13,746 joined non-scramble runs, but on **1,147 of 1,149** scramble runs. `was_pressure` is filled on all 1,149 (TRUE 426). See F5 |
| 12 | `defense_man_zone_type` blank on 23,129 of 45,184 rows (§3) | AGREE | Recounted |
| 13 | Label groups (2,4,5) 9,244; (4,2,5) 8,432; (3,4,4) 5,705; (4,3,4) 5,110; (3,3,5) 3,961 (§3) | AGREE | Recounted with the report's position mapping |
| 14 | Box counts agree 45,086 / 45,176; pass rushers 45,173; "the two charted sources can be used together" (§3) | AGREE on the counts; DISAGREE on the framing | `load_participation`: "Participation data from 2023 onwards is courtesy of FTN". For 2025 the two files are one source twice. See m1 |

### Sections 5-7

| # | Claim (report section) | Verdict | Evidence |
|---|---|---|---|
| 15 | PFR rows 2025 / 2026: rush 2,355 / 736, def 7,926 / 2,668, pass 684 / 213; 2026 weeks 1-6 (§5, §11) | AGREE | Recounted; weekly counts in my run |
| 16 | `pfr_id` on 22,696 of 24,861 players; 2,353 of 2,355 rush rows match; yards 330 / 334, carries 332 / 334 (§5) | AGREE | Recounted (carries were not in the report's script) |
| 17 | Season-level `advstats_season_*`: 404, not used (§5) | DISAGREE | `pfr_advstats/advstats_season_pass.csv` and `advstats_season_rush.csv` return 200; only the year-suffixed path is 404. The pass file has `scrambles`, `rpo_rush_att`, `rpo_rush_yards` and more for 2018-2025. See F6 |
| 18 | NGS 6,290 rows 2016-2026; 2026 weeks 0-7, 231 rows, week 7 one row; weekly minimum 10 attempts in 2025; Hunt 163 vs 103, Hubbard 134 vs 96; NGS passing 6,161 rows (§6) | AGREE | Recounted |
| 19 | NGS definitions UNVERIFIED, dictionary not reached (§6) | DISAGREE (reachable) | `nflreadr.nflverse.com/articles/dictionary_nextgen_stats.html` returned 200 and defines `efficiency` ("total distance a player traveled on rushing plays ... per rushing yards gained"), the 8+ defenders share, `avg_time_to_los` and RYOE |
| 20 | NGS and injuries modified 12:54 and 12:34 GMT; stats 14:36; FTN 22 Oct 03:12; players 14:17 (§6, §11) | AGREE | `Last-Modified` headers, all five identical to the report |
| 21 | Injuries 2025: 6,068 rows (REG 5,783, POST 285); report_status Out 1,382, Questionable 1,283, Doubtful 120, blank 3,283; practice_status 3,024 / 1,620 / 1,379 / 45. 2026: 2,124 rows, weeks 1-7 (§7) | AGREE | Recounted. POST = WC 136 + DIV 85 + CON 44 + SB 20 |
| 22 | Depth charts 2026: 689,308 rows, 253 snapshots, 2026-03-22 06:38 to 2026-10-23 12:34 UTC (§7) | AGREE | Recounted from `raw/depth_charts_2026.csv` |
| 23 | Snap counts 2026: weeks 1-6 1,301-1,502 rows, week 7 92; OL labels OL, T, G, "C not checked" (§7) | AGREE; C resolved | Weekly rows 1,492 / 1,502 / 1,494 / 1,482 / 1,400 / 1,301 / 92; C appears on 240 rows |

### Section 8

| # | Claim (report section) | Verdict | Evidence |
|---|---|---|---|
| 24 | Kneels 453 (452 qb_kneel + 1 no_play), yards -1 on 385 and -2 on 32; spikes 82 (80 + 2); sacks 1,352, all `pass`, no rusher id (§8.1) | AGREE | `W/pbp_checks.out`. All 1,352 sacks have a passer id; no run play's description says "sacked" |
| 25 | QB designed runs 705 (REG); 1,792 = 1,087 + 705 (§8.1) | AGREE on the count; DISAGREE on the label | Of the 705, 79 are aborted snaps (`aborted_play = 1`, 76 of them for 0 yards) and 10 are two-point tries. Only 616 are neither. See F4 |
| 26 | Aborted plays (brief item) | MISSING in report | 91 rows flagged in 2025: 89 `run` with `rush_attempt = 1` (81 with a QB rusher, 8 with another rusher) and 2 `no_play` |
| 27 | FTN `is_rpo` TRUE 2,148 (run 1,657, pass 436, no_play 55); `is_qb_sneak` 341, 262 on REG designed QB runs; no "sneak" in any description; RPO proxy 74 of 705 and 12 of 1,087 (§8.1, §8.3) | AGREE | `W/pbp_checks2.out` |
| 28 | Stats file 81 QBs, 2,215 carries, 9,686 yards; pbp 2,225 and 9,683; 71 of 81 exact with kneels, 13 of 81 without (§8.2) | AGREE | `W/pbp_checks.out` |
| 29 | "Remaining 10 QBs: pbp has exactly one more carry ... each; cause not identified" (§8.2) | DISAGREE | Eight QBs have +1, one has +2, and one (Daniel Jones, 00-0035710) has equal carries but 3 fewer yards. The cause is 10 two-point runs coded `rush_attempt = 1` with blank `rushing_yards`; excluding `two_point_attempt` gives 2,215 carries and **80 of 81** exact. The last gap is one aborted snap: week 7, pbp `rushing_yards` 0 against `yards_gained` 3 |
| 30 | 2026 rulebook has no statistics rule (§8.3) | AGREE | `rush_src/rulebook/rb.txt`: 0 matches for "statistic" |
| 31 | Kneels-as-rushing and sacks-not-rushing are UNVERIFIED (§8.3, §13 items 1-2) | DISAGREE (answered by a file the author holds) | `rush_src/kalshi/FOOTBALLENTITYSCALARSTAT.txt`, quoted in F3 |
| 32 | `read_thrown` SD on runs 1,022 and passes 1,419; CHK on passes 2,447; DES on passes 1,788; meanings UNVERIFIED (§8.4) | AGREE on counts; meanings readable | `dictionary_ftn_charting.html`: "“CHK” = checkdown, “DES” = designed read (throws with no progression, e.g. screens and many RPOs), “SD” = scramble drill (throw made after the play broke down)". SD sits on 1,022 runs, all of them nflverse scrambles (0 on non-scramble runs) |
| 33 | `n_defense_box` 0 on about 24% of rows; meaning UNVERIFIED (§8.4, §13.6) | AGREE (23.7%, 11,235 of 47,316); meaning answered in practice | The zeros sit on non-scrimmage rows: no_play 3,212, kickoff 2,927, punt 2,042, extra point 1,330, field goal 1,140, kneel 452, spike 80. Only 36 are on pass plays |

### Section 9

| # | Claim (report section) | Verdict | Evidence |
|---|---|---|---|
| 34 | `/series?category=Sports` 4,046 series, 388 with NFL in the ticker (§9) | AGREE | `rush_src/kalshi/series_sports.json` |
| 35 | Open and event counts per series: RSHYDS 534 / 13, RSHYDSPROT 534 / 13, RSHATT 89 / 12, RRYDS 81 / 12, RECYDS 1,199 / 13, REC 831 / 12, TD 576 / 13, PASSYDS 211 / 13, PASSATT 66 / 12, LADDER 26 / 12 (8 QBs), ESCALATOR 26 / 12 (8), LONGRSH 49 / 12, WEEKMOST 20 / 1 (§9) | AGREE | `W/kalshi_open.out` |
| 36 | 19 RSHYDS and 3 RSHATT rungs unmatched (Aaron Jones Sr., Pierre Strong Jr.) (§9) | AGREE | 12 + 7 and 3. Both are RBs, so the QB counts are unaffected |
| 37 | KXNFLWEEKMOSTRSHYDS "W7" matches neither numbering (§9, §13.11) | DISAGREE | Rules: "across all Pro Football games in Week 7". `expected_expiration_time` 2026-10-27T00:15Z; the 11-10 date is `close_time` |
| 38 | BUF, CHI, JAX, LAC, NE, WAS have no open event; reason UNVERIFIED (§9, §13.12) | AGREE; reason resolved | Week-7 byes in nflverse `games.csv`: BUF, JAX, LAC, WAS |
| 39 | Which series list RBs and QBs (brief item 4) | INCOMPLETE | No RB counts anywhere. Series that list RBs or QBs are missing from the table. See F2 |
| 40 | Product text for RSHYDS and KXNFLTD quoted (§9.1) | AGREE (exact) | Both sentences are verbatim in `product_metadata.important_info` of `/series/KXNFLRSHYDS` and `/series/KXNFLTD` (`W/kalshi_raw/series_*.json`). The market `rules_secondary` reads "rushing yards recorded" instead, so cite the source (n2) |
| 41 | FOOTBALLENTITYSTAT: underlying "the official attribution of <stat> ... recorded in the league's box score and/or play-by-play"; source order; one play; fair price; nullified play counts; final box score (§9.1) | AGREE in substance | The quoted string splices two sentences: "The Underlying ... is the official attribution of <stat> in <time period> of <game>." and "<stat> refers to an official football statistic as recorded in the league's box score and/or play-by-play". The text also says "Official stat corrections made by the league after the game will not affect the value of the Contract", which is stronger than "post-expiry revisions are ignored" (m3) |
| 42 | NFLENTITYSTATCOVERED: covered settlement, NFL first, above / at least only (§9.1) | AGREE | `NFLENTITYSTATCOVERED.txt` lines 6 and 80-89 |
| 43 | FOOTBALLENTITYSCALARSTAT: above / at least definitions; "a floor strike of 19.5 therefore means 20 or more" (§9.1) | AGREE on the definitions; DISAGREE on the use | The floor-strike sentence describes the BINARY rungs. The ladder and escalator series this PDF governs are scalar payouts. See F3 |
| 44 | Settled files carry yes/no only; `expiration_value` empty (§1.3, §9.2) | AGREE | The scratch file has no such key. On the API, a historical-tier market returns `expiration_value` "" and a live-tier market returns the label `"rushing_yards"`, never a number |

### Sections 10-12

| # | Claim (report section) | Verdict | Evidence |
|---|---|---|---|
| 45 | The ninth Big Data Bowl "uses Combine tracking, not game tracking" (§10) | DISAGREE | sportsvideo.org, 2026-10-08: it "combines NFL Scouting Combine data with in-game player tracking data" |
| 46 | §11 coverage cells | AGREE (8 checked) | Spot-checked against the files: pbp 16/16/16/16/15/14 games plus 1 in week 7, last date 2026-10-22; FTN 15,904 rows, weeks 1-6; PFR 736 / 2,668 / 213; stats week 7 holds only CHI and NE; injuries weeks 1-7; snap counts; NGS; players 24,861 / 22,696 |
| 47 | Participation 2026: HTTP 404, loaded after the postseason (§1.6, §11) | AGREE | `pbp_participation_2026.csv` 404, 2025 200. `load_participation`: "provided after all post-season games are completed" |
| 48 | nflverse-data LICENSE is CC BY 4.0 (§12) | AGREE | `raw.githubusercontent.com/nflverse/nflverse-data/master/LICENSE.md` begins "Attribution 4.0 International" |
| 49 | FTN: CC BY-SA 4.0, credit "FTN Data via nflverse" (§12) | AGREE | `load_ftn_charting`: "This data is released under the CC-BY-SA 4.0 Creative Commons license and attribution must be made to FTN Data via nflverse" |
| 50 | PFR / Sports Reference terms: automated access needs permission; reuse with credit welcomed (§12) | DISAGREE (incomplete) | See F8 |
| 51 | NGS terms UNVERIFIED (§12) | PARTLY RESOLVED | NFL.com Terms and Conditions (Updated May 16, 2024), fetched in full. See F8 |
| 52 | Kalshi: Developer Agreement s.3.1 and Data Terms s.I; the report never tells anyone to commit raw prices (§12) | AGREE | s.3.1 read in `data/kalshi_terms/dev_agreement.txt` ("except for purposes of facilitating your own trading on Kalshi"). The report holds tickers and counts only; no price appears in it |

## 3. Findings by severity

No BLOCKER.

### MAJOR

**F1. The first action item invents a numbering problem.**

Section 1, item 1, says the brief's "week 8 prospective" window "names markets that are not open at the pull" and asks
the owner to "confirm which numbering is meant". There is one numbering:
- nflverse's schedule puts 22-26 Oct in week 7 and 29 Oct to 2 Nov in week 8;
- Kalshi's own weekly series is `-26W7` and says "Week 7";
- the repo's CLAUDE.md already calls this weekend week 7.

A prospective window opens in the future by design, so its markets are not listed yet. Section 9 compounds the error
by saying W7 "does not match either numbering", and section 13 keeps both points (items 11 and 12) as UNVERIFIED.

*Fix:* replace item 1 with this:

> Open markets are week 7 (NE@CHI played 22 Oct; byes BUF, JAX, LAC, WAS). Week 8 (CAR@GB, Thu 29 Oct, to CHI@SEA,
> 2 Nov) is not listed yet. The frozen captures start with week 8 as the brief says.

Drop section 13 items 11 and 12.

**F2. The Kalshi inventory is incomplete and has no RB column.**

The report is about RB and QB rushing, but the section 9 table counts only QB rungs. My counts from the same pull
(`W/kalshi_open.out`), as RB rungs (RBs):

| Series | RB rungs (RBs) |
|---|---|
| RSHYDS / RSHYDSPROT | 365 (38) |
| RSHATT | 66 (22) |
| RRYDS | 78 (26) |
| REC | 172 (28) |
| RECYDS | 193 (30) |
| TD | 133 (58) |
| LADDER and ESCALATOR rushing yards | 17 each |
| LONGRSH | 31 |
| WEEKMOST | 19 |

Two series were pulled into `open_nfl_rush_series.json` and then left out of the table:
- KXNFLRECPROT: 831 open, 28 RBs;
- KXNFLTDPROT: 576 open, 24 QBs and 58 RBs.

Open series the author did not pull, which I fetched from the public API (`W/kalshi_raw/`):

| Series | Open markets | Events | QB rungs | RB rungs | Note |
|---|---|---|---|---|---|
| KXNFLMOSTRSHYDS | 33 | 9 | 9 | 23 | Most rushing yards in a game. A rushing market the report never names |
| KXNFLFIRSTTD | 337 | 13 | 24 | 68 | |
| KXNFLFFPTSLADDER | 59 | 12 | 8 | 17 | |
| KXNFLLADDERREC, KXNFLLADDERRECYDS, KXNFLESCALATORREC, KXNFLESCALATORRECYDS | 58 each | | | 17 each | |
| KXNFLRECYDSPROT | over 1,000 (more pages) | | | 26 RBs on the first page | |

Series with nothing open: KXNFL1HTD, KXNFL2HTD, KXNFL2TD, KXNFLRECYDSH2H.

*Fix:*
- add an RB column;
- add these series to the table;
- name the contract each one cites. MOSTRSHYDS and WEEKMOSTRSHYDS cite `FOOTBALLPLAYERCAT.pdf`, which was not read.

**F3. The author held the contract text that settles kneels, sacks and two-point tries, and still marked them UNVERIFIED.**

`rush_src/kalshi/FOOTBALLENTITYSCALARSTAT.txt` (the ladder and escalator contract) says:

> "Each statistic has the meaning, and is credited in the manner, prescribed by the NFL's official scoring rules for
> statisticians"

> "Yardage statistics are net of negative-yardage plays (including kneel-downs) and may be negative."

> "Sack yardage is deducted from a club's net passing yards and total net yards. It is not deducted from a player's
> passing yards, which are the player's individual gross passing yards."

> "No rushing, passing, or receiving statistics are credited on an extra-point or two-point conversion attempt."

That is Kalshi's own statement of the NFL scoring convention, for the scalar series. The binary RSHYDS contract says
only "official attribution", so for those markets the bracket test remains the evidence.

The bracket test also answers sacks. Re-derived (`W/ladder_check.out`), adding sack yards to rushing breaks 111 of 253
ladders in 2025, and the sacks-excluded total is right in all 111.

The test reaches the same verdict on two more periods:

| Period | QB ladders consistent | Kneels discriminating | Scrambles discriminating | Sacks discriminating |
|---|---|---|---|---|
| Jan-Feb 2026 included | 290 / 290 | 15 | 169 | 131 |
| 2026 weeks 1-6 | 178 / 178 | 14 | 119 | 101 |

The 2026 run is a settlement-convention check on the current contracts only: bracket containment counts, no rate,
residual or model figure.

Section 9.1 also misdescribes the ladder and escalator series as rungs. They are scalar:
- **KXNFLLADDERRSHYDS**: "$0.0025 per rushing yard ... cap at 400 yards". "zero yards pay Yes $0.00", and by the
  Methodology a value below the lowest bucket pays the floor.
- **KXNFLESCALATORRSHYDS**: "Payout Formula = floor(10000 x (y / 200)^3) / 10000; y = min(200, max(0, 10 x floor(s /
  10)))", and "Negative rushing yard totals pay Yes $0.00".

So a QB's kneels and negative games price differently in these series than in the binary rungs.

*Fix:*
- quote the four clauses above in section 8.3 and section 9.1;
- move kneels and sacks from UNVERIFIED to "Kalshi contract text (scalar series) plus bracket evidence (binary
  series)";
- add the sack row to the section 9.2 table;
- describe the ladder and escalator payoffs;
- attach the "19.5 means 20 or more" sentence to the binary FOOTBALLENTITYSTAT rungs, where it belongs.

**F4. Section 8 misses aborted snaps and two-point tries, and misstates the reconciliation.**

The brief named aborted plays; the report never mentions `aborted_play`. In 2025:
- 91 rows are flagged;
- 89 of them are `run` with `rush_attempt = 1`;
- 81 of those have a QB rusher.

The "QB designed run" row (`qb_scramble = 0` and the QB is the rusher) therefore holds:
- 79 fumbled snaps (76 for 0 yards);
- 10 two-point tries;
- only 616 true designed runs.

For an attempts market (RSHATT) and for "designed attempts per game" this is an 11-13% contamination. The stats file
counts aborted snaps as carries: excluding them drops the exact matches from 80 to 40 of 81.

Section 8.2's "exactly one more carry each; cause not identified" is wrong on both counts:
- eight QBs are +1, one is +2, and one differs by yards only;
- the cause is 10 two-point rows with `rush_attempt = 1` and blank yards. Excluding them reconciles 80 of 81;
- the last QB (Daniel Jones) differs by one aborted snap.

*Fix:*
- add an aborted-snap row to section 8.1;
- define designed runs as `play_type = run`, QB rusher, `qb_scramble = 0`, `aborted_play = 0`,
  `two_point_attempt = 0`;
- state in section 1, item 5, that `rush_attempt = 1` is also set on two-point tries (the contract text agrees they
  credit nothing);
- replace the section 8.2 last row and drop section 13 item 9.

What no Kalshi file in hand can test: whether Kalshi's attempts markets count aborted snaps. No settled KXNFLRSHATT
history is in scratch. Aborted yards are almost always 0, so the yards ladders cannot tell: one 2025 ladder has nonzero
aborted yards. Say so.

**F5. Coverage and pressure exist on scrambles; the report says the opposite.**

Section 2 says `defense_man_zone_type` "is blank on every run play". Scrambles are run plays in pbp (the report's own
trap, section 1, item 5b). On the 1,149 scramble runs of 2025:
- `defense_man_zone_type` is filled on 1,147 (ZONE 788, MAN 359);
- `defense_coverage_type` is filled on 1,147 (COVER_3 291, COVER_2 273, COVER_1 272, COVER_4 120, ...);
- `was_pressure` is filled on all 1,149 (TRUE 426);
- `number_of_pass_rushers` and `defenders_in_box` are filled on all 1,149.

The owner asked for scrambles against pressure, blitz, man, zone, and single- and two-high coverage. This is that data.
It runs 2016-2025 only (no 2026 participation). FTN's `is_qb_out_of_pocket`, `n_blitzers`, `n_pass_rushers` and
`read_thrown = "SD"` carry 2026; SD marks 1,022 of the 1,149 scrambles and no other run.

*Fix:* say coverage is blank on designed runs (filled on 6 of 13,746) and present on scrambles. List `was_pressure` in
section 3. Name the 2026 gap.

**F6. The season-level PFR files exist and carry scrambles and RPO rush counts.**

Section 5 calls `advstats_season_*` "404 for 2025 at the tried path ... Not used". The real path is one file per stat
type, all seasons in it:
- `pfr_advstats/advstats_season_pass.csv`: 200, 2018-2025, 848 rows;
- `pfr_advstats/advstats_season_rush.csv`: 200, 2018-2026.

`load_pfr_advstats` warns "some data is only available at the season level". The pass file has `scrambles`,
`scramble_yards_per_attempt`, `rpo_plays`, `rpo_yards`, `rpo_pass_att`, `rpo_rush_att`, `rpo_rush_yards`,
`pa_pass_att` and `pocket_time`. 2025 sums:

| Field | 2025 sum | Compare |
|---|---|---|
| scrambles | 1,091 | nflverse 1,087 REG QB scramble runs |
| rpo_rush_att | 310 | FTN RPO-flagged QB runs: 74 designed + 12 scrambles |
| rpo_rush_yards | 1,514 | |

The two RPO counts cannot both mean "QB keeps"; PFR's definition is UNVERIFIED. Section 8.3's "No free read-option flag
exists" stays true, but the report presents FTN as the only RPO source when there is a second one.
`advstats_week_rec_<Y>` (200) is also omitted; it matters for the RB receiving role.

*Fix:* add both season files and the weekly rec file to section 5, with the RPO caveat.

**F7. Run concepts: a free charted source exists, though narrow, and cheap ones were not named.**

Big Data Bowl 2025 `plays.csv` carries PFF's `pff_runConceptPrimary`:
- an R tutorial on the 2025 data reads it from `plays` (`run = pla$pff_runConceptPrimary`, rpubs, `W/web/`);
- a Kaggle data-description snippet (search result) also lists `pff_runConceptSecondary`, `pff_runPassOption`,
  `pff_passCoverage` and `pff_manZone`.

It is free with a Kaggle account but covers one partial past season; which weeks, I did not open. So it cannot feed
2023-2026 features. At most it could test how well the `run_gap` / `run_location` proxy separates zone from gap.

Licence: UNVERIFIED. Kaggle's rules page renders by script (WebFetch returned only the title), and the NFL's Big Data
Bowl terms URL returned 404.

Cheap paid leads, all from search results (secondary):
- PFF publishes run-concept splits ("Best offensive line units by run concept from 2016"); the earlier report holds
  PFF's prices and its betting clause;
- Fantasy Points publishes 2021-24 concept splits;
- Reception Perception advertises RB charting that classifies man/gap versus zone.

*Fix:*
- change section 1, item 4, and section 2 to: "no free source covers 2023-2026";
- name the BDB 2025 fields with their window and unverified terms;
- list the paid leads with "terms not read".

**F8. Section 12 understates the terms that bind PFR, snap-count and NGS data.**

*Sports Reference* (`sports-reference.com/termsofuse.html`, "Last Updated: May 19, 2023"; robots.txt does not disallow
it). Besides the reuse-with-credit principle, section 5 bars:
- use of Content "to create any database, archive, or other data store that competes with or constitutes a material
  substitute for the services or data stores offered on the Site";
- use of Content "for purposes of training, fine-tuning, prompting, or instructing artificial intelligence models or
  technologies in any manner, including without limitation for purposes of ... (ii) supporting machine learning
  methods used to predict, classify, label, or score inputs into the models".

A rushing model fitted on PFR's contact split, built and read by AI agents, sits close to that clause.

The automated-access clause is narrower than the report says: it adds "in a manner that adversely impacts site
performance or access".

nflreadr says snap counts are "provided by Pro Football Reference", yet section 12 files `snap_counts` under the
nflverse CC BY 4.0 row.

*NFL.com Terms and Conditions* (`nfl.com/legal/terms/`, "Updated: May 16, 2024"; robots.txt does not disallow
`/legal/`):
- 1.3: "You may use the Services solely for your own individual non-commercial and informational purposes only";
- also 1.3: "Systematic retrieval of data or other content from the Services ... to create or compile ... a database
  ... is prohibited absent our express prior written consent";
- elsewhere: "you are prohibited ... from using the Services to gamble or wager on the result of any NFL game".

Whether these follow the nflverse-hosted NGS and pbp copies is not settled by the text, but they belong in front of the
owner, as the NBA.com gambling clause was in the earlier report.

*Participation before 2023*: `load_participation` requires the credit "NFL NextGenStats via nflverse (for 2022 and
earlier)". Section 12 names only the FTN credit.

Section 12 lists NGS in the CC BY 4.0 row and again as UNVERIFIED (m7).

*Fix:*
- quote these clauses;
- move snap counts to the PFR row;
- add the pre-2023 participation credit;
- add "PFR-derived features in a predictive model" and "NFL-sourced data for wagering" to the open owner decisions.

### MINOR

- **m1. Box agreement is one source counted twice.** From 2023 the participation file is FTN's data
  (`load_participation`), so 99.8% agreement does not corroborate anything. *Fix:* say so, and say the zero box counts
  are non-scrimmage rows (claim 33).
- **m2. NGS week 0 is the full season.** Week-0 attempts equal nflverse REG carries for 50 of 51 players. The weekly
  rows differ because only games with 10+ attempts appear. Season rows exist for only 51 players (minimum 92
  attempts). *Fix:* add the mechanism; it tells the builder which figure to use.
- **m3. Two corrections clauses.** The binary contracts also say "Official stat corrections made by the league after
  the game will not affect the value of the Contract". The scalar contract has the same rule, with "Changes published
  after Expiration ... are not accounted for". nflverse may carry later corrections. 253 of 253 suggests the gap is
  rare, but name it. Also split the spliced underlying quote (claim 41).
- **m4. Window and selection of the section 9.2 test, and what it cannot prove.**
  - Window: the settled file starts in October 2025 (25OCT is the first 2025 month), and the regex drops January and
    February 2026. The report should state Oct-Dec 2025 and 239 of 239, and say that QB-games with no rush play were
    dropped (with them, 253 of 253).
  - Limits:
    - it reads brackets, not values;
    - it covers QB ladders only (no RB ladders, laterals or fumbles);
    - it covers neither attempts nor longest rush;
    - it cannot separate nflverse from the NFL feed, because they are the same data;
    - conventions that rarely move yards (aborted snaps, two-point tries) are untestable on yardage.
- **m5. The ninth Big Data Bowl is misdescribed** (claim 45).
- **m6. Several UNVERIFIED items are answered by public nflreadr dictionaries** (claims 19, 32, 33):
  - `dictionary_depth_charts`: `pos_slot` is "A number assigned to each position in a formation";
  - `dictionary_participation`: `defenders_in_box` is "Number of defensive players lined up in the box at the snap"
    (depth still undefined);
  - FTN's `is_rpo` is defined only as "play is considered run-pass option".

  *Fix:* update section 13.
- **m7. Section 12 contradicts itself on NGS** (CC BY 4.0 row and UNVERIFIED row).
- **m8. Section 8.1 table cells.** The scramble row's `rush_attempt = 1`, `qb_dropback = 1` and "Rusher id QB" hold for
  the 1,149 run rows only (claim 7). Also, two scramble runs in the 2025 regular season (1,089 against 1,087) belong to
  players not listed as QBs and are excluded without a word.
- **m9. Reproducibility.** "Every headline number below is reproduced by `rush_checks.py`" is not true. That script
  does not compute, for example:
  - LONGRSH 17, the LADDER / ESCALATOR 8, WEEKMOST;
  - carries 332;
  - RPO 74 / 705 and 12, sneak 262;
  - the label groups;
  - man/zone 23,129;
  - injuries, depth charts and snap counts.

  The script also lives only in scratch. *Fix:* narrow the sentence, or commit the script under `research/`. It
  reads, and never writes, Kalshi data.

### NIT

- **n1. Coverage understated.** Snap counts start in 2012, injuries in 2009 and depth charts in 2001 (nflreadr pages).
  PFR weekly pass files exist from 2018 (`advstats_week_pass_2018.csv` 200). pbp predates 2016. None of this changes
  2023-2026 work.
- **n2. Cite the product-text source.** Say the two quotes come from the series `product_metadata.important_info`, not
  from market rules (claim 40).
- **n3. Name the API path.** The KXNFLANYTD series cites `FOOTBALLARCHIVED.pdf`, so "exists; no open markets" is
  expected. The same applies to the season files the report "tried": name the URL that was 404.

## 4. What I could not check

- The Kaggle Big Data Bowl 2025 rules and data page: it renders by script. I also could not confirm which 2022 weeks it
  covers, or whether `pff_runConceptSecondary` and `pff_runPassOption` are there; I have only the search snippet for
  those.
- The NFL's Big Data Bowl terms URL (404).
- ftndata.com: 403 from here, so neither FTN's paid fields nor whether FTN charts run concepts.
- SIS DataHub contents.
- The NFL Official Statistics Manual (not public).
- PFR's glossary (I did not retry PFR, which returned a Cloudflare 403 earlier).
- Football Outsiders' line-yard weights.
- The definition of PFR's `rpo_rush_att`.
- `FOOTBALLPLAYERCAT.pdf`, the contract for MOSTRSHYDS and WEEKMOSTRSHYDS (not fetched).
- Kalshi's attempts-market settlement for aborted snaps and two-point tries: no settled KXNFLRSHATT history in hand.
- Whether the NFL.com terms or the Sports Reference terms bind nflverse-hosted copies: a legal question; the texts are
  quoted above.
- Pages of KXNFLRECYDSPROT beyond the first 1,000 markets (one cursor not followed).

## RE-CHECK (2026-10-24)

I re-read the corrected report in full and read the Sonnet review. For every figure the correction added, I re-derived
it from the raw files or re-fetched the source. The new scripts and pulls are in `W`:
- `ladder_check2.py`: one-sided ladders, bracket width, the aborted-snap ladder;
- `att_check.py`: the attempts test;
- `kalshi_raw/KXNFLRSHATT_settled.json`, `kalshi_raw/KXNFLSEASON*.json`;
- `web/dc_2024_head.csv`, `web/dc_2025_head.csv`.

As before, I made no git writes and edited no other file.

### Verdict: AGREE AFTER FIXES

All eight of my findings are fixed in substance, and nothing the correction added misquotes a number. Six small fixes
remain:
- R1-R3 correct a wording or an omission against a source;
- R4-R6 complete a fix that is only part done.

None of them changes a conclusion. Each takes one sentence or one table row.

### My findings: status

| Finding | Status | Checked against |
|---|---|---|
| F1 week numbering | FIXED | §1.1 matches the schedule and Kalshi's `-26W7` text; §13 drops the items |
| F2 Kalshi inventory | FIXED, except the season series (R4) | Every RB count and every missed-series row in §9 matches `W/kalshi_open.out` and `W/kalshi_raw/`. KXNFLMOSTRSHYDS is filed under `FOOTBALLPLAYERCAT.pdf`, which is right: the series record names that contract. The Sonnet review's "FOOTBALLENTITYSTAT" for it is not |
| F3 contract text | FIXED, one paraphrase wrong (R1) | The four §8.3 quotes are verbatim. The ladder and escalator quotes in §9 are verbatim (the ladder one with an ellipsis) |
| F4 aborted snaps, two-point tries | FIXED | 616 + 79 + 10 = 705, and 76 of the 79 run for 0 yards; 80 of 81 QBs exact; Daniel Jones 0 against 3 |
| F5 coverage on scrambles | FIXED; the 2026 gap is unstated (N1) | 6 of 13,746; 1,147 of 1,149; `was_pressure` 1,149 (TRUE 426) |
| F6 PFR season files | FIXED, two details open (R5) | Path and fields right |
| F7 run concepts | FIXED | BDB row, Razzball and the paid leads labelled as leads |
| F8 licences | FIXED | Every SR and NFL.com quote in §12 is verbatim against `W/web/sr_terms.html` and `W/web/nfl_terms.html`. Dates right ("Last Updated: May 19, 2023"; "Updated: May 16, 2024"). Snap counts moved to the PFR row; the pre-2023 credit added. The default is discussed under R6 |
| m1-m9, n1-n3 | FIXED, apart from the spliced quote (N2) and the provenance sentence (R3) | |

### Figures the correction added, re-derived

**§1.3 and §9.2, yardage brackets** (`W/ladder_check2.py`). Every cell reproduces:
- 253 ladders, 124 two-sided and 129 one-sided;
- falsified counts of 12, 145, 111 and 0;
- 290 ladders with 15, 169 and 131;
- 178 ladders, 118 two-sided and 60 one-sided, with 14, 119, 101 and 1;
- median two-sided width 10 yards in 2025. In 2026 it is 5 yards, so say "2025".

The Caleb Williams ladder is exact: `KXNFLRSHYDS-26SEP20MINCHI`, bracket 39.5 to 49.5, total 42, of which 17 come from
the aborted snap in `2026_02_MIN_CHI`. Without it the total is 25, outside the bracket.

**§1.3 and §9.2, attempts.** I pulled today's 1,000 settled KXNFLRSHATT markets (Sep-Oct 2026) and matched 304
ladders; Sonnet matched 291 from its pull. Counting every rush attempt fits 304 of 304. The falsified counts are
identical to Sonnet's:

| Convention dropped | Ladders falsified |
|---|---|
| kneels | 20 |
| aborted snaps | 6 |
| scrambles | 57 |

The report's figures are Sonnet's, and they stand.

**§1.4 and §3, base front.** The 2026 file reproduces: 19 teams at "Base 3-4 D" and 13 at "Base 4-3 D" in the last
snapshot; two teams changed (NYJ, SF). The 2025 file carries the same `pos_grp` labels, so "2025 and 2026 files" holds.
On "whether earlier seasons carry it":
- the complete 2024 file (37,311 rows) has a different layout;
- its `formation` is only "Defense", "Offense" or "Special Teams", with no base label.

So the label cannot feed a 2023-2024 fit (R6b).

**§4 and §7, snap counts.** The 2025 labels reproduce: OL 1,457, T 1,363, G 925, C 668. "A 100% player exists in 543
of 544 2024 team-games" holds for the regular season only; with the postseason it is 569 of 570. Say "regular-season"
(N3).

**§6.** "Week 0 equals nflverse regular-season carries for 50 of 51" is right. The exception is Breece Hall: NGS 242,
stats 243. The Sonnet review's "all 51" is one off.

### Remaining fixes

**R1. §9.1 and §8.3 make the scalar contract say something it does not.**
- §9.1 says the contract states "sacks come off team passing and not a player's rushing".
- §8.3 says "**Sacks are not rushing.** The same contract."

The text says sack yardage "is deducted from a club's net passing yards and total net yards. It is not deducted from a
player's passing yards". It never mentions rushing. The rushing conclusion rests on the brackets: 111 of 253, and 101
of 178.

*Fix:* in §9.1 write "sacks come off the club's net passing yards (the text does not mention rushing)". In §8.3 write
"implied by the same contract (sack yardage is charged to club net passing); shown by the brackets". §1.3's sentence
("deducted from a club's net passing yards, not a player's") is literally right; leave it.

**R2. §8.4 leaves out the one `read_thrown` code that bears on rushing.**

The dictionary sentence the report quotes omits "SD", which FTN defines as "scramble drill (throw made after the play
broke down)". SD sits on 1,022 run plays, every one an nflverse scramble, and on no designed run. The section header
still says "meanings UNVERIFIED".

*Fix:*
- add the SD definition and the 1,022-of-1,149 overlap;
- retitle the header "values checked; meanings from the nflreadr FTN dictionary";
- the sentence "So the data layer's 'CHK = checkdown' is FTN's own definition" refers to a report this one never
  cites. Say which.

**R3. The provenance sentence (line 5) still overstates the script.**

`rush_checks.py` does not compute most of §2 (`run_location`, `run_gap`), the RPO 74 / 705, the sneak 262, or any
aborted or two-point split. The widened §9.2 rows are the reviewers'.

*Fix:* "`rush_checks.py` reproduces the §8.1 flag counts, §8.2, the first §9.2 table, the §3 box agreement, the §5 PFR
yards and the §6 NGS week-0 check. Every other figure is the author's one-off check or a reviewer's recount, as cited."

**R4. The Sonnet review's season-long rushing series are still missing from §9 and from §1.2.**

I re-pulled both (`W/kalshi_raw/`); both cite FOOTBALLENTITYSTAT.

| Series | Open markets | Events | RB rungs | QB rungs |
|---|---|---|---|---|
| KXNFLSEASONRSHYDS | 116 | 4 | 99 | 4 |
| KXNFLSEASONRSHTD | 224 | 6 | 181 | 19 |

*Fix:* add two rows marked "season-long; not priced".

Add also the KXNFLTD point from the same review: KXNFLTD is all touchdowns, with D/ST selections (my unmatched list
includes "PHI Eagles D/ST"). No per-game rushing-only touchdown series exists.

**R5. Complete F6: the RPO caveat and the scramble correlation.**
- §5 gives "correlation 0.9988 with pbp scrambles" without its population. The Sonnet review computed it over the 47
  QBs of 2025 with 100+ attempts.
- §8.3's RPO bullet still presents FTN as the only RPO source. Add one line: "PFR's season file counts 310 QB RPO rush
  attempts in 2025, against 86 FTN-flagged QB runs (74 designed + 12 scrambles). The definitions differ and PFR's is
  UNVERIFIED; neither is a read-option outcome."
- The season-rush file has 2026 rows (176), so "2018-2025" applies to the pass file only.

**R6. The owner-decision default.**

*Decision 2 (PFR).* Display with credit and no fitted feature is a sound holding position for the SR machine-learning
clause, and it matches the code: `nfl_rush.py` marks PFR, PFR-snap and NGS facts `DISPLAY_ONLY` and carries the credits.

Two sentences would make it accurate:
- **(a)** The same SR clause also names "prompting, or instructing artificial intelligence models". The research
  pipeline's own agents read PFR files. That falls under the same owner decision, and the default does not cover it.
  Say so.
- **(b)** The base-front lead is 2025-2026 only. Under the "fit 2023-2024, choose on 2025" design it can only ever be a
  2025-onward or display-only field. That belongs beside the lead in §1.4 and §3.

*Decision 3 (NGS).* "The same default covers NGS" adopts the PFR remedy by analogy, but the clause cited for NGS is
about wagering ("using the Services to gamble or wager on the result of any NFL game"), not machine learning. If that
clause reaches nflverse's copy, it reaches showing NGS on a betting board just as much as fitting it. It also reaches
pbp and the pre-2023 participation, which is "NFL NextGenStats via nflverse".

*Fix:* write "the default keeps NGS out of fitted features as a conservative choice; it does not by itself answer the
NFL.com wagering clause, which, if it applies, reaches display, pbp and pre-2023 participation alike. That is decision
3."

### NIT (optional)

- **N1.** §2: say the scramble coverage and pressure fields exist for 2016-2025 only. 2026 has no participation file.
  For 2026, FTN's `is_qb_out_of_pocket`, `n_blitzers`, `n_pass_rushers` and `read_thrown = SD` are what remain.
- **N2.** §9.1, first bullet:
  - it still splices two contract sentences into one quote;
  - "Post-expiry revisions are ignored" now sits above the stronger corrections bullet; delete it;
  - the RSHYDS product-text quote should name `product_metadata.important_info`, as the KXNFLTD row now does.
- **N3.** §7: "543 of 544 2024 team-games" should say regular-season. §9.2: the 10-yard median is 2025's (2026: 5).
- **N4.** §9 table:
  - the TDPROT cell "24 QBs" sits in the rung column (its rungs are 45);
  - "Priced by the model" appears only on KXNFLRSHYDS, while `nfl_rush.SERIES` prices seven series: RSHYDS, RSHATT,
    RRYDS, LONGRSH, TD, REC, RECYDS. Label all seven.
- **Outside this report.** The comment above `nfl_rush.SERIES` (lines 66-71) still describes the ladders and
  escalators as "above X" strictly-more rungs. After F3 they are scalar payouts. It is a comment in another
  deliverable; whoever reviews `nfl_rush.py` should correct it.

## CONFIRMATION (2026-10-24)

**AGREE TO SHIP.** I re-read the passages that the R1-R6 and N1-N4 fixes touch; each one now reads as asked:
- **R1**: §8.3 and §9.1 now say the contract text never mentions rushing and that the brackets carry the sack
  conclusion.
- **R2**: SD is defined in §8.4, with its 1,022 overlap with scrambles. The header is retitled and the data-layer
  report is named.
- **R3**: line 5 has the narrowed provenance wording.
- **R4**: the KXNFLSEASONRSHYDS row (116 / 4 / 4 QB / 99 RB) and the KXNFLSEASONRSHTD row (224 / 6 / 19 QB / 181 RB)
  match my pulls. The all-touchdowns / D/ST point and the no-rushing-only-TD point are in.
- **R5**: the 0.9988 population, the 310 vs 86 RPO line, and the rush season file at 2018-2026 (176 rows of 2026).
- **R6**: (a) the "prompting, or instructing" clause and (b) the 2025-2026-only base front are both in. Decision 3
  carries my wording.
- **Nits**: the spliced quote is replaced by two exact quotes ("...for the relevant competition" verbatim),
  "post-expiry" is gone, `product_metadata.important_info` is named, "regular-season" is added, both median brackets
  are given (10 and 5), TDPROT shows 45 rungs (24 QBs), and the seven series in `nfl_rush.SERIES` are named and match
  the code.

Two optional one-line touch-ups, neither blocking:
- §1.2's "Also open" list does not yet name the season-long series. §9 does, and §1.2 points there.
- The changelog bullet on line 11 still says the contract text "settles kneels, sacks and two-point tries". After R1
  that should be "kneels and two-point tries (sacks by the brackets)".
