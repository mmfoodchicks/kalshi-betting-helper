"""Guards for the tournament scoring pass on the GPU (2026-11-27; dfs_tourney._score_range_dev). The owner:
validate the GPU on the evaluator first (done: pc-bench 2026-11-27, real cached slate, every bucket and
threshold equal, 41x), then the tournament scoring pass -- "verified per chunk", with the CPU fallback kept.

The device path is written against an array module, so numpy stands in for CuPy here: it must give the CPU
pass's sums EXACTLY (the products, buckets and field counts on the device, the small per-world steps in the
CPU's own arithmetic). The real GPU is checked on the PC: every pass's first chunk against the CPU, cell by cell.

    python3 tests/guards_auto_gpuscore.py      # exits 1 on any failed check
"""
import inspect
import os
import re
import sys

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _same(np, a, b):
    return all(np.array_equal(x[k], y[k]) for x, y in zip(a[0], b[0]) for k in x) and a[2] == b[2]


def _classic_case(np, seed=11, P=60, K=400, M=6000, N=700):
    """Nine-player lineups, DraftKings' 0.02 lattice, a uniform field, three contests."""
    import dfs_tourney as T
    rng = np.random.default_rng(seed)

    def lineups(n):
        W = np.zeros((n, P), dtype=np.float32)
        for i in range(n):
            W[i, rng.choice(P, 9, replace=False)] = 1.0
        return W
    X = (np.round(rng.gamma(2.0, 5.0, (P, N)) * 50) / 50).astype(np.float32)
    pay = [{"from": 1, "to": 1, "prize": 100000.0}, {"from": 2, "to": 50, "prize": 500.0},
           {"from": 51, "to": 4000, "prize": 40.0}]
    grids = [T.payout_grid(20000, pay, 20.0, 4000), T.payout_grid(5000, pay[:2], 5.0, 50),
             T.payout_grid(900, pay[:1], 3.0, 1)]
    return T, lineups(K), lineups(M), np.full(M, 1.0 / M), X, grids


def run(ck):
    try:
        import numpy as np
    except ImportError:
        ck("GS numpy present", True, "skipped: no numpy")
        return
    import errlog
    import guards_auto_resume_profiles as RP
    T, Wc, Wf, wf, X, grids = RP._lattice_case(np)
    ok_small = all(_same(np, T.run_vs_field(Wc, Wf, wf, X, grids, chunk=ch, workers=w, gpu=False),
                         T.run_vs_field(Wc, Wf, wf, X, grids, chunk=ch, workers=w, gpu=np))
                   for ch, w in ((16, 1), (7, 3), (500, 1)))
    dev_small = dict(T.LAST_SCORE_DEVICE)
    T, Wc, Wf, wf, X, grids = _classic_case(np)
    cpu4 = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=64, workers=4, gpu=False)
    dev = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=64, workers=4, gpu=np)
    opt_eq = np.array_equal(cpu4[1], dev[1])
    ck("GS1 the scoring pass on the device (numpy standing in for CuPy) gives the CPU pass's sums exactly -- "
       "every contest's win, first-sole, top 1% / 0.1%, cash, money, EV with and without ties, for every "
       "candidate -- on DraftKings' 0.02 lattice, uneven chunks, several ranges, three contests, against the "
       "CPU's own process pool; the device stamp says what ran",
       ok_small and _same(np, cpu4, dev) and opt_eq and dev_small.get("device") == "device stand-in"
       and T.LAST_SCORE_DEVICE.get("device") == "device stand-in", str(T.LAST_SCORE_DEVICE))

    # the uniform-weight identity the device path rests on: n copies of 1/M added in order
    M = len(wf)
    rng = np.random.default_rng(3)
    b = rng.integers(0, T._NB, size=M)
    S = T._uniform_sums(M)
    hist_cpu = np.bincount(b, weights=wf, minlength=T._NB)
    hist_dev = S[np.bincount(b, minlength=T._NB)]
    ck("GS2 a uniform field's histogram is rebuilt from integer counts to the CPU's very doubles (the CPU's "
       "bincount adds 1/M once per lineup, in order; _uniform_sums is that sum for every count), and the tables "
       "read off it are the CPU's",
       hist_cpu.tobytes() == hist_dev.tobytes()
       and all(np.array_equal(x, y) for x, y in zip(
           T._world_tables(b, wf, grids[0]["F"], np.log(np.maximum(grids[0]["E"], 1e-300)), grids[0]["E"].shape[0]),
           T._tables_from_hist(hist_dev, grids[0]["F"], np.log(np.maximum(grids[0]["E"], 1e-300)),
                               grids[0]["E"].shape[0]))))

    # a weighted field stays on the CPU, and says so
    cpu1 = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=64, gpu=False)     # the CPU's one-process order
    wf_w = rng.dirichlet(np.ones(len(wf)))
    T.run_vs_field(Wc, Wf, wf_w, X, grids, chunk=64, gpu=np)
    weighted = dict(T.LAST_SCORE_DEVICE)
    # a disagreement on the first chunk: the whole pass goes back to the CPU, ledgered
    seen = []
    real_note, real_b = errlog.note, T._xp_buckets

    def bad_buckets(xp, St):
        B = real_b(xp, St)
        B[0, 0] += 1
        return B
    try:
        errlog.note = lambda key, *a, **k: seen.append(key)
        T._xp_buckets = bad_buckets
        fell = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=64, gpu=np)
        mism = dict(T.LAST_SCORE_DEVICE)

        class Broken:
            def __getattr__(self, name):
                raise RuntimeError("no device")
        T._xp_buckets = real_b
        err = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=64, gpu=Broken())
        errd = dict(T.LAST_SCORE_DEVICE)
    finally:
        errlog.note, T._xp_buckets = real_note, real_b
    ck("GS3 the CPU stays the reference: a weighted field (the showdown universe) never takes the device path, a "
       "first chunk that disagrees in one bucket sends the whole pass back to the CPU (TOURN-gpu-mismatch), a "
       "device that errors does too (TOURN-gpu), and either way the answer is the CPU pass's",
       weighted.get("device") == "cpu" and "weighted" in weighted.get("why", "")
       and mism.get("device") == "cpu" and "check failed" in mism.get("why", "") and _same(np, fell, cpu1)
       and errd.get("device") == "cpu" and "error" in errd.get("why", "") and _same(np, err, cpu1)
       and "TOURN-gpu-mismatch" in seen and "TOURN-gpu" in seen, f"{weighted} {mism} {errd} {seen}")

    saved = os.environ.get("VIGIL_TOURNEY_GPU")
    try:
        os.environ.pop("VIGIL_TOURNEY_GPU", None)
        off = T._gpu_xp()
        os.environ["VIGIL_TOURNEY_GPU"] = "1"
        import importlib.util
        no_cupy = importlib.util.find_spec("cupy") is None
        on_without = T._gpu_xp() if no_cupy else None
    finally:
        if saved is None:
            os.environ.pop("VIGIL_TOURNEY_GPU", None)
        else:
            os.environ["VIGIL_TOURNEY_GPU"] = saved
    src = inspect.getsource(T.build_nfl_classic)
    ck("GS4 off unless VIGIL_TOURNEY_GPU=1 (nothing sets it until the real-slate bench agrees), CuPy absent is "
       "the CPU, the classic board stamps what scored it (timings.score_device), and the classic board never "
       "reads the best-of-candidates tally whose exact ties the GPU may break differently",
       off is None and on_without is None and '"score_device": score_dev' in src
       and "res, opt_c, _n = run_vs_field(" in src and len(re.findall(r"\bopt_c\b", src)) == 1)

    _worker_checks(ck)


def _worker_checks(ck):
    """The PC turns the GPU on for scoring only after its own real-slate comparison of THIS scoring code."""
    import importlib.util
    import json
    import subprocess
    import tempfile
    import pc_worker as W
    sdir = tempfile.mkdtemp()
    saved = (os.environ.get("VIGIL_SIM_CACHE_DIR"), W._api, W._cfg_value, importlib.util.find_spec)
    posts, calls = [], []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = cmd[cmd.index("--out") + 1]
        if "--kernel" in cmd and not classic[0]:
            return subprocess.CompletedProcess(cmd, 1, "", "RuntimeError: no real slate to benchmark: no classic slate")
        rep = {"inputs": "cache: 1 (500 players)", "agreement": {"exact": True}}
        with open(out, "w") as fh:
            json.dump(rep, fh)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    classic = [False]
    env = {}
    try:
        os.environ["VIGIL_SIM_CACHE_DIR"] = sdir
        W._api = lambda url, tok, path, data=None, headers=None, timeout=90: posts.append(json.loads(data))
        first = W._task_gpu_bench("u", "t", run=fake_run, now_left=lambda: None)
        n1 = len(calls)
        st1 = json.load(open(os.path.join(sdir, "gpu_bench_state.json")))
        st1["tried_ts"] = 0                                          # past the six-hour wait
        json.dump(st1, open(os.path.join(sdir, "gpu_bench_state.json"), "w"))
        env_before = {}
        W._cfg_value = lambda key: None
        importlib.util.find_spec = lambda name, *a: object() if name == "cupy" else saved[3](name, *a)
        env_before = dict(VIGIL_SIM_CACHE_DIR=sdir)
        W._apply_limits(env_before)
        classic[0] = True
        second = W._task_gpu_bench("u", "t", run=fake_run, now_left=lambda: None)
        n2 = len(calls) - n1
        st2 = json.load(open(os.path.join(sdir, "gpu_bench_state.json")))
        sig2 = st2["score_exact"]["sig"]
        env = dict(VIGIL_SIM_CACHE_DIR=sdir)
        W._apply_limits(env)
        W._cfg_value = lambda key: False if key == "gpu" else None
        env_off = dict(VIGIL_SIM_CACHE_DIR=sdir)
        W._apply_limits(env_off)
        W._cfg_value = lambda key: None
        st2["score_exact"]["sig"] = "someoldcode"
        json.dump(st2, open(os.path.join(sdir, "gpu_bench_state.json"), "w"))
        env_stale = dict(VIGIL_SIM_CACHE_DIR=sdir)
        W._apply_limits(env_stale)
    finally:
        if saved[0] is None:
            os.environ.pop("VIGIL_SIM_CACHE_DIR", None)
        else:
            os.environ["VIGIL_SIM_CACHE_DIR"] = saved[0]
        W._api, W._cfg_value, importlib.util.find_spec = saved[1], saved[2], saved[3]
    ck("GS5 the PC's bench runs the scoring-pass comparison on a CLASSIC slate only: with none cached it waits "
       "while the evaluator and tournament kernels are reported and kept done; with one it runs that comparison "
       "alone; the worker then scores on the GPU only while the scoring code is the code compared (its hash), "
       "CuPy is installed and vigil-pc.cfg does not say \"gpu\": false",
       first == "no real data" and n1 == 3 and sorted(st1["done_configs"][next(iter(st1["done_configs"]))])
       == ["evaluator", "tournament"] and "VIGIL_TOURNEY_GPU" not in env_before
       and second == "reported" and n2 == 1 and st2.get("done") and sig2 == W._score_sig()
       and env.get("VIGIL_TOURNEY_GPU") == "1" and "VIGIL_TOURNEY_GPU" not in env_off
       and "VIGIL_TOURNEY_GPU" not in env_stale, f"{first} {n1} {second} {n2} {env} {st1.get('done_configs')}")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
