"""Cross-worker board cache: what one gunicorn worker builds, all of them serve.

The NFL slate and DFS boards kept their finished product in each worker's
memory with a background thread per worker to build it. With three workers
that meant three duplicate builds of the same board (tripling the Kalshi and
Sleeper fetch load, which is how throttling storms start), and a browser whose
polls round-robin across workers saw the board flicker between "simulating in
the background" and results depending on who answered -- the "testy" NFL tab.
Same disease the MLB slate had, same cure: publish the finished board on disk
where every worker can read it, and let exactly ONE worker claim each build.

Placement mirrors the other shared stores (VIGIL_SIM_CACHE_DIR, else
DEEP_CACHE_DIR, else /tmp): on the data disk it also survives a restart, and a
board that expires is rebuilt anyway, so a wiped /tmp merely costs one build.
"""
import os
import json
import pickle
import tempfile
import time

import errlog

_DIR = os.path.join(os.environ.get("VIGIL_SIM_CACHE_DIR")
                    or os.environ.get("DEEP_CACHE_DIR") or "/tmp", "boards")

# A claim older than this belongs to a dead builder and may be taken over.
CLAIM_TIMEOUT = 300


def _path(name, ext):
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(name))
    return os.path.join(_DIR, f"{safe}.{ext}")


def get(name, max_age):
    """(payload, age_seconds) published by any worker, or (None, None)."""
    try:
        p = _path(name, "pkl")
        age = time.time() - os.stat(p).st_mtime
        if max_age is not None and age > max_age:
            return None, None
        with open(p, "rb") as fh:
            return pickle.load(fh), age
    except OSError:
        return None, None           # not on disk yet -- the normal cold case
    except Exception as e:
        errlog.note("BOARD-read", e, path=str(name))
        return None, None


def put(name, payload, age=0):
    """Publish a finished board. `age` backdates it, which is how a deliberately
    short-lived placeholder ("no games this week", "build failed") expires early
    for EVERY worker instead of only the one that built it."""
    try:
        os.makedirs(_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_DIR, suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        final = _path(name, "pkl")
        os.replace(tmp, final)
        if age:
            t = time.time() - age
            os.utime(final, (t, t))
        # Boards a day old are dead weight on a 1 GB disk.
        cutoff = time.time() - 86400
        for f in os.listdir(_DIR):
            fp = os.path.join(_DIR, f)
            try:
                if os.stat(fp).st_mtime < cutoff:
                    os.remove(fp)
            except OSError:
                pass
    except Exception as e:
        errlog.note("BOARD-write", e, path=str(name))


def claim(name):
    """True in the ONE process that should build this board right now. A claim
    left by a dead builder is taken over once it is CLAIM_TIMEOUT old."""
    try:
        os.makedirs(_DIR, exist_ok=True)
        p = _path(name, "lock")
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            if time.time() - os.stat(p).st_mtime > CLAIM_TIMEOUT:
                os.utime(p, None)
                return True
            return False
    except Exception as e:
        errlog.note("BOARD-claim", e, path=str(name))
        return True                 # cannot coordinate -> single-worker behaviour


def release(name):
    try:
        os.remove(_path(name, "lock"))
    except OSError:
        pass


def nonblocking(name, ttl, mem, key, build, note_id=None):
    """The whole shared non-blocking board pattern in one call:
    memory-fresh -> adopt a sibling's disk copy with its age -> claimed
    background build -> stale value (or None) meanwhile.

    Seven modules (golf, LoL, NBA, NHL, tennis, UFC, futures) each hand-rolled
    this dance into per-worker memory, so every worker built its own copy of
    the same board and the browser's polls flapped between "building" and
    results depending on which worker answered. `mem` stays the module's own
    cache dict {key: (ts, val)} so its other readers are untouched; `build`
    returns the payload or None. A build that RAISES is recorded under
    `note_id` -- half of these threads had no exception handler, which is how
    a board could stay "building..." forever with the reason lost."""
    import threading
    now = time.time()
    hit = mem.get(key)
    if hit and now - hit[0] < ttl and hit[1] is not None:
        return hit[1]
    disk, age = get(name, ttl)
    if disk is not None:                     # a sibling already built it
        mem[key] = (now - age, disk)
        return disk
    flight = ("_bs_flight", name)
    if not mem.get(flight) and claim(name):
        mem[flight] = (now, True)

        def _bg():
            try:
                val = build()
                if val is not None:
                    mem[key] = (time.time(), val)
                    put(name, val)
            except Exception as e:
                errlog.note(note_id or f"BOARD-build-{name}"[:60], e)
            finally:
                mem.pop(flight, None)
                release(name)
        threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None


# ---- Is the owner's PC up? --------------------------------------------------
# pc_loop pings /api/pc/ping every 60s and the server notes it in this file
# (the PC only ever calls IN, so "is it up" is "how recently did it call").
# Read here so modules that decide whether to spend the shared core -- the
# preset builder -- can ask without importing the app.
PC_STATUS_PATH = os.path.join(os.environ.get("VIGIL_SIM_CACHE_DIR")
                              or os.environ.get("DEEP_CACHE_DIR") or "/tmp",
                              "pc-status.json")


def pc_status(path=None):
    """green / yellow / red for the header light. 'on' needs a heartbeat in
    the last 5 minutes (five misses is a verdict, one slow deep-sim cycle is
    not); 'behind' means alive but running older code than this server (it
    self-updates within a minute, so persistent yellow means the PC's git
    pull is failing); 'off' is everything else."""
    try:
        with open(path or PC_STATUS_PATH) as fh:
            st = json.load(fh)
    except OSError:
        return {"state": "off", "seen_s": None, "behind": None}
    except Exception as e:
        errlog.note("BS-pc-status", e)
        return {"state": "off", "seen_s": None, "behind": None}
    seen = time.time() - (st.get("ts") or 0)
    srv = os.environ.get("RENDER_GIT_COMMIT", "")
    pc = st.get("commit") or ""
    behind = bool(srv and pc
                  and not (pc.startswith(srv) or srv.startswith(pc)))
    state = "off" if seen > 300 else ("behind" if behind else "on")
    return {"state": state, "seen_s": round(seen), "behind": behind}
