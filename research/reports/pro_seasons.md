# The NBA and NHL seasons: the real formats, the season job, the futures board

Built 2026-10-17. Code: `pro_season.py` (standings, tiebreakers, playoff
formats, the Monte Carlo), `pro_season_job.py` (the job, the Kalshi
pricing, the board). Checks: `research/pro_format_check.py`,
`research/pro_season_backtest.py`. Results in `research/data/`.

## 1. The formats reproduce three real seasons

Every real regular-season result of 2023-24, 2024-25 and 2025-26 (ESPN's
monthly scoreboards, the loader the job uses) was fed through
`pro_season.Table`, and the module's own ordering compared with ESPN's
final standings:

| | 2023-24 | 2024-25 | 2025-26 | real ties exercised |
|---|---|---|---|---|
| NHL conference order, all 32 places + the 8 first-round pairs | match | match | match | 11 (two-club, one three-club) |
| NBA conference order, all 30 places (through the real play-in) | match | match | match | 12 (two of them three-club) |

None of the real ties reached the coin flip: the check was re-run with a
random generator that raises on use. Two facts found on the way:

- **ESPN's NBA `playoffSeed` is the seed AFTER the play-in.** The 7-8
  game's winner is 7, the last game's winner 8. The two clubs knocked out
  are 9 and 10 in their regular-season order (2024-25 West: Sacramento
  stayed 9th though Dallas went out a round later).
- The NHL's 2023-24 last Eastern wildcard went to Washington over Detroit,
  both on 91 points, on **regulation wins** (32 to 27). Detroit had more ROW
  (38 to 36) and more wins, so the order RW, ROW, W is what decided it.

What the checks would catch (mutation tests, in the guards): ROW before RW
breaks NHL 2023-24. A three-way NBA tie without the division-winner step,
or without the restart, breaks NBA 2023-24 (Indiana, Orlando and
Philadelphia at 47-35). **Not exercised by any of the three seasons:** the
NBA's two-club division-winner step, and the NBA two-club rules past
conference record. They are written from the rulebook and unverified
against a real tie.

## 2. The season job

Per ordered pair, the board's validated game probability. NBA: production,
calibrated; the deep engine carries weight 0 on the board (its
pre-registered test failed), so it carries none here. NHL: 0.5 production
+ 0.5 deep engine (`hockey._DEEP_WEIGHT`, chosen on 2024-25 and confirmed
on 2025-26). The deep engine's own games give the share past regulation,
the home side's share of those and the shootout share. Every ordered pair
is priced (any two clubs can meet in a series), then 10,000 seasons are
played from the real standings that morning. Server: one niced child under
`deep_cache.HEAVY_BUILD`, PC-first. Measured in a fresh process, 10,000
seasons: NBA peak 40 MB and 75 s of CPU, NHL 68 MB and 302 s (the 992 deep
pairs at 150 games each are most of it; the PC runs 300 a pair on every
core).

**The NHL plays 84 games a club from 2026-27.** ESPN's schedule has 1,344
games, 84 for all 32. The job's quality gate refuses a schedule that is not
the season's length, and the first live run failed it until
`games_per_club` knew.

The NBA Cup's knockout nights are on no schedule until group play ends: 60
unscheduled slots on 2026-10-17 (two a club), paired at random inside each
conference every simulated season. That is an approximation of who plays
whom, not of how many games.

### The NBA's level: Kalshi's win totals

On 2026-10-17 no NBA game had been played, and the validated board model
rates every club the league average until it plays. Last season's ratings
as a prior missed their test by a hair (`pro_prior.md`). A season played on
that model gives all 30 clubs nearly the same title odds; against Kalshi
that reads as large fake "edges" on the worst clubs.

So the NBA's level comes from Kalshi's own win-total ladder (KXNBAWINS, 312
rungs), as the NFL board's does (its walk-forward chose full market weight
over a 6/(6+g) blend). Each club gets a logit offset and a strength SD
fitted so that the simulated season gives back its ladder's **median**
(within 0.5 wins) and **interquartile width**. The means are matched under
the strength draw, because averaging a logistic over a draw pulls every
club toward .500; matched without it, OKC came out 1.5 wins short. A rung
counts only when its book is two-sided within 10c: 280 of 312 were. A dead
book's "mid" read Portland 65+ wins at 49.5% (0c bid, 99c ask).

The ladder is the fit's input, so its own rows are shown on the board
but never edged. A gap there is the fit's shape, not a mispriced market.
On 2026-10-17 Portland "NO on 30+ wins, +10.5" was a missing 35-win rung
(17c wide, dropped) stretching the interpolated quartile.

**This anchor is not validated.** No Kalshi ladder history exists to test
it on. It is the NFL precedent, stated as a choice. With it, the NBA
board's edges mean something specific: **where Kalshi's other markets
disagree with its own win totals once the real format, schedule and
tiebreakers are played out.**

Example, 2026-10-17: Kalshi's "any team 65+ wins" (KXNBAMOSTWINS-27-65)
asked 31c while OKC's own 65+ rung bid 36c. Buying the any-team YES at
31c and OKC 65+ NO at 64c costs 95c and pays at least 100c whatever
happens, before about 3c of fees and with the capital held until May.

### The per-season strength uncertainty (backtest, rule written first)

A season played at fixed probabilities leaves out the uncertainty in each
club's true strength, which is most of a long-range future's width. Every
simulated season draws each club a shift ~ N(0, sd²) in logit, with
sd = TAU x sqrt(K/(K+g)), g the games it has played and K the board's
regression constant (12 NBA, 10 NHL). The NBA's anchored run reads sd off
its ladder instead; TAU is its fallback when the ladder is thin.

`research/pro_season_backtest.py`: the board's model rebuilt on the first
of each month from November to April (games before the date only; NHL
deep tables refit on the seasons before the one scored), every pair
priced, 2,000 seasons per TAU. The rule: Brier per event type (playoffs,
division, best record, finalist, champion; NBA also play-in and No. 1
seed), averaged; select TAU on 2024-25; ship it on 2025-26 if no worse than
TAU = 0.

| | TAU chosen on 2024-25 | 2024-25 score (chosen / TAU 0) | 2025-26 score (chosen / TAU 0) | ships | base rate |
|---|---|---|---|---|---|
| NBA | 0.45 | 0.05848 / 0.05984 | 0.06068 / 0.06340 | **0.45** | 0.11333 |
| NHL | 0.45 | 0.04983 / 0.05004 | 0.06036 / 0.06053 | **0.45** | 0.09570 |

Both chose the top of the grid, and the NBA's score fell steadily with
TAU in both seasons, so the true optimum may lie past 0.45. A wider grid
would be a second look after the test was read, so it was not run. The
NHL's grid is nearly flat (0.0600 to 0.0605 on 2025-26): the term barely
matters there.

**What the season figures are worth, by event (2025-26, Brier, TAU 0.45):**

| | playoffs | division | best record | finalist | champion |
|---|---|---|---|---|---|
| NHL model | 0.139 | 0.070 | 0.004 | 0.058 | 0.0300 |
| NHL base rate | 0.250 | 0.109 | 0.030 | 0.059 | 0.0303 |
| NBA model (unanchored) | 0.106 | 0.088 | 0.008 | 0.061 | 0.0348 |
| NBA base rate | 0.249 | 0.160 | 0.032 | 0.062 | 0.0322 |

Calibration, the same cuts: the NHL's playoff figures run on the
diagonal (logistic slope of outcome on forecast 0.99 on 2024-25, 1.01 on
2025-26); the NBA's 1.36 / 0.95. Division slopes swing on small bins (NHL
1.71 / 0.71). The earliest cut is November 1, about ten games a club: **a
board built earlier is outside what was tested**. The board says so while
the median club is short of ten games. The NHL on 2026-10-17, at 7 games,
gives the top clubs 80-87% to make the playoffs against Kalshi's 91-97%,
which is the regression to average at work, not a measured edge.

Both models earn their playoff, division and best-record figures. **Neither
earns its finalist or champion figure**: the NHL's equal the base rate,
and the NBA's model-only champion figure is worse than it. The board
prints this beside the figures (`pro_season_job.SKILL`). The NBA board
does not run on the model's level (section 2), so its title odds are the
win-total ladder's, played through the format. Taking
the standings that morning as certain beat the model at one event: the
NBA's No. 1 seed in 2024-25 (0.000 against 0.012), a season when the
leaders never changed.

## 3. The futures board

`pro_season_job.SERIES` holds what Kalshi actually lists. The old
`pro_prices.SERIES` asked for KXNBACHAMP, KXNHLCUP and similar, which
match nothing, so no NBA or NHL future had ever been priced.

- NBA: title, both conferences, six divisions, playoffs (the play-in does
  not count), play-in (7-10), exact seeds 2-8 (7 and 8 settled on the
  play-in), "No. N or better" (before the play-in), No. 1 seed, best and
  worst record (ties dead-heat at 1/N; the sim breaks them at random,
  which pays the same in expectation), win totals, any-team N+ wins.
- NHL: Cup, both conferences, four divisions, playoffs, Presidents'
  Trophy and worst record (the league's tiebreakers, as Kalshi's rules
  say).

The club is the ticker's last segment, with Kalshi's codes mapped to
ESPN's (WAS→WSH, GSW→GS, NOP→NO, NYK→NY, SAS→SA, the NBA's UTA→UTAH)
only when the code is not itself a club that season. Utah's NHL club is
UTA at both in 2026-27. Edges are net of Kalshi's taker fee, on YES and
on NO; a book wider than 10c or one-sided is marked thin.

## 4. Found on the way

- **ESPN's bare `/standings` serves the PRESEASON** until opening night.
  The NBA board was rating every club off exhibition games; fixed in
  3b63461 (`seasontype=2`).
- **Every Utah NHL game was missing from the NHL board.** ESPN's `/teams`
  spells the Mammoth UTAH while the standings, the scoreboard and the
  skater feed say UTA. The ratings were keyed the `/teams` way, so a Utah
  game found no rating (skipped) and no Kalshi line. Now keyed by the
  standings' spelling, with `/teams` reached through the shared team id.
- `pro_sim`'s NBA/NHL projection (an 8-seed bracket with no play-in and
  no wildcards) is retired from the nightly jobs. `/api/pro/nba` and
  `/api/pro/nhl` now answer with the season board, where a cold miss used
  to run 4,000 seasons inside a web worker.

## 5. What was not done

- The NBA anchor's weight, and its use of the median, are choices without
  a test. The live record will show whether the board's cross-market
  edges close the way it says.
- A symmetric strength draw cannot reproduce a skewed ladder (injury risk
  fattens a contender's left tail). With dead rungs dropped, the ladders on
  2026-10-17 were close to symmetric (OKC: median 60.3, mean 60.4), but
  the tails are where the board is least sure.
- The NHL board is the board's own model, unanchored: Kalshi lists no NHL
  win or points totals. On 2025-26 production barely beat the home rate on
  single games (`hockey.py`'s header); the backtest above is what its
  season figures earned.
