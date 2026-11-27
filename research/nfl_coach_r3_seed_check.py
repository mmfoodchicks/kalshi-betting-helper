"""The R3 random-stream discrepancy, checked (research/reports/nfl_coach_r3.md
section G, addendum). RESEARCH. `nfl_coach_sim_r3.simulate` seeds the R3 arm's
deal generator one game later than the R2 arm's, so the two arms share games but
not deal draws. This replays R3's development comparison (R3 - R2 per team-game
on 2026 weeks 1-12: the real starter's dropbacks CRPS and passing yards CRPS --
criteria 3 and 4 -- and, unregistered, his carries CRPS) with the arms as
registered and with the R3 arm's deal re-seeded to the R2 arm's (aligned), over
several seed sets, counting the draws whose registered endpoints differ:

  bias   the mean of (as registered - aligned) over team-games and seed sets,
         against its SE: a seeding offset should move nothing systematically;
  power  the SD of the paired difference each way, hence the SE at the
         registered window's ~190 team-games and the power of criterion 3
         (better by more than 2 SE) at the development effect sizes.

R3's engine, tables and registration are untouched: this file only calls them.

    python3 -m research.nfl_coach_r3_seed_check <pbp dir> <players.csv> [--sets 4] [--sims 200]
"""
import json
import math
import os
import random
import sys
from collections import defaultdict

import nfl_coach_sim as C
import nfl_coach_sim_r2 as E2
import nfl_coach_sim_r3 as E3
from research import nfl_coach_eval as CE
from research import nfl_coach_r2_data as R
from research import nfl_coach_r2_eval as EV2
from research import nfl_coach_r3_eval as EV3

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r3_seed_check.json")


def simulate_both(home, away, players, n, seed, T, U, Q, **kw):
    """nfl_coach_sim_r3.simulate line for line, plus a third arm: R3's deal on the
    R2 arm's seeds (aligned)."""
    rng = random.Random(seed)
    games, logs = [], []
    for _ in range(n):
        log = []
        res = C.play_game(home, away, rng, T, log=log, **kw)
        games.append(res)
        logs.append(E3.snaps(log, (kw.get("start") or {}).get("pts") or (0, 0)))
    mixes = E2.mix_of([[s[:6] for s in sn] for sn in logs])
    pls = [list(players[0]), list(players[1])]
    backups = [E3.backup_of(pls[0]), E3.backup_of(pls[1])]
    teams = [E2.TeamDeal(pls[s], U, mix=mixes[s], situational=True) for s in (0, 1)]
    out = {"r2": [], "r3": [], "r3a": []}
    prng = random.Random((seed or 0) * 6007 + 31337)
    for j, (res, sn) in enumerate(zip(games, logs)):
        pd = [E3.pull_drive([s for s in sn if s[0] == side], Q.coef, prng) for side in (0, 1)]
        for arm, off, use_pull in (("r2", 0, False), ("r3", 1, True), ("r3a", 0, True)):
            lines = []
            for side in (0, 1):
                s_sn = [s for s in sn if s[0] == side]
                drng = random.Random((int(seed or 0) * 1000003 + 2 * (j + off) + side) & 0x7FFFFFFF)
                lines.append(E3.deal(s_sn, res["h" if side == 0 else "a"], teams[side], drng,
                                     backups[side]["id"], pd[side] if use_pull else None, U))
            out[arm].append(tuple(lines))
    out["games"] = games
    return out


_W = {}


def _init(curves, sims, sets):
    _W.update(curves=curves, sims=sims, sets=sets, T={}, raw=json.load(open(C.TABLES_PATH)), U=E2.tables(),
              Q=E3.tables())


def job(pair):
    (hs, hq), (as_, aq) = pair
    g = hs["game"]
    imp = CE.implied({"spread": g["spread"], "total_line": g["total"]})
    if imp is None:
        return None
    era = CE.era_of(g["season"])
    if era not in _W["T"]:
        _W["T"][era] = C.Tables(_W["raw"], era=era)
    T = _W["T"][era]
    cv = _W["curves"][era]
    hp = CE._lvl_profile(T, CE.level_for(cv, imp[0]))
    ap = CE._lvl_profile(T, CE.level_for(cv, imp[1]))
    players = (EV3.roster(hs, hq, _W["U"]), EV3.roster(as_, aq, _W["U"]))
    base = int.from_bytes(g["id"].encode()[-8:], "little") % (2 ** 31)
    rows = []
    for k in range(_W["sets"]):
        seed = (base + 7919 * k) % (2 ** 31)
        r = simulate_both(hp, ap, players, _W["sims"], seed, T, _W["U"], _W["Q"], neutral=True)
        if k == 0:
            # the replication is R3's own function's output, line for line
            ref = E3.simulate(hp, ap, players, n=_W["sims"], seed=seed, T=T, U=_W["U"], Q=_W["Q"], neutral=True)
            assert ref["r2"] == r["r2"] and ref["r3"] == r["r3"], "replication of nfl_coach_sim_r3.simulate failed"
        for side, qtg in ((0, hq), (1, aq)):
            st = qtg["starter"]
            real, real_yd = EV3.real_starter_line(qtg)
            real_car = sum(1 for p in qtg["plays"] if p[R.Q_KIND] == R.K_RUN and p[R.Q_RUSH] == st)
            # the two registered endpoints read only the quarterback's dropbacks and
            # passing yards, which the deal assigns without a draw: count the draws
            # where the aligned arm's figures equal the registered arm's
            same = sum(1 for a, b in zip(r["r3"], r["r3a"])
                       if a[side][st]["pass_att"] + a[side][st]["sacks"] == b[side][st]["pass_att"] + b[side][st]["sacks"]
                       and a[side][st]["pass_yd"] == b[side][st]["pass_yd"])
            sc = {}
            for arm in ("r2", "r3", "r3a"):
                sc[arm] = (EV2.crps_counts([ln[side][st]["pass_att"] + ln[side][st]["sacks"] for ln in r[arm]], real),
                           EV2.crps_counts([ln[side][st]["pass_yd"] for ln in r[arm]], real_yd),
                           EV2.crps_counts([ln[side][st]["car"] for ln in r[arm]], real_car))
            rows.append((g["id"], side, k, [sc["r3"][m] - sc["r2"][m] for m in range(3)],
                         [sc["r3a"][m] - sc["r2"][m] for m in range(3)], same, len(r["r3"])))
    return rows


def main(argv):
    pbp, players_csv = argv[0], argv[1]
    sets, sims = 4, 200
    for k, a in enumerate(argv):
        if a == "--sets":
            sets = int(argv[k + 1])
        if a == "--sims":
            sims = int(argv[k + 1])
    pos = R.positions(players_csv)
    sd_ = R.load_season(pbp, 2026)
    prs = EV3.pairs(sd_, pos)
    cv = EV2.curves(C.TABLES_PATH, ["2025+"], 1000, cache=os.path.join(pbp, "nfl_coach_r2_curves_1000.json"))
    import multiprocessing as mp
    with mp.get_context("fork").Pool(4, initializer=_init, initargs=(cv, sims, sets)) as pool:
        res = pool.map(job, prs, chunksize=2)
    rows = [x for r in res if r for x in r]
    n_reg = 190
    names = ("dropbacks_registered_c3", "passing_yards_registered_c4", "starter_carries_unregistered")

    def ms(x):
        m = sum(x) / len(x)
        return m, math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))
    out = {"seasons": [2026], "weeks": "1-12", "seed_sets": sets, "sims": sims,
           "draws_compared": sum(r[6] for r in rows), "draws_endpoints_identical": sum(r[5] for r in rows),
           "endpoints": {}}
    for m, nm in enumerate(names):
        # per team-game, averaged over the seed sets (the unit the registered SE is over)
        by = defaultdict(lambda: [[], []])
        for gid, side, _k, dm, da, _s, _n in rows:
            by[(gid, side)][0].append(dm[m])
            by[(gid, side)][1].append(da[m])
        diff = [sum(v[0]) / len(v[0]) - sum(v[1]) / len(v[1]) for v in by.values()]
        mb, sdb = ms(diff)
        se_b = sdb / math.sqrt(len(diff))
        # the SD a single registered read sees: one seed set, per team-game
        mm, sdm = ms([r[3][m] for r in rows if r[2] == 0])
        ma, sda = ms([r[4][m] for r in rows if r[2] == 0])
        e = {"team_games": len(by),
             "bias": {"mean_as_registered_minus_aligned": round(mb, 6), "se": round(se_b, 6),
                      "z": round(mb / se_b, 2) if se_b > 0 else None},
             "one_read": {"as_registered": {"mean": round(mm, 4), "sd": round(sdm, 4)},
                          "aligned": {"mean": round(ma, 4), "sd": round(sda, 4)},
                          "sd_ratio": round(sdm / sda, 4) if sda > 0 else None}}
        if m == 0:
            pw = {}
            for eff in (0.181, 0.363):
                pw[str(eff)] = {}
                for k2, sd in (("as_registered", sdm), ("aligned", sda)):
                    se = sd / math.sqrt(n_reg)
                    pw[str(eff)][k2] = {"se": round(se, 4),
                                        "power": round(0.5 * math.erfc(-(eff / se - 2.0) / math.sqrt(2)), 4)}
            e["power_at_190_team_games"] = pw
        out["endpoints"][nm] = e
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), indent=1))
