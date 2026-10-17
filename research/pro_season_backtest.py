"""The NBA / NHL season sim backtested from mid-season dates: how well its
playoff, division, best-record, finalist and champion figures, made on the
first of each month from November to April, called what happened. RESEARCH.

At each cut date the board's own game model is rebuilt from what was known
that morning (games before the date), every ordered pair priced with it, and
pro_season plays the rest of the real schedule through the real formats from
the real standings that morning, 2,000 seasons:

  NBA  production (basket.simulate_game on the ratings basket.ratings would
       have built -- points per game from ESPN's results, possessions from
       the play-by-play store's box scores -- regressed by regress_g).
  NHL  0.5 x production (hockey.simulate_game, goals regressed by _REGRESS_G)
       + 0.5 x the deep engine (profiles from the play-by-play before the
       date, shrunk toward last season's final profiles; engine TABLES REFIT
       ON THE SEASONS BEFORE the one scored, so nothing scored is in them).
       The deep engine's games also give the overtime share and split.

What is chosen is TAU, the per-season strength uncertainty: every simulated
season each club draws a shift ~ N(0, sd^2) in logit applied to all its
games, sd = TAU x sqrt(K / (K + g)), g its games played at the cut and K the
model's own regression constant (12 NBA, 10 NHL) -- the posterior spread of a
true strength the model has read off g games. TAU = 0 is a season played at
fixed probabilities, which leaves the uncertainty in the ratings out.

THE RULE (written before any number below was computed)
  Grid TAU in {0, 0.1, 0.2, 0.3, 0.45}. Score: Brier, per event type
  (playoffs, division winner, best record, reached the final, champion; NBA
  also play-in and conference No. 1 seed), averaged over clubs and the six
  cut dates; the score is the mean of the event types' Brier scores.
  1. Select TAU on 2024-25 by the lowest score.
  2. On 2025-26 the chosen TAU ships if its score is no worse than TAU = 0's;
     otherwise TAU = 0 ships.
  Each league on its own. Reported beside it, as references and not part of
  the rule: the event's base rate (16 of 32, 1 of 30 ...) and, where it has
  a meaning, the standings that morning taken as certain.

Truth: the real final standings through pro_season (which reproduces ESPN's
final order exactly, research/pro_format_check.py), the clubs that played a
postseason series, the finalists and the champion from ESPN's postseason
scoreboards.

Usage: python3 -m research.pro_season_backtest <nba|nhl> <espn cache> <pbp store dir> [--jobs 4]
"""
import json
import math
import multiprocessing as mp
import os
import random
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import pro_season as PS  # noqa: E402
from research import pro_format_check as C  # noqa: E402

SELECT, TEST = 2024, 2025
TAUS = (0.0, 0.1, 0.2, 0.3, 0.45)
N = 2000
PROD_N = 1000
DEEP_N = 100
CUTS = ("11-01", "12-01", "01-01", "02-01", "03-01", "04-01")
K = {"nba": 12.0, "nhl": 10.0}
EVENTS = {"nhl": ("playoffs", "div", "best", "final", "champ"),
          "nba": ("playoffs", "playin", "div", "seed1", "best", "final", "champ")}
_SEED = 20261017


def et_day(iso):
    from research.nba_deep_validate import et_day as f
    return f(iso)


def cut_dates(season):
    return [f"{season if c >= '07' else season + 1}-{c}" for c in CUTS]


# ---- what happened -----------------------------------------------------------------
def truth(league, conf, div, reg, post):
    T = PS.Table(league, conf, div)
    for g in reg:
        T.add(g["home"], g["away"], g["hs"], g["as"], g["kind"])
    rng = random.Random(1)
    ev = {k: set() for k in EVENTS[league]}
    order = PS.league_order(T, rng)
    ev["best"] = {order[0]}
    series = [g for g in post if g["stype"] == 3]
    ev["playoffs"] = set(x for g in series for x in (g["home"], g["away"]))
    last = max(series, key=lambda g: (g["date"], g["id"]))
    pair = {last["home"], last["away"]}
    fin = [g for g in series if {g["home"], g["away"]} == pair]
    wins = {t: 0 for t in pair}
    for g in fin:
        wins[g["home"] if g["hs"] > g["as"] else g["away"]] += 1
    ev["final"] = pair
    ev["champ"] = {max(wins, key=wins.get)}
    if league == "nhl":
        for c, f in PS.nhl_field(T, rng).items():
            ev["div"].update(top[0] for top in f["div"].values())
    else:
        for c, f in PS.nba_field(T, rng).items():
            ev["div"].update(f["div_winners"])
            ev["seed1"].add(f["order"][0])
            ev["playin"].update(f["order"][6:10])
    return ev


def standings_now(league, T):
    """The standings that morning taken as certain (None where it has no meaning)."""
    rng = random.Random(1)
    out = {"best": {PS.league_order(T, rng)[0]}, "div": set(), "playoffs": set()}
    if league == "nhl":
        for c, f in PS.nhl_field(T, rng).items():
            out["div"].update(top[0] for top in f["div"].values())
            out["playoffs"].update([x for v in f["div"].values() for x in v] + f["wc"])
    else:
        out["seed1"], out["playin"] = set(), set()
        for c, f in PS.nba_field(T, rng).items():
            out["div"].update(f["div_winners"])
            out["seed1"].add(f["order"][0])
            out["playoffs"].update(f["order"][:8])
            out["playin"].update(f["order"][6:10])
    return out


# ---- the board's model that morning -------------------------------------------------
def nba_ratings(played, poss):
    """basket.ratings' build from results before the cut: {abbr: rating}."""
    import basket
    cfg = basket.LEAGUES["nba"]
    acc = {}
    for g in played:
        for s_, ab, pf, pa in ((0, g["home"], g["hs"], g["as"]), (1, g["away"], g["as"], g["hs"])):
            a = acc.setdefault(ab, {"g": 0, "pf": 0, "pa": 0, "pg": 0, "poss": 0.0})
            a["g"] += 1
            a["pf"] += pf
            a["pa"] += pa
            bp = poss.get(g["id"])
            if bp:
                a["pg"] += 1
                a["poss"] += bp[s_]
    rows = {}
    for ab, a in acc.items():
        pace = (a["poss"] / a["pg"]) if a["pg"] else cfg["lg_pace"]
        rows[ab] = {"g": a["g"], "pf": a["pf"] / a["g"], "pa": a["pa"] / a["g"],
                    "pace": max(cfg["lg_pace"] - 12, min(cfg["lg_pace"] + 12, pace))}
    if rows:
        lg_pf = sum(r["pf"] for r in rows.values()) / len(rows)
        lg_pace = sum(r["pace"] for r in rows.values()) / len(rows)
        lg_ppp = lg_pf / lg_pace
    else:
        lg_pace, lg_ppp = cfg["lg_pace"], cfg["lg_ppp"]
    out = {}
    for ab, r in rows.items():
        w = r["g"] / (r["g"] + cfg["regress_g"])
        out[ab] = {"abbr": ab, "pace": r["pace"], "lg_ppp": lg_ppp, "lg_pace": lg_pace,
                   "off_ppp": w * r["pf"] / r["pace"] + (1 - w) * lg_ppp,
                   "def_ppp": w * r["pa"] / r["pace"] + (1 - w) * lg_ppp, "g": r["g"]}
    return out, {"abbr": None, "pace": lg_pace, "lg_ppp": lg_ppp, "lg_pace": lg_pace,
                 "off_ppp": lg_ppp, "def_ppp": lg_ppp, "g": 0}


def nhl_ratings(played):
    import hockey
    acc = {}
    for g in played:
        for ab, gf, ga in ((g["home"], g["hs"], g["as"]), (g["away"], g["as"], g["hs"])):
            a = acc.setdefault(ab, {"g": 0, "gf": 0, "ga": 0})
            a["g"] += 1
            a["gf"] += gf
            a["ga"] += ga
    lg = (sum(a["gf"] / a["g"] for a in acc.values()) / len(acc)) if acc else hockey._LG_GOALS
    out = {}
    for ab, a in acc.items():
        w = a["g"] / (a["g"] + hockey._REGRESS_G)
        out[ab] = {"abbr": ab, "gf": w * a["gf"] / a["g"] + (1 - w) * lg,
                   "ga": w * a["ga"] / a["g"] + (1 - w) * lg, "lg_g": lg, "g": a["g"]}
    return out, {"abbr": None, "gf": lg, "ga": lg, "lg_g": lg, "g": 0}


_W = {}


def _init(league, raw):
    _W["league"] = league
    if raw is not None:
        import nhl_deep_sim as E
        _W["T"] = E.Tables(raw)


def _pair(job):
    """One ordered pair's entry (NBA: p_home; NHL: the four-tuple)."""
    league, h, a, rh, ra, hp, ap, key = job
    seed = zlib.crc32(f"{_SEED}|{key}|{h}|{a}".encode())
    if league == "nba":
        import basket
        return (h, a), basket.simulate_game("nba", dict(rh, abbr=h), dict(ra, abbr=a), [], [], n=PROD_N,
                                            seed=seed)["p_home"]
    import hockey
    import pro_season_job as J
    p = hockey.simulate_game(dict(rh, abbr=h), dict(ra, abbr=a), [], [], n=PROD_N, seed=seed)["p_home"]
    if hp is None or ap is None:
        p_ot, ot_home, so = J.NHL_OT, J.NHL_OT_HOME, J.NHL_SO
    else:
        import nhl_deep_sim as E
        rng = random.Random(seed ^ 0x5A5A)
        hw = ot = otw = sos = 0
        for _ in range(DEEP_N):
            g = E.play_game(hp, ap, rng, _W["T"])
            won = g["winner"] == 0
            hw += won
            if g["ot"] or g["so"]:
                ot += 1
                otw += won
                sos += 1 if g["so"] else 0
        p = 0.5 * p + 0.5 * hw / DEEP_N
        p_ot = ot / DEEP_N
        ot_home = otw / ot if ot else J.NHL_OT_HOME
        so = sos / ot if ot else J.NHL_SO
    p_ho, p_ao = p_ot * ot_home, p_ot * (1 - ot_home)
    p_hr = max(0.01, p - p_ho)
    p_ao = min(p_ao, 1 - p_hr - p_ho - 0.01)
    return (h, a), (p_hr, p_ho, p_ao, so)


def _nhl_deep_setup(store_dir, season):
    """(raw tables fit on the seasons before `season`, last season's final
    profiles, this season's store games) -- research.nhl_deep_validate's own
    construction."""
    from research import nhl_deep_fit as F
    import nhl_deep_data as D
    import nhl_deep_sim as E
    fit_seasons = [s for s in (2022, 2023, 2024, 2025) if s < season]
    raw = F.fit(F.load_stores(store_dir, fit_seasons))
    raw["dist_edges"] = list(F.DIST_EDGES)
    T = E.Tables(raw)
    prev = dict(F.load_stores(store_dir, [season - 1]))
    priors = D.profiles(D.season_units(list(prev[season - 1]["games"].values()), T, F), T) if prev else {}
    cur = dict(F.load_stores(store_dir, [season]))[season]
    return raw, T, priors, [g for g in cur["games"].values() if g.get("gtype") == 2]


def _nba_poss(store_dir, season):
    from research import nba_deep_fit as F
    from research.nba_deep_validate import box_poss
    import nba_pbp as P
    st = dict(F.load_stores(store_dir, [season])).get(season) or {"games": {}}
    out = {}
    for gid, g in st["games"].items():
        if P.league_game(g):
            try:
                out[str(g["id"])] = box_poss(g)
            except Exception:
                continue
    return out


def run_season(league, cache, store_dir, season, jobs=4, log=print):
    conf, div, _o = C.espn_alignment(league, season)
    reg, post = C.load_games(league, cache, season)
    reg = [g for g in reg if g["home"] in conf and g["away"] in conf]
    ev_true = truth(league, conf, div, reg, post)
    teams = sorted(conf)
    raw = T = priors = store_games = None
    poss = {}
    if league == "nhl":
        raw, T, priors, store_games = _nhl_deep_setup(store_dir, season)
    else:
        poss = _nba_poss(store_dir, season)
    out = []
    ctx = mp.get_context("fork")
    with ctx.Pool(jobs, initializer=_init, initargs=(league, raw)) as pool:
        for cut in cut_dates(season):
            t0 = time.time()
            played = [g for g in reg if et_day(g["date"]) < cut]
            rest = [g for g in reg if et_day(g["date"]) >= cut]
            base = PS.Table(league, conf, div)
            for g in played:
                base.add(g["home"], g["away"], g["hs"], g["as"], g["kind"])
            if league == "nba":
                rt, lg_avg = nba_ratings(played, poss)
                profs = {}
            else:
                import nhl_deep_data as D
                import nhl_pbp as P
                rt, lg_avg = nhl_ratings(played)
                acc = D.season_units(store_games, T, None, before=cut)
                profs = {P.to_espn(ab): p for ab, p in D.profiles(acc, T, priors).items()}
            work = [(league, h, a, rt.get(h) or lg_avg, rt.get(a) or lg_avg,
                     profs.get(h), profs.get(a), cut) for h in teams for a in teams if h != a]
            probs = dict(pool.map(_pair, work, chunksize=16))
            games_now = {t: base.gp(t) for t in teams}
            now = standings_now(league, base)
            res = {}
            for tau in TAUS:
                noise = ({t: tau * math.sqrt(K[league] / (K[league] + games_now[t])) for t in teams}
                         if tau else None)
                sim = PS.simulate(base, [(g["home"], g["away"]) for g in rest], probs, N,
                                  seed=zlib.crc32(f"{_SEED}|{league}|{cut}".encode()), noise=noise)
                res[f"{tau:g}"] = {e: {t: sim["tally"][t].get(e, 0.0) / N for t in teams} for e in EVENTS[league]}
            out.append({"cut": cut, "played": len(played), "rest": len(rest), "probs": res,
                        "now": {e: sorted(v) for e, v in now.items()}})
            if log:
                log(f"[backtest-{league}] {season} {cut}: {len(played)} played, {len(rest)} left ({time.time() - t0:.0f}s)")
    return {"season": season, "teams": teams, "truth": {e: sorted(v) for e, v in ev_true.items()}, "cuts": out}


def score(league, rs):
    """{tau: {"events": {e: brier}, "score": mean}} plus the references."""
    teams = rs["teams"]
    truth_ = {e: set(v) for e, v in rs["truth"].items()}
    out = {}
    for tau in (f"{t:g}" for t in TAUS):
        ev = {}
        for e in EVENTS[league]:
            sq = [(c["probs"][tau][e][t] - (1.0 if t in truth_[e] else 0.0)) ** 2 for c in rs["cuts"] for t in teams]
            ev[e] = round(sum(sq) / len(sq), 5)
        out[tau] = {"events": ev, "score": round(sum(ev.values()) / len(ev), 5)}
    base, now = {}, {}
    for e in EVENTS[league]:
        rate = len(truth_[e]) / len(teams)
        base[e] = round(sum((rate - (1.0 if t in truth_[e] else 0.0)) ** 2 for t in teams) / len(teams), 5)
        if all(e in c["now"] for c in rs["cuts"]):
            sq = [((1.0 if t in set(c["now"][e]) else 0.0) - (1.0 if t in truth_[e] else 0.0)) ** 2
                  for c in rs["cuts"] for t in teams]
            now[e] = round(sum(sq) / len(sq), 5)
    by_cut = {c["cut"]: {tau: round(sum((c["probs"][tau][e][t] - (1.0 if t in truth_[e] else 0.0)) ** 2
                                        for e in EVENTS[league] for t in teams) / (len(teams) * len(EVENTS[league])), 5)
                         for tau in (f"{t:g}" for t in TAUS)} for c in rs["cuts"]}
    return {"tau": out, "base_rate": base, "standings_now": now, "by_cut": by_cut}


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("league", choices=("nba", "nhl"))
    ap.add_argument("cache")
    ap.add_argument("store")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    sel = run_season(a.league, a.cache, a.store, SELECT, a.jobs)
    s_sel = score(a.league, sel)
    chosen = min(TAUS, key=lambda t: (s_sel["tau"][f"{t:g}"]["score"], t))
    print(f"[backtest-{a.league}] chosen on {SELECT}-{(SELECT + 1) % 100:02d}: TAU {chosen:g} "
          f"({s_sel['tau'][f'{chosen:g}']['score']} vs TAU 0 {s_sel['tau']['0']['score']})", flush=True)
    test = run_season(a.league, a.cache, a.store, TEST, a.jobs)
    s_test = score(a.league, test)
    c_, z_ = s_test["tau"][f"{chosen:g}"]["score"], s_test["tau"]["0"]["score"]
    ship = chosen if c_ <= z_ else 0.0
    print(f"[backtest-{a.league}] {TEST}-{(TEST + 1) % 100:02d}: TAU {chosen:g} {c_} vs TAU 0 {z_}; ships TAU {ship:g}",
          flush=True)
    out = {"rule": __doc__.split("Usage:")[0], "league": a.league, "chosen": chosen, "ship": ship,
           "select": {"season": SELECT, "score": s_sel, "run": sel}, "test": {"season": TEST, "score": s_test, "run": test}}
    path = os.path.join(ROOT, "research", "data", f"pro_season_backtest_{a.league}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps({"select": s_sel["tau"], "test": s_test["tau"], "base": s_test["base_rate"],
                      "now": s_test["standings_now"]}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
