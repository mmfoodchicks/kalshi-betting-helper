"""In-game adaptation of the play call, at the DECISION level (R2's second
hypothesis, research/reports/nfl_coach_r2_evidence.md sections A8 and C2).
RESEARCH: not in any engine. nflverse play-by-play (CC BY 4.0) through
research/nfl_coach_r2_data.py (R1's seal kept).

The audit found that an offence leans toward what has worked THIS game beyond
the score, clock, down and distance (nflverse's xpass) and its own season lean:
-0.227 +/- 0.013 pass-over-expected per unit of the success gap so far, while
the same gap over the REST of the game (a placebo: a call cannot see the
future) reads -0.009 +/- 0.013. So it is a response, not a game-level common
factor. Here it is written as a call model whose test is fixed before any test
snap is read:

    baseline   logit p = logit(xpass) + a0 + a1 * lean
    candidate  logit p = logit(xpass) + a0 + a1 * lean + b * gap

  lean  the offence's as-of neutral pass rate over expected this season
        (strictly earlier games), shrunk: sum / (snaps + 150) -- 150 snaps is
        the measured K (4.4 games) times ~34 neutral snaps a game
  gap   this game's earlier scrimmage snaps: (run successes - 0.42 runs) /
        (runs + 10) - (pass successes - 0.46 dropbacks) / (dropbacks + 10)
        (0.42 and 0.46 are the league's success rates; 10 is a shrink)

Snaps scored: neutral (downs 1-3, xpass known, win probability 0.2-0.8, more
than 2:00 left in the half, regulation) after the offence's 10th scrimmage
snap of the game, in a team's 4th game of the season on. Fitted on 2016-2023;
2024, 2025 and 2026 weeks 1-12 are development.

    python3 -m research.nfl_coach_r2_adapt <pbp dir>
"""
import json
import math
import os
import sys
from collections import defaultdict

from research import nfl_coach_r2_data as R

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "adapt_r2.json")
FIT_SEASONS = tuple(range(2016, 2024))
LEAN_K = 150.0
GAP_K = 10.0
SR_RUN, SR_PASS = 0.42, 0.46
MIN_PRIOR = 3
FIRST_SNAP = 10
_PASSK = (R.K_SCR, R.K_PASS, R.K_SACK)


def _neutral(p):
    return (p[R.Q_KIND] in R.SCRIMMAGE and 1 <= p[R.Q_DOWN] <= 3 and p[R.Q_XP] >= 0
            and 200 <= p[R.Q_WP] <= 800 and p[R.Q_HSR] > 120 and p[R.Q_PER] <= 4)


def rows(season_data):
    """[(team-game key, [(y, logit xpass, lean, gap), ...]), ...]."""
    bt = defaultdict(list)
    for g in season_data["games"]:
        if g["st"] != 2:
            continue
        for side in (0, 1):
            bt[g["home"] if side == 0 else g["away"]].append((g["week"], g, side))
    out = []
    for t, lst in bt.items():
        lst.sort(key=lambda x: x[0])
        lean = [0.0, 0]
        for ng, (wk, g, side) in enumerate(lst):
            h = 1 if side == 0 else 0
            pl = [p for p in g["plays"] if p[R.Q_OFFH] == h and p[R.Q_KIND] in R.SCRIMMAGE]
            L = lean[0] / (lean[1] + LEAN_K)
            rs, ps = [0, 0], [0, 0]
            game = []
            for n, p in enumerate(pl):
                if ng >= MIN_PRIOR and n >= FIRST_SNAP and _neutral(p):
                    gap = ((rs[0] - SR_RUN * rs[1]) / (rs[1] + GAP_K)
                           - (ps[0] - SR_PASS * ps[1]) / (ps[1] + GAP_K))
                    xp = min(0.999, max(0.001, p[R.Q_XP] / 1000.0))
                    game.append((1 if p[R.Q_KIND] in _PASSK else 0, math.log(xp / (1 - xp)), L, gap))
                if p[R.Q_KIND] == R.K_RUN:
                    rs[0] += p[R.Q_SUCC]
                    rs[1] += 1
                else:
                    ps[0] += p[R.Q_SUCC]
                    ps[1] += 1
            if game:
                out.append(((season_data["season"], wk, t), game))
            for p in pl:
                if _neutral(p):
                    lean[0] += (1 if p[R.Q_KIND] in _PASSK else 0) - p[R.Q_XP] / 1000.0
                    lean[1] += 1
    return out


def _feats(r, cand):
    return [1.0, r[2], r[3]] if cand else [1.0, r[2]]


def fit(team_games, cand, iters=25):
    """Logistic regression with logit(xpass) as an offset (Newton's method)."""
    import numpy as np
    X, off, y = [], [], []
    for _k, game in team_games:
        for r in game:
            X.append(_feats(r, cand))
            off.append(r[1])
            y.append(r[0])
    X = np.asarray(X)
    off = np.asarray(off)
    y = np.asarray(y, dtype=float)
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        eta = off + X @ b
        p = 1.0 / (1.0 + np.exp(-eta))
        g = X.T @ (y - p)
        H = (X * (p * (1 - p))[:, None]).T @ X
        b = b + np.linalg.solve(H, g)
    return [float(v) for v in b]


def score(team_games, coef0, coef1):
    """Per team-game: (baseline log loss, candidate log loss, snaps)."""
    out = []
    for _k, game in team_games:
        l0 = l1 = 0.0
        for r in game:
            e0 = r[1] + sum(c * f for c, f in zip(coef0, _feats(r, False)))
            e1 = r[1] + sum(c * f for c, f in zip(coef1, _feats(r, True)))
            for e, acc in ((e0, 0), (e1, 1)):
                p = 1.0 / (1.0 + math.exp(-e))
                ll = -math.log(p if r[0] else 1.0 - p)
                if acc == 0:
                    l0 += ll
                else:
                    l1 += ll
        out.append((l0, l1, len(game)))
    return out


def summarize(sc):
    d = [a - b for a, b, _ in sc]
    n = len(d)
    m = sum(d) / n
    se = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1) / n)
    snaps = sum(x[2] for x in sc)
    return {"team_games": n, "snaps": snaps, "gain_per_team_game": round(m, 5), "se": round(se, 5),
            "z": round(m / se, 2), "ll_baseline": round(sum(x[0] for x in sc) / snaps, 6),
            "ll_candidate": round(sum(x[1] for x in sc) / snaps, 6)}


def main(pbp):
    fitrows = []
    for s in FIT_SEASONS:
        fitrows += rows(R.load_season(pbp, s))
    c0 = fit(fitrows, False)
    c1 = fit(fitrows, True)
    out = {"fit_seasons": list(FIT_SEASONS), "coef_baseline": c0, "coef_candidate": c1,
           "lean_k": LEAN_K, "gap_k": GAP_K, "sr": [SR_RUN, SR_PASS],
           "credit": "nflverse-data play-by-play (CC BY 4.0)", "fit": summarize(score(fitrows, c0, c1)),
           "dev": {}}
    for s in (2024, 2025, 2026):
        out["dev"][str(s)] = summarize(score(rows(R.load_season(pbp, s)), c0, c1))
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1]), indent=1))
