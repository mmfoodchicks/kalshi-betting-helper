"""The registered read: may Adaptive become the default eligibility mode for the NFL showdown boards?
(research/data/dfs_eligibility/prereg.json; written and committed before any prospective slate was recorded.)

    python3 research/dfs_eligibility_read.py          # prints the verdict; --write saves read.json

Reads research/data/dfs_eligibility/prospective/compare_<dg>.json -- one per slate, each written by
research/dfs_eligibility_compare.py on a recording research/dfs_eligibility_record.py made BEFORE that slate's lock
-- and applies the rule exactly as prereg.json states it. Nothing here is tunable.
"""
import glob
import json
import math
import os
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(_ROOT, "research", "data", "dfs_eligibility")
PREREG = os.path.join(DATA, "prereg.json")
PROSPECTIVE = os.path.join(DATA, "prospective")


def _mean_se(xs):
    n = len(xs)
    m = sum(xs) / n
    if n < 2:
        return m, float("inf")
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, sd / math.sqrt(n)


def eligible(c, reg):
    """A slate is in the sample when its recording was made before its lock, its lock is after the registration,
    and it is not a development slate."""
    meta = c.get("meta") or {}
    return (meta.get("recorded_before_lock") is True and meta.get("recorded_ts") is not None
            and meta.get("lock_ts") is not None and meta["recorded_ts"] < meta["lock_ts"]
            and meta["lock_ts"] > reg["registered_ts"] and int(meta.get("dg") or 0) not in reg["excluded_dg"])


def read(files=None, reg=None, now_ts=None):
    reg = reg or json.load(open(PREREG))
    files = sorted(glob.glob(os.path.join(PROSPECTIVE, "compare_*.json"))) if files is None else files
    slates = []
    for f in files:
        c = json.load(open(f)) if isinstance(f, str) else f
        if not eligible(c, reg):
            continue
        a = c["summary"]["adaptive"]
        slates.append({"dg": c["meta"]["dg"], "lock_ts": c["meta"]["lock_ts"],
                       "d": a["p20_vs_conservative"]["top1"][0], "cash": a["p20_vs_conservative"]["cash"][0],
                       "d1": a["best_vs_conservative"]["top1"][0], "admitted": len(c["eligibility"]["adaptive"]["admitted"])})
    slates.sort(key=lambda s: s["lock_ts"])
    slates = slates[:reg["n_target"]]
    n = len(slates)
    out = {"n": n, "slates": slates, "rule": reg["primary"]}
    late = (now_ts or 0) >= reg["last_read_ts"]
    if n < reg["n_target"] and not late:
        out["verdict"] = "NOT READY"                  # one read: the 12th slate, or the last date -- never a peek
        return out
    if n < reg["n_min"]:
        out["verdict"] = "INCONCLUSIVE"
        return out
    D, se = _mean_se([s["d"] for s in slates])
    C, cse = _mean_se([s["cash"] for s in slates])
    D1, d1se = _mean_se([s["d1"] for s in slates])
    primary = D > 0 and D > 2 * se
    cash_ok = C + 2 * cse >= 0
    best_ok = D1 + 2 * d1se >= 0
    out.update(D=D, se=se, cash=C, cash_se=cse, d1=D1, d1_se=d1se, primary=primary, cash_ok=cash_ok, best_ok=best_ok,
               verdict=("PASS: adaptive MAY become the default with the owner's sign-off" if primary and cash_ok
                        and best_ok else "FAIL: conservative stays the default"))
    return out


if __name__ == "__main__":
    import time
    got = read(now_ts=time.time())
    print(json.dumps(got, indent=1))
    if "--write" in sys.argv:
        with open(os.path.join(DATA, "read.json"), "w") as fh:
            json.dump(got, fh, indent=1, sort_keys=True)
