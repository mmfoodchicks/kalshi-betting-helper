"""The soccer goals model, chosen on 2024 and tested on 2025 -- out of sample.

Walk-forward by month: for every month, the model is fitted on every
finished match BEFORE the month (weights relative to its first day, exactly
as production weights relative to today) and predicts every match IN the
month. Nothing a prediction sees post-dates it.

The rule, written before any 2025 number was read:
  1. On the 2024 months, pick (XI, KAPPA, RHO) from the grid below by the
     lowest mean three-way log loss over every predicted match.
  2. Freeze them. Score 2025 with them, once. The figures to quote are the
     2025 ones; 2024's are the selection and are reported as such.
  3. The model is worth showing as a probability if its 2025 log loss beats
     the league base-rate benchmark (the decayed home/draw/away frequencies
     of the match's own competition, from the same fitting window) by more
     than two standard errors of the per-match difference. If it does not,
     the tab shows the de-vigged Kalshi price alone and says so.

Benchmarks: the league base rate above, and the global 2024 rate. No odds
benchmark: ESPN's historical results carry no line (the DraftKings line is
on upcoming matches only), so the market comparison is prospective -- the
shadow bucket soccer_dk from the first day the board runs.

The owner's bet: NO on the underdog. Reported separately: the Brier score
and calibration of P(the underdog does not win), over every match and over
the heavy-underdog subset (the model's own underdog at least five points
under its draw, the owner's Kalshi rule in model terms).

Usage: python3 research/soccer_validation.py [--cache DIR] [--jobs N]
  --cache DIR  raw ESPN scoreboard rows as {slug}_{year}.json (the scratch
               download); without it every league-year is fetched from ESPN.
Writes research/data/soccer_validation.json; the report is written by hand
from it (research/reports/soccer_validation.md)."""
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

GRID_XI = (0.003, 0.005, 0.008)          # half-lives 231, 139, 87 days
GRID_KAPPA = (3.0, 6.0, 12.0)
GRID_RHO = (0.0, -0.06, -0.12)
# The first pass chose the edge of the grid on all three axes (xi 0.003,
# kappa 3, rho 0). This wider grid is a SECOND LOOK run after 2025 had been
# read once: its 2025 figures are a sensitivity check, not the test.
WIDE_XI = (0.001, 0.002, 0.003)
WIDE_KAPPA = (1.0, 2.0, 3.0)
WIDE_RHO = (0.04, 0.0, -0.04)
LEAGUES = sorted(set(soccer.ESPN_LEAGUE.values()) - {None} | set(soccer._LINK_LEAGUES))
YEARS = (2023, 2024, 2025)
SELECT_YEAR, TEST_YEAR = 2024, 2025


def load(cache):
    """Every finished 90-minute match of the leagues, 2023-2025, as
    soccer.results tuples."""
    out = []
    for slug in LEAGUES:
        for y in YEARS:
            p = os.path.join(cache, f"{slug}_{y}.json") if cache else None
            if p and os.path.exists(p):
                for r in json.load(open(p)):
                    if r.get("state") != "post" or r.get("status") not in soccer._ESPN_FINAL:
                        continue
                    try:
                        hg, ag = int(r["h"]["score"]), int(r["a"]["score"])
                        day = datetime.date.fromisoformat(r["date"][:10]).toordinal()
                    except (TypeError, ValueError, KeyError):
                        continue
                    out.append((day, slug, str(r["h"]["id"]), str(r["a"]["id"]), hg, ag,
                                bool(r.get("neutral")) or slug in soccer._NEUTRAL))
            else:
                out.extend(soccer.results(slug, y))
    out.sort()
    return out


def months(year):
    for m in range(1, 13):
        start = datetime.date(year, m, 1)
        end = datetime.date(year + (m == 12), (m % 12) + 1, 1)
        yield start.toordinal(), end.toordinal()


def base_rates(train, asof_ord, xi):
    """Decayed home/draw/away frequencies per competition and globally."""
    acc = {}
    g = [0.0, 0.0, 0.0]
    for day, comp, h, a, hg, ag, neutral in train:
        w = math.exp(-xi * (asof_ord - day))
        k = 0 if hg > ag else 1 if hg == ag else 2
        acc.setdefault(comp, [0.0, 0.0, 0.0])[k] += w
        g[k] += w
    out = {}
    for comp, v in acc.items():
        t = sum(v)
        out[comp] = [(x + 1.0) / (t + 3.0) for x in v] if t > 0 else None    # Laplace
    gt = sum(g)
    return out, [x / gt for x in g]


def logloss(p, k):
    return -math.log(max(1e-6, p[k]))


def rps(p, k):
    c = 0.0
    s = 0.0
    o = 0.0
    for i in range(2):
        c += p[i]
        o += 1.0 if k == i else 0.0
        s += (c - o) ** 2
    return s / 2.0


def one_month(args):
    """(xi, kappa, year, start, end, data) -> per-match rows for every rho."""
    xi, kappa, year, start, end, data = args
    train = [m for m in data if m[0] < start]
    test = [m for m in data if start <= m[0] < end]
    if not train or not test:
        return []
    model = soccer.fit(train, start, xi=xi, kappa=kappa)
    br, gb = base_rates(train, start, xi)
    rows = []
    for day, comp, h, a, hg, ag, neutral in test:
        k = 0 if hg > ag else 1 if hg == ag else 2
        lh, la = soccer.rates(model, h, a, comp, neutral)
        known = h in model["att"] and a in model["att"]
        rec = {"comp": comp, "k": k, "known": known, "base": br.get(comp) or gb, "glob": gb}
        for rho in GRID_RHO:
            g = soccer.grid(lh, la, rho)
            ph, pd, pa = soccer.outcome(g)
            rec[f"p{rho}"] = (ph, pd, pa)
        rows.append(rec)
    return [(xi, kappa, year, rows)]


def score(rows, rho):
    """Aggregate metrics for one (xi, kappa, rho)."""
    n = len(rows)
    if not n:
        return None
    key = f"p{rho}"
    ll = [logloss(r[key], r["k"]) for r in rows]
    lb = [logloss(r["base"], r["k"]) for r in rows]
    lg = [logloss(r["glob"], r["k"]) for r in rows]
    rp = [rps(r[key], r["k"]) for r in rows]
    rb = [rps(r["base"], r["k"]) for r in rows]
    d = [a - b for a, b in zip(ll, lb)]
    md = sum(d) / n
    se = (sum((x - md) ** 2 for x in d) / max(1, n - 1)) ** 0.5 / n ** 0.5
    # the owner's bet: P(underdog does not win), underdog = the model's lower side
    nd_b, nd_n, nd_cal = 0.0, 0, {}
    hv_b, hv_n, hv_cal = 0.0, 0, {}
    for r in rows:
        ph, pd, pa = r[key]
        dog = 2 if pa < ph else 0
        p_no = 1.0 - r[key][dog]
        hit = 0.0 if r["k"] == dog else 1.0
        nd_b += (p_no - hit) ** 2; nd_n += 1
        b = min(9, int(p_no * 10))
        c = nd_cal.setdefault(b, [0.0, 0.0, 0]); c[0] += p_no; c[1] += hit; c[2] += 1
        if r[key][dog] <= pd - 0.05:
            hv_b += (p_no - hit) ** 2; hv_n += 1
            c = hv_cal.setdefault(b, [0.0, 0.0, 0]); c[0] += p_no; c[1] += hit; c[2] += 1
    def cal(d):
        return {str(b / 10): {"pred": round(v[0] / v[2], 4), "hit": round(v[1] / v[2], 4), "n": v[2]}
                for b, v in sorted(d.items())}
    kn = [r for r in rows if r["known"]]
    return {"n": n, "logloss": round(sum(ll) / n, 5), "logloss_base": round(sum(lb) / n, 5),
            "logloss_global": round(sum(lg) / n, 5), "rps": round(sum(rp) / n, 5),
            "rps_base": round(sum(rb) / n, 5),
            "delta_vs_base": round(md, 5), "se": round(se, 5),
            "n_known": len(kn),
            "logloss_known": round(sum(logloss(r[key], r["k"]) for r in kn) / max(1, len(kn)), 5),
            "logloss_base_known": round(sum(logloss(r["base"], r["k"]) for r in kn) / max(1, len(kn)), 5),
            "no_dog": {"brier": round(nd_b / max(1, nd_n), 5), "n": nd_n, "calibration": cal(nd_cal)},
            "heavy": {"brier": round(hv_b / max(1, hv_n), 5), "n": hv_n, "calibration": cal(hv_cal),
                      "rule": "model underdog <= model draw - 0.05"}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--wide", action="store_true", help="the second-look grid (see WIDE_*)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "data", "soccer_validation.json"))
    a = ap.parse_args()
    global GRID_XI, GRID_KAPPA, GRID_RHO
    if a.wide:
        GRID_XI, GRID_KAPPA, GRID_RHO = WIDE_XI, WIDE_KAPPA, WIDE_RHO
        a.out = a.out.replace(".json", "_wide.json")
    t0 = time.time()
    data = load(a.cache)
    print(f"{len(data):,} matches, {len(LEAGUES)} competitions, {time.time() - t0:.0f}s", flush=True)
    # selection year: every grid point, every month
    jobs = [(xi, kappa, SELECT_YEAR, s, e, data)
            for xi in GRID_XI for kappa in GRID_KAPPA for s, e in months(SELECT_YEAR)]
    with mp.Pool(a.jobs) as pool:
        got = [r for rs in pool.map(one_month, jobs, chunksize=1) for r in rs]
    by = {}
    for xi, kappa, year, rows in got:
        by.setdefault((xi, kappa), []).extend(rows)
    sel = {}
    best = None
    for (xi, kappa), rows in by.items():
        for rho in GRID_RHO:
            sc = score(rows, rho)
            sel[f"{xi}|{kappa}|{rho}"] = sc
            if best is None or sc["logloss"] < best[0]:
                best = (sc["logloss"], xi, kappa, rho)
    _ll, xi, kappa, rho = best
    print(f"selected on {SELECT_YEAR}: xi={xi} kappa={kappa} rho={rho} logloss={_ll:.5f} "
          f"({time.time() - t0:.0f}s)", flush=True)
    # the test year, once, with the frozen parameters
    jobs = [(xi, kappa, TEST_YEAR, s, e, data) for s, e in months(TEST_YEAR)]
    with mp.Pool(a.jobs) as pool:
        got = [r for rs in pool.map(one_month, jobs, chunksize=1) for r in rs]
    rows = [r for _xi, _k, _y, rs in got for r in rs]
    test = score(rows, rho)
    # per-competition test log loss, for the report's table
    per = {}
    for r in rows:
        per.setdefault(r["comp"], []).append(r)
    per_comp = {c: {"n": len(rs), "logloss": round(sum(logloss(r[f"p{rho}"], r["k"]) for r in rs) / len(rs), 4),
                    "base": round(sum(logloss(r["base"], r["k"]) for r in rs) / len(rs), 4)}
                for c, rs in per.items() if len(rs) >= 50}
    out = {"rule": __doc__.split("Usage:")[0], "grid": {"xi": GRID_XI, "kappa": GRID_KAPPA, "rho": GRID_RHO},
           "select_year": SELECT_YEAR, "test_year": TEST_YEAR, "selection": sel,
           "chosen": {"xi": xi, "kappa": kappa, "rho": rho},
           "test": test, "test_by_competition": per_comp,
           "shipped": {"xi": soccer.XI, "kappa": soccer.KAPPA, "rho": soccer.RHO},
           "second_look": bool(a.wide),
           "leagues": LEAGUES, "n_matches": len(data), "ran": datetime.datetime.utcnow().isoformat() + "Z",
           "seconds": round(time.time() - t0)}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w"), indent=1)
    print(json.dumps({k: test[k] for k in ("n", "logloss", "logloss_base", "logloss_global", "rps",
                                            "rps_base", "delta_vs_base", "se")}, indent=1))
    print("no_dog brier", test["no_dog"]["brier"], "heavy", test["heavy"]["brier"], test["heavy"]["n"])
    print(f"done in {time.time() - t0:.0f}s -> {a.out}")


if __name__ == "__main__":
    main()
