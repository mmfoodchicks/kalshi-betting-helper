# NFL and college game models: diagnosis and cross-reference study

*Started 2026-10-29 (Fable). Scripts `research/game_xref_common.py`,
`research/game_xref_nfl.py`, `research/game_xref_cfb.py`. Nothing in this report
changes a board; the recommendations at the end are for the orchestrator to
implement after we agree.*

The owner's request: "Do some research into the college and nfl models and
see if any tuning can be done. We're right about 60% for nfl match ups (which
is awful). We're good with college match ups but we're very off on the point
estimation. I'd like to see where we can cross reference our different
simulations into different features to help us be more accurate."

## 0. The rules, written before any number below was scored

Written 2026-10-29 11:30 UTC, before `game_xref_nfl.py` or `game_xref_cfb.py`
produced a row. The diagnosis tables (section 1 and 3) are descriptive and
need no rule. Every tuning or stacking proposal is judged by the rules here.

**Data.** NFL: nflverse `games.csv` (closing spread, total and moneylines,
ESPN BET consensus as nflverse records it), 2024 and 2025 complete (272 games
each), 2026 weeks 1-7 (107 games). College: ESPN's week scoreboards for both
divisions 2024-2026 (results, venue, division), ESPN BET lines from the core
API's stored odds record per game (close where it carries one, else the
current line as stored), and ESPN's FPI/BPI predictor per game (its last
stored pre-game run). Kalshi's own history is not available here; ESPN BET's
close stands in for "the market". The live 2026 records on the server are
not readable from this sandbox; the replays below are the evidence.

**Clusters.** Every standard error is clustered by (season, week): the games
of one weekend are not independent draws.

**Walk-forward.** Anything fitted is fitted on 2024 and scored on 2025, then
fitted on 2024+2025 and scored on 2026 to date. The pooled test set is 2025 +
2026. Nothing is chosen on a test season. The regularisation strength is
fixed in advance (ridge lambda 1.0 on standardised... no: on logit features as
they are, with k <= 5 features and n >= 270 per fit, lambda 1.0 is a mild
prior and is not tuned).

**Loss.** Win probabilities: log loss (the parlay's loss; CLAUDE.md), with
Brier and straight-up accuracy beside it. Point estimates: MAE, with bias and
the slope of actual on predicted.

**Pass margin.** A stack or a tuning is recommended for production only when
it beats the relevant baseline by more than 2 clustered SE on the pooled
test games AND the difference has the same sign in each test season. The
baselines: for win probability, the de-vigged closing moneyline; for the
margin, the closing spread; for the total, the closing total. A model that
beats only the production model and not the market is reported as such and
recommended only as a replacement for the production number, never as an
"edge".

**Power.** At n ~ 380 NFL test games the clustered SE of a paired log-loss
difference between two correlated forecasts is about 0.004-0.006 nats (the
deep validation measured +/- 0.0045 at n = 512), so an effect below ~0.01
nats (roughly one point of straight-up accuracy) is invisible here. At
n ~ 1,400 college test games the SE is about 0.003 nats. Margin MAE
differences carry an SE of ~0.3-0.4 points (NFL) and ~0.3 (college).

**Candidates, NFL.** Features: `sim` = logit of the drive sim's home win
probability; `pd` = logit of `nfl_season._wp` on the in-season PD ratings;
`fpi` = logit of ESPN's FPI projection; `slp` = Sleeper's implied margin
(team points home - away) through Phi(m/13.2); `mkt` = logit of the
de-vigged closing moneyline. Stacks: S1 [sim]; S2 [sim, pd]; S3 [sim, pd,
fpi, slp]; S4 [mkt]; S5 [mkt, sim]; S6 [mkt, sim, pd, fpi, slp]. Margin
stacks (ridge linear, same scheme): M1 [m_sim]; M2 [spread]; M3 [spread,
m_sim]; M4 [spread, m_sim, m_fpi, m_slp]. Totals: T1 [t_sim]; T2 [total];
T3 [total, t_sim, t_slp].

**Candidates, college.** `raw` = logit of Phi(model margin / 18) (the board's
`p_home_raw`); `mkt`; `fpi`. Stacks: C1 [raw]; C2 [raw, fpi]; C3 [mkt]; C4
[mkt, raw]; C5 [mkt, raw, fpi]. The margin: ridge linear on [spread], [spread,
model margin], [spread, model margin, fpi margin]. **The total** (the owner's
"point estimation"): an offence/defence rating pair fitted the way
`cfb.inseason_ratings` fits margins -- `pts_for(i at j) = mu + O_i + D_j +
h`, prior from last season's PF and PA per game regressed by the same 0.58,
the prior worth K games -- with K chosen on 2024 from {1, 1.5, 2, 3, 4, 6}
by total MAE, then TESTED on 2025 and 2026 against (a) the production
total (the drive engine's, which the diagnosis will show is nearly constant)
and (b) the closing total. The recommendation to replace the board's total
needs (a) by more than 2 SE in both test seasons and a slope of actual on
predicted inside 0.7-1.3. Beating (b) is not expected and not required.
The margin constants (`_PRIOR_K` 1.5, `_MARGIN_CAP` 50, `_HFA_MARGIN` 3.0)
are re-measured on the same grid-on-2024, test-on-2025/2026 scheme and are
recommended for change only under the same 2-SE-both-seasons rule.

**What is NOT tested here.** The deep engine (its 2024-25 play-by-play stores
are not on this machine; its walk-forward validation is
`nfl_deep_validation.md`, +0.0025 +/- 0.0032 nats for the production blend
against the PD ratings, i.e. nothing); weather (failed its stage-1 test,
`nfl_weather.md`); recent form and head-to-head (soccer's lesson). Nothing
in-game touches any pre-game number: the replay's defence factor reads
results before the week, the lines are closes, FPI is its stored pre-game run.

## Addendum to the rules (written 2026-10-29 14:05 UTC, after the data pulls, before the college scoring run)

- ESPN's stored odds record carries ESPN BET (provider 58) for 2024-2025 and
  DraftKings (provider 100) for 2026; the parser takes 58, else 100, else any
  non-"Live Odds" provider, the `close` figure where stored (97-99% of games),
  else the current one. FCS-only games have no stored line and no predictor
  (HTTP 400), so FCS is scored against the result only.
- Every stored predictor record is stamped `lastModified` AFTER the season it
  belongs to (2024 college: 2025-01; 2025: 2026-01; NFL 2024: 2025-02; 2025:
  2026-02). ESPN's game pages freeze the matchup predictor at kickoff and the
  numbers lose to the closing line on every metric below, which argues against
  hindsight, but a pre-game provenance cannot be verified from here. FPI is
  therefore a cross-reference, never a production feature on this evidence.
- The college market tests have two folds after all (2026 lines are
  DraftKings'); the one-fold fallback in the scripts was not needed.
- Section 3.1's `p_fair` table and the ATS/total-lean tables were added after
  the diagnosis tables were read. They have no fitted parameter (the shipped
  25/75 blend and the shipped sigma 18) and are reported as what they are: a
  diagnosis of the engine step, not a pre-registered test.

## 1. NFL: where the drive sim stands (2024-2026, 651 regular-season games)

The replay runs `nfl_game_sim.simulate_game` on Sleeper's final pre-kickoff
projections for each week, the defence factor read from results before the
week, the trims shipped today. Full tables: Appendix A.

### 1.1 Straight up, against the closing moneyline

| season | n | drive sim SU | market favourite SU | sim LL | market LL | sim - market LL (+/- SE) |
|---|---|---|---|---|---|---|
| 2024 | 272 | 0.699 | 0.717 | 0.6177 | 0.5875 | +0.0302 (+/- 0.0116) |
| 2025 | 272 | 0.621 | 0.651 | 0.6426 | 0.6102 | +0.0325 (+/- 0.0095) |
| 2026 (w1-7) | 107 | 0.598 | 0.635 | 0.6533 | 0.6621 | -0.0088 (+/- 0.0119) |
| all | 651 | 0.650 | 0.676 | 0.6340 | 0.6092 | +0.0247 (+/- 0.0069) |

The owner's "about 60%" is 2026 to date: the sim reads 59.8% on 107 games
(+/- 4.5 points), and the closing favourite itself is only 63.5% this season
(65.1% in 2025, 71.7% in 2024). Over three seasons the sim trails the market
favourite by 2.6 +/- 1.4 points of accuracy and 0.025 +/- 0.007 nats of log
loss; the gap is 3 SE in each of 2024 and 2025 and reverses inside its noise
in 2026. The NFL closing favourite wins ~65-68% of games; a model at 60% over
107 games is one SE of bad luck on top of a real two-to-three-point gap.

Nothing else the app can read does better. ESPN's FPI 65.3% / 0.6362,
Sleeper's team points alone (no sim, through Phi(margin/13.2)) 65.0% /
0.6383, the PD ratings 60.7% / 0.6457. When the sim and the market disagree
(93 games) the market was right 55 times, the sim 38; FPI 30-45; PD 61-106.

**Cross-referencing the models buys nothing beyond the price.** The market
favourite's record by how many of sim / PD / FPI side with it: 3 of 3 (418
games) won 70.8% against the market's own mean 69.9%; 2 of 3 (150) 63.3% vs
60.2%; 1 of 3 (64) 56.2% vs 56.8%; 0 of 3 (19) 68.4% vs 55.6%. Each tier
realizes the market's own probability.

**By week band** (all seasons, SU / LL): weeks 1-4 sim 0.604 / 0.646 vs
market 0.646 / 0.647; weeks 5-9 0.670 / 0.649 vs 0.702 / 0.620; weeks 10-18
0.668 / 0.615 vs 0.679 / 0.576. The PD ratings are useless early (0.531) and
the best non-market number late (0.683 / 0.602, better than the sim's 0.615
in log loss): the sim never reads the season's results except through the
defence factor. A hypothesis, not a finding (section 6, item 7).

**Calibration.** The raw sim is under-confident: favourites it puts at
0.60-0.70 won 70.6%, at 0.70-0.80 77.9%, at 0.50-0.60 56.8%. A temperature
fitted on 2024 (t 0.60) or 2024-25 (t 0.72) sharpened it -- and made the
next season WORSE both times (+0.0069 +/- 0.0112, +0.0070 +/- 0.0062). The
shape changes year to year faster than a one-season fit can follow, which is
the production calibrator's situation too (CV-gated, floor 250 graded).

### 1.2 Margins, totals, team points (Appendix A, section 1.2)

Margins, all seasons (bias = predicted - actual; slope = actual on predicted):

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| drive sim mean margin | +0.25 (+/- 0.55) | 10.00 | 1.29 (+/- 0.12) | 4.47 | +0.37 (+/- 0.08) |
| Sleeper points, home - away | -1.12 (+/- 0.55) | 10.03 | 1.16 (+/- 0.12) | 4.77 | +0.41 (+/- 0.10) |
| ESPN FPI margin | -0.24 (+/- 0.53) | 9.97 | 1.05 (+/- 0.08) | 5.73 | +0.35 (+/- 0.09) |
| closing spread | -0.05 (+/- 0.50) | 9.62 | 1.17 (+/- 0.08) | 5.76 | 0 |

The sim's margins are COMPRESSED: a 5-point sim favourite is a 6.5-point
favourite in reality (slope 1.29; 1.60 in 2024, 1.21 in 2025, 0.88 in 2026),
and its spread of predictions (SD 4.5) is three quarters of the market's
(5.8). Sleeper's own projections carry most of the compression (slope 1.16,
SD 4.8) and the drive engine adds a little (the rate clamps and the
fourth-quarter script). The home edge is right: the sim's mean margin 1.85
against the real 1.87 in 2024, 1.61 against 2.07 in 2025; 2026's 107 games
ran -0.50 for the home side against the sim's +2.21 and the spread's +1.61,
a home-field drought the market did not see either.

Totals, all seasons:

| total | bias | MAE | slope | pred SD | MAE - total line's |
|---|---|---|---|---|---|
| drive sim total (shipped trims) | +0.53 (+/- 0.58) | 10.67 | 0.56 (+/- 0.09) | 5.39 | +0.53 (+/- 0.12) |
| Sleeper team points, summed | -0.45 (+/- 0.54) | 10.51 | 0.64 (+/- 0.10) | 4.76 | +0.37 (+/- 0.09) |
| closing total | -1.26 (+/- 0.48) | 10.15 | 1.05 (+/- 0.10) | 4.16 | 0 |

Two things. (a) **The level moves season to season.** Sleeper's summed team
points ran -2.45 (2024), +1.45 (2025) and -0.17 (2026) against real totals;
the shipped 2026 trims put the sim at -1.82 / +2.93 / +0.39. The trims chase
last season's Sleeper, which is why `nfl_trim.md` had to refit them in
October. (b) **The game-to-game variation is half noise.** The sim's total
has an SD of 5.4 points across games, the market's 4.2, and the slope of
the actual total on the sim's is 0.56: when the sim says a game is 6 points
over the average it is really about 3.4 over. The closing total's slope is
1.05. Given the market total and Sleeper's sum, the engine's total earns a
NEGATIVE weight (T3: -0.53 / -0.44): what the engine adds to Sleeper's sum
is noise. Team points: sim MAE 7.45, the market's implied split 7.17, slope
0.85.

### 1.3 What that means for the three records the owner reads

- Moneyline pick: 2-3 points behind the market favourite, consistently.
- Spread legs: the sim under-states favourites by ~20-30% of the margin in
  2024-25; a fixed expansion does not survive out of sample (section 2).
- Totals: fine at the league level once trimmed, half noise game to game,
  and the trims expire with each season's Sleeper.

## 2. NFL: the pre-registered tests (Appendix A, sections 2.1-2.2)

**Stacks (ridge logistic on logits, lambda 1.0, fit 2024 -> test 2025, fit
2024-25 -> test 2026).** None passes. Pooled test log loss against the market:
S1 [sim] +0.0263 (+/- 0.0120), S2 [sim, pd] +0.0230 (+/- 0.0114), S3 [sim,
pd, fpi, slp] +0.0334 (+/- 0.0118), S4 [mkt] +0.0071 (+/- 0.0047), S5 [mkt,
sim] +0.0083 (+/- 0.0053), S6 [everything] +0.0089 (+/- 0.0063). Given the
market, the sim's weight is +0.23 (fit on 2024) and -0.06 (fit on 2024-25);
the market's own recalibration (S4) is slightly worse than the raw price. The
sim, the ratings, FPI and Sleeper's points add nothing to the closing line.

**Margin stacks (ridge linear).** M3 [spread, m_sim]: m_sim +0.25 then -0.06;
pooled test MAE 9.83 vs the raw spread's 9.63 (+0.20 +/- 0.09: the refit
hurts). M1 [m_sim] 10.24 (+0.60 +/- 0.16 vs the spread). **Total stacks.**
T3 [total, t_sim, t_slp] matches the total line (+0.01 +/- 0.05) with t_sim
negative; T1 [t_sim] +0.79 (+/- 0.18).

**The market anchor** (p = w x sim + (1 - w) x de-vigged close; w chosen on
the fit seasons from {0, 0.1, 0.2, 0.3, 0.5, 0.7, 1}): w = 0 both folds.
Pooled test (379 games) against the raw sim: w 0: -0.0208 (+/- 0.0084);
w 0.3: -0.0189 (+/- 0.0059); w 0.5: -0.0155 (+/- 0.0043). Per season at w 0:
2025 -0.0325 (+/- 0.0095), 2026 +0.0088 (+/- 0.0119); at w 0.3: 2025
-0.0278 (+/- 0.0065), 2026 +0.0038 (+/- 0.0084). By the letter of section 0
this is NOT a clean pass: the 2026 fold reverses inside one SE. Pooled it is
2.5-3.2 SE, and the no-fit comparison over all 651 games (w = 0 has no
parameter) is 3.6 SE. Stated as such in section 6.

**Scale-only corrections** (no intercept): margin x k, k fitted 1.51 (2024)
and 1.35 (2024-25), made the next season's MAE WORSE by +0.16 (+/- 0.18)
and +0.37 (+/- 0.09); the total shrunk by the fitted slope: -0.07 (+/-
0.08) then +0.26 (+/- 0.18). The slopes move too much year to year
(margins 1.60 / 1.21 / 0.88; totals 0.70 / 0.59 / 0.80) for a constant to
help. Do not hand-tune either.

**Power.** With 379 pooled test games the clustered SE of a paired log-loss
difference between the sim and a stack is 0.005-0.012 nats; between the sim
and the market 0.008. Effects under ~0.02 nats (about two points of
accuracy) are not resolvable; the margin-MAE SE is 0.1-0.2 points, the
total's 0.1-0.25.

## 3. College: where the model stands (2024-2026, 4,065 rated games, 2,102 priced)

The replay reproduces `cfb.inseason_ratings` exactly (solver self-check
0.00e+00 in every season), builds the ratings from the completed games before
each week, runs `cfb._rates_for` + `cfb._play` for the card's numbers, and
the priced headline at the shipped 25% model / 75% market level with ESPN's
stored close standing in for Kalshi's spread midpoint. Full tables: Appendix B.

### 3.1 Straight up

Priced games, all seasons (n = 2,077 with every number present):

| model | log loss | SU accuracy | LL - market (+/- SE) | acc - market |
|---|---|---|---|---|
| raw ratings Phi(m/18) (`p_home_raw`) | 0.5386 | 0.717 | +0.0361 (+/- 0.0079) | -0.029 (+/- 0.009) |
| drive engine at the model level | 0.5488 | 0.722 | +0.0463 (+/- 0.0083) | -0.024 (+/- 0.009) |
| board headline (engine, 25/75 level) | 0.5246 | 0.746 | +0.0221 (+/- 0.0040) | +0.000 (+/- 0.004) |
| ESPN FPI | 0.5145 | 0.735 | +0.0120 (+/- 0.0042) | -0.011 (+/- 0.007) |
| closing moneyline, de-vigged | 0.5025 | 0.746 | 0 | 0 |

"We're good with college matchups" is true and has a reason: the headline's
pick is the market's pick in 96% of priced games (74 disagreements in 2,077,
split 37-37), so its straight-up record IS the market's (74.6%). The raw
ratings alone are 2.9 points behind the market; late in the season they are
not (weeks 9-16: 71.0% vs 71.1%, LL 0.550 vs 0.542), early they are (weeks
1-3: 76.7% vs 81.1%, LL 0.475 vs 0.388) -- the preseason prior (last season's
margin regressed 0.58) is what the market knows better. FBS-vs-FBS the raw
ratings are 69.2% vs the market's 72.3%; cross-division 89.2% vs 90.8%; FCS
games (no lines) 72.4%.

**But the headline's PROBABILITIES are badly under-confident.** The engine
step compresses margins: given the blended input margin, the engine's mean
margin sits 1.7 points nearer zero on average (3.8 points on lines of 14+,
5.3 in cross-division games; slope of the actual margin on the engine's
1.37-1.44, on the raw ratings' 1.03, on the spread 1.02). The headline's
calibration, favourite's side: predicted 0.55 -> realized 0.597; 0.65 ->
0.724; 0.75 -> 0.860; 0.85 -> 0.939; 0.93 -> 1.000 (n 781 / 540 / 394 / 264
/ 123). The raw ratings through Phi(m/18) are calibrated (0.549 -> 0.547,
0.649 -> 0.667, 0.748 -> 0.752, 0.846 -> 0.877, 0.942 -> 0.960).

Replacing the engine's win frequency with `p_fair` = Phi(fair margin / 18) --
the same 25/75 blend, the same sigma, no engine in between, no fitted
parameter -- costs nothing in accuracy and gains 0.016 nats in every season:

| season | n | engine headline LL | p_fair LL | market LL | p_fair - engine (+/- SE) | p_fair - market |
|---|---|---|---|---|---|---|
| 2024 | 806 | 0.5293 | 0.5158 | 0.5142 | -0.0135 (+/- 0.0031) | +0.0016 (+/- 0.0028) |
| 2025 | 798 | 0.5303 | 0.5136 | 0.5099 | -0.0166 (+/- 0.0025) | +0.0037 (+/- 0.0037) |
| 2026 | 498 | 0.5065 | 0.4868 | 0.4704 | -0.0198 (+/- 0.0045) | +0.0164 (+/- 0.0059) |
| all | 2102 | 0.5243 | 0.5081 | 0.5022 | -0.0162 (+/- 0.0018) | +0.0059 (+/- 0.0024) |

`p_fair` is still a touch under-confident at the top (0.848 -> 0.925, 0.94
-> 0.975): sigma 18 is the raw ratings' residual SD, and a market-anchored
margin has a smaller one (the spread's residual SD is ~15). A sigma for the
anchored number is a follow-up to register, not to set by hand.

**The card's edges are wrong-signed by construction.** `edge_home` /
`edge_away` compare the engine's headline with Kalshi's price; with the
headline under-confident by 5-11 points on every favourite, every favourite
reads over-priced and every underdog reads as value. The record's model
(`p_home_raw`) and the quick combo's 15-point rule are unaffected (they read
the raw ratings), and the raw number's 80/90/95 tiers realized 0.877 / 0.944
/ 0.981 (n 317 / 142 / 105) against the market's 0.896 / 0.951 / 0.984 in
the same bins.

### 3.2 The point estimates: the total is a constant, the ATS pick is the dog

**The card's total.** Across 2,335 priced games the engine's `exp_total` has
a mean of 56.0 and an SD of 2.34 (the closing total's SD 6.14, the real
total's 16.4); its correlation with the real total is -0.09 (the closing
total's +0.31). The mechanism is in `cfb._rates_for`: both sides' expected
points are `_BASE_PPG` 27.5 +/- margin/2, so every game is a 55-point game
and only the split moves; the small variation that remains comes from the
clamps and the fourth-quarter script, and it is anti-correlated with reality
(a bigger favourite kneels more). In FBS games it also runs 3.8 points HIGH
(bias +3.84 +/- 0.35; cross-division -3.75). MAE 13.67 against the closing
total's 12.43 (+1.23 +/- 0.14). The team points `exp_home` / `exp_away`:
MAE 9.85 against the market split's 8.58 (slope 1.37: the split is
compressed too).

**The total lean** (`card["total"]`) is therefore a function of Kalshi's line
alone: it leans under in 30% of games (mean line 59.1) and over in 70% (mean
line 49.6), and hits 50.1% / 51.2%. In cross-division games it leans under
61% of the time and those unders hit 45.7%.

**The ATS pick** reads the simulated margins at Kalshi's rung, and the
simulated margin sits nearer zero than the line in 94.8% of games with a line
of 3+ (1,746 games), so the production ATS book is structurally the
underdog. Those dogs covered 49.4% (FBS 50.5%; cross-division 41.1%, where
the FBS favourite covered 59% of the time). There is no edge on either side
of Kalshi's line in this data (the ratings carry +0.21-0.28 logit weight next
to the market and the stacks show it is worth nothing out of sample), but a
book that is always the dog loses the cross-division games by construction.

**The margin itself** (the ratings, before the engine): MAE 13.26 against the
spread's 11.87 (+1.38 +/- 0.26), slope 1.03, bias -1.74; FPI 12.32. In FBS
games 13.07 vs 11.86; cross-division 14.14 vs 11.93 (bias -3.65: the FBS side
wins by more than the 27.5 division gap allows after the first weeks).

## 4. College: the pre-registered tests (Appendix B, sections 4.1-4.3)

**The offence/defence total.** `pts_for(i at j) = mu + O_i + D_j +/- 1.5`,
priors from last season's PF/PA per game regressed 0.58, the FCS side offset
by half the division gap each way, K chosen on 2024 by total MAE: K = 4
(13.398; the grid ran 13.74 at K 1 to 13.43 at K 6). Tests:

| test | n | OD total MAE | vs engine's (+/- SE) | vs closing total's | slope | 25/75 blend vs line |
|---|---|---|---|---|---|---|
| 2025 priced | 885 | 12.88 | -0.77 (+/- 0.20) | +0.59 (+/- 0.17) | 0.60 (+/- 0.07) | +0.05 (+/- 0.04) |
| 2026 priced | 570 | 13.13 | -0.76 (+/- 0.35) | +0.85 (+/- 0.27) | 0.49 (+/- 0.14) | +0.06 (+/- 0.07) |
| 2025 unpriced (FCS) | 671 | 12.93 | -1.19 (+/- 0.31) | - | - | - |
| 2026 unpriced (FCS) | 397 | 13.94 | -0.83 (+/- 0.45) | - | - | - |

It beats the engine's total in both test seasons (3.9 and 2.2 SE on priced
games) and FAILS the slope band (0.49-0.60 against the registered 0.7-1.3):
half of its game-to-game variation is noise, so K = 4 under-shrinks. It adds
nothing to the closing total (the 25/75 blend +0.05). By the rule it does not
ship as written; what it earns is a registered re-fit (section 6, item 5).

**The margin constants.** Grid on 2024 (K {1, 1.5, 2, 3} x cap {28, 50, 100}
x HFA {2, 3, 4}): best K 2.0 / cap 100 / HFA 3.0 at MAE 13.007 vs the
shipped 13.066. Tests: 2025 -0.034 (+/- 0.021), 2026 -0.125 (+/- 0.070).
Same sign both seasons, neither at 2 SE. The shipped constants stay; the
hint is that blowouts should count fully (cap 100) and the prior a little
longer (K 2).

**Stacks.** C3 [mkt] -0.0019 (+/- 0.0011), C4 [mkt, raw] -0.0002 (+/-
0.0017), C5 [mkt, raw, fpi] +0.0002 (+/- 0.0019), C2 [raw, fpi] +0.0137
(+/- 0.0052), C1 [raw] +0.0416 (+/- 0.0112): nothing beats the market; the
raw ratings get +0.21-0.28 logit weight beside it and FPI +0.15-0.17, worth
nothing out of sample. **The anchor weight** (through Phi(margin/18)): w 0.1
chosen on both fits; against the shipped 0.25 it is within noise in 2025
(0.5141 vs 0.5136) and 0.007 better in 2026 (0.4801 vs 0.4868) -- not a
2-SE-both-seasons result; 0.25 stays.

**Power.** 1,296 pooled priced test games: SE 0.002-0.005 nats on stacks vs
the market, 0.13-0.27 points on total-MAE differences; the FCS tests carry
0.3-0.45.

## 5. Where the error comes from

**NFL.** (1) The level: Sleeper's projections are matchup-adjusted but their
scoring level drifts by +/- 2.5 points a season and carries no venue signal;
the trims and the home split are per-season patches. (2) The scale: Sleeper
compresses the gap between teams (slope 1.16) and the engine's clamps and
fourth-quarter script compress it a little more (1.29); the amount varies by
season, so no constant fixes it. (3) The game-to-game total signal is half
noise (slope 0.56). (4) The sim never reads the season's results (the PD
ratings beat it late). (5) None of this is information the market lacks: no
stack adds to the closing line. The deep engine (not re-run here) adds
+0.0025 +/- 0.0032 nats to the ratings in its own walk-forward -- nothing.

**College.** (1) The engine's conversion of a margin to a game compresses it
(slope 1.37-1.44) and biases it toward zero, most in blowouts, so every
engine-derived figure -- the headline probability, the edges, the ATS pick,
the team points -- is wrong in the same direction while the ratings
themselves are calibrated (slope 1.03). (2) The total has no team
information at all (27.5 +/- margin/2). (3) The preseason prior is the weak
spot of the ratings (weeks 1-3: 4 points behind the market; weeks 9-16:
even). (4) Cross-division: the ratings' 27.5-point gap is right as a prior
and the FBS side still outruns it (-3.65 bias), and the engine takes another
7 points off those margins.

## 6. Recommendations, ranked (what I would ship, test, and watch)

**Ship now (no fit; passes its test or needs none):**

1. **College headline = Phi(fair_margin / 18), not the engine's win rate**
   (`cfb_board._build_board`: keep the engine for the ladders and the
   candidate legs; set `p_home` from the blended margin the way `p_home_raw`
   already is from the model margin). Gain -0.016 +/- 0.002 nats, 4+ SE in
   each of 2024, 2025, 2026; SU accuracy unchanged (0.746 -> 0.747);
   calibration from (0.75 -> 0.86) to (0.75 -> 0.78). Fixes the wrong-signed
   card edges with it. Same for the ATS pick: cover probability from the
   fair margin and sigma (or the engine's margins re-centred on the fair
   margin), so the book stops being the dog 95% of the time. The combo
   maker's college spread and total legs inherit the same defects
   (compressed margins, a 56-point total); re-centring the simulated margins
   on the fair margin and the totals on Kalshi's total before `_build_masks`
   is the matching fix there.
2. **College total and team points from Kalshi's total when it is booked**
   (`kalshi_cfb.total_rung` is already read for the lean): `exp_total` = the
   line, `exp_home/exp_away` = line/2 +/- fair_margin/2. MAE 13.67 -> 12.43
   (-1.23 +/- 0.14), team points 9.85 -> 8.58, slope 1.0 instead of -0.6.
   The "lean" then has nothing to lean on: show the OD total beside the line
   as information (item 5) and stop filing `cfb_tot` picks until something
   earns it; today's lean is a coin flip (50.1% / 51.2%).
3. **NFL headline and straight-up pick anchored to Kalshi's de-vigged
   moneyline**, the way college (75%) and soccer (100%) already are: p = w x
   sim + (1 - w) x market with w in [0, 0.3]. Pooled test gain -0.019 to
   -0.021 nats (+/- 0.006-0.008) and +2-3 points of accuracy; the market beat
   the raw sim by 3 SE in 2024 and 2025 and lost by 0.7 SE on 2026's 107
   games, so this is strong but not the clean two-season pass section 0
   asked for -- I would ship it on the 651-game no-fit comparison (3.6 SE)
   and say so. Keep logging the RAW sim to predlog (the record and the
   calibrator keep their meaning) and show it beside the market on the
   card. Live check before/after: `predlog.vs_market("nfl")` on the server's
   2026 rows must show Kalshi's de-vigged mid beating the raw sim on log
   loss; Kalshi's thin pre-game price is not nflverse's close.
4. **NFL combo legs on moneylines, spreads and totals: market-led blend.**
   `combo_engine._MODEL_TRUST` (0.6-0.7, fitted on baseball) is too much
   model for game legs that add nothing to the market (S5/S6, M3, T3). Set
   the game-leg trust prior low (<= 0.3) or market-only until the graded rung
   log (`nfl_props_log._model_rungs`, weeks 7-10) says otherwise; props keep
   their own evidence.

**Test first (registered rule, then a prospective window):**

5. **College unpriced total: the OD rating, re-fit with the slope band.**
   Rule: K chosen on 2024+2025 from {4, 6, 8, 12} by total MAE subject to
   slope >= 0.7, tested on 2026 weeks 10-15 (unplayed) against the engine's
   total on unpriced games; ship for FCS/unpriced cards if it beats the
   engine by 2 SE and holds the slope. Expected: ~-1 point MAE (2025: -1.19
   +/- 0.31 at K 4, which under-shrinks).
6. **College sigma for the anchored number** (the `p_fair` top bins read
   0.925 / 0.975 against 0.848 / 0.94): sigma chosen on 2024-25 from {13..18}
   by log loss, tested on 2026 weeks 10-15 against sigma 18. Expected gain
   ~0.003-0.005 nats.
7. **NFL late-season PD blend**: a week-weighted blend of the sim and the PD
   ratings (S2's pd weight +0.43/+0.51; PD beats the sim in weeks 10-18 by
   0.013 nats). Register on 2024-25, test on 2026 weeks 10-18. It cannot beat
   the market (nothing here does); it is a better MODEL number for the record.
8. **NFL total level anchored to Kalshi's total ladder** in season (as the
   preseason path already does), so the per-season trims stop driving the
   game total; the props keep Sleeper's pins. Needs the rung log's grading to
   confirm Kalshi's total beats the sim's as the close does (+0.53 +/- 0.12
   MAE here).

**Do not do:**

- Hand-set an NFL margin expansion or total shrink (both failed walk-forward).
- Change the college margin constants (K 2 / cap 100 is -0.03 and -0.12
  points, same sign, under 2 SE); re-measure after 2026.
- Add FPI, the PD ratings or Sleeper's points as production features: none
  adds to the market (S3, S6, C2, C5), and FPI's pre-game provenance cannot
  be verified.
- Fit a sharpening temperature on one season of NFL results (t 0.6-0.7 made
  the next season worse both times).

**What a live check should watch** (once `/api/nfl/record` and
`/api/cfb/record` reach sim-history): the NFL raw sim vs Kalshi's de-vigged
mid on log loss (confirms item 3); the `cfb_ats` book's favourite/underdog
split and record (item 1 predicts ~95% dogs, ~49% covering, worse in
cross-division games); the `cfb_tot` lean's hit rate (~50%); the NFL
calibrator's current temperature for "nfl" (a value below 1 is the pattern
that failed here).

## 7. Data, caveats, reproduction

- NFL: nflverse `games.csv` (closing spread, total, moneylines; 651 played
  2024-2026 regular-season games), Sleeper `projections/nfl/{season}/{week}`
  (QB/RB/WR/TE and K; the FINAL pre-kickoff projection -- Sleeper keeps no
  earlier snapshot, so the replay is slightly better informed than a Tuesday
  build), ESPN core `predictor` per game. The sim runs with today's trims
  (2026's); 2025's totals read +2.9 high for that reason and 2024's -1.8.
- College: ESPN week scoreboards (groups 80 and 81, weeks 1-16), ESPN core
  `odds` (ESPN BET 2024-25, DraftKings 2026; `close` where stored) and
  `predictor`; standings for the priors through `cfb.teams`. 2,331 FBS-board
  games have lines and FPI; FCS-only games have neither.
- Kalshi's own prices are not in this data; ESPN's stored close stands in.
  A Kalshi price thinner than a consensus close would make items 3 and 4
  weaker than measured here, which is what the live checks are for.
- Nothing in-game touches a pre-game number: defence factors and ratings
  read results before the week; lines are closes; FPI is the stored
  pre-game run (unverifiable, see the addendum).
- Rows: `.../scratchpad/xref/nfl_rows.json` (651) and `cfb_rows.json`
  (4,065); regenerate with `python3 -m research.game_xref_nfl replay <raw>`
  / `python3 -m research.game_xref_cfb replay <raw>` then `report`. The raw
  pulls are ~110 Sleeper calls, 96 ESPN scoreboards, 651 + 2 x 2,331 ESPN
  core calls, throttled.

## 8. Addendum after the orchestrator's review (written 2026-10-29 14:33 UTC, before any 2026 NFL week-9 or college week-10 game)

### 8.1 Reproduction notes

- **Ties.** Two NFL games tied (2025 w4 DAL-GB, 2026 w5 WSH-NYG). They are kept:
  y = 0.5 in log loss and Brier; in straight-up accuracy `acc_terms` scores
  `(p >= 0.5) == (y >= 0.5)`, so a tie counts as a HOME win (the home pick is
  "right", the away pick "wrong"). That is neither the boards' convention
  (`nfl_track._grade_rows`: a tie is a loss for either side) nor the
  reviewer's (ties excluded); the three differ by 2 games in 651, the third
  decimal, identically for every model. A market at exactly 50% (two games,
  equal moneylines) is scored as a home pick, the same `p >= 0.5` rule.
- **Unpriced college games** (1,739 rows, no stored line): the raw ratings
  beat the engine's win rate by -0.0124 +/- 0.0022 nats (2024 -0.0113 +/-
  0.0035, 2025 -0.0159 +/- 0.0035, 2026 -0.0085 +/- 0.0046), accuracy
  0.730 vs 0.732. Confirmed from these rows; the reviewer's figures match.
- **The ATS lean at the 25% weight** (fair margin vs the line, 1,968 lines of
  3+, 9 pushes): the lean covers 51.0%, favourites 50.9%. Confirmed.
- **The engine's dispersion**, measured for the re-centring plan (4,000 games
  each): at rating gaps 0 / 10 / 20 / 35 the engine's margin SD is 18.0 /
  17.7 / 16.7 / 14.7 and its total SD 13.2 / 12.8 / 12.6 / 11.7. The real
  residuals around the closing line (2024-26 priced games): margin SD 15.1
  FBS (15.3 / 15.2 / 14.5 / 15.0 by |line| 0-7 / 7-14 / 14-28 / 28+) and
  14.7 cross-division; total SD 15.7 FBS, 15.0 cross.
- **The engine's level on unpriced games**: FCS games' real mean total 52.7
  against the engine's 56.6 (+3.9, the same excess as FBS priced games'
  +3.84).

### 8.2 The settled implementation (college)

Agreed: (1a) priced headline `p_home` = Phi(fair_margin / 18); (1b) unpriced
headline `cal(p_home_raw)` instead of `cal(engine p)` -- the "cfb" calibrator
is fitted on the logged raw figure, so the engine's p was a type mismatch;
(2) one mechanism in `simulate_game`: the simulated margins re-centred on the
fair margin and the totals on Kalshi's booked total with unbiased random
integer rounding before anything reads them; the total lean dropped and
`cfb_total` no longer logged; unpriced games keep the engine's level.

Two consequences of 8.1's dispersion figures, noted for the follow-up rather
than as objections: (i) the engine's total SD (12-13) is narrower than the
real residual around the line (15-16), so after re-centring the total
ladder's probabilities 10-15 points away from the line read 3-5 points too
EXTREME (e.g. P(over by 10+): real ~26%, engine ~22%), which the combo
maker's blend at trust 0.2 will mostly absorb; (ii) the engine's margin SD
(18 at an even game, 15 at a 35-point gap) is wider than the real 15 at
small lines, so cover probabilities off the line read a little too FLAT
there, and the moneyline leg's share of shifted margins above zero differs
from Phi(fair/18) by up to ~2 points at big lines (0.884 vs 0.867 at a
20-point fair margin). Both belong to item 6's sigma test (8.4), extended to
the total.

### 8.3 The registered NFL anchor test (item 3), as agreed

- **Candidate:** p = 0.3 x raw drive sim + 0.7 x the de-vigged closing
  moneyline. **Baseline:** the raw drive sim (`p_home_raw`, the number predlog
  "nfl" logs).
- **Window:** 2026 regular-season weeks 9-18, read ONCE after week 18's last
  game + 48 h (not before 2027-01-12), never earlier.
- **Primary data:** the replay harness (`research/game_xref_nfl.py`) on
  nflverse closes, the window's weeks pulled after the window. **Confirmatory
  data:** the live predlog "nfl" rows (raw sim beside Kalshi's de-vigged
  price at log time) on the same games; the candidate on that data uses
  Kalshi's price in place of the close.
- **Loss:** log loss; **pass:** the candidate beats the baseline by > 2 SE
  clustered by week on the window alone AND by > 2 SE pooled over 2024-2026
  including the window, AND the confirmatory (Kalshi-priced) difference has
  the same sign. Otherwise nothing ships and the report says so.
- **Secondary endpoints (reported, never deciding):** straight-up accuracy
  difference; the full w curve {0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0}.
- **Power:** ~150 games; the clustered SE of this paired difference was
  0.0084 on 107 games (2026 to date), so ~0.007 on the window: an effect of
  0.014 nats or more is detectable; 2024 and 2025 showed -0.028 and -0.033,
  2026 weeks 1-7 +0.004.
- Also agreed: the record card gains the market favourite's own record on
  the same games (Kalshi's pre-game close, our side >= 50c = favourite),
  with the market's log loss beside the model's, and the owner hears the
  trade plainly: anchoring makes the pick mostly the favourite, +2-3 points
  of accuracy on history and no betting edge.

### 8.4 The registered rules for items 5-8

- **Item 5, college unpriced total.** Fit seasons 2024 + 2025, every rated
  game. Two candidates against the engine's total: (A) the engine's total
  less its mean bias on the fit seasons' unpriced games (one constant, ~3.9);
  (B) the offence/defence rating with K chosen from {4, 6, 8, 12} by total
  MAE subject to a slope of actual on predicted >= 0.7 on the fit seasons.
  Test: 2026 weeks 10-16, UNPRICED rated games (no stored ESPN line; in
  production, no Kalshi total booked), read once after week 16 + 48 h.
  Pass: MAE below the engine's by > 2 SE clustered by week; (B) also needs
  the window's slope inside 0.7-1.3. The passing candidate with the lower
  MAE ships for unpriced cards; if only (A) passes, the level alone ships.
  Power: ~450 games, paired-MAE SE ~0.4; (B) read -1.19 and -0.83 at K 4
  on 2025 and 2026 weeks 1-9, so a 2-SE pass is likely for the level and
  marginal for (B).
- **Item 6, sigma for the anchored number (and the engine's dispersion).**
  Fit 2024 + 2025 priced games: sigma_m chosen from {13, 14, 15, 16, 17, 18}
  by log loss of Phi(fair_margin / sigma_m); the total-dispersion factor
  f_t chosen from {1.0, 1.1, 1.2, 1.3} (the re-centred total deviations
  scaled by f_t) by log loss of the over/under probabilities at the line
  +/- 7 and +/- 14. Test: 2026 weeks 10-16 priced games against sigma 18 and
  f_t 1.0. Pass: > 2 SE clustered by week, each on its own. Power: ~300
  games; SE ~0.003-0.004 nats against an expected 0.003-0.005: a fail is
  likely to be a power fail, in which case the choice waits for 2027 rather
  than being set by hand.
- **Item 7, the NFL late-season PD blend.** Candidate: p = (1 - a(week)) x
  raw sim + a(week) x `nfl_season._wp` on the PD ratings, a(week) = a_max x
  clamp((week - 4) / 10, 0, 1), a_max chosen on 2024 + 2025 from {0, 0.2,
  0.3, 0.4, 0.5} by log loss. Baseline: the raw sim. Test: 2026 weeks 9-18,
  read with 8.3. Pass: > 2 SE clustered by week. It is a better MODEL number
  for the record only; it cannot and need not beat the market. Power: ~150
  games, SE ~0.004; the constant-weight S2 gained 0.003 pooled.
- **Item 8, the NFL game total's level.** Data: `nfl_props_log`'s frozen
  total rungs (five minutes before kickoff), 2026 weeks 9-18: the sim's
  P(over) per rung (`_model_rungs`) and Kalshi's de-vigged P(over), graded
  on the final total. Pass: Kalshi's rung probabilities beat the sim's by
  > 2 SE clustered by game. Then the sim's total level in season is
  re-centred on Kalshi's booked total (college's mechanism), the player pins
  untouched. Read with 8.3.

## Appendix A. NFL machine output (`research/game_xref_nfl.py report`)

NFL replay rows built 2026-10-29 11:29 UTC, 2400 sims a game, trims {'td': 0.992, 'fg': 1.073, 'proj_bias': {'pass_yd': 1.011, 'rush_yd': 0.922, 'rec_yd': 0.986, 'rec': 1.025}, 'hfa': 1.05}; 651 regular-season games (2024: 272, 2025: 272, 2026: 107).

### 1.1 Win probability

**all three seasons** (n = 651)

| model | log loss | Brier | SU accuracy | LL - market (+/- SE) | acc - market (+/- SE) |
|---|---|---|---|---|---|
| drive sim (raw) | 0.634 | 0.2208 | 0.650 (+/- 0.019) | +0.0247 (+/- 0.0069) | -0.026 (+/- 0.014) |
| PD ratings (nfl_season._wp) | 0.6457 | 0.2274 | 0.607 (+/- 0.019) | +0.0365 (+/- 0.0097) | -0.069 (+/- 0.018) |
| ESPN FPI | 0.6362 | 0.2216 | 0.653 (+/- 0.021) | +0.0270 (+/- 0.0065) | -0.023 (+/- 0.015) |
| Sleeper points -> Phi(m/13.2) | 0.6383 | 0.2227 | 0.650 (+/- 0.019) | +0.0291 (+/- 0.0079) | -0.026 (+/- 0.012) |
| closing moneyline, de-vigged | 0.6092 | 0.21 | 0.676 (+/- 0.019) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.54 | 0.6918 | 0.2486 | 0.530 (+/- 0.022) | +0.0826 (+/- 0.0126) | -0.146 (+/- 0.023) |

**2024** (n = 272)

| model | log loss | Brier | SU accuracy | LL - market (+/- SE) | acc - market (+/- SE) |
|---|---|---|---|---|---|
| drive sim (raw) | 0.6177 | 0.2137 | 0.699 (+/- 0.027) | +0.0302 (+/- 0.0116) | -0.018 (+/- 0.021) |
| PD ratings (nfl_season._wp) | 0.6313 | 0.2209 | 0.651 (+/- 0.033) | +0.0438 (+/- 0.0162) | -0.066 (+/- 0.029) |
| ESPN FPI | 0.6146 | 0.2121 | 0.699 (+/- 0.038) | +0.0271 (+/- 0.0113) | -0.018 (+/- 0.027) |
| Sleeper points -> Phi(m/13.2) | 0.6176 | 0.2137 | 0.695 (+/- 0.031) | +0.0301 (+/- 0.0121) | -0.022 (+/- 0.017) |
| closing moneyline, de-vigged | 0.5875 | 0.2002 | 0.717 (+/- 0.032) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.54 | 0.6911 | 0.249 | 0.533 (+/- 0.035) | +0.1036 (+/- 0.0229) | -0.184 (+/- 0.038) |

**2025** (n = 272)

| model | log loss | Brier | SU accuracy | LL - market (+/- SE) | acc - market (+/- SE) |
|---|---|---|---|---|---|
| drive sim (raw) | 0.6426 | 0.2249 | 0.621 (+/- 0.029) | +0.0325 (+/- 0.0095) | -0.029 (+/- 0.023) |
| PD ratings (nfl_season._wp) | 0.6401 | 0.2249 | 0.588 (+/- 0.027) | +0.0299 (+/- 0.0144) | -0.062 (+/- 0.028) |
| ESPN FPI | 0.6413 | 0.2248 | 0.629 (+/- 0.026) | +0.0311 (+/- 0.0086) | -0.022 (+/- 0.020) |
| Sleeper points -> Phi(m/13.2) | 0.6562 | 0.2309 | 0.614 (+/- 0.027) | +0.0460 (+/- 0.0122) | -0.037 (+/- 0.020) |
| closing moneyline, de-vigged | 0.6102 | 0.2116 | 0.651 (+/- 0.028) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.54 | 0.6902 | 0.2476 | 0.540 (+/- 0.035) | +0.0800 (+/- 0.0174) | -0.110 (+/- 0.031) |

**2026** (n = 107)

| model | log loss | Brier | SU accuracy | LL - market (+/- SE) | acc - market (+/- SE) |
|---|---|---|---|---|---|
| drive sim (raw) | 0.6533 | 0.2284 | 0.598 (+/- 0.045) | -0.0088 (+/- 0.0119) | -0.037 (+/- 0.042) |
| PD ratings (nfl_season._wp) | 0.6968 | 0.25 | 0.542 (+/- 0.033) | +0.0347 (+/- 0.0238) | -0.093 (+/- 0.048) |
| ESPN FPI | 0.6783 | 0.2377 | 0.598 (+/- 0.035) | +0.0161 (+/- 0.0185) | -0.037 (+/- 0.040) |
| Sleeper points -> Phi(m/13.2) | 0.6453 | 0.225 | 0.626 (+/- 0.044) | -0.0168 (+/- 0.0061) | -0.009 (+/- 0.026) |
| closing moneyline, de-vigged | 0.6621 | 0.231 | 0.635 (+/- 0.035) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.54 | 0.6979 | 0.25 | 0.495 (+/- 0.047) | +0.0357 (+/- 0.0157) | -0.140 (+/- 0.061) |

Games where a model picks the other side from the closing market favourite, and who was right (all seasons):

| model | disagreements | model right | market right |
|---|---|---|---|
| drive sim (raw) | 93 | 38 | 55 |
| PD ratings (nfl_season._wp) | 167 | 61 | 106 |
| ESPN FPI | 75 | 30 | 45 |
| Sleeper points -> Phi(m/13.2) | 83 | 33 | 50 |

The market favourite's record by how many of the three models (sim, PD, FPI) agree with it:

| models agreeing | games | favourite won | market's own mean p |
|---|---|---|---|
| 0 | 19 | 0.684 | 0.556 |
| 1 | 64 | 0.562 | 0.568 |
| 2 | 150 | 0.633 | 0.602 |
| 3 | 418 | 0.708 | 0.699 |

Straight-up accuracy / log loss by week band (all seasons):

| weeks | n | sim | PD | FPI | Sleeper pts | market |
|---|---|---|---|---|---|---|
| w1-4 | 192 | 0.604 / 0.6459 | 0.531 / 0.6923 | 0.609 / 0.6552 | 0.615 / 0.6479 | 0.646 / 0.6465 |
| w5-9 | 188 | 0.670 / 0.6490 | 0.575 / 0.6608 | 0.675 / 0.6409 | 0.686 / 0.6483 | 0.702 / 0.6197 |
| w10-18 | 271 | 0.668 / 0.6151 | 0.683 / 0.6022 | 0.668 / 0.6194 | 0.649 / 0.6246 | 0.679 / 0.5756 |

Calibration, drive sim (raw), favourite's side, all seasons:

| bin | n | mean predicted | realized |
|---|---|---|---|
| 0.50-0.60 | 324 | 0.55 | 0.568 |
| 0.60-0.70 | 228 | 0.642 | 0.706 |
| 0.70-0.80 | 86 | 0.742 | 0.779 |
| 0.80-0.90 | 13 | 0.822 | 0.846 |

Calibration, closing moneyline, favourite's side, all seasons:

| bin | n | mean predicted | realized |
|---|---|---|---|
| 0.50-0.60 | 228 | 0.558 | 0.588 |
| 0.60-0.70 | 202 | 0.648 | 0.666 |
| 0.70-0.80 | 157 | 0.746 | 0.729 |
| 0.80-0.90 | 59 | 0.845 | 0.881 |
| 0.90-1.00 | 5 | 0.911 | 1.0 |

The calibrator's job, walk-forward (temperature fitted on the earlier seasons, applied to the next):

| fit -> test | temperature t | raw sim LL | calibrated LL | cal - raw (+/- SE) | market LL |
|---|---|---|---|---|---|
| 2024 -> 2025 | 0.6 | 0.6426 | 0.6496 | +0.0069 (+/- 0.0112) | 0.6102 |
| 2024+2025 -> 2026 | 0.72 | 0.6533 | 0.6603 | +0.0070 (+/- 0.0062) | 0.6621 |

### 1.2 Margins, totals, team points

**all three seasons** (n = 651). Margin = home - away; bias = predicted - actual; slope = actual on predicted.

| margin model | bias | MAE | RMSE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|---|
| drive sim mean margin | +0.25 (+/- 0.55) | 10.0 | 12.88 | 1.29 (+/- 0.12) | 4.47 | +0.37 (+/- 0.08) |
| Sleeper points, home - away (no venue term) | -1.12 (+/- 0.55) | 10.03 | 12.99 | 1.16 (+/- 0.12) | 4.77 | +0.41 (+/- 0.10) |
| ESPN FPI margin | -0.24 (+/- 0.53) | 9.97 | 12.71 | 1.05 (+/- 0.08) | 5.73 | +0.35 (+/- 0.09) |
| closing spread (home favoured = +) | -0.05 (+/- 0.50) | 9.62 | 12.37 | 1.17 (+/- 0.08) | 5.76 | +0.00 (+/- 0.00) |

| total model | bias | MAE | RMSE | slope | pred SD | MAE - total line's |
|---|---|---|---|---|---|---|
| drive sim total (shipped trims) | +0.53 (+/- 0.58) | 10.67 | 13.44 | 0.56 (+/- 0.09) | 5.39 | +0.53 (+/- 0.12) |
| Sleeper team points, summed (nfl_data) | -0.45 (+/- 0.54) | 10.51 | 13.31 | 0.64 (+/- 0.10) | 4.76 | +0.37 (+/- 0.09) |
| closing total | -1.26 (+/- 0.48) | 10.15 | 12.88 | 1.05 (+/- 0.10) | 4.16 | +0.00 (+/- 0.00) |

**2024** (n = 272). Margin = home - away; bias = predicted - actual; slope = actual on predicted.

| margin model | bias | MAE | RMSE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|---|
| drive sim mean margin | -0.02 (+/- 0.74) | 10.01 | 13.03 | 1.60 (+/- 0.20) | 4.18 | +0.40 (+/- 0.13) |
| Sleeper points, home - away (no venue term) | -1.34 (+/- 0.73) | 9.99 | 13.15 | 1.45 (+/- 0.19) | 4.41 | +0.38 (+/- 0.14) |
| ESPN FPI margin | -0.61 (+/- 0.72) | 9.91 | 12.8 | 1.25 (+/- 0.16) | 5.47 | +0.30 (+/- 0.15) |
| closing spread (home favoured = +) | -0.38 (+/- 0.66) | 9.61 | 12.61 | 1.30 (+/- 0.17) | 5.56 | +0.00 (+/- 0.00) |

| total model | bias | MAE | RMSE | slope | pred SD | MAE - total line's |
|---|---|---|---|---|---|---|
| drive sim total (shipped trims) | -1.82 (+/- 0.71) | 9.94 | 12.92 | 0.70 (+/- 0.17) | 4.39 | +0.21 (+/- 0.09) |
| Sleeper team points, summed (nfl_data) | -2.45 (+/- 0.70) | 10.01 | 13.01 | 0.74 (+/- 0.20) | 4.11 | +0.28 (+/- 0.11) |
| closing total | -1.48 (+/- 0.68) | 9.73 | 12.55 | 0.99 (+/- 0.18) | 4.01 | +0.00 (+/- 0.00) |

**2025** (n = 272). Margin = home - away; bias = predicted - actual; slope = actual on predicted.

| margin model | bias | MAE | RMSE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|---|
| drive sim mean margin | -0.46 (+/- 0.87) | 10.14 | 12.99 | 1.21 (+/- 0.20) | 4.72 | +0.42 (+/- 0.10) |
| Sleeper points, home - away (no venue term) | -1.91 (+/- 0.88) | 10.32 | 13.23 | 1.06 (+/- 0.19) | 5.07 | +0.60 (+/- 0.17) |
| ESPN FPI margin | -0.72 (+/- 0.81) | 10.11 | 12.74 | 1.01 (+/- 0.10) | 6.15 | +0.39 (+/- 0.11) |
| closing spread (home favoured = +) | -0.57 (+/- 0.77) | 9.72 | 12.27 | 1.14 (+/- 0.10) | 6.23 | +0.00 (+/- 0.00) |

| total model | bias | MAE | RMSE | slope | pred SD | MAE - total line's |
|---|---|---|---|---|---|---|
| drive sim total (shipped trims) | +2.93 (+/- 0.83) | 11.37 | 13.93 | 0.59 (+/- 0.09) | 5.17 | +0.98 (+/- 0.24) |
| Sleeper team points, summed (nfl_data) | +1.45 (+/- 0.80) | 10.92 | 13.59 | 0.68 (+/- 0.11) | 4.57 | +0.53 (+/- 0.16) |
| closing total | -1.15 (+/- 0.75) | 10.39 | 13.19 | 1.00 (+/- 0.07) | 4.17 | +0.00 (+/- 0.00) |

**2026** (n = 107). Margin = home - away; bias = predicted - actual; slope = actual on predicted.

| margin model | bias | MAE | RMSE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|---|
| drive sim mean margin | +2.72 (+/- 0.82) | 9.59 | 12.2 | 0.88 (+/- 0.10) | 4.52 | +0.18 (+/- 0.25) |
| Sleeper points, home - away (no venue term) | +1.40 (+/- 0.78) | 9.41 | 11.88 | 0.88 (+/- 0.11) | 4.82 | +0.00 (+/- 0.21) |
| ESPN FPI margin | +1.93 (+/- 0.77) | 9.78 | 12.39 | 0.63 (+/- 0.14) | 5.26 | +0.37 (+/- 0.25) |
| closing spread (home favoured = +) | +2.12 (+/- 0.87) | 9.41 | 12.03 | 0.84 (+/- 0.17) | 4.96 | +0.00 (+/- 0.00) |

| total model | bias | MAE | RMSE | slope | pred SD | MAE - total line's |
|---|---|---|---|---|---|---|
| drive sim total (shipped trims) | +0.39 (+/- 1.16) | 10.77 | 13.45 | 0.80 (+/- 0.15) | 5.33 | +0.20 (+/- 0.18) |
| Sleeper team points, summed (nfl_data) | -0.17 (+/- 1.14) | 10.75 | 13.37 | 0.96 (+/- 0.19) | 4.51 | +0.17 (+/- 0.12) |
| closing total | -1.00 (+/- 1.12) | 10.57 | 12.94 | 1.29 (+/- 0.28) | 4.45 | +0.00 (+/- 0.00) |

Team points (both sides pooled, n = 1302 team-games): the sim's expected points against the market's implied split (total/2 +/- spread/2).

| team points | bias | MAE | slope | pred SD | actual SD |
|---|---|---|---|---|---|
| drive sim exp points | +0.26 | 7.45 | 0.85 | 3.61 | 9.79 |
| Sleeper team points | -0.22 | 7.44 | 0.91 | 3.38 | 9.79 |
| market implied | -0.63 | 7.17 | 1.12 | 3.63 | 9.79 |

### 2.1 Stacks (the pre-registered tests)

Pre-registered stacks (ridge logistic, lambda 1.0, intercept free), walk-forward. 'vs market' is the paired log-loss difference against the de-vigged close (negative = the stack is better), clustered SE by week.

- S1 [sim] weights: 2025: sim +1.68, b0 -0.20 | 2026: sim +1.41, b0 -0.11
- S2 [sim, pd] weights: 2025: sim +1.34, pd +0.43, b0 -0.24 | 2026: sim +0.99, pd +0.51, b0 -0.14
- S3 [sim, pd, fpi, slp] weights: 2025: sim +0.30, pd +0.35, fpi +0.45, slp +0.77, b0 -0.09 | 2026: sim +0.94, pd +0.34, fpi +0.46, slp -0.24, b0 -0.14
- S4 [mkt] weights: 2025: mkt +1.35, b0 -0.11 | 2026: mkt +1.13, b0 -0.06
- S5 [mkt, sim] weights: 2025: mkt +1.22, sim +0.23, b0 -0.13 | 2026: mkt +1.17, sim -0.06, b0 -0.06
- S6 [mkt, sim, pd, fpi, slp] weights: 2025: mkt +1.13, sim -0.02, pd +0.17, fpi -0.11, slp +0.35, b0 -0.11 | 2026: mkt +1.46, sim +0.62, pd +0.09, fpi -0.56, slp -0.61, b0 -0.17

| stack | pooled test LL | vs market LL | vs market acc | per test season (LL, vs market) | 2-SE pass |
|---|---|---|---|---|---|
| S1 [sim] | 0.6512 | +0.0263 (+/- 0.0120) | -0.034 (+/- 0.014) | 2025: 0.6505 (+0.0403); 2026: 0.6530 (-0.0091) | no |
| S2 [sim, pd] | 0.6479 | +0.0230 (+/- 0.0114) | -0.029 (+/- 0.016) | 2025: 0.6430 (+0.0328); 2026: 0.6605 (-0.0017) | no |
| S3 [sim, pd, fpi, slp] | 0.6582 | +0.0334 (+/- 0.0118) | -0.021 (+/- 0.014) | 2025: 0.6539 (+0.0437); 2026: 0.6692 (+0.0071) | no |
| S4 [mkt] | 0.6319 | +0.0071 (+/- 0.0047) | +0.003 (+/- 0.007) | 2025: 0.6195 (+0.0093); 2026: 0.6635 (+0.0013) | no |
| S5 [mkt, sim] | 0.6332 | +0.0083 (+/- 0.0053) | +0.003 (+/- 0.008) | 2025: 0.6210 (+0.0108); 2026: 0.6640 (+0.0018) | no |
| S6 [mkt, sim, pd, fpi, slp] | 0.6338 | +0.0089 (+/- 0.0063) | +0.000 (+/- 0.009) | 2025: 0.6245 (+0.0143); 2026: 0.6574 (-0.0047) | no |

Pre-registered margin stacks (ridge linear, lambda 1.0), walk-forward; 'vs spread' is the paired MAE difference against the closing line (negative = better), clustered SE.

- M1 [m_sim] weights: 2025: m_sim +1.60, b0 -1.09 | 2026: m_sim +1.38, b0 -0.42
- M2 [spread] weights: 2025: spread +1.30, b0 -0.07 | 2026: spread +1.21, b0 +0.15
- M3 [spread, m_sim] weights: 2025: spread +1.13, m_sim +0.25, b0 -0.29 | 2026: spread +1.25, m_sim -0.06, b0 +0.20
- M4 [spread, m_sim, m_fpi, m_slp] weights: 2025: spread +1.01, m_sim +1.09, m_fpi +0.20, m_slp -0.89, b0 -1.43 | 2026: spread +1.26, m_sim +1.08, m_fpi -0.02, m_slp -1.08, b0 -1.38

| stack | pooled test MAE | vs spread MAE | per test season | 2-SE pass |
|---|---|---|---|---|
| M1 [m_sim] | 10.24 | +0.60 (+/- 0.16) | 2025: MAE 10.36 (+0.64); 2026: MAE 9.91 (+0.50) | no |
| M2 [spread] | 9.83 | +0.20 (+/- 0.08) | 2025: MAE 9.89 (+0.16); 2026: MAE 9.70 (+0.29) | no |
| M3 [spread, m_sim] | 9.83 | +0.20 (+/- 0.09) | 2025: MAE 9.89 (+0.17); 2026: MAE 9.68 (+0.28) | no |
| M4 [spread, m_sim, m_fpi, m_slp] | 9.8 | +0.16 (+/- 0.07) | 2025: MAE 9.85 (+0.13); 2026: MAE 9.66 (+0.25) | no |

Pre-registered total stacks (ridge linear, lambda 1.0), walk-forward; 'vs total' is the paired MAE difference against the closing line (negative = better), clustered SE.

- T1 [t_sim] weights: 2025: t_sim +0.70, b0 +14.99 | 2026: t_sim +0.51, b0 +22.21
- T2 [total] weights: 2025: total +0.99, b0 +1.71 | 2026: total +1.00, b0 +1.43
- T3 [total, t_sim, t_slp] weights: 2025: total +1.15, t_sim -0.53, t_slp +0.39, b0 +1.02 | 2026: total +1.04, t_sim -0.44, t_slp +0.46, b0 -0.27

| stack | pooled test MAE | vs total MAE | per test season | 2-SE pass |
|---|---|---|---|---|
| T1 [t_sim] | 11.23 | +0.79 (+/- 0.18) | 2025: MAE 11.31 (+0.91); 2026: MAE 11.04 (+0.46) | no |
| T2 [total] | 10.44 | -0.01 (+/- 0.07) | 2025: MAE 10.39 (-0.00); 2026: MAE 10.55 (-0.03) | no |
| T3 [total, t_sim, t_slp] | 10.45 | +0.01 (+/- 0.05) | 2025: MAE 10.40 (+0.01); 2026: MAE 10.58 (+0.00) | no |

### 2.2 The market anchor and scale-only corrections

Market anchor on the win probability, walk-forward: w chosen on the fit seasons from {0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0} by log loss; p = w x sim + (1 - w) x market.

| fit -> test | chosen w | test LL | test SU acc | vs raw sim LL | vs market LL |
|---|---|---|---|---|---|
| 2024 -> 2025 | 0.0 | 0.6102 | 0.651 | -0.0325 (+/- 0.0095) | +0.0000 (+/- 0.0000) |
| 2024+2025 -> 2026 | 0.0 | 0.6621 | 0.635 | +0.0088 (+/- 0.0119) | +0.0000 (+/- 0.0000) |

Every w on the pooled test seasons (2025 + 2026), for the shape of the curve, with each test season's own difference against the raw sim:

| w (model share) | pooled test LL | SU acc | vs raw sim LL | per season |
|---|---|---|---|---|
| 0.0 | 0.6249 | 0.646 | -0.0208 (+/- 0.0084) | 2025: -0.0325 (+/- 0.0095); 2026: +0.0088 (+/- 0.0119) |
| 0.1 | 0.625 | 0.644 | -0.0206 (+/- 0.0076) | 2025: -0.0315 (+/- 0.0085); 2026: +0.0069 (+/- 0.0107) |
| 0.2 | 0.6257 | 0.636 | -0.0200 (+/- 0.0068) | 2025: -0.0299 (+/- 0.0075); 2026: +0.0052 (+/- 0.0095) |
| 0.3 | 0.6267 | 0.631 | -0.0189 (+/- 0.0059) | 2025: -0.0278 (+/- 0.0065); 2026: +0.0038 (+/- 0.0084) |
| 0.5 | 0.6302 | 0.638 | -0.0155 (+/- 0.0043) | 2025: -0.0222 (+/- 0.0046); 2026: +0.0016 (+/- 0.0060) |
| 0.7 | 0.6352 | 0.625 | -0.0105 (+/- 0.0026) | 2025: -0.0147 (+/- 0.0027); 2026: +0.0003 (+/- 0.0037) |
| 1.0 | 0.6457 | 0.615 | +0.0000 (+/- 0.0000) | 2025: +0.0000 (+/- 0.0000); 2026: +0.0000 (+/- 0.0000) |

Scale-only corrections, walk-forward (no intercept; the fit seasons' slope applied to the next season):

| correction | fitted | test MAE | vs raw sim MAE | vs closing line MAE |
|---|---|---|---|---|
| margin x k, 2024 -> 2025 | k = 1.51 | 10.30 | +0.16 (+/- 0.18) | +0.58 (+/- 0.18) |
| total shrunk, 2024 -> 2025 | k_t = 0.70, level 45.8 | 11.31 | -0.07 (+/- 0.08) | +0.91 (+/- 0.24) |
| margin x k, 2024+2025 -> 2026 | k = 1.35 | 9.96 | +0.37 (+/- 0.09) | +0.55 (+/- 0.25) |
| total shrunk, 2024+2025 -> 2026 | k_t = 0.51, level 45.9 | 11.04 | +0.26 (+/- 0.18) | +0.46 (+/- 0.23) |

## Appendix B. College machine output (`research/game_xref_cfb.py report`)

College replay rows built 2026-10-29 13:58 UTC (joined 2026-10-29 14:03 UTC), 1000 engine games a matchup, constants {'prior_k': 1.5, 'cap': 50.0, 'hfa': 3.0, 'sigma': 18.0, 'reg': 0.58, 'div_gap': 27.5, 'base_ppg': 27.5, 'anchor_w': 0.25}; 4065 completed games with both sides rated (2024: 1542, 2025: 1556, 2026: 967); priced (ESPN BET spread + moneyline stored): {2024: 806, 2025: 798, 2026: 498}; by division {'fbs': 1916, 'cross': 409, 'fcs': 1740}.

### 3.1 Win probability

**Priced games** (ESPN BET close spread and moneyline present), by season and division. 'vs market' = paired log-loss difference against the de-vigged close (negative = better), clustered SE by week.

*all seasons, all divisions* (n = 2077)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.5386 | 0.1823 | 0.717 (+/- 0.012) | +0.0361 (+/- 0.0079) | -0.029 (+/- 0.009) |
| drive engine, model level | 0.5488 | 0.1857 | 0.722 (+/- 0.013) | +0.0463 (+/- 0.0083) | -0.024 (+/- 0.009) |
| board headline (25% model / 75% market level) | 0.5246 | 0.1751 | 0.746 (+/- 0.012) | +0.0221 (+/- 0.0040) | +0.000 (+/- 0.004) |
| ESPN FPI | 0.5145 | 0.1732 | 0.735 (+/- 0.012) | +0.0120 (+/- 0.0042) | -0.011 (+/- 0.007) |
| closing moneyline, de-vigged | 0.5025 | 0.1681 | 0.746 (+/- 0.012) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.6717 | 0.2393 | 0.619 (+/- 0.016) | +0.1691 (+/- 0.0142) | -0.127 (+/- 0.013) |

*all seasons, fbs* (n = 1824)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.5732 | 0.1958 | 0.692 (+/- 0.013) | +0.0360 (+/- 0.0085) | -0.031 (+/- 0.010) |
| drive engine, model level | 0.5825 | 0.1992 | 0.697 (+/- 0.013) | +0.0453 (+/- 0.0089) | -0.026 (+/- 0.010) |
| board headline (25% model / 75% market level) | 0.5575 | 0.1881 | 0.723 (+/- 0.011) | +0.0203 (+/- 0.0044) | +0.000 (+/- 0.005) |
| ESPN FPI | 0.5507 | 0.187 | 0.711 (+/- 0.011) | +0.0135 (+/- 0.0048) | -0.012 (+/- 0.007) |
| closing moneyline, de-vigged | 0.5372 | 0.1813 | 0.723 (+/- 0.011) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.6804 | 0.2437 | 0.583 (+/- 0.013) | +0.1432 (+/- 0.0120) | -0.140 (+/- 0.014) |

*all seasons, cross* (n = 251)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.2905 | 0.0855 | 0.892 (+/- 0.018) | +0.0363 (+/- 0.0137) | -0.016 (+/- 0.015) |
| drive engine, model level | 0.3063 | 0.0885 | 0.896 (+/- 0.018) | +0.0521 (+/- 0.0133) | -0.012 (+/- 0.014) |
| board headline (25% model / 75% market level) | 0.2891 | 0.082 | 0.908 (+/- 0.020) | +0.0349 (+/- 0.0070) | +0.000 (+/- 0.006) |
| ESPN FPI | 0.2551 | 0.0745 | 0.908 (+/- 0.018) | +0.0009 (+/- 0.0110) | +0.000 (+/- 0.010) |
| closing moneyline, de-vigged | 0.2542 | 0.0741 | 0.908 (+/- 0.021) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.6077 | 0.2075 | 0.884 (+/- 0.019) | +0.3535 (+/- 0.0279) | -0.024 (+/- 0.018) |

*2024, all divisions* (n = 781)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.5399 | 0.1839 | 0.708 (+/- 0.024) | +0.0244 (+/- 0.0083) | -0.018 (+/- 0.018) |
| drive engine, model level | 0.5476 | 0.1859 | 0.722 (+/- 0.023) | +0.0321 (+/- 0.0083) | -0.004 (+/- 0.017) |
| board headline (25% model / 75% market level) | 0.5305 | 0.1781 | 0.726 (+/- 0.018) | +0.0149 (+/- 0.0051) | +0.000 (+/- 0.009) |
| ESPN FPI | 0.5251 | 0.1783 | 0.722 (+/- 0.021) | +0.0095 (+/- 0.0072) | -0.004 (+/- 0.010) |
| closing moneyline, de-vigged | 0.5155 | 0.1738 | 0.726 (+/- 0.019) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.674 | 0.2405 | 0.610 (+/- 0.019) | +0.1585 (+/- 0.0207) | -0.117 (+/- 0.019) |

*2024, fbs* (n = 702)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.5741 | 0.1968 | 0.685 (+/- 0.027) | +0.0274 (+/- 0.0105) | -0.019 (+/- 0.020) |
| drive engine, model level | 0.5807 | 0.1989 | 0.701 (+/- 0.026) | +0.0340 (+/- 0.0102) | -0.003 (+/- 0.019) |
| board headline (25% model / 75% market level) | 0.5607 | 0.1902 | 0.704 (+/- 0.017) | +0.0141 (+/- 0.0062) | +0.000 (+/- 0.010) |
| ESPN FPI | 0.5577 | 0.1907 | 0.701 (+/- 0.020) | +0.0110 (+/- 0.0078) | -0.003 (+/- 0.011) |
| closing moneyline, de-vigged | 0.5467 | 0.1857 | 0.704 (+/- 0.018) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.6818 | 0.2444 | 0.577 (+/- 0.016) | +0.1352 (+/- 0.0162) | -0.127 (+/- 0.020) |

*2024, cross* (n = 79)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.2363 | 0.069 | 0.911 (+/- 0.037) | -0.0026 (+/- 0.0156) | -0.013 (+/- 0.008) |
| drive engine, model level | 0.2538 | 0.0704 | 0.911 (+/- 0.037) | +0.0150 (+/- 0.0182) | -0.013 (+/- 0.008) |
| board headline (25% model / 75% market level) | 0.2614 | 0.0713 | 0.924 (+/- 0.044) | +0.0226 (+/- 0.0132) | +0.000 (+/- 0.000) |
| ESPN FPI | 0.235 | 0.0685 | 0.911 (+/- 0.037) | -0.0039 (+/- 0.0124) | -0.013 (+/- 0.008) |
| closing moneyline, de-vigged | 0.2388 | 0.0682 | 0.924 (+/- 0.044) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.6042 | 0.2058 | 0.899 (+/- 0.047) | +0.3654 (+/- 0.0640) | -0.025 (+/- 0.028) |

*2025, all divisions* (n = 798)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.5307 | 0.1779 | 0.734 (+/- 0.012) | +0.0208 (+/- 0.0111) | -0.014 (+/- 0.009) |
| drive engine, model level | 0.5437 | 0.1826 | 0.728 (+/- 0.011) | +0.0338 (+/- 0.0109) | -0.020 (+/- 0.012) |
| board headline (25% model / 75% market level) | 0.5303 | 0.1769 | 0.752 (+/- 0.015) | +0.0204 (+/- 0.0056) | +0.004 (+/- 0.005) |
| ESPN FPI | 0.5112 | 0.171 | 0.737 (+/- 0.011) | +0.0013 (+/- 0.0049) | -0.011 (+/- 0.013) |
| closing moneyline, de-vigged | 0.5099 | 0.1706 | 0.748 (+/- 0.014) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.6744 | 0.2407 | 0.608 (+/- 0.028) | +0.1645 (+/- 0.0177) | -0.140 (+/- 0.022) |

*2025, fbs* (n = 702)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.554 | 0.1876 | 0.715 (+/- 0.011) | +0.0177 (+/- 0.0117) | -0.017 (+/- 0.010) |
| drive engine, model level | 0.5678 | 0.1925 | 0.707 (+/- 0.014) | +0.0314 (+/- 0.0120) | -0.026 (+/- 0.014) |
| board headline (25% model / 75% market level) | 0.5553 | 0.1866 | 0.735 (+/- 0.013) | +0.0189 (+/- 0.0064) | +0.003 (+/- 0.005) |
| ESPN FPI | 0.5394 | 0.1814 | 0.717 (+/- 0.011) | +0.0030 (+/- 0.0050) | -0.016 (+/- 0.014) |
| closing moneyline, de-vigged | 0.5364 | 0.1804 | 0.732 (+/- 0.012) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.6829 | 0.2449 | 0.573 (+/- 0.023) | +0.1465 (+/- 0.0164) | -0.160 (+/- 0.021) |

*2025, cross* (n = 95)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.3617 | 0.1079 | 0.874 (+/- 0.031) | +0.0426 (+/- 0.0132) | +0.011 (+/- 0.025) |
| drive engine, model level | 0.3691 | 0.1106 | 0.884 (+/- 0.028) | +0.0500 (+/- 0.0118) | +0.021 (+/- 0.023) |
| board headline (25% model / 75% market level) | 0.3498 | 0.1066 | 0.874 (+/- 0.031) | +0.0308 (+/- 0.0070) | +0.011 (+/- 0.012) |
| ESPN FPI | 0.3086 | 0.0957 | 0.884 (+/- 0.029) | -0.0105 (+/- 0.0225) | +0.021 (+/- 0.017) |
| closing moneyline, de-vigged | 0.3191 | 0.1003 | 0.863 (+/- 0.034) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.6128 | 0.21 | 0.863 (+/- 0.031) | +0.2937 (+/- 0.0371) | +0.000 (+/- 0.027) |

*2026, all divisions* (n = 498)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.5495 | 0.1869 | 0.703 (+/- 0.031) | +0.0791 (+/- 0.0178) | -0.070 (+/- 0.015) |
| drive engine, model level | 0.5588 | 0.1903 | 0.711 (+/- 0.035) | +0.0885 (+/- 0.0211) | -0.062 (+/- 0.013) |
| board headline (25% model / 75% market level) | 0.5065 | 0.1676 | 0.767 (+/- 0.035) | +0.0362 (+/- 0.0102) | -0.006 (+/- 0.007) |
| ESPN FPI | 0.5032 | 0.1687 | 0.753 (+/- 0.035) | +0.0329 (+/- 0.0080) | -0.020 (+/- 0.012) |
| closing moneyline, de-vigged | 0.4704 | 0.1553 | 0.773 (+/- 0.033) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.6636 | 0.2353 | 0.653 (+/- 0.042) | +0.1932 (+/- 0.0418) | -0.120 (+/- 0.032) |

*2026, fbs* (n = 420)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.6038 | 0.2079 | 0.667 (+/- 0.028) | +0.0810 (+/- 0.0206) | -0.074 (+/- 0.018) |
| drive engine, model level | 0.6103 | 0.211 | 0.676 (+/- 0.034) | +0.0875 (+/- 0.0245) | -0.064 (+/- 0.015) |
| board headline (25% model / 75% market level) | 0.5558 | 0.1872 | 0.736 (+/- 0.031) | +0.0330 (+/- 0.0119) | -0.005 (+/- 0.010) |
| ESPN FPI | 0.5581 | 0.1901 | 0.719 (+/- 0.033) | +0.0353 (+/- 0.0106) | -0.021 (+/- 0.014) |
| closing moneyline, de-vigged | 0.5228 | 0.1754 | 0.741 (+/- 0.030) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.674 | 0.2405 | 0.610 (+/- 0.034) | +0.1512 (+/- 0.0371) | -0.131 (+/- 0.034) |

*2026, cross* (n = 77)

| model | log loss | Brier | SU accuracy | LL - market | acc - market |
|---|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 0.2583 | 0.0747 | 0.896 (+/- 0.023) | +0.0684 (+/- 0.0212) | -0.052 (+/- 0.026) |
| drive engine, model level | 0.2826 | 0.0796 | 0.896 (+/- 0.023) | +0.0928 (+/- 0.0219) | -0.052 (+/- 0.026) |
| board headline (25% model / 75% market level) | 0.2427 | 0.0628 | 0.935 (+/- 0.023) | +0.0528 (+/- 0.0161) | -0.013 (+/- 0.011) |
| ESPN FPI | 0.2097 | 0.0544 | 0.935 (+/- 0.031) | +0.0199 (+/- 0.0129) | -0.013 (+/- 0.019) |
| closing moneyline, de-vigged | 0.1899 | 0.0477 | 0.948 (+/- 0.031) | +0.0000 (+/- 0.0000) | +0.000 (+/- 0.000) |
| constant home 0.56 | 0.6049 | 0.2061 | 0.896 (+/- 0.015) | +0.4150 (+/- 0.0383) | -0.052 (+/- 0.039) |

**All rated games** (no market needed), by division: the raw ratings against the engine and FPI where FPI exists.

*all divisions*

| model | n | log loss | Brier | SU accuracy |
|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 4065 | 0.5168 | 0.1738 | 0.736 (+/- 0.010) |
| drive engine, model level | 4065 | 0.5292 | 0.1777 | 0.739 (+/- 0.011) |
| ESPN FPI | 2331 | 0.4658 | 0.1561 | 0.762 (+/- 0.014) |
| constant home 0.56 | 4065 | 0.6715 | 0.2392 | 0.620 (+/- 0.014) |

*fbs*

| model | n | log loss | Brier | SU accuracy |
|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 1916 | 0.5568 | 0.1894 | 0.704 (+/- 0.013) |
| drive engine, model level | 1916 | 0.5677 | 0.1931 | 0.709 (+/- 0.013) |
| ESPN FPI | 1916 | 0.5301 | 0.1796 | 0.723 (+/- 0.011) |
| constant home 0.56 | 1916 | 0.6771 | 0.242 | 0.597 (+/- 0.014) |

*cross*

| model | n | log loss | Brier | SU accuracy |
|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 409 | 0.1975 | 0.0552 | 0.931 (+/- 0.012) |
| drive engine, model level | 409 | 0.2193 | 0.0585 | 0.934 (+/- 0.012) |
| ESPN FPI | 409 | 0.1677 | 0.0472 | 0.941 (+/- 0.011) |
| constant home 0.56 | 409 | 0.5975 | 0.2024 | 0.927 (+/- 0.013) |

*fcs*

| model | n | log loss | Brier | SU accuracy |
|---|---|---|---|---|
| ratings Phi(m/18) (p_home_raw) | 1740 | 0.5479 | 0.1844 | 0.724 (+/- 0.011) |
| drive engine, model level | 1740 | 0.5597 | 0.1888 | 0.726 (+/- 0.011) |
| constant home 0.56 | 1740 | 0.6827 | 0.2448 | 0.574 (+/- 0.012) |

Priced games where a model picks the other side from the market favourite (all seasons):

| model | disagreements | model right | market right |
|---|---|---|---|
| raw ratings | 272 | 106 | 166 |
| board headline | 74 | 37 | 37 |
| ESPN FPI | 166 | 72 | 94 |

The market favourite's record by whether the raw ratings and FPI agree with it:

| models agreeing (of 2) | games | favourite won | market's mean p |
|---|---|---|---|
| 0 | 127 | 0.535 | 0.562 |
| 1 | 184 | 0.674 | 0.623 |
| 2 | 1766 | 0.768 | 0.758 |

The quick-combo tiers on the raw ratings (the LOWER of model and market sets the tier; here each number's own bins), priced games:

| tier | number | n | mean predicted | realized |
|---|---|---|---|---|
| 0.80-0.90 | raw ratings | 317 | 0.846 | 0.877 |
| 0.80-0.90 | market | 394 | 0.847 | 0.896 |
| 0.90-0.95 | raw ratings | 142 | 0.925 | 0.944 |
| 0.90-0.95 | market | 224 | 0.926 | 0.951 |
| 0.95-1.00 | raw ratings | 105 | 0.966 | 0.981 |
| 0.95-1.00 | market | 126 | 0.966 | 0.984 |

Straight-up accuracy / log loss by week band, priced games, all seasons:

| weeks | n | raw ratings | headline | FPI | market |
|---|---|---|---|---|---|
| w1-3 | 562 | 0.767 / 0.4752 | 0.819 / 0.4229 | 0.801 / 0.4078 | 0.811 / 0.3878 |
| w4-8 | 826 | 0.689 / 0.5725 | 0.725 / 0.5643 | 0.711 / 0.5662 | 0.730 / 0.5473 |
| w9-16 | 689 | 0.710 / 0.5499 | 0.711 / 0.5601 | 0.711 / 0.5396 | 0.711 / 0.5424 |

Calibration, raw ratings Phi(m/18), favourite's side, priced games, all seasons:

| bin | n | mean predicted | realized |
|---|---|---|---|
| 0.50-0.60 | 591 | 0.549 | 0.547 |
| 0.60-0.70 | 535 | 0.649 | 0.667 |
| 0.70-0.80 | 411 | 0.748 | 0.752 |
| 0.80-0.90 | 318 | 0.846 | 0.877 |
| 0.90-1.00 | 247 | 0.942 | 0.96 |

Calibration, board headline, favourite's side, priced games, all seasons:

| bin | n | mean predicted | realized |
|---|---|---|---|
| 0.50-0.60 | 781 | 0.551 | 0.597 |
| 0.60-0.70 | 540 | 0.647 | 0.724 |
| 0.70-0.80 | 394 | 0.747 | 0.86 |
| 0.80-0.90 | 264 | 0.847 | 0.939 |
| 0.90-1.00 | 123 | 0.928 | 1.0 |

Calibration, closing moneyline, favourite's side, priced games, all seasons:

| bin | n | mean predicted | realized |
|---|---|---|---|
| 0.50-0.60 | 488 | 0.553 | 0.557 |
| 0.60-0.70 | 404 | 0.648 | 0.668 |
| 0.70-0.80 | 456 | 0.747 | 0.715 |
| 0.80-0.90 | 399 | 0.847 | 0.897 |
| 0.90-1.00 | 355 | 0.94 | 0.963 |

**The engine step on the headline.** `p_fair` is Phi(fair margin / 18) at the shipped 25/75 blend, the number the board would show if the engine's win frequency were not used for the headline. Paired log loss, clustered SE.

| season | n | engine headline LL | p_fair LL | market LL | p_fair - engine | p_fair - market | engine - market | SU acc engine / p_fair / market |
|---|---|---|---|---|---|---|---|---|
| all | 2102 | 0.5243 | 0.5081 | 0.5022 | -0.0162 (+/- 0.0018) | +0.0059 (+/- 0.0024) | +0.0221 (+/- 0.0039) | 0.746 / 0.747 / 0.746 |
| 2024 | 806 | 0.5293 | 0.5158 | 0.5142 | -0.0135 (+/- 0.0031) | +0.0016 (+/- 0.0028) | +0.0151 (+/- 0.0049) | 0.726 / 0.728 / 0.727 |
| 2025 | 798 | 0.5303 | 0.5136 | 0.5099 | -0.0166 (+/- 0.0025) | +0.0037 (+/- 0.0037) | +0.0204 (+/- 0.0056) | 0.752 / 0.754 / 0.748 |
| 2026 | 498 | 0.5065 | 0.4868 | 0.4704 | -0.0198 (+/- 0.0045) | +0.0164 (+/- 0.0059) | +0.0362 (+/- 0.0102) | 0.767 / 0.765 / 0.773 |

Calibration of p_fair (favourite's side, all priced games):

| bin | n | mean predicted | realized |
|---|---|---|---|
| 0.50-0.60 | 608 | 0.555 | 0.587 |
| 0.60-0.70 | 516 | 0.648 | 0.68 |
| 0.70-0.80 | 388 | 0.748 | 0.784 |
| 0.80-0.90 | 346 | 0.848 | 0.925 |
| 0.90-1.00 | 244 | 0.94 | 0.975 |

**The engine's margin against the margin it was given.** `fair_in` is the blended input margin; `m_blend` the engine's mean margin at that level. The ATS pick reads the simulated margins at Kalshi's line, so a mean below the line is a pick on the underdog.

| division | n | mean (m_blend - fair_in) | same, lines of 14+ | games with |line| >= 3 | engine on the dog's side | dog covered when picked | favourite covered, all |
|---|---|---|---|---|---|---|---|
| all | 2102 | -1.67 | -3.75 | 1746 | 94.8% | 49.4% | 50.3% |
| fbs | 1824 | -1.17 | -2.72 | 1483 | 94.9% | 50.5% | 49.2% |
| cross | 251 | -5.25 | -6.36 | 239 | 93.7% | 41.1% | 59.0% |

**The total lean.** The card leans under when the engine's total sits below Kalshi's line. With the engine's total near-constant, the lean is a function of the line alone.

| division | n | leans under | under hit when leaned under | over hit when leaned over | mean line, under-leans / over-leans |
|---|---|---|---|---|---|
| all | 2324 | 30.4% | 50.1% | 51.2% | 59.1 / 49.6 |
| fbs | 1887 | 24.5% | 51.6% | 51.8% | 59.8 / 49.6 |
| cross | 382 | 60.7% | 45.7% | 50.0% | 57.7 / 49.2 |

### 3.2 Margins, totals, team points

The card's total across 2335 priced games: mean 56.0, SD 2.34 (the closing total's SD 6.14, the actual total's 16.37); correlation with the closing total -0.21, with the actual total -0.09 (the closing total's with the actual 0.31).

*all seasons, all divisions* (n = 2275 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -1.74 (+/- 0.56) | 13.26 | 1.03 (+/- 0.02) | 14.74 | +1.38 (+/- 0.26) |
| drive engine mean margin | -4.03 (+/- 0.77) | 13.94 | 1.37 (+/- 0.03) | 11.03 | +2.06 (+/- 0.36) |
| board fair margin (25/75) | -3.78 (+/- 0.61) | 12.97 | 1.44 (+/- 0.03) | 11.53 | +1.10 (+/- 0.19) |
| ESPN FPI margin | -1.38 (+/- 0.37) | 12.32 | 1.05 (+/- 0.02) | 15.5 | +0.45 (+/- 0.08) |
| closing spread (home favoured = +) | -1.07 (+/- 0.29) | 11.87 | 1.02 (+/- 0.02) | 16.36 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | +2.58 (+/- 0.36) | 13.67 | -0.59 (+/- 0.12) | 2.34 | 16.41 | +1.23 (+/- 0.14) | +0.00 (+/- 0.00) |
| engine total at the blended level | +2.48 (+/- 0.39) | 13.72 | -0.68 (+/- 0.13) | 2.47 | 16.41 | +1.29 (+/- 0.14) | +0.05 (+/- 0.03) |
| closing total | -0.93 (+/- 0.37) | 12.43 | 0.83 (+/- 0.06) | 6.16 | 16.41 | +0.00 (+/- 0.00) | -1.23 (+/- 0.14) |
| offence/defence rating total (K 4.0) | +1.51 (+/- 0.46) | 13.01 | 0.55 (+/- 0.05) | 7.01 | 16.41 | +0.58 (+/- 0.13) | -0.66 (+/- 0.14) |

*all seasons, fbs* (n = 1887 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -1.33 (+/- 0.55) | 13.07 | 0.99 (+/- 0.03) | 11.68 | +1.21 (+/- 0.23) |
| drive engine mean margin | -2.60 (+/- 0.58) | 13.31 | 1.30 (+/- 0.04) | 8.92 | +1.45 (+/- 0.26) |
| board fair margin (25/75) | -2.29 (+/- 0.39) | 12.4 | 1.39 (+/- 0.03) | 9.59 | +0.54 (+/- 0.08) |
| ESPN FPI margin | -0.76 (+/- 0.34) | 12.19 | 1.02 (+/- 0.03) | 12.76 | +0.33 (+/- 0.07) |
| closing spread (home favoured = +) | -0.69 (+/- 0.29) | 11.86 | 1.00 (+/- 0.02) | 13.37 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | +3.84 (+/- 0.35) | 13.77 | -0.33 (+/- 0.23) | 1.52 | 16.48 | +1.23 (+/- 0.16) | +0.00 (+/- 0.00) |
| engine total at the blended level | +3.69 (+/- 0.33) | 13.8 | -0.39 (+/- 0.18) | 1.78 | 16.48 | +1.26 (+/- 0.16) | +0.03 (+/- 0.02) |
| closing total | -0.78 (+/- 0.38) | 12.54 | 0.81 (+/- 0.06) | 6.18 | 16.48 | +0.00 (+/- 0.00) | -1.23 (+/- 0.16) |
| offence/defence rating total (K 4.0) | +1.76 (+/- 0.43) | 13.05 | 0.58 (+/- 0.05) | 7.09 | 16.48 | +0.51 (+/- 0.13) | -0.72 (+/- 0.13) |

*all seasons, cross* (n = 382 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -3.65 (+/- 0.75) | 14.14 | 0.96 (+/- 0.05) | 12.97 | +2.21 (+/- 0.35) |
| drive engine mean margin | -10.98 (+/- 0.79) | 16.94 | 1.33 (+/- 0.07) | 9.15 | +5.00 (+/- 0.53) |
| board fair margin (25/75) | -11.08 (+/- 0.73) | 15.79 | 1.50 (+/- 0.08) | 9.91 | +3.86 (+/- 0.41) |
| ESPN FPI margin | -4.43 (+/- 0.72) | 12.93 | 0.97 (+/- 0.04) | 14.81 | +0.99 (+/- 0.26) |
| closing spread (home favoured = +) | -2.96 (+/- 0.68) | 11.93 | 0.97 (+/- 0.04) | 15.78 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | -3.75 (+/- 0.66) | 13.2 | -0.63 (+/- 0.30) | 2.46 | 15.82 | +1.23 (+/- 0.28) | +0.00 (+/- 0.00) |
| engine total at the blended level | -3.58 (+/- 0.69) | 13.4 | -1.08 (+/- 0.31) | 2.72 | 15.82 | +1.43 (+/- 0.29) | +0.20 (+/- 0.09) |
| closing total | -1.78 (+/- 0.64) | 11.97 | 0.87 (+/- 0.14) | 5.71 | 15.82 | +0.00 (+/- 0.00) | -1.23 (+/- 0.28) |
| offence/defence rating total (K 4.0) | +0.10 (+/- 1.07) | 12.79 | 0.34 (+/- 0.14) | 6.45 | 15.82 | +0.82 (+/- 0.36) | -0.41 (+/- 0.41) |

*2024, all divisions* (n = 829 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -0.90 (+/- 0.64) | 13.25 | 1.00 (+/- 0.04) | 14.3 | +1.24 (+/- 0.35) |
| drive engine mean margin | -3.05 (+/- 0.95) | 13.66 | 1.33 (+/- 0.05) | 10.76 | +1.64 (+/- 0.48) |
| board fair margin (25/75) | -3.23 (+/- 0.76) | 12.88 | 1.40 (+/- 0.05) | 11.19 | +0.87 (+/- 0.25) |
| ESPN FPI margin | -0.62 (+/- 0.50) | 12.4 | 1.01 (+/- 0.04) | 15.18 | +0.38 (+/- 0.13) |
| closing spread (home favoured = +) | -1.11 (+/- 0.43) | 12.02 | 1.01 (+/- 0.03) | 15.53 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | +2.18 (+/- 0.48) | 13.57 | -0.34 (+/- 0.17) | 2.23 | 16.67 | +0.87 (+/- 0.20) | +0.00 (+/- 0.00) |
| engine total at the blended level | +2.14 (+/- 0.51) | 13.61 | -0.40 (+/- 0.17) | 2.36 | 16.67 | +0.91 (+/- 0.20) | +0.04 (+/- 0.03) |
| closing total | -1.58 (+/- 0.58) | 12.7 | 0.69 (+/- 0.09) | 6.7 | 16.67 | +0.00 (+/- 0.00) | -0.87 (+/- 0.20) |
| offence/defence rating total (K 4.0) | +1.05 (+/- 0.66) | 13.07 | 0.53 (+/- 0.07) | 7.31 | 16.67 | +0.38 (+/- 0.23) | -0.50 (+/- 0.25) |

*2024, fbs* (n = 714 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -0.66 (+/- 0.61) | 13.17 | 0.97 (+/- 0.04) | 11.78 | +1.03 (+/- 0.28) |
| drive engine mean margin | -1.99 (+/- 0.64) | 13.3 | 1.26 (+/- 0.06) | 9.0 | +1.16 (+/- 0.33) |
| board fair margin (25/75) | -2.11 (+/- 0.49) | 12.59 | 1.35 (+/- 0.05) | 9.47 | +0.45 (+/- 0.12) |
| ESPN FPI margin | -0.31 (+/- 0.48) | 12.35 | 0.98 (+/- 0.04) | 12.88 | +0.20 (+/- 0.10) |
| closing spread (home favoured = +) | -0.80 (+/- 0.45) | 12.14 | 0.99 (+/- 0.04) | 12.99 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | +3.03 (+/- 0.43) | 13.54 | -0.07 (+/- 0.31) | 1.54 | 16.6 | +0.78 (+/- 0.18) | +0.00 (+/- 0.00) |
| engine total at the blended level | +2.96 (+/- 0.40) | 13.57 | -0.08 (+/- 0.23) | 1.73 | 16.6 | +0.80 (+/- 0.19) | +0.03 (+/- 0.03) |
| closing total | -1.46 (+/- 0.60) | 12.77 | 0.66 (+/- 0.09) | 6.8 | 16.6 | +0.00 (+/- 0.00) | -0.78 (+/- 0.18) |
| offence/defence rating total (K 4.0) | +1.10 (+/- 0.59) | 13.02 | 0.54 (+/- 0.07) | 7.29 | 16.6 | +0.25 (+/- 0.18) | -0.53 (+/- 0.20) |

*2024, cross* (n = 114 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -2.56 (+/- 0.84) | 13.74 | 0.94 (+/- 0.11) | 12.44 | +2.51 (+/- 0.41) |
| drive engine mean margin | -9.82 (+/- 0.99) | 15.85 | 1.32 (+/- 0.15) | 8.88 | +4.62 (+/- 0.27) |
| board fair margin (25/75) | -10.38 (+/- 1.01) | 14.72 | 1.42 (+/- 0.15) | 10.3 | +3.48 (+/- 0.22) |
| ESPN FPI margin | -2.70 (+/- 1.11) | 12.68 | 0.94 (+/- 0.09) | 13.91 | +1.44 (+/- 0.29) |
| closing spread (home favoured = +) | -3.14 (+/- 0.92) | 11.23 | 0.95 (+/- 0.08) | 15.88 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | -3.31 (+/- 1.13) | 13.65 | -0.76 (+/- 0.67) | 2.18 | 17.05 | +1.36 (+/- 0.60) | +0.00 (+/- 0.00) |
| engine total at the blended level | -3.12 (+/- 1.13) | 13.79 | -1.02 (+/- 0.54) | 2.72 | 17.05 | +1.50 (+/- 0.62) | +0.14 (+/- 0.11) |
| closing total | -2.39 (+/- 1.57) | 12.29 | 0.90 (+/- 0.46) | 5.95 | 17.05 | +0.00 (+/- 0.00) | -1.36 (+/- 0.60) |
| offence/defence rating total (K 4.0) | +0.61 (+/- 2.50) | 13.39 | 0.41 (+/- 0.37) | 7.32 | 17.05 | +1.10 (+/- 0.51) | -0.27 (+/- 0.75) |

*2025, all divisions* (n = 876 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -1.46 (+/- 0.71) | 12.85 | 1.02 (+/- 0.04) | 14.74 | +0.91 (+/- 0.36) |
| drive engine mean margin | -3.59 (+/- 1.01) | 13.5 | 1.36 (+/- 0.05) | 11.04 | +1.56 (+/- 0.45) |
| board fair margin (25/75) | -3.35 (+/- 0.83) | 12.8 | 1.43 (+/- 0.05) | 11.31 | +0.86 (+/- 0.18) |
| ESPN FPI margin | -1.44 (+/- 0.55) | 12.31 | 1.09 (+/- 0.03) | 14.44 | +0.37 (+/- 0.15) |
| closing spread (home favoured = +) | -0.86 (+/- 0.46) | 11.94 | 1.02 (+/- 0.03) | 15.86 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | +3.11 (+/- 0.63) | 13.61 | -0.59 (+/- 0.20) | 2.3 | 16.18 | +1.33 (+/- 0.26) | +0.00 (+/- 0.00) |
| engine total at the blended level | +3.06 (+/- 0.67) | 13.62 | -0.64 (+/- 0.21) | 2.36 | 16.18 | +1.34 (+/- 0.24) | +0.01 (+/- 0.04) |
| closing total | -0.34 (+/- 0.68) | 12.29 | 0.89 (+/- 0.07) | 6.14 | 16.18 | +0.00 (+/- 0.00) | -1.33 (+/- 0.26) |
| offence/defence rating total (K 4.0) | +1.82 (+/- 0.89) | 12.87 | 0.61 (+/- 0.07) | 7.18 | 16.18 | +0.59 (+/- 0.18) | -0.74 (+/- 0.20) |

*2025, fbs* (n = 738 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -1.28 (+/- 0.88) | 12.73 | 1.04 (+/- 0.06) | 11.98 | +0.82 (+/- 0.34) |
| drive engine mean margin | -2.49 (+/- 0.93) | 13.08 | 1.37 (+/- 0.07) | 9.14 | +1.18 (+/- 0.37) |
| board fair margin (25/75) | -2.10 (+/- 0.70) | 12.39 | 1.42 (+/- 0.06) | 9.71 | +0.49 (+/- 0.10) |
| ESPN FPI margin | -0.99 (+/- 0.63) | 12.29 | 1.13 (+/- 0.04) | 11.7 | +0.38 (+/- 0.11) |
| closing spread (home favoured = +) | -0.52 (+/- 0.54) | 11.9 | 1.02 (+/- 0.04) | 13.45 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | +4.36 (+/- 0.69) | 13.77 | -0.48 (+/- 0.39) | 1.57 | 16.24 | +1.48 (+/- 0.28) | +0.00 (+/- 0.00) |
| engine total at the blended level | +4.23 (+/- 0.68) | 13.76 | -0.38 (+/- 0.31) | 1.81 | 16.24 | +1.47 (+/- 0.28) | -0.00 (+/- 0.04) |
| closing total | -0.11 (+/- 0.71) | 12.29 | 0.91 (+/- 0.08) | 6.23 | 16.24 | +0.00 (+/- 0.00) | -1.48 (+/- 0.28) |
| offence/defence rating total (K 4.0) | +2.07 (+/- 0.81) | 12.89 | 0.65 (+/- 0.08) | 7.28 | 16.24 | +0.60 (+/- 0.26) | -0.88 (+/- 0.20) |

*2025, cross* (n = 136 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -2.36 (+/- 0.96) | 13.54 | 0.85 (+/- 0.06) | 13.99 | +1.45 (+/- 0.72) |
| drive engine mean margin | -9.39 (+/- 0.73) | 15.81 | 1.18 (+/- 0.09) | 9.96 | +3.72 (+/- 0.56) |
| board fair margin (25/75) | -10.02 (+/- 0.62) | 15.04 | 1.36 (+/- 0.09) | 10.28 | +2.95 (+/- 0.41) |
| ESPN FPI margin | -3.87 (+/- 0.63) | 12.32 | 0.91 (+/- 0.05) | 15.38 | +0.24 (+/- 0.59) |
| closing spread (home favoured = +) | -2.68 (+/- 0.73) | 12.08 | 0.89 (+/- 0.05) | 15.88 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | -3.71 (+/- 1.32) | 12.91 | -0.01 (+/- 0.43) | 2.67 | 15.55 | +0.52 (+/- 0.49) | +0.00 (+/- 0.00) |
| engine total at the blended level | -3.34 (+/- 1.43) | 13.01 | -0.57 (+/- 0.49) | 2.8 | 15.55 | +0.62 (+/- 0.41) | +0.10 (+/- 0.14) |
| closing total | -1.65 (+/- 1.46) | 12.4 | 0.61 (+/- 0.24) | 5.15 | 15.55 | +0.00 (+/- 0.00) | -0.52 (+/- 0.49) |
| offence/defence rating total (K 4.0) | +0.42 (+/- 2.30) | 12.86 | 0.16 (+/- 0.15) | 6.25 | 15.55 | +0.46 (+/- 0.43) | -0.06 (+/- 0.78) |

*2026, all divisions* (n = 570 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -3.38 (+/- 1.34) | 13.89 | 1.06 (+/- 0.04) | 15.24 | +2.33 (+/- 0.59) |
| drive engine mean margin | -6.13 (+/- 1.84) | 15.01 | 1.42 (+/- 0.06) | 11.28 | +3.45 (+/- 0.92) |
| board fair margin (25/75) | -5.25 (+/- 1.42) | 13.37 | 1.51 (+/- 0.05) | 12.09 | +1.81 (+/- 0.51) |
| ESPN FPI margin | -2.40 (+/- 0.70) | 12.22 | 1.05 (+/- 0.04) | 17.19 | +0.66 (+/- 0.13) |
| closing spread (home favoured = +) | -1.34 (+/- 0.65) | 11.56 | 1.04 (+/- 0.03) | 17.8 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | +2.35 (+/- 0.89) | 13.89 | -0.87 (+/- 0.27) | 2.55 | 16.33 | +1.61 (+/- 0.26) | +0.00 (+/- 0.00) |
| engine total at the blended level | +2.08 (+/- 0.98) | 14.03 | -1.05 (+/- 0.28) | 2.72 | 16.33 | +1.74 (+/- 0.26) | +0.13 (+/- 0.07) |
| closing total | -0.88 (+/- 0.50) | 12.28 | 1.08 (+/- 0.11) | 5.31 | 16.33 | +0.00 (+/- 0.00) | -1.61 (+/- 0.26) |
| offence/defence rating total (K 4.0) | +1.71 (+/- 0.61) | 13.13 | 0.49 (+/- 0.14) | 6.26 | 16.33 | +0.85 (+/- 0.27) | -0.76 (+/- 0.35) |

*2026, fbs* (n = 435 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -2.52 (+/- 1.36) | 13.48 | 0.94 (+/- 0.06) | 10.97 | +2.15 (+/- 0.59) |
| drive engine mean margin | -3.78 (+/- 1.46) | 13.73 | 1.24 (+/- 0.08) | 8.39 | +2.39 (+/- 0.70) |
| board fair margin (25/75) | -2.92 (+/- 0.80) | 12.11 | 1.41 (+/- 0.07) | 9.56 | +0.78 (+/- 0.20) |
| ESPN FPI margin | -1.12 (+/- 0.47) | 11.79 | 0.93 (+/- 0.05) | 14.15 | +0.45 (+/- 0.17) |
| closing spread (home favoured = +) | -0.79 (+/- 0.44) | 11.34 | 0.99 (+/- 0.04) | 13.79 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | +4.26 (+/- 0.65) | 14.15 | -0.50 (+/- 0.58) | 1.4 | 16.66 | +1.54 (+/- 0.35) | +0.00 (+/- 0.00) |
| engine total at the blended level | +3.95 (+/- 0.65) | 14.24 | -0.89 (+/- 0.39) | 1.82 | 16.66 | +1.63 (+/- 0.34) | +0.09 (+/- 0.05) |
| closing total | -0.80 (+/- 0.60) | 12.61 | 1.07 (+/- 0.13) | 4.89 | 16.66 | +0.00 (+/- 0.00) | -1.54 (+/- 0.35) |
| offence/defence rating total (K 4.0) | +2.33 (+/- 0.75) | 13.39 | 0.48 (+/- 0.12) | 6.4 | 16.66 | +0.78 (+/- 0.19) | -0.76 (+/- 0.35) |

*2026, cross* (n = 132 priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.

| margin | bias | MAE | slope | pred SD | MAE - spread's |
|---|---|---|---|---|---|
| ratings margin (model_margin) | -5.91 (+/- 1.01) | 15.12 | 1.09 (+/- 0.09) | 12.19 | +2.73 (+/- 0.40) |
| drive engine mean margin | -13.62 (+/- 1.18) | 19.04 | 1.52 (+/- 0.15) | 8.42 | +6.65 (+/- 0.88) |
| board fair margin (25/75) | -12.78 (+/- 1.61) | 17.5 | 1.76 (+/- 0.10) | 8.89 | +5.11 (+/- 0.63) |
| ESPN FPI margin | -6.51 (+/- 1.13) | 13.77 | 1.05 (+/- 0.07) | 14.76 | +1.38 (+/- 0.14) |
| closing spread (home favoured = +) | -3.08 (+/- 1.80) | 12.39 | 1.08 (+/- 0.07) | 15.04 | +0.00 (+/- 0.00) |

| total | bias | MAE | slope | pred SD | actual SD | MAE - total line's | MAE - engine's |
|---|---|---|---|---|---|---|---|
| drive engine total (the card's exp_total) | -4.15 (+/- 1.64) | 13.1 | -1.34 (+/- 0.62) | 2.43 | 14.94 | +1.84 (+/- 0.40) | +0.00 (+/- 0.00) |
| engine total at the blended level | -4.23 (+/- 1.58) | 13.47 | -1.83 (+/- 0.63) | 2.56 | 14.94 | +2.21 (+/- 0.46) | +0.37 (+/- 0.15) |
| closing total | -1.39 (+/- 1.34) | 11.27 | 1.07 (+/- 0.16) | 5.89 | 14.94 | +0.00 (+/- 0.00) | -1.84 (+/- 0.40) |
| offence/defence rating total (K 4.0) | -0.67 (+/- 1.35) | 12.21 | 0.48 (+/- 0.37) | 5.76 | 14.94 | +0.94 (+/- 0.82) | -0.90 (+/- 0.72) |

Team points, all priced games pooled (n = 4648 team-games): the card's expected points against the market's implied split (total/2 -/+ spread/2).

| team points | bias | MAE | slope | pred SD | actual SD |
|---|---|---|---|---|---|
| card exp points (engine, model level) | +1.36 | 9.85 | 1.37 | 6.26 | 14.7 |
| market implied split | -0.42 | 8.58 | 1.02 | 9.72 | 14.7 |
| offence/defence rating total (K 4.0) | +0.86 | 9.35 | 1.02 | 8.76 | 14.7 |

### 4.1 The total candidate (pre-registered)

The offence/defence rating (rule in section 0): K chosen on 2024 by total MAE over every rated game, then tested on 2025 and 2026 against the engine's total and the closing total.

| K (prior games) | 2024 total MAE (all rated games) | 2024 margin MAE (OD margin, descriptive) |
|---|---|---|
| 1.0 | 13.740 | 13.134 |
| 1.5 | 13.584 | 13.040 |
| 2.0 | 13.500 | 13.007 |
| 3.0 | 13.420 | 13.013 |
| 4.0 | 13.398 | 13.058 |
| 6.0 | 13.425 | 13.158 |

Chosen K = 4.0.

| test season | division | n | bias | MAE | slope | MAE - engine's | MAE - total line's | 25/75 blend MAE - line's |
|---|---|---|---|---|---|---|---|---|
| 2025 | all | 885 | +1.79 | 12.88 | 0.60 (+/- 0.07) | -0.77 (+/- 0.20) | +0.59 (+/- 0.17) | +0.05 (+/- 0.04) |
| 2025 | fbs | 738 | +2.07 | 12.89 | 0.65 (+/- 0.08) | -0.88 (+/- 0.20) | +0.60 (+/- 0.26) | +0.05 (+/- 0.06) |
| 2025 | cross | 145 | +0.28 | 12.87 | 0.15 (+/- 0.13) | -0.26 (+/- 0.79) | +0.46 (+/- 0.43) | -0.02 (+/- 0.14) |
| 2026 | all | 570 | +1.71 | 13.13 | 0.49 (+/- 0.14) | -0.76 (+/- 0.35) | +0.85 (+/- 0.27) | +0.06 (+/- 0.07) |
| 2026 | fbs | 435 | +2.33 | 13.39 | 0.48 (+/- 0.12) | -0.76 (+/- 0.35) | +0.78 (+/- 0.19) | +0.07 (+/- 0.06) |
| 2026 | cross | 132 | -0.67 | 12.21 | 0.48 (+/- 0.37) | -0.90 (+/- 0.72) | +0.94 (+/- 0.82) | -0.00 (+/- 0.22) |

Unpriced games (no ESPN BET line stored; mostly FCS):

| season | n | OD total MAE | engine total MAE | difference |
|---|---|---|---|---|
| 2025 | 671 | 12.93 | 14.12 | -1.19 (+/- 0.31) |
| 2026 | 397 | 13.94 | 14.77 | -0.83 (+/- 0.45) |

### 4.2 The margin constants

The margin constants re-measured (grid on 2024 by margin MAE over every rated game, tested on 2025 and 2026 against the shipped K 1.5 / cap 50 / HFA 3.0):

(numpy solver self-check against the replay's margins, 2024: max |diff| = 5.00e-04)

| K | cap | HFA | 2024 margin MAE |
|---|---|---|---|
| 1.0 | 50.0 | 3.0 | 13.149 |
| 1.5 | 28.0 | 3.0 | 13.132 |
| 1.5 | 50.0 | 2.0 | 13.082 |
| 1.5 | 50.0 | 3.0 | 13.066 |
| 1.5 | 50.0 | 4.0 | 13.124 |
| 1.5 | 100.0 | 3.0 | 13.043 |
| 2.0 | 50.0 | 3.0 | 13.036 |
| 3.0 | 50.0 | 3.0 | 13.050 |

Best on 2024: K 2.0, cap 100.0, HFA 3.0 (MAE 13.007) against the shipped (1.5, 50.0, 3.0) (MAE 13.066).

| test season | n | shipped constants MAE | 2024-best constants MAE | difference (+/- SE) |
|---|---|---|---|---|
| 2025 | 1556 | 12.800 | 12.770 | -0.034 (+/- 0.021) |
| 2026 | 967 | 13.450 | 13.330 | -0.125 (+/- 0.070) |

### 4.3 Stacks and the anchor weight (pre-registered)

Pre-registered win-probability stacks (ridge logistic, lambda 1.0), walk-forward, priced games with FPI; 'vs market' = paired log loss against the de-vigged close, clustered SE.

- C1 [raw] weights: 2025: raw +1.06, b0 +0.03 | 2026: raw +1.12, b0 +0.05
- C2 [raw, fpi] weights: 2025: raw +0.12, fpi +0.87, b0 +0.02 | 2026: raw +0.18, fpi +0.86, b0 +0.02
- C3 [mkt] weights: 2025: mkt +1.06, b0 +0.07 | 2026: mkt +1.08, b0 +0.07
- C4 [mkt, raw] weights: 2025: mkt +0.89, raw +0.21, b0 +0.05 | 2026: mkt +0.87, raw +0.28, b0 +0.05
- C5 [mkt, raw, fpi] weights: 2025: mkt +0.82, raw +0.12, fpi +0.15, b0 +0.05 | 2026: mkt +0.76, raw +0.19, fpi +0.17, b0 +0.04

| stack | pooled test LL | vs market LL | vs market acc | per test season | 2-SE pass |
|---|---|---|---|---|---|
| C1 [raw] | 0.5356 | +0.0416 (+/- 0.0112) | -0.038 (+/- 0.009) | 2025: 0.5276 (+0.0188); 2026: 0.5485 (+0.0781) | no |
| C2 [raw, fpi] | 0.5078 | +0.0137 (+/- 0.0052) | -0.014 (+/- 0.008) | 2025: 0.5098 (+0.0009); 2026: 0.5045 (+0.0341) | no |
| C3 [mkt] | 0.4922 | -0.0019 (+/- 0.0011) | +0.005 (+/- 0.002) | 2025: 0.5073 (-0.0015); 2026: 0.4678 (-0.0026) | no |
| C4 [mkt, raw] | 0.4939 | -0.0002 (+/- 0.0017) | -0.002 (+/- 0.003) | 2025: 0.5050 (-0.0038); 2026: 0.4760 (+0.0057) | no |
| C5 [mkt, raw, fpi] | 0.4943 | +0.0002 (+/- 0.0019) | +0.002 (+/- 0.004) | 2025: 0.5048 (-0.0041); 2026: 0.4774 (+0.0070) | no |

The board's anchor weight (model share of the LEVEL), walk-forward on the win probability through the engine's own Phi(margin/18): w chosen on the fit seasons from {0, 0.1, 0.25, 0.5, 0.75, 1}.

| fit -> test | chosen w | market level (w 0) | shipped (w 0.25) | chosen | model only (w 1) |
|---|---|---|---|---|---|
| 2024 -> 2025 | 0.1 | w 0.0: LL 0.5150, acc 0.747, vs mkt +0.0051 (+/- 0.0032) | w 0.25: LL 0.5136, acc 0.754, vs mkt +0.0037 (+/- 0.0037) | w 0.1: LL 0.5141, acc 0.748, vs mkt +0.0042 (+/- 0.0033) | w 1.0: LL 0.5307, acc 0.734, vs mkt +0.0208 (+/- 0.0111) |
| 2024+2025 -> 2026 | 0.1 | w 0.0: LL 0.4767, acc 0.777, vs mkt +0.0063 (+/- 0.0048) | w 0.25: LL 0.4868, acc 0.765, vs mkt +0.0164 (+/- 0.0059) | w 0.1: LL 0.4801, acc 0.777, vs mkt +0.0098 (+/- 0.0051) | w 1.0: LL 0.5495, acc 0.703, vs mkt +0.0791 (+/- 0.0178) |
