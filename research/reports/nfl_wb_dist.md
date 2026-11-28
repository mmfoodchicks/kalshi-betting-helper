# Week-board player distributions: the registered replacement test

2026-11-27. The owner approved it: "the registered replacement test for week-board player distributions.
Investigate mean bias, variance, asymmetric tails, discrete statistics, workload uncertainty and joint player
correlations. Do not blindly multiply every player's variance by the same factor."

Files:
- candidates: `research/data/nfl_wb_dist/candidates.json`, committed alone (88a3fed) before any candidate was scored;
- harness: `research/nfl_wb_dist.py`;
- development output: `research/data/nfl_wb_dist/dev.json`;
- frozen fit: `research/data/nfl_wb_dist/frozen.json`;
- the rule: `research/data/nfl_wb_dist/prereg.json`;
- guards: `tests/guards_auto_wb_dist.py` (WB1-WB7).

**Production is unchanged.** The week board (`nfl_dfs_sim.simulate_game`) stays as it is, labelled overconfident
wherever its probabilities show, until the registered read passes and the owner signs off.

## 1. Bottom line

The week board misses asymmetrically: real DraftKings scores fall under its floor 37.5% of the time and over its
ceiling 19.3%. Widening it would not fix that. On the development games the causes are these:

- **Mean bias is small.**
  - Sleeper's means sit within a few tenths of the real ones for catches, targets, TDs and DK points.
  - Receiving yards are within about a yard (WR -1.10, TE -0.32, RB +0.39).
  - Rushing yards run under the projection: RB -2.09 and QB -2.54 a game, as the 2026 rushing study found.
- **Shape.** Real pass catchers have a zero-yard game often: RB receiving yards 24%, TE 23%, WR 18%. The board
  gives zero essentially no probability (0.000-0.008). It multiplies a mean by a continuous factor, so a
  catchless game cannot happen. Those zeros and near-zeros are most of the floor misses. The ceiling misses are
  the over-dispersion above them.
- **Workload.**
  - Real targets are over-dispersed against their projection: dispersion index 1.37-1.48, where Poisson is 1.
    RB carries are 1.98.
  - The board draws no workload at all: no target, catch or carry.
- **Spread differs by cell, in both directions.** Realized over model variance runs from 0.72 (QB passing TDs:
  too wide) to 8.52 (RB receiving yards: far too narrow). One shared factor would make some cells worse.
- **Most TDs are fine already.**
  - The board's Poisson rushing and receiving TDs are calibrated: variance ratio 0.94-1.12, P(0) matching.
  - QB passing TDs run too wide: 0.72, with P(0) 0.277 against the real 0.191.
- **Joint correlations are far too high among teammates.**
  - WR-WR 0.334 against an observed -0.049; WR-TE 0.309 against 0.011.
  - One shared multiplier moves the whole offence together.
  - QB-WR is 0.419 against 0.240.

**Development result (out of sample).** Fitted on 2025 weeks 1-12 and scored on 2026 weeks 1-5 (79 games). The
primary is the per-line log-loss gain over production, pooled over every scored line and clustered by game:

| candidate | gain per line | SE | z |
|---|---|---|---|
| A, the constrained simulator | 0.09925 | 0.0138 | 7.19 |
| B, the discrete allocation with its QB term | 0.11205 | 0.00372 | 30.11 |
| C, production through a per-cell quantile map | 0.10132 | 0.00367 | 27.61 |

The registered rule, applied to these development games, is for reading only and decides nothing:
- **A and B pass.**
- **C fails all three vetoes:**
  - QB interceptions are worse than production by 2.24 SE.
  - Its pooled 80% coverage is 0.62.
  - Its pair correlations are further from the observed than production's. C keeps production's joint structure
    by construction, and its mapped marginals widen the Pearson correlations.
- **The rule would recommend B.**

A dramatic development result is a hypothesis until it survives the real geometry. Hence the registered read on
games no one has seen.

## 2. Data, and what was already seen

- **Development games:**
  - 2025 weeks 1-12 (178 games);
  - 2026 weeks 1-5 (79 games): everything `player_weeks_2026.csv.gz` holds. Its weeks 6-12 were never built
    into it and are not read.
- **They were already read by the integer-stats study** (`research/reports/nfl_discrete_alloc.md`). These are
  seen games: development, not a test.
- **2026 week 13 on is never read in development.** The loader asserts it. Those weeks are also the coaching
  tests' seal.
- **Inputs are Sleeper's projections as the table holds them.**
  - This is not production's full `weekly_games` pipeline (`nfl_recon`, `nfl_usage`).
  - It is the same input for every model, so the test compares the distribution engines on one input.

## 3. The candidates (written first)

Production (P) is `nfl_dfs_sim.simulate_game` as it runs; its DK points are its pinned points array.

- **A, the constrained simulator** (`nfl_dfs_csim`).
  - Its committed Stage 2E parameters, fitted on 2022-24, so 2025 and 2026 are out of sample for it. Nothing
    is refitted here.
  - Counts are drawn: targets, catches and carries dealt from team totals. Yards are rounded.
- **B, the discrete allocation with a quarterback term.**
  - `nfl_discrete_alloc.simulate_game(pass_latent_scale=k)`: event counts, conservation exact.
  - The team passing multiplier's deviation from 1 is scaled by k. Its per-event draws already carry the
    noise the multiplier stood in for, so at k = 1 the QB's yards ran twice too wide (0.455).
  - k came off the 0.0-1.0 grid by the written criterion: the QB passing-yard ratio nearest 1. That gave
    k = 0.2 on 2025 (0.9925) and 0.2 again on all development games (1.0093).
  - The "other receivers" bucket and target competition are unchanged.
- **C, production through a per-position x statistic quantile map.**
  - Each marginal goes through G^-1 o F. G is the development empirical CDF of production's randomized PIT
    for that cell: 21 knots, shrunk toward the identity with a prior of 20.
  - It corrects bias, spread and asymmetric tails cell by cell. It keeps production's copula and production's
    support, so it can re-weight values the board draws but never create one it does not. A real zero sits
    below every production world, so it still lands at PIT 0. That is why C mends the log loss but not the
    coverage.

## 4. The registered rule (`prereg.json`)

- **Sample.** Every 2026 regular-season game of weeks 14-18.
  - Week 13 is excluded whole: its Thanksgiving and Friday games had kicked off before the registration, and the
    table carries no kickoff time.
  - The table is rebuilt by `research/hist_data.py` after the read date.
  - Fewer than 60 games: the read waits for 2027 weeks 1-6 and pools them.
- **Primary.** The per-line log-loss gain over production, pooled over every scored line and clustered by game:
  - the sum over games of (production's log loss - the candidate's), over the sum of lines;
  - its SE the ratio estimator's.
- **Holm, one-sided, family-wise 5%** over the three candidates. The largest z must clear 2.128, the next 1.960,
  the third 1.645, and testing stops at the first failure.
- **Vetoes:**
  - no position x statistic cell worse than production by more than 2 SE;
  - pooled 80% coverage inside 0.70-0.90 AND nearer 0.80 than production's;
  - the mean absolute gap between the model's pair correlations and the observed ones no larger than
    production's.
- **Decision.**
  - The passing candidate with the largest z is recommended. Nothing changes until the owner signs off.
  - No pass: production stays, labelled. A failed test can be insufficient evidence; it is never proof that
    production is right.
- **Power** (normal approximation off the development gain and SE, 72 prospective games, the strictest
  threshold):
  - about 1.0 for all three at the development gain;
  - at half that gain, 0.904 for A and 1.0 for B and C.
  - Power is the chance the test passes IF a candidate is that much better, never the probability that it is.
- **Read** on or after 2027-01-13 06:00 UTC (week 18 + 48 h), once, from the registration commit's tree if a
  frozen file has changed. The read refuses otherwise.
- **Other tests on these games.** The coaching registrations (R1, R2-H1, the coaching DFS read) read 2026
  weeks 13-18 too. They test different models and questions (game-flow engines, not per-player box
  distributions), so they are not this test's family and share no multiplicity correction. The dependence
  (the same games) is acknowledged.

## 5. By position and statistic (development, out of sample)

The full tables are in section 7. In short:

- **Coverage.**
  - Production's 80% intervals cover 27-44% of real receiving and rushing outcomes, and 36-79% of DK points.
  - A and B cover 72-85% in every cell. QB rushing yards are the exception for A: 0.81 coverage but a variance
    ratio of 0.66 (too wide) and a mean 2.47 yards under.
- **Floor and ceiling**, receiving and rushing cells:
  - production: 30-48% of outcomes under its 10th percentile, 20-26% over its 90th;
  - A and B: 5-16% under, 7-13% over.
- **Zero mass.** For pass catchers' receptions and yards, A and B put 15-25% on a zero, where the real games
  have 17-27%. Production puts 0-6% and C 0-14%.
- **Quarterback passing.**
  - Production is already about right on QB passing yards (0.96).
  - B's fitted term brings its own to 1.03, but B's log loss there is still slightly worse than production's
    (cell z -0.53), and its mean sits 3.35 yards under.
  - Both are inside the veto.
- **Joint.**
  - A and B sit within about 0.06 of the observed teammate correlations, QB-WR included (0.256 / 0.278 against
    0.240).
  - They put opposing quarterbacks too low: 0.034 / 0.025 against 0.136, where production's 0.181 is nearer.
  - Production's teammate pairs run 0.16-0.38 too high, and C's 0.24-0.53.
- **A caution on the observed correlation.** It is the correlation of residuals (real minus projection) across
  player-weeks. That is not the same quantity as a within-world correlation: projection errors attenuate it.
  The comparison is the one the integer-stats study used, and it is directional.

## 6. Meanwhile

- **The week board stays as it is**, labelled overconfident: `pick6_ledger.WEEK_BOARD_CAVEAT` and the Sim / Best
  ball caveat.
- **Nothing in the app imports the harness** (guard WB4).
- **B's hook at its default is the allocator draw for draw** (guard WB4b).
- **The read is one command after the data rebuild** (`prereg.json` runbook). Its result goes in
  `research/data/nfl_wb_dist/read.json` and a result section here.

## 7. Tables (development, out of sample: 2026 weeks 1-5, 79 games; 2,000 worlds a model)

Columns:
- mean bias: real minus the model's mean;
- var ratio: realized squared error over the model's variance (1 = right);
- 50/80/90%: central-interval coverage off the randomized PIT;
- under floor / over ceiling: PIT under 0.1 / over 0.9 (0.10 each if calibrated);
- P(0): the model's mass within half a unit of zero against the real share.

| cell | model | n | log loss | Brier | mean bias | var ratio | 50% | 80% | 90% | under floor | over ceiling | P(0) model / real |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| QB|dk | P | 157 | 0.5080 | 0.1664 | +1.02 | 1.04 | 0.47 | 0.79 | 0.87 | 0.108 | 0.102 | 0.000 / 0.000 |
| QB|dk | A | 157 | 0.5079 | 0.1666 | -0.01 | 1.04 | 0.48 | 0.78 | 0.87 | 0.108 | 0.115 | 0.000 / 0.000 |
| QB|dk | B | 157 | 0.5059 | 0.1659 | +0.08 | 1.08 | 0.48 | 0.76 | 0.87 | 0.115 | 0.121 | 0.000 / 0.000 |
| QB|dk | C | 157 | 0.5072 | 0.1661 | +0.85 | 1.02 | 0.47 | 0.82 | 0.87 | 0.083 | 0.102 | 0.000 / 0.000 |
| QB|int | P | 157 | 0.7112 | 0.2591 | -0.01 | 0.92 | 0.54 | 0.81 | 0.90 | 0.083 | 0.108 | 0.531 / 0.510 |
| QB|int | A | 157 | 0.7117 | 0.2594 | -0.01 | 0.90 | 0.54 | 0.81 | 0.90 | 0.083 | 0.108 | 0.534 / 0.510 |
| QB|int | B | 157 | 0.7124 | 0.2595 | -0.01 | 0.93 | 0.54 | 0.82 | 0.90 | 0.083 | 0.102 | 0.531 / 0.510 |
| QB|int | C | 157 | 0.7237 | 0.2649 | +0.03 | 0.99 | 0.51 | 0.80 | 0.88 | 0.083 | 0.121 | 0.558 / 0.510 |
| QB|pass_td | P | 157 | 0.5468 | 0.1826 | +0.09 | 0.72 | 0.51 | 0.85 | 0.92 | 0.083 | 0.064 | 0.277 / 0.191 |
| QB|pass_td | A | 157 | 0.5469 | 0.1820 | +0.02 | 0.78 | 0.53 | 0.82 | 0.92 | 0.102 | 0.076 | 0.237 / 0.191 |
| QB|pass_td | B | 157 | 0.5467 | 0.1820 | -0.01 | 0.78 | 0.53 | 0.82 | 0.91 | 0.102 | 0.083 | 0.229 / 0.191 |
| QB|pass_td | C | 157 | 0.5441 | 0.1808 | +0.13 | 0.96 | 0.50 | 0.81 | 0.89 | 0.096 | 0.096 | 0.247 / 0.191 |
| QB|pass_yd | P | 157 | 0.4592 | 0.1476 | +1.80 | 0.96 | 0.52 | 0.80 | 0.90 | 0.102 | 0.102 | 0.000 / 0.000 |
| QB|pass_yd | A | 157 | 0.4674 | 0.1492 | -0.09 | 0.94 | 0.52 | 0.80 | 0.91 | 0.108 | 0.096 | 0.000 / 0.000 |
| QB|pass_yd | B | 157 | 0.4654 | 0.1474 | -3.35 | 1.03 | 0.47 | 0.76 | 0.86 | 0.127 | 0.115 | 0.000 / 0.000 |
| QB|pass_yd | C | 157 | 0.4606 | 0.1479 | +2.88 | 1.08 | 0.49 | 0.76 | 0.87 | 0.115 | 0.127 | 0.000 / 0.000 |
| QB|rush_yd | P | 143 | 0.8145 | 0.2734 | -0.82 | 6.02 | 0.12 | 0.28 | 0.36 | 0.475 | 0.245 | 0.000 / 0.119 |
| QB|rush_yd | A | 143 | 0.6381 | 0.2235 | -2.47 | 0.66 | 0.50 | 0.81 | 0.93 | 0.119 | 0.070 | 0.123 / 0.119 |
| QB|rush_yd | B | 143 | 0.6388 | 0.2239 | -0.78 | 0.92 | 0.52 | 0.85 | 0.94 | 0.049 | 0.105 | 0.082 / 0.119 |
| QB|rush_yd | C | 143 | 0.6430 | 0.2254 | +1.31 | 2.43 | 0.28 | 0.48 | 0.57 | 0.336 | 0.189 | 0.000 / 0.119 |
| RB|dk | P | 375 | 0.6578 | 0.2173 | -0.21 | 1.90 | 0.25 | 0.48 | 0.57 | 0.368 | 0.155 | 0.001 / 0.115 |
| RB|dk | A | 375 | 0.5753 | 0.1952 | -0.32 | 0.89 | 0.44 | 0.78 | 0.88 | 0.125 | 0.093 | 0.075 / 0.115 |
| RB|dk | B | 375 | 0.5777 | 0.1963 | -0.42 | 1.06 | 0.39 | 0.77 | 0.89 | 0.131 | 0.101 | 0.074 / 0.115 |
| RB|dk | C | 375 | 0.5839 | 0.1983 | +0.04 | 1.08 | 0.44 | 0.64 | 0.72 | 0.261 | 0.099 | 0.008 / 0.115 |
| RB|rec | P | 338 | 0.7404 | 0.2264 | +0.03 | 5.00 | 0.22 | 0.38 | 0.46 | 0.358 | 0.260 | 0.063 / 0.269 |
| RB|rec | A | 338 | 0.6039 | 0.1998 | +0.01 | 1.13 | 0.52 | 0.78 | 0.87 | 0.106 | 0.112 | 0.253 / 0.269 |
| RB|rec | B | 338 | 0.5810 | 0.1975 | +0.03 | 1.43 | 0.51 | 0.75 | 0.85 | 0.121 | 0.130 | 0.231 / 0.269 |
| RB|rec | C | 338 | 0.6155 | 0.2046 | +0.05 | 1.92 | 0.43 | 0.52 | 0.57 | 0.290 | 0.186 | 0.139 / 0.269 |
| RB|rec_yd | P | 297 | 0.7933 | 0.2679 | -0.01 | 8.52 | 0.15 | 0.30 | 0.34 | 0.465 | 0.239 | 0.000 / 0.236 |
| RB|rec_yd | A | 297 | 0.6616 | 0.2213 | -0.12 | 1.35 | 0.46 | 0.72 | 0.82 | 0.158 | 0.121 | 0.212 / 0.236 |
| RB|rec_yd | B | 297 | 0.6255 | 0.2174 | -0.01 | 1.48 | 0.46 | 0.72 | 0.81 | 0.158 | 0.125 | 0.196 / 0.236 |
| RB|rec_yd | C | 297 | 0.6302 | 0.2199 | +1.28 | 3.14 | 0.35 | 0.44 | 0.48 | 0.367 | 0.195 | 0.001 / 0.236 |
| RB|rush_td | P | 345 | 0.4914 | 0.1616 | -0.01 | 0.99 | 0.54 | 0.81 | 0.90 | 0.087 | 0.104 | 0.739 / 0.754 |
| RB|rush_td | A | 345 | 0.4908 | 0.1610 | -0.01 | 0.82 | 0.53 | 0.82 | 0.91 | 0.087 | 0.096 | 0.752 / 0.754 |
| RB|rush_td | B | 345 | 0.4902 | 0.1611 | -0.01 | 0.95 | 0.54 | 0.81 | 0.90 | 0.087 | 0.107 | 0.742 / 0.754 |
| RB|rush_td | C | 345 | 0.4915 | 0.1618 | +0.01 | 1.05 | 0.52 | 0.80 | 0.90 | 0.087 | 0.116 | 0.755 / 0.754 |
| RB|rush_yd | P | 359 | 0.7372 | 0.2450 | -2.54 | 2.71 | 0.22 | 0.39 | 0.49 | 0.409 | 0.201 | 0.000 / 0.098 |
| RB|rush_yd | A | 359 | 0.6007 | 0.2063 | -1.26 | 0.78 | 0.49 | 0.81 | 0.88 | 0.114 | 0.075 | 0.063 / 0.098 |
| RB|rush_yd | B | 359 | 0.6017 | 0.2070 | -2.54 | 0.82 | 0.48 | 0.85 | 0.93 | 0.078 | 0.075 | 0.043 / 0.098 |
| RB|rush_yd | C | 359 | 0.6073 | 0.2094 | +1.02 | 1.16 | 0.40 | 0.58 | 0.64 | 0.284 | 0.131 | 0.000 / 0.098 |
| TE|dk | P | 372 | 0.7251 | 0.2451 | +0.10 | 2.61 | 0.16 | 0.36 | 0.47 | 0.409 | 0.234 | 0.008 / 0.274 |
| TE|dk | A | 372 | 0.6186 | 0.2146 | +0.04 | 1.13 | 0.44 | 0.73 | 0.84 | 0.142 | 0.132 | 0.249 / 0.274 |
| TE|dk | B | 372 | 0.6219 | 0.2158 | +0.08 | 1.32 | 0.42 | 0.72 | 0.83 | 0.150 | 0.134 | 0.232 / 0.274 |
| TE|dk | C | 372 | 0.6298 | 0.2183 | -0.06 | 1.23 | 0.38 | 0.53 | 0.61 | 0.331 | 0.140 | 0.041 / 0.274 |
| TE|rec | P | 358 | 0.7902 | 0.2419 | -0.02 | 3.76 | 0.19 | 0.36 | 0.43 | 0.380 | 0.257 | 0.064 / 0.265 |
| TE|rec | A | 358 | 0.6154 | 0.2109 | -0.03 | 1.11 | 0.45 | 0.79 | 0.89 | 0.112 | 0.095 | 0.235 / 0.265 |
| TE|rec | B | 358 | 0.6109 | 0.2107 | -0.02 | 1.43 | 0.41 | 0.75 | 0.87 | 0.129 | 0.117 | 0.216 / 0.265 |
| TE|rec | C | 358 | 0.6517 | 0.2192 | -0.01 | 1.51 | 0.41 | 0.52 | 0.56 | 0.304 | 0.179 | 0.134 / 0.265 |
| TE|rec_td | P | 315 | 0.4546 | 0.1433 | +0.03 | 1.12 | 0.50 | 0.81 | 0.89 | 0.083 | 0.111 | 0.834 / 0.813 |
| TE|rec_td | A | 315 | 0.4563 | 0.1437 | +0.03 | 1.08 | 0.50 | 0.80 | 0.89 | 0.083 | 0.114 | 0.839 / 0.813 |
| TE|rec_td | B | 315 | 0.4552 | 0.1436 | +0.03 | 1.15 | 0.50 | 0.80 | 0.88 | 0.083 | 0.114 | 0.833 / 0.813 |
| TE|rec_td | C | 315 | 0.4528 | 0.1430 | +0.01 | 1.04 | 0.50 | 0.81 | 0.89 | 0.083 | 0.111 | 0.825 / 0.813 |
| TE|rec_yd | P | 354 | 0.8144 | 0.2751 | -1.18 | 5.10 | 0.14 | 0.27 | 0.34 | 0.483 | 0.249 | 0.000 / 0.266 |
| TE|rec_yd | A | 354 | 0.6355 | 0.2226 | -1.32 | 1.04 | 0.45 | 0.76 | 0.88 | 0.147 | 0.096 | 0.231 / 0.266 |
| TE|rec_yd | B | 354 | 0.6378 | 0.2231 | -1.17 | 1.19 | 0.44 | 0.75 | 0.87 | 0.152 | 0.102 | 0.216 / 0.266 |
| TE|rec_yd | C | 354 | 0.6409 | 0.2249 | +0.21 | 1.89 | 0.34 | 0.42 | 0.49 | 0.396 | 0.181 | 0.001 / 0.266 |
| WR|dk | P | 618 | 0.6828 | 0.2310 | +0.32 | 2.42 | 0.24 | 0.45 | 0.57 | 0.333 | 0.212 | 0.004 / 0.176 |
| WR|dk | A | 618 | 0.6365 | 0.2134 | +0.13 | 1.03 | 0.49 | 0.77 | 0.88 | 0.104 | 0.126 | 0.187 / 0.176 |
| WR|dk | B | 618 | 0.6121 | 0.2111 | +0.14 | 1.19 | 0.46 | 0.75 | 0.87 | 0.118 | 0.131 | 0.167 / 0.176 |
| WR|dk | C | 618 | 0.6204 | 0.2143 | +0.32 | 1.22 | 0.45 | 0.62 | 0.68 | 0.256 | 0.123 | 0.020 / 0.176 |
| WR|rec | P | 594 | 0.7050 | 0.2235 | +0.11 | 3.20 | 0.23 | 0.44 | 0.54 | 0.303 | 0.261 | 0.039 / 0.168 |
| WR|rec | A | 594 | 0.6248 | 0.2072 | +0.11 | 1.07 | 0.49 | 0.77 | 0.89 | 0.111 | 0.116 | 0.176 / 0.168 |
| WR|rec | B | 594 | 0.5968 | 0.2043 | +0.11 | 1.38 | 0.45 | 0.74 | 0.86 | 0.133 | 0.131 | 0.155 / 0.168 |
| WR|rec | C | 594 | 0.6162 | 0.2081 | +0.13 | 1.38 | 0.46 | 0.61 | 0.66 | 0.224 | 0.162 | 0.082 / 0.168 |
| WR|rec_td | P | 578 | 0.4687 | 0.1476 | -0.01 | 0.94 | 0.50 | 0.79 | 0.91 | 0.105 | 0.109 | 0.798 / 0.808 |
| WR|rec_td | A | 578 | 0.4738 | 0.1462 | -0.01 | 0.90 | 0.50 | 0.79 | 0.90 | 0.104 | 0.109 | 0.805 / 0.808 |
| WR|rec_td | B | 578 | 0.4685 | 0.1476 | -0.01 | 0.97 | 0.50 | 0.79 | 0.90 | 0.104 | 0.107 | 0.796 / 0.808 |
| WR|rec_td | C | 578 | 0.4694 | 0.1474 | +0.01 | 1.01 | 0.49 | 0.78 | 0.89 | 0.102 | 0.123 | 0.813 / 0.808 |
| WR|rec_yd | P | 610 | 0.7604 | 0.2555 | +0.79 | 4.26 | 0.20 | 0.36 | 0.44 | 0.389 | 0.254 | 0.000 / 0.182 |
| WR|rec_yd | A | 610 | 0.6504 | 0.2184 | +0.83 | 0.98 | 0.50 | 0.77 | 0.88 | 0.113 | 0.121 | 0.189 / 0.182 |
| WR|rec_yd | B | 610 | 0.6244 | 0.2170 | +0.81 | 1.12 | 0.50 | 0.74 | 0.88 | 0.126 | 0.131 | 0.170 / 0.182 |
| WR|rec_yd | C | 610 | 0.6377 | 0.2231 | +3.67 | 1.66 | 0.40 | 0.52 | 0.57 | 0.295 | 0.185 | 0.001 / 0.182 |

| pair type | production | A | B | C | observed (residuals) | pairs |
|---|---|---|---|---|---|---|
| QB-QB opp | 0.181 | 0.034 | 0.025 | 0.183 | 0.136 | 78 |
| QB-RB | 0.25 | 0.069 | 0.104 | 0.3 | 0.061 | 462 |
| QB-TE | 0.371 | 0.194 | 0.209 | 0.479 | 0.21 | 475 |
| QB-WR | 0.419 | 0.256 | 0.278 | 0.492 | 0.24 | 738 |
| RB-WR | 0.188 | -0.001 | 0.005 | 0.268 | -0.016 | 2187 |
| WR-TE | 0.309 | 0.008 | 0.006 | 0.487 | 0.011 | 2236 |
| WR-WR | 0.334 | 0.007 | 0.005 | 0.482 | -0.049 | 1411 |

Calibration curves (reliability: mean forecast -> hit rate, per 10% bin; bins with 30+ lines), receiving yards:

- WR|rec_yd P: 0.07->0.24, 0.18->0.33, 0.21->0.30, 0.37->0.27, 0.45->0.41, 0.66->0.40, 0.74->0.54, 0.87->0.36, 0.94->0.66
- WR|rec_yd B: 0.18->0.21, 0.25->0.27, 0.34->0.34, 0.44->0.44, 0.56->0.55, 0.64->0.67, 0.75->0.72, 0.82->0.83
- RB|rush_yd P: 0.07->0.18, 0.18->0.29, 0.21->0.25, 0.37->0.33, 0.44->0.34, 0.67->0.41, 0.75->0.52, 0.95->0.64
- RB|rush_yd B: 0.18->0.15, 0.26->0.24, 0.33->0.27, 0.44->0.37, 0.56->0.45, 0.63->0.56, 0.76->0.74, 0.81->0.86
- TE|rec P: 0.05->0.25, 0.14->0.20, 0.24->0.35, 0.34->0.29, 0.45->0.37, 0.55->0.50, 0.65->0.59, 0.75->0.55, 0.85->0.60, 0.96->0.72
- TE|rec B: 0.16->0.22, 0.25->0.26, 0.35->0.34, 0.45->0.40, 0.55->0.54, 0.65->0.54, 0.75->0.72, 0.85->0.77
- QB|pass_yd P: 0.08->0.08, 0.22->0.23, 0.46->0.50, 0.75->0.78, 0.95->0.94
- QB|pass_yd B: 0.06->0.09, 0.15->0.13, 0.24->0.29, 0.36->0.24, 0.46->0.49, 0.54->0.55, 0.76->0.84, 0.83->0.74, 0.96->0.93

| position | stat | n | projected | real | real - projected | dispersion (Poisson 1) | residual skew | real zeros |
|---|---|---|---|---|---|---|---|---|
| QB | dk | 513 | 17.4 | 17.72 | +0.326 |  | 0.36 | 0.0 |
| QB | int | 513 | 0.72 | 0.66 | -0.055 |  | 0.995 | 0.5185 |
| QB | pass_td | 513 | 1.51 | 1.5 | -0.005 |  | 0.383 | 0.1988 |
| QB | pass_yd | 513 | 226.22 | 224.94 | -1.284 |  | 0.205 | 0.0 |
| QB | rush_yd | 496 | 20.42 | 17.89 | -2.535 |  | 0.786 | 0.1008 |
| QB | rush_att | 512 | 4.03 | 3.84 | -0.185 | 1.367 | 0.359 | 0.0879 |
| RB | rec_tgt | 930 | 2.6 | 2.73 | +0.127 | 1.476 | 1.153 | 0.1398 |
| RB | dk | 1233 | 9.3 | 9.13 | -0.167 |  | 1.28 | 0.1176 |
| RB | rec | 1074 | 1.9 | 1.95 | +0.048 |  | 1.121 | 0.2542 |
| RB | rec_yd | 987 | 14.49 | 14.88 | +0.388 |  | 1.661 | 0.2421 |
| RB | rush_td | 1151 | 0.34 | 0.3 | -0.030 |  | 1.594 | 0.7646 |
| RB | rush_yd | 1192 | 40.72 | 38.64 | -2.086 |  | 1.225 | 0.0998 |
| RB | rush_att | 1214 | 9.07 | 8.89 | -0.187 | 1.981 | 0.511 | 0.0956 |
| WR | rec_tgt | 1886 | 4.7 | 4.69 | -0.013 | 1.435 | 0.563 | 0.0917 |
| WR | dk | 2040 | 7.91 | 7.96 | +0.049 |  | 1.105 | 0.1819 |
| WR | rec | 1950 | 2.83 | 2.87 | +0.033 |  | 0.816 | 0.1764 |
| WR | rec_td | 1909 | 0.25 | 0.23 | -0.021 |  | 1.909 | 0.7999 |
| WR | rec_yd | 2008 | 36.92 | 35.82 | -1.102 |  | 0.865 | 0.1897 |
| TE | rec_tgt | 933 | 3.61 | 3.63 | +0.025 | 1.369 | 0.738 | 0.1243 |
| TE | dk | 1119 | 5.69 | 5.87 | +0.172 |  | 1.423 | 0.2431 |
| TE | rec | 1064 | 2.38 | 2.39 | +0.012 |  | 0.905 | 0.2284 |
| TE | rec_td | 968 | 0.19 | 0.21 | +0.015 |  | 2.087 | 0.8223 |
| TE | rec_yd | 1059 | 24.68 | 24.36 | -0.319 |  | 1.203 | 0.2332 |

