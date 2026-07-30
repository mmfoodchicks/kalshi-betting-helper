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
_PLAYER_SERIES = ("KXMLBKS", "KXMLBHIT", "KXMLBTB", "KXMLBHR", "KXMLBHRR")
_STAT_OF = {"KXMLBKS": "ks", "KXMLBHIT": "hit", "KXMLBTB": "tb",
            "KXMLBHR": "hr", "KXMLBHRR": "hrr"}

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
        return idx.setdefault(suf, {"ml": {}, "spread": {}, "total": {},
                                    "rfi": None, "players": {},
                                    "no": {"ml": {}, "spread": {}, "rfi": None,
                                           "players": {}}})

    for series in _GAME_SERIES + _PLAYER_SERIES:
        for m in _fetch(series):
            suf = _suffix(m.get("event_ticker"))
            tk = m.get("ticker") or ""
            if not suf:
                continue
            g = game(suf)
            if series == "KXMLBGAME":
                team = tk.rsplit("-", 1)[-1]
                g["ml"][team] = _yes(m)
                g["no"]["ml"][team] = _no(m)
            elif series == "KXMLBSPREAD":
                tail = tk.rsplit("-", 1)[-1]              # e.g. "STL3"
                mt = _SPREAD_TEAM.match(tail)
                if mt:
                    key = (mt.group(1), int(mt.group(2)))
                    g["spread"][key] = _yes(m)
                    g["no"]["spread"][key] = _no(m)
            elif series == "KXMLBTOTAL":
                try:
                    n = int(tk.rsplit("-", 1)[-1])        # Over (n-0.5)
                except ValueError:
                    continue
                g["total"][n] = {"over": _yes(m), "under": _no(m)}
            elif series == "KXMLBRFI":
                g["rfi"] = _yes(m)
                g["no"]["rfi"] = _no(m)
            else:                                          # player props
                stat = _STAT_OF[series]
                sub = m.get("yes_sub_title") or m.get("title") or ""
                lm = _SUB_LINE.search(sub)
                name = sub.split(":", 1)[0]
                if lm and name:
                    key = (stat, _norm(name), int(lm.group(1)))
                    g["players"][key] = _yes(m)
                    g["no"]["players"][key] = _no(m)
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
    if t in ("ks", "hit", "tb", "hr", "hrr"):
        return _quote((src.get("players") or {}).get(
            (t, _norm(kref.get("player")), kref.get("line"))))
    return None
