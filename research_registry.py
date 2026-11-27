"""Every registered test in one place: what it decides, when it may be read, and where it stands (2026-11-27).

The owner's backlog item 9 (a research dashboard). By late November the project held a dozen pre-registered
reads -- the NFL deep weight's, the coaching engine's R1 / confirmatory / DFS / R2 reads, the classic field
protocol, the accuracy batch's dated reads, the market anchor -- each in its own report, each with its own rule
for when it may be opened. This module is the one list. The Research tab reads it (`/api/research/registry`).

An entry names its registration (a committed file, pinned by the guard), the read script when there is one,
the earliest moment it may be read (`not_before`, UTC; for a file that states one, the file's own figure is
read and wins), what else it waits for (`waits_for`, plain words: graded games, the owner's exports), and the
file a finished read leaves (`result`). The status is computed, never typed:

  read          the result file is committed (its verdict is in the report named)
  sealed        not before `not_before`
  waiting       past its date (or undated) but waiting on data the server cannot count
  readable      past its date with nothing else named -- run its read script, once, from a clean tree

Nothing here reads a sealed row or runs a read. Dates are UTC; the tab shows them in Mountain time.
"""
import datetime
import json
import os

import errlog

_HERE = os.path.dirname(os.path.abspath(__file__))

# The entries live in research/data/research_registry.json (kind "registered": a hashed rule, read once; "dated":
# a read the reports fixed a date or a count for). Data, not code, for a reason: the coaching engine's guards
# fail any production module whose source names its files, and this list must name every registration.
_REG_PATH = os.path.join(_HERE, "research", "data", "research_registry.json")


def _load():
    try:
        with open(_REG_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        errlog.note("RREG-load", e)
        return []


REGISTRY = _load()

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, _ISO).replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return datetime.datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp()


def _file_not_before(path):
    """A registration file's own not-before date, when it states one at its top level (it wins)."""
    if not path or not path.endswith(".json"):
        return None
    try:
        with open(os.path.join(_HERE, path)) as fh:
            d = json.load(fh)
        v = d.get("not_before_utc") if isinstance(d, dict) else None
        return v if isinstance(v, str) else None
    except (OSError, ValueError) as e:
        errlog.note("RREG-read", e, path=path)
        return None


def status(entry, now=None):
    """The entry with its computed status: read / sealed / waiting / readable (and `due_soon`)."""
    now = datetime.datetime.now(datetime.timezone.utc).timestamp() if now is None else float(now)
    e = dict(entry)
    nb = _file_not_before(e.get("registration")) or e.get("not_before")
    e["not_before"] = nb
    t = _ts(nb)
    res = e.get("result")
    done = bool(res) and os.path.exists(os.path.join(_HERE, res))
    if done:
        e["status"] = "read"
    elif t is not None and now < t:
        e["status"] = "sealed"
    elif e.get("waits_for"):
        e["status"] = "waiting"
    else:
        e["status"] = "readable"
    due = _ts(e.get("due_by"))
    e["due_soon"] = bool(due is not None and not done and now <= due <= now + 14 * 86400)
    e["overdue"] = bool(due is not None and not done and now > due)
    e["registration_exists"] = os.path.exists(os.path.join(_HERE, e.get("registration") or ""))
    return e


def listing(now=None):
    rows = [status(e, now) for e in REGISTRY]
    order = {"readable": 0, "waiting": 1, "sealed": 2, "read": 3}
    rows.sort(key=lambda r: (not r["due_soon"] and not r["overdue"], order.get(r["status"], 9),
                             _ts(r.get("not_before")) or 0))
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"entries": rows, "counts": counts,
            "generated": datetime.datetime.now(datetime.timezone.utc).strftime(_ISO)}
