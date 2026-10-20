# Monday night (WAS @ SF), after the game

*2026-10-20. The owner's final export of the $1.5M Monday Night Showdown
(87,465 entries, 87,284 with a lineup, contest 196786690) against the board
served for it and against S7's frozen field family. Harnesses
`research/sd_postgame.py` (descriptive, both nights) and
`research/s7_prospective.py` (the frozen test); data
`research/data/sd_postgame_154960.json`, `sd_postgame_154959.json`,
`s7_prospective.json`. The export itself is frozen locally and never
committed. Only its summary is (`research/data/dk_fields/dk_field_196786690.json`).*

**Which board this is.** The one archived for Monday is the 2:26 pm MT refresh,
229 minutes before lock. The snapshot workflow did not run after 4:19 pm MT,
so the later refreshes are not on record. Lineups entered from them may
differ from what is scored here.

## A parse bug found on the way: showdown points read 1.5x

A showdown export's player table lists every player twice, once as FLEX and
once as CPT, with the CPT row's FPTS at 1.5x. `dk_standings.parse` keyed the
points on the name alone, so the later row won. That was the captain figure
for 44 of Monday's 55 players (McCaffrey 47.55 for a 31.7-point game) and for
every player on Sunday. The committed summaries and the DFS tab's Real
fields card both read 1.5x.

Fixed: the base score is the FLEX row's, or CPT ÷ 1.5 for a player nobody
played at FLEX. Rebuilt from the fixed points, 5,000 of the first 5,000
entries of each night reproduce their score exactly. Both committed
summaries are regenerated; only the points changed. Classic exports list
each player once and were never affected.

## The field

| | served (0.2% knob) | Sunday's knob (0.468%) | real |
|---|---|---|---|
| pair collision | 2.41e-4 | 5.65e-4 | **6.32e-4** |
| most-copied build | 175 copies | 409 | **747** |
| distinct lineups | 17,418 expected | 11,535 | 14,097 |
| salary, mean | $48,297 | $48,776 | $49,243 |

- **Flatter again.** The served field was 2.6x flatter than the real one. On
  Sunday the gap was 2.4x.
- **Sunday's knob carried over.** It was fitted on Sunday's real field, and it
  lands within 11% of Monday's real collision. The served value is 62% under.
  This is two games and was not pre-registered, so it is a hint, not a test.
  S7 is the test.

**The universe could not hold 43% of the real field.** Only 56.9% of real
entries were made entirely of players in the board's pool (Sunday: 92.6%).
The most-used players outside it:

| player | entries | why the board left him out |
|---|---|---|
| Kyle Juszczyk | 10,119 | "not on any roster Sleeper knows" |
| Treylon Burks | 8,507 (in the winning lineup) | WR4 by depth chart; Sleeper projected 1.47, under the field-only floor of 2.0 |
| Jeremy McNichols | 8,395 | "roster status Inactive" (Sleeper) |
| Jordan Watkins | 4,422 | WR4 by depth chart |
| Dyami Brown | 4,039 | WR5 by depth chart |

These gates are the board's rule for OUR entries. The field universe
inherited them: on Monday the board's field-only list was empty. Wider pools,
measured on Monday's real lineups:

| pool | real entries held | legal lineups |
|---|---|---|
| as served (20 players) | 56.9% | 219,121 |
| + the 4 most used outside | 89.6% | 782,456 |
| + every DK player Sleeper projected ≥ 1.0 (exactly 6: the same six who drew 1%+ of entries) | 96.0% | 1,342,240 |
| + 8 | 97.3% | 2,208,144 |

**The change:** the field takes every DraftKings player with a Sleeper
projection ≥ 1.0, whatever our depth and roster gates say; those gates keep
him out of our entries only. That is a pre-game rule, because it uses no
usage. It needs the scoring pass that scores only our ~23k entries against
the full field: at 1.3M lineups the current pass, which scores every lineup,
would not land inside the pre-lock windows. Not shipped. It is a board
change to make with its own timing check.

## Ownership: the best-lineup share won again

| MAE over the board's players | served field share | best-lineup share (`opt_*_pct`) |
|---|---|---|
| Monday, captain | 2.42 | **1.83** |
| Monday, flex | 7.15 | **6.68** |
| Sunday, captain | 2.07 | **1.60** |
| Sunday, flex | 5.74 | **4.75** |

Monday was not read to find this, so it is two games of two. S7's rules allow
no new feature until three genuinely pre-lock slates have been scored.

## S7, the frozen test: its first prospective contest

Scored on the capture taken 0.9 h before lock (`20261019T232101Z`, the
primary):

- **Salary helps.** The frozen projection+salary field beats projection-only
  by **0.459 nats an entry** (user-cluster 95%: 0.436-0.483) on 97.4% of the
  field it can represent. The retrospective three boards read 0.23-1.01.
- **The morning capture** (11.1 h out, the timing arm) reads 0.523 on 87.8%
  representable. The later capture's pool held more of the field.
- **Concentration is still far off.** Effective lineups: 1,479 real against
  11,650 modelled. Top build: 0.88% against 0.04%. 27% of entries sat in
  lineups with 51+ copies; the model puts none there.

One contest of the three the rules need before anything is chosen.

## The board's lineups on the real points

A rank is one plus the entries strictly above. Cashed means inside the 19,130
paid places. Prizes come from DraftKings' table, ties split.

| Monday (2:26 pm MT board) | top-20 cashed | best rank | paid on $400 |
|---|---|---|---|
| EV list | 9 | 829 | $305 |
| win list | 7 | 829 | $235 |
| top-1% list | 3 | 2,803 | $100 |
| money list | 2 | 10,626 | $60 |

- **Portfolios:** 0 of 5, 2 of 10 and 4 of 20 cashed, none in the top 1%. The
  board had put the 20-entry cover at 52.8%.
- **The hindsight ceiling** for what we may enter was 120.45 (120th). The
  winner (125.8) used Burks, whom our depth gate keeps out of our entries.

Sunday, for comparison, recomputed with the fixed points:

- **Money list:** cashed 4 of its top 5, best 5,572nd. These are the figures
  reported on 2026-10-19, which used the right per-slot points.
- **Win and EV lists:** cashed none of their top 20.
- **20-entry portfolio:** held a 117.06 lineup, 3rd, tied with the field's
  second-best build.

Two games, one each way: the money list won Sunday and the EV list won
Monday. That is noise until there are more.
