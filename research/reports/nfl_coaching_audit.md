# NFL coaching intelligence and adaptive play-by-play engine: the audit (Phase 1) and the plan (Phases 2-9)

Written 2026-11-26 from the work branch at 5696b7d. **An audit and a plan: no production code changed.**
Two read-only research scripts and their outputs are the only additions:

| file | what it is |
|---|---|
| `research/nfl_coaching_data_audit.py` | one streaming pass over nflverse play-by-play: which fields exist, how often they are filled, and how large the coaching and clock signals are |
| `research/nfl_coaching_engine_probe.py` | plays the production `nfl_deep_sim` through its own passive log and measures the same end-of-half cells |
| `research/data/nfl_coaching_audit.json` | the first script's output: 2023, 2024, 2025 and 2026 weeks 1-12, 980 regular-season games |
| `research/data/nfl_coaching_engine_probe.json` | the second's: 4,000 league-average games, tables built 2026-10-16 |

Every "real" figure below is from the first file, every "engine" figure from the second, unless a report is
named. The nflverse files were fetched to a scratch directory for the measurement (CC BY 4.0) and are not
committed.

**What this audit has now seen.** League-level aggregates of 2023-2025 and of 2026 weeks 1-12. No
coach-specific value was printed or read, only reliability summaries across team-seasons. 2026 week 13 on is
unread. Section 7.1 builds the held-out logic on that.

---

## 0. The short version

1. **The engine is a situation-table engine with no coach, no timeouts and three time states.** It plays
   well on averages and is wrong in a specific, measurable place: the last two minutes of each half.
2. **Inside 2:00 the engine plays about a quarter fewer snaps and scores about 30% fewer points than real
   games**, in both halves (section 3.2). The game total is right, so the points are made up elsewhere.
3. **The three claims relayed from the November 20 audit are all confirmed**, and the third is understated:
   coach-specific intelligence is not incomplete, it is absent (section 3).
4. **The refit registered for 2026-11-06 did not happen.** The tables are the 2026-10-16 build; the
   fourth-down frame bug is still in them (section 4).
5. **The data supports most of the project in season**: head coach, timeouts, no-huddle, kneels, spikes,
   air yards, two-point tries, onside kicks, the play clock and the ball-carrier on every play are all in
   nflverse's play-by-play, 100% filled, updated nightly. Coordinators are in no licensed machine-readable
   feed. Personnel groupings arrive only after the postseason (section 5).
6. **Coach identity is the weakest of the measured signals.** Fourth-down aggressiveness is a real coach
   trait. Pass rate and pace follow the team-season, which the engine already carries, and neither travelled
   with the 13 head coaches who changed teams (section 3.3).
7. **Honest expected value.** For the moneyline: at most a thousandth or two of a nat, below what any
   feasible pre-game test can detect. For DFS: real, through who gets the ball in which script, and through
   the two-minute drill (section 6).
8. **Recommended order**: clock and urgency at league level first, then script-conditional usage, then
   fourth downs, then coach effects, then adaptive effects, then the screen. This differs from the owner's
   numbering and is his call (section 9).

---

## 1. What was read

`nfl_deep_sim.py` (whole), `nfl_pbp.py`, `research/nfl_deep_fit.py`, `research/data/nfl_deep_tables.json`,
`nfl_deep_data.py`, `nfl_dfs_engine.py`, `nfl_usage.py`, `nfl_viewer.py`, `nfl_game_sim.py` (the drive
model, briefly), `nfl_matchup_data.py`, `nfl_matchup_profiles.py`, `nfl_rush_data.py`, and the reports
`nfl_deep_plan.md`, `nfl_deep_validation.md`, `nfl_viewer.md`, `nfl_dfs_engine.md`, `nfl_usage.md`,
`nfl_deep_weight_rule.md`, `research/sprint_2026_11_26/engines/report.md`, with CLAUDE.md's account of the
matchup model, the rushing model and `game_xref.md`.

---

## 2. The engine as it stands

### 2.1 What a simulated snap conditions on

| key | values | defined at |
|---|---|---|
| time state | 0 the body of the game; 1 the last 2:00 of the second quarter OR the fourth quarter above 5:00; 2 the fourth quarter's last 5:00 and overtime | `nfl_pbp.py:537` |
| score state | down 9+, down 1-8, tied, up 1-8, up 9+ | `nfl_pbp.py:532` |
| distance | 1-2, 3-6, 7-10, 11+ | `nfl_pbp.py:505` |
| field zone | eight zones, fine near the goal line | `nfl_pbp.py:510` |
| tempo | 0 normal, 1 hurry-up, 2 clock-killing | `nfl_deep_sim.py:630` |
| timeouts | none | - |
| coach | none | - |
| exact time | only through the rules in 2.3 | - |

### 2.2 The fitted tables (`research/data/nfl_deep_tables.json`, 935 games, 171,409 plays)

| table | key | size | smoothing |
|---|---|---|---|
| play call (pass or run) | time, score, down, distance, zone | 1,438 cells, median 22 plays, 687 under 20 | prior mass 20 toward (down, distance, zone), then (down, distance); `nfl_deep_sim.py:186-199` |
| sack, interception, completion | down, distance | 16-17 cells each | toward the league rate |
| completion yards, rush yards | down, distance, zone | 100 cells each | falls back to (distance, zone) |
| fourth down: go, punt, kick | five-yard zone, distance, score, time | 1,081 cells, median 9 decisions, 478 under 8 | prior mass 8 down zone -> +distance -> +time -> +score; `nfl_deep_sim.py:266-276` |
| two-point try | score after the six (clamped to 16), late or not | 66 cells | prior mass 6 toward 6% |
| clock to the next snap | play class (run, incomplete, out of bounds, score, turnover, penalty, pre-punt, pre-kick) x tempo | 27 histograms | none |
| field goal | kick distance | logistic, 5.896 - 0.0953 x yards | plus a kicker shift |
| punt, kickoff, penalty outcome, returns | field position or era | histograms | none |

### 2.3 Rules that are code, not tables

| rule | where | note |
|---|---|---|
| two-minute warning | `_run_clock`, `nfl_deep_sim.py:652` | second and fourth quarters; stops the clock at 2:00 |
| kneel-out | `_snap`, `:777` | fourth quarter only, leading, `clk <= 40 x (4 - down) + 5`; blind to the opponent's timeouts |
| clock-expiring field goal | `:796` | at 20 seconds or less with the kick in range |
| late fourth-down overrides | `_fourth_decision`, `:666-696` | trailing inside 3:00, overtime, kicks out of range |
| onside kick | `_kickoff`, `:480` | always, when trailing by 1-16 inside 3:00 of the fourth; recovered 12% |
| overtime | `_end_period`, `_ot_decided`, `:564`, `:587` | regular season 10:00, both teams possess, then sudden death, ties stand; playoffs 15:00 periods until decided |
| safety share | `:95` | a third of the draws that reach the offence's own end zone |

### 2.4 Parameters

- **Fitted free parameters, two**: `_FORM_SD` 0.080 (margin SD) and `_HFA_YDS` 1.021 (home win rate).
- **Measured**: the matchup exponents and the shrinkage K per stat (yards per play 5.9 games, pass rate
  8.7, completions 11.5, sacks 7.9, yards per carry 22, interceptions 26).
- **Judgment or rule**: the three prior masses (20, 8, 6), `_KNEEL_SNAP_S` 40, `_SAFETY_SHARE` 0.33,
  `_ONSIDE_RECOVERY` 0.12, the 4.0-game K for pace.
- **DFS only**: `ENV_SD` 0.14, `ATT_WEIGHT` 1, `DEEP_WEIGHT` 0.30 (`nfl_dfs_engine.py`).

### 2.5 What a team profile carries (`nfl_deep_data.py`)

Per team-season, shrunk toward last season's surviving deviation: pass rate, completion rate, yards per
completion, sack and interception rates, yards per carry, penalties, seconds per snap, the kicker, the punt
and kick-return units, and a level anchored to Kalshi's win totals. The pass rate moves every play call by
one logit shift (`d_call`, `nfl_deep_sim.py:343`); pace multiplies the clock only at normal tempo after a run
or an out-of-bounds play (`:649`). **Nothing is keyed on a coach, and a coaching change does not reset a
prior.**

### 2.6 How players get the ball in the DFS worlds

`nfl_dfs_engine.deal_team` (`:356`) deals each world's finished team box to players by Sleeper's projected
shares (0.7 Sleeper + 0.3 trailing share since week 7, `nfl_usage.py`) with fitted share noise. **The deal
sees totals, not plays**: a back's share of the carries is the same in a blowout win as in a comeback.

---

## 3. The audit

### 3.1 The November 20 audit's three claims

| claim | verdict | evidence |
|---|---|---|
| explicit timeout management is missing | **Confirmed.** | No timeout state exists in `play_game`'s state dict (`nfl_deep_sim.py:415`). The fit skips timeout rows and measures the clock gap across them (`research/nfl_deep_fit.py:229-240`), so a timeout is a short gap inside the tempo histogram, drawn whether or not the team has one left. `nfl_deep_plan.md` section 7 says so itself. |
| late-game urgency is too coarse | **Confirmed, with a specific defect.** | Three time states and three tempos. Time state 1 pools the first half's two-minute drill with the fourth quarter above 5:00 (`nfl_pbp.py:537`), two situations with opposite play-calling. See 3.2. |
| coach-specific intelligence is incomplete | **Understated: it is absent.** | No file in the engine reads a coach's name (a search of every `.py` for "coach" and "coordinator" finds comments only). What exists is team-season identity: pass rate, pace, the kicker. |

### 3.2 The end of each half, engine against real games

**Snaps and points inside 2:00, per game (both teams)**

| | real (980 games) | engine (4,000 games) | engine minus real |
|---|---|---|---|
| second quarter, snaps | 8.95 | 6.71 | -25% |
| second quarter, points | 4.70 | 3.14 | -33% |
| fourth quarter, snaps | 5.41 | 3.89 | -28% |
| fourth quarter, points | 2.54 | 1.81 | -29% |
| kneel-downs, fourth quarter | 1.23 | 1.66 | +34% |
| kneel-downs, second quarter | 0.31 | 0 | not played |
| spikes | 0.29 | 0 | not played |
| onside kicks | 0.17 | 0.13 | |
| onside recovery | 6.4% (11 of 171) | 10.7% (set at 12%) | |
| overtime | 5.6% | 5.9% | |
| points, whole game | 45.2 (the fit's validation) | 45.9 | |

Snaps are runs, passes and sacks; kneels, spikes and no-play penalties are out on both sides. The engine's
total is right while its last two minutes are a third short, so it scores too much in the body of the game.
`nfl_deep_plan.md` already recorded the second quarter 8% low and the fourth 7% high; this locates the
second-quarter shortfall inside 2:00.

**Pass rate, the engine's cells against finer ones** (a scramble counted as a rush on both sides)

| situation | real | engine |
|---|---|---|
| second quarter, last minute | 0.78-0.86 | 0.47-0.76 |
| second quarter, 2:00-1:00 | 0.66-0.75 | 0.47-0.75 |
| fourth quarter above 5:00, leading by 9+ | 0.33-0.44 | 0.47 |
| fourth quarter above 5:00, leading by 1-8 | 0.50-0.51 | 0.60-0.62 |
| fourth quarter above 5:00, tied | 0.53-0.56 | 0.63-0.66 |
| fourth quarter, last minute, trailing by 1-8 | 0.86 | 0.74 |
| fourth quarter, 2:00-1:00, leading (snaps not knelt) | 0.10-0.19 | 0.45-0.55 |

The first six rows are one defect: time state 1 is a pooled cell. The table's own pooled rate for "time state
1, leading by 9+" is 0.468, between the two real situations it averages (about 0.75 and 0.40). The last row
is the smoothing order: a thin late-and-leading cell is shrunk toward a marginal that has forgotten the score.

**Seconds to the next snap after an in-bounds play** (real: gaps with no timeout between; the engine's
histograms have the timeouts folded in)

| situation | real | engine |
|---|---|---|
| fourth quarter 5:00-3:00, leading by 1-8 | 42.3 | 26.6 |
| fourth quarter 5:00-3:00, leading by 9+ | 43.4 | 27.1 |
| fourth quarter 5:00-3:00, tied | 39.9 | 24.8 |
| fourth quarter 5:00-3:00, trailing by 1-8 | 36.0 | 24.9 |
| fourth quarter 15:00-5:00, trailing by 9+ | 31.6-34.9 | 25.0-25.1 |
| second quarter 2:00-1:00 | 27.1-29.8 | 24.7-25.5 |
| either half, last minute | 17.0-21.9 | 18.4-20.4 |

The engine's leader burns 27 seconds a play because the histogram mixes a 42-second runoff with a 6-second
timeout. Real teams do one or the other, and which one depends on the inventory:

**The trailing defence's timeout after an in-bounds play, fourth quarter, down 1-8**

| clock | at least one timeout left | none left |
|---|---|---|
| 5:00-3:00 | 30% (n 668) | 0% (n 29) |
| 3:00-2:00 | 66% (n 363) | 0% (n 91) |
| 2:00-1:00 | 97% (n 302) | 1% (n 144) |

Teams call 7.7 timeouts a game; 2.8 of them fall in the last 2:00 of the first half.

**The kneel rule against what teams did** (fourth quarter, leading)

| the engine's rule says kneel, and the defence has | real kneel share | snaps |
|---|---|---|
| 0 timeouts | 97.3% | 659 |
| 1 | 71.9% | 281 |
| 2 | 52.2% | 209 |
| 3 | 59.7% | 233 |

Where the rule says "do not kneel", teams knelt on 32 of 11,752 snaps. So the rule is right when the defence
is out of timeouts and wrong on about 0.30 snaps a game when it is not. That accounts for most of the
engine's extra 0.43 fourth-quarter kneels.

### 3.3 How big is the coach?

128 team-seasons, 56 head-coach stints, 24 season-to-season changes of head coach, 13 coach-moved-team pairs.

| tendency (neutral script) | split-half r | full-season reliability | year to year, same coach (n 72) | year to year, new coach (n 24) | coach who changed teams (n 13) |
|---|---|---|---|---|---|
| pass rate over expected | 0.63 | 0.77 | 0.40 | 0.24 | 0.14 |
| fourth-down go rate over the league cell | 0.30 | 0.46 | 0.53 | 0.25 | -0.05 |
| no-huddle rate | 0.95 | 0.98 | 0.67 | -0.24 | 0.08 |
| seconds to the next snap | 0.81 | 0.90 | 0.38 | 0.05 | -0.74 |

Reading it:

- **Each tendency is a real team-season trait.** The engine already carries two of the four (pass rate,
  pace) by team-season with a measured K.
- **A coaching change breaks the carry-over** for pace and no-huddle (0.05 and -0.24 against 0.38 and 0.67)
  and weakens it for the other two. The engine's prior ignores this: a team with a new coach starts on last
  year's pace and pass rate. That is a cheap, supported fix for weeks 1-6.
- **Portability is not shown.** On 13 pairs nothing travels with the coach. With correlations this noisy
  (SE about 0.3) that is absence of evidence. Eleven seasons (2016-2026) would give about 70 pairs.
- **Fourth-down aggressiveness is the one trait where the coach looks causal**: low within-season
  reliability (few decisions) but the highest same-coach persistence. Its true spread is about 2.8 points
  of go rate on a 15% base, about 0.14 decisions a game per SD.

### 3.4 Script-conditional usage (Phase 6's premise)

| share | neutral | leading 9+, Q4 | trailing 9+, 2nd half | inside 2:00 | 3rd and 5+ | inside the 5 |
|---|---|---|---|---|---|---|
| lead back, of the backs' carries | 63.3% | 44.2% | 55.6% | 53.9% | 50.5% | 64.1% |
| lead back, of team targets | 10.0% | 6.8% | 9.0% | 7.8% | 6.0% | 6.2% |
| all backs, of team targets | 17.3% | 14.5% | 17.8% | 14.7% | 13.4% | 10.1% |
| top target, of team targets | 24.4% | 23.0% | 21.4% | 22.5% | 24.0% | 23.6% |

The lead back's share of his team's passing-situation touches is a stable team trait (split-half r 0.67, 122
team-seasons). The engine deals every script at the neutral share.

### 3.5 The audit table, capability by capability

Status: **I** implemented, **P** partial, **R** previously tested and rejected, **M** missing, **U**
unsupported by available data.

**Phase 2: the coaching decision engine**

| capability | status | detail |
|---|---|---|
| head coach conditioning | M | no coach anywhere; data exists (5.1) |
| offensive coordinator | U | no licensed machine-readable feed (5.2) |
| team identity | P | pass rate (K 8.7 games), pace (K 4), kicker: `nfl_deep_data.py:104` |
| regime changes | M | priors carry over a coaching change unchanged |
| offensive and defensive strength | I | unit rates with measured exponents, `nfl_deep_sim.py:314-353`; strength does not enter any decision |
| score differential | P | five buckets; down 9 and down 24 are one |
| quarter and exact time | P | three time states plus hard rules |
| timeouts | M | 3.1 |
| down, distance, field position | I | 2.1 |
| opponent tendencies | P | the defence's allowed pass rate enters the call (exponent 0.78) |
| personnel | U in season | participation is published after the postseason |
| game importance | M | measurable only by proxy (week, division game, elimination); expect nothing |
| hierarchical pooling with a league fallback | P | by cell, in a fixed order that drops the score first; not by coach |

**Phase 3: offensive play-calling**

| capability | status | detail |
|---|---|---|
| run or pass by situation | I | the call table |
| neutral, positive, negative scripts | P | 3.2 |
| short yardage, goal line, red zone | I | distance 1-2, zones 1-2, 3-5, 6-10, 11-20; touchdown as a table sentinel |
| passing depth | M | one completion-yards draw; `air_yards` exists in nflverse |
| checkdowns and screens | M | FTN charts screens from 2022 (CC BY-SA); no checkdown flag anywhere |
| QB scrambles and designed runs | M in the engine | ESPN files a scramble as a rush. The rushing model split them and **no group passed its pre-registered 2025 gate** (`nfl_rush_model.md`): R for prediction, open for simulation realism |
| play-action, formation | M / U | FTN play-action from 2022; shotgun in season; personnel after the postseason |
| two-minute, no-huddle, hurry-up | P | one hurry-up histogram for every urgent situation |
| clock-burning, four-minute | P | one clock-kill histogram with the opponent's timeouts folded in |
| kneels | P | fourth quarter only; blind to timeouts; none before half-time |
| spikes | M | counted as incompletions in the clock fit, never called |
| end of half | P | the clock-expiring kick at 20 seconds |

**Phase 4: fourth down and special teams**

| capability | status | detail |
|---|---|---|
| go, punt or kick | I | the four-level table plus overrides; the table carries the frame bug (section 4) |
| coach-specific aggressiveness | M | the best-supported coach trait (3.3) |
| kicker range and accuracy | P | one league curve plus a kicker shift; range is a fixed 66 yards |
| score dependence | P | five buckets, hard rules late |
| two-point attempts | I | by the score after the six, early or late |
| onside kicks | P | all-or-nothing rule; real teams kick onside on 66% of those kickoffs, and 37% of real onside kicks fall outside it; recovery set at 12% against a measured 6.4% |
| overtime | I | both rule sets; no overtime timeouts |
| observed against optimal | M | nothing computes an optimum |

**Phase 5: clock management**

| capability | status | detail |
|---|---|---|
| timeout inventories | M | |
| strategic timeouts | M | |
| two-minute warning | I | |
| stoppages: incompletion, out of bounds | I | clock classes; the out-of-bounds rate is one league number, not higher in a two-minute drill |
| play-clock consumption | P | by tempo class only |
| hurry-up | P | |
| delay of game | P | inside the no-play penalty table (real: 0.66 a game) |
| end of half | P | |
| victory formation | P | |
| ruleset-correct overtime | I | less timeouts |
| deterministic scenario tests | P | guards cover the half-time kickoff, the untimed try and overtime; none for kneels against timeouts, spikes or timeouts |

**Phase 6: dynamic player utilization**

| capability | status | detail |
|---|---|---|
| usage by score, time, down and distance | M | 2.6 |
| red zone and goal line | P | touchdowns are dealt by each player's projected touchdown rate, not by where the play was |
| committees, third-down backs | P | in Sleeper's static shares only |
| backups, blowout substitutions | M | 44% against 63% (3.4) |
| fatigue | U | no in-season snap-level participation |
| two-minute personnel | M | |
| injuries | P | pre-game only (`availability`, the roster factor); in-game injuries cannot be known |
| recent usage | I | `nfl_usage.py`, passed its 2025 test |
| air-yards usage (WOPR) | R | lost to plain target share in every year (`nfl_usage.md`) |
| conservation laws | I | exact, guarded (`tests/guards_conservation.py`) |

**Phase 7: adaptive in-game decisions**

| capability | status | detail |
|---|---|---|
| any adaptation beyond score and clock | M | the per-game form tilt is drawn once, before kickoff |
| defensive matchup effects on play choice | R | coverage, shells, blitz, box, position allowances, similar defences: nothing beat Sleeper's line (`nfl_matchup_model.md`); only a weak game-script term on passing touchdowns ships |
| weather | R | failed stage 1 (`nfl_weather.md`) |
| hot hand | not proposed | the owner's instruction |

---

## 4. The registered refit did not happen

`nfl_viewer.md` 6.2 registered the fourth-down frame refit for 2026-11-06. Today:

- `research/data/nfl_deep_tables.json` reads `built: 2026-10-16 12:36 UTC`. No commit touches it after that.
- The sack table still nets a gain on 8.1% of sacks, to a maximum of +30.
- `static/nflview.js:45` still marks those sacks "artifact ... not yet refitted".

It matters here twice. A fourth-down policy cannot be judged against tables whose failed fourth downs are
stored in the other team's frame. And the deep weight's registered read expects the refit as a dated engine
change (`nfl_deep_weight_rule.md` section 6, point 9). nflverse's play-by-play does not have the defect: it
carries `fourth_down_failed` and the play's own yards. **When to run the production refit is the owner's
decision (section 9).**

---

## 5. Data availability

### 5.1 nflverse play-by-play (CC BY 4.0, release tag `pbp`)

372 columns. The 2026 file was last modified 2026-11-26 18:58 UTC: it updates in season. Fill rates on
scrimmage plays, the same in all four seasons:

| need | column | filled |
|---|---|---|
| head coach | `home_coach`, `away_coach` | 100% |
| timeouts remaining | `home_`/`away_`/`posteam_`/`defteam_timeouts_remaining` | 100% |
| who called a timeout | `timeout`, `timeout_team` (its own row) | every timeout row |
| no-huddle, shotgun | `no_huddle`, `shotgun` | 100% |
| kneel, spike, scramble | `qb_kneel`, `qb_spike`, `qb_scramble` | 100% |
| passing depth and side | `air_yards`, `pass_length`, `pass_location` | 53% of scrimmage plays, which is the passes |
| run direction | `run_location`, `run_gap` | 42%, 31% |
| two-point tries | `two_point_attempt`, `two_point_conv_result` | every try |
| onside kicks | no column; the word in `desc` on a kickoff, with `own_kickoff_recovery` | 171 found |
| play clock | `play_clock` | 100% (its exact meaning needs one check against the rule book) |
| clock | `half_seconds_remaining`, `end_clock_time` (97%), `time_of_day` | |
| out of bounds, delay of game | `out_of_bounds`, `penalty_type` | 100% |
| win probability, expected pass | `wp`, `xpass` | 100%, 99.6% |
| ball carrier, target, passer | `rusher_`/`receiver_`/`passer_player_id` | every play |
| closing spread and total | `spread_line`, `total_line` | 100% |
| coordinators | none | - |
| personnel, formation, box | none (the participation file) | - |

### 5.2 The other sources

| source | holds | seasons | in season | licence |
|---|---|---|---|---|
| nflverse participation | personnel groupings, formation, box, coverage, who was on the field | 2016-2025 | **No.** 2026 answers 404; 2025 was published 2026-02-10 | CC BY 4.0 |
| FTN charting via nflverse | play-action, screens, RPO, no-huddle, motion, QB location | 2022-2026 | Yes (modified 2026-11-26) | CC BY-SA 4.0, credit "FTN Data via nflverse" |
| nflverse snap counts | snaps per player per game | 2012 on | Yes | PFR-sourced: **display only** by the licence default |
| PFR, NGS | advanced splits | | | display only (CLAUDE.md, the rushing model) |
| ESPN compact records (`nfl_pbp`, fourteen ints) | down, distance, spot, clock, play class, yards, score, no-huddle and shotgun bits, kneel, spike, two-point bits | 2023-2026 | Yes | as used today |
| Sleeper | projections, weekly box scores | | Yes | as used today |

**Offensive coordinators.** No nflverse file carries them. PFR's coaching pages are display-only. The one
route that is licensed for automated reading is Wikipedia through its API (CC BY-SA 4.0, attribution), and it
is prose, not a table. The practical form is a small committed table, about 130 rows for 2023-2026, compiled
by hand with its sources and reviewed by the owner. Two cautions: the play-caller is often the head coach,
not the coordinator, and no feed records which; and one coordinator per team-season is confounded with the
team-season effect the engine already has.

**What ESPN's compact records lack**: who called a timeout and how many remain, the coach, air yards and
pass location, the ball-carrier and target, the play clock, scramble against designed run, win probability,
personnel. The coaching work cannot be built on them.

### 5.3 What is cached, and the PC fetch

- **This container**: nothing. `data/deep` holds one baseball file. Four seasons of play-by-play (70 MB)
  were fetched to scratch in under a minute; the audit reads all four in 7 seconds.
- **The PC's deep store**: ESPN compact seasons 2023-2026, and the matchup stores 2016-2026. The matchup
  stores keep runs, passes, kneels and spikes only, with no kicks, punts, timeout rows, timeouts remaining
  or coaches, and the raw files are dropped after the build (`nfl_matchup_data.py:555`).
- **The fetch to design** (not built here): a store of its own, `nfl_coach_<season>`, every play row
  including kicks, punts, no-plays and timeout rows, about forty integer columns, 8 MB a season, 2016-2026.
  It piggybacks on `matchups.nfl_data_job`, which already downloads the same file daily, so there is no
  second download. Until a phase is accepted, research scripts read the raw files from a directory, as the
  audit script does.

### 5.4 Verdict by phase

| phase | verdict |
|---|---|
| 2 coaching engine | Head coach: supported in season. Coordinator: a hand table or nothing. Personnel: prior seasons only. Game importance: proxies only. |
| 3 play-calling | Supported in season, including depth, scrambles, kneels, spikes, no-huddle and (FTN, 2022 on) play-action and screens. Personnel-conditioned calls: prior seasons only. Checkdowns: unsupported. |
| 4 fourth down, special teams | Fully supported. |
| 5 clock | Fully supported. |
| 6 utilization | Who got the ball by situation: supported in season. Fatigue and on-field personnel: unsupported in season. In-game injuries: unknowable before kickoff. |
| 7 adaptive | The data exists; the evidence is expected to be thin. |
| 8 validation | Supported; the seasons are in 7.1. |
| 9 screen | No new data. |

---

## 6. The gaps, ranked

Game prediction first, honestly: the deep engine's level is anchored to the market, its walk-forward log
loss sits within 0.0014 +/- 0.0045 of the ratings it is anchored to (`nfl_deep_validation.md`), and
cross-referencing models adds nothing to the closing price (`game_xref.md`). Nothing below moves the team
level. The deep weight's own rule needs 390 games to detect 0.015 nats. **No item here can be shown to
improve the pre-game moneyline on any sample we will have.** What can be tested with power is the engine's
behaviour from a given late-game state, where thousands of states exist.

| rank | gap | game prediction | DFS |
|---|---|---|---|
| 1 | Time state 1 pools the two-minute drill with the early fourth quarter; the hurry-up and clock-kill histograms are each one size for all | Small. Moves points between windows, not the level. May close part of the total SD gap (12.6 against 13.7). | **High.** About 1.5 dropbacks a team a game are missing from the two-minute drill, and leaders throw too much in the fourth quarter: both distort who scores when. |
| 2 | No timeout inventory: the kneel rule, the leader's runoff and the trailer's last drive are blind to it | Small on average. Matters for the conditional chance of a comeback from a given state, which is what the viewer shows. | Medium: the last drive's volume is all-or-nothing in real games and averaged here. |
| 3 | Usage is dealt at one share for every script | None. | **High for tails and correlation.** The lead back gets 63% of blowout carries here against a real 44%; third-down and two-minute targets go to the wrong back. Team totals are unchanged, so means barely move. |
| 4 | The fourth-down frame bug (registered, overdue) | Small, known: a failed fourth down hands over the ball about 15 yards off; 2.2% of sacks gain 30. | Small. |
| 5 | Coach-specific fourth-down aggressiveness | Negligible: 0.14 decisions a game per SD. | Small: kicker attempts, a few plays. Real for the viewer. |
| 6 | Regime change does not reset the pass-rate and pace priors | Negligible. | Small, weeks 1-6 only. |
| 7 | Spikes, half-time kneels, the onside rule and its 12% | Negligible each. | Negligible; realism for the viewer. |
| 8 | Passing depth, screens, play-action, scramble against designed run | None expected: the rushing model's split failed its gate. | Low to medium: depth drives the yards-per-catch split between receivers, which the deal draws as noise today. |
| 9 | Coach-specific play-calling beyond the team's own rate | None: the team rate already holds it. | None. |
| 10 | Coordinators, personnel, game importance, fatigue | None. | None, and the data is missing. |

---

## 7. The plan

### 7.1 The seasons, for every phase

- **Fit**: 2016-2023 (nflverse play-by-play; the kickoff eras and the 2025 overtime rule handled as eras).
- **Selection**: 2024.
- **Confirmation**: 2025. It can block, never promote. CLAUDE.md: 2025 is SEEN for what the DFS engine does
  now, and this audit has read its league aggregates.
- **Seen by this audit**: 2026 weeks 1-12, league aggregates only. Usable for confirmation, never as the test.
- **The test**: 2026 week 13 on, the postseason, and 2027. Sealed: every fit and selection script filters
  `season == 2026 and week >= 13` out, and the harness refuses a dirty tree and refuses to read the sealed
  rows before the candidate's commit hash is registered (as `research/nfl_dfs_engine_eval.py` does for
  2025). The games being played before the candidate is frozen does not matter; reading them does.
- **Both arms refit on the same past seasons** for every comparison, as `research/nfl_deep_validate.py`
  does. The production tables contain 2025 and part of 2026 and cannot be the control on those games.
- **Power, stated now**: 2026 weeks 13-18 are 96 games. That is enough for decision-level and state-level
  tests (thousands of rows) and for the DFS gate's links at the 63-game precision the passing-volume anchor
  was shipped on. It is not enough for any pre-game win-probability claim, and none is proposed.

### 7.2 Versioning, for every phase

- A separate experimental engine, `nfl_coach_sim.py`, importing `nfl_deep_sim`'s samplers and replacing the
  snap loop. Its own tables (`research/data/nfl_coach_tables.json`), its own data store, its own research
  scripts (`research/nfl_coach_*.py`), its own guards file.
- `nfl_deep_sim.py`, its tables and `nfl_deep_data.py` are not edited. A guard pins `play_game`'s boxes on
  fixed seeds to a committed digest, so "byte-identical by default" is checked, not asserted.
- **The deep weight's read is not touched.** The experimental engine never writes to the `nfl_deep` shadow
  bucket and never changes the figure logged there. If it is ever logged live, it is to a bucket of its own
  (`nfl_coach`), which is a production change needing the owner's word.
- DFS: `dfs_tourney.BOARD_MODEL` stays "engine". The experimental worlds exist in the research harness only.

### 7.3 Phase 5 and Phase 3, league level first (clock, timeouts, urgency)

- **Model.** State gains each side's timeouts (3 a half, 2 in a regular-season overtime, 3 per two periods
  in the postseason) and the running or stopped clock. Decisions become probabilities from league-level
  logistic models, exported as lookup tables so the engine stays pure Python:
  - play call: additive in down, distance, zone, seconds left in the half (fine bins), score (fine bins),
    both timeout counts, and the closing spread as a strength proxy. This replaces the pooled cell.
  - tempo: the gap to the next snap given the clock is running, by seconds left and score, with timeouts
    taken out of the histogram.
  - timeout: offence and defence separately, given the state and the inventory.
  - kneel: allowed when the remaining kneels outlast the clock net of the opponent's timeouts and the
    two-minute warning; also before half-time, at the observed rate.
  - spike: given a running clock, no timeouts, and the seconds left.
  - out of bounds: by situation.
- **Free parameters**: `_FORM_SD` and `_HFA_YDS` are refit for the new engine by the same procedure that
  set them, on fit seasons only, before any held-out game is read.
- **Ablations**, each against the control refit on the same seasons: (a) time-state fix only; (b) plus
  tempo; (c) plus timeouts and the kneel rule; (d) plus spikes and out-of-bounds.
- **Acceptance criteria to register**:
  1. Every scenario test in 7.9 passes. A gate, not a statistic.
  2. **Fidelity** on the sealed games, a list fixed in advance: snaps and points inside 2:00 of each half,
     points by quarter, kneels by quarter, timeouts used per team-half, plays a game, and the pass rate in
     the 50 clock-by-score cells of 3.2. The candidate must be closer to the real figure than the control
     on the four inside-2:00 figures, each by more than the real figure's own standard error.
  3. **Must not worsen**: points a game, margin SD, total SD, home win rate, overtime rate, each within one
     standard error of the control's distance from real.
  4. **State-conditional win probability**: from the real state at fixed checkpoints (start of the fourth
     quarter, 5:00, 2:00), both engines play the game out; paired log loss on the real result, clustered by
     game. The candidate must win by more than 2 SE pooled over the 5:00 and 2:00 checkpoints.
  5. **Pre-game**: moneyline log loss and total CRPS not worse than the control by more than 1 SE. A
     non-inferiority bound. No gain is claimed or expected.
- **Compute.** The fit: minutes on the PC. Criterion 4: about 100 games x 3 checkpoints x 2,000 play-outs x
  2 arms, about 20 core-minutes. Criterion 5: the walk-forward, about 10 core-minutes. Small enough for
  either machine; the PC by the rule.
- **Risks.** The level moves when the end of the half is repaired, so the two free parameters must be refit
  or the comparison is confounded. The engine must start from an arbitrary state for criterion 4, which is
  new code with its own guard (a play-out from a game's opening state reproduces `play_game`). Speed: the
  DFS worlds are 180,000 a slate, and a snap loop 20% slower is 20% more PC time.

### 7.4 Phase 6 (script-conditional usage)

- **Model.** The engine counts each team's box by script bucket as it plays (counters only, as `td_def`
  was added): neutral, leading late, trailing, inside 2:00, third and long, inside the 5. The deal runs per
  bucket with the player's share multiplied by a bucket multiplier. Multipliers are hierarchical: a role
  level (lead back, second back, top target, other wide receivers, tight end) fitted on the league, and a
  team-role deviation shrunk by a K measured split-half, from this season's nflverse plays. Team totals in
  every bucket are dealt exactly, so the conservation guards hold unchanged.
- **Two stages, as `nfl_usage` did it.** Stage 1: the multinomial log loss of who actually got the ball, by
  bucket, against the static share. Stage 2 only if stage 1 passes: the DFS gate.
- **Ablations**: role level only; plus team-role deviations; carries only; targets only.
- **Acceptance criteria to register**:
  1. Stage 1: log loss per team-game below the static share's by more than 2 SE on 2025, same sign on the
     sealed games.
  2. Stage 2, on the sealed weeks with `nfl_dfs_engine.md`'s own four links: offence CRPS and pair-correlation
     RMSE both better on the point estimate, defences and kickers not worse by more than 1 SE.
  3. A tail link, new: the Brier score of "this player lands in his own top decile", not worse.
- **Compute.** Stage 1: minutes. Stage 2: the DFS evaluation harness, hours on the PC.
- **Risks.** Player ids must join Sleeper to nflverse (the crosswalk in `nfl_matchup_data.build_ids` exists).
  The recent-usage blend and the bucket multipliers could double count a role change. Small buckets for
  backups.

### 7.5 Phase 4 (fourth down and special teams)

- **Model.** A multinomial logit for go, punt or kick: additive in distance, field position, seconds left,
  score and timeouts, plus a head-coach random effect on "go" (one intercept, and one slope for "short
  yardage in plus territory" only if it earns it). Shrinkage by a measured K; the coach's prior is his own
  earlier seasons at the measured same-coach slope, else the league. The kick's make curve gains the kicker's
  range. The onside decision becomes a rate by deficit and time, and recovery a measured rate by rules era.
- **Observed against optimal.** The simulation always plays observed behaviour. Separately, for display, the
  experimental engine values each choice by play-outs from the state at league-average strength. It is
  labelled as this engine's opinion, never as the truth.
- **Ablations**: league model against the current table; plus coach intercept; plus the slope.
- **Acceptance criteria to register**: log loss per fourth-down decision. League model against the table:
  better by more than 2 SE on 2025 and the same sign sealed. Coach effect against the league model: better by
  more than 2 SE clustered by team-season on 2025, same sign sealed. Otherwise the coach effect ships as
  display only.
- **Prerequisite.** The control's table carries the frame bug, so the control here is the table refit from
  nflverse rows, not the production table.
- **Compute.** Minutes. **Risks.** About five neutral decisions a team a game: a coach's first six games
  say little, and the prior does the work.

### 7.6 Phase 2 (coach effects on play-calling, tempo and timeouts)

- **Model.** The Phase 3 and 5 league models plus random effects by team-season and by head coach, on:
  pass rate (intercept, and a slope on "leading"), tempo, no-huddle, and timeout thrift before half-time.
  The team-season effect is what the profile carries today; the coach effect is the new term, and the
  regime-change rule (a new head coach resets the prior toward the league or the coach's own history) is
  its cheapest form.
- **Ablations**: regime-change reset only; coach intercepts; coach slopes.
- **Acceptance criteria to register**: decision-level log loss on weeks 1-6 of the sealed and following
  season for the reset (the only weeks it can matter); for coach terms, better than team-season alone by
  more than 2 SE on 2025 and the same sign sealed.
- **Expectation, written now**: the reset has the best chance. Coach intercepts beyond the team-season
  effect are expected to fail (3.3). If they fail, the coach is shown, not simulated.
- **Coordinators**: out of the model unless the owner approves the hand table, and then tested as one more
  random effect by the same rule.
- **Risks.** Confounding of coach with quarterback and roster. Thirteen movers in four seasons: the PC fit
  on 2016-2026 is what gives the coach term a chance.

### 7.7 Phase 7 (adaptive in-game decisions)

Three hypotheses at most, each written with its kill rule before any number:

1. A defence that has been on the field longer allows more yards late (plays faced, time of possession).
2. An offence leans toward what has worked this game beyond what score and clock explain (success rate of
   earlier runs predicting the next call).
3. A quarterback under early pressure throws shorter later (sacks and hits so far predicting air yards).

- **Test**: does the in-game term improve the decision or outcome log loss over the Phase 3 model that
  already knows score, clock and pre-game strength? More than 2 SE on 2025 and the same sign sealed, or it is
  dropped and listed as rejected.
- **Expectation**: mostly nulls. The matchup and rushing work found nothing of this kind beyond Sleeper's
  line. No hot-hand term is proposed.

### 7.8 Phase 8 (integration)

After the phases above have each been kept or dropped: one frozen experimental engine, one registration,
one read of the sealed games under 7.3's criteria 2-5 and 7.4's stage 2. Then, only with the owner's word,
a live shadow bucket of its own. Promotion into the headline or the DFS boards is a second registered
test on games after that, by the pattern of `nfl_deep_weight_rule.md` section 6.

### 7.9 The deterministic scenario tests

Each builds a state by hand, plays it over many seeds, and asserts an invariant.

| # | scenario | must hold |
|---|---|---|
| 1 | Fourth quarter, leading by 1, first down, 1:55, opponent has no timeouts | kneels every snap; the game ends with no further play; the leader wins in every seed |
| 2 | The same with the opponent holding 3 timeouts | no kneel on first down; the opponent's inventory falls by one for each timeout and never below zero |
| 3 | Trailing by 4, 0:25, clock running after an in-bounds catch at midfield, no timeouts | the next snap is a spike or a hurried snap, never a huddle; a spike uses a down and stops the clock; no spike on fourth down or with the clock stopped |
| 4 | Leader runs in bounds at 2:30 of the fourth; the trailing defence has 3 timeouts | a timeout follows at close to the observed rate; three at most; none once they are gone |
| 5 | A play ends in bounds at 2:05 of the second or fourth quarter | the clock stops at exactly 2:00 once; never in the first or third quarter; never twice in a half |
| 6 | Regular-season overtime | a 10:00 period; both teams possess, including after an opening touchdown; then sudden death; a level score at 0:00 is a tie; two timeouts a team |
| 7 | Postseason overtime | 15:00 periods; never a tie; play continues until a winner |
| 8 | Half-time: 0:03 left, ball at the opponent's 25 | the field goal is attempted |
| 9 | Half-time: 0:20 left at the own 20, leading | a kneel is possible; no punt with time for one snap |
| 10 | Every game, 10,000 seeds | timeouts used per team-half at most 3; elapsed clock exactly 3,600 seconds plus overtime; no snap at or past a goal line; scores equal the sum of scoring events |
| 11 | The passive log | every box identical with and without the log (the viewer's existing guard, extended to the new events) |

---

## 8. Phase 9: the screen

**Where.**

- **The Matchups tab's NFL game page** (`static/matchups.js`, one boardshare piece a game) takes the
  **Coaching intelligence** section: it is where the owner already reads offence against defence.
- **The NFL card** gets one line and a link to it, beside "Watch the typical game".
- **The viewer** (`static/nflview.js`) gets the per-play explanation.

**The Coaching intelligence section, per team.**

- Head coach and games in the post (nflverse). Play-caller only if the hand table is approved.
- Each tendency as a figure against the league, with its sample size and how much of the figure is the
  coach's own data against the prior ("62% his games, 38% league"): pass rate over expected in a neutral
  script, pace, no-huddle rate, fourth-down go rate over the league cell (and by field zone), two-point
  tries, timeouts unused at half-time, play-calling with a late lead.
- A "new regime" chip in the first six games after a coaching change, and a low-evidence chip under a set
  sample size.
- Fourth downs: observed rate beside this engine's play-out valuation, the second labelled as the engine's
  opinion.
- Each tendency says whether the simulation USES it or only shows it. After the tests, most may be shown only.
- Credit: "nflverse-data (CC BY 4.0)", and FTN's line where a charted field is shown.

**"Why this play" in the viewer.** The engine does not reason: it draws a decision from a probability and
then draws an outcome. The explanation must say exactly that and nothing more.

- **The call**: "Pass. The engine throws here 71% of the time: 64% for second and 8 at midfield, +9 for
  trailing by 4 with 3:10 left, +3 this coach, -5 this defence." The parts are the model's own additive
  terms, converted from the logit, so they are the real decomposition and not a story.
- **The draw**: "drawn at 71%" on every play, so a 29% call reads as an unusual choice and not as an error.
- **Fourth down**: "Go 38%, punt 55%, kick 7%. Drew go. League 24% here; this coach +14."
- **Clock rules**, named when they fire: "Timeout, Denver (2 left): trailing defence at 2:41." "Kneel: three
  kneels outlast 1:52 and Chicago has no timeouts." "Spike: clock running, no timeouts, 0:14."
- **Never**: a reason the engine does not have (a matchup it did not read, momentum, intent).
- **Mechanics**: the same passive log (`play_game(log=)`), with the probability and its parts added to each
  event. No draw is added and the existing guard (every box identical with and without the log) covers it.
  The typical-game selection (`DIMS`) is untouched.

---

## 9. What needs the owner's decision

1. **The order.** This plan does clock and urgency first, coach effects fourth, because the measurements put
   the value there. The owner's list leads with the coach.
2. **The overdue refit** (`nfl_viewer.md` 6.2). Run it on production now as registered, or after the deep
   weight's 100-game read so that read sits on one engine. The rule handles either; it reports the split.
3. **Coordinators.** Approve a hand-compiled committed table (sources cited, Wikipedia's CC BY-SA
   attribution), or leave coordinators out. Recommended: out of the model, and shown only if the table exists.
4. **The test set.** Accept 2026 week 13 on, sealed, as the test (96 regular-season games: enough for
   decision-level and DFS-link tests, not for any win-probability claim), or wait for 2027.
5. **A live shadow bucket** for the experimental engine once it passes: a second set of simulated games a
   matchup on the PC.
6. **The onside constant.** 12% is called "a rule, not fitted"; real recovery measured 6.4% (11 of 171, SE
   1.9). Changing a production constant is a production change: fold it into the refit, or leave it.
7. **Personnel and formation.** In-season data is shotgun and no-huddle only. Personnel-conditioned calls
   would run on last season's groupings. Recommended: display only.
8. **PC time.** The DFS-link evaluation is hours of the PC per ablation arm.

---

## 10. Limits of this audit

- The engine probe is the league against itself; real games mix strengths. Mismatches add garbage time, not
  two-minute drills, so the -25% to -33% is not explained by it, but the comparison is not paired.
- "Seconds to the next snap" excludes real gaps with a timeout and cannot exclude them in the engine, which
  is the finding rather than a flaw, but the two columns are not the same quantity.
- The fourth-quarter 3:00-2:00 clock cells are selected by the two-minute warning on both sides and are not
  quoted.
- The coach table rests on four seasons. Its correlations carry standard errors of 0.1 to 0.3.
- "Backs" and "lead back" are defined from the play-by-play alone (more carries than targets, top carrier),
  not from a depth chart.
- Nothing here was fitted, and no held-out game was scored.

## 11. Reproduce

```
# fetch play_by_play_<season>.csv.gz for 2023-2026 from nflverse-data's "pbp" release into <dir>
python3 -m research.nfl_coaching_data_audit <dir>
VIGIL_NO_BG=1 python3 -m research.nfl_coaching_engine_probe --n 4000
```

Data credit: nflverse-data (CC BY 4.0).
