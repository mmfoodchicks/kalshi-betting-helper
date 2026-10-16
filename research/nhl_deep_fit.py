"""The NHL deep engine's tables, measured from the league's play-by-play
(nhl_pbp records). RESEARCH -- the output, research/data/nhl_deep_tables.json,
is what nhl_deep_sim samples from.

Every table is a COUNT (numerator and denominator kept apart), so a refit on
fewer seasons is the same code on fewer games, and the engine's smoothing
decides how far a thin cell leans on its parent.

Time in a strength state: the clock between two consecutive events is charged
to their situationCodes, HALF to each when they differ. A state change no
event marks (a goalie pulled after a faceoff, a power play expiring) happens
somewhere inside that gap; charging the whole gap to the later event, as the
first version did, over-charged the short-lived states -- the extra attacker
and the power play -- and understated their attempt rates, which is how the
engine came to score 18% too few empty-net goals. Penalty calls and goals sit
at stoppages, where the gap is zero.

  state     (own skaters, opponent skaters, own goalie in, opponent goalie in)
            from the acting team's view -- "5,4,1,1" is a power play, "6,5,0,1"
            an extra attacker with the own net empty
  sd        the acting team's goal difference before the event, clamped to +/-3
  late      1 in the last five minutes of the third period, else 0
  dist      unblocked-attempt distance bucket (DIST_EDGES, feet)
  reb       1 when the attempt follows the same team's saved shot within 3 s

  python3 -m research.nhl_deep_fit <store dir> --seasons 2022,2023,2024,2025 --out research/data/nhl_deep_tables.json
"""
import json
import os
import pickle
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import nhl_pbp as P             # noqa: E402

# the game walk and its buckets live in nhl_pbp (production), shared with the
# team profiles (nhl_deep_data) so a game is cut the same way for both
DIST_EDGES, LATE_SECS, REB_SECS = P.DIST_EDGES, P.LATE_SECS, P.REB_SECS
dist_bucket, skey, clamp_sd, walk = P.dist_bucket, P.skey, P.clamp_sd, P.walk


def load_stores(store_dir, seasons):
    out = []
    for s in seasons:
        p = os.path.join(store_dir, f"nhl_pbp_{s}.pkl")
        if not os.path.exists(p):
            print(f"[nhl-fit] missing {p}", file=sys.stderr)
            continue
        with open(p, "rb") as fh:
            st = pickle.load(fh)
        if isinstance(st, dict) and "payload" in st:
            st = st["payload"]
        out.append((int(s), st))
    return out


def _bump(d, k, i, v=1):
    row = d.setdefault(k, [0, 0])
    row[i] += v


def fit(stores, gtypes=(2,)):
    """The raw count tables from (season, store) pairs."""
    T = {"time": {}, "att": {}, "unbl": {}, "goal": {}, "goal_sd": {}, "sog": {}, "pen": {}, "pen_mix": {}, "so": [0, 0],
         "home": {"att": [0, 0], "pen": [0, 0], "goal": [0, 0]}, "pull": {}, "pull_opp": {},
         "ot_time": 0, "ot_att": [0, 0], "ot_goal": 0, "ot_unbl": 0,
         "delayed": {"secs": [], "att": 0, "unbl": 0, "sog": 0, "goals": 0, "windows": 0},
         "pen_delayed": [0, 0], "pen_even": 0,
         "games": 0, "goals": 0, "home_wins": 0, "last": {}, "seasons": []}
    for season, st in stores:
        T["seasons"].append(season)
        for rec in (st.get("games") or {}).values():
            if rec.get("gtype") not in gtypes:
                continue
            T["games"] += 1
            T["goals"] += rec["home_score"] + rec["away_score"]
            T["home_wins"] += rec["home_score"] > rec["away_score"]
            T["last"][rec["last"]] = T["last"].get(rec["last"], 0) + 1
            pulled_seen = {0: False, 1: False}
            for kind, x in walk(rec):
                if kind == "time":
                    hst, sd, late, per, dt = x
                    if per >= 4 and rec.get("gtype") == 2:
                        T["ot_time"] += dt
                        continue
                    ast = (hst[1], hst[0], hst[3], hst[2])
                    for st_, sd_ in ((hst, sd), (ast, -sd)):
                        k = f"{skey(st_)}|{sd_}|{late}"
                        T["time"][k] = T["time"].get(k, 0) + dt
                    # an extra attacker for a side trailing late in the third:
                    # the first second its net is seen empty (not a delayed
                    # penalty -- those are taken while the clock runs with
                    # the opponent in possession, and last seconds)
                    for side, st_, sd_ in ((0, hst, sd), (1, ast, -sd)):
                        if per == 3 and sd_ < 0 and st_[2] == 0 and st_[3] == 1 and not pulled_seen[side]:
                            pulled_seen[side] = True
                elif kind == "att":
                    side, st_, sd, late, per, code, db, reb, flags = x
                    if per >= 4 and rec.get("gtype") == 2:
                        T["ot_att"][side] += 1
                        if code != P.BLOCK:
                            T["ot_unbl"] += 1
                        if code == P.GOAL:
                            T["ot_goal"] += 1
                        continue
                    k = f"{skey(st_)}|{sd}|{late}"
                    T["att"][k] = T["att"].get(k, 0) + 1
                    T["home"]["att"][side] += 1
                    _bump(T["unbl"], skey(st_), 1)
                    if code != P.BLOCK:
                        _bump(T["unbl"], skey(st_), 0)
                        gk = f"{skey(st_)}|{db}|{reb}"
                        _bump(T["goal"], gk, 1)
                        # the same cell split by the score and the late flag:
                        # conversion moves with the game state (2025-26 at
                        # five-on-five: 5.5% tied or trailing late, 6.7%
                        # leading late, 6.1% overall) and the cell table
                        # cannot see it
                        gs = f"{gk}|{sd}|{late}"
                        _bump(T["goal_sd"], gs, 1)
                        if code == P.GOAL:
                            _bump(T["goal_sd"], gs, 0)
                        if code != P.GOAL:
                            _bump(T["sog"], skey(st_), 1)
                            if code == P.SOG:
                                _bump(T["sog"], skey(st_), 0)
                        if code == P.GOAL:
                            _bump(T["goal"], gk, 0)
                            T["home"]["goal"][side] += 1
                elif kind == "pen":
                    side, st_, sd, late, per, pkind, delayed = x
                    if per >= 4 and rec.get("gtype") == 2:
                        continue
                    k = f"{skey(st_)}|{sd}|{late}"
                    T["pen"][k] = T["pen"].get(k, 0) + 1
                    T["home"]["pen"][side] += 1
                    T["pen_mix"][pkind] = T["pen_mix"].get(pkind, 0) + 1
                    T["pen_delayed"][0] += delayed
                    T["pen_delayed"][1] += 1
                elif kind == "pen_even":
                    T["pen_even"] += 1
                elif kind == "dwin":
                    secs, att_, unbl_, sog_, goals_, waived_ = x
                    D = T["delayed"]
                    D["secs"].append(secs)
                    D["att"] += att_
                    D["unbl"] += unbl_
                    D["sog"] += sog_
                    D["goals"] += goals_
                    D["windows"] += 1
                elif kind == "so":
                    side, g = x
                    T["so"][0] += g
                    T["so"][1] += 1
            for side, sd_at, rem, since in _pulls(rec):
                k = str(min(3, -sd_at))
                T["pull"].setdefault(k, []).append([rem, since])
            for side, d, outcome in _pull_opps(rec):
                row = T["pull_opp"].setdefault(str(d), [0, 0, 0])      # pulled, neither, goal first
                row[0 if outcome == 1 else (1 if outcome == 0 else 2)] += 1
    return T


def _pulls(rec):
    """[(side, goal difference, seconds left at the pull, seconds left when
    that deficit began)] for each side's FIRST extra-attacker pull while
    trailing in the third period: the first event whose situation shows its
    own net empty with the opponent's in, not within ten seconds after a
    delayed-penalty call. The deficit's start (the goal that made it; 1200+
    when it predates the third) is what makes the timing usable: the raw pull
    times lean late because many deficits only arrive in the last minute, and
    a team trailing all period pulls earlier than that pooled list says."""
    score = [0, 0]
    out, seen = [], set()
    last_delay = -99
    since = 1200 + 1200                      # seconds left when the current score began
    for e in P.iter_events(rec):
        per, t, side, code, sit, flags = e[P.I_PER], e[P.I_T], e[P.I_TEAM], e[P.I_CODE], e[P.I_SIT], e[P.I_FLAGS]
        if flags & P.F_SO:
            break
        if code == P.DELAYED_PEN:
            last_delay = t if per == 3 else -99
        if per == 3:
            for s_ in (0, 1):
                st = P.strength(sit, s_)
                if st and st[2] == 0 and st[3] == 1 and s_ not in seen and t - last_delay > 10:
                    sd = score[s_] - score[1 - s_]
                    if sd < 0:
                        seen.add(s_)
                        out.append((s_, sd, 1200 - t, since))
        if code == P.GOAL and side in (0, 1):
            score[side] += 1
            since = (1200 - t) if per == 3 else (1200 + (3 - per) * 1200 - t)
    return out


# The checkpoint, by deficit, at which "does a trailing team pull its goalie"
# is asked: seconds left in the third. Past the bulk of each deficit's pull
# times (2025-26 medians 110 / 196 / 315 s), so nearly every team that will
# pull is still trailing there.
PULL_CHECK = {1: 180, 2: 300, 3: 480}


def _pull_opps(rec):
    """[(side, deficit, outcome)] for each side trailing by d (capped at 3) at
    that deficit's checkpoint in the third: outcome 1 = its net was seen
    empty (the opponent's in, outside a delayed penalty) before the score
    changed, 0 = regulation ended with neither, -1 = a goal came first. The
    engine's P(pull | deficit) is pulled / (pulled + neither): it is the
    INTENTION, and the engine lets a goal pre-empt it on its own -- counting
    the goal-first cases as "did not pull" pre-empted them twice and pulled
    goalies 15% too little."""
    out = []
    for d, check in PULL_CHECK.items():
        score = [0, 0]
        state = None                # (side, deficit) once the checkpoint is reached
        last_delay = -99
        pulled = goal_first = False
        for e in P.iter_events(rec):
            per, t, side, code, sit, flags = e[P.I_PER], e[P.I_T], e[P.I_TEAM], e[P.I_CODE], e[P.I_SIT], e[P.I_FLAGS]
            if flags & P.F_SO or per > 3:
                break
            if state is None and per == 3 and t >= 1200 - check:
                for s_ in (0, 1):
                    if min(3, score[1 - s_] - score[s_]) == d:
                        state = (s_, d)
                if state is None:
                    break
            if code == P.DELAYED_PEN:
                last_delay = t
            if state is not None:
                stv = P.strength(sit, state[0])
                if stv and stv[2] == 0 and stv[3] == 1 and t - last_delay > 10:
                    pulled = True
                    break
            if code == P.GOAL and side in (0, 1):
                score[side] += 1
                if state is not None:
                    goal_first = True
                    break
        if state is not None:
            out.append((state[0], d, 1 if pulled else (-1 if goal_first else 0)))
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("store")
    ap.add_argument("--seasons", default="2022,2023,2024,2025")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    stores = load_stores(a.store, [int(x) for x in a.seasons.split(",")])
    T = fit(stores)
    T["dist_edges"] = list(DIST_EDGES)
    T["late_secs"], T["reb_secs"] = LATE_SECS, REB_SECS
    T["pull_check"] = {str(k): v for k, v in PULL_CHECK.items()}
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(T, fh, sort_keys=True)
    return T


if __name__ == "__main__":
    T = main(sys.argv[1:])
    n = T["games"]
    print(f"games {n}, goals/game {T['goals'] / max(1, n):.3f}, home wins {T['home_wins'] / max(1, n):.3f}, last {T['last']}")


# ---- reliability and persistence of the team units (nhl_deep_data's K and PERSIST)
def _corr(xs, ys):
    import statistics
    if len(xs) < 5:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return sxy / (sx * sy) if sx and sy else None


def measure_k(stores, T):
    """K per unit (games): split each team's regular season into odd and
    even games, correlate the two halves' log units across teams (r = the
    reliability of a half season of h games), K = h (1 - r) / r. Returns
    {unit: [K per season]} and the half-season correlations."""
    import math
    import nhl_deep_data as D
    lg = D.league_rates(T)
    out, corr = {}, {}
    for season, st in stores:
        games = sorted((g for g in st["games"].values() if g.get("gtype") == 2), key=lambda g: (g["date"] or "", g["id"]))
        halves = ({}, {})
        seen = {}
        for rec in games:
            U = D.game_units(rec, T, sys.modules[__name__])
            for s, ab in ((0, rec["home"]), (1, rec["away"])):
                i = seen.get(ab, 0)
                seen[ab] = i + 1
                D.add(halves[i % 2].setdefault(ab, {}), U[s])
        teams = sorted(set(halves[0]) & set(halves[1]))
        h = sum(seen[ab] for ab in teams) / (2.0 * len(teams))
        for k in D.UNIT_KEYS:
            xs, ys = [], []
            for ab in teams:
                a = D.raw_profile(halves[0][ab], lg).get(k)
                b = D.raw_profile(halves[1][ab], lg).get(k)
                if a and b and a > 0 and b > 0:
                    xs.append(math.log(a))
                    ys.append(math.log(b))
            r = _corr(xs, ys)
            corr.setdefault(k, []).append(None if r is None else round(r, 3))
            out.setdefault(k, []).append(None if not r or r <= 0.02 else round(h * (1 - r) / r, 1))
    return out, corr


def measure_persistence(stores, T, K):
    """Year-over-year: the correlation of each team's full-season log unit
    with the next season's, divided by the full season's reliability (so it is
    the persistence of the TRUE unit, applied to last season's shrunk one)."""
    import math
    import nhl_deep_data as D
    lg = D.league_rates(T)
    full = {}
    for season, st in stores:
        acc = D.season_units(list(st["games"].values()), T, sys.modules[__name__])
        full[season] = {ab: (D.raw_profile(u, lg), u["games"]) for ab, u in acc.items()}
    out = {}
    seasons = sorted(full)
    for k in D.UNIT_KEYS:
        vals = []
        for a, b in zip(seasons, seasons[1:]):
            if b != a + 1:
                continue
            xs, ys, ns = [], [], []
            for ab in set(full[a]) & set(full[b]):
                x, y = full[a][ab][0].get(k), full[b][ab][0].get(k)
                if x and y and x > 0 and y > 0:
                    xs.append(math.log(x))
                    ys.append(math.log(y))
                    ns.append(full[a][ab][1])
            r = _corr(xs, ys)
            if r is None:
                continue
            n = sum(ns) / len(ns)
            rel = n / (n + K[k])
            vals.append(round(min(1.0, max(0.0, r / rel)), 3))
        out[k] = vals
    return out
