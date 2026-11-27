"""Guards for the coaching engine's sixth revision (R6): nfl_coach_sim_r6, the
selectively drifting league -- R1-R5 untouched, parity with R5 when the drift is
off, the three components as the fit set them and nothing else moved, conservation,
the registered statistic, the 2027 family with R6 in it, and the registration.
Self-registered (tests/guards_auto_*.py, the AUTO section); also runs alone:
python3 tests/guards_auto_nfl_coach_r6.py. Hermetic: no raw nflverse file is read.
"""
import hashlib
import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SUMS = (("car", "rush_att"), ("rush_yd", "rush_yds"), ("rush_td", "td_rush"), ("rec", "comp"),
        ("rec_yd", "pass_yds"), ("rec_td", "td_pass"), ("pass_att", "pass_att"), ("pass_cmp", "comp"),
        ("pass_yd", "pass_yds"), ("pass_td", "td_pass"), ("int", "int"), ("sacks", "sacks"),
        ("kneels", "kneels"), ("fum", "fum_lost"))
EVO_RULE_SHA = "7f139636e0ec3f3ff4b071d98df3af53d12a463113a172270e71cef00be5c8d2"


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
    import nfl_coach_sim_r2 as E2
    import nfl_coach_sim_r5 as E5
    import nfl_coach_sim_r6 as E6
    from research import nfl_coach_r6_fit as F6
    from research import nfl_coach_r6_read as RR

    print("  -- N6. The coaching engine R6 (the selectively drifting league): R1-R5 untouched, parity, the three "
          "components, conservation, the statistic, the family, the registration")
    moved = []
    for reg in ("prereg_r1.json", "prereg_r2.json", "prereg_r3.json", "prereg_r4.json", "prereg_r5.json",
                "prereg_r5_2027.json"):
        r = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", reg)))
        moved += [rel for rel, h in r["files"].items() if _sha(rel) != h]
    src = open(os.path.join(ROOT, "nfl_coach_sim_r6.py")).read()
    ck("N6-0 R1-R5 and their registrations are untouched (every pinned file byte-identical); R6 copies none of "
       "R1's clock or snap code, writes no predlog bucket, and no production module imports it",
       not moved and "def _snap" not in src and "def _gap" not in src and "C._snap(S, box, sides, T, rng)" in src
       and "log_shadow" not in src and not any("nfl_coach_sim_r6" in open(os.path.join(ROOT, f)).read()
                                                for f in os.listdir(ROOT) if f.endswith(".py")
                                                and not f.startswith("nfl_coach")), moved)
    P = E6.tables()
    tab = P.raw
    q5 = E5.tables()
    ck("N6-1 the three components are the fit's and nothing else moves: m_clock = exp(forecast - R1's 2016-2023 "
       "level), delta = forecast - R2's level, the pull's intercept R5's + its forecast (every other coefficient "
       "and the second unit R5's); R6 never touches R1's call or no-huddle tables (it passes R1's own)",
       abs(P.m_clock - math.exp(tab["clock"]["mu"] - tab["clock"]["level_r1_2016_2023"])) < 1e-12
       and abs(P.delta_rb - (tab["rb_target"]["mu"] - tab["rb_target"]["level_r2_2016_2023"])) < 1e-12
       and abs(P.pull_q.coef[0] - q5.coef[0] - tab["pull"]["mu"]) < 1e-12 and P.pull_q.coef[1:] == q5.coef[1:]
       and P.pull_q.raw["second_unit"] == q5.raw["second_unit"] and "T.nh" not in src and "p_pass" not in src
       and "T = T or C.tables()" in src)
    lg = C.league_profile()
    same = True
    for seed in range(12):
        rng1, rng2 = random.Random(seed), random.Random(seed)
        pl = (_roster("H"), _roster("A"))
        a = E5.play_game(lg, lg, rng1, random.Random(5), players=pl, deal_rngs=(random.Random(1), random.Random(2)))
        b = E6.play_game(lg, lg, rng2, random.Random(5), players=pl, deal_rngs=(random.Random(1), random.Random(2)),
                         drift=False)
        same &= a["h"] == b["h"] and a["a"] == b["a"] and a["log"] == b["log"] and a["lines"] == b["lines"] \
            and a["qb"] == b["qb"] and rng1.random() == rng2.random()
    a = E5.simulate(lg, lg, (_roster("H"), _roster("A")), n=150, seed=3)
    b = E6.simulate(lg, lg, (_roster("H"), _roster("A")), n=150, seed=3, drift=False)
    ck("N6-2 with the three components set back (drift off), R6 plays R5's game draw for draw -- boxes, logs, "
       "pulls, lines (12 dealt games; 150 simulated with pulls, every line)",
       same and a["lines"] == b["lines"] and a["qb"] == b["qb"])
    U = E2.tables()
    base = E2.TeamDeal(_roster("H"), U)
    t0 = E6.Team(_roster("H"), U, delta=0.0)
    t1 = E6.Team(_roster("H"), U, delta=-0.3)
    ok = True
    for b_ in range(E2.NB):
        p0, p1 = base.p("tgt", b_), t1.p("tgt", b_)
        ok &= t0.p("tgt", b_) == p0 and t0.p("car", b_) == base.p("car", b_) and t1.p("car", b_) == base.p("car", b_)
        for i in p0:
            for j in p0:
                if i.startswith("HRB") and not j.startswith("HRB") and p0[i] > 0 and p0[j] > 0:
                    ok &= abs((p1[i] / p1[j]) / (p0[i] / p0[j]) - math.exp(-0.3)) < 1e-9
    ck("N6-3 the backs' target odds: delta 0 is R2's probabilities; delta d multiplies every running back's "
       "target odds against every other player's by exactly exp(d); carries never move", ok)
    bad = []
    big = E6.simulate(lg, lg, (_roster("H"), _roster("A", False)), n=1000, seed=11,
                      start={"per": 3, "clk": 900, "pts": (21, 0), "off": 0, "down": 1, "dist": 10, "ytg": 75})
    for res, lines in zip(big["games"], big["lines"]):
        for key, L in (("h", lines[0]), ("a", lines[1])):
            for mine, theirs in SUMS:
                if sum(v[mine] for v in L.values()) != res[key][theirs]:
                    bad.append((key, mine))
    ck("N6-4 conservation with the drift on: every carry, catch, yard, touchdown, sack, kneel and lost fumble sums "
       "to the box (1,000 games from 21-0 at the half)", not bad, bad[:3])

    class _Slow:
        m_clock, delta_rb, mu_pull, pull_q = 1.5, 0.0, 0.0, q5
    snaps = {}
    for nm, R6 in (("base", None), ("slow", _Slow)):
        n = 0
        for seed in range(150):
            r = E6.play_game(lg, lg, random.Random(seed), random.Random(seed), drift=nm == "slow", R6=R6)
            n += r["h"]["plays"] + r["a"]["plays"] if "plays" in r["h"] else len([e for e in r["log"]
                                                                                  if e["k"] in E2._SNAPS])
        snaps[nm] = n
    ck("N6-5 the clock acts through R1's own pace hook: a league pace of 1.5 plays fewer snaps a game than R1's "
       "(150 games each)", snaps["slow"] < snaps["base"] * 0.97, snaps)
    sd, gsr = -21, 400
    h5 = E5.hazard(q5.coef, sd, gsr, 3, q5.spec)
    h6 = E5.hazard(P.pull_q.coef, sd, gsr, 3, P.pull_q.spec)
    ck("N6-6 the pull: R6's hazard is R5's with the forecast added to the log odds",
       abs(math.log(h6 / (1 - h6)) - math.log(h5 / (1 - h5)) - tab["pull"]["mu"]) < 1e-9)
    cref = {"means": {(0, 2): 3.0}, "sigma": 0.5, "default": 3.0}
    cr = {"g1": [((0, 2), 3.1), ((0, 2), 3.1)], "g2": [((0, 2), 3.1)]}
    g0, g1 = F6.clock_gain(cr, cref, 0.0), F6.clock_gain(cr, cref, 0.1)
    rref = {"rates": {(1, 0, 3): 0.2}, "p0": 0.2}
    rows = {("g1", (1, 0, 3)): [1, 5]}
    ck("N6-7 the registered statistic: no offset, no gain; an offset equal to the residual gains exactly "
       "e^2/(2 sigma^2) a gap (0.02 each); the backs' rows score 0 at no offset",
       g0 == {"g1": 0.0, "g2": 0.0} and abs(g1["g1"] - 2 * 0.01 / 0.5) < 1e-12 and abs(g1["g2"] - 0.02) < 1e-12
       and abs(F6.rb_gain(rows, rref, 0.0)["g1"]) < 1e-12)
    evo = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", "evo.json")))
    ck("N6-8 the rejected all-table experiment is preserved with its verdict: the evo rule unchanged and its scheme "
       "'full'", _sha("research/data/nfl_coach/evo_rule.json") == EVO_RULE_SHA and evo["decision"]["scheme"] == "full")
    fam = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", "family_2027_r6.json")))
    from research import nfl_coach_family_2027 as FM
    ck("N6-9 the 2027 family with R6: four primaries, Holm steps at z 2.241 / 2.128 / 1.960 / 1.645, R5's own "
       "three-test file untouched beside it",
       len(fam["family"]["tests"]) == 4 and [round(x, 3) for x in fam["family"]["holm_z_thresholds"]]
       == [2.241, 2.128, 1.96, 1.645] == [round(x, 3) for x in FM.holm(0.05, 4)]
       and len(json.load(open(FM.OUT))["family"]["tests"]) == 3)
    reg = RR.prereg()
    body = json.dumps({k: v for k, v in reg.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    moved = [rel for rel, hh in reg["files"].items() if _sha(rel) != hh]
    ck("N6-10 the registration: its hash holds; primary 2027 (on/after 2028-01-13, after R1's read), replication "
       "2028 (on/after 2029-01-16, after the primary); the components frozen; every pinned file matches",
       hashlib.sha256(body.encode()).hexdigest() == reg["block_sha256"] and not moved
       and reg["windows"]["primary"]["season"] == 2027 and reg["windows"]["replication"]["season"] == 2028
       and reg["windows"]["primary"]["not_before_utc"] == "2028-01-13T06:00:00Z"
       and reg["windows"]["replication"]["requires_reads"] == ["R6-primary"]
       and reg["components_frozen"]["drifting"] == ["clock", "rb_target", "pull"]
       and "tests/guards_auto_nfl_coach_r6.py" in reg["files"], moved)
    ok_ = {"R1": 1, "R6-primary": 1}
    w = [RR.refusal("primary", now_utc="2027-12-01T00:00:00Z", dirty=False, reg=reg, earlier_read=ok_, already=False),
         RR.refusal("primary", now_utc="2028-02-01T00:00:00Z", dirty=True, reg=reg, earlier_read=ok_, already=False),
         RR.refusal("primary", now_utc="2028-02-01T00:00:00Z", dirty=False, reg=reg, earlier_read={}, already=False),
         RR.refusal("replication", now_utc="2029-02-01T00:00:00Z", dirty=False, reg=reg, earlier_read={"R1": 1},
                    already=False),
         RR.refusal("primary", now_utc="2028-02-01T00:00:00Z", dirty=False, reg=reg, earlier_read=ok_, already=True),
         RR.refusal("primary", now_utc="2028-02-01T00:00:00Z", dirty=False, reg=reg, earlier_read=ok_, already=False),
         RR.refusal("replication", now_utc="2029-02-01T00:00:00Z", dirty=False, reg=reg, earlier_read=ok_,
                    already=False)]
    ck("N6-11 the reads refuse before their dates, a dirty tree, the primary before R1's read, the replication "
       "before the primary, a second read; and would run after",
       "before" in (w[0] or "") and "dirty" in (w[1] or "") and "R1" in (w[2] or "") and "R6-primary" in (w[3] or "")
       and "already" in (w[4] or "") and w[5] is None and w[6] is None, w)
    good = {"guards_exit": 0, "pooled": {"gain_per_game": 0.1, "se": 0.03},
            "components": {"clock": {"gain_per_game": 0.08, "se": 0.02}, "rb": {"gain_per_game": 0.02, "se": 0.01},
                           "pull": {"gain_per_game": 0.0, "se": 0.006}}}
    weak = json.loads(json.dumps(good))
    weak["pooled"]["gain_per_game"] = 0.05
    worse = json.loads(json.dumps(good))
    worse["components"]["pull"]["gain_per_game"] = -0.007
    ck("N6-12 the verdict is the registered rule: all hold -> PASS; the pooled gain under 2 SE, or a component "
       "below -1 SE -> FAIL", RR.verdict(good)["pass"] and not RR.verdict(weak)["pass"]
       and not RR.verdict(worse)["pass"])
    regy = json.load(open(os.path.join(ROOT, "research", "data", "research_registry.json")))
    ck("N6-13 R6's registration is on the Research tab's registry",
       "research/data/nfl_coach/prereg_r6.json" in {e["registration"] for e in regy})


if __name__ == "__main__":
    PASS, FAIL = [], []

    def _ck(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail and not cond else ""))
    run(_ck)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
