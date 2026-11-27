"""Write the two follow-up registrations, once each, BEFORE R1's read and
before any row they test exists in the repo:

  research/data/nfl_coach/prereg_c4.json    the confirmatory test of criterion 4
  research/data/nfl_coach/prereg_dfs.json   the independent DFS read

  python3 -m research.nfl_coach_prereg2

Neither touches research/data/nfl_coach/prereg_r1.json or a file it pins; both
pin those same files, so the candidate tested is byte for byte R1's.
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from research import nfl_coach_reg as RG          # noqa: E402

FAMILY_ALPHA = 0.05            # one-sided, over R1's criterion 4 and the confirmatory test together
R1_MIN_GAMES = 90              # prereg_r1.json: the fewest games its read accepts
# Paired late-state log-loss difference, SD per game, from the development seasons
# (research/data/nfl_coach/dev_r1.json: SE x sqrt(games) = 0.077, 0.083, 0.091).
C4_SD_GAME = 0.084
C4_DEV_EFFECTS = (0.0012, 0.0054, 0.0072)
# Offence CRPS paired difference between two engine arms, SE clustered by game: 0.0150 on the 63
# games of 2026 weeks 2-5 (research/reports/nfl_dfs_engine.md, the passing-volume anchor's test).
# Another pair of arms, so this is a guide to the scale, not this test's own SE.
DFS_SE_63 = 0.0150
ENGINE = ("nfl_coach_sim.py", "research/data/nfl_coach_tables.json", "research/nfl_coach_eval.py",
          "research/nfl_coach_data.py", "nfl_deep_sim.py", "nfl_pbp.py", "research/nfl_coach_reg.py",
          "research/nfl_deep_weight_read.py", "research/data/nfl_coach/prereg_r1.json")


def c4():
    with open(os.path.join(RG.REG_DIR, "prereg_r1.json")) as fh:
        r1 = json.load(fh)
    a_r1 = 1.0 - RG.t_cdf(2.0, R1_MIN_GAMES - 1)
    a_c = FAMILY_ALPHA - a_r1
    c = RG.obf_c(a_c, 0.5)
    n1, n2 = 136, 272
    se2 = C4_SD_GAME / math.sqrt(n2)
    d80 = RG.drift_for_power(c, 0.80)
    power = {f"{e:.4f}": round(RG.cross_prob(c, 0.5, e / se2, futility=True), 3) for e in C4_DEV_EFFECTS}
    reg = {
        "what": "confirmatory test of R1 criterion 4 (late-state win probability) on fresh games",
        "used_only_if": "research/data/nfl_coach/read_r1.json exists and its verdict fails criterion 4 and passes "
                        "the other four; otherwise this test never runs",
        "r1_registration": r1["block_sha256"], "r1_verdict": "preserved; this test never changes it",
        "registered_utc": "2026-11-27", "candidate": r1["candidate"],
        "season": 2027, "season_type": "REG",
        "why_these_games": "every game after R1's window that R1's pinned code can play: the 2026 postseason "
                           "(13 games) needs postseason overtime, which nfl_coach_eval does not pass, so it is left out",
        "statistic": "per game, log loss of the home-win probability from the real state at the first snap at or "
                     "inside 5:00 and 2:00 of the fourth quarter, candidate minus control, averaged over the "
                     "checkpoints the game has; the mean over games, SE over games",
        "settings": {"playouts": r1["settings"]["playouts"], "level_curve_n": r1["settings"]["level_curve_n"],
                     "form_sd": r1["settings"]["form_sd"], "p_clip": r1["settings"]["p_clip"],
                     "checkpoints_q4_clock": [300, 120]},
        "error_control": {
            "family": "R1's criterion 4 and this test",
            "family_one_sided_alpha": FAMILY_ALPHA,
            "r1_criterion_4_alpha": round(a_r1, 5),
            "r1_alpha_how": f"P(t > 2.0) on {R1_MIN_GAMES - 1} df: 'more than 2 SE' at the fewest games R1 accepts",
            "confirmatory_alpha": round(a_c, 5),
            "split": "Bonferroni: the two tests' rates add to the family rate whatever their dependence",
            "looks": 2, "shape": "O'Brien-Fleming: z boundary c / sqrt(information fraction)",
            "final_z": round(c, 4),
            "small_sample": "each z boundary becomes the Student t value of the same tail probability on games - 1 df",
            "futility": "look 1 stops when candidate minus control >= 0; not credited to alpha",
            "crossing_probability_under_no_effect": round(RG.cross_prob(c, 0.5), 5)},
        "looks": [
            {"look": 1, "weeks": list(range(1, 10)), "min_games": 125, "planned_games": n1,
             "not_before_utc": "2027-11-11T12:00:00Z", "z_boundary": round(c / math.sqrt(0.5), 4)},
            {"look": 2, "weeks": list(range(1, 19)), "min_games": 260, "planned_games": n2,
             "not_before_utc": "2028-01-12T12:00:00Z", "z_boundary": round(c, 4), "final": True}],
        "power": {"sd_per_game_assumed": C4_SD_GAME, "se_at_final_look": round(se2, 5),
                  "at_development_effects_nats": power,
                  "effect_with_80pct_power_nats": round(d80 * se2, 4),
                  "note": "the development estimates were 0.0012, 0.0054 and 0.0072 nats: at those sizes this test "
                          "will usually not confirm"},
        "stopping": "CONFIRMED at either look ends it; futility at look 1 ends it; look 2 is final. No third look, "
                    "no third test, no added season.",
        "meaning": {"CONFIRMED": "criterion 4 is met on fresh games at the family rate; R1's registered verdict "
                                 "stays FAIL and both are reported",
                    "NOT CONFIRMED": "a better late-state win probability is NOT ESTABLISHED for coach-r1.0, with the "
                                     "largest gain the data do not exclude (95%) printed beside it; the question is "
                                     "closed for this candidate. Realism (criteria 1-3) is a separate verdict."},
        "files": {rel: RG.sha(rel) for rel in ENGINE + ("research/nfl_coach_c4_confirm.py",)},
    }
    return RG.seal(reg)


def dfs():
    with open(os.path.join(RG.REG_DIR, "prereg_r1.json")) as fh:
        r1 = json.load(fh)
    games = 96
    se = DFS_SE_63 * math.sqrt(63.0 / games)
    crit = RG.t_ppf(0.975, games - 1)
    reg = {
        "what": "independent DFS read: production's DFS worlds against worlds whose games the coaching engine plays",
        "independent_of": "R1's verdict and the criterion-4 confirmatory test; run whatever they say",
        "registered_utc": "2026-11-27", "candidate": r1["candidate"],
        "arms": {"engine": "nfl_dfs_engine as shipped (ENV_SD, ATT_WEIGHT), games by nfl_deep_sim",
                 "coach": "the same code, seeds, anchor targets, Sleeper usage deal and allocation noise; games by "
                          "nfl_coach_sim through research.nfl_coach_dfs.coach_games on the caller's base tables",
                 "harness": "research.nfl_dfs_engine_eval._week run twice a week; fit seasons 2023-2025; K 400"},
        "window": {"season": 2026, "weeks": [13, 14, 15, 16, 17, 18], "min_games": 85},
        "not_before_utc": "2027-01-06T06:00:00Z",
        "data": "player-week DraftKings points for those weeks, built at the read by research.hist_data.build and "
                "passed with --table. No committed file holds a 2026 player-week after week 5.",
        "same_games_as_r1": "R1 reads game-level clock counts and the winner; this reads player points. The "
                            "candidate is frozen and pinned in both registrations and neither read can change it, "
                            "so neither informs the other. Its false-positive rate is its own (a DFS claim), not "
                            "part of the win-probability family.",
        "rule": {
            "primary": "offence CRPS (roled player-weeks), coach minus engine, SE clustered by game: "
                       "t < -t(0.975, games - 1)",
            "one_sided_alpha": 0.025,
            "guardrails": {"pair_rmse": "coach <= engine + 0.01", "pit_ks": "coach <= engine + 0.01",
                           "dst": "paired CRPS difference <= +1 SE", "k": "paired CRPS difference <= +1 SE"},
            "outcomes": {"PASS": "primary and every guardrail",
                         "NOT ESTABLISHED": "guardrails hold, the primary does not",
                         "WORSE": "a guardrail fails"},
            "looks": 1, "extension": "none"},
        "secondary_not_gating": ["the role-pair table (QB-WR, same-team stacks, cross-team) against real",
                                 "cross-team pair RMSE", "per-role CRPS and PIT", "defenses and kickers",
                                 "world summary: attempts, completions, yards against Sleeper and real"],
        "not_measurable": "late-game usage: the deal sees a game's totals, not its plays (audit 2.6); that is R2",
        "power": {"se_assumed_at_96_games": round(se, 4), "t_critical": round(crit, 3),
                  "source": "0.0150 on 63 games for another pair of engine arms (nfl_dfs_engine.md), scaled",
                  "power_at_0.006": round(RG.one_look_power(0.006, se, games - 1, 0.025), 3),
                  "power_at_0.017": round(RG.one_look_power(0.017, se, games - 1, 0.025), 3),
                  "effect_with_80pct_power": round((crit + 0.8416) * se, 4),
                  "note": "0.006 and 0.017 are the gains the passing-volume anchor showed on 2026 and 2025"},
        "meaning": {"PASS": "eligible for a DFS shadow build; any production use is the owner's word and its own test",
                    "NOT ESTABLISHED": "no DFS gain shown for coach-r1.0 on 96 games; the upper bound is printed; "
                                       "not re-read and not extended",
                    "WORSE": "the coaching engine is not used for DFS worlds as it stands"},
        "dry_run": "--dry plays 2026 weeks 2-5 (SEEN) end to end and writes no result; it must complete on the PC "
                   "before the date. A fix it forces is committed with a NEW registration before any week 13-18 "
                   "row is built.",
        "files": {rel: RG.sha(rel) for rel in ENGINE + (
            "research/nfl_coach_dfs.py", "research/nfl_coach_dfs_read.py", "nfl_dfs_engine.py",
            "research/nfl_dfs_engine_eval.py")},
    }
    return RG.seal(reg)


def main():
    for name, build in (("prereg_c4.json", c4), ("prereg_dfs.json", dfs)):
        p = os.path.join(RG.REG_DIR, name)
        if os.path.exists(p):
            print(f"REFUSED: {name} exists; a registration is written once")
            continue
        reg = build()
        with open(p, "w") as fh:
            json.dump(reg, fh, indent=1, sort_keys=True)
        print(name, reg["block_sha256"])
        print(json.dumps(reg.get("error_control") or reg["rule"], indent=1))
        print(json.dumps(reg["power"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
