"""Monte Carlo simulators (read-only — they touch no stored data).

Price simulator: projects a coin/commodity forward with many random GBM paths
and reports the distribution of where it lands — median, best/worst case, the
5–95% range, the chance it's up, and the chance it crosses a threshold. This is
the "run it for an hour and see the range of outcomes" view.
"""

import csv
import io
import math
import random

import odds


def _pois(lam):
    """Knuth Poisson sampler (fine for the small means in a baseball game)."""
    L = math.exp(-min(lam, 30))
    k, p = 0, 1.0
    while True:
        k += 1
        p *= random.random()
        if p <= L:
            return k - 1


def game_sim(er_home, er_away, n=20000):
    """Simulate a baseball game many times from each side's expected runs."""
    hw = aw = tie = blow = shut = 0
    totals = []
    scores = {}
    for _ in range(n):
        h, a = _pois(er_home), _pois(er_away)
        totals.append(h + a)
        if h > a: hw += 1
        elif a > h: aw += 1
        else: tie += 1
        if abs(h - a) >= 5: blow += 1
        if h == 0 or a == 0: shut += 1
        scores[(a, h)] = scores.get((a, h), 0) + 1
    totals.sort()
    def pct(p): return totals[min(n - 1, int(p * n))]
    top = sorted(scores.items(), key=lambda kv: -kv[1])[:5]
    return {
        "home_win_pct": round((hw + tie * 0.52) / n * 100, 1),
        "away_win_pct": round((aw + tie * 0.48) / n * 100, 1),
        "median_total": pct(0.5), "p10_total": pct(0.1), "p90_total": pct(0.9),
        "blowout_pct": round(blow / n * 100, 1), "shutout_pct": round(shut / n * 100, 1),
        "top_scores": [{"away": k[0], "home": k[1], "pct": round(v / n * 100, 1)} for k, v in top],
        "n": n,
    }


def temp_sim(mean, sigma, n=20000, threshold=None, direction=None):
    """Simulate the daily high from the weather model's mean +/- sigma."""
    draws = sorted(random.gauss(mean, sigma) for _ in range(n))
    def pct(p): return round(draws[min(n - 1, int(p * n))], 1)
    res = {"median": pct(0.5), "p10": pct(0.1), "p90": pct(0.9),
           "low": pct(0.02), "high": pct(0.98), "n": n}
    if threshold is not None:
        if direction == "below":
            res["prob_threshold"] = round(sum(1 for x in draws if x <= threshold) / n * 100, 1)
        else:
            res["prob_threshold"] = round(sum(1 for x in draws if x >= threshold) / n * 100, 1)
    return res


# ---- DFS (DraftKings) lineup optimizer + simulator ------------------------
def parse_dk_csv(text):
    """Parse a DraftKings DKSalaries.csv -> [{name, salary, proj, pos}]."""
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        name = (r.get("Name") or r.get("name") or "").strip()
        try:
            salary = float(r.get("Salary") or r.get("salary") or 0)
            proj = float(r.get("AvgPointsPerGame") or r.get("Projection")
                         or r.get("proj") or r.get("AvgPoints") or 0)
        except ValueError:
            continue
        if name and salary > 0:
            out.append({"name": name, "salary": salary, "proj": proj,
                        "pos": (r.get("Position") or r.get("Roster Position") or "").strip()})
    return out


def dfs_optimize(players, roster, cap):
    """Max-projection lineup of exactly `roster` players under the salary cap
    (0/1 knapsack with a cardinality constraint; salary in $100 units)."""
    U = int(cap // 100)
    dp = [[-1.0] * (U + 1) for _ in range(roster + 1)]
    back = [[None] * (U + 1) for _ in range(roster + 1)]
    dp[0][0] = 0.0
    for idx, pl in enumerate(players):
        su = int(round(pl["salary"] / 100))
        if su > U or su <= 0:
            continue
        pr = pl["proj"]
        for k in range(roster - 1, -1, -1):
            row = dp[k]
            for s in range(U - su, -1, -1):
                if row[s] >= 0 and row[s] + pr > dp[k + 1][s + su]:
                    dp[k + 1][s + su] = row[s] + pr
                    back[k + 1][s + su] = (idx, s)
    best, bs = -1.0, -1
    for s in range(U + 1):
        if dp[roster][s] > best:
            best, bs = dp[roster][s], s
    if bs < 0:
        return None
    chosen, k, s = [], roster, bs
    while k > 0:
        idx, ps = back[k][s]
        chosen.append(players[idx]); k -= 1; s = ps
    return chosen


def dfs_sim(lineup, n=20000, cv=0.55):
    """Monte Carlo a lineup's total DK points (per-player variance ~ cv)."""
    totals = []
    for _ in range(n):
        t = 0.0
        for p in lineup:
            t += max(0.0, random.gauss(p["proj"], p["proj"] * cv))
        totals.append(t)
    totals.sort()
    def pct(q): return round(totals[min(n - 1, int(q * n))], 1)
    return {"floor": pct(0.1), "median": pct(0.5), "ceiling": pct(0.9),
            "max": round(totals[-1], 1), "n": n}


def dfs_build(text, roster=6, cap=50000, sport="ufc"):
    players = parse_dk_csv(text)
    if len(players) < roster:
        return {"error": f"need at least {roster} players in the CSV (got {len(players)})"}
    lineup = dfs_optimize(players, roster, cap)
    if not lineup:
        return {"error": "no valid lineup fits the salary cap"}
    cv = {"nascar": 0.5, "f1": 0.5, "ufc": 0.6}.get(sport, 0.55)
    sim = dfs_sim(lineup, cv=cv)
    return {
        "lineup": [{"name": p["name"], "salary": int(p["salary"]), "proj": round(p["proj"], 1)} for p in lineup],
        "total_salary": int(sum(p["salary"] for p in lineup)),
        "total_proj": round(sum(p["proj"] for p in lineup), 1),
        "cap": cap, "roster": roster, "sim": sim, "pool": len(players),
    }


def price_sim(spot, candles, horizon, n=20000, threshold=None, direction=None):
    """Many random GBM paths over `horizon` (in the candles' time unit).

    Returns distribution stats + a histogram. `horizon` is in minutes for 1-min
    crypto candles, or in days for daily commodity candles (units just have to
    match the candles, which is how the odds engine already works).
    """
    mu, sigma, k = odds.estimate_params(candles)
    if sigma <= 0 or spot <= 0:
        return {"error": "not enough recent data to simulate"}
    drift = (mu - 0.5 * sigma * sigma) * horizon
    vol = sigma * math.sqrt(horizon)
    finals = sorted(spot * math.exp(drift + vol * random.gauss(0, 1)) for _ in range(n))

    def pct(p):
        return finals[min(n - 1, int(p * n))]

    up = sum(1 for f in finals if f > spot) / n
    res = {
        "spot": round(spot, 4), "n": n, "horizon": horizon,
        "median": round(pct(0.50), 4),
        "p1": round(pct(0.01), 4), "p5": round(pct(0.05), 4),
        "p25": round(pct(0.25), 4), "p75": round(pct(0.75), 4),
        "p95": round(pct(0.95), 4), "p99": round(pct(0.99), 4),
        "best": round(finals[-1], 4), "worst": round(finals[0], 4),
        "prob_up": round(up * 100, 1),
        "median_move_pct": round((pct(0.50) / spot - 1) * 100, 2),
        "best_case_move_pct": round((pct(0.95) / spot - 1) * 100, 2),
        "worst_case_move_pct": round((pct(0.05) / spot - 1) * 100, 2),
    }
    if threshold:
        if direction == "below":
            res["prob_threshold"] = round(sum(1 for f in finals if f <= threshold) / n * 100, 1)
        else:
            res["prob_threshold"] = round(sum(1 for f in finals if f >= threshold) / n * 100, 1)
        res["threshold"] = threshold
        res["direction"] = direction or "above"

    lo, hi = pct(0.02), pct(0.98)
    bins = 24
    width = (hi - lo) / bins if hi > lo else 1
    counts = [0] * bins
    for f in finals:
        if lo <= f <= hi:
            counts[min(bins - 1, int((f - lo) / width))] += 1
    res["hist"] = {"lo": round(lo, 4), "hi": round(hi, 4), "counts": counts}
    return res
