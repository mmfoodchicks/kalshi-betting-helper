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

        # Model's MLB picks, recorded pre-game and graded after finals.
        c.execute("""
            CREATE TABLE IF NOT EXISTS mlb_picks (
                game_pk INTEGER PRIMARY KEY,
                date TEXT, pick_side TEXT, pick_name TEXT,
                prob REAL, price_cents REAL,
                graded INTEGER DEFAULT 0, won INTEGER, winner_name TEXT
            )""")
        # close_price (CLV) + predicted/actual total runs (sim accuracy).
        mcols = {r["name"] for r in c.execute("PRAGMA table_info(mlb_picks)")}
        for col in ("close_price", "pred_total", "actual_total"):
            if col not in mcols:
                c.execute(f"ALTER TABLE mlb_picks ADD COLUMN {col} REAL")

        # Unified bet ledger (real bets you place, crypto or baseball or other).
        c.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                kind TEXT,                -- 'crypto' | 'baseball' | 'other'
                description TEXT,
                side TEXT,
                stake REAL,               -- dollars risked
                price_cents REAL,         -- entry price (0..100) for payout math
                status TEXT DEFAULT 'open',  -- 'open' | 'won' | 'lost' | 'void'
                pnl REAL,                 -- realized profit/loss (dollars)
                settled_at INTEGER,
                notes TEXT
            )""")


# ---- MLB model track record -----------------------------------------------
def record_mlb_pick(game_pk, date, pick_side, pick_name, prob, price_cents, pred_total=None):
    """Store the model's pre-game pick (first time we see the game)."""
    with _lock, _conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO mlb_picks
               (game_pk, date, pick_side, pick_name, prob, price_cents, close_price, pred_total)
               VALUES (?,?,?,?,?,?,?,?)""",
            (game_pk, date, pick_side, pick_name, prob, price_cents, price_cents, pred_total),
        )


def update_mlb_close(game_pk, price_cents):
    """Refresh the closing (latest pre-game) price of our side so we can measure
    closing-line value once the game is graded."""
    if price_cents is None:
        return
    with _lock, _conn() as c:
        c.execute("UPDATE mlb_picks SET close_price=? WHERE game_pk=? AND graded=0",
                  (price_cents, game_pk))


def ungraded_mlb_picks():
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM mlb_picks WHERE graded=0").fetchall()]


def set_mlb_grade(game_pk, won, winner_name, actual_total=None):
    with _lock, _conn() as c:
        c.execute("UPDATE mlb_picks SET graded=1, won=?, winner_name=?, actual_total=? WHERE game_pk=?",
                  (won, winner_name, actual_total, game_pk))


def mlb_record():
    with _lock, _conn() as c:
        graded = [dict(r) for r in c.execute(
            "SELECT * FROM mlb_picks WHERE graded=1").fetchall()]
        pending = c.execute("SELECT COUNT(*) n FROM mlb_picks WHERE graded=0").fetchone()["n"]
    n = len(graded)
    wins = sum(1 for p in graded if p["won"] == 1)

    def roi(picks):
        stake = sum(p["price_cents"] for p in picks)
        pnl = sum((100 if p["won"] else 0) - p["price_cents"] for p in picks)
        return (round(100 * pnl / stake, 1) if stake else None), len(picks)

    # ROI as if 1 contract bet per pick at the recorded Kalshi price.
    priced = [p for p in graded if p["price_cents"]]
    roi_pct, _ = roi(priced)
    # Edge-filtered ROI: only bets where the model had >=3c edge over the price
    # (the bets you'd actually place). This is the real test of the model.
    EDGE = 3.0
    edged = [p for p in priced if p["prob"] is not None
             and (p["prob"] * 100 - p["price_cents"]) >= EDGE]
    roi_edge_pct, edge_bets = roi(edged)

    # Closing-line value: did the market move toward our side after we picked?
    # Positive CLV (we beat the close) is the strongest early sign of real edge.
    clv = [p["close_price"] - p["price_cents"] for p in graded
           if p.get("close_price") is not None and p["price_cents"] is not None]
    clv_avg = round(sum(clv) / len(clv), 2) if clv else None
    clv_pos_pct = round(100 * sum(1 for x in clv if x > 0) / len(clv), 1) if clv else None

    # Brier (lower=better; 0.25=coin flip) + calibration buckets.
    bp = [p for p in graded if p["prob"] is not None]
    brier = round(sum((p["prob"] - (1 if p["won"] else 0)) ** 2 for p in bp) / len(bp), 4) if bp else None
    bins = []
    for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)):
        b = [p for p in bp if lo <= p["prob"] < hi]
        if b:
            bins.append({"range": f"{int(lo*100)}-{int(min(hi,1)*100)}%",
                         "n": len(b),
                         "predicted": round(100 * sum(p["prob"] for p in b) / len(b), 1),
                         "actual": round(100 * sum(1 for p in b if p["won"]) / len(b), 1)})
    # Total-runs accuracy: predicted vs actual, aggregated (a single game is
    # noise; over many it shows whether the run model is calibrated/biased).
    tot = [p for p in graded if p.get("pred_total") is not None and p.get("actual_total") is not None]
    if tot:
        tot_pred = sum(p["pred_total"] for p in tot) / len(tot)
        tot_act = sum(p["actual_total"] for p in tot) / len(tot)
        tot_mae = sum(abs(p["pred_total"] - p["actual_total"]) for p in tot) / len(tot)
        totals_acc = {"n": len(tot), "predicted_avg": round(tot_pred, 2),
                      "actual_avg": round(tot_act, 2), "bias": round(tot_pred - tot_act, 2),
                      "mean_abs_error": round(tot_mae, 2)}
    else:
        totals_acc = None
    return {
        "graded": n, "pending": pending, "wins": wins, "losses": n - wins,
        "accuracy_pct": round(100 * wins / n, 1) if n else None,
        "roi_pct": roi_pct, "roi_bets": len(priced),
        "roi_edge_pct": roi_edge_pct, "edge_bets": edge_bets, "edge_threshold": EDGE,
        "clv_avg": clv_avg, "clv_positive_pct": clv_pos_pct, "clv_n": len(clv),
        "totals_accuracy": totals_acc,
        "brier": brier, "brier_baseline": 0.25, "calibration": bins,
    }


# ---- Bet ledger -----------------------------------------------------------
def add_bet(kind, description, side, stake, price_cents, notes=None):
    with _lock, _conn() as c:
        cur = c.execute(
            """INSERT INTO bets (created_at, kind, description, side, stake, price_cents, notes)
               VALUES (?,?,?,?,?,?,?)""",
            (int(time.time()), kind, description, side, stake, price_cents, notes),
        )
        return cur.lastrowid


def _bet_pnl(status, stake, price_cents):
    if status == "won":
        # A contract bought at price_cents returns 100; profit scales accordingly.
        if price_cents and price_cents > 0:
            return round(stake * (100.0 / price_cents - 1.0), 2)
        return round(stake, 2)
    if status == "lost":
        return round(-stake, 2)
    return 0.0  # void


def settle_bet(bet_id, status):
    if status not in ("won", "lost", "void", "open"):
        return None
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()
        if not row:
            return None
        m = dict(row)
        if status == "open":
            pnl, settled = None, None
        else:
            pnl = _bet_pnl(status, m["stake"] or 0, m["price_cents"])
            settled = int(time.time())
        c.execute("UPDATE bets SET status=?, pnl=?, settled_at=? WHERE id=?",
                  (status, pnl, settled, bet_id))
        m.update(status=status, pnl=pnl, settled_at=settled)
        return m


def delete_bet(bet_id):
    with _lock, _conn() as c:
        c.execute("DELETE FROM bets WHERE id=?", (bet_id,))


def list_bets():
    with _lock, _conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM bets ORDER BY created_at DESC").fetchall()]
    settled = [b for b in rows if b["status"] in ("won", "lost", "void")]
    graded = [b for b in settled if b["status"] in ("won", "lost")]
    staked = sum(b["stake"] or 0 for b in graded)
    pnl = sum(b["pnl"] or 0 for b in settled)
    wins = sum(1 for b in graded if b["status"] == "won")
    by_kind = {}
    for b in settled:
        k = b["kind"] or "other"
        d = by_kind.setdefault(k, {"pnl": 0.0, "n": 0})
        d["pnl"] = round(d["pnl"] + (b["pnl"] or 0), 2)
        d["n"] += 1
    summary = {
        "open": sum(1 for b in rows if b["status"] == "open"),
        "settled": len(graded),
        "wins": wins, "losses": len(graded) - wins,
        "win_pct": round(100 * wins / len(graded), 1) if graded else None,
        "total_staked": round(staked, 2),
        "total_pnl": round(pnl, 2),
        "roi_pct": round(100 * pnl / staked, 1) if staked else None,
        "by_kind": by_kind,
    }
    return {"bets": rows, "summary": summary}


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
