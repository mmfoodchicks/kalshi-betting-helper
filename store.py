"""SQLite persistence for markets, their signal snapshots, and outcomes.

A "market" is one thing you're watching, e.g. "BTC above 63000, closing at
3:00pm". We store:
  - the market definition
  - the signal snapshot taken when it was created (so we can score the model)
  - the resolved outcome once the window closes (was it more/less than the
    amount?) and whether the model's call was correct

This drives the running accuracy + Brier score the UI shows.
"""

import os
import sqlite3
import time
import threading

DB_PATH = os.environ.get("KALSHI_DB", os.path.join(os.path.dirname(__file__), "markets.db"))

_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _lock, _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coin TEXT NOT NULL,
                threshold REAL NOT NULL,
                direction TEXT NOT NULL,          -- 'above' | 'below'
                close_time INTEGER NOT NULL,       -- epoch seconds
                created_at INTEGER NOT NULL,
                yes_price_cents REAL,              -- optional live Kalshi YES price at creation
                -- snapshot of the model at creation, for scoring
                snap_prob_yes REAL,
                snap_recommendation TEXT,
                snap_spot REAL,
                -- optional held position (so we can advise when to sell)
                kalshi_ticker TEXT,                -- live Kalshi market id, if from the scanner
                position_side TEXT,                -- 'YES' | 'NO' you bought, or NULL
                entry_cost_cents REAL,             -- what you paid for it, in cents
                -- resolution
                resolved INTEGER NOT NULL DEFAULT 0,
                outcome TEXT,                      -- 'YES' | 'NO'
                resolve_price REAL,
                correct INTEGER                    -- 1/0 whether recommendation matched outcome (NULL if HOLD)
            )
            """
        )
        # Lightweight migration for databases created before positions existed.
        cols = {r[1] for r in c.execute("PRAGMA table_info(markets)").fetchall()}
        for col, decl in (("kalshi_ticker", "TEXT"), ("position_side", "TEXT"),
                          ("entry_cost_cents", "REAL")):
            if col not in cols:
                c.execute(f"ALTER TABLE markets ADD COLUMN {col} {decl}")


def add_market(coin, threshold, direction, close_time, yes_price_cents,
               snap_prob_yes, snap_recommendation, snap_spot,
               kalshi_ticker=None, position_side=None, entry_cost_cents=None):
    with _lock, _conn() as c:
        cur = c.execute(
            """
            INSERT INTO markets
              (coin, threshold, direction, close_time, created_at, yes_price_cents,
               snap_prob_yes, snap_recommendation, snap_spot,
               kalshi_ticker, position_side, entry_cost_cents)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (coin, threshold, direction, int(close_time), int(time.time()),
             yes_price_cents, snap_prob_yes, snap_recommendation, snap_spot,
             kalshi_ticker, position_side, entry_cost_cents),
        )
        return cur.lastrowid


def set_position(market_id, side, entry_cost_cents):
    """Record (or clear) a held position so we can give sell guidance."""
    side = side.upper() if side else None
    with _lock, _conn() as c:
        c.execute(
            "UPDATE markets SET position_side=?, entry_cost_cents=? WHERE id=?",
            (side, entry_cost_cents, market_id),
        )


def get_market(market_id):
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM markets WHERE id=?", (market_id,)).fetchone()
        return dict(row) if row else None


def list_markets(include_resolved=True):
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM markets ORDER BY close_time ASC").fetchall()
    out = [dict(r) for r in rows]
    if not include_resolved:
        out = [m for m in out if not m["resolved"]]
    return out


def delete_market(market_id):
    with _lock, _conn() as c:
        c.execute("DELETE FROM markets WHERE id=?", (market_id,))


def resolve_market(market_id, resolve_price):
    """Mark a market resolved based on the price at close. Returns the row."""
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM markets WHERE id=?", (market_id,)).fetchone()
        if not row or row["resolved"]:
            return dict(row) if row else None
        m = dict(row)
        if m["direction"] == "above":
            outcome = "YES" if resolve_price >= m["threshold"] else "NO"
        else:
            outcome = "YES" if resolve_price <= m["threshold"] else "NO"

        rec = m["snap_recommendation"]
        if rec == "BUY YES":
            correct = 1 if outcome == "YES" else 0
        elif rec == "BUY NO":
            correct = 1 if outcome == "NO" else 0
        else:
            correct = None  # HOLD isn't scored as right/wrong

        c.execute(
            """
            UPDATE markets
            SET resolved=1, outcome=?, resolve_price=?, correct=?
            WHERE id=?
            """,
            (outcome, resolve_price, correct, market_id),
        )
        m.update(resolved=1, outcome=outcome, resolve_price=resolve_price, correct=correct)
        return m


def stats():
    """Aggregate accuracy + Brier score across resolved, scored markets."""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT * FROM markets WHERE resolved=1 AND correct IS NOT NULL"
        ).fetchall()
    scored = [dict(r) for r in rows]
    total = len(scored)
    wins = sum(1 for m in scored if m["correct"] == 1)

    # Brier score over all resolved markets that had a probability snapshot.
    with _lock, _conn() as c:
        prob_rows = c.execute(
            "SELECT outcome, snap_prob_yes FROM markets "
            "WHERE resolved=1 AND snap_prob_yes IS NOT NULL"
        ).fetchall()
    brier_n = 0
    brier_sum = 0.0
    for r in prob_rows:
        actual = 1.0 if r["outcome"] == "YES" else 0.0
        p = r["snap_prob_yes"]
        brier_sum += (p - actual) ** 2
        brier_n += 1

    return {
        "scored_markets": total,
        "wins": wins,
        "losses": total - wins,
        "accuracy_pct": round(100 * wins / total, 1) if total else None,
        "brier_score": round(brier_sum / brier_n, 4) if brier_n else None,
        "brier_samples": brier_n,
    }
