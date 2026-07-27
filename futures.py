"""Long-dated Kalshi contracts, ranked the way you'd actually shop for a place
to park money.

The rest of this app hunts for edges -- places our model disagrees with the
price. This board does something different and more honest: it makes no claim to
know better than the market. A contract trading at 96c is saying "96% likely",
and we take that at face value. What the board answers is the question that
actually decides where the money goes:

    given how likely this is and how long my cash is tied up, what does it pay
    per year, and how does that compare to everything else on the exchange?

That last part is why annualized yield is the ranking metric. A contract at 96c
settling in 2030 returns 4.2% -- over three and a half years, which is about
1.2%/yr, worse than a savings account. The SAME 96c settling in 60 days is 4.2%
in two months, about 28%/yr. Identical price, identical risk per dollar, wildly
different instruments. Sorting by raw price or raw return hides that; sorting by
APY surfaces it.

What this is NOT: a savings account. There is no principal protection and no
FDIC anything. A 96c contract loses the entire stake 4% of the time, and 4% is
not "never" -- across a handful of positions it is close to expected. The board
therefore always shows the implied probability next to the yield, refuses to
rank a contract it can't price, and marks how thinly a market trades, because a
yield you can't actually buy (or exit) isn't a yield.

Everything here is market-derived. No model, no edge claim.
"""

import threading
import time as _t
from datetime import datetime, timezone

import kalshi
import racing

# A "future" is something you hold, not something you trade this afternoon.
# Below a couple of weeks the fee and spread dominate any yield calculation and
# the market belongs on the regular boards instead.
MIN_DAYS = 14

# Contracts that have never traded are quotes, not markets: the ask may be a
# market maker's placeholder that fills nothing. Require a real print.
MIN_VOLUME = 25.0

# The default safety floor, and the single most consequential number here.
# Set to 80 the board's top rows were 20-day soccer ties at 80c showing "4390%
# APY" -- which is arithmetically true and completely useless as savings, since
# it is a one-in-five chance of losing the lot. At 95 the default view is
# actually the thing that was asked for; the caller can lower it deliberately.
DEFAULT_MIN_PROB = 95.0

# Annualizing a very short hold is where APY stops meaning anything: a 10-day
# contract compounds 36 times a year, and the maths quietly assumes you can find
# the same trade 36 times running and survive every one. Past this horizon the
# APY is still shown but flagged, so a huge number can't be read as free money.
SHORT_TERM_DAYS = 45

_SWEEP_TTL = 900          # 15 min; long-dated prices move slowly
_PAGE = 1000
_MAX_PAGES = 60

# Kalshi's close_time sentinel for "no fixed end date". Useless as a savings
# instrument -- you cannot annualize a return with no maturity -- so they're cut.
_NO_MATURITY_DAYS = 3650


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _cents(v):
    """Kalshi quotes dollars as strings; the rest of this app works in cents."""
    x = _f(v, None if v is None else 0.0)
    return None if x is None else round(x * 100.0, 2)


def fee_cents(price_cents):
    """Kalshi's taker fee for one contract at `price_cents`: 0.07 x p x (1-p),
    in cents. It peaks at 1.75c mid-book and nearly vanishes at the extremes,
    which is exactly where this board shops -- but on a 4% return even half a
    cent matters, so it comes off the yield rather than being waved away."""
    p = max(0.0, min(1.0, price_cents / 100.0))
    return 7.0 * p * (1.0 - p)


_cache = {}
_inflight = set()


def _sweep(min_days=MIN_DAYS):
    """Every open market closing at least `min_days` out.

    Kalshi filters this server-side via min_close_ts, which matters: the full
    open-market feed is ~61k rows dominated by same-day sports and crypto, and
    paging all of it to throw away 99% would be slow enough to time out.
    """
    cutoff = int(_t.time() + min_days * 86400)
    rows, cursor = [], None
    for _ in range(_MAX_PAGES):
        url = (f"{kalshi.BASE}/markets?status=open&limit={_PAGE}"
               f"&min_close_ts={cutoff}")
        if cursor:
            url += f"&cursor={cursor}"
        try:
            d = kalshi._get_json(url, timeout=30)
        except Exception:
            break
        ms = d.get("markets") or []
        # Drop the untradeable tail here rather than downstream: it is the bulk
        # of the rows (every margin bucket of every midterm race) and carrying
        # it makes each request slower for no benefit.
        for m in ms:
            if _f(m.get("volume_fp")) >= MIN_VOLUME:
                rows.append(m)
        cursor = d.get("cursor")
        if not cursor or not ms:
            break
    return rows


def _days_to(close_iso, now=None):
    if not close_iso:
        return None
    try:
        t = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    return (t - now).total_seconds() / 86400.0


def _tier(prob_pct):
    """How safe the market says this is. Deliberately plain-spoken -- the whole
    point is that the user can see the risk they're taking for the yield."""
    if prob_pct >= 97:
        return "very safe"
    if prob_pct >= 90:
        return "safe"
    if prob_pct >= 80:
        return "moderate"
    if prob_pct >= 60:
        return "risky"
    return "speculative"


def _side_row(m, side, ask, days, now_iso):
    """One buyable side of one market, costed out. None when it can't be priced.

    `ask` is what you pay; settlement pays 100c. The implied probability IS the
    price -- we are not second-guessing it -- so the yield and the chance of
    losing the stake are two views of the same number, shown together.
    """
    if ask is None or not (0 < ask < 100) or not days or days <= 0:
        return None
    fee = fee_cents(ask)
    net_profit = 100.0 - ask - fee          # cents kept per contract if it settles
    if net_profit <= 0:
        return None                         # fee eats the whole spread
    ret = net_profit / ask                  # return over the holding period
    years = days / 365.0
    # Compounding-equivalent annual rate, so a 2-month and a 3-year contract are
    # actually comparable. Capped for display sanity on very short horizons.
    try:
        apy = ((1.0 + ret) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else None
    except OverflowError:
        apy = None
    if apy is not None and apy > 100000:
        apy = 100000.0
    prob = ask                              # cents == implied percent
    # The disclosure that keeps the rest of the row honest. You pay `ask` for
    # something the market says is `ask`% likely, so the expected payout is
    # exactly what you paid and the fee is pure drag: EVERY row here has a
    # slightly negative expected value at the market's own probability. The
    # yield is not an edge, it is what you're paid for carrying the tail risk.
    ev_cents = (prob / 100.0) * 100.0 - ask - fee
    return {
        "ticker": m.get("ticker"),
        "event_ticker": m.get("event_ticker"),
        "title": (m.get("title") or "").strip(),
        "subtitle": ((m.get("yes_sub_title") if side == "yes" else m.get("no_sub_title"))
                     or "").strip(),
        "side": side,
        "cost_cents": round(ask, 1),
        "implied_pct": round(prob, 1),
        "tier": _tier(prob),
        "fee_cents": round(fee, 2),
        "return_pct": round(ret * 100, 2),          # over the whole holding period
        "apy_pct": round(apy, 1) if apy is not None else None,
        "short_term": days < SHORT_TERM_DAYS,        # APY is extrapolated hard
        "loss_pct": round(100.0 - prob, 1),          # chance of losing the stake
        "ev_pct": round(ev_cents / ask * 100, 2),    # expected return, after fee
        "days": round(days, 1),
        "close_time": m.get("close_time"),
        "volume": round(_f(m.get("volume_fp")), 1),
        "open_interest": round(_f(m.get("open_interest_fp")), 1),
        "payout_x": round(100.0 / ask, 3),
        "rules": (m.get("rules_primary") or "")[:400],
    }


def _build_rows(min_days):
    now = datetime.now(timezone.utc)
    out = []
    for m in _sweep(min_days):
        days = _days_to(m.get("close_time"), now)
        if days is None or days < min_days or days > _NO_MATURITY_DAYS:
            continue                         # no maturity -> can't be annualized
        for side, ask in (("yes", _cents(m.get("yes_ask_dollars"))),
                          ("no", _cents(m.get("no_ask_dollars")))):
            r = _side_row(m, side, ask, days, now)
            if r:
                out.append(r)
    return out


def rows(min_days=MIN_DAYS, block=False):
    """Every buyable side of every long-dated market, costed.

    Non-blocking by default. Sweeping Kalshi's long-dated universe is ~40 pages
    and takes half a minute, which is far too long to hold a request open, so a
    cold board builds on a background thread and the caller gets None until it
    lands. A STALE board is still served while the refresh runs -- month-out
    prices don't move fast enough for a few extra minutes to matter, and showing
    the last good board beats showing a spinner.
    """
    key = ("futures_rows", min_days)
    hit = _cache.get(key)
    fresh = hit and (_t.time() - hit[0]) < _SWEEP_TTL
    if fresh:
        return hit[1]
    if block:
        val = _build_rows(min_days)
        _cache[key] = (_t.time(), val)
        return val
    if key not in _inflight:
        _inflight.add(key)

        def _bg():
            try:
                val = _build_rows(min_days)
                if val:
                    _cache[key] = (_t.time(), val)
            finally:
                _inflight.discard(key)
        threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None           # stale if we have it, else "building"


_SORTS = {
    "best": lambda r: -(r["apy_pct"] if r["apy_pct"] is not None else -1e9),
    "worst": lambda r: (r["apy_pct"] if r["apy_pct"] is not None else 1e9),
    "soonest": lambda r: r["days"],
    "latest": lambda r: -r["days"],
    "safest": lambda r: -r["implied_pct"],
    "volume": lambda r: -r["volume"],
}


def board(q="", sort="best", min_prob=DEFAULT_MIN_PROB, max_days=None,
          min_days=MIN_DAYS, limit=60, min_volume=None):
    """The searchable, sortable board.

    `q` matches the title, subtitle and ticker so you can look for a theme
    ("fed", "nba", "bitcoin") rather than scrolling. `min_prob` is the safety
    floor -- the single most important control here, because it decides whether
    you are shopping for a place to park money or for a lottery ticket.
    """
    rs = rows(min_days)
    if rs is None:
        return {"building": True, "rows": [], "total": 0, "universe": 0,
                "sorts": list(_SORTS.keys())}
    universe = len(rs)
    ql = (q or "").strip().lower()
    if ql:
        terms = [t for t in ql.split() if t]
        def hit(r):
            hay = f"{r['title']} {r['subtitle']} {r['ticker']}".lower()
            return all(t in hay for t in terms)
        rs = [r for r in rs if hit(r)]
    if min_prob is not None:
        rs = [r for r in rs if r["implied_pct"] >= min_prob]
    if max_days:
        rs = [r for r in rs if r["days"] <= max_days]
    if min_volume:
        rs = [r for r in rs if r["volume"] >= min_volume]
    key = _SORTS.get(sort) or _SORTS["best"]
    rs = sorted(rs, key=key)
    total = len(rs)
    out = rs[:max(1, min(500, limit))]
    return {
        "rows": out,
        "total": total,
        "sort": sort if sort in _SORTS else "best",
        "min_prob": min_prob,
        "min_days": min_days,
        "max_days": max_days,
        "universe": universe,
        "sorts": list(_SORTS.keys()),
        "note": ("Yield is what the contract pays if it settles your way, net of "
                 "Kalshi's taker fee. The implied % is the market's own estimate of "
                 "that happening — which makes it also your chance of losing the "
                 "whole stake. At the market's own price the expected value of every "
                 "row here is slightly NEGATIVE (the fee); the yield is what you are "
                 "paid for carrying the tail risk, not an edge we've found. This is "
                 "not a savings account — there is no principal protection, and money "
                 "is locked up until settlement."),
    }


def summary(min_days=MIN_DAYS):
    """A quick read of what's on offer at each safety tier -- the fastest way to
    see the risk/return curve before picking a row off it."""
    rs = rows(min_days) or []
    tiers = {}
    for r in rs:
        t = tiers.setdefault(r["tier"], {"n": 0, "best_apy": None, "median_apy": [],
                                         "tier": r["tier"]})
        t["n"] += 1
        if r["apy_pct"] is not None:
            t["median_apy"].append(r["apy_pct"])
            if t["best_apy"] is None or r["apy_pct"] > t["best_apy"]:
                t["best_apy"] = r["apy_pct"]
    order = ["very safe", "safe", "moderate", "risky", "speculative"]
    out = []
    for name in order:
        t = tiers.get(name)
        if not t:
            continue
        vals = sorted(t["median_apy"])
        t["median_apy"] = round(vals[len(vals) // 2], 1) if vals else None
        out.append(t)
    return {"tiers": out, "universe": len(rs)}
