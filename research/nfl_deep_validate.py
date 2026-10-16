"""Out-of-sample walk-forward validation of the NFL deep engine. RESEARCH.

For a past season S, the engine's tables are REFIT on the seasons BEFORE S only
(research/nfl_deep_fit.build_tables), so nothing the engine samples -- yardage
tables, exponents, shrinkage K, persistence -- has seen the games it is scored
on or anything after them. (The first version of this
script used the shipped tables, fit on 2023-2026, which include the season it
scored; review 2026-10-16.) The two engine constants _FORM_SD / _HFA_YDS are
global calibration targets (margin SD, home win rate) and stay as shipped.

For every week w >= --from-week the profiles are built from S's games BEFORE w
only, with S-1's units as the prior, and every game of week w is simulated n
times in each ARM on the same seeds:

  engine     the unit model alone, no anchor
  anch_g     levels anchored to the points-differential ratings at weight
             6/(6+games) (the first version's production weight) -- PD stands
             in for Kalshi's win totals, which have no history here
  anch_full  levels anchored to the PD ratings at weight 1 (production since
             2026-10-16, on this evidence)

and scored against what happened beside the PD ratings themselves
(nfl_season._wp on 8.5 + 0.5 x PD/g blended toward 8.5 at g/(g+6)), the
constant home rate 0.54, and BLENDS of each arm with the PD ratings at 30% --
the proxy for the production question "does putting the deep engine into a
rating-like headline at 0.30 help?" (the drive model itself cannot be
replayed: its Sleeper team projections are not archived).

Usage:
  python3 -m research.nfl_deep_validate <store dir> [--seasons 2024,2025] [--n 400] [--from-week 2]
      [--workers 4] [--out research/reports/nfl_deep_validation.md]
"""
import collections
import datetime
import math
import multiprocessing as mp
import os
import pickle
import sys
import time

import nfl_deep_data as D
import nfl_deep_sim as E
import nfl_season

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "nfl_deep_validation.md")
ARMS = ("engine", "anch_g", "anch_full")
BLEND = 0.30


def _load(store_dir, season):
    with open(os.path.join(store_dir, f"nfl_pbp_{season}.pkl"), "rb") as fh:
        st = pickle.load(fh)
    return st["payload"] if "payload" in st else st


def _subset(store, before_week):
    games = {gid: r for gid, r in store["games"].items() if r.get("st") == 2 and r.get("week", 99) < before_week}
    return {"games": games, "events": [e for e in store["events"] if e.get("st") == 2 and _w(e) < before_week],
            "updated": store.get("updated")}


def _w(e):
    try:
        return int(e.get("week") or 99)
    except (TypeError, ValueError):
        return 99


def _pd_rating(store, before_week):
    """nfl_season's in-season ratings from the record to date (no market, no
    roster layer): 8.5 blended toward 8.5 + 0.5 x PD/g at g/(g+6)."""
    pf, pa, g = collections.Counter(), collections.Counter(), collections.Counter()
    for r in store["games"].values():
        if r.get("st") != 2 or r.get("week", 99) >= before_week:
            continue
        pf[r["home"]] += r["home_pts"]; pa[r["home"]] += r["away_pts"]; g[r["home"]] += 1
        pf[r["away"]] += r["away_pts"]; pa[r["away"]] += r["home_pts"]; g[r["away"]] += 1
    out = {}
    for ab in nfl_season._DIV_OF:
        if g[ab]:
            live = 8.5 + (pf[ab] - pa[ab]) / g[ab] * nfl_season._WINS_PER_PD
            w = g[ab] / (g[ab] + 6.0)
            out[ab] = (1 - w) * 8.5 + w * live
        else:
            out[ab] = 8.5
    return out


_W = {}


def _init(store_dir, season, prior, fit_seasons):
    from research import nfl_deep_fit as F
    stores = F.load_stores(store_dir, fit_seasons)
    raw = F.build_tables(stores, prior_season=prior)
    _W["T"] = E.Tables(raw)
    _W["cur"] = _load(store_dir, season)
    _W["season"] = season


def _week(args):
    w, n = args
    T, cur, season = _W["T"], _W["cur"], _W["season"]
    sub = _subset(cur, w)
    rt = _pd_rating(cur, w)
    base, _q = D.build_profiles(season, store=sub, T=T, anchor=False)
    profs = {"engine": base}
    pd_margin = {ab: (r - 8.5) * nfl_season._PTS_PER_WIN for ab, r in rt.items()}
    orig_mm, orig_k = D.market_margins, D._ANCHOR_PRIOR_GAMES
    try:
        D.market_margins = lambda _s, recs=None: (dict(pd_margin), "pd_ratings")
        for arm, k in (("anch_g", 6.0), ("anch_full", None)):
            D._ANCHOR_PRIOR_GAMES = k
            pr = {ab: dict(p) for ab, p in base.items()}
            D.apply_anchor(pr, season, T)
            profs[arm] = pr
    finally:
        D.market_margins, D._ANCHOR_PRIOR_GAMES = orig_mm, orig_k
    events = {e["id"]: e for e in cur["events"]}
    rows = []
    for r in cur["games"].values():
        if r.get("st") != 2 or r.get("week") != w:
            continue
        h, a = r["home"], r["away"]
        if h not in base or a not in base:
            continue
        neutral = bool((events.get(r["id"]) or {}).get("neutral"))
        y = 1.0 if r["home_pts"] > r["away_pts"] else 0.0 if r["home_pts"] < r["away_pts"] else 0.5
        row = {"season": season, "week": w, "home": h, "away": a, "y": y,
               "margin": r["home_pts"] - r["away_pts"], "total": r["home_pts"] + r["away_pts"],
               "p_pd": nfl_season._wp(rt[h], rt[a], 0.0 if neutral else 2.1), "p_const": 0.54}
        for arm in ARMS:
            res = E.simulate(profs[arm][h], profs[arm][a], n=n, seed=int(r["id"]) % (1 << 30), neutral=neutral, T=T)
            row[f"p_{arm}"] = res["p_home_raw"] + 0.5 * res["p_tie"]
            row[f"m_{arm}"] = res["mean_margin"]
            row[f"t_{arm}"] = res["exp_total"]
        for arm in ARMS:
            row[f"p_blend_{arm}"] = BLEND * row[f"p_{arm}"] + (1 - BLEND) * row["p_pd"]
        rows.append(row)
    return rows


def run(store_dir, season, n, from_week, workers=4, fit_seasons=None, log=print):
    # strictly PAST seasons: the tables an engine would have had going into S
    fit_seasons = fit_seasons or [s for s in (2022, 2023, 2024, 2025, 2026)
                                  if s < season and os.path.exists(os.path.join(store_dir, f"nfl_pbp_{s}.pkl"))]
    t0 = time.time()
    with mp.Pool(workers, initializer=_init, initargs=(store_dir, season, season - 1, fit_seasons)) as pool:
        parts = pool.map(_week, [(w, n) for w in range(from_week, 19)])
    rows = [r for part in parts for r in part]
    log(f"[validate] {season}: {len(rows)} games, tables fit on {fit_seasons}, {time.time()-t0:.0f}s")
    return rows, fit_seasons


def _metrics(rows, key_p, key_m=None):
    n = len(rows)
    ll = br = acc = mae = se = 0.0
    for r in rows:
        p = min(1 - 1e-6, max(1e-6, r[key_p]))
        y = r["y"]
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        br += (p - y) ** 2
        acc += 1.0 if ((p >= 0.5) == (y >= 0.5)) else 0.0
        if key_m:
            mae += abs(r[key_m] - r["margin"]); se += (r[key_m] - r["margin"]) ** 2
    out = {"n": n, "log_loss": round(ll / n, 4), "brier": round(br / n, 4), "accuracy": round(acc / n, 4)}
    if key_m:
        out["margin_mae"] = round(mae / n, 3)
        out["margin_rmse"] = round(math.sqrt(se / n), 3)
        mx = sum(r[key_m] for r in rows) / n; my = sum(r["margin"] for r in rows) / n
        sxx = sum((r[key_m] - mx) ** 2 for r in rows) or 1e-9
        out["margin_slope"] = round(sum((r[key_m] - mx) * (r["margin"] - my) for r in rows) / sxx, 3)
    return out


def _paired(rows, a, b):
    """Mean per-game log-loss difference a - b with its standard error (negative = a better)."""
    d = []
    for r in rows:
        y = r["y"]
        pa, pb = min(1 - 1e-6, max(1e-6, r[a])), min(1 - 1e-6, max(1e-6, r[b]))
        la = -(y * math.log(pa) + (1 - y) * math.log(1 - pa))
        lb = -(y * math.log(pb) + (1 - y) * math.log(1 - pb))
        d.append(la - lb)
    n = len(d)
    m = sum(d) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1)) if n > 1 else 0.0
    return m, sd / math.sqrt(n) if n else 0.0


def _calibration(rows, key_p):
    bins = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for r in rows:
        p = r[key_p]
        fav_p, y = (p, r["y"]) if p >= 0.5 else (1 - p, 1 - r["y"])
        c = bins[min(9, int(fav_p * 10))]
        c[0] += 1; c[1] += fav_p; c[2] += y
    return [{"bin": f"{b/10:.1f}-{(b+1)/10:.1f}", "n": c[0], "pred": round(c[1] / c[0], 3), "real": round(c[2] / c[0], 3)}
            for b, c in sorted(bins.items())]


_LABEL = {"p_engine": "deep engine alone (no anchor)", "p_anch_g": "deep engine, PD-anchored at 6/(6+g)",
          "p_anch_full": "deep engine, PD-anchored at weight 1", "p_pd": "points-differential ratings (nfl_season._wp)",
          "p_const": "constant home 0.54", "p_blend_engine": "0.30 engine alone + 0.70 PD",
          "p_blend_anch_g": "0.30 anchored 6/(6+g) + 0.70 PD", "p_blend_anch_full": "0.30 anchored weight 1 + 0.70 PD"}


def report(rows_by_season, n, from_week):
    allrows = [r for rows, _ in rows_by_season.values() for r in rows]
    L = ["# NFL deep engine -- out-of-sample walk-forward validation",
         f"Built {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by research/nfl_deep_validate.py. "
         f"Each season is scored with tables REFIT ON THE SEASONS BEFORE IT; profiles from that season's games before each week only, the "
         f"previous season's units as the prior; {n} simulated games per real game per arm, weeks {from_week}-18. "
         + "; ".join(f"{s}: {len(rows)} games, tables fit on {fs}" for s, (rows, fs) in sorted(rows_by_season.items())) + ".",
         "", "PD = the points-differential ratings, standing in for Kalshi's win totals (which have no history here).", ""]
    for title, rows in [("All seasons", allrows)] + [(f"{s}", rs) for s, (rs, _f) in sorted(rows_by_season.items())]:
        L.append(f"## {title}")
        L.append("| model | n | log loss | Brier | accuracy | margin MAE | slope actual~pred |")
        L.append("|---|---|---|---|---|---|---|")
        for key in ("p_engine", "p_anch_g", "p_anch_full", "p_pd", "p_const", "p_blend_engine", "p_blend_anch_g", "p_blend_anch_full"):
            mk = ("m_" + key[2:]) if key in ("p_engine", "p_anch_g", "p_anch_full") else None
            m = _metrics(rows, key, mk)
            L.append(f"| {_LABEL[key]} | {m['n']} | {m['log_loss']} | {m['brier']} | {m['accuracy']} | "
                     f"{m.get('margin_mae', '-')} | {m.get('margin_slope', '-')} |")
        L.append("")
    L.append("## Paired log-loss differences, all seasons (per game; negative = the first is better; +/- one SE)")
    for a, b in (("p_engine", "p_pd"), ("p_anch_g", "p_pd"), ("p_anch_full", "p_pd"),
                 ("p_blend_engine", "p_pd"), ("p_blend_anch_g", "p_pd"), ("p_blend_anch_full", "p_pd"),
                 ("p_anch_full", "p_anch_g")):
        m, se = _paired(allrows, a, b)
        L.append(f"- {_LABEL[a]} minus {_LABEL[b]}: {m:+.4f} +/- {se:.4f}")
    L.append("")
    L.append("## Calibration (favourite's side), all seasons")
    L.append("| arm | predicted | n | mean predicted | realized |")
    L.append("|---|---|---|---|---|")
    for key in ("p_engine", "p_anch_full"):
        for c in _calibration(allrows, key):
            L.append(f"| {_LABEL[key]} | {c['bin']} | {c['n']} | {c['pred']} | {c['real']} |")
    tp = sum(r["t_engine"] for r in allrows) / len(allrows); tr = sum(r["total"] for r in allrows) / len(allrows)
    L.append("")
    L.append(f"Totals (engine alone): predicted mean {tp:.2f}, real {tr:.2f}; total MAE "
             f"{sum(abs(r['t_engine'] - r['total']) for r in allrows)/len(allrows):.2f}.")
    return "\n".join(L)


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__); return 2
    store_dir = argv[0]
    seasons, n, from_week, workers, out = [2024, 2025], 400, 2, 4, OUT_DEFAULT
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--seasons":
            seasons = [int(x) for x in argv[i + 1].split(",")]; i += 2
        elif a == "--n":
            n = int(argv[i + 1]); i += 2
        elif a == "--from-week":
            from_week = int(argv[i + 1]); i += 2
        elif a == "--workers":
            workers = int(argv[i + 1]); i += 2
        elif a == "--out":
            out = argv[i + 1]; i += 2
        else:
            i += 1
    res = {s: run(store_dir, s, n, from_week, workers) for s in seasons}
    rep = report(res, n, from_week)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        fh.write(rep + "\n")
    print(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
