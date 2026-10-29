# ⚡ Vigil

The math behind the line — a multi-market edge engine. It pulls live prices
(Kalshi's API, DraftKings DFS, and more), runs its own models and Monte
Carlo simulators to estimate true odds, surfaces where the market is mispriced,
and grades itself honestly over time. Kalshi is just one data source, not the
whole point.

A live web app with tabs:

- **⚾ Baseball** — model win probabilities for today's MLB slate, matched to
  live Kalshi prices to find edges, plus **parlay combo suggestions**.
- **🏆 Sports** — live Kalshi markets for **tennis (ATP/WTA/ITF), golf
  (PGA H2H), UFC, MLS and more**, with **no-vig fair probabilities** (prices normalized to
  strip out the house margin) and the **vig %** so you can see which markets are
  fair and which the house is gouging. **Arbitrage detector:** if an event's
  outcome prices ever sum to under 100¢, it flags the guaranteed profit. Log any
  outcome to your ledger.
- **🌡️ Weather** — Kalshi daily high-temperature markets vs the **NOAA
  forecast**: the model puts a distribution around the forecast high, prices each
  strike, and flags where Kalshi disagrees. (Big edges usually mean a
  station mismatch, not free money — trust the moderate ones.)
- **📒 Ledger** — log every real bet you place and track actual profit, win
  rate, and ROI across everything.

The baseball tab also tracks the **model's real record** — picks are recorded
pre-game and graded after finals into a running accuracy / ROI / Brier line.

(The app began as a Kalshi crypto helper; every crypto feature was removed on
2026-10-29.)

> ⚠️ This is a decision-support tool, not a crystal ball. The odds are model
> estimates. Markets can and will move against the model. Never bet more than
> you can afford to lose.

---

**Kelly bet-sizing:** set your **bankroll** and a Kelly fraction (¼ / ½ / full)
in the top bar, and edges carry a suggested dollar stake and number of
contracts — `f = edge ÷ (100 − cost)`, scaled by your fraction.

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
  final), which **auto-refreshes every 20s** while the tab is open.
- **In-game win probability** for games in progress — instead of the pre-game
  number, live games are re-rated from the **current score, inning, outs, and
  baserunners**: each team's expected remaining runs (outs left × scoring rate +
  base/out state) feeds a Poisson win-probability model. The live combos use
  these updated numbers.
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

## ☁️ Host it 24/7 (so your PC doesn't have to run)

The repo ships with deploy config. Easiest path — **Render** (free):

1. Push this repo to your GitHub.
2. On [render.com](https://render.com): **New → Blueprint**, pick the repo.
   It reads `render.yaml` and deploys automatically (gunicorn web service).
3. You get a permanent `https://…onrender.com` URL — open it on your phone and
   **Add to Home Screen** (it's already a PWA), or feed that URL to PWABuilder
   for an APK.

That URL is HTTPS, so the full installable app + APK work with zero tunneling.

### Push → live with nothing to do but refresh

Two mechanisms make a `git push` reach your phone hands-free:

- **On Render:** `render.yaml` sets `autoDeploy: true`, so a push to the branch
  your service watches rebuilds and redeploys automatically (~1–2 min). Make
  sure the dashboard's deploy branch matches the branch that gets pushed.
- **On a home machine / VPS** (`python app.py` or `start_remote.py`): set
  `VIGIL_SELFUPDATE=1` and the server polls its branch every ~2 min, pulls new
  commits, and restarts itself — no manual `git pull`. (Optional:
  `VIGIL_SELFUPDATE_BRANCH`, `VIGIL_SELFUPDATE_INTERVAL`.) It safely no-ops
  where it can't apply (Render's container has no git checkout).

Either way, any open page checks `/api/version` once a minute and pops a
**"🔄 New version available — Refresh"** banner the moment the new code is live,
so you just tap it (or reload). The service worker is network-first, so the
refresh always pulls the fresh UI.

Also included for other hosts: a **`Procfile`** (Railway/Heroku-style) and a
**`Dockerfile`** (Fly.io, or any container host). Run command is always
`gunicorn -w 1 --threads 8 -b 0.0.0.0:$PORT app:app`.

> Notes: keep it to **one worker** (`-w 1`) so the background recorder runs as a
> single instance. On a **free** tier the service sleeps after inactivity, which
> pauses the recorder and resets the local `markets.db` — fine for casual use; a
> cheap paid tier (or a persistent disk) keeps it always-on and keeps history.

## 📱 Put it on your phone

The app is an installable **PWA** — add it to your home screen and it runs
fullscreen with its own icon. It still talks to the server you run, so the
server has to be reachable from your phone. Two ways:

**A) Same Wi-Fi (simplest).** Run the server, find your computer's local IP
(e.g. `192.168.1.50`), then on your phone's browser open
`http://192.168.1.50:5000`. In your browser menu tap **Add to Home Screen**.
(Note: over plain `http` it works as a shortcut; full install/offline needs
HTTPS — see B.)

**B) Anywhere + real install/APK (HTTPS via a free tunnel).** With the server
running, in another terminal start a Cloudflare tunnel:

```bash
cloudflared tunnel --url http://localhost:5000
```

It prints an `https://…trycloudflare.com` URL. Open that on your phone →
**Add to Home Screen** for a true installed PWA.

**Want an actual APK?** Take that `https` URL to **https://www.pwabuilder.com**,
paste it, and it generates a real Android **APK** (and a Play Store package) from
the PWA — no Android Studio needed.

> Why not a standalone offline APK? All the modeling and live Kalshi/price/stat
> calls happen in the Python backend, so the app needs the server running — it
> can't all be bundled into the phone. The PWA is the clean way to get an
> app-like icon + fullscreen experience pointed at your server.

## Run it

Requires Python 3.9+.

```bash
pip install -r requirements.txt
python app.py
```

Then open <http://localhost:5000>. (Set `PORT` to change the port.)

### Smoke suite (run before shipping model/sim changes)

```bash
python3 tests/run_smoke.py            # fast, offline: grading, DFS rules, fees, clock
python3 tests/run_smoke.py --online   # + brute-force audit of the combo math on today's slate
```

Each test encodes a bug class that actually shipped once (accumulator key
drift, over-cap lineups, DNP graded as a loss, remainder shown as season-end).
Runs against a throwaway database — it never touches your real record.

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
| `kalshi.py` | Live Kalshi market data (public, read-only) |
| `baseball.py` | MLB expected-runs model (pitching, bullpen, platoon, lineups, park, weather) + parlay combos |
| `props.py` | Derived props: run line, totals, and player/team hit odds |
| `sports.py` | Multi-sport Kalshi browser (soccer/tennis/golf/UFC) with no-vig odds |
| `weather.py` | Game-time weather (NOAA + Open-Meteo) and run-environment factor |
| `stadiums.py` | Stadium coordinates, roof type, and center-field orientation |
| `store.py` | SQLite ledgers (picks, props, slips, bets) and the shared scoreboard math |
| `templates/index.html`, `static/` | The live web UI |

## Notes & ideas for later

- The app reads Kalshi's public market data (no key). **Placing or
  selling orders** would require authenticated Kalshi API access (an API key +
  RSA request signing) — that's the natural next step if you want it to trade,
  not just advise.
