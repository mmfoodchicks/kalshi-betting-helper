"""Persistent cache + nightly scheduler for the heavy season simulations.

The deep MLB run (and the F1 / NASCAR sims) are expensive, so we compute them
ONCE, persist the result to disk, and serve the same artifact to every user.
A background scheduler re-runs each job once per day (shortly after local
midnight) so every morning reflects the latest rosters, standings and IL moves;
the result also survives process restarts by reloading from disk on boot. A
manual rerun (owner-triggered) forces a fresh run on demand.

Note on hosting: disk persistence survives restarts within a host's lifetime.
If the host is fully reclaimed (ephemeral containers), point CACHE_DIR at durable
storage or have an external cron hit the manual-rerun endpoint nightly — that's
the only way to guarantee the nightly run when the box can sleep.
"""

import os
import pickle
import threading
import time
import datetime as _dt

CACHE_DIR = os.environ.get("DEEP_CACHE_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "deep")
WEEK = 7 * 86400
DAY = 86400

_lock = threading.Lock()

# ONE heavy out-of-process build at a time, across the whole app.
#
# The expensive builds -- the MLB slate and the tennis Elo pools -- each isolate
# themselves in a child so their allocation dies with it. That is the right shape
# on its own and the wrong shape in parallel: three callers hitting the slate at
# once produced six concurrent child builds and 547 MB, which is how a 512 MB
# instance dies while every individual piece looks affordable. Callers that
# cannot get the gate wait for the one in flight rather than starting another.
HEAVY_BUILD = threading.Semaphore(
    max(1, int(os.environ.get("VIGIL_HEAVY_BUILDS") or 1)))


def _last_midnight_epoch():
    """Epoch of the most recent LOCAL midnight — the boundary a nightly job must
    have run after to count as fresh for today."""
    import clock
    return clock.midnight_et_epoch()


def _path(key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(key))
    return os.path.join(CACHE_DIR, f"{safe}.pkl")


def save(key, payload):
    """Persist a result with the wall-clock time it was generated."""
    rec = {"generated_at": time.time(), "payload": payload}
    tmp = _path(key) + ".tmp"
    with _lock:
        with open(tmp, "wb") as f:
            pickle.dump(rec, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, _path(key))
    return rec["generated_at"]


def load(key, max_age=None):
    """Return (payload, generated_at) or (None, None). If max_age (seconds) is set
    and the cache is older, still returns it — staleness is decided by callers."""
    p = _path(key)
    if not os.path.exists(p):
        return None, None
    try:
        with _lock, open(p, "rb") as f:
            rec = pickle.load(f)
        if max_age is not None and time.time() - rec["generated_at"] > max_age:
            return rec["payload"], rec["generated_at"]   # stale but usable
        return rec["payload"], rec["generated_at"]
    except Exception:
        return None, None


def age(key):
    """Seconds since this key was generated, or None if absent."""
    _, ts = load(key)
    return (time.time() - ts) if ts else None


def is_stale(key, max_age=WEEK):
    a = age(key)
    return a is None or a > max_age


def is_stale_daily(key):
    """True if this job hasn't run since the most recent local midnight (or ever)."""
    _, ts = load(key)
    return ts is None or ts < _last_midnight_epoch()


# ---- Nightly scheduler -----------------------------------------------------
# Each registered job: key -> {"run": callable(), "cadence": "daily"|"age",
#                              "max_age": seconds, "running": bool}
_jobs = {}
_sched_started = False


def register(key, run, max_age=DAY, cadence="daily"):
    """Register a schedulable job. cadence="daily" reruns once per day just after
    local midnight; cadence="age" reruns when older than max_age seconds."""
    _jobs[key] = {"run": run, "max_age": max_age, "cadence": cadence, "running": False}


def _job_stale(key, job):
    if job.get("cadence") == "daily":
        return is_stale_daily(key)
    return is_stale(key, job["max_age"])


def run_job(key, force=False):
    """Run one registered job in a background thread if it's stale (or forced).
    Returns True if a run was started."""
    job = _jobs.get(key)
    if not job or job["running"]:
        return False
    if not enabled() and not force:
        return False
    if not force and not _job_stale(key, job):
        return False

    def _worker():
        job["running"] = True
        try:
            payload = job["run"]()
            if payload is not None:
                save(key, payload)
        except Exception:
            pass
        finally:
            job["running"] = False
    threading.Thread(target=_worker, daemon=True).start()
    return True


def run_sync(key):
    """Run a registered job inline (for cheap sims) and persist it. Returns
    (payload, generated_at)."""
    job = _jobs.get(key)
    if not job:
        return None, None
    payload = job["run"]()
    ts = save(key, payload) if payload is not None else None
    return payload, ts


def status(key):
    job = _jobs.get(key) or {}
    a = age(key)
    cadence = job.get("cadence", "daily")
    return {"age_sec": a, "running": bool(job.get("running")),
            "max_age": job.get("max_age", DAY), "cadence": cadence,
            # True once the cache predates today's midnight -> the scheduler will
            # rerun it on its next pass; the UI uses this to know a refresh is due.
            "stale": (is_stale_daily(key) if cadence == "daily"
                      else is_stale(key, job.get("max_age", DAY)))}


def enabled():
    """Heavy sims can be switched off entirely without a code change -- the kill
    switch you want when an instance is thrashing and you need it serving."""
    return (os.environ.get("VIGIL_SIMS", "on") or "on").lower() not in (
        "0", "off", "false", "no")


# Seconds of quiet after boot before ANY heavy job starts. A fresh instance has an
# empty cache, so every registered job is stale at once; starting them while the
# platform is still deciding whether the app is healthy is how a deploy ends up in
# a restart loop.
STARTUP_GRACE = int(os.environ.get("VIGIL_SIM_STARTUP_GRACE") or 300)


def wait_for(key, timeout=None):
    """Block until a running job finishes. Returns True if it is idle."""
    job = _jobs.get(key)
    if not job:
        return True
    start = time.time()
    while job.get("running"):
        if timeout and (time.time() - start) > timeout:
            return False
        time.sleep(5)
    return True


def start_scheduler(check_every=1800):
    """Background loop: after a startup grace period, and every `check_every`
    seconds (default 30 min, so the nightly rerun fires within half an hour of
    local midnight), run any stale registered job -- ONE AT A TIME.

    Sequential is the whole point. run_job() only SPAWNS a thread, so the old
    30-second "stagger" waited for a job to start, not to finish: on a fresh
    instance every job is stale, and within a few minutes ten 4,000-iteration
    sims were running at once, each forking its own process pool. That is
    survivable on a workstation and fatal on a small container -- the health
    check times out, the platform restarts the instance, the cache is empty
    again, and it repeats forever. Waiting for each job keeps peak load to one
    sim no matter how many are registered."""
    global _sched_started
    if _sched_started:
        return
    _sched_started = True

    def _loop():
        time.sleep(STARTUP_GRACE)              # let the app get healthy first
        while True:
            for key in list(_jobs):
                if not enabled():
                    break
                if run_job(key):
                    wait_for(key)              # finish this one before the next
                time.sleep(2)
            time.sleep(check_every)
    threading.Thread(target=_loop, daemon=True).start()
