"""Guards for the PC's unattended GPU benchmark (2026-11-26; pc_worker._task_gpu_bench, /api/pc/bench,
research/gpu_bench.py). The owner, away for Thanksgiving: test batch 50 vs 250 on real cached data, report
source, runtime, memory and agreement to the server, never interrupt a slate build or miss a deadline, no
arbitrary remote command execution, keep temperatures and memory safe.

    python3 tests/guards_pcbench.py       # exits 1 on any failed check
    from tests.guards_pcbench import run  # run(ck) inside the main suite
"""
import inspect
import json
import os
import sys
import tempfile
import types

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "research"))


def run(ck):
    import app as A
    import boardshare
    saved_dir, saved_auth = boardshare._DIR, A._pc_auth_ok
    tmp = tempfile.mkdtemp()
    try:
        boardshare._DIR = os.path.join(tmp, "boards")
        c = A.app.test_client()
        A._pc_auth_ok = lambda: False
        no = c.post("/api/pc/bench", json={"results": []}).status_code
        A._pc_auth_ok = lambda: True
        bad = c.post("/api/pc/bench", json={"x": 1}).status_code
        big = c.post("/api/pc/bench", json={"results": ["x" * 210_000]}).status_code
        for i in range(23):
            c.post("/api/pc/bench", json={"results": [{"i": i}], "real_data": True})
        got = c.get("/api/pc/bench").get_json()
    finally:
        boardshare._DIR, A._pc_auth_ok = saved_dir, saved_auth
    runs = got.get("runs") or []
    src = inspect.getsource(A.api_pc_bench)
    ck("PB1 the PC's benchmark reports come in only through its own token door (403 without), malformed or "
       "oversized reports are refused (400 / 413), the server keeps the last 20, and reading them is the site's "
       "ordinary authenticated GET; the reply carries no instruction (the PC runs only what its code fixes)",
       no == 403 and bad == 400 and big == 413 and len(runs) == 20 and runs[0]["results"][0]["i"] == 3
       and runs[-1]["results"][0]["i"] == 22 and all("received_ts" in r for r in runs)
       and 'jsonify({"ok": True, "kept": len(runs)})' in src and "pcbench_" in boardshare._KEEP_LONG,
       f"{no} {bad} {big} {len(runs)}")

    import pc_worker as W
    sdir = tempfile.mkdtemp()
    saved_env, saved_api = os.environ.get("VIGIL_SIM_CACHE_DIR"), W._api
    posts, calls = [], []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = cmd[cmd.index("--out") + 1]
        with open(out, "w") as fh:
            json.dump({"inputs": "cache: 156900-1 (500 players)", "agreement": {"exact": True}}, fh)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def fail_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=1, stdout="", stderr="RuntimeError: no real slate to benchmark")
    try:
        os.environ["VIGIL_SIM_CACHE_DIR"] = sdir
        W._api = lambda url, tok, path, data=None, headers=None, timeout=90: posts.append((path, json.loads(data)))
        r_dead = W._task_gpu_bench("u", "t", run=fake_run, now_left=lambda: 3600.0)
        n_dead = (len(calls), len(posts))
        r_none = W._task_gpu_bench("u", "t", run=fail_run, now_left=lambda: None)
        r_wait = W._task_gpu_bench("u", "t", run=fake_run, now_left=lambda: None)      # within the retry wait
        os.remove(os.path.join(sdir, "gpu_bench_state.json"))
        r_ok = W._task_gpu_bench("u", "t", run=fake_run, now_left=lambda: 5 * 3600.0)
        r_again = W._task_gpu_bench("u", "t", run=fake_run, now_left=lambda: None)
        os.remove(os.path.join(sdir, "gpu_bench_state.json"))
        r_blind = W._task_gpu_bench("u", "t", run=fake_run, now_left=lambda: (_ for _ in ()).throw(OSError("lobby down")))
    finally:
        W._api = saved_api
        if saved_env is None:
            os.environ.pop("VIGIL_SIM_CACHE_DIR", None)
        else:
            os.environ["VIGIL_SIM_CACHE_DIR"] = saved_env
    last = posts[-1][1] if posts else {}
    fixed = [W._GPU_BENCH_CONFIGS[0]["worlds"], W._GPU_BENCH_CONFIGS[1]["worlds"]]
    ck("PB2 the task never starts with an NFL slate under three hours away, or when the lobby cannot be read; with "
       "no real slate cached it reports that and waits six hours; on real data it runs every fixed configuration, "
       "posts one report (real_data true) and never runs that bench version again",
       r_dead == "deadline" and n_dead == (0, 0) and r_none == "no real data" and r_wait == "waiting"
       and r_ok == "reported" and r_again == "done" and r_blind == "no lobby"
       and last.get("real_data") is True and [x["config"]["worlds"] for x in last.get("results", [])] == fixed
       and all(x["report"]["agreement"]["exact"] for x in last.get("results", [])),
       f"{r_dead} {n_dead} {r_none} {r_wait} {r_ok} {r_again} {r_blind}")
    cmds_ok = all(c[1].endswith(os.path.join("research", "gpu_bench.py")) and c[2:4] == ["--source", "cache"]
                  and "--gpu-batches" in c for c in calls)
    tsrc = inspect.getsource(W._task_gpu_bench)
    ck("PB3 no remote command execution: every command is research/gpu_bench.py on real cached data with the "
       "configurations fixed in pc_worker (evaluator 40,000 and tournament 160,000 worlds, batches 50 and 250), "
       "nothing read from the server shapes it, each run has a wall-clock cap; the task is the cycle's last",
       cmds_ok and W._GPU_BENCH_CONFIGS[0]["batches"] == "50,250" and W._GPU_BENCH_CONFIGS[1]["batches"] == "50,250"
       and "timeout=_GPU_BENCH_TIMEOUT_S" in tsrc and "_api(url, tok, \"/api/pc/bench\"" in tsrc
       and tsrc.count("_api(") == 1
       and inspect.getsource(W._cycle).rstrip().find('("GPU benchmark", lambda: _task_gpu_bench(url, tok))') > 0
       and inspect.getsource(W._cycle).find('("standings", _task_standings)')
       < inspect.getsource(W._cycle).find('("GPU benchmark"'), str(calls[:1]))

    import gpu_bench as G
    saved_argv, saved_stats = sys.argv, G._gpu_stats
    out = os.path.join(sdir, "hot.json")
    try:
        empty = tempfile.mkdtemp()
        os.environ["VIGIL_POOL_CACHE_DIR"], prev_pool = empty, os.environ.get("VIGIL_POOL_CACHE_DIR")
        sys.argv = ["gpu_bench.py", "--source", "cache", "--worlds", "100", "--field", "100", "--out", out]
        try:
            G.main()
            strict = False
        except RuntimeError as e:
            strict = "no real slate" in str(e)
        G._gpu_stats = lambda: {"temp_c": 90.0}
        sys.argv = ["gpu_bench.py", "--source", "synthetic", "--worlds", "60", "--field", "200", "--out", out]
        G.main()
        with open(out) as fh:
            hot = json.load(fh)
    finally:
        sys.argv, G._gpu_stats = saved_argv, saved_stats
        if prev_pool is None:
            os.environ.pop("VIGIL_POOL_CACHE_DIR", None)
        else:
            os.environ["VIGIL_POOL_CACHE_DIR"] = prev_pool
    wf = open(os.path.join(_ROOT, ".github", "workflows", "error-log.yml")).read()
    ck("PB4 the bench under --source cache refuses synthetic inputs when no real slate is cached (a report always "
       "says what it measured), skips the GPU above its temperature limit and says so, and the workflow copies "
       "the reports to the data branch (errors/pc-bench-latest.json)",
       strict and "skipped: GPU at 90.0 C" in str((hot.get("gpu") or {}).get("error"))
       and '"pc-bench:/api/pc/bench"' in wf, f"{strict} {hot.get('gpu')}")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
