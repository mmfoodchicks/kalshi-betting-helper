# Recent form and head-to-head: tested, and neither earns a weight

`research/soccer_form_validation.py` → `research/data/soccer_form_validation.json`.
Run 2026-10-17 on 78,333 finished 90-minute matches from 85 ESPN
competitions, 2023 to 16 October 2026. The rule is the script's docstring,
written before the run; it is summarised here, not changed.

## What was asked

The owner asked for recent form and head-to-head records to be weighted into
each match's price. Both are reasonable things to believe in. Both were
tested the way every other number in this model was: chosen on one year,
checked once on the next, confirmed on a third.

## The test

Walk-forward by **week**, in production's own window (1 January of the
previous year up to the week; `soccer._FIT_YEARS`): every week the shipped
goals model is refitted on the matches before it and predicts every match in
it. Weekly, not monthly, because production refits every six hours and a
monthly refit would hand form a staler signal than the board has.

Two ways of measuring form, both a shift of each side's log scoring rate:

- **A, short-memory fit.** A second fit with a half-life of 69, 35 or 17
  days, blended into the long one at weight w.
- **B, last N matches.** Each side's goals for and against over its last 5
  or 8 matches (all competitions) against what the long fit expected, with
  two expected goals of prior: log((goals + 2) / (expected + 2)).

Head-to-head: each side's goals in the window's past meetings of the two
teams against what the current fit expected, the same residual, at weight
w_h. On the 2024-2026 weeks 72-77% of matches had at least one past
meeting in the window (1.6-1.8 on average).

84 combinations. Select on 2024; each term ships only if 2025 confirms it on
its own by more than two standard errors of the per-match log-loss
difference; 2026 can veto, never promote.

## Results

2024, the selection (20,055 matches, mean log loss; the base is the shipped
model with no form and no head-to-head):

| | log loss | vs base |
|---|---|---|
| **base** | **1.00514** | |
| A, 35-day half-life, w 0.1 (best) | 1.00505 | −0.00008 |
| A, 17-day half-life, w 0.1 | 1.00506 | −0.00008 |
| A, 35-day half-life, w 0.3 | 1.00638 | +0.00124 |
| B, last 8, w 0.1 | 1.00546 | +0.00032 |
| B, last 5, w 0.1 | 1.00565 | +0.00051 |
| B, last 5, w 0.5 | 1.02137 | +0.01623 |
| head-to-head, w 0.25 | 1.00790 | +0.00276 |
| head-to-head, w 0.5 | 1.01546 | +0.01032 |
| head-to-head, w 1.0 | 1.04446 | +0.03932 |

2024 chose the short-memory blend at 0.1 and **no head-to-head**: every
head-to-head weight made 2024 worse, increasingly so as the weight grew.

The chosen form term, held out:

| | base | with the form term | difference ± SE |
|---|---|---|---|
| 2025 (20,625) | 1.01008 | 1.01003 | −0.00005 ± 0.00015 |
| 2026 to 16 Oct (17,503) | 1.01582 | 1.01570 | −0.00013 ± 0.00017 |

A third of a standard error on 2025: **the form term ships at 0**, as does
head-to-head. `soccer.W_FORM = W_H2H = 0`.

What this says: a team's rating already leans toward the recent -- the
fitted half-life is 231 days, so a result from last month weighs 0.93 of
last week's and one from last season about 0.4. Adding weight to the last
five results, or to how two clubs have done against each other, adds noise.
The head-to-head residual in particular is a handful of matches each
carrying the full randomness of a soccer score; the more it is trusted the
worse the price. The finding is the standard one in the literature, and it
held here on 58,183 predictions.

**The card still shows both**: each side's last five results (date,
opponent, venue, score) and the past meetings in the window, with a line
saying they carry no weight because they did not improve held-out
predictions. If a future test earns either a weight, the same line says how
far it moved the three outcomes.

## What changed in the base numbers, and why

These runs use `soccer._ninety`: a match that went to extra time or
penalties now enters at its **90-minute** score, rebuilt from ESPN's goal
minutes (stoppage is "90'+3'", extra time "98'"), and is dropped only when
the goal events do not add up to the final score. The first version dropped
every such match. Over 2023-2026: 1,397 extra-time matches, 1,264 rebuilt,
133 dropped; **197 of the rebuilt were not level at 90** -- second legs,
which go to extra time on a level aggregate (PSV 3-1 Juventus AET, 2025, was
2-1 at 90) -- so "call them all draws" would have been wrong too.

The rebuilt matches are mostly cup ties level at 90 and are harder to
predict, so the base log loss on 2025 reads 1.01008 here against 1.00236 in
`soccer_validation.md` (same parameters; different matches). Against the
league base rates the gap is unchanged in kind: 1.01008 vs 1.06721 on 2025,
1.01582 vs 1.06732 on 2026.

**The weak-competition list was an artifact of the dropped matches.** The
first version flagged seven cups where the model lost to the competition's
base rate on 2025. With the extra-time matches back, the cups' draws are in
the benchmark as well as the fit, and pooled over every held-out week of
2024-2026 (n ≥ 100) only the **Scottish Cup** is worse than its base rate
(1.0280 vs 1.0100, 256 matches); the next closest, the Leagues Cup, is
0.003 better. `soccer._WEAK_COMPS = {"sco.tennents"}`.

## Calibration of the owner's leg

P(the underdog does not win) on held-out heavy-underdog matches (the model's
underdog at least five points under its draw), the shipped model:

| | said | happened | n |
|---|---|---|---|
| 2025 | 76.5% | 74.9% | 1,633 |
| | 84.7% | 83.2% | 2,948 |
| | 92.7% | 92.1% | 833 |
| 2026 to date | 76.5% | 75.5% | 1,314 |
| | 84.6% | 83.7% | 2,392 |
| | 92.4% | 93.6% | 612 |

About a point and a half high on 2025 and within about a point on 2026. The
card prints both rows; the edge column does not correct for them.

## Not tested

xG-based form (shots and chance quality) is the version of "form" that the
literature finds does carry signal beyond results. ESPN carries no xG across
these leagues; that test needs a data source this app does not have.
