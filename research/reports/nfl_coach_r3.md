# NFL coaching engine, R3: the quarterback's substitution

Written 2026-11-27. **Research only: no production file changed, and no board, viewer, DFS world or predlog
bucket reads any of this.** R1 and R2 and every file their registrations pin are byte-identical (guard NQ0a). The
seal holds: 2026 week 13 on is never read (R2's loader, R1's `sealed()`). **The registered read has not been
run.**

The owner: "quarterback blowout substitutions first ... particularly important for DFS and directly addresses our
goal of emulating a real coaching staff." The substitutions are learned from historical evidence, never from a
score threshold.

Data credit: nflverse-data play-by-play and players (CC BY 4.0). Raw files are gitignored.

## 0. The short version

1. **Learned hazard.** The chance the starter is pulled at a drive is a logistic in the margin against the time
   left, learned on nflverse 2016-2024 (`research/nfl_coach_r3_qb.py`). The lead and the deficit enter apart, each
   as log(1 + margin / sqrt(time) / 10), with their squares and their products with log(time). The form was chosen
   on 2024 from seven candidates, and the seasons are weighted with a half-life of two: pulls rose from about 69 a
   season (2016-19) to about 100 (2023-25). Against a constant hazard it gains **+0.283 nats a team-game in 2025
   (z 6.7) and +0.261 in 2026 weeks 1-12 (z 5.5)**.
2. **The pulled starter stays out.** After a pull at a margin the game has decided, he came back 6 times in 410
   (1.5%) in the fit seasons and 1 in 179 since.
3. **What the evidence did not support is not built:**
   - a head coach's own habit: shrunk multipliers lost out of sample at every K;
   - a different rate when the starter is already a backup: no gain;
   - a persistent pull for backs: after the first backup carry with a 17+ lead, the lead back still takes 41% of
     the rest, so backs rotate and R2 already deals that;
   - play selection with an inexperienced backup: real (-5.5 points of pass rate when trailing), but R1 makes the
     call, so it is measured, not modelled.
4. **The engine (`nfl_coach_sim_r3.py`, `coach-r3.0`).** R1 plays the game, R2 deals the ball, and R3 says who is
   at quarterback. Every dropback, sack, spike, kneel and quarterback carry after the pull is the backup's.
   - Conservation is exact.
   - With the pull off, every line is R2's to the yard (NQ3).
   - The owner's situations play out as learned (B.2).
5. **Development realism** (R1's games at the closing line, R2 against R3 on the same games and the same deal
   draws): the CRPS of the real starter's dropbacks improves by **0.363 +/- 0.049 (2025, z -7.4) and 0.181 +/- 0.057 (2026 weeks 1-12, z -3.2)**, and of his passing yards by 1.45 +/- 0.30 and 0.71 +/- 0.41. The starter's share of his team's dropbacks when losing by 17+ is 0.888 against a real 0.884 (2025); R2 has 1.0.
6. **The registration** (`research/data/nfl_coach/prereg_r3.json`, block `c8c21e27...`): 2026 weeks 13-18, on or after
   2027-01-06 06:00 UTC, after R1's read, with the dependence on R1's and R2's reads stated. Power about 0.8-0.95: the hazard's gain about 0.95, the dropbacks' CRPS 0.82-1.0.

## A. The evidence (2016-2025, and 2026 weeks 1-12 as development)

**Definitions** (`research/nfl_coach_r3_qb.py`).
- **The game's starter** is the quarterback on his team's first QB-identifying snap: a pass, a sack, a spike or a
  kneel, by a player nflverse lists at quarterback.
- **An identified drive** holds such a snap, and its quarterback is the one on its first. Scrambles and designed
  runs do not identify, because R1 plays both as runs and the engine must see the same drives the fit saw.
- **The pull** is the first identified drive whose quarterback is someone else. The outcome sequence of a
  team-game is 0 for every identified drive the starter begins, then a 1 or nothing.
- **Counts:** 512-544 team-games a season, 67-71 pulls a season in 2016-19, 83-102 in 2020-25, and 48 in 2026
  weeks 1-12.

### A1. The hazard: score differential, time remaining and possession, learned

The state is read at the first snap of each identified drive. Possession is built in: the hazard applies to the
team with the ball, the side whose quarterback is on the field. Selection on 2024, log loss a team-game:

| candidate | features | 2024 |
|---|---|---|
| s0 | a constant (the injury-and-rest average) | 0.969 |
| s1 | log(1 + z), lead and deficit | 0.723 |
| s3 | + squares, + log time | 0.701 |
| s4 | + the starter is not the season's starter so far | 0.701 (no gain) |
| s6 | + each term times log time | **0.638** (half-life 2) |
| s7 | the margin and the time apart, curved, with products | 0.640 |

Gain over the constant hazard (the selected form, refitted with 2024 in, as R1 did; nats a team-game):
**2025 +0.283 +/- 0.042 (z 6.7), 2026 weeks 1-12 +0.261 +/- 0.047 (z 5.5)**. The 2024 selection figure is +0.296.

The owner's situations, as the learned per-drive hazard (the engine's own function):

| state | per drive | from that state, the starter pulled (300 games, B.2) |
|---|---|---|
| leading by 35, 8:00 left | 0.76 | 92% |
| trailing by 35, 5:00 left | 0.29 | 42% |
| leading by 24, 2:00 left | 0.67 | 67% |
| leading by 7, 8:00 / 2:00 left | 0.003 / 0.010 | 4% from the fourth quarter's start, 1% from 2:00 |
| tied, first quarter | 0.005 | the injury rate |

The hazard rises with the lead at a fixed clock (0.005, 0.034, 0.20, 0.61, 0.89 for 7 to 35 points at 5:00) and
with the clock at a fixed lead (a 28-point lead: 0.04 at 30:00, 0.15 at 15:00, 0.39 at 8:00, 0.85 at 2:00).
Leading teams rest their starter far sooner than trailing teams replace theirs.

**Calibration** (2025 + 2026 weeks 1-12, pulls observed against the model's expectation, by margin x time; a
check, never an input):
- Most cells agree: leading 17-27 in the last 2:00, 14 against 14.2; 28+ with 2-8 minutes, 3 against 3.5;
  within 8 in the first half, 15 against 16.2.
- **Trailing by 17-27 with 2-8 minutes left the model is short: 18 pulls against 6.7**, and short in the fit
  seasons too. Leading 28+ with 8-15 minutes it is 11 against 6.6.
- **The miss is real and it is in the trailing team's late pull**, which rose most in recent seasons. No
  candidate form closed it without losing on 2024. It is the first thing a revision should look at, and it is
  why the registered read's realism criterion is on the starter's line, not on calibration.

### A2. A pulled starter stays out

| pull state | fit seasons: came back | development |
|---|---|---|
| the margin has decided the game (abs(margin) / sqrt(time) / 10 >= 3; descriptive only) | 6 of 410 (1.5%) | 1 of 179 (0.6%) |
| other (mostly injury) | 54 of 208 (26%) | 15 of 73 (21%) |

The engine plays every pull as permanent. For a rest pull that is the measured behaviour. For an injury pull it
overstates the starter's loss in about a quarter of those games: about one game in 80 overall.

### A3. Quarterback availability

- **The starter already out** (the game's starter is not the season's starter so far, i.e. a backup is
  starting): as a feature (s4) it adds nothing (-0.0005 +/- 0.0004, -0.0005 +/- 0.0004, -0.0001 +/- 0.0004 a
  team-game in 2024-2026). The hazard applies to whoever starts, at the same rates.
- **Who the backup is:** play-by-play names him only when he plays, so the hazard cannot condition on him
  (selection). He enters the engine as the second quarterback on the list, or a placeholder.

### A4. Team and head-coach tendencies

Observed over expected pulls per head coach (59 coaches with 3+ expected, fit seasons): chi-square 94.9 on 59,
a dispersion of 1.61, so coaches differ in sample. Out of sample (2024-2025, 1,088 team-games) a shrunk multiplier
(O + k) / (E + k) gains:

| k | 2 | 5 | 10 | 20 |
|---|---|---|---|---|
| nats a team-game | -0.014 +/- 0.008 | -0.004 +/- 0.003 | -0.001 +/- 0.002 | +0.001 +/- 0.002 |

**No evidence that a coach's habit predicts his later games.** The in-sample dispersion does not survive a season
boundary, and nothing is built.

### A5. Play selection with a backup in

Pass rate over expected (nflverse `xpass`), downs 1-3, second half, margin 17+ either way:

| at quarterback | leading | trailing |
|---|---|---|
| the starter | -1.1 (11,288 snaps) | -0.2 (17,726) |
| an experienced backup (300+ dropbacks before) | +4.2 (730) | -3.7 (1,639) |
| an inexperienced backup | +1.2 (1,082) | -5.7 (1,989) |

(points; the SE is about 1.0-1.8)

- **Trailing with a new backup in, teams run more:** 5.5 points below the starter in the same states (about
  4.7 SE). Against an experienced backup the gap is 2.0 points, only 1.2 SE.
- **Not modelled.** R1 makes every call, and its call tables are fitted on the same seasons, so the average
  garbage-time call already includes the backups who were in.
- **What it moves:** about one pass in the games where a new backup plays.

### A6. Extending the mechanism to backs and receivers

- **Backs: R2 already deals it, and a pull is not supported.** With a 17+ lead in the second half, once a back
  other than the game's lead back carries, the lead back still takes **40.8%** of the rest (3,700 carries). His
  share of that state overall is 43.1% (R2's audit). In close play it is 64.6%. The second unit rotates in; it
  does not take over. R2's per-snap multipliers are the right form.
- **Receivers:** the second unit's target share in blowouts is R2's role x situation multiplier (the third
  receiver and the second tight end rise). A persistent receiver pull was **not tested** here; it is listed in E.

## B. What was built (`nfl_coach_sim_r3.py`, `coach-r3.0`)

### B.1 The engine

- **R1 plays the game draw for draw** (NQ2).
- **R2's deal, snap for snap, with the quarterback switched.** R3's deal draws carriers and targets exactly as R2's
  does, in the same order on the same generator, so with the pull off every line equals `nfl_coach_sim_r2.deal`'s
  (NQ3, 60 team-games). The pull is drawn on its own generator. The two arms of `simulate` (R2, R3) are the same
  games and the same deal draws and differ by the quarterback alone.
- **The drives** are rebuilt from R1's log: a change of possession, a punt, a field goal, a try, a kickoff or a
  free kick ends one (NQ6). The margin is read before the snap, from the start state's score when a game is played
  from one. R2's own parser assumes 0-0, which read a 24-point lead at 2:00 as a tie; R3's does not.
- **From the pull's drive on**, every attempt, completion, sack, spike, interception, kneel and quarterback carry
  is the backup's. His passing yards are the sum of his own completions' yards, and so are the starter's.
- **Conservation** (NQ4, 1,200 games x 2 arms, a listed backup and a placeholder): both quarterbacks' lines and
  every carry, catch, yard, touchdown, sack, kneel and lost fumble sum to the team's box.

### B.2 The owner's situations (guards SQ1-SQ6, from R1 played from the state, 300 games each)

| # | situation | result |
|---|---|---|
| SQ1 | leading by 35, 8:00 left | the starter rested in 92%; the backup takes 95% of the victory-formation kneels |
| SQ2 | trailing by 35, 5:00 left | replaced in 42%: in some games, not most |
| SQ3 | leading by 24, 2:00 left | protected in 67% |
| SQ4 | leading by 7 | 4% from the fourth quarter's start, 1% from 2:00 (the injury rate's size) |
| SQ5 | the hazard | rises with the lead and with the clock; a first-quarter tie under 1% a drive |
| SQ6 | whole games | the starter's dropback share is lowest losing big, as in real games |

## C. Development (`research/nfl_coach_r3_eval.py`, `research/data/nfl_coach/dev_r3.json`)

**Stage 1, the pull sequence on real drives** (the engine's own hazard against the constant hazard
fitted on the same seasons; nats a team-game; every regular-season team-game):

| | 2025 (544 team-games, 102 pulls) | 2026 wk 1-12 (328, 48) |
|---|---|---|
| gain | **+0.283 +/- 0.042 (z 6.7)** | **+0.261 +/- 0.047 (z 5.5)** |

**Realism.** Each real game (from each team's 4th game on) is played 200 times by R1 at its closing line's level,
and every simulated game is dealt by R2 and by R3 on the same deal draws. The scores are the CRPS of the real
starter's line, per team-game; R3 - R2 is negative when R3 is better:

| | 2025 (448 team-games) | 2026 wk 1-12 (232) |
|---|---|---|
| starter's dropbacks (R2 5.79 / 5.34) | **-0.363 +/- 0.049 (z -7.4)** | **-0.181 +/- 0.057 (z -3.2)** |
| starter's passing yards (R2 43.1 / 42.5) | **-1.45 +/- 0.30 (z -4.9)** | -0.71 +/- 0.41 (z -1.7) |
| team-games with a pull, real / R3 simulated | 0.192 / 0.172 | 0.147 / 0.168 |

**The starter's share of his team's dropbacks, by final margin** (real / R3 / R2):

| | 2025 | 2026 wk 1-12 |
|---|---|---|
| lost by 17+ | 0.884 / 0.888 / 1.0 | 0.856 / 0.887 / 1.0 |
| within 16 | 0.974 / 0.963 / 1.0 | 0.987 / 0.964 / 1.0 |
| won by 17+ | 0.970 / 0.958 / 1.0 | 0.992 / 0.959 / 1.0 |

Reading it:

- **What is shown.** The quarterback's line is more realistic in both seasons. The losing-big share is close to
  real (0.888 against 0.884 in 2025), where R2 gives the starter every snap.
- **Where it is short.**
  - In close games R3 takes 1-2 points of dropbacks from the starter that real starters keep. The injury pulls
    are permanent, and the 2026 real rate is lower.
  - When winning big, R3 gives the backup a little more passing than real backups get. A winning team with a
    backup in mostly runs, which R1's call does not know.
  - 2026's gain is half 2025's, on half the games.

## D. The registration (`research/data/nfl_coach/prereg_r3.json`)

| | |
|---|---|
| block sha256 | `c8c21e27f95204921178e0a52de27a1980d57d93115872f28ea466f63aee0d7a` |
| written | before any test game was read. Regenerated once before its commit: a scenario guard (SQ5) claimed the hazard rises from a tie to a 7-point lead, which the learned hazard does not (0.0058 against 0.0048 a drive at 5:00; a late tie carries more injury-and-end-of-game pulls than a small lead). The guard now claims 7 to 35, and no data had been read |
| pinned by sha256 | R3's engine, tables, `nfl_coach_r3_qb.py`, `_eval.py`, `_read.py`, `tests/guards_auto_nfl_coach_r3.py`; R2's engine, tables, loader and eval; R1's engine, tables, loader and eval; `nfl_deep_sim.py`, `nfl_pbp.py` |
| window | 2026 weeks 13-18 (about 190 team-games; at least 150) |
| read | `python3 -m research.nfl_coach_r3_read <dir>` on or after **2027-01-06 06:00 UTC**, after R1's read (`read_r1.json` committed), from a clean tree; writes `read_r3.json` |
| refuses | before its date or the last game + 48 h; a dirty tree; a block or pinned-file mismatch; a second read; R1 unread; too few team-games |
| settings | 200 simulated games a real game, a level curve of 1,000 a level, a seed per game from its nflverse id |

**All four must hold:**

1. `tests/guards_auto_nfl_coach_r3.py` exits 0.
2. Stage 1: the hazard's gain over the constant hazard is > 2 SE.
3. The starter's dropbacks CRPS: R3 - R2 < -2 SE (better).
4. The starter's passing yards CRPS: R3 - R2 <= +1 SE (not worse).

**Power, stated before the read.**
- Stage 1: z about 3.6 at 190 team-games, power about 0.95.
- Dropbacks: z about 4.8 at 2025's effect, 2.9 at 2026's, so power 1.0 or 0.82.
- Yards: not worse with probability about 0.99.
- Jointly about 0.78-0.94. The likeliest failure is criterion 3, if 2026's smaller gain is the truth.

**The dependence on R1's and R2's reads** (the owner's ruling, 2026-11-27).
- The window's games are R1's registered test games and R2-H1's. R1's statistics are game-level, R2-H1's are who
  got the ball, and R3's are the quarterback's pull and his line: a different question, frozen here before any
  of those games was played.
- The errors are correlated through the shared games: a blowout-heavy stretch moves all three. Joint passes are
  therefore not independent confirmations. Each test's own false-pass rate is unchanged.
- R3 is not tuned on R1's or R2's results, because none has been read.
- Any change to R3, or to the R1 or R2 files it plays on, fails the pinned hashes. That moves R3 to fresh games
  under a new registration.

## E. For the owner, and what comes next

1. **The trailing team's late pull is under-predicted** (A1, about 2.7x in 2025-26). A revision needs a new form
   and fresh games. The registered criteria do not test calibration, and the report says so.
2. **The injury pulls are permanent in the engine**, while 21-26% of real ones end with the starter back (A2).
   Separating injury from rest needs an injury signal the play-by-play does not carry. Recorded, not modelled.
3. **Play selection with a backup** (A5) needs R1's snap loop to know who is at quarterback: a fork, with its own
   test (R4 at the earliest).
4. **Receivers' persistent substitution** is untested (A6), and so are backup receivers' and backs' own
   efficiency.
5. **The coaching-change prior** comes after R3, as the owner set. Not started.

## F. Reproduce, files, guards

```
python3 -m research.nfl_coach_r3_qb <dir> <dir>/players.csv          # A and the tables (~10 s)
python3 -m research.nfl_coach_r3_eval <dir> <dir>/players.csv --sims 200
python3 tests/guards_auto_nfl_coach_r3.py
```

| file | what |
|---|---|
| `nfl_coach_sim_r3.py` | the engine: the hazard, the drives, the pull, R2's deal with the quarterback switched, `simulate` (R2 and R3 on the same games) |
| `research/data/nfl_coach_r3_tables.json` | the hazard's coefficients, the constant baseline, the selection |
| `research/nfl_coach_r3_qb.py` | section A; `research/data/nfl_coach/r3_evidence.json` |
| `research/nfl_coach_r3_eval.py` | section C |
| `research/nfl_coach_r3_prereg.py`, `research/data/nfl_coach/prereg_r3.json` | the registration |
| `research/nfl_coach_r3_read.py` | the registered read (not run) |
| `tests/guards_auto_nfl_coach_r3.py` | 23 guards, section NQ (self-registered, AUTO) |
