"""The deep-anchor flip, staged as a patch, because nfl_deep_data.py is FROZEN until the deep-weight read.

The registered 100-game read grades the deep engine as it runs, and four coaching guard files pin
nfl_deep_data.py byte for byte (NC0, NR0, NQ0, N4-0: "the deep weight's read sits on" it). So the fix cannot
live in that file, not even switched off. It lives in research/data/deep_anchor_flip.patch, and the flip is:

    git apply research/data/deep_anchor_flip.patch
    # then, in the same commit: the four coaching guards' PINNED nfl_deep_data.py sha256 -> the patched file's
    # (DEEP_DATA_FLIPPED_SHA below), and research/data/nfl_deep_weight_read.json already committed (guard DA2)

The patched file is the measured fix:
- ANCHOR_V2 True, MARKET_PTS_PER_WIN_V2 1.86 (research/deep_anchor_scale.py);
- ANCHOR_VERSION 2 stamped in every anchor block.

Everything outside that file that a flip must reach is already in production and reads the version
defensively (version 1 when unstamped):
- nfl_game_sim._deep_profiles refuses a nightly payload of another version;
- nfl_deep_season stamps the version in its payload;
- nfl_deep_data.py is in pool_cache._CODE_FILES.

`load(v2)` builds the patched module in memory (the switch set either way) for the guards and the downstream
evaluation, so neither needs the frozen file edited.

usage: python3 research/deep_anchor_patch.py make <flipped nfl_deep_data.py>   # writes the patch (once)
       python3 research/deep_anchor_patch.py check                              # git apply --check
"""
import difflib
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = os.path.join(ROOT, "research", "data", "deep_anchor_flip.patch")
TARGET = "nfl_deep_data.py"
DEEP_DATA_PINNED_SHA = "dff6756fb9ceb5a746750557aaef3d8424a1b57e953ab6aef8cd0abd4375b022"   # the read's file
DEEP_DATA_FLIPPED_SHA = "cb64ce6e077e62ec9ec2c91de5441428b4bb44c53e000794797199a67e5c0ced"  # after the patch


def make(flipped_path):
    with open(os.path.join(ROOT, TARGET)) as fh:
        old = fh.read().splitlines(keepends=True)
    with open(flipped_path) as fh:
        new = fh.read().splitlines(keepends=True)
    diff = difflib.unified_diff(old, new, fromfile=f"a/{TARGET}", tofile=f"b/{TARGET}")
    with open(PATCH, "w") as fh:
        fh.writelines(diff)


def patched_source():
    """The frozen file with the patch applied, through git apply in a scratch directory (the file itself once the
    flip has landed)."""
    with open(os.path.join(ROOT, TARGET)) as fh:
        cur = fh.read()
    if hashlib.sha256(cur.encode()).hexdigest() == DEEP_DATA_FLIPPED_SHA:
        return cur
    d = tempfile.mkdtemp(prefix="deep_anchor_patch_")
    try:
        shutil.copy(os.path.join(ROOT, TARGET), os.path.join(d, TARGET))
        r = subprocess.run(["git", "apply", PATCH], cwd=d, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"the flip patch no longer applies: {r.stderr.strip()}")
        with open(os.path.join(d, TARGET)) as fh:
            return fh.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def flipped_sha():
    return hashlib.sha256(patched_source().encode()).hexdigest()


def load(v2=True):
    """The patched nfl_deep_data as a module object (not registered in sys.modules), switch set to `v2`."""
    src = patched_source()
    if not v2:
        src = src.replace("\nANCHOR_V2 = True\n", "\nANCHOR_V2 = False\n", 1)
    m = types.ModuleType("nfl_deep_data")
    m.__file__ = os.path.join(ROOT, TARGET)
    exec(compile(src, m.__file__, "exec"), m.__dict__)
    return m


def check():
    r = subprocess.run(["git", "apply", "--check", PATCH], cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "make":
        make(sys.argv[2])
        print("written", PATCH, "flipped sha256", flipped_sha())
    else:
        print(check())
