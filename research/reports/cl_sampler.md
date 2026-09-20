# Task 8, second artifact: the field the classic sampler actually produces, beside the field that entered

Artifact `research/data/cl_sampler.json` (module `research/cl_sampler.py`).
Two realised fields against the week-1 $3.5M Millionaire's 831,028 active
entries (the first artifact, `research/data/cl_field.json`): the SERVED
field, production's own pre-lock build of draft group 151307 (built
2026-09-13 16:27:24 UTC, 32.6 minutes before lock; unseeded; 300,000 draws
after a calibration on 20,000; pulled by the error-log workflow into the
sim-history branch that day and pinned here by hash), and the RE-RUN, the
untouched sampler on exactly the served pool of 240 players (216 the roster
gate kept plus 24 field-only, every one reconciled to the pinned draft group
by salary, team and opponent) at the real field's size, 831,028 draws, under
five fixed seeds, each calibrating afresh the way a build does. Nothing is
fitted to the real field; calibrate_field aims at CL_FIELD_MAX_OWN and
CL_SALARY_USED, which is production's mechanism with production's targets.
No production change follows from this artifact: one week-1 Millionaire is
one slate, and this artifact earns a diagnosis, not a constant swap.

Every table keeps four things apart: the TARGET (an input constant), the
SERVED realisation, the RE-RUN realisation (mean, sd over the five seeds with
ddof 1, min and max) and the REAL field. The five re-runs are stochastic
reproducibility, not five independent validations: same slate, same pre-lock
inputs, same public field; they say whether the served miss is stable under
sampler randomness, and nothing more.

**Verdict. Does the untouched classic field sampler, on the exact production
pre-lock input pool, reproduce the real public field at the levels that
matter for tournament economics? No. It realises its aggregate inputs about
as set and reproduces itself across seeds to 0.06 pp per player, but the
field it produces is wrong where duplication and lineup popularity are
decided: the player-level chalk ordering (Spearman 0.32 over the real top 50;
its own top 10 hold two thirds of the real mass the real top 10 hold; single
misses of 26 and 21 points of ownership), the construction (a tight end at
FLEX never against 21.4% of the public; one forced punt in 77% of lineups
against 48%; $48,000 or less in 11.7% against 0.6%) and the concentration
(distinct-entry collision 2.1e-8 against the real 1.30e-6, two orders of
magnitude). The pool is not the reason: it covers 99.7% of the real roster
slots.**

## 1. Provenance: content identity, not a waiver

The seed partials were produced at commit 0e4c20a and HEAD moved for
unrelated work (UFC 331) before assembly. The assembler accepts them only on
content identity, every check recorded in `meta.content_identity`: the
partials' stored source hash equals the hash recomputed from 0e4c20a's tree
(45 files); `dfs_tourney.py`, `research/cl_field.py`, the pinned board, its
manifest and the first artifact are byte-identical between 0e4c20a and now;
this module's producing functions and constants (served_board, served_pool,
grading_pool, lineups_from_idx, sample_field, _rankdata, compare_ownership,
grade, real_side, run_seed; SEEDS, FIELD_N, CAL_N, CHUNK, TOP_TABLE) are
identical by the AST, while assembly-only code may change; each partial's
seed, sizes, chunk and targets match this module. Any one difference refuses
assembly (a partial re-stamped at a commit where the module did not exist,
and a partial with a changed chunk, are both refused by the guard's own
test). The partials' stamps are kept as written; the assembly carries its own
clean stamp.

## 2. Aggregates: targets, served, re-run, real

| Quantity | Target (input) | Served | Re-run mean ± sd [min, max] | Real |
|---|---|---|---|---|
| Most-owned player, any slot (%) | 38.0 | 39.4 | 39.9 ± 0.76 [39.5, 41.3] | 42.8 |
| Mean salary used ($) | 49,400 | 49,281 | 49,303 ± 52 [49,272, 49,395] | 49,850 |
| Share at exactly $50,000 (%) | | | 31.2 ± 1.3 [30.5, 33.5] | 49.9 |
| Share at $49,500 or more (%) | | | 71.3 ± 1.8 [70.2, 74.6] | 94.7 |
| Share at $48,000 or less (%) | | | 11.7 ± 1.1 [9.7, 12.4] | 0.6 |
| Salary p10 / p25 ($) | | | 47,780 / 49,300 | 49,600 / 49,800 |
| Stack: naked QB / one / two / three-plus (%) | 17.4 / 49.0 / 28.9 / 4.7 | 17.4 / 49.1 / 28.8 / 4.7 | 17.4 / 49.0 / 28.9 / 4.7 (sd under 0.07) | 19.2 / 53.0 / 26.1 / 1.7 |
| Bring-back, all entries (%) | 35.0 | 34.9 | 35.0 ± 0.05 | 32.2 |
| Defense against own QB (%) | 8.0 is the share NOT steering round it | 0.381 | 0.368 ± 0.009 | 0.495 |
| Defense against one of the lineup's backs (%) | | | 10.4 ± 0.05 | 2.4 |
| FLEX is a RB / WR / TE (%) | | | 29.5 / 70.5 / 0.0 | 42.5 / 36.1 / 21.4 |
| Punts under $3,000: none / one / two-plus (%) | | | 14.4 / 77.3 / 8.4 | 36.6 / 48.4 / 15.0 |
| Players from one team (non-DST), max 2 / 3 / 4+ (%) | | | 53.5 / 32.7 / 7.7 | 52.8 / 34.9 / 5.8 |
| Players from one game, max 3 / 4 / 5+ (%) | | | 44.3 / 19.4 / 5.2 | 39.4 / 21.3 / 8.3 |
| Distinct lineups (of 831,028) | | | 827,104 ± 642 | 773,891 |
| Most-copied lineup (copies) | | 0.015% of 300,000 | 28.6 ± 5.9 [22, 38] | 346 (0.042%) |
| Entries in unique lineups / in lineups with 51+ copies (%) | | | 99.2 / 0.0 | 89.8 / 0.5 |
| Plug-in HHI (descriptive; floor 1/N = 1.20e-6) | | | 1.22e-6 ± 4.9e-9 | 2.50e-6 |
| Effective lineups, 1/HHI | | | 816,597 | 400,044 |
| Distinct-entry collision (the constant's definition; dfs_tourney._collision) | 1.2e-7 | 4.5e-8 (on 300,000) | 2.13e-8 ± 4.9e-9 [1.77e-8, 2.98e-8] | 1.30e-6 |
| Effective lineups by pairs, 1/collision | 8.3 million | 22.2 million | 48.6 million | 771,367 |

Reading the aggregates. The drawn rates (stack distribution, bring-back)
are realised exactly as set, and the defense-against-own-QB constant, once
read as the share that does not steer round it, realises at 0.37% against
the public's 0.50%: the first artifact's "0.08 against 0.005" was a target
beside a realisation and is resolved here, not a sixteen-fold miss. The two
calibration targets are not met on this pool and land short of the public on
both: the calibration's least-combined-miss compromise gives 39.9% where the
target is 38% and the public 42.8%, and $49,303 where the target is $49,400
and the public $49,850. Everything the constants do not set, the sampler's
mechanics decide, and there it diverges from the public at every row: no
tight end at FLEX ever, a WR at FLEX twice as often as the public, one
sub-$3,000 filler in three lineups of four where the public plays none in
one of three, a salary distribution with a fat tail below $48,000 the public
does not have, a defense facing one of its own backs four times as often,
and a field of near-unique lineups where the public's has 346 copies of its
favourite and eleven times the sampled lineups' collision rate (sixty times
the re-run's). Both collision definitions are kept apart everywhere: the
distinct-entry statistic is the one compared, the HHI is descriptive.

## 3. Player-level ownership: the chalk is misranked, not merely noisy

Over the sampler's 240-player pool, any slot, pct of lineups:

| Metric | Served | Re-run mean ± sd [min, max] |
|---|---|---|
| MAE (pp) | 2.159 | 2.164 ± 0.021 [2.151, 2.201] |
| RMSE (pp) | 3.815 | 3.830 ± 0.061 |
| Bias (pp) | 0.007 | 0.010 |
| Pearson | 0.751 | 0.750 ± 0.006 |
| Spearman, all 240 | 0.857 | 0.854 ± 0.008 [0.839, 0.859] |
| Spearman, real top 50 | 0.316 | 0.318 ± 0.004 [0.312, 0.322] |
| Real top 50: MAE / RMSE / bias (pp) | 5.23 / 6.96 / −3.57 | 5.23 / 6.98 / −3.54 |
| Real top 20: MAE / RMSE / bias (pp) | 8.37 / 9.96 / −6.30 | 8.35 / 9.97 / −6.28 ± 0.06 |
| Largest over / under (pp) | +20.7 (Cardinals DST) / −25.9 (Michael Mayer) | same players, same size |
| Re-run against served: MAE / Spearman | | 0.058 pp / 0.9992 |

Real ownership mass captured by the sampler's own top players (share of
real roster slots; the same to three decimals on every seed):

| Sampler's top k | Real slots they hold | Real slots the real top k hold | Capture ratio | Overlap |
|---|---|---|---|---|
| 10 | 17.5% | 26.2% | 0.665 | 3 of 10 |
| 20 | 30.4% | 41.0% | 0.743 | 9 of 20 |
| 50 | 57.1% | 70.3% | 0.812 | 35 of 50 |

Calibration by modelled ownership (served; the re-runs agree within a few
tenths): under 1%: 48 players, modelled 0.55% against real 0.10%; 1 to 2%:
61 players, 1.46 against 1.29; 2 to 5%: 80 players, 3.05 against 3.60; 5 to
10%: 32 players, 6.88 against 8.29; 10 to 20%: 14 players, 13.0 against
11.0; 20% and over: 5 players, 27.4 against 21.5. The sampler is
over-confident at both ends and under in the middle: it pushes its chalk
past the public's and starves the 5 to 10% tier where the public's mid-priced
plays live.

The real top 20, with the export's slot split (pct at that roster slot):

| Player | Pos, salary | Real (slot split) | Served | Re-run |
|---|---|---|---|---|
| Jahmyr Gibbs | RB $8,000 | 42.8 (RB 38.6, FLEX 4.2) | 39.4 | 39.9 |
| Ja'Marr Chase | WR $7,800 | 30.2 (WR 28.3, FLEX 1.8) | 29.4 | 29.6 |
| Michael Mayer | TE $2,900 | 27.5 (TE 20.8, FLEX 6.7) | 1.6 | 1.6 |
| Chris Olave | WR $6,700 | 24.8 | 11.9 | 11.9 |
| Saquon Barkley | RB $6,900 | 21.1 (RB 16.3, FLEX 4.8) | 9.7 | 9.8 |
| Omarion Hampton | RB $6,800 | 19.9 (RB 15.6, FLEX 4.2) | 9.1 | 8.9 |
| De'Von Achane | RB $7,000 | 18.4 | 10.7 | 10.7 |
| Amon-Ra St. Brown | WR $7,500 | 17.8 | 23.8 | 23.9 |
| Jaguars | DST $3,400 | 17.8 | 2.3 | 2.2 |
| Garrett Wilson | WR $5,800 | 15.8 | 7.2 | 7.1 |
| Ladd McConkey | WR $5,900 | 15.1 | 6.8 | 6.7 |
| Bijan Robinson | RB $7,700 | 14.5 | 21.9 | 21.9 |
| Jonathan Taylor | RB $7,300 | 13.5 | 18.5 | 18.5 |
| Juwan Johnson | TE $3,700 | 13.1 (TE 11.2, FLEX 1.9) | 2.9 | 3.0 |
| Bucky Irving | RB $5,700 | 12.9 | 5.2 | 5.1 |
| Colston Loveland | TE $5,300 | 12.9 (TE 11.2, FLEX 1.7) | 9.9 | 10.0 |
| Quentin Johnston | WR $4,700 | 12.7 | 4.0 | 4.0 |
| Tetairoa McMillan | WR $6,100 | 12.6 | 8.3 | 8.3 |
| Justin Jefferson | WR $7,300 | 12.6 | 14.9 | 14.8 |
| Jets | DST $2,500 | 12.5 | 5.1 | 5.1 |

The largest served over-projections beyond the table: Cardinals DST 22.7
against 2.0, Trey McBride 15.7 against 4.9, James Cook III 11.3 against 3.9,
Browns DST 14.5 against 7.4, Nico Collins 12.5 against 5.6.

Reading the players. The shape is one pattern, not noise. The public's
chalk below $7,000 (Mayer, the Jaguars, the Jets, Olave, Barkley, Hampton,
Wilson, McConkey, Johnston, Irving) is under-owned by the sampler by 8 to 26
points, and its expensive names (Bijan Robinson, Jonathan Taylor, St. Brown,
Jefferson, McBride, Cardinals DST) are over-owned by 2 to 21. That is what a
negative price sensitivity produces: the calibration reached toward its
salary target by driving kappa to −1.98 (served) and −1.88 to −2.23 (re-runs)
points per $1,000, so the sampler's pull rises with salary at a given
projection and the public's value plays are structurally out of reach; the
mean-salary target is then met with one sub-$3,000 filler in 77% of lineups
rather than the public's spend-out to $50,000. Mayer's miss has a second,
mechanical part: 6.7 of his 27.5 points sit at FLEX, and the sampler has no
tight-end-at-FLEX path at all. This is a reading of the mechanism consistent
with every row above, offered for the next study, not a fit.

## 4. What this artifact does and does not say

It says the served week-1 field was the sampler's, not a draw: five re-runs
land within 0.06 pp per player of the served board and within 0.02 of its
Spearman, and every miss above is the same on every seed. It says the miss
is not the pool's: 99.7% of the real roster slots sit on players the sampler
could draw (425 real players outside the pool hold 0.27%, the largest of
them Travis Hunter at 0.36%). It does not say the aggregate constants are validated: the max-ownership
and salary targets are not equal to the real field and the collision
benchmark is far from it. The sharper conclusion, the reviewer's: retuning
the existing aggregate targets cannot plausibly repair the observed player
ordering, roster construction and concentration failures; the sampler
family and its mechanics are the larger problem.

It does not say what to change. One slate; the public field, the projections
and the slate's price structure all vary week to week, and the Showdown
work's lesson stands: a per-player ownership model needs a held-out test,
not a constant swap. No production change follows from this artifact.
