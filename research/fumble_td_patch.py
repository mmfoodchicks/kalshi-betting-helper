"""The fumble-return touchdown fix, STAGED as a patch (2026-11-28), because nfl_deep_sim.py must not move before
the deep-weight read.

The registered 100-game read (research/nfl_deep_weight_read.py) grades the deep engine AS IT STANDS: its rows
are the slate's shadow predictions, logged by production until the 100th game. So the fix cannot reach
production before research/data/nfl_deep_weight_read.json is committed. It lives in
research/data/fumble_td_flip.patch (nfl_deep_sim.py and nfl_dfs_engine.py), and the flip is ONE commit:

    git apply research/data/deep_anchor_flip.patch research/data/fumble_td_flip.patch
    # the live-file pins of the current-tree guards move to the flipped sha256s below and the anchor's, in the
    # same commit; research/data/nfl_deep_weight_read.json is already committed (guards DA2 and FT2)

The registrations that pin these files (R1-R6, C4, the coaching DFS read) are NOT moved: each read runs from a
checkout of its own registration commit (research/registered_pins.py, research/reports/
registered_reads_runbook.md), where the pinned bytes still are.

The patched files:
- nfl_deep_sim.py: FUMBLE_TD True, ENGINE_VERSION 2, the fitted chance a lost fumble from scrimmage is returned
  for a touchdown (kind x the snap's 20-yard zone, research/fumble_td_fit.py), scored as the pick-six is
  (td_ret, td_def, _return_td). With FUMBLE_TD False it is the old engine draw for draw.
- nfl_dfs_engine.py: SIM_MODEL_VERSION 3 (the boards' simulator stamp) and off_points' docstring.

`load(on)` builds the patched engine in memory (not registered in sys.modules), switch set either way, so the
guards and the D/ST evaluation never need the live file edited.

usage: python3 research/fumble_td_patch.py make <dir holding the two patched files>   # writes the patch (once)
       python3 research/fumble_td_patch.py check                                       # git apply --check
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
PATCH = os.path.join(ROOT, "research", "data", "fumble_td_flip.patch")
TARGETS = ("nfl_deep_sim.py", "nfl_dfs_engine.py")
# the read's files (the coaching registrations' pins too) and the bytes after the patch
PINNED_SHA = {"nfl_deep_sim.py": "22a221d7810495db3f8f7bb1bef7c4e16c3bbbf282902a95f133cecdabc29820",
              "nfl_dfs_engine.py": "ea910abe866c09c7232906e376174d8ec04cc00f48c099186f167fbf48f47ebd"}
FLIPPED_SHA = {"nfl_deep_sim.py": "9d5278385d5ef5cf88dda8e380bc7da794efe29d905f6cfe90a13316e235f49c",
               "nfl_dfs_engine.py": "41036ce610844b5d2b3c7f7382548ff50c78937dfea4247854b9edaff125577a"}


def _sha_text(s):
    return hashlib.sha256(s.encode()).hexdigest()


def live_sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def state():
    """'unpatched' (the read's bytes), 'flipped' (exactly the patch's), else 'other'."""
    shas = {rel: live_sha(rel) for rel in TARGETS}
    if all(shas[r] == PINNED_SHA[r] for r in TARGETS):
        return "unpatched"
    if all(shas[r] == FLIPPED_SHA[r] for r in TARGETS):
        return "flipped"
    return "other"


def make(patched_dir):
    out = []
    for rel in TARGETS:
        with open(os.path.join(ROOT, rel)) as fh:
            old = fh.read().splitlines(keepends=True)
        with open(os.path.join(patched_dir, rel)) as fh:
            new = fh.read().splitlines(keepends=True)
        out += list(difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    with open(PATCH, "w") as fh:
        fh.writelines(out)


def patched_sources():
    """{file: text} of the two files with the patch applied (git apply in a scratch directory; the files
    themselves once the flip has landed)."""
    if state() == "flipped":
        return {rel: open(os.path.join(ROOT, rel)).read() for rel in TARGETS}
    d = tempfile.mkdtemp(prefix="fumble_td_patch_")
    try:
        for rel in TARGETS:
            shutil.copy(os.path.join(ROOT, rel), os.path.join(d, rel))
        r = subprocess.run(["git", "apply", PATCH], cwd=d, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"the fumble-TD patch no longer applies: {r.stderr.strip()}")
        return {rel: open(os.path.join(d, rel)).read() for rel in TARGETS}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def flipped_shas():
    return {rel: _sha_text(s) for rel, s in patched_sources().items()}


def load(on=True):
    """The patched nfl_deep_sim as a module object (not in sys.modules), FUMBLE_TD set to `on`."""
    src = patched_sources()["nfl_deep_sim.py"]
    if not on:
        src = src.replace("\nFUMBLE_TD = True\n", "\nFUMBLE_TD = False\n", 1)
    m = types.ModuleType("nfl_deep_sim")
    m.__file__ = os.path.join(ROOT, "nfl_deep_sim.py")
    exec(compile(src, m.__file__, "exec"), m.__dict__)
    return m


def check():
    r = subprocess.run(["git", "apply", "--check", PATCH], cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "make":
        make(sys.argv[2])
        print("written", PATCH, "flipped sha256", flipped_shas())
    else:
        print(check())
