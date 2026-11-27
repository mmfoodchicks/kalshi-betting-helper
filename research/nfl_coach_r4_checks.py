"""Two checks on the coaching engine's fourth revision, asked by the owner after he
read it (research/reports/nfl_coach_r4.md section H). REPORT ONLY: R4's
registration, tables and engine are not changed by anything here.

    python3 -m research.nfl_coach_r4_checks <pbp dir> <players.csv> <injuries dir>

1. LABEL SENSITIVITY. R4 calls a pull an injury when a play description names
   the starter injured OR he is on the next week's report; else strategic. Two
   groups are ambiguous:
     A1  not named, but on the next report (labelled injury) -- a listing that
         may predate the game;
     A2  not named, not reported, and the game not decided (labelled strategic)
         -- possibly an unrecorded injury or a benching.
   The shifts are refitted with each group flipped (and both), the same rule
   applied, and the development gain recomputed with each variant's own labels.
2. THE SECOND UNIT, real against simulated. After a real strategic pull, does
   the lead back sit out the rest of the game, or keep a smaller share? And the
   top two targets? Against R4's simulated strategic pulls (per-snap odds).

Writes research/data/nfl_coach/r4_checks.json.
"""
import json
import os
import random
import sys
from collections import defaultdict

from research import nfl_coach_r2_data as R
from research import nfl_coach_r3_qb as QB
from research import nfl_coach_r4_data as XD
from research import nfl_coach_r4_eval as EV4
from research import nfl_coach_r4_evidence as EV
from research import nfl_coach_r4_fit as F

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r4_checks.json")


def labeler(rep, flip_a1=False, flip_a2=False):
    def cls(tg, ids, ex):
        if tg["pull"] is None:
            return None
        named = EV.classify(tg, ids, ex) == "injury"
        reported = (tg["season"], tg["week"] + 1, ids[tg["starter"]]) in rep
        sd, gsr, _y, _p = tg["seq"][-1]
        decided = EV.decided(sd, gsr)
        if named:
            return "injury"
        if reported:
            return "strategic" if flip_a1 else "injury"
        if flip_a2 and not decided:
            return "injury"
        return "strategic"
    return cls


class _X:
    def __init__(self, effects, cells):
        self.effects = effects
        self.raw = {"cells": cells}


def variant(allg, ids_of, extra, pos, cls, cells):
    ctrl_f, pull_f = EV.collect({s: allg[s] for s in F.FIT}, ids_of, extra, pos, cls)
    ctrl_d, pull_d = EV.collect({s: allg[s] for s in F.DEV}, ids_of, extra, pos, cls)
    eff = {}
    for kind in ("strategic", "injury"):
        ef, ed = F.effects(ctrl_f, pull_f, kind), F.effects(ctrl_d, pull_d, kind)
        t = {}
        for nm in ("call_lead", "call_trail", "call_close", "comp", "int"):
            t[nm] = ef[nm]["logit_shift"] if F.keep(ef[nm], ed[nm]) else 0.0
        t["tempo"] = ef["tempo"]["mult"] if F.keep(ef["tempo"], ed["tempo"]) else 1.0
        for m in ("rb1", "top2"):
            t[m] = ef[m]["odds_ratio"] if F.keep(ef[m], ed[m]) else 1.0
        eff[kind] = t
    X = _X(eff, cells)
    gains = {}
    for s in F.DEV:
        pulled = EV4.collect_with_calls({s: allg[s]}, ids_of, extra, pos, cls)
        gains[str(s)] = EV4.summarize(EV4.decision(pulled, X))["joint"]
    n = {"fit": {k: sum(1 for g in pull_f if g["cls"] == k) for k in ("injury", "strategic")},
         "dev": {k: sum(1 for g in pull_d if g["cls"] == k) for k in ("injury", "strategic")}}
    return {"effects": eff, "dev_joint_gain": gains, "counts": n}


def second_unit_real(allg, ids_of, pos, cls, extra, seasons):
    """After each real strategic pull: did the game's pre-pull lead back carry
    again, and did either of the season's top two targets get a target?"""
    out = {"games": 0, "lead_back_sat": 0, "lead_share_after": [], "top2_none": 0, "top2_games": 0}
    for s in seasons:
        ids = ids_of[s]
        for tg in allg[s]:
            if cls(tg, ids, extra[s]) != "strategic":
                continue
            pre = defaultdict(int)
            after_rb, after_lead = 0, None
            cut = tg["pull_drive"]
            for p in tg["plays"]:
                if p[R.Q_KIND] == R.K_RUN and p[R.Q_RUSH] >= 0 and pos.get(ids[p[R.Q_RUSH]], "") in ("RB", "FB"):
                    if p[R.Q_DRIVE] < cut:
                        pre[p[R.Q_RUSH]] += 1
            if sum(pre.values()) < 3:
                continue
            lead = max(pre, key=pre.get)
            after_lead = 0
            tgt_after = defaultdict(int)
            for p in tg["plays"]:
                if p[R.Q_DRIVE] < cut:
                    continue
                if p[R.Q_KIND] == R.K_RUN and p[R.Q_RUSH] >= 0 and pos.get(ids[p[R.Q_RUSH]], "") in ("RB", "FB"):
                    after_rb += 1
                    after_lead += p[R.Q_RUSH] == lead
                elif p[R.Q_KIND] == R.K_PASS and p[R.Q_RECV] >= 0:
                    tgt_after[p[R.Q_RECV]] += 1
            if after_rb >= 3:
                out["games"] += 1
                out["lead_back_sat"] += after_lead == 0
                out["lead_share_after"].append(after_lead / after_rb)
            # the game's two most-targeted wide receivers / tight ends before the pull
            pre_t = defaultdict(int)
            for p in tg["plays"]:
                if (p[R.Q_DRIVE] < cut and p[R.Q_KIND] == R.K_PASS and p[R.Q_RECV] >= 0
                        and pos.get(ids[p[R.Q_RECV]], "") in ("WR", "TE")):
                    pre_t[p[R.Q_RECV]] += 1
            top2 = sorted(pre_t, key=lambda i: -pre_t[i])[:2]
            if len(top2) == 2 and sum(tgt_after.values()) >= 3:
                out["top2_games"] += 1
                out["top2_none"] += all(tgt_after.get(i, 0) == 0 for i in top2)
    return _shape(out)


def _shape(o):
    sh = sorted(o.pop("lead_share_after"))
    n = len(sh)
    o["lead_back_sat_rate"] = round(o["lead_back_sat"] / max(o["games"], 1), 4)
    o["lead_share_after_quartiles"] = [round(sh[int(q * (n - 1))], 3) for q in (0.25, 0.5, 0.75)] if n else None
    o["lead_share_after_zero_or_one"] = round(sum(1 for x in sh if x in (0.0, 1.0)) / max(n, 1), 4)
    o["top2_none_rate"] = round(o["top2_none"] / max(o["top2_games"], 1), 4)
    return o


def second_unit_sim(n=4000, seed=29):
    """R4's simulated strategic pulls on a league roster: the same two figures
    (the lead back here is the roster's RB1; the top two its WR1 and WR2)."""
    import nfl_coach_sim as C
    import nfl_coach_sim_r4 as E4
    lg = C.league_profile()
    ros = [dict(id="QB", pos="QB", car=3.0, tgt=0, ypc=5.0), dict(id="QB2", pos="QB", car=0.0, tgt=0, ypc=3.5),
           dict(id="RB1", pos="RB", car=15.0, tgt=3.5, ypc=4.3, ypr=7.5, cr=0.78),
           dict(id="RB2", pos="RB", car=6.0, tgt=2.0, ypc=4.1, ypr=7.0, cr=0.76),
           dict(id="WR1", pos="WR", car=0.4, tgt=8.5, ypc=7.0, ypr=13.0, cr=0.64),
           dict(id="WR2", pos="WR", car=0.0, tgt=6.0, ypr=12.5, cr=0.62),
           dict(id="WR3", pos="WR", car=0.0, tgt=4.0, ypr=12.0, cr=0.6),
           dict(id="TE1", pos="TE", car=0.0, tgt=5.0, ypr=10.0, cr=0.7)]
    out = {"games": 0, "lead_back_sat": 0, "lead_share_after": [], "top2_none": 0, "top2_games": 0}
    import nfl_coach_sim_r2 as E2
    import nfl_coach_sim_r3 as E3
    X = E4.tables()
    U = E2.tables()
    rng, prng = random.Random(seed), random.Random(seed + 1)
    team = None
    for j in range(n):
        r = E4.play_game(lg, lg, rng, prng, X=X)
        q = r["qb"][0]
        if q["pull"] is None or q["kind"] != "strategic":
            continue
        if team is None:
            team = E2.TeamDeal(ros, U)
        sn = [s for s in E3.snaps(r["log"]) if s[0] == 0]
        # deal snap by snap, recording who carried after the pull
        after = [s for s in sn if s[6] >= q["pull"]]
        drng = random.Random(j)
        rb_after = lead_after = 0
        tg = defaultdict(int)
        ef = X.effects["strategic"]
        for o, k, b, *_ in after:
            if k == "run":
                p = E4._personnel(team.p("car", b), team, ef, "car")
                i = E2._draw(drng, p)
                if i in ("RB1", "RB2"):
                    rb_after += 1
                    lead_after += i == "RB1"
            elif k in ("cmp", "inc"):
                p = E4._personnel(E2._reweight(team.p("tgt", b), team, k == "cmp"), team, ef, "tgt")
                tg[E2._draw(drng, p)] += 1
        if rb_after >= 3:
            out["games"] += 1
            out["lead_back_sat"] += lead_after == 0
            out["lead_share_after"].append(lead_after / rb_after)
        if sum(tg.values()) >= 3:
            out["top2_games"] += 1
            out["top2_none"] += tg.get("WR1", 0) == 0 and tg.get("WR2", 0) == 0
    return _shape(out)


def main(pbp, players_csv, inj):
    allg, pos, ids_of = QB.load_all(pbp, players_csv, tuple(range(2016, 2027)))
    extra = {s: XD.load_extra(pbp, s) for s in allg}
    rep = F.reports(inj, range(2016, 2026))
    cells = json.load(open(F.TABLES))["cells"]
    out = {"sensitivity": {}}
    for nm, a1, a2 in (("as_registered", False, False), ("A1_as_strategic", True, False),
                       ("A2_as_injury", False, True), ("both_flipped", True, True)):
        out["sensitivity"][nm] = variant(allg, ids_of, extra, pos, labeler(rep, a1, a2), cells)
    base = labeler(rep)
    out["second_unit_real_2016_2024"] = second_unit_real(allg, ids_of, pos, base, extra, F.FIT)
    out["second_unit_sim_r4"] = second_unit_sim()
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2], sys.argv[3]), indent=1)[:4000])
