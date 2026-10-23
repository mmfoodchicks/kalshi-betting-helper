# Independent review: the Matchups integration layer (serving, route, jobs, PC, tab, guards)

Reviewer: Fable, a fresh instance with no part in the deliverable. Written 2026-10-23 09:30-10:20 UTC
(03:30-04:20 MDT). Scope: the ORCHESTRATOR's integration work -- `matchups.py`, the `app.py` diff
(`/api/matchups`, `_json_finite`, the `nba_matchup` / `nfl_matchup` deep jobs, `_asset_version`), the
`pc_worker.py` diff, `artifacts.py` SCHEMA 6, the MX section of `tests/combo_audit_guards.py`,
`static/matchups.js` with its wiring (`templates/index.html`, `static/app.js`, `static/sw.js`),
`tests/guards_matchups.py`, the R-1 / R-2 edits in `prop_value.label_reasons` / `tests/guards_prop_math.py`,
and `research/nba_absent_reasons.py` (O4). The engines are out of scope except where the integration calls them.

Everything below was re-run or re-read by me. No file of the tree was written except this one; no git
command was run. Scratch work (scripts, logs, screenshots) is under
`/tmp/claude-0/-home-user-kalshi-betting-helper/2a51b1d4-3290-577b-b97b-fbbf27fce813/scratchpad/review_integration/`
("`$S`" below): `mutate.py` + `mutation_results.txt`, `mxfix.py` (fixtures), `run_app.py`, `publish_fixtures.py`,
`ui.js` / `ui2.js` / `ui3.js` + `ui_out.txt` / `ui2_out.txt`, `app.log`, `fake_build.log`, `suite_full2.log`,
`shot_*.png`, `sha_before.txt` / `sha_after.txt`.

Method notes. Mutation tests ran on a COPY of the tree (`$S/mut`), never on the tree; each mutation copied the
pristine file in, changed one thing, ran `python3 tests/guards_matchups.py`, restored the file and asserted it
byte-identical. The browser tests ran the real app (`VIGIL_NO_BG=1`, scratch `VIGIL_SIM_CACHE_DIR` /
`DEEP_CACHE_DIR` / `KALSHI_DB`, no login configured, port 5190, stopped and verified gone) with ONE monkeypatch
in the launcher: `matchups.build_isolated` replaced by a fake (NBA: sleep 30 s then publish the hostile fixture's
pieces and return its index; NFL: fail after 1 s; every call logged with its time). Boards on disk: the REAL
2026-10-23 NBA board (`scratchpad/board_2026-10-23.json`, 260 players, 12 games, every `market` null) under
today's MT date, a hostile NFL board under today, a small NBA board under yesterday. Playwright drove Chromium
at 1280 px and 390 px, recording every dialog, page error, `on*` attribute under `#mxRoot`, and every
`/api/matchups` request.

Tree state. In-scope files changed under me while I worked, in two batches announced by the coordinator:
first `app.py` (the `nba_matchup` registration, §D1), `tests/guards_matchups.py` (check 16 pins the new
registration string), `nba_matchup.py` (`mt_today`, `_entries(season, today)`, `rebuild(..., today)`),
`nfl_matchup.py` (slate-note wording) and `artifacts.py` (a comment line on `nfl_matchup_defhist_<season>_wNN.pkl`);
then `prop_value.py` (`labels_from`'s zero-width rule, §D3), `tests/guards_prop_math.py` (its guard, 219 checks),
`matchups.py` (`market_record` / `mark_record`, §D4), `static/matchups.js` (the `market_better` qualifier) and
`tests/guards_matchups.py` (two more checks, 20). I diffed each against the copy I took at the start; everything
in this review was re-checked against the CURRENT files (`guards_matchups` 20/20 and `guards_prop_math` 219/219 on
the real tree at 10:33 UTC; pyflakes and `node --check` clean). `sha_before.txt` / `sha_after.txt` record the
states; none of the differences were written by me.

---

## A. What I verified and AGREE with

Serving layer (`matchups.py`)
- The slimming keeps every field the scanner reads. I listed what `static/matchups.js` reads off the index
  (`player_id name team opp game_id pos status`, `markets[].stat baseline_mean adjusted_mean`, `lines[].k p p_lo
  p_hi labels fit_side market{yes_ask no_ask} value{best_side ev_yes ev_no ev_interval roi_yes roi_no edge
  p_market market_kind fee_assumed believable} hit_similar_def.n_eff`, top `games notes slate provenance
  built_ts as_of`) and every one survives `_slim_line` / `index_of`. On the real board: `index_of` 0.05 s, index
  JSON 1.62 MB (pickle 0.85 MB) against 13.2 MB for the full payload; 9,772 of 15,801 lines kept by the 2-98%
  rule with no Kalshi index attached; the 24-player piece for game 401909848 is 1.25 MB of JSON.
- Pieces are written before the index (`publish`; the child publishes with `with_index=False` and the parent's
  `nonblocking._bg` writes the index last). Mutation M01 (index first) is caught.
- No request path imports an engine: `produce()` runs only in `_child`; `board()` / `_build` / the route import
  nothing but `boardshare`, `deep_cache`, `errlog`. The child is niced and under `deep_cache.HEAVY_BUILD`.
- Memory in the web worker: Flask dev server RSS 35.8 MB -> 52.5 MB after ten index requests (the `_MEM` copy
  plus response buffers) -> 55.0 MB after two pieces; 0.09-0.20 s per 1.43 MB index response; one piece
  unpickle per `?game=` request. `_MEM` holds at most `_MEM_KEEP` (4) slates; the flight tuples are not strings
  and are popped by `_bg`'s `finally`. In one process I confirmed: stale served while the rebuild thread runs
  (`building` True), one thread per build, zero threads and no lock file left after it finishes.
- The date rule. `buildable` accepts exactly today and tomorrow in MT; a past date is served from disk only and
  never builds (`fake_build.log` has no entry for 2026-10-22 / 2026-10-01 / 2026-11-30 across all three browser
  runs). `today_mt()` is `clock.mt_date(time.time())` -- machine-timezone-independent, so the server (UTC) and
  the PC agree on the key. An NBA night is one MT date (the latest tip is 8:30 pm MT). The engine's own refusal
  (`nba_matchup.board`: "the matchup book already holds games through ...") is a second fence behind it.
- `_json_finite`: NaN / inf leave as null, tuples as lists; the real piece and index serialise with no bare `NaN`
  or `Infinity` token (checked the raw bytes).
- Route validation: `mlb` -> 400; `2026-13-40`, `2026-10-23T00:00`, `../../etc/passwd` -> 400 "date must be
  YYYY-MM-DD"; `20261023` and `2026-W43-5` are accepted and CANONICALISED to `2026-10-23` by `fromisoformat` ->
  `isoformat` (fine: one file name per day); `game=11;x`, `game=401909848%00`, `game=None`, `game=null` -> 400; a
  280-digit game id -> "not published yet". The game id reaches a file name only after `isdigit()` and
  `boardshare._path`.
- An engine `error` rides in the slate notes of the index AND of every piece; `slate` is carried (guard check 4,
  mutation M07 caught; the hostile board's `error` string is drawn, escaped, above the slate).
- `nfl_data_job` against the REAL `nfl_matchup_data` API (fetch stubbed to raise, no network): `_current_season`
  2026, `load_season`, `default_seasons`, `refresh(seasons=, keep_raw=, log=)` all exist with those names;
  the job runs end to end and would fetch pbp 2024/2025/2026, `players.csv`, `db_playerids.csv` (participation and
  FTN follow a successful pbp). The backfill deletes its raw files (`keep_raw=False`) -- the 49 MB participation
  file is the disk concern on 1 GB and it is handled for the backfilled seasons.

Route and jobs (`app.py`)
- `run_nba_matchup` / `run_nfl_matchup` follow the `run_pro_season` pattern exactly: `in_season` first,
  `_deep_pc_first` -> `DEFERRED`, then one niced child under `HEAVY_BUILD` (`nba_matchup.run_job_isolated`,
  `matchups.job_isolated("matchups", "nfl_data_job")`); registered beside the other deep jobs; `pc_worker._task_deep`
  runs the same bodies and saves under the same keys, so the server's scheduler sees the PC's upload as "already
  ran". `_asset_version` includes `matchups.js`.

PC (`pc_worker.py`)
- The two board builders use the same `matchups.board` the server serves from, so the pieces have the same names
  (`boardshare._path` keeps `-` and digits; `artifacts._NAME_RE` accepts them at the upload door). The uploads
  are schema-gated (6 both sides).

Artifacts / guards plumbing
- `SCHEMA = 6` with a comment naming every new pickle; the soccer guard's `== 5` relaxed to `>= 5`. The MX
  section imports the five guard files and turns an import/raise into a FAIL (`"<mod> runs to the end"`),
  never a skip. I ran the WHOLE suite on a scratch copy of the tree (`$S/full2`, with `.git`): 3,037 passed,
  1 failed, 511 checks after the MX banner. The one failure ("the standings drop folder arrives with a git
  pull and its README...") is my copy's: my tar excluded `./pc-*`, so `pc-standings/README.txt` was absent.
  Not a defect of the tree.

Tab (`static/matchups.js`) -- re-checked live against the hostile board (every field an attack string)
- No inline handler anywhere; `on*` attributes under `#mxRoot` after the scan, a card, a detail toggle, the
  compare view and the phone layout: ONLY the two in finding F1 below (`best_side`, `fit_side`). Dialogs fired
  across all three runs: none (`alert` never ran -- see F1 for why that is luck, not safety).
- Text escaping holds for: name, team, opp, pos, status value/source, notes (slate and player), `slate`, `as_of`,
  provenance source/covers, game home/away, stat (option VALUE and text), `k`, labels, `market_kind`, factor /
  player / opponent / assessment text, effect stat, evidence kind, every detail key/value (nested objects via
  JSON then escape), role keys / unit / basis (title attribute AND the visible small line, so M5 of the earlier
  review is addressed), rank, comparables, baseline_source, dist.family, the route's `why`. `constructor` /
  `__proto__` as stat, label or role key render as plain text (`hasOwnProperty` guards in `_mxStat`, `_mxChips`).
- Units: `share` -> percent, `per_min` -> "0.12 per_min", `x_league` -> "1.10 x_league (#3)", NaN fact -> "-",
  string fact -> text. NaN `p` -> "-" everywhere; `p_hi: "x"` -> "-". Cents and EV in cents, ROI percent, MT times.
- Per-game pieces: the player page fetched exactly its game (`&game=401909848`); compare fetched the two games
  once (`_mx.full` held 3 keys after three distinct games); a sport switch mid-fetch (piece delayed 1.5 s by a
  route hook) dropped the NBA piece (`_mx.full` empty, sport `nfl`, no error); a missing piece says "That game's
  detail is not published yet - reload the slate."
- Weight-0 fold on the real board: 29 rows shown, "Show the 27 factors measured but held at weight 0" -> 56 rows,
  each weight-0 row's effect cell "not applied", then "Hide the 27 ...". Slate notes drawn above the status line.
  "Strong fit (NO)" names its side from `fit_side`; "High probability (NO)" / "+EV (NO)" name theirs; the
  `thin_book` qualifier is a muted chip and the EV cell reads "NO +5.5¢ (thin book)"; an all-sentinel book -> "-".
- Building state: "Building the NBA matchup board for 2026-10-24 - about half a minute; this page retries."; the
  retry fired at 20, 40, ..., 160 s and then stopped (8 tries, as pinned); a stale board stays on screen while
  it refreshes ("(N min ago, refreshing)").
- Phone (390 px): `scrollWidth` 390 on the scan and the player page (tables scroll inside `.scroller`).
  Screenshots: `shot_scan_real.png`, `shot_player_real.png`, `shot_compare_real.png`, `shot_scan_hostile.png`,
  `shot_evil.png`, `shot_phone_scan.png`, `shot_phone_player.png`.
- Wiring: tab button, `#tab-matchups`, `#mxRoot`, the script after `app.js` (so `$` and `escapeHtml` exist),
  `initMatchups` behind `typeof`, the service worker precache entry (no SHELL bump needed, as the earlier review
  said). `node --check` clean; pyflakes clean on `matchups.py`, `app.py`, `pc_worker.py`, `artifacts.py`,
  `tests/guards_matchups.py`, `research/nba_absent_reasons.py`.

Prop math edits (R-1, R-2)
- `label_reasons` (`prop_value.py:894-905`): a one-sided book that has traded says "-- no offer on the other
  side", an untraded one "-- a listing, not an opinion" (`traded > 0 and kind == one_sided`). The guard at
  `tests/guards_prop_math.py:1389-1393` asserts both the presence of "no offer on the other side" and the
  absence of "listing" on the traded ask-only case. AGREE.
- `tests/guards_prop_math.py:1365` `_tk = lambda m: (m or {}).get("ticker") or ""` -- a None lookup is now a
  FAIL line, not a traceback. AGREE.

`research/nba_absent_reasons.py` (O4)
- I ran it (`python3 -I`, stdlib only, 70 s) on the raw summaries at `scratchpad/data/espn_nba_summary` for 2024
  and 2025: `{'2024': 1646, '2025': 1709}` = 3,355 absences; no box line 57.1% (59.4% by minutes); inactive with a
  reason 24.7% (26.0%); COACH'S DECISION 18.2% (14.6%). Every figure matches the docstring and the review's table
  exactly. The script does what the review says: a regular = 15+ minutes in his team's PREVIOUS regular-season
  game (season type 2, games in date order, `last` reset per season), missing from this one; the three buckets
  partition the absences; COACH'S DECISION is matched exactly, so the headline ("about one in seven by minutes is
  a coach's decision production can never see") rests on an exact match. Two wording nits in §C (N9).

---

## B. Findings

Severity: blocker / major / minor / nit. Every "reproduced" item has its evidence in `$S`.

| id | sev | file:line | the failure (inputs -> wrong output) | fix |
|---|---|---|---|---|
| **F1** | **major** | `static/matchups.js:85` (`_mxChips`: `${side ? \` (${side})\` : ""}`), `:105` (`_mxEvCell`: `${b.side.toUpperCase()}`) | The ONE pair of interpolations in the tab that are not escaped. `side` comes from `value.best_side` (`_mxChips`, `_mxBest`) and from `line.fit_side` (`_mxChips`) and is written raw into `innerHTML`. Reproduced: fixture `best_side = "<img src=x onerror=alert(9)>"`, `fit_side = "<img src=x onerror=alert(8)>"` -> the DOM holds `IMG onerror=ALERT(9)` (x2, scan EV cell and chip) and `IMG onerror=ALERT(8)` (chip), and the handler RAN: 3 page errors `ReferenceError: ALERT is not defined` per render (`ui_out.txt` B1, `ui2_out.txt` E2). Only `.toUpperCase()` turned `alert` into `ALERT`; HTML is case-insensitive where it matters and numeric entities (`&#97;&#108;&#101;&#114;&#116;(1)`) survive uppercasing, so this is the earlier review's B1 class, not a mitigated one. Today both fields are produced by first-party code with values `yes` / `no` / null (`prop_value.line_value:783`, `nba_matchup.fit_side`, `nfl_matchup.line_labels`), so no third-party string reaches them; it is a defence-in-depth gap on the one path the earlier fix was meant to close, plus the PC-upload path. | `const s = side === "no" ? "NO" : side === "yes" ? "YES" : ""` (whitelist) or at least `_mxEsc(side)` in both places; same for `b.side` in `_mxEvCell` and the ternaries in `_mxBest`. Guard (hermetic, no browser): a Python check that `_mxChips` and `_mxEvCell` source contain no `${side` / `${b.side` outside `_mxEsc(`; better, run `static/matchups.js` under `node` (present in the sandbox and on the CI runner) with stubs for `$`/`escapeHtml` and assert `_mxChips({labels:["positive_ev"], value:{best_side:"<img>"}})` contains no `<img`. |
| **F2** | **major** | `pc_worker.py:313-314` (`_task_boards`), `matchups.py:134-149` (`publish`), `:236-252` (`board`) | **The PC can replace the server's full board with an empty one** -- against CLAUDE.md's "the PC can only add speed or be ignored". `_task_boards` builds `matchups.board("nba")` on whatever the PC has. On a machine whose deep store lacks the NBA book, `nba_matchup.board(date)` returns in 0.0 s, before any fetch, with 0 players and the note "no matchup book for 2026 yet (the nba_matchup job has not run)" (reproduced with an empty `DEEP_CACHE_DIR` and sockets blocked). `publish` writes that as a valid, fresh index; `_sync_kind` ships every local board fresher than the server's by 60 s; `boardshare.nonblocking` on the server prefers a fresh disk copy to its own rebuild for `TTL` 900 s. So the owner's tab says the job "has not run" while the server's own book is fine. When: (a) the PC's FIRST cycle after this deploys -- `_cycle` runs `boards` BEFORE `deep nightly`, so the book does not exist yet when the board is built; (b) any cycle while the PC lacks the book: its own `_due` is 19 h on the SERVER's marker, so a PC that was off when the server ran the job itself does not run it until the server next stamps a request (now up to ~6.5 h, §D) -- every cycle in between ships the empty board; (c) a wiped PC deep store. The NFL side has the same mechanism by reading (`nfl_matchup._board` continues with `ctx None` -> no profiles, or `error` -> an error board); CANNOT VERIFY it live (the schedule fetch needs the network). | Make an empty slate unable to out-freshen a full one: in `publish`/the child, when `not idx["players"]` publish the index BACKDATED (`boardshare.put(name, idx, age=TTL - 60)`) and have `_build` return None so the parent publishes nothing newer -- the server then serves it for at most 60 s and rebuilds, and `_sync_kind` never ships it over a server copy younger than ~14 min. And/or in `pc_worker._task_boards` skip `matchups-nba` when `nba_matchup.load_book(season)` is None and `matchups-nfl` when `nfl_matchup.production_ctx(season)` is None, printing why. Guard: `publish` of a 0-player payload -> `boardshare.get(name, TTL)` is None while `get(name, None)` is not. |
| **F3** | **major** | `matchups.py:170-184` (`build_isolated` returns None, publishes nothing), `app.py:2354` ("about half a minute; this page retries"), `static/matchups.js:188-193` | **A failing build is relaunched on every request, and the tab's statement becomes false.** Reproduced with the NFL fake failing after 1 s: 9 child launches in 170 s (`fake_build.log` 09:50:50 ... 09:53:30), one per retry; after the 8th retry the body still reads "Building the NFL matchup board for 2026-10-24 - about half a minute; this page retries." and nothing retries again (`ui2_out.txt` F2/F3). On the server the owner sees that line indefinitely (with a stale board behind it if one exists), the ledger has `MX-child`, the tab says nothing. The 160 s retry window is also shorter than a real build can take: in my harness the first NBA build entered its child 41 s after the request because `deep_cache.HEAVY_BUILD` was held by the tennis Elo pools build the page load had kicked off (`tennis_elo_pools.pkl` written 09:46:00 = the fake's entry time; `app.log`, `fake_build.log`), then 30 s of build; production's gate holders (a slate child, the soccer refit) are longer, and the child's own timeout is 240 s. | On a failed child publish a short-lived placeholder index (`players: []`, notes `["the build failed this time -- ledger MX-child"]`, `boardshare.put(..., age=TTL - 120)`) so the route answers `status ok` with the reason and `building False`, and the next visit after two minutes tries again -- the pattern every other board uses. In the tab, when `_mx.tries` reaches the bound, replace the text with "still building after N tries -- reload to check" instead of leaving the server's sentence. Guard: a fake `build_isolated` returning None -> the route's second call within a minute does not start a second build and `why` names the failure. |
| F4 | minor | `matchups.py:222-226` (`nfl_data_job`) | Before October, on a machine where the previous season has no store, the previous season is built TWICE and its raw files are downloaded twice: the backfill (keep_raw False) deletes them, then the daily `default_seasons` (month < 10 -> `[cur-1, cur]`) finds no file, re-downloads 2025's 19 MB pbp + 49 MB participation + 8 MB FTN, rebuilds, and this time KEEPS the raw files -- the disk saving the backfill exists for is undone. Reproduced with a fake module and August: `[([2025, 2026], False), ([2026, 2027], True)]`. NFL `in_season` runs from Aug 1, so a fresh server or PC in August / September hits it. | `daily = D.default_seasons(cur, month)`; `back = [s for s in (cur-2, cur-1) if s not in daily and D.load_season(s) is None]`. Guard: the existing fake with month 8 and both stores missing -> `[([2025], False), ([2026, 2027], True)]`. |
| F5 | minor | `matchups.py:140,145` (`publish`), `:130` (`index_of`), `static/matchups.js:200-203` | A player with no `game_id` (the contract forbids it; nothing enforces it) is listed in the index but his piece is written as `matchups_<sport>_<key>_gNone.pkl` -- a file no route can read (`isdigit`). The tab fetches `&game=null` -> 400 -> his card reads "game must be a game id" (reproduced: `ui_out.txt` C4; the `_gNone.pkl` file is on disk). | In `publish`, drop players without a game id from both the index and the pieces and ledger `MX-nogame` with the count; in `_mxCardFor`, a player whose `_mxGameOf` is "" prints "He is not on this slate's published detail." |
| F6 | minor | `static/matchups.js:414-421` (`_mxCardFor`), `:200-203` | A player page (or compare slot) left open while the date changes to a slate the player is not on shows "Loading his matchup detail…" forever: `_mxGameOf` returns "", `_mx.full[""]` is undefined, and `_mxNeedPieces` never fetches. Reproduced (`ui3.js`): open a player on 2026-10-23, change the date to 2026-10-22 -> body "Loading his matchup detail…" still there 7 s later; same in compare. | In `_mxCardFor`: `if (!_mxGameOf(id)) return "<div class=empty>He is not on this slate.</div>"`; or reset `_mx.player` / `_mx.cmp` in `_mxChange("date")`. |
| F7 | minor | `pc_worker.py:313-314, 336-339` | `_task_boards` reads `matchups.board`'s tuple with `out[0]`; when a stale index is on disk, `board()` returns `(stale, age, True)` -- not None -- so the loop prints "board matchups-nba: fresh" at once and stops re-invoking, while the rebuild runs in a daemon thread that dies with the worker process if the cycle ends first. Every other board returns None while rebuilding, which is what the wait loop expects. Reproduced in-process (first call on a stale index: stale served, `building` True). | `_add("matchups-nba", lambda: (lambda r: None if r[2] else r[0])(__import__("matchups").board("nba")))`, same for nfl. |
| F8 | minor | `tests/guards_matchups.py:166` | The "no inline handler" regex covers `on(click|change|input|submit)=` only. Mutation M23 added `onmouseover="mxOpen('${_mxEsc(...)}')"` to the open link and the guard stayed green. The earlier review's B1 was specifically about handlers, not about these four names. | `\bon[a-z]+\s*=` on non-comment lines -- zero hits on the current file (checked), so no false positive. |
| F9 | minor | `static/matchups.js:301` + the real board | When Kalshi's index was unavailable at build time (every `market` null -- exactly the real 2026-10-23 board), the default tick "only lines Kalshi lists" hides all 9,772 lines and the table says only "No lines match." (0 rows drawn; 300 of 9,772 after unticking). The owner has no hint that the lines exist. | Say it: "No lines match -- N lines are hidden by 'only lines Kalshi lists'" (count the unlisted rows). |
| F10 | nit | `matchups.py:110-115` with `nfl_matchup._with_reason` | The NFL engine already copies `error` into `notes` as "The NFL matchup board could not be built: <error>"; `index_of` appends the raw error again, so an NFL error board carries the reason twice in different words (reproduced: two notes). | Skip the append when any note already contains `str(payload["error"])`. |
| F11 | nit | `app.py:2343` | `str.isdigit()` is Unicode: `game=７００１` (fullwidth) and `game=²` pass the check, reach `matchups.game` and `boardshare._path` (whose `isalnum` keeps them) as a non-ASCII file name. Harmless -- no file matches -- but the stated intent is "a game id". | `gid.isascii() and gid.isdigit()` (or `re.fullmatch(r"\d{1,20}", gid)`). |
| F12 | nit | `static/matchups.js:341,350` | `tone[f.assessment]` walks the prototype: assessment `constructor` -> `style="color:function Object() { [native code] }"`, `__proto__` -> `color:[object Object]` (reproduced). Harmless CSS. | `Object.prototype.hasOwnProperty.call(tone, a) ? tone[a] : "inherit"`. |
| F13 | nit | `tests/guards_matchups.py:54, 113` | Not fully restored: `matchups._MEM` keeps four fake `matchups_nba_2026-11-0N` keys after `run()`, and the `mkdtemp` directory is never removed. `boardshare._DIR`, `put`, `nonblocking`, `today_mt`, `sys.modules["nfl_matchup_data"]` ARE restored. Four regressions (M07, M08, M40, M41) end in a KeyError traceback rather than a FAIL line -- the suite still goes red (R-2 class). | `matchups._MEM.clear()` and `shutil.rmtree(tmp)` in the `finally`; `.get()` on the asserted keys. |
| F14 | nit | `matchups.py:236` (`board`), `pc_worker.py:313-314` | No season gate: out of season the PC builds both boards every cycle and the server on every TTL expiry of a visit; the NFL one loops `nfl_live.schedule` for weeks 1-18 (18 ESPN calls) to produce an error board. | `pro_sim.in_season(sport)` in `board()`: serve a one-note index without building. |
| F15 | nit | `static/matchups.js:169,231-246` | While a sport switch loads, the previous sport's status line (its notes included) stays above "Loading the NBA matchups…" (seen in `ui_out.txt` A1: NFL's hostile notes over the NBA loading body). | Clear `#mxStatus` in `mxLoad` when `_mx.data` is nulled. |
| F16 | note | `matchups.py:245-250` vs gunicorn | `building` is per worker: a worker whose `_MEM` still holds a stale copy answers `building False` (and the tab's retry stops) while the worker that started the rebuild answers True. The owner may see a 16-minute-old board without "refreshing" until the next reload. The stale age shown is correct. | None required; mention in the docstring. |

---

## C. Other observations

- N1. `nonblocking`'s thread does not leak: thread count returned to baseline after each build; the claim lock is
  released in `finally`; a hung child is killed by `subprocess.run(timeout=240)`. The claim is cross-process, so
  the retry storm F3 describes is one child at a time (per `CLAIM_TIMEOUT`), not N.
- N2. `HEAVY_BUILD` is a per-process semaphore, so a matchups build in a web worker waits only for heavy children
  of THAT worker, and runs beside the background owner's deep child in another process. That is the repo's
  existing pattern; the child is niced. Not a finding.
- N3. The real board's `start_ts` are not consulted by the serving layer: a "today" board rebuilt at 23:00 MT
  carries that night's finished games, with whatever Kalshi then quotes. The engine's `fetch_games` keeps states
  2/3/5 and `board()` lists them all; whether in-play lines should be dropped is the engine's call (its review),
  but the serving layer is what makes the rebuild happen through the night (TTL 900 s on every visit).
- N4. Off the real board, `index_of` keeps the top-level `definitions` and `timing_s`; neither is read by the tab.
  Harmless (a few KB).
- N5. The mutation test (`$S/mutation_results.txt`, 43 mutations; `$S/mutate2.py`, 12 more for §D3/§D4): 50 caught,
  1 control survived as intended (no change), 3 survived as real gaps (M23 -> F8; N05 -> F19; N09 -> F21), and one
  harness artifact -- a same-size, same-second restore (`SCHEMA = 5` -> `6`) left a stale `.pyc`, which clearing
  `__pycache__` fixed (18/18, later 20/20, after). The string-pinned checks (8, 12-16, 18) catch their textual
  mutations; the behavioural checks (1-7, 9-11, 17, 19, 20) catch theirs. Per CLAUDE.md "a guard pins a number;
  only a reader pins its meaning": checks 16 and 18 pin the shape of the registration lines, which is right for
  wiring; check 20 pins the record's VALUES and key sets but not its definition (§D4); F1-F3 above are the
  behaviours no check pins.
- N6. `nfl_data_job`'s backfill reruns whenever a season's store is at an older `nfl_matchup_data.SCHEMA` -- by
  design ("has no store at the current SCHEMA"); the raw files are deleted again after it.
- N7. `_asset_version` and the service-worker precache: fine; `caches.match` of the versioned request against the
  unversioned precache key is the pre-existing pattern (the earlier review's B15).
- N8. R-1's wording: "{traded:g} contracts traded or open -- no offer on the other side" reads well for 131,000;
  for a book with `volume` + `oi` = 1 it says "1 contracts". Nit, not re-raised.
- N9. `research/nba_absent_reasons.py`: (a) the third bucket is labelled "injury/rest/personal reason (inactive)"
  but takes EVERY `didNotPlay` reason other than COACH'S DECISION, including `SUSPENDED BY LEAGUE` (18),
  `NOT WITH TEAM` (9) and `DNP (no reason)` -- I listed the 829 reason strings (`$S/dnp_reasons.py`): REST 60,
  ILLNESS 40, then injuries; the label should read "any other DNP reason (injury, rest, illness, suspension, not
  with team)". (b) The first bucket's label names "G League" and "traded", which the box cannot tell from
  "omitted"; the review's table already says "or simply omitted". Neither moves the headline figure.

---

## D. The four changes that landed mid-review

**D1. `app.py:1725-1733` -- `deep_cache.register("nba_matchup", run_nba_matchup, max_age=6 * 3600, cadence="age")`.**
AGREE, with notes. The reason holds: the new `nba_matchup._entries(season, today)` drops every game whose MT day
is `today` or later (`mt_today()` = `clock.mt_date(time.time())`, machine-independent), and a once-a-night run at
the ET-midnight slot (22:00 MT) would replay a book without that MT day's games, leaving the next day's board a
day behind. With `cadence="age"`, `run_job` is attempted on the scheduler's 30-minute pass whenever the saved
marker is older than 6 h (`is_stale`), so runs land every 6-6.5 h and at least one falls in [00:00, 06:30) MT --
"current by 6 am MT" is right to within the half-hour tick. Interplay checked: PC-first is unchanged (`DEFERRED`
while the request is pending, the server runs it itself after `_DEEP_PC_WAIT_S` 45 min); the PC's own 19-hour
`_due` is overridden by the request marker, so the PC also runs it ~4x a day; `run_job`'s `today` is the
machine-local date but only bounds the scoreboard fetch -- the cut is `rebuild(...)` -> `_entries(today=None)` ->
MT on both machines. Cost: I timed `rebuild(2026)` on a copy of the scratch book (1,213 + 15 games): 2.5 s,
160 MB peak RSS in-process -- consistent with the comment's "3.9 s, two seasons"; the registration's 4x exposure
of the ~250 MB child (the job's docstring, incl. the update) is the same per run as before, so the memory risk is
unchanged per event and four times as frequent. The updated guard string catches the cadence reverting (M42) or
widening to 24 h (M43). Side effect on F2: the request marker every ~6.5 h shortens the window in which a PC that
missed the job keeps shipping an empty board, from "until the next midnight" to "until the next request".

**D2. `matchups.index_of` / `publish` copy `error` into the slate notes and carry `slate`.** AGREE. Verified by
guard check 4 (M07 caught), by the hostile board (the error string is drawn above the slate, escaped), and by
reading: the error note rides in the index and in every piece's `top`. One nit (F10): the NFL engine already
writes the reason into `notes`, so NFL error boards say it twice.

**D3. `prop_value.labels_from` refuses `positive_ev_robust` on a band KNOWN to have no width
(`prop_value.py:831`: `... and (width is None or width > 1e-9)`).** AGREE. "Still positive at the band's low end"
is the same statement as "positive" when `p_lo = p = p_hi`, so awarding "robust" there was a tautology dressed as
evidence. Verified: `labels_from(p_side=.79, ev_c=25.2, ev_lo_c=25.2, width=0.0)` -> no robust; `width=0.06,
ev_lo_c=3.0` -> robust; through the real path `line_labels(line_value(.79, book, .79, .79))` -> no robust (the new
guard in `tests/guards_prop_math.py:517-527`, 219/219). My mutations: reverting the condition -> red (218/1, as
the coordinator reported); refusing robust on EVERY band -> red (213/6, so a real band still earning it is
pinned). `line_labels` passes `width=None` only when `p_lo` is None, so the one way robust can still be awarded
without a band is a direct `labels_from` call with `ev_lo_c` and no `width` -- the "KNOWN" in the docstring is
honest about that. I could not see the 2026-10-25 NFL board, so the "99% of lines zero-width / 786 robust" figure
is CANNOT VERIFY; the rule is right regardless of the count. For the engine reviewers, not this file: a band with
no width on 99% of lines means the NFL board's `p_lo` / `p_hi` carry only the matchup shift's uncertainty and
none of the baseline's -- "Low evidence" and the band column then say nothing on those lines.

**D4. `matchups.market_record(sport)` / `mark_record(payload, record)`: every +EV line in a stat whose held-out
record has Kalshi's pre-game log loss below the model's gets the qualifier `market_better`; the tab draws it as a
muted chip.** AGREE with the mechanism, DISAGREE with two parts of the definition and the presentation (F17, F18).

What I verified. `market_record("nba")` reads `research/data/nba_matchup/coefs.json` `market_test`: `by_stat` ast /
pts (market better), reb / fg3m (model better), `*` = `tight` (n 2,278, 0.542 vs 0.556, market better);
`market_record("nfl")` reads `kalshi.json` `by_season["2026"]`: pass_yd / rec / rec_yd / rush_yd / td and `*` = all
rungs (n 1,510, 0.432 vs 0.456), the market better in every one. `mark_record` on a payload with INDEPENDENT label
lists per line (my first fixture aliased one list across two lines and misled me for a minute): pts, ast, pra,
dk_fp, stl -> `market_better` appended; reb, fg3m -> untouched; non-+EV lines untouched; `publish` applies it
before the pieces and the index are cut, so both carry it (checked on the published index). The tab: the chip
reads "market better on record" with a tooltip, escaped; the legend concatenates `_MX_QUALS`, so it is listed.
Mutations N01-N04, N06, N07, N10 caught; N05, N08, N09 are F19-F21.

The three questions asked:

(a) *Is the record's definition honest?* NFL: yes -- one season (2026, frozen pre-kickoff lines), the stat's own
rungs or the union of all rungs, the same population either way. NBA: not as written. `by_stat` is computed over
ALL two-sided books (`research/nba_matchup_kalshi.py:314`) while the fallback is the TIGHT books, spread <= 10c
(`:313`). The report's own clustered SEs (`research/reports/nba_matchup.md:572-579`) say the market beats the
model on tight books by 0.0138 +/- 0.0041 nats (z -3.3) and that on all two-sided books the two are TIED (+0.0018
+/- 0.0020). So the chip's absence on reb and fg3m rests on per-stat point estimates (0.015 and 0.026 nats, n 686
and 593) from the population on which the report itself finds no difference, while every other stat is qualified
off the population where the market clearly wins. The owner reads a missing chip as "model better on record for
rebounds and threes", and the one statistically clear fact (tight: market better; one-contract bets -14.3% over
485) is slate-wide. The tooltip compounds it: "the note at the top has the numbers" -- the note has the tight
figures, not the by-stat ones that decided the chip. Fix (F17): compute the by-stat record on the SAME population
as the fallback (`tight_by_stat`: one line in `nba_matchup_kalshi.py`, `summary([r for r in recs if r["stat"] == s
and r["spread"] <= 10])`, re-run, commit) and read that; until it exists, qualify every NBA stat off the tight
record. Either way print the chip's own basis (stat, n, both log losses, population) from `market_record`, which
the index already carries (F21).

(b) *Qualifier beside +EV, or withhold +EV?* `thin_book` / `stale_quote` REPLACE the EV labels because the price is
not evidence; here the price is real and executable and the EV arithmetic is right -- what the record impeaches
is the probability. So a qualifier is the right KIND of statement, and withholding would blank every +EV label on
the NFL scanner (the market won every stat), which is honest but hides the owner's own question. The qualifier is
acceptable only if the tab does not headline the number it qualifies, and today it does (F18): the EV cell is
bold (`_mxEvCell(b, true)`, `b.ok` ignores the qualifier) and the EV sort ranks the line with every believable
line, so the default Opportunities view leads with bold "+4.2¢" cells the record says lost money, the qualifier
sitting last in the Labels column (reproduced: row `Clean Player | Points 20+ | YES <b>+4.2¢</b> | ... market
better on record`). Mute the cell and demote the sort exactly as `thin_book` does (`ok: v.believable !== false &&
!labels.includes("market_better")` or a third state), keep the label, and the slate note.

(c) *Reading research JSON from the serving layer?* Acceptable: the files are committed, the engines already read
beside them (`nba_matchup.params()` <- `research/data/nba_matchup/`, `nfl_matchup.load_coefs()` <-
`research/data/nfl_matchup_model/coefs.json`) and the repo commits-and-reads research tables elsewhere
(`nfl_deep_tables.json`); the read happens once per publish in the child or on the PC, never per request; a
failure is ledgered. Two caveats: a missing or renamed file degrades SILENTLY into "no qualifier anywhere" (F20),
and the two research schemas are now parsed in `matchups.py` rather than beside the code that writes them
(F23, design note).

| id | sev | file:line | the failure | fix |
|---|---|---|---|---|
| **F17** | **minor, required** | `matchups.py` `market_record` (NBA branch), `research/nba_matchup_kalshi.py:313-314`, `static/matchups.js:54` (tooltip) | The NBA by-stat record and its fallback are measured on different populations (all two-sided books vs tight); reb and fg3m +EV lines carry no qualifier on the strength of per-stat point estimates from the population the report calls a tie, while the tooltip points at the tight numbers. Reproduced: `mark_record` leaves reb / fg3m unmarked, marks pts / ast / pra / dk_fp / stl. | Same population for stat and fallback (`tight_by_stat`, committed), or the tight record for every NBA stat until then; the chip prints its own basis from `market_record`. |
| **F18** | **minor, required** | `static/matchups.js:91-107` (`_mxBest`, `_mxEvCell`), `:275` (EV sort key) | A `market_better` line's EV is bold and sorts as a believable line; the qualifier is the last chip. The default view headlines what the record impeaches. | Treat `market_better` like `thin_book` in the EV cell (muted, "(market better on record)") and the sort; keep the label. |
| F19 | minor | `tests/guards_matchups.py:97-121` | `publish` applying `mark_record` is unpinned: removing the `mark_record(payload, market_record(sport))` call in `publish` left both new checks green (mutation N05). | After `publish`, assert the published index's +EV line in a market-better stat carries the qualifier (monkeypatch `matchups.market_record` so the check does not depend on the committed values). |
| F20 | minor | `matchups.py` `market_record` (`except (OSError, ValueError)` -> `{}`) | A missing / unreadable record file -> `{}` -> no qualifier on any line, and nothing on the tab says the record was unavailable (ledger `MX-record` only): the failure mode is silent flattery. Mutation N08 (renamed file) went red only by a KeyError in check 20, not a FAIL line. | When the sport has a record file and the record comes back empty, append a slate note ("held-out record unavailable -- +EV lines are unqualified") and have check 20 use `.get()`. |
| F21 | nit | `matchups.py:58` (`_TOP_KEYS`), `static/matchups.js` | `market_record` rides on the index and every piece (13 copies a slate) and nothing reads it (`/market_record/` is absent from the tab's code). Mutation N09 (drop it from `_TOP_KEYS`) survived, as expected. | Read it in the tooltip (F17) or drop it. |
| F22 | nit | `static/matchups.js:54` | The tooltip says "On the engine's held-out test" for both sports; `kalshi.json` is `rule: "R5 (descriptive)"` for NFL. | "On the engine's record against Kalshi's pre-game prices (<season>, n ...)". |
| F23 | note | `matchups.py` `market_record` | Two research-file schemas parsed in the serving module; the engines own those files. | `nba_matchup.market_record()` / `nfl_matchup.market_record()`, called from `publish`. No change in behaviour. |

---

## E. Verdict (first pass, 10:20 UTC; superseded by the RE-CHECK below)

**AGREE AFTER FIXES.** Required before ship: **F1** (two escapes -- the one unescaped pair in a module whose
earlier blocker was exactly this), **F2** (an empty slate must not out-freshen a full one; one PC cycle after
deploy WILL ship one), **F3** (a failed build must publish a short-lived reason and the tab must stop saying it
retries), **F17** (the NBA record's stat and fallback populations must agree, or every NBA stat reads the tight
record, and the chip states its basis) and **F18** (a qualified EV is not headlined). Each is a few lines and
each wants a guard that fails on the current code (suggested beside each). F4-F9 and F19-F20 belong in the same
commit if cheap (F4, F5, F6, F7, F8, F19 are one-liners); F10-F16 and F21-F23 are nits. Everything in §A I re-ran
and agree with as it stands; of the mid-review changes, D1, D2 and D3 are sound and D4's mechanism is sound with
F17/F18 applied.

---

## RE-CHECK (2026-10-23 14:40-15:05 UTC; 08:40-09:05 MDT) -- the fixes

The orchestrator reported every finding addressed except F23 (left as is, agreed: the schemas are named in
`market_record`'s docstring). Files re-read in full against my pre-fix copies: `matchups.py`, `static/matchups.js`,
`app.py` (route), `pc_worker.py`, `tests/guards_matchups.py` (29 checks). Re-run: `guards_matchups` 29/29 on the
real tree, pyflakes clean, `node --check` clean. Mutations (`$S/mutate3.py`, 24 on the scratch copy, every file
restored and asserted byte-identical). Browser: the same hostile fixtures published through the new `publish`
(`$S/ui5_out.txt`, `ui6_out.txt`, `ui7` / `ui8_out`), the server running `run_app2.py` whose fake builder returns an
EMPTY slate for NBA (the no-book machine, F2's server path) and a failed child for NFL (F3). Server stopped, port
free, nothing of mine left running. `sha_recheck.txt` holds the final hashes; the tree holds the orchestrator's
edits and this file only.

### Required fixes

| id | verdict | evidence |
|---|---|---|
| F1 | **FIXED** | `_mxSide` whitelists YES/NO; `_mxChips` wraps the side in `_mxEsc`; `_mxBest` normalises `best_side`. Live, hostile `best_side` / `fit_side` = `<img src=x onerror=alert(N)>`: on* attributes under `#mxRoot` after the scan (listed and all), the hostile card, every detail toggle: **[]**; dialogs []; page errors [] (the earlier run had three `ReferenceError: ALERT`). The chips read "+EV" / "Strong fit" with no side. Mutation R01 (whitelist off + `_mxBest` normalisation off + escape off) -> the new node harness goes red for the chip and the EV cell; R02 (whitelist off alone) survives because the other two layers hold, as the orchestrator said. The node part of the guard is soft: with node off PATH the check passes on the source regex alone (29/29 verified with a node-less PATH) -- acceptable, since CI and the sandbox have node, but the string half is what runs on a PC without it. |
| F2 | **FIXED** | Server: `_build` returns None for an empty or failed slate; publishes it only when no full board exists for the date, BACKDATED to `TTL - _RETRY_S`; the PC calls `pc_board`, synchronous, publishes only a slate with players, returns `{"skipped": why}` never None. Live (NBA tomorrow, fake empty slate): first answer "Building ...", one child launched, the retry 20 s later served the empty slate from disk with its note, `building` False, age 798 s (= 780 backdate + 18), **no further request and no second launch** in the next 24 s. Mutations R03 (publish over a full board), R04 (return the empty slate), R05 (no backdate), R06 (`pc_board` publishes empty), R08 (`pc_board` returns None) all caught by the F2 check. `_task_boards` against `pc_board`: simulated the loop body with `pc_board`'s shapes -- a `{"skipped"}` dict and a full index are both "fresh" and popped on the first pass, an old-style `(None, None, True)` would have been re-invoked until the deadline; covers F7. Nit left: a skipped board prints "board matchups-nba: fresh" in the PC log. Also: `pc_board` runs `build_isolated` inline (up to 240 s each), so two slow matchups builds can eat most of the 300 s boards deadline before `futures` / `dfs-*` / `presets` are first invoked; those still start, they are just not waited for. |
| F3 | **FIXED** | Failure remembered per worker for `_RETRY_S` (120 s); `board()` serves the disk copy or the placeholder with `building` False; `building` is now `bool(_MEM[("_bs_flight", name)])`. Live (NFL tomorrow, fake failing child): one launch; the retry served the placeholder whose note reads "This board's build failed this time (ledgered MX-child); it is tried again within two minutes of the next visit.", `building` False, no further request or launch. Tab: with the route stubbed to answer "building" forever, requests at 0, 20, ..., 160 s (eight checks), then "Still building after eight checks (about three minutes) - reload the tab to look again." and **no request in the next 25 s**; the route's sentence no longer promises half a minute. Mutations R09 (no fail memory) and R10 (board ignores it) caught. R20 (`building` always False) SURVIVED: the real in-flight flag is documented (F16) but not pinned -- a nit, since the flag only decides whether the tab keeps checking. |
| F17 | **FIXED** | NBA reads the tight record for every stat (`set(nba_rec) == {"*"}`, the by-stat all-books figures no longer decide a chip); each record carries `basis`; the chip's tooltip prints season, n, pooled/own, basis and both log losses from `market_record` (live: "Record (2026, 1510 pooled markets; sampled Kalshi prop rungs, last pre-kickoff hourly price, frozen pre-kickoff lines): Kalshi's log loss 0.432 vs the model's 0.456 (lower is better)."). Covers F21 (the payload field is now read) and F22 (tooltip wording "the engine's record against Kalshi's own pre-game prices"). |
| F18 | **FIXED** | Live: the EV cell of a `market_better` line is a muted span "YES +4.2¢ (market better on record)", no `<b>`; the EV sort puts those lines with the thin books (desc order: thin-book NO +5.5¢, then the market-better lines by EV, then "-"). Mutation R13 caught (two checks). |

### The smaller ones

| id | verdict | evidence |
|---|---|---|
| F4 | FIXED | `back` excludes the seasons the daily rule reads; guard with month < 10 and both stores missing (`[([2024], False), ([2025, 2026], True)]`); mutation R11 caught. |
| F5 | FIXED | Players without an all-digit game id are dropped before the pieces and index are cut, ledgered `MX-nogame` (live ledger: "nfl 2026-10-23: 1 players without a game id dropped"); no `_gNone.pkl` on disk; "No Game Id" absent from the scan and the picker. R12 caught. |
| F6 | FIXED | Live: player page open, date changed to a slate he is not on -> "He is not on this slate - pick a player from this date's list." (previously "Loading his matchup detail…" forever). R18 caught (string). |
| F8 | FIXED | Regex `\bon[a-z]+\s*=`; mutation R21 (`onmouseover=`) caught. |
| F9 | FIXED | Live, real board, tick on: "No lines match. 9772 lines are hidden by "only lines Kalshi lists" - Kalshi lists none of them on this board." |
| F10 | FIXED | NFL error payload -> the index carries the reason once (the engine's sentence), verified; not pinned by a guard (R19 survived) -- nit. |
| F11 | FIXED | `isascii() and isdigit() and len <= 20`: fullwidth `７００１` -> 400, `²` -> 400, 21 digits -> 400, 20 digits -> "not published". The length cap itself is not pinned (R17 survived) -- nit. |
| F12 | FIXED | Live: assessment `constructor` / `__proto__` -> `color:inherit`. |
| F13 | FIXED | After `run()`: `matchups._MEM` empty, `boardshare._DIR` restored, no `mx_guard_*` directory left. |
| F14 | FIXED | `_in_season` gate in `board()` (one-note placeholder, nothing built) and `pc_board` (`{"skipped": "out of season"}`); guard; R16 caught. |
| F15 | FIXED | Live, with the response delayed 1.5 s: status "" and body "Loading the NFL matchups…" during the load. |
| F16 | documented | `board()`'s docstring says which worker says `building`. |
| F19 / F20 | FIXED | `publish` applying the record is pinned on the PUBLISHED index (R14 caught); an unreadable record adds the slate note "... could not be read: +EV labels below carry no market check (ledgered MX-record)" (R15 caught). |

### New, from the re-check (none blocks)

| id | sev | what | fix |
|---|---|---|---|
| RC1 | nit | A backdated empty slate or placeholder reads "Built <time> (13 min ago)" seconds after it was built: the backdate that keeps it from out-freshening a full board also sets the age the tab prints. | Carry `built_ts` (the placeholder has it) and print the age from it when `placeholder` is set, or print "built just now; retried in two minutes". |
| RC2 | nit | `pc_worker._task_boards` prints "board matchups-nba: fresh" for a `{"skipped": ...}` answer. | Print the marker's `why`. |
| RC3 | nit | The real in-flight `building` flag (F16) and the F10 dedupe are unpinned (mutations R20, R19 survived). | One assertion each if wanted; both are cosmetic. |

### Verdict

**AGREE TO SHIP.** F1, F2, F3, F17 and F18 are fixed as claimed and re-verified live and by mutation; every smaller
finding is fixed or deliberately left (F23). The remaining items (RC1-RC3, the soft node half of the F1 guard, the
PC boards-deadline note under F2) are nits that change nothing the owner would act on. The agreement log should
record: the matchups key contract (`matchups_<sport>_<MT date>`, pieces `_g<digits>`), that an empty slate is
never published fresh over a full one (server backdates, PC skips), the 120 s failure memory, the one-population
rule for the NBA record, and that a `market_better` EV is muted and ranked with the thin books.

---

## FINAL SWEEP (2026-10-23 16:05-16:45 UTC; 10:05-10:45 MDT) -- the pre-commit edits

Scope, as asked: the RC1-RC3 and deadline edits, the `_mxBand` band display, the CLAUDE.md "Matchups tab" bullet
against code and reports, agreement-log rows 1-7 and 9, the SCHEMA 6 comment, the `_entries` docstring, and one
read-only run of the whole suite on the real tree. Files diffed against my re-check copies: `pc_worker.py`,
`static/matchups.js`, `tests/guards_matchups.py`, `nfl_matchup.py` (the null bands, read only as the band display's
input), plus `git diff` for `CLAUDE.md` and `artifacts.py`; `matchups.py`, `app.py`, `nba_matchup.py` are unchanged
since the re-check (hashes). Mutations: 7 on the scratch copy (`$S/mutate4.py`), files restored and asserted
identical. No server was started; nothing of mine is running. Only this file was written.

**The full suite on the real tree**: `python3 tests/combo_audit_guards.py` -> `RESULT: 3089 passed, 0 failed`,
`EXIT 0`; 562 checks after the MX banner; no FAIL line. A sha256 of every file in the tree (outside `.git`, no
`.pyc`) before and after the run differs in exactly one entry, `data/errlog.db` -- the error ledger the guards
write to (`.gitignore:30` ignores `data/`), so the git-visible tree is byte-identical; `git status` is unchanged.

### 1. The nits (RC1-RC3, the deadline)

| item | verdict | evidence |
|---|---|---|
| RC1 | FIXED | `static/matchups.js`: `_mxOk(d.age_s) && !d.placeholder` -- no age printed for a placeholder, with the reason in a comment. Mutation Q05 (condition reverted) caught by the F3/F6/F9/F15 string check. |
| RC2 | FIXED | `pc_worker._task_boards`: a `{"skipped": why}` answer prints `board <label>: skipped - <why>` and is popped. Read; not guarded (Q06 survived) -- a log line, fine. |
| RC3 / F10 | FIXED and PINNED | New check: `_MEM[("_bs_flight", name)]` set -> `board()[2]` True, cleared -> False; an engine whose notes already carry its error gets it once. Mutations Q01 (`building` always False) and Q02 (dedupe off) each turn it red; 30/30 on the real tree. |
| 300 s deadline | FIXED | The two matchups builders are now the LAST `_add` calls, after `presets`, with the reason ("build inline (~25 s each): every board above has been started once before they take the loop's 300 s"). Order read, not guarded (Q07 survived) -- acceptable. |

### 2. The band display

`_mxBand(l)` prints `p_lo-p_hi` when both are finite numbers and "no band" otherwise; it replaces all three
`${_mxPct(l.p_lo)}-${_mxPct(l.p_hi)}` spans (the guard pins `${_mxBand(` x3 and that the raw pair occurs only inside
`_mxBand`; mutation Q03, one span reverted, caught). Under node: `{null, null}` -> "no band", `{"x", 0.8}` -> "no
band", `{0.75, 0.83}` -> "75%-83%". The NFL engine now emits `(p, None, None)` where no fitted term carries
uncertainty (`nfl_matchup.py` diff: `return {k: (lad[k], None, None) ...}`, `width=None if lo is None ...`), so the
NFL board reads "no band" there instead of "79%-79%". Two nits: (a) `_mxBand({0.79, 0.79})` still prints "79%-79%"
-- the comment's "never a zero-width" holds only because the producer now sends None; `_mxOk(..) && l.p_hi > l.p_lo`
would make it true of the function too; (b) the text "no band" is not pinned (Q04, `"-"` in its place, survived).

### 3. CLAUDE.md -- the "Matchups tab" bullet, sentence by sentence

| sentence | verdict | checked against |
|---|---|---|
| labels: high probability, +EV, robust +EV, strong fit, low evidence | AGREE | `prop_value.LABELS` (:664) |
| `thin_book` / `stale_quote` stand in for a +EV label; quote over 600 s old | AGREE | `QUALIFIERS` (:671), `LABEL_DEFAULTS["max_quote_age_s"] = 600.0` (600 fresh, 601 stale) |
| robust refused on a zero-width band | AGREE | §D3 |
| `kalshi_nba_props` reads KXNBAPTS/REB/AST/3PT/PRA and their fees | AGREE | the SERIES block (:9-29); PRA listed among the standard series |
| event ticker AWAY then HOME on the Eastern date | AGREE | `kalshi_nba_props.py:85` ("AWAY THEN HOME -- the opposite" of soccer's), the `event_key` parse (:201-226, `"date" (ET, ISO)`) |
| a player who never plays settles at a fair price, so every probability is conditional on him entering | AGREE | the SETTLEMENT docstring (:25-69): inactive, or active but never takes the court -> scalar fair price (18 of ~700 markets, 2.6%); once he enters, the market settles on his recorded stat -- so "entering", not "finishing", is the right word |
| as-of profiles from strictly earlier MT dates | AGREE | report lines 18 / 114 ("strictly earlier dates"); `game_day` is the MT date (UTC-6) |
| validated on 2024-25, tested once on 2025-26 by a hashed rule | AGREE | report §"The rule, written before..." / §"Validation, 2024-25" / §"Test, 2025-26 (read once...)" |
| "Overall defense allowed and teammates out ship. Style x concession and comparable opponents measured nothing for points, assists and threes; they show as weight 0." | AGREE, INCOMPLETE | coefs.json `shipped`: pts [1,2,6], ast [1,2,3,6], tpm [1,2,6], reb [1-6]; report :515-528. True as written, but rung 3 (position allowances) ships for rebounds and assists and rungs 4-5 ship for rebounds; a reader of this sentence would think only rungs 2 and 6 ship. Add: "for rebounds they ship (position allowances for rebounds and assists too)". |
| teammates-out gain an UPPER BOUND; reads the box in research, ESPN "Out" in production | AGREE | report :617-618, :650-681; `nba_absent_reasons.py` re-run by me (§A) |
| tight books beat it (0.542 vs 0.556), betting its edges lost 14% | AGREE | `market_test.tight`: 0.54238 vs 0.55616, bets -14.3% (all books -13.8%) |
| `dk_fp` beats DK's average (MAE 8.16 -> 7.81), feeds no builder | AGREE | `shipped.dk_fp_test`: avg_mae 8.1643, shipped_mae 7.814; report :545-547 (the average is RECONSTRUCTED from the box, as the report says) |
| the replay never takes in the owner's MT today; the job runs every six hours | AGREE | `_entries`, §D1 |
| nflverse pbp, participation, FTN (credit "FTN Data via nflverse", CC BY-SA); participation after the postseason, so 2026's coverage is prior-season only | AGREE | the data-sources review A.1 #2, #7; `nfl_matchup_data` PARTICIPATION_SEASONS. (The CC BY-SA is FTN's licence; nflverse's own pbp is CC BY 4.0 -- the sentence attaches it to FTN, correctly.) |
| nothing about the defense beats Sleeper's line out of sample: coverage, shells, blitz, box, position allowances, similar defenses | AGREE | report §5 ("What a defense allows ... did not add to Sleeper out of sample. The one exception the rule found on 2025 (completions vs passing yards allowed to QBs) reversed on 2026's frozen lines"); coefs `validation_accepted` pcmp ["D3"], `shipped` pcmp [] |
| the man/zone target split is real but adds nothing past Sleeper | AGREE | agreement row 5 (r 0.39-0.55, a reliability statement) + the model report's D5 result; report :137 notes the shell split borrows its k |
| what ships: only a game-script term on passing TDs (weak evidence); the DFS hook does not ship | AGREE | `shipped` ptd ["V"], every other list empty; `dfs_hook.ships` False; report :33-41 ("weak evidence: under one SE on the frozen rows") |
| rushing yards run high for RBs (10+ 0.86 vs 0.67 hit); the line-dependent fix failed its own 2025 gate, so the slate says so | AGREE | report :540 (0.671 / 0.857), §9.1 "The 2025 gate: FAILED" (run 15:10:13 UTC after registration at 15:10:09); the slate note at `nfl_matchup.py:1718-1724` |
| built in a niced child under HEAVY_BUILD, today or tomorrow MT only; index + one piece per game; one NBA night 13-19 MB whole | AGREE | §A; 13.2 MB compact JSON, 18.8 MB as the file on disk |
| an empty or failed build never out-freshens a full board; backdated, retried after 120 s; `pc_board` never publishes one | AGREE | §RE-CHECK F2/F3 |
| "market better on record" mutes the EV of any +EV line in a stat where Kalshi's pre-game price beat the engine (NBA tight books; NFL 2026 frozen lines) | AGREE | §D4, §RE-CHECK F17/F18 |
| deep jobs `nba_matchup` (6 h) and `nfl_matchup` (daily, "backfills the two prior seasons once") | AGREE, one phrase | after F4 the backfill covers only a prior season the daily rule does not already read: before October that is one season (cur-2), from October both. "backfills the prior seasons the daily rule does not read, once" is exact. |
| guards: the five files, run from the MX section | AGREE | `tests/guards_*.py` present; 562 checks after the banner in the full run |

### 4. The agreement log, rows 1-7 and 9 (row 8 skipped as asked)

| row | verdict text vs the review | finding |
|---|---|---|
| 1 | matches: first pass "AGREE AFTER FIXES" (F1, F2 blocking), re-check "AGREE TO SHIP", "61 applied ... 59 caught, 1 equivalent mutant, 1 control survived" (`prop_math.md` R.3, R.5) | none |
| 2 | matches: A "AGREE AFTER FIXES" (A1, A2) -> re-check "AGREE TO SHIP"; the doubled "use" nit | none |
| 3 | matches: B "AGREE AFTER FIXES", blocker B1, B2-B6 majors, B7-B13, B14 -> re-check "AGREE TO SHIP", M1-M5, B14 open to the producer | none |
| 4 | verdicts match ("AGREE AFTER FIXES" with F1 blocker; the orchestrator's "AGREE TO SHIP" disputed at :393; re-check "AGREE TO SHIP"; confirmation "AGREE TO SHIP, nothing open", 149/149). **The figure "28/33 mutations caught" is not in the review**: its first pass applied 28 and caught 22 (§8, :363-365), and its confirmation says each new guard kills its revert (149/149). | correct the figure |
| 5 | matches: "AGREE as a reliability statement, with two caveats" (:271); the row says "caveat" -- the review has two (reliability is not predictive value; the model's held-out test decides) | optional: "two caveats" |
| 6 | matches: "AGREE on substance, CANNOT VERIFY exact counts" (§4.7, :295); every post row 2026-09-15 04:00:13 UTC (a Tuesday); QB pass_yd differs 28 of 32 | none |
| 7 | matches: "AGREE AFTER FIXES", no blocker (:13, :248); "15 of 38 real mutations pass" (:233); the coach's-decision leak +0.000097 +/- 0.000041, 5% of the points gain (:161-168); re-check "AGREE AFTER ONE FIX (R1) ... After R1, AGREE TO SHIP" (:342-346); confirmation "AGREE TO SHIP" (:396). "68 guards, 49/49 real mutants caught" is attributed to the builder; the Fable re-check's own figures were 47 real + 1 control with two small real mutants still passing (:272-277), later caught per the confirmation | none (attribution is right) |
| 9 | verdicts match mine ("AGREE AFTER FIXES" F1/F2/F3/F17/F18 required; re-check "AGREE TO SHIP"; 30 guards). **"24 mutations caught" should read "24 mutations run, every revert of a required fix caught; the five survivors expected (two control-like, three unguarded nits)"** | correct the phrase |
| order | row 9 is printed before row 8 | cosmetic |

### 5. `artifacts.py` SCHEMA 6 comment

The added clause names `nfl_matchup_defhist_<season>_wNN.pkl`: `nfl_matchup.def_history` builds the key as
`DEFHIST_KEY.format(season=s) + f"_w{w:02d}"` (`nfl_matchup.py:1371`), loads it first and computes-then-saves only
when the deep store has none and the committed table lacks the week (`if not got: ... deep_cache.save(key, got)`;
weeks already in `out` are skipped) -- "written once ... never rewritten" holds. "~2.7 KB": CANNOT VERIFY here (no
store); 32 teams x a short vector is the right order. The rest of the comment is unchanged from §A.

### 6. `nba_matchup._entries` docstring

The reworded sentence reads: "a job run can land after the early tips are final (ET midnight is 22:00 MT) ... The
job reruns every six hours (app.py registers it with cadence "age") so a run after MT midnight takes in the night
before." -- consistent with §D1 and with the code below it (`if day >= today: continue`). `nba_matchup.py` hashes
identically to my re-check copy (732224c0...), so nothing moved since. Against my pre-R1 snapshot of 09:48 UTC the
only differences are `mt_today`, the `_entries(season, today)` cut and `rebuild(..., today)` -- exactly the R1 the
NBA review describes -- plus this docstring. The intermediate a070a47d state itself is not in any scratch copy, so
"moved only by the docstring" is CANNOT VERIFY byte-for-byte, but no other code line differs from the pre-R1 file
except R1's.

### Verdict

**AGREE TO SHIP.** Suite green on the real tree (3089/0, exit 0, tree unchanged but for the ignored ledger); RC1-RC3
and the deadline move are in, two of them pinned; the band display is right for the producer's new nulls; the
CLAUDE.md bullet is accurate in every number and file name. Before the commit I would make three wording edits,
none of which changes code: (1) CLAUDE.md -- say that position allowances ship for rebounds and assists and rungs
4-5 for rebounds; (2) CLAUDE.md -- "backfills the prior seasons the daily rule does not read, once"; (3) the
agreement log -- row 4's "28/33" to the review's own 22/28 + 149/149, row 9's "24 mutations caught" to the phrase
above. Nits, no action needed: `_mxBand` on an equal finite pair, the unpinned "no band" text, the unpinned PC log
line and builder order, row order 9/8.
