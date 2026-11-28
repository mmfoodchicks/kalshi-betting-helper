"""Guards for the crypto terminal (2026-11-27, crypto_terminal.py; section CX). Every crypto feature
was removed on 2026-10-29 after the old quote recorder's backtest read every saved quote into one
gunicorn worker as dicts (~1.39 KB a row) and drove it to 2,047/2,048 MB. These pin the rules that
removal taught, and the terminal's own claims:

  CX-RULE    the settlement rule is read from each market's own rules text, on real fixtures
             (research/data/crypto/rule_fixtures.json): index, window, comparison, the precise strike;
             a range, a one-touch, a wrong index or a strike the text and fields disagree on is refused;
  CX-MODEL   the settlement-average SD and probability are the definitions their docstrings state;
  CX-PRE     nothing is logged inside the settlement window; the loop wakes only on the looks;
  CX-SQL     no fetchall, and no statement hands Python more than a bounded number of rows, on a
             50,000-row record;
  CX-MEM     the worker's RSS stays flat over hundreds of simulated recorder passes, and the record
             read over 200,000 rows costs a few MB, not the hundreds a fetchall would;
  CX-STORE   retention and downsampling bound the file; the measured bytes a day;
  CX-PAPER   no order endpoint, no credential, GET only, routes GET only;
  CX-DROP    the new table never wears a dropped table's name, lives in its own file, and the one-way
             drop still takes exactly the three removed tables;
  CX-UI      the tab: escaped, no inline handlers, one timer that never polls a hidden screen;
  CX-VAL     the committed validation figures are what the committed frame and code compute, and the
             production constants are the development week's choice.
  CXU / CXS  UI v3 (2026-11-28): market windows from Kalshi's metadata, window-only candles, the paper
             position's value, "meaningful" moves, the movement outlook and scenarios, the chart helpers.

    python3 tests/guards_auto_crypto.py      # exits 1 on any failed check
"""
import contextlib
import inspect
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import time

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)


def _read(*p):
    with open(os.path.join(_ROOT, *p), encoding="utf-8") as f:
        return f.read()


def _rss_mb():
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError):
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3


@contextlib.contextmanager
def _db(CT):
    d = tempfile.mkdtemp(prefix="cx-guard-")
    old = os.environ.get("CRYPTO_DB")
    os.environ["CRYPTO_DB"] = os.path.join(d, "crypto_record.db")
    try:
        CT.init_db()
        yield os.environ["CRYPTO_DB"]
    finally:
        if old is None:
            os.environ.pop("CRYPTO_DB", None)
        else:
            os.environ["CRYPTO_DB"] = old
        import shutil
        shutil.rmtree(d, ignore_errors=True)


@contextlib.contextmanager
def _patched(*trip):
    saved = [(o, n, getattr(o, n)) for o, n, _ in trip]
    try:
        for o, n, v in trip:
            setattr(o, n, v)
        yield
    finally:
        for o, n, v in saved:
            setattr(o, n, v)


def _row_values(CT, i, close, kind, look, coin="BTC"):
    p = 0.5 + 0.4 * math.sin(i)
    return (f"KX{coin}{'15M' if kind == 'm15' else 'D'}-{int(close)}-{i % 7}", look, coin, kind, close - look, close,
            float(look), 61000.0 + i % 97, 61000.0 + i % 89, ">=", 9e-5, p, 40.0 + i % 20, 42.0 + i % 20, 58.0 - i % 20,
            1.7, 1.7, 4, i % 2, 61000.0 + i % 91, close + 120)


def _fill(CT, path, n, now, spread_days=40):
    with contextlib.closing(sqlite3.connect(path)) as c, c:
        rows = []
        for i in range(n):
            kind = "m15" if i % 2 else "hourly"
            look = CT.CHECKPOINTS[kind][i % len(CT.CHECKPOINTS[kind])]
            close = now - (i % (spread_days * 96)) * 900.0 - 900
            rows.append(_row_values(CT, i, close, kind, look))
        c.executemany(f"INSERT OR IGNORE INTO {CT.TABLE} VALUES ({','.join('?' * 21)})", rows)


class _CountCur:
    def __init__(self, cur, sink):
        self._c, self._sink, self.n = cur, sink, 0
        sink.append(self)

    def fetchone(self):
        r = self._c.fetchone()
        self.n += r is not None
        return r

    def fetchall(self):                       # counted too: a guard, not a ban, at this layer
        r = self._c.fetchall()
        self.n += len(r)
        return r

    def __iter__(self):
        for r in self._c:
            self.n += 1
            yield r


class _CountConn:
    def __init__(self, conn, sink):
        self._c, self._sink = conn, sink

    def execute(self, *a):
        return _CountCur(self._c.execute(*a), self._sink)

    def __enter__(self):
        self._c.__enter__()
        return self

    def __exit__(self, *a):
        return self._c.__exit__(*a)

    def __getattr__(self, k):
        return getattr(self._c, k)


def run(ck):
    import crypto_terminal as CT
    src = _read("crypto_terminal.py")

    # ---- CX-RULE ---------------------------------------------------------------------------------------------
    fx = json.loads(_read("research", "data", "crypto", "rule_fixtures.json"))["markets"]
    got = {m["ticker"]: CT.parse_rule(m, m["coin"]) for m in fx}
    by = {m["ticker"].split("-")[0]: (m, got[m["ticker"]]) for m in fx}
    b15 = by["KXBTC15M"][1]
    ck("CX-RULE the 15-minute rule reads as Kalshi states it: the 60-second average of CF Benchmarks' BRTI before the "
       "close, AT LEAST the same average before the open, which is the market's floor_strike",
       bool(b15) and b15["index"] == "BRTI" and b15["window_s"] == 60 and b15["op"] == ">=" and b15["reference"] == "open_avg"
       and b15["strike"] == by["KXBTC15M"][0]["floor_strike"] and "at least" in b15["says"], str(b15))
    sett = [m for m in fx if m["ticker"].startswith("KXBTC15M") and m.get("source", "").find("settled") >= 0]
    ck("CX-RULE the open market's strike is the previous window's settlement: the settled fixture's close and the next "
       "window's floor_strike are the same rule text's two averages (read live 2026-11-27: 61659.25 both)",
       "is at least the simple average" in by["KXBTC15M"][0]["rules_primary"] and len(sett) == 1)
    d15 = by["KXDOGE15M"]
    ck("CX-RULE DOGE's 15-minute strike is custom_strike's 7-place figure, not floor_strike's rounded one "
       "(0.0581877 vs 0.058187: priced on the rounded one a level between them reads the wrong side)",
       bool(d15[1]) and d15[1]["strike"] == float(d15[0]["custom_strike"]["floor_strike"])
       and d15[1]["strike"] != d15[0]["floor_strike"] and d15[1]["round_digits"] == 7, str(d15[1]))
    hd = by["KXBTCD"][1]
    xd = by["KXXRPD"][1]
    dd = by["KXDOGED"][1]
    sd_ = by["KXSOLD"][1]
    ck("CX-RULE the hourly rules read ABOVE a fixed strike: BTC (named 'Bitcoin Real-Time Index (BRTI)'), XRP (with the "
       "'no data resolves No' clause), DOGE (strike_type custom, the comparison inside custom_strike) and SOL ($ in the text)",
       all(r and r["op"] == ">" and r["reference"] == "fixed" for r in (hd, xd, dd, sd_))
       and hd["index"] == "BRTI" and xd["no_data_no"] and not hd["no_data_no"] and dd["strike"] == 0.2349999
       and sd_["strike"] == 99.9999 and xd["round_digits"] == 4, str([hd, xd, dd, sd_])[:400])
    ck("CX-RULE a range market ('is between') is refused, not priced as above/below", got[by["KXBTC"][0]["ticker"]] is None)
    m = dict(by["KXBTCD"][0], floor_strike=69899.99)
    ck("CX-RULE a strike the text and the field disagree on is refused", CT.parse_rule(m, "BTC") is None)
    ck("CX-RULE the wrong coin's index is refused (a BTC rule read as ETH)", CT.parse_rule(by["KXBTCD"][0], "ETH") is None)
    touch = dict(by["KXBTCD"][0], rules_primary="If CF Benchmarks' BRTI reaches 150000 at any time before Dec 31, 2026, "
                                                "then the market resolves to Yes.")
    ck("CX-RULE a one-touch rule (no settlement average) is refused", CT.parse_rule(touch, "BTC") is None)
    ck("CX-RULE every coin's index in COINS (or its listed alias) is the one its fixture names",
       all(CT._norm_index(got[m["ticker"]]["index"]) in CT.index_names(m["coin"]) for m in fx if got[m["ticker"]]))
    eh = by.get("KXETHD", (None, None))[1]
    ck("CX-RULE ETH's hourly rules name the index 'Ethereum Real-Time Index (ERTI)' where the 15-minute ones say "
       "ETHUSDRTI; the series' settlement source is ETHUSD_RTI, so ERTI is read as ETH's index (without the alias all "
       "300 of the live ETH hourly strikes went unpriced on 2026-11-27)",
       bool(eh) and eh["index"] == "ERTI" and eh["op"] == ">" and CT.parse_rule(by["KXETHD"][0], "BTC") is None, str(eh))

    # ---- CX-MODEL --------------------------------------------------------------------------------------------
    sig = 1e-4
    n = 60
    disc = (300 - 60) + (n + 1) * (2 * n + 1) / (6.0 * n)        # one-second ticks of a Brownian path
    cont = CT.settle_sd(sig, 300, proxy_sd=0.0) ** 2 / sig ** 2
    ck("CX-MODEL the settlement average's variance time 300 s out is tau - 2W/3 = 260 s (a 60-tick discrete average "
       "reads 260.5): not tau, which overstates it by 40 s",
       abs(cont - 260.0) < 1e-9 and abs(cont - disc) / disc < 0.01, f"{cont} {disc}")
    inw = CT.settle_sd(sig, 30, proxy_sd=0.0) ** 2 / sig ** 2
    ck("CX-MODEL inside the window only the part still to come is uncertain: (tau/W)^2 x tau/3", abs(inw - 0.25 * 10.0) < 1e-9)
    ck("CX-MODEL the proxy's distance adds in quadrature",
       abs(CT.settle_sd(sig, 300, proxy_sd=3e-4) ** 2 - (sig ** 2 * 260 + 9e-8)) < 1e-18)
    r_ge = {"op": ">=", "strike": 100.0}
    r_lt = {"op": "<", "strike": 100.0}
    ck("CX-MODEL at the strike the chance is a half; 'below' is the complement of 'above'; higher level, higher chance",
       abs(CT.p_yes(r_ge, 100.0, sig, 300) - 0.5) < 1e-12
       and abs(CT.p_yes(r_lt, 100.3, sig, 300) + CT.p_yes(r_ge, 100.3, sig, 300) - 1.0) < 1e-12
       and CT.p_yes(r_ge, 100.3, sig, 300) > CT.p_yes(r_ge, 100.1, sig, 300) > 0.5)
    iv = CT.implied_vol(r_ge, 100.2, 300, 0.7)
    ck("CX-MODEL the implied volatility gives back Kalshi's mid", iv is not None and abs(CT.p_yes(r_ge, 100.2, iv, 300) - 0.7) < 1e-6)
    cl = [100.0 * math.exp(0.001 * ((i * 7919) % 13 - 6)) for i in range(300)]
    rets = [math.log(b / a) for a, b in zip(cl, cl[1:])]
    lam = 0.5 ** (1 / 60.0)
    w = [lam ** k for k in range(len(rets))][::-1]
    ref = math.sqrt(sum(a * r * r for a, r in zip(w, rets)) / sum(w) / 60.0)
    ck("CX-MODEL ewma_sigma is the EWMA of squared one-minute returns (newest heaviest) per second, times the multiplier",
       abs(CT.ewma_sigma(cl, 60, 1.0) - ref) < 1e-15 and abs(CT.ewma_sigma(cl, 60, 1.1) - 1.1 * ref) < 1e-15
       and CT.ewma_sigma(cl[:20], 60, 1.0) is None)

    # ---- CX-PRE ----------------------------------------------------------------------------------------------
    ck("CX-PRE every look is at least a settlement window before the close (nothing logged has seen a second of the average)",
       all(cp >= CT.WINDOW_S for v in CT.CHECKPOINTS.values() for cp in v))
    with _db(CT) as path:
        now = 1795770000.0
        row = {"ticker": "X", "rule": {"op": ">=", "strike": 1.0}, "close_ts": now + 59, "p_model": 0.5, "edge": {}}
        with contextlib.closing(CT._conn()) as c, c:
            a = CT._log_row(c, "BTC", "m15", 60, row, 1.0, 1e-4, 4, now)
            b = CT._log_row(c, "BTC", "m15", 60, dict(row, close_ts=now + 61), 1.0, 1e-4, 4, now)
            c2 = CT._log_row(c, "BTC", "m15", 60, dict(row, ticker="Y", rule=None, close_ts=now + 300), 1.0, 1e-4, 4, now)
        with contextlib.closing(sqlite3.connect(path)) as c:
            nrow = c.execute(f"SELECT COUNT(*) FROM {CT.TABLE}").fetchone()[0]
    ck("CX-PRE a row 59 s before its close is refused, 61 s is kept, a market whose rule did not parse is never logged",
       a is False and b is True and c2 is False and nrow == 1, f"{a} {b} {c2} {nrow}")
    base = 1795770000.0 - 1795770000.0 % 3600
    wakes, t = [], base
    while t < base + 3600:
        t = CT.next_wake(t)
        wakes.append(t - base)
    ck("CX-PRE the loop sleeps between looks: 4 a quarter-hour plus the hourly 30/15/5-minute ones, 18 wakes an hour "
       "(the old recorder polled every 90 s), each on a look's own second",
       len([x for x in wakes if x <= 3600]) == 18 and 3600 - 1800 in wakes and 900 - 600 in wakes and 3600 - 60 in wakes,
       str(wakes))
    ck("CX-PRE a late wake past the slack logs nothing", CT.due_look(255, (300,)) is None and CT.due_look(299, (300,)) == 300)

    # ---- CX-SQL ----------------------------------------------------------------------------------------------
    ck("CX-SQL the module never calls fetchall (the removed backtest's pattern)", ".fetchall(" not in src)
    sink = []
    with _db(CT) as path:
        _fill(CT, path, 50_000, time.time())
        real = CT._conn
        with _patched((CT, "_conn", lambda **kw: _CountConn(real(**kw), sink)),
                      (CT, "_kalshi", lambda p: {"markets": []}), (CT.time, "sleep", lambda s: None)):
            CT.record()
            CT.record(coin="BTC", kind="m15", look=300)
            CT.record_by()
            CT.record_extra()
            CT.paper_positions()
            CT.grade_tick(time.time())
            CT.prune(time.time())
    worst = max((c.n for c in sink), default=0)
    ck("CX-SQL on a 50,000-row record no statement hands Python more than 200 rows (the grading pass's LIMIT; the "
       "record is one row of sums, ten bins, one GROUP BY of at most 40)", worst <= 200 and len(sink) >= 8,
       f"worst={worst} statements={len(sink)}")

    with _db(CT) as path:
        _fill(CT, path, 2_000, time.time())
        CT.write_summary()

        def no_sql(*a, **k):
            raise AssertionError("a page read touched the record")
        with _patched((CT, "_conn", no_sql)):
            try:
                s = CT.summary()
                served = bool(s.get("record", {}).get("n")) and isinstance(s.get("by"), list)
            except AssertionError:
                served = False
    import inspect
    import app as _app0
    rsrc = inspect.getsource(_app0.api_crypto_record)
    loop = inspect.getsource(CT._loop)
    ck("CX-SQL a page never scans the record: the background owner writes the summary after each grading pass and "
       "the board and the record route read that file (measured: the two queries over 200,000 rows take ~2 s, and "
       "the tab refreshes every 15 s)",
       served and "crypto_terminal.summary()" in rsrc and "crypto_terminal.record(" not in rsrc
       and loop.index("grade_tick(now)") < loop.index("write_summary()") and "summary().get(\"record\")" in src)

    # ---- CX-MEM ----------------------------------------------------------------------------------------------
    sim = {"now": 0.0}

    def fake_get(url, timeout=6):
        if "candles" in url:
            t0 = int(sim["now"] // 60 * 60)
            return [[t0 - 60 * i, 1.0e5, 1.0e5 + 50, 1.0e5 - 20 + i % 7, 1.0e5 + (i * 37) % 41, 3.0] for i in range(300)]
        if "kraken" in url:
            return {"result": {"XXBTZUSD": {"a": ["100010.0"], "b": ["100000.0"], "c": ["100005.0"]}}}
        if "gemini" in url:
            return {"bid": "100001", "ask": "100011", "last": "100006", "volume": {"timestamp": sim["now"] * 1000}}
        if "bitstamp" in url:
            return {"bid": "100002", "ask": "100012", "last": "100007", "timestamp": str(int(sim["now"]))}
        return {"bid": "100000", "ask": "100010", "price": "100005",
                "time": time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(sim["now"]))}
    tmpl15 = by["KXBTC15M"][0]
    tmplh = by["KXBTCD"][0]

    def iso(ts):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

    def coin_of(series):
        return next(c for c, v in CT.COINS.items() if series in (v["m15"], v["hourly"]))

    def fake_kalshi(path):
        now = sim["now"]
        if "series_ticker=" in path:
            close = (math.floor(now / 900) + 1) * 900
            series = path.split("series_ticker=")[1].split("&")[0]
            idx = CT.COINS[coin_of(series)]["index"]
            return {"markets": [dict(tmpl15, ticker=f"{series}-{int(close)}",
                                     rules_primary=tmpl15["rules_primary"].replace("BRTI", idx),
                                     close_time=iso(close), floor_strike=100003.5, custom_strike={"round_digits": "2"},
                                     yes_bid_dollars="0.4500", yes_ask_dollars="0.4700", no_ask_dollars="0.5500",
                                     volume_fp="1000")]}
        if "event_ticker=" in path:
            close = (math.floor(now / 3600) + 1) * 3600
            series = path.split("event_ticker=")[1].split("-")[0]
            idx = CT.COINS[coin_of(series)]["index"]
            out = []
            for k in range(188):                 # bitcoin's hourly ladder: 188 strikes, ~360 KB raw
                s = 90000.0 + 250.0 * k - 0.01
                out.append(dict(tmplh, ticker=f"{series}-{int(close)}-T{s:.2f}", close_time=iso(close), floor_strike=s,
                                rules_primary=tmplh["rules_primary"].replace("69799.99", f"{s:.2f}")
                                .replace("(BRTI)", f"({idx})"),
                                yes_bid_dollars="0.3000", yes_ask_dollars="0.3200", no_ask_dollars="0.7000",
                                volume_fp="10"))
            return {"markets": out}
        if "tickers=" in path:
            return {"markets": [{"ticker": t, "result": "yes" if i % 2 else "no", "expiration_value": "100004.2"}
                                for i, t in enumerate(path.split("tickers=")[1].split(","))]}
        return {}

    looks = sorted({c - cp for c0 in range(0, 6 * 3600, 900) for c in [c0 + 900]
                    for cp in CT.CHECKPOINTS["m15"]} | {c - cp for c in range(3600, 6 * 3600 + 1, 3600)
                                                        for cp in CT.CHECKPOINTS["hourly"]})
    with _db(CT) as path, _patched((CT, "_get", fake_get), (CT, "_kalshi", fake_kalshi),
                                   (CT, "_fee", lambda t, c: 7.0 * c / 100 * (1 - c / 100)),
                                   (CT.time, "sleep", lambda s: None)):
        start = 1795766400.0

        def passes(k0, k1):
            logged = 0
            for k in range(k0, k1):
                day, off = divmod(k, len(looks))
                sim["now"] = start + day * 6 * 3600 + looks[off]
                logged += CT.log_tick(sim["now"])
                CT.board("BTC")
                if k % 9 == 0:
                    CT.grade_tick(sim["now"] + 3600)
                    CT.record()
                    CT.record_extra()
                    CT.paper_positions()
            return logged
        passes(0, 60)
        import gc
        gc.collect()
        m0 = _rss_mb()
        logged = passes(60, 460)
        gc.collect()
        m1 = _rss_mb()
        with contextlib.closing(sqlite3.connect(path)) as c:
            nrec = c.execute(f"SELECT COUNT(*) FROM {CT.TABLE}").fetchone()[0]
    ck("CX-MEM 400 simulated recorder passes (every coin, the 188-strike hourly ladder, grading and the record each "
       "ninth pass) leave the worker's RSS flat: under 8 MB of growth while the record gains thousands of rows",
       m1 - m0 < 8.0 and logged > 1000 and nrec > 1000, f"growth={m1 - m0:.1f} MB logged={logged} rows={nrec}")
    with _db(CT) as path:
        _fill(CT, path, 200_000, time.time(), spread_days=300)
        import gc
        gc.collect()
        r0 = _rss_mb()
        t0 = time.time()
        rec = CT.record()
        CT.record_by()
        dt = time.time() - t0
        gc.collect()
        r1 = _rss_mb()
    ck("CX-MEM the record read over 200,000 rows grows the worker by under 15 MB (a fetchall at the removed backtest's "
       "~1.39 KB a row would be ~280 MB) and answers in seconds",
       r1 - r0 < 15.0 and rec["n"] > 0 and dt < 20.0, f"growth={r1 - r0:.1f} MB n={rec['n']} {dt:.1f}s")

    # ---- CX-STORE --------------------------------------------------------------------------------------------
    per_day = 5 * 96 * len(CT.CHECKPOINTS["m15"]) + 5 * 24 * len(CT.CHECKPOINTS["hourly"]) * CT.HOURLY_STRIKES
    dec_day = 5 * 96 + 5 * 24 * CT.HOURLY_STRIKES
    with _db(CT) as path:
        now = 1795770000.0
        with contextlib.closing(sqlite3.connect(path)) as c, c:
            rows = []
            for d in range(10):
                i = 0
                for coin in CT.COINS:
                    for q in range(96):
                        close = now - d * 86400 - q * 900
                        for cp in CT.CHECKPOINTS["m15"]:
                            rows.append(_row_values(CT, i, close, "m15", cp, coin))
                            i += 1
                    for hr in range(24):
                        close = now - d * 86400 - hr * 3600 + 7
                        for cp in CT.CHECKPOINTS["hourly"]:
                            for s in range(CT.HOURLY_STRIKES):
                                v = list(_row_values(CT, i, close, "hourly", cp, coin))
                                v[0] = f"KX{coin}D-{int(close)}-T{s}"
                                rows.append(tuple(v))
                                i += 1
            c.executemany(f"INSERT OR IGNORE INTO {CT.TABLE} VALUES ({','.join('?' * 21)})", rows)
        with contextlib.closing(sqlite3.connect(path)) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            n10 = c.execute(f"SELECT COUNT(*) FROM {CT.TABLE}").fetchone()[0]
        bpr = os.path.getsize(path) / n10
        steady = (CT.DETAIL_DAYS * per_day + (CT.KEEP_DAYS - CT.DETAIL_DAYS) * dec_day)
        ck(f"CX-STORE measured: {per_day:,} rows a day at {bpr:.0f} bytes a row = {per_day * bpr / 1e6:.2f} MB a day; "
           f"at retention ({CT.DETAIL_DAYS} days of every look, then the decision look to {CT.KEEP_DAYS} days) "
           f"{steady:,} rows = {steady * bpr / 1e6:.0f} MB, under the {CT.MAX_ROWS:,}-row cap and 60 MB of a 1 GB disk every store shares",
           n10 == 10 * per_day and steady < CT.MAX_ROWS and steady * bpr < 60e6, f"{n10} {bpr} {steady}")
        size0 = os.path.getsize(path)
        with _patched((CT, "DETAIL_DAYS", 3), (CT, "KEEP_DAYS", 7)):
            CT.prune(now + 1)
        with contextlib.closing(sqlite3.connect(path)) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            old_detail = c.execute(f"""SELECT COUNT(*) FROM {CT.TABLE} WHERE close_ts < ? AND look_s NOT IN (300, 900)""",
                                   (now + 1 - 3 * 86400,)).fetchone()[0]
            too_old = c.execute(f"SELECT COUNT(*) FROM {CT.TABLE} WHERE close_ts < ?", (now + 1 - 7 * 86400,)).fetchone()[0]
            kept_dec = c.execute(f"""SELECT COUNT(*) FROM {CT.TABLE} WHERE close_ts < ? AND look_s IN (300, 900)""",
                                 (now + 1 - 3 * 86400,)).fetchone()[0]
            left = c.execute(f"SELECT COUNT(*) FROM {CT.TABLE}").fetchone()[0]
        size1 = os.path.getsize(path)
        ck("CX-STORE prune downsamples past DETAIL_DAYS to the decision look, drops past KEEP_DAYS, and hands the pages "
           "back to the disk (incremental auto-vacuum)",
           old_detail == 0 and too_old == 0 and kept_dec > 0 and size1 < size0 * 0.75, f"{old_detail} {too_old} {kept_dec} "
                                                                                       f"{size0}->{size1}")
        with _patched((CT, "MAX_ROWS", 1000)):
            CT.prune(now + 1)
        with contextlib.closing(sqlite3.connect(path)) as c:
            capped = c.execute(f"SELECT COUNT(*) FROM {CT.TABLE}").fetchone()[0]
        ck("CX-STORE the row cap holds beneath the schedule (oldest closes go first)", 0 < capped <= 1000 < left,
           f"{left} -> {capped}")

    # ---- CX-PAPER --------------------------------------------------------------------------------------------
    js = _read("static", "crypto.js")
    # Kalshi's authenticated API (orders, balances) needs KALSHI-ACCESS-KEY / -SIGNATURE headers signed with an
    # RSA key loaded from a PEM file: none of those pieces may appear in a paper-only module
    bad = [w for w in ("/portfolio", "/orders", "create_order", "KALSHI-ACCESS", "Authorization", "load_pem", "sign(",
                       "method=", "data=") if w in src]
    ck("CX-PAPER the module names no order endpoint, no credential, and builds no request with a body or a method",
       not bad and src.count("urllib.request.Request(") == 1, str(bad))
    # v2: the tab may POST, but only to Vigil's own paper ledger, through one function that refuses any other URL;
    # its one WebSocket is Coinbase's public market-data feed, and it only ever sends a subscribe
    jsbad = [w for w in ("/portfolio", "/orders", "KALSHI-ACCESS", "Authorization") if w in js]
    urls = re.findall(r'getJSON\("([^"?]+)', js)
    posts = re.findall(r'postJSON\("([^"]+)"', js)
    post_fn = js[js.index("function postJSON("):js.index("// ---- the live feed")]
    sends = re.findall(r"\.send\(([^;]+)\);", js)
    ck("CX-PAPER the tab GETs /api/crypto/* and POSTs only to /api/crypto/paper/* (a guard inside postJSON refuses "
       "anything else); one WebSocket, Coinbase's public feed, sending only a subscribe",
       not jsbad and urls and all(u.startswith("/api/crypto/") for u in urls) and js.count("fetch(") == 2
       and posts and all(u.startswith("/api/crypto/paper/") for u in posts) and js.count('method: "POST"') == 1
       and 'url.indexOf("/api/crypto/paper/") !== 0' in post_fn and js.count("new WebSocket(") == 1
       and 'WS_URL = "wss://ws-feed.exchange.coinbase.com"' in js and len(sends) == 1 and '"subscribe"' in sends[0],
       f"{jsbad} {urls} {posts} {sends}")
    import app as _app
    rules = [r for r in _app.app.url_map.iter_rules() if r.rule.startswith("/api/crypto")]
    posts_r = sorted(r.rule for r in rules if "POST" in (r.methods or set()))
    ck("CX-PAPER the crypto routes answer GET only, but for the two paper-ledger routes and the PC's token-only book upload (POST only), which write "
       "rows to the terminal's own file and read Kalshi by GET; the open takes no price from the page",
       len(rules) == 9 and posts_r == ["/api/crypto/book", "/api/crypto/paper/close", "/api/crypto/paper/open"]
       and all((r.methods or set()) <= ({"POST", "OPTIONS"} if r.rule in posts_r else {"GET", "HEAD", "OPTIONS"}) for r in rules)
       and "price" not in inspect.signature(CT.paper_open).parameters,
       str([(r.rule, sorted(r.methods or ())) for r in rules]))

    # ---- CX-DROP ---------------------------------------------------------------------------------------------
    import store
    ck("CX-DROP the one-way drop still names exactly the three removed tables, and the terminal's table is none of them",
       tuple(store._CRYPTO_TABLES) == ("quote_samples", "resolved_markets", "markets") and CT.TABLE not in store._CRYPTO_TABLES
       and not any(re.search(rf"CREATE TABLE[^(]*\b{t}\b", src) for t in store._CRYPTO_TABLES))
    old = {k: os.environ.get(k) for k in ("CRYPTO_DB", "PREDLOG_DB", "KALSHI_DB")}
    try:
        os.environ.pop("CRYPTO_DB", None)
        os.environ["PREDLOG_DB"], os.environ["KALSHI_DB"] = "/data/predlog.db", "/data/markets.db"
        p_render = CT._db_path()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    ck("CX-DROP the record lives in its own file on the persistent disk beside predlog.db (on Render /data/crypto_record.db), "
       "never inside markets.db where the drop runs", p_render == "/data/crypto_record.db" and p_render != store.DB_PATH,
       p_render)
    gi = _read(".gitignore")
    ck("CX-DROP the record's file is gitignored (*.db)", "*.db" in gi.split())

    # ---- CX-UI -----------------------------------------------------------------------------------------------
    html = _read("templates", "index.html")
    appjs = _read("static", "app.js")
    inline = [x for x in ("onclick=", "onchange=", "oninput=", "onload=", "onerror=") if x in js]
    sec = html[html.index('<div id="tab-crypto"'):html.index("<!-- /tab-crypto -->")]
    ck("CX-UI the tab: wired in setupTabs, its script loaded, no inline handlers, every server string through h(), "
       "paper-only said on the page, no coin name hard-coded in the markup (the list comes from the server)",
       'data-tab="crypto"' in html and "/static/crypto.js" in html and '$("tab-crypto").classList.toggle("hidden", tab !== "crypto")'
       in appjs and "initCrypto()" in appjs and not inline and "function h(x)" in js and "paper only" in sec
       and "Nothing here places an order" in sec and "BTC" not in html and 'h(rule.says)' in js and 'h(d.note)' in js and 'h(err && err.message)' in js, str(inline))
    arm = js[js.index("function arm()"):js.index("function getJSON")]
    ck("CX-UI one refresh timer: cleared before it is armed, armed only while the tab is on screen and the page visible "
       "(the live socket closed when it is not), a setTimeout chain (no setInterval)",
       "clearTimeout(st.timer)" in arm and "if (!onScreen()) { closeWS(); return; }" in arm and "setInterval" not in js
       and "document.hidden" in js and 'classList.contains("hidden")' in js and "if (!onScreen()) { closeWS(); return; }" in js)
    ck("CX-UI bounded buffers in the browser: every bar buffer capped at MAXBARS (480), the session's fair-price line at 120, "
       "chart redraws coalesced to one per animation frame",
       "MAXBARS = 480" in js and "if (b.length > MAXBARS) b.splice(0, b.length - MAXBARS);" in js
       and "if (pts.length > 120) pts.splice(0, pts.length - 120);" in js and "requestAnimationFrame(" in js)
    lib = os.path.join(_ROOT, "static", "vendor", "lightweight-charts-4.1.3.standalone.production.js")
    import hashlib
    with open(lib, "rb") as f:
        lib_sha = hashlib.sha256(f.read()).hexdigest()
    ck("CX-UI the chart library is vendored and pinned: TradingView Lightweight Charts 4.1.3 from the npm tarball, its "
       "sha256 fixed, its Apache-2.0 licence beside it, loaded from static/ only when the tab opens, the attribution "
       "its README asks for on the page; the tables scroll inside their own box at phone width",
       lib_sha == "0c146b9bfbc6e5f19da1a0de876dae43023c332d5157d82f7575f9b7653ee519"
       and os.path.exists(os.path.join(_ROOT, "static", "vendor", "lightweight-charts-LICENSE.txt"))
       and 'LIB = "/static/vendor/lightweight-charts-4.1.3.standalone.production.js"' in js
       and "lightweight-charts" not in html.replace(sec, "") and "tradingview.com" in sec
       and "cdn" not in js.lower() and js.count('overflow-x:auto') >= 2, lib_sha)
    run_v2(ck, CT)

    # ---- CX-VAL ----------------------------------------------------------------------------------------------
    vpath = os.path.join(_ROOT, "research", "data", "crypto", "validation.json")
    if not os.path.exists(vpath):
        ck("CX-VAL the validation's figures are committed", False, "research/data/crypto/validation.json missing")
        return
    sys.path.insert(0, os.path.join(_ROOT, "research"))
    import crypto_validation as V
    saved = json.loads(_read("research", "data", "crypto", "validation.json"))
    now = V.score()
    ck("CX-VAL the committed validation figures are exactly what the committed frame and the production code compute "
       "(proxy SD, the development choice, every held-out row, the paper rule, the decision)",
       now == saved, str([k for k in now if now.get(k) != saved.get(k)]))
    ch = saved["chosen"]
    ck("CX-VAL the production constants are the development week's choice, frozen before the held-out week",
       CT.VOL_HALFLIFE_MIN == ch["halflife_min"] and CT.VOL_MULT == ch["mult"]
       and abs(CT.PROXY_SD - saved["proxy_sd"]) < 5e-6, f"{CT.VOL_HALFLIFE_MIN} {CT.VOL_MULT} {CT.PROXY_SD} {ch} "
                                                         f"{saved['proxy_sd']}")
    p = saved["heldout"]["300"]["pooled"]
    ck("CX-VAL the board never calls an edge proven off the validation: the held-out week did not show the model past "
       "Kalshi by 2 SE (the decision string says which way), so edges_proven rests on the live record alone",
       not (p.get("se") and p["diff"] < -2 * p["se"]) and saved["decision"]["edges"].startswith("unproven")
       and 'rec.get("decided") == "model"' in src)


def run_v2(ck, CT):
    """v2 (2026-11-27): the what-if panel, the crossing probability, the signals, paper positions at executable
    prices, the intrawindow record, and the movement research's harness (CXM)."""
    # ---- what if, crossing, signal ----
    rule = {"op": ">=", "strike": 100.0}
    sg, tau = 8e-5, 400.0
    w = CT.what_if(rule, 100.02, sg, tau)
    ok = len(w) == 2 * len(CT.WHATIF_BP) and all(abs(x["p"] - round(CT.p_yes(rule, 100.02 * math.exp(x["bp"] / 1e4), sg,
                                                                             tau - 60), 4)) < 1e-12 for x in w)
    ck("CXV2-WHATIF the what-if fair value is the v1 model a minute on (tau - 60) at the level moved by +-4/8/16 bp "
       "(about $25/50/100 on bitcoin at $61k), rising with the move, and empty once a minute would reach the window",
       ok and [x["p"] for x in w] == sorted(x["p"] for x in w) and CT.what_if(rule, 100.0, sg, 119.0) == [], str(w)[:200])
    sd = math.sqrt(sg ** 2 * tau + CT.PROXY_SD ** 2)
    pc = CT.p_cross(100.3, 100.0, sg, tau)
    ck("CXV2-CROSS the crossing chance is the reflection principle, 2 x (1 - Phi(|ln(L/K)| / sd)) with the proxy's sd "
       "inside: 1 at the strike, symmetric, falling with distance",
       abs(pc - round(2 * (1 - CT._phi(math.log(1.003) / sd)), 4)) < 1e-12 and CT.p_cross(100.0, 100.0, sg, tau) == 1.0
       and CT.p_cross(99.7, 100.0, sg, tau) == CT.p_cross(100.0, 99.7, sg, tau) and CT.p_cross(101, 100, sg, tau) < pc)
    s1 = CT.signal({"edge": {"yes": 5.0, "no": -9.0}})
    s2 = CT.signal({"edge": {"yes": 1.0, "no": 4.0}})
    s3 = CT.signal({"edge": {"yes": 2.9, "no": 2.9}})
    s4 = CT.signal({"edge": {"yes": 9.0}, "in_window": True})
    ck("CXV2-SIGNAL the paper signal is the v1 rule (fee-in edge at the ask 3c+), the larger side; none inside the window",
       (s1["code"], s2["code"], s3["code"], s4["code"]) == ("yes_entry", "no_entry", "none", "none"))
    # ---- paper positions, at executable prices the SERVER reads ----
    book = {"yes_bid_dollars": "0.4400", "yes_ask_dollars": "0.4500", "no_bid_dollars": "0.5300", "no_ask_dollars": "0.5600"}
    fx = json.loads(_read("research", "data", "crypto", "rule_fixtures.json"))["markets"]
    tm = next(m for m in fx if m["ticker"].startswith("KXBTC15M"))
    state = {"result": "", "status": "active", "ask": "0.4500", "bid": "0.4400"}
    now0 = time.time()

    def mk(t):
        return dict(tm, ticker=t, status=state["status"], result=state["result"], close_time=time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now0 + 600)), **dict(book, yes_ask_dollars=state["ask"], yes_bid_dollars=state["bid"]))

    def fake_k(path):
        if path.startswith("/markets/"):
            return {"market": mk(path.split("/markets/")[1])}
        if "tickers=" in path:
            return {"markets": [mk(t) for t in path.split("tickers=")[1].split(",")]}
        return {}
    with _db(CT), _patched((CT, "_kalshi", fake_k), (CT, "proxy", lambda c, fresh=False: {"price": 61600.0}),
                           (CT, "realised_vol", lambda c: 7e-5), (CT, "_fee", lambda t, c: 7.0 * c / 100 * (1 - c / 100))):
        T = "KXBTC15M-TEST-00"
        a = CT.paper_open(T, "BTC", "yes", 3)
        pos = CT.paper_positions()
        state.update(ask="0.4800", bid="0.4700")
        c1 = CT.paper_close(a["id"])
        b = CT.paper_open(T, "BTC", "no", 1)
        refused = []
        for args in ((T, "ETH", "yes", 1), ("KXNFLGAME-X", "BTC", "yes", 1), (T, "BTC", "maybe", 1), (T, "BTC", "yes", 0)):
            try:
                CT.paper_open(*args)
                refused.append(False)
            except ValueError:
                refused.append(True)
        with _patched((CT, "MAX_OPEN", 1)):
            try:
                CT.paper_open(T, "BTC", "yes", 1)
                refused.append(False)
            except ValueError:
                refused.append(True)
        state["ask"] = None
        try:
            CT.paper_open(T, "BTC", "yes", 1)
            refused.append(False)
        except ValueError:
            refused.append(True)
        state.update(status="finalized", result="no")
        with contextlib.closing(CT._conn()) as cc, cc:
            cc.execute(f"UPDATE {CT.PAPER_TABLE} SET close_ts = ? WHERE id = ?", (now0 - 200, b["id"]))
        CT.settle_paper(now0)
        rec = CT.paper_positions()["record"]
        ev = (pos["open"] or [{}])[0].get("eval") or {}
    fee3_45, fee3_47, fee1_56 = math.ceil(7 * 3 * .45 * .55), math.ceil(7 * 3 * .47 * .53), math.ceil(7 * .56 * .44)
    want = (47 * 3 - fee3_47) - (45 * 3 + fee3_45) + (100 - 56 - fee1_56)
    ck("CXV2-PAPER a paper buy is at the ask the SERVER reads (3 YES at 45c, fee rounded up per order: "
       f"{fee3_45}c), a paper sell at the bid (47c, fee {fee3_47}c), a position left open settles at Kalshi's result with "
       "no fee (1 NO at 56c settled NO); the record is SQL sums of exactly that",
       a["entry_c"] == 45.0 and c1["exit_c"] == 47.0 and b["entry_c"] == 56.0 and rec["positions"] == 2
       and abs(rec["pnl_c"] - want) < 1e-9 and rec["fees_c"] == fee3_45 + fee3_47 + fee1_56, f"{a} {c1} {b} {rec} want={want}")
    ck("CXV2-PAPER refused: another coin's or another series' ticker, a side that is not yes/no, a zero quantity, an "
       f"open position past MAX_OPEN ({CT.MAX_OPEN}), and a one-sided book with no ask to buy at", refused == [True] * 6,
       str(refused))
    ck("CXV2-HOLD hold, sell and the other side judged apart: selling is the bid less the exit fee, holding is 100 x the "
       "model's chance of the side (and Kalshi's mid beside it), and buying the other side is its own fee-in edge at its "
       "own ask -- never implied by the sell",
       ev.get("close_value_c") == 44 * 3 - math.ceil(7 * 3 * .44 * .56) and ev.get("bid_c") == 44.0
       and abs(ev["hold_value_model_c"] - round(100 * ev["p_side"] * 3, 1)) < 0.06 and ev["hold_value_kalshi_mid_c"] == 44.5 * 3
       and ev["verdict_by_model"] == ("close" if ev["close_value_c"] > ev["hold_value_model_c"] else "hold")
       and ev["other_side"]["side"] == "no" and ev["other_side"]["ask_c"] == 56.0
       and ev["other_side"]["worth_it_by_model"] == (ev["other_side"]["edge_c"] is not None and ev["other_side"]["edge_c"] >= 3),
       str(ev)[:300])
    # ---- the intrawindow record, by hand ----
    with _db(CT) as path:
        rows = [("T1", 600, 0.7, 50.0, 52.0), ("T1", 300, 0.7, 60.0, 62.0), ("T2", 600, 0.2, 40.0, 42.0),
                ("T2", 300, 0.2, 68.0, 70.0), ("T3", 600, 0.5, 49.0, 51.0), ("T3", 300, 0.5, 49.0, 51.0)]
        with contextlib.closing(sqlite3.connect(path)) as cc, cc:
            for i, (t, lk, p, yb, ya) in enumerate(rows):
                cc.execute(f"""INSERT INTO {CT.TABLE} (ticker, look_s, coin, kind, logged_ts, close_ts, tau_s, p_model,
                               yes_bid, yes_ask, no_ask) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           (t, lk, "BTC", "m15", 1000.0 + i, 2000.0 + i // 2, lk, p, yb, ya, 100 - yb))
        iw = CT.record_extra()["intrawindow"]
    f = lambda x: 7 * x * (100 - x) / 10000.0                                         # noqa: E731
    want = [(60 - f(60)) - (52 + f(52)), (30 - f(30)) - (60 + f(60))]
    ck("CXV2-INTRA the intrawindow record enters at the 10-minute look's ask on the side the rule picks and sells at the "
       "5-minute look's bid of THAT side (NO bid = 100 - YES ask), fee on both legs; a market with no edge is no trade",
       iw.get("trades") == 2 and abs(iw["total_c"] - round(sum(want), 1)) < 1e-9, f"{iw} {want}")

    # ---- the movement research harness (research/crypto_moves.py) ----
    sys.path.insert(0, os.path.join(_ROOT, "research"))
    import crypto_moves as M
    t0 = 1794528000
    raw = []
    for i in range(420):
        if i % 37 == 5:
            continue                                                            # gaps: minutes with no trade
        c = 100.0 * math.exp(0.002 * math.sin(i / 7.0) + 0.0007 * math.cos(i / 3.1))
        raw.append([t0 + 60 * i, c * 0.999, c * 1.001, c * (1 + 0.0002 * math.sin(i)), c, 1.0 + (i * 13) % 7])
    g = M.grid(raw, t0, t0 + 420 * 60)
    f1 = M.features(g)
    k = 360
    raw2 = [r if r[0] <= t0 + 60 * k else [r[0], r[1] * 1.07, r[2] * 1.07, r[3] * 0.9, r[4] * 1.05, r[5] * 9] for r in raw]
    f2 = M.features(M.grid(raw2, t0, t0 + 420 * 60))
    ck("CXM-LEAK every research feature at minute t uses minutes up to t only: rewriting every later candle leaves every "
       "earlier feature identical (and the later ones move)", f1[:k + 1] == f2[:k + 1] and f1[k + 5] != f2[k + 5]
       and all(x is None for x in f1[:M.WARM]) and f1[M.WARM] is not None)
    ck("CXM-SAME production's direction features are the research's own (crypto_terminal.move_grid / move_features equal "
       "on a series with gaps)", CT.move_grid(raw, t0, t0 + 420 * 60) == g and CT.move_features(g) == f1)
    dsrc = inspect.getsource(M.direction)
    ck("CXM-SPLIT the harness fits on development minutes only (before 2026-11-13), forward-chains inside them, and "
       "scores the held-out fortnight once; the targets are the closes h minutes AFTER the feature minute",
       M.SPLIT == 1794528000 and M.END == 1795737600 and "r[0] < SPLIT" in dsrc and "r[0] >= SPLIT" in dsrc
       and "day(r[0]) < 14" in dsrc and "g[i + hh][4] > c" in inspect.getsource(M.rows_for))
    mv = json.loads(_read("research", "data", "crypto", "moves.json"))
    d = mv["direction"]
    ck("CXM-VERDICT by the rule written first: the all-feature logistic passed at 1, 3 and 5 minutes (+0.7 / +1.4 / "
       "+2.0 millinats a minute, z 2.8 / 3.8 / 3.6, every coin) -- a 1-3 point tilt; the settlement drift did not ship; "
       "trading on the model inside the window lost on held-out quotes",
       all(d[h]["passed"] for h in ("1", "3", "5")) and abs(d["1"]["gain_vs_rw_nats"] - 0.000707) < 1e-9
       and abs(d["5"]["gain_vs_rw_nats"] - 0.002044) < 1e-9 and mv["settlement_drift"]["ships"] is False
       and mv["intrawindow"]["profitable"] is False and mv["intrawindow"]["heldout"]["per_trade_c"] < 0)
    # the live direction applies the committed weights to the last COMPLETE minute's features
    end = int(time.time()) // 60 * 60 - 60
    base = [[t, 100.0 * math.exp(0.003 * math.sin(t / 420.0)), 0, 0, 0, 1.0 + (t // 60) % 5]
            for t in range(end - 900 * 60, end + 120, 60)]
    cand = [[t, c * 0.999, c * 1.001, c, c, v] for t, c, _a, _b, _c, v in base]

    def fake_get(url, timeout=6):
        q = dict(x.split("=") for x in url.split("?")[1].split("&"))
        s_, e_ = (datetime_ts(q["start"]), datetime_ts(q["end"]))
        return [r for r in cand if s_ <= r[0] <= e_][::-1]
    CT._cache.pop(("direction", "BTC"), None)
    with _patched((CT, "_get", fake_get)):
        dn = CT.direction_now("BTC")
    CT._cache.pop(("direction", "BTC"), None)
    gg = M.grid([r for r in cand if r[0] <= end], end - 899 * 60, end + 60)
    ff = M.features(gg)[-1]
    dd = CT.research()["direction"]["5"]
    z = dd["weights"][0] + sum(wt * ff[n] for wt, n in zip(dd["weights"][1:], dd["weight_names"][1:]))
    ck("CXM-LIVE the panel's live direction is the committed 5-minute model on the last complete minute (the minute in "
       "progress dropped), its features the research's own",
       dn.get("as_of") == end + 60 and abs(dn["horizons"]["5"]["p_up"] - round(1 / (1 + math.exp(-z)), 4)) < 1e-12,
       str(dn)[:200])
    run_v3(ck, CT)


def run_v3(ck, CT):
    """v2 stage 2 (2026-11-27): Kalshi's dollar Quick Order, the final-reversal panel, and the hybrid harness
    (research/crypto_hybrid.py, rule df45623)."""
    n = CT.contracts_for_dollars(1000, 45)
    ck("CXH-FEE a $10 Quick Order at 45c buys 21 contracts: 21 x 45c + the order's fee (0.07 x 21 x .45 x .55 dollars "
       "rounded UP to the cent, 37c) = $9.82, and a 22nd would cost $10.28; the fee is per ORDER, never per contract rounded",
       n == 21 and CT.order_fee_c(45, 21) == 37.0 and 22 * 45 + CT.order_fee_c(45, 22) > 1000
       and CT.order_fee_c(50, 1) == 2.0 and CT.order_fee_c(50, 100) == 175.0 and CT.contracts_for_dollars(1000, 0) == 0)
    # the final-reversal panel on a fixed window
    T = 1795800600
    bars = [[T - 900 + 60 * i, 100.0 + 0.05 * i + 0.02, 100.0 + 0.05 * i - 0.04 - (0.2 if i == 3 else 0), 100.0 + 0.05 * i]
            for i in range(9)]
    feat = {k: 0.1 for k in ("m1", "m3", "m5", "m15", "acc", "rsi", "macd", "ema_gap", "px_ema9", "vol_reg", "vol_z",
                             "range_pos")}
    feat["sig"] = 0.0008
    direc = {"as_of": T - 900 + 60 * 9, "bars": bars, "feat": feat}
    m15 = {"close_ts": T, "rule": {"op": ">=", "strike": 100.2}, "yes_ask": 45.0, "yes_bid": 44.0, "no_ask": 56.0, "no_bid": 55.0}
    F = CT.final_reversal(m15, direc)
    x, lo, tau = bars[-1][3], min(b[2] for b in bars), T - direc["as_of"]
    sig = CT.VOL_MULT * 0.0008 / math.sqrt(60.0)
    sd = sig * math.sqrt(tau)
    want_low = 2 * CT._phi(math.log(x / lo) / sd) - 1
    R = (CT._RESEARCH.get("hybrid") or {}).get("r1_final_reversal") or {}
    w = R["final_low"]["weights"]
    z = w[0] + w[1] * CT._logit(want_low) + sum(wi * feat[k] for wi, k in zip(w[2:], R["final_low"]["weight_names"][2:]))
    ck("CXH-NULL the final-reversal panel's null is the walk's first passage (P no new low = 2 Phi(ln(x/L)/sd) - 1, the "
       "research's own sigma), the challenger is the committed logistic on its logit and the features, and the round "
       "trip names the bid a $10 buy needs after both fees",
       F and abs(F["null"]["final_low"] - round(want_low, 4)) < 1e-12 and abs(F["model"]["final_low"] - round(1 / (1 + math.exp(-z)), 4)) < 1e-12
       and F["leg"] == "down" and F["round_trip"]["yes"]["contracts"] == 21
       and 21 * F["round_trip"]["yes"]["breakeven_bid_c"] - CT.order_fee_c(F["round_trip"]["yes"]["breakeven_bid_c"], 21)
       >= F["round_trip"]["yes"]["cost_c"], str(F)[:300])
    sv = [CT._survive(d, 0.002, 1e-4, t) for d, t in ((0.0, 1e-9), (0.0, 60), (0.0, 600), (0.001, 600), (0.0021, 60))]
    ck("CXH-SURVIVE the chance a move persists (a reflected walk's drawdown staying under delta) is 1 at once, falls with "
       "time and with the drawdown already taken, and is 0 past delta", abs(sv[0] - 1) < 1e-6 and sv[0] > sv[1] > sv[2] > sv[3]
       and sv[4] == 0.0, str(sv))
    # the R1 harness on a synthetic tape: later bars rewritten, earlier looks unchanged
    sys.path.insert(0, os.path.join(_ROOT, "research"))
    import crypto_moves as M
    import crypto_hybrid as HY
    d0 = tempfile.mkdtemp(prefix="cxh-")
    t0 = M.START - 6 * 3600
    rows = [[t0 + 60 * i, 0, 0, 0, 0, 1.0] for i in range(6 * 60 + 2 * 1440 + 60)]
    for r in rows:
        c = 100.0 * math.exp(0.002 * math.sin(r[0] / 700.0) + 0.001 * math.sin(r[0] / 170.0))
        r[1], r[2], r[3], r[4] = c * 0.9995, c * 1.0005, c, c
    cut = M.START + 86400 + 900 * 3 + 60 * 6
    rows2 = [r if r[0] < cut else [r[0], r[1] * 0.97, r[2] * 1.03, r[3], r[4] * 1.01, r[5]] for r in rows]
    try:
        out = []
        for rr in (rows, rows2):
            with open(os.path.join(d0, "cbx_TST.json"), "w") as fh:
                json.dump(rr, fh)
            with _patched((M, "RAW", d0), (M, "END", M.START + 2 * 86400)):
                out.append(HY.r1_rows("TST"))
    finally:
        import shutil
        shutil.rmtree(d0, ignore_errors=True)
    a = [(r["t"], r["p"], r["f"]) for r in out[0] if r["t"] <= cut]
    b = [(r["t"], r["p"], r["f"]) for r in out[1] if r["t"] <= cut]
    ck("CXH-LEAK the final-reversal harness reads only bars that END by the look: rewriting every later bar leaves every "
       "earlier look's null and features identical while the labels may move", a == b and len(a) > 100
       and any(x["y"] != y["y"] for x, y in zip(out[0], out[1]) if x["t"] <= cut), str(len(a)))
    hs = inspect.getsource(HY.r2_markets) + inspect.getsource(HY.run_strategy)
    ck("CXH-R2 the hybrid harness decides on the book at or before t and fills at the NEXT minute's candle (t, t+60], "
       "sizes $10 with production's Quick-Order function and Kalshi's per-order fee",
       "b[0] <= t" in hs and "t < b[0] <= t + 60" in hs and "CT.contracts_for_dollars(1000, px)" in hs
       and "CT.order_fee_c(px, n)" in hs)
    hj = json.loads(_read("research", "data", "crypto", "hybrid.json"))
    r1, r2 = hj["r1_final_reversal"], hj["r2_hybrid"]
    ck("CXH-VERDICT by the rule written first: the walk's null was mis-calibrated and the challenger beat it for the "
       "final low, the final high and the touch; NO strategy was profitable on held-out windows -- the hybrid "
       "+4.2c a window +/- 17.8 (not past 2 SE), settlement-only, trading-only and take-profit all lost",
       all(r1[k]["passed"] for k in ("final_low", "final_high", "touch"))
       and abs(r2["heldout"]["S4"]["per_window_c"] - 4.239) < 1e-9 and not any(r2["heldout"][s]["profitable"] for s in
                                                                                ("S1", "S2", "S3", "S4"))
       and all(r2["heldout"][s]["per_window_c"] < 0 for s in ("S1", "S2", "S3")) and r2["chosen_on_dev"]["theta_c"] == 4)
    js = _read("static", "crypto.js")
    ck("CXH-UI Beginner Mode is the default: dollars (a $10 Quick Order posted as dollars, the server sizing it), the "
       "cash-out value at the bid after the fee, P/L in $ and %, plain-English movement and final-reversal lines, and the "
       "technical panels one tap away",
       'mode: "beginner"' in js and "setMode(st.mode)" in js and "dollars: dol" in js and 'TECH = ["cxChartBar", "cxMomentum", "cxBoard", "cxRecord"]' in js
       and "cash out now (bid" in js and "The low may have been the final one" in js and "Paper only." in js
       and "dollars" in inspect.signature(CT.paper_open).parameters)
    run_v4(ck, CT)
    run_v5(ck, CT)
    run_v6(ck, CT)


def run_v4(ck, CT):
    """v2 stage 3 (2026-11-27): the forecasts' live record (cx_preds), the Kalshi book recorder (cx_book), and
    research/crypto_regimes.py (rule a1057e7)."""
    T = 1795800600.0
    now = T - 600
    direc = {"as_of": now - 20, "horizons": {"1": {"p_up": 0.52}, "3": {"p_up": 0.47}, "5": {"p_up": 0.55}},
             "trailing_5m": 1}
    final = {"level": 100.0, "window_low": 99.5, "window_high": 100.4, "null": {"final_low": 0.6, "final_high": 0.3,
                                                                               "touch": 0.5},
             "model": {"final_low": 0.7, "final_high": 0.25, "touch": 0.45}}
    row = {"close_ts": T, "rule": {"strike": 100.2}}
    bars = {int(now - 20 + 60 * i): (100.0 + h_, 100.0 - l_, 100.0 + c_) for i, (h_, l_, c_) in
            enumerate([(0.1, 0.1, 0.05), (0.3, 0.1, -0.02), (0.25, 0.2, 0.1), (0.2, 0.3, 0.0), (0.5, 0.1, 0.3),
                       (0.1, 0.1, 0.0), (0.1, 0.1, 0.0), (0.1, 0.1, 0.0), (0.1, 0.1, 0.0), (0.1, 0.1, 0.0), (0.1, 0.1, 0.0)])}
    lab = CT.label_pred(bars, now - 20, 100.0, 99.5, 100.4, 100.2, T)
    ck("CXP-LABEL a forecast is graded only off bars that START at or after its minute: direction at 1/3/5 minutes "
       "from the closes ending then, the final low / high and the touch over the rest of the window",
       lab == {"y1": 1, "y3": 1, "y5": 1, "y_fl": 1, "y_fh": 0, "y_tc": 1}, str(lab))
    with _db(CT) as path:
        with contextlib.closing(CT._conn()) as c, c:
            ok1 = CT.log_pred(c, "BTC", 600, row, direc, final, now)
            ok2 = CT.log_pred(c, "BTC", 300, row, dict(direc, as_of=now + 5), final, now)
            ok3 = CT.log_pred(c, "ETH", 60, dict(row, close_ts=now + 59), direc, final, now)
        with _patched((CT, "_candles_between", lambda coin, t0, t1: bars)):
            g = CT.grade_preds(T + 200)
        rp = CT.record_preds()
        with contextlib.closing(sqlite3.connect(path)) as c:
            got = c.execute(f"SELECT y1, y3, y5, y_fl, y_fh, y_tc, graded FROM {CT.PRED_TABLE}").fetchall()
    llf = lambda p, y: -(y * math.log(p) + (1 - y) * math.log(1 - p))                  # noqa: E731
    ck("CXP-LOG a forecast is logged only from a COMPLETE minute before now and a settlement window or more before the "
       "close, graded after the close, and its record is SQL sums: direction against a coin flip, each final question "
       "model against the walk's null on the same row",
       ok1 and not ok2 and not ok3 and g == 1 and got == [(1, 1, 1, 1, 0, 1, 1)]
       and abs(rp["direction_1"]["diff_nats"] - round(llf(0.52, 1) - math.log(2), 5)) < 1e-9
       and abs(rp["final_low"]["diff_nats"] - round(llf(0.7, 1) - llf(0.6, 1), 5)) < 1e-9
       and abs(rp["touch"]["diff_nats"] - round(llf(0.45, 1) - llf(0.5, 1), 5)) < 1e-9, f"{ok1} {ok2} {ok3} {g} {got} {rp}")
    ck("CXB-TICKER the 15-minute ticker the recorder reads is built from the close in Eastern time "
       "(KXBTC15M-26NOV270445-45 closes 09:45 UTC on 2026-11-27, read live)",
       CT.m15_ticker("KXBTC15M", 1795772700) == "KXBTC15M-26NOV270445-45")
    import urllib.error
    calls = {"n": 0, "mode": "ok", "px": 0.45}

    def fake_get(url, timeout=6):
        calls["n"] += 1
        if calls["mode"] == "429":
            raise urllib.error.HTTPError(url, 429, "too many", {}, None)
        tk = url.split("tickers=")[1].split(",")
        return {"markets": [{"ticker": t, "yes_bid_dollars": f"{calls['px'] - 0.01:.4f}",
                             "yes_ask_dollars": f"{calls['px']:.4f}", "yes_bid_size_fp": "10", "yes_ask_size_fp": "12"}
                            for t in tk]}
    with _db(CT) as path, _patched((CT, "_get", fake_get), (CT, "_book", {"every": CT.BOOK_EVERY_S, "last": {}, "calls": 0,
                                                                          "r429": 0, "rows": 0})):
        t0 = 1795800000.0
        a = CT.book_tick(t0)
        b = CT.book_tick(t0 + 5)                           # unchanged book: no row
        calls["px"] = 0.47
        cnew = CT.book_tick(t0 + 10)                        # moved: a row per market
        d = CT.book_tick(t0 + 75)                          # unchanged but a minute on: the heartbeat
        calls["mode"] = "429"
        ev = []
        for i in range(6):
            CT.book_tick(t0 + 100 + i)
            ev.append(CT._book["every"])
        calls["mode"] = "ok"
        for i in range(30):
            CT.book_tick(t0 + 200 + i)
        back = CT._book["every"]
        n_calls = calls["n"]
        import gc
        gc.collect()
        m0 = _rss_mb()
        for i in range(3000):
            calls["px"] = 0.40 + 0.01 * (i % 7)
            CT.book_tick(t0 + 400 + 5 * i)
        gc.collect()
        m1 = _rss_mb()
        with contextlib.closing(sqlite3.connect(path)) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            nb = c.execute(f"SELECT COUNT(*) FROM {CT.BOOK_TABLE}").fetchone()[0]
        bpr = os.path.getsize(path) / max(nb, 1)
        with _patched((CT, "BOOK_MAX_ROWS", 1000)):
            CT.prune(t0 + 400 + 5 * 3000)
        with contextlib.closing(sqlite3.connect(path)) as c:
            capped = c.execute(f"SELECT COUNT(*) FROM {CT.BOOK_TABLE}").fetchone()[0]
    ck("CXB-PACE the book recorder makes ONE read a pass, writes a market only when its book moved or on the minute's "
       "heartbeat, doubles its spacing on each 429 up to 60 s and eases back to 5 s on success",
       (a, b, cnew, d) == (5, 0, 5, 5) and ev == [10.0, 20.0, 40.0, 60.0, 60.0, 60.0] and back == CT.BOOK_EVERY_S
       and n_calls == 40, f"{(a, b, cnew, d)} {ev} {back} {n_calls}")
    ck(f"CXB-MEM 3,000 recorder passes leave the worker flat (under 5 MB) and the table bounded by its row cap; measured "
       f"{bpr:.0f} bytes a snapshot, so the {CT.BOOK_MAX_ROWS:,}-row cap holds the table under "
       f"{CT.BOOK_MAX_ROWS * bpr / 1e6:.0f} MB of the shared 1 GB disk (10 days kept)",
       m1 - m0 < 5.0 and capped == 1000 and nb > 3000 and CT.BOOK_MAX_ROWS * bpr < 45e6, f"{m1 - m0:.1f} {nb} {capped} {bpr}")
    sys.path.insert(0, os.path.join(_ROOT, "research"))
    import crypto_regimes as RG
    snaps = [(100, 40.0, 42.0), (104, 41.0, 43.0), (112, 44.0, 46.0), (131, 45.0, 47.0), (170, 50.0, 52.0)]
    fills = [RG.fill_at(snaps, 100, dd) for dd in RG.DELAYS]
    ck("CXR-DELAY a delayed fill is the first book snapshot at or after decision + delay, never one before it, and the "
       "decision sees only the latest snapshot at or before its moment",
       fills == [snaps[2], snaps[3], snaps[3], snaps[4]] and RG.quote_at(snaps, 111) == snaps[1]
       and RG.quote_at(snaps, 99) is None and "h = g[j - 299:j + 1]" in inspect.getsource(RG.r3_rows))
    rj = json.loads(_read("research", "data", "crypto", "regimes.json"))
    lab = rj["r3_final_reversal_models"]["labels"]
    z = rj["r4_delay"]
    ck("CXR-VERDICT by the rule written first: neither the regime-switching null nor the path simulation beat R1's "
       "challenger on any question (it stays on the panel); with fills at the very quote decided on, the hybrid made "
       "+41.0c a window +/- 17.1 held out -- a ceiling -- against +4.2c +/- 17.8 a minute later",
       all(not lab[k]["compare"][f"{m}_vs_challenger"]["beats"] for k in ("final_low", "final_high", "touch")
           for m in ("regime", "pathsim"))
       and abs(z["zero_delay"]["heldout"]["S4"]["per_window_c"] - 40.99) < 0.01
       and abs(z["one_minute_delay_heldout"]["S4"]["per_window_c"] - 4.239) < 1e-9)


def run_v5(ck, CT):
    """2026-11-28: the book recorder moves to the PC. On the server it would share the app's Kalshi budget with
    every board, so it is OFF there by default; the PC records on its own IP and uploads through its token door."""
    src = _read("crypto_terminal.py")
    sb = inspect.getsource(CT.start_background)
    ck("CXB2-OFF the server's book recorder is off unless VIGIL_CRYPTO_BOOK=on (unset is off), the forecast logging "
       "(cx_preds, one look a window) is not tied to it, and the background owner starts the recorder thread only when on",
       CT._book_on({}) is False and CT._book_on({"VIGIL_CRYPTO_BOOK": "on"}) is True
       and CT._book_on({"VIGIL_CRYPTO_BOOK": "off"}) is False and "if BOOK_ON:" in sb and "_book_loop" in sb
       and "BOOK_ON" not in inspect.getsource(CT.log_tick))
    import pc_loop
    import pc_worker
    lane = inspect.getsource(pc_loop._book_lane)
    main_src = inspect.getsource(pc_loop.main)
    bm = inspect.getsource(pc_worker.book_main)
    ck("CXB2-LANE the PC keeps one `pc_worker.py --book` process (restarted if it exits, stopped for an update), "
       "niced, its database under pc-simcache (gitignored), switchable off in vigil-pc.cfg",
       '"--book"' in lane and "_book_lane" in main_src and "_stop_book()" in main_src and "os.nice(10)" in bm
       and '"crypto_book"' in bm and "pc-simcache" in inspect.getsource(pc_worker._book_paths)
       and "pc-simcache/" in _read(".gitignore"))
    posted = []

    def fake_api(url, tok, path, data=None, headers=None, timeout=90):
        posted.append((path, len(json.loads(data)["rows"])))
        return {"ok": True}
    with _db(CT) as path, _patched((CT, "book_tick", lambda now: 0)):
        t0 = 1795800000
        rows = [(ci, t0 + 5 * i, t0 + 900 - t0 % 900 if False else (t0 // 900 + 1) * 900, 450, 460, 10, 12)
                for i in range(1200) for ci in range(5)]
        with contextlib.closing(sqlite3.connect(path)) as c, c:
            c.executemany(f"INSERT OR IGNORE INTO {CT.BOOK_TABLE} VALUES (?,?,?,?,?,?,?)", rows)
        st = {}
        waits = [pc_worker.book_once(st, t0 + 10 * k, "u", "t", api=fake_api) for k in range(40)]
    sizes = [n for p_, n in posted]
    ck("CXB2-PACE the lane sleeps the recorder's own backoff between passes (5 s, doubled on a 429), uploads at most "
       f"every {CT.BOOK_UPLOAD_S} s and at most {CT.BOOK_UPLOAD_MAX_ROWS:,} rows a call, oldest first, and never "
       "re-sends what the server took",
       all(w == CT._book["every"] for w in waits) and sizes and max(sizes) <= CT.BOOK_UPLOAD_MAX_ROWS
       and len(posted) == 2 and sum(sizes) == 6000 and all(p_ == "/api/crypto/book" for p_, _n in posted), str(posted))
    import app as _app
    client = _app.app.test_client()
    t_ok = int(time.time()) - 30          # its window from ITS OWN time: (now // 900 + 1) * 900 failed in the first 30 s of a window
    good = [[0, t_ok, (t_ok // 900 + 1) * 900, 450, 460, 3, 4]]
    bad = [[9, int(time.time()), 0, 1, 1, 1, 1], [0, int(time.time()) + 3600, 0, 1, 1, 1, 1], ["x"]]
    with _db(CT) as path, _patched((_app, "_SIM_TOKEN", "guard-token"), (_app, "_pc_note_seen", lambda *a, **k: None)):
        r_none = client.post("/api/crypto/book", json={"rows": good})
        r_bad = client.post("/api/crypto/book", json={"rows": good}, headers={"X-Sim-Token": "wrong"})
        r_many = client.post("/api/crypto/book", json={"rows": good * (CT.BOOK_UPLOAD_MAX_ROWS + 1)},
                             headers={"X-Sim-Token": "guard-token"})
        r_ok = client.post("/api/crypto/book", json={"rows": good + bad}, headers={"X-Sim-Token": "guard-token"})
        with contextlib.closing(sqlite3.connect(path)) as c:
            n = c.execute(f"SELECT COUNT(*) FROM {CT.BOOK_TABLE}").fetchone()[0]
    ck("CXB2-DOOR the upload is token-only (none or a wrong token: 403), capped at 512 KB and "
       f"{CT.BOOK_UPLOAD_MAX_ROWS:,} rows (413), and every row is checked (an unknown coin, a snapshot from the future, "
       "a malformed row are dropped)",
       r_none.status_code == 403 and r_bad.status_code == 403 and r_many.status_code == 413 and r_ok.status_code == 200
       and r_ok.get_json() == {"ok": True, "inserted": 1, "dropped": 3} and n == 1,
       f"{r_none.status_code} {r_bad.status_code} {r_many.status_code} {r_ok.get_json()} {n}")
    js = _read("static", "crypto.js")
    ck("CXB2-TAB the tab says where the book is recorded and that nothing is recorded while the PC is off",
       "when the PC is off nothing is recorded" in js and '"recorded_on": "server" if BOOK_ON else "pc"' in src)


def run_v6(ck, CT):
    """UI v3 (2026-11-28): market windows from Kalshi's metadata, window-only candles, the paper position's value
    over time, "meaningful" in price and contract value, the movement outlook (direction bars, scenarios), and the
    tab's pure chart helpers (through node when it is installed)."""
    import shutil
    import subprocess
    from datetime import datetime, timezone
    fx = json.loads(_read("research", "data", "crypto", "rule_fixtures.json"))["markets"]
    m15 = dict(next(m for m in fx if m["ticker"].startswith("KXBTC15M")))
    lad = next(m for m in fx if m["ticker"].startswith("KXBTCD"))
    now = 1795832000.0                                     # 2026-11-28 02:13 UTC = 21:13 ET Nov 27
    et17 = datetime(2026, 11, 28, 22, 0, tzinfo=timezone.utc).timestamp()      # 17:00 EST Nov 28

    def ladder(ev, strikes, close):
        out = []
        for k in strikes:
            m = dict(lad)
            m.update(ticker=f"{ev}-T{k:.2f}", event_ticker=ev, floor_strike=k, close_time=datetime.fromtimestamp(close, timezone.utc)
                     .strftime("%Y-%m-%dT%H:%M:%SZ"), open_time=datetime.fromtimestamp(close - 90000, timezone.utc)
                     .strftime("%Y-%m-%dT%H:%M:%SZ"), yes_bid_dollars="0.40", yes_ask_dollars="0.42",
                     rules_primary=lad["rules_primary"].replace("69799.99", f"{k:.2f}"), expiration_value="60124.99")
            out.append(m)
        return out
    state = {"events": [], "fail_ladder": False, "calls": 0}

    def fake_k(path):
        state["calls"] += 1
        if path.startswith("/events?"):
            return {"events": state["events"]}
        if "status=settled" in path:
            s = dict(m15)
            s.update(expiration_value="61700.5", result="yes")
            return {"markets": [s]}
        if path.startswith("/markets?event_ticker="):
            ev = path.split("=")[1].split("&")[0]
            if state["fail_ladder"]:
                return {"markets": []}
            return {"markets": ladder(ev, [60049.99, 60099.99, 60149.99, 60199.99], et17 if ev.endswith("17") else now + 1800)}
        return {}
    ev_daily = {"event_ticker": "KXBTCD-26NOV2817", "strike_date": "2026-11-28T22:00:00Z"}
    ev_other = {"event_ticker": "KXBTCD-26NOV2722", "strike_date": "2026-11-28T03:00:00Z"}
    ev_far = {"event_ticker": "KXBTCD-26DEC0417", "strike_date": "2026-12-04T22:00:00Z"}
    with _patched((CT, "_kalshi", fake_k)):
        CT._cache.clear()
        state["events"] = [ev_other, ev_daily, ev_far]
        d1 = CT.daily_event("BTC", now)
        CT._cache.clear()
        state["events"] = [ev_other, ev_far]
        d2 = CT.daily_event("BTC", now)
        CT._cache.clear()
        d3 = CT.daily_event("BTC", et17 + 60)             # after the 17:00 close: Dec 4 is more than 26 h away
    ck("CXU-DAILY Daily is the event Kalshi lists closing at 5 PM Eastern within 26 hours, read from the events' own "
       "close (strike_date), never a clock rule: present, absent (only the 10 PM hourly and next week's Friday), and "
       "after the close", d1 == ("KXBTCD-26NOV2817", et17) and d2 is None and d3 is None, f"{d1} {d2} {d3}")

    row15 = {"ticker": m15["ticker"], "close_ts": now + 300, "open_ts": now - 600, "rule": {"strike": 61659.25, "op": ">="}}
    hourly = {"event": "KXBTCD-26NOV2722", "close_ts": now + 2700,
              "markets": [{"ticker": f"KXBTCD-26NOV2722-T{k}", "rule": {"strike": k, "op": ">"}, "open_ts": now - 900}
                          for k in (60049.99, 60099.99, 60149.99)]}
    with _patched((CT, "_kalshi", fake_k)):
        CT._cache.clear()
        state["events"] = [ev_other, ev_daily]
        state["fail_ladder"] = False
        W1 = CT.windows("BTC", {"m15": row15, "hourly": hourly}, 60120.0, 9e-5, now)
        CT._cache.clear()
        state["fail_ladder"] = True
        W2 = CT.windows("BTC", {"m15": row15, "hourly": hourly}, 60120.0, 9e-5, now)
        n0 = state["calls"]
        W3 = CT.windows("BTC", {"m15": row15, "hourly": hourly}, 60120.0, 9e-5, now)
        refetched = state["calls"] > n0
        CT._cache.clear()
    pv, ph, dw = W1["m15"].get("previous") or {}, W1["hourly"].get("previous") or {}, W1.get("daily") or {}
    ck("CXU-WIN each tab's window comes from Kalshi's metadata: the 15-minute market's own open and close, the previous "
       "15-minute market with ITS OWN strike and official settlement, the previous hour's strike the one nearest its "
       "settlement value (stated), the daily market's open and close from its own fields, and a daily ladder that "
       "could not be read says so (daily_event kept) and is fetched again next board, never cached as absent",
       W1["m15"]["open_ts"] == now - 600 and W1["m15"]["close_ts"] == now + 300 and pv.get("strike") == 61659.25
       and pv.get("settle") == 61700.5 and pv.get("result") == "yes" and ph.get("strike") == 60099.99
       and "nearest its settlement" in ph.get("strike_basis", "") and ph.get("close_ts") == now + 2700 - 3600
       and dw.get("close_ts") == et17 and dw.get("open_ts") == et17 - 90000 and dw.get("event") == "KXBTCD-26NOV2817"
       and W1["hourly"]["target"]["rule"]["strike"] == 60099.99 and W2.get("daily") is None
       and W2.get("daily_event") == "KXBTCD-26NOV2817" and refetched and W3.get("daily") is None,
       f"{pv} {ph} {dw.get('close_ts')} {W2.get('daily_event')} {refetched}")

    # window candles: one call, at most 300 bars, a bounded cache key whatever the window
    calls = []

    def fake_get(url):
        calls.append(url)
        return [[t, 1.0, 2.0, 1.5, 1.7, 3.0] for t in range(int(now) - 6000, int(now), 60)][::-1]
    with _patched((CT, "_get", fake_get)):
        CT._cache.clear()
        a = CT.window_candles("BTC", 60, now - 900, now)
        b = CT.window_candles("BTC", 60, now - 3600, now + 3600)
        keys = [k for k in CT._cache if k and k[0] == "wcandles"]
        try:
            CT.window_candles("BTC", 60, now - 90000, now)
            long_ok = False
        except ValueError:
            long_ok = True
        try:
            CT.window_candles("BTC", 15, now - 900, now)
            g15 = False
        except ValueError:
            g15 = True
        CT._cache.clear()
    ck("CXU-CANDLES the chart's window candles are the window only ([open, close), one call, <= 300 bars; a longer "
       "window or a granularity Coinbase does not serve is refused), and the cache key is (coin, granularity) whatever "
       "the window, so it cannot grow as windows roll",
       a and all(now - 900 - 60 <= r[0] < now for r in a) and len(b) <= 300 and keys == [("wcandles", "BTC", 60)]
       and long_ok and g15 and len(calls) == 2, f"{len(a)} {len(b)} {keys} {long_ok} {g15} {len(calls)}")

    # the position's value over time: Kalshi's bids sold after the per-order fee, NO's bid the mirror of YES's ask
    cs = {"candlesticks": [{"end_period_ts": int(now) - 600 + 60 * i, "yes_bid": {"close_dollars": "0.30"},
                            "yes_ask": {"close_dollars": "0.33"}} for i in range(1500)]}     # 1,500 minutes: both caps bite
    with _patched((CT, "_kalshi", lambda p: cs), (CT.time, "time", lambda: now + 89400)):
        CT._POSHIST.clear()
        vy = CT.position_value("KXBTC15M-26NOV272130-30", "yes", 19, now - 600, 40.0, 32.0,
                               {"close_value_c": 709.0})
        vn = CT.position_value("KXBTC15M-26NOV272145-45", "no", 5, now - 600, 60.0, 8.0, None)
    p0 = vy["points"][0]
    fee = CT.order_fee_c(30.0, 19)
    ck("CXU-POS the paper position's graph: each minute's cash-out is the bid x quantity less Kalshi's per-order fee "
       "(NO's bid = 100 - YES's ask), the live point the current executable value, the cost the spend with its fee "
       "(the break-even line), upside = the payout - now, downside = now; at most 240 points, one cache entry per "
       "open ticker",
       abs(p0[1] - round(30.0 * 19 - fee, 1)) < 1e-9 and vy["cost_c"] == 40.0 * 19 + 32.0 and vy["payout_c"] == 1900.0
       and vy["now_c"] == 709.0 and vy["upside_c"] == 1191.0 and vy["downside_c"] == 709.0 and len(vy["points"]) <= 240
       and abs(vn["points"][0][1] - round((100 - 33.0) * 5 - CT.order_fee_c(67.0, 5), 1)) < 1e-9
       and vn["now_c"] is None and len(CT._POSHIST) == 2, f"{p0} {fee} {vn['points'][:1]}")
    psrc = inspect.getsource(CT.paper_positions)
    ck("CXU-POS-BOUND the value cache drops every ticker that is no longer an open position (MAX_OPEN bounds the rest)",
       "_POSHIST.pop(k, None)" in psrc and "if k not in live" in psrc)
    CT._POSHIST.clear()

    # "meaningful": a meaningful new low is a SUBSET of any new low, so the walk's chance of it can never exceed the
    # walk's chance of any new low; the contract value of the move is the model's own fair-price change
    rule = {"strike": 61000.0, "op": ">=", "kind": "above"}
    x, lo, hi, sig, tau, dl = 61010.0, 60950.0, 61080.0, 9e-5, 400.0, 2 * 9e-5 * math.sqrt(60)
    M = CT.meaningful(rule, x, lo, hi, dl, sig, tau)
    sd = sig * math.sqrt(tau)
    any_low = 1 - max(0.0, 2 * CT._phi(math.log(x / lo) / sd) - 1)
    any_high = 1 - max(0.0, 2 * CT._phi(math.log(hi / x) / sd) - 1)
    pu, p0_, pd = (CT.p_yes(rule, v, sig, tau) for v in (x * math.exp(dl), x, x * math.exp(-dl)))
    ck("CXU-MEAN a meaningful move is 2 one-minute SDs in dollars; the walk's chance of a MEANINGFUL new low (high) is "
       "below its chance of any new low (high), and its value on the contract is the model's fair-YES change, in cents",
       abs(M["move_usd"] - x * (math.exp(dl) - 1)) < 1e-6 and 0 < M["new_low_walk"] < any_low
       and 0 < M["new_high_walk"] < any_high and abs(M["yes_c_per_contract"] - round(50 * (abs(pu - p0_) + abs(p0_ - pd)), 2)) < 1e-9,
       f"{M} {any_low:.4f} {any_high:.4f}")

    # the outlook: 30 s untested; a lean only past the measured noise band; the walk's scenarios analytic
    saved = dict(CT._RESEARCH)
    try:
        CT.research()
        CT._RESEARCH["scenarios"] = {"uncertain": {"1": {"threshold": 0.02}, "3": {"threshold": None},
                                                   "5": {"threshold": 0.01, "heldout": {"at_or_above": {"hit": 0.53}}}},
                                     "scenarios": {}}
        dirn = {"horizons": {"1": {"passed": True, "p_up": 0.51}, "3": {"passed": True, "p_up": 0.6},
                             "5": {"passed": True, "p_up": 0.485}}, "feat": None}
        O = CT.outlook(dirn, {"tau_s": 250.0})
        O2 = CT.outlook({"horizons": {"1": {"passed": False}}}, {"tau_s": 30.0})
    finally:
        CT._RESEARCH.clear()
        CT._RESEARCH.update(saved)
    st = {r["h_s"]: r for r in O["direction"]}
    sc = {r["key"]: r for r in O["scenarios"]}
    walk3 = 1 - CT._phi(2.0 / (CT.VOL_MULT * math.sqrt(3)))
    ck("CXU-OUT the direction bars: 30 s is UNTESTED (no sub-minute history), a validated model's lean inside its "
       "measured noise band (or with no band at all) is UNCERTAIN, past it VALIDATED with its held-out hit rate, a "
       "horizon that failed is the random walk; the scenarios are the walk's analytic odds unless a model was "
       "validated, they sum to 1, and the 15-minute close is a scenario only with 1 to 14 minutes left",
       st[30]["status"] == "untested" and st[60]["status"] == "uncertain" and st[180]["status"] == "uncertain"
       and st[300]["status"] == "validated" and st[300]["heldout"]["at_or_above"]["hit"] == 0.53
       and {r["status"] for r in O2["direction"][1:]} == {"null"}
       and abs(sc["3"]["up"] - round(walk3, 4)) < 1e-9 and sc["3"]["source"] == "null" and sc["close"]["h_s"] == 240
       and all(abs(r["up"] + r["sideways"] + r["down"] - 1) < 1e-3 for r in O["scenarios"])
       and "close" not in {r["key"] for r in O2["scenarios"]}, f"{st} {sc}")
    sj = os.path.join(_ROOT, "research", "data", "crypto", "scenarios.json")
    if os.path.exists(sj):
        import importlib.util
        spec = importlib.util.spec_from_file_location("crypto_scenarios", os.path.join(_ROOT, "research", "crypto_scenarios.py"))
        RS = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.join(_ROOT, "research"))
        spec.loader.exec_module(RS)
        S = json.load(open(sj))
        ok = all(abs(S["null_by_h"][h][0] - (1 - CT._phi(2.0 / (CT.VOL_MULT * math.sqrt(int(h)))))) < 1e-4 for h in S["null_by_h"])
        ck("CXS the scenario research's walk is production's (VOL_MULT, the 2-SD band) and its committed verdicts "
           "are read by the page exactly (a scenario shows validated figures only where the rule passed)",
           RS.VOL_MULT == CT.VOL_MULT and ok and set(S["scenarios"]) >= {"3", "5", "close", "mu", "sd"}
           and all(isinstance(S["scenarios"][k]["passed"], bool) for k in ("3", "5", "close")),
           str({k: S["scenarios"][k].get("passed") for k in ("3", "5", "close")}))
        sc_, dec_ = S["scenarios"], S.get("decomposition") or {}
        osrc = inspect.getsource(CT.outlook)
        ck("CXS-VERDICT by the rule written first (7ddb640): the scenario model beat the walk at 3 min, 5 min and the "
           "close (gain = the null's held-out log loss minus the model's, nats a row: 0.00565 / 0.00588 / 0.01357); "
           "the decomposition (descriptive, after the verdict) puts 3 and 5 minutes' gain in HOW FAR it moves, so the "
           "page splits up and down evenly there and shows a direction only to the close; no lean-size threshold "
           "exists, so every direction bar reads Direction uncertain",
           all(sc_[k]["passed"] for k in ("3", "5", "close")) and abs(sc_["3"]["gain_vs_null_nats"] - 0.005653) < 1e-9
           and abs(sc_["5"]["gain_vs_null_nats"] - 0.00588) < 1e-9 and abs(sc_["close"]["gain_vs_null_nats"] - 0.013568) < 1e-9
           and [dec_[k]["which_way_supported"] for k in ("3", "5", "close")] == [False, False, True]
           and all(v["threshold"] is None for v in S["uncertain"].values())
           and "p = [(p[0] + p[2]) / 2, p[1], (p[0] + p[2]) / 2]" in osrc and "if not way:" in osrc,
           str(dec_))
    else:
        ck("CXS the scenario research's figures are committed", False, "research/data/crypto/scenarios.json missing")

    # the tab: static contract
    js = _read("static", "crypto.js")
    html = _read("templates", "index.html")
    sec = html[html.index('<div id="tab-crypto"'):html.index("<!-- /tab-crypto -->")]
    ck("CXU-TAB the tab is market-first: tabs, the numbers above the chart, the outlook below it, the off-chart target "
       "badge; overlays (band, median, EMAs, VWAP, Bollinger, the previous target) are kept out of the autoscale; the "
       "band is OFF by default and 50/80/95% when on; a figure with no validated lean reads Direction uncertain; no "
       "scenario path is drawn; browser buffers bounded (the window's candles at MAXPTS, the trailing at MAXBARS)",
       all(f'id="{i}"' in sec for i in ("cxTabs", "cxStats", "cxCtl", "cxChartWrap", "cxOff", "cxNow", "cxKBox"))
       and "autoscaleInfoProvider: keep ? undefined : none" in js and "bandu: line(c, acc, 1, 2), bandl: line(c, acc, 1, 2)" in js
       and 'med: line(c, "rgba(91,140,255,0.8)", 1, 1)' in js and "band: 0," in js
       and "var BAND_Z = {50: 0.6745, 80: 1.2816, 95: 1.96};" in js and "Direction uncertain" in js
       and "no scenario path is drawn" in js and "st.hist = c[1].candles.slice(-MAXPTS)" in js
       and "st.trail = res[4].candles.slice(-MAXBARS)" in js and "BTC" not in html)
    node = shutil.which("node")
    if not node:
        ck("CXU-JS the chart helpers (node not installed here: static checks only)", "window.cxPure = {" in js)
        return
    prog = r"""
global.window = {}; global.document = {};
require(process.argv[1]);
const P = window.cxPure, out = {};
const m15 = {open_ts: 9000, close_ts: 9900, previous: {open_ts: 8100}};
const day = {open_ts: 0, close_ts: 90000};
out.p15 = P.plan(m15, false, "beginner", 60, "line");
out.p15c = P.plan(m15, true, "beginner", 60, "candles");
out.pday = P.plan(day, false, "beginner", 60, "line");
out.grid = P.gridTimes(out.pday).length;
const hist = [[9000, 1, 2, 0.5, 10, 1], [9060, 1, 2, 0.5, 11, 1], [9120, 1, 2, 0.5, 12, 1]];
const live = [[9075, 0, 0, 0, 20, 0], [9090, 0, 0, 0, 21, 0], [9120, 0, 0, 0, 22, 0]];
out.line = P.lineData(hist, live, 60, 15, 9000, 9900);
out.cand = P.candleData(hist, [[9120, 12, 30, 1, 25, 2]], 60, 60, 9000, 9900);
out.near = P.scaleRange(100, 110, 112, 0);
out.far = P.scaleRange(100, 110, 200, 0);
out.quiet = P.scaleRange(100, 100.5, null, 20);
const pts = []; for (let t = 0; t <= 600; t += 15) pts.push({time: t, value: 100 * Math.exp(0.0004 * t / 15)});
out.obs = P.observed(pts, 0.0001, 615);
const flat = pts.map((x) => ({time: x.time, value: 100}));
out.flat = P.observed(flat, 0.0001, 615);
out.svg = P.svgValue({points: [[0, 500], [60, 700]], cost_c: 600, entry_ts: 0}, 600, 90);
console.log(JSON.stringify(out));
"""
    try:
        r = subprocess.run([node, "-e", prog, os.path.join(_ROOT, "static", "crypto.js")], capture_output=True, text=True,
                           timeout=60)
        o = json.loads(r.stdout)
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        ck("CXU-JS the chart helpers run under node", False, repr(e))
        return
    ln = o["line"]
    ck("CXU-JS the chart's grid is the market's window (15 s over 15 minutes, one REST call of 1-minute candles; 5 "
       "minutes over a day, <= 300 bars), the previous window widens it to its own open; the line takes the live "
       "feed's bars where it has them and each REST minute's close at the slot where that close happened, never both "
       "for one minute; the axis includes a near target, leaves a far one to the badge, never zooms a quiet window "
       "below its floor; observed direction reads rising / sideways against the walk's own 3-minute move; the value "
       "graph draws the break-even line",
       o["p15"]["step"] == 15 and o["p15"]["rest"] == 60 and o["p15c"]["start"] == 8100 and o["p15c"]["step"] == 60
       and o["pday"]["rest"] == 300 and o["pday"]["step"] == 300 and o["grid"] <= 600 + 2 * o["pday"]["pad"]
       and [x["time"] for x in ln] == [9045, 9075, 9090, 9120] and [x["value"] for x in ln] == [10, 20, 21, 22]
       and o["cand"][-1][2] == 30 and o["cand"][-1][4] == 25 and o["near"]["targetIn"] and not o["far"]["targetIn"]
       and o["far"]["hi"] < 120 and o["quiet"]["hi"] - o["quiet"]["lo"] >= 20 and o["obs"]["dir"] == "rising"
       and o["flat"]["dir"] == "sideways" and "stroke-dasharray" in o["svg"] and "<polyline" in o["svg"], r.stdout[:600])


def datetime_ts(s):
    from datetime import datetime as _dt
    return int(_dt.fromisoformat(s.replace("Z", "+00:00")).timestamp())


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
