"""Development measurements and the registered statistics for the coaching
engine's third revision (nfl_coach_sim_r3: the quarterback's substitution),
research/reports/nfl_coach_r3.md. RESEARCH: nflverse (CC BY 4.0) through
research/nfl_coach_r2_data.py, so R1's seal holds.

    python3 -m research.nfl_coach_r3_eval <pbp dir> <players.csv> [--seasons 2025,2026] [--sims 200]

  stage 1  each real team-game's pull sequence (research.nfl_coach_r3_qb), scored by
           the ENGINE's hazard (nfl_coach_sim_r3.hazard) against the constant
           hazard fitted on the same seasons: log loss summed a team-game.
  realism  each real game played by R1 at its closing line's level and dealt by
           R2 and by R3 on the same games and the same draws: the CRPS of the
           real starter's dropbacks and passing yards, a team-game at a time; and
           the starter's share of his team's dropbacks by final margin.
"""
import json
import math
import os
import sys
from collections import defaultdict

import nfl_coach_sim as C
import nfl_coach_sim_r2 as E2
import nfl_coach_sim_r3 as E3
from research import nfl_coach_eval as CE
from research import nfl_coach_r2_data as R
from research import nfl_coach_r2_eval as EV2
from research import nfl_coach_r3_qb as QB

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "dev_r3.json")
MARGINS = (("lost 17+", -999, -17), ("within 16", -16, 16), ("won 17+", 17, 999))


def stage1(qtgs, Q):
    """Per team-game (engine hazard log loss, constant hazard log loss)."""
    c0 = Q.raw["coef_constant"][0]
    p0 = 1.0 / (1.0 + math.exp(-c0))
    rows = []
    for tg in qtgs:
        l1 = l0 = 0.0
        for sd, gsr, y, _per in tg["seq"]:
            p = min(1 - 1e-9, max(1e-9, E3.hazard(Q.coef, sd, gsr)))
            l1 -= math.log(p if y else 1 - p)
            l0 -= math.log(p0 if y else 1 - p0)
        rows.append((l0, l1))
    return rows


def summarize1(rows):
    d = [a - b for a, b in rows]
    n = len(d)
    m = sum(d) / n
    se = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1) / n)
    return {"team_games": n, "gain_per_team_game": round(m, 5), "se": round(se, 5), "z": round(m / se, 2),
            "pulls": None}


def real_starter_line(qtg):
    """The game's starter's real dropbacks (attempts, spikes and sacks) and his
    passing yards (yards on his completions)."""
    st = qtg["starter"]
    drop = yd = 0
    for p in qtg["plays"]:
        if p[R.Q_PASSER] != st:
            continue
        if p[R.Q_KIND] in (R.K_PASS, R.K_SACK, R.K_SPIKE):
            drop += 1
            if p[R.Q_KIND] == R.K_PASS and p[R.Q_CMP]:
                yd += p[R.Q_YDS]
    return drop, yd


def roster(tg, qtg, U):
    """R2's player list for the team-game with the real starter first at
    quarterback and a backup (who came in, else a placeholder) beside him,
    the backup's carries 0 so he plays only after a pull."""
    pl = [p for p in EV2.players_of(tg, U) if E2.grp(p["pos"]) != "QB"]
    st = qtg["starter"]
    c, ry = tg["eff"].get(st, (0, 0, 0, 0, 0))[:2]
    sp = {"id": st, "pos": "QB", "car": float(tg["car"].get(st, 0)) * U.M0["car"]["QB"] or 1.0, "tgt": 0.0,
          "role_car": "QB", "ypc": (ry + 4.3 * 10) / (c + 10), "ypr": 0, "cr": 0}
    bk = dict(E3.BACKUP_PLACEHOLDER)
    if qtg["backup"] is not None and qtg["backup"] != st:
        bk["id"] = qtg["backup"]
    return [sp, bk] + pl


_RW = {}


def _rinit(curves, sims):
    _RW.update(curves=curves, sims=sims, T={}, raw=json.load(open(C.TABLES_PATH)), U=E2.tables(), Q=E3.tables())


def _T_of(era):
    if era not in _RW["T"]:
        _RW["T"][era] = C.Tables(_RW["raw"], era=era)
    return _RW["T"][era]


def job(pair):
    (hs, hq), (as_, aq) = pair
    g = hs["game"]
    imp = CE.implied({"spread": g["spread"], "total_line": g["total"]})
    if imp is None:
        return None
    era = CE.era_of(g["season"])
    T = _T_of(era)
    cv = _RW["curves"][era]
    hp = CE._lvl_profile(T, CE.level_for(cv, imp[0]))
    ap = CE._lvl_profile(T, CE.level_for(cv, imp[1]))
    U, Q = _RW["U"], _RW["Q"]
    players = (roster(hs, hq, U), roster(as_, aq, U))
    seed = int.from_bytes(g["id"].encode()[-8:], "little") % (2 ** 31)
    res = E3.simulate(hp, ap, players, n=_RW["sims"], seed=seed, T=T, U=U, Q=Q, neutral=True)
    out = []
    for side, (tg, qtg) in enumerate(((hs, hq), (as_, aq))):
        st = qtg["starter"]
        real_drop, real_yd = real_starter_line(qtg)
        sc = {}
        share = {}
        for arm in ("r2", "r3"):
            drops = [ln[side][st]["pass_att"] + ln[side][st]["sacks"] for ln in res[arm]]
            yds = [ln[side][st]["pass_yd"] for ln in res[arm]]
            sc[arm] = (EV2.crps_counts(drops, real_drop), EV2.crps_counts(yds, real_yd))
            acc = defaultdict(lambda: [0, 0])
            for r_, ln in zip(res["games"], res[arm]):
                m = (r_["h"]["pts"] - r_["a"]["pts"]) * (1 if side == 0 else -1)
                k = next(nm for nm, lo, hi in MARGINS if lo <= m <= hi)
                box = r_["h" if side == 0 else "a"]
                acc[k][0] += ln[side][st]["pass_att"] + ln[side][st]["sacks"]
                acc[k][1] += box["pass_att"] + box["sacks"]
            share[arm] = dict(acc)
        team_drop = sum(1 for p in qtg["plays"] if p[R.Q_KIND] in (R.K_PASS, R.K_SACK, R.K_SPIKE))
        pulled = sum(1 for x in res["pulled"] if x[side] is not None) / len(res["pulled"])
        out.append({"gid": g["id"], "team": tg["team"], "scores": sc, "share": share, "margin": qtg["margin"],
                    "real": (real_drop, team_drop), "real_pulled": qtg["pull"] is not None, "sim_pulled": pulled})
    return out


def pairs(sd_, pos):
    tgs = EV2.team_games(sd_, pos)
    qtgs = {(q["gid"], q["side"]): q for q in QB.team_games(sd_, pos)}
    by = defaultdict(dict)
    for tg in tgs:
        q = qtgs.get((tg["gid"], tg["side"]))
        if q is not None:
            by[tg["gid"]][tg["side"]] = (tg, q)
    return [(v[0], v[1]) for v in by.values() if 0 in v and 1 in v]


def run(prs, sims, curves, workers=4):
    import multiprocessing as mp
    if workers > 1:
        with mp.get_context("fork").Pool(workers, initializer=_rinit, initargs=(curves, sims)) as pool:
            res = pool.map(job, prs, chunksize=4)
    else:
        _rinit(curves, sims)
        res = [job(p) for p in prs]
    return [x for r in res if r for x in r]


def summarize_realism(rows):
    out = {"team_games": len(rows)}
    for j, nm in enumerate(("starter_dropbacks_crps", "starter_pass_yards_crps")):
        d = [r["scores"]["r3"][j] - r["scores"]["r2"][j] for r in rows]
        n = len(d)
        m = sum(d) / n
        se = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1) / n)
        out[nm] = {"r3_minus_r2_per_team_game": round(m, 4), "se": round(se, 4), "z": round(m / se, 2) if se else None,
                   "r2_mean": round(sum(r["scores"]["r2"][j] for r in rows) / n, 4)}
    tab = {}
    for nm, lo, hi in MARGINS:
        ent = {}
        for arm in ("r2", "r3"):
            a = [0, 0]
            for r in rows:
                v = r["share"][arm].get(nm)
                if v:
                    a[0] += v[0]
                    a[1] += v[1]
            ent[arm] = round(a[0] / a[1], 4) if a[1] else None
        a = [0, 0, 0]
        for r in rows:
            if lo <= r["margin"] <= hi:
                a[0] += r["real"][0]
                a[1] += r["real"][1]
                a[2] += 1
        ent["real"] = round(a[0] / a[1], 4) if a[1] else None
        ent["real_team_games"] = a[2]
        tab[nm] = ent
    out["starter_share_by_margin"] = tab
    out["pull_rate"] = {"real": round(sum(r["real_pulled"] for r in rows) / len(rows), 4),
                        "sim_r3": round(sum(r["sim_pulled"] for r in rows) / len(rows), 4)}
    return out


def main(argv):
    pbp, players_csv = argv[0], argv[1]
    seasons = (2025, 2026)
    sims = 200
    for k, a in enumerate(argv):
        if a == "--seasons":
            seasons = tuple(int(x) for x in argv[k + 1].split(","))
        if a == "--sims":
            sims = int(argv[k + 1])
    pos = R.positions(players_csv)
    Q = E3.tables()
    cv = EV2.curves(C.TABLES_PATH, sorted({CE.era_of(s) for s in seasons}), 1000,
                    cache=os.path.join(pbp, "nfl_coach_r2_curves_1000.json"))
    out = {"version": E3.VERSION, "sims": sims, "seasons": {}}
    for s in seasons:
        sd_ = R.load_season(pbp, s)
        prs = pairs(sd_, pos)
        qtgs = QB.team_games(sd_, pos)          # stage 1 needs no as-of shares: every team-game
        ent = {"stage1": summarize1(stage1(qtgs, Q))}
        ent["stage1"]["pulls"] = sum(q["pull"] is not None for q in qtgs)
        if sims:
            ent["realism"] = summarize_realism(run(prs, sims, cv))
        out["seasons"][str(s)] = ent
        print(s, json.dumps(ent["stage1"]), flush=True)
    with open(OUT_DEFAULT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    main(sys.argv[1:])
