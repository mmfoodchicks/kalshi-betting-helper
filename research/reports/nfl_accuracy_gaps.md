# What the NFL combo process is still missing

*2026-10-19, after the combo maker audit (`nfl_combo_audit.md`). Everything
below was measured on the live week-7 slate or on 2026 weeks 1-6 unless it
says otherwise. Nothing here changes a board; each item names the change and
how it would be judged.*

## Ranked

### 1. The team level does not know who may not play (mid-week)

`nfl_data.week_teams` sums every Sleeper projection into the team's expected
production, and Sleeper's early-week projection assumes everyone plays. Read
on Monday for week 7:

| team | projected PPR on Out/IR/Doubtful-tagged players | who |
|---|---|---|
| BAL | 34% | Lamar Jackson (QB, 272 pass yds projected; Tyler Huntley projected 0) |
| MIN | 34% | Hockenson, Mason, Addison |
| CLE | 23% | Judkins, Sampson |
| NYJ | 22% | Breece Hall, Mitchell |
| TB | 18% | Baker Mayfield (QB) |
| PHI / KC / IND / HOU | 15-17% | Barkley / Rice / Warren / Montgomery |

**Corrected 2026-10-19 (evening).** The first version of this item read
those tags as "out for week 7". They are not: a Monday tag is last week's
game status, and ESPN gives 56 of its Out players a return date of this
Sunday. What the record does show is a projection that keeps assuming a
return. Lamar Jackson missed weeks 5 and 6, and Huntley was Sleeper's
projected starter at kickoff both weeks and the real one. Mayfield missed
weeks 5-6 with Jalon Daniels starting both. Each was back at a full
projection on Monday. Mayfield's had moved to Daniels again by Monday
evening. Whether either plays in week 7 is not known on Monday, and the
model was not "priced off a quarterback who is not playing". It was priced
as if the doubt did not exist, which the market does not do.

Sleeper does settle it by kickoff: in weeks 4 and 5 no player projected at
8+ PPR sat out. So the damage is to slips built Tuesday through Saturday,
which is when the owner builds them.

**Shipped (see the end):** a player Sleeper still counts as a real part of
the plan who carries an out-type tag, or a Questionable one after missing
his last game, is in doubt. His legs leave the pool. When he is a
quarterback, his game's moneyline, spreads and totals and his team's player
legs take Kalshi's number. Otherwise only his position group's legs on his
team do. **Judge:** the graded rung log (item 2), weeks 8-12: the flagged
legs' log loss at the market's number against the model's.

Note on settlement: Kalshi's standard player markets settle at the pre-game
fair price if the player never takes a snap, so a prop on an inactive is
closer to a refund than a loss. The maker treats every leg as win/lose; a
combo "resolves to the product of the underlying positions", so a leg settled
at 0.55 pays 0.55 of the rest. Small, but it is the direction of least harm.

### 2. Nothing but the moneyline has a graded record

`predlog` logs one moneyline pick per game. Totals, spreads and every player
prop run on hand-set trust priors (`combo_engine._MODEL_TRUST`: 0.6-0.7), and
the blend's scale (`_MARKET_K` 3.0) and edge cap (`_MAX_EDGE` 0.10) were fitted
on baseball. We cannot answer "does the model beat Kalshi on totals or
props?" with any number, and today's refit had to use Kalshi's week-7 ladder
as the only check.

What already exists: `nfl_props_log` freezes every believable Kalshi prop
rung, its de-vigged probability and Sleeper's projection five minutes before
each kickoff. It does not store the drive sim's probability for the rung.

**The change:** at the same freeze, store the sim's probability for every
rung it priced (props, the total ladder, the spread ladder), and grade them
against Sleeper's box and ESPN's final. Four weeks of that is ~2,000 rungs a
week: enough to fit a per-type blend weight the way `calibrate.blend_weight`
does for baseball, and to check the shape finding below. **Judge:** the
pre-registered rule is the one baseball uses: the weight that minimises
log loss over the graded record, shrunk toward the prior by sample size.

Also: the server's graded record is not in any snapshot. The error-log
workflow should pull `/api/nfl/record` and `/api/baseball/sliplog?sport=nfl`
into `sim-history`, so a session can read them. Today's audit could not.

### 3. The real combo quote is never recorded

Kalshi prices a combo by request-for-quote: "if no market participant
responds to the RFQ with a quote, the combo order will not fill", and quotes
"may change between viewing and execution" (Kalshi's help centre). The maker
now estimates the quote as the asks times the sim's correlation
(`stack_lift`), which is the most generous quote a maker could give. One
quote has ever been recorded (2026-09-08: asks 1,600x, quote 133x, sim fair
200x). `QUOTE_ROOM` for one-leg-a-game slips is unmeasured.

**The change:** a "quoted at" field on the slip ledger the owner fills in
from the app (payout quoted, and whether it filled). Twenty quotes fit the
gap between the estimate and the real price; until then the slip says the EV
is an upper bound (it does). **Judge:** the ratio quote / estimate, by stack
size.

### 4. The board and the combo maker disagree on the same game

The board's headline is the calibrated drive sim blended 0.30 with the deep
engine; the combo maker's moneyline leg is the raw drive sim (`_slate_sims`
calls `simulate_game` alone). The deep blend buys nothing measurable
(`nfl_deep_validation.md`: +0.0025 +/- 0.0032 against the ratings), so this
is consistency rather than accuracy, but the calibration
(`calibrate.apply("nfl")`) is the record's correction and the combo legs do
not get it. **The change:** one function that both read.

### 5. Model shape against the market (small n, watch it)

Implied-SD ratio, sim over Kalshi, from the slope of the de-vigged ladders
(players with four or more believable rungs, week 6-7):

| | n | median | range |
|---|---|---|---|
| totals | 3 | 1.04 | 1.00-1.04 |
| rushing yards | 6 | 1.11 | 1.06-1.28 |
| receiving yards | 6 | 0.94 | 0.75-1.18 |
| receptions | 4 | 0.91 | 0.86-0.99 |
| passing yards | 2 | 1.22 | 0.98-1.22 |

Totals are right. Rushing may be too wide and receiving too narrow, by about
a tenth. Item 2's record is what would settle it.

### 6. The 40-leg cap selects for disagreement

When a game offers more than 40 legs in the band, the pool keeps "the biggest
model-vs-price gap" first. That is the selection the module's own blend
comment warns about. It binds only on fully-posted games (1 of 14 in week 7;
tonight's game), and the kept legs averaged a 2.8c gap against 0.9c for the
dropped. Now that unlisted legs are out of the pool, sort by liquidity, or
raise the cap. Small.

### 7. Recent usage reaches the DFS boards, not the combos

`nfl_usage` (passed its 2025 test; targets -0.0049 +/- 0.0011 log loss per
team-game) re-splits targets and carries inside `nfl_dfs_sim.weekly_games`.
The combo maker's player shares are Sleeper's raw ones. A port into
`nfl_data.week_teams` would give the player legs the same measured gain.

### 8. A second opinion on the lines

Every number is judged against Kalshi, and Kalshi's props are thin (the
S7 and soccer lessons both: the market tends to win, but a thin market is a
weak judge). `odds_api.py` already exists for tennis: a de-vigged consensus
of 7-8 books, 500 requests a month free. One pull per NFL week (spreads,
totals, props) is a sharp closing line to grade the model and Kalshi against.
Needs the odds API's key in the server's environment (the variable `odds_api.py` reads).

## Checked and fine

- **The defence factor earns its place.** 2026 weeks 1-6 replayed: moneyline
  log loss 0.6439 with it, 0.6505 without (92 games; a mild look-ahead,
  since the rates include the games themselves).
- **Fees.** Kalshi's series metadata: `quadratic_with_maker_fees`, multiplier 1
  for GAME/TOTAL/SPREAD; plain `quadratic`, multiplier 1 for the prop series.
  Both are the 0.07·p·(1-p) curve the app charges. The combo fee rides on the
  basket once (`combo_cost`).
- **Started games, tickers, kickoffs, the week** (after today's fix).

## Not worth doing

- Fitting more constants by hand. Every number above that changed today was
  measured and shipped by a rule written first; item 2 is what makes that
  possible for everything else.
- Weather (failed its stage-1 test), head-to-head and form (soccer's lesson).

## Sources (Kalshi mechanics)

- Kalshi help: [Combos](https://help.kalshi.com/en/articles/13823820-combos)
  (RFQ, "will not fill" without a quoter, payout the product of the legs,
  $1.00 cap).
- Kalshi help: [Protected NFL Player
  Markets](https://help.kalshi.com/en/articles/17515177-protected-nfl-player-markets)
  (a standard player market on a player who never plays settles at the
  pre-game fair price).
- Kalshi help: [How are prices
  determined?](https://help.kalshi.com/en/articles/13823836-how-are-prices-determined)
- Kalshi API `/series/<ticker>`: fee_type and fee_multiplier per series
  (read 2026-10-19).

## Shipped 2026-10-19 (evening)

- **Item 6, the leg cap** (`nfl_game_sim._cap_pool`). The cap now keeps the
  most liquid legs, spread evenly over the probability band, and nothing on
  the gap. On Monday night's fully posted game the old cap's 40 were all
  props (none of the game's 21 spreads or 16 totals) at a 2.8c mean gap.
  The new 40 hold every leg type at 1.1c, against 1.2c for the whole pool.
  This item was understated above: it was measured Monday, before week 7's
  props posted, and on game day the cap binds on every game.
- **Item 1, who may not play** (`nfl_game_sim.availability`,
  `_avail_scope`), corrected as above. Read for week 7 on Monday evening: 13
  players in doubt on 11 teams. The one quarterback is Lamar Jackson, so the
  Bengals-Ravens game is the only one fully at Kalshi's number. The slip
  names the players in doubt in its own games.
- **Item 2, the model beside the market** (`nfl_props_log._model_rungs`).
  Every priced leg the slate sim prices (props, totals, spreads,
  moneylines) gets the sim's figure and Kalshi's de-vigged mid, on the first
  pass that sees it and on every pass until the game freezes, so the frozen
  "last" is the close. On Monday night's game that is 229 legs. The sim is
  read from the shared store, never run in the logger. The export to
  `sim-history` carries it, and grading is a research step once weeks 7-10
  have played.
- **A drift alarm** (`nfl_props_log.drift`), not in the list above. Each
  pass ledgers `NFL-DRIFT` when a leg type sits 4+ points one way off
  Kalshi's mid across 4+ games and 30+ legs. The totals bias the 2025
  trims carried would have rung it in week 1.

Not shipped, by the owner's choice: a link to the owner's own Kalshi account.

## Shipped 2026-10-22 (Thursday)

- **The drift alarm now acts.** Its first live week rang on one type. At
  09:45 UTC on Thursday, rushing yards sat **-4.4 points** under Kalshi's mid
  over 127 rungs in 12 games. Receiving yards read -3.5, under the 4-point
  line. Passing yards read -0.2, spreads +1.1, TDs +1.5, moneylines +1.9 and
  totals +2.3. The rushing gap is on the low rungs. The sim's rushing spread is
  fitted on every rusher, so it leaves a featured back too much mass near zero
  (item 5's 1.11 SD ratio). That is the same direction.
- Each pass now keeps its readout apart from the week's log
  (`nfl_props_log.DRIFT_KEY`). The combo maker reads it on every build
  (`nfl_game_sim.drift_types`, fresh for 3 h). Each leg type the alarm rings on
  is priced at Kalshi's number, with model weight 0. That is the same
  `market_only` path a player in doubt takes. The slip names the types and
  their gaps.
- This is not a fix for the model. It stops a known one-sided gap from
  reading as edge while the model is wrong. The fix is the rushing shape, to
  be judged on the graded rung log.

