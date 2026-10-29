# The web server's memory — an independent investigation (Fable, 2026-10-29)

Scope: the Render Standard instance (2,048 MB cgroup, one CPU, gunicorn 3 workers x 8 threads,
`--max-requests 600`). Everything cited is from HEAD `bcc39f4` (what is deployed), read from a scratch
`git archive` because the working tree was being edited by the crypto removal while this ran.
Nothing tracked was edited; the live app was not touched. Measurement scripts and the extracted
evidence are in the session scratchpad (`.../scratchpad/mem/`: `mem_notes.json` 63 MEM-high notes,
`mem_history.json` 317 six-hourly snapshots, `jobs_all.json` 22,152 job rows rebuilt from every
`jobs-latest.json` on `sim-history`, `measure/` the scripts and synthetic databases).

## (a) Verdict

The 1.0-1.77 GB worker is not a leak in the sense of memory that is never freed, and it is not a
build: it is **one request, `GET /api/recorder/backtest`, fired by `static/app.js` `init()` on every
page load (line 8124, `loadStrategy()`), which runs `recorder.backtest()` and materialises the whole
crypto `quote_samples` table as Python dicts** (`recorder.py:171-172`, `SELECT * FROM quote_samples
ORDER BY ts ASC`). Measured here on the recorder's exact schema: **1.52 KB of RSS per row at the peak
(+154 / +458 / +1,522 MB at 100k / 300k / 1M rows, 0.8 / 4.9 / 18.3 s), all returned after the call**,
which is why the six-hourly snapshots (median fattest worker ~230 MB, one of 317 above 700 MB) never
see it while the 20-second watchdog does. Nothing prunes the table, so the fattest worker's weekly
peak has grown linearly: **349 MB + 18.1 MB/day since 2026-08-12** (least squares on the six weekly
maxima W39-W44, residual sd 26 MB; the all-notes fit is 296 + 16.1/day), i.e. ~12-13 rows per 90-second
recorder tick, and it reached 1,766 MB on Oct 28 against a 2,048 MB limit. The same page load also
runs the crypto *calibration* in whichever worker takes `/api/calibration` or the Best Bets landing
scan (`calibrate._params("crypto")` -> `recorder.calibration_pairs()`, +418 MB at 1M rows, then a
5-fold cross-validated fit I measured at **44 s of one-core CPU for 10,000 pairs, 134 s for 30,000**,
re-paid per worker every 6 h). With three workers taking the init's parallel fetches, two or three
surges overlap and the cgroup pins at 2,047/2,048 MB: the owner-pid churn in the notes (Oct 25,
16:32 -> 16:39 -> 16:47 UTC, owners 40 -> 9 -> 8, pids 7/8/9 being a fresh container) is the instance
being killed and restarted. The coordinator's finding is **confirmed** (independently measured within
10%: their 1.39 KB/row and 342 MB@1M vs my 1.52 KB/row and 418 MB@1M; the fit slopes 17.6 vs 18.1 MB/day).
Removing crypto removes the blocker. Separately, the server does a real amount of CPU work in web
workers that the PC could own — NFL board / slate sims / presets / combo builds in worker threads,
eight nightly deep jobs run inline in the owner's scheduler thread without a PC-first gate, soccer
never built by the PC at all — but those are ~100-300 MB transients and one-core CPU, not the 1.7 GB.

## (b) Findings, ranked

### 1. BLOCKER — `/api/recorder/backtest` on every page load loads the whole crypto quote table into Python

- **Evidence.** `static/app.js:8124` (`loadStrategy();` unconditionally in `init()`; also every 60 s
  while the crypto tab is visible, 8125) -> `static/app.js:3035-3036` fetches `/api/recorder/status`
  and `/api/recorder/backtest` -> `app.py:3098-3108` -> `recorder.backtest()` (`recorder.py:160-235`):
  `resolved_markets` as dicts, then `samples = [dict(s) for s in c.execute("SELECT * FROM quote_samples
  ORDER BY ts ASC").fetchall()]` — 17 columns a row, the `fetchall()` list of `sqlite3.Row` and the dict
  list both alive. No `WHERE`, no `LIMIT`, and `recorder.py` has no retention anywhere (`init_db`,
  `sample_once`, `resolve_due`: only INSERTs). The recorder appends every two-sided open market of six
  `(coin, timeframe)` pairs every 90 s (`WATCH`, `SAMPLE_INTERVAL`, `recorder.py:25-27, 58-90`) to the
  persistent `/data/markets.db` (Dockerfile `KALSHI_DB=/data/markets.db`).
- **Data.** 63 MEM-high notes; since Sep 25 the fattest process is always a gunicorn worker, rising
  1,170 (Sep 25) -> 1,340 (Oct 4) -> 1,424 (Oct 11) -> 1,533 (Oct 18) -> 1,730 (Oct 25) -> 1,766 MB
  (Oct 28). The watchdog runs only in the background owner (`app.py:1150-1153`, after
  `if not _BG_OWNER: return`), so its pid in a note is the owner's: the fat pid is the owner in 17 of
  the 50 notes since Sep 25 and a request-serving sibling in 33 — the mechanism lives in whichever
  worker takes the request, as a page load would. The timed-job log shows no heavy job coinciding
  (the `deep:nfl` / `season:nba` / `season:nhl` runs on Oct 20 05:08-05:15 UTC produced no note; the
  `nfl-board` builds that do coincide ran in a *different* pid from the fat one). The surges fall when
  the owner opens the app: Sunday 09:00-10:50 MDT (15:00-16:50 UTC) before the 11:00 MDT lock,
  Thursday/Monday 15:00-17:30 MDT, Saturday evening, and six times on deploy-heavy Oct 16
  (13:25-16:54 MDT, pids 9, 8, 9, 8, 7, 9 in turn — the "new version, tap to refresh" reloads).
- **Measured (scratchpad `measure/mk_quotes.py` + `run_clean.py`: a fresh process per call, the
  recorder's own `init_db()` schema, synthetic rows, `ru_maxrss` peak and `/proc/self/status` RSS,
  tracemalloc OFF — an earlier run with tracemalloc on read +1,122 MB at 300k and "543 MB retained";
  that was tracemalloc's own bookkeeping and is discarded):**

  | rows | `backtest()` peak | time | RSS after the call | `calibration_pairs()` peak | time |
  |---|---|---|---|---|---|
  | 100k | +154 MB | 0.8 s | 32 MB (from 20) | +42 MB | 0.2 s |
  | 300k | +458 MB | 4.9 s | 38 MB | +127 MB | 0.7 s |
  | 1M | +1,522 MB | 18.3 s | 52 MB | +418 MB | 2.7 s |

  1.52 KB/row and 0.42 KB/row, linear; memory returned (so the fat state is transient, matching the
  snapshots). Disk: 218 B/row with both indexes -> ~2.7 MB/day -> roughly 200 MB of the 1 GB data disk
  by now (estimate from the fitted row rate; the real count is not exported anywhere, see (d)).
- **Mechanism check against the growth.** Weekly max of the fattest worker since W39 = 349 MB +
  18.1 MB/day since 2026-08-12 (residual sd 26 MB, n=6). At 1.52 KB/row that is ~12,200 rows/day =
  12.7 rows per 90-s tick (6 series x ~2 two-sided strikes), and projects 1,745 MB for Oct 28 against
  the observed 1,766. The intercept is a normal worker (~200-350 MB) plus a young table.
- **Fix.** The owner's decision removes crypto: delete `recorder.py` and its loop start
  (`app.py:1133-1134` `recorder.start_background()`), the two routes (`app.py:3092-3108`), the
  `loadStrategy` call and interval in `init()`, and DROP `quote_samples` / `resolved_markets` (and the
  crypto `markets` table) followed by `VACUUM` to give the disk back. If any of it were kept: never
  `SELECT *` the table into Python (aggregate in SQL, or stream the cursor), and prune rows older than
  the resolution horizon. **Failsafe risk: none** — nothing sports-side reads these tables (see the
  removal checklist below for what IS shared).

### 2. MAJOR — the crypto calibration fit: a whole-table read plus a minute of one-core CPU, per worker, every 6 h, triggered by page loads

- **Evidence.** `calibrate.py:278-280` `_crypto_pairs` -> `recorder.calibration_pairs()`
  (`recorder.py:93-113`, `SELECT ticker, coin, timeframe, fair_yes FROM quote_samples ORDER BY ts`
  — every row, 0.42 KB/row measured above); `calibrate.py:357-375` `_params()` caches per model per
  WORKER for `_TTL = 6 * 3600` and otherwise runs `_fit(pairs, 400)` (5-fold CV, `_cv_loss`
  `calibrate.py:169-266`). Triggers in web workers: `calibrate.report()` on `/api/calibration`
  (`app.py:2931`; fetched by `calNote()` `static/app.js:404` on the first card that renders a
  calibration note), `odds.kalshi_signal(..., calibrated=True)` -> `calibrate.crypto()`
  (`odds.py:628-647`) from the crypto scanner `/api/kalshi/scan` and from `combine._crypto_legs()`
  (`combine.py:194+`) which the **Best Bets landing tab** pulls on every load (`static/app.js:8130-8132`
  `loadBestBets()` -> `/api/bestbets` -> `bestbets.scan` -> `pull("crypto", _crypto_rows)`
  `bestbets.py:374`, `app.py:5051`).
- **Measured** (`measure/fit_cost.py`, `calibrate._fit` on synthetic pairs, HEAD code): 8.4 s at
  2,000 pairs, **44.3 s at 10,000, 133.5 s at 30,000** (roughly linear in n; one core, GIL held in that
  worker for the duration). The resolved-market count in production is unknown (one pair per resolved
  ticker; my 1M-row synthetic table had 166k); even at the 10k scale this is the single largest CPU
  item any request can start on Render's one-core quota, and it recurs in each of the three workers.
- **Fix.** Removed with crypto (`_MODELS["crypto"]`, `_crypto_pairs`, `calibrate.crypto`, the
  `calibrated=True` path in `odds.kalshi_signal`). **Failsafe risk: none**; the sports models in
  `_MODELS` are untouched and `calibrate.report()` keeps working with the key gone.

### 3. MAJOR — board builds, slate sims, presets and combo builds run in gunicorn worker threads (not children), and the PC does not keep them fresh enough to stop it

- **Evidence.** `boardshare.nonblocking()` (`boardshare.py:118-156`) runs `build()` in a daemon
  thread of the worker that took the request — used by golf, LoL, NBA/WNBA, NHL, tennis, UFC, futures,
  soccer and racing DFS (`basket.py:621`, `hockey.py:525`, `golf.py:436`, `lol.py:511`,
  `tennis_prices.py:816`, `ufc_sim.py:260`, `soccer.py:1239`, `racing_dfs.py:340`; `mfutures.py:647`,
  `futures.py:275` hand-roll the same thread). `nfl_game_sim.board()` (`nfl_game_sim.py:1056-1096`,
  `jobs.timed("nfl-board:w..")`) and `nfl_dfs_sim.board()` (`:1158-1188`) and `cfb_board.board()`
  (`:567`) build the drive-engine slates (16 games x 2,400 sims + Sleeper + Kalshi) in a worker thread;
  `nfl_game_sim._slate_sims()` (`:1842-1945`, 16 games x 4,000 sims) runs inline in whichever worker
  a Build (`api_nfl_parlay` -> `_run_job` thread, `app.py:4369`) or a forced presets kick
  (`api_nfl_presets` `app.py:4624-4633` -> `nfl_presets.tick(force=True)` -> `_slate_sims`,
  `nfl_presets.py:455-458`) lands on; the presets DP itself (`jobs.timed("nfl-presets")`, 6-23 s)
  ran in nine different pids on Oct 28 alone (10885, 10914, 11320, 11353, 11373, 7, 9, 97, 51).
  The jobs timeline holds 17 server-side `nfl-board` builds (38-123 s each) and 7 `cfb-board` builds in
  October while the PC was "on" in 91-100 % of the weekly snapshots — because the PC's cycle is serial
  (`pc_worker._cycle`: MLB sims, boards with a 300 s wait loop, showdown tourney, classic tourney up
  to 2,578 s, deep nightly, standings) and the boards' TTLs are 1,800 s (`nfl_game_sim._BOARD_TTL`,
  `_SIMS_TTL`, `nfl_dfs_sim._BOARD_TTL`, `cfb_board`), so during any tourney build every board on the
  server goes stale and a worker rebuilds it.
- **Cost.** Not measured here (the drive engine needs live Sleeper/ESPN; no offline fixture): the
  repo's own measurements are "~140 MB fatter per slate sim, fragmented arenas no collection returns"
  (Dockerfile) and a "175 MB plateau" (render.yaml); `nfl-board` 38-123 s and `nfl-presets` 6-23 s
  of one-core time are measured in the jobs log. Memory-wise these are the ~200-400 MB "second
  worker" in the MEM-high notes, not the 1.7 GB.
- **Fix (keeps the failsafe).** (i) Make the PC's board task independent of its tourney build — a
  second loop or process on the PC for `_task_boards` so boards never age past their TTL while a
  45-minute classic board builds (the PC has the cores; the worker's between-build update check can
  stay). (ii) Server side, when `boardshare.pc_status()` is "on", serve the stale board and stamp a
  request instead of rebuilding (the `deep_cache.request_pc` marker generalised to boards), falling
  back to its own build only after a wait (the `_DEEP_PC_WAIT_S` pattern); with the PC off nothing
  changes. (iii) Any server-side fallback build that simulates (NFL/CFB boards, `_slate_sims`,
  soccer's board, the NBA/NHL/golf/LoL/tennis/UFC sims at n=3,000-20,000) should run in a niced child
  like `matchups.build_isolated` / `baseball._game_sim_isolated`, under `HEAVY_BUILD`, so the
  allocation dies with the child instead of fragmenting a long-lived worker.

### 4. MAJOR — eight nightly deep jobs run inline in the background owner's thread with no PC-first gate and no child

- **Evidence.** `app.py:1551-1571` (`run_f1`, `run_nascar`, `run_nfl_season` = `nfl_season.run_season(
  n=4000)`, `run_cfb` = `cfb.run_season(n=4000)`), `:1598-1623` (`run_nhl_deep`, `run_nba_deep`),
  `:1682-1687` (`run_model_trust`), `:1744` (`run_pro("nfl")` = `pro_sim.project("nfl", 4000)`): none
  calls `_deep_pc_first`, none takes `HEAVY_BUILD`, none is `jobs.timed`, and `deep_cache.run_job`
  runs `job["run"]()` in a thread of the owner worker (`deep_cache.py:279-303`). The scheduler fires
  them when the file predates ET midnight (`is_stale_daily`, 30-minute passes); the PC runs the same
  jobs only when the server's copy is >19 h old (`pc_worker._due`), so the server's 00:00-00:30 ET
  (22:00-22:30 MT) pass normally wins and the PC's later run is redundant. The six PC-first jobs
  (`mlb_deep`, `nfl_deep`, `nba_matchup`, `nfl_matchup`, `nba_season`, `nhl_season`) show the intended
  shape.
- **Cost.** Unmeasured here (not timed in the jobs log — that is itself the gap); CFB is 4,000
  seasons of ~800 games and NFL 4,000 x 272 in pure Python on the one core, inside the process that
  also serves requests and runs every recorder loop.
- **Fix.** Register all of them through the same `_deep_pc_first(key)` + `DEFERRED` + 45-minute
  fallback, run the fallback in a child (`pro_season_job.run_isolated` is the template), and wrap each
  in `jobs.timed` so the next ledger can attribute a surge. With the PC off the server still runs
  them — in a child, after the wait.

### 5. MAJOR (CPU/memory churn) — `deep_cache.is_stale_daily()` unpickles whole payloads to read a timestamp

- **Evidence.** `deep_cache.py:102-105` calls `load(key)` (full unpickle under `_lock`) and only uses
  `generated_at`; `status()` (`:318-330`) calls it, and `/api/sim/status` (`app.py:4825-4829`) polls six
  keys — `mlb_deep` ("several MB", per the `age()` comment that fixed exactly this once), `nfl_deep`
  (the 4,000-season aggregate with every unit profile), `nba_season`/`nhl_season` (10,000-season
  payloads) — on the Featured tab (`static/app.js:3539, 6429`); the scheduler's `_job_stale` does the
  same for every daily job each pass. Sizes could not be measured locally (no deep store here).
- **Fix.** Read `os.stat(_path(key)).st_mtime` as `age()` does (`save()` renames the finished file,
  so mtime is generation time to within a second). One line; no failsafe impact.

### 6. MINOR — per-worker copies of payloads, and date/week-keyed caches that are never swept

- `_register_deep_sims()` -> `_deep_refresh()` unpickles `mlb_deep.pkl` into EVERY worker at boot and
  whenever the file moves (`app.py:1471-1484, 1749-1766`) although MLB is paused; `_nfl_deep["agg"]`
  (`:3679-3699`), `_pro_season[league]["payload"]` (`:3574-3590`) and every `nonblocking` `mem` dict
  hold one copy per worker (x3). Caches keyed by date or week with no sweep: `hockey._cache`
  (`nhl_{date}`), `basket._cache` (`bk_{lg}_{date}`), `nfl_game_sim._cache` ((season, week, pre)),
  `nfl_dfs_sim._cache` and `engine_reports` ((season, week, want, seed)), `cfb_board._cache`,
  `kalshi._series_cache`, `soccer._slug_retry_at`, `nfl_rush._FFT`, `pro_season._CUM`,
  `combo_engine._tau_cache` (grep: no `pop`/`clear`/`ttlcache` on any of them). Growth is one board
  per day or week per worker — bounded by `--max-requests` recycling in practice; `matchups._MEM` is
  the pruned model (`_MEM_KEEP = 4`). Fix: route the per-date ones through `ttlcache.cached` or
  prune to the last few keys; load `mlb_deep` lazily on first use.

### 7. MINOR — whole-table reads on request paths that grow with the season

- `dfslog.report()` `SELECT * FROM dfs_builds ORDER BY ts` (`dfslog.py:966`, `/api/dfs/lookback` on
  the DFS tab, `app.py:2663`): every logged lineup with its `players` JSON plus the seeded backtest
  rows (`seeds/dfs_backtest_seed.json`, 510 KB) and the tournament boards' top lineups logged at lock.
  Thousands of rows x a few KB — tens of MB at most today, but unbounded by design; aggregate in SQL
  or page it. `store.list_markets` `SELECT * FROM markets` (`store.py:1323`, crypto tracked markets —
  removed with crypto) and `store.list_bets` (`:1261`, the owner's own ledger, small).
- `/api/dfs/tourney/list` unpickles up to twelve 1-5 MB tournament boards per tab load
  (`app.py:2618-2623`, already capped and commented), and `_tourney_pending()` unpickles the board of
  every live request on every `/api/dfs/tourney` poll (`app.py:2332`); `_KEEP_LONG` (8 days) grows the
  disk, not the per-request cost. Cheap fix: cache `built_ts` per board name by mtime.

### 8. MINOR — the recorder loop's own 90-second work (removed with crypto)

`resolve_due()` (`recorder.py:127-148`) runs `SELECT DISTINCT ... WHERE close_time < ? AND ticker NOT IN
(SELECT ticker FROM resolved_markets)` over the whole table every 90 s — no index on `close_time`, so
a full scan of ~1M rows in the owner every tick; `sample_once()` makes six Kalshi series calls plus
six spot and six candle fetches a tick (the `KAL-candles` 429s in the ledger, 12 in the last week, are
this loop and the scanner). Unmeasured; moot after removal.

### 9. MINOR — soccer is never built by the PC

`pc_worker._task_boards` (`pc_worker.py:276-331`) lists NFL, CFB, golf, tennis, UFC, LoL, NBA/WNBA,
NHL, futures, racing DFS, presets and matchups — not soccer. So the ~900-event board builds in a
worker thread every 600 s (`soccer.py:1237-1239`) and the 6-hourly Dixon-Coles refit child (~120 MB,
~200 ESPN year files, `soccer.py:1108-1129` under `HEAVY_BUILD`) always run on the server. Offload:
add `soccer.board()` to the PC's boards task and make `refit_main` PC-first.

### 10. MINOR — the matchups child builds on the server while the PC is on

`matchups.TTL = 900` (`matchups.py:46`); the PC builds the matchup pieces LAST in its boards task,
once per cycle, with the cycle often far longer than 15 minutes (finding 3), so a visit to the tab
spawns the ~250 MB niced child (`matchups._build` -> `build_isolated`) on the server. Isolated and
gated already, so a CPU cost rather than a memory risk; the fix is the same PC-first marker as (3).

### 11. Not the cause — checked and cleared

- `/api/art/upload` buffers at most 96 MB + a 64 MB gunzip in the worker (`app.py:47, 654-662`):
  <=160 MB transient, and the PC's boards are 1-5 MB.
- The DFS sheet builders are streaming: `nfl_dfs.contest_sim` / `_showdown_contest_sims` score one
  iteration's field at a time (`nfl_dfs.py:1346-1419, 1488-1564`), `simulate._contest_sim` likewise;
  the per-player `arr` lists (1,500 or 12,000 floats) are tens of MB. Numpy is not in the server image
  (`requirements.txt`; `requirements-pc.txt` says so on purpose), so `dfs_tourney` cannot run there.
- Matchups pieces are published per game precisely so a board read is never slate-sized (one NBA
  night is 13-19 MB whole).
- `ttlcache.cached` sweeps on insert; the caches `/api/diag/mem` lists (baseball, racing_form, savant,
  value, weather, kalshi_moves) use it or the `_cached` sweep.
- `errlog` dedups and prunes; `predlog` queries are per model and graded (`predlog.py:310-355`).

## The crypto removal — a checklist against this report

Crypto code that touches memory or CPU (all of it should go): `recorder.py` (loop, tables, `backtest`,
`calibration_pairs`, `status`), `app.py:1133-1134` (`recorder.start_background()`), the routes
`/api/recorder/status`, `/api/recorder/backtest`, `/api/kalshi/scan`, `/api/backtest`,
`/api/simulate/price`, `/api/quote`, `/api/coins`, `/api/kalshi/meta`, `/api/markets*`
(`app.py:1855-2049, 3072-3108`), `calibrate._MODELS["crypto"]` and `_crypto_pairs` / `crypto()`,
`odds.kalshi_signal`'s `calibrated=True` path, `combine._crypto_legs` (+ `CRYPTO_COINS`),
`bestbets._crypto_rows` and its `pull("crypto", ...)`, `mfutures.rows`' `crypto_futures.rows()` block
(`mfutures.py:437-439`), `prices.py` / `deribit.py` / `crypto_futures.py` / `backtest.py` / `odds.py`,
`static/app.js` `loadStrategy` + the crypto `init()` block (lines 8083-8125) and `calNote`'s crypto
model, `templates/index.html` `tab-crypto`. Then `DROP TABLE quote_samples, resolved_markets, markets`
and `VACUUM` on the live `/data/markets.db` (the sports ledgers share that file).

Looks crypto, is shared — do not remove: **`store.py`** (`_conn`, `mlb_picks`, `nfl_picks`,
`prop_log`, `slip_log`, `bets`, `dfs_*`, `_pick_stats` live in the same `markets.db`; only the
`markets` table and `list_markets`/`get_market`/`set_position`/`delete_market` are crypto);
**`kalshi.py`** (the shared client; only `series_ticker`/`get_open_markets`/the coin helpers are
crypto; `price_move`/`_move_cache` serve the sports combo makers); **`calibrate.py`** (all sports
models; `temps()` is win/prop); **`combine.py`** (the Combine tab's tennis/UFC legs stay);
**`simulate.py`** (`dfs_build`, `dfs_sim`, `_contest_sim` are the UFC/F1/NASCAR DFS builder; only
`simulate_price` and `odds.estimate_params` at `:1280` are crypto); **`value.py`** (the MLB
batter-prop value finder, imported by `mlb_dfs` and `mlb_recorder`, listed as the `value` cache in
`/api/diag/mem` — not crypto despite the name); **`mfutures.py` / `futures.py`** (sports futures;
drop only the crypto rows); **`bestbets.py`** (drop `_crypto_rows` only); `polymarket.py`
(`season_sim` uses it). Two traps: `init()` dereferences `$("coin")`, `$("scanCoin")`, `$("btCoin")`,
`$("tab-crypto")` without guards, so deleting the markup without editing `init()` throws before
`setupTabs()` and breaks every tab; and `api_simulate_dfs`'s UFC path still needs `simulate.py`.

## (c) The PC-offload table (what the server does while the PC is on)

| Work | Where it runs on the server | Cost | PC builds it? | Change |
|---|---|---|---|---|
| `recorder.backtest()` per page load | request thread, any worker | +1.52 KB/row RSS (1.5 GB today), 18 s | n/a (crypto) | remove (finding 1) |
| crypto calibration fit | request thread, any worker, per worker / 6 h | +0.42 KB/row, 44 s CPU @10k pairs | n/a | remove (2) |
| crypto quote recorder loop | owner thread, every 90 s | 18 API calls + full-table anti-join per tick | n/a | remove (8) |
| NFL slate board `nfl_game_sim.board` | worker daemon thread (`nfl-board:wN`) | 38-123 s one-core; ~140-175 MB plateau (repo's figures) | yes (`_task_boards`), but stale during tourney builds | PC-first marker + child fallback (3) |
| NFL DFS sim board `nfl_dfs_sim.board` | worker thread | similar | yes | same |
| `_slate_sims` 16 x 4,000 | worker thread on Build / forced presets kick | unmeasured; the heaviest pure-Python sim a click can start | yes (`_slate_sims(wk, pre, 4000)` warmed) | same; never simulate on a forced kick when the PC is on |
| NFL / UFC / MLB presets | owner every 10 min + any worker on kick | 6-23 s each (jobs log) | yes (`presets.pc_build` MLB only) | let the PC build `nfl_presets`/`ufc_presets` too |
| combo builds (NFL/CFB/UFC/baseball) | `_run_job` daemon thread in the request worker | minutes of DP + sims | no (interactive) | sims from the shared store only; DP in a child when PC is off |
| CFB board | worker thread | 30-86 s (7 in Oct) | yes | PC-first marker (3) |
| NBA/WNBA/NHL/golf/LoL/tennis/UFC/futures/racing DFS boards | `nonblocking` worker threads (n=3,000-20,000 sims) | unmeasured; seconds to a minute each | yes | PC-first marker; child fallback |
| soccer board + 6-hourly refit | worker thread + HEAVY child (~120 MB) | not on the PC's list | **no** | add to `_task_boards`; PC-first refit (9) |
| matchups NBA/NFL | niced child (~250 MB) under HEAVY_BUILD | 15-min TTL vs a long PC cycle | yes (last in the loop) | PC-first marker; longer TTL when PC on (10) |
| nightly f1, nascar, nfl_season, cfb, nfl, model_trust, nhl_deep, nba_deep | owner scheduler THREAD, inline | unmeasured, untimed | yes, but the server fires first | `_deep_pc_first` + child + `jobs.timed` (4) |
| `/api/sim/status` and scheduler staleness | request worker unpickles 6 payloads | unmeasured ("several MB" each) | n/a | mtime, not unpickle (5) |
| `mlb_deep` unpickle at boot | every worker | unmeasured, x3 | n/a (paused) | lazy load (6) |
| tennis Elo pools, MLB slate/game sims, NBA/NHL seasons, nfl_deep, matchup data jobs | already niced children, PC-first or HEAVY-gated | — | yes | keep as the model |

## (d) What could not be determined, and the instrumentation that would settle it

- **The real `quote_samples` row count** (and resolved count): no workflow exports
  `recorder.status()`, the local `markets.db` is empty, and I did not touch the live app. The fitted
  slope implies ~12-13 rows per tick and ~1M rows now; one `SELECT COUNT(*)` on `/data/markets.db`
  before the DROP would pin it (and the disk space the VACUUM returns).
- **Peak RSS of the in-worker builds** (NFL board, `_slate_sims`, presets, soccer, the n=3,000-20,000
  sport sims) and the **sizes of the deep payloads** the status polls unpickle: no offline fixtures.
  Two cheap instruments: (1) `jobs.timed` records `ru_maxrss` and `/proc/self/status` RSS at entry and
  exit beside the duration; (2) an `after_request` hook that logs any request whose worker RSS grew
  by >100 MB (path, delta, seconds) — that one line would have named `/api/recorder/backtest` on the
  first day. Also time the eight inline deep jobs (`jobs.timed`) so the next ledger can see them.
- **Whether the owner-pid churn is the kernel OOM-killer or Render's instance restart**: the notes
  show pids 7/8/9 reappearing within minutes at 2,047/2,048 MB, which is a fresh container either way;
  Render's event log would say which.
- The jobs log keeps 200 rows per snapshot; reconstructed from 264 snapshots it still misses runs
  between snapshots, and `nfl-board:w6` in pid 8 on Oct 16 shows a start with no end (killed worker
  or lost row). Raising `_KEEP` or writing end rows first would make it a complete record.
