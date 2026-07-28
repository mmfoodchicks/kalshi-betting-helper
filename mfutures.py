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

# Non-sport models. Anything here prices its own markets end-to-end and returns
# rows in the same shape, so the board treats a Bitcoin strike exactly like a
# division title: our number, the book's number, and what the difference is worth.
_OTHER = ("crypto", "climate")

_LABEL = {"mlb": "⚾ MLB", "nfl": "🏈 NFL", "cfb": "🏈 CFB",
          "nba": "🏀 NBA", "nhl": "🏒 NHL", "wnba": "🏀 WNBA",
          "crypto": "⚡ Crypto", "climate": "🌡️ Climate"}

# Human names for the market kinds the boards emit, normalized across sports
# (MLB says "pennant", the NBA means "conference", they're the same bet).
_MARKET = {
    "world_series": "Championship", "champ": "Championship",
    "pennant": "Conference / pennant", "conf": "Conference / pennant",
    "division": "Division", "playoffs": "Make the playoffs",
    "cfp": "Make the playoff", "win_total": "Season win total",
    "price_level": "Price level", "temperature": "Global temperature",
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
_progress = {}      # key -> sources finished so far, while a build runs


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
    ("nfl", "world_series"): ["KXSB"],
    ("nfl", "pennant"): ["KXNFLAFCCHAMP", "KXNFLNFCCHAMP"],
    ("nfl", "division"): ["KXNFLAFCEAST", "KXNFLAFCNORTH", "KXNFLAFCSOUTH",
                          "KXNFLAFCWEST", "KXNFLNFCEAST", "KXNFLNFCNORTH",
                          "KXNFLNFCSOUTH", "KXNFLNFCWEST"],
    ("cfb", "champ"): ["KXNCAAF"],
    ("cfb", "cfp"): ["KXNCAAFPLAYOFF"],
}


def _close_days():
    """({ticker: days}, {series prefix: days}, {ticker: NO ask}) for our series.

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
            return pre, {}, None, {}
        best, tick, nos = None, {}, {}
        for m in d.get("markets") or []:
            days = _fut.settles_in(m)
            if days is None or days <= 0:
                continue
            t = m.get("ticker")
            if t:
                tick[t] = days
                # Kalshi quotes NO separately, and the two sides each carry the
                # spread, so the NO price is never just 100 minus the YES.
                na = kalshi._cents(m.get("no_ask_dollars"))
                if na is not None and 0 < na < 100:
                    nos[t] = na
            # All of a season's markets settle together; the soonest is this
            # season's, and later ones belong to seasons after it.
            if best is None or days < best:
                best = days
        return pre, tick, best, nos

    by_ticker, by_series, by_no = {}, {}, {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for pre, tick, best, nos in ex.map(one, wanted):
            by_ticker.update(tick)
            by_no.update(nos)
            if best is not None:
                by_series[pre] = best
    return by_ticker, by_series, by_no


def _days_for(sport, mtype, ticker, by_ticker, by_series):
    d = by_ticker.get(ticker) if ticker else None
    if d is not None:
        return d
    for pre in _SERIES_FOR.get((sport, _norm_market(mtype)), ()):
        if pre in by_series:
            return by_series[pre]
    return None


def _no_row(row, no_ask):
    """The other side of a modeled future.

    We were only ever offering YES, which throws away half the board: when the
    model says a team makes the playoffs 38% of the time and the book wants 55c,
    the bet isn't "don't buy", it's BUY NO at whatever NO costs. Priced off
    Kalshi's own no_ask rather than 100-minus-yes, because both sides carry the
    spread and inverting the YES price would overstate the return."""
    if not row or no_ask is None or not (0 < no_ask < 100):
        return None
    out = _row(row["sport"], row["market"], f"NO — {row['label']}",
               100.0 - row["model_pct"], no_ask,
               {"team": row.get("team"), "ticker": row.get("ticker"),
                "volume": row.get("volume"), "thin": row.get("thin"),
                "confidence": row.get("confidence"), "best_book": row.get("book")},
               row.get("days"))
    if out:
        out["side"] = "no"
        out["in_season"] = row.get("in_season", True)
    return out


def _row(sport, mtype, label, model_pct, price_cents, extra, days):
    """One modeled future, costed. None when it can't be priced or dated."""
    if price_cents is None or not (0 < price_cents < 100) or model_pct is None:
        return None
    w = model_trust.weight(sport)
    # Whether that weight is a MEASURED read or just the cautious default. The
    # difference matters more than the number: MLB's 0.57 comes from 131 graded
    # results, while NFL's 0.50 means "we have never checked". Both render as a
    # trust figure, and without this flag a preseason model with no track record
    # looks exactly as authoritative as one that has earned its weight.
    _rec = (model_trust.load().get("weights") or {}).get(sport) or {}
    measured_n = _rec.get("n") or 0
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
        "trust_measured": bool(measured_n),
        "trust_n": measured_n,
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
        "side": "yes",
        # Continuous markets (crypto, climate) are always live; a league is only
        # "in season" once its teams have actually played, which is what lets the
        # board hide a sport whose year hasn't started.
        "in_season": bool(extra.get("in_season", True)),
    }


def _collect_mlb(by_ticker, by_series, by_no):
    import season_sim
    d = season_sim.futures_edges()
    out = []
    for r in (d or {}).get("edges") or []:
        price = r.get("market_cents")
        days = _days_for("mlb", r.get("type"), r.get("ticker"), by_ticker, by_series)
        row = _row("mlb", r.get("type"), r.get("label"), r.get("model_pct"), price,
                   {**r, "in_season": True}, days)
        if row:
            out.append(row)
            no = _no_row(row, by_no.get(r.get("ticker")))
            if no:
                out.append(no)
    return out


def _nfl_second_opinion():
    """{abbr: championship %} from the OTHER NFL season sim we run nightly.

    We have two, and they disagree. nfl_season builds a roster-aware rating and
    then lets an external win-projection source overwrite it wholesale, while
    pro_sim keeps the roster read -- which for Seattle is the difference between
    6.85% and 14.32% to win the Super Bowl, on the same 4,000 seasons. Until one
    of them is validated against graded results there is no basis for picking a
    winner, so the board cross-checks them and refuses to present a number both
    halves of the app can't agree on."""
    try:
        import deep_cache
        payload, _ts = deep_cache.load("nfl")
        out = {}
        for t in (payload or {}).get("teams") or []:
            ab = t.get("abbrev") or t.get("abbr")
            if not ab:
                continue
            out[ab] = {"champ": t.get("champ_pct"), "wins": t.get("proj_wins")}
        return out
    except Exception:
        return {}


# How far the two NFL sims may differ before their rows are treated as unsafe.
_SECOND_OPINION_RATIO = 1.6
# ...and how far their projected win totals may differ. Kalshi lists no NFL
# championship market right now, so EVERY bettable NFL row is a win total: if
# the check only covered championships it would never fire on anything you
# could actually buy. Three quarters of a win is roughly one line on the ladder.
_SECOND_OPINION_WINS = 0.75


def _collect_board(sport, board, by_ticker, by_series, by_no):
    """NFL / CFB / pro-league boards: {markets: {key: {label, teams: [...]}}}."""
    out = []
    # A league whose teams have all played zero games is between seasons: its
    # futures are listed but nothing has happened yet, which is exactly what the
    # "in season only" filter is for.
    played = False
    for blk in ((board or {}).get("markets") or {}).values():
        for t in blk.get("teams") or []:
            if (t.get("wins") or 0) + (t.get("losses") or 0) > 0:
                played = True
                break
        if played:
            break
    second = _nfl_second_opinion() if sport == "nfl" else {}
    for key, blk in ((board or {}).get("markets") or {}).items():
        for t in blk.get("teams") or []:
            price = t.get("kalshi_cents")
            if price is None:
                price = t.get("market_cents")
            name = t.get("team") or t.get("abbr") or ""
            lbl = f"{name} — {blk.get('label') or key}"
            row = _row(sport, key, lbl, t.get("model_pct"), price,
                       {**t, "team": name, "in_season": played},
                       _days_for(sport, key, t.get("ticker"), by_ticker, by_series))
            if row:
                # Championship-style NFL rows get checked against the other sim;
                # a wide split means we don't actually know our own number, which
                # is worse than having none, so the row is flagged out of the
                # default board rather than quietly shown as an edge.
                alt = second.get(t.get("abbr")) or {}
                mk = _norm_market(key)
                if mk == "world_series" and alt.get("champ") is not None and row["model_pct"] > 0:
                    o = float(alt["champ"])
                    r = max(o, row["model_pct"]) / max(0.01, min(o, row["model_pct"]))
                    if r >= _SECOND_OPINION_RATIO:
                        row["suspect"] = True
                        row["disagreement"] = f"our other NFL sim says {o:.1f}%"
                elif mk == "win_total" and alt.get("wins") is not None:
                    mine = t.get("proj_wins")
                    if mine is not None and abs(float(alt["wins"]) - float(mine)) >= _SECOND_OPINION_WINS:
                        row["suspect"] = True
                        row["disagreement"] = (f"our two NFL sims project {float(mine):.1f} "
                                               f"vs {float(alt['wins']):.1f} wins")
                out.append(row)
                no = _no_row(row, by_no.get(t.get("ticker")))
                if no:
                    if row.get("disagreement"):
                        no["suspect"] = True
                        no["disagreement"] = row["disagreement"]
                    out.append(no)
    return out


def _has_listed_futures(sport):
    """Does this league have ANY open futures market right now?

    Cheap gate in front of an expensive simulation. Out of season a league's
    futures simply aren't listed yet, and running its season sim to produce rows
    that can never be priced cost ~100 seconds of every cold build across the
    three of them -- most of the wait before the board could show anything."""
    import kalshi
    import pro_prices
    from concurrent.futures import ThreadPoolExecutor
    tickers = [t for group in (pro_prices.SERIES.get(sport) or {}).values()
               for t in group]
    if not tickers:
        return False

    def probe(t):
        try:
            d = kalshi._get_json(
                f"{kalshi.BASE}/markets?series_ticker={t}&status=open&limit=1",
                timeout=15)
            return bool(d.get("markets"))
        except Exception:
            return False
    with ThreadPoolExecutor(max_workers=6) as ex:
        return any(ex.map(probe, tickers))


def _collect_pro(sport, by_ticker, by_series, by_no):
    """NBA / NHL / WNBA off the shared projection engine + the futures price map."""
    import pro_sim
    import pro_prices
    if not _has_listed_futures(sport):
        return []                            # out of season: don't run the sim
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


def _collect_crypto():
    """Long-dated crypto price levels, priced off realized volatility.

    Its own module owns the hard parts (touch versus terminal payoff, and a
    horizon-matched vol estimate); here it just gets mapped into a board row."""
    import crypto_futures
    out = []
    for r in crypto_futures.rows():
        row = _row("crypto", "price_level", r["label"], r["model_pct"],
                   r["price_cents"],
                   {"team": r["coin"], "ticker": r["ticker"],
                    "volume": r["volume"], "in_season": True,
                    # A touch market read as terminal (or vice versa) is the main
                    # way this can be wrong, so the payoff kind rides along.
                    "confidence": "med" if r["kind"] == "touch" else "high"},
                   r["days"])
        if row:
            row["detail"] = (f"{r['coin']} ${r['spot']:,.0f} → ${r['strike']:,.0f} "
                             f"· {r['kind']} · {r['vol_pct']:.0f}% vol")
            out.append(row)
            no = _no_row(row, r.get("no_cents"))
            if no:
                out.append(no)
    return out


def _collect_climate():
    """Global-temperature markets, against the NASA GISS series they settle on.

    Rare among these: the model reads the exact index the contract pays out from,
    so there is no gap between what we forecast and what resolves. Months already
    published are fixed, and only the rest of the year is uncertain -- the same
    partial-information shape as resuming a live game."""
    import re
    import kalshi
    import climate_model as cmod
    import futures as _fut
    out = []
    year = None
    for series_t in ("KXGTEMP", "KXHMONTH", "KXHMONTHRANGE"):
        try:
            d = kalshi._get_json(
                f"{kalshi.BASE}/markets?series_ticker={series_t}&status=open&limit=100",
                timeout=25)
        except Exception:
            continue
        for m in d.get("markets") or []:
            days = _fut.settles_in(m)
            ask = kalshi._cents(m.get("yes_ask_dollars"))
            vol = float(m.get("volume_fp") or 0)
            if days is None or days <= 0 or ask is None or not (0 < ask < 100):
                continue
            # These ladders carry untraded placeholder quotes: the same month was
            # listed at 75c for "above 1.30" AND 71c for "below 1.03", which
            # cannot both be true. An ask nobody has traded against isn't a price.
            if vol < _fut.MIN_VOLUME:
                continue
            title = (m.get("title") or "").strip()
            rules = m.get("rules_primary") or ""
            pct = None
            if series_t == "KXGTEMP":
                ym = re.search(r"(20\d\d) be the hottest year", title)
                if not ym:
                    continue
                year = int(ym.group(1))
                # Some of these add an explicit degree bar on top of "beat every
                # prior year"; take it from the rules rather than assuming.
                fm = re.search(r"([0-9]+\.[0-9]+)\s*degrees", rules)
                pct = cmod.p_hottest_year(year, floor=float(fm.group(1)) if fm else None)
            elif series_t == "KXHMONTH":
                mm = re.search(r"hottest\s+([A-Za-z]{3})", title)
                ym = re.search(r"([A-Za-z]{3})\s+(20\d\d)", title)
                if not (mm and ym):
                    continue
                idx = cmod.month_index(mm.group(1))
                year = int(ym.group(2))
                if idx is None:
                    continue
                pct = cmod.p_hottest_month(year, idx)
            else:                                   # anomaly ladder
                ym = re.search(r"([A-Za-z]{3})\s+(20\d\d)", title)
                thr = re.search(r"(above|below)\s+([0-9]+\.[0-9]+)", rules, re.I)
                if not (ym and thr):
                    continue
                idx = cmod.month_index(ym.group(1))
                year = int(ym.group(2))
                if idx is None:
                    continue
                lvl = float(thr.group(2))
                p_above = cmod.p_month_above(year, idx, lvl)
                if p_above is None:
                    continue
                pct = p_above if thr.group(1).lower() == "above" else 100.0 - p_above
            if pct is None:
                continue
            label = title.rstrip("?")
            sub = (m.get("yes_sub_title") or "").strip()
            if sub and sub.lower() not in label.lower():
                label = f"{label} — {sub}"
            row = _row("climate", "temperature", label, pct, ask,
                       {"team": "NASA GISS", "ticker": m.get("ticker"),
                        "volume": vol, "in_season": True,
                        "confidence": "high"},
                       days)
            if row:
                out.append(row)
                no = _no_row(row, kalshi._cents(m.get("no_ask_dollars")))
                if no:
                    out.append(no)
    return out


def _sources():
    """(name, callable) for every model feeding the board, cheapest first.

    Order matters because the board publishes incrementally: MLB, crypto and
    climate are seconds apart, so the page fills within about fifteen seconds
    instead of staying blank until the slowest league finishes."""
    by_ticker, by_series, by_no = _close_days()

    def sport_src(sp):
        def run():
            if sp == "mlb":
                return _collect_mlb(by_ticker, by_series, by_no)
            if sp == "nfl":
                import nfl_season
                return _collect_board("nfl", nfl_season.futures_board(), by_ticker, by_series, by_no)
            if sp == "cfb":
                import cfb
                return _collect_board("cfb", cfb.futures_board(), by_ticker, by_series, by_no)
            return _collect_pro(sp, by_ticker, by_series, by_no)
        return run
    out = [("climate", _collect_climate), ("crypto", _collect_crypto)]
    out += [(sp, sport_src(sp)) for sp in ("mlb", "cfb", "nfl", "nba", "nhl", "wnba")]
    return out


def _build():
    by_ticker, by_series, by_no = _close_days()
    out = []
    for sport in _SPORTS:
        try:
            if sport == "mlb":
                out += _collect_mlb(by_ticker, by_series, by_no)
            elif sport == "nfl":
                import nfl_season
                out += _collect_board("nfl", nfl_season.futures_board(), by_ticker, by_series, by_no)
            elif sport == "cfb":
                import cfb
                out += _collect_board("cfb", cfb.futures_board(), by_ticker, by_series, by_no)
            else:
                out += _collect_pro(sport, by_ticker, by_series, by_no)
        except Exception:
            continue        # a sport out of season shouldn't sink the board
    for other in _OTHER:
        try:
            if other == "crypto":
                out += _collect_crypto()
            elif other == "climate":
                out += _collect_climate()
        except Exception:
            continue
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
            # Publish as we go. A cold build runs several season simulations and
            # takes minutes end to end; holding every row back until the slowest
            # one finishes is what made the board look stuck behind a wall of
            # 202s. Each source that lands is merged in and served immediately.
            try:
                acc, done = [], []
                for name, fn in _sources():
                    try:
                        got = fn()
                    except Exception:
                        got = []
                    acc += got
                    done.append(name)
                    if acc:
                        _cache[key] = (_t.time(), list(acc))
                        _progress[key] = list(done)
                _cache[key] = (_t.time(), acc)
                _progress.pop(key, None)
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
          max_days=None, limit=60, include_suspect=False, positive_only=True,
          in_season_only=False):
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
    if in_season_only:
        # Hides leagues that haven't kicked off yet -- the point being that a
        # football win total is a six-month hold whether or not you want one.
        rs = [r for r in rs if r.get("in_season", True)]
    if not include_suspect:
        rs = [r for r in rs if not r["suspect"]]
    if positive_only:
        rs = [r for r in rs if r["ev_pct"] > 0]
    rs = sorted(rs, key=_SORTS.get(sort) or _SORTS["best"])
    building = ("mfut",) in _inflight
    return {
        "rows": rs[:max(1, min(400, limit))],
        "total": len(rs),
        "universe": universe,
        "partial": building,                 # more sources still landing
        "loaded": _progress.get(("mfut",)) or [],
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
