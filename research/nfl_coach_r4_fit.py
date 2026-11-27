"""The fit for the coaching engine's fourth revision (nfl_coach_sim_r4,
research/reports/nfl_coach_r4.md): what a quarterback substitution changes, and
the two kinds of substitution. RESEARCH. nflverse (CC BY 4.0) through R2's
loader, research/nfl_coach_r4_data.py and the weekly injury reports; R1's seal
holds.

    python3 -m research.nfl_coach_r4_fit <pbp dir> <players.csv> <injuries dir>

The kind of a pull, from two independent records and no score threshold:
  injury     the play descriptions name the starter "was injured" before the
             backup's first drive, OR he is on the NEXT week's injury report
             (Out, Doubtful or Questionable);
  strategic  neither.
An effect enters the engine only if it is 3+ SE on the fit seasons (2016-2024)
AND the same sign on 2025-2026 weeks 1-12 (the rule written before the dev
numbers were looked at); otherwise it is 0 (a multiplier 1). Writes
research/data/nfl_coach_r4_tables.json and research/data/nfl_coach/r4_fit.json.
"""
import csv
import json
import math
import os
import sys
import time
from collections import defaultdict

from research import nfl_coach_r2_data as R
from research import nfl_coach_r3_qb as QB
from research import nfl_coach_r4_data as X
from research import nfl_coach_r4_evidence as EV

TABLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach_r4_tables.json")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r4_fit.json")
FIT = tuple(range(2016, 2025))
DEV = (2025, 2026)
Z_MIN = 3.0


def reports(inj_dir, seasons):
    rep = set()
    for s in seasons:
        fp = os.path.join(inj_dir, f"injuries_{s}.csv")
        if not os.path.exists(fp):
            continue
        with open(fp, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("game_type") == "REG" and r.get("report_status") in ("Out", "Doubtful", "Questionable"):
                    rep.add((int(r["season"]), int(r["week"]), r["gsis_id"]))
    return rep


def make_cls(rep):
    def cls(tg, ids, ex):
        if tg["pull"] is None:
            return None
        if EV.classify(tg, ids, ex) == "injury":
            return "injury"
        if (tg["season"], tg["week"] + 1, ids[tg["starter"]]) in rep:
            return "injury"
        return "strategic"
    return cls


def matched(ctrl, games, metric, sides=None):
    """Backup minus matched starter, cell by cell, weighted by the backup's
    counts: (diff, the matched control mean, backup mean of xpass for "proe",
    snaps); `sides` restricts to margin bins."""
    num = den = cm = 0.0
    for g in games:
        for c, ms in g["cells"].items():
            if sides is not None and c[0] not in sides:
                continue
            xs = ms.get(metric)
            a = ctrl.get((c, metric))
            if not xs or not a or a[1] < 20:
                continue
            num += sum(xs) - len(xs) * a[0] / a[1]
            cm += len(xs) * a[0] / a[1]
            den += len(xs)
    return (num / den, cm / den, den) if den else (0.0, 0.0, 0)


def mh_or(ctrl, games, metric):
    """Mantel-Haenszel odds ratio, backup against starter, over the cells."""
    nu = de = 0.0
    for g in games:
        for c, ms in g["cells"].items():
            xs = ms.get(metric)
            a = ctrl.get((c, metric))
            if not xs or not a or a[1] < 20:
                continue
            x1, n1, x0, n0 = sum(xs), len(xs), a[0], a[1]
            N = n0 + n1
            nu += x1 * (n0 - x0) / N
            de += x0 * (n1 - x1) / N
    return nu / de if de > 0 else 1.0


def logit(p):
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def fit_type(pulls):
    """P(injury | pull, state): logistic on [1, zl, zt, log tau] (Newton)."""
    import numpy as np
    X_, y = [], []
    for g in pulls:
        sd, gsr, _y, _p = g["tg"]["seq"][-1]
        f = QB.feats(sd, gsr, 1, "s3")
        X_.append([f[0], f[1], f[3], f[5]])
        y.append(1.0 if g["cls"] == "injury" else 0.0)
    X_, y = np.asarray(X_), np.asarray(y)
    b = np.zeros(4)
    for _ in range(30):
        p = 1 / (1 + np.exp(-np.clip(X_ @ b, -30, 30)))
        H = (X_ * (p * (1 - p))[:, None]).T @ X_ + 1e-2 * np.eye(4)
        b = b + np.linalg.solve(H, X_.T @ (y - p) - 1e-2 * b)
    return [float(v) for v in b]


def returns(pulls, ids_of):
    """Injury pulls: did the starter come back, and after how many of the
    backup's identified drives (1, 2, 3, 4+)."""
    back, n, gaps = 0, 0, defaultdict(int)
    for g in pulls:
        if g["cls"] != "injury":
            continue
        n += 1
        tg = g["tg"]
        if not tg["back"]:
            continue
        back += 1
        # identified drives with the backup before the starter's return
        ids = ids_of[g["season"]]
        pos_drives = []
        seen = set()
        for p in tg["plays"]:
            q = QB._ident(p, ids, {ids[tg["starter"]]: "QB", ids[tg["backup"]]: "QB"}) if tg["backup"] is not None else None
            if q is None or p[R.Q_DRIVE] in seen or p[R.Q_DRIVE] < tg["pull_drive"]:
                continue
            seen.add(p[R.Q_DRIVE])
            pos_drives.append(q)
        k = next((j for j, q in enumerate(pos_drives) if q == tg["starter"]), len(pos_drives))
        gaps[min(max(k, 1), 4)] += 1
    tot = sum(gaps.values()) or 1
    return {"pulls": n, "came_back": back, "rate": round(back / n, 4) if n else 0.0,
            "drives_out": {str(k): round(v / tot, 4) for k, v in sorted(gaps.items())}}


def effects(ctrl, pulls, kind):
    gs = [g for g in pulls if g["cls"] == kind]
    out = {}
    lead, trail = (4, 5, 6), (0, 1, 2)
    for nm, sides in (("call_lead", lead), ("call_trail", trail), ("call_close", (3,))):
        d, cm, n = matched(ctrl, gs, "proe", sides)
        e = EV.effect(ctrl, [{**g, "cells": {c: v for c, v in g["cells"].items() if c[0] in sides}} for g in gs], "proe")
        xp = 0.55
        out[nm] = {"diff": round(d, 4), "se": e["se"], "z": e["z"], "snaps": int(n),
                   "logit_shift": round(d / (xp * (1 - xp)), 4)}
    for m in ("comp", "int"):
        d, cm, n = matched(ctrl, gs, m)
        e = EV.effect(ctrl, gs, m)
        out[m] = {"diff": round(d, 4), "se": e["se"], "z": e["z"], "snaps": int(n), "ctrl": round(cm, 4),
                  "logit_shift": round(logit(cm + d) - logit(cm), 4) if n else 0.0}
    d, cm, n = matched(ctrl, gs, "tempo")
    e = EV.effect(ctrl, gs, "tempo")
    out["tempo"] = {"diff": round(d, 3), "se": e["se"], "z": e["z"], "snaps": int(n), "ctrl": round(cm, 3),
                    "mult": round((cm + d) / cm, 4) if cm else 1.0}
    for m in ("rb1", "top2"):
        e = EV.effect(ctrl, gs, m)
        out[m] = {"diff": e["diff"], "se": e["se"], "z": e["z"], "snaps": e["snaps"],
                  "odds_ratio": round(mh_or(ctrl, gs, m), 4)}
    out["pulled_games"] = len(gs)
    return out


def keep(fitv, devv):
    """The rule: |z| >= 3 on the fit seasons and the same sign in development."""
    return fitv["z"] is not None and abs(fitv["z"]) >= Z_MIN and devv["diff"] * fitv["diff"] > 0


def main(pbp, players_csv, inj_dir):
    allg, pos, ids_of = QB.load_all(pbp, players_csv, tuple(range(2016, 2027)))
    extra = {s: X.load_extra(pbp, s) for s in allg}
    rep = reports(inj_dir, range(2016, 2026))
    cls = make_cls(rep)
    ctrl_f, pull_f = EV.collect({s: allg[s] for s in FIT}, ids_of, extra, pos, cls)
    ctrl_d, pull_d = EV.collect({s: allg[s] for s in DEV}, ids_of, extra, pos, cls)
    out = {"fit_seasons": list(FIT), "dev": list(DEV), "rule": f"|z| >= {Z_MIN} on fit and the same sign in dev",
           "credit": "nflverse-data play-by-play, players and injuries (CC BY 4.0)",
           "classes": {"fit": {k: sum(1 for g in pull_f if g["cls"] == k) for k in ("injury", "strategic")},
                       "dev": {k: sum(1 for g in pull_d if g["cls"] == k) for k in ("injury", "strategic")}}}
    tables = {"version": "coach-r4.0", "fit_seasons": list(FIT), "credit": out["credit"],
              "seal": "2026 week 13 on and every later season: never read by this fit",
              "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "effects": {}}
    for kind in ("strategic", "injury"):
        ef, ed = effects(ctrl_f, pull_f, kind), effects(ctrl_d, pull_d, kind)
        out[kind] = {"fit": ef, "dev": ed}
        t = {}
        for nm in ("call_lead", "call_trail", "call_close"):
            t[nm] = ef[nm]["logit_shift"] if keep(ef[nm], ed[nm]) else 0.0
        for m in ("comp", "int"):
            t[m] = ef[m]["logit_shift"] if keep(ef[m], ed[m]) else 0.0
        t["tempo"] = ef["tempo"]["mult"] if keep(ef["tempo"], ed["tempo"]) else 1.0
        for m in ("rb1", "top2"):
            t[m] = ef[m]["odds_ratio"] if keep(ef[m], ed[m]) else 1.0
        tables["effects"][kind] = t
    # the starters' own rates by cell (fit seasons): the registered statistics'
    # baseline for the lead back's share, the top two targets' share, completion
    tables["cells"] = {m: {",".join(map(str, c)): [round(a[0], 3), a[1]] for (c, mm), a in ctrl_f.items()
                           if mm == m and a[1] >= 20} for m in ("rb1", "top2", "comp")}
    tables["type_coef"] = fit_type(pull_f)
    tables["returns"] = returns(pull_f, ids_of)
    out["returns_dev"] = returns(pull_d, ids_of)
    out["tables"] = tables
    with open(TABLES, "w") as fh:
        json.dump(tables, fh, indent=1, sort_keys=True)
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    o = main(sys.argv[1], sys.argv[2], sys.argv[3])
    print(json.dumps({"classes": o["classes"], "effects": o["tables"]["effects"], "type": o["tables"]["type_coef"],
                      "returns": o["tables"]["returns"], "returns_dev": o["returns_dev"]}, indent=1))
