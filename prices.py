"""Live crypto price feed using Coinbase's public API (no API key required).

Provides:
  - get_spot(coin)            -> current spot price (float)
  - get_candles(coin, gran)   -> recent OHLCV candles (oldest first)
  - get_price_at(coin, epoch) -> historical price at/just before a moment in time

Coinbase exposes two relevant public endpoints:
  - https://api.coinbase.com/v2/prices/<PRODUCT>/spot          (simple spot)
  - https://api.exchange.coinbase.com/products/<PRODUCT>/candles (OHLCV history)

We deliberately use only the Python standard library (urllib) so the only
third-party dependency for the whole project is Flask.
"""

import json
import time
import urllib.request
import urllib.error

# Coins we support out of the box. Key is the symbol the UI uses; value is the
# Coinbase product id. Any USD spot market on Coinbase will work if added here.
SUPPORTED_COINS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "DOGE": "DOGE-USD",
    "ADA": "ADA-USD",
    "AVAX": "AVAX-USD",
    "LINK": "LINK-USD",
    "LTC": "LTC-USD",
    "BCH": "BCH-USD",
}

_USER_AGENT = "kalshi-betting-helper/1.0"

# Tiny in-process cache so rapid polling from multiple markets doesn't hammer
# the API. {key: (timestamp, value)}
_cache = {}


def _get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _product(coin):
    coin = (coin or "").upper()
    if coin not in SUPPORTED_COINS:
        raise ValueError(f"Unsupported coin '{coin}'. Supported: {', '.join(SUPPORTED_COINS)}")
    return SUPPORTED_COINS[coin]


def get_spot(coin, max_age=2.0):
    """Return current spot price in USD as a float."""
    product = _product(coin)
    ckey = ("spot", product)
    now = time.time()
    cached = _cache.get(ckey)
    if cached and now - cached[0] < max_age:
        return cached[1]
    url = f"https://api.coinbase.com/v2/prices/{product}/spot"
    data = _get_json(url)
    price = float(data["data"]["amount"])
    _cache[ckey] = (now, price)
    return price


def get_history(coin, granularity=60, count=1500):
    """Page back through Coinbase candles to assemble a longer history.

    Coinbase returns at most ~300 candles per request, so we walk backwards in
    time windows until we have `count` candles. Returns oldest-first, deduped.
    Used by the backtester, so it is not cached.
    """
    product = _product(coin)
    by_time = {}
    end = int(time.time())
    window = 300 * granularity
    # A little headroom on the number of pages so gaps don't starve us.
    for _ in range(count // 250 + 3):
        start = end - window
        url = (
            f"https://api.exchange.coinbase.com/products/{product}/candles"
            f"?granularity={granularity}&start={start}&end={end}"
        )
        try:
            raw = _get_json(url)
        except Exception:
            break
        if not raw:
            break
        for c in raw:
            by_time[int(c[0])] = {
                "time": int(c[0]), "low": float(c[1]), "high": float(c[2]),
                "open": float(c[3]), "close": float(c[4]), "volume": float(c[5]),
            }
        end = start
        if len(by_time) >= count:
            break
        time.sleep(0.15)  # be gentle with the public API
    candles = sorted(by_time.values(), key=lambda c: c["time"])
    return candles[-count:]


def get_candles(coin, granularity=60, max_age=10.0):
    """Return recent OHLCV candles, oldest first.

    Each candle is a dict: {time, low, high, open, close, volume}.
    granularity is in seconds (60, 300, 900, 3600, ...). Coinbase returns up to
    ~300 candles, newest first; we reverse to oldest-first for analysis.
    """
    product = _product(coin)
    ckey = ("candles", product, granularity)
    now = time.time()
    cached = _cache.get(ckey)
    if cached and now - cached[0] < max_age:
        return cached[1]
    url = (
        f"https://api.exchange.coinbase.com/products/{product}/candles"
        f"?granularity={granularity}"
    )
    raw = _get_json(url)
    candles = [
        {
            "time": int(c[0]),
            "low": float(c[1]),
            "high": float(c[2]),
            "open": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
        }
        for c in raw
    ]
    candles.sort(key=lambda c: c["time"])
    _cache[ckey] = (now, candles)
    return candles


def get_price_at(coin, epoch):
    """Best-effort historical price at a past moment (epoch seconds).

    Used to resolve a market once its window closes. We pull 1-minute candles
    spanning the target time and return the close of the candle covering it.
    Falls back to current spot if no candle is found (e.g. very recent close).
    """
    product = _product(coin)
    start = int(epoch) - 180
    end = int(epoch) + 60
    url = (
        f"https://api.exchange.coinbase.com/products/{product}/candles"
        f"?granularity=60&start={start}&end={end}"
    )
    try:
        raw = _get_json(url)
    except Exception:
        raw = []
    best = None
    for c in raw:
        t = int(c[0])
        if t <= epoch and (best is None or t > best[0]):
            best = (t, float(c[4]))  # close price
    if best is not None:
        return best[1]
    # Fallback: if the close just happened, spot is close enough.
    return get_spot(coin)
