"""Stage 2C: the reconciliation weights, fitted on 2022-2024 only, and the
reconciliation's effect on the training seasons' projections.

Two things are measured for nfl_recon:

  lambda, per identity -- the weight on the quarterback's figure in the
  blend T = lambda * Q + (1 - lambda) * S that best predicts the ACTUAL
  team quantity (least squares over the training team-weeks, clipped to
  [0, 1], a clustered bootstrap by team-season for the interval), with
  the mean squared error of each side alone, the blend and a plain
  average, so the report can show what the blend buys;

  the residual variance function, per position and component -- squared
  residual (actual minus projected) regressed on the projection level,
  var = a + b * m, which decides how a side's change is shared among its
  players (the least reliable numbers move the most).

Then every 2022-2024 team-week is reconciled with those weights and the
adjustments are reported: the gap distribution before and after, the
size of the changes by position and component, the DraftKings-point
shifts, the tripwires and the worst cases, an in-sample accuracy check
(does the reconciled mean sit closer to the actual than the original,
on the same seasons lambda was fitted to -- labelled as such), and the
identity, bound, idempotence and preservation tests. 2025 outcomes are
not read; 2025 projections are not touched until Stage 2E.

    python3 -m research.stage2c
"""
import collections
import json
import os
import sys
import time

import numpy as np

from research import hist_data as H
from research import hist_stats as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
REPORTS = os.path.join(ROOT, "research", "reports")
WEIGHTS = os.path.join(DATA, "reconcile_weights.json")
BASE_SEED = 20220902
# (identity, quarterback component, receivers' component, actual team quantity)
IDENT = (("pass_att", "pass_att", "rec_tgt", "rec_tgt"), ("pass_cmp", "pass_cmp", "rec", "pass_cmp"),
         ("pass_yd", "pass_yd", "rec_yd", "pass_yd"), ("pass_td", "pass_td", "rec_td", "pass_td"))
VAR_COMPS = {"QB": ("pass_att", "pass_cmp", "pass_yd", "pass_td"),
             "RB": ("rec_tgt", "rec", "rec_yd", "rec_td"), "WR": ("rec_tgt", "rec", "rec_yd", "rec_td"),
             "TE": ("rec_tgt", "rec", "rec_yd", "rec_td")}


def _commit():
    """Kept for the artifacts that only record a hash; `_prov()` is what new
    metadata blocks should carry. The commit alone was never enough: it names
    the tree the research code was sitting on, not the code that ran."""
    from research import provenance
    return provenance.commit()


def _prov(**extra):
    """commit, dirty flag, and a hash over the source that actually ran."""
    from research import provenance
    return provenance.stamp(**extra)


def team_weeks(rows, seasons):
    """Per (season, week, team): the projected quarterback figures, the
    projected receivers' sums and the actual team quantities."""
    by = collections.defaultdict(list)
    for r in rows:
        if r["season"] in seasons and r["keep"] and r["team"]:
            by[(r["season"], r["week"], r["team"])].append(r)
    out = []
    for key, rr in sorted(by.items()):
        pr = [r for r in rr if r["has_proj"] and (r["proj_dk"] or 0) > 0]
        qbs = [r for r in pr if r["pos"] == "QB" and (r["proj_pass_att"] or 0) > 0]
        played = [r for r in rr if r["has_act"]]
        if not qbs or not played:
            continue
        tw = {"key": key}
        for name, qc, rc, ac in IDENT:
            tw["Q_" + name] = sum((r[f"proj_{qc}"] or 0.0) for r in qbs)
            tw["S_" + name] = sum((r[f"proj_{rc}"] or 0.0) for r in pr if r["pos"] in ("RB", "WR", "TE", "QB"))
            tw["A_" + name] = sum((r[f"act_{ac}"] or 0.0) for r in played)
        tw["A_att"] = sum((r["act_pass_att"] or 0.0) for r in played)
        if tw["A_att"] < 10:
            continue                                   # a wildcat week is no evidence about passing books
        out.append(tw)
    return out


def _lambda(Q, S, A):
    d = Q - S
    den = float((d * d).sum())
    if den <= 0:
        return 0.5
    return float(min(1.0, max(0.0, ((A - S) * d).sum() / den)))


def fit_lambda(tws, rng, boots=400):
    """lambda per identity with a clustered (team-season) bootstrap; the
    quarterback side of targets is attempts times the measured factor."""
    att = np.asarray([t["A_att"] for t in tws])
    tgt = np.asarray([t["A_pass_att"] for t in tws])
    factor = float(tgt.sum() / att.sum())
    clusters = np.asarray([f"{t['key'][0]}-{t['key'][2]}" for t in tws])
    ucl = np.unique(clusters)
    out = {"target_factor": {"value": factor, "n": int(len(tws))}}
    fb = []
    for _ in range(boots):
        pick = rng.choice(len(ucl), size=len(ucl), replace=True)
        idx = np.concatenate([np.flatnonzero(clusters == ucl[i]) for i in pick])
        fb.append(float(tgt[idx].sum() / att[idx].sum()))
    out["target_factor"].update({"boot_lo": float(np.quantile(fb, 0.05)), "boot_hi": float(np.quantile(fb, 0.95))})
    for name, _, _, _ in IDENT:
        Q = np.asarray([t["Q_" + name] for t in tws]) * (factor if name == "pass_att" else 1.0)
        Sm = np.asarray([t["S_" + name] for t in tws])
        A = np.asarray([t["A_" + name] for t in tws])
        lam = _lambda(Q, Sm, A)
        bl = []
        for _ in range(boots):
            pick = rng.choice(len(ucl), size=len(ucl), replace=True)
            idx = np.concatenate([np.flatnonzero(clusters == ucl[i]) for i in pick])
            bl.append(_lambda(Q[idx], Sm[idx], A[idx]))
        per_season = {}
        for s in sorted({t["key"][0] for t in tws}):
            m = np.asarray([t["key"][0] == s for t in tws])
            per_season[str(s)] = _lambda(Q[m], Sm[m], A[m])
        mse = lambda pred: float(((A - pred) ** 2).mean())
        out[name] = {"value": lam, "n": int(len(A)), "boot_lo": float(np.quantile(bl, 0.05)), "boot_hi": float(np.quantile(bl, 0.95)),
                     "per_season": per_season, "mse_qb": mse(Q), "mse_sum": mse(Sm), "mse_average": mse(0.5 * (Q + Sm)),
                     "mse_blend": mse(lam * Q + (1 - lam) * Sm), "actual_mean": float(A.mean()),
                     "bias_qb": float((Q - A).mean()), "bias_sum": float((Sm - A).mean())}
    return out


def fit_variance(rows, seasons):
    """var = a + b * projection per position and component, on rows with a
    positive projection; a and b floored at zero (b at a small positive
    value so a larger projection never carries less variance)."""
    sc = S.scored(rows, seasons)
    out = {}
    for pos, comps in VAR_COMPS.items():
        out[pos] = {}
        for comp in comps:
            x = np.asarray([(r[f"proj_{comp}"] or 0.0) for r in sc if r["pos"] == pos])
            y = np.asarray([(r[f"act_{comp}"] or 0.0) for r in sc if r["pos"] == pos])
            m = x > 0
            if m.sum() < 100:
                continue
            res2 = (y[m] - x[m]) ** 2
            A = np.c_[np.ones(m.sum()), x[m]]
            a, b = np.linalg.lstsq(A, res2, rcond=None)[0]
            a, b = max(0.0, float(a)), max(1e-6, float(b))
            qs = np.quantile(x[m], [0.0, 0.25, 0.5, 0.75, 1.0])
            bins = []
            for lo, hi in zip(qs[:-1], qs[1:]):
                sel = (x[m] >= lo) & (x[m] <= hi)
                bins.append({"lo": round(float(lo), 2), "hi": round(float(hi), 2), "n": int(sel.sum()),
                             "resid_sd": float(np.sqrt(res2[sel].mean())), "fit_sd": float(np.sqrt(a + b * x[m][sel].mean()))})
            out[pos][comp] = {"a": a, "b": b, "n": int(m.sum()), "bins": bins}
    return out


def apply_training(rows, seasons, weights):
    """Reconcile every training team-week (projections only) and measure
    what moved; then the in-sample accuracy check against the actuals."""
    import nfl_recon
    by = collections.defaultdict(list)
    for r in rows:
        if r["season"] in seasons and r["keep"] and r["team"] and r["has_proj"] and (r["proj_dk"] or 0) > 0:
            by[(r["season"], r["week"], r["team"])].append(r)
    gaps_before = collections.defaultdict(list)
    gaps_after = collections.defaultdict(list)
    changes = collections.defaultdict(list)          # (pos, comp) -> (delta, rel)
    dk = collections.defaultdict(list)
    flags = collections.Counter()
    tripped = []
    acc = collections.defaultdict(lambda: {"orig": [], "recon": [], "n": 0})
    n_tw = 0
    for key, rr in sorted(by.items()):
        players = [{"name": r["name"], "pos": r["pos"], "row": r,
                    "means": {c: float(r[f"proj_{c}"] or 0.0) for c in ("pass_att", "pass_cmp", "pass_yd", "pass_td", "rec_tgt", "rec", "rec_yd", "rec_td")}}
                   for r in rr]
        rep = nfl_recon.reconcile_team(players, weights, team=key[2])
        n_tw += 1
        for fl in rep["flags"]:
            flags[fl] += 1
        if rep["tripped"]:
            tripped.append({"key": list(key), "gaps": {k: v["gap_rel"] for k, v in rep["identities"].items() if abs(v["gap_rel"]) > nfl_recon.TRIPWIRE[k]}})
        for name, v in rep["identities"].items():
            gaps_before[name].append(v["gap_rel"])
            gaps_after[name].append(v["after_gap"])
        for p in players:
            for c, v in p["recon"].items():
                m = p["means"][c]
                if m > 0 or v > 0:
                    changes[(p["pos"], c)].append((v - m, (v - m) / m if m > 0 else None))
            dk[p["pos"]].append(nfl_recon.dk_mean(_dkline(p["recon"], p["row"])) - nfl_recon.dk_mean(_dkline(p["means"], p["row"])))
            r = p["row"]
            if r["has_act"] and (r.get("act_gp") or 0) >= 1:
                for c in p["means"]:
                    a = r.get(f"act_{c}")
                    if a is None or p["means"][c] <= 0:
                        continue
                    acc[(p["pos"], c)]["orig"].append(abs(a - p["means"][c]))
                    acc[(p["pos"], c)]["recon"].append(abs(a - p["recon"][c]))
                    acc[(p["pos"], c)]["n"] += 1
    def q(xs, ps):
        xs = np.asarray(xs, dtype=np.float64)
        return {f"p{int(100 * p)}": float(np.quantile(xs, p)) for p in ps} if len(xs) else {}
    out = {"team_weeks": n_tw, "flags": dict(flags), "tripped_count": len(tripped), "tripped": tripped[:40],
           "gap_before": {k: {"abs_median": float(np.median(np.abs(v))), "abs_p95": float(np.quantile(np.abs(v), 0.95)), "max_abs": float(np.max(np.abs(v)))} for k, v in gaps_before.items()},
           "gap_after_max_abs": {k: float(np.max(np.abs(v))) for k, v in gaps_after.items()},
           "changes": {}, "dk_shift": {}, "accuracy_in_sample": {}}
    for (pos, c), xs in sorted(changes.items()):
        d = np.asarray([x[0] for x in xs]); rel = np.asarray([x[1] for x in xs if x[1] is not None])
        out["changes"][f"{pos}/{c}"] = {"n": int(len(d)), "abs_median": float(np.median(np.abs(d))), "abs_p90": float(np.quantile(np.abs(d), 0.9)),
                                        "abs_max": float(np.max(np.abs(d))), "mean": float(d.mean()),
                                        "rel_abs_median": float(np.median(np.abs(rel))) if len(rel) else None,
                                        "rel_abs_p90": float(np.quantile(np.abs(rel), 0.9)) if len(rel) else None,
                                        "rel_abs_max": float(np.max(np.abs(rel))) if len(rel) else None,
                                        "share_over_25pct": float((np.abs(rel) > 0.25).mean()) if len(rel) else None}
    for pos, xs in sorted(dk.items()):
        xs = np.asarray(xs)
        out["dk_shift"][pos] = {"n": int(len(xs)), "mean": float(xs.mean()), "abs_median": float(np.median(np.abs(xs))),
                                "abs_p90": float(np.quantile(np.abs(xs), 0.9)), "abs_max": float(np.max(np.abs(xs))),
                                "share_over_1pt": float((np.abs(xs) > 1.0).mean())}
    for (pos, c), v in sorted(acc.items()):
        if v["n"] < 100:
            continue
        o, r = np.asarray(v["orig"]), np.asarray(v["recon"])
        out["accuracy_in_sample"][f"{pos}/{c}"] = {"n": v["n"], "mae_orig": float(o.mean()), "mae_recon": float(r.mean()),
                                                   "mae_change_pct": float(100.0 * (r.mean() - o.mean()) / o.mean()) if o.mean() > 0 else None}
    return out


def _dkline(comp, row):
    """A DraftKings line from the reconciled components plus the row's
    untouched rushing and turnover projections."""
    return {"pass_yd": comp.get("pass_yd", 0.0), "pass_td": comp.get("pass_td", 0.0), "int": row["proj_pass_int"] or 0.0,
            "rush_yd": row["proj_rush_yd"] or 0.0, "rush_td": row["proj_rush_td"] or 0.0, "rec": comp.get("rec", 0.0),
            "rec_yd": comp.get("rec_yd", 0.0), "rec_td": comp.get("rec_td", 0.0), "fum": row["proj_fum_lost"] or 0.0}


def invariants(weights):
    """The identity, bound, idempotence and preservation tests on
    synthetic teams; every entry must be True."""
    import nfl_recon
    def team(gap_yd=0.2, gap_td=0.3, two_qbs=False):
        qb = [{"name": "QB", "pos": "QB", "means": {"pass_att": 34.0, "pass_cmp": 22.0, "pass_yd": 250.0, "pass_td": 1.6, "rec_tgt": 0.0, "rec": 0.0, "rec_yd": 0.0, "rec_td": 0.0}}]
        if two_qbs:
            qb = [{"name": "QBa", "pos": "QB", "means": {"pass_att": 17.0, "pass_cmp": 11.0, "pass_yd": 125.0, "pass_td": 0.8}},
                  {"name": "QBb", "pos": "QB", "means": {"pass_att": 17.0, "pass_cmp": 11.0, "pass_yd": 125.0, "pass_td": 0.8}}]
        recs = [("WR1", "WR", 9.0, 6.0, 80.0, 0.5), ("WR2", "WR", 6.5, 4.0, 55.0, 0.35), ("WR3", "WR", 4.0, 2.5, 30.0, 0.2),
                ("TE1", "TE", 5.0, 3.5, 38.0, 0.3), ("RB1", "RB", 4.0, 3.2, 25.0, 0.15), ("RB2", "RB", 1.5, 1.2, 8.0, 0.05), ("WR4", "WR", 2.5, 1.6, 14.0, 0.1)]
        s_yd = sum(r[4] for r in recs); s_td = sum(r[5] for r in recs)
        ky = 250.0 * (1 + gap_yd) / s_yd; kt = 1.6 * (1 + gap_td) / s_td
        return qb + [{"name": n, "pos": p, "means": {"rec_tgt": t, "rec": c, "rec_yd": y * ky, "rec_td": d * kt}} for n, p, t, c, y, d in recs]
    out = {}
    pl = team()
    orig = [dict(p["means"]) for p in pl]
    rep = nfl_recon.reconcile_team(pl, weights, team="T")
    def sums(players, qc, rc, f=1.0):
        return (f * sum(p["recon"].get(qc, 0.0) for p in players if p["pos"] == "QB"), sum(p["recon"].get(rc, 0.0) for p in players if p["pos"] != "QB"))
    factor = weights["weights"]["target_factor"]["value"]
    out["identities_hold_exactly"] = all(abs(a - b) < 1e-9 for a, b in (sums(pl, "pass_yd", "rec_yd"), sums(pl, "pass_td", "rec_td"), sums(pl, "pass_cmp", "rec"), sums(pl, "pass_att", "rec_tgt", factor)))
    out["originals_preserved"] = all(p["means"] == o for p, o in zip(pl, orig))
    out["nothing_negative"] = all(v >= 0 for p in pl for v in p["recon"].values())
    out["receptions_within_targets"] = all(p["recon"]["rec"] <= p["recon"]["rec_tgt"] + 1e-9 for p in pl if p["pos"] != "QB")
    out["touchdowns_within_receptions"] = all(p["recon"]["rec_td"] <= p["recon"]["rec"] + 1e-9 for p in pl if p["pos"] != "QB")
    out["completions_within_attempts"] = all(p["recon"]["pass_cmp"] <= p["recon"]["pass_att"] + 1e-9 for p in pl if p["pos"] == "QB")
    out["target_between_sides"] = all(min(v["qb"], v["sum"]) - 1e-6 <= v["target"] <= max(v["qb"], v["sum"]) + 1e-6 for v in rep["identities"].values())
    # a receiver's share of the move follows his variance: the WR1 (largest projection) moves the most yards
    moves = {p["name"]: p["recon"]["rec_yd"] - p["means"]["rec_yd"] for p in pl if p["pos"] != "QB"}
    out["largest_projection_moves_most"] = abs(moves["WR1"]) >= max(abs(v) for v in moves.values()) - 1e-9
    # idempotence: a second pass changes nothing
    again = [{"name": p["name"], "pos": p["pos"], "means": dict(p["recon"])} for p in pl]
    nfl_recon.reconcile_team(again, weights, team="T")
    out["idempotent"] = all(abs(a["recon"][k] - b["recon"][k]) < 1e-9 for a, b in zip(again, pl) for k in a["recon"])
    # a balanced team is untouched
    bal = team(gap_yd=0.0, gap_td=0.0)
    k_tgt = 34.0 * factor / sum(x["means"]["rec_tgt"] for x in bal if x["pos"] != "QB")
    k_rec = 22.0 / sum(x["means"]["rec"] for x in bal if x["pos"] != "QB")
    for p in bal:
        if p["pos"] != "QB":
            p["means"]["rec_tgt"] *= k_tgt
            p["means"]["rec"] *= k_rec
    o2 = [dict(p["means"]) for p in bal]
    nfl_recon.reconcile_team(bal, weights, team="T")
    out["balanced_team_unchanged"] = all(abs(p["recon"][k] - o[k]) < 1e-9 for p, o in zip(bal, o2) for k in o)
    # no quarterback: untouched and flagged
    nq = [p for p in team() if p["pos"] != "QB"]
    r3 = nfl_recon.reconcile_team(nq, weights, team="T")
    out["no_qb_untouched_and_flagged"] = "no_qb" in r3["flags"] and all(p["recon"] == p["means"] for p in nq)
    # a tripwire fires on a 40% yardage gap and not on 5%
    out["tripwire_fires_on_large_gap"] = nfl_recon.reconcile_team(team(gap_yd=0.4), weights, team="T")["tripped"] \
        and not nfl_recon.reconcile_team(team(gap_yd=0.05, gap_td=0.05), weights, team="T")["tripped"]
    # two quarterbacks share the quarterback side and the identity still holds
    t2 = team(two_qbs=True)
    nfl_recon.reconcile_team(t2, weights, team="T")
    out["two_quarterbacks_reconcile"] = all(abs(a - b) < 1e-9 for a, b in (sums(t2, "pass_yd", "rec_yd"), sums(t2, "pass_td", "rec_td")))
    # a bound that binds: a receiver whose receptions already equal his targets cannot take more receptions
    t3 = team()
    for p in t3:
        if p["name"] == "WR3":
            p["means"]["rec"] = p["means"]["rec_tgt"]
    nfl_recon.reconcile_team(t3, weights, team="T")
    out["bound_respected_when_binding"] = all(p["recon"]["rec"] <= p["recon"]["rec_tgt"] + 1e-9 for p in t3 if p["pos"] != "QB")
    return out


def run(boots=400):
    t0 = time.time()
    rows = H.read_csv_gz(os.path.join(DATA, "player_weeks_2022_2025.csv.gz"))
    rng = np.random.default_rng(BASE_SEED)
    tws = team_weeks(rows, S.TRAIN)
    lam = fit_lambda(tws, rng, boots=boots)
    var = fit_variance(rows, S.TRAIN)
    import nfl_recon
    weights = {"lambda": {k: v for k, v in lam.items() if k != "target_factor"}, "target_factor": lam["target_factor"],
               "variance": var, "tripwire": dict(nfl_recon.TRIPWIRE)}
    art = {"meta": {"stage": "2C", "commit": _commit(), "base_seed": BASE_SEED, "bootstraps": boots, "train_seasons": list(S.TRAIN),
                    "holdout_season": H.HOLDOUT, "holdout_outcomes_read": False, "model": nfl_recon.RECON_MODEL, "version": nfl_recon.RECON_VERSION,
                    "team_weeks": len(tws), "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                    "method": {"lambda": "least-squares weight on the quarterback's figure in the blend that predicts the actual team quantity; "
                                         "clipped to [0, 1]; 90% clustered bootstrap by team-season",
                               "variance": "squared residual regressed on the projection level per position and component (a + b * m), "
                                           "a floored at 0 and b at 1e-6; shares a side's change among its players",
                               "target_factor": "actual team targets over actual team attempts, pooled over the training team-weeks",
                               "allocation": "minimise sum(delta^2 / variance) subject to the sum of the deltas, bounds by an active set"}},
           "weights": weights, "hash": nfl_recon.weights_hash(weights)}
    json.dump(art, open(WEIGHTS, "w"), indent=1, sort_keys=True)
    applied = apply_training(rows, S.TRAIN, art)
    inv = invariants(art)
    st = {"meta": art["meta"], "hash": art["hash"], "lambda": lam, "variance": var, "applied": applied, "invariants": inv}
    st["meta"]["seconds"] = round(time.time() - t0, 1)
    json.dump(st, open(os.path.join(DATA, "stage2c_stats.json"), "w"), indent=1, sort_keys=True)
    write_report(st)
    return st


def _f(x, d=3):
    return "-" if x is None else f"{x:.{d}f}"


def write_report(st):
    m = st["meta"]
    L = ["# Stage 2C: reconciling Sleeper's component means, weights fitted on 2022 to 2024\n",
         f"Commit {m['commit']}, base seed {m['base_seed']}, {m['bootstraps']} clustered bootstraps, {m['team_weeks']:,} training team-weeks. "
         f"Built {m['built']}. Weights artifact research/data/reconcile_weights.json, SHA-256 {st['hash'][:16]}. "
         f"No 2025 outcome is read; 2025 projections are not touched until Stage 2E. Model {m['model']} v{m['version']}.\n",
         "## Method\n",
         f"Lambda: {m['method']['lambda']}. Variance: {m['method']['variance']}. Target factor: {m['method']['target_factor']}. Allocation: {m['method']['allocation']}. "
         "Order: attempts, completions (bounded by the reconciled attempts and targets), yards, touchdowns (bounded by the reconciled receptions). "
         "The originals stay on every player as `means`; the reconciled line is `recon`. The legacy simulator keeps reading `means`.\n",
         "## The weight on the quarterback's figure (lambda), per identity\n",
         "| Identity | Team-weeks | lambda | 90% bootstrap | 2022 / 2023 / 2024 | MSE quarterback alone | MSE receivers' sum alone | MSE plain average | MSE blend | Bias quarterback | Bias sum | Actual mean |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    lam = st["lambda"]
    for k in ("pass_att", "pass_cmp", "pass_yd", "pass_td"):
        v = lam[k]
        L.append(f"| {k} | {v['n']:,} | {v['value']:.3f} | [{v['boot_lo']:.3f}, {v['boot_hi']:.3f}] | {' / '.join('%.2f' % v['per_season'][s] for s in ('2022', '2023', '2024'))} | "
                 f"{v['mse_qb']:.2f} | {v['mse_sum']:.2f} | {v['mse_average']:.2f} | {v['mse_blend']:.2f} | {v['bias_qb']:+.2f} | {v['bias_sum']:+.2f} | {v['actual_mean']:.1f} |")
    tf = lam["target_factor"]
    L.append(f"\nTarget factor (actual team targets over actual attempts): {tf['value']:.4f}, 90% bootstrap [{tf['boot_lo']:.4f}, {tf['boot_hi']:.4f}]. "
             "The quarterback side of the attempts identity is attempts times this factor.\n")
    L.append("## Residual variance by projection level (the allocation weights)\n")
    L.append("| Position | Component | n | a | b | Residual sd by projection quartile (observed / fitted) |\n|---|---|---|---|---|---|")
    for pos, comps in st["variance"].items():
        for c, v in comps.items():
            L.append(f"| {pos} | {c} | {v['n']:,} | {v['a']:.3f} | {v['b']:.4f} | " + "; ".join(f"[{b['lo']:g}-{b['hi']:g}] {b['resid_sd']:.2f} / {b['fit_sd']:.2f}" for b in v["bins"]) + " |")
    ap = st["applied"]
    L.append(f"\n## Applied to the training seasons' projections ({ap['team_weeks']:,} team-weeks)\n")
    L.append("| Identity | Gap before, abs median | abs p95 | max | Gap after, max abs |\n|---|---|---|---|---|")
    for k, v in ap["gap_before"].items():
        L.append(f"| {k} | {v['abs_median']:.3f} | {v['abs_p95']:.3f} | {v['max_abs']:.3f} | {ap['gap_after_max_abs'][k]:.2e} |")
    L.append(f"\nFlags: {json.dumps(ap['flags'])}. Tripwires fired on {ap['tripped_count']} team-weeks ({100.0 * ap['tripped_count'] / max(1, ap['team_weeks']):.1f}%); "
             "the first forty: " + "; ".join(f"{t['key']} {json.dumps(t['gaps'])}" for t in ap["tripped"][:40]) + ".\n")
    L.append("### Size of the changes by position and component\n")
    L.append("| Position / component | n | abs median | abs p90 | abs max | mean (signed) | relative abs median | relative abs p90 | relative abs max | share moved over 25% |\n|---|---|---|---|---|---|---|---|---|---|")
    for k, v in ap["changes"].items():
        if v["n"] < 100 or v["abs_max"] <= 0:
            continue                                   # a handful of trick-play passers with nothing moved
        L.append(f"| {k} | {v['n']:,} | {v['abs_median']:.3f} | {v['abs_p90']:.3f} | {v['abs_max']:.2f} | {v['mean']:+.3f} | {_f(v['rel_abs_median'])} | {_f(v['rel_abs_p90'])} | {_f(v['rel_abs_max'], 2)} | {_f(v['share_over_25pct'])} |")
    L.append("\n### DraftKings-point shift of the projected mean\n")
    L.append("| Position | n | mean (signed) | abs median | abs p90 | abs max | share over 1 point |\n|---|---|---|---|---|---|---|")
    for k, v in ap["dk_shift"].items():
        L.append(f"| {k} | {v['n']:,} | {v['mean']:+.3f} | {v['abs_median']:.3f} | {v['abs_p90']:.3f} | {v['abs_max']:.2f} | {v['share_over_1pt']:.3f} |")
    L.append("\n### In-sample accuracy check (training seasons; lambda was fitted on these same seasons, so this is not validation)\n")
    L.append("| Position / component | n | MAE original | MAE reconciled | change |\n|---|---|---|---|---|")
    for k, v in ap["accuracy_in_sample"].items():
        L.append(f"| {k} | {v['n']:,} | {v['mae_orig']:.3f} | {v['mae_recon']:.3f} | {_f(v['mae_change_pct'], 2)}% |")
    L.append("\n## Invariants (synthetic teams)\n")
    for k, v in st["invariants"].items():
        L.append(f"- {k}: {'pass' if v else 'FAIL'}")
    L.append("\n## What this says\n")
    L.append("The reconciliation exists so that a team-budget simulator can hand the quarterback's day out by shares that sum to one; it is not a projection improvement and the in-sample table above is reported so that nobody mistakes it for one. "
             "The weights are frozen in the artifact and hashed; the constrained simulator (Stage 2D) reads `recon`, the legacy simulator keeps `means`, and a board built on either says which.\n")
    with open(os.path.join(REPORTS, "stage2c_report.md"), "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    st = run()
    print("stage 2C written; invariants:", all(st["invariants"].values()), st["invariants"])
