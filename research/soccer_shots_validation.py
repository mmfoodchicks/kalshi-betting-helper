"""Shots as a rating input -- a free stand-in for expected goals: chosen on
2024, tested on 2025, confirmed on 2026, every match out of sample. RESEARCH.

The goals model rates teams on goals alone. Goals are a noisy count (a side
that outshoots its opponent 20-5 and loses 1-0 tells the fit it was the
worse team); shots on target are a steadier measure of the same thing, and
ESPN's scoreboards carry them for three quarters of the matches the fit
reads (soccer_rows with shots: 58,603 of 78,505, 2023-2026).

THE RULE (written before any number below was computed)
  The model is the shipped one (soccer.fit with XI, KAPPA, RHO; the weekly
  walk-forward in production's window, research/soccer_btts_validation.py's
  construction: fitted on every match from 1 January of the year before the
  week up to the week's first day, predicting every match in the week).
  One change: every match the FIT sees enters at PSEUDO-GOALS, per side,
      g' = (1 - w) g + w x,    x = C_ON * on_target + C_OFF * (shots - on_target)
  (C_ON, C_OFF) the least-squares fit, through the origin, of a side's goals
  on its shots on target and its other shots, over every 2023 match with
  shots, then scaled together so x averages exactly the 2023 goals (a fit
  through the origin need not, and a level gap would bias every scoring
  rate the pseudo-goals fit predicts) -- 2023 is the warm-up year of the
  2024 windows and is scored by none of the tests. A match without shots (no statistics, or extra time,
  whose shots cover 120 minutes) enters at its goals. Every prediction is of
  the real 90-minute result. Grid: w in {0, 0.25, 0.5, 0.75, 1}.
  1. SELECT on the 2024 weeks: the w with the lowest mean three-way log loss.
  2. TEST on 2025, once, w frozen: it ships only if it beats w = 0 by more
     than two standard errors of the per-match log-loss difference.
  3. CONFIRM on 2026 (1 January to the data's last week): a w that passed 2
     but is worse than w = 0 on 2026 by more than two standard errors ships
     at 0. 2026 cannot promote anything.
  4. The both-teams-to-score figure ships with the model it was tested on
     (soccer.BTTS_RECAL, fitted on the w = 0 model): a w that passed 2 and 3
     but makes the recalibrated BTTS figure worse than w = 0 on 2025 by more
     than two standard errors ships at 0.
  Reported beside it: each year's log loss for every w, and the 2025/2026
  differences on the matches where both sides had shots in the window.

Usage: python3 -m research.soccer_shots_validation --rows DIR [--jobs N]
  DIR holds {slug}_{year}.json = {"rows": [soccer.parse_scoreboard row +
  (home on target, away on target, home shots, away shots) or four Nones]}.
Writes research/data/soccer_shots_validation.json.
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

LEAGUES = sorted(set(soccer.ESPN_LEAGUE.values()) - {None} | set(soccer._LINK_LEAGUES))
GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
SELECT, TEST, CONFIRM = 2024, 2025, 2026
DATA = []        # (row7, shots4) sorted by day, set before the pool forks
COEF = []        # [C_ON, C_OFF]


def load(rows_dir):
    out = {}
    for slug in LEAGUES:
        for y in (2023, 2024, 2025, 2026):
            p = os.path.join(rows_dir, f"{slug}_{y}.json")
            if not os.path.exists(p):
                continue
            for r in json.load(open(p))["rows"]:
                row = tuple(r[:7])
                sh = tuple(r[7:11]) if len(r) >= 11 and r[7] is not None else None
                out[row] = sh               # a match listed twice keeps one copy
    return sorted(out.items())


def weeks(year, last_day=None):
    d = datetime.date(year, 1, 1)
    end = datetime.date(year + 1, 1, 1)
    if last_day:
        end = min(end, last_day)
    while d < end:
        yield d.toordinal(), min(end, d + datetime.timedelta(days=7)).toordinal()
        d += datetime.timedelta(days=7)


def conversion(data):
    """(C_ON, C_OFF): least squares through the origin of goals on (shots on
    target, other shots), per side, over every 2023 match with shots."""
    s11 = s12 = s22 = t1 = t2 = 0.0
    for row, sh in data:
        if sh is None or datetime.date.fromordinal(row[0]).year != 2023:
            continue
        for g, on, tot in ((row[4], sh[0], sh[2]), (row[5], sh[1], sh[3])):
            off = tot - on
            s11 += on * on; s12 += on * off; s22 += off * off
            t1 += on * g; t2 += off * g
    det = s11 * s22 - s12 * s12
    c_on, c_off = (t1 * s22 - t2 * s12) / det, (t2 * s11 - t1 * s12) / det
    gx = gg = 0.0
    for row, sh in data:
        if sh is None or datetime.date.fromordinal(row[0]).year != 2023:
            continue
        gg += row[4] + row[5]
        gx += c_on * (sh[0] + sh[1]) + c_off * (sh[2] - sh[0] + sh[3] - sh[1])
    k = gg / gx
    return [c_on * k, c_off * k]


def pseudo(row, sh, w):
    if not w or sh is None:
        return row
    xh = COEF[0] * sh[0] + COEF[1] * (sh[2] - sh[0])
    xa = COEF[0] * sh[1] + COEF[1] * (sh[3] - sh[1])
    return row[:4] + ((1 - w) * row[4] + w * xh, (1 - w) * row[5] + w * xa) + row[6:]


def _ll3(p, k):
    return -math.log(max(1e-12, p[k]))


def _llb(p, y):
    p = min(1 - 1e-6, max(1e-6, p))
    return -math.log(p if y else 1 - p)


def one_week(args):
    start, end = args
    year = datetime.date.fromordinal(start).year
    lo = datetime.date(year - (soccer._FIT_YEARS - 1), 1, 1).toordinal()
    train = [(r, s) for r, s in DATA if lo <= r[0] < start]
    test = [(r, s) for r, s in DATA if start <= r[0] < end]
    if not train or not test:
        return []
    # the league BTTS base rate in the same window, as production stores it
    acc, glob = {}, [0.0, 0.0]
    for r, _s in train:
        wt = math.exp(-soccer.XI * (start - r[0]))
        y = 1 if (r[4] > 0 and r[5] > 0) else 0
        a = acc.setdefault(r[1], [0.0, 0.0])
        a[0] += wt * y; a[1] += wt
        glob[0] += wt * y; glob[1] += wt
    teams_with_shots = set()
    for r, s in train:
        if s is not None:
            teams_with_shots.add(r[2]); teams_with_shots.add(r[3])
    models = {w: soccer.fit([pseudo(r, s, w) for r, s in train], start) for w in GRID}
    out = []
    for r, _s in test:
        day, comp, h, a, hg, ag, neu = r
        k = 0 if hg > ag else 1 if hg == ag else 2
        b = acc.get(comp)
        base = (b[0] + 1.0) / (b[1] + 2.0) if b else glob[0] / glob[1]
        rec = {"y": year, "known": h in teams_with_shots and a in teams_with_shots,
               "btts": 1 if (hg > 0 and ag > 0) else 0, "ll": {}, "llb": {}}
        for w, m in models.items():
            lh, la = soccer.rates(m, h, a, comp, neu)
            g = soccer.grid(lh, la, soccer.RHO)
            p = soccer.outcome(g)
            raw = sum(v for (x, y), v in g.items() if x > 0 and y > 0)
            rec["ll"][w] = _ll3(p, k)
            rec["llb"][w] = _llb(soccer.btts_cal(raw, base), rec["btts"])
        out.append(rec)
    return out


def _init(data, coef):
    DATA[:] = data
    COEF[:] = coef


def diff(rows, w, key="ll"):
    d = [r[key][w] - r[key][0.0] for r in rows]
    n = len(d)
    if not n:
        return None
    m = sum(d) / n
    sd = (sum((x - m) ** 2 for x in d) / max(1, n - 1)) ** 0.5
    return {"n": n, "mean": round(m, 5), "se": round(sd / n ** 0.5, 5)}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    data = load(a.rows)
    coef = conversion(data)
    last = datetime.date.fromordinal(max(r[0] for r, _s in data))
    wk = list(weeks(SELECT)) + list(weeks(TEST)) + list(weeks(CONFIRM, last_day=last + datetime.timedelta(days=1)))
    t0 = time.time()
    with mp.get_context("fork").Pool(a.jobs, initializer=_init, initargs=(data, coef)) as pool:
        rows = [r for part in pool.map(one_week, wk, chunksize=2) for r in part]
    res = {"rule": __doc__.split("Usage:")[0], "data_last_day": last.isoformat(),
           "n_rows": len(data), "n_with_shots": sum(1 for _r, s in data if s is not None),
           "coef": {"on_target": round(coef[0], 5), "off_target": round(coef[1], 5)},
           "seconds": round(time.time() - t0)}
    for y in (SELECT, TEST, CONFIRM):
        ys = [r for r in rows if r["y"] == y]
        res[str(y)] = {"n": len(ys),
                       "logloss": {str(w): round(sum(r["ll"][w] for r in ys) / len(ys), 5) for w in GRID},
                       "btts_logloss": {str(w): round(sum(r["llb"][w] for r in ys) / len(ys), 5) for w in GRID},
                       "minus_w0": {str(w): diff(ys, w) for w in GRID if w},
                       "minus_w0_known": {str(w): diff([r for r in ys if r["known"]], w) for w in GRID if w}}
    sel = res[str(SELECT)]["logloss"]
    chosen = min(GRID, key=lambda w: sel[str(w)])
    res["chosen"] = chosen
    dec = {"chosen": chosen}
    if chosen:
        t = diff([r for r in rows if r["y"] == TEST], chosen)
        c = diff([r for r in rows if r["y"] == CONFIRM], chosen)
        bt = diff([r for r in rows if r["y"] == TEST], chosen, key="llb")
        dec["test"] = t
        dec["test_passes"] = t["mean"] < -2 * t["se"]
        dec["confirm"] = c
        dec["confirm_vetoes"] = c["mean"] > 2 * c["se"]
        dec["btts_test"] = bt
        dec["btts_vetoes"] = bt["mean"] > 2 * bt["se"]
        dec["ship"] = chosen if (dec["test_passes"] and not dec["confirm_vetoes"] and not dec["btts_vetoes"]) else 0.0
    else:
        dec["ship"] = 0.0
    res["decision"] = dec
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "soccer_shots_validation.json")
    with open(out, "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: res[k] for k in ("coef", "chosen", "decision", "seconds", "n_rows", "n_with_shots")}, indent=1))
    for y in (SELECT, TEST, CONFIRM):
        print(y, res[str(y)]["n"], res[str(y)]["logloss"], res[str(y)]["minus_w0"])


if __name__ == "__main__":
    main(sys.argv[1:])
