"""Live Kalshi college-football game-market prices (kalshi_nfl's twin).

Kalshi books FBS games exactly the way it books the NFL, under its own series:

    KXNCAAFGAME-26SEP05UNTIND-IND       "Indiana wins"
    KXNCAAFSPREAD-26SEP05UNTIND-IND18   "Indiana wins by over 17.5 points"
    KXNCAAFTOTAL-26SEP05CCARWVU-34      "Over 33.5 points scored"

Same suffix convention (date + two team codes), same "N means N-0.5" ladder
rule, same one-market-two-sides shape for the total. One thing is NOT the
same: the team codes are Kalshi's own and only sometimes agree with ESPN's
(Indiana is IND here and IU there; Coastal Carolina is CCAR here and CCU
there), so a game is matched to its Kalshi event by DATE and TEAM NAME --
the moneyline's yes_sub_title is the team's name as Kalshi writes it -- and
the codes ride on the board's game as `kx_home` / `kx_away` so every leg's
kref prices straight off this index. The index is shared across workers
through boardshare with the same last-good fallback as kalshi_nfl.index.
"""
import re
import time

import kalshi
import errlog
import kalshi_nfl as _k

_ML_SERIES = "KXNCAAFGAME"
_SPREAD_SERIES = "KXNCAAFSPREAD"
_TOTAL_SERIES = "KXNCAAFTOTAL"
_SPREAD_RE = re.compile(r"^KXNCAAFSPREAD-([0-9A-Z]+)-([A-Z]+?)(\d+)$")
_TOTAL_RE = re.compile(r"^KXNCAAFTOTAL-([0-9A-Z]+)-(\d+)$")
_DATE_RE = re.compile(r"^(\d{2}[A-Z]{3}\d{2})")

_TTL = 120
_IDX_STALE_MAX = 45 * 60
_cache = {"ts": 0.0, "data": None}
_BOARD_KEY = "kalshi_cfb_idx"


def _build():
    """{suffix: {ml, spread, total, ticker, close, q, tick, names, no, pair}}
    for every open college game. `names` maps a Kalshi team code to the name
    the moneyline carries, which is what match_game resolves ESPN teams
    against."""
    idx = {}

    def ent(suffix):
        return idx.setdefault(suffix, {"ml": {}, "spread": {}, "total": {},
                                       "ticker": {}, "close": None, "q": {},
                                       "tick": {}, "names": {},
                                       "no": {"ml": {}, "spread": {}}})

    for m in _k._fetch(_ML_SERIES):
        tk = m.get("ticker") or ""
        suffix = (m.get("event_ticker") or "").split("-", 1)[-1]
        if not suffix:
            continue
        team = tk.rsplit("-", 1)[-1]
        e = ent(suffix)
        e["ml"][team] = _k._quote(kalshi._cents(m.get("yes_ask_dollars")))
        e["no"]["ml"][team] = _k._quote(kalshi._cents(m.get("no_ask_dollars")))
        e["q"][("ml", team, False)] = _k._q(m, "yes")
        e["q"][("ml", team, True)] = _k._q(m, "no")
        e["ticker"][team] = tk
        close = kalshi._parse_time(m.get("close_time"))
        e["close"] = close or e["close"]
        e["tick"][("ml", team)] = (tk, close)
        e["names"][team] = (m.get("yes_sub_title") or "").strip()

    for m in _k._fetch(_SPREAD_SERIES):
        mt = _SPREAD_RE.match(m.get("ticker") or "")
        if not mt:
            continue
        suffix, team, by = mt.group(1), mt.group(2), int(mt.group(3))
        e = ent(suffix)
        e["spread"][(team, by)] = _k._quote(kalshi._cents(m.get("yes_ask_dollars")))
        e["no"]["spread"][(team, by)] = _k._quote(kalshi._cents(m.get("no_ask_dollars")))
        e["q"][("spread", team, by, False)] = _k._q(m, "yes")
        e["q"][("spread", team, by, True)] = _k._q(m, "no")
        e["tick"][("spread", team, by)] = (m.get("ticker"),
                                           kalshi._parse_time(m.get("close_time")))

    for m in _k._fetch(_TOTAL_SERIES):
        mt = _TOTAL_RE.match(m.get("ticker") or "")
        if not mt:
            continue
        suffix, n = mt.group(1), int(mt.group(2))
        e = ent(suffix)
        e["total"][n] = {"over": _k._quote(kalshi._cents(m.get("yes_ask_dollars"))),
                         "under": _k._quote(kalshi._cents(m.get("no_ask_dollars")))}
        e["q"][("total", n, True)] = _k._q(m, "yes")
        e["q"][("total", n, False)] = _k._q(m, "no")
        e["tick"][("total", n)] = (m.get("ticker"),
                                   kalshi._parse_time(m.get("close_time")))

    for suffix, e in idx.items():
        if len(e["ml"]) >= 2:
            e["pair"] = frozenset(e["ml"].keys())
    return {k: v for k, v in idx.items() if v.get("pair")}


def index():
    """The Kalshi college index, shared across workers with a last-good
    fallback -- kalshi_nfl.index's rules, its own key."""
    import boardshare
    now = time.time()
    if _cache["data"] and now - _cache["ts"] <= _TTL:
        return _cache["data"]
    disk, age = boardshare.get(_BOARD_KEY, _TTL)
    if disk:
        _cache["data"], _cache["ts"] = disk, now - age
        return disk
    try:
        built = _build()
    except Exception as _e:
        errlog.note("KCFB-index-build", _e)
        built = None
    if built:
        _cache["data"], _cache["ts"] = built, now
        boardshare.put(_BOARD_KEY, built)
    elif not _cache["data"]:
        stale, _sa = boardshare.get(_BOARD_KEY, _IDX_STALE_MAX)
        _cache["data"], _cache["ts"] = (stale or {}), now
        if not _cache["data"]:
            errlog.note("KCFB-index-empty",
                        msg="index build failed/empty with no usable fallback")
    else:
        _cache["ts"] = now
    return _cache["data"]


# ---- Matching an ESPN game to its Kalshi event --------------------------------
_ABBREV = {"st": "state", "st.": "state", "univ": "university", "&": "and"}
# ESPN locations Kalshi spells differently, in normalized form. Measured on
# the 2026 week-1 card: 52 of 55 pregame games matched on names alone; these
# three did not.
# Kalshi's short forms that no rule recovers: "SE Louisiana" is SoutheastERN
# but "SE Missouri St." is Southeast, so a prefix rule cannot serve both.
_ALIASES = {"app state": "appalachian state", "ul monroe": "louisiana monroe",
            "se louisiana": "southeastern louisiana", "usa": "south alabama",
            "se missouri state": "southeast missouri state"}


def _nm(s):
    """Name normalization for matching: lowercase, punctuation out, 'St.' to
    'state', so 'Iowa St.' (Kalshi) meets 'Iowa State' (ESPN)."""
    s = _k._norm(s)
    s = " ".join(_ABBREV.get(w, w) for w in s.split())
    return _ALIASES.get(s, s)


def _score(kname, meta):
    """How well a Kalshi team name fits an ESPN team record: 3 exact on the
    location or display name, 2 exact on the nickname-less form or a
    location prefix, 1 a containment either way, 0 nothing."""
    k = _nm(kname)
    if not k:
        return 0
    loc = _nm(meta.get("location") or "")
    name = _nm(meta.get("name") or "")
    nick = _nm(meta.get("nick") or "")
    if k in (loc, name):
        return 3
    if nick and name and k == name.replace(nick, "").strip():
        return 2
    if loc and (k.startswith(loc + " ") or loc.startswith(k + " ")):
        return 2
    if loc and len(loc) > 3 and (loc in k or k in loc):
        return 1
    return 0


def suffix_date(suffix):
    m = _DATE_RE.match(suffix or "")
    return m.group(1) if m else None


def date_key(d):
    """A datetime.date as Kalshi writes it in a suffix: 26SEP05."""
    return d.strftime("%y%b%d").upper()


def match_game(idx, dates, home_meta, away_meta):
    """(suffix, home_code, away_code) for an ESPN game, or None.

    `dates` is the set of Kalshi date keys the game may sit under (its ET
    date, and the day either side -- a late kickoff crosses the calendar).
    Both teams must match DISTINCT codes inside one event; the best total
    score wins, exact names first."""
    best, best_score = None, 0
    for suffix, e in (idx or {}).items():
        if suffix_date(suffix) not in dates:
            continue
        names = e.get("names") or {}
        if len(names) < 2:
            continue
        hs = [(c, _score(nm, home_meta)) for c, nm in names.items()]
        as_ = [(c, _score(nm, away_meta)) for c, nm in names.items()]
        for hc, hsc in hs:
            if hsc <= 0:
                continue
            for ac, asc in as_:
                if asc <= 0 or ac == hc:
                    continue
                sc = hsc + asc
                if sc > best_score:
                    best, best_score = (suffix, hc, ac), sc
    # Both sides must be more than a loose containment: 3 is exact + nothing
    # else, and a single containment on each side is how two Miamis collide.
    return best if best_score >= 3 else None


def game_prices(idx, suffix, home_code, away_code):
    """{'home_cents', 'away_cents', 'home_ticker', 'away_ticker', 'close'} off
    a matched event, or None."""
    e = (idx or {}).get(suffix)
    if not e:
        return None
    return {"home_cents": e["ml"].get(home_code), "away_cents": e["ml"].get(away_code),
            "home_ticker": e["ticker"].get(home_code),
            "away_ticker": e["ticker"].get(away_code), "close": e["close"],
            "suffix": suffix}


# ---- Leg pricing: kalshi_nfl's contract, Kalshi's own codes ------------------
def _qkey(kref):
    if not kref:
        return None
    t, no = kref.get("t"), bool(kref.get("no"))
    if t == "ml":
        return ("ml", kref.get("team"), no)
    if t == "spread":
        return ("spread", kref.get("team"), kref.get("by"), no)
    if t == "total":
        return ("total", kref.get("n"), bool(kref.get("over")) != no)
    return None


def price_leg(idx, suffix, kref):
    """Live Kalshi ask (cents) for one college leg, or None. The kref's team
    is the Kalshi code the board stamped, so no canon step exists here."""
    if not suffix or not kref:
        return None
    g = (idx or {}).get(suffix)
    if not g:
        return None
    t, no = kref.get("t"), bool(kref.get("no"))
    src = g.get("no") if no else g
    if t == "ml":
        return _k._quote((src.get("ml") or {}).get(kref.get("team")))
    if t == "spread":
        return _k._quote((src.get("spread") or {}).get((kref.get("team"), kref.get("by"))))
    if t == "total":
        tot = (g.get("total") or {}).get(kref.get("n"))
        over = bool(kref.get("over")) != no
        return _k._quote(tot.get("over" if over else "under")) if tot else None
    return None


def quote_leg(idx, suffix, kref):
    if not suffix or not kref:
        return None
    g = (idx or {}).get(suffix)
    key = _qkey(kref)
    if not g or not key:
        return None
    return (g.get("q") or {}).get(key)


def ticker_leg(idx, suffix, kref):
    """(ticker, close_time) for one leg's market; both sides share a ticket."""
    if not suffix or not kref:
        return None, None
    g = (idx or {}).get(suffix)
    key = _qkey(kref)
    if not g or not key:
        return None, None
    base = key[:-1]
    if kref.get("t") == "total":
        base = ("total", kref.get("n"))
    got = (g.get("tick") or {}).get(base)
    return got if got else (None, None)


def ladders(idx, suffix):
    """{'spread': {code: [by, ...]}, 'total': [n, ...]} actually booked for a
    game, so the engine offers the lines Kalshi trades."""
    g = (idx or {}).get(suffix) or {}
    sp = {}
    for (team, by) in (g.get("spread") or {}):
        sp.setdefault(team, []).append(by)
    for v in sp.values():
        v.sort()
    return {"spread": sp, "total": sorted(g.get("total") or {})}


def implied_margin(idx, suffix, home_code):
    """The market's own expected margin (home minus away) off the spread
    ladder, de-vigged and interpolated to the 50% line; None without a
    ladder. Shown beside the model's number on the card -- the pick'em
    question is 'who covers', and this is the line to cover."""
    g = (idx or {}).get(suffix) or {}
    q = g.get("q") or {}
    pts = []
    for (team, by) in (g.get("spread") or {}):
        y = (q.get(("spread", team, by, False)) or {}).get("mid")
        n = (q.get(("spread", team, by, True)) or {}).get("mid")
        if y is None:
            continue
        p = y / (y + n) if (n is not None and y + n > 0) else y / 100.0
        m = (by - 0.5) if team == home_code else -(by - 0.5)
        pts.append((m, p if team == home_code else 1.0 - p))
    if len(pts) < 2:
        return None
    # p(home margin > m) falls as m rises; find where it crosses 0.5.
    pts.sort()
    for (m0, p0), (m1, p1) in zip(pts, pts[1:]):
        if (p0 - 0.5) * (p1 - 0.5) <= 0 and p0 != p1:
            return round(m0 + (0.5 - p0) * (m1 - m0) / (p1 - p0), 1)
    return None
