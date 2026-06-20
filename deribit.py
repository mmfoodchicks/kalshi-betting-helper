"""Deribit DVOL — the sharp crypto options market's implied volatility.

Deribit is the deepest crypto options venue, and its DVOL index is an annualized
30-day implied volatility (like the VIX for crypto). Comparing Kalshi's
ladder-implied vol to Deribit's DVOL tells us whether Kalshi is pricing
volatility richer or cheaper than the smartest options money — a real edge
signal. Public API, no key. Only BTC and ETH have a DVOL index.
"""

import json
import time
import urllib.request

_cache = {}


def get_dvol(coin):
    """Latest DVOL (annualized implied vol, in %) for BTC/ETH, else None."""
    coin = (coin or "").upper()
    if coin not in ("BTC", "ETH"):
        return None
    hit = _cache.get(coin)
    now = time.time()
    if hit and now - hit[0] < 300:
        return hit[1]
    end = int(now * 1000)
    start = int((now - 7200) * 1000)
    url = ("https://www.deribit.com/api/v2/public/get_volatility_index_data"
           f"?currency={coin}&start_timestamp={start}&end_timestamp={end}&resolution=3600")
    val = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kalshi-betting-helper/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read()).get("result", {}).get("data", [])
        if data:
            val = round(float(data[-1][4]), 1)  # latest close
    except Exception:
        val = None
    _cache[coin] = (now, val)
    return val
