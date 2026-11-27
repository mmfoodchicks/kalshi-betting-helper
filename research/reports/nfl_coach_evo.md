# Does NFL coaching behaviour drift? A walk-forward over the coaching engine's tables

2026-11-27. The owner asked, after reading R5: "Investigate whether the trailing-quarterback-pull discrepancy
represents broader changes in NFL coaching behavior over time. Compare full-history training, recent three-season
and five-season windows, exponential recency weighting, and hierarchical models that adapt to changing league
tendencies. Use historical walk-forward development and prospective validation. Do not hand-tune the problematic
substitution situation."

- **The rule:** `research/data/nfl_coach/evo_rule.json`, committed alone (ae6e018) before any score was
  computed, with its sha256 pinned (guard EVO-1).
- **The study:** `research/nfl_coach_evo.py`, writing `research/data/nfl_coach/evo.json`.
- **The data:** nflverse 2016 to 2026 week 12 (CC BY 4.0). R1's seal holds; EVO-7 checks this.
- **Nothing is built into R1-R5;** EVO-8 checks this.

## A. The design (as the rule fixed it)

- **The folds:** each season S from 2019 to 2025, plus 2026 weeks 1-12. Each trains on 2016..S-1 and scores S.
- **The tables:**

  | table | what is predicted | stands for |
  |---|---|---|
  | T1 | pass or run, by quarter x margin x down x distance x zone | R1's call table |
  | T2 | no-huddle, by quarter x margin x the last 2:00 | R1's tempo |
  | T3 | a 35 s+ runoff between consecutive snaps | R1's clock by phase |
  | T4 | a target going to a running back | R2's who-gets-the-ball |
  | T5 | the starter's pull at each drive, in R3's s6 logistic form | R3/R5's hazard |

  R4's shifts are left out: about 70 pulls a season cannot score a one-season fold.
- **The candidates:**
  - **full:** every training season at equal weight.
  - **last3 and last5:** the last three or five seasons.
  - **exp:** exponential recency weighting. Its half-life h comes from {0.5, ..., 16}, chosen per fold by a
    nested walk-forward over the training seasons only.
  - **rw:** hierarchical. It keeps the full-history shape, plus a league tendency: a logit offset common to
    every cell, following a season-level random walk. The innovation variance is fitted by marginal likelihood,
    and the forecast is the Kalman-filtered level after S-1.
- **The score:** held-out log loss per game against full, with SEs clustered by game, all folds pooled.
- **The decision:**
  - **A table drifts** when a candidate beats full by more than 2 SE and is positive in at least 5 of the 8
    folds.
  - **A scheme** (one candidate for every table) needs its summed gain above 2 SE and no table below -1 SE.

## B. Results

Gains are over full, in nats a game (positive = better), with the number of the 8 folds positive:

| table | last3 | last5 | exp | rw | drifts? | fitted half-life |
|---|---|---|---|---|---|---|
| T1 call | -0.521 (z -28.8) | -0.125 (-13.8) | -0.045 (-22.4) | +0.002 +/- 0.005 (0.4), 5/8 | **no** | inf |
| T2 no-huddle | -0.081 (-4.0) | -0.044 (-4.8) | -0.049 (-1.9) | -0.052 +/- 0.031 (-1.7), 3/8 | **no** | inf |
| T3 clock runoff | +0.131 (11.0), 6/8 | +0.081 (12.8), 5/8 | +0.101 (9.5), 6/8 | **+0.169 +/- 0.017 (9.8), 6/8** | **yes** | 1 season |
| T4 RB target | -0.016 (-2.7) | +0.001 (0.2) | +0.002 (0.9) | **+0.022 +/- 0.006 (3.9), 6/8** | **yes** | 4 seasons |
| T5 pull hazard | +0.005 (1.7) | +0.004 (2.3), 4/8 | +0.004 (2.0), 7/8 | **+0.0056 +/- 0.0022 (2.5), 5/8** | **yes** | 1 season |

**Pooled over the five tables:**

- **rw:** +0.147 +/- 0.034 a game (z 4.4), but its no-huddle loss (z -1.7) breaks the -1 SE veto.
- **exp:** +0.013 (z 0.5).
- **last3 and last5:** -0.48 and -0.08.

By the rule, **no single scheme qualifies, and full history stays.**

### What drifts, and how

- **The clock (T3) drifts fast and in one direction.** The league offset of the 35 s+ runoff rose every season
  but two: from -0.16 (2016) to +0.24 (2025), where the per-season sampling SD is 0.013. Offences take more clock
  between snaps than they did. The best fixed half-life is one season, and every recency candidate wins.
- **The backs' target share (T4) drifts slowly.** It was high in 2017-19 (+0.07 to +0.14), near 0 in
  2020-23, and falling in 2024-25 (-0.13, -0.09). Half-life 4. Only the random walk wins.
- **The pull hazard (T5) drifts at the league level.** The offset was -0.30 to -0.38 in 2017-2019 and +0.16 to
  +0.29 in 2022-2025, against a sampling SD of 0.12. That is about +0.55 in logit, or roughly 1.7x the pull odds
  everywhere. Half-life 1. The random walk wins, narrowly (z 2.5, 5 of 8).
- **The call (T1) does not drift.** Every window shorter than full loses; the cells are fine-grained and need
  all the data. The random walk's league offset moves about +/-0.05 and buys nothing.
- **No-huddle (T2) moves but does not drift.** The league offset swings +/-0.27 a season, against a sampling SD
  of 0.02, but it reverts rather than walking, so every adaptive forecast is worse than the long-run mean.

### The trailing pull

The owner's question was whether R5's corner is part of a broader change. It is, partly, and only partly. Pulls
did rise league-wide from 2022, and the clock and the backs' targets drifted too, so coaching behaviour does
change. But under the walk-forward, the corner's expected pulls for 2022-2026 against the real 47 were:

| full | last3 | last5 | exp | rw |
|---|---|---|---|---|
| 14.3 | 19.5 | 16.7 | 18.2 | 17.7 |

The league-wide level closes a tenth to a sixth of the gap between full history and the real count (3.4-5.2
of 32.7 pulls). The rest is
specific to trailing teams protecting their quarterback in decided games. As the owner asked, it is not tuned
here; R5's registered secondary is what decides whether it persists.

## C. Prospective validation and R6 (the owner's call)

**No candidate won by the rule, so nothing becomes R6's scheme automatically.** The evidence does point to a
per-table scheme: random-walk training for the clock, the backs' targets and the pull, with full history for the
call and tempo tables. Because that scheme was read off these walk-forward results, its test must be
prospective, on games no fold has seen.

**The owner's call:**

1. **The 2028 regular season (fresh).** Or 2027, sharing games with R2-H2, R4, C4, C3-N and R5's amendment, with
   the dependence stated (`research/reports/nfl_coach_family_2027.md`).
2. **Whether R6 should be built at all.** The pooled gain is real (+0.15 nats a game). Almost all of it is the
   clock table, which R1 plays with a pinned engine, so an R6 would be a new engine version.

Until the owner decides, nothing is registered.

## D. Files and guards

| file | what |
|---|---|
| `research/data/nfl_coach/evo_rule.json` | the rule (ae6e018, before any score) |
| `research/nfl_coach_evo.py` | the walk-forward |
| `research/data/nfl_coach/evo.json` | every table x candidate: pooled gain, SE, folds, the chosen h per fold, the random walk's offsets and tau, the half-life profile, the corner |
| `tests/guards_auto_nfl_coach_evo.py` | 9 guards, section EVO (AUTO) |
