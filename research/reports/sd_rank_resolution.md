# Does the exact engine resolve Top-10? Measured, not inferred

**Run 2026-09-19 on the two pinned boards, served model, 12,000 worlds at the
A/B's seed, provenance clean at `596bd61` (`research/data/sd_rank_resolution.json`).**
The audit document had said the top-10 region of an 88,235-entry field was
"unresolvable by construction" at 4,000 football worlds. The reviewer pointed
out that this confuses two sources of randomness: the exact engine integrates
contest rank analytically inside each football world (the field mass strictly
above and level with a lineup, mapped through `payout_grid`'s Poisson and
normal rank curves), so P(rank ≤ 10) is as analytic as P(rank ≤ 882) and the
only noise is the football world. Resolution is therefore a measurement. This
is it.

## Method

The rank curves were extended from the served k = 88 (Top 0.1%) and 882
(Top 1%) to k = 10, 20 and 50 with the same arithmetic as production, and the
extension was **checked against the grid's own `top1` / `top01` columns before
use (exact match, both boards)**. Every enterable lineup's per-world rank
probability was accumulated in 1,000-world blocks with sums of squares, so a
world-level standard error and three 4,000-world folds are both readable; for
the strongest 400 lineups under each objective the per-world values were kept,
so the greedy-cover portfolio could be selected on each fold. The served
column reproduces the A/B's served arm exactly (best Top-1% 7.993% on DEN @
KC, 11.761% on DAL @ NYG), which ties the two artifacts together.

## Result

| Objective (share of field) | Relative SE per strong lineup, 12,000 worlds | …at 4,000 worlds | Fold-to-fold rank correlation (4,000-world folds) | Top-20 list agreement between folds, of 20 | Greedy-cover portfolio agreement between folds, of 20 | Lineups within 2 SE of 20th place |
|---|---|---|---|---|---|---|
| **Top 10 (0.011%)** | **16.6% / 15.2%** | 28.8% / 26.3% | **0.38 / 0.41** | 12.7 / 15.0 | 9.7 / 12.3 | **39 / 28** |
| Top 20 (0.023%) | 13.8% / 12.6% | 23.8% / 21.8% | 0.51 / 0.51 | 14.3 / 14.3 | 10.3 / 12.0 | 33 / 24 |
| Top 50 (0.057%) | 10.0% / 9.5% | 17.4% / 16.4% | 0.64 / 0.62 | 15.0 / 15.3 | 11.7 / 14.0 | 28 / 25 |
| Top 0.1%, served (88 / 87) | 8.3% / 7.9% | 14.3% / 13.6% | 0.71 / 0.68 | 14.7 / 16.7 | 13.0 / 15.3 | 25 / 22 |
| Top 1%, served (882 / 871) | 3.8% / 3.5% | 6.6% / 6.1% | **0.90 / 0.87** | 15.7 / 16.7 | 11.3 / 12.7 | **23 / 15** |

Each cell is DEN @ KC / DAL @ NYG. "Strong lineup" is the top 50 under that
objective; "fold-to-fold" is the mean over the three pairs of 4,000-world
folds, over the objective's own 400-lineup shortlist.

## What it earns

- **Top-10 is not unresolvable.** At 12,000 worlds each of the strongest
  lineups carries a 15–17% relative standard error; the best lineup on each
  board is separated from the field of candidates by two to three noise units
  (spread/noise 2.1 and 3.2). The sentence "unresolvable by construction" was
  wrong and is withdrawn.
- **Top-10 is not resolved enough to order a top 20 at 4,000 worlds.** The
  fold-to-fold rank correlation is 0.38–0.41 against 0.87–0.90 for Top 1%,
  and 28–39 lineups sit within two standard errors of twentieth place against
  15–23. At 12,000 worlds it is a coarse screen: the top-20 list agrees on
  13–15 of 20 between folds (Top 1%: 16–17).
- **Resolution degrades smoothly with k**, not at a cliff: 0.38 → 0.51 →
  0.64 → 0.71 → 0.90 in rank correlation from Top 10 to Top 1%. The served
  Top 0.1% sits two-thirds of the way up.
- **The greedy-cover portfolio is a noisier object than the ranking at every
  objective.** Between folds it agrees on 11–13 of 20 under Top 1%, no better
  than under Top 0.1% (13–15) and only modestly better than under Top 10
  (10–12). The A/B's reseed floor (14–15 of 20 at 12,000 worlds) is
  consistent with this.

## What it does not license

No objective change: S7 comes first, and every probability here is under the
uncalibrated field. If a finer objective is ever wanted, the numbers say
12,000 worlds and a k of 50 or so is where the ranking becomes reliable; Top
10 would need several times more worlds to order a top 20. The economic
question — which rank band carries the prize pool a portfolio can actually
reach — stays behind the field calibration.
