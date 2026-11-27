"""Writes R3's registration ONCE (research/data/nfl_coach/prereg_r3.json),
before any test game is read: the window, date, settings, criteria, power, the
dependence on R1's and R2's reads, and the sha256 of every file the read runs
on. Refuses if it exists. research/reports/nfl_coach_r3.md section D is the prose.

    python3 -m research.nfl_coach_r3_prereg
"""
import datetime
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r3.json")
FILES = ("nfl_coach_sim_r3.py", "research/data/nfl_coach_r3_tables.json", "research/nfl_coach_r3_qb.py",
         "research/nfl_coach_r3_eval.py", "research/nfl_coach_r3_read.py", "tests/guards_auto_nfl_coach_r3.py",
         # R2's deal and measures, R1's engine and anchor, production's deep engine
         "nfl_coach_sim_r2.py", "research/data/nfl_coach_r2_tables.json", "research/nfl_coach_r2_data.py",
         "research/nfl_coach_r2_eval.py", "nfl_coach_sim.py", "research/data/nfl_coach_tables.json",
         "research/nfl_coach_data.py", "research/nfl_coach_eval.py", "nfl_deep_sim.py", "nfl_pbp.py")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def block():
    return {
        "version": "coach-r3.0",
        "written_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "what": "the quarterback's substitution (nfl_coach_sim_r3): a learned pull hazard at each identified "
                "drive, the pulled starter staying out, against R2 (the starter plays every snap)",
        "window": [{"season": 2026, "weeks": list(range(13, 19))}],
        "not_before_utc": "2027-01-06T06:00:00Z",
        "requires_r1_read": True,
        "min_team_games": 150,
        "settings": {"sims": 200, "level_curve_n": 1000, "workers": 4,
                     "seed": "per game: the last 8 bytes of the nflverse game id, little-endian, mod 2^31",
                     "players": "players.csv (nflverse, fresh) in the read's directory",
                     "realism_team_games": "each team's 4th game on (the as-of shares need three)"},
        "statistics": {
            "stage1": "nfl_coach_r3_eval.stage1 / summarize1 on every regular-season team-game in the window: "
                      "constant-hazard minus engine-hazard log loss of the pull sequence (positive = R3 better)",
            "realism": "nfl_coach_r3_eval.run / summarize_realism: R3 minus R2 per team-game of the CRPS of the "
                       "real starter's dropbacks and passing yards, both arms on the same simulated games and "
                       "the same deal draws (negative = R3 better)"},
        "criteria": {"1": "tests/guards_auto_nfl_coach_r3.py exits 0",
                     "2": "stage1 gain > 2 SE (SE over team-games)",
                     "3": "starter dropbacks CRPS: R3 - R2 < -2 SE",
                     "4": "starter passing yards CRPS: R3 - R2 <= +1 SE"},
        "power": "development, 2025 / 2026 weeks 1-12. Stage 1: +0.283 / +0.261 nats a team-game at SE 0.042 / "
                 "0.047 (544 / 328 team-games); at ~190 team-games SE ~0.072, z ~3.6, power ~0.95. Starter dropbacks "
                 "CRPS: -0.363 +/- 0.049 / -0.181 +/- 0.057 (448 / 232 team-games); at ~190, z ~4.8 / ~2.9, power "
                 "~1.0 / ~0.82. Passing yards CRPS: -1.45 +/- 0.30 / -0.71 +/- 0.41; 'not worse than +1 SE' holds "
                 "with probability ~0.99. Jointly ~0.78-0.94; the likeliest failure is criterion 3 if 2026's smaller "
                 "dropbacks gain is the truth",
        "dependence": "The window's games are R1's registered test games and R2-H1's. R1's statistics are "
                      "game-level and R2-H1's who-got-the-ball; R3's are the quarterback's pull and his line, a "
                      "different question, frozen here before any of those games was played. Errors are "
                      "correlated through the shared games (a blowout-heavy stretch moves all three), so joint "
                      "passes are not independent confirmations; each test's own false-pass rate is unchanged. "
                      "R3 is not tuned on R1's or R2's results: none has been read. Any change to R3 or to the "
                      "R1/R2 files it plays on fails the pinned hashes, and moves R3 to fresh games under a new "
                      "registration (the owner's ruling, 2026-11-27).",
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
