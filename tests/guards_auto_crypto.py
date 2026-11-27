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

    python3 tests/guards_auto_crypto.py      # exits 1 on any failed check
"""
import contextlib
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
        with _patched((CT, "_conn", lambda: _CountConn(real(), sink)),
                      (CT, "_kalshi", lambda p: {"markets": []}), (CT.time, "sleep", lambda s: None)):
            CT.record()
            CT.record(coin="BTC", kind="m15", look=300)
            CT.record_by()
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
    jsbad = [w for w in ("method:", "POST", "/portfolio", "/orders") if w in js]
    urls = re.findall(r'getJSON\("([^"?]+)', js)
    ck("CX-PAPER the tab only GETs /api/crypto/* (no method, no POST)",
       not jsbad and urls and all(u.startswith("/api/crypto/") for u in urls) and js.count("fetch(") == 1, f"{jsbad} {urls}")
    import app as _app
    rules = [r for r in _app.app.url_map.iter_rules() if r.rule.startswith("/api/crypto")]
    ck("CX-PAPER the crypto routes answer GET only",
       len(rules) == 3 and all((r.methods or set()) <= {"GET", "HEAD", "OPTIONS"} for r in rules),
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
    ck("CX-UI one refresh timer: cleared before it is armed, armed only while the tab is on screen and the page visible, "
       "a setTimeout chain (no setInterval)",
       "clearTimeout(st.timer)" in arm and "if (!onScreen()) return;" in arm and "setInterval" not in js
       and "document.hidden" in js and 'classList.contains("hidden")' in js)
    ck("CX-UI phone width: the ladder and the record tables scroll inside their own box; the chart is a viewBox SVG at 100% width",
       js.count('overflow-x:auto') >= 2 and 'width="100%"' in js and "viewBox" in js)

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


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
