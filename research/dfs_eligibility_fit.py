"""The adaptive eligibility model: who beyond the depth chart has a credible chance of a game that matters?
RESEARCH; the rule below was written 2026-11-27 before any number in this file was computed (committed first).

THE RULE
  Question: which players may the DFS boards put in OUR lineups beyond the depth-chart rank gate (QB1, RB1-2,
  WR1-3, TE1-2)? Eligibility never changes a projection: the simulator's own numbers decide whether an eligible
  player is any good. Adaptive keeps every player the gate keeps and ADDS rank-gated players by this model.
  Data: research/data/player_weeks_2022_2025.csv.gz and player_weeks_2026.csv.gz (Sleeper's weekly projection
  and box score per player, DraftKings scoring). Regular season. FIT 2022-2024; TEST 2025; 2026 weeks 1-5 a
  second test. The tests are descriptive: the model ships as fitted, whatever they show; whether Adaptive becomes
  the default is research/data/dfs_eligibility/prereg.json's question, never this file's.
  Population: player-weeks of QB/RB/WR/TE with a positive projection (proj_dk > 0) and none of the flags
  team_conflict, pos_out_of_scope, negative_stat.
  Gate stand-in: Sleeper does not keep depth charts by week, so history ranks a team-week's position group by
  projection (proj_dk, descending); ranks 1..keep (QB 1, RB 2, WR 3, TE 2) are "kept".
  Level L_pos: the median DraftKings score of the LAST kept rank (QB1, RB2, WR3, TE2) in the fit seasons, over the
  player-weeks where that player played (act_gp > 0).
  Outcome Y: 1 when his DraftKings points reach L_pos (a player who did not play scored 0).
  Features (dfs_eligibility.features / usage_row, the code production runs; strictly earlier weeks of the same
  season): x1 = ln(1 + proj_dk); x2 = his targets + carries over his last 3 games played for this team over the
  team's targets + carries in those weeks (0 with none); x3 = 1 with no earlier game for this team this season;
  x4 = 1 when he played for this team earlier this season but not in the team's most recent game (returning);
  x5 = x4 * x2 (his pre-absence share). Team totals are every row of the team that week; a week counts for a team
  only when its total is positive (byes).
  Model: per position, logistic regression on (1, x1..x5), ridge 1e-3 on the slopes, Newton to convergence.
  Threshold tau_pos: the 5th percentile of the fitted probability among the kept player-weeks of the fit seasons.
  Adaptive admits a rank-gated player when p >= tau_pos: "at least as likely to have a starter-level game as the
  weakest 5% of the players the gate already trusts".
  Reported on each test: log loss of the model against the projection-only logistic (1, x1) fitted the same way
  (paired, SE over team-weeks); calibration by decile; among gated player-weeks (rank > keep) the share the rule
  admits, Y's rate among admitted and not admitted, the share of gated Y = 1 it admits, admitted per team-week;
  the returning players among them.

    python3 research/dfs_eligibility_fit.py          # writes research/data/dfs_eligibility/{model,fit}.json
"""
import json
import math
import os
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

import dfs_eligibility as E  # noqa: E402

KEEP = {"QB": 1, "RB": 2, "WR": 3, "TE": 2}
FIT = (2022, 2023, 2024)
TESTS = ((2025, None), (2026, 5))
RIDGE = 1e-3
TAU_Q = 5.0
OUT = os.path.join(_ROOT, "research", "data", "dfs_eligibility")
_BAD = ("team_conflict", "pos_out_of_scope", "negative_stat")


def _f(v):
    return float(v) if v not in ("", None) else 0.0


def load():
    import csv
    import gzip
    rows = []
    for fn in ("player_weeks_2022_2025.csv.gz", "player_weeks_2026.csv.gz"):
        with gzip.open(os.path.join(_ROOT, "research", "data", fn), "rt") as fh:
            rows += list(csv.DictReader(fh))
    return rows


def histories(rows):
    """{season: {week: compact week}} in nfl_usage.compact's shape, from the table."""
    H = {}
    for r in rows:
        if any(b in (r.get("flags") or "") for b in ("team_conflict",)):
            continue
        s, w, tm = int(r["season"]), int(r["week"]), r["team"]
        if not tm:
            continue
        wk = H.setdefault(s, {}).setdefault(w, {"pl": {}, "tot": {}})
        tg, ca = _f(r.get("act_rec_tgt")), _f(r.get("act_rush_att"))
        t = wk["tot"].setdefault(tm, [0.0, 0.0])
        t[0] += tg
        t[1] += ca
        if _f(r.get("act_gp")) > 0:
            wk["pl"][str(r["player_id"])] = [tm, tg, ca]
    return H


def frame(rows):
    H = histories(rows)
    pop = [r for r in rows if r["pos"] in KEEP and _f(r.get("proj_dk")) > 0
           and not any(b in (r.get("flags") or "") for b in _BAD)]
    groups = {}
    for r in pop:
        groups.setdefault((r["season"], r["week"], r["team"], r["pos"]), []).append(r)
    out = []
    for (s, w, tm, pos), g in groups.items():
        g.sort(key=lambda r: -_f(r["proj_dk"]))
        s, w = int(s), int(w)
        past = {k: v for k, v in H.get(s, {}).items() if k < w}
        for i, r in enumerate(g):
            u = E.usage_row(r["player_id"], tm, past)
            out.append({"season": s, "week": w, "team": tm, "pos": pos, "name": r["name"], "rank": i + 1,
                        "kept": i + 1 <= KEEP[pos], "proj": _f(r["proj_dk"]), "act": _f(r.get("act_dk")),
                        "played": _f(r.get("act_gp")) > 0, "u": u,
                        "x": E.features(_f(r["proj_dk"]), u["share"], u["has_hist"], u["returning"])})
    return out


def logit_fit(X, y, ridge=RIDGE, iters=100):
    X, y = np.asarray(X, float), np.asarray(y, float)
    b = np.zeros(X.shape[1])
    R = np.eye(X.shape[1]) * ridge
    R[0, 0] = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(X @ b)))
        g = X.T @ (p - y) + R @ b
        H = (X * (p * (1 - p))[:, None]).T @ X + R
        step = np.linalg.solve(H, g)
        b -= step
        if np.abs(step).max() < 1e-10:
            break
    return b


def ll(p, y):
    p = np.clip(np.asarray(p, float), 1e-12, 1 - 1e-12)
    y = np.asarray(y, float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def main():
    rows = load()
    F = frame(rows)
    fit = [r for r in F if r["season"] in FIT]
    model = {"version": 1, "fit_seasons": list(FIT), "recent_n": E.RECENT_N, "tau_quantile": TAU_Q,
             "ridge": RIDGE, "features": ["1", "ln(1+proj)", "share", "no_history", "returning", "returning*share"],
             "rule": "research/dfs_eligibility_fit.py docstring (committed before the fit)", "positions": {}}
    report = {"positions": {}, "tests": {}}
    for pos in E.POS:
        lastk = [r["act"] for r in fit if r["pos"] == pos and r["rank"] == KEEP[pos] and r["played"]]
        L = float(np.median(lastk))
        rs = [r for r in fit if r["pos"] == pos]
        y = [1.0 if r["act"] >= L else 0.0 for r in rs]
        b = logit_fit([r["x"] for r in rs], y)
        b0 = logit_fit([r["x"][:2] for r in rs], y)
        pk = [E.prob(list(b), r["x"]) for r in rs if r["kept"]]
        tau = float(np.percentile(pk, TAU_Q))
        model["positions"][pos] = {"coef": [round(float(c), 6) for c in b], "L": round(L, 2),
                                   "tau": round(tau, 6), "n": len(rs), "base_rate": round(float(np.mean(y)), 4),
                                   "proj_only_coef": [round(float(c), 6) for c in b0]}
    os.makedirs(OUT, exist_ok=True)
    for season, upto in TESTS:
        T = [r for r in F if r["season"] == season and (upto is None or r["week"] <= upto)]
        rep = {}
        tot = {"admit": 0, "gated": 0, "gated_y": 0, "admit_y": 0, "team_weeks": len({(r["week"], r["team"]) for r in T})}
        dl_all, tw_all = [], []
        for pos in E.POS:
            m = model["positions"][pos]
            rs = [r for r in T if r["pos"] == pos]
            if not rs:
                continue
            y = np.array([1.0 if r["act"] >= m["L"] else 0.0 for r in rs])
            p = np.array([E.prob(m["coef"], r["x"]) for r in rs])
            p0 = np.array([E.prob(m["proj_only_coef"], r["x"][:2]) for r in rs])
            d = ll(p, y) - ll(p0, y)
            # paired SE over team-weeks (rows of one team-week share a game)
            cl = {}
            for r, v in zip(rs, d):
                cl[(r["week"], r["team"])] = cl.get((r["week"], r["team"]), 0.0) + v
            cv = np.array(list(cl.values()))
            se = float(cv.std(ddof=1) * math.sqrt(len(cv)) / len(rs)) if len(cv) > 1 else None
            dl_all += list(d)
            tw_all += [(r["week"], r["team"]) for r in rs]
            dec = []
            order = np.argsort(p)
            for chunk in np.array_split(order, 10):
                if len(chunk):
                    dec.append([round(float(p[chunk].mean()), 4), round(float(y[chunk].mean()), 4), int(len(chunk))])
            gated = [i for i, r in enumerate(rs) if not r["kept"]]
            adm = [i for i in gated if p[i] >= m["tau"]]
            ret = [i for i in gated if rs[i]["u"]["returning"]]
            rep[pos] = {"n": len(rs), "y_rate": round(float(y.mean()), 4),
                        "ll_model": round(float(ll(p, y).mean()), 5), "ll_proj_only": round(float(ll(p0, y).mean()), 5),
                        "ll_diff": round(float(d.mean()), 5), "ll_diff_se": None if se is None else round(se, 5),
                        "deciles": dec, "gated": len(gated), "gated_y": int(sum(y[i] for i in gated)),
                        "admitted": len(adm), "admitted_y": int(sum(y[i] for i in adm)),
                        "y_rate_admitted": round(float(np.mean([y[i] for i in adm])), 4) if adm else None,
                        "y_rate_gated_not_admitted": round(float(np.mean([y[i] for i in gated if i not in set(adm)])), 4)
                        if len(adm) < len(gated) else None,
                        "returning_gated": len(ret), "returning_admitted": sum(1 for i in ret if p[i] >= m["tau"]),
                        "returning_gated_y": int(sum(y[i] for i in ret)),
                        "kept_below_tau": sum(1 for i, r in enumerate(rs) if r["kept"] and p[i] < m["tau"])}
            tot["admit"] += len(adm)
            tot["gated"] += len(gated)
            tot["gated_y"] += rep[pos]["gated_y"]
            tot["admit_y"] += rep[pos]["admitted_y"]
        cl = {}
        for k, v in zip(tw_all, dl_all):
            cl[k] = cl.get(k, 0.0) + v
        cv = np.array(list(cl.values()))
        tot["ll_diff_all"] = round(float(np.mean(dl_all)), 5)
        tot["ll_diff_all_se"] = round(float(cv.std(ddof=1) * math.sqrt(len(cv)) / len(dl_all)), 5)
        tot["admitted_per_team_week"] = round(tot["admit"] / max(1, tot["team_weeks"]), 3)
        tot["share_of_gated_y_admitted"] = round(tot["admit_y"] / max(1, tot["gated_y"]), 4)
        report["tests"][f"{season}" + (f"_w1-{upto}" if upto else "")] = {"positions": rep, "total": tot}
    with open(os.path.join(OUT, "model.json"), "w") as fh:
        json.dump(model, fh, indent=1, sort_keys=True)
    with open(os.path.join(OUT, "fit.json"), "w") as fh:
        json.dump({"model": model, "report": report}, fh, indent=1, sort_keys=True)
    print(json.dumps(model["positions"], indent=1))
    for k, v in report["tests"].items():
        print(k, json.dumps(v["total"]))
        for pos, r in v["positions"].items():
            print("  ", pos, {kk: r[kk] for kk in ("n", "ll_diff", "ll_diff_se", "gated", "gated_y", "admitted",
                                                     "admitted_y", "y_rate_admitted", "y_rate_gated_not_admitted",
                                                     "returning_gated", "returning_admitted", "kept_below_tau")})


if __name__ == "__main__":
    main()
