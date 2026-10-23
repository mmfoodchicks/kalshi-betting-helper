# Matchup engine: data sources

Status: research only. No validation or held-out split was run, so nothing was pre-registered. No git write, no shared file touched, no raw data committed (partial downloads are in the agent scratchpad).

Written 2026-10-23 01:17 UTC (2026-10-22 19:17 MDT). Reach tests ran 00:54-01:13 UTC (18:54-19:13 MDT).

How to read the reach column: `curl -sS -m 30 -o /dev/null -w "%{http_code}"`, unauthenticated, from this sandbox. On a GitHub release URL, 302 means the file exists (redirect to object storage) and 404 means it does not. "WebFetch" means the page was read through the fetch tool, whose quotes are its extracts, not a raw copy. Re-check exact wording before relying on it. Where a page could not be opened, the source is marked as a search snippet or unverified.

---

## 1. Bottom line

1. **Free and reachable now:** nflverse (play-by-play 2016-2026, participation 2016-2025, FTN charting 2022-2026, snap counts, NGS, PFR advanced, players crosswalk); Sleeper NFL and NBA projections and game logs (terms: non-commercial); Kalshi's historical tier for KXNBAPTS (23,562 markets, 2025-11-19 to 2026-06-14).
2. **No 2026 NFL participation file exists** (`pbp_participation_2026.csv` returns 404). nflverse publishes participation only after each postseason. The brief's 2026 weeks 1-7 test therefore cannot use coverage or route from any free source. FTN charting 2026 exists; the 2025 header has no coverage or route columns (2026 header not inspected, assumed the same schema).
3. **NBA defender-matchup data is not reachable from this sandbox** (stats.nba.com: Akamai 403 on the root, no response in 30 s on a stats endpoint; cdn.nba.com: 403). The NBA.com terms, which govern NBA statistics, say those statistics "may not be used in connection with any gambling activity" (section 9(iv)). Whether that reaches stats.nba.com endpoints is unverified, but it must be answered before any NBA stats enter betting-analysis output.
4. **PFF's API is the only matchup-grade paid source I could price; its terms restrict betting-related use.** PFF terms section 14.4, item 7, in full -- the user may not "use API Data to provide, or in connection with, any betting, wagering, daily fantasy, handicapping, tout, picks, or advisory product or service to any other person, or in connection with any sportsbook, exchange, or gaming operator;" The clause bars providing such a product or service *to any other person* and any use *in connection with a sportsbook, exchange, or gaming operator*; whether one person's own analysis for trading on Kalshi (an exchange) falls inside it is not settled by the text. Ask PFF in writing before paying. (Corrected 2026-10-23 after the Fable review, research/reviews/data_sources_and_ui.md A1: the first version quoted the item truncated.)
5. **Kalshi's API terms** limit storage and aggregation of API data to "purposes of facilitating your own trading" and forbid sharing it with third parties (Developer Agreement v1.1, section 3.1). The Kalshi Data Terms require prior written consent for "the development of any software program" (section I). These clauses apply to this app and need an owner decision.

---

## 2. Summary table

| Source | Reach here (UTC) | Licence / terms (what was read) | Price (source) | Use for the matchup engine |
|---|---|---|---|---|
| nflverse release files | 302 for every file in section 3.1, 00:54 | Repo LICENSE.md = CC BY 4.0 text; FTN-sourced participation and charting = CC BY-SA 4.0; NFL-owned data "governed by their terms of use" | Free | Yes: priors 2016-2025; NFL 2026 charting without coverage |
| FTN Data | ftndata.com: curl 502, WebFetch DNS failure; charting.ftntools.com/api/docs 200 (script shell, no content) | Not read | Search snippet only: CSV $599/yr tier, API $5,000/yr tier (unverified) | Only after terms are read |
| NFL Next Gen Stats (public site) | nextgenstats.nfl.com 200 | NFL.com T&C: fetch 403; search excerpt bars automated collection "for any commercial purpose" | Free | Summaries only; use nflverse NGS files |
| Pro Football Focus | www.pff.com 200 | Terms: personal non-commercial; API data barred from betting products for others and from use "in connection with any sportsbook, exchange, or gaming operator" (s.14.4(7), quoted in full in section 4) | Annual $99.99/yr; PFF Pro (API) $199.99/yr (pff.com/subscribe) | Unclear for Kalshi trading; ask PFF |
| SIS DataHub | www.sportsinfosolutions.com 200; store 200 | Terms link not opened | NFL $99.99/mo or $749.99/yr (store page) | Unverified; terms not read |
| TruMedia | trumedianetworks.com/football 200; trumedia.networks curl 502 | Not public | Not public | No |
| Pro-Football-Reference | 403, Cloudflare challenge | Sports Reference terms: automated access needs written permission | Free | Use the nflverse copy |
| ESPN site API | site.api.espn.com 200 | No official API; Disney terms bar scraping of "Disney Products" (ESPN coverage unverified) | Free | Yes for box and play data (already used); no defender fields |
| Sleeper API | api.sleeper.com 200 | Docs: "free to use for non-commercial purposes" | Free (non-commercial) | Yes for projections and game logs |
| stats.nba.com | root 403 (AkamaiGHost); stats endpoint timed out (30 s) | NBA.com terms s.9: no gambling, fantasy or commercial use | Free | Not reachable here |
| cdn.nba.com | 403 | NBA.com terms | Free | Not reachable here |
| pbpstats | www.pbpstats.com 200; api.pbpstats.com get-totals 200 | No licence or terms on docs page | Not stated | Totals and shot-zone stats; terms unverified |
| Basketball-Reference | 200 | Sports Reference terms (same as PFR) | Free | Terms conflict with automated use |
| Cleaning the Glass | cleaningtheglass.com/stats 200 | ToS: no scraping clause; IP reserved | $5/mo or $50/yr (stats page) | Team context; data-use licence not granted |
| Synergy (Sportradar) | www.synergysports.com 302; shop 402 | Not read | NBA $39.99/mo (search snippet, unverified); enterprise not public | Unverified |
| Second Spectrum | 302 | Not public | Not public | No |
| BettingPros | www.bettingpros.com 200; /terms-of-service/ 404 | Unverified | Not public | Unverified |
| nba_api (Python client) | pypi.org 200 | Code MIT; data "subject to the NBA.com Terms of Use" | Free | Client only |
| Kalshi historical API | api.elections.kalshi.com 200; external-api.kalshi.com 200 | Developer Agreement v1.1 s.3; Data Terms s.I | No charge observed; docs silent on price | Yes, prices; subject to s.3 |

---

## 3. NFL

### 3.1 nflverse (free base layer)

- **Access.** `https://github.com/nflverse/nflverse-data/releases/download/<tag>/<file>`. R: nflreadr. Python: nflreadpy. Docs read: https://nflreadr.nflverse.com/reference/load_participation.html and https://nflreadr.nflverse.com/
- **Reach (00:54-01:13 UTC, all 302 unless noted):** `pbp/play_by_play_2026.csv.gz`; `pbp/play_by_play_2016.csv.gz`; `pbp_participation/pbp_participation_2016` through `_2025`.csv (2017 and 2018 also 302); `pbp_participation_2026.csv` **404**; `ftn_charting/ftn_charting_2022`, `_2025`, `_2026`.csv; `snap_counts/snap_counts_2026.csv`; `nextgen_stats/ngs_receiving.csv.gz`, `ngs_passing.csv.gz`; `pfr_advstats/advstats_week_rec_2025.csv`, `advstats_week_def_2025.csv`; `players/players.csv`.
- **Licence.** LICENSE.md is the standard Creative Commons Attribution 4.0 text (https://github.com/nflverse/nflverse-data/blob/master/LICENSE.md); the repo page labels it CC-BY-4.0 (https://github.com/nflverse/nflverse-data). Dataset exceptions are on the dataset pages: participation and FTN charting are "released under the CC-BY-SA 4.0" with credit "FTN Data via nflverse" for 2023 onward (https://nflreadr.nflverse.com/reference/load_participation.html; https://nflreadr.nflverse.com/reference/load_ftn_charting.html). The NGS load page has no licence text (https://nflreadr.nflverse.com/reference/load_nextgen_stats.html). The nflreadr home page says: "NFL data accessed by this package belong to their respective owners, and are governed by their terms of use." (https://nflreadr.nflverse.com/)
- **Open question on paid use.** nflverse-data issue #101 asked whether a paid product may use these files. Per the page, no maintainer answered, and the issue was closed "not planned" on 2 Oct 2026 (https://github.com/nflverse/nflverse-data/issues/101, WebFetch).
- **Cost.** Free.
- **Update and lag.** Play-by-play nightly in season, "usually available within 15 minutes after a game has ended". FTN charting, snap counts and PFR advanced refresh at 0/6/12/18 UTC in season. NGS refreshes nightly, 3-5 am ET (https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html, WebFetch). HEAD Last-Modified: `ngs_receiving.csv.gz` 22 Oct 2026 13:47 GMT; `ftn_charting_2025.csv` 23 Sep 2026; `pbp_participation_2025.csv` 10 Feb 2026 18:54 GMT.
- **Sizes (HEAD).** participation 2025: 49.1 MB; ftn_charting 2025: 8.1 MB; ngs_receiving: 1.0 MB.

### 3.2 Participation (the defender file)

- **Years.** 2016-2025 files exist; nflreadr: "all available data since 2016" (load_participation page). 2026: 404.
- **Source by year.** Pre-2023 from NFL NGS. From 2023, from FTN, "provided after all post-season games are completed"; "It does not update during the season!" (load_participation and schedule pages). "The data source died during the 2023 season" refers to NGS (schedule page).
- **Why 2026 is missing.** Not a stopped feed. The replacement source releases once per season. nflreadr 1.5.0 (CRAN, 2025-09-02): "FTNData.com has graciously offered to provide participation data after each season has ended"; "load_participation() is back, somewhat!" (https://nflreadr.nflverse.com/news/index.html, WebFetch). The 2025 file dates from 10 Feb 2026. The 2026 file should follow the 2026 postseason. That date is inferred from the stated policy; no date is published.
- **Columns (verified in the header of the 2025 file).** nflverse_game_id, old_game_id, play_id, possession_team, offense_formation, offense_personnel, defenders_in_box, defense_personnel, number_of_pass_rushers, players_on_play, offense_players, defense_players (gsis ids), n_offense, n_defense, ngs_air_yards, time_to_throw, was_pressure, route, defense_man_zone_type, defense_coverage_type, offense_names, defense_names, offense_positions, defense_positions, offense_numbers, defense_numbers.
- **Allowed values (dictionary, https://nflreadr.nflverse.com/articles/dictionary_participation.html, WebFetch).**
  - `defense_coverage_type`: COVER_0, COVER_1, COVER_2, 2_MAN, COVER_3, COVER_4, COVER_6, COVER_9, COMBO, BLOWN (10 values; the brief also lists COVER_0/1/2/3/4/6/9, 2_MAN, COMBO, BLOWN, which matches).
  - `route`: 13 values: CORNER, DEEP OUT, GO, HITCH/CURL, IN/DIG, POST, QUICK OUT, SCREEN, SHALLOW CROSS/DRAG, SLANT, SWING, TEXAS/ANGLE, WHEEL.
  - `defense_man_zone_type`: no allowed values listed; the sample shows ZONE_COVERAGE and MAN_COVERAGE.
- **Route definition conflict.** The dictionary says "the route the primary receiver on a play took". The brief says "targeted receiver". Use the dictionary wording until the data says otherwise. The file has no receiver-id column for route, so the dictionary is the only source.
- **Fill rates.** Share of non-blank values in the first 120 KB of each file (week 1 only, 110-270 plays). These are partial samples over ALL plays. **Corrected after the Fable review (A2):** on the full 2025 file (45,184 rows, 100% joined to pbp) the blanks are non-pass plays -- `route` is present on 99.4% of the 17,582 pass attempts with a receiver and 0.0% of the 13,746 runs; coverage on 99.9% of attempts, sacks and scrambles and 0% of runs. The data is essentially complete where it applies. Empirically `route` tracks the TARGETED receiver (SWING 78.7% RBs; SCREEN mean air yards -3.3); from 2023 a route is also charted on 52% of attempts with no target, so the engine reads `route` only where pbp's receiver_player_id is set.

  | Season | defense_coverage_type | route | ngs_air_yards |
  |---|---|---|---|
  | 2021 (262 plays) | 0.50 | 0.48 | 0.48 |
  | 2022 (269) | 0.40 | 0.39 | 0.39 |
  | 2023 (112) | 0.56 | 0.45 | 0 (legacy, 2023 and prior only) |
  | 2024 (110) | 0.47 | 0.40 | 0 |
  | 2025 (111) | 0.48 | 0.38 | not checked |

- **Not in the file.** Which defender covered which receiver. There are only per-play sets (`defense_players`), not an assignment per target.
- **Release page.** https://github.com/nflverse/nflverse-data/releases/tag/pbp_participation (WebFetch) lists 48 assets but the file list did not load. Its release text reads "Participation data for plays from NGS", which conflicts with the FTN source in the docs. Treat it as stale until checked.

### 3.3 FTN charting

- **Years and timing.** 2022 onward; "charted within 48 hours following each game" (https://nflreadr.nflverse.com/reference/load_ftn_charting.html).
- **Columns (verified in the 2025 header).** ftn_game_id, nflverse_game_id, season, week, ftn_play_id, nflverse_play_id, starting_hash, qb_location, n_offense_backfield, n_defense_box, is_no_huddle, is_motion, is_play_action, is_screen_pass, is_rpo, is_trick_play, is_qb_out_of_pocket, is_interception_worthy, is_throw_away, read_thrown, is_catchable_ball, is_contested_ball, is_created_reception, is_drop, is_qb_sneak, n_blitzers, n_pass_rushers, is_qb_fault_sack, date_pulled. **No route column and no coverage column.**
- **Licence.** CC BY-SA 4.0, credit "FTN Data via nflverse" (load_ftn_charting page). Any committed table derived from this file is an adaptation: ShareAlike and attribution apply if it is distributed (owner decision).
- **FTN's own product.** https://www.ftndata.com/pricing could not be opened (DNS failure from WebFetch; curl 502). A search snippet of that page reports a $599 CSV tier and a $5,000 API tier "All Historical Advanced Charting stats (since 2019)", with enterprise by quote. Another snippet attributes a FAQ figure of $5,000/yr commercial and $3,000/yr private. Both are unverified. charting.ftntools.com/api/docs returns 200, but WebFetch got only the title "FTN Charting" (script shell).

### 3.4 Next Gen Stats

- **nflverse file.** `ngs_receiving.csv.gz` columns (streamed header): season, season_type, week, player_display_name, player_position, team_abbr, avg_cushion, avg_separation, avg_intended_air_yards, percent_share_of_intended_air_yards, receptions, targets, catch_percentage, yards, rec_touchdowns, avg_yac, avg_expected_yac, avg_yac_above_expectation, player ids. Receiver side only.
- **Scope.** Passing, receiving and rushing; no defensive table (load_nextgen_stats page, WebFetch). "NGS will only provide data for players above a minimum number of pass/rush/rec attempts."
- **Public site.** nextgenstats.nfl.com returns 200 (curl). Its feeds and licence were not checked.
- **NFL.com terms.** https://support.nfl.com/hc/en-us/articles/35869743904916-NFL-com-Terms-and-Conditions returned 403 to WebFetch. A search excerpt says "Automated collection tools such as spiders or intelligent agents are barred from harvesting information from the Services for any commercial purpose." That is the excerpt only; the page was not read in full.

### 3.5 PFR advanced stats and snap counts

- `advstats_week_rec_2025.csv` (302): game_id, pfr_game_id, season, week, game_type, team, opponent, pfr_player_name, pfr_player_id, rushing_broken_tackles, receiving_broken_tackles, passing_drops, passing_drop_pct, receiving_drop, receiving_drop_pct, receiving_int, receiving_rat, ... `advstats_week_def_2025.csv` (302): columns not inspected.
- `snap_counts_2026.csv` (302): 2026 week 1 rows seen (ARI at LAC): offense_snaps, offense_pct, defense_snaps, defense_pct, st_snaps.
- pro-football-reference.com: 403 with `cf-mitigated: challenge` (Cloudflare) from the sandbox at 00:56 UTC.
- Terms, Sports Reference LLC (https://www.sports-reference.com/termsofuse.html, WebFetch; the page's navigation links Basketball-Reference and Pro-Football-Reference): "without our express written permission, use any automated means to access or use the Site, including scripts, bots, scrapers, data miners". Also: "sharing, using, modifying, repackaging, or publishing data found on individual SRL webpages is welcomed, whether for commercial or non-commercial purposes", with credit. Use the nflverse copy, not scraping.

### 3.6 Pro Football Focus

- **Plans** (https://www.pff.com/subscribe, WebFetch, undated): Monthly $9.99/mo, no Premium Stats; Annual $99.99/yr, Premium Stats; PFF Pro $199.99/yr, Premium Stats Pro and API.
- **API.** https://developer.pff.com/ (WebFetch): "The docs are public, but every data endpoint needs a PFF Pro subscription." The page mentions "player grades and Premium Stats Pro data". It does not mention route, coverage or alignment fields.
- **Naming conflict.** https://www.pff.com/news/a-new-chapter-of-pff-pricing (posted Sep 1, 2026 per the page) calls the $199.99 tier "Elite", "coming soon", with "Programmatic data access for modeling (API/scrape)". Ask PFF which is current.
- **Terms** (https://www.pff.com/terms, WebFetch; "Last Updated: October 12, 2026"):
  - s.1.2: "the PFF Data solely for your own personal, non-commercial use"; the restrictions name data "retrieving it through the API".
  - s.1.5(i): no "machine learning or training of artificial intelligence models or tools".
  - s.14.2: Commercial Use "includes use by or on behalf of any business, employer, team, league, media outlet, sportsbook ... fantasy or gaming operator, data provider".
  - s.14.3: "Nothing in the Agreement grants you any right to use API Data for Commercial Use, for Public Distribution".
  - s.14.4 item 7, in full -- the user may not "use API Data to provide, or in connection with, any betting, wagering, daily fantasy, handicapping, tout, picks, or advisory product or service to any other person, or in connection with any sportsbook, exchange, or gaming operator;"
  - s.1.9 and s.1.10 (the carve-outs for API data) are cross-referenced but not in the fetched text, so their scope is unverified.
- **Read.** The item bars betting/DFS products offered to other people and any use in connection with a sportsbook, exchange or gaming operator; whether personal analysis for trading on Kalshi is covered is not settled by the text (no legal reading here). Premium Stats CSV exports fall under s.1.2 (personal, non-commercial); whether that covers betting use is not stated.

### 3.7 SIS DataHub

- **Prices** (https://store.sportsinfosolutions.com/, WebFetch): NFL $99.99/mo or $749.99/yr; NFL+NCAA $199.99/mo or $1,399.99/yr; NCAA $149.99/mo or $999.99/yr; NFL projections $9.99/wk or $99.99/yr. Features on the NFL plan: Player Leaderboards, Advanced Stats, Total Points, Time Filters, Advanced Filters, Team Leaderboards, CSV Export.
- **Feeds and enterprise.** No price shown; contact info@sportsinfosolutions.com.
- **Route data.** A 2019 SIS post (search snippet) lists route data among proprietary metrics; current inclusion unverified.
- **Terms.** Linked from the store page; not opened.

### 3.8 TruMedia

- https://www.trumedianetworks.com/football (WebFetch): no prices. The NFL API serves "clubs needing to merge multiple datasets together". The platform "merges play-by-play, video and third-party data with player tracking data". No third-party names. Clubs and media are the customers.

### 3.9 ESPN site API

- **Reach.** site.api.espn.com scoreboard and summary return 200 (NBA event 401859967 on 2026-06-13; NFL event 401872933).
- **Payload check.** NBA plays carry `participants` (shooter), `coordinate` (x, y) and `text`. There is no defender field (0 matches for "defender" in the summary). The NFL summary (keys include drives, winprobability, odds) has no coverage or route fields; "coverage" and "route" appear only in prose.
- **Terms.** No official API (third-party guides; not checked with ESPN). The one legal text read was Disney's general terms (https://disneytermsofuse.com/english/, WebFetch, "Last Updated May 24, 2024"), section 2B(x): no "robot, spider, script, or other automated means" to "access, monitor, copy or extract the Disney Products", including for "data mining or web scraping". Whether ESPN's endpoints fall under these terms or ESPN's own is unverified (ESPN's terms URL I guessed returned 404).
- **Existing use.** The repo's `nba_pbp.py` already reads ESPN NBA summaries (in-repo comment).

### 3.10 Sleeper (NFL and NBA)

- **Terms** (https://docs.sleeper.com/, WebFetch): "The Sleeper API is a read-only HTTP API that is free to use for non-commercial purposes." "For commercial use of the Sleeper API, please reach out to us directly." Limit: "stay under 1000 API calls per minute". The projections and stats endpoints are not documented there.
- **NFL projections.** `https://api.sleeper.com/projections/nfl/2026/7?season_type=regular` returns 200, 9,418 rows, every `company` "rotowire".
- **NBA projections, season level.** `https://api.sleeper.com/projections/nba/2026?season_type=regular` returns 200, 2,132 rows, no game_id, opponent null. `.../nba/2025?...` returns 200, 2,132 rows, last updated 2026-07-09 (the snapshot is overwritten, not archived).
- **NBA projections, weekly.** `.../projections/nba/2026/1` returns 200, 1,096 rows dated 2026-10-20, with team, opponent and game_id. `.../2026/2` returns 200, 1,211 rows dated 2026-10-26 to 2026-11-01. Stat keys: pts, reb, ast, fga, fgm, fta, tpa, tpm, stl, blk, to, dreb, ftm, dd, td, gp, sp (`sp` is unlabelled; appears to be a fantasy-points total, unverified).
- **NBA game logs.** `https://api.sleeper.com/stats/nba/2025/10?season_type=regular` returns 200, 1,861 rows dated 2025-12-22 to 2025-12-28, 30 teams, game_id on every row. Team and opponent were checked on the first rows; the position sits in the player object (first row checked). `company` is "sportradar". Stat keys include pts, reb, ast, fga, fgm, fta, tpa, oreb, dreb, plus_minus, and quarter and half splits (q1_pts ... h2_reb). Week 10 also returns 200 for 2024 (1,892 rows, 2024-12-23 to 2024-12-29) and 2023 (1,873 rows, 2023-12-25 to 2023-12-31). The season label is the starting year: "2025" is the 2025-26 season. Other weeks not tested.
- **Provenance.** Payload `company` fields name RotoWire (projections) and Sportradar (stats). Those providers' terms were not read.
- **Use.** Projections are a live snapshot. No archive endpoint was found, so pre-game projections cannot be rebuilt for past dates (unverified that none exists). Log them at fetch time.
- **Defence.** Sleeper has no defensive endpoint. Points or stats allowed by position can be derived from the game logs (the position field is present), labelled as derived.

---

## 4. NBA

### 4.1 stats.nba.com (the matchup source)

- **Reach.** GET https://stats.nba.com/ returns 403, `Server: AkamaiGHost` (00:56 UTC). GET `/stats/leaguedashteamstats?...` returned no response within 30 s (curl exit 28, 00:54 UTC).
- **Recent breakage.** nba_api issue #696 (opened 8 Oct 2026, WebFetch): stats endpoints returned HTTP 200 with an Akamai HTML error page "since around Oct 7, 2026". The reporter closed it on 10 Oct: "Seems to be fixed now". No maintainer reply (https://github.com/swar/nba_api/issues/696).
- **Cloud blocking.** The nba_api README says stats.nba.com "throttles rapid requests and does not respond to some cloud-hosting IP ranges" (https://pypi.org/project/nba-api/, WebFetch). The repo's `nba_pbp.py` comment says the same.
- **Relevant endpoints** (nba_api docs, WebFetch; the docs carry "last validated" dates from 2020 to 2023, so treat them as stale):
  - `boxscorematchupsv3` (`GameID`): one row per offensive and defensive player pair. Fields include personIdOff, personIdDef, matchupMinutes, partialPossessions, percentageTotalTimeBothOn, switchesOn, playerPoints, matchupFieldGoalsMade/Attempted, matchupThreePointers*, matchupFreeThrows*, matchupAssists, matchupPotentialAssists, matchupTurnovers, matchupBlocks, shootingFouls, helpBlocks, helpFieldGoals*. Field names only; meanings inferred (https://raw.githubusercontent.com/swar/nba_api/master/docs/nba_api/stats/endpoints/boxscorematchupsv3.md).
  - `leaguedashptdefend`: DefenseCategory = "Overall", "3 Pointers", "2 Pointers", "Less Than 6Ft", "Less Than 10Ft", "Greater Than 15Ft". Fields: CLOSE_DEF_PERSON_ID, D_FGM, D_FGA, D_FG_PCT, NORMAL_FG_PCT, PCT_PLUSMINUS, FREQ, GP, G (https://raw.githubusercontent.com/swar/nba_api/master/docs/nba_api/stats/endpoints/leaguedashptdefend.md).
  - `leaguedashptstats`: PtMeasureType = SpeedDistance, Rebounding, Possessions, CatchShoot, PullUpShot, Defense, Drives, Passing, ElbowTouch, PostTouch, PaintTouch, Efficiency. The page does not describe them (https://raw.githubusercontent.com/swar/nba_api/master/docs/nba_api/stats/endpoints/leaguedashptstats.md).
  - `synergyplaytypes`: fields PLAY_TYPE, TYPE_GROUPING, PERCENTILE, POSS_PCT, PPP, FG_PCT, EFG_PCT, POSS, PTS, FGM, FGA, FGMX, TOV_POSS_PCT, and others. No allowed values for PlayType or TypeGrouping, so defensive groupings are unverified (https://raw.githubusercontent.com/swar/nba_api/master/docs/nba_api/stats/endpoints/synergyplaytypes.md).
  - Potential assists and rebounding chances at shot level: not verified beyond the PtMeasureType names above.
- **Terms** (https://www.nba.com/termsofuse, WebFetch):
  - s.1: "No Basketball Content from the Services may be reproduced, republished, uploaded, posted, modified, reused ... without the written permission of the Operator"; downloads "for your personal, noncommercial use".
  - s.9 (NBA Statistics), conditions: "(ii) ... may only be used, displayed, or published for legitimate news reporting or private, non-commercial purposes"; "(iv) ... may not be used in connection with any gambling activity (including legal gambling activity)"; "(v) ... may not be used in connection with any fantasy game or other commercial product or service"; "(vii) ... without the Operator's express prior consent" for any database of comprehensive, regularly updated statistics.
  - The page does not address automated access.
- **Library.** nba_api is MIT-licensed; "Data retrieved with this package comes from NBA.com and is subject to the NBA.com Terms of Use" (https://pypi.org/project/nba-api/, WebFetch).
- **Cost.** Free; use is limited by the terms above.
- **Unverified.** Whether the owner's PC on a home connection reaches stats.nba.com (the brief's guess).

### 4.2 cdn.nba.com

- liveData `todaysScoreboard_00.json`: 403 (text/html, 441 bytes), 00:54 UTC. staticData `scheduleLeagueV2.json`: 403. The nba_api issue reporter says cdn.nba.com liveData and staticData "still respond normally" for them (issue #696). Live play-by-play only; no defender data. Same NBA.com terms.

### 4.3 pbpstats

- **Docs** (https://pbpstats.readthedocs.io/en/latest/, WebFetch): "All stats on pbpstats.com are derived from these stats" (stats.nba.com and data.nba.com). No licence, no terms, no statement on the hosted API.
- **Hosted API.** `https://api.pbpstats.com/get-totals/nba?Season=2025-26&SeasonType=Regular+Season&Type=Player` returns 200, 3.05 MB. JSON with `single_row_table_data` and `multi_row_table_data`; 500 rows (whether capped is unverified). Columns include EntityId, AtRimFGA, Arc3FGA, Corner3FGA, DefAtRimReboundPct, DefPoss, Avg2ptShotDistance, Avg3ptShotDistance, BlockedAtRim. These are player totals, not matchups.
- `get-game-logs/nba` returns 422 unless EntityType and EntityId are given (response body).
- **Cost.** Not stated. **Terms.** Not stated (unverified). www.pbpstats.com returns 200; the bare pbpstats.com returned a TLS error.

### 4.4 Basketball-Reference

- basketball-reference.com: 200 (homepage). Terms: the Sports Reference terms in section 3.5. Automated access needs written permission; data reuse is welcomed with credit; training AI models is prohibited. Free site; Stathead pricing not checked.

### 4.5 Cleaning the Glass

- **Price** (https://cleaningtheglass.com/stats/, WebFetch): "Check out the stats free for one week, then just $5/month"; "Subscribe for $50/year". Renews automatically.
- **Terms** (https://cleaningtheglass.com/terms-of-service/, WebFetch): fees are "not refundable"; no clause on scraping or automated access; "This Agreement does not transfer ... any Cleaning the Glass ... intellectual property". No data-use licence granted.
- **Data.** Possessions "counted directly from play-by-play". No API, export or historical-season terms appear on the stats page.

### 4.6 Synergy (Sportradar)

- shop.synergysports.com returns 402 (curl). NBA consumer price $39.99/mo appears only in a search snippet (unverified).
- Sportradar's developer FAQ (search snippet; not opened) describes Synergy API play types, pick-and-roll detail, lineups and shot coordinates, and says the products "should not be considered an official data source".
- Enterprise price not public. Sportradar acquired Synergy in 2021 (search snippet).

### 4.7 Second Spectrum

- Not public. NBA.com publishes tracking-based stats; "More sophisticated and detailed tools are available to teams and broadcasters" (search snippets of 2016-2018 articles; not opened). secondspectrum.com returns 302. Price not public.

### 4.8 BettingPros

- bettingpros.com: 200. `/terms-of-service` returns 301 to `/terms-of-service/`, which returns **404** (01:07 UTC). `/terms` returns 301 (not followed).
- A defense-vs-position widget exists on team pages (search snippet of bettingpros.com/nba/teams/boston-celtics/stats/: "Position: vs PG. Rank: 14th of 28"; not opened). Licence and price: unverified.

---

## 5. Markets: Kalshi historical

### 5.1 Structure (docs read)

- https://docs.kalshi.com/getting_started/historical_data (WebFetch): "Kalshi partitions exchange data into live and historical tiers." Settled markets older than the cutoff: `GET /historical/markets`. Candlesticks: `GET /historical/markets/{ticker}/candlesticks`. Trades: `GET /historical/trades`. Fills: `GET /historical/fills`. "Old Events and Series will always still be available through their original endpoints." No retention period is stated. The cutoff moves forward.
- **Cutoffs** (`GET /historical/cutoff`, 200 on both hosts, 00:55 UTC): market_settled_ts 2026-08-23T00:00:00Z; trades_created_ts 2026-08-23T00:00:00Z; market_positions_last_updated_ts 2026-08-23T00:00:00Z; orders_updated_ts 2026-10-08T00:00:00Z.
- **Markets endpoint** (https://docs.kalshi.com/api-reference/historical/get-historical-markets, WebFetch): `limit` default 100, max 1000; `cursor`; `tickers`; `event_ticker`; `series_ticker`; `mve_filter`. No `min_close_ts` or `max_close_ts` is listed; my test with `max_close_ts` was ignored (the same June 2026 page came back).
- **Candlesticks** (https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks, WebFetch): `period_interval` must be 1, 60 or 1440. `start_ts` and `end_ts` are required. No range limit is stated. Fields: end_period_ts, yes_bid and yes_ask (open, high, low, close), price (open, high, low, close, mean, previous), volume and open_interest (strings).
- **Rate limits** (https://docs.kalshi.com/getting_started/rate_limits, WebFetch): token budgets per second (the table read is the predictions budget; Basic tier 200 read and 100 write). The docs' 429 example is `{"error": "too many requests"}`; the body observed here was `{"error":{"code":"too_many_requests","message":"too many requests"}}`. The page says 429s carry no Retry-After or X-RateLimit headers and "There is no penalty or cooldown." In the first paging run, 4 of 12 requests returned 429 (4 s pauses between successes, 15 s after a 429); each was retried at the same cursor. A second run at 3 s pauses had none.
- **Authentication.** No key used. Every call below returned 200 except where noted (429s, one 404).

### 5.2 Test on one settled 2025-26 KXNBAPTS market

- **Market.** `KXNBAPTS-26JUN13NYKSAS-SASVWEMBANYAMA1-40` (Victor Wembanyama 40+ points, NYK at SAS, 2026-06-13). From `GET /historical/markets?series_ticker=KXNBAPTS&limit=20`: status "finalized", result "no", expiration_value "points", floor_strike 39.5, close_time 2026-06-14T04:00:18Z, no_sub_title "Victor Wembanyama: 40+", custom_strike {basketball_player: UUID, basketball_team: UUID}. Also rules_primary text, settlement fields, volume_fp, open_interest_fp, and yes/no bid and ask. **No NBA, ESPN, Sleeper or PFF player id appears** in the response.
- **Hourly candles.** `GET /historical/markets/{ticker}/candlesticks?start_ts=1781000000&end_ts=1781500000&period_interval=60` returns 200 with 67 candles, first end_period_ts 1781154000 (2026-06-11T05:00:00Z), last 1781413200 (2026-06-14T05:00:00Z). That is fewer than the 72 hours in the window; whether empty periods are dropped is unverified. First candle: yes_ask open 0.9900, close 0.1400.
- **1-minute candles.** Same ticker, 2026-06-11T05:00Z to 07:00Z, `period_interval=1`: 200, 32 candles (sparse; the 120-minute window is not fully populated).
- **Trades.** `GET /historical/trades?ticker=...&limit=1000`: 200, 654 trades on one page, cursor empty, created_time 2026-06-11T05:19:15Z to 2026-06-14T02:56:53Z. Fields: trade_id, created_time, yes_price_dollars, no_price_dollars, count_fp, taker_side, taker_outcome_side, taker_book_side, is_block_trade.
- **Live route for the same ticker.** `GET /series/KXNBAPTS/markets/{ticker}/candlesticks` returns 404 not_found, as the tier split predicts.

### 5.3 How far back: KXNBAPTS

- **Paging.** `GET /historical/markets?series_ticker=KXNBAPTS&limit=1000`, following each cursor to the end: 23,562 rows across the pages (duplicates not checked). close_time runs from **2025-11-19T03:07:14Z** to **2026-06-14T04:00:24Z**. Pages were fetched in cursor order and failed requests were retried at the same cursor, so no page was skipped.
- **Launch.** Front Office Sports reported Kalshi NBA player props on 14 Nov 2025 (search snippet, https://frontofficesports.com/article/kalshi-adds-nba-prop-markets-as-betting-crackdowns-surge/; not opened). That fits the earliest close.
- **After the cutoff.** `GET /markets?series_ticker=KXNBAPTS&status=settled` returns live markets settled 2026-10-22 (e.g. KXNBAPTS-26OCT21SACLAC), so post-cutoff history sits in the live tier.
- **Result.** One season (2025-26) in the historical tier, plus the 2026-27 start in the live tier. Nothing before 2025-11-19 for this series.
- **Other prop series** (KXNFLREC and the rest): not paged; depth unverified.

### 5.4 Kalshi terms that apply

- **Developer Agreement v1.1** (https://kalshi-public-docs.s3.amazonaws.com/Kalshi-Developer-Agreement.pdf; PDF text extracted with pdftotext, so these quotes are exact):
  - s.3: "Use of Kalshi APIs is expressly limited to facilitating a members own trading on the Exchange; all other usages are disallowed and may result in account suspension."
  - s.3.1: "Collecting, caching, aggregating, or storing data or content accessed via the API except for purposes of facilitating your own trading on Kalshi. You may not share such data or content with third parties in any manner without prior written authorization from Kalshi."
  - s.3.5: prohibits "Accessing the API for purposes of monitoring the availability, performance, or functionality of any of Kalshi's services or for any other benchmarking or competitive purposes."
  - s.3.7: "Sublicensing the API for use by a third party."
  - s.5: Kalshi "may immediately suspend or terminate your access to the API without notice". s.2.3: rate limits may change without notice.
- **Kalshi Data Terms of Use** (https://kalshi-public-docs.s3.amazonaws.com/kalshi-data-terms-of-service.pdf; text extracted). Defines "Kalshi Data" as content on the kalshi.com website (volume, bid-ask and settlement prices, indexes, open interest). Section I: "You may access content only for your personal use for non-commercial purposes. Non-commercial use does not include the use of Kalshi Data without prior written consent from Kalshi in connection with: (1) the development of any software program, including, but not limited to, training a machine learning or artificial intelligence system; or (2) providing archived or cached data sets containing Kalshi Data to another person or entity." It also bars use of Kalshi Data "for any machine learning and/or artificial intelligence", and bots that "copy in bulk, retrieve, harvest".
- **Reading.** The two documents differ in scope: the Data Terms describe website content, while the Developer Agreement governs the API. Which one governs API-sourced prices is unverified. For this app, s.3.1 allows storage only for the owner's own trading. s.3.5 concerns benchmarking Kalshi's services, which may not cover comparing a model with market prices, but that reading is mine and unverified. Counsel or Kalshi should answer this, not this report.

---

## 6. Ranked recommendation

**Use now (free, reachable, terms read or low risk)**

1. **nflverse core files:** play-by-play 2016-2026; participation 2016-2025 (no 2026); FTN charting 2022-2026 (CC BY-SA); NGS 2016 onward; PFR advanced (2025 file checked); snap counts (2026 present); players crosswalk (includes pff_id, espn_id, pfr_id, nfl_id). Caveats: participation coverage and route are blank on non-pass plays only (99%+ complete on pass attempts, see section 2); FTN-derived committed tables carry ShareAlike.
2. **Sleeper NFL and NBA projections and game logs:** non-commercial only (docs). Log projections at fetch time; game logs give team, opponent, game_id, position and quarter splits for NBA 2023-2025 (other weeks not tested).
3. **Kalshi historical KXNBAPTS** (23,562 rows, 2025-11-19 to 2026-06-14) for price baselines, once the Developer Agreement s.3.1/s.3.5 and Data Terms s.I questions are decided.
4. **ESPN site API** for NBA and NFL box and play data (already in the repo). No defender fields. Terms unverified.

**Worth paying for only after the terms are read (none of the paid terms I read clears betting use)**

- **PFF Annual, $99.99/yr** (Premium Stats, CSV exports): personal use under s.1.2 only; ask PFF in writing whether betting analysis is allowed. The PFF Pro API ($199.99/yr) is restricted by s.14.4 item 7 (betting products for others; any use in connection with a sportsbook, exchange or gaming operator): ask PFF in writing whether Kalshi trading is covered.
- **FTN charting API** (snippet: $5,000/yr API, $599 CSV). What it adds beyond the nflverse 2022+ file is unverified (a snippet says historical since 2019). The nflverse file has no route or coverage columns. Read FTN's terms first.
- **SIS DataHub NFL, $749.99/yr:** consumer leaderboards with CSV export; route data unverified; terms not read.
- **Cleaning the Glass, $50/yr:** NBA team-level context; no scraping clause; no data-use licence granted; no API.
- **Synergy:** play-type data (the Sportradar snippet names play types and pick-and-roll detail; whether defensive groupings are included is unverified). Consumer price is a snippet; enterprise price not public.

**Cannot be obtained from any source checked, at this price and terms**

- NFL 2026 defender coverage and route (participation) before the 2026 postseason, by nflverse's policy. FTN's paid API may cover 2026 weeks; unverified.
- Who covered whom on each NFL target from any free source (participation gives sets, not assignments).
- NBA matchup pairs (`boxscorematchupsv3`) and defense by zone (`leaguedashptdefend`) from this sandbox (blocked), and under NBA.com section 9(iv) if gambling use is covered.
- Second Spectrum tracking data (not public).
- Kalshi NBA prop history before 2025-11-19.
- A Kalshi-to-NBA player-id crosswalk: none in the responses.
- Pre-game Sleeper projections for past dates (current snapshot only; no archive endpoint found).
- ESPN defender or coverage fields (none in the payloads checked).

---

## 7. Corrections to the brief

- **pbp_participation exists for 2016-2025**, not only 2021-2025 (2016, 2017, 2018 and 2019-2025 all returned 302; 2026 returns 404).
- **Route definition:** the nflreadr dictionary says "the primary receiver", not "the targeted receiver".
- **The 2026 participation absence** is the designed release schedule, not a stopped feed. The NGS source it replaced died during the 2023 season.
- **The release page's note** ("Participation data for plays from NGS") conflicts with the FTN source in the docs.
- **NBA prop series:** only KXNBAPTS was tested. The other series named in the brief are unverified.
- **stats.nba.com from the owner's PC:** unverified. No PC test was run.

---

## 8. Owner decisions and open items

- **Legal read needed before use in a betting-analysis output or committed derived table:**
  - Kalshi Developer Agreement s.3.1 and s.3.5, and Data Terms s.I (software development, machine learning).
  - PFF s.14.4 item 7, if any PFF data is ever used.
  - NBA.com terms s.9(iv) and (v), for any NBA.com-derived statistics, including feeds built on them (pbpstats says its stats are derived from stats.nba.com and data.nba.com).
  - Sleeper's non-commercial limit.
  - FTN CC BY-SA ShareAlike, for committed derived tables.
- **Written answers wanted from PFF:** the scope of s.1.9 and s.1.10 for API data, and whether betting analysis is allowed.
- **No shared-file change is proposed by this report.** No hook or guard is needed for research output.

## 9. Method notes

- Commands run: HTTP reach tests (`curl -sS -m 30`), HEAD requests with redirect following, partial downloads with `Range` headers (120 KB or 60 KB), a streamed gzip header read, cursor paging of the Kalshi historical markets endpoint, one candlestick, one trade and one cutoff call per host, and JSON and CSV parsing with `python3 -I`. No package was installed.
- Documents read: WebFetch pages (quotes are the fetch tool's extracts) and PDF text from pdftotext (Kalshi only).
- Not read: any page marked "not opened" or "search snippet" above.
