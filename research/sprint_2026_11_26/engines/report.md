# NFL weekly board: drive model vs deep engine (backlog Phase 1.1)

Audit date 2026-11-26. Repo read-only at branch `claude/kalshi-crypto-predictor-ckutwm`; live record
from `origin/sim-history` at commit 3a86b72 (2026-11-26 08:12 UTC). Scripts and intermediate data are
beside this file: `series.py` / `series.json` (every committed version of `errors/sharp-latest.json`),
`weekly.py` (differenced record), `var.py` (drive engine dispersion), `deep.py` (deep engine dispersion).

Evidence labels: **[code]** read in the source, **[sim]** run here, **[live]** from the data branch,
**[report]** quoted from a committed research report, **[unknown]** not determinable from what I could read.

The specific Green Bay / Rams board row is NOT on the data branch (only aggregates are), so the card
itself could not be re-derived: which side was home, the two teams' Sleeper projections and their deep
profiles that week are **[unknown]**. Everything about that card below is arithmetic on the quoted
figures (64.9%, 52%, totals 43.6 / 44.4) plus the engines' measured behaviour.

---

## 1. Mechanism: why they disagree

### 1.1 The two engines read different inputs for team strength

| | drive model (`nfl_game_sim.simulate_game`) | deep engine (`nfl_deep_sim.simulate`) |
|---|---|---|
| strength source | THIS WEEK's Sleeper projection: team sums of `pass_td + rush_td`, `fgm`, `pass_int + fum_lost` (`nfl_data.py:80-135`) turned into per-drive rates (`nfl_game_sim.py:219-233`) | season play-by-play unit rates shrunk to a prior (`nfl_deep_data.py:105-131`), then the LEVEL set at full weight to Kalshi's win totals as rest-of-season pace (`_ANCHOR_PRIOR_GAMES = None`, `nfl_deep_data.py:71`; `market_margins` :227-240, `(pace - 8.5) x 2.25` points; `apply_anchor` :243-265; `nfl_season.market_pace`, `nfl_season.py:178`) |
| what the market contributes | nothing (Kalshi's lines only choose which rungs the ladders show) | the whole level: the unit rates only shape HOW the points are scored |
| opponent | residual defence exponent 0.225 on shrunk points allowed, clamped 0.88-1.14 (`nfl_game_sim.py:207-216`) | offence level / opponent's defence level on every play (`nfl_deep_sim.py:324-327`) |
| this week's roster | fully: it IS the Sleeper weekly projection | HALF of Sleeper's week / season-average ratio, offence only, clamped 0.85-1.12 (`_ROSTER_WEIGHT 0.5`, `nfl_deep_data.py:79-80`, :268-314) |
| home field | scoring rates x1.05 home, /1.05 away (`_HFA_SCORE`, `nfl_game_sim.py:45`, :224) | yardage x1.021 home, /1.021 away (`_HFA_YDS`, `nfl_deep_sim.py:68`, :398) |
| post-processing on the card | the "drive model" line is the CALIBRATED figure, `calibrate.apply("nfl", p)` (`nfl_game_sim.py:1241`, :1302, :1309) | raw (`:1311`) |

So the drive number is "Sleeper's opinion of this week's two offences", and the deep number is "Kalshi's
season-long opinion of the two teams, plus half of Sleeper's week-specific offence deviation". They are
two different forecasts of the mean margin, not two simulations of one forecast.

### 1.2 Home field is the same size in both **[sim]**

Equal teams: drive p_home 0.5455, mean margin +1.56 (40,000 games, SE 0.0025); deep p_home 0.5402,
mean margin +1.30 (12,000 games, SE 0.0045). Both are fitted to the home WIN RATE (0.542 / 0.540),
not the mean margin, by the same stated choice. Home field does not explain a 13-point gap.

### 1.3 The same total with a different win probability is a mean-margin difference, not a variance one

Both engines produce the same spread of outcomes around their mean (section 2: margin SD 13.1 vs 13.3,
total SD 13.1 vs 12.8). Reading the card through that common SD (Phi(margin / 13.2)):

| figure on the card | implied mean margin for the Rams |
|---|---|
| drive 64.9% (calibrated) | +5.1 |
| drive raw, if the calibrator was at its current t = 0.8: 62.0% | +4.1 |
| deep 52% | +0.7 |
| blended headline 0.7 x 64.9 + 0.3 x 52 = 61.0% | +3.7 |

A total fixes the SUM of the two means; it says nothing about their difference. Totals of 43.6 and 44.4
are compatible with a 24.3-19.3 split and a 22.5-21.9 split alike. The 12.9-point gap decomposes as:

- **about 10 points (3.4 points of margin): the level source.** Sleeper's weekly projection had the Rams
  ~3.4 points better against Green Bay than Kalshi's win-total pace plus the half-weight roster factor
  did. Which input was closer to the market's moneyline for that game is **[unknown]** (the row is not
  on the data branch).
- **about 3 points: the calibrator, applied to the drive figure only.** The shared calibration file on
  the data branch carries `models.nfl = [0.8, 0.5, 0.0, 258]`, a sharpening temperature
  (`calibrate.scale`: `sigmoid(logit(p) / 0.8)`); 62.0% raw becomes 64.9%. Whether t was 0.8 on the
  day of that card is **[unknown]**; it is today.

Two structural reasons the pair will often disagree, in either direction **[report]**: the drive
model's margins are compressed (actual on predicted slope 1.29 +/- 0.12, 2024-26, 651 games,
`game_xref.md` 1.2) while the anchored deep engine's are slightly over-dispersed (slope 0.88,
`nfl_deep_validation.md`, 512 games). A week-specific input (a quarterback in or out) moves the drive
model in full and the deep engine by at most half, offence only.

---

## 2. Variance: neither engine's dispersion is wrong

| | drive model | deep engine | real NFL |
|---|---|---|---|
| margin SD, equal teams | 13.05 **[sim]** (40,000 games); 12.8-13.25 across TD splits of up to 0.8 | 13.34 **[sim]** (12,000); 13.2-13.7 across level splits of up to 10 points; 13.26 in the code comment (60,000) | 13.3 conditional on the spread (both engines' comments: unconditional 14.37 / 14.43 less a ~5.4 SD spread) |
| margin SD without the form tilt | 10.69 **[sim]**; comment says 10.90 (`nfl_game_sim.py:256-258`) | 12.03 (comment, `nfl_deep_sim.py:71-78`) | - |
| fitted tilt | `_FORM_SD 0.20` (`nfl_game_sim.py:158`) | `_FORM_SD 0.080` (`nfl_deep_sim.py:79`) | - |
| total SD | 12.9-13.2 **[sim]** | 12.6-12.9 **[sim]** | 13.7 unconditional (`nfl_deep_plan.md` :104); around the closing total about 12.7 if normal (MAE 10.15 x 1.25, `game_xref.md` 1.2), my conversion, not a measured figure |
| mean margin realized per point asked | not measured here in points asked | asked gaps 3 / 6 / 10 realized 3.1 / 5.8 / 9.8 | - |

Both engines were fitted to the same target (conditional margin SD 13.3) with the same device (a
zero-sum game-day tilt) and both land on it. The deep engine's total SD is ~1 point under the real
unconditional figure and at the conditional one; the drive engine's is between. Neither is a defect
that could produce the Green Bay / Rams gap. The dispersion that IS off is across games, not within
one: the drive model's predicted margins vary too little from game to game (SD 4.5 vs the spread's
5.8) and its predicted totals too much (5.4 vs 4.2, slope 0.56) **[report]**, both inherited mostly
from Sleeper.

---

## 3. Prospective record

### 3.1 What the data branch holds

`errors/sharp-latest.json` holds AGGREGATES per bucket, one row per Kalshi ticker (`predlog.vs_market`,
`predlog.py:519-560`). A game has two tickers (home, away) and the two rows carry the identical log loss
and Brier term, so "n" there is 2 x games and the per-row means equal per-game means. Row-level data is
not committed anywhere, so accuracy and any blended log loss cannot be computed exactly here.

Headline aggregates at 2026-11-26 **[live]** (log loss / Brier, model vs the Kalshi price logged beside it):

| bucket | rows (games) | model | Kalshi | model - Kalshi (log loss) |
|---|---|---|---|---|
| nfl (raw drive) | 260 (130) | 0.6109 / 0.2116 | 0.5981 / 0.2052 | +0.0127 |
| nfl_deep (shadow) | 168 (84) | 0.6007 / 0.2067 | 0.5806 / 0.1978 | +0.0200 |
| nfl_book (shadow, DraftKings) | 114 (57) | 0.5243 / 0.1739 | 0.5162 / 0.1703 | +0.0081 |

**These three lines are on different games and must not be compared with each other.** The 92 oldest
`nfl` rows have no deep row; the Kalshi log loss on each set (0.598 / 0.581 / 0.516) shows how
different the games are. `accuracy_gaps.md` section 0 read "nfl_deep ... worse than the drive sim
live" from 54 deep rows against 146 drive rows: Kalshi's own log loss was 0.7168 on the 54 and 0.6622
on the 146, so that sentence compared an upset-heavy subset with the whole and is not supported.

### 3.2 The same-games comparison that CAN be made

The file has 72 committed versions since 2026-10-29. Since then `nfl`, `nfl_deep` and `nfl_book` have
each grown by exactly the same count at every snapshot (114 rows), and the Kalshi sums on the `nfl` and
`nfl_deep` increments agree (58.83 vs 58.83 nats), so the increments are the same tickers. Differencing
n x mean between snapshots gives the same-games record for weeks 9-12, 57 games (114 rows); rounding in
the published means costs about +/-0.0003 per row.

| snapshot window (about) | rows | drive LL | deep LL | Kalshi LL | drive - deep |
|---|---|---|---|---|---|
| week 9 | 28 | 0.5555 | 0.5213 | 0.5404 | +0.034 |
| week 10 | 30 | 0.5149 | 0.3909 | 0.3998 | +0.124 |
| week 11 | 28 | 0.6251 | 0.5887 | 0.5936 | +0.036 |
| week 12 (+2 late rows) | 28 | 0.5479 | 0.6406 | 0.5385 | -0.093 |
| **all, 57 games** | 114 | **0.5601** | **0.5329** | **0.5160** | **+0.027** |
| Brier, 57 games | | 0.1901 | 0.1775 | 0.1702 | |

DraftKings on the same 57 games: 0.5243 / 0.1739.

Paired differences, per game, SE clustered on the four weekly windows (a ratio estimator over 4
clusters; 4 clusters is very few, so read each SE as a rough scale, not a test):

- drive - deep: **+0.027 +/- 0.045** (deep better in 3 of 4 windows, worse in the latest). Not distinguishable from zero.
- drive - Kalshi: +0.044 +/- 0.025.
- deep - Kalshi: +0.017 +/- 0.028.

The windows are snapshot dates, not exact NFL weeks (a Monday game can fall into the next window).

**The 0.30 blend on those 57 games.** Its exact log loss needs rows. Log loss is convex in the
probability, so the blend of raw drive and deep is bounded: LL(blend) <= 0.7 x 0.5601 + 0.3 x 0.5329 =
0.5519, at least 0.008 nats better than the drive model alone on these games as a point estimate.
Its uncertainty is of the order 0.3 x 0.045 = 0.014, so that is also inside the noise.

**The 27 earlier deep games (logged before 2026-10-29)**: deep 0.7439 vs Kalshi 0.7168. The drive
model's log loss on those same 27 games is **[unknown]** (not separable from its 146-row aggregate).
For the deep engine to trail the drive model over all 84 graded games, the drive model would need
under 0.6865 on those 27, i.e. to have beaten Kalshi there by 0.030 nats; over its first 146 rows it
beat Kalshi's early price by 0.0116.

**Accuracy** (share of favourites that won) for drive vs deep on the same games: **[unknown]**, not
derivable from aggregates. The only accuracy on the branch is the HEADLINE pick's
(`errors/nfl-record-latest.json`, regular season): 105-59, 64.0% of 164 (binomial SE 3.7 points),
Brier 0.2233; on the 131 with a Kalshi closing price, 67.9% vs Kalshi's favourite 71.0%, log loss
0.6208 vs 0.5906. That record mixes weeks before and after the deep blend and is the calibrated, blended
headline, so it grades neither engine.

### 3.3 What the earlier studies say **[report]**

- Drive model, replayed 2024-26, 651 games (`game_xref.md` 1.1): 65.0% vs the closing favourite's
  67.6%; log loss 0.6340 vs 0.6092, +0.0247 +/- 0.0069. Gap ~3 SE in each of 2024 and 2025, reversed
  inside noise in 2026 weeks 1-7 (-0.0088 +/- 0.0119).
- Deep engine, out-of-sample walk-forward 2024-25, 512 games, points-differential ratings standing in
  for Kalshi's win totals (`nfl_deep_validation.md`): anchored at weight 1, 0.6317 / 62.3%, against the
  ratings' 0.6331 (-0.0014 +/- 0.0045); 0.30 anchored deep + 0.70 ratings vs ratings alone
  -0.0015 +/- 0.0013. The drive model was NOT in that study (Sleeper's inputs were not archived), so
  there is no historical head-to-head of the two engines; the two studies also cover different seasons.

### 3.4 Is the 0.30 blend supported?

Not established either way. The weight was a judgement when set (`nfl_game_sim.py:979-992` says so),
the walk-forward evidence is -0.0015 +/- 0.0013 against a stand-in, and the live same-games evidence
(57 games) points the deep engine's way at 0.6 of a rough SE. Nothing here contradicts 0.30; nothing
earns it.

### 3.5 The registered read (accuracy_gaps.md 13.2)

Text: read at 200 graded `nfl_deep` shadow games ("about week 13"); the 0.30 blend against the drive
sim alone on the same tickers, log loss clustered by week; if the blend does not beat the drive sim
(point estimate) the weight goes to 0; if the deep engine beats the drive sim by > 2 SE it stays.
CLAUDE.md summarizes it "asymmetric: a null sends it to 0".

Status: 168 graded, 198 logged **[live]**. At ~28 rows a week the count passes 200 when week 13's
Sunday and Monday games are graded, about 2026-11-30 to 12-01.

Three things need settling by the owner BEFORE that read. This audit has now looked at interim
aggregates, so they should be settled on the text, not on the direction seen above:

1. **Unit.** "200 games" is 200 ROWS in the file's own count, i.e. 100 games. The "about week 13"
   gloss and the n = 54 quoted beside it show rows were meant. If distinct games were meant, 200 is not
   reachable this regular season (84 now, about 15 a week).
2. **The middle case.** The two clauses do not cover "blend ahead on the point estimate, deep not ahead
   by 2 SE", which is where the interim record sits (blend at least 0.008 ahead on 57 of the 84 games;
   deep - drive 0.6 SE). The 13.2 sentence sends only a blend that fails the point estimate to 0;
   CLAUDE.md's "a null sends it to 0" reads as sending this case to 0 as well. The two readings give
   opposite decisions.
3. **Power.** The rule's "SE ~0.008 nats on 200 games" treats 200 rows as independent. Each game is two
   identical rows, and the week-clustered SE of drive - deep on 57 games measured here is ~0.045
   (so ~0.014 for the 0.30 blend against the drive model). At 100 games expect roughly 0.010-0.035,
   not 0.008. A > 2 SE result is not attainable at this sample for any plausible effect.

The read also cannot be done from the data branch: it needs the blended probability per ticker
(`shadow JOIN predictions` on the app's store).

---

## 4. Blend validity

**[code]** `_build_board`, `nfl_game_sim.py:1297-1317`:

- Headline `p_home = 0.7 x calibrate("nfl", drive raw) + 0.3 x deep p_home`; `p_away = 1 - p_home`.
  A convex combination of two probabilities is a valid probability, and the deep figure is conditional
  on a decision (`nfl_deep_sim.py:1064-1066`: ties split out), so the pair sums to one. Valid.
- The pick, its percentage and the Kalshi edge chips (`edge_home`, `:1340`) use the blend.
- **Everything else on the card is the drive model alone, uncalibrated**: `exp_home`, `exp_away`,
  `exp_total`, `mean_margin`, `spread_ladder` (`:1303-1305`), `total_ladder` (`:1320`), the player
  lines, top props and the same-game parlay (`:1375`, built from the drive sim's masks). The deep
  engine's score appears only in the small two-model line (`static/app.js:3258-3259`).

So each ladder is a valid distribution (it is one engine's simulated games), but the card as a whole
is not one distribution: the win% comes from a mixture the expected score and spread rungs do not
share. On the Green Bay / Rams card the headline (61.0%) implied about +3.7 for the Rams while the
score line and the -2.5 / -6.5 / -9.5 chips were the drive model's +4 game. The inconsistency is
bounded: the headline moves from the drive figure by 0.3 x (deep - drive), 3.9 points here, plus the
calibrator's shift. A visible contradiction (headline win% below the drive ladder's own P(win by 3+))
needs a drive-deep gap of roughly 25 points; how often that has happened is **[unknown]** (no board
rows on the data branch).

What is logged is clean: `nfl` gets the raw drive probability, `nfl_deep` the raw deep one
(`:1346-1362`); neither record grades the blend, by design.

Two side observations, not blend errors:

- The calibrator sharpening the drive figure (t = 0.8) is fitted on the live `nfl` bucket.
  `game_xref.md` 1.1 found a fitted temperature made the NEXT season worse in both folds
  (+0.0069 +/- 0.0112, +0.0070 +/- 0.0062). It is CV-gated in production; I did not audit the gate.
- The registered read compares the blend with the RAW drive probability, while the card blends the
  CALIBRATED one. They are different quantities whenever t differs from 1.

---

## 5. Recommendation

**No change to `_DEEP_WEIGHT`, the engines, or the ladders now.** Reasons:

- The disagreement on the card is two inputs, not a bug: both engines have the right dispersion and
  the same home edge (sections 1.2, 2).
- The only same-games live evidence (57 games) is inside its noise, and I have now seen it; moving the
  weight on it would be tuning on used data. The registered read is days away and owns this decision.

What I do recommend, none of it a model change:

1. **Before the week-13 games are graded, the owner fixes the three ambiguities in 3.5 in writing**
   (unit = rows as counted by `/api/sharp`; the outcome of the middle case; the SE estimator = paired
   per-GAME differences clustered by NFL week). The middle case is the owner's call. The reading
   closest to the registered words "a null sends it to 0" and to the rule's own statement that a null
   is the expected read is: 0.30 stays only if the deep engine beats the drive model by > 2 SE,
   otherwise 0. I state it only so that a choice is recorded before the data, not as a preference.
2. **Make the read reproducible from the data branch.** Add a paired export (per NFL week: games, sum
   of log loss for raw drive, raw deep, the 0.30 blend and Kalshi on tickers present in both buckets)
   to what the error-log workflow pulls. Acceptance criterion, written now: summed over weeks, the
   export reproduces `sharp-latest.json`'s `nfl_deep` model and market log loss to 4 decimals, and its
   drive / deep / Kalshi figures on the rows graded after 2026-10-29 match section 3.2
   (0.5601 / 0.5329 / 0.5160 on those 114 rows) to 0.001.
3. **Correct the record of the earlier claim**: `accuracy_gaps.md` section 0's "worse than the drive
   sim live" compared different games (3.1).
4. **If the weight survives the read**, register (do not build now) a card-coherence check: count
   boards where the headline win% falls outside the drive ladder's own bracket
   [P(home wins by 3+), 1 - P(away wins by 3+)]. Criterion written now: if any game in a full season of
   boards violates it, the ladders and expected score must move to the same mixture as the headline;
   if none does, a label on the card ("score and ladders: drive model") is enough. If the weight goes
   to 0 the question disappears.

Not recommended: fitting a new weight, a margin expansion, or a temperature on 2026's games.
`game_xref.md` section 2 tested each family and every one made the following season worse.

---

## 6. Open questions

- The Green Bay / Rams card's inputs: home side, Sleeper projections, deep profiles, roster factor, the
  calibrator's t that day, and Kalshi's moneyline for the game. With the board row the section 1.3
  decomposition can be made exact.
- The drive model's log loss on the 27 pre-10-29 deep games, hence the full 84-game head-to-head.
- Straight-up accuracy of each engine on the same games; the exact blended log loss.
- Whether the two engines' errors are correlated enough that a blend gains nothing beyond convexity.
- Which engine is right when they disagree by 10+ points. No row-level data; `game_xref.md` found the
  market right 55 of 93 times when the DRIVE sim picked the other side, which is indirect.
- Whether the earlier deep rows are comparable at all: before 2026-10-30 the figure came from 600
  simulated games (about 2 points of dice) and the engine has since changed (sack clamp, 2026-10-30).
- The week windows in 3.2 are snapshot dates; exact NFL-week clustering needs kickoff times per ticker.
- The real total SD conditional on the closing total is my conversion from an MAE, not a measurement.
