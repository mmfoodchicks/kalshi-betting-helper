"""The 2027 family of coaching reads with R6 in it: four primaries, Holm recomputed,
every member's power, and R6's correlation with the other three on the development
games (research/reports/nfl_coach_family_2027.md section F). RESEARCH.

R5's amendment pins nfl_coach_family_2027.py and family_2027.json: they are not
changed, and the three-test analysis R5's amendment registered stands as registered.
This file adds R6 beside it.

    python3 -m research.nfl_coach_family_2027_r6 <pbp dir> <players.csv> <injuries dir>

R6's unit is a game (nfl_coach_r6_fit's per-game gains, r6_dev_games.json); the
others' a team-game. For a game-level A and a team-game-level B the covariance of
their means is sum over games of (a_g - mean a) x (the sum over the game's B units of
(b_u - mean b)), over nA nB -- the shared-units formula when both are team-games.
Writes research/data/nfl_coach/family_2027_r6.json.
"""
import json
import math
import os
import sys
from collections import defaultdict

from research import nfl_coach_family_2027 as FM

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "family_2027_r6.json")
R6_DEV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r6_dev_games.json")
R6_FIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r6_fit.json")


def corr_game_vs_units(a, b, game_of):
    """a {game: stat}, b {unit: stat}, game_of {unit: game}."""
    na, nb = len(a), len(b)
    ma, mb = sum(a.values()) / na, sum(b.values()) / nb
    va = sum((x - ma) ** 2 for x in a.values()) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b.values()) / (nb - 1)
    agg = defaultdict(float)
    for u, x in b.items():
        g = game_of.get(u)
        if g is not None:
            agg[g] += x - mb
    cov = sum((a[g] - ma) * s for g, s in agg.items() if g in a) / (na * nb)
    return {"nA_games": na, "nB_units": nb, "shared_games": sum(1 for g in agg if g in a),
            "corr_of_means": round(cov / math.sqrt(va / na * vb / nb), 4)}


def main(pbp, players_csv, inj):
    from research import nfl_coach_r3_qb as QB
    st = FM.statistics(pbp, players_csv, inj)
    allg, _pos, _ids = QB.load_all(pbp, players_csv, (2025, 2026))
    game_of = {(tg["season"], tg["week"], tg["team"]): tg["gid"] for s in allg for tg in allg[s]}
    r6 = json.load(open(R6_DEV))
    three = json.load(open(FM.OUT))
    res = {"three_test_analysis": "research/data/nfl_coach/family_2027.json, as R5's amendment registered it",
           "pairs_with_r6": {}, "pairs_without_r6": three["pairs"]}
    for s in (2025, 2026):
        a = r6[str(s)]
        for nm in ("R2-H2", "R4", "R5"):
            res["pairs_with_r6"].setdefault(f"R6 x {nm}", {})[str(s)] = corr_game_vs_units(a, st[s][nm], game_of)
    th = FM.holm(0.05, 4)
    fit = json.load(open(R6_FIT))
    pooled, comps = fit["development"]["pooled"], fit["development"]["components"]
    n = fit["power"]["window_games"]
    # R6's registered pull statistic is against R5's hazard, whose forecast shift is ~0: its share of
    # the development gain (measured against a flat full-history hazard) is dropped for the power
    eff_wo_pull = pooled["gain_per_game"] - comps["pull"]["gain_per_game"]
    l2 = fit["development"]["last_two_folds"]
    ratios = {"R2-H2": 0.042 / 0.013, "R2-H2 (last two seasons)": 0.025 / 0.013, "R4": 3.2,
              "R5 (2027, ~45)": 1.34 / (2.68 / math.sqrt(45)), "R5 (2027 floor, 30)": 1.34 / (2.68 / math.sqrt(30)),
              "R6 (dev mean, pull excluded)": eff_wo_pull / (pooled["sd"] / math.sqrt(n)),
              "R6 (2025-26 level)": l2["gain_per_game"] / (l2["sd"] / math.sqrt(n))}
    ph = FM._phi
    res["family"] = {"tests": ["R2-H2 criterion 2", "R4 criterion 2", "R5 criterion 2 (2027 amendment)",
                               "R6 criterion 2 (2027 primary)"],
                     "definition": "the primary criterion of every coaching read whose primary window is the 2027 "
                                   "regular season (R4's window adds 2026 weeks 13-18); C4 stays in R1's "
                                   "owner-approved family; C3-N is not coaching; the replications (R5 2028, R6 "
                                   "2028) are not members",
                     "holm_familywise_one_sided": 0.05, "holm_z_thresholds": th,
                     "uncorrected_familywise_if_independent": round(1 - (1 - (1 - ph(2.0))) ** 4, 4),
                     "power": {k: {"effect_over_se": round(r, 3), "own_rule_z2": round(ph(r - 2.0), 4),
                                   "holm_first_step": round(ph(r - th[0]), 4),
                                   "holm_last_step": round(ph(r - th[-1]), 4)} for k, r in ratios.items()}}
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    return res


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2], sys.argv[3]), indent=1))
