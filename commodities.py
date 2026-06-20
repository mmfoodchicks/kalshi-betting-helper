"""Commodity price feed (Yahoo Finance) for Kalshi commodity price markets.

Models commodities the same way as crypto (GBM from recent price action), but on
a DAILY timescale: candles are daily closes and the horizon is measured in days,
so the volatility units stay consistent through the shared odds engine.

Basis caveat: our spot comes from the Yahoo front-month future, which may differ
from Kalshi's exact settlement reference. If our spot doesn't line up with where
the Kalshi ladder is centered, the "edges" aren't real — so the spot is shown
prominently for a sanity check.
"""

import json
import time
import urllib.parse
import urllib.request

COMMODITIES = {
    "gold":   {"label": "🥇 Gold", "yahoo": "GC=F", "series": ["KXGOLDW", "KXGOLDMON"]},
    "silver": {"label": "🥈 Silver", "yahoo": "SI=F", "series": ["KXSILVERMON", "KXSILVERW"]},
    "wti":    {"label": "🛢️ WTI Crude", "yahoo": "CL=F", "series": ["KXWTIMAX", "KXWTIMIN", "KXWTIH", "KXWTIEU"]},
    "brent":  {"label": "🛢️ Brent Crude", "yahoo": "BZ=F", "series": ["KXBRENTD"]},
    "natgas": {"label": "🔥 Natural Gas", "yahoo": "NG=F", "series": ["KXNGAS", "KXNATGASMON", "KXNGASMAX"]},
    "copper": {"label": "🟤 Copper", "yahoo": "HG=F", "series": ["KXCOPPERW", "KXCOPPERMON"]},
}

_cache = {}


def _yahoo(symbol, rng, interval, timeout=10):
    enc = urllib.parse.quote(symbol)
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}"
           f"?range={rng}&interval={interval}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_spot(key, max_age=30.0):
    cfg = COMMODITIES[key]
    ck = ("spot", key)
    now = time.time()
    hit = _cache.get(ck)
    if hit and now - hit[0] < max_age:
        return hit[1]
    d = _yahoo(cfg["yahoo"], "1d", "1m")
    price = d["chart"]["result"][0]["meta"].get("regularMarketPrice")
    _cache[ck] = (now, price)
    return price


def get_candles(key, max_age=600.0):
    """Daily closes -> candle dicts ({time, close}) for the odds engine."""
    cfg = COMMODITIES[key]
    ck = ("candles", key)
    now = time.time()
    hit = _cache.get(ck)
    if hit and now - hit[0] < max_age:
        return hit[1]
    d = _yahoo(cfg["yahoo"], "3mo", "1d")
    r = d["chart"]["result"][0]
    ts = r["timestamp"]
    closes = r["indicators"]["quote"][0]["close"]
    candles = [{"time": t, "close": c} for t, c in zip(ts, closes) if c is not None]
    _cache[ck] = (now, candles)
    return candles
