"""Verification of the 5% family-wise rate the owner approved (2026-11-27):
R1's criterion 4 and its confirmatory test, simulated END TO END under no
effect. It reads both registrations and edits neither.

  python3 -m research.nfl_coach_family_check [--n 4000000]

What one simulated "world" is:
  1. R1's criterion 4: a one-sample t statistic on `n_r1` games (Student t on
     n_r1 - 1 df under the null), rejected above its registered 2.0.
  2. THE GATE. The confirmatory test runs only when R1 fails criterion 4 and
     passes the other four. Nothing is assumed about how often the other four
     pass: the WORST case for a false positive is that they always pass, so
     the confirmatory test runs every time criterion 4 fails. That is what is
     simulated; any real pass rate below one only lowers the family rate.
  3. The confirmatory test on FRESH games (2027; independent of R1's games):
     look 1 on the first 136 games, look 2 on all 272. The two looks share the
     first 136 games, so they are correlated exactly as the design says
     (information fraction 0.5): the simulation draws the first block's and the
     second block's means and sums of squares and builds look 2 from both.
     Each look's t statistic is compared with its registered z boundary
     converted to Student t on games - 1 df, as the read does. Futility (look
     1 not ahead) is simulated both obeyed and ignored.
A world is a false positive when criterion 4 rejects in R1 OR the
confirmatory test confirms.

Three variants: R1 at its fewest games (90) and at its expected 96; and a
heavy-tailed one where each game's difference is Student t on 3 df (the real
differences are clipped log losses with a long tail), built game by game.
"""
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from research import nfl_coach_reg as RG          # noqa: E402

OUT = os.path.join(RG.REG_DIR, "family_check.json")


def _design():
    with open(os.path.join(RG.REG_DIR, "prereg_c4.json")) as fh:
        c4 = json.load(fh)
    with open(os.path.join(RG.REG_DIR, "prereg_r1.json")) as fh:
        r1 = json.load(fh)
    lk = c4["looks"]
    return {"r1_min_games": r1["window"]["min_games"], "r1_threshold_t": 2.0,
            "n1": lk[0]["planned_games"], "n2": lk[1]["planned_games"],
            "z1": lk[0]["z_boundary"], "z2": lk[1]["z_boundary"],
            "family_alpha": c4["error_control"]["family_one_sided_alpha"],
            "bonferroni": c4["error_control"]["r1_criterion_4_alpha"] + c4["error_control"]["confirmatory_alpha"]}


def _rate(hits):
    p = float(hits.mean())
    return [round(p, 5), round(math.sqrt(p * (1 - p) / hits.size), 5)]


def simulate(n_worlds, n_r1, d, seed=20261127):
    """Normal game differences: the exact sampling distributions, no game-level draws."""
    rng = np.random.default_rng(seed)
    n1, n2 = d["n1"], d["n2"]
    nb = n2 - n1
    b1 = RG.t_ppf(RG.norm_cdf(d["z1"]), n1 - 1)
    b2 = RG.t_ppf(RG.norm_cdf(d["z2"]), n2 - 1)
    r1 = rng.standard_t(n_r1 - 1, n_worlds) > d["r1_threshold_t"]
    m1 = rng.standard_normal(n_worlds) / math.sqrt(n1)
    mb = rng.standard_normal(n_worlds) / math.sqrt(nb)
    ss1 = rng.chisquare(n1 - 1, n_worlds)
    ss2 = ss1 + rng.chisquare(nb - 1, n_worlds) + (n1 * nb / n2) * (m1 - mb) ** 2
    t1 = m1 / np.sqrt(ss1 / (n1 - 1) / n1)
    t2 = ((n1 * m1 + nb * mb) / n2) / np.sqrt(ss2 / (n2 - 1) / n2)
    return _summ(r1, t1, t2, b1, b2)


def simulate_heavy(n_worlds, n_r1, d, df=3, seed=20261128, block=20000):
    """Each game's difference Student t on `df` degrees of freedom, game by game."""
    rng = np.random.default_rng(seed)
    n1, n2 = d["n1"], d["n2"]
    b1 = RG.t_ppf(RG.norm_cdf(d["z1"]), n1 - 1)
    b2 = RG.t_ppf(RG.norm_cdf(d["z2"]), n2 - 1)
    r1s, t1s, t2s = [], [], []
    for _ in range(n_worlds // block):
        x = rng.standard_t(df, (block, n_r1))
        r1s.append(x.mean(1) / (x.std(1, ddof=1) / math.sqrt(n_r1)) > d["r1_threshold_t"])
        y = rng.standard_t(df, (block, n2))
        a = y[:, :n1]
        t1s.append(a.mean(1) / (a.std(1, ddof=1) / math.sqrt(n1)))
        t2s.append(y.mean(1) / (y.std(1, ddof=1) / math.sqrt(n2)))
    return _summ(np.concatenate(r1s), np.concatenate(t1s), np.concatenate(t2s), b1, b2)


def _summ(r1, t1, t2, b1, b2):
    conf_any = (t1 > b1) | (t2 > b2)                       # futility ignored
    conf_fut = (t1 > b1) | ((t1 > 0) & (t2 > b2))          # futility obeyed: look 2 only when ahead at look 1
    return {"worlds": int(r1.size), "t_boundaries": [round(b1, 4), round(b2, 4)],
            "r1_criterion_4": _rate(r1),
            "confirmatory_alone": _rate(conf_any), "confirmatory_alone_with_futility": _rate(conf_fut),
            "gate_opens": _rate(~r1),
            "family_worst_case_gate": _rate(r1 | (~r1 & conf_any)),
            "family_worst_case_gate_with_futility": _rate(r1 | (~r1 & conf_fut))}


def main(argv):
    n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 4000000
    d = _design()
    out = {"design": d,
           "gate_assumption": "worst case: criteria 1, 2, 3 and 5 always pass, so the confirmatory test runs whenever "
                              "R1 fails criterion 4; any lower pass rate lowers the family rate",
           "independence": "the confirmatory games (2027) are disjoint from R1's (2026 weeks 13-18)",
           "normal_r1_at_min_games": simulate(n, d["r1_min_games"], d),
           "normal_r1_at_96_games": simulate(n, 96, d, seed=20261129),
           "heavy_tailed_t3_r1_at_min_games": simulate_heavy(min(n, 400000), d["r1_min_games"], d)}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
