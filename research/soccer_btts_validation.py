"""Both teams to score: is the goals model's P(BTTS) worth showing as a
probability? Tested out of sample on 2025 and confirmed on 2026. RESEARCH.

The model is the shipped one, frozen (soccer.XI, soccer.KAPPA, soccer.RHO,
form and head-to-head at weight 0): nothing is chosen here, so there is no
selection year. P(BTTS) is read off the same scoreline grid the three-way
price comes from -- P(home >= 1 and away >= 1) -- the figure the card has
shown since the tab shipped, untested until now: the three-way validation
scored the result, not the split of the goals.

THE RULE (written before any number below was computed)
  Walk-forward by WEEK in production's window (soccer_form_validation's
  construction: fitted on every match from 1 January of the year before the
  week up to the week's first day, predicting every match in the week).
  Benchmark: the competition's own decayed BTTS frequency in the same window
  (weights exp(-XI x age), Laplace +1/+2), the global rate where a
  competition has none.
  1. TEST on 2025: the model's mean BTTS log loss must beat the benchmark's
     by more than two standard errors of the per-match difference.
  2. CONFIRM on 2026 (1 January to the data's last week): the model must not
     be worse than the benchmark by more than two standard errors.
  Both, and the board prices Kalshi's BTTS markets as a probability with an
  edge; otherwise it shows the model's figure as information and no edge.
  Reported beside it: calibration by tenth, the Brier score, and the matches
  where both sides were in the fit.

RESULT OF THE RULE ABOVE (2026-10-17): it FAILED. The model's BTTS was worse
than the league's own rate on 2025 (+0.00381 +/- 0.00125 log loss a match)
and on 2026 (+0.00632 +/- 0.00140), and too extreme both ways (said 26% ->
happened 36%; said 73% -> 66%).

RULE B (a second attempt, written after rule A failed and after 2025's and
2026's calibration had been read; so it is held to a stricter bar):
  Recalibrate: p' = logistic(a + b logit(p_model) + c logit(base)), (a, b, c)
  the maximum-likelihood fit on the 2024 weekly walk-forward (out of sample
  for the goals model itself; 2025 and 2026 play no part in the fit).
  Frozen, it ships as a probability with an edge only if it beats the league
  base rate by more than two standard errors on 2025 AND on 2026 -- both,
  since neither year is unseen any longer.

Usage: python3 -m research.soccer_btts_validation --rows DIR [--jobs N]
  DIR as research/soccer_form_validation.py's (the 2023-2026 result rows).
Writes research/data/soccer_btts_validation.json.
"""
import argparse
import datetime
import json
import math
import multiprocessing as mp
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import soccer  # noqa: E402
from research import soccer_form_validation as FV  # noqa: E402

TEST, CONFIRM = 2025, 2026
CALIB = 2024                     # rule B's fitting year
DATA = []


def btts_grid(lh, la):
    g = soccer.grid(lh, la)
    return sum(p for (x, y), p in g.items() if x > 0 and y > 0)


def one_week(args):
    start, end = args
    year = datetime.date.fromordinal(start).year
    lo = datetime.date(year - (soccer._FIT_YEARS - 1), 1, 1).toordinal()
    train = [m for m in DATA if lo <= m[0] < start]
    test = [m for m in DATA if start <= m[0] < end]
    if not train or not test:
        return []
    model = soccer.fit(train, start)
    acc, glob = {}, [0.0, 0.0]
    for m in train:
        w = math.exp(-soccer.XI * (start - m[0]))
        y = 1 if (m[4] > 0 and m[5] > 0) else 0
        a = acc.setdefault(m[1], [0.0, 0.0])
        a[0] += w * y
        a[1] += w
        glob[0] += w * y
        glob[1] += w
    g_rate = glob[0] / glob[1]
    out = []
    for day, comp, h, a, hg, ag, neu in test:
        lh, la = soccer.rates(model, h, a, comp, neu)
        b = acc.get(comp)
        out.append({"y": year, "comp": comp, "hit": 1 if (hg > 0 and ag > 0) else 0,
                    "known": h in model["att"] and a in model["att"],
                    "p": btts_grid(lh, la),
                    "base": (b[0] + 1.0) / (b[1] + 2.0) if b else g_rate})
    return out


def _init(data):
    DATA[:] = data


def ll(p, y):
    p = min(1 - 1e-6, max(1e-6, p))
    return -math.log(p if y else 1 - p)


def summary(rows):
    n = len(rows)
    d = [ll(r["p"], r["hit"]) - ll(r["base"], r["hit"]) for r in rows]
    m = sum(d) / n
    sd = (sum((x - m) ** 2 for x in d) / max(1, n - 1)) ** 0.5
    bins = []
    for lo in [i / 10 for i in range(10)]:
        b = [r for r in rows if lo <= r["p"] < lo + 0.1]
        if len(b) >= 50:
            bins.append({"pred": round(sum(r["p"] for r in b) / len(b), 4),
                         "hit": round(sum(r["hit"] for r in b) / len(b), 4), "n": len(b)})
    return {"n": n, "rate": round(sum(r["hit"] for r in rows) / n, 4),
            "logloss": round(sum(ll(r["p"], r["hit"]) for r in rows) / n, 5),
            "logloss_base": round(sum(ll(r["base"], r["hit"]) for r in rows) / n, 5),
            "minus_base": round(m, 5), "se": round(sd / n ** 0.5, 5),
            "brier": round(sum((r["p"] - r["hit"]) ** 2 for r in rows) / n, 5),
            "brier_base": round(sum((r["base"] - r["hit"]) ** 2 for r in rows) / n, 5),
            "calibration": bins}


def calib_fit(rows, iters=60):
    """(a, b, c): logistic regression of the outcome on logit(p_model) and
    logit(base), Newton's method."""
    def lg(p):
        p = min(1 - 1e-6, max(1e-6, p))
        return math.log(p / (1 - p))
    X = [(1.0, lg(r["p"]), lg(r["base"])) for r in rows]
    Y = [r["hit"] for r in rows]
    w = [0.0, 1.0, 0.0]
    for _ in range(iters):
        g = [0.0, 0.0, 0.0]
        H = [[0.0] * 3 for _ in range(3)]
        for x, y in zip(X, Y):
            q = 1.0 / (1.0 + math.exp(-sum(wi * xi for wi, xi in zip(w, x))))
            for i in range(3):
                g[i] += (y - q) * x[i]
                for j in range(3):
                    H[i][j] += q * (1 - q) * x[i] * x[j]
        # solve H d = g (3x3, Cramer)
        def det(M):
            return (M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1]) - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
                    + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]))
        D = det(H)
        d = []
        for k in range(3):
            M = [row[:] for row in H]
            for i in range(3):
                M[i][k] = g[i]
            d.append(det(M) / D)
        w = [wi + di for wi, di in zip(w, d)]
        if max(abs(x) for x in d) < 1e-9:
            break
    return w


def recal(w, r):
    def lg(p):
        p = min(1 - 1e-6, max(1e-6, p))
        return math.log(p / (1 - p))
    return 1.0 / (1.0 + math.exp(-(w[0] + w[1] * lg(r["p"]) + w[2] * lg(r["base"]))))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    data = FV.load(a.rows)
    last = datetime.date.fromordinal(max(m[0] for m in data))
    wk = list(FV.weeks(CALIB)) + list(FV.weeks(TEST)) + list(FV.weeks(CONFIRM, last_day=last + datetime.timedelta(days=1)))
    t0 = time.time()
    with mp.get_context("fork").Pool(a.jobs, initializer=_init, initargs=(data,)) as pool:
        rows = [r for part in pool.map(one_week, wk, chunksize=2) for r in part]
    res = {"rule": __doc__.split("Usage:")[0], "data_last_day": last.isoformat(), "seconds": round(time.time() - t0)}
    for y in (TEST, CONFIRM):
        ys = [r for r in rows if r["y"] == y]
        res[str(y)] = {"all": summary(ys), "known": summary([r for r in ys if r["known"]])}
    t, c = res[str(TEST)]["all"], res[str(CONFIRM)]["all"]
    passes = t["minus_base"] < -2 * t["se"]
    vetoed = c["minus_base"] > 2 * c["se"]
    res["decision"] = {"test_passes": passes, "confirm_vetoes": vetoed, "ship": passes and not vetoed}
    # rule B: the recalibration fitted on 2024 alone, then 2025 and 2026 both must pass
    w = calib_fit([r for r in rows if r["y"] == CALIB])
    res["recal"] = {"a": round(w[0], 5), "b": round(w[1], 5), "c": round(w[2], 5)}
    okB = True
    for y in (TEST, CONFIRM):
        ys = [dict(r, p=recal(w, r)) for r in rows if r["y"] == y]
        sB = summary(ys)
        res[f"recal_{y}"] = sB
        okB = okB and sB["minus_base"] < -2 * sB["se"]
    res["decision_b"] = {"ship": okB}
    # the per-competition view, both years pooled, n >= 100: where the model is worse than the base
    comps = {}
    for r in rows:
        comps.setdefault(r["comp"], []).append(r)
    res["weak"] = sorted(c_ for c_, rs in comps.items() if len(rs) >= 100
                         and sum(ll(r["p"], r["hit"]) - ll(r["base"], r["hit"]) for r in rs) / len(rs) > 0)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "soccer_btts_validation.json")
    with open(out, "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: res[k] for k in ("decision", "decision_b", "recal", "data_last_day")}, indent=1))
    for y in (TEST, CONFIRM):
        s = res[f"recal_{y}"]
        print("rule B", y, {k: s[k] for k in ("n", "logloss", "logloss_base", "minus_base", "se", "brier", "brier_base")})
        print("   calibration", s["calibration"])
    for y in (TEST, CONFIRM):
        s = res[str(y)]["all"]
        print(y, {k: s[k] for k in ("n", "rate", "logloss", "logloss_base", "minus_base", "se", "brier", "brier_base")})
        print("   calibration", s["calibration"])


if __name__ == "__main__":
    main(sys.argv[1:])
