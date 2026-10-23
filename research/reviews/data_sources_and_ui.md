# Independent review: matchup data sources (A) and the Matchups UI + route (B)

Reviewer: Fable, a fresh instance with no part in either deliverable. Written 2026-10-23 01:55 UTC
(2026-10-22 19:55 MDT). Everything below was re-run or re-read by me; "the report" means
`research/reports/matchup_data_sources.md`. No existing file was edited; no git write. Scratch
work (scripts, raw responses, screenshots, `log.txt` with UTC stamps) is under
`/tmp/claude-0/-home-user-kalshi-betting-helper/2a51b1d4-3290-577b-b97b-fbbf27fce813/scratchpad/review_live/`.

Method notes. Reach tests: `curl -sS -m 30`, unauthenticated, from this sandbox, 01:37-01:52 UTC.
Terms PDFs: downloaded again and read with `pdftotext` (quotes exact); their sha256 match the
research agent's copies (`9d3e3368...` developer agreement, `d19a0f94...` data terms). Web pages:
the fetch tool, 01:38-01:47 UTC -- its quotes are extracts, as the report says of its own. The
empirical route test is `route_test.py` (sha256 `25550de6...`), pure stdlib, over the research
agent's 2025 downloads (`data/nflverse/`). The UI was run locally (`VIGIL_NO_BG=1`, scratch
`VIGIL_SIM_CACHE_DIR` / `DEEP_CACHE_DIR` / `KALSHI_DB`, no login configured, ports 5177-5179, every
server stopped and verified gone) against fixture boards whose `market` blocks came from
`kalshi_nba_props.market_block` on REAL raw Kalshi markets (saved 00:54 UTC) and whose `value`
and `labels` came from `prop_value.line_value` / `line_labels` (`fixture.py`, sha256 `0adab7a0...`),
driven by Playwright (`ui_test.js`, sha256 `8bad1bcd...`; `ui_test2.js` for the error paths).

---

## A. `research/reports/matchup_data_sources.md`

### A.1 Claim-by-claim

| # | Claim in the report | Verdict | My evidence |
|---|---|---|---|
| 1 | `pbp_participation_2026.csv` does not exist (404); 2016-2025 exist | **AGREE** | 01:37:45Z: 2026 -> 404; 2025 -> 302 (release-assets redirect); 01:51:28Z: 2016 -> 302. `play_by_play_2025.csv.gz` and `ftn_charting_2026.csv` -> 302. |
| 2 | nflreadr: participation from 2023 "is provided after all post-season games are completed"; "It does not update during the season!"; NGS source "died during the 2023 season" | **AGREE** (one attribution nit) | `reference/load_participation.html`: "Participation data prior to 2023 is from NFL NGS." / "Participation data from 2023 onwards is courtesy of FTN and is provided after all post-season games are completed." The "It does not update during the season!" and "The data source died during the 2023 season." sentences are on `articles/nflverse_data_schedule.html` only -- the report cites "load_participation and schedule pages" for the former; the load page does not carry it. |
| 3 | nflreadr 1.5.0 (2025-09-02): "FTNData.com has graciously offered to provide participation data after each season has ended"; "load_participation() is back, somewhat!" | **AGREE** | `news/index.html`, nflreadr 1.5.0, CRAN 2025-09-02, both sentences verbatim, plus "so we now have participation data from 2024 and 2023." |
| 4 | Dictionary: `route` is "the route the primary receiver on a play took"; 13 values; coverage 10 values | **AGREE** on the wording | `articles/dictionary_participation.html`: "A string indicating the route the primary receiver on a play took." Values as listed. `defense_coverage_type` values as listed; `ngs_air_yards` "Legacy column ... Is NA for 2024 on". |
| 5 | Route / coverage are "sparse (40-56% non-blank in the sampled files, cause unverified)" (§3.2 table, §6 item 1) | **DISAGREE** with the framing | 2025, every participation row (45,184) joined to pbp (100%): `route` present on **99.4%** of 17,582 pass attempts with a receiver, 51.8% of 805 attempts with none (throwaways), 0.1% of 1,352 sacks, 0.2% of scrambles, **0.0% of 13,746 runs**; `defense_coverage_type` present on 99.9% of attempts, sacks and scrambles, 0.0% of runs. All-plays shares 0.418 / 0.488 -- the report's 0.38 / 0.48 for 2025 are just the share of plays that are passes. The cause of the blanks is known: they are run, kick and kneel plays. An engine that reads route/coverage on pass plays has essentially complete data; the report's "sparse" would steer it wrong. |
| 6 | Route definition conflict (brief: "targeted receiver"; dictionary: "primary receiver") | **AGREE** with "use the dictionary wording", and the data answers the question | Route by the TARGETED receiver's position (from the play's own `offense_players` / `offense_positions`, 2025): SWING 78.7% RB (n=917), TEXAS/ANGLE 72.3% RB (148), WHEEL 40.4% RB, SCREEN 38.3% RB / 43.3% WR; GO 80.2% WR, IN/DIG 80.3%, DEEP OUT 82.1%, POST 78.6%. Air yards of the target: GO mean 24.8 (0.1% at or behind the line), POST 19.1, CORNER 19.1, DEEP OUT 13.3, IN/DIG 12.9, SLANT 6.2, HITCH/CURL 5.9, QUICK OUT 2.7, SWING -3.1 (89.8% <= 0), SCREEN -3.3 (96.7% <= 0). So on targeted plays the charted route IS the targeted receiver's; on 52% of no-target attempts a route is still charted (the primary read nobody threw to). Engine rule that follows: read `route` only where `receiver_player_id` is set, and never read a blank as "no route run". |
| 7 | FTN charting: CC BY-SA 4.0, credit "FTN Data via nflverse"; 2022 on, "charted within 48 hours" | **AGREE** | `reference/load_ftn_charting.html`: "This data is released under the CC-BY-SA 4.0 Creative Commons license and attribution must be made to FTN Data via nflverse." / "Data is available from the 2022 season onwards and is charted within 48 hours following each game." The loader's console line reads 'ftndata.com via nflverse' (a second spelling of the credit). Participation page: "attribution must be made to FTN Data via nflverse (from 2023 onwards) or NFL NextGenStats via nflverse (for 2022 and earlier)". |
| 8 | nflverse-data LICENSE.md is the CC BY 4.0 text | **AGREE** | Raw file: title "Attribution 4.0 International"; no ShareAlike anywhere. |
| 9 | FTN 2025 header has no route / coverage column; "2026 header not inspected, assumed the same" | **AGREE**, gap closed | Both the 2025 and 2026 files on disk: 29 columns, 0 matching route/coverage, identical header. |
| 10 | Kalshi `/historical/cutoff` values | **AGREE** | 01:38:08Z: market_settled_ts / trades_created_ts / market_positions_last_updated_ts 2026-08-23T00:00:00Z; orders_updated_ts 2026-10-08T00:00:00Z. |
| 11 | KXNBAPTS historical: 23,562 markets, close_time 2025-11-19 to 2026-06-14 | **AGREE** (range from the saved paging output; I re-fetched one page, not all 24) | One call, 01:38:08Z: `/historical/markets?series_ticker=KXNBAPTS&limit=1000` -> 200, 1,000 rows, cursor present, close_time 2026-05-12T01:40:03Z..2026-06-14T04:00:24Z, first ticker `KXNBAPTS-26JUN13NYKSAS-SASVWEMBANYAMA1-40`, status finalized. The agent's saved output (`data/kalshi_hist/KXNBAPTS_markets.json`): 23,562 rows, **23,562 unique tickers** (the report left duplicates unchecked -- none), min close 2025-11-19T03:07:14Z, max 2026-06-14T04:00:24Z, by month 2025-11 1,055 / 12 2,862 / 2026-01 3,642 / 02 3,265 / 03 6,541 / 04 4,312 / 05 1,600 / 06 285; results yes 8,483 / no 14,074 / scalar 1,005. All 1,000 of my live page sit in that set. |
| 12 | Historical markets endpoint: limit default 100 max 1000; no `min_close_ts` / `max_close_ts` | **AGREE** | Docs list limit (default 100, max 1000), cursor, tickers, event_ticker, series_ticker, mve_filter; "Filters are mutually exclusive."; no timestamp filter. |
| 13 | Historical-data doc quotes; no retention period or price stated | **AGREE** | Partition sentence and the four endpoints verbatim; "Old Events and Series will always still be available through their original endpoints."; no retention or price on the page. |
| 14 | Kalshi Developer Agreement v1.1 s.3, 3.1, 3.5, 3.7 | **AGREE**, exact | pdftotext of the same PDF: s.3 "Use of Kalshi APIs is expressly limited to facilitating a members own trading on the Exchange; all other usages are disallowed and may result in account suspension."; 3.1, 3.5, 3.7 word for word as quoted. |
| 15 | Kalshi Data Terms s.I (personal, non-commercial; software program / ML; archived or cached data sets) and the bots / ML sentences | **AGREE**, exact | Section I verbatim as quoted; "copy in bulk, retrieve, harvest, index, search or analyze any portion of the Website is strictly prohibited" and the "machine learning and/or artificial intelligence" sentence present. |
| 16 | NBA.com terms s.9(iv): "may not be used in connection with any gambling activity (including legal gambling activity)"; (ii), (v), (vii) | **AGREE** | Section 9 "NBA Statistics", items (i)-(vii) as quoted; the page has no provision on automated access (the report says the same). |
| 17 | PFF terms "Last Updated: October 12, 2026"; s.1.2 personal, non-commercial; s.1.5(i) no ML training; s.14.2 Commercial Use "includes use by or on behalf of any business, employer, team, league, media outlet, sportsbook ... fantasy or gaming operator, data provider" | **AGREE** | All present verbatim (the 14.2 sentence sits inside the definition at 14.2(6)). |
| 18 | PFF s.14.4 item 7: no use of API Data "to provide, or in connection with, any betting, wagering, daily fantasy, handicapping, tout"; report's reading "API data cannot be used for betting analysis as the fetched terms stand" | **AGREE the fragment is accurate; DISAGREE with the reading as stated** | The full item: "use API Data to provide, or in connection with, any betting, wagering, daily fantasy, handicapping, tout, picks, or advisory product or service **to any other person**, or in connection with any sportsbook, **exchange**, or gaming operator;". The report's quote stops before "to any other person" and the exchange clause; both qualifiers matter to what the owner decides (not legal advice -- the report must carry the whole sentence; Kalshi is an exchange). |
| 19 | PFF s.14.3 "Nothing in the Agreement grants you any right to use API Data for Commercial Use, for Public Distribution" | **CANNOT VERIFY** | The fetch returned 14.3's first sentence (a limited licence "solely for Personal Use"); the quoted sentence may sit later in 14.3 but I could not locate it. Not load-bearing. |
| 20 | PFF s.1.9 / s.1.10 cross-referenced but absent from the fetched text | **AGREE** | Fetch: section 1 ends at 1.8; 1.9 and 1.10 are referred to, not present. |
| 21 | stats.nba.com root 403 AkamaiGHost; a stats endpoint times out; cdn.nba.com 403 | **AGREE** | 01:37:46Z: `GET /` -> 403, `Server: AkamaiGHost`; `leaguedashteamstats` with browser UA + Referer -> curl exit 28 at 20 s, 0 bytes; cdn `todaysScoreboard_00.json` -> 403. |
| 22 | Sleeper docs: "free to use for non-commercial purposes"; "reach out" for commercial; 1000 calls/min; projections/stats undocumented | **AGREE** | All verbatim; no `/projections` or `/stats` on the docs page. |
| 23 | Sleeper NBA weekly projections exist with team, opponent, game_id (2026/1: 1,096 rows dated 2026-10-20) | **AGREE** | 01:38:06Z: 200, 1,096 rows, game_id and opponent on all 1,096, dates 2026-10-20..25, company rotowire, stat keys as listed (plus fgmi/ftmi/tpmi). |
| 24 | "No archive endpoint was found, so pre-game projections cannot be rebuilt for past dates (unverified that none exists)"; season snapshot overwritten | **AGREE with the conclusion, with a correction** | Past WEEKS are served: `/projections/nba/2025/1` -> 200, 1,327 rows (games 2025-10-21..26); `/2025/10` -> 200, 1,734 rows (12-22..28). But every row in each carries ONE `updated_at` / `last_modified`: 2025-10-27 06:40 UTC and 2025-12-29 07:40 UTC, the morning after each week ended, and ~30% of rows have null stats. So what is stored is the week's final state, not a frozen pre-game copy; whether the numbers equal the pre-game ones CANNOT be verified from here. "Log at fetch time" stands. The report should say past weeks answer and why they are not an archive. |
| 25 | Participation file has no receiver id for `route`; no who-covered-whom | **AGREE** | Header as listed (26 columns); per-play sets only. |
| 26 | Prices and terms of FTN, SIS, PFF plans, TruMedia, Synergy, Second Spectrum, CTG, BettingPros, pbpstats; nba_api issue #696; ESPN payload fields; Sports Reference terms | **CANNOT VERIFY** (not re-checked) | None of these steers the engine; the report marks most of them unverified itself. |

### A.2 Other observations on the report

- The report is unusually honest about what it did not read, and its exact-quote discipline on
  the Kalshi PDFs held up word for word. Its reach column held up at every point I re-tested.
- Its §6 recommendation "participation coverage and route are sparse" (A.1 #5) is the one
  statement that would mis-steer the NFL engine; the fix is a sentence, not new work.
- The §3.6 reading of PFF (A.1 #18) is the one statement that would mis-steer the owner's legal
  read; the fix is to quote the whole item 7.
- Duplicates in the 23,562 KXNBAPTS rows were "not checked" -- checked here: none.

### A.3 Verdict on A

**AGREE AFTER FIXES.** Required before the report is cited by any engine or owner decision:
A1 (quote PFF 14.4(7) in full and soften the reading to what the text says) and A2 (replace
"sparse, cause unverified" with the measured fill by play type). A3-A4 are wording.

---

## B. The Matchups UI and route

Files: `static/matchups.js` (new, 275 lines), `app.py` 2261-2293 (`_MATCHUP_SPORTS`,
`_matchups_board`, `api_matchups`), `templates/index.html` (tab button, `#tab-matchups`,
script tag), `static/app.js` 2607 / 2629, `static/sw.js` PRECACHE.

### B.1 What holds up (checked, not assumed)

- **Units.** Asks shown in cents (`_mxCents(mk.yes_ask)`; market block carries cents: fixture
  `yes_bid 81, yes_ask 82, no_ask 19`). EV shown in cents per contract (`ev_yes` / `ev_no`
  from `line_value`, fixture `ev_no 18.9227` -> "+18.9¢"). Interval `ev_interval[side]` is
  `[lo_c, hi_c]`, lower first for both sides (NO: `[10.92, 27.92]` -> "+10.9 to +27.9").
  ROI a fraction of all-in cost -> "94.2%". Probabilities fractions -> "61%". `ts` epoch seconds
  -> "Thu 7:48 PM MT" in America/Denver. `built_ts` likewise.
- **Best side.** `_mxBest` reads `best_side` first; the EV, ROI, interval and `edge_exec` are
  all taken from that same side (fixture 35+ line: p 0.21, best NO, "+37.3¢", chips
  high_probability/+EV/Robust/Low evidence -- the same list `line_labels` produced).
- **Label text matches `prop_value`** semantics: fee at the executable ask, "robust" = positive
  at the band's unfavourable end, "low evidence" = n_eff/width rule. The note under the table
  says EV is after the fee.
- **Text-context escaping** is complete: player name `<img src=x onerror=alert(2)>`, notes with
  `<script>`, factor text, assessment, detail-table keys and cells (nested objects via
  JSON.stringify then escape), provenance, `as_of`, status source, comparables -- all rendered as
  text; no dialog fired from any of them. `_mxKind` colours come from a fixed map.
- **Route.** Never computes: `boardshare.get` of a pickle only. `sport` validated (`mlb` ->
  400 with a reason; `NBA` lowercased). `date` is sanitised by `boardshare._path` (every char
  not alnum/`-`/`_` becomes `_`): `date=../../etc/passwd` -> "No NFL matchup board for
  ../../etc/passwd yet." and no file read outside the boards dir. Blank date -> the MT day
  (`clock.mt_date`), correct at 01:48 UTC = 2026-10-22 MT. Missing board -> `status none` with
  a plain reason, which the tab shows verbatim-escaped.
- **Degenerate boards.** Empty `players` -> "No lines match." / pickers only; players with no
  markets -> renders; lines with `market: null` are hidden by the default "only lines Kalshi
  lists" and shown when unticked; p null -> "-" everywhere, no exception. Zero console errors
  from the tab (the two 5xx in the console were `/api/recorder/*`, other tabs' loads).
- **Wiring.** Tab toggle and `initMatchups` guard in app.js; script order (app.js first, so `$`
  and `escapeHtml` exist); `#mxRoot` present; the service worker's PRECACHE gains the file
  (SHELL bump not needed: a changed sw.js reinstalls and the same cache gains the entry).
- **Phone width** (390 px): no horizontal page scroll (scrollWidth 390); tables scroll inside
  their `.scroller`.
- Sorting by EV flips direction on a second click (observed desc then asc); filters by label,
  stat and game work on the fixture.

### B.2 Findings

| id | severity | file:line | what | evidence | suggested fix |
|---|---|---|---|---|---|
| B1 | **blocker** | `static/matchups.js:157`, `:207` | XSS through a JS string inside an `onclick` attribute: `onclick="mxOpen('${_mxEsc(r.p.player_id)}')"` and `onclick="mxToggle('${_mxEsc(key)}')"` (key embeds player_id). HTML-entity escaping is undone by the attribute parser BEFORE the JavaScript is parsed, so `'` survives. Player ids/names come from third-party feeds (Kalshi titles, ESPN ids), not from the owner. | Fixture `player_id = "x');alert(1);//"` -> DOM attribute `mxOpen('x');alert(1);//');return false` (read back with `getAttribute`) -> Playwright recorded `dialog: alert: 1` on clicking the link. | No inline handlers with interpolated data. Put the id in a data attribute (`data-pid="${_mxEsc(id)}"`) and bind one delegated listener on `#mxBody` (`e.target.closest('[data-pid]')`); same for the matrix rows (`data-key`). This also removes the global `mxOpen`/`mxToggle` functions from the page. |
| B2 | major | `:167` (`<option value="${s}"`), `:158` / `:228` / `:231` (`${l.k}`), `:184` (`${val.rank}`), `:221` (`${h.hits}/${h.n}`) | Unescaped interpolation into an attribute (stat) and into text (k, rank, hits/n). | Fixture stat `pts" onmouseover="alert(6)` -> the `<option>` carries attribute `onmouseover=alert(6)` (read back from the DOM). `k = 7" onmouseover="alert(7)` rendered as text (text context), but a `<` there would be markup. | `_mxEsc` on every interpolation; format numbers through `_mxNum`. A small guard: grep matchups.js for `${` not wrapped in `_mxEsc`/`_mxNum`/`_mxPct`/`_mxCents`/`_mxSigned`. |
| B3 | major | `:183` (`_mxFacts`) | Unit is guessed from magnitude: any number with 0 < \|v\| < 1 is printed as a percent. Role features are not all shares. | Fixture `ast_per_min 0.12` -> "12.0%"; `yds_per_route 0.95` -> "95.0%" while 1.9 would print "1.90". `nba_matchup_profiles.role` emits a `basis` string ("rim FGA share", ...) and `raw`; the UI drops both, so the row has a number and no meaning. | Print a percent only when the field says so (`unit: "pct"`, or `basis` naming a share/rate); otherwise `_mxNum`. Show `basis` as the row label's title (or a muted suffix). Ask the engines to add `unit` to the contract. |
| B4 | major | `:148`, `:161` ("Edge" column) | The "Edge" column is the EV column in other units: `edge_exec[side] = p_side - (ask+fee)/100 = ev_c/100` exactly (size-free fee). The owner sees one number twice and reads it as two facts. The quantities that answer "is Kalshi's price off" -- `edge` (p - p_market), `p_market`, `market_kind` (ask-only / wide book), `fee_assumed` -- are never shown. | Fixture rows: "NO +37.3 pts" beside "+37.3¢"; "+18.9 pts" beside "+18.9¢"; "+24.9" / "+24.9¢". `line_value` returns `edge -0.205, p_market 0.815, market_kind two_sided` for the 25+ line; none appears. | Replace the column with model-vs-market: `p_market` and `edge` in points, suffixed by `market_kind` when not two-sided ("ask only", "wide book"); keep edge_exec, if at all, as a tooltip "margin over break-even incl. fee". Flag `fee_assumed`. |
| B5 | major | `app.py:2291-2293`; `matchups.js:100-103` | A NaN anywhere in a board is serialised by Flask as the bare token `NaN` (stdlib `allow_nan`), invalid JSON; the browser's `JSON.parse` throws; the tab then says "could not reach the server", which is false. | Fixture board with `float("nan")` in `baseline_mean` / `p`: route 200, body `"baseline_mean":NaN,...`; node `JSON.parse` -> "Unexpected token 'N'"; Playwright: body "could not reach the server". | Route: build the body with `json.dumps(out, allow_nan=False)` inside try/except, ledger `MX-nan` and answer `status: "error"` with the reason; producers: assert `json.dumps(board, allow_nan=False)` before `boardshare.put` (guard it). UI: separate "unreachable" (fetch rejected) from "bad payload" (`.json()` threw) in the message. |
| B6 | major | `matchups.js:113-117` | A JSON body without `status` renders as a healthy empty board. The service worker answers a dead network with HTTP 503 and `{error:"network unavailable", offline:true}`; `fetch` resolves, `.json()` parses, `d.status` is undefined, and the tab prints "Built · data through ? · 0 players · sources: -" with the full filter bar. Same for `{}`. | `ui_test2.js` with service workers blocked and the API stubbed: 503 `{error, offline}` -> "Built · data through ? · 0 players · sources: -"; 200 `{}` -> the same; 401 / 500 HTML -> "could not reach the server". | `if (!res.ok || d.status !== "ok")` -> show `d.why || d.error || res.status`. |
| B7 | minor | `:60-66`, `:161-162`, `:234` | A market block that is all sentinels (0/100) yields a `value` with `best_side null`; `_mxBest` falls back to "yes" and the row reads "YES - pts" / "-¢ - to -" / "YES -¢ (- to -) ROI -". | Fixture 30+ line (empty book): exactly that text in the scan and player tables. | Return null from `_mxBest` when the chosen side's `ev` is null; print "no offer". |
| B8 | minor | `:237` | Delta printed against a null baseline: `(adjusted_mean \|\| 0) - (baseline_mean \|\| 0)`. | Fixture rebounds market with `baseline_mean null`: "baseline - () -> adjusted 8.1 (+8.1)". | "-" when either side is null; hide "(source)" when empty. |
| B9 | minor | `:29`, `:54` | The `high_probability` chip is about the BEST side, which can be NO, while the row's Model column shows P(YES). The chip and its explanation ("The model expects this often") do not name the side. | 35+ line: Model "21%", chip "High probability" (NO at 79%). | Chip text "High probability (NO)"; explanation "the better side wins >= 70% of the model's outcomes". |
| B10 | minor | `:147-151` | Sort comparator yields NaN when `b.ev` or `l.p` is null (`-1e9` covers only a missing `b`); NaN is treated as 0, so the order of such rows is implementation-defined. | Fixture: the "-¢" row landed between the EV rows and the "-" row in both directions (stable here, not guaranteed). | Map null to `-Infinity` in each key. |
| B11 | minor | `app.py:60-61` (`_asset_version`) | The `?v=` token is the max mtime of app.js / style.css / sw.js; a change to matchups.js alone does not change it. Flask sets no max-age on static files and the shell is network-first, so today it is a revalidation, not a stale file. | Read. | Add `"matchups.js"` to the tuple. |
| B12 | minor | `:52-56`, `:147-150`, `:154` | Unknown labels are dropped silently by `_mxChips`; the `fit` sort key has no header (unreachable); `rows.slice(0, 300)` truncates with no note. | Read. | Render unknown labels as plain chips; drop `fit` or add its header; print "showing 300 of N". |
| B13 | minor | `:184`, `:212` | A role / opponent fact without `kind` shows no tag, so it reads as a plain fact (the contract says every field carries one; nothing enforces it). `hit_similar_def` and comparables carry no kind either (they are modelled weights). | Fixture field with no kind -> no tag. | Default a missing kind to a visible "unlabelled"; caption "Hit, similar defenses" as "modelled similarity weights". |
| B14 | major (integration) | `app.py:2269` vs the tree | Nothing publishes `matchups_{sport}_{date}`: `grep -rn matchups_ --include=*.py` finds only app.py; `nba_matchup.py` has no `board()` and never calls `prop_value.line_value` or `boardshare`. The route and tab are a renderer with no producer. | grep; `nba_matchup.py` function list. | Record the key contract (`matchups_{nfl\|nba}_{YYYY-MM-DD}`, MT date) in the agreement log, and when the producer lands add a guard that it publishes under the key the route reads. The tab is honest meanwhile ("No NBA matchup board for <date> yet."). |
| B15 | nit | `static/sw.js:6` | PRECACHE keys are unversioned (`/static/matchups.js`) while the page requests `?v=...`; `caches.match(e.request)` will not match the precached copy. Pre-existing pattern (app.js is the same); the network-first `c.put` of the versioned request covers offline after one load. | Read. | None required; note for whoever next touches the SW. |
| B16 | nit | `:97` | "Loading the NFL matchups…" while `_mx.date` is blank gives no hint which day is loading until the response arrives. | Observed. | Show the MT date being requested. |

### B.3 Verdict on B

**AGREE AFTER FIXES.** Blocker: B1. B2-B6 should be fixed in the same pass (B3/B4 are
"misleading to the owner" findings -- a number without its meaning and a column that duplicates
another; B5/B6 are silent failures that print a false reason). B14 is not a defect of the UI but
must be in the agreement log so the key contract does not slip. The fixes are all local to
`static/matchups.js` and the route; none changes a number the engines produce.

---

## C. What I did not do

- I did not re-page all 24 pages of KXNBAPTS (one page, politely, plus the saved output).
- I did not read PFF's, SIS's or FTN's commercial pages beyond the terms quotes above.
- I did not test the tab against a real engine board (none exists yet, B14); the fixture
  exercised every field the contract names, with producers' real `market` and `value` shapes.
- I did not run the guard suite (no code of mine is proposed for the tree).

---

## Re-check (2026-10-23 04:15-04:18 UTC; 22:15-22:18 MDT 10-22)

The orchestrator reported fixes for A1-A2 and B1-B13 (B14 left open). I re-read the new
`static/matchups.js` (25,797 bytes, 02:00 UTC), `app.py` `_json_finite` / `api_matchups` /
`_asset_version`, the report's edits (04:11 UTC), and re-ran the reproductions against the new
code with new fixture boards (`fixture2.py`: `market` blocks from `kalshi_nba_props.market_block`
on the same real raw markets; `value` / `labels` from `prop_value.line_value` / `line_labels`;
now with `unit` and `basis` on every fact as the contract requires, plus an ask-only book, a
market with no fee record, an unknown label, a 310-line player, the all-sentinel book, the
hostile strings, and a NaN/inf board). Drivers: `ui_test3.js` (tab, service workers blocked),
`ui_test4.js` (error paths). Servers on 5181/5182, stopped by PID and verified gone;
`pyflakes app.py` clean; `node --check static/matchups.js` clean.

### A -- report edits

| id | fix claimed | verdict | evidence |
|---|---|---|---|
| A1 | PFF 14.4(7) quoted in full, no legal conclusion | **FIXED** | Lines 16, 114, 116, 28, 259: the full item incl. "to any other person" and "sportsbook, exchange, or gaming operator"; the reading now says the text does not settle personal Kalshi use, "no legal reading here", ask PFF in writing. (Nit: line 16 reads 'may not be used to "use API Data to provide...' -- a doubled "use"; harmless.) |
| A2 | "sparse 40-56%" replaced by the measured 2025 fill | **FIXED** | Line 70 carries the measured figures (99.4% of attempts with a receiver, 0.0% of runs, coverage 99.9% / 0%), the targeted-receiver evidence, and the engine rule (read `route` only where `receiver_player_id` is set). "sparse" no longer appears except for 1-minute candles (line 223, correct there). |

### B -- code

| id | fix claimed | verdict | evidence (re-run) |
|---|---|---|---|
| B1 | no inline handlers; data-mx + one delegated listener | **FIXED** | `grep -c "onclick\|onchange" static/matchups.js` = 0 (comment only). Fixture player_id `x');alert(1);//`: link carries `data-val="x');alert(1);//"` (read back decoded), clicking it opened the player view with `_mx.player === "x');alert(1);//"` and the card rendered the hostile name as text. Scan of every element under `#mxRoot` for any `on*` attribute or `javascript:` href after the scan, the hostile card, a detail toggle and the compare view: **[] each time**. **DIALOGS: []** for the whole run (previously `alert: 1`). |
| B2 | every interpolation escaped (option values, k, rank, hit counts) | **FIXED** | Stat `pts" onmouseover="alert(6)` is now the option's VALUE only (no extra attribute); `k`, `rank` (`(#<b>7</b>)` as text), hits/n, the hostile label `<b>lab</b>" onmouseover="alert(12)` and a hostile `basis` (`" onmouseover="alert(11)`, now a title attribute's text) all render as text. |
| B3 | explicit `unit`; basis as tooltip; contract requires unit | **FIXED** | Facts: `usage 31.0%` (share), `ast per min 0.12 per_min`, `yds per route 0.95 yds`, `rim share 0.0%`, `minutes proj 35.20 min`, `def pts oe 1.04 ratio (#22)`, `pace 101 poss/48 (#3)`; a field without unit prints `0.42` (number, no guess). Row titles carry the basis ("assists per minute", "rim FGA share", ...). Contract line 14-15 now requires `unit`. |
| B4 | "vs market" column: p_market, market_kind, fee_assumed, model-minus-market edge | **FIXED** | Scan and player tables: `mkt 62% two_sided · -41.0 pts` beside `NO +37.3¢` (35+ line); ask-only book `mkt 82% one_sided · +8.0 pts` beside `YES +7.0¢`; no-fee-record market `mkt 82% two_sided, fee assumed · -1.5 pts`. The column sorts (`▾` on "vs market"), and the note under the table says what it is. EV and vs-market now differ, as they should. |
| B5 | `_json_finite` NaN/inf -> null; client distinguishes bad payload | **FIXED** | NaN/inf board: route 200, `JSON.parse ok`, `baseline_mean null, adjusted_mean null, p null, ev_yes null, extra [1, null]` (tuple -> list); the tab renders it (`nfl 2026-10-26` shows the board, no "could not reach"). `pyflakes app.py` clean. |
| B6 | unreachable / unreadable / unexpected-status / offline distinguished | **FIXED** | Stubbed responses, service workers blocked: network abort -> "Could not reach the server."; SW body 503 `{error, offline}` -> "Offline - the server could not be reached."; 401 HTML -> "The server sent a response the tab cannot read (HTTP 401)."; 500 HTML -> same (HTTP 500); 200 text/plain -> same (HTTP 200); 200 `{}` -> "Unexpected response (HTTP 200)."; 400 `status:none` -> its `why`, escaped; a `status:ok` payload with strings where numbers belong and null names/ids -> renders with "-", no page error. |
| B7 | "-" when no side has EV | **FIXED** | 30+ line (all-sentinel book): EV cell "-", vs market "-" (was "YES -¢ (- to -)"). |
| B8 | delta only when both means exist | **FIXED** | "Rebounds baseline - () → adjusted 8.1 ·" (no "+8.1"); Points still "(+1.3)". |
| B9 | chips name their side | **FIXED** | "High probability (NO) +EV (NO) Robust +EV (NO)" on the 21%-YES line; "High probability (YES)" on the 90% line. |
| B10 | null-safe sort keys | **FIXED** | EV ascending: the two "-" rows first, then -0.1, +7.0, +18.9, +24.9, +29.9 ...; vs-market descending: +8.0, -1.5, -20.5, -26.5, -31.5. |
| B11 | `_asset_version` includes matchups.js | **FIXED** | `app.py:61` tuple is `("app.js", "matchups.js", "style.css", "sw.js")`. |
| B12 | unknown labels raw; dead fit sort removed; cap message | **FIXED** | "Showing the first 300 of 319 lines - filter by stat or game to see the rest." (300 rows drawn); `engine_special` shown raw with title "label from the engine"; no `fit` key. |
| B13 | facts without kind tagged | **FIXED** | "unlabelled" tag on fact rows and on the "No kind" matrix row. |
| B14 | no producer yet | **OPEN, as agreed** | Still only app.py references `matchups_`; integration item for the agreement log. |

Also re-confirmed: route validation (`mlb` -> 400 with reason; traversal date -> harmless "no
board"), empty board and no-markets board render, NFL board renders, phone width 390 has no
horizontal page scroll, all 20 `/api/matchups` requests in the server log answered 200 (the
500/502 in the browser console are `/api/recorder/*` from other tabs' page load, pre-existing).

### New since the first review (minor / nits; none blocks)

| id | severity | file:line | what | evidence | suggested fix |
|---|---|---|---|---|---|
| M1 | minor | `static/matchups.js:35-41` | `prop_value.py` (edited 04:15 UTC) now returns the QUALIFIERS `thin_book` / `stale_quote` IN PLACE of `positive_ev` / `positive_ev_robust` when the book is not believable or the quote is older than 600 s. `_MX_LABELS` does not know them, so the tab prints them raw with the tooltip "label from the engine" -- a label without its meaning, on exactly the lines where the owner most needs it (the ask-only 40+ line reads "High probability (YES) thin_book" beside "YES +7.0¢"). | re-run chips list: `thin_book [label from the engine]`. | Add both to `_MX_LABELS` with prop_value's own wording ("+EV withheld: the YES book is not an opinion -- one-sided, wider than 20c, or nothing traded" / "+EV withheld: the quote is older than 10 minutes or undated") and to the legend; consider greying the EV cell when either is present. |
| M2 | nit | `:51` (`_mxStat`) | `_MX_STAT[s]` walks the prototype chain: a stat named `constructor` prints "function Object() { [native code] }". Escaped, so harmless. | row text in the re-run. | `Object.prototype.hasOwnProperty.call(_MX_STAT, s) ? ... : String(s)`. |
| M3 | nit | `:245` | Values >= 100 print with 0 decimals: pace 101.2 -> "101". | facts table. | Keep one decimal unless the unit says integer. |
| M4 | nit | `:309` | "baseline - ()" and a trailing "·" when `baseline_source` / `dist.family` are missing. | rebounds heading. | Omit the empty parenthesis and separator. |
| M5 | nit | `:250` | `basis` lives in a hover title, invisible on the phone. | design. | Show it as muted text under the row on narrow screens, or in the facts legend. |

### Re-check verdicts

- **A) `research/reports/matchup_data_sources.md`: AGREE TO SHIP.** A1 and A2 are fixed as
  claimed; the remaining items in A.1 (#2 attribution nit, #24 Sleeper nuance) are wording and
  do not steer anything.
- **B) Matchups UI + route: AGREE TO SHIP.** B1 (the blocker) and B2-B13 are fixed and
  re-verified live; M1 is the one item I would fold into the same commit because it is a label
  without its meaning (cheap: two dictionary entries), but it does not block. B14 stays on the
  agreement log as the integration item it is.
