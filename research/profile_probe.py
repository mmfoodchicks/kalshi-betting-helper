"""The deep profile on the owner's PC, measured: memory, VRAM, disk, temperatures, runtime, and a real
interruption and resume (2026-11-27).

The owner approved one unattended deep build of the week's main slate "provided it does not interfere with
scheduled production builds or slate deadlines" (pc_worker._task_profile_probe decides when), and asked for
"actual peak RAM, VRAM, disk usage, temperatures, runtime, checkpoint recovery and numerical agreement". The
profiles' memory figures so far are a model fitted on a synthetic slate here (research/hp_memory.py), and the
checkpoints (build_ckpt) have never run on Windows' process pools. Three builds, each its own process:

  A  the deep profile's classic build, KILLED (the whole process tree) once its scoring pass reports
     KILL_FRAC of the ranking worlds -- a real interruption, mid-pass, with the pool's worker processes;
  B  the same command again: it resumes from A's checkpoint (the generator's entropy, the filed worlds, the
     finished best-lineup solves, the scoring ranges) and runs to the end;
  C  a FRESH build on the same worlds and the same generator entropy (A's rng.json copied into an empty
     checkpoint directory, nothing else): the uninterrupted build.

B must equal C exactly (every figure on the board but the clocks and the checkpoint stamp). While each runs a
sampler keeps, every two seconds: the machine's memory in use (memprobe), the GPU's temperature, memory in
use, load and power (nvidia-smi; this build is CPU-only, so VRAM is a baseline), and the bytes under the
probe's own directories (its worlds cache, its checkpoints, the make-it file if the matrix went to disk).
CPU temperature is not readable without administrator rights on Windows, and the report says so.

Isolation: the probe's worlds cache, checkpoints and scratch are its own directories (never production's
entries or a production build's checkpoint), its boards are written to its own directory and NEVER to
boardshare, and they are deleted at the end with everything else but the report. Nothing a server sends
shapes what runs: the profile and the fraction are fixed here.

    python research/profile_probe.py --dg <draft group> --out report.json      (the PC worker runs it)
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)

VERSION = 1
PROFILE = "deep"
KILL_FRAC = 0.4
SAMPLE_S = 2.0
_PROGRESS = re.compile(r"\[tourney\]\s+([\d,]+)/([\d,]+) worlds, \d+s\s*$")
_VOLATILE = ("built_ts", "timings", "timing", "checkpoint", "eval_artifact", "memory", "requested_by", "profile")


def _gpu():
    """(temperature C, memory used MB, load %, power W) or Nones."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,memory.used,utilization.gpu,power.draw",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        vals = [v.strip() for v in (r.stdout or "").splitlines()[0].split(",")]
        return tuple(float(v) if v not in ("", "[N/A]") else None for v in vals[:4])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return (None, None, None, None)


def _bytes(path):
    tot = 0
    for root, _d, files in os.walk(path):
        for fn in files:
            try:
                tot += os.path.getsize(os.path.join(root, fn))
            except OSError:
                continue
    return tot


class _Sampler:
    def __init__(self, dirs):
        import memprobe
        self.mp, self.dirs = memprobe, dirs
        self.base_used, self.total = memprobe.system_used()
        self.peak = {"system_used": self.base_used, "gpu_temp_c": None, "vram_mb": None, "gpu_load_pct": None,
                     "gpu_power_w": None, **{f"disk_{k}": 0 for k in dirs}}
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    def _max(self, k, v):
        if v is not None:
            self.peak[k] = v if self.peak.get(k) is None else max(self.peak[k], v)

    def _run(self):
        n = 0
        while not self._stop.wait(SAMPLE_S):
            used, _t = self.mp.system_used()
            self._max("system_used", used)
            t, m, u, w = _gpu()
            self._max("gpu_temp_c", t)
            self._max("vram_mb", m)
            self._max("gpu_load_pct", u)
            self._max("gpu_power_w", w)
            n += 1
            if n % 3 == 0:                     # the directories every ~6 s
                for k, d in self.dirs.items():
                    self._max(f"disk_{k}", _bytes(d))

    def stop(self):
        self._stop.set()
        self._th.join(timeout=10)
        gb = 1024.0 ** 3
        p = self.peak
        out = {"system_peak_used_gb": round(p["system_used"] / gb, 2) if p["system_used"] else None,
               "system_start_used_gb": round(self.base_used / gb, 2) if self.base_used else None,
               "system_total_gb": round(self.total / gb, 2) if self.total else None,
               "build_added_gb": (round((p["system_used"] - self.base_used) / gb, 2)
                                  if p["system_used"] and self.base_used else None),
               "gpu_peak_temp_c": p["gpu_temp_c"], "vram_peak_mb": p["vram_mb"], "gpu_peak_load_pct": p["gpu_load_pct"],
               "gpu_peak_power_w": p["gpu_power_w"], "cpu_temp": "not readable without administrator rights",
               "disk_peak_gb": {k[5:]: round(v / gb, 3) for k, v in p.items() if k.startswith("disk_")}}
        return out


def _kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=60)
    else:
        import signal
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()
    proc.wait(timeout=120)


def _run(tag, dg, env, dirs, board_out, kill_frac=None, timeout_s=4 * 3600):
    cmd = [sys.executable, os.path.abspath(__file__), "--child", "--dg", str(dg), "--board-out", board_out]
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    s = _Sampler(dirs)
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=_HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            bufsize=1, **kw)
    killed_at, tail, frac = None, [], 0.0
    for line in proc.stdout:
        tail = (tail + [line.rstrip()])[-40:]
        m = _PROGRESS.search(line)
        if m:
            done, n = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
            frac = done / max(1, n)
            if kill_frac is not None and frac >= kill_frac and killed_at is None:
                killed_at = round(time.time() - t0, 1)
                _kill_tree(proc)
                break
        if time.time() - t0 > timeout_s:
            _kill_tree(proc)
            tail.append(f"[probe] timed out after {timeout_s} s")
            break
    try:
        rc = proc.wait(timeout=600)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        rc = proc.returncode
    stats = s.stop()
    return dict(tag=tag, seconds=round(time.time() - t0, 1), returncode=rc, killed_at_s=killed_at,
                scoring_frac_seen=round(frac, 3), log_tail=tail[-12:], **stats)


def _strip(board):
    return {k: v for k, v in (board or {}).items() if k not in _VOLATILE}


def _diff(a, b, path="", out=None, cap=12):
    out = [] if out is None else out
    if len(out) >= cap:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            _diff(a.get(k), b.get(k), f"{path}.{k}", out, cap)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out, cap)
    elif a != b:
        out.append(path or "(root)")
    return out


def probe(dg, root, out_path):
    os.makedirs(root, exist_ok=True)
    dirs = {"worlds_cache": os.path.join(root, "pool"), "checkpoints": os.path.join(root, "ckpt"),
            "scratch": os.path.join(root, "scratch")}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    env = dict(os.environ, VIGIL_POOL_CACHE_DIR=dirs["worlds_cache"], VIGIL_CKPT_DIR=dirs["checkpoints"],
               VIGIL_SCRATCH_DIR=dirs["scratch"], PYTHONUNBUFFERED="1")
    import pc_profiles
    prof = dict(pc_profiles.PROFILES[PROFILE], name=PROFILE)
    rep = {"version": VERSION, "profile": PROFILE, "counts": pc_profiles.build_kwargs(prof), "dg": int(dg),
           "kill_frac": KILL_FRAC, "started_ts": time.time(), "estimate_gb": None, "runs": []}
    shutil.rmtree(dirs["checkpoints"], ignore_errors=True)
    os.makedirs(dirs["checkpoints"], exist_ok=True)
    bA, bB, bC = (os.path.join(root, f"board_{t}.json") for t in "ABC")
    rep["runs"].append(_run("A interrupted", dg, env, dirs, bA, kill_frac=KILL_FRAC))
    builds = os.path.join(dirs["checkpoints"], "builds")
    keys = [k for k in os.listdir(builds)] if os.path.isdir(builds) else []
    rep["checkpoint_after_kill"] = {"builds": len(keys), "bytes": _bytes(dirs["checkpoints"])}
    rep["runs"].append(_run("B resumed", dg, env, dirs, bB))
    # C: the uninterrupted build -- the same worlds (the probe's worlds cache) and A's generator entropy, an
    # otherwise empty checkpoint directory
    ent = None
    for k in keys:
        p = os.path.join(builds, k, "rng.json")
        if os.path.exists(p):
            ent = (k, open(p, "rb").read())
    c_ckpt = os.path.join(root, "ckpt_fresh")
    shutil.rmtree(c_ckpt, ignore_errors=True)
    if ent:
        os.makedirs(os.path.join(c_ckpt, "builds", ent[0]), exist_ok=True)
        with open(os.path.join(c_ckpt, "builds", ent[0], "rng.json"), "wb") as fh:
            fh.write(ent[1])
    envC = dict(env, VIGIL_CKPT_DIR=c_ckpt)
    rep["runs"].append(_run("C fresh", dg, envC, dict(dirs, checkpoints=c_ckpt), bC))
    boards = {}
    for t, p in (("B", bB), ("C", bC)):
        try:
            with open(p) as fh:
                boards[t] = json.load(fh)
        except (OSError, ValueError):
            boards[t] = None
    B, C = boards["B"], boards["C"]
    rep["resumed"] = (B or {}).get("checkpoint")
    rep["board_memory"] = {t: (boards[t] or {}).get("memory") for t in boards}
    rep["timings"] = {t: (boards[t] or {}).get("timings") for t in boards}
    rep["agreement"] = {"entropy_copied": bool(ent), "both_built": bool(B) and bool(C),
                        "equal": bool(B) and bool(C) and _strip(B) == _strip(C),
                        "first_differences": _diff(_strip(B), _strip(C)) if (B and C) else None}
    try:
        pl = len((B or {}).get("players") or ()) or 500
        rep["estimate_gb"] = round(pc_profiles.memory_gb(prof, pl, len((B or {}).get("contests") or ()) or 3,
                                                         int((B or {}).get("candidates") or 0) or 150000,
                                                         ram_gb=(rep["runs"][0].get("system_total_gb"))), 1)
    except Exception as e:                              # the estimate is a comparison, never the report's point
        rep["estimate_gb"] = f"{type(e).__name__}: {e}"
    rep["finished_ts"] = time.time()
    with open(out_path, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    for d in list(dirs.values()) + [c_ckpt]:
        shutil.rmtree(d, ignore_errors=True)
    for p in (bA, bB, bC):
        try:
            os.remove(p)
        except OSError:
            continue
    return rep


def child(dg, board_out):
    """One build in this process, with the worker's own measurement around it (pc_worker._mem_report's
    memory stamp, without its report: the parent reports)."""
    import dfs_tourney
    import memprobe
    import pc_profiles
    import pc_worker
    prof = dict(pc_profiles.PROFILES[PROFILE], name=PROFILE)
    w = memprobe.Watch()
    art = dfs_tourney.build_nfl_classic(int(dg), min_pool=pc_worker._CL_MIN_POOL,
                                        log=lambda *a: print(*a, flush=True), **pc_profiles.build_kwargs(prof))
    if not art:
        print(f"[probe] nothing built: {dfs_tourney.last_refusal()}", flush=True)
        sys.exit(3)
    art["memory"] = w.stop()
    tmp = board_out + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(art, fh)
    os.replace(tmp, board_out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dg", type=int, required=True)
    ap.add_argument("--out")
    ap.add_argument("--root")
    ap.add_argument("--child", action="store_true")
    ap.add_argument("--board-out")
    a = ap.parse_args()
    if a.child:
        child(a.dg, a.board_out)
        return
    root = a.root or os.path.join(os.environ.get("VIGIL_SIM_CACHE_DIR") or os.path.join(_HERE, "pc-simcache"), "probe")
    rep = probe(a.dg, root, a.out or os.path.join(root, "..", "profile_probe.json"))
    print(json.dumps({k: rep[k] for k in ("profile", "dg", "agreement", "estimate_gb")}, default=str))


if __name__ == "__main__":
    main()
