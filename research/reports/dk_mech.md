# The duplication mechanisms, and a mixture opponent field (2026-11-26)

Modules `research/dk_mech.py` (Phase A, the measurements), `research/dk_mix.py`
(Phase B, showdown) and `research/dk_mix_classic.py` (Phase B, classic).
Aggregates `research/data/dk_mech/`. Research only: no board, no payout and no
EV figure changes; `field_duplication` and every dollar figure stay
experimental.

**Verdict. The pre-registered acceptance criteria FAILED in both formats;
duplication is not calibrated and nothing ships.** Showdown: a 33-parameter
entrant-level mixture (optimizer cluster, templates, contrarian, casual,
portfolios) beats production by 0.43-0.59 nats an entry on two held-out
contests and fixes salary ($1,461 and $838 of error to $228 and $71), yet
leaves the aggregate collision where production's rule had it (0.155x and
0.262x of real, from 0.144x and 0.255x) and gives the public's 20 most-copied
builds an eighth of their real copies. Classic: a two-parameter noisy-optimizer
cluster (10% of entries, optimizer "randomness" 10%), fitted on week 10,
lands the held-out week 11's aggregate collision at 0.61x of real (the served
field: 0.0015x) with ownership, salary and score all slightly better -- and
holds 1 of the public's 20 most-copied builds. That is aggregate accuracy
without individual-lineup accuracy, on one week. What the measurements say
(section A): the duplicates are different entrants entering the SAME exact
lineup (0.1-1.8% of showdown duplicate pairs and 9-23% of classic ones are
within one entrant), at the cap, near an optimum that is not our projection's;
and in classic about 85x of the 435-687x gap is plain per-player ownership --
the served field is half as concentrated per player as the public and scores
15-29 real DraftKings points lower. Every dramatic figure here rests on two
classic slates and six showdowns; treat them as hypotheses (sections C.3-C.4).

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

## A. What the real fields do (Phase A)

Every figure below is in `research/reports/dk_mech_tables.md` (all eight
contests, real beside model, each row a figure `dk_mech.py` defines) and in
`research/data/dk_mech/<contest>.json` (with the 20 most-copied builds, the
pair tables and the ownership lists). The definitions, once:

- **lineup**: classic, the set of nine players; showdown, the captain plus
  the set of five flex (`dk_standings.summarize`'s). **C**: sum n(n-1) /
  (N(N-1)) over distinct lineups, N the entries with a lineup.
- **overlap**: classic, players two lineups share, of nine; showdown, slot
  tokens shared, of six, the captain's token being (CPT, player). Pair shares
  by overlap are exact over all N(N-1)/2 pairs.
- **same user**: two entries of one handle (grouped in memory only).
- **model**: the served field through the SAME code. Classic: production's
  sampler re-steered onto the ownership the board stamped
  (`classic_replica`; its collision reads 1.1e-8 and 1.5e-8 against the
  boards' stamped 1.4e-8 and 1.9e-8). Showdown: production's softmax rule
  (top lineup 0.2%) on the contest's universe, compared on the representable
  entries. For the three S7 contests that is the rule on S7's lifted
  universe, not a board served that night.
- **projection**: the board's (Sleeper's line as served). The public may use
  other projections; distance to OUR optimum overstates distance to theirs.
- Sample sizes: 147k entries a classic contest, 47k-126k a showdown. Two
  classic slates and six showdown slates: every "always" below is n = 2 or 6.

### A.1 Classic (weeks 10 and 11, the $2.5M Millionaires)

| | week 10 real | week 10 served field | week 11 real | week 11 served field |
|---|---|---|---|---|
| C | 4.79e-6 | 1.10e-8 (435x low) | 1.00e-5 | 1.46e-8 (687x low) |
| entries in duplicated lineups | 10.7% | 0.16% | 13.7% | 0.20% |
| most copies of one build | 144 | 3 | 234 | 4 |
| pairs sharing 8 of 9 / 7 of 9 | 7.0e-6 / 1.46e-4 | 1.1e-7 / 3.6e-6 | 1.65e-5 / 3.09e-4 | 1.3e-7 / 4.3e-6 |
| one-swap pairs per exact pair | 1.5 | 9.6 | 1.6 | 8.7 |
| entries with ANOTHER entrant's lineup within 0 / 1 / 2 swaps | 9.4 / 23.8 / 71.0% | 0.16 / 1.5 / 26.2% | 12.3 / 28.5 / 73.7% | 0.20 / 1.6 / 27.4% |
| mean players two entries share (= sum of ownership^2) | 1.53 | 0.87 | 1.65 | 0.88 |
| most-owned player | 64.8% | 17.1% (his) / 29.9% (the field's top) | 70.7% | 29.3% |
| ownership MAE, real top 40 (field / opt / casual) | | 6.39 / 6.31 / 7.75 | | 7.23 / 6.89 / 9.57 |
| salary mean / at the cap / $48,000 or less | $49,868 / 47.5% / 0.2% | $49,267 / 37.2% / 13.2% | $49,874 / 48.5% / 0.2% | $49,220 / 35.6% / 14.8% |
| stack 0 / 1 / 2 / 3+; bring-back | 15.5 / 53.8 / 28.9 / 1.7; 34.5% | 17.4 / 49.0 / 28.8 / 4.8; 34.9% | 12.7 / 51.1 / 33.5 / 2.7; 44.0% | 17.4 / 49.0 / 28.8 / 4.8; 34.9% |
| FLEX RB / WR / TE | 46 / 30 / 23% | 39 / 61 / 0% | 56 / 29 / 15% | 47 / 53 / 0% |
| median projection gap to OUR optimum | 17.7 pts | 24.8 | 19.3 | 26.8 |
| entries within 12 pts of our optimum | 16.1% | 0.9% | 7.8% | 0.3% |
| mean REAL points of the field | 156.1 | 127.2 | 137.7 | 122.5 |

1. **Who duplicates: different entrants, not one entrant's portfolio.** Of
   all duplicate pairs, 8.8% (week 10) and 23.4% (week 11) are two entries of
   the same entrant; week 11's share is two entrants who each entered ONE
   lineup ~150 times (150 and 147 copies, one entrant each). Cross-user C
   alone is 4.37e-6 and 7.69e-6. 150-max entrants hold 16% of entries and
   13-14% of the duplicated ones; all but those two repeat almost nothing, and
   their lineups share 2.2-2.3 players pairwise against the field's 1.5-1.6
   (a portfolio is built around a core, but it does not repeat itself).
2. **The concentration is exact, not near.** The real field holds 1.5 one-swap
   pairs per exact pair; the sampler's field holds 9. A smooth per-player
   sampler cannot do that at any temperature: identical nine-man lineups from
   different entrants are the signature of a deterministic construction (an
   optimizer's output, or a published lineup), not of similar tastes.
3. **The most-copied builds look like optimizer output.** 16 and 13 of the
   top 20 sit exactly at $50,000 (the field: 48%), all 144 copies of week
   10's top build are at the cap; the duplicated lineups sit far nearer our
   optimum than the unique ones (week 10 median gap 18.3 points for unique
   lineups, 13.3 at 2-5 copies, 8.3 at 21-100; within 10 points 5.7% / 25% /
   65%) -- but NOT at it: our own optimum has 0 copies both weeks, and the
   top 20 builds sit a median 8.8 and 12.0 points under it. They are an optimizer's output on
   SOMEBODY ELSE'S projections.
4. **Most of the missing duplication is per-player ownership, and the rest
   is lineup-level.** The diagnostic that hands production's sampler the real
   field's own per-player ownership (the oracle marginals; not a model)
   raises C from 1.1e-8 to 9.6e-7 (week 10) and from 1.5e-8 to 1.2e-6 (week
   11): about 85x of the 435-687x. What is left -- 5.0x and 8.2x (6.3x
   against week 11's cross-user C) -- no per-player ownership can supply. The
   served field's ownership is far too flat: the sum of squared ownership is
   0.87-0.88 against 1.53-1.65, and one $4,700 back was 64.8% and 70.7%
   owned against 17.1% and 29.3%. The same diagnostic moves the field's mean
   real score from 127 to 153 (real 156) and from 123 to 136 (real 138): **the
   served field scores 15-29 DraftKings points under the real one**, and that
   is ownership too. This is the larger finding for payouts: a field that
   weak makes every candidate's cash line and EV read high.
5. **Correlated ownership is roughly right in kind.** QB-catcher lifts are
   real (Purdy-Kittle 3.8x, Lawrence-Washington 3.3x, Shough-Olave 3.1x) and
   the sampler makes them, if anything too strongly (6.4x, 3.6x, 4.2x). The
   top-30 pair shares are off by 1.9-2.3 points on a 2.9-3.3 point mean, and
   that is the marginals again: the oracle cuts it to 0.7. Week 11's
   bring-back rate (44%) is above the constant (35%); week 10's is on it.
6. **Salary and FLEX**: known (`cl_slot.md`): 13% of the served field leaves
   $2,000+ where 0.2% of the public does, and the sampler never plays a tight
   end at FLEX (the public: 23%).
7. **Where duplicated lineups finish** (one slate each, descriptive): week
   10's 21-100-copy builds averaged 159.8 points against the uniques' 155.2
   and its one 144-copy build scored 143.5 (no cash); week 11's 21+ copy
   builds averaged 158-169 against 135, 44-46% of them in the top 10%.
   Chalk wins when the chalk hits; that is the week, not a rule.

### A.2 Showdown: the development contests (written before the fit)

| | NE @ SEA | DEN @ KC | DET @ BUF | IND @ HOU |
|---|---|---|---|---|
| entries / entrants | 125,800 / 42,368 | 87,909 / 26,460 | 88,023 / 26,348 | 65,073 / 19,696 |
| C real (all entries) | 4.58e-4 | 2.84e-4 | 6.07e-4 | 8.04e-4 |
| C real / production's rule, same support | 2.3x | 2.4x | 4.4x | 7.8x |
| entries in duplicated lineups, real / model | 93.1 / 88.9% | 90.0 / 78.4% | 92.9 / 83.1% | 91.7 / 75.5% |
| entries in 101+ copy builds, real / model | 17.9 / 5.7% | 4.2 / 1.0% | 12.3 / 1.0% | 15.4 / 0.4% |
| same-user share of duplicate pairs | 0.12% | 0.19% | 0.09% | 0.36% |
| one-swap pairs per exact pair, real / model | 6.9 / 15.9 | 6.8 / 20.4 | 2.9 / 14.2 | 6.4 / 25.8 |
| entries in OUR projection's top 1,000, real / model | 22.5 / 34.2% | 14.6 / 25.5% | 9.6 / 30.2% | 33.2 / 23.9% |
| copies of OUR optimum, real / model expects | 136 / 252 | 56 / 176 | 18 / 176 | 451 / 130 |
| $0 left: unique lineups / 101+ copy builds | 0.1 / 42.6% | 0.3 / 24.4% | 2.5 / 29.7% | 1.3 / 18.6% |
| salary mean, real / model | $48,712 / $47,821 | $48,928 / $47,586 | $49,373 / $48,446 | $48,880 / $47,157 |
| salary left $2,100+, real / model | 19.7 / 40.3% | 15.1 / 45.8% | 4.6 / 27.2% | 16.6 / 52.2% |
| 5-1 split, real / model | 13.5 / 11.1% | 15.0 / 13.1% | 15.9 / 10.9% | 38.2 / 21.3% |
| QB captain, real / model | 20.1 / 35.7% | 27.8 / 41.9% | 24.2 / 16.2% | 30.2 / 41.7% |
| QB captain with 2+ of his catchers, real / model | 60.9 / 45.1% | 58.9 / 43.8% | 70.5 / 32.2% | 77.7 / 44.7% |

1. **Showdown duplication is almost entirely cross-user**: 99.6-99.9% of
   duplicate pairs are two different entrants. 90%+ of entries share their
   exact lineup with someone else. Single-entry entrants are the MOST
   duplicated (mean 34-95 copies of their lineup) and 150-max entrants the
   least (15-28): the portfolios spread, the casual entrants converge.
2. **Convergence is not onto our projection's head.** On three of four
   contests the real field puts LESS of itself in our projection's top 1,000
   lineups than production's softmax does, and our optimum is under-copied
   (18-136 real against 176-252 expected); on IND @ HOU it is the other way
   (451 against 130). The most-copied 20 builds sit at median projection rank
   191-9,074 and production expects 2.5-36 copies of a build the public
   entered 146-290 times. The public's shared ranking is not ours (S7's
   "ordering is poor", again).
3. **What the copied builds have in common is the cap.** 11-18 of each top
   20 spend $49,800+, and lineups with $0 left are 19-43% of the 101+ copy
   builds against 0.1-2.5% of the unique ones. Then construction: the real
   field captains fewer quarterbacks and more backs, stacks a QB captain with
   two or more of his catchers 59-78% of the time (the softmax: 32-45%), and
   on a lopsided game (IND @ HOU) plays 5-1 38% of the time (21%).
4. **Exact, not near**, as in classic: 2.9-6.9 one-swap pairs per exact pair
   against the softmax's 14-26.
5. Pair lifts in a two-team showdown are small (mean |log lift| 0.15-0.17
   real, 0.11-0.12 model); joint-ownership error (2.8-3.8 points) is mostly
   the flex marginals (MAE 4.9-7.7).

### A.3 The hypotheses, as Phase A leaves them

| The owner's hypothesis | Verdict on these contests |
|---|---|
| public optimizers generating identical lineups | SUPPORTED in kind (exact duplicates across entrants, at the cap, near-optimal), but the optimum is not OUR projection's: 0 copies of our classic optimum, and showdown mass in our top 1,000 is below even the softmax's on 3 of 4 |
| correlated player ownership | pairs are roughly right once marginals are; the big error is the MARGINALS (classic sum of ownership^2 0.87 against 1.6) |
| popular stack combinations | SUPPORTED in showdown (QB-captain stacks, the cap, 5-1 onslaughts); classic stack rates are within ~5 points |
| excessive randomness in the simulated field | SUPPORTED, strongly, classic: the served field is 7 points further from the optimum at the median and 15-29 real points weaker |
| salary utilization | SUPPORTED (classic 13% of the model field leaves $2,000+; showdown 27-52% against 5-20%) |
| multi-entry behaviour | NOT the cause of duplication: 0.1-0.4% of showdown duplicate pairs and 9-23% of classic ones are within one entrant; max-entry portfolios are the LEAST duplicated |
| common construction templates | SUPPORTED in showdown (captain mix, stack depth, splits) |

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

## C. Results (the fits, then the validation contests, read once)

Commits, in order: the Phase A code and data with section B (before any fit);
section B.7 (the owner's criteria, before any validation read); each frozen
development fit; then this section. The first showdown fit was stopped by the
session's 30-minute background limit during M5 and re-run unchanged (the same
module, the same seeds; the trace lines it had logged repeated to the digit). One
correction to B.2's wording, the numbers there being typed from memory: the
softmax holds 14.2-25.8 one-swap pairs per exact pair on the four development
contests, not "16-26"; the reason given for not building the near-duplicate
kernel stands.

### C.1 Showdown: the mixture FAILED. It learned where the public shops, not which lineups it piles into

**Development fit** (`research/data/dk_mech/mix_fit.json`; four contests,
343,452 representable entries; contest-balanced negative log-likelihood per
entry, nats):

| | M0 production's rule | M1 S7's family | M2 + point mass + templates | M3 four components | M4 + weights by size | M5 entrant-level (primary) |
|---|---|---|---|---|---|---|
| NLL | 10.242 | 9.669 | 9.496 | 9.467 | 9.437 | 9.373 |
| parameters | 0 | 2 | 15 | 24 | 33 | 33 |

Every rung earns likelihood, in sample. (M3 and M5 were still moving in the
fourth decimal at the 80-iteration cap fixed beforehand.) What the fitted
hypotheses say:

- **The optimizer cluster on OUR projections is tiny.** rank^-0.78, with 0.3%
  of single-entry entrants and 2.3% of 150-max entrants. Bigger portfolios
  use it more, as an optimizer story predicts, but it holds almost none of
  the field.
- **Templates carry the field, and more of it the bigger the entrant**: 35%
  of single entrants, 40% at 2-19 entries, 59% at 20-149, 76% at 150. Its
  coefficients are large: a non-QB captain +0.5 (a kicker) to +1.4 against the
  quarterback at equal projection (the 1.5x projection over-credits a QB
  captain), each catcher stacked with a QB captain +0.87, a non-QB captain
  with his own quarterback +1.17, a 5-1 split +0.62, each $1,000 left -0.68.
- **"Casual" is not loose, it is cap-driven**: 58% of single entrants sit in a
  component with -2.43 a $1,000 left and a small projection weight (0.21).
  The casual public's rule is "spend all of it".
- **The contrarian hypothesis is not supported as specified.** Its popularity
  coefficient came out POSITIVE (+0.80; +2.36 with iid entries): the
  component became "popular players, loosely", i.e. chalk-seeking. 7-17% of
  entrants.
- **Entrant-level strategies are real** in likelihood terms (M5 beats M4 by
  0.064 nats an entry with the same parameters), but see below: they do not
  produce the duplication.

**And already in sample the concentration is not there**: M5's C is 0.56,
0.39, 0.17 and 0.25 of the real one on the four development contests
(production's rule: 0.43, 0.41, 0.23, 0.12).

**Validation** (`mix_validation.json`; CIN @ WAS: 24 players, 769,040
lineups, 63,663 of 70,488 entries representable, 90.3%; GB @ LAR: 24 players,
784,884 lineups, 44,048 of 46,864, 94.0%). Production's rule (M0) beside the
primary (M5); the real figure where it is not an error:

| The owner's criterion (B.7) | CIN @ WAS: production | CIN @ WAS: M5 | GB @ LAR: production | GB @ LAR: M5 |
|---|---|---|---|---|
| 1. real top 20 in the model's top 200 (need 10) | 15 | **20 PASS** | 6 | **10 PASS** |
| 2. median model / real copies of those 20 (need 0.5-2) | 0.110 | 0.130 FAIL | 0.104 | 0.125 FAIL |
| 3. C, model / real (need 0.5-2) | 0.144 | 0.155 FAIL | 0.255 | 0.262 FAIL |
| 4. entries in unique lineups (real 7.0%; 10.1%), within 5 pp | 21.1% | 14.2% FAIL | 23.3% | 16.4% FAIL |
| 4. entries in 2-10 copy builds (real 27.8%; 36.7%), within 2x | 49.1% | 51.9% pass | 53.7% | 54.8% pass |
| 4. entries in 11+ copy builds (real 65.2%; 53.3%), within 2x | 29.7% | 33.4% pass (1.95x) | 23.0% | 28.4% pass (1.87x) |
| 5. captain / flex ownership MAE, pp (no more than +0.5) | 2.16 / 4.26 | 1.01 / 5.55 FAIL (flex +1.29) | 1.88 / 5.36 | 1.85 / 4.53 pass |
| 5. salary mean error (within $200, or no worse) | $1,461 | $228 pass (no worse) | $838 | $71 pass |
| 5. worst category against production (no more than +2 pp) | | TE captain 4.1 vs 0.5 FAIL | | RB captain 9.3 vs 4.2, QB 8.4 vs 5.1, TE 3.9 vs 0.7 FAIL |
| 5. real-points P50 / P90 / P99 error (no worse) | 0.6 / 0.2 / 0.3 | 1.8 / 1.0 / 0.3 FAIL | 4.9 / 4.6 / 5.6 | 9.4 / 8.5 / 7.3 FAIL |
| held-out NLL per entry (not a criterion) | 9.420 | 8.831 | 9.708 | 9.282 |
| B.5 A2: real top 20 within 3x (need 15) | 1 | 1 FAIL | 1 | 0 FAIL |

**Verdict: no model on the ladder, M0 to M5, passes, on either contest**
(`duplication_calibrated` and `owner_criteria_all_pass` are false for all
six). Duplication is NOT calibrated. Read rung by rung, the result is
specific:

- The mixture transfers as a description of single entries: 0.59 and 0.43
  nats an entry better than production held out, salary error cut from
  $1,461 and $838 to $228 and $71, the team split to within 1.2 and 2.5
  points, and the real most-copied builds are now among its 200 likeliest
  (20 of 20 and 10 of 20).
- **It moves the aggregate collision by nothing** (0.144 -> 0.155, 0.255 ->
  0.262) and gives the public's top builds an eighth of their real copies
  (0.13). The real fields put 65% and 53% of entries in builds with 11+
  copies; the mixture, 33% and 28%. Aggregate and individual fail together
  here: there is no hidden pass on one and not the other.
- It was not bought with the other measures, but two of them got worse
  anyway: flex ownership on CIN @ WAS (+1.3 pp) and the captain mix and the
  real-points quantiles on GB @ LAR (the fitted captain template, tuned on
  four games, put 23% of captains on the quarterback where that field had
  15%). A template fitted on four slates does not transfer cleanly to a
  fifth.
- So: salary, templates and entrant-level strategies on OUR projections tell
  us WHERE the public shops and do not tell us WHICH few hundred lineups it
  piles into. The optimizer component is the one that could, and on our
  projections it finds 0.3-2.3% of the field. What is missing is the
  ordering the public shares, not another shape parameter.

### C.2 Classic: the noisy-optimizer cluster reproduces the AGGREGATE on the held-out week, and none of the real builds

**Development (week 10)**, 66 cells of 60,000 entries
(`mix_classic_fit.json`): the chosen cell is **sigma 0.10, w 0.10** (loss 6.53
against the served field's ~24). At that cell C is 2.9e-6 (0.61 of real), the
8-of-9 pair share 6.9e-6 (0.98) and the 7-of-9 share 9.1e-5 (0.62) -- three
concentration levels at once from two parameters -- with the real-top-40
ownership MAE 6.00 (served 6.34), the joint MAE 1.72 (1.89) and the salary
mean $49,327 (served $49,261, real $49,868). The stack distribution got worse
(L1 24 against 10): an optimizer with no stack rule plays a naked quarterback
far more often than the public does. The surface is steep: at sigma 0.06 the
same 10% gives C 4.8e-5 (10x too high), at 0.15, 1.5e-7 (30x too low).

**Validation (week 11, read once;** `mix_classic_validation.json`, 147,015
entries**):**

| The owner's criterion (B.7) | real | served field | noisy-optimizer mixture | |
|---|---|---|---|---|
| 1. real top 20 among the field's 200 most-copied (need 10) | | 0 | 1 | FAIL |
| 2. median copies / real copies over those 20 (need 0.5-2) | | 0 | 0 | FAIL |
| 3. C (need within 2x) | 1.00e-5 | 1.46e-8 (0.0015x) | 6.16e-6 (0.61x) | **PASS** |
| 4. entries in unique lineups, within 5 pp | 86.3% | 99.8% | 94.3% | FAIL (8.0 pp) |
| 4. entries in 2-10 copy builds, within 2x | 11.3% | 0.2% | 3.6% | FAIL (3.1x) |
| 4. entries in 11+ copy builds, within 2x | 2.36% | 0% | 2.05% | pass |
| 5. real-top-40 ownership MAE (no more than +0.5 pp) | | 7.25 | 6.93 | pass |
| 5. salary mean error | | $655 | $584 | pass (no worse) |
| 5. worst stack category against the served field (+2 pp) | | no-stack 4.6, bring-back 9.1 | no-stack 10.5, bring-back 11.2 | FAIL |
| 5. real-points P50 / P90 / P99 error | | 15.5 / 16.9 / 14.6 | 13.2 / 13.3 / 11.3 | pass |
| pairs sharing 8 of 9 / 7 of 9 (not a criterion) | 1.65e-5 / 3.09e-4 | 1.3e-7 / 4.3e-6 | 9.5e-6 / 1.44e-4 | 0.58x / 0.47x |

**Verdict: not calibrated** (B.6's own criteria fail on the individual builds
and the stack distribution as well). But this is the first field in this
repository whose aggregate collision landed within 2x of a real classic
contest it was not fitted on -- 0.61x, from 0.0015x, with the same two
numbers that gave 0.61x on the development week -- and it did so while the
ownership MAE, the pair shares, the salary error and the score quantiles all
moved the right way. **It is exactly the case the owner asked to be told
apart: aggregate collision accuracy WITHOUT individual-lineup accuracy.** Of
the public's 20 most-copied builds (29-234 copies), 19 do not exist in the
model field at all; the model's duplicated mass sits on OUR projection's
near-optimal lineups, which the public did not enter (our optimum: 0 real
copies, both weeks). Its copy-count distribution is also the wrong shape:
too many uniques, a third of the 2-10 copy mass, about the right 11+ mass --
a few heavily copied wrong builds instead of many lightly copied right ones.
One development week and one validation week: a hypothesis that survived one
contact, nothing more.

### C.3 What the two formats say together

1. **Optimizer convergence: supported as a mechanism, and it is not on our
   projections.** Exact duplicates across different entrants, at the cap,
   nearer the optimum the more they are copied; a 10% low-noise optimizer
   cluster reproduces the classic aggregate at three overlap levels on a
   held-out week. But the public's builds are another projection's optimum:
   1 of 20 found in classic, an eighth of the copies in showdown.
2. **Multi-entry: not supported as the cause.** 0.1-1.8% of showdown duplicate
   pairs and 9-23% of classic ones are within one entrant; 150-max portfolios
   are the least duplicated class in every contest. (Week 11's 23% is two
   entrants entering one lineup ~150 times each.) Entrant-level strategies
   improve the likelihood and do nothing for duplication.
3. **In classic, most of the gap is plain per-player ownership.** The oracle
   marginals recover ~85x of the 435-687x on both weeks and almost all of the
   15-29 point deficit in the field's real score. The remaining 5-8x is
   lineup-level, and it is the part an optimizer cluster supplies.
4. **Neither format can be fixed by a shape parameter on Sleeper's line.**
   That is now measured three ways: S7's knob, this mixture's 33 parameters,
   and the classic cluster's individual-build miss.

### C.4 What stays experimental, and what would change it

Nothing here changes a board. `field_duplication`, `money_gate`,
`sd_money_gate`, every first-place, cash and EV figure stay experimental, as
`dk_duplication.md` section 4 left them: the acceptance criteria were written
first and they failed. In particular nobody should scale the board's copies by
435, 687 or 7: the missing copies are on lineups the model does not rank.

Recommendations, each needing the owner's call or a rule written before its
data (none acted on here):

1. **Get the public's ordering, before lock.** The one input that would let
   the optimizer cluster name the right builds is a consensus projection the
   public's optimizers actually run on. A capture of one or two public
   projection sets beside each board (the terms of each source checked first)
   is a data decision for the owner; the test is criterion 1 above, on
   contests captured before lock. No retrospective source exists for the
   eight contests read here.
2. **Classic ownership first.** The largest measured error in the classic
   field is that it is 15-29 real points too weak and half as concentrated
   per player as the public; that is what moves payout and EV, and it is
   upstream of duplication. The served field puts a
   $4,700 starting back at 17% and 29% where the public had 65% and 71%.
   Whether a LOW-noise optimizer share is a better steering target than the
   hindsight-best share is a per-player question with a clean test (the
   real-top-40 MAE, weeks not yet read).
3. **An optimizer cluster with the public's stack rule** (quarterback with at
   least one catcher), pre-registered again and judged on contests not read
   here: this run's stack failure is the mechanism's, not the public's.
4. **The classic development set.** One week each way is n = 1. The raw
   exports for weeks 1, 2 and 6 are on the PC; with them the cluster's two
   parameters can be fitted on three weeks and weeks 10-11 kept as the test.
5. **Showdown templates need more than four slates**, or fewer parameters:
   the captain mix transferred to one validation game and not the other.

### C.5 Files

- `research/dk_mech.py` -- Phase A, re-runnable on the exports
  (`python3 -m research.dk_mech <exports dir> [--boards DIR]`);
  `research/dk_mech_tables.py` -> `research/reports/dk_mech_tables.md`.
- `research/dk_mix.py` (showdown, `fit` / `validate`),
  `research/dk_mix_classic.py` (classic, `fit` / `validate`).
- `research/data/dk_mech/`: `<contest>.json` (eight), `pool_<dg>.json`
  (DraftKings' public salary pools, cached so a re-run needs no network),
  `mix_fit.json`, `mix_validation.json`, `mix_classic_fit.json`,
  `mix_classic_validation.json`.
- Not reproducible from the repository alone: the raw exports (they name
  entrants and stay out of git) and, for the three S7 contests, the committed
  S7 captures and feeds that `research/s7_beta.lifted_pool` reads.
