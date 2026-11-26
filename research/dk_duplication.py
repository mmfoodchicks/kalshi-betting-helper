"""Duplication in the real DraftKings fields against the board's field model
(2026-11-26; the owner's priority 2: "exact metric definition, sample sizes,
uncertainty"; the review: "do not scale duplication by 387").
Report: research/reports/dk_duplication.md.

The metric, exactly as dk_standings.summarize computes it on a contest's
standings: distinct_pair_collision C = sum over distinct lineups of
n(n-1) / (N(N-1)), N the entries with a lineup, a lineup the set of its nine
players (classic) or its captain plus the set of its five flex (showdown). It
is the chance that two DIFFERENT entries, drawn at random, hold the same
lineup. If the N entries were independent draws from a distribution p over
lineups it would be an unbiased estimate of sum p^2.

Per contest this prints the sample sizes, C, and a bound on its sampling SE
under that independence (see `se_bound`); across contests, each format's
geometric mean of C with a log-scale SE. The board's own model figure
(`field_duplication` on boards built from 2026-11-26) is read from the data
branch's board exports where the contest's board carries one.

    python research/dk_duplication.py           # the table, from research/data/dk_fields/
    python research/dk_duplication.py --json    # the same as JSON
"""
import argparse
import glob
import json
import math
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
FIELDS = os.path.join(_HERE, "data", "dk_fields")


def se_bound(C, N, max_copies):
    """An UPPER bound on the SE of C as an estimate of sum p^2, if the N
    entries were independent draws. The U-statistic's variance is
        4(N-2)/(N(N-1)) (S3 - S2^2) + 2/(N(N-1)) (S2 - S2^2),
    S2 = sum p^2, S3 = sum p^3. The summaries do not keep every count, so S3
    is bounded by p_max x S2 with p_max read as max_copies / N (the largest
    lineup's share), and S2 by C itself. Real entries are not independent
    (150-entry players diversify on purpose; the public copies the same
    optimizer), so this is the sampling floor, not the whole uncertainty --
    the spread ACROSS contests below is the honest one."""
    if not (N and N > 2 and C is not None):
        return None
    pmax = max_copies / float(N)
    s3 = pmax * C
    var = 4.0 * (N - 2) / (N * (N - 1.0)) * max(0.0, s3 - C * C) + 2.0 / (N * (N - 1.0)) * max(0.0, C - C * C)
    return math.sqrt(var)


def se_plugin(C, N, hist):
    """The same variance with S3 read off the copy-count histogram (summaries
    made after 2026-11-26 keep it): S3 = sum over lineups of (n/N)^3."""
    if not (N and N > 2 and C is not None and hist):
        return None
    s3 = sum(int(v) * (int(k) / float(N)) ** 3 for k, v in hist.items())
    var = 4.0 * (N - 2) / (N * (N - 1.0)) * max(0.0, s3 - C * C) + 2.0 / (N * (N - 1.0)) * max(0.0, C - C * C)
    return math.sqrt(var)


def geo(xs):
    """Geometric mean and the SE of its log (sample SD / sqrt n), n >= 2."""
    ls = [math.log(x) for x in xs if x and x > 0]
    n = len(ls)
    if n == 0:
        return None
    m = sum(ls) / n
    if n < 2:
        return {"n": n, "geo_mean": math.exp(m), "log_se": None, "min": min(xs), "max": max(xs)}
    sd = math.sqrt(sum((x - m) ** 2 for x in ls) / (n - 1))
    return {"n": n, "geo_mean": math.exp(m), "log_se": sd / math.sqrt(n),
            "x_range_95": [math.exp(m - 1.96 * sd / math.sqrt(n)), math.exp(m + 1.96 * sd / math.sqrt(n))],
            "min": min(xs), "max": max(xs)}


def _board_for(cid):
    """The data branch's board export whose contest (or one of whose
    contests) is `cid`, if it carries the model's figure."""
    try:
        names = subprocess.run(["git", "ls-tree", "--name-only", "origin/sim-history", "errors/"], cwd=_ROOT,
                               capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    for nm in names:
        if not (nm.startswith("errors/tourney-") and nm.endswith(".json")) or "list" in nm:
            continue
        try:
            d = json.loads(subprocess.run(["git", "show", f"origin/sim-history:{nm}"], cwd=_ROOT,
                                          capture_output=True, text=True, check=True).stdout)
        except (subprocess.CalledProcessError, ValueError):
            continue
        ids = [((d.get("contest") or {}).get("id"))] + [c.get("id") for c in (d.get("contests") or [])]
        if int(cid) not in [int(i) for i in ids if i]:
            continue
        if d.get("field_duplication"):
            return {"board": nm, "dg": d.get("draft_group_id"), "built_ts": d.get("built_ts"), **d["field_duplication"]}
        # classic boards have carried the same estimator on their field sample since cl_sampler
        fm = d.get("field_model") or {}
        if d.get("kind") == "classic" and fm.get("collision") is not None:
            return {"board": nm, "dg": d.get("draft_group_id"), "built_ts": d.get("built_ts"),
                    "model_pair_collision": fm["collision"], "rows": fm.get("n"), "kind": "classic",
                    "basis": "field_model.collision: the field sample's sum n(n-1) / (M(M-1))"}
    return None


def rows():
    out = []
    for f in sorted(glob.glob(os.path.join(FIELDS, "dk_field_*.json"))):
        with open(f) as fh:
            d = json.load(fh)
        dup = d.get("duplicates") or {}
        N = int(d.get("with_lineup") or d.get("entries") or 0)
        C = dup.get("distinct_pair_collision")
        r = {"contest_id": d.get("contest_id"), "kind": d.get("kind"), "name": (d.get("contest") or {}).get("name"),
             "entries": d.get("entries"), "with_lineup": N, "distinct_lineups": dup.get("distinct_lineups"),
             "entries_in_duplicated_lineups_pct": dup.get("entries_in_duplicated_lineups_pct"),
             "max_copies": dup.get("max_copies"), "max_entry_users_pct": d.get("max_entry_users_pct"),
             "collision": C, "se_bound_iid": se_bound(C, N, dup.get("max_copies") or 0),
             "se_iid": se_plugin(C, N, dup.get("copies_histogram")),
             "one_in": (round(1.0 / C) if C else None)}
        b = _board_for(d.get("contest_id"))
        if b:
            r["model"] = b
            r["model_over_real"] = (b["model_pair_collision"] / C) if C else None
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rs = rows()
    by = {k: geo([r["collision"] for r in rs if r["kind"] == k and r["collision"]]) for k in ("classic", "showdown")}
    if args.json:
        print(json.dumps({"contests": rs, "by_format": by}, indent=1))
        return 0
    print("contest    kind      entries  distinct  dup-entries%  max  150-max%  collision   1-in        iid SE bound  model")
    for r in rs:
        m = r.get("model")
        print(f"{r['contest_id']}  {r['kind']:<8} {r['entries']:>8,} {r['distinct_lineups']:>9,} "
              f"{r['entries_in_duplicated_lineups_pct']:>12.2f} {r['max_copies']:>4} {r['max_entry_users_pct']:>8.2f}  "
              f"{r['collision']:.3e} {r['one_in']:>10,}   {r['se_bound_iid']:.2e} "
              f"({100 * r['se_bound_iid'] / r['collision']:.1f}%)  "
              + (f"{m['model_pair_collision']:.2e} (x{r['model_over_real']:.3g})" if m else "-"))
    for k, g in by.items():
        if g:
            rng = (f", 95% {g['x_range_95'][0]:.2e}-{g['x_range_95'][1]:.2e}" if g.get("x_range_95") else "")
            print(f"{k}: {g['n']} contests, geometric mean {g['geo_mean']:.3e}"
                  + (f", log SE {g['log_se']:.3f}{rng}" if g.get("log_se") else "")
                  + f"; range {g['min']:.2e}-{g['max']:.2e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
