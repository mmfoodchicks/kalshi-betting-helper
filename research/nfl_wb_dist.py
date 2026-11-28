"""The registered replacement test for the NFL week board's player distributions (2026-11-27; the owner's
approval; candidates research/data/nfl_wb_dist/candidates.json, committed before any was scored; rule
research/data/nfl_wb_dist/prereg.json; report research/reports/nfl_wb_dist.md).

Production (P) is nfl_dfs_sim.simulate_game, the continuous legacy model the integer-stats study measured
overconfident. Candidates: A the constrained simulator (nfl_dfs_csim, as fitted on 2022-24), B the discrete
allocation with one fitted quarterback term, C production with a per-position x statistic quantile mapping.

    python3 research/nfl_wb_dist.py dev       # fit on 2025 wk 1-12, score on 2026 wk 1-5 (development, seen games)
    python3 research/nfl_wb_dist.py freeze    # refit on all development games -> frozen.json
    python3 research/nfl_wb_dist.py read      # the registered read (refuses before its date or on a changed file)

Every probability is (worlds above the line + 0.5) / (n + 1); every line comes off the projection only; the
randomized PIT's uniform is a hash of the player-week and statistic, so every model is scored on the same draw.
"""
import argparse
import collections
import csv
import gzip
import hashlib
import json
import math
import os
import random
import sys
import time
import zlib

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402

import nfl_dfs_csim as C  # noqa: E402
import nfl_dfs_sim as S  # noqa: E402
import nfl_discrete_alloc as D  # noqa: E402

OUT = os.path.join(_ROOT, "research", "data", "nfl_wb_dist")
TABLES = {"2025": "research/data/player_weeks_2022_2025.csv.gz", "2026": "research/data/player_weeks_2026.csv.gz",
          "2027": "research/data/player_weeks_2027.csv.gz"}
FIT = {"2025": range(1, 13)}
# The 2026 table holds weeks 1-5 only; pinned to them, so a table rebuilt for the read (weeks 1-18) leaves the
# development games exactly as they were. 2026 weeks 6-12 are past but unread here.
DEV_TEST = {"2026": range(1, 6)}
DEV = {"2025": range(1, 13), "2026": range(1, 6)}
SEALED_FROM = {"2026": 13}                   # never read in development (the coaching tests' seal too)
STATS_BY_POS = {"QB": ("pass_yd", "pass_td", "int", "rush_yd", "dk"), "RB": ("rush_yd", "rush_td", "rec", "rec_yd", "dk"),
                "WR": ("rec", "rec_yd", "rec_td", "dk"), "TE": ("rec", "rec_yd", "rec_td", "dk")}
COMP = ("pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int", "rush_att", "rush_yd", "rush_td",
        "rec_tgt", "rec", "rec_yd", "rec_td", "fum_lost")
KNOTS = [j / 20 for j in range(21)]
PRIOR = 20.0
K_GRID = [j / 10 for j in range(11)]
# 2026 weeks 14-18: 80 regular-season games scheduled minus byes; 72 is the planning figure for the power
# (conservative), and the read waits for 2027 weeks 1-6 if fewer than 60 arrive.
PROSPECTIVE_GAMES = 72
PATHS = ("P", "A", "B", "C")
CANDIDATES = ("A", "B", "C")
# The decision rule (prereg.json carries it verbatim; guard WB2 holds the two equal). Holm, one-sided,
# family-wise 5% over three candidates: z > 2.128, 1.960, 1.645 in order of z.
RULE = {"primary": {"holm_z": [2.128, 1.960, 1.645]}, "veto": {"cell_z": 2.0, "cover80_band": [0.70, 0.90]}}
PAIRS = {("QB", "WR"): "QB-WR", ("QB", "TE"): "QB-TE", ("QB", "RB"): "QB-RB", ("WR", "WR"): "WR-WR",
         ("TE", "WR"): "WR-TE", ("RB", "WR"): "RB-WR"}


def _f(x):
    try:
        return float(x) if x not in (None, "") else 0.0
    except ValueError:
        return 0.0


def load(window, allow_sealed=False):
    """{(season, week, game_key): game} for the seasons and weeks in `window`, players QB/RB/WR/TE with a
    projection; the real box beside each (None when not kept)."""
    games = collections.OrderedDict()
    for s in sorted(window):
        path = os.path.join(_ROOT, TABLES[s])
        if not os.path.exists(path):
            continue
        with gzip.open(path, "rt") as fh:
            for r in csv.DictReader(fh):
                if r["season"] != s or int(r["week"]) not in window[s]:
                    continue
                w = int(r["week"])
                assert allow_sealed or not (s in SEALED_FROM and w >= SEALED_FROM[s]), "a sealed week was read"
                if r["has_proj"] != "1" or _f(r["proj_dk"]) <= 0 or r["pos"] not in STATS_BY_POS:
                    continue
                m = {c: _f(r[f"proj_{c}"]) for c in COMP}
                means = {"pass_yd": m["pass_yd"], "pass_td": m["pass_td"], "int": m["pass_int"],
                         "rush_yd": m["rush_yd"], "rush_td": m["rush_td"], "rec": m["rec"], "rec_yd": m["rec_yd"],
                         "rec_td": m["rec_td"], "fum": m["fum_lost"], "pass_att": m["pass_att"],
                         "pass_cmp": m["pass_cmp"], "rec_tgt": m["rec_tgt"], "rush_att": m["rush_att"]}
                act = None
                if r["has_act"] == "1" and r["keep"] == "1":
                    a = {c: _f(r[f"act_{c}"]) for c in COMP}
                    act = {"pass_yd": a["pass_yd"], "pass_td": a["pass_td"], "int": a["pass_int"],
                           "rush_yd": a["rush_yd"], "rush_td": a["rush_td"], "rec": a["rec"], "rec_yd": a["rec_yd"],
                           "rec_td": a["rec_td"], "rec_tgt": a["rec_tgt"], "rush_att": a["rush_att"],
                           "dk": _f(r["act_dk"])}
                key = (s, w, r["game_key"])
                g = games.setdefault(key, {"players": [], "teams": [], "label": r["game_key"]})
                g["players"].append({"name": r["player_id"], "pos": r["pos"], "team": r["team"], "opp": r["opp"],
                                     "means": means, "proj_pts": _f(r["proj_dk"]), "_act": act})
                if r["team"] not in g["teams"]:
                    g["teams"].append(r["team"])
    return collections.OrderedDict((k, g) for k, g in sorted(games.items()) if len(g["teams"]) == 2)


def _mean(p, s):
    return p["proj_pts"] if s == "dk" else p["means"][s]


def applies(p, s):
    m = _mean(p, s)
    return m >= (5 if s.endswith("_yd") else 0.5 if s == "rec" else 1 if s == "dk" else 0.05)


def lines(s, m):
    if s.endswith("_yd"):
        out = {round(m * f) + 0.5 for f in (0.5, 0.75, 1, 1.25, 1.5)}
    elif s == "rec":
        out = {math.floor(m * f) + 0.5 for f in (0.5, 0.75, 1, 1.25, 1.5)}
    elif s == "pass_td":
        out = {0.5, 1.5, 2.5}
    elif s == "dk":
        out = {round(m * f) + 0.25 for f in (0.5, 1, 1.5)}
    else:
        out = {0.5}
    return sorted(x for x in out if x > 0)


def _uniform(key, name, s):
    """The randomized PIT's uniform: a hash, identical for every model."""
    return ((zlib.crc32(f"{key}|{name}|{s}".encode()) % 1_000_000) + 0.5) / 1_000_000


def pit(x, a, v):
    lo, hi = float((x < a).mean()), float((x <= a).mean())
    return lo + v * (hi - lo)


# ---- the models' worlds ------------------------------------------------------------------------------
def _stats(comps, dk):
    return {s: np.asarray(comps[s] if s != "dk" else dk, dtype=np.float64) for s in
            ("pass_yd", "pass_td", "int", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td", "dk")}


def worlds_P(g, n, seed):
    random.seed(seed)
    sim = S.simulate_game(g, n=n, with_components=True, with_samples=True)
    by = {r["name"]: r for r in sim["players"]}
    return [_stats({k: v for k, v in by[p["name"]]["comps"].items()}, by[p["name"]]["arr"]) for p in g["players"]]


def worlds_A(g, n, seed):
    sim = C.simulate_game(g, n=n, rng=np.random.default_rng(seed))
    out = []
    for i, p in enumerate(g["players"]):
        c = dict(sim["components"][i])
        for k in ("pass_yd", "pass_td", "int", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td"):
            c.setdefault(k, np.zeros(n))
        out.append(_stats(c, sim["players"][i]["arr"]))
    return out


def worlds_B(g, n, seed, k):
    sim = D.simulate_game(g, n=n, rng=np.random.default_rng(seed), pass_latent_scale=k)
    return [_stats(r["comps"], r["dk"]) for r in sim["players"]]


def g_values(us):
    """The quantile map's knots off a cell's development PITs (monotone; shrunk toward the identity)."""
    u = np.sort(np.asarray(us, dtype=np.float64))
    N = len(u)
    return [0.0] + [(float(np.searchsorted(u, t, side="right")) + PRIOR * t) / (N + PRIOR) for t in KNOTS[1:-1]] + [1.0]


def worlds_C(wP, g, gmap):
    out = []
    for p, st in zip(g["players"], wP):
        row = {}
        for s, x in st.items():
            G = gmap.get(f"{p['pos']}|{s}")
            if G is None:
                row[s] = x
                continue
            n = len(x)
            order = np.argsort(x, kind="stable")
            u = np.empty(n)
            u[order] = (np.arange(n) + 0.5) / n
            lev = np.interp(u, G, KNOTS)                     # G^-1
            row[s] = np.sort(x)[np.minimum(n - 1, (lev * n).astype(np.int64))]
        out.append(row)
    return out


# ---- scoring -----------------------------------------------------------------------------------------
class Cell:
    def __init__(self):
        self.n = self.lines = 0
        self.ll = self.br = self.bias = self.sq = self.var = 0.0
        self.bins = [[0, 0.0, 0] for _ in range(10)]
        self.cov = {50: 0, 80: 0, 90: 0}
        self.below10 = self.above90 = 0
        self.p0_m = self.p2_m = 0.0
        self.z_a = self.two_a = 0

    def out(self):
        n, L = max(self.n, 1), max(self.lines, 1)
        return {"n": self.n, "lines": self.lines, "log_loss": round(self.ll / L, 5), "brier": round(self.br / L, 5),
                "mean_bias": round(self.bias / n, 3), "realized_over_model_var": round(self.sq / max(self.var, 1e-9), 3),
                "cover": {str(k): round(v / n, 4) for k, v in self.cov.items()},
                "under_floor": round(self.below10 / n, 4), "over_ceiling": round(self.above90 / n, 4),
                "p0_model": round(self.p0_m / n, 4), "p0_real": round(self.z_a / n, 4),
                "p2plus_model": round(self.p2_m / n, 4), "p2plus_real": round(self.two_a / n, 4),
                "calibration": [{"bin": f"{10 * i}-{10 * i + 10}%", "n": b[0], "mean_p": round(b[1] / b[0], 4) if b[0] else None,
                                 "hit": round(b[2] / b[0], 4) if b[0] else None} for i, b in enumerate(self.bins)]}


def score_games(games, paths, n, gmap=None, k=None, want_corr=True):
    """Every path scored on the same games, lines and PIT draws. Returns the pooled cells, the per-game sums
    the primary statistic clusters on, the pair correlations and the observed ones."""
    cells = {p: collections.defaultdict(Cell) for p in paths}
    per_game = {p: [] for p in paths}                   # [(ll_sum, lines, {cell: (ll, lines)})]
    corr = {p: collections.defaultdict(list) for p in paths}
    obs = collections.defaultdict(list)
    for gi, (key, g) in enumerate(games.items()):
        seed = 1000 + gi
        W = {}
        if "P" in paths or "C" in paths:
            W["P"] = worlds_P(g, n, seed)
        if "A" in paths:
            W["A"] = worlds_A(g, n, seed)
        if "B" in paths:
            W["B"] = worlds_B(g, n, seed, k)
        if "C" in paths:
            W["C"] = worlds_C(W["P"], g, gmap)
        for p in paths:
            tot, nl, bycell = 0.0, 0, collections.defaultdict(lambda: [0.0, 0])
            for i, pl in enumerate(g["players"]):
                a = pl["_act"]
                if a is None:
                    continue
                for s in STATS_BY_POS[pl["pos"]]:
                    if not applies(pl, s):
                        continue
                    x = W[p][i][s]
                    c = cells[p][f"{pl['pos']}|{s}"]
                    av = a[s]
                    mu = float(x.mean())
                    c.n += 1
                    c.bias += av - mu
                    c.sq += (av - mu) ** 2
                    c.var += float(x.var())
                    u = pit(x, av, _uniform(key, pl["name"], s))
                    for lvl in c.cov:
                        c.cov[lvl] += int(abs(u - 0.5) <= lvl / 200)
                    c.below10 += int(u < 0.1)
                    c.above90 += int(u > 0.9)
                    c.p0_m += float((np.abs(x) < 0.5).mean())
                    c.z_a += int(abs(av) < 0.5)
                    c.p2_m += float((x >= 1.5).mean())
                    c.two_a += int(av >= 1.5)
                    for L in lines(s, _mean(pl, s)):
                        q = (float((x > L).sum()) + 0.5) / (len(x) + 1)
                        y = 1 if av > L else 0
                        ll = -math.log(q if y else 1 - q)
                        c.ll += ll
                        c.br += (q - y) ** 2
                        c.lines += 1
                        b = c.bins[min(9, int(q * 10))]
                        b[0] += 1
                        b[1] += q
                        b[2] += y
                        tot += ll
                        nl += 1
                        bycell[f"{pl['pos']}|{s}"][0] += ll
                        bycell[f"{pl['pos']}|{s}"][1] += 1
            per_game[p].append((tot, nl, dict(bycell)))
        if want_corr:
            pl = g["players"]
            for i in range(len(pl)):
                for j in range(i + 1, len(pl)):
                    a_, b_ = pl[i], pl[j]
                    same = a_["team"] == b_["team"]
                    pos = tuple(sorted((a_["pos"], b_["pos"])))
                    typ = PAIRS.get(pos) if same else ("QB-QB opp" if pos == ("QB", "QB") else None)
                    if typ is None:
                        continue
                    for p in paths:
                        xa, xb = W[p][i]["dk"], W[p][j]["dk"]
                        if xa.std() > 0 and xb.std() > 0:
                            corr[p][typ].append(float(np.corrcoef(xa, xb)[0, 1]))
                    if a_["_act"] is not None and b_["_act"] is not None:
                        obs[typ].append((a_["_act"]["dk"] - a_["proj_pts"], b_["_act"]["dk"] - b_["proj_pts"]))
    return cells, per_game, corr, obs


def ratio(dg):
    """The pooled per-line gain sum(d) / sum(m) and its game-cluster SE (ratio estimator)."""
    G = len(dg)
    M = sum(m for _, m in dg)
    if G < 2 or M == 0:
        return None, None
    Dv = sum(d for d, _ in dg) / M
    se = math.sqrt(sum((d - Dv * m) ** 2 for d, m in dg) * G / (G - 1)) / M
    return Dv, se


def compare(per_game, base="P"):
    """Each candidate against production: the primary (all lines) and each position x statistic cell."""
    out = {}
    for c in per_game:
        if c == base:
            continue
        pairs = list(zip(per_game[base], per_game[c]))
        Dv, se = ratio([(b[0] - x[0], b[1]) for b, x in pairs])
        cellz = {}
        for cell in sorted({k for b, _ in pairs for k in b[2]}):
            dg = [(b[2].get(cell, (0.0, 0))[0] - x[2].get(cell, (0.0, 0))[0], b[2].get(cell, (0.0, 0))[1])
                  for b, x in pairs if cell in b[2]]
            d, s = ratio(dg)
            cellz[cell] = {"gain_per_line": round(d, 5) if d is not None else None,
                           "se": round(s, 5) if s else None, "z": round(d / s, 2) if s else None}
        out[c] = {"gain_per_line": round(Dv, 5), "se": round(se, 5), "z": round(Dv / se, 2), "games": len(pairs),
                  "cells": cellz}
    return out


def corr_out(corr, obs):
    res = {}
    for typ in sorted(set(obs) | {t for p in corr for t in corr[p]}):
        o = np.asarray(obs.get(typ) or [])
        res[typ] = {p: round(float(np.mean(corr[p][typ])), 3) if corr[p][typ] else None for p in corr}
        res[typ]["observed"] = round(float(np.corrcoef(o[:, 0], o[:, 1])[0, 1]), 3) if len(o) > 2 else None
        res[typ]["observed_pairs"] = len(o)
    return res


# ---- fitting (development only) ----------------------------------------------------------------------
def fit_gmap(games, n):
    us = collections.defaultdict(list)
    for gi, (key, g) in enumerate(games.items()):
        wP = worlds_P(g, n, 1000 + gi)
        for i, pl in enumerate(g["players"]):
            if pl["_act"] is None:
                continue
            for s in STATS_BY_POS[pl["pos"]]:
                if applies(pl, s):
                    us[f"{pl['pos']}|{s}"].append(pit(wP[i][s], pl["_act"][s], _uniform(key, pl["name"], s)))
    return {cell: [round(v, 6) for v in g_values(u)] for cell, u in sorted(us.items())}, {c: len(u) for c, u in us.items()}


def fit_k(games, n):
    """B's one term: the k whose quarterbacks' passing-yard realized-over-model variance is closest to 1."""
    rows = []
    for k in K_GRID:
        sq = var = 0.0
        for gi, (key, g) in enumerate(games.items()):
            wB = worlds_B(g, n, 1000 + gi, k)
            for i, pl in enumerate(g["players"]):
                if pl["pos"] == "QB" and pl["_act"] is not None and applies(pl, "pass_yd"):
                    x = wB[i]["pass_yd"]
                    sq += (pl["_act"]["pass_yd"] - float(x.mean())) ** 2
                    var += float(x.var())
        rows.append({"k": k, "realized_over_model": round(sq / max(var, 1e-9), 4)})
    best = min(rows, key=lambda r: (abs(r["realized_over_model"] - 1), -r["k"]))
    return best["k"], rows


def workload(games):
    """Sleeper's projected workload against the real one, and the shape of the real residuals (descriptive)."""
    acc = collections.defaultdict(lambda: collections.defaultdict(list))
    for g in games.values():
        for pl in g["players"]:
            a = pl["_act"]
            if a is None:
                continue
            for s, ps in (("rec_tgt", "rec_tgt"), ("rush_att", "rush_att")):
                if pl["means"][ps] >= 1:
                    acc[pl["pos"]][s].append((pl["means"][ps], a[s]))
            for s in STATS_BY_POS[pl["pos"]]:
                if applies(pl, s):
                    acc[pl["pos"]]["resid|" + s].append((_mean(pl, s), a[s]))
    out = {}
    for pos, d in sorted(acc.items()):
        out[pos] = {}
        for s, rows in sorted(d.items()):
            m = np.asarray(rows, dtype=np.float64)
            r = m[:, 1] - m[:, 0]
            rec = {"n": len(m), "mean_proj": round(float(m[:, 0].mean()), 2), "mean_real": round(float(m[:, 1].mean()), 2),
                   "bias_real_minus_proj": round(float(r.mean()), 3),
                   "resid_skew": round(float(((r - r.mean()) ** 3).mean() / max(r.std(), 1e-9) ** 3), 3),
                   "real_zero_share": round(float((np.abs(m[:, 1]) < 0.5).mean()), 4)}
            if not s.startswith("resid|"):
                rec["dispersion_index"] = round(float((r ** 2).mean() / m[:, 0].mean()), 3)   # Poisson = 1
            out[pos][s] = rec
    return out


def _sha(path):
    with open(os.path.join(_ROOT, path), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def power(cmp, games_dev, games_pro, z_crit):
    """Normal approximation: the development out-of-sample gain and its SE scaled to the prospective games."""
    out = {}
    for c, r in cmp.items():
        se_pro = r["se"] * math.sqrt(games_dev / games_pro)
        for lab, eff in (("at_dev_gain", r["gain_per_line"]), ("at_half", r["gain_per_line"] / 2)):
            z = eff / se_pro
            out.setdefault(c, {})[lab] = round(0.5 * (1 + math.erf((z - z_crit[c]) / math.sqrt(2))), 3)
    return out


# ---- modes -------------------------------------------------------------------------------------------
def dev(n):
    t0 = time.time()
    fit_games, test_games = load(FIT), load(DEV_TEST)
    gmap, gn = fit_gmap(fit_games, n)
    k, kgrid = fit_k(fit_games, max(500, n // 2))
    cells, per_game, corr, obs = score_games(test_games, PATHS, n, gmap=gmap, k=k)
    cmp = compare(per_game)
    cor = corr_out(corr, obs)
    cells_in, per_in, _, _ = score_games(fit_games, PATHS, n, gmap=gmap, k=k, want_corr=False)
    res = {"meta": {"mode": "dev", "fit": "2025 weeks 1-12", "scored": "2026 weeks 1-5 (out of sample for every fit)",
                    "fit_games": len(fit_games), "test_games": len(test_games), "worlds": n,
                    "note": "development: these games were already read by the integer-stats study",
                    "seconds": round(time.time() - t0, 1)},
           "fitted_on_2025": {"k": k, "k_grid": kgrid, "gmap_cells": gn},
           "out_of_sample": {"compare": cmp, "cells": {p: {c: v.out() for c, v in sorted(cells[p].items())} for p in cells},
                             "correlation": cor,
                             # the registered rule applied to development, for reading only: it decides nothing
                             "rule_on_development": decide(cmp, cells, cor, RULE),
                             "power": power(cmp, len(test_games), PROSPECTIVE_GAMES, {c: RULE["primary"]["holm_z"][0]
                                                                                       for c in CANDIDATES})},
           "in_sample_2025": {"compare": compare(per_in),
                              "cells": {p: {c: v.out() for c, v in sorted(cells_in[p].items())} for p in cells_in}},
           "workload_and_shape": workload(collections.OrderedDict(list(fit_games.items()) + list(test_games.items())))}
    return res


def freeze(n):
    games = load(DEV)
    gmap, gn = fit_gmap(games, n)
    k, kgrid = fit_k(games, max(500, n // 2))
    body = {"gmap": gmap, "gmap_n": gn, "k": k, "k_grid": kgrid, "knots": KNOTS, "prior": PRIOR,
            "fitted_on": "2025 weeks 1-12 and 2026 weeks 1-5 (all development games)", "games": len(games), "worlds": n}
    body["hash"] = hashlib.sha256(json.dumps({x: body[x] for x in ("gmap", "k", "knots", "prior")}, sort_keys=True).encode()).hexdigest()
    return body


def read(now=None):
    reg = json.load(open(os.path.join(OUT, "prereg.json")))
    now = now or time.time()
    if now < reg["read"]["not_before_ts"]:
        return {"refused": f"not before {reg['read']['not_before_utc']}"}
    bad = [p for p, h in reg["frozen_sha256"].items() if _sha(p) != h]
    if bad:
        return {"refused": f"a frozen file changed: {bad}"}
    fz = json.load(open(os.path.join(OUT, "frozen.json")))
    window = {s: range(a, b + 1) for s, (a, b) in reg["sample"]["window"].items()}
    games = load(window, allow_sealed=True)
    if len(games) < reg["sample"]["n_min_games"]:
        ext = {s: range(a, b + 1) for s, (a, b) in reg["sample"]["extension"].items()}
        games = load({**window, **ext}, allow_sealed=True)
        if len(games) < reg["sample"]["n_min_games"]:
            return {"refused": f"{len(games)} games, fewer than {reg['sample']['n_min_games']}: wait for the extension"}
    cells, per_game, corr, obs = score_games(games, PATHS, reg["worlds"], gmap=fz["gmap"], k=fz["k"])
    cmp = compare(per_game)
    cor = corr_out(corr, obs)
    verdict = decide(cmp, cells, cor, {"primary": reg["primary_rule"], "veto": reg["veto"]})
    return {"games": len(games), "compare": cmp, "verdict": verdict,
            "cells": {p: {c: v.out() for c, v in sorted(cells[p].items())} for p in cells},
            "correlation": cor, "read_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}


def cover80(cells, path):
    """80% central-interval coverage pooled over every scored position x statistic (weighted by player-weeks)."""
    cs = list(cells[path].values())
    n = sum(c.n for c in cs)
    return sum(c.cov[80] for c in cs) / max(n, 1)


def corr_mae(cor, path):
    vals = [abs(v[path] - v["observed"]) for v in cor.values() if v.get(path) is not None and v.get("observed") is not None]
    return sum(vals) / len(vals) if vals else None


def decide(cmp, cells, cor, reg):
    """The registered decision: Holm on the primary (one-sided, family-wise 5%, candidates in order of z), then
    the three vetoes -- no position x statistic cell worse than production by more than 2 SE; pooled 80% coverage
    inside the band AND nearer 0.80 than production's; pair correlations no further from the observed than
    production's (mean absolute difference over the pair types)."""
    order = sorted(CANDIDATES, key=lambda c: -cmp[c]["z"])
    cP, mP = cover80(cells, "P"), corr_mae(cor, "P")
    verdict, alive = {}, True
    for rank, c in enumerate(order):
        thr = reg["primary"]["holm_z"][rank]
        ok_primary = alive and cmp[c]["z"] > thr
        alive = ok_primary
        veto = [cell for cell, v in cmp[c]["cells"].items() if v["z"] is not None and v["z"] < -reg["veto"]["cell_z"]]
        cv, mc = cover80(cells, c), corr_mae(cor, c)
        lo, hi = reg["veto"]["cover80_band"]
        cov_ok = lo <= cv <= hi and abs(cv - 0.8) < abs(cP - 0.8)
        cor_ok = mc is not None and mP is not None and mc <= mP
        verdict[c] = {"z": cmp[c]["z"], "holm_threshold": thr, "primary_pass": ok_primary, "cells_worse_2se": veto,
                      "cover80": round(cv, 4), "cover80_production": round(cP, 4), "cover_ok": cov_ok,
                      "corr_mae": round(mc, 4) if mc is not None else None,
                      "corr_mae_production": round(mP, 4) if mP is not None else None, "corr_ok": cor_ok,
                      "passes": ok_primary and not veto and cov_ok and cor_ok}
    passing = [c for c in order if verdict[c]["passes"]]
    verdict["recommended"] = passing[0] if passing else None
    return verdict


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("dev", "freeze", "read"))
    ap.add_argument("--n", type=int, default=2000)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.mode == "dev":
        res, name = dev(a.n), "dev.json"
    elif a.mode == "freeze":
        res, name = freeze(a.n), "frozen.json"
    else:
        res, name = read(), "read.json"
    with open(os.path.join(OUT, name), "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    print(json.dumps(res.get("out_of_sample", res).get("compare", res) if isinstance(res, dict) else res, indent=1)[:3000])
