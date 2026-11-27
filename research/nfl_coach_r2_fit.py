"""The fit for the coaching engine's second revision: who gets the ball, by
situation (nfl_coach_sim_r2, research/reports/nfl_coach_r2_evidence.md section
B). RESEARCH. Reads nflverse play-by-play 2016-2023 (CC BY 4.0) and nflverse's
players file for positions; writes research/data/nfl_coach_r2_tables.json.

    python3 -m research.nfl_coach_r2_fit <pbp dir> <players.csv>

The seasons are R1's: fit 2016-2023, select 2024, 2025 can only block, 2026
weeks 1-12 development, 2026 week 13 on sealed (the loader never hands it over).
Nothing was selected on 2024 here: the multipliers are maximum likelihood on
the fit seasons with no tuning constant (the one candidate with a constant,
the player's own goal-line deviation, was not supported and is not built).
"""
import json
import os
import sys
import time

import nfl_coach_sim_r2 as E2
from research import nfl_coach_r2_data as R
from research import nfl_coach_r2_evidence as E

FIT_SEASONS = tuple(range(2016, 2024))
OUT = E2.TABLES_PATH


def target_rates(data):
    """Share of incompletions (not intercepted) and of interceptions that had a
    targeted receiver, regular season (nflverse leaves it blank on throwaways)."""
    inc = [0, 0]
    itc = [0, 0]
    for d in data.values():
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for p in g["plays"]:
                if p[R.Q_KIND] != R.K_PASS:
                    continue
                if p[R.Q_INT]:
                    itc[0] += p[R.Q_RECV] >= 0
                    itc[1] += 1
                elif not p[R.Q_CMP]:
                    inc[0] += p[R.Q_RECV] >= 0
                    inc[1] += 1
    return {"inc": round(inc[0] / inc[1], 4), "int": round(itc[0] / itc[1], 4), "n_inc": inc[1], "n_int": itc[1]}


def league_mix(data):
    """The fit seasons' carries and targets by bucket (regular season)."""
    m = {"car": [0] * E2.NB, "tgt": [0] * E2.NB}
    for d in data.values():
        for g in d["games"]:
            if g["st"] != 2:
                continue
            for p in g["plays"]:
                k = p[R.Q_KIND]
                if k in (R.K_RUN, R.K_SCR) and p[R.Q_RUSH] >= 0:
                    m["car"][E.pbucket(p)] += 1
                elif k == R.K_PASS and p[R.Q_RECV] >= 0:
                    m["tgt"][E.pbucket(p)] += 1
    return m


def build(pbp_dir, players_csv):
    data = {s: R.load_season(pbp_dir, s) for s in FIT_SEASONS}
    pos = R.positions(players_csv)
    ug = E.usage_games(data, pos)
    out = {"version": E2.VERSION, "fit_seasons": list(FIT_SEASONS), "buckets": list(E2.BUCKETS),
           "roles": {"car": list(E2.CARRY_ROLES), "tgt": list(E2.TGT_ROLES)},
           "credit": "nflverse-data play-by-play and players (CC BY 4.0)",
           "seal": "2026 week 13 on and every later season: never read by this fit",
           "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "mult": {}, "mult_role_only": {}, "fit_plays": {}}
    for kind in ("car", "tgt"):
        M, n = E.fit_mult(ug, kind)
        M0, _ = E.fit_mult(ug, kind, pooled=True)
        out["mult"][kind] = {r: [round(x, 5) for x in v] for r, v in M.items()}
        out["mult_role_only"][kind] = {r: round(v[0], 5) for r, v in M0.items()}
        out["fit_plays"][kind] = n
    out["mix"] = league_mix(data)
    out["p_target"] = target_rates(data)
    return out


if __name__ == "__main__":
    res = build(sys.argv[1], sys.argv[2])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    print(OUT, res["fit_plays"], res["p_target"])
