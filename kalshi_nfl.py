"""Live Kalshi NFL game-market prices for the weekly slate (kalshi_mlb's twin).

Kalshi lists NFL moneylines under KXNFLGAME with the same suffix convention as
MLB (KXNFLGAME-26AUG15DALSEA-SEA). The spread and total series NOW EXIST and are
parsed here -- this file used to carry a probe stub for them ("schema unknown
until they exist") that never did anything, so every spread and total leg the
engine built went unpriced:

    KXNFLSPREAD-26AUG06CARARI-ARI10   "Arizona wins by over 9.5 points?"
    KXNFLTOTAL-26AUG06CARARI-34       "Will there be over 33.5 points scored?"

Both follow the MLB convention: the trailing integer N means "N-0.5", i.e. the
spread market is "wins by N+" and the total is "N+ points". Kalshi books a deep
ladder per game (24 spread markets and 19 totals on the Hall of Fame game), which
is what lets a combo walk to the line that lands in a confidence band.

PLAYER PROPS EXIST IN THE PRESEASON, which is the opposite of what you would
guess and is the single most useful thing on this board:

    KXNFLPASSYDS-26AUG06CARARI-ARICBECKQB-150   "Carson Beck: 150+ passing yards"
    KXNFLRSHYDS-26AUG06CARARI-ARIJLOVE4-25      "Jeremiyah Love: 25+ rushing yards"
    KXNFLTD-26AUG06CARARI-CARTETIENNE23-1       "Trevor Etienne: 1+ touchdowns"

Note WHO those are: a rookie quarterback, a rookie running back, and a
fourth-string back. Kalshi books the players who will actually be on the field in
an exhibition, not the stars -- which is the preseason usage inversion showing up
in the market's own choice of what to list, and it means the market's player list
is a better "who is playing" signal than any depth chart.

The player leg is parsed off the TITLE and `floor_strike` rather than the ticker.
The middle segment is an opaque code (CARKPICKETT12, ARICBECKQB) with no stable
shape to match on, while "Carson Beck: 150+ passing yards" and floor_strike=149.5
are exactly the name and the line.

Games are matched by the PAIR of team abbreviations in the event suffix, so no
date math is needed. All lookups degrade gracefully to None.
"""
import re
import time
import unicodedata

import kalshi

_ML_SERIES = "KXNFLGAME"
_SPREAD_SERIES = "KXNFLSPREAD"
_TOTAL_SERIES = "KXNFLTOTAL"

# Kalshi player-prop series -> the stat key the game engine simulates.
_PROP_SERIES = {"KXNFLPASSYDS": "pass_yd", "KXNFLRSHYDS": "rush_yd",
                "KXNFLRECYDS": "rec_yd", "KXNFLREC": "rec", "KXNFLTD": "td"}
PROP_LABEL = {"pass_yd": "pass yds", "rush_yd": "rush yds",
              "rec_yd": "rec yds", "rec": "receptions", "td": "TD"}

_TTL = 120
_cache = {"ts": 0.0, "data": None}

# Kalshi <-> app abbreviation canon.
_CANON = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR"}


def _canon(ab):
    return _CANON.get((ab or "").upper(), (ab or "").upper())


def _norm(name):
    """Player match key -- same normalization nfl_adp uses, so a Kalshi title and
    a Sleeper roster name land on the same string."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", s.lower())
    s = re.sub(r"[^a-z ]", " ", s)
    return " ".join(s.split())


def _fetch(series):
    out, cursor = [], None
    for _ in range(4):
        url = f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            d = kalshi._get_json(url)
        except Exception:
            break
        out.extend(d.get("markets") or [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def _quote(c):
    """A quote at or beyond the bounds is Kalshi's "no offer" sentinel, not a
    price -- a contract costing 100c to win 100c is not a bet."""
    return c if (c is not None and 0 < c < 100) else None


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _q(m, side):
    """Full quote for one side: ask, bid, mid, spread and depth -- the shape
    combo_engine.tradeable() and blend_prob() expect.

    Without this the NFL board had no depth feed at all, so every leg came back
    untradeable and was charged at FAIR value: a whole slate of live Kalshi asks
    reported priced_frac 0.0 and EV 0, and the objective had nothing to rank on.

    Kalshi does not publish `no_ask_size_fp` on any market in either sport, so
    the NO side's depth is read off the YES BID -- buying NO at the ask lifts the
    same resting orders as selling YES at the bid, so it is the same size, not a
    missing one."""
    ask = kalshi._cents(m.get(f"{side}_ask_dollars"))
    bid = kalshi._cents(m.get(f"{side}_bid_dollars"))
    if ask is None or not (0 < ask < 100):
        return None
    if bid is None or not (0 <= bid <= 100) or bid > ask:
        bid = None
    size = m.get(f"{side}_ask_size_fp")
    if size is None and side == "no":
        size = m.get("yes_bid_size_fp")
    return {"ask": ask, "bid": bid,
            "mid": ((bid + ask) / 2.0) if bid is not None else ask,
            "spread": (ask - bid) if bid is not None else None,
            "size": _f(size), "vol": _f(m.get("volume_fp")),
            "oi": _f(m.get("open_interest_fp"))}


_SPREAD_RE = re.compile(r"^KXNFLSPREAD-([0-9A-Z]+)-([A-Z]+?)(\d+)$")
_TOTAL_RE = re.compile(r"^KXNFLTOTAL-([0-9A-Z]+)-(\d+)$")


def _build():
    """{suffix: {ml, spread, total, ticker, close, no}} for every open NFL game.

    Keyed by the event suffix (26AUG06CARARI) rather than by team pair, because
    the spread and total series carry no team pair of their own -- and a suffix
    is what a leg's kref is resolved against anyway. `pair` keeps the old
    frozenset lookup working for game_prices."""
    idx = {}

    def ent(suffix):
        # "q" holds the FULL quote for every leg key, keyed exactly as _qkey
        # resolves them. The flat ml/spread/total maps stay as they are because
        # the board UI and game_prices read cents off them directly.
        return idx.setdefault(suffix, {"ml": {}, "spread": {}, "total": {},
                                       "ticker": {}, "close": None, "q": {},
                                       "players": {}, "names": {},
                                       "no": {"ml": {}, "spread": {},
                                              "players": {}}})

    for m in _fetch(_ML_SERIES):
        tk = m.get("ticker") or ""
        suffix = (m.get("event_ticker") or "").split("-", 1)[-1]
        if not suffix:
            continue
        team = _canon(tk.rsplit("-", 1)[-1])
        e = ent(suffix)
        e["ml"][team] = _quote(kalshi._cents(m.get("yes_ask_dollars")))
        e["no"]["ml"][team] = _quote(kalshi._cents(m.get("no_ask_dollars")))
        e["q"][("ml", team, False)] = _q(m, "yes")
        e["q"][("ml", team, True)] = _q(m, "no")
        e["ticker"][team] = tk
        e["close"] = kalshi._parse_time(m.get("close_time")) or e["close"]

    for m in _fetch(_SPREAD_SERIES):
        mt = _SPREAD_RE.match(m.get("ticker") or "")
        if not mt:
            continue
        suffix, team, by = mt.group(1), _canon(mt.group(2)), int(mt.group(3))
        e = ent(suffix)
        e["spread"][(team, by)] = _quote(kalshi._cents(m.get("yes_ask_dollars")))
        e["no"]["spread"][(team, by)] = _quote(kalshi._cents(m.get("no_ask_dollars")))
        e["q"][("spread", team, by, False)] = _q(m, "yes")
        e["q"][("spread", team, by, True)] = _q(m, "no")

    for m in _fetch(_TOTAL_SERIES):
        mt = _TOTAL_RE.match(m.get("ticker") or "")
        if not mt:
            continue
        suffix, n = mt.group(1), int(mt.group(2))
        e = ent(suffix)
        # One market, two sides: YES is the over, NO is the under.
        e["total"][n] = {"over": _quote(kalshi._cents(m.get("yes_ask_dollars"))),
                         "under": _quote(kalshi._cents(m.get("no_ask_dollars")))}
        e["q"][("total", n, True)] = _q(m, "yes")
        e["q"][("total", n, False)] = _q(m, "no")

    for series, stat in _PROP_SERIES.items():
        for m in _fetch(series):
            suffix = (m.get("event_ticker") or "").split("-", 1)[-1]
            title = m.get("title") or ""
            if not suffix or ":" not in title:
                continue
            name = title.split(":", 1)[0].strip()
            line = m.get("floor_strike")
            if not name or line is None:
                continue
            e = ent(suffix)
            key = (stat, _norm(name), float(line))
            e["players"][key] = _quote(kalshi._cents(m.get("yes_ask_dollars")))
            e["no"]["players"][key] = _quote(kalshi._cents(m.get("no_ask_dollars")))
            e["q"][("prop",) + key + (False,)] = _q(m, "yes")
            e["q"][("prop",) + key + (True,)] = _q(m, "no")
            e["names"][_norm(name)] = name       # display name, as Kalshi writes it

    for suffix, e in idx.items():
        if len(e["ml"]) >= 2:
            e["pair"] = frozenset(e["ml"].keys())
    return {k: v for k, v in idx.items() if v.get("pair")}


def price_leg(idx, suffix, kref):
    """Live Kalshi ask (cents) for one NFL leg, or None. Mirrors kalshi_mlb so a
    candidate built by nfl_game_sim prices through the same combo engine."""
    if not suffix or not kref:
        return None
    g = idx.get(suffix)
    if not g:
        return None
    t, no = kref.get("t"), bool(kref.get("no"))
    src = g.get("no") if no else g
    if t == "ml":
        return _quote((src.get("ml") or {}).get(kref.get("team")))
    if t == "spread":
        return _quote((src.get("spread") or {}).get(
            (_canon(kref.get("team")), kref.get("by"))))
    if t == "total":
        tot = (g.get("total") or {}).get(kref.get("n"))
        over = bool(kref.get("over")) != no
        return _quote(tot.get("over" if over else "under")) if tot else None
    if t == "prop":
        return _quote((src.get("players") or {}).get(
            (kref.get("stat"), _norm(kref.get("player")), kref.get("line"))))
    return None


def _qkey(kref):
    """The `q` index key for a leg, or None if the leg has no structured key."""
    if not kref:
        return None
    t, no = kref.get("t"), bool(kref.get("no"))
    if t == "ml":
        return ("ml", _canon(kref.get("team")), no)
    if t == "spread":
        return ("spread", _canon(kref.get("team")), kref.get("by"), no)
    if t == "total":
        # Over and Under are the two sides of one market, so a NO on Over is an
        # Under and resolves to that side's own quote.
        return ("total", kref.get("n"), bool(kref.get("over")) != no)
    if t == "prop":
        return ("prop", kref.get("stat"), _norm(kref.get("player")),
                kref.get("line"), no)
    return None


def quote_leg(idx, suffix, kref):
    """Full quote dict for one leg (ask/bid/mid/spread/size/vol/oi), or None.

    price_leg answers "what does this cost"; this answers "what does the market
    think, and how much is that opinion worth" -- which is what the combo engine
    needs to tell a real edge from a quote nobody is trading."""
    if not suffix or not kref:
        return None
    g = idx.get(suffix)
    key = _qkey(kref)
    if not g or not key:
        return None
    return (g.get("q") or {}).get(key)


def ladders(suffix):
    """{'spread': {team: [by, ...]}, 'total': [n, ...]} actually booked for a
    game, so the engine offers the lines Kalshi trades rather than inventing
    them."""
    g = index().get(suffix) or {}
    sp = {}
    for (team, by) in (g.get("spread") or {}):
        sp.setdefault(team, []).append(by)
    for v in sp.values():
        v.sort()
    return {"spread": sp, "total": sorted(g.get("total") or {}),
            "props": prop_ladders(suffix)}


# A listed market is not a quoted one. Kalshi opens a prop the moment the game is
# announced, and until someone trades it the book sits at 5c/92c with no volume:
#
#     Haynes King: 100+ passing yards   bid 0.05  ask 0.92  vol 0  oi 0
#     Haynes King:  50+ passing yards   bid 0.05  ask 0.92  vol 0  oi 0
#
# Both rungs de-vig to 0.49, so an empty book claims Carolina's third-string
# quarterback is a coin flip to throw for a hundred yards AND the same coin flip
# at fifty -- a shape no distribution can have. That number is worse than no
# number, because prop_ladders feeds the MODEL and not just the pricing, so these
# are dropped here rather than downstream.
_MAX_PROP_SPREAD = 20.0    # cents between bid and ask before a book is an opinion


def prop_ladders(suffix):
    """{(stat, norm_name): {'name', 'rungs': [(line, p_over), ...]}} for a game.

    Rungs are de-vigged off the two MIDS, not the two asks: an ask carries half
    the spread in it, and both sides carrying it is what makes a pair of asks sum
    past 100c. Mids on a tight book already sum to about 1.00, which is the check
    that the de-vig is doing arithmetic rather than repair work."""
    g = index().get(suffix) or {}
    names, q = g.get("names") or {}, g.get("q") or {}
    out = {}
    for (stat, nm, line) in (g.get("players") or {}):
        yq = q.get(("prop", stat, nm, line, False))
        nq = q.get(("prop", stat, nm, line, True))
        if not yq or not nq:
            continue
        if (yq.get("spread") is None or yq["spread"] > _MAX_PROP_SPREAD
                or ((yq.get("vol") or 0) + (yq.get("oi") or 0)) <= 0):
            continue
        tot = yq["mid"] + nq["mid"]
        if tot <= 0:
            continue
        e = out.setdefault((stat, nm), {"name": names.get(nm, nm), "rungs": []})
        e["rungs"].append((float(line), yq["mid"] / tot))
    for e in out.values():
        e["rungs"].sort()
    return {k: v for k, v in out.items() if v["rungs"]}


def index():
    now = time.time()
    if _cache["data"] is None or now - _cache["ts"] > _TTL:
        try:
            _cache["data"] = _build()
            _cache["ts"] = now
        except Exception:
            if _cache["data"] is None:
                _cache["data"] = {}
    return _cache["data"]


def game_prices(home_ab, away_ab):
    """{'home_cents', 'away_cents', 'home_ticker', 'away_ticker', 'close'} for a
    matchup, or None when Kalshi hasn't listed it."""
    h, a = _canon(home_ab), _canon(away_ab)
    want = frozenset({h, a})
    e = next((v for v in index().values() if v.get("pair") == want), None)
    if not e:
        return None
    return {"home_cents": e["ml"].get(h), "away_cents": e["ml"].get(a),
            "home_ticker": e["ticker"].get(h), "away_ticker": e["ticker"].get(a),
            "close": e["close"], "suffix": next(
                (k for k, v in index().items() if v is e), None)}


def implied(suffix):
    """The market's own expected total and margin for a game, de-vigged.

    A preseason exhibition has no usable team profile -- Sleeper projects nothing
    in August -- but Kalshi books a deep ladder on it, and the ladder IS an
    estimate of the score. Read the median off it and the drive engine can be
    anchored to the market's level while still supplying the JOINT structure a
    parlay needs, which no single market price carries.

    Returns {'total', 'margin', 'home', 'away'} or None. `margin` is signed
    toward the first team of the pair as listed in the ticker (away+home)."""
    g = index().get(suffix)
    if not g:
        return None

    def devig(yes, no):
        if yes is None or no is None:
            return None if yes is None else yes / 100.0
        t = yes + no
        return (yes / t) if t > 0 else None

    # Total: the ladder is P(over N-0.5) falling as N rises. Interpolate to .50.
    pts = []
    for n, side in sorted((g.get("total") or {}).items()):
        p_over = devig(side.get("over"), side.get("under"))
        if p_over is not None:
            pts.append((n - 0.5, p_over))
    total = None
    for (l1, p1), (l2, p2) in zip(pts, pts[1:]):
        if p1 >= 0.5 >= p2 and p1 != p2:
            total = l1 + (p1 - 0.5) * (l2 - l1) / (p1 - p2)
            break
    if total is None and pts:
        total = min(pts, key=lambda x: abs(x[1] - 0.5))[0]

    # Margin: same read on each team's "wins by N+" ladder, taking whichever
    # side is favoured so the interpolation runs through the middle of its curve.
    by_team = {}
    for (team, n), c in (g.get("spread") or {}).items():
        by_team.setdefault(team, []).append((n - 0.5, c))
    margin, who = None, None
    for team, rows in by_team.items():
        rows.sort()
        no_rows = {n: c for (t, n), c in (g.get("no", {}).get("spread") or {}).items()
                   if t == team}
        cur = []
        for line, yes in rows:
            p = devig(yes, no_rows.get(int(line + 0.5)))
            if p is not None:
                cur.append((line, p))
        for (l1, p1), (l2, p2) in zip(cur, cur[1:]):
            if p1 >= 0.5 >= p2 and p1 != p2:
                m = l1 + (p1 - 0.5) * (l2 - l1) / (p1 - p2)
                if margin is None or m > margin:
                    margin, who = m, team
                break
    if margin is None:
        # No crossing: fall back to the moneyline favourite at a token margin.
        ml = {k: v for k, v in (g.get("ml") or {}).items() if v}
        if len(ml) == 2:
            who = min(ml, key=ml.get)
            margin = 1.0
    if total is None:
        return None
    return {"total": round(total, 2),
            "margin": round(margin or 0.0, 2), "favourite": who}
