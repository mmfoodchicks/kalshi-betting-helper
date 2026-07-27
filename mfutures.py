"""Futures we can actually put a number on -- World Series, division winners,
conference titles, playoff berths, season win totals -- ranked by what they're
worth to hold.

The sibling module (futures.py) reads the whole exchange and makes no claim to
know better than the price. This one is the opposite: it only lists markets that
one of our season simulators already models, so every row can say "the market
says 8c, we say 19%" and mean something.

That difference matters for the thing this is for. A pure market board can never
show a genuinely positive expected return -- at the market's own probability the
fee makes every row slightly negative. A MODEL board can, because the model is
allowed to disagree. A long-dated contract we think is underpriced is the one
case where money sitting still is actually working.

Three things keep that from turning into wishful thinking:

  * The model doesn't get taken at face value. Every sport's number is blended
    with the market using the weight `model_trust` MEASURED for that sport from
    graded results. Where the model has proven it beats the market the blend
    leans on it; where it has proven it doesn't (we measured ~0 for a couple of
    leagues) the blend collapses to the market and the "edge" correctly vanishes
    instead of printing free money.
  * Wild disagreements are flagged, not celebrated. When the model says 22% and
    the book says 3c, the likely explanation is a mis-mapped team or a market
    that means something subtly different -- not a 7x mispricing nobody noticed.
  * Returns are annualized to the actual settlement date, so a division title
    resolving in ten weeks is comparable to a championship resolving in eight
    months, and neither is comparable to the same edge on tonight's game.

Rows are only as good as the simulator behind them; `confidence` and `thin`
travel with each one so a thinly-quoted market can't masquerade as a lock.
"""

import threading
import time as _t

import futures as _fut
import model_trust

# Season simulators, in the order they're most likely to be in season. Each
# entry says how to pull its board and how to read the rows out of it, because
# MLB grew a flat `edges` list while the newer boards use a market->teams dict.
_SPORTS = ("mlb", "nfl", "cfb", "nba", "nhl", "wnba")

_LABEL = {"mlb": "⚾ MLB", "nfl": "🏈 NFL", "cfb": "🏈 CFB",
          "nba": "🏀 NBA", "nhl": "🏒 NHL", "wnba": "🏀 WNBA"}

# Human names for the market kinds the boards emit, normalized across sports
# (MLB says "pennant", the NBA means "conference", they're the same bet).
_MARKET = {
    "world_series": "Championship", "champ": "Championship",
    "pennant": "Conference / pennant", "conf": "Conference / pennant",
    "division": "Division", "playoffs": "Make the playoffs",
    "cfp": "Make the playoff", "win_total": "Season win total",
}

# A disagreement past this is far more likely to be a mapping bug, a market that
# means something subtly different, or an unvalidated preseason model than a real
# edge -- so it's flagged and left out of the default board.
#
# The ratio and the gap are checked INDEPENDENTLY, which matters more than it
# sounds. Requiring both missed the worst offenders: a 1c national-championship
# market against a 13% preseason model is a thirteen-fold disagreement, but the
# absolute gap is only 12 points, so an AND rule waved it straight through to the
# top of the board on a +608% expected return. Cheap markets are exactly where
# the ratio is the honest signal and the gap is meaningless.
_ABSURD_RATIO = 3.0
_ABSURD_GAP = 30.0

_TTL = 1800               # season sims move slowly; half an hour is plenty
_cache = {}
_inflight = set()


def _norm_market(mtype):
    m = (mtype or "").lower()
    if m.startswith("win_"):
        return "win_total"
    return m


# Where a row carries no Kalshi ticker -- MLB's World Series and pennant rows can
# be priced off Polymarket alone -- the horizon comes from the Kalshi SERIES that
# settles the same event. Soonest wins: these series list future seasons too, and
# the current one is what a position held today actually resolves against.
_SERIES_FOR = {
    ("mlb", "world_series"): ["KXMLB"],
    ("mlb", "pennant"): ["KXMLBAL", "KXMLBNL"],
    ("mlb", "division"): ["KXMLBALEAST", "KXMLBALCENT", "KXMLBALWEST",
                          "KXMLBNLEAST", "KXMLBNLCENT", "KXMLBNLWEST"],
    ("mlb", "playoffs"): ["KXMLBPLAYOFFS"],
    # MLB lists win totals as one series PER TEAM (KXMLBWINS-MIL), so there is no
    # single series to ask. They all settle when the regular season ends, so a
    # couple of probes date the whole market rather than thirty requests.
    ("mlb", "win_total"): ["KXMLBWINS-MIL", "KXMLBWINS-NYY", "KXMLBWINS-LAD"],
    ("nfl", "win_total"): ["KXNFLWINS"],
    ("nfl", "world_series"): ["KXNFLSB", "KXNFLCHAMP"],
    ("cfb", "champ"): ["KXNCAAF"],
    ("cfb", "cfp"): ["KXNCAAFPLAYOFF"],
}


def _close_days():
    """({ticker: days}, {series prefix: days}) for the handful of series we use.

    This asks Kalshi directly for each series rather than mining the exchange-wide
    sweep. The sweep is forty paginated pages and a single failed page truncates
    the chain -- which silently produced a half-built index and left most rows
    undated. Fifteen small, independent requests can't fail that way: a series
    that doesn't answer costs its own rows a date and nothing else.
    """
    import kalshi
    from concurrent.futures import ThreadPoolExecutor
    wanted = sorted({p for ps in _SERIES_FOR.values() for p in ps})

    def one(pre):
        try:
            d = kalshi._get_json(
                f"{kalshi.BASE}/markets?series_ticker={pre}&status=open&limit=500",
                timeout=25)
        except Exception:
            return pre, {}, None
        best, tick = None, {}
        for m in d.get("markets") or []:
            days = _fut.settles_in(m)
            if days is None or days <= 0:
                continue
            t = m.get("ticker")
            if t:
                tick[t] = days
            # All of a season's markets settle together; the soonest is this
            # season's, and later ones belong to seasons after it.
            if best is None or days < best:
                best = days
        return pre, tick, best

    by_ticker, by_series = {}, {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for pre, tick, best in ex.map(one, wanted):
            by_ticker.update(tick)
            if best is not None:
                by_series[pre] = best
    return by_ticker, by_series


def _days_for(sport, mtype, ticker, by_ticker, by_series):
    d = by_ticker.get(ticker) if ticker else None
    if d is not None:
        return d
    for pre in _SERIES_FOR.get((sport, _norm_market(mtype)), ()):
        if pre in by_series:
            return by_series[pre]
    return None


def _row(sport, mtype, label, model_pct, price_cents, extra, days):
    """One modeled future, costed. None when it can't be priced or dated."""
    if price_cents is None or not (0 < price_cents < 100) or model_pct is None:
        return None
    w = model_trust.weight(sport)
    # The honest probability: our number pulled toward the market by exactly how
    # much that sport's model has earned. w=0 means "we measured this model and
    # it doesn't beat the price" -- the row then correctly shows no edge.
    fair = w * float(model_pct) + (1.0 - w) * price_cents
    fee = _fut.fee_cents(price_cents)
    ev_cents = (fair / 100.0) * 100.0 - price_cents - fee
    ev_pct = ev_cents / price_cents * 100.0
    apy = None
    if days and days > 0:
        years = days / 365.0
        base = 1.0 + ev_pct / 100.0
        if base > 0:
            try:
                apy = (base ** (1.0 / years) - 1.0) * 100.0
            except OverflowError:
                apy = None
        else:
            apy = -100.0
    if apy is not None:
        apy = max(-100.0, min(100000.0, apy))
    edge = float(model_pct) - price_cents
    ratio = (float(model_pct) / price_cents) if price_cents else 0
    absurd = (ratio >= _ABSURD_RATIO or ratio <= 1.0 / _ABSURD_RATIO
              or abs(edge) >= _ABSURD_GAP)
    return {
        "sport": sport,
        "sport_label": _LABEL.get(sport, sport.upper()),
        "market": _norm_market(mtype),
        "market_label": _MARKET.get(_norm_market(mtype), (mtype or "").replace("_", " ").title()),
        "label": label,
        "team": extra.get("team"),
        "model_pct": round(float(model_pct), 1),
        "price_cents": round(float(price_cents), 1),
        "fair_pct": round(fair, 1),
        "trust": round(w, 2),
        "edge": round(edge, 1),                 # raw model disagreement
        "ev_pct": round(ev_pct, 1),             # expected return, trust-weighted
        "apy_pct": round(apy, 1) if apy is not None else None,
        "days": round(days, 1) if days else None,
        "payout_x": round(100.0 / price_cents, 2),
        "fee_cents": round(fee, 2),
        "ticker": extra.get("ticker"),
        "volume": extra.get("volume"),
        "thin": bool(extra.get("thin")),
        "confidence": extra.get("confidence") or "med",
        "book": extra.get("best_book"),
        "suspect": absurd,
    }


def _collect_mlb(by_ticker, by_series):
    import season_sim
    d = season_sim.futures_edges()
    out = []
    for r in (d or {}).get("edges") or []:
        price = r.get("market_cents")
        days = _days_for("mlb", r.get("type"), r.get("ticker"), by_ticker, by_series)
        row = _row("mlb", r.get("type"), r.get("label"), r.get("model_pct"), price,
                   r, days)
        if row:
            out.append(row)
    return out


def _collect_board(sport, board, by_ticker, by_series):
    """NFL / CFB / pro-league boards: {markets: {key: {label, teams: [...]}}}."""
    out = []
    for key, blk in ((board or {}).get("markets") or {}).items():
        for t in blk.get("teams") or []:
            price = t.get("kalshi_cents")
            if price is None:
                price = t.get("market_cents")
            name = t.get("team") or t.get("abbr") or ""
            lbl = f"{name} — {blk.get('label') or key}"
            row = _row(sport, key, lbl, t.get("model_pct"), price,
                       {**t, "team": name},
                       _days_for(sport, key, t.get("ticker"), by_ticker, by_series))
            if row:
                out.append(row)
    return out


def _collect_pro(sport, by_ticker, by_series):
    """NBA / NHL / WNBA off the shared projection engine + the futures price map."""
    import pro_sim
    import pro_prices
    proj = pro_sim.project(sport)
    if not proj:
        return []
    try:
        proj = pro_prices.attach(sport, proj) or proj
    except Exception:
        pass
    out = []
    fields = (("champ", "champ_pct", "Championship"),
              ("conf", "conf_pct", "Conference"),
              ("division", "division_pct", "Division"),
              ("playoffs", "playoff_pct", "Make the playoffs"))
    for key, pct_key, lbl in fields:
        for t in proj.get("teams") or []:
            pct = t.get(pct_key)
            price = (t.get(f"{key}_cents") or t.get("kalshi_cents")
                     if key == "champ" else t.get(f"{key}_cents"))
            row = _row(sport, key, f"{t.get('team') or t.get('abbr')} — {lbl}",
                       pct, price, {**t, "team": t.get("team") or t.get("abbr")},
                       _days_for(sport, key, t.get(f"{key}_ticker"), by_ticker, by_series))
            if row:
                out.append(row)
    return out


def _build():
    by_ticker, by_series = _close_days()
    out = []
    for sport in _SPORTS:
        try:
            if sport == "mlb":
                out += _collect_mlb(by_ticker, by_series)
            elif sport == "nfl":
                import nfl_season
                out += _collect_board("nfl", nfl_season.futures_board(), by_ticker, by_series)
            elif sport == "cfb":
                import cfb
                out += _collect_board("cfb", cfb.futures_board(), by_ticker, by_series)
            else:
                out += _collect_pro(sport, by_ticker, by_series)
        except Exception:
            continue        # a sport out of season shouldn't sink the board
    return out


def rows(block=False):
    """Every modeled future, costed. Non-blocking: the season sims behind this
    take tens of seconds cold, so it builds off-thread and serves the last good
    board meanwhile."""
    key = ("mfut",)
    hit = _cache.get(key)
    if hit and (_t.time() - hit[0]) < _TTL:
        return hit[1]
    if block:
        val = _build()
        _cache[key] = (_t.time(), val)
        return val
    if key not in _inflight:
        _inflight.add(key)

        def _bg():
            try:
                val = _build()
                if val:
                    _cache[key] = (_t.time(), val)
            finally:
                _inflight.discard(key)
        threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None


_SORTS = {
    "best": lambda r: -(r["apy_pct"] if r["apy_pct"] is not None else -1e9),
    "worst": lambda r: (r["apy_pct"] if r["apy_pct"] is not None else 1e9),
    "edge": lambda r: -r["edge"],
    "soonest": lambda r: (r["days"] if r["days"] is not None else 1e9),
    "latest": lambda r: -(r["days"] if r["days"] is not None else -1e9),
    "safest": lambda r: -r["fair_pct"],
    "cheapest": lambda r: r["price_cents"],
}


def board(q="", sort="best", sports=None, markets=None, min_prob=0.0,
          max_days=None, limit=60, include_suspect=False, positive_only=True):
    rs = rows()
    if rs is None:
        return {"building": True, "rows": [], "total": 0, "universe": 0,
                "sorts": list(_SORTS.keys())}
    universe = len(rs)
    ql = (q or "").strip().lower()
    if ql:
        terms = ql.split()
        rs = [r for r in rs
              if all(t in f"{r['label']} {r['sport']} {r['market_label']}".lower()
                     for t in terms)]
    if sports:
        want = {s.lower() for s in sports}
        rs = [r for r in rs if r["sport"] in want]
    if markets:
        want = {m.lower() for m in markets}
        rs = [r for r in rs if r["market"] in want]
    if min_prob:
        rs = [r for r in rs if r["fair_pct"] >= min_prob]
    if max_days:
        rs = [r for r in rs if r["days"] is not None and r["days"] <= max_days]
    if not include_suspect:
        rs = [r for r in rs if not r["suspect"]]
    if positive_only:
        rs = [r for r in rs if r["ev_pct"] > 0]
    rs = sorted(rs, key=_SORTS.get(sort) or _SORTS["best"])
    return {
        "rows": rs[:max(1, min(400, limit))],
        "total": len(rs),
        "universe": universe,
        "sort": sort if sort in _SORTS else "best",
        "sorts": list(_SORTS.keys()),
        "sports": sorted({r["sport"] for r in rows() or []}),
        "markets": sorted({r["market"] for r in rows() or []}),
        "note": ("Expected return uses our simulation blended with the market at "
                 "the weight that sport's model has actually EARNED on graded "
                 "results — so a league where the model has been measured no "
                 "better than the price shows no edge, by construction. It is "
                 "still a forecast: if the model is wrong the edge is imaginary, "
                 "and the contract pays nothing rather than a little less."),
    }
