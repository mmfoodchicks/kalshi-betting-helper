# S7, second artifact: the softmax field's one knob, fitted

Artifact `research/data/s7_beta.json` (module `research/s7_beta.py`), built
on the three frozen standings exports and the pinned inputs of the first S7
artifact. Everything here was run under the fences the independent study set
and section 8 of `s7_field.md` adopted: support first, counts only, ordering
labelled beta-invariant, six single-board transfers plus leave-one-contest-out
with both poolings, the out-of-objective metrics graded afterward, no owner
rule anywhere near the field, no new feature, no second parameter.

**The knob's value was roughly right; the family is wrong in shape, and fitting
the knob does not fix it.** The maximum-likelihood beta moves 0.02 to 0.05 from
the value production's 0.2% rule already solves for on each board and improves
the per-entry log-likelihood by 0.004 to 0.036 nats, out of a 1.5 to 2.2 nats
gap that the family leaves against the empirical distribution. At the fitted
beta the model still spreads the field over 3.7 to 11 times too many effective
lineups, leaves $1,280 to $1,640 too much salary on the table, and misses the
captain position mix by 28 to 50 percentage points. No single beta transfers
to DEN @ KC, and DEN @ KC is the board with pre-lock inputs: the two post-game
reconstructions agree with each other and the clean board is the outlier, so
that disagreement is not obviously the inputs.

## 1. Support first

The universe for each board is every DraftKings pool player with a Sleeper
projection under the game's teams, enumerated by production's own legality
(under the $50,000 cap with the 1.5x captain salary, both teams) with the
depth-chart gate lifted, no field-only cap and no owner rule. Injury statuses
are cleared as in the first artifact; the gate itself is not applied. Every
real entry whose six players are in that pool was found in the enumeration
(`legal_but_not_enumerated` is 0 on all three boards), so the salary file and
the standings agree.

| Board | Integrity | Projected players (of DK pool) | Legal lineups (gated universe was) | Active entries | Representable | Outside, not repaired |
|---|---|---|---|---|---|---|
| NE @ SEA | post-game reconstruction | 25 of 68 | 1,040,958 (319,933) | 125,800 | **96.65%** | 3.35% (Montorie Foster Jr. 1,664 entries, Lan Larison 975) |
| DEN @ KC | **highest integrity** (pool, contest, feeds pre-lock, about 50 h before kickoff; roster post-game) | 30 of 55 | 3,483,231 (777,056) | 87,909 | **98.68%** | 1.32% (Justin Fields 431, Jake Briningstool 284, Adam Prentice 268) |
| DET @ BUF | post-game reconstruction | 26 of 47 | 1,333,214 (421,702) | 88,023 | **82.59%** | 17.41% (Frank Gore Jr. 11,731, Greg Dortch 1,608, Keleki Latu 956) |

Every number below for DET @ BUF is conditional on the 82.6% of its field the
inputs can represent. A sixth of that field holds a player Sleeper never
projected, and nothing here stands in for the missing line: no final points,
no post-game projection, no zero, no invented floor.

## 2. The fit

Beta maximises the likelihood of the exact-lineup counts, captain identity
included, over the representable universe. The log-likelihood of this
one-parameter exponential family is concave in beta and its score is the
moment condition E_beta[x] = observed mean x (x = the lineup's projected
points, 1.5x on the captain), so the MLE is solved by bisection on that
condition; the moment residual at the solution is the checksum (0.000 on all
three boards) and the `nll_scan` in the artifact shows the single minimum.

| Board | beta, own MLE | production's beta (0.2% rule on its gated universe) | 0.2% rule re-solved on the lifted universe | Per-entry NLL at own MLE | at production's beta | uniform | saturated (empirical entropy) |
|---|---|---|---|---|---|---|---|
| NE @ SEA | **0.3318** | 0.2913 | 0.3737 | 10.021 | 10.047 | 13.856 | 8.557 |
| DEN @ KC | **0.4343** | 0.4548 | 0.4749 | 10.637 | 10.641 | 15.063 | 8.916 |
| DET @ BUF | **0.3009** | 0.3514 | 0.3895 | 10.569 | 10.604 | 14.103 | 8.375 |

Read the last four columns together. Uniform to saturated is the whole
distance a field model could travel; the softmax family travels 72%, 72% and
62% of it and stops. The MLE beats production's placeholder value by 0.026,
0.004 and 0.036 nats per entry on the same support. The remaining 1.46, 1.72
and 2.19 nats per entry are what a projection-only ordering cannot explain,
and no value of the knob reaches them.

## 3. The family's own test: the second moment

Once beta is fixed, the family fixes the variance of x across entries. The
observed variance is lower on every board:

| Board | Var of x, model at MLE | observed | ratio |
|---|---|---|---|
| NE @ SEA | 29.77 | 24.91 | **0.84** |
| DEN @ KC | 19.44 | 13.02 | **0.67** |
| DET @ BUF | 30.56 | 18.03 | **0.59** |

The real field sits in a narrower band of projected totals than
a softmax at the matched mean, and inside that band it concentrates on
lineups the projection does not single out. Both halves show in the bin
calibration at the MLE: observed over model is 1.16 to 1.18 in the 10^-5 to
10^-4 band on all three boards and 0.18 to 0.67 in the 10^-7 to 10^-6 band.
Pushing beta up to match the concentration overshoots the mean: the 0.2% rule
re-solved here leaves a residual of +1.1, +0.7 and +2.2 projected points, and
its top share of 0.2% is still under the observed 0.44%, 0.36% and 1.32%.

## 4. Shape at the fitted beta

| Board | Top lineup share, observed | model at MLE | at production's beta | Effective lineups (1/sum p^2), observed | model at MLE | Distinct lineups, observed | model expected | Entries in lineups with 51+ copies, observed | model |
|---|---|---|---|---|---|---|---|---|---|
| NE @ SEA | 0.442% | 0.133% | 0.086% | 2,021 | 7,483 | 16,949 | 33,433 | 34.6% | 8.3% |
| DEN @ KC | 0.361% | 0.139% | 0.168% | 3,297 | 12,237 | 17,725 | 36,665 | 14.4% | 1.6% |
| DET @ BUF | 1.323% | 0.081% | 0.139% | 1,366 | 15,052 | 11,917 | 31,993 | 22.1% | 0.3% |

The "observed" shares here are over the representable entries, so the DET @
BUF chalk (962 copies) reads 1.32% rather than the 1.09% of the whole field
in the first artifact. The duplication curve is the plainest statement of the
model-form failure: the family predicts that lineups with more than fifty
copies hold 0.3% to 8% of the field, and they hold 14% to 35%.

## 5. Ordering is invariant to beta

In this family every ordering statistic is a property of the projections
alone; beta rescales the probabilities monotonically and cannot reorder them.
These numbers are therefore reported once per board, and no fit can move them.

| Board | Spearman, observed count vs x over observed lineups | Real chalk's model rank | Observed mass in the model's top 1,000 (model at MLE) | Winner's model rank (tied) |
|---|---|---|---|---|
| NE @ SEA | 0.317 | 4,109 | 23.3% (27.3%) | 2,790 (23) |
| DEN @ KC | 0.248 | **2** | 14.9% (20.9%) | 19,623 (44) |
| DET @ BUF | 0.185 | 3,197 | 11.6% (18.2%) | 31,601 (17) |

DEN @ KC's chalk is the projection's second-ranked lineup; the model's most
probable lineup holds 0.139% at the MLE, so the chalk, one place below it,
holds less than that against the 0.36% it drew. The ordering is right at the
very top of that board and the mass is at least 2.6x under. On the other two the public's
favourite lineup is thousands of places down the projection order.

## 6. Transfer

Six single-board directions and leave-one-contest-out with both poolings. The
per-entry NLL is on the target board's own representable support, so every
column of a row is comparable; entry-weighted and contest-balanced pooling
differ by under 0.01 in beta because the three fields are of similar size.

| Graded on | own MLE | from NE @ SEA | from DEN @ KC | from DET @ BUF | leave-one-out, entry-weighted | leave-one-out, contest-balanced | production's beta |
|---|---|---|---|---|---|---|---|
| NE @ SEA (reconstruction) | 0.3318 / 10.021 | — | 0.4343 / 10.155 | 0.3009 / 10.036 | 0.3683 / 10.040 | 0.3625 / 10.034 | 0.2913 / 10.047 |
| DEN @ KC (**highest integrity**) | 0.4343 / 10.637 | 0.3318 / 10.756 | — | 0.3009 / 10.848 | 0.3206 / 10.786 | 0.3167 / 10.797 | 0.4548 / 10.641 |
| DET @ BUF (reconstruction) | 0.3009 / 10.569 | 0.3318 / 10.582 | 0.4343 / 10.792 | — | 0.3695 / 10.633 | 0.3779 / 10.649 | 0.3514 / 10.604 |

NE @ SEA and DET @ BUF transfer onto each other almost freely (0.014 and
0.015 nats). DEN @ KC does not: its beta costs 0.13 and 0.22 nats on the
other two, theirs cost it 0.12 and 0.21, and the leave-one-out beta (0.32)
costs it 0.15 nats, more than production's placeholder value does (0.004).
A pooled beta therefore does not beat the 0.2% rule as a rule: it wins on
NE @ SEA by 0.01 nats and loses on DEN @ KC by 0.15 and on DET @ BUF by
0.03 to 0.04. Leave-one-out here is a reconstruction sensitivity, not a clean
historical validation, and the caveat cuts the unusual way: the two
post-game boards agree with each other, and the board whose inputs were
captured before lock is the one no other board's beta fits.

### 6.1 Why DEN @ KC's beta is higher: the universe's tail, measured

Beta multiplies raw projected points and the softmax is normalised over the
whole universe. The MLE solves E_beta[x] = observed mean, and every
low-projected lineup added to the universe lowers E_beta[x] at a given beta,
so a board with a longer tail of near-zero players needs a higher beta to
reach the same observed mean. DEN @ KC enumerates 30 projected players
against 25 and 26. Each board refitted on its K highest-projected players
only (entries holding a player outside the top K leave the support; the
share kept is beside each row):

| Board | K = 20 | K = 22 | K = 24 | full universe |
|---|---|---|---|---|
| NE @ SEA | 0.309 (89.2% kept) | 0.323 (93.0%) | 0.329 (96.3%) | 0.332 (25 players, 96.7%) |
| DEN @ KC | 0.383 (81.0%) | 0.399 (89.0%) | 0.414 (91.6%) | **0.434** (30 players, 98.7%) |
| DET @ BUF | 0.287 (55.0%) | 0.288 (79.2%) | 0.293 (81.3%) | 0.301 (26 players, 82.6%) |

Beta rises with the universe on every board, so a beta is not a property of
the public alone: it is the public read against a particular universe, and
production's per-board 0.2% rule re-solves it on each universe where a
transferred constant would not. The tail explains about a quarter of DEN @
KC's excess, not all of it: over the other two boards' mean its beta is
0.118 higher on the full universes and 0.086 higher at twenty players. The
rest is the board. The twenty-player rows are on a shrinking support (DET @
BUF keeps 55% of its field there) and are a diagnostic of the
parameterisation, not a fit anything downstream uses.

## 7. Out of objective

Graded after the fit at each board's own MLE, model against the representable
entries (the same support the likelihood saw), with the whole active field's
figure beside the structural ones. Distances are L1 in percentage points over
the categories.

| Board | Captain position mix, L1 | 5-1 share, model / observed | Structure L1 | K/DST slots L1 | Salary left, model mean / observed | Share leaving $1,000+, model / observed | Captain ownership, L1 over players (max) | Flex ownership, L1 (max) |
|---|---|---|---|---|---|---|---|---|
| NE @ SEA | 31.0 | 11.4% / 13.7% | 4.8 | 12.0 | $2,560 / $1,281 | 70.6% / 39.3% | 42.6 (10.5) | 113.8 (19.4) |
| DEN @ KC | 27.6 | 13.1% / 15.0% | 6.1 | 14.9 | $2,714 / $1,073 | 72.3% / 36.1% | 46.5 (13.6) | 143.0 (19.0) |
| DET @ BUF | 49.6 | 11.7% / 16.4% | 9.4 | 9.0 | $2,151 / $618 | 65.0% / 20.4% | 58.7 (18.0) | 187.8 (19.6) |

Three findings, each a model-form failure recorded here before any parameter
is added:

- **Salary.** The softmax has no salary term, so it leaves $2,150 to $2,700
  on the table where the public leaves $620 to $1,280, and gives 65% to 72%
  of its mass to lineups with $1,000 or more unspent where the public gives
  20% to 39%. This is the largest single miss and it is the same on every
  board.
- **Captain.** The projection-driven captain is the wrong captain: 34% and
  40% quarterback captains on NE @ SEA and DEN @ KC against 20% and 28%;
  48% running-back captains on DET @ BUF (Jahmyr Gibbs 31.7% against 13.7%
  drawn) against 26%, with wide receivers at 16% against 30%.
- **5-1.** The public-field model still underweights 5-1 (11.4 / 13.1 /
  11.7% against 13.7 / 15.0 / 16.4%). This stays separate from the other 5-1
  observation, that our optimizer's top-ranked list is far more 5-1-heavy
  than the real field; compatible, opposite in direction, never one
  statement.

## 8. What this settles and what it does not

Settled: fitting the one knob is not the fix. The counts MLE moves beta by at
most 0.05 from where the 0.2% rule already puts it and buys 0.004 to 0.036
nats per entry; the family then misses concentration, duplication, salary,
captain and the second moment by amounts no beta reaches, and no beta
transfers to the highest-integrity board. `SD_MONEY_FIELD_CALIBRATED` stays
False and nothing downstream changes.

Not settled: what replaces the family. That is a modelling decision the
reviewer and the owner make with these tables in front of them, and the
candidates are not equal. Salary is the largest miss, present on every board,
and it is a single observable feature of a lineup; the captain mix and the
duplication curve are the next two. Each would be a new feature, and this
artifact adds none. When one is proposed, the same fences apply: fitted to
counts on representable support, graded out of objective, transferred across
the three boards with DEN @ KC labelled, and never compared against the
historical +0.047078.

The S5 rerun stays controlled and stays deferred: current source with the
placeholder field against current source with whatever field the next stage
produces, same worlds, same universe.
