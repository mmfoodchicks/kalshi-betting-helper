"""Guards for defensive coaching, step 1 (research/nfl_def_persist.py,
research/reports/nfl_def_persist.md): the rule committed before the numbers and
unchanged, the residuals against the same season's league cells, the permutation
and Holm arithmetic, the seal, the separate 2028 framework, and nothing in R1-R6.
Self-registered (tests/guards_auto_*.py, the AUTO section); also runs alone:
python3 tests/guards_auto_nfl_def.py. Hermetic.
"""
import hashlib
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# research/data/nfl_coach/def_rule.json as committed in 70278a5, before any number was computed
RULE_SHA = "7ea3fbff816f56d2970b1c714a0fa454d6d224f0e270c16c4965404f627f62dc"


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(ck):
    from research import nfl_def_persist as DP

    print("  -- DEF. Defensive coaching step 1: the rule first, residuals, permutations, Holm, the seal, the "
          "2028 framework")
    out = json.load(open(DP.OUT))
    ck("DEF-1 the rule is the one committed before any number (sha256 pinned) and the results were computed under "
       "it", _sha("research/data/nfl_coach/def_rule.json") == RULE_SHA == out["rule_sha256"])
    a = DP.Acc()
    for s, c, y, k in ((2022, "x", 1.0, "A"), (2022, "x", 0.0, "B"), (2022, "y", 3.0, "A"), (2022, "y", 5.0, "B"),
                       (2023, "x", 1.0, "A"), (2023, "x", 1.0, "B")):
        a.add(s, c, y, k)
    tg = a.team_games()
    ck("DEF-2 residuals are against the same season's league mean in the cell: they sum to 0 over every season "
       "and cell, and a team's value is its own deviation (A: +0.5 and -1.0 in 2022, 0 in 2023)",
       abs(tg["A"][0] - (0.5 - 1.0 + 0.0)) < 1e-12 and abs(tg["B"][0] - (-0.5 + 1.0 + 0.0)) < 1e-12
       and tg["A"][1] == 3)
    rng = random.Random(1)
    same = {(2022 + i % 3, f"T{i}", w): (float(i % 9) + (0.01 * w), 1.0) for i in range(60) for w in range(1, 9)}
    noise = {(2022 + i % 3, f"T{i}", w): (random.Random(i * 100 + w).gauss(0, 1), 1.0)
             for i in range(60) for w in range(1, 9)}
    sh1, sh0 = DP.split_half(same, rng), DP.split_half(noise, rng)
    ck("DEF-3 the split-half permutation test: a pure team trait reads r ~1 at the smallest p; pure noise reads "
       "r ~0 and an unremarkable p", sh1["r"] > 0.99 and sh1["p_one_sided"] < 0.001 and abs(sh0["r"]) < 0.3
       and sh0["p_one_sided"] > 0.01)
    h = DP.holm({"a": 0.001, "b": 0.02, "c": 0.04})
    h2 = DP.holm({"a": 0.001, "b": 0.03, "c": 0.04})
    ck("DEF-4 Holm step-down at 0.05: 0.001 <= 0.05/3, 0.02 <= 0.05/2, 0.04 <= 0.05 all reject; with 0.03 at step "
       "two the walk stops there", h == {"a": True, "b": True, "c": True} and h2 == {"a": True, "b": False, "c": False})
    src = open(os.path.join(ROOT, "research", "nfl_def_persist.py")).read()
    ck("DEF-5 the seal: the study reads R2's sealed loader and checks R1's seal on every raw row; no unseal token; "
       "no FTN season past 2025", "unseal" not in src and "_UNSEAL" not in src and "CD.sealed(" in src
       and max(DP.FTN_SEASONS) == 2025 and max(DP.PBP_SEASONS) == 2026)
    d = out["tendencies"]
    ck("DEF-6 the decision as written: a tendency persists only with a Holm-significant split-half AND a "
       "year-over-year p < 0.05 (halftime also above its Q1 -> Q2 placebo); the list matches",
       out["persisting"] == [t for t in DP.TENDENCIES if d[t]["holm"] and d[t]["year_over_year"]["all"].get(
           "p_one_sided", 1) < 0.05 and (t != "halftime" or d[t]["split_half"]["r"]
                                         > out["halftime_placebo_q1_q2"]["r"])])
    moved = []
    for reg in ("prereg_r1.json", "prereg_r2.json", "prereg_r3.json", "prereg_r4.json", "prereg_r5.json",
                "prereg_r5_2027.json", "prereg_r6.json"):
        # option (c), 2026-11-28 (research/registered_pins.py): each registration's pins are checked at its
        # registration commit, where its read runs; live, only the named exceptions may differ (a staged engine
        # flip behind the deep-weight read's gate, the current coaching guard files)
        from research import registered_pins as REGP
        moved += REGP.moved("research/data/nfl_coach/" + reg)
    ck("DEF-7 [option (c): pins checked at the registration commit; live, only research/registered_pins.py's "
       "named exceptions] nothing enters R1-R6: every file their registrations pin is byte-identical", not moved, moved)
    fw = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_def_2028.json")))
    body = json.dumps({k: v for k, v in fw.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    fam27 = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", "family_2027_r6.json")))
    ck("DEF-8 the 2028 framework: its hash holds, its files match, the 2028 regular season, its own Holm family "
       "(no defensive member in the 2027 family), components frozen before 2028-09-01",
       hashlib.sha256(body.encode()).hexdigest() == fw["block_sha256"]
       and all(_sha(rel) == hh for rel, hh in fw["files"].items())
       and fw["window"]["season"] == 2028 and "2028-09-01" in fw["eligibility"]["3"]
       and not any("def" in t.lower() for t in fam27["family"]["tests"]))
    regy = json.load(open(os.path.join(ROOT, "research", "data", "research_registry.json")))
    ck("DEF-9 the framework is on the Research tab's registry",
       "research/data/nfl_coach/prereg_def_2028.json" in {e["registration"] for e in regy})


if __name__ == "__main__":
    PASS, FAIL = [], []

    def _ck(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail and not cond else ""))
    run(_ck)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
