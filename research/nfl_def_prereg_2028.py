"""Writes the 2028 defensive confirmatory FRAMEWORK once (research/data/nfl_coach/prereg_def_2028.json).
The owner (2026-11-27): "Use a separate 2028 confirmatory test for R6 defensive intelligence rather
than expanding the existing 2027 shared-games testing family. Development and exploratory research
can continue beforehand."

Two hashes, in this order: the FRAMEWORK now (window, family, eligibility, statistic form, criteria
form, the freeze deadline); the COMPONENT block (which tendencies, the R7 engine files, the read
script, each component's power) later, written and committed before the 2028 season's first game.
research/reports/nfl_def_persist.md section D is the prose.

    python3 -m research.nfl_def_prereg_2028
"""
import datetime
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_def_2028.json")
FILES = ("research/data/nfl_coach/def_rule.json", "research/nfl_def_persist.py",
         "research/data/nfl_coach/def_persist.json", "research/nfl_def_prereg_2028.py")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def block():
    pers = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", "def_persist.json")))
    return {
        "version": "def-framework-1",
        "written_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "owner_decision": {"date": "2026-11-27", "words": "Use a separate 2028 confirmatory test for R6 defensive "
                           "intelligence rather than expanding the existing 2027 shared-games testing family. "
                           "Development and exploratory research can continue beforehand."},
        "window": {"season": 2028, "season_type": "REG", "weeks": list(range(1, 19)),
                   "not_before_utc": "2029-01-16T06:00:00Z"},
        "family": {"name": "the 2028 defensive family", "members": "the primary criterion of each component frozen "
                   "in the component block, and nothing else", "method": "Holm, family-wise one-sided 0.05",
                   "not": "never a member of the 2027 coaching family (R2-H2, R4, R5, R6)"},
        "eligibility": {
            "1": "the tendency PERSISTED under def_rule.json (def_persist.json, pinned here: "
                 f"{pers['persisting']})",
            "2": "its development decision model passed a rule written before its development numbers (fit "
                 "2022-2024, chosen on 2025, checked on 2026 weeks 1-12 where the data exist)",
            "3": "the component block (prereg_def_2028_components.json) is written, hashed, committed and added to "
                 "the registry before 2028-09-01T00:00:00Z, naming each component, the R7 engine files (new files: "
                 "nothing enters R1-R6), the read script and each component's power; with no such block by then, "
                 "no confirmatory read happens"},
        "statistic_form": "per component, the held-out log score of the defence's decision (or of its measured "
                          "consequence) under R7 minus under the same season-less league table, per team-game, SE "
                          "clustered by game; the tables frozen in the component block, nothing refitted on 2028",
        "criteria_form": {"1": "R7's guards exit 0", "2": "each component's gain > its Holm step (one-sided, the "
                          "family above)", "3": "no component below -1 SE"},
        "hashed_now_vs_later": "this framework now (before any defensive model exists); the component block when the "
                               "components are chosen, which can only be after the decision-model development",
        "files": {rel: _sha(rel) for rel in FILES if rel != "research/nfl_def_prereg_2028.py"},
        "writer_sha256": _sha("research/nfl_def_prereg_2028.py"),
    }


def write():
    if os.path.exists(OUT):
        raise SystemExit("the framework exists; it is written once")
    b = block()
    body = json.dumps(b, sort_keys=True, separators=(",", ":"))
    b["block_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    with open(OUT, "w") as fh:
        json.dump(b, fh, indent=1, sort_keys=True)
    return b


if __name__ == "__main__":
    print(write()["block_sha256"])
    sys.exit(0)
