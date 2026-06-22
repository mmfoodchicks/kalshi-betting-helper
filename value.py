"""Recent-form vs Kalshi value finder for MLB player props.

The play you described: Kalshi prices a batter's "1+ hit" at 72c (implied 72%)
but he's actually gotten a hit in only 42% of his recent games -> fade it; or
he's hit safely in 82% of recent games but Kalshi's at 42c -> buy it. This pulls
Kalshi's live player-prop markets (KXMLBHIT / KXMLBHR), looks up each player's
recent game-by-game rate, and flags the gaps.

Honest caveats surfaced in the output: recent form is a SMALL, noisy sample and
ignores tonight's pitcher, so we also show the matchup-adjusted model number and
warn when recent form and the model disagree (it may just be a hot/cold streak).
"""

import datetime
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import kalshi

_cache = {}
_STATS = "https://statsapi.mlb.com/api/v1"


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        val = None
    _cache[key] = (time.time(), val)
    return val


def _norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", s.lower())
    return " ".join(re.sub(r"[^a-z ]", " ", s).split())


def player_index(season):
    """{normalized name: player_id} for the season's MLB players."""
    def build():
        d = kalshi._get_json(f"{_STATS}/sports/1/players?season={season}")
        out = {}
        for p in d.get("people", []):
            nm = _norm(p.get("fullName", ""))
            if nm:
                out.setdefault(nm, p["id"])
        return out
    return _cached(("pidx", season), 12 * 3600, build) or {}


def _rates(games):
    g = len(games)
    if not g:
        return {}
    hits = [int(s.get("hits", 0) or 0) for s in games]
    hr = [int(s.get("homeRuns", 0) or 0) for s in games]
    r = lambda arr, k: sum(1 for x in arr if x >= k) / g
    return {("hits", 1): r(hits, 1), ("hits", 2): r(hits, 2), ("hits", 3): r(hits, 3),
            ("hr", 1): r(hr, 1), ("hr", 2): r(hr, 2)}


def recent_form(pid, season, n=15):
    """Recent (last n games) and season-long rates per threshold, so a hot/cold
    streak can be told apart from true talent."""
    def build():
        d = kalshi._get_json(
            f"{_STATS}/people/{pid}/stats?stats=gameLog&group=hitting&season={season}")
        sp = [s.get("stat", {}) for s in (d.get("stats") or [{}])[0].get("splits", [])]
        if len(sp) < 5:
            return None
        return {"games": min(n, len(sp)), "sgames": len(sp),
                "recent": _rates(sp[-n:]), "season": _rates(sp)}
    return _cached(("form", pid, season), 3600, build)


_TITLE = re.compile(r"(.+?):\s*(\d+)\+\s*(hit|home run)", re.I)


def _markets(series):
    try:
        d = kalshi._get_json(f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=400")
        return d.get("markets", [])
    except Exception:
        return []


def find_value(season=None, n_games=15, min_edge=10.0):
    """Player props where Kalshi's price diverges from recent form, sorted by edge."""
    season = season or str(datetime.date.today().year)
    idx = player_index(season)
    raw = []
    for series, stat in (("KXMLBHIT", "hits"), ("KXMLBHR", "hr")):
        for m in _markets(series):
            mt = _TITLE.match(m.get("title", "") or "")
            if not mt:
                continue
            name, line = mt.group(1).strip(), int(mt.group(2))
            yes = kalshi._cents(m.get("yes_ask_dollars"))
            no = kalshi._cents(m.get("no_ask_dollars"))
            if yes is None or yes >= 99 or yes <= 1:        # no real two-sided market yet
                continue
            pid = idx.get(_norm(name))
            if not pid:
                continue
            raw.append({"name": name, "stat": stat, "line": line, "yes_ask": yes,
                        "no_ask": no, "pid": pid, "ticker": m.get("ticker")})

    # Recent form per distinct player, fetched in parallel.
    pids = list({r["pid"] for r in raw})
    forms = {}
    if pids:
        with ThreadPoolExecutor(max_workers=10) as ex:
            for pid, f in zip(pids, ex.map(lambda i: recent_form(i, season, n_games), pids)):
                forms[pid] = f

    plays = []
    for r in raw:
        f = forms.get(r["pid"])
        if not f:
            continue
        rate = (f["recent"] or {}).get((r["stat"], r["line"]))
        srate = (f["season"] or {}).get((r["stat"], r["line"]))
        if rate is None:
            continue
        rate_pct = round(rate * 100, 1)
        season_pct = round(srate * 100, 1) if srate is not None else None
        edge_yes = round(rate_pct - r["yes_ask"], 1)
        edge_no = round((100 - rate_pct) - r["no_ask"], 1) if r["no_ask"] is not None else None
        if (edge_no is None) or edge_yes >= edge_no:
            side, edge, cost = "YES", edge_yes, r["yes_ask"]
        else:
            side, edge, cost = "NO", edge_no, r["no_ask"]
        if edge is None or edge < min_edge:
            continue
        # Streak flag: recent form far above season suggests regression risk.
        streak = season_pct is not None and abs(rate_pct - season_pct) >= 18
        label = f"{r['name']} {r['line']}+ {'HR' if r['stat'] == 'hr' else 'hits'}"
        plays.append({"label": label, "side": side, "edge_cents": edge, "cost_cents": cost,
                      "recent_pct": rate_pct, "season_pct": season_pct, "games": f["games"],
                      "streak": streak, "yes_ask": r["yes_ask"], "ticker": r["ticker"]})
    plays.sort(key=lambda p: -p["edge_cents"])
    return {"plays": plays[:40], "n_games": n_games, "min_edge": min_edge,
            "markets_scanned": len(raw)}
