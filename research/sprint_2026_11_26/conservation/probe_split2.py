import sys, os
os.environ.setdefault("VIGIL_NO_BG", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))  # the repo root
import numpy as np
from nfl_dfs_csim import _split_int
rng = np.random.default_rng(1)
cnt = {"td>cmp": 0, "cmp>att": 0, "int>inc": 0, "yd!=0 with cmp 0": 0}
ex = {}
for trial in range(20000):
    k = 2
    sh = rng.dirichlet(np.ones(k))
    att = int(rng.integers(0, 50)); comp = int(rng.integers(0, att + 1)); td = int(rng.integers(0, min(comp, 5) + 1))
    it = int(rng.integers(0, min(att - comp, 3) + 1)); yd = comp * int(rng.integers(3, 15))
    S = {n: _split_int(np.array([v]), sh) for n, v in (("a", att), ("c", comp), ("d", td), ("i", it), ("y", yd))}
    for q in range(k):
        a, c, d, i_, y = (int(S[n][q][0]) for n in "acdiy")
        for key, cond in (("td>cmp", d > c), ("cmp>att", c > a), ("int>inc", i_ > a - c), ("yd!=0 with cmp 0", c == 0 and y != 0)):
            if cond:
                cnt[key] += 1; ex.setdefault(key, (list(np.round(sh, 3)), dict(att=att, comp=comp, td=td, int=it, yd=yd), q, dict(a=a, c=c, d=d, i=i_, y=y)))
print(cnt)
for k, v in ex.items(): print(k, v)
