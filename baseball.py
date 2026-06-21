"""Baseball (MLB) betting insights + parlay combo generator.

Data sources (all public, no key):
  - MLB Stats API: schedule + probable pitchers, league-wide team hitting
    (overall + vs LHP/RHP splits), team pitching (overall + bullpen split),
    per-starter season & recent (lastX) stats, pitcher handedness, standings.
  - Kalshi KXMLBGAME markets: live moneyline-style prices per team.

Model (expected runs -> win probability), deepest version
---------------------------------------------------------
For each game we estimate each side's expected runs, then convert to a win
probability. Inputs folded in:

  STARTERS   each probable starter's ERA + WHIP, regressed toward league by
             innings, then blended with their recent (lastX) form. ~60% of game.
  BULLPEN    each team's isolated reliever ERA + WHIP (not the whole staff) for
             the remaining ~40% of the game.
  OFFENSE    team runs/game and OPS, with OPS taken vs the opposing starter's
             handedness (platoon split) for the innings he pitches.
  HOME FIELD home expected runs nudged up.
  PARK       venue run factor -> expected TOTAL runs (park has little effect on
             the moneyline since it helps both offenses, but matters for totals).

  ExpRuns = lgRPG * Offense * OppPitching
  p_home  = ExpRuns_home^1.83 / (ExpRuns_home^1.83 + ExpRuns_away^1.83)
"""

import itertools
import time as _time
from concurrent.futures import ThreadPoolExecutor

import kalshi  # reuse BASE + _get_json + _parse_time + _cents helpers
import weather as weather_mod
import stadiums as stadiums_mod
import props as props_mod

STATS_BASE = "https://statsapi.mlb.com/api/v1"

PYTH_EXP = 1.83
SP_INNINGS_WEIGHT = 0.60   # share of game the starter is responsible for
HOME_RUNS_MULT = 1.08      # home-field edge on home expected runs (~54%)
SP_IP_REGRESS = 50.0       # innings constant for regressing a starter's season ERA
RECENT_IP_REGRESS = 25.0   # innings constant for the recent-form blend
RECENT_WEIGHT = 0.25       # how much recent form pulls the season number

# Multiplicative run park factors by home team id (~1.0 = neutral). Approximate,
# directionally-standard values; affects expected TOTAL runs, not the moneyline.
PARK_FACTORS = {
    115: 1.15,  # COL Coors Field
    113: 1.06,  # CIN Great American
    111: 1.04,  # BOS Fenway
    109: 1.03,  # ARI Chase
    140: 1.02,  # TEX Globe Life
    147: 1.02,  # NYY Yankee Stadium
    110: 1.02,  # BAL Camden Yards
    112: 1.01,  # CHC Wrigley
    144: 1.01,  # ATL Truist
    145: 1.01,  # CWS Rate Field
    158: 1.00,  # MIL American Family
    118: 1.00,  # KC Kauffman
    116: 0.98,  # DET Comerica
    146: 0.98,  # MIA loanDepot
    121: 0.97,  # NYM Citi Field
    119: 0.97,  # LAD Dodger Stadium
    135: 0.96,  # SD Petco
    137: 0.94,  # SF Oracle
    136: 0.93,  # SEA T-Mobile
}

# ---- tiny TTL cache -------------------------------------------------------
_cache = {}
def _cached(key, ttl, producer):
    now = _time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = producer()
    _cache[key] = (now, val)
    return val


def _get(url):
    return kalshi._get_json(url)


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---- league-wide team stats (one call each, cached) -----------------------
def _team_split_map(season, group, sit_code, fields):
    """Generic league-wide statSplits fetch -> {team_id: {field: value}}."""
    url = (f"{STATS_BASE}/teams/stats?sportId=1&season={season}"
           f"&stats=statSplits&group={group}&sitCodes={sit_code}")
    data = _get(url)
    out = {}
    for s in data.get("stats", [{}])[0].get("splits", []):
        st = s["stat"]
        out[s["team"]["id"]] = {k: _f(st.get(k)) for k in fields}
    return out


def _hitting_map(season):
    def fetch():
        data = _get(f"{STATS_BASE}/teams/stats?sportId=1&season={season}&group=hitting&stats=season")
        out = {}
        for s in data["stats"][0]["splits"]:
            st = s["stat"]; g = _f(st.get("gamesPlayed"), 1) or 1
            out[s["team"]["id"]] = {"ops": _f(st.get("ops")), "rpg": _f(st.get("runs")) / g,
                                    "runs": int(_f(st.get("runs"))), "g": int(g)}
        return out
    return _cached(("hit", season), 600, fetch)


def _hitting_platoon(season):
    def fetch():
        return {"vl": _team_split_map(season, "hitting", "vl", ["ops"]),
                "vr": _team_split_map(season, "hitting", "vr", ["ops"])}
    return _cached(("hitplat", season), 600, fetch)


def _pitching_map(season):
    def fetch():
        data = _get(f"{STATS_BASE}/teams/stats?sportId=1&season={season}&group=pitching&stats=season")
        out = {}
        for s in data["stats"][0]["splits"]:
            st = s["stat"]
            out[s["team"]["id"]] = {"era": _f(st.get("era")), "whip": _f(st.get("whip"))}
        return out
    return _cached(("pit", season), 600, fetch)


def _bullpen_map(season):
    def fetch():
        return _team_split_map(season, "pitching", "rp", ["era", "whip"])
    return _cached(("bp", season), 600, fetch)


def _records_map(season):
    def fetch():
        data = _get(f"{STATS_BASE}/standings?leagueId=103,104&season={season}")
        out = {}
        for rec in data.get("records", []):
            for t in rec.get("teamRecords", []):
                out[t["team"]["id"]] = {"wins": int(t.get("wins", 0)), "losses": int(t.get("losses", 0)),
                                        "run_diff": int(t.get("runDifferential", 0))}
        return out
    return _cached(("rec", season), 600, fetch)


def _abbr_map(season):
    def fetch():
        data = _get(f"{STATS_BASE}/teams?sportId=1&season={season}")
        return {t["id"]: t.get("abbreviation", "") for t in data.get("teams", [])}
    return _cached(("abbr", season), 86400, fetch)


def _handedness(pids):
    """Batch pitcher handedness -> {id: 'R'|'L'}."""
    pids = [p for p in pids if p]
    if not pids:
        return {}
    def fetch():
        ids = ",".join(str(p) for p in sorted(pids))
        d = _get(f"{STATS_BASE}/people?personIds={ids}")
        return {p["id"]: p.get("pitchHand", {}).get("code", "R") for p in d.get("people", [])}
    return _cached(("hand", tuple(sorted(pids))), 3600, fetch)


def _pitcher_stats(pid, season):
    """Season + recent (lastX) line for a starter."""
    if not pid:
        return None
    def fetch():
        try:
            d = _get(f"{STATS_BASE}/people/{pid}/stats?stats=season,lastXGames"
                     f"&group=pitching&season={season}&limit=5")
            res = {}
            for s in d.get("stats", []):
                disp = s.get("type", {}).get("displayName")
                sp = s.get("splits", [])
                if not sp:
                    continue
                st = sp[0]["stat"]
                rec = {"era": _f(st.get("era")), "whip": _f(st.get("whip")),
                       "ip": _f(st.get("inningsPitched")), "k9": _f(st.get("strikeoutsPer9Inn"))}
                if disp == "season":
                    res["season"] = rec
                elif disp == "lastXGames":
                    res["recent"] = rec
            return res or None
        except Exception:
            return None
    return _cached(("sp", pid, season), 600, fetch)


def _league_avgs(hit, pit, bp, hitplat):
    n_h = len(hit) or 1; n_p = len(pit) or 1; n_b = len(bp) or 1
    def mean(d, k):
        vals = [t[k] for t in d.values() if t.get(k)]
        return sum(vals) / len(vals) if vals else 0
    lg = {
        "rpg": mean(hit, "rpg"), "ops": mean(hit, "ops"),
        "era": mean(pit, "era"), "whip": mean(pit, "whip"),
        "bp_era": mean(bp, "era"), "bp_whip": mean(bp, "whip"),
        "ops_vl": mean(hitplat["vl"], "ops"), "ops_vr": mean(hitplat["vr"], "ops"),
    }
    defaults = {"rpg": 4.5, "ops": 0.72, "era": 4.2, "whip": 1.30,
                "bp_era": 4.0, "bp_whip": 1.28, "ops_vl": 0.72, "ops_vr": 0.72}
    for k, v in lg.items():
        if not v or v <= 0:
            lg[k] = defaults[k]
    return lg


# ---- per-game component estimates -----------------------------------------
def _starter_ra9(sp, lg):
    """RA/9 from a starter: ERA+WHIP, regressed by IP, blended with recent form."""
    if not sp or "season" not in sp or sp["season"]["era"] <= 0:
        return None
    s = sp["season"]
    rel_s = s["ip"] / (s["ip"] + SP_IP_REGRESS) if s["ip"] > 0 else 0.0
    era_eff = rel_s * s["era"] + (1 - rel_s) * lg["era"]
    whip_eff = rel_s * s["whip"] + (1 - rel_s) * lg["whip"]
    r = sp.get("recent")
    if r and r["ip"] > 0:
        rel_r = r["ip"] / (r["ip"] + RECENT_IP_REGRESS)
        recent_era = rel_r * r["era"] + (1 - rel_r) * era_eff
        recent_whip = rel_r * r["whip"] + (1 - rel_r) * whip_eff
        era_eff = (1 - RECENT_WEIGHT) * era_eff + RECENT_WEIGHT * recent_era
        whip_eff = (1 - RECENT_WEIGHT) * whip_eff + RECENT_WEIGHT * recent_whip
    whip_ra9 = lg["era"] * (whip_eff / lg["whip"]) if whip_eff > 0 else era_eff
    return 0.65 * era_eff + 0.35 * whip_ra9


def _bullpen_ra9(team_bp, lg):
    era = team_bp.get("era") or lg["bp_era"]
    whip = team_bp.get("whip") or lg["bp_whip"]
    whip_ra9 = lg["era"] * (whip / lg["whip"]) if whip > 0 else era
    return 0.70 * era + 0.30 * whip_ra9


def _pitching_factor(sp, team_bp, lg):
    sp_ra9 = _starter_ra9(sp, lg)
    bp_ra9 = _bullpen_ra9(team_bp, lg)
    if sp_ra9 is None:
        game_ra9 = bp_ra9
    else:
        game_ra9 = SP_INNINGS_WEIGHT * sp_ra9 + (1 - SP_INNINGS_WEIGHT) * bp_ra9
    return game_ra9 / lg["era"] if lg["era"] else 1.0, sp_ra9, bp_ra9


def _offense_factor(team_hit, ops_vs_hand, opp_hand, lg):
    """Offense relative to league, platoon-adjusted for the starter's hand."""
    off_runs = team_hit["rpg"] / lg["rpg"] if lg["rpg"] else 1.0
    lg_ops_hand = lg["ops_vl"] if opp_hand == "L" else lg["ops_vr"]
    ops_overall = team_hit["ops"]
    # OPS the offense actually faces: starter's hand for his innings, overall for the bullpen.
    ops_eff = SP_INNINGS_WEIGHT * (ops_vs_hand or ops_overall) + (1 - SP_INNINGS_WEIGHT) * ops_overall
    lg_ops_eff = SP_INNINGS_WEIGHT * lg_ops_hand + (1 - SP_INNINGS_WEIGHT) * lg["ops"]
    off_ops = ops_eff / lg_ops_eff if lg_ops_eff else 1.0
    return 0.6 * off_runs + 0.4 * off_ops


# ---- Kalshi price matching ------------------------------------------------
def get_kalshi_prices():
    markets = []; cursor = None
    for _ in range(4):
        url = f"{kalshi.BASE}/markets?series_ticker=KXMLBGAME&status=open&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        data = _get(url)
        markets.extend(data.get("markets", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    by_event = {}
    for m in markets:
        ev = m.get("event_ticker")
        team = (m.get("ticker", "").rsplit("-", 1) + [""])[1]
        if not ev or not team:
            continue
        entry = by_event.setdefault(ev, {"event": ev, "close": kalshi._parse_time(m.get("close_time")), "prices": {}})
        entry["prices"][team] = kalshi._cents(m.get("yes_ask_dollars"))
    out = {}
    for ev, e in by_event.items():
        teams = frozenset(e["prices"].keys())
        if len(teams) >= 2:
            out.setdefault(teams, []).append(e)
    return out


def _match_price(kalshi_index, abbr_map, home_id, away_id, start_epoch):
    home = abbr_map.get(home_id, ""); away = abbr_map.get(away_id, "")
    candidates = kalshi_index.get(frozenset({home, away}))
    if not candidates:
        return None, home, away
    best = min(candidates, key=lambda e: abs((e["close"] or 0) - (start_epoch or 0)))
    return best, home, away


def _boxscore_lineup(game_pk):
    """Posted lineup per side -> {'home': [batter, ...], 'away': [...]}.

    batter = {name, ops, ab, hits, pa} from season stats (ordered by lineup spot).
    """
    def fetch():
        try:
            d = _get(f"{STATS_BASE}/game/{game_pk}/boxscore")
            out = {}
            for side in ("home", "away"):
                t = d["teams"][side]
                batters = []
                for pid in t.get("battingOrder", []):
                    pl = t["players"].get(f"ID{pid}", {})
                    bs = pl.get("seasonStats", {}).get("batting", {})
                    batters.append({
                        "name": pl.get("person", {}).get("fullName", ""),
                        "ops": _f(bs.get("ops")), "ab": _f(bs.get("atBats")),
                        "hits": _f(bs.get("hits")), "pa": _f(bs.get("plateAppearances")),
                        "doubles": _f(bs.get("doubles")), "triples": _f(bs.get("triples")),
                        "hr": _f(bs.get("homeRuns")), "bb": _f(bs.get("baseOnBalls")),
                    })
                out[side] = batters
            return out
        except Exception:
            return None
    return _cached(("box", game_pk), 300, fetch)


def _lineup_factor(batters, team_ops, lg):
    """Offense multiplier from the actual posted lineup vs the team's norm.

    Each hitter's OPS is regressed toward the team OPS by at-bats so a hot/cold
    bench bat doesn't swing it; result is capped to a sane range. Captures
    rested regulars / call-ups / injuries that are already out of the lineup.
    """
    if not batters:
        return None, None
    base = team_ops or lg["ops"]
    regressed = []
    for b in batters:
        ops = b.get("ops") or 0
        if ops <= 0:
            continue
        ab = b.get("ab") or 0
        rel = ab / (ab + 50) if ab > 0 else 0.0
        regressed.append(rel * ops + (1 - rel) * base)
    if not regressed:
        return None, None
    lineup_ops = sum(regressed) / len(regressed)
    factor = lineup_ops / base if base else 1.0
    return max(0.85, min(1.12, factor)), round(lineup_ops, 3)


def _opp_hit_factor(opp_sp, opp_bp, lg):
    """How many hits the opposing pitching tends to allow vs league (1.0 = avg).

    WHIP-based (baserunners), starter weighted for his innings + bullpen for the
    rest, regressed and capped.
    """
    sp_whip = lg["whip"]
    if opp_sp and "season" in opp_sp and opp_sp["season"]["whip"] > 0:
        s = opp_sp["season"]
        rel = s["ip"] / (s["ip"] + SP_IP_REGRESS) if s["ip"] > 0 else 0.0
        sp_whip = rel * s["whip"] + (1 - rel) * lg["whip"]
    bp_whip = opp_bp.get("whip") or lg["whip"]
    whip = SP_INNINGS_WEIGHT * sp_whip + (1 - SP_INNINGS_WEIGHT) * bp_whip
    return max(0.85, min(1.15, whip / lg["whip"] if lg["whip"] else 1.0))


def _live_linescore(game_pk):
    """Current outs + baserunners for a live game (cached briefly)."""
    def fetch():
        try:
            d = _get(f"{STATS_BASE}/game/{game_pk}/linescore")
            off = d.get("offense", {})
            lt = d.get("teams", {})
            return {
                "inning": d.get("currentInning"), "state": d.get("inningState"),
                "outs": d.get("outs") or 0,
                "on1": bool(off.get("first")), "on2": bool(off.get("second")),
                "on3": bool(off.get("third")),
                "home_runs": lt.get("home", {}).get("runs"),
                "away_runs": lt.get("away", {}).get("runs"),
            }
        except Exception:
            return None
    return _cached(("ls", game_pk), 20, fetch)


def _outs_remaining(inning, state, outs, home_runs, away_runs):
    """Outs each team has left to bat in regulation (home bats bottom)."""
    N = inning or 9
    s = (state or "").capitalize()
    full_rem = max(0, 9 - N)            # complete future innings before the 9th
    if s == "Top":                      # away batting now; home bats this inning after
        away_cur, home_cur = 3 - outs, 3
    elif s == "Middle":                 # away done; home upcoming this inning
        away_cur, home_cur = 0, 3
    elif s == "Bottom":                 # home batting now
        away_cur, home_cur = 0, 3 - outs
    else:                               # End of inning
        away_cur, home_cur = 0, 0
    # Home doesn't bat in the bottom of the 9th+ if already ahead.
    if N >= 9 and s in ("Middle", "End") and (home_runs or 0) > (away_runs or 0):
        home_cur = 0
    away_rem = full_rem * 3 + max(0, away_cur)
    home_rem = full_rem * 3 + max(0, home_cur)
    return home_rem, away_rem


def _weather_block(winfo):
    if not winfo or not winfo.get("wx"):
        s = winfo.get("stadium") if winfo else None
        return {"available": False, "roof": s.get("roof") if s else None,
                "stadium": s.get("name") if s else None}
    wx = winfo["wx"]; s = winfo["stadium"]
    out_mph = winfo.get("wind_out_mph", 0)
    wind_desc = "calm"
    if wx.get("wind_mph"):
        if out_mph > 3:
            wind_desc = f"blowing out {out_mph} mph"
        elif out_mph < -3:
            wind_desc = f"blowing in {abs(out_mph)} mph"
        else:
            wind_desc = "crosswind"
    return {
        "available": True, "stadium": s["name"], "roof": s["roof"],
        "temp_f": wx.get("temp_f"), "wind_mph": wx.get("wind_mph"),
        "wind_dir": wx.get("wind_dir"), "wind_effect": wind_desc,
        "precip_pct": wx.get("precip_pct"), "summary": wx.get("summary"),
        "run_factor": round(winfo.get("factor", 1.0), 3), "source": wx.get("source"),
    }


def _schedule(date, season):
    data = _get(f"{STATS_BASE}/schedule?sportId=1&date={date}&hydrate=probablePitcher,linescore")
    dates = data.get("dates", [])
    games = dates[0]["games"] if dates else []
    out = []
    for g in games:
        home = g["teams"]["home"]; away = g["teams"]["away"]
        hp = home.get("probablePitcher"); ap = away.get("probablePitcher")
        st = g.get("status", {})
        ls = g.get("linescore", {})
        state = st.get("abstractGameState", "")  # Preview | Live | Final
        lt = ls.get("teams", {})
        live = {
            "state": state,
            "detailed": st.get("detailedState", ""),
            "is_live": state == "Live",
            "is_final": state == "Final",
            "inning": ls.get("currentInning"),
            "inning_state": ls.get("inningState"),
            "away_runs": lt.get("away", {}).get("runs"),
            "home_runs": lt.get("home", {}).get("runs"),
            "away_hits": lt.get("away", {}).get("hits"),
            "home_hits": lt.get("home", {}).get("hits"),
        }
        out.append({
            "game_pk": g.get("gamePk"),
            "home_id": home["team"]["id"], "home_name": home["team"]["name"],
            "away_id": away["team"]["id"], "away_name": away["team"]["name"],
            "venue_id": g.get("venue", {}).get("id"),
            "home_sp_id": hp.get("id") if hp else None, "home_sp_name": hp.get("fullName") if hp else None,
            "away_sp_id": ap.get("id") if ap else None, "away_sp_name": ap.get("fullName") if ap else None,
            "start": g.get("gameDate"), "start_epoch": kalshi._parse_time(g.get("gameDate")),
            "status": st.get("detailedState", ""), "live": live,
        })
    return out


def analyze_slate(date, season):
    schedule = _schedule(date, season)
    hit = _hitting_map(season); pit = _pitching_map(season)
    bp = _bullpen_map(season); hitplat = _hitting_platoon(season)
    rec = _records_map(season); abbr_map = _abbr_map(season)
    lg = _league_avgs(hit, pit, bp, hitplat)
    try:
        kalshi_index = get_kalshi_prices()
    except Exception:
        kalshi_index = {}

    sp_ids = {g[k] for g in schedule for k in ("home_sp_id", "away_sp_id") if g[k]}
    hand = _handedness(sp_ids)
    sp_stats = {}
    if sp_ids:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for pid, st in zip(sp_ids, ex.map(lambda i: _pitcher_stats(i, season), sp_ids)):
                sp_stats[pid] = st

    # Posted lineups (rest/injuries) and game-time weather, fetched in parallel.
    pks = [g["game_pk"] for g in schedule if g["game_pk"]]
    lineups = {}
    if pks:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for pk, lu in zip(pks, ex.map(_boxscore_lineup, pks)):
                lineups[pk] = lu

    def fetch_weather(g):
        s = stadiums_mod.STADIUMS.get(g["home_id"])
        if not s:
            return None
        wx = weather_mod.get_weather(s["lat"], s["lon"], g["start_epoch"] or _time.time())
        factor, wind_comp = weather_mod.run_factor(wx, s["cf_bearing_deg"], s["roof"])
        return {"stadium": s, "wx": wx, "factor": factor, "wind_out_mph": wind_comp}
    weather_by_pk = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for g, w in zip(schedule, ex.map(fetch_weather, schedule)):
            weather_by_pk[g["game_pk"]] = w

    # Live game state (outs, baserunners, score) for the in-game win model.
    live_pks = [g["game_pk"] for g in schedule if (g.get("live") or {}).get("state") == "Live"]
    linescores = {}
    if live_pks:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for pk, ls in zip(live_pks, ex.map(_live_linescore, live_pks)):
                linescores[pk] = ls

    def th(tid): return hit.get(tid, {"ops": lg["ops"], "rpg": lg["rpg"]})
    def tbp(tid): return bp.get(tid, {"era": lg["bp_era"], "whip": lg["bp_whip"]})
    def tpit(tid): return pit.get(tid, {"era": lg["era"], "whip": lg["whip"]})
    def ops_hand(tid, h):
        m = hitplat["vl"] if h == "L" else hitplat["vr"]
        return m.get(tid, {}).get("ops")

    games = []
    for g in schedule:
        h_sp = sp_stats.get(g["home_sp_id"]); a_sp = sp_stats.get(g["away_sp_id"])
        h_hand = hand.get(g["home_sp_id"], "R"); a_hand = hand.get(g["away_sp_id"], "R")

        off_h = _offense_factor(th(g["home_id"]), ops_hand(g["home_id"], a_hand), a_hand, lg)
        off_a = _offense_factor(th(g["away_id"]), ops_hand(g["away_id"], h_hand), h_hand, lg)
        pit_a_factor, a_sp_ra9, a_bp_ra9 = _pitching_factor(a_sp, tbp(g["away_id"]), lg)
        pit_h_factor, h_sp_ra9, h_bp_ra9 = _pitching_factor(h_sp, tbp(g["home_id"]), lg)

        # Posted-lineup adjustment to each offense (rest days, call-ups, injuries).
        lu = lineups.get(g["game_pk"]) or {}
        lf_home, lops_home = _lineup_factor(lu.get("home"), th(g["home_id"]).get("ops"), lg)
        lf_away, lops_away = _lineup_factor(lu.get("away"), th(g["away_id"]).get("ops"), lg)
        if lf_home:
            off_h *= lf_home
        if lf_away:
            off_a *= lf_away

        er_home = lg["rpg"] * off_h * pit_a_factor * HOME_RUNS_MULT
        er_away = lg["rpg"] * off_a * pit_h_factor
        p_home = er_home ** PYTH_EXP / (er_home ** PYTH_EXP + er_away ** PYTH_EXP)
        p_home = max(0.04, min(0.96, p_home))
        p_away = 1 - p_home

        # Park + weather drive the expected total (over/under), not the moneyline.
        park = PARK_FACTORS.get(g["home_id"], 1.0)
        winfo = weather_by_pk.get(g["game_pk"]) or {}
        wx_factor = winfo.get("factor", 1.0)
        exp_total = round((er_home + er_away) * park * wx_factor, 1)

        # In-game win probability for games in progress: blend the current score
        # and game state with each team's expected remaining runs.
        in_game = None
        ls = linescores.get(g["game_pk"])
        if ls and ls.get("home_runs") is not None and ls.get("away_runs") is not None:
            home_rem, away_rem = _outs_remaining(ls["inning"], ls["state"], ls["outs"],
                                                 ls["home_runs"], ls["away_runs"])
            rate_h, rate_a = er_home / 27.0, er_away / 27.0
            half = (ls["state"] or "").lower()
            base_bonus = 0.35 * ls["on1"] + 0.55 * ls["on2"] + 0.8 * ls["on3"]
            exp_rem_h = home_rem * rate_h + (base_bonus if half == "bottom" else 0)
            exp_rem_a = away_rem * rate_a + (base_bonus if half == "top" else 0)
            p_home = props_mod.in_game_win_prob(ls["home_runs"], ls["away_runs"], exp_rem_h, exp_rem_a)
            p_home = max(0.01, min(0.99, p_home))
            p_away = 1 - p_home
            in_game = {"home_score": ls["home_runs"], "away_score": ls["away_runs"],
                       "inning": ls["inning"], "state": ls["state"], "outs": ls["outs"],
                       "on_base": [b for b, on in (("1B", ls["on1"]), ("2B", ls["on2"]),
                                                   ("3B", ls["on3"])) if on]}

        pick_home = p_home >= p_away
        pick_name = g["home_name"] if pick_home else g["away_name"]
        pick_prob = p_home if pick_home else p_away

        price_entry, home_abbr, away_abbr = _match_price(
            kalshi_index, abbr_map, g["home_id"], g["away_id"], g["start_epoch"])
        edge = market_prob = pick_price = None
        if price_entry:
            pick_abbr = home_abbr if pick_home else away_abbr
            pick_price = price_entry["prices"].get(pick_abbr)
            if pick_price is not None:
                market_prob = round(pick_price, 1)
                edge = round(pick_prob * 100 - pick_price, 1)

        def sp_block(name, st, h):
            if not st or "season" not in st:
                return {"name": name, "hand": h, "era": None, "whip": None, "ip": None,
                        "recent_era": None, "recent_whip": None, "k9": None}
            s = st["season"]; r = st.get("recent") or {}
            return {"name": name, "hand": h, "era": round(s["era"], 2), "whip": round(s["whip"], 2),
                    "ip": s["ip"], "k9": round(s.get("k9", 0), 1),
                    "recent_era": round(r["era"], 2) if r.get("ip") else None,
                    "recent_whip": round(r["whip"], 2) if r.get("ip") else None,
                    "recent_ip": r.get("ip")}

        rh = rec.get(g["home_id"], {}); ra = rec.get(g["away_id"], {})
        bph = tbp(g["home_id"]); bpa = tbp(g["away_id"])

        # Derived props: run line + game totals from the run distribution, and
        # per-batter hit odds from the posted lineups (when available).
        gp = props_mod.game_props(er_home, er_away, home_abbr or g["home_id"], away_abbr or g["away_id"])
        hit_home = hit_away = None
        bat_home = bat_away = None
        ohf_home = _opp_hit_factor(a_sp, tbp(g["away_id"]), lg)  # home bats vs away pitching
        ohf_away = _opp_hit_factor(h_sp, tbp(g["home_id"]), lg)  # away bats vs home pitching
        if lu.get("home"):
            hit_home = props_mod.hit_props(lu["home"], ohf_home)
            bat_home = [props_mod.batter_props(b, i, ohf_home) for i, b in enumerate(lu["home"])]
            bat_home = [x for x in bat_home if x]
        if lu.get("away"):
            hit_away = props_mod.hit_props(lu["away"], ohf_away)
            bat_away = [props_mod.batter_props(b, i, ohf_away) for i, b in enumerate(lu["away"])]
            bat_away = [x for x in bat_away if x]
        # Starter strikeout props.
        ks_home = props_mod.pitcher_k_props((h_sp or {}).get("season", {}).get("k9")) if h_sp else None
        ks_away = props_mod.pitcher_k_props((a_sp or {}).get("season", {}).get("k9")) if a_sp else None
        game_props = {"run_line": gp["run_line"], "totals": gp["totals"],
                      "totals_ladder": gp["totals_ladder"],
                      "model_total": gp["model_total"],
                      "hits_home": hit_home, "hits_away": hit_away,
                      "batters_home": bat_home, "batters_away": bat_away,
                      "ks_home": ks_home, "ks_away": ks_away,
                      "home_sp_name": g.get("home_sp_name"), "away_sp_name": g.get("away_sp_name")}

        games.append({
            "live": g["live"],
            "in_game": in_game,
            "props": game_props,
            "game_pk": g["game_pk"],
            "matchup": f"{g['away_name']} @ {g['home_name']}",
            "away_name": g["away_name"], "home_name": g["home_name"],
            "away_abbr": away_abbr, "home_abbr": home_abbr,
            "start": g["start"], "status": g["status"],
            "p_home": round(p_home, 4), "p_away": round(p_away, 4),
            "exp_runs_home": round(er_home, 2), "exp_runs_away": round(er_away, 2),
            "exp_total": exp_total, "park_factor": park,
            "weather": _weather_block(winfo),
            "home_sp": sp_block(g["home_sp_name"], h_sp, h_hand),
            "away_sp": sp_block(g["away_sp_name"], a_sp, a_hand),
            "home_team": {"ops": round(th(g['home_id']).get("ops", 0), 3),
                          "ops_vs_opp_hand": round(ops_hand(g['home_id'], a_hand) or 0, 3),
                          "rpg": round(th(g['home_id']).get("rpg", 0), 2),
                          "bullpen_era": round(bph.get("era", 0), 2), "bullpen_whip": round(bph.get("whip", 0), 2),
                          "lineup_factor": round(lf_home, 3) if lf_home else None, "lineup_ops": lops_home,
                          "wins": rh.get("wins"), "losses": rh.get("losses"), "run_diff": rh.get("run_diff")},
            "away_team": {"ops": round(th(g['away_id']).get("ops", 0), 3),
                          "ops_vs_opp_hand": round(ops_hand(g['away_id'], h_hand) or 0, 3),
                          "rpg": round(th(g['away_id']).get("rpg", 0), 2),
                          "bullpen_era": round(bpa.get("era", 0), 2), "bullpen_whip": round(bpa.get("whip", 0), 2),
                          "lineup_factor": round(lf_away, 3) if lf_away else None, "lineup_ops": lops_away,
                          "wins": ra.get("wins"), "losses": ra.get("losses"), "run_diff": ra.get("run_diff")},
            "pick": pick_name, "pick_is_home": pick_home,
            "pick_prob": round(pick_prob, 4), "pick_pct": round(pick_prob * 100, 1),
            "confidence": round(abs(pick_prob - 0.5) * 200),
            "pick_price_cents": pick_price, "market_prob": market_prob, "edge_cents": edge,
        })
    games.sort(key=lambda x: x["pick_prob"], reverse=True)
    return games


def _game_state(g):
    return (g.get("live") or {}).get("state") or ""


def _candidate_legs(games, live_only=False):
    """All bettable legs across the slate: moneyline + run line + totals + hits.

    Skips games that are already Final (those results are decided). If
    `live_only` is set, includes only games currently in progress. Each leg is
    tagged with `live`. Combos enforce one leg per game so they stay independent.
    """
    legs = []
    for g in games:
        state = _game_state(g)
        if state == "Final":
            continue  # game's over -- don't put settled games in suggested combos
        if live_only and state != "Live":
            continue
        live = state == "Live"
        pk = g["game_pk"]; mu = g["matchup"]

        def add(typ, label, prob, price=None):
            legs.append({"game_pk": pk, "type": typ, "label": label, "matchup": mu,
                         "prob": prob, "price_cents": price, "live": live})

        if g["pick_prob"] >= 0.5:
            add("ML", f"{g['pick']} to win", g["pick_prob"], g["pick_price_cents"])
        if live:
            continue  # mid-game: props (totals/hits) are stale, so ML only
        p = g.get("props") or {}
        rl = p.get("run_line")
        if rl:
            fb = rl["fav_by2_pct"] / 100.0
            dp = rl["dog_plus15_pct"] / 100.0
            if fb >= 0.55:
                add("Run line", f"{rl['favorite']} −1.5 (win by 2+)", fb)
            elif dp >= 0.62:
                add("Run line", f"{rl['underdog']} +1.5 (lose by ≤1 or win)", dp)
        best_tot = None
        for t in p.get("totals", []):
            over = t["over_pct"] / 100.0; under = t["under_pct"] / 100.0
            side, pr = ("Over", over) if over >= under else ("Under", under)
            if pr >= 0.58 and (best_tot is None or pr > best_tot[1]):
                best_tot = (f"{side} {t['line']} runs", pr)
        if best_tot:
            add("Total", best_tot[0], best_tot[1])
        best_hit = None
        for key in ("hits_away", "hits_home"):
            h = p.get(key)
            if h and h.get("batters"):
                b = h["batters"][0]; pr = b["hit1_pct"] / 100.0
                if pr >= 0.62 and (best_hit is None or pr > best_hit[1]):
                    best_hit = (f"{b['name']} 1+ hit", pr)
        if best_hit:
            add("Hit", best_hit[0], best_hit[1])
        # Expanded props: home runs, total bases (from the exact prop math).
        for key in ("batters_away", "batters_home"):
            for b in (p.get(key) or [])[:6]:
                add("HR", f"{b['name']} 1+ HR", b["hr1"] / 100.0)
                add("Bases", f"{b['name']} 2+ total bases", b["tb2"] / 100.0)
        # Starter strikeout props.
        for key, nm in (("ks_home", p.get("home_sp_name")), ("ks_away", p.get("away_sp_name"))):
            ks = p.get(key)
            if ks and nm:
                for line in (4, 5, 6, 7):
                    if ks.get(line):
                        add("Ks", f"{nm} {line}+ Ks", ks[line] / 100.0)
    return legs


def _combo_item(combo):
    """Build the payload for one combo (list of legs)."""
    prob = 1.0; cost = 1.0; priced = True
    for l in combo:
        prob *= l["prob"]
        if l.get("price_cents"):
            cost *= l["price_cents"] / 100.0
        else:
            priced = False
    item = {
        "legs": [{"pick": l["label"], "matchup": l["matchup"], "type": l.get("type"),
                  "prob_pct": round(l["prob"] * 100, 1), "price_cents": l.get("price_cents"),
                  "live": l.get("live", False)}
                 for l in combo],
        "n_legs": len(combo),
        "any_live": any(l.get("live") for l in combo),
        "combined_prob_pct": round(prob * 100, 1),
        "fair_payout_x": round(1 / prob, 2) if prob > 0 else None,
    }
    if priced and cost > 0:
        payout = 1 / cost
        item["parlay_payout_x"] = round(payout, 2)
        item["parlay_cost_cents"] = round(cost * 100, 1)
        item["ev_pct"] = round((prob * payout - 1) * 100, 1)
    return item


def _assemble(pool, max_legs):
    """Build combos from a leg pool, allowing at most one leg per game."""
    combos = []
    for n in range(2, max_legs + 1):
        for combo in itertools.combinations(pool, n):
            if len({l["game_pk"] for l in combo}) == n:  # one leg per game
                combos.append(_combo_item(combo))
    return combos


def _max_confidence_combo(legs, n):
    """The highest-combined-probability n-leg combo: greedily take the n
    highest-probability legs from distinct games (optimal since all probs < 1)."""
    used = set()
    chosen = []
    for l in sorted(legs, key=lambda x: x["prob"], reverse=True):
        if l["game_pk"] in used:
            continue
        chosen.append(l)
        used.add(l["game_pk"])
        if len(chosen) >= n:
            break
    return _combo_item(chosen) if len(chosen) >= 2 else None


def _final_winners(date):
    """{gamePk: winning_team_name} for Final games on a date."""
    out = {}
    try:
        data = _get(f"{STATS_BASE}/schedule?sportId=1&date={date}&hydrate=linescore")
    except Exception:
        return out
    for d in data.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            lt = g.get("linescore", {}).get("teams", {})
            hr = lt.get("home", {}).get("runs"); ar = lt.get("away", {}).get("runs")
            if hr is None or ar is None:
                continue
            home = g["teams"]["home"]["team"]["name"]
            away = g["teams"]["away"]["team"]["name"]
            out[g.get("gamePk")] = home if hr > ar else away
    return out


def _game_variants(g):
    """Every line variant for a game, so the combo maker can tune for confidence.

    Includes moneyline, run line (±1.5), the full totals ladder (over/under at
    each line), and the top hitters' 1+/2+ hit props. Returns leg dicts with a
    probability so the builder can pick the line nearest a target confidence.
    """
    out = []
    state = _game_state(g)
    if state == "Final":
        return out
    live = state == "Live"
    pk, mu = g["game_pk"], g["matchup"]

    def add(typ, label, prob, price=None):
        if 0.02 <= prob <= 0.995:
            out.append({"game_pk": pk, "type": typ, "label": label, "matchup": mu,
                        "prob": prob, "price_cents": price, "live": live})

    if g.get("pick_prob") is not None:
        add("ML", f"{g['pick']} to win", g["pick_prob"], g.get("pick_price_cents"))
    p = g.get("props") or {}
    rl = p.get("run_line")
    if rl:
        add("Run line", f"{rl['favorite']} −1.5 (win by 2+)", rl["fav_by2_pct"] / 100.0)
        add("Run line", f"{rl['underdog']} +1.5 (lose by ≤1 or win)", rl["dog_plus15_pct"] / 100.0)
    for t in p.get("totals_ladder", []):
        add("Total", f"Over {t['line']} runs", t["over_pct"] / 100.0)
        add("Total", f"Under {t['line']} runs", t["under_pct"] / 100.0)
    for key in ("hits_away", "hits_home"):
        h = p.get(key)
        if h and h.get("batters"):
            for b in h["batters"][:2]:
                add("Hit", f"{b['name']} 1+ hit", b["hit1_pct"] / 100.0)
                add("Hit", f"{b['name']} 2+ hits", b["hit2_pct"] / 100.0)
    return out


def build_target_parlay(games, n_legs, target_pct, target_payout=None, max_legs=12):
    """Build a parlay where each leg is tuned to ~the target confidence.

    For each game we pick the variant that meets the target with the best payout
    (lowest probability still >= target). Then either take n_legs (safest first)
    or, if target_payout is given, add legs until the parlay reaches that payout.
    """
    target = max(0.05, min(0.97, target_pct / 100.0))
    chosen = []  # one best variant per game
    for g in games:
        variants = _game_variants(g)
        if not variants:
            continue
        meeting = [v for v in variants if v["prob"] >= target]
        pick = (min(meeting, key=lambda v: v["prob"]) if meeting
                else max(variants, key=lambda v: v["prob"]))
        pick["meets_target"] = bool(meeting)
        chosen.append(pick)
    if len(chosen) < 2:
        return None
    if target_payout and target_payout > 1:
        # Prefer legs nearest the target (most payout per leg) to reach the
        # payout with fewer ~target-confidence legs.
        chosen.sort(key=lambda v: (not v["meets_target"], v["prob"]))
        sel, prob = [], 1.0
        for v in chosen:
            sel.append(v); prob *= v["prob"]
            if (prob > 0 and 1 / prob >= target_payout) or len(sel) >= max_legs:
                break
        legs = sel if len(sel) >= 2 else chosen[:2]
    else:
        chosen.sort(key=lambda v: (v["meets_target"], v["prob"]), reverse=True)
        legs = chosen[:max(2, min(n_legs, len(chosen)))]
    item = _combo_item(legs)
    item["target_pct"] = round(target * 100, 1)
    item["legs_meeting_target"] = sum(1 for v in legs if v.get("meets_target"))
    if target_payout:
        item["target_payout_x"] = target_payout
        item["payout_reached"] = (item.get("fair_payout_x") or 0) >= target_payout
    return item


def build_same_game_parlays(games, n_legs=3, target_pct=55, target_payout=0,
                            n_sims=5000, max_legs=4, top_n=8):
    """Same-game parlays with honest, correlation-aware joint odds.

    Unlike the cross-game combos (independent games -> exact product), legs from
    one game are correlated, so we simulate each game and read the joint hit-rate
    directly. Each item also reports the naive independent product so you can see
    whether the correlation helps (legs reinforce) or hurts.
    """
    import mlb_sim
    target = max(0.05, min(0.97, target_pct / 100.0))
    out = []
    for g in games:
        if _game_state(g) in ("Final", "Live"):
            continue  # settled games are decided; live in-game props are stale
        sim = mlb_sim.simulate(g, n_sims)
        cands = mlb_sim.build_candidates(g, sim)
        item = mlb_sim.best_same_game(cands, sim["n"], n_legs, target,
                                      target_payout, max_legs)
        if not item:
            continue
        item["matchup"] = g["matchup"]
        item["has_props"] = bool((g.get("props") or {}).get("batters_home")
                                 or (g.get("props") or {}).get("batters_away"))
        out.append(item)
    out.sort(key=lambda x: x["combined_prob_pct"], reverse=True)
    return {"games": out[:top_n], "best": out[0] if out else None,
            "n_sims": n_sims}


def grade_picks():
    """Grade any recorded MLB picks whose games are now final."""
    import store
    import datetime as _dt
    picks = store.ungraded_mlb_picks()
    by_date = {}
    for p in picks:
        by_date.setdefault(p["date"], []).append(p)
    for date, ps in by_date.items():
        results = dict(_final_winners(date))
        # A game can land on the neighbouring calendar date (timezones, late
        # finishes, doubleheaders) — check ±1 day so finished games always grade.
        try:
            d0 = _dt.date.fromisoformat(date)
            for off in (1, -1):
                results.update(_final_winners((d0 + _dt.timedelta(days=off)).isoformat()))
        except Exception:
            pass
        for p in ps:
            winner = results.get(p["game_pk"])
            if winner:
                store.set_mlb_grade(p["game_pk"], 1 if winner == p["pick_name"] else 0, winner)


def build_combos(games, max_legs=3, top_n=6):
    # Only games that haven't finished -- a settled game has no business in a
    # suggested parlay. Upcoming and in-progress games are eligible.
    live_games = [g for g in games if _game_state(g) != "Final"]

    # Moneyline-only combos drive the EV-based highlights (those legs are priced).
    ml_legs = [{"game_pk": g["game_pk"], "type": "ML", "label": f"{g['pick']} to win",
                "matchup": g["matchup"], "prob": g["pick_prob"],
                "price_cents": g["pick_price_cents"], "live": _game_state(g) == "Live"}
               for g in live_games if g["pick_prob"] >= 0.5][:top_n]
    ml_combos = _assemble(ml_legs, max_legs)
    safest = max(ml_combos, key=lambda c: c["combined_prob_pct"], default=None)
    priced = [c for c in ml_combos if c.get("ev_pct") is not None]
    best_value = max(priced, key=lambda c: c["ev_pct"], default=None)

    # Mixed combos draw from every bet type (moneyline + props).
    all_legs = _candidate_legs(live_games)
    legs = sorted(all_legs, key=lambda l: l["prob"], reverse=True)[:10]
    mixed = _assemble(legs, max_legs)
    mixed.sort(key=lambda c: c["combined_prob_pct"], reverse=True)

    # Combo maker: the single highest-confidence parlay for each leg count.
    max_games = len({l["game_pk"] for l in all_legs})
    by_size = {}
    for n in range(2, min(8, max_games) + 1):
        item = _max_confidence_combo(all_legs, n)
        if item:
            by_size[str(n)] = item

    # Live-only combos: parlays built purely from games currently in progress.
    live_legs = sorted(_candidate_legs(games, live_only=True),
                       key=lambda l: l["prob"], reverse=True)[:10]
    live_combos = _assemble(live_legs, max_legs)
    live_combos.sort(key=lambda c: c["combined_prob_pct"], reverse=True)

    return {"safest": safest, "best_value": best_value,
            "all": sorted(ml_combos, key=lambda c: c["combined_prob_pct"], reverse=True)[:12],
            "mixed": mixed[:12], "live": live_combos[:8],
            "by_size": by_size, "max_legs_available": max_games}
