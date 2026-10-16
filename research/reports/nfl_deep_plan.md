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
and kickoff field position (kickoffs under the 2025+ rules only), the clock
between snaps by what the previous play did (run / incomplete / out of bounds /
score / turnover / penalty) and by tempo (normal / hurry-up / clock-killing),
the extra point (95.0%) and the two-point decision by the score after the six.
Rules that are rules: the two-minute warning, end of quarter and half, the
kneel-out when the lead is safe, the clock-expiring field goal, the late-game
fourth-down overrides a table cannot see, overtime (regular season: ten
minutes, both teams possess, then sudden death, ties stand; postseason:
fifteen-minute periods until decided), onside kicks when trailing late, field
position after a missed or blocked kick, and a safety on a third of the draws
that would reach the offence's own end zone (calibrated to 0.2% of drives).

**Decision: matchups by measured exponents on shrunk rates.** For every unit
stat the fit regresses a team-game's value on its own season rate over the
OTHER games and the opponent's allowed rate over its other games, both shrunk
toward the league with a K from the within-game / between-team variance
decomposition. With shrunk inputs an offence carries ~1.0 of its deviation and
a defence ~0.7: yards per play 1.08 / 0.76 (K 7.3 games), yards per completion
1.13 / 0.69 (K 18.8), completions 1.11 / 0.91 (K 63), sacks 1.01 / 0.36 (K
15.5), interceptions 0.39 / 0.28 (K 14), penalties 0.48 / 0.52 (K 12), pass
rate 0.99 / 0.77 (K 4.1). Fumbles lost: K = 147,000 games — nothing carries,
every team fumbles at the league rate. These are measured (`_matchup_exponents`
in `research/nfl_deep_fit.py`), not chosen.

**Decision: the two free parameters are fitted to real results, not to the
projection.** The zero-sum game-day form tilt `_FORM_SD = 0.085` puts the
engine's margin SD at 13.29 against the real conditional 13.3 (unconditional
14.43 less the spread's own ~5.4; grid 0.06 → 12.91, 0.09 → 13.44, 0.12 →
14.45; the total SD does not move with it). The home yardage edge `_HFA_YDS =
1.02` gives two average teams p_home 0.5415 against the real 0.5402 (1.04 →
0.5665, 1.06 → 0.5994); that realizes +1.18 points of margin against the real
+2.04, deliberately, for the same reason the drive engine's `_HFA_SCORE` does
(skewed margins; the moneyline is what gets graded).

**What the engine reproduces with no points parameter anywhere** (league
average against itself, `pbp_tools/engine_check`): 22.9 points a side (real
22.6), total SD 13.0 (13.7), overtime 5.9% (5.6%), ties 0.22% (0.22%),
62.8 pass+rush plays a side (62.9), pass share 0.576 (0.577), completion
63.9% (64.0%), 10.8 yards a completion (10.7), sack rate 7.0% (6.9%),
interception 2.2% (2.2%), 4.4 yards a carry (4.25; the effective yardage
carries tacked-on penalty yards), conversions by down 22.5 / 34.3 / 40.0 /
53.0% (real 21.6 / 32.6 / 40.4 / 54.9%), red-zone trips 3.5 a side (3.15) at
59% touchdowns (58%), 1.5 kneel-downs a game (1.6), points by quarter 4.64 /
6.40 / 4.72 / 7.00 (real 4.61 / 6.93 / 4.70 / 6.60). Known residuals: drives
run ~5% more numerous and shorter than real, and the fourth quarter scores ~6%
high while the second scores ~8% low.

Speed: ~1.0 ms a game in pure Python.

## 4. The profiles (`nfl_deep_data.py`)

**Decision: shrink toward a measured prior, by the fit's K, with last season's
deviation weighted by its measured persistence.** Year-over-year slopes (two
season pairs, 64 team-pairs each): an offence keeps ~0.42 of its yards-per-play
edge, 0.38 of its pass rate, 0.33 of its completion rate, 0.39 of its yards per
completion, 0.24 of its sack rate; a defence keeps 0.16 / 0.25 / 0.25 / 0.04 /
0.12 of the same; interceptions, fumbles, punting and returns keep nothing. So
before week 1 a profile IS last season times its persistence; by mid-season the
games have taken over the fast stats (pass rate, yards per play) and the slow
ones (completion %, interceptions) still lean on the prior. Kicker, punter and
returners are shrunk in kicks (K 30 / 40 / 40 — judgment, stated as such).

**Decision: anchor the level to the market the codebase already trusts.**
Unit rates say how a team moves the ball; they do not know the roster. Each
team's engine margin (through the measured points–yards elasticity, pts ∝
ypp^1.45) is blended with Kalshi's win-total margin at the weight
`nfl_season._ratings` already gives the prior against live results, 6/(6+games)
(0.5 at six games), and the difference becomes a yardage multiplier, half on
the offence and half on the defence. The team view shows the anchor record
(source, engine read, market read, weight, target) so the residual is visible.

**Decision: Sleeper's weekly team projection is the roster signal, at half
weight, for the current week only.** It is the only football roster signal the
codebase has; a quarterback out moves it directly. A judgment, bounded
[0.85, 1.12].

## 5. The season (`nfl_deep_season.py`)

Every unfinished game played with the engine (real neutral-site flag, each
game on its own random stream seeded from the season seed and the game index,
so paired runs difference cleanly); the real results banked; standings ranked
with the NFL tiebreakers (head-to-head, division record, common games,
conference record, strength of victory, strength of schedule, the coin;
re-applied from the top as a tie shrinks; same-division ties settled by the
division procedure before a wild-card tie, as the rulebook has it); seven seeds
a conference; wild card 2v7 3v6 4v5, the 1 seed's bye, the divisional round
with the 1 seed drawing the lowest survivor, the conference game, the Super
Bowl neutral under postseason overtime. Output: per team the exact-wins and
seed histograms, playoffs / division / No. 1 seed / conference / Super Bowl
counts, mean points for and against; per remaining game BOTH teams' win
probabilities, the tie rate, the mean margin and total; the profiles the run
used; a quality block.

Measured: 200 seasons in 18 s on four cores (90 ms a season with 192 games
left plus the bracket). The server runs 2,000 seasons (one worker ≈ 3 min);
the PC 4,000. Registered as deep job `nfl_deep`: PC first (`_deep_pc_first`),
one heavy build at a time (`HEAVY_BUILD`), a timeline row naming the pool,
None out of season before any fetch, and the quality gate (play-by-play
coverage ≥ 90% of finished games; a prior season or games for every team)
before anything is published. The futures board keeps `nfl_season`'s payload
shape and adds the No. 1 seed market; the team view adds record, projected
wins, seeds, every remaining game with win AND loss probabilities, the unit
profile beside the league, and keeps the player lines.

## 6. The cards: both teams, two models

Every regular-season game on the weekly slate is also played by the deep
engine (600 games; ~8 s of worker CPU per half-hourly build, measured, kept
deliberately small because the board builds in a web worker on a one-core
quota). The pick line now reads "Pick: **Jaguars** 63.7% · Texans 36.3%", and
a line under the win bar shows both models for both sides: "drive model HOU
38.0% – 62.0% JAX · play-by-play deep engine HOU 32.4% – 67.6% JAX (tie 0.4%)
· deep score HOU 19.1 – 24.4 JAX". The headline blends the two at 0.30 on the
deep engine.

**Why 0.30 and not more — the validation.** A 2025 walk-forward
(`research/nfl_deep_validate.py`; profiles from the games before each week
only, 2024 units as the prior, 300 simulated games per real game, weeks 2–18,
256 games):

| model | log loss | Brier | accuracy | margin MAE | slope actual~pred |
|---|---|---|---|---|---|
| deep engine alone (no anchor) | 0.673 | 0.239 | 0.578 | 10.97 | 1.14 |
| deep engine anchored to the PD ratings at 6/(6+g) | 0.655 | 0.231 | 0.594 | 10.73 | 1.26 |
| points-differential ratings (`nfl_season._wp`) | 0.637 | 0.223 | 0.590 | — | — |
| constant home 0.54 | 0.690 | 0.248 | 0.539 | — | — |

The engine has real skill (it beats the constant) and, on moneylines over one
season, less of it than a plain points-differential rating — anchored or not.
Its margin slopes above 1 say its spreads are too narrow later in the season
(and the weeks 2–6 slope of 0.40 says the preseason prior is too confident),
each on ~85 games with a slope standard error near 0.4: suggestive, not
something to tune on. So the deep engine enters the headline at a minority
weight, its raw probability is logged to its own predlog bucket (`nfl_deep`)
and graded apart from the drive model's, and the weight is to be earned from
that record, not argued. Baseball's deep engine entered its headline at 35 on
the same reasoning. Where the engine's value is not in doubt is the structure:
totals, spreads, both-team score shapes, the tie rate, and (next) the joint of
player lines with the game — which a rating cannot produce at all.

## 7. What is deliberately not done yet

- The player dealer still runs off the drive engine's team game; dealing the
  deep engine's team box (pass attempts, completions, yards, sacks, rushes)
  to players would carry the matchup into props and same-game parlays.
- No day-over-day history / attribution (`deep_history`'s twin) for football.
- Timeouts are implicit in the measured clock deltas, not modelled; spikes are
  not modelled; onside recovery (12%) and the safety share (33%) are rules.
- Preseason (exhibition) games stay on the Kalshi-anchored drive path.

## 8. Files

`nfl_pbp.py`, `nfl_deep_sim.py`, `nfl_deep_data.py`, `nfl_deep_season.py`,
`research/nfl_deep_fit.py`, `research/nfl_deep_validate.py`,
`research/data/nfl_deep_tables.json`, `research/reports/nfl_deep_fit.md`,
`research/reports/nfl_deep_validation.md`,
`research/reports/nfl_deep_validation_anchored.md`; wiring in `app.py`,
`nfl_game_sim.py`, `pc_worker.py`, `artifacts.py` (SCHEMA 4), `static/app.js`;
guards at the end of `tests/combo_audit_guards.py` ("The NFL deep engine").
