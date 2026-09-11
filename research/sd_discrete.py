"""Does the lattice defect actually move the numbers, or only the grid?

The previous pass measured that 47% of simulated showdown scores cannot occur
under DraftKings scoring, and said the consequence was a downward bias in
exact-tie probability. It did NOT say how large that bias is, because the grid
spacing does not imply it. This measures it.

Two showdown pools are built for the same game from the same model: the
production legacy one, and the experimental discrete one (nfl_dfs_sim
.simulate_game(discrete=True)), which keeps the identical latent structure --
shared game factor, quarterback latent, opposed scripts -- draws yards and
catches on their integer support, scores them with DraftKings' own scorer, and
pins the mean by scaling component means BEFORE the draw instead of
multiplying a finished point array afterwards.

Nothing here is promoted. The discrete pool is never served; `discrete`
defaults to False everywhere and rides in the pool's cache key.

    python3 -m research.sd_discrete <dir with the cached Sleeper feeds>
"""
import itertools
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
REPORTS = os.path.join(ROOT, "research", "reports")
SEASON, WEEK, WORLDS = "2026", 1, 20000
DK_STEP = 0.02


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def _feeds(feed_dir):
    import nfl_dfs_sim as S
    raw = json.load(open(os.path.join(feed_dir, f"proj_{SEASON}_{WEEK}.json")))
    dfn = json.load(open(os.path.join(feed_dir, f"proj_{SEASON}_{WEEK}_def.json")))
    kck = json.load(open(os.path.join(feed_dir, f"proj_{SEASON}_{WEEK}_k.json")))
    S._get = lambda url: (dfn if "DEF" in url else kck if "position[]=K" in url else raw)
    S._cache.clear()
    return S


def _arrs(sim):
    return {p["name"]: np.asarray(p["arr"], dtype=np.float64) for p in sim["players"]}


def tie_rate(A, k=6):
    """How often two random six-man lineups drawn from these players land on
    exactly the same score -- the quantity the sole-win column depends on."""
    names = sorted(A)
    M = np.stack([A[n] for n in names])          # (players, worlds)
    rng = np.random.default_rng(5)
    n_w = M.shape[1]
    hits = tot = 0
    for _ in range(400):
        a = rng.choice(len(names), size=k, replace=False)
        b = rng.choice(len(names), size=k, replace=False)
        if set(a.tolist()) == set(b.tolist()):
            continue
        sa = M[a].sum(axis=0) + 0.5 * M[a[0]]     # captain 1.5x
        sb = M[b].sum(axis=0) + 0.5 * M[b[0]]
        hits += int((np.abs(sa - sb) < 1e-9).sum())
        tot += n_w
    return hits / max(tot, 1)


def run(feed_dir, log=print):
    S = _feeds(feed_dir)
    games = S.weekly_games(SEASON, WEEK)
    gid = sorted(games)[0]
    g = games[gid]
    log(f"[SDD] {g.get('label')}: {len(g['players'])} players x {WORLDS:,} worlds")
    out = {"meta": {"stage": "showdown discrete counterfactual (NOT promoted)",
                    "game": g.get("label"), "season": SEASON, "week": WEEK, "worlds": WORLDS,
                    "provenance": _prov(worlds=WORLDS, model="legacy vs legacy-discrete")},
           "modes": {}}
    arrs = {}
    for mode, disc in (("legacy", False), ("discrete", True)):
        sim = S.simulate_game(g, n=WORLDS, with_samples=True, discrete=disc)
        A = _arrs(sim)
        arrs[mode] = A
        allv = np.concatenate(list(A.values()))
        off = np.abs(allv / DK_STEP - np.rint(allv / DK_STEP))
        rows = []
        for p in sim["players"]:
            a = A[p["name"]]
            rows.append({"name": p["name"], "pos": p["pos"], "proj": p["proj_pts"],
                         "mean": round(float(a.mean()), 3), "sd": round(float(a.std()), 3),
                         "p90": round(float(np.percentile(a, 90)), 2),
                         "p99": round(float(np.percentile(a, 99)), 2)})
        out["modes"][mode] = {
            "off_lattice": int((off > 1e-9).sum()), "samples": int(allv.size),
            "off_lattice_pct": round(100.0 * float((off > 1e-9).mean()), 2),
            "distinct_values": int(len(np.unique(np.round(allv, 6)))),
            "pair_tie_rate": round(float(tie_rate(A)), 6),
            "players": rows}
        log(f"[SDD] {mode:8s} off-lattice {out['modes'][mode]['off_lattice_pct']:5.2f}%  "
            f"distinct {out['modes'][mode]['distinct_values']:,}  "
            f"pair tie rate {out['modes'][mode]['pair_tie_rate']:.5f}")
    # what changed, player by player and pair by pair
    la, da = arrs["legacy"], arrs["discrete"]
    common = sorted(set(la) & set(da))
    dm = [abs(float(da[n].mean() - la[n].mean())) for n in common]
    rel = [abs(float(da[n].mean() - la[n].mean())) / max(float(la[n].mean()), 1e-9)
           for n in common if la[n].mean() > 1.0]
    ds = [abs(float(da[n].std() - la[n].std())) / max(float(la[n].std()), 1e-9)
          for n in common if la[n].std() > 0.5]
    pairs = []
    for a, b in itertools.combinations([n for n in common if la[n].std() > 1.0][:14], 2):
        pairs.append((float(np.corrcoef(la[a], la[b])[0, 1]),
                      float(np.corrcoef(da[a], da[b])[0, 1])))
    out["deltas"] = {
        "mean_abs_points_median": round(float(np.median(dm)), 4),
        "mean_rel_pct_median": round(100.0 * float(np.median(rel)), 2),
        "mean_rel_pct_max": round(100.0 * float(np.max(rel)), 2),
        "sd_rel_pct_median": round(100.0 * float(np.median(ds)), 2),
        "pair_corr_max_abs_diff": round(float(max(abs(x - y) for x, y in pairs)), 4),
        "pair_corr_median_abs_diff": round(float(np.median([abs(x - y) for x, y in pairs])), 4),
        "tie_rate_ratio": (round(out["modes"]["discrete"]["pair_tie_rate"]
                                 / max(out["modes"]["legacy"]["pair_tie_rate"], 1e-12), 2)
                           if out["modes"]["legacy"]["pair_tie_rate"] > 0 else None)}
    log(f"[SDD] means move {out['deltas']['mean_rel_pct_median']:.2f}% at the median, "
        f"SDs {out['deltas']['sd_rel_pct_median']:.2f}%, "
        f"pair correlations at most {out['deltas']['pair_corr_max_abs_diff']:.4f}")
    log(f"[SDD] TIE RATE: discrete is {out['deltas']['tie_rate_ratio']}x the legacy rate")
    # ---- board level: the columns the owner actually reads -----------------
    # A controlled showdown board on the same players under both modes: the
    # same lineup universe, the same field weights, the same contest. Only the
    # point arrays differ, so every difference below is the lattice and
    # nothing else.
    import dfs_tourney as T
    out["board"] = {}
    names = [n for n in common if la[n].std() > 0.5][:14]
    lineups = []
    for combo in itertools.combinations(range(len(names)), 6):
        for cpt in combo:
            lineups.append([cpt] + [x for x in combo if x != cpt])
    idx = np.asarray(lineups[:4000], dtype=np.int64)
    pay = [{"from": 1, "to": 1, "prize": 1_000_000.0}, {"from": 2, "to": 2, "prize": 100_000.0},
           {"from": 3, "to": 10, "prize": 10_000.0}, {"from": 11, "to": 1000, "prize": 100.0},
           {"from": 1001, "to": 26_400, "prize": 5.0}]
    C = 132_000
    grid = T.payout_grid(C, pay, 20.0, 26_400)
    for mode in ("legacy", "discrete"):
        A = arrs[mode]
        X = np.stack([A[n] for n in names]).astype(np.float32)
        W = T.lineup_matrix(idx, len(names), cpt_mult=1.5)
        f, beta = T.field_weights([{"proj": float(A[n].mean())} for n in names], idx)
        res = T.run_vs_field(W, W, f, X, [grid])[0][0]
        top1 = np.asarray(res["top1"]); t01 = np.asarray(res["top01"])
        out["board"][mode] = {
            "lineups": int(len(idx)), "beta": round(float(beta), 4),
            "sole_first_mean_pct": round(100.0 * float(np.asarray(res["win_sole"]).mean()), 6),
            "any_first_mean_pct": round(100.0 * float(np.asarray(res["win"]).mean()), 6),
            "top1_best_pct": round(100.0 * float(top1.max()), 3),
            "top01_best_pct": round(100.0 * float(t01.max()), 4),
            "ev_best": round(float(np.asarray(res["ev"]).max()), 2),
            "ev_notie_best": round(float(np.asarray(res["ev_notie"]).max()), 2),
            "top10_by_top1": [names[int(idx[i, 0])] for i in np.argsort(-top1)[:10]]}
        log(f"[SDD] board {mode:8s} sole-first {out['board'][mode]['sole_first_mean_pct']:.5f}%  "
            f"any-first {out['board'][mode]['any_first_mean_pct']:.5f}%  "
            f"best top1 {out['board'][mode]['top1_best_pct']:.2f}%  "
            f"best EV ${out['board'][mode]['ev_best']:,.0f}")
    lb, db = out["board"]["legacy"], out["board"]["discrete"]
    same = sum(1 for a, b in zip(lb["top10_by_top1"], db["top10_by_top1"]) if a == b)
    out["board"]["deltas"] = {
        "sole_first_ratio": (round(db["sole_first_mean_pct"] / lb["sole_first_mean_pct"], 3)
                             if lb["sole_first_mean_pct"] else None),
        "any_minus_sole_legacy_pct": round(lb["any_first_mean_pct"] - lb["sole_first_mean_pct"], 6),
        "any_minus_sole_discrete_pct": round(db["any_first_mean_pct"] - db["sole_first_mean_pct"], 6),
        "top1_best_abs_diff": round(abs(db["top1_best_pct"] - lb["top1_best_pct"]), 3),
        "top01_best_abs_diff": round(abs(db["top01_best_pct"] - lb["top01_best_pct"]), 4),
        "ev_best_pct_diff": (round(100.0 * (db["ev_best"] - lb["ev_best"]) / lb["ev_best"], 2)
                             if lb["ev_best"] else None),
        "top10_positions_identical": same}
    log(f"[SDD] board: sole-first ratio {out['board']['deltas']['sole_first_ratio']}x, "
        f"top-10 order identical in {same}/10 slots, "
        f"best EV moves {out['board']['deltas']['ev_best_pct_diff']}%")
    with open(os.path.join(DATA, "sd_discrete.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "feeds")
    run(fd)
    print("showdown discrete counterfactual written")
