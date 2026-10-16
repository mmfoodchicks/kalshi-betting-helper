"""The NHL deep engine against the production slate model, walk-forward and
OUT OF SAMPLE. RESEARCH.

For a season S every regular-season game is priced the way the board would
have priced it that morning:

  deep        nhl_deep_sim on tables refit on the seasons BEFORE S (2022-23
              onward), each team's profile from its S games before the date
              (nhl_deep_data, shrunk by the measured K) with last season's
              final profile as the prior, N games a matchup
  production  hockey.simulate_game -- the board's model -- on the standings it
              would have read: goals for / against per game to date, regressed
              by its own _REGRESS_G, at its own home edge
  home rate   the training seasons' home win rate, for scale

and scored against what happened: log loss and Brier on the winner (every
game has one: OT and shootouts included), the total goals' ranked
probability score over 0..12+, and calibration by decile.

THE RULE, written 2026-10-16 before any deep run on a scored season:
  The headline blends the deep engine into the production probability at a
  weight w on the grid 0 / 0.15 / 0.3 / 0.5 / 0.7 / 1, chosen on 2024-25 by
  log loss. It ships on the board only if, on 2025-26 at that frozen w, the
  blend's log loss is below production's (the point estimate decides; the
  paired standard error is reported beside it). w = 0 means the deep engine
  is shown and shadow-graded but not blended. Nothing is tuned on 2025-26.

  python3 -m research.nhl_deep_validate <store dir> --season 2024 [--n 300] [--workers 4]
      [--out research/data/nhl_deep_validation_2024.json]
"""
import json
import math
import multiprocessing as mp
import os
import random
import sys
import time
import zlib

GRID = (0.0, 0.15, 0.3, 0.5, 0.7, 1.0)
BASE_SEED = 20261016
MAX_GOALS = 12

_W = {}


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _init(store_dir, season, fit_seasons, n):
    from research import nhl_deep_fit as F
    import nhl_deep_sim as E
    stores = F.load_stores(store_dir, fit_seasons)
    raw = F.fit(stores)
    raw["dist_edges"] = list(F.DIST_EDGES)
    _W["T"] = E.Tables(raw)
    _W["n"] = n


def _price(job):
    import nhl_deep_sim as E
    gid, hp, ap = job
    rng = random.Random(zlib.crc32(f"{BASE_SEED}|{gid}".encode()))
    hw, tot = 0, [0] * (MAX_GOALS + 1)
    for _ in range(_W["n"]):
        g = E.play_game(hp, ap, rng, _W["T"])
        hw += g["winner"] == 0
        tot[min(MAX_GOALS, g["score"][0] + g["score"][1])] += 1
    return gid, hw / _W["n"], [x / _W["n"] for x in tot]


def _rps(dist, y):
    """Ranked probability score of a total-goals distribution against y."""
    cp = co = 0.0
    s = 0.0
    for k in range(MAX_GOALS + 1):
        cp += dist[k]
        co += 1.0 if y <= k else 0.0
        s += (cp - co) ** 2
    return s / MAX_GOALS


def _prod_model(hockey, acc, lg_g, home, away, gid):
    """hockey.simulate_game on the standings to date (what the board reads)."""
    def rt(ab):
        a = acc.get(ab)
        g = a["g"] if a else 0
        gf = (a["gf"] / g) if g else lg_g
        ga = (a["ga"] / g) if g else lg_g
        w = g / (g + hockey._REGRESS_G)
        return {"abbr": ab, "gf": w * gf + (1 - w) * lg_g, "ga": w * ga + (1 - w) * lg_g, "lg_g": lg_g}
    sim = hockey.simulate_game(rt(home), rt(away), [], [], n=2000, seed=zlib.crc32(f"prod|{gid}".encode()))
    tot = [0] * (MAX_GOALS + 1)
    for t in sim["_totals"]:
        tot[min(MAX_GOALS, t)] += 1
    return sim["p_home"], [x / len(sim["_totals"]) for x in tot]


def run(store_dir, season, n=300, workers=4, log=print):
    from research import nhl_deep_fit as F
    import nhl_deep_data as D
    import nhl_deep_sim as E
    import hockey
    fit_seasons = [s for s in (2022, 2023, 2024, 2025) if s < season]
    stores = F.load_stores(store_dir, fit_seasons)
    raw = F.fit(stores)
    raw["dist_edges"] = list(F.DIST_EDGES)
    T = E.Tables(raw)
    lg_g = raw["goals"] / raw["games"] / 2.0
    home_rate = raw["home_wins"] / raw["games"]
    # last season's final profiles: the prior
    prev = dict(F.load_stores(store_dir, [season - 1]))
    priors = {}
    if prev:
        acc_prev = D.season_units(list(prev[season - 1]["games"].values()), T, F)
        priors = D.profiles(acc_prev, T)
    cur = dict(F.load_stores(store_dir, [season]))[season]
    games = sorted((g for g in cur["games"].values() if g.get("gtype") == 2), key=lambda g: (g["date"] or "", g["id"]))
    acc, prod_acc = {}, {}
    rows = []
    t0 = time.time()
    with mp.Pool(workers, initializer=_init, initargs=(store_dir, season, fit_seasons, n)) as pool:
        i = 0
        while i < len(games):
            day = games[i]["date"]
            todays = []
            while i < len(games) and games[i]["date"] == day:
                todays.append(games[i])
                i += 1
            prof = D.profiles(acc, T, priors)
            jobs = []
            for g in todays:
                hp = prof.get(g["home"]) or dict(E.league_profile(g["home"]))
                ap = prof.get(g["away"]) or dict(E.league_profile(g["away"]))
                jobs.append((g["id"], hp, ap))
            deep = {gid: (p, dist) for gid, p, dist in pool.map(_price, jobs)}
            for g in todays:
                pp, pdist = _prod_model(hockey, prod_acc, lg_g, g["home"], g["away"], g["id"])
                dp, ddist = deep[g["id"]]
                y = 1 if g["home_score"] > g["away_score"] else 0
                rows.append({"id": g["id"], "date": day, "home": g["home"], "away": g["away"], "y": y,
                             "total": g["home_score"] + g["away_score"], "last": g["last"],
                             "deep": dp, "prod": pp, "deep_tot": ddist, "prod_tot": pdist,
                             "h_games": prof.get(g["home"], {}).get("games", 0)})
            # the day's games enter the accumulators only after every game of
            # the day is priced: nothing priced that morning sees that night
            for g in todays:
                U = D.game_units(g, T, F)
                for s, ab in ((0, g["home"]), (1, g["away"])):
                    D.add(acc.setdefault(ab, {}), U[s])
                for ab, gf, ga in ((g["home"], g["home_score"], g["away_score"]), (g["away"], g["away_score"], g["home_score"])):
                    a = prod_acc.setdefault(ab, {"g": 0, "gf": 0, "ga": 0})
                    # the standings count a shootout winner's extra goal, as ESPN's do
                    a["g"] += 1
                    a["gf"] += gf
                    a["ga"] += ga
            if log and len(rows) % 200 < len(todays):
                log(f"[nhl-val] {season}: {len(rows)} games ({time.time() - t0:.0f}s)")
    return rows, {"season": season, "fit_seasons": fit_seasons, "n": n, "home_rate": home_rate, "lg_g": lg_g,
                  "seconds": round(time.time() - t0, 1), "base_seed": BASE_SEED}


def _ll(p, y):
    p = min(1 - 1e-6, max(1e-6, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def score(rows, meta, w=None):
    """Log loss / Brier per arm and per blend weight, the paired SE of each
    blend against production, totals RPS, calibration deciles."""
    n = len(rows)
    out = {"n_games": n}
    arms = {"deep": [r["deep"] for r in rows], "prod": [r["prod"] for r in rows],
            "home_rate": [meta["home_rate"]] * n}
    ys = [r["y"] for r in rows]
    for k, ps in arms.items():
        out[k] = {"logloss": round(sum(_ll(p, y) for p, y in zip(ps, ys)) / n, 5),
                  "brier": round(sum((p - y) ** 2 for p, y in zip(ps, ys)) / n, 5)}
    blends = {}
    for wt in GRID:
        ll = [_ll(wt * r["deep"] + (1 - wt) * r["prod"], r["y"]) - _ll(r["prod"], r["y"]) for r in rows]
        mean = sum(ll) / n
        sd = (sum((x - mean) ** 2 for x in ll) / (n - 1)) ** 0.5
        blends[f"{wt:g}"] = {"logloss": round(sum(_ll(wt * r["deep"] + (1 - wt) * r["prod"], r["y"]) for r in rows) / n, 5),
                             "minus_prod": round(mean, 5), "se": round(sd / n ** 0.5, 5)}
    out["blends"] = blends
    out["totals_rps"] = {k: round(sum(_rps(r[f"{k}_tot"], r["total"]) for r in rows) / n, 5) for k in ("deep", "prod")}
    out["mean_total"] = {"real": round(sum(r["total"] for r in rows) / n, 3),
                         "deep": round(sum(sum(i * p for i, p in enumerate(r["deep_tot"])) for r in rows) / n, 3),
                         "prod": round(sum(sum(i * p for i, p in enumerate(r["prod_tot"])) for r in rows) / n, 3)}
    cal = {}
    for k in ("deep", "prod"):
        b = [[0, 0.0, 0] for _ in range(10)]
        for r in rows:
            i = min(9, int(r[k] * 10))
            b[i][0] += 1
            b[i][1] += r[k]
            b[i][2] += r["y"]
        cal[k] = [(c, round(s / c, 3), round(yy / c, 3)) for c, s, yy in b if c]
    out["calibration"] = cal
    return out


def choose_w(sc):
    best = min(GRID, key=lambda wt: (sc["blends"][f"{wt:g}"]["logloss"], wt))
    return best


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("store")
    ap.add_argument("--season", type=int, default=2024)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.season >= 2025:
        # the test season may only be read by committed code
        from research import provenance
        provenance.require_clean("the NHL deep test")
    rows, meta = run(a.store, a.season, a.n, a.workers)
    sc = score(rows, meta)
    sc["meta"] = meta
    sc["chosen_w"] = choose_w(sc)
    from research import provenance
    sc["meta"]["provenance"] = provenance.stamp(seed=BASE_SEED, model="nhl_deep_sim",
                                                data_split=f"{season_label(a.season)} held out; tables {meta['fit_seasons']}")
    if a.out:
        # the rows kept compact: the per-game figures the scores are made of
        keep = [{k: r[k] for k in ("id", "date", "home", "away", "y", "total", "last", "deep", "prod")} for r in rows]
        with open(a.out, "w") as fh:
            json.dump({"score": sc, "rows": keep}, fh, sort_keys=True)
    return sc


def season_label(s):
    return f"{s}-{(s + 1) % 100:02d}"


if __name__ == "__main__":
    s = main(sys.argv[1:])
    print(json.dumps({k: v for k, v in s.items() if k != "calibration"}, indent=1))
