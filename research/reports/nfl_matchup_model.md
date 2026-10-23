# The NFL matchup model: how much does the opponent's defense move a player's line?

Status: **VALIDATED (2025) AND TESTED ONCE (2026 weeks 1-6).** The rule
below was written before any 2025 or 2026 outcome was scored and hashed at
**2026-10-23 09:39:09 UTC, sha256
`4b7e792cb4256251ddeda0d0004e6b5527bd500b303eafa97498103bb8c9f051`**
(`research/data/nfl_matchup_model/prereg.json`, with the three code files'
hashes at that moment). The validation ran at 09:40 UTC and the test at
09:40:50 UTC (`test_lock.json`, not forced), both on code identical to the
registered hashes (recorded in `validate.json` / `test.json`), on the data
foundation the reviews agreed (dataset sha256 `766a60ed...`).

**Reviewed 2026-10-23: AGREE AFTER FIXES** (`research/reviews/nfl_matchup_model.md`,
the Fable review; `research/reviews/nfl_matchup_model_orchestrator.md`). The
fixes and what they disclose are in section 2a. None changes a decision or a
tested number: the yards family the rule was meant to choose was never chosen
(gamma won by loop order, and stays: it is the better one out of sample), the
board no longer prints a zero-width band where nothing was fitted, and the
board now says on the slate that low rushing-yard thresholds run high. A
line-dependent spread was pre-registered (section 9) and failed its 2025 gate
(-0.0086 +/- 0.0137), so nothing ships and the 2026 weeks 8-10 judgement
does not run.

**The answer, in one paragraph.** On top of Sleeper's weekly line, nothing
about the opponent's defense measurably moved a player's targets,
receptions, receiving yards, carries, rushing yards, pass attempts, passing
yards or interceptions out of sample. That covers the overall defense, the
position-specific defense, the location / depth proxy, scheme vs scheme
(coverage, shells, pressure, blitz, box) and his history against similar
defenses. Every receiving and rushing rung was within two clustered SE of
Sleeper on 2025, and the full-interaction model was worse or flat. Two QB
groups passed 2025: completions with the QB-passing-yards-allowed defense
(D3, -0.0110 +/- 0.0055 nats, z -2.00) and passing TDs with game script (V:
the spread, the total, the team's PROE; -0.0103 +/- 0.0036, z -2.84). On
2026's frozen pre-kickoff lines the completions term was WORSE (+0.0113 +/-
0.0125, 91 QB-games) and ships at weight 0. The passing-TD term was better
(-0.0067 +/- 0.0082, 114 QB-games) and ships by the rule's point-estimate
walk. It is weak evidence: under one SE on the frozen rows, +0.0013 on
the revised ones, and leaning on week 3, whose captures were 7-11 days old
while the term reads the closing line (without week 3: -0.0035 +/- 0.0099,
82 QB-games; section 2a). It is game script, not a defense matchup, and no Kalshi
market prices passing TDs. The DFS hook does not ship. Even if it did, with
only passing TDs carrying weight and team totals preserved, it would leave
every line unchanged.

Files: `nfl_matchup.py` (production: features, the shipped model, distributions,
`apply()` for the DFS board, `board()` for the matchup tab; pure Python),
`research/nfl_matchup_frame.py` (the modelling frame: features computed BY the
production code), `research/nfl_matchup_fit.py` (the ladder, validation, test,
export), `research/nfl_matchup_kalshi.py` (pre-kickoff Kalshi prices),
`research/data/nfl_matchup_model/` (coefficients and outputs),
`tests/guards_nfl_matchup_model.py`.

<!-- PREREG-START -->
## Pre-registered rule (written 2026-10-23, before any 2025 or 2026 outcome was scored)

### R1. Data, rows and baselines
- Frames: `NFLM_K=fit_only python3 -m research.nfl_matchup_frame --seasons 2023
  2024 2025` (the fitting / validation frame) and `python3 -m
  research.nfl_matchup_frame` (the test / production frame, 2023-2026) -- one
  row per player-game of
  `research/data/nfl_matchup/dataset_2023_2026.csv.gz`, 2023 weeks 1-18 through
  2026 weeks 1-6, plus every 2026 player with a FROZEN Sleeper line whom the
  revised feed dropped). Every feature is computed by `nfl_matchup.features`
  as of the row's week from the nflverse stores (current season's weeks before
  the cutoff + two prior seasons at 0.5 / 0.25). Participation-derived signals
  (coverage, man / zone, shells, pressure, the on-field target rates) read the
  PRIOR SEASONS ONLY for every row of every season, because production never
  has the current season's participation.
- Outcome: Sleeper's official box (what Kalshi settles on; kneels count as
  carries); nflverse pbp only for a player who played without a box row; zero
  for a projected player who did not play.
- Baselines: `sl_*` = Sleeper's post-week REVISED line (every season); `fz_*`
  (2026 only) = the latest frozen capture whose content time (Sleeper
  `updated_at`, or the props log's pass time) is strictly before the game's
  kickoff (`research/data/feeds/proj_2026_{1,2}.json`,
  `research/data/prospective/*/*/proj_2026_<w>.json`, the sim-history props log
  of week 6).
- Splits: fit 2023-2024, validate 2025, test 2026 weeks 1-6 ONCE (week 7 had
  not been played). The training-internal check (`traincheck`: fit 2023,
  score 2024) is the only run before this rule.
- Fit universe per market: positions tgt / rec / rec_yd WR TE RB; car /
  rush_yd RB QB WR; passing QB with Sleeper pass attempts >= 10; Sleeper's
  line for the market > 0.
- Scored ("priced") universe: receiving markets Sleeper targets >= 2; rushing
  RB with carries >= 5 or QB with carries >= 2; passing QB attempts >= 20;
  anytime TD Sleeper rec TD + rush TD >= 0.15; DK points Sleeper's DK mean >= 4.

### R2. The model
- Per market m in {targets, receptions, receiving yards, carries, rushing
  yards, pass attempts, completions, passing yards, passing TDs,
  interceptions}: y ~ quasi-Poisson with mean Sleeper_m x exp(X b), X the
  market's features for the groups accepted so far, each column divided by
  its training SD, ridge penalty lambda on every non-intercept coefficient,
  lambda chosen from {1, 10, 100, 300, 1e3, 3e3, 1e4, 3e4, 1e5, 1e6} by
  leave-one-season-out Poisson deviance on the fitting seasons; log factor
  clamped to [-1, 1]. Receiving / rushing TDs move with the targets /
  carries factor only; anytime TD = Poisson(rec TD + rush TD).
- Distributions, fit ONCE per (market, position) on the Sleeper baseline's
  training residuals and held fixed across rungs: NegBin (method-of-moments
  alpha, floored at 0) for counts; yards = point mass at P(0 receptions /
  carries) under the count market's NegBin at Sleeper's count line scaled by
  the yards factor, plus a gamma or lognormal positive part with a
  per-position CV (moments; family chosen by training log loss); passing
  yards NormalCC with sd = c x mean^d (decile fit); DK points = mean of the
  scoring with every bonus at its probability, variance = the independent
  components' variance x a per-position factor fitted on training, shifted
  gamma (shift 3).
- Feature groups (exact lists: `nfl_matchup.MARKET_FEATURES`; each feature
  is a deviation from the league at the same cutoff shrunk by n / (n + k)
  with the measured split-half k of its signal, `nfl_matchup.K_SIGNAL`).
  **The k:** the fitting / validation frame (2023-2025 rows) is built with
  the k measured on 2018-2024 ONLY (`python3 -m research.nfl_matchup_kfit`,
  the data foundation's own `measure_reliability` restricted to those
  seasons, `research/data/nfl_matchup_model/k_fit_only.json`;
  `NFLM_K=fit_only`), so no k the 2025 validation reads has seen 2025. The
  test / production frame (refit 2023-2025, scored on 2026) is built with
  the 2018-2025 table (`research/data/nfl_matchup/reliability.json`). A
  signal measured at r <= 0.02 has no k and its feature is 0. The groups:
  - **R** level recalibration: an intercept per position.
  - **V** team volume and game script: closing spread (team side, /7), total
    ((t - 44) / 10), own PROE.
  - **D2** overall defense: EPA / dropback allowed, YPC allowed, rush success
    allowed, pass rate faced, sack rate, catch rate and YPT allowed (by market).
  - **D3** position-specific defense: targets per dropback to the position,
    opponent-adjusted targets and receiving yards per game, catch rate and YPT
    to the position, RB rushing yards and QB passing yards allowed
    (opponent-adjusted).
  - **D4** location / depth proxy vs positional weakness: the player's middle-
    third share (alignment PROXY) and deep share x the defense's middle /
    deep target share allowed; depth-mix x catch / YPT allowed by depth;
    middle share x YPT allowed middle vs outside.
  - **D5** scheme vs scheme: the player's target rate per on-field dropback vs
    man vs zone (and single- vs two-high) x the defense's man share (shell
    mix), log of the expected rate under the defense's mix over his faced mix,
    his split shrunk by n / (n + k), n the smaller side's dropbacks and k the
    man-zone split's measured k (the shell split borrows it: unmeasured);
    defense blitz rate on RB and TE rows;
    defense pressure x the QB's EPA drop under pressure (receptions, yards,
    passing); defense blitz x the QB's EPA change vs blitz; light / heavy box
    share; the player's YPC by box x the defense's box mix.
  - **D6** comparable-opponent history: his own games (current season before
    the cutoff + two seasons, any team) weighted by recency (0.5 a season) x
    exp(-d^2 / 8), d the Euclidean distance between this opponent's and that
    game's opponent's standardised 8-vector AS OF THAT GAME (man, single-high
    share, blitz, pressure, light box, EPA / dropback allowed, YPC allowed,
    pass rate faced); feature = n_eff / (n_eff + 10) x log((similar-weighted
    mean + c) / (recency-weighted mean + c)).
  - **D7** full interaction: every V-D6 feature of the market plus each x
    position dummies (receiving TE, RB; rushing QB, WR), cov_mz x target share
    (receiving), every feature x the spread (passing).
- QB the passing features describe: 2023-2025 the last starter as of the
  cutoff (most attempts in the team's last game before the week); 2026 the
  frozen capture's QB1 by projected attempts, else the last starter.

### R3. Metric and the ladder's decision (2025)
- Primary per row: the threshold log loss, summed over the market's
  thresholds (tgt 3/5/7/9; rec 2-7; rec_yd and rush_yd 15/25/40/50/60/75/100;
  car 5/10/15/20; pass att 25/30/35/40; completions 15/20/25; pass_yd
  150-300 by 25; pass TD 1/2/3; INT 1/2; anytime TD 1/2; DK points
  5/10/15/20/25/30). Secondary: MAE, RMSE, Poisson deviance, Brier.
  Standard errors of a paired difference are clustered by game.
- The ladder per market, in the order R, V, D2, D3, D4, D5, D6, D7: the
  candidate is everything accepted so far plus the group, fit on 2023-2024,
  scored on 2025. **It is accepted iff its 2025 log loss is lower than the
  accepted model's by more than two clustered SE AND by at least 0.0005 nats
  per threshold of the market.** A group a market has no feature for is
  skipped. D7, if accepted, replaces the accepted set.
- O1 sensitivity (reported, not decisive): the D5 rung of the passing and
  receiving markets refit with the passing features built on Sleeper's
  revised QB1.
- Also reported on 2025, deciding nothing: every rung's metrics by position;
  DK points per rung across markets (each market's accepted-before set plus
  the group; S x M vs S and SU x M vs SU); each rung's gain split at the
  median reliability of its lead feature; the accepted models' gain by
  position and by history depth (weighted games < 4, 4-10, 10+).

### R4. The 2026 test (once) and what ships
- Every market's accepted groups are refit on 2023-2025 (lambda by the same
  CV, three folds) with dispersion and the DK factor refit on 2023-2025.
- Scored on 2026 weeks 1-6 twice: on FROZEN-baseline rows (decides; the
  only 2026 baseline any decision reads) and on REVISED-baseline rows
  (reported, for the revision effect only). The shipped coefficients are
  these refits (the test frame, 2018-2025 k), which production reads. A market a frozen capture does not carry
  is not scored on frozen rows (the props log keeps no completions or
  interceptions, so those rows drop out of those two markets, and its
  quarterbacks out of DK points; a missing lost-fumble line reads 0 in both
  arms of every DK comparison).
- **The shipping walk (frozen rows):** the accepted groups are walked in
  ladder order; a group keeps its weight iff the frozen-row log loss of
  (shipped so far + the group) is lower than that of (shipped so far) --
  point estimate, no SE requirement (the frozen sample is small); otherwise
  it ships at weight 0 and the walk continues without it. A group not
  accepted on 2025 never ships.
- **The DFS hook** (`nfl_matchup.apply`) applies the shipped market factors to
  Sleeper + nfl_usage's lines and renormalises every component to the team's
  total. It ships iff (Sleeper + usage) x matchup with team totals preserved
  has DK-points log loss <= (Sleeper + usage) on 2025 AND on the frozen 2026
  rows. Team totals are freed only if the unconstrained version beats the
  preserved one on 2025 by more than two SE AND on the frozen rows (point
  estimate).
- Reported on the same rows: each validation rung's cumulative model on
  frozen and revised rows; anytime TD; DK points for Sleeper, Sleeper + usage,
  x matchup (free / preserved); reliability tables (10 bins) at rec 4+,
  rec_yd 50+, rush_yd 50+, pass_yd 250+, targets 5+; the revision effect
  (Sleeper revised vs frozen on the rows that have both).

### R5. Kalshi (descriptive; no decision reads it)
- A seeded sample (seed 20261023, 900 markets a series, all of a sampled
  player-game's rungs) of settled KXNFLRECYDS / KXNFLREC / KXNFLRSHYDS /
  KXNFLPASSYDS / KXNFLTD markets with volume, regular season 2025 (historical
  tier) and 2026 weeks 1-6 (live tier). Price = the last hourly candle that
  ended at or before kickoff, YES mid, books with a bid and an ask no more
  than 20c apart. Matched to frame rows by normalised name and kickoff within
  six hours. Model probability: 2025 the validation model (fit 2023-2024,
  revised baseline -- the only one 2025 has); 2026 the shipped model on the
  frozen baseline only. Reported: log loss and Brier of model, Sleeper
  baseline and Kalshi mid on the same events; one contract of the side with
  positive fee-adjusted EV at the pre-kickoff ask (prop_value.ev_buy,
  contracts=1), ROI with a game-clustered bootstrap 95% interval (2,000
  draws). Also the app's own props log for 2026 week 6 (de-vigged mids frozen
  five minutes before kickoff).

### R6. Disclosed before the runs
- The k: see R2 (2018-2024 for the validation frame). DECAY (0.5, from
  `persistence.json`) was read off year-over-year pairs through 2024 -> 2025;
  the pooled tendency r is 0.485 with that pair and 0.480 without it, both in
  the data rule's 0.3-0.6 band, so DECAY is 0.5 either way. Sensitivity (reported, deciding nothing): the
  validation ladder rerun on the 2018-2025-k frame's 2023-2025 rows.
- Amended after the data foundation's rebuild (2026-10-23 ~04:45 UTC) and
  before this block was first hashed: the k source above (the orchestrator's
  condition that the 2025 validation read no k that saw 2025; it was the
  2018-2025 table with a disclosure), the DK-points rows a frozen capture
  cannot score, the list of descriptive extras, the training-only
  univariate disclosure. No feature, group, threshold or decision rule
  changed after the rebuild.
- The training-internal check (fit 2023, score 2024) was run before this rule
  and changed two things: the lambda grid was widened to 1e6 (the CV kept
  choosing its top) and the 0.0005-nats-per-threshold floor was added (a
  0.00005-nat "significant" gain at lambda 1e6 is not a finding).
- A univariate look at single features against Sleeper's residual on
  2023-2024 only (training) was taken before this rule: the position
  allowances (targets per game to the position z 4.7, targets per dropback
  3.1, RB rushing yards 3.1, QB passing yards 2.9) carried something, the
  man / zone target-rate interaction nothing (z -0.2). No feature or group
  was added, dropped or reordered after it.
- "Plumbing" runs of validate and test were made with every 2025 / 2026
  outcome replaced by a random 2023 outcome of the same position (no
  held-out information) to exercise the code paths.
- The 2026 frame was built with 2026 outcome columns present; nothing was
  scored on them. Smoke checks read only feature distributions and source
  counts.
<!-- PREREG-END -->

## 1. Results

Every number below is in `research/data/nfl_matchup_model/validate.json`,
`test.json` or `kalshi.json`, written by the commands in section 7. The full
tables (every market, every rung, both baselines) are in section 8, rendered
by `python3 -m research.nfl_matchup_fit tables`. "d" is a difference in the
threshold log loss per row (nats, summed over the market's thresholds), and
SEs are clustered by game.

### 1.1 Validation, 2025 (fit 2023-2024; 2018-2024 k)

| market | rows | Sleeper log loss | best rung (d +/- SE) | accepted |
|---|---|---|---|---|
| targets | 2,954 | 1.7508 | D3 position defense -0.0026 +/- 0.0017 | none |
| receptions | 2,954 | 2.6852 | D3 -0.0012 +/- 0.0017 | none |
| receiving yards | 2,954 | 3.0019 | D7 -0.0007 +/- 0.0014 | none |
| carries | 1,327 | 1.2555 | D5 -0.0004 +/- 0.0004 | none |
| rushing yards | 1,327 | 2.8640 | D5 +0.0002 +/- 0.0018 | none |
| pass attempts | 544 | 2.2181 | D7 -0.0163 +/- 0.0103 | none |
| completions | 544 | 1.6276 | **D3 -0.0110 +/- 0.0055 (z -2.00)** | **D3** |
| passing yards | 544 | 3.8268 | D3 -0.0117 +/- 0.0096 | none |
| passing TDs | 544 | 1.6781 | **V -0.0103 +/- 0.0036 (z -2.84)** | **V** |
| interceptions | 544 | 1.0842 | V -0.0059 +/- 0.0044 | none |

- **The data report's headline signal does not move a line past Sleeper.**
  The man / zone and shell target-rate interaction (D5) chose the strongest
  ridge penalty on the fitting seasons' own CV (lambda 1e6) and scored
  +0.00000 +/- 0.00002 on targets. A real trait in a player's split (r 0.39)
  times a real trait in a defense's coverage mix (r 0.5 a season apart) is a
  share shift of a percent or two, and Sleeper's line already holds most of
  the player's volume.
- **Recalibrating Sleeper's level per position (R) made 2025 worse in seven
  of ten markets.** Five of those were by two SE or more: receptions +0.0106
  +/- 0.0049 (z 2.14), receiving yards +0.0079 +/- 0.0039 (2.06), carries
  +0.0052 +/- 0.0021 (2.49), completions +0.0151 +/- 0.0061 (2.48) and
  passing yards +0.0104 +/- 0.0050 (2.08). (The first version of this
  sentence said three; review F4.) In the other three markets it was not
  significantly better. The
  revised lines' 2023-2024 biases did not carry to 2025.
- **The k sensitivity** (the same ladder on the 2018-2025-k frame) made the
  same two decisions and no other.
- **O1 (the QB choice):** neither accepted group reads a QB-specific feature
  (D3 for completions is the defense's passing yards allowed; V is the spread,
  the total and the team's PROE). D5, the only group that reads the QB, was
  rejected under both choices: the last starter (completions +0.0057 +/-
  0.0025, passing TDs +0.0042 +/- 0.0016) and Sleeper's revised QB1
  (+0.0072 +/- 0.0032, +0.0034 +/- 0.0016).
- **Low reliability (the brief's small-sample check).** Each rung's gain was
  split at the median reliability of its lead feature (`low_reliability` in
  `validate.json`). One half beat the accepted model by more than two SE:
  targets D2 (overall defense) in its high-reliability half, -0.0040 +/-
  0.0019 (low half -0.0001 +/- 0.0016). That is the direction shrinkage
  predicts, but the rung as a whole (-0.0020 +/- 0.0013) did not pass. Two
  halves were worse by more than two SE: completions D6 high, +0.0177 +/-
  0.0056, and passing-TD D5 low, +0.0060 +/- 0.0024. Nothing here suggests a
  signal that low reliability hides.
- **By history depth** (the accepted models). Completions D3 gained most for
  QBs with 10+ weighted games (-0.0158 +/- 0.0068, 333 rows). Passing-TD V
  gained most for QBs with under 4 (-0.0235 +/- 0.0075, 87 rows).
- **Anytime TD** moves only through the targets and carries factors. Neither
  ships, so it is Sleeper's line: 3,357 rows, log loss 0.7489.
- **DraftKings points** (3,811 rows, Sleeper's DK mean >= 4):

  | variant | MAE | log loss |
  |---|---|---|
  | Sleeper | 5.576 | 2.2278 |
  | + nfl_usage | 5.562 | 2.2271 |
  | x matchup, teams preserved | 5.562 | 2.2271 |

  Matchup with team totals preserved vs Sleeper + usage: -0.00001 +/-
  0.00001. Unconstrained vs preserved: -0.0008 +/- 0.0005, 1.8 SE, short of
  the two needed to free team totals.

### 1.2 The 2026 test (once; models refit 2023-2025; 2018-2025 k)

| market | frozen rows | frozen: Sleeper log loss | shipped model d | revised rows | revised: shipped model d |
|---|---|---|---|---|---|
| targets | 642 | 1.8265 | 0 (nothing accepted) | 1,007 | 0 |
| receptions | 642 | 2.7738 | 0 | 1,007 | 0 |
| receiving yards | 642 | 3.0715 | 0 | 1,007 | 0 |
| carries | 286 | 1.3345 | 0 | 464 | 0 |
| rushing yards | 286 | 2.7092 | 0 | 464 | 0 |
| pass attempts | 114 | 2.2096 | 0 | 185 | 0 |
| completions | 91 | 1.6137 | 0 (D3 failed the walk) | 185 | 0 |
| passing yards | 114 | 3.8122 | 0 | 185 | 0 |
| passing TDs | 114 | 1.6512 | **-0.0067 +/- 0.0082 (V ships)** | 185 | +0.0013 +/- 0.0068 |
| interceptions | 91 | 1.0211 | 0 | 185 | 0 |

- **The shipping walk (frozen rows).** Completions D3: +0.0113 +/- 0.0125,
  weight 0. Passing TDs V: -0.0067 +/- 0.0082, ships. The shipped
  coefficients (refit 2023-2025, lambda 1000) are 0.051 per 7 points of
  spread, 0.053 per 10 points of total and 0.447 per unit of shrunk PROE, all
  on log passing TDs. A 7-point favourite in a 54-point game, with league-
  average PROE, throws about 11% more touchdowns than Sleeper's line says
  (exp(0.051 + 0.053)). By week (review F5): week 1 +0.0060 +/- 0.0136 (28
  QB-games), week 2 -0.0167 +/- 0.0204 (30), week 3 -0.0148 +/- 0.0144 (32),
  week 6 +0.0020 +/- 0.0164 (24). Week 3's captures are 7-11 days old and the
  term reads the closing spread and total, so on those 32 rows a stale Sleeper
  line is paired with a line dated after it; without week 3 the gain is
  -0.0035 +/- 0.0099 (82). It ships by the rule's letter (d < 0 either way).
- **Every unaccepted rung** was also scored on the frozen rows (section 8).
  None would have changed the picture: most were worse than Sleeper there
  (targets D3 +0.0050, receptions R +0.0162).
- **DFS hook: does not ship.** By the rule's letter, the frozen-row
  difference between Sleeper + usage x matchup (team totals preserved) and
  Sleeper + usage is +2.8e-17 nats. That is floating-point noise, because the
  two are the same lines: the only shipped factor is a QB's passing TDs, and
  preserving his team's total returns his own line. `apply()` returns a stamp
  and changes nothing. The DK results on frozen rows (772 rows): Sleeper MAE
  5.744, + usage 5.727, x matchup preserved 5.727.
- **The revision effect (rule C3).** The rows with both a frozen and a
  revised Sleeper line show the post-week revision makes the baseline look
  better than it was before kickoff. Targets -0.0226 +/- 0.0111 (613 rows,
  35% of lines changed). Receiving yards -0.0527 +/- 0.0268 (40% changed,
  mean change 1.4 yds). Receptions -0.0232 +/- 0.0198. Rushing yards
  +0.0205 +/- 0.0169. Passing yards +0.0096 +/- 0.0509 (59% of QB lines
  changed, mean 6.9 yds). In receiving, the revised line (which knows the
  inactives) is the STRONGER baseline: a gain measured against it on
  2023-2025 is, if anything, conservative. In rushing it is slightly weaker.
- **Calibration** (10-bin ECE, frozen rows, Sleeper = the shipped model
  wherever nothing ships): receptions 4+ 0.022, receiving yards 50+ 0.059,
  rushing yards 50+ 0.049, passing yards 250+ 0.056, targets 5+ 0.028. The
  2025 tables are in `validate.json` `calibration`. Rushing yards 50+ is the
  one threshold where the spread is fine; the low thresholds are not
  (section 2a, review F3).
- **Research equals production:** on about 4,000 random rows each run,
  `nfl_matchup.market_adjust` reproduces the research design's factor to
  2.2e-16, `nfl_matchup.market_dist` (`prop_dist`) reproduces the numpy
  probabilities to 2.3e-14, and the DK gamma to 1.1e-16 (`agreement` in both
  JSONs).

### 1.3 Kalshi, pre-kickoff prices (descriptive, R5)

The sample was 4,509 sampled settled markets (`research/nfl_matchup_kalshi.py`,
seed 20261023, about 900 per series). 3,856 had a pre-kickoff book within
20c. 2,952 matched a frame row and a priced rung. 836 more were 2026 markets
whose player-game has no frozen baseline, and were not used: weeks 4-5 have
no capture at all, and some players in weeks 1-3 and 6 were not captured (of
the 949 such 2026 frame rows, 743 are weeks 4-5; review N4). On every
matched event the official outcome in the frame agreed with Kalshi's
settlement (2,952 / 2,952). Because no shipped factor touches these markets,
"model" equals Sleeper with the fitted spread.

| season | rungs | hit rate | model / Sleeper log loss | Kalshi mid log loss | model Brier | Kalshi Brier |
|---|---|---|---|---|---|---|
| 2025 (revised baseline) | 1,442 | 0.427 | 0.5948 | 0.5935 | 0.2038 | 0.2041 |
| 2026 wk 1-3, 6 (frozen) | 1,510 | 0.268 | 0.4563 | **0.4322** | 0.1451 | **0.1382** |
| 2026 wk 6, the app's props log (frozen) | 2,875 | 0.310 | 0.5051 | **0.4553** | 0.1615 | **0.1484** |

- **On 2026's frozen lines, Kalshi's pre-kickoff mid beat the model in every
  stat:** passing yards 0.4739 vs 0.5011, receptions 0.5045 vs 0.5119,
  receiving yards 0.4306 vs 0.4642, rushing yards 0.4466 vs 0.5063, anytime
  TD 0.3291 vs 0.3340. The props log tells the same story on 2,875 rungs.
  On 2025 the two were level (0.5948 vs 0.5935), but that comparison uses
  Sleeper's revised line, which knew the inactives.
- **Hypothetical ROI** (one contract on the positive-EV side at the
  pre-kickoff ask, fee in):
  - 2025: 770 bets over 123 games, +0.2% (95% -12.2% to +11.8%).
  - 2026 frozen: 1,028 bets over 54 games, **-7.0% (95% -14.4% to +1.1%)**.

  There is no edge against Kalshi here. The 2026 figure is the honest one,
  and it lost money.
- **Where the market is better, 2026 frozen** (`kalshi.json` `by_stat_pos`,
  `rush_yd_by_k`, added after the review; every earlier figure in the file
  unchanged): rushing yards by QBs is the worst cell, model 0.5524 vs Kalshi
  0.4067 (67 rungs); RB rushing yards 0.4921 vs 0.4589 (217); WR receiving
  yards 0.4605 vs 0.4171 (168); TE receptions 0.5542 vs 0.4949 (85). RB
  receptions (0.5296 vs 0.5740, 66) and TE anytime TD (0.5445 vs 0.5522, 82)
  went the model's way. Rushing yards by threshold: under 20 0.663 vs 0.651
  (43), 20-39 0.676 vs 0.592 (82), 40-59 0.631 vs 0.509 (54), 60+ 0.246 vs
  0.217 (105).

## 2. Bugs found in the data foundation and how this model avoids them

- **Team-change mismatch in the on-field target rates (the review's F1),
  found independently here first.** `nfl_matchup_profiles.player(pid, team=)`
  divided targets over ALL of a player's games by on-field dropbacks over his
  current team's games only: Nelson Agholor, 2023 week 5 (BAL): 0.46 vs man and
  0.91 vs zone against an overall 0.15 (`p_man_tgt` 18.25 targets over ~40
  BAL dropbacks). This model never reads those columns:
  `nfl_matchup.player_splits` sums, over the PRIOR seasons' games only (any
  team), the on-field dropbacks under each coverage and the player's targets on
  those very plays (profiles schema 2's `G_TGT_MAN` / `G_TGT_ZONE` /
  `G_TCOV0` slots), so numerator and denominator are the same plays.
- **2026's overall target rate per on-field dropback climbs with the season
  (found here).** No 2026 participation exists, so a 2026 player's on-field
  dropbacks are 2024-25's only while his 2026 targets join the numerator:
  the dataset's `p_tgt_per_db` for WRs read 0.18, 0.20, 0.24, 0.27, 0.31, 0.33
  across 2026 weeks 1-6 (2023-2025 weeks 1-6: 0.16-0.20 flat). This model's
  denominator for 2026 is 2024's and 2025's on-field dropbacks and its
  numerator their targets on those plays: no 2026 game enters either
  (`player_splits` loops `range(season - BACK, season)`; a guard proves a 2026
  cutoff ignores 2026 games).
- **Shrinkage k.** The profiles shrank pressure, sack, time to throw and EPA
  per dropback allowed with the blitz k (90.7) and box shares with the
  personnel k (48.1). This model reads each raw value and its n and shrinks
  with the measured k (`nfl_matchup.K`: pressure 437, sack 621, EPA / dropback
  1,040, light box 163, heavy box 167).
- **Within-week leak in the dataset builder (F3).** The frame builds its own
  snapshot per week from `season_tallies(max_week=week)`; it reads none of the
  dataset's `d_*_adj_*` / `x_*` columns.
- **Route vocabulary (F2).** No route feature is used.
- **QB selection (F6 / O1).** Last starter as of the cutoff for 2023-2025, the
  frozen QB1 for 2026; the revised-QB1 variant is a reported sensitivity.

## 2a. After the reviews (2026-10-23): fixes and disclosures

Nothing here re-ran the validation or the test (`test_lock.json` is
untouched). Every figure below is a reading of the committed frames and
JSONs with the committed model, reproduced for this section. Each matches
the Fable review's to the digits it printed, with one exception: the
training-row family log losses in F1. The review scored a slightly
different row set there (rushing 1.3921 vs 1.3662, receiving 2.1464 vs
2.1549), and its verdict is the same.

**F1: the yards family was never chosen by the rule, and could not reach
production.** R2 says the positive part's family (gamma or lognormal) is
"chosen by training log loss". `fit_dispersion` took the mean threshold log
loss over the whole fit universe, which holds rows with a yards line but no
count line (rushing yards 2023-2024: 26 rows, 25 of them WRs). Those rows score
NaN, so both means were NaN, `nan < nan` is False, and the first family in
the loop (gamma) was kept whatever the data said. The choice was then stored
under `_family`, which `strip()` drops from every JSON written, so
`coefs.json`, `validate.json` and `test.json` never named a family, and
production fell back to gamma. Over the rows both families score (2023-2024
fit rows, spread fitted on them):

| market | NaN rows | gamma | lognormal | the rule as written picks |
|---|---|---|---|---|
| receiving yards | 3 | 2.1783 | 2.1871 | gamma |
| rushing yards | 26 | 1.3923 | **1.3658** | **lognormal** |

Out of sample, lognormal is WORSE (lognormal minus gamma, threshold log loss
per row):

- 2025 (spread fitted 2023-2024): rushing +0.0213 +/- 0.0084 (1,327 rows),
  receiving +0.0138 +/- 0.0051 (2,954 rows).
- 2026 frozen (the committed 2023-2025 spread): rushing +0.0232 +/- 0.0212
  (286 rows), receiving +0.0324 +/- 0.0127 (642 rows).

The tested and shipped family, gamma, reached the board by loop order. It is
also the better one out of sample. Fixing the code and re-running R2 as
written would therefore make the board worse, so nothing was re-run. What
changed:

1. `choose_family` compares the families on the rows every family scores.
2. The choice is stored under `family`, a key `strip()` keeps.
   `nfl_matchup.yards_family` reads it. `coefs.json` gains exactly
   `"family": "gamma"` for both yards markets (sha256 `ee318ff5...` ->
   `fddfdee0...`); nothing else in the file changed.
3. **R2 is amended for future refits (not applied to this run).** The
   family is chosen on the VALIDATION season's priced rows and carried into
   the test and the export (`cmd_validate` passes `family_rows`, `cmd_test`
   passes `family`). On 2025 it picks gamma for both markets. This amendment
   was written AFTER the review measured both families on 2025 and 2026, so
   its agreement with the shipped choice is known, not tested. It binds only
   future refits. `cmd_test` now refuses a `validate.json` that names no
   family, as the committed one does.
4. `agreement()` hands production the model through the JSON round trip, so
   a key that never reaches the file can no longer pass it.
5. Guards pin that each yards market in the committed file names a family,
   that production draws exactly that family, and (numpy, on a synthetic
   frame) that a NaN row no longer decides the choice.

**F2: zero-width bands.** `probs_with_band` returned `p_lo = p_hi = p`
wherever the shipped coefficients' SE is 0. That covers every market with no
shipped term, plus anytime TD and DK points: 99% of the 2026-10-25 board's
lines, and every +EV line on it read "robust". These now carry `p_lo = p_hi
= null`, which means no measured band. Passing TDs, the one market with a
shipped term, keep their band (0.005-0.083 wide on that board, rebuilt after
the fix).
`prop_value` reads a null band as unknown, so `positive_ev_robust` cannot
fire on it. Guards cover both directions: null where nothing ships, positive
width where a term ships.

**F3: low rushing-yard thresholds run high for running backs.** The table
uses production's `market_dist` with the committed model; each cell is
hit rate / model:

| rows | Sleeper line | n | 10+ | 15+ | 25+ | 40+ |
|---|---|---|---|---|---|---|
| QB 2023-25 revised | 10-20 yds | 619 | 0.527 / 0.579 | 0.384 / 0.381 | **0.234 / 0.148** | **0.086 / 0.032** |
| QB 2023-25 revised | 20-30 | 238 | **0.735 / 0.798** | 0.643 / 0.653 | 0.412 / 0.399 | 0.197 / 0.169 |
| QB 2026 frozen | 10-20 | 49 | 0.469 / 0.600 | 0.367 / 0.401 | 0.224 / 0.160 | 0.020 / 0.036 |
| QB 2026 frozen | 20-30 | 21 | 0.714 / 0.799 | 0.667 / 0.651 | 0.333 / 0.394 | 0.095 / 0.164 |
| RB 2023-25 revised | 20-30 | 487 | **0.671 / 0.857** | **0.579 / 0.714** | 0.394 / 0.435 | 0.232 / 0.171 |
| RB 2026 frozen | 20-30 | 30 | 0.667 / 0.862 | 0.467 / 0.721 | 0.300 / 0.442 | 0.233 / 0.175 |
| RB 2023-25 revised | 50+ | 1,322 | 0.982 / 0.981 | 0.964 / 0.952 | 0.900 / 0.863 | 0.743 / 0.693 |

The model uses one positive-part CV per position (QB 0.70, RB 0.63), with
the zero mass from a NegBin on the carry line. That is too thin at both
tails for low lines:

- A back projected for 20-30 yards reached 10 two times in three; the model
  said six in seven.
- A quarterback projected for 10-20 reached 25 and 40 yards far more often
  than the model allows.
- The zero mass is short too. On 2023-2024 RB lines of 20-30 yards, 8.6% of
  outcomes were 0 or less, against a NegBin zero mass of 1.1% on average.

Against Kalshi on 2026, the rushing-yard rungs from 20 to 59 yards lose by
0.08-0.12 nats (under 20 by 0.01), and QB rushing yards is the worst cell
(1.3). Nothing ships in rushing
yards either way.

The coordinator's example, Bryce Young's 10+ at 0.79 against Kalshi's ~0.51,
is mostly Kalshi disagreeing with Sleeper's MEAN: a 51c 10+ implies a median
near 10 yards, against Sleeper's 23. The shape error accounts for 6-8 points
of it.

What changed:

- The slate notes now say this (`slate_notes`), for as long as the rushing
  spread is the single-CV form.
- `matchups.mark_record` already puts "market better on record" on every +EV
  line in a stat Kalshi beat.
- A line-dependent spread was pre-registered in section 9, before any 2026
  week-8 game. It fixed the low-line cells on 2025 but failed its gate
  overall (-0.0086 +/- 0.0137), so it does not ship, and the weeks 8-10
  judgement does not run (9.1).

**F5:** the shipped V term's frozen gain by week is in 1.2.

**F6: the registered `nfl_matchup.py` bytes were not preserved.** Its hash
(`cdea47a5...`) is recorded in `prereg.json`, `validate.json` and
`test.json`. The files were untracked and nothing was committed before
`prereg`, so no copy of that version exists. The Fable review (its section
4) proved the behaviour instead:

- The current code recomputes every feature of 701 frame rows (2025 week 10,
  2026 week 6) to the frame's 6-dp rounding, and the defense vectors
  exactly.
- `agreement()` returns the test's exact figures.
- The shipped model's frozen and revised log losses recompute exactly.

`research/nfl_matchup_fit.py`'s registered bytes do survive; they differ
from the post-test file only in the `kalshi` command. `nfl_matchup_frame.py`
is byte-identical to its registered version. The section 9 rule does what
this one did not: its code's bytes are stored beside its record.

**N1.** The GLM clips negative yards at 0 (`np.maximum(y, 0)` in `fit_glm`
and in the lambda CV's deviance). In 2023-2025, 438 rushing and 199
receiving outcomes were below 0. A quasi-Poisson needs y >= 0, and R2 did
not say so. The threshold scoring reads the real values, where a negative
and a 0 score the same at every threshold. The spread's CV reads the
positive outcomes. Without the clip, the review's refit moved receiving
yards' R rung by 3e-4.

**N3.** A game with no believable Kalshi spread or total reads the neutral
fallback (margin 0, total 44). It is now marked in three places: the factor
row ("FALLBACK: no believable Kalshi spread ladder"), the feature's evidence
(`kind` "fallback"), and one slate note per game whose shipped term reads
it. On the 2026-10-25 board this was CLE @ TEN (margin 0, total 37), and in
the review's test the fallback moved Josh Allen's passing-TD mean by 11%.

**Code changed after the test** (none of it touches a tested number):

- `nfl_matchup.py`:
  - `yards_family` (F1);
  - null bands in `probs_with_band`, the DK points ladder and `line_labels`
    (F2);
  - the F3 slate note;
  - the margin / total evidence kind, the factor text and
    `line_fallback_notes` (N3). The feature VALUES are unchanged.
- `research/nfl_matchup_fit.py`:
  - `choose_family`, `fit_dispersion(family_rows=, family=)` and the amended
    validate / test handling (F1);
  - the dead first `fit_dk_scale` call in `cmd_validate` removed (N2);
  - `agreement()` through the JSON round trip;
  - `kalshi`'s by-position and by-threshold cells. Re-running it reproduced
    every earlier figure in `kalshi.json` exactly and added only those
    cells.
- `research/nfl_matchup_kalshi.py`: an identity map in `kick_map` removed
  (N5; WAS is WAS on both sides). The output is identical.
- `research/nfl_matchup_rushcv.py`: new (section 9).

## 3. Production

- `nfl_matchup.board(season, week)` / `board_for_date(date)` (what
  `matchups.produce` calls): every player with a Sleeper projection in the
  week's games -> role (unit on every fact), opponent profile (value, league,
  rank / percentile, unit, basis), factor rows (every factor the model has for
  the position; `effect` = the shipped model's own contribution, 0 at weight
  0, split so the rows sum exactly to the applied change), markets (Sleeper
  baseline, adjusted mean, the fitted distribution, every Kalshi-listed rung
  plus the standard ladder, each with p and its band (`p_lo` / `p_hi`; null
  where no shipped coefficient carries uncertainty, i.e. every market but
  passing TDs, and anytime TD and DK points), hit rates all / last 10
  / weighted vs similar defenses with n_eff, Kalshi's quote and
  `prop_value.line_value` + labels), comparables (the eight most similar past
  games with similarity, weight, stats and why), notes. Slate notes: what
  ships, the Kalshi record, the low-threshold rushing-yard warning (2a, F3),
  and a line per game whose spread or total fell back (2a, N3). Game ids are
  Sleeper's (digits). No NaN / inf (`_clean`). Pure Python; reads the
  committed `coefs.json` and `def_history.json`, the deep store's nflverse
  stores (the data foundation's PC job) and Kalshi's index.
- `nfl_matchup.apply(pairs, season, week)`: the DFS hook. A no-op stamp at
  weight 0. Otherwise every component of `means` moves by the shipped
  factors (targets / receptions / receiving yards / receiving TDs by the
  receiving markets' factors, carries / rushing yards / rushing TDs by the
  rushing markets', the QB's passing lines by the passing markets'), every
  team total of every component is renormalised back to the input's
  (Sleeper + nfl_usage), `proj_pts` moves by the PPR change. Never raises.

- Cost, measured 2026-10-23 on the 2026 week-7 Sunday slate
  (`board_for_date("2026-10-25")`: 12 games, 254 players, 2,854 Kalshi-listed
  rungs matched; live Sleeper, ESPN and Kalshi feeds): with the committed
  coefficients 14.0 s and peak RSS 146 MB; with a DEV file shipping every
  group (the worst case, never committed) 17.5 s cold, 7.7 s warm, 147 MB;
  payload 10.4 MB; numpy never imported. Re-measured after the review fixes
  on the same slate: 6.8 s warm and 152 MB peak. Of 9,227 lines, 72 carry a
  band (passing TDs) and the other 9,155 carry none. No line is zero-width,
  and none reads `positive_ev_robust` (813 read `positive_ev`). One slate
  note flagged CIN @ BAL's spread as the fallback. Nothing was ledgered.
- A board that cannot be built (no committed model, no week, an exception)
  says why in `error` AND in the slate-level `notes`.
- The frozen baselines the test can use (from the frame, no outcome read):
  week 1 330 of 407 rows (feeds/proj_2026_1, median 25 h before kickoff;
  the Thursday opener's rows are after the capture and excluded), week 2 377
  of 415 (prospective 153428, median 0.4 h), week 3 403 of 440 (prospective
  153448: an EARLY capture, median 233 h -- ten days -- before kickoff), weeks
  4-5 none, week 6 290 of 344 (the props log, median 0.3 h).

### Should the same adjustments feed `nfl_data.week_teams`?

Only if the DFS hook ships, and then through the same call. `week_teams`
sums each team's Sleeper lines into the drive engine's team expectations and
deals the simulated team game back to players by their share. With team
totals preserved, `apply()` leaves every team sum unchanged and moves only
the shares -- exactly the part of the combo maker's player props a matchup
can touch. The rows there carry no volume fields (no targets / carries /
attempts), so receiving and rushing TDs ride on the factors as in the DFS
hook and a QB's passing lines stay Sleeper's (one passer a team: preserved
totals return them unchanged). The diff is in the final hand-off; it is not
proposed while nothing ships.

## 4. Integration (for the orchestrator)

1. `nfl_dfs_sim.weekly_games`, right after the nfl_usage block:
   ```python
        # Matchup factors (nfl_matchup; research/reports/nfl_matchup_model.md):
        # the validated factors only, team totals preserved; a no-op stamp at
        # weight 0. Any failure leaves the lines as nfl_usage left them.
        try:
            import nfl_matchup
            mstamp = nfl_matchup.apply(pairs, int(season), int(week))
            for g in games.values():
                g["matchup"] = mstamp
        except Exception as _e:
            errlog.note("NFLM-weekly", _e)
   ```
2. `matchups.produce` already calls `nfl_matchup.board_for_date(key)`.
3. Guard suite: `from tests.guards_nfl_matchup_model import run as _nflmm_run; _nflmm_run(ck)`
   (already wired into `tests/combo_audit_guards.py`'s MX section).
   - **Size and cost:** 96 checks; 93 where numpy is absent, as in CI, where
     three research-fit checks report themselves skipped. Under a second,
     hermetic: the data foundation's synthetic season, the committed JSONs and
     report, no network, nothing written to the deep store.
   - **The original mutation run** (22 single-line bugs) still kills 20:
     leakage of the cutoff week and of the current season into the split
     rates, a frozen capture at kickoff, the shrinkage, the split sign, DK
     scoring and bonus, the yards zero mass, effects, team totals, proj_pts
     scoring, the hook's weight-0 switch, the coefs schema, a fact without a
     unit, the strong-fit threshold / side / matchup-only rule, the notes
     reason, distribution means, and the fitted positions. The two
     survivors are equivalent mutants: a non-finite component reads 0 and
     so can never change, and a non-digit game id falls back to a stable
     numeric id.
   - **The review's survivors and the fixes' reversals** (27 mutants), all
     killed:
     - participation read from a charted current season;
     - bands of zero width, or null even where a term ships;
     - proe as a matchup term;
     - the split's reliability read from the larger side;
     - the yards family hard-coded, or read from the stripped key;
     - every market labelled shipped;
     - the 300- and 100-yard bonuses one yard late;
     - accept() at 1 SE or with a smaller floor;
     - NaN rows deciding the family again;
     - the fallback marks and the F3 note dropped;
     - the N2 and N5 lines restored;
     - four committed-JSON edits the replays must catch (a shipped group the
       walk dropped, a walk keeping d > 0, a ladder rung and a k-sensitivity
       rung accepted against R3), and the committed family removed.
   - **On the pre-fix code** the new guard file fails 15 checks.
4. Deep store keys added: `nfl_matchup_defhist_<season>_wNN` (one week's
   defense vectors as of that week, written by `def_history` for weeks the
   committed table does not hold; never changes once written).


## 5. What the data supports, and what it does not

- **Who gets the ball against what** is a real trait on both sides (the data
  report: a player's man-vs-zone target-rate split r 0.39, a defense's man
  share r 0.87 within a season and 0.5 across seasons). It does not move a
  prop past Sleeper's line. Sleeper already projects the volume, and the
  coverage mix a 2026 game will see is known only from prior seasons.
- **What a defense allows** (per target, per position, per depth, per
  location) did not add to Sleeper out of sample. The one exception the
  rule found on 2025 (completions vs passing yards allowed to QBs) reversed
  on 2026's frozen lines.
- **Game script** (spread, total, PROE) on passing TDs is the only adjustment
  shipping, and its 2026 support is under one SE.
- **Kalshi's pre-kickoff prices were better than Sleeper's frozen line** in
  every stat on 2026.

## 6. Limitations and approximations

- 2026's frozen baselines exist for weeks 1, 2, 3 and 6 only. Week 3's
  capture is ten days early, and weeks 4-5 have none. The frozen test is
  642 receiving, 286 rushing and 114 passing player-games.
- 2023-2025 were fit and validated on Sleeper's REVISED lines (no frozen
  history exists). The revision effect (1.2) says that baseline is stronger
  in receiving than the one production faces.
- Coverage, shells, pressure and the on-field target rates are prior-season
  only for every row; the on-field rate is a proxy for per-route rate (a snap
  is not a route); the middle-third share is an alignment PROXY.
- The shell split's k is borrowed from the man-zone split (unmeasured). The
  by-depth / by-location allowances use the pooled per-target k (unmeasured
  per depth).
- The comparables' similarity bandwidth and the shrinkage constant (10
  games) are conventions, written before the run.
- DK points: 2-point plays and return TDs are in the outcome, not in the
  projection. On frozen props-log rows a missing lost-fumble line reads 0
  in both arms.
- The shipped V term reads the spread and total. It was fit on the closing
  line; production reads Kalshi's de-vigged ladder (`kalshi_nfl.implied`),
  so a game with no ladder reads margin 0 and total 44. The board marks that
  fallback (2a, N3).
- Parameter bands (`p_lo` / `p_hi`) are the shipped coefficients'
  uncertainty only, and the dispersion fit's own uncertainty is not in them.
  Where nothing ships there is no band: both are null since 2026-10-23. Until
  then they printed a zero-width band, which made every +EV line "robust"
  (2a, F2).
- The rushing-yards spread is one CV per position. It runs high at low
  thresholds for backs and low in the upper tail for quarterbacks on small
  lines, and the zero mass is short (2a, F3; section 9 is the pre-registered
  fix).

## 7. Reproduce

    export DEEP_CACHE_DIR=<agreed nflverse stores> VIGIL_NO_BG=1 W=<work dir> SL=<sleeper dir with proj/stats_2026_<w>.json>
    python3 -m research.nfl_matchup_kfit                                     # k_fit_only.json (2018-2024)
    NFLM_K=fit_only python3 -m research.nfl_matchup_frame --out $W/frame_val.csv.gz --defhist $W/dh_val.json \
        --sleeper $SL --props props_w06.json --seasons 2023 2024 2025      # sha256 97e90a68c29ef21f...
    python3 -m research.nfl_matchup_frame --out $W/frame_test.csv.gz --defhist $W/dh_test.json \
        --sleeper $SL --props props_w06.json                                # sha256 5180c0d59140ba3b...
    python3 -m research.nfl_matchup_fit prereg                               # done once: 09:39:09 UTC
    python3 -m research.nfl_matchup_fit validate --frame $W/frame_val.csv.gz --frame-sens $W/frame_test.csv.gz
    python3 -m research.nfl_matchup_fit test --frame $W/frame_test.csv.gz   # ran once; refuses again
    python3 research/nfl_matchup_kalshi.py list <raw>; python3 research/nfl_matchup_kalshi.py candles <raw> --n 900
    python3 -m research.nfl_matchup_fit kalshi --frame $W/frame_test.csv.gz --frame2025 $W/frame_val.csv.gz \
        --raw <raw> --props props_w06.json
    python3 -m research.nfl_matchup_fit export --defhist $W/dh_test.json
    python3 -m research.nfl_matchup_fit tables                               # section 8
    python3 tests/guards_nfl_matchup_model.py

`props_w06.json` is `git show origin/sim-history:props/nfl_props_2026_w06.json`.
Right after the test, only presentation code in `nfl_matchup.py` changed
(the slate notes' wording and the Kalshi note), and the `kalshi` command
gained per-season frames. The registered `nfl_matchup.py` bytes were not
preserved; section 2a (F6) says how the behaviour was proven instead. The
review fixes since then are listed at the end of 2a. Because of the F1
amendment, `test` now refuses the committed `validate.json`, which names no
family. It is locked in any case.

## 8. Full tables (rendered by `python3 -m research.nfl_matchup_fit tables`)

### Validation ladder (fit 2023-2024, scored on 2025; d = candidate minus accepted, threshold log loss per row, SE clustered by game)

| market | rung | n | MAE | RMSE | log loss | Brier | d log loss | SE | z | lambda | decision |
|---|---|---|---|---|---|---|---|---|---|---|---|
| tgt | 1 | 2954 | 1.972 | 2.515 | 1.75085 | 0.14222 |  |  |  |  | baseline |
| tgt | R | 2954 | 1.994 | 2.525 | 1.75337 | 0.14245 | +0.00253 | 0.00207 | +1.22 | 1.0 | weight 0 |
| tgt | V | 2954 | 1.975 | 2.518 | 1.75221 | 0.14234 | +0.00136 | 0.00080 | +1.69 | 30000.0 | weight 0 |
| tgt | D2 | 2954 | 1.969 | 2.512 | 1.74882 | 0.14202 | -0.00203 | 0.00125 | -1.62 | 3000.0 | weight 0 |
| tgt | D3 | 2954 | 1.967 | 2.510 | 1.74825 | 0.14194 | -0.00259 | 0.00174 | -1.49 | 3000.0 | weight 0 |
| tgt | D4 | 2954 | 1.972 | 2.516 | 1.75083 | 0.14222 | -0.00001 | 0.00079 | -0.02 | 30000.0 | weight 0 |
| tgt | D5 | 2954 | 1.972 | 2.515 | 1.75085 | 0.14222 | +0.00000 | 0.00002 | +0.01 | 1000000.0 | weight 0 |
| tgt | D6 | 2954 | 1.972 | 2.515 | 1.75085 | 0.14222 | +0.00000 | 0.00000 | +0.16 | 1000000.0 | weight 0 |
| tgt | D7 | 2954 | 1.971 | 2.514 | 1.75022 | 0.14213 | -0.00063 | 0.00110 | -0.57 | 100000.0 | weight 0 |
| rec | 1 | 2954 | 1.530 | 1.960 | 2.68524 | 0.14561 |  |  |  |  | baseline |
| rec | R | 2954 | 1.559 | 1.979 | 2.69581 | 0.14627 | +0.01058 | 0.00493 | +2.14 | 1.0 | weight 0 |
| rec | V | 2954 | 1.540 | 1.970 | 2.69731 | 0.14642 | +0.01207 | 0.00352 | +3.43 | 3000.0 | weight 0 |
| rec | D2 | 2954 | 1.530 | 1.959 | 2.68443 | 0.14558 | -0.00081 | 0.00165 | -0.49 | 10000.0 | weight 0 |
| rec | D3 | 2954 | 1.529 | 1.958 | 2.68407 | 0.14551 | -0.00117 | 0.00169 | -0.69 | 10000.0 | weight 0 |
| rec | D4 | 2954 | 1.530 | 1.960 | 2.68525 | 0.14558 | +0.00001 | 0.00114 | +0.01 | 10000.0 | weight 0 |
| rec | D5 | 2954 | 1.530 | 1.960 | 2.68524 | 0.14561 | +0.00000 | 0.00002 | +0.05 | 1000000.0 | weight 0 |
| rec | D6 | 2954 | 1.530 | 1.960 | 2.68525 | 0.14561 | +0.00001 | 0.00001 | +1.44 | 1000000.0 | weight 0 |
| rec | D7 | 2954 | 1.535 | 1.964 | 2.68946 | 0.14585 | +0.00422 | 0.00333 | +1.27 | 30000.0 | weight 0 |
| rec_yd | 1 | 2954 | 21.156 | 27.704 | 3.00191 | 0.13897 |  |  |  |  | baseline |
| rec_yd | R | 2954 | 21.540 | 27.883 | 3.00986 | 0.13953 | +0.00795 | 0.00386 | +2.06 | 1.0 | weight 0 |
| rec_yd | V | 2954 | 21.287 | 27.772 | 3.00682 | 0.13929 | +0.00491 | 0.00196 | +2.50 | 100000.0 | weight 0 |
| rec_yd | D2 | 2954 | 21.153 | 27.706 | 3.00162 | 0.13896 | -0.00029 | 0.00035 | -0.83 | 1000000.0 | weight 0 |
| rec_yd | D3 | 2954 | 21.149 | 27.702 | 3.00135 | 0.13894 | -0.00057 | 0.00059 | -0.97 | 1000000.0 | weight 0 |
| rec_yd | D4 | 2954 | 21.146 | 27.709 | 3.00127 | 0.13893 | -0.00065 | 0.00058 | -1.12 | 1000000.0 | weight 0 |
| rec_yd | D5 | 2954 | 21.159 | 27.706 | 3.00206 | 0.13898 | +0.00014 | 0.00027 | +0.54 | 1000000.0 | weight 0 |
| rec_yd | D6 | 2954 | 21.157 | 27.703 | 3.00194 | 0.13897 | +0.00003 | 0.00004 | +0.63 | 1000000.0 | weight 0 |
| rec_yd | D7 | 2954 | 21.182 | 27.720 | 3.00118 | 0.13897 | -0.00073 | 0.00140 | -0.52 | 1000000.0 | weight 0 |
| car | 1 | 1327 | 3.092 | 4.087 | 1.25550 | 0.10046 |  |  |  |  | baseline |
| car | R | 1327 | 3.135 | 4.122 | 1.26074 | 0.10086 | +0.00523 | 0.00210 | +2.49 | 1.0 | weight 0 |
| car | V | 1327 | 3.111 | 4.110 | 1.25790 | 0.10069 | +0.00240 | 0.00364 | +0.66 | 3000.0 | weight 0 |
| car | D2 | 1327 | 3.108 | 4.103 | 1.25881 | 0.10090 | +0.00331 | 0.00288 | +1.15 | 3000.0 | weight 0 |
| car | D3 | 1327 | 3.118 | 4.122 | 1.25959 | 0.10106 | +0.00408 | 0.00367 | +1.11 | 1000.0 | weight 0 |
| car | D4 | | | | | | | | | | skipped (no feature) |
| car | D5 | 1327 | 3.092 | 4.086 | 1.25509 | 0.10044 | -0.00041 | 0.00039 | -1.06 | 30000.0 | weight 0 |
| car | D6 | 1327 | 3.090 | 4.083 | 1.25527 | 0.10042 | -0.00023 | 0.00090 | -0.26 | 10000.0 | weight 0 |
| car | D7 | 1327 | 3.111 | 4.110 | 1.25544 | 0.10068 | -0.00007 | 0.00373 | -0.02 | 30000.0 | weight 0 |
| rush_yd | 1 | 1327 | 21.247 | 28.778 | 2.86400 | 0.13368 |  |  |  |  | baseline |
| rush_yd | R | 1327 | 21.827 | 29.135 | 2.87506 | 0.13433 | +0.01106 | 0.00609 | +1.82 | 1.0 | weight 0 |
| rush_yd | V | 1327 | 21.377 | 28.903 | 2.86624 | 0.13375 | +0.00224 | 0.00456 | +0.49 | 30000.0 | weight 0 |
| rush_yd | D2 | 1327 | 21.247 | 28.780 | 2.86469 | 0.13372 | +0.00070 | 0.00101 | +0.69 | 1000000.0 | weight 0 |
| rush_yd | D3 | 1327 | 21.284 | 28.832 | 2.86906 | 0.13401 | +0.00506 | 0.00411 | +1.23 | 100000.0 | weight 0 |
| rush_yd | D4 | | | | | | | | | | skipped (no feature) |
| rush_yd | D5 | 1327 | 21.258 | 28.777 | 2.86417 | 0.13370 | +0.00017 | 0.00179 | +0.10 | 100000.0 | weight 0 |
| rush_yd | D6 | 1327 | 21.240 | 28.755 | 2.86467 | 0.13370 | +0.00067 | 0.00248 | +0.27 | 10000.0 | weight 0 |
| rush_yd | D7 | 1327 | 21.390 | 28.889 | 2.86779 | 0.13402 | +0.00380 | 0.00723 | +0.53 | 100000.0 | weight 0 |
| patt | 1 | 544 | 6.707 | 8.466 | 2.21813 | 0.18670 |  |  |  |  | baseline |
| patt | R | 544 | 6.688 | 8.453 | 2.21611 | 0.18649 | -0.00202 | 0.00193 | -1.05 | 1.0 | weight 0 |
| patt | V | 544 | 6.685 | 8.432 | 2.20824 | 0.18579 | -0.00989 | 0.00497 | -1.99 | 10000.0 | weight 0 |
| patt | D2 | 544 | 6.667 | 8.432 | 2.20706 | 0.18557 | -0.01107 | 0.00683 | -1.62 | 10000.0 | weight 0 |
| patt | D3 | 544 | 6.633 | 8.424 | 2.20227 | 0.18496 | -0.01586 | 0.00845 | -1.88 | 300.0 | weight 0 |
| patt | D4 | | | | | | | | | | skipped (no feature) |
| patt | D5 | 544 | 6.722 | 8.495 | 2.22306 | 0.18721 | +0.00493 | 0.00409 | +1.21 | 30000.0 | weight 0 |
| patt | D6 | 544 | 6.707 | 8.467 | 2.21824 | 0.18671 | +0.00011 | 0.00011 | +1.04 | 1000000.0 | weight 0 |
| patt | D7 | 544 | 6.631 | 8.417 | 2.20188 | 0.18499 | -0.01625 | 0.01028 | -1.58 | 30000.0 | weight 0 |
| pcmp | 1 | 544 | 4.573 | 5.890 | 1.62762 | 0.18130 |  |  |  |  | baseline |
| pcmp | R | 544 | 4.649 | 5.973 | 1.64274 | 0.18329 | +0.01512 | 0.00611 | +2.48 | 1.0 | weight 0 |
| pcmp | V | 544 | 4.665 | 5.977 | 1.64692 | 0.18394 | +0.01930 | 0.00817 | +2.36 | 3000.0 | weight 0 |
| pcmp | D2 | 544 | 4.557 | 5.887 | 1.62427 | 0.18077 | -0.00335 | 0.00544 | -0.62 | 10000.0 | weight 0 |
| pcmp | D3 | 544 | 4.522 | 5.863 | 1.61667 | 0.17959 | -0.01095 | 0.00546 | -2.00 | 3000.0 | ACCEPT |
| pcmp | D4 | | | | | | | | | | skipped (no feature) |
| pcmp | D5 | 544 | 4.550 | 5.882 | 1.62242 | 0.18050 | +0.00575 | 0.00247 | +2.32 | 10000.0 | weight 0 |
| pcmp | D6 | 544 | 4.561 | 5.881 | 1.62457 | 0.18084 | +0.00790 | 0.00436 | +1.81 | 100000.0 | weight 0 |
| pcmp | D7 | 544 | 4.607 | 5.933 | 1.63373 | 0.18202 | +0.01706 | 0.00712 | +2.40 | 30000.0 | weight 0 |
| pass_yd | 1 | 544 | 57.495 | 73.459 | 3.82685 | 0.18302 |  |  |  |  | baseline |
| pass_yd | R | 544 | 57.684 | 73.672 | 3.83724 | 0.18362 | +0.01039 | 0.00500 | +2.08 | 1.0 | weight 0 |
| pass_yd | V | 544 | 57.496 | 73.417 | 3.82514 | 0.18296 | -0.00171 | 0.00561 | -0.30 | 100000.0 | weight 0 |
| pass_yd | D2 | 544 | 57.474 | 73.447 | 3.82601 | 0.18296 | -0.00084 | 0.00287 | -0.29 | 1000000.0 | weight 0 |
| pass_yd | D3 | 544 | 57.193 | 73.320 | 3.81514 | 0.18223 | -0.01171 | 0.00962 | -1.22 | 100000.0 | weight 0 |
| pass_yd | D4 | | | | | | | | | | skipped (no feature) |
| pass_yd | D5 | 544 | 57.520 | 73.501 | 3.82807 | 0.18311 | +0.00122 | 0.00204 | +0.60 | 1000000.0 | weight 0 |
| pass_yd | D6 | 544 | 57.516 | 73.468 | 3.82734 | 0.18305 | +0.00049 | 0.00091 | +0.54 | 1000000.0 | weight 0 |
| pass_yd | D7 | 544 | 57.379 | 73.318 | 3.81991 | 0.18261 | -0.00694 | 0.00583 | -1.19 | 1000000.0 | weight 0 |
| ptd | 1 | 544 | 0.950 | 1.151 | 1.67806 | 0.18810 |  |  |  |  | baseline |
| ptd | R | 544 | 0.945 | 1.148 | 1.67561 | 0.18780 | -0.00245 | 0.00231 | -1.06 | 1.0 | weight 0 |
| ptd | V | 544 | 0.944 | 1.146 | 1.66781 | 0.18682 | -0.01025 | 0.00361 | -2.84 | 1000.0 | ACCEPT |
| ptd | D2 | 544 | 0.944 | 1.146 | 1.66819 | 0.18686 | +0.00038 | 0.00059 | +0.65 | 1000.0 | weight 0 |
| ptd | D3 | 544 | 0.944 | 1.147 | 1.66889 | 0.18694 | +0.00108 | 0.00106 | +1.02 | 1000.0 | weight 0 |
| ptd | D4 | | | | | | | | | | skipped (no feature) |
| ptd | D5 | 544 | 0.947 | 1.148 | 1.67205 | 0.18736 | +0.00424 | 0.00160 | +2.65 | 3000.0 | weight 0 |
| ptd | D6 | 544 | 0.943 | 1.146 | 1.66796 | 0.18681 | +0.00015 | 0.00075 | +0.20 | 1000.0 | weight 0 |
| ptd | D7 | 544 | 0.947 | 1.148 | 1.67292 | 0.18742 | +0.00511 | 0.00301 | +1.70 | 10000.0 | weight 0 |
| int | 1 | 544 | 0.666 | 0.807 | 1.08421 | 0.18214 |  |  |  |  | baseline |
| int | R | 544 | 0.665 | 0.804 | 1.07874 | 0.18117 | -0.00548 | 0.00437 | -1.25 | 1.0 | weight 0 |
| int | V | 544 | 0.662 | 0.804 | 1.07833 | 0.18096 | -0.00588 | 0.00436 | -1.35 | 100.0 | weight 0 |
| int | D2 | 544 | 0.665 | 0.807 | 1.08364 | 0.18204 | -0.00057 | 0.00168 | -0.34 | 1000.0 | weight 0 |
| int | D3 | | | | | | | | | | skipped (no feature) |
| int | D4 | | | | | | | | | | skipped (no feature) |
| int | D5 | 544 | 0.666 | 0.807 | 1.08422 | 0.18215 | +0.00000 | 0.00000 | +2.05 | 1000000.0 | weight 0 |
| int | D6 | 544 | 0.666 | 0.807 | 1.08364 | 0.18201 | -0.00057 | 0.00120 | -0.47 | 300.0 | weight 0 |
| int | D7 | 544 | 0.665 | 0.807 | 1.08328 | 0.18195 | -0.00094 | 0.00066 | -1.43 | 10000.0 | weight 0 |

### DraftKings points, 2025 (validation models)

| variant | n | MAE | RMSE | log loss | Brier |
|---|---|---|---|---|---|
| S | 3811 | 5.576 | 7.220 | 2.22783 | 0.11794 |
| SU | 3811 | 5.562 | 7.225 | 2.22713 | 0.11793 |
| SxM | 3811 | 5.574 | 7.219 | 2.22703 | 0.11789 |
| SUxM_free | 3811 | 5.560 | 7.223 | 2.22634 | 0.11789 |
| SUxM_team | 3811 | 5.562 | 7.225 | 2.22713 | 0.11793 |

- SU_vs_S: d log loss -0.00069 (SE 0.00301); d MAE -0.0136 (SE 0.0086)

- SxM_vs_S: d log loss -0.00079 (SE 0.00045); d MAE -0.0021 (SE 0.0015)

- SUxM_free_vs_SU: d log loss -0.00080 (SE 0.00045); d MAE -0.0022 (SE 0.0015)

- SUxM_team_vs_SU: d log loss -0.00001 (SE 0.00001); d MAE -0.0000 (SE 0.0000)

- SUxM_free_vs_SUxM_team: d log loss -0.00079 (SE 0.00045); d MAE -0.0022 (SE 0.0015)

### 2026 weeks 1-6 test (models refit 2023-2025; d vs Sleeper on the same rows)

| market | baseline | rung | n | MAE | RMSE | log loss | Brier | d log loss vs Sleeper | SE | accepted on 2025 |
|---|---|---|---|---|---|---|---|---|---|---|
| tgt | frozen | 1 | 642 | 2.123 | 2.723 | 1.82654 | 0.14932 |  |  |  |
| tgt | frozen | R | 642 | 2.135 | 2.727 | 1.82946 | 0.14950 | +0.00292 | 0.00431 | False |
| tgt | frozen | V | 642 | 2.123 | 2.723 | 1.82629 | 0.14930 | -0.00026 | 0.00012 | False |
| tgt | frozen | D2 | 642 | 2.126 | 2.729 | 1.83043 | 0.14951 | +0.00388 | 0.00208 | False |
| tgt | frozen | D3 | 642 | 2.126 | 2.730 | 1.83150 | 0.14960 | +0.00495 | 0.00284 | False |
| tgt | frozen | D4 | 642 | 2.123 | 2.723 | 1.82691 | 0.14936 | +0.00037 | 0.00049 | False |
| tgt | frozen | D5 | 642 | 2.123 | 2.724 | 1.82710 | 0.14935 | +0.00055 | 0.00046 | False |
| tgt | frozen | D6 | 642 | 2.123 | 2.723 | 1.82654 | 0.14932 | +0.00000 | 0.00001 | False |
| tgt | frozen | D7 | 642 | 2.124 | 2.725 | 1.82893 | 0.14938 | +0.00239 | 0.00188 | False |
| tgt | revised | 1 | 1007 | 2.101 | 2.711 | 1.83607 | 0.14976 |  |  |  |
| tgt | revised | R | 1007 | 2.107 | 2.708 | 1.83445 | 0.14958 | -0.00162 | 0.00334 | False |
| tgt | revised | V | 1007 | 2.101 | 2.711 | 1.83583 | 0.14973 | -0.00024 | 0.00011 | False |
| tgt | revised | D2 | 1007 | 2.100 | 2.713 | 1.83639 | 0.14970 | +0.00033 | 0.00225 | False |
| tgt | revised | D3 | 1007 | 2.097 | 2.711 | 1.83528 | 0.14964 | -0.00078 | 0.00300 | False |
| tgt | revised | D4 | 1007 | 2.100 | 2.711 | 1.83583 | 0.14973 | -0.00024 | 0.00038 | False |
| tgt | revised | D5 | 1007 | 2.101 | 2.711 | 1.83598 | 0.14975 | -0.00008 | 0.00038 | False |
| tgt | revised | D6 | 1007 | 2.101 | 2.711 | 1.83606 | 0.14975 | -0.00001 | 0.00001 | False |
| tgt | revised | D7 | 1007 | 2.097 | 2.707 | 1.83375 | 0.14947 | -0.00232 | 0.00176 | False |
| rec | frozen | 1 | 642 | 1.609 | 2.072 | 2.77378 | 0.15027 |  |  |  |
| rec | frozen | R | 642 | 1.635 | 2.089 | 2.78998 | 0.15135 | +0.01619 | 0.01006 | False |
| rec | frozen | V | 642 | 1.611 | 2.072 | 2.77363 | 0.15025 | -0.00016 | 0.00122 | False |
| rec | frozen | D2 | 642 | 1.610 | 2.074 | 2.77668 | 0.15034 | +0.00290 | 0.00301 | False |
| rec | frozen | D3 | 642 | 1.610 | 2.075 | 2.77760 | 0.15040 | +0.00382 | 0.00357 | False |
| rec | frozen | D4 | 642 | 1.609 | 2.072 | 2.77440 | 0.15029 | +0.00062 | 0.00105 | False |
| rec | frozen | D5 | 642 | 1.609 | 2.073 | 2.77460 | 0.15031 | +0.00082 | 0.00042 | False |
| rec | frozen | D6 | 642 | 1.609 | 2.072 | 2.77378 | 0.15027 | -0.00000 | 0.00001 | False |
| rec | frozen | D7 | 642 | 1.613 | 2.075 | 2.77806 | 0.15047 | +0.00428 | 0.00260 | False |
| rec | revised | 1 | 1007 | 1.610 | 2.117 | 2.75906 | 0.15031 |  |  |  |
| rec | revised | R | 1007 | 1.630 | 2.125 | 2.76570 | 0.15072 | +0.00664 | 0.00743 | False |
| rec | revised | V | 1007 | 1.610 | 2.116 | 2.75834 | 0.15023 | -0.00072 | 0.00116 | False |
| rec | revised | D2 | 1007 | 1.611 | 2.118 | 2.75997 | 0.15034 | +0.00091 | 0.00274 | False |
| rec | revised | D3 | 1007 | 1.609 | 2.114 | 2.75741 | 0.15021 | -0.00165 | 0.00374 | False |
| rec | revised | D4 | 1007 | 1.609 | 2.116 | 2.75882 | 0.15028 | -0.00024 | 0.00087 | False |
| rec | revised | D5 | 1007 | 1.609 | 2.117 | 2.75922 | 0.15031 | +0.00016 | 0.00038 | False |
| rec | revised | D6 | 1007 | 1.610 | 2.117 | 2.75906 | 0.15031 | +0.00000 | 0.00000 | False |
| rec | revised | D7 | 1007 | 1.609 | 2.115 | 2.75811 | 0.15019 | -0.00095 | 0.00239 | False |
| rec_yd | frozen | 1 | 642 | 21.577 | 29.021 | 3.07154 | 0.14183 |  |  |  |
| rec_yd | frozen | R | 642 | 21.727 | 29.073 | 3.07386 | 0.14200 | +0.00233 | 0.00610 | False |
| rec_yd | frozen | V | 642 | 21.613 | 29.025 | 3.07068 | 0.14183 | -0.00085 | 0.00115 | False |
| rec_yd | frozen | D2 | 642 | 21.580 | 29.028 | 3.07251 | 0.14184 | +0.00097 | 0.00267 | False |
| rec_yd | frozen | D3 | 642 | 21.566 | 29.006 | 3.07112 | 0.14177 | -0.00041 | 0.00121 | False |
| rec_yd | frozen | D4 | 642 | 21.587 | 29.029 | 3.07234 | 0.14185 | +0.00081 | 0.00099 | False |
| rec_yd | frozen | D5 | 642 | 21.579 | 29.024 | 3.07158 | 0.14182 | +0.00004 | 0.00053 | False |
| rec_yd | frozen | D6 | 642 | 21.577 | 29.021 | 3.07153 | 0.14183 | -0.00001 | 0.00002 | False |
| rec_yd | frozen | D7 | 642 | 21.592 | 29.014 | 3.06966 | 0.14168 | -0.00188 | 0.00343 | False |
| rec_yd | revised | 1 | 1007 | 21.794 | 28.750 | 3.07546 | 0.14315 |  |  |  |
| rec_yd | revised | R | 1007 | 21.941 | 28.806 | 3.07778 | 0.14330 | +0.00232 | 0.00500 | False |
| rec_yd | revised | V | 1007 | 21.815 | 28.742 | 3.07415 | 0.14310 | -0.00131 | 0.00095 | False |
| rec_yd | revised | D2 | 1007 | 21.790 | 28.740 | 3.07607 | 0.14313 | +0.00061 | 0.00230 | False |
| rec_yd | revised | D3 | 1007 | 21.772 | 28.721 | 3.07417 | 0.14305 | -0.00129 | 0.00114 | False |
| rec_yd | revised | D4 | 1007 | 21.793 | 28.749 | 3.07519 | 0.14313 | -0.00026 | 0.00089 | False |
| rec_yd | revised | D5 | 1007 | 21.791 | 28.748 | 3.07478 | 0.14312 | -0.00068 | 0.00046 | False |
| rec_yd | revised | D6 | 1007 | 21.794 | 28.750 | 3.07546 | 0.14315 | -0.00000 | 0.00002 | False |
| rec_yd | revised | D7 | 1007 | 21.772 | 28.684 | 3.06958 | 0.14280 | -0.00588 | 0.00307 | False |
| car | frozen | 1 | 286 | 3.348 | 4.250 | 1.33449 | 0.10681 |  |  |  |
| car | frozen | R | 286 | 3.382 | 4.273 | 1.33940 | 0.10731 | +0.00491 | 0.00305 | False |
| car | frozen | V | 286 | 3.381 | 4.276 | 1.34276 | 0.10735 | +0.00827 | 0.00697 | False |
| car | frozen | D2 | 286 | 3.364 | 4.266 | 1.33522 | 0.10690 | +0.00073 | 0.00490 | False |
| car | frozen | D3 | 286 | 3.369 | 4.280 | 1.33635 | 0.10704 | +0.00186 | 0.00425 | False |
| car | frozen | D5 | 286 | 3.349 | 4.249 | 1.33425 | 0.10677 | -0.00024 | 0.00144 | False |
| car | frozen | D6 | 286 | 3.347 | 4.253 | 1.33447 | 0.10685 | -0.00002 | 0.00172 | False |
| car | frozen | D7 | 286 | 3.369 | 4.276 | 1.33689 | 0.10688 | +0.00240 | 0.00660 | False |
| car | revised | 1 | 464 | 3.139 | 4.038 | 1.25839 | 0.10013 |  |  |  |
| car | revised | R | 464 | 3.173 | 4.067 | 1.26175 | 0.10050 | +0.00336 | 0.00224 | False |
| car | revised | V | 464 | 3.163 | 4.065 | 1.26572 | 0.10072 | +0.00733 | 0.00487 | False |
| car | revised | D2 | 464 | 3.155 | 4.053 | 1.26066 | 0.10028 | +0.00226 | 0.00398 | False |
| car | revised | D3 | 464 | 3.148 | 4.046 | 1.25804 | 0.10007 | -0.00036 | 0.00303 | False |
| car | revised | D5 | 464 | 3.141 | 4.038 | 1.25835 | 0.10010 | -0.00004 | 0.00103 | False |
| car | revised | D6 | 464 | 3.133 | 4.033 | 1.25631 | 0.09996 | -0.00209 | 0.00149 | False |
| car | revised | D7 | 464 | 3.151 | 4.049 | 1.25940 | 0.10009 | +0.00101 | 0.00464 | False |
| rush_yd | frozen | 1 | 286 | 19.528 | 25.635 | 2.70924 | 0.12409 |  |  |  |
| rush_yd | frozen | R | 286 | 19.771 | 25.820 | 2.71804 | 0.12462 | +0.00880 | 0.00473 | False |
| rush_yd | frozen | V | 286 | 19.540 | 25.648 | 2.71040 | 0.12411 | +0.00115 | 0.00131 | False |
| rush_yd | frozen | D2 | 286 | 19.534 | 25.673 | 2.70899 | 0.12410 | -0.00025 | 0.00182 | False |
| rush_yd | frozen | D3 | 286 | 19.528 | 25.667 | 2.70897 | 0.12408 | -0.00027 | 0.00163 | False |
| rush_yd | frozen | D5 | 286 | 19.538 | 25.636 | 2.70893 | 0.12415 | -0.00031 | 0.00295 | False |
| rush_yd | frozen | D6 | 286 | 19.527 | 25.647 | 2.70800 | 0.12402 | -0.00124 | 0.00387 | False |
| rush_yd | frozen | D7 | 286 | 19.562 | 25.701 | 2.71181 | 0.12420 | +0.00257 | 0.00447 | False |
| rush_yd | revised | 1 | 464 | 19.679 | 25.879 | 2.71827 | 0.12505 |  |  |  |
| rush_yd | revised | R | 464 | 19.973 | 26.040 | 2.72493 | 0.12547 | +0.00666 | 0.00354 | False |
| rush_yd | revised | V | 464 | 19.701 | 25.892 | 2.71895 | 0.12506 | +0.00069 | 0.00093 | False |
| rush_yd | revised | D2 | 464 | 19.693 | 25.897 | 2.71805 | 0.12505 | -0.00022 | 0.00136 | False |
| rush_yd | revised | D3 | 464 | 19.689 | 25.893 | 2.71795 | 0.12503 | -0.00032 | 0.00123 | False |
| rush_yd | revised | D5 | 464 | 19.677 | 25.868 | 2.71756 | 0.12504 | -0.00071 | 0.00237 | False |
| rush_yd | revised | D6 | 464 | 19.664 | 25.873 | 2.71548 | 0.12493 | -0.00279 | 0.00360 | False |
| rush_yd | revised | D7 | 464 | 19.697 | 25.904 | 2.71743 | 0.12501 | -0.00084 | 0.00379 | False |
| patt | frozen | 1 | 114 | 7.686 | 10.806 | 2.20962 | 0.18523 |  |  |  |
| patt | frozen | R | 114 | 7.647 | 10.783 | 2.21116 | 0.18520 | +0.00154 | 0.00870 | False |
| patt | frozen | V | 114 | 7.703 | 10.742 | 2.20611 | 0.18471 | -0.00350 | 0.01665 | False |
| patt | frozen | D2 | 114 | 7.761 | 10.830 | 2.22021 | 0.18650 | +0.01060 | 0.01666 | False |
| patt | frozen | D3 | 114 | 7.793 | 10.815 | 2.22703 | 0.18717 | +0.01741 | 0.01697 | False |
| patt | frozen | D5 | 114 | 7.685 | 10.803 | 2.20890 | 0.18516 | -0.00072 | 0.00045 | False |
| patt | frozen | D6 | 114 | 7.686 | 10.806 | 2.20958 | 0.18522 | -0.00004 | 0.00004 | False |
| patt | frozen | D7 | 114 | 7.752 | 10.691 | 2.20097 | 0.18446 | -0.00865 | 0.02081 | False |
| patt | revised | 1 | 185 | 6.811 | 9.131 | 2.17184 | 0.18100 |  |  |  |
| patt | revised | R | 185 | 6.823 | 9.182 | 2.18522 | 0.18224 | +0.01339 | 0.00650 | False |
| patt | revised | V | 185 | 6.835 | 9.136 | 2.18020 | 0.18158 | +0.00836 | 0.01267 | False |
| patt | revised | D2 | 185 | 6.843 | 9.109 | 2.16740 | 0.18076 | -0.00444 | 0.01207 | False |
| patt | revised | D3 | 185 | 6.896 | 9.174 | 2.18245 | 0.18225 | +0.01062 | 0.01229 | False |
| patt | revised | D5 | 185 | 6.810 | 9.130 | 2.17145 | 0.18097 | -0.00038 | 0.00037 | False |
| patt | revised | D6 | 185 | 6.811 | 9.131 | 2.17184 | 0.18100 | +0.00000 | 0.00003 | False |
| patt | revised | D7 | 185 | 6.832 | 9.097 | 2.16117 | 0.18014 | -0.01067 | 0.01474 | False |
| pcmp | frozen | 1 | 91 | 5.489 | 7.376 | 1.61374 | 0.17964 |  |  |  |
| pcmp | frozen | R | 91 | 5.511 | 7.407 | 1.61672 | 0.18007 | +0.00299 | 0.00483 | False |
| pcmp | frozen | V | 91 | 5.489 | 7.376 | 1.61401 | 0.17967 | +0.00027 | 0.00032 | False |
| pcmp | frozen | D2 | 91 | 5.506 | 7.407 | 1.61871 | 0.18038 | +0.00498 | 0.01032 | False |
| pcmp | frozen | D3 | 91 | 5.507 | 7.361 | 1.62500 | 0.18100 | +0.01126 | 0.01253 | True |
| pcmp | frozen | D5 | 91 | 5.485 | 7.366 | 1.62059 | 0.18046 | +0.00685 | 0.01021 | False |
| pcmp | frozen | D6 | 91 | 5.489 | 7.348 | 1.61970 | 0.18035 | +0.00596 | 0.01065 | False |
| pcmp | frozen | D7 | 91 | 5.488 | 7.373 | 1.61404 | 0.17966 | +0.00031 | 0.00104 | False |
| pcmp | revised | 1 | 185 | 4.784 | 6.144 | 1.56798 | 0.17323 |  |  |  |
| pcmp | revised | R | 185 | 4.767 | 6.128 | 1.56294 | 0.17258 | -0.00504 | 0.00305 | False |
| pcmp | revised | V | 185 | 4.783 | 6.144 | 1.56818 | 0.17326 | +0.00021 | 0.00021 | False |
| pcmp | revised | D2 | 185 | 4.763 | 6.144 | 1.56827 | 0.17329 | +0.00029 | 0.00677 | False |
| pcmp | revised | D3 | 185 | 4.761 | 6.153 | 1.57357 | 0.17373 | +0.00559 | 0.00825 | True |
| pcmp | revised | D5 | 185 | 4.759 | 6.142 | 1.56920 | 0.17333 | +0.00122 | 0.00695 | False |
| pcmp | revised | D6 | 185 | 4.762 | 6.147 | 1.57139 | 0.17353 | +0.00341 | 0.00674 | False |
| pcmp | revised | D7 | 185 | 4.781 | 6.142 | 1.56801 | 0.17322 | +0.00003 | 0.00079 | False |
| pass_yd | frozen | 1 | 114 | 61.893 | 81.747 | 3.81224 | 0.18309 |  |  |  |
| pass_yd | frozen | R | 114 | 61.838 | 81.676 | 3.81054 | 0.18297 | -0.00170 | 0.00295 | False |
| pass_yd | frozen | V | 114 | 61.965 | 81.679 | 3.80450 | 0.18279 | -0.00774 | 0.00445 | False |
| pass_yd | frozen | D2 | 114 | 61.899 | 81.625 | 3.80721 | 0.18286 | -0.00503 | 0.00492 | False |
| pass_yd | frozen | D3 | 114 | 62.527 | 81.848 | 3.82528 | 0.18412 | +0.01304 | 0.02113 | False |
| pass_yd | frozen | D5 | 114 | 61.854 | 81.685 | 3.81237 | 0.18309 | +0.00013 | 0.00389 | False |
| pass_yd | frozen | D6 | 114 | 61.850 | 81.719 | 3.81123 | 0.18302 | -0.00101 | 0.00114 | False |
| pass_yd | frozen | D7 | 114 | 61.766 | 81.196 | 3.78809 | 0.18187 | -0.02415 | 0.01350 | False |
| pass_yd | revised | 1 | 185 | 58.990 | 73.839 | 3.79983 | 0.18289 |  |  |  |
| pass_yd | revised | R | 185 | 59.002 | 73.867 | 3.80062 | 0.18294 | +0.00079 | 0.00251 | False |
| pass_yd | revised | V | 185 | 58.952 | 73.701 | 3.79115 | 0.18248 | -0.00868 | 0.00405 | False |
| pass_yd | revised | D2 | 185 | 58.932 | 73.780 | 3.79633 | 0.18273 | -0.00350 | 0.00384 | False |
| pass_yd | revised | D3 | 185 | 59.013 | 73.936 | 3.80475 | 0.18325 | +0.00492 | 0.01697 | False |
| pass_yd | revised | D5 | 185 | 58.936 | 73.849 | 3.80072 | 0.18292 | +0.00089 | 0.00392 | False |
| pass_yd | revised | D6 | 185 | 58.977 | 73.846 | 3.80018 | 0.18290 | +0.00035 | 0.00105 | False |
| pass_yd | revised | D7 | 185 | 58.584 | 73.539 | 3.77895 | 0.18181 | -0.02088 | 0.01022 | False |
| ptd | frozen | 1 | 114 | 0.864 | 1.094 | 1.65121 | 0.18419 |  |  |  |
| ptd | frozen | R | 114 | 0.860 | 1.093 | 1.65574 | 0.18480 | +0.00453 | 0.00867 | False |
| ptd | frozen | V | 114 | 0.872 | 1.092 | 1.64456 | 0.18376 | -0.00665 | 0.00815 | True |
| ptd | frozen | D2 | 114 | 0.873 | 1.092 | 1.64426 | 0.18373 | -0.00696 | 0.00823 | False |
| ptd | frozen | D3 | 114 | 0.872 | 1.092 | 1.64450 | 0.18375 | -0.00671 | 0.00811 | False |
| ptd | frozen | D5 | 114 | 0.872 | 1.091 | 1.64366 | 0.18363 | -0.00755 | 0.00816 | False |
| ptd | frozen | D6 | 114 | 0.873 | 1.092 | 1.64438 | 0.18375 | -0.00683 | 0.00844 | False |
| ptd | frozen | D7 | 114 | 0.862 | 1.091 | 1.64651 | 0.18373 | -0.00470 | 0.00913 | False |
| ptd | revised | 1 | 185 | 0.863 | 1.093 | 1.64225 | 0.18241 |  |  |  |
| ptd | revised | R | 185 | 0.862 | 1.098 | 1.65387 | 0.18389 | +0.01161 | 0.00655 | False |
| ptd | revised | V | 185 | 0.867 | 1.096 | 1.64354 | 0.18268 | +0.00129 | 0.00676 | True |
| ptd | revised | D2 | 185 | 0.867 | 1.096 | 1.64349 | 0.18267 | +0.00124 | 0.00690 | False |
| ptd | revised | D3 | 185 | 0.867 | 1.096 | 1.64369 | 0.18271 | +0.00144 | 0.00675 | False |
| ptd | revised | D5 | 185 | 0.866 | 1.095 | 1.64179 | 0.18242 | -0.00047 | 0.00708 | False |
| ptd | revised | D6 | 185 | 0.867 | 1.096 | 1.64387 | 0.18270 | +0.00161 | 0.00710 | False |
| ptd | revised | D7 | 185 | 0.861 | 1.097 | 1.64749 | 0.18298 | +0.00524 | 0.00702 | False |
| int | frozen | 1 | 91 | 0.576 | 0.678 | 1.02113 | 0.17042 |  |  |  |
| int | frozen | R | 91 | 0.572 | 0.670 | 1.01856 | 0.17035 | -0.00257 | 0.01049 | False |
| int | frozen | V | 91 | 0.577 | 0.683 | 1.02925 | 0.17183 | +0.00812 | 0.00844 | False |
| int | frozen | D2 | 91 | 0.570 | 0.677 | 1.01718 | 0.16934 | -0.00395 | 0.00603 | False |
| int | frozen | D5 | 91 | 0.576 | 0.678 | 1.02114 | 0.17043 | +0.00001 | 0.00001 | False |
| int | frozen | D6 | 91 | 0.575 | 0.678 | 1.02068 | 0.17019 | -0.00045 | 0.00288 | False |
| int | frozen | D7 | 91 | 0.578 | 0.682 | 1.02568 | 0.17128 | +0.00456 | 0.00425 | False |
| int | revised | 1 | 185 | 0.660 | 0.779 | 1.14393 | 0.19488 |  |  |  |
| int | revised | R | 185 | 0.656 | 0.776 | 1.15157 | 0.19583 | +0.00764 | 0.00818 | False |
| int | revised | V | 185 | 0.657 | 0.782 | 1.15249 | 0.19589 | +0.00856 | 0.00660 | False |
| int | revised | D2 | 185 | 0.655 | 0.778 | 1.14172 | 0.19416 | -0.00222 | 0.00506 | False |
| int | revised | D5 | 185 | 0.660 | 0.779 | 1.14395 | 0.19488 | +0.00002 | 0.00001 | False |
| int | revised | D6 | 185 | 0.660 | 0.779 | 1.14299 | 0.19470 | -0.00094 | 0.00280 | False |
| int | revised | D7 | 185 | 0.662 | 0.783 | 1.15217 | 0.19619 | +0.00824 | 0.00402 | False |

Shipping walk (frozen rows): tgt: nothing accepted on 2025; rec: nothing accepted on 2025; rec_yd: nothing accepted on 2025; car: nothing accepted on 2025; rush_yd: nothing accepted on 2025; patt: nothing accepted on 2025; pcmp: D3 d +0.01126 (weight 0); pass_yd: nothing accepted on 2025; ptd: V d -0.00665 (ships); int: nothing accepted on 2025

DFS hook: {"ships": false, "team_totals_free": false}

### Kalshi, pre-kickoff (descriptive, R5)

| season | stat | n | hit rate | model LL | Sleeper LL | Kalshi LL | model Brier | Kalshi Brier |
|---|---|---|---|---|---|---|---|---|
| 2025 | all | 1442 | 0.427 | 0.5948 | 0.5948 | 0.5935 | 0.2038 | 0.2041 |
| 2025 | pass_yd | 464 | 0.425 | 0.5253 | 0.5253 | 0.5145 | 0.1746 | 0.1710 |
| 2025 | rec | 255 | 0.463 | 0.6041 | 0.6041 | 0.6011 | 0.2082 | 0.2063 |
| 2025 | rec_yd | 332 | 0.479 | 0.6674 | 0.6674 | 0.6779 | 0.2355 | 0.2401 |
| 2025 | rush_yd | 391 | 0.363 | 0.6094 | 0.6094 | 0.6107 | 0.2088 | 0.2113 |

- 2025 roi_model: 770 bets over 123 games, staked 356.32 $, profit +0.68 $, ROI +0.2% (95% -12.2% to +11.8%)

- 2025 roi_sleeper: 770 bets over 123 games, staked 356.32 $, profit +0.68 $, ROI +0.2% (95% -12.0% to +12.0%)
| 2026 | all | 1510 | 0.268 | 0.4563 | 0.4563 | 0.4322 | 0.1451 | 0.1382 |
| 2026 | pass_yd | 233 | 0.399 | 0.5011 | 0.5011 | 0.4739 | 0.1647 | 0.1542 |
| 2026 | rec | 357 | 0.359 | 0.5119 | 0.5119 | 0.5045 | 0.1677 | 0.1664 |
| 2026 | rec_yd | 256 | 0.234 | 0.4642 | 0.4642 | 0.4306 | 0.1417 | 0.1353 |
| 2026 | rush_yd | 284 | 0.261 | 0.5063 | 0.5063 | 0.4466 | 0.1621 | 0.1461 |
| 2026 | td | 380 | 0.132 | 0.3340 | 0.3340 | 0.3291 | 0.1013 | 0.0979 |

- 2026 roi_model: 1028 bets over 54 games, staked 499.74 $, profit -34.74 $, ROI -7.0% (95% -14.4% to +1.1%)

- 2026 roi_sleeper: 1028 bets over 54 games, staked 499.74 $, profit -34.74 $, ROI -7.0% (95% -14.6% to +1.2%)

<!-- RUSHCV-PREREG-START -->
## 9. Pre-registered: a line-dependent rushing-yard spread (written 2026-10-23, before any 2026 week-8 game)

This is a separate rule with its own hash (`research/data/nfl_matchup_model/rush_cv/prereg.json`).
It decides nothing about the rule above. It answers one question: does a
rushing-yard spread that depends on the line beat the shipped
single-CV-per-position spread on games nobody has seen?

**What was seen before writing it.** Three things:

- The review's calibration table (section 2a). It pools 2023-2025 revised
  rows and 2026 weeks 1-6 frozen rows, so neither 2025 nor 2026 weeks 1-6
  is held out for this question.
- The 2023-2024 decile CVs that the candidate below is fitted on (training
  data). Across bins they ran 0.48-3.12 for RB and 0.52-1.95 for QB. The
  fits were RB c 3.69 d -0.43 and QB c 3.64 d -0.48.
- A plumbing pass of the code on 2025 outcomes scrambled from 2023 rows, and
  on the 2026 week-6 and week-7 props logs with scrambled outcomes.

No 2026 week-8 game has been played: the first kicks off on Thursday
2026-10-29.

**Q1. The candidate.** For RB and QB, the positive part's coefficient of
variation is cv(L) = c_p x max(L, 1)^d_p, clamped to [0.25, 4.0]. L is the
yards mean the distribution is built at: Sleeper's rushing-yards line, since
no matchup term ships in rushing yards. Everything else is the shipped
model: the zero mass from the carries NegBin at Sleeper's carry line (alpha
by moments per position), the gamma family, and WR's single CV.

c_p and d_p come from the passing-yards decile template:

1. Take the fit rows of position p (Sleeper rushing-yards line > 0 and carry
   line > 0).
2. Cut ten bins at the deciles of the line, both ends inclusive.
3. In each bin with at least 10 positive outcomes, take the moment CV of the
   positive part: sqrt(sum((y - m_pos)^2) / sum(m_pos^2)) over y > 0, with
   m_pos = L / (1 - p0).
4. Fit log CV on log(mean line in the bin) by least squares.

The baseline, A, is the shipped single CV per position: the same moments
over the same rows, pooled.

**Q2. Data.**

- **2023-2025:** `research/data/nfl_matchup_model/rush_cv/rows_2023_2025.csv.gz`.
  These are the frames' revised Sleeper lines and Sleeper box scores; the
  validation and test frames give byte-identical extracts. The harness
  refits A and refuses unless it reproduces `validate.json`'s (2023-2024)
  and `coefs.json`'s (2023-2025) carry alphas and rushing CVs exactly.
- **2026 lines:** the sim-history props logs (`props/nfl_props_2026_wNN.json`).
  A player's frozen line is his Sleeper record there, used only when its
  content time is strictly before his game's kickoff.
- **2026 outcomes:** Sleeper's box (`stats_2026_<w>.json`), fetched at least
  48 hours after the week's last game and matched on (normalised name,
  team).
  - A projected player with no box line did not play and scores 0 yards.
  - A team missing from the box file drops out.
  - If more than 10% of the scored rows have no box line, the judgement
    refuses: that is a data problem, not an outcome.
- **Rows scored** (R3's priced universe): RB with 5+ projected carries, QB
  with 2+, and a positive yards line.

**Q3. The metric.** Per row, the threshold log loss summed over 10, 15, 25,
40, 50, 60, 75 and 100 yards: R3's thresholds plus 10+, which Kalshi lists.
Probabilities are clipped to [1e-6, 1 - 1e-6]. d is the mean over rows of
(candidate minus baseline), with its SE clustered by game (G / (G - 1)).

**Q4. The 2025 gate (fit 2023-2024).** Go on to the judgement only if d < -2
SE AND d < -0.0005 x 8 (R3's rule). 2025 is not blind for this question,
since the review's table pools it. The gate only stops a candidate that
does not hold even on the season after its fit. If it fails, nothing is
judged, the single CV stays and so does the slate note.

**Q5. The judgement (once).** Refit c_p and d_p on 2023-2025, the export's
seasons. The baseline is `coefs.json`'s committed spread; the harness
refuses if its own refit differs.

- **Window:** 2026 weeks 8-10. If those weeks hold fewer than 30 games with
  a scored row, the window extends one week at a time, up to week 12. The
  count comes from the frozen captures alone, before any outcome is read.
- **PASS** if d < 0 (R4's shipping rule).
- **Once:** it runs after the window's last game and writes
  `judge_lock.json`. A re-run needs `--force`, and the report must say so.
- **Reported beside the decision, deciding nothing:** d by position, and the
  calibration cells (RB lines 20-30, QB 10-20 and 20-30, RB 50+) at 10+,
  15+, 25+ and 40+.

**Q6. What a result does.** Nothing ships automatically.

- **PASS:** the line-dependent spread becomes eligible to ship as its own
  change, with production reading `cv_c` / `cv_d`, a guard, and the slate
  note removed.
- **FAIL:** the single CV stays, and so does the slate note.
- **Not addressed by the candidate:** the excess zero mass (2a).

**Q7. The code.** `research/nfl_matchup_rushcv.py`. It is self-contained:
its own numerics, checked against `research/nfl_matchup_fit.gammaq` (0.0
difference on a grid) and against production's `market_dist` (1.1e-15
over 600 rows x 8 thresholds). Its bytes are stored at registration under
`research/data/nfl_matchup_model/rush_cv/code/`, with their sha256 in
`rush_cv/prereg.json`. Every command refuses when the file, the extract or
this block no longer hash to what was registered. `kalshi_nfl.py`, which
supplies the name and team keys, is hashed too, and any drift is reported
in the outputs.

    python3 -m research.nfl_matchup_rushcv choose
    python3 -m research.nfl_matchup_rushcv judge --props nfl_props_2026_w08.json nfl_props_2026_w09.json \
        nfl_props_2026_w10.json --sleeper <dir with stats_2026_8..10.json>
<!-- RUSHCV-PREREG-END -->

### 9.1 The 2025 gate: FAILED (run 2026-10-23 15:10:13 UTC, after registration at 15:10:09)

`rush_cv/choose.json`. The harness first reproduced `validate.json`'s 2023-2024
carry alphas and rushing CVs exactly; it refuses otherwise. The candidate
fitted on 2023-2024 was RB c 3.686 d -0.432 and QB c 3.644 d -0.477. Its
CV falls from 1.84 (RB) and 1.69 (QB) at a 5-yard line to 0.92 / 0.79 at
25 and 0.56 / 0.45 at 80; the single CVs are 0.64 and 0.73.

| 2025 priced rows | n | games | d (line minus single) | SE | the rule |
|---|---|---|---|---|---|
| all | 1,327 | 272 | -0.0086 | 0.0137 | needs < -2 SE and < -0.004: **FAIL** |
| RB | 871 | | -0.0121 | 0.0169 | |
| QB | 456 | | -0.0021 | 0.0216 | |

Calibration on the same rows (deciding nothing), hit / single CV / line CV:

| cell | n | 10+ | 15+ | 25+ | 40+ |
|---|---|---|---|---|---|
| RB lines 20-30 | 150 | 0.693 / 0.856 / **0.722** | 0.580 / 0.715 / **0.595** | 0.380 / 0.439 / 0.397 | 0.200 / 0.177 / 0.211 |
| QB lines 10-20 | 198 | 0.465 / 0.581 / **0.489** | 0.348 / 0.397 / 0.358 | 0.192 / 0.169 / 0.194 | 0.051 / 0.043 / 0.077 |
| QB lines 20-30 | 117 | 0.701 / 0.790 / 0.763 | 0.590 / 0.653 / 0.631 | 0.385 / 0.413 / 0.407 | 0.231 / 0.188 / 0.196 |
| RB lines 50+ | 452 | 0.980 / 0.981 / 0.985 | 0.973 / 0.954 / 0.962 | 0.909 / 0.871 / 0.889 | 0.750 / 0.711 / 0.737 |

The candidate does what it was built for. On 2025, the RB 10+ on lines of
20-30 moves from 16 points high to 3, and the QB 10+ on lines of 10-20 from
12 to 2. But
those cells are a few hundred of 1,327 rows, and the summed threshold log
loss per row has an SE of 0.014, so the overall gain (0.0086 nats) is
0.6 SE. By the rule's letter it fails. The single CV stays, so does the
slate note, and the weeks 8-10 judgement does not run (`rush_cv/judge.json`
records the skip; no lock was written).

A test of the low-threshold cells alone, for example the log loss at 10+ and
15+ on lines under 30, would be a different question. It is not this rule,
and it would need its own block written before the weeks it is judged on.
