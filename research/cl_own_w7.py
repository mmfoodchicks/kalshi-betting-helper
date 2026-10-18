"""Does the public draft like an optimizer? The week-7 test, written first.

research/cl_own.py's per-position pull field FAILED its pre-registered test
(fit on week 1, tested on week 6: top-40 ownership MAE 8.95 points against
the served field's 8.29; research/reports/cl_own.md). Reading why, one number
every classic board already carries did better on BOTH weeks: each player's
share of the simulated Sundays' exact best lineups (the board's opt_pct,
which sums to 900% like real ownership). Against real any-slot ownership:

    week 1: all players 1.83 vs the field's 2.16; top 40 5.02 vs 5.89
    week 6: all players 2.62 vs the field's 3.42; top 40 6.27 vs 8.29

Both weeks were read to find this, so neither tests it. This is the test,
WRITTEN BEFORE week 7's main slate was played (2026-10-18, its lock is
2026-10-25 17:00 UTC):

  Data: the Sunday 2026-10-25 main-slate classic board as served at lock
  (the snapshot workflow's errors/tourney-<dg>.json on sim-history; its
  players' field_pct and opt_pct as built), and the owner's standings
  export of that slate's largest classic GPP (the Millionaire), read by
  dk_standings.parse (any-slot ownership over the entries with a lineup).
  Metrics: MAE in points over the board's players, and over the 40 players
  the real field owned most -- field_pct against opt_pct.
  Verdict: opt_pct WINS week 7 when it beats field_pct on both metrics.
  - Wins: three weeks of three, and the next build is a field drawn the way
    an optimizer user drafts (lineups optimal under noisy projections, mixed
    with the casual draws), its own rule written before week 8.
  - Loses either: the lead is dropped; the report says so.
  Nothing here changes a board.

python3 -m research.cl_own_w7 <board json> <standings zip or csv>
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "cl_own_w7.json")


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
    main(sys.argv[1], sys.argv[2])
