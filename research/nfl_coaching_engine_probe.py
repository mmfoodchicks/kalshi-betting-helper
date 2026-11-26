"""What nfl_deep_sim does at the end of halves, in the cells
research/nfl_coaching_data_audit.py measures on real games. RESEARCH,
read-only: it plays the production engine through its own passive log
(play_game(log=)) and changes nothing (research/reports/nfl_coaching_audit.md).

League average against itself, each game on its own seed. ~2 ms a game with
the log, so the default 4,000 games is a few seconds on one core.

  python3 -m research.nfl_coaching_engine_probe [--n 4000] [--out research/data/nfl_coaching_engine_probe.json]

Measured: kneels by quarter; snaps and points inside 2:00 of each half; the
pass rate and the seconds to the next snap after an in-bounds play by
fourth-quarter (and second-quarter) clock x score cell; onside kicks; and,
straight from the fitted tables, the play-call table's pooled pass rate by
(time state, score state) -- time state 1 pools the first half's last two
minutes with the fourth quarter above five minutes.
"""
import collections
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nfl_deep_sim as E      # noqa: E402
import nfl_pbp as P           # noqa: E402

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coaching_engine_probe.json")
SNAPS = ("run", "cmp", "inc", "sack", "int")


def _q4bin(clk):
    return ("15:00-10:00" if clk > 600 else "10:00-5:00" if clk > 300 else "5:00-3:00" if clk > 180
            else "3:00-2:00" if clk > 120 else "2:00-1:00" if clk > 60 else "1:00-0:00")


def _q2bin(clk):
    return ("15:00-5:00" if clk > 300 else "5:00-2:00" if clk > 120 else "2:00-1:00" if clk > 60
            else "1:00-0:00")


def _acc(d, key, num, den=1.0):
    c = d.setdefault(key, [0.0, 0.0])
    c[0] += num
    c[1] += den


def probe(n, seed=20261126):
    T = E.tables()
    lg = E.league_profile(T)
    kneel_q = collections.Counter()
    win = collections.Counter()
    q4_pass, q4_dt, q2_pass, q2_dt = {}, {}, {}, {}
    ko = collections.Counter()
    tot = collections.Counter()
    for g in range(n):
        log = []
        res = E.play_game(lg, lg, random.Random(seed + g), T, log=log)
        tot["ot"] += res["ot"]
        tot["pts"] += res["h"]["pts"] + res["a"]["pts"]
        tot["kneels"] += res["h"]["kneels"] + res["a"]["kneels"]
        before = {2: 0, 4: 0}
        last = {2: 0, 4: 0}
        prev = (0, 0)
        for k, e in enumerate(log):
            kind, q, c = e["k"], e["q"], e["c"]
            s_before = prev                 # the score before this event
            prev = e["s"]
            if q in (2, 4):
                # an event that STARTS above 2:00 belongs to the body of the half
                if c > 120:
                    before[q] = sum(e["s"])
                last[q] = sum(e["s"])
            if kind == "kneel":
                kneel_q[q] += 1
            if kind == "ko":
                ko[e.get("res")] += 1
            if kind not in SNAPS:
                continue
            o = e["o"]
            sd = s_before[o] - s_before[1 - o]
            if q in (2, 4) and c <= 120:
                win[f"q{q}_snaps"] += 1
            is_pass = kind != "run"
            dt = None
            if (kind in ("run", "cmp", "sack") and not e.get("ob") and not e.get("td") and not e.get("to")
                    and not e.get("sf") and k + 1 < len(log)):
                y = log[k + 1]
                if y["k"] in SNAPS and y.get("o") == o and y["q"] == q and 0 <= c - y["c"] <= 60 \
                        and not (q in (2, 4) and c > 120 >= y["c"]):
                    dt = c - y["c"]
            if q == 4:
                key = (_q4bin(c), P.score_state(sd))
                _acc(q4_pass, key, is_pass)
                if dt is not None:
                    _acc(q4_dt, key, dt)
            elif q == 2:
                key = (_q2bin(c), P.score_state(sd))
                _acc(q2_pass, key, is_pass)
                if dt is not None:
                    _acc(q2_dt, key, dt)
        for q in (2, 4):
            win[f"q{q}_pts"] += last[q] - before[q]

    def table(d, digits=3):
        return {"|".join(str(p) for p in k): [round(v[0] / v[1], digits) if v[1] else None, int(v[1])]
                for k, v in sorted(d.items(), key=lambda kv: str(kv[0]))}
    # the play-call table itself, pooled over down / distance / zone
    cells = {}
    for key, (hits, cnt) in T.raw["call"].items():
        ts, ss = key.split(",")[:2]
        _acc(cells, (int(ts), int(ss)), hits, cnt)
    return {"games": n, "engine_tables_built": T.raw["fit"]["built"],
            "per_game": {"pts": round(tot["pts"] / n, 2), "kneels": round(tot["kneels"] / n, 3),
                         "ot_rate": round(tot["ot"] / n, 4),
                         "kneels_by_quarter": {str(q): round(v / n, 3) for q, v in sorted(kneel_q.items())},
                         "q2_snaps": round(win["q2_snaps"] / n, 3), "q4_snaps": round(win["q4_snaps"] / n, 3),
                         "q2_pts": round(win["q2_pts"] / n, 3), "q4_pts": round(win["q4_pts"] / n, 3),
                         "kickoffs": {k: round(v / n, 4) for k, v in ko.items()}},
            "q4_pass_rate": table(q4_pass), "q4_sec_to_snap": table(q4_dt, 1),
            "q2_pass_rate": table(q2_pass), "q2_sec_to_snap": table(q2_dt, 1),
            "call_table_pass_rate_by_time_score": table(cells),
            "clock_table_cells": sorted(T.raw["clock"]),
            "situational_keys": {"time_state": "0 body | 1 last 2:00 of Q2 or Q4 above 5:00 | 2 Q4 last 5:00 and OT",
                                 "score_state": "down 9+ | down 1-8 | tied | up 1-8 | up 9+",
                                 "tempo": "0 normal | 1 hurry-up | 2 clock-killing",
                                 "timeouts": "none (absorbed in the clock histograms)"}}


def main(argv):
    n, outp = 4000, OUT_DEFAULT
    for i, a in enumerate(argv):
        if a == "--n":
            n = int(argv[i + 1])
        elif a == "--out":
            outp = argv[i + 1]
    res = probe(n)
    with open(outp, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
