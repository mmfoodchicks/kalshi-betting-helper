# Coaching engine R5: the trailing team's pull, and the second unit as drawn decisions

2026-11-27. `nfl_coach_sim_r5.py` (`coach-r5.0`) is experimental and for research only. No board, viewer, DFS
world or predlog bucket reads it. It is built on R4 and leaves R1-R4's pinned files untouched; guard N5-0a checks
this. The data is nflverse 2016-2026 week 12 (CC BY 4.0). R1's seal holds: 2026 week 13 onward is never read.

The owner asked for four things after reading R4:

1. **The injury report as a label only.** It must never feed the live simulator. See R4's report, section H.1,
   with guards N5-T1 to T3, and section H.2 for the labelling sensitivity.
2. **R3's random-stream offset.** See the addendum to section G of R3's report, and guard N5-7.
3. **R5, the trailing team's pull.** Section A here.
4. **The second unit's substitutions.** They must be probabilistic, with no assumption that the starters leave
   together. Section B here.

## A. The trailing team's pull (`research/nfl_coach_r5_fit.py`, `research/data/nfl_coach/r5_fit.json`)

### A.1 The rule, written before 2025 was read

The candidates are nested, and each is learned:

- **s6:** R3's form.
- **p1:** s6 plus the possessions a trailing offence needs against the possessions left: log(1 + ceil(deficit/8)
  / ((seconds + 30)/327)) and its square. Eight points is a touchdown and a try; 327 s is one possession, the
  league's 11 a game.
- **p2:** p1 plus the offence's timeouts, read from nflverse's `posteam_timeouts_remaining` at the drive's first
  play. All 57,198 drive states matched a play row.
- **p3:** p2 plus the leader's own need term.
- **p4:** p3 plus the head coach's earlier pull tendency: his observed pulls against R3's expected over his
  EARLIER seasons, log((o+5)/(e+5)).

Each candidate is fitted on 2016-2024, with R3's weighting (half-life 2 seasons). Walking up from s6, a candidate
is kept only if it beats the current one by more than 2 SE of 2025 log loss, paired per team-game.

| on 2025 (544 team-games), gain over s6 a team-game | p1 | p2 | p3 | p4 |
|---|---|---|---|---|
| gain +/- SE (z) | -0.0006 +/- 0.0033 (-0.19) | -0.0004 +/- 0.0031 (-0.12) | +0.0032 +/- 0.0037 (0.86) | +0.0047 +/- 0.0041 (1.16) |

- **The rule keeps s6.** The walk stopped at p1. None of possession, timeouts, the leader's need or the coach
  helps.
- **R5's hazard is R3's form refitted on 2016-2025.** On 2026 weeks 1-12 it scores +0.0007 +/- 0.0025 a
  team-game over R3's shipped coefficients (z 0.29): the same hazard.

### A.2 Why nothing fixed the miss: it is a change in behaviour, not a missing state

R3's miss is the pulls of teams trailing 17-27, counted at the drive's first snap with 2:01-8:00 left. Broken out
by season (R3's shipped hazard, which is s6 fitted on 2016-2024):

| season | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 | 26 (w1-12) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| real pulls | 5 | 4 | 5 | 5 | 3 | 8 | 9 | 8 | 12 | 9 | 9 |
| R3 expected | 3.7 | 5.3 | 4.5 | 6.5 | 4.9 | 6.7 | 3.7 | 4.4 | 5.5 | 4.1 | 2.6 |

- **2016-2021:** 30 real pulls against 31.6 expected. The form is calibrated.
- **2022-2026:** 47 against 20.3, a 2.3x excess (Poisson z ~5.9).
- **What that means.** Since 2022, teams that are three scores down late protect their quarterback far more
  often (R4 found 28 of the 30 misses strategic). Nothing in the game state changed: the behaviour did.

#### The regime search (section 3 of the fit, added after the rule refused every state candidate)

Each candidate is R3's form plus terms that switch on at a change-point season c, for c in 2018-2024. There are
seven term sets: an intercept, the deficit, the deficit x time, four deficit terms, lead plus deficit, the
possession term, and the possession term with and without the regime. The form and c were chosen on 2016-2024 by
BIC.

- **The best is a 2022 intercept**, BIC 2214.7, but R3's form scores 2210.5, so no regime term was adopted.
- **None of them reaches the corner even in sample:** 2024 expected 6.1 against 12 real.
- **Development gains:** +0.0013 +/- 0.0018 (2025) and -0.0001 +/- 0.0032 (2026).
- **Why the forms fail.** The excess sits in one corner. A smooth hazard that raised it would have to raise the
  neighbouring trailing states too, and their real rates did not rise.

So R5 does NOT build a trailing-pull term. Each form allowed by "score, time, possession, timeouts, team
tendencies, never hand-set thresholds" was tried and refused by its own test. A term that fits only this corner
would be a threshold in all but name, and its evidence is in-sample.

What R5 does instead is register the question (section D's secondary): **does the 2022+ excess persist on fresh
games?** If it does, that is the evidence a learned corner surface (for example, cells shrunk toward s6) would need,
and it would be registered on games after 2028.

## B. The second unit as drawn decisions

**What R4 does.** R4 shifts each player's odds on every snap. It does not force everyone out together, but it
under-disperses badly: the lead back sat out the rest of the game after 4-5% of its simulated strategic pulls,
against about 50% of the real ones (R4's report, section H.3).

**The model** (`unit_rows`, `unit_fit`; 2016-2024; 424 strategic pulls, 353 with the lead back active before the
pull, 643 receiver rows). The players are the season's lead back and top two WR/TE targets, leaving that game
out. A player counts only if he touched the ball before the pull; his season share also leaves that game out.
Each pull has latent decisions:

- the back sits for the rest of the game with q_b;
- each receiver sits with q_w1 if the back sat, and q_w0 if he played;
- a player who plays gets Binomial(touches after, expit(logit(season share) + d)).

The fit is maximum likelihood, with SEs from the observed information.

| parameter | 2016-2024 (the tables) | 2025-2026 w1-12 refit (84 pulls, a check) |
|---|---|---|
| q_b, the lead back sits | 0.530 +/- 0.029 | 0.630 +/- 0.063 |
| q_w1, a top-two target sits, the back sat | 0.478 +/- 0.053 | 0.587 +/- 0.137 |
| q_w0, a top-two target sits, the back played | 0.050 +/- 0.024 | 0.102 +/- 0.093 |
| d_b, a playing back's log-odds shift | +0.004 +/- 0.062 | -0.37 +/- 0.20 |
| d_w, a playing receiver's | +0.031 +/- 0.040 | -0.25 +/- 0.14 |

What the fit says:

- **The decision is per player, and it is correlated, not forced.** A receiver sits 48% of the time when the back
  sat, and 5% when he played. A player who plays keeps his season share (d ~ 0).
- **Recent seasons sit more** (q_b 0.63). This is the same direction as the corner in A.2: recent coaches protect
  starters more. The tables keep 2016-2024; the window will say.

**The engine** (`nfl_coach_sim_r5.deal`). At a strategic pull, the decisions are drawn once per side:

- a seated player's weight is 0 for the rest of the game;
- a playing one's odds are multiplied by exp(d).

The draws use their own generator, seeded from the deal generator's state without consuming it (guard N5-6). A
deal with no pull, an injury pull, or R5's second unit switched off is R4's line for line (N5-3). With no pull, a
game is R4's and R1's draw for draw (N5-2). Conservation is exact (N5-4). A seated player never touches the ball
after the pull, and a playing one does (N5-6b). The fitted rates come back out of the draws, including both
"apart" cases (N5-6a).

### B.1 Development (`research/nfl_coach_r5_eval.py`, `research/data/nfl_coach/r5_eval.json`)

**The likelihood of what the second unit actually got after each real strategic pull**, R5 minus R4, per pulled
team-game, with no simulation. R4 is the same model with the sit probabilities at 0 and d = log of R4's odds
ratio.

| | 2016-2024 (in sample) | 2025 | 2026 w1-12 |
|---|---|---|---|
| gain, nats a pulled team-game | +1.22 +/- 0.13 (z 9.5, 424) | +1.34 +/- 0.36 (z 3.8, 57) | +1.51 +/- 0.50 (z 3.0, 27) |

**Simulated against real.** The simulation starts at 24-0 with 10:00 left in the third quarter, on a league
roster, with 1,500 games an engine. Both engines are read on the same definitions as the real pulls.

| after a strategic pull | real 2016-2024 | real 2025-26 | R4 | R5 |
|---|---|---|---|---|
| lead back no more carries (3+ backs' carries after) | 51.3% | 65.1% | 5.2% | 48.8% |
| both top two no target (3+ targets after) | 24.5% | 50.0% | 8.8% | 21.3% |
| lead back's share when he carries, median | 0.60 | 0.50 | 0.44 | 0.63 |
| back sat, both receivers played | 10.0% | 0.0% | 0.4% | 7.9% |
| back played, a receiver sat | 19.2% | 28.6% | 50.2% | 25.6% |

R4 under-seats and makes a receiver "sit" by chance (few targets). R5 reproduces the real shape.

## C. What R5 does not do

- **No trailing-pull term** (section A.2).
- **No pull habit for backs or receivers beyond the second unit.** R2 deals the rotation, and an injury pull keeps
  R4's personnel (none).
- **Coordinators stay out** (the owner, 2026-11-26).
- **R5 is not wired into anything.**

## D. The registration (`research/data/nfl_coach/prereg_r5.json`, block d20d0e13)

- **Window:** the 2028 regular season. Of the earlier windows, 2026 weeks 13-18 belong to R1, R2-H1, R3 and R4,
  and 2027 to R4. No registered window and no development set holds 2028.
- **Timing:** read once, on or after 2029-01-16 06:00 UTC, after R1's and R4's reads. It needs at least 30
  strategic pulled team-games.
- **No simulation:** every statistic is a likelihood of what happened, at the frozen tables. `research/nfl_coach_r5_read.py`
  refuses a dirty tree, a hash mismatch, a second read, an earlier read missing, or the window's last game plus
  48 h not yet passed. Guards N5-8 to N5-11 check these refusals, the verdict rule, and the registry entry.

The criteria (all must hold):

1. `tests/guards_auto_nfl_coach_r5.py` exits 0.
2. The second unit's gain over R4 is above 2 SE.
3. The hazard's gain over R4's (R3's) is above -2 SE, as a guardrail.

The secondary is reported and never decides: the corner's real pulls against R5's expectation, one-sided Poisson
P(X >= real) < 0.05, meaning the 2022+ excess persists.

**Power** (power is the chance a test passes IF R5 is better by the stated amount; it is not the probability that
it is better):

- **The window's size:** about 45 strategic pulled team-games.
- **Criterion 2:** about 0.91 at +1.34, about 0.37 at half of that, and about 0.77 at the 30-game floor.
- **Criterion 3:** it fails about 2.3% of the time by chance.
- **The secondary:** R5 expects about 4.5 corner pulls a season, so the critical count is 9. Power is about 0.71
  if the 2.3x excess persists, and about 0.24 at 1.5x.

**Dependence:** R5's unit statistic conditions on the real pulls, so it does not depend on R4's verdict. Both are
built on R3's pull and R4's labels, so a defect there is shared, and H.2's label sensitivity applies to R5's
"strategic" set too. 2025 and 2026 weeks 1-12 were read in development, including the corner by season, so no
development figure is a test.

## E. For the owner

1. **Item 1: the report is a label only.** It is verified, with guards that would catch a regression. Labelling
   the ambiguous pulls the other way moves the call shifts the most, but not the personnel or completion claims.
   R4's registration is unchanged.
2. **Item 2: the offset cannot move R3's registered read.** The dropbacks and passing yards behind criteria 3 and
   4 never touch the deal's draws: bias 0, variance and power identical. R3 is untouched.
3. **Item 3: R5 did NOT fix the trailing miss.** Every learned form was refused. The miss is a 2022+ change in one
   corner (47 real against 20.3 expected), now registered as a question for 2028.
4. **Item 4: the second unit is now drawn per player.** The decisions are correlated as measured, not forced
   together, and the model reproduces the real 50% / 20% sit rates. It is +1.3 to +1.5 nats a pulled team-game
   in development.

## F. Reproduce, files, guards

```
# play_by_play 2016-2026 and players.csv in <dir>; injuries_2016..2025.csv in <inj dir>
python3 -m research.nfl_coach_r5_fit <dir> <dir>/players.csv <inj dir>
python3 -m research.nfl_coach_r5_eval <dir> <dir>/players.csv <inj dir>
python3 -m research.nfl_coach_r4_checks <dir> <dir>/players.csv <inj dir>
python3 -m research.nfl_coach_r3_seed_check <dir> <dir>/players.csv --sets 4 --sims 200
python3 tests/guards_auto_nfl_coach_r5.py
```

| file | what |
|---|---|
| `nfl_coach_sim_r5.py` | the engine: R4's loop with R5's tracker (timeouts from the log, the hazard's inputs recorded), the drawn second unit |
| `research/data/nfl_coach_r5_tables.json` | the hazard (spec, coefficients; the rule's choice), the second unit's five values |
| `research/nfl_coach_r5_fit.py` | A (the rule, the corner by season, the regime search) and B's fit; `r5_fit.json` |
| `research/nfl_coach_r5_eval.py` | B.1 and the power; `r5_eval.json` |
| `research/nfl_coach_r5_prereg.py`, `research/data/nfl_coach/prereg_r5.json` | the registration |
| `research/nfl_coach_r5_read.py` | the registered read (not run) |
| `research/nfl_coach_r4_checks.py` | R4 report section H.2-H.3; `r4_checks.json` |
| `research/nfl_coach_r3_seed_check.py` | R3 report section G addendum; `r3_seed_check.json` |
| `tests/guards_auto_nfl_coach_r5.py` | 26 guards, section N5 (AUTO) |
