# Accuracy gaps: the updates we do not have (independent survey, Fable)

*2026-10-29, 15:30 UTC. The owner: "look through and talk through any updates we
don't have that would give us better simulation accuracy." This is my draft,
written before reading the orchestrator's. Evidence is labelled: **[live]** the
2026 records on `origin/sim-history` (`errors/sharp-latest.json`,
`nfl-record-latest.json`, `cfb-record-latest.json`, `calib-params-latest.json`,
read 2026-10-29); **[repo]** this repo's research reports; **[measured here]**
small computations made for this survey today (the code is in the text);
**[outside]** established public knowledge, never a number measured here.
Nothing below changes a board; every candidate carries the rule that would
have to be registered before it could.*

## 0. What the live record says first

Model against Kalshi's price at log time, Brier, lower is better **[live]**:

| bucket | model | market | n | close moves toward us | reading |
|---|---|---|---|---|---|
| tennis | 0.2177 | 0.2309 | 32,858 | 57.9% of 3,068 (+5.4c) | the one model that beats the price |
| nfl | 0.2284 | 0.2325 | 146 | 52.5% of 122 (+1.7c) | beats Kalshi's EARLY price; loses to the consensus close (`game_xref.md`) |
| nfl_pre | 0.2804 | 0.2850 | 62 | - | market-anchored by design |
| nfl_deep (shadow) | 0.2684 | 0.2559 | 54 | - | worse than the drive sim live |
| nba | 0.2504 | 0.2200 | 106 | - | the widest game-market gap; October ratings are noise (section 4) |
| nba_deep (shadow) | 0.2316 | 0.2210 | 78 | - | far better than production live, blend weight 0 |
| nhl | 0.2406 | 0.2267 | 194 | 66.7% of 12 | second-widest gap; no goalie, no prior (section 5) |
| nhl_deep (shadow) | 0.2299 | 0.2277 | 106 | - | near the market; blended at 0.5 |
| soccer | 0.2023 | 0.1967 | 1,896 | 60.6% of 492 (+3.3c) | loses to the close, beats the open |
| soccer_btts / soccer_dk | 0.2447 / 0.196 | 0.2448 / 0.195 | 233 / 1,296 | - | even |
| mlb props | worse in every bucket but extras / sb / rfi | | | | ks -0.032 and total -0.022 log loss are the widest; both get blend weight 0.0 live |
| golf, ufc, racing | no vs-market rows | | 358 / 411 / 0 graded | | the record cannot judge them (section 1, C3) |

Two patterns run through it. (1) Where the model has positive closing-line
value (tennis, soccer, NFL) the value is EARLY: Kalshi's first prices lag,
the close catches up. (2) The two game engines that know nothing but this
season's points for and against (NBA, NHL production) are the two worst
against the price, and in both leagues the deep engine, which carries a
committed prior from last season, reads far better live. The shared
calibration has already fitted NBA a damped t of 1.331 on 106 rows **[live]**
-- it is shrinking October probabilities built on four games of efficiency.

## 1. Cross-cutting candidates

### C1. A book line as the anchor and benchmark for every game market
1. **What.** Read the pre-game line of a real sportsbook (ESPN's core odds
   record: DraftKings and ESPN BET, `open`/`current`, spread, total,
   moneyline) beside Kalshi's at log time, for NFL, college, NBA, NHL and
   soccer (soccer already has it: `soccer_dk`). Use it two ways: as the
   LEVEL the model is anchored to where today's code anchors to Kalshi or
   to nothing, and as a shadow benchmark that says whether Kalshi's price
   or the book's is the better judge.
2. **Evidence.** [measured here] the closing book line beat every model and
   every stack in both football codes (`game_xref.md`: NFL +0.025 nats,
   college +0.036 against the ratings, nothing added to it by any feature).
   [live] our NFL sim BEATS Kalshi's log-time price (0.2284 vs 0.2325) while
   losing to the consensus close -- Kalshi's early football prices are
   softer than a book's. [outside] closing lines of sharp books are the
   best public forecast of game outcomes; exchange prices converge to them.
3. **Missing today.** `nfl_game_sim._build_board` (nfl_game_sim.py:1151-
   1346) and `basket._build_board` / `hockey._build_board` read Kalshi only;
   `cfb_board._market_margin` (cfb_board.py:272-284) anchors to Kalshi's
   spread midpoint only; `nfl_deep_data.market_margins` anchors the deep
   engine to Kalshi's win totals (nfl_deep_data.py:227-241). No bucket
   records a book's line except `soccer_dk`.
4. **Data.** ESPN core API `.../events/{id}/competitions/{id}/odds`
   (verified today on an upcoming NBA game: DraftKings spread -1.5, total
   237.5, moneylines; the same record this repo already reads for soccer).
   Free, unofficial, the terms this repo already lives with. NEVER the
   "Live Odds" provider rows (in-game numbers). The-odds-api (`odds_api.py`,
   500 requests a month free, its own key in the environment) gives a 7-8 book consensus for
   the thin markets where ESPN carries nothing.
5. **Test.** Shadow-log the book's de-vigged line beside Kalshi's for every
   game bucket (a `*_book` shadow like `soccer_dk`). Rule: after 10 dates
   and 150 games per league, book vs Kalshi log loss on the same games,
   clustered by date; where the book wins by 2 SE it becomes the anchor
   LEVEL at the weight the league's existing anchor rule uses (college
   25%, NFL per the registered 8.3 test, NBA/NHL a weight chosen by the
   same walk-forward on 2024-26 replays with ESPN's stored closes, which
   exist for all three). Expected effect sizes are known from the replays:
   NBA/NHL production are 0.03 Brier behind Kalshi live, so the gain against
   the OUTCOME is of that order; against Kalshi's price the gain is the
   soft-early-price effect (NFL +0.004 Brier live).
6. **Gain / risk.** The largest single accuracy gain available anywhere in
   the app for game markets; the risk is that it makes the headline the
   book's number (as soccer's already is), which the owner must hear
   plainly, and that ESPN's odds feed can change provider or vanish (it
   did between 2025 and 2026).
7. **Priority 1.**

### C2. A graded record for everything (plumbing, not a model)
1. **What.** Golf, UFC and racing cannot be judged against Kalshi today.
2. **Evidence.** [live] `golf` 358 graded, `vs_market` n 0; `ufc` 411 graded,
   n 0; no `f1`/`nascar` bucket exists.
3. **Missing today.** golf.py:452-457 logs `(ticker, model p, None)`: no
   market price, no close time. predlog.py:640-650 logs UFC rows with
   `mkt_win` from the fair table, which arrives None (every row). The racing
   boards log nothing.
4. **Data.** Kalshi's own asks, already on the boards.
5. **Test.** None needed; it is what makes every later test possible.
6. **Gain.** Indirect but necessary. 7. **Priority 2 (do first; small).**

### C3. The season simulators play compressed engines
1. **What.** The college season/CFP sim (`cfb.run_season`) and the NFL
   season sim (`nfl_season.run_season`) resolve games with the drive engine
   at `_rates_for` / `_drive_rates`, whose mean margin is a fraction of the
   margin it is given.
2. **Evidence.** [measured here] NFL season engine at `_matchup_pts`
   margins +2.2 / +6.7 / +11.2 / +15.7: realized +1.5 / +5.5 / +9.4 / +13.2
   (84% at the top), p_home 0.838 against `_wp`'s 0.881 at a 6-win gap.
   College: `game_xref.md` 3.2 (slope 1.37-1.44, -5 points on cross-division
   games), and the season sim adds a 2.6-point form draw on top. So every
   favourite's win total, division, playoff and title probability is
   under-stated and the field's over-stated -- on futures Kalshi books
   (KXNFLWINS, division, playoffs, KXNCAAF, KXNCAAFPLAYOFF).
3. **Missing today.** cfb.py:425-429 (`_rates_for` + `_play`, form 2.6),
   cfb.py:422 (FBS over FCS at a flat 0.95), cfb.py:437-453 (CFP selection
   by `0.6 x wins + rating + form`, no committee input, no conference title
   games); nfl_season.py:45-52 of `run_season` (`_play(_drive_rates(...))`).
   The deep NFL season (`nfl_deep_season`) is anchored and not affected.
4. **Data.** None new for the engine fix. For the CFP selection, ESPN's
   rankings feed carries the committee's weekly ranking from early November
   (`.../college-football/rankings`, free).
5. **Test.** Replay 2024 and 2025 seasons week by week: projected win totals
   and playoff/CFP probabilities from the compressed engine vs the analytic
   resolution (Phi(margin/sigma), sigma 18 college / 13.2 NFL, the numbers
   the boards already use) scored against the real win totals (MAE) and
   playoff outcomes (log loss); and vs Kalshi's win-total ladder where the
   sim-history nightly history holds it. Rule: ship the resolution that wins
   both seasons by 2 SE (clustered by team). For the CFP field, the
   committee's own last ranking as the ranking metric from the week it
   exists, tested on 2024-25 bracket membership (24 slots).
6. **Gain / risk.** Futures accuracy only; medium. Low risk: it removes a
   known defect.
7. **Priority 5.**

## 2. NFL

### N1. A second projection source for the player means (props, DFS, the team level)
1. **What.** Average Sleeper's weekly projections with ESPN's fantasy
   projections (component stats per player per week) before the sim.
2. **Evidence.** [outside] consensus projections beat any single source in
   every public accuracy comparison (lower MAE by several percent); the
   per-stat biases of a single source swing year to year, which [repo]
   `nfl_trim.md` measured on Sleeper (touchdowns 0.957x in 2025, 1.056x in
   2026) and [measured here] the team-points level swung -2.45 / +1.45 /
   -0.17 by season. A second source averages part of that away.
3. **Missing today.** `nfl_data.week_teams` (nfl_data.py:80-170) is Sleeper
   alone; `nfl_dfs_sim.weekly_games` (nfl_dfs_sim.py:86) likewise.
4. **Data.** ESPN fantasy API (`lm-api-reads.fantasy.espn.com/.../
   leaguedefaults/3?view=kona_player_info`, verified today: weekly
   projections per player, statSourceId 1), free, unofficial, same terms as
   ESPN's other feeds; no history, so the test is prospective.
5. **Test.** Register now: from week 10, store both sources' projections at
   the slate build; score 0.5/0.5 vs Sleeper alone on Sleeper's box per
   stat (yards, receptions, TDs) by MAE and by the rung log's prop log loss,
   weeks 10-18, clustered by week; pass > 2 SE. Power: ~1,500 player-games a
   week; a 3% MAE gain is ~5 SE over nine weeks.
6. **Gain / risk.** Props and DFS means a few percent tighter; the team
   level's season drift halved. Low risk; cheap.
7. **Priority 7.**

### N2. The rushing prop distribution by role
1. **What.** The sim draws every rusher's yards with one lognormal SD
   (`_RUSH_LSD` 0.92, nfl_game_sim.py:125); a featured back and a
   change-of-pace back get the same relative spread.
2. **Evidence.** [repo] `nfl_accuracy_gaps.md` item 5 and the drift alarm
   (2026-10-22): rushing yards sat -4.4 points under Kalshi's mid over 127
   rungs, "the sim leaves a featured back too much mass near zero"; the
   rushing SD ratio read 1.11 against Kalshi's ladders. The rushing MODEL's
   matchup groups failed their registered test and are not re-proposed;
   this is the distribution's shape, a different object.
3. **Missing today.** nfl_game_sim.py:124-127, one SD per stat for all
   players; the compound opportunities-times-efficiency structure that
   `nfl_rush.py` already implements is never used for the shape.
4. **Data.** Sleeper box scores (in the deep store) for the fit; the frozen
   rung log (`nfl_props_log`) for the test.
5. **Test.** Fit role-conditional SDs (carries share >= 55% / 30-55% / under)
   on 2025 boxes; register; score the rung log's rushing rungs weeks 10-16
   by log loss vs the shipped shape, clustered by game; pass > 2 SE.
6. **Gain / risk.** Prop and combo legs on rushing yards stop reading a
   fake edge on low rungs; medium for the combo maker, nothing for games.
7. **Priority 9.**

### N3. Already registered, not re-proposed
The market anchor (game_xref 8.3), the late-season PD blend (8.4 item 7),
the total level off Kalshi's ladder (8.4 item 8), the prop-ladder anchor
(`research/nfl_prop_anchor.py`, weeks 8-10), the usage port to the combo
maker (`nfl_accuracy_gaps.md` item 7), weather stage 2 (after week 18). The
deep engine reads worse than the drive sim live (0.2684 vs 0.2559 market)
and nothing new is proposed for it.

### N4. DFS
The real-field summaries pipeline and `field_refit` (week 7+), the
`cl_own_w7b` ownership test and `nfl_prop_anchor` are the open items; N1
reaches the DFS means through `weekly_games`. Nothing else here.

## 3. College

### G1. The season/CFP sim: see C3 (the compressed engine, the flat 0.95, the committee).

### G2. A market-implied preseason prior
1. **What.** Replace `cfb.ratings`' prior (last season's margin regressed
   0.58) with a rating fitted to the market's OPENING spreads of the first
   weeks (a Massey on lines instead of margins), fading into the results'
   rating by the shipped K.
2. **Evidence.** [measured here] the raw ratings trail the market by 4.4
   points of accuracy and 0.087 nats in weeks 1-3 and are EVEN from week 9
   (`game_xref.md` 3.1): the deficit is the prior, i.e. the offseason the
   market knows (portal, coaching, returning production). [outside]
   market-implied power ratings are the standard way to carry that.
3. **Missing today.** cfb.py:168-184 (`ratings`: last season's differential
   only); cfb.py:187-222 (`inseason_ratings` has no market term).
4. **Data.** ESPN core odds `open` lines (stored for 2024-26, verified) for
   the backtest; the week's current lines for production. FBS only (FCS has
   no lines); FCS keeps the current prior.
5. **Test.** Fit 2024 (prior weight K_m from {1, 2, 4} games-equivalent of
   the line-implied rating), test 2025 and 2026 weeks 1-6 priced games by
   log loss of Phi(model margin / 18) vs the shipped prior; pass > 2 SE
   clustered by week. Power: ~1,000 early-season test games, SE ~0.004
   nats; the gap to close is 0.087.
6. **Gain / risk.** Large in September (the owner's quick-combo locks are
   read off the raw number), nothing by November. Risk: the raw number
   stops being "our own" early in the season; the record keeps grading it.
7. **Priority 8.**

### G3. Registered already: the unpriced total (8.4 item 5), the anchored sigma and engine dispersion (item 6).

## 4. NBA

### B1. Last season as the prior for the production ratings (judge on 2026-27)
1. **What.** `basket.ratings` regresses every team toward the LEAGUE average
   by g/(g+12) (basket.py:125-200); on opening night every team is average.
   The candidate pulls toward last season's rating instead (pro_prior's
   rho 0.8, K 12).
2. **Evidence.** [repo] `pro_prior.md`: chosen on 2024-25, 2025-26 gain
   -0.00657 +/- 0.00334 (1.97 SE), the gain all in games where a side had
   played under 15 (0.59075 vs 0.62140 on 233) -- not shipped by the letter,
   "the first change to judge on 2026-27". The NEW reason: [live] 2026-27's
   first 106 games read 0.2504 vs the market's 0.2200, the widest gap of any
   game bucket, with the shadow deep engine (committed priors) at 0.2316.
3. **Missing today.** basket.py:181-190 (the regression target is `lg_ppp`,
   never a team prior); no stored last-season rating.
4. **Data.** ESPN standings/team stats for the prior season (free, already
   read).
5. **Test.** The one already registered in `pro_prior.md`, read on 2026-27:
   `research/pro_prior_validation.py` replayed through 31 December (~450
   games) at the frozen K 12 / rho 0.8 vs production, > 2 SE clustered by
   date. Power: the 2025-26 effect was 0.0066 on 1,202 games (SE 0.0033);
   over the first 450 games, where the effect concentrates, the SE is
   ~0.005 against an expected gain of 0.02-0.03.
6. **Gain / risk.** Medium, front-loaded in October-December; near-zero risk
   (two seasons of out-of-sample evidence already).
7. **Priority 4.**

### B2. Who is playing: availability in the game engine
1. **What.** Remove listed-out players from the team's production and move
   the team's efficiency by the share they carry; the game engine ignores
   injuries entirely.
2. **Evidence.** [outside] a star's absence is the largest single pre-game
   information in the NBA (lines move several points on an MVP-level
   scratch; load management makes it a nightly event); [repo] the matchups
   engine already reads ESPN's injury report (nba_matchup.py:741-766) and
   found teammates-out a real effect for every market, with the caveat
   that its research gain was an upper bound (box absences, not the
   pre-game report).
3. **Missing today.** basket.py:432-470 (`simulate_game` takes ratings and
   the season-average player list; nothing about tonight's roster);
   `pro_data.availability` (pro_data.py:118-144) reads ESPN roster buckets
   for the SEASON sim only.
4. **Data.** ESPN's league-wide injuries feed (verified today: 27 teams, 86
   players with status and timestamp), free; for the backtest, the deep
   store's player-games give who played (an upper bound, as the matchups
   research warned) and the league's official injury-report archive (PDFs,
   `ak-static.cdn.nba.com/referee/injury/Injury-Report_<date>_<time>.pdf`,
   public since 2019-20) gives the real pre-game listing -- unreachable from
   this sandbox, to be checked from the PC.
5. **Test.** Effect = each listed-out player's share of team points x a
   coefficient fitted on 2024-25 (replay with the pbp store's absences),
   tested on 2025-26 and on 2026-27 live from the ESPN feed; pass > 2 SE
   clustered by date, with the box-absence result reported as the upper
   bound and the live result as the decision.
6. **Gain / risk.** Large against the outcome (Kalshi already prices
   absences, so smaller against the price); risk is the report's timing
   (5 pm ET the day before, updates to tip) and "questionable" handling.
7. **Priority 2.**

### B3. Rest and schedule
1. **What.** Back-to-back, third game in four nights, road trip length,
   altitude (Denver, Utah) as pre-game adjustments.
2. **Evidence.** [outside] second night of a back-to-back is worth about a
   point to a point and a half of margin in public analyses; the schedule
   is known months ahead.
3. **Missing today.** basket.py, hockey.py: no schedule term (grep: none).
4. **Data.** ESPN schedules / the pbp stores' dates, free.
5. **Test.** Fit on 2023-25 stores, test 2025-26 by log loss; pass > 2 SE.
6. **Gain / risk.** Small (0.001-0.003 nats), cheap, no risk.
7. **Priority 10 (fold into B2's test).**

### B4. The deep engine's weight, re-judged on the live record
1. **What.** `basket._DEEP_WEIGHT` is 0 after the registered test failed
   by +0.00065 +/- 0.00102 -- a null, not a loss. The new reason is [live]:
   78 games in, the shadow deep engine reads 0.2316 against production's
   0.2504. CLAUDE.md already says "2026-27 can judge it again".
2. **Test.** Make the rule explicit now: on the shadow bucket's rows after
   400 graded games (about New Year), deep vs production log loss clustered
   by date; a weight from {0.3, 0.5, 0.7, 1.0} chosen on the first 400 and
   held through the season's end as the decision. If B1 ships first, the
   comparison is against the prior-bearing production.
3. **Priority 6 (no code until the read).**

### B5. The matchup props engine
Minutes are modelled (nba_matchup.py:17-51) and ESPN "Out" is read; the
record and the Platt tuning (mx_record) now accumulate on their own. The
open structural gap is "Questionable/Doubtful" handling and the
teammates-out upper bound; nothing to add before the record has ten dates.

## 5. NHL

### H1. The starting goalie
1. **What.** Rate each side's goals against through the goalie who starts
   (his save percentage, shrunk), not the team's season goals-against.
2. **Evidence.** [outside] the starter-versus-backup gap is the largest
   single-player effect in hockey: ~0.005-0.015 of save percentage over
   ~30 shots is 0.15-0.45 goals, three to eight points of win probability,
   and books move on the confirmation. [live] production is 0.014 Brier
   behind the market (0.2406 vs 0.2267) with no goalie term at all.
3. **Missing today.** hockey.py:91-142 (`ratings`: team GF/GA only);
   hockey.py:382-500 (`simulate_game`: team rates, skaters' shares); the
   deep engine's goaltending is a TEAM unit (nhl_deep_sim.py:40, 617).
4. **Data.** The league's own API (verified today): the pre-game gamecenter
   landing carries `matchup.goalieComparison` (each side's goalies with
   season GAA/sv%), and every historical boxscore carries each goalie's
   `starter` flag and TOI -- so a backtest knows who started. The
   CONFIRMED starter pre-game is not in the official API (it is public on
   DailyFaceoff hours before; its terms would need reading before any
   automated use); without it the model uses the likelier starter (rest
   pattern: the backup on the second night of a back-to-back) and the
   expected goalie as a mixture.
5. **Test.** Fit the save% shrinkage K on 2024-25 (replay with the boxscore
   starters, the optimistic case), test 2025-26 the same way and 2026-27
   live with the mixture; log loss vs production, > 2 SE clustered by
   date. Power: 1,312 games a season, SE ~0.003; expected 0.005-0.01.
6. **Gain / risk.** Medium-large; the main risk is the starter being wrong
   (the mixture bounds it).
7. **Priority 3.**

### H2. The deep weight re-judged live
The 0.5 blend was confirmed out of sample; [live] the shadow deep engine
(0.2299) sits closer to the market than production (0.2406), so the blend
is pulled down by production. Same rule as B4, read at 400 graded games.
**Priority 6.**

### H3. Rest and the backup: fold into H1's test (the back-to-back is also
the backup goalie's night).

## 6. Soccer

### S1. An external rating as the prior (Club Elo)
1. **What.** Shrink each club's attack/defence toward a rating derived from
   Club Elo instead of its league mean.
2. **Evidence.** [outside] clubelo.com's Elo has a published, good forecast
   record across European leagues and offers dated historical ratings
   (`api.clubelo.com/YYYY-MM-DD`), so a pre-game replay is honest.
   [live] the model loses to the close by 0.006 Brier.
3. **Missing today.** soccer.py:396-500 (`fit`: priors are the league mean,
   KAPPA 3). Coverage: European tiers 1-2 and UEFA competitions only --
   not the South American, Asian or North American leagues Kalshi also
   lists.
4. **Data.** Free CSV API; could NOT be reached from this sandbox today
   (connection reset), so availability is unverified.
5. **Test.** Chosen on 2024, tested on 2025 and 2026 (the soccer harness's
   seasons) by log loss vs the shipped fit, covered leagues only; > 2 SE.
6. **Gain / risk.** Small-medium on covered leagues. **Priority 11.**

### S2. Not proposed: xG (Understat/FBref/Sofascore terms forbid automated
use at our scale, as FotMob's do), lineups (no free pre-game source),
form and head-to-head (failed their tests). The one actionable live fact
is timing: the model has +3.3c of closing-line value and the close moves
toward it 60.6% of the time, so a soccer position is worth more at first
listing than at kickoff -- a note for the owner, not a model change.

## 7. Tennis

The model beats the market on 32,858 graded markets with +5.4c of CLV.
Nothing structural is missing that I can name with evidence; the useful
update is a record SPLIT by series (ATP / WTA / ITF men / ITF women) so the
blend and the calibration (t 0.67) are earned where the edge actually is
rather than pooled. **Priority 12 (plumbing).**

## 8. UFC

U1. The rows carry no market (C2). U2. The model is far behind the market
([repo] model_trust: 0.6855 vs 0.6145 log loss on 59 fights, trust 0.1); a
fighter Elo over full fight histories harvested from Kalshi's settled
markets (the tennis Elo's method, tennis_elo.py) would be the first model
with a chance; needs U1 to be judged. **Priority 13.**

## 9. Golf

L1. No market and no close in the log (C2; golf.py:452-457). L2. The skill
input is each player's SEASON SCORING AVERAGE (golf.py:108-145), unadjusted
for field strength or course: [outside] strokes gained relative to the
field, time-decayed, is the industry's skill measure (DataGolf's core), and
it can be built free from ESPN's past leaderboards (every event's rounds,
`golf.leaderboard`'s own source) as score minus the field's mean that
round. Test on 2024 -> 2025 events (make-cut and head-to-head log loss vs
the scoring-average model); DataGolf's API (paid subscription, roughly $30
a month) is the alternative if the home-built rating disappoints.
**Priority 10 (after L1).**

## 10. Racing

OpenF1 practice gaps and Jolpica results are already in (racing_sim.py:1068);
the gap is the record: no predlog bucket exists for F1/NASCAR futures or
race-winner markets, so nothing can be judged. **Priority 13 (plumbing).**

## 11. MLB, for spring

Every prop bucket but extras/sb/rfi loses to the price, and `mlb_ks` /
`mlb_total` earn blend weight 0.0 live. The K model is the widest gap
(-0.032 log loss): the engine's strikeouts come from a pitch-count-aware
staff sim on season K rates (mlb_sim.py:17-19), with Statcast used for
hitters only (savant.py). A spring rebuild on pitch-level data (whiff and
called-strike rates by pitch type and handedness, the opposing lineup's
K%, the umpire's zone, all free from Baseball Savant / MLB StatsAPI),
fit 2024-25 and tested on 2026's logged rungs (already graded), is the
queue item. **Priority 14 (spring).**

## 12. The ranked list

| # | candidate | expected gain (basis) | risk | effort |
|---|---|---|---|---|
| 1 | C1 book line as anchor/benchmark for every game market | NBA/NHL ~0.03 Brier vs outcome [live gap]; NFL/college the close's margin over us [measured here] | headline becomes the book's where it wins; feed can change | medium |
| 2 | B2 NBA availability in the game engine | large vs outcome [outside]; smaller vs price | report timing, "questionable" | medium |
| 3 | H1 NHL starting goalie | 0.005-0.01 nats [outside] | wrong starter (mixture bounds it) | medium |
| 4 | B1 NBA last-season prior, judged on 2026-27 | 0.02-0.03 nats in Oct-Dec [repo pro_prior] | ~none | small (rule exists) |
| 5 | C3 season sims off the compressed engines (+ CFP committee ranking) | futures bias removed [measured here] | low | small-medium |
| 6 | B4/H2 deep weights re-judged on the live shadow | depends on the read [live] | none until read | none |
| 7 | N1 second projection source (ESPN fantasy) | a few % of prop/DFS MAE [outside] | low | small |
| 8 | G2 market-implied preseason prior, college | up to 0.09 nats in weeks 1-3 [measured here] | raw number less "ours" early | small |
| 9 | N2 rushing prop shape by role | combo legs' fake low-rung edge [repo drift] | low | small |
| 10 | B3/H3 rest and schedule; L2 golf skill from scores | small / medium for golf | none | small |
| 11 | S1 Club Elo prior (Europe) | small-medium on covered leagues | feed unverified | small |
| 12 | C2 record plumbing: golf, UFC, racing, tennis split | enables everything else | none | small, first |
| 13 | U2 UFC fighter Elo | unknown; market far ahead | low | medium |
| 14 | M1 MLB strikeout model on pitch-level data | the widest MLB gap | low | large (spring) |

Not re-proposed: weather stage 1, NFL defence features, the rushing matchup
groups, soccer form and head-to-head, the NFL anchor (registered), the NBA
deep weight at 0.3 as tested (B4 is a new rule on new data), any hand-set
margin expansion or total shrink.

## 13. The agreed list (reconciled with the orchestrator's survey, 2026-10-29 16:20 UTC)

The two surveys were written independently and agree on the substance; the
orchestrator's survey added two things mine had not weighed properly -- the
NBA/NHL boards never read the trust weight `model_trust` already measured for
them, and the tennis record's logged figure is a market blend that admits
in-play first sightings -- and withdrew its CFBD college prior for my
market-implied one. What follows is the settled list and, for every item that
needs one, the rule registered BEFORE its data is read.

### 13.1 Ships in the next push (no new parameter fitted)

- **C2 plumbing.** UFC rows get the market (`ufc_prices.attach` never sets
  `mkt_win`, so predlog.py:640-650 logs NULL); golf rows get the market and
  the close (golf.py:452-457 logs `(ticker, p, None)`); the tennis record is
  split by series (ATP / WTA / Challenger / ITF men / ITF women); racing gets
  a bucket.
- **Pre-game only, tennis and UFC.** `_harvest_tennis` (predlog.py:617) logs
  every row the board shows, and a match's first sighting can be in play
  (`in_play` is on the row, tennis_prices.py:710); the logged "model" figure
  `fair_win_raw` is the confidence blend `w x model + (1 - w) x market`
  (tennis_prices.py:617-648), so an in-play sighting logs a live price as a
  pre-game number. Only `not in_play` rows are logged from now on, and the
  PURE model number (`model_win`) goes to a shadow bucket `tennis_model`
  beside the blend so the record can separate the model's skill from the
  market's. The same filter for UFC on the market's `close_time` (Kalshi
  closes a bout at its start; a row seen after it is not pre-fight).
  **Caveat on section 0:** until this lands, the tennis row (0.2177 vs
  0.2309) is a blend that contains Kalshi's own price and some in-play
  sightings; the pure model's record starts with the shadow bucket.
- **NBA / NHL headline on the measured weight.** `basket._build_board` and
  `hockey._build_board` set the headline and the edges from the calibrated
  model alone; `model_trust` has already fitted nba 0.0 and nhl 0.0
  (team_backtest, point in time, n 304 / 310; effective 0.066 / 0.065 after
  the shrink toward the 0.20 default), and college (`cfb_board._model_weight`)
  and UFC (`ufc_prices._model_weight_cap`) already apply theirs. The headline
  becomes `w x model + (1 - w) x Kalshi's de-vigged price` with
  `w = model_trust.weight(league)`; the model's own number stays on the card
  and in predlog (unchanged logging, so the record keeps grading the model);
  the combo maker's NBA/NHL game legs read the same weight. No fresh
  walk-forward is needed: the weight was fitted on the season before the one
  it is applied to, and the live record (0.2504 vs 0.2200, 0.2406 vs 0.2267)
  is the independent check. Live rule: at 400 graded games a league, the
  anchored headline's log loss against the raw model's is reported; the
  weight itself is only ever moved by `model_trust`'s own refresh.
- **C1 shadow book lines.** Shadow buckets `nfl_book`, `cfb_book`, `nba_book`,
  `nhl_book`: the book's de-vigged line (ESPN's core odds record, DraftKings
  or ESPN BET, the `current` figure at log time, never a "Live Odds" provider,
  pre-game only) logged in the same pass and at the same moment as the model
  row and Kalshi's price for the same ticker.
- **N1 shadow projections.** From week 10's first slate build, ESPN's weekly
  projections stored beside Sleeper's for every projected player (the deep
  store, per week), nothing read from them yet.
- **The NFL deep weight is decoupled from the DFS engine.** `nfl_dfs_engine`
  blends its scoring level at `nfl_game_sim._DEEP_WEIGHT` and passed its
  pre-registered 2025 test at 0.30; it pins its own 0.30 so the headline's
  weight can be judged (13.2) without touching a tested board.

### 13.2 Registered rules (dates fixed now; nothing is read before them)

- **B1 NBA last-season prior.** The rule in `pro_prior.md` stands: frozen
  K 12 / rho 0.8, `research/pro_prior_validation.py` replayed on 2026-27
  games through 2026-12-31, read on or after 2027-01-02; pass if it beats
  the raw production rating by > 2 SE clustered by date (raw model against
  raw model, not the anchored headline). Its benefit is largely 2027-28's
  opening weeks; 13.1's anchor carries this season.
- **B4 / H2 deep weights.** At 400 graded shadow games a league (NBA about
  the turn of the year, NHL mid-December), deep vs production on the SAME
  tickers, log loss clustered by date. NBA: the weight moves from 0 to the
  best of {0.3, 0.5, 0.7, 1.0} on those games only if the deep engine beats
  production by > 2 SE, and the chosen weight holds to the season's end as
  the decision; otherwise 0 stays. NHL: 0.5 stays unless the same read moves
  it by the same rule.
- **NFL deep weight 0.30.** Read at 200 graded `nfl_deep` shadow games
  (about week 13): the 0.30 blend against the drive sim alone on the same
  tickers, log loss clustered by week. ASYMMETRIC by design, because the
  weight was never earned (`nfl_deep_validation.md`: -0.0015 +/- 0.0013):
  if the blend does not beat the drive sim (point estimate), the weight
  goes to 0; if the deep engine beats the drive sim by > 2 SE it stays.
  Power: SE ~0.008 nats on 200 games, so a null read is the expected one,
  and the rule sends a null to 0. The DFS engine keeps its own 0.30 (13.1).
- **C1 the anchor level.** Read at 10 dates AND 150 games a league (NBA/NHL
  about mid-November, NFL about week 13, college week 13): the book's line
  against Kalshi's price on the same tickers, log loss clustered by date.
  Where the book wins by > 2 SE it replaces Kalshi as the LEVEL the league
  anchors to, at that league's existing weight (NBA/NHL the measured
  `model_trust` weight; college 25%; NFL only through 8.3's registered test,
  whose candidate the book line then stands in for at build time). Where it
  does not, Kalshi stays the level.
- **N1 the second projection source.** Read after week 18 + 48 h: for every
  projected player-week from week 10, 0.5 Sleeper + 0.5 ESPN against Sleeper
  alone on Sleeper's box, MAE per stat (passing, rushing, receiving yards,
  receptions, touchdowns) and the rung log's prop log loss, clustered by
  week; pass > 2 SE on the yardage MAEs and no loss on the rung log. Ships
  for the player means and the team level in 2027; the preseason path is
  untouched.
- **N2 rushing shape by role.** The role-conditional SDs are fitted on 2025
  boxes and written into the report before week 10's rungs are read; tested
  on the rung log's rushing rungs, weeks 10-16, log loss vs the shipped one-SD
  shape, clustered by game; pass > 2 SE.
- **G2 market-implied college prior.** Fit on 2024 (the line-implied rating's
  prior weight K_m from {1, 2, 4}), tested on 2025 AND 2026 weeks 1-6 priced
  games by log loss of Phi(model margin / 18) against the shipped prior,
  clustered by week; pass > 2 SE in each test season. A replay test on
  stored opening lines (ESPN core odds `open`), FBS only; ships for the 2027
  preseason.
- **C3 the season simulators.** Replay 2024 and 2025 week by week: the
  college and NFL season sims' projected win totals, division/playoff and
  CFP/title probabilities from the compressed engine against the analytic
  resolution (Phi(margin / 18) college, Phi(margin / 13.2) NFL), scored on
  real win totals (MAE) and playoff outcomes (log loss), clustered by team;
  the CFP field from the committee's own latest ranking (ESPN's rankings
  feed) from the week it exists, tested on 2024-25 field membership. Pass:
  the analytic resolution wins both seasons by > 2 SE. Target: before the
  2026 CFP selection (2026-12-06), since the race is now.
- **Tennis blend and calibration per series.** After 13.1's hygiene has
  logged a month of pre-game rows: `_BLEND_K` per series chosen on rows
  first seen before 2026-12-01 by log loss, tested on December's rows,
  > 2 SE clustered by day; the calibration per series the same way. Nothing
  moves before the December read.

### 13.3 Next sessions, then spring

- **B2 NBA availability** and **H1 NHL starting goalie** (with B3/H3 rest
  folded in): research harnesses first -- the pbp store's absences (an upper
  bound) and the boxscore `starter` flags (the optimistic case) -- with their
  rules written at the top of each harness before it runs, as section 4 and
  5 describe; the live ESPN feed and the gamecenter landing feed production.
  The NBA injury-report archive is checked from the PC.
- **C3 and G2 replays** as registered above.
- **S1, U2, L2** low; **M1** spring (fit 2024-25 on pitch-level Statcast,
  test on 2026's graded strikeout rungs).

Not re-proposed, unchanged: weather stage 1, NFL defence features, rushing
matchup groups, soccer form/head-to-head, the NFL anchor (registered in
`game_xref.md` 8.3), the NBA deep weight as tested (B4 is a new rule on new
data), hand-set margin expansions and total shrinks.

### 13.4 What 13.1 became in code (orchestrator, 2026-10-29 evening)

Every item below is guarded in `tests/guards_accuracy.py` (section AG of the
suite), and each guard was checked against a mutant that reintroduces the
bug it is there for (37 of 37 caught, the round-2 fixes' mutants included). Where the build departs from the text
above, it says so and why. The build review (Fable, 13.5) agreed every
departure; its three MINOR findings are fixed below.

- **C1 book lines.** `predlog.book_probs(comp)` reads DraftKings' (or ESPN
  BET's) moneyline off the ESPN scoreboard competition each schedule parser
  already holds (`basket.schedule`, `hockey.schedule`,
  `nfl_live._parse_events` -> `nfl_data.week_games`, `cfb_board._week_games`):
  no extra ESPN call. On an upcoming game ESPN names the latest pre-game line
  `close` (checked 2026-10-29: it moves; `open` is the opener), so `close` IS
  "the current figure at log time". Refused: any provider named "live", a
  game ESPN does not call "pre", "OFF", a pair whose implied chances sum
  under 1 or over 1.25. `predlog.book_row` adds the second gate: the start
  must be known and ahead, because the schedules are cached (5 min NBA/NHL,
  30 college, 60 NFL) and a cached "pre" can be a started game. Each board
  logs the book's row in the SAME pass as the model's row for the same
  ticker, with the same Kalshi de-vigged price beside it, to `nba_book`,
  `nhl_book`, `nfl_book` (regular season), `cfb_book`, and carries it
  (`CARRY_MODELS`, shadows). The cache makes the book up to an hour older
  than Kalshi's price beside it on the NFL board: that handicaps the book,
  never Kalshi, so a book that still wins the 13.2 read has won it fairly.
  Coverage on 2026-10-29: NBA 3/3, NHL 11/11, NFL 13/14 (ATL @ TB "OFF"),
  college every FBS game (FCS games carry no line). The 13.2 read compares
  the book's figure with the Kalshi price in the SAME shadow row (`shadow.mkt`,
  logged in the same pass), never the model row's price, which predlog's
  generic `vs_market` falls back to when a shadow row has none.
- **NBA/NHL anchor.** `model_trust.anchor` on the moneyline as agreed;
  ADDED in the build: the spread and total rungs read the same weight
  against their OWN de-vigged price (`model_trust.rung`: yes / (yes + no)
  off the rung's two asks, the convention of the moneyline's de-vig; the
  YES ask alone where the book is one-sided), because anchoring a rung to
  its ask flattered the edge by about half the spread. The best-bets list
  and the cross-sport combo (`bestbets._board_rows`, `combine._board_legs`)
  read the rung's headline figure, as their moneylines already read the
  headline -- this is what "the combo maker's NBA/NHL game legs read the
  same weight" amounts to in this codebase (no NBA/NHL maker prices a game
  leg off the market otherwise; the card's same-game parlay is the
  simulation's own). Logged predictions are the raw model, unchanged. On
  the NHL the weight measured on the production model is applied to the
  0.5 production + 0.5 deep blend: conservative, since the blend validated
  better than production.
- **C2 plumbing.** UFC: `mkt_win` set on every fighter; the logged figure
  is `ufc_prices`' blend toward the market (at the measured 0.05 it is
  nearly Kalshi's own), so the model's raw `win_pct` goes beside it to a
  shadow `ufc_model` -- ADDED, the tennis treatment. The pre-fight cutoff is
  the ticker's day (`predlog._event_ts`, midnight ET of the card), NOT
  `close_time`: Kalshi's UFC close_time is an administrative backstop weeks
  after the card (predlog's own measurement), so the text's "close_time"
  would have admitted in-fight sightings. Found in passing and fixed:
  `ufc_prices._model_weight_cap`'s unmeasured fallback unpacked
  `calibrate._params`' four values into three names, raised, and read 0
  graded fights every time. Golf: make-cut rows carry the YES mid of a
  two-sided book (none for a one-sided one) and Kalshi's close; ADDED, the
  event start (ESPN's event date, midnight ET of round one's day) and the
  carry -- the PC builds golf and its predlog is never graded, so without
  it the fix would reach the record only on the server's fallback builds.
  Tennis: in-play matches skipped, the pure model to `tennis_model`, and the
  record split by series at read time (`predlog.by_series`, SQL sums; on
  `/api/sharp` for the two tennis buckets).
- **Racing: deferred to the 2027 openers (agreed in the build review).** The race boards keep
  only the model's top 14 (F1) / 16 (NASCAR) drivers per race, prices match
  by name substring with a last-name fallback, and only asks are read; a
  bucket built on that would grade a model-selected subset and could grade
  the wrong driver's ticker. The honest bucket needs the full field, exact
  tickers, a field de-vig and the race start -- and 2026 has five F1 races
  and two NASCAR Cup races left (the schedules the boards read, 2026-10-29),
  nothing judgeable before 2027. Proposed: build
  it before the 2027 openers (NASCAR in February, F1 in March).
- **N1.** `nfl_props_log` keeps ESPN's projection beside Sleeper's
  (`store["espn"]`, same name key, same freeze), read in pages of 50 sorted
  by the week's projection and stopped under 0.5 points (week 9: 305
  players, 4.2 s, ~4 MB over seven pages, 10.5 MB traced peak), at most
  every 3 h (`ESPN_EVERY_S`). The stat ids were checked by rebuilding ESPN's
  own PPR total from them on a QB, RB, WR and TE (to 0.01), and a guard pins
  that check on those four real projections. It starts now (week 8), not at
  week 10, so the plumbing is proven before the window opens; the registered
  read takes weeks 10-18 only.
- **DFS deep weight.** `nfl_dfs_engine.DEEP_WEIGHT = 0.30`, read by
  `blend_targets`, `week_context` and `sim_stamp`.

### 13.5 The build review (Fable, 2026-10-29 ~19:00 UTC) and what changed

Verdict **AGREE WITH CHANGES**: no MAJOR, three MINOR, five NITs; every
departure in 13.4 agreed, (a) to (k).

- **MINOR 1, fixed.** The rungs took the trust weight only when the
  moneyline could be anchored, so a game whose moneyline had one ask quoted
  (common before noon) priced every two-sided rung at the raw model's full
  edge -- the flattery the anchor removes -- and best bets and the combo
  ranked those rungs first. The rungs now read `model_trust.weight(league)`
  whatever the moneyline's book; a one-sided moneyline leaves only the
  headline at the model's own figure.
- **MINOR 2, fixed.** Tennis's `in_play` reads False for a match without a
  readable start, so such a match could still log a live price. Skipped now,
  the repo's rule everywhere else (a start that cannot be read proves nothing
  pre-game). Checked on 2026-10-29: all 144 of the board's matches carry a
  start on both sides (`occurrence_datetime` on the raw `/markets` rows;
  `kalshi.markets_for_series` does not return it), so the gate costs nothing
  today. For the 13.2 December tennis read: only rows logged after this
  deploy -- the older `tennis` rows are the blend with in-play sightings in
  them, which cannot be separated after the fact.
- **MINOR 3, fixed.** `model_trust.weight` read only the local deep store,
  and the PC fits its own; a PC whose store lacked a sport anchored at the
  0.20 default while the server's fallback build read 0.066. The shared
  calibration file (`calibrate.compute_params`, fetched by the PC every
  cycle) now carries the measured weights as plain numbers, `model_trust`
  falls back to it before the default (`_record`), and an anchor served the
  default is ledgered (`TRUST-default`).
- **Found while fixing MINOR 1:** a book row whose Kalshi moneyline was
  one-sided would have been logged with no price beside it, and
  `vs_market`'s fallback would then have compared it with the model row's
  price from another moment -- point (j). `predlog.book_row` refuses such a
  row, so every book row carries its own same-pass Kalshi price and (j)
  holds mechanically.
- **NITs.** (1) The `mkt` column's two conventions (the two-ask de-vig for
  two-sided markets, the YES mid for golf's make-cut rows) are now said
  beside `vs_market`. (2) The book's provider is not stored: the parser
  takes the first DraftKings or ESPN BET row (every row on 2026-10-29 was
  DraftKings, provider 100); the 13.2 read reports the providers ESPN listed
  over its window. (3) N1's registered read reports the ESPN-to-Sleeper join
  rate (normalized name and team) and scores joined players only. (4)
  `rung`'s one-sided fallback has no overround gate; harmless at w 0.066,
  left. (5) UFC rows now pass their cutoff as the fifth element, so the
  closing snapshot stops there whatever the ticker's format becomes. Every
  UFC row is logged before midnight ET of the ticker's day: hours before the
  first bout for a card in the Americas or Europe. A card held in
  Asia-Pacific runs on the evening BEFORE its local date in ET, so if Kalshi
  dates such a ticker by the local date its cutoff falls after the card --
  `_event_ts`'s convention before this batch too (it already stopped the
  closing snapshots). **Rule (agreed in round 2):** when the next
  Asia-Pacific card lists, compare `_event_ts(ticker)` with ESPN's card
  start in ET; if the ticker's midnight-ET cutoff falls after the first
  bout, void that card's rows in the record (they would hold in-fight
  prices) and change the cutoff to the earlier of the two sources before the
  following such card. If Kalshi dates by the ET date, nothing is needed.

**Round 2 (Fable, ~19:40 UTC): AGREE.** All three MINORs and the book-row
refusal verified in the tree and by the guard file (44/44); the
`TRUST-default` note cannot flood the ledger (`errlog.note` throttles and
rolls up per code). One follow-up, not for this push: `model_trust._record`
reads the local store before the shared file, which inverts the "one
calibration, every process" precedence -- a PC whose own backtest wrote an
odd record (a partial ESPN fetch) would override the server's. Both
machines fit the same deterministic backtest, so the numbers should agree;
the change to make is "the file first while it is fresh (`file_params`'
48 h rule), the local store otherwise", with its own guard.

