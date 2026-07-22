"""NFL team + player data layer for the drive-level game engine (deep_data's
football twin).

The core trick: Sleeper's weekly player projections are already MATCHUP-ADJUSTED
(their modelers bake in the opponent), and every row carries the full component
stat line. Summing a team's players therefore yields an opponent-aware expected
TEAM production — pass/rush yards, TDs, INTs, and (via kickers) field goals —
which converts straight into expected points and per-drive rates for the
simulator. Each player's share of his team's expected production becomes his
usage share, so the sim can deal a simulated team game back out to the players
and keep every stat line consistent with the team total and the game script.

Home/away pairing comes from ESPN's scoreboard (Sleeper rows only know
team/opponent). Everything is cached and fails safe to None.
"""
import clock

import racing                      # shared cached JSON getter
import nfl_live                    # ESPN schedule + last-season ratings (fallback)

_SLEEPER = "https://api.sleeper.com/projections/nfl/{season}/{week}"
_POS = ("QB", "RB", "WR", "TE")

# Sleeper (and ESPN mostly) abbreviations; canon fixes the few that differ.
_CANON = {"WAS": "WSH", "JAC": "JAX", "OAK": "LV", "SD": "LAC", "LA": "LAR"}


def _canon(ab):
    return _CANON.get((ab or "").upper(), (ab or "").upper())


_STATS = ("pass_yd", "pass_td", "pass_int", "rush_yd", "rush_td",
          "rec", "rec_yd", "rec_td", "fum_lost")

# Points a defense/special teams unit adds per game league-wide (pick-sixes,
# return TDs, safeties) — not in any offensive player's projection.
_DST_PTS = 1.1


def _rows(season, week, positions):
    q = "&".join(f"position[]={p}" for p in positions)
    url = f"{_SLEEPER.format(season=season, week=week)}?season_type=regular&{q}&order_by=pts_ppr"
    try:
        return racing._get_json(url, timeout=25) or []
    except Exception:
        return None


def week_teams(season, week):
    """{abbr: team profile} for one week, from Sleeper projections + ESPN schedule.

    profile = {abbr, opp, home (bool|None), exp:{...team sums...}, points,
               players:[{name, pos, pass_yd, rush_yd, rec, rec_yd, pass_td,
                         rush_td, rec_td, int, fum}]}
    Returns None when Sleeper has nothing for the week (deep offseason)."""
    def build():
        skill = _rows(season, week, _POS)
        if not skill:
            return None
        kick = _rows(season, week, ("K",)) or []

        teams = {}

        def team(ab, opp):
            return teams.setdefault(ab, {
                "abbr": ab, "opp": opp, "home": None,
                "exp": {k: 0.0 for k in _STATS} | {"fgm": 0.0, "xpm": 0.0},
                "players": []})

        for r in skill:
            st = r.get("stats") or {}
            p = r.get("player") or {}
            ab, opp = _canon(r.get("team")), _canon(r.get("opponent"))
            if not ab or not opp or p.get("position") not in _POS:
                continue
            if (st.get("pts_ppr") or 0) <= 0.5:            # camp bodies
                continue
            t = team(ab, opp)
            row = {"name": (p.get("first_name", "") + " " + p.get("last_name", "")).strip(),
                   "pos": p.get("position")}
            for k in _STATS:
                v = st.get(k) or 0.0
                t["exp"][k] += v
                row[k] = round(v, 2)
            row["int"] = row.pop("pass_int")
            row["fum"] = row.pop("fum_lost")
            t["players"].append(row)

        for r in kick:
            st = r.get("stats") or {}
            ab = _canon(r.get("team"))
            if ab in teams:
                teams[ab]["exp"]["fgm"] += st.get("fgm") or 0.0
                teams[ab]["exp"]["xpm"] += st.get("xpm") or 0.0

        # Expected points: 6/TD + real XP + 3/FG + the DST/return sliver.
        for t in teams.values():
            e = t["exp"]
            tds = e["pass_td"] + e["rush_td"]
            xp = e["xpm"] if e["xpm"] > 0 else 0.97 * tds
            t["points"] = round(6.0 * tds + xp + 3.0 * e["fgm"] + _DST_PTS, 2)
            t["players"].sort(key=lambda p: -(p["pass_yd"] + p["rush_yd"] + p["rec_yd"]))
            t["players"] = t["players"][:14]              # the lines a book would post

        # Home/away from ESPN's scoreboard for the week.
        try:
            for g in nfl_live.schedule(week, int(season)) or []:
                h, a = _canon(g["home"]), _canon(g["away"])
                if h in teams:
                    teams[h]["home"] = True
                if a in teams:
                    teams[a]["home"] = False
        except Exception:
            pass
        return teams or None
    return racing._cached(("nfl_week_teams", str(season), week), 3600, build)


def week_games(season, week):
    """[(home_profile, away_profile)] — the week's games, paired. Uses ESPN's
    schedule for the pairing/order; profiles from week_teams."""
    teams = week_teams(season, week)
    if not teams:
        return []
    out, seen = [], set()
    sched = []
    try:
        sched = nfl_live.schedule(week, int(season)) or []
    except Exception:
        pass
    for g in sched:
        h, a = _canon(g["home"]), _canon(g["away"])
        if h in teams and a in teams and (h, a) not in seen:
            seen.add((h, a))
            th, ta = teams[h], teams[a]
            th["home"], ta["home"] = True, False
            th["date"], ta["date"] = g.get("date"), g.get("date")
            th["name"], ta["name"] = g.get("home_name") or h, g.get("away_name") or a
            th["state"], ta["state"] = g.get("state"), g.get("state")
            out.append((th, ta))
    if out:
        return out
    # Schedule unreachable: fall back to Sleeper's own team/opponent pairing
    # (home unknown — the sim halves the home edge on these).
    for ab, t in teams.items():
        o = t["opp"]
        if o in teams and (o, ab) not in seen and (ab, o) not in seen:
            seen.add((ab, o))
            t["name"], teams[o]["name"] = ab, o
            out.append((t, teams[o]))
    return out
