"""Should the classic field model's constants move to what real fields play?
RESEARCH; the rule below was written 2026-10-18 13:55 UTC, before any
standings export after week 1 had been read (the intake, dk_standings, ships
with it; week 1's Millionaire is the only classic export read so far, by
research/cl_field.py).

The classic sampler (dfs_tourney.classic_sample) is steered by constants about
the public: the most-owned player's share (CL_FIELD_MAX_OWN), the stack mix
(CL_STACK_DIST), the bring-back rate (CL_BRING_BACK), the salary spent
(CL_SALARY_USED), the defense-against-own-quarterback share (CL_DST_VS_OWN_QB).
Week 1 read several of them off (cl_field.json: defense vs own QB 0.5%
against 8%, bring-back 32% against 35%, salary $49,850 against $49,400), but
one contest is one slate. This decides each constant on contests not yet
played.

THE RULE (written before any standings export after week 1 had been read)
  Contests: Sunday main-slate classic GPPs with 50,000+ entries, from 2026
  week 7 on, summarized by dk_standings (its definitions are cl_field's,
  reproduced exactly on week 1), in order of start time; at most one per
  week (the largest).
  Real value of each constant per contest: max_own = the top any-slot
  ownership / 100; stack_dist = the four stack shares / 100; bring_back,
  dst_vs_own_qb and salary_used likewise from the summary's shape.
  1. FIT on the first three: each constant's refit value is the mean of the
     three real values (stack_dist component by component).
  2. TEST on the next two: a constant ships at its refit value only if, on
     BOTH test contests, the refit value is closer to the real value than the
     current constant is (stack_dist by the sum of absolute errors over its
     four shares).
  3. Fewer than five such contests by the end of the 2026 regular season:
     nothing ships; the question carries to 2027 by a rule written then.
  CL_FIELD_COLLISION is reported, not fitted (the sampler reports it; the
  sampler's own collision is what a change to the others moves).
  What a shipped constant changes: its value in dfs_tourney, and the field
  every classic tournament board samples. A board built after the change says
  which constants it ran with (the board's stamp already carries them).

Usage: python3 -m research.field_refit [--store DIR]
  (reads dk_field_index and dk_field_<cid> from the deep store). Writes
  research/data/field_refit.json.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

FIRST_WEEK_START = "2026-10-25"      # 2026 week 7's Sunday; a Sunday main slate starts on or after it
MIN_ENTRIES = 50000
N_FIT, N_TEST = 3, 2
SCALARS = ("max_own", "bring_back", "dst_vs_own_qb", "salary_used")


def real_values(summ):
    """The constants' real values in one summary, under the model's units."""
    sh = summ.get("shape") or {}
    own = summ.get("ownership") or [{}]
    return {"max_own": (own[0].get("pct") or 0.0) / 100.0,
            "stack_dist": [x / 100.0 for x in sh.get("stack_pct") or [0, 0, 0, 0]],
            "bring_back": (sh.get("bring_back_pct") or 0.0) / 100.0,
            "dst_vs_own_qb": (sh.get("dst_vs_own_qb_pct") or 0.0) / 100.0,
            "salary_used": float(sh.get("salary_mean") or 0.0)}


def eligible(index_rows):
    """The contests the rule reads, in start order, the largest per week."""
    rows = [r for r in index_rows if r.get("kind") == "classic" and (r.get("entries") or 0) >= MIN_ENTRIES
            and str(r.get("starts") or "") >= FIRST_WEEK_START]
    by_day = {}
    for r in rows:
        day = str(r.get("starts"))[:10]
        if day not in by_day or r["entries"] > by_day[day]["entries"]:
            by_day[day] = r
    return [by_day[d] for d in sorted(by_day)]


def _err(name, a, b):
    if name == "stack_dist":
        return sum(abs(x - y) for x, y in zip(a, b))
    return abs(a - b)


def decide(fit, test, current):
    """{constant: {"refit", "current", "test_errors", "ship"}} by the rule."""
    out = {}
    for name in SCALARS + ("stack_dist",):
        if name == "stack_dist":
            refit = [sum(v[name][k] for v in fit) / len(fit) for k in range(4)]
        else:
            refit = sum(v[name] for v in fit) / len(fit)
        errs = [{"refit": _err(name, refit, t[name]), "current": _err(name, current[name], t[name])} for t in test]
        ship = len(test) == N_TEST and all(e["refit"] < e["current"] for e in errs)
        out[name] = {"refit": refit, "current": current[name], "test_errors": errs, "ship": ship}
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--store")
    a = ap.parse_args(argv)
    import deep_cache
    import dk_standings
    if a.store:
        deep_cache.CACHE_DIR = a.store
    idx = dk_standings.listing()["contests"]
    use = eligible(idx)
    vals = [real_values(dk_standings.summary(r["contest_id"])[0] or {}) for r in use[:N_FIT + N_TEST]]
    current = dk_standings._model_constants()
    res = {"rule": __doc__.split("Usage:")[0], "contests": [r["contest_id"] for r in use[:N_FIT + N_TEST]],
           "readable": len(vals) >= N_FIT + N_TEST,
           "decision": decide(vals[:N_FIT], vals[N_FIT:], current) if len(vals) >= N_FIT + N_TEST else None}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "field_refit.json"), "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: res[k] for k in ("contests", "readable")}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
