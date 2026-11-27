# NFL coaching engine, R1: explicit clock management and fine time/score states

Written 2026-11-27. **Research only: no production file changed, no board, viewer, DFS world or predlog
bucket reads any of this.** R1 is the audit's Phases 5 and 3 at league level
(`research/reports/nfl_coaching_audit.md` 7.3). The registered test has NOT been run: it reads 2026 weeks
13-18 once, on or after 2027-01-06 06:00 UTC.

Data credit: nflverse-data (CC BY 4.0). Raw files are not committed; only fitted tables and aggregates are.

## 0. The short version

1. **A separate engine, `nfl_coach_sim.py` (version `coach-r1.0`), with its own tables.** It imports
   `nfl_deep_sim`'s samplers and replaces the snap loop. `nfl_deep_sim.py`, `nfl_deep_data.py`, `nfl_pbp.py`
   and `research/data/nfl_deep_tables.json` are byte-identical (guard NC0 pins their sha256).
2. **On the development seasons the end of each half is repaired.** Inside 2:00 the control plays 6.6 and
   3.8 snaps (second and fourth quarter) against a real 9.0 and 5.6; the new engine plays 8.5 and 5.2. Points
   inside 2:00: control 2.9 and 1.8, real 4.6 and 2.8, new engine 4.3 and 2.4 (2024, section 4).
3. **The leader's runoff is 41.9 s against a real 41.8** (control 26.8), and the trailing defence's timeouts
   are played at their learned rates.
4. **All eleven scenario tests pass**, with one added (section 3).
5. **The level did not move**: points, both spreads, home win rate and overtime rate are within one standard
   error of the control's distance from real in every development season.
6. **Expect the registered test to FAIL on criterion 4, and read that as a statement about power.** The
   late-state win-probability gain is 0.3 to 1.4 standard errors on 164-272 development games. The sealed
   window is about 96 games, where "more than 2 SE" needs an effect about three times the largest one seen.
   The criterion is the audit's and was not loosened. Section 6 and section 8 say what that leaves the owner.

## 1. What was built

| file | what it is |
|---|---|
| `nfl_coach_sim.py` | the experimental engine; `play_game(start=)` plays from any state; `control_from` does the same for `nfl_deep_sim` without editing it |
| `research/nfl_coach_data.py` | nflverse rows -> 23 ints a play (the first 14 are `nfl_pbp`'s record); holds the seal |
| `research/nfl_coach_fit.py` | the fit -> `research/data/nfl_coach_tables.json` (273 KB) |
| `research/nfl_coach_eval.py` | the measurements for real games and both engines, the level anchor, `--free` |
| `research/nfl_coach_r1_read.py` | the registered read (not run) |
| `research/nfl_coach_prereg.py` | writes the registration once |
| `research/data/nfl_coach/prereg_r1.json` | the registration |
| `research/data/nfl_coach/dev_r1.json`, `free_r1.json` | the development table and the form-tilt fit |
| `tests/guards_auto_nfl_coach.py` | 37 guards, section NC |

### 1.1 The two arms sit on the same fit

`base` in the tables file is **production's own fit code** (`research.nfl_deep_fit.build_tables`, imported)
run on nflverse 2016-2023: 2,095 regular-season games. The control arm is `nfl_deep_sim.play_game` on `base`.
The candidate reads the same `base` for the pass chain, yardage, penalties and kicks, and adds its `coach`
tables. Three things differ from the shipped `nfl_deep_tables.json`, for both arms alike:

- **The fourth-down end spot is read correctly.** nflverse flags a failed fourth down, so the fit takes the
  play's own yards. Sack yards average -6.6 here against the production table's -5.0 (which holds the 30-yard
  "gains"). **The production tables are not refitted** (the owner: after the deep weight's 100-game read).
- A fumble's takeover spot is measured from the end of the play, and a sack-fumble stays a sack (the lost
  fumble rate on sacks is 7.1% here; ESPN's coding filed those plays as completions).
- Kickoff field position is kept per rules era (2016-2023, 2024, 2025+). The two later eras hold only
  kickoff field position from those seasons: a rule of the game, shared by both arms.

### 1.2 What the candidate adds (all league level, all learned unless called a rule)

| piece | how |
|---|---|
| timeout inventories | 3 a half, reset at half-time; 2 in a regular-season overtime; 3 per two periods in the postseason |
| strategic timeouts | after an in-bounds play the defence, then the offence, may stop the clock: rates by phase x score x own inventory. A stopped clock can still cost a timeout at its own rate |
| the clock | seconds to the next snap from histograms **with timeouts taken out**, by 15 clock phases x 5 score states x no-huddle |
| fine phases | the second quarter's 5:00-2:45, 2:45-2:00, 2:00-1:00, 1:00-0:30, last 0:30 are apart from the fourth quarter's seven phases; overtime is its own |
| the play call | production's (down, distance, zone) cell plus ridge effects of phase x score (nine bins), phase x score x down, phase x the offence's timeouts |
| no-huddle | drawn by phase x score; it keys the clock histogram |
| kneels | victory formation is a **rule**: leading in the fourth, when the kneels left outlast the clock net of the opponent's timeouts and the warning. Real teams knelt on 1,790 of 1,826 such snaps (98.0%). Every other kneel (before half-time, tied, a lead not yet safe) is learned |
| spikes | learned, only with the clock running, no timeout called, downs 1-3 |
| two-minute warning | every second goes through one function; it stops at exactly 2:00 once. `nfl_deep_sim`'s kickoff can run through 2:00 without the stop; this engine's cannot |
| out of bounds | by play type x phase x score |
| onside kicks | kicked at the learned rate by deficit and clock; **recovered 7.6% (24 of 316, 2018-2023)**, this engine only. Production's 12% is untouched |
| overtime | both rule sets, as production, plus overtime timeouts, the overtime warning and the postseason's two-period halves |
| end of half | **rule**: with time for one snap (0:06 before half-time; 0:03 for a fourth-quarter leader) nobody punts |

**Smoothing.** Every rate cell is pulled toward its parent with prior mass 5; a histogram draw falls through
to its parent with probability 160/(n+160); the call's ridge is 8. Each was chosen on 2024 from three values.
The first two landed on the edge of their grids, and the scores are flat (0.20808 against 0.20811;
2.8203 against 2.8244), so the choice is not sharp.

**Not modelled, and why.** The ten-second runoff: 3 rows mention it in 569 games of 2022-2023. Coordinators
and personnel: out, by the owner's settings. Sack, completion and yardage tables are unchanged, so the
two-minute drill's own completion profile is not there yet.

**Speed.** 2.1 ms a game with the log against the control's 1.7 (4,000 games, this box).

## 2. Seasons

| role | seasons |
|---|---|
| fit | 2016-2023 |
| select (three smoothing masses) | 2024 |
| can only block | 2025: it did not block (section 4) |
| development, seen by the audit | 2026 weeks 1-12 |
| **sealed** | **2026 week 13 on.** `nfl_coach_data.load_season` drops those rows before parsing a field of them, for every caller but the read (guards NC8, NC9). Not read by this work |

## 3. The scenario tests (guards S1-S12, all pass)

| # | scenario | result |
|---|---|---|
| S1 | leading by 1, first down, 1:55, opponent out of timeouts | kneels only, leader wins, 300 of 300 seeds |
| S2 | the same against three timeouts | a kneel on first down in 0 of 600; never more than three timeouts, never below zero |
| S3 | trailing by 4, 0:25, clock running, no timeouts | never a huddle; 227 of 800 spike; every spike costs a down and stops the clock. The clock ran out before a snap in 111 |
| S3b | 6,000 whole games | no spike on fourth down, none with the clock stopped |
| S4 | leader runs in bounds at 2:30, defence has three | timeout on 67.3% against the learned 66.6%; three at most; none once gone |
| S5 | a play ends in bounds at 2:05, second and fourth quarter | one warning, at exactly 2:00; whole games: one in each of Q2 and Q4, none in Q1 or Q3 |
| S6 | regular-season overtime | one 10:00 period; the game went on after all 377 opening touchdowns; 280 of 1,500 ended level at 0:00 (a tie); two timeouts a team |
| S7 | postseason overtime | 15:00 periods, 0 ties in 1,500, 89 reached a second period |
| S8 | half-time, 0:03, the opponent's 25 | field goal, 300 of 300 |
| S9 | half-time, 0:20, own 20, leading | kneel first in 371 of 600; no punt at 0:06 or less |
| S10 | 6,000 games | at most three timeouts a team-half; the clock adds to 3,600 s plus overtime; no snap at a goal line; the score equals its scoring events |
| S11 | the passive log | every box identical with and without it |
| S12 | trailing by 4 at 1:30 with three timeouts | never a kneel; 5.96 snaps a drive; an in-bounds play costs 24 s, not the leader's 42 |

Two scenarios are stated more narrowly than the audit wrote them, because the wider form is not true of real
football. S2's "no kneel" is a learned rate (0 of 600 here), not a rule. S9's "nobody punts" holds at one
snap's time; a team at its own 20 with 0:14 left on fourth down does punt.

## 4. Development measurements

Each real game is played by both arms at levels set from its closing line (section 5). 80 games a real game,
300 play-outs a checkpoint. Real figures carry their standard error.

**2024 (the selection season, 272 games)**

| per game | real | control | candidate |
|---|---|---|---|
| snaps inside 2:00, second quarter | 8.98 +/- 0.15 | 6.58 | 8.51 |
| points inside 2:00, second quarter | 4.63 +/- 0.23 | 2.95 | 4.31 |
| snaps inside 2:00, fourth quarter | 5.58 +/- 0.21 | 3.78 | 5.21 |
| points inside 2:00, fourth quarter | 2.80 +/- 0.22 | 1.76 | 2.39 |
| kneels, second quarter | 0.36 | 0 | 0.31 |
| kneels, fourth quarter | 1.13 | 1.69 | 1.32 |
| spikes | 0.26 | 0 | 0.15 |
| timeouts (regulation, both teams) | 7.36 | none | 6.94 |
| leader's runoff, Q4 5:00-3:00, up 1-8, no timeout between (s) | 41.8 | 26.8 | 41.9 |
| trailing defence's timeout, 5:00-3:00 | 27% | none | 37% |
| the same, 3:00-2:00 | 56% | none | 60% |
| the same, 2:00-1:00 | 95% | none | 89% |
| points | 45.8 +/- 0.8 | 44.5 | 44.7 |
| margin SD about the spread | 12.61 | 12.56 | 12.53 |
| total SD about the total | 12.55 | 12.67 | 12.56 |
| home win rate | 0.533 | 0.541 | 0.545 |
| overtime rate | 5.9% +/- 1.4 | 5.1% | 6.2% |

**The four inside-2:00 figures on the other two seasons** (real / control / candidate)

| | 2025 (272 games, can only block) | 2026 weeks 1-12 (164 games) |
|---|---|---|
| Q2 snaps | 8.76 / 6.57 / 8.48 | 9.06 / 6.54 / 8.46 |
| Q2 points | 4.88 / 2.94 / 4.34 | 4.79 / 2.92 / 4.27 |
| Q4 snaps | 5.33 / 3.80 / 5.21 | 5.32 / 3.81 / 5.17 |
| Q4 points | 2.45 / 1.81 / 2.37 | 2.85 / 1.83 / 2.35 |
| leader's runoff (s) | 41.2 / 26.7 / 41.9 | 40.5 / 26.7 / 41.8 |
| overtime rate | 5.2% / 5.3% / 6.0% | 7.3% / 5.2% / 5.9% |

**Decision-level checks** (log loss; lower is better)

| | 2024 | 2025 |
|---|---|---|
| play call, candidate | 0.5811 | 0.5824 |
| play call, the control's (time state, score state) cell | 0.5942 | 0.5920 |

**The registered rule applied to the development seasons** (not the test; `verdict` on `dev_r1.json`)

| | 2024 | 2025 | 2026 wk 1-12 |
|---|---|---|---|
| 2 fidelity (all four closer by more than the real SE) | pass | pass | pass |
| 3 level not worse (five figures) | pass | pass | pass |
| 4 late-state win probability, candidate minus control | -0.0012 +/- 0.0047 | -0.0072 +/- 0.0051 | -0.0054 +/- 0.0071 |
| 4 as registered (better by more than 2 SE) | fail | fail | fail |
| 5 pre-game log loss, candidate minus control | +0.0073 +/- 0.0082 | -0.0096 +/- 0.0102 | -0.0040 +/- 0.0130 |
| 5 total CRPS, candidate minus control | +0.047 +/- 0.074 | -0.077 +/- 0.077 | +0.173 +/- 0.106 |
| 5 as registered (each within +1 SE) | pass | pass | **fail** (CRPS) |

Reading it:

- **What is shown.** The candidate reproduces how the last two minutes are played: snaps, points, kneels,
  runoff and timeouts, in all three seasons, by many standard errors on the snap counts.
- **What is not shown.** That this makes a better win probability. From 5:00 and 2:00 of the fourth quarter
  the candidate's log loss is lower in all three seasons, by 0.3, 1.4 and 0.8 standard errors.
- **Where it is still short.** Fourth-quarter points inside 2:00 (2.39 against 2.80 in 2024). Spikes are at
  about half the real rate. The 2:00-1:00 timeout rate is 89% against 95-97%: the fit seasons' cell, shrunk.
  The overtime rate runs 0.6 to 1.1 points above the control, inside the real figure's standard error.
- **From the start of the fourth quarter the candidate is worse** in 2024 and 2025 (+0.005 +/- 0.004,
  +0.009 +/- 0.007). That checkpoint is not in criterion 4 (the audit pooled 5:00 and 2:00), but it is
  reported at the read. I have not found the cause.
- **Criterion 5 failed on 2026 weeks 1-12** through the total's CRPS, 1.6 standard errors worse, after being
  better in 2025. A one-SE bound fails about one time in six on noise alone, for each of its two halves.

## 5. The level anchor and the free parameter

- **The anchor.** Each team is the league profile with an offensive level set so the arm's own expected
  points equal the closing line's implied points, (total +/- spread) / 2, on a neutral field. The level curve
  is measured per arm (15 levels, 2,500 games each), so neither arm is handed the other's scale. Both arms
  land within 0.2 points of each other and about 1.2 under the real totals of 2024-2026.
- **Team profiles are not used.** `nfl_deep_data`'s profiles need the ESPN deep store and Kalshi's win
  totals. R1 is league level; the closing line is the strength, the same for both arms.
- **The form tilt is 0 for both arms** (`free_r1.json`). With no tilt the margin's spread about the line is
  already 13.04 (candidate) and 13.01 (control) against the real 12.79 over 2016-2023; at 0.04, 0.065 and
  0.08 it is 13.26, 13.64, 14.06. Production's 0.08 was fitted on ESPN-built tables under another anchor.
- **The home edge was not refit.** It is never applied on a neutral field. If this engine is ever run
  off the line, `_HFA_YDS` needs its own fit first.
- **League average against itself, unanchored**, both arms run about 1.5 points under the fit seasons'
  real 45.5. The cause is in `base`, common to both arms, and not found.

## 6. The registration

| | |
|---|---|
| file | `research/data/nfl_coach/prereg_r1.json` |
| block sha256 | `ba0bae085ed537d91a14844076d473828c44f1b26089d4a80f36a13fa75514ae` |
| commit | the commit that adds this report (its hash is in the note at the end) |
| window | 2026 regular season, weeks 13-18, at least 90 games |
| read date | on or after **2027-01-06 06:00 UTC**, and not before the last game's date + 78 h by the data |
| settings | 200 games a real game, 1,000 play-outs a checkpoint, level curve 2,500 a level, form tilt 0 both arms, probabilities clipped at 0.002 |
| pinned by sha256 | the read, `nfl_coach_eval.py`, `nfl_coach_data.py`, `nfl_coach_sim.py`, the tables, the guards file, `nfl_deep_sim.py`, `nfl_pbp.py` |

**The five criteria. All must hold.**

1. `tests/guards_auto_nfl_coach.py` exits 0.
2. **Fidelity.** Snaps and points inside 2:00 of the second and of the fourth quarter: for each of the four,
   the candidate is closer to the real mean than the control by more than the real mean's standard error.
3. **Level.** Points, margin SD about the spread, total SD about the total, home win rate, overtime rate: for
   each, the candidate's distance from real is at most the control's plus one standard error.
4. **Late-state win probability.** From the real state at the first snap at or inside 5:00 and 2:00 of the
   fourth quarter, both arms play the game out. Log loss on the real result, candidate minus control,
   averaged within a game; the mean over games is below -2 standard errors.
5. **Pre-game.** Moneyline log loss and total CRPS, paired by game: each at most +1 standard error.

These are the audit's, made exact. Nothing was loosened. Two were tightened: criterion 4 clusters by game
(one number a game, not one a checkpoint), and criterion 5 must hold for both statistics.

**The read refuses** before its date, a dirty tree, any pinned file that differs, fewer than 90 games, and a
second read. A FAIL is final for this candidate.

**Why weeks 13-18.** It is every sealed regular-season game: about 96. Week 18 has resting starters, which is
real football. The cost: later phases have no unread regular-season games of 2026 (section 8).

**Power, stated before the read.** Criterion 4's standard error was 0.0047-0.0071 on 164-272 games. At 96
games it is about 0.008-0.009, so a pass needs a gain near 0.017 nats. The largest development estimate is
0.0072. **Unless the sealed games behave very differently, criterion 4 fails and R1 fails.** That would be
insufficient evidence on win probability, not evidence that the clock repair is wrong: criteria 1-3 are
where R1's claim is, and those passed on every development season.

**Running it.** The pinned files must match, so later phases must not be in the tree. Check out the
registration commit in a clean worktree, fetch a fresh `play_by_play_2026.csv.gz`, and run
`python3 -m research.nfl_coach_r1_read <dir>`. It writes `research/data/nfl_coach/read_r1.json`.

## 7. Guards (`tests/guards_auto_nfl_coach.py`, 37 checks)

- NC0: the four production files' sha256; the engine writes to no predlog bucket and no production module
  imports it; its own version constant and tables file.
- NC1: fit seasons, selection season, the seal and the credit in the tables; the onside figure is the
  measured one and production's 0.12 is untouched.
- S1-S12: section 3.
- NC2, NC3: a play-out from the opening state is `play_game` draw for draw, for the candidate and for the
  control against `nfl_deep_sim.play_game`.
- NC4-NC7: the phases, the smoothing, the play call apart in the two-minute drill and for a fourth-quarter
  leader, the runoff without timeouts.
- NC8, NC9: the seal's definition; the loader drops a sealed game and refuses to hand the set over.
- NC10-NC13: the registration's file hashes and its own hash; the window and date; the read's refusals; the
  verdict rule on two hand-built summaries.

## 8. For the owner

1. **The test is likely to fail on power (section 6).** Options, each his call and none taken here: accept
   R1 on criteria 1-3 as a fidelity repair with the win-probability claim unproven; or register a second
   read on more games (the 2026 postseason and 2027). The registered rule stands either way.
2. **The window uses every sealed regular-season game of 2026.** R2-R4 then have the 2026 postseason and
   2027 as unread games. R2's statistic (who gets the ball) is not one R1's read looks at; whether those
   games still count as unread for R2 is his decision.
3. **The start-of-fourth-quarter checkpoint is worse** in two development seasons and unexplained. I would
   look at it before R2 builds on this engine.
4. **Both arms run 1.2 to 1.5 points under real totals** on nflverse-built tables. It does not affect the
   comparison, but it is a defect of the `base` fit to find before this engine is used off the line.
5. **The fourth-down refit of the production tables is still open**, as he decided. This engine's own tables
   do not carry the defect.

## 9. Reproduce

```
# fetch play_by_play_<season>.csv.gz for 2016-2026 from nflverse-data's "pbp" release into <dir>
python3 -m research.nfl_coach_fit <dir>
python3 -m research.nfl_coach_eval <dir> --free
python3 -m research.nfl_coach_eval <dir> --seasons 2024,2025,2026 --sims 80 --playouts 300
python3 tests/guards_auto_nfl_coach.py
```

The tables carry a build timestamp, so a refit differs from the committed file in that field and the
registration pins the committed one.

## 10. Registration commit

The registration, the read, the engine and the tables it pins were committed together as `eaca95d` (this
worktree; the hash changes if the commit is cherry-picked, the registration block `ba0bae08` and the pinned
file hashes do not). Full guard suite at that commit: 3,878 passed, 0 failed.
