"""Development measurements and the registered statistics for the coaching
engine's fourth revision (nfl_coach_sim_r4), research/reports/nfl_coach_r4.md.
RESEARCH; nflverse (CC BY 4.0), R1's seal held by the loaders.

    python3 -m research.nfl_coach_r4_eval <pbp dir> <players.csv> <injuries dir> [--sims 100]

  decision  on the REAL snaps after each real pull (its kind from the record, as
            the fit labelled it): the log score of (a) the lead back against the
            other backs on each back's carry and the top two targets against the
            rest on each target, the starters' own cell rate (the tables' `cells`)
            against that rate moved by R4's odds ratio; (b) each down 1-3 call,
            nflverse's xpass against xpass moved by R4's call shift; (c) each pass's
            completion, the starters' cell rate against it moved by R4's shift.
            Summed per pulled team-game; positive = R4 better.
  realism   each real game at its closing line, simulated by R3 and by R4 from the
            same seeds: the CRPS of the real lead back's carries, the real top two
            targets' targets and the starter's dropbacks, per team-game.
"""
import json
import math
import os
import sys
from collections import defaultdict

import nfl_coach_sim as C
import nfl_coach_sim_r2 as E2
import nfl_coach_sim_r3 as E3
import nfl_coach_sim_r4 as E4
from research import nfl_coach_eval as CE
from research import nfl_coach_r2_data as R
from research import nfl_coach_r2_eval as EV2
from research import nfl_coach_r3_eval as EV3
from research import nfl_coach_r3_qb as QB
from research import nfl_coach_r4_data as XD
from research import nfl_coach_r4_evidence as EV
from research import nfl_coach_r4_fit as F

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "dev_r4.json")


def _ll(p, y):
    p = min(1 - 1e-9, max(1e-9, p))
    return -math.log(p if y else 1 - p)


def _sig(x):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def _shift_odds(p, orr):
    o = p / (1 - p) * orr
    return o / (1 + o)


def decision(pulled, X):
    """Per pulled team-game: {"kind", "personnel": gain, "call": gain, "comp": gain, snaps...}."""
    rows = []
    cells = X.raw["cells"]
    for g in pulled:
        ef = X.effects[g["cls"]]
        acc = {"personnel": 0.0, "call": 0.0, "comp": 0.0, "n_personnel": 0, "n_call": 0, "n_comp": 0}
        for c, ms in g["cells"].items():
            key = ",".join(map(str, c))
            for m in ("rb1", "top2"):
                a = cells[m].get(key)
                if not a or not ms.get(m):
                    continue
                p0 = a[0] / a[1]
                p1 = _shift_odds(p0, ef[m])
                for y in ms[m]:
                    acc["personnel"] += _ll(p0, y) - _ll(p1, y)
                    acc["n_personnel"] += 1
            a = cells["comp"].get(key)
            if a and ms.get("comp"):
                p0 = a[0] / a[1]
                p1 = _sig(math.log(p0 / (1 - p0)) + ef["comp"])
                for y in ms["comp"]:
                    acc["comp"] += _ll(p0, y) - _ll(p1, y)
                    acc["n_comp"] += 1
            for y, xp in ms.get("call", []):
                sh = ef[E4._margin_key(_mid(c[0]))]
                p1 = _sig(math.log(xp / (1 - xp)) + sh)
                acc["call"] += _ll(xp, y) - _ll(p1, y)
                acc["n_call"] += 1
        acc["kind"] = g["cls"]
        rows.append(acc)
    return rows


def _mid(a):
    lo, hi = EV._MB[a]
    return max(-40, min(40, (lo + hi) / 2.0))


def collect_with_calls(allg, ids_of, extra, pos, cls_fn):
    """EV.collect, plus each backup snap's (dropback, xpass) on downs 1-3 for the
    call statistic."""
    ctrl, pulled = EV.collect(allg, ids_of, extra, pos, cls_fn)
    for g in pulled:
        tg = g["tg"]
        for p in tg["plays"]:
            if p[R.Q_DRIVE] < tg["pull_drive"] or p[R.Q_KIND] not in R.SCRIMMAGE:
                continue
            if 1 <= p[R.Q_DOWN] <= 3 and p[R.Q_XP] >= 0:
                xp = min(0.999, max(0.001, p[R.Q_XP] / 1000.0))
                g["cells"].setdefault(EV.cell(p), defaultdict(list)).setdefault("call", []).append(
                    (1 if p[R.Q_KIND] in (R.K_SCR, R.K_PASS, R.K_SACK) else 0, xp))
    return pulled


def summarize(rows):
    out = {"pulled_team_games": len(rows)}
    for r in rows:
        r["joint"] = r["personnel"] + r["call"] + r["comp"]
        r["n_joint"] = r["n_personnel"] + r["n_call"] + r["n_comp"]
    for k in ("joint", "personnel", "call", "comp"):
        d = [r[k] for r in rows]
        n = len(d)
        m = sum(d) / n
        se = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1) / n) if n > 1 else float("nan")
        out[k] = {"gain_per_pulled_team_game": round(m, 4), "se": round(se, 4),
                  "z": round(m / se, 2) if se and se == se else None, "snaps": sum(r["n_" + k] for r in rows)}
    return out


# ---- realism ------------------------------------------------------------------------------
_RW = {}


def _rinit(curves, sims):
    _RW.update(curves=curves, sims=sims, T={}, raw=json.load(open(C.TABLES_PATH)), U=E2.tables(), Q=E3.tables(),
               X=E4.tables())


def job(pair):
    (hs, hq), (as_, aq) = pair
    g = hs["game"]
    imp = CE.implied({"spread": g["spread"], "total_line": g["total"]})
    if imp is None:
        return None
    era = CE.era_of(g["season"])
    if era not in _RW["T"]:
        _RW["T"][era] = C.Tables(_RW["raw"], era=era)
    T = _RW["T"][era]
    cv = _RW["curves"][era]
    hp = CE._lvl_profile(T, CE.level_for(cv, imp[0]))
    ap = CE._lvl_profile(T, CE.level_for(cv, imp[1]))
    U = _RW["U"]
    players = (EV3.roster(hs, hq, U), EV3.roster(as_, aq, U))
    seed = int.from_bytes(g["id"].encode()[-8:], "little") % (2 ** 31)
    r3 = E3.simulate(hp, ap, players, n=_RW["sims"], seed=seed, T=T, U=U, Q=_RW["Q"], neutral=True)
    r4 = E4.simulate(hp, ap, players, n=_RW["sims"], seed=seed, T=T, U=U, Q=_RW["Q"], X=_RW["X"], neutral=True)
    out = []
    for side, (tg, qtg) in enumerate(((hs, hq), (as_, aq))):
        rc = E2.roles(tg["car"], tg["tgt"], tg["pos"], "car")
        rb1 = next((i for i, r in rc.items() if r == "RB1"), None)
        wrte = sorted((i for i in tg["tgt"] if tg["pos"].get(i) in ("WR", "TE")), key=lambda i: -tg["tgt"][i])[:2]
        st = qtg["starter"]
        real = tg["real"]
        sc = {}
        for arm, lines in (("r3", [ln[side] for ln in r3["r3"]]), ("r4", [ln[side] for ln in r4["lines"]])):
            v = []
            if rb1 is not None:
                v.append(EV2.crps_counts([ln[rb1]["car"] for ln in lines], real.get(rb1, (0, 0, 0))[0]))
            else:
                v.append(0.0)
            v.append(EV2.crps_counts([sum(ln[i]["tgt"] for i in wrte) for ln in lines],
                                     sum(real.get(i, (0, 0, 0))[1] for i in wrte)))
            rd, _ = EV3.real_starter_line(qtg)
            v.append(EV2.crps_counts([ln[st]["pass_att"] + ln[st]["sacks"] for ln in lines], rd))
            sc[arm] = v
        out.append({"gid": g["id"], "scores": sc})
    return out


def realism(prs, sims, curves, workers=4):
    import multiprocessing as mp
    with mp.get_context("fork").Pool(workers, initializer=_rinit, initargs=(curves, sims)) as pool:
        res = pool.map(job, prs, chunksize=4)
    rows = [x for r in res if r for x in r]
    out = {"team_games": len(rows)}
    for j, nm in enumerate(("rb1_carries_crps", "top2_targets_crps", "starter_dropbacks_crps")):
        d = [r["scores"]["r4"][j] - r["scores"]["r3"][j] for r in rows]
        n = len(d)
        m = sum(d) / n
        se = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1) / n)
        out[nm] = {"r4_minus_r3_per_team_game": round(m, 4), "se": round(se, 4), "z": round(m / se, 2) if se else None,
                   "r3_mean": round(sum(r["scores"]["r3"][j] for r in rows) / n, 4)}
    return out


def main(argv):
    pbp, players_csv, inj = argv[0], argv[1], argv[2]
    sims = 100
    for k, a in enumerate(argv):
        if a == "--sims":
            sims = int(argv[k + 1])
    X = E4.tables()
    allg, pos, ids_of = QB.load_all(pbp, players_csv, tuple(range(2016, 2027)))
    extra = {s: XD.load_extra(pbp, s) for s in (2025, 2026)}
    cls = F.make_cls(F.reports(inj, range(2016, 2027)))
    out = {"version": E4.VERSION, "sims": sims, "seasons": {}}
    cv = EV2.curves(C.TABLES_PATH, ["2025+"], 1000, cache=os.path.join(pbp, "nfl_coach_r2_curves_1000.json"))
    for s in (2025, 2026):
        pulled = collect_with_calls({s: allg[s]}, ids_of, extra, pos, cls)
        rows = decision(pulled, X)
        ent = {"all": summarize(rows),
               "strategic": summarize([r for r in rows if r["kind"] == "strategic"]),
               "injury": summarize([r for r in rows if r["kind"] == "injury"])}
        if sims:
            sd_ = R.load_season(pbp, s)
            ent["realism"] = realism(EV3.pairs(sd_, pos), sims, cv)
        out["seasons"][str(s)] = ent
        print(s, json.dumps(ent["all"]), flush=True)
    with open(OUT_DEFAULT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    main(sys.argv[1:])
