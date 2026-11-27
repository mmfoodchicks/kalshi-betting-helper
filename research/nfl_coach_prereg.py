"""Write the R1 registration (research/data/nfl_coach/prereg_r1.json). Run
once, BEFORE any sealed game is read, and committed with the files it pins.
It refuses to overwrite an existing registration.

  python3 -m research.nfl_coach_prereg
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r1.json")
PINNED = ("research/nfl_coach_r1_read.py", "research/nfl_coach_eval.py", "research/nfl_coach_data.py",
          "nfl_coach_sim.py", "research/data/nfl_coach_tables.json", "tests/guards_auto_nfl_coach.py",
          # the control arm and what it shares with the candidate
          "nfl_deep_sim.py", "nfl_pbp.py")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def build():
    import nfl_coach_sim as C
    import nfl_deep_sim as D
    with open(os.path.join(ROOT, "research", "data", "nfl_coach", "free_r1.json")) as fh:
        free = json.load(fh)
    reg = {
        "what": "R1 of the NFL coaching engine: explicit clock management and fine time/score states, league level",
        "candidate": C.VERSION,
        "registered_utc": "2026-11-27",
        "sealed_never_read": "2026 week 13 on; nfl_coach_data.load_season drops those rows unparsed for every caller but the read",
        "seasons": {"fit": "2016-2023", "select": 2024, "can_only_block": 2025,
                    "development_seen_by_the_audit": "2026 weeks 1-12"},
        "window": {"season": 2026, "weeks": [13, 14, 15, 16, 17, 18], "season_type": "REG", "min_games": 90},
        "not_before_utc": "2027-01-06T06:00:00Z",
        "also": "and not before the window's last game date + 30 h + 48 h by the data itself",
        "arms": {"control": "nfl_deep_sim.play_game on this file's `base` tables (production's fit on 2016-2023), kickoff era 2025+",
                 "candidate": "nfl_coach_sim.play_game on the same base tables plus its coach tables",
                 "anchor": "both: league profile, each team's level_off set so the arm's own expected points equal "
                           "(total_line +/- spread_line) / 2, neutral field; level curve n per level below"},
        "settings": {"sims": 200, "playouts": 1000, "level_curve_n": 2500,
                     "form_sd": {"control": free["form_sd"]["control"], "coach": C._FORM_SD},
                     "checkpoints_q4_clock": [900, 300, 120], "p_clip": 0.002,
                     "seeds": "random.Random(game id hash) per game and arm, as nfl_coach_eval.game_job"},
        "criteria": {
            "1_scenarios": "tests/guards_auto_nfl_coach.py exits 0 (the eleven scenario tests and the rest)",
            "2_fidelity": "snaps and points inside 2:00 of the second and of the fourth quarter, per game: for EACH of "
                          "the four, |control - real| - |candidate - real| > the real mean's standard error",
            "3_level": "points per game, margin SD about the spread, total SD about the total, home win rate, overtime "
                       "rate: for each, |candidate - real| <= |control - real| + one standard error of the real figure",
            "4_late_state_wp": "from the real state at the first snap at or inside 5:00 and 2:00 of the fourth quarter "
                               "both arms play the game out; log loss on the real result (a tie scores half), "
                               "candidate minus control averaged within a game, mean over games < -2 standard errors",
            "5_pregame": "moneyline log loss and total CRPS, candidate minus control paired by game, each <= +1 "
                         "standard error (non-inferiority; no gain is claimed)"},
        "decision": "PASS only if all five hold. A FAIL is final for this candidate; nothing is re-read; a revised "
                    "engine is judged on games after the window. A PASS changes no production path by itself.",
        "power_stated_before_the_read": "see research/reports/nfl_coach_r1.md section 6: on the development seasons "
                                        "criterion 4's point estimate is about one standard error at 272 games",
        "files": {rel: _sha(rel) for rel in PINNED},
        "control_form_default": D._FORM_SD,
    }
    body = json.dumps(reg, sort_keys=True, separators=(",", ":"))
    reg["block_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    return reg


def main():
    if os.path.exists(OUT):
        print("REFUSED: the registration exists; it is written once")
        return 1
    reg = build()
    with open(OUT, "w") as fh:
        json.dump(reg, fh, indent=1, sort_keys=True)
    print(reg["block_sha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
