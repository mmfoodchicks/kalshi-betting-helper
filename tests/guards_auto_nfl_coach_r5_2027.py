"""Guards for R5's owner-approved 2027 amendment (research/data/nfl_coach/prereg_r5_2027.json):
the original registration untouched, the criteria copied verbatim, the implementation
frozen by its pins, the read's order and refusals, and the 2027 family's multiplicity
arithmetic. Self-registered (tests/guards_auto_*.py, the AUTO section); also runs
alone: python3 tests/guards_auto_nfl_coach_r5_2027.py. Hermetic.
"""
import hashlib
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# prereg_r5.json as committed in 79f2bee, before the amendment existed
ORIGINAL_SHA_BLOCK = "d20d0e137cadebdd1134d9fd9bbdcb676971c0ca3c187a7e2c717d2766b1a65b"


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(ck):
    from research import nfl_coach_family_2027 as FM
    from research import nfl_coach_r5_read_2027 as RA

    print("  -- N5A. R5's 2027 amendment: the original kept, the criteria verbatim, the pins, the order, the family")
    orig = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r5.json")))
    am = RA.prereg()
    body = json.dumps({k: v for k, v in orig.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    ck("N5A-1 prereg_r5.json (the 2028 read) is unchanged: its block hash is the one committed before the "
       "amendment, and its file matches the hash the amendment recorded",
       orig["block_sha256"] == ORIGINAL_SHA_BLOCK == am["amends"]["block_sha256"]
       and hashlib.sha256(body.encode()).hexdigest() == ORIGINAL_SHA_BLOCK
       and _sha("research/data/nfl_coach/prereg_r5.json") == am["amends"]["file_sha256"])
    body = json.dumps({k: v for k, v in am.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    same = all(am[k] == orig[k] for k in ("criteria", "statistics", "secondary", "min_strategic_pulled_team_games",
                                          "settings", "requires_reads", "version"))
    ck("N5A-2 the amendment's own hash holds; the 2027 regular season, on/after 2028-01-13 after R1's and R4's "
       "reads; criteria, statistics, the secondary and the floor are prereg_r5.json's verbatim; the 2028 read is "
       "named a replication",
       hashlib.sha256(body.encode()).hexdigest() == am["block_sha256"] and same
       and am["window"] == [{"season": 2027, "weeks": list(range(1, 19))}]
       and am["not_before_utc"] == "2028-01-13T06:00:00Z" and am["requires_reads"] == ["R1", "R4"]
       and "REPLICATION" in am["read_order"] and "replication" in am["replication"])
    moved = [rel for rel, h in am["files"].items() if _sha(rel) != h]
    ck("N5A-3 the implementation is frozen: every R5 file prereg_r5.json pins, plus the wrapper, this guard and "
       "the family analysis, matches its sha256", not moved and set(orig["files"]) <= set(am["files"])
       and "tests/guards_auto_nfl_coach_r5_2027.py" in am["files"], moved)
    ok = {"R1": 1, "R4": 1}
    w = [RA.refusal(now_utc="2027-12-01T00:00:00Z", dirty=False, reg=am, earlier_read=ok, already=False),
         RA.refusal(now_utc="2028-02-01T00:00:00Z", dirty=True, reg=am, earlier_read=ok, already=False),
         RA.refusal(now_utc="2028-02-01T00:00:00Z", dirty=False, reg=am, earlier_read={"R1": 1}, already=False),
         RA.refusal(now_utc="2028-02-01T00:00:00Z", dirty=False, reg=am, earlier_read=ok, already=True),
         RA.refusal(now_utc="2028-02-01T00:00:00Z", dirty=False, reg=dict(am, amends=dict(am["amends"],
                                                                                         file_sha256="0" * 64)),
                    earlier_read=ok, already=False),
         RA.refusal(now_utc="2028-02-01T00:00:00Z", dirty=False, reg=am, earlier_read=ok, already=False)]
    from research import nfl_coach_r5_read as RR
    ck("N5A-4 the 2027 read refuses before its date, a dirty tree, before R4's read, a second read and a changed "
       "original; would run after; and leaves the original read's paths as they were",
       "before" in (w[0] or "") and "dirty" in (w[1] or "") and "R4" in (w[2] or "") and "already" in (w[3] or "")
       and "original" in (w[4] or "") and w[5] is None and RR.PREREG.endswith("prereg_r5.json")
       and RR.OUT.endswith("read_r5.json"), w)
    th = FM.holm(0.05, 3)
    fam = json.load(open(FM.OUT))
    a = {i: float(i % 7) for i in range(40)}
    b = {i: float(i % 7) for i in range(20, 60)}
    mc = FM.mean_corr(a, b)
    ck("N5A-5 the family arithmetic: Holm at a family-wise one-sided 0.05 over three tests steps at z 2.128, "
       "1.960, 1.645; the correlation of two means over nested units is n_shared / sqrt(nA nB) x rho (identical "
       "statistics on 20 of 40 + 40 units: 0.5); the amendment carries the family file's thresholds",
       [round(x, 3) for x in th] == [2.128, 1.96, 1.645] and abs(mc["corr_of_means"] - 0.5) < 1e-9
       and am["multiplicity"]["proposed_2027_family"]["z_thresholds"] == fam["family"]["holm_z_thresholds"]
       and len(fam["family"]["tests"]) == 3)
    pw = fam["family"]["power"]["R5 (2027, ~45)"]
    ck("N5A-6 power as stated: R5 at ~45 strategic pulls, Phi(effect/SE - z) at z 2 and Holm's first step",
       abs(pw["own_rule_z2"] - 0.5 * math.erfc(-(1.34 / (2.68 / math.sqrt(45)) - 2) / math.sqrt(2))) < 1e-4
       and pw["holm_first_step"] < pw["own_rule_z2"])
    regy = json.load(open(os.path.join(ROOT, "research", "data", "research_registry.json")))
    listed = {e["registration"] for e in regy}
    ck("N5A-7 the amendment and the original are both on the Research tab's registry",
       "research/data/nfl_coach/prereg_r5_2027.json" in listed and "research/data/nfl_coach/prereg_r5.json" in listed)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def _ck(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail and not cond else ""))
    run(_ck)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
