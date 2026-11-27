"""Does NFL coaching behaviour drift, and does a recency-aware training scheme
predict the next season better than full history? (research/reports/nfl_coach_evo.md).
RESEARCH. The rule is research/data/nfl_coach/evo_rule.json, committed (ae6e018)
before any score here was computed; this file implements it and nothing else.
nflverse (CC BY 4.0) through R2's loader; R1's seal holds (2026 week 13 on is never read).

    python3 -m research.nfl_coach_evo <pbp dir> <players.csv>

Writes research/data/nfl_coach/evo.json. Builds nothing into R1-R5.
"""
import json
import math
import os
import sys
from collections import defaultdict

import nfl_coach_sim_r3 as E3
from research import nfl_coach_r2_data as R
from research import nfl_coach_r3_qb as QB
from research import nfl_coach_r5_fit as F5

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "evo.json")
RULE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "evo_rule.json")
SEASONS = tuple(range(2016, 2027))
FOLDS = tuple(range(2019, 2027))          # 2026 = weeks 1-12, the seal's limit
H_GRID = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
TAU_GRID = (0.0, 0.01, 0.02, 0.04, 0.08, 0.16, 0.32)
SHRINK = 20.0
CANDS = ("full", "last3", "last5", "exp", "rw")
CELL_TABLES = ("T1_call", "T2_nohuddle", "T3_clock", "T4_rb_target")
_MB = ((-99, -17), (-16, -9), (-8, -4), (-3, 3), (4, 8), (9, 16), (17, 99))


def mb(sd):
    for i, (lo, hi) in enumerate(_MB):
        if lo <= sd <= hi:
            return i
    return 0 if sd < 0 else 6


def _dist(d):
    return 0 if d <= 2 else 1 if d <= 6 else 2 if d <= 10 else 3


def _zone(yl):
    return 0 if yl >= 80 else 1 if yl >= 50 else 2 if yl >= 21 else 3


def cell_rows(sd_, pos):
    """{table: {(game, cell): [k, n]}} for one season (regular season)."""
    ids = sd_["players"]
    out = {t: defaultdict(lambda: [0, 0]) for t in CELL_TABLES}
    for g in sd_["games"]:
        if g["st"] != 2:
            continue
        gid = g["id"]
        prev = None
        for p in g["plays"]:
            k = p[R.Q_KIND]
            if k not in R.SCRIMMAGE:
                continue
            q = min(p[R.Q_PER], 4)
            m = mb(p[R.Q_SD])
            late = 1 if p[R.Q_HSR] <= 120 else 0
            if 1 <= p[R.Q_DOWN] <= 3:
                c = out["T1_call"][(gid, (q, m, p[R.Q_DOWN], _dist(p[R.Q_DIST]), _zone(p[R.Q_YL])))]
                c[0] += 1 if k in (R.K_PASS, R.K_SACK, R.K_SCR) else 0
                c[1] += 1
            c = out["T2_nohuddle"][(gid, (q, m, late))]
            c[0] += 1 if p[R.Q_NH] > 0 else 0
            c[1] += 1
            if (prev is not None and p[R.Q_PER] <= 4 and prev[R.Q_DRIVE] == p[R.Q_DRIVE]
                    and prev[R.Q_OFFH] == p[R.Q_OFFH] and (prev[R.Q_PER] <= 2) == (p[R.Q_PER] <= 2)):
                el = prev[R.Q_GSR] - p[R.Q_GSR]
                if el >= 0:
                    c = out["T3_clock"][(gid, (min(prev[R.Q_PER], 4), mb(prev[R.Q_SD]),
                                               1 if prev[R.Q_HSR] <= 120 else 0))]
                    c[0] += 1 if el >= 35 else 0
                    c[1] += 1
            if k == R.K_PASS and p[R.Q_RECV] >= 0:
                ps = pos.get(ids[p[R.Q_RECV]], "")
                if ps in ("RB", "FB", "WR", "TE"):
                    c = out["T4_rb_target"][(gid, (p[R.Q_DOWN], _zone(p[R.Q_YL]), m))]
                    c[0] += 1 if ps in ("RB", "FB") else 0
                    c[1] += 1
            prev = p
    return {t: dict(v) for t, v in out.items()}


# ---- the cell tables ------------------------------------------------------------------------
def _cells_by_season(rows):
    """{season: {cell: [k, n]}} from {season: {(game, cell): [k, n]}}."""
    out = {}
    for s, d in rows.items():
        a = defaultdict(lambda: [0, 0])
        for (_g, c), (k, n) in d.items():
            a[c][0] += k
            a[c][1] += n
        out[s] = dict(a)
    return out


def _rates(cs, weights):
    K, N = defaultdict(float), defaultdict(float)
    for s, w in weights.items():
        if w <= 0:
            continue
        for c, (k, n) in cs[s].items():
            K[c] += w * k
            N[c] += w * n
    tk, tn = sum(K.values()), sum(N.values())
    p0 = tk / tn
    return {c: (K[c] + SHRINK * p0) / (N[c] + SHRINK) for c in N}, p0


def _ll(k, n, p):
    p = min(1 - 1e-9, max(1e-9, p))
    return -(k * math.log(p) + (n - k) * math.log(1 - p))


def _sig(x):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def _logit(p):
    p = min(1 - 1e-9, max(1e-9, p))
    return math.log(p / (1 - p))


def game_losses_cells(season_rows, pred):
    """{game: loss} for one season's rows under pred(cell) -> p."""
    out = defaultdict(float)
    for (g, c), (k, n) in season_rows.items():
        out[g] += _ll(k, n, pred(c))
    return dict(out)


def weights_for(cand, S, h=None):
    tr = [s for s in SEASONS if s < S]
    if cand == "full":
        return {s: 1.0 for s in tr}
    if cand == "last3":
        return {s: 1.0 for s in tr[-3:]}
    if cand == "last5":
        return {s: 1.0 for s in tr[-5:]}
    return {s: 0.5 ** ((S - 1 - s) / h) for s in tr}


def _offset(cs_s, L, d0=0.0):
    """One season's league offset on a fixed shape: the ML logit shift and its variance."""
    d = d0
    for _ in range(30):
        g = H = 0.0
        for c, (k, n) in cs_s.items():
            p = _sig(L.get(c, 0.0) + d)
            g += k - n * p
            H += n * p * (1 - p)
        step = g / H
        d += step
        if abs(step) < 1e-10:
            break
    return d, 1.0 / H


def kalman(obs, tau2):
    """Local level: obs [(delta, var)] in season order. Returns (filtered level after the
    last obs, its variance, the marginal log-likelihood of obs 2..n)."""
    m, P = obs[0]
    ll = 0.0
    for d, v in obs[1:]:
        P = P + tau2
        S_ = P + v
        ll += -0.5 * (math.log(2 * math.pi * S_) + (d - m) ** 2 / S_)
        K = P / S_
        m = m + K * (d - m)
        P = (1 - K) * P
    return m, P, ll


def rw_forecast(offsets):
    best = max(TAU_GRID, key=lambda t: kalman(offsets, t * t)[2])
    m, _P, _ll = kalman(offsets, best * best)
    return m, best


def cell_table(rows):
    """Every candidate's per-game held-out losses per fold for one cell table."""
    cs = _cells_by_season(rows)
    loss = {}      # (cand, S) -> {game: loss}
    exp_loss = {}  # (S, h) -> {game: loss} for S in 2018..2026
    for S in range(2018, 2027):
        for h in H_GRID:
            r, p0 = _rates(cs, weights_for("exp", S, h))
            exp_loss[(S, h)] = game_losses_cells(rows[S], lambda c, r=r, p0=p0: r.get(c, p0))
    chosen, rw_info = {}, {}
    for S in FOLDS:
        for cand in ("full", "last3", "last5"):
            r, p0 = _rates(cs, weights_for(cand, S))
            loss[(cand, S)] = game_losses_cells(rows[S], lambda c, r=r, p0=p0: r.get(c, p0))
        h = min(H_GRID, key=lambda hh: sum(sum(exp_loss[(s, hh)].values()) for s in range(2018, S)))
        chosen[S] = h
        loss[("exp", S)] = exp_loss[(S, h)]
        r, p0 = _rates(cs, weights_for("full", S))
        L = {c: _logit(v) for c, v in r.items()}
        offs = [_offset(cs[s], L) for s in SEASONS if s < S]
        mu, tau = rw_forecast(offs)
        loss[("rw", S)] = game_losses_cells(rows[S], lambda c, L=L, mu=mu, p0=p0: _sig(L.get(c, _logit(p0)) + mu))
        rw_info[S] = {"tau": tau, "forecast": round(mu, 4), "offsets": [round(d, 4) for d, _v in offs],
                      "offset_sd_sampling": round(math.sqrt(sum(v for _d, v in offs) / len(offs)), 4)}
    prof = {str(h): sum(sum(exp_loss[(S, h)].values()) for S in FOLDS) for h in H_GRID}
    prof["inf"] = sum(sum(loss[("full", S)].values()) for S in FOLDS)
    return loss, chosen, rw_info, prof


# ---- the pull hazard (R3's s6 logistic) ------------------------------------------------------
def pull_rows(allg):
    """{season: [(game, [(sd, gsr, 3, 0.0, y), ...]), ...]} per team-game."""
    return {s: [(tg["gid"], [(sd, gsr, 3, 0.0, y) for sd, gsr, y, _per in tg["seq"]]) for tg in allg[s]]
            for s in allg}


def _pull_losses(rows_S, coef, shift=0.0):
    out = defaultdict(float)
    for g, seq in rows_S:
        for sd, gsr, _to, _ct, y in seq:
            p = min(1 - 1e-9, max(1e-9, _sig(sum(c * v for c, v in zip(coef, E3.features(sd, gsr))) + shift)))
            out[g] -= math.log(p if y else 1 - p)
    return dict(out)


def pull_fit(pr, cand, S, h=None):
    """R5's fitter (nfl_coach_r5_fit.fit) on R3's s6 form: flat weights over the
    candidate's seasons, or the exponential weights anchored on S - 1."""
    tr = [s for s in SEASONS if s < S]
    if cand in ("full", "last3", "last5"):
        use = {"full": tr, "last3": tr[-3:], "last5": tr[-5:]}[cand]
        return F5.fit([(s, seq) for s in use for _g, seq in pr[s]], "s6", max(use), hl=1e9)
    return F5.fit([(s, seq) for s in tr for _g, seq in pr[s]], "s6", S - 1, hl=h)


def _pull_offset(rows_s, coef):
    d = 0.0
    for _ in range(30):
        g = H = 0.0
        for _g, seq in rows_s:
            for sd, gsr, _to, _ct, y in seq:
                p = _sig(sum(c * v for c, v in zip(coef, E3.features(sd, gsr))) + d)
                g += y - p
                H += p * (1 - p)
        step = g / H
        d += step
        if abs(step) < 1e-10:
            break
    return d, 1.0 / H


def pull_table(pr):
    loss, exp_loss, chosen, rw_info, coefs = {}, {}, {}, {}, {}
    for S in range(2018, 2027):
        for h in H_GRID:
            exp_loss[(S, h)] = _pull_losses(pr[S], pull_fit(pr, "exp", S, h))
    for S in FOLDS:
        for cand in ("full", "last3", "last5"):
            coefs[(cand, S)] = pull_fit(pr, cand, S)
            loss[(cand, S)] = _pull_losses(pr[S], coefs[(cand, S)])
        h = min(H_GRID, key=lambda hh: sum(sum(exp_loss[(s, hh)].values()) for s in range(2018, S)))
        chosen[S] = h
        loss[("exp", S)] = exp_loss[(S, h)]
        coefs[("exp", S)] = pull_fit(pr, "exp", S, h)
        base = coefs[("full", S)]
        offs = [_pull_offset(pr[s], base) for s in SEASONS if s < S]
        mu, tau = rw_forecast(offs)
        loss[("rw", S)] = _pull_losses(pr[S], base, mu)
        coefs[("rw", S)] = [base[0] + mu] + base[1:]
        rw_info[S] = {"tau": tau, "forecast": round(mu, 4), "offsets": [round(d, 4) for d, _v in offs],
                      "offset_sd_sampling": round(math.sqrt(sum(v for _d, v in offs) / len(offs)), 4)}
    prof = {str(h): sum(sum(exp_loss[(S, h)].values()) for S in FOLDS) for h in H_GRID}
    prof["inf"] = sum(sum(loss[("full", S)].values()) for S in FOLDS)
    return loss, chosen, rw_info, prof, coefs


# ---- the scores and the rule -------------------------------------------------------------------
def gains(loss, cand):
    """Per game (all folds): full's loss minus the candidate's; and per fold means."""
    per, folds = {}, {}
    for S in FOLDS:
        a, b = loss[("full", S)], loss[(cand, S)]
        d = [a[g] - b[g] for g in a]
        folds[S] = sum(d) / len(d)
        for g in a:
            per[g] = a[g] - b[g]
    return per, folds


def summ(d):
    v = list(d.values())
    n = len(v)
    m = sum(v) / n
    se = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1) / n)
    return {"gain_per_game": round(m, 5), "se": round(se, 5), "z": round(m / se, 2) if se else None, "games": n}


def decide(tables):
    """The rule (evo_rule.json "decision")."""
    out = {"tables": {}, "pooled": {}}
    for t, d in tables.items():
        out["tables"][t] = {}
        for c in CANDS[1:]:
            per, folds = d["gains"][c]
            sm = summ(per)
            pos = sum(1 for v in folds.values() if v > 0)
            sm.update({"folds_positive": pos, "fold_gains": {str(k): round(v, 5) for k, v in folds.items()},
                       "wins": sm["z"] is not None and sm["z"] > 2 and pos >= 5})
            out["tables"][t][c] = sm
        out["tables"][t]["drifts"] = any(out["tables"][t][c]["wins"] for c in CANDS[1:])
    best = None
    for c in CANDS[1:]:
        tot = defaultdict(float)
        for d in tables.values():
            for g, v in d["gains"][c][0].items():
                tot[g] += v
        sm = summ(tot)
        worst = min(out["tables"][t][c]["z"] or 0 for t in tables)
        sm["no_table_below_minus_1se"] = worst > -1
        sm["qualifies"] = sm["z"] is not None and sm["z"] > 2 and sm["no_table_below_minus_1se"]
        out["pooled"][c] = sm
        if sm["qualifies"] and (best is None or sm["gain_per_game"] > out["pooled"][best]["gain_per_game"]):
            best = c
    out["scheme"] = best or "full"
    return out


def half_life(prof):
    h = min(prof, key=prof.get)
    return {"best_fixed_h": h, "loss_minus_full": {k: round(v - prof["inf"], 3) for k, v in prof.items()}}


def main(pbp, players_csv):
    import hashlib
    rule_sha = hashlib.sha256(open(RULE, "rb").read()).hexdigest()
    pos = R.positions(players_csv)
    rows = {t: {} for t in CELL_TABLES}
    for s in SEASONS:
        cr = cell_rows(R.load_season(pbp, s), pos)
        for t in CELL_TABLES:
            rows[t][s] = cr[t]
    tables, extra = {}, {}
    for t in CELL_TABLES:
        loss, chosen, rw_info, prof = cell_table(rows[t])
        tables[t] = {"gains": {c: gains(loss, c) for c in CANDS[1:]}}
        extra[t] = {"exp_h_chosen": {str(k): v for k, v in chosen.items()}, "rw": {str(k): v for k, v in rw_info.items()},
                    "half_life": half_life(prof),
                    "rows": sum(n for s in SEASONS for (_g, _c), (_k, n) in rows[t][s].items())}
    allg, _pos, _ids = QB.load_all(pbp, players_csv, SEASONS)
    pr = pull_rows(allg)
    loss, chosen, rw_info, prof, coefs = pull_table(pr)
    tables["T5_pull"] = {"gains": {c: gains(loss, c) for c in CANDS[1:]}}
    corner = {}
    for c in CANDS:
        e = sum(F5.cell([(S, seq) for _g, seq in pr[S]], coefs[(c, S)], "s6")["expected"] for S in range(2022, 2027))
        corner[c] = round(e, 2)
    real = sum(F5.cell([(S, seq) for _g, seq in pr[S]], coefs[("full", S)], "s6")["pulls"] for S in range(2022, 2027))
    extra["T5_pull"] = {"exp_h_chosen": {str(k): v for k, v in chosen.items()},
                        "rw": {str(k): v for k, v in rw_info.items()}, "half_life": half_life(prof),
                        "trailing_corner_2022_2026_walk_forward": {"real": real, "expected": corner}}
    out = {"rule_sha256": rule_sha, "folds": list(FOLDS), "decision": decide(tables), "detail": extra,
           "credit": "nflverse-data play-by-play and players (CC BY 4.0)"}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    o = main(sys.argv[1], sys.argv[2])
    print(json.dumps(o["decision"], indent=1)[:6000])
