# Projection sensitivity: lineup robustness under uncertain usage, ownership and game scripts (2026-11-26)

The owner (priority 4): "Develop a framework for testing lineup robustness against uncertain player usage,
ownership and game scripts ... A lineup that performs well across several plausible assumptions may be more
robust than one that dominates a single modeled scenario." He asked, by name, what happens when:

- usage projections are off by 10%;
- a running back gets fewer carries than expected;
- a receiver's target share is overestimated;
- a game becomes an unexpected blowout;
- actual ownership differs substantially from projected;
- an alternative opponent-field model is used.

**This is a research framework. No board changes.** The files:

- `dfs_sensitivity.py`: the spec, the transforms, the runner and the statistics.
- `research/dfs_sensitivity_run.py`: the PC harness, plus a synthetic demonstration.
- `tests/guards_auto_sensitivity.py`: guards SN1-SN18, about 16 s in the suite's AUTO section.

Nothing on the server imports any of them, and pc_worker is untouched (section 9 lists what to wire).

## 1. The scenario specification

A scenario is a dict: a name, a list of ops, an optional `weight` and an optional `reseed`. `normalize()` is
strict. An unknown op, key, selector or out-of-range number raises, because a typo that silently did nothing
would read as "this lineup is robust". Each op acts on exactly one layer of the pipeline the board runs:

| layer | ops | what moves | cost on the PC |
|---|---|---|---|
| lines | `share`, `usage_noise`, `efficiency`, `script.att_mult` | Sleeper's lines the engine deals by, `means` AND `recon` | the touched games re-simulated |
| level | `script.d_pts` | the engine's points target for a team | that game re-simulated |
| worlds | `points` | a player's simulated points (mult, add) | none |
| mask | `condition` | which worlds count (margin, total, team points) | none: a subset of the base's worlds |
| field | `field`: `board`, `casual`, `tilt`, `sd_top_share` | the opponents' lineups or weights | the field re-scored |

An example scenario, "this back's carries 25% short, his backfield mates take them":

```json
{"name": "rb_carries_down:X", "ops": [{"kind": "share", "comp": "rush_att", "mult": 0.75, "absorb": "position",
  "select": {"names": ["X"], "team": "KC"}}]}
```

Selectors combine `names`, `team`/`teams`, `pos` and `role` (QB1, RB1, RB2, WR1-3, TE1, ranked by the
engine's line). A name resolves exactly first. It falls back to `nfl_adp._norm` only when no player carries
it exactly. The guards found that the normalised form drops numerals: "WR10" became "wr", and a points op
zeroed every receiver on the slate (SN9).

**What each op conserves, and how it is checked.**

- **`share` and `usage_noise` move shares only.**
  - Each team's line totals of targets and carries are unchanged in `means` and in `recon`, to 1e-9. The
    engine deals by `recon` where present; the means-only version of this op would have done nothing.
  - Every carried stat moves by the same factor, so catch rate, yards per touch and TDs per touch are kept,
    as `nfl_usage` does.
  - `absorb: position` hands a back's lost carries to the other backs only. `absorb: team` spreads them
    over the team.
  - Checked by SN2 and SN3. Each receipt also stamps `conserved`.
- **Under the engine the team's volume is the GAME's.** `deal_team` splits the box's totals by these shares.
  - So a share op leaves every world's box score untouched: the same games, the ball dealt differently.
  - SN5 checks the production deal on 20,000 worlds: carries, yards, TDs, receptions and receiving yards sum
    to the box in every world.
  - SN6b runs it end to end through `player_pool`: all four teams' points are equal world for world, the
    back loses points and his backup gains them.
- **`script.d_pts` moves a team's target by exactly d.** The drive level moves by d / (1 - deep weight),
  checked through `nfl_dfs_engine.blend_targets` (SN4). On the real engine the anchor's reported target moved
  +7.00 / -7.00 (SN6c), and simulated points landed +6.8 / -8.3.
  - The passing-volume anchor stays at Sleeper's attempts unless `att_mult` is given. A shifted blowout
    therefore throws its projected volume.
  - The `condition` version is the one to read for "the game turned into a blowout". It conditions the base
    worlds, where the engine's own clock and script already reacted to the score.
- **Neither `points` nor `field` is conserved, by design.** `points` is a stress: a late scratch nobody
  replaces, or a mean shift nobody absorbs. The field is the public's, not the game's.
- **Sleeper's `proj_pts` never moves.** A scenario is the truth differing from the projection, so the field
  the board scored against is still the field.

**Field alternatives** (`field_for`):

- **`casual`** redraws the calibrated softmax field the optimizer field replaced. It uses
  `dfs_tourney.calibrate_field` and `FieldSampler` on the board's own players. Classic only.
- **`tilt`** re-weights the board's own field (maximum entropy: the row weights times exp of the sum of
  per-player multipliers) until each player's ownership reaches a target. Each position's total is kept,
  and the stacks, salaries and sampler rules are untouched. The targets:
  - `concentrate`: ownership to the power gamma within each position, gamma bisected to a stated sum of
    own^2. The real classic fields sit at 1.5-1.65, the served one at 0.87 (`dk_mech.md`); the preset uses
    1.5.
  - `noise`: lognormal ownership error sized to a stated top-40 MAE. The preset uses 5.5 points, the
    measured error of our field against the real Millionaires (5.14-6.27).
  - `scale`: the top k players times m.
  - `casual`: the board's stamped `casual_pct`.
  - Each receipt carries the reach (to 0.02 points) and the Kish effective sample size it cost (SN7).
- **`sd_top_share`** re-weights a showdown universe at another duplication knob. The 0.468% that matched
  Sunday's real collision is in `sd_dup.md` (SN7b).

## 2. The runner

`Runner(meta, field, lineups, simulate)` works from the evaluator artifact: the board's players in its
order, the contests with their full payout tables, and the field it scored against. The steps:

1. **The worlds.** `EngineWorlds` patches the scenario's lines, context and a capturing `simulate_game` into
   `nfl_dfs_sim.player_pool`, in this process only, restored after each call (SN6a). It calls one game at a
   time.
   - `pool_cache` therefore keys each game on that game's own inputs, and the drive levels are narrowed to
     the game's teams.
   - A scenario that touches one game re-simulates one game; the rest are cache hits, array for array (SN6b).
   - Each side's box-score points ride out as pseudo-players and are filed with the worlds. Conditions read
     them on a cache hit too.
2. **Its own cache directory** (`pc-simcache/sensitivity/poolcache`, 800 entries / 20 GB). A run is many
   per-game entries, and the boards' cache keeps 24.
3. **Its own seed** (`sens_seed`). It is neither the board's worlds, which are unseeded and chose the
   lineups, nor the evaluator's final worlds. A run spends neither the board's selection worlds nor the
   evaluator's held-out ones.
   - Every scenario uses the same seed, so the random numbers are common: differences are paired world for
     world.
4. **The scoring is dfs_evaluate's.**
   - `field_thresholds` gives the field's per-world rank thresholds, on the CPU or the GPU verified per job.
   - Each lineup is bucketed exactly as `score_lineup` buckets it.
   - Every figure equals `score_lineup` and an independent brute force (SN8).
5. **Block sums.** Worlds are cut into 40 blocks, and each metric's sum is kept per (scenario, lineup, block).
   Every statistic below is computed from those sums, so any two scenarios can be differenced afterwards
   without re-simulating.

The lineup set is every list on the board, each lineup once with every list it came from: the top lists
(20 each), the portfolio and the chalk. Custom lineups come from `--lineups file.json`. DraftKings legality
is checked by `check_lineup`.

## 3. The robustness metrics

The metrics are for the primary contest. Every contest's sums are kept.

- **Per scenario and lineup:** P(top 0.1%), P(top 1%), P(cash), and the no-tie EV. The EV is experimental,
  as the evaluator's is, because ties are not split. Each carries a delete-one-block jackknife SE.
- **The change from the base:** the paired difference d, its paired jackknife SE, and z. Both sides delete
  the same block, so shared worlds cancel.
  - Calibrated: over 300 replications the spread of d matched the reported SE (ratio 0.97). Through the
    Runner, 16 replications gave 0.98 (SN13).
  - The jackknife SE alone matches the binomial SE to within 10% RMS (SN12).
- **Rank stability:** Spearman and Kendall tau-b of the lineup ordering against the base.
  - Under common random numbers the full-sample correlation is inflated by shared noise. The figures to read
    are the split-half pair: the **noise floor** (the base's even blocks against its odd blocks) beside the
    **cross** (base half against scenario half, averaged over 16 random splits).
  - A cross near the floor means no reordering beyond Monte Carlo noise.
- **Regret** against each scenario's best lineup:
  - in sample, with its paired SE;
  - **cross-fitted**: the best chosen on one half and measured on the other, which the winner's curse cannot
    inflate.
- **Across scenarios, per lineup** (weighted scenarios only):
  - min, max and SD of each figure;
  - worst-case regret and the scenario that caused it;
  - mean regret;
  - the share of scenarios in which the lineup stays in the top N (10 by default);
  - rank by the mean across scenarios, and rank by the worst case;
  - the largest |z| and its scenario.
- **Weight 0 marks a diagnostic.** The `noise_reference` gets its own row but never votes on robustness
  (SN17).
- **The seed check** (`seed_check`): the noise reference's mean z^2. Section 5 explains why it exceeds 1.
- **Worlds needed.** For a probability p at target SE e, W = p(1 - p) / e^2:

| figure | SE wanted | worlds |
|---|---|---|
| P(top 1%) = 1% | 0.1 point | 9,900 |
| P(top 1%) = 2% | 0.1 point | 19,600 |
| P(top 1%) = 5% | 0.1 point | 47,500 |
| P(cash) = 20% | 0.5 point | 6,400 |

  For a paired difference, the worlds at which the observed d sits at 2 SE are 4 var(d) / d^2. Both are on
  every scenario row (`worlds_to_detect_median`, `worlds_needed`).

## 4. Validation (guards SN1-SN18)

| guard | known answer |
|---|---|
| SN1 | the spec refuses 11 malformed scenarios; signatures say which scenarios share worlds |
| SN2-SN3 | conservation in both lines; a share moves by exactly the multiplier; noise is reproducible and of the stated size |
| SN4 | the script shift is exact through `blend_targets`; `att_mult` is exact through `sleeper_attempts` |
| SN5 | the production deal conserves the box in every world under a shifted line |
| SN6a-c | the real engine through `player_pool` and `pool_cache`: a repeat is a cache hit; a share change re-simulates one game with the box identical world for world; a script change moves the target by exactly 7 |
| SN7, SN7b | tilts reach their targets with each position's total kept; the identity is exact; the showdown knob works |
| SN8 | the scorer equals `score_lineup` and a brute force |
| SN9 | **zero a player**: every lineup holding him drops out of the top 5 (all five had been the top 5), each scores exactly its base minus his points, and every other lineup is unchanged to the bit (paired d and SE exactly 0) |
| SN10 | **identical scenarios give identical results**: one simulate call, sums equal to the bit, d = SE = 0, Spearman 1 |
| SN11 | complementary conditions partition every block of every figure; an always-true condition is the base |
| SN12-SN13 | the jackknife against the binomial; the rank correlations against brute force; the paired SE's calibration |
| SN14-SN18 | hygiene (no server import, no silent except, its own seed and cache), the presets, the estimate, the outputs, weight 0, the anchor size in the seed |

Mistakes the guards caught on the way:

- **The digit-dropping name match** (SN9).
- **A numpy rounding difference.** `round()` on a numpy float64 rounds the scaled value half-to-even
  (22.075 to 22.08); `score_lineup` rounds a Python float (22.07). The guard compares as `score_lineup` does.
- **Too few blocks.** A smoke run cut 3,000 worlds into 3 blocks of 1,000. The noise reference then put 4 of
  40 lineups beyond 3 SE, because an SE with 2 degrees of freedom is not one. Blocks are now 1/40 of the
  run's worlds (`NBLOCKS`).

## 5. A finding: each run's anchor is a random effect no world count removes

The demonstration's noise reference (the base on another seed) moved **20 of 27** lineups beyond 2 SE on
P(top 1%), mean z^2 11.3. On P(cash) it moved 26 of 27, mean z^2 65. Independent worlds alone would give
about 1 lineup and a mean z^2 of 1.

The cause is the engine's anchor. Each game's offences are levelled on `ANCHOR_N` measuring games (800, two
corrective passes). That is ONE draw a run, shared by all its worlds.

- **Measured at production settings** (a synthetic league-average game, 8 seeds, 4,000 worlds each): a
  team's mean points moved **0.34-0.41 across seeds**, against a world-level SE of 0.16. At 40,000 worlds the
  world-level SE is 0.05, so the anchor term is about 7x larger. This matches the engine's own docstring
  ("within about +/-0.35").
- **The demonstration ran a 300-game anchor**, and its teams moved up to 2.2 points between seeds.

Implications:

- **Paired comparisons within a run are unaffected.** Share, usage, field, points and condition scenarios
  leave the anchor untouched (SN6b: the box is identical), and their SEs are calibrated for "given this
  run's anchor" (SN13).
- **Absolute figures carry this extra variance.** That holds for the board's own and for the evaluator's
  final worlds, and more worlds do not shrink it. `seed_check` reports it on every run with a reseed.
  `EngineWorlds(anchor_n=)` (`--anchor-n`) shrinks it by sqrt of the anchor size; the size is mixed into the
  seed, so the cache cannot confuse two sizes (SN18).
- **Hypothesis, per CLAUDE.md:** this is measured on made-up lines and league-average profiles, not a real
  slate. The PC run measures it on a real board through its noise reference.
  - If it holds, the board's top-list order is partly an artifact of one anchor draw.
  - `cl_worlds.md`'s world-count comparisons share one anchor per replicate.
  - An `ANCHOR_N` review is the engine owner's call. Nothing here changes it.

## 6. The synthetic demonstration (mechanics only, never a slate's numbers)

`python research/dfs_sensitivity_run.py --synthetic` ran a made-up 4-game slate through the real engine:

- the lines and salaries are invented;
- league-average deep profiles;
- the production sampler's calibrated field of 20,000;
- 27 "board" lineups chosen on 4,000 generation worlds;
- 21 scenarios on 8,000 independent worlds;
- 394 s on 2 cores.

What it shows about the method, at P(top 1%) unless noted:

| scenario | lineups beyond 2 SE | noise floor / cross (split Spearman) | paired SE / single SE |
|---|---|---|---|
| noise_reference (another seed) | 20 / 27 | 0.76 / 0.47 | 1.30 |
| one back's carries -25% | 22 | 0.76 / 0.50 | 0.69 |
| every WR1's targets -15% | 14 | 0.76 / 0.50 | 1.30 |
| one receiver's usage -10% | 5 | 0.76 / 0.74 | 1.06 |
| casual field redrawn (the demo's field IS casual: a resample) | 4 | 0.76 / 0.77 | 0.28 |
| ownership error of the measured size | 16 | 0.76 / 0.71 | 0.57 |
| ownership moved to sum own^2 1.5: from 1.72, so a FLATTENING on this 4-game pool (a real board's served 0.87 rises) | 27 | 0.76 / 0.77 | 0.50 |
| the favourite by 17+ (12.9% of worlds kept) | 26 | 0.76 / 0.20 | 1.13 |
| a receiver scratched | 27 | 0.76 / 0.46 | 2.14 |

How to read it:

- **Pairing pays where the worlds are shared**: field and points scenarios run at 0.28-0.57x a single
  figure's SE.
- **Moving the ownership concentration moves every lineup's figures** (27 of 27 beyond 2 SE) **but not
  their order** (cross 0.77 against a floor of 0.76). Here it flattened the field; on a real board it
  concentrates it.
- **A one-receiver 10% usage error** moves a few lineups and reorders nothing beyond noise.
- **Conditioned game scripts reorder the list completely** (cross 0.20).
- **Base P(top 1%) ran 1.1-3.9%.** Pinning it to 0.1 point needs a median of 23,000 worlds (37,000 max), and
  0.05 point needs 93,000.

The robust ranking moved too. The base's #1 stayed first by the mean across scenarios but fell to 3rd by the
worst case. The base's #5 was 3rd by the mean and 26th by the worst case.

## 7. The PC run design

```
cd /d "D:\Games\Kalshi Betting\kalshi-betting-helper"
python research\dfs_sensitivity_run.py --dg <draft group> --dry-run        (the scenarios and the estimate)
python research\dfs_sensitivity_run.py --dg <draft group> --workers 12 --gpu
```

Run it in a second Command Prompt, never in the worker's window. It needs the board's evaluator artifact
and the board itself on the PC's disk, the same build. A board built before 2026-11-26 has no artifact:
rebuild it first.

**Scenarios (the standard preset: 34 on a classic board).**

| scenario | count |
|---|---|
| base | 1 |
| noise_reference (weight 0) | 1 |
| usage noise, SD 10% | 4 seeds |
| carries -25%, the 3 most-used backs (absorb position) | 3 |
| every RB1 -20% | 1 |
| targets -20%, the 3 most-used receivers | 3 |
| every WR1 -15% | 1 |
| usage +/-10% for the 2 most-used skill players | 4 |
| for each of the 2 most-exposed games: favourite by 17+, underdog by 7+, total 60+, total 34 or fewer | 8 |
| that game's line 14 points off (script shift +7 / -7) | 1 |
| casual field | 1 |
| ownership concentrated to 1.5 | 1 |
| ownership error of the measured size | 3 seeds |
| top-10 owned x1.5 | 1 |
| the most-used player scratched | 1 |

On a showdown board the casual field is replaced by the duplication-matched knob. Exposure is read off the
board's own lineups.

**Worlds: 40,000 a scenario.** That puts P(top 1%) at 2% to SE 0.07 points. The paired differences are
tighter where the worlds are shared. The anchor term of section 5 sits on top of every absolute figure.

**Runtime** (`estimate()`; 13 games, field 300,000, 12 workers):

- Each game simulated costs ~11.4 s (2.4 s of anchor plus 40,000 worlds over 8 processes). The run needs 115
  game-simulations: **22 min**.
- The field's thresholds are computed once per distinct (worlds, points, field), 26 times: **89 min on the
  CPU, 11 min on the GPU** (the owner's measured 102.5 s / 13.1 s per 20,000 x 300,000).
- **Total: about 1 h 52 min on the CPU, about 35 min with `--gpu`.**
- A showdown universe of up to 900,000 rows makes the thresholds 3x heavier: about 5 h on the CPU and 42 min
  on the GPU. Use `--gpu`, or `--worlds 20000`.
- Peak memory is about 2 GB. The worlds cache takes about 2 GB of disk.

**Output**, in `pc-simcache/sensitivity/<dg>/<artifact>-<UTC>/`:

| file | contents |
|---|---|
| `summary.json` | every figure, every receipt |
| `share.json` | the digest: per scenario the counts beyond 2 / 3 SE, rank correlations beside the noise floor, the seed check; the 20 most robust lineups by name; under 200 KB |
| `blocks.npz` | the block sums for re-analysis |
| `digest.md` | the tables, readable |

A run holds no entrant data: the field is the board's model sample, and the lineups are the board's and the
owner's.

## 8. Limitations

- **EV does not split ties**, so duplication's main channel, shared prizes, is not measured. Concentration
  enters through ownership only. The duplication mixtures failed their criteria (`dk_mech.md`), and no
  cluster is invented here.
- **Field scenarios are stresses, not validated models.** Their magnitudes are measured; their shapes are
  not.
  - The tilt keeps the board's field's structure. A field that is structurally different, like the real
    one's 15-29-point strength gap, is out of its reach.
  - The concentrate tilt's effective sample size fell to 26% in the demonstration.
- **Script shifts keep Sleeper's passing volume.** Read the conditioned scenarios for "the game turned
  into" questions; they keep fewer worlds (13-27% of 40,000) and say so.
- **A role-wide or noise op re-simulates every game.** The deal's own noise then decorrelates the pairing
  (paired SE 1.15-1.3x a single figure's). Its z-scores stay valid; they are just wider.
- **In-sample regret is biased upward** (the winner's curse). Read the cross-fitted column.

## 9. What the orchestrator should wire later

None of this was done here: pc_worker and pc_loop belong to another workstream.

1. **A pc_worker task** (lowest priority, idle only, never within 3 h of an NFL slate's start, like the GPU
   bench). It runs `run_board` on the newest Millionaire and $500K+ showdown boards once each per board
   build, then uploads `share.json` through the token door to a new `/api/dfs/sensitivity` (last N, small
   JSON on the server).
2. **error-log.yml** pulls it to `errors/dfs-sensitivity-latest.json` on sim-history, beside
   `pc-bench-latest.json`.
3. **Optionally, the DFS tab** shows the robust ranking and the seed check beside the board's lists, once
   the owner has read a real run.

## 10. For the owner's decision

- **Whether a robustness column belongs on the board at all.** Read a real run's `share.json` first.
- **Whether to review the engine's `ANCHOR_N`** in light of section 5. It affects every board's absolute
  figures, not this framework's paired comparisons.
- **The scenario weights.** They are all equal by default. Some conditioned scenarios are rarer (13% of
  worlds) than they are likely, and the owner may prefer weights by how plausible each assumption is.
