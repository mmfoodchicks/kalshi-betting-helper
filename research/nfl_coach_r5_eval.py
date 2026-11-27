"""Development measurements for the coaching engine's fifth revision
(nfl_coach_sim_r5, research/reports/nfl_coach_r5.md). RESEARCH. nflverse (CC BY 4.0);
R1's seal holds.

    python3 -m research.nfl_coach_r5_eval <pbp dir> <players.csv> <injuries dir>

1. THE SECOND UNIT, scored on the real strategic pulls (no simulation): per pulled
   team-game, the log-likelihood of what the lead back and the top two targets
   actually got after the pull under R5 (drawn sit decisions, nfl_coach_r5_fit's
   latent model at the tables' values) minus under R4 (every one plays, his odds
   x R4's ratio -- the same model with the sit probabilities 0). Positive = R5
   better. 2025 and 2026 weeks 1-12 (R5's second-unit table is fitted on
   2016-2024 only).
2. THE HAZARD, R5's (s6 refitted on 2016-2025) against R4's (R3's): per team-game
   log loss on 2026 weeks 1-12 (2025 is in R5's fit).
3. THE DISTRIBUTION, simulated: R4's and R5's strategic pulls on a league roster,
   how often the lead back sits out the rest of the game and the top two targets
   both get nothing, and the lead back's share when he plays, beside the real
   2016-2024 pulls.
Writes research/data/nfl_coach/r5_eval.json.
"""
import json
import math
import os
import random
import sys
from collections import defaultdict

import nfl_coach_sim_r3 as E3
import nfl_coach_sim_r4 as E4
import nfl_coach_sim_r5 as E5
from research import nfl_coach_r3_qb as QB
from research import nfl_coach_r4_data as XD
from research import nfl_coach_r4_fit as F4
from research import nfl_coach_r5_fit as F5

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "r5_eval.json")
WINDOW_GAMES = 45           # strategic pulled team-games a regular season holds (2016-2024: 424 in 9)


def theta_r5(P):
    return (P.q_b, P.q_w0, P.q_w1, P.d_b, P.d_w)


def theta_r4(X):
    ef = X.effects["strategic"]
    return (0.0, 0.0, 0.0, math.log(ef["rb1"]), math.log(ef["top2"]))


def unit_score(rows, P, X):
    """Per pulled team-game log-likelihood, R5 minus R4, and the summary."""
    a, b = theta_r5(P), theta_r4(X)
    d = [F5._game_ll(r, a) - F5._game_ll(r, b) for r in rows]
    return d, _summ(d)


def _summ(d):
    n = len(d)
    if n < 2:
        return {"n": n}
    m = sum(d) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1))
    return {"gain_per_pulled_team_game": round(m, 4), "se": round(sd / math.sqrt(n), 4),
            "z": round(m / (sd / math.sqrt(n)), 2) if sd else None, "sd": round(sd, 4), "n": n}


def power(effect, sd, n, z=2.0):
    se = sd / math.sqrt(n)
    return round(0.5 * math.erfc(-(effect / se - z) / math.sqrt(2)), 4)


def hazard_score(rows, P):
    q3 = E3.tables().coef
    d = []
    for _s, sq in rows:
        a = b = 0.0
        for sd, gsr, to, ct, y in sq:
            p3 = min(1 - 1e-9, max(1e-9, E3.hazard(q3, sd, gsr)))
            p5 = min(1 - 1e-9, max(1e-9, E5.hazard(P.coef, sd, gsr, to, P.spec, ct)))
            a -= math.log(p3 if y else 1 - p3)
            b -= math.log(p5 if y else 1 - p5)
        d.append(a - b)
    return _summ(d)


def _roster():
    return [dict(id="QB", pos="QB", car=3.0, tgt=0, ypc=5.0), dict(id="QB2", pos="QB", car=0.0, tgt=0, ypc=3.5),
            dict(id="RB1", pos="RB", car=15.0, tgt=3.5, ypc=4.3, ypr=7.5, cr=0.78),
            dict(id="RB2", pos="RB", car=6.0, tgt=2.0, ypc=4.1, ypr=7.0, cr=0.76),
            dict(id="WR1", pos="WR", car=0.4, tgt=8.5, ypc=7.0, ypr=13.0, cr=0.64),
            dict(id="WR2", pos="WR", car=0.0, tgt=6.0, ypr=12.5, cr=0.62),
            dict(id="WR3", pos="WR", car=0.0, tgt=4.0, ypr=12.0, cr=0.6),
            dict(id="TE1", pos="TE", car=0.0, tgt=5.0, ypr=10.0, cr=0.7)]


def sim_distribution(engine, n=1500, seed=29):
    """Strategic pulls simulated by `engine` (E4 or E5), the home side's second unit
    after the pull: the lead back sat (0 of 3+ backs' carries), both top two got
    nothing (0 of 3+ targets), the lead back's share of the backs' carries when he
    carried, and how often he sat while both receivers played."""
    import nfl_coach_sim as C
    lg = C.league_profile()
    out = {"back_games": 0, "back_sat": 0, "top2_games": 0, "top2_none": 0, "share_playing": [],
           "back_sat_receivers_played": 0, "back_played_a_receiver_sat": 0, "both_games": 0}
    # games from a 24-0 lead with 10:00 left in the third (most pulls strategic); the touches after
    # the pull are the full deal less the deal of the snaps before it (the same generator: the
    # pre-pull snaps are the deal's prefix, drawn identically)
    rng = random.Random(seed)
    prng = random.Random((seed * 6007 + 31337) & 0x7FFFFFFF)
    import nfl_coach_sim_r2 as E2
    U = E2.tables()
    X = E4.tables()
    P = E5.tables()
    team = None
    for j in range(n):
        kw = {"start": {"per": 3, "clk": 600, "pts": (24, 0), "off": 0, "down": 1, "dist": 10, "ytg": 75}}
        r = engine.play_game(lg, lg, rng, prng, X=X, **kw) if engine is E4 else \
            engine.play_game(lg, lg, rng, prng, X=X, Q=P, **kw)
        q = r["qb"][0]
        if q["pull"] is None or q["kind"] != "strategic":
            continue
        if team is None:
            team = E2.TeamDeal(_roster(), U)
        sn = [s for s in E3.snaps(r["log"], (24, 0)) if s[0] == 0]
        drng = random.Random(j)
        if engine is E4:
            ln = E4.deal(sn, r["h"], team, drng, "QB2", q, X, U)
        else:
            ln = E5.deal(sn, r["h"], team, drng, "QB2", q, X, U, P)
        # after-the-pull touches: deal the pulled game again with the pre-pull snaps only, and subtract
        pre = [s for s in sn if s[6] < q["pull"]]
        drng2 = random.Random(j)
        q0 = {"pull": None, "back": None, "kind": None}
        ln0 = E4.deal(pre, {"rush_yds": 0, "pass_yds": 0, "fum_lost": sum(s[5] for s in pre)}, team, drng2,
                      "QB2", q0, X, U)
        car = {i: ln[i]["car"] - ln0[i]["car"] for i in ln}
        tgt = {i: ln[i]["tgt"] - ln0[i]["tgt"] for i in ln}
        rbc = car.get("RB1", 0) + car.get("RB2", 0)
        tt = sum(tgt.values())
        bs = None
        if rbc >= 3:
            out["back_games"] += 1
            bs = car["RB1"] == 0
            out["back_sat"] += bs
            if car["RB1"] > 0:
                out["share_playing"].append(car["RB1"] / rbc)
        if tt >= 3:
            out["top2_games"] += 1
            out["top2_none"] += tgt["WR1"] == 0 and tgt["WR2"] == 0
            if bs is not None:
                out["both_games"] += 1
                out["back_sat_receivers_played"] += bs and tgt["WR1"] > 0 and tgt["WR2"] > 0
                out["back_played_a_receiver_sat"] += (not bs) and (tgt["WR1"] == 0 or tgt["WR2"] == 0)
    sh = sorted(out.pop("share_playing"))
    return {"back_games": out["back_games"], "back_sat": round(out["back_sat"] / max(1, out["back_games"]), 4),
            "top2_games": out["top2_games"], "top2_none": round(out["top2_none"] / max(1, out["top2_games"]), 4),
            "share_when_playing_median": round(sh[len(sh) // 2], 3) if sh else None,
            "back_sat_receivers_played": round(out["back_sat_receivers_played"] / max(1, out["both_games"]), 4),
            "back_played_a_receiver_sat": round(out["back_played_a_receiver_sat"] / max(1, out["both_games"]), 4)}


def real_extras(rows):
    """The real counterparts of sim_distribution's last three figures."""
    sh = sorted(b[1] / b[0] for b, _ in rows if b is not None and b[0] >= 3 and b[1] > 0)
    both = [(b, r) for b, r in rows if b is not None and b[0] >= 3 and len(r) == 2
            and all(x is not None and x[0] >= 3 for x in r)]
    return {"share_when_playing_median": round(sh[len(sh) // 2], 3) if sh else None,
            "both_games": len(both),
            "back_sat_receivers_played": round(sum(1 for b, r in both if b[1] == 0 and r[0][1] > 0 and r[1][1] > 0) /
                                               max(1, len(both)), 4),
            "back_played_a_receiver_sat": round(sum(1 for b, r in both if b[1] > 0 and (r[0][1] == 0 or r[1][1] == 0))
                                                / max(1, len(both)), 4)}


def main(pbp, players_csv, inj):
    allg, pos, ids_of = QB.load_all(pbp, players_csv, tuple(range(2016, 2027)))
    cls = F4.make_cls(F4.reports(inj, range(2016, 2026)))
    extra = {s: XD.load_extra(pbp, s) for s in allg}
    P, X = E5.tables(), E4.tables()
    out = {"credit": "nflverse-data play-by-play, players and injuries (CC BY 4.0)", "unit": {}}
    sds = []
    for s in (2025, 2026):
        rows = F5.unit_rows(allg, ids_of, pos, cls, extra, (s,))
        d, sm = unit_score(rows, P, X)
        out["unit"][str(s)] = sm
        sds.append(sm)
    fit_rows = F5.unit_rows(allg, ids_of, pos, cls, extra, F5.FIT)
    out["unit"]["2016_2024_in_sample"] = unit_score(fit_rows, P, X)[1]
    eff = min(x["gain_per_pulled_team_game"] for x in sds)
    sd = max(x["sd"] for x in sds)
    out["power"] = {"window_games_expected": WINDOW_GAMES, "effect_smaller_dev_season": eff, "sd_larger": sd,
                    "power_2se": power(eff, sd, WINDOW_GAMES),
                    "power_half_effect": power(eff / 2, sd, WINDOW_GAMES),
                    "power_at_30_games": power(eff, sd, 30)}
    states = {}
    for s in (2026,):
        tos = F5.timeouts(pbp, s)
        states[s] = [F5.drive_states(tg, ids_of[s], pos, tos) for tg in allg[s]]
    # the coach term is 0 unless the chosen spec reads it
    cts = defaultdict(float)
    rows26 = [(2026, [(sd_, gsr, to, cts[0], y) for sd_, gsr, to, y in st]) for st in states[2026]]
    out["hazard_2026_w1_12_r5_minus_r4_gain"] = hazard_score(rows26, P)
    # the corner secondary's power: R5's expectation for a full season (2025's, 4.5 under the refit), the
    # 2022-2026 excess (47 real against 20.3, x2.3) if it persists
    from research import nfl_coach_r5_read as RR
    mu = json.load(open(F5.OUT))["r3_miss_cell"]["2025"]["r5"]["expected"]
    k = next(k for k in range(1, 60) if RR.corner_p(k, mu) < 0.05)
    out["corner_secondary_power"] = {"r5_expected_season": mu, "critical_count": k,
                                     "power_if_x2.3": round(RR.corner_p(k, mu * 47 / 20.3), 4),
                                     "power_if_x1.5": round(RR.corner_p(k, mu * 1.5), 4)}
    out["sim"] = {"r4": sim_distribution(E4), "r5": sim_distribution(E5)}
    out["real_2016_2024"] = dict(F5.observed(fit_rows), **real_extras(fit_rows))
    dev_rows = F5.unit_rows(allg, ids_of, pos, cls, extra, (2025, 2026))
    out["real_2025_2026"] = dict(F5.observed(dev_rows), **real_extras(dev_rows))
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2], sys.argv[3]), indent=1)[:5000])
