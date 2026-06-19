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
                rec = {"era": _f(st.get("era")), "whip": _f(st.get("whip")), "ip": _f(st.get("inningsPitched"))}
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


def _schedule(date, season):
    data = _get(f"{STATS_BASE}/schedule?sportId=1&date={date}&hydrate=probablePitcher")
    dates = data.get("dates", [])
    games = dates[0]["games"] if dates else []
    out = []
    for g in games:
        home = g["teams"]["home"]; away = g["teams"]["away"]
        hp = home.get("probablePitcher"); ap = away.get("probablePitcher")
        out.append({
            "game_pk": g.get("gamePk"),
            "home_id": home["team"]["id"], "home_name": home["team"]["name"],
            "away_id": away["team"]["id"], "away_name": away["team"]["name"],
            "venue_id": g.get("venue", {}).get("id"),
            "home_sp_id": hp.get("id") if hp else None, "home_sp_name": hp.get("fullName") if hp else None,
            "away_sp_id": ap.get("id") if ap else None, "away_sp_name": ap.get("fullName") if ap else None,
            "start": g.get("gameDate"), "start_epoch": kalshi._parse_time(g.get("gameDate")),
            "status": g.get("status", {}).get("detailedState", ""),
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

        er_home = lg["rpg"] * off_h * pit_a_factor * HOME_RUNS_MULT
        er_away = lg["rpg"] * off_a * pit_h_factor
        p_home = er_home ** PYTH_EXP / (er_home ** PYTH_EXP + er_away ** PYTH_EXP)
        p_home = max(0.04, min(0.96, p_home))
        p_away = 1 - p_home

        park = PARK_FACTORS.get(g["home_id"], 1.0)
        exp_total = round((er_home + er_away) * park, 1)

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
                        "recent_era": None, "recent_whip": None}
            s = st["season"]; r = st.get("recent") or {}
            return {"name": name, "hand": h, "era": round(s["era"], 2), "whip": round(s["whip"], 2),
                    "ip": s["ip"],
                    "recent_era": round(r["era"], 2) if r.get("ip") else None,
                    "recent_whip": round(r["whip"], 2) if r.get("ip") else None,
                    "recent_ip": r.get("ip")}

        rh = rec.get(g["home_id"], {}); ra = rec.get(g["away_id"], {})
        bph = tbp(g["home_id"]); bpa = tbp(g["away_id"])
        games.append({
            "game_pk": g["game_pk"],
            "matchup": f"{g['away_name']} @ {g['home_name']}",
            "away_name": g["away_name"], "home_name": g["home_name"],
            "away_abbr": away_abbr, "home_abbr": home_abbr,
            "start": g["start"], "status": g["status"],
            "p_home": round(p_home, 4), "p_away": round(p_away, 4),
            "exp_runs_home": round(er_home, 2), "exp_runs_away": round(er_away, 2),
            "exp_total": exp_total, "park_factor": park,
            "home_sp": sp_block(g["home_sp_name"], h_sp, h_hand),
            "away_sp": sp_block(g["away_sp_name"], a_sp, a_hand),
            "home_team": {"ops": round(th(g['home_id']).get("ops", 0), 3),
                          "ops_vs_opp_hand": round(ops_hand(g['home_id'], a_hand) or 0, 3),
                          "rpg": round(th(g['home_id']).get("rpg", 0), 2),
                          "bullpen_era": round(bph.get("era", 0), 2), "bullpen_whip": round(bph.get("whip", 0), 2),
                          "wins": rh.get("wins"), "losses": rh.get("losses"), "run_diff": rh.get("run_diff")},
            "away_team": {"ops": round(th(g['away_id']).get("ops", 0), 3),
                          "ops_vs_opp_hand": round(ops_hand(g['away_id'], h_hand) or 0, 3),
                          "rpg": round(th(g['away_id']).get("rpg", 0), 2),
                          "bullpen_era": round(bpa.get("era", 0), 2), "bullpen_whip": round(bpa.get("whip", 0), 2),
                          "wins": ra.get("wins"), "losses": ra.get("losses"), "run_diff": ra.get("run_diff")},
            "pick": pick_name, "pick_is_home": pick_home,
            "pick_prob": round(pick_prob, 4), "pick_pct": round(pick_prob * 100, 1),
            "confidence": round(abs(pick_prob - 0.5) * 200),
            "pick_price_cents": pick_price, "market_prob": market_prob, "edge_cents": edge,
        })
    games.sort(key=lambda x: x["pick_prob"], reverse=True)
    return games


def build_combos(games, max_legs=3, top_n=6):
    pool = [g for g in games if g["pick_prob"] >= 0.5][:top_n]
    combos = []
    for legs in range(2, max_legs + 1):
        for combo in itertools.combinations(pool, legs):
            prob = 1.0; cost = 1.0; priced = True
            for g in combo:
                prob *= g["pick_prob"]
                if g["pick_price_cents"]:
                    cost *= g["pick_price_cents"] / 100.0
                else:
                    priced = False
            item = {
                "legs": [{"pick": g["pick"], "matchup": g["matchup"],
                          "prob_pct": g["pick_pct"], "price_cents": g["pick_price_cents"]} for g in combo],
                "n_legs": legs,
                "combined_prob_pct": round(prob * 100, 1),
                "fair_payout_x": round(1 / prob, 2) if prob > 0 else None,
            }
            if priced and cost > 0:
                payout = 1 / cost
                item["parlay_payout_x"] = round(payout, 2)
                item["parlay_cost_cents"] = round(cost * 100, 1)
                item["ev_pct"] = round((prob * payout - 1) * 100, 1)
            combos.append(item)
    safest = max(combos, key=lambda c: c["combined_prob_pct"], default=None)
    priced = [c for c in combos if c.get("ev_pct") is not None]
    best_value = max(priced, key=lambda c: c["ev_pct"], default=None)
    return {"safest": safest, "best_value": best_value,
            "all": sorted(combos, key=lambda c: c["combined_prob_pct"], reverse=True)[:12]}
