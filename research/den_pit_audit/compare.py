"""The DEN @ PIT audit's comparison: the pre-sprint build against the HEAD build
(identical recorded inputs, the same seed), the served board described beside
them, the D/ST correction isolated on the same simulated games, and -- when
universe.py has run on both builds -- every lineup probability with its
PAIRED Monte Carlo standard error, the depth gate's cost, and the rare
outcomes' counts. Research only.

    python3 research/den_pit_audit/compare.py --head <dir> --pre <dir> --served <board.json>
        --boxes <pool_boxes dir> [--u-head <npz> --u-pre <npz>] --out <results.json>
"""
import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--head", required=True)
ap.add_argument("--pre", required=True)
ap.add_argument("--served", required=True)
ap.add_argument("--boxes", default=None)
ap.add_argument("--u-head", default=None)
ap.add_argument("--u-pre", default=None)
ap.add_argument("--out", required=True)
a = ap.parse_args()
sys.path.insert(0, common.ROOT)
R = {}


def load(d):
    b = json.load(open(os.path.join(d, "board.json")))
    m = json.load(open(os.path.join(d, "meta.json")))
    c = np.load(os.path.join(d, "capture.npz"))
    req = [json.loads(x) for x in open(os.path.join(d, "requests.jsonl"))]
    return b, m, c, req


bh, mh, ch, qh = load(a.head)
bp, mp_, cp, qp = load(a.pre)
served = json.load(open(a.served))

# ---- inputs: the same requests, the same pool, the same universe and field
kh, kp = [x["k"] for x in qh], [x["k"] for x in qp]
R["requests"] = {"head": len(kh), "pre": len(kp), "head_unique": len(set(kh)), "pre_unique": len(set(kp)),
                 "only_head": sorted({x["url"] for x in qh if x["k"] not in set(kp)}),
                 "only_pre": sorted({x["url"] for x in qp if x["k"] not in set(kh)}),
                 "pre_live_misses": mp_["net"].get("miss", 0), "head_counts": mh["net"], "pre_counts": mp_["net"],
                 "same_order": kh == kp}
names_h, names_p = list(ch["ents"]), list(cp["ents"])
R["inputs"] = {"ents_equal": names_h == names_p, "idx_equal": bool(np.array_equal(ch["idx"], cp["idx"])),
               "allowed_equal": bool(np.array_equal(ch["allowed"], cp["allowed"])),
               "f_max_abs_diff": float(np.abs(ch["f"] - cp["f"]).max()) if ch["f"].shape == cp["f"].shape else None,
               "salaries_proj_equal": [(e["name"], e["salary"], e["proj"], e.get("_field_only")) for e in mh["ents"]]
               == [(e["name"], e["salary"], e["proj"], e.get("_field_only")) for e in mp_["ents"]],
               "beta": [mh["beta"], mp_["beta"]], "commits": {"head": mh["head"], "pre": mp_["head"]},
               "n": mh["n"], "seed": mh["seed"]}


# ---- players: the same worlds, paired
def qtile(arr, fr):
    s = np.sort(arr)
    return float(s[min(len(s) - 1, int(fr * len(s)))])


def boot_q_se(x, y, fr, B=200, seed=11):
    r = np.random.default_rng(seed)
    n = len(x)
    v = []
    for _ in range(B):
        ii = r.integers(0, n, n)
        v.append(qtile(x[ii], fr) - qtile(y[ii], fr))
    return float(np.std(v, ddof=1))


Xh, Xp = ch["X"].astype(np.float64), cp["X"].astype(np.float64)
N = Xh.shape[1]
pl = []
for i, nm in enumerate(names_h):
    x, y = Xh[i], Xp[i]
    d = x - y
    moved = int((d != 0).sum())
    row = {"name": nm, "pos": mh["ents"][i].get("pos"), "team": mh["ents"][i].get("team"),
           "field_only": bool(mh["ents"][i].get("_field_only")),
           "mean_pre": round(float(y.mean()), 3), "mean_head": round(float(x.mean()), 3),
           "d_mean": round(float(d.mean()), 4), "d_mean_paired_se": round(float(d.std(ddof=1) / math.sqrt(N)), 4),
           "floor_pre": qtile(y, 0.1), "floor_head": qtile(x, 0.1), "ceil_pre": qtile(y, 0.9), "ceil_head": qtile(x, 0.9),
           "worlds_moved": moved, "bitwise_equal": moved == 0}
    if moved:
        row["d_floor_paired_se"] = round(boot_q_se(x, y, 0.1), 3)
        row["d_ceil_paired_se"] = round(boot_q_se(x, y, 0.9), 3)
        row["sd_pre"], row["sd_head"] = round(float(y.std()), 3), round(float(x.std()), 3)
    pl.append(row)
R["players"] = pl
moved_worlds = np.any(Xh != Xp, axis=0)
R["worlds_any_player_moved"] = int(moved_worlds.sum())


# ---- rankings, field, duplication, top lists (the boards' own figures)
def spearman(a_, b_):
    ra, rb = np.argsort(np.argsort(-np.asarray(a_))), np.argsort(np.argsort(-np.asarray(b_)))
    return float(np.corrcoef(ra, rb)[0, 1])


def players_tab(b):
    return {p["name"]: p for p in b["players"]}


ph, pp = players_tab(bh), players_tab(bp)
common_names = [n for n in ph if n in pp]
rk = {}
for col in ("opt_cpt_pct", "opt_flex_pct", "field_cpt_pct", "field_flex_pct"):
    xs, ys = [ph[n][col] for n in common_names], [pp[n][col] for n in common_names]
    rk[col] = {"spearman": round(spearman(xs, ys), 4), "max_abs_diff_pts": round(max(abs(u - v) for u, v in zip(xs, ys)), 3),
               "top10_head": [n for n, _ in sorted(zip(common_names, xs), key=lambda t: -t[1])[:10]],
               "top10_pre": [n for n, _ in sorted(zip(common_names, ys), key=lambda t: -t[1])[:10]],
               "diffs": {n: round(ph[n][col] - pp[n][col], 3) for n in common_names if ph[n][col] != pp[n][col]}}
R["rankings"] = rk
R["field_duplication"] = {"head": bh.get("field_duplication"), "pre": bp.get("field_duplication"),
                          "served": served.get("field_duplication")}
if bp.get("field_duplication") is None:
    import dfs_tourney
    R["field_duplication"]["pre_recomputed_with_head_code"] = dfs_tourney.field_duplication(cp["idx"], cp["f"], "showdown")


def lkey(e):
    return (e["captain"], tuple(sorted(e["flex"])))


def list_cmp(name):
    Lh, Lp = bh[name], bp[name]
    kh_, kp_ = [lkey(e) for e in Lh], [lkey(e) for e in Lp]
    out = {"same_order_top10": kh_[:10] == kp_[:10], "overlap_top10": len(set(kh_[:10]) & set(kp_[:10])),
           "overlap_top20": len(set(kh_[:20]) & set(kp_[:20])), "overlap_all": len(set(kh_) & set(kp_)),
           "n": len(Lh), "leaders": []}
    byp = {lkey(e): e for e in Lp}
    for e in Lh[:10]:
        o = byp.get(lkey(e))
        out["leaders"].append({"captain": e["captain"], "flex": e["flex"], "proj": e["proj"],
                               **{c: [o.get(c) if o else None, e.get(c)] for c in
                                  ("money_pct", "top1_pct", "win_any_pct", "win_pct", "ev", "roi_pct", "opt_pct",
                                   "expected_copies")}})
    return out


R["lists"] = {n: list_cmp(n) for n in ("top_money", "top_top1", "top_ev", "top_win")}
R["portfolio"] = {k: [bp["portfolio"].get(k, {}).get("p_any_top1_pct"), bh["portfolio"].get(k, {}).get("p_any_top1_pct")]
                  for k in ("5", "10", "20")}
R["timings"] = {"head": bh.get("timings"), "pre": bp.get("timings"), "served": served.get("timings")}
R["simulator"] = {"head": {k: bh["simulator"].get(k) for k in ("model", "version", "n", "seed")},
                  "pre": {k: bp["simulator"].get(k) for k in ("model", "version", "n", "seed")},
                  "served": {k: served["simulator"].get(k) for k in ("model", "version", "n", "seed")}}

# ---- the D/ST correction on the same simulated games
if a.boxes:
    import nfl_dfs_engine as NE
    bx = np.load(os.path.join(a.boxes, "boxes.npz"))
    H = {k[5:]: bx[k] for k in bx.files if k.startswith("home_")}
    A = {k[5:]: bx[k] for k in bx.files if k.startswith("away_")}
    pb = np.load(os.path.join(a.boxes, "pool.npz"))
    pool = {n: pb["arr"][i] for i, n in enumerate(pb["names"])}

    def old_off(box, opp):
        return box["pts"] - box["ret_pts"] - 2 * opp["safety_conceded"]

    def dst(me, opp, offf):
        import dk_scoring
        D_ = dk_scoring.NFL_DST
        pa = offf(opp, me)
        tiers = np.full(len(pa), -4, dtype=np.int64)
        for lo, hi, p in reversed(dk_scoring.NFL_DST_PA):
            tiers = np.where((pa >= lo) & (pa <= hi), p, tiers)
        return (tiers + D_["sack"] * opp["sacks"] + D_["int"] * opp["int"] + D_["fumble_rec"] * opp["fum_lost"]
                + D_["return_td"] * me["td_ret"] + D_["safety"] * opp["safety_conceded"]
                + D_["blocked_kick"] * me["blk"]).astype(np.float64)

    PRf = __import__("pickle").load(open(os.path.join(a.boxes, "profiles.pkl"), "rb"))
    home, away = PRf["home"], PRf["away"]
    dsts = {}
    for t, me, opp in ((home, H, A), (away, A, H)):
        new, old = NE.dk_dst(me, opp), dst(me, opp, old_off)
        assert np.array_equal(new, dst(me, opp, NE.off_points))
        d = new - old
        nick = {"PIT": "Steelers", "DEN": "Broncos"}[t]
        i_h, i_p = names_h.index(nick), names_p.index(nick)
        dsts[t] = {"mean_old": round(float(old.mean()), 4), "mean_new": round(float(new.mean()), 4),
                   "d_mean": round(float(d.mean()), 4), "d_mean_paired_se": round(float(d.std(ddof=1) / math.sqrt(len(d))), 4),
                   "sd_old": round(float(old.std()), 3), "sd_new": round(float(new.std()), 3),
                   "worlds_moved": int((d != 0).sum()), "moved_pct": round(100.0 * float((d != 0).mean()), 3),
                   "moved_up": int((d > 0).sum()), "moved_down": int((d < 0).sum()),
                   "floor_old": qtile(old, 0.1), "floor_new": qtile(new, 0.1), "ceil_old": qtile(old, 0.9),
                   "ceil_new": qtile(new, 0.9),
                   "pool_head_equals_new": bool(np.array_equal(Xh[i_h].astype(np.float32), new.astype(np.float32))),
                   "pool_pre_equals_old": bool(np.array_equal(Xp[i_p].astype(np.float32), old.astype(np.float32))),
                   "opp_kick_punt_return_tds": int((opp["td_ret"] - opp["td_def"]).sum()),
                   "opp_pick_sixes": int(opp["td_def"].sum()),
                   "opp_return_pts_old_rule_excluded": int(opp["ret_pts"].sum())}
    # every non-DST array of the replayed pool equals the HEAD build's (the same worlds)
    same = {n: bool(np.array_equal(pool[n].astype(np.float32), Xh[names_h.index(n)].astype(np.float32)))
            for n in names_h if n in pool}
    dsts["replayed_pool_equals_head_build"] = all(same.values())
    dsts["box_means"] = {t: {k: round(float(v.mean()), 3) for k, v in bx_.items()} for t, bx_ in ((home, H), (away, A))}
    R["dst_correction"] = dsts

with open(a.out, "w") as fh:
    json.dump(R, fh, indent=1, default=str)
print(json.dumps({k: R[k] for k in ("requests", "inputs")}, indent=1, default=str)[:3000])
print("DONE", a.out)
