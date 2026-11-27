"""The experimental discrete allocation (nfl_discrete_alloc) against the legacy week-board model
(nfl_dfs_sim.simulate_game) on the same Sleeper projections, scored against the real box scores
(2026-11-27; report research/reports/nfl_discrete_alloc.md).

Inputs: research/data/player_weeks_2022_2025.csv.gz and player_weeks_2026.csv.gz (Sleeper's weekly
projection beside Sleeper's box, per player-week; research/hist_data.py). Read: 2025 weeks 1-12 and
2026 weeks 1-5 (all the 2026 table holds). 2026 week 13 on is SEALED for the coaching tests and is
never read (asserted). Nothing is fitted: both models run as they are, so this is a development
comparison, not a registered test.

    python3 research/nfl_discrete_alloc_eval.py [--n 2000] [--out research/data/nfl_discrete_alloc_eval.json]
"""
import argparse
import collections
import csv
import gzip
import json
import math
import os
import random
import sys
import time

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402

import nfl_dfs_sim as S  # noqa: E402
import nfl_discrete_alloc as D  # noqa: E402

TABLES = ("research/data/player_weeks_2022_2025.csv.gz", "research/data/player_weeks_2026.csv.gz")
WINDOW = {"2025": range(1, 13), "2026": range(1, 13)}
SEALED_FROM = {"2026": 13}
COMP = ("pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int", "rush_att", "rush_yd", "rush_td",
        "rec_tgt", "rec", "rec_yd", "rec_td", "fum_lost")
STATS = ("pass_yd", "pass_td", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td")
CLIP = 1e-4


def _f(x):
    try:
        return float(x) if x not in (None, "") else 0.0
    except ValueError:
        return 0.0


def load_games():
    games = collections.OrderedDict()
    for path in TABLES:
        with gzip.open(os.path.join(_ROOT, path), "rt") as fh:
            for r in csv.DictReader(fh):
                s, w = r["season"], int(r["week"])
                if s not in WINDOW or w not in WINDOW[s]:
                    continue
                assert not (s in SEALED_FROM and w >= SEALED_FROM[s]), "a sealed week was read"
                if r["has_proj"] != "1" or _f(r["proj_dk"]) <= 0 or r["pos"] not in ("QB", "RB", "WR", "TE"):
                    continue
                m = {c: _f(r[f"proj_{c}"]) for c in COMP}
                means = {"pass_yd": m["pass_yd"], "pass_td": m["pass_td"], "int": m["pass_int"],
                         "rush_yd": m["rush_yd"], "rush_td": m["rush_td"], "rec": m["rec"], "rec_yd": m["rec_yd"],
                         "rec_td": m["rec_td"], "fum": m["fum_lost"], "pass_att": m["pass_att"],
                         "pass_cmp": m["pass_cmp"], "rec_tgt": m["rec_tgt"], "rush_att": m["rush_att"]}
                act = ({c: _f(r[f"act_{c}"]) for c in COMP} | {"dk": _f(r["act_dk"])}
                       if r["has_act"] == "1" and r["keep"] == "1" else None)
                key = (s, w, r["game_key"])
                g = games.setdefault(key, {"players": [], "teams": [], "label": r["game_key"]})
                g["players"].append({"name": f"{r['player_id']}", "pos": r["pos"], "team": r["team"], "opp": r["opp"],
                                     "means": means, "proj_pts": _f(r["proj_dk"]), "_act": act,
                                     "_proj_dk": _f(r["proj_dk"])})
                if r["team"] not in g["teams"]:
                    g["teams"].append(r["team"])
    return {k: g for k, g in games.items() if len(g["teams"]) == 2}


def _legacy_world(g, n, seed):
    random.seed(seed)
    sim = S.simulate_game(g, n=n, with_components=True)
    by = {r["name"]: r for r in sim["players"]}
    out = []
    for p in g["players"]:
        c = by[p["name"]]["comps"]
        comps = {"pass_yd": np.asarray(c["pass_yd"]), "pass_td": np.asarray(c["pass_td"]), "int": np.asarray(c["int"]),
                 "rush_yd": np.asarray(c["rush_yd"]), "rush_td": np.asarray(c["rush_td"]), "rec": np.asarray(c["rec"]),
                 "rec_yd": np.asarray(c["rec_yd"]), "rec_td": np.asarray(c["rec_td"]), "fum": np.asarray(c["fum"])}
        out.append({"comps": comps, "dk": D._dk(comps)})
    return out


def _disc_world(g, n, seed):
    sim = D.simulate_game(g, n=n, rng=np.random.default_rng(seed))
    return [{"comps": r["comps"], "dk": r["dk"]} for r in sim["players"]], sim["conservation"]


class Score:
    """Pooled log loss, Brier and a 10-bin reliability table for binary forecasts."""
    def __init__(self):
        self.n = 0
        self.ll = self.br = 0.0
        self.bins = [[0, 0.0, 0] for _ in range(10)]

    def add(self, p, y):
        q = min(max(p, CLIP), 1 - CLIP)
        self.ll += -math.log(q if y else 1 - q)
        self.br += (p - y) ** 2
        self.n += 1
        b = self.bins[min(int(p * 10), 9)]
        b[0] += 1
        b[1] += p
        b[2] += y

    def out(self):
        return {"n": self.n, "log_loss": round(self.ll / self.n, 5) if self.n else None,
                "brier": round(self.br / self.n, 5) if self.n else None,
                "reliability": [{"bin": f"{10 * i}-{10 * i + 10}%", "n": b[0],
                                 "mean_p": round(b[1] / b[0], 4) if b[0] else None,
                                 "hit": round(b[2] / b[0], 4) if b[0] else None} for i, b in enumerate(self.bins)]}


def _lines(stat, mean):
    """The half-point lines scored for a stat, chosen off the PROJECTION only (never a model output)."""
    if stat == "rec":
        c = int(round(mean))
        return [k - 0.5 for k in range(max(1, c - 3), c + 4)]
    step = 25 if stat == "pass_yd" else 10
    c = int(round(mean / step)) * step
    return [c + d * step - 0.5 for d in range(-4, 5) if c + d * step > 0]


def _pit(sim, a, rng):
    """Randomized PIT of an outcome under a sample: P(X < a) + U * P(X = a)."""
    return float((sim < a).mean() + rng.random() * (sim == a).mean())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--out", default=os.path.join(_ROOT, "research", "data", "nfl_discrete_alloc_eval.json"))
    a = ap.parse_args(argv)
    t0 = time.time()
    games = load_games()
    rng = np.random.default_rng(20261127)
    paths = ("legacy", "discrete")
    prob = {p: {s: Score() for s in ("rec", "rec_yd", "rush_yd", "pass_yd")} for p in paths}
    exact = {p: [0.0, 0] for p in paths}                  # log score of the realized reception count
    moments = {p: collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0]) for p in paths}   # stat: [Σmean, Σvar, Σsq err, n]
    per_game_means = {p: collections.defaultdict(list) for p in paths}
    pit = {p: [] for p in paths}
    cover = {p: [0, 0] for p in paths}
    dk_mom = {p: [0.0, 0.0, 0] for p in paths}            # Σ|mean - proj|, Σsd, n
    corr = {p: collections.defaultdict(list) for p in paths}
    obs_pairs = collections.defaultdict(list)
    floor_ceil = {p: collections.Counter() for p in paths}
    tdc = {p: [] for p in paths}
    tdc_obs = []
    bonus = {p: {s: collections.Counter() for s in ("rec_yd", "rush_yd", "pass_yd")} for p in paths}
    ties = {p: [0.0, 0] for p in paths}
    ties_obs = [0, 0]
    cons_d = collections.Counter()
    cons_l = {"worlds": 0, "yd_gap_gt_half": 0, "td_mismatch": 0, "abs_yd_gap": 0.0}
    for gi, (key, g) in enumerate(games.items()):
        seed = 1000 + gi
        worlds = {"legacy": _legacy_world(g, a.n, seed)}
        worlds["discrete"], cons = _disc_world(g, a.n, seed)
        for t, c in cons.items():
            cons_d.update(c)
        # the legacy board's conservation: the quarterback's yards and TDs against his receivers'
        for t in g["teams"]:
            idx = [i for i, p in enumerate(g["players"]) if p["team"] == t]
            qbs = [i for i in idx if g["players"][i]["pos"] == "QB" and g["players"][i]["means"]["pass_yd"] > 0]
            if not qbs:
                continue
            q = max(qbs, key=lambda i: g["players"][i]["means"]["pass_yd"])
            lw = worlds["legacy"]
            rec_yd = sum(lw[i]["comps"]["rec_yd"] for i in idx if i != q)
            rec_td = sum(lw[i]["comps"]["rec_td"] for i in idx if i != q)
            gap = lw[q]["comps"]["pass_yd"] - rec_yd
            cons_l["worlds"] += a.n
            cons_l["yd_gap_gt_half"] += int((np.abs(gap) > 0.5).sum())
            cons_l["td_mismatch"] += int((lw[q]["comps"]["pass_td"] != rec_td).sum())
            cons_l["abs_yd_gap"] += float(np.abs(gap).sum())
        for p in paths:
            w = worlds[p]
            for i, pl in enumerate(g["players"]):
                c = w[i]["comps"]
                for s in STATS:
                    if pl["means"][s if s != "pass_yd" else "pass_yd"] <= 0 and s not in ("rec_td", "rush_td", "pass_td"):
                        continue
                    v = c[s].astype(np.float64)
                    mom = moments[p][s]
                    mom[0] += float(v.mean())
                    mom[1] += float(v.var())
                    per_game_means[p][s].append((gi, float(v.mean()), float(v.var())))
                    if pl["_act"] is not None:
                        mom[2] += (pl["_act"][s] - float(v.mean())) ** 2
                        mom[3] += 1
                act = pl["_act"]
                if act is None:
                    continue
                for s in ("rec", "rec_yd", "rush_yd", "pass_yd"):
                    if pl["means"][s] <= (0.5 if s == "rec" else 5):
                        continue
                    for L in _lines(s, pl["means"][s]):
                        prob[p][s].add(float((c[s] > L).mean()), 1 if act[s] > L else 0)
                if pl["means"]["rec"] >= 1:
                    v = c["rec"]
                    a_ = act["rec"]
                    # the probability given to the realized count: an exact count here, the mass within half a
                    # catch of it for the continuous legacy draw (the rounding convention)
                    pr = float((v == a_).mean()) if p == "discrete" else float(((v > a_ - 0.5) & (v <= a_ + 0.5)).mean())
                    exact[p][0] += math.log(max(pr, CLIP))
                    exact[p][1] += 1
                dk = w[i]["dk"]
                pit[p].append(_pit(dk, act["dk"], rng))
                lo, hi = np.quantile(dk, [0.1, 0.9])
                cover[p][0] += int(lo <= act["dk"] <= hi)
                cover[p][1] += 1
                fc = floor_ceil[p]
                fc["below_q10"] += int(act["dk"] < lo)
                fc["above_q90"] += int(act["dk"] > hi)
                fc["q10"] += float(lo)
                fc["q90"] += float(hi)
                fc["n"] += 1
                # a whole-number DraftKings score is on the 0.02-point lattice; a continuous draw almost never is
                fc["on_lattice"] += float((np.abs(dk * 50 - np.round(dk * 50)) < 1e-6).mean())
                # receptions and receiving touchdowns, inside one player's worlds
                if pl["means"]["rec"] >= 2 and c["rec"].std() > 0 and c["rec_td"].std() > 0:
                    tdc[p].append(float(np.corrcoef(c["rec"], c["rec_td"])[0, 1]))
                    if p == "legacy":
                        tdc_obs.append((act["rec"] - pl["means"]["rec"], act["rec_td"] - pl["means"]["rec_td"]))
                # DraftKings' yardage bonuses: >= 100 rushing / receiving, >= 300 passing, scored per world
                for s, T in (("rec_yd", 100), ("rush_yd", 100), ("pass_yd", 300)):
                    if pl["means"][s] < T / 3:
                        continue
                    x = c[s].astype(np.float64)
                    b = bonus[p][s]
                    pb = float((x >= T).mean())
                    b["pred"] += pb
                    b["brier"] += (pb - (act[s] >= T)) ** 2
                    b["obs"] += int(act[s] >= T)
                    b["n"] += 1
                    # the mass a whole-yard box would round to the bonus that the continuous rule (>= T) misses
                    b["mass_half_below"] += float(((x >= T - 0.5) & (x < T)).mean())
                dk_mom[p][0] += abs(float(dk.mean()) - pl["_proj_dk"])
                dk_mom[p][1] += float(dk.std())
                dk_mom[p][2] += 1
            # within-game DraftKings-point correlations by pair type
            P = g["players"]
            for i in range(len(P)):
                for j in range(i + 1, len(P)):
                    typ = _pair_type(P[i], P[j])
                    if typ is None:
                        continue
                    di, dj = w[i]["dk"], w[j]["dk"]
                    if di.std() > 0 and dj.std() > 0:
                        corr[p][typ].append(float(np.corrcoef(di, dj)[0, 1]))
                    if typ in ("WR-WR same team", "TE-WR same team"):
                        ties[p][0] += float((np.abs(di - dj) < 1e-9).mean())
                        ties[p][1] += 1
                        if p == "legacy" and P[i]["_act"] is not None and P[j]["_act"] is not None:
                            ties_obs[0] += int(abs(P[i]["_act"]["dk"] - P[j]["_act"]["dk"]) < 1e-9)
                            ties_obs[1] += 1
                    if p == "legacy" and P[i]["_act"] is not None and P[j]["_act"] is not None:
                        obs_pairs[typ].append((P[i]["_act"]["dk"] - P[i]["_proj_dk"], P[j]["_act"]["dk"] - P[j]["_proj_dk"]))
    # ---- summaries
    def boot_ratio(stat, k):
        rows = {p: collections.defaultdict(float) for p in paths}
        for p in paths:
            for gi, mu, var in per_game_means[p][stat]:
                rows[p][gi] += (mu, var)[k]
        gids = sorted(set(rows["legacy"]) & set(rows["discrete"]))
        num = np.asarray([rows["discrete"][g_] for g_ in gids])
        den = np.asarray([rows["legacy"][g_] for g_ in gids])
        if den.sum() <= 0:
            return None
        b = np.random.default_rng(7)
        rs = []
        for _ in range(400):
            ix = b.integers(0, len(gids), len(gids))
            rs.append(num[ix].sum() / max(den[ix].sum(), 1e-12))
        return {"ratio": round(float(num.sum() / den.sum()), 4),
                "ci95": [round(float(np.quantile(rs, 0.025)), 4), round(float(np.quantile(rs, 0.975)), 4)]}

    def ks(u):
        u = np.sort(np.asarray(u))
        nn = len(u)
        return round(float(max(np.max(np.arange(1, nn + 1) / nn - u), np.max(u - np.arange(nn) / nn))), 4)

    obs = {}
    for typ, xs in obs_pairs.items():
        arr = np.asarray(xs)
        obs[typ] = {"n": len(xs), "corr": round(float(np.corrcoef(arr[:, 0], arr[:, 1])[0, 1]), 3) if len(xs) > 10 else None}
    res = {
        "meta": {"games": len(games), "worlds": a.n, "window": {k: [min(v), max(v)] for k, v in WINDOW.items()},
                 "weeks_read": {s_: sorted({k[1] for k in games if k[0] == s_}) for s_ in WINDOW},
                 "seconds": round(time.time() - t0, 1),
                 "note": "development comparison; nothing fitted; 2026 week 13+ sealed and never read"},
        "conservation": {"discrete_violations": dict(cons_d),
                         "legacy": {"team_worlds": cons_l["worlds"],
                                    "qb_yards_differ_from_receivers_pct": round(100 * cons_l["yd_gap_gt_half"] / max(cons_l["worlds"], 1), 2),
                                    "qb_tds_differ_from_receivers_pct": round(100 * cons_l["td_mismatch"] / max(cons_l["worlds"], 1), 2),
                                    "mean_abs_yard_gap": round(cons_l["abs_yd_gap"] / max(cons_l["worlds"], 1), 2)}},
        "moments": {s: {"mean_ratio_discrete_over_legacy": boot_ratio(s, 0),
                        "var_ratio_discrete_over_legacy": boot_ratio(s, 1),
                        "spread": {p: {"model_var": round(moments[p][s][1] / max(len(per_game_means[p][s]), 1), 3),
                                       "realized_mse": round(moments[p][s][2] / max(moments[p][s][3], 1), 3),
                                       "realized_over_model": round((moments[p][s][2] / max(moments[p][s][3], 1))
                                                                    / max(moments[p][s][1] / max(len(per_game_means[p][s]), 1), 1e-9), 3)}
                                   for p in paths}} for s in STATS},
        "lines": {p: {s: prob[p][s].out() for s in prob[p]} for p in paths},
        "exact_count_log_score": {p: {"n": exact[p][1], "mean": round(exact[p][0] / max(exact[p][1], 1), 4)} for p in paths},
        "dk": {p: {"n": dk_mom[p][2], "pit_ks": ks(pit[p]), "cover_10_90": round(cover[p][0] / max(cover[p][1], 1), 4),
                   "mean_abs_mean_minus_proj": round(dk_mom[p][0] / max(dk_mom[p][2], 1), 3),
                   "mean_sd": round(dk_mom[p][1] / max(dk_mom[p][2], 1), 3)} for p in paths},
        "correlation": {typ: {"legacy": round(float(np.mean(corr["legacy"][typ])), 3) if corr["legacy"][typ] else None,
                              "discrete": round(float(np.mean(corr["discrete"][typ])), 3) if corr["discrete"][typ] else None,
                              "observed": obs.get(typ)} for typ in sorted(set(corr["legacy"]) | set(corr["discrete"]))},
    }
    to = np.asarray(tdc_obs) if tdc_obs else None
    res["td_reception_corr"] = {
        "definition": "within one receiver's worlds, corr(receptions, receiving TDs), averaged over receivers "
                      "projected 2+ catches; observed: corr of (actual - projection) for both, over player-weeks",
        **{p: round(float(np.mean(tdc[p])), 3) for p in paths},
        "observed": round(float(np.corrcoef(to[:, 0], to[:, 1])[0, 1]), 3) if to is not None else None,
        "observed_n": len(tdc_obs)}
    res["bonus"] = {s: {p: {"n": bonus[p][s]["n"], "pred_rate": round(bonus[p][s]["pred"] / max(bonus[p][s]["n"], 1), 4),
                            "brier": round(bonus[p][s]["brier"] / max(bonus[p][s]["n"], 1), 4),
                            "mass_in_half_yard_below": round(bonus[p][s]["mass_half_below"] / max(bonus[p][s]["n"], 1), 4)}
                        for p in paths} | {"observed_rate": round(bonus["legacy"][s]["obs"] / max(bonus["legacy"][s]["n"], 1), 4)}
                    for s in ("rec_yd", "rush_yd", "pass_yd")}
    res["floor_ceiling"] = {p: {"n": floor_ceil[p]["n"],
                                "actual_below_q10": round(floor_ceil[p]["below_q10"] / max(floor_ceil[p]["n"], 1), 4),
                                "actual_above_q90": round(floor_ceil[p]["above_q90"] / max(floor_ceil[p]["n"], 1), 4),
                                "mean_q10": round(floor_ceil[p]["q10"] / max(floor_ceil[p]["n"], 1), 2),
                                "mean_q90": round(floor_ceil[p]["q90"] / max(floor_ceil[p]["n"], 1), 2),
                                "worlds_on_dk_lattice": round(floor_ceil[p]["on_lattice"] / max(floor_ceil[p]["n"], 1), 4)}
                            for p in paths}
    res["fp_ties"] = {"definition": "share of worlds in which two same-team pass catchers (WR-WR, TE-WR) score "
                                    "exactly the same DraftKings points; observed: share of real pairs",
                      **{p: round(ties[p][0] / max(ties[p][1], 1), 5) for p in paths},
                      "pairs": ties["legacy"][1], "observed": round(ties_obs[0] / max(ties_obs[1], 1), 5),
                      "observed_pairs": ties_obs[1]}
    diffs = [abs(v["legacy"] - v["discrete"]) for v in res["correlation"].values() if v["legacy"] is not None and v["discrete"] is not None]
    res["correlation_max_abs_diff"] = round(max(diffs), 3) if diffs else None
    with open(a.out, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    print(json.dumps({k: res[k] for k in ("meta", "conservation", "exact_count_log_score", "dk", "correlation_max_abs_diff")}, indent=1))
    return res


def _pair_type(a, b):
    pa, pb = a["pos"], b["pos"]
    same = a["team"] == b["team"]
    s = tuple(sorted((pa, pb)))
    if same and "QB" in s and s != ("QB", "QB"):
        return f"QB-{s[0] if s[1] == 'QB' else s[1]} same team"
    if same and s in (("RB", "RB"), ("WR", "WR"), ("TE", "WR"), ("RB", "WR")):
        return f"{s[0]}-{s[1]} same team"
    if not same and s == ("QB", "QB"):
        return "QB-QB opponents"
    if not same and s == ("RB", "RB"):
        return "RB-RB opponents"
    return None


if __name__ == "__main__":
    main()
