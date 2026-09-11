"""What code produced a research artifact.

Every stage used to stamp `git rev-parse HEAD`, which is the commit the tree
was sitting ON while the research code itself was still uncommitted. So
`csim_params.json` says it came from fc10b3e, and fc10b3e does not contain
`research/stage2e.py` at all: the fitting script and the artifact both arrive
one commit later. Anyone checking out the stamped hash to reproduce the
numbers finds no script to run, and -- worse for an audit -- git history
cannot by itself prove the fitting code and the frozen parameters existed
before the holdout season was opened.

There was no leakage: stage2e reads 2022-2024 only, and the params file is
two minutes older than the validation file. But "no leakage" resting on file
timestamps is weaker than it needs to be, so the stamp now identifies the
source that actually ran:

  commit       the HEAD the tree sits on, as before
  dirty        whether the tracked tree differs from that commit
  source_sha   SHA-256 over the sorted contents of the Python that produced
               the artifact (the research package plus the production modules
               it imports), so two runs match if and only if the code matched
  files        how many files went into that hash

`require_clean()` is the discipline for anything whose ORDER matters -- a
parameter freeze before a holdout read. It refuses to run against a dirty
tree, which forces the sequence the audit asked for: commit the code, run the
fit, commit the frozen artifact, only then open the holdout.
"""
import hashlib
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# the production modules the research stages import; the artifact's meaning
# depends on these exactly as much as on the research scripts themselves
_PROD = ("dfs_tourney.py", "nfl_dfs_sim.py", "nfl_dfs_csim.py", "nfl_recon.py", "nfl_dfs.py")


def _git(*args):
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except Exception:
        return ""


def commit():
    return _git("rev-parse", "--short", "HEAD") or "unknown"


def dirty():
    """True when a tracked file differs from HEAD, so the stamped commit is
    not the code that ran."""
    return bool(_git("status", "--porcelain", "--untracked-files=no"))


def source_files():
    out = []
    rp = os.path.join(ROOT, "research")
    for fn in sorted(os.listdir(rp)):
        if fn.endswith(".py"):
            out.append(os.path.join(rp, fn))
    for fn in _PROD:
        p = os.path.join(ROOT, fn)
        if os.path.exists(p):
            out.append(p)
    return out


def source_sha():
    """One hash over the code that produces research numbers. Sorted by path
    and fed the path with the bytes, so a rename is a different hash."""
    h = hashlib.sha256()
    files = source_files()
    for p in files:
        h.update(os.path.relpath(p, ROOT).encode())
        with open(p, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest(), len(files)


#: what an artifact has to say about itself before its numbers mean anything
REQUIRED = ("commit", "dirty", "source_sha", "source_files", "seed", "model", "data_split")


def stamp(seed=None, model=None, data_split=None, **extra):
    """The provenance block every research artifact carries.

    A hash of the source is not enough on its own: the same code answers
    differently on a different seed, a different simulator or a different
    slice of seasons, so those three travel with it. `data_split` is the
    seasons or worlds the numbers were computed on -- "2022-2024 train",
    "2025 holdout", "7,500 build / 7,500 held out" -- which is the field that
    makes a blind result legible as blind.
    """
    sha, n = source_sha()
    out = {"commit": commit(), "dirty": dirty(), "source_sha": sha, "source_files": n,
           "seed": seed, "model": model, "data_split": data_split}
    out.update(extra)
    return out


def complete(block):
    """True when a stamp names everything in REQUIRED (a None seed is a real
    answer -- unseeded -- so only a missing KEY is incomplete)."""
    return isinstance(block, dict) and all(k in block for k in REQUIRED)


def require_clean(what):
    """Refuse to produce an order-sensitive artifact from an uncommitted
    tree. A parameter freeze that a holdout is later validated against has to
    be provable from history, not from the modification time of a file."""
    if dirty():
        raise RuntimeError(
            f"{what} must be produced from a committed tree so the freeze is provable: "
            "commit the research code first, then run this, then commit the artifact, "
            "and only then open the holdout")
