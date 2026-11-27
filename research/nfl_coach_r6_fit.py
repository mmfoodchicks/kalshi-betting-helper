"""The fit and the scores of the coaching engine's sixth revision (nfl_coach_sim_r6,
research/reports/nfl_coach_r6.md). RESEARCH. nflverse (CC BY 4.0) through R2's
loader; R1's seal holds (2026 week 13 on is never read here; only the registered
read opens its own window).

    python3 -m research.nfl_coach_r6_fit <pbp dir> <players.csv>

The three drifting components, each a league tendency on a full-history reference,
following the evo study's season-level random walk (research/nfl_coach_evo.py:
`kalman`, `rw_forecast`; innovation variance by marginal likelihood):
  clock  the log of the seconds between a run and the next snap of the same drive,
         in R1's unhurried phases (0, 1, 6, 7, 8 -- where R1's pace hook acts), the
         next snap not no-huddle, 10-50 s (a timeout, an injury or a review is not
         pace); reference = the mean by phase x score state, a Gaussian score at the
         pooled residual SD; a season's offset = its mean residual, its variance
         clustered by game
  rb     the evo study's T4 table (a target to a running back), reference = the
         full-history cell rates; a season's offset = its maximum-likelihood logit shift
  pull   R3's s6 drive hazard; the engine's offset is measured against R5's own hazard
Per game, a component's gain = the reference's loss minus the loss with the forecast
offset added (positive = R6 better). Writes research/data/nfl_coach_r6_tables.json and
research/data/nfl_coach/r6_fit.json (with the development walk-forward and the power).
"""
import json
import math
import os
import sys
import time
from collections import defaultdict

import nfl_coach_sim as C
import nfl_coach_sim_r5 as E5
import nfl_coach_sim_r6 as E6
import nfl_pbp as P
from research import nfl_coach_evo as EVO
from research import nfl_coach_r2_data as R
from research import nfl_coach_r3_qb as QB

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r6_fit.json")
DEV_GAMES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r6_dev_games.json")
SEASONS = tuple(range(2016, 2027))
FOLDS = tuple(range(2019, 2027))
ENGINE_FIT = tuple(range(2016, 2024))       # R1's clock tables and R2's deal were fitted on 2016-2023
CHOICE_PHASES = (0, 1, 6, 7, 8)             # nfl_coach_sim._gap applies tempo_mult only here
WINDOW_GAMES = 272                          # a 17-week regular season


# ---- rows ------------------------------------------------------------------------------------
def clock_rows(sd_):
    """{game: [(cell, log seconds), ...]} for one season."""
    out = defaultdict(list)
    for g in sd_["games"]:
        if g["st"] != 2:
            continue
        prev = None
        for p in g["plays"]:
            if p[R.Q_KIND] not in R.SCRIMMAGE:
                continue
            if (prev is not None and prev[R.Q_KIND] == R.K_RUN and p[R.Q_PER] <= 4
                    and prev[R.Q_DRIVE] == p[R.Q_DRIVE] and prev[R.Q_OFFH] == p[R.Q_OFFH]
                    and (prev[R.Q_PER] <= 2) == (p[R.Q_PER] <= 2) and p[R.Q_NH] == 0):
                per = prev[R.Q_PER]
                ph = C.phase(per, prev[R.Q_GSR] - (4 - per) * 900)
                el = prev[R.Q_GSR] - p[R.Q_GSR]
                if ph in CHOICE_PHASES and 10 <= el <= 50:
                    out[g["id"]].append(((ph, P.score_state(prev[R.Q_SD])), math.log(el)))
            prev = p
    return dict(out)


# ---- the clock: a Gaussian on log seconds ------------------------------------------------
def clock_ref(cr, seasons):
    s, n = defaultdict(float), defaultdict(int)
    for y in seasons:
        for rows in cr[y].values():
            for c, r in rows:
                s[c] += r
                n[c] += 1
    means = {c: s[c] / n[c] for c in s}
    tot = sum(n.values())
    ss = sum((r - means[c]) ** 2 for y in seasons for rows in cr[y].values() for c, r in rows)
    return {"means": means, "sigma": math.sqrt(ss / tot), "default": sum(s.values()) / tot}


def clock_offset(cr_s, ref):
    """A season's mean residual and its variance clustered by game."""
    games = [[r - ref["means"].get(c, ref["default"]) for c, r in rows] for rows in cr_s.values()]
    n = sum(len(x) for x in games)
    m = sum(sum(x) for x in games) / n
    v = sum((sum(x) - len(x) * m) ** 2 for x in games) / n / n
    return m, v


def clock_gain(cr_S, ref, mu):
    s2 = 2 * ref["sigma"] ** 2
    out = {}
    for g, rows in cr_S.items():
        t = 0.0
        for c, r in rows:
            e = r - ref["means"].get(c, ref["default"])
            t += (e * e - (e - mu) ** 2) / s2
        out[g] = t
    return out


# ---- the backs' targets: the evo study's T4 ------------------------------------------------
def rb_ref(cs, seasons):
    r, p0 = EVO._rates(cs, {s: 1.0 for s in seasons})
    return {"rates": r, "p0": p0}


def rb_gain(rows_S, ref, mu):
    r, p0 = ref["rates"], ref["p0"]
    a = EVO.game_losses_cells(rows_S, lambda c: r.get(c, p0))
    b = EVO.game_losses_cells(rows_S, lambda c: EVO._sig(EVO._logit(r.get(c, p0)) + mu))
    return {g: a[g] - b[g] for g in a}


def rb_offsets(cs, ref, seasons):
    L = {c: EVO._logit(v) for c, v in ref["rates"].items()}
    return [EVO._offset(cs[s], L) for s in seasons]


# ---- the pull ----------------------------------------------------------------------------
def pull_gain(rows_S, coef, mu):
    a = EVO._pull_losses(rows_S, coef)
    b = EVO._pull_losses(rows_S, coef, mu)
    return {g: a[g] - b[g] for g in a}


def _pw(eff, sd, n, z=2.0):
    return round(0.5 * math.erfc(-(eff / (sd / math.sqrt(n)) - z) / math.sqrt(2)), 4)


def summ(d):
    v = list(d.values())
    n = len(v)
    m = sum(v) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))
    return {"gain_per_game": round(m, 5), "se": round(sd / math.sqrt(n), 5), "sd": round(sd, 4),
            "z": round(m / (sd / math.sqrt(n)), 2) if sd else None, "games": n}


def load(pbp, players_csv):
    pos = R.positions(players_csv)
    cr, rb = {}, {}
    for s in SEASONS:
        sd_ = R.load_season(pbp, s)
        cr[s] = clock_rows(sd_)
        rb[s] = EVO.cell_rows(sd_, pos)["T4_rb_target"]
    allg, _pos, _ids = QB.load_all(pbp, players_csv, SEASONS)
    return cr, rb, EVO.pull_rows(allg)


def development(cr, rb, pr):
    """The walk-forward: each fold's references on the seasons before it, the
    offsets of those seasons, the random walk's forecast, the gains on the fold."""
    cs = EVO._cells_by_season(rb)
    per = {"clock": {}, "rb": {}, "pull": {}}
    folds = {}
    for S in FOLDS:
        tr = [s for s in SEASONS if s < S]
        cref = clock_ref(cr, tr)
        mc, _ = EVO.rw_forecast([clock_offset(cr[s], cref) for s in tr])
        rref = rb_ref(cs, tr)
        mr, _ = EVO.rw_forecast(rb_offsets(cs, rref, tr))
        base = EVO.pull_fit(pr, "full", S)
        mp, _ = EVO.rw_forecast([EVO._pull_offset(pr[s], base) for s in tr])
        gs = {"clock": clock_gain(cr[S], cref, mc), "rb": rb_gain(rb[S], rref, mr),
              "pull": pull_gain(pr[S], base, mp)}
        tot = defaultdict(float)
        for k, d in gs.items():
            for g, v in d.items():
                per[k][g] = v
                tot[g] += v
        folds[S] = {"forecasts": {"clock": round(mc, 4), "rb": round(mr, 4), "pull": round(mp, 4)},
                    "pooled": summ(tot), "games": dict(tot)}
    return per, folds


def final(cr, rb, pr):
    cs = EVO._cells_by_season(rb)
    cref = clock_ref(cr, SEASONS)
    co = [clock_offset(cr[s], cref) for s in SEASONS]
    mc, tc = EVO.rw_forecast(co)
    rref = rb_ref(cs, SEASONS)
    ro = rb_offsets(cs, rref, SEASONS)
    mr, tr_ = EVO.rw_forecast(ro)

    def level(offs):
        ix = [SEASONS.index(s) for s in ENGINE_FIT]
        w = [1 / offs[i][1] for i in ix]
        return sum(wi * offs[i][0] for wi, i in zip(w, ix)) / sum(w)
    q5 = E5.tables()
    po = [EVO._pull_offset(pr[s], q5.coef) for s in SEASONS]
    mp, tp = EVO.rw_forecast(po)
    lc, lr = level(co), level(ro)
    return {
        "version": E6.VERSION, "fit_seasons": "2016 to 2026 week 12 (R1's seal)", "forecast_season": 2027,
        "clock": {"m_clock": math.exp(mc - lc), "mu": mc, "level_r1_2016_2023": lc, "tau": tc,
                  "offsets": [round(d, 5) for d, _v in co],
                  "ref_means": {f"{c[0]},{c[1]}": m for c, m in sorted(cref["means"].items())},
                  "sigma": cref["sigma"], "default": cref["default"]},
        "rb_target": {"delta": mr - lr, "mu": mr, "level_r2_2016_2023": lr, "tau": tr_,
                      "offsets": [round(d, 5) for d, _v in ro],
                      "ref_rates": {",".join(map(str, c)): v for c, v in sorted(rref["rates"].items())},
                      "p0": rref["p0"]},
        "pull": {"mu": mp, "tau": tp, "offsets": [round(d, 5) for d, _v in po], "against": "R5's hazard"},
        "credit": "nflverse-data play-by-play and players (CC BY 4.0)",
        "seal": "2026 week 13 on and every later season: never read by this fit",
        "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


# ---- the registered statistic (read by research/nfl_coach_r6_read.py) ----------------------
def frozen_refs(tab):
    cref = {"means": {tuple(int(x) for x in k.split(",")): v for k, v in tab["clock"]["ref_means"].items()},
            "sigma": tab["clock"]["sigma"], "default": tab["clock"]["default"]}
    rref = {"rates": {tuple(int(x) for x in k.split(",")): v for k, v in tab["rb_target"]["ref_rates"].items()},
            "p0": tab["rb_target"]["p0"]}
    return cref, rref


def window_gains(tab, cr_S, rb_S, pr_S):
    """Per game, each component's gain on the window at the FROZEN references and
    forecasts (nothing refitted), and the pooled sum."""
    cref, rref = frozen_refs(tab)
    gs = {"clock": clock_gain(cr_S, cref, tab["clock"]["mu"]), "rb": rb_gain(rb_S, rref, tab["rb_target"]["mu"]),
          "pull": pull_gain(pr_S, E5.tables().coef, tab["pull"]["mu"])}
    tot = defaultdict(float)
    for d in gs.values():
        for g, v in d.items():
            tot[g] += v
    return gs, dict(tot)


def main(pbp, players_csv):
    cr, rb, pr = load(pbp, players_csv)
    per, folds = development(cr, rb, pr)
    tot = defaultdict(float)
    for d in per.values():
        for g, v in d.items():
            tot[g] += v
    pooled = summ(tot)
    comp = {k: summ(d) for k, d in per.items()}
    last2 = {g: v for S in (2025, 2026) for g, v in folds[S]["games"].items()}
    l2 = summ(last2)
    power = {"window_games": WINDOW_GAMES,
             "criterion_2_at_dev_mean": _pw(pooled["gain_per_game"], pooled["sd"], WINDOW_GAMES),
             "criterion_2_at_2025_26": _pw(l2["gain_per_game"], l2["sd"], WINDOW_GAMES),
             "criterion_2_at_half_dev_mean": _pw(pooled["gain_per_game"] / 2, pooled["sd"], WINDOW_GAMES),
             "criterion_3_each_component_holds": {
                 k: round(0.5 * math.erfc(-(c["gain_per_game"] / (c["sd"] / math.sqrt(WINDOW_GAMES)) + 1)
                                          / math.sqrt(2)), 4) for k, c in comp.items()}}
    out = {"development": {"pooled": pooled, "components": comp, "last_two_folds": l2,
                           "folds": {str(S): {k: v for k, v in f.items() if k != "games"} for S, f in folds.items()}},
           "power": power}
    tab = final(cr, rb, pr)
    out["final"] = {k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items()
                                                           if kk not in ("ref_means", "ref_rates")})
                    for k, v in tab.items()}
    with open(E6.TABLES_PATH, "w") as fh:
        json.dump(tab, fh, indent=1, sort_keys=True)
    with open(DEV_GAMES, "w") as fh:
        json.dump({str(S): {g: round(v, 6) for g, v in folds[S]["games"].items()} for S in (2025, 2026)}, fh,
                  sort_keys=True)
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2]), indent=1)[:5000])
