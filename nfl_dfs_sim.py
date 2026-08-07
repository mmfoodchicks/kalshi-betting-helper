"""NFL per-game correlated Monte Carlo, seeded by Sleeper's weekly projections.

Sleeper publishes per-week projections that carry BOTH fantasy points and the
component stats behind them (pass/rush/rec yards, TDs, receptions), and every
row is tagged with the game it belongs to. That lets us stop treating each
player as an isolated projection and instead *simulate the game*: draw a shared
game environment (pace/total), a game script (who's ahead -> RB carries vs.
garbage-time passing), and a per-team passing latent that a QB shares with his
own pass-catchers. The result is a fantasy-point DISTRIBUTION per player -- floor
/ median / ceiling / boom-rate -- with the correlations best-ball and DFS live
on (a QB + his WR boom together; a game shootout lifts everyone).

Marginals stay centred on Sleeper's projection (every scaling factor is mean-1 /
mean-0), so the sim only adds shape + correlation, it doesn't invent new means.
"""

import urllib.request
import clock
import json as _json
import gzip as _gzip
import random as _random
import time as _time
import threading as _threading

_PROJ = "https://api.sleeper.com/projections/nfl/{season}/{week}"
_cache = {}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = _gzip.decompress(data)
    return _json.loads(data)


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    if val is not None:
        _cache[key] = (_time.time(), val)
    return val


# ---- PPR scoring ------------------------------------------------------------
def _ppr(pass_yd, pass_td, ints, rush_yd, rush_td, rec, rec_yd, rec_td, fum):
    return (pass_yd * 0.04 + pass_td * 4 - ints * 2
            + rush_yd * 0.1 + rush_td * 6
            + rec * 1.0 + rec_yd * 0.1 + rec_td * 6 - fum * 2)


# ---- Weekly projections (Sleeper), grouped by game --------------------------
_POS = ("QB", "RB", "WR", "TE")


def weekly_games(season, week):
    """{game_id: {label, players:[{name,pos,team,opp, means:{...}, proj_pts}]}} for a
    week's skill players, from Sleeper. Cached 1h."""
    def build():
        q = "&".join(f"position[]={p}" for p in _POS)
        url = f"{_PROJ.format(season=season, week=week)}?season_type=regular&{q}&order_by=pts_ppr"
        try:
            rows = _get(url)
        except Exception:
            return None
        games = {}
        for r in rows:
            st = r.get("stats") or {}
            pts = st.get("pts_ppr")
            if not pts or pts <= 0:
                continue
            p = r.get("player") or {}
            gid = r.get("game_id")
            means = {"pass_yd": st.get("pass_yd", 0.0) or 0.0,
                     "pass_td": st.get("pass_td", 0.0) or 0.0,
                     "int": st.get("pass_int", 0.0) or 0.0,
                     "rush_yd": st.get("rush_yd", 0.0) or 0.0,
                     "rush_td": st.get("rush_td", 0.0) or 0.0,
                     "rec": st.get("rec", 0.0) or 0.0,
                     "rec_yd": st.get("rec_yd", 0.0) or 0.0,
                     "rec_td": st.get("rec_td", 0.0) or 0.0,
                     "fum": st.get("fum_lost", 0.0) or 0.0}
            games.setdefault(gid, {"players": [], "label": None})["players"].append({
                "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                "pos": p.get("position"), "team": r.get("team"), "opp": r.get("opponent"),
                "means": means, "proj_pts": round(pts, 2)})
        for gid, g in games.items():
            teams = []
            for pl in g["players"]:
                if pl["team"] not in teams:
                    teams.append(pl["team"])
            g["teams"] = teams
            g["label"] = f"{teams[1]} @ {teams[0]}" if len(teams) >= 2 else (teams[0] if teams else "?")
        return games or None
    return _cached(("nfl_sleeper", season, week), 3600, build)


# DraftKings scoring, for turning a projected stat line into projected points.
_DK = {"pass_yd": 0.04, "pass_td": 4.0, "int": -1.0, "rush_yd": 0.1,
       "rush_td": 6.0, "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "fum": -1.0}


def dk_points(means):
    return sum(_DK[k] * (means.get(k) or 0.0) for k in _DK)


def preseason_games(season, week):
    """weekly_games' shape for exhibitions, where Sleeper projects nothing.

    Every stat line is built the same way the preseason game engine builds one:
    the measured team-game budget from 96 exhibitions, distributed by the
    INVERTED usage model, and scaled to what Kalshi's ladder says the game is
    worth. So a DFS lineup and a combo slip on the same board are reading the
    same projection rather than two that happen to look similar."""
    def build():
        import nfl_live, nfl_preseason, kalshi_nfl, nfl_game_sim
        try:
            sched = nfl_live.schedule(week, int(season), seasontype=1) or []
        except Exception:
            return None
        try:
            ros = nfl_preseason.rosters(season) or {}
        except Exception:
            return None
        try:
            idx = kalshi_nfl.index()
        except Exception:
            idx = {}
        games = {}
        for gm in sched:
            h, a = gm.get("home"), gm.get("away")
            if not h or not a:
                continue
            # Kalshi's number where there is one; the measured league average
            # where there is not, so a game with no market still gets a lineup.
            pts = {h: nfl_preseason.PRE_TEAM["points"], a: nfl_preseason.PRE_TEAM["points"]}
            props = {}
            suffix = nfl_game_sim._suffix_for(idx, h, a)
            if suffix:
                try:
                    imp = kalshi_nfl.implied(suffix) or {}
                    props = kalshi_nfl.prop_ladders(suffix) or {}
                except Exception:
                    imp = {}
                tot = float(imp.get("total") or 0) or None
                if tot:
                    m = float(imp.get("margin") or 0.0)
                    fav = imp.get("favourite")
                    edge = m if fav == h else (-m if fav == a else 0.0)
                    pts = {h: max(6.0, (tot + edge) / 2.0), a: max(6.0, (tot - edge) / 2.0)}
            rows = []
            for ab, opp in ((h, a), (a, h)):
                names = {nfl_preseason._key(p["name"]) for p in (ros.get(ab) or [])}
                mine = {k: v for k, v in props.items() if k[1] in names}
                prof = nfl_game_sim.profile_from_points(ab, ab, pts[ab], ab == h,
                                                        ros.get(ab), mine)
                for pl in prof["players"]:
                    means = {"pass_yd": pl["pass_yd"], "pass_td": pl["pass_td"],
                             "int": pl.get("pass_int", 0.0), "rush_yd": pl["rush_yd"],
                             "rush_td": pl["rush_td"], "rec": pl["rec"],
                             "rec_yd": pl["rec_yd"], "rec_td": pl["rec_td"],
                             "fum": 0.0}
                    rows.append({"name": pl["name"], "pos": pl["pos"], "team": ab,
                                 "opp": opp, "means": means,
                                 "proj_pts": round(dk_points(means), 2),
                                 "note": pl.get("note")})
            if rows:
                games[f"pre-{a}@{h}"] = {"players": rows, "teams": [h, a],
                                         "label": f"{a} @ {h}"}
        return games or None
    return _cached(("nfl_pre_dfs", season, week), 3600, build)


# ---- Correlated game simulation --------------------------------------------
# How much each latent swings a stat (mean-1 multiplicative unless noted).
_ENV_SD = 0.18          # shared pace/total: a shootout lifts both teams' volume
_QB_SD = 0.26           # a team's passing day -- QB shares it with his WR/TE
_RUSH_SD = 0.24         # rusher-specific volume noise
_SCRIPT_SD = 1.0        # game script; + = team ahead (more rush, less late pass)
_SCRIPT_RUSH = 0.14     # leading -> more rush yards
_SCRIPT_PASS = 0.12     # trailing -> garbage-time pass volume
_TD_SD = 0.0            # TDs handled as Poisson, no extra multiplier


def _pois(mean):
    """Poisson draw (small means) via inversion."""
    if mean <= 0:
        return 0
    import math
    L, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= _random.random()
        if p <= L:
            return k
        k += 1


# Prop components we expose (stat key -> (label, line rounding step, min mean to offer)).
# Floors keep the board to players a book would actually post a line on.
_PROP_SPECS = [("pass_yd", "pass yds", 5, 180), ("rush_yd", "rush yds", 5, 30),
               ("rec_yd", "rec yds", 5, 30), ("rec", "receptions", 0.5, 3.0)]
# The "is this worth posting a line on" floors above are regular-season sized and
# post NOTHING in August: a preseason quarterback projects around 100 yards
# against a 180 cutoff, so the whole Pick 6 board came back empty. These are the
# same idea against the measured exhibition budget -- roughly a 40% game.
_PRE_PROP_FLOOR = {"pass_yd": 40.0, "rush_yd": 12.0, "rec_yd": 12.0, "rec": 1.0}


def _prop_line(mean, step):
    """A real book-style half-point line near the projection."""
    import math
    if step >= 1:                          # yards: snap to a round number, minus .5
        return round(mean / step) * step - 0.5
    return math.floor(mean) + 0.5          # receptions: X.5


def simulate_game(game, n=4000, with_samples=False, preseason=False):
    """Correlated MC of one game. Returns per-player fantasy-point distributions,
    correlation-aware component prop over/unders, and QB->receiver stacks.
    with_samples=True attaches each player's rescaled point array (`arr`) so a DFS
    contest sim can score whole lineups with the within-game correlation intact."""
    players = game["players"]
    teams = game.get("teams") or []
    pts = {i: [] for i in range(len(players))}
    comp = {i: {k: [] for k, _, _, _ in _PROP_SPECS} for i in range(len(players))}
    fp_raw = {i: [] for i in range(len(players))}    # for the QB<->WR stack correlation
    # Per-iteration offensive output by team. A defense's score is mostly a
    # function of what the OTHER offense did to it, and nothing here tracked
    # that -- so the two defenses in a game were drawn independently, as if a
    # shootout could punish one and spare the other.
    team_fp = {t: [] for t in teams}
    gauss = _random.gauss

    for _ in range(n):
        by_team = {t: 0.0 for t in teams}
        env = max(0.4, gauss(1.0, _ENV_SD))                 # shared game pace/total
        qb_lat = {t: max(0.0, gauss(1.0, _QB_SD)) for t in teams}
        script = {t: gauss(0.0, _SCRIPT_SD) for t in teams}
        if len(teams) >= 2:                                  # scripts are opposite
            script[teams[1]] = -script[teams[0]]
        for i, pl in enumerate(players):
            m, t = pl["means"], pl["team"]
            ql, sc = qb_lat.get(t, 1.0), script.get(t, 0.0)
            pass_f = env * ql * (1 - _SCRIPT_PASS * sc)      # trailing -> more pass
            rush_f = env * (1 + _SCRIPT_RUSH * sc) * max(0.0, gauss(1.0, _RUSH_SD))
            rec_f = env * ql * (1 - _SCRIPT_PASS * sc)       # receivers ride the QB latent
            pass_yd, rush_yd = m["pass_yd"] * pass_f, m["rush_yd"] * rush_f
            rec_yd, rec = m["rec_yd"] * rec_f, m["rec"] * rec_f
            pass_td = _pois(m["pass_td"] * env * ql)
            rush_td = _pois(m["rush_td"] * env * (1 + _SCRIPT_RUSH * sc))
            rec_td = _pois(m["rec_td"] * env * ql)
            fp = _ppr(pass_yd, pass_td, _pois(m["int"]), rush_yd, rush_td,
                      rec, rec_yd, rec_td, _pois(m["fum"]))
            pts[i].append(fp)
            fp_raw[i].append(fp)
            if t in by_team:
                by_team[t] += fp
            c = comp[i]
            c["pass_yd"].append(pass_yd); c["rush_yd"].append(rush_yd)
            c["rec_yd"].append(rec_yd); c["rec"].append(rec)
        for t, v in by_team.items():
            team_fp[t].append(v)

    def pct(arr, q):
        s = sorted(arr)
        return s[min(len(s) - 1, int(q * len(s)))]

    out, props = [], []
    for i, pl in enumerate(players):
        arr = pts[i]
        raw = sum(arr) / len(arr)
        proj = pl["proj_pts"]
        f = proj / raw if raw > 0 else 1.0                   # pin points-mean to Sleeper
        arr = [x * f for x in arr]
        boom = proj * 1.5
        row = {"name": pl["name"], "pos": pl["pos"], "team": pl["team"], "opp": pl["opp"],
               "proj_pts": proj, "sim_mean": round(sum(arr) / len(arr), 1),
               "floor": round(pct(arr, 0.10), 1), "median": round(pct(arr, 0.50), 1),
               "ceiling": round(pct(arr, 0.90), 1),
               "boom_pct": round(100.0 * sum(1 for x in arr if x >= boom) / len(arr), 1),
               "bust_pct": round(100.0 * sum(1 for x in arr if x <= proj * 0.5) / len(arr), 1)}
        if with_samples:
            row["arr"] = [round(x, 2) for x in arr]
        out.append(row)
        # Component props (correlation is already baked into the samples).
        for key, lab, step, floor_mean in _PROP_SPECS:
            if preseason:
                floor_mean = _PRE_PROP_FLOOR.get(key, floor_mean)
            cs = comp[i][key]
            mean = sum(cs) / len(cs)
            if mean < floor_mean:
                continue
            line = _prop_line(mean, step)
            p_over = sum(1 for x in cs if x > line) / len(cs)
            side, prob = ("More", p_over) if mean >= line else ("Less", 1 - p_over)
            if not (0.52 <= prob <= 0.9):
                continue
            props.append({"player": pl["name"], "pos": pl["pos"], "team": pl["team"],
                          "stat": lab, "line": round(line, 1), "side": side,
                          "prob": round(prob * 100, 1), "proj": round(mean, 1),
                          "matchup": game["label"]})

    out.sort(key=lambda x: -x["ceiling"])
    props.sort(key=lambda x: -x["prob"])
    stacks = _stacks(players, fp_raw, teams)
    return {"label": game["label"], "teams": teams, "players": out,
            "props": props, "stacks": stacks, "n_sims": n,
            # Per-iteration offensive output by team, so a defense can be scored
            # against what it actually faced in that same iteration.
            "team_fp": team_fp if with_samples else None}


def _corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def _stacks(players, fp_raw, teams):
    """QB + his two best pass-catchers per team, with the sim's combined ceiling and
    the QB<->receiver correlation that makes the stack a best-ball weapon."""
    out = []
    for t in teams:
        qb_i = next((i for i, p in enumerate(players) if p["team"] == t and p["pos"] == "QB"), None)
        if qb_i is None:
            continue
        recs = sorted([i for i, p in enumerate(players)
                       if p["team"] == t and p["pos"] in ("WR", "TE")],
                      key=lambda i: -players[i]["proj_pts"])[:2]
        if not recs:
            continue
        combo = [fp_raw[qb_i][s] + sum(fp_raw[r][s] for r in recs) for s in range(len(fp_raw[qb_i]))]
        combo.sort()
        ceil = combo[min(len(combo) - 1, int(0.90 * len(combo)))]
        corr = sum(_corr(fp_raw[qb_i], fp_raw[r]) for r in recs) / len(recs)
        out.append({"team": t, "qb": players[qb_i]["name"],
                    "receivers": [players[r]["name"] for r in recs],
                    "combined_ceiling": round(ceil, 1),
                    "qb_wr_corr": round(corr, 2)})
    return out


# ---- DST projections (Sleeper) + full DFS player pool ----------------------
def dst_projections(season, week):
    """{team_abbr: {nickname, proj}} for team defenses. Cached 1h."""
    def build():
        url = (f"{_PROJ.format(season=season, week=week)}?season_type=regular"
               f"&position[]=DEF&order_by=pts_ppr")
        try:
            rows = _get(url)
        except Exception:
            return None
        out = {}
        for r in rows:
            pts = (r.get("stats") or {}).get("pts_ppr")
            if pts is None:
                continue
            p = r.get("player") or {}
            out[r.get("team")] = {"nickname": p.get("last_name") or r.get("team"),
                                  "proj": round(pts, 2)}
        return out or None
    return _cached(("nfl_dst", season, week), 3600, build)


def player_pool(week, n=3000, preseason=False):
    """Every DFS-relevant player for a week: skill players carry correlated point
    arrays from the game sims; DSTs carry independent Normal-sampled arrays from
    Sleeper's team-defense projection. {name: {pos, team, proj, ceiling, floor, arr}}.
    Cached 30m (this is the heavy correlated sim over the whole slate)."""
    season = _season()

    def build():
        games = (preseason_games(str(season), week) if preseason
                 else weekly_games(str(season), week))
        if not games:
            return None
        pool = {}
        pre_dst = {}
        for gid, g in games.items():
            sim = simulate_game(g, n=n, with_samples=True, preseason=preseason)
            for p in sim["players"]:
                pool[p["name"]] = {"pos": p["pos"], "team": p["team"], "opp": p.get("opp"),
                                   "proj": p["proj_pts"], "ceiling": p["ceiling"],
                                   "floor": p["floor"], "arr": p["arr"]}
            # In August a defense is scored against the offense it faced in that
            # same iteration, so the two defenses in a game move together and a
            # shootout punishes both. Sleeper's regular-season DST projection --
            # which dst_projections asks for unconditionally -- knows nothing
            # about either.
            tfp = sim.get("team_fp") or {}
            if preseason and len(tfp) >= 2:
                import nfl_preseason as _np
                ts = list(tfp)
                for me, opp in ((ts[0], ts[1]), (ts[1], ts[0])):
                    arr = _np.dst_from_offense(tfp[opp], n, _random)
                    if arr:
                        pre_dst[me] = arr
        dst = dst_projections(str(season), week) or {}
        for team, d in dst.items():
            arr = pre_dst.get(team)
            if arr:
                proj = round(sum(arr) / len(arr), 2)
            else:
                proj = d["proj"]
                sd = 0.7 * proj + 4.0                    # DST scoring is high-variance
                arr = [round(max(-4.0, _random.gauss(proj, sd)), 2) for _ in range(n)]
            for key in {d["nickname"], team}:            # match DK by nickname or abbr
                pool[key] = {"pos": "DST", "team": team, "opp": None, "proj": proj,
                             "ceiling": round(sorted(arr)[int(0.9 * len(arr))], 1),
                             "floor": round(sorted(arr)[int(0.1 * len(arr))], 1), "arr": arr}
        # A preseason defense whose team Sleeper did not list still gets its
        # simulated array rather than dropping out of the pool entirely.
        for team, arr in pre_dst.items():
            if team in pool:
                continue
            pool[team] = {"pos": "DST", "team": team, "opp": None,
                          "proj": round(sum(arr) / len(arr), 2),
                          "ceiling": round(sorted(arr)[int(0.9 * len(arr))], 1),
                          "floor": round(sorted(arr)[int(0.1 * len(arr))], 1), "arr": arr}
        return pool or None
    return _cached(("nfl_pool", season, week, n, bool(preseason)), 1800, build)


# ---- Week board (all games simmed) -----------------------------------------
def _season():
    t = clock.today_et()
    return t.year if t.month >= 3 else t.year - 1


_board_inflight = set()


def board(week=1, preseason=False):
    """Non-blocking week sim board: cached if fresh, else kick a background build
    (Sleeper fetch + 16 correlated game sims) and return None while it runs."""
    season = _season()
    key = ("nfl_sim_board", season, week, bool(preseason))
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < 1800:
        return hit[1]
    if key not in _board_inflight:
        _board_inflight.add(key)

        def _bg():
            try:
                val = _build_board(season, week, preseason=preseason)
                if val is not None:
                    _cache[key] = (_time.time(), val)
            finally:
                _board_inflight.discard(key)
        _threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None


def _build_board(season, week, n=4000, preseason=False):
    games = (preseason_games(str(season), week) if preseason
             else weekly_games(str(season), week))
    if not games:
        return None
    # Sleeper consensus draft rank (~ADP) for best-ball value, if available.
    adp = {}
    try:
        import nfl_adp
        for nm, v in (nfl_adp.consensus() or {}).items():
            if v.get("rank"):
                adp[nm] = v["rank"]                 # keyed by lowercased name
    except Exception:
        adp = {}

    sims, ceilings, props, stacks = [], [], [], []
    for gid, g in games.items():
        s = simulate_game(g, n=n, preseason=preseason)
        sims.append(s)
        props.extend(s["props"])
        for st in s["stacks"]:
            st["matchup"] = s["label"]
            stacks.append(st)
        for p in s["players"]:
            p2 = dict(p, matchup=s["label"], adp=adp.get(p["name"].lower()))
            ceilings.append(p2)
    if not sims:
        return None
    sims.sort(key=lambda x: -max((p["ceiling"] for p in x["players"]), default=0))
    ceilings.sort(key=lambda x: -x["ceiling"])
    # Ranking props by raw probability just surfaces the lowest-variance stat
    # (receptions unders) over and over. Instead round-robin across the stat types
    # -- each already sorted by confidence -- so the board stays varied, capped at
    # two props per player.
    buckets = {}
    for p in sorted(props, key=lambda x: -x["prob"]):
        buckets.setdefault(p["stat"], []).append(p)
    order = ["pass yds", "rush yds", "rec yds", "receptions"]
    props, seen = [], {}
    while any(buckets.get(s) for s in order):
        for s in order:
            b = buckets.get(s)
            if not b:
                continue
            p = b.pop(0)
            if seen.get(p["player"], 0) >= 2:
                continue
            seen[p["player"]] = seen.get(p["player"], 0) + 1
            props.append(p)
    stacks.sort(key=lambda x: -x["combined_ceiling"])
    note = ("Correlated per-game Monte Carlo seeded by Sleeper's weekly "
            "projections. Player means are pinned to Sleeper; the sim adds "
            "floor/ceiling shape and same-game correlation (QB<->WR stacks).")
    if preseason:
        note = ("PRESEASON lineups. Sleeper projects nothing for exhibitions, so "
                "the means are measured instead: one team-game from 96 of last "
                "August's, distributed by a usage model that runs INVERTED -- the "
                "backup quarterback throws about a third more than the starter, a "
                "camp-body running back gets roughly double a starter's touches, "
                "and the starters sit after a series. Where Kalshi books a player "
                "his own ladder sets his level. Chalk is upside down here: the "
                "names at the top of a DraftKings salary list are the ones who "
                "will not play.")
    return {"season": season, "week": week, "n_games": len(sims), "n_sims": n,
            "games": sims, "ceilings": ceilings[:80], "props": props[:80],
            "stacks": stacks[:16], "has_adp": bool(adp),
            "preseason": bool(preseason), "note": note}
