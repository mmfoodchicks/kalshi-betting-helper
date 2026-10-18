"""The optimizer field on week 7: keep it or revert it. Written first.

dfs_tourney.FIELD_MODEL = "optimizer" shipped 2026-10-18. Each player's
field ownership is steered to 0.9 x his share of the simulated Sundays' best
lineups + 0.1 x the calibrated field's own share, because the best-lineup
share sat closer to real ownership on weeks 1, 2 and 6. Week 2 was the one
week not read to find it (research/cl_own_w2.py, rule first); OPT_FIELD_ALPHA
0.1 came from a sweep over all three. The steered field drew, against real
any-slot ownership (MAE all / top 40):
    week 1 1.86 / 5.14 vs the served 2.16 / 5.89
    week 2 2.09 / 5.64 vs 2.38 / 6.62
    week 6 2.66 / 6.27 vs 3.42 / 8.29
(research/data/cl_own_steer.json; every one of those weeks was read before
the field shipped). A board built under it stamps both shares per player:
field_pct, the steered field it scores against, and casual_pct, the
calibrated field it replaced.

THE RULE, WRITTEN BEFORE week 7 was played (its lock is 2026-10-25 17:00 UTC):
  Data: the 2026-10-25 Sunday main-slate classic board as served at lock (the
  snapshot workflow's errors/tourney-<dg>.json), and the owner's standings
  export of that slate's largest classic GPP (dk_standings.parse, any-slot
  ownership over the entries with a lineup).
  Metrics: MAE in points over the board's players and over the 40 the real
  field owned most -- field_pct against casual_pct.
  Verdict:
  - KEEP when field_pct beats casual_pct on the top-40 metric.
  - Otherwise REVERT: FIELD_MODEL goes back to "softmax" in the next push,
    and the report says why.
  research/cl_own_w7.py, written before this field shipped, compares
  field_pct with opt_pct. Under the steered field the two are near-equal by
  construction, so its verdict is reported and does not decide.

python3 -m research.cl_own_w7b <board json> <standings zip or csv>
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "cl_own_w7b.json")


def main(board_path, export_path):
    sys.path.insert(0, ROOT)
    import dk_standings as DS
    b = json.load(open(board_path))
    b = b.get("board", b)
    text, sha = DS.read_text(export_path)
    parsed = DS.parse(text)
    with_lu = [e for e in parsed["entries"] if e["lineup"]]
    n = max(1, len(with_lu))
    real = {}
    for e in with_lu:
        for _s, nm in e["lineup"]:
            real[nm] = real.get(nm, 0) + 1
    real = {k: 100.0 * v / n for k, v in real.items()}
    P = b["players"]
    if not all("casual_pct" in p for p in P):
        raise SystemExit("the board carries no casual_pct: it was not built under the optimizer field")
    R = [real.get(DS._norm(p["name"]), 0.0) for p in P]
    F = [float(p.get("field_pct") or 0.0) for p in P]
    C = [float(p.get("casual_pct") or 0.0) for p in P]
    top = sorted(range(len(P)), key=lambda i: -R[i])[:40]

    def mae(x, idx):
        return sum(abs(x[i] - R[i]) for i in idx) / max(1, len(idx))
    allx = range(len(P))
    res = {"export_sha256": sha, "entries_with_lineup": len(with_lu), "draft_group_id": b.get("draft_group_id"),
           "board_built_ts": b.get("built_ts"), "field_model": (b.get("field_model") or {}).get("model"),
           "mae_all": {"field_pct": mae(F, allx), "casual_pct": mae(C, allx)},
           "mae_top40": {"field_pct": mae(F, top), "casual_pct": mae(C, top)}}
    res["keep"] = bool(res["mae_top40"]["field_pct"] < res["mae_top40"]["casual_pct"])
    with open(OUT, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
