"""Writes R6's registration ONCE (research/data/nfl_coach/prereg_r6.json), before any
2027 game is played. research/reports/nfl_coach_r6.md section D is the prose.

    python3 -m research.nfl_coach_r6_prereg
"""
import datetime
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r6.json")
FILES = ("nfl_coach_sim_r6.py", "research/data/nfl_coach_r6_tables.json", "research/nfl_coach_r6_fit.py",
         "research/nfl_coach_r6_read.py", "tests/guards_auto_nfl_coach_r6.py", "research/nfl_coach_evo.py",
         "research/data/nfl_coach/evo_rule.json", "research/data/nfl_coach/evo.json",
         "research/nfl_coach_family_2027_r6.py", "research/data/nfl_coach/family_2027_r6.json",
         "research/data/nfl_coach/r6_fit.json",
         "nfl_coach_sim_r5.py", "research/data/nfl_coach_r5_tables.json", "nfl_coach_sim_r4.py",
         "research/data/nfl_coach_r4_tables.json", "nfl_coach_sim_r3.py", "research/data/nfl_coach_r3_tables.json",
         "research/nfl_coach_r3_qb.py", "nfl_coach_sim_r2.py", "research/data/nfl_coach_r2_tables.json",
         "research/nfl_coach_r2_data.py", "nfl_coach_sim.py", "research/data/nfl_coach_tables.json",
         "research/nfl_coach_data.py", "nfl_deep_sim.py", "nfl_pbp.py")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def block():
    fit = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", "r6_fit.json")))
    fam = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", "family_2027_r6.json")))
    pw, fp = fit["power"], fam["family"]["power"]
    return {
        "version": "coach-r6.0",
        "written_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "owner_approval": {"date": "2026-11-27", "words": "Approved: build R6 using the selectively drifting training "
                           "architecture. Clock management, running back target share and quarterback substitutions use "
                           "the supported drifting-league methods. Play calling and no-huddle retain full-history "
                           "training. Preserve the rejected all-table drift experiment and its original verdict. Use the "
                           "2027 regular season for primary prospective validation and 2028 for independent "
                           "replication. Include R6 in the shared 2027 Holm correction framework, with updated power "
                           "calculations and a clearly defined hypothesis family. Freeze the implementation, component "
                           "selection and acceptance criteria before examining prospective outcomes. Keep R1-R5 and "
                           "their registrations untouched."},
        "what": "R5 with three components moved by a league tendency following a season-level random walk, forecast "
                "for 2027 from 2016 to 2026 week 12: the clock (R1's pace hook), the backs' target odds (R2's deal) and "
                "the pull (R5's hazard's intercept); the call and no-huddle tables R1's, full history",
        "components_frozen": {"drifting": ["clock", "rb_target", "pull"], "full_history": ["call", "no_huddle"],
                              "source": "research/reports/nfl_coach_evo.md (the per-table drift result); the "
                                        "all-table scheme the evo rule tested was REJECTED there (scheme 'full'), and "
                                        "that verdict stands: evo_rule.json and evo.json are pinned here unchanged"},
        "windows": {"primary": {"season": 2027, "weeks": list(range(1, 19)), "not_before_utc": "2028-01-13T06:00:00Z",
                                "requires_reads": ["R1"]},
                    "replication": {"season": 2028, "weeks": list(range(1, 19)),
                                    "not_before_utc": "2029-01-16T06:00:00Z", "requires_reads": ["R6-primary"]}},
        "min_games": 200,
        "statistics": {
            "per_game": "nfl_coach_r6_fit.window_gains at the FROZEN tables (nothing refitted, the replication "
                        "included): per game, each component's held-out log score with its 2027 forecast offset minus "
                        "without it (clock: a Gaussian on the log seconds between a run and the next snap in R1's "
                        "unhurried phases against the full-history cell means; backs' targets: the evo T4 cell "
                        "rates; pull: R5's hazard), and the pooled sum; positive = R6 better",
            "se": "over games (clustered by game)",
            "call_and_no_huddle": "R6 plays R1's call and no-huddle tables unchanged, so their difference from R5 is "
                                  "identically 0 (guard N6-1): no table of the five can be worse there"},
        "criteria": {"1": "tests/guards_auto_nfl_coach_r6.py exits 0",
                     "2": "the pooled gain > 2 SE",
                     "3": "each drifting component's gain > -1 SE"},
        "family": {"definition": fam["family"]["definition"], "tests": fam["family"]["tests"],
                   "method": "Holm, family-wise one-sided 0.05; every original verdict stands under its own rule and "
                             "the family statement is reported beside it", "z_thresholds": fam["family"]["holm_z_thresholds"],
                   "r5_amendment": "R5's own three-test analysis (family_2027.json, pinned by prereg_r5_2027.json) stands "
                                   "as registered; this four-test family is the one reported once R6 exists"},
        "power": (f"~272 games. Criterion 2: {fp['R6 (dev mean, pull excluded)']['own_rule_z2']:.2f} at the development "
                  f"mean (walk-forward 2019-2026, the pull's share dropped since its 2027 shift against R5 is ~0), "
                  f"{fp['R6 (2025-26 level)']['own_rule_z2']:.2f} at the 2025-26 level; at Holm's strictest step "
                  f"(z {fam['family']['holm_z_thresholds'][0]}) {fp['R6 (dev mean, pull excluded)']['holm_first_step']:.2f} "
                  f"and {fp['R6 (2025-26 level)']['holm_first_step']:.2f}. Criterion 3: the clock and the backs hold "
                  f"> -1 SE with ~{pw['criterion_3_each_component_holds']['clock']:.2f} and "
                  f"~{pw['criterion_3_each_component_holds']['rb']:.2f}; the pull's forecast shift against R5 is "
                  f"+0.0095 logit, so its gain is ~0 and its guardrail holds ~0.84 by chance alone. Jointly ~0.65 at "
                  f"the development mean, ~0.79 at the 2025-26 level. Power is the chance the test passes IF R6 is "
                  f"better by the stated amount, never the probability that it is."),
        "dependence": "2027 is shared with R2-H2, R4, R5's amendment, C4 (weeks 1-9) and C3-N; R6's games are every "
                      "regular-season game, so every other 2027 read's units sit inside its games (the correlations "
                      "measured on development games are in family_2027_r6.json). R6 is built on R5's hazard and "
                      "second unit and R1's engine: a defect there is shared. The evo study read 2019-2026 week 12, so "
                      "no development figure is a test. The 2028 replication shares no game with any 2027 read.",
        "files": {rel: _sha(rel) for rel in FILES},
    }


def write():
    if os.path.exists(OUT):
        raise SystemExit("the registration exists; it is written once")
    b = block()
    body = json.dumps(b, sort_keys=True, separators=(",", ":"))
    b["block_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    with open(OUT, "w") as fh:
        json.dump(b, fh, indent=1, sort_keys=True)
    return b


if __name__ == "__main__":
    print(write()["block_sha256"])
    sys.exit(0)
