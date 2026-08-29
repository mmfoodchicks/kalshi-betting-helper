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
import re as _re
import os as _os
import time as _time
from datetime import datetime as _dt, timedelta as _td
from concurrent.futures import ThreadPoolExecutor

import kalshi  # reuse BASE + _get_json + _parse_time + _cents helpers
import weather as weather_mod
import stadiums as stadiums_mod
import props as props_mod
import mlb_form
import errlog

STATS_BASE = "https://statsapi.mlb.com/api/v1"

PYTH_EXP = 1.83
# Home-plate umpire zone -> run-environment multiplier, and the share of a
# game's strikeout change that lands on the STARTER.
#
# Both effects are sized from the regression ump_build runs when it builds the
# tendency table (meta.r_per_bias / meta.k_per_bias: whole-game runs and Ks per
# unit of bias, measured across a season of finished games), so these are
# fallbacks for a board running before the first table exists. The multiplier is
# 1 - bias * r_per_bias / lg_runs; the 0.72 here is that quantity at the measured
# 2026 values, replacing a hand-set 1.0 that overstated it by ~40%.
_UMP_RUN = 0.72
SP_INNINGS_WEIGHT = 0.60   # share of game the starter is responsible for
# A starter throws SP_INNINGS_WEIGHT of his side's innings, and a game's K change
# is split across the two staffs, so one starter's share of it is w/2.
_UMP_K_STARTER_SHARE = SP_INNINGS_WEIGHT / 2.0

# Home-field edge, expressed as the RATIO of home to away expected runs. 1.08 is
# reverse-engineered from the win rate: through PYTH_EXP it yields ~53.5% home
# wins, against a real 52.89% over 7,145 games (2023-25). As a ratio it is doing
# its job -- the moneyline is close to unbiased against the market (median +0.6pp).
#
# But it used to be APPLIED one-sided, home x1.08 and away x1.00, which lifts every
# game TOTAL by (1.08+1.00)/2 = 1.040. That was 4 of the ~8.8% by which this
# model's expected totals ran hot, and it is why the sim sat above the market's
# implied Over in 10 of 10 games.
#
# It is applied geometrically now: home x sqrt(1.08), away / sqrt(1.08). The ratio
# is preserved exactly, so every moneyline is unchanged to the last decimal, while
# the product -- and therefore the total -- is neutral.
#
# Worth recording WHY a one-sided run bump was the wrong shape in the first place.
# Home-field advantage in baseball is not a run-scoring advantage. Over the same
# 7,145 games home teams scored 4.421 rpg against away 4.401, a ratio of 1.0002 --
# no scoring edge at all -- and yet won 52.89%. Pythagoras on the true run ratio
# predicts 50.01%. The edge lives in the structure of the game, not in run
# production: the home team bats last, and stops batting the moment it is ahead in
# the ninth, which truncates its run total while preserving its win. So a run
# multiplier can reproduce the right WIN rate or the right TOTAL, never both. The
# geometric split lets the ratio carry the win rate and keeps the level honest.
HOME_RUNS_MULT = 1.08      # home:away expected-run RATIO (not a one-sided level)
_HOME_SPLIT = HOME_RUNS_MULT ** 0.5      # applied to home; away gets its inverse
SP_IP_REGRESS = 50.0       # innings constant for regressing a starter's season ERA
RECENT_IP_REGRESS = 25.0   # innings constant for the recent-form blend
RECENT_WEIGHT = 0.25       # how much recent form pulls the season number

# Multiplicative run park factors by home team id (~1.0 = neutral).
# FALLBACK ONLY: the live numbers come from Statcast's measured 3-year rolling
# park factors (savant.park_factors -- runs AND homers per park); this static
# table serves when Savant is unreachable. It is kept deliberately, but know
# its history: these were eyeballed "directionally-standard" values, and the
# measurement caught them badly wrong in places -- Dodger Stadium sat at 0.97
# (run-suppressing) against a measured 102 runs / 127 HR, and Coors at 1.15
# against a measured 1.25.
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


def _park_factor(home_id, season):
    """Measured run park factor (Statcast 3-yr rolling), static table fallback."""
    try:
        import savant
        pf = (savant.park_factors(season) or {}).get(home_id)
        if pf and pf.get("runs"):
            # Clamp is a sanity rail, wide enough for the real extremes:
            # T-Mobile measures 0.83, Coors 1.25.
            return max(0.80, min(1.30, pf["runs"]))
    except Exception as e:
        errlog.note("MLB-park-factor", e)
    return PARK_FACTORS.get(home_id, 1.0)


def _park_hr_ratio(home_id, season):
    """How much MORE (or less) a park boosts homers than its run factor alone
    implies -- hr_factor / run_factor. Yankee Stadium's short porch and Dodger
    Stadium's carry are HR parks beyond their run levels; Oracle is the
    opposite. Feeds the HR ladders on top of the run environment. 1.0 when
    Savant is unreachable (the static table has no HR dimension)."""
    try:
        import savant
        pf = (savant.park_factors(season) or {}).get(home_id)
        if pf and pf.get("runs") and pf.get("hr"):
            return max(0.80, min(1.30, pf["hr"] / pf["runs"]))
    except Exception as e:
        errlog.note("MLB-park-hr-ratio", e)
    return 1.0

# ---- tiny TTL cache -------------------------------------------------------
_cache = {}
# Expired entries used to sit here forever: the TTL was only ever checked on
# READ, so a key never asked for again was never removed. Keys like
# ("bvp", batter_id, pitcher_id) are per batter-pitcher PAIR, so a long-running
# instance accumulated thousands of dead entries it would never look at again.
# Sweeping on insert keeps the cache proportional to what is actually live.
#
# Every-N-puts alone is calibrated for thousands of TINY entries, and that
# calibration inverts for the live game sims: a serving worker does a handful
# of puts a minute, each live entry is a multi-MB simulation keyed by a
# situation that never comes back, and 500 puts between sweeps is hours --
# measured against a 2 GB instance, an evening of live slates was an
# "exceeded memory" kill. The time floor makes big-value/low-rate patterns
# safe: any put more than _CACHE_SWEEP_S after the last sweep sweeps.
_CACHE_SWEEP_EVERY = 500
_CACHE_SWEEP_S = 120
_cache_puts = 0
_cache_swept = 0.0


def _sweep_cache(now):
    """Drop entries whose own TTL has expired.

    Snapshot with list() before iterating: this cache is written from the worker
    threads every slate build fans out into, and walking the live dict races any
    thread inserting a fresh entry ("dictionary changed size during iteration").
    Rare by construction -- the sweep runs on one put in five hundred -- which is
    exactly what makes it the kind of bug that reads as a fluke in a log."""
    for k, (ts, _v, ttl) in list(_cache.items()):
        if now - ts >= ttl:
            _cache.pop(k, None)


def _cached(key, ttl, producer):
    global _cache_puts, _cache_swept
    now = _time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < hit[2]:
        return hit[1]
    val = producer()
    _cache[key] = (now, val, ttl)
    _cache_puts += 1
    if _cache_puts % _CACHE_SWEEP_EVERY == 0 or now - _cache_swept > _CACHE_SWEEP_S:
        _cache_swept = now
        _sweep_cache(now)
    return val


def _peek(key):
    """A cache READ that never builds. For work worth showing when it happens to
    be free and not worth paying for on its own — `_cached` would run the
    producer, which is exactly what the caller is trying to avoid."""
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < hit[2]:
        return hit[1]
    return None


def _get(url):
    return kalshi._get_json(url)


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _ip_float(v, default=0.0):
    """MLB innings-pitched strings are base-3 after the point ('45.1' = 45 and
    ONE THIRD innings, not 45.1). Convert to true decimal innings."""
    try:
        s = str(v)
        whole, _, frac = s.partition(".")
        return float(whole) + (int(frac or 0) / 3.0)
    except (TypeError, ValueError):
        return default


_HIT_EVENTS = {"Single", "Double", "Triple", "Home Run"}


def _slate_game(game_pk):
    """Our analyzed model for one game (props + SP) by game_pk, best-effort."""
    try:
        feed_date = _get(f"{STATS_BASE}/schedule?sportId=1&gamePk={game_pk}"
                         "&fields=dates,games,gamePk,officialDate,season")
        date = feed_date["dates"][0]["games"][0]["officialDate"]
        for g in analyze_slate(date, date[:4]):
            if g.get("game_pk") == game_pk:
                return g
    except Exception as _e:
        errlog.note("BB-slate_game", _e)
    return None


def _batter_model(slate, side, name):
    """Match a live hitter to our model props -> (full-game hit%, P(2nd|1st)%,
    per-PA hit rate, expected PA)."""
    if not slate:
        return None
    key = "batters_home" if side == "home" else "batters_away"
    nm = _norm(name)
    for b in (slate.get("props") or {}).get(key) or []:
        if _norm(b.get("name", "")) == nm:
            h1 = b.get("hit1"); h2 = b.get("hit2")
            r = (b.get("r1") or 0) + (b.get("r2") or 0) + (b.get("r3") or 0) + (b.get("rhr") or 0)
            return {"hit1": h1, "second_given_first": round(h2 / h1 * 100, 1) if h1 else None,
                    "r_hit": r, "exp_pa": b.get("pa") or 4.2}
    return None


def _norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum() or c == " ").strip()


def _bvp(batter_id, pitcher_id):
    """Career batter-vs-pitcher line (small-sample, informational). Cached 12h."""
    if not batter_id or not pitcher_id:
        return None
    def fetch():
        try:
            d = _get(f"{STATS_BASE}/people/{batter_id}/stats?stats=vsPlayerTotal"
                     f"&opposingPlayerId={pitcher_id}&group=hitting&sportId=1")
        except Exception:
            return None
        for s in d.get("stats", []):
            for sp in s.get("splits", []):
                st = sp.get("stat", {})
                ab = int(st.get("atBats", 0) or 0)
                pa = ab + int(st.get("baseOnBalls", 0) or 0) + int(st.get("hitByPitch", 0) or 0)
                if pa <= 0:
                    return None
                return {"ab": ab, "pa": pa, "h": int(st.get("hits", 0) or 0),
                        "hr": int(st.get("homeRuns", 0) or 0), "so": int(st.get("strikeOuts", 0) or 0),
                        "bb": int(st.get("baseOnBalls", 0) or 0),
                        "avg": st.get("avg"), "ops": st.get("ops")}
        return None
    return _cached(("bvp", batter_id, pitcher_id), 12 * 3600, fetch)


def live_game_feedback(game_pk):
    """Rich live-game feed: pitcher live lines (pitch count, K/BB/H/IP + season
    ERA/WHIP and our K projection), and per-hitter AB-by-AB results with our
    model's hit odds + the conditional odds of a 2nd hit, plus live remaining-AB
    hit odds. Merges MLB's live feed with our slate model."""
    feed = _get(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live")
    ld = feed["liveData"]; gd = feed["gameData"]
    box = ld["boxscore"]["teams"]
    ls = ld.get("linescore", {})
    names = {s: gd["teams"][s]["clubName"] for s in ("home", "away")}
    slate = _slate_game(game_pk)
    sp_model = {}
    if slate:
        for s, k in (("home", "home_sp"), ("away", "away_sp")):
            blk = slate.get(k) or {}
            if blk.get("k9"):
                sp_model[s] = blk

    # AB-by-AB log per batter id.
    ablog = {}
    for pl in ld.get("plays", {}).get("allPlays", []):
        res = pl.get("result", {})
        ev = res.get("event")
        bid = (pl.get("matchup") or {}).get("batter", {}).get("id")
        if ev and bid:
            ablog.setdefault(bid, []).append({"event": ev, "hit": ev in _HIT_EVENTS,
                                              "rbi": res.get("rbi", 0)})

    # Each side's starting pitcher — the arm the opposing lineup's batter-vs-pitcher
    # history is most meaningful against.
    starter = {}
    for side in ("away", "home"):
        for p in box[side]["players"].values():
            if p.get("stats", {}).get("pitching", {}).get("gamesStarted"):
                starter[side] = {"id": p["person"]["id"], "name": p["person"]["fullName"]}
                break

    # Our deep pitch-by-pitch sim's batter-vs-starter read (large sample, model),
    # keyed by MLB player id so it matches the box directly. Non-blocking.
    sim_bvp = {}
    try:
        import mlb_dfs
        season = str((gd.get("game") or {}).get("season") or "")
        if slate and season:
            sim_bvp = mlb_dfs.game_sim_bvp(slate, season) or {}
    except Exception:
        sim_bvp = {}

    pitchers, hitters = [], []
    bvp_tasks = []                          # (hitter_dict, batter_id, opp_starter_id)
    for side in ("away", "home"):
        players = box[side]["players"]
        opp = "home" if side == "away" else "away"
        opp_sp = starter.get(opp)
        for p in players.values():
            person = p["person"]; nm = person["fullName"]
            ps = p.get("stats", {}).get("pitching", {})
            ss = p.get("seasonStats", {}).get("pitching", {})
            if ps.get("numberOfPitches"):
                k_now = int(ps.get("strikeOuts", 0))
                is_starter = bool(ps.get("gamesStarted"))
                # Our K projection is the STARTER's — don't show it against relievers.
                smod = sp_model.get(side, {}) if is_starter else {}
                model_k = smod.get("k9") if is_starter else None
                pitchers.append({
                    "name": nm, "side": side, "team": names[side],
                    "pitches": int(ps.get("numberOfPitches", 0)),
                    "ip": ps.get("inningsPitched"), "k": k_now,
                    "bb": int(ps.get("baseOnBalls", 0)), "h": int(ps.get("hits", 0)),
                    "er": int(ps.get("earnedRuns", 0)),
                    "season_era": ss.get("era"), "season_whip": ss.get("whip"),
                    "model_k9": model_k, "starter": is_starter,
                    "est_ip": smod.get("est_ip"), "est_pitches": smod.get("est_pitches"),
                })
            order = p.get("battingOrder")
            if order:
                bs = p.get("stats", {}).get("batting", {})
                bss = p.get("seasonStats", {}).get("batting", {})
                log = ablog.get(person["id"], [])
                h_now = int(bs.get("hits", 0)); ab_now = int(bs.get("atBats", 0))
                m = _batter_model(slate, side, nm)
                next_hit = None
                if m and m["r_hit"]:
                    rem = max(0, round(m["exp_pa"]) - len(log))
                    next_hit = round(100 * (1 - (1 - m["r_hit"]) ** rem), 1) if rem else 0.0
                hd = {
                    "name": nm, "side": side, "team": names[side], "order": int(order) // 100,
                    "ab_log": [x["event"] for x in log],
                    "hits": h_now, "ab": ab_now, "avg": bss.get("avg"),
                    "model_hit_pct": m["hit1"] if m else None,
                    "second_given_first": m["second_given_first"] if m else None,
                    "live_next_hit_pct": next_hit,
                    "vs_pitcher": opp_sp["name"] if opp_sp else None, "bvp": None,
                    "sim_bvp": sim_bvp.get(person["id"]),
                }
                hitters.append(hd)
                if opp_sp:
                    bvp_tasks.append((hd, person["id"], opp_sp["id"]))

    # Batter-vs-pitcher career lines, fetched in parallel (cached, so cheap after
    # the first live poll of a game).
    if bvp_tasks:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            for hd, res in zip([t[0] for t in bvp_tasks],
                               ex.map(lambda t: _bvp(t[1], t[2]), bvp_tasks)):
                hd["bvp"] = res

    hitters.sort(key=lambda x: (x["side"] != "away", x["order"]))
    pitchers.sort(key=lambda x: -x["pitches"])
    return {
        "game_pk": game_pk, "away": names["away"], "home": names["home"],
        "state": {
            "inning": ls.get("currentInning"), "half": ls.get("inningState"),
            "outs": ls.get("outs"), "balls": (ld.get("plays", {}).get("currentPlay", {})
                                              .get("count", {}) or {}).get("balls"),
            "strikes": (ld.get("plays", {}).get("currentPlay", {}).get("count", {}) or {}).get("strikes"),
            "away_runs": (ls.get("teams", {}).get("away", {}) or {}).get("runs"),
            "home_runs": (ls.get("teams", {}).get("home", {}) or {}).get("runs"),
            "status": gd.get("status", {}).get("detailedState"),
        },
        "pitchers": pitchers, "hitters": hitters,
    }


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


def _sb_defense_map(season):
    """{team_id: stolen-base success rate ALLOWED} plus '_lg' -- the catcher's
    arm (and the staff's hold game), measured by what actually happens on their
    watch. Feeds the sim's steal-success shift: the same runner succeeds less
    against the club that throws people out."""
    def fetch():
        data = _get(f"{STATS_BASE}/teams/stats?sportId=1&season={season}"
                    f"&group=fielding&stats=season")
        out, tot_sb, tot_cs = {}, 0.0, 0.0
        for s in data["stats"][0]["splits"]:
            st = s["stat"]
            sb, cs = _f(st.get("stolenBases")), _f(st.get("caughtStealing"))
            if sb + cs >= 20:                     # enough attempts to mean anything
                out[s["team"]["id"]] = round(sb / (sb + cs), 4)
            tot_sb += sb; tot_cs += cs
        if tot_sb + tot_cs > 0:
            out["_lg"] = round(tot_sb / (tot_sb + tot_cs), 4)
        return out
    return _cached(("sbdef", season), 6 * 3600, fetch) or {}


def _team_k_map(season):
    """{team_id: batting strikeout rate (SO/PA)} plus '_lg' -- how whiff-prone
    each LINEUP is. Feeds the staff sim's opponent factor: the same starter
    striking out a fifth of the league fans a quarter of the whiffiest lineup
    and a sixth of the best bat-to-ball club. 2026 spread: .188 to .254 around
    a .221 league."""
    def fetch():
        data = _get(f"{STATS_BASE}/teams/stats?sportId=1&season={season}"
                    f"&group=hitting&stats=season")
        out, tot_so, tot_pa = {}, 0.0, 0.0
        for s in data["stats"][0]["splits"]:
            st = s["stat"]
            so, pa = _f(st.get("strikeOuts")), _f(st.get("plateAppearances"))
            if pa >= 500:
                out[s["team"]["id"]] = round(so / pa, 4)
            tot_so += so; tot_pa += pa
        if tot_pa > 0:
            out["_lg"] = round(tot_so / tot_pa, 4)
        return out
    return _cached(("teamk", season), 6 * 3600, fetch) or {}


def _bat_sides(pids):
    """{pid: 'L'|'R'|'S'} for a set of batter ids, one bulk call, cached."""
    pids = tuple(sorted(p for p in pids if p))
    if not pids:
        return {}
    def fetch():
        try:
            d = _get(f"{STATS_BASE}/people?personIds="
                     + ",".join(str(p) for p in pids)
                     + "&fields=people,id,batSide,code")
            return {p["id"]: (p.get("batSide") or {}).get("code")
                    for p in d.get("people", [])}
        except Exception:
            return {}
    return _cached(("batside", pids), 24 * 3600, fetch) or {}


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
                       "ip": _ip_float(st.get("inningsPitched")),
                       "gs": _f(st.get("gamesStarted")),
                       # Games PITCHED, not just started: g >> gs is how an
                       # OPENER is recognized (a reliever whose season innings
                       # would otherwise be divided over his handful of starts).
                       "g": _f(st.get("gamesPitched")),
                       "k9": _f(st.get("strikeoutsPer9Inn")),
                       "hr": _f(st.get("homeRuns")), "bb": _f(st.get("baseOnBalls")),
                       "k": _f(st.get("strikeOuts")), "hbp": _f(st.get("hitByPitch"))}
                if disp == "season":
                    res["season"] = rec
                elif disp == "lastXGames":
                    res["recent"] = rec
            return res or None
        except Exception:
            return None
    return _cached(("sp", pid, season), 600, fetch)


# A start has to clear this many Ks ABOVE the pitcher's own season average
# before it counts as a genuine outlier rather than a good night. Fitted to
# taste rather than measured: at +4 a typical starter's season shows one or two
# such games, which is what "he had a night" should mean. Lowering it to +3
# tagged a third of every rotation and the callout stopped carrying information.
_DAWG_OVER_AVG = 4.0
_DAWG_MIN_K = 9          # ...and it has to be a big number in absolute terms too


def _k_log(pid, season):
    """A starter's per-START strikeout log: {avg, high, gs, dawg}.

    The card shows what the model expects tonight; this is what he has actually
    DONE. `dawg` is his best start of the year when it clears both bars in
    _DAWG_OVER_AVG / _DAWG_MIN_K -- the "he was an absolute dawg that night"
    game, with the date, the opponent and the line, so a 13-K ceiling read isn't
    an abstraction. Relief appearances are excluded: a one-inning cameo would
    drag the per-start average down and it is not what he is doing tonight."""
    if not pid:
        return None

    def fetch():
        try:
            d = _get(f"{STATS_BASE}/people/{pid}/stats?stats=gameLog"
                     f"&group=pitching&season={season}")
        except Exception:
            return None
        starts = []
        for s in (d.get("stats") or [{}])[0].get("splits") or []:
            st = s.get("stat") or {}
            if not _f(st.get("gamesStarted")):        # relief outing -> not a start
                continue
            starts.append({
                "k": int(_f(st.get("strikeOuts"))),
                "ip": _ip_float(st.get("inningsPitched")),
                "date": s.get("date"),
                "opp": (s.get("opponent") or {}).get("name"),
                "home": bool(s.get("isHome")),
            })
        if not starts:
            return None
        ks = [x["k"] for x in starts]
        avg = sum(ks) / len(ks)
        best = max(starts, key=lambda x: x["k"])
        out = {"avg": round(avg, 1), "high": best["k"], "gs": len(starts),
               "recent": [x["k"] for x in starts[-5:]]}
        if best["k"] >= _DAWG_MIN_K and best["k"] - avg >= _DAWG_OVER_AVG:
            out["dawg"] = {"k": best["k"], "ip": best["ip"], "date": best["date"],
                           "opp": best["opp"], "home": best["home"],
                           "over_avg": round(best["k"] - avg, 1)}
        return out
    # A finished start never changes, so the only thing this can go stale on is
    # TONIGHT's start landing in it -- an hour is well inside that.
    return _cached(("klog", pid, season), 3600, fetch)


def _league_avgs(hit, pit, bp, hitplat):
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
FIP_CONSTANT = 3.15  # puts FIP on the ERA scale (league FIP ~ league ERA)


def _fip(rec):
    """Fielding-Independent Pitching: what a pitcher's ERA "should" be from just
    the outcomes he controls (K, BB, HR). More predictive of FUTURE runs than
    ERA, which is noisy and defense/sequencing dependent."""
    if not rec:
        return None
    ip = rec.get("ip") or 0
    hr, bb, k = rec.get("hr"), rec.get("bb"), rec.get("k")
    if ip <= 0 or hr is None or bb is None or k is None:
        return None
    hbp = rec.get("hbp") or 0
    fip = (13 * hr + 3 * (bb + hbp) - 2 * k) / ip + FIP_CONSTANT
    return max(1.5, min(9.0, fip))


def _starter_ra9(sp, lg):
    """RA/9 from a starter, blending three complementary reads, each regressed by
    IP and toward recent form:
      - ERA   (actual runs allowed; captures sequencing/defense)
      - FIP   (K/BB/HR only; the most predictive of future runs)
      - WHIP  (baserunners allowed)
    Keeping all three is more robust than any one alone."""
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

    # FIP, regressed by IP toward league and blended with recent FIP when present.
    fip_s = _fip(s)
    if fip_s is not None:
        fip_eff = rel_s * fip_s + (1 - rel_s) * lg["era"]
        fip_r = _fip(r) if r and (r.get("ip") or 0) > 0 else None
        if fip_r is not None:
            rel_r = r["ip"] / (r["ip"] + RECENT_IP_REGRESS)
            recent_fip = rel_r * fip_r + (1 - rel_r) * fip_eff
            fip_eff = (1 - RECENT_WEIGHT) * fip_eff + RECENT_WEIGHT * recent_fip
        # Statcast xERA as the FOURTH read, when attached: the contact quality
        # FIP is structurally blind to (it treats every ball in play as
        # league-average) and ERA sees only through sequencing/defense luck.
        # Regressed by the same IP reliability as the other reads.
        xera = s.get("xera")
        if xera and xera > 0:
            xera_eff = rel_s * xera + (1 - rel_s) * lg["era"]
            ra9 = (0.25 * era_eff + 0.30 * fip_eff + 0.25 * xera_eff
                   + 0.20 * whip_ra9)
        else:
            ra9 = 0.35 * era_eff + 0.40 * fip_eff + 0.25 * whip_ra9
    else:
        ra9 = 0.65 * era_eff + 0.35 * whip_ra9  # no FIP inputs -> ERA+WHIP only
    return _velo_adjust(ra9, sp)


def _velo_adjust(ra9, sp):
    """Fastball-velocity fatigue term. A last start 1+ mph under his season
    average is the signal that moves BEFORE results do (dead arm, injury,
    age cliff): +3% RA9 per mph down, capped at 3 mph — roughly the
    +0.13 ERA/mph the public research converges on. A dead zone under 0.8
    mph absorbs ordinary start-to-start noise (cold nights, pitch counts),
    and a clearly LIVE arm (+1 mph) earns a modest 3% credit — drops are
    the reliable side of the signal, so the credit stays half-sized."""
    v = (sp or {}).get("velo") or {}
    d = v.get("delta")
    if d is None:
        return ra9
    if d <= -0.8:
        return ra9 * (1 + 0.03 * min(3.0, -d))
    if d >= 1.0:
        return ra9 * 0.97
    return ra9


def _bullpen_ra9(team_bp, lg):
    era = team_bp.get("era") or lg["bp_era"]
    whip = team_bp.get("whip") or lg["bp_whip"]
    whip_ra9 = lg["era"] * (whip / lg["whip"]) if whip > 0 else era
    return 0.70 * era + 0.30 * whip_ra9


# How tired an arm is, as a function of PITCHES rather than appearances. The
# old rule fired a full "gassed" flag on any back-to-back regardless of load, so
# a reliever who threw 9 pitches one night and 4 the next was scored exactly as
# tired as one who threw 30 and 28 -- and more tired than a single 27-pitch
# outing, which only scored 0.4. Measured over 14 days that was 20.7% of all
# flags (126 of 610), median two-day load 29 pitches, lightest 6 pitches across
# three days. These ramps make the score monotone in workload: an appearance
# costs nothing on its own, and every trigger below scales with pitches thrown.
_PEN_LOAD_FREE = 10.0      # weighted pitches an arm carries with no penalty
_PEN_LOAD_FULL = 45.0      # weighted pitches at which he is fully down
_B2B_FREE = 8.0            # a back-to-back this light is not a usage constraint
_B2B_FULL = 22.0           # a back-to-back this heavy sits him tonight
_MULTI_BASE = 0.35         # 3-of-4 days is a real constraint even when light
# Where "tired" becomes "not pitching tonight", read off usage rather than
# picked. Scoring every reliever from his prior days and then checking whether
# he actually appeared (2,431 arm-days) gives a cleanly monotone curve:
#
#   fatigue   0.0-0.1  0.2-0.3  0.4-0.5  0.5-0.6  0.6-0.7  0.7-0.8  0.8+
#   appeared    42.6%    25.8%    14.2%     6.1%     3.2%     2.4%   0.0%
#
# against a 32.9% baseline for all arms with recent work. At 0.60 the arms we
# sit went on to pitch 1.5% of the time, 22x below baseline, so calling them
# unavailable is right about 98.5% of the time. A stricter 0.75 is never wrong
# (0 of 101) but misses the 0.6-0.75 band, which is just as absent in practice.
_PEN_OUT_AT = 0.60
_PEN_DAY_DECAY = (1.0, 0.60, 0.30)   # yesterday, 2 days ago, 3 days ago


def _ramp(x, lo, hi):
    """0 below lo, 1 above hi, linear between. Monotone by construction."""
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return (x - lo) / (hi - lo)


def _arm_fatigue(days, pitches):
    """How unavailable one reliever is tonight, in [0, 1].

    `days` is the set of days-ago he appeared, `pitches` maps day -> pitch count.
    Three routes to tired, each a ramp on pitches so none of them can rank a
    light night above a heavy one:

      workload   recency-weighted pitches over the last three days
      b2b        pitched both of the last two days -- governed by the LIGHTER of
                 the two outings, because that is what decides whether a manager
                 treats it as a real back-to-back
      multi      three of the last four days, a usage constraint on its own but
                 one that still scales with how much he actually threw
    """
    d1, d2, d3 = (pitches.get(i, 0) for i in (1, 2, 3))
    w1, w2, w3 = _PEN_DAY_DECAY
    load = w1 * d1 + w2 * d2 + w3 * d3
    f = _ramp(load, _PEN_LOAD_FREE, _PEN_LOAD_FULL)
    if {1, 2} <= days:
        f = max(f, _ramp(min(d1, d2), _B2B_FREE, _B2B_FULL))
    if len(days) >= 3:
        f = max(f, _MULTI_BASE + (1.0 - _MULTI_BASE)
                * _ramp(load, _PEN_LOAD_FREE, _PEN_LOAD_FULL))
    return max(0.0, min(1.0, f))


def _pen_boxscore_pitchers(game_pk):
    """Relievers who appeared in a finished game -> {team_id: [(pid, name, pitches), ...]}.
    The first pitcher in each side's `pitchers` list is the starter and is skipped."""
    def fetch():
        try:
            d = _get(f"{STATS_BASE}/game/{game_pk}/boxscore")
            out = {}
            for side in ("home", "away"):
                t = d["teams"][side]
                tid = t.get("team", {}).get("id")
                arms = []
                for pid in (t.get("pitchers") or [])[1:]:      # [0] is the starter
                    pl = t["players"].get(f"ID{pid}", {})
                    # Skip position players mopping up a blowout — they aren't real
                    # bullpen arms and shouldn't count against tomorrow's pen. (MLB
                    # tags them position "P" while pitching, so check every position
                    # they played that game — a true reliever only ever shows "P".)
                    allpos = pl.get("allPositions") or []
                    if any((p or {}).get("abbreviation") not in (None, "P") for p in allpos):
                        continue
                    ps = pl.get("stats", {}).get("pitching", {})
                    pitches = _f(ps.get("numberOfPitches") or ps.get("pitchesThrown"))
                    nm = pl.get("person", {}).get("fullName", "")
                    arms.append((pid, nm, pitches))
                if tid:
                    out[tid] = arms
            return out
        except Exception:
            return None
    return _cached(("penbox", game_pk), 1800, fetch)


def _bullpen_fatigue(date, season):
    """{team_id: {"factor", "count", "arms"}} — how gassed each pen is tonight.

    Looks back at the last two days of finished games and tallies each reliever's
    recent workload. An arm is "gassed" (likely unavailable / less effective) if it
    pitched on back-to-back days or threw a heavy count yesterday; "tired" for a
    moderate outing yesterday. The more high-leverage arms down, the higher the
    factor (>1.0) applied to the bullpen's run prevention — nudging totals up.
    Best-effort: any fetch failure yields a neutral 1.0 for that team."""
    def fetch():
        try:
            base = _dt.strptime(date, "%Y-%m-%d")
        except Exception:
            return {}
        # day 1 = yesterday (heaviest weight) back through day 4, so we see a
        # multi-day grind (pitched 3 of the last 4) not just a single back-to-back.
        days = {d: (base - _td(days=d)).strftime("%Y-%m-%d") for d in (1, 2, 3, 4)}
        pks_by_day = {}
        for dnum, dstr in days.items():
            try:
                data = _get(f"{STATS_BASE}/schedule?sportId=1&date={dstr}")
                dates = data.get("dates", [])
                gms = dates[0]["games"] if dates else []
                pks_by_day[dnum] = [g.get("gamePk") for g in gms
                                    if g.get("status", {}).get("abstractGameState") == "Final"]
            except Exception:
                pks_by_day[dnum] = []
        # usage[team][pid] = {"days": set(), "name": str, "pitches": {day: count}}
        usage = {}
        all_pks = [(d, pk) for d, pks in pks_by_day.items() for pk in pks if pk]
        boxes = {}
        if all_pks:
            with ThreadPoolExecutor(max_workers=8) as ex:
                for (dnum, pk), box in zip(all_pks, ex.map(lambda x: _pen_boxscore_pitchers(x[1]), all_pks)):
                    boxes[(dnum, pk)] = box
        for (dnum, pk), box in boxes.items():
            if not box:
                continue
            for tid, arms in box.items():
                tu = usage.setdefault(tid, {})
                for pid, nm, pitches in arms:
                    a = tu.setdefault(pid, {"days": set(), "name": nm, "pitches": {}})
                    a["days"].add(dnum)
                    a["pitches"][dnum] = max(a["pitches"].get(dnum, 0), pitches)
        out = {}
        for tid, tu in usage.items():
            score = 0.0
            down = []                                   # (fatigue, pid, name)
            for pid, a in tu.items():
                f = _arm_fatigue(a["days"], a["pitches"])
                score += f                              # continuous, not 1.0/0.4 buckets
                if f >= _PEN_OUT_AT:
                    down.append((f, pid, a["name"]))
            down.sort(reverse=True)                     # most tired first
            factor = 1.0 + min(0.15, 0.025 * score)     # a touch more headroom for a truly gassed pen
            out[tid] = {"factor": round(factor, 3), "count": len(down),
                        "arms": [nm for _f, _p, nm in down[:4]],
                        # WHICH arms, not just how many. The sim used to sit
                        # `count` pitchers off the good end of the pen, so a
                        # mop-up man's back-to-back benched the closer.
                        "out_ids": [pid for _f, pid, _nm in down],
                        "score": round(score, 2)}
        return out
    return _cached(("penfatigue", date, season), 1800, fetch)


def _defense_map(season):
    """{team_id(str): hit/run multiplier} from Statcast team OAA. A good defense
    (high OAA) turns more balls in play into outs, so the OPPOSING offense hits and
    scores a little less. Season OAA -> per-game factor (~4000 balls in play a
    season, league BABIP ~.290, so each out saved trims hits by OAA/(BIP*BABIP)),
    capped +/-3%. Neutral 1.0 on any fetch failure."""
    def build():
        try:
            import savant
            oaa = savant.team_defense(season)
        except Exception:
            oaa = {}
        return {str(tid): round(max(0.97, min(1.03, 1 - v / 1160.0)), 4)
                for tid, v in oaa.items()}
    return _cached(("defense", season), 6 * 3600, build) or {}


def _sp_share(sp):
    """THIS starter's share of his side's innings, from his own expected
    workload -- not the league-flat 0.60. A 6.5-inning workhorse owns 72% of
    the game and leaves his (possibly bad) bullpen only a fifth of it; a
    4.7-inning fifth starter hands 48% to the pen; an opener hands nearly all
    of it. The flat weight blunted exactly the games where the starter/bullpen
    quality gap is the story. Falls back to the flat weight without workload
    data."""
    try:
        wl = _starter_workload(sp) or {}
        est = wl.get("est_ip")
        if est:
            return max(0.15, min(0.78, est / 9.0))
    except Exception as e:
        errlog.note("MLB-sp-share", e)
    return SP_INNINGS_WEIGHT


def _pitching_factor(sp, team_bp, lg, bp_fatigue=1.0):
    sp_ra9 = _starter_ra9(sp, lg)
    bp_ra9 = _bullpen_ra9(team_bp, lg) * bp_fatigue    # gassed pen -> higher RA9
    w = _sp_share(sp)
    if sp_ra9 is None:
        game_ra9 = bp_ra9
    else:
        game_ra9 = w * sp_ra9 + (1 - w) * bp_ra9
    return game_ra9 / lg["era"] if lg["era"] else 1.0, sp_ra9, bp_ra9


def _offense_factor(team_hit, ops_vs_hand, opp_hand, lg, sp_share=SP_INNINGS_WEIGHT):
    """Offense relative to league, platoon-adjusted for the starter's hand --
    weighted by how long THAT starter actually pitches (his platoon hand only
    matters while he is in the game)."""
    off_runs = team_hit["rpg"] / lg["rpg"] if lg["rpg"] else 1.0
    lg_ops_hand = lg["ops_vl"] if opp_hand == "L" else lg["ops_vr"]
    ops_overall = team_hit["ops"]
    # OPS the offense actually faces: starter's hand for his innings, overall for the bullpen.
    ops_eff = sp_share * (ops_vs_hand or ops_overall) + (1 - sp_share) * ops_overall
    lg_ops_eff = sp_share * lg_ops_hand + (1 - sp_share) * lg["ops"]
    off_ops = ops_eff / lg_ops_eff if lg_ops_eff else 1.0
    return 0.6 * off_runs + 0.4 * off_ops


# ---- Kalshi price matching ------------------------------------------------
def _kalshi_fee(cents):
    """Kalshi taker fee in cents at price `cents` — see kalshi.taker_fee_cents.

    Rounded to 0.1c because this one feeds DISPLAYED edge figures, where a
    trailing 0.0693 reads as false precision. The unrounded value is the one to
    compute with; every caller doing money math should use kalshi's directly."""
    return round(kalshi.taker_fee_cents(cents), 1)


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


_TICKER_MONTHS = {m: i + 1 for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}
_TICKER_START = _re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})")
# A Kalshi event ticker whose start is further than this from the game's start is
# a different game, not a loose match -- refuse it rather than price off it.
_MATCH_TOLERANCE_S = 3 * 3600


def _ticker_start(event_ticker):
    """First pitch encoded in a Kalshi event ticker, as an epoch.
    'KXMLBGAME-26JUL301340KCMIN' -> Jul 30 2026 13:40 America/New_York. Kalshi
    names game tickers in Eastern; this matches MLB's scheduled start exactly."""
    m = _TICKER_START.search(event_ticker or "")
    if not m:
        return None
    yy, mon, dd, hh, mi = m.groups()
    if mon not in _TICKER_MONTHS:
        return None
    try:
        from zoneinfo import ZoneInfo
        return _dt(2000 + int(yy), _TICKER_MONTHS[mon], int(dd), int(hh), int(mi),
                   tzinfo=ZoneInfo("America/New_York")).timestamp()
    except Exception:
        return None


def _match_price(kalshi_index, abbr_map, home_id, away_id, start_epoch):
    """The Kalshi event for THIS game. Matched on first pitch, which the event
    ticker encodes exactly.

    Not on close_time: Kalshi closes an MLB game market a flat 72 hours after it
    starts (a legal backstop, not the settlement time), so |close - start| is
    ~72h for the right game, ~48h for yesterday's and ~96h for tomorrow's. Taking
    the minimum therefore preferred the EARLIEST game of a series -- every game
    with a same-opponent game the previous day got priced off that ALREADY-PLAYED
    market, whose contracts sit at 0c/100c because they have settled. Half of a
    typical slate matched the wrong day.
    """
    home = abbr_map.get(home_id, ""); away = abbr_map.get(away_id, "")
    candidates = kalshi_index.get(frozenset({home, away}))
    if not candidates:
        return None, home, away
    if start_epoch:
        timed = [(abs(ts - start_epoch), e) for e in candidates
                 for ts in (_ticker_start(e.get("event")),) if ts]
        if timed:
            gap, best = min(timed, key=lambda t: t[0])
            # Better to show a game unpriced than priced off the wrong game.
            return (best if gap <= _MATCH_TOLERANCE_S else None), home, away
    # Ticker unparseable (naming change): fall back to the closest close_time.
    best = min(candidates, key=lambda e: abs((e["close"] or 0) - (start_epoch or 0)))
    return best, home, away


def _boxscore_lineup(game_pk):
    """Posted lineup per side -> {'home': [batter, ...], 'away': [...]}.

    batter = {name, ops, ab, hits, pa} from season stats (ordered by lineup spot).
    Also returns `<side>_posted` (is the batting order actually up yet) and
    `<side>_sp` (the starting pitcher id the boxscore lists, once the game is under
    way) so callers can tell a confirmed lineup from a projected one and catch a
    late scratch (boxscore starter != the listed probable)."""
    def fetch():
        try:
            d = _get(f"{STATS_BASE}/game/{game_pk}/boxscore")
            out = {}
            for side in ("home", "away"):
                t = d["teams"][side]
                order = t.get("battingOrder") or []
                batters = []
                for pid in order:
                    pl = t["players"].get(f"ID{pid}", {})
                    bs = pl.get("seasonStats", {}).get("batting", {})
                    batters.append({
                        "id": pid,
                        "name": pl.get("person", {}).get("fullName", ""),
                        "ops": _f(bs.get("ops")), "ab": _f(bs.get("atBats")),
                        "g": _f(bs.get("gamesPlayed")),
                        "hits": _f(bs.get("hits")), "pa": _f(bs.get("plateAppearances")),
                        "doubles": _f(bs.get("doubles")), "triples": _f(bs.get("triples")),
                        "hr": _f(bs.get("homeRuns")), "bb": _f(bs.get("baseOnBalls")),
                        "hbp": _f(bs.get("hitByPitch")),
                        "strikeouts": _f(bs.get("strikeOuts")),
                        # What KIND of out this hitter makes. A strikeout leaves
                        # every runner where he stood, a fly ball scores the man
                        # on third six times in ten, a ground ball turns two on
                        # four in ten. The engine cannot tell them apart without
                        # these, and it is the same three counters the league
                        # mix is built from.
                        "gouts": _f(bs.get("groundOuts")),
                        "aouts": _f(bs.get("airOuts")),
                        "sb": _f(bs.get("stolenBases")), "cs": _f(bs.get("caughtStealing")),
                    })
                out[side] = batters
                out[side + "_posted"] = bool(order)
                pitchers = t.get("pitchers") or []
                if pitchers:
                    sp = t["players"].get(f"ID{pitchers[0]}", {})
                    out[side + "_sp"] = {"id": pitchers[0],
                                         "name": sp.get("person", {}).get("fullName", "")}
                else:
                    out[side + "_sp"] = None
            return out
        except Exception:
            return None
    return _cached(("box", game_pk), 300, fetch)


def _with_form(lineup, form_map):
    """A copy of a posted lineup with each hitter's OPS nudged by his last-10 form.

    The season OPS is what a hitter has BEEN; ten games is what he is doing now.
    Neither alone is right -- ten games is ~40 PA and mostly noise -- so the
    multiplier is regressed by the plate appearances behind it and capped at
    +/-12% (see mlb_form). A hitter's talent estimate still governs; form moves
    it a few percent and earns a label.

    Copies rather than mutating: the lineup dicts come out of a shared cache, so
    editing them in place would compound the same adjustment on every slate build
    until the cache expired."""
    if not lineup:
        return lineup
    if not form_map:
        return lineup
    out = []
    for b in lineup:
        rec = form_map.get(b.get("id"))
        if not rec:
            out.append(b)
            continue
        f, note = mlb_form.hitter_factor(rec, b.get("ops"))
        if f == 1.0:
            out.append(b)
            continue
        nb = dict(b)
        nb["ops"] = round((b.get("ops") or 0) * f, 4)
        nb["form_factor"] = round(f, 4)
        nb["form_note"] = note
        nb["form_tag"] = mlb_form.trend_tag(rec, b.get("ops"))
        out.append(nb)
    return out


def _last_posted_lineup(team_id, date):
    """The team's most recent POSTED batting order (their regulars) — the
    per-batter prop fallback for the hours before today's card is out. Without
    it the whole morning slate has zero batter props (no hits/TB/HR/SB legs in
    combos, pitcher-only Pick 6 sheets) until lineups post in the afternoon.
    The confirm flag stays 'projected', so the honesty labeling is unchanged."""
    def fetch():
        try:
            import datetime as _dt
            end = _dt.date.fromisoformat(date) - _dt.timedelta(days=1)
            start = end - _dt.timedelta(days=6)
            d = _get(f"{STATS_BASE}/schedule?sportId=1&teamId={team_id}"
                     f"&startDate={start.isoformat()}&endDate={end.isoformat()}")
            last = None
            for day in d.get("dates") or []:
                for gm in day.get("games") or []:
                    if (gm.get("status") or {}).get("abstractGameState") == "Final":
                        side = ("home" if (gm["teams"]["home"]["team"]["id"] == team_id)
                                else "away")
                        last = (gm["gamePk"], side)
            if not last:
                return None
            lu = _boxscore_lineup(last[0]) or {}
            bats = lu.get(last[1]) or None
            # 9 order spots only — the boxscore order can include mid-game subs.
            return bats[:9] if bats else None
        except Exception:
            return None
    return _cached(("lastlu", team_id, date), 6 * 3600, fetch)


def _confirm_status(g, lu):
    """Scratch / confirmation guard for one game: is our read built on posted
    lineups and the listed starters, or is it still provisional (and liable to
    move)? Catches the two bets you don't want to make blind — a starter that's
    been scratched (the posted/actual arm differs from the listed probable) and a
    game whose starter is still TBD (the model falls back to league average)."""
    state = (g.get("live") or {}).get("state")
    final = state == "Final"

    def sp_status(side, sp_id):
        if final:
            return "final"
        if not sp_id:
            return "tbd"                              # no probable -> league-average read
        actual = (lu.get(side + "_sp") or {})
        if actual.get("id") and actual["id"] != sp_id:
            return "scratched"
        return "listed"

    h = sp_status("home", g.get("home_sp_id"))
    a = sp_status("away", g.get("away_sp_id"))
    scratched = []
    if h == "scratched":
        scratched.append({"side": "home", "listed": g.get("home_sp_name"),
                          "actual": (lu.get("home_sp") or {}).get("name")})
    if a == "scratched":
        scratched.append({"side": "away", "listed": g.get("away_sp_name"),
                          "actual": (lu.get("away_sp") or {}).get("name")})
    # Human note, worst issue first: a scratch is a red flag, a TBD starter or an
    # unposted lineup is a "provisional" yellow flag.
    note, level = "", "ok"
    if scratched:
        who = ", ".join(f"{s['listed'] or 'listed SP'} → {s['actual'] or 'new SP'}" for s in scratched)
        note, level = f"Starter changed: {who} - model still on the listed arm, refresh before betting.", "scratch"
    elif h == "tbd" or a == "tbd":
        note, level = "Starter TBD - pitching read is league-average until it's announced.", "provisional"
    elif not final and not (lu.get("home_posted") and lu.get("away_posted")):
        note, level = "Lineups not posted yet - offense assumes the regulars play; may shift once cards are out.", "provisional"
    return {"level": level, "note": note,
            "home_lineup": "confirmed" if lu.get("home_posted") else "projected",
            "away_lineup": "confirmed" if lu.get("away_posted") else "projected",
            "home_sp": h, "away_sp": a, "scratched": scratched}


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


def _exp_ip_per_start(sp):
    """A starter's expected innings THIS start, from his own workload history —
    a workhorse (6.3 IP/GS) and an opener (3.5) should not share one K ladder.
    Season IP-per-start regressed toward the league ~5.15 by starts (k=5), with a
    30% blend toward recent form when available. Clamped to a sane range."""
    s = (sp or {}).get("season") or {}
    ip, gs = s.get("ip") or 0.0, s.get("gs") or 0
    if not gs:
        return 5.15
    # OPENER GUARD: season ip counts RELIEF innings too, so ip/gs invents a
    # workhorse out of a reliever who has opened a few games -- 51 IP over 67
    # appearances with 3 opener starts "averaged" 17 innings a start and
    # clamped to 7.2, pricing a 2-out lefty like an ace on the K ladder and
    # the DFS slate. When he has pitched far more games than he has started,
    # his real outing is his per-APPEARANCE workload.
    g = s.get("g") or gs
    if g >= 8 and g > gs * 1.5:
        return max(1.0, min(3.0, ip / g))
    per = ip / gs
    per = (gs * per + 5 * 5.15) / (gs + 5)          # shrink small samples
    r = (sp or {}).get("recent") or {}
    rip, rgs = r.get("ip") or 0.0, r.get("gs") or 0
    if rgs >= 2:                                    # recent workload (last 5)
        per = 0.7 * per + 0.3 * (rip / rgs)
    return max(3.0, min(7.2, per))


# Strikeout rate stabilizes fast but not instantly: a K/9 off a handful of
# innings is mostly noise, so regress it toward a league-average starter K/9 by
# innings pitched (K% reaches useful reliability around ~70 batters faced, ~16
# IP). An established arm's rate barely moves; a one-start rookie's is pulled
# most of the way to league, so his strikeout ladder stops printing phantom
# edges off a 7-inning sample.
LG_K9 = 8.4            # ~league-average MLB starter strikeouts per 9
K9_REGRESS_IP = 16.0   # innings of league-average prior blended into every arm


def _regressed_k9(season):
    """Season K/9 regressed toward the league starter average by innings pitched,
    so a tiny sample (e.g. one 7-IP start) can't drive an overconfident K ladder.
    Returns None when there's no usable rate at all."""
    if not season:
        return None
    k9 = season.get("k9")
    if not k9 or k9 <= 0:
        return None
    ip = season.get("ip") or 0.0
    if ip <= 0:
        return LG_K9
    return (ip * k9 + K9_REGRESS_IP * LG_K9) / (ip + K9_REGRESS_IP)


# A starter's outing length isn't just his historical innings -- it's how fast he
# burns pitches. A wild arm throws more pitches per inning (deep counts + traffic),
# hits the manager's pitch budget sooner, and goes fewer innings, which caps his
# strikeouts. We model pitches/inning from walk (and, mildly, strikeout) rate,
# hold a stamina "budget," and derive expected innings = budget / pitches-per-inning.
LG_BB9 = 3.1           # ~league-average starter walks per 9
REF_PIP = 15.8         # pitches per inning at league-average command
PRIOR_BUDGET = 85.0    # default pitch budget a listed starter gets (rookie-safe;
                       # real starters average 83 pitches, n=560 Jul-Aug 2026)
_BF_PER_9 = 38.0       # batters faced per 9 innings, to turn /9 rates into per-PA


def _starter_workload(sp):
    """Walk-aware outing shape for a starter. Returns pitches-per-inning, expected
    innings, an expected pitch count, and a per-PA walk rate for the Monte Carlo.
    Rates are regressed by innings so a tiny sample can't swing it, and expected
    innings blends his empirical IP/start with a rookie-safe budget by sample."""
    season = (sp or {}).get("season")
    if not season:
        return None
    ip = season.get("ip") or 0.0
    k9 = _regressed_k9(season) or LG_K9
    bb = season.get("bb")
    bb9_raw = (bb / ip * 9.0) if (bb is not None and ip > 0) else LG_BB9
    bb9 = ((ip * bb9_raw + K9_REGRESS_IP * LG_BB9) / (ip + K9_REGRESS_IP)
           if ip > 0 else LG_BB9)
    pip = REF_PIP + 0.85 * (bb9 - LG_BB9) + 0.30 * (k9 - LG_K9)
    pip = max(14.5, min(20.5, pip))
    emp_ip = _exp_ip_per_start(sp) if ip > 0 else 5.15
    if emp_ip < 3.0:
        # An OPENER (the guard in _exp_ip_per_start fired): his outing length
        # is his measured per-appearance workload over a big relief sample --
        # blending a rookie-starter prior back in, or flooring at 3 innings,
        # would re-invent exactly the workhorse the guard just removed.
        return {"bb9": round(bb9, 2), "pip": round(pip, 1),
                "est_ip": round(emp_ip, 2),
                "est_pitches": int(round(emp_ip * pip)),
                "bb_pa": max(0.045, min(0.16, bb9 / _BF_PER_9))}
    w = ip / (ip + K9_REGRESS_IP) if ip > 0 else 0.0     # sample reliability
    budget = w * (emp_ip * REF_PIP) + (1 - w) * PRIOR_BUDGET
    est_ip = max(3.0, min(7.6, budget / pip))
    return {"bb9": round(bb9, 2), "pip": round(pip, 1), "est_ip": round(est_ip, 2),
            "est_pitches": int(round(est_ip * pip)),
            "bb_pa": max(0.045, min(0.16, bb9 / _BF_PER_9))}


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
    # THIS starter's real innings share, not the flat 0.60 -- a workhorse's
    # WHIP owns more of the hit environment; an opener's owns almost none.
    w = _sp_share(opp_sp)
    whip = w * sp_whip + (1 - w) * bp_whip
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
            wind_desc = f"blowing OUT to center {out_mph} mph"
        elif out_mph < -3:
            wind_desc = f"blowing IN from center {abs(out_mph)} mph"
        else:
            wind_desc = "crosswind (little run effect)"
    factor = winfo.get("factor", 1.0)
    return {
        "available": True, "stadium": s["name"], "roof": s["roof"],
        "temp_f": wx.get("temp_f"), "wind_mph": wx.get("wind_mph"),
        "wind_dir": wx.get("wind_dir"), "wind_effect": wind_desc,
        "wind_out_mph": out_mph,          # +out (toward CF, more runs) / -in
        "cf_bearing_deg": s.get("cf_bearing_deg"),
        "precip_pct": wx.get("precip_pct"), "summary": wx.get("summary"),
        "run_factor": round(factor, 3),
        "hr_extra": round(winfo.get("hr_extra") or 1.0, 3),
        "run_pct": round((factor - 1.0) * 100, 1),   # net weather nudge to runs
        "roof_closed_pct": winfo.get("roof_closed_pct"),
        "source": wx.get("source"),
    }


# Detailed states MLB reports while the teams are still warming up. The abstract
# state is already "Live" here, but no pitch has been thrown.
# "Delayed Start" belongs here but a plain "Delayed"/"Suspended" does not — those
# halt a game that is already in progress, and its pre-game props are stale.
_PREGAME_DETAILED = ("scheduled", "pre-game", "pregame", "warmup", "warm up",
                     "delayed start")


def _really_started(live, start_epoch=None):
    """True only if the game is genuinely under way. MLB's abstract state alone
    says Live from warmups onward, so: a first pitch still in the future settles
    it outright, then trust the detailed state when it is one we recognise, and
    otherwise fall back to whether anything has actually happened on the field
    (past the 1st, an out recorded, or a run in)."""
    if start_epoch and start_epoch > _time.time():
        return False                    # scheduled start hasn't arrived yet
    det = (live.get("detailed") or "").strip().lower()
    if any(det.startswith(x) for x in _PREGAME_DETAILED):
        return False
    if det.startswith("in progress") or det.startswith("manager challenge"):
        return True
    inning = live.get("inning") or 0
    outs = live.get("outs") or 0
    runs = (live.get("away_runs") or 0) + (live.get("home_runs") or 0)
    return bool(inning > 1 or outs or runs)


def _schedule(date, season):
    data = _get(f"{STATS_BASE}/schedule?sportId=1&date={date}&hydrate=probablePitcher,linescore")
    dates = data.get("dates", [])
    games = dates[0]["games"] if dates else []
    out = []
    for g in games:
        # Only games our roster-based engine can actually model: regular season
        # and the postseason (Wild Card / DS / LCS / WS). Skip the All-Star Game
        # (gameType "A" — cross-league all-star squads with no season roster, so
        # no team profile to sim), spring training ("S") and exhibitions ("E").
        # These otherwise appear in the slate with no props, which is confusing.
        if g.get("gameType", "R") not in ("R", "F", "D", "L", "W"):
            continue
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
            "outs": ls.get("outs"),
            "away_runs": lt.get("away", {}).get("runs"),
            "home_runs": lt.get("home", {}).get("runs"),
            "away_hits": lt.get("away", {}).get("hits"),
            "home_hits": lt.get("home", {}).get("hits"),
        }
        # MLB flips abstractGameState to "Live" during warmups, and the linescore
        # already reads "Top 1st, 0-0" — an hour before first pitch. Every reader
        # (combo makers, the slate UI, the live banner) then treats a game that
        # hasn't started as under way: it gets dropped from combos and shown the
        # thin live leg set instead of its full pre-game props. Correct the state
        # here, at the one place the payload is built, so nothing downstream has
        # to know about the quirk.
        if state == "Live" and not _really_started(live, kalshi._parse_time(g.get("gameDate"))):
            state = "Preview"
            live["state"] = "Preview"
            live["is_live"] = False
            live["not_started"] = True
        out.append({
            "game_pk": g.get("gamePk"),
            "home_id": home["team"]["id"], "home_name": home["team"]["name"],
            "away_id": away["team"]["id"], "away_name": away["team"]["name"],
            "venue_id": g.get("venue", {}).get("id"),
            "home_sp_id": hp.get("id") if hp else None, "home_sp_name": hp.get("fullName") if hp else None,
            "away_sp_id": ap.get("id") if ap else None, "away_sp_name": ap.get("fullName") if ap else None,
            "start": g.get("gameDate"), "start_epoch": kalshi._parse_time(g.get("gameDate")),
            "status": st.get("detailedState", ""), "live": live,
            "game_number": g.get("gameNumber"),
            "doubleheader": g.get("doubleHeader") not in (None, "N"),
        })
    return out


_DEEP_WP_CACHE = {}


def _deep_game_wp(g, season, n=800, ump=0.0, frame=0.0):
    """Deep-engine win probability for TODAY'S exact matchup: the per-player
    engine (regressed + Statcast rates, arsenal matchups, batter platoon splits,
    TTO, real bullpen chains, pinch hitters, steals, real end-game rules) plays
    the two rosters n times with today's probable starters. This is the second
    opinion blended into the factor model's Pythagorean number -- it carries all
    the player-level data the factor model can't see. Returns None (honest skip)
    when a probable starter isn't identified or profiles aren't available.
    Seeded per game so the number is stable across page loads; cached 6h."""
    import time as _t
    key = (g["game_pk"], g.get("home_sp_id"), g.get("away_sp_id"), round(ump, 3), round(frame, 2))
    hit = _DEEP_WP_CACHE.get(key)
    if hit and _t.time() - hit[0] < 6 * 3600:
        return hit[1]
    try:
        import deep_data
        import deep_sim
        import random as _r
        ph = deep_data.team_profile(g["home_id"], season)
        pa = deep_data.team_profile(g["away_id"], season)
        if not ph or not pa or not ph.get("lineup") or not pa.get("lineup"):
            return None

        def find_sp(prof, pid):
            if not pid:
                return None
            return next((p for p in prof.get("rotation", []) + prof.get("bullpen", [])
                         if p.get("id") == pid), None)
        sp_h = find_sp(ph, g.get("home_sp_id"))
        sp_a = find_sp(pa, g.get("away_sp_id"))
        if sp_h is None or sp_a is None:
            return None
        rng = _r.Random((g["game_pk"] * 2654435761) & 0xFFFFFFFF)
        hw = 0
        for _ in range(n):
            hw += 1 if deep_sim.play_game(ph, pa, sp_home=sp_h, sp_away=sp_a,
                                          rng=rng, ump=ump, frame=frame)["home_win"] else 0
        wp = max(0.05, min(0.95, hw / n))
        _DEEP_WP_CACHE[key] = (_t.time(), wp)
        return wp
    except Exception:
        return None


# Blend weight: the factor model keeps the majority (it carries the graded ~55%
# track record); the deep engine's player-level read gets a meaningful minority
# vote until the recorder grades it on its own.
_DEEP_WP_WEIGHT = 0.35
_DEEP_W_SHRINK_N = 150     # graded games at which the fit gets an equal vote with the prior
_DEEP_W_MIN_N = 20         # below this, don't even fit -- the grid is reading noise
_DEEP_W_CACHE = {"t": 0.0, "w": None, "n": 0, "fit": None}


def _deep_wp_weight():
    """Evidence-tuned blend weight, SHRUNK toward the 0.35 prior by sample size:

        w_used = (n * w_fit + K * w_prior) / (n + K),  K = 150 graded games

    The previous version was a cliff: at 39 graded games the deep engine got its
    default 35% vote, at 40 a grid-search took over outright -- and on the first
    41 games the grid said 0.00, silently switching the whole per-player engine
    OFF on the strength of a sample whose Brier standard error (~0.078) dwarfed
    every difference in the grid (~0.012). One game decided a 35-point swing.
    Shrinkage makes the evidence buy exactly the influence it has earned: at
    n=41 a fitted 0.0 moves the weight to ~0.27, at n=300 to ~0.12, and only a
    persistent verdict over hundreds of games can actually zero the engine.
    Cached 6h; the cache also keeps n and the raw fit so the board can SAY what
    the blend is running on instead of moving silently."""
    import time as _t
    if _DEEP_W_CACHE["w"] is not None and _t.time() - _DEEP_W_CACHE["t"] < 6 * 3600:
        return _DEEP_W_CACHE["w"]
    w, n, fit = _DEEP_WP_WEIGHT, 0, None
    try:
        import store
        rows = store.deep_grades()
        n = len(rows)
        if n >= _DEEP_W_MIN_N:
            best = None
            for cand in (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35,
                         0.4, 0.45, 0.5, 0.55, 0.6, 0.65):
                b = sum(((1 - cand) * m + cand * d - hw) ** 2
                        for m, d, hw in rows) / n
                if best is None or b < best[0]:
                    best = (b, cand)
            fit = best[1]
            w = (n * fit + _DEEP_W_SHRINK_N * _DEEP_WP_WEIGHT) / (n + _DEEP_W_SHRINK_N)
    except Exception as _e:
        errlog.note("BB-deep_wp_weight", _e)
    _DEEP_W_CACHE.update(t=_t.time(), w=w, n=n, fit=fit)
    return w


def deep_blend_info():
    """What the daily blend is actually running on, for the board: the weight in
    use, the raw fitted value, and the graded-game count behind it. Reading the
    cache (warming it if cold) so this costs nothing on a served slate."""
    _deep_wp_weight()
    return {"w_deep": round(_DEEP_W_CACHE["w"], 3),
            "w_fitted": _DEEP_W_CACHE["fit"],
            "n_graded": _DEEP_W_CACHE["n"],
            "w_prior": _DEEP_WP_WEIGHT}


def stale_slate(date, season, max_age=3600):
    """The cached slate even if the TTL has lapsed, with its age in seconds, as
    (games, age) -- or (None, None) when there is nothing worth showing.

    The TTL is five minutes, which is exactly the length of an errand: leave the
    app to place a bet on the exchange, come back, and the board has always
    expired. Rebuilding from cold takes about a minute, and answering 202 in the
    meantime means the user stares at "simulating every game" every single time
    they return. A five-minute-old board is a much better answer than no board:
    serve it immediately, refresh it underneath, and let the next poll swap in
    the new numbers. Past `max_age` the staleness stops being cosmetic (lineups
    and prices have really moved), so it is withheld."""
    hit = _cache.get(("slate", date, season))
    if hit:
        age = _time.time() - hit[0]
        if max_age is None or age <= max_age:
            return hit[1], age
        return None, None
    return _slate_disk_get(date, season, max_age)   # a sibling may have it


def analyze_slate(date, season, cached_only=False):
    """The day's modelled slate. Cached, because a cold build simulates every game
    and takes ~54s on four fast cores -- minutes on a small instance.

    `cached_only` returns the cached board or None WITHOUT building, so a web
    handler can answer immediately and let the build happen in the background
    rather than holding a request open past the server's worker timeout."""
    key = ("slate", date, season)
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < _SLATE_TTL:
        return hit[1]
    # A sibling worker may already have built this board. Adopt it WITH ITS AGE,
    # so the TTL and stale_slate's cosmetics stay honest rather than restarting
    # the clock on a board that is minutes old.
    disk, age = _slate_disk_get(date, season, _SLATE_TTL)
    if disk is not None:
        _cache[key] = (_time.time() - age, disk, _SLATE_TTL)
        return disk
    if cached_only:
        return None

    # Share one build across every caller. analyze_slate is reached from the web
    # handler, the prop recorder's background loop, best-bets and the DFS board,
    # and each used to start its own -- so a user opening the tab while the
    # recorder ticked meant several full simulations of the same slate at once.
    # Measured: three concurrent callers, six child builds, 547 MB. The first
    # caller builds; the rest wait and take the same answer.
    with _slate_guard:
        ev = _slate_building.get(key)
        leader = ev is None
        if leader:
            ev = _threading.Event()
            _slate_building[key] = ev
    if not leader:
        ev.wait(timeout=_SLATE_BUILD_TIMEOUT)
        hit = _cache.get(key)
        if hit:
            return hit[1]
        return _analyze_slate_uncached(date, season)   # the leader failed
    try:
        if not _slate_claim(date, season):
            # A SIBLING WORKER is already building this board. Wait for its
            # publish instead of spawning a duplicate ~175 MB child on the same
            # CPU; if it dies, the stale claim is taken over next call.
            deadline = _time.time() + _SLATE_BUILD_TIMEOUT
            while _time.time() < deadline:
                disk, age = _slate_disk_get(date, season, _SLATE_TTL)
                if disk is not None:
                    _cache[key] = (_time.time() - age, disk, _SLATE_TTL)
                    return disk
                _time.sleep(5)
            return _analyze_slate_uncached(date, season)   # builder died mid-run
        try:
            with deep_cache_gate():
                out = _analyze_slate_isolated(date, season)
            _cache[key] = (_time.time(), out, _SLATE_TTL)
            _slate_disk_put(date, season, out)     # share it with the other workers
            return out
        finally:
            _slate_release(date, season)
    finally:
        with _slate_guard:
            _slate_building.pop(key, None)
        ev.set()


import threading as _threading

_slate_guard = _threading.Lock()
_slate_building = {}


def deep_cache_gate():
    """The app-wide one-heavy-build-at-a-time gate, or a no-op if unavailable."""
    try:
        import deep_cache
        return deep_cache.HEAVY_BUILD
    except Exception:
        import contextlib
        return contextlib.nullcontext()


def _slate_blob(date, season):
    """Child entry point: the slate as a pickle. Module level so a subprocess can
    import and call it."""
    import pickle
    return pickle.dumps(_analyze_slate_uncached(date, season),
                        protocol=pickle.HIGHEST_PROTOCOL)


def _analyze_slate_isolated(date, season):
    """Build the slate in a SEPARATE PROCESS and bring back just the result.

    Building costs vastly more memory than the answer occupies. Simulating ten
    games allocates millions of tiny short-lived objects; measured, the process
    goes from 34 MB to ~175 MB and STAYS there -- clearing the cache and forcing
    a collection recovers almost none of it, because the survivors leave pymalloc
    arenas too fragmented to release (malloc_trim gets ~10 MB back). It plateaus
    rather than growing without bound, but it is a permanent ~140 MB that a
    512 MB instance cannot spare on top of the Elo pools and the season sim.

    The finished board is 0.5 MB. So the child does the allocating, the parent
    gets the answer, and the OS reclaims the rest when the child exits.

    A plain subprocess rather than multiprocessing, for the same reasons as
    tennis_elo._build_isolated: 'fork' can inherit a lock held by another thread
    of a threaded server, and 'spawn' re-imports the parent's __main__, so any
    script that reaches this would re-run itself inside the child. Falls back to
    building in-process if the subprocess cannot run -- heavier, never broken."""
    import pickle
    import subprocess
    import sys
    try:
        out = subprocess.run(
            # The child NICES ITSELF before importing anything. A slate build
            # saturates a CPU for the better part of a minute, and on a
            # one-core box that is in direct competition with the web worker --
            # including the platform's health probe, which is killed at five
            # seconds and takes the whole instance down with it. At +10 the
            # rebuild only ever gets the CPU nobody else wants, so warming the
            # board can never cost a request. (The child nices itself rather
            # than the parent using preexec_fn, which is documented as unsafe
            # in a threaded process -- and this runs from a background thread.)
            [sys.executable, "-c",
             "import os; os.nice(10)\n"
             "import sys, baseball; sys.stdout.buffer.write("
             "baseball._slate_blob(sys.argv[1], sys.argv[2]))",
             str(date), str(season)],
            cwd=_os.path.dirname(_os.path.abspath(__file__)),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=_SLATE_BUILD_TIMEOUT)
        if out.returncode == 0 and out.stdout:
            return pickle.loads(out.stdout)
        errlog.note("SLATE-child", msg=f"build child exited rc={out.returncode}"
                    f" with {len(out.stdout or b'')} bytes; building in-process")
    except Exception as _e:
        errlog.note("SLATE-child", _e)
    return _analyze_slate_uncached(date, season)


_SLATE_BUILD_TIMEOUT = 600


# Short enough that live scores and prices stay fresh, long enough that a page
# refresh doesn't pay for another full slate simulation.
_SLATE_TTL = 300


def _analyze_slate_uncached(date, season):
    schedule = _schedule(date, season)
    hit = _hitting_map(season); pit = _pitching_map(season)
    bp = _bullpen_map(season); hitplat = _hitting_platoon(season)
    try:
        import savant
        xstats = savant.expected_stats(season)   # Statcast xBA/xSLG by player id
        speed = savant.sprint_speed(season)      # sprint speed (ft/s) by player id
    except Exception:
        xstats = {}; speed = {}
    rec = _records_map(season); abbr_map = _abbr_map(season)
    # Last-10 form for every hitter in the league in one request (~3s, cached an
    # hour). Empty on any failure, which every consumer reads as "no form data"
    # and falls back to the season line.
    try:
        form_map = (mlb_form.form(season) or {}).get("hitting") or {}
    except Exception:
        form_map = {}
    lg = _league_avgs(hit, pit, bp, hitplat)
    try:
        pen_fatigue = _bullpen_fatigue(date, season)   # {team_id: {factor, count, arms}}
        sb_def = _sb_defense_map(season)               # {team_id: SB%% allowed, "_lg": lg}
        team_k = _team_k_map(season)                   # {team_id: lineup SO/PA, "_lg": lg}
        try:
            framing = savant.catcher_framing() or {}
        except Exception:
            framing = {}
        # TRAVEL/FATIGUE: measured and deliberately NOT modeled. On 3,574
        # 2026 team-games, short-rest travel east showed -0.16 +/- 0.49 runs
        # (noise), day-after-night scored HIGHER (+0.13 -- that is "day games
        # are warmer", which the weather model already carries), and no
        # schedule condition cleared its own error bars. A fatigue factor
        # here would be folklore wearing a coefficient.
        try:
            park_hand = savant.handed_hr_factors()     # {club: {"L": res, "R": res}}
        except Exception:
            park_hand = {}
    except Exception:
        pen_fatigue = {}
    def_map = _defense_map(season)                     # {team_id: hit/run multiplier}
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
        # Statcast xERA rides into each starter's record: contact quality FIP
        # can't see and ERA only sees through luck (idempotent on cached recs).
        try:
            pxs = savant.pitcher_expected_stats(season)
            for pid, st in sp_stats.items():
                if st and st.get("season") and pxs.get(str(pid), {}).get("xera"):
                    st["season"]["xera"] = pxs[str(pid)]["xera"]
        except Exception as e:
            errlog.note("MLB-pitcher-xera", e)
        # VELOCITY-DROP FATIGUE FLAG: each starter's last-start fastball velo
        # vs his season average. Velocity moves BEFORE results do -- a starter
        # down 1.5 mph is tired or hurt and his ERA hasn't heard yet. Attached
        # to the record (idempotent), applied in _starter_ra9, shown on the
        # card. Needs 8+ fastballs in the outing to beat start-to-start noise.
        try:
            vb = savant.velo_baselines(season)
            since = (_dt.fromisoformat(str(date)[:10]) - _td(days=16)).date().isoformat()

            def _velo_one(pid):
                base = vb.get(str(pid))
                if not base:
                    return None
                last = savant.last_start_velo(pid, since)
                if not last or last["n"] < 8 or last["type"] not in base:
                    return None
                d = round(last["velo"] - base[last["type"]], 1)
                return {"delta": d, "recent": last["velo"],
                        "season": base[last["type"]], "type": last["type"],
                        "n": last["n"], "date": last["date"]}
            memo = _velo_memo_get(date)
            missing = [pid for pid in sp_ids if pid not in memo]
            if missing:
                with ThreadPoolExecutor(max_workers=8) as ex:
                    for pid, v in zip(missing, ex.map(_velo_one, missing)):
                        memo[pid] = v
                _velo_memo_put(date, memo)
            for pid in sp_ids:
                v = memo.get(pid)
                if v and sp_stats.get(pid):
                    sp_stats[pid]["velo"] = v
        except Exception as e:
            errlog.note("MLB-velo-flag", e)

    # Posted lineups (rest/injuries) and game-time weather, fetched in parallel.
    pks = [g["game_pk"] for g in schedule if g["game_pk"]]
    lineups = {}
    if pks:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for pk, lu in zip(pks, ex.map(_boxscore_lineup, pks)):
                lineups[pk] = lu

    # Named bullpens from the deep engine's shared player profiles -> feeds the
    # combo pitching sim with real relievers (K-rates) instead of anonymous draws.
    # IL-aware + cached (same data the 4,000-season deep sim runs on). Best-effort.
    bp_arms = {}
    tids = list({g["home_id"] for g in schedule} | {g["away_id"] for g in schedule})

    def _arms(tid):
        try:
            import deep_data
            prof = deep_data.team_profile(tid, season)
            # deep_data ranks relievers WORST-FIRST (best arm last, so the sim
            # holds the closer back for late innings). A plain [:8] therefore
            # kept the eight WORST arms and threw the closer away: on a 10-deep
            # pen the modelled bullpen had no Munoz, no Bender, no Diaz. Half the
            # teams sampled were missing their best arm entirely, which quietly
            # inflated every run total against a deep pen. [-8:] keeps the eight
            # BEST and leaves them worst-first, which is the order the sim wants.
            #
            # `id` rides along so the fatigue layer can sit the arm that is
            # actually gassed rather than a count off the top of the pen.
            return [{"kpa": r["kpa"], "era": r["era"], "id": r.get("id"),
                     "name": r.get("name")} for r in prof.get("bullpen", [])][-8:]
        except Exception:
            return None
    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for tid, arms in zip(tids, ex.map(_arms, tids)):
                bp_arms[tid] = arms
    except Exception:
        bp_arms = {}

    def fetch_weather(g):
        s = stadiums_mod.STADIUMS.get(g["home_id"])
        if not s:
            return None
        wx = weather_mod.get_weather(s["lat"], s["lon"], g["start_epoch"] or _time.time())
        factor, wind_comp = weather_mod.run_factor(wx, s["cf_bearing_deg"], s["roof"],
                                                   home_id=g["home_id"])
        return {"stadium": s, "wx": wx, "factor": factor, "wind_out_mph": wind_comp,
                "hr_extra": weather_mod.hr_extra(wx, s["roof"], home_id=g["home_id"]),
                # P(roof closed) for retractable parks -- the board says it
                # instead of silently half-weighting the weather.
                "roof_closed_pct": weather_mod.roof_closed_pct(g["home_id"], wx)}
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
    _predlog_rows = []
    import predlog as predlog_mod
    # Per-start K logs for every listed starter, fetched once for the slate and
    # concurrently — one call per pitcher, the same shape as the sp_stats fan-out
    # right above. Best-effort: a card without one just omits the season line.
    klogs = {}
    _kpids = [p for p in ({g.get("home_sp_id") for g in schedule}
                          | {g.get("away_sp_id") for g in schedule}) if p]
    if _kpids:
        try:
            with ThreadPoolExecutor(max_workers=8) as ex:
                for pid, kl in zip(_kpids, ex.map(lambda p: _k_log(p, season), _kpids)):
                    klogs[pid] = kl
        except Exception:
            klogs = {}
    for g in schedule:
        h_sp = sp_stats.get(g["home_sp_id"]); a_sp = sp_stats.get(g["away_sp_id"])
        h_hand = hand.get(g["home_sp_id"], "R"); a_hand = hand.get(g["away_sp_id"], "R")

        # Each offense faces the OPPOSING starter for however long HE actually
        # pitches -- his platoon hand and his quality weight both scale with it.
        off_h = _offense_factor(th(g["home_id"]), ops_hand(g["home_id"], a_hand), a_hand, lg,
                                sp_share=_sp_share(a_sp))
        off_a = _offense_factor(th(g["away_id"]), ops_hand(g["away_id"], h_hand), h_hand, lg,
                                sp_share=_sp_share(h_sp))
        fat_a = pen_fatigue.get(g["away_id"]) or {}
        fat_h = pen_fatigue.get(g["home_id"]) or {}
        pit_a_factor, a_sp_ra9, a_bp_ra9 = _pitching_factor(a_sp, tbp(g["away_id"]), lg, fat_a.get("factor", 1.0))
        pit_h_factor, h_sp_ra9, h_bp_ra9 = _pitching_factor(h_sp, tbp(g["home_id"]), lg, fat_h.get("factor", 1.0))

        # Posted-lineup adjustment to each offense (rest days, call-ups, injuries).
        lu = lineups.get(g["game_pk"]) or {}
        lf_home, lops_home = _lineup_factor(lu.get("home"), th(g["home_id"]).get("ops"), lg)
        lf_away, lops_away = _lineup_factor(lu.get("away"), th(g["away_id"]).get("ops"), lg)
        confirm = _confirm_status(g, lu)
        if lf_home:
            off_h *= lf_home
        if lf_away:
            off_a *= lf_away

        # Team defense (Statcast OAA): the FIELDING team suppresses the opposing
        # offense's hits on balls in play, so each team's expected runs also ride
        # on the OTHER team's glove.
        def_h = def_map.get(str(g["home_id"]), 1.0)
        def_a = def_map.get(str(g["away_id"]), 1.0)
        er_home = lg["rpg"] * off_h * pit_a_factor * _HOME_SPLIT * def_a
        er_away = lg["rpg"] * off_a * pit_h_factor * def_h / _HOME_SPLIT

        # Park + weather scale BOTH teams' run environment. Baking them into the
        # expected runs (not just the headline total) keeps every downstream
        # consumer consistent: the totals/run-line ladders, RFI, the Monte Carlo
        # (which calibrates lineups to these ERs), hitter props and the live
        # remaining-runs model all see the same environment. The moneyline is
        # untouched — a common multiplier cancels in the Pythagorean ratio.
        park = _park_factor(g["home_id"], season)
        winfo = weather_by_pk.get(g["game_pk"]) or {}
        wx_factor = winfo.get("factor", 1.0)
        env = max(0.75, min(1.40, park * wx_factor))
        er_home *= env
        er_away *= env

        # Home-plate umpire zone (ABS-challenge-damped net effect). Applied to the
        # run total only — NOT to `env` above, which is a ball-flight scale for hit
        # props; an ump moves runs through Ks/walks, not BABIP. Hits both offenses,
        # so it cancels in the Pythagorean moneyline but shifts the total + run
        # props. 0.0 (no ump posted / no tendency on file) is a no-op. The deep sim
        # gets the full per-PA challenge mechanic; here we use the net run effect.
        ump_bias, ump_prof = 0.0, None
        if (g.get("live") or {}).get("state") == "Preview":
            try:
                import umpires
                ump_prof = umpires.game_profile(g["game_pk"])
                ump_bias = float((ump_prof or {}).get("bias") or 0.0)
            except Exception:
                ump_bias, ump_prof = 0.0, None
        if ump_bias:
            # Prefer the slope MEASURED when the tendency table was built (runs
            # per unit of bias, over a season of finished games) and fall back to
            # the constant only when no table exists yet.
            umpr = _UMP_RUN
            try:
                rb = umpires.slope("r")          # None unless it is significant
                lgr = umpires.meta().get("lg_r_per_game")
                if rb is not None and lgr:
                    umpr = -rb / lgr
            except Exception as _e:
                errlog.note("BB-analyze_slate_uncached", _e)
            umpf = max(0.90, min(1.10, 1.0 - umpr * ump_bias))
            er_home *= umpf
            er_away *= umpf
        # The same zone reaches the STRIKEOUT ladders directly. game_profile
        # already carries k_effect -- whole-game Ks (both staffs) per the slope
        # MEASURED when the tendency table was built -- and only the run side
        # was being consumed. Half the effect to each staff over a ~8.5 K/game
        # baseline, clamped small; one ump serves both ladders, so the two
        # starters' K lines move TOGETHER (a K-over stack under a tight zone
        # is doubly wrong, and now prices that way).
        ump_k_mult = 1.0
        _ke = (ump_prof or {}).get("k_effect")
        if _ke:
            ump_k_mult = max(0.95, min(1.05, 1.0 + (float(_ke) / 2.0) / 8.5))

        p_home = er_home ** PYTH_EXP / (er_home ** PYTH_EXP + er_away ** PYTH_EXP)
        p_home = max(0.04, min(0.96, p_home))
        # Deep-engine second opinion (pre-game only): blend the per-player game
        # sim's win prob into the factor model so the daily winner sees ALL the
        # player-level data (xStats, arsenals, platoon splits, TTO, bullpen
        # chains, PH, steals). Both numbers ride along in the payload.
        p_home_model = p_home
        p_home_deep = None
        if (g.get("live") or {}).get("state") == "Preview":
            p_home_deep = _deep_game_wp(g, season, ump=ump_bias)
            if p_home_deep is not None:
                w_deep = _deep_wp_weight()
                p_home = (1 - w_deep) * p_home + w_deep * p_home_deep
                p_home = max(0.04, min(0.96, p_home))
        # The RAW (pre-calibration) blended prob. Recorded alongside the calibrated
        # one so the calibrator fits its temperature on the model's own raw output,
        # not on the number it already corrected (which would be a feedback loop).
        p_home_raw = p_home
        # Reality-calibrate the pre-game win prob against our graded record — the
        # model runs overconfident on the high end, so temperature scaling reins
        # the tails toward what actually happens (self-tuning; a no-op until enough
        # games grade). The in-game prob below is its own live calc, left alone.
        if (g.get("live") or {}).get("state") == "Preview":
            import calibrate
            p_home = max(0.04, min(0.96, calibrate.win_prob(p_home)))
        p_away = 1 - p_home
        exp_total = round(er_home + er_away, 1)

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
            p_home_raw = p_home   # live prob is uncalibrated: raw == shown
            in_game = {"home_score": ls["home_runs"], "away_score": ls["away_runs"],
                       "inning": ls["inning"], "state": ls["state"], "outs": ls["outs"],
                       "exp_rem_home": round(exp_rem_h, 2), "exp_rem_away": round(exp_rem_a, 2),
                       "on_base": [b for b, on in (("1B", ls["on1"]), ("2B", ls["on2"]),
                                                   ("3B", ls["on3"])) if on]}

        pick_home = p_home >= p_away
        pick_name = g["home_name"] if pick_home else g["away_name"]
        pick_prob = p_home if pick_home else p_away
        # Same pick, before calibration — the honest training signal for the fit.
        pick_prob_raw = p_home_raw if pick_home else (1 - p_home_raw)

        price_entry, home_abbr, away_abbr = _match_price(
            kalshi_index, abbr_map, g["home_id"], g["away_id"], g["start_epoch"])
        # Log the pre-game prediction beside the price it disagreed with. MLB was
        # the ONLY sport with no prediction log -- the flagship board, graded
        # nowhere. Raw (pre-calibration) prob, per the convention every other
        # harvester follows, so the calibrator fits on the model's own output and
        # not on a number it already corrected; predlog dedups by ticker, so the
        # slate rebuilding every few minutes logs each game once, at first price.
        # Pregame only: a live in-game prob is a different quantity with the
        # score already in it, and grading it as a pregame call would flatter us.
        if price_entry and (g.get("live") or {}).get("state") == "Preview":
            hc = price_entry["prices"].get(home_abbr)
            ac = price_entry["prices"].get(away_abbr)
            for ab, p_side, own, opp in ((home_abbr, p_home_raw, hc, ac),
                                         (away_abbr, 1 - p_home_raw, ac, hc)):
                if ab and own is not None:
                    _predlog_rows.append((f"{price_entry['event']}-{ab}", p_side,
                                          price_entry.get("close"),
                                          predlog_mod.devig(own, opp)))
        edge = market_prob = pick_price = fee_cents = net_edge = None
        fair_prob = edge_vs_fair = vig_cents = None
        if price_entry:
            pick_abbr = home_abbr if pick_home else away_abbr
            opp_abbr = away_abbr if pick_home else home_abbr
            pick_price = price_entry["prices"].get(pick_abbr)
            opp_price = price_entry["prices"].get(opp_abbr)
            if pick_price is not None:
                market_prob = round(pick_price, 1)
                edge = round(pick_prob * 100 - pick_price, 1)
                fee_cents = _kalshi_fee(pick_price)
                net_edge = round(edge - fee_cents, 1)
                # De-vig: both team asks sum to >100 (the overround). The fair price
                # strips it out, so `edge_vs_fair` is the model's genuine
                # disagreement with the market — NOT the vig you pay on any bet.
                if opp_price is not None and (pick_price + opp_price) > 0:
                    fair_prob = round(100.0 * pick_price / (pick_price + opp_price), 1)
                    edge_vs_fair = round(pick_prob * 100 - fair_prob, 1)
                    vig_cents = round(pick_price + opp_price - 100, 1)

        def sp_block(name, st, h):
            if not st or "season" not in st:
                return {"name": name, "hand": h, "era": None, "whip": None, "ip": None,
                        "recent_era": None, "recent_whip": None, "k9": None}
            s = st["season"]; r = st.get("recent") or {}
            fip = _fip(s)
            # Model K/9 is regressed by sample size (so small-sample arms don't
            # over-project); k9_raw keeps his true stat-line number for display.
            k9_raw = round(s.get("k9", 0), 1)
            k9_mod = _regressed_k9(s)
            wl = _starter_workload(st) or {}
            return {"name": name, "hand": h, "era": round(s["era"], 2), "whip": round(s["whip"], 2),
                    "velo": st.get("velo"),
                    "ip": s["ip"], "k9": round(k9_mod, 1) if k9_mod else k9_raw, "k9_raw": k9_raw,
                    "exp_ip": round(_exp_ip_per_start(st), 1),
                    "est_ip": wl.get("est_ip"), "est_pitches": wl.get("est_pitches"),
                    "pip": wl.get("pip"), "bb9": wl.get("bb9"), "bb_pa": wl.get("bb_pa"),
                    "fip": round(fip, 2) if fip is not None else None,
                    "recent_era": round(r["era"], 2) if r.get("ip") else None,
                    "recent_whip": round(r["whip"], 2) if r.get("ip") else None,
                    "recent_ip": r.get("ip")}

        def sp_ks(sp, pid, ks):
            """His real per-start K log plus the closed-form projection for
            tonight. The SIMULATED number is merged in by _attach_sim_ks below,
            once the game dict exists for the sim to read."""
            out = dict(klogs.get(pid) or {})
            if ks and ks.get("expected") is not None:
                out["proj"] = ks["expected"]        # closed form behind the K ladder
            return out or None

        rh = rec.get(g["home_id"], {}); ra = rec.get(g["away_id"], {})
        bph = tbp(g["home_id"]); bpa = tbp(g["away_id"])

        # Derived props: run line + game totals from the run distribution, and
        # per-batter hit odds from the posted lineups (when available).
        gp = props_mod.game_props(er_home, er_away, home_abbr or g["home_id"], away_abbr or g["away_id"])
        hit_home = hit_away = None
        bat_home = bat_away = None
        # Hits scale roughly with the square root of the run environment (Coors
        # lifts runs ~28% but hits ~13%), so hitter props see env^0.55 on top of
        # the opposing pitching. Both lineups share the same park/weather.
        hit_env = env ** 0.55
        # HR carry is steeper than contact: singles ride env^0.55, homers ~env^1.0.
        # hr_env is the EXTRA multiplier (env^0.45) applied on top of the hit-scale
        # already in ohf, so a hot/thin-air park lifts homers about twice as much.
        # Weather hits homers harder than it hits runs (measured 1.3%/F vs
        # 0.55%/F), so after env takes the run share, HR ladders get the rest.
        # Per-park HR beyond the run level: Statcast's hr/runs factor ratio
        # (Yankee's short porch, Dodger carry vs Oracle's graveyard) -- the run
        # environment alone can't see a park that turns doubles into homers.
        hr_env = env ** 0.45 * (winfo.get("hr_extra") or 1.0) * _park_hr_ratio(g["home_id"], season)
        # home bats vs away pitching + away defense (and vice versa).
        ohf_home = _opp_hit_factor(a_sp, tbp(g["away_id"]), lg) * hit_env * def_a
        ohf_away = _opp_hit_factor(h_sp, tbp(g["home_id"]), lg) * hit_env * def_h
        # PARK GEOMETRY BY HANDEDNESS. Statcast splits each park's HR factor by
        # batter side; dividing by the park's own two-side mean leaves only the
        # residual (Orioles: LHB 1.18, RHB 0.82 of the park average), so the
        # park's overall level -- already in hr_env -- is not counted twice. A
        # switch hitter bats opposite the starter's hand. Missing park or side
        # degrades to 1.0.
        res = {}
        for club, r in (park_hand or {}).items():
            key = "Diamondbacks" if club == "D-backs" else club
            if g.get("home_name", "").endswith(key):
                res = r
                break

        def _hr_env_for(b, opp_hand):
            side = b.get("bat_side")
            if side == "S":
                side = "L" if opp_hand == "R" else "R"
            f = (res or {}).get(side or "")
            return hr_env * f if f else hr_env

        def bat_list(lineup, ohf, opp_hand="R"):
            sides = _bat_sides([b.get("id") for b in lineup])
            out = []
            for i, b in enumerate(lineup):
                pid = str(b.get("id"))
                b["bat_side"] = sides.get(b.get("id"))
                cm, pm = savant.quality_mults(xstats.get(pid))
                bp_ = props_mod.batter_props(b, i, ohf, cm, pm, sprint=speed.get(pid),
                                             hr_env=_hr_env_for(b, opp_hand))
                if bp_:
                    bp_["bat_side"] = b.get("bat_side")   # ride through for the UI/audits
                    out.append(bp_)
            return out
        # Posted lineup when it's out; otherwise the team's last posted order
        # (their regulars) so batter props exist all morning. Confirm status
        # still says 'projected' either way until the real card posts.
        lu_home = _with_form(lu.get("home") or _last_posted_lineup(g["home_id"], date),
                             form_map)
        lu_away = _with_form(lu.get("away") or _last_posted_lineup(g["away_id"], date),
                             form_map)
        if lu_home:
            hit_home = props_mod.hit_props(lu_home, ohf_home)
            bat_home = bat_list(lu_home, ohf_home, opp_hand=a_hand)
        if lu_away:
            hit_away = props_mod.hit_props(lu_away, ohf_away)
            bat_away = bat_list(lu_away, ohf_away, opp_hand=h_hand)
        # Starter strikeout props, sized to THIS pitcher's expected workload
        # (season/recent IP per start), not a one-size 5.6-inning template.
        wl_h, wl_a = _starter_workload(h_sp), _starter_workload(a_sp)
        # THE UMPIRE MOVES STRIKEOUTS, not just runs. A bigger zone is more
        # called strikes, and the K ladder is the market that feels it most
        # directly -- it used to see nothing at all, so a Doug Eddings start and
        # a Willie Traynor start priced identically. `k_per_bias` is the measured
        # whole-game slope; one starter owns w/2 of it.
        ump_k = 0.0
        if ump_bias:
            try:
                kb = umpires.slope("k")          # None unless it is significant
                if kb is not None:
                    ump_k = kb * ump_bias * _UMP_K_STARTER_SHARE
            except Exception:
                ump_k = 0.0

        def _k_props(sp, wl):
            if not sp:
                return None
            ip = (wl or {}).get("est_ip") or _exp_ip_per_start(sp)
            k9 = _regressed_k9(sp.get("season"))
            if ump_k and k9 and ip > 0:
                # Fold the umpire's Ks into the RATE, so the whole ladder moves
                # with it rather than only the headline expectation.
                k9 = max(2.0, k9 + ump_k * 9.0 / ip)
            return props_mod.pitcher_k_props(k9, ip,
                                             est_pitches=(wl or {}).get("est_pitches"))
        ks_home = _k_props(h_sp, wl_h)
        ks_away = _k_props(a_sp, wl_a)
        game_props = {"run_line": gp["run_line"], "totals": gp["totals"],
                      "totals_ladder": gp["totals_ladder"],
                      "model_total": gp["model_total"], "rfi_pct": gp.get("rfi_pct"),
                      "hits_home": hit_home, "hits_away": hit_away,
                      "batters_home": bat_home, "batters_away": bat_away,
                      "ks_home": ks_home, "ks_away": ks_away,
                      "home_sp_name": g.get("home_sp_name"), "away_sp_name": g.get("away_sp_name")}
        # Live-decaying odds: once a game is underway the pre-game totals/run line are
        # stale (they ignore runs already scored). Recompute the displayed totals +
        # run line from the current score plus expected remaining runs so the odds the
        # user sees tick with the game. `props_live` flags the swap for the UI.
        if in_game and in_game.get("exp_rem_home") is not None:
            lp = props_mod.live_game_props(
                in_game["home_score"], in_game["away_score"],
                in_game["exp_rem_home"], in_game["exp_rem_away"],
                home_abbr or g["home_id"], away_abbr or g["away_id"])
            lt = lp["model_total"]
            game_props["run_line"] = lp["run_line"]
            game_props["totals_ladder"] = lp["totals_ladder"]
            game_props["model_total"] = lt
            wanted = {round(lt) - 0.5, round(lt) + 0.5, 8.5}
            game_props["totals"] = [t for t in lp["totals_ladder"] if t["line"] in wanted]
            game_props["props_live"] = True
        # Kill phantom lines Kalshi won't book: trim game totals + run-line ladders
        # to the lines the sportsbook actually offers (with a sane fallback when the
        # Kalshi index is unavailable). Player-prop bet surfaces (Edge Finder, combo
        # maker) already drop any leg without a live Kalshi price via price_leg.
        gctx = {"kalshi_suffix": (price_entry["event"].split("-", 1)[1]
                                  if price_entry and price_entry.get("event") and "-" in price_entry["event"]
                                  else None)}
        _validate_game_props(game_props, gctx)

        games.append({
            "live": g["live"],
            "in_game": in_game,
            "props": game_props,
            "game_pk": g["game_pk"],
            "matchup": f"{g['away_name']} @ {g['home_name']}"
                       + (f" (Game {g['game_number']})"
                          if g.get("doubleheader") and g.get("game_number") else ""),
            "away_name": g["away_name"], "home_name": g["home_name"],
            "away_abbr": away_abbr, "home_abbr": home_abbr,
            "kalshi_suffix": (price_entry["event"].split("-", 1)[1]
                              if price_entry and price_entry.get("event") and "-" in price_entry["event"]
                              else None),
            "start": g["start"], "status": g["status"],
            "confirm": confirm,
            "p_home": round(p_home, 4), "p_away": round(p_away, 4),
            "p_home_model": round(p_home_model, 4),
            "p_home_deep": round(p_home_deep, 4) if p_home_deep is not None else None,
            "exp_runs_home": round(er_home, 2), "exp_runs_away": round(er_away, 2),
            # The same expected runs with the home-field tilt DIVIDED BACK OUT.
            #
            # HOME_RUNS_MULT exists because the closed-form Pythagorean win prob
            # cannot see the rules of baseball: it works on run totals alone, so
            # the only way to make it produce a realistic home win rate is to hand
            # the home team more runs. The SIMULATOR has no such limitation -- it
            # plays the bottom of the 9th only when the home team needs it and
            # ends on a walk-off -- and those rules generate the home edge by
            # themselves. Measured with identical run inputs on both sides, the
            # sim returns 53.35% home wins against a real MLB 52.89%. It is
            # already right, from first principles.
            #
            # Feeding it the tilted runs on top of that counted the advantage
            # twice: +2.9pp over the closed form on all 15 games of a slate, and
            # against the market a HOME mean of +2.0pp versus an AWAY mean of
            # -2.1pp -- a 4.1pp tilt that the pooled median (+0.7pp) hid entirely,
            # because the two sides cancel.
            "exp_runs_home_talent": round(er_home / _HOME_SPLIT, 4),
            "exp_runs_away_talent": round(er_away * _HOME_SPLIT, 4),
            "exp_total": exp_total, "park_factor": park,
            "weather": _weather_block(winfo),
            "umpire": ump_prof,
            "ump_k_mult": round(ump_k_mult, 4),
            "home_sp": sp_block(g["home_sp_name"], h_sp, h_hand),
            "away_sp": sp_block(g["away_sp_name"], a_sp, a_hand),
            "home_sp_ks": sp_ks(h_sp, g.get("home_sp_id"), ks_home),
            "away_sp_ks": sp_ks(a_sp, g.get("away_sp_id"), ks_away),
            "home_team": {"ops": round(th(g['home_id']).get("ops", 0), 3),
                          "ops_vs_opp_hand": round(ops_hand(g['home_id'], a_hand) or 0, 3),
                          "rpg": round(th(g['home_id']).get("rpg", 0), 2),
                          "bullpen_era": round(bph.get("era", 0), 2), "bullpen_whip": round(bph.get("whip", 0), 2),
                          "bp_arms": bp_arms.get(g['home_id']),
                          "sb_allow_pct": sb_def.get(g['home_id']),
                          "sb_lg_pct": sb_def.get("_lg"),
                          "bat_k_pct": team_k.get(g['home_id']),
                          "bat_k_lg": team_k.get("_lg"),
                          "frame_k": framing.get(g['home_id']),
                          "bullpen_fatigue": fat_h or None,
                          "lineup_factor": round(lf_home, 3) if lf_home else None, "lineup_ops": lops_home,
                          "wins": rh.get("wins"), "losses": rh.get("losses"), "run_diff": rh.get("run_diff")},
            "away_team": {"ops": round(th(g['away_id']).get("ops", 0), 3),
                          "ops_vs_opp_hand": round(ops_hand(g['away_id'], h_hand) or 0, 3),
                          "rpg": round(th(g['away_id']).get("rpg", 0), 2),
                          "bullpen_era": round(bpa.get("era", 0), 2), "bullpen_whip": round(bpa.get("whip", 0), 2),
                          "bp_arms": bp_arms.get(g['away_id']),
                          "sb_allow_pct": sb_def.get(g['away_id']),
                          "sb_lg_pct": sb_def.get("_lg"),
                          "bat_k_pct": team_k.get(g['away_id']),
                          "bat_k_lg": team_k.get("_lg"),
                          "frame_k": framing.get(g['away_id']),
                          "bullpen_fatigue": fat_a or None,
                          "lineup_factor": round(lf_away, 3) if lf_away else None, "lineup_ops": lops_away,
                          "wins": ra.get("wins"), "losses": ra.get("losses"), "run_diff": ra.get("run_diff")},
            "pick": pick_name, "pick_is_home": pick_home,
            "pick_prob": round(pick_prob, 4), "pick_pct": round(pick_prob * 100, 1),
            "pick_prob_raw": round(pick_prob_raw, 4),
            "confidence": round(abs(pick_prob - 0.5) * 200),
            "pick_price_cents": pick_price, "market_prob": market_prob, "edge_cents": edge,
            "fee_cents": fee_cents, "net_edge_cents": net_edge,
            "fair_prob": fair_prob, "edge_vs_fair": edge_vs_fair, "vig_cents": vig_cents,
        })
    if _predlog_rows:
        try:
            predlog_mod.init_db()
            predlog_mod.log_many("mlb", _predlog_rows)
        except Exception as _e:
            errlog.note("BB-analyze_slate_uncached-2", _e)  # a logging hiccup must never cost the user his slate
    _attach_sim_ks(games)
    games.sort(key=lambda x: x["pick_prob"], reverse=True)
    return games


def _attach_sim_ks(games):
    """Merge each starter's SIMULATED strikeout line onto his card block — but
    ONLY for games whose sim is already in the cache.

    The first cut of this ran the slate's sims itself, concurrently. It worked on
    a warm box and got the process OOM-killed on a cold one: a 4,000-iteration
    game sim is ~26 MB of retained arrays, six in flight is most of the
    instance's headroom, and the failure is a dead worker rather than a slow one.
    This module had already learned that lesson once -- build_combos was taken
    off the slate load for the same reason, in the same units -- and this walked
    straight back into it.

    So the sim is never built here. It is read when the edge finder or the combo
    maker has already paid for it, which is the common case once the user does
    anything, and the card falls back to the closed-form projection until then.
    The two agree within about half a strikeout (measured across a slate), so the
    fallback is a rounding difference, not a different answer."""
    import mlb_sim
    for g in games:
        if _game_state(g) != "Preview":
            continue
        gs = _peek(("game_sim", g.get("game_pk")))
        if not gs:
            continue
        try:
            lines = mlb_sim._pitchers(g, gs["sim"])
        except Exception:
            continue
        for p in lines or []:
            if not p:
                continue
            for side in ("home", "away"):
                blk = g.get(f"{side}_sp_ks")
                if not blk or (g.get(f"{side}_sp") or {}).get("name") != p["name"]:
                    continue
                blk["sim_k"] = p["exp_k"]
                blk["sim_ip"] = p["avg_ip"]
                blk["sim_pitches"] = p["avg_pitches"]
                blk["k_dist"] = p["k_dist"]
    # vs_avg is measured against whatever number the card actually leads with,
    # so it has to be recomputed once a sim value is in.
    for g in games:
        for side in ("home", "away"):
            blk = g.get(f"{side}_sp_ks") or {}
            shown = blk.get("sim_k", blk.get("proj"))
            if shown is not None and blk.get("avg") is not None:
                blk["vs_avg"] = round(shown - blk["avg"], 1)


def _game_state(g):
    return (g.get("live") or {}).get("state") or ""


# Shared game-sim depth. The edge finder, the combo maker AND the same-game
# parlays all read this one 4000-run simulation per game (cached below), so every
# surface agrees and each game is simulated once per cycle -- not separately by
# each feature. Combos used to read a closed-form Poisson that ignored the
# starter's early hook and so overstated high strikeout lines (6+ Ks 58% vs the
# sim's 49%); sourcing them from the sim fixes that.
_SIM_N = 4000


def _ml_margin(g):
    """The model's projected winning margin for its pick (expected-runs diff)."""
    if g.get("exp_runs_home") is None or g.get("exp_runs_away") is None:
        return None
    diff = g["exp_runs_home"] - g["exp_runs_away"]
    return round(diff if g.get("pick_is_home") else -diff, 1)


# Markets whose forecasts are worth grading, mapped to the predlog model key
# they are filed under. Kept SEPARATE per market on purpose: combo_engine trusts
# the model differently per market (_MODEL_TRUST runs 1.0 for the moneyline down
# to 0.35 for home runs), so the evidence that would justify changing any one of
# those numbers has to be per market too. A single pooled "props" bucket could
# not tell us which trust value was wrong.
_PREDLOG_TYPES = {"Total": "mlb_total", "Ks": "mlb_ks", "Run line": "mlb_runline",
                  "Hit": "mlb_hit", "Bases": "mlb_bases", "HR": "mlb_hr",
                  "HRR": "mlb_hrr", "RFI": "mlb_rfi", "SB": "mlb_sb", "RBI": "mlb_rbi",
                  "Extras": "mlb_extras"}


def _log_prop_predictions(g, cands):
    """File each pregame prop forecast against the price it disagreed with.

    Only the MONEYLINE was ever logged, so the only market we could ever prove
    anything about was the one market the blend already trusts at 1.0. Props are
    where the model disagrees most (median raw edge 5.6pp on hit legs against
    2.4pp on run lines) and where it is trusted least, and none of that
    disagreement was being scored.

    Called from the sim builder, where the marginals are the model's OWN numbers:
    _price_cands blends the market into `marg` in place on these same cached
    dicts, so reading them later would log the market's opinion back to itself.

    Best-effort throughout. This is bookkeeping, and a board must never fail to
    render because a log write did not work."""
    if (g.get("live") or {}).get("state") != "Preview":
        return                                  # a live prob is a different quantity
    suffix = g.get("kalshi_suffix")
    if not suffix:
        return
    try:
        import kalshi_mlb
        import predlog as predlog_mod
        idx = kalshi_mlb.index()
    except Exception:
        return
    rows = []
    for c in cands:
        model = _PREDLOG_TYPES.get(c.get("type"))
        kref = c.get("kref")
        p = c.get("marg")
        if not model or not kref or p is None or not (0.0 < p < 1.0):
            continue
        try:
            tk, close = kalshi_mlb.ticker_leg(idx, suffix, kref)
            if not tk:
                continue
            own = kalshi_mlb.price_leg(idx, suffix, kref)
            opp = kalshi_mlb.price_leg(idx, suffix, dict(kref, no=not kref.get("no")))
            if own is None or opp is None:
                continue           # one-sided: no honest de-vig, so no market row
            rows.append((model, tk, p, close, predlog_mod.devig(own, opp)))
        except Exception:
            continue
    if not rows:
        return
    try:
        predlog_mod.init_db()
        for model in {r[0] for r in rows}:
            predlog_mod.log_many(model, [(r[1], r[2], r[3], r[4])
                                         for r in rows if r[0] == model])
    except Exception as _e:
        errlog.note("BB-log_prop_predictions", _e)


# A cache whose TTL is SHORTER THAN THE TIME IT TAKES TO FILL cannot ever be
# warm. One game's 4,000-run sim is ~32s, so a 15-game slate is ~8 minutes of
# work -- against a 180s TTL, game 1 had already expired before game 15 was
# simulated, and every combo build re-ran however many had lapsed. That is where
# "Optimal for my x" spent 67 seconds: not on the optimisation, on re-simulating
# games it had just simulated.
#
# This path serves PRE-GAME states only (a game under way goes through
# _live_game_sim), and a pre-game state moves slowly: lineups are posted once and
# Kalshi's pre-game prices drift, they do not jump. Fifteen minutes is still far
# fresher than the lines it prices against, and it is comfortably longer than a
# full slate takes to build, so the cache actually holds.
# An hour, not fifteen minutes. The cached object is the MATCHUP SIMULATION and
# nothing else -- Kalshi prices are fetched fresh at build time by _price_cands
# and were never baked in. So this only goes stale when the lineup or the
# starter changes, which is hours of warning, not minutes. Fifteen minutes cost
# a full re-simulation several times an hour for no added accuracy, and on a
# one-CPU box a warm cycle takes longer than that, so the cache could never
# actually be full.
_GAME_SIM_TTL = int(_os.environ.get("VIGIL_GAME_SIM_TTL") or 3600)


# ---- real build progress ----------------------------------------------------
# The combo build is one HTTP request, so the browser cannot see inside it -- the
# old bar just eased along a curve and hoped. But the work decomposes exactly:
# it is N games, each costing one ~32s simulation (or nothing, when the game is
# already cached). So count the games as they are actually finished and let the
# client read the count. That is true progress, not an animation.
_progress = {}
_progress_lock = _threading.Lock()

# ...and the same state has to be SHARED BETWEEN WORKERS, for the same reason
# the sim cache does. Held only in one worker's memory, a build was invisible to
# the other two: the browser's poll round-robins, so a poll landing elsewhere
# found no job, started a SECOND build, and the progress bar read whichever
# worker answered -- climbing to "game 10 of 11" and then dropping back to
# "game 1 of 11". Three full simulations of the same slate then fought over one
# CPU. One JSON file per token, written by the builder and readable by all.
_JOB_DIR = _os.path.join(
    _os.environ.get("VIGIL_SIM_CACHE_DIR")
    or _os.environ.get("DEEP_CACHE_DIR") or "/tmp", "combojobs")


def _job_path(token):
    return _os.path.join(_JOB_DIR, f"{token}.json")


def _job_write(token, data):
    try:
        import json
        import tempfile
        _os.makedirs(_JOB_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_JOB_DIR, suffix=".tmp")
        with _os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        _os.replace(tmp, _job_path(token))
    except Exception as _e:
        errlog.note("BB-job_write", _e)


def job_claim(token):
    """True in exactly ONE worker -- whoever creates the file wins. O_EXCL is the
    whole point: without an atomic claim every polling request that lands on a
    cold worker starts its own duplicate build."""
    if not token:
        return True
    try:
        _os.makedirs(_JOB_DIR, exist_ok=True)
        fd = _os.open(_job_path(token),
                      _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    except Exception:
        return True             # cannot share -> single-process behaviour
    import json
    with _os.fdopen(fd, "w") as fh:
        json.dump({"status": "running", "at": 0, "done": 0, "total": 0,
                   "cached": 0, "started": _time.time()}, fh)
    # Old tokens from abandoned builds must not accumulate.
    try:
        cutoff = _time.time() - 3600
        for name in _os.listdir(_JOB_DIR):
            p = _os.path.join(_JOB_DIR, name)
            if _os.stat(p).st_mtime < cutoff:
                _os.remove(p)
    except OSError:
        pass                    # a sibling swept the same file first -- routine
    except Exception as _e:
        errlog.note("BB-job_claim", _e)
    return True


def job_read(token):
    if not token:
        return None
    try:
        import json
        with open(_job_path(token)) as fh:
            return json.load(fh)
    except Exception:
        return None


def job_update(token, **fields):
    if not token:
        return
    d = job_read(token) or {"status": "running", "at": 0, "done": 0,
                            "total": 0, "cached": 0, "started": _time.time()}
    d.update(fields)
    _job_write(token, d)


def job_finish(token, status, result=None, error=None):
    job_update(token, status=status, result=result, error=error)


def job_drop(token):
    try:
        _os.remove(_job_path(token))
    except OSError:
        pass                    # already gone -- job_drop is called defensively
    except Exception as _e:
        errlog.note("BB-job_drop", _e)


def job_heartbeat(token):
    """Freshen the job file's mtime without rewriting it. The build thread
    beats every ~20s from a side thread, so a stale mtime can only mean the
    process running the build is GONE (recycled by --max-requests, killed by
    a deploy swap, OOMed) -- never merely busy: the GIL rotates every few ms
    even under a full simulation, so a live beat thread always gets a slice."""
    if not token:
        return
    try:
        _os.utime(_job_path(token))
    except OSError:
        pass                    # job finished and was swept mid-beat -- fine


def job_takeover(token, dead_s):
    """Remove a 'running' job whose builder stopped beating, so the caller's
    normal O_EXCL claim can rebuild it. Job files live on the PERSISTENT disk,
    so a build killed mid-flight used to leave 'running' there forever -- the
    deploy that shipped this fix did exactly that to the owner: every poll
    answered 202 'building', the bar froze at 'simulated 1/5', and there was
    no path back short of the hourly sweep. Two racing takeovers of one token
    cannot happen in practice (a token is minted per Build click and polled
    by ONE sequential loop), and ownership of the rebuild is still decided by
    the claim, not by this."""
    if not token:
        return False
    try:
        if _time.time() - _os.stat(_job_path(token)).st_mtime <= dead_s:
            return False
        _os.remove(_job_path(token))
        return True
    except OSError:
        return False            # already gone, or lost the race -- not ours
    except Exception as _e:
        errlog.note("BB-job_takeover", _e)
        return False


def progress_declare(token, passes):
    """A sweep announces up front how many passes one build will make (optimal
    mode tries three per-leg floors, a max bet four), so the bar's denominator
    is honest from the first pixel. The previous scheme EXTENDED the total on
    each re-entry instead: the bar reached 15/15 looking finished, then the
    total grew under it to 30 and 45 -- three separate "done"s per build,
    reported verbatim by the owner as maddening."""
    if not token:
        return
    with _progress_lock:
        p = _progress.get(token)
        if p:
            p["passes"] = int(passes)
        else:
            _progress[token] = {"done": 0, "at": 0, "total": 0,
                                "phase": "simulating games", "cached": 0,
                                "passes": int(passes), "pass": 0,
                                "started": _time.time()}
    job_update(token, passes=int(passes))


def progress_start(token, total, phase="simulating games"):
    """Begin the NEXT PASS of a build's progress.

    Each pass gets its own numerator over a per-pass total; the overall
    fraction is ((pass-1) + done/total) / passes, computed client-side. Passes
    after the first run against a warm sim cache and fly, so the tail of the
    bar moves fast -- but the denominator never changes underneath the user."""
    if not token:
        return
    with _progress_lock:
        p = _progress.get(token)
        if p:
            p["pass"] = int(p.get("pass") or 0) + 1
            p["total"] = int(total)
            p["at"] = p["done"] = p["cached"] = 0
        else:
            _progress[token] = {"done": 0, "at": 0, "total": int(total),
                                "phase": phase, "cached": 0,
                                "passes": 1, "pass": 1,
                                "started": _time.time()}
        # Keep the table from growing without bound if a client abandons a build.
        if len(_progress) > 40:
            for k in sorted(_progress, key=lambda k: _progress[k]["started"])[:20]:
                _progress.pop(k, None)
        cur = dict(_progress.get(token) or {})
    # The shared job file gets the same picture, so a poll landing on a
    # sibling worker reports the true total and pass instead of zeros.
    job_update(token, total=cur.get("total", int(total)),
               at=0, done=0, cached=0, phase=phase,
               passes=cur.get("passes", 1), **{"pass": cur.get("pass", 1)})


def progress_enter(token):
    """A game is about to be simulated. Reported separately from `done` because
    the FIRST game takes ~32s to finish, and counting only completions left the
    bar frozen at 0/N for that whole time -- which reads exactly like the frozen
    bar this was meant to replace. `at` lets the client say 'simulating game 1
    of 14' the instant the work starts."""
    if not token:
        return
    with _progress_lock:
        p = _progress.get(token)
        if p:
            p["at"] += 1
    j = job_read(token)
    if j is not None:
        job_update(token, at=int(j.get("at") or 0) + 1)


def progress_step(token, cached=False):
    if not token:
        return
    with _progress_lock:
        p = _progress.get(token)
        if p:
            p["done"] += 1
            if cached:
                p["cached"] += 1
    j = job_read(token)
    if j is not None:
        job_update(token, done=int(j.get("done") or 0) + 1,
                   cached=int(j.get("cached") or 0) + (1 if cached else 0))


def progress_get(token):
    with _progress_lock:
        p = _progress.get(token)
        if p:
            return dict(p)
    j = job_read(token)                      # a sibling worker owns this build
    if j:
        j.setdefault("phase", "simulating games")
        return j
    return None


def progress_done(token):
    """Forget a build entirely -- memory AND the shared file. Dropping only the
    in-memory copy left the token alive on disk, where progress_get would keep
    finding it and any worker would keep reporting a finished build as live."""
    if not token:
        return
    with _progress_lock:
        _progress.pop(token, None)
    job_drop(token)


# The per-game sim cache has to be SHARED BETWEEN WORKERS and SINGLE-FLIGHT.
# Both were broken at once, and together they made the maker slower than before:
#
#   * Running three gunicorn workers (needed, so a killed worker cannot take the
#     health check down) gave each its own in-process cache. The warmer runs in
#     exactly ONE worker, so roughly two builds in three landed on a worker that
#     had never seen the game and re-simulated it from cold.
#   * _cached() has no single-flight: two callers who miss both run the
#     producer. So the warmer and the user's build would simulate the SAME game
#     concurrently, and on a one-CPU box they simply halve each other's speed.
#
# A finished sim is ~1.5 MB pickled. Writing it where every worker can read it
# makes one simulation serve all of them, and an in-process wait makes the
# second caller queue behind the first instead of duplicating the work.
_SIM_DISK = _os.environ.get("VIGIL_SIM_CACHE_DIR") or _os.path.join(
    _os.environ.get("DEEP_CACHE_DIR") or "/tmp", "gamesim")
# Version of the game-sim payload shape. The PC compute worker uploads sims
# built on ITS checkout; the server only adopts uploads whose schema matches
# its own, so a stale (or ahead-of-deploy) PC can only be ignored, never
# poison the cache. BUMP THIS in the same commit as any change to what
# _game_sim stores.
GAME_SIM_SCHEMA = 1


def sim_disk_write_raw(pk, data):
    """Adopt an EXTERNALLY-computed game sim: write the pickled bytes where
    every worker reads (temp + rename, so no reader sees a half-written file).
    The caller has already authenticated and schema-checked the upload."""
    import tempfile
    _os.makedirs(_SIM_DISK, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_SIM_DISK, suffix=".tmp")
    with _os.fdopen(fd, "wb") as fh:
        fh.write(data)
    _os.replace(tmp, _os.path.join(_SIM_DISK, f"{int(pk)}.pkl"))
    try:
        _sim_disk_prune(2 * _GAME_SIM_TTL)
    except Exception as e:
        errlog.note("BB-sim-upload-prune", e)


def sim_disk_ages():
    """{game_pk: age_seconds} of every fresh sim on disk — what the PC worker
    asks before uploading, so it skips what the server already has."""
    out = {}
    try:
        now = _time.time()
        for name in _os.listdir(_SIM_DISK):
            if not name.endswith(".pkl"):
                continue
            try:
                age = now - _os.stat(_os.path.join(_SIM_DISK, name)).st_mtime
                if age < _GAME_SIM_TTL:
                    out[int(name[:-4])] = round(age, 1)
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return out
_sim_flight = {}
_sim_flight_lock = _threading.Lock()


def _sim_disk_get(pk):
    """A sibling worker's simulation, if one is on disk and still fresh."""
    try:
        path = _os.path.join(_SIM_DISK, f"{pk}.pkl")
        if _time.time() - _os.stat(path).st_mtime > _GAME_SIM_TTL:
            return None
        import pickle
        with open(path, "rb") as fh:
            return pickle.load(fh)
    except Exception:
        return None


# The last sim-cache write failure, for the warm bar to surface. A cache that
# cannot write is slow, never broken -- but SILENTLY slow is how a full data
# disk read as "0/9 warming up" for an hour: every sim completed, landed
# nowhere, and the count (derived from the disk) never moved.
_SIM_DISK_ERR = {"ts": 0.0, "msg": None}


def sim_disk_health():
    """The last cache-write error if it is recent, else None."""
    if _SIM_DISK_ERR["msg"] and _time.time() - _SIM_DISK_ERR["ts"] < 1800:
        return _SIM_DISK_ERR["msg"]
    return None


def _sim_disk_prune(max_age):
    """Drop cached sims older than max_age. Expired files are pure dead weight,
    and on a small data disk they are the first thing to shed when a write
    fails for space."""
    cutoff = _time.time() - max_age
    for name in _os.listdir(_SIM_DISK):
        path = _os.path.join(_SIM_DISK, name)
        try:
            if _os.stat(path).st_mtime < cutoff:
                _os.remove(path)
        except OSError:
            pass


def _sim_disk_put(pk, val):
    """Publish a simulation for the other workers. Written to a temp file and
    renamed, so a reader never sees a half-written pickle."""
    def _write():
        import pickle
        import tempfile
        _os.makedirs(_SIM_DISK, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_SIM_DISK, suffix=".tmp")
        with _os.fdopen(fd, "wb") as fh:
            pickle.dump(val, fh, protocol=pickle.HIGHEST_PROTOCOL)
        _os.replace(tmp, _os.path.join(_SIM_DISK, f"{pk}.pkl"))
    try:
        _write()
        # Yesterday's games are never coming back. Drop anything well past its
        # TTL so the cache directory cannot creep across the disk.
        try:
            _sim_disk_prune(2 * _GAME_SIM_TTL)
        except Exception as _e:
            errlog.note("BB-sim_disk_put", _e)
        _SIM_DISK_ERR["msg"] = None
    except OSError as e:
        # Most likely the disk is full. Shed everything already expired and
        # try once more -- and if it still fails, say so where the warm bar
        # can see it instead of letting 200 seconds of simulation vanish.
        try:
            _sim_disk_prune(_GAME_SIM_TTL)
            _write()
            _SIM_DISK_ERR["msg"] = None
            return
        except Exception as _e:
            errlog.note("BB-sim_disk_put-2", _e)
        _SIM_DISK_ERR.update(ts=_time.time(),
                             msg=f"{type(e).__name__}: {e}"[:200])
        errlog.note("SIM-disk-write", e)
    except Exception as _e:
        errlog.note("BB-sim_disk_put-3", _e)  # a cache that cannot write is slow, never broken


# The BOARD needs sharing between workers just like the game sims do. It lived
# only in each worker's memory, so with three workers the same /api/warm poll
# flickered between "15/15 ready" and "0/0 building today's board" depending on
# which worker answered -- and every worker paid its own ~90s slate build for a
# board a sibling already had. Same cure as the sims: publish the finished board
# (~0.5 MB pickled) where every worker can read it.
_SLATE_DISK = _os.path.join(
    _os.environ.get("VIGIL_SIM_CACHE_DIR")
    or _os.environ.get("DEEP_CACHE_DIR") or "/tmp", "slates")


def _slate_disk_get(date, season, max_age):
    """(board, age_seconds) from a sibling worker, or (None, None)."""
    try:
        path = _os.path.join(_SLATE_DISK, f"{date}_{season}.pkl")
        age = _time.time() - _os.stat(path).st_mtime
        if max_age is not None and age > max_age:
            return None, None
        import pickle
        with open(path, "rb") as fh:
            return pickle.load(fh), age
    except Exception:
        return None, None


def _slate_claim(date, season):
    """True in the ONE worker that should build this board.

    The in-process _slate_building guard stops threads duplicating a build, but
    with three workers a cold start had every worker spawn its own ~175 MB
    build child for the same board on the same CPU. O_EXCL on a claim file
    settles it across processes; a claim older than the build timeout is a dead
    builder and may be taken over."""
    try:
        _os.makedirs(_SLATE_DISK, exist_ok=True)
        path = _os.path.join(_SLATE_DISK, f"{date}_{season}.lock")
        try:
            fd = _os.open(path, _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY)
            _os.close(fd)
            return True
        except FileExistsError:
            if _time.time() - _os.stat(path).st_mtime > _SLATE_BUILD_TIMEOUT:
                _os.utime(path, None)          # take over the stale claim
                return True
            return False
    except Exception:
        return True             # cannot coordinate -> single-worker behaviour


def _slate_release(date, season):
    try:
        _os.remove(_os.path.join(_SLATE_DISK, f"{date}_{season}.lock"))
    except OSError:
        pass                    # no claim to release -- routine on the wait path
    except Exception as _e:
        errlog.note("BB-slate_release", _e)


def _slate_disk_put(date, season, out):
    try:
        import pickle
        import tempfile
        _os.makedirs(_SLATE_DISK, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_SLATE_DISK, suffix=".tmp")
        with _os.fdopen(fd, "wb") as fh:
            pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
        _os.replace(tmp, _os.path.join(_SLATE_DISK, f"{date}_{season}.pkl"))
        cutoff = _time.time() - 6 * 3600          # yesterday's boards are dead weight
        for name in _os.listdir(_SLATE_DISK):
            fp = _os.path.join(_SLATE_DISK, name)
            if _os.stat(fp).st_mtime < cutoff:
                _os.remove(fp)
    except Exception as _e:
        errlog.note("BB-slate_disk_put", _e)  # a cache that cannot write is slow, never broken


# The per-pitcher last-start velocity lookups are ~30 separate Statcast
# searches, and the slate rebuilds in a FRESH child process every few minutes
# -- so without a shared memo every rebuild refetched all of them from a cold
# cache: thousands of Savant hits a day, and the slowest minutes of every
# rebuild window a returning phone spends reading "building today's board".
# A pitcher's last start only changes when he pitches again, so the day's
# answers (including "no signal", stored as None so it is not re-asked) are
# memoized on disk beside the shared slate board; only pids the memo has
# never seen are fetched. Lives in _SLATE_DISK, so the 6h prune there
# retires stale memos with the dead boards.
_VELO_MEMO_TTL = 3 * 3600


def _velo_memo_get(date):
    try:
        path = _os.path.join(_SLATE_DISK, f"velo_{date}.pkl")
        if _time.time() - _os.stat(path).st_mtime > _VELO_MEMO_TTL:
            return {}
        import pickle
        with open(path, "rb") as fh:
            return pickle.load(fh) or {}
    except Exception:
        return {}                   # no memo is a slow build, never a broken one


def _velo_memo_put(date, memo):
    try:
        import pickle
        import tempfile
        _os.makedirs(_SLATE_DISK, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_SLATE_DISK, suffix=".tmp")
        with _os.fdopen(fd, "wb") as fh:
            pickle.dump(memo, fh, protocol=pickle.HIGHEST_PROTOCOL)
        _os.replace(tmp, _os.path.join(_SLATE_DISK, f"velo_{date}.pkl"))
    except Exception as _e:
        errlog.note("BB-velo_memo", _e)


def _game_sim_cached(g):
    """True when this game's sim is already in cache -- i.e. it costs nothing.
    Lets the progress bar distinguish 'simulating 15 games' from 'reading 15
    cached ones', which are two very different waits."""
    pk = g.get("game_pk")
    if pk is None:
        return False
    hit = _cache.get(("game_sim", pk))
    if hit and _time.time() - hit[0] < _GAME_SIM_TTL:
        return True
    try:                        # a sibling worker may already have done it
        path = _os.path.join(_SIM_DISK, f"{pk}.pkl")
        return _time.time() - _os.stat(path).st_mtime <= _GAME_SIM_TTL
    except Exception:
        return False


def _game_sim(g):
    """The shared 4000-run game simulation + its candidate legs, cached per game
    (see _GAME_SIM_TTL). Everything that prices a leg reads this, so combos,
    edges and SGPs are the SAME simulation."""
    import mlb_sim
    pk = g.get("game_pk")

    def build():
        sim = mlb_sim.simulate(g, _SIM_N)
        cands = mlb_sim.build_candidates(g, sim)
        # Log BEFORE anything can blend these dicts in place. predlog dedups by
        # ticker (first write wins), so the sim rebuilding every few minutes
        # records each leg once, at the first price we saw it at.
        try:
            _log_prop_predictions(g, cands)
        except Exception as _e:
            errlog.note("BB-build", _e)
        return {"sim": sim, "cands": cands}
    if pk is None:
        return build()
    key = ("game_sim", pk)
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < hit[2]:
        return hit[1]
    disk = _sim_disk_get(pk)                 # another worker already did it
    if disk is not None:
        _cache[key] = (_time.time(), disk, _GAME_SIM_TTL)
        return disk
    # SINGLE-FLIGHT. Without this the warmer and a user's build run the same
    # 200-second simulation side by side and each takes twice as long.
    with _sim_flight_lock:
        ev = _sim_flight.get(pk)
        leader = ev is None
        if leader:
            ev = _threading.Event()
            _sim_flight[pk] = ev
    if not leader:
        ev.wait(timeout=_SLATE_BUILD_TIMEOUT)
        hit = _cache.get(key)
        if hit:
            return hit[1]
        disk = _sim_disk_get(pk)
        if disk is not None:
            _cache[key] = (_time.time(), disk, _GAME_SIM_TTL)
            return disk
        return build()                       # the leader failed; do it ourselves
    try:
        val = build()
        _cache[key] = (_time.time(), val, _GAME_SIM_TTL)
        _sim_disk_put(pk, val)               # share it with the other workers
        return val
    finally:
        with _sim_flight_lock:
            _sim_flight.pop(pk, None)
        ev.set()


def _live_state_sig(snap):
    """A signature that changes whenever anything bettable has changed. Cheaper
    and safer than a plain timer: a cached live board must never outlive the
    situation it was priced from."""
    banked = sum(v.get("hit", 0) + v.get("k", 0)
                 for side in ("home", "away")
                 for v in (snap["banked"].get(side) or {}).values())
    pit = snap.get("pitching") or {}
    return (snap["inning"], snap["is_top"], snap["outs"], snap["away_runs"],
            snap["home_runs"], tuple(snap.get("bases") or ()), banked,
            (pit.get("home") or {}).get("sp_k"), (pit.get("away") or {}).get("sp_k"),
            (pit.get("home") or {}).get("sp_in"), (pit.get("away") or {}).get("sp_in"))


def _live_game_sim(g):
    """Sim + candidates for a game ALREADY UNDER WAY, resumed from where it
    stands. Returns None when the live feed can't be read, so callers can fall
    back to the thin score-derived legs rather than showing nothing."""
    import mlb_sim, mlb_live
    pk = g.get("game_pk")
    if pk is None:
        return None
    try:
        snap = mlb_live.snapshot(pk)
    except Exception:
        return None
    if not snap:
        return None

    def build():
        sim = mlb_sim.simulate(g, _SIM_N, live=snap)
        return {"sim": sim, "cands": mlb_sim.build_candidates(g, sim), "snap": snap}
    # Keyed on the situation itself, and short-lived on top of that. The
    # signature moves with every at-bat and NEVER repeats, so each superseded
    # entry is dead the moment the next lands -- and at several MB of
    # simulation each, waiting for the periodic sweep meant an evening of live
    # games grew a worker by hundreds of MB. Evict the game's older situations
    # here, where we know they are dead, keeping exactly one live sim a game.
    sig = _live_state_sig(snap)
    for k in [k for k in list(_cache)
              if isinstance(k, tuple) and len(k) == 3
              and k[0] == "game_sim_live" and k[1] == pk and k[2] != sig]:
        _cache.pop(k, None)
    return _cached(("game_sim_live", pk, sig), 45, build)


def _sim_live_legs(g, types=None):
    """Full sim-backed legs for a game IN PROGRESS.

    Same shape and same markets as the pre-game legs -- hits, total bases, Ks,
    HR, H+R+RBI, steals, run line, totals -- but every probability comes from a
    sim resumed at the current base-out state, with what each player has already
    banked counted toward his line. A prop the game has already decided reads
    100% (or 0%) instead of being re-guessed, and prices come from the live
    Kalshi market, which is the whole point of betting one mid-game.

    Returns [] when the live feed can't be read, so the caller can fall back.
    """
    gs = _live_game_sim(g)
    if not gs:
        return []
    return _sim_pregame_legs(g, types=types, _gs=gs, _live=True)


def _sim_pregame_legs(g, types=None, _gs=None, _live=False):
    """Combo legs for a PRE-GAME matchup, sourced from the shared game sim
    (simulated probability + per-market average) and priced live off Kalshi.

    `_gs`/`_live` let the in-progress builder reuse this exact body against a
    resumed sim, so a live board can never drift from the pre-game one."""
    import kalshi_mlb
    pk, mu = g["game_pk"], g["matchup"]
    try:
        idx = kalshi_mlb.index()
    except Exception:
        idx = {}
    suffix = g.get("kalshi_suffix")

    def price(kref):
        if not (suffix and kref):
            return None
        try:
            return kalshi_mlb.price_leg(idx, suffix, kref)
        except Exception:
            return None
    out = []
    # Moneyline (favorite) added explicitly, so a heavy-chalk leg the sim's
    # marginal filter would drop is still available to the safest combos. Skipped
    # for a live game: `pick_prob` is the PRE-GAME number and a team down 6 in the
    # 7th is not still a 55% favourite -- there, both moneylines come off the
    # resumed sim below, like every other leg.
    if (not _live and g.get("pick_prob") is not None and g["pick_prob"] >= 0.5
            and (types is None or "ML" in types)):
        out.append({"game_pk": pk, "type": "ML", "label": f"{g['pick']} to win",
                    "matchup": mu, "prob": g["pick_prob"],
                    "price_cents": g.get("pick_price_cents"), "live": False,
                    "sim_avg": _ml_margin(g), "avg_unit": "run margin", "group": "ML"})
    for c in (_gs or _game_sim(g))["cands"]:
        if c["type"] == "ML" and not _live:
            continue                        # favorite handled above
        # `types is None` means no filter; an EMPTY list means the user unchecked
        # every chip and expects nothing. `if types` conflated the two, so
        # clearing every type silently returned the full board.
        if types is not None and c["type"] not in types:
            continue
        out.append({"game_pk": pk, "type": c["type"], "label": c["label"], "matchup": mu,
                    "prob": c["marg"], "price_cents": price(c.get("kref")), "live": _live,
                    "sim_avg": c.get("sim_avg"), "avg_unit": c.get("avg_unit"),
                    "group": c.get("group"), "side": c.get("side", "yes")})
    return out


def _curate_legs(legs):
    """One best (highest-probability) leg per market group and SIDE -- the small,
    varied pool the suggested-combos assembler works from. Keying on the group
    alone would let a market's NO side (the near-certain negation of its longest
    shot) evict the YES leg that is actually worth betting."""
    best = {}
    for l in legs:
        k = (l.get("group") or l["type"], l.get("side", "yes"))
        if k not in best or l["prob"] > best[k]["prob"]:
            best[k] = l
    return list(best.values())


def _live_variants(g, types=None):
    """Live games contribute only the live moneyline plus run line / totals
    recomputed from the current score (pre-game props are stale once underway)."""
    out = []
    pk, mu = g["game_pk"], g["matchup"]

    def add(typ, label, prob, price=None, avg=None, unit=None):
        if types is not None and typ not in types:
            return
        if 0.02 <= prob <= 0.995:
            out.append({"game_pk": pk, "type": typ, "label": label, "matchup": mu,
                        "prob": prob, "price_cents": price, "live": True,
                        "sim_avg": avg, "avg_unit": unit})
    if g.get("pick_prob") is not None:
        add("ML", f"{g['pick']} to win", g["pick_prob"], g.get("pick_price_cents"),
            avg=_ml_margin(g), unit="run margin")
    ig = g.get("in_game")
    if ig and ig.get("exp_rem_home") is not None:
        ktot = _ktotals(g)
        lp = props_mod.live_game_props(
            ig["home_score"], ig["away_score"], ig["exp_rem_home"], ig["exp_rem_away"],
            g.get("home_abbr") or "HOME", g.get("away_abbr") or "AWAY")
        live_total = (ig.get("home_score", 0) + ig.get("away_score", 0)
                      + ig["exp_rem_home"] + ig["exp_rem_away"])
        _add_spread_legs(add, lp.get("run_line"))
        for t in lp["totals_ladder"]:
            mk = _tradeable_total(t["line"], ktot)
            if mk is None:
                continue
            add("Total", f"Over {t['line']} runs", t["over_pct"] / 100.0, mk.get("over"),
                avg=round(live_total, 1), unit="runs")
            add("Total", f"Under {t['line']} runs", t["under_pct"] / 100.0, mk.get("under"),
                avg=round(live_total, 1), unit="runs")
    return out


def _candidate_legs(games, live_only=False, types=None, allow_live=False):
    """Curated combo leg pool across the slate -- one best leg per market per game,
    from the shared game sim. One leg per game keeps the combos independent."""
    legs = []
    for g in games:
        state = _game_state(g)
        if state == "Final":
            continue
        if live_only and state != "Live":
            continue
        if state == "Live":
            # `live_only` is itself a request for in-progress games, so the
            # live-only board resumes from the current state without needing the
            # opt-in flag. Without this the live board fell through to the
            # PRE-GAME moneyline below while still tagging every leg "LIVE —
            # priced from the current game state": a team down six in the 7th
            # showed as the same favourite it was at first pitch.
            if allow_live or live_only:
                # The full sim-backed board, resumed from the current base-out
                # state, same as any pre-game matchup.
                lv = _sim_live_legs(g, types)
                if lv:
                    legs.extend(_curate_legs(lv))
                    continue
                # Live feed unreadable: fall back to the thin moneyline. Inside
                # the opt-in branch, so a caller that did NOT ask for live games
                # can never be handed one.
                if (types is None or "ML" in types) and (g.get("pick_prob") or 0) >= 0.5:
                    legs.append({"game_pk": g["game_pk"], "type": "ML",
                                 "label": f"{g['pick']} to win", "matchup": g["matchup"],
                                 "prob": g["pick_prob"],
                                 "price_cents": g.get("pick_price_cents"),
                                 "live": True, "sim_avg": _ml_margin(g),
                                 "avg_unit": "run margin"})
            continue
        legs.extend(_curate_legs(_sim_pregame_legs(g, types=types)))
    return legs


def _pct(x):
    """Round a percentage for display without collapsing small ones to zero."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return round(x, 1)
    # Under 1% a single decimal reads 0.0, and the first non-zero decimal still
    # overstates badly (0.0064% -> 0.01%, a 56% error against a payout figure
    # computed from the exact number). Two significant figures keeps the printed
    # probability consistent with the printed payout.
    import math
    return round(x, min(12, 1 - math.floor(math.log10(x))))


def _combo_item(combo):
    """Build the payload for one combo (list of legs)."""
    prob = 1.0; cost = 1.0; priced = True
    for l in combo:
        prob *= l["prob"]
        pc = l.get("price_cents")
        # A quote at or above 100c is Kalshi saying there is no offer, not a price
        # you can fill. Counted as a price it costs a full stake and pays 1.0x, so
        # the leg drags the parlay's probability down for zero extra payout -- a
        # strictly dominated slip. `_kalshi_payout` already required 0 < c < 100;
        # this keeps the two payout paths on the same rule.
        if pc and 0 < pc < 100:
            cost *= pc / 100.0
        else:
            priced = False
    item = {
        "legs": [{"pick": l["label"], "matchup": l["matchup"], "type": l.get("type"),
                  "prob_pct": round(l["prob"] * 100, 1), "price_cents": l.get("price_cents"),
                  "sim_avg": l.get("sim_avg"), "avg_unit": l.get("avg_unit"),
                  "side": l.get("side", "yes"), "live": l.get("live", False)}
                 for l in combo],
        "n_legs": len(combo),
        "any_live": any(l.get("live") for l in combo),
        # One decimal is right for a normal slip but silently prints 0.0% for a
        # longshot: three legs at the 4% floor is a genuine 0.0064% chance, and
        # showing "0.0% to cash" next to a 15,625x payout reads as either free
        # money or a broken number. Keep enough precision that a non-zero chance
        # never displays as zero.
        "combined_prob_pct": _pct(prob * 100),
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
    """{gamePk: (winning_team_name, total_runs)} for Final games on a date."""
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
            out[g.get("gamePk")] = (home if hr > ar else away, hr + ar)
    return out


def _add_spread_legs(add, rl, avg=None, unit=None):
    """Offer Kalshi's adjustable run line for both teams -- the 1.5 / 2.5 / 3.5
    lines pre-game, extending to 4.5 once a game is live and the runs are in.
    `add` filters out near-0/near-1 lines.

    Named the way Kalshi names it (see combo_engine.spread_label): the ticker
    integer and the printed line differ by a half, and a slip that says "win by
    4+" against a board that says 3.5 makes the reader convert every leg."""
    if not rl:
        return
    import combo_engine
    for tm, key in ((rl.get("home"), "home_by"), (rl.get("away"), "away_by")):
        for m, pct in sorted((rl.get(key) or {}).items(), key=lambda x: int(x[0])):
            add("Run line", combo_engine.spread_label(tm, m, "runs"),
                pct / 100.0, avg=avg, unit=unit)


def _ktotals(g):
    """Kalshi's offered total lines for a game -> {line(float): {over,under}¢}, so
    we only surface totals the sportsbook actually books (no phantom 'Under 19.5'
    at 99.7% that Kalshi won't even list)."""
    suf = g.get("kalshi_suffix")
    if not suf:
        return {}
    try:
        import kalshi_mlb
        tot = (kalshi_mlb.index().get(suf) or {}).get("total") or {}
    except Exception:
        return {}
    return {n - 0.5: v for n, v in tot.items()}      # Kalshi n = Over (n-0.5)


def _tradeable_total(line, ktot):
    """The Kalshi price dict for a total line, or None if untradeable. When the
    Kalshi index is available we require the exact line; when it's unavailable we
    fall back to a realistic MLB range so we still never show absurd phantom lines."""
    if ktot:
        return ktot.get(line)                        # {over,under} or None
    return {} if 6.5 <= line <= 12.5 else None       # Kalshi down -> sane range, no price


def _validate_game_props(gp, g):
    """Trim the displayed slate props to lines Kalshi actually books, so the UI
    never shows a phantom line the sportsbook won't quote (the classic 'Under
    19.5 runs at 99.7%'). Game-level markets — totals and the run-line ladder —
    key reliably off the Kalshi index; when it's unavailable we fall back to
    realistic MLB ranges. Player props are validated per line in _validate_player_props."""
    ktot = _ktotals(g)                                    # {line: {over,under}} or {}
    def ok_total(ln):
        return _tradeable_total(round(ln, 1), ktot) is not None
    if gp.get("totals_ladder"):
        gp["totals_ladder"] = [t for t in gp["totals_ladder"] if ok_total(t["line"])]
    if gp.get("totals"):
        gp["totals"] = [t for t in gp["totals"] if ok_total(t["line"])]
    # Run line: keep only the win-by margins Kalshi lists for each side.
    try:
        import kalshi_mlb
        kg = kalshi_mlb.index().get(g.get("kalshi_suffix")) or {}
    except Exception:
        kg = {}
    spread = kg.get("spread") or {}
    rl = gp.get("run_line") or {}
    def trim_by(team_abbr, by_map):
        if not by_map:
            return by_map
        if spread:
            ok = {str(by) for (tm, by) in spread if tm == team_abbr}
            return {k: v for k, v in by_map.items() if k in ok}
        return {k: v for k, v in by_map.items() if int(k) <= 4}   # Kalshi down -> sane range
    if rl:
        rl["home_by"] = trim_by(rl.get("home"), rl.get("home_by"))
        rl["away_by"] = trim_by(rl.get("away"), rl.get("away_by"))
    return gp



# The NO side of a market is a real, bookable leg we were ignoring. If the sim
# says a batter gets 1+ hits 36% of the time, "NO hit" is a 64% leg on that same
# Kalshi contract. Skipped where a NO is meaningless or already emitted: the
# moneyline pairs both teams, Under is already the NO of Over, and RFI has no
# yes/no split on Kalshi.
_NO_SKIP = {"RFI", "ML"}


def _mirror_no(variants):
    """Add the NO side of each eligible variant, sharing its game_pk so a leg and
    its own negation can never be picked into the same parlay."""
    out = []
    for v in variants:
        t = v.get("type") or ""
        lab = (v.get("label") or "").lower()
        p = v.get("prob")
        if t in _NO_SKIP or p is None or not (0.02 < p < 0.98):
            continue
        if lab.startswith("under ") or lab.startswith("no "):
            continue
        out.append({**v, "label": f"NO - {v.get('label')}", "prob": 1.0 - p,
                    # Kalshi quotes a separate no_ask the sim layer doesn't carry
                    # yet; better model-only than a faked 100-yes_ask that would
                    # ignore the spread and overstate EV.
                    "price_cents": v.get("no_cents"), "type": t, "side": "no"})
    return variants + out


def _sim_for(g, allow_live=False):
    """The right simulation for this game: resumed from the current base-out
    state when it's under way, otherwise the pre-game one. None if a live game
    can't be read."""
    if _game_state(g) == "Live":
        return _live_game_sim(g) if allow_live else None
    return _game_sim(g)


def _live_desc(g):
    """'Top 3rd, 2 out, 0-2' for a game in progress, so a slip can say what the
    situation was when it was priced."""
    try:
        import mlb_live
        return mlb_live.describe(mlb_live.snapshot(g.get("game_pk")))
    except Exception:
        return ""


def _playable(g, allow_live=False):
    """Can this game still be bet into a combo? Finished games never; in-progress
    games only when the user has opted in to live pricing."""
    state = _game_state(g)
    if state == "Final":
        return False
    return allow_live or state != "Live"


def _game_variants(g, types=None, allow_live=False):
    """Every priced line variant for a game (moneyline, run line, totals ladder,
    hitter + pitcher props), each with the SIMULATED probability and its market
    average, so the combo maker tunes on the same 4000-run sim the edge finder
    uses. `types` restricts which prop kinds are produced. Live games fall back to
    the live-recomputed moneyline + totals (pre-game props are stale mid-game)."""
    state = _game_state(g)
    if state == "Final":
        return []
    if state == "Live":
        if not allow_live:
            return []               # in-progress games are opt-in only
        legs = _sim_live_legs(g, types)
        if legs:
            return legs             # full board, resumed from the current state
        # Opted in but the live feed is unreadable: fall back to the thin path,
        # recomputed from the score rather than the sim, so it has no NO side of
        # its own and still needs mirroring.
        return _mirror_no(_live_variants(g, types))
    # Pre-game legs come from the shared sim, whose candidates already carry their
    # own NO side -- priced off Kalshi's real no_ask. Mirroring here would double them.
    return _sim_pregame_legs(g, types=types)


def _kalshi_up():
    """Is the MLB price index actually answering?

    The maker excludes legs with no Kalshi market, which is right when the book
    is up and simply doesn't list that line. If the index is empty the exchange
    is unreachable (or the slate hasn't listed yet) and NOTHING is priced, so the
    same filter silently empties every pool and the maker returns nothing while
    blaming the user's filters. Cached upstream, so this costs a dict lookup."""
    try:
        import kalshi_mlb
        return bool(kalshi_mlb.index())
    except Exception:
        return False


def _single_game_fallback(games, n_legs, target_pct, target_payout, max_legs, types,
                          allow_live=False):
    """A one-game slate can't field a cross-game parlay (one leg per game), which
    used to surface as a bare "no combo". The legs are all correlated here, so
    hand it to the same-game builder, which reads the joint probability straight
    off the sim instead of multiplying legs that aren't independent."""
    try:
        res = build_same_game_parlays(games, n_legs=n_legs, target_pct=target_pct,
                                      target_payout=target_payout or 0,
                                      max_legs=max_legs, top_n=1, types=types,
                                      allow_live=allow_live)
    except Exception:
        return None
    item = (res or {}).get("best")
    if not item:
        return None
    matchup = item.get("matchup")
    for l in item.get("legs", []):
        l.setdefault("matchup", matchup)
        l.setdefault("side", "yes")      # same-game legs carry their own side
        l.setdefault("live", False)
    item["same_game"] = True
    item["target_pct"] = round(max(0.05, min(0.97, target_pct / 100.0)) * 100, 1)
    if target_payout:
        item["target_payout_x"] = target_payout
        item["payout_reached"] = bool((item.get("fair_payout_x") or 0) >= target_payout)
    item["note"] = ("Only one game left on the slate, so this is a same-game "
                    "parlay - the legs are correlated, and the combined odds come "
                    "from the simulation rather than multiplying the legs.")
    return item


def build_target_parlay(games, n_legs, target_pct, target_payout=None, max_legs=12,
                        types=None, allow_live=False):
    """Build a parlay.

    Payout mode (target_payout given): the target multiplier governs. We pick the
    legs that reach it with the highest combined probability, preferring your leg
    count and only adding legs when that count physically can't reach the target
    (never forcing in a near-zero "punt" leg). See parlay.payout_combo.

    Confidence mode: each leg tuned to ~the target confidence; take n_legs safest.

    Both modes take at most one leg per game, so a slate with a single game left
    can't build anything across games. Rather than return nothing, fall back to
    the correlation-aware same-game parlay for that one game.
    """
    target = max(0.05, min(0.97, target_pct / 100.0))
    up = _kalshi_up()      # read once, not per game and not per variant

    if len([g for g in games if _playable(g, allow_live)]) < 2:
        sgp = _single_game_fallback(games, n_legs, target_pct, target_payout,
                                    max_legs, types, allow_live)
        if sgp:
            return sgp

    if target_payout and target_payout > 1:
        import parlay
        # Confidence floor is REQUIRED: only legs >= the target confidence are
        # eligible. The selector then adds as many qualifying legs as needed to
        # reach the payout (expanding the count), never dropping below the floor.
        groups = []
        for g in games:
            # Bettable legs only: a leg with no Kalshi market can't go on a real
            # slip, so it doesn't belong in the pool (same rule as the mixed
            # maker). Variants carry their live price from the sim layer. When
            # the exchange itself is unreachable nothing is priced, so the filter
            # would empty the pool -- see _kalshi_up().
            vs = [v for v in _game_variants(g, types, allow_live)
                  if v["prob"] >= target and (v.get("price_cents") or not up)]
            if vs:
                groups.append(vs)
        res = parlay.payout_combo(groups, n_legs, target_payout, max_legs=max_legs)
        if not res:
            return None
        item = _combo_item(res["legs"])
        item["target_pct"] = round(target * 100, 1)
        item["min_confidence_pct"] = round(target * 100, 1)
        item["target_payout_x"] = target_payout
        item["payout_reached"] = res["reached"]
        item["legs_used"] = res["n_used"]
        item["requested_legs"] = res["requested_legs"]
        item["expanded"] = res["expanded"]
        item["all_priced"] = res["all_priced"]
        return item

    chosen = []  # one best variant per game, tuned near the target confidence
    for g in games:
        variants = [v for v in _game_variants(g, types, allow_live)
                    if v.get("price_cents") or not up]        # bettable legs only
        if not variants:
            continue
        meeting = [v for v in variants if v["prob"] >= target]
        pick = (min(meeting, key=lambda v: v["prob"]) if meeting
                else max(variants, key=lambda v: v["prob"]))
        pick["meets_target"] = bool(meeting)
        chosen.append(pick)
    if len(chosen) < 2:
        return None
    chosen.sort(key=lambda v: (v["meets_target"], v["prob"]), reverse=True)
    legs = chosen[:max(2, min(n_legs, len(chosen)))]
    item = _combo_item(legs)
    item["target_pct"] = round(target * 100, 1)
    item["legs_meeting_target"] = sum(1 for v in legs if v.get("meets_target"))
    return item


def build_same_game_parlays(games, n_legs=3, target_pct=55, target_payout=0,
                            n_sims=5000, max_legs=4, top_n=8, types=None,
                            allow_live=False):
    """Same-game parlays with honest, correlation-aware joint odds.

    Unlike the cross-game combos (independent games -> exact product), legs from
    one game are correlated, so we simulate each game and read the joint hit-rate
    directly. Each item also reports the naive independent product so you can see
    whether the correlation helps (legs reinforce) or hurts.
    """
    import mlb_sim
    target = max(0.05, min(0.97, target_pct / 100.0))
    upcoming = [g for g in games if _playable(g, allow_live)]
    # Big leg counts make the per-game combinatorial search expensive; split a
    # fixed budget across the slate so the request stays responsive.
    budget = max(80_000, 700_000 // max(1, len(upcoming)))
    up = _kalshi_up()      # read once, not per game
    out = []
    for g in upcoming:
        gs = _sim_for(g, allow_live)    # shared with the edge finder + combos
        if not gs:
            continue
        sim = gs["sim"]
        cands = [c for c in gs["cands"]
                 if (types is None or c["type"] in types) and c["marg"] >= target]
        # Bettable legs only, same rule as the other makers. Priced WITHOUT the
        # market blend — this builder's probabilities have always been the pure
        # model margins, and the request was to exclude unlisted legs, not to
        # re-price the listed ones.
        _price_cands(cands, g.get("kalshi_suffix"), blend=False)
        if up:
            cands = [c for c in cands if c.get("price_cents")]
        item = mlb_sim.best_same_game(cands, sim["n"], n_legs, target,
                                      target_payout, max_legs, budget=budget)
        if not item:
            continue
        item["matchup"] = g["matchup"]
        if _game_state(g) == "Live":
            item["live"] = True
            item["live_state"] = _live_desc(g)
            for leg in item.get("legs", []):
                leg["live"] = True          # so each leg renders its LIVE marker
        item["has_props"] = bool((g.get("props") or {}).get("batters_home")
                                 or (g.get("props") or {}).get("batters_away"))
        # Deep per-player / per-pitcher simulated detail behind this slip.
        item["breakdown"] = mlb_sim.deep_breakdown(g, sim)
        item.update(_kalshi_payout([(leg, g.get("kalshi_suffix")) for leg in item["legs"]]))
        out.append(item)
    out.sort(key=lambda x: x["combined_prob_pct"], reverse=True)
    return {"games": out[:top_n], "best": out[0] if out else None,
            "n_sims": _SIM_N}


# Below this many season innings, a starter's K/9 is too thin to trust a K
# ladder on -- the "edge" is really vs the league prior, not a real read -- so
# those props drop to low confidence (and off the Best Bets board).
K_TRUST_MIN_IP = 20.0


def _edge_confidence(typ):
    """How much to trust a model-vs-market gap, by market. The model is best
    grounded where it leans on stable team/pitcher rates (moneyline, total, Ks);
    weaker on low-base-rate or combined-stat props (HR, H+R+RBI)."""
    return {"ML": "high", "Total": "high", "Ks": "high",
            "Run line": "med", "Hit": "med", "Bases": "med",
            "RFI": "med", "HR": "low", "HRR": "low", "Extras": "med"}.get(typ, "med")


def find_edges(games, n_sims=4000, min_edge=4.0, top_n=60, types=None):
    """Scan every priced leg across the slate and rank model-vs-Kalshi gaps.

    For each candidate leg we already know our simulated probability (and, for
    most, a closed-form model probability) and can look up Kalshi's live price.
    The edge is our probability minus Kalshi's implied probability (the price in
    cents). A positive edge means we think YES is likelier than the market does.

    This is a disagreement finder, not a money printer: a gap is only an edge if
    the model is right, so each row carries a confidence by market type and the
    UI flags that lines are often loosely quoted until close to game time.
    """
    import kalshi_mlb
    idx = kalshi_mlb.index()
    rows = []
    for g in games:
        if _game_state(g) in ("Final", "Live"):
            continue
        suffix = g.get("kalshi_suffix")
        if not suffix or not idx.get(suffix):
            continue
        # Shared per-game sim (same object the combo maker + SGPs read).
        for c in _game_sim(g)["cands"]:
            if types is not None and c["type"] not in types:
                continue
            kref = c.get("kref")
            if not kref:
                continue
            cents = None
            try:
                cents = kalshi_mlb.price_leg(idx, suffix, kref)
            except Exception:
                cents = None
            if not cents or not (0 < cents < 100):
                continue
            # The sim's RAW number. The combo maker shows this same figure as
            # "pre-blend"; its own probabilities are market-blended, so this
            # screen and a slip's legs are DIFFERENT views of one leg on
            # purpose: this is where the model disagrees, the slip is what
            # you'd actually be paid to believe.
            sim_pct = round(c["marg"] * 100, 1)
            model_pct = c.get("model_pct")
            edge = round(sim_pct - cents, 1)
            # Kalshi taker fee (~7¢ x p x (1-p) per contract) comes off whichever
            # side you'd buy, so the tradeable edge is |edge| minus the fee.
            fee = _kalshi_fee(cents)
            net = round(edge - fee, 1) if edge >= 0 else round(edge + fee, 1)
            if edge * net < 0:                          # fee ate the whole edge
                net = 0.0
            conf = _edge_confidence(c["type"])
            # A strikeout ladder is only as trustworthy as the K/9 behind it: a
            # thin-sample starter (e.g. a rookie with one start) gets his K props
            # knocked to low confidence, which also keeps them off Best Bets.
            if c["type"] == "Ks":
                spip = (c.get("kref") or {}).get("sp_ip")
                if spip is not None and spip < K_TRUST_MIN_IP:
                    conf = "low"
            side = c.get("side", "yes")
            # Both sides carry the spread, so yes_ask + no_ask > 100 and at most
            # one side can show a positive edge. A NO leg priced against us is
            # therefore never news -- it's the mirror of a YES row already on the
            # board -- and listing it would bury the real bets under their own
            # reflections. Keep NO only where NO is the side worth buying.
            if side == "no" and edge <= 0:
                continue
            rows.append({
                "matchup": g["matchup"], "type": c["type"], "pick": c["label"],
                "our_pct": sim_pct, "model_pct": model_pct,
                "market_cents": cents, "market_payout_x": round(100.0 / cents, 2),
                "edge": edge, "fee_cents": fee, "net_edge": net,
                "confidence": conf, "side": side,
            })
    # Per-market lean, over the YES legs only. If a whole market type is one-sided
    # (e.g. every starter's Ks read high vs the market), that's a systematic model
    # bias to distrust, not a slate full of independent edges -- surface it so the
    # user can tell the two apart. NO legs are excluded because each is the mirror
    # of its YES leg: counting both would make every market look balanced by
    # construction and silence the lean flag entirely.
    summary = {}
    for r in (r for r in rows if r.get("side", "yes") == "yes"):
        s = summary.setdefault(r["type"], {"count": 0, "pos": 0, "neg": 0, "edge_sum": 0.0})
        s["count"] += 1
        s["pos" if r["edge"] >= 0 else "neg"] += 1
        s["edge_sum"] += r["edge"]
    for t, s in summary.items():
        s["avg_edge"] = round(s["edge_sum"] / s["count"], 1)
        # "lean" when a market is lopsided enough that the gap looks like model bias
        frac = max(s["pos"], s["neg"]) / s["count"]
        s["lean"] = (frac >= 0.78 and s["count"] >= 5 and abs(s["avg_edge"]) >= 5)
        del s["edge_sum"]

    filtered = [r for r in rows if abs(r["edge"]) >= min_edge]
    # Rank by the edge you can actually trade (net of Kalshi's fee), biggest first.
    filtered.sort(key=lambda r: abs(r.get("net_edge", r["edge"])), reverse=True)
    return {"edges": filtered[:top_n], "n_priced": len(rows),
            "summary": summary, "n_sims": _SIM_N}


def _kalshi_payout(leg_suffix_pairs):
    """Price each (leg, game-suffix) off live Kalshi markets, annotate the leg
    with its market cents/payout, and return the combo's real Kalshi payout
    (product of priced legs). Degrades gracefully: unpriced legs are skipped and
    flagged so the UI can show a partial/none figure instead of breaking."""
    try:
        import kalshi_mlb
        idx = kalshi_mlb.index()
    except Exception:
        idx = {}
    payout, payout_net, priced, total = 1.0, 1.0, 0, 0
    for leg, suf in leg_suffix_pairs:
        total += 1
        c = None
        try:
            c = kalshi_mlb.price_leg(idx, suf, leg.get("kref")) if suf and idx else None
        except Exception:
            c = None
        leg["market_cents"] = c
        # The leg's own market ticker + close, so the SLIP can be graded as a
        # unit later (sliplog): both sides share a ticker, the side rides on
        # the leg itself. Settlement is the one grader that never argues.
        try:
            tk, close = (kalshi_mlb.ticker_leg(idx, suf, leg.get("kref"))
                         if suf and idx else (None, None))
        except Exception:
            tk, close = None, None
        leg["ticker"] = tk
        leg["close_time"] = close
        if c and 0 < c < 100:
            leg["market_payout_x"] = round(100.0 / c, 2)
            payout *= 100.0 / c
            # Net payout: each leg effectively costs price + taker fee.
            payout_net *= 100.0 / min(99.9, c + _kalshi_fee(c))
            priced += 1
        else:
            leg["market_payout_x"] = None
    return {"kalshi_payout_x": round(payout, 2) if priced else None,
            "kalshi_payout_net_x": round(payout_net, 2) if priced else None,
            "kalshi_priced": priced, "kalshi_total_legs": total,
            "kalshi_full": priced == total and priced > 0}


def _cand_side(cand, g):
    """Which team a candidate leg belongs to ('home'/'away'), or None for a
    game-level leg (totals, RFI). Used to honor a single-team grid selection."""
    grp = cand.get("group") or ""
    if grp.startswith("bat:home:"):
        return "home"
    if grp.startswith("bat:away:"):
        return "away"
    kref = cand.get("kref") or {}
    t = kref.get("t")
    if t in ("ml", "spread"):
        ha = g.get("home_abbr") or g.get("home_name")
        return "home" if kref.get("team") == ha else "away"
    if t == "ks":
        props = g.get("props") or {}
        if kref.get("player") == props.get("home_sp_name"):
            return "home"
        if kref.get("player") == props.get("away_sp_name"):
            return "away"
    return None


def _edge_ok(c, min_edge_c):
    """Does this leg's MODEL edge clear the floor?

    Judged on the PRE-BLEND model number against the leg's own ask (a NO leg
    carries the NO ask, so one formula serves both sides). The blend cannot be
    the yardstick here: it is anchored to the market and its fitted weights
    keep legs within a couple of cents of the price by construction, so a
    post-blend floor above ~3c would match nothing, ever. The floor asks
    "where does the MODEL genuinely disagree by this much" -- the slip's
    displayed probabilities and EV stay blended and honest."""
    if min_edge_c is None:
        return True
    px = c.get("price_cents")
    if not px:
        return False                    # no price -> no measurable edge
    p = (c.get("marg_model") if c.get("marg_model") is not None
         else c.get("marg"))
    if p is None:
        return False
    return p * 100.0 - px >= min_edge_c


def _price_cands(cands, suffix, blend=True):
    """Annotate each candidate with its live Kalshi ask and market-blended
    probability, in place.

    Done BEFORE bundles are built, for two reasons. The price has to be inside
    the search rather than decoration on the finished slip, and the blend has to
    reach `marg` before mlb_sim.game_bundles reads it, so the bundle's joint
    probability is rescaled onto the blended marginals by the machinery already
    there. Unpriced legs keep price_cents=None and are EV-neutral in
    combo_engine; legs with no two-sided quote keep the model number unchanged."""
    import combo_engine
    try:
        import kalshi_mlb
        idx = kalshi_mlb.index()
    except Exception:
        idx = {}
    quotes = {}
    for c in cands:
        px = q = None
        if idx and suffix:
            try:
                px = kalshi_mlb.price_leg(idx, suffix, c.get("kref"))
                q = kalshi_mlb.quote_leg(idx, suffix, c.get("kref"))
            except Exception:
                px = q = None
        c["price_cents"] = px
        quotes[id(c)] = q
    if blend:
        combo_engine.blend_candidates(cands, quotes)
    return cands


def build_mixed_parlay(games, n_legs=4, target_pct=55, target_payout=0,
                       n_sims=5000, max_legs_per_game=3, max_total_legs=8,
                       legs_mode="prefer", payout_mode="off", conn="or", types=None,
                       game_sel=None, include_live=False, objective="balanced",
                       net_fees=True, cap_pct=None, max_bet=False, cap_x=None,
                       progress_token=None, sides=None, min_edge_c=None,
                       leg_ok=None):
    """One parlay across MULTIPLE games that may stack correlated legs within a
    game and add single legs from others.

    Honest math: within a game legs are correlated, so each game contributes a
    simulated joint probability; across games they're independent (measured --
    see combo_engine), so the parlay probability is the product of the per-game
    joint odds. The independent product of every leg's marginal is also returned
    so the correlation effect of any stacked game is visible.

    `legs_mode`/`payout_mode` ("require"/"prefer"/"off") + `conn` ("and"/"or")
    control whether the leg count and the payout are hard requirements or just
    recommendations. `objective` ("balanced"/"safe"/"value") then orders whatever
    satisfies them, with the Kalshi price inside the search.

    `min_edge_c` is EDGE MODE: keep only legs where the pre-blend model
    number beats the leg's own ask by at least this many cents (see _edge_ok
    for why pre-blend is the only honest yardstick). Composes with the
    confidence floor, so "edges of +5c or better, each leg 55%+ likely"
    means exactly what it reads.

    `sides` restricts the pool to YES legs, NO legs, or both (None). It exists
    because the maker is a probability optimizer and a home run is a ~12% event:
    asked for the likeliest slip it will ALWAYS answer with the fade, so
    "three home run picks" came back as three NO legs. Wanting the longshot
    side is a legitimate ask that no confidence target can express -- a floor
    picks a probability, not a direction. Pair {"yes"} with a low floor.

    `cap_pct` turns the confidence floor into a BAND. Ask for 60-70% and a leg
    at 90% is not "safely inside the range", it is out -- so the builder walks
    the ladder to the line that lands in it. Over 3.5 at 90% becomes Over 4.5 or
    Over 5.5; a run line at 40% becomes the NO side of the same margin. The
    ladders and both sides already exist as candidates, so the band is a filter
    and the walking falls out of it.

    `max_bet` replaces every other target with the one Kalshi imposes: build the
    slip most likely to cash among those that still collect the full capped
    payout (`cap_x`, default combo_engine.MAX_PAYOUT_X). See combo_engine.max_bet
    for why that is a different question from any of the three objectives.
    """
    import mlb_sim
    import combo_engine
    floor = max(0.05, min(0.97, target_pct / 100.0))
    # A ceiling at or below the floor is not a band, it is an empty set. Treat it
    # as "no ceiling" rather than returning nothing and making the caller guess
    # why -- the UI already refuses to send one, but the endpoint is public.
    ceil = 1.0
    if cap_pct is not None and cap_pct / 100.0 > floor:
        ceil = min(1.0, cap_pct / 100.0)
    banded = ceil < 1.0

    def team_side(g):
        """If the grid restricts this game to one team, return that team's side."""
        if not game_sel:
            return None
        sel = game_sel.get(g["game_pk"])
        if sel is True or sel is None:
            return None
        return ("home" if sel == g.get("home_name")
                else "away" if sel == g.get("away_name") else None)

    # "NOT LISTED" AND "CAN'T REACH KALSHI" ARE DIFFERENT PROBLEMS. Dropping legs
    # with no market is right when the book is up and simply doesn't carry that
    # line. It is badly wrong when the price index came back empty, because then
    # NOTHING is priced, every leg is dropped, and the maker returns nothing at
    # all -- which is what it did, while telling the user his filters were too
    # narrow. One cached lookup tells the two apart: an empty index means the
    # exchange is unreachable or the slate hasn't listed yet, so fall back to
    # building unpriced and say so on the slip.
    priced_ok = _kalshi_up()

    games_bundles = []
    excluded_unpriced = 0
    excluded_no_edge = 0
    # Count what this build will actually simulate BEFORE starting, so the bar
    # has a real denominator rather than a guess.
    _todo = [g for g in games
             if _game_state(g) != "Final"
             and not (game_sel and g["game_pk"] not in game_sel)
             and (include_live or _game_state(g) != "Live")]
    progress_start(progress_token, len(_todo))
    for g in games:
        state = _game_state(g)
        if state == "Final":
            continue
        if game_sel and g["game_pk"] not in game_sel:
            continue
        # Count the game only if it will actually be worked on. Live games are
        # skipped a few lines down when include_live is off, and entering them
        # here made `at` outrun `total` (the bar clamps, but the count lied).
        if state == "Live" and not include_live:
            continue
        _was_cached = _game_sim_cached(g)
        progress_enter(progress_token)
        live_gs = None
        if state == "Live":
            if not include_live:
                continue
            # Resume the game from where it stands: every market is live again,
            # not just the moneyline, because the sim now knows what has already
            # been banked. Falls back to the win leg alone if the feed is down.
            live_gs = _live_game_sim(g)
            if live_gs is None:
                if sides is not None and "yes" not in sides:
                    continue          # the thin live fallback is a YES ML only
                if not g.get("pick_prob") or not (floor <= g["pick_prob"] <= ceil):
                    continue
                side = team_side(g)
                pick = g.get("pick")
                pick_side = "home" if pick == g.get("home_name") else "away"
                if side and side != pick_side:
                    continue  # selected the team that isn't the live favorite
                ha = g.get("home_abbr") or g.get("home_name")
                aa = g.get("away_abbr") or g.get("away_name")
                leg = {"type": "ML", "label": f"{pick} to win",
                       "marg": g["pick_prob"], "model_pct": round(g["pick_prob"] * 100, 1),
                       "group": "ML", "live": True,
                       "kref": {"t": "ml", "team": ha if pick_side == "home" else aa}}
                _price_cands([leg], g.get("kalshi_suffix"))
                if priced_ok and not leg.get("price_cents"):
                    excluded_unpriced += 1          # same rule as every other leg
                    continue
                if not _edge_ok(leg, min_edge_c):
                    excluded_no_edge += 1
                    continue
                bundle = {"size": 1, "prob": g["pick_prob"], "legs": [leg]}
                games_bundles.append((g["matchup"] + " 🔴", [bundle], g.get("kalshi_suffix")))
                continue
        gs = live_gs or _game_sim(g)    # shared with the edge finder + combos
        progress_step(progress_token, cached=_was_cached)
        sim = gs["sim"]
        side = team_side(g)
        cands = [c for c in gs["cands"]
                 if (types is None or c["type"] in types)
                 and (sides is None or c.get("side", "yes") in sides)
                 and (side is None or _cand_side(c, g) == side)]
        if not cands:
            continue
        # EDGE MODE hunts mispricing, and the juiciest fades live ABOVE the
        # normal NO band: a 9+ Ks line the model puts at 6% is a 94% NO --
        # in a fairly-priced book that's padding (which is why the sim's own
        # NO generation caps at 90%), but against an overpriced YES ask it is
        # exactly the bet being asked for ("he's facing a monster lineup").
        # Generated here at build time so the cached sim's candidate pool is
        # untouched for every other mode, then priced and gated like any leg
        # -- an extra fade still has to clear the edge floor on ITS OWN ask.
        if min_edge_c is not None and (sides is None or "no" in sides):
            have = {c["label"] for c in cands if c.get("side") == "no"}
            # Complement from the game's YES pool BEFORE the yes/no side
            # filter -- under "NO only" the YES cands were just filtered out
            # of `cands`, and that is precisely the fades-only request this
            # extension exists to serve.
            base_yes = [c for c in gs["cands"]
                        if (types is None or c["type"] in types)
                        and c.get("side", "yes") == "yes"
                        and (side is None or _cand_side(c, g) == side)]
            extra = [c for c in mlb_sim._no_candidates(
                         base_yes, sim["n"], lo=0.90, hi=0.97)
                     if c["label"] not in have]
            cands = cands + extra
        _price_cands(cands, g.get("kalshi_suffix"))
        # A caller-supplied per-leg rule, applied AFTER pricing so the rule can
        # see the ask (the locked presets' "a 2+ hits line only as a conviction
        # bet" needs the edge). None = every leg passes, exactly as before.
        if leg_ok is not None:
            cands = [c for c in cands if leg_ok(c)]
        # ONLY BETTABLE LEGS. A leg with no Kalshi market cannot go on a real
        # slip -- the maker used to include them anyway (EV-neutral at fair
        # value), which built slips the user then could not place: "no Kalshi
        # market" rows sitting in the middle of a parlay. Excluded at the pool,
        # not at display, so the optimizer never spends a slot on one. The count
        # is carried out so a thin morning pool (lines post near game time) is
        # explainable instead of mysterious.
        if priced_ok:
            n_all = len(cands)
            cands = [c for c in cands if c.get("price_cents")]
            excluded_unpriced += n_all - len(cands)
        # EDGE MODE: only legs where the model genuinely disagrees with the
        # price by the asked margin. After pricing (the edge needs the ask),
        # before the confidence band, so the two filters compose.
        if min_edge_c is not None:
            n_all = len(cands)
            cands = [c for c in cands if _edge_ok(c, min_edge_c)]
            excluded_no_edge += n_all - len(cands)
        # The per-leg floor is applied AFTER the blend, because the blend is what
        # decides the number the user actually sees. Filtering first let a leg
        # qualify at 60% pre-blend and then get marked down to 41% by the market,
        # so a slip built under "each leg >= 55%" came back showing 38-43% legs.
        cands = [c for c in cands if floor <= c["marg"] <= ceil]
        # A max bet reaches for cheap legs and multiplies them, so a leg our
        # model likes far more than the market can carry the whole slip on its
        # own. combo_engine.stackable bounds that optimism as a ratio.
        if max_bet:
            cands = [c for c in cands
                     if combo_engine.stackable(c["marg"], c.get("price_cents"))]
        if not cands:
            continue
        # A same-game stack never needs to be deeper than the parlay itself, and
        # the depth is what costs -- so asking for 4 legs buys a much wider pool
        # to find a correlating pair in than passing the 30-leg tier ceiling did.
        #
        # The floor is 1, NOT 2. With same-game parlays switched off the caller
        # passes max_legs_per_game=1, and a floor of 2 silently overrode it: the
        # bundle search still built pairs, the frontier still picked them, and a
        # slip came back stacking "Over 2.5 runs" and "Under 11.5 runs" from one
        # game with the box unticked. The floor was there to keep the search from
        # being pointless, but one leg per game is a legitimate ask, not a
        # degenerate one -- the cross-game frontier still has plenty to do.
        _depth = max(1, min(max_legs_per_game, max(n_legs, 3), max_total_legs))
        bundles = mlb_sim.game_bundles(cands, sim["n"], max_legs=_depth)
        if bundles:
            label = g["matchup"] + (" 🔴" if live_gs else "")
            games_bundles.append((label, bundles, g.get("kalshi_suffix")))
    if not games_bundles:
        return None

    # The DP goes exactly as deep as THIS request needs -- a 4-leg ask no
    # longer pays for a 12-leg state space, and a 19-leg ask is no longer
    # silently truncated to 12 (which is what returned a 2-leg slip for a
    # "require 19" build). A max bet is a payout chase, so it keeps the
    # default depth rather than the leg control's.
    _dp = combo_engine.dp_legs(
        n_legs, "off" if max_bet else legs_mode, max_total_legs,
        payout_mode="require" if max_bet else payout_mode)
    states = combo_engine.frontier(games_bundles, max_total_legs=_dp,
                                   net=net_fees)
    if max_bet:
        # The ceiling IS the target, so the leg count and payout controls have
        # nothing left to say -- passing them through would only let a "4 legs"
        # preference outrank the one thing being asked for.
        targets = {}
        best, meta = combo_engine.max_bet(states, cap=cap_x)
    else:
        targets = {"legs_target": n_legs, "payout_target": target_payout,
                   "legs_mode": legs_mode, "payout_mode": payout_mode, "conn": conn}
        best, meta = combo_engine.choose(states, objective=objective, **targets)
    if not best:
        return None
    item = mlb_sim._mixed_item(best["sel"], games_bundles,
                               None if max_bet else
                               (target_payout if payout_mode != "off" else None))
    item["n_sims"] = _SIM_N
    # Only overwrite what the chooser actually decided. legs_met/payout_reached
    # come back None when that target is "off", and blanking _mixed_item's own
    # values with them would turn "no target, so nothing to miss" into "unknown".
    for k, v in meta.items():
        if k != "objective" and v is not None:
            item[k] = v
    item["objective"] = "max_bet" if max_bet else objective
    item["excluded_unpriced"] = excluded_unpriced
    item["excluded_no_edge"] = excluded_no_edge
    item["min_edge_c"] = min_edge_c
    item["pricing_unavailable"] = not priced_ok
    item["legs_target"] = None if max_bet else (n_legs if legs_mode != "off" else None)
    if max_bet:
        # _mixed_item defaults payout_reached to True when it is given no
        # fair-payout target, which on a max bet would announce success no matter
        # what happened. The only "reached" that means anything here is the cap.
        item["payout_reached"] = meta.get("cap_reached")
        item["target_payout_x"] = None
    # The band the legs were drawn from, so the slip can say so rather than
    # leaving the reader to check every leg by eye.
    item["leg_floor_pct"] = round(floor * 100, 1)
    item["leg_cap_pct"] = round(ceil * 100, 1) if banded else None
    # What the price actually costs and returns, as opposed to the fair payout.
    item["net_fees"] = bool(net_fees)
    item["cost_x"] = round(best["cost"], 4)
    item["market_payout_x"] = round(best["payout"], 2) if best["payout"] else None
    item["ev_pct"] = round(best["ev"] * 100, 1) if best["ev"] is not None else None
    item["kelly_pct"] = round(combo_engine.kelly(best["prob"], best["cost"]) * 100, 2)
    item["priced_frac"] = round(best["priced_frac"], 2)
    item["priced_legs"] = best["priced"]
    # The same frontier ranked the other two ways, so "the price mattered" is
    # showable rather than asserted. Meaningless for a max bet: the three
    # objectives all answer "which slip is best", and this one is answering
    # "which slip reaches the ceiling", so they would be compared on a question
    # none of them was asked.
    if not max_bet:
        item["alternatives"] = combo_engine.compare(states, best, **targets)
    # Price via each group's own game suffix (carried through the DP) so a
    # doubleheader's two games don't collide on an identical matchup string.
    pairs = [(leg, grp.get("suffix"))
             for grp in item["groups"] for leg in grp["legs"]]
    item.update(_kalshi_payout(pairs))
    return item


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
        except Exception as _e:
            errlog.note("BB-grade_picks", _e)
        for p in ps:
            res = results.get(p["game_pk"])
            if res:
                winner, total = res
                pick_won = 1 if winner == p["pick_name"] else 0
                # home_won: the pick was either the home or away side, so the
                # pick result + side pins down the home result.
                home_won = pick_won if p.get("pick_side") == "home" else 1 - pick_won
                store.set_mlb_grade(p["game_pk"], pick_won, winner,
                                    actual_total=total, home_won=home_won)


# DraftKings Pick 6 player-prop stats we can price, and the standard "Power play"
# (all correct) payout multipliers by pick count. DK's live board is authoritative
# -- these are the typical tiers so we can show an approximate payout/EV.
_PICK6_STATS = {"Hit": "hits", "Bases": "total bases", "HR": "home runs",
                "SB": "stolen bases", "Ks": "strikeouts"}
# DK Pick 6 only offers a Less side on pitcher strikeouts (and fantasy points,
# which we don't board). Batter counting stats are More-only -- you lower the
# line for a smaller multiplier, you never bet the under.
_PICK6_LESS_OK = {"Ks"}
_PICK6_PAYOUT = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 25.0}


def pick6_board(games, top_n=60):
    """DraftKings Pick 6 board: our player-prop projections framed as More/Less at
    DK-style half-lines, from the SAME shared game sim the combos use. Pick 6 is an
    all-must-hit player-prop parlay, so we rank safest first and show each pick's
    projected value + our probability. DK's actual line governs -- match ours to
    the board."""
    picks = []
    for g in games:
        if _game_state(g) == "Final":
            continue
        ladders = {}   # (player, type) -> {"lines": {N: prob}, "avg": proj}
        for c in _game_sim(g)["cands"]:
            if c["type"] not in _PICK6_STATS:
                continue
            kref = c.get("kref") or {}
            player, line_n = kref.get("player"), kref.get("line")
            if not player or line_n is None:
                continue
            d = ladders.setdefault((player, c["type"]), {"lines": {}, "avg": c.get("sim_avg")})
            d["lines"][line_n] = c["marg"]
        for (player, typ), d in ladders.items():
            proj, lines = d["avg"], d["lines"]
            if proj is None or not lines:
                continue
            if typ in _PICK6_LESS_OK:
                # DK offers More AND Less here (pitcher strikeouts): take the line
                # nearest the projection and lean by where our number sits.
                N = min(lines, key=lambda n: abs((n - 0.5) - proj))
                line, p_more = N - 0.5, lines[N]
                side, prob = ("More", p_more) if proj >= line else ("Less", 1 - p_more)
            else:
                # More-only on DK (hits, total bases, home runs) -- there is no Less;
                # you just lower the line for a smaller multiplier. So always a More,
                # at the HIGHEST line where we're still confident (most value). DK's
                # default line falls out naturally: a 2+-hit bat lands on 1.5, a
                # weaker one on 0.5.
                cand = [(N, lines[N]) for N in lines if 0.5 <= lines[N] <= 0.9]
                if not cand:
                    continue
                N, prob = max(cand, key=lambda x: x[0])
                line, side = N - 0.5, "More"
            if not (0.5 <= prob <= 0.9):        # skip coin-flips and chalk
                continue
            picks.append({"player": player, "stat": _PICK6_STATS[typ], "type": typ,
                          "line": line, "side": side, "prob": round(prob * 100, 1),
                          "proj": proj, "matchup": g["matchup"], "game_pk": g["game_pk"]})
    picks.sort(key=lambda x: -x["prob"])        # safest first (all must hit)
    return {"picks": picks[:top_n],
            "payouts": {str(k): v for k, v in _PICK6_PAYOUT.items()}}


_P6_SHEET_LABELS = {"hit": "hits", "tb": "total bases", "hrr": "H+R+RBI",
                    "hr": "home runs", "sb": "stolen bases", "ks": "strikeouts"}


def pick6_game_sheet(games, pk=None):
    """Full per-player simulated stat sheet for ONE game: every batter's hits /
    total bases / H+R+RBI / HR and every starter's Ks, with the sim average and
    the More probability at every line the sim tracked. This powers the Pick 6
    browser -- the user picks the line (0.5 / 1.5 / 2.5...), we just report what
    the 4000-run sim says about it."""
    upcoming = [g for g in games if _game_state(g) != "Final"]
    glist = [{"pk": g["game_pk"], "matchup": g["matchup"],
              "live": _game_state(g) == "Live"} for g in upcoming]
    g = next((x for x in upcoming if x["game_pk"] == pk),
             upcoming[0] if upcoming else None)
    if g is None:
        return {"games": [], "error": "no upcoming games on this slate"}
    gs = _game_sim(g)
    by = {}
    for c in gs["cands"]:
        if c.get("side", "yes") != "yes":
            continue          # the sheet quotes More/Less itself; a NO twin would
                              # overwrite the More % with its own complement
        kref = c.get("kref") or {}
        t, player, N = kref.get("t"), kref.get("player"), kref.get("line")
        if t not in _P6_SHEET_LABELS or not player or N is None:
            continue
        s = by.setdefault(player, {}).setdefault(
            t, {"avg": c.get("sim_avg"), "unit": c.get("avg_unit"), "lines": {}})
        s["lines"][int(N)] = round(c["marg"] * 100, 1)
    players = []
    for nm, stats in by.items():
        kind = "P" if "ks" in stats else "B"
        rows = []
        for t in ("hit", "tb", "hrr", "hr", "sb", "ks"):
            if t not in stats:
                continue
            s = stats[t]
            rows.append({"t": t, "label": _P6_SHEET_LABELS[t], "avg": s["avg"],
                         "unit": s["unit"],
                         "lines": [{"n": nn, "line": nn - 0.5, "more_pct": p}
                                   for nn, p in sorted(s["lines"].items())]})
        lead = next((r["avg"] for r in rows if r["avg"] is not None), 0)
        players.append({"player": nm, "kind": kind, "stats": rows, "_s": lead or 0})
    players.sort(key=lambda x: (x["kind"] != "P", -x["_s"]))
    for p in players:
        p.pop("_s")
    return {"games": glist, "pk": g["game_pk"], "matchup": g["matchup"],
            "players": players, "n_sims": gs["sim"]["n"],
            "payouts": {str(k): v for k, v in _PICK6_PAYOUT.items()}}


def pick6_eval(games, pk, legs):
    """Exact joint odds for a hand-built Pick 6 slip, read off the shared game
    sim: AND the legs' hit-masks (complement for a Less side) instead of
    multiplying marginals -- same-game legs are correlated and the product lies."""
    import mlb_sim
    g = next((x for x in games if x["game_pk"] == pk), None)
    if g is None:
        return {"error": "game not found"}
    gs = _game_sim(g)
    n = gs["sim"]["n"]
    full = (1 << n) - 1
    jm, indep = full, 1.0
    for leg in legs:
        c = next((c for c in gs["cands"]
                  if c.get("side", "yes") == "yes"        # Less is the complement below
                  and (c.get("kref") or {}).get("t") == leg.get("t")
                  and c["kref"].get("player") == leg.get("player")
                  and c["kref"].get("line") == leg.get("n")), None)
        if c is None:
            return {"error": f"leg not in the sim: {leg.get('player')} {leg.get('t')} {leg.get('n')}"}
        m = c["mask"] if leg.get("side", "More") == "More" else (~c["mask"]) & full
        indep *= mlb_sim._popcount(m) / n
        jm &= m
    joint = mlb_sim._popcount(jm) / n
    return {"n_legs": len(legs), "n_sims": n,
            "joint_pct": round(joint * 100, 1), "indep_pct": round(indep * 100, 1),
            "sims_hit": mlb_sim._popcount(jm)}


# A model-vs-market gap wider than this on a game-level market means something
# is wrong (a mis-joined ticker, a stale quote, a market that isn't the moneyline)
# rather than an edge worth betting. Player props legitimately move further, so
# this only guards the game-level lines.
_MAX_PLAUSIBLE_GAP = 25.0
_GAME_LEVEL = {"ML", "Run line", "Total"}


def _implausible(legs):
    """True if any game-level leg disagrees with its price beyond belief."""
    for l in legs:
        if (l.get("type") in _GAME_LEVEL and l.get("price_cents")
                and abs(l["prob_pct"] - l["price_cents"]) > _MAX_PLAUSIBLE_GAP):
            return True
    return False


def combo_context(games, allow_live=False):
    """The few cheap facts the interactive combo MAKER needs, without building any
    suggestions.

    build_combos assembles safest / best-value / mixed / live slips on every slate
    load, which means enumerating candidate legs for every game and running the
    combinatorial assembler -- measured at ~26 MB and several seconds each time,
    paid whether or not anyone scrolls down to look. The maker itself only needs
    to know how many legs are available and whether cross-game combos are
    possible at all, and both are a count of playable games."""
    playable = [g for g in games if _playable(g, allow_live)]
    n = len(playable)
    return {"max_legs_available": n,
            # One playable game cannot make a cross-game parlay, so the maker
            # falls back to stacking correlated legs from that single game.
            "same_game_only": n == 1}


def build_combos(games, max_legs=3, top_n=6, types=None, allow_live=False):
    # Only games that haven't finished -- a settled game has no business in a
    # suggested parlay. Upcoming and in-progress games are eligible.
    live_games = [g for g in games if _playable(g, allow_live)]

    # Moneyline-only combos drive the EV-based highlights (those legs are priced).
    ml_legs = [{"game_pk": g["game_pk"], "type": "ML", "label": f"{g['pick']} to win",
                "matchup": g["matchup"], "prob": g["pick_prob"],
                "price_cents": g["pick_price_cents"], "live": _game_state(g) == "Live",
                "sim_avg": _ml_margin(g), "avg_unit": "run margin"}
               for g in live_games if g["pick_prob"] >= 0.5][:top_n]
    ml_combos = _assemble(ml_legs, max_legs)
    safest = max(ml_combos, key=lambda c: c["combined_prob_pct"], default=None)
    # Ranking by EV means picking whichever leg the model disagrees with MOST --
    # which is a broken-price detector, not an edge finder. A live MLB moneyline
    # is efficient to within a few points, so a 50-point gap (the model at 61% on
    # a side the book has at 8c) is one of the two numbers being wrong, not value.
    # Left unguarded this produced a "Best value" slip advertising +697% EV.
    priced = [c for c in ml_combos
              if c.get("ev_pct") is not None and not _implausible(c["legs"])]
    best_value = max(priced, key=lambda c: c["ev_pct"], default=None)

    # Mixed combos draw from every bet type (moneyline + props).
    all_legs = _candidate_legs(live_games, types=types, allow_live=allow_live)
    # Taking the 10 likeliest legs outright would make a NO leg structurally
    # ineligible here: NO tops out around 90% by design, and a slate always
    # fields ten 95%+ Overs. Give the NO side its own few slots so it can be
    # considered. The combos are still ranked purely on combined probability, so
    # a NO leg only survives into a suggestion when it genuinely competes.
    _by_prob = lambda ls: sorted(ls, key=lambda l: l["prob"], reverse=True)
    legs = (_by_prob([l for l in all_legs if l.get("side", "yes") == "yes"])[:10]
            + _by_prob([l for l in all_legs if l.get("side") == "no"])[:3])
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
    # Scale how many we surface with the size of the live slate — one or two live
    # games -> one or two combos; a full live board -> up to seven, mixing leg
    # counts so the list is a varied lineup, not the same legs reshuffled.
    n_live_games = sum(1 for g in games if _game_state(g) == "Live")
    live_legs = sorted(_candidate_legs(games, live_only=True, types=types),
                       key=lambda l: l["prob"], reverse=True)[:12]
    live_all = _assemble(live_legs, min(max_legs, max(2, n_live_games)))
    live_all.sort(key=lambda c: c["combined_prob_pct"], reverse=True)
    # Spread picks across leg counts so a big board shows 2-, 3-, 4-leg combos.
    n_live_show = max(1, min(7, n_live_games))
    live_combos, seen = [], set()
    for sz in (2, 3, 4, 5):
        for c in live_all:
            if c["n_legs"] == sz and id(c) not in seen:
                live_combos.append(c); seen.add(id(c)); break
    for c in live_all:
        if len(live_combos) >= n_live_show:
            break
        if id(c) not in seen:
            live_combos.append(c); seen.add(id(c))
    live_combos = live_combos[:n_live_show]
    live_combos.sort(key=lambda c: c["combined_prob_pct"], reverse=True)

    # Thin slate (typically a single game — e.g. the day back from the All-Star
    # break): cross-game combos need legs from different games, so nothing forms.
    # Fall back to SAME-GAME parlays, which are the right tool anyway — legs from
    # one game are correlated, so these use the correlation-aware sim (not a naive
    # independent product) to price the slip honestly.
    if not mixed and max_games < 2:
        sg = []
        for nl in (2, 3, 4):
            res = build_same_game_parlays(live_games, n_legs=nl, target_pct=45,
                                          max_legs=nl, top_n=4, types=types)
            for it in (res.get("games") or []):
                it = dict(it)
                for lg in it.get("legs", []):
                    lg.setdefault("price_cents", lg.get("market_cents"))
                    lg.setdefault("matchup", it.get("matchup"))
                it["same_game"] = True
                sg.append(it)
        # De-dup identical slips and keep the best per leg count for by_size.
        seen_leg = set()
        uniq = []
        for it in sorted(sg, key=lambda c: -c.get("combined_prob_pct", 0)):
            key = tuple(sorted(l.get("pick", "") for l in it.get("legs", [])))
            if key in seen_leg:
                continue
            seen_leg.add(key)
            uniq.append(it)
            by_size.setdefault(str(it.get("n_legs")), it)
        if uniq:
            mixed = uniq
            safest = safest or uniq[0]

    return {"safest": safest, "best_value": best_value,
            "all": sorted(ml_combos, key=lambda c: c["combined_prob_pct"], reverse=True)[:12],
            "mixed": mixed[:12], "live": live_combos,
            "by_size": by_size, "max_legs_available": max_games,
            "same_game_only": (not ml_combos and bool(mixed))}
