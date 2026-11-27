"""Guards for the research dashboard (2026-11-27; research_registry.py, /api/research/registry, the Research tab).

    python3 tests/guards_auto_registry.py      # exits 1 on any failed check
"""
import datetime
import glob
import os
import re
import sys

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)


def _utc(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()


def run(ck):
    import research_registry as R
    reg = {e["registration"] for e in R.REGISTRY}
    files = sorted(os.path.relpath(p, _ROOT) for p in glob.glob(os.path.join(_ROOT, "research", "data", "**", "*.json"),
                                                                 recursive=True)
                   if re.match(r"(prereg.*|protocol)\.json$", os.path.basename(p))
                   and "manifest" not in os.path.basename(p))      # a registration's file manifest is part of it
    missing = [f for f in files if f not in reg]
    refs = [p for e in R.REGISTRY for p in (e.get("registration"), e.get("script"), e.get("report")) if p]
    gone = [p for p in refs if not os.path.exists(os.path.join(_ROOT, p))]
    ids = [e["id"] for e in R.REGISTRY]
    ck("RG1 every registration file committed under research/data (prereg*.json, protocol.json) is on the research "
       "dashboard, every rule, read script and report an entry names exists, and the ids are unique -- a new "
       "registration that is not listed fails the build",
       not missing and not gone and len(ids) == len(set(ids)) and all(e.get("title") and e.get("decides")
                                                                       for e in R.REGISTRY),
       f"unlisted {missing}; missing {gone}")
    r1 = next(e for e in R.REGISTRY if e["id"] == "coach_r1")
    before = R.status(r1, now=_utc("2027-01-05T00:00:00Z"))
    after = R.status(r1, now=_utc("2027-01-07T00:00:00Z"))
    # C3 was read on 2026-11-27 (a read entry is never due); its due date still exercises the flag unread
    c3 = dict(next(e for e in R.REGISTRY if e["id"] == "acc_c3"), result=None)
    c3_soon = R.status(c3, now=_utc("2026-11-27T00:00:00Z"))
    c3_late = R.status(c3, now=_utc("2026-12-07T00:00:00Z"))
    rush = R.status(next(e for e in R.REGISTRY if e["id"] == "nfl_rush"))
    fake = R.status({"id": "x", "registration": "research/data/nfl_coach/prereg_dfs.json", "not_before": "2020-01-01T00:00:00Z",
                     "result": None, "waits_for": None}, now=_utc("2026-12-01T00:00:00Z"))
    ck("RG2 the status is computed, never typed: sealed before its date, readable or waiting after it, read once its "
       "result file is committed; a date a registration file states itself wins over the list's; a due date "
       "within 14 days is flagged and a missed one is overdue",
       before["status"] == "sealed" and after["status"] == "waiting" and rush["status"] == "read"
       and c3_soon["due_soon"] and not c3_soon["overdue"] and c3_late["overdue"]
       and fake["not_before"] == "2027-01-06T06:00:00Z" and fake["status"] == "sealed",
       f"{before['status']} {after['status']} {rush['status']} {fake['not_before']}")
    import app as A
    c = A.app.test_client()
    saved = A.app.config.get("TESTING")
    got = c.get("/api/research/registry").get_json() or {}
    A.app.config["TESTING"] = saved
    html = open(os.path.join(_ROOT, "templates", "index.html")).read()
    js = open(os.path.join(_ROOT, "static", "research.js")).read()
    appjs = open(os.path.join(_ROOT, "static", "app.js")).read()
    ck("RG3 the Research tab reads /api/research/registry (every entry, counted by status), shows dates in Mountain "
       "time, escapes every string it prints, and cannot run a read",
       len(got.get("entries") or []) == len(R.REGISTRY) and sum((got.get("counts") or {}).values()) == len(R.REGISTRY)
       and 'data-tab="research"' in html and 'id="tab-research"' in html and "/static/research.js" in html
       and 'if (tab === "research" && typeof initResearch === "function") initResearch();' in appjs
       and "America/Denver" in js and "function h(x)" in js and "innerHTML" in js and "POST" not in js
       and "onclick" not in js, str(got.get("counts")))


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
