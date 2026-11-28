"""Registered reads run from their registration commits (option (c), approved by the owner 2026-11-27).

Nine registrations pin production's engine files by sha256 (nfl_deep_sim.py in all nine, nfl_dfs_engine.py in
the coaching DFS read's): R1-R6 (R5 twice: the 2028 original and the 2027 amendment), C4 and the coaching DFS
read. A fix to the engine (research/reports/fumble_td_fix.md, the fumble-return touchdown) would otherwise break
every one of them. So each read runs from a checkout of ITS registration commit, where the pinned bytes are,
and the current tree's guards check the pins there instead of in the live files:

- every file a registration pins hashes to its pin AT THE REGISTRATION COMMIT (`git show <commit>:<path>`);
- the registration commit is the one that added the registration file, and the registration file is unchanged
  since (live bytes == the commit's bytes: a prereg is never edited);
- in the LIVE tree a pinned file must still be the pinned bytes, with two kinds of exception, each named here:
  * STAGED flips (nfl_deep_sim.py, nfl_dfs_engine.py, nfl_deep_data.py): exactly the flipped bytes of a staged
    patch, and only once the deep-weight read's result (research/data/nfl_deep_weight_read.json) is committed
    and triggered -- the read grades the deep engine as it stood;
  * CURRENT GUARDS (tests/guards_auto_nfl_coach*.py): a registration pins the guard file it was registered
    with, and that copy is verified at the commit; the live copy is the current tree's checker and moves when
    a check has to (this file was the first such move).
  Anything else that differs live fails, as before: the coaching engines and research scripts stay frozen.

A guard therefore still fails when the registered version is lost or altered (a missing commit, a shallow
clone -- CI fetches the whole history for this -- a rewritten history, an edited prereg) and when a frozen file
moves in the live tree.

`prepare` builds the read's tree: research/reports/registered_reads_runbook.md.

usage: python3 research/registered_pins.py verify                     # every registration, at its commit
       python3 research/registered_pins.py prepare <prereg> <dir>     # a worktree at the commit, inputs added
"""
import contextlib
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG_DIR = os.path.join("research", "data", "nfl_coach")
DEEP_READ = os.path.join("research", "data", "nfl_deep_weight_read.json")

# prereg -> (the commit that added it, the read's entry point). Recorded 2026-11-28 from `git log
# --diff-filter=A`; R1's commit is the one its report names (eaca95d), R2 dfc026e, R3 59b15fc, R4 c569318,
# R6 35809d1, as CLAUDE.md records them.
REGISTRATIONS = {
    "research/data/nfl_coach/prereg_r1.json": ("eaca95d8076fa177c47aaa4eae9b2ddc348f7c89", "research/nfl_coach_r1_read.py"),
    "research/data/nfl_coach/prereg_r2.json": ("dfc026e9296b6fa16835bcaa2b3f6cc0f7e31604", "research/nfl_coach_r2_read.py"),
    "research/data/nfl_coach/prereg_r3.json": ("59b15fcf8ead70aaa5892cab4257670ab1e06555", "research/nfl_coach_r3_read.py"),
    "research/data/nfl_coach/prereg_r4.json": ("c569318a31a685d0becc94090bb6b5af7f6c9c5f", "research/nfl_coach_r4_read.py"),
    "research/data/nfl_coach/prereg_r5.json": ("79f2bee70a85096c7f4756bf00cd063d8eb0fb4b", "research/nfl_coach_r5_read.py"),
    "research/data/nfl_coach/prereg_r5_2027.json": ("416ba98212b203fcfd5435affd7cbe4c5da3dae3",
                                                    "research/nfl_coach_r5_read_2027.py"),
    "research/data/nfl_coach/prereg_r6.json": ("35809d1521af673903aa69f0d61dfde06eec1ddd", "research/nfl_coach_r6_read.py"),
    "research/data/nfl_coach/prereg_c4.json": ("33c49ab5853b05358631d5499258075b0dafd325", "research/nfl_coach_c4_confirm.py"),
    "research/data/nfl_coach/prereg_dfs.json": ("33c49ab5853b05358631d5499258075b0dafd325", "research/nfl_coach_dfs_read.py"),
}
CURRENT_GUARDS = "tests/guards_auto_nfl_coach"           # tests/guards_auto_nfl_coach*.py


def _git(*args, binary=False):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=not binary)
    return r.returncode, r.stdout


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def live_sha(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    with open(p, "rb") as fh:
        return sha_bytes(fh.read())


def sha_at(commit, rel):
    """sha256 of `rel` as committed at `commit`, or None when the commit or the path is not there."""
    rc, out = _git("show", f"{commit}:{rel}", binary=True)
    return sha_bytes(out) if rc == 0 else None


def added_by(rel):
    rc, out = _git("log", "--diff-filter=A", "--format=%H", "--", rel)
    lines = out.split() if rc == 0 else []
    return lines[-1] if lines else None


def deep_read_committed():
    """The gate: the deep-weight read's result tracked and triggered (the read writes it only after its 100th
    game). Until then production is the engine the read grades."""
    rc, _ = _git("ls-files", "--error-unmatch", DEEP_READ)
    if rc != 0 or not os.path.exists(os.path.join(ROOT, DEEP_READ)):
        return False
    try:
        with open(os.path.join(ROOT, DEEP_READ)) as fh:
            return bool(json.load(fh).get("triggered"))
    except (OSError, ValueError):
        return False


def staged_flips():
    """{file: the flipped sha256 of a staged patch} for every staged engine fix."""
    sys.path.insert(0, ROOT)
    from research import deep_anchor_patch as A
    from research import fumble_td_patch as F
    out = dict(F.FLIPPED_SHA)
    out["nfl_deep_data.py"] = A.DEEP_DATA_FLIPPED_SHA
    return out


def live_ok(rel, pin):
    """The live file may stand: the pinned bytes, or (behind the deep-weight read's gate) a staged flip's."""
    here = live_sha(rel)
    if here == pin:
        return True
    flips = staged_flips()
    return rel in flips and here == flips[rel] and deep_read_committed()


def at_commit_problems(prereg):
    """Why the registration's pinned version cannot be recovered from its commit (empty when it can)."""
    commit = REGISTRATIONS[prereg][0]
    out = []
    if _git("cat-file", "-e", f"{commit}^{{commit}}")[0] != 0:
        return [f"registration commit {commit[:9]} is not in this clone (a shallow clone or rewritten history)"]
    if added_by(prereg) != commit:
        out.append(f"{prereg} was not added by {commit[:9]}")
    if sha_at(commit, prereg) != live_sha(prereg):
        out.append(f"{prereg} differs from its registration commit (a prereg is never edited)")
    with open(os.path.join(ROOT, prereg)) as fh:
        reg = json.load(fh)
    for rel, pin in (reg.get("files") or {}).items():
        if sha_at(commit, rel) != pin:
            out.append(f"{rel} at {commit[:9]} is not its registered sha256")
    return out


def live_problems(prereg):
    """Pinned files that moved in the live tree without being a named exception."""
    with open(os.path.join(ROOT, prereg)) as fh:
        reg = json.load(fh)
    out = []
    for rel, pin in (reg.get("files") or {}).items():
        if live_ok(rel, pin):
            continue
        if rel.startswith(CURRENT_GUARDS) and rel.endswith(".py"):
            continue
        out.append(rel)
    return out


def moved(prereg):
    """Every problem with a registration: its pinned version lost or altered at its commit, or a frozen file
    moved live. What the current tree's guards used to call `moved` (the live files against the pins)."""
    return at_commit_problems(prereg) + [f"{rel} moved in the live tree" for rel in live_problems(prereg)]


def verify_all():
    return {p: moved(p) for p in REGISTRATIONS}


@contextlib.contextmanager
def registered_view(prereg, *modules):
    """Each read module's ROOT pointed, for the block, at a tree holding the registration commit's pinned files
    and the registrations it held (what `prepare` checks out), so a guard can ask whether the read WOULD run
    there; restored on exit. Paths a module computed at import (its output, the prereg) stay the live ones."""
    commit = REGISTRATIONS[prereg][0]
    with open(os.path.join(ROOT, prereg)) as fh:
        reg = json.load(fh)
    d = tempfile.mkdtemp(prefix="registered_view_")
    saved = [(m, m.ROOT) for m in modules]
    try:
        for rel in sorted(set(reg.get("files") or {}) | set(REGISTRATIONS)):
            rc, out = _git("show", f"{commit}:{rel}", binary=True)
            if rc == 0:
                os.makedirs(os.path.dirname(os.path.join(d, rel)) or d, exist_ok=True)
                with open(os.path.join(d, rel), "wb") as fh:
                    fh.write(out)
        for m in modules:
            m.ROOT = d
        yield d
    finally:
        for m, r in saved:
            m.ROOT = r
        shutil.rmtree(d, ignore_errors=True)


def pinning(files):
    """Every committed registration under research/data (prereg*.json / protocol*.json, any depth) whose
    `files` pins one of `files`: what option (c) must cover when those files change."""
    out = set()
    for pat in ("prereg*.json", "protocol*.json"):
        for f in glob.glob(os.path.join(ROOT, "research", "data", "**", pat), recursive=True):
            try:
                with open(f) as fh:
                    pins = json.load(fh).get("files")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(pins, dict) and any(rel in pins for rel in files):
                out.add(os.path.relpath(f, ROOT))
    return out


def earlier_reads():
    """Committed results of reads that later reads require (copied into a read's tree by `prepare`)."""
    pats = ("read_*.json", "confirm_c4_look*.json")
    return sorted(os.path.relpath(f, ROOT) for pat in pats for f in glob.glob(os.path.join(ROOT, REG_DIR, pat)))


def prepare(prereg, dest):
    """A worktree at the registration commit, with the earlier reads this branch holds committed on top of it
    (a local commit, never pushed: the read refuses a dirty tree), every pin checked at its HEAD."""
    commit, entry = REGISTRATIONS[prereg]
    if _git("worktree", "add", "--detach", dest, commit)[0] != 0:
        raise SystemExit(f"git worktree add {dest} {commit[:9]} failed")
    copied = []
    for rel in earlier_reads():
        if sha_at(commit, rel) is None:
            os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
            shutil.copy(os.path.join(ROOT, rel), os.path.join(dest, rel))
            copied.append(rel)
    if copied:
        subprocess.run(["git", "add", *copied], cwd=dest, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "Inputs for a registered read: earlier reads copied byte for byte "
                        "from the work branch (never pushed)"], cwd=dest, check=True)
    with open(os.path.join(ROOT, prereg)) as fh:
        reg = json.load(fh)
    bad = []
    for rel, pin in (reg.get("files") or {}).items():
        with open(os.path.join(dest, rel), "rb") as fh:
            if sha_bytes(fh.read()) != pin:
                bad.append(rel)
    if bad:
        raise SystemExit(f"pinned files differ in {dest}: {bad}")
    print(f"ready: {dest} at {commit[:9]} (+{len(copied)} earlier read(s): {copied}); run "
          f"`cd {dest} && python3 {entry} <its arguments>`, then copy its output back to the work branch")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "verify":
        res = verify_all()
        for p, probs in res.items():
            print(("ok   " if not probs else "FAIL ") + p + ("" if not probs else f"  {probs}"))
        sys.exit(1 if any(res.values()) else 0)
    elif len(sys.argv) == 4 and sys.argv[1] == "prepare":
        prepare(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
