"""The compact pool at scale: exact scoring, rounding, ties and rankings (2026-11-27).

The owner, on the compact pool (nfl_dfs_sim.player_pool(compact=True): float32 rows instead of lists of
Python floats): "provided it preserves exact scoring, rounding, ties and lineup rankings. That needs actual
numerical verification." tests/guards_auto_memory.py checks it on small cases and one whole board; this
runs the three paths a player's worlds can take into the tournament's score matrix X on a slate-sized
sample and compares everything the tournament reads from X:

  lists    the old path: np.round(arr, 2).tolist(), then np.asarray(rows, float32)
  compact  pool_cache.squeeze (int32 hundredths while built) then as_float32
  cache    pool_cache.encode of the lists, written and read back by decode_f32 (the PC's disk cache)

and, on each X: every lineup's score in every world (the float32 product the passes compute), its 0.01-point
bucket (dfs_tourney._buckets), every world's ranking of the lineups (stable argsort on the bucket, the order
ties are broken in), the tie groups, each world's best lineup and the field mass above each score.

    python3 research/hp_memory_exact.py [--players 400] [--worlds 50000] [--lineups 100000]
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def worlds(players, n, seed):
    """Engine-shaped scores: continuous draws (gamma bodies, some negatives, a few large), rounded to 2 dp as
    nfl_dfs_sim rounds the engine's arrays, plus exact zeros and negative zeros."""
    rng = np.random.default_rng(seed)
    a = rng.gamma(1.6, 6.0, (players, n)) - rng.exponential(0.6, (players, n))
    a[rng.random((players, n)) < 0.02] *= 6.0
    a[rng.random((players, n)) < 0.01] = 0.0
    a[rng.random((players, n)) < 0.002] = -0.0
    return a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", type=int, default=400)
    ap.add_argument("--worlds", type=int, default=50000)
    ap.add_argument("--lineups", type=int, default=100000)
    ap.add_argument("--seed", type=int, default=20261127)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    import pool_cache as PC
    import dfs_tourney as T
    t0 = time.time()
    raw = worlds(a.players, a.worlds, a.seed)
    out = {"players": a.players, "worlds": a.worlds, "scores": a.players * a.worlds, "lineups": a.lineups}
    # --- the three paths into X ---
    X_list = np.asarray([np.round(r, 2).tolist() for r in raw], dtype=np.float32)
    X_comp = np.stack([PC.as_float32(PC.squeeze(np.round(r, 2))) for r in raw])
    m, dt, sc = PC.encode([np.round(r, 2).tolist() for r in raw[: min(a.players, 400)]])
    X_cache = np.stack([PC.decode_f32(m[i], dt, sc) for i in range(m.shape[0])])
    del raw
    k = X_cache.shape[0]
    out["X"] = {
        "compact_bytes_equal": bool(X_comp.tobytes() == X_list.tobytes()),
        "cache_rows_compared": int(k), "cache_dtype": dt,
        "cache_values_equal": bool(np.array_equal(X_cache, X_list[:k])),
        "cache_bytes_differ_only_at_signed_zeros": int(np.count_nonzero(
            (X_cache.view(np.uint32) != X_list[:k].view(np.uint32)) & ~((X_cache == 0) & (X_list[:k] == 0)))) == 0,
        "signed_zeros_in_lists": int(np.count_nonzero((X_list == 0) & np.signbit(X_list))),
    }
    # --- what the tournament reads from X: scores, buckets, rankings, ties, the best lineup, field mass ---
    rng = np.random.default_rng(a.seed + 1)
    L = a.lineups
    idx = np.stack([rng.choice(a.players, 9, replace=False) for _ in range(L)])
    W = np.zeros((L, a.players), dtype=np.float32)
    np.put_along_axis(W, idx, 1.0, axis=1)
    WT = np.ascontiguousarray(W.T)
    f = rng.dirichlet(np.ones(L)).astype(np.float64)
    chunk = 200
    eq = {"scores": True, "buckets": True, "rank_order": True, "tie_groups": True, "best": True, "mass_above": True}
    ties = worlds_checked = 0
    X_cmp = {"compact": X_comp, "cache": None}
    for start in range(0, a.worlds, chunk):
        stop = min(a.worlds, start + chunk)
        S0 = np.ascontiguousarray(X_list[:, start:stop].T) @ WT
        S1 = np.ascontiguousarray(X_cmp["compact"][:, start:stop].T) @ WT
        eq["scores"] &= bool(np.array_equal(S0, S1))
        B0, B1 = T._buckets(S0.copy()), T._buckets(S1.copy())
        eq["buckets"] &= bool(np.array_equal(B0, B1))
        for j in range(0, stop - start, 25):                       # rankings on every 25th world (cost)
            o0 = np.argsort(-B0[j], kind="stable")
            o1 = np.argsort(-B1[j], kind="stable")
            eq["rank_order"] &= bool(np.array_equal(o0, o1))
            _u0, c0 = np.unique(B0[j], return_counts=True)
            _u1, c1 = np.unique(B1[j], return_counts=True)
            eq["tie_groups"] &= bool(np.array_equal(c0, c1))
            ties += int(np.sum(c0[c0 > 1]))
            h0 = np.bincount(B0[j], weights=f, minlength=T._NB)
            h1 = np.bincount(B1[j], weights=f, minlength=T._NB)
            eq["mass_above"] &= bool(np.array_equal(np.cumsum(h0[::-1]), np.cumsum(h1[::-1])))
            worlds_checked += 1
        eq["best"] &= bool(np.array_equal(np.argmax(S0, axis=1), np.argmax(S1, axis=1)))
    out["downstream"] = eq
    out["ranked_worlds"] = worlds_checked
    out["tied_lineups_seen"] = ties
    # the cache path's rows, through the same product (the first k players only, so on lineups within them)
    sub = np.where(idx.max(axis=1) < k)[0][:20000]
    if len(sub):
        Wk = np.ascontiguousarray(W[sub][:, :k].T)
        Sa = np.ascontiguousarray(X_list[:k, :5000].T) @ Wk
        Sb = np.ascontiguousarray(X_cache[:, :5000].T) @ Wk
        out["cache_downstream"] = {"lineups": int(len(sub)), "worlds": 5000,
                                   "buckets_equal": bool(np.array_equal(T._buckets(Sa.copy()), T._buckets(Sb.copy())))}
    out["seconds"] = round(time.time() - t0, 1)
    out["verdict"] = "EXACT" if (out["X"]["compact_bytes_equal"] and out["X"]["cache_values_equal"]
                                 and all(eq.values()) and out.get("cache_downstream", {}).get("buckets_equal", True)) \
        else "DIFFERS"
    txt = json.dumps(out, indent=1)
    print(txt)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(txt + "\n")


if __name__ == "__main__":
    main()
