import sys, os
os.environ.setdefault("VIGIL_NO_BG", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))  # the repo root
import numpy as np
from nfl_dfs_csim import _multinomial_capped as MC, _multinomial as M, _capped_short as CS

def run(name, N, w, cap, seed=0):
    rng = np.random.default_rng(seed)
    N = np.asarray(N, dtype=np.int64)
    w = np.asarray(w, dtype=np.float64); cap = np.asarray(cap, dtype=np.int64)
    a = MC(rng, N, w, cap)
    want = np.minimum(N, cap.sum(axis=0))
    lost = want - a.sum(axis=0)
    over = (a > cap).sum()
    neg = (a < 0).sum()
    print(f"{name:55s} sum={a.sum(axis=0)[:6]} want={want[:6]} lost_worlds={(lost!=0).sum()} lost_total={lost.sum()} over_cap={over} neg={neg} short={CS(N,cap)[:4]}")
    return a

n = 1000
# zero budget
run("zero budget", np.zeros(n), np.ones((3, n)), np.full((3, n), 2))
# exact capacity
run("exact capacity 3 players cap [1,2,3] N=6", np.full(n, 6), np.ones((3, n)), np.tile(np.array([[1],[2],[3]]), (1, n)))
# insufficient capacity
run("insufficient capacity N=10 cap 6", np.full(n, 10), np.ones((3, n)), np.tile(np.array([[1],[2],[3]]), (1, n)))
# zero-capacity player with huge weight
run("zero-cap player weight 1e9", np.full(n, 3), np.tile(np.array([[1e9],[1.0],[1.0]]), (1, n)), np.tile(np.array([[0],[2],[2]]), (1, n)))
# extreme weights
run("extreme weights 1e300/1e-300", np.full(n, 3), np.tile(np.array([[1e300],[1e-300]]), (1, n)), np.tile(np.array([[1],[5]]), (1, n)))
run("tiny weights 1e-13 (below the 1e-12 floor)", np.full(n, 3), np.full((2, n), 1e-13), np.full((2, n), 2))
# zero weight with capacity
run("zero-weight player WITH capacity (cap [1,5], w [1,0], N=3)", np.full(n, 3), np.tile(np.array([[1.0],[0.0]]), (1, n)), np.tile(np.array([[1],[5]]), (1, n)))
# many players, geometric weights, cap 1 each: rounds
for k in (12, 13, 20):
    w = np.tile((10.0 ** (-3 * np.arange(k)))[:, None], (1, n))
    run(f"k={k} cap 1 each, geometric weights 1e-3^i, N=k", np.full(n, k), w, np.ones((k, n), dtype=np.int64))
# equal weights large budget
for k in (13, 20, 40):
    run(f"k={k} cap 1 each, equal weights, N=k", np.full(n, k), np.ones((k, n)), np.ones((k, n), dtype=np.int64))
# large budgets vs capacity
run("N=1e6 cap 3x[5]", np.full(n, 10**6), np.ones((3, n)), np.full((3, n), 5))
run("N=1e6 cap 3x[1e6]", np.full(n, 10**6), np.ones((3, n)), np.full((3, n), 10**6))
