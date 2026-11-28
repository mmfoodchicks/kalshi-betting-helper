"""Guards for the coaching engine's fourth revision (R4): nfl_coach_sim_r4,
personnel-aware decisions after a quarterback substitution -- the fork of R1's
loop (no substitution, no change), the learned effects and the rule that kept
them, the two kinds of pull, conservation, the owner's chain, and the
registered read. Self-registered (tests/guards_auto_*.py, the AUTO section);
also runs alone: python3 tests/guards_auto_nfl_coach_r4.py
Hermetic: no raw nflverse file is read. research/reports/nfl_coach_r4.md is the prose.
"""
import hashlib
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from research import registered_pins as REGP  # noqa: E402  (option (c), 2026-11-28)

PINNED = {
    "nfl_deep_sim.py": "22a221d7810495db3f8f7bb1bef7c4e16c3bbbf282902a95f133cecdabc29820",
    "nfl_deep_data.py": "dff6756fb9ceb5a746750557aaef3d8424a1b57e953ab6aef8cd0abd4375b022",
    "nfl_pbp.py": "660234badefc3e3b5f0e1417a48431e4ad8ff33aab5df8a207834faa4862865d",
    "research/data/nfl_deep_tables.json": "8c118b8fe741566ba1a0f7fb9aa8f41a27a5578b25e0d4988dda059c131b870f",
}
SUMS = (("car", "rush_att"), ("rush_yd", "rush_yds"), ("rush_td", "td_rush"), ("rec", "comp"),
        ("rec_yd", "pass_yds"), ("rec_td", "td_pass"), ("pass_att", "pass_att"), ("pass_cmp", "comp"),
        ("pass_yd", "pass_yds"), ("pass_td", "td_pass"), ("int", "int"), ("sacks", "sacks"),
        ("kneels", "kneels"), ("fum", "fum_lost"))


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _roster(t, backup=True):
    out = [dict(id=t + "QB", pos="QB", car=3.0, tgt=0, ypc=5.0)]
    if backup:
        out.append(dict(id=t + "QB2", pos="QB", car=0.0, tgt=0, ypc=3.5))
    return out + [dict(id=t + "RB1", pos="RB", car=15.0, tgt=3.5, ypc=4.3, ypr=7.5, cr=0.78),
                  dict(id=t + "RB2", pos="RB", car=6.0, tgt=2.0, ypc=4.1, ypr=7.0, cr=0.76),
                  dict(id=t + "WR1", pos="WR", car=0.4, tgt=8.5, ypc=7.0, ypr=13.0, cr=0.64),
                  dict(id=t + "WR2", pos="WR", car=0.0, tgt=6.0, ypr=12.5, cr=0.62),
                  dict(id=t + "WR3", pos="WR", car=0.0, tgt=4.0, ypr=12.0, cr=0.6),
                  dict(id=t + "TE1", pos="TE", car=0.0, tgt=5.0, ypr=10.0, cr=0.7)]


def run(ck):
    import nfl_coach_sim as C
    import nfl_coach_sim_r3 as E3
    import nfl_coach_sim_r4 as E4
    from research import nfl_coach_r4_fit as F
    from research import nfl_coach_r4_read as RR

    print("  -- N4. The coaching engine R4 (personnel after a substitution): the fork, the rule, the kinds, "
          "conservation, the chain, the registration")
    for rel, want in PINNED.items():
        ck(f"N4-0 {rel} is byte-identical to the file the deep weight's read sits on (or, once that read is committed, exactly a staged flip's bytes)", REGP.live_ok(rel, want))
    moved = []
    for reg in ("prereg_r1.json", "prereg_r2.json", "prereg_r3.json"):
        moved += REGP.moved("research/data/nfl_coach/" + reg)
    ck("N4-0a [option (c): pins checked at the registration commit; live, only research/registered_pins.py's named exceptions] every file R1's, R2's and R3's registrations pin is byte-identical (R4 is new files)", not moved, moved)
    src = open(os.path.join(ROOT, "nfl_coach_sim_r4.py")).read()
    ck("N4-0b R4 plays R1's own snaps (no copy of _snap), writes no predlog bucket, and no production module "
       "imports it", "def _snap" not in src and "C._snap(S, box, sides, T, rng)" in src and "log_shadow" not in src
       and not any("nfl_coach_sim_r4" in open(os.path.join(ROOT, f)).read()
                   for f in os.listdir(ROOT) if f.endswith(".py") and not f.startswith("nfl_coach")))

    X = E4.tables()
    fit = json.load(open(F.OUT))
    ok = True
    for kind in ("strategic", "injury"):
        for nm, tkey, neutral in (("call_lead", "logit_shift", 0.0), ("call_trail", "logit_shift", 0.0),
                                  ("call_close", "logit_shift", 0.0), ("comp", "logit_shift", 0.0),
                                  ("int", "logit_shift", 0.0), ("tempo", "mult", 1.0),
                                  ("rb1", "odds_ratio", 1.0), ("top2", "odds_ratio", 1.0)):
            kept = F.keep(fit[kind]["fit"][nm], fit[kind]["dev"][nm])
            want = fit[kind]["fit"][nm][tkey] if kept else neutral
            ok &= X.effects[kind][nm] == want
    ck("N4-1 every effect in the tables is the fit's value where the rule kept it (|z| >= 3 on 2016-2024, the "
       "same sign on 2025-26) and neutral where it refused it", ok)
    ck("N4-1b what the rule kept: the strategic backup's call by margin, completion, the lead back's and the top "
       "two targets' odds; the injured starter's backup's completion and interceptions; tempo refused for both",
       X.effects["strategic"]["rb1"] < 0.7 and X.effects["strategic"]["top2"] < 0.9
       and X.effects["strategic"]["call_lead"] > 0 > X.effects["strategic"]["call_trail"]
       and X.effects["injury"]["rb1"] == 1.0 and X.effects["injury"]["int"] > 0
       and X.effects["strategic"]["tempo"] == 1.0 == X.effects["injury"]["tempo"]
       and 0.05 < X.ret_rate < 0.3 and len(X.type_coef) == 4)

    lg = C.league_profile()
    same = True
    for seed in range(15):
        rng1, rng2 = random.Random(seed), random.Random(seed)
        a = C.play_game(lg, lg, rng1)
        b = E4.play_game(lg, lg, rng2, random.Random(7), pull=False)
        same &= a["h"] == b["h"] and a["a"] == b["a"] and rng1.random() == rng2.random()
    ck("N4-2 no substitution, no change: R4's loop plays R1's game draw for draw (15 seeds, the box and the "
       "generator after it)", same)
    a = E4.simulate(lg, lg, (_roster("H"), _roster("A")), n=150, seed=3, pull=False)
    b = E3.simulate(lg, lg, (_roster("H"), _roster("A")), n=150, seed=3, coef="off")
    ck("N4-3 no substitution: every line of 150 games is R3's deal's (the R2 arm of nfl_coach_sim_r3.simulate, "
       "the same seeds)", all(x == y for x, y in zip(a["lines"], b["r2"])))
    c = E4.simulate(lg, lg, (_roster("H"), _roster("A")), n=300, seed=8, effects=False)
    same_g = all(x["h"] == y["h"] and x["a"] == y["a"] for x, y in zip(
        c["games"], E3.simulate(lg, lg, (_roster("H"), _roster("A")), n=300, seed=8, coef="off")["games"]))
    ck("N4-3b with the pull on and its consequences off, the games are still R1's (300 games)", same_g)

    # forced pull at the first identified drive: R4's in-game pull lands where R3's post-hoc pull does
    class _Q:
        coef = [30.0] + [0.0] * 9
    hit = True
    for seed in range(20):
        r = E4.play_game(lg, lg, random.Random(seed), random.Random(1), Q=_Q, effects=False)
        sn = E3.snaps(r["log"])
        for side in (0, 1):
            want = E3.pull_drive([s for s in sn if s[0] == side], _Q.coef, random.Random(2))
            hit &= r["qb"][side]["pull"] == want
    ck("N4-5 R4 draws the pull at the same drive R3 does (the first identified drive under a certain hazard, "
       "20 games x 2 sides): the in-game drive parser is R3's", hit)

    bad = []
    big = E4.simulate(lg, lg, (_roster("H"), _roster("A", False)), n=1500, seed=11)
    for res, lines in zip(big["games"], big["lines"]):
        for key, L in (("h", lines[0]), ("a", lines[1])):
            for mine, theirs in SUMS:
                if sum(v[mine] for v in L.values()) != res[key][theirs]:
                    bad.append((key, mine))
    ck("N4-4 conservation with every consequence on: both quarterbacks' lines and every carry, catch, yard, "
       "touchdown, sack, kneel and lost fumble sum to the box (1,500 games, a listed backup and a placeholder)",
       not bad, bad[:3])
    kinds = [q["kind"] for g in big["qb"] for q in g if q["pull"] is not None]
    rets = [q for g in big["qb"] for q in g if q["back"] is not None]
    ck("N4-6 both kinds are drawn, only injured starters come back, and after the return the starter has the ball "
       "again", "injury" in kinds and "strategic" in kinds and rets and all(q["kind"] == "injury" for q in rets)
       and all(q["back"] > q["pull"] for q in rets))

    # ---- the owner's chain, played from the state
    def from_state(lead, clk, effects, n=300):
        st = {"per": 4, "clk": clk, "pts": (lead, 0) if lead >= 0 else (0, -lead), "off": 0, "down": 1,
              "dist": 10, "ytg": 75}
        return E4.simulate(lg, lg, (_roster("H"), _roster("A")), n=n, seed=19, effects=effects, start=st)
    on, off = from_state(35, 600, True), from_state(35, 600, False)

    def share(sim, who, stat, tot):
        a = b = 0
        for res, lines, q in zip(sim["games"], sim["lines"], sim["qb"]):
            if q[0]["pull"] is None or q[0]["kind"] != "strategic":
                continue
            a += lines[0][who][stat]
            b += res["h"][tot]
        return a / b if b else None
    s_on, s_off = share(on, "HRB1", "car", "rush_att"), share(off, "HRB1", "car", "rush_att")
    t_on = share(on, "HWR1", "tgt", "pass_att")
    t_off = share(off, "HWR1", "tgt", "pass_att")
    ck("N4-7 the chain, leading by 35 with 10:00 left: the starter is rested, the second unit comes in with the "
       "backup -- the lead back's share of the carries and the top receiver's of the targets fall",
       s_on is not None and s_off is not None and s_on < s_off - 0.05 and t_on < t_off, (s_on, s_off, t_on, t_off))
    # the shifts are set before every snap for whoever is at quarterback, the call by the CURRENT margin.
    # (A whole-game pass share is not a usable check: a backup's misses stop the clock and the games diverge
    # after the pull, so it moved the "wrong" way in 300 games while every snap's shift was right.)
    import nfl_deep_sim as D
    T = C.tables()
    sides = (D._Side(lg, lg, T.base, 1.0), D._Side(lg, lg, T.base, 1.0))
    base = [(x.d_call, x.d_comp, x.d_int, x.tempo) for x in sides]
    tr = E4._Track(E3.tables(), X, random.Random(0))
    tr.qb[0].update({"in": "backup", "kind": "strategic", "pull": 3})
    tr.qb[1].update({"in": "backup", "kind": "injury", "pull": 4})
    got = []
    for pts in ((20, 0), (0, 20), (3, 0)):
        box = ({"pts": pts[0]}, {"pts": pts[1]})
        E4._apply(sides, base, tr, box, X)
        got.append((sides[0].d_call - base[0][0], sides[1].d_call - base[1][0], sides[0].d_comp - base[0][1],
                    sides[1].d_int - base[1][2]))
    tr.qb[0]["in"] = "starter"
    E4._apply(sides, base, tr, ({"pts": 20}, {"pts": 0}), X)
    ef_s, ef_i = X.effects["strategic"], X.effects["injury"]
    ck("N4-8 the call follows the backup as learned and the current margin: a strategic backup leading by 20 "
       "gets the leading shift, trailing by 20 the trailing one, within 3 the close one; an injured starter's "
       "backup his completion and interception shifts; back to the starter, back to the base",
       abs(got[0][0] - ef_s["call_lead"]) < 1e-12 and abs(got[1][0] - ef_s["call_trail"]) < 1e-12
       and abs(got[2][0] - ef_s["call_close"]) < 1e-12 and abs(got[0][2] - ef_s["comp"]) < 1e-12
       and abs(got[0][3] - ef_i["int"]) < 1e-12 and abs(got[1][1] - ef_i["call_lead"]) < 1e-12
       and (sides[0].d_call, sides[0].d_comp, sides[0].d_int, sides[0].tempo) == base[0], got)

    # ---- registration
    reg = RR.prereg()
    body = json.dumps({k: v for k, v in reg.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    ck("N4-9 the registration: its hash holds; 2026 weeks 13-18 and 2027 weeks 1-18 on/after 2028-01-13 after "
       "R1's read; the dependence on R1, R2 and R3 stated",
       hashlib.sha256(body.encode()).hexdigest() == reg["block_sha256"]
       and reg["window"] == [{"season": 2026, "weeks": list(range(13, 19))}, {"season": 2027, "weeks": list(range(1, 19))}]
       and reg["not_before_utc"] == "2028-01-13T06:00:00Z" and reg["requires_r1_read"]
       and all(x in reg["dependence"] for x in ("R1", "R2", "R3")))
    moved = REGP.moved("research/data/nfl_coach/prereg_r4.json")
    ck("N4-9b [option (c): pins checked at the registration commit; live, only research/registered_pins.py's named exceptions] every file the registration pins matches its sha256", not moved
       and "tests/guards_auto_nfl_coach_r4.py" in reg["files"] and "nfl_coach_sim_r3.py" in reg["files"], moved)
    # option (c), 2026-11-28: the read runs from its registration commit (research/reports/registered_reads_runbook.md),
    # so whether it WOULD run is asked of that commit's tree; the live tree may hold a staged fix.
    with REGP.registered_view("research/data/nfl_coach/prereg_r4.json", RR):
        w = [RR.refusal(now_utc="2027-06-01T00:00:00Z", dirty=False, reg=reg, r1_read=True, already=False),
             RR.refusal(now_utc="2028-02-01T00:00:00Z", dirty=True, reg=reg, r1_read=True, already=False),
             RR.refusal(now_utc="2028-02-01T00:00:00Z", dirty=False, reg=reg, r1_read=False, already=False),
             RR.refusal(now_utc="2028-02-01T00:00:00Z", dirty=False, reg=reg, r1_read=True, already=True),
             RR.refusal(now_utc="2028-02-01T00:00:00Z", dirty=False, reg=reg, r1_read=True, already=False)]
    ck("N4-10 [would-run asked of the registration commit's tree, option (c)] the read refuses before its date, a dirty tree, before R1's read and a second read, and would run "
       "after", "before" in (w[0] or "") and "dirty" in (w[1] or "") and "R1" in (w[2] or "")
       and "already" in (w[3] or "") and w[4] is None, w)
    good = {"guards_exit": 0,
            "decision": {"joint": {"gain_per_pulled_team_game": 0.4, "se": 0.12},
                         "personnel": {"gain_per_pulled_team_game": 0.3, "se": 0.11},
                         "call": {"gain_per_pulled_team_game": 0.06, "se": 0.02},
                         "comp": {"gain_per_pulled_team_game": 0.02, "se": 0.02}},
            "realism": {"rb1_carries_crps": {"r4_minus_r3_per_team_game": -0.01, "se": 0.02},
                        "top2_targets_crps": {"r4_minus_r3_per_team_game": 0.0, "se": 0.02}}}
    weak = json.loads(json.dumps(good))
    weak["decision"]["joint"]["gain_per_pulled_team_game"] = 0.2
    rev = json.loads(json.dumps(good))
    rev["decision"]["comp"]["gain_per_pulled_team_game"] = -0.03
    worse = json.loads(json.dumps(good))
    worse["realism"]["rb1_carries_crps"]["r4_minus_r3_per_team_game"] = 0.05
    ck("N4-11 the verdict is the registered rule: all hold -> PASS; the joint gain under 2 SE, a component worse "
       "by more than 1 SE, or the lead back's CRPS worse by more than 2 SE -> FAIL",
       RR.verdict(good)["pass"] and not RR.verdict(weak)["pass"] and not RR.verdict(rev)["pass"]
       and not RR.verdict(worse)["pass"])
    regy = json.load(open(os.path.join(ROOT, "research", "data", "research_registry.json")))
    listed = {e["registration"] for e in regy}
    ck("N4-12 R3's and R4's registrations are on the Research tab's registry",
       "research/data/nfl_coach/prereg_r3.json" in listed and "research/data/nfl_coach/prereg_r4.json" in listed)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def _ck(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail and not cond else ""))
    run(_ck)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
