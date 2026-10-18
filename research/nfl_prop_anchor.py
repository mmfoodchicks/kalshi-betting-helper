"""Should the DFS players' projections lean toward Kalshi's player props?
RESEARCH; the rule below was written 2026-10-18 13:21 UTC, before the first
logged week (2026 week 6, whose first game kicked off at 17:00 UTC that day)
had a single outcome.

The data is nfl_props_log's: for every game, the last pre-kickoff pass's
believable prop rungs, each ladder's implied mean, and Sleeper's projection
for every player on the two teams, all frozen five minutes before kickoff.
The outcomes are Sleeper's weekly box scores (the feed dfslog grades from).

THE RULE (written before any number below was computed)
  Units: one per (week, player, stat) for stat in pass_yd, rush_yd, rec_yd,
  rec and td, where the log holds both the market's implied mean m and
  Sleeper's projection s, and the player played (Sleeper's gp > 0; Kalshi
  voids the prop of a player who does not). For td, s = projected rushing +
  receiving touchdowns and the actual is rushing + receiving touchdowns (the
  anytime market's definition); m is the ladder's Poisson rate.
  Candidate: b = (1 - w) s + w m, w in {0, 0.25, 0.5, 0.75, 1}.
  Loss: the squared error (actual - b)^2, per stat.
  1. SELECT, per stat, on weeks 6 and 7: the w with the lowest mean loss.
  2. TEST, per stat, on weeks 8, 9 and 10, w frozen: the stat ships at w
     only if its mean loss beats w = 0 (Sleeper alone) by more than two
     standard errors of the per-unit difference; otherwise it ships at 0.
  3. Weeks after 10 judge nothing here; a shipped weight is re-judged only
     by a new rule written before the weeks it reads.
  What a shipped weight changes: before the DFS engine deals a game's box,
  each player's line for that stat moves to b, and the rest of his position
  group shares what is left of the team's budget (nfl_game_sim._anchor's
  construction), so team totals stay the drive model's.
  Reported beside it: each stat's mean loss for every w on both windows, the
  unit counts, and Sleeper's and the market's mean error (bias).

Usage: python3 -m research.nfl_prop_anchor --season 2026 [--store DIR]
  (reads the deep store's nfl_props_<season>_wNN and fetches Sleeper's box
  scores for those weeks). Writes research/data/nfl_prop_anchor.json.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

STATS = ("pass_yd", "rush_yd", "rec_yd", "rec", "td")
GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
SELECT_WEEKS = (6, 7)
TEST_WEEKS = (8, 9, 10)
_STATS_URL = "https://api.sleeper.com/stats/nfl/{season}/{week}?season_type=regular&position[]=QB&position[]=RB&position[]=WR&position[]=TE"


def projection(stat, sleeper_stats):
    st = sleeper_stats or {}
    if stat == "td":
        v = (st.get("rush_td") or 0.0) + (st.get("rec_td") or 0.0)
        return v if ("rush_td" in st or "rec_td" in st) else None
    return st.get(stat)


def actual(stat, box):
    if stat == "td":
        return float(box.get("rush_td") or 0.0) + float(box.get("rec_td") or 0.0)
    return float(box.get(stat) or 0.0)


def units(log, boxes):
    """[(stat, s, m, actual)] for one week's log and {norm name: box stats}."""
    out = []
    sl = log.get("sleeper") or {}
    for g in (log.get("games") or {}).values():
        for nm, p in (g.get("players") or {}).items():
            box = boxes.get(nm)
            if not box or not (box.get("gp") or 0) > 0:
                continue
            for stat in STATS:
                e = (p.get("stats") or {}).get(stat)
                if not e or e.get("mean") is None:
                    continue
                s = projection(stat, (sl.get(nm) or {}).get("stats"))
                if s is None:
                    continue
                out.append((stat, float(s), float(e["mean"]), actual(stat, box)))
    return out


def loss(us, w):
    return [(a - ((1 - w) * s + w * m)) ** 2 for _stat, s, m, a in us]


def paired(us, w):
    d = [x - y for x, y in zip(loss(us, w), loss(us, 0.0))]
    n = len(d)
    if n < 2:
        return {"n": n, "mean": None, "se": None}
    mu = sum(d) / n
    sd = (sum((x - mu) ** 2 for x in d) / (n - 1)) ** 0.5
    return {"n": n, "mean": round(mu, 4), "se": round(sd / n ** 0.5, 4)}


def decide(sel_units, test_units):
    """{stat: {"chosen", "test", "ship"}} by the rule above."""
    out = {}
    for stat in STATS:
        su = [u for u in sel_units if u[0] == stat]
        tu = [u for u in test_units if u[0] == stat]
        if not su:
            out[stat] = {"chosen": 0.0, "ship": 0.0, "why": "no selection units"}
            continue
        chosen = min(GRID, key=lambda w: (sum(loss(su, w)) / len(su), w))
        t = paired(tu, chosen) if chosen else {"n": len(tu), "mean": 0.0, "se": 0.0}
        ship = chosen if (chosen and t["mean"] is not None and t["mean"] < -2 * t["se"]) else 0.0
        out[stat] = {"chosen": chosen, "test": t, "ship": ship,
                     "select_mse": {str(w): round(sum(loss(su, w)) / len(su), 4) for w in GRID},
                     "test_mse": ({str(w): round(sum(loss(tu, w)) / len(tu), 4) for w in GRID} if tu else None)}
    return out


def _boxes(season, week):
    import kalshi_nfl
    import racing
    rows = racing._get_json(_STATS_URL.format(season=season, week=week), timeout=30) or []
    out = {}
    for r in rows:
        pl = r.get("player") or {}
        nm = kalshi_nfl._norm(f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip())
        if nm:
            out[nm] = r.get("stats") or {}
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    a = ap.parse_args(argv)
    import deep_cache
    import nfl_props_log
    by_week = {}
    for wk in SELECT_WEEKS + TEST_WEEKS:
        log, _ts = deep_cache.load(nfl_props_log.KEY.format(season=a.season, week=wk))
        if log:
            by_week[wk] = units(log, _boxes(a.season, wk))
    missing = [w for w in SELECT_WEEKS + TEST_WEEKS if w not in by_week]
    sel = [u for w in SELECT_WEEKS for u in by_week.get(w, [])]
    tst = [u for w in TEST_WEEKS for u in by_week.get(w, [])]
    res = {"rule": __doc__.split("Usage:")[0], "weeks_logged": sorted(by_week), "weeks_missing": missing,
           "n_select": len(sel), "n_test": len(tst),
           "decision": decide(sel, tst) if not missing else None}
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_prop_anchor.json")
    with open(out, "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: res[k] for k in ("weeks_logged", "weeks_missing", "n_select", "n_test")}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
