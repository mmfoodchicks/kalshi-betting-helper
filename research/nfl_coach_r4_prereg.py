"""Writes R4's registration ONCE (research/data/nfl_coach/prereg_r4.json), before
any test game is read. research/reports/nfl_coach_r4.md section D is the prose.

    python3 -m research.nfl_coach_r4_prereg "<power statement>"
"""
import datetime
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r4.json")
FILES = ("nfl_coach_sim_r4.py", "research/data/nfl_coach_r4_tables.json", "research/nfl_coach_r4_data.py",
         "research/nfl_coach_r4_evidence.py", "research/nfl_coach_r4_fit.py", "research/nfl_coach_r4_eval.py",
         "research/nfl_coach_r4_read.py", "tests/guards_auto_nfl_coach_r4.py",
         "nfl_coach_sim_r3.py", "research/data/nfl_coach_r3_tables.json", "research/nfl_coach_r3_qb.py",
         "research/nfl_coach_r3_eval.py", "nfl_coach_sim_r2.py", "research/data/nfl_coach_r2_tables.json",
         "research/nfl_coach_r2_data.py", "research/nfl_coach_r2_eval.py", "nfl_coach_sim.py",
         "research/data/nfl_coach_tables.json", "research/nfl_coach_data.py", "research/nfl_coach_eval.py",
         "nfl_deep_sim.py", "nfl_pbp.py")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def block(power):
    return {
        "version": "coach-r4.0",
        "written_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "what": "personnel-aware decisions after a quarterback substitution (nfl_coach_sim_r4): the call, "
                "completion and interceptions with the backup in, the second unit's carries and targets after a "
                "strategic pull, by the kind of pull (injury from the play descriptions and the next week's "
                "report, else strategic)",
        "window": [{"season": 2026, "weeks": list(range(13, 19))}, {"season": 2027, "weeks": list(range(1, 19))}],
        "not_before_utc": "2028-01-13T06:00:00Z",
        "requires_r1_read": True,
        "min_pulled_team_games": 80,
        "settings": {"sims": 100, "level_curve_n": 1000, "workers": 4,
                     "seed": "per game: the last 8 bytes of the nflverse game id, little-endian, mod 2^31",
                     "files": "play_by_play_<season>.csv.gz, players.csv, injuries_<season>.csv in the read's "
                              "directory (nflverse, fresh); week 18's kind from the descriptions alone"},
        "statistics": {
            "decision": "nfl_coach_r4_eval.collect_with_calls / decision / summarize on every pulled team-game in "
                        "the window: the starters' cell rates (or xpass for the call) against them moved by R4's "
                        "frozen effects, log score summed per pulled team-game (positive = R4 better); joint = "
                        "personnel + call + completion",
            "realism": "nfl_coach_r4_eval.realism: R4 minus R3 per team-game of the CRPS of the real lead back's "
                       "carries and the real top two targets' targets, both from the same seeds (negative = "
                       "R4 better)"},
        "criteria": {"1": "tests/guards_auto_nfl_coach_r4.py exits 0",
                     "2": "decision joint gain > 2 SE (SE over pulled team-games)",
                     "3": "each of personnel, call and completion > -1 SE",
                     "4": "realism (a guardrail): the lead back's carries CRPS and the top two targets' CRPS, "
                          "R4 - R3 <= +2 SE"},
        "power": power,
        "dependence": "2026 weeks 13-18 are R1's, R2-H1's and R3's registered games; 2027 is fresh. R1's statistics "
                      "are game-level, R2-H1's who-got-the-ball on every snap, R3's the quarterback's pull and his "
                      "line, R4's what happens AFTER a pull (the backup's calls and efficiency, the second unit's "
                      "workload). Different questions, frozen before any of those games was played; errors are "
                      "correlated through the shared weeks, so joint passes are not independent confirmations. R4 "
                      "is fitted on 2016-2024, its rule checked on 2025-2026 weeks 1-12, and tuned on no read "
                      "(none has been run). R4 plays R3's pull unchanged: R3's verdict is not an input. Any change "
                      "to R4 or to an R1/R2/R3 file it plays on fails the pinned hashes and moves R4 to fresh games "
                      "under a new registration.",
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
