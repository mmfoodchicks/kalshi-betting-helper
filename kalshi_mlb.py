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
    rfi:c, players:{(stat,norm_name,line):c}}."""
    idx = {}

    def game(suf):
        return idx.setdefault(suf, {"ml": {}, "spread": {}, "total": {},
                                    "rfi": None, "players": {}})

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
            elif series == "KXMLBSPREAD":
                tail = tk.rsplit("-", 1)[-1]              # e.g. "STL3"
                mt = _SPREAD_TEAM.match(tail)
                if mt:
                    g["spread"][(mt.group(1), int(mt.group(2)))] = _yes(m)
            elif series == "KXMLBTOTAL":
                try:
                    n = int(tk.rsplit("-", 1)[-1])        # Over (n-0.5)
                except ValueError:
                    continue
                g["total"][n] = {"over": _yes(m), "under": _no(m)}
            elif series == "KXMLBRFI":
                g["rfi"] = _yes(m)
            else:                                          # player props
                stat = _STAT_OF[series]
                sub = m.get("yes_sub_title") or m.get("title") or ""
                lm = _SUB_LINE.search(sub)
                name = sub.split(":", 1)[0]
                if lm and name:
                    g["players"][(stat, _norm(name), int(lm.group(1)))] = _yes(m)
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


def price_leg(idx, suffix, kref):
    """Live Kalshi YES/NO ask (cents) for one leg, or None if not found/quoted.
    `kref` is the structured key the candidate carries (see mlb_sim)."""
    if not suffix or not kref:
        return None
    g = idx.get(suffix)
    if not g:
        return None
    t = kref.get("t")
    if t == "ml":
        return g["ml"].get(kref.get("team"))
    if t == "spread":
        return g["spread"].get((kref.get("team"), kref.get("by")))
    if t == "total":
        tot = g["total"].get(kref.get("n"))
        return tot.get("over" if kref.get("over") else "under") if tot else None
    if t == "rfi":
        return g["rfi"]
    if t in ("ks", "hit", "tb", "hr", "hrr"):
        return g["players"].get((t, _norm(kref.get("player")), kref.get("line")))
    return None
