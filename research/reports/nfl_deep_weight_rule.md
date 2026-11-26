# The NFL deep weight: the registered read, frozen (2026-11-26)

**The owner's decision (2026-11-26).** The weight stays at 0.30 until the registered read. Before it reads,
its ambiguities are settled in writing:

- 200 rows = 100 unique games.
- Paired by game and clustered by NFL week.
- Evaluate both the registered comparison and the production blend (calibrated drive).
- An inconclusive result is insufficient evidence.
- Any replacement protocol uses fresh data.
- Nothing is revised after the fact.

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
| 2 | **The middle case.** The blend is ahead on its point estimate, but the deep engine is not ahead by 2 SE. The text gives no outcome for it. CLAUDE.md's gloss ("a null sends it to 0") would send it to 0. This is where the interim record sits. | **INCONCLUSIVE: 0.30 stays frozen, recorded as not earned.** The owner said "inconclusive means insufficient evidence" and "do not retroactively revise", so the text's two clauses stand as written and the owner's sentence fills the gap: insufficient evidence changes nothing. **If the owner meant the other reading (inconclusive sends it to 0), say so before the 100th game is graded.** It is one line in `decide()`. | owner; reading flagged |
| 3 | The SE. "~0.008 on 200 games" treated rows as independent. | Per-GAME paired differences, clustered by NFL week (CR1: K/(K-1) x the sum over weeks of the squared within-week residual sum, divided by G^2). Expected sizes are in section 5. | owner |
| 4 | Which 100 games. | **The first 100 graded games by kickoff.** The read waits while any game with a deep row that kicked off at or before the 100th is unsettled, so grading order cannot choose the games. Rows count only where BOTH models logged them; a game with one paired row is a game. Voided markets (graded 2) neither count nor block. | default |
| 5 | Which figures. | **Each model's FIRST logged figure** (first write wins per ticker and model), as logged. Nothing is re-simulated. Rows logged more than an hour apart by the two models (different builds) are counted and reported (`split_build_games`), never dropped. | default |
| 6 | "The production blend (calibrated drive)" for past games. The calibrated figure was never stored. | **Rebuilt from the calibrator in force when the drive row was logged:** the newest fit made at or before that moment, taken from the data branch's committed snapshots of the shared calibration file (`errors/calib-params-latest.json`, 67 versions since 2026-10-29). A fit made later never applies, since it was made on later games, this one's own included. Before the first snapshot (2026-10-29 15:10 UTC) the calibrator is the identity, which that snapshot itself chose at 146 rows. Facts: the NFL calibrator was the identity until 2026-11-09, then a sharpening temperature of 0.846 falling to 0.814. A PC board built between snapshots may have used a fit up to a few hours fresher; adjacent snapshots differ by at most 0.02 in t. | default |
| 7 | "Evaluate both": which comparison decides? | **The registered comparison decides** (the 0.30 blend on the RAW drive figure against the raw drive figure): that is the text, and making another comparison decide now would be a revision after the fact. The production comparison (the 0.30 blend on the CALIBRATED drive figure against the calibrated drive figure, which is the card's headline) is computed with the same clauses and reported beside it. If its label differs, the read says so (`production_agrees: false`) for the owner to see. The weight still follows the registered label. | default; flagged |
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
| `WEIGHT_TO_ZERO` | `blend_vs_drive` mean <= 0 | **0**, in one commit. The deep figure stays on the card and keeps logging to `nfl_deep`. |
| `STAYS_EARNED` | `deep_vs_drive` mean > 2 x its SE | **0.30, earned.** |
| `INCONCLUSIVE_STAYS_FROZEN` | anything else | **0.30, not earned.** Only the replacement protocol (section 6) moves it. |

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

**`STAYS_EARNED` is therefore not reachable for any plausible effect.** In practice the read decides between
0 (the blend's point estimate not ahead) and frozen 0.30 (ahead). Under no real effect the sign of that point
estimate is close to a coin toss. That is what was registered, it is stated here before the data, and it is
why section 6 exists.

## 6. The replacement protocol (fresh data only)

The 100-game read settles the weight for the rest of 2026. The only route to another value, or back from 0,
is this protocol, written now:

1. **Fresh data:** games that kick off after the read's 100th game. None of the 100, and no game before them,
   may be used.
2. **Trigger:** 200 fresh unique games, pooled across the season break (about 90 remain in 2026 including the
   playoffs, so about 2027 week 7). The read waits while any earlier-kicked game is unsettled. Before it reads
   it prints counts only.
3. **Candidates, fixed now:** w in {0, 0.15, 0.30, 0.50}. No other value.
4. **Decision:** the candidate with the lowest mean per-game log loss replaces the incumbent (whatever
   section 4 left) only if it beats the incumbent by **> 2.5 SE** (paired per game, clustered by week) on
   BOTH the raw and the calibrated-drive comparisons. 2.5 rather than 2 because the best of three
   challengers is chosen on the same games. Otherwise the incumbent stays.
5. **Engine changes during the window** (the registered 2026-11-06 sack/fourth-down refit, 2027's table
   refits): the read reports the games before and after each one separately, as description only. The
   decision uses all of them, because the weight is a property of the engines as they run.
6. Read once. A new question is a new registration on data that does not yet exist.

## 7. How to run it

    git fetch origin sim-history
    python research/nfl_deep_weight_read.py          # counts until the trigger; then the full read
    python research/nfl_deep_weight_read.py --write  # after the trigger: research/data/nfl_deep_weight_read.json

**The data.** The error-log workflow pulls `/api/predlog/paired?owner=nfl&shadow=nfl_deep,nfl_book` to
`errors/nfl-deep-pairs-latest.json`: every drive row that a shadow model also logged, ticker by ticker. It
was added with this report, because the read could not be done from the data branch (the audit's finding
3.5). The calibration history is read from the same branch's git log.

## 8. Registration log

- **2026-11-26.** Registered. read script sha256: `f076b2d54f3dfbce3df11d1bccf16b47cd8af949ea0f86922831e4234311a3e8`
  - Disclosed: the sprint's engine audit looked once at interim aggregates (57 paired games, weeks 9-12;
    deep ahead in 3 of 4 weeks, +0.027 +/- 0.045). Every setting in section 3 marked default was chosen to
    follow the registered words, not that direction.
  - The paired export did not exist before this commit, so no per-game figure has been seen.
  - The synthetic data in the guards is generated, not drawn from the record.
- Any later edit to the read script: a dated line here giving the reason and the new hash, BEFORE the
  trigger. After the trigger: none.
