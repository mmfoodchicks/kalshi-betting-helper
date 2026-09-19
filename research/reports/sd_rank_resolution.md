# Does the exact engine resolve Top-10? Measured, not inferred

**Run 2026-09-19 on the two pinned boards, served model, 12,000 worlds at the
A/B's seed, provenance clean at `2764f29` (`research/data/sd_rank_resolution.json`).**
The audit document had said the top-10 region of an 88,235-entry field was
"unresolvable by construction" at 4,000 football worlds. The reviewer pointed
out that this confuses two sources of randomness: the exact engine integrates
contest rank analytically inside each football world (the field mass strictly
above and level with a lineup, mapped through `payout_grid`'s Poisson and
normal rank curves), so P(rank ≤ 10) is as analytic as P(rank ≤ 882) and the
only noise is the football world. Resolution is therefore a measurement. This
is it — in its second version, after the reviewer's pre-freeze check
(section 0).

## 0. Two statistical corrections before the numbers

The first version of this report screened lineups "within 2 SE of 20th place"
with each lineup's **own** standard error, and quoted the best lineup as "two
to three noise units above" the shortlist on the same basis. Every lineup is
scored on the same football worlds, so their estimates are correlated and a
lineup's own SE is not the uncertainty of its gap to another lineup. The
screen is now the **paired** worldwise difference against the 20th place, and
the marginal version is kept only as a labelled descriptive quantity. Measured,
the paired SE of a gap is **1.5–2.0× the marginal SE** (the two lineups'
per-world values are weakly anti-correlated: when one is near the top of a
world the other tends not to be), so the marginal screen understated the
uncertainty of the ordering.

The first version also chose the fold-to-fold comparison set on all 12,000
worlds. It was identical for both folds, but not independent of the scoring
folds, so its rank correlation mixed candidate-selection noise into the
objective's noise. The set is now chosen by the **third fold** — identical for
the two folds compared and independent of both folds' worlds. That raised the
Top-10 number from 0.38–0.41 to 0.67–0.70; the all-worlds version is kept
beside it, labelled.

## Method

The rank curves were extended from the served k = 88 (Top 0.1%) and 882
(Top 1%) to k = 10, 20 and 50 with the same arithmetic as production, and the
extension was **checked against the grid's own `top1` / `top01` columns before
use (exact match, both boards)**. Pass one accumulated every enterable
lineup's per-world rank probability in 1,000-world blocks with sums of
squares; pass two kept the per-world values for the union of each objective's
strongest 300 on all worlds and on each fold (636–670 lineups). The served
column reproduces the A/B's served arm exactly (best Top-1% 7.993% on DEN @
KC, 11.761% on DAL @ NYG), which ties the two artifacts together.

## Result

| Objective (share of field) | Relative SE per strong lineup, 12,000 worlds | …at 4,000 | Best lineup above 20th place, in paired SEs | Lineups within 2 paired SEs of 20th, of 300 | Fold-to-fold rank correlation, third-fold set (all-worlds set) | Top-20 list agreement between folds, of 20 | Greedy-cover portfolio agreement between folds, of 20 |
|---|---|---|---|---|---|---|---|
| **Top 10 (0.011%)** | **16.6% / 15.2%** | 28.8% / 26.3% | **3.6 / 6.1** | **67 / 41** | **0.70 / 0.67** (0.43 / 0.44) | 13.0 / 15.0 | 9.7 / 12.3 |
| Top 20 (0.023%) | 13.8% / 12.6% | 23.8% / 21.8% | 4.2 / 7.2 | 57 / 36 | 0.73 / 0.71 (0.55 / 0.52) | 14.3 / 14.3 | 10.3 / 12.0 |
| Top 50 (0.057%) | 10.0% / 9.5% | 17.4% / 16.4% | 4.9 / 8.7 | 44 / 35 | 0.80 / 0.77 (0.67 / 0.63) | 15.0 / 15.3 | 11.7 / 14.0 |
| Top 0.1%, served (88 / 87) | 8.3% / 7.9% | 14.3% / 13.6% | 5.8 / 10.8 | 32 / 30 | 0.84 / 0.79 (0.73 / 0.67) | 14.7 / 16.7 | 13.0 / 15.3 |
| Top 1%, served (882 / 871) | 3.8% / 3.5% | 6.6% / 6.1% | **7.1 / 13.8** | **30 / 19** | **0.91 / 0.90** (0.88 / 0.88) | 15.7 / 16.7 | 11.7 / 12.7 |

Each cell is DEN @ KC / DAL @ NYG. "Strong lineup" is the top 50 under that
objective; the paired columns compare each of the objective's strongest 300
lineups (on all 12,000 worlds) with the 20th-ranked lineup, world by world;
"fold-to-fold" is the mean over the three pairs of 4,000-world folds.

## What it earns

- **Top-10 is not unresolvable.** At 12,000 worlds each of the strongest
  lineups carries a 15–17% relative standard error, and the best lineup on
  each board stands 3.6 and 6.1 paired standard errors above the 20th place.
  The sentence "unresolvable by construction" was wrong and is withdrawn.
- **Top-10 is not resolved enough to order a top 20 at 4,000 worlds.** On an
  independently chosen set the fold-to-fold rank correlation is 0.67–0.70
  against 0.90–0.91 for Top 1%, and 41–67 of the 300 strongest lineups are
  within two paired standard errors of twentieth place against 19–30. At
  12,000 worlds it is a coarse screen: the top-20 list agrees on 13–15 of 20
  between folds (Top 1%: 16–17).
- **Resolution degrades smoothly with k**, not at a cliff: 0.70 → 0.73 → 0.80
  → 0.84 → 0.91 in rank correlation from Top 10 to Top 1% on DEN, 0.67 → 0.71
  → 0.77 → 0.79 → 0.90 on DAL.
- **The greedy-cover portfolio is a noisier object than the ranking at every
  objective.** Between folds it agrees on 12–13 of 20 under Top 1%, no better
  than under Top 0.1% (13–15) and only modestly better than under Top 10
  (10–12). The A/B's reseed floor (14–15 of 20 at 12,000 worlds) is consistent
  with this, and it means a same-field reseed or fold control belongs beside
  any future portfolio comparison, so ordinary greedy instability is not read
  as an effect.

## What it does not license

No objective change: S7 comes first, and every probability here is under the
uncalibrated field. The engine can estimate much narrower finish bands than
Top 0.1% analytically within each football world, but Monte Carlo uncertainty
in the football worlds increases as the target narrows: at current world
counts Top-10 is useful as a diagnostic and substantially less stable for
lineup ordering and portfolio construction than Top 1%. If a finer objective
is ever wanted, k of about 50 at 12,000 worlds is where the ranking becomes
reliable. The economic question — which rank band carries the prize pool a
portfolio can actually reach — stays behind the field calibration.
