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


def implied_center(markets):
    """Infer the price Kalshi's ladder is centered on, to sanity-check our spot
    against (basis risk). On a threshold ladder ("price >= strike"), YES falls as
    the strike rises, so the strike where YES crosses 50c is the market's implied
    median. We only trust a *single coherent* ladder (one series + one expiry),
    since mixing series/expiries (e.g. WTI's max/min/threshold markets) yields a
    meaningless number. Returns None if there's no clean threshold ladder.
    """
    ladders = {}
    for m in markets:
        if (m.get("floor") is not None and m.get("cap") is None and m.get("yes_ask")
                and m.get("ticker")):
            key = (m["ticker"].split("-", 1)[0], m.get("close_time"))  # series + expiry
            ladders.setdefault(key, []).append((m["floor"], m["yes_ask"]))
    best = max(ladders.values(), key=len, default=[])
    if len(best) < 2:
        return None
    pts = sorted(best)
    for (s1, y1), (s2, y2) in zip(pts, pts[1:]):
        if (y1 - 50) * (y2 - 50) <= 0 and y1 != y2:      # 50c is bracketed here
            t = (50 - y1) / (y2 - y1)
            return round(s1 + t * (s2 - s1), 2)
    return min(pts, key=lambda p: abs(p[1] - 50))[0]      # else nearest-to-50c strike


def basis_check(spot, markets, tol_pct=2.0, max_real_pct=20.0):
    """Compare our spot to Kalshi's implied center. A small gap is fine; a moderate
    one means our price feed and Kalshi's settlement reference disagree (basis
    risk) so edges are suspect. A huge gap means the ladder isn't a plain
    price-at-expiry ladder (e.g. WTI max/min markets) and basis can't be verified.

    ok: True (aligned) / False (real mismatch, warn) / None (can't verify).
    """
    center = implied_center(markets)
    if not center or not spot:
        return {"center": center, "diff_pct": None, "ok": None}
    diff = round((spot - center) / center * 100, 2)
    if abs(diff) > max_real_pct:
        return {"center": center, "diff_pct": diff, "ok": None, "incomparable": True}
    return {"center": center, "diff_pct": diff, "ok": abs(diff) <= tol_pct}

