"""Last season as the prior for the NBA and NHL board ratings: chosen on
2024-25, tested on 2025-26 -- every game priced the way the board would have
priced it that morning. RESEARCH.

The boards' production models (basket.ratings, hockey.ratings) rate each
team from this season's points (goals) for and against per game, regressed
toward the LEAGUE AVERAGE by regress_g (NBA 12) / _REGRESS_G (NHL 10) games.
On opening night every team is average; a week in, a team that won the title
and a team that won 20 games are still most of the way to the same rating.
The candidate replaces the league average with LAST SEASON'S final rating:

  prior = league_now * (1 + rho * (last_season / league_last_season - 1))
  rating = (g * this_season + K * prior) / (g + K)

for offence, defence and (NBA) pace, rho the share of last season's edge
carried over (rosters change), K the games the prior is worth. rho = 0 and
K = the shipped constant IS production, to the digit: the harness's
`variant` with those values is the harnesses' _prod_model
(research/nba_deep_validate.py, research/nhl_deep_validate.py).

THE RULE (written before any number below was computed)
  Grid: K in {R, 2R, 3R, 5R} (R the shipped constant), rho in {0, 0.4, 0.6,
  0.8}: 16 variants, production among them.
  1. Select on 2024-25 by mean log loss on the home side's win over every
     regular-season game.
  2. Ship on 2025-26 only if the chosen variant's log loss beats
     production's by more than two standard errors of the per-game
     difference. Otherwise production stays.
  Each league is decided on its own. The NHL board blends production with
  the deep engine at 0.5 (hockey._DEEP_WEIGHT); that weight was chosen with
  production as it is and is NOT re-chosen here -- this tests production's
  own probability, the piece being changed.

Same-day games never see each other: a day's results enter the
accumulators only after every game of the day is priced. Each game's
simulation draws from one seed for every variant (common random numbers),
so differences between variants are differences between ratings.

Usage: python3 -m research.pro_prior_validation <league nba|nhl> <store dir>
         [--jobs N] [--out research/data/pro_prior_<league>.json]
"""
import json
import math
import multiprocessing as mp
import os
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SELECT, TEST = 2024, 2025
RHO = (0.0, 0.4, 0.6, 0.8)
K_MULT = (1, 2, 3, 5)
# Arizona's franchise moved to Utah for 2024-25: the same team's prior
_RENAMED = {"nhl": {"ARI": "UTA"}, "nba": {}}


def _lg_name(league):
    return "nba" if league == "nba" else "nhl"


def _load(league, store_dir, season):
    if league == "nba":
        from research import nba_deep_fit as F
    else:
        from research import nhl_deep_fit as F
    got = dict(F.load_stores(store_dir, [season]))
    return got.get(season)


def _games(league, store):
    if league == "nba":
        import nba_pbp as P
        from research.nba_deep_validate import et_day
        gs = [g for g in store["games"].values() if P.league_game(g)]
        key = (lambda g: (et_day(g["date"]), g["id"]))
        day = (lambda g: et_day(g["date"]))
    else:
        # the league's feed stamps the local game date already ("2024-10-08"),
        # as research.nhl_deep_validate groups its days
        gs = [g for g in store["games"].values() if g.get("gtype") == 2]
        key = (lambda g: (g.get("date") or "", g["id"]))
        day = (lambda g: g.get("date") or "")
    return sorted(gs, key=key), day


def _poss(g):
    from research.nba_deep_validate import box_poss
    return box_poss(g)


def final_ratings(league, store):
    """Last season's per-team totals: NBA {g, pf, pa, poss}, NHL {g, gf, ga}."""
    games, _day = _games(league, store)
    acc = {}
    for g in games:
        if league == "nba":
            bp = _poss(g)
            for s_, ab, pf, pa in ((0, g["home"], g["home_score"], g["away_score"]),
                                   (1, g["away"], g["away_score"], g["home_score"])):
                a = acc.setdefault(ab, {"g": 0, "pf": 0, "pa": 0, "poss": 0.0})
                a["g"] += 1; a["pf"] += pf; a["pa"] += pa; a["poss"] += bp[s_]
        else:
            for ab, gf, ga in ((g["home"], g["home_score"], g["away_score"]),
                               (g["away"], g["away_score"], g["home_score"])):
                a = acc.setdefault(ab, {"g": 0, "gf": 0, "ga": 0})
                a["g"] += 1; a["gf"] += gf; a["ga"] += ga
    return acc


def prior_table(league, prev):
    """{abbr: relative strengths} from last season's totals: NBA (off, def,
    pace) as ratios to last season's league, NHL (gf, ga) the same."""
    if not prev:
        return {}
    ren = _RENAMED[_lg_name(league)]
    out = {}
    if league == "nba":
        lg_ppp = sum(a["pf"] for a in prev.values()) / sum(a["poss"] for a in prev.values())
        lg_pace = sum(a["poss"] / a["g"] for a in prev.values()) / len(prev)
        for ab, a in prev.items():
            out[ren.get(ab, ab)] = {"off": (a["pf"] / a["poss"]) / lg_ppp, "def": (a["pa"] / a["poss"]) / lg_ppp,
                                    "pace": (a["poss"] / a["g"]) / lg_pace}
    else:
        lg = sum(a["gf"] for a in prev.values()) / sum(a["g"] for a in prev.values())
        for ab, a in prev.items():
            out[ren.get(ab, ab)] = {"gf": (a["gf"] / a["g"]) / lg, "ga": (a["ga"] / a["g"]) / lg}
    return out


def variant_ratings(league, acc, prior, K, rho, cfg):
    """{abbr: rating dict} exactly as production builds it, with the league
    average replaced by the prior (rho = 0 leaves it the league average)."""
    if league == "nba":
        played = {ab: a for ab, a in acc.items() if a["g"]}

        def pace_of(a):
            return max(cfg["lg_pace"] - 12, min(cfg["lg_pace"] + 12, a["poss"] / a["g"]))
        if played:
            lg_pf = sum(a["pf"] / a["g"] for a in played.values()) / len(played)
            lg_pace = sum(pace_of(a) for a in played.values()) / len(played)
            lg_ppp = lg_pf / lg_pace
        else:
            lg_pace, lg_ppp = cfg["lg_pace"], cfg["lg_ppp"]

        def rt(ab):
            pr = prior.get(ab) or {"off": 1.0, "def": 1.0, "pace": 1.0}
            p_off = lg_ppp * (1 + rho * (pr["off"] - 1))
            p_def = lg_ppp * (1 + rho * (pr["def"] - 1))
            p_pace = lg_pace * (1 + rho * (pr["pace"] - 1))
            a = played.get(ab)
            if not a:
                return {"abbr": ab, "pace": p_pace if rho else lg_pace, "off_ppp": p_off, "def_ppp": p_def,
                        "lg_ppp": lg_ppp, "lg_pace": lg_pace, "w": 0, "l": 0}
            pace = pace_of(a)
            w = a["g"] / (a["g"] + K)
            return {"abbr": ab,
                    # production's pace is the team's own (clamped), unregressed;
                    # with a prior it is regressed like the rest
                    "pace": (w * pace + (1 - w) * p_pace) if rho else pace,
                    "off_ppp": w * (a["pf"] / a["g"] / pace) + (1 - w) * p_off,
                    "def_ppp": w * (a["pa"] / a["g"] / pace) + (1 - w) * p_def,
                    "lg_ppp": lg_ppp, "lg_pace": lg_pace, "w": 0, "l": 0}
        return rt
    lg_g = cfg["lg_g"]

    def rt(ab):
        pr = prior.get(ab) or {"gf": 1.0, "ga": 1.0}
        p_gf = lg_g * (1 + rho * (pr["gf"] - 1))
        p_ga = lg_g * (1 + rho * (pr["ga"] - 1))
        a = acc.get(ab)
        g = a["g"] if a else 0
        gf = (a["gf"] / g) if g else lg_g
        ga = (a["ga"] / g) if g else lg_g
        w = g / (g + K)
        return {"abbr": ab, "gf": w * gf + (1 - w) * p_gf, "ga": w * ga + (1 - w) * p_ga, "lg_g": lg_g}
    return rt


_W = {}


def _init(league):
    if league == "nba":
        import basket
        _W["mod"], _W["cfg"] = basket, basket.LEAGUES["nba"]
    else:
        import hockey
        _W["mod"], _W["cfg"] = hockey, None
    _W["league"] = league


def _price(job):
    """(gid, [p_home per variant]) for one game, one seed for every variant."""
    gid, pairs = job
    seed = zlib.crc32(f"prod|{gid}".encode())
    out = []
    for rh, ra in pairs:
        if _W["league"] == "nba":
            sim = _W["mod"].simulate_game("nba", rh, ra, [], [], n=2000, seed=seed)
        else:
            sim = _W["mod"].simulate_game(rh, ra, [], [], n=2000, seed=seed)
        out.append(sim["p_home"])
    return gid, out


def run(league, store_dir, season, jobs=4, log=print):
    import basket
    import hockey
    cur = _load(league, store_dir, season)
    prev = _load(league, store_dir, season - 1)
    prior = prior_table(league, final_ratings(league, prev) if prev else {})
    games, day_of = _games(league, cur)
    if league == "nba":
        cfg, R = basket.LEAGUES["nba"], basket.LEAGUES["nba"]["regress_g"]
    else:
        # the NHL league level: the harness's (the fit seasons' goals per team-game)
        from research import nhl_deep_fit as F
        raw = F.fit(F.load_stores(store_dir, [s for s in (2022, 2023, 2024, 2025) if s < season]))
        cfg, R = {"lg_g": raw["goals"] / raw["games"] / 2.0}, hockey._REGRESS_G
    variants = [(K * R, rho) for K in K_MULT for rho in RHO]
    acc = {}
    rows = []
    t0 = time.time()
    with mp.get_context("fork").Pool(jobs, initializer=_init, initargs=(league,)) as pool:
        i = 0
        while i < len(games):
            d = day_of(games[i])
            todays = []
            while i < len(games) and day_of(games[i]) == d:
                todays.append(games[i]); i += 1
            rts = [variant_ratings(league, acc, prior, K, rho, cfg) for K, rho in variants]
            work = [(g["id"], [(rt(g["home"]), rt(g["away"])) for rt in rts]) for g in todays]
            got = dict(pool.map(_price, work))
            for g in todays:
                rows.append({"id": g["id"], "date": d, "home": g["home"], "away": g["away"],
                             "y": 1 if g["home_score"] > g["away_score"] else 0,
                             "hg": (acc.get(g["home"]) or {}).get("g", 0), "ag": (acc.get(g["away"]) or {}).get("g", 0),
                             "p": [round(x, 5) for x in got[g["id"]]]})
            for g in todays:
                if league == "nba":
                    bp = _poss(g)
                    for s_, ab, pf, pa in ((0, g["home"], g["home_score"], g["away_score"]),
                                           (1, g["away"], g["away_score"], g["home_score"])):
                        a = acc.setdefault(ab, {"g": 0, "pf": 0, "pa": 0, "poss": 0.0})
                        a["g"] += 1; a["pf"] += pf; a["pa"] += pa; a["poss"] += bp[s_]
                else:
                    for ab, gf, ga in ((g["home"], g["home_score"], g["away_score"]),
                                       (g["away"], g["away_score"], g["home_score"])):
                        a = acc.setdefault(ab, {"g": 0, "gf": 0, "ga": 0})
                        a["g"] += 1; a["gf"] += gf; a["ga"] += ga
            if log and len(rows) % 300 < len(todays):
                log(f"[prior-{league}] {season}: {len(rows)} games ({time.time() - t0:.0f}s)")
    return rows, variants, {"season": season, "R": R, "prior_teams": len(prior), "seconds": round(time.time() - t0)}


def _ll(p, y):
    p = min(1 - 1e-6, max(1e-6, p))
    return -math.log(p if y else 1 - p)


def score(rows, variants, prod_ix):
    out = []
    n = len(rows)
    for j, (K, rho) in enumerate(variants):
        d = [_ll(r["p"][j], r["y"]) - _ll(r["p"][prod_ix], r["y"]) for r in rows]
        m = sum(d) / n
        sd = (sum((x - m) ** 2 for x in d) / max(1, n - 1)) ** 0.5
        early = [r for r in rows if min(r["hg"], r["ag"]) < 15]
        out.append({"K": K, "rho": rho, "logloss": round(sum(_ll(r["p"][j], r["y"]) for r in rows) / n, 5),
                    "minus_prod": round(m, 5), "se": round(sd / n ** 0.5, 5),
                    "early_n": len(early),
                    "early_logloss": round(sum(_ll(r["p"][j], r["y"]) for r in early) / max(1, len(early)), 5)})
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("league", choices=("nba", "nhl"))
    ap.add_argument("store")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out_path = a.out or os.path.join(ROOT, "research", "data", f"pro_prior_{a.league}.json")
    res = {"rule": __doc__.split("Usage:")[0], "league": a.league}
    sel_rows, variants, m1 = run(a.league, a.store, SELECT, a.jobs)
    prod_ix = variants.index((m1["R"], 0.0))
    sel = score(sel_rows, variants, prod_ix)
    best = min(range(len(variants)), key=lambda j: sel[j]["logloss"])
    print(f"[{a.league}] chosen on {SELECT}-{SELECT % 100 + 1}: K={variants[best][0]:g} rho={variants[best][1]} "
          f"({sel[best]['logloss']:.5f} vs production {sel[prod_ix]['logloss']:.5f})", flush=True)
    test_rows, variants2, m2 = run(a.league, a.store, TEST, a.jobs)
    test = score(test_rows, variants2, prod_ix)
    t = test[best]
    ship = t["minus_prod"] < -2 * t["se"] and best != prod_ix
    res.update({"select": {"season": SELECT, "meta": m1, "variants": sel}, "chosen": {"K": variants[best][0], "rho": variants[best][1]},
                "test": {"season": TEST, "meta": m2, "variants": test, "chosen": t, "production": test[prod_ix]},
                "ship": ship})
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(res, open(out_path, "w"), indent=1)
    print(f"[{a.league}] {TEST}-{TEST % 100 + 1}: chosen {t['logloss']:.5f} vs production {test[prod_ix]['logloss']:.5f} "
          f"({t['minus_prod']:+.5f} +/- {t['se']:.5f}); early games {t['early_logloss']:.5f} vs "
          f"{test[prod_ix]['early_logloss']:.5f} (n {t['early_n']}); ship: {ship}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
