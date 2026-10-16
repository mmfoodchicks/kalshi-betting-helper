"""Walk-forward validation of the NFL deep engine on a past season. RESEARCH.

For every regular-season week w from --from-week on, the team profiles are
built from the season's games BEFORE week w only (exactly what production
would have known), with the previous season's units as the prior and NO
market anchor -- so this measures the engine and its unit model alone, not
Kalshi's win totals. Every game of week w is then simulated n times and the
engine's home win probability and expected margin are scored against what
happened: log loss, Brier, accuracy, a calibration table, margin MAE/RMSE and
the slope of actual on predicted margin (1.0 = the spread is on scale).

Two baselines sit beside it: the constant home rate (0.54), and the ratings
formula nfl_season already uses in-season (8.5 + 0.5 x points differential per
game, blended toward 8.5 at games/(games+6), through _wp) -- the model the
deep engine has to beat to be worth its cost.

Usage:
  python3 -m research.nfl_deep_validate <store dir> [--season 2025] [--prior 2024] [--n 300]
      [--from-week 2] [--out research/reports/nfl_deep_validation.md]
"""
import collections
import datetime
import math
import os
import pickle
import sys
import time

import nfl_pbp as P
import nfl_deep_data as D
import nfl_deep_sim as E
import nfl_season

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "nfl_deep_validation.md")


def _load(store_dir, season):
    with open(os.path.join(store_dir, f"nfl_pbp_{season}.pkl"), "rb") as fh:
        st = pickle.load(fh)
    return st["payload"] if "payload" in st else st


def _subset(store, before_week):
    games = {gid: r for gid, r in store["games"].items() if r.get("st") == 2 and r.get("week", 99) < before_week}
    return {"games": games, "events": [e for e in store["events"] if e.get("st") == 2 and e.get("week", 99) < before_week],
            "updated": store.get("updated")}


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


def _metrics(rows, key_p, key_m=None):
    """rows: dicts with actual home win (0/1, ties 0.5), p (home), margin."""
    n = len(rows)
    ll = br = acc = 0.0
    mae = se = 0.0
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
        sxy = sum((r[key_m] - mx) * (r["margin"] - my) for r in rows)
        out["margin_slope"] = round(sxy / sxx, 3)
    return out


def _calibration(rows, key_p):
    bins = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for r in rows:
        p = r[key_p]
        # fold to the favourite so each bin is "the side we liked"
        fav_p, y = (p, r["y"]) if p >= 0.5 else (1 - p, 1 - r["y"])
        b = min(9, int(fav_p * 10))
        c = bins[b]
        c[0] += 1; c[1] += fav_p; c[2] += y
    out = []
    for b in sorted(bins):
        n, sp, sy = bins[b]
        out.append({"bin": f"{b/10:.1f}-{(b+1)/10:.1f}", "n": n, "pred": round(sp / n, 3), "real": round(sy / n, 3)})
    return out


def run(store_dir, season, prior, n, from_week, log=print, anchor=None):
    """`anchor` None = the engine alone; "pd" = the engine anchored (at
    nfl_deep_data's 6/(6+g) weight) to the points-differential ratings, which
    is what production does with Kalshi's win totals -- the test of whether
    the unit model adds anything on top of a rating."""
    T = E.tables()
    cur = _load(store_dir, season)
    prev = _load(store_dir, prior)
    T.raw["team_prior"] = {"season": int(prior), "units": P.team_units(prev, seasontype=2)}
    rows = []
    t0 = time.time()
    for w in range(from_week, 19):
        sub = _subset(cur, w)
        rt = _pd_rating(cur, w)
        profs, _q = D.build_profiles(season, store=sub, T=T, anchor=False)
        if anchor == "pd":
            _orig = D.market_margins
            D.market_margins = lambda _s, _rt=rt: ({ab: (r - 8.5) * nfl_season._PTS_PER_WIN for ab, r in _rt.items()}, "pd_ratings")
            try:
                D.apply_anchor(profs, season, T)
            finally:
                D.market_margins = _orig
        games = [r for r in cur["games"].values() if r.get("st") == 2 and r.get("week") == w]
        for r in games:
            h, a = r["home"], r["away"]
            if h not in profs or a not in profs:
                continue
            ev = next((e for e in cur["events"] if e["id"] == r["id"]), {})
            res = E.simulate(profs[h], profs[a], n=n, seed=int(r["id"]) % (1 << 30), neutral=bool(ev.get("neutral")), T=T)
            y = 1.0 if r["home_pts"] > r["away_pts"] else 0.0 if r["home_pts"] < r["away_pts"] else 0.5
            rows.append({"week": w, "home": h, "away": a, "y": y, "margin": r["home_pts"] - r["away_pts"],
                         "total": r["home_pts"] + r["away_pts"],
                         "p": res["p_home_raw"] + 0.5 * res["p_tie"], "m": res["mean_margin"], "t": res["exp_total"],
                         "p_pd": nfl_season._wp(rt[h], rt[a], 0.0 if ev.get("neutral") else 2.1),
                         "p_const": 0.54, "g_home": profs[h]["g"]})
        log(f"[validate] week {w}: {len(games)} games, {len(rows)} scored, {time.time()-t0:.0f}s")
    return rows


def report(rows, season, prior, n, from_week, anchor=None):
    L = [f"# NFL deep engine -- walk-forward validation, {season} regular season"
         + (" (anchored to the points-differential ratings)" if anchor == "pd" else " (engine alone)"),
         f"Built {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
         f"Profiles from the games before each week only (prior: {prior} units), "
         + ("levels anchored to the PD ratings at 6/(6+games) as production anchors to Kalshi; "
            if anchor == "pd" else "no anchor; ")
         + f"{n} simulated games per real game; weeks {from_week}-18; {len(rows)} games.", ""]
    L.append("## Whole span")
    L.append("| model | n | log loss | Brier | accuracy | margin MAE | margin RMSE | slope actual~pred |")
    L.append("|---|---|---|---|---|---|---|---|")
    m = _metrics(rows, "p", "m")
    L.append(f"| deep engine | {m['n']} | {m['log_loss']} | {m['brier']} | {m['accuracy']} | {m['margin_mae']} | {m['margin_rmse']} | {m['margin_slope']} |")
    for key, name in (("p_pd", "points-differential ratings (nfl_season._wp)"), ("p_const", "constant home 0.54")):
        m2 = _metrics(rows, key)
        L.append(f"| {name} | {m2['n']} | {m2['log_loss']} | {m2['brier']} | {m2['accuracy']} | - | - | - |")
    L.append("")
    L.append("## By stretch of the season")
    L.append("| weeks | n | deep log loss | deep Brier | deep acc | PD log loss | PD Brier | deep margin MAE | slope |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for lo, hi in ((from_week, 6), (7, 12), (13, 18)):
        sub = [r for r in rows if lo <= r["week"] <= hi]
        if not sub:
            continue
        a = _metrics(sub, "p", "m"); b = _metrics(sub, "p_pd")
        L.append(f"| {lo}-{hi} | {a['n']} | {a['log_loss']} | {a['brier']} | {a['accuracy']} | {b['log_loss']} | {b['brier']} | {a['margin_mae']} | {a['margin_slope']} |")
    L.append("")
    L.append("## Calibration (favourite's side), deep engine")
    L.append("| predicted | n | mean predicted | realized |")
    L.append("|---|---|---|---|")
    for c in _calibration(rows, "p"):
        L.append(f"| {c['bin']} | {c['n']} | {c['pred']} | {c['real']} |")
    tot_pred = sum(r["t"] for r in rows) / len(rows); tot_real = sum(r["total"] for r in rows) / len(rows)
    L.append("")
    L.append(f"Totals: predicted mean {tot_pred:.2f}, real {tot_real:.2f}; "
             f"total MAE {sum(abs(r['t'] - r['total']) for r in rows)/len(rows):.2f}.")
    L.append("")
    L.append("Reading: log loss below the ratings baseline is the engine earning its matchup "
             "structure; a margin slope near 1.0 means its spreads are on scale (below 1 = too "
             "confident, above = too timid). The calibration table is the honesty check: a bin's "
             "realized rate should sit near its mean prediction.")
    return "\n".join(L)


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__); return 2
    store_dir = argv[0]
    season, prior, n, from_week, out = 2025, 2024, 300, 2, OUT_DEFAULT
    anchor = None
    i = 1
    while i < len(argv):
        if argv[i] == "--season":
            season = int(argv[i + 1]); i += 2
        elif argv[i] == "--prior":
            prior = int(argv[i + 1]); i += 2
        elif argv[i] == "--n":
            n = int(argv[i + 1]); i += 2
        elif argv[i] == "--from-week":
            from_week = int(argv[i + 1]); i += 2
        elif argv[i] == "--out":
            out = argv[i + 1]; i += 2
        elif argv[i] == "--anchor":
            anchor = argv[i + 1]; i += 2
        else:
            i += 1
    rows = run(store_dir, season, prior, n, from_week, anchor=anchor)
    rep = report(rows, season, prior, n, from_week, anchor=anchor)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        fh.write(rep + "\n")
    print(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
