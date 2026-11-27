"""One pass over every simulated world of a captured DEN @ PIT board, with the
board's OWN per-world arithmetic (dfs_tourney._score_range: float32 scores
Xc @ W.T, 0.01-point buckets, the field's weighted histogram, the mass
strictly above and level, the payout grid's gathers) -- research only.

What the board sums and throws away, this keeps:
  * for every TRACKED lineup, per world: its grid index iF (mass strictly
    above) and tie index iFE -- so any column (first, top 1%, top 0.1%, cash,
    sole first, EV) is a per-world value, and two builds on common worlds give
    a PAIRED difference per world;
  * per world, the best lineup of the whole universe, the best one WE may enter
    (the board's rules), and the best one we could enter with the depth gate
    on the five field-only players lifted (`--gate-off`);
  * the board's per-lineup sums for every lineup allowed with the gate off
    (a superset of the board's rows, so the board's own figures are reproduced
    as a check).

    python3 research/den_pit_audit/universe.py --capture <build dir> --track <rows.json>
        --out <file.npz> --net <store> [--workers 4]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--capture", required=True)
ap.add_argument("--track", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--net", required=True)
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--chunk", type=int, default=96)
ap.add_argument("--only-worlds", default=None,
                help="a .npy of world indices: only those worlds (the pre build differs from HEAD in no other)")
a = ap.parse_args()
scratch = os.path.join(os.path.dirname(os.path.abspath(a.out)), "u_priv")
common.setup(scratch, a.net, "replay")

import multiprocessing as mp  # noqa: E402
import numpy as np  # noqa: E402
import dfs_tourney as T  # noqa: E402
import dk  # noqa: E402
import nfl_dfs  # noqa: E402

cap = np.load(os.path.join(a.capture, "capture.npz"))
meta = json.load(open(os.path.join(a.capture, "meta.json")))
X = np.ascontiguousarray(cap["X"], dtype=np.float32)
SEL = None
if a.only_worlds:
    SEL = np.load(a.only_worlds).astype(np.int64)
    X = np.ascontiguousarray(X[:, SEL])
idx, f, allowed = cap["idx"], np.asarray(cap["f"], dtype=np.float64), cap["allowed"]
ents = meta["ents"]
P, N = X.shape
assert P == len(ents) == idx.max() + 1

# ---- the board's rules with the depth gate lifted for the field-only players
rich = sum(1 for p in ents if p.get("pos") in nfl_dfs._SD_GPP_CPT_POS and not p.get("_field_only")
           and p["cpt_salary"] >= nfl_dfs._SD_MIN_CPT_SALARY) >= 3


def cpt_ok_off(p):
    if p.get("pos") not in nfl_dfs._SD_GPP_CPT_POS:
        return False
    return (not rich) or p["cpt_salary"] >= nfl_dfs._SD_MIN_CPT_SALARY


def entry_ok_off(cap_p, picked):
    got = []
    for p in picked:
        if not nfl_dfs._sd_allowed(p, cap_p, got):
            return False
        got.append(p)
    return True


def cpt_ok_on(p):
    return (not p.get("_field_only")) and cpt_ok_off(p)


def entry_ok_on(cap_p, picked):
    got = []
    for p in picked:
        if p.get("_field_only") or not nfl_dfs._sd_allowed(p, cap_p, got):
            return False
        got.append(p)
    return True


idx2, W, allowed_on = T.enumerate_showdown(ents, nfl_dfs.CAP, cpt_mult=nfl_dfs.CPT_MULT, entry_ok=entry_ok_on,
                                           cpt_ok=cpt_ok_on)
assert np.array_equal(idx2, idx), "re-enumerated universe differs from the board's"
assert np.array_equal(allowed_on, allowed), "re-derived rules differ from the board's"
_i3, _w3, allowed_off = T.enumerate_showdown(ents, nfl_dfs.CAP, cpt_mult=nfl_dfs.CPT_MULT, entry_ok=entry_ok_off,
                                             cpt_ok=cpt_ok_off)
gated = np.array([bool(p.get("_field_only")) for p in ents])
has_gated = gated[idx].any(axis=1)
assert not (allowed & has_gated).any()

contest = dk.contest_detail(common.CONTEST)
C = T.contest_capacity(contest)
grid = T.payout_grid(C, contest.get("payouts") or [], float(contest.get("entry_fee") or 1.0),
                     int(contest.get("places_paid") or max(1, C // 5)))
G = T._grid_table([grid])
G2, nE = T._tie_table([grid])
F_grid = grid["F"]
E_log = np.log(np.maximum(grid["E"], 1e-300))
track = np.asarray(json.load(open(a.track))["rows"], dtype=np.int64)
chalk = int(np.argmax(f))
rows = np.union1d(np.where(allowed_off)[0], np.union1d(track, [chalk]))
WT = np.ascontiguousarray(W.T, dtype=np.float32)
on_rows = np.where(allowed)[0]
off_rows = np.where(allowed_off)[0]
gated_rows = np.where(has_gated)[0]


def work(rng_):
    a0, b0 = rng_
    K = len(rows)
    acc = np.zeros((K, G.shape[1]), dtype=np.float64)
    acc2 = np.zeros((K, G2.shape[1]), dtype=np.float64)
    nw = b0 - a0
    tF = np.zeros((len(track), nw), dtype=np.int32)
    tFE = np.zeros((len(track), nw), dtype=np.int32)
    tS = np.zeros((len(track), nw), dtype=np.float32)
    tFm = np.zeros((len(track), nw), dtype=np.float64)     # the field mass strictly above, unrounded to the grid
    tEm = np.zeros((len(track), nw), dtype=np.float64)     # the field mass level with it (its own bucket)
    best = np.zeros((4, nw), dtype=np.int64)                 # all, ours (gate on), ours (gate off), any gated
    bests = np.zeros((4, nw), dtype=np.float32)
    for s in range(a0, b0, a.chunk):
        e = min(b0, s + a.chunk)
        Xc = np.ascontiguousarray(X[:, s:e].T)
        Sc = Xc @ WT
        for j in range(e - s):
            w = s - a0 + j
            row = Sc[j]
            for k, sub in enumerate((None, on_rows, off_rows, gated_rows)):
                if sub is None:
                    i = int(np.argmax(row))
                else:
                    i = int(sub[np.argmax(row[sub])])
                best[k, w], bests[k, w] = i, row[i]
        Bc = T._buckets(Sc)
        for j in range(Bc.shape[0]):
            w = s - a0 + j
            bj = Bc[j]
            hist = np.bincount(bj, weights=f, minlength=T._NB)
            strictly = np.cumsum(hist[::-1])[::-1] - hist
            iF, iFE = T._tables_from_hist(hist, F_grid, E_log, nE)
            br = bj[rows]
            acc += G[iF][br]
            acc2 += G2[iFE][br]
            bt = bj[track]
            tF[:, w] = iF[bt]
            tFE[:, w] = iFE[bt]
            tFm[:, w] = strictly[bt]
            tEm[:, w] = hist[bt]
        tS[:, s - a0:e - a0] = (Xc @ WT[:, track]).T
    return acc, acc2, tF, tFE, tFm, best, bests, tS, tEm


if __name__ == "__main__":
    w = max(1, a.workers)
    edges = np.linspace(0, N, 4 * w + 1).astype(int)
    jobs = [(int(edges[i]), int(edges[i + 1])) for i in range(len(edges) - 1) if edges[i + 1] > edges[i]]
    ctx = mp.get_context("fork")
    with ctx.Pool(w) as pool:
        parts = pool.map(work, jobs)
    acc = sum(p[0] for p in parts) / N
    acc2 = sum(p[1] for p in parts) / N
    cat = lambda k: np.concatenate([p[k] for p in parts], axis=-1)   # noqa: E731
    np.savez_compressed(a.out, sel=(SEL if SEL is not None else np.arange(N)), rows=rows, track=track, chalk=chalk, allowed_off=allowed_off, has_gated=has_gated,
                        acc=acc, acc2=acc2, tF=cat(2), tFE=cat(3), tFm=cat(4), best=cat(5), bests=cat(6), tS=cat(7), tEm=cat(8),
                        G=G, G2=G2, C=C, places=int(grid["places"]), money_places=int(grid["money_places"]))
    if SEL is not None:
        print("DONE (worlds subset)", len(SEL), "worlds")
        raise SystemExit(0)
    # the check: the board's own figures, reproduced for its own rows
    res = {k[4:]: cap[k] for k in cap.files if k.startswith("res_")}
    pos = {r: i for i, r in enumerate(rows)}
    ii = np.array([pos[r] for r in cap["rows"]])
    chk = {"win": np.abs(acc[ii, 0] - res["win"][cap["rows"]]).max(),
           "top1": np.abs(acc[ii, 1] - res["top1"][cap["rows"]]).max(),
           "top01": np.abs(acc[ii, 2] - res["top01"][cap["rows"]]).max(),
           "cash": np.abs(acc[ii, 3] - res["cash"][cap["rows"]]).max(),
           "ev": np.abs(acc2[ii, 1] - res["ev"][cap["rows"]]).max()}
    print("CHECK max |diff| vs the board's own figures:", {k: float(v) for k, v in chk.items()})
    print("DONE", len(rows), "rows,", len(track), "tracked,", N, "worlds; allowed on/off",
          int(allowed.sum()), int(allowed_off.sum()))
