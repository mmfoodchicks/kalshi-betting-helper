"""Universal prediction logger — the missing piece for site-wide calibration.

MLB has graded prediction-vs-outcome history (game picks, prop log), so it
calibrates. Tennis, UFC, racing, NFL and LoL didn't, so they couldn't. This module fixes that: it captures each sim's Kalshi
prediction the first time it's priced, and when that market settles it reads the
real outcome and grades it. calibrate.py then reads pairs(model) and fits a
temperature exactly as it does for MLB — no new calibration code needed.

Design:
  - one row per (model, ticker), logged the first time we see it (INSERT OR
    IGNORE), storing the model's YES probability for that market;
  - a cheap background resolver reads each settled ticker's result and grades it;
  - harvesting piggybacks on the sims' already-cached boards, so it adds ~no
    compute (a warm board is a cache hit).

Nothing here changes a prediction — it only records and grades, so calibration
accrues honestly from real outcomes.
"""
import os
import re as _re
import sqlite3
import threading
import time
import errlog

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
            resolved_ts INTEGER,
            mkt REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_pred_model ON predictions(model, graded)")
        # `mkt` is the de-vigged market probability at the moment we logged our
        # own. Without it the graded record can only say whether WE were
        # calibrated, never whether we beat the price -- and for the ITF matches
        # that make up most of the tennis board there is no other benchmark:
        # bookmakers do not price ITF, and Kalshi's settled markets report a
        # post-resolution last price (0.99), so a close cannot be recovered after
        # the fact. It has to be captured live or not at all. Added later, so
        # tolerate an older table that predates it.
        cols = {r[1] for r in c.execute("PRAGMA table_info(predictions)")}
        if "settle_value" not in cols:          # scalar settlements (2026-09-10)
            try:
                c.execute("ALTER TABLE predictions ADD COLUMN settle_value REAL")
            except Exception as _e:
                errlog.note("PL-init-settle", _e)   # a sibling worker won the race
        # `event_ts`: the event start the LOGGER knew (an NFL kickoff from
        # the schedule). Day-only tickers resolve to midnight otherwise,
        # and the closing-line snapshot stopped a day early for football.
        if "event_ts" not in cols:
            try:                   # several workers migrate at once on boot
                c.execute("ALTER TABLE predictions ADD COLUMN event_ts INTEGER")
            except Exception as _e:
                errlog.note("PRED-init_db-evt", _e)  # a sibling just added it
        if "mkt" not in cols:
            try:                   # several workers migrate at once on boot
                c.execute("ALTER TABLE predictions ADD COLUMN mkt REAL")
            except Exception as _e:
                errlog.note("PRED-init_db", _e)  # a sibling worker just added it
        # `close_mkt` is the last PRE-EVENT market price (see snapshot_closes):
        # the closing line, the fastest honest benchmark a pick can be graded
        # against -- it accrues per event instead of waiting out settlement.
        if "close_mkt" not in cols:
            try:
                c.execute("ALTER TABLE predictions ADD COLUMN close_mkt REAL")
                c.execute("ALTER TABLE predictions ADD COLUMN close_mkt_ts INTEGER")
            except Exception as _e:
                errlog.note("PRED-init_db-2", _e)
        # Repair: NFL exhibitions logged before the nfl_pre split carried
        # model='nfl', and first-write-wins means the corrected router could
        # never re-file them — so August games would grade into the REGULAR
        # SEASON calibration bucket, the exact contamination the split exists
        # to prevent. An NFL game ticker with an AUG date is always an
        # exhibition (the regular season has never opened before September),
        # so the re-file is safe to run every boot; it's a no-op once clean.
        c.execute("UPDATE predictions SET model='nfl_pre' "
                  "WHERE model='nfl' AND ticker LIKE 'KXNFLGAME-%' "
                  "AND substr(ticker, 13, 3) = 'AUG'")
        # SHADOW predictions: a second model's forecast of a market another model
        # already owns. `predictions` is keyed on the ticker alone (first write
        # wins, which is what keeps a forecast honest), so a second model's
        # log_many on the same tickers was silently ignored -- the NFL deep
        # engine's "own graded record" (bucket nfl_deep, 2026-10-16) recorded
        # nothing at all. Keyed (ticker, model), first write wins per model,
        # graded off the OWNING row's settlement (resolve_due grades tickers,
        # not models), read by pairs() / vs_market() like any bucket.
        c.execute("""CREATE TABLE IF NOT EXISTS shadow (
            ticker TEXT, model TEXT, prob REAL, ts INTEGER, mkt REAL,
            PRIMARY KEY (ticker, model))""")


def log_many(model, rows):
    """rows: iterable of (ticker, prob 0-1, close_time_epoch|None[, mkt 0-1
    [, event_ts_epoch]]). The optional fifth element is the event START as
    the logger knows it (an NFL kickoff); it anchors the closing-line
    snapshot where the ticker alone carries only the day.
    One row per ticker ever (the first, pre-settlement prediction) — later
    re-prices are ignored so we grade the genuine forecast, not a near-settled
    one. The optional fourth element is the market's own probability at that
    moment, which is what lets the record answer "did we beat the price"."""
    clean = []
    for row in rows:
        tk, p, ct = row[0], row[1], row[2]
        mk = row[3] if len(row) > 3 else None
        ev = row[4] if len(row) > 4 else None
        if not tk or p is None:
            continue
        try:
            p = float(p)
        except (TypeError, ValueError):
            continue
        if not (0.0 < p < 1.0):
            continue
        try:
            mk = float(mk) if mk is not None else None
        except (TypeError, ValueError):
            mk = None
        if mk is not None and not (0.0 < mk < 1.0):
            mk = None
        try:
            ev = int(ev) if ev else None
        except (TypeError, ValueError):
            ev = None
        clean.append((tk, model, p, int(ct) if ct else None, int(time.time()),
                      mk, ev))
    if not clean:
        return 0
    with _lock, _conn() as c:
        c.executemany(
            "INSERT OR IGNORE INTO predictions "
            "(ticker, model, prob, close_time, ts, mkt, event_ts) "
            "VALUES (?,?,?,?,?,?,?)", clean)
        # The PREDICTION is first-write-wins forever — that is the genuine
        # forecast, and later re-prices must never touch it. The market column
        # is a benchmark, not a forecast: a row logged before its book was
        # quoted (or before this column existed) carries NULL, and leaving it
        # NULL forever just means the sport can never answer "did we beat the
        # price". Backfill it once, while the prediction is still ungraded.
        c.executemany(
            "UPDATE predictions SET mkt=? WHERE ticker=? AND graded=0 AND mkt IS NULL",
            [(row[5], row[0]) for row in clean if row[5] is not None])
    return len(clean)


def log_shadow(model, rows):
    """Shadow forecasts for markets another model owns in `predictions` (see
    init_db). rows: (ticker, prob 0-1, close_time|None[, mkt 0-1[, event_ts]])
    -- log_many's shape. Returns how many rows were offered."""
    clean = []
    now = int(time.time())
    for row in rows:
        tk, p = row[0], row[1]
        mk = row[3] if len(row) > 3 else None
        try:
            p = float(p)
            mk = float(mk) if mk is not None else None
        except (TypeError, ValueError):
            continue
        if not tk or not (0.0 < p < 1.0):
            continue
        if mk is not None and not (0.0 < mk < 1.0):
            mk = None
        clean.append((tk, model, p, now, mk))
    if not clean:
        return 0
    with _lock, _conn() as c:
        c.executemany("INSERT OR IGNORE INTO shadow (ticker, model, prob, ts, mkt) VALUES (?,?,?,?,?)", clean)
    return len(clean)


def log(model, ticker, prob, close_time=None, mkt=None):
    return log_many(model, [(ticker, prob, close_time, mkt)])


def devig(own_cents, opp_cents):
    """The market's own probability for a side, with the overround stripped:
    own/(own+opp), as a 0-1 float, or None when either side is unquoted.

    This is the number to pass as `mkt`. The raw ask overstates both sides at
    once (a 106c book says every team is better than it is), so grading against
    it would flatter the model by exactly the vig. Grading against the de-vigged
    price asks the only question that matters: did we know something the market,
    fairly stated, did not."""
    try:
        a, b = float(own_cents), float(opp_cents)
    except (TypeError, ValueError):
        return None
    if a <= 0 or b <= 0:
        return None
    return a / (a + b)


def iso_ts(s):
    """ESPN's start time ('2026-10-29T23:30Z') as an epoch, or None."""
    import kalshi
    try:
        return kalshi._parse_time(s) if s else None
    except (ValueError, TypeError):
        return None


# ---- Carried predictions: the PC's builds reach the record ------------------
# Six boards log inside their builders (basket, hockey, nfl_game_sim, cfb_board,
# soccer; golf). When the PC builds them, log_many writes the PC's own
# predlog.db, which nothing grades -- predlog.start runs only in app.py -- and
# since 19cf29c the server serves the PC's copy instead of rebuilding, so the
# server's record for those sports grew only when it built as the fallback
# (found 2026-10-29, Fable's review agreed). Each builder now also CARRIES the
# rows it logs beside its board (`carry`), the boards sync ships them like any
# board, and the server logs them from the copy it holds (_harvest_carried):
# one mechanism, the boards' own door, no PC-side bookkeeping. First write wins
# as everywhere here, so a row the server already logged from its own build is
# a no-op. Only rows whose event START is known and still ahead are logged: the
# carry lands minutes after its build, and a forecast filed after kickoff is
# not a pre-game forecast.
CARRY_MODELS = {"nba": False, "wnba": False, "nba_deep": True, "nhl": False, "nhl_deep": True,
                "nfl": False, "nfl_pre": False, "nfl_deep": True, "cfb": False, "cfb_ats": False,
                "cfb_total": False, "soccer": False, "soccer_dk": True, "soccer_btts": False}   # model -> shadow?
CARRY_PREFIX = "plcarry_"
_TICKER_OK = _re.compile(r"^KX[A-Z0-9][A-Z0-9._-]{3,90}$")


def carry(key, groups):
    """Publish a builder's prediction rows beside its board for the server to
    log (see above). `groups`: [(model, rows)], rows in log_many's shape with
    the event start as the fifth element. Never raises; returns rows carried."""
    try:
        import boardshare
        g = [[m, [list(r) for r in rows]] for m, rows in groups if rows and m in CARRY_MODELS]
        if not g:
            return 0
        boardshare.put(CARRY_PREFIX + str(key), {"ts": time.time(), "groups": g})
        return sum(len(r) for _m, r in g)
    except Exception as e:
        errlog.note("PRED-carry", e, path=str(key))
        return 0


def _carry_row(r, now):
    """A carried row fit to log: a Kalshi ticker, 0 < p < 1, a known start
    still ahead. Returns log_many's 5-tuple or None."""
    if not isinstance(r, (list, tuple)) or len(r) < 5:
        return None
    tk, p, ct, mk, ev = r[0], r[1], r[2], r[3], r[4]
    if not isinstance(tk, str) or not _TICKER_OK.match(tk):
        return None
    try:
        p = float(p)
        ev = float(ev)
    except (TypeError, ValueError):
        return None
    if not (0.0 < p < 1.0) or ev <= now:
        return None
    return (tk, p, ct, mk, int(ev))


_carry_seen = {}          # carry name -> the file version already logged


def _harvest_carried():
    """Log every carried file version not yet logged (harvest() runs it with the
    other harvesters). A file that fails is ledgered and retried next pass."""
    import boardshare
    now = time.time()
    total = 0
    listed = boardshare.names(CARRY_PREFIX)
    for name in [n for n in _carry_seen if n not in dict(listed)]:
        _carry_seen.pop(name, None)            # swept from disk: the memo follows the listing
    for name, mt in listed:
        if _carry_seen.get(name) == mt:
            continue
        try:
            d, _age = boardshare.get(name, 3 * 86400)
            for model, rows in (d or {}).get("groups") or []:
                if model not in CARRY_MODELS:
                    continue
                ok = [x for x in (_carry_row(r, now) for r in rows or []) if x]
                if ok:
                    total += (log_shadow if CARRY_MODELS[model] else log_many)(model, ok)
        except Exception as e:
            errlog.note("PRED-carry-log", e, path=name)
            continue
        _carry_seen[name] = mt
    return total


_TICKER_MON = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
               "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
_EVT_RE = _re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{4})?")


def _event_ts(ticker):
    """Event START encoded in the ticker itself, as an epoch, or None.

    Kalshi's close_time CANNOT anchor anything pre-game: it is an
    administrative backstop set WEEKS after the event (a Sep 2 fight
    'closes' Sep 17) and trading runs in-game, so a price taken near
    close_time has the outcome inside it. The ticker is the honest source:
    KXUFCFIGHT-26SEP02RIVDAR encodes the event day, and game series
    (KXMLBGAME-26AUG312138NYYLAA) carry the start HHMM in ET. Day-only
    tickers resolve to MIDNIGHT ET of the event day -- snapshots stop the
    night before, trading a few hours of line movement for zero risk of
    in-game contamination."""
    m = _EVT_RE.search(ticker or "")
    if not m:
        return None
    yy, mon, dd, hhmm = m.groups()
    month = _TICKER_MON.get(mon)
    if not month:
        return None
    hour = minute = 0
    if hhmm and int(hhmm[:2]) < 24 and int(hhmm[2:]) < 60:
        hour, minute = int(hhmm[:2]), int(hhmm[2:])
    import datetime
    import zoneinfo
    try:
        dt = datetime.datetime(2000 + int(yy), month, int(dd), hour, minute,
                               tzinfo=zoneinfo.ZoneInfo("America/New_York"))
    except ValueError:
        return None
    return dt.timestamp()


def snapshot_closes(limit=40):
    """Refresh each open prediction's pre-event market price; the last write
    before the event starts IS the closing line.

    Runs every loop pass over ungraded rows whose event is inside the next
    36 hours, nearest first, capped per pass to be polite to Kalshi. The
    snapshot is the yes mid (the ask alone when nobody bids); the log-time
    `mkt` is a two-sided de-vig, so the two conventions differ by half the
    vig split -- fine for the movement question, which reads direction and
    size of the drift, not absolute level."""
    import kalshi
    now = time.time()
    with _lock, _conn() as c:
        rows = [(r["ticker"], r["event_ts"]) for r in c.execute(
            "SELECT ticker, event_ts FROM predictions WHERE graded=0").fetchall()]
    due = []
    for tk, logged_ev in rows:
        ev = logged_ev or _event_ts(tk)
        if ev and now < ev and ev - now < 36 * 3600:
            due.append((ev, tk))
    due.sort()
    n = 0
    for _ev, tk in due[:limit]:
        try:
            m = kalshi.get_market(tk)
        except Exception:
            continue                     # closed/404/blink -- next pass retries
        yb, ya = m.get("yes_bid"), m.get("yes_ask")
        px = (yb + ya) / 2.0 if yb and ya else ya
        if px and 0 < px < 100:
            with _lock, _conn() as c:
                c.execute("UPDATE predictions SET close_mkt=?, close_mkt_ts=? "
                          "WHERE ticker=? AND graded=0", (px / 100.0, int(now), tk))
            n += 1
    return n


def prices_for(tickers):
    """{ticker: P(YES)} as predlog last saw each ticker's market PRE-GAME: the
    closing snapshot (close_mkt, the YES mid before the start) when one was
    taken, else the de-vigged price at log time. Tickers without a row, or
    without a price, are absent. Chunked IN queries: a book's own tickers,
    never the table."""
    out = {}
    tks = [t for t in dict.fromkeys(tickers or ()) if isinstance(t, str) and t]
    with _lock, _conn() as c:
        for i in range(0, len(tks), 500):
            chunk = tks[i:i + 500]
            marks = ",".join("?" for _ in chunk)
            for r in c.execute(f"SELECT ticker, mkt, close_mkt FROM predictions WHERE ticker IN ({marks})", chunk):
                p = r["close_mkt"] if r["close_mkt"] is not None else r["mkt"]
                if p is not None and 0.0 < p < 1.0:
                    out[r["ticker"]] = float(p)
    return out


def close_report(model):
    """Does the CLOSING line move toward this model's numbers?

    The fastest honest verdict a model can earn: settlement grading needs
    weeks of coin flips, but every pick where we disagreed with the price
    gets a close-movement grade the moment its event starts. Read it like a
    sharp reads beat-the-close: toward_pct over 50 with positive capture
    means the market's own information flow keeps agreeing with us late;
    under 50 means our disagreements are noise the market never validates."""
    with _lock, _conn() as c:
        rows = [(r["prob"], r["mkt"], r["close_mkt"]) for r in c.execute(
            "SELECT prob, mkt, close_mkt FROM predictions WHERE model=? "
            "AND mkt IS NOT NULL AND close_mkt IS NOT NULL", (model,)).fetchall()]
    live = [(p, m, cm) for p, m, cm in rows if abs(p - m) > 0.005]
    if len(live) < 10:
        return {"n": len(live), "ready": False}
    toward = sum(1 for p, m, cm in live if (cm - m) * (p - m) > 0)
    flat = sum(1 for p, m, cm in live if abs(cm - m) <= 0.002)
    cap = sum((cm - m) * (1 if p > m else -1) for p, m, cm in live) / len(live)
    return {"n": len(live), "ready": True,
            "toward_pct": round(100.0 * toward / len(live), 1),
            "flat_pct": round(100.0 * flat / len(live), 1),
            "avg_capture_c": round(cap * 100, 2)}


def pairs(model):
    """(prob, outcome 0/1, day) for every graded prediction of a model — the
    evidence calibrate.py fits its temperature on.

    The DAY (from the market's close time) rides along for calibrate's
    day-floor damping: rows that settle on one slate share that slate's
    conditions and are far from independent, so a correction has to be earned
    across days, not rows. calibrate._fit accepts bare (prob, outcome) too, so
    older callers are unaffected."""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT prob, outcome, close_time FROM predictions "
            "WHERE model=? AND graded=1 AND outcome IS NOT NULL",
            (model,)).fetchall()
        # a shadow bucket's rows, graded off the owning row's settlement
        rows += c.execute(
            "SELECT s.prob AS prob, p.outcome AS outcome, p.close_time AS close_time "
            "FROM shadow s JOIN predictions p ON p.ticker = s.ticker "
            "WHERE s.model=? AND p.graded=1 AND p.outcome IS NOT NULL",
            (model,)).fetchall()
        return [(r["prob"], float(r["outcome"]),
                 time.strftime("%Y-%m-%d", time.gmtime(r["close_time"]))
                 if r["close_time"] else None)
                for r in rows]


def vs_market(model):
    """Model against the price it was logged beside, on graded predictions.

    This is the benchmark ITF otherwise has no way to get. Returns None until
    enough has settled to say anything; `n` is small at first and grows daily.

    Read it the way the odds benchmark is read: `edge` positive means our number
    carried information the price did not."""
    import math
    with _lock, _conn() as c:
        rows = [(r["prob"], r["mkt"], float(r["outcome"])) for r in c.execute(
            "SELECT prob, mkt, outcome FROM predictions "
            "WHERE model=? AND graded=1 AND outcome IS NOT NULL AND mkt IS NOT NULL",
            (model,)).fetchall()]
        rows += [(r["prob"], r["mkt"], float(r["outcome"])) for r in c.execute(
            "SELECT s.prob AS prob, COALESCE(s.mkt, p.mkt) AS mkt, p.outcome AS outcome "
            "FROM shadow s JOIN predictions p ON p.ticker = s.ticker "
            "WHERE s.model=? AND p.graded=1 AND p.outcome IS NOT NULL "
            "AND COALESCE(s.mkt, p.mkt) IS NOT NULL", (model,)).fetchall()]
    if len(rows) < 30:
        return {"n": len(rows), "ready": False}

    def ll(get):
        s = 0.0
        for p, m, y in rows:
            q = min(0.999, max(0.001, get(p, m)))
            s += -math.log(q if y else 1 - q)
        return s / len(rows)

    ours, mkt = ll(lambda p, m: p), ll(lambda p, m: m)
    return {"n": len(rows), "ready": True,
            "model_logloss": round(ours, 4), "market_logloss": round(mkt, 4),
            "edge": round(mkt - ours, 4),
            "model_brier": round(sum((p - y) ** 2 for p, m, y in rows) / len(rows), 4),
            "market_brier": round(sum((m - y) ** 2 for p, m, y in rows) / len(rows), 4),
            "beats_market": ours < mkt}


def results(tickers):
    """{ticker: {"graded": 0/1/2, "outcome": 0/1/None}} for the tickers
    asked about -- how a ledger that files a pick as a Kalshi ticket (the
    college ATS and total picks, cfb_track) reads its settlement."""
    tks = [t for t in tickers if t]
    out = {}
    if not tks:
        return out
    with _lock, _conn() as c:
        for i in range(0, len(tks), 400):
            chunk = tks[i:i + 400]
            qs = ",".join("?" * len(chunk))
            for r in c.execute("SELECT ticker, graded, outcome FROM predictions "
                               f"WHERE ticker IN ({qs})", chunk).fetchall():
                out[r["ticker"]] = {"graded": r["graded"], "outcome": r["outcome"]}
    return out


def _mark(ticker, graded, outcome=None, resolved_ts=None, settle_value=None):
    """graded 1 = yes/no and scored, 2 = dead (void / gone), 3 = settled at a
    scalar VALUE (settle_value, cents): kept as money, excluded from the
    binary record rather than filed as a scratch."""
    with _lock, _conn() as c:
        c.execute("UPDATE predictions SET graded=?, outcome=?, resolved_ts=?, settle_value=? "
                  "WHERE ticker=?",
                  (graded, outcome, resolved_ts, settle_value, ticker))


def resolve_due(limit=150):
    """Grade ungraded predictions whose market has settled. A settled Kalshi
    market carries a decided `result` ('yes'/'no') — that, not its status string,
    is the grade (Kalshi reports settled markets as 'finalized', not 'settled').
    Tickers Kalshi no longer serves (404) or that settle void are marked dead
    (graded=2) so they can't wedge the front of the capped queue forever."""
    import kalshi
    import urllib.error
    now = int(time.time())
    with _lock, _conn() as c:
        due = [r["ticker"] for r in c.execute(
            "SELECT ticker FROM predictions WHERE graded=0 "
            "AND (close_time IS NULL OR close_time <= ?) "
            "ORDER BY close_time IS NULL, close_time LIMIT ?", (now, limit)).fetchall()]
        # EARLY PROBE for game markets. Kalshi closes MLB/NFL markets on a
        # BACKSTOP long after first pitch (72h on game/most prop series, 48h on
        # RBI) -- a legal formality, not the settlement time. Waiting for
        # close_time graded a Tuesday game on Friday, which starved every
        # calibration that feeds on these rows (2,198 prop forecasts sat at
        # zero graded while their markets were settled). Two signals gate the
        # probe, and BOTH must hold, because neither alone is safe:
        #   close_time - 66h <= now  : within 66h of close. On a 72h backstop
        #     that is first pitch + 6h; on RBI's 48h backstop it would open 18
        #     HOURS BEFORE first pitch, which is what the second signal stops.
        #   ts + 6h <= now           : rows are logged pregame on slate day, so
        #     log time + a full game's length means the game has been played.
        # A market probed before settlement just reports itself open and is
        # retried next pass, at the cost of one lookup. Tennis/UFC close at
        # event time, so they are excluded -- probing them early would poll
        # matches that haven't been played.
        # College rows probe off the KICKOFF the logger knew instead: Kalshi's
        # college backstop is 48h (measured 2026-09-06: close_time = kickoff
        # + 48h on every KXNCAAF series), so the 66h window above would open
        # 18 HOURS BEFORE kickoff, and the week board logs its rows on
        # Tuesday, so log-time + 6h says nothing about the game. Five hours
        # after the logged kickoff the game is over; rows without event_ts
        # wait for close_time as before. NFL game rows are the same shape
        # (measured 2026-09-09: KXNFLGAME/SPREAD/TOTAL all close kickoff +
        # 48h, and the slate logs its rows days out), so those that carry a
        # kickoff probe the same way; the 66h rule stays for the old rows
        # that do not.
        early = [r["ticker"] for r in c.execute(
            "SELECT ticker FROM predictions WHERE graded=0 AND close_time > ? AND ("
            "(close_time - 237600 <= ? AND ts + 21600 <= ? "
            " AND (model LIKE 'mlb%' OR model LIKE 'nfl%')"
            " AND NOT (model LIKE 'nfl%' AND event_ts IS NOT NULL)) "
            "OR ((model LIKE 'cfb%' OR model LIKE 'nfl%') "
            "    AND event_ts IS NOT NULL AND event_ts + 18000 <= ?)) "
            "ORDER BY close_time LIMIT ?", (now, now, now, now, limit)).fetchall()]
        due += early
        early = set(early)
    graded = 0
    for tk in due:
        try:
            m = kalshi.get_market(tk)
        except urllib.error.HTTPError as e:
            if getattr(e, "code", None) == 404:      # market delisted -> never gradable
                _mark(tk, 2)
            continue                                 # other HTTP errors: retry later
        except Exception:
            continue                                 # transient (network) -> retry
        status = (m.get("status") or "").lower()
        result = (m.get("result") or "").lower()     # get_market already lower-cases
        if result in ("yes", "no"):
            _mark(tk, 1, 1 if result == "yes" else 0, now)
            graded += 1
        elif result == "scalar" or m.get("settlement_value") is not None:
            _mark(tk, 3, None, now, settle_value=m.get("settlement_value"))
            graded += 1
        elif (status in ("finalized", "settled", "determined", "closed")
              and result not in ("yes", "no") and tk not in early):
            # Settled void / scratched -> abandon. But NEVER on an early probe:
            # before close_time a result-less "closed" can be a market caught
            # mid-settlement (or a suspended game), and graded=2 is permanent.
            # Past close_time the same state really does mean scratched.
            _mark(tk, 2)
    return graded


def status():
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT model, COUNT(*) n, "
            "COALESCE(SUM(CASE WHEN graded=1 THEN 1 ELSE 0 END),0) g, "
            "COALESCE(SUM(CASE WHEN graded=2 THEN 1 ELSE 0 END),0) dead "
            "FROM predictions GROUP BY model").fetchall()
        shadow = c.execute(
            "SELECT s.model AS model, COUNT(*) n, "
            "COALESCE(SUM(CASE WHEN p.graded=1 THEN 1 ELSE 0 END),0) g, "
            "COALESCE(SUM(CASE WHEN p.graded=2 THEN 1 ELSE 0 END),0) dead "
            "FROM shadow s LEFT JOIN predictions p ON p.ticker = s.ticker "
            "GROUP BY s.model").fetchall()
    # "logged" excludes abandoned (delisted/void) markets so the count reflects
    # predictions that can actually graduate to a grade.
    out = {r["model"]: {"logged": r["n"] - r["dead"], "graded": r["g"],
                        "abandoned": r["dead"]} for r in rows}
    for r in shadow:
        out[r["model"]] = {"logged": r["n"] - r["dead"], "graded": r["g"],
                           "abandoned": r["dead"], "shadow": True}
    return out


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
            mk = p.get("mkt_win")
            if p.get("ticker") and fair is not None:
                rows.append((p["ticker"], fair / 100.0, ct,
                             (mk / 100.0) if mk is not None else None))
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
            mk = f.get("mkt_win")
            if f.get("ticker") and fair is not None:
                rows.append((f["ticker"], fair / 100.0, f.get("close_time"),
                             (mk / 100.0) if mk is not None else None))
    return log_many("ufc", rows)


_HARVESTERS = (_harvest_tennis, _harvest_ufc, _harvest_carried)


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
    # RESOLVE FIRST, SLEEP AFTER. The loop used to sleep its full interval
    # before doing anything, so a short-lived process -- the common case for a
    # locally run app that is opened, used and closed -- never graded a single
    # prediction. That is how MLB sat at 120 logged, 0 graded while its
    # calibration starved: every open finished before the first 15-minute
    # alarm. Grading work that is already due should not wait behind a timer.
    while True:
        try:
            harvest()
        except Exception as _e:
            errlog.note("PRED-loop", _e)
        try:
            snapshot_closes()
        except Exception as _e:
            errlog.note("PRED-loop-3", _e)
        try:
            resolve_due()
        except Exception as _e:
            errlog.note("PRED-loop-2", _e)
        time.sleep(interval)


def start(interval=900):
    """Background harvest + resolve loop. Boards are cached, so harvesting is
    cheap; resolving is a handful of Kalshi lookups. Runs once per process."""
    global _started
    if _started:
        return
    _started = True
    init_db()
    threading.Thread(target=_loop, args=(interval,), daemon=True).start()
