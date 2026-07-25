"""Point-in-time backtest for the RACING models (F1 today, NASCAR-ready).

Racing needs a different question than the ball sports. ESPN publishes no
historical odds for motorsport, so "does the model beat the closing line?"
can't be asked. But for a DFS user that was never the important question —
lineups are built from PROJECTED FINISH, so what matters is whether our
predicted order actually matches how the race finishes.

So this measures ordering skill directly:
  * Spearman rank correlation between predicted and actual finishing order
  * how often the predicted winner wins, and the predicted top-5 lands top-5
  * mean absolute finish-position error
  * the same for a naive baseline (season-to-date average finish), because a
    model only earns its keep by beating the obvious alternative

Point-in-time throughout: race N is predicted using only races 1..N-1 of that
season. Nothing sees its own result or anything after it.
"""

import math
import random
from collections import defaultdict

import racing

ERGAST = "https://api.jolpi.ca/ergast/f1"


def f1_season(year):
    """[{round, name, results:[{driver, grid, position, status}]}] for a season."""
    def build():
        races = []
        for off in (0, 100, 200, 300, 400, 500):
            try:
                d = racing._get_json(
                    f"{ERGAST}/{year}/results.json?limit=100&offset={off}", timeout=25)
            except Exception:
                break
            rs = d.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            if not rs:
                break
            races.extend(rs)
            tot = int(d.get("MRData", {}).get("total") or 0)
            if off + 100 >= tot:
                break
        # Ergast pages split a race across pages; merge by round.
        merged = {}
        for r in races:
            rd = int(r.get("round") or 0)
            m = merged.setdefault(rd, {"round": rd, "name": r.get("raceName"),
                                       "date": r.get("date"), "results": []})
            for res in r.get("Results", []):
                m["results"].append({
                    "driver": res["Driver"]["driverId"],
                    "grid": int(res.get("grid") or 0),
                    "position": int(res.get("position") or 99),
                    "status": res.get("status", "")})
        return [merged[k] for k in sorted(merged)]
    return racing._cached(("f1_bt_season", year), 30 * 86400, build) or []


def _spearman(a, b):
    """Rank correlation between two equal-length sequences of positions."""
    n = len(a)
    if n < 3:
        return None
    ra = {v: i for i, v in enumerate(sorted(range(n), key=lambda i: a[i]))}
    rb = {v: i for i, v in enumerate(sorted(range(n), key=lambda i: b[i]))}
    d2 = sum((ra[i] - rb[i]) ** 2 for i in range(n))
    return round(1 - (6.0 * d2) / (n * (n * n - 1)), 4)


def run(year=2025, min_races=3, sims=400, seed=7):
    """Replay a season race by race and score predicted vs actual finish order."""
    import racing_sim
    races = f1_season(year)
    if len(races) <= min_races:
        return {"error": f"not enough races for {year}"}
    rng = random.Random(seed)
    # Running point-in-time form: finishes and DNFs from PRIOR races only.
    fin, starts, dnf, grid = defaultdict(list), defaultdict(int), defaultdict(int), defaultdict(list)
    rows = []
    for r in races:
        res = [x for x in r["results"] if x["driver"]]
        if not res:
            continue
        eligible = [x for x in res if starts[x["driver"]] >= min_races]
        if len(eligible) >= 8:
            lg_fin = 10.5
            drivers = []
            for x in eligible:
                d = x["driver"]
                f = sum(fin[d]) / len(fin[d]) if fin[d] else lg_fin
                g = sum(grid[d]) / len(grid[d]) if grid[d] else lg_fin
                drivers.append({
                    "id": d,
                    # quali pace on a grid scale; race pace on a finish scale —
                    # the same shapes racing_sim's engine consumes.
                    "quali": g / 10.0,
                    "race": f,
                    "dnf": min(0.5, (dnf[d] + 4 * 0.09) / (starts[d] + 4)),
                    "race_by_type": {},
                })
            # Predict with the REAL engine: simulate the race many times from a
            # simulated grid and average each driver's finishing position.
            tally = defaultdict(float)
            for _ in range(sims):
                q = racing_sim._sim_quali(drivers, rng)
                order = racing_sim._sim_race(drivers, q, rng)
                for pos, did in enumerate(order, 1):
                    tally[did] += pos
            pred = {d: tally[d] / sims for d in tally}
            actual = {x["driver"]: x["position"] for x in eligible}
            ids = [d for d in pred if d in actual]
            if len(ids) >= 8:
                pv = [pred[d] for d in ids]
                av = [actual[d] for d in ids]
                base = [sum(fin[d]) / len(fin[d]) if fin[d] else lg_fin for d in ids]
                pw = min(ids, key=lambda d: pred[d])
                bw = min(ids, key=lambda d: base[ids.index(d)])
                top5_pred = set(sorted(ids, key=lambda d: pred[d])[:5])
                top5_act = set(sorted(ids, key=lambda d: actual[d])[:5])
                rows.append({
                    "race": r["name"], "n": len(ids),
                    "rho": _spearman(pv, av),
                    "rho_base": _spearman(base, av),
                    "mae": sum(abs(p - a) for p, a in zip(pv, av)) / len(ids),
                    "mae_base": sum(abs(p - a) for p, a in zip(base, av)) / len(ids),
                    "win_hit": 1 if actual[pw] == 1 else 0,
                    "win_hit_base": 1 if actual[bw] == 1 else 0,
                    "top5_hits": len(top5_pred & top5_act),
                })
        # fold this race into history AFTER predicting it
        for x in res:
            d = x["driver"]
            starts[d] += 1
            grid[d].append(x["grid"] or 11)
            st = x["status"]
            if st == "Finished" or st.startswith("+"):
                fin[d].append(x["position"])
            else:
                dnf[d] += 1
                fin[d].append(min(20, x["position"]))
    if not rows:
        return {"error": "no races scored"}
    n = len(rows)

    def avg(k):
        vals = [r[k] for r in rows if r.get(k) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None
    return {
        "sport": "f1", "year": year, "races_scored": n,
        "model": {"spearman": avg("rho"), "mae_positions": avg("mae"),
                  "winner_hit_rate": avg("win_hit"),
                  "top5_of_5": avg("top5_hits")},
        "baseline_avg_finish": {"spearman": avg("rho_base"),
                                "mae_positions": avg("mae_base"),
                                "winner_hit_rate": avg("win_hit_base")},
        "verdict": ("model beats the naive season-average baseline"
                    if (avg("rho") or 0) > (avg("rho_base") or 0)
                    else "model does not beat a season-average baseline"),
    }
