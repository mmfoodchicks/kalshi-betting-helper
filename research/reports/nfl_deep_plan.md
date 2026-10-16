# The NFL deep simulation — plan, decisions, what was measured

Owner's brief (2026-10-16): "HEAVILY bolster football. Deep simulations on the
level we saw in baseball. Offense vs defense, as far as we can go for
simulations. Each game needs both teams' probs as right now it just shows 1.
Plan it all out and make the decisions ... on par with the baseball deep sim.
Go as far as you can, then further."

This document is the plan as built, every decision with its reason, and the
figures each decision rests on. Where a figure came from a fit or a validation,
the function that produced it is named; a guard pins each number and the
sentence beside it.

## 1. What existed, and the gap

Football had two engines and no play-level data. The weekly slate
(`nfl_game_sim`) walks alternating DRIVES with per-drive touchdown / field
goal / turnover rates derived from Sleeper's projections; the defence enters
only as a points-allowed multiplier (exponent 0.225). The season sim
(`nfl_season`) is a ratings model: projected wins → expected points → the same
drive engine; no units at all. Baseball's deep sim, by contrast, plays every
plate appearance from per-player rates with the real rotations and the
bracket, publishes nightly from the owner's PC, gates on data quality and keeps
a history. The game cards' win bar already carried both sides, but the pick
line named one team and one number.

## 2. The data: ESPN's play-by-play (`nfl_pbp.py`)

**Decision: the summary endpoint is the source.** Every finished game's
summary carries the drive chart and every play with down, distance, yards to
the end zone, the clock, the effective result (penalties enforced) and the
running score — the first play-level source in this codebase. A season is 272
summaries (~0.3 s and 40 KB gzipped each, measured), parsed into fourteen
integers a play and cached per season in the deep store (`nfl_pbp_<season>.pkl`,
2.6 MB a season), which the PC uploads like any other artifact; finished games
never change, so a game is fetched once. 2023–2026 (914 games, 190,000 play
rows) are the fit; the 2026 store is production's input.

**ESPN quirks found and handled** (each with a parser guard): home-team punt
rows carry the wrong yards-to-go frame (260 of 268 in 80 games of 2025;
rebuilt from the yardLine); "touchdown nullified by penalty" and reviewed
reversals carry the word and no points (a touchdown is ESPN's `scoringPlay`
flag plus the word); 2024's onside kicks are typed "Kickoff Return Touchdown";
extra points are almost never named when missed (1 miss in 420 by text, 55 in
1,302 by the score ledger — the point-after record comes from `scoringPlays`);
a defensive penalty tacked onto a play moves the chains without showing in the
stat yardage (74 of 74 such first-and-tens were penalties on the play), so the
tables store EFFECTIVE yards with a first down floored at the distance.

## 3. The engine (`nfl_deep_sim.py`): snap by snap

**Decision: a play-level engine, every mechanism an empirical table.** The
play call by situation (down, distance bucket, field zone, score state, time
state), the pass chain (sack → interception → completion → yards, by down and
distance), rush yards (by down, distance, zone), the no-play penalty and its
outcome, the fourth-down decision (go / punt / kick by field position,
distance, score, time, smoothed down a hierarchy), the field-goal curve (a
two-parameter logistic in kick distance: 5.94 − 0.0961·yards, n = 3,624), punt
and kickoff field position (kickoffs under the NEWEST rules era the tables
carry — 2025+ in production), the clock between snaps by what the previous play
did (run / incomplete / out of bounds / score / turnover / penalty) and by
tempo (normal / hurry-up / clock-killing), the extra point (95.0%) and the
two-point decision by the score after the six. Rules that are rules: the
two-minute warning, end of quarter and half, the kneel-out when the lead is
safe, the clock-expiring field goal, the late-game fourth-down overrides a table
cannot see, overtime (regular season: ten minutes, both teams possess, then
sudden death, ties stand; postseason: fifteen-minute periods until decided),
onside kicks when trailing late, field position after a missed or blocked kick,
and a safety on a third of the draws that would reach the offence's own end
zone (calibrated to 0.2% of drives). A kickoff the rules owe (the second half,
an overtime period) is QUEUED and played by the game loop before the next snap,
never inline — see §9 for what inline cost.

**Decision: matchups by measured exponents on shrunk rates.** For every unit
stat the fit regresses a team-game's value on its own season rate over the
OTHER games and the opponent's allowed rate over its other games, both shrunk
toward the league with a K from the within-game / between-team variance
decomposition. With shrunk inputs an offence carries ~1.0 of its deviation and
a defence ~0.7: yards per play 1.08 / 0.76, yards per completion 1.13 / 0.69,
completions 1.11 / 0.91, sacks 1.01 / 0.36, interceptions 0.39 / 0.28,
penalties 0.48 / 0.52, pass rate 0.99 / 0.78. Fumbles lost carry nothing
measurable (between-team variance indistinguishable from zero in every season),
so every team fumbles at the league rate. These are measured
(`_matchup_exponents` in `research/nfl_deep_fit.py`), not chosen. The shrinkage
K is the MEDIAN over the three full seasons 2023-2025, every season's value
recorded in the tables: yards per play 5.9 games, yards per completion 13.6,
completions 11.5, sacks 7.9, interceptions 26, yards per carry 22, pass rate
8.7. A team's multiplier is applied to each gain with unbiased random
rounding, so its mean scales exactly.

**Decision: the two free parameters are fitted to real results, not to the
projection.** The zero-sum game-day form tilt `_FORM_SD = 0.080` puts the
engine's margin SD at 13.26 against the real conditional 13.3 (unconditional
14.43 less the spread's own ~5.4; grid 0.00 → 12.03, 0.03 → 12.30, 0.05 →
12.49, 0.07 → 13.12; the total SD does not move with it). The home yardage
edge `_HFA_YDS = 1.021` gives two average teams p_home ~0.537-0.540 against the
real 0.5402 (1.019 → 0.5372, 1.021 → 0.5374 on 40,000 games each, 1.022 →
0.5450 on 60,000; shallow and noisy here); that realizes ~+1.2 points of margin
against the real +2.04, deliberately, for the same reason the drive engine's
`_HFA_SCORE` does (skewed margins; the moneyline is what gets graded).

**What the engine reproduces with no points parameter anywhere** (league
average against itself, 8,000 games): 23.0 points a side (real 22.6), margin SD
13.2 (13.3 conditional), total SD 12.6 (13.7), overtime 5.6% (5.6%), ties
0.22% (0.22%), 62.6 pass+rush plays a side (62.9), pass share 0.573 (0.577),
10.8 yards a completion (10.7), sack rate 7.0% (6.9%), interception 2.2%
(2.2%), conversions by down 22.5 / 34.2 / 39.6 / 52.7% (real 21.6 / 32.6 /
40.4 / 54.9%), red-zone trips 3.5 a side (3.15) at 59% touchdowns (58%), 1.6
kneel-downs a game (1.6), points by quarter 4.70 / 6.35 / 4.71 / 7.04 (real
4.61 / 6.93 / 4.70 / 6.60). Two box figures read high by construction: the
completion rate (65.7% against 64.0%) and yards a carry (4.48 against 4.25),
because the tables store EFFECTIVE results — a defensive penalty that moved the
chains on an incompletion or a short run is stored as the gain it produced.
Known residuals: drives run ~10% more numerous and shorter than real, and the
fourth quarter scores ~7% high while the second scores ~8% low.

Speed: ~1.0 ms a game in pure Python.

## 4. The profiles (`nfl_deep_data.py`)

**Decision: shrink toward a measured prior, by the pooled K, with last season's
deviation weighted by its measured persistence.** Year-over-year slopes
(consecutive full seasons only, 64 team-pairs): an offence keeps ~0.42 of its
yards-per-play edge, 0.38 of its pass rate, 0.33 of its completion rate, 0.39
of its yards per completion, 0.24 of its sack rate; a defence keeps 0.16 / 0.25
/ 0.25 / 0.04 / 0.12 of the same; interceptions, fumbles, punting and returns
keep nothing. Kicker, punter and returners are shrunk in kicks (K 30 / 40 / 40
— judgment, stated as such).

**Decision: the market sets the level, converted to the games LEFT, at full
weight.** Unit rates say how a team moves the ball; they do not know the
roster. Kalshi's win-total ladder prices the WHOLE season, banked wins
included, so it is converted to the market's rest-of-season pace
(`nfl_season.market_pace`: (E[W] − wins banked) / games left × 17) before it
becomes points of margin. That margin is the level's target at weight 1 — the
out-of-sample walk-forward's choice (§6) — and the difference from the engine's
own read becomes a yardage multiplier, half on the offence and half on the
defence, through the engine's own measured points elasticity (1.42, beside the
1.45 measured on real team-games). The engine's "own read" is an analytic
approximation that tracks the simulated margin with correlation 0.92 and slope
0.89 (mean gap 0.68 points against Monte Carlo noise 0.30). On today's board
the simulated rest-of-season margin tracks the target with correlation 0.975
and slope 1.13 — a 13% overshoot at the extremes, recorded rather than tuned on
one board. The team view shows the anchor record (source, engine read, market
read, weight, target).

**Decision: Sleeper's weekly team projection is the roster signal, at half
weight, for the current week only, on the offence alone** (`level_off`). It is
the only football roster signal the codebase has; a quarterback out moves it
directly. A judgment, bounded [0.85, 1.12].

## 5. The season (`nfl_deep_season.py`)

Every unfinished game played with the engine (real neutral-site flag, each
game on its own random stream seeded from the season seed and the game index,
so paired runs difference cleanly); the real results banked; standings ranked
with the NFL tiebreakers (head-to-head — among three or more wild-card clubs a
SWEEP only —, division record, common games — four minimum for every tied club
in a wild-card tie —, conference record, strength of victory and of schedule as
COMBINED records, the coin; re-applied from the top as a tie shrinks;
same-division ties settled by the division procedure before a wild-card tie);
seven seeds a conference; wild card 2v7 3v6 4v5, the 1 seed's bye, the
divisional round with the 1 seed drawing the lowest survivor, the conference
game, the Super Bowl neutral under postseason overtime. Output: per team the
exact-wins and seed histograms, playoffs / division / No. 1 seed / conference /
Super Bowl counts, mean points for and against; per remaining game BOTH teams'
win probabilities, the tie rate, the mean margin and total; the profiles the
run used; a quality block.

Measured on ONE worker the way the server runs it: 225 ms a season with 192
games left plus the bracket (~320 ms with a full schedule left). The server
runs 1,000 seasons (~4-5 minutes on this box, more on Render's core); the PC,
which takes the job whenever it is on, runs 4,000. Registered as deep job
`nfl_deep`: PC first (`_deep_pc_first`), one heavy build at a time
(`HEAVY_BUILD`), a timeline row naming the pool, None out of season before any
fetch, and the quality gate before anything is published: play-by-play coverage
≥ 90% of the finished games, every team's full seventeen-game schedule present
(scoreboard weeks are MERGED into the store by event id; a week that fails to
load is recorded, never dropped), and a prior season or games for every team.

## 6. The cards: both teams, two models

Every regular-season game on the weekly slate is also played by the deep
engine (600 games; ~8 s of worker CPU per half-hourly build, measured, kept
deliberately small because the board builds in a web worker on a one-core
quota). The pick line reads "Pick: **Jaguars** 63.7% · Texans 36.3%", and a
line under the win bar shows both models for both sides. The headline blends
the two at 0.30 on the deep engine; the deep engine's raw probability is logged
with `predlog.log_shadow` to the bucket `nfl_deep` and graded apart.

**The validation — out of sample.** `research/nfl_deep_validate.py`: for 2024
and 2025 the tables are REFIT on the seasons before (2023; 2023-24), profiles
come from that season's games before each week only, every arm runs on the same
games and seeds, 400 simulated games per real game per arm, weeks 2-18, 512
games. PD = the points-differential ratings, standing in for Kalshi's win totals
(no history) and for the drive model (its Sleeper inputs are not archived).

| model | log loss | Brier | accuracy | slope actual~pred |
|---|---|---|---|---|
| deep engine alone (no anchor) | 0.6596 | 0.2332 | 0.613 | 1.87 |
| deep engine, anchored at 6/(6+g) (the first version) | 0.6418 | 0.2251 | 0.621 | 1.39 |
| deep engine, anchored at weight 1 (production) | 0.6317 | 0.2210 | 0.623 | 0.88 |
| points-differential ratings | 0.6331 | 0.2217 | 0.617 | — |
| constant home 0.54 | 0.6915 | 0.2487 | 0.531 | — |
| 0.30 anchored weight 1 + 0.70 PD | 0.6316 | 0.2211 | 0.615 | — |

Paired per-game differences (± one SE): full anchoring beats 6/(6+g) by
−0.0101 ± 0.0073 and in each season on its own; the fully anchored engine is
level with the rating it is anchored to (−0.0014 ± 0.0045); the 0.30 blend is
level with the rating alone (−0.0015 ± 0.0013, slightly better, not
significant). Reading: the unit model's own LEVEL read is the weakest part of
the engine — its margins are compressed (slope 1.87) — so the market sets the
level, and the engine's value is the structure a rating cannot produce: both
teams' score shapes, totals, spreads, the tie rate, the matchup-dependent spread
of outcomes. The 0.30 headline weight is a judgment the evidence neither
supports nor contradicts; the shadow record decides it from here. Out-of-sample
totals run 44.5 against a real 46.1, because a fit on 2023 alone has only 2023's
kickoff rules (receiver 74.5 yards to go against 69 since the touchback moved);
with the 2025+ kickoff table the same fit reproduces the real level, and
production carries it.

## 7. What is deliberately not done yet

- The player dealer still runs off the drive engine's team game; dealing the
  deep engine's team box (pass attempts, completions, yards, sacks, rushes)
  to players would carry the matchup into props and same-game parlays. Next:
  the owner's request to make the Sleeper drive model and this engine work
  with the 40,000-world DFS deep sims, after a full bug run-through of that
  system.
- No day-over-day history / attribution (`deep_history`'s twin) for football.
- Timeouts are implicit in the measured clock deltas, not modelled (the kneel
  rule therefore lets a leader kneel from ~2:05 regardless of the opponent's
  timeouts); spikes are not modelled; onside recovery (12%) and the safety
  share (33%) are rules.
- Red-zone touchdowns are a table sentinel, so a team's yardage multiplier does
  not change its per-play touchdown rate inside the 20 (it changes how often it
  gets there and what the non-scoring plays gain).
- Preseason (exhibition) games stay on the Kalshi-anchored drive path.
- Tables must be refit each offseason (`research/nfl_deep_fit.py`): the prior
  season's units ride inside them, and a new season without them runs on the
  league prior until every team has played.

## 8. Files

`nfl_pbp.py`, `nfl_deep_sim.py`, `nfl_deep_data.py`, `nfl_deep_season.py`,
`research/nfl_deep_fit.py`, `research/nfl_deep_validate.py`,
`research/data/nfl_deep_tables.json`, `research/reports/nfl_deep_fit.md`,
`research/reports/nfl_deep_validation.md`; wiring in `app.py`,
`nfl_game_sim.py`, `nfl_season.py` (`market_pace`), `predlog.py` (shadow
table), `pc_worker.py`, `artifacts.py` (SCHEMA 4), `static/app.js`; guards at
the end of `tests/combo_audit_guards.py` ("The NFL deep engine", and "the
2026-10-16 review fixes").

## 9. Review and fixes (2026-10-16, a second model reviewing the first)

Every finding was measured on the shipped code (f048760) before it was fixed,
and each fix ships with a guard that would have caught it.

| # | What was wrong | Measured | Fix |
|---|---|---|---|
| 1 | The half-time / overtime kickoff was kicked inline in `_end_period`; a punt, turnover or missed kick that ended the half then handed the ball over again, overriding it. A score at the end of the 1st/3rd quarter skipped the kickoff; a touchdown as a period expired advanced the period twice; a fourth-down choice survived into later drives. | 20,000 games: wrong team opened the 2nd half 4.17%, scorer kept the ball 0.85%, quarter skipped 0.04%, stale choice 16% of games | Owed kickoffs queued and played by the game loop; one after-score path; the try scored before an end-of-period ruling; new drives clear the choice. 0 anomalies in 25,000 games. |
| 2 | `predlog` keys `predictions` on the ticker alone; the deep engine's `log_many("nfl_deep")` on the drive model's tickers was silently ignored. The "graded record that earns the weight" recorded nothing. | 0 rows | A shadow table keyed (ticker, model), graded off the owning row; `pairs()` / `vs_market()` / `status()` read it. |
| 3 | `int(round(yds × m))` erased small team multipliers. | m = 1.02: 18% of the intended rushing change survived (43% completions); 3rd-and-2 conversion 63.85% for every m in 0.85-1.15 | Unbiased random rounding; form tilt and home edge refitted. |
| 4 | The anchor's level delivered a fraction of its target; the roster factor moved both sides. | 9-72% of the target margin delivered; a 0.88 factor raised the opponent's points 10.9% | Fixed by (3) (a 1.08 level now delivers ≥ 95%); engine-measured elasticity; roster factor on `level_off` only. |
| 5 | Kalshi's season win total (banked wins included) read as a rest-of-season rating — here and in `nfl_season._ratings`. | Error correlated +0.90 with W−L; MIN at 5-0 anchored +6.2 against the market's own +0.8; the board showed MIN 16.7% to win the Super Bowl against 4¢ | `market_pace`; mean Super Bowl edge on today's board 1.6 points. |
| 6 | The shrinkage K was assigned inside the season loop: every K was 2026's alone (five games a team). | completions 62.7 (full seasons 11-26), interceptions 14 (20-143), yards per carry 6.75 (12-30) | Median over the full seasons, every season recorded. |
| 7 | The anchor blended a live posterior (the market) with the engine at a prior-vs-live weight 6/(6+g). | out of sample: 0.6418 against 0.6317 at full weight | Full weight. |
| 8 | Wild-card head-to-head took any perfect percentage as a sweep; the common-games minimum ranked the short club last; strength of victory / schedule averaged percentages. | Rulebook scenario: the shipped code put BUF first; the rule puts LAC first | Sweep rule, group-wide minimum, combined records. |
| 9 | One failed scoreboard week replaced the stored schedule with the weeks that answered; the coverage gate could not see it. | — (code path) | Events merged by id; failed weeks recorded; a full 17-game schedule required to publish. |
| 10 | The validation scored 2025 with tables fit on 2023-2026 (in sample). | 0.673 in sample against 0.660 out of sample after the fixes | Refit on the seasons before; 2024 and 2025; every arm on the same games; paired SEs. |
| 11 | The engine fell back to the OLDEST kickoff era when 2025+ was absent. | a 2023-24 refit ran totals ~2 points low | Newest era. |
| 12 | Seeds from `hash()` of strings (randomized per process); the server's cost comment read a four-core wall time as per-core. | 3 min claimed, 7.5-11 min measured for 2,000 seasons on one worker | `zlib.crc32`; 1,000 seasons on the server, the measurement in the comment. |
