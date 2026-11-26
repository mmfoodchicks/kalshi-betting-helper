# Simulation cache and custom lineup evaluator: design (2026-11-26)

The owner, 2026-11-26: simulation caching combined with custom lineup evaluation
is "the feature I want most. It would allow us to challenge Vigil's conclusions
directly, using the same simulated football games, without repeatedly consuming
your PC's resources or entering additional paid contests." The independent
review's requirements are quoted in each section.

## Where the time goes, and what a cache can remove

The owner's unrestricted Thanksgiving classic build, by stage:

| stage | seconds |
|---|---|
| simulations | 158 |
| candidate generation | 32 |
| field generation | 42 |
| lineup scoring | 1,172 |
| portfolio | 399 |
| total | about 30 minutes |

- **The worlds can be cached.** `nfl_dfs_sim.player_pool` caches the worlds 30 minutes in memory, inside
  ONE process. Each PC build, a picked build included, is a fresh process, so it re-simulates the same
  games. The disk cache (stage 1) removes that stage and the seeded field draw. It also makes a picked
  build read the plain board's worlds exactly, which a controlled forced/excluded comparison needs.
- **The scoring pass cannot be cached this way.** Each world's field is a 300,000-lineup score
  distribution, so the per-world tables would run to tens of GB. Its cost is the GPU benchmark's
  question, not this cache's.

## Stage 1: the worlds cache (PC only)

`pool_cache.py`, consulted by `nfl_dfs_sim.player_pool` when `VIGIL_POOL_CACHE_DIR` is set (the PC
worker sets it) and the model is `engine` (the boards' model). The server never sets it, and the
large matrices never reach Render.

**Key.** sha256 of:
- the call: season, week, n, preseason, teams, model, seed, xside, discrete;
- `CACHE_VERSION`;
- the simulator's code: the source of `nfl_dfs_sim`, `nfl_dfs_engine`, `nfl_deep_sim`, `nfl_dfs_csim`,
  `nfl_usage`, plus the deep tables file;
- the INPUTS: the week's games as Sleeper projected them (usage-adjusted, injury tags included), the
  engine's week context (deep profiles, drive levels, weight), and the defense and kicker projections.

A new projection, a new injury tag or a code change is a new key. Nothing is invalidated by time.

**Entry.** `<dir>/<key>/` holds:
- `manifest.json`: the call; the inputs hash; the code hash; the engine report; every player's
  name, pos, team, opp, proj, ceiling, floor, sim_mean and rec_tgt; the projection snapshot (the games
  JSON); the arrays' dtype, scale, shape and sha256.
- `arrays.npy`: P x n.

**Storage.**
- Scores are stored as int32 hundredths when every value round-trips exactly (checked on write:
  k/100.0 == x for every element), otherwise as float64.
- Captain multipliers are applied downstream on the floats, exactly as now.
- int32 hundredths cannot overflow below +/-21 million points.

**Safety.**
- Writes are atomic: write to `<key>.tmp-<pid>`, then rename.
- Every load checks the arrays' sha256 against the manifest. A mismatch is a miss, ledgered
  `POOLCACHE-integrity`, and the entry is deleted.
- Retention: the newest `KEEP_ENTRIES` entries, capped at `MAX_BYTES` in total, oldest pruned on
  each write.
- `clear()` gives explicit invalidation.
- Loads use `mmap_mode="r"`.

**Contract.** The loaded pool equals the built pool exactly: the same dict, every `arr` a list of the
same floats.

## Stage 2: the field artifact

The classic build's field sample (indices into the board's players, `wf`) and the showdown universe
with its weights are saved beside the board on the PC (`<dir>/field_<board key>.npz`), keyed by the
board's pool signature, seed and engine. They are "compatible opponent-field artifacts": the evaluator
scores against the field the board was built on.

## Stage 3: independent final-evaluation worlds

The review: "Implement and test a three-stage architecture: 1. Candidate generation. 2. Ranking and
portfolio selection. 3. Independent final evaluation."

- **Phase 1 (this sprint, evaluator only).** A slate's final worlds are a SEPARATE `player_pool`
  call: same inputs, `FINAL_N` = 40,000 worlds, seed `child_seed(board seed, "final")`. Cached by
  stage 1. Nothing on the board was chosen on them, so a hand-built lineup and Vigil's own picks are
  compared out of sample.
- **The field on them.** Scored once per slate into per-world thresholds: the field score at each
  contest's top 0.1%, top 1% and cash line, and at a log grid of rank fractions for payouts. Stored
  as `final_<board key>.npz` (W x m float32).
- **The rule.** The evaluator's comparisons read only these worlds. A lineup changed in response to
  them is development data: the result says so on every re-evaluation of the same slate after the
  first (`looks` counter).
- **Phase 2 (registered, not built).** The board itself on three stages: generation, then ranking
  and portfolio on about 120k worlds, then a 40k final read printed beside the ranked figures.
  Its rare-event precision at 40k is to be benchmarked first; a 1% event has an SE of 0.05 points
  there. It changes every board number, so it is an ENGINE bump judged by a rule written first.

## Stage 4: the evaluator

- **Request.** The tab queues an evaluation: the draft group, the lineup by name (and captain), and
  a label. "custom" marks a hand-built lineup; it is never an optimizer one.
- **PC side.** A fast lane polls every 15 s, independent of the long build cycle. It prepares the
  slate's final pack once (stages 1-3), then scores the lineup and Vigil's top lineups on the same
  final worlds:
  - legality: DraftKings' slots and cap, and both teams in a showdown;
  - mean, median, SD, P10/P50/P90/P95/P99;
  - top 0.1%, top 1% and cash rates;
  - payout by the threshold grid (ties not split, so labelled experimental);
  - the lineup's per-player correlation matrix on those worlds;
  - finish percentile against the field per world.
- **Result.** Uploaded as a small JSON (boardshare piece `dfseval_<id>`). It is logged as
  `custom:<label>` in the DFS ledger, so it is graded with the slate.
- **When the PC is off.** The evaluation waits, and the tab says so. Nothing large runs on Render.
