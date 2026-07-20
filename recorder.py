"""Live Kalshi quote recorder + real-outcome strategy backtester.

The model-only backtest (backtest.py) proves whether the model predicts price
*direction*. But the scanner's actual edge is catching when Kalshi *misprices* a
contract. To validate THAT we need real Kalshi quotes paired with real outcomes
-- which don't exist historically, so we record them ourselves.

A background thread samples open crypto contracts every ~90s, storing the live
Kalshi prices alongside the model's fair value, recommendation, and edge. When a
market's window closes we settle it from the real price. Then `backtest()` asks
the only question that matters: if you had bet every time the model flagged an
edge, at the real Kalshi price, would you have made money?
"""

import threading
import time

import store
import prices
import odds
import kalshi

# Coins/timeframes we passively record while the app runs.
WATCH = [("BTC", "15M"), ("BTC", "hourly"), ("ETH", "15M"),
         ("ETH", "hourly"), ("SOL", "15M"), ("SOL", "hourly")]
SAMPLE_INTERVAL = 90  # seconds

_lock = threading.Lock()


def _conn():
    # Share store's tuned connection (WAL + busy timeout) so the recorder's writes
    # and the request threads' writes to the same markets.db don't collide.
    return store._conn()


def init_db():
    with _lock, _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS quote_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER, ticker TEXT, coin TEXT, timeframe TEXT,
                spot REAL, fair_yes REAL,
                yes_ask REAL, no_ask REAL, yes_bid REAL, no_bid REAL,
                edge_yes REAL, edge_no REAL, recommendation TEXT,
                strike_type TEXT, floor REAL, cap REAL, close_time INTEGER
            )""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS resolved_markets (
                ticker TEXT PRIMARY KEY, coin TEXT, close_time INTEGER,
                settle_price REAL, outcome TEXT, resolved_ts INTEGER
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_qs_ticker ON quote_samples(ticker)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_qs_ts ON quote_samples(ts)")


def sample_once():
    """Record one snapshot of every liquid open contract on the watchlist."""
    n = 0
    for coin, tf in WATCH:
        try:
            markets = kalshi.get_open_markets(coin, tf)
            spot = prices.get_spot(coin)
            candles = prices.get_candles(coin, granularity=60)
        except Exception:
            continue
        ts = int(time.time())
        with _lock, _conn() as c:
            for m in markets:
                # Only record contracts that are actually tradeable (two-sided).
                if not m.get("yes_ask") or m["yes_ask"] >= 100:
                    continue
                if not m.get("yes_bid"):
                    continue
                mins = max(0.0, (m["close_time"] - ts) / 60.0) if m["close_time"] else 0.0
                sig = odds.kalshi_signal(spot, candles, m, mins)
                c.execute(
                    """INSERT INTO quote_samples
                       (ts, ticker, coin, timeframe, spot, fair_yes, yes_ask, no_ask,
                        yes_bid, no_bid, edge_yes, edge_no, recommendation,
                        strike_type, floor, cap, close_time)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (ts, m["ticker"], coin, tf, spot, sig["fair_yes_cents"],
                     m.get("yes_ask"), m.get("no_ask"), m.get("yes_bid"), m.get("no_bid"),
                     sig["edge_yes_cents"], sig["edge_no_cents"], sig["recommendation"],
                     m.get("strike_type"), m.get("floor"), m.get("cap"), m.get("close_time")),
                )
                n += 1
    return n


def calibration_pairs(coin=None, timeframe=None):
    """(model fair-YES prob 0-1, actual 0/1) for every resolved crypto market —
    one pair per ticker (its first recorded fair value). Feeds the crypto
    temperature calibrator."""
    with _lock, _conn() as c:
        resolved = {r["ticker"]: (r["outcome"]) for r in
                    c.execute("SELECT ticker, outcome FROM resolved_markets").fetchall()}
        rows = c.execute(
            "SELECT ticker, coin, timeframe, fair_yes FROM quote_samples ORDER BY ts ASC").fetchall()
    seen, out = set(), []
    for s in rows:
        tk = s["ticker"]
        if tk in seen or tk not in resolved or s["fair_yes"] is None:
            continue
        if coin and s["coin"] != coin:
            continue
        if timeframe and s["timeframe"] != timeframe:
            continue
        seen.add(tk)
        out.append((s["fair_yes"] / 100.0, 1.0 if resolved[tk] == "YES" else 0.0))
    return out


def _outcome(strike_type, floor, cap, price):
    st = (strike_type or "").lower()
    if st in ("greater", "greater_or_equal"):
        return "YES" if price >= floor else "NO"
    if st in ("less", "less_or_equal"):
        return "YES" if price <= cap else "NO"
    if st == "between" and floor is not None and cap is not None:
        return "YES" if floor <= price <= cap else "NO"
    return "NO"


def resolve_due():
    """Settle any recorded market whose window has closed."""
    now = int(time.time())
    with _lock, _conn() as c:
        rows = c.execute(
            """SELECT DISTINCT ticker, coin, close_time, strike_type, floor, cap
               FROM quote_samples
               WHERE close_time IS NOT NULL AND close_time < ?
                 AND ticker NOT IN (SELECT ticker FROM resolved_markets)""",
            (now,),
        ).fetchall()
    for r in rows:
        try:
            price = prices.get_price_at(r["coin"], r["close_time"])
        except Exception:
            continue
        outcome = _outcome(r["strike_type"], r["floor"], r["cap"], price)
        with _lock, _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO resolved_markets VALUES (?,?,?,?,?,?)",
                (r["ticker"], r["coin"], r["close_time"], price, outcome, int(time.time())),
            )


def status():
    with _lock, _conn() as c:
        s = c.execute("SELECT COUNT(*) n, COUNT(DISTINCT ticker) t, MIN(ts) lo, MAX(ts) hi "
                      "FROM quote_samples").fetchone()
        r = c.execute("SELECT COUNT(*) n FROM resolved_markets").fetchone()
    return {"samples": s["n"], "tickers": s["t"], "resolved": r["n"],
            "first_ts": s["lo"], "last_ts": s["hi"]}


def backtest(coin=None, timeframe=None):
    """Realized P&L of following the model's edge, at real Kalshi prices.

    One bet per resolved market: the first recorded moment the model flagged an
    edge above the threshold (its entry), filled at that snapshot's ask, settled
    at the real outcome. Swept across several edge thresholds so you can see how
    selective you need to be.
    """
    with _lock, _conn() as c:
        resolved = {r["ticker"]: dict(r) for r in
                    c.execute("SELECT * FROM resolved_markets").fetchall()}
        samples = [dict(s) for s in c.execute(
            "SELECT * FROM quote_samples ORDER BY ts ASC").fetchall()]

    if coin:
        samples = [s for s in samples if s["coin"] == coin]
    if timeframe:
        samples = [s for s in samples if s["timeframe"] == timeframe]

    # Model calibration: first sample per resolved ticker, fair vs actual outcome.
    cal_seen = set()
    brier_sum = brier_n = 0
    for s in samples:
        tk = s["ticker"]
        if tk in resolved and tk not in cal_seen:
            cal_seen.add(tk)
            actual = 1.0 if resolved[tk]["outcome"] == "YES" else 0.0
            p = (s["fair_yes"] or 0) / 100.0
            brier_sum += (p - actual) ** 2
            brier_n += 1
    brier = round(brier_sum / brier_n, 4) if brier_n else None

    thresholds = [2, 4, 6, 8, 10, 15]
    sweep = []
    for thr in thresholds:
        used = set()
        bets = []
        for s in samples:
            tk = s["ticker"]
            if tk not in resolved or tk in used:
                continue
            rec = s["recommendation"]
            if rec == "BUY YES" and (s["edge_yes"] or 0) >= thr:
                side, cost = "YES", s["yes_ask"]
            elif rec == "BUY NO" and (s["edge_no"] or 0) >= thr:
                side, cost = "NO", s["no_ask"]
            else:
                continue
            if not cost or cost <= 0 or cost >= 100:
                continue
            used.add(tk)
            win = resolved[tk]["outcome"] == side
            bets.append((cost, win))
        n = len(bets)
        if n:
            staked = sum(c for c, _ in bets)
            pnl = sum((100 if w else 0) - c for c, w in bets)
            # Kalshi trading fee ≈ 7 × price × (1−price) cents per contract (paid
            # on entry). Net P&L is what actually lands in your account.
            fees = sum(7.0 * (c / 100.0) * (1 - c / 100.0) for c, _ in bets)
            net = pnl - fees
            wins = sum(1 for _, w in bets if w)
            sweep.append({"min_edge": thr, "bets": n,
                          "win_pct": round(100 * wins / n, 1),
                          "roi_pct": round(100 * pnl / staked, 1) if staked else None,
                          "roi_net_pct": round(100 * net / staked, 1) if staked else None,
                          "pnl_per_contract_c": round(pnl / n, 1),
                          "net_per_contract_c": round(net / n, 1)})
        else:
            sweep.append({"min_edge": thr, "bets": 0, "win_pct": None,
                          "roi_pct": None, "roi_net_pct": None,
                          "pnl_per_contract_c": None, "net_per_contract_c": None})

    return {"resolved_markets": len(resolved), "calibration_brier": brier,
            "brier_n": brier_n, "sweep": sweep,
            "coin": coin, "timeframe": timeframe}


def _loop():
    while True:
        try:
            sample_once()
            resolve_due()
        except Exception:
            pass
        time.sleep(SAMPLE_INTERVAL)


def start_background():
    init_db()
    threading.Thread(target=_loop, daemon=True, name="kalshi-recorder").start()
