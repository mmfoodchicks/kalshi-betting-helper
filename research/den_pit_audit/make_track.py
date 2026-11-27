"""The lineups universe.py follows world by world: the top 20 of each of the
board's four lists in both builds, the chalk, and every lineup of the served
board's lists that exists in the rebuilt universe (matched by captain and
FLEX names). Research only.

    python3 research/den_pit_audit/make_track.py --head <dir> --pre <dir> --served <board.json> --out <rows.json>
"""
import argparse
import json
import os

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--head", required=True)
ap.add_argument("--pre", required=True)
ap.add_argument("--served", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--top", type=int, default=20)
a = ap.parse_args()
cap = np.load(os.path.join(a.head, "capture.npz"))
names = list(cap["ents"])
idx = cap["idx"]
key = {}
for r, row in enumerate(idx):
    key[(names[row[0]], tuple(sorted(names[j] for j in row[1:])))] = r
rows, labels, missing = [], {}, []
LISTS = ("top_money", "top_top1", "top_ev", "top_win")
for tag, path in (("head", a.head), ("pre", a.pre), ("served", None)):
    b = json.load(open(os.path.join(path, "board.json"))) if path else json.load(open(a.served))
    for ln in LISTS:
        for k, e in enumerate(b[ln][: (a.top if tag != "served" else 10)]):
            r = key.get((e["captain"], tuple(sorted(e["flex"]))))
            if r is None:
                missing.append([tag, ln, k, e["captain"], e["flex"]])
                continue
            if r not in labels:
                rows.append(r)
                labels[r] = []
            labels[r].append(f"{tag}:{ln}:{k + 1}")
    ch = (b.get("field_model") or {}).get("chalk")
    if ch:
        r = key.get((ch["captain"], tuple(sorted(ch["flex"]))))
        if r is not None:
            if r not in labels:
                rows.append(r)
                labels[r] = []
            labels[r].append(f"{tag}:chalk")
json.dump({"rows": [int(r) for r in rows], "labels": {str(r): v for r, v in labels.items()}, "missing": missing},
          open(a.out, "w"), indent=1)
print(len(rows), "tracked;", len(missing), "served/other lineups not in the rebuilt universe")
