# The duplication mechanisms, and a mixture opponent field (2026-11-26)

Modules `research/dk_mech.py` (Phase A, the measurements), `research/dk_mix.py`
(Phase B, showdown) and `research/dk_mix_classic.py` (Phase B, classic).
Aggregates `research/data/dk_mech/`. Research only: no board, no payout and no
EV figure changes; `field_duplication` and every dollar figure stay
experimental.

The owner: "Proceed beyond documenting the discrepancy. Investigate a
mixture-based opponent-field model representing different entrant
construction behaviors ... These are hypotheses, not predetermined model
weights. Fit using available development contests and preserve independent
contests for validation. ... Do not improve duplication artificially by
sacrificing the accuracy of the other measurements." And: "Preserve prior
failed experiments and investigate genuinely new mechanisms rather than
repeating previously rejected approaches."

**Privacy.** The standings exports name every entrant. Handles were held in
memory as integer codes to group one entrant's entries and were written
nowhere. Everything committed is a count, a share, or a lineup as player
names with its number of copies.

**Order of work, for the record.** Sections 0 and B (the pre-registration)
were written and committed before `dk_mix.py` or `dk_mix_classic.py` was run
on any contest. At that moment the Phase A output of the DEVELOPMENT contests
had been read (the four development showdowns and the week-10 Millionaire);
of the validation contests, only what was already public in this repository
(the summaries in `research/data/dk_fields/`, `dk_duplication.md`) and two log
lines of the week-11 Millionaire (its collision and its same-user share of
duplicate pairs). Section A's validation columns and section C were written
after the validation read.

## 0. What was already tried, and is not repeated here

| Study | What it tried | Verdict | What this study takes from it |
|---|---|---|---|
| `cl_field.md`, `cl_sampler.md` (week 1) | the classic sampler's constants beside the real field | collision ~70x too low; the chalk "misranked, not merely noisy" | the definitions (stack, bring-back, collision), reused unchanged |
| `cl_mech.md`, `cl_rank` (week 1) | a price term of either sign (kappa grid), DK-complete FLEX, eleven per-player rankings of projection and salary | FALSIFIED: no region of the per-player family orders the real chalk; every positive kappa worsens salary | "the public's chalk looks like a lineup-construction pattern": components below are LINEUP-level, never a new per-player ranking |
| `cl_own.md` (weeks 1, 6) | per-position pulls on projection and value | FAILED on week 6 | not re-run. Its lead (the public drafts like an optimizer) shipped as ownership steering; the field it proposed -- "lineups optimal under noisy projections, mixed with casual draws" -- was never built. Section B.6 builds exactly that |
| optimizer field, ENGINE 10 (shipped) | the sampler's per-player pulls steered to 0.9 x best-lineup share + 0.1 x calibrated | shipped; "duplicates are still ~100x too rare" | it is the baseline every classic figure here is measured against, rebuilt by production's own code |
| `cl_slot.md`, `cl_week2.md` | slot allocation v1 (FLEX allocation, spend) | PASSED held-out week 2 on its two co-primaries; not in production; did not move the chalk | not re-tested; the salary and FLEX failures it fixes are reported as known |
| `field_refit.py` | the classic constants (max ownership, stacks, bring-back, salary) refit on real fields | pending its own rule (fit 3, test 2) | not touched |
| `s7_field.md`, `s7_beta.md` | the showdown softmax's one knob (beta) fitted by likelihood | the knob is NOT the defect: ordering poor on every board, effective lineups 3.7-11x too many at the fitted beta | no temperature is re-fitted as a finding |
| `s7_salary.md`, `sd_mnf.md` | projection + one linear salary-left term | PASSED held out (0.23-1.01 nats an entry; 0.459 prospective) but "does not reproduce concentration or duplication, the point mass at $0, the captain mix or the 5-1 share" | kept as the BASE of two components below, credited, not presented as new. Its recorded-and-unbuilt hypothesis -- "a mixture of user-level lineup-generation strategies rather than IID entries" -- is model M5 |
| `sd_dup.md` | the knob that matches one night's real collision (0.468%) | descriptive; reads the next night within 11%; "within the softmax family S7 showed is the wrong shape" | duplication is never fitted directly here |
| `dk_duplication.md` | the gap itself: classic 340-520x, showdown 2.4x | documented | the metric, exactly |

## B. The pre-registration (written before any fit)

### B.1 The split

| | Development | Validation |
|---|---|---|
| Showdown | NE @ SEA 193391013, DEN @ KC 195526287, DET @ BUF 195677825 (S7, September; two of the three are post-game reconstructions of the pool), IND @ HOU 197912905 | CIN @ WAS 198005475, GB @ LAR 198150093 |
| Classic | week 10 Millionaire 197645153 | week 11 Millionaire 197882014 |

The validation contests are read by the fitted models ONCE, by
`dk_mix.py validate` / `dk_mix_classic.py validate`, which refuse to run
twice. Caveats fixed now: the S7 universes are "lifted" pools (25-30 projected
players, 1.0-3.5M lineups); the November universes are the served boards'
pools (23-24 players, ~0.6-0.8M) -- a model that only transfers within one
construction will show it. **Classic has ONE development contest and ONE
validation contest. That is a hypothesis test with n = 1 on each side; the
owner's raw exports for weeks 1, 2 and 6 (on the PC) are what a real classic
development set needs.**

### B.2 The hypotheses as components (showdown)

Each component is a distribution over the enumerated legal universe of the
slate, log-linear in LINEUP-level features, so it normalises exactly.

| Component | Hypothesis | Form | New, or prior work |
|---|---|---|---|
| OPT | public optimizers emit the same top lineups | p ~ rank^-a over the lineups ranked by projection; one parameter | NEW (a rank law over lineups; not a temperature) |
| TPL | builders follow construction templates and spend the cap | projection, salary left (S7's two terms) + a point mass at $0 left and at $100-200 left + captain position (5) + team split (2) + flex kickers/defenses + the QB captain's stack count + a non-QB captain with his own QB + both QBs | base: `s7_salary` (prior, kept); the point mass and the templates are NEW (S7 graded them, never fitted them) |
| CON | contrarians avoid the popular build | projection, salary left, minus lambda x the lineup's log popularity (its players' captain/flex ownership under production's field); lambda's sign free and reported | NEW |
| CAS | casual manual entries | projection and salary left only, own coefficients | prior work (S7's family), here as the residual the others must beat |
| portfolios | an entrant has ONE strategy and never repeats a lineup | the ENTRANT draws the component for all his entries; weights depend on his size class (1, 2-19, 20-149, 150 entries); his lineups are distinct | NEW (S7's recorded, unbuilt hypothesis) |

Not built, with the reason: a **near-duplicate core kernel** (an optimizer
lineup with one manual swap). The development contests do not ask for it: the
real fields hold FEWER one-swap pairs per exact pair than the softmax does
(2.9-6.9 against 16-26), so the real concentration is exact, not near.

The ladder (every rung is fitted and reported, so each mechanism has to earn
its place): M0 production's rule, no fit; M1 S7's family refit; M2 = TPL alone;
M3 = the four components, entries iid; M4 = M3 with weights by size class;
**M5 = the entrant-level mixture with distinct portfolios -- the PRIMARY.**

### B.3 The objective

The contest-balanced log-likelihood of the exact lineups entered, over the
representable entries (those whose six players all carry a projection). It is
a proper score of the whole joint distribution: marginal ownership, pairs,
salary, stacks and duplication are functionals of the one object fitted.
**Duplication is not in the objective, and neither is any single measure**, so
none can be bought with another by re-weighting a hand-made loss. No parameter
is tuned on a measure after the fit. Initial values are fixed in the module;
M5 starts from M4, M4 from M3.

### B.4 The measures (each defined in `dk_mech.py` / `dk_mix.evaluate`)

On the representable support, real against model: marginal ownership (captain
and flex MAE over the universe's players, percentage points); joint ownership
(MAE of any-slot pair shares over the real top 20 players' 190 pairs); the
aggregate collision C; the copy-count distribution (share of entries in
lineups with 1, 2-5, 6-20, 21-100, 101+ copies; L1 in points); salary (mean,
and the L1 over six salary-left bands); stacking (team split L1, captain
position L1); the field's real-points distribution (mean absolute difference
of sorted scores, i.e. Wasserstein-1, in DraftKings points); and
INDIVIDUAL-lineup duplication, both ways: the real 20 most-copied builds'
predicted copies, and the model's 20 most-expected builds' real copies.

Model counts: lineup i's expected copies lam_i = sum over entrants of the
chance he holds it; for M5 an entrant with m entries holds it with
probability sum_k w_k (1 - (1 - p_k,i)^m), entrant sizes drawn from the
development contests' pooled distribution (a validation contest's own is not
known before lock). C = (sum lam^2 - sum over entrants q^2) / (N(N-1)).
Copy counts are Poisson around lam.

### B.5 Acceptance: "duplication calibrated" (showdown)

On EACH validation contest, for the primary model M5, all of:

- **A1 aggregate.** model C / real C within [0.5, 2].
- **A2 individual.** Of the real 20 most-copied builds, at least 15 have
  expected copies within a factor 3 of their real copies; AND of the model's
  20 most-expected builds, at least 15 have real copies within a factor 3 (a
  build nobody entered fails). A1 without A2 is aggregate accuracy only and
  will be reported as such.
- **A3 distribution.** The copy-count L1 no worse than production's rule (M0).
- **A4 non-inferiority against M0** on the same universe: captain MAE +0.25
  pt, flex MAE +0.5 pt, joint MAE +0.25 pt, salary mean error +$100,
  salary-left L1 +2 pt, team-split L1 +2 pt, captain-position L1 +2 pt,
  score Wasserstein-1 +0.5 DraftKings points. One failure fails the model.

Passing on both contests would make duplication calibrated on two showdowns,
which is still not a payout model: `SD_MONEY_FIELD_CALIBRATED` and every EV
figure stay experimental until a prospective test on contests captured before
lock, by a rule written then. Failing is a result and is reported as it falls.

### B.6 Classic: the noisy-optimizer cluster (one development week, one validation week)

The classic universe cannot be enumerated, so the showdown likelihood is not
available. The test is of ONE new mechanism, the field `cl_own.md` proposed
and nobody built: **an entry is, with probability w, the exact optimum
(DraftKings' rules only, production's `optimal_lineups`) of the board's
projections each multiplied by 1 + sigma x a standard normal, drawn per player
per entry -- an optimizer run with "randomness" sigma -- and otherwise a draw
of the served optimizer field** (rebuilt by production's sampler,
`dk_mech.classic_replica`). Two parameters, w and sigma.

- **Development (week 10).** sigma in {0.03, 0.06, 0.10, 0.15, 0.20, 0.30}, w
  in {0, 0.1, ..., 1.0}; 60,000 entries a cell, built from fixed pools. The
  cell chosen minimises the SUM of seven normalised errors, equal weights,
  none of them alone: |log2 ratio| of C, of the 8-of-9 pair share and of the
  7-of-9 pair share; and, each divided by the served field's own error on
  week 10, the real-top-40 ownership MAE, the top-30 joint-ownership MAE, the
  salary-mean error and the stack-distribution L1.
- **Validation (week 11), read once**, the chosen cell at the contest's own
  entry count. "Duplication calibrated (classic, one week)" needs: C within
  2x; at least 10 of the real 20 most-copied builds within a factor 3 in the
  model field; and non-inferiority against the served field's replica:
  real-top-40 ownership MAE +0.25 pt, joint MAE +0.10 pt, salary-mean error
  +$50, stack L1 +2 pt, score Wasserstein-1 +2 DraftKings points.
- Reported beside it, as a diagnostic that is NOT a model: the sampler steered
  onto the real field's own per-player ownership (the oracle marginals), which
  says how much of the missing duplication is per-player ownership and how
  much is lineup-level.

Whatever the result, one week each way cannot ship a classic field. It can
say whether the mechanism deserves the owner's weeks 1, 2 and 6 exports.

### B.7 The owner's acceptance criteria (added before any validation contest was read)

The owner, on reviewing the plan: "Before examining validation contests,
numerically define the most-copied-lineup acceptance criterion. Investigate
lineup-level concentration without sacrificing marginal ownership accuracy or
artificially multiplying every lineup's duplication."

Registered here, in a commit of its own, AFTER the development fits were
started and BEFORE either `validate` command was run: no fitted model had
read a validation contest, and the validation columns of section A had not
been opened. These five govern; B.5's and B.6's stricter individual criterion
(15 of 20 within a factor 3) is still reported beside them. On EACH
validation contest, with production's field on the same contest beside every
figure:

1. **Individual recall.** At least 10 of the real 20 most-copied builds are
   among the model's 200 most probable lineups. Showdown: by expected copies
   over the enumerated universe; the real 20 are taken over ALL entries, and a
   build the universe cannot hold is a miss. Classic (a sampled field, where
   nearly every lineup has one copy): the model field's 200 most-copied
   builds at the contest's own entry count, a build with a single copy never
   counting.
2. **Individual counts.** Over those real 20 builds, the median of model
   copies / real copies lies in [0.5, 2.0] (a build the model never holds
   counts as 0).
3. **Aggregate.** C within a factor 2 of real (showdown: on the
   representable support, the only entries the model describes).
4. **Not by inflating everything.** The share of entries in unique lineups
   within 5 percentage points of real; the share of entries in lineups with
   2-10 copies, and with 11+ copies, each within a factor 2 of real.
5. **Non-inferiority of the rest.** Marginal ownership MAE no more than 0.5
   pp worse than production (classic: the real top 40; **showdown has 23-30
   players in a universe, so "top 40" is undefined there and is replaced by
   the captain MAE and the flex MAE over every universe player, each held to
   the 0.5 pp**); salary-used mean within $200 of real or no worse than
   production; no stacking category (classic: the four stack shares and the
   bring-back; showdown: the three team splits and the six captain positions)
   worse than production by more than 2 pp; the field's real-points P50, P90
   and P99 each no worse than production (showdown: exact over the universe,
   no sample).

A model that fails is a result. Nothing is tuned on a validation contest.
