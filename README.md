# ⚡ Kalshi Crypto Betting Helper

A live web app that helps you trade Kalshi's short-term crypto price markets
(e.g. "Will BTC be above $63,000 at 3:00pm?"). You enter the threshold and
window; it runs an **internal odds generator** off live price data and tells you
when to **buy YES**, **buy NO**, or **hold** — including when to *buy the dip*.
It also tracks every call and shows you how accurate the model has been.

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
- **Outcome tracking.** Each market you track is **auto-resolved** when its
  window closes (was the price more or less than your amount?). The app records
  whether the model's call was right and shows a running **accuracy %** and
  **Brier score** (lower = sharper probabilities).

## How the odds work (plain English)

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

### Using it

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
| `odds.py` | The odds generator + signal logic |
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
- A natural next step is connecting Kalshi's own API to auto-pull live contract
  prices (instead of pasting them) and to place/sell orders.
