"""Does game-day weather move a team's passing, rushing and kicking away from
what Sleeper projected? RESEARCH; the rule below was written 2026-10-18 13:30
UTC, before any number in this file was computed.

The DFS engine's points come from the market's total (which prices the
weather already), its passing volume from Sleeper's projected attempts
(ATT_WEIGHT 1) and its kicks from the deep engine's distance curve, and the
app's lineup sheet reads Sleeper's lines directly. Nothing in the stack reads
the weather. If Sleeper's lines do not move enough for wind, rain or cold, the
split of a fixed total between passing, rushing and kicking is wrong on those
days -- the thing a DFS lineup is built from.

THE RULE (written before any number below was computed)
  Data: research/data/player_weeks_*.csv.gz (Sleeper's projection and box
  score per player, 2022-2025 and 2026 weeks 1-5) and nflverse's
  play_by_play_<season>.csv.gz (the game's roof, temperature, wind and
  weather text as the gamebook records them at kickoff, and every field
  goal). Regular season only.
  Weather features of a game whose roof is "outdoors" or "open" (a dome or a
  closed roof has none, and is outside both the fit and the test): wind and
  temperature from nflverse's columns, else parsed from the weather text
  ("Wind: SW 13 mph", "calm" = 0; "Temp: 51"); a game with either missing is
  left out. x = (wind_x, precip, cold_x) with wind_x = max(0, wind - 10) mph,
  precip = the text names rain, snow, showers, drizzle, sleet or flurries and
  not "chance" or "possible", cold_x = max(0, 40 - temp) / 10.
  A. Team lines. One unit per outdoor (season, week, team) for each of
     pass_att, pass_yd, rush_att, rush_yd: r = log(actual / projected), both
     the team's sums over its rows (actual over every row Sleeper scored;
     projected over every row it projected), both positive. Fit by least
     squares on 2022-2024: r = a + b . x. Per unit
     d = (r - a - b . x)^2 - (r - a)^2 (the intercept is Sleeper's general
     bias, kept out of the comparison so the test is the weather's alone).
  B. Field goals. One unit per attempt (blocked = missed), z = (distance -
     40) / 10. Base: logit p = c0 + c1 z + c2 z^2, fitted on every 2022-2024
     attempt. Weather: base + k1 wind_x + k2 wind_x z + k3 precip + k4
     cold_x, every term fitted jointly on the same attempts (indoor x = 0).
     Per unit d = the weather model's log loss minus the base model's.
  1. TEST on 2025, frozen, over units with some x non-zero: an outcome passes
     only if the mean d is below zero by more than two standard errors.
  2. CONFIRM on 2026 (the weeks the data holds), the same units: the mean d
     must not be above zero by more than two standard errors. Fewer than 30
     units reads as unreadable and does not block.
  2025 is a fair test: nothing in the stack has read the weather, and every
  coefficient comes from 2022-2024.
  The gamebook's conditions are what happened at kickoff; production would
  read a forecast (weather.get_weather, the module the baseball board uses),
  which is noisier, so a passing effect is an upper bound on what a forecast
  delivers.
  What a passing outcome changes: A -- the team's Sleeper line for that
  outcome is multiplied by exp(b . x) at the forecast's features (precip = a
  50%+ chance; a retractable roof counts as closed), every player's share of
  it scaled by the same factor (pass_att: the quarterback's attempts and
  completions and every receiver's targets and receptions; pass_yd: the
  quarterback's passing yards and every receiver's receiving yards; rush_att
  and rush_yd: each rusher's carries or rushing yards), before nfl_recon
  reconciles the team; the engine reads its attempts anchor from those
  lines. B -- the DFS engine's field-goal logit gains k . x at each kick's
  distance (a game weather argument to play_game, off for every other
  caller, as ENV_SD is).
  Reported beside it: every coefficient, the unit counts, the mean d and its
  standard error on both years, and the mean r of the units by wind band.

Usage: python3 -m research.nfl_weather_validation --nflverse DIR
  (DIR holds play_by_play_<season>.csv.gz for 2022-2026). Writes
  research/data/nfl_weather_validation.json.
"""
import argparse
import csv
import gzip
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

FIT = (2022, 2023, 2024)
TEST, CONFIRM = 2025, 2026
OUTCOMES = ("pass_att", "pass_yd", "rush_att", "rush_yd")
WIND_FREE, COLD_AT = 10.0, 40.0
_OUTDOOR = ("outdoors", "open")
_TEAM = {"LA": "LAR"}
_PRECIP = re.compile(r"rain|snow|shower|drizzle|sleet|flurr", re.I)
_NOT_PRECIP = re.compile(r"chance|possible", re.I)


def parse_weather(roof, temp, wind, text):
    """(wind_mph, temp_f, precip) of an outdoor game, or None (indoors, or a
    reading missing)."""
    if (roof or "").lower() not in _OUTDOOR:
        return None
    text = text or ""
    w = _num(wind)
    if w is None:
        m = re.search(r"wind:\s*([a-z]+\s*)?(\d+)\s*mph", text, re.I)
        w = float(m.group(2)) if m else (0.0 if re.search(r"wind:\s*calm", text, re.I) else None)
    t = _num(temp)
    if t is None:
        m = re.search(r"temp:\s*(-?\d+)", text, re.I)
        t = float(m.group(1)) if m else None
    if w is None or t is None:
        return None
    precip = bool(_PRECIP.search(text)) and not _NOT_PRECIP.search(text)
    return w, t, precip


def features(wx):
    """x = (wind_x, precip, cold_x) of a parsed reading; zeros indoors."""
    if wx is None:
        return (0.0, 0.0, 0.0)
    w, t, p = wx
    return (max(0.0, w - WIND_FREE), 1.0 if p else 0.0, max(0.0, COLD_AT - t) / 10.0)


def _num(v):
    try:
        return float(v) if v not in ("", None, "NA") else None
    except (TypeError, ValueError):
        return None


def read_pbp(paths):
    """({game_id: {season, week, home, away, wx}}, [(season, distance, made,
    wx)]) from nflverse's play-by-play, regular season."""
    games, fgs = {}, []
    for path in paths:
        if not os.path.exists(path):
            continue
        with gzip.open(path, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("season_type") != "REG":
                    continue
                gid = r["game_id"]
                if gid not in games:
                    games[gid] = {"season": int(r["season"]), "week": int(r["week"]),
                                  "home": _TEAM.get(r["home_team"], r["home_team"]),
                                  "away": _TEAM.get(r["away_team"], r["away_team"]),
                                  "wx": parse_weather(r.get("roof"), r.get("temp"), r.get("wind"), r.get("weather")),
                                  "roof": r.get("roof")}
                if r.get("field_goal_attempt") == "1" and _num(r.get("kick_distance")) is not None:
                    fgs.append((int(r["season"]), _num(r["kick_distance"]), r.get("field_goal_result") == "made",
                                games[gid]["wx"]))
    return games, fgs


def team_units(rows, games):
    """[(season, outcome, r, x)] for every outdoor team-game."""
    by_game = {}
    for g in games.values():
        for team in (g["home"], g["away"]):
            by_game[(g["season"], g["week"], team)] = g
    sums = {}
    for r in rows:
        k = (r["season"], r["week"], r["team"])
        s = sums.setdefault(k, {"proj": {o: 0.0 for o in OUTCOMES}, "act": {o: 0.0 for o in OUTCOMES}})
        for o in OUTCOMES:
            if r.get("has_proj"):
                s["proj"][o] += float(r.get(f"proj_{o}") or 0.0)
            if r.get("has_act"):
                s["act"][o] += float(r.get(f"act_{o}") or 0.0)
    out = []
    for k, s in sums.items():
        g = by_game.get(k)
        if not g or g["wx"] is None:
            continue
        x = features(g["wx"])
        for o in OUTCOMES:
            p, a = s["proj"][o], s["act"][o]
            if p > 0 and a > 0:
                out.append((k[0], o, math.log(a / p), x))
    return out


def ols(ys, xs):
    """Least squares with an intercept: (a, [b...]). Pure Python normal
    equations (four unknowns)."""
    X = [[1.0] + list(x) for x in xs]
    k = len(X[0])
    A = [[sum(r[i] * r[j] for r in X) for j in range(k)] for i in range(k)]
    v = [sum(r[i] * y for r, y in zip(X, ys)) for i in range(k)]
    beta = _solve(A, v)
    return beta[0], beta[1:]


def _solve(A, v):
    n = len(v)
    M = [row[:] + [v[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12:
            M[c][c] = 1e-12
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * b for a, b in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def fg_design(dist, wx, weather):
    z = (dist - 40.0) / 10.0
    row = [1.0, z, z * z]
    if weather:
        wind_x, precip, cold_x = features(wx)
        row += [wind_x, wind_x * z, precip, cold_x]
    return row


def logit_fit(X, y, iters=50):
    """Newton-Raphson logistic regression with a tiny ridge (pure Python)."""
    k = len(X[0])
    b = [0.0] * k
    for _ in range(iters):
        g = [0.0] * k
        H = [[1e-6 if i == j else 0.0 for j in range(k)] for i in range(k)]
        for row, yi in zip(X, y):
            eta = sum(bi * xi for bi, xi in zip(b, row))
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, eta))))
            for i in range(k):
                g[i] += (yi - p) * row[i]
                for j in range(k):
                    H[i][j] += p * (1 - p) * row[i] * row[j]
        step = _solve(H, g)
        b = [bi + si for bi, si in zip(b, step)]
        if max(abs(s) for s in step) < 1e-9:
            break
    return b


def _ll(b, row, yi):
    eta = sum(bi * xi for bi, xi in zip(b, row))
    p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, eta))))
    p = min(1 - 1e-12, max(1e-12, p))
    return -(math.log(p) if yi else math.log(1 - p))


def stats(d):
    n = len(d)
    if n < 2:
        return {"n": n, "mean": None, "se": None}
    mu = sum(d) / n
    sd = (sum((x - mu) ** 2 for x in d) / (n - 1)) ** 0.5
    return {"n": n, "mean": round(mu, 6), "se": round(sd / n ** 0.5, 6)}


def verdict(test, conf):
    passed = test["mean"] is not None and test["mean"] < -2 * test["se"]
    readable = conf["n"] >= 30 and conf["mean"] is not None
    vetoed = bool(readable and conf["mean"] > 2 * conf["se"])
    return {"test": test, "confirm": conf, "test_passed": bool(passed), "confirm_readable": readable,
            "confirm_vetoed": vetoed, "ship": bool(passed and not vetoed)}


def team_result(units):
    out = {}
    for o in OUTCOMES:
        fit = [(r, x) for s, oo, r, x in units if oo == o and s in FIT]
        a, b = ols([r for r, _x in fit], [x for _r, x in fit])

        def d_of(season):
            return [(r - a - sum(bi * xi for bi, xi in zip(b, x))) ** 2 - (r - a) ** 2
                    for s, oo, r, x in units if oo == o and s == season and any(x)]
        bands = {}
        for s, oo, r, x in units:
            if oo == o:
                band = "calm" if x[0] == 0 else ("10-15" if x[0] <= 5 else "15+")
                bands.setdefault(band, []).append(r)
        out[o] = {"a": round(a, 5), "b": {"wind_x": round(b[0], 5), "precip": round(b[1], 5), "cold_x": round(b[2], 5)},
                  "n_fit": len(fit), **verdict(stats(d_of(TEST)), stats(d_of(CONFIRM))),
                  "mean_r_by_wind": {k: {"n": len(v), "mean": round(sum(v) / len(v), 4)} for k, v in bands.items()}}
    return out


def fg_result(fgs):
    fit = [f for f in fgs if f[0] in FIT]
    y = [1 if f[2] else 0 for f in fit]
    bb = logit_fit([fg_design(f[1], f[3], False) for f in fit], y)
    bw = logit_fit([fg_design(f[1], f[3], True) for f in fit], y)

    def d_of(season):
        return [_ll(bw, fg_design(f[1], f[3], True), f[2]) - _ll(bb, fg_design(f[1], f[3], False), f[2])
                for f in fgs if f[0] == season and any(features(f[3]))]
    return {"base": [round(c, 5) for c in bb],
            "weather": dict(zip(("c0", "c1", "c2", "wind_x", "wind_x_z", "precip", "cold_x"), (round(c, 5) for c in bw))),
            "n_fit": len(fit), **verdict(stats(d_of(TEST)), stats(d_of(CONFIRM)))}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--nflverse", required=True)
    a = ap.parse_args(argv)
    from research import hist_data as H
    data = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    rows = H.read_csv_gz(os.path.join(data, "player_weeks_2022_2025.csv.gz"))
    rows += H.read_csv_gz(os.path.join(data, "player_weeks_2026.csv.gz"))
    seasons = FIT + (TEST, CONFIRM)
    games, fgs = read_pbp([os.path.join(a.nflverse, f"play_by_play_{s}.csv.gz") for s in seasons])
    units = team_units(rows, games)
    res = {"rule": __doc__.split("Usage:")[0],
           "games": {str(s): {"all": sum(1 for g in games.values() if g["season"] == s),
                              "outdoor_read": sum(1 for g in games.values() if g["season"] == s and g["wx"] is not None)}
                     for s in seasons},
           "team": team_result(units), "fg": fg_result(fgs)}
    out = os.path.join(data, "nfl_weather_validation.json")
    with open(out, "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({"team": {o: {k: v[k] for k in ("b", "test", "confirm", "ship")} for o, v in res["team"].items()},
                      "fg": {k: res["fg"][k] for k in ("weather", "test", "confirm", "ship")}}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
