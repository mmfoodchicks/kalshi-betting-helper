"""NFL week board: modeled scores, yards, TDs + key-player projections.

Mirrors the MLB "today" slate (a card per game with a modeled score, a win-
probability bar and per-team detail), but for football. The 2026 season hasn't
kicked off, so ratings come from the last COMPLETED season (2025); the schedule
comes from the upcoming season and skips preseason (ESPN seasontype=2).

Data is ESPN's public API (same source as the live scoreboard + racing feeds):
  - standings                -> points for/against per team (the score model)
  - teams/{id}/statistics    -> pass / rush yards + TDs per game (yard model)
  - teams/{id}/leaders       -> each team's passing/rushing/receiving leader
                                (resolved to a name for the key-player props)

ESPN rate-limits a big fan-out, so the board is assembled in a NON-BLOCKING
background thread (like the LoL board) and the frontend polls until it lands.
"""

import math as _math
import clock
import threading as _threading
import time as _time
import concurrent.futures as _cf

import racing                       # reuse racing._get_json (ESPN-friendly getter)
import errlog

_CORE = "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
_SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"

_cache = {}                         # key -> (epoch, value)


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    if val is not None:              # never cache a failed fetch
        _cache[key] = (_time.time(), val)
    return val


def _get(url):
    try:
        return racing._get_json(url, timeout=15)
    except Exception as _e:
        errlog.note("NFL-espn-fetch", _e, path=url.split("?")[0][-120:])
        return None


# ---- Model constants --------------------------------------------------------
_HFA = 1.3                          # home-field points edge per side (~2.6 spread)
_MARGIN_SD = 13.2                   # SD of an NFL game's final margin
_DEF_DAMP = 0.5                     # how hard opponent defense swings a yardage line
_TD_POINTS = 6.95                   # avg points per "touchdown-equivalent" drive


def _norm_cdf(z):
    return 0.5 * (1.0 + _math.erf(z / _math.sqrt(2)))


def _season():
    """Upcoming NFL season year (a new season is 'current' from March on)."""
    t = clock.today_et()
    return t.year if t.month >= 3 else t.year - 1


# ---- Team ratings (scoring) -------------------------------------------------
def team_ratings(season):
    """{abbr: {name, id, w, l, g, pf_pg, pa_pg}} from one standings call. Cached 6h."""
    def build():
        d = _get(f"https://site.api.espn.com/apis/v2/sports/football/nfl/standings?season={season}")
        if not d:
            return None
        out = {}
        for grp in d.get("children", []):
            for e in grp.get("standings", {}).get("entries", []):
                st = {s["name"]: s.get("value") for s in e.get("stats", [])}
                w, l, t = st.get("wins") or 0, st.get("losses") or 0, st.get("ties") or 0
                g = (w + l + t) or 1
                out[e["team"]["abbreviation"]] = {
                    "name": e["team"].get("displayName"), "id": e["team"].get("id"),
                    "w": int(w), "l": int(l), "g": int(g),
                    "pf_pg": round((st.get("pointsFor") or 0) / g, 2),
                    "pa_pg": round((st.get("pointsAgainst") or 0) / g, 2)}
        return out or None
    return _cached(("nfl_rtg", season), 6 * 3600, build)


# ---- Team offense (yards / TDs) ---------------------------------------------
def team_offense(season, tid):
    """{pass_ypg, rush_ypg, pass_td_g, rush_td_g, tot_td_g} for a team. Cached 24h."""
    def build():
        d = _get(f"{_CORE}/seasons/{season}/types/2/teams/{tid}/statistics")
        if not d:
            return None
        cats = {c["name"]: {s["name"]: s.get("value") for s in c["stats"]}
                for c in d.get("splits", {}).get("categories", [])}
        pas, rus = cats.get("passing", {}), cats.get("rushing", {})
        g = (pas.get("gamesPlayed") or 17) or 17
        return {"pass_ypg": round(pas.get("passingYardsPerGame") or 0, 1),
                "rush_ypg": round(rus.get("rushingYardsPerGame") or 0, 1),
                "pass_td_g": round((pas.get("passingTouchdowns") or 0) / g, 2),
                "rush_td_g": round((rus.get("rushingTouchdowns") or 0) / g, 2),
                "tot_td_g": round((pas.get("totalTouchdowns") or 0) / g, 2)}
    return _cached(("nfl_off", season, tid), 24 * 3600, build)


# ---- Key players (leaders) --------------------------------------------------
_LEAD_CATS = [("passingLeader", "QB", "pass"), ("rushingLeader", "RB", "rush"),
              ("receivingLeader", "WR", "rec")]


def team_leaders(season, tid, games):
    """[{role, name, pos, ypg, kind}] -- the passing/rushing/receiving leaders.
    Two hops (team leaders -> athlete name), so it's cached 24h."""
    def build():
        d = _get(f"{_CORE}/seasons/{season}/types/2/teams/{tid}/leaders")
        if not d:
            return None
        by = {c["name"]: c for c in d.get("categories", [])}
        out = []
        for cat, role, kind in _LEAD_CATS:
            lst = (by.get(cat) or {}).get("leaders") or []
            if not lst:
                continue
            top = lst[0]
            ref = (top.get("athlete") or {}).get("$ref")
            name = None
            if ref:
                a = _get(ref)
                name = (a or {}).get("displayName")
            out.append({"role": role, "kind": kind, "name": name or role,
                        "ypg": round((top.get("value") or 0) / (games or 17), 1)})
        return out
    return _cached(("nfl_lead", season, tid), 24 * 3600, build) or []


# ---- Schedule ---------------------------------------------------------------
def schedule(week, season, seasontype=2):
    """Week `week` of `season`. `seasontype` 2 = regular season, 1 = PRESEASON.

    Preseason used to be unreachable here, which made the whole NFL board look
    dead through August -- the Hall of Fame game and three weeks of exhibitions
    are real games that Kalshi books moneylines, spreads and totals on. Cached 1h,
    keyed on the season type so the two do not collide."""
    def build():
        d = _get(f"{_SITE}/scoreboard?seasontype={seasontype}&week={week}&dates={season}")
        if not d:
            return None
        out = []
        for e in d.get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            cs = comp.get("competitors", [])
            home = next((c for c in cs if c.get("homeAway") == "home"), None)
            away = next((c for c in cs if c.get("homeAway") == "away"), None)
            if not home or not away:
                continue
            out.append({
                "date": e.get("date"),
                "state": comp.get("status", {}).get("type", {}).get("state"),
                "home": home["team"]["abbreviation"], "away": away["team"]["abbreviation"],
                "home_id": home["team"].get("id"), "away_id": away["team"].get("id"),
                "home_name": home["team"].get("shortDisplayName"),
                "away_name": away["team"].get("shortDisplayName")})
        return out or None
    return _cached(("nfl_sched", week, season, seasontype), 3600, build)


# ---- Model ------------------------------------------------------------------
def _def_adj(pa_pg_opp, lg_pa_pg):
    """Yardage multiplier from an opponent's scoring defense (weak D -> more yards)."""
    if lg_pa_pg <= 0:
        return 1.0
    return min(1.22, max(0.82, (pa_pg_opp / lg_pa_pg) ** _DEF_DAMP))


def _team_view(rt, off, leaders, def_adj, exp_pts):
    """Per-team modeled line: matchup-adjusted yards, TDs, and key players."""
    scale = exp_pts / off["tot_td_g"] / _TD_POINTS if off and off["tot_td_g"] else 1.0
    pass_y = round((off["pass_ypg"] if off else 220) * def_adj)
    rush_y = round((off["rush_ypg"] if off else 110) * def_adj)
    tds = round(exp_pts / _TD_POINTS, 1)
    players = []
    for p in (leaders or []):
        adj = def_adj if p["kind"] in ("pass", "rec") else def_adj  # all scale on opp D
        players.append({"role": p["role"], "name": p["name"],
                        "proj_yds": round(p["ypg"] * adj)})
    return {"pass_yds": pass_y, "rush_yds": rush_y, "tds": tds,
            "pass_td": round((off["pass_td_g"] if off else 1.4) * (scale if scale else 1), 1),
            "rush_td": round((off["rush_td_g"] if off else 0.9) * (scale if scale else 1), 1),
            "players": players}


_board_inflight = set()


def board(week=1):
    """Non-blocking: return the cached week board if fresh, else kick a background
    build and return None (frontend polls). Heavy ESPN fan-out -> background."""
    season = _season()
    key = ("nfl_board", week, season)
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < 1800:
        return hit[1]
    if week not in _board_inflight:
        _board_inflight.add(week)

        def _bg():
            try:
                val = _build_board(week, season)
                if val is not None:
                    _cache[key] = (_time.time(), val)
            finally:
                _board_inflight.discard(week)
        _threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None


def _build_board(week, season):
    rt_season = season - 1                       # last completed season for ratings
    rtg = team_ratings(rt_season)
    sched = schedule(week, season)
    if not rtg or not sched:
        return None
    lg_pf = sum(r["pf_pg"] for r in rtg.values()) / len(rtg)
    lg_pa = sum(r["pa_pg"] for r in rtg.values()) / len(rtg)

    # Pre-fetch offense + leaders for every team on the slate, concurrently.
    tids = {}
    for g in sched:
        for side in ("home", "away"):
            ab = g[side]
            if ab in rtg:
                tids[ab] = rtg[ab]["id"]
    with _cf.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda ab: team_offense(rt_season, tids[ab]), tids))
        list(ex.map(lambda ab: team_leaders(rt_season, tids[ab], rtg[ab]["g"]), tids))

    games = []
    for g in sched:
        h, a = g["home"], g["away"]
        rh, ra = rtg.get(h), rtg.get(a)
        if not rh or not ra:
            continue
        # Expected points: average of a team's offense and its opponent's defense.
        exp_h = 0.5 * (rh["pf_pg"] + ra["pa_pg"]) + _HFA
        exp_a = 0.5 * (ra["pf_pg"] + rh["pa_pg"]) - _HFA
        margin = exp_h - exp_a
        p_home = _norm_cdf(margin / _MARGIN_SD)
        offh, offa = team_offense(rt_season, rh["id"]), team_offense(rt_season, ra["id"])
        ldh = team_leaders(rt_season, rh["id"], rh["g"])
        lda = team_leaders(rt_season, ra["id"], ra["g"])
        games.append({
            "date": g["date"], "state": g["state"],
            "home": h, "away": a, "home_name": rh["name"], "away_name": ra["name"],
            "home_rec": f"{rh['w']}-{rh['l']}", "away_rec": f"{ra['w']}-{ra['l']}",
            "exp_home": round(exp_h, 1), "exp_away": round(exp_a, 1),
            "exp_total": round(exp_h + exp_a, 1),
            "score_home": max(0, round(exp_h)), "score_away": max(0, round(exp_a)),
            "p_home": round(p_home * 100, 1), "p_away": round((1 - p_home) * 100, 1),
            "fav": h if margin >= 0 else a, "spread": round(abs(margin), 1),
            "home_view": _team_view(rh, offh, ldh, _def_adj(ra["pa_pg"], lg_pa), exp_h),
            "away_view": _team_view(ra, offa, lda, _def_adj(rh["pa_pg"], lg_pa), exp_a),
        })
    if not games:
        return None
    games.sort(key=lambda x: x["date"] or "")
    return {"week": week, "season": season, "ratings_season": rt_season,
            "games": games, "n": len(games),
            "note": "Modeled from %d team ratings; scores are expected points, not "
                    "predictions of the exact final. Yards/TDs adjust for opponent "
                    "defense." % rt_season}
