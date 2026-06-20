# ⚡ Kalshi Betting Helper

A live web app with two tabs:

- **⚡ Crypto** — trade Kalshi's short-term crypto price markets (e.g. "Will BTC
  be above $63,000 at 3:00pm?"). An **internal odds generator** off live price
  data tells you when to **buy YES**, **buy NO**, or **hold** — including when to
  *buy the dip* — and tracks how accurate it's been.
- **⚾ Baseball** — model win probabilities for today's MLB slate, matched to
  live Kalshi prices to find edges, plus **parlay combo suggestions**.
- **🏆 Sports** — live Kalshi markets for the **World Cup, tennis (ATP), golf
  (PGA H2H), and UFC**, with **no-vig fair probabilities** (prices normalized to
  strip out the house margin) and the **vig %** so you can see which markets are
  fair and which the house is gouging. Log any outcome to your ledger.
- **📒 Ledger** — log every real bet you place and track actual profit, win
  rate, and ROI across everything.

The baseball tab also tracks the **model's real record** — picks are recorded
pre-game and graded after finals into a running accuracy / ROI / Brier line.

While it runs, the app also **records real Kalshi prices** in the background and
backtests the edge strategy against real settled outcomes (Crypto tab → *Live
strategy tracker*) — the honest test of whether catching mispricings makes money.

> ⚠️ This is a decision-support tool, not a crystal ball. The odds are model
> estimates. Markets can and will move against the model. Never bet more than
> you can afford to lose.

---

## What it does

- **Live prices** for BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, LINK, LTC, BCH via
  Coinbase's public API (no API key needed).
- **Internal odds generator.** Models price as Geometric Brownian Motion (the
  Black–Scholes assumption). It estimates *volatility* and *drift* from recent
  1-minute candles, then computes a real probability that the price ends
  **above/below** your threshold at the window close. That probability is your
  "fair" YES/NO price in cents.
- **Buy/sell signals.**
  - **BUY YES / BUY NO / HOLD** based on the model probability.
  - **Edge mode:** paste the current Kalshi YES price (in cents) and it compares
    your fair value to the market price, only signaling when there's a real edge.
  - **Dip / pump detection:** "the trend favors YES but price just dipped → YES
    is cheap right now, good dip-buy" (and the mirror for fading a pump).
  - **Take-profit hints:** since Kalshi lets you sell anytime, it suggests a
    target to lock in profit.
  - **Kelly bet-sizing:** set your **bankroll** and a Kelly fraction (¼ / ½ /
    full) in the top bar, and every edge turns into a suggested dollar stake and
    number of contracts — `f = edge ÷ (100 − cost)`, scaled by your fraction.
  - **Backtesting:** the **🔬 Backtest the model** card replays real price
    history (predicting from past data only, then checking what happened) and
    reports accuracy, **Brier score** vs the 0.25 coin-flip baseline,
    **calibration** (predicted % vs actual), and an illustrative ROI. Use it to
    see whether a coin/horizon actually has signal before betting it. *(Reality
    check: at very short horizons crypto tends to mean-revert, so the directional
    model is near a coin flip — the scanner's value is in catching Kalshi
    mispricings, not in predicting direction.)*
  - **When to sell what you hold:** tap **“I bought this”** on a scanned
    contract (or any tracked market) and enter the side + what you paid. The app
    then shows live **SELL / HOLD** guidance with your profit/loss, updating every
    few seconds. The rule: your contract is worth its **fair value** if you hold
    to the close, so if you can **sell for more than fair value, take it**;
    if the sale price is still below fair value, hold for the remaining upside.
- **Kalshi live scanner.** Pulls the *real* open Kalshi crypto contracts
  (15-minute, hourly, and daily) with their live YES/NO prices — no API key
  needed — runs the model on every strike, and **ranks them by edge** so the
  best opportunities float to the top. One click tracks any contract.
- **Outcome tracking.** Each market you track is **auto-resolved** when its
  window closes (was the price more or less than your amount?). The app records
  whether the model's call was right and shows a running **accuracy %** and
  **Brier score** (lower = sharper probabilities).

## Baseball (⚾ tab)

Pick a date and **Load slate**. For every MLB game that day it shows:

- **Win probability** from an **expected-runs model** that folds in:
  - **Starting pitchers** — each probable starter's **ERA + WHIP**, regressed
    toward league average by innings pitched (so small-sample ERAs don't fool
    it) and blended with their **recent form** (last-5 starts). ~60% of the game.
  - **Bullpen** — each team's **isolated reliever ERA + WHIP** (relievers only,
    not the whole staff) for the rest of the game.
  - **Offense** — each team's **runs/game and OPS**, with OPS taken vs the
    opposing starter's **handedness** (platoon split: OPS vs LHP / vs RHP).
  - **Confirmed lineups** — once today's lineup is posted, each hitter's OPS
    (regressed by at-bats) sets a lineup-strength adjustment, so rested
    regulars / call-ups / injuries that are out of the lineup are reflected.
  - **Home-field advantage** and **park factors**.
  - **Game-time weather** — geocoded to the stadium and pulled from **NOAA**
    (with an **Open-Meteo** fallback): temperature, plus **wind blowing out/in
    to center** (using each park's orientation) and rain chance. Warm air and
    wind blowing out raise scoring. Domes are neutral; retractable roofs get
    half weight.

  These produce each side's **expected runs** (via the odds-ratio method), which
  convert to a win probability with the **Pythagorean** formula. Lineups affect
  the pick; park + weather mainly drive the **expected total** (over/under),
  since they help both offenses. The card shows starters' lines (handedness +
  recent form), platoon-split OPS, bullpen ERA/WHIP, lineup OPS, records,
  expected runs, the expected total, and the live weather.
- The model's **pick** and confidence, ranked most-confident first.
- The **live Kalshi price** for that pick and the **edge** (model % − market ¢),
  when the game can be matched to a Kalshi `KXMLBGAME` market.
- **Live score + status** for every game (scheduled / 🔴 live with inning /
  final), which **auto-refreshes every 20s** while the tab is open — like the
  crypto tab.
- A **📊 Props & odds** panel (expandable) per game, derived from the run model:
  - **Run line / win-by-margin** — chance the favorite wins by 2+ (−1.5) and the
    underdog stays within 1 (+1.5).
  - **Total runs over/under** for a few lines around the model total.
  - **Hit props** — each posted hitter's **1+ and 2+ hit odds** (binomial over
    expected plate appearances and a per-PA hit rate adjusted for the opposing
    pitching), plus a **team total-hits over/under**.

### Combos (parlays)

Below the games, **🎲 Suggested combos** builds parlays from the most confident
picks (all legs must win). For each combo it shows the **combined chance**
(probabilities multiplied), the **fair payout** (1 ÷ chance), and — when every
leg has a live Kalshi price — the **actual parlay payout and EV%**, so you can
spot +EV combos. It highlights a **🛡️ Safest** combo (highest chance) and a
**💰 Best value** combo (highest EV).

There are two groups:
- **Game-winner parlays** — moneyline picks (these carry live Kalshi prices, so
  they get EV).
- **🎲 Mixed combos (incl. props)** — legs drawn from *every* bet type
  (moneyline, run line, totals, hit props), each tagged by type. To keep the
  math honest, a combo never uses two legs from the same game, so the legs stay
  (roughly) independent and the combined chance is a straight multiply.

> Parlays are higher-risk: a 3-leg combo of 60% picks only hits ~22% of the
> time. Bigger payout, longer odds. The "safest" combo is the more conservative
> play.

## How the crypto odds work (plain English)

1. Pull the last few hours of 1-minute candles.
2. Measure how *jumpy* the price is (volatility) and any recent *lean* (drift,
   deliberately damped so it doesn't over-trust the last few candles).
3. Project that forward to your window's close time as a bell curve of likely
   prices.
4. The share of that curve on the YES side of your threshold = the YES
   probability. Closer threshold + more time + more volatility = closer to a
   coin flip; far-away threshold + little time = near-certain.

## Run it

Requires Python 3.9+.

```bash
pip install -r requirements.txt
python app.py
```

Then open <http://localhost:5000>. (Set `PORT` to change the port.)

### Two ways to use it

**A) Kalshi live scanner (easiest).** In the scanner card, pick a coin and
timeframe (15-min / hourly / daily) and hit **Scan**. It pulls the real open
Kalshi contracts and their live prices, shows the model's fair value and edge
for each, and ranks them best-first. Hit **Track** on any contract to monitor it
and score the call.

**B) Manual market.** Define your own market if you want to model a specific
threshold:

1. Pick a **coin** and whether YES means price **ABOVE** or **BELOW** your number.
2. Enter the **threshold** (the dollar amount the Kalshi market is set at).
3. Choose the **window** — next 15-min mark, top of the hour, +15/+60 min, or a
   custom time — matching the Kalshi contract you're looking at.
4. *(Optional)* Enter the current **Kalshi YES price in cents** to get edge-based
   signals instead of pure probability signals.
5. A live preview appears instantly. Hit **Track this market** to save it; it
   keeps updating every few seconds and auto-resolves at close.

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask server + JSON API |
| `prices.py` | Live spot + candle feed (Coinbase, stdlib only) |
| `kalshi.py` | Live Kalshi market data (public, read-only) |
| `baseball.py` | MLB expected-runs model (pitching, bullpen, platoon, lineups, park, weather) + parlay combos |
| `props.py` | Derived props: run line, totals, and player/team hit odds |
| `sports.py` | Multi-sport Kalshi browser (soccer/tennis/golf/UFC) with no-vig odds |
| `recorder.py` | Background Kalshi-quote recorder + real-outcome strategy backtest |
| `backtest.py` | Model-only crypto backtest over historical prices |
| `weather.py` | Game-time weather (NOAA + Open-Meteo) and run-environment factor |
| `stadiums.py` | Stadium coordinates, roof type, and center-field orientation |
| `odds.py` | The crypto odds generator + signal logic |
| `store.py` | SQLite storage, auto-resolution, accuracy/Brier stats |
| `templates/index.html`, `static/` | The live web UI |

## Tuning the model

In `odds.py`:
- `DRIFT_DAMPING` — how much to trust recent trend (0 = ignore, 1 = full).
- `STRONG_PROB` / `LEAN_PROB` — thresholds for strong vs. mild signals.
- `MIN_EDGE_CENTS` — minimum edge over the live Kalshi price before it calls a buy.

## Notes & ideas for later

- Resolution uses Coinbase's price, which may differ slightly from Kalshi's
  settlement source; treat outcomes as approximate.
- The model assumes "random walk" behavior — it has no knowledge of news events.
- The live scanner reads Kalshi's public market data (no key). **Placing or
  selling orders** would require authenticated Kalshi API access (an API key +
  RSA request signing) — that's the natural next step if you want it to trade,
  not just advise.
