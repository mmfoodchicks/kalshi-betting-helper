"""Wind alone, judged on games not yet played. RESEARCH; the rule below was
written 2026-10-18 13:41 UTC, after stage 1 (research/nfl_weather_validation.py)
had failed, and before any week-6 team line had been computed: the
player-week table ends at week 5, and the one week-6 game in the nflverse
file in hand (the Thursday game) entered stage 1 only through the 2026
field-goal aggregate.

Stage 1 fitted wind, rain or snow, and cold together on 2022-2024 and none
of its team outcomes beat Sleeper's lines on 2025. Afterwards, looking (not
testing): team passing yards in wind over 15 mph ran under Sleeper's
projection in every season, 2022-2025, by more than calm games did, while
the rain and cold terms had no stable sign. That is a hypothesis drawn from
the data stage 1 already spent, so it is judged only on games played after it
was written.

THE RULE (written before any 2026 week-6 team line had been computed)
  Data, units and r exactly as stage 1 part A (outdoor team-games,
  r = log(actual / Sleeper's projected team line)), for pass_att and pass_yd
  only. One feature: wind_x = max(0, wind - 10) mph.
  Fit on 2022-2025 by least squares: r = a + b wind_x.
  TEST on 2026 weeks 6-18, read once, after week 18's games are in nflverse's
  play-by-play file: units with wind_x > 0 from the gamebook,
  d = (r - a - b wind_x)^2 - (r - a)^2.
  An outcome ships only if BOTH
    1. the mean d is below zero by more than two standard errors, and
    2. the same d computed with wind_x from the forecast nfl_props_log froze
       before kickoff (units with a logged forecast whose wind_x > 0) has a
       mean below zero -- production reads the forecast, not the gamebook.
  Fewer than 30 gamebook units: unreadable, nothing ships, and the question
  carries to 2027 by a rule written before that season.
  What a passing outcome changes: stage 1's part A for that outcome, with
  b wind_x alone.

Usage: python3 -m research.nfl_weather_stage2 --nflverse DIR --props-store DIR
  (DIR holds play_by_play_<season>.csv.gz 2022-2026; the props store is a
  deep store holding nfl_props_2026_wNN). Writes
  research/data/nfl_weather_stage2.json.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

FIT = (2022, 2023, 2024, 2025)
TEST_SEASON, TEST_WEEKS = 2026, tuple(range(6, 19))
OUTCOMES = ("pass_att", "pass_yd")
MIN_UNITS = 30


def forecast_wind(store_dir, season=TEST_SEASON, weeks=TEST_WEEKS):
    """{(week, team): forecast wind mph} from the frozen kickoff forecasts."""
    import deep_cache
    import nfl_props_log
    os.environ["DEEP_CACHE_DIR"] = store_dir
    deep_cache.CACHE_DIR = store_dir
    out = {}
    for wk in weeks:
        st, _ts = deep_cache.load(nfl_props_log.KEY.format(season=season, week=wk))
        for w in ((st or {}).get("weather") or {}).values():
            wx = w.get("wx") or {}
            if wx.get("roof") == "outdoors" and wx.get("wind_mph") is not None:
                for team in (w.get("home"), w.get("away")):
                    out[(wk, _sleeper(team))] = float(wx["wind_mph"])
    return out


def _sleeper(code):
    """Kalshi's / ESPN's codes to Sleeper's (the table's)."""
    return {"WSH": "WAS", "JAC": "JAX", "LA": "LAR"}.get(str(code or "").upper(), str(code or "").upper())


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--nflverse", required=True)
    ap.add_argument("--props-store", required=True)
    a = ap.parse_args(argv)
    from research import hist_data as H
    from research import nfl_weather_validation as S1
    data = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    rows = H.read_csv_gz(os.path.join(data, "player_weeks_2022_2025.csv.gz"))
    rows += H.read_csv_gz(os.path.join(data, "player_weeks_2026.csv.gz"))
    games, _fgs = S1.read_pbp([os.path.join(a.nflverse, f"play_by_play_{s}.csv.gz") for s in FIT + (TEST_SEASON,)])
    # stage 1's team_units drops the week; rebuilt here keyed so the
    # forecast can join
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
    import math
    units = []
    for k, s in sums.items():
        g = by_game.get(k)
        if not g or g["wx"] is None:
            continue
        wind_x = S1.features(g["wx"])[0]
        for o in OUTCOMES:
            p, act = s["proj"][o], s["act"][o]
            if p > 0 and act > 0:
                units.append((k[0], k[1], k[2], o, math.log(act / p), wind_x))
    fw = forecast_wind(a.props_store)
    res = {"rule": __doc__.split("Usage:")[0], "outcomes": {}}
    for o in OUTCOMES:
        fit = [(r, x) for se, _wk, _t, oo, r, x in units if oo == o and se in FIT]
        a0, b = S1.ols([r for r, _x in fit], [(x,) for _r, x in fit])
        b = b[0]
        test = [(wk, t, r, x) for se, wk, t, oo, r, x in units
                if oo == o and se == TEST_SEASON and wk in TEST_WEEKS and x > 0]
        d_book = [(r - a0 - b * x) ** 2 - (r - a0) ** 2 for _wk, _t, r, x in test]
        d_fc = []
        for se, wk, t, oo, r, _x in units:
            if oo != o or se != TEST_SEASON or wk not in TEST_WEEKS or (wk, t) not in fw:
                continue
            xf = max(0.0, fw[(wk, t)] - S1.WIND_FREE)
            if xf > 0:
                d_fc.append((r - a0 - b * xf) ** 2 - (r - a0) ** 2)
        book, fc = S1.stats(d_book), S1.stats(d_fc)
        readable = book["n"] >= MIN_UNITS and book["mean"] is not None
        ship = bool(readable and book["mean"] < -2 * book["se"] and fc["mean"] is not None and fc["mean"] < 0)
        res["outcomes"][o] = {"a": round(a0, 5), "b_wind_x": round(b, 5), "n_fit": len(fit),
                              "gamebook": book, "forecast": fc, "readable": readable, "ship": ship}
    with open(os.path.join(data, "nfl_weather_stage2.json"), "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps(res["outcomes"], indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
