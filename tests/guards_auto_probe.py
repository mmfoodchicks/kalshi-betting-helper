"""Guards for the unattended deep-profile probe (2026-11-27; research/profile_probe.py,
pc_worker._task_profile_probe). The owner: "Run the unattended Deep test build on my PC, provided it does not
interfere with scheduled production builds or slate deadlines", reporting "actual peak RAM, VRAM, disk usage,
temperatures, runtime, checkpoint recovery and numerical agreement".

    python3 tests/guards_auto_probe.py      # exits 1 on any failed check
"""
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "research"))


def run(ck):
    import pc_worker as W
    sdir = tempfile.mkdtemp()
    saved = (os.environ.get("VIGIL_SIM_CACHE_DIR"), W._api, W._ram_gb, W._probe_budget_s)
    posts, calls = [], []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        out = cmd[cmd.index("--out") + 1]
        with open(out, "w") as fh:
            json.dump({"agreement": {"equal": True}, "runs": [{"tag": "A interrupted"}]}, fh)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    slate = {"draft_group_id": 4242, "starts": "2026-11-29T18:00:00Z"}
    # a Friday 02:00 MT clock: far from Saturday 6 pm MT
    fri = 1795770000.0
    res = {}
    try:
        os.environ["VIGIL_SIM_CACHE_DIR"] = sdir
        W._api = lambda url, tok, path, data=None, headers=None, timeout=90: posts.append((path, json.loads(data)))
        W._ram_gb = lambda: 32.0
        W._probe_budget_s = lambda board: 3 * 3600.0
        res["close"] = W._task_profile_probe("u", "t", run=fake_run, now_left=lambda: 5 * 3600.0, slate=slate,
                                             free_gb=200, now=fri)
        n_close = (len(calls), len(posts))
        # Saturday 4 pm MT: a three-hour probe would run into the 6 pm window (kickoff ~19 h away)
        res["window"] = W._task_profile_probe("u", "t", run=fake_run, now_left=lambda: 19 * 3600.0, slate=slate,
                                              free_gb=200, now=fri + 38 * 3600)
        res["disk"] = W._task_profile_probe("u", "t", run=fake_run, now_left=lambda: 30 * 3600.0, slate=slate,
                                            free_gb=5, now=fri)
        res["wait"] = W._task_profile_probe("u", "t", run=fake_run, now_left=lambda: 30 * 3600.0, slate=slate,
                                            free_gb=200, now=fri + 60)
        os.remove(os.path.join(sdir, "profile_probe_state.json"))
        W._ram_gb = lambda: 8.0
        res["ram"] = W._task_profile_probe("u", "t", run=fake_run, now_left=lambda: 30 * 3600.0, slate=slate,
                                           free_gb=200, now=fri)
        os.remove(os.path.join(sdir, "profile_probe_state.json"))
        W._ram_gb = lambda: 32.0
        res["ok"] = W._task_profile_probe("u", "t", run=fake_run, now_left=lambda: 30 * 3600.0, slate=slate,
                                          free_gb=200, now=fri)
        res["again"] = W._task_profile_probe("u", "t", run=fake_run, now_left=lambda: 30 * 3600.0, slate=slate,
                                             free_gb=200, now=fri + 7 * 3600)
        res["blind"] = W._task_profile_probe("u", "t", run=fake_run, now_left=lambda: (_ for _ in ()).throw(OSError("x")),
                                             slate=slate, free_gb=200, now=fri)
    finally:
        os.environ.pop("VIGIL_SIM_CACHE_DIR", None) if saved[0] is None else os.environ.__setitem__("VIGIL_SIM_CACHE_DIR", saved[0])
        W._api, W._ram_gb, W._probe_budget_s = saved[1:]
    probe_posts = [p for p in posts if p[1].get("kind") == "profile_probe"]
    last = probe_posts[-1][1] if probe_posts else {}
    ok_calls = [c for c in calls]
    ck("PR1 the deep probe never starts unless its whole budget ends three hours before any NFL slate and outside "
       "the Saturday-night window; it is refused (and says so) on a disk under 40 GB free or a PC whose memory "
       "does not fit the deep estimate, then waits six hours; it runs once, reports once (profile_probe, real "
       "data), and an unreadable lobby runs nothing",
       res["close"] == "deadline" and n_close == (0, 0) and res["window"] == "window" and res["disk"] == "refused"
       and res["wait"] == "waiting" and res["ram"] == "refused" and res["ok"] == "reported" and res["again"] == "done"
       and res["blind"] == "done" and len(ok_calls) == 1 and last.get("real_data") is True
       and last["results"][0]["report"]["agreement"]["equal"] is True, str(res))
    cmd, kw = ok_calls[0] if ok_calls else ([], {})
    src = inspect.getsource(W._task_profile_probe)
    cyc = inspect.getsource(W._cycle)
    ck("PR2 the probe is one fixed command (research/profile_probe.py --dg <the main slate> --out), capped at its "
       "own budget, its whole process tree stopped at the cap; nothing the server says shapes it; it runs after "
       "the cycle's syncs, so a long probe never ages the night's deep payloads into a server build",
       cmd[1:2] == [os.path.join(W._HERE, "research", "profile_probe.py")] and cmd[2:4] == ["--dg", "4242"]
       and kw.get("timeout") == min(W._PROBE_TIMEOUT_S, 3 * 3600.0) and "(run or _run_tree)" in src
       and src.count("_api(") == 1 and cyc.index("_sync_kind(") < cyc.index("_task_profile_probe(url, tok)"),
       str(cmd))
    # the tree kill: a child that starts a sleeping grandchild; the cap must take both
    flag = os.path.join(sdir, "grandchild.pid")
    gpath, cpath = os.path.join(sdir, "grand.py"), os.path.join(sdir, "child.py")
    with open(gpath, "w") as fh:
        fh.write(f"import os, time\nopen({flag!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
    with open(cpath, "w") as fh:
        fh.write(f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, {gpath!r}])\ntime.sleep(60)\n")
    t0 = time.time()
    try:
        W._run_tree([sys.executable, cpath], timeout=2)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
    gc_alive = None
    for _ in range(50):
        if os.path.exists(flag):
            break
        time.sleep(0.05)
    try:
        gpid = int(open(flag).read())
        time.sleep(0.3)
        try:
            os.kill(gpid, 0)
            # a killed process nobody has reaped yet (a container's init may never reap) still answers kill 0:
            # it is gone if /proc says zombie
            try:
                with open(f"/proc/{gpid}/stat") as fh:
                    gc_alive = fh.read().rsplit(")", 1)[1].split()[0] != "Z"
            except OSError:
                gc_alive = False
        except OSError:
            gc_alive = False
    except (OSError, ValueError):
        gc_alive = None
    ck("PR3 at its cap the probe's runner stops the WHOLE process tree (a build child's pool workers too), "
       "where subprocess.run would leave the grandchildren running",
       timed_out and gc_alive is False and time.time() - t0 < 30, f"timed_out={timed_out} grandchild_alive={gc_alive}")
    import profile_probe as PP
    psrc = inspect.getsource(PP)
    m = PP._PROGRESS.search("[tourney]   200,000/500,000 worlds, 812s")
    ck("PR4 the probe is isolated and compares the right things: its worlds cache, checkpoints and scratch are its "
       "own directories, its boards never go to boardshare and are deleted with them, the kill reads the scoring "
       "pass's progress line (never the best-lineup solve's), and the resumed and fresh boards are compared on "
       "everything but the clocks, stamps and the measured memory",
       "import boardshare" not in psrc and "boardshare." not in psrc and "VIGIL_POOL_CACHE_DIR=dirs[\"worlds_cache\"]" in psrc
       and "VIGIL_CKPT_DIR=dirs[\"checkpoints\"]" in psrc and "shutil.rmtree(d, ignore_errors=True)" in psrc
       and m and m.group(1) == "200,000" and PP._PROGRESS.search("[tourney]   1,000/20,000 worlds solved, 9s") is None
       and set(PP._VOLATILE) == {"built_ts", "timings", "timing", "checkpoint", "eval_artifact", "memory",
                                 "requested_by", "profile"}
       and PP._diff({"a": [1, 2], "b": 1}, {"a": [1, 3], "b": 1}) == [".a[1]"]
       and PP.PROFILE == "deep" and PP.KILL_FRAC == 0.4)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
