"""The evidence for the coaching engine's fourth revision: what a quarterback
substitution changes (research/reports/nfl_coach_r4.md). RESEARCH: nflverse
play-by-play, players and injury reports (CC BY 4.0) through R2's loader and
research/nfl_coach_r4_data.py, so R1's seal holds (2026 week 13 on never read).

    python3 -m research.nfl_coach_r4_evidence <pbp dir> <players.csv> [<injuries dir>]

Every pull (research.nfl_coach_r3_qb) is classed by what the plays themselves say:
  injury     the game's description names the starter "was injured" on a play
             before the backup's first drive;
  strategic  no such mention, and the margin at the pull is decided
             (|margin| / sqrt(time) / 10 >= 3, R3's descriptive split);
  other      neither (benchings, unrecorded injuries, late-season rest).
After a pull, each of the backup's snaps is compared with the STARTERS' snaps
in the same state (margin x time x down cell, every team-game of the seasons),
weighted by the backup's counts, so the score and the clock are held: pass rate
over expected, air yards, completion, sack and interception rates, EPA and
success, the seconds to the next snap after an in-bounds run, the lead back's
share of the backs' carries and the top two targets' share of the targets (as-of
roles: season-to-date leaders, earlier games only). The SE is a bootstrap over
the pulled team-games.
"""
import csv
import json
import math
import os
import random
import sys
from collections import defaultdict

from research import nfl_coach_r2_data as R
from research import nfl_coach_r3_qb as QB
from research import nfl_coach_r4_data as X

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r4_evidence.json")
SEASONS = tuple(range(2016, 2027))
_MB = ((-99, -28), (-27, -17), (-16, -9), (-8, 8), (9, 16), (17, 27), (28, 99))
_TB = ((1801, 3600), (901, 1800), (481, 900), (121, 480), (0, 120))
METRICS = ("proe", "air", "comp", "sack", "int", "epa", "succ", "tempo", "rb1", "top2")


def cell(p):
    sd, g = p[R.Q_SD], p[R.Q_GSR]
    a = next(k for k, (lo, hi) in enumerate(_MB) if lo <= sd <= hi)
    b = next(k for k, (lo, hi) in enumerate(_TB) if lo <= g <= hi)
    return (a, b, min(max(p[R.Q_DOWN], 1), 3))


def decided(sd, gsr):
    return abs(sd) / math.sqrt((gsr + 30) / 3600.0) / 10.0 >= 3.0


def classify(tg, ids, ex):
    """injury / strategic / other for a pulled team-game; None if no pull."""
    if tg["pull"] is None:
        return None
    pl = tg["plays"]
    first = min((p[R.Q_PLAYID] for p in pl if p[R.Q_DRIVE] == tg["pull_drive"]), default=10 ** 9)
    name = ex["names"].get(ids[tg["starter"]])
    hurt = any(pid <= first and nm == name for pid, nm in ex["inj"].get(tg["gid"], [])) if name else False
    if hurt:
        return "injury"
    sd, gsr, _y, _per = tg["seq"][-1]
    return "strategic" if decided(sd, gsr) else "other"


def play_values(p, ex, gid, rb1, top2, pos_of):
    """{metric: value} for one snap (only the metrics it carries)."""
    v = {}
    k = p[R.Q_KIND]
    if k in R.SCRIMMAGE:
        v["epa"] = p[R.Q_EPA] / 1000.0
        v["succ"] = p[R.Q_SUCC]
        if 1 <= p[R.Q_DOWN] <= 3 and p[R.Q_XP] >= 0:
            v["proe"] = (k in (R.K_SCR, R.K_PASS, R.K_SACK)) - p[R.Q_XP] / 1000.0
    if k in (R.K_PASS, R.K_SACK, R.K_SCR):
        v["sack"] = 1 if k == R.K_SACK else 0
    if k == R.K_PASS:
        v["comp"] = p[R.Q_CMP]
        v["int"] = p[R.Q_INT]
        a = ex["air"].get((gid, p[R.Q_PLAYID]))
        if a is not None:
            v["air"] = a
        if p[R.Q_RECV] >= 0 and top2:
            v["top2"] = 1 if p[R.Q_RECV] in top2 else 0
    if k == R.K_RUN and p[R.Q_RUSH] >= 0 and pos_of(p[R.Q_RUSH]) in ("RB", "FB") and rb1 is not None:
        v["rb1"] = 1 if p[R.Q_RUSH] == rb1 else 0
    return v


def collect(allg, ids_of, extra, pos, cls_fn=None):
    """Per (cell, metric): the starters' sums; per pulled team-game: the backup's
    values by cell. Also the as-of roles (earlier games only)."""
    ctrl = defaultdict(lambda: [0.0, 0])
    pulled = []
    for s, tgs in allg.items():
        ids, ex = ids_of[s], extra[s]

        def pos_of(i):
            return pos.get(ids[i], "")
        by_team = defaultdict(list)
        for tg in tgs:
            by_team[tg["team"]].append(tg)
        for t, lst in by_team.items():
            lst.sort(key=lambda x: x["week"])
            car, tgt = defaultdict(int), defaultdict(int)
            for tg in lst:
                rb1 = max((i for i in car if pos_of(i) in ("RB", "FB")), key=lambda i: car[i], default=None)
                top2 = set(sorted((i for i in tgt if pos_of(i) in ("WR", "TE")), key=lambda i: -tgt[i])[:2])
                cut = tg["pull_drive"]
                cls = (cls_fn or classify)(tg, ids, ex)
                mine = defaultdict(lambda: defaultdict(list))
                prev = None
                for p in tg["plays"]:
                    backup = cut is not None and p[R.Q_DRIVE] >= cut
                    vals = play_values(p, ex, tg["gid"], rb1, top2, pos_of)
                    # tempo: from an in-bounds run (in the same drive) to this snap
                    if (prev is not None and prev[R.Q_KIND] == R.K_RUN and p[R.Q_KIND] in R.SCRIMMAGE
                            and prev[R.Q_DRIVE] == p[R.Q_DRIVE] and not prev[R.Q_TD] and not prev[R.Q_FUM]):
                        gap = prev[R.Q_GSR] - p[R.Q_GSR]
                        if 0 < gap <= 60:
                            vals["tempo"] = gap
                    if p[R.Q_KIND] in R.SCRIMMAGE:
                        prev = p
                    c = cell(p)
                    for m, x in vals.items():
                        if backup:
                            mine[c][m].append(x)
                        else:
                            a = ctrl[(c, m)]
                            a[0] += x
                            a[1] += 1
                if cls is not None:
                    pulled.append({"season": s, "cls": cls, "back": tg["back"], "cells": mine,
                                   "career": tg["backup_career"] or 0, "gid": tg["gid"], "tg": tg})
                for p in tg["plays"]:
                    if p[R.Q_KIND] in (R.K_RUN, R.K_SCR) and p[R.Q_RUSH] >= 0:
                        car[p[R.Q_RUSH]] += 1
                    elif p[R.Q_KIND] == R.K_PASS and p[R.Q_RECV] >= 0:
                        tgt[p[R.Q_RECV]] += 1
    return ctrl, pulled


def effect(ctrl, games, metric, reps=300, seed=1):
    """The backup-minus-starter difference in `metric`, cell-matched, weighted by
    the backup's counts; bootstrap SE over the team-games."""
    def est(gs):
        num = den = 0.0
        for g in gs:
            for c, ms in g["cells"].items():
                xs = ms.get(metric)
                if not xs:
                    continue
                a = ctrl.get((c, metric))
                if not a or a[1] < 20:
                    continue
                num += sum(xs) - len(xs) * a[0] / a[1]
                den += len(xs)
        return (num / den if den else float("nan")), den
    m, n = est(games)
    rng = random.Random(seed)
    bs = []
    for _ in range(reps):
        smp = [games[rng.randrange(len(games))] for _ in games]
        v, _ = est(smp)
        if v == v:
            bs.append(v)
    mu = sum(bs) / len(bs)
    se = math.sqrt(sum((x - mu) ** 2 for x in bs) / (len(bs) - 1))
    return {"diff": round(m, 4), "se": round(se, 4), "z": round(m / se, 2) if se else None, "snaps": int(n),
            "games": len(games)}


def injury_report_check(inj_dir, allg, ids_of, extra):
    """Corroboration from nflverse's weekly injury reports (2024-2025): is a
    pulled starter on the NEXT week's report, by class?"""
    rep = set()
    for s in (2024, 2025):
        fp = os.path.join(inj_dir or "", f"injuries_{s}.csv")
        if not os.path.exists(fp):
            return None
        with open(fp, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("game_type") == "REG" and r.get("report_status") in ("Out", "Doubtful", "Questionable"):
                    rep.add((int(r["season"]), int(r["week"]), r["gsis_id"]))
    acc = defaultdict(lambda: [0, 0])
    for s in (2024, 2025):
        for tg in allg[s]:
            cls = classify(tg, ids_of[s], extra[s])
            if cls is None or tg["week"] >= 18:
                continue
            a = acc[cls]
            a[0] += (s, tg["week"] + 1, ids_of[s][tg["starter"]]) in rep
            a[1] += 1
    return {k: {"pulls": v[1], "on_next_report": v[0], "rate": round(v[0] / v[1], 3)} for k, v in acc.items()}


def r3_miss(allg, ids_of, extra, coef):
    """R3's known miss: trailing by 17-27 with 2-8 minutes left. Pulls there by
    class and by week, against the drives' share by week."""
    import nfl_coach_sim_r3 as E3
    out = {"pulls": defaultdict(int), "drives_late_season": 0, "drives": 0, "pulls_late_season": 0,
           "expected": 0.0, "expected_late_season": 0.0}
    for s in (2024, 2025, 2026):
        for tg in allg[s]:
            for j, (sd, gsr, y, _per) in enumerate(tg["seq"]):
                if -27 <= sd <= -17 and 121 <= gsr <= 480:
                    late = tg["week"] >= 15
                    h = E3.hazard(coef, sd, gsr)
                    out["drives"] += 1
                    out["drives_late_season"] += late
                    out["expected"] += h
                    out["expected_late_season"] += h * late
                    if y:
                        out["pulls"][classify(tg, ids_of[s], extra[s])] += 1
                        out["pulls_late_season"] += late
    out["pulls"] = dict(out["pulls"])
    out["expected"] = round(out["expected"], 2)
    out["expected_late_season"] = round(out["expected_late_season"], 2)
    return out


def main(pbp, players_csv, inj_dir=None):
    import nfl_coach_sim_r3 as E3
    allg, pos, ids_of = QB.load_all(pbp, players_csv, SEASONS)
    extra = {s: X.load_extra(pbp, s) for s in SEASONS}
    ctrl, pulled = collect(allg, ids_of, extra, pos)
    ev = {"seasons": list(SEASONS), "credit": "nflverse-data play-by-play, players and injuries (CC BY 4.0)"}
    cls_n = defaultdict(lambda: [0, 0])
    for g in pulled:
        cls_n[g["cls"]][0] += 1
        cls_n[g["cls"]][1] += g["back"]
    ev["classes"] = {k: {"pulls": v[0], "starter_came_back": v[1], "rate": round(v[1] / v[0], 3)}
                     for k, v in cls_n.items()}
    ev["injury_report_check"] = injury_report_check(inj_dir, allg, ids_of, extra)
    ev["effects"] = {}
    groups = {"strategic": [g for g in pulled if g["cls"] == "strategic"],
              "injury": [g for g in pulled if g["cls"] == "injury"],
              "other": [g for g in pulled if g["cls"] == "other"],
              "strategic_new_backup": [g for g in pulled if g["cls"] == "strategic" and g["career"] < 300],
              "strategic_experienced_backup": [g for g in pulled if g["cls"] == "strategic" and g["career"] >= 300]}
    for nm, gs in groups.items():
        ev["effects"][nm] = {m: effect(ctrl, gs, m) for m in METRICS}
    # leading and trailing apart for the call (the backup's strategic snaps)
    for side, keep in (("lead", (4, 5, 6)), ("trail", (0, 1, 2))):
        gs = [{**g, "cells": {c: v for c, v in g["cells"].items() if c[0] in keep}} for g in groups["strategic"]]
        ev["effects"][f"strategic_{side}"] = {m: effect(ctrl, gs, m) for m in ("proe", "air", "comp", "epa", "tempo")}
    ev["r3_miss"] = r3_miss(allg, ids_of, extra, E3.tables().coef)
    with open(OUT, "w") as fh:
        json.dump(ev, fh, indent=1, sort_keys=True)
    return ev


if __name__ == "__main__":
    e = main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    print(json.dumps({k: e[k] for k in ("classes", "injury_report_check", "r3_miss")}, indent=1))
