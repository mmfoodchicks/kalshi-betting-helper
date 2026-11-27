"""Build the DEN @ PIT showdown board (DraftKings draft group 157076) from ANY
checkout of this tree, on recorded inputs, and keep what the board itself
does not print (research only; never run on the PC or the server).

    python3 research/den_pit_audit/build_board.py --repo <checkout> --out <dir>
        --net <store> --mode record|replay --n 60000 --seed 20261127

The board is dfs_tourney.build_nfl_showdown exactly as that checkout has it.
Beside it this keeps, in <out>/capture.npz: every pool player's simulated
worlds (the matrix X the board scores, captain unscaled), the universe
(idx), the field's weights (f), the scored rows and run_subset's per-lineup
means (win, top1, top01, cash, money, ev, opt) -- top01 is computed by the
board and never printed on it. The environment is the suite's hermetic one:
VIGIL_NO_BG=1, no worlds cache, no checkpoints, private disk caches.
"""
import argparse
import json
import os
import shutil
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--repo", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--net", required=True)
ap.add_argument("--mode", choices=("record", "replay"), required=True)
ap.add_argument("--n", type=int, default=60000)
ap.add_argument("--seed", type=int, default=20261127)
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--dg", type=int, default=157076)
ap.add_argument("--contest", type=int, default=198174394)
ap.add_argument("--week", type=int, default=12)
ap.add_argument("--deep-seed", default=None, help="a deep-store directory copied in first (both builds alike)")
a = ap.parse_args()

repo = os.path.abspath(a.repo)
out = os.path.abspath(a.out)
os.makedirs(out, exist_ok=True)
priv = os.path.join(out, "priv")
for sub in ("deep", "sim", "run", "scratch", "tmp"):
    os.makedirs(os.path.join(priv, sub), exist_ok=True)
if a.deep_seed:
    for fn in os.listdir(a.deep_seed):
        if fn.endswith(".pkl") and not os.path.exists(os.path.join(priv, "deep", fn)):
            shutil.copy2(os.path.join(a.deep_seed, fn), os.path.join(priv, "deep", fn))
os.environ.update(VIGIL_NO_BG="1", VIGIL_POOL_CACHE_DIR="", DEEP_CACHE_DIR=os.path.join(priv, "deep"),
                  VIGIL_SIM_CACHE_DIR=os.path.join(priv, "sim"), VIGIL_RUN_DIR=os.path.join(priv, "run"),
                  VIGIL_SCRATCH_DIR=os.path.join(priv, "scratch"), TMPDIR=os.path.join(priv, "tmp"))
os.environ.pop("VIGIL_CKPT_DIR", None)
os.environ.pop("VIGIL_TOURNEY_GPU", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netreplay  # noqa: E402

netreplay.install(os.path.abspath(a.net), a.mode, os.path.join(out, "requests.jsonl"))
sys.path.insert(0, repo)
os.chdir(repo)

import numpy as np  # noqa: E402
import errlog  # noqa: E402
import dfs_tourney  # noqa: E402
import nfl_dfs_sim  # noqa: E402

assert os.path.dirname(os.path.abspath(dfs_tourney.__file__)) == repo, dfs_tourney.__file__
NOTES = []
_note = errlog.note


def note(*args, **kw):
    NOTES.append({"args": [str(x) for x in args], **{k: str(v) for k, v in kw.items()}})
    return _note(*args, **kw)


errlog.note = note
CAP = {}
_pool = nfl_dfs_sim.player_pool


def player_pool(*args, **kw):
    p = _pool(*args, **kw)
    CAP["pool"] = p
    return p


nfl_dfs_sim.player_pool = player_pool
_enum = dfs_tourney.enumerate_showdown


def enumerate_showdown(ents, *args, **kw):
    r = _enum(ents, *args, **kw)
    CAP["ents"] = [{k: v for k, v in e.items() if k != "arr"} for e in ents]
    CAP["idx"] = r[0]
    CAP["allowed"] = r[2]
    return r


dfs_tourney.enumerate_showdown = enumerate_showdown
_fw = dfs_tourney.field_weights


def field_weights(*args, **kw):
    r = _fw(*args, **kw)
    CAP["f"], CAP["beta"] = r
    return r


dfs_tourney.field_weights = field_weights
_rs = dfs_tourney.run_subset


def run_subset(W, X, f, grid, rows, *args, **kw):
    r = _rs(W, X, f, grid, rows, *args, **kw)
    CAP["X"] = np.asarray(X)
    CAP["rows"] = np.asarray(rows)
    CAP["res"] = r
    CAP["grid"] = {k: grid[k] for k in ("F", "first", "top1", "top01", "cash", "money")}
    return r


dfs_tourney.run_subset = run_subset

t0 = time.time()
board = dfs_tourney.build_nfl_showdown(a.dg, contest_id=a.contest, n_sims=a.n, week=a.week, preseason=False,
                                       model="engine", seed=a.seed, workers=a.workers)
t1 = time.time()
if board is None:
    print("REFUSED:", dfs_tourney.last_refusal() if hasattr(dfs_tourney, "last_refusal") else "?")
    sys.exit(2)
with open(os.path.join(out, "board.json"), "w") as fh:
    json.dump(dfs_tourney.plain(board), fh, default=str)
pool = CAP["pool"] or {}
names = sorted(k for k, v in pool.items() if v.get("arr") is not None and len(v["arr"]))
res = CAP["res"]
np.savez_compressed(os.path.join(out, "capture.npz"),
                    pool_names=np.array(names),
                    pool_arr=np.stack([np.asarray(pool[k]["arr"], dtype=np.float32) for k in names]),
                    ents=np.array([e["name"] for e in CAP["ents"]]),
                    idx=CAP["idx"], allowed=CAP["allowed"], f=CAP["f"], X=CAP["X"], rows=CAP["rows"],
                    **{"res_" + k: np.asarray(v) for k, v in res.items() if np.ndim(v) == 1},
                    **{"grid_" + k: np.asarray(v) for k, v in CAP["grid"].items()})
with open(os.path.join(out, "meta.json"), "w") as fh:
    json.dump({"repo": repo, "head": os.popen(f"git -C '{repo}' rev-parse HEAD").read().strip(),
               "n": a.n, "seed": a.seed, "workers": a.workers, "mode": a.mode, "build_s": round(t1 - t0, 1),
               "net": netreplay.counts(), "beta": float(CAP["beta"]), "notes": NOTES,
               "ents": CAP["ents"]}, fh, default=str)
print("DONE", round(t1 - t0, 1), "s", netreplay.counts())
