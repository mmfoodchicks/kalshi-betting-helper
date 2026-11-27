"""Guards for the coaching-evolution study (research/nfl_coach_evo.py,
research/reports/nfl_coach_evo.md): the rule committed before the scores and
unchanged since, the candidates' arithmetic (weights, shrinkage, the random walk's
Kalman filter), the decision rule as written, the seal, and nothing built into
R1-R5. Self-registered (tests/guards_auto_*.py, the AUTO section); also runs
alone: python3 tests/guards_auto_nfl_coach_evo.py. Hermetic.
"""
import hashlib
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# research/data/nfl_coach/evo_rule.json as committed in ae6e018, before any score was computed
RULE_SHA = "7f139636e0ec3f3ff4b071d98df3af53d12a463113a172270e71cef00be5c8d2"


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(ck):
    from research import nfl_coach_evo as EVO

    print("  -- EVO. Coaching evolution: the rule first, the candidates' arithmetic, the decision, the seal")
    out = json.load(open(EVO.OUT))
    ck("EVO-1 the rule is the one committed before any score (sha256 pinned) and the results were computed "
       "under it", _sha("research/data/nfl_coach/evo_rule.json") == RULE_SHA == out["rule_sha256"])
    w3 = EVO.weights_for("last3", 2024)
    we = EVO.weights_for("exp", 2024, 2.0)
    ck("EVO-2 the windows: last3 is the three seasons before S, last5 five, full every one from 2016; the "
       "exponential weight is 1 at S-1 and halves every h seasons",
       sorted(w3) == [2021, 2022, 2023] and len(EVO.weights_for("last5", 2024)) == 5
       and min(EVO.weights_for("full", 2024)) == 2016 and abs(we[2023] - 1.0) < 1e-12
       and abs(we[2021] - 0.5) < 1e-12)
    cs = {2016: {"a": [3, 10], "b": [0, 0]}, 2017: {"a": [5, 10]}}
    r, p0 = EVO._rates(cs, {2016: 1.0, 2017: 1.0})
    ck("EVO-3 a cell's rate is (successes + 20 x pooled) / (trials + 20): 8 of 20 at a pooled 0.4 reads 0.4; an "
       "empty cell the pooled rate", abs(p0 - 0.4) < 1e-12 and abs(r["a"] - 0.4) < 1e-12 and abs(r["b"] - 0.4) < 1e-12)
    obs = [(0.1, 0.01), (0.3, 0.01), (0.2, 0.04)]
    m0, _P, _ll = EVO.kalman(obs, 0.0)
    wmean = sum(d / v for d, v in obs) / sum(1 / v for _d, v in obs)
    m1, _P1, _ll1 = EVO.kalman([(0.1, 1e-8), (0.5, 1e-8)], 1.0)
    ck("EVO-4 the random walk: with no innovation the forecast is the precision-weighted mean of the seasons' "
       "offsets; with exact offsets and a wide walk it is the last season's",
       abs(m0 - wmean) < 1e-9 and abs(m1 - 0.5) < 1e-6)
    per = {i: 1.0 + (0.5 if i % 2 else -0.5) for i in range(200)}
    folds_ok = {S: 0.1 for S in EVO.FOLDS}
    folds_few = {S: (-0.1 if k < 4 else 0.1) for k, S in enumerate(EVO.FOLDS)}
    good = {"gains": {c: (per, folds_ok) for c in EVO.CANDS[1:]}}
    few = {"gains": {c: (per, folds_few) for c in EVO.CANDS[1:]}}
    neg = {"gains": {c: ({i: -0.2 + (0.05 if i % 2 else -0.05) for i in range(200)}, folds_ok)
                     for c in EVO.CANDS[1:]}}
    d1 = EVO.decide({"A": good, "B": good})
    d2 = EVO.decide({"A": few})
    d3 = EVO.decide({"A": good, "B": neg})
    ck("EVO-5 the decision as written: a table drifts when a candidate gains > 2 SE AND is positive in 5+ of the "
       "8 folds (4 of 8 is not enough); the scheme needs the summed gain > 2 SE and no table below -1 SE",
       d1["tables"]["A"]["drifts"] and d1["scheme"] != "full" and not d2["tables"]["A"]["drifts"]
       and d3["scheme"] == "full" and not d3["pooled"]["rw"]["no_table_below_minus_1se"])
    d = out["decision"]
    ck("EVO-6 the result under the rule: the clock runoff, the backs' target share and the pull hazard drift; "
       "the call and the no-huddle tables do not; no single scheme qualifies (the random walk's pooled gain is "
       "over 2 SE but its no-huddle loss is below -1 SE), so full history stays",
       d["tables"]["T3_clock"]["drifts"] and d["tables"]["T4_rb_target"]["drifts"] and d["tables"]["T5_pull"]["drifts"]
       and not d["tables"]["T1_call"]["drifts"] and not d["tables"]["T2_nohuddle"]["drifts"] and d["scheme"] == "full"
       and d["pooled"]["rw"]["z"] > 2 and not d["pooled"]["rw"]["no_table_below_minus_1se"]
       and d["tables"]["T2_nohuddle"]["rw"]["z"] < -1)
    src = open(os.path.join(ROOT, "research", "nfl_coach_evo.py")).read()
    ck("EVO-7 the seal: the study reads through R2's sealed loader (never an unseal token), its last fold is "
       "2026 (weeks 1-12, the seal's limit)", "unseal" not in src and "_UNSEAL" not in src
       and max(EVO.FOLDS) == 2026 and max(EVO.SEASONS) == 2026)
    moved = []
    for reg in ("prereg_r1.json", "prereg_r2.json", "prereg_r3.json", "prereg_r4.json", "prereg_r5.json",
                "prereg_r5_2027.json"):
        r_ = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", reg)))
        moved += [rel for rel, h in r_["files"].items() if _sha(rel) != h]
    ck("EVO-8 nothing is built into R1-R5: every file their registrations (and R5's amendment) pin is "
       "byte-identical", not moved, moved)
    hl = out["detail"]["T3_clock"]["half_life"]
    ck("EVO-9 the half-life is descriptive and defined as the fixed h with the lowest pooled walk-forward loss "
       "(full history = inf): the clock's is 1 season, the call's inf",
       hl["best_fixed_h"] == "1.0" and out["detail"]["T1_call"]["half_life"]["best_fixed_h"] == "inf"
       and math.isclose(hl["loss_minus_full"]["inf"], 0.0))


if __name__ == "__main__":
    PASS, FAIL = [], []

    def _ck(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail and not cond else ""))
    run(_ck)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
