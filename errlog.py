"""The app's error ledger: every failure gets an ID and lands somewhere durable.

WHY THIS EXISTS. The codebase is deliberately fault-tolerant -- a cache that
cannot write is slow, a feed that cannot be read is skipped -- but tolerant
meant SILENT: dozens of `except Exception: pass` sites where a real breakage
looked exactly like normal operation, and every incident this app has had
("0/9 warming up", "rerun does nothing", "hasn't run in 32 hours") took a code
dig to even locate. Now every guarded failure calls note() with a stable ID,
uncaught exceptions in requests and background threads are hooked centrally,
and a scheduled GitHub Action pulls /api/errors/export into the repo's
sim-history branch under errors/, so the ledger can be read without touching
the server.

DESIGN RULES.
- note() NEVER raises and never blocks meaningfully: errors about errors are
  worse than silence, and half these calls sit inside except blocks.
- Same code+message within a window becomes one row with a count, not a row
  per occurrence -- a broken loop must not fill the 1 GB data disk overnight.
- SQLite in WAL mode: three gunicorn workers write concurrently.
- Nothing sensitive: message + traceback + route path only. No headers, no
  cookies, no request bodies -- the export lands in a PUBLIC repo.
- errlog imports nothing from the app, so any module may import errlog.

ID CONVENTION. "<AREA>-<what>", stable across releases: SLATE-build,
KAL-index-build, DEEP-mlb_deep, WARM-tick, HTTP-<endpoint>, THREAD-<name>,
JS-error. The swept `except: pass` sites carry "<MOD>-<function>" IDs
generated from their enclosing function, e.g. BB-_slate_release.
"""
import os
import sqlite3
import threading
import time
import traceback

_DB = os.environ.get("ERRLOG_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "errlog.db")

_lock = threading.Lock()
_init_done = False

# Per-code write throttle: first occurrence writes immediately; repeats inside
# the window accumulate in memory and are folded into the row on the next
# allowed write (or counted into the same row by the DB-side dedup).
_THROTTLE_S = 2.0
_last_write = {}          # code -> monotonic ts of last DB write
_pending = {}             # code -> occurrences held back by the throttle

# One row absorbs repeats of the same (code, msg) for this long.
_DEDUP_S = 900
# Rows older than this are pruned opportunistically.
_KEEP_DAYS = 30
_MSG_CAP = 500
_TB_CAP = 4000


def _conn():
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    c = sqlite3.connect(_DB, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _init(c):
    global _init_done
    if _init_done:
        return
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("""
        CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, last_ts REAL,
            code TEXT, msg TEXT, tb TEXT, path TEXT,
            pid INTEGER, n INTEGER DEFAULT 1
        )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_err_code ON errors(code, last_ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_err_last ON errors(last_ts)")
    _init_done = True


def note(code, exc=None, msg="", path=None):
    """Record one failure under a stable ID. Never raises.

    `exc` may be the caught exception (its type, message and traceback are
    captured); `msg` adds or replaces context; `path` is the route or job that
    was being served, if any."""
    try:
        code = str(code)[:80]
        if exc is not None and not msg:
            msg = f"{type(exc).__name__}: {exc}"
        elif exc is not None:
            msg = f"{msg} -- {type(exc).__name__}: {exc}"
        msg = str(msg)[:_MSG_CAP]
        tb = ""
        if exc is not None and getattr(exc, "__traceback__", None):
            tb = "".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__))[-_TB_CAP:]
        now = time.time()
        with _lock:
            held = _pending.pop(code, 0)
            if now - _last_write.get(code, 0) < _THROTTLE_S:
                _pending[code] = held + 1
                return
            _last_write[code] = now
        add = 1 + held
        with _conn() as c:
            _init(c)
            row = c.execute(
                "SELECT id FROM errors WHERE code=? AND msg=? AND last_ts>? "
                "ORDER BY last_ts DESC LIMIT 1",
                (code, msg, now - _DEDUP_S)).fetchone()
            if row:
                c.execute("UPDATE errors SET n=n+?, last_ts=?, tb=?, pid=? "
                          "WHERE id=?", (add, now, tb, os.getpid(), row["id"]))
            else:
                c.execute("INSERT INTO errors (ts, last_ts, code, msg, tb, "
                          "path, pid, n) VALUES (?,?,?,?,?,?,?,?)",
                          (now, now, code, msg, tb, path, os.getpid(), add))
                if row is None and (int(now) % 97) == 0:
                    c.execute("DELETE FROM errors WHERE last_ts < ?",
                              (now - _KEEP_DAYS * 86400,))
    except Exception:
        pass                    # an error log must never be a source of errors


class guard:
    """`with errlog.guard("SLATE-adopt"):` -- note the failure and swallow it.
    The drop-in replacement for `try: ... except Exception: pass` where that
    control flow is genuinely wanted and only the silence was the bug."""

    def __init__(self, code, path=None):
        self.code, self.path = code, path

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if ev is not None and isinstance(ev, Exception):
            note(self.code, ev, path=self.path)
            return True         # swallow, exactly as the old `pass` did
        return False            # KeyboardInterrupt etc. still propagate


def recent(limit=200, code=None, hours=None):
    """Newest rows first, optionally filtered by code and/or age."""
    try:
        q = "SELECT * FROM errors"
        cond, args = [], []
        if code:
            cond.append("code=?")
            args.append(code)
        if hours:
            cond.append("last_ts>?")
            args.append(time.time() - hours * 3600)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY last_ts DESC LIMIT ?"
        args.append(int(limit))
        with _conn() as c:
            _init(c)
            return [dict(r) for r in c.execute(q, args)]
    except Exception:
        return []


def summary(hours=24):
    """Per-code rollup for the window: how often, when last, what it said."""
    try:
        with _conn() as c:
            _init(c)
            rows = c.execute(
                "SELECT code, SUM(n) AS n, COUNT(*) AS variants, "
                "MIN(ts) AS first_ts, MAX(last_ts) AS last_ts "
                "FROM errors WHERE last_ts>? GROUP BY code ORDER BY n DESC",
                (time.time() - hours * 3600,)).fetchall()
            out = []
            for r in rows:
                last = c.execute(
                    "SELECT msg FROM errors WHERE code=? ORDER BY last_ts "
                    "DESC LIMIT 1", (r["code"],)).fetchone()
                d = dict(r)
                d["last_msg"] = last["msg"] if last else None
                out.append(d)
            return out
    except Exception:
        return []


def export_bundle(days=7):
    """Everything the GitHub Action commits: a per-code rollup plus the raw
    rows for the window, tracebacks included. Sanitized by construction --
    note() never stores headers, cookies or bodies."""
    return {"generated_at": time.time(),
            "window_days": days,
            "summary": summary(hours=days * 24),
            "errors": recent(limit=2000, hours=days * 24)}
