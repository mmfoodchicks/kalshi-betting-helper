"""The quarterback substitution model for the coaching engine's third revision
(nfl_coach_sim_r3, research/reports/nfl_coach_r3.md). RESEARCH: nflverse
play-by-play and players (CC BY 4.0) through research/nfl_coach_r2_data.py, so
R1's seal holds: 2026 week 13 on is never read here.

    python3 -m research.nfl_coach_r3_qb <pbp dir> <players.csv>

What is learned, from the plays themselves and never from a score threshold:

  * the game's starter is the quarterback on his team's first QB-identifying
    snap (a pass, a sack, a spike, a kneel);
  * a drive is "identified" when it holds such a snap, and its quarterback is
    the one on its first; the starter is PULLED at the first identified drive
    whose quarterback is someone else, and the outcome sequence of a team-game
    is 0 for every identified drive he starts, then a 1 (or nothing);
  * the hazard of a pull at an identified drive's first snap is a logistic in
    the margin against the time left: z = margin / sqrt(tau) / 10 for the
    lead and for the deficit apart (tau = (game seconds left + 30) / 3600, the
    scale on which a margin decides a game), through log(1 + z) so it
    saturates, plus log tau. The specification is chosen on 2024 from five
    nested candidates, fitted on 2016-2023;
  * what the audit also measures: whether the pulled starter returns, the
    starter-already-out case, the head coach's own habit, play selection with
    a backup in, and whether backs and receivers are substituted the same way.

Writes research/data/nfl_coach/r3_evidence.json and the tables
research/data/nfl_coach_r3_tables.json.
"""
import json
import math
import os
import sys
import time
from collections import defaultdict

from research import nfl_coach_r2_data as R

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r3_evidence.json")
TABLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach_r3_tables.json")
FIT_SEASONS = tuple(range(2016, 2024))
SELECT = 2024
DEV = (2025, 2026)
SPECS = ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7")
# fit-season weights 0.5 ** ((2023 - season) / H): pulls rose over the decade (2016-19 ~69 a
# season, 2023-25 ~100 on 17 games), so recency is a candidate, chosen on 2024 with the spec
HALF_LIVES = (None, 4.0, 2.0)
_DROP = (R.K_PASS, R.K_SACK, R.K_SPIKE)


# ---- the state and the features (the engine computes exactly these) ---------------------
def feats(sd, gsr, reg, spec):
    """The hazard's features at a drive's first snap: `sd` the offence's margin,
    `gsr` the game's seconds left (overtime: the period's), `reg` 1 when the
    game's starter is the team's season starter so far."""
    tau = (max(0, gsr) + 30) / 3600.0
    if spec == "s7":
        # the margin in touchdowns and the time left apart, each curved, and
        # their products: the leading and the trailing pull may each peak at
        # their own time (s3-s6 tie the time to the margin through z, and put
        # too many pulls in the third quarter and too few in the last 8:00)
        ml, mt, q = math.log1p(max(sd, 0) / 7.0), math.log1p(max(-sd, 0) / 7.0), math.log(tau)
        return [1.0, ml, ml * ml, mt, mt * mt, q, q * q, ml * q, mt * q, ml * q * q, mt * q * q]
    st = math.sqrt(tau)
    zl = math.log1p(max(sd, 0) / st / 10.0)
    zt = math.log1p(max(-sd, 0) / st / 10.0)
    if spec == "s0":
        return [1.0]
    if spec == "s1":
        return [1.0, zl, zt]
    f = [1.0, zl, zl * zl, zt, zt * zt]
    if spec == "s2":
        return f
    lt = math.log(tau)
    f.append(lt)
    if spec == "s3":
        return f
    if spec == "s4":
        return f + [1.0 - reg]
    # s5: the lead's and the deficit's pull each change with the time left in
    # their own way (a trailing team is pulled later, then faster)
    f += [zl * lt, zt * lt]
    if spec == "s5":
        return f
    return f + [zl * zl * lt, zt * zt * lt]


def hazard(coef, x):
    e = sum(c * v for c, v in zip(coef, x))
    e = max(-30.0, min(30.0, e))
    return 1.0 / (1.0 + math.exp(-e))


# ---- the data ---------------------------------------------------------------------------
def _ident(p, ids, pos):
    """The quarterback a snap identifies, or None: a pass, a sack, a spike or a
    kneel, by a player listed at quarterback. Scrambles and designed runs do not
    identify: R1's engine plays both as runs and cannot tell them apart, and the
    engine's pull is drawn on exactly the drives this definition sees."""
    k = p[R.Q_KIND]
    if k in _DROP:
        i = p[R.Q_PASSER]
    elif k == R.K_KNEEL:
        i = p[R.Q_RUSH] if p[R.Q_RUSH] >= 0 else p[R.Q_PASSER]
    else:
        return None
    if i is None or i < 0 or pos.get(ids[i], "") != "QB":
        return None
    return i


def team_games(sd_, pos, career=None):
    """Per team-game (regular season): the identified drives in order with their
    first-snap state and quarterback, the game's starter, whether he was the
    season starter so far, the pull (drive index or None), whether he came
    back, and per-play rows for the play-selection and substitution measures.
    `career` {player: dropbacks before this season} is updated in place."""
    ids = sd_["players"]
    bt = defaultdict(list)
    for g in sd_["games"]:
        if g["st"] != 2:
            continue
        for side in (0, 1):
            bt[g["home"] if side == 0 else g["away"]].append((g["week"], g, side))
    out = []
    car = career if career is not None else defaultdict(int)
    for t, lst in bt.items():
        lst.sort(key=lambda x: x[0])
        season_drop = defaultdict(int)
        for ng, (wk, g, side) in enumerate(lst):
            h = 1 if side == 0 else 0
            pl = [p for p in g["plays"] if p[R.Q_OFFH] == h]
            first = None
            drives = []                 # [(drive no, state play, qb)]
            seen = set()
            byd = {}
            for p in pl:
                d = p[R.Q_DRIVE]
                if d not in byd:
                    byd[d] = p
                q = _ident(p, ids, pos)
                if q is None:
                    continue
                if first is None:
                    first = q
                if d not in seen:
                    seen.add(d)
                    drives.append((d, byd[d], q))
            if first is None:
                for p in pl:
                    if p[R.Q_KIND] in _DROP and p[R.Q_PASSER] >= 0:
                        season_drop[p[R.Q_PASSER]] += 1
                continue
            reg = 1 if (ng == 0 or not season_drop or max(season_drop, key=season_drop.get) == first) else 0
            seq, pull, back, pull_drive = [], None, False, None
            for j, (d, sp, q) in enumerate(drives):
                if pull is None:
                    seq.append((sp[R.Q_SD], sp[R.Q_GSR] if sp[R.Q_PER] <= 4 else min(sp[R.Q_GSR], 600),
                                1 if q != first else 0, sp[R.Q_PER]))
                    if q != first:
                        pull, pull_drive = j, d
                elif q == first:
                    back = True
            backup = drives[pull][2] if pull is not None else None
            me = g["home_pts"] if side == 0 else g["away_pts"]
            op = g["away_pts"] if side == 0 else g["home_pts"]
            out.append({"season": sd_["season"], "week": wk, "team": t, "gid": g["id"], "side": side,
                        "coach": g["home_coach"] if side == 0 else g["away_coach"], "reg": reg,
                        "seq": seq, "pull": pull, "pull_drive": pull_drive, "back": back, "starter": first, "backup": backup,
                        "backup_career": car.get(backup, 0) if backup is not None else None,
                        "margin": me - op, "plays": pl, "game": g})
            for p in pl:
                if p[R.Q_KIND] in _DROP + (R.K_SCR,) and p[R.Q_PASSER] >= 0:
                    season_drop[p[R.Q_PASSER]] += 1
                    car[p[R.Q_PASSER]] = car.get(p[R.Q_PASSER], 0) + 1
    return out


# ---- the fit ----------------------------------------------------------------------------
def rows(tgs, spec):
    return [(feats(sd, gsr, tg["reg"], spec), y, k) for k, tg in enumerate(tgs) for sd, gsr, y, _per in tg["seq"]]


def season_w(season, hl, anchor=FIT_SEASONS[-1]):
    return 1.0 if hl is None else 0.5 ** ((anchor - season) / hl)


def fit(tgs, spec, iters=60, ridge=1e-2, hl=None, anchor=FIT_SEASONS[-1]):
    """Penalized logistic regression, Newton's method with step halving (the
    plain Newton step diverged on the quadratic candidates: probabilities of 1
    on drives that were not pulls)."""
    import numpy as np
    rr = rows(tgs, spec)
    X = np.asarray([r[0] for r in rr])
    y = np.asarray([r[1] for r in rr], dtype=float)
    w = np.asarray([season_w(tgs[r[2]]["season"], hl, anchor) for r in rr])

    def obj(b):
        e = np.clip(X @ b, -30, 30)
        return float(np.sum(w * (y * e - np.log1p(np.exp(e)))) - 0.5 * ridge * b @ b)

    b = np.zeros(X.shape[1])
    m = max(y.mean(), 1e-6)
    b[0] = math.log(m / (1 - m))
    cur = obj(b)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(X @ b, -30, 30)))
        g = X.T @ (w * (y - p)) - ridge * b
        H = (X * (w * p * (1 - p))[:, None]).T @ X + ridge * np.eye(X.shape[1])
        step = np.linalg.solve(H, g)
        t = 1.0
        while t > 1e-6:
            nb = b + t * step
            nv = obj(nb)
            if nv >= cur - 1e-12:
                break
            t *= 0.5
        if nv - cur < 1e-10:
            b, cur = nb, nv
            break
        b, cur = nb, nv
    return [float(v) for v in b]


def team_ll(tgs, coef, spec, mult=None):
    """Per team-game log loss of its pull sequence (`mult`: a hazard multiplier
    per team-game index, the head-coach term)."""
    out = []
    for k, tg in enumerate(tgs):
        ll = 0.0
        for sd, gsr, y, _per in tg["seq"]:
            p = hazard(coef, feats(sd, gsr, tg["reg"], spec))
            if mult is not None:
                p = min(0.999, p * mult[k])
            p = min(1 - 1e-9, max(1e-9, p))
            ll -= math.log(p if y else 1 - p)
        out.append(ll)
    return out


def paired(a, b):
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    m = sum(d) / n
    se = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1) / n)
    return {"gain_per_team_game": round(m, 5), "se": round(se, 5), "z": round(m / se, 2) if se else None,
            "team_games": n}


# ---- the measures -----------------------------------------------------------------------
_LEAD = ((-99, -28), (-27, -17), (-16, -9), (-8, 8), (9, 16), (17, 27), (28, 99))
_TIME = ((1801, 3600), (901, 1800), (481, 900), (121, 480), (0, 120))


def cell_table(tgs, coef, spec):
    """Observed pulls against the model's expectation, drives, by margin x time
    left (a calibration table, never an input)."""
    c = defaultdict(lambda: [0, 0.0, 0])
    for tg in tgs:
        for sd, gsr, y, _per in tg["seq"]:
            a = next(k for k, (lo, hi) in enumerate(_LEAD) if lo <= sd <= hi)
            b = next(k for k, (lo, hi) in enumerate(_TIME) if lo <= gsr <= hi)
            v = c[(a, b)]
            v[0] += y
            v[1] += hazard(coef, feats(sd, gsr, tg["reg"], spec))
            v[2] += 1
    return {f"{_LEAD[a][0]}..{_LEAD[a][1]} | {_TIME[b][0]}..{_TIME[b][1]}s":
            {"drives": v[2], "pulls": v[0], "expected": round(v[1], 2)} for (a, b), v in sorted(c.items())}


def returns(tgs):
    """Did the pulled starter come back? Split by the pull's own state: a big
    margin late (rest) or not (mostly injury)."""
    acc = {"rest": [0, 0], "other": [0, 0]}
    for tg in tgs:
        if tg["pull"] is None:
            continue
        sd, gsr, _y, _per = tg["seq"][-1]
        tau = (gsr + 30) / 3600.0
        rest = abs(sd) / math.sqrt(tau) / 10.0 >= 3.0
        a = acc["rest" if rest else "other"]
        a[0] += tg["back"]
        a[1] += 1
    return {k: {"pulls": v[1], "starter_came_back": v[0], "rate": round(v[0] / v[1], 4) if v[1] else None}
            for k, v in acc.items()}


def coach_term(fit_tgs, test_tgs, coef, spec, ks=(2.0, 5.0, 10.0, 20.0)):
    """A head coach's own habit: observed over expected pulls per coach on the
    fit seasons (dispersion against Poisson), then his shrunk multiplier
    (O + k) / (E + k) applied to later seasons' hazards, against none."""
    O, E = defaultdict(float), defaultdict(float)
    for tg in fit_tgs:
        for sd, gsr, y, _per in tg["seq"]:
            O[tg["coach"]] += y
            E[tg["coach"]] += hazard(coef, feats(sd, gsr, tg["reg"], spec))
    big = [c for c in E if E[c] >= 3.0]
    chi = sum((O[c] - E[c]) ** 2 / E[c] for c in big)
    out = {"coaches": len(big), "dispersion_chi2": round(chi, 1), "df": len(big),
           "ratio": round(chi / max(len(big), 1), 3)}
    base = team_ll(test_tgs, coef, spec)
    for k in ks:
        mult = [(O.get(tg["coach"], 0.0) + k) / (E.get(tg["coach"], 0.0) + k) for tg in test_tgs]
        out[f"k{int(k)}"] = paired(base, team_ll(test_tgs, coef, spec, mult))
    return out


def play_selection(tgs):
    """Pass rate over expected (nflverse xpass) on downs 1-3 with the margin 17+
    either way in the second half: the starter's snaps, an experienced backup's
    (300+ dropbacks before this season, since 2016) and an inexperienced one's."""
    acc = defaultdict(lambda: [0.0, 0])
    for tg in tgs:
        ids_bk = tg["backup"]
        exp = tg["backup_career"]
        pulled = False
        pl = tg["plays"]
        cut = tg["pull_drive"]            # from the pull's drive on, the backup's snaps
        for p in pl:
            if p[R.Q_KIND] not in R.SCRIMMAGE or p[R.Q_XP] < 0 or not (1 <= p[R.Q_DOWN] <= 3):
                continue
            if p[R.Q_PER] < 3 or abs(p[R.Q_SD]) < 17:
                continue
            if cut is not None and p[R.Q_DRIVE] >= cut:
                pulled = True
            who = "starter" if not pulled else ("backup_experienced" if (exp or 0) >= 300 else "backup_new")
            if ids_bk is None and pulled:
                continue
            side = "lead" if p[R.Q_SD] > 0 else "trail"
            a = acc[(who, side)]
            a[0] += (p[R.Q_KIND] in (R.K_SCR, R.K_PASS, R.K_SACK)) - p[R.Q_XP] / 1000.0
            a[1] += 1
    return {f"{w} {s}": {"snaps": v[1], "pass_over_expected": round(v[0] / v[1], 4) if v[1] else None}
            for (w, s), v in sorted(acc.items())}


def rb_persistence(tgs, pos, ids_of):
    """Backs: with a 17+ lead in the second half, once a back other than the
    game's lead back carries, how much of the rest goes back to the lead back,
    against his share of the same state before it; the same in close play."""
    res = {}
    for name, cond in (("lead17", lambda p: p[R.Q_PER] >= 3 and p[R.Q_SD] >= 17),
                       ("close", lambda p: abs(p[R.Q_SD]) <= 8 and p[R.Q_PER] <= 3)):
        before, after = [0, 0], [0, 0]
        for tg in tgs:
            ids = ids_of[tg["season"]]
            runs = [p for p in tg["plays"] if p[R.Q_KIND] == R.K_RUN and p[R.Q_RUSH] >= 0
                    and pos.get(ids[p[R.Q_RUSH]], "") in ("RB", "FB")]
            if len(runs) < 12:
                continue
            cnt = defaultdict(int)
            for p in runs:
                cnt[p[R.Q_RUSH]] += 1
            lead = max(cnt, key=cnt.get)
            st = [p for p in runs if cond(p)]
            sw = next((k for k, p in enumerate(st) if p[R.Q_RUSH] != lead), None)
            if sw is None:
                continue
            for p in st[:sw]:
                before[0] += p[R.Q_RUSH] == lead
                before[1] += 1
            for p in st[sw + 1:]:
                after[0] += p[R.Q_RUSH] == lead
                after[1] += 1
        res[name] = {"lead_share_before_first_other": round(before[0] / max(before[1], 1), 4),
                     "lead_share_after": round(after[0] / max(after[1], 1), 4),
                     "carries_before": before[1], "carries_after": after[1]}
    return res


def starter_share(tgs):
    """The game's starter's share of his team's dropbacks by final margin."""
    acc = defaultdict(lambda: [0, 0])
    for tg in tgs:
        m = tg["margin"]
        k = "lost 17+" if m <= -17 else "within 16" if m < 17 else "won 17+"
        for p in tg["plays"]:
            if p[R.Q_KIND] in _DROP and p[R.Q_PASSER] >= 0:
                acc[k][0] += p[R.Q_PASSER] == tg["starter"]
                acc[k][1] += 1
    return {k: round(v[0] / v[1], 4) for k, v in acc.items()}


def load_all(pbp, players_csv, seasons):
    pos = R.positions(players_csv)
    career = {}
    out, ids_of = {}, {}
    for s in seasons:
        d = R.load_season(pbp, s)
        # careers are keyed by gsis id across seasons
        local = {i: career.get(pid, 0) for i, pid in enumerate(d["players"])}
        tg = team_games(d, pos, local)
        for i, pid in enumerate(d["players"]):
            if local.get(i, 0):
                career[pid] = local[i]
        out[s] = tg
        ids_of[s] = d["players"]
    return out, pos, ids_of


def main(pbp, players_csv):
    allg, pos, ids_of = load_all(pbp, players_csv, tuple(range(2016, 2027)))
    fitg = [tg for s in FIT_SEASONS for tg in allg[s]]
    ev = {"fit_seasons": list(FIT_SEASONS), "select": SELECT,
          "credit": "nflverse-data play-by-play and players (CC BY 4.0)",
          "counts": {str(s): {"team_games": len(allg[s]), "pulls": sum(tg["pull"] is not None for tg in allg[s]),
                              "identified_drives": sum(len(tg["seq"]) for tg in allg[s])} for s in allg}}
    cand = {(sp, hl): fit(fitg, sp, hl=hl) for sp in SPECS for hl in HALF_LIVES}
    sel = {k: sum(team_ll(allg[SELECT], c, k[0])) / len(allg[SELECT]) for k, c in cand.items()}
    bk = min(sel, key=sel.get)
    best, best_hl = bk
    coefs = {sp: cand[(sp, best_hl)] for sp in SPECS}
    ev["select_ll_per_team_game"] = {f"{k[0]} hl={k[1]}": round(v, 5) for k, v in sel.items()}
    ev["spec"], ev["half_life"] = best, best_hl
    ev["coef_fit_2016_2023"] = coefs[best]
    # the selected form refitted with the selection season in (as R1 did: choose on
    # 2024, then 2025 can only block). The recent rise in pulls is what the
    # half-life is for; 2024 is its latest season
    final = fit(fitg + allg[SELECT], best, hl=best_hl, anchor=SELECT)
    final0 = fit(fitg + allg[SELECT], "s0", hl=best_hl, anchor=SELECT)
    ev["coef"], ev["coef_constant"] = final, final0
    for s in DEV:
        ev[f"final_gain_vs_constant_{s}"] = paired(team_ll(allg[s], final0, "s0"),
                                                   team_ll(allg[s], final, best))
    ev["cells_dev_final"] = cell_table([tg for s in DEV for tg in allg[s]], final, best)
    for s in (SELECT,) + DEV:
        ev[f"gain_vs_constant_{s}"] = paired(team_ll(allg[s], coefs["s0"], "s0"), team_ll(allg[s], coefs[best], best))
        ev[f"gain_s4_vs_s3_{s}"] = paired(team_ll(allg[s], coefs["s3"], "s3"), team_ll(allg[s], coefs["s4"], "s4"))
    ev["by_season"] = {str(s): {"pulls": sum(tg["pull"] is not None for tg in allg[s]),
                                "expected": round(sum(hazard(final, feats(sd, gsr, tg["reg"], best))
                                                      for tg in allg[s] for sd, gsr, _y, _p in tg["seq"]), 1)}
                       for s in allg}
    ev["cells_fit"] = cell_table(fitg, coefs[best], best)
    ev["cells_dev"] = cell_table([tg for s in (SELECT,) + DEV for tg in allg[s]], coefs[best], best)
    ev["returns_fit"] = returns(fitg)
    ev["returns_dev"] = returns([tg for s in (SELECT,) + DEV for tg in allg[s]])
    ev["coach"] = coach_term(fitg, [tg for s in (SELECT, 2025) for tg in allg[s]], coefs[best], best)
    ev["play_selection"] = play_selection(fitg + [tg for s in (SELECT,) + DEV for tg in allg[s]])
    ev["rb_persistence"] = rb_persistence(fitg, pos, ids_of)
    ev["starter_share_by_margin"] = {str(s): starter_share(allg[s]) for s in (SELECT,) + DEV}
    ex = {}
    for name, sd, gsr in (("lead 35, 8:00 left", 35, 480), ("trail 35, 5:00 left", -35, 300),
                          ("lead 24, 2:00 left", 24, 120), ("lead 7, 8:00 left", 7, 480),
                          ("lead 7, 2:00 left", 7, 120), ("tied, first quarter", 0, 3300),
                          ("lead 21, start of Q4", 21, 900), ("lead 28, start of Q4", 28, 900)):
        ex[name] = round(hazard(final, feats(sd, gsr, 1, best)), 4)
    ev["owner_examples_hazard_per_drive"] = ex
    with open(OUT, "w") as fh:
        json.dump(ev, fh, indent=1, sort_keys=True)
    tables = {"version": "coach-r3.0", "fit_seasons": list(FIT_SEASONS) + [SELECT], "select_season": SELECT,
              "spec": best, "half_life": best_hl, "anchor_season": SELECT, "coef": final, "coef_constant": final0,
              "credit": ev["credit"],
              "seal": "2026 week 13 on and every later season: never read by this fit",
              "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "pull_rule": "a pulled starter stays out (returns_fit gives the measured rate)",
              "returns_fit": ev["returns_fit"]}
    with open(TABLES, "w") as fh:
        json.dump(tables, fh, indent=1, sort_keys=True)
    return ev


if __name__ == "__main__":
    e = main(sys.argv[1], sys.argv[2])
    print(json.dumps({k: e[k] for k in ("spec", "select_ll_per_team_game", "owner_examples_hazard_per_drive",
                                         "returns_fit", "returns_dev")}, indent=1))
