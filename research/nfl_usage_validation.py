"""Should the DFS deal lean on what each player has actually been getting?
RESEARCH; the rule below was written 2026-10-18 13:30 UTC, before any number
in this file was computed.

The DFS engine shares a team's targets and carries among its players by
Sleeper's projected lines (nfl_dfs_engine.deal_team: a receiver's base is his
projected targets, a rusher's his projected carries; the app's lineup sheet
reads the same lines). Sleeper's projections already look at usage, so the
question is whether a player's RECENT share of his team's targets and carries
-- the most-quoted usage signal there is, and the one nflverse publishes with
air yards beside it -- carries anything Sleeper's line does not.

THE RULE (written before any number below was computed)
  Data: research/data/player_weeks_*.csv.gz (Sleeper's weekly projection and
  box score per player; 2022-2025 and 2026 weeks 1-5) and nflverse's
  stats_player_week_<season>.csv (release "stats_player", for air yards).
  Regular season only.
  Units: one per (season, week, team) for TARGETS and one for CARRIES. The
  set is the team's players with a positive projection of that component
  (proj_rec_tgt, proj_rush_att) who played (act_gp > 0 -- active at kickoff;
  the engine's pool drops a player DraftKings lists OUT). A unit needs two
  players in the set and a positive actual total within it.
  Sleeper's share s_i = proj_i / sum of proj over the set.
  Trailing share r_i: the player's share of the TEAM's total over his last N
  games this season for this team before this week (games with act_gp > 0),
  as a ratio of sums. The team totals are every row of that team in that game
  (Sleeper's actuals). For WOPR candidates r_i is 1.5 x his target share +
  0.7 x his air-yards share over the same games, from nflverse (joined by
  season, week, team -- nflverse's LA is the Rams -- and kalshi_nfl._norm of
  the name). Floored at 0.005. A player with fewer than MIN_G (2) such games,
  or not joined, has no trailing share.
  Trailing allocation t: players with a trailing share split the total share
  Sleeper gives THEM in proportion to r; players without one keep s. So t
  sums to one and only redistributes among the players with a history.
  Candidate: q = (1 - w) s + w t.
  Candidates: targets -- TS3, TS6 (target share over 3 or 6 games), WOPR3,
  WOPR6; carries -- CS3, CS6. w in GRID.
  Loss per unit: the multinomial log loss of the realised counts,
  -sum_i a_i log q_i / sum_i a_i (the engine deals by a multinomial on q).
  1. SELECT on 2024, per component: the (candidate, w) with the lowest mean
     unit loss (ties to the smaller w).
  2. TEST on 2025, frozen: the paired per-unit difference against w = 0
     (Sleeper alone). The component passes only if the mean difference is
     below zero by more than two standard errors.
  3. CONFIRM on 2026 (weeks the table holds): the mean difference must not be
     above zero by more than two standard errors. Fewer than 50 units reads as
     unreadable and does not block.
  2025 is a fair test here: the engine's 2025 test measured lineups and
  points, nothing in the stack has read a trailing share, and no candidate's
  parameter is chosen anywhere but 2024.
  What a passing component changes: before nfl_recon reconciles a team, each
  player's projected count for that component (targets, or carries) moves to
  q_i x the set's projected total, and the components that hang off it move
  by the same factor (receptions, receiving yards and touchdowns for
  targets -- his catch rate, yards per catch and touchdowns per catch stay
  Sleeper's; rushing yards and touchdowns for carries). Team totals stay
  Sleeper's, so the engine's anchors do not move; only who gets the ball.
  Production reads the trailing games from Sleeper's weekly box scores
  (the feed dfslog grades from) and, for WOPR, nflverse's season file.
  Reported beside it: the mean loss for every candidate and w on all three
  years, the unit counts, and the nflverse join rate.

Usage: python3 -m research.nfl_usage_validation --nflverse DIR
  (DIR holds stats_player_week_<season>.csv for 2024-2026). Writes
  research/data/nfl_usage_validation.json.
"""
import argparse
import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

GRID = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0)
MIN_G = 2
FLOOR = 0.005
SELECT, TEST, CONFIRM = 2024, 2025, 2026
CANDIDATES = {"tgt": (("TS", 3), ("TS", 6), ("WOPR", 3), ("WOPR", 6)),
              "car": (("CS", 3), ("CS", 6))}
_COMP = {"tgt": "rec_tgt", "car": "rush_att"}
_NFLVERSE_TEAM = {"LA": "LAR"}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def team_games(rows):
    """{(season, week, team): [row, ...]} over the regular season rows."""
    out = {}
    for r in rows:
        out.setdefault((int(r["season"]), int(r["week"]), r["team"]), []).append(r)
    return out


def history(tg):
    """Per (season, team): {week: {"tot": {comp: team total}, "pl": {player_id:
    {comp: count}}}} for the games each player played (act_gp > 0)."""
    h = {}
    for (season, week, team), rs in tg.items():
        tot = {c: sum(_f(r.get(f"act_{c}")) for r in rs) for c in _COMP.values()}
        pl = {r["player_id"]: {c: _f(r.get(f"act_{c}")) for c in _COMP.values()}
              for r in rs if _f(r.get("act_gp")) > 0}
        h.setdefault((season, team), {})[week] = {"tot": tot, "pl": pl}
    return h


def nflverse_usage(path_by_season):
    """{(season, week, team, norm name): (targets, air yards)} and the team-game
    totals {(season, week, team): (targets, air yards)} from nflverse."""
    import kalshi_nfl
    pl, tot = {}, {}
    for season, path in path_by_season.items():
        if not path or not os.path.exists(path):
            continue
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("season_type") != "REG":
                    continue
                team = _NFLVERSE_TEAM.get(r["team"], r["team"])
                k = (int(r["season"]), int(r["week"]), team)
                tg, ay = _f(r.get("targets")), _f(r.get("receiving_air_yards"))
                nm = kalshi_nfl._norm(r.get("player_display_name") or r.get("player_name") or "")
                pl[k + (nm,)] = (tg, ay)
                t = tot.get(k, (0.0, 0.0))
                tot[k] = (t[0] + tg, t[1] + ay)
    return pl, tot


def trailing(kind, n, season, week, team, row, h, nv=None):
    """The player's trailing share r (floored), or None without MIN_G games."""
    import kalshi_nfl
    hist = h.get((season, team)) or {}
    pid = row["player_id"]
    weeks = sorted((w for w, g in hist.items() if w < week and pid in g["pl"]), reverse=True)[:n]
    if len(weeks) < MIN_G:
        return None
    if kind in ("TS", "CS"):
        c = "rec_tgt" if kind == "TS" else "rush_att"
        num = sum(hist[w]["pl"][pid][c] for w in weeks)
        den = sum(hist[w]["tot"][c] for w in weeks)
        return max(FLOOR, num / den) if den > 0 else None
    pl, tot = nv
    nm = kalshi_nfl._norm(row["name"])
    got = [(pl.get((season, w, team, nm)), tot.get((season, w, team))) for w in weeks]
    if any(p is None or t is None for p, t in got):
        return None
    tg = sum(p[0] for p, _t in got) / max(sum(t[0] for _p, t in got), 1e-9)
    ay_den = sum(t[1] for _p, t in got)
    ay = sum(p[1] for p, _t in got) / ay_den if ay_den > 0 else 0.0
    return max(FLOOR, 1.5 * tg + 0.7 * ay)


def unit(comp, rs):
    """(set rows, s, actual counts) for one team-game, or None."""
    c = _COMP[comp]
    st = [r for r in rs if _f(r.get(f"proj_{c}")) > 0 and _f(r.get("act_gp")) > 0]
    if len(st) < 2:
        return None
    a = [_f(r.get(f"act_{c}")) for r in st]
    if sum(a) <= 0:
        return None
    p = [_f(r.get(f"proj_{c}")) for r in st]
    s = [x / sum(p) for x in p]
    return st, s, a


def allocation(s, r):
    """t: the players with a trailing share split Sleeper's share of them by r."""
    have = [i for i, x in enumerate(r) if x is not None]
    if not have:
        return list(s)
    mass = sum(s[i] for i in have)
    rs = sum(r[i] for i in have)
    t = list(s)
    for i in have:
        t[i] = mass * r[i] / rs
    return t


def unit_loss(q, a):
    return -sum(ai * math.log(qi) for qi, ai in zip(q, a) if ai > 0) / sum(a)


def evaluate(tg, h, nv, season, comp):
    """{(kind, n): {w: [loss per unit]}} for one season and component, plus the
    unit count and, for WOPR, the joined share of trailing players."""
    out = {cand: {w: [] for w in GRID} for cand in CANDIDATES[comp]}
    joins = {"tried": 0, "joined": 0}
    for (se, week, team), rs in sorted(tg.items()):
        if se != season:
            continue
        u = unit(comp, rs)
        if u is None:
            continue
        st, s, a = u
        for kind, n in CANDIDATES[comp]:
            r = [trailing(kind, n, se, week, team, row, h, nv) for row in st]
            if kind == "WOPR":
                base = [trailing("TS", n, se, week, team, row, h) for row in st]
                joins["tried"] += sum(1 for x in base if x is not None)
                joins["joined"] += sum(1 for x, b in zip(r, base) if x is not None and b is not None)
            t = allocation(s, r)
            for w in GRID:
                q = [(1 - w) * si + w * ti for si, ti in zip(s, t)]
                out[(kind, n)][w].append(unit_loss(q, a))
    return out, joins


def paired(cand_losses, w):
    d = [x - y for x, y in zip(cand_losses[w], cand_losses[0.0])]
    n = len(d)
    if n < 2:
        return {"n": n, "mean": None, "se": None}
    mu = sum(d) / n
    sd = (sum((x - mu) ** 2 for x in d) / (n - 1)) ** 0.5
    return {"n": n, "mean": round(mu, 6), "se": round(sd / n ** 0.5, 6)}


def decide(sel, tst, cnf):
    """The rule above, for one component's three evaluations."""
    def mean(v):
        return sum(v) / len(v) if v else float("inf")
    best = min(((cand, w) for cand in sel for w in GRID), key=lambda cw: (mean(sel[cw[0]][cw[1]]), cw[1]))
    cand, w = best
    test = paired(tst[cand], w) if w else {"n": len(tst[cand][0.0]), "mean": 0.0, "se": 0.0}
    conf = paired(cnf[cand], w) if w else {"n": len(cnf[cand][0.0]), "mean": 0.0, "se": 0.0}
    passed = bool(w and test["mean"] is not None and test["mean"] < -2 * test["se"])
    readable = conf["n"] >= 50 and conf["mean"] is not None
    vetoed = bool(readable and conf["mean"] > 2 * conf["se"])
    return {"chosen": {"kind": cand[0], "n": cand[1], "w": w}, "test": test, "confirm": conf,
            "test_passed": passed, "confirm_readable": readable, "confirm_vetoed": vetoed,
            "ship": w if (passed and not vetoed) else 0.0}


def _table(ev):
    return {f"{k}{n}": {f"{w:g}": round(sum(v) / len(v), 6) if v else None for w, v in ws.items()}
            for (k, n), ws in ev.items()}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--nflverse", required=True)
    a = ap.parse_args(argv)
    from research import hist_data as H
    data = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    rows = H.read_csv_gz(os.path.join(data, "player_weeks_2022_2025.csv.gz"))
    rows += H.read_csv_gz(os.path.join(data, "player_weeks_2026.csv.gz"))
    rows = [r for r in rows if r["season"] in (SELECT, TEST, CONFIRM)]
    tg = team_games(rows)
    h = history(tg)
    nv = nflverse_usage({s: os.path.join(a.nflverse, f"stats_player_week_{s}.csv") for s in (SELECT, TEST, CONFIRM)})
    res = {"rule": __doc__.split("Usage:")[0], "components": {}}
    for comp in CANDIDATES:
        evs = {}
        joins = {}
        for season in (SELECT, TEST, CONFIRM):
            evs[season], joins[season] = evaluate(tg, h, nv, season, comp)
        res["components"][comp] = {
            "decision": decide(evs[SELECT], evs[TEST], evs[CONFIRM]),
            "units": {str(s): len(next(iter(evs[s].values()))[0.0]) for s in evs},
            "mean_loss": {str(s): _table(evs[s]) for s in evs},
            "wopr_join": {str(s): joins[s] for s in joins} if comp == "tgt" else None}
    out = os.path.join(data, "nfl_usage_validation.json")
    with open(out, "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({c: v["decision"] for c, v in res["components"].items()}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
