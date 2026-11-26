# The NFL deep weight: the registered read, frozen (2026-11-26)

**The owner's decision (2026-11-26).** The weight stays at 0.30 until the registered read. Before it reads,
its ambiguities are settled in writing:

- 200 rows = 100 unique games.
- Paired by game and clustered by NFL week.
- Evaluate both the registered comparison and the production blend (calibrated drive).
- An inconclusive result is insufficient evidence.
- Any replacement protocol uses fresh data.
- Nothing is revised after the fact.

**The owner's follow-up (2026-11-26, before the trigger).** The owner settled the two flagged rows:

- The inconclusive middle case returns the weight to **0**, consistent with "a null sends it to 0".
- The registered raw-drive comparison decides.
- The production blend is reported apart, against calibrated drive alone.
- The deep engine keeps running in shadow.
- The DFS engine is untouched.
- A new prospective evaluation is registered with a justified sample size and is not tested repeatedly.

Sections 3, 4 and 6 below are the settled text. The registration log (section 8) records the change.

This report is that rule in full. `research/nfl_deep_weight_read.py` is the same rule as code, pinned below
by sha256. `tests/guards_deep_rule.py` (section DR) holds the two together and pins each definition next to
the code that computes it.

## 1. What the weight is

`nfl_game_sim._DEEP_WEIGHT` = 0.30. The NFL card's headline is:

    p_home = 0.7 x calibrate("nfl", drive model's raw p_home) + 0.3 x deep engine's p_home

- **It was set by judgement and never earned.** The code comment says so. The only out-of-sample evidence
  is `nfl_deep_validation.md`: -0.0015 +/- 0.0013 log loss, measured against a points-differential
  stand-in because the drive model's Sleeper inputs were never archived.
- **The record logs each model raw.** Bucket `nfl` holds the drive model and the shadow bucket `nfl_deep`
  holds the deep engine. Neither grades the blend.
- **Out of scope:** the DFS engine's own 0.30 (`nfl_dfs_engine.DEEP_WEIGHT`). That weight passed its own
  pre-registered test (accuracy_gaps.md 13.1), and this read does not touch it.

## 2. The registered text (accuracy_gaps.md 13.2, as written 2026-10-29)

> **NFL deep weight 0.30.** Read at 200 graded `nfl_deep` shadow games (about week 13): the 0.30 blend
> against the drive sim alone on the same tickers, log loss clustered by week. ASYMMETRIC by design,
> because the weight was never earned (`nfl_deep_validation.md`: -0.0015 +/- 0.0013): if the blend does not
> beat the drive sim (point estimate), the weight goes to 0; if the deep engine beats the drive sim by > 2 SE
> it stays. Power: SE ~0.008 nats on 200 games, so a null read is the expected one, and the rule sends a null
> to 0.

The 2026-10-30 note beside it: the PC plays the deep figure at 5,000 games and the server at 600. The dice
cost the blend about w^2/2n nats a row, which is negligible, so the rule was left unchanged.

## 3. The ambiguities, and how each is settled

The sprint's engine audit (`research/sprint_2026_11_26/engines/report.md` 3.5) found the first three. The
rest came up while writing the read as code. Where the owner has not spoken, the setting is the one closest
to the registered words, and it is marked **default**.

| # | ambiguity | settled as | by |
|---|---|---|---|
| 1 | "200 games" counted the file's rows: each game is two mirror-image rows, so 200 rows are 100 games. | **100 unique games.** | owner |
| 2 | **The middle case.** The blend is ahead on its point estimate, but the deep engine is not ahead by 2 SE. The text gives no outcome for it. This is where the interim record sits. | **INCONCLUSIVE, and the weight goes to 0.** This is the owner's call (2026-11-26), consistent with the original "a null sends it to 0". The first draft of this report read it as "0.30 stays, not earned"; the owner rejected that reading because keeping 0.30 after seeing the interim direction would have moved the acceptance criterion toward the deep engine. | owner |
| 3 | The SE. "~0.008 on 200 games" treated rows as independent. | Per-GAME paired differences, clustered by NFL week (CR1: K/(K-1) x the sum over weeks of the squared within-week residual sum, divided by G^2). Expected sizes are in section 5. | owner |
| 4 | Which 100 games. | **The first 100 graded games by kickoff.** The read waits while any game with a deep row that kicked off at or before the 100th is unsettled, so grading order cannot choose the games. Rows count only where BOTH models logged them; a game with one paired row is a game. Voided markets (graded 2) neither count nor block. | default |
| 5 | Which figures. | **Each model's FIRST logged figure** (first write wins per ticker and model), as logged. Nothing is re-simulated. Rows logged more than an hour apart by the two models (different builds) are counted and reported (`split_build_games`), never dropped. | default |
| 6 | "The production blend (calibrated drive)" for past games. The calibrated figure was never stored. | **Rebuilt from the calibrator in force when the drive row was logged:** the newest fit made at or before that moment, taken from the data branch's committed snapshots of the shared calibration file (`errors/calib-params-latest.json`, 67 versions since 2026-10-29). A fit made later never applies, since it was made on later games, this one's own included. Before the first snapshot (2026-10-29 15:10 UTC) the calibrator is the identity, which that snapshot itself chose at 146 rows. Facts: the NFL calibrator was the identity until 2026-11-09, then a sharpening temperature of 0.846 falling to 0.814. A PC board built between snapshots may have used a fit up to a few hours fresher; adjacent snapshots differ by at most 0.02 in t. | default |
| 7 | "Evaluate both": which comparison decides? | **The registered comparison decides**: the 0.30 blend on the RAW drive figure against the raw drive figure. The production comparison (the 0.30 blend on the CALIBRATED drive figure against the calibrated drive figure alone, the card's headline) is reported beside it as the deep engine's incremental contribution in production (`incremental_contribution_of_deep`). It never decides. `production_agrees` says whether its label matches. | owner |
| 8 | A tie. | It settles both moneylines NO. A game's figure is the mean over its paired rows, with no special case. | default |
| 9 | "Week". | The Tuesday-to-Monday window in US Eastern time holding the kickoff. Thanksgiving's Thursday and the following Monday night share a week. | default |
| 10 | Log-loss clip. | 0.001-0.999, the record's own (`predlog.vs_market`), so the read's figures match the record's. | default |
| 11 | "By > 2 SE". | One-sided: the deep engine's mean per-game gain over the drive sim must exceed 2 x its clustered SE. | default |
| 12 | Kalshi. | Reported on the same games as context. It plays no part in the decision. | default |

## 4. The decision

The registered comparison is computed on the 100 games, using each game's mean log loss over its paired rows:

- `blend_vs_drive` is the mean of LL(drive) - LL(0.7 drive + 0.3 deep), with its clustered SE.
- `deep_vs_drive` is the mean of LL(drive) - LL(deep), with its clustered SE.

| label | condition, checked in this order | the weight from then on |
|---|---|---|
| `WEIGHT_TO_ZERO` | `blend_vs_drive` mean <= 0 | **0** |
| `STAYS_EARNED` | `deep_vs_drive` mean > 2 x its SE | **0.30, earned** |
| `INCONCLUSIVE_TO_ZERO` | anything else | **0** |

On a 0, the change is one commit. The deep figure stays on the card and keeps logging to the shadow bucket
`nfl_deep`, and the DFS engine's own 0.30 (`nfl_dfs_engine.DEEP_WEIGHT`) is not touched. Only the prospective
test (section 6) can bring the headline weight back.

The first two cannot both hold. Log loss is convex, so on every game the blend's gain over the drive sim is
at least 0.30 x the deep engine's (guard DR6). A deep engine ahead therefore implies a blend ahead.

## 5. Power, stated before the read

- **Interim, measured once by the audit:** 57 games and 4 weekly clusters, deep minus drive SE about 0.045.
- **Projected at 100 games over about 9 weeks:**
  - `deep_vs_drive` SE about 0.03-0.045, so its 2 SE bar is about 0.06-0.09 nats a game;
  - `blend_vs_drive` SE about 0.01-0.015.
  - A synthetic check (`tests/guards_deep_rule.py`'s generator, 100 games, 7 weeks) gave SEs of 0.045 and
    0.016.
- **For scale:** the whole walk-forward advantage measured for the deep engine was 0.0015.

**`STAYS_EARNED` is therefore not reachable for any plausible effect, and the expected outcome of this read is
a weight of 0.** That is what was registered, it is stated here before the data, and it is why section 6
exists.

## 6. The prospective test (registered 2026-11-26, fresh data only)

This is the only route back to a non-zero headline weight. It is coded in `prospective()` in the read script
and pinned by the same hash.

**1. Data.** Fresh games only: every game that kicks off after the 100-game read's last game. None of the
100, and no game before them, is used. A game is one unique event; its two mirror rows count once.

**2. One candidate, fixed now: reinstate w = 0.30.** There is no grid, so nothing is chosen on the games it
is tested on.

**3. The primary statistic is the production comparison.** It is the 0.30 blend on the calibrated drive
figure against the calibrated drive figure alone, per game, clustered by NFL week, one-sided. This is the
deep engine's incremental contribution to the headline, which is the quantity the weight changes. The raw
comparison and the deep engine alone against calibrated drive are reported at each look. They never decide.

**4. Why 0.015 nats a game is the effect designed for.** Two arguments, stated before any fresh data exists.

- **Horizon.** The engines change between seasons: the registered sack/fourth-down refit, and the 2027 table
  refits. A test running longer than about a season and a half no longer measures one engine. At the
  planning SD, the games needed for 80% power scale with 1/effect^2. These figures use the t-adjusted design
  of point 6, at about 280 deep-logged games a season:

  | smallest effect worth detecting (nats a game) | unique games for 80% power | seasons |
  |---|---|---|
  | 0.020 | about 220 | 0.8 |
  | **0.015** | **390** | **1.4** |
  | 0.010 | about 880 | 3.1 |
  | 0.008 | about 1,370 | 4.9 |

  0.015 is the smallest effect a test can resolve inside one engine generation. Anything smaller would be
  judged on engines that no longer exist by the time it reads.
- **What the weight is for.** On the audit's 57 games, the drive model trails Kalshi by 0.044 nats and
  DraftKings by 0.036. A 0.015 gain closes about a third of the gap to the market. A 0.008 gain, about the
  interim point estimate, would leave the headline 0.036 behind Kalshi, so it would still be the weaker
  forecaster on every card.

This is a design choice balancing sensitivity against horizon, not a law. A smaller effect can be designed
for, at the cost in the table, by a registration made before its data exist.

**5. Planning SD 0.10 nats a game.** It is the larger of the two estimates on hand:

- the walk-forward's 0.30-blend SE (0.0013 over 512 games) implies about 0.03;
- the audit's live figure (deep minus drive SE 0.045 over 57 games and 4 weeks) implies about 0.34 for the
  engine alone, so about 0.10 for a 0.30 blend.

Using the larger is conservative, at the cost of a longer test if the truth is nearer 0.03.

**6. The design: looks at 130, 260 and 390 fresh games.**

- **Boundaries.** O'Brien-Fleming for three looks (z 3.471 / 2.454 / 2.004), each converted to Student t on
  (week clusters at that look - 1) degrees of freedom: about 5.59 / 2.73 / 2.11 at 9 / 18 / 26 weeks.
- **Why the t conversion.** The week-clustered SE runs small with 8-26 clusters. With plain z boundaries the
  simulated false-positive rate was 3.7%, not 2.5%. With the conversion it is 2.4-2.6% at every weekly
  dependence tried. The conversion costs power, which is why the looks are at 130 / 260 / 390 and not
  120 / 240 / 360. With a known SE, 348 games would do.
- **Futility.** A non-binding stop at the second look when the mean gain is not above 0. It can only end the
  test early with the weight at 0.
- **Before a look the script prints counts only.** Every look's statistic is kept in its output.
- **Simulated with the script's own estimator** (20,000 runs, 15 games a week):

  | weekly dependence (intra-week correlation rho) | false positives | power at 0.008 | at 0.010 | at 0.015 | at 0.020 |
  |---|---|---|---|---|---|
  | 0 (games independent) | 2.4% | 33% | 47% | **80%** | 97% |
  | 0.05 | 2.6% | 21% | 30% | 58% | 82% |
  | 0.10 | 2.6% | 17% | 23% | 45% | 68% |

- **Reading the dependence row.** The false-positive rate holds at every rho; power is what weekly dependence
  costs. The only evidence on rho is the audit's four weekly windows. Their spread of drive-minus-deep means
  (SD about 0.09) is about what independent games at a per-game SD of about 0.34 would give (0.34/sqrt(14)),
  which is consistent with rho near 0. Four windows cannot rule out 0.05.

**7. The schedule.** About 280 deep-logged games a season, and about 90 fresh games left in 2026 including the
playoffs:

- 130 fresh games lands around 2027 week 3;
- 260 around 2027 week 12;
- 390 around the 2027 playoffs.

Each look is read once, when every earlier-kicked game is settled.

**8. Decisions, and what they do and do not say.**

- `REINSTATE_030`: a boundary is crossed. The weight returns to 0.30. The improvement is **established**.
- `STOPPED_FUTILITY_STAYS_ZERO` (the second look) or `STAYS_ZERO` (the last): the weight stays 0. The
  improvement is **NOT ESTABLISHED**, which is not evidence that there is none.
  - The output carries the one-sided 95% upper bound on the gain: the largest improvement the data still
    cannot exclude.
  - Only when that bound is under **0.005 nats a game** (registered now as "negligible") does it say "no
    meaningful improvement".
  - The 100-game read's 0 is worded the same way.
- `CONTINUE`: otherwise.

**9. Engine changes during the window** (the registered refit, 2027's table refits): the read reports the
games before and after each one, as description only. The decision uses all of them, because the weight is
a property of the engines as they run.

**10. Read once per look.** A new question is a new registration on data that does not yet exist.

## 7. How to run it

    git fetch origin sim-history
    python research/nfl_deep_weight_read.py          # counts until the trigger; then the full read
    python research/nfl_deep_weight_read.py --write  # after the trigger: research/data/nfl_deep_weight_read.json

**The data.** The error-log workflow pulls `/api/predlog/paired?owner=nfl&shadow=nfl_deep,nfl_book` to
`errors/nfl-deep-pairs-latest.json`: every drive row that a shadow model also logged, ticker by ticker. It
was added with this report, because the read could not be done from the data branch (the audit's finding
3.5). The calibration history is read from the same branch's git log.

**The DraftKings figure, traced (2026-11-26).** The audit quoted DraftKings on the same 57 games as 0.5243
log loss / 0.1739 Brier (`research/sprint_2026_11_26/engines/report.md` 3.2). It is the shadow bucket
`nfl_book`'s whole graded record at data-branch snapshot `3a86b72` (`errors/sharp-latest.json`: 114 rows,
0.5243 / 0.1739, Kalshi 0.5162 on them).

From the per-ticker export it reproduces exactly, 0.5243 / 0.1739 on 114 graded rows, and the matched sample
holds:

- every graded `nfl_book` row has an `nfl_deep` row (57 unique games);
- those 114 tickers are exactly the 114 deep rows logged after the audit's base snapshot (2026-10-29 15:23
  UTC), the window its drive and deep figures came from.

Only DraftKings' and Kalshi's figures were computed for this check.

## 8. Registration log

- **2026-11-26.** Registered. read script sha256: `f076b2d54f3dfbce3df11d1bccf16b47cd8af949ea0f86922831e4234311a3e8`
  - Disclosed: the sprint's engine audit looked once at interim aggregates (57 paired games, weeks 9-12;
    deep ahead in 3 of 4 weeks, +0.027 +/- 0.045). Every setting in section 3 marked default was chosen to
    follow the registered words, not that direction.
  - The paired export did not exist before this commit, so no per-game figure has been seen.
  - The synthetic data in the guards is generated, not drawn from the record.
- **2026-11-26, later (before the trigger: 84 of 100 games graded, 15 pending, counts only).** The owner's
  settlement of rows 2 and 7, and the prospective test. read script sha256: `6f1da07c0b4d0dc24a89379ca1dda86e8a9242cd17976e54ef0e661aa175e6ca`
  - `decide()` sends the middle case to 0 (`INCONCLUSIVE_TO_ZERO`, `WEIGHT_AFTER`).
  - The production comparison is reported as `incremental_contribution_of_deep`.
  - DraftKings' line (shadow `nfl_book`) is reported on the same games as context.
  - `prospective()` implements section 6. The t conversion and the 130/260/390 looks replaced a first draft
    (120/240/360 on z boundaries) the same day, after its own simulation showed 3.7% false positives.
  - Every 0 carries "not established" and the upper bound.
  - No per-game deep or drive figure was looked at to make any of these changes. The only figures read were
    DraftKings' and Kalshi's on the `nfl_book` rows, to trace the audit's 0.5243 (section 7).
- Any later edit to the read script: a dated line here giving the reason and the new hash, BEFORE the
  trigger. After the trigger: none.
