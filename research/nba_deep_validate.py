"""The NBA deep engine against the production slate model, walk-forward and
OUT OF SAMPLE. RESEARCH. The NHL harness's twin (research/nhl_deep_validate.py).

For a season S every regular-season game is priced the way the board would
have priced it that morning:

  deep        nba_deep_sim on tables refit on the seasons BEFORE S, each
              team's levers from its S games before the date (nba_deep_data,
              shrunk by K, last season's final units as the prior, raised to
              the engine's lever exponent), N games a matchup
  production  basket.simulate_game -- the board's model -- on what its ratings
              would have read: points for / against and possessions per game
              to date, regressed by its own regress_g, at its own home edge
  home rate   the training seasons' home win rate

scored by log loss and Brier on the winner, the total points' ranked
probability score in 5-point bins, and calibration.

THE RULE, written 2026-10-16 before any deep run on a scored season (the
NHL rule, unchanged):
  The headline blends the deep engine into the production probability at a
  weight w on the grid 0 / 0.15 / 0.3 / 0.5 / 0.7 / 1, chosen on 2024-25 by
  log loss. It ships on the board only if, on 2025-26 at that frozen w, the
  blend's log loss is below production's (the point estimate decides; the
  paired standard error is reported beside it). w = 0 means the deep engine
  is shown and shadow-graded but not blended. Nothing is tuned on 2025-26.

What was set before this harness ran, and on what: the engine's league-level
constants -- home court (nba_deep_sim._HFA_MAKE, matched to 2024-25's league
home edge) and the lever exponent (nba_deep_data.LEVER_EXP, measured on
2023-24) -- carry no team's information; the shrinkage K and the prior's
persistence are round values set before anything was measured. 2025-26 was
read by none of them.

  python3 -m research.nba_deep_validate <store dir> --season 2024 --out research/data/nba_deep_validation_2024.json
  python3 -m research.nba_deep_validate <store dir> --season 2025 --out research/data/nba_deep_validation_2025.json
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
TOT_LO, TOT_HI, TOT_W = 170, 290, 5          # totals binned 170..290 by 5

_W = {}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _init(store_dir, fit_seasons, n):
    from research import nba_deep_fit as F
    import nba_deep_sim as E
    stores = F.load_stores(store_dir, fit_seasons)
    raw = F.fit(stores)
    raw["dur_max"] = F.DUR_MAX
    raw["score_slope"] = F.score_slopes(stores)
    _W["T"] = E.Tables(raw)
    _W["n"] = n


def et_day(iso):
    """The game's ESPN start (UTC, '2024-10-23T23:30Z') as its Eastern date: a
    10:30 pm tip is stamped the next UTC day, and grouped by that it would be
    priced after the same night's earlier games had entered the profiles. Five
    hours back is Eastern standard; the four-hour summer offset only matters
    for a 7:30-8:00 pm ET tip, which lands on the same day either way."""
    import datetime
    try:
        t = datetime.datetime.strptime((iso or "")[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return (iso or "")[:10]
    return (t - datetime.timedelta(hours=5)).date().isoformat()


def _bin(t):
    return max(0, min((TOT_HI - TOT_LO) // TOT_W, (t - TOT_LO) // TOT_W))


def _price(job):
    import nba_deep_sim as E
    gid, hp, ap = job
    rng = random.Random(zlib.crc32(f"{BASE_SEED}|{gid}".encode()))
    hw, nb = 0, (TOT_HI - TOT_LO) // TOT_W + 1
    tot = [0] * nb
    for _ in range(_W["n"]):
        g = E.play_game(hp, ap, rng, _W["T"])
        hw += g["winner"] == 0
        tot[_bin(g["score"][0] + g["score"][1])] += 1
    return gid, hw / _W["n"], [x / _W["n"] for x in tot]


def _rps(dist, y):
    nb = len(dist)
    yb = _bin(y)
    cp = co = s = 0.0
    for k in range(nb):
        cp += dist[k]
        co += 1.0 if yb <= k else 0.0
        s += (cp - co) ** 2
    return s / (nb - 1)


def box_poss(rec):
    """Each side's possessions the way basket.ratings estimates them from the
    box score: FGA - offensive rebounds + turnovers + 0.44 FTA, every period."""
    import nba_pbp as P
    c = [[0, 0, 0, 0], [0, 0, 0, 0]]
    for p in P.iter_plays(rec):
        s_ = p[P.I_TEAM]
        if s_ not in (0, 1):
            continue
        code = p[P.I_CODE]
        if code == P.FGA:
            c[s_][0] += 1
        elif code == P.OREB and not p[P.I_FLAGS] & P.F_TEAM:
            c[s_][1] += 1
        elif code == P.TOV:
            c[s_][2] += 1
        elif code == P.FT:
            c[s_][3] += 1
    return [fga - orb + tov + 0.44 * fta for fga, orb, tov, fta in c]


def _prod_model(basket, acc, home, away, gid):
    """basket.simulate_game on the ratings basket.ratings would have built
    that morning: each team's points for / against and box-score possessions a
    game to date, pace clamped to the config's +/-12, the league's means over
    the teams that have played, regressed by regress_g. Before any game, the
    config's league (what ratings falls back on)."""
    cfg = basket.LEAGUES["nba"]
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
        a = played.get(ab)
        if not a:
            return {"abbr": ab, "pace": lg_pace, "off_ppp": lg_ppp, "def_ppp": lg_ppp,
                    "lg_ppp": lg_ppp, "lg_pace": lg_pace, "w": 0, "l": 0}
        pace = pace_of(a)
        w = a["g"] / (a["g"] + cfg["regress_g"])
        return {"abbr": ab, "pace": pace, "off_ppp": w * (a["pf"] / a["g"] / pace) + (1 - w) * lg_ppp,
                "def_ppp": w * (a["pa"] / a["g"] / pace) + (1 - w) * lg_ppp,
                "lg_ppp": lg_ppp, "lg_pace": lg_pace, "w": 0, "l": 0}
    sim = basket.simulate_game("nba", rt(home), rt(away), [], [], n=2000, seed=zlib.crc32(f"prod|{gid}".encode()))
    nb = (TOT_HI - TOT_LO) // TOT_W + 1
    tot = [0] * nb
    for t in sim["_totals"]:
        tot[_bin(t)] += 1
    return sim["p_home"], [x / len(sim["_totals"]) for x in tot]


def run(store_dir, season, n=300, workers=4, log=print):
    from research import nba_deep_fit as F
    import nba_deep_data as D
    import nba_deep_sim as E
    import nba_pbp as P
    import basket
    fit_seasons = [s for s in (2022, 2023, 2024, 2025) if s < season]
    stores = F.load_stores(store_dir, fit_seasons)
    raw = F.fit(stores)
    home_rate = raw["home_wins"] / raw["games"]
    # last season's final units: the prior (measured units, not levers)
    prev = dict(F.load_stores(store_dir, [season - 1]))
    priors = {}
    if prev:
        acc_prev = {}
        for g in prev[season - 1]["games"].values():
            if not P.league_game(g):
                continue
            U = D.game_units(g)
            for s_, ab in ((0, g["home"]), (1, g["away"])):
                D.add(acc_prev.setdefault(ab, {}), U[s_])
        _p, priors = D.profiles(acc_prev)
    lg_prior = D.league({ab: u for ab, u in acc_prev.items()}) if prev else None
    cur = dict(F.load_stores(store_dir, [season]))[season]
    games = sorted((g for g in cur["games"].values() if P.league_game(g)), key=lambda g: (et_day(g["date"]), g["id"]))
    acc, prod_acc = {}, {}
    rows = []
    t0 = time.time()
    with mp.Pool(workers, initializer=_init, initargs=(store_dir, fit_seasons, n)) as pool:
        i = 0
        while i < len(games):
            day = et_day(games[i]["date"])
            todays = []
            while i < len(games) and et_day(games[i]["date"]) == day:
                todays.append(games[i])
                i += 1
            # the league the units are measured against: this season's games so
            # far once there are enough of them, last season's before that
            nseen = sum(u.get("games", 0) for u in acc.values()) // 2
            lg = D.league(acc) if nseen >= 60 else lg_prior
            prof, _units = D.profiles(acc, priors, lg=lg)
            jobs = []
            for g in todays:
                hp = prof.get(g["home"]) or dict(E.league_profile(g["home"]))
                ap = prof.get(g["away"]) or dict(E.league_profile(g["away"]))
                jobs.append((g["id"], hp, ap))
            deep = {gid: (p, dist) for gid, p, dist in pool.map(_price, jobs)}
            for g in todays:
                pp, pdist = _prod_model(basket, prod_acc, g["home"], g["away"], g["id"])
                dp, ddist = deep[g["id"]]
                y = 1 if g["home_score"] > g["away_score"] else 0
                rows.append({"id": g["id"], "date": day, "home": g["home"], "away": g["away"], "y": y,
                             "total": g["home_score"] + g["away_score"], "deep": dp, "prod": pp,
                             "deep_tot": ddist, "prod_tot": pdist})
            # the day's games enter the accumulators only after every game of
            # the day is priced: nothing priced that morning sees that night
            for g in todays:
                U = D.game_units(g)
                for s_, ab in ((0, g["home"]), (1, g["away"])):
                    D.add(acc.setdefault(ab, {}), U[s_])
                bp = box_poss(g)
                for s_, ab, pf, pa in ((0, g["home"], g["home_score"], g["away_score"]),
                                       (1, g["away"], g["away_score"], g["home_score"])):
                    a = prod_acc.setdefault(ab, {"g": 0, "pf": 0, "pa": 0, "poss": 0.0})
                    a["g"] += 1
                    a["pf"] += pf
                    a["pa"] += pa
                    a["poss"] += bp[s_]
            if log and len(rows) % 200 < len(todays):
                log(f"[nba-val] {season}: {len(rows)} games ({time.time() - t0:.0f}s)")
    return rows, {"season": season, "fit_seasons": fit_seasons, "n": n, "home_rate": home_rate,
                  "seconds": round(time.time() - t0, 1), "base_seed": BASE_SEED}


def _ll(p, y):
    p = min(1 - 1e-6, max(1e-6, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def score(rows, meta):
    n = len(rows)
    out = {"n_games": n}
    ys = [r["y"] for r in rows]
    for k, ps in (("deep", [r["deep"] for r in rows]), ("prod", [r["prod"] for r in rows]),
                  ("home_rate", [meta["home_rate"]] * n)):
        out[k] = {"logloss": round(sum(_ll(p, y) for p, y in zip(ps, ys)) / n, 5),
                  "brier": round(sum((p - y) ** 2 for p, y in zip(ps, ys)) / n, 5)}
    blends = {}
    for wt in GRID:
        d = [_ll(wt * r["deep"] + (1 - wt) * r["prod"], r["y"]) - _ll(r["prod"], r["y"]) for r in rows]
        mean = sum(d) / n
        sd = (sum((x - mean) ** 2 for x in d) / (n - 1)) ** 0.5
        blends[f"{wt:g}"] = {"logloss": round(sum(_ll(wt * r["deep"] + (1 - wt) * r["prod"], r["y"]) for r in rows) / n, 5),
                             "minus_prod": round(mean, 5), "se": round(sd / n ** 0.5, 5)}
    out["blends"] = blends
    out["totals_rps"] = {k: round(sum(_rps(r[f"{k}_tot"], r["total"]) for r in rows) / n, 5) for k in ("deep", "prod")}
    mids = [TOT_LO + TOT_W * k + TOT_W / 2.0 for k in range((TOT_HI - TOT_LO) // TOT_W + 1)]
    out["mean_total"] = {"real": round(sum(r["total"] for r in rows) / n, 2),
                         "deep": round(sum(sum(m * p for m, p in zip(mids, r["deep_tot"])) for r in rows) / n, 2),
                         "prod": round(sum(sum(m * p for m, p in zip(mids, r["prod_tot"])) for r in rows) / n, 2)}
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
    return min(GRID, key=lambda wt: (sc["blends"][f"{wt:g}"]["logloss"], wt))


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
        provenance.require_clean("the NBA deep test")
    rows, meta = run(a.store, a.season, a.n, a.workers)
    sc = score(rows, meta)
    sc["meta"] = meta
    sc["chosen_w"] = choose_w(sc)
    from research import provenance
    sc["meta"]["provenance"] = provenance.stamp(seed=BASE_SEED, model="nba_deep_sim",
                                                data_split=f"{season_label(a.season)} held out; tables {meta['fit_seasons']}")
    if a.out:
        # the rows kept compact: the per-game figures the scores are made of
        keep = [{k: r[k] for k in ("id", "date", "home", "away", "y", "total", "deep", "prod")} for r in rows]
        with open(a.out, "w") as fh:
            json.dump({"score": sc, "rows": keep}, fh, sort_keys=True)
    return sc


def season_label(s):
    return f"{s}-{(s + 1) % 100:02d}"


if __name__ == "__main__":
    s = main(sys.argv[1:])
    print(json.dumps({k: v for k, v in s.items() if k != "calibration"}, indent=1))


# ---- the report --------------------------------------------------------------------
def shape(rows, k):
    """How a forecaster's probabilities sit against the outcomes: the
    logistic calibration slope and intercept (y on the forecast's logit; a
    slope above 1 means outcomes are more extreme than forecast -- the
    forecaster is UNDER-confident) and the spread of its logits."""
    def lg(p):
        p = min(1 - 1e-6, max(1e-6, p))
        return math.log(p / (1 - p))
    xs = [lg(r[k]) for r in rows]
    a, b = 0.0, 1.0
    for _ in range(50):
        ga = gb = haa = hab = hbb = 0.0
        for x, r in zip(xs, rows):
            q = 1.0 / (1.0 + math.exp(-(a + b * x)))
            e, wq = r["y"] - q, q * (1 - q)
            ga, gb, haa, hab, hbb = ga + e, gb + e * x, haa + wq, hab + wq * x, hbb + wq * x * x
        det = haa * hbb - hab * hab
        a, b = a + (hbb * ga - hab * gb) / det, b + (haa * gb - hab * ga) / det
    m = sum(xs) / len(xs)
    return {"slope": b, "intercept": a, "sd_logit": (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5}


def logit_corr(rows):
    import statistics
    def lg(p):
        p = min(1 - 1e-6, max(1e-6, p))
        return math.log(p / (1 - p))
    return statistics.correlation([lg(r["deep"]) for r in rows], [lg(r["prod"]) for r in rows])


def write_report(v24, v25, calib, out):
    """research/reports/nba_deep_validation.md from the two seasons' artifacts
    and the shipped engine's league calibration."""
    import nba_deep_data as D
    import nba_deep_sim as E
    s24, s25 = v24["score"], v25["score"]
    w = choose_w(s24)
    b25 = s25["blends"][f"{w:g}"]
    ship = b25["minus_prod"] < 0
    e, r = calib["engine"], calib["real"]

    def tab(sc):
        L = ["| arm | log loss | Brier |", "|---|---|---|"]
        for k, name in (("prod", "production (basket.py, the board's model)"), ("deep", "deep engine"),
                        ("home_rate", "home rate alone")):
            L.append(f"| {name} | {sc[k]['logloss']:.5f} | {sc[k]['brier']:.5f} |")
        L += ["", "| blend w (on the deep engine) | log loss | minus production | SE (paired) |", "|---|---|---|---|"]
        for wt in GRID:
            b = sc["blends"][f"{wt:g}"]
            L.append(f"| {wt:g} | {b['logloss']:.5f} | {b['minus_prod']:+.5f} | {b['se']:.5f} |")
        L += ["", f"Total points: ranked probability score (5-point bins) deep {sc['totals_rps']['deep']:.4f}, "
              f"production {sc['totals_rps']['prod']:.4f}; mean total real {sc['mean_total']['real']:.1f}, deep "
              f"{sc['mean_total']['deep']:.1f}, production {sc['mean_total']['prod']:.1f}.", "",
              "Calibration (games, mean forecast, home win rate) by decile of the home probability:", ""]
        for k in ("deep", "prod"):
            L.append(f"- {k}: " + "; ".join(f"{c} @ {p:.3f} -> {y:.3f}" for c, p, y in sc["calibration"][k]))
        return L
    m24, m25 = s24["meta"], s25["meta"]
    L = ["# The NBA deep engine: out-of-sample validation", "",
         f"research/nba_deep_validate.py; 2024-25 ({s24['n_games']} games, tables fit on {m24['fit_seasons']}) and "
         f"2025-26 ({s25['n_games']} games, tables fit on {m25['fit_seasons']}), {m25['n']} engine games a matchup, "
         f"base seed {m25['base_seed']}. Provenance: 2024-25 commit {m24['provenance']['commit']} (dirty "
         f"{m24['provenance']['dirty']}), 2025-26 commit {m25['provenance']['commit']} (dirty {m25['provenance']['dirty']}).",
         "",
         "## What it is", "",
         "ESPN's play-by-play for every game (nba_pbp: each shot's value, distance and family, every free throw as "
         "k of n, rebounds, turnovers, fouls, 2022-23 through 2025-26, every stored game reproducing its final "
         "score) cut into possessions (nba_poss) and fitted into tables (research/nba_deep_fit.py): possession "
         "length by how the ball came and the game situation; the opportunity mix (turnover live or dead, a "
         "free-throw trip, a shot) for a first chance and after an offensive rebound; the shot's zone and make "
         "rate by situation, chance and how the ball came (a steal's transition rim attempt against a set "
         "defence); offensive rebounds by zone; free throws; and-ones; the period's final possession; the pull "
         "back toward an even game. The engine (nba_deep_sim) plays a game possession by possession on the real "
         "clock, each team a profile of eighteen units measured from its games (nba_deep_data), raised to the "
         "engine's lever exponent.", "",
         "## The engine against the league (shipped tables, two league-average sides, "
         f"{calib['n']:,} games)", "",
         "| | engine | real (2022-23 to 2025-26) |", "|---|---|---|"]
    for k, name, f in (("pts", "points a game", "{:.1f}"), ("home_win", "home win rate", "{:.3f}"),
                       ("ot", "past regulation", "{:.3f}"), ("margin_sd", "margin SD", "{:.2f}"),
                       ("poss", "possessions a team", "{:.1f}"), ("fga", "field goal attempts a team", "{:.1f}"),
                       ("tpa", "threes attempted a team", "{:.1f}"), ("fta", "free throws attempted a team", "{:.1f}"),
                       ("tov", "turnovers a team", "{:.1f}"), ("fg_pct", "field goal %", "{:.3f}"),
                       ("tp_pct", "three-point %", "{:.3f}"), ("ft_pct", "free throw %", "{:.3f}")):
        L.append(f"| {name} | {f.format(e[k])} | {f.format(r[k])} |")
    L += ["", "The real margin SD is the raw one; net of the two teams' strengths it is smaller, and the engine's "
          "sides are identical, so its SD is the one to read against that.", "",
          "## The rule, written before any deep run on a scored season", "",
          "The headline blends the deep engine into the production probability at a weight w on the grid "
          "0 / 0.15 / 0.3 / 0.5 / 0.7 / 1, chosen on 2024-25 by log loss. It ships only if, on 2025-26 at that "
          "frozen w, the blend's log loss is below production's (the point estimate decides; the paired standard "
          "error is reported beside it). Nothing is tuned on 2025-26.", "",
          f"Set before the harness ran: home court (nba_deep_sim._HFA_MAKE {E._HFA_MAKE:g}, matched to the 2024-25 "
          f"league's home edge on league-average sides) and the lever exponent (nba_deep_data.LEVER_EXP "
          f"{D.LEVER_EXP:g}, measured on 2023-24, where the teams' real margins on the engine's have a slope of 1). "
          "Neither carries a team's information; 2025-26 was read by neither. ESPN files the All-Star games as "
          "regular-season games; every table, unit and score here is on games between two of the thirty "
          "franchises (nba_pbp.league_game).", "",
          f"## 2024-25 (where w was chosen): w = **{w:g}**", ""]
    L += tab(s24)
    L += ["", "## 2025-26, the test", ""]
    L += tab(s25)
    L += ["", f"**Verdict: {'SHIPPED' if ship else 'NOT shipped'}** -- at the frozen w = {w:g} the blend's log loss is "
          f"{b25['logloss']:.5f} against production's {s25['prod']['logloss']:.5f} ({b25['minus_prod']:+.5f} +/- "
          f"{b25['se']:.5f}). basket._DEEP_WEIGHT = {w:g}." if ship else
          f"**Verdict: NOT shipped** -- at the frozen w = {w:g} the blend's log loss is {b25['logloss']:.5f} against "
          f"production's {s25['prod']['logloss']:.5f} ({b25['minus_prod']:+.5f} +/- {b25['se']:.5f}). The deep "
          "figure is shown on the card and shadow-graded; basket._DEEP_WEIGHT = 0.", "",
          "## How it misses", ""]
    sh = {(s_, k): shape(v["rows"], k) for s_, v in ((2024, v24), (2025, v25)) for k in ("deep", "prod")}
    L += ["| | calibration slope | intercept | SD of the logit |", "|---|---|---|---|"]
    for s_ in (2024, 2025):
        for k, name in (("prod", "production"), ("deep", "deep engine")):
            x = sh[(s_, k)]
            L.append(f"| {season_label(s_)} {name} | {x['slope']:.3f} | {x['intercept']:+.3f} | {x['sd_logit']:.3f} |")
    L += ["", f"Both models are UNDER-confident: a calibration slope above 1 means the outcomes are more extreme than "
          f"the forecasts, here by about half again in logit, every season. The deep engine's forecasts are the more "
          f"compressed (a narrower logit spread), and the two agree closely -- their logits correlate "
          f"{logit_corr(v24['rows']):.3f} on 2024-25 and {logit_corr(v25['rows']):.3f} on 2025-26 -- so the engine adds "
          "little that production lacks, and less than it costs in sharpness. The board's live calibration "
          "(calibrate.apply on the graded ledger) stretches production's figure; nothing stretches the deep one. "
          "Two candidates for the compression, neither tested here: the round shrinkage K (heavy for an 82-game "
          "sport's shooting units) and the lever exponent, measured on full-season units the engine never sees "
          "unshrunk. Changing either is a model change, judged on 2026-27 games through the shadow bucket; this "
          "test does not choose it.", "",
          "## Limits, stated", "",
          f"- Overtime is too rare: {e['ot']:.3f} of the engine's games against the league's {r['ot']:.3f}. Real "
          "endings cluster at a tie (the trailing team fouls and shoots threes, the leader protects); the engine's "
          "clutch tables carry the rates of those possessions but not the choice to extend a game. A moneyline "
          "barely feels it (overtime is close to a coin flip); a total or a spread near zero does.",
          f"- The deep engine's totals ran {s25['mean_total']['deep'] - s25['mean_total']['real']:+.1f} points a game "
          f"on 2025-26 ({s24['mean_total']['deep'] - s24['mean_total']['real']:+.1f} on 2024-25); production's "
          f"{s25['mean_total']['prod'] - s25['mean_total']['real']:+.1f} and "
          f"{s24['mean_total']['prod'] - s24['mean_total']['real']:+.1f}. The rule covered the moneyline only, so "
          "the board's total and spread edges still come from production; the deep total is shown beside it.",
          "- Teams are TEAM units: no player, no injury, no rest day, no lineup. A star's absence is the biggest "
          "single-game factor in this sport and neither model sees it.",
          "- The team units run on round shrinkage K and a prior persistence of 0.6 set before anything was "
          "measured, not fitted.",
          "- There is no market anchor: the level comes from the play-by-play alone.",
          "- Two seasons is a measurement, not a proof. 2024-25 chose the weight; 2025-26 alone tested it.", ""]
    with open(out, "w") as fh:
        fh.write("\n".join(L) + "\n")
    return ship
