"""Live Kalshi NFL game-market prices for the weekly slate (kalshi_mlb's twin).

Kalshi lists NFL moneylines under KXNFLGAME with the same suffix convention as
MLB (KXNFLGAME-26AUG15DALSEA-SEA). Spread/total series don't exist yet — the
probe list below checks the obvious sibling names each refresh, so if Kalshi
adds them mid-season (as they did for MLB) they start pricing automatically.

Games are matched by the PAIR of team abbreviations in the event suffix, so no
date math is needed. All lookups degrade gracefully to None.
"""
import time

import kalshi

_ML_SERIES = "KXNFLGAME"
_PROBE = ("KXNFLSPREAD", "KXNFLTOTAL")     # light up automatically if listed

_TTL = 120
_cache = {"ts": 0.0, "data": None}

# Kalshi <-> app abbreviation canon.
_CANON = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR"}


def _canon(ab):
    return _CANON.get((ab or "").upper(), (ab or "").upper())


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


def _build():
    """{frozenset({A, B}): {"ml": {abbr: yes_ask_cents}, "ticker": {abbr: ticker},
    "close": epoch}} for every open NFL moneyline pair."""
    idx = {}
    for m in _fetch(_ML_SERIES):
        tk = m.get("ticker") or ""
        team = _canon(tk.rsplit("-", 1)[-1])
        suffix = (m.get("event_ticker") or "").split("-", 1)[-1]
        # the suffix tail is AWAYHOME concatenated; recover the pair from the
        # event's two markets rather than parsing (they share event_ticker)
        ev = m.get("event_ticker")
        e = idx.setdefault(ev, {"ml": {}, "ticker": {}, "close": None, "suffix": suffix})
        e["ml"][team] = kalshi._cents(m.get("yes_ask_dollars"))
        e["ticker"][team] = tk
        e["close"] = kalshi._parse_time(m.get("close_time")) or e["close"]
    out = {}
    for e in idx.values():
        if len(e["ml"]) >= 2:
            out[frozenset(e["ml"].keys())] = e
    for s in _PROBE:                      # future spread/total series
        for m in _fetch(s):
            break                         # (schema unknown until they exist)
    return out


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
    e = index().get(frozenset({h, a}))
    if not e:
        return None
    return {"home_cents": e["ml"].get(h), "away_cents": e["ml"].get(a),
            "home_ticker": e["ticker"].get(h), "away_ticker": e["ticker"].get(a),
            "close": e["close"]}
