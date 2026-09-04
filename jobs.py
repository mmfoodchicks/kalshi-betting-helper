"""Heavy-job timeline: WHO was burning the shared core, and WHEN.

A health probe starved by CPU restarts the instance as "failed" and leaves
the error ledger empty -- nothing threw, something was merely busy. Two such
restarts on Sep 4 (15:16, 17:20) could not be attributed to anything. So
every heavy job records its start and end here -- the slate child, the
tennis Elo pools, the preset rebuild, the NFL board, every combo build --
and the error-log export carries the last _KEEP rows, so a restart can be
laid against the jobs in flight at that minute without touching the server.

One JSON file on the data disk, shared by all workers (atomic replace; a
simultaneous append from two workers can drop a row, which is acceptable
for a diagnostic). Best-effort throughout: timing a job must never fail it.
"""

import json
import os
import tempfile
import threading
import time

import errlog

PATH = os.path.join(os.environ.get("VIGIL_SIM_CACHE_DIR")
                    or os.environ.get("DEEP_CACHE_DIR") or "/tmp",
                    "jobs-timeline.json")
_KEEP = 200
_lock = threading.Lock()


def _read():
    try:
        with open(PATH) as fh:
            return json.load(fh)
    except OSError:
        return []
    except Exception as e:
        errlog.note("JOBS-read", e)
        return []


def _write(rows):
    try:
        d = os.path.dirname(PATH)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(rows[-_KEEP:], fh)
        os.replace(tmp, PATH)
    except Exception as e:
        errlog.note("JOBS-write", e)


def note(name, phase, started, ended=None, err=None):
    with _lock:
        rows = _read()
        rows.append({"job": name, "phase": phase, "start": round(started, 1),
                     "end": round(ended, 1) if ended else None,
                     "dur_s": round(ended - started, 1) if ended else None,
                     "pid": os.getpid(), "err": err})
        _write(rows)


class timed:
    """with jobs.timed("slate:2026-09-04"): ...  -- a start row at entry, an
    end row (with duration and any exception) at exit. Never raises."""

    def __init__(self, name):
        self.name = str(name)[:80]
        self.t0 = None

    def __enter__(self):
        self.t0 = time.time()
        try:
            note(self.name, "start", self.t0)
        except Exception as e:
            errlog.note("JOBS-enter", e)
        return self

    def __exit__(self, et, ev, tb):
        try:
            note(self.name, "end", self.t0, time.time(),
                 err=(repr(ev)[:200] if ev else None))
        except Exception as e:
            errlog.note("JOBS-exit", e)
        return False


def recent(n=150):
    return _read()[-n:]
