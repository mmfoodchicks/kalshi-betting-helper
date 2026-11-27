"""Writes R2's registration ONCE (research/data/nfl_coach/prereg_r2.json), before
any test game is read: two hypotheses, their windows, dates, settings and
criteria, the power stated, and the sha256 of every file the reads run on.
Refuses if the registration exists. research/reports/nfl_coach_r2_evidence.md
section C is the prose.

    python3 -m research.nfl_coach_r2_prereg
"""
import datetime
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r2.json")
FILES = ("nfl_coach_sim_r2.py", "research/data/nfl_coach_r2_tables.json", "research/nfl_coach_r2_data.py",
         "research/nfl_coach_r2_eval.py", "research/nfl_coach_r2_adapt.py", "research/data/nfl_coach/adapt_r2.json",
         "research/nfl_coach_r2_read.py", "tests/guards_auto_nfl_coach_r2.py",
         # R1's engine, tables and level anchor, and production's deep engine R1 plays on
         "nfl_coach_sim.py", "research/data/nfl_coach_tables.json", "research/nfl_coach_data.py",
         "research/nfl_coach_eval.py", "nfl_deep_sim.py", "nfl_pbp.py")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def block():
    return {
        "version": "coach-r2.0",
        "written_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requires_r1_read": True,
        "hypotheses": {
            "H1": {
                "what": "who gets the ball by situation (nfl_coach_sim_r2): role x bucket multipliers on the "
                        "player's share, mean-preserving, against the static share on the same base",
                "window": [{"season": 2026, "weeks": list(range(13, 19))}],
                "not_before_utc": "2027-01-06T06:00:00Z",
                "min_team_games": 150,
                "settings": {"sims": 200, "level_curve_n": 1000, "workers": 4, "min_prior_games": 3,
                             "seed": "per game: the last 8 bytes of the nflverse game id, little-endian, mod 2^31",
                             "players": "players.csv (nflverse, fresh) in the read's directory"},
                "statistics": {
                    "stage1": "nfl_coach_r2_eval.stage1 / summarize1: per team-game, static minus R2 log loss of "
                              "who got each real carry and target, summed (positive = R2 better)",
                    "realism": "nfl_coach_r2_eval.run_realism / summarize_realism: R2 minus static per team-game "
                               "(negative = R2 better) of the CRPS of RB1/RB2/QB carries and WR1-3/TE1/RB1 targets "
                               "and the log score of their touchdowns, both arms on the same simulated games"},
                "criteria": {
                    "1": "tests/guards_auto_nfl_coach_r2.py exits 0",
                    "2": "stage1 both (carries + targets): gain > 2 SE (SE over team-games)",
                    "3": "stage1 carries gain > 0, and targets gain > -1 SE",
                    "4": "realism: carries CRPS, targets CRPS and TD log score each R2 - static <= +1 SE"},
                "power": "development (2024 / 2025 / 2026 wk 1-12): joint gain 1.08 / 0.81 / 0.90 a team-game at SE "
                         "0.089 / 0.077 / 0.117 -> at ~190 team-games z ~ 6-8, criteria 2-3 pass with probability "
                         "> 0.99; criterion 4's three 1-SE bounds: ~0.8 jointly if the development differences "
                         "hold (a 1-SE bound fails about one time in six with no true difference)"},
            "H2": {
                "what": "in-game adaptation of the pass call at the decision level (research/nfl_coach_r2_adapt.py, "
                        "frozen coefficients in research/data/nfl_coach/adapt_r2.json); not in any engine",
                "window": [{"season": 2026, "weeks": list(range(13, 19))},
                           {"season": 2027, "weeks": list(range(1, 19))}],
                "not_before_utc": "2028-01-13T06:00:00Z",
                "min_team_games": 500,
                "statistics": {"gain": "nfl_coach_r2_adapt.score / summarize: per team-game, baseline minus "
                                       "candidate log loss of the neutral pass calls after the offence's 10th snap"},
                "criteria": {"1": "tests/guards_auto_nfl_coach_r2.py exits 0",
                             "2": "gain > 2 SE (SE over team-games)"},
                "power": "development gains 0.069 / 0.026 / 0.022 a team-game (2024 / 2025 / 2026 wk 1-12; fit "
                         "seasons 0.052): at ~640 team-games SE ~0.013, so power ~0.89 at 0.042 (the development "
                         "mean) and ~0.47 at 0.025 (the last two seasons)"}},
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
