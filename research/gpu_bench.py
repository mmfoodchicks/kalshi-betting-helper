"""CPU vs GPU on the tournament board's scoring kernel (2026-11-26, backlog
5.3; the owner: "Provide a reproducible benchmark script that I can execute on
my RTX 4070 SUPER ... Do not assume GPU acceleration is faster until measured.
Retain a CPU fallback and do not interrupt active production builds").

The kernel is the one that dominates a classic build (lineup scoring, 1,172 s
of the Thanksgiving MEGA's ~30 minutes): for every world, every field lineup's
score (a matrix product of the field's 0/1 weights with the players' points),
bucketed to DraftKings' 0.01 points, then the field's histogram over the
buckets and the mass strictly above each one (dfs_tourney._score_range /
_world_tables, and dfs_evaluate.prepare's thresholds). It is run twice on the
same inputs -- numpy on the CPU, CuPy on the GPU -- and the script reports, per
device:

  init_s        CuPy import + context + the first kernels (GPU only)
  h2d_s / d2h_s host-to-device and device-to-host copies
  matmul_s      the score products
  bucket_s      the bucketing
  hist_s        the per-world histograms and their cumulative mass, one world
                a call (production's loop: dfs_tourney._world_tables,
                dfs_evaluate.prepare)
  hist_batched_s the same histograms --hist-batch worlds a call (below)
  total_s       end to end, transfers included (both histogram passes)
  total_loop_s / total_batched_s   the end to end with ONE histogram pass
  peak_ram_mb   this process's peak resident memory
  peak_vram_mb  CuPy's memory pool high-water mark (GPU only)

and AGREEMENT: the bucket of every (world, lineup) and the threshold bucket of
every (world, fraction) compared exactly between the two devices, and the
batched thresholds against the loop's on each device. On
DraftKings' 0.02 lattice they should be identical (the conservation audit's
D1-D3: float32 summation error ~5.6e-5 against a 0.005 bucket margin); any
mismatch is counted and printed, never hidden.

Why a batched pass. The owner's first GPU run (2026-11-26, RTX 4070 SUPER,
20,000 worlds x 300,000 lineups, exact agreement) read matmul 13.4 -> 1.1 s and
bucketing 31.5 -> 1.7 s, but the histograms only 63.4 -> 53.3 s: 93% of the
GPU's time. One world a call is ~10 small kernels and the device-to-host syncs
cupy.bincount makes to size its output -- 20,000 times, ~2.6 ms a world, latency
not arithmetic. The batched pass offsets world j's buckets by j x _NB, makes ONE
weighted bincount over --hist-batch worlds, reshapes it to (worlds, _NB), and
counts the mass above each fraction row-wise (`above` is non-increasing, so the
count of buckets with above > q IS searchsorted's left insertion point). On the
CPU it sums the same weights in the same order as the loop.

Inputs: --source cache reads the newest entry of the PC's worlds cache
(pool_cache) and a field from the newest evaluator artifact (dfs_evaluate),
i.e. a real slate; --source synthetic (default when neither exists) draws
players on the 0.02 lattice and a random field of the given size.

Safety: it refuses to start while pc_worker reports a build in progress (the
light's task file), unless --force; it lowers its own priority; numpy's BLAS
threads are capped with --threads (default: half the cores).

    pip install "cupy-cuda12x[ctk]"   # once, for the GPU half: CuPy plus the CUDA headers its
                                      # kernels compile against (plain cupy-cuda12x failed on the
                                      # owner's PC: "Failed to find CUDA headers")
    python research/gpu_bench.py                       # synthetic, 20,000 worlds x 300,000 lineups
    python research/gpu_bench.py --source cache        # the newest real slate on this PC
    python research/gpu_bench.py --worlds 40000 --field 300000 --out bench.json
    python research/gpu_bench.py --hist-batch 200      # more worlds per batched call (more VRAM)
"""
import argparse
import json
import os
import platform
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

_RES = 100.0
_MAXPTS = 600.0
_NB = int(_MAXPTS * _RES) + 1
FRACTIONS = (0.0, 0.0001, 0.001, 0.01, 0.05, 0.1, 0.2, 0.5)


def _cap_threads(n):
    for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[k] = str(n)


def _lower_priority():
    try:
        os.nice(10)
        return "nice 10"
    except (AttributeError, OSError):
        pass
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        return "below normal"
    except Exception:
        return "unchanged (install psutil to lower it on Windows)"


def _build_running():
    """What pc_worker says it is building, if anything (its status file)."""
    d = os.environ.get("VIGIL_SIM_CACHE_DIR") or os.path.join(_HERE, "..", "pc-simcache")
    p = os.path.join(d, "pc_task.json")
    try:
        with open(p) as f:
            t = json.load(f)
        # pc_worker._task_note: {"task", "since"}, removed when the cycle is idle
        if t.get("task") and time.time() - float(t.get("since") or 0) < 3 * 3600:
            return t.get("task")
    except (OSError, ValueError, TypeError):
        pass
    return None


def _peak_ram_mb():
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(r / 1024.0 if platform.system() != "Darwin" else r / 1024.0 / 1024.0, 1)
    except ImportError:
        try:
            import psutil
            return round(psutil.Process().memory_info().peak_wset / 1024.0 / 1024.0, 1)
        except Exception:
            return None


def inputs(args, np):
    """(X players x worlds float32, idx field rows x 9 int, w field weights, label)."""
    if args.source in ("cache", "auto"):
        try:
            import pool_cache
            import dfs_evaluate
            ents = pool_cache.entries()
            root = dfs_evaluate.root()
            arts = []
            if root and os.path.isdir(root):
                for slate in os.listdir(root):
                    for a in os.listdir(os.path.join(root, slate)):
                        p = os.path.join(root, slate, a)
                        if os.path.isfile(os.path.join(p, "field_idx.npy")):
                            arts.append((os.path.getmtime(p), p))
            if ents and arts:
                path = sorted(arts)[-1][1]
                with open(os.path.join(path, "meta.json")) as f:
                    meta = json.load(f)
                idx = np.load(os.path.join(path, "field_idx.npy")).astype(np.int64)
                hit = pool_cache.load(os.path.basename(ents[0][0]))
                pool = hit[0] if hit else {}
                rows = [pool.get(p["name"], {}).get("arr") for p in meta["players"]]
                if all(r for r in rows):
                    W = min(min(len(r) for r in rows), args.worlds)
                    X = np.asarray([r[:W] for r in rows], dtype=np.float32)
                    idx = idx[: args.field]
                    return X, idx, np.full(len(idx), 1.0 / len(idx)), f"cache: {meta['id']} ({len(rows)} players)"
                print("[bench] the cached worlds are not this artifact's slate; using synthetic inputs")
        except Exception as e:
            if args.source == "cache":
                raise
            print(f"[bench] no usable cache ({type(e).__name__}: {e}); using synthetic inputs")
    rng = np.random.default_rng(args.seed)
    P = args.players
    X = (np.rint(rng.gamma(2.0, 4.0, size=(P, args.worlds)) * 50.0) / 50.0).astype(np.float32)   # the 0.02 lattice
    idx = np.stack([rng.choice(P, 9, replace=False) for _ in range(args.field)])
    return X, idx, np.full(args.field, 1.0 / args.field), f"synthetic: {P} players on the 0.02 lattice"


def _hist_loop(xp, B, s, T, wd, qs):
    """Production's form: one world a call."""
    for j in range(B.shape[0]):
        hist = xp.bincount(B[j], weights=wd, minlength=_NB)
        above = xp.cumsum(hist[::-1])[::-1] - hist
        T[s + j] = xp.searchsorted(-above, -qs, side="left")


def _hist_batched(xp, B, s, T, wt, qs_host, hb):
    """`hb` worlds a call: world j's buckets offset by j x _NB into one
    bincount (wt is the field weights tiled hb times), then the mass strictly
    above each bucket row-wise and, per fraction, the count of buckets whose
    mass above exceeds it -- searchsorted's left point on a non-increasing row."""
    c, M = B.shape
    for u in range(0, c, hb):
        n = min(hb, c - u)
        off = (xp.arange(n, dtype=xp.int32) * _NB)[:, None]
        flat = (B[u:u + n] + off).ravel()
        hist = xp.bincount(flat, weights=wt[: n * M], minlength=n * _NB).reshape(n, _NB)
        above = xp.cumsum(hist[:, ::-1], axis=1)[:, ::-1] - hist
        for k, q in enumerate(qs_host):
            T[s + u:s + u + n, k] = xp.count_nonzero(above > q, axis=1)


def run(xp, X, idx, w, chunk, fractions, device, hist="both", hb=50):
    """The kernel on `xp` (numpy or cupy). Returns (timings, buckets of the
    first chunk, loop thresholds, batched thresholds) -- the last three as host
    numpy arrays (None for a pass not run)."""
    import numpy as np
    t = {"h2d_s": 0.0, "matmul_s": 0.0, "bucket_s": 0.0, "d2h_s": 0.0}
    do_loop, do_batch = hist in ("loop", "both"), hist in ("batched", "both")
    if do_loop:
        t["hist_s"] = 0.0
    if do_batch:
        t["hist_batched_s"] = 0.0
    sync = (lambda: xp.cuda.Stream.null.synchronize()) if device == "gpu" else (lambda: None)
    P, N = X.shape
    M = len(idx)
    t0 = time.time()
    Wf = np.zeros((M, P), dtype=np.float32)
    Wf[np.arange(M)[:, None], idx] = 1.0
    qs_host = [float(q) + 1e-12 for q in fractions]
    a = time.time()
    Xd = xp.asarray(X)
    WfT = xp.asarray(np.ascontiguousarray(Wf.T))
    wd = xp.asarray(w)
    qs = xp.asarray(np.asarray(qs_host, dtype=np.float64))
    wt = xp.tile(wd, hb) if do_batch else None
    sync()
    t["h2d_s"] += time.time() - a
    T = xp.zeros((N, len(fractions)), dtype=xp.int32) if do_loop else None
    Tb = xp.zeros((N, len(fractions)), dtype=xp.int64) if do_batch else None
    first_b = None
    for s in range(0, N, chunk):
        a = time.time()
        S = Xd[:, s:s + chunk].T @ WfT                       # (c, M) scores
        sync()
        t["matmul_s"] += time.time() - a
        a = time.time()
        # dfs_tourney._buckets exactly: in place on the score matrix, then the
        # cast (truncation equals floor once clipped at 0). The first version
        # here built temporaries and read the CPU's bucketing ~4x the matmul
        # on the owner's PC; production does not pay that.
        xp.multiply(S, _RES, out=S)
        S += 0.5
        xp.clip(S, 0, _NB - 1, out=S)
        B = S.astype(xp.int32)
        del S
        sync()
        t["bucket_s"] += time.time() - a
        if first_b is None:
            first_b = B
        if do_loop:
            a = time.time()
            _hist_loop(xp, B, s, T, wd, qs)
            sync()
            t["hist_s"] += time.time() - a
        if do_batch:
            a = time.time()
            _hist_batched(xp, B, s, Tb, wt, qs_host, hb)
            sync()
            t["hist_batched_s"] += time.time() - a
    a = time.time()
    host = (lambda v: None if v is None else (v.get() if device == "gpu" else v))
    Th, Tbh, Bh = host(T), host(Tb), host(first_b)
    t["d2h_s"] += time.time() - a
    t["total_s"] = time.time() - t0
    base = t["h2d_s"] + t["matmul_s"] + t["bucket_s"] + t["d2h_s"]
    if do_loop:
        t["total_loop_s"] = base + t["hist_s"]
    if do_batch:
        t["total_batched_s"] = base + t["hist_batched_s"]
    return {k: round(v, 3) for k, v in t.items()}, Bh, Th, Tbh


def _mis(a, b):
    return None if a is None or b is None else int((a != b).sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", choices=("auto", "cache", "synthetic"), default="auto")
    ap.add_argument("--worlds", type=int, default=20000)
    ap.add_argument("--field", type=int, default=300000)
    ap.add_argument("--players", type=int, default=150)
    ap.add_argument("--chunk", type=int, default=250, help="worlds per chunk (VRAM: chunk x field x 8 bytes)")
    ap.add_argument("--hist", choices=("auto", "both", "loop", "batched"), default="auto",
                    help="histogram pass: production's one world a call, the batched one, or both; auto "
                         "(default) runs the loop on the CPU and both on the GPU -- batched on the CPU measured "
                         "~3x SLOWER than the loop here (8 row-wise counts over hist-batch x 60,001 buckets), "
                         "and it sums the same weights in the same order, so it would only add minutes")
    ap.add_argument("--hist-batch", type=int, default=50,
                    help="worlds per batched bincount (memory: hist-batch x field x 12 bytes)")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cpu-only", action="store_true")
    ap.add_argument("--gpu-only", action="store_true")
    ap.add_argument("--force", action="store_true", help="run even while the PC worker is building")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    busy = _build_running()
    if busy and not args.force:
        print(f"[bench] the PC worker is building ({busy}); not competing with it. Re-run when it is idle, or --force.")
        return 2
    _cap_threads(args.threads)
    prio = _lower_priority()
    import numpy as np
    X, idx, w, label = inputs(args, np)
    rep = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "host": platform.platform(), "python": sys.version.split()[0],
           "numpy": np.__version__, "inputs": label, "players": int(X.shape[0]), "worlds": int(X.shape[1]),
           "field": int(len(idx)), "chunk": args.chunk, "hist": args.hist, "hist_batch": args.hist_batch,
           "cpu_threads": args.threads, "priority": prio,
           "fractions": list(FRACTIONS)}
    print(f"[bench] {label}: {X.shape[1]:,} worlds x {len(idx):,} field lineups, chunk {args.chunk}, "
          f"histograms {args.hist} (batch {args.hist_batch}), {args.threads} CPU threads, priority {prio}")
    cpu = gpu = None
    if not args.gpu_only:
        print("[bench] CPU (numpy)...", flush=True)
        tc, Bc, Tc, Tcb = run(np, X, idx, w, args.chunk, FRACTIONS, "cpu", "loop" if args.hist == "auto" else args.hist,
                                args.hist_batch)
        tc["peak_ram_mb"] = _peak_ram_mb()
        rep["cpu"] = tc
        cpu = (Bc, Tc, Tcb)
        print(f"[bench]   {tc}")
    if not args.cpu_only:
        try:
            a = time.time()
            import cupy as cp
            cp.cuda.Device(0).use()
            _ = (cp.arange(10) * 2).sum().get()                    # context + a first kernel
            init = time.time() - a
            dev = cp.cuda.runtime.getDeviceProperties(0)
            rep["gpu_name"] = dev["name"].decode() if isinstance(dev["name"], bytes) else str(dev["name"])
            rep["gpu_vram_mb"] = round(dev["totalGlobalMem"] / 1024 / 1024)
            print(f"[bench] GPU (CuPy) on {rep['gpu_name']}...", flush=True)
            pool = cp.get_default_memory_pool()
            tg, Bg, Tg, Tgb = run(cp, X, idx, w, args.chunk, FRACTIONS, "gpu", "both" if args.hist == "auto" else args.hist,
                                args.hist_batch)
            tg["init_s"] = round(init, 3)
            tg["peak_vram_mb"] = round(pool.total_bytes() / 1024 / 1024, 1)
            tg["peak_ram_mb"] = _peak_ram_mb()
            rep["gpu"] = tg
            gpu = (Bg, Tg, Tgb)
            print(f"[bench]   {tg}")
        except ImportError:
            rep["gpu"] = {"error": 'CuPy is not installed: pip install "cupy-cuda12x[ctk]"'}
            print('[bench] CuPy not installed: the CPU figures stand alone (pip install "cupy-cuda12x[ctk]")')
        except Exception as e:
            rep["gpu"] = {"error": f"{type(e).__name__}: {e}"}
            print(f"[bench] GPU run failed: {type(e).__name__}: {e} (lower --chunk if out of memory)")
    # Agreement. The reference is production's answer: the CPU's loop. Every
    # other (device, pass) is compared to it cell by cell, and the batched pass
    # to the loop on its own device.
    ag = {}
    ref = cpu[1] if cpu and cpu[1] is not None else (cpu[2] if cpu else None)
    for dev, got in (("cpu", cpu), ("gpu", gpu)):
        if not got:
            continue
        ag[f"{dev}_batched_vs_{dev}_loop"] = _mis(got[1], got[2])
        if dev == "gpu" and ref is not None:
            ag["gpu_loop_vs_cpu"] = _mis(ref, got[1])
            ag["gpu_batched_vs_cpu"] = _mis(ref, got[2])
    if cpu and gpu:
        ag["bucket_mismatches_first_chunk"] = _mis(cpu[0], gpu[0])
        ag["bucket_cells_first_chunk"] = int(cpu[0].size)
    if ref is not None:
        ag["threshold_cells"] = int(ref.size)
    ag = {k: v for k, v in ag.items() if v is not None}        # a pass not run compares nothing
    compared = [v for k, v in ag.items() if k.endswith(("_loop", "_cpu")) or k.startswith("bucket_mismatches")]
    if compared:
        ag["exact"] = all(v == 0 for v in compared)
        rep["agreement"] = ag
        print(f"[bench] agreement (mismatched cells; 0 is exact): {ag}")
    sp = {}
    c, g = rep.get("cpu", {}), rep.get("gpu", {})
    if "hist_s" in c and "hist_batched_s" in c:
        sp["cpu_hist_batched_vs_loop"] = round(c["hist_s"] / max(1e-9, c["hist_batched_s"]), 2)
    if "hist_s" in g and "hist_batched_s" in g:
        sp["gpu_hist_batched_vs_loop"] = round(g["hist_s"] / max(1e-9, g["hist_batched_s"]), 2)
    cref = c.get("total_loop_s") or c.get("total_batched_s")
    if cref and "init_s" in g:
        # production today (the CPU loop) against each GPU pass, GPU start-up included
        for k in ("total_loop_s", "total_batched_s"):
            if k in g:
                sp[f"cpu_production_vs_gpu_{k[6:-2]}"] = round(cref / max(1e-9, g[k] + g["init_s"]), 2)
        if "total_batched_s" in c and "total_batched_s" in g:
            sp["cpu_batched_vs_gpu_batched"] = round(c["total_batched_s"] / max(1e-9, g["total_batched_s"] + g["init_s"]), 2)
    if sp:
        rep["speedups"] = sp
        print(f"[bench] speed-ups (x faster): {sp}")
    out = args.out or f"gpu_bench_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w") as f:
        json.dump(rep, f, indent=1)
    print(f"[bench] written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
