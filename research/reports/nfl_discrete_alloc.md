# Integer football statistics: where the week board goes continuous, and a discrete alternative

2026-11-27. The owner: "investigate why the player simulator generates continuous reception counts and
other naturally integer-valued football statistics ... evaluate an experimental discrete-stat allocation
mechanism ... preserve the production engine until any alternative passes validation", and: "distinguish
between continuous underlying talent estimates, which are perfectly reasonable, and simulated realized
football statistics, which should generally follow the sport's actual scoring rules."

Code: `nfl_discrete_alloc.py` (the experiment; nothing in the app imports it),
`research/nfl_discrete_alloc_eval.py` (the comparison), `research/data/nfl_discrete_alloc_eval.json` (its
output, every figure below), guards `tests/guards_auto_discrete_alloc.py` (DA1-DA5).

## 1. Bottom line

- The week board's realized receptions, catches' yards and rushing yards are **not event counts**: they
  are the player's Sleeper mean multiplied by shared random factors. That is the talent estimate wearing a
  random scale, not a simulated game. Touchdowns, interceptions and fumbles ARE integer draws.
- Made discrete (targets, catches, carries and per-event yards drawn as whole events on the same latents),
  the realized statistics follow the box-score rules, the quarterback's line equals his receivers' sums in
  every world, the player means are unchanged, and on 257 real games (2025 weeks 1-12, 2026 weeks 1-5)
  the discrete path is much better calibrated for receptions, receiving and rushing yards and DraftKings
  points.
- It is **not ready for production**. Its quarterback passing yards are twice as spread as the real ones,
  its quarterback means run 2.7% (yards) and 4.4% (TDs) high, its yardage-bonus rates run high, and its
  shapes are stated, not fitted. It stays an experiment; the production engine is unchanged (a guard pins
  it byte for byte).

## 2. Where each statistic becomes what it is

### The week board (`nfl_dfs_sim.simulate_game`, the legacy latent model; the NFL Pick 6 view, the ledger)

| Statistic | What it is | Where |
|---|---|---|
| receptions, receiving yards | **continuous**: Sleeper mean x `rec_f = env * ql * (1 - _SCRIPT_PASS * sc)` | lines 629, 632 |
| passing yards | **continuous**: mean x `pass_f` (the same latents) | line 631 |
| rushing yards | **continuous**: mean x `rush_f = env * (1 + _SCRIPT_RUSH * sc) * max(0, gauss(1, _RUSH_SD))` | lines 628, 631 |
| passing / rushing / receiving TDs | **integer** Poisson draws on latent-scaled means, drawn independently of the yards | line 639 on |
| interceptions, lost fumbles | **integer** Poisson draws, not scaled by any latent | lines 642-643 |
| attempts, completions, targets, carries | **not simulated** (weekly_games carries the means; the loop never reads them) | lines 122-128 |
| `discrete=True` (showdown experiment only) | **an approximation**: the scaled continuous value rounded, `int(x + 0.5)` | line 635 |
| the board's point array `arr` | the raw points **rescaled** by proj/raw to pin the mean to Sleeper | line 689 |

So a "4.37 catches" world is a scale of the mean, not a count: no target or catch is ever drawn, a
receiver cannot have 0 catches unless a latent hits 0, and the quarterback's yards are scaled from his own
mean rather than summed from his receivers' (97.28% of team-worlds differ by more than half a yard; mean
gap 13.34 yards; the touchdowns differ in 74.24%).

### The DFS engine's worlds (`nfl_dfs_engine`, the tournament boards)

| Statistic | What it is | Where |
|---|---|---|
| team attempts, completions, yards, TDs | **real simulated event counts**: the deep engine plays whole plays, yards drawn as whole numbers from measured histograms (`nfl_deep_sim` line 205), team multipliers by unbiased rounding (`_scale`, line 142) | `nfl_deep_sim` |
| player catches, targets, carries, TDs, INTs, fumbles | **integer allocations** of the team counts: multinomials by usage share x noise | `deal_team` lines 388-454 |
| player yards | **an integer allocation of a real team total**: team yards split by continuous weights (catches x yards per catch x noise) by largest remainder, so every player's yards are whole and sum to the team's exactly | `_split_w`, lines 314-334, 396, 439 |

The engine's realized statistics are already integers that obey conservation; its per-player yardage is a
weighted split of a real total, not a per-catch draw.

The constrained simulator (`nfl_dfs_csim`, Stage 2D/2E's alternate) draws targets, catches and carries as
multinomial counts but **rounds** a continuous yard product (`np.rint(rec * ypr * eff * big)`, lines 335,
363): counts are events, yards are an approximation.

### Where fractional realized statistics reach a figure the owner sees

The owner read the DEN @ PIT showdown page as showing that Showdown already scores integer stat lines, and
that the continuous issue may sit on another path. **Confirmed, with one qualification:**

| Surface | Simulator | Realized statistics |
|---|---|---|
| Tournament boards, classic and showdown (the PC; DEN @ PIT) | `nfl_dfs_engine` (`dfs_tourney.BOARD_MODEL = "engine"`, `board_model`; `player_pool(model=...)`, dfs_tourney lines 1755, 3915) | **integer events**: team counts from whole plays, dealt to players as integers; player yards a whole-yard split of the real team total |
| ... a game the engine has no deep profile for (ledgered `NFLENG-fallback`), and every preseason board | legacy latents with `discrete=True` (nfl_dfs_sim line 1266; `board_model` returns legacy in the preseason) | **integers by rounding** the continuous scaled value (`int(x + 0.5)`), an approximation, with the mean pinned upstream |
| The showdown lineup SHEET (the tab's Build) | legacy latents, `discrete=nfl_dfs_sim.SD_DISCRETE` (True; nfl_dfs line 2447) | **integers by rounding** -- what CLAUDE.md calls "legacy-discrete" is this: whole-number lines rounded from continuous draws, not event counts |
| The classic lineup sheet | legacy latents, `discrete=False` (nfl_dfs line 2693) | **continuous**, points rescaled to Sleeper's projection (off DraftKings' lattice) |
| The NFL tab's week board: Sim cards, Best ball ceilings, **Pick 6 view**, the **ledger's figures and whole-line ties** | legacy latents (`_build_board` never passes `discrete`) | **continuous** -- the path studied here |
| The constrained alternate (`model="constrained"`, not the default) | `nfl_dfs_csim` | catches and carries are counts; yards **rounded** (`np.rint`) |

So the fractional realized statistics reach the owner on the week board (its Pick 6 leans, the ledger's
Vigil figures and modeled ties, the Sim and Best ball cards) and the classic lineup sheet; the showdown sheet
and the engine's fallback games show whole numbers that were rounded, not counted; the tournament boards
(the engine) deal real integer events.

## 3. The experiment (`nfl_discrete_alloc.simulate_game`)

Same inputs as the week board (weekly_games' game dict), **the legacy latents drawn the same way** (so its
correlation structure starts where the board's does and its means are the board's means in expectation),
and every realized statistic an event count:

- targets Poisson(target mean x the team's passing factor) per receiver (equivalent in law to a Poisson team
  total dealt multinomially by target share), catches Binomial(targets, catch rate), receiving yards one
  integer draw **per catch** (gamma-shaped yards per catch, shape 1.5, turned into an integer by unbiased
  random rounding, so the mean is exact), receiving TDs Binomial(catches, TD rate per catch);
- the quarterback's completions, yards and TDs are the sums over his receivers' catches plus an "other
  receivers" bucket for the part of his projection Sleeper assigns to nobody listed; attempts are targets
  plus untargeted throws (nfl_recon's 0.950 target factor);
- carries Poisson(carry mean x the rusher's legacy factor), rushing yards one integer draw per carry (a
  gamma shifted to allow losses to -3, shape 1.2), rushing TDs Binomial(carries, TD rate per carry);
- interceptions and fumbles: the legacy Poisson draws.

The shapes (1.5, 1.2, -3) are stated, not fitted.

## 4. The comparison

257 games with both teams projected: 2025 weeks 1-12 and 2026 weeks 1-5 (all the 2026 table holds;
**2026 week 13 on is sealed for the coaching tests and never read** -- the harness asserts it), Sleeper's
weekly projection as the input and Sleeper's box as the outcome (`research/data/player_weeks_*.csv.gz`).
2,000 worlds a game a path, fixed seeds. Nothing is fitted, so this is a development comparison, not a
registered test. "Realized over model" below is the
**realized squared error of the box score around the model's mean, over the model's variance**,
pooled over player-weeks: 1 is a calibrated spread, above 1 too narrow, below 1 too wide.

| | Legacy (production) | Discrete (experiment) |
|---|---|---|
| QB = sum of receivers (completions, yards, TDs), worlds broken | 97.28% / 74.24% (yards / TDs) | 0 |
| receptions: mean ratio (discrete / legacy, 95% CI) | 1 | 0.9999 (0.9988-1.0008) |
| receiving yards: mean ratio | 1 | 0.9996 (0.9983-1.0007) |
| rushing yards: mean ratio | 1 | 1.0001 (0.9991-1.0012) |
| passing yards / TDs: mean ratio | 1 | 1.0269 / 1.0442 (the other bucket where receivers' projections overrun the QB's) |
| receptions: realized over model variance | 3.403 | 0.963 |
| receiving yards: realized over model | 4.386 | 0.886 |
| rushing yards: realized over model | 3.377 | 0.914 |
| passing yards: realized over model | 0.88 | 0.473 (twice too wide) |
| half-line log loss, receptions (21,748 lines) | 0.66763 | 0.48597 |
| half-line log loss, receiving yards (26,694) | 0.70246 | 0.47952 |
| half-line log loss, rushing yards (12,102) | 0.60242 | 0.45841 |
| half-line log loss, passing yards (4,617) | 0.49407 | 0.50981 |
| log score of the realized catch count (3,299 player-weeks) | -2.6539 (mass within half a catch) | -1.9418 (exact count) |
| DraftKings points: PIT Kolmogorov distance | 0.3022 | 0.0176 |
| DraftKings points: actual inside the 10-90% band (ideal 80%) | 41.9% | 83.5% |
| floor: actual under the 10th percentile (ideal 10%) | 38.7% (mean q10 3.80) | 5.2% (mean q10 1.92) |
| ceiling: actual over the 90th percentile (ideal 10%) | 19.4% (mean q90 12.56) | 11.3% (mean q90 15.36) |
| worlds on DraftKings' 0.02-point lattice | 0% | 100% |

The lines are half-points chosen off the projection only (never a model output), so both paths are scored
on the same lines. Lines of one player-week are correlated, so the log losses are not independent samples.

### The owner's specific questions

**Touchdown-reception correlation.** Within a receiver's worlds, corr(receptions, receiving TDs): legacy
0.151 (the TDs share only the latents with the catches), discrete 0.32 (a TD is a catch), observed 0.287
(corr of actual minus projection, 2,164 player-weeks). Fractional receptions understate it by half.

**Yardage bonuses** (DraftKings +3 at 100 rushing / receiving and 300 passing yards; both paths score
`>= threshold` per world, the legacy one on the continuous value):

| bonus | observed rate | legacy rate (Brier) | discrete rate (Brier) | legacy mass in [T - 0.5, T) |
|---|---|---|---|---|
| 100 receiving (1,367 player-weeks) | 10.17% | 5.27% (0.0852) | 13.29% (0.0835) | 0.13% |
| 100 rushing (699) | 12.30% | 8.74% (0.1055) | 15.37% (0.1042) | 0.19% |
| 300 passing (513) | 13.45% | 17.36% (0.1162) | 23.98% (0.1270) | 0.13% |

The 99.6-vs-100 question is small: the continuous mass a whole-yard box would round up into the bonus is
0.13-0.19% of worlds. The bonus error comes from the spread, not the rounding: the legacy board puts half
the real rate on the 100-yard receiving bonus, the discrete path a third too much; both overstate the
300-yard passing bonus, the discrete path badly (its quarterback yards are too wide).

**Exact fantasy-point ties.** Two same-team pass catchers score exactly the same DraftKings points in
9.24% of discrete worlds, 0.001% of legacy worlds (both at 0), and 7.49% of real pairs (10,797). Continuous
statistics make an exact tie practically impossible; real box scores tie often.

**Same-game correlations** (DraftKings points, mean over pairs; observed = corr of actual minus projection):

| pair | legacy | discrete | observed (n) |
|---|---|---|---|
| QB-WR same team | 0.419 | 0.412 | 0.272 (2,357) |
| QB-TE same team | 0.374 | 0.342 | 0.205 (1,398) |
| QB-RB same team | 0.247 | 0.199 | 0.060 (1,459) |
| WR-WR same team | 0.334 | 0.116 | -0.022 (4,380) |
| TE-WR same team | 0.310 | 0.110 | -0.003 (6,417) |
| RB-WR same team | 0.183 | 0.070 | -0.009 (6,713) |
| RB-RB same team | 0.148 | 0.063 | -0.057 (1,429) |
| QB-QB opponents | 0.183 | 0.116 | 0.239 (256) |
| RB-RB opponents | 0.105 | 0.049 | -0.047 (2,083) |

Largest difference between the paths 0.218 (TE-WR). Discrete count noise lowers the teammate correlations
the shared latents inflate, toward the observed values; both paths keep the quarterback-receiver
correlation too high (0.41-0.42 against 0.27) and the discrete path lowers the opposing quarterbacks'
further from the observed 0.24. Neither path has target competition (one receiver's targets taken from
another), which is what drives the observed teammate correlations negative.

## 5. What a tie means under each path

- **Legacy (production)**: no world ever lands on a whole number. The ledger's whole-number More / tie /
  Less (`nfl_dfs_sim.hist_mtl`) counts a "tie" as the mass within half a unit of the line, a world rounded to
  the nearest yard or catch. That is a **rounding convention**, not a simulated event, and the ledger and the
  card now say so in those words ("tie by the rounding convention; modeled").
- **Discrete (experiment)**: receptions and yards are counts, so a tie is a world that lands on the line:
  More / tie / Less are exact model probabilities (`nfl_discrete_alloc.mtl`). They remain model
  probabilities, not calibrated real-world rates.

## 6. What would have to pass before any board reads it

A registered test, written before the numbers, on games not read here (the 2026 games from week 13 on
become available only under the coaching tests' terms), judged on the line and DraftKings-point scores
above. Before registering, the known failures need fixing on development data: the quarterback passing
variance (the per-catch noise is added on top of latents sized for a model without it -- the latents'
share of the variance would have to shrink), the other-bucket means, and target competition. The fitted
constrained simulator (`nfl_dfs_csim`, Stage 2E: promoted as an alternate after beating the legacy model
on CRPS, pair correlations and PIT on 2025) is the nearer candidate for the week board; its yards would
need the per-event draws here instead of `np.rint`.

## 7. Changes made with this study

- `nfl_discrete_alloc.py`, `research/nfl_discrete_alloc_eval.py`, this report, the JSON, guards DA1-DA5.
- Wording: the ledger's whole-line source text and the card now name the tie as the continuous sim's mass
  within half a unit, "a rounding convention, not a simulated count"; the model rule says the half-line
  figures are exact "as the board's own worlds count it -- a model probability, not a calibrated one".
- Production: unchanged. `nfl_dfs_sim.py` is untouched by this commit, and guard DA4 pins the legacy
  simulator's seeded output byte for byte.
