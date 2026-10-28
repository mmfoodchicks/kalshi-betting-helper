# Fable review: the Millionaire queue fix (2026-10-28)

Reviewer: Claude Fable 5.1, adversarial pass on the uncommitted diff to `app.py`,
`boardshare.py`, `dfs_tourney.py`, `dk.py`, `nfl_game_sim.py`, `pc_worker.py`,
`static/app.js` and the "The Millionaire queue" guard section. Scratch work in
`<scratchpad>/review_mill_fable/` (live-lobby probe `live_lobby.py`, ESPN
schedule probe `sched.py` / `wk12.py`, mutant runner `mut_fable.py`). Nothing
in the repo was edited except this file.

## What I verified myself

- **Timeline** (`git show <sha>:errors/tourney-list-latest.json` on `sim-history`):
  week 7's classic board dg 155198 built 1792706664 = 2026-10-22 22:04 UTC, listed at
  85474a9 (10-23 21:22), gone at 71cabc6 (10-24 04:33) -- 30 h old, the day-old sweep.
  Request for dg 153728 pending from ts 1792856101 (10-24 15:35 UTC), re-pressed
  1792906455 (10-25 05:34) and 1792946881 (10-25 16:48); `pending` empty at 573e3fe
  (10-25 16:13) and 1ccf5d2 (10-26 04:43). No classic board for 153728 in any snapshot.
  Orchestrator's claims hold.
- **The working tree matches `fix.diff`** for the seven files (`git diff` vs the file:
  no difference). The **guards diff I was handed is stale**: the working tree has since
  re-pinned two older guards (lines 13101-13108, 15622-15652) and added a no-numpy branch
  to the builder-week check. See B1.
- **`<scratchpad>/mill/suite1.log` is RED**: `RESULT: 3494 passed, 2 failed`, `EXIT 1`
  (the two guards above, both fixture artifacts of the new clock window / TTL). The
  repaired fixtures look right, but no green full-suite run exists yet.
- **The new section passes here**: 7/7 (`run_mq.py`), with numpy and node v22 present, so
  the builder-week and tab checks ran for real. `pyflakes` clean on the six touched `.py`.
- **Live DK lobby** (`dk.slates("nfl")`, fetched 10-28): four classic slates -- 155589
  14-game "(Thu-Mon)", 155592 13-game "(Sun-Mon)", **153728 11-game untagged**, 155591
  8-game "(Early Only)"; 153728's top pool $6M. `dk.main_slate` returns 153728 at: now,
  Thu 10-29 noon MDT, Sat 10-31 19:00 MDT (the Saturday-night window), Sun 11-01 09:40 MST
  (the pregame window; lock 18:00 UTC after the clock change), lock+3h; `None` at
  lock+4h+1m and later (week 9's slate is not posted yet). The old rule gives the same
  answers on today's lobby -- and would have kept 153728 even once week 9's 11-game slate
  posts (strict `>` keeps the first on a tie), so this weekend was going to be right by
  luck either way; the fix is still the correct rule.
- **`current_week(day=)` against ESPN**: 2026-10-29 -> 8, 11-01 -> 8, 11-02 (MNF) -> 8,
  11-03 -> 9, 11-26 -> 12, 12-25 -> 16, 2027-01-03 -> 17. TNF/SNF/MNF/Thanksgiving/Christmas
  all land in the right week; `_slate_day` converts a Z string to the Eastern date
  (guard pins `2026-10-30T00:15Z` -> `2026-10-29`). No extra network: the same
  `nfl_live.schedule` loop, one call per week up to the slate's week.
- **DST**: every window and the slate pick are epoch arithmetic; `_cl_window_key`'s
  Saturday-night window is MT wall-clock on Oct 31 (before the change). Fine.
- **Where the queue lives**: `render.yaml` sets `DEEP_CACHE_DIR=/data/deep`, no
  `VIGIL_SIM_CACHE_DIR`, so `boardshare._DIR` is `/data/deep/boards` on the persistent disk.
- **Why the request vanished twice** (the orchestrator's open question): the file is only
  emptied by `cancel: true` (`api_dfs_tourney_request`) or by the sweep. The sweep is ruled
  out: the file's mtime was 10-25 05:34 and it was gone before 16:13 (10.6 h), while the
  showdown boards written 10-24 20:56 / 21:19 survived in place with continuous `age_s`.
  A disk loss is ruled out the same way: `artifacts.write_raw` stamps an adopted board
  `mtime=now`, so a re-ship after a wipe would have reset `age_s` to ~0, and it did not.
  No board for 153728 ever reached the server (not listed; no `BOARD-read` in the ledger
  snapshots). That leaves the owner's own ✕ / "cancel" link, twice (Sunday ~05:30-10:10 MDT
  and ~14:50-22:40 MDT) -- consistent with "clicking run doesn't run it" being press,
  cancel, press. Not provable from here; the attempt report makes the next one legible.
  Incidentally, `errors/mem-latest.json` shows a **container restart ~15:33 UTC Sun 10-25**
  (pid 50, uptime 2448 s at 16:13) -- the disk persisted; unrelated to the queue but it
  is the kind of swap the owner reads as a crash.
- **Why no week-8 board ever reached the server is still unexplained**, and the fix does
  not establish it. The orchestrator's reduced replay with week=7 (`wk_7.log`) *returns a
  board* (wrong, Allen/Jackson/Lawrence excluded); it does not fail. So "wrong week" explains
  the wrong Saturday pick and a wrong board, not "no board at all" across ~2 days of 10-minute
  retries (the classic task runs before the deep job every cycle, pc_worker.py:1160-1164, so
  it was reached). Whatever stopped the full-size build on the PC (memory at 180,000 worlds on
  an 11-game pool, `RosterUnavailable`, a crash) is invisible to the server, and if it
  recurs this Saturday the *scheduled* build fails exactly as silently as before. See MJ2.

## Findings

### BLOCKER

**B1 -- the only full-suite run on record is red (process).** `<scratchpad>/mill/suite1.log`
ends `3494 passed, 2 failed`, `EXIT 1`. The two failures are older guards whose fixtures the
working tree has since repaired (`tests/combo_audit_guards.py:13101-13108` repins the
`< 10 or sl.get("tag")` string to `dk.py`; `:15622-15652` moves the unpicklable-board
fixture's `ts` near `now` because `_tourney_pending` now drops three-day-old requests). I
read both repairs and they pin the same meaning as before. But per CLAUDE.md the push gates on
`python3 tests/combo_audit_guards.py; SUITE=$?` being 0 -- re-run it, read the exit code, and
only then commit. Not a code defect; listed as a blocker because it is the one thing that
must happen before this ships.

### MAJOR

**MJ1 -- the "nearest week" rule still has the ten-game cliff, and Thanksgiving week falls
off it.** `dk.main_slate` (dk.py:183) keeps `(games) >= 10`, inherited from the old
`_main_slate`. ESPN's 2026 week-12 schedule (`wk12.py`): Wed GB@LAR, three Thanksgiving games,
Fri DEN@PIT, **nine** Sunday afternoon games (six at 1:00, TEN@JAX 4:05, WSH@ARI and SEA@SF
4:25), SNF NE@LAC, MNF CAR@TB. DraftKings' main slate that week -- the one carrying the
Millionaire -- is nine games. So on **Saturday Nov 28 at 18:00 MT** `main_slate` finds no
week-12 slate at all and returns week 13's (Dec 6, 7.6 days out, 11 eligible games, posted by
then on DK's ~9-day lead) as "the main slate": the PC's Saturday-night window builds **week
13's board a week early** ("Schedule"), week 12's Millionaire is never built on its own, and
the tab's "Built automatically by the PC" entry names next week's slate all Thanksgiving week.
That is the owner's exact complaint, reproduced one month out, by the function written to
fix it. (The old code had the same cliff; the fix's stated contract is "the NEAREST week's",
which this breaks.) Week 17's ESPN times are still TBD placeholders (12:00 AM), so I cannot
say the same about January.
*Fix:* lower the floor to `>= 8` (nothing untagged on DK's NFL lobby sits between the
holiday slates and the main slate -- the Thanksgiving/Christmas/Saturday slates carry
suffixes and are 2-3 games; `_LINEUP_TYPES` already drops the untagged Snake), or better,
make `pc_worker._main_slate` pass `dk.contests`' top pools (already fetched and cached 600 s
in the showdown task) and require `>= _CL_MIN_POOL` the way `_tourney_auto` already does --
"the slate with the Millionaire" is the owner's definition. Either way the nearest-week
cluster stays. *Guard:* a lobby with a nine-game untagged $2.5M slate nearest and a
13-game untagged slate seven days later must pick the nine-game one (and `_tourney_auto`
must label it `main`). The current pin of the floor is a source string
(`tests/combo_audit_guards.py:13107`), which this change will have to replace anyway.

**MJ2 -- a *scheduled* build that fails is still invisible, and that is the half of the
complaint the fix cannot yet explain.** `_report_attempt` fires only under `if dg in wanted`
(pc_worker.py:921-956, 1051-1086). The owner's first sentence was "the million nfl hasn't
ran" -- the Saturday-night / pregame build, which nobody queued. If this Saturday's build of
153728 dies for the still-unknown reason that produced no board over ~2 days of retries (see
above), the tab says "not built yet - on its own the PC builds the Sunday main slate Saturday
night..." and nothing more, exactly as last weekend, and we still have no diagnosis. The route
exists; the gap is small.
*Fix:* let `/api/dfs/tourney/attempt` accept `req_ts: 0` meaning "scheduled" (app.py:2530
currently 400s it), store it under the same `tries[str(dg)]`, and have `api_dfs_tourney`
return it as `last_attempt` on `status: "none"` (and the list route beside the slate) with
one line in the tab's "not built yet" message: "the PC's last try at HH:MM: <why>". On the
PC, call `_report_attempt(url, tok, dg, {"ts": 0}, why)` in the four non-request branches.
Also ledger it server-side (`errlog.note("TOURN-attempt", msg=...)`) so the sim-history
snapshots carry it. ~25 lines plus one guard (a scheduled failure shows; a later success
hides it).

### MINOR

**MN1 -- the 8-day keep makes two readers unpickle a week of boards.** `api_dfs_tourney_list`
(app.py:2549-2578) loads *every* `cl_/sd_tourney_nfl_*.pkl` on each tab load, then sorts and
keeps 12; `dfs_hindsight.capture_tourney` (dfs_hindsight.py:96) does the same every half
hour in the recorder, inside a gunicorn worker on Render ("measure before it ships"). With
boards at "about 1-5 MB" and ~2-4 a week each kind, that is 10-15 multi-MB pickles per call
instead of 2-4. Cheap: stat-sort by mtime and unpickle only the newest 12 in the list route;
skip files with mtime older than two days in `capture_tourney` (a board is captured within 30
minutes of lock, so nothing older is new). Its docstring ("boardshare keeps a board a day")
is now false either way.

**MN2 -- a Build request expires silently after three days.** `_tourney_pending`
(app.py:2301-2328) drops a request three days old with no trace: a Build pressed Monday for
Sunday's Millionaire disappears from the tab on Thursday, unbuilt, and the owner sees
nothing queued and no board. The old code was no better (the file-level TTL, and the sweep
took the whole file after a day idle), so not a regression, but the owner is about to be
told "queued ... it tries again every cycle until you cancel", which is untrue after day
three. Fix: keep a request until its slate's lock (the label carries the start; or read
`dk.slates`), or show "expired" for a day; at minimum make the queued line say "for three
days".

**MN3 -- "nothing to build" names two causes; `build_nfl_classic` returns `None` for at
least five.** pc_worker.py:954 says "no $1M+ contest with room left, or no projections for
the slate's week yet", but `None` also comes from `_contests_with_capacity` dropping every
record, `RosterUnavailable` (dfs_tourney.py:2935 -- Sleeper's roster feed down, ledgered
`NFLD-roster` on the PC only) and the position floors (dfs_tourney.py:2953). The roster case
is the one the owner would act on differently. Fix: have the builders return a reason (or
raise a typed exception the task turns into the report), or append "or the roster feed was
down (PC ledger NFLD-roster)".

**MN4 -- guards pin strings and endpoints where they could pin meaning.** I ran my own
mutants against the current section (`mut_fable.py`); these SURVIVE: `_MAIN_WEEK_S` shrunk
to one hour (S1) and the cluster anchored a day early (S7) -- the "same week" meaning is
pinned only as "not seven days apart"; `_KEEP_LONG_S` cut to three days (S3) -- the lower
bound is unpinned (a 7.5-day-old board kept and an 8.5-day-old one removed would pin it);
the attempt file's own TTL removed (S4); the showdown "more than eight days out" branch made
unreachable (S5) -- neither that reason nor "not a one-game showdown slate" is exercised. The
games floor removed (S2) survives the section and is caught in the full suite only by the
source string at :13107 -- which MJ1 replaces. In CI (no numpy) the builder-week check is a
source-string pin by design, stated in the guard text; acceptable.

**MN5 -- the `req_ts` binding hides a report for up to one cycle after a re-press.** The PC
reads the queue at cycle start (ts T1); the owner re-presses during the build (T2); the PC
fails and reports `req_ts=T1`; the server shows nothing until the next cycle reports against
T2 (10 min plus a build). Correct by the stated design ("a fresh Build starts clean"); worth
one sentence in the tab so a vanished warning is not read as progress.

### NIT

- N1 -- `dfs_hindsight.capture_tourney` docstring: "boardshare keeps a board a day" -- now eight.
- N2 -- `<scratchpad>/mill/try_build.py` prints `art.get("week")` (None); the week lives under
  `slate.week`. Only the replay script; the artifact is fine.
- N3 -- `_report_attempt` POSTs every 10 minutes for up to three days for a request DK no
  longer lists (after lock) and for a kicked-off showdown. Harmless; the owner can cancel.
- N4 -- showdown boards built before this deploys on the wrong week (a Monday build of a
  Thursday game simulated the finishing week) stay "current" on the PC until a pool/price
  change or the pre-lock windows; the pre-lock windows (T-4h..T-25m) rebuild before lock, so
  no engine bump is needed. Noting it so nobody is surprised by a stale-looking board midweek.
- N5 -- `_task_classic_tourney` inserts each request at index 0 in ascending-ts order, so the
  NEWEST request builds first. Pre-existing; fine for one owner.

## Checks that came back clean

- `dk.main_slate` edge cases: lobby Eastern string and contest Z string both through
  `nfl_dfs.iso_ts`; two untagged 10+ slates in one week -> most games, earlier start on a tie
  (guard pins both); London 9:30 games are not on the untagged main; week-18 Saturday and
  holiday slates are 2-3 games and never qualify; a cluster of 3 days cannot join two Sundays.
- `current_week(day=)`: default unchanged for every other caller (`day=None`); preseason
  path reads seasontype 1 with the same `day`; `TOURN-week` fallback unchanged; no new fetches.
- `boardshare` keep: `str.startswith(tuple)` correct; `.lock`/`.tmp` files not kept; the PC's
  `tourney_state.json` lives a directory up and is untouched; the PC prunes identically, and
  showdown rebuild decisions do not change in effect -- `_rebuild_reason`'s "no board yet" no
  longer fires daily, but the pre-lock windows, the nightly/Sunday status windows and the
  price-sensitive `pool_sig_rich` still force every rebuild that matters for lock freshness.
  Disk: tens of MB over eight days on a 1 GB disk.
- The attempt route: token door (403 without it, guard-pinned); `dg`/`req_ts` ints both sides
  (`int(time.time())` in the request, `int(...)` on the PC and the server); atomic writes;
  the owner's request file and the PC's attempt file are separate so no cross-write race; the
  PC posts sequentially; `why` capped at 240 chars; one entry per dg; TTL'd; `escapeHtml` in
  both tab places (node check ran). A later success hides the report (board newer than the
  request); a re-press starts clean.
- PC reports: every `wanted[dg]` is behind `if dg in wanted`; `listed[dg]` behind
  `if dg in listed`; classic-kind requests skip the showdown loop; unlisted / kicked-off /
  >8 days / malformed each get a reason. A board built locally whose upload failed is
  re-shipped by the cycle-end `_sync_kind("boards")` (pc_worker.py:1176), so no silent
  "current but never shipped" case (only a schema skew blocks uploads, and the PC pulls within
  a minute).
- The tab: queue line and board line both show the report; Build and cancel clear it;
  `queued_attempt` rides beside an *older* board too (board older than the ask).

## Verdict

**AGREE AFTER FIXES:**

1. **MJ1** -- remove the ten-game cliff from `dk.main_slate` (floor `>= 8`, or key the PC's
   pick on the $1M+ contest as `_tourney_auto` already does), with a behavioural guard: a
   nine-game untagged Millionaire slate nearest beats a 13-game one seven days out.
2. **MJ2** -- report scheduled-build failures too (`req_ts: 0`), shown in the "not built yet"
   message and ledgered, with a guard. Without it the half of the complaint we cannot explain
   stays invisible if it repeats this Saturday.
3. **B1** -- a green full-suite run with the exit code read, on the tree as committed.

MN1-MN5 are at the orchestrator's discretion; I would take MN1's two one-liners and MN4's
cluster/keep pins in the same commit since the files are open. Everything else in the diff I
agree with: the nearest-week rule, the slate's own week in both builders, the eight-day keep,
the request-bound report, and the tab wording are right and I could not make any of them
misbehave on the live lobby, the ESPN schedule, or by mutation beyond what MN4 lists.

## CONFIRMATION (2026-10-28, second pass on the ship copy `<scratchpad>/mill/clean`)

What I checked on the final files: the ship copy's `git diff` equals `fix2.diff` plus
`guards2.diff` byte for byte (index lines aside) and carries no rushing MX hunk; the new
section passes 9/9 both with numpy and with `MQ_NO_NUMPY=1`; `pyflakes` is clean on the seven
touched `.py`; neither builder has a bare `return` or `return None` left (5 and 6 `_refuse`
calls, both reset `_REFUSAL`); each fix re-read against the code:

- **MJ1** -- `dk._MAIN_MIN_GAMES = 8` (dk.py:582-604); guarded by behaviour (Thanksgiving's
  9 beats next week's 13; a 7-game slate is never main; `_mq_3h` and `_mq_thu` pin the
  same-week cluster). My S1/S2/S7 mutants are now caught in both numpy modes.
- **MJ2** -- `req_ts 0` accepted (app.py:2270), `TOURN-attempt` / `TOURN-attempt-sd` ledgered,
  `last_attempt` hidden by a newer board and never the queued request's own
  (`_tourney_last_attempt`); the classic task decides "owed" before `dk.slate_for`
  (pc_worker.py:918-939 in the ship copy) so a quiet Thursday fetches and reports nothing (guarded);
  scheduled failures report as req_ts 0 in both tasks; the tab's `_dfsTourneyTried` renders on
  both branches, escaped (node ran).
- **MN1** -- newest 12 by mtime; `capture_tourney` skips files over two days old, docstring
  states the trade-off. **MN2** -- `starts_ts` recorded at request time, live until start +
  30 min, 3 days when unknown, never past 8 (`_tourney_req_live`, guarded in six cases).
  **MN3** -- every early return is `_refuse(why)`, the PC reports `last_refusal()`.
  **MN4** -- my S1-S7 all caught; 45 mutants on the orchestrator's list.

**Still wrong -- one item, and it blocks the ship until green:** the CI-like full suite on the
FINAL files is red. `<scratchpad>/mill/suite_c2_nonp.log` (21:08) ends `RESULT: 3014 passed,
1 failed`, `EXIT 1`. The failing guard is the older one at `tests/combo_audit_guards.py:17037`
("every builder that runs the depth-chart gate handles RosterUnavailable ..."), which pins
`"return None" in src.split("RosterUnavailable")[1][:80]` for `build_nfl_showdown` and
`build_nfl_classic`. MN3's rewrite made that line
`return _refuse("the depth-chart gate's roster lookup is unavailable (NFLD-roster)")`
(dfs_tourney.py:1329-1330, 2956-2957), so the string pin fails while the behaviour it guards --
None from the builder, no board with the gate off -- is intact (the same guard's own dict reads
`(True, True)` for all four builders). The numpy run (`suite_c2_np.log`, still at line 2391 when
I looked) will fail the same way: the pin reads source, not numpy. *Fix:* repin to
`"return _refuse(" in ...[1][:120]` and, now that the guard can pin the meaning, require
`"NFLD-roster" in ...` too; then re-run both suites on the final files and read both exit codes.

Minor, take or leave, none blocks:
- **Ledger noise.** A $500K+ showdown that enters the 8-day window before Sleeper posts that
  week's projections is "owed" every cycle (`_rebuild_reason` -> "no board yet"), refuses with
  "only 0 players with a week-N projection (six needed)", and posts a `TOURN-attempt-sd` note
  every 10 minutes for a day or two (~150 a day per showdown; three such slates a week). It is
  honest and transient (the card's warning goes when the board builds), and the Millionaire's
  code is separate, as intended. If it grates, skip the ledger note in `api_dfs_tourney_attempt`
  when the stored record for that dg has the same `req_ts` and `why` and is under an hour old.
- **Three of my extra mutants survive** (`<scratchpad>/review_mill_fable/mut_fable2.py`, both
  numpy modes): F1 drops `_tourney_last_attempt`'s own 3-day age check (unpinned); F4 gates the
  classic task's "no player pool posted" report on `dg in wanted` (no scheduled slate_for-None
  case is exercised); F6 sorts the list by NAME instead of mtime -- the fixture's names 900..913
  and mtimes are co-monotonic, so the "newest by mtime" meaning is not pinned (give the newest
  mtime to the lowest number). F2, F3, F5, F7 are caught.
- Stale text: `pc_worker._main_slate` and `app._tourney_auto` docstrings still say "ten-plus
  games"; `dk.py` and CLAUDE.md say eight.

**Verdict: AGREE TO SHIP once the :17037 guard is re-pinned and both full-suite runs on the
final files exit 0.** Every fix I asked for is in and verified; the only thing between this
tree and the owner's screen is a stale source pin the final suite caught, which is what the
suite is for.
