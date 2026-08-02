"""Live Kalshi market data (public, read-only -- no API key required).

Kalshi exposes market data without authentication. We use it to pull the real,
currently-open crypto contracts and their live YES/NO prices so the app can
compare its model's fair value against the actual market instead of making you
type the price in by hand.

Series ticker convention (crypto):
  - 15-minute markets : KX<COIN>15M   e.g. KXBTC15M
  - hourly markets    : KX<COIN>       e.g. KXBTC
  - daily markets     : KX<COIN>D      e.g. KXBTCD

Not every coin has every timeframe; if a series has no open markets we simply
return an empty list and the UI says so.
"""

import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_USER_AGENT = "kalshi-betting-helper/1.0"

# Coins that exist on both our price feed (prices.py) and Kalshi crypto series.
SCANNABLE_COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BCH", "LTC", "AVAX", "LINK"]

TIMEFRAMES = {
    "15M": "15-minute",
    "hourly": "hourly",
    "daily": "daily",
}


def series_ticker(coin, timeframe):
    coin = coin.upper()
    if timeframe == "15M":
        return f"KX{coin}15M"
    if timeframe == "hourly":
        return f"KX{coin}"
    if timeframe == "daily":
        return f"KX{coin}D"
    raise ValueError(f"unknown timeframe '{timeframe}'")


def _get_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_time(s):
    """ISO8601 (e.g. '2026-06-19T16:30:00Z') -> epoch seconds."""
    if not s:
        return None
    return int(datetime.fromisoformat(s.replace("Z", "+00:00"))
               .astimezone(timezone.utc).timestamp())


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def markets_for_series(series_ticker_, limit=100):
    """Normalized open markets for any series ticker (crypto, commodities, ...).

    Each item: ticker, title, subtitle, strike_type, floor, cap, close_time
    (epoch), yes_bid, yes_ask, no_bid, no_ask, last (CENTS), volume.
    """
    params = urllib.parse.urlencode({
        "series_ticker": series_ticker_, "status": "open", "limit": limit,
    })
    data = _get_json(f"{BASE}/markets?{params}")
    out = []
    for m in data.get("markets", []):
        out.append({
            "ticker": m.get("ticker"),
            "title": m.get("title"),
            "subtitle": m.get("yes_sub_title") or m.get("subtitle"),
            "strike_type": m.get("strike_type"),
            "floor": _f(m.get("floor_strike")),
            "cap": _f(m.get("cap_strike")),
            "close_time": _parse_time(m.get("close_time")),
            # Kalshi prices come as dollars (0..1); convert to cents (0..100).
            "yes_bid": _cents(m.get("yes_bid_dollars")),
            "yes_ask": _cents(m.get("yes_ask_dollars")),
            "no_bid": _cents(m.get("no_bid_dollars")),
            "no_ask": _cents(m.get("no_ask_dollars")),
            "last": _cents(m.get("last_price_dollars")),
            "volume": _f(m.get("volume_fp")) or 0,
        })
    out.sort(key=lambda x: (x["close_time"] is None, x["close_time"] or 0))
    return out


def get_open_markets(coin, timeframe, limit=100):
    """Normalized open Kalshi markets for a coin + timeframe."""
    return markets_for_series(series_ticker(coin, timeframe), limit)


def get_market(ticker):
    """Live bid/ask for a single Kalshi market by ticker (cents)."""
    data = _get_json(f"{BASE}/markets/{ticker}")
    m = data.get("market", {})
    return {
        "ticker": m.get("ticker"),
        "status": m.get("status"),
        "result": (m.get("result") or "").lower(),   # 'yes'/'no'/'' once settled
        "close_time": _parse_time(m.get("close_time")),
        "yes_bid": _cents(m.get("yes_bid_dollars")),
        "yes_ask": _cents(m.get("yes_ask_dollars")),
        "no_bid": _cents(m.get("no_bid_dollars")),
        "no_ask": _cents(m.get("no_ask_dollars")),
        "last": _cents(m.get("last_price_dollars")),
    }


# ---- Taker fee -------------------------------------------------------------
# Kalshi charges 0.07 * C * p * (1-p) per order, rounded UP to the cent. This is
# the smooth per-contract expectation of that, which is the right number for edge
# math: a single contract really pays the rounded-up cent, but over any real order
# size the per-contract cost converges here.
#
# THIS IS THE ONE COPY. There were five -- baseball._kalshi_fee, futures.fee_cents,
# combine._fee_cents, bestbets._fee and odds.taker_fee_cents -- each re-deriving
# the same formula, and they had already drifted on rounding. The smoke test that
# was supposed to catch that guarded on `hasattr(combine, "_kalshi_fee")`, a name
# combine never had, so it silently compared three of the five and passed.
def taker_fee_cents(cents):
    """Expected Kalshi taker fee in cents for one contract priced at `cents`.

    ~1.75c at 50c, ~0.6c at 90c, ~0 at the extremes. Returns 0.0 for a price
    outside (0, 100), which is Kalshi's "no offer" sentinel rather than a price."""
    if cents is None:
        return 0.0
    try:
        c = float(cents)
    except (TypeError, ValueError):
        return 0.0
    if not (0 < c < 100):
        return 0.0
    p = c / 100.0
    return 7.0 * p * (1.0 - p)


def _cents(dollars):
    v = _f(dollars)
    return round(v * 100, 1) if v is not None else None
