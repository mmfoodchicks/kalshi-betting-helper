"""Guards for the exact portfolio acceleration (2026-11-27; dfs_tourney.greedy_cover / _CoverStep / PackedRows,
portfolio_vs_field / _make_it_rows / _make_it_world). The owner: "Continue ... portfolio acceleration", exact
methods only -- every chosen lineup, in order, and every figure the board shows identical to the old pass's bit
for bit, on DraftKings' 0.02-point lattice, at 1 and N workers, with and without the owner's caps, with
checkpoints on and off, for BOTH formats (the classic board's uniform field and the showdown's weighted
universe both come through portfolio_vs_field).

The reference is the old code itself, frozen below verbatim but for names (the pass and the cover as they stood
at e316055): every check compares the new pass with it, never with a re-derivation.

What each group proves:
  PS1 the cover: the packed, screened, lazy cover chooses what the old loop chose, in order, with the same
      coverage floats, on matrices built to break it -- the board's own shape (mostly exact 0, many exact 1,
      a few fractions), twins and exact ties, all-zero tails, dense random rows, float64 input, the owner's caps
      binding and not, and a non-finite matrix (the old loop itself);
  PS2 the pass, classic (nine-player lineups, a uniform field) and showdown (a captain at 1.5x, a weighted
      universe that IS the field): portfolio_vs_field equals the frozen old pass, every contest, every seed,
      ties forced (twin candidates, twin field lineups, flat worlds), workers 1 and 4, caps on;
  PS3 every route of the per-world shortcut gives the old matrix exactly: the top-of-field region, the
      region the sample left short (recounted whole), no region (a floor too deep to be worth it), and no field
      at all (every contest's value the same everywhere); the per-world indices equal the old tables';
  PS4 checkpoints: a build checkpointed and one resumed from every range file equal the uncheckpointed pass, the
      resume scores no world, and the file names carry the packed format (an old matrix range is never read);
  PS5 past the memory budget the rows go to files, the answer is the same, the files are gone afterwards;
  PS6 the numbers the docstrings claim: the cover's error bound is the one in _CoverStep, the region's floor is
      read off each contest's own float32 values.

Hermetic, numpy only (skipped with a stated reason where numpy is missing).

    python3 tests/guards_auto_portfolio_speed.py      # exits 1 on any failed check
"""
import os
import sys
import tempfile

os.environ.setdefault("VIGIL_NO_BG", "1")
os.environ.setdefault("VIGIL_POOL_CACHE_DIR", "")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---- the old code, frozen (dfs_tourney at e316055) -------------------------------------------------------------
def old_greedy(np, P, k, limits=None):
    """greedy_cover at e316055, verbatim but for the name and `np`."""
    P = np.asarray(P)
    K, N = P.shape
    if limits is not None:
        limits.reset(k)
    if not K or not N:
        if limits is not None:
            limits.finish(k, [], 1 if k else None)
        return [], []
    rows_at_once = max(1, int(4_000_000 // max(N, 1)))
    chosen = []
    cov = np.zeros(N, dtype=np.float64)
    p_any = []
    stop = None
    for _ in range(min(k, K)):
        gain = np.empty(K, dtype=np.float64)
        for a in range(0, K, rows_at_once):
            blk = P[a:a + rows_at_once].astype(np.float64, copy=False)
            gain[a:a + rows_at_once] = np.maximum(blk - cov[None, :], 0.0).sum(axis=1)
        if chosen:
            gain[chosen] = -1.0
        if limits is not None:
            free = int(np.argmax(gain))
            gain[limits.blocked(len(chosen) + 1)] = -2.0
            j = int(np.argmax(gain))
            if gain[j] < 0.0:
                stop = len(chosen) + 1
                break
            limits.take(j, changed=j != free)
        else:
            j = int(np.argmax(gain))
        chosen.append(j)
        cov = np.maximum(cov, P[j].astype(np.float64))
        p_any.append(float(cov.mean()))
    if limits is not None:
        limits.finish(k, chosen, stop)
    return chosen, p_any


def old_make_it_range(job):
    """_make_it_range at e316055 (no checkpoint): one world range's (contests, K', worlds) make-it matrix."""
    import numpy as np
    import dfs_tourney as T
    a, b, chunk = job
    WfT, WkT, X, wf, F_grid, tabs = (T._PAR["WfT"], T._PAR["WkT"], T._PAR["X"], T._PAR["wf"],
                                     T._PAR["F_grid"], T._PAR["tabs"])
    out = np.zeros((len(tabs), WkT.shape[1], b - a), dtype=np.float32)
    for start in range(a, b, chunk):
        Xc = np.ascontiguousarray(X[:, start:min(b, start + chunk)].T)
        Bf = T._buckets(Xc @ WfT)
        Bk = T._buckets(Xc @ WkT)
        for j in range(Bf.shape[0]):
            pos = T._world_tables(Bf[j], wf, F_grid)
            for gi, tab in enumerate(tabs):
                out[gi, :, start - a + j] = tab[pos][Bk[j]]
    return out


def old_matrix(np, T, Wc, Wf, wf, X, grids, cand_idx, chunk=500, rank="top1"):
    """The old pass's (contests, K', N) make-it matrix, one process (its answer never depended on the count)."""
    K, M, N = Wc.shape[0], Wf.shape[0], X.shape[1]
    ch = T.chunk_for(max(K, M), chunk)
    arrays = {"WfT": np.ascontiguousarray(Wf.T, dtype=np.float32),
              "WkT": np.ascontiguousarray(Wc[cand_idx].T, dtype=np.float32),
              "X": np.ascontiguousarray(X, dtype=np.float32), "wf": np.asarray(wf, dtype=np.float64)}
    extra = {"F_grid": grids[0]["F"], "tabs": [g[rank] for g in grids]}
    parts = T._par_map(old_make_it_range, T._range_jobs(N, 1, ch), 1, arrays, extra)
    return np.concatenate(parts, axis=2)


def old_portfolio(np, T, Wc, Wf, wf, X, grids, cand_idx, k, chunk=500, rank="top1", limits=None):
    """portfolio_vs_field at e316055 (the in-memory matrix): per contest (chosen, p_any)."""
    if not len(cand_idx):
        return [([], []) for _ in grids]
    P = old_matrix(np, T, Wc, Wf, wf, X, grids, cand_idx, chunk=chunk, rank=rank)
    return [old_greedy(np, P[gi], k, limits=limits) for gi in range(len(grids))]


# ---- cases on DraftKings' lattice --------------------------------------------------------------------------------
_PAY = ({"from": 1, "to": 1, "prize": 100000.0}, {"from": 2, "to": 50, "prize": 500.0},
        {"from": 51, "to": 4000, "prize": 40.0})


_GRIDS = {}


def _grid(T, *args):
    """payout_grid, built once a run (a grid is seconds of work and the same for every seed)."""
    key = repr(args)
    if key not in _GRIDS:
        _GRIDS[key] = T.payout_grid(args[0], list(args[1]), args[2], args[3])
    return _GRIDS[key]


def classic_case(np, seed, P=40, K=160, M=3000, N=360, twins=True):
    """Nine-player lineups, the 0.02 lattice, a uniform field, three contests; twin candidates, twin field
    lineups and a few flat worlds (every player the same score) force exact ties."""
    import dfs_tourney as T
    rng = np.random.default_rng(seed)

    def lineups(n):
        W = np.zeros((n, P), dtype=np.float32)
        for i in range(n):
            W[i, rng.choice(P, 9, replace=False)] = 1.0
        return W
    X = (np.round(rng.gamma(2.0, 5.0, (P, N)) * 50) / 50).astype(np.float32)
    Wc, Wf = lineups(K), lineups(M)
    if twins:
        Wc[7] = Wc[3]
        Wc[K - 1] = Wc[0]
        Wf[: M // 50] = Wf[M // 50: 2 * (M // 50)]
        Wc[11] = Wf[5]                                   # a candidate the field also plays
        X[:, :3] = np.float32(6.0)                       # flat worlds: every lineup ties every other
    grids = [_grid(T, 20000, _PAY, 20.0, 4000), _grid(T, 5000, _PAY[:2], 5.0, 50), _grid(T, 900, _PAY[:1], 3.0, 1)]
    return T, Wc, Wf, np.full(M, 1.0 / M), X, grids


def showdown_case(np, seed, P=22, N=300, n_universe=2500):
    """Six-player lineups with a captain at 1.5x, a weighted universe that is both the candidates and the field
    (the showdown board's `portfolio`), one contest."""
    import dfs_tourney as T
    rng = np.random.default_rng(seed)
    idx = np.stack([rng.choice(P, 6, replace=False) for _ in range(n_universe)])
    idx[1] = idx[0]                                      # a twin in the universe
    W = np.zeros((n_universe, P), dtype=np.float32)
    for i, row in enumerate(idx):
        W[i, row[1:]] = 1.0
        W[i, row[0]] = 1.5
    X = (np.round(rng.gamma(2.0, 4.0, (P, N)) * 50) / 50).astype(np.float32)
    X[:, 0] = np.float32(4.0)
    f = rng.gamma(0.6, 1.0, n_universe)
    f /= f.sum()
    grid = _grid(T, 30000, _PAY, 10.0, 4000)
    return T, W, X, f, grid


def _limits(np, D, n_rows, seed, binding=True):
    rng = np.random.default_rng(seed)
    NP, NT = 30, 4
    mem = np.stack([rng.choice(NP, size=6, replace=False) for _ in range(n_rows)])
    pcap = [40 if binding else 100] * NP
    pcap[0] = -1
    tcap = [50 if binding else 100] * NT
    return D.Limits(mem, rng.integers(0, NT, size=n_rows), pcap, tcap, 3 if binding else 6, (5, 10))


def _env(**kv):
    import contextlib

    @contextlib.contextmanager
    def cm():
        old = {k: os.environ.get(k) for k in kv}
        try:
            for k, v in kv.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            yield
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return cm()


# ---- PS1 the cover -------------------------------------------------------------------------------------------------
def _board_like(np, rng, K, N, p_one=0.03, p_frac=0.01):
    """The board's matrix shape: exact 0 mostly, exact 1 often, a few float32 fractions from a short value list."""
    vals = np.concatenate([[1.0], rng.random(40)]).astype(np.float32)
    u = rng.random((K, N))
    P = np.zeros((K, N), dtype=np.float32)
    P[u < p_one] = 1.0
    fr = (u >= p_one) & (u < p_one + p_frac)
    P[fr] = vals[rng.integers(1, len(vals), size=int(fr.sum()))]
    return P


def _cover_checks(ck, np, T, D):
    rng = np.random.default_rng(20261127)
    same, cases = [], 0
    for trial in range(40):
        K, N = int(rng.integers(1, 90)), int(rng.integers(1, 700))
        kind = trial % 5
        if kind == 0:
            P = _board_like(np, rng, K, N)
        elif kind == 1:
            P = rng.random((K, N)).astype(np.float32)                  # dense
        elif kind == 2:
            P = _board_like(np, rng, K, N, p_one=0.2, p_frac=0.0)      # ones only: ties everywhere
        elif kind == 3:
            P = rng.random((K, N))                                     # float64 input
            P[P < 0.7] = 0.0
        else:
            P = _board_like(np, rng, K, N, p_one=0.002, p_frac=0.002)  # almost empty: all-zero tails
        if K > 4:
            P[2] = P[K - 1]                                            # twins at both ends of the index order
        k = int(rng.integers(1, 30))
        same.append(T.greedy_cover(P, k) == old_greedy(np, P, k))
        if K > 2:
            Ln, Lo = _limits(np, D, K, trial), _limits(np, D, K, trial)
            got, ref = T.greedy_cover(P, k, limits=Ln), old_greedy(np, P, k, limits=Lo)
            same.append(got == ref and Ln.reports == Lo.reports and Ln.changed == Lo.changed)
            Ln, Lo = _limits(np, D, K, trial, binding=False), _limits(np, D, K, trial, binding=False)
            same.append(T.greedy_cover(P, k, limits=Ln) == old_greedy(np, P, k, limits=Lo)
                        and Ln.reports == Lo.reports)
        cases += 1
    # the packed form handed in directly, and a non-finite matrix (the old loop itself)
    P = _board_like(np, rng, 50, 400)
    packed = T.PackedRows.from_dense(P)
    same.append(T.greedy_cover(packed, 12) == old_greedy(np, P, 12))
    Pn = P.copy()
    Pn[4, 7] = np.inf
    with np.errstate(invalid="ignore"):                  # inf - inf, in both loops alike
        same.append(T.greedy_cover(Pn, 6) == old_greedy(np, Pn, 6))
    zero = T.greedy_cover(np.zeros((0, 5)), 3) == ([], []) and T.greedy_cover(np.zeros((4, 0)), 3) == ([], [])
    # the screen did not sum every row at every step (the point of it), on the board's shape
    P = _board_like(np, np.random.default_rng(5), 600, 3000)
    seen = []
    real = T.PackedRows.screen

    def counted(self, rows, cov):
        seen.append(len(rows))
        return real(self, rows, cov)
    T.PackedRows.screen = counted
    try:
        lazy_ok = T.greedy_cover(P, 20) == old_greedy(np, P, 20)
    finally:
        T.PackedRows.screen = real
    ck("PS1 the cover: the packed, screened, lazy cover chooses exactly what the old loop chose, in order, with the "
       f"same coverage floats, on {cases} matrices built to break it (the board's shape, dense rows, ones only, "
       "float64, almost empty; twins at both ends of the index order) with the owner's caps binding and not (the "
       "same picks, the same reports, the same 'a cap changed this pick' marks), the packed rows handed in, and "
       "a non-finite matrix through the old loop; and on the board's shape it re-screened far fewer rows than "
       "the old loop's 20 x 600",
       all(same) and zero and lazy_ok and sum(seen) < 0.5 * 20 * 600,
       f"{sum(1 for s in same if not s)} differ; screened {sum(seen)} rows")


# ---- PS2 the pass, both formats --------------------------------------------------------------------------------------
def _pass_checks(ck, np, D):
    res = []
    for seed in range(6):
        T, Wc, Wf, wf, X, grids = classic_case(np, 100 + seed, N=240)
        cand = np.arange(0, Wc.shape[0], 1 + seed % 3)
        k = 6 + seed
        Po = old_matrix(np, T, Wc, Wf, wf, X, grids, cand, chunk=64)          # the old pass, once a case
        ref = [old_greedy(np, Po[gi], k) for gi in range(len(grids))]
        res.append(("classic", seed, 1, T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, k, chunk=64) == ref))
        if seed < 2:
            res.append(("classic", seed, 4,
                        T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, k, chunk=64, workers=4) == ref))
            Ln, Lo = _limits(np, D, len(cand), seed), _limits(np, D, len(cand), seed)
            got = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, k, chunk=64, limits=Ln, workers=2 + seed)
            refl = [old_greedy(np, Po[gi], k, limits=Lo) for gi in range(len(grids))]
            res.append(("classic caps", seed, 2 + seed, got == refl and Ln.reports == Lo.reports))
        if seed == 0:
            for rank in ("top01", "cash"):
                res.append((rank, seed, 1, T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, k, chunk=64, rank=rank)
                            == old_portfolio(np, T, Wc, Wf, wf, X, grids, cand, k, chunk=64, rank=rank)))
    for seed in range(4):
        T, W, X, f, grid = showdown_case(np, 200 + seed, N=240)
        cand = np.sort(np.random.default_rng(seed).choice(W.shape[0], 150, replace=False))
        cand = np.unique(np.concatenate([cand, [0, 1]]))       # the universe's twins, both candidates
        Po = old_matrix(np, T, W, W, f, X, [grid], cand, chunk=64)[0]
        ref = old_greedy(np, Po, 10)
        res.append(("showdown", seed, 1, T.portfolio(W, X, f, grid, cand, 10, chunk=64) == ref))
        if seed < 2:
            res.append(("showdown", seed, 4, T.portfolio(W, X, f, grid, cand, 10, chunk=64, workers=4) == ref))
            Ln, Lo = _limits(np, D, len(cand), seed), _limits(np, D, len(cand), seed)
            got = T.portfolio(W, X, f, grid, cand, 10, chunk=64, limits=Ln, workers=3)
            refl = old_greedy(np, Po, 10, limits=Lo)
            res.append(("showdown caps", seed, 3, got == refl and Ln.reports == Lo.reports))
    bad = [r[:3] for r in res if not r[3]]
    ck("PS2 the pass, both formats: portfolio_vs_field (classic: nine-player lineups, a uniform field, three "
       "contests) and portfolio (showdown: a captain at 1.5x, the weighted universe as the field) choose exactly "
       "the old pass's entries, in order, with the same coverage floats -- every seed, twin candidates, twin field "
       "lineups and flat worlds forcing ties, 1 to 5 workers, the owner's caps on, the top-0.1% and cash columns too",
       not bad and len(res) == 20, f"{len(res)} runs; differ: {bad}")


# ---- PS3 every route of the per-world shortcut ---------------------------------------------------------------------------
def _new_matrix(np, T, Wc, Wf, wf, X, grids, cand, rank="top1", chunk=64):
    """The new pass's packed rows, made dense: (contests, K', N)."""
    N = X.shape[1]
    arrays = {"WfT": np.ascontiguousarray(Wf.T, dtype=np.float32),
              "WkT": np.ascontiguousarray(Wc[cand].T, dtype=np.float32),
              "X": np.ascontiguousarray(X, dtype=np.float32), "wf": np.asarray(wf, dtype=np.float64)}
    tabs32 = np.stack([np.asarray(g[rank]).astype(np.float32) for g in grids])
    extra = {"F_grid": grids[0]["F"], "tabs32": tabs32, "region": T._make_it_region(tabs32, grids[0]["F"], arrays["wf"])}
    parts = T._par_map(T._make_it_rows, T._range_jobs(N, 1, T.chunk_for(max(Wc.shape[0], Wf.shape[0]), chunk)), 1,
                       arrays, extra)
    out = []
    for gi in range(len(grids)):
        R = T.PackedRows(len(cand), N, [(p[f"ip{gi}"], p[f"c{gi}"], p[f"v{gi}"]) for p in parts])
        out.append(R.dense(np.arange(len(cand))).astype(np.float32))
    return np.stack(out), extra["region"]


def _route_checks(ck, np):
    import dfs_tourney as T
    T0, Wc, Wf, wf, X, grids = classic_case(np, 7)
    cand = np.arange(Wc.shape[0])
    routes = {}
    calls = {"region": 0, "whole": 0}
    real_world, real_b = T._make_it_world, T._buckets

    def spy(s, bc, wf_, F_grid, thr, reg):
        n0 = [0]

        def counting(St):
            n0[0] += len(St)
            return real_b(St)
        T._buckets = counting
        try:
            r = real_world(s, bc, wf_, F_grid, thr, reg)
        finally:
            T._buckets = real_b
        calls["whole" if n0[0] >= len(s) else "region"] += 1
        return r
    old_safety = T._REGION_SAFETY
    try:
        T._make_it_world = spy
        for name, safety in (("region", old_safety), ("short region, recounted whole", 0.05), ("no region", 1e9)):
            T._REGION_SAFETY = safety
            calls.update(region=0, whole=0)
            ref = old_matrix(np, T, Wc, Wf, wf, X, grids, cand, chunk=64)
            got, reg = _new_matrix(np, T, Wc, Wf, wf, X, grids, cand)
            routes[name] = (got.tobytes() == ref.tobytes(), dict(calls), reg is not None and reg["target"] is not None)
        T._REGION_SAFETY = old_safety
        # no field at all: a column whose float32 value is the same at every index
        flat = [dict(g, top1=np.full(len(g["F"]), 0.25)) for g in grids]
        ref = old_matrix(np, T, Wc, Wf, wf, X, flat, cand, chunk=64)
        got, reg = _new_matrix(np, T, Wc, Wf, wf, X, flat, cand)
        routes["no field"] = (got.tobytes() == ref.tobytes(), dict(calls), reg is None)
        # the showdown's weighted universe through the region
        Ts, W, Xs, f, grid = showdown_case(np, 9)
        cs = np.arange(0, W.shape[0], 7)
        calls.update(region=0, whole=0)
        ref = old_matrix(np, T, W, W, f, Xs, [grid], cs, chunk=64)
        got, reg = _new_matrix(np, T, W, W, f, Xs, [grid], cs)
        # (a weighted universe samples with replacement in proportion to the weights: repeats in the sample)
        routes["showdown region"] = (got.tobytes() == ref.tobytes(), dict(calls),
                                     reg["target"] is not None and len(np.unique(reg["samp"])) < len(reg["samp"]))
    finally:
        T._make_it_world, T._buckets, T._REGION_SAFETY = real_world, real_b, old_safety
    ok = (all(v[0] for v in routes.values())
          and routes["region"][1]["region"] > 0 and routes["region"][2]
          and routes["short region, recounted whole"][1]["whole"] > 0
          and routes["no region"][1]["region"] == 0 and not routes["no region"][2]
          and routes["no field"][2] and routes["showdown region"][1]["region"] > 0 and routes["showdown region"][2])
    ck("PS3 every route of the per-world shortcut gives the old (contests, candidates, worlds) matrix byte for byte: "
       "the top of the field alone (most worlds), a region the sample left short (that world recounted whole), no "
       "region (a floor too deep to be worth one), no field at all (a column the same everywhere), and the "
       "showdown's weighted universe through its weighted sample",
       ok, str({k: v for k, v in routes.items()}))


# ---- PS4 checkpoints -------------------------------------------------------------------------------------------------
def _ckpt_checks(ck, np):
    import build_ckpt as BC
    import dfs_tourney as T
    T0, Wc, Wf, wf, X, grids = classic_case(np, 31)
    cand = np.arange(0, Wc.shape[0], 2)
    root = tempfile.mkdtemp()
    out = {}
    try:
        with _env(VIGIL_CKPT_DIR=root):
            ref = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 8, chunk=32)
            old = old_portfolio(np, T, Wc, Wf, wf, X, grids, cand, 8, chunk=32)
            runs = {}
            for w in (1, 3):
                T._CKPT[0] = BC.open_build("guard", f"ps4-{w}")
                try:
                    runs[(w, "first")] = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 8, chunk=32, workers=w)
                    path = T._CKPT[0].path
                finally:
                    T._CKPT[0] = None
                n = [0]
                real = T._make_it_world

                def counted(*a, **kw):
                    n[0] += 1
                    return real(*a, **kw)
                T._make_it_world = counted               # counts in this process: the one-worker pass's worlds
                try:
                    T._CKPT[0] = BC.open_build("guard", f"ps4-{w}")
                    runs[(w, "resumed")] = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 8, chunk=32, workers=w)
                finally:
                    T._make_it_world = real
                    T._CKPT[0] = None
                if w == 1:
                    files = sorted(fn for fn in os.listdir(path) if fn.startswith("makeit-"))
                    payload = BC._arrays_from(BC.read_file(os.path.join(path, files[0])))
                    out = {"files": len(files), "keys": sorted(payload), "rescored": n[0]}
                else:
                    out["files3"] = len([fn for fn in os.listdir(path) if fn.startswith("makeit-")])
            src = open(os.path.join(_ROOT, "dfs_tourney.py")).read()
            sig_has_format = '"format": "packed-rows-v1"' in src
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    ck("PS4 checkpoints: the pass checkpointed range by range and the pass resumed from those files both equal the "
       "uncheckpointed pass and the old pass exactly; the resume scored no world; the files hold packed rows (ip/c/v "
       "per contest, no 'out' matrix) and the pass's signature names that format, so an old matrix range is never "
       "read as rows",
       ref == old and all(v == ref for v in runs.values()) and len(runs) == 4 and out.get("rescored") == 0
       and out.get("files") == 1 and out.get("files3", 0) >= 3
       and "out" not in out.get("keys", ["out"]) and "ip0" in out.get("keys", []) and sig_has_format, str(out))


# ---- PS5 the memory budget ------------------------------------------------------------------------------------------
def _spill_checks(ck, np):
    import dfs_tourney as T
    T0, Wc, Wf, wf, X, grids = classic_case(np, 41)
    cand = np.arange(0, Wc.shape[0], 2)
    ref = old_portfolio(np, T, Wc, Wf, wf, X, grids, cand, 7, chunk=32)
    scratch = tempfile.mkdtemp()
    seen = []
    real_open = np.lib.format.open_memmap

    def spy(path, *a, **k):
        seen.append(path)
        return real_open(path, *a, **k)
    np.lib.format.open_memmap = spy
    try:
        with _env(VIGIL_SCRATCH_DIR=scratch, VIGIL_MAKEIT_RAM_GB="0.0000001"):
            on_disk = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 7, chunk=32)
            on_disk2 = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 7, chunk=32, workers=2)
    finally:
        np.lib.format.open_memmap = real_open
    left = os.listdir(scratch)
    ck("PS5 past the make-it memory budget each range's packed rows go to files in the scratch directory and are "
       "read back mapped: the same portfolio as the old pass (one process and two), and the files are gone "
       "afterwards",
       on_disk == ref and on_disk2 == ref and left == [] and len(seen) >= 6
       and all(os.path.dirname(p) == scratch and os.path.basename(p).startswith("makeit-") for p in seen),
       f"{len(seen)} files, left {left}")


# ---- PS6 the definitions --------------------------------------------------------------------------------------------
def _definition_checks(ck, np):
    import inspect
    import dfs_tourney as T
    step = inspect.getsource(T._CoverStep)
    reg = inspect.getsource(T._make_it_region)
    world = inspect.getsource(T._make_it_world)
    _T, Wc, Wf, wf, X, grids = classic_case(np, 1, twins=False)
    tabs32 = np.stack([np.asarray(g["top1"]).astype(np.float32) for g in grids])
    r = T._make_it_region(tabs32, grids[0]["F"], wf)
    i_sat = int(np.searchsorted(grids[0]["F"], r["F_cut"]) + 1)
    sat = all(np.all(tabs32[gi, i_sat:].view(np.uint32) == tabs32[gi, -1:].view(np.uint32)) for gi in range(3)) \
        and any(tabs32[gi, i_sat - 1] != tabs32[gi, -1] for gi in range(3))
    ck("PS6 the definitions the docstrings state: the cover's tolerance is 4 (N + 2 parts + 8) u -- twice the "
       "float64 bound for two summation orders of N terms -- and the window is every row whose ceiling reaches the "
       "best floor; the region's floor F_cut is F_grid[i_sat - 1], i_sat the first index from which EVERY contest's "
       "float32 value is bit-equal to its last; a region is trusted only past F_cut plus a margin",
       "4.0 * (R.shape[1] + 2 * len(R.parts) + 8) * 2.0 ** -53" in step
       and "win = done[S[done] * (1.0 + tol) >= floor]" in step
       and "bits != bits[:, -1:]" in reg and 'F_cut = float(F_grid[i_sat - 1])' in reg
       and 'if C[0] > reg["F_cut"] + reg["margin"]:' in world and sat,
       f"i_sat {i_sat} saturated {sat}")


def run(ck):
    try:
        import numpy as np
    except ImportError:
        ck("(skipped for a stated reason: numpy is not installed here; the boards are the PC's) PS the exact "
           "portfolio acceleration", True)
        return
    import dfs_portfolio as D
    import dfs_tourney as T
    import time
    for label, fn, args in (("PS1", _cover_checks, (ck, np, T, D)), ("PS2", _pass_checks, (ck, np, D)),
                            ("PS3", _route_checks, (ck, np)), ("PS4", _ckpt_checks, (ck, np)),
                            ("PS5", _spill_checks, (ck, np)), ("PS6", _definition_checks, (ck, np))):
        t0 = time.time()
        try:
            fn(*args)
            print(f"    ({label} {time.time() - t0:.1f}s)")
        except Exception as e:
            import traceback
            ck(f"{label} (the checks raised)", False, f"{type(e).__name__}: {e} " + traceback.format_exc()[-900:])


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name[:150] + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
