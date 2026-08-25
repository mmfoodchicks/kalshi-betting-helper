# Vigil — working agreement

Flask sports-betting / Kalshi analysis app, live at https://vigil-vdoh.onrender.com.
Single-user (the repo owner). Read this before touching anything.

## Non-negotiables

- **Branch**: all work goes on `claude/kalshi-crypto-predictor-ckutwm`. Never
  push anywhere else without being asked.
- **No pull requests** unless explicitly requested. Pushing to that branch
  auto-deploys.
- **Commit trailers**: match the trailer block on recent commits exactly —
  `git log -1 --format=%B` and copy the footer format.
- **Secret scan before every commit**: run `sh scripts/secret-scan.sh` and get
  a clean result. It greps the staged diff's added lines for two
  historically-leaked key prefixes and for credential-shaped assignments. The
  patterns live in that script (which excludes itself from the search) rather
  than in prose here, so documenting the check can never trip it.
  `vigil-pc.cfg` (holds SIM_TOKEN) is gitignored and must stay that way, along
  with `pc-simcache/` and `pc-deepcache/`.
- **Guard suite gates every push**: `python3 tests/combo_audit_guards.py`
  (~1,390 checks, 3-8 min). Check the **exit code explicitly** —
  `python3 tests/... ; SUITE=$?; [ $SUITE -eq 0 ] && git commit ...`. Piping to
  `tail` once masked a red suite and shipped a broken commit.
- **Batch pushes.** Every push triggers a ~30-minute Docker build on Render and
  a swap that cold-starts the app and emails a health-check alert. Group related
  work into one commit where you reasonably can.

## Conventions

- **No silent `except Exception: pass`** in service modules — the guard suite
  fails the build on it. Use `errlog.note("<STABLE-ID>", e)` so the failure is
  visible in the ledger. IDs are grep-able and stable (e.g. `SLATE-child`,
  `WARM-game-sim`, `PCUP-art-write`, `MEM-high`).
- **pyflakes is part of the guard suite** — unused imports/vars fail it.
- Comments explain *why*, especially for anything counter-intuitive or
  measured. Most constants in this codebase were fitted against real data; say
  what was measured and what it cost. Match that density.
- New behaviour ships with a guard in `tests/combo_audit_guards.py` that would
  have caught the bug it fixes.

## Reading the running app without touching it

The app holds no GitHub credentials. Workflows PULL from it and commit to the
data-only `sim-history` branch (which never deploys):

- `.github/workflows/error-log.yml` — the error ledger + an instance memory
  snapshot. Fire it (`actions_run_trigger`, ref = the work branch), then
  `git fetch origin sim-history` and read `errors/latest.json` and
  `errors/mem-latest.json`.
- `.github/workflows/nightly-history.yml` — nightly deep-sim history.
- `/healthz` and `/robots.txt` are the only anonymous routes; everything else is
  behind HTTP Basic auth. `X-Sim-Token` (SIM_TOKEN) is the automation door.

## Architecture, briefly

- `app.py` — every route; background jobs are claimed by ONE worker via a
  file lock (`_own_background_jobs`).
- `baseball.py` — MLB slate, game sims, the shared TTL cache, disk-shared
  boards/sims. `mlb_sim.py` is the 4,000-run engine; `deep_season.py` /
  `deep_data.py` / `deep_sim.py` are the pitch-by-pitch season engine.
- `combo_engine.py` — the parlay frontier DP + objective chooser. Leg-count
  depth is demand-driven (`dp_legs`).
- `store.py` — SQLite ledgers (picks, props) and the shared scoreboard math
  (`_pick_stats`). `predlog.py` grades market predictions; `calibrate.py` fits
  the CV-gated calibration.
- `nfl_*.py`, `racing*.py`, `basket.py`, `hockey.py`, `golf.py`, `tennis_*.py`,
  `ufc_sim.py`, `lol.py` — the other sports. `boardshare.py` publishes any
  board so all workers serve one build.
- `artifacts.py` — the schema-gated contract for the three compute stores
  (gamesim / boards / deep). Bump `SCHEMA` with any pickle-shape change.

## The PC offload

The owner's always-on Windows PC runs `pc_loop.py` (git-checks every 60s,
restarts itself on update) which runs `pc_worker.py`: it builds the SAME
artifacts with the SAME functions and uploads anything fresher than the
server's copy through `/api/art/upload`. The server adopts by file freshness
and self-computes whenever the PC is off — **the PC can only add speed or be
ignored**. `vigil-pc.bat` is frozen on purpose (cmd reads .bat by byte offset,
so a self-pulling batch file corrupts itself); all logic lives in Python.

## Hard-won gotchas

- Render is memory-constrained. Anything that caches per-process, or spawns a
  build child, gets measured before it ships. Two "exceeded memory" kills came
  from caches keyed on values that never repeat.
- The slate builds in a **fresh subprocess** every few minutes; anything cached
  only in memory is re-paid every time. Persist to the deep store instead.
- Never let in-game information touch a number that claims to be pre-game
  (entry prices, closing lines, "model vs market" comparisons). That mistake
  produced a fake +14.8c CLV and a fake +47% prop ROI.
- The user reads these numbers as a scoreboard. Honesty beats flattery: fees
  in, one-sided pools labelled, small samples flagged.
