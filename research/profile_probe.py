"""The deep profile on the owner's PC, measured: memory, VRAM, disk, temperatures, runtime, and real
interruptions and resumes (2026-11-27; v2 the same day).

The owner approved one unattended deep build of the week's main slate "provided it does not interfere with
scheduled production builds or slate deadlines" (pc_worker._task_profile_probe decides when), and asked for
"actual peak RAM, VRAM, disk usage, temperatures, runtime, checkpoint recovery and numerical agreement".

v1 (deep-v1) measured that a resume resumed NOTHING: each build re-fetched its inputs live (Sleeper's lines,
the deep profiles, the drive levels, DraftKings' slate), the inputs had moved, and the checkpoint keys --
which are keys of the inputs, correctly -- named a different build (B `resumed: {}`, 7,129 s; C 7,163 s; the
worlds cache one ~0.66 GB entry larger per run). Production now freezes a started build's inputs under its
REQUEST key (build_ckpt, dfs_tourney._BuildInputs): "Resume existing build" reuses the frozen inputs, the
cached worlds and the completed stages; "Start fresh build" fetches current information.

v2 tests that production path. One build is interrupted at three stages and resumed each time, every run
its own process, the whole process tree killed (a real interruption, mid-pass, with the pool's workers):

  A1  Start fresh build, KILLED at the best-lineup solves' first progress line (25% of the generation worlds);
  A2  Resume existing build, KILLED once the scoring pass reports KILL_FRAC of the ranking worlds;
  A3  Resume existing build, KILLED in the portfolio pass (its first finished make-it range on disk, or
      PORTFOLIO_KILL_S after the pass starts when its matrix is over the checkpoint budget);
  B   Resume existing build, to the end;
  C   the uninterrupted build: an EMPTY checkpoint directory given only A1's frozen inputs and A1's
      generator entropy (rng.json), on the probe's worlds cache -- the same production code path, the same
      frozen inputs, worlds, seed (none: production is unseeded) and random stream, nothing resumed.

B must equal C exactly (every figure on the board but the clocks and the checkpoint stamp). The comparison
is REFUSED, loudly, when any two runs ran different code or model versions (each child records its git
commit, the code signatures and the engine and simulator versions: the worker's loop may pull between
runs), or when C did not read A1's frozen inputs. Each child also reads the inputs LIVE before it builds
(dfs_tourney.input_digests of the same reads the build makes; nfl_dfs_sim.engine_inputs) so the report says
which inputs drifted, which Sleeper player fields moved for how many players, and over what time.

While each runs a sampler keeps, every two seconds: the machine's memory in use (memprobe; samples it cannot
read sanely are counted, never reported as a figure), the GPU's temperature, memory in use, load and power
(nvidia-smi; this build is CPU-only, so VRAM is a baseline), and the bytes under the probe's own directories.
CPU temperature is not readable without administrator rights on Windows, and the report says so.

Isolation: the probe's worlds cache, checkpoints and scratch are its own directories (never production's
entries or a production build's checkpoint), its boards are written to its own directory and NEVER to
boardshare, and they are deleted at the end with everything else but the report. Nothing a server sends
shapes what runs: the profile, the stages and the fraction are fixed here.

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

VERSION = 2
PROFILE = "deep"
KILL_FRAC = 0.4
PORTFOLIO_KILL_S = 90.0
SAMPLE_S = 2.0
MAX_DIFFS = 40
_PROGRESS = re.compile(r"\[tourney\]\s+([\d,]+)/([\d,]+) worlds, \d+s\s*$")
_SOLVED = re.compile(r"\[tourney\]\s+([\d,]+)/([\d,]+) worlds solved, \d+s\s*$")
_PORT_START = re.compile(r"\[tourney\] classic \d+: scored \(\d+s\); portfolios of")
_OPLINE = re.compile(r"\[tourney\] classic \d+: (Resume existing build|Start fresh build) -- (.*) \(request (\w+)\)\s*$")
_CKLINE = re.compile(r"\[tourney\] classic \d+: checkpoint (\w+) \((new|found)\)")
_VOLATILE = ("built_ts", "timings", "timing", "checkpoint", "eval_artifact", "memory", "requested_by", "profile")
# (tag, where it is killed); None runs to the end
STAGES = (("A1 killed in the best-lineup solves", "solve"), ("A2 killed in scoring", "score"),
          ("A3 killed in the portfolio pass", "portfolio"), ("B resumed to the end", None))


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
        self.anom0 = memprobe.ANOMALIES["n"]
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
            used, t = self.mp.system_used()
            self._max("system_used", used)
            self.total = self.total or t
            t_, m, u, w = _gpu()
            self._max("gpu_temp_c", t_)
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
        bad = self.mp.ANOMALIES["n"] - self.anom0
        out = {"system_peak_used_gb": round(p["system_used"] / gb, 2) if p["system_used"] else None,
               "system_start_used_gb": round(self.base_used / gb, 2) if self.base_used else None,
               "system_total_gb": round(self.total / gb, 2) if self.total else None,
               "build_added_gb": (round((p["system_used"] - self.base_used) / gb, 2)
                                  if p["system_used"] and self.base_used else None),
               "system_unreadable_samples": bad,
               "system_memory": "unreadable" if not p["system_used"] else ("partly unreadable" if bad else "read"),
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


def _makeit_on_disk(ckpt_dir):
    """Whether the portfolio pass has saved a finished make-it range (build_ckpt) under `ckpt_dir`."""
    b = os.path.join(ckpt_dir, "builds")
    try:
        return any(fn.startswith("makeit-") and fn.endswith(".ck")
                   for k in os.listdir(b) for fn in os.listdir(os.path.join(b, k)))
    except OSError:
        return False


def _run(tag, dg, env, dirs, board_out, kill=None, timeout_s=4 * 3600):
    """One build in its own process; `kill` ("solve" | "score" | "portfolio" | None) is where its tree is
    killed. Returns what the run saw: its operation and keys off its log, the live inputs' digests its
    child wrote, the sampler's peaks."""
    dig_out = board_out + ".digests.json"
    cmd = [sys.executable, os.path.abspath(__file__), "--child", "--dg", str(dg), "--board-out", board_out,
           "--digests-out", dig_out]
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    s = _Sampler(dirs)
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=_HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            bufsize=1, **kw)
    st = {"killed_at": None, "port_t": None, "lock": threading.Lock()}

    def kill_now():
        with st["lock"]:
            if st["killed_at"] is None:
                st["killed_at"] = round(time.time() - t0, 1)
                _kill_tree(proc)

    stop_watch = threading.Event()

    def watch():
        # the portfolio pass logs nothing while it runs: the kill reads the checkpoint directory and the clock
        while not stop_watch.wait(1.0):
            if kill == "portfolio" and st["port_t"] is not None and st["killed_at"] is None and \
                    (_makeit_on_disk(env.get("VIGIL_CKPT_DIR") or "") or time.time() - st["port_t"] >= PORTFOLIO_KILL_S):
                kill_now()
                return
    wt = threading.Thread(target=watch, daemon=True)
    wt.start()
    tail, frac, op, rkey, bkey, why = [], 0.0, None, None, None, None
    for line in proc.stdout:
        tail = (tail + [line.rstrip()])[-40:]
        m = _OPLINE.search(line)
        if m:
            op, why, rkey = m.group(1), m.group(2), m.group(3)
        m = _CKLINE.search(line)
        if m:
            bkey = m.group(1)
        m = _PROGRESS.search(line)
        if m:
            done, n = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
            frac = done / max(1, n)
            if kill == "score" and frac >= KILL_FRAC:
                kill_now()
                break
        if kill == "solve" and _SOLVED.search(line):
            kill_now()
            break
        if kill == "portfolio" and st["port_t"] is None and _PORT_START.search(line):
            st["port_t"] = time.time()
        if time.time() - t0 > timeout_s:
            kill_now()
            tail.append(f"[probe] timed out after {timeout_s} s")
            break
    stop_watch.set()
    try:
        rc = proc.wait(timeout=600)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        rc = proc.returncode
    stats = s.stop()
    try:
        with open(dig_out) as fh:
            seen = json.load(fh)
    except (OSError, ValueError):
        seen = {}
    return dict(tag=tag, kill=kill, seconds=round(time.time() - t0, 1), returncode=rc, killed_at_s=st["killed_at"],
                completed=st["killed_at"] is None and rc == 0, scoring_frac_seen=round(frac, 3), operation=op,
                operation_why=why, request_key=rkey, build_key=bkey, versions=seen.get("versions"),
                live=seen.get("live"), log_tail=tail[-12:], **stats)


def _strip(board):
    return {k: v for k, v in (board or {}).items() if k not in _VOLATILE}


def _diff(a, b, path="", out=None, cap=MAX_DIFFS):
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


def _versions_split(runs):
    """[(field, {tag: value})] for every version field two runs disagree on."""
    fields = sorted({k for r in runs for k in (r.get("versions") or {})})
    out = []
    for f in fields:
        vals = {r["tag"]: (r.get("versions") or {}).get(f) for r in runs}
        if len(set(json.dumps(v, sort_keys=True, default=str) for v in vals.values())) > 1:
            out.append((f, vals))
    return out


def _drift(runs):
    """Which live inputs moved after A1's read, for every later run: the components whose digests differ,
    the Sleeper player fields that moved and for how many players, and the seconds since A1's read."""
    base = next((r.get("live") for r in runs if (r.get("live") or {}).get("digests")), None)
    if not base:
        return {"error": "no run read its live inputs"}
    out = {"first_read_ts": base.get("ts"), "first_read_digests": base.get("digests"), "later": []}
    for r in runs[1:]:
        lv = r.get("live") or {}
        if not lv.get("digests"):
            out["later"].append({"tag": r["tag"], "error": lv.get("error") or "not read"})
            continue
        a, b = base["digests"], lv["digests"]
        moved = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        ga, gb = base.get("games_fields") or {}, lv.get("games_fields") or {}
        fields = {}
        for f in sorted(set(ga) | set(gb)):
            pa, pb = ga.get(f) or {}, gb.get(f) or {}
            n = sum(1 for p in set(pa) | set(pb) if pa.get(p) != pb.get(p))
            if n:
                fields[f] = n
        players_a = set().union(*[set(v) for v in ga.values()]) if ga else set()
        players_b = set().union(*[set(v) for v in gb.values()]) if gb else set()
        out["later"].append({"tag": r["tag"], "seconds_after_first": round(float(lv.get("ts") or 0) - float(base.get("ts") or 0)),
                             "moved": moved, "player_fields_moved": fields,
                             "players_added": len(players_b - players_a), "players_removed": len(players_a - players_b)})
    return out


def _copy_frozen(src_ckpt, dst_ckpt):
    """A1's frozen inputs (inputs/<request>/) and its generator entropy (builds/<key>/rng.json) into an
    empty checkpoint directory: what C reads, through production's own resume path."""
    shutil.rmtree(dst_ckpt, ignore_errors=True)
    os.makedirs(dst_ckpt, exist_ok=True)
    got = {"inputs": [], "rng": []}
    inp = os.path.join(src_ckpt, "inputs")
    if os.path.isdir(inp):
        shutil.copytree(inp, os.path.join(dst_ckpt, "inputs"))
        got["inputs"] = sorted(os.listdir(inp))
    b = os.path.join(src_ckpt, "builds")
    for k in (os.listdir(b) if os.path.isdir(b) else []):
        p = os.path.join(b, k, "rng.json")
        if os.path.exists(p):
            os.makedirs(os.path.join(dst_ckpt, "builds", k), exist_ok=True)
            shutil.copy2(p, os.path.join(dst_ckpt, "builds", k, "rng.json"))
            got["rng"].append(k)
    return got


def _agreement(runs, B, C, copied):
    """The comparison of B and C -- or the reason it is refused."""
    split = _versions_split(runs)
    out = {"entropy_copied": bool(copied.get("rng")), "inputs_copied": bool(copied.get("inputs")),
           "both_built": bool(B) and bool(C), "comparable": False, "equal": None, "first_differences": None,
           "refused": None}
    cB, cC = (B or {}).get("checkpoint") or {}, (C or {}).get("checkpoint") or {}
    iB, iC = cB.get("inputs") or {}, cC.get("inputs") or {}
    a1 = runs[0] if runs else {}
    if split:
        out["refused"] = ("REFUSED: the runs ran different code or model versions (the worker's loop may pull "
                          "between runs): " + "; ".join(f"{f} {v}" for f, v in split))
    elif not (B and C):
        out["refused"] = "REFUSED: B or C built no board"
    elif iC.get("operation") != "Resume existing build" or iC.get("key") != a1.get("request_key"):
        out["refused"] = (f"REFUSED: C did not read A1's frozen inputs ({iC.get('operation')}: {iC.get('why')}; "
                          f"request {iC.get('key')} vs A1's {a1.get('request_key')})")
    elif iB.get("key") != iC.get("key") or cB.get("key") != cC.get("key"):
        out["refused"] = (f"REFUSED: B and C are not the same request and build (request {iB.get('key')} vs "
                          f"{iC.get('key')}, build {cB.get('key')} vs {cC.get('key')})")
    else:
        out["comparable"] = True
        out["equal"] = _strip(B) == _strip(C)
        out["first_differences"] = _diff(_strip(B), _strip(C))
        out["seed"] = {"B": B.get("seed"), "C": C.get("seed")}
        out["rng"] = {"B": (cB.get("resumed") or {}).get("rng"), "C": (cC.get("resumed") or {}).get("rng")}
        out["inputs_digests_equal"] = iB.get("digests") == iC.get("digests")
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
           "kill_frac": KILL_FRAC, "portfolio_kill_s": PORTFOLIO_KILL_S, "stages": [t for t, _k in STAGES] + ["C fresh"],
           "started_ts": time.time(), "estimate_gb": None, "runs": []}
    shutil.rmtree(dirs["checkpoints"], ignore_errors=True)
    os.makedirs(dirs["checkpoints"], exist_ok=True)
    c_ckpt = os.path.join(root, "ckpt_fresh")
    boards = {}
    copied = {}
    paths = []
    for i, (tag, kill) in enumerate(STAGES):
        bp = os.path.join(root, f"board_{i}.json")
        paths.append(bp)
        rep["runs"].append(_run(tag, dg, env, dirs, bp, kill=kill))
        if i == 0:
            # C's inputs are A1's, frozen before anything else ran
            copied = _copy_frozen(dirs["checkpoints"], c_ckpt)
            rep["checkpoint_after_A1"] = {"builds": len(copied.get("rng") or []), "inputs": len(copied.get("inputs") or []),
                                          "bytes": _bytes(dirs["checkpoints"])}
    bC = os.path.join(root, "board_C.json")
    paths.append(bC)
    envC = dict(env, VIGIL_CKPT_DIR=c_ckpt)
    rep["runs"].append(_run("C fresh", dg, envC, dict(dirs, checkpoints=c_ckpt), bC))
    for t, p in (("B", paths[len(STAGES) - 1]), ("C", bC)):
        try:
            with open(p) as fh:
                boards[t] = json.load(fh)
        except (OSError, ValueError):
            boards[t] = None
    B, C = boards["B"], boards["C"]
    rep["resumed"] = (B or {}).get("checkpoint")
    rep["fresh_checkpoint"] = (C or {}).get("checkpoint")
    rep["keys"] = {"request": {r["tag"]: r.get("request_key") for r in rep["runs"]},
                   "build": {r["tag"]: r.get("build_key") for r in rep["runs"]},
                   "operation": {r["tag"]: r.get("operation") for r in rep["runs"]}}
    rep["versions"] = {r["tag"]: r.get("versions") for r in rep["runs"]}
    rep["drift"] = _drift(rep["runs"])
    for r in rep["runs"]:
        r.pop("live", None)                 # the per-player digests stay out of the report (the drift has them)
    rep["board_memory"] = {t: (boards[t] or {}).get("memory") for t in boards}
    rep["timings"] = {t: (boards[t] or {}).get("timings") for t in boards}
    rep["agreement"] = _agreement(rep["runs"], B, C, copied)
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
    for p in paths:
        for q in (p, p + ".digests.json"):
            try:
                os.remove(q)
            except OSError:
                continue
    return rep


def versions():
    """What a run's numbers depend on besides its inputs: the commit and every code signature and version."""
    import build_ckpt
    import dfs_tourney
    import nfl_dfs_engine
    import pool_cache
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_HERE, capture_output=True, text=True,
                                timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"commit": commit, "tourney_version": dfs_tourney.VERSION, "tourney_engine": dfs_tourney.ENGINE,
            "scoring_code": build_ckpt.code_sig()[:16], "inputs_code": build_ckpt.inputs_code_sig()[:16],
            "simulator_code": pool_cache.code_sig()[:16], "sim_model": nfl_dfs_engine.SIM_MODEL,
            "sim_model_version": nfl_dfs_engine.SIM_MODEL_VERSION, "deep_weight": nfl_dfs_engine.DEEP_WEIGHT}


def live_inputs(dg, min_pool):
    """The inputs a build of `dg` would read now, read the way build_nfl_classic reads them, in its frozen
    form (dfs_tourney._BuildInputs.snap) so dfs_tourney.input_digests describes both. The depth gate runs on
    the slate's players with Sleeper's projections (no worlds): its answer on the live roster."""
    import dfs_tourney as T
    import dk
    import nfl_dfs
    import nfl_dfs_sim
    import nfl_game_sim
    import nfl_preseason
    import simulate
    ts = time.time()
    slate = dk.slate_for("nfl", draft_group_id=int(dg))
    rows = (slate or {}).get("contests") or []
    want = [int(c["id"]) for c in rows if float(c.get("prize_pool") or 0) >= float(min_pool)]
    details = [dk.contest_detail(cid) for cid in want[:6]]
    preseason = nfl_preseason.is_preseason()
    week = nfl_game_sim.current_week(preseason, day=T._slate_day(slate))
    csv_players = [c for c in simulate.parse_dk_csv(slate["csv"]) if nfl_dfs._playable(c)]
    teams = {str(c.get("team") or "").upper() for c in csv_players if c.get("team")}
    pool = nfl_dfs_sim.engine_inputs(week, teams=teams)
    proj = {}
    for g in (pool.get("games") or {}).values():
        for pl in g.get("players") or []:
            proj[pl["name"]] = {"pos": pl.get("pos"), "team": pl.get("team"), "proj": pl.get("proj_pts")}
    nidx, norm = nfl_dfs._norm_index(proj)
    ents = []
    for c in csv_players:
        pos = (c.get("pos") or "").upper().split("/")[0]
        if pos not in T._CL_POS:
            continue
        hit = nfl_dfs._pool_match(proj, c["name"], pos, c.get("team"), nidx, norm) or {}
        ents.append({"name": c["name"], "pos": pos, "team": c.get("team"), "proj": hit.get("proj") or 0.0})
    kept, dx = nfl_dfs._apply_depth(ents, preseason, nfl_dfs.seconds_to_lock(slate.get("starts")))
    snap = {"slate": slate, "details": details, "week": (week, preseason), "pool": pool,
            "depth": {"kept": [(e["name"], e.get("depth")) for e in kept], "dx": dx,
                      "status": T.status_classes(sorted(c["name"] for c in csv_players)),
                      "roster": nfl_dfs.roster_state()}}
    return {"ts": ts, "digests": T.input_digests(snap), "games_fields": T.games_fields(pool.get("games"))}


def child(dg, board_out, digests_out=None):
    """One build in this process, with the worker's own measurement around it (pc_worker._mem_report's
    memory stamp, without its report: the parent reports). First the versions and the live inputs' digests
    (written before the build, so a killed run has them too)."""
    import dfs_tourney
    import memprobe
    import pc_profiles
    import pc_worker
    prof = dict(pc_profiles.PROFILES[PROFILE], name=PROFILE)
    if digests_out:
        seen = {"versions": versions()}
        try:
            seen["live"] = live_inputs(dg, pc_worker._CL_MIN_POOL)
        except Exception as e:                  # a measurement, never the build's business
            seen["live"] = {"error": f"{type(e).__name__}: {e}"[:300]}
        with open(digests_out, "w") as fh:
            json.dump(seen, fh, default=str)
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
    ap.add_argument("--digests-out")
    a = ap.parse_args()
    if a.child:
        child(a.dg, a.board_out, a.digests_out)
        return
    root = a.root or os.path.join(os.environ.get("VIGIL_SIM_CACHE_DIR") or os.path.join(_HERE, "pc-simcache"), "probe")
    rep = probe(a.dg, root, a.out or os.path.join(root, "..", "profile_probe.json"))
    print(json.dumps({k: rep[k] for k in ("profile", "dg", "agreement", "estimate_gb")}, default=str))


if __name__ == "__main__":
    main()
