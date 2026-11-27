"""Writes R5's registration ONCE (research/data/nfl_coach/prereg_r5.json), before
any test game is played. research/reports/nfl_coach_r5.md section D is the prose.

    python3 -m research.nfl_coach_r5_prereg "<power statement>"
"""
import datetime
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r5.json")
FILES = ("nfl_coach_sim_r5.py", "research/data/nfl_coach_r5_tables.json", "research/nfl_coach_r5_fit.py",
         "research/nfl_coach_r5_eval.py", "research/nfl_coach_r5_read.py", "tests/guards_auto_nfl_coach_r5.py",
         "nfl_coach_sim_r4.py", "research/data/nfl_coach_r4_tables.json", "research/nfl_coach_r4_data.py",
         "research/nfl_coach_r4_evidence.py", "research/nfl_coach_r4_fit.py",
         "nfl_coach_sim_r3.py", "research/data/nfl_coach_r3_tables.json", "research/nfl_coach_r3_qb.py",
         "nfl_coach_sim_r2.py", "research/data/nfl_coach_r2_tables.json", "research/nfl_coach_r2_data.py",
         "nfl_coach_sim.py", "research/data/nfl_coach_tables.json", "research/nfl_coach_data.py",
         "nfl_deep_sim.py", "nfl_pbp.py")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def block(power):
    return {
        "version": "coach-r5.0",
        "written_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "what": "the second unit after a STRATEGIC quarterback pull as drawn per-player decisions (the lead back "
                "sits with q_b, each of the top two targets with q_w given the back's decision, a player who plays "
                "at his season share shifted by d) against R4's per-snap odds; R3's hazard form refitted on "
                "2016-2025 (every richer form was refused) as a guardrail; the trailing corner R3 missed as a "
                "registered secondary",
        "window": [{"season": 2028, "weeks": list(range(1, 19))}],
        "not_before_utc": "2029-01-16T06:00:00Z",
        "requires_reads": ["R1", "R4"],
        "min_strategic_pulled_team_games": 30,
        "settings": {"files": "play_by_play_2028.csv.gz, players.csv, injuries_2028.csv in the read's directory "
                              "(nflverse, fresh); the kind of pull is R4's classifier (descriptions, the next week's "
                              "report; week 18's from the descriptions alone)",
                     "simulation": "none: every statistic is a likelihood of what happened, at the frozen tables"},
        "statistics": {
            "unit": "nfl_coach_r5_eval.unit_score over every strategic pulled team-game in the window "
                    "(nfl_coach_r5_fit.unit_rows): log-likelihood of the lead back's and the top two targets' "
                    "touches after the pull, R5 minus R4 (positive = R5 better)",
            "hazard": "nfl_coach_r5_eval.hazard_score over every team-game: the pull's log loss, R4's (R3's "
                      "hazard) minus R5's, per team-game (positive = R5 better)",
            "cell": "nfl_coach_r5_fit.cell (trailing 17-27 at the drive's first snap, 2:01-8:00 left): real "
                    "pulls against R4's and R5's expected"},
        "criteria": {"1": "tests/guards_auto_nfl_coach_r5.py exits 0",
                     "2": "unit gain > 2 SE (SE over strategic pulled team-games)",
                     "3": "hazard gain > -2 SE (a guardrail)"},
        "secondary": "reported, never part of the verdict: the trailing corner's real pulls against R5's "
                     "expectation, one-sided Poisson P(X >= real) < 0.05 = the post-2021 excess persists on "
                     "fresh games (the evidence a corner-specific term would need)",
        "power": power,
        "dependence": "2026 weeks 13-18 are R1's, R2-H1's, R3's and R4's registered games and 2027 is R4's, so R5 "
                      "takes the 2028 regular season, which no registered window and no development set has seen "
                      "(C3-N reads 2027 for the deep engine; nothing coaching reads 2028). R5 is R4 with two "
                      "changes; its unit statistic conditions on the real pulls, so it does not depend on R4's "
                      "verdict, but both are built on R3's pull and R4's labels: a defect there is shared. R5 was "
                      "fitted on 2016-2024 (second unit) and 2016-2025 (hazard); 2025 and 2026 weeks 1-12 were "
                      "read in development, including the season-by-season corner that motivated the regime "
                      "candidates, so no development figure is a test. Any change to an R5 file or to an R1-R4 "
                      "file it plays on fails the pinned hashes and moves R5 to fresh games under a new "
                      "registration.",
        "files": {rel: _sha(rel) for rel in FILES},
    }


def write(power):
    if os.path.exists(OUT):
        raise SystemExit("the registration exists; it is written once")
    b = block(power)
    body = json.dumps(b, sort_keys=True, separators=(",", ":"))
    b["block_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    with open(OUT, "w") as fh:
        json.dump(b, fh, indent=1, sort_keys=True)
    return b


if __name__ == "__main__":
    print(write(sys.argv[1])["block_sha256"])
    sys.exit(0)
