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
    # timeout = SQLite's busy timeout: wait for a competing writer instead of
    # failing with "database is locked". The recorder and the request threads use
    # separate Python locks on this same file, so they rely on this to serialize.
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    # WAL lets readers proceed while the recorder writes (and vice-versa); it's a
    # persistent per-file setting, so re-issuing it each connect is a cheap no-op.
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=10000")
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
                # try/except, not just the PRAGMA check: several gunicorn
                # workers run this migration at the same moment on boot, and on
                # a fresh database two of them can both see the column missing
                # before either adds it -- the loser then crashes the worker
                # with "duplicate column name" and the boot loops.
                try:
                    c.execute(f"ALTER TABLE markets ADD COLUMN {col} {decl}")
                except Exception:
                    pass               # a sibling worker just added it

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
        for col in ("close_price", "pred_total", "actual_total",
                    "p_home_model", "p_home_deep", "home_won",
                    # prob_raw: the model's UNCALIBRATED win prob for the pick. `prob`
                    # is the calibrated number we display/bet; the temperature must be
                    # fit on the raw one, or the calibrator trains on its own output
                    # (a feedback loop). Legacy rows have it NULL -> fall back to prob.
                    "prob_raw"):
            if col not in mcols:
                try:               # same boot race as the markets migration
                    c.execute(f"ALTER TABLE mlb_picks ADD COLUMN {col} REAL")
                except Exception:
                    pass           # a sibling worker just added it

        # Player-prop log: every Kalshi-listed batter prop we record while the app
        # runs, with the model %, Kalshi price, and recent/season form at the time,
        # graded against the real box score after the game. Builds the aggregate
        # dataset that validates the prop model + value finder (noise per night).
        c.execute("""
            CREATE TABLE IF NOT EXISTS prop_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER, game_pk INTEGER, date TEXT,
                player_id INTEGER, name TEXT,
                stat TEXT, line INTEGER, market TEXT,
                model_pct REAL, kalshi_cents REAL,
                recent_pct REAL, season_pct REAL,
                graded INTEGER DEFAULT 0, actual INTEGER,
                UNIQUE(game_pk, player_id, market)
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_prop_graded ON prop_log(graded)")
        # Additive migration: entry-time snapshot for closing-line-value (CLV).
        # log_prop refreshes price/model in place while ungraded, so without
        # these the entry read is lost by grading time.
        for col in ("entry_cents REAL", "entry_model_pct REAL", "entry_ts INTEGER",
                    # Which generation of the run model produced model_pct. A
                    # calibration is a correction for a SPECIFIC model's errors;
                    # applied to a different model it is just a bias. When the MLB
                    # run level was fixed (the one-sided home multiplier that
                    # inflated every total by 4%), the 1,738 rows already logged
                    # became evidence about a model that no longer exists -- and
                    # the Platt intercept fit on them, b=-0.36, would have been
                    # applied on top of the fix, double-correcting every prop.
                    # Stamping the version lets the fitter use only rows its own
                    # model produced. Rows predating this column read as version 0.
                    "model_version INTEGER"):
            try:
                c.execute(f"ALTER TABLE prop_log ADD COLUMN {col}")
            except Exception:
                pass                       # column already exists

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
def record_mlb_pick(game_pk, date, pick_side, pick_name, prob, price_cents, pred_total=None,
                    p_home_model=None, p_home_deep=None, prob_raw=None):
    """Store the model's pre-game pick (first time we see the game), including the
    two blend components (factor model / deep player engine, home-perspective) so
    each can be graded on its own.

    `prob` is the calibrated probability we show and bet; `prob_raw` is the same
    pick's UNCALIBRATED probability, kept so the calibrator fits its temperature on
    the raw model rather than on its own already-calibrated output."""
    with _lock, _conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO mlb_picks
               (game_pk, date, pick_side, pick_name, prob, price_cents, close_price, pred_total,
                p_home_model, p_home_deep, prob_raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (game_pk, date, pick_side, pick_name, prob, price_cents, price_cents, pred_total,
             p_home_model, p_home_deep, prob_raw if prob_raw is not None else prob),
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


def set_mlb_grade(game_pk, won, winner_name, actual_total=None, home_won=None):
    with _lock, _conn() as c:
        c.execute("UPDATE mlb_picks SET graded=1, won=?, winner_name=?, actual_total=?, home_won=? "
                  "WHERE game_pk=?",
                  (won, winner_name, actual_total, home_won, game_pk))


def deep_grades():
    """Graded games carrying BOTH blend components (home-perspective probs +
    whether home actually won) -- the evidence the blend-weight tuner runs on."""
    with _lock, _conn() as c:
        return [(r["p_home_model"], r["p_home_deep"], r["home_won"])
                for r in c.execute(
                    "SELECT p_home_model, p_home_deep, home_won FROM mlb_picks "
                    "WHERE graded=1 AND p_home_model IS NOT NULL "
                    "AND p_home_deep IS NOT NULL AND home_won IS NOT NULL").fetchall()]


def win_grade_pairs():
    """(RAW pick probability, won 0/1) for every graded game pick — the evidence the
    win-model calibrator fits its temperature on. Uses the uncalibrated prob so the
    fit isn't trained on its own calibrated output; legacy rows (prob_raw NULL) fall
    back to prob, which for pre-calibration history already IS the raw number.

    Carries the DATE for the same reason the prop pairs do: a slate's fifteen
    games share a day, so days are the unit that actually accumulates evidence."""
    with _lock, _conn() as c:
        return [(r["p"], r["won"], r["date"]) for r in c.execute(
            "SELECT COALESCE(prob_raw, prob) AS p, won, date FROM mlb_picks "
            "WHERE graded=1 AND won IS NOT NULL AND COALESCE(prob_raw, prob) IS NOT NULL").fetchall()]


def prop_grade_pairs():
    """(model probability 0-1, hit 0/1, date) for every graded batter prop — the
    evidence the prop calibrator fits its temperature on.

    The DATE rides along because these rows are not independent: one slate
    grades several hundred of them under a single day's weather, pitching and
    league-wide offence. Counting them as several hundred observations let four
    dates clear an 800-row floor and earn a full-strength correction."""
    with _lock, _conn() as c:
        return [(r["model_pct"] / 100.0, r["actual"], r["date"]) for r in c.execute(
            "SELECT model_pct, actual, date FROM prop_log "
            "WHERE graded=1 AND model_pct IS NOT NULL AND actual IS NOT NULL "
            "AND COALESCE(model_version, 0) = ?", (MODEL_VERSION,)).fetchall()]


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

    # Brier (lower=better; 0.25=coin flip) — on the CALIBRATED prob, i.e. how good
    # the numbers we actually show and bet are.
    bp = [p for p in graded if p["prob"] is not None]
    brier = round(sum((p["prob"] - (1 if p["won"] else 0)) ** 2 for p in bp) / len(bp), 4) if bp else None
    # Calibration buckets on the RAW (pre-calibration) prob — this is the overconfidence
    # the temperature is fit on and corrects, so the audit shows what's being fixed
    # (not the already-corrected output). Legacy rows fall back to prob.
    def _raw(p):
        pr = p.get("prob_raw")
        return pr if pr is not None else p.get("prob")
    braw = [p for p in graded if _raw(p) is not None]
    bins = []
    for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)):
        b = [p for p in braw if lo <= _raw(p) < hi]
        if b:
            bins.append({"range": f"{int(lo*100)}-{int(min(hi,1)*100)}%",
                         "n": len(b),
                         "predicted": round(100 * sum(_raw(p) for p in b) / len(b), 1),
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
    # Model split: grade the two blend components (and the blend) separately on
    # home-perspective probabilities, so the deep engine earns its weight on
    # evidence rather than assumption.
    def _mstats(rows, key):
        rows = [r for r in rows if r.get(key) is not None and r.get("home_won") is not None]
        if not rows:
            return None
        acc = sum(1 for r in rows if (r[key] >= 0.5) == (r["home_won"] >= 0.5)) / len(rows)
        br = sum((r[key] - r["home_won"]) ** 2 for r in rows) / len(rows)
        return {"n": len(rows), "acc_pct": round(100 * acc, 1), "brier": round(br, 4)}
    blended = []
    for p in graded:
        if p.get("home_won") is not None and p.get("prob") is not None:
            ph = p["prob"] if p.get("pick_side") == "home" else 1 - p["prob"]
            blended.append({"p": ph, "home_won": p["home_won"]})
    model_split = {"factor": _mstats(graded, "p_home_model"),
                   "deep": _mstats(graded, "p_home_deep"),
                   "blend": _mstats(blended, "p")}
    try:
        import calibrate
        cal_temps = calibrate.temps()
    except Exception:
        cal_temps = None
    return {
        "graded": n, "pending": pending, "wins": wins, "losses": n - wins,
        "model_split": model_split, "calibration_temps": cal_temps,
        "accuracy_pct": round(100 * wins / n, 1) if n else None,
        "roi_pct": roi_pct, "roi_bets": len(priced),
        "roi_edge_pct": roi_edge_pct, "edge_bets": edge_bets, "edge_threshold": EDGE,
        "clv_avg": clv_avg, "clv_positive_pct": clv_pos_pct, "clv_n": len(clv),
        "totals_accuracy": totals_acc,
        "brier": brier, "brier_baseline": 0.25, "calibration": bins,
    }


# ---- Player-prop log (model vs Kalshi vs recent form, graded) -------------
# Generation of the MLB run model. BUMP THIS whenever a change alters the level
# or shape of the numbers written to prop_log — a new park/weather term, a
# different run baseline, a fix like the one-sided home multiplier. Calibration is
# fit per version, so a bump retires the old evidence instead of letting a
# correction for yesterday's model be applied to today's.
#
#   1  original
#   2  home-field applied geometrically (was one-sided, inflating totals 4%)
MODEL_VERSION = 2


def log_prop(game_pk, date, player_id, name, stat, line, market,
             model_pct, kalshi_cents, recent_pct, season_pct):
    """Record (or refresh, while still ungraded) one batter prop observation."""
    now = int(time.time())
    with _lock, _conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO prop_log
               (ts, game_pk, date, player_id, name, stat, line, market,
                model_pct, kalshi_cents, recent_pct, season_pct,
                entry_cents, entry_model_pct, entry_ts, model_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (now, game_pk, date, player_id, name, stat, line, market,
             model_pct, kalshi_cents, recent_pct, season_pct,
             kalshi_cents, model_pct, now, MODEL_VERSION),
        )
        # Keep the latest pre-game read (price/model/form drift) until it grades —
        # that becomes the CLOSING read. The entry_* snapshot is frozen at first
        # sight (backfilled once if the market had no quote when first logged).
        c.execute(
            """UPDATE prop_log SET kalshi_cents=?, model_pct=?, recent_pct=?, season_pct=?,
                   model_version=?,
                   entry_model_pct=COALESCE(entry_model_pct, ?),
                   entry_ts=CASE WHEN entry_cents IS NULL THEN ? ELSE entry_ts END,
                   entry_cents=COALESCE(entry_cents, ?)
               WHERE game_pk=? AND player_id=? AND market=? AND graded=0""",
            (kalshi_cents, model_pct, recent_pct, season_pct, MODEL_VERSION,
             model_pct, now, kalshi_cents, game_pk, player_id, market),
        )


def ungraded_props():
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM prop_log WHERE graded=0").fetchall()]


def grade_prop(prop_id, actual):
    with _lock, _conn() as c:
        c.execute("UPDATE prop_log SET graded=1, actual=? WHERE id=?", (actual, prop_id))


def grade_prop_void(prop_id):
    """Scratched / didn't play: Kalshi and DK both void the leg, so it must not
    count as a loss. graded=2 removes it from the pending queue AND from the
    accuracy math (every report filters on graded=1)."""
    with _lock, _conn() as c:
        c.execute("UPDATE prop_log SET graded=2 WHERE id=?", (prop_id,))


def prop_report(min_edge=8.0):
    """Aggregate accuracy of the prop model, recent-form, and Kalshi's price, plus
    the realized ROI of betting the edges each flags. One night is noise; this is
    the honest read once enough props have graded."""
    with _lock, _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM prop_log WHERE graded=1").fetchall()]
        pending = c.execute("SELECT COUNT(*) n FROM prop_log WHERE graded=0").fetchone()["n"]
    n = len(rows)

    def brier(key):
        b = [r for r in rows if r.get(key) is not None]
        if not b:
            return None, 0
        return round(sum(((r[key] / 100.0) - r["actual"]) ** 2 for r in b) / len(b), 4), len(b)

    model_brier, model_n = brier("model_pct")
    recent_brier, recent_n = brier("recent_pct")
    # Kalshi's own price as a forecast (the line to beat).
    market_brier, market_n = brier("kalshi_cents")

    # Model calibration: predicted vs actual hit-rate by probability bucket.
    bp = [r for r in rows if r["model_pct"] is not None]
    bins = []
    for lo, hi in ((0, 20), (20, 40), (40, 60), (60, 80), (80, 101)):
        b = [r for r in bp if lo <= r["model_pct"] < hi]
        if b:
            bins.append({"range": f"{lo}-{min(hi, 100)}%", "n": len(b),
                         "predicted": round(sum(r["model_pct"] for r in b) / len(b), 1),
                         "actual": round(100 * sum(r["actual"] for r in b) / len(b), 1)})

    # ROI of following an edge signal: one contract per logged market with a price,
    # filled at the recorded Kalshi price, settled by the real box score. The NO
    # cost is approximated as 100-yes (we only store the YES ask).
    def roi_for(signal):
        bets = []
        for r in rows:
            if r["kalshi_cents"] is None:
                continue
            side, cost = signal(r)
            if side is None or not cost or cost <= 0 or cost >= 100:
                continue
            won = (r["actual"] == 1) if side == "YES" else (r["actual"] == 0)
            bets.append((cost, won))
        if not bets:
            return None
        staked = sum(c for c, _ in bets)
        pnl = sum((100 if w else 0) - c for c, w in bets)
        wins = sum(1 for _, w in bets if w)
        return {"bets": len(bets), "win_pct": round(100 * wins / len(bets), 1),
                "roi_pct": round(100 * pnl / staked, 1) if staked else None,
                "pnl_per_contract_c": round(pnl / len(bets), 1)}

    def edge_sig(key):
        def sig(r):
            if r.get(key) is None:
                return None, None
            yc = r["kalshi_cents"]
            ey = r[key] - yc                 # edge on YES
            if ey >= min_edge:
                return "YES", yc
            if -ey >= min_edge:              # the model/form is well below the price -> fade
                return "NO", 100 - yc
            return None, None
        return sig

    return {
        "graded": n, "pending": pending, "min_edge": min_edge,
        "hit_rate_pct": round(100 * sum(r["actual"] for r in rows) / n, 1) if n else None,
        "model_brier": model_brier, "recent_brier": recent_brier,
        "market_brier": market_brier, "brier_n": model_n,
        "calibration": bins,
        "model_edge_roi": roi_for(edge_sig("model_pct")),
        "recent_edge_roi": roi_for(edge_sig("recent_pct")),
        "clv": clv_report(min_edge),
    }


def clv_report(min_edge=8.0):
    """Closing-line value: for every prop the model flagged at ENTRY (its first
    logged read), did the market close closer to our number? Beating the close
    consistently is the gold-standard proof of edge — it isolates model skill
    from short-run win/loss variance. Buying YES: CLV = close − entry (the
    market came up to us). Fading (NO): CLV = entry − close."""
    with _lock, _conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT market, entry_cents, entry_model_pct, kalshi_cents, actual, entry_ts, ts
               FROM prop_log WHERE graded=1
                 AND entry_cents IS NOT NULL AND kalshi_cents IS NOT NULL
                 AND entry_model_pct IS NOT NULL""").fetchall()]
    picks = []
    for r in rows:
        ey = r["entry_model_pct"] - r["entry_cents"]     # entry-time edge on YES
        if ey >= min_edge:
            side, clv = "YES", r["kalshi_cents"] - r["entry_cents"]
        elif -ey >= min_edge:
            side, clv = "NO", r["entry_cents"] - r["kalshi_cents"]
        else:
            continue
        # A single 10-min snapshot window can't move; require some time between
        # entry and close so "CLV 0" means the market held, not that we only
        # ever saw one quote.
        if (r["ts"] or 0) - (r["entry_ts"] or 0) < 900:
            continue
        picks.append({"side": side, "clv": clv, "market": r["market"],
                      "won": bool(r["actual"]) if side == "YES" else not r["actual"]})
    if not picks:
        return {"picks": 0}
    n = len(picks)
    avg = sum(p["clv"] for p in picks) / n
    beat = sum(1 for p in picks if p["clv"] > 0)
    push = sum(1 for p in picks if p["clv"] == 0)
    by_mkt = {}
    for p in picks:
        m = by_mkt.setdefault(p["market"], {"n": 0, "clv_sum": 0.0, "beat": 0})
        m["n"] += 1; m["clv_sum"] += p["clv"]; m["beat"] += 1 if p["clv"] > 0 else 0
    for m in by_mkt.values():
        m["avg_clv"] = round(m["clv_sum"] / m["n"], 1)
        del m["clv_sum"]
    return {"picks": n, "avg_clv_cents": round(avg, 2),
            "beat_close_pct": round(100 * beat / n, 1),
            "push_pct": round(100 * push / n, 1),
            "win_pct": round(100 * sum(p["won"] for p in picks) / n, 1),
            "by_market": by_mkt}


def prop_hits(date=None, predicted_min=55.0, risky_max_cents=42.0):
    """Showcase from graded props: which ones the model liked actually hit, and
    which longshots cashed.

    - 'predicted': props the app would lean YES on (model >= predicted_min) that
      have graded, each marked hit/miss, with a summary hit-rate (honest record).
    - 'risky': props that HIT but were market longshots (YES priced cheap, big
      payout) -- the "you'd have won big" board, ranked by payout.

    `date` (YYYY-MM-DD) filters to one slate; omit for all-time."""
    where, params = "graded=1", []
    if date:
        where += " AND date=?"; params.append(date)
    with _lock, _conn() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT * FROM prop_log WHERE {where}", params).fetchall()]

    def payout(cents):
        return round(100.0 / cents, 2) if cents and cents > 0 else None

    def view(r):
        c_ = r.get("kalshi_cents")
        return {"name": r["name"], "stat": r["stat"], "line": r["line"],
                "label": f"{r['name']} {r['line']}+ {('HR' if r['stat']=='hr' else 'hits')}",
                "model_pct": r.get("model_pct"),
                "market_pct": round(c_, 1) if c_ is not None else None,
                "edge": (round(r["model_pct"] - c_, 1)
                         if r.get("model_pct") is not None and c_ is not None else None),
                "payout_x": payout(c_), "hit": bool(r.get("actual") == 1),
                "date": r.get("date")}

    predicted = sorted((view(r) for r in rows
                        if r.get("model_pct") is not None and r["model_pct"] >= predicted_min),
                       key=lambda v: (v["hit"], v["model_pct"] or 0), reverse=True)
    hit_n = sum(1 for p in predicted if p["hit"])
    risky = sorted((view(r) for r in rows
                    if r.get("actual") == 1 and r.get("kalshi_cents") is not None
                    and r["kalshi_cents"] <= risky_max_cents),
                   key=lambda v: (v["payout_x"] or 0), reverse=True)
    return {
        "date": date, "graded_n": len(rows),
        "predicted": predicted,
        "predicted_summary": {"recommended": len(predicted), "hit": hit_n,
                              "hit_pct": round(100 * hit_n / len(predicted), 1) if predicted else None},
        "risky": risky,
    }


def prop_hit_combos(date=None, predicted_min=55.0, longshot_max_cents=42.0):
    """Hindsight COMBOS from graded props (single props are lame): for each slate,
    the combo of model-liked props that all cashed, and the longshot moonshot —
    cheap props that all hit, the few-dollars-to-few-thousand parlay. One leg per
    game so legs are independent and the multiplied payout is honest."""
    where, params = "graded=1 AND actual=1 AND kalshi_cents IS NOT NULL", []
    if date:
        where += " AND date=?"; params.append(date)
    with _lock, _conn() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT * FROM prop_log WHERE {where}", params).fetchall()]
        graded_n = c.execute(
            "SELECT COUNT(*) n FROM prop_log WHERE graded=1" + (" AND date=?" if date else ""),
            ([date] if date else [])).fetchone()["n"]

    def leg(r):
        c_ = r["kalshi_cents"]
        unit = {"hr": "HR", "ks": "Ks"}.get(r["stat"], "hits")
        return {"pick": f"{r['name']} {r['line']}+ {unit}",
                "type": "KS" if r["stat"] == "ks" else ("HR" if r["stat"] == "hr" else "HIT"),
                "matchup": None, "live": False, "game_pk": r["game_pk"],
                "prob_pct": r.get("model_pct"), "price_cents": round(c_, 1),
                "payout_x": round(100.0 / c_, 2) if c_ > 0 else None, "date": r["date"]}

    def combo(legs):
        """Pack a list of one-per-game legs into a combo payload (multiplied)."""
        prob = pay = 1.0
        for l in legs:
            if l["prob_pct"] is not None:
                prob *= l["prob_pct"] / 100.0
            pay *= (l["payout_x"] or 1.0)
        return {"legs": legs, "n_legs": len(legs),
                "combined_prob_pct": round(prob * 100, 2) if prob else None,
                "parlay_payout_x": round(pay, 1), "fair_payout_x": round(pay, 1),
                "ret_5": round(5 * pay), "ret_10": round(10 * pay),
                "date": legs[0]["date"]}

    def one_per_game(cands):
        used, out = set(), []
        for r in cands:
            if r["game_pk"] in used:
                continue
            used.add(r["game_pk"]); out.append(leg(r))
        return out

    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)

    predicted_combos, moonshots = [], []
    for d, rs in by_date.items():
        liked = sorted((r for r in rs if r.get("model_pct") and r["model_pct"] >= predicted_min),
                       key=lambda r: r["model_pct"], reverse=True)
        legs = one_per_game(liked)[:4]
        if len(legs) >= 2:
            predicted_combos.append(combo(legs))
        longs = sorted((r for r in rs if r["kalshi_cents"] <= longshot_max_cents),
                       key=lambda r: r["kalshi_cents"])
        mlegs = one_per_game(longs)[:6]
        if len(mlegs) >= 2:
            moonshots.append(combo(mlegs))

    predicted_combos.sort(key=lambda c: c["parlay_payout_x"], reverse=True)
    moonshots.sort(key=lambda c: c["parlay_payout_x"], reverse=True)
    return {
        "date": date, "graded_n": graded_n,
        "predicted_combos": predicted_combos[:4],
        "moonshot": moonshots[0] if moonshots else None,
        "predicted_summary": prop_hits(date)["predicted_summary"],
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
