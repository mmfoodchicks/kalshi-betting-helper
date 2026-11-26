import sys, os, time
os.environ.setdefault("VIGIL_NO_BG", "1")
os.environ.setdefault("ERRLOG_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "errlog.db"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))  # the repo root
import numpy as np
import dfs_tourney as T
rng = np.random.default_rng(7)
P, N, K = 60, 400, 3000
u = rng.integers(-150, 2200, size=(P, N))          # 0.02-point units (negative: a QB's bad day)
X = np.asarray([[round(float(v) * 0.02, 2) for v in row] for row in u], dtype=np.float32)
idx = np.stack([rng.choice(P, 9, replace=False) for _ in range(K)]).astype(np.int32)
W = T.lineup_matrix(idx, P)
exact = 2 * u[idx].sum(axis=1)                       # (K, N) buckets of 0.01
exact = np.clip(exact, 0, T._NB - 1)
B1 = T._buckets(np.ascontiguousarray(X.T) @ np.ascontiguousarray(W.T))   # (N, K)
perm = rng.permutation(P)
B2 = T._buckets(np.ascontiguousarray(X[perm].T) @ np.ascontiguousarray(W[:, perm].T))
B3 = np.concatenate([T._buckets(np.ascontiguousarray(X[:, a:a+37].T) @ np.ascontiguousarray(W.T)) for a in range(0, N, 37)])
print("classic exact vs matmul", bool(np.array_equal(B1.T, exact)), "perm", bool(np.array_equal(B2.T, exact)), "chunks", bool(np.array_equal(B3.T, exact)))
# showdown
idx6 = np.stack([rng.choice(P, 6, replace=False) for _ in range(K)]).astype(np.int32)
W6 = T.lineup_matrix(idx6, P, cpt_mult=1.5)
ex6 = np.clip(3 * u[idx6[:, 0]] + 2 * u[idx6[:, 1:]].sum(axis=1), 0, T._NB - 1)
S6 = T._buckets(np.ascontiguousarray(X.T) @ np.ascontiguousarray(W6.T))
print("showdown exact vs matmul", bool(np.array_equal(S6.T, ex6)))
# max float error vs half-bucket
S = (np.ascontiguousarray(X.T) @ np.ascontiguousarray(W.T)).astype(np.float64)
print("max |float32 lineup - exact| =", float(np.abs(S - (2 * u[idx].sum(axis=1)).T / 100).max()))
# off-lattice: 0.01-grid values x 1.5 sit on half buckets
v = np.asarray([round(0.01 * k, 2) for k in range(1, 4001, 2)], dtype=np.float32)   # odd hundredths
cpt = (v * np.float32(1.5)).astype(np.float32)
b_a = T._buckets(cpt.copy()[None, :])[0]
b_b = T._buckets((cpt + np.float32(0.0)).astype(np.float32)[None, :])[0]
print("off-lattice captain scores at a half bucket:", len(v), "half-hundredths; buckets that round up", int((b_a == np.rint(v*150).astype(int)+0).sum()))
# summation order flip search: float32 sums of off-lattice values
flips = 0; ex = None
for t in range(20000):
    a = rng.integers(1, 4000, size=6).astype(np.float64) * 0.005     # 0.005 grid (CPT 1.5 x 0.01)
    a32 = a.astype(np.float32)
    s1 = np.float32(0); s2 = np.float32(0)
    for x in a32: s1 = np.float32(s1 + x)
    for x in a32[::-1]: s2 = np.float32(s2 + x)
    b1 = T._buckets(np.asarray([[s1]], dtype=np.float32))[0, 0]; b2 = T._buckets(np.asarray([[s2]], dtype=np.float32))[0, 0]
    if b1 != b2:
        flips += 1; ex = ex or (list(map(float, a)), float(s1), float(s2), int(b1), int(b2))
print("off-lattice 0.005-grid sums whose bucket flips with summation order:", flips, "of 20000; e.g.", ex)
