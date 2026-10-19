# The drive sim's trims, refit for 2026

*2026-10-19. Harness `research/nfl_trim.py` (sha256 `d1a7df33…`, hashed
before it ran); data `research/data/nfl_trim_2026.json`, with
`research/data/nfl_trim_2025_table.json` for 2025 on the same definitions.*

## Why

`nfl_game_sim` trims Sleeper's projections before it simulates. It scales
touchdowns and field goals per drive (`_TD_CAL`, `_FG_CAL`), and pins each
player's mean yards and catches to a per-stat factor (`_PROJ_BIAS`). All of
these were fitted on 2025, a season when Sleeper ran hot: projected touchdowns
landed at about 0.94x, points at 0.969x.

In 2026 Sleeper is not hot. Before this was written, three things had been
read:

- **Real results.** I replayed 2026 weeks 1-6 (92 played games) on Sleeper's
  projections for each week. Real totals averaged 46.24 against Sleeper's
  45.80 projected (+0.44 ± 1.42). The sim averaged 44.02, so it ran **2.21 ±
  1.39 points a game low**.
- **Kalshi.** On the week-7 slate, the sim's median total sat under Kalshi's
  de-vigged median on every game where both could be read (8 of 8). Its
  chance of the over was lower on 12 of 14 games.
- **Sleeper's own 2026 table.** Touchdowns came in at 1.056x the projection
  and points at 1.010x.

## What it cost

The combo maker blends each leg's model number with Kalshi's price; the model
keeps about 20-40% of the weight on totals and props. So a sim that runs low
shows up as fake edge on every under and every NO. Once same-game stacks were
priced honestly (`research/reports/nfl_combo_audit.md`), the maker's slips
were all unders and NOs. Monday night's top pick was "NO - Ekeler 1.5+
receptions": the sim said 75.7% against a 58c market.

## The table, same definitions both seasons

Sleeper's projections against Sleeper's own box scores. Team rows count every
projected QB, RB, WR, TE and K who played. Player rows are the prop-sized
players: passing ≥ 150 projected yards, rushing and receiving ≥ 25,
receptions ≥ 2.5.

| actual / projected | 2025 (weeks 1-18) | 2026 (weeks 1-6) |
|---|---|---|
| touchdowns (team) | 0.957 (549 team-games) | **1.056** (184) |
| field goals (team) | 1.039 | 1.029 |
| passing yards | 0.974 (n 543) | **1.011** (183) |
| rushing yards | 0.958 (976) | 0.922 (302) |
| receiving yards | 0.939 (1,823) | **0.986** (621) |
| receptions | 0.981 (1,803) | **1.025** (624) |
| points (ESPN finals ÷ nfl_data) | 0.969 (shipped) | **1.010** (92 games) |

On these definitions 2025 lands within about 1-2% of the shipped figures
(0.975 / 0.972 / 0.931 / 0.972, touchdowns 0.939), including the same 543
QB-games. So the change is in Sleeper, not in the method.

Each 2026 figure on its own is ± 2-4%. They all moved the same way, and the
same way the points did.

## The refit and its rule

The fit used the 2025 method's two steps, on the week-7 slate's profiles at
2,000 games each with common random numbers:

1. Set `_TD_CAL` so realized touchdowns land at 1.056x the projection:
   **0.992** (was 0.880).
2. Then set `_FG_CAL` so points land at 1.010x: **1.073** (was 1.21).

`_PROJ_BIAS` takes 2026's per-stat ratios: passing 1.011, rushing 0.922,
receiving yards 0.986, receptions 1.025.

**The rule, written first:** ship if, on week 7's games where both medians
can be read, the refit sim's mean gap to Kalshi (sim median minus Kalshi's)
lies within ±1.5 points and is smaller than the old trims' gap. Kalshi's
numbers had already been read, so they are not held out. But the fit never
reads them, so they are an independent check.

| week 7 (sim median − Kalshi median) | old trims | 2026 trims |
|---|---|---|
| mean over 8 games | −2.55 | **−0.26** |
| per game | −0.8 to −4.9, all negative | −2.2 to +2.3, both signs |

**It ships.** On the over side of the week-7 ladders, the old trims sat under
Kalshi on 12 of 14 games, by 2.6 to 10.3 points of probability a game. The
2026 trims average +0.2 over all 48 over rungs.

Monday night's props, where the gap was largest:

| prop | before | after |
|---|---|---|
| passing yards | −6.3 | −3.2 |
| receiving yards | −7.8 | −5.3 |
| receptions | −8.9 | −5.8 |
| touchdowns | −3.1 | −0.5 |

What remains is Sleeper's view of particular players, such as McCaffrey's
receiving, which no league-wide trim should paper over.

## Not changed

The preseason path keeps the 2025 trims (`_TD_CAL_PRE`, `_FG_CAL_PRE`). Its
`_ENGINE_BIAS`, `_ENGINE_HFA_PTS` and `_EDGE_KEEP` were measured through those
trims; a synthetic exhibition profile carries `preseason: True` and rates on
them.

## Re-run it

A season's projection bias changed sign between 2025 and 2026, so these are
in-season constants now. Re-run the harness with
`python3 -m research.nfl_trim 2026 <last week played> <next week>` every few
weeks. Anything it changes ships the same way: by its rule and with its guard.
