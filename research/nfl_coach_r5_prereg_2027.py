"""Writes R5's owner-approved 2027 amendment ONCE (research/data/nfl_coach/prereg_r5_2027.json),
before any 2027 game is played. prereg_r5.json is not touched. research/reports/nfl_coach_r5.md
section G and research/reports/nfl_coach_family_2027.md are the prose.

    python3 -m research.nfl_coach_r5_prereg_2027
"""
import datetime
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r5_2027.json")
ORIGINAL = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r5.json")
FAMILY = os.path.join(ROOT, "research", "data", "nfl_coach", "family_2027.json")
EXTRA = ("research/nfl_coach_r5_read_2027.py", "tests/guards_auto_nfl_coach_r5_2027.py",
         "research/nfl_coach_family_2027.py", "research/data/nfl_coach/family_2027.json")
COPIED = ("min_strategic_pulled_team_games", "settings", "statistics", "criteria", "secondary", "requires_reads",
          "version", "what")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def block():
    orig = json.load(open(ORIGINAL))
    fam = json.load(open(FAMILY))
    b = {k: orig[k] for k in COPIED}
    pw = fam["family"]["power"]
    b.update({
        "written_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "amends": {"file": "research/data/nfl_coach/prereg_r5.json", "file_sha256": _sha(
            "research/data/nfl_coach/prereg_r5.json"), "block_sha256": orig["block_sha256"]},
        "owner_approval": {"date": "2026-11-27", "words": "Allow R5 to use the 2027 regular season rather than "
                           "waiting until January 2029. Freeze its implementation and acceptance criteria now. "
                           "Account explicitly for dependence and multiple comparisons across R1-R5. Preserve all "
                           "original registrations and verdicts. Do not modify R5 based on the shared evaluation "
                           "games."},
        "window": [{"season": 2027, "weeks": list(range(1, 19))}],
        "not_before_utc": "2028-01-13T06:00:00Z",
        "read_order": "R1's read (on/after 2027-01-06) first; then R2-H2's and R4's (both on/after 2028-01-13, either "
                      "order); then this read, which refuses until R1's and R4's results are committed; prereg_r5.json's "
                      "2028 read (on/after 2029-01-16) last, as a REPLICATION on games no 2027 read sees",
        "criteria_unchanged": "criteria, statistics, the secondary and the 30-game floor are prereg_r5.json's, copied "
                              "verbatim; the implementation is frozen by the pins below (R5's files as prereg_r5.json "
                              "pins them, plus this amendment's read wrapper, guard and family analysis)",
        "frozen": "no R5 file may change on the strength of any 2027 game; a change is a new version with its own "
                  "registration on games no read has seen",
        "replication": "prereg_r5.json stays byte-identical and its 2028 read stands as a later, independent "
                       "replication: a 2027 PASS is the primary result, the 2028 read its confirmation, and each "
                       "verdict is reported as its own rule gives it",
        "power": f"2027 should hold ~45 strategic pulled team-games (424 in 2016-2024; 57 in 2025): criterion 2's "
                 f"power ~{pw['R5 (2027, ~45)']['own_rule_z2']:.2f} at the development effect (+1.34, SD 2.68) under "
                 f"its own z > 2, ~{pw['R5 (2027, ~45)']['holm_first_step']:.2f} at the 2027 family's strictest Holm "
                 f"step (z 2.128); at the 30-game floor ~{pw['R5 (2027 floor, 30)']['own_rule_z2']:.2f} and "
                 f"~{pw['R5 (2027 floor, 30)']['holm_first_step']:.2f}. Criterion 3 fails ~2.3% by chance; the "
                 f"corner secondary as in prereg_r5.json. Power is the chance the test passes IF R5 is better by the "
                 f"stated amount, never the probability that it is.",
        "multiplicity": {
            "reads_on_2026_weeks_13_18": ["R1 (criteria 1-4)", "the coaching DFS read", "R2-H1", "R3",
                                          "R4 (with 2027)"],
            "reads_on_2027": ["R2-H2 (the regular season)", "R4 (with 2026 weeks 13-18)",
                              "C4 (weeks 1-9, run only if R1 fails criterion 4 alone)", "R5 (this amendment)",
                              "C3-N (not coaching: the deep engine's season scale, the same games)"],
            "owner_approved_family": "R1's criterion 4 and C4 only, at a family-wise one-sided 5% over every "
                                     "scheduled look and C4's conditional eligibility (prereg_c4.json); no other "
                                     "read is in it",
            "proposed_2027_family": {"tests": fam["family"]["tests"], "method": "Holm, family-wise one-sided 0.05",
                                     "z_thresholds": fam["family"]["holm_z_thresholds"],
                                     "rule": "every original verdict stands as its own rule gives it (each at z > 2, "
                                             "one-sided 0.0228); the family statement is reported BESIDE them: a test "
                                             "is family-confirmed when Holm's step-down at these thresholds rejects "
                                             "it, ordered by z",
                                     "uncorrected_familywise_if_independent": fam["family"][
                                         "uncorrected_familywise_if_independent"]},
            "shared_games": "the 2027 tests read the same games: R5's units are a subset of R4's (every strategic "
                            "pulled team-game is a pulled one) and both sit inside R2-H2's team-games. On the "
                            "development seasons the correlation of the tests' MEANS (n_shared / sqrt(nA nB) x the "
                            "per-unit correlation) was R4 x R5 +0.22 (2025) / +0.08 (2026), R2-H2 x R4 -0.10 / "
                            "+0.05, R2-H2 x R5 +0.02 / -0.05 (family_2027.json): small, positive where the units "
                            "nest. Holm holds under any dependence. A pass on shared games is weaker evidence than "
                            "the same pass on independent games: joint passes are not independent confirmations, "
                            "and a defect common to R3's pull or R4's labels moves R4 and R5 together."},
        "files": {rel: _sha(rel) for rel in list(orig["files"]) + list(EXTRA)},
    })
    return b


def write():
    if os.path.exists(OUT):
        raise SystemExit("the amendment exists; it is written once")
    b = block()
    body = json.dumps(b, sort_keys=True, separators=(",", ":"))
    b["block_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    with open(OUT, "w") as fh:
        json.dump(b, fh, indent=1, sort_keys=True)
    return b


if __name__ == "__main__":
    print(write()["block_sha256"])
    sys.exit(0)
