"""Live Kalshi MLB market prices for combo legs.

The combo maker prices legs off our model. To show what Kalshi *actually* pays
(so the number matches their combo builder), we look up each leg's live YES/NO
price here and multiply.

Every Kalshi MLB market ticker shares a game-key suffix, e.g.
  KXMLBGAME-26JUN241945AZSTL-STL   (moneyline, STL to win)
  KXMLBTOTAL-26JUN241945AZSTL-9    (Over 8.5 runs)
  KXMLBKS-26JUN241945AZSTL-AZZGALLEN9-7  (Gallen 7+ Ks)
so once we know a game's suffix (from the moneyline match in baseball.py) we can
join every other market to it. Prices come from the *_dollars fields.

All lookups degrade gracefully: a leg we can't price returns None, and the combo
shows a partial/none Kalshi payout rather than breaking.
"""

import re
import time

import kalshi

_GAME_SERIES = ("KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL", "KXMLBRFI")
# KXMLBSB was missing here, which made "Stolen bases" a dead chip: the sim built
# 255 SB legs on a 15-game slate, none of them could be priced, and once unlisted
# legs stopped reaching slips every one of them was silently dropped. Kalshi does
# list the market, in the same "Name: 1+" shape as every other player prop.
_PLAYER_SERIES = ("KXMLBKS", "KXMLBHIT", "KXMLBTB", "KXMLBHR", "KXMLBHRR",
                  "KXMLBSB")
_STAT_OF = {"KXMLBKS": "ks", "KXMLBHIT": "hit", "KXMLBTB": "tb",
            "KXMLBHR": "hr", "KXMLBHRR": "hrr", "KXMLBSB": "sb"}

# The player-prop stat codes, derived from the series map so a new market
# cannot be indexed but left unresolvable (which is exactly how SB failed).
_PLAYER_STATS = frozenset(_STAT_OF.values())

_TTL = 60
_cache = {"ts": 0.0, "data": None}

_SUB_LINE = re.compile(r":\s*(\d+)\+")          # "Ketel Marte: 2+" -> 2
_SPREAD_TEAM = re.compile(r"^([A-Za-z]+?)(\d+)$")  # "STL3" -> ("STL", 3)


def _norm(name):
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _suffix(event_ticker):
    """Game-key shared across series, e.g. 'KXMLBTOTAL-26JUN241945AZSTL' ->
    '26JUN241945AZSTL'."""
    if not event_ticker or "-" not in event_ticker:
        return None
    return event_ticker.split("-", 1)[1]


def _fetch(series):
    out, cursor = [], None
    for _ in range(6):
        url = f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=400"
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


def _yes(m):
    return kalshi._cents(m.get("yes_ask_dollars"))


def _no(m):
    return kalshi._cents(m.get("no_ask_dollars"))


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _q(m, side):
    """Full quote for one side: ask, bid, mid, spread and depth.

    The ASK is what a leg costs; the MID is the market's opinion of fair value.
    Those are different numbers and conflating them was costing accuracy in both
    directions -- a leg quoted 6c/5c is a 5.5c market you pay 6c for, not a 6c
    market. `spread` and the depth fields say how much that opinion is worth: a
    penny-wide market with real volume is a strong estimator, while an untraded
    prop with a 20c spread is barely an opinion at all."""
    ask = kalshi._cents(m.get(f"{side}_ask_dollars"))
    bid = kalshi._cents(m.get(f"{side}_bid_dollars"))
    if ask is None or not (0 < ask < 100):
        return None
    if bid is None or not (0 <= bid <= 100) or bid > ask:
        bid = None
    # Kalshi does not publish `no_ask_size_fp` on ANY market -- checked across
    # every open MLB and NFL market, zero of them carry it -- so the NO side was
    # reading a missing field and reporting depth 0.0 on a book that is often
    # thick. Buying NO at the ask lifts the same resting orders as selling YES at
    # the bid, so the YES bid size IS the NO ask size.
    size = m.get(f"{side}_ask_size_fp")
    if size is None and side == "no":
        size = m.get("yes_bid_size_fp")
    return {"ask": ask, "bid": bid,
            "mid": ((bid + ask) / 2.0) if bid is not None else ask,
            "spread": (ask - bid) if bid is not None else None,
            "size": _f(size),
            "vol": _f(m.get("volume_fp")),
            "oi": _f(m.get("open_interest_fp"))}


def _build_index():
    """suffix -> {ml:{team:c}, spread:{(team,by):c}, total:{n:{over,under}},
    rfi:c, players:{(stat,norm_name,line):c}, no:{...}}.

    `no` mirrors the ml/spread/rfi/players maps with the NO ask, so a leg bet the
    other way is priced off Kalshi's real quote rather than 100-minus-YES — the
    two sides each carry the spread, so inverting the YES ask would understate
    what the NO side actually costs. (Totals already hold both sides: the NO of
    Over n IS Under n.)"""
    idx = {}

    def game(suf):
        # "q" holds the FULL quote (ask/bid/mid/spread/depth) for every leg key,
        # keyed exactly as price_leg resolves them plus the side. The flat cent
        # maps above stay as they are -- every existing caller keeps working and
        # keeps getting the ask, which is what it should pay.
        # "tick" maps a leg key to the Kalshi TICKER and close time. A market has
        # one ticker covering both sides, so the key carries no yes/no flag. The
        # prediction log needs it: a logged forecast is graded by looking its
        # ticker up after settlement, which is why only the moneyline was ever
        # gradable -- it was the only leg whose ticker survived indexing.
        return idx.setdefault(suf, {"ml": {}, "spread": {}, "total": {},
                                    "rfi": None, "players": {}, "q": {},
                                    "tick": {},
                                    "no": {"ml": {}, "spread": {}, "rfi": None,
                                           "players": {}}})

    for series in _GAME_SERIES + _PLAYER_SERIES:
        for m in _fetch(series):
            suf = _suffix(m.get("event_ticker"))
            tk = m.get("ticker") or ""
            if not suf:
                continue
            g = game(suf)

            def both(key):
                """Record the yes/no quotes for one leg key."""
                for side, tag in (("yes", False), ("no", True)):
                    q = _q(m, side)
                    if q:
                        g["q"][key + (tag,)] = q
                g["tick"][key] = (tk, kalshi._parse_time(m.get("close_time")))
            if series == "KXMLBGAME":
                team = tk.rsplit("-", 1)[-1]
                g["ml"][team] = _yes(m)
                g["no"]["ml"][team] = _no(m)
                both(("ml", team))
            elif series == "KXMLBSPREAD":
                tail = tk.rsplit("-", 1)[-1]              # e.g. "STL3"
                mt = _SPREAD_TEAM.match(tail)
                if mt:
                    key = (mt.group(1), int(mt.group(2)))
                    g["spread"][key] = _yes(m)
                    g["no"]["spread"][key] = _no(m)
                    both(("spread",) + key)
            elif series == "KXMLBTOTAL":
                try:
                    n = int(tk.rsplit("-", 1)[-1])        # Over (n-0.5)
                except ValueError:
                    continue
                g["total"][n] = {"over": _yes(m), "under": _no(m)}
                # A total's two sides ARE the two sides of one market, so Over
                # takes the yes quote and Under the no quote (not a mirrored one).
                for side, over in (("yes", True), ("no", False)):
                    q = _q(m, side)
                    if q:
                        g["q"][("total", n, over)] = q
                # Totals skip both() because their two sides are one market, so
                # the ticker has to be recorded here or it goes missing.
                g["tick"][("total", n)] = (tk, kalshi._parse_time(m.get("close_time")))
            elif series == "KXMLBRFI":
                g["rfi"] = _yes(m)
                g["no"]["rfi"] = _no(m)
                both(("rfi",))
            else:                                          # player props
                stat = _STAT_OF[series]
                sub = m.get("yes_sub_title") or m.get("title") or ""
                lm = _SUB_LINE.search(sub)
                name = sub.split(":", 1)[0]
                if lm and name:
                    key = (stat, _norm(name), int(lm.group(1)))
                    g["players"][key] = _yes(m)
                    g["no"]["players"][key] = _no(m)
                    both(key)
    return idx


def index():
    """Cached market index (refreshed every _TTL seconds)."""
    now = time.time()
    if _cache["data"] is None or now - _cache["ts"] > _TTL:
        try:
            _cache["data"] = _build_index()
            _cache["ts"] = now
        except Exception:
            if _cache["data"] is None:
                _cache["data"] = {}
    return _cache["data"]


def _quote(c):
    """A quote AT or beyond the bounds is Kalshi's "no offer" sentinel, not a
    price. A 100c ask means nobody is selling: a contract that costs 100c to win
    100c is not a bet, and letting it through prices a leg the board cannot
    actually buy. Seen live on the NO side of thin player props."""
    return c if (c is not None and 0 < c < 100) else None


def price_leg(idx, suffix, kref):
    """Live Kalshi ask (cents) for one leg, or None if not found/quoted. `kref` is
    the structured key the candidate carries (see mlb_sim); `kref["no"]` asks for
    the NO side's own ask rather than the YES side's.

    Every return goes through _quote, so an unbuyable 0/100 sentinel reaches
    callers as "unpriced" rather than as a real-looking number."""
    if not suffix or not kref:
        return None
    g = idx.get(suffix)
    if not g:
        return None
    t = kref.get("t")
    no = bool(kref.get("no"))
    src = (g.get("no") or {}) if no else g
    if t == "ml":
        return _quote((src.get("ml") or {}).get(kref.get("team")))
    if t == "spread":
        return _quote((src.get("spread") or {}).get((kref.get("team"), kref.get("by"))))
    if t == "total":
        # Both sides live in the same market: the NO of Over is Under, and vice versa.
        tot = g["total"].get(kref.get("n"))
        over = bool(kref.get("over")) != no
        return _quote(tot.get("over" if over else "under")) if tot else None
    if t == "rfi":
        return _quote(src.get("rfi"))
    if t in _PLAYER_STATS:
        return _quote((src.get("players") or {}).get(
            (t, _norm(kref.get("player")), kref.get("line"))))
    return None


def _qkey(kref):
    """The `q` index key for a leg, or None if the leg has no structured key."""
    if not kref:
        return None
    t, no = kref.get("t"), bool(kref.get("no"))
    if t == "ml":
        return ("ml", kref.get("team"), no)
    if t == "spread":
        return ("spread", kref.get("team"), kref.get("by"), no)
    if t == "total":
        # Over and Under are the two sides of one market, so a NO on Over is an
        # Under and resolves to that side's own quote.
        return ("total", kref.get("n"), bool(kref.get("over")) != no)
    if t == "rfi":
        return ("rfi", no)
    if t in _PLAYER_STATS:
        return (t, _norm(kref.get("player")), kref.get("line"), no)
    return None


def ticker_leg(idx, suffix, kref):
    """(kalshi ticker, close_time epoch) for one leg's market, or (None, None).

    Both sides of a market share one ticker, so the yes/no flag on `kref` is
    ignored. This is what lets a forecast be GRADED later: predlog stores the
    ticker and asks Kalshi for the settled result once the market closes."""
    if not suffix or not kref:
        return None, None
    g = idx.get(suffix)
    key = _qkey(kref)
    if not g or not key:
        return None, None
    base = key[:-1] if key and isinstance(key[-1], bool) else key
    if kref.get("t") == "total":
        base = ("total", kref.get("n"))
    got = (g.get("tick") or {}).get(base)
    return got if got else (None, None)


def quote_leg(idx, suffix, kref):
    """Full quote dict for one leg (ask/bid/mid/spread/size/vol/oi), or None.

    price_leg answers "what does this cost"; this answers "what does the market
    think, and how much is that opinion worth". The combo engine needs the second
    to tell a real edge from a stale quote nobody is trading."""
    if not suffix or not kref:
        return None
    g = idx.get(suffix)
    key = _qkey(kref)
    if not g or not key:
        return None
    return (g.get("q") or {}).get(key)
