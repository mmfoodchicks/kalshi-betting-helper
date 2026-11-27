"""The audit's hermetic environment (research only): private disk caches, no
background loops, no worlds cache, no checkpoints, every HTTP response from
the recorded store (netreplay). Call setup() before importing the tree."""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DG, CONTEST, WEEK, SEASON = 157076, 198174394, 12, 2026
SEED = 20261127


def setup(out, net, mode="replay", deep_seed=None, repo=ROOT):
    priv = os.path.join(out, "priv")
    for sub in ("deep", "sim", "run", "scratch", "tmp"):
        os.makedirs(os.path.join(priv, sub), exist_ok=True)
    if deep_seed:
        for fn in os.listdir(deep_seed):
            if fn.endswith(".pkl") and not os.path.exists(os.path.join(priv, "deep", fn)):
                shutil.copy2(os.path.join(deep_seed, fn), os.path.join(priv, "deep", fn))
    os.environ.update(VIGIL_NO_BG="1", VIGIL_POOL_CACHE_DIR="", DEEP_CACHE_DIR=os.path.join(priv, "deep"),
                      VIGIL_SIM_CACHE_DIR=os.path.join(priv, "sim"), VIGIL_RUN_DIR=os.path.join(priv, "run"),
                      VIGIL_SCRATCH_DIR=os.path.join(priv, "scratch"), TMPDIR=os.path.join(priv, "tmp"))
    os.environ.pop("VIGIL_CKPT_DIR", None)
    os.environ.pop("VIGIL_TOURNEY_GPU", None)
    sys.path.insert(0, HERE)
    import netreplay
    netreplay.install(os.path.abspath(net), mode, os.path.join(out, "requests.jsonl"))
    sys.path.insert(0, repo)
    os.chdir(repo)
    return netreplay
