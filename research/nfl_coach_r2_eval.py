"""Development measurements and the registered statistics for the coaching
engine's second revision (nfl_coach_sim_r2, research/reports/nfl_coach_r2_evidence.md).
RESEARCH. nflverse play-by-play (CC BY 4.0) through research/nfl_coach_r2_data.py
(R1's seal kept: 2026 week 13 on is never handed over here).

    python3 -m research.nfl_coach_r2_eval <pbp dir> <players.csv> [--seasons 2024,2025,2026] [--sims 200]

Two families, both PAIRED (the same plays, the same simulated games):

  stage 1  who got the ball on each REAL snap: the engine's own deal
           probabilities (nfl_coach_sim_r2.TeamDeal, calibrated to the league's
           mix of situations) against the static share, on the same base
           shares -- the team's as-of (season-to-date, strictly earlier games)
           carries and targets of the players who touched the ball in the game,
           times the fitted role constant. Log loss summed a team-game.
  realism  each real game played by R1 at its closing line's level, every
           simulated game dealt by both arms: CRPS of the backs' and the
           quarterback's carries and the top receivers' targets, the log score
           of their touchdowns, a team-game at a time; and the lead back's share
           of carries by the final margin, simulated against real.
"""
import json
import math
import os
import sys
from collections import defaultdict

import nfl_coach_sim as C
import nfl_coach_sim_r2 as E2
from research import nfl_coach_eval as CE
from research import nfl_coach_r2_data as R

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "dev_r2.json")
MIN_PRIOR = 3                    # a team's games before one is scored (its as-of shares need them)
_YPC_K, _YPR_K, _CR_K = 10.0, 6.0, 8.0      # as-of efficiency shrunk toward the league (touches)
_LG = {"ypc": 4.3, "ypr": 10.5, "cr": 0.66}
MARGINS = ((-99, -17), (-16, -8), (-7, 7), (8, 16), (17, 99))


def team_games(season_data, pos):
    """Per team-game (regular season, the team's 4th game on): the players who
    touched the ball, their as-of counts, the game's snaps (bucket, kind, the
    player, touchdown) and each player's real line. Roles come from the as-of
    counts BEFORE any scaling."""
    ids = season_data["players"]
    bt = defaultdict(list)
    for g in season_data["games"]:
        if g["st"] != 2:
            continue
        for side in (0, 1):
            bt[g["home"] if side == 0 else g["away"]].append((g["week"], g, side))
    out = []
    for t, lst in bt.items():
        lst.sort(key=lambda x: x[0])
        a = defaultdict(lambda: [0, 0, 0, 0, 0])      # car, rush_yd, tgt, rec, rec_yd
        for ng, (wk, g, side) in enumerate(lst):
            h = 1 if side == 0 else 0
            pl = [p for p in g["plays"] if p[R.Q_OFFH] == h]
            snaps, active, real = [], set(), defaultdict(lambda: [0, 0, 0])   # car, tgt, td
            for p in pl:
                k = p[R.Q_KIND]
                b = E2.bucket(p[R.Q_PER], p[R.Q_HSR], p[R.Q_SD], p[R.Q_DOWN], p[R.Q_DIST], p[R.Q_YL])
                if k in (R.K_RUN, R.K_SCR) and p[R.Q_RUSH] >= 0:
                    i = p[R.Q_RUSH]
                    snaps.append(("car", b, i, p[R.Q_TD]))
                    active.add(i)
                    real[i][0] += 1
                    real[i][2] += p[R.Q_TD]
                elif k == R.K_PASS and p[R.Q_RECV] >= 0:
                    i = p[R.Q_RECV]
                    snaps.append(("tgt", b, i, p[R.Q_TD] if p[R.Q_CMP] else 0))
                    active.add(i)
                    real[i][1] += 1
                    real[i][2] += p[R.Q_TD] if p[R.Q_CMP] else 0
            if ng >= MIN_PRIOR:
                me = g["home_pts"] if side == 0 else g["away_pts"]
                op = g["away_pts"] if side == 0 else g["home_pts"]
                pos_g = {i: E2.grp(pos.get(ids[i], "")) for i in active}
                car = {i: a[i][0] for i in active if a[i][0] > 0}
                tgt = {i: a[i][2] for i in active if a[i][2] > 0}
                out.append({"season": season_data["season"], "week": wk, "team": t, "gid": g["id"], "side": side,
                            "game": g, "margin": me - op, "snaps": snaps, "pos": pos_g,
                            "car": car, "tgt": tgt,
                            "eff": {i: tuple(a[i]) for i in active},
                            "real": {i: tuple(v) for i, v in real.items()}})
            for p in pl:
                k = p[R.Q_KIND]
                if k in (R.K_RUN, R.K_SCR) and p[R.Q_RUSH] >= 0:
                    a[p[R.Q_RUSH]][0] += 1
                    a[p[R.Q_RUSH]][1] += p[R.Q_YDS]
                elif k == R.K_PASS and p[R.Q_RECV] >= 0:
                    a[p[R.Q_RECV]][2] += 1
                    if p[R.Q_CMP]:
                        a[p[R.Q_RECV]][3] += 1
                        a[p[R.Q_RECV]][4] += p[R.Q_YDS]
    return out


def players_of(tg, U):
    """The engine's player list for a team-game: as-of carries and targets
    times the fitted role constant (the as-of share of a back who is active
    understates his share that day; the role-only fit measures by how much),
    roles named from the raw as-of ranks, efficiency shrunk to the league."""
    rc = E2.roles(tg["car"], tg["tgt"], tg["pos"], "car")
    rt = E2.roles(tg["car"], tg["tgt"], tg["pos"], "tgt")
    out = []
    for i in set(tg["car"]) | set(tg["tgt"]):
        c, ry, t, rec, recy = tg["eff"][i]
        out.append({"id": i, "pos": tg["pos"][i],
                    "car": tg["car"].get(i, 0) * U.M0["car"].get(rc.get(i, "OT"), 1.0),
                    "tgt": tg["tgt"].get(i, 0) * U.M0["tgt"].get(rt.get(i, "OT"), 1.0),
                    "role_car": rc.get(i), "role_tgt": rt.get(i),
                    "ypc": (ry + _LG["ypc"] * _YPC_K) / (c + _YPC_K),
                    "ypr": (recy + _LG["ypr"] * _YPR_K) / (rec + _YPR_K),
                    "cr": (rec + _LG["cr"] * _CR_K) / (t + _CR_K)})
    # the passer: the quarterback with the most as-of carries+targets is no guide,
    # so a QB-less list gets a placeholder passer (the deal needs one)
    if not any(E2.grp(p["pos"]) == "QB" for p in out):
        out.insert(0, {"id": "QB*", "pos": "QB", "car": 0.0, "tgt": 0.0, "ypc": 4.0, "ypr": 0, "cr": 0})
    else:
        out.sort(key=lambda p: 0 if E2.grp(p["pos"]) == "QB" else 1)
    return out


# ---- stage 1 ----------------------------------------------------------------------------
def stage1(tgs, U, core=False):
    """Per team-game: (static log loss, engine log loss, snaps) for carries and
    targets, over the real snaps whose player has an as-of figure. `core`: the
    lead two backs and the quarterback for carries, the top three receivers,
    the top tight end and the lead back for targets (the players a DFS lineup
    holds), every other snap left out of both."""
    rows = []
    for tg in tgs:
        pl = players_of(tg, U)
        r2 = E2.TeamDeal(pl, U, situational=True)
        st = E2.TeamDeal(pl, U, situational=False)
        acc = {"car": [0.0, 0.0, 0], "tgt": [0.0, 0.0, 0], "td": [0.0, 0.0, 0]}
        keep = None
        if core:
            keep = {"car": {i for i, r in r2.d["car"][2].items() if r in ("RB1", "RB2", "QB")},
                    "tgt": {i for i, r in r2.d["tgt"][2].items() if r in ("WR1", "WR2", "WR3", "TE1", "RB1")}}
        for kind, b, i, td in tg["snaps"]:
            p1 = r2.p(kind, b)
            p0 = st.p(kind, b)
            if i not in p1:
                continue
            if keep is not None:
                if i not in keep[kind]:
                    continue
                z1 = sum(p1[j] for j in keep[kind])
                z0 = sum(p0[j] for j in keep[kind])
                q1, q0 = p1[i] / z1, p0[i] / z0
            else:
                q1, q0 = p1[i], p0[i]
            a = acc[kind]
            a[0] -= math.log(q0)
            a[1] -= math.log(q1)
            a[2] += 1
            if td:
                acc["td"][0] -= math.log(q0)
                acc["td"][1] -= math.log(q1)
                acc["td"][2] += 1
        rows.append({k: tuple(v) for k, v in acc.items()})
    return rows


def _mse(xs):
    n = len(xs)
    m = sum(xs) / n
    return m, (math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n) if n > 1 else float("nan"))


def summarize1(rows):
    out = {"team_games": len(rows)}
    for kind in ("car", "tgt", "td"):
        d = [r[kind][0] - r[kind][1] for r in rows if r[kind][2]]
        m, se = _mse(d)
        n = sum(r[kind][2] for r in rows)
        out[kind] = {"gain_per_team_game": round(m, 4), "se": round(se, 4), "z": round(m / se, 2),
                     "snaps": n, "ll_static": round(sum(r[kind][0] for r in rows) / n, 5),
                     "ll_r2": round(sum(r[kind][1] for r in rows) / n, 5)}
    d = [(r["car"][0] - r["car"][1]) + (r["tgt"][0] - r["tgt"][1]) for r in rows]
    m, se = _mse(d)
    out["both"] = {"gain_per_team_game": round(m, 4), "se": round(se, 4), "z": round(m / se, 2)}
    return out


# ---- realism ----------------------------------------------------------------------------
def crps_counts(draws, x):
    """CRPS of an integer outcome against an empirical sample (exact for the
    step distribution: E|X - x| - E|X - X'| / 2)."""
    n = len(draws)
    s = sorted(draws)
    e1 = sum(abs(d - x) for d in s) / n
    # E|X - X'| from the sorted sample in one pass
    acc, pre = 0.0, 0.0
    for k, d in enumerate(s):
        acc += d * k - pre
        pre += d
    e2 = 2.0 * acc / (n * n)
    return e1 - e2 / 2.0


def _ls_count(draws, x):
    n = len(draws)
    c = sum(1 for d in draws if d == x)
    return -math.log((c + 0.5) / (n + 1.0))


_RW = {}


def _rinit(curves, sims, tables_path):
    _RW.update(curves=curves, sims=sims, T={}, raw=json.load(open(tables_path)), U=E2.tables())


def _T_of(era):
    if era not in _RW["T"]:
        _RW["T"][era] = C.Tables(_RW["raw"], era=era)
    return _RW["T"][era]


def realism_job(pair):
    """Both sides of one real game: simulate, deal both arms, score."""
    hs, as_ = pair
    g = hs["game"]
    imp = CE.implied({"spread": g["spread"], "total_line": g["total"]})
    if imp is None:
        return None
    era = CE.era_of(g["season"])
    T = _T_of(era)
    cv = _RW["curves"][era]
    hp = CE._lvl_profile(T, CE.level_for(cv, imp[0]))
    ap = CE._lvl_profile(T, CE.level_for(cv, imp[1]))
    U = _RW["U"]
    players = (players_of(hs, U), players_of(as_, U))
    seed = int.from_bytes(g["id"].encode()[-8:], "little") % (2 ** 31)
    res = E2.simulate(hp, ap, players, n=_RW["sims"], seed=seed, T=T, U=U, neutral=True)
    out = []
    for side, tg in ((0, hs), (1, as_)):
        lines = {arm: [ln[side] for ln in res[arm]] for arm in ("r2", "static")}
        margins = [(r["h"]["pts"] - r["a"]["pts"]) * (1 if side == 0 else -1) for r in res["games"]]
        rc = {p["id"]: p.get("role_car") for p in players[side]}
        rt = {p["id"]: p.get("role_tgt") for p in players[side]}
        sc = {"r2": [0.0, 0.0, 0.0], "static": [0.0, 0.0, 0.0]}       # carries CRPS, targets CRPS, TD log score
        npl = [0, 0, 0]
        who_c = [i for i, r in rc.items() if r in ("RB1", "RB2", "QB")]
        who_t = [i for i, r in rt.items() if r in ("WR1", "WR2", "WR3", "TE1", "RB1")]
        for arm in ("r2", "static"):
            for i in who_c:
                d = [ln[i]["car"] for ln in lines[arm]]
                sc[arm][0] += crps_counts(d, tg["real"].get(i, (0, 0, 0))[0])
                tdd = [ln[i]["rush_td"] + ln[i]["rec_td"] for ln in lines[arm]]
                sc[arm][2] += _ls_count(tdd, tg["real"].get(i, (0, 0, 0))[2])
            for i in who_t:
                d = [ln[i]["tgt"] for ln in lines[arm]]
                sc[arm][1] += crps_counts(d, tg["real"].get(i, (0, 0, 0))[1])
        npl[0], npl[1] = len(who_c), len(who_t)
        # the lead back's share of the team's carries by final margin
        rb1 = [i for i, r in rc.items() if r == "RB1"]
        mshare = {}
        if rb1:
            i = rb1[0]
            for arm in ("r2", "static"):
                acc = defaultdict(lambda: [0, 0])
                for ln, m in zip(lines[arm], margins):
                    tot = sum(v["car"] for v in ln.values())
                    for k, (lo, hi) in enumerate(MARGINS):
                        if lo <= m <= hi:
                            acc[k][0] += ln[i]["car"]
                            acc[k][1] += tot
                mshare[arm] = {k: v for k, v in acc.items()}
            real_tot = sum(v[0] for v in tg["real"].values())
            mshare["real"] = (tg["margin"], tg["real"].get(i, (0, 0, 0))[0], real_tot)
        out.append({"gid": g["id"], "team": tg["team"], "scores": sc, "n": npl, "mshare": mshare})
    return out


def pair_sides(tgs):
    by = defaultdict(dict)
    for tg in tgs:
        by[tg["gid"]][tg["side"]] = tg
    return [(v[0], v[1]) for v in by.values() if 0 in v and 1 in v]


def summarize_realism(rows):
    out = {"team_games": len(rows)}
    for j, name in enumerate(("carries_crps", "targets_crps", "td_logscore")):
        d = [r["scores"]["r2"][j] - r["scores"]["static"][j] for r in rows]
        m, se = _mse(d)
        out[name] = {"r2_minus_static_per_team_game": round(m, 4), "se": round(se, 4),
                     "z": round(m / se, 2) if se == se and se > 0 else None,
                     "static_mean": round(sum(r["scores"]["static"][j] for r in rows) / len(rows), 4)}
    # lead back's share by margin
    tab = {}
    for k, (lo, hi) in enumerate(MARGINS):
        key = f"{lo}..{hi}"
        ent = {}
        for arm in ("r2", "static"):
            a = [0, 0]
            for r in rows:
                v = r["mshare"].get(arm, {}).get(k)
                if v:
                    a[0] += v[0]
                    a[1] += v[1]
            ent[arm] = round(a[0] / a[1], 4) if a[1] else None
        a = [0, 0, 0]
        for r in rows:
            rv = r["mshare"].get("real")
            if rv and lo <= rv[0] <= hi:
                a[0] += rv[1]
                a[1] += rv[2]
                a[2] += 1
        ent["real"] = round(a[0] / a[1], 4) if a[1] else None
        ent["real_team_games"] = a[2]
        tab[key] = ent
    out["rb1_share_by_margin"] = tab
    return out


def curves(tables_path, eras, n, cache=None):
    if cache and os.path.exists(cache):
        with open(cache) as fh:
            c = json.load(fh)
        if all(e in c for e in eras):
            return c
    raw = json.load(open(tables_path))
    c = {e: CE.level_curve("coach", C.Tables(raw, era=e), n=n) for e in eras}
    if cache:
        with open(cache, "w") as fh:
            json.dump(c, fh)
    return c


def run_realism(tgs, sims, curves_, workers=4):
    pairs = pair_sides(tgs)
    if workers > 1:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(workers, initializer=_rinit,
                                          initargs=(curves_, sims, C.TABLES_PATH)) as pool:
            res = pool.map(realism_job, pairs, chunksize=4)
    else:
        _rinit(curves_, sims, C.TABLES_PATH)
        res = [realism_job(p) for p in pairs]
    return [x for r in res if r for x in r]


def main(argv):
    pbp, players_csv = argv[0], argv[1]
    seasons = (2024, 2025, 2026)
    sims = 200
    curve_n = 1000
    for k, a in enumerate(argv):
        if a == "--seasons":
            seasons = tuple(int(x) for x in argv[k + 1].split(","))
        if a == "--sims":
            sims = int(argv[k + 1])
    pos = R.positions(players_csv)
    U = E2.tables()
    out = {"version": E2.VERSION, "sims": sims, "level_curve_games": curve_n, "seasons": {}}
    eras = sorted({CE.era_of(s) for s in seasons})
    cv = curves(C.TABLES_PATH, eras, curve_n,
                cache=os.path.join(pbp, f"nfl_coach_r2_curves_{curve_n}.json"))
    for s in seasons:
        tgs = team_games(R.load_season(pbp, s), pos)
        ent = {"stage1": summarize1(stage1(tgs, U)), "stage1_core": summarize1(stage1(tgs, U, core=True))}
        if sims:
            ent["realism"] = summarize_realism(run_realism(tgs, sims, cv))
        out["seasons"][str(s)] = ent
        print(s, json.dumps(ent["stage1"]["both"]), json.dumps(ent["stage1_core"]["both"]), flush=True)
    path = OUT_DEFAULT
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    main(sys.argv[1:])
