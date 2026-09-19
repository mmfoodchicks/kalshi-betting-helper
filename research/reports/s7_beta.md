# S7, second artifact: the softmax field's one knob, fitted

Artifact `research/data/s7_beta.json` (module `research/s7_beta.py`), built
on the three frozen standings exports and the pinned inputs of the first S7
artifact. Everything here was run under the fences the independent study set
and section 8 of `s7_field.md` adopted: support first, counts only, ordering
labelled beta-invariant, six single-board transfers plus leave-one-contest-out
with both poolings, the out-of-objective metrics graded afterward, no owner
rule anywhere near the field, no new feature, no second parameter.

**The deployed beta values happen to be in the right broad numerical range.
The universal 0.2%-top-share calibration rule is not validated, and the
projection-only softmax family is wrong in shape; fitting the knob does not
fix it.** The deployed values (0.291 / 0.455 / 0.351, the rule solved on the
gated universe) sit within 0.02 to 0.05 of the maximum-likelihood betas on
the lifted universe and cost 0.004 to 0.036 nats per entry against them, out
of a 1.5 to 2.2 nats gap that the family leaves against the empirical
distribution. That nearness is a coincidence of two universes, not a
validation of the rule: re-solved on the same universe as the fit, the rule
gives 0.374 / 0.475 / 0.390 and is worse than the MLE on every board (by
0.024 / 0.016 / 0.104 nats); and on its own gated universe, the one production serves, the rule's beta
(0.291 / 0.455 / 0.351) is off that support's counts MLE (0.323 / 0.414 /
0.291) by -0.03 / +0.04 / +0.06 and 0.017 / 0.015 / 0.048 nats worse. The
rule is not validated on either universe. At the fitted
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
| DEN @ KC | **highest integrity**: pool, contest and feeds captured about 50 hours before kickoff, genuinely before the game but not a near-lock snapshot (no late news in it); roster post-game | 30 of 55 | 3,483,231 (777,056) | 87,909 | **98.68%** | 1.32% (Justin Fields 431, Jake Briningstool 284, Adam Prentice 268) |
| DET @ BUF | post-game reconstruction | 26 of 47 | 1,333,214 (421,702) | 88,023 | **82.59%** | 17.41% (Frank Gore Jr. 11,731, Greg Dortch 1,608, Keleki Latu 956) |

Every number below for DET @ BUF is conditional on the 82.6% of its field the
inputs can represent. A sixth of that field holds a player Sleeper never
projected, and nothing here stands in for the missing line: no final points,
no post-game projection, no zero, no invented floor.

## 2. The fit

The question is not whether beta can learn which lineups the public chooses;
it cannot. The softmax is a strictly monotone transformation of the lineup
projection for every positive beta, so beta changes concentration and
probability gaps and never the ordering. The question is: given the
projection ordering already in hand, how aggressively does the public
concentrate along it, and is one concentration parameter portable across
contests?

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

The MLE matches the sufficient statistic, the mean, by construction; the
variance is not a second free moment, so this is the family's first
over-identifying test, and it fails. Precisely: a second-moment mismatch
diagnoses the one-parameter exponential tilt conditional on the supplied
projections and support, with finite-sample and reconstruction caveats; it
does not by itself say whether the projections or the functional form
deserve the blame. What it does say is that, given these projections, the
real field sits in a narrower band of projected totals than a softmax at the
matched mean, and inside that band it concentrates on lineups the projection
does not single out. Both halves show in the bin
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

### 6.1 Beta moves with the represented choice set: the universe-tail sensitivity

DEN @ KC enumerates 30 projected players against 25 and 26, so the first
question about its higher beta is whether the universe's size, not the
public, moves the estimate. Each board refitted on its K highest-projected
players only (entries holding a player outside the top K leave the support;
the share kept is beside each row):

| Board | K = 20 | K = 22 | K = 24 | full universe |
|---|---|---|---|---|
| NE @ SEA | 0.309 (89.2% kept) | 0.323 (93.0%) | 0.329 (96.3%) | 0.332 (25 players, 96.7%) |
| DEN @ KC | 0.383 (81.0%) | 0.399 (89.0%) | 0.414 (91.6%) | **0.434** (30 players, 98.7%) |
| DET @ BUF | 0.287 (55.0%) | 0.288 (79.2%) | 0.293 (81.3%) | 0.301 (26 players, 82.6%) |

Beta rises with the universe on every board. In a correctly specified
softmax it would not: if the field were p(i) proportional to exp(beta x_i)
with one true beta, conditioning on the lineups made of the top-K players
would give p(i | subset) proportional to exp(beta x_i) with the same beta;
the normaliser changes and the coefficient does not. The systematic drift
(0.309 to 0.332, 0.383 to 0.434, 0.287 to 0.301) is therefore further
evidence of misspecification and support sensitivity, not a mechanical
property of a bigger universe and not evidence for production's per-board
re-solving of the rule. The observed mean projection barely moves between
the top-20 support and the full one (86.63 to 86.50, 71.03 to 70.71, 90.21
to 90.30) while the uniform mean falls by 11 to 15 points: the public holds
far fewer entries in the tail than a softmax carrying the core's beta would
put there, the same thin tail sections 3 and 6.4 show. The conclusion is
the reviewer's: raw beta estimates are empirically sensitive to the
represented choice set because the projection-only softmax is misspecified;
a universal transferred beta is not supported by these data, and neither is
the 0.2%-share recalibration rule. On a common twenty-player support the
spread across boards narrows from 0.118 to 0.086 over the other two boards'
mean, and DEN @ KC stays the highest at every K. The twenty-player rows are
on a shrinking support (DET @ BUF keeps 55% of its field there) and are a
diagnostic of the parameterisation, not a fit anything downstream uses.

### 6.2 The score equation, and which boards want hotter or colder

A pooled fit does not zero three residuals with one beta. Its checksum is
its score equation: the weighted sum of the fitted boards' mean residuals is
zero (entry weights for the entry-weighted fit, equal weights for the
contest-balanced one). The residual each board keeps under the common beta
(model mean x minus observed mean x) is the transfer diagnostic: negative
means the board wants a hotter field than the pool gives it, positive a
colder one.

| Fit | beta | NE @ SEA | DEN @ KC | DET @ BUF | Weighted sum of the fitted boards' residuals |
|---|---|---|---|---|---|
| all three, entry-weighted | 0.3514 | +0.558 | -1.926 | +1.366 | 0.000 |
| all three, contest-balanced | 0.3514 | +0.558 | -1.925 | +1.367 | 0.000 |
| leave out NE @ SEA, entry-weighted | 0.3683 | **held out: +0.998** | -1.473 | +1.758 | 0.000 |
| leave out NE @ SEA, contest-balanced | 0.3625 | **held out: +0.850** | -1.626 | +1.626 | 0.000 |
| leave out DEN @ KC, entry-weighted | 0.3206 | -0.342 | **held out: -2.855** | +0.572 | 0.000 |
| leave out DEN @ KC, contest-balanced | 0.3167 | -0.465 | **held out: -2.981** | +0.465 | 0.000 |
| leave out DET @ BUF, entry-weighted | 0.3695 | +1.029 | -1.442 | **held out: +1.786** | 0.000 |
| leave out DET @ BUF, contest-balanced | 0.3779 | +1.233 | -1.233 | **held out: +1.968** | 0.000 |

DEN @ KC wants a hotter field under every common beta (a residual of -1.9 to -3.0 projected points); NE @ SEA and DET @ BUF want a colder one. The same three signs appear whichever two boards are pooled, so the disagreement is between DEN @ KC and the other two, not an artefact of one pooling.

## 6.3 The decomposition: three baselines

Comparing the fitted beta only against the served knob would mix two changes,
the universe and the value. Three baselines separate them: the served
placeholder on its own gated universe (historical fidelity, on its own and
smaller support, so its likelihood is not comparable to the other rows and
its shape columns are), the same numerical beta applied to the lifted
universe (the universe change and its renormalisation alone), and the 0.2%
rule re-solved on the lifted universe (the apples-to-apples baseline for the
fitted beta).

| Board | (i) served placeholder on its gated universe: support, beta, NLL on that support (saturated, uniform), top share model / observed there, effective lineups model / observed there | (ii) the same beta value on the lifted universe: NLL, top share, effective lineups | (iii) 0.2% rule re-solved on the lifted universe: beta, NLL, top share, effective lineups | (iv) MLE: beta, NLL, top share, effective lineups |
|---|---|---|---|---|
| NE @ SEA | 47.46%, 0.2913, 9.241 (7.827, 12.676), 0.20% / 0.90%, 4,900 / 906 | 10.047, 0.086%, 12,008 | 0.3737, 10.045, 0.200%, 4,747 | 0.3318, 10.021, 0.133%, 7,483 |
| DEN @ KC | 91.59%, 0.4548, 10.369 (8.763, 13.563), 0.20% / 0.39%, 7,451 / 2,900 | 10.641, 0.168%, 10,086 | 0.4749, 10.653, 0.200%, 8,384 | 0.4343, 10.637, 0.139%, 12,237 |
| DET @ BUF | 74.29%, 0.3514, 10.220 (8.224, 12.952), 0.20% / 1.47%, 5,889 / 1,186 | 10.604, 0.139%, 8,864 | 0.3895, 10.673, 0.200%, 6,065 | 0.3009, 10.569, 0.081%, 15,052 |

Read across a row. Lifting the gate (i to ii) keeps the beta and changes the universe: the top share falls from the rule's 0.2% to 0.09 / 0.17 / 0.14% and the effective number of lineups rises from 4,900 / 7,451 / 5,889 to 12,008 / 10,086 / 8,864, because the tail the gate had removed now holds mass. Re-solving the rule on the bigger universe (ii to iii) raises the beta on every board and moves it away from the MLE on every board; its likelihood is within 0.002 nats of the raw value's on NE @ SEA and worse on DEN @ KC (10.653 against 10.641) and DET @ BUF (10.673 against 10.604). The MLE (iv) beats both by 0.004 to 0.036 nats. The served placeholder's own-universe row is on its own support (47.5 / 91.6 / 74.3% of active entries) and its likelihood is not comparable to the other three columns; on that support the real field is 5.4 / 2.6 / 5.0 times more concentrated than the placeholder. The rule can still be tested there against the counts MLE on the same gated support: that MLE is 0.323 / 0.414 / 0.291 against the rule's 0.291 / 0.455 / 0.351, and the rule is worse by 0.017 / 0.015 / 0.048 nats per entry. The rule loses to the counts MLE on its own universe as well as on the lifted one.

## 6.4 Calibration over fixed projection-rank bins

Bins keyed on fitted-probability thresholds move their own membership with
beta. These bins are fixed by the projection order (rank 1-10, 11-100, ...),
so beta is visibly doing the only thing it can: redistributing mass more or
less aggressively along one ordering. Observed field mass, the 0.2% rule
re-solved on this universe, and the fitted beta, per bin:

| Projection rank | NE @ SEA: observed / 0.2% rule here / MLE | DEN @ KC: observed / rule / MLE | DET @ BUF: observed / rule / MLE |
|---|---|---|---|
| 1-10 | 0.99 / 1.66 / 1.13 | 0.57 / 1.21 / 0.87 | 0.36 / 1.53 / 0.65 |
| 11-100 | 3.91 / 7.76 / 5.67 | 1.59 / 5.24 / 4.04 | 2.01 / 6.26 / 3.19 |
| 101-1000 | 18.35 / 24.80 / 20.52 | 12.68 / 19.07 / 15.97 | 9.23 / 22.38 / 14.35 |
| 1001-10000 | 50.27 / 44.53 / 44.14 | 42.43 / 40.29 / 38.31 | 43.79 / 44.97 / 40.72 |
| 10001-100000 | 25.34 / 20.60 / 27.10 | 40.66 / 30.93 / 35.63 | 44.31 / 24.26 / 38.30 |
| 100001+ | 1.14 / 0.65 / 1.44 | 2.06 / 3.27 / 5.18 | 0.31 / 0.61 / 2.80 |

The same picture on every board, at either beta. The public puts less of its field than the model in the projection's top 1,000 (23.2 / 14.8 / 11.6% against 27.3 / 20.9 / 18.2% at the MLE and 34.2 / 25.5 / 30.2% under the rule) and more in ranks 1,001 to 100,000 (75.6 / 83.1 / 88.1% against 71.2 / 73.9 / 79.0% at the MLE). A lower beta would move mass down the order, but it moves it past the public into the deep tail, where the MLE already holds more than the field on DEN @ KC (5.2% against 2.1%) and DET @ BUF (2.8% against 0.3%). No beta matches both ends: along one fixed ordering the real field is thinner than the model at the very top and in the deep tail and thicker in the middle. That is the variance finding of section 3, made visible.

## 6.5 Reading the failure

The independent study's taxonomy, applied board by board:

- **Case A (ordering good, concentration wrong; beta may be enough)**: no
  board. The ordering is poor everywhere (Spearman 0.19 to 0.32; 12% to 23%
  of the field in the projection's top 1,000 where the fitted model puts 18%
  to 27%), and on the one board whose chalk the projection ranks second, the
  winner sits at rank 19,623.
- **Case B (ordering bad; the fit improves the likelihood but cannot fix the
  ranking)**: every board, in the weak sense that the MLE buys 0.004 to
  0.036 nats. But the fit does not even improve concentration consistently:
  against the served placeholder's value the MLE raises the top share on NE
  @ SEA (0.086% to 0.133%) and lowers it on DEN @ KC (0.168% to 0.139%) and
  DET @ BUF (0.139% to 0.081%), because the sufficient statistic pulls beta
  toward the observed mean while the concentration wants it higher. The
  likelihood-optimal beta and the concentration-matching beta pull in
  opposite directions on two boards of three.
- **Case C (ordering reasonable and mean matched, but captain, salary,
  structure and variance still wrong)**: no board, under the definition as
  given, because Case C assumes a reasonable ordering and the ordering here
  is poor. The boards are **Case B plus an independently demonstrated shape
  failure after moment matching**: the ordering is poor, and even after beta
  matches the projection mean the remaining distribution is still wrong. The
  mean is matched by construction; the variance ratio is 0.84 / 0.67 / 0.59,
  the salary left is $1,280 to $1,640 too high, the captain mix 28 to 50 pp
  off, the effective number of lineups 3.7 to 11 times too many, all at the
  fitted beta. That shape failure, not a relabelled Case C, is the finding;
  the taxonomy is not redefined after the answer.
- **Case D (fitted beta varies heavily by contest; no evidence for a
  universal temperature)**: partly. The MLEs span 0.30 to 0.43, DEN @ KC is
  the outlier and the board with pre-lock inputs, about a quarter of its
  excess is the universe's tail (section 6.1) and the rest is the board. Two
  of three boards agree; that is not evidence for a universal temperature
  and not strong evidence against one.

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

Settled: fitting the one knob is not the fix. The counts MLE sits within
0.05 of the deployed values and buys 0.004 to 0.036 nats per entry against
them; the 0.2% rule itself, re-solved on the fit's universe, is worse than
the MLE on every board, so the deployed values are near the MLE by
coincidence of two universes and the rule is not validated; the family then
misses concentration, duplication, salary, captain and the second moment by
amounts no beta reaches, and no beta transfers to the highest-integrity
board. `SD_MONEY_FIELD_CALIBRATED` stays
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
