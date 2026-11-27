"""The crypto terminal (2026-11-27): Kalshi's fifteen-minute and hourly crypto
contracts priced off the rule that settles them, the exchange data behind
them, a chart, and a PAPER record kept before any outcome is known.

PAPER ONLY. This module reads public market data with GET requests and
writes nothing anywhere but its own SQLite record: no order, no account, no
credential, no Kalshi endpoint that needs a key (guards CX-PAPER).

Why it is shaped the way it is: every crypto feature was removed on
2026-10-29 after the old 90-second quote recorder's backtest read every quote
saved since August into one gunicorn worker as dicts (~1.39 KB a row) and
drove it to 2,047/2,048 MB. So here:

- the record keeps a handful of rows per market at FIXED looks before the
  close (four per fifteen-minute market, three per hourly strike, five
  strikes an hour), in its own file (`crypto_record.db`, beside predlog.db),
  under table names the one-way drop (`store._CRYPTO_TABLES`) never names;
- every read of it is an SQL aggregate (`record`): no fetchall, no row ever
  becomes a Python object beyond a bounded GROUP BY;
- storage is bounded by retention and downsampling (`prune`, `KEEP_*`);
- nothing CPU-bound runs anywhere: the model is a closed form (a normal CDF
  and a 60-step bisection a row), the work is a few HTTP GETs, cached per
  process on a fixed key set (coin x granularity x series).

What settles the contracts (Kalshi's own rules text, `rules_primary` /
`rules_secondary` on every market, read 2026-11-27; parsed per market by
`parse_rule`, and a market whose rule does not parse is shown unpriced):

- KX<COIN>15M "up in the next 15 minutes": YES if the simple average of the
  sixty seconds of CF Benchmarks' real-time index (BRTI for bitcoin) before
  the close is AT LEAST the same sixty-second average before the open. That
  opening average is the market's floor_strike (it equals the previous
  window's expiration_value), so it is known for the whole window.
- KX<COIN>D hourly above/below: YES if the sixty-second average before the
  hour is ABOVE the strike (many strikes an hour; DOGE's are "custom" strikes
  whose exact figure is in `custom_strike`, and its 15-minute floor_strike is
  rounded to 6 places while the precise one, 7 places, is in custom_strike).
- A settled market carries `expiration_value`, the official average. That is
  the only public copy of the index: CF Benchmarks' real-time feed is
  licensed. The live level here is therefore a PROXY -- the median of four of
  the index's constituent exchanges' mids -- and it says so wherever it is
  shown, with each source's price, age and distance from the median.

The model is deliberately the benchmark: a driftless random walk on log price
at an EWMA of realised one-minute volatility, with the settlement AVERAGE
handled exactly (`settle_sd`) and the proxy's measured distance from the
index added in quadrature. Its validation on held-out history is
`research/crypto_validation.py` (report `research/reports/crypto_validation.md`);
until the live record decides otherwise every edge it prints is "unproven".
"""
import contextlib
import json
import math
import os
import re
import sqlite3
import statistics
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import errlog

# The kill switch. VIGIL_CRYPTO=off stops the recorder and the board answers
# "switched off" -- the owner ordered every crypto feature removed once
# (2026-10-29); turning this one off again must not need a deploy of code.
ENABLED = os.environ.get("VIGIL_CRYPTO", "on").strip().lower() not in ("off", "0", "false", "no")

_UA = "vigil-terminal/1.0"

# Coin -> its exchange symbols, its Kalshi series and the CF Benchmarks index
# Kalshi names in the rules. Every series was read live on 2026-11-27.
COINS = {
    "BTC": {"coinbase": "BTC-USD", "kraken": "XBTUSD", "bitstamp": "btcusd", "gemini": "btcusd",
            "m15": "KXBTC15M", "hourly": "KXBTCD", "index": "BRTI"},
    "ETH": {"coinbase": "ETH-USD", "kraken": "ETHUSD", "bitstamp": "ethusd", "gemini": "ethusd",
            "m15": "KXETH15M", "hourly": "KXETHD", "index": "ETHUSD_RTI",
            # KXETHD's rules call it "Ethereum Real-Time Index (ERTI)"; the series' own
            # settlement source is cfbenchmarks.com/data/indices/ETHUSD_RTI (read 2026-11-27)
            "aliases": ("ERTI",)},
    "SOL": {"coinbase": "SOL-USD", "kraken": "SOLUSD", "bitstamp": "solusd", "gemini": "solusd",
            "m15": "KXSOL15M", "hourly": "KXSOLD", "index": "SOLUSD_RTI"},
    "XRP": {"coinbase": "XRP-USD", "kraken": "XRPUSD", "bitstamp": "xrpusd", "gemini": "xrpusd",
            "m15": "KXXRP15M", "hourly": "KXXRPD", "index": "XRPUSD_RTI"},
    "DOGE": {"coinbase": "DOGE-USD", "kraken": "XDGUSD", "bitstamp": "dogeusd", "gemini": "dogeusd",
             "m15": "KXDOGE15M", "hourly": "KXDOGED", "index": "DOGEUSD_RTI"},
}
# Kraken answers with its own legacy names for some pairs.
_KRAKEN_KEYS = {"XBTUSD": ("XXBTZUSD", "XBTUSD"), "ETHUSD": ("XETHZUSD", "ETHUSD"),
                "XRPUSD": ("XXRPZUSD", "XRPUSD"), "XDGUSD": ("XDGUSD", "XXDGZUSD"), "SOLUSD": ("SOLUSD",)}

WINDOW_S = 60            # the settlement average's window (Kalshi's rules: "sixty seconds")
# Chosen on the validation's DEVELOPMENT week (closes 2026-11-13..19, 3,257 markets at the
# 300 s look, research/crypto_validation.py) by pooled log loss over a 5 x 5 grid, then
# frozen before the held-out week was scored. 60 min / 0.9 read 0.3830 nats, the runner-up
# (120 min / 0.9) 0.3834: the surface is flat, so neither figure is a discovery. The 0.9:
# one-minute closes carry trade-to-trade noise that a sixty-second average does not.
VOL_HALFLIFE_MIN = 60    # EWMA half-life of one-minute log returns
VOL_MULT = 0.9           # multiplier on the EWMA volatility
# The proxy's distance from the index, in log terms: RMS of log(official sixty-second
# average / Coinbase's (O+H+L+C)/4 of the same minute) on the development week, the median
# over coins (BTC 1.85, ETH 2.48, SOL 2.90, XRP 2.76, DOGE 3.65 bp). Measured on Coinbase
# alone; the board's four-exchange median is probably tighter, and the live record's
# proxy_rms_bps measures that path.
PROXY_SD = 0.000276
STALE_S = 30             # a source quote older than this is flagged and left out of the median

_cache = {}
_cache_lock = threading.Lock()


def _get(url, timeout=6):
    """GET only: no request body is ever built here (CX-PAPER)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _kalshi(path):
    """Kalshi's public trade API through the app's throttled getter (429 and
    5xx backoff, Retry-After honoured): read-only, no key."""
    import kalshi
    return kalshi._get_json(kalshi.BASE + path, timeout=10)


def _cached(key, ttl, fn, fresh=False):
    """A small TTL cache. Keys are bounded (coin x granularity x series), so it
    cannot grow on values that never repeat -- the pattern that killed two
    Render instances (CLAUDE.md)."""
    now = time.time()
    if not fresh:
        with _cache_lock:
            hit = _cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (time.time(), val)
    return val


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp() if s else None
    except ValueError:
        return None


# ---- exchange data ----------------------------------------------------------
def _src_coinbase(sym):
    d = _get(f"https://api.exchange.coinbase.com/products/{sym}/ticker")
    t = d.get("time")
    ts = _ts(t[:26] + "Z") if t else None
    return {"bid": _f(d.get("bid")), "ask": _f(d.get("ask")), "last": _f(d.get("price")), "ts": ts}


def _src_kraken(sym):
    d = (_get(f"https://api.kraken.com/0/public/Ticker?pair={sym}") or {}).get("result") or {}
    row = next((d[k] for k in _KRAKEN_KEYS.get(sym, (sym,)) if k in d), None) or next(iter(d.values()), None)
    if not row:
        raise ValueError("kraken: no row")
    # Kraken's ticker carries no timestamp: the fetch moment stands in.
    return {"bid": _f(row["b"][0]), "ask": _f(row["a"][0]), "last": _f(row["c"][0]), "ts": None}


def _src_bitstamp(sym):
    d = _get(f"https://www.bitstamp.net/api/v2/ticker/{sym}/")
    return {"bid": _f(d.get("bid")), "ask": _f(d.get("ask")), "last": _f(d.get("last")), "ts": _f(d.get("timestamp"))}


def _src_gemini(sym):
    d = _get(f"https://api.gemini.com/v1/pubticker/{sym}")
    ts = _f((d.get("volume") or {}).get("timestamp"))
    return {"bid": _f(d.get("bid")), "ask": _f(d.get("ask")), "last": _f(d.get("last")),
            "ts": ts / 1000.0 if ts else None}


_SOURCES = (("coinbase", _src_coinbase), ("kraken", _src_kraken), ("bitstamp", _src_bitstamp),
            ("gemini", _src_gemini))


def _proxy_now(coin):
    c = COINS[coin]
    now = time.time()
    rows = []
    for name, fn in _SOURCES:
        sym = c.get(name)
        try:
            q = fn(sym)
        except Exception as e:
            errlog.note("CRYPTO-src", e, path=f"{name}:{sym}")
            rows.append({"source": name, "ok": False, "error": str(e)[:80]})
            continue
        bid, ask, last = q.get("bid"), q.get("ask"), q.get("last")
        mid = (bid + ask) / 2.0 if bid and ask and ask >= bid else last
        age = (now - q["ts"]) if q.get("ts") else None
        rows.append({"source": name, "ok": mid is not None, "mid": mid,
                     "age_s": round(age, 1) if age is not None else None,
                     "stale": bool(age is not None and age > STALE_S)})
    live = [r["mid"] for r in rows if r.get("ok") and not r.get("stale")]
    price = statistics.median(live) if live else None
    for r in rows:
        if price and r.get("mid"):
            r["dev_bps"] = round(1e4 * (r["mid"] / price - 1.0), 1)
    return {"coin": coin, "price": price, "n_sources": len(live), "sources": rows, "ts": now,
            "basis": f"median of {len(live)} exchange mids -- a PROXY for CF Benchmarks' {c['index']}, "
                     "which is licensed and not public; Kalshi settles on the index itself"}


def proxy(coin, fresh=False):
    """The live level: the median of the constituent exchanges' mids."""
    return _cached(("proxy", coin), 4, lambda: _proxy_now(coin), fresh=fresh)


_GRANS = (60, 300, 900, 3600)


def candles(coin, gran=60):
    """[[t, open, high, low, close, volume], ...] oldest first: Coinbase's public
    candles, its last 300 (one call). Coinbase rows are [time, low, high,
    open, close, volume], newest first."""
    gran = int(gran)
    if gran not in _GRANS:
        raise ValueError(f"granularity {gran} not offered")
    sym = COINS[coin]["coinbase"]

    def fetch():
        raw = _get(f"https://api.exchange.coinbase.com/products/{sym}/candles?granularity={gran}")
        return [[int(r[0]), float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5])] for r in reversed(raw)]
    return _cached(("candles", coin, gran), 15 if gran <= 300 else 60, fetch)


def ewma_sigma(closes, halflife_min=None, mult=None):
    """Per-SECOND volatility of log price from one-minute closes, oldest first:
    an EWMA of squared one-minute log returns (the newest weighs most,
    half-life `halflife_min`), divided by sqrt(60), times `mult`. Zero-mean,
    as the random-walk benchmark assumes. None under 30 closes."""
    h = VOL_HALFLIFE_MIN if halflife_min is None else halflife_min
    k = VOL_MULT if mult is None else mult
    cl = [x for x in closes if x and x > 0]
    if len(cl) < 30:
        return None
    lam = 0.5 ** (1.0 / h)
    num = den = 0.0
    w = 1.0
    for i in range(len(cl) - 1, 0, -1):
        r = math.log(cl[i] / cl[i - 1])
        num += w * r * r
        den += w
        w *= lam
    return k * math.sqrt(num / den / 60.0) if num > 0 else None


def realised_vol(coin):
    return ewma_sigma([r[4] for r in candles(coin, 60)])


# ---- the settlement rule ----------------------------------------------------
_RX_WINDOW = re.compile(r"\b(?:sixty|60)[ -]seconds?\b", re.I)
_RX_INDEX = re.compile(r"CF Benchmarks'\s+(?P<name>[^()]*?)\s*(?:\((?P<code>[A-Z0-9_]+)\))?\s+before\b")
_RX_UP = re.compile(r"\bis at least the simple average of the (?:sixty|60) seconds of CF Benchmarks'", re.I)
# "at least <number>": some 15-minute markets state the opening average as a
# number (111 of 26,061 rows of the validation week, e.g. KXBTC15M-26NOV221730-30)
_RX_CMP = re.compile(r"\bis (?P<op>above|below|at least) \$?(?P<num>[0-9][0-9,]*(?:\.[0-9]+)?)", re.I)
_OPS = {"above": (">", "greater", "above"), "at least": (">=", "greater_or_equal", "at least"),
        "below": ("<", "less", "below")}
_RX_ROUND = re.compile(r"rounded to the nearest (\d+) decimal places?", re.I)


def _norm_index(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def index_names(coin):
    """Every name Kalshi's rules use for `coin`'s CF Benchmarks index, normalized."""
    c = COINS[coin]
    return {_norm_index(x) for x in (c["index"],) + tuple(c.get("aliases", ()))}


def parse_rule(m, coin=None):
    """What one Kalshi market settles on, read from its own rules text and
    strike fields: {"index", "window_s", "op" (">=", ">" or "<"), "strike",
    "reference" ("open_avg" -- the same average before the window opened -- or
    "fixed"), "close_ts", "round_digits", "no_data_no", "says"}; None when the
    rule is not one this model prices (a one-touch, a range, an unnamed index,
    a strike the text and the fields disagree on). Refusing is the point: a
    market priced on a reference it does not settle on is the error this
    terminal exists to avoid."""
    text = str(m.get("rules_primary") or "")
    sec = str(m.get("rules_secondary") or "")
    if "average" not in text.lower() or not _RX_WINDOW.search(text):
        return None
    mi = _RX_INDEX.search(text)
    if not mi:
        return None
    code = (mi.group("code") or mi.group("name") or "").strip()
    if not code or len(code) > 40:
        return None
    if coin and _norm_index(code) not in index_names(coin):
        return None
    cs = m.get("custom_strike") if isinstance(m.get("custom_strike"), dict) else {}
    # the precise strike: custom_strike carries it where floor_strike is rounded
    # (DOGE 15-minute: floor_strike 0.058187, custom_strike 0.0581877)
    precise = _f(cs.get("floor_strike")) if cs.get("floor_strike") not in (None, "") else None
    floor = precise if precise is not None else _f(m.get("floor_strike"))
    cap = _f(cs.get("cap_strike")) if cs.get("cap_strike") not in (None, "") else _f(m.get("cap_strike"))
    stype = m.get("strike_type")
    if stype == "custom":
        stype = cs.get("strike_type")
    rd = _f(cs.get("round_digits"))
    mr = _RX_ROUND.search(sec)
    if rd is None and mr:
        rd = float(mr.group(1))
    out = {"index": code, "window_s": WINDOW_S, "close_ts": _ts(m.get("close_time")),
           "round_digits": int(rd) if rd is not None else None,
           "no_data_no": "resolve to no" in sec.lower()}
    if _RX_UP.search(text):
        if stype != "greater_or_equal" or floor is None:
            return None
        out.update(op=">=", strike=floor, reference="open_avg")
        out["says"] = (f"YES if the simple average of the 60 seconds of CF Benchmarks' {code} before the close "
                       f"is at least the same average before the window opened ({_fmt(floor)})")
        return out
    mc = _RX_CMP.search(text)
    if not mc:
        return None
    num = _f(mc.group("num").replace(",", ""))
    op, want, word = _OPS[mc.group("op").lower()]
    field = (cap if cap is not None else floor) if op == "<" else floor
    if stype != want or field is None or num is None:
        return None
    # the text and the field must name the same strike (to the rounding the
    # market states); a disagreement is a rule this code does not understand
    tol = 0.5 * 10 ** -(int(rd) if rd is not None else 2) + 1e-12
    if abs(num - field) > max(tol, 1e-9 * abs(field)):
        return None
    out.update(op=op, strike=field, reference="fixed")
    out["says"] = (f"YES if the simple average of the 60 seconds of CF Benchmarks' {code} before the close "
                   f"is {word} {_fmt(field)}")
    return out


def _fmt(x):
    if x is None:
        return "?"
    if abs(x) >= 100:
        return f"{x:,.2f}"
    return f"{x:.7g}"


# ---- the settlement-average model -------------------------------------------
def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def settle_sd(sigma_s, tau_s, window_s=WINDOW_S, proxy_sd=None):
    """SD of log(settlement average) around log(current level), `tau_s`
    seconds before the close. The average of a Brownian path over its final
    `window_s` seconds has variance window/3 measured from the window's start,
    so with more than a window to go the variance time is tau - 2*window/3
    (the final tick's tau would overstate it by 40 seconds' worth). Inside the
    window only the part still to come is uncertain: (tau/window)^2 x tau/3 --
    the part already elapsed is taken AT THE CURRENT LEVEL (an approximation
    the board flags; the record never logs inside the window). The proxy's
    own distance from the index adds in quadrature."""
    ps = PROXY_SD if proxy_sd is None else proxy_sd
    tau = max(0.0, float(tau_s))
    if tau > window_s:
        t_eff = tau - 2.0 * window_s / 3.0
    else:
        t_eff = (tau / window_s) ** 2 * tau / 3.0
    return math.sqrt(max(0.0, sigma_s) ** 2 * t_eff + ps ** 2)


def p_yes(rule, level, sigma_s, tau_s, proxy_sd=None):
    """P(YES) under the driftless random walk for a parsed rule. For a
    continuous average "at least" and "above" are the same event (the rounding
    to the stated decimals moves it by under 1e-6 at these levels)."""
    if not rule or not level or not rule.get("strike") or sigma_s is None:
        return None
    sd = settle_sd(sigma_s, tau_s, proxy_sd=proxy_sd)
    z = math.log(level / rule["strike"]) / sd if sd > 0 else (math.inf if level >= rule["strike"] else -math.inf)
    p = _phi(z) if math.isfinite(z) else (1.0 if z > 0 else 0.0)
    return p if rule["op"] in (">=", ">") else 1.0 - p


def implied_vol(rule, level, tau_s, p_target):
    """The per-second volatility at which the model gives `p_target` (Kalshi's
    mid), by bisection; None where none does (the price on the wrong side of a
    half, or at an extreme). The honest way to read a gap between the model
    and Kalshi: not "the market is wrong by 13 cents" but "the market prices
    X% volatility where the last hour ran Y%"."""
    if not rule or not level or tau_s is None or tau_s <= 0 or p_target is None or not 0.02 < p_target < 0.98:
        return None
    lo, hi = 1e-8, 5e-3
    f = lambda sg: p_yes(rule, level, sg, tau_s) - p_target          # noqa: E731
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if fm * flo > 0:
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _ann(sigma_s):
    return round(100.0 * sigma_s * math.sqrt(365 * 86400), 1) if sigma_s else None


# ---- Kalshi -------------------------------------------------------------------
def _cents(v):
    x = _f(v)
    return round(x * 100.0, 2) if x is not None and 0 < x < 1 else None


def _fee(ticker, cents):
    try:
        import kalshi
        return kalshi.fee_for_market(ticker, cents)
    except Exception as e:
        errlog.note("CRYPTO-fee", e)
        return 7.0 * (cents / 100.0) * (1.0 - cents / 100.0) if cents else 0.0


def _quote(m, coin):
    """The compact row kept from one market (the raw hourly event answer is
    ~360 KB for bitcoin; only these fields survive the call)."""
    return {"ticker": m.get("ticker"), "rule": parse_rule(m, coin), "close_ts": _ts(m.get("close_time")),
            "yes_bid": _cents(m.get("yes_bid_dollars")), "yes_ask": _cents(m.get("yes_ask_dollars")),
            "no_bid": _cents(m.get("no_bid_dollars")), "no_ask": _cents(m.get("no_ask_dollars")),
            "volume": _f(m.get("volume_fp"))}


def m15_market(coin, fresh=False):
    """The open fifteen-minute market for `coin` (one at a time)."""
    series = COINS[coin]["m15"]

    def fetch():
        ms = (_kalshi(f"/markets?series_ticker={series}&status=open&limit=5") or {}).get("markets") or []
        ms = [m for m in ms if m.get("close_time")]
        return _quote(min(ms, key=lambda m: m["close_time"]), coin) if ms else None
    return _cached(("m15", coin), 5, fetch, fresh=fresh)


_ET = None


def _et():
    global _ET
    if _ET is None:
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")
    return _ET


def hourly_event_ticker(series, close_utc):
    """Kalshi names an hourly event by its close in Eastern time,
    <series>-YYMONDDHH (KXBTCD-26NOV2705 closes 5 AM ET = 10:00 UTC on
    2026-11-27, read live)."""
    et = close_utc.astimezone(_et())
    return f"{series}-{et:%y}{et.strftime('%b').upper()}{et:%d}{et:%H}"


def hourly_markets(coin, now=None, fresh=False):
    """The next hour's above/below ladder for `coin`, compact."""
    series = COINS[coin]["hourly"]
    now = now or time.time()
    close = datetime.fromtimestamp(now, timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    ev = hourly_event_ticker(series, close)

    def fetch():
        ms = (_kalshi(f"/markets?event_ticker={ev}&limit=400") or {}).get("markets") or []
        rows = [_quote(m, coin) for m in ms]
        rows = [r for r in rows if r["rule"] and r["rule"]["op"] == ">"]
        return {"event": ev, "close_ts": close.timestamp(), "unpriced": len(ms) - len(rows),
                "markets": sorted(rows, key=lambda r: r["rule"]["strike"])}
    # keyed on the coin alone (an event-keyed cache would gain a key an hour,
    # forever); a cached ladder for an hour already gone is fetched again
    val = _cached(("hourly", coin), 15, fetch, fresh=fresh)
    return val if val.get("event") == ev else _cached(("hourly", coin), 15, fetch, fresh=True)


def _edges(ticker, p, q):
    """Fee-in edge in cents per contract, buying at the ASK on each side."""
    out = {}
    for side, ask, prob in (("yes", q.get("yes_ask"), p), ("no", q.get("no_ask"), 1.0 - p)):
        if ask is None or prob is None:
            out[side] = None
            continue
        fee = _fee(ticker, ask)
        out[side] = round(100.0 * prob - ask - fee, 1)
        out[side + "_fee"] = round(fee, 2)
    return out


def price_row(q, level, sigma_s, now):
    """One market priced: the model, its volatility band, Kalshi's book, the
    implied volatility and the fee-in edges."""
    rule = q.get("rule")
    row = dict(q)
    tau = (q.get("close_ts") or now) - now
    row["tau_s"] = round(tau, 1)
    row["in_window"] = bool(tau < WINDOW_S)
    bid, ask = q.get("yes_bid"), q.get("yes_ask")
    mid = (bid + ask) / 2.0 if bid is not None and ask is not None and ask > bid else None
    row["kalshi_mid"] = round(mid, 2) if mid is not None else None
    row["spread_c"] = round(ask - bid, 2) if mid is not None else None
    if not rule or tau <= 0:
        row["p_model"] = None
        return row
    p = p_yes(rule, level, sigma_s, tau)
    row["p_model"] = round(p, 4) if p is not None else None
    if p is None:
        return row
    # the band: the same model at 0.8x and 1.25x the volatility -- how much the
    # figure leans on a volatility estimate that is itself noisy
    a, b = p_yes(rule, level, sigma_s * 0.8, tau), p_yes(rule, level, sigma_s * 1.25, tau)
    row["p_band"] = [round(min(a, b), 4), round(max(a, b), 4)]
    sd = settle_sd(sigma_s, tau)
    row["z"] = round(math.log(level / rule["strike"]) / sd, 2) if sd > 0 else None
    iv = implied_vol(rule, level, tau, mid / 100.0) if mid is not None else None
    row["implied_vol_ann_pct"] = _ann(iv)
    if mid is not None and iv is None and 2.0 < mid < 98.0:
        # seen live 2026-11-27: two minutes out, proxy 1.2 bp over the strike,
        # model 57%, Kalshi 81.5c -- no volatility reaches 81.5% from that level
        row["iv_note"] = ("no volatility reconciles the model with Kalshi's price: the market is reading the index "
                          "at a different level than the proxy (or knows the seconds already averaged)")
    row["edge"] = _edges(q["ticker"], p, q)
    return row


def board(coin):
    """Everything the tab shows for one coin, one call. Light: a few cached
    HTTP GETs and closed-form arithmetic (no simulation)."""
    coin = coin.upper()
    if coin not in COINS:
        raise ValueError(f"unknown coin {coin}")
    if not ENABLED:
        return {"coin": coin, "coins": list(COINS), "off": True,
                "note": "the crypto terminal is switched off (VIGIL_CRYPTO=off)"}
    now = time.time()
    px = proxy(coin)
    level = px.get("price")
    try:
        sigma = realised_vol(coin)
    except Exception as e:
        errlog.note("CRYPTO-vol", e, path=coin)
        sigma = None
    rec = record_summary()
    out = {"coin": coin, "coins": list(COINS), "ts": now, "proxy": px, "sigma_s": sigma,
           "sigma_ann_pct": _ann(sigma), "paper_only": True,
           "model": {"name": "driftless random walk at realised volatility, settlement-average aware",
                     "vol_halflife_min": VOL_HALFLIFE_MIN, "vol_mult": VOL_MULT, "proxy_sd": PROXY_SD,
                     "window_s": WINDOW_S, "validation": "research/reports/crypto_validation.md"},
           "edges_proven": bool(rec and rec.get("decided") == "model"),
           "record": rec}
    try:
        q = m15_market(coin)
        out["m15"] = price_row(q, level, sigma, now) if q else None
    except Exception as e:
        errlog.note("CRYPTO-m15", e, path=coin)
        out["m15"] = None
    try:
        h = hourly_markets(coin, now)
        if h and level:
            near = sorted(h["markets"], key=lambda r: abs(r["rule"]["strike"] - level))[:9]
            out["hourly"] = {"event": h["event"], "close_ts": h["close_ts"], "unpriced": h["unpriced"],
                             "markets": [price_row(r, level, sigma, now)
                                         for r in sorted(near, key=lambda r: r["rule"]["strike"])]}
        else:
            out["hourly"] = None
    except Exception as e:
        errlog.note("CRYPTO-hourly", e, path=coin)
        out["hourly"] = None
    return out


# ---- the paper record: logged before the close, graded off Kalshi's settlement ----
def _db_path():
    explicit = os.environ.get("CRYPTO_DB")
    if explicit:
        return explicit
    beside = os.environ.get("PREDLOG_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "predlog.db")
    return os.path.join(os.path.dirname(os.path.abspath(beside)), "crypto_record.db")


_db_lock = threading.Lock()
TABLE = "cx_forecasts"          # never one of store._CRYPTO_TABLES (that drop is one-way; guard CX-DROP)
# The looks, seconds before the close. One row per market per look, written
# once (the primary key). Every look is at least a full settlement window
# before the close, so nothing logged has seen a second of the average.
CHECKPOINTS = {"m15": (600, 300, 120, 60), "hourly": (1800, 900, 300)}
DECISION_LOOK = {"m15": 300, "hourly": 900}
HOURLY_STRIKES = 5               # the strikes nearest the level, per hourly look
PAPER_EDGE_C = 3.0               # the paper rule buys a side whose fee-in edge clears this, at the decision look
# Retention (measured 2026-11-27, tests/guards_auto_crypto.py CX-STORE): the
# schedule logs 3,720 rows a day (5 coins x 96 fifteen-minute markets x 4 looks
# + 5 coins x 24 hours x 3 looks x 5 strikes) at 229 bytes a row in the file,
# 0.85 MB a day. Every look is kept DETAIL_DAYS, then only the decision look
# (1,080 rows a day) until KEEP_DAYS: 231,360 rows, ~53 MB at steady state. The
# disk is 1 GB and shared with every other store (render.yaml), and a year of
# detail was 109 MB, so six months. The row cap sits above the schedule in
# case it ever logs more than it should.
DETAIL_DAYS = 14
KEEP_DAYS = 180
MAX_ROWS = 300_000
DEAD_S = 7 * 86400               # a market unsettled a week after its close is void, never scored


def _conn(new=False):
    c = sqlite3.connect(_db_path(), timeout=30)
    if new:
        # incremental auto-vacuum must be set on a new file BEFORE WAL mode and
        # the first table (measured: set after WAL it reads back 0 and a prune
        # never shrinks the file); on a file that already has tables it is a no-op
        c.execute("PRAGMA auto_vacuum=INCREMENTAL")
    c.execute("PRAGMA journal_mode=WAL")
    # SQLite's own LN exists only when it was built with its math functions;
    # the record's log loss must not depend on how Debian built it.
    c.create_function("LN", 1, math.log, deterministic=True)
    return c


def init_db():
    with _db_lock, contextlib.closing(_conn(new=True)) as c:
        # pruned pages are handed back to the disk (prune): the file stays the
        # size of what is kept
        with c:
            c.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                ticker TEXT NOT NULL, look_s INTEGER NOT NULL, coin TEXT NOT NULL, kind TEXT NOT NULL,
                logged_ts REAL NOT NULL, close_ts REAL NOT NULL, tau_s REAL NOT NULL, level REAL, strike REAL,
                op TEXT, sigma_s REAL, p_model REAL, yes_bid REAL, yes_ask REAL, no_ask REAL,
                fee_yes REAL, fee_no REAL, n_sources INTEGER,
                result INTEGER, settle_value REAL, graded_ts REAL,
                PRIMARY KEY (ticker, look_s)) WITHOUT ROWID""")
            # partial: only the ungraded rows (a few hundred at any time) are
            # indexed; a full (result, close_ts) index cost ~60 bytes on every row
            c.execute(f"CREATE INDEX IF NOT EXISTS cx_open ON {TABLE} (close_ts) WHERE result IS NULL")


def _log_row(c, coin, kind, look, row, level, sigma, n_sources, now):
    """One pre-close row. Refused unless it is at least a settlement window
    before its close and its book and level were read at this look."""
    rule = row.get("rule")
    tau = row["close_ts"] - now
    if not rule or row.get("p_model") is None or tau < WINDOW_S:
        return False
    e = row.get("edge") or {}
    c.execute(f"""INSERT OR IGNORE INTO {TABLE} (ticker, look_s, coin, kind, logged_ts, close_ts, tau_s, level,
                 strike, op, sigma_s, p_model, yes_bid, yes_ask, no_ask, fee_yes, fee_no, n_sources)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (row["ticker"], look, coin, kind, now, row["close_ts"], round(tau, 1), level, rule["strike"],
               rule["op"], sigma, row["p_model"], row.get("yes_bid"), row.get("yes_ask"), row.get("no_ask"),
               e.get("yes_fee"), e.get("no_fee"), n_sources))
    return True


def due_look(tau, looks, slack=45):
    """The look this moment falls on: within `slack` seconds AFTER it (the
    loop wakes on each look's second, so each is caught once; a late wake past
    the slack logs nothing rather than a row with less time left than its
    look says)."""
    for cp in looks:
        if cp - slack < tau <= cp:
            return cp
    return None


def log_tick(now=None):
    """One look: every coin's open fifteen-minute market and its hourly strikes
    nearest the level, logged at their looks. Everything is fetched fresh at
    this moment (never a cached quote from before it)."""
    now = now or time.time()
    n = 0
    for coin in COINS:
        try:
            rows = []
            q = m15_market(coin, fresh=True)
            cp15 = due_look(q["close_ts"] - now, CHECKPOINTS["m15"]) if q and q.get("close_ts") else None
            close_h = (math.floor(now / 3600.0) + 1) * 3600.0
            cph = due_look(close_h - now, CHECKPOINTS["hourly"])
            if not cp15 and not cph:
                continue
            px = proxy(coin, fresh=True)
            level = px.get("price")
            sigma = realised_vol(coin)
            if not level or not sigma:
                continue
            if cp15:
                rows.append(("m15", cp15, price_row(q, level, sigma, now)))
            if cph:
                h = hourly_markets(coin, now, fresh=True)
                for r in sorted(h["markets"], key=lambda r: abs(r["rule"]["strike"] - level))[:HOURLY_STRIKES]:
                    rows.append(("hourly", cph, price_row(r, level, sigma, now)))
            if rows:
                with _db_lock, contextlib.closing(_conn()) as c, c:
                    for kind, cp, row in rows:
                        n += bool(_log_row(c, coin, kind, cp, row, level, sigma, px.get("n_sources"), now))
        except Exception as e:
            errlog.note("CRYPTO-log", e, path=coin)
    return n


def grade_tick(now=None, max_markets=200, batch=20):
    """Grade rows whose market has closed, off Kalshi's own result and its
    official settlement value: one batched read (`tickers=`, twenty a call --
    a hundred answered 429 on 2026-11-27), oldest first; a refused fetch ends
    the pass; a market still unsettled a week after its close is void (-1)."""
    now = now or time.time()
    with _db_lock, contextlib.closing(_conn()) as c:
        tickers = [r[0] for r in c.execute(
            f"""SELECT ticker FROM {TABLE} WHERE result IS NULL AND close_ts < ?
                GROUP BY ticker ORDER BY MIN(close_ts) LIMIT ?""", (now - 90, max_markets))]
    graded = 0
    for i in range(0, len(tickers), batch):
        part = tickers[i:i + batch]
        try:
            ms = (_kalshi("/markets?tickers=" + ",".join(part)) or {}).get("markets") or []
        except Exception as e:
            errlog.note("CRYPTO-grade", e, path=part[0])
            break
        with _db_lock, contextlib.closing(_conn()) as c, c:
            for m in ms:
                res = str(m.get("result") or "").lower()
                if res in ("yes", "no"):
                    c.execute(f"UPDATE {TABLE} SET result=?, settle_value=?, graded_ts=? WHERE ticker=?",
                              (1 if res == "yes" else 0, _f(m.get("expiration_value")), now, m.get("ticker")))
                    graded += 1
            c.execute(f"UPDATE {TABLE} SET result=-1, graded_ts=? WHERE result IS NULL AND close_ts < ?",
                      (now, now - DEAD_S))
        time.sleep(0.3)
    return graded


def prune(now=None):
    """The storage bound: every look DETAIL_DAYS, the decision look to
    KEEP_DAYS, never more than MAX_ROWS (oldest first); freed pages go back
    to the disk."""
    now = now or time.time()
    with _db_lock, contextlib.closing(_conn()) as c:
        with c:
            c.execute(f"""DELETE FROM {TABLE} WHERE close_ts < ? AND look_s != (CASE kind WHEN 'm15' THEN
                          {DECISION_LOOK['m15']} ELSE {DECISION_LOOK['hourly']} END)""", (now - DETAIL_DAYS * 86400,))
            c.execute(f"DELETE FROM {TABLE} WHERE close_ts < ?", (now - KEEP_DAYS * 86400,))
            over = c.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0] - MAX_ROWS
            if over > 0:
                # exactly `over` rows, oldest closes first (a cut on close_ts alone
                # kept every row tied at the cut: 1,005 under a 1,000 cap)
                c.execute(f"""DELETE FROM {TABLE} WHERE (ticker, look_s) IN
                              (SELECT ticker, look_s FROM {TABLE} ORDER BY close_ts LIMIT ?)""", (over,))
        # executescript steps the pragma to completion; a plain execute steps it
        # once and frees ONE page (measured: 1,056 free pages, one returned)
        c.executescript("PRAGMA incremental_vacuum;")


def record(coin=None, kind=None, look=None):
    """The graded record as SQL sums -- never a fetchall over the table (the
    removed recorder's backtest read ~1M rows into one worker as dicts).

    Per row: the model's probability and Kalshi's mid at the same look, log
    loss and Brier on the rows priced both ways, the paired difference's SE
    (one row per market at a single look), ten reliability bins each, the
    proxy's distance from the official average at the last look, and the
    paper rule: one contract on the side whose fee-in edge at the ask clears
    PAPER_EDGE_C at the decision look, its running P/L and drawdown by an SQL
    window over the close times."""
    where, args = ["result IN (0, 1)", "p_model IS NOT NULL"], {"e": PAPER_EDGE_C}
    if coin:
        where.append("coin = :coin")
        args["coin"] = coin
    if kind:
        where.append("kind = :kind")
        args["kind"] = kind
    w_all = " AND ".join(where)
    look_w = "look_s = :look" if look else \
        f"look_s = (CASE kind WHEN 'm15' THEN {DECISION_LOOK['m15']} ELSE {DECISION_LOOK['hourly']} END)"
    if look:
        args["look"] = int(look)
    w = f"{w_all} AND {look_w}"
    pm = "MIN(MAX(p_model, 0.001), 0.999)"
    mm = "MIN(MAX((yes_bid + yes_ask) / 200.0, 0.001), 0.999)"
    two = "yes_bid IS NOT NULL AND yes_ask IS NOT NULL AND yes_ask > yes_bid"
    llm = f"-(result * LN({pm}) + (1 - result) * LN(1 - {pm}))"
    llk = f"-(result * LN({mm}) + (1 - result) * LN(1 - {mm}))"
    q = f"""SELECT COUNT(*), SUM({llm}), SUM((p_model - result) * (p_model - result)), SUM(result),
                   MIN(close_ts), MAX(close_ts), COUNT(DISTINCT CAST(close_ts / 86400 AS INTEGER)),
                   SUM(CASE WHEN {two} THEN 1 ELSE 0 END),
                   SUM(CASE WHEN {two} THEN {llm} END), SUM(CASE WHEN {two} THEN {llk} END),
                   SUM(CASE WHEN {two} THEN ({llm} - {llk}) * ({llm} - {llk}) END),
                   SUM(CASE WHEN {two} THEN ((yes_bid + yes_ask) / 200.0 - result)
                                            * ((yes_bid + yes_ask) / 200.0 - result) END),
                   SUM(CASE WHEN {two} THEN (p_model - result) * (p_model - result) END)
            FROM {TABLE} WHERE {w}"""
    bins_q = f"""SELECT MIN(CAST(p_model * 10 AS INTEGER), 9) AS b, COUNT(*), AVG(p_model), AVG(result)
                 FROM {TABLE} WHERE {w} GROUP BY b ORDER BY b LIMIT 10"""
    kbins_q = f"""SELECT MIN(CAST((yes_bid + yes_ask) / 20.0 AS INTEGER), 9) AS b, COUNT(*),
                         AVG((yes_bid + yes_ask) / 200.0), AVG(result)
                  FROM {TABLE} WHERE {w} AND {two} GROUP BY b ORDER BY b LIMIT 10"""
    # the proxy against the official average, at the last look of each market
    # (its level a minute or more before the close, so it carries a minute's
    # moves as well as the proxy error -- an upper bound on the proxy's own)
    prox_q = f"""SELECT COUNT(*), AVG((LN(level) - LN(settle_value)) * (LN(level) - LN(settle_value)))
                 FROM {TABLE} WHERE {w_all} AND settle_value > 0 AND level > 0
                   AND look_s = (CASE kind WHEN 'm15' THEN 60 ELSE 300 END)"""
    paper_q = f"""WITH d AS (
                    SELECT close_ts, 100.0 * p_model - yes_ask - COALESCE(fee_yes, 0) AS ey,
                           100.0 * (1 - p_model) - no_ask - COALESCE(fee_no, 0) AS en, yes_ask, no_ask, result,
                           fee_yes, fee_no
                    FROM {TABLE} WHERE {w}),
                  b AS (SELECT close_ts,
                      CASE WHEN ey IS NOT NULL AND ey >= :e AND (en IS NULL OR ey >= en)
                             THEN (CASE WHEN result = 1 THEN 100.0 ELSE 0 END) - yes_ask - COALESCE(fee_yes, 0)
                           WHEN en IS NOT NULL AND en >= :e
                             THEN (CASE WHEN result = 0 THEN 100.0 ELSE 0 END) - no_ask - COALESCE(fee_no, 0)
                      END AS pnl FROM d),
                  t AS (SELECT close_ts, pnl, SUM(pnl) OVER (ORDER BY close_ts ROWS UNBOUNDED PRECEDING) AS cum
                        FROM b WHERE pnl IS NOT NULL),
                  u AS (SELECT cum, MAX(cum) OVER (ORDER BY close_ts ROWS UNBOUNDED PRECEDING) AS peak FROM t)
                  SELECT (SELECT COUNT(*) FROM t), (SELECT SUM(pnl) FROM t), (SELECT SUM(pnl * pnl) FROM t),
                         (SELECT MAX(MAX(peak, 0) - cum) FROM u),
                         (SELECT SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) FROM t)"""
    try:
        with contextlib.closing(_conn()) as c:
            r = c.execute(q, args).fetchone()
            bins = [x for x in c.execute(bins_q, args)]
            kbins = [x for x in c.execute(kbins_q, args)]
            px = c.execute(prox_q, args).fetchone()
            pr = c.execute(paper_q, args).fetchone()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return {"n": 0, "decided": None}
        raise
    n = r[0] or 0
    out = {"n": n, "look": int(look) if look else "decision", "yes_rate": round(r[3] / n, 4) if n else None,
           "first_close_ts": r[4], "last_close_ts": r[5], "days": r[6] or 0,
           "model_logloss": round(r[1] / n, 4) if n else None, "model_brier": round(r[2] / n, 4) if n else None,
           "priced": r[7] or 0, "decided": None}
    k = r[7] or 0
    if k:
        dm = (r[8] - r[9]) / k
        var = max(0.0, r[10] / k - dm * dm)
        se = math.sqrt(var / k) if k > 1 else None
        out.update({"same_rows_model_logloss": round(r[8] / k, 4), "same_rows_kalshi_logloss": round(r[9] / k, 4),
                    "same_rows_model_brier": round(r[12] / k, 4), "same_rows_kalshi_brier": round(r[11] / k, 4),
                    "diff_logloss": round(dm, 4), "diff_se": round(se, 4) if se else None})
        # decided only on a real sample: DECIDE_MIN markets over DECIDE_DAYS
        # days, the paired difference past 2 SE either way
        if se and k >= DECIDE_MIN and (r[6] or 0) >= DECIDE_DAYS and abs(dm) > 2 * se:
            out["decided"] = "kalshi" if dm > 0 else "model"
    out["bins_model"] = [{"bin": b, "n": cnt, "said": round(p, 3), "hit": round(h, 3)} for b, cnt, p, h in bins]
    out["bins_kalshi"] = [{"bin": b, "n": cnt, "said": round(p, 3), "hit": round(h, 3)} for b, cnt, p, h in kbins]
    out["proxy_rms_bps"] = round(1e4 * math.sqrt(px[1]), 1) if px and px[0] and px[1] is not None else None
    out["proxy_n"] = px[0] if px else 0
    pn = pr[0] or 0
    out["paper"] = {"rule": f"one contract on the side whose fee-in edge at the ask is {PAPER_EDGE_C:g}c or more, "
                            f"at the {DECISION_LOOK['m15']} s look (hourly {DECISION_LOOK['hourly']} s)",
                    "bets": pn, "pnl_c": round(pr[1], 1) if pn else 0.0,
                    "per_bet_c": round(pr[1] / pn, 2) if pn else None,
                    "se_per_bet_c": round(math.sqrt(max(0.0, pr[2] / pn - (pr[1] / pn) ** 2) / pn), 2) if pn > 1 else None,
                    "max_drawdown_c": round(pr[3], 1) if pr[3] is not None else 0.0,
                    "win_rate": round(pr[4] / pn, 3) if pn else None}
    return out


DECIDE_MIN = 500
DECIDE_DAYS = 10


def record_by():
    """The record split by coin, kind and look: one GROUP BY, at most
    5 x 2 x 4 rows."""
    pm = "MIN(MAX(p_model, 0.001), 0.999)"
    mm = "MIN(MAX((yes_bid + yes_ask) / 200.0, 0.001), 0.999)"
    two = "yes_bid IS NOT NULL AND yes_ask IS NOT NULL AND yes_ask > yes_bid"
    q = f"""SELECT coin, kind, look_s, COUNT(*),
                   AVG(CASE WHEN {two} THEN -(result * LN({pm}) + (1 - result) * LN(1 - {pm})) END),
                   AVG(CASE WHEN {two} THEN -(result * LN({mm}) + (1 - result) * LN(1 - {mm})) END),
                   SUM(CASE WHEN {two} THEN 1 ELSE 0 END)
            FROM {TABLE} WHERE result IN (0, 1) AND p_model IS NOT NULL
            GROUP BY coin, kind, look_s ORDER BY coin, kind, look_s DESC LIMIT 64"""
    try:
        with contextlib.closing(_conn()) as c:
            return [{"coin": a, "kind": b, "look_s": cp, "n": n,
                     "model_logloss": round(x, 4) if x is not None else None,
                     "kalshi_logloss": round(y, 4) if y is not None else None, "priced": k}
                    for a, b, cp, n, x, y, k in c.execute(q)]
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return []
        raise


# The record a page reads is computed by the BACKGROUND OWNER after each
# grading pass and kept as a small JSON file beside the record; a web worker
# only reads that file. Measured: record() + record_by() over 200,000 rows take
# ~2 s of SQLite time, and the tab refreshes every 15 s -- on a one-core quota
# that scan belongs nowhere near a request.
SUMMARY_TTL = 240


def _summary_path():
    return _db_path() + ".summary.json"


def write_summary():
    data = {"ts": time.time(), "record": record(), "by": record_by()}
    path = _summary_path()
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    return data


def summary():
    """The latest record summary: the background owner's file when it is under
    two hours old, else one computation cached SUMMARY_TTL in this process
    (one key, so it cannot grow)."""
    try:
        path = _summary_path()
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7200:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError) as e:
        errlog.note("CRYPTO-summary-read", e)
    return _cached(("summary",), SUMMARY_TTL, lambda: {"ts": time.time(), "record": record(), "by": record_by()})


def record_cached(coin=None, kind=None, look=None):
    """A filtered record (the route's coin / kind / look), cached SUMMARY_TTL
    on its bounded key (6 coins-or-none x 3 kinds-or-none x 5 looks-or-none)."""
    return _cached(("record", coin, kind, look), SUMMARY_TTL, lambda: record(coin=coin, kind=kind, look=look))


def record_summary():
    try:
        return summary().get("record")
    except Exception as e:
        errlog.note("CRYPTO-record", e)
        return None


# ---- the background loop (the app's background owner only) -----------------
_started = False


def next_wake(now):
    """The next second any look falls on (all looks are whole seconds before a
    quarter-hour or an hour, so the loop sleeps between them instead of
    polling: eighteen wakes an hour, where the removed recorder polled every
    90 s)."""
    best = None
    for period, looks in ((900, CHECKPOINTS["m15"]), (3600, CHECKPOINTS["hourly"])):
        base = math.floor(now / period) * period
        for k in (1, 2):
            close = base + k * period
            for cp in looks:
                t = close - cp
                if t > now and (best is None or t < best):
                    best = t
    return best


def _loop():
    last_grade = last_prune = 0.0
    while True:
        try:
            now = time.time()
            wake = next_wake(now)
            time.sleep(max(0.5, min(300.0, wake - now + 1.0)))
            if not ENABLED:
                continue
            now = time.time()
            if due_look(next_close(now, 900) - now, CHECKPOINTS["m15"]) or \
                    due_look(next_close(now, 3600) - now, CHECKPOINTS["hourly"]):
                log_tick(now)
            if now - last_grade > 240:
                last_grade = now
                # the summary is ~2 s of SQLite at steady state (231k rows): only
                # when something was graded, or the file is an hour old
                sp = _summary_path()
                if grade_tick(now) or not os.path.exists(sp) or now - os.path.getmtime(sp) > 3600:
                    write_summary()
            if now - last_prune > 6 * 3600:
                last_prune = now
                prune(now)
        except Exception as e:
            errlog.note("CRYPTO-loop", e)
            time.sleep(30)


def next_close(now, period):
    return (math.floor(now / period) + 1) * period


def start_background():
    global _started
    if _started or not ENABLED:
        return
    _started = True
    init_db()
    threading.Thread(target=_loop, daemon=True, name="crypto-record").start()
