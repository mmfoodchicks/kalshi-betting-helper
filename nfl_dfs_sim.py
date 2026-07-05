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
import json as _json
import gzip as _gzip
import random as _random
import time as _time
import threading as _threading
import datetime as _dt

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


def _prop_line(mean, step):
    """A real book-style half-point line near the projection."""
    import math
    if step >= 1:                          # yards: snap to a round number, minus .5
        return round(mean / step) * step - 0.5
    return math.floor(mean) + 0.5          # receptions: X.5


def simulate_game(game, n=4000):
    """Correlated MC of one game. Returns per-player fantasy-point distributions,
    correlation-aware component prop over/unders, and QB->receiver stacks."""
    players = game["players"]
    teams = game.get("teams") or []
    pts = {i: [] for i in range(len(players))}
    comp = {i: {k: [] for k, _, _, _ in _PROP_SPECS} for i in range(len(players))}
    fp_raw = {i: [] for i in range(len(players))}    # for the QB<->WR stack correlation
    gauss = _random.gauss

    for _ in range(n):
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
            c = comp[i]
            c["pass_yd"].append(pass_yd); c["rush_yd"].append(rush_yd)
            c["rec_yd"].append(rec_yd); c["rec"].append(rec)

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
        out.append({"name": pl["name"], "pos": pl["pos"], "team": pl["team"], "opp": pl["opp"],
                    "proj_pts": proj, "sim_mean": round(sum(arr) / len(arr), 1),
                    "floor": round(pct(arr, 0.10), 1), "median": round(pct(arr, 0.50), 1),
                    "ceiling": round(pct(arr, 0.90), 1),
                    "boom_pct": round(100.0 * sum(1 for x in arr if x >= boom) / len(arr), 1),
                    "bust_pct": round(100.0 * sum(1 for x in arr if x <= proj * 0.5) / len(arr), 1)})
        # Component props (correlation is already baked into the samples).
        for key, lab, step, floor_mean in _PROP_SPECS:
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
            "props": props, "stacks": stacks, "n_sims": n}


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


# ---- Week board (all games simmed) -----------------------------------------
def _season():
    t = _dt.date.today()
    return t.year if t.month >= 3 else t.year - 1


_board_inflight = set()


def board(week=1):
    """Non-blocking week sim board: cached if fresh, else kick a background build
    (Sleeper fetch + 16 correlated game sims) and return None while it runs."""
    season = _season()
    key = ("nfl_sim_board", season, week)
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < 1800:
        return hit[1]
    if week not in _board_inflight:
        _board_inflight.add(week)

        def _bg():
            try:
                val = _build_board(season, week)
                if val is not None:
                    _cache[key] = (_time.time(), val)
            finally:
                _board_inflight.discard(week)
        _threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None


def _build_board(season, week, n=4000):
    games = weekly_games(str(season), week)
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
        s = simulate_game(g, n=n)
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
    return {"season": season, "week": week, "n_games": len(sims), "n_sims": n,
            "games": sims, "ceilings": ceilings[:80], "props": props[:80],
            "stacks": stacks[:16], "has_adp": bool(adp),
            "note": "Correlated per-game Monte Carlo seeded by Sleeper's weekly "
                    "projections. Player means are pinned to Sleeper; the sim adds "
                    "floor/ceiling shape and same-game correlation (QB<->WR stacks)."}
