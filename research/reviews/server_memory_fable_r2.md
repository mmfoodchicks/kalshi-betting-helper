# Review of the server-memory change (crypto removal + "the PC builds, the server serves") — Fable, 2026-10-29

Reviewed: `full.diff` against HEAD `bcc39f4` (38 files, 5,509 lines), applied to a `git clone --shared`
scratch tree (`.../scratchpad/mem/tree`); nothing in the real tree was touched. Companion to
`server_memory_fable.md` (the investigation this change answers).

## Verdict: AGREE AFTER FIXES — two MAJOR fixes, no BLOCKER

The change removes the mechanism behind every 1.0-1.77 GB worker (the per-page-load
`recorder.backtest()` whole-table read and the crypto calibration fit), does it cleanly (nothing left
references the removed code; the failsafe paths behave exactly as HEAD with the PC off), and the
PC-first plumbing is the right shape. Two of the new grace rules reach further than they should and
need a fix before shipping: (1) the Kalshi NFL/CFB **index** — which is where `ladders()` / `implied()`
and so the makers' asks come from — now serves prices up to 30 minutes old while the PC is on, where
HEAD refreshed it every 2 minutes; (2) the NFL/UFC presets' **tab kick** (`api_nfl_presets` /
`api_ufc_presets`) still forces an in-worker build (`_slate_sims` + the DP) whenever the payload is over
30 minutes old, which with the PC rebuilding on the same 30-minute threshold is exactly the moment the
server was meant to wait. Everything else is MINOR or a documented trade-off. The child-isolated
server fallback for board builds (my 3(iii)) is **not a blocker**: with the PC on no worker build
starts, and with the PC off nothing is worse than HEAD.

## What I ran (scratch tree, HEAD + diff)

- `python3 -m compileall`: OK. `pyflakes *.py tests/*.py`: no findings in service modules; the nine
  findings in `tests/` are identical at HEAD (shifted line numbers) and the suite's lint gate excludes
  tests. `VIGIL_NO_BG=1 python3 -c "import app"`: 0.2 s, 119 routes, no threads started.
- **The full guard suite**: `EXIT=0`, 3,559 PASS lines, no FAIL (the "thread kaput" traceback in the log
  is the excepthook test). CR and PCF sections run green, including the drop/VACUUM trace, the
  X-Art-Age cap, the boards-pass timeout, the VmHWM reset and the presets deferral matrix.
- Dangling references: none. Service modules, JS, HTML, `.github/workflows`, `tests/run_smoke.py`,
  `tests/model_sanity_check.py` carry no import, route, DOM id or function of the removed code
  (`tests/combo_audit_math.py` only matches a comment; `/api/simulate/price` is the kept commodity
  sim, called by the sim tab with `kind=commodity`). `node --check static/app.js` passes;
  `loadStrategy`, `runScan`, `runBacktest`, `trackMarket`, `refreshPreview`, `renderMarkets` are
  neither defined nor called; `addBet`, `calNote`, `loadBestBets`, `setupTabs`, `applyTierUI` exist.
  DOM ids: the JS uses 34 ids that no static HTML defines at HEAD and 34 after (all built by JS
  templates) — zero new ones; of the 31 ids the template lost, none is still used by the JS.
- `deep_cache.run_child`'s stdout channel: importing `racing_sim, pro_sim, nfl_season, cfb,
  nhl_deep_data, nba_deep_data, model_trust` writes 0 bytes to stdout; none of the 17 repo modules in
  that import closure contains a bare `print(`; the entry functions take `log=None` or print nothing.

## Findings

### MAJOR 1 — the Kalshi NFL/CFB index is a price source, and its grace is 30 minutes

`kalshi_nfl.index()` (patched `kalshi_nfl.py:479-487`) and `kalshi_cfb.index()` now serve the disk copy
within `boardshare.pc_grace(_TTL)` = `max(120, 1800)` while the PC is on or behind. The index is not a
board: its entries hold the asks (`e["ml"][team]`, `e["spread"][...]`, `e["total"][n]`,
`e["players"][key]` at `kalshi_nfl.py:196, 267, 281, 295`), and `ladders(suffix)` / `implied(suffix)`
(`:387-398, :522+`) read `index().get(suffix)` — so the slate board, `_slate_sims`' kref pricing, the
presets' rungs and the combo maker's legs are priced off it. HEAD rebuilt it every 120 s in whichever
worker asked (a few pages of 200 markets, one `_fetch`; not a simulation). After the change, while the
PC is on, the server's copy is as old as the PC's last upload: the boards pass uploads only at its END
(`boards_main` -> `_sync_kind` after `_task_boards` returns), so an index built at the start of a pass
that waits its full 300 s and then runs the slower builders is 5-10 minutes old on arrival, and up to
30 minutes old when a pass runs long (the pass is killed at 1,800 s). Stale asks make stale edges on
every NFL/CFB surface, and nothing on the page says the quotes are not current.

**Fix.** Leave the two indexes on their own TTL — they are fetches, not builds, and the memory the
owner complained about is not here: in `kalshi_nfl.index()` / `kalshi_cfb.index()` use `_TTL` (drop
the `pc_grace`), or add a cap, `pc_grace(ttl, cap=300)`, so the server fetches itself whenever the
PC's copy is older than one boards pass. If the 429s are the worry (16 `KNFL-markets-fetch` in the last
week), have the boards pass upload the index right after building it rather than at the end.
Failsafe risk: none (the PC-off path is already the TTL path). Update the PCF guard that pins
`"boardshare.pc_grace(_TTL)"` in both files accordingly.

### MAJOR 2 — the presets' tab kick bypasses the PC deferral at the moment it matters

`nfl_presets.tick(force=True)` / `ufc_presets.tick(force=True)` skip `_pc_covers` (`if not force and
_pc_covers(...)`), and `api_nfl_presets` (patched `app.py:4499+`) still kicks a forced build in a
claimed worker thread whenever `now - built_ts > nfl_presets._STALE_S` (1,800 s) — `api_ufc_presets`
likewise. The PC's `pc_build` rebuilds on the same 1,800-s threshold and its payload reaches the server
only at the end of a pass, so a tab open on the presets (polled every 8 s, `_nflPresetFetchTs`) will
routinely find a 30-31-minute-old payload and kick: `_slate_sims` (16 games x 4,000 if the pool is
also past its TTL) plus the DP, in a web worker, while the PC is on — the exact build the deferral
exists to avoid, and the PCF matrix pins "on, forced -> builds" as intended behaviour of `tick`.

**Fix** (keep `tick(force=True)` as it is, gate the kick): in both routes, when `boardshare.pc_status()`
is on/behind, treat the payload as stale only past `nfl_presets._PC_STALE_S` / `ufc_presets._PC_STALE_S`
(45 min) — or simply do not kick while `_pc_covers(payload, same)` is true — and answer with the PC's
payload and its age. With the PC off the kick threshold stays 1,800 s. Add the case to the PCF matrix.

### MINOR 3 — placeholders and short-TTL boards inherit the 30-minute grace

`boardshare.adopt` stretches every board under `nonblocking` to 1,800 s while the PC is on. For boards
whose TTL is already 1,800 s nothing changes, but: matchups (`TTL` 900) publishes a failed/empty build
backdated to `TTL - _RETRY_S` so it reads fresh for two minutes and is retried ("it is tried again
within two minutes of the next visit", `matchups._build`); under the grace that placeholder is served
for up to 1,020 s before the server retries, while the PC's `pc_board` never publishes an empty slate —
so a slate the PC cannot build (no book on the PC) waits 17 extra minutes and the placeholder's own
text is wrong while the PC is on. Hockey/basket/soccer (600), golf/LoL (900) and tennis (1,200) boards
also carry Kalshi quotes and now run 5-10 minutes old in the normal case (one boards pass), 30 in the
worst — the design's trade, and acceptable, but the tab should show the age (the payloads carry
`age_s` only where a route adds it). Suggest: in `adopt`, a disk payload flagged `placeholder` or
`empty` keeps the plain TTL (one `isinstance(dict)` check); and surface `age_s` on the boards that
lack it. The "empty never out-freshens a full board" rule itself is intact (the server backdates, the
PC never publishes an empty), and "behind" counting as on is fine (bounded by the grace).

### MINOR 4 — `run_child` uses stdout as the pickle channel

Verified clean today (above), and `matchups.build_isolated` has used the same channel in production.
Cheap insurance against the next `print` in a nightly's dependency: in the child, `fd = os.dup(1)`
before `sys.stdout = sys.stderr`, write the pickle to `fd`. Otherwise a single stray line turns the
nightly into an `UnpicklingError` every night the PC is off, with the real output lost in `stderr[-300:]`.

### MINOR 5 — the boards process survives a PC update

`pc_loop._boards_loop` is a daemon thread; when the loop exits for an update (`UPDATE_WAITING` ->
`.bat` restart) the running `pc_worker.py --boards` child is not stopped, and the fresh loop starts
another pass: two passes (old and new code) can run and upload for up to 30 minutes. Harmless to the
server (atomic writes, `SCHEMA` gate) and the same exposure the cycle already had, but keep the
`Popen` handle and terminate it before exiting, or take a lock file per pass.

### MINOR 6 — `pc_grace` reads the status file on every board request

`adopt` -> `pc_grace` -> `pc_status()` opens and parses `pc-status.json` per call, on every board
request and every `_slate_sims`/`index()` call. Tiny today; a 5-second memo in `pc_status()` would
remove it from the hot path.

### MINOR 7 — follow-ups deferred from my report, agreed as follow-ups

Child-isolated server fallback for board builds and `_slate_sims` (the PC-off path still builds in a
worker thread, as HEAD did); `dfslog.report()`'s whole-table read; the unswept per-date caches
(`hockey._cache`, `basket._cache`, `nfl_game_sim._cache`, `nfl_dfs_sim._cache`/`engine_reports`,
`cfb_board._cache`). None blocks this ship.

## The specific questions

- **References to removed code**: none (see "What I ran"). `kalshi.TIMEFRAMES` / `SCANNABLE_COINS` /
  `series_ticker` / `get_open_markets` gone and unreferenced; the removed tier features are not
  consulted anywhere (`dfslog`'s "backtest" strings are a column, unrelated).
- **Grace edge cases**: placeholders/backdated empties — see MINOR 3 (only sub-1,800-s TTL boards are
  affected; NFL/DFS/CFB empties keep their 5-minute retry since grace == TTL there); "behind" — on,
  bounded by the grace, and the PC updates between builds, fine; PC up but one board failing — served
  stale to the grace, then the server builds, and the PCF guard pins it ("past the grace the server
  builds"); a PC upload newer than the memory copy is adopted on the next request (`held` compares
  mtimes), also pinned.
- **Presets deferral / deep PC-first stranding**: no strand. Presets: a same-slate payload is served
  while < 45 min old, a missing one waits 15 min, then the server builds; the PC rebuilds every 30 min,
  so the 45-minute line leaves 15 minutes of pass latency — right. The kick is the gap (MAJOR 2). Deep:
  `_deep_pc_first` -> `DEFERRED` holds for `_DEEP_PC_WAIT_S` (45 min) then the child runs; a request
  the PC answers before midnight is simply fresh at the next pass. The PC's `_task_deep` sits behind
  the tourney builds in the main cycle, so a 45-minute wait can expire mid-cycle and both run — double
  work on the PC's cores, no harm to the server.
- **`run_child` pickling**: args are strings/ints, returns are dicts (`sim_f1`, `sim_nascar`,
  `project`, `run_season`, the deep-data `update` states) or a set (`model_trust.refresh`); `DEFERRED`
  is decided in the parent and never crosses the boundary; `model_trust.load()` is read in the parent
  after the child's `refresh()` wrote the file; `None` returns are recorded "empty" as before; a
  non-zero child raises and the scheduler records the error. `HEAVY_BUILD` is held by the parent, so
  it serializes with the pro-season and matchup children. See MINOR 4 for the one hardening.
- **VACUUM on the live `/data/markets.db`**: safe as written. The DROPs run in autocommit (the sqlite3
  module opens no implicit transaction for DDL), `VACUUM` outside any transaction, `secure_delete=OFF`
  is per-connection and set on that connection; in WAL mode readers in the other workers keep their
  snapshot, writers wait on `busy_timeout` (10 s) for the measured 0.3 s; VACUUM's temp copy is the
  live ~13 MB in the container's temp dir, not the 1 GB data disk; a locked-database failure is
  ledgered (`STORE-crypto-vacuum`) and the checkpoint still runs; `store._lock` is held only in the
  owner. Idempotent: the second call finds nothing (pinned). One note: `init_db()` no longer creates
  `markets`, so the drop cannot loop.
- **The watch**: a `/proc/self/status` read and one `clear_refs` write per worker per 20 s — negligible;
  `_thread_tops` only on an alarm. `clear_refs` "5" resets VmHWM for the whole process (per-mm, so the
  8 threads are covered) and the guard proves a transient 120 MB shows in the peak and clears; refusal
  falls back to VmRSS. `_inflight_req`/`_recent_req` are per-thread keyed and GIL-atomic list ops; the
  ring is capped at 40. The detail lands in the public ledger: it holds basenames, line numbers,
  function and thread names, and `request.path` without the query string — nothing sensitive.
- **Mapping of my report**: (1)(2)(8) removed; (3) grace + the PC's own boards pass; (4) PC-first +
  child for all eight (the PC's job list already carries every key); (5) `generated_at` memo — your
  reason to keep the record's stamp (deep uploads get upload-time mtimes) is right; (6) `mlb_deep`
  lazy (every reader calls `_deep_refresh()` first: `app.py:3447, 4837, 4866`), soccer on the pass,
  tourney reads once per version; (7) `MEM-worker` with VmHWM is better than what I asked for.
  Missed/new: MAJOR 1 and MAJOR 2 above (both introduced by the change, not in my report).

## Ship list

1. Index grace: TTL or `cap=300` for `kalshi_nfl.index()` / `kalshi_cfb.index()`; re-point the PCF guard.
2. Presets kick: gate `api_nfl_presets` / `api_ufc_presets` on `_pc_covers` / `_PC_STALE_S` while the
   PC is on; add the case to the PCF matrix.
3. (Optional, cheap) placeholder/empty payloads keep the plain TTL in `adopt`; `run_child` writes the
   pickle to a dup'd fd; the boards process is stopped on update.

With 1 and 2 in and the suite green, I agree to ship.


---

## Round 3 — Fable, 2026-10-29: the r2 fixes (base 5dc486c + `r2_fixes.diff`)

**Verdict: AGREE.** Both MAJOR fixes do what the ship list asked, the three optional items are in, each piece is pinned by a guard that a single-point mutant fails, and nothing new rises above MINOR. Ship once the full suite reports green.

Ran (scratch clone at 5dc486c + diff at `.../scratchpad/mem/r3tree`; the real tree untouched; no full suite): compileall/pyflakes clean on the eight files; `VIGIL_NO_BG=1 import app` 0.25 s, 119 routes, no threads; my own 30-check oracle (`.../scratchpad/mem/r3_checks.py`) green on the patched tree and red on all five mutants I tried (grace back in `kalshi_nfl.index`, `limit = _STALE_S`, UFC gate removed, `_short_lived` disabled, `dup2` dropped: 8/3/4/4/1 failures). The orchestrator's 16 mutants (`mutate3.py`) are the right single points.

1. **MAJOR 1 fixed.** `kalshi_nfl.py`/`kalshi_cfb.py` are byte-identical to bcc39f4 (`git diff bcc39f4` empty); `index()` (:482/:108) serves a copy within 120 s and fetches past it in every PC state (on/behind/off checked), from disk or memory. The remaining grace takers are the fourteen board builders (basket, cfb_board, golf, hockey, lol, matchups, mfutures, nfl_dfs_sim, nfl_game_sim, racing_dfs, soccer, tennis_prices, ufc_sim, nonblocking); `_slate_sims` bakes no prices ("price_cands fetches live Kalshi quotes at build time") and its `_SIMS_TTL` 1800 equals the grace. No price source takes the grace.

2. **MAJOR 2 fixed.** `app.py:4552-4561`, `:4485`: PC on/behind → the NFL kick needs `> _PC_STALE_S` (44 vs 46 min checked), off → HEAD's `_STALE_S` (29/31); no payload → waits `_PC_WAIT_S` (14/16 min); the week-over/no-future-kickoff rule still blocks at any age; UFC kicks only on a missing payload, gated the same way. `_pc_status()` and `_pc_covers` read one file (`app._PC_STATUS_PATH == boardshare.PC_STATUS_PATH`). `tick(force=True)` occurs nowhere else in app.py; the recorder's tick honours `_pc_covers`; `pc_build` is PC-side. No web-worker presets build remains while the PC covers.

3. **MINOR 3/4/5, no regression.** `_short_lived` (boardshare.py:87-112) restores matchups' 2-minute placeholder life (the only `placeholder` producer); the `empty` producers (nfl_game_sim, nfl_dfs_sim, cfb_board) have TTL 1800 = grace, so nothing moves; the PC's own `adopt` is unaffected (it never reads itself "on": pc_worker never writes pc-status.json). `run_child` (deep_cache.py:144): `os.dup` is non-inheritable, the eight jobs spawn only `subprocess.run` children with their own pipes (no multiprocessing), a job leaving a grandchild running returns in ~2 s, a 3 MB result crosses, a raising job raises RuntimeError with the reason. `_stop_boards` (pc_loop.py:174-184): the restart race (daemon loop starting a new pass between the stop and `sys.exit(0)`) is closed by `_boards_loop`'s 30-s floor (:195).

**MINOR A (in 5dc486c, not the fixes; I missed it in r2).** `boards_main` (pc_worker.py:1201-1210) syncs and exits when `_task_boards`' 300-s loop (:345-367) ends; every board builder runs in a daemon thread, which dies with the process, its `.lock` left. The loop is from the in-cycle era (a8b185f), when hours of cycle followed it; the ":367 will finish next cycle" message is now false. Measured builds fit even on the server's one core (`nfl-board` max 124 s over 17 runs, `cfb-board` 30 s, tennis pools 55 s); the one that may not, soccer's 6-hourly refit subprocess (soccer.py:1114, up to 1,200 s), survives as an orphan and lands `soccer_model` for the next pass. Fix (a few lines): in `boards_main`, join every non-main thread with a budget of `BOARDS_TIMEOUT_S` minus elapsed before `_sync_kind`; reword :367; guard: a builder thread still running at the deadline is joined before the pass syncs.

**MINOR B.** A pass is in flight ~90% of the time (300-s window plus the inline matchups builds, then a 30-s floor), so most pushes kill one: builders mid-build leave `.lock` files that `claim` takes over only after `CLAIM_TIMEOUT` (boardshare.py:28, 300 s), so those boards sit out the fresh loop's first pass — inside the server's 30-min grace, so invisible there. On Windows `terminate()` is TerminateProcess and does not reach the pass's own children: a soccer refit child finishes on the old code (the artifact the un-stopped pass would have shipped anyway), a matchups `build_isolated` child dies on its closed pipe. Acceptable as is; the cleaner path is a stop marker `_task_boards` checks between builders (the `_task_note`/`_yield_point` pattern), not a kill.

Noted, no fix: in a web worker `nfl_presets._pc_wait[0]` (:449) is set at the first no-payload request and never cleared while the PC stays on (the route only ever calls `_pc_covers(None, False)`), so a payload that vanishes a second time in one process lifetime kicks at once — HEAD's behaviour, one claimed build, offseason only in practice.

4. **Guards.** Each fix is pinned: the index guard (both modules, PC on: within-TTL copy served, TTL+300 copy inside the grace fetched here) plus the source pin on the exact `boardshare.get(..., _TTL)` lines and `pc_grace` absent; the 11-case route matrix drives the real route through the test client with `pc_status`/`get`/`claim` stubbed; the six-case `adopt` guard covers the memory and disk branches and keeps a full board's grace; the noisy-job guard covers import-time print, in-job print and `os.write(1)`; the `_PfPass` guard checks `Popen`, both waits, the kill and that `_BOARDS_PROC` holds the pass during the wait, and `_stop_boards` runs on a real process with the source order (`_stop_boards()` before `sys.exit(0)`) pinned. Re-pointing the line-12574 guard from `"> nfl_presets._STALE_S"` to the `limit = ...` line and `> limit` is faithful and sharper: `_STALE_S` is still pinned as the PC-off threshold, `_PC_STALE_S` now as the PC-on one; its description ("kicks a first build and a stale one") stays true.

**MINOR 6:** declined, accepted — one ~200-byte read per board request (`index()` no longer makes it after MAJOR 1), and a memo would blur the on/off switch exactly when `adopt` must start building, plus flip-the-file test-ordering fragility.

### Orchestrator's response (2026-10-29)

MINOR A fixed before the push, by a narrower route than joining every thread: joining every non-main thread would hold each pass for the full budget behind any long-lived daemon thread a module starts. Instead:
- `boardshare` keeps the claims this process holds (`_HELD`, `held_claims()`; every claim site releases in a `finally`).
- `pc_worker.boards_main` uploads what is done when the window closes. It then waits for its own claimed builds until `_BOARDS_JOIN_S` (1,200 s) from the pass start, releases any claim it abandons so the next pass may take it at once, and uploads again.
- The `:367` message is reworded.
- Guards pin the behaviour (a one-second build, a stuck one, the upload order, the freed claim) and both timing relations: the next pass replaces the first upload inside `PC_GRACE_S`, and the last upload lands before `BOARDS_TIMEOUT_S`. Seven mutants, all caught.

MINOR B is accepted as is.

## Round 3 follow-up — Fable, 2026-10-29: the MINOR A fix (`r3_minorA.diff` on 6c6b03d)

**Verdict: AGREE** on the fix; trade-offs (a)–(c) accepted.

Checked (scratch tree `.../scratchpad/mem/r3tree` = 5dc486c + r2_fixes + r3_minorA, no suite): compileall/pyflakes clean. Every claim the wait can see is released in a `finally` (nonblocking boardshare.py:235; nfl_game_sim.py:1092 and `_slate_sims` :1941; nfl_dfs_sim.py:1184; cfb_board.py:562; mfutures.py:617), so no pass waits 20 minutes on a claim nobody will release; `pc_build`, `kalshi_*.index` and `matchups.pc_board` never claim. `set(_HELD)` copies under the GIL. My own harness of `boards_main` (`.../scratchpad/mem/r3_minorA_check.py`): the first upload sees the window's leftovers, the 1-s build lands, the second upload sees none, the stuck lock is gone and re-claimable at once, an empty pass uploads once without waiting, a failing upload is printed not raised. The relation guard pins 1200+30+300 < 1800 and 1200+300 <= 1800 rather than the number. Nit, no change needed: a `_task_boards` that raises outside its per-builder try (its own imports) skips the wait and leaves its claims; the `finally` still uploads.

**New finding, outside this fix, pre-existing in 5dc486c — MAJOR for the change's stated goal, one line to fix.** `pc_grace` (boardshare.py:63-68) is `max(ttl, PC_GRACE_S)`: for the four TTL-1800 boards (NFL, DFS, CFB, futures — the "tens to ~175 MB" builds) the grace equals the TTL, zero slack. The PC checks each board once per pass (`_task_boards` pops it when it returns a value, pc_worker.py:355-362) and uploads at the window's end, so once the PC's copy crosses 1800 s the rebuilt upload lands 5-10 min later (0-330 s to the next pass start + the 300-s window + upload), and meanwhile any request starts a web-worker build on the server (`adopt` -> None -> `claim` -> `_bg`, e.g. nfl_game_sim.py:1053). HEAD avoided this by accident: uploads took the upload-time mtime, so the server's copy aged out one PC cycle after the PC's; `X-Art-Age` (new in 5dc486c, app.py:672-679; 0 occurrences at bcc39f4) removed that slack. Fix: in `pc_grace`, `return max(ttl + PC_SLACK_S, PC_GRACE_S)` with `PC_SLACK_S` ~ 720 (two pass periods plus an upload, the gap above) — sub-1800 boards stay at 1800, TTL-1800 boards go to 2520 — plus a guard: a TTL-1800 PC copy at 1800+300 s is still served with the PC on. (A PC-side lead so the PC rebuilds early is the alternative.) Without it the four boards the change named are still built on the server about twice an hour in season while a tab polls.

### Orchestrator's response

- **Fixed as proposed.** `boardshare.PC_SLACK_S = 720`. `pc_grace` returns `max(ttl + PC_SLACK_S, PC_GRACE_S)` while the PC is on or behind, and `ttl` otherwise. The PC never sees itself as on, so its own rebuild timing is unchanged.
- **Why the server-side slack, not a PC-side lead:** a lead would rebuild the heaviest boards about every 18 min instead of every 30, with more ESPN and Kalshi fetches from the PC. The slack costs nothing on either machine. The trade is that while the PC is on, those four boards can be served up to about 38 min old in the normal case (42 at most) instead of 30.
- **Guards:**
  - With the PC on, a TTL-1800 copy at 2,100 s is served, and one at 1,800 + 720 + 60 s is not.
  - With the PC off, the 2,100-s copy is not served.
  - The older "a longer TTL keeps its own" guard now expects the TTL plus the slack.
  - Three mutants, all caught: the slack removed, the slack set to 0, and the slack applied with the PC off.
- **Why MINOR A mattered more than "minor":** `_task_boards` counts a stale-while-revalidate return as "fresh", so before the wait, the PC's rebuild thread for those boards died at every pass exit.
- **Process note.** Full suite run 7 went red on the JOIN relation guard, which read `_BOARDS_JOIN_S` as 1700. That was a mutant (`1200 -> 1700`):
  - The mutant kept the file size, and the restore landed in the same second.
  - A `__pycache__` pyc compiled from the mutant therefore passed Python's mtime+size check, and the suite loaded it.
  - The source was right throughout.
  - The fix: `__pycache__` is cleared before every suite run, and the mutation harness drops each file's pyc and bumps its mtime after the restore.
  - CI checks out fresh and was never exposed.

## Round 4 — Fable, 2026-10-29: the zero-slack fix (`r4_full.diff` on 6c6b03d)

**Verdict: AGREE.** `pc_grace` is exactly the proposed `max(ttl + PC_SLACK_S, PC_GRACE_S)` with the PC on or behind, `ttl` off (boardshare.py:67-76). Checked at the boundaries in my scratch tree (5dc486c + r2 + r3 + r4, lint clean): on and behind give 600->1800, 1200->1920, 1800->2520, 6 h->6 h+720; a TTL-1800 copy at 2,100 s is served, at 2,521 s built, a memory copy at 2,400 s held, an empty board past its TTL still builds; off is the plain TTL (2,100 s built, 1,700 s served). The three guard cases plus the 6 h + slack expectation pin the behaviour, and the 720 is pinned beside its reason. Expected side effects, fine: tennis (TTL 1200) is served to 32 min, the slate sims (nfl_game_sim.py:1858, `pc_grace(_SIMS_TTL)`) to 42 min with prices still fetched live.

MINOR nit: two comments still say "30 min" — pc_loop.py:44 ("a PC copy up to boardshare.PC_GRACE_S (30 min) old") and pc_worker.py:1211 ("inside the server's 30-minute grace", now true only for sub-1800 boards); one line each. On the pyc incident: run the mutant harness's suite with `python3 -B` (or PYTHONDONTWRITEBYTECODE=1) as cheap insurance beside the cache clear. Agreed on (2): MINOR A was a real defect for every board whose builder returned stale-while-revalidating.

### Orchestrator's response

Both comments are fixed. With the slack in place, full suite run 8 (on clean caches) had one failure: the CFB failsafe guard, "the last good board is served at once while a rebuild runs". That guard puts the slate at `_BOARD_TTL + 60` (1,860 s) and expects a rebuild:
- **Cause:** an earlier guard's valid-token request (`_pc_auth_ok` -> `_pc_note_seen`) stamps the real `/tmp/pc-status.json` "on" for five minutes. With the slack, the copy was served instead of rebuilt.
- **Why it never failed before:** before the slack, `pc_grace(1800) == 1800` whatever the PC's state, which hid the ambient "on".
- **Fix:** the block now stubs `boardshare.pc_status` to "off", which is what its scenario ("PC off, ESPN 403 on the server") assumes, and restores it in its `finally`.
- **Scope:** no other guard in `tests/*.py` puts a copy in (1800, 2520] (`guards_matchups` stubs `nonblocking`).
- **CI:** CI would have hit the same failure, since the stamp happens inside the run.

## Round 5 — Fable, 2026-10-29: the CFB guard fix (`r5_full.diff` on 6c6b03d)

**Verdict: AGREE.** The guard's scenario is "PC off", so stubbing `boardshare.pc_status` to off (restored in its `finally`) pins the right state; cfb's own sims read is a plain `_SIMS_TTL` (cfb_board.py:579), so nothing else in that block depended on the PC. My band grep agrees with yours: only that guard puts a copy in (1800, 2520]; `guards_matchups.py:339` (TTL+5) is safe because it stubs `nonblocking`; nothing sits in the 6-hour band. Both new comments (pc_loop.py:44, the `_BOARDS_JOIN_S` note) are accurate. Lint clean; my r2/r3 oracles re-run clean on the r5 tree.

MINOR follow-up, not blocking: the root cause stays — a guard's token call stamps the real `/tmp/pc-status.json` "on" for 300 s, so any later guard expecting a server build inside (TTL, grace] is timing-dependent on suite speed. Setting `VIGIL_SIM_CACHE_DIR` to a temp dir at the suite's top (before the first `app`/`boardshare` import) closes the class and keeps the dev box's /tmp untouched.

### Orchestrator's response

Agreed as a follow-up, not in this change. Moving `VIGIL_SIM_CACHE_DIR` for the whole suite also moves every guard's boards and deep-cache paths, so it needs its own full run and review. Recorded beside the other follow-ups: child-isolating the server's fallback builds, `dfslog.report`'s whole-table read, and the unswept per-date caches.
