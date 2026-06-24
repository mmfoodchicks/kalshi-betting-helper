"""Polymarket (Gamma API) as a second market source, so futures show both books
and we can edge our model against whichever is softer.

Polymarket structures a futures market as one event (e.g. "MLB World Series
Champion 2026") whose `markets` are one Yes/No contract per team, with
`groupItemTitle` = full team name ("New York Yankees") and `bestAsk` in dollars.
We match those to our model by team nickname (a clean substring of the full
name) and expose YES ask prices in cents to mirror the Kalshi side.
"""

import json
import time
import urllib.parse
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
_cache = {}


def _get(url, ttl=600, timeout=20):
    now = time.time()
    hit = _cache.get(url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last = None
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
            _cache[url] = (now, data)
            return data
        except Exception as e:
            last = e
    raise last


def _cents(dollar_str):
    try:
        c = round(float(dollar_str) * 100, 1)
        return c if 0 < c < 100 else None
    except (TypeError, ValueError):
        return None


def search_event_id(query):
    """First open Polymarket event id whose title best matches `query`."""
    url = f"{GAMMA}/public-search?" + urllib.parse.urlencode(
        {"q": query, "limit_per_type": 6})
    try:
        evs = _get(url, ttl=1800).get("events") or []
    except Exception:
        return None
    ql = query.lower()
    # Prefer a title containing the distinctive words of the query.
    for e in evs:
        if e.get("title", "").lower() == ql:
            return e.get("id")
    return evs[0].get("id") if evs else None


def event_prices(event_id):
    """[{team, yes_cents, bid_cents, volume}] for a per-team futures event."""
    try:
        ev = _get(f"{GAMMA}/events/{event_id}")
    except Exception:
        return []
    out = []
    for m in ev.get("markets") or []:
        name = m.get("groupItemTitle") or m.get("question") or ""
        ask = m.get("bestAsk")
        cents = _cents(ask) if ask is not None else None
        if cents is None:
            px = m.get("outcomePrices")
            try:
                cents = _cents(json.loads(px)[0]) if px else None
            except (ValueError, IndexError, TypeError):
                cents = None
        if name and cents is not None:
            out.append({"team": name, "yes_cents": cents,
                        "bid_cents": _cents(m.get("bestBid")),
                        "volume": m.get("volume")})
    return out


# Futures events we mirror against our MLB model. Searched by title (slugs/ids
# change season to season, so we resolve them dynamically + cache).
_MLB_EVENTS = {
    "world_series": "MLB World Series Champion 2026",
    "al": "MLB: 2026 American League Champion",
    "nl": "MLB: 2026 National League Champion",
}


def mlb_futures(ttl=600):
    """{'world_series': {full_team_name: yes_cents}, 'al': {...}, 'nl': {...}}."""
    ck = ("mlb_futures",)
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    out = {}
    for cat, query in _MLB_EVENTS.items():
        eid = search_event_id(query)
        if eid:
            out[cat] = {m["team"]: m["yes_cents"] for m in event_prices(eid)}
    _cache[ck] = (time.time(), out)
    return out


def match_team(nickname, price_map):
    """YES cents for our team (nickname) in a {full_name: cents} map, or None."""
    if not nickname or not price_map:
        return None
    nl = nickname.lower()
    for full, cents in price_map.items():
        if nl in full.lower():
            return cents
    return None
