# The soccer goals model: chosen on 2024, tested on 2025

> **Superseded in two places (2026-10-17, `soccer_form.md`).** This run, like
> the first version of `soccer.results`, dropped every match that went to
> extra time; they now enter at their 90-minute score, which moves the base
> log loss (the cup draws are back) and removes six of the seven competitions
> in "Where the model is weak" below -- that list was an artifact of the
> dropped draws. The parameters chosen here are unchanged and still shipped;
> the current numbers, the current weak list (the Scottish Cup alone) and the
> current calibration are in `soccer_form.md`.

`research/soccer_validation.py` → `research/data/soccer_validation.json`
(the test) and `soccer_validation_wide.json` (a labelled second look).
Run 2026-10-17 on 59,861 finished 90-minute matches from 85 ESPN
competitions, 2023-2025 (every league Kalshi lists that ESPN carries, plus
the cross-league competitions and domestic cups that tie the leagues to
one scale). Every number below comes from the shipped code: `soccer.fit`,
`soccer.rates`, `soccer.grid`, `soccer.outcome`.

## The rule, written before any 2025 number was read

Walk-forward by month. For every month the model is fitted on every match
before the month -- weights relative to the month's first day, as
production weights relative to today -- and predicts every match in it.

1. On the 2024 months, choose (XI, KAPPA, RHO) from the grid
   XI ∈ {0.003, 0.005, 0.008}, KAPPA ∈ {3, 6, 12}, RHO ∈ {0, −0.06, −0.12}
   by the lowest mean three-way log loss.
2. Freeze them. Score 2025 once.
3. Ship the model as a probability if its 2025 log loss beats the league
   base-rate benchmark (the decayed home/draw/away frequencies of the
   match's own competition from the same window) by more than two standard
   errors of the per-match difference; otherwise show the de-vigged Kalshi
   price alone and say so.

## What the selection chose

| XI (per day) | KAPPA | RHO | 2024 log loss |
|---|---|---|---|
| **0.003** | **3** | **0** | **1.00037** |
| 0.003 | 3 | −0.06 | 1.00067 |
| 0.003 | 6 | 0 | 1.00080 |
| 0.005 | 3 | 0 | 1.00172 |
| 0.008 | 12 | −0.12 | (worst) |

19,737 matches. A half-life of 231 days, three matches of league-average
play behind every rating, and no Dixon-Coles low-score correction.

All three sit on the edge of that grid. A **second look** with a wider grid
(XI ∈ {0.001, 0.002, 0.003}, KAPPA ∈ {1, 2, 3}, RHO ∈ {+0.04, 0, −0.04})
was run AFTER 2025 had been read once; it is a sensitivity check, not the
test, and `soccer_validation_wide.json` says so (`second_look: true`). It
chose the same three values: 0.002 scores 1.00066 and 0.005 1.00172 around
XI = 0.003; KAPPA 2 scores 1.00127 and 6 1.00080 around 3; RHO ±0.04 scores
1.00046 / 1.00073 around 0. The optimum is interior across the union of the
two grids, so the 2025 figure below is the first pass's, unchanged.

## 2025, held out, with the frozen parameters

| | log loss | RPS |
|---|---|---|
| **the goals model** | **1.00236** | **0.20843** |
| league base rates | 1.05425 | 0.22844 |
| one global rate | 1.06606 | |

20,277 matches. The model is 0.0519 nats a match better than the league
base rates, standard error 0.0025 (20.5 SE): the promotion rule is met and
the board shows the model as a probability. Over the 19,836 matches where
both sides had a result in the fit the gap is the same (1.00441 against
1.05639).

For scale: a book's closing line typically sits near 0.96-0.98 on the
major leagues; this model is a good deal better than knowing nothing and
some way short of the market. No odds benchmark is possible on history --
ESPN's results carry no line -- so the market comparison is prospective:
from the first day the board runs, DraftKings' de-vigged three-way line (as
ESPN shows it on upcoming matches, about 40% of them) logs to the shadow
bucket `soccer_dk` beside the model's bucket `soccer`, both graded against
Kalshi's de-vigged price.

## The owner's leg: NO on the underdog

The figure the card leads with is P(the underdog does not win), the
underdog being the model's lower side. Calibration on 2025, held out:

| the model said | it happened | n |
|---|---|---|
| 66.7% | 67.2% | 6,123 |
| 74.7% | 75.1% | 8,580 |
| 84.2% | 82.9% | 4,453 |
| 93.3% | 91.6% | 1,120 |

Brier 0.1803. On the **heavy-underdog** subset (the model's underdog at
least five points under its draw -- the owner's Kalshi rule in model terms;
4,908 matches, Brier 0.1449):

| the model said | it happened | n |
|---|---|---|
| 76.5% | 74.6% | 1,422 |
| 84.7% | 83.1% | 2,686 |
| 92.6% | 91.3% | 778 |

It reads about a point and a half high at the top. The card prints this
table under the summary and the edge column does NOT correct for it: a
+1.5 edge on a 90%+ leg is inside the model's own bias.

## Where the model is weak

Per competition on 2025 (n ≥ 50), the model was WORSE than the base rate in
seven: the English FA Cup (+0.018), League Cup (+0.125), Scottish Cup
(+0.241), U.S. Open Cup (+0.155), Taça de Portugal (+0.020), USL League One
(+0.006) and international friendlies (+0.000). Knockout cups across the
tiers: the lower side is rated on its own league's results, the tie is
played at rotated strength, and the base rate for "the higher side at home
wins" is hard to beat. Those competitions are `soccer._WEAK_COMPS`; the
card shows a warning and the price, and says to read the price, not the
edge. The largest gains were in the women's leagues (Vrouwen Eredivisie
−0.269, Première Ligue −0.244, WSL −0.169, Liga F −0.138) and in Portugal
(−0.161), where the spread of strength is widest.

## What the fitter is

`soccer.fit`: every team an attack and a defence rate, a home advantage per
competition, results weighted exp(−XI · days). Each block (attack, then
defence, then home advantage) takes its Poisson maximum-likelihood value
given the others' LATEST values, with a Gamma prior worth KAPPA matches at
the league's mean rate (the league mean itself shrunk toward the global
mean by two pseudo-teams, which pins a league no cross-league match
reaches); the attacks are re-centred every sweep, which pins the one exact
degeneracy (every attack and every defence up by the same constant changes
no rate). The first version updated all three blocks from one snapshot and
oscillated -- on a synthetic three-league season it recovered the ratings
at a correlation of 0.1 and put a home advantage at 3.3; the corrected
fitter recovers them at 0.91 / 0.94 with each league's home advantage
within 0.1 of the truth, and that pool is the guard. Two real seasons
(41,441 matches, 2,454 teams) fit in 1.1 s in pure Python; the largest
rating moves 0.07 between 24 and 48 sweeps and under 0.01 past 40, which is
`SWEEPS`.

## What was not done

- No xG, no lineups, no injuries, no rest days, no cup-rotation term. The
  cups above are where that shows.
- No calibration layer. The 1.5-point bias at the top is reported, not
  corrected; `calibrate` can earn one from the live bucket.
- Promoted sides carry their lower-league results into the fit (the same
  ESPN team id across tiers) -- the league prior pulls them toward their
  new league's mean at KAPPA = 3 matches, which is quick. Whether that is
  the right speed was not measured separately.
