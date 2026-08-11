"""Optional self-updater: make a git push go live on a long-running server with
no manual pull + restart. Opt in with VIGIL_SELFUPDATE=1.

Every `interval` seconds it fetches the tracked branch; when new commits land it
hard-resets to them and re-execs the process, so the next page refresh serves
the new code. The DB and caches are gitignored, so the reset only advances the
tracked source — nothing you care about is lost.

No-ops safely wherever it can't apply — no opt-in, no git binary, or no .git
checkout (e.g. Render's Docker container, which redeploys via its own autoDeploy
on push instead). Intended for a home machine / VPS run (python app.py or
start_remote.py), which is exactly the "I'm not home to update it" case.
"""
import os
import subprocess
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.abspath(__file__))
_started = False


def _git(*args, timeout=90):
    return subprocess.run(["git", "-C", _ROOT, *args],
                          capture_output=True, text=True, timeout=timeout)


def _branch():
    r = _git("rev-parse", "--abbrev-ref", "HEAD")
    b = r.stdout.strip() if r.returncode == 0 else ""
    return b if b and b != "HEAD" else None


def _pull_if_new(branch):
    """Hard-reset to origin/branch and return True when it's ahead of HEAD."""
    if _git("fetch", "--quiet", "origin", branch).returncode != 0:
        return False
    local = _git("rev-parse", "HEAD").stdout.strip()
    remote = _git("rev-parse", f"origin/{branch}").stdout.strip()
    if not remote or not local or local == remote:
        return False
    return _git("reset", "--hard", f"origin/{branch}").returncode == 0


def _loop(branch, interval, log):
    while True:
        time.sleep(interval)
        try:
            if _pull_if_new(branch):
                log(f"[selfupdate] pulled origin/{branch} - restarting to apply")
                # Replace this process image with the freshly-pulled code.
                os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            log(f"[selfupdate] check failed (will retry): {e!r}")


def start(interval=120, log=print):
    """Begin polling if VIGIL_SELFUPDATE is set and this is a git checkout with a
    git binary. Returns the watched branch, or None if inactive."""
    global _started
    if _started:
        return None
    if os.environ.get("VIGIL_SELFUPDATE", "").lower() not in ("1", "true", "yes", "on"):
        return None
    if not os.path.isdir(os.path.join(_ROOT, ".git")):
        return None
    try:
        if _git("--version").returncode != 0:
            return None
    except Exception:
        return None
    branch = os.environ.get("VIGIL_SELFUPDATE_BRANCH") or _branch()
    if not branch:
        return None
    try:
        interval = max(30, int(os.environ.get("VIGIL_SELFUPDATE_INTERVAL", interval)))
    except ValueError:
        pass
    _started = True
    threading.Thread(target=_loop, args=(branch, interval, log), daemon=True).start()
    log(f"[selfupdate] watching origin/{branch} every {interval}s")
    return branch
