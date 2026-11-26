import sys, os
os.environ.setdefault("VIGIL_NO_BG", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))  # the repo root
import numpy as np
from nfl_dfs_csim import _split_int
from nfl_dfs_engine import _split_w
rng = np.random.default_rng(1)
bad = []
for trial in range(20000):
    k = int(rng.integers(2, 4))
    sh = rng.dirichlet(np.ones(k))
    att = int(rng.integers(0, 60)); comp = int(rng.integers(0, att + 1)); td = int(rng.integers(0, comp + 1))
    it = int(rng.integers(0, att - comp + 1))
    A = _split_int(np.array([att]), sh); C = _split_int(np.array([comp]), sh); D = _split_int(np.array([td]), sh)
    I = _split_int(np.array([it]), sh)
    for q in range(k):
        a, c, d, i_ = int(A[q][0]), int(C[q][0]), int(D[q][0]), int(I[q][0])
        if not (d <= c <= a) or i_ > a - c:
            bad.append((list(np.round(sh, 4)), att, comp, td, it, q, a, c, d, i_)); break
print("multi-QB split inconsistencies:", len(bad), "of 20000")
for b in bad[:5]: print(b)
# _split_w checks: zero weights with nonzero total, negative totals
w = np.array([[0.0, 1.0, 0.0], [0.0, 0.0, 0.0]])
print(_split_w(np.array([5, 7, -3]), w))
