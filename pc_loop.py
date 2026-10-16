"""Vigil PC worker loop — fast update checks, measured sim cadence.

The .bat bootstrap runs this in a restart loop and NEVER changes again (a
batch file that git-pulls itself is a corruption hazard: cmd reads .bat files
by byte offset, so rewriting one mid-run can garble the running copy). All
looping logic lives here, where an update is applied safely: pull, reinstall
deps if they changed, and EXIT — the .bat restarts this file, which is now
the new version.

Two speeds:
  - every CHECK_S (60s): `git fetch` + hash compare. An update pulls,
    reinstalls, exits for restart, and the fresh loop runs a sim cycle
    immediately — so the PC is current within about a minute of a push,
    ready to backfill the server the moment its deploy swaps.
  - every CYCLE_S (600s), or right after an update/restart: run one
    pc_worker.py cycle in a subprocess (fresh import of the fresh code).

The loop WAITS on the worker, so "within about a minute" held only between
cycles: a cycle that rebuilds every board runs for hours, and on 2026-10-16
the PC sat on older code from the morning on, each push landing before the
previous cycle ended. The worker now checks for newer code BETWEEN builds and
exits with UPDATE_WAITING when one is waiting (pc_worker._yield_point); the
loop pulls straight away. A pull that fails is reported through the
heartbeat (the light shows the error) and switches yielding off, so a broken
pull leaves the PC building on old code instead of stopping every cycle.
"""

import os
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_S = 60
CYCLE_S = 600
UPDATE_WAITING = 3              # pc_worker's exit code when it stopped for newer code
_PULL_ERR = [""]                # the last pull's failure, "" while pulls succeed


def _git(*args):
    r = subprocess.run(["git", *args], cwd=_HERE, capture_output=True, text=True)
    # a failure's reason is on stderr ("Your local changes would be overwritten
    # ..."); the heartbeat carries it to the light
    out = r.stdout if r.returncode == 0 else (r.stderr or r.stdout)
    return r.returncode, (out or "").strip()


def _update_available():
    rc, _ = _git("fetch", "-q")
    if rc != 0:
        print("[vigil-pc] git fetch failed (offline?) - carrying on with current code")
        return False
    _, local = _git("rev-parse", "HEAD")
    rc2, remote = _git("rev-parse", "@{u}")
    return rc2 == 0 and local and remote and local != remote


def _apply_update():
    print("[vigil-pc] update found - pulling")
    rc, out = _git("pull", "--ff-only")
    print(out or "(pulled)")
    if rc != 0:
        _PULL_ERR[0] = (out or "pull failed").splitlines()[0][:200]
        print("[vigil-pc] pull failed - will retry next check; building on the current code meanwhile")
        return False
    _PULL_ERR[0] = ""
    # requirements-pc.txt is the PC-only extras (numpy for the DFS
    # tournament); the server's requirements.txt stays lean on purpose.
    for fn in ("requirements.txt", "requirements-pc.txt"):
        req = os.path.join(_HERE, fn)
        if os.path.exists(req):
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req],
                           cwd=_HERE)
    return True


def _run_cycle():
    """One worker cycle; its exit code (UPDATE_WAITING when it stopped early)."""
    print(f"[vigil-pc] cycle starting ({time.strftime('%H:%M:%S')})")
    env = dict(os.environ, VIGIL_PC_YIELD="0" if _PULL_ERR[0] else "1")
    return subprocess.run([sys.executable, os.path.join(_HERE, "pc_worker.py")],
                          cwd=_HERE, env=env).returncode


def _task_header():
    """What the worker is doing now (its pc_task.json), as "label, N min in"."""
    try:
        import json
        d = os.environ.get("VIGIL_SIM_CACHE_DIR") or os.path.join(_HERE, "pc-simcache")
        with open(os.path.join(d, "pc_task.json")) as fh:
            t = json.load(fh)
        return f"{t.get('task')}, {max(0, (time.time() - float(t.get('since') or 0)) / 60):.0f} min in"
    except (OSError, ValueError, TypeError):
        return ""


def _ping_server():
    """60-second heartbeat that feeds the app's PC status light. Best-effort:
    a failed ping IS the red light doing its job, never a reason to disturb
    the loop. Sent from here rather than pc_worker because a long deep-sim
    cycle can keep the worker away from the API for an hour -- and from a
    thread of its own (_heartbeat), because this loop WAITS on the worker:
    the first 60,000-world tournament build ran 1,241s (2026-09-09) with no
    ping in between, and the light sat red while the PC was at full tilt."""
    try:
        import json
        import urllib.request
        cfg = {}
        path = os.path.join(_HERE, "vigil-pc.cfg")
        if os.path.exists(path):
            with open(path) as fh:
                cfg = json.load(fh)
        url = (os.environ.get("VIGIL_APP_URL")
               or cfg.get("app_url") or "").rstrip("/")
        tok = os.environ.get("SIM_TOKEN") or cfg.get("sim_token") or ""
        if not url or not tok:
            return
        _, commit = _git("rev-parse", "HEAD")
        ascii_ = lambda v: (v or "").encode("ascii", "replace").decode()[:200]
        req = urllib.request.Request(
            url + "/api/pc/ping",
            headers={"X-Sim-Token": tok, "X-PC-Commit": commit or "",
                     "X-PC-Task": ascii_(_task_header()), "X-PC-Pull": ascii_(_PULL_ERR[0]),
                     "User-Agent": "vigil-pc-loop"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        # Same volume as the offline fetch note above; the light is already red.
        print(f"[vigil-pc] ping failed ({type(e).__name__}) - light goes red")


def _heartbeat():
    """The ping every CHECK_S seconds, on a daemon thread, so a worker cycle
    of any length cannot silence it. Daemon: the loop's exit-to-restart
    after an update must not wait on it."""
    while True:
        time.sleep(CHECK_S)
        _ping_server()


def main():
    print(f"[vigil-pc] loop up - git check every {CHECK_S}s, sim cycle every "
          f"{CYCLE_S // 60} min (or immediately after an update)")
    _ping_server()
    threading.Thread(target=_heartbeat, daemon=True).start()
    rc = _run_cycle()                              # fresh start = fresh cycle
    last_cycle = time.time()
    while True:
        early = rc == UPDATE_WAITING
        if not early:
            time.sleep(CHECK_S)
        rc = None
        if _update_available():
            if _apply_update():
                print("[vigil-pc] restarting on the new code...")
                sys.exit(0)                   # the .bat loop restarts us fresh
        # a cycle that stopped early for an update the pull could not take
        # runs again now, on the current code with yielding off (_run_cycle)
        if early or time.time() - last_cycle >= CYCLE_S:
            rc = _run_cycle()
            last_cycle = time.time()


if __name__ == "__main__":
    main()
