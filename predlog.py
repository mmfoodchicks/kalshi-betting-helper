"""Universal prediction logger — the missing piece for site-wide calibration.

MLB and crypto have graded prediction-vs-outcome history (game picks, prop log,
the crypto recorder), so they calibrate. Tennis, UFC, racing, NFL and LoL don't,
so they can't — yet. This module fixes that: it captures each sim's Kalshi
prediction the first time it's priced, and when that market settles it reads the
real outcome and grades it. calibrate.py then reads pairs(model) and fits a
temperature exactly as it does for MLB/crypto — no new calibration code needed.

Design mirrors the crypto recorder:
  - one row per (model, ticker), logged the first time we see it (INSERT OR
    IGNORE), storing the model's YES probability for that market;
  - a cheap background resolver reads each settled ticker's result and grades it;
  - harvesting piggybacks on the sims' already-cached boards, so it adds ~no
    compute (a warm board is a cache hit).

Nothing here changes a prediction — it only records and grades, so calibration
accrues honestly from real outcomes.
"""
import os
import sqlite3
import threading
import time

_DB = os.environ.get("PREDLOG_DB") or os.path.join(os.path.dirname(__file__), "predlog.db")
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")   # readers (calibrate) don't block the writer
    return c


def init_db():
    with _lock, _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS predictions (
            ticker TEXT PRIMARY KEY,
            model TEXT,
            prob REAL,
            close_time INTEGER,
            ts INTEGER,
            graded INTEGER DEFAULT 0,
            outcome INTEGER,
            resolved_ts INTEGER)""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_pred_model ON predictions(model, graded)")


def log_many(model, rows):
    """rows: iterable of (ticker, prob 0-1, close_time_epoch|None). One row per
    ticker ever (the first, pre-settlement prediction) — later re-prices are
    ignored so we grade the genuine forecast, not a near-settled one."""
    clean = []
    for tk, p, ct in rows:
        if not tk or p is None:
            continue
        try:
            p = float(p)
        except (TypeError, ValueError):
            continue
        if not (0.0 < p < 1.0):
            continue
        clean.append((tk, model, p, int(ct) if ct else None, int(time.time())))
    if not clean:
        return 0
    with _lock, _conn() as c:
        c.executemany(
            "INSERT OR IGNORE INTO predictions (ticker, model, prob, close_time, ts) "
            "VALUES (?,?,?,?,?)", clean)
    return len(clean)


def log(model, ticker, prob, close_time=None):
    return log_many(model, [(ticker, prob, close_time)])


def pairs(model):
    """(prob, outcome 0/1) for every graded prediction of a model — the evidence
    calibrate.py fits its temperature on."""
    with _lock, _conn() as c:
        return [(r["prob"], float(r["outcome"])) for r in c.execute(
            "SELECT prob, outcome FROM predictions "
            "WHERE model=? AND graded=1 AND outcome IS NOT NULL", (model,)).fetchall()]


def resolve_due(limit=80):
    """Grade ungraded predictions whose market has settled. Reads each ticker's
    real result from Kalshi (cheap: only past-close tickers, capped per cycle)."""
    import kalshi
    now = int(time.time())
    with _lock, _conn() as c:
        due = [r["ticker"] for r in c.execute(
            "SELECT ticker FROM predictions WHERE graded=0 "
            "AND (close_time IS NULL OR close_time <= ?) "
            "ORDER BY close_time IS NULL, close_time LIMIT ?", (now, limit)).fetchall()]
    graded = 0
    for tk in due:
        try:
            m = kalshi.get_market(tk)
        except Exception:
            continue
        if (m.get("status") or "").lower() != "settled" or m.get("result") not in ("yes", "no"):
            continue
        outcome = 1 if m["result"] == "yes" else 0
        with _lock, _conn() as c:
            c.execute("UPDATE predictions SET graded=1, outcome=?, resolved_ts=? WHERE ticker=?",
                      (outcome, now, tk))
        graded += 1
    return graded


def status():
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT model, COUNT(*) n, COALESCE(SUM(graded),0) g FROM predictions "
            "GROUP BY model").fetchall()
    return {r["model"]: {"logged": r["n"], "graded": r["g"]} for r in rows}


# ---- Harvest: piggyback on the sims' (cached) boards -----------------------
def _harvest_tennis():
    import tennis_prices
    board = tennis_prices.board() or {}
    rows = []
    for m in board.get("matches", []):
        ct = m.get("close_epoch") or m.get("close_time")
        for side in ("a", "b"):
            p = m.get(side) or {}
            # Log the RAW model number, not the calibrated one (avoids feedback).
            fair = p.get("fair_win_raw", p.get("fair_win"))
            if p.get("ticker") and fair is not None:
                rows.append((p["ticker"], fair / 100.0, ct))
    return log_many("tennis", rows)


def _harvest_ufc():
    import ufc_sim
    import ufc_prices
    board = ufc_sim.board()
    if not board:
        return 0
    ufc_prices.attach(board)
    rows = []
    for bt in board.get("bouts", []):
        for side in ("a", "b"):
            f = bt.get(side) or {}
            fair = f.get("fair_win_raw", f.get("fair_win"))
            if f.get("ticker") and fair is not None:
                rows.append((f["ticker"], fair / 100.0, f.get("close_time")))
    return log_many("ufc", rows)


_HARVESTERS = (_harvest_tennis, _harvest_ufc)


def harvest():
    total = 0
    for h in _HARVESTERS:
        try:
            total += h()
        except Exception:
            continue
    return total


_started = False


def _loop(interval):
    while True:
        time.sleep(interval)
        try:
            harvest()
        except Exception:
            pass
        try:
            resolve_due()
        except Exception:
            pass


def start(interval=900):
    """Background harvest + resolve loop. Boards are cached, so harvesting is
    cheap; resolving is a handful of Kalshi lookups. Runs once per process."""
    global _started
    if _started:
        return
    _started = True
    init_db()
    threading.Thread(target=_loop, args=(interval,), daemon=True).start()
