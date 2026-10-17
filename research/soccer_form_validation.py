"""Recent form and head-to-head on top of the soccer goals model:
chosen on 2024, tested on 2025, confirmed on 2026 -- every match out of sample.

THE RULE (written before any number below was computed)

Walk-forward by WEEK (production refits every six hours; a monthly refit
would hand form a staler signal than the board will actually have). For
each week starting d, every model is fitted on the matches in PRODUCTION'S
window -- from 1 January of the year before d up to d (soccer._FIT_YEARS) --
and predicts every match in [d, d + 7). The base model is the shipped one
(soccer.XI, soccer.KAPPA, soccer.RHO), unchanged.

Two candidate FORM terms, both a shift of each side's log scoring rate:
  A  a second, short-memory fit (half-life ln2/xi_s days) blended into the
     long one: log lam += w * (log lam_short - log lam_long).
  B  the last N matches' residual against the long fit (all competitions):
     r_att = log((goals + 2) / (expected + 2)), r_def = log((expected
     conceded + 2) / (conceded + 2)); log lam_home += w * (r_att(home) -
     r_def(away)) and the same for the away side. The +2 is two expected
     goals of prior, so a team with two lucky games is not a team in form.
One HEAD-TO-HEAD term: in the window's past meetings of the two teams (any
venue, any competition), each side's goals against its expected goals
under the current fit, r = log((goals + 2) / (expected + 2));
log lam_side += w_h * r_side.

Grid: A xi_s in {0.01, 0.02, 0.04} (half-lives 69, 35, 17 days),
B N in {5, 8}, form weight w in {0.1, 0.2, 0.3, 0.5}, and no form; H2H
weight w_h in {0, 0.25, 0.5, 1.0}. 84 combinations.

1. SELECT on the 2024 weeks: the combination with the lowest mean
   three-way log loss over every predicted match.
2. TEST on 2025, once, the chosen weights frozen:
   - the form term ships at its chosen weight only if [chosen form, no
     H2H] beats the base by more than two standard errors of the per-match
     log-loss difference; otherwise it ships at 0;
   - the H2H term ships at its chosen weight only if [chosen form, chosen
     H2H] beats [chosen form, no H2H] by more than two standard errors;
     otherwise 0. Each term earns its own place.
3. CONFIRM on 2026 (1 January to the data's last week): a term that passed
   2 but makes 2026 worse by more than two standard errors ships at 0, and
   the report says so. 2026 cannot promote anything.
Whatever ships at 0 is still SHOWN on the card -- the last five results and
the past meetings -- labelled as information the model gives no weight.

Also reported, for the record: the base model on all three years with the
extra-time correction (soccer._ninety) and weekly refits, and the
calibration of P(the underdog does not win) for what ships.

Usage: python3 research/soccer_form_validation.py --rows DIR [--jobs N]
  DIR holds {slug}_{year}.json = {"rows": soccer.parse_scoreboard rows,
  "names": ...} for every league soccer_validation.LEAGUES, 2023-2026.
Writes research/data/soccer_form_validation.json.
Numpy is used here for the scoring grid (research runs on a PC); the
production terms are pure Python in soccer.py."""
import argparse
import datetime
import json
import math
import multiprocessing as mp
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import soccer  # noqa: E402

LEAGUES = sorted(set(soccer.ESPN_LEAGUE.values()) - {None} | set(soccer._LINK_LEAGUES))
XI_SHORT = (0.01, 0.02, 0.04)
FORM_N = (5, 8)
W_FORM = (0.1, 0.2, 0.3, 0.5)
W_H2H = (0.0, 0.25, 0.5, 1.0)
PRIOR_GOALS = 2.0
SELECT, TEST, CONFIRM = 2024, 2025, 2026
DATA = []          # every row, sorted by day; set before the pool forks


def load(rows_dir):
    out = []
    for slug in LEAGUES:
        for y in (2023, 2024, 2025, 2026):
            p = os.path.join(rows_dir, f"{slug}_{y}.json")
            if os.path.exists(p):
                out.extend(tuple(r) for r in json.load(open(p))["rows"])
    out = sorted(set(out))
    return out


def weeks(year, last_day=None):
    d = datetime.date(year, 1, 1)
    end = datetime.date(year + 1, 1, 1)
    if last_day:
        end = min(end, last_day)
    while d < end:
        yield d.toordinal(), min(end, d + datetime.timedelta(days=7)).toordinal()
        d += datetime.timedelta(days=7)


def _xg(model, comp, h, a, neutral):
    lh, la = soccer.rates(model, h, a, comp, neutral)
    return lh, la


def one_week(args):
    start, end = args
    year = datetime.date.fromordinal(start).year
    lo = datetime.date(year - (soccer._FIT_YEARS - 1), 1, 1).toordinal()
    train = [m for m in DATA if lo <= m[0] < start]
    test = [m for m in DATA if start <= m[0] < end]
    if not train or not test:
        return []
    long_m = soccer.fit(train, start)
    shorts = {xs: soccer.fit(train, start, xi=xs) for xs in XI_SHORT}
    # every team's matches in the window, latest first, and every pair's meetings
    by_team, by_pair = {}, {}
    for m in reversed(train):
        day, comp, h, a, hg, ag, neu = m
        by_team.setdefault(h, []).append(m)
        by_team.setdefault(a, []).append(m)
        by_pair.setdefault((min(h, a), max(h, a)), []).append(m)
    # the league base rate: decayed home/draw/away frequencies of each
    # competition in the same window (Laplace), the benchmark the weak-cup
    # list is judged against
    br = {}
    for m in train:
        w = math.exp(-soccer.XI * (start - m[0]))
        k = 0 if m[4] > m[5] else 1 if m[4] == m[5] else 2
        br.setdefault(m[1], [0.0, 0.0, 0.0])[k] += w
    br = {c: [(x + 1.0) / (sum(v) + 3.0) for x in v] for c, v in br.items()}
    xg_cache = {}

    def xg(m):
        if m not in xg_cache:
            xg_cache[m] = _xg(long_m, m[1], m[2], m[3], m[6])
        return xg_cache[m]

    def form(t, n):
        gf = ga = xf = xa = 0.0
        for m in by_team.get(t, [])[:n]:
            lh, la = xg(m)
            if m[2] == t:
                gf += m[4]; ga += m[5]; xf += lh; xa += la
            else:
                gf += m[5]; ga += m[4]; xf += la; xa += lh
        return (math.log((gf + PRIOR_GOALS) / (xf + PRIOR_GOALS)),
                math.log((xa + PRIOR_GOALS) / (ga + PRIOR_GOALS)))

    def h2h(h, a):
        gh = ga_ = xh = xa = 0.0
        ms = by_pair.get((min(h, a), max(h, a)), [])
        for m in ms:
            lh, la = xg(m)
            if m[2] == h:
                gh += m[4]; ga_ += m[5]; xh += lh; xa += la
            else:
                gh += m[5]; ga_ += m[4]; xh += la; xa += lh
        return (math.log((gh + PRIOR_GOALS) / (xh + PRIOR_GOALS)),
                math.log((ga_ + PRIOR_GOALS) / (xa + PRIOR_GOALS)), len(ms))

    out = []
    for day, comp, h, a, hg, ag, neu in test:
        lh, la = soccer.rates(long_m, h, a, comp, neu)
        rec = {"y": year, "comp": comp, "k": 0 if hg > ag else 1 if hg == ag else 2,
               "known": h in long_m["att"] and a in long_m["att"],
               "lh": math.log(lh), "la": math.log(la), "short": {}, "form": {},
               "br": br.get(comp) or [0.45, 0.27, 0.28]}
        for xs, sm in shorts.items():
            sh, sa = soccer.rates(sm, h, a, comp, neu)
            rec["short"][xs] = (math.log(sh), math.log(sa))
        for n in FORM_N:
            fah, fdh = form(h, n)
            faa, fda = form(a, n)
            rec["form"][n] = (fah - fda, faa - fdh)
        rh, ra, nm = h2h(h, a)
        rec["h2h"] = (rh, ra, nm)
        out.append(rec)
    return out


def _probs(llh, lla, max_goals=soccer.MAX_GOALS):
    """(n, 3) home/draw/away from log rates, independent Poisson (RHO = 0
    shipped), renormalised over the 0..max_goals grid like soccer.grid."""
    lh, la = np.exp(llh), np.exp(lla)
    k = np.arange(max_goals + 1)
    lf = np.array([math.lgamma(x + 1) for x in k])
    ph = np.exp(-lh[:, None] + k[None, :] * np.log(lh)[:, None] - lf[None, :])
    pa = np.exp(-la[:, None] + k[None, :] * np.log(la)[:, None] - lf[None, :])
    g = ph[:, :, None] * pa[:, None, :]
    tot = g.sum(axis=(1, 2))
    tri = np.tril(np.ones((max_goals + 1, max_goals + 1)), -1)      # x > y
    p_h = (g * tri[None]).sum(axis=(1, 2)) / tot
    p_d = np.einsum("nii->n", g) / tot
    return np.stack([p_h, p_d, 1.0 - p_h - p_d], axis=1)


def combos():
    yield ("base", None, 0.0)
    for xs in XI_SHORT:
        for w in W_FORM:
            yield ("A", xs, w)
    for n in FORM_N:
        for w in W_FORM:
            yield ("B", n, w)


def rates_for(recs, form, w_h):
    kind, par, w = form
    lh = np.array([r["lh"] for r in recs])
    la = np.array([r["la"] for r in recs])
    if kind == "A":
        sh = np.array([r["short"][par][0] for r in recs])
        sa = np.array([r["short"][par][1] for r in recs])
        lh, la = lh + w * (sh - lh), la + w * (sa - la)
    elif kind == "B":
        lh = lh + w * np.array([r["form"][par][0] for r in recs])
        la = la + w * np.array([r["form"][par][1] for r in recs])
    if w_h:
        lh = lh + w_h * np.array([r["h2h"][0] for r in recs])
        la = la + w_h * np.array([r["h2h"][1] for r in recs])
    return lh, la


def losses(recs, form, w_h):
    lh, la = rates_for(recs, form, w_h)
    p = _probs(lh, la)
    k = np.array([r["k"] for r in recs])
    return -np.log(np.clip(p[np.arange(len(k)), k], 1e-6, 1.0)), p


def paired(a, b):
    """mean(a - b) and its standard error, per match."""
    d = a - b
    return float(d.mean()), float(d.std(ddof=1) / math.sqrt(len(d)))


def calib(recs, p):
    """P(the underdog does not win), the underdog the model's lower side."""
    k = np.array([r["k"] for r in recs])
    dog = np.where(p[:, 2] < p[:, 0], 2, 0)
    p_no = 1.0 - p[np.arange(len(k)), dog]
    hit = (k != dog).astype(float)
    heavy = p[np.arange(len(k)), dog] <= p[:, 1] - 0.05
    out = {}
    for name, mask in (("all", np.ones(len(k), bool)), ("heavy", heavy)):
        rows = []
        for lo in (0.6, 0.7, 0.8, 0.9):
            mm = mask & (p_no >= lo) & (p_no < lo + 0.1)
            if mm.sum():
                rows.append({"bucket": lo, "pred": round(float(p_no[mm].mean()), 4),
                             "hit": round(float(hit[mm].mean()), 4), "n": int(mm.sum())})
        out[name] = {"brier": round(float(((p_no - hit) ** 2)[mask].mean()), 5), "n": int(mask.sum()),
                     "buckets": rows}
    return out


def label(form, w_h):
    kind, par, w = form
    f = "no form" if kind == "base" else (f"A short fit xi={par} w={w}" if kind == "A" else f"B last {par} w={w}")
    return f"{f} | h2h w={w_h}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "data", "soccer_form_validation.json"))
    a = ap.parse_args()
    t0 = time.time()
    global DATA
    DATA = load(a.rows)
    last = datetime.date.fromordinal(max(m[0] for m in DATA) + 1)
    print(f"{len(DATA):,} matches, {len(LEAGUES)} competitions, last result {last - datetime.timedelta(days=1)}", flush=True)
    jobs = list(weeks(SELECT)) + list(weeks(TEST)) + list(weeks(CONFIRM, last_day=last))
    ctx = mp.get_context("fork")
    with ctx.Pool(a.jobs) as pool:
        recs = [r for rs in pool.imap(one_week, jobs, chunksize=1) for r in rs]
    print(f"walk-forward done: {len(recs):,} predictions ({time.time() - t0:.0f}s)", flush=True)
    by = {y: [r for r in recs if r["y"] == y] for y in (SELECT, TEST, CONFIRM)}
    # 1. select on 2024
    sel = {}
    best = None
    for form in combos():
        for w_h in W_H2H:
            ll, _p = losses(by[SELECT], form, w_h)
            sel[label(form, w_h)] = round(float(ll.mean()), 6)
            if best is None or ll.mean() < best[0]:
                best = (float(ll.mean()), form, w_h)
    _ll, form_c, wh_c = best
    print(f"chosen on {SELECT}: {label(form_c, wh_c)} ({_ll:.5f})", flush=True)
    base = ("base", None, 0.0)
    out = {"rule": __doc__.split("Usage:")[0], "n_matches": len(DATA),
           "last_result": (last - datetime.timedelta(days=1)).isoformat(),
           "selection": dict(sorted(sel.items(), key=lambda kv: kv[1])),
           "chosen": {"form": list(form_c), "h2h": wh_c, "label": label(form_c, wh_c)}, "years": {}}
    # 2-3. test and confirm
    for y in (SELECT, TEST, CONFIRM):
        rs = by[y]
        l_base, p_base = losses(rs, base, 0.0)
        l_form, p_form = losses(rs, form_c, 0.0)
        l_both, p_both = losses(rs, form_c, wh_c)
        l_h2h_only, _ = losses(rs, base, wh_c) if wh_c else (l_base, None)
        f_d, f_se = paired(l_form, l_base)
        h_d, h_se = paired(l_both, l_form) if wh_c else (0.0, 0.0)
        ho_d, ho_se = paired(l_h2h_only, l_base) if wh_c else (0.0, 0.0)
        nm = np.array([r["h2h"][2] for r in rs])
        out["years"][y] = {
            "n": len(rs), "logloss_base": round(float(l_base.mean()), 5),
            "logloss_form": round(float(l_form.mean()), 5), "logloss_both": round(float(l_both.mean()), 5),
            "form_vs_base": {"delta": round(f_d, 5), "se": round(f_se, 5)},
            "h2h_vs_form": {"delta": round(h_d, 5), "se": round(h_se, 5)},
            "h2h_alone_vs_base": {"delta": round(ho_d, 5), "se": round(ho_se, 5)},
            "share_with_meetings": round(float((nm > 0).mean()), 3),
            "mean_meetings": round(float(nm.mean()), 2),
            "calibration_base": calib(rs, p_base), "calibration_both": calib(rs, p_both)}
        # per competition: the candidate model against the league base rate
        k = np.array([r["k"] for r in rs])
        l_br = -np.log(np.clip(np.array([r["br"][int(kk)] for r, kk in zip(rs, k)]), 1e-6, 1.0))
        comps = {}
        for i, r in enumerate(rs):
            comps.setdefault(r["comp"], []).append(i)
        out["years"][y]["by_competition"] = {
            c: {"n": len(ix), "model": round(float(l_both[ix].mean()), 4), "base_rate": round(float(l_br[ix].mean()), 4)}
            for c, ix in sorted(comps.items()) if len(ix) >= 50}
        out["years"][y]["logloss_base_rate"] = round(float(l_br.mean()), 5)
        print(y, json.dumps({k: out["years"][y][k] for k in ("n", "logloss_base", "logloss_form", "logloss_both",
                                                              "form_vs_base", "h2h_vs_form", "h2h_alone_vs_base",
                                                              "share_with_meetings")}), flush=True)
    t, c = out["years"][TEST], out["years"][CONFIRM]
    form_ok = form_c[0] != "base" and t["form_vs_base"]["delta"] < -2 * t["form_vs_base"]["se"]
    h2h_ok = wh_c > 0 and t["h2h_vs_form"]["delta"] < -2 * t["h2h_vs_form"]["se"]
    form_vetoed = form_ok and c["form_vs_base"]["delta"] > 2 * c["form_vs_base"]["se"]
    h2h_vetoed = h2h_ok and c["h2h_vs_form"]["delta"] > 2 * c["h2h_vs_form"]["se"]
    out["decision"] = {"form_passes_test": form_ok, "h2h_passes_test": h2h_ok,
                       "form_vetoed_by_confirm": form_vetoed, "h2h_vetoed_by_confirm": h2h_vetoed,
                       "ship_form": list(form_c) if (form_ok and not form_vetoed) else ["base", None, 0.0],
                       "ship_h2h": wh_c if (h2h_ok and not h2h_vetoed) else 0.0}
    out["seconds"] = round(time.time() - t0)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w"), indent=1, default=str)
    print("decision:", json.dumps(out["decision"]))
    print(f"done in {out['seconds']}s -> {a.out}")


if __name__ == "__main__":
    main()
