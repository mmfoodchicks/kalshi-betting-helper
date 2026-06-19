"""Baseball (MLB) betting insights + parlay combo generator.

Data sources (all public, no key):
  - MLB Stats API (statsapi.mlb.com): schedule + probable pitchers, team
    hitting (AVG/OBP/SLG/OPS, runs), team pitching (ERA/WHIP), per-pitcher
    season stats (ERA/WHIP/IP), and standings (W-L).
  - Kalshi KXMLBGAME markets: live moneyline-style prices per team.

Model (expected runs -> win probability)
----------------------------------------
For each game we estimate how many runs each side should score, then convert to
a win probability.

  OFFENSE (relative to league):   blend of the team's runs/game and OPS.
  PITCHING the offense faces:      a weighted mix of
       - the opposing STARTER (ERA + WHIP, regressed toward league by innings,
         since small-sample ERAs are noisy), throwing ~60% of the game, and
       - the opposing BULLPEN/STAFF (team ERA + WHIP) for the rest.
  EXPECTED RUNS (odds-ratio):  ExpRuns = lgRPG * Offense * OppPitching
  HOME FIELD:  home expected runs nudged up a touch.
  WIN PROB (Pythagorean on this game's expected runs):
       p_home = ExpRuns_home^1.83 / (ExpRuns_home^1.83 + ExpRuns_away^1.83)

Picks feed the parlay/combo generator (probabilities multiplied per leg).
"""

import itertools
from concurrent.futures import ThreadPoolExecutor

import kalshi  # reuse BASE + _get_json + _parse_time + _cents helpers

STATS_BASE = "https://statsapi.mlb.com/api/v1"

PYTH_EXP = 1.83          # Pythagorean exponent for run -> win conversion
SP_INNINGS_WEIGHT = 0.60  # share of the game the starter is responsible for
HOME_RUNS_MULT = 1.08     # home-field edge applied to home expected runs (~54%)
SP_IP_REGRESS = 50.0      # innings constant for regressing a starter's ERA

# ---- tiny TTL cache -------------------------------------------------------
import time as _time
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


# ---- team-level season stats (one call each, cached) ----------------------
def _hitting_map(season):
    def fetch():
        data = _get(f"{STATS_BASE}/teams/stats?sportId=1&season={season}&group=hitting&stats=season")
        out = {}
        for s in data["stats"][0]["splits"]:
            st = s["stat"]
            g = _f(st.get("gamesPlayed"), 1) or 1
            out[s["team"]["id"]] = {
                "ops": _f(st.get("ops")), "avg": _f(st.get("avg")),
                "obp": _f(st.get("obp")), "slg": _f(st.get("slg")),
                "runs": int(_f(st.get("runs"))), "g": int(g),
                "rpg": _f(st.get("runs")) / g,
            }
        return out
    return _cached(("hit", season), 600, fetch)


def _pitching_map(season):
    def fetch():
        data = _get(f"{STATS_BASE}/teams/stats?sportId=1&season={season}&group=pitching&stats=season")
        out = {}
        for s in data["stats"][0]["splits"]:
            st = s["stat"]
            out[s["team"]["id"]] = {
                "era": _f(st.get("era")), "whip": _f(st.get("whip")),
                "runs_allowed": int(_f(st.get("runs"))),
            }
        return out
    return _cached(("pit", season), 600, fetch)


def _records_map(season):
    def fetch():
        data = _get(f"{STATS_BASE}/standings?leagueId=103,104&season={season}")
        out = {}
        for rec in data.get("records", []):
            for t in rec.get("teamRecords", []):
                out[t["team"]["id"]] = {
                    "wins": int(t.get("wins", 0)), "losses": int(t.get("losses", 0)),
                    "run_diff": int(t.get("runDifferential", 0)),
                }
        return out
    return _cached(("rec", season), 600, fetch)


def _abbr_map(season):
    def fetch():
        data = _get(f"{STATS_BASE}/teams?sportId=1&season={season}")
        return {t["id"]: t.get("abbreviation", "") for t in data.get("teams", [])}
    return _cached(("abbr", season), 86400, fetch)


def _pitcher_stats(pid, season):
    if not pid:
        return None
    def fetch():
        try:
            d = _get(f"{STATS_BASE}/people/{pid}/stats?stats=season&group=pitching&season={season}")
            st = d["stats"][0]["splits"][0]["stat"]
            return {
                "era": _f(st.get("era")), "whip": _f(st.get("whip")),
                "ip": _f(st.get("inningsPitched")), "k9": _f(st.get("strikeoutsPer9Inn")),
            }
        except Exception:
            return None
    return _cached(("sp", pid, season), 600, fetch)


def _league_avgs(hit, pit):
    n_h = len(hit) or 1
    n_p = len(pit) or 1
    lg = {
        "rpg": sum(t["rpg"] for t in hit.values()) / n_h,
        "ops": sum(t["ops"] for t in hit.values()) / n_h,
        "era": sum(t["era"] for t in pit.values()) / n_p,
        "whip": sum(t["whip"] for t in pit.values()) / n_p,
    }
    # guard against zeros
    for k, v in lg.items():
        if v <= 0:
            lg[k] = {"rpg": 4.5, "ops": 0.72, "era": 4.2, "whip": 1.30}[k]
    return lg


# ---- per-team run-prevention estimate for one game ------------------------
def _starter_ra9(sp, lg):
    """Run-prevention (RA/9) from a starter, using ERA + WHIP, regressed by IP."""
    if not sp or sp["era"] <= 0:
        return None
    ip = sp["ip"]
    rel = ip / (ip + SP_IP_REGRESS) if ip > 0 else 0.0
    era_eff = rel * sp["era"] + (1 - rel) * lg["era"]
    whip_ra9 = lg["era"] * (sp["whip"] / lg["whip"]) if sp["whip"] > 0 else era_eff
    return 0.65 * era_eff + 0.35 * whip_ra9


def _staff_ra9(team_pit, lg):
    """Bullpen/overall staff run-prevention from team ERA + WHIP."""
    era = team_pit["era"] or lg["era"]
    whip_ra9 = lg["era"] * (team_pit["whip"] / lg["whip"]) if team_pit["whip"] > 0 else era
    return 0.70 * era + 0.30 * whip_ra9


def _offense_factor(team_hit, lg):
    """Offense relative to league (>1 = better), blending runs/game and OPS."""
    off_runs = team_hit["rpg"] / lg["rpg"] if lg["rpg"] else 1.0
    off_ops = team_hit["ops"] / lg["ops"] if lg["ops"] else 1.0
    return 0.6 * off_runs + 0.4 * off_ops


def _pitching_factor(sp, team_pit, lg):
    """Opponent run-prevention for this game relative to league (>1 = worse D)."""
    sp_ra9 = _starter_ra9(sp, lg)
    staff_ra9 = _staff_ra9(team_pit, lg)
    if sp_ra9 is None:
        game_ra9 = staff_ra9                       # no probable starter listed
    else:
        game_ra9 = SP_INNINGS_WEIGHT * sp_ra9 + (1 - SP_INNINGS_WEIGHT) * staff_ra9
    return game_ra9 / lg["era"] if lg["era"] else 1.0, sp_ra9, staff_ra9


# ---- Kalshi price matching (unchanged shape) ------------------------------
def get_kalshi_prices():
    markets = []
    cursor = None
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
    home = abbr_map.get(home_id, "")
    away = abbr_map.get(away_id, "")
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
            "home_sp_id": hp.get("id") if hp else None, "home_sp_name": hp.get("fullName") if hp else None,
            "away_sp_id": ap.get("id") if ap else None, "away_sp_name": ap.get("fullName") if ap else None,
            "start": g.get("gameDate"), "start_epoch": kalshi._parse_time(g.get("gameDate")),
            "status": g.get("status", {}).get("detailedState", ""),
        })
    return out


def analyze_slate(date, season):
    schedule = _schedule(date, season)
    hit = _hitting_map(season)
    pit = _pitching_map(season)
    rec = _records_map(season)
    abbr_map = _abbr_map(season)
    lg = _league_avgs(hit, pit)
    try:
        kalshi_index = get_kalshi_prices()
    except Exception:
        kalshi_index = {}

    # Fetch all probable-starter stats in parallel (cached across reloads).
    sp_ids = {g[k] for g in schedule for k in ("home_sp_id", "away_sp_id") if g[k]}
    sp_stats = {}
    if sp_ids:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for pid, st in zip(sp_ids, ex.map(lambda i: _pitcher_stats(i, season), sp_ids)):
                sp_stats[pid] = st

    def team_hit(tid): return hit.get(tid, {"ops": lg["ops"], "rpg": lg["rpg"]})
    def team_pit(tid): return pit.get(tid, {"era": lg["era"], "whip": lg["whip"]})

    games = []
    for g in schedule:
        h_sp = sp_stats.get(g["home_sp_id"])
        a_sp = sp_stats.get(g["away_sp_id"])

        off_h = _offense_factor(team_hit(g["home_id"]), lg)
        off_a = _offense_factor(team_hit(g["away_id"]), lg)
        pit_a_factor, a_sp_ra9, a_staff = _pitching_factor(a_sp, team_pit(g["away_id"]), lg)
        pit_h_factor, h_sp_ra9, h_staff = _pitching_factor(h_sp, team_pit(g["home_id"]), lg)

        # Home offense faces away pitching, and vice-versa.
        er_home = lg["rpg"] * off_h * pit_a_factor * HOME_RUNS_MULT
        er_away = lg["rpg"] * off_a * pit_h_factor
        p_home = er_home ** PYTH_EXP / (er_home ** PYTH_EXP + er_away ** PYTH_EXP)
        p_home = max(0.04, min(0.96, p_home))
        p_away = 1 - p_home

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

        def sp_block(name, st):
            if not st:
                return {"name": name, "era": None, "whip": None, "ip": None}
            return {"name": name, "era": round(st["era"], 2), "whip": round(st["whip"], 2),
                    "ip": st["ip"], "k9": round(st.get("k9", 0), 1)}

        rh = rec.get(g["home_id"], {}); ra = rec.get(g["away_id"], {})
        th = team_hit(g["home_id"]); ta = team_hit(g["away_id"])
        tph = team_pit(g["home_id"]); tpa = team_pit(g["away_id"])

        games.append({
            "game_pk": g["game_pk"],
            "matchup": f"{g['away_name']} @ {g['home_name']}",
            "away_name": g["away_name"], "home_name": g["home_name"],
            "away_abbr": away_abbr, "home_abbr": home_abbr,
            "start": g["start"], "status": g["status"],
            "p_home": round(p_home, 4), "p_away": round(p_away, 4),
            "exp_runs_home": round(er_home, 2), "exp_runs_away": round(er_away, 2),
            "home_sp": sp_block(g["home_sp_name"], h_sp),
            "away_sp": sp_block(g["away_sp_name"], a_sp),
            "home_team": {"ops": round(th.get("ops", 0), 3), "rpg": round(th.get("rpg", 0), 2),
                          "team_era": round(tph.get("era", 0), 2), "team_whip": round(tph.get("whip", 0), 2),
                          "wins": rh.get("wins"), "losses": rh.get("losses"), "run_diff": rh.get("run_diff")},
            "away_team": {"ops": round(ta.get("ops", 0), 3), "rpg": round(ta.get("rpg", 0), 2),
                          "team_era": round(tpa.get("era", 0), 2), "team_whip": round(tpa.get("whip", 0), 2),
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
