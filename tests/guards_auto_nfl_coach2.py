"""Guards for the coaching engine's follow-ups (2026-11-27, the owner's four
decisions): R1's registration preserved, the three-verdict report, the
confirmatory test of criterion 4 with its error control, and the independent
DFS read with its research-only engine variant. A separate file because
tests/guards_auto_nfl_coach.py is pinned by R1's registration.

Runs alone: python3 tests/guards_auto_nfl_coach2.py
"""
import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
R1_BLOCK = "ba0bae085ed537d91a14844076d473828c44f1b26089d4a80f36a13fa75514ae"
MINE = ("nfl_coach_sim.py", "research/data/nfl_coach_tables.json", "research/nfl_coach_eval.py",
        "research/nfl_coach_data.py", "research/nfl_coach_reg.py", "research/nfl_coach_c4_confirm.py",
        "research/nfl_coach_dfs.py", "research/nfl_coach_dfs_read.py", "research/data/nfl_coach/prereg_r1.json")


def _read(c):
    """A stand-in for read_r1.json with the given criteria."""
    return {"verdict": {"pass": all(c.values()), "criteria": dict(c)}, "registration": R1_BLOCK, "games": 96,
            "read_utc": "2027-01-07T00:00:00Z",
            "summary": {"wp_late": {"n": 96, "diff": -0.004, "se": 0.008},
                        "pregame_logloss_diff": [0.001, 0.01], "total_crps_diff": [0.0, 0.1]}}


def run(ck):
    from research import nfl_coach_c4_confirm as CF
    from research import nfl_coach_dfs_read as DR
    from research import nfl_coach_r1_report as RP
    from research import nfl_coach_reg as RG

    print("  -- NF. Coaching engine follow-ups: R1 preserved, three verdicts, the confirmatory test, the DFS read")
    crit = ("1_scenarios", "2_fidelity", "3_level", "4_late_state_wp", "5_pregame")
    allp = dict.fromkeys(crit, True)
    only4 = dict(allp, **{"4_late_state_wp": False})
    two = dict(only4, **{"2_fidelity": False})
    five = dict(allp, **{"5_pregame": False})

    r1 = json.load(open(os.path.join(RG.REG_DIR, "prereg_r1.json")))
    ck("NF0 R1's registration is the one committed at eaca95d, and the engine, tables and code it pins still match",
       r1["block_sha256"] == R1_BLOCK and RG.block_hash(r1) == R1_BLOCK
       and all(RG.sha(rel) == r1["files"][rel] for rel in MINE if rel in r1["files"]), r1["block_sha256"][:8])

    # ---- the three-verdict report
    t = RP.three(_read(only4))
    t2 = RP.three(_read(two))
    t3 = RP.three(_read(allp))
    ck("NF1 the report states the registered verdict first and unchanged, then realism, late-state and pre-game apart",
       t["registered"] == "FAIL" and [g[1] for g in t["groups"]] == ["PASS", "FAIL", "PASS"]
       and t["only_criterion_4_failed"] and RP.text(_read(only4)).startswith("R1 REGISTERED VERDICT: FAIL")
       and t2["registered"] == "FAIL" and [g[1] for g in t2["groups"]] == ["FAIL", "FAIL", "PASS"]
       and not t2["only_criterion_4_failed"] and t3["registered"] == "PASS" and not t3["only_criterion_4_failed"]
       and [k for g in RP.GROUPS for k in g[1]] == list(crit))
    ck("NF1b the report cannot turn a registered FAIL into a pass (it reads the read's own verdict)",
       RP.three(dict(_read(only4), verdict={"pass": False, "criteria": allp}))["registered"] == "FAIL")

    # ---- the confirmatory test's registration and error control
    reg = CF.prereg()
    ec = reg["error_control"]
    c = ec["final_z"]
    ck("NF2 the confirmatory registration matches its own hash and its pinned files, and tests R1's own candidate",
       RG.block_hash(reg) == reg["block_sha256"] and all(RG.sha(rel) == reg["files"][rel] for rel in MINE if rel in reg["files"])
       and reg["r1_registration"] == R1_BLOCK and reg["candidate"] == r1["candidate"]
       and all(reg["files"][rel] == r1["files"][rel] for rel in ("nfl_coach_sim.py", "research/data/nfl_coach_tables.json",
                                                                 "research/nfl_coach_eval.py", "research/nfl_coach_data.py"))
       and reg["settings"]["playouts"] == r1["settings"]["playouts"], reg["block_sha256"][:8])
    a_r1 = 1.0 - RG.t_cdf(2.0, 89)
    ck("NF3 the family-wise one-sided rate is 5%: R1's 2 SE on 89 df plus the confirmatory test's share, and the two "
       "looks' crossing probability under no effect is that share",
       abs(ec["r1_criterion_4_alpha"] - a_r1) < 1e-5 and abs(ec["r1_criterion_4_alpha"] + ec["confirmatory_alpha"] - 0.05) < 1e-9
       and abs(RG.cross_prob(c, 0.5) - ec["confirmatory_alpha"]) < 2e-5 and ec["family_one_sided_alpha"] == 0.05,
       f"{ec['r1_criterion_4_alpha']} + {ec['confirmatory_alpha']}")
    rng = random.Random(7)
    hits, n_mc = 0, 400000
    b1, s = c / math.sqrt(0.5), math.sqrt(0.5)
    for _ in range(n_mc):
        z1 = rng.gauss(0, 1)
        hits += z1 > b1 or (s * z1 + s * rng.gauss(0, 1)) > c
    ck("NF3b a simulation of the two looks under no effect agrees (within 4 SE)",
       abs(hits / n_mc - ec["confirmatory_alpha"]) < 4 * math.sqrt(0.0257 * 0.9743 / n_mc), round(hits / n_mc, 5))
    lk = reg["looks"]
    ck("NF4 two looks on the 2027 regular season, the second final; fresh games only (2027 is sealed to every "
       "caller but a registered read)",
       len(lk) == 2 and lk[1].get("final") and reg["season"] == 2027 and lk[0]["weeks"] == list(range(1, 10))
       and lk[1]["weeks"] == list(range(1, 19)) and abs(lk[0]["z_boundary"] - c / math.sqrt(0.5)) < 1e-3
       and lk[0]["not_before_utc"] < lk[1]["not_before_utc"])
    pw = reg["power"]
    ck("NF5 the stated power is the design's: about 4%, 18% and 29% at the development effects, 80% at 0.014 nats",
       abs(pw["at_development_effects_nats"]["0.0072"] - RG.cross_prob(c, 0.5, 0.0072 / pw["se_at_final_look"], True)) < 2e-3
       and pw["at_development_effects_nats"]["0.0012"] < 0.06 and 0.25 < pw["at_development_effects_nats"]["0.0072"] < 0.33
       and 0.013 < pw["effect_with_80pct_power_nats"] < 0.016
       and abs(RG.cross_prob(c, 0.5, pw["effect_with_80pct_power_nats"] / pw["se_at_final_look"], True) - 0.80) < 0.01,
       pw["at_development_effects_nats"])
    late, early = "2027-11-20T00:00:00Z", "2027-11-01T00:00:00Z"
    ck("NF6 it refuses without R1's read's leave: R1 passed, R1 failed more than criterion 4, R1 failed another one",
       "did not fail criterion 4 alone" in (CF.refusal(1, late, False, reg, _read(allp)) or "")
       and "did not fail criterion 4 alone" in (CF.refusal(1, late, False, reg, _read(two)) or "")
       and "did not fail criterion 4 alone" in (CF.refusal(1, late, False, reg, _read(five)) or "")
       and CF.r1_allows(_read(only4)) and not CF.r1_allows({"verdict": {"criteria": {"4_late_state_wp": False}}}))
    ck("NF7 and before its date, on a dirty tree, for a third look, and for look 2 once look 1 ended the test",
       "before" in (CF.refusal(1, early, False, reg, _read(only4)) or "")
       and "dirty" in (CF.refusal(1, late, True, reg, _read(only4)) or "")
       and CF.refusal(1, late, False, reg, _read(only4)) is None
       and "no third" in (CF.refusal(3, late, False, reg, _read(only4)) or "")
       and "ended the test" in (CF.refusal(2, "2028-02-01T00:00:00Z", False, reg, _read(only4),
                                           {"decision": {"stop": True, "outcome": "x"}}) or "")
       and CF.refusal(2, "2028-02-01T00:00:00Z", False, reg, _read(only4), {"decision": {"stop": False}}) is None)
    se = 0.0072
    d_win = CF.decide(1, -3.0 * se, se, 136, reg)
    d_go = CF.decide(1, -1.0 * se, se, 136, reg)
    d_fut = CF.decide(1, 0.001, se, 136, reg)
    d_end = CF.decide(2, -1.5 * 0.005, 0.005, 272, reg)
    d_win2 = CF.decide(2, -2.1 * 0.005, 0.005, 272, reg)
    ck("NF8 the decision: over the boundary confirms; not ahead at look 1 stops for futility; look 2 always ends it; "
       "the t boundary is wider than the z one",
       d_win["outcome"] == "CONFIRMED" and d_win["stop"] and not d_go["stop"] and d_go["outcome"].startswith("CONTINUE")
       and d_fut["stop"] and "futility" in d_fut["outcome"] and d_end["stop"] and d_end["outcome"].startswith("NOT CONFIRMED")
       and d_win2["outcome"] == "CONFIRMED" and d_go["t_critical"] > lk[0]["z_boundary"]
       and d_end["t_critical"] > lk[1]["z_boundary"] and d_end["gain_upper_95"] > 0.0075,
       f"{d_go['t_critical']} vs z {lk[0]['z_boundary']}; {d_end['t_critical']} vs z {lk[1]['z_boundary']}")

    # ---- the DFS read
    dreg = DR.prereg()
    ck("NF9 the DFS registration matches its own hash, pins this workstream's files as they are, and the production "
       "DFS engine and its harness by sha256",
       RG.block_hash(dreg) == dreg["block_sha256"] and all(RG.sha(rel) == dreg["files"][rel] for rel in MINE
                                                           if rel in dreg["files"])
       and {"nfl_dfs_engine.py", "research/nfl_dfs_engine_eval.py", "research/nfl_coach_dfs.py",
            "research/nfl_coach_dfs_read.py", "nfl_coach_sim.py"} <= set(dreg["files"])
       and dreg["window"]["weeks"] == [13, 14, 15, 16, 17, 18] and dreg["rule"]["looks"] == 1, dreg["block_sha256"][:8])
    import csv
    import gzip
    with gzip.open(os.path.join(ROOT, "research", "data", "player_weeks_2026.csv.gz"), "rt", newline="") as fh:
        wk = {int(r["week"]) for r in csv.DictReader(fh)}
    ck("NF10 no committed file holds a 2026 player-week of the DFS window (the table stops before week 13)",
       wk and max(wk) < 13, sorted(wk))
    ck("NF11 the DFS read refuses before its date and on a dirty tree",
       "before" in (DR.refusal("2026-12-25T00:00:00Z", False, dreg) or "")
       and "dirty" in (DR.refusal("2027-01-08T00:00:00Z", True, dreg) or ""))

    def sc(pr_c, ks_c):
        return {"pair_rmse": {"coach": pr_c, "e": 0.100},
                "calibration": {"coach": {"_pooled": {"pit_ks": ks_c}}, "e": {"_pooled": {"pit_ks": 0.030}}}}

    def pd(m, se_, dst=0.0):
        return {"offence": {"mean_diff": m, "se_game_clustered": se_}, "dst": {"mean_diff": dst, "se_game_clustered": 0.02},
                "k": {"mean_diff": 0.0, "se_game_clustered": 0.02}}
    v1 = DR.verdict(pd(-0.03, 0.012), sc(0.105, 0.035), "e", 96)
    v2 = DR.verdict(pd(-0.02, 0.012), sc(0.105, 0.035), "e", 96)
    v3 = DR.verdict(pd(-0.03, 0.012), sc(0.115, 0.035), "e", 96)
    v4 = DR.verdict(pd(-0.03, 0.012, dst=0.03), sc(0.105, 0.035), "e", 96)
    ck("NF12 the DFS rule: 2.5% one-sided on the paired offence CRPS with every guardrail -> PASS; short of it -> "
       "NOT ESTABLISHED; a guardrail broken -> WORSE",
       v1["outcome"] == "PASS" and v2["outcome"] == "NOT ESTABLISHED" and v3["outcome"] == "WORSE"
       and v4["outcome"] == "WORSE" and abs(v1["t_critical"] - RG.t_ppf(0.975, 95)) < 1e-3,
       f"{v1['outcome']} {v2['outcome']} {v3['outcome']} {v4['outcome']}")

    # ---- the research-only DFS variant leaves production alone
    import nfl_deep_sim as D
    import nfl_dfs_engine as G
    from research import nfl_coach_dfs as CD
    src = open(os.path.join(ROOT, "nfl_dfs_engine.py")).read()
    prod = [f for f in os.listdir(ROOT) if f.endswith(".py") and not f.startswith("nfl_coach")]
    ck("NF13 nfl_dfs_engine.py has no hook for the coaching engine and no production module imports the variant",
       "nfl_coach" not in src and "coach_games" not in src
       and not any("nfl_coach" in open(os.path.join(ROOT, f)).read() for f in prod))
    orig = D.play_game
    lg = D.league_profile()
    T = D.tables()
    a = G.play_worlds(lg, lg, 60, random.Random(5), T, neutral=True)
    with CD.coach_games():
        inside = D.play_game is CD.coach_play_game
        cw = G.play_worlds(lg, lg, 60, random.Random(5), T, neutral=True)
        g1 = D.play_game(lg, lg, random.Random(9), T)
    b = G.play_worlds(lg, lg, 60, random.Random(5), T, neutral=True)
    try:
        with CD.coach_games():
            raise ValueError("x")
    except ValueError:
        pass
    same = all((a[i][k] == b[i][k]).all() for i in (0, 1) for k in a[i])
    diff = any((a[i][k] != cw[i][k]).any() for i in (0, 1) for k in a[i])
    ck("NF14 inside the block the DFS worlds' games are the coaching engine's (same keys, different games, timeouts "
       "in the box); outside it, before and after and after an error, production's worlds are identical",
       inside and D.play_game is orig and same and diff and set(a[0]) == set(cw[0]) and "tmo" in g1["h"]
       and int(cw[0]["pts"].sum()) > 0)
    ct = CD.coach_tables(T)
    ck("NF15 the variant plays on the caller's base tables (the ones the anchor measured on), not the coaching fit's own",
       ct.base is T and CD.coach_tables(T) is ct and ct.gap is __import__("nfl_coach_sim").tables().gap)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def _ck(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    run(_ck)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
