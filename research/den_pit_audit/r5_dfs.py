"""EXPERIMENTAL, RESEARCH ONLY: the DEN @ PIT DFS worlds with the game played by
the coaching engine R5 instead of nfl_deep_sim, everything else production's:
nfl_dfs_engine.simulate_game's anchor (each arm re-levelled on its OWN engine to
the same blended targets -- 0.7 drive model + 0.3 deep engine, and Sleeper's
pass attempts), its Sleeper-usage deal, its allocation noise, its defenses and
kickers. The swap is research/nfl_coach_dfs.py's (nfl_deep_sim.play_game
replaced for the length of a `with` block in this process and the pool
processes it forks), with R5's game instead of R1's. R5's own player deal
(R2's situational usage, R5's second unit) is NOT used here: only its boxes
reach the DFS deal, so this arm shows what R5's game script does to DFS points.
Nothing is validated; nothing is deployed; nothing reads the game's result.

    python3 research/den_pit_audit/r5_dfs.py --boxes <pool_boxes dir> --n 20000 --out <npz>
"""
import argparse
import contextlib
import os
import pickle
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--boxes", required=True)
ap.add_argument("--n", type=int, default=20000)
ap.add_argument("--seed", type=int, default=common.SEED)
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--arms", default="prod,r5")
ap.add_argument("--out", required=True)
a = ap.parse_args()
sys.path.insert(0, common.ROOT)
sys.path.insert(0, os.path.join(common.ROOT, "research"))

import numpy as np  # noqa: E402
import nfl_coach_dfs as CD  # noqa: E402
import nfl_coach_sim_r5 as E5  # noqa: E402
import nfl_deep_sim as D  # noqa: E402
import nfl_dfs_engine as NE  # noqa: E402

PR = pickle.load(open(os.path.join(a.boxes, "profiles.pkl"), "rb"))
_PRNG = {}


def r5_play_game(home, away, rng, T=None, neutral=False, playoff=False, form_sd=None, env_sd=None, log=None):
    """nfl_deep_sim.play_game's signature, played by R5 on the caller's base
    tables (as nfl_coach_dfs.coach_play_game sets R1 up). R5's pull generator
    is seeded off the game generator, one draw a game."""
    prng = random.Random(rng.getrandbits(31))
    r = E5.play_game(home, away, rng, prng, T=CD.coach_tables(T or D.tables()), neutral=neutral, playoff=playoff,
                     form_sd=D._FORM_SD if form_sd is None else form_sd, env_sd=env_sd, hfa=D._HFA_YDS)
    r.pop("log", None)
    return r


@contextlib.contextmanager
def r5_games():
    orig = D.play_game
    D.play_game = r5_play_game
    try:
        yield
    finally:
        D.play_game = orig


game = dict(PR["game"], players=PR["players"])
ctx = {"T": D.tables(), "profiles": PR["raw"], "drive": PR["drive"], "weight": PR["weight"], "workers": a.workers}
out = {}
for arm in a.arms.split(","):
    rng = np.random.default_rng(a.seed + (0 if arm == "prod" else 1))
    cm = r5_games() if arm == "r5" else contextlib.nullcontext()
    with cm:
        sim = NE.simulate_game(game, a.n, rng, dict(ctx))
    for p in sim["players"]:
        out[f"{arm}|{p['name']}"] = np.asarray(p["arr"], dtype=np.float32)
    for t, v in sim["dst"].items():
        out[f"{arm}|DST_{t}"] = np.asarray(v, dtype=np.float32)
    for t, v in sim["k"].items():
        out[f"{arm}|K_{t}"] = np.asarray(v, dtype=np.float32)
    rep = sim["anchor"]
    out[f"{arm}|_box_pts"] = np.asarray([rep["box"][PR["home"]]["pts"], rep["box"][PR["away"]]["pts"]])
    print(arm, "box", {t: rep["box"][t]["pts"] for t in rep["box"]}, "targets", rep.get("targets"), flush=True)
np.savez_compressed(a.out, **out)
print("DONE", a.out)
