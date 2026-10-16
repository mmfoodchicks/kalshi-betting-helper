"""NBA deep engine team profiles: each team's units measured from its games'
possessions (nba_poss over nba_pbp records) against the league, shrunk toward
a prior, then turned into the engine's levers. The basketball twin of
nfl_deep_data / nhl_deep_data.

A unit is a ratio to the league (nba_deep_sim.PROFILE_KEYS):

  tov_off / tov_def       turnovers per shot opportunity, committed / forced
  fts_off / fts_def       free-throw trips without an attempt per opportunity
  three_off / three_def   share of attempts from three, taken / allowed
  rim_off / rim_def       share at the rim
  mk_rim / mk_two / mk_three     field goal % by zone (two = 4 ft to the arc)
  dmk_rim / dmk_two / dmk_three  the same, allowed
  oreb_off / oreb_def     offensive rebounds per miss, grabbed / allowed
  ft                      free-throw %
  pace                    seconds a possession (both ends)

LEVERS. The engine's pull back toward an even game takes back part of any
edge it is given -- home court as a lever of 1.0072 realized 1.0085 of the
league's 1.0144 points-a-possession ratio (nba_deep_sim._HFA_MAKE) -- so a
team's MEASURED units, which already include that pull (its season was played
with it), are raised to LEVER_EXP before the engine sees them. The exponent
is the engine's own elasticity, measured on league-average sides
(research/nba_deep_fit.py measure_elasticity).
"""
import datetime as _dt
import json as _json
import math
import os as _os
import time as _time
import zlib as _zlib

import errlog
import nba_pbp as P
import nba_poss as Q

UNIT_KEYS = ("tov_off", "tov_def", "fts_off", "fts_def", "three_off", "three_def", "rim_off", "rim_def",
             "mk_rim", "mk_two", "mk_three", "dmk_rim", "dmk_two", "dmk_three", "oreb_off", "oreb_def",
             "ft", "pace")
# Shrinkage K (games) by unit and the prior's persistence: round values set
# BEFORE any measurement, the ones the validation runs with.
K_GAMES = {"tov_off": 15.0, "tov_def": 25.0, "fts_off": 20.0, "fts_def": 25.0, "three_off": 10.0,
           "three_def": 20.0, "rim_off": 10.0, "rim_def": 15.0, "mk_rim": 30.0, "mk_two": 40.0,
           "mk_three": 60.0, "dmk_rim": 40.0, "dmk_two": 50.0, "dmk_three": 80.0, "oreb_off": 15.0,
           "oreb_def": 20.0, "ft": 30.0, "pace": 10.0}
PERSIST = {k: 0.6 for k in UNIT_KEYS}
# The engine's elasticity to its levers, measured on 2023-24 (a TRAINING season
# for both validation seasons; research/nba_deep_fit.py measure_elasticity):
# each team's full-season units at exponent e against a league-average side,
# neutral, 300 games, the slope of the thirty teams' real mean margins on the
# engine's --
#
#   e       1.00   1.10   1.15   1.20   1.25   1.33   1.50   1.67   2.00
#   slope   1.162  1.029  1.004  0.971  0.915  0.908  0.802  0.700  0.596
#
# 1.15 is where the slope is 1 (a team's real margin is the engine's on
# average); the engine's spread of team margins there is 5.34 points against
# the real 5.50, correlation 0.974. The first measurement let ESPN's All-Star
# games in (filed as regular season, nba_pbp.FRANCHISES): two one-game
# "teams" with no defence dragged the correlation to 0.85 and read 1.5.
LEVER_EXP = 1.15
# Units whose effect on scoring the pull back attenuates (efficiency and
# volume); pace is a clock, not an edge, and is passed straight through.
_LEVERED = tuple(k for k in UNIT_KEYS if k != "pace")


def _blank():
    return {"games": 0, "opp_f": 0, "opp_a": 0, "tov_f": 0, "tov_a": 0, "fts_f": 0, "fts_a": 0,
            "fga_f": 0, "fga_a": 0, "three_f": 0, "three_a": 0, "rim_f": 0, "rim_a": 0,
            "m": [[0, 0] for _ in range(4)], "dm": [[0, 0] for _ in range(4)],
            "miss_f": 0, "oreb_f": 0, "miss_a": 0, "oreb_a": 0, "ftm": 0, "fta": 0,
            "secs": 0.0, "poss": 0}


def _zone_row(z):
    return 0 if z == 0 else (3 if z == 3 else 1)


def game_units(rec):
    """{side: raw counts} for one game from its possessions."""
    U = {0: _blank(), 1: _blank()}
    for s in (0, 1):
        U[s]["games"] = 1
    for x in Q.possessions(rec):
        o, d = U[x["side"]], U[1 - x["side"]]
        if x["per"] <= 4 and x["end"] != "period":
            o["secs"] += x["start_clk"] - x["end_clk"]
            d["secs"] += x["start_clk"] - x["end_clk"]
            o["poss"] += 1
            d["poss"] += 1
        seq = x["seq"]
        want = True
        for j, ev in enumerate(seq):
            k = ev[0]
            nxt = seq[j + 1] if j + 1 < len(seq) else None
            if k == "oreb":
                want = True
                continue
            if k == "fts":
                if not want or ev[4] or ev[5]:
                    continue
                o["opp_f"] += 1
                d["opp_a"] += 1
                o["fts_f"] += 1
                d["fts_a"] += 1
                want = False
                continue
            if not want:
                continue
            o["opp_f"] += 1
            d["opp_a"] += 1
            want = False
            if k == "tov":
                o["tov_f"] += 1
                d["tov_a"] += 1
            elif k == "fga":
                _, z, made, blk, and1 = ev
                o["fga_f"] += 1
                d["fga_a"] += 1
                if z == 3:
                    o["three_f"] += 1
                    d["three_a"] += 1
                if z == 0:
                    o["rim_f"] += 1
                    d["rim_a"] += 1
                r = _zone_row(z)
                o["m"][r][0] += made
                o["m"][r][1] += 1
                d["dm"][r][0] += made
                d["dm"][r][1] += 1
                if not made:
                    reb = 1 if (nxt and nxt[0] == "oreb") else 0
                    o["miss_f"] += 1
                    o["oreb_f"] += reb
                    d["miss_a"] += 1
                    d["oreb_a"] += reb
        for made, _o in x["fts"]:
            o["fta"] += 1
            o["ftm"] += made
    return U


def add(acc, u):
    for k, v in u.items():
        if k in ("m", "dm"):
            row = acc.setdefault(k, [[0, 0] for _ in range(4)])
            for i in range(4):
                row[i][0] += v[i][0]
                row[i][1] += v[i][1]
        else:
            acc[k] = acc.get(k, 0) + v
    return acc


def league(acc_all):
    """League totals from every team's accumulated counts (each game counted
    from both sides, so the ratios are right)."""
    tot = {}
    for u in acc_all.values():
        add(tot, u)
    return tot


def _r(a, b):
    return a / b if b else None


def raw_profile(u, lg):
    """A team's UNSHRUNK units: ratios of its rates to the league's."""
    def ratio(x, y):
        return (x / y) if (x is not None and y) else None
    lr = {
        "tov": _r(lg["tov_f"], lg["opp_f"]), "fts": _r(lg["fts_f"], lg["opp_f"]),
        "three": _r(lg["three_f"], lg["fga_f"]), "rim": _r(lg["rim_f"], lg["fga_f"]),
        "m": [_r(lg["m"][i][0], lg["m"][i][1]) for i in range(4)],
        "oreb": _r(lg["oreb_f"], lg["miss_f"]), "ft": _r(lg["ftm"], lg["fta"]),
        "pace": _r(lg["secs"], lg["poss"])}
    return {
        "tov_off": ratio(_r(u["tov_f"], u["opp_f"]), lr["tov"]), "tov_def": ratio(_r(u["tov_a"], u["opp_a"]), lr["tov"]),
        "fts_off": ratio(_r(u["fts_f"], u["opp_f"]), lr["fts"]), "fts_def": ratio(_r(u["fts_a"], u["opp_a"]), lr["fts"]),
        "three_off": ratio(_r(u["three_f"], u["fga_f"]), lr["three"]),
        "three_def": ratio(_r(u["three_a"], u["fga_a"]), lr["three"]),
        "rim_off": ratio(_r(u["rim_f"], u["fga_f"]), lr["rim"]), "rim_def": ratio(_r(u["rim_a"], u["fga_a"]), lr["rim"]),
        "mk_rim": ratio(_r(*u["m"][0]), lr["m"][0]), "mk_two": ratio(_r(*u["m"][1]), lr["m"][1]),
        "mk_three": ratio(_r(*u["m"][3]), lr["m"][3]),
        "dmk_rim": ratio(_r(*u["dm"][0]), lr["m"][0]), "dmk_two": ratio(_r(*u["dm"][1]), lr["m"][1]),
        "dmk_three": ratio(_r(*u["dm"][3]), lr["m"][3]),
        "oreb_off": ratio(_r(u["oreb_f"], u["miss_f"]), lr["oreb"]),
        "oreb_def": ratio(_r(u["oreb_a"], u["miss_a"]), lr["oreb"]),
        "ft": ratio(_r(u["ftm"], u["fta"]), lr["ft"]), "pace": ratio(_r(u["secs"], u["poss"]), lr["pace"])}


def shrink(raw, n, prior=None, K=None, persist=None):
    K = K or K_GAMES
    persist = persist or PERSIST
    out = {}
    for k in UNIT_KEYS:
        pr = persist[k] * math.log(max(1e-3, prior[k])) if (prior and prior.get(k)) else 0.0
        v = raw.get(k)
        if v is None or v <= 0 or n <= 0:
            out[k] = math.exp(pr)
            continue
        w = n / (n + K[k])
        out[k] = math.exp(w * math.log(v) + (1.0 - w) * pr)
    return out


def levers(units, exp=None):
    """The engine's levers from measured (realized) units."""
    e = LEVER_EXP if exp is None else exp
    return {k: (v ** e if k in _LEVERED else v) for k, v in units.items()}


def profiles(acc, priors=None, lg=None, K=None, persist=None, exp=None):
    """{abbr: engine profile} from accumulated counts and optional priors
    (priors are MEASURED units -- last season's shrunk ones -- not levers)."""
    lg = lg or league(acc)
    out, units = {}, {}
    for ab, u in acc.items():
        un = shrink(raw_profile(u, lg), u.get("games", 0), (priors or {}).get(ab), K, persist)
        units[ab] = un
        prof = levers(un, exp)
        prof.update(abbr=ab, level_off=1.0, games=u.get("games", 0))
        out[ab] = prof
    for ab, pr in (priors or {}).items():
        if ab not in out:
            un = shrink({}, 0, pr, K, persist)
            units[ab] = un
            prof = levers(un, exp)
            prof.update(abbr=ab, level_off=1.0, games=0)
            out[ab] = prof
    return out, units


# ---- production: the season's units, kept current by the nightly job ---------
STATE_KEY = "nba_deep"
DAY_KEY = "nba_deep_day"
PRIORS_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "research", "data", "nba_deep_priors.json")
# Games a matchup for the board's deep figure (the validation priced at 300);
# ~1 ms a game, so a 15-game night is ~5 s, paid once a date in a niced child.
DEEP_N = 300
_SEED = 20261016
_DAYS_KEPT = 7


def season_of(date_iso):
    """The NBA season (by its start year) a date belongs to."""
    y, m = int(date_iso[:4]), int(date_iso[5:7])
    return y if m >= 8 else y - 1


def priors():
    """Last season's final MEASURED units, committed (the prior for this one)."""
    try:
        with open(PRIORS_PATH) as fh:
            return (_json.load(fh) or {}).get("units") or {}
    except (OSError, ValueError) as e:
        errlog.note("NBADEEP-priors", e)
        return {}


def update(season, today=None, max_fetch=200, log=None):
    """The nightly refresh: the scoreboard for the days since the last run (the
    whole season on a first run), each newly finished regular-season game
    fetched ONCE, checked against its final score, cut into units and added to
    the running counts. The plays are dropped. Returns the state."""
    import deep_cache
    state, _ts = deep_cache.load(STATE_KEY)
    if not isinstance(state, dict) or state.get("season") != int(season):
        state = {"season": int(season), "acc": {}, "seen": [], "events": [], "mismatch": [], "through": None}
    today = today or _dt.date.today()
    start = _dt.date(int(season), 10, 1)
    if state.get("through"):
        start = max(start, _dt.date.fromisoformat(state["through"]) - _dt.timedelta(days=3))
    events = {e["id"]: e for e in state.get("events") or []}
    day = start
    while day <= today:
        try:
            for ev in P._day_events(day.strftime("%Y%m%d")):
                if ev["espn_year"] == int(season) + 1 and ev["st"] in (2, 3, 5):
                    events[ev["id"]] = ev
        except Exception as e:
            errlog.note("NBADEEP-day", e, path=day.isoformat())
        day += _dt.timedelta(days=1)
    seen = set(state.get("seen") or [])
    mismatch = list(state.get("mismatch") or [])
    acc = state.get("acc") or {}
    fetched = 0
    for g in sorted(events.values(), key=lambda g: (g.get("date") or "", g["id"])):
        if not P.league_game(g) or g.get("state") != "post" or g["id"] in seen or g["id"] in mismatch:
            continue
        if fetched >= max_fetch:
            break
        try:
            rec = P.fetch_game(g["id"])
        except Exception as e:
            errlog.note("NBADEEP-game", e, path=str(g["id"]))
            continue
        fetched += 1
        if not rec or P.points_check(rec) != (rec["home_score"], rec["away_score"]):
            mismatch.append(g["id"])
            continue
        U = game_units(rec)
        for s_, ab in ((0, rec["home"]), (1, rec["away"])):
            add(acc.setdefault(ab, {}), U[s_])
        seen.add(g["id"])
    profs, units = profiles(acc, priors(), lg=_league_or_none(acc))
    state.update(events=sorted(events.values(), key=lambda g: (g.get("date") or "", g["id"])), seen=sorted(seen),
                 acc=acc, mismatch=mismatch, through=today.isoformat(), updated=_time.time(),
                 profiles=profs, units=units)
    deep_cache.save(STATE_KEY, state)
    return state


def _league_or_none(acc):
    """This season's league once it has 60 games; before that the priors'
    league is implicit (units are ratios, so None lets profiles() measure
    against what there is)."""
    n = sum(u.get("games", 0) for u in acc.values()) // 2
    return league(acc) if n >= 60 else (_prior_league() or (league(acc) if acc else None))


def _prior_league():
    try:
        with open(PRIORS_PATH) as fh:
            return (_json.load(fh) or {}).get("league")
    except (OSError, ValueError):
        return None


def current_profiles(season):
    try:
        import deep_cache
        state, _ts = deep_cache.load(STATE_KEY)
        if isinstance(state, dict) and state.get("season") == int(season) and state.get("profiles"):
            return state["profiles"]
    except Exception as e:
        errlog.note("NBADEEP-load", e)
    pr = priors()
    if not pr:
        return None
    profs, _u = profiles({}, pr)
    return profs


def game_figure(hp, ap, date, home, away, n=DEEP_N, T=None):
    import random
    import nba_deep_sim as E
    T = T or E.tables()
    rng = random.Random(_zlib.crc32(f"{_SEED}|{date}|{home}|{away}".encode()))
    hw = ot = 0
    ph = pa = 0
    for _ in range(n):
        g = E.play_game(hp, ap, rng, T)
        hw += g["winner"] == 0
        ot += 1 if g["ot"] else 0
        ph += g["score"][0]
        pa += g["score"][1]
    return {"p_home": round(hw / n, 4), "p_away": round(1 - hw / n, 4), "p_ot": round(ot / n, 4),
            "exp_home": round(ph / n, 1), "exp_away": round(pa / n, 1), "exp_total": round((ph + pa) / n, 1), "n": n}


def _figures(date, matchups, profs):
    import nba_deep_sim as E
    T = E.tables()
    return {f"{h}|{a}": game_figure(profs[h], profs[a], date, h, a, T=T)
            for h, a in matchups if h in profs and a in profs}


def _figures_blob(raw):
    import pickle
    job = pickle.loads(raw)
    return pickle.dumps(_figures(job["date"], job["matchups"], job["profiles"]), protocol=pickle.HIGHEST_PROTOCOL)


def _days():
    import deep_cache
    try:
        cached, _ts = deep_cache.load(DAY_KEY)
        if isinstance(cached, dict) and isinstance(cached.get("days"), dict):
            return cached["days"]
    except Exception as e:
        errlog.note("NBADEEP-day-load", e)
    return {}


def cached_day_figures(date):
    return _days().get(date) or {}


def day_figures(date, matchups):
    """{"HOME|AWAY": figure} for a date's games: once a date in a niced child,
    kept in the deep store for every worker. {} when no profiles exist."""
    import deep_cache
    have = _days().get(date) or {}
    if matchups and all(f"{h}|{a}" in have for h, a in matchups):
        return have
    profs = current_profiles(season_of(date))
    if not profs:
        return {}
    figs = None
    try:
        import pickle
        import subprocess
        import sys
        out = subprocess.run(
            [sys.executable, "-c", "import os; os.nice(10)\n"
             "import sys, nba_deep_data; sys.stdout.buffer.write(nba_deep_data._figures_blob(sys.stdin.buffer.read()))"],
            input=pickle.dumps({"date": date, "matchups": list(matchups), "profiles": profs}),
            cwd=_os.path.dirname(_os.path.abspath(__file__)), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=180)
        if out.returncode == 0 and out.stdout:
            figs = pickle.loads(out.stdout)
        else:
            errlog.note("NBADEEP-child", msg=f"child rc={out.returncode}; no deep figures this build")
    except Exception as e:
        errlog.note("NBADEEP-child", e)
    if figs is None:
        # NOT computed in-process: this runs on the board's build thread inside
        # a gunicorn worker on a one-core quota, the exact placement that took
        # the instance down twice on 2026-09-05. The card shows production
        # alone until the next build's child succeeds.
        return {}
    try:
        days = _days()
        days[date] = figs
        for d in sorted(days)[:-_DAYS_KEPT]:
            days.pop(d, None)
        deep_cache.save(DAY_KEY, {"days": days})
    except Exception as e:
        errlog.note("NBADEEP-day-save", e)
    return figs
