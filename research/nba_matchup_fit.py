"""The NBA matchup engine's fit and its held-out test (RESEARCH; numpy).

  stage 1  pass 1 of the walk: every player-game's own as-of sums; measure the
           reliability of every concession channel, role share and (after
           pass 2) observed-over-expected (split-half, fit seasons only) and
           the year-over-year persistence; fit the baseline rate weights, the
           minutes model, its residual distribution and each market's
           dispersion -- all on the FIT seasons (2022-23, 2023-24).
  stage 2  pass 2: the as-of features of every rung for every row (2022-23 ..
           2025-26), the comparable-opponent scores.
  stage 3  the ladder: each rung's coefficients fitted on the fit seasons;
           2024-25 chooses (pre-registered rule, research/reports/nba_matchup.md);
           2025-26 is read ONCE by `--test`.

  python3 research/nba_matchup_fit.py stage1 <store dir> <work dir>
  python3 research/nba_matchup_fit.py stage2 <store dir> <work dir>
  python3 research/nba_matchup_fit.py validate <work dir>
  python3 research/nba_matchup_fit.py test <work dir>
  python3 research/nba_matchup_fit.py freeze <work dir>     # writes research/data/nba_matchup/

Seasons are named by their start year (2024 = 2024-25). 2021-22 is read as
HISTORY only (the prior season of 2022-23's as-of profiles); no row of it is
fitted or scored.
"""
import json
import math
import os
import pickle
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import nba_matchup_data as D            # noqa: E402
import nba_matchup_profiles as M        # noqa: E402
from research import nba_matchup_walk as W   # noqa: E402

FIT = (2022, 2023)
VAL = (2024,)
TEST = (2025,)
HIST = (2021,)
ALL = HIST + FIT + VAL + TEST
MARKETS = ("pts", "reb", "ast", "tpm")
COMBOS = {"pra": ("pts", "reb", "ast"), "pr": ("pts", "reb"), "pa": ("pts", "ast"), "ra": ("reb", "ast")}
# The alt thresholds the ladder scores (Kalshi's own ladders, read off the open
# books 2026-10-23: points 10+ .. 45+ by 5, assists 2+ .. 14+ by 2, threes
# 1+ .. 6+, PRA 15+ .. 55+ by 5; rebounds by 2 like assists).
THRESH = {"pts": (10, 15, 20, 25, 30, 35, 40, 45), "reb": (2, 4, 6, 8, 10, 12, 14, 16),
          "ast": (2, 4, 6, 8, 10, 12, 14), "tpm": (1, 2, 3, 4, 5, 6),
          "pra": (15, 20, 25, 30, 35, 40, 45, 50, 55), "pr": (10, 15, 20, 25, 30, 35, 40, 45),
          "pa": (10, 15, 20, 25, 30, 35, 40, 45), "ra": (4, 6, 8, 10, 12, 14, 16, 18, 20),
          # steals / blocks / turnovers: only their dispersion is fitted on these (they feed dk_fp)
          "stl": (1, 2, 3, 4), "blk": (1, 2, 3, 4), "tov": (1, 2, 3, 4, 5)}
OUT_DIR = os.path.join(ROOT, "research", "data", "nba_matchup")


def log(*a):
    print(*a, flush=True)


def _stat(row, s):
    if s == "reb":
        return row[D.PI["oreb"]] + row[D.PI["dreb"]]
    return row[D.PI[s]]


# ---- pass 1 -------------------------------------------------------------------
def pass1(store_dir, work):
    """Rows: own as-of sums of every RATE stat, minutes features, actuals."""
    entries = W.load_entries(store_dir, ALL)
    log(f"[pass1] {len(entries)} games")
    rows = []
    stats = M.RATE_STATS

    def on_date(book, day, games):
        for g in games:
            for pid, r in g["players"].items():
                if not r[D.PI["played"]]:
                    continue
                sn = book.player(pid, day)
                if sn is None:
                    continue
                side = r[D.PI["side"]]
                rec = {"gid": g["id"], "pid": pid, "day": day, "season": g["season"], "side": side,
                       "team": (g["home"], g["away"])[side], "opp": (g["home"], g["away"])[1 - side],
                       "pos": sn["pos"] if sn["pos"] >= 0 else r[D.PI["pos_g"]],
                       "min": r[D.PI["min"]], "starter": r[D.PI["starter"]],
                       "y": {s: _stat(r, s) for s in stats},
                       "g_hist": sn["cur"][M.PS["g"]] + sn["prev"][M.PS["g"]],
                       "mf": M.minutes_features(sn),
                       "missed_last": 1.0 if (book.teams.get(sn["team"]) and sn["team"] == (g["home"], g["away"])[side]
                                              and book.teams[sn["team"]].tg - sn["last_tg"] >= 1) else 0.0,
                       "traded": 1.0 if sn["team"] != (g["home"], g["away"])[side] else 0.0}
                own = {}
                lp = sn.get("lg_pos") if sn.get("lg_pos") and sn["lg_pos"][M.PS["min"]] > 0 else sn.get("lg_pos_cur")
                for s in stats + ("min",):
                    if s == "min":
                        own[s] = (sn["cur"][M.PS["min"]], sn["prev"][M.PS["min"]], sn["rec"][M.PS["min"]])
                    else:
                        own[s] = (M._stat_val(sn["cur"], s), M._stat_val(sn["prev"], s), M._stat_val(sn["rec"], s))
                rec["own"] = own
                rec["prior"] = {s: (M._stat_val(lp, s) / lp[M.PS["min"]] if lp and lp[M.PS["min"]] > 0 else
                                    M.LEAGUE_PER_MIN[s]) for s in stats}
                rows.append(rec)
        return None

    W.walk(entries, on_date, log=log)
    log(f"[pass1] {len(rows)} rows")
    with open(os.path.join(work, "pass1.pkl"), "wb") as fh:
        pickle.dump(rows, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return rows


# ---- reliability -----------------------------------------------------------------
def _split_half_K(units):
    """units: [(num_list, den_list)] per unit (team-season / player-season),
    each a per-game list. Split games odd/even; the across-unit correlation
    of the half rates is the half's reliability r; K = mean half denominator
    x (1 - r) / r (Spearman-Brown's noise-to-signal in denominator units)."""
    a, b, dens = [], [], []
    for num, den in units:
        n0, d0 = sum(num[0::2]), sum(den[0::2])
        n1, d1 = sum(num[1::2]), sum(den[1::2])
        if d0 <= 0 or d1 <= 0:
            continue
        a.append(n0 / d0)
        b.append(n1 / d1)
        dens.append((d0 + d1) / 2.0)
    if len(a) < 10:
        return None, None, len(a)
    a, b = np.array(a), np.array(b)
    r = float(np.corrcoef(a, b)[0, 1])
    md = float(np.mean(dens))
    if r <= 0.02:
        return md * 50.0, r, len(a)
    return md * (1.0 - r) / r, r, len(a)


def measure_team(store_dir):
    """team_K (per channel, def and off side) and rho (year-over-year slope of
    the log ratio) from the fit seasons (2022-23, 2023-24; 2021-22 only as the
    year before 2022-23 for rho)."""
    by_ts = {}
    for s in HIST + FIT:
        for e in W.load_entries(store_dir, (s,)):
            for side in (0, 1):
                ab = (e["home"], e["away"])[side]
                own, opp = e["teams"][side], e["teams"][1 - side]
                by_ts.setdefault((ab, s), []).append((own, opp))
    K, rho, rel = {}, {}, {}
    for side_name in ("def", "off"):
        for name, num, den, _k, _b in M.CONC_DEFS:
            units = []
            full = {}
            for (ab, s), games in by_ts.items():
                pick = (lambda g: g[1]) if side_name == "def" else (lambda g: g[0])
                nums = [pick(g)[D.TI[num]] for g in games]
                if den == "secs":
                    dens = [pick(g)[D.TI[den]] / 2880.0 for g in games]
                else:
                    dens = [pick(g)[D.TI[den]] for g in games]
                if s in FIT:
                    units.append((nums, dens))
                full[(ab, s)] = (sum(nums), sum(dens))
            k, r, n = _split_half_K(units)
            if den == "secs":
                k = k * 100.0 if k else k        # back to possession-ish units (poss per 48 x games)
            key = f"{side_name}:{name}"
            K[key] = round(k, 1) if k else 400.0
            rel[key] = {"split_half_r": None if r is None else round(r, 3), "units": n}
            # persistence: slope of ln(ratio_t) on ln(ratio_{t-1}) over FIT pairs
            xs, ys = [], []
            for s in FIT:
                lg_t = sum(v[0] for (a_, s_), v in full.items() if s_ == s) / max(1e-9, sum(v[1] for (a_, s_), v in full.items() if s_ == s))
                lg_p = sum(v[0] for (a_, s_), v in full.items() if s_ == s - 1) / max(1e-9, sum(v[1] for (a_, s_), v in full.items() if s_ == s - 1))
                for (ab, s_), v in full.items():
                    if s_ != s or (ab, s - 1) not in full:
                        continue
                    pv = full[(ab, s - 1)]
                    if v[1] <= 0 or pv[1] <= 0 or v[0] <= 0 or pv[0] <= 0:
                        continue
                    ys.append(math.log(v[0] / v[1] / lg_t))
                    xs.append(math.log(pv[0] / pv[1] / lg_p))
            if len(xs) >= 10 and np.var(xs) > 0:
                slope = float(np.cov(xs, ys)[0, 1] / np.var(xs, ddof=1))
                rho[key] = round(min(1.0, max(0.0, slope)), 3)
            else:
                rho[key] = 0.5
    return K, rho, rel


def measure_roles(store_dir):
    """role_K: split-half reliability of each role share across player-seasons
    with 400+ minutes (fit seasons only)."""
    acc = {}
    for s in FIT:
        for e in W.load_entries(store_dir, (s,)):
            for pid, r in e["players"].items():
                if not r[D.PI["played"]]:
                    continue
                acc.setdefault((pid, s), []).append(r)
    K, rel = {}, {}
    for name, num, den, _k, _b in M.ROLE_DEFS:
        units = []
        for key, games in acc.items():
            if sum(g[D.PI["min"]] for g in games) < 400:
                continue
            arrs = []
            for g in games:
                a = [0.0] * len(M.PSUM)
                for k_, src in enumerate(M._PSRC):
                    if src >= 0:
                        a[k_] = g[src]
                a[M.PS["g"]] = 1.0
                arrs.append(a)
            units.append(([M._expr(a, num) for a in arrs], [M._expr(a, den) for a in arrs]))
        k, r, n = _split_half_K(units)
        K[name] = round(k, 1) if k else 50.0
        rel[name] = {"split_half_r": None if r is None else round(r, 3), "units": n}
    return K, rel


# ---- baseline rate weights -------------------------------------------------------
def fit_rate_weights(rows):
    """(a, b, K) per stat: minimize the Poisson deviance of rate x actual
    minutes over the fit seasons' rows (players with 1+ prior game)."""
    fit = [r for r in rows if r["season"] in FIT and r["min"] >= 1]
    m = np.array([r["min"] for r in fit], float)
    out = {}
    grid_a = (0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
    grid_b = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0)
    grid_K = (5.0, 15.0, 30.0, 60.0, 120.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0)
    mc = np.array([r["own"]["min"][0] for r in fit])
    mp_ = np.array([r["own"]["min"][1] for r in fit])
    mr = np.array([r["own"]["min"][2] for r in fit])
    for s in M.RATE_STATS:
        y = np.array([r["y"][s] for r in fit], float)
        xc = np.array([r["own"][s][0] for r in fit])
        xp = np.array([r["own"][s][1] for r in fit])
        xr = np.array([r["own"][s][2] for r in fit])
        pr = np.array([r["prior"][s] for r in fit])
        best = None
        for a in grid_a:
            for b in grid_b:
                num = xc + a * xp + b * xr
                den = mc + a * mp_ + b * mr
                for K in grid_K:
                    mu = (num + K * pr) / (den + K) * m
                    mu = np.maximum(mu, 1e-6)
                    dev = float(np.mean(mu - y * np.log(mu)))
                    if best is None or dev < best[0]:
                        best = (dev, a, b, K)
        out[s] = [best[1], best[2], best[3]]
        log(f"[rates] {s}: a={best[1]} b={best[2]} K={best[3]} dev={best[0]:.5f}")
    return out


# ---- minutes model ------------------------------------------------------------------
MIN_KEYS = ("e2", "e6", "e15", "avg_cur", "avg_prev", "has_prev", "st_e", "b2b", "long_rest", "g_small",
            "missed_last", "traded")


def min_x(r):
    f = r["mf"]
    return [1.0, f["e2"], f["e6"], f["e15"], f["avg_cur"], f["avg_prev"] * f["has_prev"], f["has_prev"], f["st_e"],
            f["b2b"], f["long_rest"], min(f["g_cur"], 10.0) / 10.0, r["missed_last"], r["traded"]]


def fit_minutes(rows, extra=None):
    fit = [r for r in rows if r["season"] in FIT]
    X = np.array([min_x(r) + (extra(r) if extra else []) for r in fit])
    y = np.array([r["min"] for r in fit], float)
    lam = 1.0
    beta = np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ y)
    pred = X @ beta
    resid = y - pred
    log(f"[minutes] RMSE {np.sqrt(np.mean(resid ** 2)):.3f} MAE {np.mean(np.abs(resid)):.3f} on {len(y)} fit rows")
    return beta.tolist()


MIN_BANDS = (0.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0, 36.0, 99.0)
MIN_Q = 20


def minutes_dist(rows, beta, extra=None):
    """Per band of projected minutes: MIN_Q equal-mass nodes of actual minutes
    / projected (conditional on entering the game: what Kalshi settles on)."""
    fit = [r for r in rows if r["season"] in FIT]
    X = np.array([min_x(r) + (extra(r) if extra else []) for r in fit])
    pred = np.clip(X @ np.array(beta), 1.0, 46.0)
    y = np.array([r["min"] for r in fit], float)
    out = []
    for lo, hi in zip(MIN_BANDS[:-1], MIN_BANDS[1:]):
        sel = (pred >= lo) & (pred < hi)
        ratio = y[sel] / pred[sel]
        qs = [(i + 0.5) / MIN_Q for i in range(MIN_Q)]
        nodes = np.quantile(ratio, qs).tolist() if sel.sum() >= 50 else [1.0] * MIN_Q
        out.append({"lo": lo, "hi": hi, "n": int(sel.sum()), "nodes": [round(x, 4) for x in nodes]})
    return out


def pred_minutes(r, beta, extra=None):
    x = min_x(r) + (extra(r) if extra else [])
    return float(min(46.0, max(1.0, sum(b * v for b, v in zip(beta, x)))))


def stage1(store_dir, work):
    os.makedirs(work, exist_ok=True)
    t0 = time.time()
    rows = pass1(store_dir, work)
    log(f"[stage1] pass1 {time.time() - t0:.0f}s")
    team_K, rho, team_rel = measure_team(store_dir)
    role_K, role_rel = measure_roles(store_dir)
    W_ = fit_rate_weights(rows)
    beta = fit_minutes(rows)
    mdist = minutes_dist(rows, beta)
    params = {"team_K": team_K, "rho": rho, "team_reliability": team_rel, "role_K": role_K,
              "role_reliability": role_rel, "rate_W": W_, "min_beta": beta, "min_keys": list(MIN_KEYS),
              "min_dist": mdist, "fit_seasons": list(FIT), "built": time.time()}
    with open(os.path.join(work, "params1.json"), "w") as fh:
        json.dump(params, fh, indent=1)
    log(f"[stage1] done {time.time() - t0:.0f}s")
    return params




# ---- pass 2 / pass 3: the features ---------------------------------------------------
import nba_matchup as NMP                # noqa: E402

E_STATS = NMP.E_STATS
expected_for = NMP.expected_for


def pass2(store_dir, work, P):
    """Fill the defenses' observed-over-expected; collect per-game A/E by
    defense for the reliability of oe (fit seasons)."""
    entries = W.load_entries(store_dir, ALL)
    per_def = {}

    def on_date(book, day, games):
        ex = {}
        for g in games:
            ex_g = {}
            for pid, r in g["players"].items():
                if not r[D.PI["played"]]:
                    continue
                sn = book.player(pid, day)
                if sn is None:
                    continue
                ex_g[pid] = expected_for(r, sn, P)
                side = r[D.PI["side"]]
                dfn = (g["home"], g["away"])[1 - side]
                key = (dfn, g["season"])
                acc = per_def.setdefault(key, {})
                gacc = acc.setdefault(g["id"], {})
                pg = r[D.PI["pos_g"]]
                for s in E_STATS:
                    a = _stat(r, s)
                    for c in ((0, pg + 1) if pg >= 0 else (0,)):
                        t = gacc.setdefault((s, c), [0.0, 0.0])
                        t[0] += a
                        t[1] += ex_g[pid][s]
            ex[g["id"]] = ex_g
        return ex

    W.walk(entries, on_date, log=log)
    oe_K, oe_rel, oe_rho = {}, {}, {}
    for s in M.OE_STATS:
        for c, cn in enumerate(M.OE_CATS):
            units = []
            full = {}
            for (dfn, season), games in per_def.items():
                rowsg = [games[gid].get((s, c), [0.0, 0.0]) for gid in sorted(games)]
                full[(dfn, season)] = (sum(x[0] for x in rowsg), sum(x[1] for x in rowsg))
                if season in FIT:
                    units.append(([x[0] for x in rowsg], [x[1] for x in rowsg]))
            k, r, n = _split_half_K(units)
            oe_K[f"{s}:{cn}"] = round(k, 1) if k else 500.0
            oe_rel[f"{s}:{cn}"] = {"split_half_r": None if r is None else round(r, 3), "units": n}
            xs, ys = [], []
            for (dfn, season), v in full.items():
                if season in FIT and (dfn, season - 1) in full:
                    pv = full[(dfn, season - 1)]
                    if v[0] > 0 and v[1] > 0 and pv[0] > 0 and pv[1] > 0:
                        ys.append(math.log(v[0] / v[1]))
                        xs.append(math.log(pv[0] / pv[1]))
            if len(xs) >= 10 and np.var(xs) > 0:
                oe_rho[f"{s}:{cn}"] = round(max(0.0, min(1.0, float(np.cov(xs, ys)[0, 1] / np.var(xs, ddof=1)))), 3)
    return oe_K, oe_rel, oe_rho


def pass3(store_dir, work, P):
    import nba_matchup as NM
    entries = W.load_entries(store_dir, ALL)
    rows = []

    def on_date(book, day, games):
        ex = {}
        for g in games:
            ex_g = {}
            tsn = (book.team(g["home"]), book.team(g["away"]))
            nets = (NMP.net_rating(tsn[0], P), NMP.net_rating(tsn[1], P))
            played = {s: {pid for pid, r in g["players"].items() if r[D.PI["played"]] and r[D.PI["side"]] == s}
                      for s in (0, 1)}
            ctx = {s: NMP.team_context(book, (g["home"], g["away"])[s], day,
                                       (lambda pid, s=s: pid not in played[s]), P, tsn[s]) for s in (0, 1)}
            for pid, r in g["players"].items():
                if not r[D.PI["played"]]:
                    continue
                sn = book.player(pid, day)
                if sn is None:
                    continue
                side = r[D.PI["side"]]
                ex_g[pid] = expected_for(r, sn, P)
                if g["season"] in HIST:
                    continue
                c = ctx[side]
                fe = NM.features(sn, tsn[side], tsn[1 - side], P, home=(side == 0), ctx={"tm_out": NMP.tm_out_x(c)})
                pos = sn["pos"] if sn["pos"] >= 0 else r[D.PI["pos_g"]]
                own_team = (g["home"], g["away"])[side]
                mr = NMP.minutes_row(sn, own_team, book.teams[own_team].tg if own_team in book.teams else 0, pos, c,
                                     nets[side], nets[1 - side])
                row = {"gid": g["id"], "pid": pid, "day": day, "dn": M._day(day), "season": g["season"],
                       "team": own_team, "opp": (g["home"], g["away"])[1 - side], "pos": pos, "home": side == 0,
                       "min": r[D.PI["min"]], "starter": r[D.PI["starter"]],
                       "y": {s: _stat(r, s) for s in E_STATS},
                       "g_hist": sn["cur"][M.PS["g"]] + sn["prev"][M.PS["g"]],
                       "mf": mr["mf"], "missed_last": mr["missed_last"], "traded": mr["traded"], "m6": mr["m6"],
                       "rate": {s: fe["rates"][s][0] for s in E_STATS},
                       "eff_min": {s: fe["rates"][s][1] for s in E_STATS},
                       "E": ex_g[pid],
                       "x": fe["x"],
                       "vec": {m: NM.comp_vector(fe["chan"], m, P) for m in NM.MARKETS},
                       "role": {k: v["value"] for k, v in fe["role"].items()},
                       "pace_hat": fe["pace_hat"], "his_pace": fe["his_pace"]}
                rows.append(row)
            ex[g["id"]] = ex_g
        return ex

    W.walk(entries, on_date, log=log)
    return rows


def comp_sd(rows):
    """SD of each similarity dimension's log ratio over the fit seasons' rows."""
    import nba_matchup as NM
    out = {}
    fit = [r for r in rows if r["season"] in FIT]
    for m in NM.MARKETS:
        for j, d in enumerate(NM.COMP_DIMS[m]):
            # vec was standardized by the default 0.05; undo for the SD
            vals = np.array([r["vec"][m][j] * 0.05 for r in fit])
            out[d] = round(float(np.std(vals)), 5)
    return out


def add_comparables(rows, P):
    """Rung 5's score for every row from the player's EARLIER rows only (the
    numpy twin of nba_matchup.comparables, guarded equal)."""
    import nba_matchup as NM
    sd = P["comp_sd"]
    by_p = {}
    for i, r in enumerate(rows):
        by_p.setdefault(r["pid"], []).append(i)
    for m in NM.MARKETS:
        scale = np.array([0.05 / (sd.get(d) or 0.05) for d in NM.COMP_DIMS[m]])
        for pid, idx in by_p.items():
            idx = sorted(idx, key=lambda i: rows[i]["dn"])
            V = np.array([rows[i]["vec"][m] for i in idx]) * scale
            dn = np.array([rows[i]["dn"] for i in idx], float)
            y = np.array([rows[i]["y"][m] for i in idx], float)
            E = np.array([rows[i]["E"][m] for i in idx], float)
            res = np.log((y + 0.5) / (E + 0.5))
            q = np.minimum(1.0, np.array([rows[i]["min"] for i in idx], float) / 20.0)
            for a, i in enumerate(idx):
                prior = dn < dn[a]
                if not prior.any():
                    rows[i]["x"][m]["comp"] = 0.0
                    rows[i].setdefault("comp_neff", {})[m] = 0.0
                    continue
                d2 = np.mean((V[prior] - V[a]) ** 2, axis=1)
                sim = np.exp(-d2 / (2 * NM.COMP_H ** 2))
                rec = 0.5 ** ((dn[a] - dn[prior]) / NM.COMP_TAU)
                w = sim * rec * q[prior]
                bw = rec * q[prior]
                score = float((w * res[prior]).sum() / (w.sum() + NM.COMP_K) - (bw * res[prior]).sum() / (bw.sum() + NM.COMP_K))
                rows[i]["x"][m]["comp"] = score
                rows[i].setdefault("comp_neff", {})[m] = float(w.sum() ** 2 / (w ** 2).sum()) if (w ** 2).sum() > 0 else 0.0
        # the rows' vec was built with the default scale; the comp_sd-scaled one is what production uses
    return rows


def stage2(store_dir, work):
    with open(os.path.join(work, "params1.json")) as fh:
        P = json.load(fh)
    t0 = time.time()
    oe_K, oe_rel, oe_rho = pass2(store_dir, work, P)
    P.update(oe_K=oe_K, oe_reliability=oe_rel, oe_rho_measured=oe_rho)
    # one persistence for all oe (the per-cell slopes are noisy; their mean is used)
    vals = [v for v in oe_rho.values() if v is not None]
    P["oe_rho"] = round(float(np.mean(vals)), 3) if vals else 0.5
    log(f"[stage2] pass2 {time.time() - t0:.0f}s; oe_rho {P['oe_rho']}")
    P["comp_sd"] = {}
    rows = pass3(store_dir, work, P)
    P["comp_sd"] = comp_sd(rows)
    log(f"[stage2] pass3 {time.time() - t0:.0f}s, {len(rows)} rows")
    add_comparables(rows, P)
    log(f"[stage2] comparables {time.time() - t0:.0f}s")
    with open(os.path.join(work, "params2.json"), "w") as fh:
        json.dump(P, fh, indent=1)
    with open(os.path.join(work, "rows.pkl"), "wb") as fh:
        pickle.dump(rows, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return P, rows


# ---- stage 3: the ladder ---------------------------------------------------------------
def nb_tables(mu, phi, kappa, kmax):
    """pmf[..., 0..kmax] and sf[..., 0..kmax+1] of the negative binomial with
    mean mu and variance phi*mu + kappa*mu^2, vectorized (numpy twin of
    nba_matchup.nb_sf_all; guarded equal)."""
    mu = np.maximum(np.asarray(mu, float), 1e-9)
    v = phi * mu + kappa * mu * mu
    pois = v <= mu * (1 + 1e-9)
    r = np.where(pois, 1e9, mu * mu / np.maximum(v - mu, 1e-12))
    p = r / (r + mu)
    q = 1.0 - p
    pm = np.where(pois, np.exp(-mu), np.exp(r * np.log(p)))
    pmf = np.empty(mu.shape + (kmax + 1,))
    for k in range(kmax + 1):
        pmf[..., k] = pm
        pm = np.where(pois, pm * mu / (k + 1), pm * (k + r) / (k + 1) * q)
    cdf = np.cumsum(pmf, axis=-1)
    sf = np.concatenate([np.ones(mu.shape + (1,)), np.clip(1.0 - cdf, 0.0, 1.0)], axis=-1)
    return pmf, sf


KMAX = {"pts": 80, "reb": 35, "ast": 30, "tpm": 15, "pra": 110, "pr": 90, "pa": 90, "ra": 50, "stl": 12, "blk": 14,
        "tov": 14, "dk": 130}


def mix_tables(rate, mhat, nodes, phi, kappa, kmax):
    """Mixture over minutes nodes: rate (N,), mhat (N,), nodes (N, Q)."""
    mu = rate[:, None] * mhat[:, None] * nodes
    pmf, sf = nb_tables(mu, phi, kappa, kmax)
    return pmf.mean(axis=1), sf.mean(axis=1)


def node_matrix(mhat, dist):
    out = np.empty((len(mhat), MIN_Q))
    for b in dist:
        sel = (mhat >= b["lo"]) & (mhat < b["hi"])
        out[sel] = np.array(b["nodes"])
    return out


def poisson_ridge(X, y, off, lam, w=None, iters=30):
    """min sum w*(exp(off + Xb) - y*(off + Xb)) + lam/2 * |b[1:]|^2 (column 0
    the unpenalized intercept), Newton steps."""
    n, k = X.shape
    w = np.ones(n) if w is None else w
    b = np.zeros(k)
    pen = np.full(k, lam)
    pen[0] = 0.0
    for _ in range(iters):
        eta = np.clip(off + X @ b, -20, 10)
        mu = np.exp(eta)
        g = X.T @ (w * (mu - y)) + pen * b
        H = (X * (w * mu)[:, None]).T @ X + np.diag(pen)
        step = np.linalg.solve(H, g)
        b -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return b


def rung_features(market, rungs):
    import nba_matchup as NM
    names = []
    for k in sorted(rungs):
        names += list(NM.RUNGS[k][market])
    return names


def design(rows, market, names):
    return np.array([[1.0] + [r["x"][market].get(n, 0.0) for n in names] for r in rows])


def fit_market(rows_fit, market, names, lam_grid=(0.3, 3.0, 30.0, 300.0, 3000.0)):
    """Coefficients for one market and feature set on the fit seasons, the
    ridge chosen by 2-fold season CV inside the fit seasons (2022 <-> 2023)."""
    y = np.array([r["y"][market] for r in rows_fit], float)
    off = np.log(np.maximum(1e-9, np.array([r["rate"][market] * r["min"] for r in rows_fit])))
    X = design(rows_fit, market, names)
    sd = X.std(axis=0)
    sd[0] = 1.0
    sd[sd == 0] = 1.0
    Xs = X / sd
    seas = np.array([r["season"] for r in rows_fit])
    best = None
    for lam in lam_grid:
        dev = 0.0
        for a, b in ((FIT[0], FIT[1]), (FIT[1], FIT[0])):
            tr, te = seas == a, seas == b
            bb = poisson_ridge(Xs[tr], y[tr], off[tr], lam)
            mu = np.exp(off[te] + Xs[te] @ bb)
            dev += float(np.sum(mu - y[te] * np.log(np.maximum(mu, 1e-12))))
        if best is None or dev < best[0]:
            best = (dev, lam)
    bb = poisson_ridge(Xs, y, off, best[1])
    beta = bb / sd
    return {"_icpt": float(beta[0]), **{n: float(v) for n, v in zip(names, beta[1:])}}, best[1]


def predict_rate(rows, market, coef):
    names = [k for k in coef if k != "_icpt"]
    X = design(rows, market, names)
    b = np.array([coef["_icpt"]] + [coef[n] for n in names])
    return np.array([r["rate"][market] for r in rows]) * np.exp(X @ b)


def min_x6(r):
    return list(r["m6"])


def eval_population(rows, seasons, mbeta):
    out = []
    for r in rows:
        if r["season"] not in seasons or r["g_hist"] < 5:
            continue
        if pred_minutes(r, mbeta) < 20.0:
            continue
        out.append(r)
    return out


PHI_GRID = (1.0, 1.1, 1.2, 1.3, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0)
KAPPA_GRID = (0.0, 0.005, 0.01, 0.02, 0.04, 0.08, 0.15, 0.25, 0.4)


def _ladder_ll(sf, y, thresh):
    T = np.array(thresh)
    Pp = np.clip(sf[:, T], 1e-4, 1 - 1e-4)
    O = (y[:, None] >= T[None, :])
    return float((-(O * np.log(Pp) + (1 - O) * np.log(1 - Pp))).mean())


def fit_dispersion(rows_fit, market, rate_fn, mhat, nodes):
    """(phi, kappa) minimizing the fit population's LADDER log loss (the
    primary metric) of the minutes-mixed negative binomial; the exact-outcome
    log likelihood at that point is returned beside it."""
    y = _y(rows_fit, market)
    rate = rate_fn(rows_fit)
    kmax = KMAX[market]
    best = None
    for phi in PHI_GRID:
        for kappa in KAPPA_GRID:
            pmf, sf = mix_tables(rate, mhat, nodes, phi, kappa, kmax)
            ll = _ladder_ll(sf, y, THRESH[market])
            if best is None or ll < best[0]:
                ex = float(np.mean(np.log(np.maximum(pmf[np.arange(len(y)), np.minimum(y, kmax)], 1e-12))))
                best = (ll, phi, kappa, ex)
    return best[1], best[2], best[0], best[3]


def score(y, mean, pmf, sf, thresh, gids):
    """The metrics of one arm: ladder log loss / Brier over the thresholds,
    the exact log score, MAE / RMSE of the mean, per-row ladder losses (for
    the paired test) and the (p, outcome) pairs for reliability."""
    y = np.asarray(y).astype(int)
    T = np.array(thresh)
    P_ = np.clip(sf[:, T], 1e-4, 1 - 1e-4)
    O = (y[:, None] >= T[None, :]).astype(float)
    ll_rows = -(O * np.log(P_) + (1 - O) * np.log(1 - P_)).mean(axis=1)
    br_rows = ((P_ - O) ** 2).mean(axis=1)
    kmax = pmf.shape[1] - 1
    ex = -np.log(np.maximum(pmf[np.arange(len(y)), np.minimum(y, kmax)], 1e-12))
    return {"ll": float(ll_rows.mean()), "brier": float(br_rows.mean()), "exact": float(ex.mean()),
            "mae": float(np.mean(np.abs(mean - y))), "rmse": float(np.sqrt(np.mean((mean - y) ** 2))),
            "bias": float(np.mean(mean - y)), "n": int(len(y)), "ll_rows": ll_rows, "P": P_, "O": O,
            "sharp": float(np.std(P_))}


def paired(a_rows, b_rows, gids):
    """mean(a - b) and its standard error clustered by game."""
    d = np.asarray(a_rows) - np.asarray(b_rows)
    gids = np.asarray(gids)
    order = np.argsort(gids, kind="stable")
    g_sorted = gids[order]
    d_sorted = d[order]
    _, starts = np.unique(g_sorted, return_index=True)
    sums = np.add.reduceat(d_sorted, starts)
    counts = np.diff(np.append(starts, len(d_sorted)))
    mean = d.mean()
    se = math.sqrt(float(np.sum((sums - counts * mean) ** 2))) / len(d)
    return float(mean), float(se)


def reliability(P_, O, bins=10):
    p = P_.ravel()
    o = O.ravel()
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        sel = (p >= lo) & (p < hi) if i < bins - 1 else (p >= lo)
        if sel.sum():
            out.append([round(lo, 2), round(hi, 2), int(sel.sum()), round(float(p[sel].mean()), 4),
                        round(float(o[sel].mean()), 4)])
    return out


def _load(work):
    with open(os.path.join(work, "params2.json")) as fh:
        P = json.load(fh)
    with open(os.path.join(work, "rows.pkl"), "rb") as fh:
        rows = pickle.load(fh)
    add_dk(rows)
    return P, rows


AUX = ("stl", "blk", "tov")
DK_THRESH = (10, 20, 30, 40, 50, 60)


def add_dk(rows):
    """Each row's DraftKings points and the DK-average baseline: his
    season-to-date DK points per game played, as of the game (DraftKings'
    own AvgPointsPerGame; last season's when he has none this season)."""
    import nba_matchup as NM
    by = {}
    for r in rows:
        r["dk"] = NM.dk_points(r["y"])
        by.setdefault(r["pid"], []).append(r)
    for rs in by.values():
        rs.sort(key=lambda r: r["dn"])
        acc = {}
        for r in rs:
            cur = acc.get(r["season"])
            prev = acc.get(r["season"] - 1)
            r["dk_avg"] = (cur[0] / cur[1]) if cur and cur[1] else ((prev[0] / prev[1]) if prev and prev[1] else None)
            t = acc.setdefault(r["season"], [0.0, 0])
            t[0] += r["dk"]
            t[1] += 1


def _y(rows, market):
    if market == "dk":
        return np.array([math.floor(max(0.0, r["dk"])) for r in rows], int)
    return np.array([sum(r["y"][c] for c in COMBOS.get(market, (market,))) for r in rows], int)


class Ladder:
    """Everything fitted on the FIT seasons once: the two minutes models, each
    market's dispersion at rung 1 (on a seeded 8,000-row subsample of the
    fit population, by the ladder log loss), each feature set's coefficients
    (cached)."""

    def __init__(self, P, rows, disp_n=8000, work=None):
        import nba_matchup as NM
        self.NM = NM
        self.P, self.rows = P, rows
        cache = os.path.join(work, "disp.json") if work else None
        fit_all = [r for r in rows if r["season"] in FIT]
        self.mb = {"m1": P["min_beta"], "m6": fit_minutes(fit_all, extra=min_x6)}
        self.md = {"m1": P["min_dist"], "m6": minutes_dist(fit_all, self.mb["m6"], extra=min_x6)}
        self.fit_rate_rows = [r for r in fit_all if r["g_hist"] >= 5 and r["min"] >= 1]
        self.fit_pop = eval_population(rows, FIT, self.mb["m1"])
        rng = np.random.default_rng(20261023)
        idx = sorted(rng.choice(len(self.fit_pop), size=min(disp_n, len(self.fit_pop)), replace=False))
        sub = [self.fit_pop[i] for i in idx]
        self._coef = {}
        self.disp = {}
        if cache and os.path.exists(cache):
            # fitted on the FIT seasons only, so a validation and the test reuse
            # it (deleting the file refits it); a partial file resumes
            with open(cache) as fh:
                self.disp = {k: tuple(v) for k, v in json.load(fh).items()}
            log(f"[disp] loaded {sorted(self.disp)} from {cache}")
            if "dk" in self.disp:
                return

        def save():
            if cache:
                with open(cache, "w") as fh:
                    json.dump({k: list(v) for k, v in self.disp.items()}, fh, indent=1)
        mh, nd = self.minutes(sub, "m1")
        for m in MARKETS + AUX + tuple(COMBOS):
            if m in self.disp:
                continue
            self.disp[m] = fit_dispersion(sub, m, lambda rr, m=m: self.rate(rr, m, (1,)), mh, nd)
            log(f"[disp] {m}: phi {self.disp[m][0]} kappa {self.disp[m][1]} ladder ll {self.disp[m][2]:.5f}")
            save()
        base_sel = {m: (1,) for m in MARKETS}
        mu, _mh, _nd = self.dk_mu(sub, base_sel, "m1")
        y = _y(sub, "dk")
        best = None
        for phi in PHI_GRID + (5.0, 6.0, 8.0):
            for kappa in KAPPA_GRID:
                _pmf, sf = nb_tables(np.maximum(mu - 0.375, 0.1), phi, kappa, KMAX["dk"])
                ll = _ladder_ll(sf.mean(axis=1), y, DK_THRESH)
                if best is None or ll < best[0]:
                    best = (ll, phi, kappa)
        self.disp["dk"] = (best[1], best[2], best[0])
        log(f"[disp] dk: phi {best[1]} kappa {best[2]} ladder ll {best[0]:.5f}")
        save()

    def coef(self, market, rungs):
        key = (market, tuple(sorted(rungs)))
        if key not in self._coef:
            names = ["pace", "home"] if market in AUX else rung_features(market, rungs)
            self._coef[key] = fit_market(self.fit_rate_rows, market, names)
        return self._coef[key][0]

    def rate(self, rows, market, rungs):
        if market in COMBOS:
            return sum(self.rate(rows, c, rungs[c] if isinstance(rungs, dict) else rungs) for c in COMBOS[market])
        if market in AUX:
            rungs = (1,)
        return predict_rate(rows, market, self.coef(market, rungs))

    def minutes(self, rows, mm):
        extra = min_x6 if mm == "m6" else None
        mh = np.array([pred_minutes(r, self.mb[mm], extra=extra) for r in rows])
        return mh, node_matrix(mh, self.md[mm])

    def arm(self, rows, market, rungs, mm="m1"):
        rate = self.rate(rows, market, rungs)
        mh, nd = self.minutes(rows, mm)
        phi, kappa = self.disp[market][0], self.disp[market][1]
        pmf, sf = mix_tables(rate, mh, nd, phi, kappa, KMAX[market])
        y = _y(rows, market)
        mean = rate * mh * nd.mean(axis=1)
        return score(y, mean, pmf, sf, THRESH[market], [r["gid"] for r in rows])

    def dk_mu(self, rows, sel, mm):
        """Per minutes node: the DK points mean -- the components' weighted
        per-minute rates x minutes, plus the double-/triple-double bonus from
        P(component >= 10) under each component's own distribution at that
        node (independent given minutes; minutes carry the shared swing)."""
        NM = self.NM
        mh, nd = self.minutes(rows, mm)
        rates = {c: self.rate(rows, c, sel.get(c, (1,))) for c in MARKETS}
        for a in AUX:
            rates[a] = self.rate(rows, a, (1,))
        lin = sum(w * rates[k] for k, w in NM.DK_W.items())
        dist = np.zeros(nd.shape + (6,))
        dist[..., 0] = 1.0
        for c in NM.DK_BONUS_PARTS:
            phi, kappa = self.disp[c][0], self.disp[c][1]
            _pmf, sf = nb_tables(rates[c][:, None] * mh[:, None] * nd, phi, kappa, 10)
            p = sf[..., 10]
            new = dist * (1 - p)[..., None]
            new[..., 1:] += dist[..., :-1] * p[..., None]
            dist = new
        pdd = dist[..., 2:].sum(-1)
        ptd = dist[..., 3:].sum(-1)
        mu = lin[:, None] * mh[:, None] * nd + NM.DK_DD * pdd + NM.DK_TD * ptd
        return mu, mh, nd

    def dk_arm(self, rows, sel, mm="m1"):
        mu, _mh, _nd = self.dk_mu(rows, sel, mm)
        phi, kappa = self.disp["dk"][0], self.disp["dk"][1]
        pmf, sf = nb_tables(np.maximum(mu - 0.375, 0.1), phi, kappa, KMAX["dk"])
        pmf, sf = pmf.mean(axis=1), sf.mean(axis=1)
        y = _y(rows, "dk")
        sc = score(y, mu.mean(axis=1), pmf, sf, DK_THRESH, [r["gid"] for r in rows])
        act = np.array([r["dk"] for r in rows])
        m_ = mu.mean(axis=1)
        sc.update(mae=float(np.mean(np.abs(m_ - act))), rmse=float(np.sqrt(np.mean((m_ - act) ** 2))),
                  bias=float(np.mean(m_ - act)))
        return sc

    @staticmethod
    def dk_avg_arm(rows, cv=0.55):
        """What the DFS builder does today: DK's average, Normal with sd 0.55 x mean."""
        avg = np.array([r["dk_avg"] for r in rows], float)
        act = np.array([r["dk"] for r in rows])
        T = np.array(DK_THRESH, float)
        sd = np.maximum(cv * avg, 1.0)
        z = (T[None, :] - avg[:, None]) / sd[:, None]
        P_ = np.clip(0.5 * np.vectorize(math.erfc)(z / math.sqrt(2.0)), 1e-4, 1 - 1e-4)
        O = (act[:, None] >= T[None, :]).astype(float)
        ll_rows = -(O * np.log(P_) + (1 - O) * np.log(1 - P_)).mean(axis=1)
        return {"ll": float(ll_rows.mean()), "brier": float(((P_ - O) ** 2).mean()), "exact": None,
                "mae": float(np.mean(np.abs(avg - act))), "rmse": float(np.sqrt(np.mean((avg - act) ** 2))),
                "bias": float(np.mean(avg - act)), "n": int(len(act)), "ll_rows": ll_rows, "P": P_, "O": O,
                "sharp": float(np.std(P_))}


def _pos_label(r):
    return ("G", "F", "C")[r["pos"]] if r["pos"] in (0, 1, 2) else "?"


ROLE_AXES = (("creator", ("usage", "ast_pct")), ("spacer", ("three_share", "spot3_share")),
             ("pull-up shooter", ("pullup_share",)), ("slasher", ("drive_share",)),
             ("rim finisher", ("cut_share", "putback_share")), ("post", ("post_share",)),
             ("offensive rebounder", ("oreb_pct",)))


def role_label(r, stats):
    """The player's PRIMARY role for the breakdowns: the axis on which his
    shrunk shares sit furthest above the fit seasons' mean, in SDs."""
    best, lab = -1e9, "?"
    for name, keys in ROLE_AXES:
        z = sum((r["role"][k] - stats[k][0]) / stats[k][1] for k in keys) / len(keys)
        if z > best:
            best, lab = z, name
    return lab


def role_stats(rows):
    fit = [r for r in rows if r["season"] in FIT and r["g_hist"] >= 5]
    out = {}
    for _n, keys in ROLE_AXES:
        for k in keys:
            v = np.array([r["role"][k] for r in fit])
            out[k] = (float(v.mean()), float(v.std()) or 1.0)
    return out


def by_group(a, b, rows, label_fn, min_n=200):
    """Paired ladder-LL difference (a minus b; positive = b better) by subgroup."""
    out = {}
    labs = np.array([label_fn(r) for r in rows])
    gids = np.array([r["gid"] for r in rows])
    for lab in sorted(set(labs)):
        sel = labs == lab
        if sel.sum() < min_n:
            continue
        mean, se = paired(a["ll_rows"][sel], b["ll_rows"][sel], gids[sel])
        out[lab] = {"n": int(sel.sum()), "gain": round(mean, 6), "se": round(se, 6)}
    return out


def strip(sc):
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in sc.items() if k not in ("ll_rows", "P", "O")}


def _gain(a, b, gids):
    d, se = paired(a["ll_rows"], b["ll_rows"], gids)
    return {"gain": round(d, 6), "se": round(se, 6), "z": round(d / se, 2) if se else None}


def run_eval(work, seasons, selection=None, tag="val"):
    """The ladder on `seasons`. selection None: VALIDATION -- choose by the
    pre-registered rule. selection given (frozen from validation): TEST."""
    P, rows = _load(work)
    L = Ladder(P, rows, work=work)
    pop = eval_population(rows, seasons, L.mb["m1"])
    gids = [r["gid"] for r in pop]
    rs = role_stats(rows)
    res = {"seasons": list(seasons), "n_rows": len(pop), "n_games": len(set(gids)), "n_players": len({r["pid"] for r in pop}),
           "markets": {}, "dispersion": {m: list(v) for m, v in L.disp.items()}, "minutes": {}}
    for mm in ("m1", "m6"):
        mh, nd = L.minutes(pop, mm)
        act = np.array([r["min"] for r in pop], float)
        res["minutes"][mm] = {"mae": round(float(np.mean(np.abs(mh * nd.mean(axis=1) - act))), 3),
                              "rmse": round(float(np.sqrt(np.mean((mh * nd.mean(axis=1) - act) ** 2))), 3)}
    sel = dict(selection["rungs"]) if selection else {}
    base_arm = {}
    for m in MARKETS:
        mres = {"cumulative": {}, "forward": []}
        base = L.arm(pop, m, (1,))
        base_arm[m] = base
        mres["cumulative"]["1"] = strip(base)
        mres["reliability_rung1"] = reliability(base["P"], base["O"])
        prev = base
        for k in range(2, 7):
            rungs = tuple(range(1, k + 1))
            sc = L.arm(pop, m, rungs)
            mres["cumulative"][str(k)] = dict(strip(sc), **_gain(prev, sc, gids))
            prev = sc
        full = L.arm(pop, m, (1, 2, 3, 4, 5, 6), "m6")
        mres["cumulative"]["6+M6"] = dict(strip(full), **_gain(prev, full, gids))
        mres["full_vs_baseline"] = _gain(base, full, gids)
        mres["coefs_full"] = L.coef(m, (1, 2, 3, 4, 5, 6))
        if selection is None:
            acc = [1]
            cur = base
            for k in range(2, 7):
                sc = L.arm(pop, m, tuple(acc + [k]))
                g = _gain(cur, sc, gids)
                ok = g["gain"] > 0 and g["z"] is not None and g["z"] >= 1.0
                mres["forward"].append(dict(g, rung=k, accepted=bool(ok)))
                if ok:
                    acc = acc + [k]
                    cur = sc
            sel[m] = acc
        res["markets"][m] = mres
        log(f"[{tag}] {m}: base ll {base['ll']:.5f}; rungs {sel[m]}")
    # the minutes context (M6): one shared minutes model, judged on the four
    # markets' summed ladder loss with each market's selected rate rungs
    a1 = {m: L.arm(pop, m, tuple(sel[m]), "m1") for m in MARKETS}
    a6 = {m: L.arm(pop, m, tuple(sel[m]), "m6") for m in MARKETS}
    s1 = {"ll_rows": sum(a1[m]["ll_rows"] for m in MARKETS)}
    s6 = {"ll_rows": sum(a6[m]["ll_rows"] for m in MARKETS)}
    gm = _gain(s1, s6, gids)
    if selection is None:
        mm = "m6" if (gm["gain"] > 0 and gm["z"] is not None and gm["z"] >= 1.0) else "m1"
    else:
        mm = selection["minutes"]
    res["minutes_m6_vs_m1"] = dict(gm, accepted=(mm == "m6"))
    for m in MARKETS:
        final = a6[m] if mm == "m6" else a1[m]
        res["markets"][m]["selected"] = {"rungs": sel[m], "minutes": mm, "score": strip(final),
                                         "vs_baseline": _gain(base_arm[m], final, gids),
                                         "coefs": L.coef(m, tuple(sel[m])),
                                         "reliability": reliability(final["P"], final["O"]),
                                         "by_pos": by_group(base_arm[m], final, pop, _pos_label),
                                         "by_role": by_group(base_arm[m], final, pop, lambda r: role_label(r, rs))}
        full = L.arm(pop, m, (1, 2, 3, 4, 5, 6), "m6")
        res["markets"][m]["full_by_pos"] = by_group(base_arm[m], full, pop, _pos_label)
        res["markets"][m]["full_by_role"] = by_group(base_arm[m], full, pop, lambda r: role_label(r, rs))
        g = res["markets"][m]["selected"]["vs_baseline"]
        log(f"[{tag}] {m}: selected {sel[m]} {mm}: gain {g['gain']:+.6f} +/- {g['se']:.6f}")
    for c, comps in COMBOS.items():
        csel = {x: tuple(sel[x]) for x in comps}
        base = L.arm(pop, c, (1,))
        final = L.arm(pop, c, csel, mm)
        full = L.arm(pop, c, (1, 2, 3, 4, 5, 6), "m6")
        res["markets"][c] = {"baseline": strip(base), "selected": {"rungs": {k: list(v) for k, v in csel.items()},
                             "minutes": mm, "score": strip(final), "vs_baseline": _gain(base, final, gids),
                             "reliability": reliability(final["P"], final["O"])},
                             "full": {"score": strip(full), "vs_baseline": _gain(base, full, gids)},
                             "reliability_rung1": reliability(base["P"], base["O"])}
        log(f"[{tag}] {c}: base ll {base['ll']:.5f} selected gain {res['markets'][c]['selected']['vs_baseline']}")
    # dk_fp on the DFS population (rung-1 projected minutes 10+, a DK average to compare with)
    dpop = [r for r in rows if r["season"] in seasons and r["g_hist"] >= 5 and r["dk_avg"] is not None
            and pred_minutes(r, L.mb["m1"]) >= 10.0]
    dg = [r["gid"] for r in dpop]
    avg_arm = L.dk_avg_arm(dpop)
    roll = L.dk_arm(dpop, {m: (1,) for m in MARKETS}, "m1")
    fin = L.dk_arm(dpop, {m: tuple(sel[m]) for m in MARKETS}, mm)
    full = L.dk_arm(dpop, {m: (1, 2, 3, 4, 5, 6) for m in MARKETS}, "m6")
    res["markets"]["dk"] = {"n_rows": len(dpop), "dk_avg_normal": strip(avg_arm), "rolling": strip(roll),
                            "selected": strip(fin), "full": strip(full),
                            "rolling_vs_avg": _gain(avg_arm, roll, dg), "selected_vs_avg": _gain(avg_arm, fin, dg),
                            "selected_vs_rolling": _gain(roll, fin, dg),
                            "reliability_avg": reliability(avg_arm["P"], avg_arm["O"]),
                            "reliability_selected": reliability(fin["P"], fin["O"]),
                            "by_threshold": {str(t): {"avg": round(float(np.mean(avg_arm["P"][:, i])), 4),
                                                      "selected": round(float(np.mean(fin["P"][:, i])), 4),
                                                      "observed": round(float(np.mean(fin["O"][:, i])), 4)}
                                             for i, t in enumerate(DK_THRESH)}}
    log(f"[{tag}] dk: avg mae {avg_arm['mae']:.3f} ll {avg_arm['ll']:.5f}; rolling mae {roll['mae']:.3f} ll {roll['ll']:.5f}; "
        f"selected mae {fin['mae']:.3f} ll {fin['ll']:.5f}")
    res["selection"] = {"rungs": sel, "minutes": mm}
    res["coefs"] = {m: L.coef(m, tuple(sel[m])) for m in MARKETS}
    res["coefs"].update({a: L.coef(a, (1,)) for a in AUX})
    res["min_beta6"] = L.mb["m6"]
    res["min_dist6"] = L.md["m6"]
    return res, L


REPORT = os.path.join(ROOT, "research", "reports", "nba_matchup.md")


def prereg_hash(path=REPORT):
    """sha256 of the bytes between the PREREG markers of the report."""
    import hashlib
    with open(path, "rb") as fh:
        txt = fh.read()
    a = txt.index(b"<!-- PREREG BEGIN -->")
    b = txt.index(b"<!-- PREREG END -->") + len(b"<!-- PREREG END -->")
    return hashlib.sha256(txt[a:b]).hexdigest()


def _recorded_hash(path=REPORT):
    with open(path) as fh:
        for line in fh:
            if line.startswith("PREREG_SHA256:"):
                return line.split(":", 1)[1].strip()
    return None


def _require_prereg():
    h, rec = prereg_hash(), _recorded_hash()
    if not rec or rec != h:
        raise SystemExit(f"pre-registration not frozen or edited (recorded {rec}, now {h}); refusing")
    return h


def validate(work):
    h = _require_prereg()
    res, _L = run_eval(work, VAL, tag="val")
    res["prereg_sha256"] = h
    with open(os.path.join(work, "val.json"), "w") as fh:
        json.dump(res, fh, indent=1, default=float)
    with open(os.path.join(work, "selection.json"), "w") as fh:
        json.dump(res["selection"], fh, indent=1)
    return res


def test(work):
    """The one look at 2025-26 with the frozen selection. "Once" is enforced by
    refusing when <work>/test.json exists -- a file-existence check on this
    work directory, not a recorded marker (stated in the report)."""
    h = _require_prereg()
    with open(os.path.join(work, "selection.json")) as fh:
        sel = json.load(fh)
    if os.path.exists(os.path.join(work, "test.json")):
        raise SystemExit("the test season was already read (work/test.json exists); refusing a second look")
    res, _L = run_eval(work, TEST, selection=sel, tag="test")
    res["prereg_sha256"] = h
    with open(os.path.join(work, "test.json"), "w") as fh:
        json.dump(res, fh, indent=1, default=float)
    return res


def freeze(work):
    """Write the production fit: research/data/nba_matchup/params.json (the
    profiles' measured K / rho / role K / oe K, the baseline rate weights, the
    minutes models and nodes, the dispersions, the comparables' SDs) and
    coefs.json (each market's SHIPPED coefficients by the test verdict, the
    rung-1 coefficients, the verdict itself)."""
    with open(os.path.join(work, "params2.json")) as fh:
        P = json.load(fh)
    with open(os.path.join(work, "test.json")) as fh:
        T = json.load(fh)
    with open(os.path.join(work, "val.json")) as fh:
        V = json.load(fh)
    with open(os.path.join(work, "disp.json")) as fh:
        disp = json.load(fh)
    _P, rows = _load(work)
    L = Ladder(_P, rows, work=work)
    shipped = {}
    coefs, base = {}, {}
    for m in MARKETS:
        sel = T["selection"]["rungs"][m]
        g = T["markets"][m]["selected"]["vs_baseline"]
        ok = len(sel) > 1 and g["gain"] > 0
        rungs = sel if ok else [1]
        shipped[m] = {"rungs": rungs, "selected_on_validation": sel, "test_gain": g["gain"], "test_se": g["se"],
                      "test_z": g["z"], "shipped": bool(ok)}
        coefs[m] = L.coef(m, tuple(rungs))
        base[m] = L.coef(m, (1,))
    for a in AUX:
        coefs[a] = base[a] = L.coef(a, (1,))
    mm = T["selection"]["minutes"]
    gm = T["minutes_m6_vs_m1"]
    shipped["minutes"] = mm if (mm == "m6" and gm["gain"] > 0) else "m1"
    # dk_fp's rule reads the SHIPPED dk_fp: its components' shipped rungs with
    # the shipped minutes model (which may differ from the frozen selection's
    # when M6 failed the test), scored on the test season's DFS population --
    # the rule's own consequence, no choice made on it
    test_pop = [r for r in rows if r["season"] in TEST and r["g_hist"] >= 5 and r["dk_avg"] is not None
                and pred_minutes(r, L.mb["m1"]) >= 10.0]
    dk_ship = L.dk_arm(test_pop, {m: tuple(shipped[m]["rungs"]) for m in MARKETS}, shipped["minutes"])
    dk_avg = L.dk_avg_arm(test_pop)
    shipped["dk_fp_test"] = {"shipped_mae": round(dk_ship["mae"], 4), "avg_mae": round(dk_avg["mae"], 4),
                             "shipped_ll": round(dk_ship["ll"], 6), "avg_ll": round(dk_avg["ll"], 6),
                             "n": len(test_pop)}
    shipped["dk_fp_replaces_avg"] = bool(dk_ship["mae"] < dk_avg["mae"] and dk_ship["ll"] < dk_avg["ll"])
    params_out = {k: P[k] for k in ("team_K", "rho", "team_reliability", "role_K", "role_reliability", "rate_W",
                                    "min_beta", "min_keys", "min_dist", "oe_K", "oe_reliability", "oe_rho",
                                    "comp_sd")}
    params_out.update(min_beta6=T["min_beta6"], min_dist6=T["min_dist6"], disp=disp, fit_seasons=list(FIT),
                      built=time.time(), fit_id=prereg_hash()[:12],
                      provenance={"source": "ESPN game summaries 2021-22 .. 2025-26 (research/nba_matchup_fetch.py)",
                                  "fit": "research/nba_matchup_fit.py", "report": "research/reports/nba_matchup.md"})
    coefs_out = {"coefs": coefs, "coefs_base": base, "shipped": shipped, "prereg_sha256": prereg_hash(),
                 "validation_selection": V["selection"]}
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "params.json"), "w") as fh:
        json.dump(params_out, fh, indent=1, sort_keys=True)
    with open(os.path.join(OUT_DIR, "coefs.json"), "w") as fh:
        json.dump(coefs_out, fh, indent=1, sort_keys=True)
    return params_out, coefs_out


def shipped_report(work):
    """The SHIPPED model (coefs.json: each market's rungs by its test verdict,
    the minutes model by M6's) scored on 2024-25 and 2025-26 against rung 1.
    Computed AFTER every decision, as their consequence: nothing is chosen on
    it (when M6 fails the test, the shipped combination -- selected rungs with
    the rung-1 minutes model -- is not any single arm the test printed)."""
    with open(os.path.join(OUT_DIR, "coefs.json")) as fh:
        C = json.load(fh)
    P, rows = _load(work)
    L = Ladder(P, rows, work=work)
    sh = C["shipped"]
    mm = sh["minutes"]
    rungs = {m: tuple(sh[m]["rungs"]) for m in MARKETS}
    out = {"minutes": mm, "rungs": {m: list(v) for m, v in rungs.items()}}
    for tag, seasons in (("val", VAL), ("test", TEST)):
        pop = eval_population(rows, seasons, L.mb["m1"])
        gids = [r["gid"] for r in pop]
        res = {"n_rows": len(pop)}
        for m in MARKETS + tuple(COMBOS):
            base = L.arm(pop, m, (1,))
            rr = {c: rungs[c] for c in COMBOS[m]} if m in COMBOS else rungs[m]
            shp = L.arm(pop, m, rr, mm)
            res[m] = {"rung1": strip(base), "shipped": strip(shp), "vs_rung1": _gain(base, shp, gids),
                      "reliability": reliability(shp["P"], shp["O"])}
            log(f"[shipped {tag}] {m}: {res[m]['vs_rung1']}")
        dpop = [r for r in rows if r["season"] in seasons and r["g_hist"] >= 5 and r["dk_avg"] is not None
                and pred_minutes(r, L.mb["m1"]) >= 10.0]
        dg = [r["gid"] for r in dpop]
        avg = L.dk_avg_arm(dpop)
        roll = L.dk_arm(dpop, {m: (1,) for m in MARKETS}, "m1")
        shp = L.dk_arm(dpop, rungs, mm)
        res["dk"] = {"n_rows": len(dpop), "avg": strip(avg), "rolling": strip(roll), "shipped": strip(shp),
                     "shipped_vs_avg": _gain(avg, shp, dg), "shipped_vs_rolling": _gain(roll, shp, dg),
                     "reliability_shipped": reliability(shp["P"], shp["O"]),
                     "reliability_avg": reliability(avg["P"], avg["O"]),
                     "by_threshold": {str(t): {"avg": round(float(np.mean(avg["P"][:, i])), 4),
                                               "shipped": round(float(np.mean(shp["P"][:, i])), 4),
                                               "observed": round(float(np.mean(shp["O"][:, i])), 4)}
                                      for i, t in enumerate(DK_THRESH)}}
        log(f"[shipped {tag}] dk: avg mae {avg['mae']:.3f} ll {avg['ll']:.5f}; shipped mae {shp['mae']:.3f} ll {shp['ll']:.5f}")
        out[tag] = res
    with open(os.path.join(work, "shipped_report.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)
    return out


def record_market(work):
    """Copy the pre-registered market comparison's summary (research/
    nba_matchup_kalshi.py compare -> work/kalshi_compare.json) into coefs.json
    under `market_test`, so the board can say what it found."""
    with open(os.path.join(work, "kalshi_compare.json")) as fh:
        K = json.load(fh)
    path = os.path.join(OUT_DIR, "coefs.json")
    with open(path) as fh:
        C = json.load(fh)

    def cut(x):
        return {k: x.get(k) for k in ("n", "games", "ll_p", "ll_mid", "brier_p", "brier_mid", "bets")}
    C["market_test"] = {"season": "2025-26", "source": "Kalshi historical prop markets, pre-tip hourly candles, "
                        "seeded sample (research/nba_matchup_kalshi.py)", "all": cut(K["all"]), "tight": cut(K["tight"]),
                        "by_stat": {k: cut(v) for k, v in K["by_stat"].items()}}
    with open(path, "w") as fh:
        json.dump(C, fh, indent=1, sort_keys=True)
    return C["market_test"]


def dump_preds(work, seasons=TEST):
    """For the market comparison: the SHIPPED model's (coefs.json) and rung 1's
    P(stat >= k) for every played row of `seasons` with 5+ prior games (any
    projected minutes: Kalshi lists bench players too)."""
    with open(os.path.join(OUT_DIR, "coefs.json")) as fh:
        C = json.load(fh)
    P, rows = _load(work)
    L = Ladder(P, rows, work=work)
    pop = [r for r in rows if r["season"] in seasons and r["g_hist"] >= 5]
    sh = C["shipped"]
    mm = sh["minutes"]
    out = {(r["gid"], r["pid"]): {"base": {}, "y": {}} for r in pop}
    for m in MARKETS + ("pra",):
        rungs = {c: tuple(sh[c]["rungs"]) for c in MARKETS}
        rr = rungs if m == "pra" else rungs[m]
        for arm, rg, mmx in (("ship", rr, mm), ("base", (1,), "m1")):
            rate = L.rate(pop, m, rg)
            mh, nd = L.minutes(pop, mmx)
            phi, kappa = L.disp[m][0], L.disp[m][1]
            _pmf, sf = mix_tables(rate, mh, nd, phi, kappa, KMAX[m])
            for i, r in enumerate(pop):
                d = out[(r["gid"], r["pid"])]
                if arm == "ship":
                    d[m] = [float(x) for x in sf[i]]
                else:
                    d["base"][m] = [float(x) for x in sf[i]]
        for i, r in enumerate(pop):
            out[(r["gid"], r["pid"])]["y"][m] = int(_y([r], m)[0])
    with open(os.path.join(work, "test_preds.pkl"), "wb") as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return len(out)


def render(work, which="val"):
    """Markdown tables of one evaluation (val.json / test.json) for the report."""
    with open(os.path.join(work, f"{which}.json")) as fh:
        R = json.load(fh)
    out = [f"Rows scored: {R['n_rows']:,} player-games, {R['n_games']:,} games, {R['n_players']} players "
           f"(seasons {R['seasons']}).", ""]
    out.append(f"Minutes (projected mean vs actual): rung-1 model MAE {R['minutes']['m1']['mae']}, RMSE "
               f"{R['minutes']['m1']['rmse']}; M6 MAE {R['minutes']['m6']['mae']}, RMSE {R['minutes']['m6']['rmse']}.")
    g = R["minutes_m6_vs_m1"]
    if which == "test":
        verdict = ("kept (test loss fell)" if g["gain"] > 0 else
                   "NOT shipped: the rule ships M6 only if the summed test loss falls; the rung-1 minutes model ships")
    else:
        verdict = "ADOPTED" if g["accepted"] else "not adopted"
    out.append(f"M6 (four markets' summed ladder loss, with each market's selected rungs): gain {g['gain']:+.6f} "
               f"+/- {g['se']:.6f} (z {g['z']}) -> {verdict}.")
    out.append("")
    for m in MARKETS:
        mr = R["markets"][m]
        out.append(f"**{m}** -- cumulative ladder (each rung added on top of all before it)")
        out.append("")
        out.append("| rungs | ladder log loss | gain vs previous | SE | z | Brier | exact log score | MAE | RMSE | bias |")
        out.append("|---|---|---|---|---|---|---|---|---|---|")
        for k, v in mr["cumulative"].items():
            out.append(f"| 1..{k} | {v['ll']:.5f} | {v.get('gain', 0):+.6f} | {v.get('se', 0):.6f} | {v.get('z', '')} | "
                       f"{v['brier']:.5f} | {v['exact']:.4f} | {v['mae']:.3f} | {v['rmse']:.3f} | {v['bias']:+.3f} |")
        if mr.get("forward"):
            out.append("")
            out.append("Forward selection (rule: gain > 0 and z >= 1): " + "; ".join(
                f"rung {f['rung']} {f['gain']:+.6f} (z {f['z']}) {'ACCEPT' if f['accepted'] else 'reject'}"
                for f in mr["forward"]))
        sel = mr["selected"]
        vb = sel["vs_baseline"]
        out.append("")
        out.append(f"Selected rungs {sel['rungs']} with minutes {sel['minutes']}: ladder log loss {sel['score']['ll']:.5f}, "
                   f"gain vs rung 1 {vb['gain']:+.6f} +/- {vb['se']:.6f} (z {vb['z']}); MAE {sel['score']['mae']:.3f}.")
        if sel.get("by_pos"):
            out.append("By position (gain vs rung 1, SE): " + "; ".join(
                f"{k} {v['gain']:+.6f} ({v['se']:.6f}, n {v['n']})" for k, v in sel["by_pos"].items()))
        if sel.get("by_role"):
            out.append("By primary role: " + "; ".join(
                f"{k} {v['gain']:+.6f} ({v['se']:.6f}, n {v['n']})" for k, v in sel["by_role"].items()))
        out.append("Full model (1..6 + M6) by position: " + "; ".join(
            f"{k} {v['gain']:+.6f} ({v['se']:.6f})" for k, v in mr["full_by_pos"].items()))
        out.append("Full model by primary role: " + "; ".join(
            f"{k} {v['gain']:+.6f} ({v['se']:.6f})" for k, v in mr["full_by_role"].items()))
        out.append("Reliability, selected (bin, n, mean forecast, observed): " + "; ".join(
            f"{b[0]:.1f}-{b[1]:.1f} {b[2]} {b[3]:.3f}->{b[4]:.3f}" for b in sel["reliability"]))
        out.append("")
    out.append("**Combos** (components' selections; dispersion their own)")
    out.append("")
    out.append("| market | rung-1 ladder LL | selected LL | gain | SE | full LL | full gain | SE |")
    out.append("|---|---|---|---|---|---|---|---|")
    for c in COMBOS:
        cr = R["markets"][c]
        out.append(f"| {c} | {cr['baseline']['ll']:.5f} | {cr['selected']['score']['ll']:.5f} | "
                   f"{cr['selected']['vs_baseline']['gain']:+.6f} | {cr['selected']['vs_baseline']['se']:.6f} | "
                   f"{cr['full']['score']['ll']:.5f} | {cr['full']['vs_baseline']['gain']:+.6f} | {cr['full']['vs_baseline']['se']:.6f} |")
    dk = R["markets"]["dk"]
    out.append("")
    out.append(f"**dk_fp** (DFS population: {dk['n_rows']:,} player-games)")
    out.append("")
    out.append("| arm | MAE | RMSE | bias | ladder LL (10..60) | Brier |")
    out.append("|---|---|---|---|---|---|")
    for nm, k in (("DK average, Normal sd 0.55 x mean (the builder today)", "dk_avg_normal"),
                  ("rolling baseline (rung 1)", "rolling"), ("selected", "selected"), ("full (1..6 + M6)", "full")):
        v = dk[k]
        out.append(f"| {nm} | {v['mae']:.3f} | {v['rmse']:.3f} | {v['bias']:+.3f} | {v['ll']:.5f} | {v['brier']:.5f} |")
    out.append("")
    out.append("P(DK >= x), mean forecast vs observed: " + "; ".join(
        f"{t}: avg-arm {v['avg']:.3f}, selected {v['selected']:.3f}, observed {v['observed']:.3f}"
        for t, v in dk["by_threshold"].items()))
    for nm in ("rolling_vs_avg", "selected_vs_avg", "selected_vs_rolling"):
        g = dk[nm]
        out.append(f"{nm}: ladder LL gain {g['gain']:+.6f} +/- {g['se']:.6f} (z {g['z']})")
    return "\n".join(out)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "stage1":
        stage1(sys.argv[2], sys.argv[3])
    elif cmd == "stage2":
        stage2(sys.argv[2], sys.argv[3])
    elif cmd == "validate":
        validate(sys.argv[2])
    elif cmd == "test":
        test(sys.argv[2])
    elif cmd == "freeze":
        freeze(sys.argv[2])
    elif cmd == "render":
        print(render(sys.argv[2], sys.argv[3]))
    elif cmd == "shipped_report":
        shipped_report(sys.argv[2])
    elif cmd == "record_market":
        print(json.dumps(record_market(sys.argv[2]), indent=1))
    elif cmd == "dump_preds":
        print(dump_preds(sys.argv[2]))
    elif cmd == "fitcheck":
        # in-sample on the FIT seasons only: a pipeline check, never a verdict
        res, _L = run_eval(sys.argv[2], FIT, tag="fitcheck")
        with open(os.path.join(sys.argv[2], "fitcheck.json"), "w") as fh:
            json.dump(res, fh, indent=1, default=float)
