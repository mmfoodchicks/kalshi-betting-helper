# NFL coaching engine, R4: personnel-aware decisions after a quarterback substitution

Written 2026-11-27. **Research only: no production file changed, and nothing reads this.** Every file R1's, R2's
and R3's registrations pin is byte-identical (guard N4-0a), and R3's registration and verdict are untouched. The
seal holds: 2026 week 13 on is never read. **The registered read has not been run.**

The owner's chain, all of it emerging inside the simulated game: a lead builds -> R3 pulls the starter -> the
offence adjusts to the backup -> the backs' and receivers' workloads change -> R1's clock and possessions follow
-> the fantasy lines change.

Data credit: nflverse-data play-by-play, players and injury reports (CC BY 4.0). Raw files are gitignored.

## 0. The short version

1. **Two kinds of pull, told apart by the record, never by the margin.**
   - An *injury* pull: the play descriptions say the starter "was injured" before the backup's first drive, or he
     is on the next week's injury report.
   - A *strategic* pull: neither.
   - Counts: 197 and 523 in 2016-2024. Injured starters came back 11% of the time, strategic ones 1%.
2. **After a strategic pull the second unit comes in with the backup** (cell-matched against starters in the same
   margin, time and down):
   - the lead back's share of the backs' carries falls 14.7 points (z -6.9);
   - the top two targets' share falls 6.5 points (z -5.0);
   - the backup completes 3.0 points less;
   - he drops back more when leading (+3.9 points over expected) and less when trailing (-3.2).
   After an injury pull the personnel does not change, but completions fall (-3.0) and interceptions rise (+1.2).
3. **What entered the engine was set by a rule written before the development seasons were read:** 3+ SE on
   2016-2024 and the same sign on 2025-2026 weeks 1-12.
   - Refused by the rule: tempo (z 2.2), the injury backup's call (z -2.99) and the strategic backup's
     interceptions (z 1.8).
   - No evidence for air yards or sacks.
4. **The engine (`nfl_coach_sim_r4.py`, `coach-r4.0`) owns a copy of R1's game loop and plays R1's own snaps
   (unedited).**
   - Between snaps it sets the side's call, completion and interception shifts for whoever is at quarterback.
   - R2's deal gets the second unit's odds.
   - No substitution, no change: R1's game and R3's lines, draw for draw (N4-2, N4-3). Conservation is exact.
5. **Development, decision level** (the registered statistic, real snaps after real pulls): the joint log-score
   gain is **+0.330 +/- 0.119 a pulled team-game (2025) and +0.524 +/- 0.149 (2026 weeks 1-12)**. Simulated
   realism against R3 is unchanged within noise: pulls touch about 15-19% of team-games.
6. **R3's known miss is strategic, not injuries and not late-season rest.** Trailing by 17-27 with 2-8 minutes
   left: 30 pulls against R3's 12.2 in 2024-26, 28 of them strategic, and the late weeks are not where they
   concentrate. R4 keeps R3's hazard unchanged. A new form is an R5 candidate for fresh games.
7. **Registration** (`prereg_r4.json`, block `98308727...`): 2026 weeks 13-18 plus the 2027 regular season, on or
   after 2028-01-13, after R1's read. Power about 0.8-0.9. Listed on the Research tab with R3's.

## A. The evidence (`research/nfl_coach_r4_evidence.py`, `research/nfl_coach_r4_fit.py`)

**Method.** Each of the backup's snaps after a real pull is compared with the starters' snaps in the same cell:
margin (7 bins) x time left (5 bins) x down (1, 2, 3+), over every team-game of the seasons, weighted by the
backup's counts. Score and clock are therefore held. The SE is a bootstrap over the pulled team-games. As-of roles
are the season-to-date leaders from earlier games only.

### A1. Telling injury from strategy: which records are reliable

| record | what it is | reliable? |
|---|---|---|
| the play description, "<starter> was injured" (nflverse `desc`, GSIS text) | an in-game note on the play | **yes, as a positive**: of the pulled starters it names, 50% are on the next week's report, against 5% of the decided-game pulls it does not name (2024-25). **Incomplete**: 41% of the unnamed close-game pulls are on the next report too |
| the next week's injury report (nflverse `injuries`, Out / Doubtful / Questionable) | a pre-game practice and status report a week later | **yes, as corroboration of history**: it says he was hurt, not when. Unusable live (it comes later) and for week 18 |
| the starter's return later in the game | an outcome | **not a predictor**. Injured starters returned 11% (fit) / 14% (development), strategic 1% |
| snap counts | PFR-sourced through nflverse | **not used**: display only under the repo's licence default |

**The label used:** injury = named in a description OR on the next week's report; strategic = neither. A pull's
kind is drawn in the engine from P(injury | the state), a logistic on the margin against the time left fitted on
the 720 fit-season pulls. Its coefficients give a falling injury share as the game is decided.

**Returns** (injury pulls, fit seasons): 22 of 197 came back (11.2%). The backup had 1 identified drive in 73% of
those returns, 2 in 23% and 3 in 5%. Development: 6 of 44.

### A2. What changes with the backup in (fit seasons 2016-2024 | development 2025-2026 weeks 1-12)

| | strategic | injury |
|---|---|---|
| pulled team-games | 523 \| 106 | 197 \| 44 |
| dropbacks over expected, leading 9+ | **+3.9 +/- 1.3 (z 3.0)** \| +0.6 | -0.8 (n.s.) |
| ... trailing 9+ | **-3.2 +/- 1.0 (z -3.2)** \| -9.8 | -1.7 (z -1.7) |
| ... within 8 | **-4.6 +/- 1.4 (z -3.4)** \| -5.6 | -4.1 +/- 1.4 (z -2.99: refused by the rule) |
| completion | **-3.0 +/- 0.9 (z -3.2)** \| -3.3 | **-3.0 +/- 0.85 (z -3.5)** \| -5.8 |
| interceptions | +0.55 (z 1.8) | **+1.15 +/- 0.36 (z 3.2)** \| +1.0 |
| seconds to the next snap after an in-bounds run | +0.51 (z 2.2) | +0.31 (z 1.1) |
| the lead back's share of the backs' carries | **-14.7 +/- 2.1 (z -6.9)** \| -25.8 | -0.1 (n.s.) |
| the top two targets' share of targets | **-6.5 +/- 1.3 (z -5.0)** \| -9.5 | +0.2 (n.s.) |

(points of rate; bold = entered by the rule. The audit pass on the description label alone, `r4_evidence.json`,
found the same pattern, and also no evidence on air yards (-0.02 +/- 0.27 strategic) or sacks.)

- **What it says, in football terms.** When the game is decided the whole first unit sits, not just the
  quarterback. The backup completes fewer passes, and leading he is let throw a little where the starter would
  not. Trailing, the team runs more and lets the clock go.
- **An injury replacement is different.** The other starters stay in and their shares do not move. The
  replacement is simply worse: fewer completions, more interceptions.
- **Engine form.** The effects enter as logit shifts on R1's own call, completion and interception
  probabilities, and as Mantel-Haenszel odds ratios (x0.485 the lead back, x0.741 the top two targets) on R2's
  deal.

### A3. Other skill-position personnel changes: what the evidence supports now

- **Supported and built:** the second unit's backs and receivers coming in with a STRATEGIC backup quarterback.
- **Not supported:**
  - a persistent rest pull for backs alone. R3 measured rotation: after a backup's first carry with a 17+ lead,
    the lead back takes 41% of the rest against 43% of the state, which R2 already deals.
- **Not built, data available:**
  - in-game injuries to backs and receivers. The same description signal names them; that is the natural R5
    extension.
- **Not available in season:** who is on the field (nflverse participation is 2016-2025 only).

### A4. R3's known miss: trailing by 17-27 with 2-8 minutes left

2024-2026 weeks 1-12, R3's hazard against the real pulls: **30 against 12.2** in 137 drives.

| is it ... | the evidence | verdict |
|---|---|---|
| injuries? | 2 of the 30 are injury-labelled | no |
| late-season rest? | weeks 15-18 hold 22 of the 137 drives (16%) and 4 of the 30 pulls (13%), against 1.9 expected there | no |
| strategic trailing pulls? | 28 of 30 | **yes** |

- **What it is.** Trailing teams protect their quarterback late more than R3's smooth form allows. The rise is
  recent: R3's half-life-2 weighting helps but does not reach it.
- **What would fix it.** A fix is a new hazard form, refit through 2025 and chosen on data R3's window cannot see,
  then judged on fresh games under its own registration.
- **R4 does not do it.** R4 keeps R3's hazard, so that R4's test isolates the consequences of a pull. R3's
  registration and verdict are untouched.

## B. What was built (`nfl_coach_sim_r4.py`, `coach-r4.0`)

- **The fork.** R4 has its own copy of R1's `play_game` so that it holds the two side objects. It plays every snap
  with R1's unedited `_snap`. Before each snap it sets each side's `d_call` (by the current margin), `d_comp`,
  `d_int` and `tempo` for whoever is at quarterback (N4-8), and it restores them when the starter returns.
- **The pull is R3's.** R3's drive parser runs event by event and draws the hazard at each drive's first
  identifying snap, with the state at the drive's first snap. It is the same drive R3 draws after the game (N4-5).
  Pulls, kinds and returns use their own generator.
- **The deal is R3's**, with the quarterback switched from the pull's drive until a return, and R4's odds ratios
  on the lead back and the top two targets while a strategic backup is in. `simulate` deals each batch as R3
  does: R2's TeamDeal calibrated to the batch's mix, on R3's per-side deal generators.
- **No substitution, no change.**
  - R4 with no pull is R1's game draw for draw: the box and the generator after it (N4-2).
  - Its lines are R3's deal's, line for line (N4-3).
  - With the pull on and its consequences off, the games are still R1's (N4-3b).
- **Conservation is exact** (N4-4, 1,500 games, with consequences on).
- **Kinds and returns:** both kinds are drawn, only injured starters return, and the starter has the ball after
  the return (N4-6).
- **The chain, played from the state:** leading by 35 with 10:00 left, the lead back's share of the carries and
  the top receiver's of the targets fall after a strategic pull (N4-7).
- **One measured lesson while writing the guards.** A whole-game pass share cannot check the call shift: the
  backup's incompletions stop the clock, add snaps, and the games diverge after the pull. Trailing-big attempts
  ROSE with the shift on. The shift is checked where it acts, at every snap (N4-8).

## C. Development (`research/nfl_coach_r4_eval.py`, `research/data/nfl_coach/dev_r4.json`)

**Decision level, the registered statistic.** Real snaps after each real pull, the kind labelled as the fit
labelled it (2026: descriptions only, because the 2026 injury reports were not fetched). The baseline is the
starters' own cell rate, or xpass for the call; R4 moves it by the frozen effects. Log score per pulled team-game,
positive = R4 better:

| | 2025 (102 pulled team-games) | 2026 wk 1-12 (48) |
|---|---|---|
| **joint** | **+0.330 +/- 0.119 (z 2.8)** | **+0.524 +/- 0.149 (z 3.5)** |
| personnel (lead back; top two targets) | +0.260 +/- 0.120 | +0.395 +/- 0.137 |
| call | +0.046 +/- 0.017 | +0.088 +/- 0.033 |
| completion | +0.024 +/- 0.017 | +0.042 +/- 0.032 |

**Realism** (every real game from each team's 4th game, 100 simulations by R3 and by R4 from the same seeds; CRPS,
R4 - R3 per team-game):

| | 2025 (448) | 2026 wk 1-12 (232) |
|---|---|---|
| the real lead back's carries | -0.007 +/- 0.019 | +0.015 +/- 0.029 |
| the real top two targets' targets | -0.013 +/- 0.018 | -0.013 +/- 0.025 |
| the starter's dropbacks | -0.015 +/- 0.033 | +0.014 +/- 0.049 |

- **What is shown.** Who gets the ball after a real pull, and how the backup calls and completes, is predicted
  better in both development seasons.
- **What is not shown.** That season-wide simulated player lines are measurably better. Pulls touch about 15-19%
  of team-games, and the team-game CRPS moves inside one SE.

## D. The registration (`research/data/nfl_coach/prereg_r4.json`)

| | |
|---|---|
| block sha256 | `98308727af262a2520cd55b1d52a41ea0889ffb2ff77a2418a35e3bbfcbb8e72` |
| written | before any test game was read. Regenerated twice before its commit: guard N4-8 first checked whole-team attempts, then a whole-game pass share; both wrong for the reason in B. No data had been read |
| window | 2026 weeks 13-18 and the 2027 regular season (about 125 pulled team-games; at least 80) |
| read | `python3 -m research.nfl_coach_r4_read <dir>` on or after **2028-01-13 06:00 UTC**, after R1's read, from a clean tree; writes `read_r4.json`. The directory needs `play_by_play_2026/2027.csv.gz`, `players.csv` and `injuries_2026/2027.csv` |
| refuses | before its date or the last game + 48 h; a dirty tree; a block or pinned-file mismatch (R4's, R3's, R2's, R1's and production's deep-engine files); a second read; R1 unread; too few pulled team-games |

**All four must hold:**

1. `tests/guards_auto_nfl_coach_r4.py` exits 0.
2. The decision-level joint gain is > 2 SE.
3. Personnel, call and completion are each > -1 SE.
4. Realism guardrail: the lead back's and the top two targets' CRPS, R4 - R3 <= +2 SE.

Criterion 4 is a 2-SE bound, set before the data, because R4's expected team-game difference is about 0. A 1-SE
bound would fail about 30% of the time with no harm done.

**Power, stated before the read.**
- Criterion 2: about 0.9-1.0 (z about 3.2-5.0 at about 125 pulled team-games).
- Criterion 3: about 0.95.
- Criterion 4: about 0.95.
- Overall about 0.8-0.9.

**The dependence.**
- 2026 weeks 13-18 are R1's, R2-H1's and R3's registered games; 2027 is fresh. R4's question is what happens
  AFTER a pull, a different statistic, frozen before those games were played.
- The errors are correlated through the shared weeks, so joint passes are not independent confirmations.
- R4 is tuned on no read, and R3's verdict is not an input.
- Any change to R4, or to the R1-R3 files it plays on, fails the pinned hashes and moves R4 to fresh games.

## E. For the owner

1. **R3's trailing-late miss** is strategic (A4). A new hazard form is an R5 candidate, refit through 2025, on
   fresh games.
2. **In-game injuries to backs and receivers** can be labelled the same way (A3). That is the next personnel
   extension.
3. **Tempo with a backup** (+0.5 s, z 2.2) and **the strategic backup's interceptions** (z 1.8) were refused by
   the rule. They are recorded, not modelled.
4. **The 2026 injury reports** should be fetched before any 2026 development rerun; the 2026 labels here are
   descriptions only.

## F. Reproduce, files, guards

```
# play_by_play 2016-2026 and players.csv in <dir>; injuries_2016..2025.csv in <inj dir>
python3 -m research.nfl_coach_r4_evidence <dir> <dir>/players.csv <inj dir>
python3 -m research.nfl_coach_r4_fit <dir> <dir>/players.csv <inj dir>
python3 -m research.nfl_coach_r4_eval <dir> <dir>/players.csv <inj dir> --sims 100
python3 tests/guards_auto_nfl_coach_r4.py
```

| file | what |
|---|---|
| `nfl_coach_sim_r4.py` | the engine: the fork of R1's loop, the in-game pull and its kind, the shifts, the deal |
| `research/data/nfl_coach_r4_tables.json` | the effects (as the rule left them), the kind model, returns, the starters' cell rates |
| `research/nfl_coach_r4_data.py` | air yards, injury mentions and names from nflverse, R1's seal kept |
| `research/nfl_coach_r4_evidence.py` | A (description-only labels); `research/data/nfl_coach/r4_evidence.json` |
| `research/nfl_coach_r4_fit.py` | A with the two-record label and the rule; `research/data/nfl_coach/r4_fit.json` |
| `research/nfl_coach_r4_eval.py` | C; `research/data/nfl_coach/dev_r4.json` |
| `research/nfl_coach_r4_prereg.py`, `research/data/nfl_coach/prereg_r4.json` | the registration |
| `research/nfl_coach_r4_read.py` | the registered read (not run) |
| `tests/guards_auto_nfl_coach_r4.py` | 21 guards, section N4 (AUTO) |

## H. The owner's checks after reading R4 (2026-11-27; report only, R4's registration unchanged)

### H.1 The injury report is a training label, never a play-time input

These are all the inputs R4 reads while a game is played (`nfl_coach_sim_r4.py`):

- **The pull** (`_Track._decide`) reads two things from the drive's FIRST snap: the offence's margin and the game
  seconds left. The margin comes from the score logged before that snap; the seconds come from its period and
  clock. Add R3's coefficients and `prng`.
- **The kind of pull** is `p_injury(X, margin, seconds)`, a four-term logistic on that same state, drawn on `prng`.
- **A return** draws on `prng` against the tables' rate and drive counts.
- **The shifts and the second unit** read the tables' effects for the kind that was drawn, plus the current
  margin.

R4 reads nothing else. No report, no play description, no player status, no season or week, and nothing from
after the snap. The next week's injury report and the descriptions' "was injured" are read in exactly one place,
`research/nfl_coach_r4_fit.py` (`reports`, `make_cls`). There they label the 2016-2024 pulls, and those labels fit
`type_coef` and the per-kind effects. The tables hold aggregates only.

Guards in `tests/guards_auto_nfl_coach_r5.py`, which apply to R5 as well:

- **N5-T1** parses the engine's source. It requires that the engine imports only the engines and the standard
  library, opens a file only in `tables()`, and names no report, description, season, week or player id. It also
  requires that `Tables` reads only the fitted values.
- **N5-T2** checks that the tables contain no player id, game id or play text.
- **N5-T3** intercepts every call to the hazard and to `p_injury` over 30 played games with 25 pulls. The
  arguments must be exactly the first-snap margin and seconds of each identified drive, as R3's post-game parser
  recovers them from the log. The calls must also come in the right order: up to and including the pull drive,
  then none.

What the decision is conditioned on: it is drawn at the drive's first quarterback-identifying snap. That is
whether the drive has one, which is the same condition the fit used. The identifying snap's own outcome is never
read. One consequence: the first identifying snap of a pulled drive is played under the starter's shifts. The deal
still credits the whole drive to the backup, as R3 does. This is documented, not hidden, and the same holds in R5.

### H.2 Classification sensitivity (`research/nfl_coach_r4_checks.py`, `research/data/nfl_coach/r4_checks.json`)

Two groups of pulls can be labelled either way:

- **A1** (73 fit pulls): the play does not name the starter, but the next report lists him. Labelled injury; the
  listing may predate the game.
- **A2** (111 fit pulls): not named, not reported, and the game not decided. Labelled strategic; it could be an
  unrecorded injury or a benching.

Every shift was refitted with each group flipped and with both flipped, under R4's own rule (3 SE on the fit
seasons, same sign in development), and the development gain was recomputed with each variant's own labels.

| variant | fit injury / strategic | strategic: call lead, trail, close | comp | lead back OR | top-two OR | injury: comp, int | dev joint gain 2025 / 2026 |
|---|---|---|---|---|---|---|---|
| as registered | 197 / 523 | +0.158, -0.131, -0.185 | -0.128 | 0.48 | 0.74 | -0.127, +0.371 | +0.33 +/- 0.12 / +0.52 +/- 0.15 |
| A1 as strategic | 124 / 596 | refused, -0.125, -0.148 | -0.118 | 0.54 | 0.83 | -0.151, refused | +0.21 +/- 0.12 / +0.49 +/- 0.14 |
| A2 as injury | 308 / 412 | +0.233, refused, refused | refused | 0.28 | 0.63 | -0.116, +0.318 (close -0.174) | +0.30 +/- 0.08 / +0.37 +/- 0.14 |
| both flipped | 235 / 485 | refused x3 | -0.132 | 0.44 | refused | -0.125, refused (close -0.202) | +0.12 +/- 0.10 / +0.28 +/- 0.10 |

How each effect held up:

- **Robust:**
  - The strategic lead back's odds ratio survives every variant, ranging from 0.28 to 0.54.
  - The injury backup's completion shift survives every variant (-0.12 to -0.15).
  - The development joint gain stays positive in all four.
- **Fragile:**
  - The top-two odds ratio and the strategic completion shift each survive 3 of 4.
  - The strategic call shifts depend on the labels: each margin's shift is refused in at least one variant, and
    the leading call is refused under A1 and under both flipped.
  - The injury interception shift is refused when A1 moves to strategic.

Two caveats:

- **What the brackets cover.** Labelling a group all one way and then all the other brackets the moved
  statistics. A fitted-probability weighting would land between the extremes for the effect sizes, though not
  necessarily for the rule's keep or refuse decisions.
- **What this means for the claims.** R4's registered read uses the registered labels and stands as written.
  The labels decide how much of the call claim survives, but not the personnel claim or the completion claim.

### H.3 The second unit: per-snap odds, not one sit decision

R4 multiplies the lead back's odds (x0.48) and the top two targets' odds (x0.74) on every snap after a strategic
pull. It never forces them out together, and it never seats anyone: each keeps a reduced share.

The real decisions are mostly whole-game (2016-2024, 293 strategic pulls with 3+ backs' carries after):

| after a strategic pull | real 2016-2024 | R4 simulated |
|---|---|---|
| the lead back gets no carry for the rest of the game | 50.2% | 4.2% |
| his share of the backs' carries, quartiles | 0, 0, 0.60 | 0.38, 0.50, 0.61 |
| both top-two targets get nothing (3+ targets after) | 19.6% | 2.0% |

R4's mean shift is about right, but the shape is wrong: the real distribution is bimodal, sit or play. R5 draws
the decision per player (`research/reports/nfl_coach_r5.md`). Its simulated rates are 48.8% for the back and
21.3% for the top two, against the real 51.3% and 24.5% on R5's own definitions.
