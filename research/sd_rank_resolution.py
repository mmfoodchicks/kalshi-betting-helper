"""Does the exact Showdown engine RESOLVE the ranks that carry the prize pool?

The audit document said, until 2026-09-19, that the top-10 region of an
88,235-entry field (0.011% of it) was "unresolvable by construction" at 4,000
football worlds, as if Top-10 waited for a Monte Carlo world in which our
lineup literally finished among ten sampled opponents. It does not. The exact
engine (dfs_tourney.run_vs_field) computes, in each football world, the field
mass strictly above and level with every lineup and maps that mass through the
Poisson / normal rank curves of payout_grid; P(rank <= 882), P(rank <= 88) and
P(rank <= 10) are all analytic inside a world. The only randomness left is the
football world itself. So whether Top-10 is usable at 4,000 or 12,000 worlds is
a MEASUREMENT of football-world noise, not an inference from 10 / C. The
reviewer asked for it; this is it.

On the pinned boards, served model, one pool at the A/B's seed:

  * the same rank curves as production, extended to k = 10, 20, 50 beside the
    served 88 (Top 0.1%) and 882 (Top 1%) -- the extension is CHECKED against
    the grid's own top1 / top01 columns before it is used;
  * every enterable lineup's per-world rank probabilities, accumulated in
    1,000-world blocks (so 4,000-world folds and the 12,000-world total are
    both readable) with sums of squares for the world-level standard error;
  * for a shortlist (the strongest lineups under each objective), the
    per-world values themselves, so the greedy-cover portfolio can be selected
    on each 4,000-world fold and compared fold to fold.

Reported per objective: the relative standard error of the strongest lineups
at 4,000 and 12,000 worlds; how far the top-50's spread exceeds its noise;
fold-to-fold rank correlation and top-20 overlap; fold-to-fold portfolio
overlap; and how many lineups are within two standard errors of the 20th
place, i.e. how much of the top 20 the worlds actually decide.

Not a promotion, not an objective change: S7 comes first. If Top-10 is stable
it is a candidate surrogate objective for later; if it is not, the sentence
"below simulation resolution" is earned rather than assumed.

    python3 -m research.sd_rank_resolution all
    python3 -m research.sd_rank_resolution board <dg>
"""
import json
import os
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")

from research import sd_model_ab as MAB     # noqa: E402  the pinned boards, seed, feeds

#: rank thresholds: the served two and three finer ones
KS = (10, 20, 50, 88, 882)
BLOCK = 1000                 # worlds per accumulation block
FOLD = 4000                  # the S5 held-out fold size
SHORT = 400                  # strongest lineups per objective kept world by world
PORT_K = 20


def rank_curves(grid, ks):
    """P(rank <= k) on the grid's F axis, the same arithmetic as
    payout_grid's p_at_most: Poisson where the expected number above is small,
    the normal rank approximation where it is not."""
    import dfs_tourney as T
    C = int(grid["C"])
    F = np.asarray(grid["F"], dtype=np.float64)
    m = 1.0 + (C - 1) * F
    s = np.sqrt(np.maximum(1e-9, (C - 1) * F * (1.0 - F)))
    lamA = -(C - 1) * np.log(np.maximum(1.0 - F, 1e-300))
    pois = lamA <= T.POIS_LAM_MAX
    cdfA = T._pois_cdf_table(lamA[pois]) if pois.any() else np.zeros((0, T.POIS_K_MAX + 1))
    out = {}
    for k in ks:
        kk = int(k) - 1
        v = T._ncdf_arr((kk + 1.5 - m) / s)
        if pois.any():
            if kk < 0:
                head = np.zeros(cdfA.shape[0])
            elif kk > T.POIS_K_MAX:
                head = np.ones(cdfA.shape[0])
            else:
                head = cdfA[:, kk]
            v = v.copy()
            v[pois] = head
        out[int(k)] = v
    return out


def _check_curves(grid, curves):
    """The extension reproduces production's own columns exactly, or nothing
    below is trusted."""
    C = int(grid["C"])
    k1, k01 = max(1, int(0.01 * C)), max(1, int(0.001 * C))
    ok1 = np.array_equal(curves[k1], np.asarray(grid["top1"]))
    ok01 = np.array_equal(curves[k01], np.asarray(grid["top01"]))
    if not (ok1 and ok01):
        raise SystemExit(f"rank curve replica disagrees with the grid (top1 {ok1}, top01 {ok01})")
    return {"top1_k": k1, "top01_k": k01, "replica_matches_grid": True}


def _rank_corr(a, b):
    ra = np.argsort(np.argsort(-np.asarray(a, dtype=np.float64)))
    rb = np.argsort(np.argsort(-np.asarray(b, dtype=np.float64)))
    return round(float(np.corrcoef(ra, rb)[0, 1]), 3) if len(a) > 2 else None


def board(dg, log=print):
    import resource
    import dfs_tourney as T
    from research import s6_capture, sd_board
    S = sd_board.feeds(MAB.FEEDS)
    S._cache.clear()
    slate, details = s6_capture.replay(int(dg))
    contest = details.get(str(MAB.PRIMARY[int(dg)]))
    ents, _secs, _rss = sd_board.ents_for(slate, sd_board.WEEK, True, n_sims=MAB.N_SIMS,
                                          seed=MAB.SEED, log=log, model="legacy")
    idx, W, allowed = sd_board.universe(ents)
    f, beta = T.field_weights(ents, idx, cpt_mult=1.5)
    grid, C = sd_board.grid_for(contest)
    ks = tuple(sorted(set(KS) | {max(1, int(0.01 * C)), max(1, int(0.001 * C))}))
    curves = rank_curves(grid, ks)
    check = _check_curves(grid, curves)
    Tab = np.stack([curves[k] for k in ks], axis=1).astype(np.float32)      # (nF, nK)
    N = min(len(e["arr"]) for e in ents)
    X = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    cand = np.where(allowed)[0]
    K, nK = len(cand), len(ks)
    F_grid = grid["F"]
    WT = np.ascontiguousarray(W.T)
    chunk = T.chunk_for(len(idx), 500)
    nblk = (N + BLOCK - 1) // BLOCK
    log(f"[RES] dg {dg}: {len(idx):,} lineups, {K:,} enterable candidates, {N:,} worlds, "
        f"C {C:,}, ks {ks}, {chunk} worlds a chunk")

    def sweep(n_worlds, keep=None):
        """One pass over the first n_worlds: per-candidate block sums and sums
        of squares per objective; per-world values for `keep` (positions into
        cand) when given."""
        sums = np.zeros((nblk, K, nK), dtype=np.float64)
        sq = np.zeros((K, nK), dtype=np.float64)
        P = None if keep is None else np.zeros((len(keep), nK, n_worlds), dtype=np.float32)
        t0 = time.time()
        for start in range(0, n_worlds, chunk):
            Xc = np.ascontiguousarray(X[:, start:start + chunk].T)
            Bc = T._buckets(Xc @ WT)
            for j in range(Bc.shape[0]):
                iF = T._world_tables(Bc[j], f, F_grid)
                vals = Tab[iF][Bc[j][cand]]                     # (K, nK)
                w = start + j
                sums[w // BLOCK] += vals
                sq += vals * vals
                if P is not None:
                    P[:, :, w] = vals[keep]                  # (short, nK)
        return sums, sq, P, time.time() - t0

    # pre-pass: a shortlist per objective from the first 1,000 worlds
    s_pre, _, _, t_pre = sweep(BLOCK)
    est_pre = s_pre[0] / BLOCK
    keep = sorted(set(int(i) for c in range(nK) for i in np.argsort(-est_pre[:, c])[:SHORT]))
    log(f"[RES] dg {dg}: pre-pass {t_pre:.0f}s, shortlist {len(keep)} lineups across {nK} objectives")
    sums, sq, P, t_main = sweep(N, keep=np.asarray(keep))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    est = sums.sum(axis=0) / N                                  # (K, nK) at N worlds
    var = np.maximum(sq / N - est * est, 0.0)
    se = np.sqrt(var / N)
    nf = N // FOLD
    folds = [sums[i * (FOLD // BLOCK):(i + 1) * (FOLD // BLOCK)].sum(axis=0) / FOLD for i in range(nf)]
    keep_pos = {int(i): p for p, i in enumerate(keep)}
    out = {"draft_group_id": int(dg), "contest_id": int(contest["id"]), "entries": int(C),
           "worlds": int(N), "fold_worlds": FOLD, "folds": nf, "legal_lineups": int(len(idx)),
           "enterable": int(K), "shortlist": len(keep), "field_beta": round(float(beta), 4),
           "seed": MAB.SEED, "ks": list(ks), "curve_check": check,
           "build": {"pre_pass_s": round(t_pre, 1), "main_pass_s": round(t_main, 1),
                     "peak_rss_mb": round(rss, 1)},
           "objectives": {}}
    for c, k in enumerate(ks):
        col = est[:, c]
        order = np.argsort(-col)
        top50, top20 = order[:50], order[:20]
        rel = se[top50, c] / np.maximum(col[top50], 1e-12)
        spread = float(col[top50].std())
        noise = float(np.median(se[top50, c]))
        # fold-to-fold: ranking of the objective's own shortlist, its top 20, and its portfolio
        short_c = [int(i) for i in order[:SHORT]]
        pos_c = np.asarray([keep_pos[i] for i in short_c if i in keep_pos])
        rc, ov, pov = [], [], []
        port_full = None
        if len(pos_c) >= PORT_K:
            Pc = P[pos_c, c, :]                                  # (short, N)
            chosen_full, _ = T.greedy_cover(Pc, PORT_K)
            port_full = set(int(pos_c[j]) for j in chosen_full)
            ports = []
            for i in range(nf):
                sl = slice(i * FOLD, (i + 1) * FOLD)
                ch, _ = T.greedy_cover(Pc[:, sl], PORT_K)
                ports.append(set(int(pos_c[j]) for j in ch))
            for i in range(nf):
                for j in range(i + 1, nf):
                    fi, fj = folds[i][short_c, c], folds[j][short_c, c]
                    rc.append(_rank_corr(fi, fj))
                    ti = set(int(x) for x in np.asarray(short_c)[np.argsort(-fi)[:PORT_K]])
                    tj = set(int(x) for x in np.asarray(short_c)[np.argsort(-fj)[:PORT_K]])
                    ov.append(len(ti & tj))
                    pov.append(len(ports[i] & ports[j]))
            port_vs_full = [len(p & port_full) for p in ports]
        else:
            port_vs_full = []
        twentieth = float(col[top20[-1]])
        within = int(np.sum(np.abs(col[order[:SHORT]] - twentieth) <= 2.0 * se[order[:SHORT], c]))
        out["objectives"][f"top{k}"] = {
            "k": int(k), "share_of_field_pct": round(100.0 * k / C, 4),
            "best_pct": round(100.0 * float(col[order[0]]), 4),
            "twentieth_pct": round(100.0 * twentieth, 4),
            "top50_rel_se_median_12000": round(float(np.median(rel)), 4),
            "top50_rel_se_median_4000": round(float(np.median(rel)) * float(np.sqrt(N / FOLD)), 4),
            "top50_spread_over_noise": round(spread / noise, 2) if noise > 0 else None,
            "fold_rank_corr_mean": round(float(np.mean(rc)), 3) if rc else None,
            "fold_top20_overlap_mean": round(float(np.mean(ov)), 2) if ov else None,
            "fold_portfolio_overlap_mean": round(float(np.mean(pov)), 2) if pov else None,
            "fold_portfolio_vs_full_overlap": port_vs_full,
            "within_2se_of_twentieth": within,
            "top5": [{"i": int(i), "pct": round(100.0 * float(col[i]), 4), "se_pct": round(100.0 * float(se[i, c]), 4)}
                     for i in order[:5]]}
        log(f"[RES] dg {dg} top{k:<4d} best {100 * col[order[0]]:.3f}% rel-SE(4k) {out['objectives'][f'top{k}']['top50_rel_se_median_4000']:.3f} "
            f"spread/noise {out['objectives'][f'top{k}']['top50_spread_over_noise']} fold rank corr {out['objectives'][f'top{k}']['fold_rank_corr_mean']} "
            f"top20 overlap {out['objectives'][f'top{k}']['fold_top20_overlap_mean']} portfolio overlap {out['objectives'][f'top{k}']['fold_portfolio_overlap_mean']} "
            f"within 2se of 20th {within}")
    with open(os.path.join(DATA, f"_res_{dg}.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


def run_all(dgs=MAB.DGS, log=print):
    from research import provenance
    procs = {}
    for dg in dgs:
        cmd = [sys.executable, "-m", "research.sd_rank_resolution", "board", str(dg)]
        log(f"[RES] spawning {' '.join(cmd[2:])}")
        procs[dg] = subprocess.Popen(cmd, cwd=ROOT, env={**os.environ, "VIGIL_NO_BG": "1"})
    for dg, p in procs.items():
        if p.wait() != 0:
            raise SystemExit(f"board {dg} failed (rc {p.returncode})")
    out = {"meta": {"stage": "rank resolution of the exact Showdown engine at the served world count",
                    "question": ("is P(rank <= 10 / 20 / 50) usable at 4,000 and 12,000 football worlds, "
                                 "or only the served Top 0.1% and Top 1%?"),
                    "method": ("per-world rank probabilities are analytic (payout_grid's curves on the field "
                               "mass above the lineup); the noise is the football world; measured by world-level "
                               "standard error, 4,000-world folds, and greedy-cover portfolio agreement"),
                    "ks": list(KS), "fold_worlds": FOLD, "shortlist_per_objective": SHORT,
                    "not": ["a promotion", "an objective change", "an economic claim; the field is uncalibrated"],
                    "provenance": provenance.stamp(worlds=MAB.N_SIMS, model="legacy-discrete", seed=MAB.SEED)},
           "boards": {}}
    for dg in dgs:
        p = os.path.join(DATA, f"_res_{dg}.json")
        out["boards"][str(dg)] = json.load(open(p))
        os.remove(p)
    with open(os.path.join(DATA, "sd_rank_resolution.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "board":
        board(int(a[1]))
    elif a and a[0] == "all":
        run_all(tuple(int(x) for x in a[1:]) or MAB.DGS)
        print("rank resolution written")
    else:
        print(__doc__)
