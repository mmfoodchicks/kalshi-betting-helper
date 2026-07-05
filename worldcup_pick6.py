"""World Cup player Pick 6 board.

DraftKings-style More/Less player props for the World Cup, built from ESPN's
fifa.world squad rosters -- which carry each player's competition stats INLINE
(shots, shots on target, goals, assists, fouls, yellow cards, and saves for
keepers), with appearances as the per-match denominator. We regress each rate
toward a position prior (small tournament samples), then frame DK-style props:
counting rarities (goals / assists / cards) are More-only at 0.5, the rest are
More/Less at a line near the projection -- exactly the DK Pick 6 structure.

ESPN's WC categories don't expose tackles / passes / interceptions, so those
props aren't offered (honest: we only board what the feed actually measures).
"""

import math as _math
import time as _time

import worldcup                     # reuse worldcup._get (ESPN soccer getter)

_SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    if val is not None:
        _cache[key] = (_time.time(), val)
    return val


def _get(url):
    try:
        return worldcup._get(url)
    except Exception:
        return None


# ---- Position priors (per-match rates) for regression ----------------------
_PRIOR = {
    "F": {"shots": 2.4, "sot": 0.95, "goals": 0.34, "assists": 0.16, "fouls": 1.0, "saves": 0.0},
    "M": {"shots": 1.2, "sot": 0.42, "goals": 0.13, "assists": 0.16, "fouls": 1.3, "saves": 0.0},
    "D": {"shots": 0.5, "sot": 0.15, "goals": 0.05, "assists": 0.07, "fouls": 1.2, "saves": 0.0},
    "G": {"shots": 0.02, "sot": 0.0, "goals": 0.0, "assists": 0.02, "fouls": 0.1, "saves": 3.0},
}
_REG = 3.5                          # pseudo-matches of prior blended into each rate


def _flat(stats):
    out = {}
    for c in (stats or {}).get("splits", {}).get("categories", []):
        for s in c.get("stats", []):
            try:
                out[s.get("name")] = float(s.get("value"))
            except (TypeError, ValueError):
                out[s.get("name")] = 0.0
    return out


def team_players(team_id):
    """[{name, pos, apps, rates{...}, yellow_rate}] for a squad. Cached 6h."""
    def build():
        r = _get(f"{_SITE}/teams/{team_id}/roster")
        if not r:
            return None
        out = []
        for a in r.get("athletes", []):
            f = _flat(a.get("statistics"))
            apps = f.get("appearances", 0.0)
            if apps < 1:
                continue                    # no minutes -> nothing to project
            pos = (a.get("position") or {}).get("abbreviation") or "M"
            base = _PRIOR.get(pos, _PRIOR["M"])

            def rate(total_key, prior_key):
                per = f.get(total_key, 0.0) / apps
                return (apps * per + _REG * base[prior_key]) / (apps + _REG)

            out.append({
                "name": a.get("displayName"), "pos": pos, "apps": int(apps),
                "rates": {"shots": rate("totalShots", "shots"),
                          "sot": rate("shotsOnTarget", "sot"),
                          "goals": rate("totalGoals", "goals"),
                          "assists": rate("goalAssists", "assists"),
                          "fouls": rate("foulsCommitted", "fouls"),
                          "saves": rate("saves", "saves")},
                # P(gets booked) this match, regressed
                "yellow_rate": min(0.6, (f.get("yellowCards", 0.0) + 0.5) / (apps + 4.0))})
        return out or None
    return _cached(("wc_squad", team_id), 6 * 3600, build)


# ---- Prop framing -----------------------------------------------------------
def _pois_over(mean, line):
    """P(X > line) for X ~ Poisson(mean)."""
    if mean <= 0:
        return 0.0
    k, cdf, term = int(_math.floor(line)) + 1, 0.0, _math.exp(-mean)
    for i in range(k):
        if i > 0:
            term *= mean / i
        cdf += term
    return max(0.0, min(1.0, 1.0 - cdf))


# stat key -> (label, More/Less allowed, min projection to offer).  Rarities are
# More-only at 0.5 on DK; the volume floor keeps a defender's 0.1-shot line off
# the board (DK only posts props on players who actually rack the stat up).
_PROPS = [("shots", "shots", True, 1.1), ("sot", "shots on target", True, 0.7),
          ("fouls", "fouls", True, 0.9), ("saves", "saves (GK)", True, 1.8),
          ("goals", "goal", False, 0.18), ("assists", "assist", False, 0.14)]


def _leg(player, team, label, mean, both, matchup):
    if not both:                                    # More-only at 0.5 (goal / assist)
        p = _pois_over(mean, 0.5)
        if not (0.5 <= p <= 0.9):
            return None
        return _mk(player, team, label, 0.5, "More", p, mean, matchup)
    # More/Less at the nearest round line, so a real shooter leans More.
    line = max(0.5, round(mean) - 0.5)
    p_over = _pois_over(mean, line)
    side, prob = ("More", p_over) if mean >= line else ("Less", 1 - p_over)
    if not (0.52 <= prob <= 0.9):
        return None
    return _mk(player, team, label, line, side, prob, mean, matchup)


def _mk(player, team, label, line, side, prob, mean, matchup):
    return {"player": player, "team": team, "stat": label, "line": round(line, 1),
            "side": side, "prob": round(prob * 100, 1), "proj": round(mean, 2),
            "matchup": matchup}


def _match_props(team_id, team_name, matchup):
    picks = []
    for p in (team_players(team_id) or []):
        for key, label, both, floor in _PROPS:
            mean = p["rates"].get(key, 0.0)
            if key == "saves" and p["pos"] != "G":
                continue
            if key in ("shots", "sot", "goals", "assists") and p["pos"] == "G":
                continue
            if mean < floor:
                continue
            leg = _leg(p["name"], team_name, label, mean, both, matchup)
            if leg:
                picks.append(leg)
        # yellow card: a straight P(booked) More at 0.5
        if 0.5 <= p["yellow_rate"] <= 0.9:
            picks.append(_mk(p["name"], team_name, "yellow card", 0.5, "More",
                             p["yellow_rate"], p["yellow_rate"], matchup))
    return picks


def upcoming(limit=6):
    """Upcoming WC matches: [{team1,team2,id1,id2,date}]. Cached 30m."""
    def build():
        sb = _get(f"{_SITE}/scoreboard")
        if not sb:
            return None
        out = []
        for e in sb.get("events", []):
            c = (e.get("competitions") or [{}])[0]
            cs = c.get("competitors", [])
            if len(cs) < 2:
                continue
            home = next((x for x in cs if x.get("homeAway") == "home"), cs[0])
            away = next((x for x in cs if x.get("homeAway") == "away"), cs[1])
            out.append({"team1": away["team"]["displayName"], "team2": home["team"]["displayName"],
                        "id1": away["team"]["id"], "id2": home["team"]["id"],
                        "date": (e.get("date") or "")[:10]})
        return out or None
    return _cached(("wc_upcoming",), 1800, build)


_PICK6_PAYOUT = {"2": 3.0, "3": 6.0, "4": 10.0, "5": 20.0, "6": 25.0}


def board(max_matches=6):
    """The WC player Pick 6 board: More/Less legs across the upcoming slate."""
    def build():
        matches = upcoming()
        if not matches:
            return None
        picks, seen = [], []
        for m in matches[:max_matches]:
            mu = f"{m['team1']} vs {m['team2']}"
            picks += _match_props(m["id1"], m["team1"], mu)
            picks += _match_props(m["id2"], m["team2"], mu)
            seen.append({"matchup": mu, "date": m["date"]})
        if not picks:
            return None
        picks.sort(key=lambda x: -x["prob"])
        return {"picks": picks[:80], "matches": seen, "payouts": _PICK6_PAYOUT,
                "note": "Per-match player rates from ESPN's WC squad stats, regressed to a "
                        "position prior. Goals/assists/cards are More-only at 0.5 (DK style); "
                        "shots/fouls/saves are More/Less. Match ours to the book's line."}
    return _cached(("wc_pick6", max_matches), 1800, build)
