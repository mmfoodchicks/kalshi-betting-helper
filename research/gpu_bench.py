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
  hist_s        the per-world histograms and their cumulative mass
  total_s       end to end, transfers included
  peak_ram_mb   this process's peak resident memory
  peak_vram_mb  CuPy's memory pool high-water mark (GPU only)

and AGREEMENT: the bucket of every (world, lineup) and the threshold bucket of
every (world, fraction) compared exactly between the two devices. On
DraftKings' 0.02 lattice they should be identical (the conservation audit's
D1-D3: float32 summation error ~5.6e-5 against a 0.005 bucket margin); any
mismatch is counted and printed, never hidden.

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


def run(xp, X, idx, w, chunk, fractions, device):
    """The kernel on `xp` (numpy or cupy). Returns (timings, buckets of the
    first chunk, thresholds) -- the last two as host numpy arrays."""
    import numpy as np
    t = {"h2d_s": 0.0, "matmul_s": 0.0, "bucket_s": 0.0, "hist_s": 0.0, "d2h_s": 0.0}
    sync = (lambda: xp.cuda.Stream.null.synchronize()) if device == "gpu" else (lambda: None)
    P, N = X.shape
    M = len(idx)
    t0 = time.time()
    Wf = np.zeros((M, P), dtype=np.float32)
    Wf[np.arange(M)[:, None], idx] = 1.0
    a = time.time()
    Xd = xp.asarray(X)
    WfT = xp.asarray(np.ascontiguousarray(Wf.T))
    wd = xp.asarray(w)
    qs = xp.asarray(np.asarray(fractions, dtype=np.float64) + 1e-12)
    sync()
    t["h2d_s"] += time.time() - a
    T = xp.zeros((N, len(fractions)), dtype=xp.int32)
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
        sync()
        t["bucket_s"] += time.time() - a
        if first_b is None:
            first_b = B
        a = time.time()
        for j in range(B.shape[0]):
            hist = xp.bincount(B[j], weights=wd, minlength=_NB)
            above = xp.cumsum(hist[::-1])[::-1] - hist
            T[s + j] = xp.searchsorted(-above, -qs, side="left")
        sync()
        t["hist_s"] += time.time() - a
    a = time.time()
    Th = T.get() if device == "gpu" else T
    Bh = first_b.get() if device == "gpu" else first_b
    t["d2h_s"] += time.time() - a
    t["total_s"] = time.time() - t0
    return {k: round(v, 3) for k, v in t.items()}, Bh, Th


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", choices=("auto", "cache", "synthetic"), default="auto")
    ap.add_argument("--worlds", type=int, default=20000)
    ap.add_argument("--field", type=int, default=300000)
    ap.add_argument("--players", type=int, default=150)
    ap.add_argument("--chunk", type=int, default=250, help="worlds per chunk (VRAM: chunk x field x 8 bytes)")
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
           "field": int(len(idx)), "chunk": args.chunk, "cpu_threads": args.threads, "priority": prio,
           "fractions": list(FRACTIONS)}
    print(f"[bench] {label}: {X.shape[1]:,} worlds x {len(idx):,} field lineups, chunk {args.chunk}, "
          f"{args.threads} CPU threads, priority {prio}")
    cpu = gpu = None
    if not args.gpu_only:
        print("[bench] CPU (numpy)...", flush=True)
        tc, Bc, Tc = run(np, X, idx, w, args.chunk, FRACTIONS, "cpu")
        tc["peak_ram_mb"] = _peak_ram_mb()
        rep["cpu"] = tc
        cpu = (Bc, Tc)
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
            tg, Bg, Tg = run(cp, X, idx, w, args.chunk, FRACTIONS, "gpu")
            tg["init_s"] = round(init, 3)
            tg["peak_vram_mb"] = round(pool.total_bytes() / 1024 / 1024, 1)
            tg["peak_ram_mb"] = _peak_ram_mb()
            rep["gpu"] = tg
            gpu = (Bg, Tg)
            print(f"[bench]   {tg}")
        except ImportError:
            rep["gpu"] = {"error": 'CuPy is not installed: pip install "cupy-cuda12x[ctk]"'}
            print('[bench] CuPy not installed: the CPU figures stand alone (pip install "cupy-cuda12x[ctk]")')
        except Exception as e:
            rep["gpu"] = {"error": f"{type(e).__name__}: {e}"}
            print(f"[bench] GPU run failed: {type(e).__name__}: {e} (lower --chunk if out of memory)")
    if cpu and gpu:
        b_mis = int((cpu[0] != gpu[0]).sum())
        t_mis = int((cpu[1] != gpu[1]).sum())
        rep["agreement"] = {"bucket_mismatches_first_chunk": b_mis, "bucket_cells_first_chunk": int(cpu[0].size),
                            "threshold_mismatches": t_mis, "threshold_cells": int(cpu[1].size),
                            "exact": b_mis == 0 and t_mis == 0}
        if "total_s" in rep.get("gpu", {}) and "total_s" in rep.get("cpu", {}):
            rep["speedup_total"] = round(rep["cpu"]["total_s"] / max(1e-9, rep["gpu"]["total_s"] + rep["gpu"]["init_s"]), 2)
        print(f"[bench] agreement: {rep['agreement']}; speed-up incl. init {rep.get('speedup_total')}x")
    out = args.out or f"gpu_bench_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w") as f:
        json.dump(rep, f, indent=1)
    print(f"[bench] written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
