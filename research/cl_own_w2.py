"""Does the public draft like an optimizer? The week-2 check, written first.

research/cl_own_w7.py states the lead and its week-7 test: each player's
share of the simulated Sundays' exact best lineups (a classic board's
opt_pct) was closer to real ownership than the board's field model
(field_pct) on BOTH weeks read to find it (weeks 1 and 6). On 2026-10-18 the
owner sent week 2's Millionaire export (contest 195648007, dg 153428, the $3M
Millionaire of 2026-09-20). Week 2 was NOT read to find the lead: this rule
was WRITTEN BEFORE week 2's ownership was computed (its file was opened only
to read the contest id, the row count and the time remaining).

  Data: the board served for dg 153428 as pinned by the week-2 input receipt
  (research/data/cl_week2/served_board_153428.json, built before that lock),
  its players' field_pct and opt_pct as built, and the export's any-slot
  ownership over the entries with a lineup (dk_standings.parse).
  Metrics: MAE in points over the board's players, and over the 40 players
  the real field owned most -- field_pct against opt_pct.
  Verdict: opt_pct WINS week 2 when it beats field_pct on both metrics.
  - Wins: week 2 is the first week that was not read to find the lead. The
    optimizer-style field gets built now, and week 7 (research/cl_own_w7.py,
    as written) is its test.
  - Loses either: the lead is weakened in the report; week 7 still runs as
    written and decides.
  Nothing here changes a board.

python3 -m research.cl_own_w2 <standings zip or csv>
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "cl_own_w2.json")
BOARD = os.path.join(ROOT, "research", "data", "cl_week2", "served_board_153428.json")


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
    R = [real.get(DS._norm(p["name"]), 0.0) for p in P]
    F = [float(p.get("field_pct") or 0.0) for p in P]
    O = [float(p.get("opt_pct") or 0.0) for p in P]
    top = sorted(range(len(P)), key=lambda i: -R[i])[:40]

    def mae(x, idx):
        return sum(abs(x[i] - R[i]) for i in idx) / max(1, len(idx))
    allx = range(len(P))
    res = {"export_sha256": sha, "entries_with_lineup": len(with_lu), "draft_group_id": b.get("draft_group_id"),
           "board_built_ts": b.get("built_ts"),
           "mae_all": {"field_pct": mae(F, allx), "opt_pct": mae(O, allx)},
           "mae_top40": {"field_pct": mae(F, top), "opt_pct": mae(O, top)}}
    res["opt_wins"] = bool(res["mae_all"]["opt_pct"] < res["mae_all"]["field_pct"]
                           and res["mae_top40"]["opt_pct"] < res["mae_top40"]["field_pct"])
    with open(OUT, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    main(BOARD, sys.argv[1])
