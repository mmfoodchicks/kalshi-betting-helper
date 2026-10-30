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

# Kept a week, not a day: the tournament boards are built once or twice a
# slate (the classic one is the better part of an hour of the PC) and read
# until it locks, and the tab's Build queue (and the PC's report on a
# request it could not build) ages out its own entries (three days). The
# day-old sweep deleted week 7's Millionaire board, built Thursday
# 2026-10-22, by Friday night, so the tab said "not built yet" all weekend.
# About 1-5 MB a board, a handful a week.
# The game viewer's pieces (nfl_viewer) too: built up to a week ahead, served
# to kickoff + 4 h, and a game-day build that fails must not leave a hole.
_KEEP_LONG = ("cl_tourney_", "sd_tourney_", "tourney_requests.", "tourney_attempts.", "nflview_")
_KEEP_LONG_S = 8 * 86400


def _path(name, ext):
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(name))
    return os.path.join(_DIR, f"{safe}.{ext}")


# ---- The PC builds; the server serves ---------------------------------------
# The owner (2026-10-29): "the website should only be housing the data the pc
# [builds] and takes over as a failsafe if the pc is off". The PC runs every
# board builder the server serves (pc_worker's boards loop, every few minutes)
# and uploads each one, but the server still rebuilt any board the moment its
# copy passed the board's TTL -- inside whichever web worker the request landed
# on, in a thread -- because the PC's cycle can sit for 13-45 minutes on one
# tournament build. Those in-process builds cost a worker tens to ~175 MB each
# (measured one per fresh process: CFB 175, tennis 130, NFL DFS 111, soccer 83)
# and the worker keeps most of it until it recycles. While the PC is on, a copy
# on disk younger than PC_GRACE_S is now served as is; the server builds only
# when the PC is off or its copy has aged past the grace (a PC that is up but
# stuck still gets covered). The PC's own process never sees itself "on" (it
# writes no pc-status file), so it keeps building on every board's own TTL.
PC_GRACE_S = 1800
# The PC rebuilds a board once ITS copy passes the board's TTL, and the fresh
# upload lands a pass period, a build and an upload later (up to ~8 min). The
# upload keeps the PC's age (X-Art-Age), so at max(ttl, PC_GRACE_S) the four
# TTL-1800 boards -- NFL, DFS, CFB, futures, the heaviest builds -- had no
# slack: past 1,800 s every request built one here until the PC's copy landed,
# about twice an hour in season (Fable's review r3). The slack covers that gap.
PC_SLACK_S = 720


def pc_grace(ttl):
    """How old a disk copy may be and still be served instead of built here:
    the board's own TTL; while the PC is on, its TTL plus PC_SLACK_S, and never
    under PC_GRACE_S."""
    if pc_status().get("state") in ("on", "behind"):
        return max(ttl + PC_SLACK_S, PC_GRACE_S)
    return ttl


def mtime(name):
    try:
        return os.stat(_path(name, "pkl")).st_mtime
    except OSError:
        return None


def names(prefix):
    """[(name, mtime)] of the published boards whose (safe) name starts with
    `prefix` -- a directory listing, nothing unpickled."""
    out = []
    try:
        for fn in os.listdir(_DIR):
            if fn.startswith(prefix) and fn.endswith(".pkl"):
                try:
                    out.append((fn[:-4], os.stat(os.path.join(_DIR, fn)).st_mtime))
                except OSError:
                    continue                # swept between the listing and the stat
    except OSError:
        return []
    return sorted(out)


def held(name, ts, max_age):
    """True when the disk copy is the one already in memory (its file time within
    2 s of `ts`, which adopting a disk copy stamps as now - age) and is no older
    than max_age: serve memory instead of unpickling the same board on every
    request past the TTL."""
    mt = mtime(name)
    return mt is not None and abs(mt - ts) < 2 and time.time() - mt <= max_age


def _short_lived(val):
    """A placeholder or an empty board ("build failed, retried shortly", "no
    games this week"): it keeps its own short life, never the PC's grace --
    matchups' failed-build placeholder says "tried again within two minutes"
    and would otherwise have read on for up to 17 (Fable's review r2)."""
    return isinstance(val, dict) and bool(val.get("placeholder") or val.get("empty"))


def adopt(name, mem, key, ttl):
    """The board to serve WITHOUT building, or None when it is this server's to
    build: memory younger than `ttl`; else the disk copy (a sibling's build or
    the PC's upload) younger than pc_grace(ttl) -- a placeholder or an empty
    board only within its own `ttl`. `mem` is the module's own {key: (ts, val)}
    cache."""
    now = time.time()
    hit = mem.get(key)
    if hit and hit[1] is not None and now - hit[0] < ttl:
        return hit[1]
    grace = pc_grace(ttl)
    if hit and hit[1] is not None and not _short_lived(hit[1]) and held(name, hit[0], grace):
        return hit[1]
    disk, age = get(name, grace)
    if disk is None or (_short_lived(disk) and age >= ttl):
        return None
    mem[key] = (now - age, disk)
    return disk


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
        # Boards a day old are dead weight on a 1 GB disk -- but not the
        # tournament boards or the tab's Build queue (_KEEP_LONG).
        cutoff = time.time() - 86400
        long_cutoff = time.time() - _KEEP_LONG_S
        for f in os.listdir(_DIR):
            fp = os.path.join(_DIR, f)
            try:
                if os.stat(fp).st_mtime < (long_cutoff if f.startswith(_KEEP_LONG) else cutoff):
                    os.remove(fp)
            except OSError:
                pass
    except Exception as e:
        errlog.note("BOARD-write", e, path=str(name))


# The claims THIS process holds: its builds in flight. A PC boards pass is a
# process that exits after its upload, and its builders run in daemon threads
# that die with it, so it waits on these first (pc_worker.boards_main).
_HELD = set()


def held_claims():
    """The boards this process has claimed and not yet released."""
    return set(_HELD)


def claim(name):
    """True in the ONE process that should build this board right now. A claim
    left by a dead builder is taken over once it is CLAIM_TIMEOUT old."""
    try:
        os.makedirs(_DIR, exist_ok=True)
        p = _path(name, "lock")
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            _HELD.add(name)
            return True
        except FileExistsError:
            if time.time() - os.stat(p).st_mtime > CLAIM_TIMEOUT:
                os.utime(p, None)
                _HELD.add(name)
                return True
            return False
    except Exception as e:
        errlog.note("BOARD-claim", e, path=str(name))
        _HELD.add(name)
        return True                 # cannot coordinate -> single-worker behaviour


def release(name):
    _HELD.discard(name)
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
    got = adopt(name, mem, key, ttl)         # fresh here, a sibling's, or the PC's
    if got is not None:
        return got
    hit = mem.get(key)
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
    not); 'behind' means alive but running older code than this server. That
    is either a build in progress -- the PC updates between builds, never in
    the middle of one (`task` says which, and for how long) -- or a pull that
    fails (`pull_error` carries git's reason); 'off' is everything else.
    `commit` is the PC's checkout, `server_commit` this server's."""
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
    return {"state": state, "seen_s": round(seen), "behind": behind,
            "commit": pc[:7] or None, "server_commit": srv[:7] or None,
            "task": st.get("task") or None, "pull_error": st.get("pull") or None}
