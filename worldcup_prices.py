"""Attach live Kalshi + Polymarket prices and model edges to a World Cup run.

  * Champion (KXMENWORLDCUP + Polymarket "World Cup winner") -> per team,
  * Match 3-way result (KXWCGAME), total goals (KXWCTOTAL) and both-teams-to-score
    (KXWCBTTS) -> per upcoming match, matched by the shared event suffix.

Edge = model probability - market ask (both on a 0-100 scale). Anything not open
yet is simply skipped.
"""

import re

import kalshi


def _markets(series):
    out, cursor = [], ""
    for _ in range(6):
        url = f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=1000"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            d = kalshi._get_json(url)
        except Exception:
            break
        out += d.get("markets", [])
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    return out


def _suffix(ev):
    """Match key shared by a game's winner/total/BTTS events:
    'KXWCGAME-26JUN28RSACAN' -> '26JUN28RSACAN'."""
    return (ev or "").split("-", 1)[1] if ev and "-" in ev else ev


def _clean(sub):
    return re.sub(r"^reg time:\s*", "", (sub or "").strip().lower())


def _game_index():
    """{suffix: {teams:set, cents:{team_lower: ask, 'tie': ask}}} from KXWCGAME."""
    idx = {}
    for m in _markets("KXWCGAME"):
        suf = _suffix(m.get("event_ticker"))
        title = m.get("title") or ""
        mt = re.match(r"(.+?)\s+vs\s+(.+?)\s+Winner", title, re.I)
        e = idx.setdefault(suf, {"teams": set(), "cents": {}})
        if mt:
            e["teams"] = {mt.group(1).strip(), mt.group(2).strip()}
        sub = _clean(m.get("yes_sub_title"))
        e["cents"][sub] = kalshi._cents(m.get("yes_ask_dollars"))
    return idx


def _total_index():
    """{suffix: {line: over_ask}} from KXWCTOTAL."""
    idx = {}
    for m in _markets("KXWCTOTAL"):
        suf = _suffix(m.get("event_ticker"))
        mt = re.search(r"over\s+(\d+(?:\.5)?)", _clean(m.get("yes_sub_title")))
        if mt:
            idx.setdefault(suf, {})[float(mt.group(1))] = kalshi._cents(m.get("yes_ask_dollars"))
    return idx


def _btts_index():
    idx = {}
    for m in _markets("KXWCBTTS"):
        idx[_suffix(m.get("event_ticker"))] = kalshi._cents(m.get("yes_ask_dollars"))
    return idx


def _champion_kalshi():
    out = {}
    for m in _markets("KXMENWORLDCUP"):
        nm = (m.get("yes_sub_title") or "").strip().lower()
        if nm:
            out[nm] = kalshi._cents(m.get("yes_ask_dollars"))
    return out


def _champion_poly():
    try:
        import polymarket
        eid = polymarket.search_event_id("2026 World Cup winner")
        if not eid:
            return {}
        rows = {r["team"].lower(): r["yes_cents"] for r in polymarket.event_prices(eid)}
        # Guard against a wrong/illiquid match: a real winner market has a
        # favorite in the teens of cents, not a field all priced at ~0.
        if not rows or max(rows.values()) < 5:
            return {}
        return rows
    except Exception:
        return {}


def _edge(model_pct, cents):
    return round(model_pct - cents, 1) if cents is not None else None


def attach(data):
    """Mutate `data` in place with Kalshi/Poly prices + edges; return it."""
    if not data:
        return data
    games = _game_index()
    totals = _total_index()
    btts = _btts_index()
    champ_k = _champion_kalshi()
    champ_p = _champion_poly()

    priced = {"champion": False, "match": False}
    for t in data.get("teams", []):
        key = t["name"].lower()
        ck = champ_k.get(key)
        cp = champ_p.get(key)
        mk = {}
        if ck is not None:
            mk["kalshi"] = {"cents": ck, "edge": _edge(t["champion_pct"], ck)}
            priced["champion"] = True
        if cp is not None:
            mk["poly"] = {"cents": cp, "edge": _edge(t["champion_pct"], cp)}
            priced["champion"] = True
        if mk:
            t["champion_market"] = mk

    for m in data.get("matches", []):
        pair = {m["home"], m["away"]}
        suf = next((s for s, e in games.items() if e["teams"] == pair), None)
        if not suf:
            # totals/btts share the suffix even if the winner market is unmatched;
            # try a looser name overlap.
            suf = next((s for s, e in games.items()
                        if e["teams"] & pair and len(e["teams"]) == 2 and
                        all(any(w in tt.lower() for w in p.lower().split())
                            for p, tt in zip(sorted(pair), sorted(e["teams"])))), None)
        mk = {}
        if suf and suf in games:
            cents = games[suf]["cents"]
            home_c = cents.get(m["home"].lower())
            away_c = cents.get(m["away"].lower())
            tie_c = cents.get("tie")
            mk["winner"] = {"home": {"cents": home_c, "edge": _edge(m["p_home"], home_c)},
                            "draw": {"cents": tie_c, "edge": _edge(m["p_draw"], tie_c)},
                            "away": {"cents": away_c, "edge": _edge(m["p_away"], away_c)}}
            priced["match"] = True
        if suf and suf in totals:
            o25 = totals[suf].get(2.5)
            if o25 is not None:
                mk["over25"] = {"cents": o25, "edge": _edge(m["over25"], o25)}
        if suf and suf in btts:
            mk["btts"] = {"cents": btts[suf], "edge": _edge(m["btts_pct"], btts[suf])}
        if mk:
            m["markets"] = mk
    data["priced"] = priced
    return data
