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
"""

import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_S = 60
CYCLE_S = 600


def _git(*args):
    r = subprocess.run(["git", *args], cwd=_HERE, capture_output=True, text=True)
    return r.returncode, (r.stdout or "").strip()


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
        print("[vigil-pc] pull failed - will retry next check")
        return False
    req = os.path.join(_HERE, "requirements.txt")
    if os.path.exists(req):
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req],
                       cwd=_HERE)
    return True


def _run_cycle():
    print(f"[vigil-pc] cycle starting ({time.strftime('%H:%M:%S')})")
    subprocess.run([sys.executable, os.path.join(_HERE, "pc_worker.py")], cwd=_HERE)


def _ping_server():
    """60-second heartbeat that feeds the app's PC status light. Best-effort:
    a failed ping IS the red light doing its job, never a reason to disturb
    the loop. Sent from here rather than pc_worker because a long deep-sim
    cycle can keep the worker away from the API for an hour."""
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
        req = urllib.request.Request(
            url + "/api/pc/ping",
            headers={"X-Sim-Token": tok, "X-PC-Commit": commit or "",
                     "User-Agent": "vigil-pc-loop"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        # Same volume as the offline fetch note above; the light is already red.
        print(f"[vigil-pc] ping failed ({type(e).__name__}) - light goes red")


def main():
    print(f"[vigil-pc] loop up - git check every {CHECK_S}s, sim cycle every "
          f"{CYCLE_S // 60} min (or immediately after an update)")
    _ping_server()
    _run_cycle()                              # fresh start = fresh cycle
    last_cycle = time.time()
    while True:
        time.sleep(CHECK_S)
        _ping_server()
        if _update_available():
            if _apply_update():
                print("[vigil-pc] restarting on the new code...")
                sys.exit(0)                   # the .bat loop restarts us fresh
        if time.time() - last_cycle >= CYCLE_S:
            _run_cycle()
            last_cycle = time.time()


if __name__ == "__main__":
    main()
