"""Replay the HEAD board's pool call (same inputs, same seed, same arguments as
dfs_tourney.build_nfl_showdown makes) and keep what the board throws away:
both teams' simulated box scores per world, the exact (anchored) profiles the
worlds were played on, and the week context. Research only.

    python3 research/den_pit_audit/pool_boxes.py --out <dir> --net <store> --deep-seed <dir>

Writes <out>/boxes.npz (home_*/away_* per box key), <out>/profiles.pkl
({raw: {DEN, PIT}, anchored: (hp2, ap2), neutral, n, seed, env_sd}), and
<out>/pool.npz (every pool player's worlds) so the caller can check the worlds
are the board's bit for bit.
"""
import argparse
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--net", required=True)
ap.add_argument("--deep-seed", default=None)
ap.add_argument("--n", type=int, default=60000)
ap.add_argument("--seed", type=int, default=common.SEED)
ap.add_argument("--workers", type=int, default=4)
a = ap.parse_args()
out = os.path.abspath(a.out)
os.makedirs(out, exist_ok=True)
nr = common.setup(out, a.net, "replay", a.deep_seed)

import numpy as np  # noqa: E402
import dfs_tourney  # noqa: E402
import nfl_dfs_engine  # noqa: E402
import nfl_dfs_sim  # noqa: E402

CAP = {}
_sg = nfl_dfs_engine.simulate_game


def simulate_game(game, n, rng, ctx):
    ctx = dict(ctx, keep_boxes=True)          # only adds the boxes to the result; no draw changes
    CAP["ctx"] = {k: v for k, v in ctx.items() if k in ("profiles", "drive", "weight", "att_weight", "env_sd")}
    CAP["game"] = {k: game.get(k) for k in ("home", "away", "neutral", "teams", "label")}
    CAP["players"] = [{k: v for k, v in p.items() if k not in ("arr",)} for p in game.get("players") or []]
    r = _sg(game, n, rng, ctx)
    CAP["sim"] = r
    return r


nfl_dfs_engine.simulate_game = simulate_game
_pwp = nfl_dfs_engine.play_worlds_parallel


def play_worlds_parallel(hp, ap_, n, seed, neutral=False, workers=1, chunk=5000, env_sd=None, T=None):
    CAP["anchored"] = (hp, ap_)
    CAP["play"] = {"n": n, "seed": seed, "neutral": neutral, "chunk": chunk, "env_sd": env_sd}
    return _pwp(hp, ap_, n, seed, neutral=neutral, workers=workers, chunk=chunk, env_sd=env_sd, T=T)


nfl_dfs_engine.play_worlds_parallel = play_worlds_parallel

pool = nfl_dfs_sim.player_pool(common.WEEK, n=a.n, preseason=False, teams={"DEN", "PIT"}, discrete=False,
                               model="engine", seed=a.seed, workers=dfs_tourney._workers(a.workers), compact=True)
sim = CAP["sim"]
home, away = sim["anchor"]["home"], sim["anchor"]["away"]
boxes = sim["boxes"]
np.savez_compressed(os.path.join(out, "boxes.npz"),
                    **{f"home_{k}": np.asarray(v) for k, v in boxes[home].items()},
                    **{f"away_{k}": np.asarray(v) for k, v in boxes[away].items()})
names = sorted(k for k, v in pool.items() if v.get("arr") is not None and len(v["arr"]))
np.savez_compressed(os.path.join(out, "pool.npz"), names=np.array(names),
                    arr=np.stack([np.asarray(pool[k]["arr"], dtype=np.float32) for k in names]))
comp = {}
for i, p in enumerate(CAP["players"]):
    for k, v in (sim["components"].get(i) or {}).items():
        comp[f"{p['name']}|{k}"] = np.asarray(v).astype(np.int32)
np.savez_compressed(os.path.join(out, "components.npz"), **comp)
with open(os.path.join(out, "profiles.pkl"), "wb") as fh:
    pickle.dump({"home": home, "away": away, "raw": {t: CAP["ctx"]["profiles"].get(t) for t in ("DEN", "PIT")},
                 "anchored": CAP["anchored"], "play": CAP["play"], "drive": CAP["ctx"].get("drive"),
                 "weight": CAP["ctx"].get("weight"), "anchor_report": sim["anchor"], "game": CAP["game"],
                 "players": CAP["players"]}, fh)
print("DONE", home, away, nr.counts())
