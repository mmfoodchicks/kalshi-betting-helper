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
import random
import errlog
import time
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
    """GET with two retries on throttling and transient server errors.

    The app now runs three web workers, an always-on board warmer, and slate
    builds in fresh subprocesses -- an order of magnitude more Kalshi requests
    than when this was written, from one egress IP. A single 429 used to raise
    straight through, and one throttled response inside a slate build shipped a
    board with NO prices to every worker: the user stared at "no Kalshi prices
    on the slate" while the exchange was quoting every moneyline on their
    phone. Honour Retry-After when it is sane, back off briefly when it is
    absent, and only then give up."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            try:
                wait = min(5.0, float(e.headers.get("Retry-After") or 0))
            except (TypeError, ValueError):
                wait = 0.0
            time.sleep(wait if wait > 0 else 0.7 * (attempt + 1) + random.random() * 0.4)
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    # All three attempts spent. Callers have their own fallbacks, but the
    # ledger should show a throttling storm as itself, not as its symptoms.
    errlog.note("KAL-fetch", last, path=url.split("?")[0][-120:])
    raise last


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


# --- What Kalshi will actually accept in a parlay ----------------------------
# A "combo" on Kalshi is a MULTIVARIATE EVENT COLLECTION, and a market can only
# be a leg if its EVENT is one of the collection's associated events. That is the
# whole rule, it is published, and it is the only thing worth asking -- every
# heuristic for it is wrong somewhere:
#
#     KXMVESPORTSMULTIGAMEEXTENDED-R   "What will happen across all games?"
#     KXMVECROSSCATEGORY-R             "Will multiple conditions be met?"
#
# Today those two carry 11,922 events, and the tennis slice alone is
#
#     KXATPMATCH 24   KXWTAMATCH 17   KXITFMATCH 95   KXITFWMATCH 87
#     KXATPCHALLENGERMATCH 20   KXWTACHALLENGERMATCH 8
#     KXITFDOUBLES 36   KXITFWDOUBLES 32
#
# which is the correction that mattered: ITF IS combo-eligible, in quantity. The
# codebase had carried "Kalshi does not allow ITF matches as parlay legs" as a
# bare assertion, and it was hiding 87 perfectly good matches.
_COMBO_TTL = 600
_combo_cache = {"ts": 0.0, "events": None}


def combo_events():
    """Set of event tickers Kalshi will accept as a parlay leg. Empty on failure,
    which callers must treat as "unknown", never as "nothing is eligible"."""
    now = time.time()
    if _combo_cache["events"] is not None and now - _combo_cache["ts"] < _COMBO_TTL:
        return _combo_cache["events"]
    out = set()
    try:
        d = _get_json(f"{BASE}/multivariate_event_collections", timeout=25)
        for c in (d.get("multivariate_contracts") or []):
            out |= set(c.get("associated_event_tickers") or [])
    except Exception:
        return _combo_cache["events"] or set()
    _combo_cache["events"] = out
    _combo_cache["ts"] = now
    return out


def combo_ok(event_ticker):
    """True when this event can be a parlay leg. Unknown (feed down) -> True, so
    a fetch failure never silently empties a board."""
    ev = combo_events()
    return (not ev) or (event_ticker in ev)


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


# ---- Price movement (candlesticks) -----------------------------------------
_move_cache = {}


def price_move(ticker, hours=24):
    """Mid-price drift over the window, in cents: {"from", "to", "move", "n"}.

    Where the market has been moving is information the ask alone can't show:
    a pick the market has drifted TOWARD is sharp confirmation, one it has
    drifted away from is the market disagreeing. Hourly candles carry bid/ask
    OHLC even when nothing traded, so the MID close per hour reads a thin book
    too; hours without a sane two-sided quote (empty-book sentinels, 20c+
    spreads) are skipped. Cached 10 minutes; None until two readable hours
    exist."""
    if not ticker:
        return None
    # ttlcache sweep: tickers rotate constantly (a tennis week is hundreds),
    # so a per-ticker cache without eviction only ever grows.
    import ttlcache

    def build():
        try:
            series = ticker.split("-")[0]
            end = int(time.time())
            d = _get_json(f"{BASE}/series/{series}/markets/{ticker}/candlesticks"
                          f"?start_ts={end - hours * 3600}&end_ts={end}"
                          "&period_interval=60", timeout=15)
            mids = []
            for c in d.get("candlesticks") or []:
                a = _f((c.get("yes_ask") or {}).get("close_dollars"))
                b = _f((c.get("yes_bid") or {}).get("close_dollars"))
                if a and b and 0 < b <= a < 1 and (a - b) <= 0.20:
                    mids.append((a + b) / 2.0 * 100.0)
            if len(mids) >= 2:
                return {"from": round(mids[0], 1), "to": round(mids[-1], 1),
                        "move": round(mids[-1] - mids[0], 1), "n": len(mids)}
        except Exception as e:
            errlog.note("KAL-candles", e)
        return None
    return ttlcache.cached(_move_cache, ticker, 600, build)


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
