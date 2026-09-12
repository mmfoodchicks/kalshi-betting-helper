# DFS tournament engine: the audit of stages 2B through 4

Written 2026-09-11, 02:00 UTC (2026-09-10, 8:00 PM MDT). Branch
`claude/kalshi-crypto-predictor-ckutwm`. Everything below is measured, and
where something could not be measured the report says so rather than
rounding the claim up. Read sections I, K, N and T first: they are the ones
that change what the board should be trusted for.

---

## A. The commit chain

| Commit | UTC | Stage |
|---|---|---|
| `25a996a` | 09-10 19:41 | 2A, the baseline this audit starts from (already reviewed) |
| `395c3af` | 09-10 20:43 | 2B, the historical evidence base |
| `3006cc9` | 09-10 21:13 | 2C, reconciling the component means |
| `fc10b3e` | 09-10 21:34 | 2D, the constrained team simulator as an alternate mode |
| `448ea9c` | 09-10 22:46 | 2E, the fit on 2022-2024 and the blind 2025 validation |
| `9b39e8c` | 09-10 22:47 | 3B and 3D, tie-aware payout and the money gate |
| `e054862` | 09-11 02:01 | 2F, 3A, 3C, 4 and this report |
| `1366221` | 09-11 02:19 | the log-factorial table off module scope, and the guard that pins the numpy-free import surface |
| `ee4f5a4` | 09-11 02:42 | the correction in limitation 9 |
| `59e563b` | 09-11 03:52 | limitation 8 closed by the PC's engine-5 rebuild |
| `b3d99c5` | | the correction pass: probe identity, the shared-field cover, the simulator's lattice and touchdown books, research provenance, and the exact-path duplication probe |

## B. What changed, by file

Production code:

- `nfl_recon.py` (new). Makes four team identities hold on Sleeper's
  component means before anything is simulated: receivers' targets against
  the quarterback's attempts times a measured 0.950, receptions against
  completions, receiving yards against passing yards, receiving touchdowns
  against passing touchdowns. Weighted least change, bounded, hashed
  weights fitted on the training seasons.
- `nfl_dfs_csim.py` (new). The constrained team simulator: a world is built
  from a shared pace, each team's volume and script, noisy target shares,
  catch rates on a team accuracy day, yards on a team efficiency day with
  per-player big plays, team touchdowns allocated among the men who caught
  the ball, then rushing, then DraftKings scoring. The quarterback's line is
  his receivers' sum in every world; nothing is rescaled afterwards.
- `nfl_dfs_sim.py`. `weekly_games` now carries attempts, completions,
  targets and carries and attaches the reconciliation; `player_pool` takes
  `model` and `seed`, keys its cache on both, and routes the constrained
  model's defenses and kickers through a seeded generator. The legacy model's
  behaviour is unchanged.
- `dfs_tourney.py`. `cum_prize`, `tie_payout`, a two-dimensional
  `payout_grid` over (mass above, mass level), an exact Poisson head where
  the count of entries above has a small mean, `money_gate`, engine 5, score
  buckets of 0.01 points, `RB_STACK_TARGETS`, and `simulator_stamp`.
- `static/app.js`, `static/sw.js`. The outright-win and tie-aware payout
  columns, with the old keys read as a fallback so an engine-4 board stays
  readable in the window between the deploy and the PC's rebuild. Shell v128.

Research (all of `research/`, none of it imported by the server):
`seeds.py`, `hist_data.py`, `hist_stats.py`, `csim_fit.py`, and one module
per stage (`stage2b`, `stage2c`, `stage2d`, `stage2e`, `stage2e_validate`,
`stage2f`, `tails`, `stage4`), each writing a JSON artifact to
`research/data/` and a report to `research/reports/`.

## C. Test history

The guard suite ran to completion after every stage and the exit code was
read explicitly each time. Counts: 1,869 at 2B, 1,872 at 2C, 1,878 at 2D,
1,887 at 3B/3D, and the final run for this push. Four failures happened and
all four were real:

1. `allocate` guard: my own test inputs could not be satisfied inside the
   bounds I gave them (the guard was wrong, not the code).
2. the import gate: numpy imported outside a try/except in the new
   simulator, and the `research` package not recognised as ours.
3. three engine guards pinned the old 0.1-point bucket, the old
   `classic_allowed` signature, and the Stage 2D artifact's parameter hash
   (which legitimately changed once Stage 2E froze the parameters).
4. the tie-table float32 tolerance.

## D. Stage 2B: the data

Four seasons of Sleeper weekly projections joined to Sleeper weekly box
scores by stable player id. 28,366 real player-weeks kept; 272 games and 18
weeks in each of 2022, 2023, 2024 and 2025; roles assigned from pregame
projections only. Questionable rows are flagged, counted and excluded with a
reason, never repaired: 200, 197, 127 and 111 exclusions by season, almost
all positions out of scope. The feed's ~49,000 placeholder rows a season are
counted, not stored. 2025 is loaded and coverage-counted; its outcomes were
not read until Stage 2E part two.

Two data faults were found and fixed in later stages: the table's row order
followed Python's per-process string hash (so a rebuild never hashed the
same; rows are now written in a fixed order with the gzip timestamp pinned,
and the archive now repeats bit for bit across hash seeds), and a row that
played without a projection carried the box-score feed's game id, which
numbers games differently from the projection feed's, putting Cooper Rush's
Dallas-Tampa Bay line inside Atlanta-New Orleans. Every game key in the
table now carries exactly two teams.

## E. Stage 2B: the statistics that matter

Role-pair co-movement of standardised projection residuals, training
seasons, with 90% clustered-bootstrap intervals (clusters are team-seasons):

| Pair | r | interval |
|---|---|---|
| QB1 / WR1 | 0.387 | [0.348, 0.422] |
| QB1 / WR2 | 0.355 | [0.307, 0.401] |
| QB1 / TE1 | 0.284 | [0.242, 0.321] |
| QB1 / WR3 | 0.213 | [0.175, 0.251] |
| QB1 / RB1 | 0.066 | [0.030, 0.099] |
| WR1 / WR2 | 0.017 | [-0.027, 0.060] |
| WR1 / TE1 | -0.007 | [-0.046, 0.027] |
| QB1 / opposing QB1 | 0.202 | [0.136, 0.270] |
| team offense / opposing offense | 0.207 | [0.152, 0.264] |

The production simulator was producing 0.64 for QB1/WR1 and 0.64 for
WR1/WR2 on the same slate. That gap is the whole reason for the rest of
this work.

Marginals around the projection, by role: residual sd 7.87 points for QB1
rising to 8.92 for WR1; coefficient of variation 0.49 for QB1, 0.65 for WR1,
1.05 for WR3; P(over twice projection) 0.03 for QB1, 0.09 for WR1, 0.14 for
WR3. Target competition: team target ratio sd 0.25, and share residuals
correlate -0.09 to -0.26 across roles, so one man's extra targets are
another's lost ones. Touchdowns: team passing touchdowns mean 1.42,
distribution {0: .218, 1: .360, 2: .267, 3: .107, 4: .039, 5+: .009};
passing touchdowns equal the teammates' receiving touchdowns in 98.9% of
team-weeks.

## F. Stage 2C: reconciliation

Sleeper projects every player alone, so a team's books do not balance: the
receivers' projected yards sit 23% away from the quarterback's at the 95th
percentile, touchdowns 39%. Box scores keep those identities exactly (yards
97.9% of team-weeks, touchdowns 99.6%, completions 98.0%).

The team target is a blend of the quarterback's figure and the receivers'
sum, the weight fitted per identity on 2022-2024 as the blend that best
predicts the actual team quantity: 0.78 for yards, 0.73 for attempts, 0.55
for completions, 0.43 for touchdowns. Each side's change is shared among its
players by the residual variance of that component at that projection level
(fitted per position and component), within bounds, by an active set.

What it moves, on this week's live slate: the quarterback gains 0.26
DraftKings points at the median (absolute), receivers lose 0.12, backs 0.04,
tight ends 0.06; the largest single move is Daniel Jones, +1.10 points. One
tripwire fired (Indianapolis passing touchdowns, the receivers summing to
1.24 against his 0.82). On the training seasons, gaps after reconciliation
are zero to 1e-6 and 71 of 1,632 team-weeks trip a wire.

Fourteen invariants hold, including that the originals are never touched,
that reconciling twice changes nothing, and that a quarterback whose own
figure breaks its own bound (42.9 completions on 27.5 attempts, once) is
treated as no evidence.

## G. Stage 2D: the constrained simulator

816 training games at 200 worlds each: 163,200 team-game-worlds with zero
identity failures, zero bound failures, no negative values, and every point
recomputable from its components. The same seed reproduces the worlds; a
different seed does not. One game at 60,000 worlds takes 0.9 seconds.

The moment harness reproduces the Stage 2B artifact from the same table
(pair correlations agree to 0.0013, marginals and stacks exactly), so the
observed side of the fit is the artifact already reviewed, not a second
implementation of it.

## H. Stage 2E part one: the fit

Fifteen parameters, Nelder-Mead on the mean squared standardised mismatch
over 97 moments, each mismatch measured against that moment's own
uncertainty from a 200-replicate bootstrap over games within season. Common
random numbers, K = 40 worlds a game, 475 evaluations: objective 13.44 to
4.24. Frozen at `research/data/csim_params.json`, SHA-256 `df906d36b9effb01`.

Three honesty notes are in the artifact and the report:

- at K = 120 the same frozen parameters read 4.70, not 4.24. About half a
  unit of the apparent gain was the fit reading the noise of its own random
  numbers, and 4.70 is the honest figure.
- a second fit under different random numbers moved no parameter by more
  than 0.19 (env_rush), most by under 0.07.
- the profile step was too coarse to resolve most intervals. The report says
  so instead of printing an interval it did not measure, and Stage 2F's
  parameter draws use the second fit's distances instead.

The smallest-model pass pinned nothing: every parameter buys at least one
unit of z-squared. The biggest earners are the target-share noise (4.2), the
per-receiver big-play noise (2.3) and the shared pace (2.1).

## I. Stage 2E part two: the blind 2025 result

The gate was written before any 2025 outcome was read. All three pass:

| | constrained | legacy | verdict |
|---|---|---|---|
| CRPS, mean over seven roles (lower better) | 3.804 | 3.937 | pass (no worse than 1%) |
| pair-correlation RMSE against observed 2025 | 0.086 | 0.257 | pass (lower) |
| PIT uniformity, pooled (Kolmogorov, lower better) | 0.028 | 0.178 | pass (no worse) |

The legacy model's failure has one cause: its pass-catchers carry no
individual noise, so each receiver's distribution is far too narrow and his
teammates move with him. On 2025 it placed 27% of actual scores in the
lowest tenth of its own predicted distribution and 15% in the highest, and
its 80% interval covered WR3 only 42% of the time. Its WR1/WR2 correlation
was 0.544 where the season shows 0.024; QB1/RB1 0.308 where the season shows
0.083.

The constrained model's PIT histogram is flat to a hundredth in every bin,
its coverage is 0.77 to 0.86 against a nominal 0.80, its QB1/WR1 is 0.391
against an observed 0.379, its WR1/WR2 is -0.006 against 0.024, and its
marginal tails land almost exactly (WR3 above twice projection 0.138 against
0.140; WR1 0.090 against 0.063). Two seeds differ by 0.005 of CRPS.

**Where it loses.** The constrained model under-couples the two sides of a
game: quarterback against opposing quarterback 0.033 against an observed
0.235, team offense against opposing offense 0.082 against 0.262, and the
two largest game stacks calibrate slightly worse than under the legacy
model (PIT distance 0.101 and 0.099 against 0.075 and 0.061). The mechanism
is structural: the only link across the two teams is the shared pace, and
the game script is exactly opposite-signed, so a shootout cannot lift both
sides at once. A parameter cannot fix it. The fix is a shared efficiency or
scoring latent, and it cannot be validated blind on 2025 now that 2025 has
been read, so it is written down as the next version's pre-registered change
rather than made here.

Verdict: promoted to the board's **alternate** mode. Not the default.

## J. The two probe lineups across models

On the production-size board built for this push (the live $3.5M
Millionaire, 832,342 entries, legacy model):

| Probe | top 1% | rank | top 0.1% | rank | outright win | expected payout |
|---|---|---|---|---|---|---|
| L1 (Goff, six from one game) | 10.16% | #3 | 2.422% | #3 | 0.0243% | $292 |
| L2 (Geno Smith, five from one game) | 9.18% | #7 | 2.849% | #2 | 0.0991% | $1,072 |

Both are legal; both fail a cap of four from one game; 132,974 allowed
candidates.

On the Stage 2F comparison (same slate, same field model, 20,000 worlds, the
two simulators side by side):

**Retraction, kept on the record.** The first edition of this section said
L1 fell from third to 2,182nd under the constrained model. That was measured
on the wrong lineup. `research/stage2f.py` carried its own nine-name copy of
the owner's two entries and the copy was a different pair: four of nine names
wrong in L1, three of nine in L2. The tell was in the table and I did not
read it, because the substitute L2 showed a 1.45% top-1% rate where the real
L2 is near 10% on the same slate. The duplicated literal is deleted, the
module resolves `dfs_tourney.PROBES`, and a guard fails the build if a copy
reappears or if the artifact's rows do not carry those exact nine names.

**The canonical probes**, resolved from the one place they are defined:

- **L1** Jared Goff, Jahmyr Gibbs, Travis Etienne Jr., Amon-Ra St. Brown,
  Chris Olave, Devaughn Vele, Tyler Warren, Tre Tucker, Steelers.
  Three Lions and three Saints, and Detroit plays New Orleans: a six-man
  single-game stack.
- **L2** Geno Smith, Jonathan Taylor, Derrick Henry, Zay Flowers, Garrett
  Wilson, Josh Downs, Tyler Warren, Breece Hall, Raiders. Three Jets, three
  Colts, two Ravens, across three different games: same-team structure, no
  game stack.

**The rerun**, 2026-09-11, 4,324s, same slate and same field model for both,
20,000 worlds, 66,666 candidate draws:

| | legacy top 1% | rank | constrained top 1% | rank | legacy top 0.1% | rank | constrained top 0.1% | rank | p99 |
|---|---|---|---|---|---|---|---|---|---|
| L1 | 10.11% | #1 | 4.39% | #50 | 2.445% | #1 | 0.617% | #128 | 211.9 -> 211.7 |
| L2 | 9.85% | #1 | 4.59% | #38 | 2.973% | #1 | 0.866% | #5 | 200.8 -> 204.4 |

Ranks are against 52,749 allowed candidates under legacy and 53,135 under
constrained. Under legacy both lineups beat every candidate on the board, the
best of which reaches 9.27%; under constrained the best candidate reaches
5.89% and both probes fall behind it. Six parameter draws, each resampling
every fitted parameter inside the wider of its profile half-width and the
second fit's distance and rebuilding on a lighter grid, put the probes'
top 1% between 3.64% and 5.28%: the fall is not a knife-edge of the fitted
values.

**What the pair does and does not establish.** L2 is a useful comparator,
not a controlled counterfactual, and an earlier draft of this section
overclaimed by saying the teammate correction "hits both equally" because
both carry three-man team groups. That is not established. The two lineups
hold different players with different roles, target shares, rushing and
receiving mixes, salaries, projections and team environments, so the
constrained model's teammate correction can land on them very differently.
Subtracting L2's fall from L1's to isolate the cross-game term is too neat
and this report does not do it.

What can be said: **L2 shows that a lineup with substantial same-team
concentration can remain near the extreme tail under the constrained model,
at 5th on the top 0.1%, while L1 suffers a much larger relative penalty at
128th. That pattern is consistent with the constrained model's known
under-coupling of opposing offences, but does not uniquely identify or
quantify that mechanism.**

**The p99 column is the most informative thing here, and it points away from
a simple ceiling story.** L1's p99 barely moves, 211.9 to 211.7, while its
top-0.1% rank falls from 1st to 128th. L2's p99 actually *rises*, 200.8 to
204.4, while its top-1% probability roughly halves. So the constrained model
did not chop the ceilings off these lineups. It changed the competitive
environment around them: other constructions gained the individual variance
the legacy model was missing, and the relative advantage of a highly
correlated build shrank even as its absolute tail held or grew. That is the
behaviour predicted for adding missing individual variance, and it means the
change is in joint-tail structure and field competition rather than in
absolute ceiling.

**The answer to the question that started this, stated to the confidence the
evidence supports.** Legacy materially overvalues L1 and very large game
stacks generally; the constrained model and the population shift behind it
(five-plus from one game falling 21.5% to 1.0%, six-plus 6.0% to zero) are
strong evidence for that direction. But the constrained model also materially
underestimates cross-team game correlation, so its exact penalty to a
six-man bring-back stack is not calibrated. **L1's true tournament value is
likely below legacy's estimate, but it cannot presently be pinned to the
constrained 50th and 128th with high confidence.**

### The game-level shared-variance sensitivity

That measurement was then run (`research/xside_sweep.py`, artifact
`research/data/xside_sweep.json`). Every fitted parameter is frozen; one
experimental knob, defaulting to zero and never fitted, mixes a shared
game-level efficiency draw into both offences. At zero the model is
bit-identical to the frozen path.

| xside | QB vs opposing QB | L1 top 0.1% (rank) | L1 p99 | L2 top 0.1% (rank) | L2 p99 | 5+ from one game |
|---|---|---|---|---|---|---|
| 0.000 | 0.035 | 0.550% (#135) | 208.0 | 0.850% (#12) | 203.4 | 3.0% |
| 0.140 | 0.099 | 0.462% (#309) | 212.0 | 0.712% (#29) | 203.3 | 1.5% |
| 0.200 | 0.149 | 0.466% (#275) | 214.5 | 0.650% (#46) | 203.9 | 1.5% |
| 0.245 | 0.201 | 0.688% (#30) | 223.6 | 0.709% (#27) | 209.1 | 5.0% |
| 0.280 | 0.241 | 0.713% (#18) | 221.0 | 0.775% (#11) | 210.8 | 7.0% |

Three things have to be said about how to read that table, and none of them
is optional.

**It is a shared-VARIANCE sweep, not a coupling isolation.** The knob mixes
the shared draw in on top of each offence's own efficiency rather than
reallocating variance out of it, so raising it widens the marginals as well
as the coupling. Measured directly over 8,000 worlds per game: at the top
level the quarterback's own standard deviation is 12.9% wider than at zero
and team points 13.1% wider, and both probes' p99 rise with it. So this is a
**game-level shared-variance sensitivity**. L1's climb cannot be attributed
to cross-side correlation alone.

**The ranks are Monte Carlo noise at this resolution.** These boards run
6,000 worlds against Stage 2F's 20,000; the zero row reads 135th where the
full board reads 128th for the same lineup. The result is the *large
recovery* across the sweep, not 18th versus 30th as precise values.

**The 5+ and 6+ statistics are candidate-only.** They are computed over the
sampler's allowed draws, and the probes are inserted afterwards as
passengers and are never in that array. So L1 reaching 18th as an inserted
probe is not in tension with six-from-one-game staying at 0.0% of the
strongest candidates at every level: those measure different populations.

**The supported conclusion, and the limit of it.** Legacy overvalues very
large game stacks. Constrained v1 materially under-couples opposing offences
and therefore over-penalises structures such as L1. Adding plausible shared
game-level variance substantially restores L1's relative standing, but
because this also widens the marginal distributions, the sweep bounds the
sensitivity rather than estimating L1's calibrated rank. A constrained-v2
common-plus-idiosyncratic decomposition that preserves marginal variance is
required to resolve the question.

The observed holdout values, 0.235 for quarterbacks and 0.262 for offences,
fall between the 0.245 and 0.280 rows, so interpolating there is informative.
It is **not** an estimated production parameter and must not be used as one.
Nothing in this sweep is promoted: the frozen artifact is untouched, the knob
defaults to zero, and no board the owner reads is built with it set.

## K. Stage 3A: the tail

Ground truth by brute force: 3,000,000 field lineups on the real slate,
scored on 400 worlds, giving the exact mass above any score. Then the
300,000-lineup sample a board actually draws is asked what it thinks that
mass is, at the field's own order statistics.

| True mass above | raw count: median ratio | 10th-90th | within 3x | says zero | Jeffreys | within 3x | fitted Pareto tail | within 3x |
|---|---|---|---|---|---|---|---|---|
| 1 in 1,000 | 1.00 | 0.92-1.07 | 100% | 0% | 1.00 | 100% | 0.78 | 100% |
| 1 in 10,000 | 0.97 | 0.77-1.20 | 100% | 0% | 0.99 | 100% | 0.48 | 76% |
| 1 in 100,000 | 1.03 | 0.34-1.72 | 94% | 6% | 1.21 | 94% | 0.16 | 26% |
| 1 in 1,000,000 | 5.00 | 5.00-5.00 | 0% | 83% | 2.50 | 82% | 0.03 | 10% |

The top 1% (1 in 100) and the top 0.1% (1 in 1,000) are resolved cleanly.
One in a million, which is where first place in an 832,000-entry contest
lives, is not resolved by anything: the raw count says zero 83% of the time
and is five times too high when it does not, Jeffreys is 2.5 times high by
construction (its floor is 1.67e-6 against a truth of 6.7e-7), and the
fitted generalised Pareto is an order of magnitude low and worse than the
raw count everywhere below 1 in 10,000. Threshold diagnostics for the Pareto
fit: shape -0.20 to -0.08 across thresholds from the 90th to the 99.5th
percentile, i.e. a bounded tail, which is why it under-extrapolates.

Importance sampling is not on that list and the report says why: it needs
the probability the field sampler assigns to a drawn lineup, and this
sampler has none in closed form. The salary reserve makes each pick's
probability depend on the money already spent, so a lineup's density is a
sum over construction paths whose state is the set of players *and* the
salary. That is the same sum Stage 3C set out to take by dynamic
programming.

The arithmetic consequence for the first-place column: where the sample
reports nobody above, the truth typically has about half an entry to a few
entries above. At a true mass of 6.7e-7 the honest first-place probability is
exp(-0.55) = 0.58, while a sample reporting nobody above yields 1.0. The
column is therefore biased high, by a factor that depends on the slate and
is of order 1.5 to 3 at the very top.

## L. Stage 3B: ties

DraftKings does not break ties: entries tied for positions r through r+t
split the prizes for those positions equally. The payout curve now reads two
numbers from each world, the mass strictly above and the mass exactly level,
on a grid of 6,000 by 160. `tie_payout` is the rule itself and is exact by
inspection (a two-way tie for first in a 1,000,000 / 500,000 contest pays
750,000 each; a tie straddling the last paid place pays everyone in it the
average). The grid matches a direct multinomial draw of the same contest to
within 2.6% across the tie range and the engine matches a brute-force
simulation of the DraftKings rule lineup by lineup.

Rewriting it surfaced a second error. The count of entries above is
Binomial(C-1, F), and where its mean is small the normal approximation was
badly wrong: at 5,000 entries with one in 10,000 above it valued a 1,000,000
top prize at 825,000 against a true 767,000, 7.6% high. That count is now
Poisson term by term with lambda chosen so that P(nobody above) is
(1-F)^(C-1) exactly, and the grid lands within a fraction of a percent.

Score buckets went from a tenth of a point to a hundredth, because a bucket
has to mean "the same score" once ties are paid.

How much ties cost, measured: on a synthetic board with a coarse score
lattice, a lineup the field also builds loses 99.6% of its first-place money
to its own copies and a unique lineup loses 19.1%. On the real production
board, the cost is small (median 0.005%, largest 6.8%) because the legacy
simulator rescales each player's points to the Sleeper mean, which spreads
scores off the DraftKings lattice and makes exact ties between different
lineups artificially rare. The constrained simulator produces scores on the
real lattice, so its tie rates will be realistic; that is a further reason
the money columns and the simulator question are the same question.

## M. Stage 3C: duplication

The planned dynamic programme is not possible, for the reason in section K,
and the report demonstrates it rather than asserting it. The measurement
instead, on the 3,000,000-lineup field: 2,968,427 distinct lineups, 99.3% of
them drawn once, the most popular drawn 109 times, which is 30 expected
copies in an 832,000-entry contest.

Against that truth, the 300,000-lineup sample the board uses overstates the
copy count by 89% at the median for lineups with five or more copies in the
big field, with a 10th-to-90th range of -36% to +194%. The obvious shortcut,
multiplying the nine ownerships, overstates by 63 times at the median and up
to 604 times: the sampler's salary cap and stack rules make most independent
combinations impossible, so independence is not even the right order of
magnitude.

## N. Stage 3D: what the money columns are worth

`money_gate` names three links and the board carries its verdict:

| Link | State |
|---|---|
| the field sample resolves the mass the contest asks about | **fails** (300,000 sampled against 832,342 entries: one sampled lineup stands for 2.8 entries) |
| ties are paid the way the house pays them | holds (section L) |
| the duplicate count is derived, not measured | fails (section M) |

So first place, expected payout and ROI stay labelled experimental, with the
arithmetic in the label, and the board's own ranking lists are the top 1%
and the top 0.1%. The production board's numbers show why this matters: the
best candidate's expected payout reads $714 on a $5 entry, a +14,177% ROI,
and essentially all of it is the first-place term that section K says cannot
be resolved. Raising the field to the contest's size would fix the
arithmetic of link one; it would not fix the field *model*, which is a guess
at what the public builds.

## O. Stage 4A: what a portfolio should cover

Chosen on 7,500 worlds, judged on 7,500 it never saw, tie-aware payouts
throughout.

Rerun 2026-09-11 on the corrected shared-field cover (3,184s). The outright
column is now the strongest single entry rather than a union across entries,
because only our best-scoring entry can win and the old column combined the
per-lineup means as if they were independent.

| Objective | Entries | P(one in top 1%) | P(one in top 0.1%) | best single outright | Expected payout |
|---|---|---|---|---|---|
| cover top 1% | 10 | 0.5547 | 0.1531 | - | $3,326 |
| cover top 0.1% | 10 | 0.5300 | **0.1780** | - | **$6,086** |
| best by payout | 10 | 0.4150 | 0.1324 | - | $5,307 |
| cover top 1% | 20 | **0.7671** | 0.2388 | - | $4,626 |
| cover top 0.1% | 20 | 0.7098 | **0.2773** | - | **$9,621** |
| best by payout | 20 | 0.5929 | 0.2090 | - | $9,238 |

Every figure above came back identical to four decimals under the corrected
cover. That is the measurement, not a coincidence: see the note below.

> **WITHDRAWN, and with it the only recommendation this report made.** Every
> "P(one of N ...)" figure above, in sections P and Q, and on the served
> board, was produced by a cover that multiplied `(1 - P)` across our entries
> inside a world. That is the arithmetic for entries each meeting their own
> fresh public field. Ours all enter ONE contest against ONE realised set of
> opponents, so they are nested rather than independent: our best-scoring
> entry holds our best rank, and if it misses a cut none of the others can
> clear it. The union in a world is the best entry's chance, not one minus a
> product.
>
> How wrong in principle: two identical lineups with a 10% chance each came
> back as 19% instead of 10%, and on uniformly random probabilities an
> eight-entry set reports 99.7% against a true 90.6%.
>
> **How wrong on THIS board: not measurably.** That deserves saying plainly,
> because it contradicts the review that found the bug and my own first
> summary of it. On the live slate the per-world make-it probability is
> almost perfectly binary -- of 200,000 entry-world pairs, 94.13% are exactly
> zero, 5.34% are exactly one and 0.20% lie anywhere in between, and two of
> our entries are both in that middle band in 0.01% of worlds. Where the
> values are that binary, one minus a product and the maximum agree. On the
> twenty chosen entries the two forms differ by 4.0e-8:
>
> | twenty-entry cover | product form | maximum | empirical same-field union |
> |---|---|---|---|
> | 832,342 entries, top 1% | 0.726503 | 0.726503 | 0.7268 |
> | 5,000 entries, top 1% | 0.723174 | 0.720883 | not run |
>
> The empirical column takes the sampled field AS the contest and counts the
> held-out worlds where at least one of ours actually landed in the top 1%,
> integrating nothing. It agrees with the corrected figure to 0.0005 at every
> size from one entry to twenty. The Stage 4 rerun says the same thing from
> the other direction: every policy number came back identical to four
> decimals. So the served 78% twenty-entry coverage was NOT overstated, and
> the claim that this invalidates the coverage numbers does not survive
> measurement.
>
> The fix stays, for two reasons that do not depend on the size of the error.
> It is the correct semantics and exact by construction, and the error grows
> as the cut stops being extreme: on a 5,000-entry contest, where 2.67% of
> values fall in the open interval, the product form is already 0.3% high.
> The greedy step was wrong in the same way and its selections are now
> correct regardless of what the reported number does.
>
> The greedy gain is `sum_w max(0, P_cand,w - covered_w)`, not
> `sum_w miss_w * P_cand,w` which paid for probability already covered. Both
> the production cover and the research one now call a single function,
> `dfs_tourney.greedy_cover`, with exact tests on identical candidates,
> dominated candidates and candidates whose order flips between worlds.
>
> What was never touched by any of this: every per-lineup top-1% and
> top-0.1% value and every rank. They do not use the union.
>
> **What the recommendation may rest on, and what it may not.** The money
> gate is still closed: duplication is unresolved and the exact construction
> probability runs about 5% light, so the payout column is a diagnostic, not
> evidence. The $9,621 against $4,626 at twenty entries is therefore reported
> and must NOT be the reason for choosing an objective. The defensible
> argument is the held-out coverage alone: covering the top 0.1% returns
> **0.27728** of the held-out worlds with an entry in the top 0.1%, against
> **0.23877** for the top-1% cover, on worlds neither cover was chosen on.
> That is a 16% relative gain in the thing being optimised, measured where it
> counts, and it does not depend on a dollar figure.
>
> **So: cover the top 0.1% from ten entries up, on the coverage number.** The
> engine's default stays the top-1% cover until that one line is changed
> deliberately. The honest reading is that this conclusion was previously
> right for a reason that was wrong, which is not the same as being right,
> and the corrected cross-fit run is what now supports it.

## P. Stage 4B: a pass-catching back as the stack partner

The question cannot be asked on ordinary rule-abiding draws, because those
already force a receiver from the quarterback's team. Asked on 59,917 draws
with the stack requirement off: 36,795 carry a receiver stack anyway, and
932 (1.6%) are legal only if a back may count. Two thirds of those 932 have
a back projected for three or more targets; the highest is 5.4.

| Projected targets to count | Candidates allowed | P(one of 20 in top 1%) | P(one in top 0.1%) |
|---|---|---|---|
| never (the rule as it stands) | 36,795 | 0.7446 | 0.2135 |
| 3 or more | 37,413 | 0.7446 | 0.2135 |
| 4 or more | 37,207 | 0.7446 | 0.2135 |
| 5 or more | 37,037 | 0.7446 | 0.2135 |

**A genuine null, and worth more than it looks.** The first run of this
study could not produce one: the cached pool carried no projected targets, so
every setting returned the same 36,795 allowed candidates and the knob was
provably doing nothing because it was not connected. It is connected now.
The knob really does enlarge the candidate universe, from 36,795 to 37,413
at a three-target bar, and admitting those lineups still changes the held-out
numbers not at all, to four decimal places. Not one of the extra lineups
reaches the top-twenty cover.

That is the difference between "no effect measured" and "nothing was
measured", and only the second run earns the first. **`RB_STACK_TARGETS`
stays at never on this slate**, not because a back cannot be a useful stack
partner in general, but because on these 59,917 unforced draws the 932
lineups the rule excludes are not lineups worth entering. The owner's
instinct not to be limited without a reason is answered by the measurement
rather than by the rule.

## Q. Stage 4C: the per-game cap

| Cap | Candidates allowed | P(one of 20 in top 1%) | P(one in top 0.1%) | best single top 1% |
|---|---|---|---|---|
| 4 | 51,421 (79.2%) | 0.7526 | 0.2275 | 10.00% |
| 5 | 52,120 (80.3%) | 0.7726 | 0.2396 | 10.00% |
| 6 | 52,146 (80.4%) | 0.7671 | 0.2388 | 10.00% |
| none | 52,146 (80.4%) | 0.7671 | 0.2388 | 10.00% |

**Inconclusive, and it should be reported that way.** The four caps span
0.7526 to 0.7726 on the held-out top-1% cover with no monotone relationship:
five is nominally best, four nominally worst, six and none identical. That
is the shape of noise, not of a rule, and nothing here distinguishes the
options. The strongest single lineup is identical under every cap.

No winner is being manufactured from that range. **`CL_MAX_PER_GAME` stays
at none because nothing argues for moving it**, which is a different claim
from "none is best" and is the only one the measurement supports. Choosing
between four, five and six would need a measurement that actually separates
them: more slates, more seeds, and enough worlds that the gap clears Monte
Carlo error. Under the constrained simulator the question may evaporate
anyway, since five-from-one-game falls from 17.5% to 1.0% of the strongest
200 candidates, so the cap would bind on almost nothing.

## R. Stage 4D: the production board

Built here against the live slate through the real pipeline, not served (the
PC serves boards): draft group 151307, the Sunday main slate, 12 games, 590
DraftKings players, four seven-figure contests including the $3.5M
Millionaire at 832,342 entries and $1M for first. Engine 5, serialization 3,
model legacy, 60,000 simulated worlds split 20,000 for generation and 40,000
for evaluation, 47 minutes.

Receipts: field 300,000 of 300,000 completed with nothing rejected;
candidate draws 150,000 of 150,000; 144,613 candidates of which 132,974
allowed; 19,997 distinct hindsight-optimal lineups from 20,000 generation
worlds; both probes resolved; simulator stamped legacy-latent v1 with its
constants; the payout block naming the tie rule, the 0.01-point bucket and
the 160-point tie grid; `experimental` built by the money gate. Field
calibration achieved 39.4% top ownership against a 38% target, $49,281 mean
salary against $49,400, 35.0% bring-backs against 35%, and 0.38% of lineups
holding a defense against their own quarterback.

The most popular build in the field (41.6 expected copies) is worth $3.28
against a $5 entry.

### The served board, measured after the deploy

The PC pulled commit `1366221`, saw the engine go from 4 to 5, and rebuilt
the same slate on its own hardware: built 2026-09-11 03:22:08 UTC in 3,546
seconds, engine 5, serialization 3, model legacy, simulator stamped
`legacy-latent` v1 with the same seven constants, 60,000 worlds split 20,000
and 40,000. Field 300,000 of 300,000 completed, nothing rejected, no unfilled
slot, no over-cap row, no row without a quarterback; candidate draws 150,000
of 150,000 on the same terms; 144,759 candidates of which 132,913 allowed;
20,000 distinct hindsight-optimal lineups. All 243 pool rows carry a team, an
opponent and a projection-feed game id, the 12 game keys hold exactly two
teams each, and each of the 24 teams has exactly one opponent. Both probe
lineups resolve as passengers. Every probe number is CONTEST-dependent, and
the first edition of this section quoted the board's first contest by
position rather than the one the rest of the report is about: those were the
$4,444 MEGA Millionaire's 750-entry figures. Named properly, on the
$5 / 832,342-entry Millionaire: L1 tops 1% at 10.34% and 0.1% at 2.478%,
ranking 2nd and 2nd of 132,913; L2 at 9.49% and 2.867%, ranking 2nd and 1st.
On the $4,444 / 750-entry MEGA Millionaire the same two read 9.71% / 2.701%
and 8.93% / 2.927%. The ranks are identical either way, so no preference
between the lineups changes, but a bare top-0.1% figure without its contest
beside it is not a number. Both are legal, both are illegal under a
four-per-game cap, and both carry the tie-aware keys (`win_pct`,
`win_any_pct`, `ev`, `ev_notie`) with no `ev_dup`. The money gate on the served board is the Stage 3D verdict
verbatim: not authoritative, the sample link false at 2.77 entries per
sampled lineup, the tie link true, the duplicate link false.

This is the receipt limitation 8 was waiting for, so that limitation is
closed. The three showdown boards on file were built before the engine bump
and are still read through the tab's engine-4 fallback until the PC rebuilds
them on its own schedule.

## S. What to enter, by portfolio size

From the production board's top-0.1% and top-1% lists, with the caveat of
section N on the money columns. Ranked by top 0.1%.

1. **One entry.** Tyler Shough, Gibbs, Hall, St. Brown, Olave, Vele,
   LaPorta, Wan'Dale Robinson, Eagles. Top 1% 10.73%, top 0.1% 2.973%.
2. **Two.** Add Bryce Young, Gibbs, Etienne, St. Brown, Olave, Vele,
   LaPorta, McMillan, Dolphins (top 1% 10.80%, top 0.1% 2.751%, the best
   single lineup by payout and by outright win).
3. **Three.** Add Goff, Gibbs, Hall, St. Brown, Olave, Vele, Juwan Johnson,
   Reed, Eagles (9.91% / 2.409%).
5. **Five.** Add the Goff/Montgomery/Etienne/St. Brown/Olave/Vele/LaPorta/
   Flowers/Jaguars build (9.89% / 2.328%) and the Shough/Gibbs/Hall/St.
   Brown/Olave/Vele/LaPorta/Dowdle/Panthers build (9.41% / 2.373%).
10. **Ten and 20.** Use the board's portfolio list rather than the top-N
    list: its first twenty entries raise P(at least one in the top 1%) from
    10.8% for one entry to 78.3% for twenty. Section O says to cover the top
    0.1% instead of the top 1% at these sizes, which the engine does not yet
    do by default, so the served portfolio is the top-1% cover until that
    one line changes.

Your own two lineups sit at #3 and #7 by top 1% and #3 and #2 by top 0.1% on
this board, so if you enter them you are not giving much up on this slate
under the production model. Section J is the warning: the better-calibrated
model does not agree.

## T. Known limitations

1. **The first-place, payout and ROI columns are not resolvable** from a
   300,000-lineup sample, and no smoothing crosses that (section K). They
   are labelled, and the label carries the arithmetic.
2. **The constrained model under-couples the two sides of a game**
   (section I). It specifically understates bring-back game stacks, which is
   exactly the structure the owner asked about.
3. **2025 is spent as a holdout.** Any further change to the constrained
   model cannot be validated blind against it. The next version needs a
   pre-registered structure and either a new season or a
   leave-one-season-out scheme inside 2022-2024.
4. **The field model is a guess.** Everything in sections K, L, M and N
   concerns how well we resolve *our own* field model's tail. Whether that
   model resembles the real public is a separate, unmeasured question; the
   ownership and stack-rate targets come from a published Milly Maker record
   and the calibrator hits them, which is not the same as being right.
5. **The fit read its own Monte Carlo noise** to the tune of half an
   objective unit (section H), and the profile intervals were not resolved.
6. **Stage 4's decisions were measured on one slate** with the legacy
   simulator, 7,500 build and 7,500 scoring worlds. Differences of a few
   percent in the tables are not distinguishable from noise.
7. **The tie measurement on the real board is small** only because the
   legacy simulator's rescale spreads scores off the DraftKings lattice
   (section L). Expect it to matter more under the constrained model.
8. **Closed.** The production board in section R was built in the session,
   not served, and the served receipt had to come from the PC's own rebuild.
   It arrived: the PC rebuilt the same slate at engine 5 on 2026-09-11
   03:22:08 UTC in 3,546 seconds, with both completion receipts at 100%, both
   probes resolved, the game keys complete and the money gate unchanged. The
   numbers are in section R under "The served board". What remains is not a
   limitation of this work but the ordinary wait for the three showdown
   boards to rebuild at engine 5 on the PC's own schedule.
9. **The exact lineup probability is not exact yet.** `dfs_pathprob` sums
   the sampler's own paths for a fixed lineup by driving `classic_sample`
   rather than re-implementing it. Against three million draws on the twelve
   most-duplicated lineups it lands within a median ratio of 0.953, but the
   median sits below one and the worst case is 2.8 standard errors low, so a
   small systematic undercount survives. It is labelled experimental, the
   money gate's duplicate link stays shut because of it, and the number that
   would close it is a bound on the undercount rather than another anecdote.
10. **One claim in this chain was overstated, and this is the correction.**
   Commit `e054862` built the Poisson log-factorial table at module scope in
   `dfs_tourney`, which is a numpy call, and the server has no numpy: the
   module could not be imported there at all. The guards run in CI installs
   no numpy either, so the suite crashed on the import and the commit went
   red within three minutes. The fix commit `1366221` describes that as the
   tournament route dying in production. It did not. Nothing the web app
   serves imports `dfs_tourney` -- the routes read a pickle the PC wrote, and
   `pc_worker` is the only importer, on the machine that does have numpy.
   Measured after the fact by putting the broken module ahead of the repo
   with numpy hidden and calling the routes: `/healthz`,
   `/api/dfs/tourney` and `/api/dfs/tourney/list` all answered 200, and only
   a direct `import dfs_tourney` raised. The error ledger for the day agrees:
   no code of any kind was filed in that window. So the blast radius was a
   red CI run, and the guard's value is preventing the break the next time
   something on the server does import it, not repairing one that happened.

## U. Regression guards added

Every stage shipped guards in `tests/combo_audit_guards.py` (429 lines added
over the five commits, final suite count in the commit message):

- **2B**: the table joins by stable id with no duplicate player-week, every
  kept row has a team, an opponent and a position in scope, receptions never
  exceed targets, one pregame QB1 per team-week; the coverage report counts
  every season at 18 weeks and 272 games and marks 2025 the holdout; the
  statistics are training-only with counts and clustered intervals;
  DraftKings points from components; the research seeds.
- **2C**: the weights artifact (training seasons, hash, every lambda inside
  its own interval, the target factor, positive variance slopes); the four
  identities to 1e-9 with the target between the sides and every bound;
  idempotence, variance-ordered shares, the quarterback's move equal to
  (1-lambda) of the gap; no quarterback, the tripwire, the
  completions-over-attempts rule; `allocate`'s pinning and leftover;
  `reconcile_games` on a weekly_games-shaped dict; the source pins that keep
  the legacy simulator away from `recon`; the reproducible archive.
- **2D**: the constrained stamp; `player_pool`'s model and seed plumbing,
  its refusals, its seeded side generators; the classic build's model and
  stamp with the showdown build and the PC untouched; the artifact's
  invariants and reproducibility; a synthetic two-team game through the
  simulator with every identity, bound and mean checked.
- **3B**: the DraftKings tie rule on paper across six cases; the no-tie
  column reproduced exactly; monotonicity in the tie mass with the clamp
  bounded; the Poisson fix against a direct draw, including a demonstration
  that the old normal approximation was 7.6% high; the engine against a
  brute-force multinomial; the 0.01-point bucket; the board's payout block
  and the tab's columns.
- **3D**: the money gate's verdict, its three links, its arithmetic, and
  that it still refuses on the duplicate link alone with a field as large as
  the contest.
- **4B**: the stack knob off by default, legal at a four-target bar,
  illegal at six, with the published rule text saying a back does not count;
  projected targets reaching the engine from the pool.
- **The numpy-free import surface**: every numeric module imports in a
  subprocess where importing numpy raises, and the served app answers
  `/healthz` and both tournament routes there without ever pulling
  `dfs_tourney` or `nfl_dfs_csim` into `sys.modules`. Limitation 9's
  correction is pinned too, so the nicer version of that story cannot come
  back by deletion.

## V. Questions for the adversarial review

1. **The opposing-side coupling.** My proposed fix is a game-level
   efficiency or scoring latent shared by both teams, fitted on 2022-2024
   with 2024 held out of that fit so it has a blind season. Is that the
   right structure, and is leave-one-season-out an acceptable substitute for
   a fresh holdout now that 2025 is read?
2. **The first-place column.** Given section K, is there any estimator I
   have missed that works without the sampler's density? If not, do you
   agree the column should stay experimental rather than be removed, on the
   grounds that its *ranking* is still informative even where its level is
   not?
3. **The tie grid's deep end.** Beyond a tie group of 64 I use a closed-form
   band average with a deterministic count, and the two branches meet with a
   step worth a ten-thousandth of the top prize, which I clamp. Is the clamp
   acceptable, or would you rather see the discontinuity left visible?
4. **Stage 4's power.** *Superseded, and answered.* This asked what would be
   wanted before changing the portfolio objective to the top-0.1% cover. The
   Showdown pass then ran that experiment properly -- disjoint selection and
   scoring folds, objective-specific shortlists, two boards, two seeds, both fold
   directions, both exact tie conventions, paired bootstrap -- and the answer is
   in `showdown_audit_report.md` section S4. The tail objective **does** improve
   held-out tail coverage, and the objective **did not change**, because it costs
   more top-1% coverage than an unvalidated economic model may price and its size
   rides on the uncalibrated field knob. Note that the recommendation in section O
   of THIS document predates that experiment and is superseded by it.
5. **The duplicate estimator.** The sample overstates copies by 89% at the
   median. Would you shrink it toward something, and if so toward what,
   given that independence is 63 times wrong?
6. **L1.** *Answered in part, by the rerun rather than by argument.* The
   better-calibrated model puts the real L1 at 50th of 53,135 on the top 1%
   and 128th on the top 0.1%, not 2,182nd -- that figure belonged to the
   wrong lineup and is retracted in section J.

   **An earlier revision of this item then reintroduced the overclaim section J
   exists to reject, and an adversarial review caught it.** It said L2 "separates
   the two mechanisms", that the shared part is the teammate correction and the
   excess on L1 tracks the opposing-side weakness. Section J says the opposite and
   says it for a reason: L2 is a **comparator, not a controlled counterfactual**.
   It differs from L1 in more than the game stack, nothing holds the other
   differences fixed, and subtracting its fall from L1's to isolate a cross-game
   term is, in section J's words, "too neat". That subtraction is withdrawn again
   here, and the fact that it grew back once is itself worth knowing: a corrected
   claim can reappear in a later section of the same document written at a
   different time.

   What the two probes support is narrower. Both fall sharply under the
   better-calibrated model; L1 falls further; the two differ in both team structure
   and game stacking, so the *attribution* of L1's extra fall between those two
   causes is **not identified by this comparison**. Establishing it needs a
   controlled design -- constrained v2 with the game-level shared latent, holding
   team structure fixed -- validated prospectively on untouched 2026 weeks rather
   than on 2025.
