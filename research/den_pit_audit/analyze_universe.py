"""Read universe.py's per-world output for both builds (research only):

  * every tracked lineup's board figures (first or a share, sole first, top 1%,
    top 0.1%, cash, EV) for the pre-sprint and HEAD builds with the PAIRED
    Monte Carlo SE -- the builds share their worlds, so the SE is the SD of the
    per-world difference over sqrt(n);
  * the depth gate on the field-only players: how often the best lineup of the
    universe holds one, what lifting the gate would add to OUR best lineup, and
    the lists with the gate off;
  * the rare outcomes behind the first-place and top-0.1% figures: world counts
    and exact (Clopper-Pearson) bounds;
  * the game scripts of the worlds where a lineup finishes in the top 1%.

    python3 research/den_pit_audit/analyze_universe.py --head <dir> --pre <dir> --u-head <npz> --u-pre <npz>
        --track <json> --boxes <dir> --served <board.json> --out <json>
"""
import argparse
import json
import math
import os
import pickle

import numpy as np

ap = argparse.ArgumentParser()
for k in ("head", "pre", "u_head", "u_head_delta", "u_pre_delta", "track", "boxes", "served", "out"):
    ap.add_argument("--" + k.replace("_", "-"), required=k not in ("u_head_delta", "u_pre_delta"))
a = ap.parse_args()

ch = np.load(os.path.join(a.head, "capture.npz"))
mh = json.load(open(os.path.join(a.head, "meta.json")))
bh = json.load(open(os.path.join(a.head, "board.json")))
uh = np.load(a.u_head)
# the pre-sprint build differs from HEAD only in the worlds where a defense moved (compare.py): the
# per-world figures are computed there for both builds (universe.py --only-worlds) and are equal elsewhere
dh = np.load(a.u_head_delta) if a.u_head_delta else None
dp = np.load(a.u_pre_delta) if a.u_pre_delta else None
tr = json.load(open(a.track))
served = json.load(open(a.served))
names = list(ch["ents"])
ents = mh["ents"]
idx = ch["idx"]
N = ch["X"].shape[1]
G, G2 = uh["G"], uh["G2"]
C = int(uh["C"])
COLS = {"first_or_share": (G, 0, "tF"), "top1": (G, 1, "tF"), "top01": (G, 2, "tF"), "cash": (G, 3, "tF"),
        "money": (G, 5, "tF"), "sole_first": (G2, 0, "tFE"), "ev": (G2, 1, "tFE")}
track = uh["track"]
labels = tr["labels"]
R = {"N": N, "C": C}


def lineup_txt(r):
    row = idx[r]
    return {"captain": names[row[0]], "flex": [names[j] for j in row[1:]],
            "proj": round(1.5 * float(ents[row[0]]["proj"]) + sum(float(ents[j]["proj"]) for j in row[1:]), 1),
            "salary": int(ents[row[0]]["cpt_salary"]) + sum(int(ents[j]["salary"]) for j in row[1:])}


def cp_upper(x, n, conf=0.95):
    """Clopper-Pearson two-sided upper bound (x = 0: 1 - (alpha/2)^(1/n))."""
    from math import exp, log
    al = 1 - conf
    if x == 0:
        return 1 - exp(log(al / 2) / n)
    if x == n:
        return 1.0
    lo, hi = x / n, 1.0
    from math import lgamma

    def tail(p):                       # P(X <= x | p)
        s = 0.0
        for k in range(0, x + 1):
            s += exp(lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1) + k * log(p) + (n - k) * log(1 - p))
        return s
    for _ in range(80):
        mid = (lo + hi) / 2
        if tail(mid) > al / 2:
            lo = mid
        else:
            hi = mid
    return hi


# ---- (a) paired figures for every tracked lineup
pair = []
for t, r in enumerate(track):
    ent = {"row": int(r), "labels": labels.get(str(int(r)), []), **lineup_txt(int(r))}
    for name, (tab, col, key) in COLS.items():
        vh = tab[uh[key][t], col].astype(np.float64)
        ent[name + "_head"] = float(vh.mean())
        if dp is not None:
            sel = dh["sel"]
            assert np.array_equal(tab[dh[key][t], col], vh[sel]), "the subset pass disagrees with the full pass"
            d = np.zeros(N)
            d[sel] = vh[sel] - tab[dp[key][t], col].astype(np.float64)
            ent[name + "_pre"] = float((vh - d).mean())
            ent[name + "_d"] = float(d.mean())
            ent[name + "_d_paired_se"] = float(d.std(ddof=1) / math.sqrt(N))
            ent[name + "_worlds_differing"] = int((d != 0).sum())
    pair.append(ent)
R["tracked"] = pair

# ---- (b) the depth gate on the field-only players
gated = np.array([bool(e.get("_field_only")) for e in ents])
gated_names = [e["name"] for e in ents if e.get("_field_only")]
has_g = uh["has_gated"]
allowed = ch["allowed"]
allowed_off = uh["allowed_off"]
best, bests = uh["best"], uh["bests"]
proj_row = 1.5 * np.array([float(e["proj"]) for e in ents])[idx[:, 0]] + \
    np.array([float(e["proj"]) for e in ents])[idx[:, 1:]].sum(axis=1)
b_all = best[0]
univ_g = has_g[b_all]
ours_off_better = bests[2] > bests[1]
rows_u = uh["rows"]
acc, acc2 = uh["acc"], uh["acc2"]
pos = {int(r): i for i, r in enumerate(rows_u)}


def fig(r):
    i = pos[int(r)]
    return {"top1_pct": round(100 * acc[i, 1], 3), "top01_pct": round(100 * acc[i, 2], 4),
            "cash_pct": round(100 * acc[i, 3], 2), "win_any_pct": round(100 * acc[i, 0], 4),
            "win_pct": round(100 * acc2[i, 0], 5), "ev": round(float(acc2[i, 1]), 2)}


on_rows = np.where(allowed)[0]
off_only = np.where(allowed_off & ~allowed)[0]
ii_on = np.array([pos[int(r)] for r in on_rows])
ii_off = np.array([pos[int(r)] for r in np.where(allowed_off)[0]])
off_rows = np.where(allowed_off)[0]


def top_by(ii, rows, col, tab, k=10):
    o = np.argsort(-tab[ii, col])[:k]
    return [dict(lineup_txt(int(rows[j])), gated=bool(has_g[int(rows[j])]), **fig(int(rows[j]))) for j in o]


def prop(b):
    p = float(np.mean(b))
    return {"p": round(p, 5), "se": round(math.sqrt(max(p * (1 - p), 1e-15) / len(b)), 5), "count": int(np.sum(b))}


R["gate"] = {
    "field_only_players": [{"name": e["name"], "pos": e["pos"], "team": e["team"], "depth": e.get("depth"),
                            "salary": e["salary"], "proj": e["proj"]} for e in ents if e.get("_field_only")],
    "lineups_we_may_enter": int(allowed.sum()), "lineups_we_could_enter_gate_off": int(allowed_off.sum()),
    "universe_best_holds_a_gated_player": prop(univ_g),
    "universe_best_is_ours": prop(allowed[b_all]),
    "universe_best_ours_if_gate_off": prop(allowed_off[b_all]),
    "universe_best_blocked_by_gate_only": prop(univ_g & allowed_off[b_all]),
    "universe_best_blocked_by_other_rules": prop(~allowed_off[b_all]),
    "our_best_improves_gate_off": prop(ours_off_better),
    "our_best_gain_pts_when_it_does": round(float((bests[2] - bests[1])[ours_off_better].mean()), 3)
    if ours_off_better.any() else 0.0,
    "our_best_gain_pts_mean_all_worlds": round(float((bests[2] - bests[1]).mean()), 4),
    "best_projection_gate_on": round(float(proj_row[allowed].max()), 2),
    "best_projection_gate_off": round(float(proj_row[allowed_off].max()), 2),
    "best_top1_gate_on": round(100 * float(acc[ii_on, 1].max()), 3),
    "best_top1_gate_off": round(100 * float(acc[ii_off, 1].max()), 3),
    "best_cash_gate_on": round(100 * float(acc[ii_on, 3].max()), 2),
    "best_cash_gate_off": round(100 * float(acc[ii_off, 3].max()), 2),
    "top10_top1_gate_off": top_by(ii_off, off_rows, 1, acc),
    "top10_money_gate_off": top_by(ii_off, off_rows, 5, acc),
    "gated_in_top60_top1_gate_off": int(sum(has_g[int(off_rows[j])] for j in np.argsort(-acc[ii_off, 1])[:60])),
    "gated_in_top60_money_gate_off": int(sum(has_g[int(off_rows[j])] for j in np.argsort(-acc[ii_off, 5])[:60])),
}
# how much of the board's opt share (best of all lineups) each gated player carries
R["gate"]["opt_share_by_gated_player"] = {
    nm: prop(np.isin(b_all, np.where((idx == names.index(nm)).any(axis=1))[0])) for nm in gated_names}

# ---- (c) rare outcomes behind the first-place and top-0.1% figures (HEAD, the board's leaders)
rare = []
tFm, tEm = uh["tFm"], uh["tEm"]
top01_k = max(1, int(0.001 * C))
for t, r in enumerate(track):
    lab = labels.get(str(int(r)), [])
    if not any(x.startswith("head:") for x in lab):
        continue
    F, E = tFm[t], tEm[t]
    n_top = int((best[0] == r).sum())
    n_f0 = int((F <= 0).sum())
    n_first_exp = int(((C - 1) * F < 1.0).sum())         # fewer than one entry expected above
    n_top01 = int(((C - 1) * F < top01_k).sum())          # the expected entries above it inside the top 0.1%
    f = fig(int(r))
    rare.append({"row": int(r), "labels": lab, **lineup_txt(int(r)), **f,
                 "worlds_best_of_universe": n_top, "worlds_no_field_mass_above": n_f0,
                 "worlds_under_one_entry_expected_above": n_first_exp,
                 "worlds_inside_top01_expected": n_top01,
                 "mean_level_mass_when_on_top": round(float(E[F <= 0].mean()), 6) if n_f0 else None,
                 "expected_copies_when_on_top": round(float((C - 1) * E[F <= 0].mean()), 1) if n_f0 else None,
                 "cp95_upper_first_if_zero": cp_upper(0, N) if n_f0 == 0 else None,
                 "cp95_upper_best_of_universe": cp_upper(n_top, N)})
R["rare"] = rare
R["rare_note"] = {"cp95_upper_for_zero_in_N": cp_upper(0, N), "top01_places": top01_k}

# ---- (d) the worlds where a lineup finishes top 1%: their game scripts
bx = np.load(os.path.join(a.boxes, "boxes.npz"))
PR = pickle.load(open(os.path.join(a.boxes, "profiles.pkl"), "rb"))
home, away = PR["home"], PR["away"]
H = {k[5:]: bx[k] for k in bx.files if k.startswith("home_")}
A = {k[5:]: bx[k] for k in bx.files if k.startswith("away_")}
box = {home: H, away: A}
X = ch["X"].astype(np.float64)


def script(r):
    t = int(np.where(track == r)[0][0])
    w = G[uh["tF"][t], 1].astype(np.float64)             # the per-world top-1% value: the board's own weight
    tot = w.sum()
    out = {"lineup": lineup_txt(int(r)), "top1_pct": round(100 * tot / N, 3), "effective_worlds": round(float(tot), 1)}
    feats = {}
    for tm in (away, home):
        b = box[tm]
        feats[f"{tm}_pts"] = b["pts"]
        feats[f"{tm}_td_rush"] = b["td_rush"]
        feats[f"{tm}_td_pass"] = b["td_pass"]
        feats[f"{tm}_fg_made"] = b["fg_made"]
        feats[f"{tm}_rush_yds"] = b["rush_yds"]
        feats[f"{tm}_pass_yds"] = b["pass_yds"]
        feats[f"{tm}_sacks_taken"] = b["sacks"]
        feats[f"{tm}_giveaways"] = b["int"] + b["fum_lost"]
    feats["margin_DEN_minus_PIT"] = box["DEN"]["pts"] - box["PIT"]["pts"]
    feats["total"] = box["DEN"]["pts"] + box["PIT"]["pts"]
    for nm in lineup_txt(int(r))["flex"] + [lineup_txt(int(r))["captain"]]:
        feats[f"pts:{nm}"] = X[names.index(nm)]
    for k, v in feats.items():
        v = np.asarray(v, dtype=np.float64)
        out[k] = {"all": round(float(v.mean()), 2), "top1_worlds": round(float((w * v).sum() / tot), 2)}
    m = feats["margin_DEN_minus_PIT"]
    cls = {"DEN by 8+": m >= 8, "DEN by 1-7": (m >= 1) & (m <= 7), "tie": m == 0, "PIT by 1-7": (m <= -1) & (m >= -7),
           "PIT by 8+": m <= -8}
    out["script_share"] = {k: {"all": round(float(c.mean()), 4), "top1": round(float((w * c).sum() / tot), 4)}
                           for k, c in cls.items()}
    tot_c = feats["total"]
    tc = {"total <= 34": tot_c <= 34, "35-44": (tot_c >= 35) & (tot_c <= 44), "45+": tot_c >= 45}
    out["total_share"] = {k: {"all": round(float(c.mean()), 4), "top1": round(float((w * c).sum() / tot), 4)}
                          for k, c in tc.items()}
    dr = box["DEN"]["td_rush"]
    out["den_rush_td_share"] = {str(k): {"all": round(float((dr == k).mean()), 4),
                                         "top1": round(float((w * (dr == k)).sum() / tot), 4)} for k in range(0, 4)}
    return out


targets = {}
sm = served["top_money"][0]
key = {(names[row[0]], tuple(sorted(names[j] for j in row[1:]))): r for r, row in enumerate(idx)}
r_served = key.get((sm["captain"], tuple(sorted(sm["flex"]))))
if r_served is not None and r_served in set(int(x) for x in track):
    targets["served_top_money_1"] = script(r_served)
for ln in ("top_money", "top_top1"):
    e = bh[ln][0]
    r = key.get((e["captain"], tuple(sorted(e["flex"]))))
    if r is not None and r in set(int(x) for x in track):
        targets[f"head_{ln}_1"] = script(r)
R["scripts"] = targets
with open(a.out, "w") as fh:
    json.dump(R, fh, indent=1, default=str)
print("DONE", a.out)
