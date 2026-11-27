# NFL coaching engine, R2: the evidence audit, the plan, and what was built

Written 2026-11-27. **Research only: no production file changed, and no board, viewer, DFS world or predlog
bucket reads any of this.** R1 (`nfl_coach_sim.py`, `coach-r1.0`) and every file its registration pins are
byte-identical (guard NR0a). The seal is R1's: 2026 week 13 on, the 2026 postseason and every later season are
never read here (`research/nfl_coach_r2_data.py` drops those rows on their season, week and season-type fields,
before any other field is parsed; guard NR7). **No registered read has been run.**

Data credit: nflverse-data play-by-play and players (CC BY 4.0); FTN Data via nflverse (CC BY-SA 4.0). Raw files
are gitignored; only fitted tables and aggregates are committed.

## 0. The short version

1. **Ten mechanisms audited on 2016-2025 (2026 weeks 1-12 where it helps).** Three are supported strongly enough
   to act on, and their order is set by what they move:
   - **Who gets the ball by situation** (field zone, clock, score, down): the clearest and largest effect, and the
     one that moves DFS player production. **Built as R2** (`nfl_coach_sim_r2.py`, `coach-r2.0`).
   - **In-game adaptation of the call**: an offence leans toward what has worked this game, beyond the score, clock,
     down, distance and its season lean. A placebo built from the rest of the game reads zero, so this is a
     response, not a common factor. **Registered at the decision level only, not in any engine.**
   - **Head-coach tendencies and regime changes**: pass rate over expected, no-huddle, pace and fourth-down
     aggressiveness are team-season traits that carry over with the same head coach and break with a new one.
     **Planned for R3**, not built: nothing in R1's league-level engine consumes a team tendency yet.
2. **Not supported, so not built:** offensive coordinators (no lawful structured source reachable), team or coach
   deviations in usage, the goal-line specialist's own in-season deviation, fatigue, halftime adjustments,
   adaptation to the opponent's strengths, defensive tendencies as a player-production input, and any interaction
   between the two staffs. Each says why in section A.
3. **R2, development** (2024 / 2025 / 2026 weeks 1-12). Who got the ball on each real snap, the engine's own deal
   against the static share on the same base: **+1.08 / +0.81 / +0.90 nats a team-game (z 12.2 / 10.5 / 7.7)**.
   On simulated games, the realism of the backs' carries, the top receivers' targets and their touchdowns is not
   worse in any season, and the touchdown log score is better in two of three. Conservation is exact in every game.
4. **The registration** (`research/data/nfl_coach/prereg_r2.json`, block `84144a48...`): H1 (R2) reads 2026
   weeks 13-18 on or after 2027-01-06 06:00 UTC, **only after R1's own read** of those games; H2 (adaptation)
   reads 2026 weeks 13-18 plus 2027 weeks 1-18 on or after 2028-01-13. Power: H1 about 0.8 (criteria 2-3 above
   0.99; the three 1-SE "not worse" bounds are the risk); H2 0.47 to 0.89.
5. **For the owner:** whether R1's sealed weeks may carry R2's player-level read after R1's read (section C.1).
   If not, H1 moves to 2027 weeks 1-9, and the registration is rewritten before either window is read.

## A. The evidence audit

Methods, common to every section. Regular-season games, 2016-2025 (320 team-seasons). A tendency is measured
against its own league-season mean, because the league's pass rate and fourth-down aggressiveness moved over the
decade. "Neutral" snaps: downs 1-3, nflverse's `xpass` known, win probability 0.2-0.8, more than 2:00 left in the
half, regulation. Split-half is odd games against even games within a team-season; "reliability" is its
Spearman-Brown full-season figure; K is the games at which a team's own figure counts as much as the league's
(within-team per-game variance over the true between-team variance). Year-to-year pairs are classified by the
head coach of the season's last game against the next season's first. Code: `research/nfl_coach_r2_evidence.py`;
every number below is in `research/data/nfl_coach/r2_evidence.json`.

### A1. Individual head-coach and offensive-coordinator tendencies

| tendency (neutral) | true SD | split-half r | reliability | K (games) | year to year, same HC (n 220) | new HC (n 61) | HC who moved (n 12) |
|---|---|---|---|---|---|---|---|
| pass rate over expected | 4.3 pts | 0.70 | 0.82 | 4.4 | 0.51 +/- 0.05 | 0.11 +/- 0.13 | 0.06 +/- 0.32 |
| fourth-down go rate over the league cell | 1.9 pts | 0.34 | 0.50 | 30 | 0.47 +/- 0.05 | 0.03 +/- 0.13 | 0.09 +/- 0.31 |
| no-huddle rate | 8.9 pts | 0.91 | 0.95 | 1.0 | 0.72 +/- 0.03 | -0.03 +/- 0.13 | -0.02 +/- 0.32 |
| seconds from an in-bounds run to the next snap | 1.17 s | 0.66 | 0.79 | 5.1 | 0.52 +/- 0.05 | 0.20 +/- 0.13 | 0.08 +/- 0.31 |
| two-point tries per offensive touchdown | 1.8 pts | 0.23 | 0.38 | 181 | 0.22 +/- 0.07 | 0.33 +/- 0.12 | -0.02 +/- 0.32 |

- **Each of the first four is a real team-season trait** that carries over when the head coach stays and does not
  when he changes. The gap (same minus new) is 0.40 for pass rate over expected (about 2.9 SE), 0.44 for fourth
  downs, 0.75 for no-huddle and 0.32 for pace. This is the regime change in A2.
- **Two-point aggressiveness is not a measurable trait** at this sample: K is 181 games (eleven seasons), and the
  carry-over is no larger with the same coach than with a new one.
- **Portability is not shown.** Twelve head coaches changed teams with a full season at each end. With a standard
  error near 0.3, nothing distinguishes "the coach brings his tendencies" from "he does not". This is absence of
  evidence, not evidence of absence.
- **Shrinkage.** Pass rate over expected and pace are readable in about 4-5 games and no-huddle in one. Fourth-down
  aggressiveness needs about 30 games, nearly two seasons, before a coach's figure counts as much as the league's.
  The same-coach carry-over (0.47) is what makes his previous seasons the right prior.
- **What it moves.** One true SD of pass rate over expected is about 1.5 passes a game on 34 neutral snaps. That
  shifts touches between backs and receivers, but the engine already carries a team-season pass rate
  (`nfl_deep_data`, K 8.7). The coach adds only to the prior, which is A2.
- **Offensive coordinators: no lawful structured source was reachable.** nflverse carries head coaches only.
  Pro-Football-Reference is display-only by this codebase's licence default. Wikipedia's robots.txt
  disallows `/w/` and `/api/` for generic agents and allows article pages. One article request from this container
  was answered 429 (rate-limited egress), so I did not verify that the team-season infoboxes carry coordinators,
  and I fetched nothing more. A hand-compiled table with a cited source per row is still possible, but it is the
  owner's call (audit section 9.3). It also has a structural limit: no source records who actually calls the
  plays, and one coordinator per team-season is confounded with the team-season effect. The upper bound on what a
  coordinator could add: with the same head coach, the year-to-year r (0.51) against the full-season reliability
  (0.82) says about 62% of the true pass-rate variance carries over. Coordinator, roster and quarterback changes
  share the other 38%.

### A2. Coaching regime changes

The prior a team carries into weeks 1-6 is last season's deviation times a persistence. Production uses one pooled
persistence. Here it is fitted leaving the predicted season out, pooled against split by regime. The error is
the play-weighted squared error of the weeks 1-6 rate, over 288 team-season pairs (61 with a new coach).

| stat | persistence: pooled / same HC / new HC | MSE pooled -> by regime | gain, all teams | gain, new-coach teams |
|---|---|---|---|---|
| no-huddle | 0.57 / 0.81 / 0.02 | 0.00614 -> 0.00492 (-20%) | 2.1 SE | 2.0 SE |
| pass rate over expected | 0.52 / 0.59 / 0.15 | 0.00236 -> 0.00231 (-2.2%) | 1.1 SE | 1.1 SE |
| pace | 0.54 / 0.57 / 0.40 | 2.346 -> 2.347 | 0.05 SE | -0.04 SE |
| fourth-down go | 0.42 / 0.48 / 0.08 | 0.002249 -> 0.002253 | 0.0 SE | -0.2 SE |

- **Supported, and cheap:** a new head coach's team should start near the league in no-huddle, and nearer it in
  pass rate over expected, than last season's team did. Pace in weeks 1-6 carries even across a change (0.40),
  and fourth downs are too few in six weeks for any prior to show.
- **What it would change:** about 20% of the early-season error in no-huddle, and about 2% in pass rate. For DFS
  that is roughly 0.5-1 snap a game for an extreme new-coach team in weeks 1-6. It is real but small, and it
  matters only once an engine reads team tendencies. **Planned as R3 (section D), not built.**

### A3. Situational player and personnel utilization

**Who gets the ball.** Each real snap is dealt among the team's players who touched the ball that game. Each
player's weight is his as-of carries or targets (strictly earlier games this season, from the team's 4th game
on), by situation. The ten situations (`nfl_coach_sim_r2.bucket`) take field zone first, because zone decides
touchdowns: inside the 2, 3-5, 6-10, the last 2:00 of a half, leading 17+ in the second half (or 14+ in the
fourth), leading 9+ in the fourth, trailing 14+ in the second half, passing downs, short yardage, and everything
else.

The lead back's realized share of the team's carries, fit seasons 2016-2023 (85,651 carries):

| | neutral | inside 2 | 3-5 | 6-10 | 2:00 | lead 17+ | lead 9+ Q4 | trail 14+ | pass down | short yd |
|---|---|---|---|---|---|---|---|---|---|---|
| RB1 | 58.9% | 54.8 | 55.9 | 54.2 | 41.0 | 43.1 | 56.2 | 46.7 | 46.0 | 43.0 |
| RB2 | 23.1 | 19.2 | 20.5 | 21.3 | 24.8 | 29.9 | 25.9 | 23.3 | 20.6 | 19.1 |
| QB | 7.1 | 15.4 | 13.7 | 13.1 | 18.4 | 4.9 | 6.9 | 13.5 | 22.8 | 23.1 |

Model: weight times a role x situation multiplier, maximum likelihood (iterative scaling) on 2016-2023, against
the role-only constant on the same snaps. Per team-game, in nats:

| | 2024 | 2025 | 2026 wk 1-12 |
|---|---|---|---|
| carries: role x situation vs role only | 1.05 +/- 0.08 (z 12.6) | 0.83 +/- 0.07 (11.5) | 0.84 +/- 0.11 (7.8) |
| targets: role x situation vs role only | 0.12 +/- 0.03 (4.3) | 0.08 +/- 0.03 (2.8) | 0.16 +/- 0.04 (4.2) |

- **Supported in all three held-out seasons**, carries strongly and targets clearly.
- **The carries effect is the quarterback and the backs trading places.** The QB carries 2.5x his neutral
  propensity inside the 2 (sneaks) and 4-5x on passing downs and short yardage (scrambles and sneaks). The second
  back's propensity against the lead back's rises 1.9x when leading by 17+ and 1.7x in the two-minute drill.
- **Targets:** the second tight end's propensity at the goal line is 2.5x his neutral one, the lead back's half.
  The third receiver and the second back rise in the two-minute drill and when trailing.
- **Selection caveat** (it bounds what the numbers mean): the candidates are the players who touched the ball that
  game, a look-ahead both arms share. It inflates the third back's blowout multiplier, because he is included only
  in games he played. The engine uses the multipliers only to redistribute a given mean (B.1). The "core"
  measurement in B.3, which leaves out every player but the lead two backs, the quarterback and the top receivers,
  holds about 70% of the gain.
- **Personnel and formation** (who is on the field): nflverse participation (CC BY 4.0) covers 2016-2025. It is
  published after each postseason (2026 answers 404), so in season it is last year's groupings only. FTN (CC BY-SA
  4.0) is in season from 2022 but charts play type (play action, screens, RPO, motion, box count), not personnel.
  **Not used:** an in-season personnel model would run on last season's groupings (audit 9.7, recommended display
  only).

### A4. Running-back committees and goal-line specialists

- **The lead back's share is a team-season trait** (audit 3.4: split-half 0.67 on passing-situation touches). R2
  deals each team at its given shares, so committees are whatever the input says.
- **The goal-line specialist is real but thin.** Per back (40+ carries, 727 team-seasons), his share of the team's
  carries inside the 5 minus his overall share: split-half r 0.30 +/- 0.03, full-season reliability 0.46, true SD
  8.8 points. A team has about 23 carries inside the 5 a season, so the shrinkage needs about 27 of them: more than
  a season.
- **In season it adds nothing measurable.** Each back's own as-of inside-10 deviation, shrunk with K chosen on 2024
  (20 carries), adds +0.005 +/- 0.005 (2024), +0.018 +/- 0.005 (2025) and +0.003 +/- 0.007 (2026) nats a team-game to the role x situation model. That is 1/50th of the
  situational gain, inside 1 SE in two of three seasons. **Not built.** Last season's figure for the same
  back on the same team is the natural prior (R3 candidate, section D).
- **DFS translation:** dealt over the team's actual inside-5 carries, the role x situation model moves a lead
  back's expected inside-5 carries by only 0.035-0.04 a game against the role-only model. Its squared error is
  better in 2024 and worse in 2025 and 2026. **At the role level, "goal-line back" is not where the gain is.** The
  quarterback is: his inside-the-5 share is 13.7-15.4% against 7.1% neutral.

### A5. Blowout substitutions

- **Backs:** leading by 17+ the lead back's share falls from 58.9% to 43.1%, and the second and third backs take it
  (table in A3). Dealt over each team's actual blowout carries, the bucket model's error on the lead back's blowout
  carries is 18-29% below the role-only model's in all three seasons (2024 1.31 -> 1.08, 2025 1.44 -> 1.18, 2026
  2.06 -> 1.47 squared carries a game). The error on the quarterback's blowout carries falls 41-60%.
- **Is the habit the coach's?** Each team-season's lead-back drop with a 14+ lead: observed SD 0.20 against a
  sampling SD of 0.09, so teams differ. But year to year with the same head coach it is 0.03 +/- 0.08 (n 149). **No
  evidence that resting starters is a coach's persistent trait**; it is league behaviour plus the season's own
  roster. Only the league level is built.
- **Quarterbacks:** the as-of starter takes 96.2% of the dropbacks within 8 points in the fourth quarter. He takes
  78.5% leading by 17+ (1,545 dropbacks) and 81.2% trailing by 17+ (5,918). That is real, but it is a persistent
  switch (once pulled, he stays out) for a player production's player lists rarely carry. **Not built in R2:** it
  needs a pull model and a backup quarterback in the list (section D). The average effect is about 0.3 dropbacks a
  game, and more in a blowout world.

### A6. Fatigue and workload

- **Efficiency by carry number**, among a back's 18+ carry games (so every bin reads the same games), Q1-Q3 and
  within 8 points, success over the league cell: carries 1-6 +3.8 pts (SE 0.5), 7-12 +2.9 (0.7), 13-18 0.0 (1.0),
  19+ -3.0 (2.9). Yards a carry over the cell: +0.33, +0.33, +0.18, -0.38 (SE 0.07-0.38).
- **The decline is confounded.** A back reaches 18 carries partly because his early carries worked (selection on
  the early bins), defences adjust, and the late carries come later in the game. The yards decline is 0.15 a carry
  over carries 13-18: about one yard a game. **No evidence of a fatigue effect worth modelling.**
- **Rotation within a game:** after the game's lead back has carried once, the next team carry is his 10.3 points
  above his game rate. After four or more in a row it is 2.3 below. Backs stay in for a series, then rotate. This
  changes when a back carries, not how often, and is not modelled.
- **On-field snap counts** (the real workload) are PFR-sourced through nflverse: display only by the licence
  default.

### A7. Halftime adjustments

- **No break at halftime.** Each team-game's quarter figures minus its team-season mean, correlated between
  adjacent quarters (about 4,000 team-games each; SE about 0.015):

  | | Q1-Q2 | Q2-Q3 (halftime) | Q3-Q4 |
  |---|---|---|---|
  | EPA a play | 0.010 | 0.012 | -0.005 |
  | pass rate over expected | 0.104 | 0.130 | 0.204 |

  Efficiency does not persist within a game in any quarter pair, so there is nothing for an adjustment to break.
  The call's lean persists across halftime no less than within a half.
- **Second-half lean against the first half's run-minus-pass EPA:** -0.037 +/- 0.010 (z -3.5), that is -0.9
  points of pass rate over expected per SD. It is the in-game adaptation of A8 seen at a coarser grain, not a
  halftime-specific mechanism. **Nothing halftime-specific is supported.**

### A8. Adaptation to opponent strengths and to in-game performance

- **The opponent's strengths: negligible.** A team-game's neutral pass rate over expected against its own
  leave-one-out lean (0.80 +/- 0.03), the opponent defence's leave-one-out allowed lean (0.34 +/- 0.04, the defence
  effect of A9), and that defence's rush-minus-pass EPA allowed: -0.028 +/- 0.013 (z -2.2). That is 0.28 points of
  pass rate per SD, about 0.1 of a pass a game. **Not worth modelling.**
- **In-game performance: supported, and it is a response.** On every neutral snap after the offence's 10th, pass
  minus `xpass` against the shrunk success gap so far this game (runs' success minus passes', each shrunk toward
  the league by 10 snaps), with the team's season lean held:

  | form | coefficient | SE | z |
  |---|---|---|---|
  | the gap so far | -0.231 | 0.013 | -18.3 |
  | the same, holding this game's pass lean so far (the plan) | -0.221 | 0.012 | -18.2 |
  | **placebo**: the gap over the REST of the game, beside the gap so far | future -0.009 / past -0.227 | 0.013 | -0.7 / -18 |

  A call cannot see the future. A future coefficient as large as the past one would mean weather, the matchup's
  plan or the opponent's front. It reads zero, so the lean answers what has happened. Its size: 2.3-2.5 points of
  pass rate per SD of the gap, about 0.8 of a pass a game per SD. The dynamics are not simple: the last snap of
  the same drive works the OTHER way (+3.9 points after a successful run, i.e. alternation), while the earlier and
  the last-three gaps lean toward what worked (-0.19 and -0.39). Only the registered summary form is tested.
- **As a call model** (`research/nfl_coach_r2_adapt.py`, logistic with `logit(xpass)` as an offset, fitted on
  2016-2023): gain per team-game in nats over the baseline (the same model without the gap term). Fit seasons
  0.052 +/- 0.006; development 2024 0.069 +/- 0.016 (z 4.2), 2025 0.026 +/- 0.015 (1.8), 2026 0.022 +/- 0.023
  (1.0). Real, but smaller in the last two seasons. Treat it as a hypothesis until its read.
- **Why it is not in an engine yet.** R1's call is computed inside its snap loop, and R1 is pinned. Putting the
  term in an engine means forking that loop into a new version whose games are no longer R1's draw for draw,
  which doubles what has to be validated. The decision-level test comes first (H2).

### A9. Defensive coaching tendencies

- **From play-by-play:** the pass rate over expected a defence draws from its opponents is a weak team-season
  trait. Split-half 0.19, reliability 0.33, true SD 1.7 points, K 34 games; year to year 0.22 +/- 0.06 with the
  same head coach, 0.03 +/- 0.13 with a new one. In A8's regression it passes through to the offence at 0.34.
- **From FTN** (2022-2025, in season, CC BY-SA 4.0, joined on nflverse's game and play ids): the blitz rate on
  dropbacks is strongly a trait. Split-half 0.74, reliability 0.85, SD 6.6 points; year to year 0.64 +/- 0.07 with
  the same head coach (n 76), 0.13 +/- 0.23 with a new one (n 20). The league rate rose from 23.9% to 29.4%.
  **Participation** (coverage, man/zone) is 2016-2025 and not in season.
- **Worth modelling for player production? No evidence.** The Matchups work (`research/reports/nfl_matchup_model.md`)
  found that nothing about the defence (coverage, shells, blitz, box, position allowances) beats Sleeper's line
  out of sample. The defence's effect on the opponent's pass rate is 1.7 points of true SD, about 0.6 of a pass a
  game. Shown here as information; R3 may carry the blitz rate into the sack table if a test supports it.

### A10. Strategic interactions between the staffs

- **Test:** each season's team-game rates fitted as offence + defence (alternating weighted means), then the
  residual of a pairing correlated with the same pairing's residual when the two meet again that season (division
  rematches, 791 pairs). The control is the same offence's residual against another opponent at a like gap in
  weeks (786).
- **Pass rate over expected:** rematch -0.123 +/- 0.035, control -0.077 +/- 0.036. **EPA a play:** -0.103 +/- 0.035
  against -0.044 +/- 0.036. Both negative because a fitted effect absorbs part of each residual, and the rematch
  more (both meetings enter both teams' fits). The difference is -0.05 +/- 0.05: **no evidence of a pairwise
  interaction** beyond the two main effects. Not modelled.

### A11. Verdict

| mechanism | evidence | moves | verdict |
|---|---|---|---|
| HC tendencies (pass rate over expected, no-huddle, pace, 4th down) | reliable team-season traits; carry over with the same HC only | the prior for weeks 1-6 | R3 (with A2) |
| OC tendencies | no lawful structured source reachable | - | owner's call on a cited hand table |
| regime changes | no-huddle -20% early error (2 SE); pass rate -2% (1.1 SE) | weeks 1-6 | R3 |
| situational utilization | +0.8 to +1.1 nats a team-game, z 7.7-12 | who carries, catches, scores | **built: R2** |
| RB committees | the input's shares | - | inherited |
| goal-line specialist | reliability 0.46; in season +0.003 to +0.018 | inside-5 carries | not built; prior-season version is an R3 candidate |
| blowout substitutions (backs) | 18-29% lower blowout-carry error | lead back's ceiling | **built** (part of R2) |
| blowout substitutions (QB) | starter 78.5% of dropbacks with a 17+ lead | QB tail | R3 (a pull model) |
| fatigue | confounded; about 1 yard a game | - | not supported |
| halftime | no break at halftime | - | not supported |
| opponent adaptation | 0.28 pts of pass rate per SD | - | not worth modelling |
| in-game adaptation | z 18, placebo 0; call model +0.02 to +0.07 nats | the call, so volume by type | **registered (H2), decision level** |
| defensive tendencies | blitz rate reliable (0.85), HC-attached | not player production (Matchups) | display; sack link untested |
| staff interaction | rematch minus control -0.05 +/- 0.05 | - | none |

## B. What was built: R2, who gets the ball by situation

### B.1 The engine (`nfl_coach_sim_r2.py`, `coach-r2.0`)

- **R1 plays the game.** R2 imports `nfl_coach_sim`, never edits it and never draws from the game's generator. A
  game here is R1's game, draw for draw (NR2: 40 seeds, every box and the generator state after it equal).
- **R2 deals the box, snap by snap, from R1's passive log.** Every carry and every target goes to one player,
  drawn from his given share times the role x situation multiplier. The situation is read with the score before
  the snap: the log carries the score after each event (NR6).
- **The multipliers redistribute, never add.** Each player's weight is calibrated (`calibrate`) so that his share,
  averaged over the matchup's own expected mix of situations (the batch of simulated games), is the share he was
  given (NR4: to 1e-9; NR4b: the lead back's mean carries within 2% of the static deal on 3,000 games). A blowout
  world takes carries from the lead back; the average world does not. The control is the same code at the static
  share, so the two arms differ by the multipliers alone.
- **Touchdowns go to whoever had the ball** on the scoring snap; lost fumbles to the man who held it;
  interceptions, sacks and kneels to the passer. A completion goes to a receiver by share x catch rate, a targeted
  miss by share x (1 - catch rate), which is production's own split. 91.3% of incompletions and 98.5% of
  interceptions had a target in 2016-2023.
- **Yards:** each team's rushing and receiving yards are split over its players by touches x the player's yards
  per touch, largest remainder, so they sum to the box exactly. Player efficiency is an input, as in production's
  deal. The engine's per-snap yards come from league tables and carry no player identity.
- **Conservation** (NR3, 1,950 games x 2 arms, four roster shapes including two quarterbacks, nobody projected for
  a target, and a one-man roster): carries, rushing yards and touchdowns, receptions, receiving yards and
  touchdowns, attempts, completions, passing yards, touchdowns, interceptions, sacks, kneels and lost fumbles all
  sum to the box in every game. Targets never exceed attempts, and catches never exceed targets. One measured
  wrinkle: R1's log does not name the play of a lost fumble on a snap that ends in a safety (3 team-games in 4,000).
  The box's count is kept and the unnamed fumble is the passer's.
- **Speed:** 300 games dealt by both arms in 0.7 s on this box.

### B.2 The scenarios (guards SC1-SC7, all pass)

| # | scenario | result |
|---|---|---|
| SC1 | inside the 2 | the quarterback carries more than 1.5x his neutral share; a receiver less |
| SC2 | leading big late | the lead back carries at least 5 points less; RB2 and RB3 more |
| SC3 | passing downs, two-minute drill | the QB runs more than 2x (scrambles); the second back is targeted more |
| SC4 | inside the 2, targets | both tight ends up, the lead back down |
| SC5 | every situation | probabilities sum to 1; the static deal is the given share in every bucket |
| SC6 | whole games | winning by 17+ the lead back's share of carries falls below his close-game share; the static deal's does not move |
| SC7 | a roster of one quarterback | he takes every carry, catch and yard |

### B.3 Development measurements (`research/nfl_coach_r2_eval.py`, `research/data/nfl_coach/dev_r2.json`)

**Stage 1, who got the ball on each real snap** (the registered statistic). The engine's own `TeamDeal` against the
static share, on the same base: as-of counts times the fitted role constant. Gain per team-game in nats (positive
= R2 better), SE over team-games:

| | 2024 (448 team-games) | 2025 (448) | 2026 wk 1-12 (232) |
|---|---|---|---|
| carries + targets | **1.08 +/- 0.09 (z 12.2)** | **0.81 +/- 0.08 (10.5)** | **0.90 +/- 0.12 (7.7)** |
| carries | 0.97 +/- 0.08 | 0.73 +/- 0.07 | 0.75 +/- 0.11 |
| targets | 0.12 +/- 0.03 | 0.08 +/- 0.03 | 0.15 +/- 0.04 |
| who scored the touchdowns (those snaps only) | 0.035 +/- 0.017 | 0.048 +/- 0.016 | 0.073 +/- 0.021 |
| core players only (RB1, RB2, QB; WR1-3, TE1, RB1) | 0.78 +/- 0.07 | 0.57 +/- 0.06 | 0.66 +/- 0.09 |

Per carry that is 1.163 -> 1.125 nats (2024), 1.151 -> 1.123 (2025), 1.062 -> 1.032 (2026).

**Realism on simulated games.** Each real game is played 200 times by R1 at its closing line's level (R1's anchor,
a level curve of 1,000 games a level), and every simulated game is dealt by both arms. R2 minus static, per
team-game (negative = R2 better):

| | 2024 | 2025 | 2026 wk 1-12 |
|---|---|---|---|
| CRPS, RB1/RB2/QB carries (static 7.62 / 7.28 / 7.19) | -0.011 +/- 0.015 | +0.000 +/- 0.014 | -0.038 +/- 0.020 |
| CRPS, WR1-3/TE1/RB1 targets (static 9.20 / 9.24 / 9.44) | +0.013 +/- 0.015 | +0.005 +/- 0.015 | -0.028 +/- 0.022 |
| log score of their touchdowns (static 2.08 / 2.22 / 2.01) | -0.007 +/- 0.015 | -0.046 +/- 0.016 | -0.064 +/- 0.018 |

**The lead back's share of his team's carries, by final margin** (simulated, both arms, against real):

| margin | 2024 real / R2 / static | 2025 | 2026 wk 1-12 |
|---|---|---|---|
| lost by 17+ | 0.497 / 0.544 / 0.560 | 0.510 / 0.580 / 0.593 | 0.479 / 0.609 / 0.626 |
| within 7 | 0.558 / 0.569 / 0.562 | 0.572 / 0.579 / 0.573 | 0.576 / 0.636 / 0.629 |
| won by 17+ | 0.517 / 0.548 / 0.561 | 0.494 / 0.551 / 0.564 | 0.566 / 0.625 / 0.636 |

Reading it:

- **What is shown.** Who gets the ball on a real snap is predicted better in every season, by 7-12 standard
  errors. The gain comes mostly from carries, and two thirds of it survives among the players a lineup holds. Who
  scores the touchdowns is predicted better in every season.
- **What is not shown.** That simulated player production is much more realistic. The CRPS of carries and targets
  moves by less than 0.5% of its size and inside about 2 SE every season. The touchdown log score improves in two
  of three seasons.
- **Where it is still short.** In real games the lead back's share falls in blowouts BOTH ways, by 1-10 points.
  R2 moves the right way when winning big (a third of the real drop in 2025, half in 2024, all of a small one in
  2026) and only slightly when losing big; the static deal does not move at all. Part of the real drop is injuries and benchings, which no
  pre-game share knows. Separately, both arms run the lead back above real, by about 1 point in 2024-25 and 6
  in 2026: the role constant fitted on 2016-2023 is the same in both arms and is not R2's to fix.

## C. The registration (`research/data/nfl_coach/prereg_r2.json`)

| | |
|---|---|
| block sha256 | `84144a48c39aeefbc61564eb4a25e865f37c04fbc7413d84679424baacd81222` |
| written | before any test game was read. The block was regenerated once, before its commit, because a guard's own test case (NR10) was wrong; no data had been read |
| pinned by sha256 | `nfl_coach_sim_r2.py`, its tables, `research/nfl_coach_r2_data.py`, `_eval.py`, `_adapt.py`, `adapt_r2.json`, `_read.py`, `tests/guards_auto_nfl_coach_r2.py`; R1's `nfl_coach_sim.py`, its tables, `research/nfl_coach_data.py`, `research/nfl_coach_eval.py`; `nfl_deep_sim.py`, `nfl_pbp.py` |
| the read | `python3 -m research.nfl_coach_r2_read H1 <dir>` / `H2 <dir>` from a clean tree at a commit holding every pinned file; writes `read_r2_H1.json` / `read_r2_H2.json` |
| refuses | before its date; before the window's last game + 48 h (by the data's own dates); a dirty tree; a block or pinned-file mismatch; a second read; **R1 not yet read** (`read_r1.json` absent); too few team-games |

**H1: who gets the ball by situation.** Window: 2026 weeks 13-18 (about 190 team-games; at least 150). Date: on or
after 2027-01-06 06:00 UTC, after R1's read. Settings: 200 simulated games a real game, a level curve of 1,000
games a level, a seed per game from its nflverse id, the team's 4th game on. **All four must hold:**

1. `tests/guards_auto_nfl_coach_r2.py` exits 0.
2. Stage 1, carries + targets: gain > 2 SE.
3. Stage 1, carries gain > 0 and targets gain > -1 SE.
4. Realism: carries CRPS, targets CRPS and touchdown log score, R2 - static <= +1 SE each.

**Power, stated before the read.** Criteria 2-3: at 190 team-games the development gains give z of about 6-8, so a
pass with probability above 0.99. Criterion 4: three "not worse than 1 SE" bounds. If the development differences
hold, they pass jointly about 0.8 of the time. A 1-SE bound alone fails about one time in six with no true
difference, and targets' CRPS is the likeliest to trip it. Overall about 0.8.

**H2: in-game adaptation, decision level.** Window: 2026 weeks 13-18 plus 2027 weeks 1-18 (about 640 team-games; at
least 500). Date: on or after 2028-01-13 06:00 UTC, after R1's read. Frozen coefficients in
`research/data/nfl_coach/adapt_r2.json`. Criteria: the guards exit 0, and the per-team-game gain over the
baseline call model is > 2 SE. **Power:** at 640 team-games the SE is about 0.013, so power is about 0.89 at the
development mean (0.042) and about 0.47 at the last two seasons' 0.025. A FAIL here would mostly be a statement
about power. The only route to an engine is a pass.

### C.1 Why H1 uses R1's sealed weeks, and what the owner decides

R1 read first, R2 second. R1's criteria are game-level: snaps and points inside 2:00, the level, win probability.
They are frozen by sha256, so nothing seen afterwards can move them, and R2's read refuses to run before R1's
result is committed. R2's statistics (who got each ball; the pass call) are different statistics on the same
games, and this audit has never read them. The R1 report (section 8.2) left it to the owner whether those games
still count as unread for R2. **If he says no**, H1 moves to 2027 weeks 1-9 (about 180 scored team-games, each
team's 4th game on; the postseason is not scored, as-of shares are regular-season), same power, read in
November 2027. The registration is then rewritten before either is read, and
this file and the guards say so.

## D. The plan beyond R2 (each needs its own rule, written and hashed before its test data)

1. **R3: head-coach tendencies with the regime-change prior** (A1, A2). A team's prior for pass rate over expected,
   no-huddle, pace and fourth-down go rate is the league plus last season's deviation times a persistence BY REGIME
   (same HC 0.59 / 0.81 / 0.57 / 0.48; new HC 0.15 / 0.02 / 0.40 / 0.08). For a head coach with earlier seasons
   elsewhere it uses his own deviation (not shown to port, n 12: test it, do not assume it). It needs an engine
   that reads team tendencies: R1 at team profiles, or the production profiles' prior. Test: weeks 1-6 of 2027,
   decision-level log loss of the call and fourth-down choice and the pace error, regime prior against pooled
   prior, more than 2 SE. Power is moderate for no-huddle, low for the rest: 32 teams, about 8 new coaches a year.
2. **R3: the quarterback's blowout pull** (A5). A persistent switch: entering the fourth quarter at 17+ either way,
   the starter leaves for the rest of the game at a measured rate, and the passing line splits play by play.
   Needs a backup quarterback in the player list and his own efficiency.
3. **R3 candidate: the goal-line specialist from last season** (A4). The same back on the same team, his
   prior-season inside-5 deviation shrunk by the measured reliability. Stage 1 on 2027 weeks 1-8.
4. **R4: adaptation in the engine** (A8), only if H2 passes: a new snap loop with the registered call term, judged
   on game-level realism (rush attempts' variance and their correlation with rush efficiency) against R2.
5. **The DFS link** (the owner's "independent DFS validation path"): R2's deal in place of production's
   `deal_team` inside `nfl_dfs_engine`, judged by `nfl_dfs_engine.md`'s four links plus a top-decile Brier
   (audit 7.4). That is hours on the PC, and only after H1 passes and the owner approves. Nothing here reaches
   a DFS world before then.

Rejected, with the reason recorded in A: fatigue, halftime, opponent adaptation, staff interaction, coach-level
usage deviations, two-point aggressiveness as a coach trait.

## E. For the owner

1. **May H1 read 2026 weeks 13-18 after R1's read?** (C.1). The alternative costs eleven months.
2. **Coordinators:** a hand-compiled table with a cited public source per row, or leave them out (recommended: out
   of any model, because no source records the play-caller).
3. **R3's order:** the regime prior first (cheap, supported, weeks 1-6 only), or the quarterback pull first (DFS
   tails).
4. **The 2026 role constants** run the lead back about 6 points high in both arms (B.3; about 1 in 2024-25). The fix lives in whatever
   supplies the shares (Sleeper in production), not in R2.

## F. Reproduce

```
# nflverse-data play_by_play_<season>.csv.gz 2016-2026 ("pbp" release) and players.csv ("players" release) in <dir>
# FTN charting 2022-2025 ("ftn_charting" release) in <ftn dir>
python3 -m research.nfl_coach_r2_evidence <dir> <dir>/players.csv <ftn dir>   # A: ~4 min
python3 -m research.nfl_coach_r2_fit <dir> <dir>/players.csv                  # the tables
python3 -m research.nfl_coach_r2_adapt <dir>                                  # H2's coefficients
python3 -m research.nfl_coach_r2_eval <dir> <dir>/players.csv --sims 200      # B.3: ~10 min on 4 cores
python3 tests/guards_auto_nfl_coach_r2.py
```

The tables carry a build timestamp. A refit differs from the committed file in that field, and the registration
pins the committed one.

## G. Files and guards

| file | what |
|---|---|
| `nfl_coach_sim_r2.py` | the engine: situations, roles, calibration, the deal, `simulate` (both arms on the same games) |
| `research/data/nfl_coach_r2_tables.json` | multipliers, role constants, the league mix, target rates (2016-2023) |
| `research/nfl_coach_r2_data.py` | nflverse -> 27 ints a play with the ball carrier, target and passer; R1's seal, its own read token |
| `research/nfl_coach_r2_evidence.py` | section A; `research/data/nfl_coach/r2_evidence.json` |
| `research/nfl_coach_r2_fit.py` | the tables |
| `research/nfl_coach_r2_eval.py` | stage 1 and realism; `research/data/nfl_coach/dev_r2.json` |
| `research/nfl_coach_r2_adapt.py` | H2's call model; `research/data/nfl_coach/adapt_r2.json` |
| `research/nfl_coach_r2_prereg.py`, `research/data/nfl_coach/prereg_r2.json` | the registration |
| `research/nfl_coach_r2_read.py` | the registered reads (not run) |
| `tests/guards_auto_nfl_coach_r2.py` | 30 guards, section NR (self-registered, AUTO) |

Guards: NR0-NR0c (production and every R1-pinned file byte-identical; no predlog; no production import; R1 imported,
never copied), NR1-NR1b (tables), NR2 (R1's game draw for draw), NR3 (conservation), NR4-NR4b (calibration and the
mean), SC1-SC7 (scenarios), NR5-NR6 (the buckets by hand; the score before the snap), NR7 (the seal on a synthetic
file), NR8-NR8b (H2's frozen model and its gap by hand), NR9-NR11b (the registration's hashes, windows and dates;
the read's refusals; both verdict rules on hand-built summaries).
