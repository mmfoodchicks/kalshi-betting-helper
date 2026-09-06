"""DFS look-back: the builder's lineups, graded against DraftKings' real
scoring after the event, per objective, and fed back in.

Every DFS number on the site is a projection until something checks it.
This module closes that loop for the lineup builder the way sliplog does
for parlays: each lineup built for a BIG event (a full F1 or Cup race, an
NFL or MLB main slate, a full UFC card, a 1,000-entry contest) is logged at
build time under its objective -- cash (max projection), GPP (max ceiling),
leverage, and UFC's most-confident -- with every player's projection and
the lineup's simulated floor / median / ceiling. Once the event is over the
players' real DraftKings points are fetched from the feeds the app already
reads (jolpica for F1, NASCAR's weekend feed, MLB's box scores, Sleeper's
weekly NFL stats), scored with the same dk_scoring tables the sims use, and
each lineup gets an actual total, a per-player error, and where the actual
landed against its own floor / median / ceiling.

The graded record then feeds back as a CORRECTION the builders apply before
they optimize: per sport and position group, the ratio of actual to
projected points, shrunk toward 1 by sample size and used only when it beats
"no correction" out of sample (leave-one-event-out) on at least five graded
events. A correction that only fits noise never ships. The report shows the
scoreboard per objective, the coverage of the floor / ceiling band, and the
corrections in force, so the owner reads what the builder learned, not a
promise that it did.

UFC and LoL lineups are logged but not graded yet: DraftKings' MMA score
runs on significant strikes and control time, and its LoL score on per-map
objectives, and no feed the app reads carries them.
"""

import datetime
import hashlib
import json
import time
from collections import defaultdict

import clock
import dk_scoring
import errlog
import racing
import store

_JOLPICA = "https://api.jolpi.ca/ergast/f1"
_NASCAR = "https://cf.nascar.com/cacher/{year}/{series}/{race_id}/weekend-feed.json"
_NASCAR_LIST = "https://cf.nascar.com/cacher/{year}/{series}/race_list_basic.json"
_SLEEPER_STATS = "https://api.sleeper.com/stats/nfl/{season}/{week}?season_type=regular"

GRADED_SPORTS = ("f1", "nascar", "mlb", "nfl")
NOT_GRADED = {"ufc": "DraftKings scores MMA on significant strikes and control time; no feed we read carries them",
              "lol": "DraftKings scores LoL on per-map kills, assists, CS and objectives; the match feed is not wired yet"}
OBJECTIVES = ("projection", "ceiling", "leverage", "confidence", "median")

_GRADE_EVERY_S = 30 * 60
_state = {"last_grade": 0.0, "corr": {}, "corr_ts": 0.0}
_MIN_EVENTS = 5          # graded events before a correction may ship
_SHRINK_K = 6.0          # events of prior weight pulling the ratio toward 1
_GATE_SHARE = 0.6        # leave-one-event-out: must beat identity this often
# Backtested events seed the record so the first correction has a direction
# instead of a coin flip, but they were run blind on form alone (no practice
# pace, no this-weekend reads), so each counts half a live event; and every
# event decays by recency (half-life eight events) so what happens from today
# on takes the wheel quickly. Both measured against nothing yet: they are the
# owner's stated intent ("weigh it harder from today on"), pinned by guards.
_BACKTEST_W = 0.5
_HALF_LIFE_EVENTS = 8.0
SEED_PATH = "seeds/dfs_backtest_seed.json"


# ---- storage -----------------------------------------------------------------
def _ensure():
    with store._lock, store._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS dfs_builds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER, sport TEXT, event_key TEXT, event_date TEXT, week INTEGER,
                draft_group_id INTEGER, contest_id INTEGER, contest_size INTEGER,
                entry_fee REAL, objective TEXT, mode TEXT, lineup_ix INTEGER,
                players TEXT, total_salary INTEGER, total_proj REAL,
                floor REAL, median REAL, ceiling REAL, max REAL,
                big INTEGER, big_reason TEXT, key TEXT UNIQUE,
                graded INTEGER DEFAULT 0, actual REAL, actual_players TEXT,
                n_missing INTEGER, bucket TEXT, graded_ts INTEGER, note TEXT
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_dfs_builds_graded ON dfs_builds(graded, sport)")
        cols = {r[1] for r in c.execute("PRAGMA table_info(dfs_builds)")}
        if "backtest" not in cols:
            try:                   # several workers migrate at once on boot
                c.execute("ALTER TABLE dfs_builds ADD COLUMN backtest INTEGER DEFAULT 0")
            except Exception as _e:
                errlog.note("DFSLB-init-bt", _e)   # a sibling just added it
        c.execute("""
            CREATE TABLE IF NOT EXISTS dfs_actuals (
                sport TEXT, event_key TEXT, payload TEXT, ts INTEGER,
                PRIMARY KEY (sport, event_key)
            )""")


def _norm(s):
    return racing.norm_name(s or "")


def n_games_from_csv(text):
    """Distinct 'Game Info' values in a DK CSV: the slate's size."""
    games = set()
    for ln in (text or "").splitlines()[1:]:
        cols = ln.split(",")
        if len(cols) >= 7 and cols[6].strip():
            games.add(cols[6].strip())
    return len(games)


# ---- what counts as a big event ----------------------------------------------
def is_big(sport, req, n_games, n_players):
    """(big?, reason). Only these are logged: the owner wants the builder graded
    on the events that matter, not a Tuesday two-game slate."""
    try:
        size = int(req.get("contest_size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size >= 1000:
        return True, f"{size:,}-entry contest"
    if sport in ("f1", "nascar"):
        return True, "every race"
    if sport == "nfl" and n_games >= 8:
        return True, f"{n_games}-game main slate"
    if sport == "mlb" and n_games >= 8:
        return True, f"{n_games}-game main slate"
    if sport == "ufc" and n_players >= 20:
        return True, f"full card ({n_players} fighters)"
    if sport == "lol" and n_games >= 3:
        return True, f"{n_games}-match slate"
    return False, None


# ---- event identity ----------------------------------------------------------
def _f1_schedule(season):
    def build():
        d = racing._get_json(f"{_JOLPICA}/{season}.json?limit=40")
        return d.get("MRData", {}).get("RaceTable", {}).get("Races", []) or None
    return racing._cached(("dfslb_f1_sched", season), 6 * 3600, build) or []


def _nascar_races(year):
    def build():
        return racing._get_json(_NASCAR_LIST.format(year=year, series=1)) or None
    return racing._cached(("dfslb_nas_list", year), 6 * 3600, build) or []


def event_of(sport, req, today=None):
    """(event_key, event_date, week) for a build made today."""
    today = today or clock.today_et().isoformat()
    if sport == "f1":
        for r in _f1_schedule(today[:4]):
            if (r.get("date") or "") >= today:
                return f"f1:{r['date']}:{r['round']}", r["date"], None
        return f"f1:{today}:?", today, None
    if sport == "nascar":
        races = sorted((r for r in _nascar_races(today[:4]) if r.get("race_id")),
                       key=lambda r: r.get("race_date") or "")
        for r in races:
            if (r.get("race_date") or "")[:10] >= today:
                return f"nascar:{r['race_id']}", (r.get("race_date") or today)[:10], None
        return f"nascar:{today}", today, None
    if sport == "nfl":
        try:
            week = int(req.get("week") or 1)
        except (TypeError, ValueError):
            week = 1
        season = int(today[:4]) if int(today[5:7]) >= 3 else int(today[:4]) - 1
        return f"nfl:{season}:w{week}", today, week
    date = (req.get("date") or today)[:10]
    dg = req.get("draft_group_id")
    return f"{sport}:{date}" + (f":{dg}" if dg else ""), date, None


# ---- logging a build ---------------------------------------------------------
def _lineups_of(res):
    """[(players, total_salary, total_proj, floor, median, ceiling, max)] from
    any of the three builders' result shapes."""
    out = []
    lus = res.get("lineups") or []
    if not lus and res.get("lineup"):
        lus = [{"lineup": res["lineup"], "players": res["lineup"],
                "total_salary": res.get("total_salary"), "total_proj": res.get("total_proj"),
                "sim": res.get("sim"), "floor": res.get("total_floor"),
                "ceil": res.get("total_ceil")}]
    for lu in lus:
        rows = lu.get("lineup") or lu.get("players") or []
        sim = lu.get("sim") or {}
        players = []
        for p in rows:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            players.append({
                "name": p["name"], "salary": p.get("salary"), "proj": p.get("proj"),
                "pos": p.get("pos") or p.get("slot") or p.get("kind"),
                "slot": p.get("slot"), "team": p.get("team"),
                "captain": bool(p.get("captain")) or (p.get("slot") == "CPT")})
        if not players:
            continue
        floor = sim.get("floor", lu.get("floor"))
        median = sim.get("median", lu.get("median"))
        ceil = sim.get("ceiling", lu.get("ceil", lu.get("ceiling")))
        mx = sim.get("max", lu.get("max"))
        total_proj = lu.get("total_proj", lu.get("proj"))
        if total_proj is None:
            total_proj = sum((p["proj"] or 0) * (1.5 if p["captain"] and False else 1) for p in players)
        out.append((players, lu.get("total_salary", lu.get("salary")), total_proj,
                    floor, median, ceil, mx))
    return out


def log_build(sport, req, res, auto_slate=None, csv_text=""):
    """Log every lineup of a big-event build. First write wins per (event,
    objective, player set); returns how many rows were inserted."""
    if not isinstance(res, dict) or res.get("error"):
        return 0
    sport = (sport or "").lower()
    n_games = n_games_from_csv(csv_text)
    n_players = int((auto_slate or {}).get("n_players") or res.get("pool") or 0)
    big, why = is_big(sport, req, n_games, n_players)
    if not big:
        return 0
    _ensure()
    event_key, event_date, week = event_of(sport, req)
    objective = str(req.get("objective") or res.get("objective") or "projection")
    if sport == "mlb":
        objective = "ceiling" if objective == "ceiling" else "median"
    try:
        contest_id = int(req.get("contest_id") or 0) or None
        contest_size = int(req.get("contest_size") or 0) or None
        entry_fee = float(req.get("entry_fee") or 0) or None
    except (TypeError, ValueError):
        contest_id = contest_size = entry_fee = None
    dg = (auto_slate or {}).get("draft_group_id") or req.get("draft_group_id")
    n = 0
    with store._lock, store._conn() as c:
        for ix, (players, sal, proj, floor, median, ceil, mx) in enumerate(_lineups_of(res)):
            names = sorted(f"{p['name']}{'*' if p['captain'] else ''}" for p in players)
            key = hashlib.sha1(f"{sport}|{event_key}|{objective}|{'|'.join(names)}".encode()).hexdigest()[:20]
            cur = c.execute(
                "INSERT OR IGNORE INTO dfs_builds (ts, sport, event_key, event_date, week, "
                "draft_group_id, contest_id, contest_size, entry_fee, objective, mode, lineup_ix, "
                "players, total_salary, total_proj, floor, median, ceiling, max, big, big_reason, key) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (int(time.time()), sport, event_key, event_date, week, dg, contest_id,
                 contest_size, entry_fee, objective, str(req.get("mode") or res.get("mode") or ""),
                 ix, json.dumps(players), sal, proj, floor, median, ceil, mx, why, key))
            n += cur.rowcount or 0
    return n


# ---- actual DraftKings points per event --------------------------------------
def _f1_laps_led(season, rnd):
    led = defaultdict(int)
    off, total = 0, None
    while True:
        d = racing._get_json(f"{_JOLPICA}/{season}/{rnd}/laps.json?limit=100&offset={off}")
        md = d.get("MRData", {})
        total = int(md.get("total") or 0)
        races = md.get("RaceTable", {}).get("Races", [])
        n = 0
        for lap in (races[0].get("Laps", []) if races else []):
            for t in lap.get("Timings", []):
                n += 1
                if str(t.get("position")) == "1":
                    led[t.get("driverId")] += 1
        off += 100
        if n == 0 or off >= total or off > 3000:
            break
    return led


def actuals_f1(event_key):
    """{norm name: pts} for every driver, plus constructors under their
    normalized team name and a last-name index. DK F1: finish table + place
    differential + 0.25 per lap led + 3 fastest lap + 5 defeated teammate +
    1 classified. Constructors follow the app's own rule (both cars' finish
    points, laps led, both-classified / top-10 / podium bonuses)."""
    _, date, rnd = event_key.split(":")[:3]
    season = date[:4]
    d = racing._get_json(f"{_JOLPICA}/{season}/{rnd}/results.json?limit=40")
    races = d.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races or not races[0].get("Results"):
        return None
    results = races[0]["Results"]
    led = _f1_laps_led(season, rnd)
    n_start = len(results)
    rows, by_team = [], defaultdict(list)
    for r in results:
        try:
            pos = int(r.get("position"))
        except (TypeError, ValueError):
            continue
        try:
            grid = int(r.get("grid") or 0) or n_start
        except (TypeError, ValueError):
            grid = n_start
        st = r.get("status") or ""
        classified = st == "Finished" or st.startswith("+")
        fl = str(((r.get("FastestLap") or {}).get("rank"))) == "1"
        did = r["Driver"]["driverId"]
        pts = (dk_scoring.f1_finish(pos) + dk_scoring.F1["place_differential"] * (grid - pos)
               + dk_scoring.F1["lap_led"] * led.get(did, 0)
               + (dk_scoring.F1["fastest_lap"] if fl else 0)
               + (dk_scoring.F1["classified"] if classified else 0))
        team = (r.get("Constructor") or {}).get("name") or ""
        name = f"{r['Driver'].get('givenName', '')} {r['Driver'].get('familyName', '')}"
        rows.append({"name": name, "last": r["Driver"].get("familyName", ""), "pos": pos,
                     "pts": pts, "team": team, "classified": classified, "did": did})
        by_team[team].append(len(rows) - 1)
    out = {}
    for team, idxs in by_team.items():
        cars = [rows[i] for i in idxs]
        if len(cars) == 2:
            a, b = sorted(cars, key=lambda x: x["pos"])
            a["pts"] += dk_scoring.F1["defeated_teammate"]
            cp = dk_scoring.f1_finish(a["pos"]) + dk_scoring.f1_finish(b["pos"])
            cp += dk_scoring.F1["lap_led"] * (led.get(a["did"], 0) + led.get(b["did"], 0))
            if a["classified"] and b["classified"]:
                cp += 2.0
                if a["pos"] <= 10 and b["pos"] <= 10:
                    cp += 5.0
                if a["pos"] <= 3 and b["pos"] <= 3:
                    cp += 3.0
            out[f"cnstr:{_norm(team)}"] = round(cp, 2)
    last = defaultdict(list)
    for r in rows:
        out[_norm(r["name"])] = round(r["pts"], 2)
        last[_norm(r["last"])].append(round(r["pts"], 2))
    for k, v in last.items():
        if len(v) == 1 and f"last:{k}" not in out:
            out[f"last:{k}"] = v[0]
    return out


def actuals_nascar(event_key):
    """{norm name: pts}: DK finish table + place differential + 0.25 per lap
    led. The weekend feed carries no fastest-lap count, so that component
    (0.45 each) is missing and the grade says so."""
    race_id = event_key.split(":")[1]
    year = clock.today_et().year
    feed = racing._get_json(_NASCAR.format(year=year, series=1, race_id=race_id))
    res = (feed.get("weekend_race") or [{}])[0].get("results") or []
    out = {}
    for r in res:
        try:
            fp, sp = int(r.get("finishing_position") or 0), int(r.get("starting_position") or 0)
            led = int(r.get("laps_led") or 0)
        except (TypeError, ValueError):
            continue
        if fp <= 0:
            continue
        pts = (dk_scoring.nascar_finish(fp) + dk_scoring.NASCAR["place_differential"] * ((sp or fp) - fp)
               + dk_scoring.NASCAR["lap_led"] * led)
        fl = r.get("fastest_laps")
        if fl not in (None, ""):
            try:
                pts += dk_scoring.NASCAR["fastest_lap"] * int(fl)
            except (TypeError, ValueError):
                pass
        out[_norm(r.get("driver_fullname") or r.get("driver_name") or "")] = round(pts, 2)
    out["_partial"] = "fastest laps not in the feed"
    return out or None


def _mlb_points(bat, pit):
    h = dk_scoring.MLB_HIT
    hits = int(bat.get("hits", 0) or 0)
    d2, d3, hr = (int(bat.get(k, 0) or 0) for k in ("doubles", "triples", "homeRuns"))
    b = (h["single"] * max(0, hits - d2 - d3 - hr) + h["double"] * d2 + h["triple"] * d3 + h["hr"] * hr
         + h["rbi"] * int(bat.get("rbi", 0) or 0) + h["run"] * int(bat.get("runs", 0) or 0)
         + h["bb"] * int(bat.get("baseOnBalls", 0) or 0) + h["hbp"] * int(bat.get("hitByPitch", 0) or 0)
         + h["sb"] * int(bat.get("stolenBases", 0) or 0))
    p = dk_scoring.MLB_PIT
    outs = int(pit.get("outs", 0) or 0)
    if not outs and pit.get("inningsPitched"):
        try:
            ip = float(pit["inningsPitched"])
            outs = int(ip) * 3 + int(round((ip - int(ip)) * 10))
        except (TypeError, ValueError):
            outs = 0
    cg = int(pit.get("completeGames", 0) or 0)
    ph = int(pit.get("hits", 0) or 0)
    pp = (p["out"] * outs + p["k"] * int(pit.get("strikeOuts", 0) or 0) + p["win"] * int(pit.get("wins", 0) or 0)
          + p["er"] * int(pit.get("earnedRuns", 0) or 0) + p["hit"] * ph
          + p["bb"] * int(pit.get("baseOnBalls", 0) or 0) + p["hbp"] * int(pit.get("hitBatsmen", 0) or 0)
          + p["cg"] * cg + p["cg_shutout"] * int(pit.get("shutouts", 0) or 0)
          + (p["no_hitter"] if cg and ph == 0 else 0))
    return round(b, 2), round(pp, 2)


def actuals_mlb(event_key):
    """{norm name: {'bat': pts, 'pit': pts}} for every player in every final
    game on the date, from MLB's box scores."""
    import baseball
    import kalshi
    import mlb_recorder
    date = event_key.split(":")[1]
    pks = mlb_recorder._final_pks(date)
    if not pks:
        return None
    out = {}
    for pk in sorted(pks):
        try:
            box = kalshi._get_json(f"{baseball.STATS_BASE}/game/{pk}/boxscore")
        except Exception as e:
            errlog.note("DFSLB-mlb-box", e, path=str(pk))
            continue
        for side in ("home", "away"):
            for pl in ((box.get("teams") or {}).get(side, {}).get("players") or {}).values():
                nm = _norm((pl.get("person") or {}).get("fullName"))
                st = pl.get("stats") or {}
                bat, pit = st.get("batting") or {}, st.get("pitching") or {}
                if not nm or not (bat or pit):
                    continue
                b, p = _mlb_points(bat, pit)
                out[nm] = {"bat": b, "pit": p}
    return out or None


def _nfl_points(pos, s):
    o, d = dk_scoring.NFL_OFF, dk_scoring.NFL_DST
    g = lambda k: float(s.get(k, 0) or 0)   # noqa: E731
    if pos == "DEF":
        pa = g("pts_allow")
        return round(d["sack"] * g("sack") + d["int"] * g("int") + d["fumble_rec"] * g("fum_rec")
                     + d["int_td"] * (g("def_td") + g("def_st_td") + g("st_td"))
                     + d["safety"] * g("safe") + d["blocked_kick"] * g("blk_kick")
                     + d["two_point_return"] * g("def_2pt")
                     + dk_scoring.nfl_dst_pa_points(int(pa)), 2)
    pts = (o["pass_yd"] * g("pass_yd") + o["pass_td"] * g("pass_td") + o["int"] * g("pass_int")
           + (o["pass_300"] if g("pass_yd") >= 300 else 0)
           + o["rush_yd"] * g("rush_yd") + o["rush_td"] * g("rush_td")
           + (o["rush_100"] if g("rush_yd") >= 100 else 0)
           + o["rec"] * g("rec") + o["rec_yd"] * g("rec_yd") + o["rec_td"] * g("rec_td")
           + (o["rec_100"] if g("rec_yd") >= 100 else 0)
           + o["return_td"] * (g("st_td") + g("pr_td") + g("kr_td"))
           + o["fumble_lost"] * g("fum_lost")
           + o["two_point"] * (g("pass_2pt") + g("rush_2pt") + g("rec_2pt"))
           + o["fum_rec_td"] * g("fum_rec_td"))
    return round(pts, 2)


def actuals_nfl(event_key):
    """{norm name: pts} from Sleeper's weekly stats (offense) and its DEF rows
    (keyed by nickname and team code)."""
    _, season, wk = event_key.split(":")[:3]
    week = int(wk.lstrip("w"))
    url = _SLEEPER_STATS.format(season=season, week=week)
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        url += f"&position[]={pos}"
    rows = racing._get_json(url + "&order_by=pts_ppr")
    if not rows:
        return None
    out = {}
    for r in rows:
        p = r.get("player") or {}
        st = r.get("stats") or {}
        pos = p.get("position")
        if pos == "DEF":
            pts = _nfl_points("DEF", st)
            out[f"dst:{_norm(p.get('last_name'))}"] = pts
            out[f"dst:{(r.get('team') or '').lower()}"] = pts
            continue
        nm = _norm(f"{p.get('first_name', '')} {p.get('last_name', '')}")
        if nm:
            out[nm] = _nfl_points(pos, st)
    return out or None


def _actuals(sport, event_key):
    _ensure()
    with store._lock, store._conn() as c:
        row = c.execute("SELECT payload FROM dfs_actuals WHERE sport=? AND event_key=?",
                        (sport, event_key)).fetchone()
    if row:
        return json.loads(row["payload"])
    fn = {"f1": actuals_f1, "nascar": actuals_nascar, "mlb": actuals_mlb, "nfl": actuals_nfl}.get(sport)
    if fn is None:
        return None
    got = fn(event_key)
    if got:
        with store._lock, store._conn() as c:
            c.execute("INSERT OR REPLACE INTO dfs_actuals (sport, event_key, payload, ts) VALUES (?,?,?,?)",
                      (sport, event_key, json.dumps(got), int(time.time())))
    return got


def _lookup(sport, actuals, p):
    """A lineup row's real points, or None if the feed has no such player."""
    nm = _norm(p.get("name"))
    pos = (p.get("pos") or p.get("slot") or "").upper()
    if sport == "f1":
        if pos == "CNSTR" or nm not in actuals and f"last:{nm.split()[-1] if nm.split() else ''}" not in actuals:
            # constructor: match by the first word of the team name
            for k, v in actuals.items():
                if k.startswith("cnstr:") and nm.split() and nm.split()[0] in k:
                    return v
        if nm in actuals:
            return actuals[nm]
        return actuals.get(f"last:{nm.split()[-1]}") if nm.split() else None
    if sport == "nascar":
        return actuals.get(nm)
    if sport == "mlb":
        rec = actuals.get(nm)
        if rec is None:
            return None
        return rec["pit"] if pos.startswith(("P", "SP", "RP")) else rec["bat"]
    if sport == "nfl":
        if pos == "DST" or pos == "DEF":
            for key in (f"dst:{nm.split()[-1] if nm.split() else ''}", f"dst:{(p.get('team') or '').lower()}"):
                if key in actuals:
                    return actuals[key]
            return None
        return actuals.get(nm)
    return None


def _ready(sport, event_date, today):
    """Is the event over and its feed settled? Racing and baseball the next
    day; football two days after the slate (Monday night is in the week)."""
    if sport in ("f1", "nascar", "mlb"):
        return event_date < today
    if sport == "nfl":
        try:
            d = datetime.date.fromisoformat(event_date)
            return datetime.date.fromisoformat(today) >= d + datetime.timedelta(days=2)
        except ValueError:
            return False
    return False


def _bucket(actual, floor, median, ceil):
    if floor is None or median is None or ceil is None:
        return None
    if actual < floor:
        return "below_floor"
    if actual < median:
        return "low"
    if actual <= ceil:
        return "high"
    return "above_ceiling"


def grade_due(limit=60):
    """Grade logged lineups whose events are over. One feed fetch per event
    (cached in dfs_actuals). Returns how many lineups were graded."""
    _ensure()
    today = clock.today_et().isoformat()
    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM dfs_builds WHERE graded=0 ORDER BY ts LIMIT ?",
                         (limit,)).fetchall()
    n = 0
    for r in rows:
        sport = r["sport"]
        if sport not in GRADED_SPORTS or not _ready(sport, r["event_date"] or "", today):
            continue
        try:
            actuals = _actuals(sport, r["event_key"])
        except Exception as e:
            errlog.note("DFSLB-actuals", e, path=f"{sport} {r['event_key']}")
            continue
        if not actuals:
            continue
        players = json.loads(r["players"] or "[]")
        got, missing, total = [], 0, 0.0
        for p in players:
            v = _lookup(sport, actuals, p)
            if v is None:
                missing += 1
                got.append([p["name"], p.get("proj"), None])
                continue
            pts = v * 1.5 if p.get("captain") else v
            total += pts
            got.append([p["name"], p.get("proj"), round(pts, 2)])
        if missing > max(1, len(players) // 2):
            # the feed is up but does not know most of the lineup: leave it,
            # a later pass (or a name fix) can still grade it
            continue
        note = actuals.get("_partial") if isinstance(actuals, dict) else None
        bucket = _bucket(total, r["floor"], r["median"], r["ceiling"])
        with store._lock, store._conn() as c:
            c.execute("UPDATE dfs_builds SET graded=1, actual=?, actual_players=?, n_missing=?, "
                      "bucket=?, graded_ts=?, note=? WHERE id=?",
                      (round(total, 2), json.dumps(got), missing, bucket, int(time.time()), note, r["id"]))
        n += 1
    if n:
        _state["corr"] = {}
        _state["corr_ts"] = 0.0
    return n


# ---- the feedback: gated corrections -----------------------------------------
def _pos_group(sport, pos, slot=None):
    p = (pos or slot or "").upper()
    if sport == "f1":
        return "CNSTR" if p == "CNSTR" else "D"
    if sport == "nascar":
        return "D"
    if sport == "ufc":
        return "F"
    if sport == "mlb":
        return "pit" if p.startswith(("P", "SP", "RP")) or p == "PIT" else "bat"
    if sport == "nfl":
        p = p.split("/")[0]
        return "DST" if p in ("DST", "DEF") else (p if p in ("QB", "RB", "WR", "TE") else "FLEX")
    return p or "?"


def _graded_pairs(sport):
    """[(event_key, group, proj, actual)] over every graded lineup's players."""
    _ensure()
    with store._lock, store._conn() as c:
        rows = c.execute("SELECT event_key, event_date, players, actual_players, backtest FROM dfs_builds "
                         "WHERE graded=1 AND sport=?", (sport,)).fetchall()
    seen, pairs, ev_info = set(), [], {}
    for r in rows:
        players = {p["name"]: p for p in json.loads(r["players"] or "[]")}
        ev_info[r["event_key"]] = (r["event_date"] or "", bool(r["backtest"]))
        for name, proj, actual in json.loads(r["actual_players"] or "[]"):
            if proj is None or actual is None:
                continue
            k = (r["event_key"], name)
            if k in seen:                 # the same player across many lineups is one observation
                continue
            seen.add(k)
            p = players.get(name) or {}
            base = actual / 1.5 if p.get("captain") else actual
            pairs.append((r["event_key"], _pos_group(sport, p.get("pos"), p.get("slot")), float(proj), float(base)))
    # event weight: recency (newest = 1, half-life eight events) x backtest 0.5
    order = sorted(ev_info, key=lambda e: ev_info[e][0])
    rank_from_latest = {e: len(order) - 1 - i for i, e in enumerate(order)}
    ev_w = {e: (0.5 ** (rank_from_latest[e] / _HALF_LIFE_EVENTS)) * (_BACKTEST_W if ev_info[e][1] else 1.0)
            for e in order}
    return [(e, g, pj, ac, ev_w.get(e, 1.0)) for e, g, pj, ac in pairs]


def _fit(pairs):
    """Weighted, shrunk ratio of actual to projected points over the pairs'
    events. The shrink runs on the events' summed weight, so five half-weight
    backtests pull the ratio toward 1 as much as two and a half live ones."""
    ev = {}
    for e, _, _, _, w in pairs:
        ev[e] = w
    sp = sum(x[4] * x[2] for x in pairs)
    sa = sum(x[4] * x[3] for x in pairs)
    if sp <= 0 or not ev:
        return None
    r = sa / sp
    n_eff = sum(ev.values())
    return 1.0 + (r - 1.0) * n_eff / (n_eff + _SHRINK_K)


_bt_cache = {}


def _is_backtest_event(sport, event_key):
    key = (sport, event_key)
    if key not in _bt_cache:
        with store._lock, store._conn() as c:
            r = c.execute("SELECT MAX(backtest) b FROM dfs_builds WHERE sport=? AND event_key=?",
                          (sport, event_key)).fetchone()
        _bt_cache[key] = bool(r and r["b"])
    return _bt_cache[key]


def corrections(sport, min_events=_MIN_EVENTS):
    """{group: factor} that ships, with the evidence under `_meta`. A group
    needs `min_events` graded events, and the factor fitted WITHOUT each
    event must beat no-correction on that event's total error at least
    _GATE_SHARE of the time; otherwise nothing ships for that group."""
    now = time.time()
    cached = _state["corr"].get(sport)
    if cached is not None and now - _state["corr_ts"] < 600:
        return cached
    pairs = _graded_pairs(sport)
    by_group = defaultdict(list)
    for row in pairs:
        by_group[row[1]].append(row)
    out, meta = {}, {}
    for g, rows in by_group.items():
        events = sorted({x[0] for x in rows})
        if len(events) < min_events:
            meta[g] = {"events": len(events), "status": f"needs {min_events} graded events"}
            continue
        wins = 0
        for e in events:
            train = [x for x in rows if x[0] != e]
            f = _fit(train) or 1.0
            tp = sum(x[2] for x in rows if x[0] == e)
            ta = sum(x[3] for x in rows if x[0] == e)
            if abs(ta - f * tp) < abs(ta - tp):
                wins += 1
        f_all = _fit(rows) or 1.0
        share = wins / len(events)
        raw = (sum(x[3] for x in rows) / max(1e-9, sum(x[2] for x in rows)))
        n_bt = sum(1 for e in events if e.startswith("bt:") or _is_backtest_event(sport, e))
        meta[g] = {"events": len(events), "backtest_events": n_bt, "players": len(rows),
                   "raw_ratio": round(raw, 3), "factor": round(f_all, 3), "loo_share": round(share, 2),
                   "status": "in force" if share >= _GATE_SHARE and abs(f_all - 1) >= 0.01 else "gated out"}
        if share >= _GATE_SHARE and abs(f_all - 1) >= 0.01:
            out[g] = round(f_all, 3)
    out["_meta"] = meta
    _state["corr"][sport] = out
    _state["corr_ts"] = now
    return out


def adjust(sport, players):
    """Scale each player's projection (and its ceiling / floor / samples) by
    the group's correction in force. Marks the row so the card can say so.
    Returns how many rows changed."""
    try:
        fac = corrections(sport)
    except Exception as e:
        errlog.note("DFSLB-corr", e, path=sport)
        return 0
    n = 0
    for p in players:
        g = _pos_group(sport, p.get("roster_pos") or p.get("pos") or p.get("kind"), p.get("slot"))
        f = fac.get(g)
        if not f or f == 1.0:
            continue
        for k in ("proj", "ceil_proj", "ceiling", "floor", "ceil", "model_proj"):
            if p.get(k) is not None:
                try:
                    p[k] = p[k] * f
                except TypeError:
                    pass
        if p.get("arr"):
            try:
                p["arr"] = [x * f for x in p["arr"]]
            except TypeError:
                pass
        p["lookback_factor"] = f
        n += 1
    return n


# ---- seeding from a backtest -------------------------------------------------
def seed_rows(rows, source="backtest"):
    """Insert backtested events as graded, flagged rows. `rows` is
    [{sport, event_key, event_date, players: [{name, pos, proj, actual}]}]
    (dfs_backtest writes them). Idempotent per (sport, event). Returns how
    many events were inserted."""
    _ensure()
    n = 0
    with store._lock, store._conn() as c:
        for ev in rows:
            pls = [p for p in (ev.get("players") or []) if p.get("proj") is not None and p.get("actual") is not None]
            if len(pls) < 3:
                continue
            key = hashlib.sha1(f"{source}|{ev['sport']}|{ev['event_key']}".encode()).hexdigest()[:20]
            players = [{"name": p["name"], "pos": p.get("pos"), "proj": p["proj"], "captain": False} for p in pls]
            actual_players = [[p["name"], p["proj"], p["actual"]] for p in pls]
            tp = round(sum(p["proj"] for p in pls), 2)
            ta = round(sum(p["actual"] for p in pls), 2)
            cur = c.execute(
                "INSERT OR IGNORE INTO dfs_builds (ts, sport, event_key, event_date, objective, mode, lineup_ix, "
                "players, total_proj, big, big_reason, key, graded, actual, actual_players, n_missing, graded_ts, backtest, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,1,?,?,1,?,?,0,?,1,?)",
                (int(time.time()), ev["sport"], ev["event_key"], ev.get("event_date"), "backtest", "",
                 0, json.dumps(players), tp, f"backtest ({source})", key, ta, json.dumps(actual_players),
                 int(time.time()), ev.get("note")))
            n += cur.rowcount or 0
    if n:
        _state["corr"] = {}
        _bt_cache.clear()
    return n


def ingest_seed(path=None):
    """Load the committed backtest seed once (idempotent by event)."""
    import os
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), SEED_PATH)
    if not os.path.exists(path):
        return 0
    try:
        with open(path) as fh:
            d = json.load(fh)
    except Exception as e:
        errlog.note("DFSLB-seed-read", e, path=path)
        return 0
    return seed_rows(d.get("rows") or [], source=d.get("source") or "seed")


# ---- the report -------------------------------------------------------------
def report():
    _ensure()
    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM dfs_builds ORDER BY ts").fetchall()
    per = {}
    pending = defaultdict(lambda: {"lineups": 0, "events": set()})
    seeded = defaultdict(lambda: {"backtest": set(), "live": set(), "players": 0})
    for r in rows:
        sp, obj = r["sport"], r["objective"]
        if not r["graded"]:
            pending[sp]["lineups"] += 1
            pending[sp]["events"].add(r["event_key"])
            continue
        if r["backtest"]:
            seeded[sp]["backtest"].add(r["event_key"])
            seeded[sp]["players"] += len(json.loads(r["actual_players"] or "[]"))
            continue                      # the objective table is live lineups only
        seeded[sp]["live"].add(r["event_key"])
        d = per.setdefault((sp, obj), {"sport": sp, "objective": obj, "events": set(), "lineups": 0,
                                       "proj": 0.0, "actual": 0.0, "beat": 0, "buckets": defaultdict(int),
                                       "abs_err": 0.0, "n_players": 0, "partial": 0})
        d["events"].add(r["event_key"])
        d["lineups"] += 1
        d["proj"] += float(r["total_proj"] or 0)
        d["actual"] += float(r["actual"] or 0)
        d["beat"] += 1 if (r["actual"] or 0) >= (r["total_proj"] or 0) else 0
        if r["bucket"]:
            d["buckets"][r["bucket"]] += 1
        if r["note"]:
            d["partial"] += 1
        for name, pj, ac in json.loads(r["actual_players"] or "[]"):
            if pj is not None and ac is not None:
                d["abs_err"] += abs(ac - pj)
                d["n_players"] += 1
    out = []
    for (sp, obj), d in sorted(per.items()):
        n = d["lineups"]
        b = d["buckets"]
        out.append({"sport": sp, "objective": obj, "events": len(d["events"]), "lineups": n,
                    "mean_proj": round(d["proj"] / n, 1), "mean_actual": round(d["actual"] / n, 1),
                    "bias_pct": round(100 * (d["actual"] / max(1e-9, d["proj"]) - 1), 1),
                    "beat_proj_pct": round(100 * d["beat"] / n, 0),
                    "player_mae": round(d["abs_err"] / d["n_players"], 1) if d["n_players"] else None,
                    "below_floor_pct": round(100 * b["below_floor"] / n, 0),
                    "in_band_pct": round(100 * (b["low"] + b["high"]) / n, 0),
                    "above_ceiling_pct": round(100 * b["above_ceiling"] / n, 0),
                    "partial": d["partial"]})
    best = {}
    for row in out:
        if row["events"] >= 2:
            cur = best.get(row["sport"])
            if cur is None or row["mean_actual"] > cur["mean_actual"]:
                best[row["sport"]] = row
    corr = {}
    for sp in GRADED_SPORTS:
        try:
            corr[sp] = corrections(sp)
        except Exception as e:
            errlog.note("DFSLB-report-corr", e, path=sp)
    # the backtest's own scoreboard: projection bias per sport over its events
    bt_rows = []
    for sp, v in seeded.items():
        if not v["backtest"]:
            continue
        pairs = [x for x in _graded_pairs(sp) if _is_backtest_event(sp, x[0])]
        by_g = defaultdict(list)
        for x in pairs:
            by_g[x[1]].append(x)
        for g, xs in sorted(by_g.items()):
            spj, sac = sum(x[2] for x in xs), sum(x[3] for x in xs)
            mae = sum(abs(x[3] - x[2]) for x in xs) / len(xs)
            bt_rows.append({"sport": sp, "group": g, "events": len({x[0] for x in xs}), "players": len(xs),
                            "mean_proj": round(spj / len(xs), 1), "mean_actual": round(sac / len(xs), 1),
                            "bias_pct": round(100 * (sac / max(1e-9, spj) - 1), 1), "player_mae": round(mae, 1)})
    return {"rows": out, "backtest": bt_rows,
            "seeded": {sp: {"backtest_events": len(v["backtest"]), "live_events": len(v["live"]),
                            "backtest_players": v["players"]} for sp, v in seeded.items()},
            "weights": {"backtest": _BACKTEST_W, "half_life_events": _HALF_LIFE_EVENTS},
            "best_objective": {sp: r["objective"] for sp, r in best.items()},
            "pending": {sp: {"lineups": v["lineups"], "events": len(v["events"])} for sp, v in pending.items()},
            "not_graded": NOT_GRADED, "corrections": corr,
            "gate": {"min_events": _MIN_EVENTS, "loo_share": _GATE_SHARE, "shrink_events": _SHRINK_K},
            "n_logged": len(rows)}


def tick():
    """Recorder-cadence pass: grade what is due, at most every 30 minutes; the
    committed backtest seed is loaded once per process (idempotent)."""
    now = time.time()
    if now - _state["last_grade"] < _GRADE_EVERY_S:
        return 0
    _state["last_grade"] = now
    if not _state.get("seeded"):
        _state["seeded"] = True
        try:
            ingest_seed()
        except Exception as e:
            errlog.note("DFSLB-seed", e)
    return grade_due()
