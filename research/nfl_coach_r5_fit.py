"""The fit for the coaching engine's fifth revision (nfl_coach_sim_r5,
research/reports/nfl_coach_r5.md). RESEARCH. nflverse (CC BY 4.0) through R2's
loader; R1's seal holds (2026 week 13 on is never read; the posteam timeouts are
read under the same seal rule, row by row).

    python3 -m research.nfl_coach_r5_fit <pbp dir> <players.csv> <injuries dir>

THE RULE (written before 2025 was read for any of this):
1. THE HAZARD. The nested candidates of nfl_coach_sim_r5.features -- s6 (R3's
   form), p1 (+ the possessions a trailing offence needs against the possessions
   left), p2 (+ its timeouts), p3 (+ the leader's own need term), p4 (+ the head
   coach's earlier pull tendency) -- are each fitted on 2016-2024 (half-life 2
   seasons anchored on 2024, R3's weighting). Walking up the ladder from s6, a
   spec replaces the current one only when its 2025 log loss is lower by more
   than 2 SE (paired per team-game); the walk stops at the first spec that fails.
   The chosen spec is refitted on 2016-2025 (anchor 2025). 2026 weeks 1-12 is a
   check, read once after the choice and never used to change it.
2. THE SECOND UNIT. Over the real strategic pulls of 2016-2024 (R4's labels): per
   game, the season's lead back and top two wide receivers / tight ends (leave
   this game out), their season shares (same), and their touches after the pull.
   Latent decisions: the back sits for the rest of the game with q_b; each
   receiver sits with q_w1 if the back sat, q_w0 if not; a player who plays gets
   Binomial(n after, expit(logit(season share) + d)). Maximum likelihood over
   (q_b, q_w0, q_w1, d_b, d_w), SEs from the observed information. 2025 and 2026
   weeks 1-12 are a check (refit there and compared), never used in the tables.
Writes research/data/nfl_coach_r5_tables.json and research/data/nfl_coach/r5_fit.json.
"""
import csv
import gzip
import json
import math
import os
import sys
import time
from collections import defaultdict

import nfl_coach_sim_r3 as E3
import nfl_coach_sim_r5 as E5
from research import nfl_coach_data as CD
from research import nfl_coach_r2_data as R
from research import nfl_coach_r3_qb as QB
from research import nfl_coach_r4_data as XD
from research import nfl_coach_r4_fit as F4

TABLES = E5.TABLES_PATH
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r5_fit.json")
FIT = tuple(range(2016, 2025))
SELECT = 2025
CHECK = 2026
_FOUND = [0, 0]             # drive states whose timeouts were found in the play rows
CT_K = 5.0                  # the coach term's shrinkage: five expected pulls of prior evidence


def timeouts(dirname, season):
    """{(game id, play id): the offence's timeouts remaining} (R1's seal on every row)."""
    out = {}
    with gzip.open(CD.path(dirname, season), "rt", newline="") as fh:
        rd = csv.reader(fh)
        ix = {h: j for j, h in enumerate(next(rd))}
        js, jw, jt = ix["season"], ix["week"], ix["season_type"]
        jg, jp, jo = ix["game_id"], ix["play_id"], ix["posteam_timeouts_remaining"]
        for raw in rd:
            if CD.sealed(raw[js], raw[jw], raw[jt]):
                continue
            try:
                out[(raw[jg], int(float(raw[jp])))] = int(float(raw[jo]))
            except ValueError:
                pass
    return out


def drive_states(tg, ids, pos, tos):
    """R3's pull sequence with each drive's timeouts: [(sd, gsr, to, y)] -- the
    drives R3's `seq` holds, the timeouts read at the drive's first play."""
    firsts, seen, byd = [], set(), {}
    for p in tg["plays"]:
        d = p[R.Q_DRIVE]
        byd.setdefault(d, p)
        if d not in seen and QB._ident(p, ids, pos) is not None:
            seen.add(d)
            firsts.append(byd[d])
    found = [(tg["gid"], p[R.Q_PLAYID]) in tos for p in firsts[:len(tg["seq"])]]
    _FOUND[0] += sum(found)
    _FOUND[1] += len(found)
    return [(sd, gsr, tos.get((tg["gid"], p[R.Q_PLAYID]), 3), y)
            for (sd, gsr, y, _per), p in zip(tg["seq"], firsts)]


def coach_terms(allg, states):
    """{(season, team, week): ct} the head coach's pulls observed over R3's
    expected in his EARLIER seasons, log((o + K) / (e + K)); 0 with no history."""
    by = defaultdict(lambda: [0.0, 0.0])
    out = {}
    q3 = E3.tables().coef
    for s in sorted(allg):
        cur = defaultdict(lambda: [0.0, 0.0])
        for tg, st in zip(allg[s], states[s]):
            o, e = by[tg["coach"]]
            out[(s, tg["team"], tg["week"])] = math.log((o + CT_K) / (e + CT_K))
            c = cur[tg["coach"]]
            c[0] += 1.0 if tg["pull"] is not None else 0.0
            c[1] += sum(E3.hazard(q3, sd, gsr) for sd, gsr, _to, _y in st)
        for k, (o, e) in cur.items():
            by[k][0] += o
            by[k][1] += e
    return out


def rows_of(allg, states, cts, seasons):
    out = []
    for s in seasons:
        for tg, st in zip(allg[s], states[s]):
            ct = cts[(s, tg["team"], tg["week"])]
            out.append((s, [(sd, gsr, to, ct, y) for sd, gsr, to, y in st]))
    return out


def _era(s, c):
    return 1.0 if (c is None or s >= c) else 0.0


def fit(rows, spec, anchor, hl=2.0, iters=60, ridge=1e-2, c=None, nll_out=None):
    import numpy as np
    X, y, w = [], [], []
    for s, sq in rows:
        for sd, gsr, to, ct, yy in sq:
            X.append(E5.features(sd, gsr, to, spec, ct, _era(s, c)))
            y.append(yy)
            w.append(0.5 ** ((anchor - s) / hl))
    X, y, w = np.asarray(X), np.asarray(y, dtype=float), np.asarray(w)

    def obj(b):
        e = np.clip(X @ b, -30, 30)
        return float(np.sum(w * (y * e - np.log1p(np.exp(e)))) - 0.5 * ridge * b @ b)
    b = np.zeros(X.shape[1])
    b[0] = math.log(y.mean() / (1 - y.mean()))
    cur = obj(b)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ b, -30, 30)))
        g = X.T @ (w * (y - p)) - ridge * b
        H = (X * (w * p * (1 - p))[:, None]).T @ X + ridge * np.eye(X.shape[1])
        step = np.linalg.solve(H, g)
        t, nv = 1.0, cur
        while t > 1e-6:
            nv = obj(b + t * step)
            if nv >= cur - 1e-12:
                break
            t *= 0.5
        b = b + t * step
        done = nv - cur < 1e-10
        cur = nv
        if done:
            break
    if nll_out is not None:
        nll_out.append((-cur, len(y)))
    return [float(v) for v in b]


def era_search(fit_r, sel_r, chk_r):
    """Section 3 (added AFTER the rule refused every state candidate): the season-
    by-season cell showed R3's form calibrated through 2021 (30 real pulls against
    31.6 expected) and short from 2022 (47 against 20.3, 2022-2026 weeks 1-12) --
    a change in behaviour, not a state the features miss. Candidates: R3's form
    plus regime terms (nfl_coach_sim_r5.ERA_SPECS) switching on at a change-point
    season c in 2018-2024; the form and c chosen on 2016-2024 alone by the
    penalized likelihood (weighted log-likelihood less half the parameters times
    log drives, BIC's penalty). 2025 and 2026 weeks 1-12 were LOOKED AT in the
    diagnosis, so their gains are development figures, not a test: R5's only test
    is its registered window (prereg_r5.json)."""
    grid = []
    for sp in E5.ERA_SPECS:
        for c in range(2018, 2025):
            box = []
            coef = fit(fit_r, sp, 2024, c=c, nll_out=box)
            nll, n = box[0]
            k = len(E5.features(-10, 300, 3, sp, 0.0, 1.0))
            grid.append({"spec": sp, "c": c, "nll": round(nll, 3), "k": k, "bic": round(2 * nll + k * math.log(n), 3),
                         "coef": coef})
    base = []
    fit(fit_r, "s6", 2024, nll_out=base)
    best = min(grid, key=lambda g: g["bic"])
    s6_bic = 2 * base[0][0] + 10 * math.log(base[0][1])
    q3 = E3.tables().coef
    r3_sel = team_ll(sel_r, q3, "s6")
    r3_chk = team_ll(chk_r, q3, "s6")
    final = fit(fit_r + sel_r, best["spec"], SELECT, c=best["c"])
    return {"grid": [{k: v for k, v in g.items() if k != "coef"} for g in sorted(grid, key=lambda g: g["bic"])[:8]],
            "s6_bic": round(s6_bic, 3),
            # adopted only where the penalized likelihood prefers it to R3's form itself
            "adopted": best["bic"] < s6_bic,
            "chosen": {"spec": best["spec"], "era_from": best["c"]},
            "dev_2025_vs_r3": paired(r3_sel, team_ll(sel_r, best["coef"], best["spec"], c=best["c"])),
            "dev_2026_w1_12_vs_r3": paired(r3_chk, team_ll(chk_r, final, best["spec"], c=best["c"])),
            "final_coef": final, "fit_coef": best["coef"]}


def team_ll(rows, coef, spec, c=None):
    out = []
    for _s, sq in rows:
        ll = 0.0
        for sd, gsr, to, ct, y in sq:
            p = min(1 - 1e-9, max(1e-9, E5.hazard(coef, sd, gsr, to, spec, ct, _era(_s, c))))
            ll -= math.log(p if y else 1 - p)
        out.append(ll)
    return out


def paired(base, new):
    d = [a - b for a, b in zip(base, new)]
    n = len(d)
    m = sum(d) / n
    se = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1) / n)
    return {"gain_per_team_game": round(m, 5), "se": round(se, 5), "z": round(m / se, 2) if se else None,
            "team_games": n}


def cell(rows, coef, spec, lo=-27, hi=-17, t0=121, t1=480, c=None):
    """Pulls and their expectation where R3 missed: trailing 17-27, 2:01-8:00 left."""
    o = e = n = 0.0
    for _s, sq in rows:
        for sd, gsr, to, ct, y in sq:
            if lo <= sd <= hi and t0 <= gsr <= t1:
                o += y
                e += E5.hazard(coef, sd, gsr, to, spec, ct, _era(_s, c)) if spec else E3.hazard(coef, sd, gsr)
                n += 1
    return {"drives": int(n), "pulls": int(o), "expected": round(e, 2)}


# ---- the second unit ----------------------------------------------------------------------
def _usage(tg, ids, pos):
    """(RB/FB carries by id, targets by id, WR/TE ids) over a team-game."""
    car, tgt, wr = defaultdict(int), defaultdict(int), set()
    for p in tg["plays"]:
        if p[R.Q_KIND] == R.K_RUN and p[R.Q_RUSH] >= 0 and pos.get(ids[p[R.Q_RUSH]], "") in ("RB", "FB"):
            car[p[R.Q_RUSH]] += 1
        elif p[R.Q_KIND] == R.K_PASS and p[R.Q_RECV] >= 0:
            tgt[p[R.Q_RECV]] += 1
            if pos.get(ids[p[R.Q_RECV]], "") in ("WR", "TE"):
                wr.add(p[R.Q_RECV])
    return car, tgt, wr


def unit_rows(allg, ids_of, pos, cls, extra, seasons):
    """Per real strategic pull: (back, [receiver, receiver]) where back =
    (n RB carries after, the lead back's, his season share) and each receiver =
    (n targets after, his, his season share); None where the player did not touch
    the ball before the pull (absent or inactive). Season figures leave the game out."""
    out = []
    for s in seasons:
        ids = ids_of[s]
        season = defaultdict(lambda: [defaultdict(int), defaultdict(int), set()])
        use = []
        for tg in allg[s]:
            u = _usage(tg, ids, pos)
            use.append(u)
            a = season[tg["team"]]
            for i, v in u[0].items():
                a[0][i] += v
            for i, v in u[1].items():
                a[1][i] += v
            a[2] |= u[2]
        for tg, u in zip(allg[s], use):
            if cls(tg, ids, extra[s]) != "strategic":
                continue
            a = season[tg["team"]]
            car = {i: a[0][i] - u[0].get(i, 0) for i in a[0]}
            tgt = {i: a[1][i] - u[1].get(i, 0) for i in a[1]}
            ncar, ntgt = sum(car.values()), sum(tgt.values())
            if ncar < 20 or ntgt < 40:
                continue
            lead = max(car, key=car.get)
            top2 = sorted((i for i in a[2]), key=lambda i: -tgt.get(i, 0))[:2]
            cut = tg["pull_drive"]
            pre_c, pre_t, n_c, k_c, n_t = defaultdict(int), defaultdict(int), 0, 0, 0
            k_t = defaultdict(int)
            for p in tg["plays"]:
                isrb = p[R.Q_KIND] == R.K_RUN and p[R.Q_RUSH] >= 0 and pos.get(ids[p[R.Q_RUSH]], "") in ("RB", "FB")
                ist = p[R.Q_KIND] == R.K_PASS and p[R.Q_RECV] >= 0
                if p[R.Q_DRIVE] < cut:
                    if isrb:
                        pre_c[p[R.Q_RUSH]] += 1
                    if ist:
                        pre_t[p[R.Q_RECV]] += 1
                else:
                    if isrb:
                        n_c += 1
                        k_c += p[R.Q_RUSH] == lead
                    if ist:
                        n_t += 1
                        k_t[p[R.Q_RECV]] += 1
            back = (n_c, k_c, car[lead] / ncar) if pre_c.get(lead) and n_c >= 1 else None
            recs = [(n_t, k_t.get(i, 0), tgt[i] / ntgt) if pre_t.get(i) and n_t >= 1 and tgt.get(i) else None
                    for i in top2]
            if back is not None or any(recs):
                out.append((back, recs))
    return out


def _lb(n, k, p, d):
    """log Binomial(k; n, expit(logit p + d))."""
    p = min(1 - 1e-6, max(1e-6, p))
    x = math.log(p / (1 - p)) + d
    q = 1.0 / (1.0 + math.exp(-x))
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1) + k * math.log(q) + (n - k) * math.log(1 - q)


def _game_ll(row, th):
    qb, qw0, qw1, db, dw = th
    back, recs = row
    tot = 0.0
    for bs, pb in ((1, qb), (0, 1 - qb)):
        if back is None:
            lb = 1.0
        else:
            n, k, p = back
            lb = (1.0 if k == 0 else 0.0) if bs else math.exp(_lb(n, k, p, db))
        qw = qw1 if bs else qw0
        lr = 1.0
        for r in recs:
            if r is None:
                continue
            n, k, p = r
            lr *= qw * (1.0 if k == 0 else 0.0) + (1 - qw) * math.exp(_lb(n, k, p, dw))
        tot += pb * lb * lr
    return math.log(max(tot, 1e-300))


def _unpack(v):
    s = lambda x: 1.0 / (1.0 + math.exp(-x))     # noqa: E731
    return (s(v[0]), s(v[1]), s(v[2]), v[3], v[4])


def unit_fit(rows):
    def nll(v):
        th = _unpack(v)
        return -sum(_game_ll(r, th) for r in rows)
    v = [0.0, -1.0, 0.0, 0.0, 0.0]
    cur = nll(v)
    step = 1.0
    while step > 1e-3:
        moved = False
        for j in range(5):
            for sg in (1, -1):
                w = list(v)
                w[j] += sg * step
                f = nll(w)
                if f < cur - 1e-9:
                    v, cur, moved = w, f, True
        if not moved:
            step /= 2
    # SEs on the natural scale from the observed information (central differences)
    h = 1e-3
    th0 = list(_unpack(v))

    def nll_t(t):
        t = [min(1 - 1e-6, max(1e-6, t[0])), min(1 - 1e-6, max(1e-6, t[1])), min(1 - 1e-6, max(1e-6, t[2])),
             t[3], t[4]]
        return -sum(_game_ll(r, t) for r in rows)
    import numpy as np
    H = np.zeros((5, 5))
    for a in range(5):
        for b in range(a, 5):
            def at(da, db_):
                t = list(th0)
                t[a] += da
                t[b] += db_
                return nll_t(t)
            H[a, b] = H[b, a] = (at(h, h) - at(h, -h) - at(-h, h) + at(-h, -h)) / (4 * h * h)
    try:
        se = [float(x) for x in np.sqrt(np.clip(np.diag(np.linalg.inv(H)), 0, None))]
    except np.linalg.LinAlgError:
        se = [None] * 5
    nm = ("q_b", "q_w0", "q_w1", "d_b", "d_w")
    return {"params": {k: round(x, 4) for k, x in zip(nm, th0)},
            "se": {k: (round(x, 4) if x is not None else None) for k, x in zip(nm, se)},
            "games": len(rows), "with_back": sum(1 for b, _ in rows if b is not None),
            "receivers": sum(1 for _b, rc in rows for r in rc if r is not None), "nll": round(cur, 3)}


def observed(rows):
    """The two observables r4_checks.py reads, on this file's definitions: the lead
    back with 3+ backs' carries after the pull got none; both top-two targets got
    none with 3+ targets after."""
    b = [r[0] for r in rows if r[0] is not None and r[0][0] >= 3]
    t = [r[1] for r in rows if len(r[1]) == 2 and all(x is not None and x[0] >= 3 for x in r[1])]
    return {"back_games": len(b), "back_sat": round(sum(1 for x in b if x[1] == 0) / max(1, len(b)), 4),
            "top2_games": len(t), "top2_none": round(sum(1 for x in t if x[0][1] == 0 and x[1][1] == 0) /
                                                    max(1, len(t)), 4)}


def main(pbp, players_csv, inj):
    allg, pos, ids_of = QB.load_all(pbp, players_csv, tuple(range(2016, 2027)))
    states = {}
    for s in allg:
        tos = timeouts(pbp, s)
        states[s] = [drive_states(tg, ids_of[s], pos, tos) for tg in allg[s]]
    cts = coach_terms(allg, states)
    fit_r = rows_of(allg, states, cts, FIT)
    sel_r = rows_of(allg, states, cts, (SELECT,))
    chk_r = rows_of(allg, states, cts, (CHECK,))
    cand = {sp: fit(fit_r, sp, 2024) for sp in E5.SPECS}
    ll = {sp: team_ll(sel_r, cand[sp], sp) for sp in E5.SPECS}
    walk, cur = [], "s6"
    for sp in E5.SPECS[1:]:
        g = paired(ll[cur], ll[sp])
        ok = g["z"] is not None and g["z"] > 2.0
        walk.append({"from": cur, "to": sp, "gain": g, "kept": ok})
        if not ok:
            break
        cur = sp
    best = cur
    final = fit(fit_r + sel_r, best, SELECT)
    final_s6 = fit(fit_r + sel_r, "s6", SELECT)
    q3 = E3.tables().coef
    r3_chk = []
    for _s, sq in chk_r:
        x = 0.0
        for sd, gsr, _to, _ct, y in sq:
            p = min(1 - 1e-9, max(1e-9, E3.hazard(q3, sd, gsr)))
            x -= math.log(p if y else 1 - p)
        r3_chk.append(x)
    every = {sp: paired(ll["s6"], ll[sp]) for sp in E5.SPECS[1:]}
    out = {"fit_seasons": list(FIT), "select": SELECT, "check": f"{CHECK} weeks 1-12", "walk": walk, "spec": best,
           "select_gain_vs_s6_every_spec": every,
           "check_gain_vs_r3_shipped": paired(r3_chk, team_ll(chk_r, final, best)),
           "check_gain_vs_s6_refit": paired(team_ll(chk_r, final_s6, "s6"), team_ll(chk_r, final, best)),
           "credit": "nflverse-data play-by-play, players and injuries (CC BY 4.0)",
           "timeouts_found": {"found": _FOUND[0], "drives": _FOUND[1]}}
    miss = {}
    for nm, rr in (("2024", rows_of(allg, states, cts, (2024,))), ("2025", sel_r), ("2026_w1_12", chk_r)):
        miss[nm] = {"r3": cell(rr, q3, None), "r5": cell(rr, final, best)}
    out["r3_miss_cell"] = miss
    # the miss season by season under every candidate (fitted on 2016-2024): is the
    # cell a form the features cannot reach, or a recent change in behaviour?
    by_season = {}
    for s in sorted(allg):
        rr = rows_of(allg, states, cts, (s,))
        c = {"real": cell(rr, q3, None)["pulls"], "drives": cell(rr, q3, None)["drives"],
             "r3_shipped": cell(rr, q3, None)["expected"]}
        for sp in E5.SPECS:
            c[sp] = cell(rr, cand[sp], sp)["expected"]
        by_season[str(s)] = c
    out["miss_cell_by_season"] = by_season
    era = era_search(fit_r, sel_r, chk_r)
    out["era"] = {k: v for k, v in era.items() if k not in ("final_coef", "fit_coef")}
    esp, ec = era["chosen"]["spec"], era["chosen"]["era_from"]
    for s in sorted(allg):
        rr = rows_of(allg, states, cts, (s,))
        by_season[str(s)]["era_fit_2016_2024"] = cell(rr, era["fit_coef"], esp, c=ec)["expected"]
        by_season[str(s)]["era_final"] = cell(rr, era["final_coef"], esp, c=ec)["expected"]
    rule_spec, rule_coef = best, final
    if era["adopted"]:
        best, final = esp, era["final_coef"]
    else:
        ec = None
    # the second unit
    cls = F4.make_cls(F4.reports(inj, range(2016, 2026)))
    extra = {s: XD.load_extra(pbp, s) for s in allg}
    ur = unit_rows(allg, ids_of, pos, cls, extra, FIT)
    uf = unit_fit(ur)
    ud = unit_rows(allg, ids_of, pos, cls, extra, (SELECT, CHECK))
    out["second_unit"] = {"fit": uf, "check_refit": unit_fit(ud), "observed_fit": observed(ur),
                          "observed_check": observed(ud)}
    tables = {"version": E5.VERSION, "spec": best, "coef": final, "fit_seasons": list(FIT) + [SELECT],
              "anchor_season": SELECT, "half_life": 2.0, "poss_s": E5.POSS_S, "ct_k": CT_K, "era_from": ec,
              "rule_spec": rule_spec, "rule_coef": rule_coef,
              "hazard_basis": "the rule (section 1) refused every state candidate (s6 kept); the shipped hazard "
                              "is section 3's regime candidate, chosen on 2016-2024 by BIC after the diagnosis "
                              "read 2022-2026 -- tested only by prereg_r5.json's window",
              "second_unit": dict(uf["params"]), "credit": out["credit"],
              "seal": "2026 week 13 on and every later season: never read by this fit",
              "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(TABLES, "w") as fh:
        json.dump(tables, fh, indent=1, sort_keys=True)
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2], sys.argv[3]), indent=1)[:6000])
