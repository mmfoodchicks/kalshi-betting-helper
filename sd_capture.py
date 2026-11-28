"""Every NFL showdown's pre-lock input snapshot, kept for exact replay (2026-11-27; the owner: "Automatically
capture every Showdown's complete pre-lock input snapshot, model version, configuration and seeds. Preserve
everything necessary for exact replay and prevent post-lock data leakage.")

The PC (pc_worker._task_sd_capture, between builds) captures each showdown it would build twice: once at
LEAD_S before lock and once CLOSE_S before it (after the inactives). A capture is a CHILD process that runs the
eligibility study's recorder (research/dfs_eligibility_record.py: the production builder's gate and the
production simulator, seeded) with
  * every HTTP response recorded in order (research/den_pit_audit/netreplay.py, the audit's harness, keyed on
    method, URL, body, Accept-Encoding and User-Agent, sequences kept so a retry replays as it happened);
  * every deep-store file it reads copied, as first read, into the snapshot (`deep_orig/`), and every write
    landing in the snapshot (`deep_work/`), never in the PC's own store;
  * private, empty disk caches (no worlds cache, no checkpoints, no GPU) and one worker process, so nothing the
    snapshot does not hold can reach the build;
  * the model and configuration stamped: the commit, the engine and simulator versions, the board model, the
    seed and the world count, the eligibility model's sha256.
Post-lock leakage is refused three ways: a capture never starts within MIN_LEAD_S of lock, the recorder refuses a
locked slate, and a snapshot any of whose network answers arrived at or after lock is deleted (`refused`).

Exact replay is the test: right after a capture, a second child plays the same recording back with the network
FORBIDDEN (a live request fails the replay), the deep store a copy of `deep_orig/` and the clock shifted to the
capture's, and the board (the recorder's board.json, every world of every player, the pool files) must come out
bit for bit; the manifest says `replay: exact` or why not. The store keeps MAX_KEEP snapshots and MAX_GB on disk
(oldest first); a sha256 manifest lists every file. A small summary goes to the server (/api/pc/captures).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import errlog

_HERE = os.path.dirname(os.path.abspath(__file__))
LEAD_S = 6 * 3600                 # the first look: six hours before lock (any time from 6 h to 2 h before)
LEAD_LATEST_S = 2 * 3600
CLOSE_S = 50 * 60                 # the second: after the inactives (lock - 90 min), 50 to 15 minutes before lock
CLOSE_LATEST_S = 15 * 60
MIN_LEAD_S = 10 * 60              # never start closer than this: a capture takes minutes and must end pre-lock
CHILD_TIMEOUT_S = 40 * 60
MAX_KEEP = 40
MAX_GB = 8.0
WORLDS = 60000
_VOLATILE = ("ts", "recorded_ts", "built_ts", "age_s", "seconds_to_lock", "fetched_utc", "lock")


def root():
    base = os.environ.get("VIGIL_SIM_CACHE_DIR") or os.path.join(_HERE, "pc-simcache")
    return os.path.join(base, "sd-captures")


def due(slates, now, done):
    """[(dg, label, lock_ts)] the captures owed now: each showdown (one game, DraftKings' showdown type 96) in its
    lead window (LEAD_S .. LEAD_LATEST_S before lock) or its close window (CLOSE_S .. CLOSE_LATEST_S), not yet
    captured under that label (`done`: a set of (dg, label)), never within MIN_LEAD_S of lock."""
    out = []
    for sl in slates or []:
        if sl.get("games") != 1 or sl.get("contest_type") != 96 or not sl.get("draft_group_id"):
            continue
        import nfl_dfs
        lock = nfl_dfs.iso_ts(sl.get("starts"))
        if lock is None:
            continue
        left = lock - now
        if left < MIN_LEAD_S:
            continue
        for label, hi, lo in (("lead", LEAD_S, LEAD_LATEST_S), ("close", CLOSE_S, CLOSE_LATEST_S)):
            if lo <= left <= hi and (int(sl["draft_group_id"]), label) not in done:
                out.append((int(sl["draft_group_id"]), label, int(lock)))
    return sorted(out, key=lambda x: x[2])


def captured(base=None):
    """{(dg, label)} already in the store (refused ones included: a refusal is not retried into lock)."""
    base = base or root()
    out = set()
    if not os.path.isdir(base):
        return out
    for d in os.listdir(base):
        m = os.path.join(base, d, "manifest.json")
        if os.path.exists(m):
            try:
                j = json.load(open(m))
                out.add((int(j["dg"]), j["label"]))
            except (OSError, ValueError, KeyError) as e:
                errlog.note("SDCAP-manifest", e, path=m)
    return out


def _sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def manifest_files(snap):
    """{relative path: sha256} of every file of a snapshot except the manifest itself."""
    out = {}
    for dp, _dn, fns in os.walk(snap):
        for fn in fns:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, snap)
            if rel != "manifest.json":
                out[rel.replace(os.sep, "/")] = _sha_file(p)
    return dict(sorted(out.items()))


def prune(base=None, keep=MAX_KEEP, max_gb=MAX_GB):
    """Oldest snapshots out until at most `keep` remain and they fit `max_gb`."""
    base = base or root()
    if not os.path.isdir(base):
        return []
    snaps = []
    for d in os.listdir(base):
        p = os.path.join(base, d)
        if os.path.isdir(p):
            size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _dn, fs in os.walk(p) for f in fs)
            snaps.append((os.path.getmtime(p), p, size))
    snaps.sort()
    gone = []
    while snaps and (len(snaps) > keep or sum(s[2] for s in snaps) > max_gb * 1024 ** 3):
        _t, p, _s = snaps.pop(0)
        shutil.rmtree(p, ignore_errors=True)
        gone.append(os.path.basename(p))
    return gone


def stamp():
    """The model and configuration a capture was made under."""
    import dfs_eligibility
    import dfs_tourney
    import nfl_dfs_sim
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_HERE, capture_output=True, text=True,
                                timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        errlog.note("SDCAP-git", e)
        commit = None
    model_sha = _sha_file(dfs_eligibility.MODEL_PATH) if os.path.exists(dfs_eligibility.MODEL_PATH) else None
    return {"commit": commit, "sd_engine": dfs_tourney.SD_ENGINE, "engine": dfs_tourney.ENGINE,
            "board_model": dfs_tourney.BOARD_MODEL, "sim_model_version": getattr(nfl_dfs_sim, "SIM_MODEL_VERSION", None),
            "score_path_sig": dfs_tourney.score_path_sig(), "eligibility_model_sha256": model_sha,
            "python": sys.version.split()[0], "numpy": _numpy_version(), "worlds": WORLDS}


def _numpy_version():
    try:
        import numpy
        return numpy.__version__
    except ImportError:
        return None


# ---- the child: record or replay, in its own process so the patches never touch the worker ----

def _patch_deep(snap, real_dir):
    """deep_cache reads and writes inside the snapshot: a key's file is copied from the real store into
    deep_orig/ (as first read, never again) and deep_work/ the first time any path is asked for it."""
    import deep_cache
    orig, work = os.path.join(snap, "deep_orig"), os.path.join(snap, "deep_work")
    os.makedirs(orig, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    seen = set()
    real_path = deep_cache._path

    def _path(key):
        p = real_path(key)                      # CACHE_DIR is deep_work now
        fn = os.path.basename(p)
        if fn not in seen:
            seen.add(fn)
            src = os.path.join(real_dir, fn) if real_dir else None
            if src and os.path.exists(src) and not os.path.exists(p):
                shutil.copy2(src, os.path.join(orig, fn))
                shutil.copy2(src, p)
        return p
    deep_cache.CACHE_DIR = work
    deep_cache._path = _path


def _child_env(snap, phase):
    priv = os.path.join(snap, f"priv_{phase}")
    for sub in ("sim", "run", "scratch", "tmp"):
        os.makedirs(os.path.join(priv, sub), exist_ok=True)
    os.environ.update(VIGIL_NO_BG="1", VIGIL_POOL_CACHE_DIR="", VIGIL_SIM_CACHE_DIR=os.path.join(priv, "sim"),
                      VIGIL_RUN_DIR=os.path.join(priv, "run"), VIGIL_SCRATCH_DIR=os.path.join(priv, "scratch"),
                      TMPDIR=os.path.join(priv, "tmp"))
    for k in ("VIGIL_CKPT_DIR", "VIGIL_TOURNEY_GPU", "VIGIL_GPU"):
        os.environ.pop(k, None)
    return priv


def _load(name, rel):
    """A research module by its file (the audit's harness, the recorder): loaded once per process under its own
    name, so every caller shares one module object (netreplay's patch state lives in it)."""
    import importlib.util
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, *rel.split("/")))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _netreplay():
    return _load("netreplay", "research/den_pit_audit/netreplay.py")


def _recorder():
    return _load("dfs_eligibility_record", "research/dfs_eligibility_record.py")


def child_capture(dg, snap, lock_ts, builder=None):
    """In a fresh process: record the build's every input into `snap`. Returns the capture's meta."""
    real_deep = os.environ.get("DEEP_CACHE_DIR") or os.path.join(_HERE, "data", "deep")
    _child_env(snap, "capture")
    NR = _netreplay()
    NR.install(os.path.join(snap, "net"), "record", os.path.join(snap, "requests.jsonl"))
    _patch_deep(snap, real_deep)
    t0 = time.time()
    if t0 >= lock_ts - MIN_LEAD_S:
        raise SystemExit("too close to lock: a capture must end before it")
    if builder is None:
        out = _recorder().record(dg, os.path.join(snap, "rec"), workers=1)
    else:
        out = builder(dg, os.path.join(snap, "rec"))
    t1 = time.time()
    net_t = [json.load(open(os.path.join(snap, "net", f))).get("t") for f in os.listdir(os.path.join(snap, "net"))
             if f.endswith(".json")] if os.path.isdir(os.path.join(snap, "net")) else []
    meta = {"dg": int(dg), "lock_ts": int(lock_ts), "started_ts": t0, "ended_ts": t1,
            "net_requests": len(net_t), "net_last_ts": max(net_t) if net_t else None,
            "counts": NR.counts(), "result": out}
    json.dump(meta, open(os.path.join(snap, "capture.json"), "w"), indent=1, default=str)
    return meta


def child_replay(dg, snap, builder=None):
    """In a fresh process: play the recording back -- the network forbidden, the deep store a copy of what the
    capture first read, the clock the capture's -- into `snap/replay`."""
    import urllib.error
    cap = json.load(open(os.path.join(snap, "capture.json")))
    work = os.path.join(snap, "priv_replay", "deep")
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(os.path.join(snap, "deep_orig"), work)
    os.environ["DEEP_CACHE_DIR"] = work
    import deep_cache
    deep_cache.CACHE_DIR = work               # whatever imported it first, the replay reads the snapshot only
    _child_env(snap, "replay")
    NR = _netreplay()

    def _forbidden(req, *a, **k):
        raise urllib.error.URLError("live fetch during replay (the recording does not hold this request)")
    NR._REAL = _forbidden
    NR.install(os.path.join(snap, "net"), "replay", os.path.join(snap, "replay_requests.jsonl"))
    real_time = time.time
    offset = float(cap["started_ts"]) - real_time()
    time.time = lambda: real_time() + offset
    out_dir = os.path.join(snap, "replay")
    shutil.rmtree(out_dir, ignore_errors=True)
    if builder is None:
        _recorder().record(dg, out_dir, workers=1)
    else:
        builder(dg, out_dir)
    json.dump({"counts": NR.counts()}, open(os.path.join(snap, "replay.json"), "w"))


def _canon(obj):
    """A JSON value with the clock's own stamps removed (a replay runs at another wall time)."""
    if isinstance(obj, dict):
        return {k: _canon(v) for k, v in obj.items() if k not in _VOLATILE}
    if isinstance(obj, list):
        return [_canon(v) for v in obj]
    return obj


def compare(snap):
    """'exact' when the replay reproduced the capture's board bit for bit, else the first difference."""
    import gzip
    import pickle
    try:
        import numpy as np                # the PC's (requirements-pc.txt); the server never captures
    except ImportError:
        return "numpy missing: the worlds cannot be compared here"
    a, b = os.path.join(snap, "rec"), os.path.join(snap, "replay")
    rep = json.load(open(os.path.join(snap, "replay.json"))) if os.path.exists(os.path.join(snap, "replay.json")) else {}
    if (rep.get("counts") or {}).get("miss"):
        return f"the replay asked for {rep['counts']['miss']} request(s) the recording does not hold"
    for fn in ("board.json", "contest.json", "dk_players.json"):
        if _canon(json.load(open(os.path.join(a, fn)))) != _canon(json.load(open(os.path.join(b, fn)))):
            return f"{fn} differs"
    for fn, part in (("sleeper_proj.json.gz", "rows"), ("usage_hist.json.gz", "hist")):
        if json.load(gzip.open(os.path.join(a, fn), "rt"))[part] != json.load(gzip.open(os.path.join(b, fn), "rt"))[part]:
            return f"{fn} differs"
    pa, pb = pickle.load(open(os.path.join(a, "pool_engine.pkl"), "rb")), pickle.load(open(os.path.join(b, "pool_engine.pkl"), "rb"))
    if sorted(pa) != sorted(pb):
        return "the pool holds other players"
    for k in pa:
        if pa[k].get("proj") != pb[k].get("proj") or not np.array_equal(np.asarray(pa[k]["arr"]), np.asarray(pb[k]["arr"])):
            return f"{k}'s worlds differ"
    return "exact"


def post_lock(cap, lock_ts):
    """True when the capture ended, or any network answer it holds arrived, at or after lock: such a snapshot
    could carry in-game information into numbers that claim to be pre-game, so it is refused."""
    ts = [cap.get("ended_ts"), cap.get("net_last_ts")]
    return any(t is not None and float(t) >= float(lock_ts) for t in ts)


def capture(dg, label, lock_ts, base=None, verify=True, python=None, now=None, timeout=CHILD_TIMEOUT_S):
    """One snapshot, start to finish, from the worker: the capture child, the post-lock check, the replay child
    and its comparison, the manifest. Returns the summary row (the server's listing)."""
    base = base or root()
    now = time.time() if now is None else now
    snap = os.path.join(base, f"{int(dg)}_{label}_{int(now)}")
    os.makedirs(snap, exist_ok=True)
    py = python or sys.executable
    row = {"dg": int(dg), "label": label, "lock_ts": int(lock_ts), "dir": os.path.basename(snap), "status": None}

    def run(args):
        return subprocess.run([py, os.path.abspath(__file__)] + args, cwd=_HERE, capture_output=True, text=True,
                              timeout=timeout)
    try:
        got = run(["--capture", str(dg), snap, str(int(lock_ts))])
        if got.returncode != 0:
            row.update(status="failed", why=(got.stderr or got.stdout or "")[-300:])
        else:
            cap = json.load(open(os.path.join(snap, "capture.json")))
            if post_lock(cap, lock_ts):
                # post-lock data must never reach a number that claims to be pre-game: the whole snapshot goes
                shutil.rmtree(os.path.join(snap, "net"), ignore_errors=True)
                shutil.rmtree(os.path.join(snap, "rec"), ignore_errors=True)
                row.update(status="refused", why="a network answer or the capture's end came at or after lock")
            else:
                row.update(status="captured", net_requests=cap.get("net_requests"),
                           started_ts=cap.get("started_ts"), ended_ts=cap.get("ended_ts"))
                if verify:
                    rp = run(["--replay", str(dg), snap])
                    row["replay"] = compare(snap) if rp.returncode == 0 else f"replay failed: {(rp.stderr or '')[-300:]}"
    except subprocess.TimeoutExpired:
        row.update(status="failed", why=f"timed out after {timeout}s")
    except Exception as e:
        errlog.note("SDCAP-capture", e, path=str(dg))
        row.update(status="failed", why=f"{type(e).__name__}: {e}")
    # the recording says which look it is and whether it replayed exact: the registered reads take ONLY the look
    # their registration named in advance (primary_snapshot) and only an exact one -- never whichever did better
    rec_meta = os.path.join(snap, "rec", "meta.json")
    if os.path.exists(rec_meta) and row.get("status") == "captured":
        try:
            m = json.load(open(rec_meta))
            m.update(label=label, snapshot=os.path.basename(snap), replay=row.get("replay"))
            json.dump(m, open(rec_meta, "w"), indent=1)
        except (OSError, ValueError) as e:
            errlog.note("SDCAP-meta", e, path=rec_meta)
    for sub in ("priv_capture", "priv_replay", "replay"):
        shutil.rmtree(os.path.join(snap, sub), ignore_errors=True)   # the replay's own output is the evidence only
    try:
        man = {"dg": int(dg), "label": label, "lock_ts": int(lock_ts), "status": row["status"],
               "replay": row.get("replay"), "why": row.get("why"), "stamp": stamp(), "seed": int(dg),
               "files": manifest_files(snap)}
        json.dump(man, open(os.path.join(snap, "manifest.json"), "w"), indent=1, default=str)
        row["manifest_sha256"] = _sha_file(os.path.join(snap, "manifest.json"))
        row["bytes"] = sum(os.path.getsize(os.path.join(dp, f)) for dp, _dn, fs in os.walk(snap) for f in fs)
        row["commit"] = man["stamp"].get("commit")
    except Exception as e:
        errlog.note("SDCAP-manifest", e, path=snap)
    prune(base)
    return row


if __name__ == "__main__":
    if sys.argv[1] == "--capture":
        child_capture(int(sys.argv[2]), sys.argv[3], float(sys.argv[4]))
    elif sys.argv[1] == "--replay":
        child_replay(int(sys.argv[2]), sys.argv[3])
