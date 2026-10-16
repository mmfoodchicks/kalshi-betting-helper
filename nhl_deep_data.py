"""NHL deep engine team profiles: each team's units, measured from its games
(nhl_pbp records) against the engine's own league tables, shrunk toward a
prior by the measured reliability of each stat. The hockey twin of
nfl_deep_data.

A unit is a multiplier on the league (nhl_deep_sim.PROFILE_KEYS):

  att_off / att_def   five-on-five shot attempts for / against per 60
  xg_off / xg_def     expected goals per unblocked five-on-five attempt for /
                      against (the engine's own (state, distance, rebound)
                      cells), i.e. shot quality
  fin                 goals for over expected, every guarded-net state
  gk                  goals against over expected, every guarded-net state
                      (goaltending and the skaters in front of it)
  pp / pk             power-play goals for per 60 of advantage / against per
                      60 shorthanded, as RESIDUALS over what the team's
                      even-strength units already imply
  pen_take / pen_draw man-advantage incidents taken / drawn per 60

Each is n / (n + K) of the team's own figure plus K / (n + K) of its prior,
n the team's games, K per stat measured by split-half reliability over full
seasons (research/nhl_deep_fit.py measure_k). The prior is last season's
profile pulled toward 1 by the measured year-over-year persistence, or 1.
"""
import json
import math
import os
import time
import zlib

import errlog
import nhl_pbp as P
import nhl_deep_sim as E

UNIT_KEYS = E.PROFILE_KEYS
# Shrinkage K (games) and year-over-year persistence by unit: the values the
# out-of-sample validation RAN WITH (research/reports/nhl_deep_validation.md)
# -- round judgments set before anything was measured. The measured ones
# (research/nhl_deep_fit.py measure_k / measure_persistence, 2022-23 through
# 2025-26, split-half reliability across teams) differ, some a lot:
#
#   unit       K here   K measured   persistence measured (3 season pairs)
#   att_off    15       3.9          0.79 0.85 0.73
#   att_def    15       11.2         0.81 0.83 0.76
#   xg_off     30       25.0         0.79 0.78 0.66
#   xg_def     30       28.1         0.72 0.72 0.56
#   fin        120      539          (unmeasurable: reliability ~0)
#   gk         80       160          0.62 0.63 0.00
#   pp         80       82           0.56 0.20 0.74
#   pk         80       156          0.40 1.00 0.34
#   pen_take   40       30           0.98 0.85 0.94
#   pen_draw   40       48           0.78 0.61 0.86
#
# Swapping them in is a MODEL CHANGE, to be judged on 2026-27 games the
# validation never read -- not shipped on the seasons that measured them.
K_GAMES = {"att_off": 15.0, "att_def": 15.0, "xg_off": 30.0, "xg_def": 30.0, "fin": 120.0, "gk": 80.0,
           "pp": 80.0, "pk": 80.0, "pen_take": 40.0, "pen_draw": 40.0}
PERSIST = {k: 0.5 for k in UNIT_KEYS}


def _blank():
    return {"games": 0, "ev_secs": 0.0, "ev_att_f": 0, "ev_att_a": 0, "ev_unbl_f": 0, "ev_unbl_a": 0,
            "ev_xg_f": 0.0, "ev_xg_a": 0.0, "gi_g_f": 0, "gi_g_a": 0, "gi_xg_f": 0.0, "gi_xg_a": 0.0,
            "pp_secs": 0.0, "pp_g": 0, "pk_secs": 0.0, "pk_g": 0, "pp_xg_ev": 0.0, "pk_xg_ev": 0.0,
            "secs": 0.0, "pen_take": 0, "pen_draw": 0}


def game_units(rec, T, fitmod=None):
    """{side: raw unit counts} for one game, through nhl_pbp.walk -- the
    fit's own cut of a game, so the units are measured the way the tables
    they are compared against were. (`fitmod` is accepted for the research
    harness and ignored: the walk is one definition.)"""
    U = {0: _blank(), 1: _blank()}
    for s in (0, 1):
        U[s]["games"] = 1
    for kind, x in P.walk(rec):
        if kind == "time":
            hst, sd, late, per, dt = x
            if per >= 4:
                continue
            for s, st in ((0, hst), (1, (hst[1], hst[0], hst[3], hst[2]))):
                U[s]["secs"] += dt
                if st == (5, 5, 1, 1):
                    U[s]["ev_secs"] += dt
                elif st[2] and st[3] and st[0] > st[1]:
                    U[s]["pp_secs"] += dt
                elif st[2] and st[3] and st[0] < st[1]:
                    U[s]["pk_secs"] += dt
        elif kind == "att":
            side, st, sd, late, per, code, db, reb, flags = x
            if per >= 4:
                continue
            o = 1 - side
            if st == (5, 5, 1, 1):
                U[side]["ev_att_f"] += 1
                U[o]["ev_att_a"] += 1
            if code == P.BLOCK or not st[3]:
                continue
            p = T.p_goal(st, db, reb)
            g = 1 if code == P.GOAL else 0
            U[side]["gi_g_f"] += g
            U[o]["gi_g_a"] += g
            U[side]["gi_xg_f"] += p
            U[o]["gi_xg_a"] += p
            if st == (5, 5, 1, 1):
                U[side]["ev_unbl_f"] += 1
                U[o]["ev_unbl_a"] += 1
                U[side]["ev_xg_f"] += p
                U[o]["ev_xg_a"] += p
            elif st[2] and st[0] > st[1]:
                U[side]["pp_g"] += g
                U[o]["pk_g"] += g
        elif kind == "pen":
            side, st, sd, late, per, pkind, delayed = x
            if per >= 4:
                continue
            U[side]["pen_take"] += 1
            U[1 - side]["pen_draw"] += 1
    return U


def add(acc, u):
    for k, v in u.items():
        acc[k] = acc.get(k, 0) + v
    return acc


def league_rates(T):
    """The league's per-60 / per-attempt figures from the engine's own tables
    (what a multiplier of 1 means)."""
    raw = T.raw
    ev_secs = sum(v for k, v in raw["time"].items() if k.split("|")[0] == "5,5,1,1") / 2.0
    ev_att = sum(n for k, n in raw["att"].items() if k.split("|")[0] == "5,5,1,1") / 2.0
    ev_unbl = sum(n for k, (g, n) in raw["goal"].items() if k.split("|")[0] == "5,5,1,1") / 2.0
    ev_xg = sum(n * T.p_goal((5, 5, 1, 1), int(k.split("|")[1]), int(k.split("|")[2]))
                for k, (g, n) in raw["goal"].items() if k.split("|")[0] == "5,5,1,1") / 2.0
    pp_secs = pp_g = 0.0
    for k, v in raw["time"].items():
        st = [int(x) for x in k.split("|")[0].split(",")]
        if st[2] and st[3] and st[0] > st[1]:
            pp_secs += v
    for k, (g, n) in raw["goal"].items():
        st = [int(x) for x in k.split("|")[0].split(",")]
        if st[2] and st[3] and st[0] > st[1]:
            pp_g += g
    tot_secs = sum(raw["time"].values()) / 2.0
    pens = sum(raw["pen"].values()) / 2.0
    return {"ev_att60": 3600.0 * ev_att / ev_secs, "ev_xg_per": ev_xg / ev_unbl if ev_unbl else 0.06,
            "pp_g60": 3600.0 * pp_g / pp_secs if pp_secs else 6.0, "pen60": 3600.0 * pens / tot_secs}


def raw_profile(u, lg):
    """A team's UNSHRUNK multipliers from its accumulated counts (None where
    the denominator is empty)."""
    def r(a, b):
        return a / b if b else None
    att_off = r(3600.0 * u["ev_att_f"] / u["ev_secs"], lg["ev_att60"]) if u["ev_secs"] else None
    att_def = r(3600.0 * u["ev_att_a"] / u["ev_secs"], lg["ev_att60"]) if u["ev_secs"] else None
    xg_off = r(r(u["ev_xg_f"], u["ev_unbl_f"]) or 0.0, lg["ev_xg_per"]) if u["ev_unbl_f"] else None
    xg_def = r(r(u["ev_xg_a"], u["ev_unbl_a"]) or 0.0, lg["ev_xg_per"]) if u["ev_unbl_a"] else None
    fin = r(u["gi_g_f"], u["gi_xg_f"])
    gk = r(u["gi_g_a"], u["gi_xg_a"])
    implied_pp = (att_off or 1.0) * (xg_off or 1.0) * (fin or 1.0)
    implied_pk = (att_def or 1.0) * (xg_def or 1.0) * (gk or 1.0)
    pp = r(3600.0 * u["pp_g"] / u["pp_secs"], lg["pp_g60"] * implied_pp) if u["pp_secs"] else None
    pk = r(3600.0 * u["pk_g"] / u["pk_secs"], lg["pp_g60"] * implied_pk) if u["pk_secs"] else None
    pen_take = r(3600.0 * u["pen_take"] / u["secs"], lg["pen60"]) if u["secs"] else None
    pen_draw = r(3600.0 * u["pen_draw"] / u["secs"], lg["pen60"]) if u["secs"] else None
    return {"att_off": att_off, "att_def": att_def, "xg_off": xg_off, "xg_def": xg_def, "fin": fin, "gk": gk,
            "pp": pp, "pk": pk, "pen_take": pen_take, "pen_draw": pen_draw}


def shrink(raw, n, prior=None, K=None, persist=None):
    """Each unit n / (n + K) of the team's own figure (in logs) plus the rest
    of its prior; the prior is last season's profile pulled toward 1 by the
    unit's persistence, or 1."""
    K = K or K_GAMES
    persist = persist or PERSIST
    out = {}
    for k in UNIT_KEYS:
        pr = 0.0
        if prior and prior.get(k):
            pr = persist[k] * math.log(max(1e-3, prior[k]))
        v = raw.get(k)
        if v is None or v <= 0 or n <= 0:
            out[k] = math.exp(pr)
            continue
        w = n / (n + K[k])
        out[k] = math.exp(w * math.log(v) + (1.0 - w) * pr)
    return out


def season_units(games, T, fitmod=None, before=None, teams=None):
    """{abbr: accumulated counts} over regular-season games (before the date
    `before`, an ISO string, when given)."""
    acc = {}
    for rec in games:
        if rec.get("gtype") != 2:
            continue
        if before is not None and (rec.get("date") or "") >= before:
            continue
        U = game_units(rec, T)
        for s, ab in ((0, rec["home"]), (1, rec["away"])):
            if teams is not None and ab not in teams:
                continue
            add(acc.setdefault(ab, {}), U[s])
    return acc


def profiles(acc, T, priors=None, K=None, persist=None):
    """{abbr: engine profile} from accumulated counts and optional priors."""
    lg = league_rates(T)
    out = {}
    for ab, u in acc.items():
        prof = shrink(raw_profile(u, lg), u.get("games", 0), (priors or {}).get(ab), K, persist)
        prof["abbr"], prof["level_off"], prof["games"] = ab, 1.0, u.get("games", 0)
        out[ab] = prof
    for ab, pr in (priors or {}).items():
        if ab not in out:
            prof = shrink({}, 0, pr, K, persist)
            prof["abbr"], prof["level_off"], prof["games"] = ab, 1.0, 0
            out[ab] = prof
    return out


# ---- production: the season's units, kept current by the nightly job ---------
STATE_KEY = "nhl_deep"
DAY_KEY = "nhl_deep_day"
PRIORS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", "data", "nhl_deep_priors.json")
# Games a matchup for the board's deep figure. The validation priced at 300
# (+/-2.9pp of Monte Carlo noise on a half-weight component, so +/-1.4pp on the
# headline); measured 2026-10-16 at ~1.6 ms a game, 300 x 16 games is ~8 s,
# paid ONCE a date in a niced child and served from the deep store.
DEEP_N = 300
_SEED = 20261016


def season_of(date_iso):
    """The NHL season (by its start year) a date belongs to."""
    y, m = int(date_iso[:4]), int(date_iso[5:7])
    return y if m >= 8 else y - 1


def priors():
    """Last season's final profiles, committed (the prior for this one)."""
    try:
        with open(PRIORS_PATH) as fh:
            d = json.load(fh)
        return d.get("profiles") or {}
    except (OSError, ValueError) as e:
        errlog.note("NHLDEEP-priors", e)
        return {}


def update(season, max_fetch=400, log=None):
    """The nightly refresh: the season's schedule merged by id, every newly
    finished regular-season game fetched ONCE, checked against its final
    score, cut into units (nhl_pbp.walk) and added to the running counts. The
    raw events are dropped, so the state stays a few kilobytes a team instead
    of a season's play-by-play in memory. Returns the state."""
    import deep_cache
    state, _ts = deep_cache.load(STATE_KEY)
    if not isinstance(state, dict) or state.get("season") != int(season):
        state = {"season": int(season), "acc": {}, "seen": [], "events": [], "mismatch": []}
    events = {e["id"]: e for e in state.get("events") or []}
    try:
        for g in P.schedule(season, log=log):
            events[g["id"]] = g
    except Exception as e:
        errlog.note("NHLDEEP-sched", e, path=str(season))
    T = E.tables()
    seen = set(state.get("seen") or [])
    mismatch = list(state.get("mismatch") or [])
    acc = state.get("acc") or {}
    fetched = 0
    for g in sorted(events.values(), key=lambda g: (g.get("date") or "", g["id"])):
        if g.get("gtype") != 2 or g.get("state") not in P.FINAL_STATES or g["id"] in seen or g["id"] in mismatch:
            continue
        if fetched >= max_fetch:
            break
        try:
            rec = P.fetch_game(g["id"])
        except Exception as e:
            errlog.note("NHLDEEP-game", e, path=str(g["id"]))
            continue
        fetched += 1
        if not rec or P.goals_check(rec) != (rec["home_score"], rec["away_score"]):
            mismatch.append(g["id"])
            continue
        U = game_units(rec, T)
        for s_, ab in ((0, rec["home"]), (1, rec["away"])):
            add(acc.setdefault(ab, {}), U[s_])
        seen.add(g["id"])
    state.update(events=sorted(events.values(), key=lambda g: (g.get("date") or "", g["id"])),
                 seen=sorted(seen), acc=acc, mismatch=mismatch, updated=time.time(),
                 profiles=profiles(acc, T, priors()))
    deep_cache.save(STATE_KEY, state)
    return state


def current_profiles(season):
    """{NHL abbr: profile} for `season`: the nightly state's when it is this
    season's, else the committed priors alone (a team's profile before its
    first game). None when neither exists."""
    try:
        import deep_cache
        state, _ts = deep_cache.load(STATE_KEY)
        if isinstance(state, dict) and state.get("season") == int(season) and state.get("profiles"):
            return state["profiles"]
    except Exception as e:
        errlog.note("NHLDEEP-load", e)
    pr = priors()
    return profiles({}, E.tables(), pr) if pr else None


def game_figure(hp, ap, date, home, away, n=DEEP_N, T=None):
    """One matchup's deep figure: n games on a seed that every worker and the
    PC share (crc32 of the date and the teams)."""
    import random
    T = T or E.tables()
    rng = random.Random(zlib.crc32(f"{_SEED}|{date}|{home}|{away}".encode()))
    hw = ot = 0
    gh = ga = 0
    tot = [0] * 13
    for _ in range(n):
        g = E.play_game(hp, ap, rng, T)
        hw += g["winner"] == 0
        ot += 1 if g["ot"] else 0
        gh += g["score"][0]
        ga += g["score"][1]
        tot[min(12, g["score"][0] + g["score"][1])] += 1
    return {"p_home": round(hw / n, 4), "p_away": round(1 - hw / n, 4), "p_ot": round(ot / n, 4),
            "exp_home": round(gh / n, 2), "exp_away": round(ga / n, 2), "exp_total": round((gh + ga) / n, 2),
            "total_dist": [round(x / n, 4) for x in tot], "n": n}


def _figures(date, matchups, profs):
    out = {}
    T = E.tables()
    for h_espn, a_espn in matchups:
        h, a = P.from_espn(h_espn), P.from_espn(a_espn)
        if h in profs and a in profs:
            out[f"{h_espn}|{a_espn}"] = game_figure(profs[h], profs[a], date, h_espn, a_espn, T=T)
    return out


def _figures_blob(raw):
    import pickle
    job = pickle.loads(raw)
    return pickle.dumps(_figures(job["date"], job["matchups"], job["profiles"]), protocol=pickle.HIGHEST_PROTOCOL)


_DAYS_KEPT = 7


def _days():
    import deep_cache
    try:
        cached, _ts = deep_cache.load(DAY_KEY)
        if isinstance(cached, dict) and isinstance(cached.get("days"), dict):
            return cached["days"]
    except Exception as e:
        errlog.note("NHLDEEP-day-load", e)
    return {}


def cached_day_figures(date):
    """The figures kept for `date`, if they were computed that day; {} else."""
    return _days().get(date) or {}


def day_figures(date, matchups):
    """{"HOME|AWAY" (ESPN abbreviations): figure} for a date's games, computed
    once a date in a niced child process (the board builds inside a web worker
    on a one-core quota) and kept in the deep store for every worker. {} when
    no profiles exist or the child fails (never computed in-process)."""
    import deep_cache
    days = _days()
    have = days.get(date) or {}
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
             "import sys, nhl_deep_data; sys.stdout.buffer.write(nhl_deep_data._figures_blob(sys.stdin.buffer.read()))"],
            input=pickle.dumps({"date": date, "matchups": list(matchups), "profiles": profs}),
            cwd=os.path.dirname(os.path.abspath(__file__)), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=180)
        if out.returncode == 0 and out.stdout:
            figs = pickle.loads(out.stdout)
        else:
            errlog.note("NHLDEEP-child", msg=f"child rc={out.returncode}; no deep figures this build")
    except Exception as e:
        errlog.note("NHLDEEP-child", e)
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
        errlog.note("NHLDEEP-day-save", e)
    return figs
