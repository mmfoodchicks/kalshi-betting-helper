"""Kalshi price-matching for the F1/NASCAR futures board.

Kalshi lists racing markets with the driver's full name in the title, so we join
by name-substring (robust, no brittle ticker parsing):

  - KXF1         -> F1 Drivers' Championship (all season)
  - KXF1RACE     -> the UPCOMING grand prix's race winner
  - KXF1POLE     -> the upcoming grand prix's pole
  - KXNASCARRACE -> the upcoming race's winner (Cup + lower series mixed, so we
                    also gate on a token from the race name)

Only the upcoming race is tradeable, so per-race prices attach to our first
remaining race; the championship is priced across the board. Prices are fetched
fresh per request (lightly cached) while the model stays weekly-cached.
"""

import time

import kalshi

_cache = {}


def priced(series, ttl=300):
    """[(title_lower, yes_ask_cents)] for a series' open markets, cached briefly."""
    hit = _cache.get(series)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    out = []
    try:
        for m in kalshi.markets_for_series(series, limit=120):
            a = m.get("yes_ask")
            if a is not None:
                out.append(((m.get("title") or m.get("yes_sub_title") or "").lower(), float(a)))
    except Exception:
        out = []
    _cache[series] = (time.time(), out)
    return out


def match_name(name, rows, race_token=None):
    """YES cents for a driver in a priced series, matched by name in the title
    (optionally gated on a race-name token). None if unmatched."""
    if not rows:
        return None
    nm = (name or "").lower().strip()
    last = nm.split()[-1] if nm else ""
    tok = (race_token or "").lower()
    fallback = None
    for title, cents in rows:
        if tok and tok not in title:
            continue
        if nm and nm in title:
            return cents
        if last and last in title:
            fallback = cents
    return fallback


def attach(data):
    """Best-effort: attach Kalshi YES prices + edge to the championship drivers and
    the first (upcoming) race's winner + pole. Mutates `data` in place."""
    sport = data.get("sport")
    races = data.get("races") or []
    if sport == "f1":
        champ = priced("KXF1")
        for d in data.get("drivers", []):
            c = match_name(d["name"], champ)
            if c is not None:
                d["kalshi_cents"] = c
                d["edge"] = round(d["title_pct"] - c, 1)
        if races:
            _attach_race(races[0], priced("KXF1RACE"), priced("KXF1POLE"))
    elif sport == "nascar":
        if races:
            tok = _token(races[0]["name"])
            _attach_race(races[0], priced("KXNASCARRACE"), None, token=tok)
    return data


def _token(race_name):
    """A distinctive lowercase token to gate NASCAR matches to the right race
    (the series mixes Cup / Xfinity / Trucks)."""
    words = [w for w in (race_name or "").replace("/", " ").split() if len(w) > 3]
    return words[-1] if words else ""


def _attach_race(race, win_rows, pole_rows, token=None):
    race["priced"] = True
    for x in race.get("winner", []):
        c = match_name(x["name"], win_rows, race_token=token)
        if c is not None:
            x["kalshi_cents"] = c
            x["edge"] = round(x["pct"] - c, 1)
    if pole_rows is not None:
        for x in race.get("pole", []):
            c = match_name(x["name"], pole_rows)
            if c is not None:
                x["kalshi_cents"] = c
                x["edge"] = round(x["pct"] - c, 1)
