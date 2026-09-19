# S7, third artifact: projection plus one linear salary-left feature

Artifact `research/data/s7_salary.json` (module `research/s7_salary.py`), on
the same frozen exports, the same lifted universes and the same representable
support as the beta fit. Pre-registered before the run, in the module's
docstring and the artifact's `meta`: the model, the three nested arms, the
principal criterion, what does not count as evidence, and two additions to
the reviewer's design (a user-cluster bootstrap on the held-out delta, and the
salary-left profile of the excluded entries).

The model, exactly and nothing else: log w = beta x - gamma s, with x the
lineup's projected points (1.5x on the captain) and s the salary left in
thousands of dollars; gamma free in sign. No captain coefficient, no stack
coefficient, no ownership input, no duplication feature, no salary buckets.

**Salary survives the transfer test on every direction. Projection plus
salary assigns higher probability to a held-out board's entries than
projection alone by 0.23 to 1.01 nats per entry in all eight directions, with
user-cluster 95% intervals that exclude zero by more than ten standard
errors; salary alone beats projection alone on every board; and the real
chalk moves from utility rank 4,119 / 2 / 3,218 to 694 / 2 / 308. What
salary does not do is reproduce the field's concentration: the top lineup's
share is still 6 to 40 times under, lineups with more than fifty copies still
hold 0 to 3.5% of the model's field against 14 to 35% of the real one, and
the salary-left variance is 1.5 to 1.9 times the model's. The gain comes from
putting the field in the right salary band, not from finding the lineups the
public piles onto.** Projection+salary earns "preferred research field family
for prospective validation". It earns nothing in production, and S5 stays
deferred.

## 1. Support, and the salary feature on it

The support is the beta fit's: the same representable entries (96.65 /
98.68 / 82.59% of active), the same universes. Salary left is exact on every
board, projected or not: DraftKings salaries do not change after lock, so the
reconstruction caveats on NE @ SEA and DET @ BUF do not touch this feature.
The entries outside the support (they hold a player Sleeper never projected)
are profiled beside the entries inside it. Three marginal summaries can show
that the exclusion does not appear to materially explain the salary-left
result; they cannot establish that it introduces no bias:

| Board | Representable entries: mean salary left, share leaving $1,000+ | Excluded entries: mean, share leaving $1,000+ | Universe mean salary left |
|---|---|---|---|
| NE @ SEA | $1,281, 39.28% (121,587 entries) | $1,492, 39.85% (4,213 entries) | $17,601 |
| DEN @ KC | $1,073, 36.05% (86,751 entries) | $993, 26.51% (1,158 entries) | $20,346 |
| DET @ BUF | $618, 20.42% (72,701 entries) | $668, 21.35% (15,322 entries) | $17,862 |

On the board with the large support hole, DET @ BUF (17.4% excluded), the
excluded and included profiles are similar ($668 against $618 left; 21.4%
against 20.4% leaving $1,000 or more). DEN @ KC's excluded entries leave
$1,000 or more less often (26.5% against 36.1%), but they are 1.3% of its
field. The universe's mean salary left is $17,600 to $20,300 (most legal
lineups are cheap junk); the public's is $618 to $1,281. A projection-only softmax
reached $2,151 to $2,714 at its MLE. That gap is what the one new coefficient
is asked to close.

## 2. The fits: three nested arms

Each arm is a maximum-likelihood fit of the exact-lineup counts by Newton on
the concave log-likelihood; the score is the moment condition on the fitted
sufficient statistics and the residuals at the solution are the checksums
(0.000 everywhere).

| Board | projection only: beta, NLL | salary only: gamma, NLL | projection + salary: beta, gamma, NLL | saturated | uniform |
|---|---|---|---|---|---|
| NE @ SEA | 0.3318, 10.021 | 1.0556, 9.640 | **0.1207, 0.8168, 9.549** | 8.557 | 13.856 |
| DEN @ KC | 0.4343, 10.637 | 1.2223, 10.124 | **0.1756, 0.9665, 9.978** | 8.916 | 15.063 |
| DET @ BUF | 0.3009, 10.569 | 1.7161, 9.674 | **0.0601, 1.5875, 9.648** | 8.375 | 14.103 |

gamma is positive on every board (the expected sign; not evidence). With
salary in the model, beta falls from 0.33 / 0.43 / 0.30 to 0.12 / 0.18 /
0.06: most of what the projection coefficient was doing was standing in for
salary. The in-sample gains (0.47 / 0.66 / 0.92 nats) are reported because
they are in the artifact and are not evidence: a nested model cannot lose in
sample.

**Identification.** The Hessian at the two-parameter MLE is the model
covariance of the sufficient statistics; projection and salary are related
through DraftKings' pricing, so the question is whether beta and gamma are
separately identified or trading off.

| Board | corr(beta_hat, gamma_hat) | condition number of the sufficient-statistic covariance | nominal SE beta, gamma |
|---|---|---|---|
| NE @ SEA | -0.580 | 18.1 | 0.0008, 0.0027 |
| DEN @ KC | -0.448 | 13.1 | 0.0011, 0.0035 |
| DET @ BUF | -0.350 | 42.4 | 0.0010, 0.0060 |

A correlation of -0.35 to -0.58 is a real trade-off but not a ridge: both
parameters are identified. The condition numbers are inflated by the units
(x varies over tens of points, s over one or two thousand dollars) and are
reported for the record. The nominal standard errors treat every entry as an
independent draw and are optimistic; one user can hold 150 entries. The
user-cluster bootstrap in section 3 is the honest uncertainty.

## 3. The principal criterion: held-out delta-NLL

Pre-registered: the change in per-entry NLL of projection+salary relative to
projection-only on a board whose counts did not fit gamma. Negative is
better. Six single-board directions and leave-one-contest-out with both
poolings; the theta for each column comes from the same source for both
arms.

| Held out | Integrity | from NE @ SEA | from DEN @ KC | from DET @ BUF | leave-one-out, entry-weighted | leave-one-out, contest-balanced | in-sample at own MLE (not evidence) |
|---|---|---|---|---|---|---|---|
| NE @ SEA | reconstruction | — | -0.543 | -0.230 | **-0.409** | -0.397 | -0.472 |
| DEN @ KC | highest integrity | -0.728 | — | -0.716 | **-0.776** | -0.785 | -0.659 |
| DET @ BUF | reconstruction | -0.780 | -1.013 | — | **-0.857** | -0.875 | -0.921 |

Every direction is negative. The held-out gains are 87%, 118% and 93% of the
in-sample ones (DEN @ KC's exceeds its in-sample figure because the
leave-one-out beta hurts projection-only there more than it hurts the
two-feature model). The uncertainty that respects multi-entry users:

| Held out | leave-one-out gain (nats per entry, positive = salary helps) | user-cluster SE | naive per-entry SE | 95% user-cluster interval | users, entries |
|---|---|---|---|---|---|
| NE @ SEA | 0.409 | 0.0187 | 0.0043 | [0.374, 0.446] | 41,603, 121,587 |
| DEN @ KC | 0.776 | 0.0171 | 0.0041 | [0.742, 0.809] | 26,301, 86,751 |
| DET @ BUF | 0.857 | 0.0080 | 0.0035 | [0.840, 0.873] | 23,533, 72,701 |

These intervals are conditional on the trained parameters: only the
held-out board's users are resampled and the source-board fit is held
fixed; a full training-plus-evaluation interval would refit on resampled
source users in every replicate, and the stage was not rerun for that
because the effects are far too large for it to matter. The cluster
standard error is two to four times the naive one, and the intervals still
sit more than ten standard errors from zero. Against the beta
fit's 0.004 to 0.036 nats, one salary coefficient is worth 0.23 to 1.01 nats
per entry out of sample.

**The three arms out of sample** (leave-one-out, entry-weighted; NLL per
entry on the held-out board):

| Held out | projection only | salary only | projection + salary | salary-only minus projection-only | projection's contribution beyond salary |
|---|---|---|---|---|---|
| NE @ SEA | 10.040 | 9.710 | 9.630 | -0.330 | 0.080 |
| DEN @ KC | 10.786 | 10.124 | 10.010 | -0.661 | 0.114 |
| DET @ BUF | 10.633 | 9.782 | 9.776 | -0.851 | 0.006 |

Salary alone beats projection alone on every board, by 0.33 to 0.85 nats.
Projection keeps 0.08 and 0.11 nats of independent information beyond salary
on NE @ SEA and DEN @ KC and 0.006 on DET @ BUF, where the held-out beta is
0.136 and buys almost nothing. That is the reviewer's "different conclusion":
on these boards the public's lineup choice is described far better by how
much salary it leaves than by how many points the projection gives it, and
the projection's independent contribution is small and board-dependent.

**The fitted parameters by source:**

| Source | projection + salary: beta, gamma | salary only: gamma | projection only: beta |
|---|---|---|---|
| own MLE, NE @ SEA | 0.121, 0.817 | 1.056 | 0.332 |
| own MLE, DEN @ KC | 0.176, 0.967 | 1.222 | 0.434 |
| own MLE, DET @ BUF | 0.060, 1.587 | 1.716 | 0.301 |
| leave out NE @ SEA, entry-weighted | 0.115, 1.182 | 1.382 | 0.368 |
| leave out NE @ SEA, contest-balanced | 0.110, 1.207 | 1.402 | 0.362 |
| leave out DEN @ KC, entry-weighted | 0.098, 1.002 | 1.201 | 0.321 |
| leave out DEN @ KC, contest-balanced | 0.090, 1.083 | 1.268 | 0.317 |
| leave out DET @ BUF, entry-weighted | 0.136, 0.879 | 1.118 | 0.370 |
| leave out DET @ BUF, contest-balanced | 0.141, 0.891 | 1.131 | 0.378 |
| all three, entry-weighted | 0.116, 0.995 | 1.208 | 0.351 |
| all three, contest-balanced | 0.112, 1.045 | 1.251 | 0.351 |

gamma spans 0.82 to 1.59 across the boards' own fits (DET @ BUF's field left
$618 and wants the stiffest penalty), and every transferred or pooled gamma
between 0.88 and 1.21 still wins by the margins above. The coefficient is not
universal; the improvement is.

## 4. Over-identifying diagnostics at the fitted parameters

Both means are matched by construction at each board's own MLE (the
checksums). Everything below was free to fail.

**Second moments.** The projection-only family was too wide in x (observed
variance 0.84 / 0.67 / 0.59 of the model's). With salary in, the sign flips
and a second failure appears in s:

| Board | Var x, observed over model | Var s, observed over model | corr(x, s), model / observed |
|---|---|---|---|
| NE @ SEA | 1.34 | 1.94 | -0.58 / -0.71 |
| DEN @ KC | 1.13 | 1.78 | -0.45 / -0.54 |
| DET @ BUF | 1.10 | 1.45 | -0.35 / -0.42 |

The observed salary-left variance is 1.5 to 1.9 times what a linear penalty
at the matched mean allows: the real distribution has a point mass at $0 and
a longer tail than an exponential tilt produces. The salary-left distribution
itself, model against observed on the representable entries, at each
board's own MLE:

| Board | exactly $0 | $1 to $200 | $201 to $500 | $501 to $1,000 | $1,001 to $2,000 | over $2,000 | median (p10, p25, p75, p90) |
|---|---|---|---|---|---|---|---|
| NE @ SEA, model | 7.69% | 12.34% | 15.0% | 20.46% | 23.73% | 20.77% | $900 ($100, $300, $1,800, $3,000) |
| NE @ SEA, observed | 13.92% | 16.27% | 15.39% | 17.92% | 16.8% | 19.7% | $600 ($0, $200, $1,600, $3,400) |
| DEN @ KC, model | 4.68% | 15.42% | 21.2% | 20.05% | 23.22% | 15.43% | $700 ($100, $300, $1,500, $2,500) |
| DEN @ KC, observed | 6.67% | 19.86% | 21.07% | 18.27% | 18.94% | 15.2% | $600 ($100, $200, $1,400, $2,600) |
| DET @ BUF, model | 15.0% | 21.64% | 22.26% | 21.93% | 14.93% | 4.24% | $400 ($0, $100, $900, $1,500) |
| DET @ BUF, observed | 13.24% | 24.42% | 24.09% | 19.72% | 14.05% | 4.48% | $400 ($0, $100, $900, $1,500) |

The tail is close (over $2,000: 22.7 / 16.3 / 5.0% against 21.1 / 15.9 /
4.9%). The point mass at exactly $0 is under on NE @ SEA and DEN @ KC (7.7%
and 4.7% against 13.9% and 6.7%), and the share leaving $1,000 or more is
still over on NE @ SEA (48.1% against 39.3%). A linear term moves the field
into the right band; it does not put the public's spike at the cap.

**Concentration and duplication.** This is where salary does not deliver:

| Board | Top lineup share, observed / beta-only / projection+salary | Effective lineups, observed / beta-only / projection+salary | Distinct lineups expected, beta-only / projection+salary / observed | Entries in lineups with 51+ copies, observed / beta-only / projection+salary |
|---|---|---|---|---|
| NE @ SEA | 0.442% / 0.133% / 0.070% | 2,021 / 7,482 / 7,695 | 33,433 / 22,468 / 16,949 | 34.59% / 8.26% / 3.5% |
| DEN @ KC | 0.361% / 0.139% / 0.061% | 3,297 / 12,237 / 11,725 | 36,665 / 25,111 / 17,725 | 14.43% / 1.57% / 0.1% |
| DET @ BUF | 1.323% / 0.081% / 0.034% | 1,366 / 15,052 / 10,318 | 31,993 / 18,318 / 11,917 | 22.1% / 0.29% / 0.0% |

The expected number of distinct lineups improves (closer to the observed
count on every board), but the top share gets worse, the effective number of
lineups moves little (a third better on DET @ BUF, unchanged elsewhere), and the mass in heavily duplicated lineups stays near
zero against 14 to 35% observed. The likelihood gain is a band effect: mass
moves out of the $2,000-plus-left lineups the public never plays into the
band it does play, and inside that band the model is still nearly uniform
over tens of thousands of lineups while the public concentrates on hundreds.
Duplication stays an outcome, not a feature; it did not emerge from salary.

**Captain, structure, kickers and defenses** (L1 in percentage points at each
board's own MLE; the beta-only figure beside it):

| Board | Captain position mix L1, beta-only / projection+salary | 5-1 share, model / observed | Structure L1 | K/DST slots L1 |
|---|---|---|---|---|
| NE @ SEA | 30.99 / 36.1 | 10.48% / 13.73% | 4.75 / 6.51 | 11.96 / 12.0 |
| DEN @ KC | 27.59 / 12.24 | 11.22% / 15.0% | 6.12 / 7.57 | 14.91 / 15.53 |
| DET @ BUF | 49.6 / 20.21 | 12.81% / 16.41% | 9.4 / 7.43 | 8.98 / 9.85 |

The captain mix improves a lot on DEN @ KC (27.6 to 12.2) and DET @ BUF
(49.6 to 20.2) and gets worse on NE @ SEA (31.0 to 36.1, where the model now
plays a quarterback captain 32% of the time against 20%). 5-1 stays under on
every board. Kickers and defenses are unchanged. The captain is a separate
failure that salary does not address.

## 5. Ordering, now allowed to move

The utility beta x - gamma s reorders the universe, so these are computed per
fit. At each board's own MLE, beta-only beside projection+salary:

| Board | Spearman, observed count vs utility | Real chalk's utility rank | Observed mass in the utility's top 10 / 100 / 1,000 | Winner's rank (diagnostic only) |
|---|---|---|---|---|
| NE @ SEA | 0.317 / 0.331 | 4,119 / **694** | 0.986 / 4.897 / 23.25% against 1.179 / 6.526 / 33.954% | 2,798 / 1,340 |
| DEN @ KC | 0.248 / 0.239 | 2 / **2** | 0.567 / 2.163 / 14.841% against 0.853 / 4.081 / 22.869% | 19,616 / 3,822 |
| DET @ BUF | 0.185 / 0.226 | 3,218 / **308** | 0.362 / 2.369 / 11.595% against 0.567 / 2.623 / 21.038% | 31,579 / 2,665 |

The real chalk moves from the projection's rank 4,119 and 3,218 on NE @ SEA
and DET @ BUF to 694 and 308 under the salary-aware utility, and stays at 2
on DEN @ KC. The utility's top 1,000 now holds 34 / 23 / 21% of the field
against 23 / 15 / 12% under projection alone. The Spearman over all observed
lineups barely moves: salary fixes the top of the order, not the body.

Under the leave-one-out parameters the same holds (chalk rank 504 / 2 / 766;
observed mass in the top 1,000 34.9 / 23.2 / 20.0%). The reviewer's strong
outcome test, "if salary dramatically improves likelihood while the real
chalk remains rank 4,000 on NE and BUF, the projection and captain structure
is fundamentally wrong", resolves the other way at the top of the order: the
chalk is now within the first few hundred. The captain mix on NE @ SEA and
the duplication on every board say the projection and captain structure is
still wrong below the top.

**Fixed bins of the new utility's order** (observed / model at own MLE):

| Utility rank | NE @ SEA | DEN @ KC | DET @ BUF |
|---|---|---|---|
| 1-10 | 1.18 / 0.63 | 0.85 / 0.48 | 0.57 / 0.30 |
| 11-100 | 5.35 / 3.91 | 3.23 / 2.81 | 2.06 / 2.28 |
| 101-1000 | 27.43 / 21.85 | 18.79 / 16.48 | 18.41 / 16.75 |
| 1001-10000 | 49.74 / 57.82 | 53.30 / 53.91 | 62.62 / 63.42 |
| 10001-100000 | 15.34 / 15.71 | 22.58 / 26.03 | 16.21 / 17.23 |
| 100001+ | 0.97 / 0.09 | 1.25 / 0.30 | 0.13 / 0.01 |

The beta-only picture was a public thinner than the model at the very top
and in the deep tail and thicker in the middle. Under the salary-aware
utility the top inverts: the public is now thicker than the model in the top
1,000 on every board (NE @ SEA 34.0% against 26.4%) and thinner in the
1,001 to 10,000 band. The model has found the right neighbourhood and is
too flat inside it.

**The composition of the top 1,000, UNWEIGHTED** (a secondary diagnostic:
the utility's 1,000 highest lineups against the 1,000 most-duplicated real
unique lineups, each lineup counting once on either side; it answers "what
kinds of lineups live in each side's favourite set", not "what share of the
field has each property"; ties at the observed cutoff count are broken
deterministically by universe row order, ascending, and the artifact stamps
the cutoff count, the lineups tied at it and how many of those are included):

| Board | Captain position mix, model top 1,000 / observed top 1,000 | Mean salary left, model / observed | 5-1 share, model / observed |
|---|---|---|---|
| NE @ SEA | WR 39.2, QB 36.7, RB 16.5 / WR 38.5, RB 29.7, QB 18.2 | $286 / $530 | 9.0% / 12.0% |
| DEN @ KC | QB 45.5, WR 29.9, RB 14.7 / WR 34.3, QB 27.8, RB 23.2 | $257 / $458 | 11.3% / 15.3% |
| DET @ BUF | RB 48.4, WR 18.7, QB 18.6 / WR 29.8, RB 26.3, QB 22.9 | $66 / $398 | 12.7% / 17.4% |

The model's favourite thousand spend tighter than the public's favourite
thousand ($66 to $286 left against $398 to $530) and lean on the captain the
projection likes (quarterbacks on NE @ SEA and DEN @ KC, running backs on
DET @ BUF) where the public's most-duplicated builds spread the captain
across positions. Linear salary overshoots at the very top of its own order.

## 6. What this settles and what it does not

Settled, on three contests with the stated reconstruction caveats:

- One linear salary-left coefficient improves held-out probability
  assignment by 0.23 to 1.01 nats per entry in every transfer direction,
  against the beta fit's 0.004 to 0.036. The principal criterion is met
  decisively.
- Salary alone beats projection alone on every board. Projection retains a
  small, board-dependent independent contribution (0.08 / 0.11 / 0.006 nats
  beyond salary out of sample). On DET @ BUF, once a lineup's salary left is
  known, this projection adds almost no independent information about which
  lineups the public chose. That is not a statement about projecting
  football outcomes; it is a statement about this projection as a
  description of human lineup popularity conditional on salary. Those are
  different jobs.
- Salary moves the real chalk into the top few hundred of the utility order
  on the two boards where the projection had it at rank 4,000 and 3,000.
  Salary explains where the public shops. It does not explain why thousands
  of people pile into the same small subset once they are there.
- Salary does not reproduce concentration or duplication, the point mass at
  $0, the salary-left variance, the captain mix on NE @ SEA, or the 5-1
  share.

Not settled, and not claimed: money EV. Three contests, two of them
reconstructed after the game, identify a defect and compare simple families;
they do not certify a field model. `SD_MONEY_FIELD_CALIBRATED` stays False.

**The retrospective baseline is frozen here** (commit bc7a52c, CI run 97;
the reviewer's decision of 2026-09-19). These three contests have been asked
many questions, and selecting the next residual feature on the same boards
would quietly turn the process into iterative training on three contests.
So:

- **No new retrospective feature.** The $0 point mass is not fitted now,
  and realised duplication is never a feature: it is the outcome the model is
  asked to predict, and feeding it back would use the answer to predict the
  answer.
- **The next stage is prospective validation of the frozen family**
  (`research/s7_prospective.py`, `research/data/s7_frozen_field.json`). The
  primary parameter set is the all-three contest-balanced fit, beta 0.112
  and gamma 1.045, chosen because the target is cross-slate generalisation
  and a larger contest must not define the universal research parameter by
  its size; the entry-weighted fit (0.116, 0.995) is the secondary
  sensitivity arm; the contest-balanced projection-only beta (0.351) is the
  research baseline and the contest-balanced salary-only gamma (1.251) a
  diagnostic arm. Neither beta nor gamma is refitted per slate. For each
  Showdown contest captured before lock, five arms are scored after the
  game: the served production field untouched, projection only, projection
  plus salary (primary and secondary), salary only. The principal criterion
  is the per-entry NLL of projection+salary against projection-only on the
  representable support, then support coverage, likelihood, chalk rank,
  top-10/100/1,000 mass, the salary-left distribution, captain mix,
  structures, K/DST, effective lineups, top share and heavy-duplication mass,
  all graded without fitting. A contest is scored before any of its counts
  changes anything, and at least three genuinely pre-lock contests come
  before another feature-selection round.
- **If the residual repeats prospectively** (salary mean and tail roughly
  right, a consistent unexplained spike at exactly $0), the next single
  feature is delta times an indicator of salary left equal to zero: pre-game,
  exact, one coefficient, tied to a measured residual. The three retrospective
  boards already warn that it may not transfer (model share at $0 7.69%
  against 13.92% observed on NE @ SEA, 4.68% against 6.67% on DEN @ KC,
  15.00% against 13.24% on DET @ BUF; two boards want a bonus at the cap and
  the third may want the opposite), which is exactly why it waits for
  prospective data. If it is ever fitted, the three checksums are the mean
  projection, the mean salary left and the probability of leaving exactly
  $0; the test remains held-out NLL and the rest of the salary distribution.
- **Recorded, not built:** the cluster bootstrap showed that a 150-max-entry
  contest is not 88,000 independent decisions. If the duplication and
  concentration failure survives the salary model prospectively, one
  hypothesis for a later model-family change is that the field is a mixture
  of user-level lineup-generation strategies rather than IID entries from one
  universal softmax. That is a more principled account of the overdispersion
  than a duplication coefficient, and it is not the next experiment.

S5 stays shut. The sequence is: freeze projection+salary, capture before
lock, score the frozen model, repeat, decide the next feature or family, and
only then the controlled S5 rerun: the same current source with the
placeholder field against the same current source with the prospectively
supported field, identical worlds, universe and seeds, never against the
historical +0.047078.
