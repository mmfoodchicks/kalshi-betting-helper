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
from datetime import datetime as _dt, timedelta as _td
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
    except Exception:
        pass
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
                model_k = sp_model.get(side, {}).get("k9") if is_starter else None
                pitchers.append({
                    "name": nm, "side": side, "team": names[side],
                    "pitches": int(ps.get("numberOfPitches", 0)),
                    "ip": ps.get("inningsPitched"), "k": k_now,
                    "bb": int(ps.get("baseOnBalls", 0)), "h": int(ps.get("hits", 0)),
                    "er": int(ps.get("earnedRuns", 0)),
                    "season_era": ss.get("era"), "season_whip": ss.get("whip"),
                    "model_k9": model_k, "starter": is_starter,
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
        return 0.35 * era_eff + 0.40 * fip_eff + 0.25 * whip_ra9
    return 0.65 * era_eff + 0.35 * whip_ra9   # no FIP inputs -> ERA+WHIP only


def _bullpen_ra9(team_bp, lg):
    era = team_bp.get("era") or lg["bp_era"]
    whip = team_bp.get("whip") or lg["bp_whip"]
    whip_ra9 = lg["era"] * (whip / lg["whip"]) if whip > 0 else era
    return 0.70 * era + 0.30 * whip_ra9


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
        # day 1 = yesterday (heaviest weight), day 2 = the day before
        days = {1: (base - _td(days=1)).strftime("%Y-%m-%d"),
                2: (base - _td(days=2)).strftime("%Y-%m-%d")}
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
        # usage[team][pid] = {"days": set(), "name": str, "pitches_y": int}
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
                    a = tu.setdefault(pid, {"days": set(), "name": nm, "pitches_y": 0})
                    a["days"].add(dnum)
                    if dnum == 1:
                        a["pitches_y"] = max(a["pitches_y"], pitches)
        out = {}
        for tid, tu in usage.items():
            score = 0.0
            gassed = []
            for pid, a in tu.items():
                back_to_back = a["days"] == {1, 2}
                heavy_y = 1 in a["days"] and a["pitches_y"] >= 28
                mod_y = 1 in a["days"] and 15 <= a["pitches_y"] < 28
                if back_to_back or heavy_y:
                    score += 1.0
                    gassed.append(a["name"])
                elif mod_y:
                    score += 0.4
            factor = 1.0 + min(0.12, 0.025 * score)
            out[tid] = {"factor": round(factor, 3), "count": len(gassed), "arms": gassed[:4]}
        return out
    return _cached(("penfatigue", date, season), 1800, fetch)


def _pitching_factor(sp, team_bp, lg, bp_fatigue=1.0):
    sp_ra9 = _starter_ra9(sp, lg)
    bp_ra9 = _bullpen_ra9(team_bp, lg) * bp_fatigue    # gassed pen -> higher RA9
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
def _kalshi_fee(cents):
    """Expected Kalshi taker fee in cents per contract at price `cents`:
    fee = 0.07 x price x (1 - price), i.e. ~1.75¢ at 50¢, ~0.6¢ at 90¢. Kalshi
    rounds up per order; we use the smooth expectation for edge math."""
    p = cents / 100.0
    return round(7.0 * p * (1.0 - p), 1)


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
                        "hits": _f(bs.get("hits")), "pa": _f(bs.get("plateAppearances")),
                        "doubles": _f(bs.get("doubles")), "triples": _f(bs.get("triples")),
                        "hr": _f(bs.get("homeRuns")), "bb": _f(bs.get("baseOnBalls")),
                        "strikeouts": _f(bs.get("strikeOuts")),
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
        note, level = f"Starter changed: {who} — model still on the listed arm, refresh before betting.", "scratch"
    elif h == "tbd" or a == "tbd":
        note, level = "Starter TBD — pitching read is league-average until it's announced.", "provisional"
    elif not final and not (lu.get("home_posted") and lu.get("away_posted")):
        note, level = "Lineups not posted yet — offense assumes the regulars play; may shift once cards are out.", "provisional"
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
    Season IP-per-start regressed toward the league ~5.3 by starts (k=5), with a
    30% blend toward recent form when available. Clamped to a sane range."""
    s = (sp or {}).get("season") or {}
    ip, gs = s.get("ip") or 0.0, s.get("gs") or 0
    if not gs:
        return 5.3
    per = ip / gs
    per = (gs * per + 5 * 5.3) / (gs + 5)          # shrink small samples
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
        "run_pct": round((factor - 1.0) * 100, 1),   # net weather nudge to runs
        "source": wx.get("source"),
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
            "game_number": g.get("gameNumber"),
            "doubleheader": g.get("doubleHeader") not in (None, "N"),
        })
    return out


def analyze_slate(date, season):
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
    lg = _league_avgs(hit, pit, bp, hitplat)
    try:
        pen_fatigue = _bullpen_fatigue(date, season)   # {team_id: {factor, count, arms}}
    except Exception:
        pen_fatigue = {}
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

    # Named bullpens from the deep engine's shared player profiles -> feeds the
    # combo pitching sim with real relievers (K-rates) instead of anonymous draws.
    # IL-aware + cached (same data the 4,000-season deep sim runs on). Best-effort.
    bp_arms = {}
    tids = list({g["home_id"] for g in schedule} | {g["away_id"] for g in schedule})

    def _arms(tid):
        try:
            import deep_data
            prof = deep_data.team_profile(tid, season)
            return [{"kpa": r["kpa"], "era": r["era"]} for r in prof.get("bullpen", [])][:8]
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

        er_home = lg["rpg"] * off_h * pit_a_factor * HOME_RUNS_MULT
        er_away = lg["rpg"] * off_a * pit_h_factor

        # Park + weather scale BOTH teams' run environment. Baking them into the
        # expected runs (not just the headline total) keeps every downstream
        # consumer consistent: the totals/run-line ladders, RFI, the Monte Carlo
        # (which calibrates lineups to these ERs), hitter props and the live
        # remaining-runs model all see the same environment. The moneyline is
        # untouched — a common multiplier cancels in the Pythagorean ratio.
        park = PARK_FACTORS.get(g["home_id"], 1.0)
        winfo = weather_by_pk.get(g["game_pk"]) or {}
        wx_factor = winfo.get("factor", 1.0)
        env = max(0.75, min(1.40, park * wx_factor))
        er_home *= env
        er_away *= env

        p_home = er_home ** PYTH_EXP / (er_home ** PYTH_EXP + er_away ** PYTH_EXP)
        p_home = max(0.04, min(0.96, p_home))
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
            in_game = {"home_score": ls["home_runs"], "away_score": ls["away_runs"],
                       "inning": ls["inning"], "state": ls["state"], "outs": ls["outs"],
                       "exp_rem_home": round(exp_rem_h, 2), "exp_rem_away": round(exp_rem_a, 2),
                       "on_base": [b for b, on in (("1B", ls["on1"]), ("2B", ls["on2"]),
                                                   ("3B", ls["on3"])) if on]}

        pick_home = p_home >= p_away
        pick_name = g["home_name"] if pick_home else g["away_name"]
        pick_prob = p_home if pick_home else p_away

        price_entry, home_abbr, away_abbr = _match_price(
            kalshi_index, abbr_map, g["home_id"], g["away_id"], g["start_epoch"])
        edge = market_prob = pick_price = fee_cents = net_edge = None
        if price_entry:
            pick_abbr = home_abbr if pick_home else away_abbr
            pick_price = price_entry["prices"].get(pick_abbr)
            if pick_price is not None:
                market_prob = round(pick_price, 1)
                edge = round(pick_prob * 100 - pick_price, 1)
                fee_cents = _kalshi_fee(pick_price)
                net_edge = round(edge - fee_cents, 1)

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
            return {"name": name, "hand": h, "era": round(s["era"], 2), "whip": round(s["whip"], 2),
                    "ip": s["ip"], "k9": round(k9_mod, 1) if k9_mod else k9_raw, "k9_raw": k9_raw,
                    "exp_ip": round(_exp_ip_per_start(st), 1),
                    "fip": round(fip, 2) if fip is not None else None,
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
        # Hits scale roughly with the square root of the run environment (Coors
        # lifts runs ~28% but hits ~13%), so hitter props see env^0.55 on top of
        # the opposing pitching. Both lineups share the same park/weather.
        hit_env = env ** 0.55
        ohf_home = _opp_hit_factor(a_sp, tbp(g["away_id"]), lg) * hit_env  # home bats vs away pitching
        ohf_away = _opp_hit_factor(h_sp, tbp(g["home_id"]), lg) * hit_env  # away bats vs home pitching
        def bat_list(lineup, ohf):
            out = []
            for i, b in enumerate(lineup):
                pid = str(b.get("id"))
                cm, pm = savant.quality_mults(xstats.get(pid))
                bp_ = props_mod.batter_props(b, i, ohf, cm, pm, sprint=speed.get(pid))
                if bp_:
                    out.append(bp_)
            return out
        if lu.get("home"):
            hit_home = props_mod.hit_props(lu["home"], ohf_home)
            bat_home = bat_list(lu["home"], ohf_home)
        if lu.get("away"):
            hit_away = props_mod.hit_props(lu["away"], ohf_away)
            bat_away = bat_list(lu["away"], ohf_away)
        # Starter strikeout props, sized to THIS pitcher's expected workload
        # (season/recent IP per start), not a one-size 5.6-inning template.
        ks_home = (props_mod.pitcher_k_props(_regressed_k9((h_sp or {}).get("season")),
                                             _exp_ip_per_start(h_sp)) if h_sp else None)
        ks_away = (props_mod.pitcher_k_props(_regressed_k9((a_sp or {}).get("season")),
                                             _exp_ip_per_start(a_sp)) if a_sp else None)
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
            "exp_runs_home": round(er_home, 2), "exp_runs_away": round(er_away, 2),
            "exp_total": exp_total, "park_factor": park,
            "weather": _weather_block(winfo),
            "home_sp": sp_block(g["home_sp_name"], h_sp, h_hand),
            "away_sp": sp_block(g["away_sp_name"], a_sp, a_hand),
            "home_team": {"ops": round(th(g['home_id']).get("ops", 0), 3),
                          "ops_vs_opp_hand": round(ops_hand(g['home_id'], a_hand) or 0, 3),
                          "rpg": round(th(g['home_id']).get("rpg", 0), 2),
                          "bullpen_era": round(bph.get("era", 0), 2), "bullpen_whip": round(bph.get("whip", 0), 2),
                          "bp_arms": bp_arms.get(g['home_id']),
                          "bullpen_fatigue": fat_h or None,
                          "lineup_factor": round(lf_home, 3) if lf_home else None, "lineup_ops": lops_home,
                          "wins": rh.get("wins"), "losses": rh.get("losses"), "run_diff": rh.get("run_diff")},
            "away_team": {"ops": round(th(g['away_id']).get("ops", 0), 3),
                          "ops_vs_opp_hand": round(ops_hand(g['away_id'], h_hand) or 0, 3),
                          "rpg": round(th(g['away_id']).get("rpg", 0), 2),
                          "bullpen_era": round(bpa.get("era", 0), 2), "bullpen_whip": round(bpa.get("whip", 0), 2),
                          "bp_arms": bp_arms.get(g['away_id']),
                          "bullpen_fatigue": fat_a or None,
                          "lineup_factor": round(lf_away, 3) if lf_away else None, "lineup_ops": lops_away,
                          "wins": ra.get("wins"), "losses": ra.get("losses"), "run_diff": ra.get("run_diff")},
            "pick": pick_name, "pick_is_home": pick_home,
            "pick_prob": round(pick_prob, 4), "pick_pct": round(pick_prob * 100, 1),
            "confidence": round(abs(pick_prob - 0.5) * 200),
            "pick_price_cents": pick_price, "market_prob": market_prob, "edge_cents": edge,
            "fee_cents": fee_cents, "net_edge_cents": net_edge,
        })
    games.sort(key=lambda x: x["pick_prob"], reverse=True)
    return games


def _game_state(g):
    return (g.get("live") or {}).get("state") or ""


def _candidate_legs(games, live_only=False, types=None):
    """All bettable legs across the slate: moneyline + run line + totals + hits.

    Skips games that are already Final (those results are decided). If
    `live_only` is set, includes only games currently in progress. `types` (a set
    of type names) restricts which prop kinds are produced. Each leg is tagged with
    `live`. Combos enforce one leg per game so they stay independent.
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
            if types and typ not in types:
                return
            legs.append({"game_pk": pk, "type": typ, "label": label, "matchup": mu,
                         "prob": prob, "price_cents": price, "live": live})

        if g["pick_prob"] >= 0.5:
            add("ML", f"{g['pick']} to win", g["pick_prob"], g["pick_price_cents"])
        if live:
            continue  # mid-game: props (totals/hits) are stale, so ML only
        p = g.get("props") or {}
        ktot = _ktotals(g)              # only Kalshi-bookable total lines
        rl = p.get("run_line")
        if rl:
            hb = rl["home_by2_pct"] / 100.0
            ab = rl["away_by2_pct"] / 100.0
            if hb >= 0.55:
                add("Run line", f"{rl['home']} win by 2+", hb)
            elif ab >= 0.55:
                add("Run line", f"{rl['away']} win by 2+", ab)
        best_tot = None
        for t in p.get("totals", []):
            if _tradeable_total(t["line"], ktot) is None:
                continue               # skip lines Kalshi won't book
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
        # Starter strikeout props -- every line the model offers (high lines are
        # the long odds); skip the "expected" summary key.
        for key, nm in (("ks_home", p.get("home_sp_name")), ("ks_away", p.get("away_sp_name"))):
            ks = p.get(key)
            if ks and nm:
                for line in sorted(int(k) for k in ks if k.isdigit()):
                    if ks.get(str(line)):
                        add("Ks", f"{nm} {line}+ Ks", ks[str(line)] / 100.0)
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


def _add_spread_legs(add, rl):
    """Offer Kalshi's adjustable spread for both teams: '<team> win by 2+/3+/4+/5+'
    (the 1.5/2.5/3.5/4.5 lines). `add` filters out near-0/near-1 lines."""
    if not rl:
        return
    for tm, key in ((rl.get("home"), "home_by"), (rl.get("away"), "away_by")):
        for m, pct in sorted((rl.get(key) or {}).items(), key=lambda x: int(x[0])):
            add("Run line", f"{tm} win by {m}+", pct / 100.0)


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


def _game_variants(g, types=None):
    """Every line variant for a game, so the combo maker can tune for confidence.
    `types` (a set of type names) restricts which prop kinds are produced.

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
        if types and typ not in types:
            return
        if 0.02 <= prob <= 0.995:
            out.append({"game_pk": pk, "type": typ, "label": label, "matchup": mu,
                        "prob": prob, "price_cents": price, "live": live})

    if g.get("pick_prob") is not None:
        add("ML", f"{g['pick']} to win", g["pick_prob"], g.get("pick_price_cents"))

    # Live games: the pre-game totals/hit props are stale (they ignore runs
    # already scored). Recompute the run line + totals LIVE from the current
    # score + expected remaining runs; skip per-batter hit props (we don't track
    # how many at-bats a hitter already has). The moneyline above is already
    # the live in-game win probability.
    ktot = _ktotals(g)                 # only surface Kalshi-bookable total lines
    ig = g.get("in_game") if live else None
    if live:
        if ig and ig.get("exp_rem_home") is not None:
            lp = props_mod.live_game_props(
                ig["home_score"], ig["away_score"], ig["exp_rem_home"], ig["exp_rem_away"],
                g.get("home_abbr") or "HOME", g.get("away_abbr") or "AWAY")
            _add_spread_legs(add, lp.get("run_line"))
            for t in lp["totals_ladder"]:
                mk = _tradeable_total(t["line"], ktot)
                if mk is None:
                    continue
                add("Total", f"Over {t['line']} runs", t["over_pct"] / 100.0, mk.get("over"))
                add("Total", f"Under {t['line']} runs", t["under_pct"] / 100.0, mk.get("under"))
        return out

    p = g.get("props") or {}
    _add_spread_legs(add, p.get("run_line"))
    for t in p.get("totals_ladder", []):
        mk = _tradeable_total(t["line"], ktot)
        if mk is None:
            continue
        add("Total", f"Over {t['line']} runs", t["over_pct"] / 100.0, mk.get("over"))
        add("Total", f"Under {t['line']} runs", t["under_pct"] / 100.0, mk.get("under"))
    if p.get("rfi_pct") is not None:
        add("RFI", "Run in the 1st inning", p["rfi_pct"] / 100.0)
        add("RFI", "No run in the 1st inning", 1 - p["rfi_pct"] / 100.0)
    # Per-batter ladders (Kalshi adjustable thresholds): hits, total bases, HR.
    # Full thresholds, not just a +/-2 window -- the higher lines (4+ hits, 6+/7+
    # total bases, 2+ HR) are long odds for payout-chasing combos; the variant
    # filter drops any that are too unlikely to be useful.
    for key in ("batters_away", "batters_home"):
        for b in (p.get(key) or [])[:5]:
            for m, hk in ((1, "hit1"), (2, "hit2"), (3, "hit3"), (4, "hit4")):
                if b.get(hk) is not None:
                    add("Hit", f"{b['name']} {m}+ hits", b[hk] / 100.0)
            for m, tk in ((2, "tb2"), (3, "tb3"), (4, "tb4"), (5, "tb5"),
                          (6, "tb6"), (7, "tb7")):
                if b.get(tk) is not None:
                    add("Bases", f"{b['name']} {m}+ total bases", b[tk] / 100.0)
            for m, hk in ((1, "hr1"), (2, "hr2")):
                if b.get(hk) is not None:
                    add("HR", f"{b['name']} {m}+ HR", b[hk] / 100.0)
    return out


def build_target_parlay(games, n_legs, target_pct, target_payout=None, max_legs=12, types=None):
    """Build a parlay.

    Payout mode (target_payout given): the target multiplier governs. We pick the
    legs that reach it with the highest combined probability, preferring your leg
    count and only adding legs when that count physically can't reach the target
    (never forcing in a near-zero "punt" leg). See parlay.payout_combo.

    Confidence mode: each leg tuned to ~the target confidence; take n_legs safest.
    """
    target = max(0.05, min(0.97, target_pct / 100.0))

    if target_payout and target_payout > 1:
        import parlay
        # Confidence floor is REQUIRED: only legs >= the target confidence are
        # eligible. The selector then adds as many qualifying legs as needed to
        # reach the payout (expanding the count), never dropping below the floor.
        groups = []
        for g in games:
            vs = [v for v in _game_variants(g, types) if v["prob"] >= target]
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
        variants = _game_variants(g, types)
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
                            n_sims=5000, max_legs=4, top_n=8, types=None):
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
        cands = [c for c in mlb_sim.build_candidates(g, sim, types) if c["marg"] >= target]
        item = mlb_sim.best_same_game(cands, sim["n"], n_legs, target,
                                      target_payout, max_legs)
        if not item:
            continue
        item["matchup"] = g["matchup"]
        item["has_props"] = bool((g.get("props") or {}).get("batters_home")
                                 or (g.get("props") or {}).get("batters_away"))
        # Deep per-player / per-pitcher simulated detail behind this slip.
        item["breakdown"] = mlb_sim.deep_breakdown(g, sim)
        item.update(_kalshi_payout([(leg, g.get("kalshi_suffix")) for leg in item["legs"]]))
        out.append(item)
    out.sort(key=lambda x: x["combined_prob_pct"], reverse=True)
    return {"games": out[:top_n], "best": out[0] if out else None,
            "n_sims": n_sims}


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
            "RFI": "med", "HR": "low", "HRR": "low"}.get(typ, "med")


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
    import mlb_sim
    import kalshi_mlb
    idx = kalshi_mlb.index()
    rows = []
    for g in games:
        if _game_state(g) in ("Final", "Live"):
            continue
        suffix = g.get("kalshi_suffix")
        if not suffix or not idx.get(suffix):
            continue
        sim = mlb_sim.simulate(g, n_sims)
        for c in mlb_sim.build_candidates(g, sim, types):
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
            sim_pct = round(c["marg"] * 100, 1)         # holistic estimate (combos use this)
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
            rows.append({
                "matchup": g["matchup"], "type": c["type"], "pick": c["label"],
                "our_pct": sim_pct, "model_pct": model_pct,
                "market_cents": cents, "market_payout_x": round(100.0 / cents, 2),
                "edge": edge, "fee_cents": fee, "net_edge": net,
                "confidence": conf,
            })
    # Per-market lean over ALL priced legs. If a whole market type is one-sided
    # (e.g. every starter's Ks read high vs the market), that's a systematic model
    # bias to distrust, not a slate full of independent edges -- surface it so the
    # user can tell the two apart.
    summary = {}
    for r in rows:
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
            "summary": summary, "n_sims": n_sims}


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


def build_mixed_parlay(games, n_legs=4, target_pct=55, target_payout=0,
                       n_sims=5000, max_legs_per_game=3, max_total_legs=8,
                       legs_mode="prefer", payout_mode="off", conn="or", types=None,
                       game_sel=None, include_live=False):
    """One parlay across MULTIPLE games that may stack correlated legs within a
    game and add single legs from others.

    Honest math: within a game legs are correlated, so each game contributes a
    simulated joint probability; across games they're independent, so the parlay
    probability is the product of the per-game joint odds. The independent
    product of every leg's marginal is also returned so the correlation effect
    of any stacked game is visible.

    `legs_mode`/`payout_mode` ("require"/"prefer"/"off") + `conn` ("and"/"or")
    control whether the leg count and the payout are hard requirements or just
    recommendations.
    """
    import mlb_sim
    floor = max(0.05, min(0.97, target_pct / 100.0))

    def team_side(g):
        """If the grid restricts this game to one team, return that team's side."""
        if not game_sel:
            return None
        sel = game_sel.get(g["game_pk"])
        if sel is True or sel is None:
            return None
        return ("home" if sel == g.get("home_name")
                else "away" if sel == g.get("away_name") else None)

    games_bundles = []
    for g in games:
        state = _game_state(g)
        if state == "Final":
            continue
        if game_sel and g["game_pk"] not in game_sel:
            continue
        if state == "Live":
            # In-progress games: props are stale, so only the live win leg is
            # sound. Offer it (model's favored side) as a single-leg bundle.
            if not include_live or not g.get("pick_prob") or g["pick_prob"] < floor:
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
            bundle = {"size": 1, "prob": g["pick_prob"], "legs": [leg]}
            games_bundles.append((g["matchup"] + " 🔴", [bundle], g.get("kalshi_suffix")))
            continue
        sim = mlb_sim.simulate(g, n_sims)
        side = team_side(g)
        cands = [c for c in mlb_sim.build_candidates(g, sim, types)
                 if c["marg"] >= floor and (side is None or _cand_side(c, g) == side)]
        if not cands:
            continue
        bundles = mlb_sim.game_bundles(cands, sim["n"], max_legs=max_legs_per_game)
        if bundles:
            games_bundles.append((g["matchup"], bundles, g.get("kalshi_suffix")))
    if not games_bundles:
        return None
    item = mlb_sim.assemble_mixed(games_bundles, n_legs, target_payout,
                                  legs_mode=legs_mode, payout_mode=payout_mode,
                                  conn=conn, max_total_legs=max_total_legs)
    if item:
        item["n_sims"] = n_sims
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
        except Exception:
            pass
        for p in ps:
            res = results.get(p["game_pk"])
            if res:
                winner, total = res
                store.set_mlb_grade(p["game_pk"], 1 if winner == p["pick_name"] else 0,
                                    winner, actual_total=total)


def build_combos(games, max_legs=3, top_n=6, types=None):
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
    all_legs = _candidate_legs(live_games, types=types)
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

    return {"safest": safest, "best_value": best_value,
            "all": sorted(ml_combos, key=lambda c: c["combined_prob_pct"], reverse=True)[:12],
            "mixed": mixed[:12], "live": live_combos,
            "by_size": by_size, "max_legs_available": max_games}
