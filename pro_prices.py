"""Attach live Kalshi futures prices + model edges to a pro-league projection.

Whatever season futures Kalshi has open right now — NFL win totals, NBA/NHL
conference winners, and later the championship + division markets — we match each
market to a team by name and hang the price, our model probability and the edge
(model - market) off that team. Markets that aren't open yet are simply skipped,
so the board lights up more columns as the calendar approaches the season.
"""

import math
import re

import kalshi
import pro_sim

# Series tickers per league, grouped by the market they price. Multiple
# candidates per slot because Kalshi's naming has drifted over time.
SERIES = {
    "nfl": {"champ": ["KXNFLSB", "KXNFLCHAMP", "KXSUPERBOWL"],
            "conf": ["KXNFLCONF", "KXNFLAFC", "KXNFLNFC"],
            "division": ["KXNFLDIV"],
            "wins": ["KXNFLWINS"]},
    "nba": {"champ": ["KXNBACHAMP", "KXNBAFINALS"],
            "conf": ["KXNBAEAST", "KXNBAWEST", "KXNBACONF"],
            "division": ["KXNBADIV"],
            "wins": ["KXNBAWINS"]},
    "nhl": {"champ": ["KXNHLCUP", "KXSTANLEYCUP", "KXNHLCHAMP"],
            "conf": ["KXNHLCONF", "KXNHLEAST", "KXNHLWEST"],
            "division": ["KXNHLDIV"],
            "wins": ["KXNHLWINS"]},
}


def _markets(series_ticker):
    """All open markets for a series, following the cursor (a full league of
    win-total lines is ~550 markets, past the single-page limit)."""
    rows, cursor = [], ""
    for _ in range(8):                                   # safety bound on paging
        url = (f"{kalshi.BASE}/markets?series_ticker={series_ticker}"
               f"&status=open&limit=1000")
        if cursor:
            url += f"&cursor={cursor}"
        try:
            d = kalshi._get_json(url)
        except Exception:
            break
        for m in d.get("markets", []):
            rows.append({
                "title": (m.get("title") or "").lower(),
                "sub": (m.get("yes_sub_title") or m.get("subtitle") or "").lower(),
                "cents": kalshi._cents(m.get("yes_ask_dollars")),
            })
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    return rows


# Cities shared by two franchises -> a city match alone is ambiguous and must be
# pinned by the nickname initial (Kalshi writes "Los Angeles C", "New York J").
def _ambiguous_locations(meta):
    seen = {}
    for t in meta.values():
        loc = (t.get("location") or "").lower()
        seen[loc] = seen.get(loc, 0) + 1
    return {loc for loc, n in seen.items() if loc and n > 1}


def _team_match(team, rows, ambiguous):
    """First market that names this team; returns the row or None. Matches on the
    city, disambiguating shared cities by the nickname's first letter, and falls
    back to a distinctive nickname."""
    loc = (team.get("location") or "").lower()
    nick = (team.get("nick") or "").lower()
    pin = f"{loc} {nick[:1]}" if loc in ambiguous else loc
    for r in rows:
        hay = r["title"] + " " + r["sub"]
        if loc and pin in hay:
            return r
        if nick and len(nick) > 3 and nick in hay:
            return r
    return None


def attach(league, data):
    """Mutates `data` in place: adds `markets` to each team and a top-level
    `priced` flag per market type. Returns `data`."""
    if not data or not data.get("teams"):
        return data
    cfg = SERIES.get(league, {})
    # Pull each market group once.
    champ_rows = [r for st in cfg.get("champ", []) for r in _markets(st)]
    conf_rows = [r for st in cfg.get("conf", []) for r in _markets(st)]
    div_rows = [r for st in cfg.get("division", []) for r in _markets(st)]
    win_rows = [r for st in cfg.get("wins", []) for r in _markets(st)]

    import pro_data
    # Teams in `data` carry abbrev/name but not location/nick, which the matcher
    # needs, so pull the richer team meta.
    tmeta = {t["id"]: t for t in pro_data.teams(league)}
    ambiguous = _ambiguous_locations(tmeta)

    def edge(model_pct, cents):
        if cents is None:
            return None
        return round(model_pct - cents, 1)             # both on a 0-100 scale

    def team_rows(meta, rows):
        """Every market naming this team (win totals have several)."""
        loc = (meta.get("location") or "").lower()
        nick = (meta.get("nick") or "").lower()
        pin = f"{loc} {nick[:1]}" if loc in ambiguous else loc
        out = []
        for r in rows:
            hay = r["title"] + " " + r["sub"]
            if (loc and pin in hay) or (nick and len(nick) > 3 and nick in hay):
                out.append(r)
        return out

    priced = {"champ": False, "conf": False, "division": False, "wins": False}
    for t in data["teams"]:
        meta = tmeta.get(t["id"], {})
        markets = {}
        cm = _team_match(meta, champ_rows, ambiguous)
        if cm:
            priced["champ"] = True
            markets["champ"] = {"cents": cm["cents"], "model": t["champ_pct"],
                                "edge": edge(t["champ_pct"], cm["cents"])}
        com = _team_match(meta, conf_rows, ambiguous)
        if com:
            priced["conf"] = True
            markets["conf"] = {"cents": com["cents"], "model": t["conf_pct"],
                               "edge": edge(t["conf_pct"], com["cents"])}
        dm = _team_match(meta, div_rows, ambiguous)
        if dm:
            priced["division"] = True
            markets["division"] = {"cents": dm["cents"], "model": t["division_pct"],
                                   "edge": edge(t["division_pct"], dm["cents"])}
        # Win totals: each open market for this team carries a line in its subtitle
        # (e.g. "9+ wins"); price every line.
        wt, seen_lines = [], set()
        for r in team_rows(meta, win_rows):
            mline = re.search(r"(\d+)\s*\+?\s*win", r["sub"])
            if not mline:
                continue
            line = int(mline.group(1))
            if line in seen_lines:
                continue
            seen_lines.add(line)
            model = round(100 * pro_sim.win_total_prob(t, line), 1)
            wt.append({"line": line, "cents": r["cents"], "model": model,
                       "edge": edge(model, r["cents"])})
        if wt:
            priced["wins"] = True
            wt.sort(key=lambda x: x["line"])
            markets["win_totals"] = wt
        t["markets"] = markets
    data["priced"] = priced
    return data
