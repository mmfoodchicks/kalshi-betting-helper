"""Guards for the coaching engine's third revision (R3): nfl_coach_sim_r3, the
quarterback's substitution -- the learned hazard, the pulled starter staying
out, conservation, R2 exactly when the pull is off, the owner's situations, and
the registered read. Self-registered (tests/guards_auto_*.py run in
combo_audit_guards' AUTO section); also runs alone:
python3 tests/guards_auto_nfl_coach_r3.py

Hermetic: nothing here reads nflverse's raw files. research/reports/nfl_coach_r3.md
is the prose.
"""
import hashlib
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

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
                  dict(id=t + "TE1", pos="TE", car=0.0, tgt=5.0, ypr=10.0, cr=0.7)]


def _start(lead, clk):
    return {"per": 4, "clk": clk, "pts": (lead, 0) if lead >= 0 else (0, -lead), "off": 0, "down": 1,
            "dist": 10, "ytg": 75}


def run(ck):
    import nfl_coach_sim as C
    import nfl_coach_sim_r2 as E2
    import nfl_coach_sim_r3 as E3
    from research import nfl_coach_r3_qb as QB
    from research import nfl_coach_r3_read as RR

    print("  -- NQ. The coaching engine R3 (the quarterback's substitution): production, R1 and R2 untouched, "
          "the learned hazard, the pull that stays, conservation, the registration")
    for rel, want in PINNED.items():
        ck(f"NQ0 {rel} is byte-identical to the file the deep weight's read sits on", _sha(rel) == want)
    moved = []
    for reg in ("prereg_r1.json", "prereg_r2.json"):
        r = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", reg)))
        moved += [rel for rel, h in r["files"].items() if _sha(rel) != h]
    ck("NQ0a every file R1's and R2's registrations pin is still byte-identical (R3 is new files only)",
       not moved, moved)
    src = open(os.path.join(ROOT, "nfl_coach_sim_r3.py")).read()
    ck("NQ0b R3 writes no predlog bucket, no production module imports it, and it imports R1 and R2 "
       "(never a copy of either's snap loop or tables)",
       "log_shadow" not in src and "import predlog" not in src and "def _snap" not in src
       and "import nfl_coach_sim as C" in src and "import nfl_coach_sim_r2 as E2" in src
       and not any("nfl_coach_sim_r3" in open(os.path.join(ROOT, f)).read()
                   for f in os.listdir(ROOT) if f.endswith(".py") and not f.startswith("nfl_coach")))

    Q = E3.tables()
    raw = Q.raw
    ck("NQ1 the tables: the s6 form chosen on 2024, fitted 2016-2024 at a half-life of two seasons anchored on "
       "2024, a constant-hazard baseline beside it, credit and the seal stated, and no coach term",
       raw["spec"] == E3.SPEC == "s6" and raw["fit_seasons"] == list(range(2016, 2025))
       and raw["select_season"] == 2024 and raw["half_life"] == 2.0 and raw["anchor_season"] == 2024
       and len(raw["coef"]) == 10 and len(raw["coef_constant"]) == 1 and "CC BY 4.0" in raw["credit"]
       and "2026 week 13" in raw["seal"] and not any("coach" in k for k in raw if k != "credit"))
    grid = [(sd, g) for sd in (-45, -28, -17, -7, 0, 3, 9, 16, 24, 35, 50) for g in (0, 30, 120, 480, 900, 1800, 3600)]
    ck("NQ1b the engine's features are the fit's s6 features, to the last bit, on 77 states",
       all(E3.features(sd, g) == QB.feats(sd, g, 1, "s6") for sd, g in grid))

    lg = C.league_profile()
    same = True
    for seed in range(12):
        rng = random.Random(seed)
        a = [C.play_game(lg, lg, rng) for _ in range(3)]
        r = E3.simulate(lg, lg, (_roster("H"), _roster("A")), n=3, seed=seed)
        same &= all(x["h"] == y["h"] and x["a"] == y["a"] for x, y in zip(a, r["games"]))
    ck("NQ2 R3's games are R1's draw for draw (12 seeds x 3 games, every box equal)", same)

    # ---- NQ3: with the pull off, R3's deal is R2's deal, line for line
    eq = True
    U = E2.tables()
    for seed in range(30):
        log = []
        res = C.play_game(lg, lg, random.Random(seed), log=log)
        sn = E3.snaps(log)
        for side in (0, 1):
            pl = _roster("H" if side == 0 else "A")
            td = E2.TeamDeal(pl, U)
            s_sn = [s for s in sn if s[0] == side]
            box = res["h" if side == 0 else "a"]
            a = E2.deal([s[:6] for s in s_sn], box, td, random.Random(seed * 7 + side), U)
            b = E3.deal(s_sn, box, td, random.Random(seed * 7 + side), pl[1]["id"], None, U)
            eq &= all(a[i] == b[i] for i in a)
    ck("NQ3 with no pull, R3's deal is R2's (E2.deal) for every player's line in 60 team-games, the passing "
       "yards summed play by play equal to R2's box figure", eq)

    # ---- NQ4: conservation, both arms, three roster shapes
    bad = []
    for shape in ("backup", "placeholder", "both_placeholder"):
        ros = (_roster("H", shape == "backup"), _roster("A", shape != "both_placeholder"))
        sim = E3.simulate(lg, lg, ros, n=400, seed=21)
        for arm in ("r2", "r3"):
            for res, lines in zip(sim["games"], sim[arm]):
                for key, L in (("h", lines[0]), ("a", lines[1])):
                    for mine, theirs in SUMS:
                        if sum(v[mine] for v in L.values()) != res[key][theirs]:
                            bad.append((shape, arm, key, mine))
    ck("NQ4 conservation: both quarterbacks' attempts, completions, yards, touchdowns, interceptions, sacks and "
       "kneels, and every carry, catch, yard and touchdown, sum to the box (1,200 games x 2 arms, a listed "
       "backup and a placeholder)", not bad, bad[:3])

    # ---- NQ5: the pull stays
    sim = E3.simulate(lg, lg, (_roster("H"), _roster("A")), n=1500, seed=5)
    stay, pulls = True, 0
    for res, lines, pd in zip(sim["games"], sim["r3"], sim["pulled"]):
        for side, t in ((0, "H"), (1, "A")):
            L = lines[side]
            if pd[side] is None:
                stay &= all(L[t + "QB2"][k] == 0 for k in ("pass_att", "sacks", "kneels", "car"))
            else:
                pulls += 1
    # the starter's dropbacks are exactly the dropbacks before the pull's drive
    ok_cut = True
    for seed in range(40):
        log = []
        res = C.play_game(lg, lg, random.Random(seed), log=log)
        sn = [s for s in E3.snaps(log) if s[0] == 0]
        ds = sorted({s[6] for s in sn if s[1] in ("cmp", "inc", "int", "sack", "spike", "kneel")})
        if len(ds) < 3:
            continue
        cut = ds[len(ds) // 2]
        td = E2.TeamDeal(_roster("H"), U)
        L = E3.deal(sn, res["h"], td, random.Random(1), "HQB2", cut, U)
        want = sum(1 for s in sn if s[6] < cut and s[1] in ("cmp", "inc", "int", "spike"))
        ok_cut &= L["HQB"]["pass_att"] == want and L["HQB2"]["pass_att"] == res["h"]["pass_att"] - want
    ck("NQ5 a pulled starter stays out: no backup line without a pull; with a pull at drive d the starter's "
       "attempts are exactly those before d and the backup's every one after", stay and ok_cut and pulls > 0)

    # ---- the owner's situations, played from the state
    def pulled(lead, clk, n=300):
        r = E3.simulate(lg, lg, (_roster("H"), _roster("A")), n=n, seed=9, start=_start(lead, clk))
        return sum(1 for a, _b in r["pulled"] if a is not None) / n, r
    p35, r35 = pulled(35, 480)
    kn = sum(ln[0]["HQB2"]["kneels"] for ln in r35["r3"])
    knt = kn + sum(ln[0]["HQB"]["kneels"] for ln in r35["r3"])
    ck("SQ1 leading by 35 with 8:00 left the starter is rested in most games (learned: 0.76 a drive) and the "
       "backup takes the victory formation", p35 > 0.8 and kn > 0.8 * knt, (p35, kn, knt))
    pt, _ = pulled(-35, 300)
    ck("SQ2 trailing by 35 with 5:00 left the starter may be replaced: in some games, not most", 0.2 < pt < 0.65, pt)
    p24, _ = pulled(24, 120)
    ck("SQ3 leading by 24 with 2:00 left the starter is protected in most games", p24 > 0.5, p24)
    p7, _ = pulled(7, 900)
    p7b, _ = pulled(7, 120)
    ck("SQ4 leading by 7 the starter stays: the pull rate from the fourth quarter's start or 2:00 is the "
       "injury rate's size (< 6%)", p7 < 0.06 and p7b < 0.03, (p7, p7b))
    h = [E3.hazard(Q.coef, lead, 300) for lead in (7, 14, 21, 28, 35)]
    ht = [E3.hazard(Q.coef, 28, g) for g in (1800, 900, 480, 120, 30)]
    ck("SQ5 the learned hazard rises with the lead (7 to 35) at 5:00 left and, at a 28-point lead, as the clock runs "
       "out; a tie in the first quarter is the injury rate (< 1% a drive)",
       all(a < b for a, b in zip(h, h[1:])) and all(a < b for a, b in zip(ht, ht[1:]))
       and E3.hazard(Q.coef, 0, 3300) < 0.01, (h, ht))
    sh = {"close": [0, 0], "won": [0, 0], "lost": [0, 0]}
    for res, lines in zip(sim["games"], sim["r3"]):
        m = res["h"]["pts"] - res["a"]["pts"]
        k = "won" if m >= 17 else "lost" if m <= -17 else "close" if abs(m) <= 7 else None
        if k:
            sh[k][0] += lines[0]["HQB"]["pass_att"] + lines[0]["HQB"]["sacks"]
            sh[k][1] += res["h"]["pass_att"] + res["h"]["sacks"]
    s = {k: v[0] / v[1] for k, v in sh.items()}
    ck("SQ6 whole games: the starter's share of his team's dropbacks is lowest losing big, as in real games "
       "(2024-26: 0.86-0.92 lost by 17+, 0.96-0.99 otherwise)",
       s["lost"] < s["close"] and s["lost"] < s["won"] and 0.8 < s["lost"] < 0.95 and s["close"] > 0.93, s)

    # ---- NQ6: drive boundaries on a hand-built log
    log = [{"k": "ko", "q": 1, "c": 900, "s": (0, 0)},
           {"k": "run", "q": 1, "c": 890, "o": 0, "d": 1, "t": 10, "y": 75, "yd": 4, "s": (0, 0)},
           {"k": "cmp", "q": 1, "c": 860, "o": 0, "d": 2, "t": 6, "y": 71, "yd": 9, "s": (0, 0)},
           {"k": "punt", "q": 1, "c": 800, "o": 0, "s": (0, 0)},
           {"k": "run", "q": 1, "c": 790, "o": 1, "d": 1, "t": 10, "y": 80, "yd": 80, "td": 1, "s": (0, 6)},
           {"k": "try", "q": 1, "c": 789, "o": 1, "s": (0, 7)},
           {"k": "ko", "q": 1, "c": 789, "s": (0, 7)},
           {"k": "sack", "q": 1, "c": 760, "o": 0, "d": 1, "t": 10, "y": 75, "yd": -7, "s": (0, 7)}]
    sn = E3.snaps(log)
    ck("NQ6 drives: a punt, a score and a kickoff end one; the margin is read before the snap",
       [x[6] for x in sn] == [0, 0, 1, 2] and sn[3][9] == -7 and sn[2][9] == 0)

    # ---- NQ7: the registration and the read
    reg = RR.prereg()
    body = json.dumps({k: v for k, v in reg.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    ck("NQ7 the registration: its own hash holds; 2026 weeks 13-18 on/after 2027-01-06 after R1's read; "
       "the dependence on R1 and R2 stated",
       hashlib.sha256(body.encode()).hexdigest() == reg["block_sha256"]
       and reg["window"] == [{"season": 2026, "weeks": list(range(13, 19))}]
       and reg["not_before_utc"] == "2027-01-06T06:00:00Z" and reg["requires_r1_read"]
       and "R1" in reg["dependence"] and "R2" in reg["dependence"])
    moved = [rel for rel, hh in reg["files"].items() if _sha(rel) != hh]
    ck("NQ7b every file the registration pins matches its sha256", not moved and
       "tests/guards_auto_nfl_coach_r3.py" in reg["files"] and "nfl_coach_sim_r2.py" in reg["files"], moved)
    w = [RR.refusal(now_utc="2026-12-01T00:00:00Z", dirty=False, reg=reg, r1_read=True, already=False),
         RR.refusal(now_utc="2027-02-01T00:00:00Z", dirty=True, reg=reg, r1_read=True, already=False),
         RR.refusal(now_utc="2027-02-01T00:00:00Z", dirty=False, reg=reg, r1_read=False, already=False),
         RR.refusal(now_utc="2027-02-01T00:00:00Z", dirty=False, reg=reg, r1_read=True, already=True),
         RR.refusal(now_utc="2027-02-01T00:00:00Z", dirty=False, reg=reg, r1_read=True, already=False)]
    ck("NQ8 the read refuses before its date, a dirty tree, before R1's read and a second read, and would run "
       "on a clean tree after R1's", "before" in (w[0] or "") and "dirty" in (w[1] or "") and "R1" in (w[2] or "")
       and "already" in (w[3] or "") and w[4] is None, w)
    good = {"guards_exit": 0, "stage1": {"gain_per_team_game": 0.28, "se": 0.07},
            "realism": {"starter_dropbacks_crps": {"r3_minus_r2_per_team_game": -0.34, "se": 0.09},
                        "starter_pass_yards_crps": {"r3_minus_r2_per_team_game": -1.0, "se": 0.6}}}
    weak = json.loads(json.dumps(good))
    weak["stage1"]["gain_per_team_game"] = 0.13
    flat = json.loads(json.dumps(good))
    flat["realism"]["starter_dropbacks_crps"]["r3_minus_r2_per_team_game"] = -0.15
    worse = json.loads(json.dumps(good))
    worse["realism"]["starter_pass_yards_crps"]["r3_minus_r2_per_team_game"] = 0.7
    ck("NQ9 the verdict is the registered rule: all hold -> PASS; the hazard's gain under 2 SE, the dropbacks' "
       "CRPS better by under 2 SE, or the yards' CRPS worse by more than 1 SE -> FAIL",
       RR.verdict(good)["pass"] and not RR.verdict(weak)["pass"] and not RR.verdict(flat)["pass"]
       and not RR.verdict(worse)["pass"])


if __name__ == "__main__":
    PASS, FAIL = [], []

    def _ck(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail and not cond else ""))
    run(_ck)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
