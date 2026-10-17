# Soccer accuracy: shots as a rating input, and the model against the market

Run 2026-10-17 at the owner's request ("Is there any way to make the model
more accurate?"). Two held-out tests, each with its rule written into the
harness's docstring before any number was computed. Code:
`research/soccer_shots_validation.py`, `research/soccer_market_validation.py`;
results in `research/data/`.

## 1. Shots on target as a rating input: shipped at 0.25

Goals are a noisy measure of how good a side is. A team that outshoots its
opponent 20-5 and loses 1-0 tells a goals-only fit it was the worse team.
ESPN's yearly scoreboards, already the model's source, carry each side's
shots and shots on target for 58,603 of the 78,505 matches of 2023-2026
(75%). The fit now reads each side's **pseudo-goals**, `(1 - w) goals + w x`.
Here `x = 0.3773 on-target - 0.02771 other shots`, fitted on 2023 alone and
scaled to 2023's goals. A match without shots enters at its goals; that is
the matches with no statistics, plus extra-time matches, whose shots cover
120 minutes.

Selection on 2024, then a frozen test on 2025 and a confirmation on 2026,
weekly walk-forward in production's own window. The table gives three-way
log loss per match:

| w | 2024 (select) | 2025 (test) | 2026 (confirm) |
|---|---|---|---|
| 0 (goals only) | 1.00509 | 1.00901 | 1.01467 |
| **0.25** | **1.00420** | **1.00732** | **1.01312** |
| 0.5 | 1.00498 | 1.00806 | 1.01457 |
| 0.75 | 1.00749 | 1.01191 | 1.02128 |
| 1 (shots only) | 1.01198 | 1.02147 | 1.04980 |

Against goals only, w = 0.25 was:
- **2025:** -0.00169 +/- 0.00033 per match (about 5 SE), so the test passed.
- **2026:** -0.00156 +/- 0.00037, the same direction, so it was confirmed.
- **Both-teams-to-score figure** (its recalibration was fitted on the
  goals-only model): unchanged on 2025, -0.00004 +/- 0.00016, so no veto.

On matches where both sides had shots in the window, the gain is larger:
-0.00207 on 2025 and -0.00193 on 2026. Shots alone (w = 1) are clearly
worse. Goals carry finishing quality that shot counts miss, and the blend
keeps both.

A detail of the conversion: once shots on target are counted, more
off-target shots mean slightly fewer goals. Teams chasing a game shoot from
distance.

Production (`soccer.SHOTS_W`, `soccer.SHOTS_COEF`):
- `parse_scoreboard` keeps the shots, `history` stores them with each league
  year in the deep store, and `build_artifact` fits on `pseudo_rows`.
- The form panel, the head-to-head and the BTTS base rate stay on real
  goals.
- A stored year without shots is refetched once.
- The artifact refits when `SHOTS_W` changes.
- `artifacts.SCHEMA` 5.
- A full refit with shots, measured in a fresh process: 76 s, mostly the ESPN
  fetch. The fit itself took 2.9 s, peak RSS was 129 MB (about 120 before), and
  30,861 of 37,562 rows carry shots.

## 2. The model against the closing market: it lost, everywhere

No part of the soccer stack had been tested against a market. Kalshi keeps
no price history, so the market here is the sharpest public one: Pinnacle's
closing three-way price, from football-data.co.uk, which publishes results
and closing odds free for this kind of study. Its 2026 files mostly lack
Pinnacle's column, so 2026 uses the bookmakers' average closing price.

The join: 25,298 matches in 27 leagues, 24,841 of them joined to ESPN's
(date +-1 day, same 90-minute score, names to break ties). The model is
the shipped one, shots included.

The blend is the logarithmic pool `p ~ p_model^a * p_market^(1-a)`. 2024
chose **a = 0**, all market. Every weight on the model, down to 0.05, made
the market's price worse, in every year:

| | 2024 | 2025 | 2026 |
|---|---|---|---|
| matches | 8,939 | 9,226 | 6,668 |
| market alone | 0.99002 | 0.99542 | 1.00106 |
| model alone | 1.01368 | 1.01460 | 1.02292 |
| model minus market | +0.0237 +/- 0.0019 | **+0.0192 +/- 0.0018** | **+0.0219 +/- 0.0020** |

- **B1 (does the model add anything to the market?) fails.** The chosen
  blend is the market itself.
- **B2 (does the market add to the model?) passes**, by about 10 SE on both
  held-out years.
- **League by league**, the model was worse than the market in all 27 (Ligue
  1 +0.011 per match at best, Serie B +0.035 at worst).

What shipped (`soccer.MODEL_WEIGHT = 0`, `soccer.headline`):
- Every pre-game card's headline chance is the pool at weight 0 on the
  model, which is Kalshi's own de-vigged price.
- The NO-on-the-underdog edge reads that chance. At weight 0 it is what a
  bet at the ask costs if Kalshi's price is right, never a claimed edge.
- The model's figure stays on every card, beside the headline.
- The record keeps grading the raw model against Kalshi (bucket `soccer`).
- The quick picks' 15-point rule keeps comparing the model's own number
  with Kalshi's. Their combined chance is the headline's.
- A live match's chance is the model's pre-game figure, never a blend with
  a price that carries the match.

**What this does not settle:**
- Kalshi's pre-game book is thinner and earlier than Pinnacle at the close,
  so Kalshi alone may deserve less weight than the closing market did. If
  the live record shows the model beating Kalshi's pre-game price, that is
  the case for a weight above 0, judged on new matches.
- The both-teams-to-score figure has no market history to test against and
  is unchanged.
- Leagues outside football-data's 27 (most of Kalshi's smaller ones) were
  not tested. The weight applies to them on the evidence of the 27.

## 3. What was looked at and not done

- **College:** ESPN keeps no closing lines for past college games (scoreboard
  odds are null and summaries have no pickcenter for 2025), and the free
  alternative (collegefootballdata.com) needs an API key the owner would
  register. The college anchor weight is already fitted from the live graded
  record (`model_trust` "cfb").
- **NBA:** both models ran about 1.5x under-confident in logit on 2024-26
  held out (`nba_deep_validation.md`). The board's live calibration
  (`calibrate.apply` on the graded ledger) already stretches production's
  figure. Checking it needs the server's ledger, not something run from
  here.
- **The biggest missing input everywhere is injuries and lineups.** No free
  source with history exists to test them on. A paid soccer feed
  (API-Football) would carry them going forward, with the owner's key.
