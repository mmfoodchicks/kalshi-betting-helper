# C3: the season simulators against the analytic resolution (read 2026-11-27)

The registered read C3 from `accuracy_gaps.md` section 13.2. The harness is `research/c3_season_replay.py`
and its inputs are `research/data/c3_season/inputs.json`. Both were committed at 47973e7, before the replay
or the scoring ran. The per-week projections are in `research/data/c3_season/proj.json`, the scored result is
`research/data/c3_season_replay.json`, and the guards are in `tests/guards_auto_c3.py` (section AUTO).

## Verdict

| sport | 2024: win-total MAE | 2024: playoff log loss | 2025: win-total MAE | 2025: playoff log loss | verdict |
|---|---|---|---|---|---|
| NFL (`nfl_season.run_season`) | +0.016 +/- 0.014 (1.1 SE) | -0.012 +/- 0.011 (-1.0 SE) | +0.006 +/- 0.013 (0.5 SE) | **-0.027 +/- 0.011 (-2.4 SE)** | **NOT ESTABLISHED** |
| College (`cfb.run_season`) | +0.005 +/- 0.008 (0.6 SE) | -0.007 +/- 0.004 (-1.6 SE) | **+0.017 +/- 0.007 (2.4 SE)** | -0.003 +/- 0.002 (-1.5 SE) | **NOT ESTABLISHED** |

How to read the table:
- Each figure is the paired difference engine minus analytic, per team per week. A positive figure means the
  analytic resolution did better.
- The SE is clustered by team.
- The rule needs the analytic resolution to win both scores by more than 2 SE, in both seasons. Settlement S9
  in the harness reads "wins" across both scores the rule names.

Neither sim passes, and nothing changes in production. Both season sims keep resolving games with the drive
engine.

The improvement from the analytic resolution is **not established**. Its 95% bounds:
- **NFL win totals:** -0.011 to +0.043 wins per team-week in 2024, and -0.019 to +0.031 in 2025.
- **NFL playoff and division log loss:** -0.034 to +0.011 in 2024, and -0.049 to -0.005 in 2025.
- **College win totals:** -0.011 to +0.020 in 2024, and +0.003 to +0.032 in 2025.
- **College CFP and title log loss:** -0.015 to +0.001 in 2024, and -0.008 to +0.001 in 2025.

On the win totals, all four point estimates favour the analytic resolution, and one (college 2025) clears 2
SE. On the playoff scores, all four point estimates favour the engine, and one (NFL 2025) is 2.4 SE in the
engine's favour. The rule asks for both scores and gets neither in any season.

## What was replayed

The settlements S1-S10 are at the top of the harness. They were written and committed before any score
existed.

- **Data.** Only ESPN data was used, from the endpoints the production sims already read:
  - the college FBS scoreboards for weeks 1-15;
  - the standings trees, for the prior and the membership;
  - the NFL scoreboards for weeks 1-18;
  - the NFL standings' `playoffSeed`;
  - the real CFP games;
  - the committee's own ranking (core `rankings/21`, weeks 11-16 in both seasons).

  Some sources the rule allows were not available:
  - Kalshi's win-total ladders have no 2024 or 2025 history in the repo.
  - The deep store's cached seasons are not in this checkout. They hold play-by-play, and the season sims read
    scoreboards anyway.
- **As of.** The projection for week w locks the results of weeks before w and plays every game from week w
  on. Ratings are built from earlier results only.
  - A conference title game enters the schedule only in its own week.
  - A committee ranking counts only if ESPN dates it before week w's first kickoff.
  - Guard C3a checks this: every week's projection is bit-identical when every later result, ranking and
    outcome is scrambled.
- **Two arms on identical inputs.** Both arms share the schedule, teams, ratings, banked results and selection
  rules. They also share the same random draws for everything except the game itself: college's 2.6-point
  season form, the 95% non-FBS games and the tiebreaks (C3b).
  - The **engine** arm is production's code. When one generator drives every draw, it reproduces
    `cfb.run_season` and `nfl_season.run_season` draw for draw (C3c).
  - The **analytic** arm resolves each game at Phi(margin / 18) for college and Phi(margin / 13.2) for the
    NFL. It is given the same margin the engine is given (C3d).
  - Each week runs 4,000 seasons, production's default, with the same seed for both arms.
- **NFL ratings.** Production's preseason level is Kalshi's ladder, which cannot be replayed (S4). The
  replay uses the record-only rating instead, the one the deep validation and game_xref used: 8.5 blended
  toward 8.5 + 0.5 x points differential per game, at weight g / (g + 6). Weeks 1-3 are close to flat for both
  arms.
- **College ratings.** These are production's prior and Massey fit, unchanged. The team set and conferences
  are the season's own (S5): production reads last season's tree, a defect noted below.
- **CFP field.** From week 11, the ranking metric is the committee's latest ranking. Unranked teams sit below
  every ranked team, in production's order. Before week 11, production's metric is used (S6).
- **Targets.**
  - NFL: regular-season wins (a tie counts half), division title (seed 1-4) and playoffs (seed 1-7).
  - College: regular-season wins excluding conference title games, CFP field membership and the national
    title.
  - A probability is read as (k + 0.5) / (n + 1).

## The scores in more detail (descriptive; none of this is the rule)

Each arm's level, as the mean per team-week:

| | MAE engine | MAE analytic | log loss engine | log loss analytic |
|---|---|---|---|---|
| NFL 2024 | 1.629 | 1.613 | 0.718 | 0.730 |
| NFL 2025 | 1.685 | 1.680 | 0.902 | 0.929 |
| College 2024 | 1.069 | 1.065 | 0.257 | 0.264 |
| College 2025 | 1.094 | 1.076 | 0.204 | 0.208 |

The log loss for each outcome (engine / analytic):
- **NFL division:** 0.384 / 0.394 in 2024, and 0.459 / 0.473 in 2025.
- **NFL playoffs:** 0.334 / 0.336 in 2024, and 0.443 / 0.455 in 2025.
- **College CFP membership:** 0.236 / 0.244 in 2024, and 0.187 / 0.191 in 2025.
- **College title:** 0.0210 / 0.0199 in 2024, and 0.0174 / 0.0161 in 2025. On the title, the analytic arm is
  the better of the two in both seasons.

**By phase.** The analytic arm's win-total gain is concentrated late in the college season: weeks 11-15 read
+0.007 (1.9 SE) in 2024 and +0.017 (5.1 SE) in 2025.

Its playoff-score loss is concentrated early:
- NFL weeks 1-6: -0.054 (-2.1 SE) in 2024 and -0.030 (-2.4 SE) in 2025.
- College weeks 1-5: -0.011 (-2.4 SE) in 2024 and -0.006 (-1.9 SE) in 2025.

NFL weeks 13-18 of 2024 went the other way, +0.010 (2.3 SE).

These phase cuts were not registered. They describe the read, and they decide nothing.

**A likely mechanism (a hypothesis, not tested here).** The analytic resolution is sharper than the engine,
and the season figures it sharpens are the ones the as-of ratings know least. For the NFL there is a specific
reason. `nfl_season._PTS_PER_WIN` is 2.25, and its comment says it was set as 1.66 / 0.74 so that the
compressed engine would hand the market's wins back. Feeding that same 2.25 into Phi(. / 13.2) therefore
over-corrects.

The registered rule fixed "the numbers the boards already use", so the replay kept 2.25.

*Corrected the same day (addendum below).* The first version of this paragraph said "about 1.35x too steep" and
"a fair retry would resolve at 1.66". Both were wrong.
- The measured over-spread is 1.19x.
- The analytic resolution needs 1.842 points per win to give the market's wins back. At 1.66 it compresses
  them (slope 0.91).

The new comparison is registered on fresh games.

## The CFP committee ranking (the secondary test, S6)

The test compares membership log loss on weeks 11-15 with a committee ranking. It sets production's metric
(0.6 x wins + rating + form) against the committee's latest ranking used as the metric, with the difference
read as old minus committee.
- **Engine arm:** -0.060 +/- 0.051 in 2024 and -0.047 +/- 0.051 in 2025.
- **Analytic arm:** -0.062 +/- 0.052 in 2024 and -0.049 +/- 0.052 in 2025.

The committee metric is the worse of the two at both point estimates, inside 1.2 SE. It is **not established**,
and nothing ships from it.

The reason is visible in the metric itself. Read literally, as the registered metric is, a ranked team's
place does not move when it loses a simulated game after the ranking. Production's metric does move with the
season it simulates.

The 24 real slots are in the inputs. In 2024 they were OSU, PSU, CLEM, ORE, TEX, SMU, TENN, UGA, BOIS, IU,
ND and ASU. In 2025 they were MISS, OSU, OU, MIA, TA&M, ORE, JMU, TTU, TULN, ALA, UGA and IU.

## Power and gaps

- **Power.** The SE per season is about 0.013 wins (NFL, 32 team clusters) and 0.0075 (college, 134-136
  clusters) on win totals. For 80% power to clear 2 SE in a single season, the true gain would have to be
  about 0.037 wins per team-week for the NFL and 0.021 for college. The rule needs both seasons, which
  compounds that. The observed win-total gains are 0.006-0.016 (NFL) and 0.005-0.017 (college): between a sixth and
  four-fifths of the size needed.
- **The NFL preseason is not production's.** The replay had no historical market, so weeks 1-3 are near-flat
  for both arms (S4). Production's live sim starts from Kalshi's ladder. What this read says about the NFL is
  about the record-driven part of the season.
- **No conference title games or committee realism in either arm.** Champions come from simulated conference
  wins, and the bracket keeps 2024's champion byes in 2025 (title only). The two arms share these
  approximations.
- **A banked NFL tie** (one, in 2025) counts as an away win, as it does in production, for both arms.

## For the owner

1. **Nothing changes in production.** C3 is read and not established for either sim. The Research tab's entry
   now points at the result.
2. **A production defect the replay worked around (S5).** It is not part of C3, and it is live for the 2026
   CFP race. `cfb.teams(season)` reads season - 1's standings tree for both the prior AND the conference
   membership. Checked today against ESPN's 2026 tree:
   - The 2026 sim puts nine schools in their 2025 conferences: Boise State, San Diego State, Fresno State,
     Utah State and Colorado State are in the Mountain West rather than the new Pac-12, and Texas State is in
     the Sun Belt rather than the Pac-12. UTEP and NIU are not in the Mountain West, and Louisiana Tech is not
     in the Sun Belt.
   - The sim leaves out the two new FBS schools, Sacramento State and North Dakota State.

   Conference champions decide the five auto-bids, so the board's CFP odds for those leagues read the wrong
   races. The fix is to take membership from season S's tree and only the prior from S-1's. It is a small
   change, but it is not this read's to make.

Guards for the read are in `tests/guards_auto_c3.py`:
- **C3a:** as-of discipline, with teeth.
- **C3b:** the arms run on identical inputs.
- **C3c:** the engine arm equals production in both sports.
- **C3d:** the analytic arithmetic.
- **C3e:** the rule on fixtures.
- **C3f:** the inputs.
- **C3g:** the committed verdict, recomputed from its own figures. It also checks that production still
  resolves games by the engine exactly where the verdict is not PASS.

## Addendum (2026-11-27): the 2.25 investigation

The owner asked us to "investigate the suspected double margin-compression correction. Distinguish a
demonstrable implementation error from a proposed modeling improvement. Preserve the original replay result,
and register any new predictive comparison on fresh data."

**The test.** Each reader of the constant is checked against the constant's own contract. That contract, from
the comment above `nfl_season._PTS_PER_WIN`, is that "the season sim seeded with the market's wins has to give
those wins back."

The test seeds 32 teams with a market-like win vector: 2025's real wins shrunk halfway to 8.5, a spread of 1.70
wins. It plays 2025's 272-game schedule, then regresses the returned expected wins on the seeded wins. A slope of
1 keeps the contract.

The script is `research/c3_margin_scale.py`. Its output is `research/data/c3_margin_scale.json`. No outcome is
read: this is a property of each engine.

| reader of 2.25 | production? | slope at 2.25 | other points |
|---|---|---|---|
| `nfl_season._matchup_pts` -> drive engine (`run_season`) | **yes**: the NFL season sim | **1.040 +/- 0.025** | 1.66: 0.787 |
| `nfl_season._wp`: Phi((gap x 2.25 + 2.1) / 13.2) | **no** caller in production | **1.194 +/- 0.029** | 1.66: 0.909; slope 1 at **1.842** |
| `nfl_deep_data.market_margins` -> `apply_anchor` -> deep engine | **yes**: the deep engine's market level | **1.206 +/- 0.031** | 1.95: 1.080 |

For the deep engine, the realized margin on the anchored margin is 1.007. So the anchor delivers the margin it
is asked for; the margin it is asked for is the problem.

1. **No production path applies the correction twice.** The NFL season sim applies 2.25 once, through the
   engine whose compression it corrects. It gives the market's wins back (1.04).
2. **The C3 analytic arm is a modeling question, not a production bug.** Its construction carries the
   over-spread: Phi(. / 13.2) at 2.25 returns 1.19x the market's spread. The registered rule used
   `_wp`'s numbers, so the over-spread lives in C3's arm (and in the research PD arms of game_xref and the deep
   validation that call `_wp`), not in any production number.

   C3 stays read for its own question. The question it could not answer, whether the analytic resolution
   scaled to 1.842 beats the drive engine, is registered as **C3-N** on fresh games only:
   - `research/c3_nfl_rescaled.py`, and `research/data/c3_nfl_rescaled/prereg.json`, which pins it by sha256.
   - The data are 2026's projections before weeks 13-18 (every simulated game from 2026-12-03) and all of
     2027.
   - It passes only at > 2 SE on both scores, pooled, clustered by team-season.
   - It is not read before 2028-01-12.
   - It is underpowered: 80% power needs about 0.034 wins and 0.028 nats per team-week. Expect "not
     established".
3. **A demonstrable implementation error in production, reported and NOT changed here.** The deep engine's
   market anchor converts Kalshi's rest-of-season pace to margin at the drive engine's 2.25. The deep engine
   does not compress margins (realized on anchored 1.007), so a deep season seeded with the ladder returns the
   market's spread 1.21x (6.6 SE from 1). Its own contract says otherwise: `market_margins` is "points of
   margin ... from Kalshi's win totals", and the level is "anchored at full weight to Kalshi's win totals".

   **Proposed fix.** Give the deep anchor its own constant, measured by the same criterion. The two points
   measured here bracket it at 1.81-1.87, and the analytic slope-1 figure is 1.842. Measure it at higher n
   before shipping.

   **Proposed guard.** The deep season seeded through `market_margins` returns slope 1 +/- 0.05. The drive
   season sim keeps 2.25 at slope 1.04.

   **Effect on the current board** (week 12 of 2026, the live ladder; `research/data/c3_margin_scale_board.json`):
   - This measures the level alone, on league-average units.
   - The deep figure moves 0.3-5.6 points a game toward the market's spread. For example, WSH @ ARI goes from
     26.7% to 32.3% for the home side, and TEN @ JAX from 88.0% to 84.2%.
   - The slate headline, which takes the deep figure at 0.30, moves up to 1.7 points.
   - The deep season's favourites read too strong in division and playoff odds, and the DFS worlds' deep
     scoring level is over-spread the same way.

   **Why the orchestrator and the owner must decide.** The registered NFL deep-weight read (100 graded
   `nfl_deep` games, 84 by 2026-11-26) is grading this deep engine as it stands. Changing the anchor
   mid-read changes the model under test.
