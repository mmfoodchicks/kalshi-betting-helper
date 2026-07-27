"""Long-dated crypto contracts, priced off realized volatility.

Kalshi lists a year's worth of "will Bitcoin be above $150,000 by Dec 31" style
markets. They're futures in the sense this app means it -- you buy and hold for
months -- and unlike an election they're genuinely modelable, because the only
input is the distribution of where a price can wander in the time remaining.

Two things here are easy to get wrong, and getting either wrong invents an edge
that isn't there.

**These are usually TOUCH markets, not terminal ones.** "Above $150k BY Dec 31"
pays if the price is ever there at any point, not just at the end. The chance of
touching a level is far higher than the chance of finishing above it -- with no
drift it is exactly twice as high (the reflection principle). Pricing a touch
market with a terminal formula halves it, which would have shown Bitcoin's
$100k market at 2.4% against a 16c book and screamed "sell" at a market that is
priced about right. Titles are parsed for "by/before" versus "on/at" and each
gets its own formula.

**Volatility has to match the horizon.** Two weeks of hourly candles put
Bitcoin's annualized vol at 29%; a full year of daily candles puts it at 44%.
For a five-month contract the second is the honest number, and the difference
moves the fair value of an out-of-the-money strike by an order of magnitude. So
the estimate comes from daily candles over as long a window as the exchange
will serve.

No drift is assumed. Claiming to know which way crypto is going is exactly the
kind of thing that turns a model into a story, so the barrier maths runs at
zero drift and the model speaks only about dispersion.
"""

import math
import re
import time as _t

import kalshi
import odds
import prices

# Series we can price, and the coin each one refers to. Every one of these is a
# plain "price above/below a strike by a date" market -- no baskets, no
# multi-leg structures, nothing whose payoff isn't a function of one spot price.
SERIES = {
    "KXBTCMAXY": "BTC", "KXBTCMINY": "BTC", "KXBTCMAX100": "BTC",
    "KXBTCMAX150": "BTC", "KXBTC2026200": "BTC", "KXBTC2026250": "BTC",
    "KXETHMAXY": "ETH", "KXETHMINY": "ETH",
    "KXSOLMAXY": "SOL", "KXSOL26500": "SOL",
    "KXXRPMAXY": "XRP", "KXXRPMINY": "XRP",
    "KXDOGEMAX1": "DOGE",
}

# A contract that pays if the price is EVER through the strike, versus one that
# only looks at the final print. "by"/"before" mean the former.
_TOUCH_RE = re.compile(r"\b(by|before)\b", re.I)

_VOL_TTL = 1800
_cache = {}


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def annual_vol(coin):
    """Annualized volatility from DAILY candles over the longest window the feed
    will give (about a year). Deliberately not the short intraday estimate the
    live scanner uses: a five-month contract cares how far price can travel over
    five months, and two weeks of hourly bars badly understates that."""
    key = ("vol", coin)
    hit = _cache.get(key)
    if hit and _t.time() - hit[0] < _VOL_TTL:
        return hit[1]
    try:
        candles = prices.get_candles(coin, granularity=86400)
        _mu, sigma, n = odds.estimate_params(candles, lookback=5000)
    except Exception:
        return None
    if not sigma or n < 40:
        return None
    val = sigma * math.sqrt(365.0)          # daily sigma -> annual
    _cache[key] = (_t.time(), val)
    return val


def p_touch_above(spot, strike, years, vol):
    """P(price trades AT OR ABOVE `strike` at some point before expiry).

    Reflection principle at zero drift: the running maximum crosses a level with
    twice the probability that the terminal value finishes beyond it."""
    if spot <= 0 or strike <= 0 or years <= 0 or not vol:
        return 1.0 if spot >= strike else 0.0
    if spot >= strike:
        return 1.0                           # already through it
    b = math.log(strike / spot)
    s = vol * math.sqrt(years)
    return max(0.0, min(1.0, 2.0 * _norm_cdf(-b / s)))


def p_touch_below(spot, strike, years, vol):
    """P(price trades AT OR BELOW `strike` at some point before expiry)."""
    if spot <= 0 or strike <= 0 or years <= 0 or not vol:
        return 1.0 if spot <= strike else 0.0
    if spot <= strike:
        return 1.0
    b = math.log(spot / strike)
    s = vol * math.sqrt(years)
    return max(0.0, min(1.0, 2.0 * _norm_cdf(-b / s)))


def p_terminal_above(spot, strike, years, vol):
    """P(price FINISHES above `strike`) -- for markets that read the final print
    on a stated date rather than any touch along the way."""
    if spot <= 0 or strike <= 0 or years <= 0 or not vol:
        return 1.0 if spot >= strike else 0.0
    s = vol * math.sqrt(years)
    # Zero drift in log space means the median finishes at spot, so the -s^2/2
    # convexity term is the whole of the expected log move.
    d = (math.log(spot / strike) - 0.5 * s * s) / s
    return max(0.0, min(1.0, _norm_cdf(d)))


def _model_pct(title, spot, strike, side_above, years, vol):
    touch = bool(_TOUCH_RE.search(title or ""))
    if touch:
        p = (p_touch_above(spot, strike, years, vol) if side_above
             else p_touch_below(spot, strike, years, vol))
    else:
        p = (p_terminal_above(spot, strike, years, vol) if side_above
             else 1.0 - p_terminal_above(spot, strike, years, vol))
    return p * 100.0, ("touch" if touch else "terminal")


def rows(min_days=14):
    """[{...}] one row per priceable long-dated crypto market."""
    import futures as _fut
    out = []
    vols = {}
    for series, coin in SERIES.items():
        if coin not in vols:
            vols[coin] = annual_vol(coin)
        vol = vols[coin]
        if not vol:
            continue
        try:
            spot = prices.get_spot(coin)
        except Exception:
            continue
        if not spot:
            continue
        try:
            d = kalshi._get_json(
                f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=200",
                timeout=25)
        except Exception:
            continue
        for m in d.get("markets") or []:
            days = _fut.settles_in(m)
            if days is None or days < min_days:
                continue
            st = (m.get("strike_type") or "").lower()
            floor_s, cap_s = m.get("floor_strike"), m.get("cap_strike")
            if st in ("greater", "greater_or_equal") and floor_s:
                strike, above = float(floor_s), True
            elif st in ("less", "less_or_equal") and cap_s:
                strike, above = float(cap_s), False
            else:
                continue                     # ranges/baskets: not this model's job
            ask = kalshi._cents(m.get("yes_ask_dollars"))
            if ask is None or not (0 < ask < 100):
                continue
            pct, kind = _model_pct(m.get("title"), spot, strike, above,
                                   days / 365.0, vol)
            out.append({
                "ticker": m.get("ticker"),
                "label": (m.get("title") or "").strip().rstrip("?"),
                "coin": coin, "spot": round(spot, 2), "strike": strike,
                "side": "above" if above else "below",
                "kind": kind, "vol_pct": round(vol * 100, 1),
                "model_pct": round(pct, 1),
                "price_cents": ask,
                "days": days,
                "volume": float(m.get("volume_fp") or 0),
            })
    return out
