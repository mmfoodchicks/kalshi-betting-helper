"""Which commit, if any, can actually reconstruct an artifact's hashed source.

An artifact's `provenance.commit` is the HEAD the tree was sitting on. That is
NOT the same as "the commit whose source recomputes `source_sha`" -- and before
the contract repair on 2026-09-12 it could differ silently, because `dirty()`
ignored untracked files while `source_sha()` hashed them. An artifact could say
`commit: 96bf08b, dirty: false` while the module that produced it did not exist
at 96bf08b.

So this walks history and answers the question directly, per artifact:

  source_commit_verified   the FIRST commit whose tree recomputes the stamped
                           source_sha exactly, or null if no commit does

`null` is an honest and expected answer for an artifact produced from a tree that
was never committed in that exact state. It says: the numbers are internally
consistent and the hash is stable, but the code that produced them is not
recoverable from Git. That is strictly more information than `dirty: false` was
giving.

Historical artifacts are NOT regenerated. Their bytes are the evidence; this adds
a separate, verifiable record beside them.
"""
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")

ARTIFACTS = ("s5_rules.json", "s5_ablate.json", "s5_pairs.json", "s5_sens.json",
             "s5_tail.json")
MAX_COMMITS = 400


def _git(*args, binary=False):
    try:
        out = subprocess.check_output(["git", *args], cwd=ROOT,
                                      stderr=subprocess.DEVNULL)
        return out if binary else out.decode().strip()
    except Exception:
        return b"" if binary else ""


def sha_at(commit, prod):
    """Recompute provenance.source_sha() from a commit's tree.

    Mirrors source_files() exactly: research/*.py sorted by filename, then the
    production modules in their declared order, each contributing its
    repo-relative path bytes followed by its content bytes.
    """
    tree = _git("ls-tree", "--name-only", f"{commit}:research")
    rels = [f"research/{fn}" for fn in sorted(
        x for x in tree.splitlines() if x.endswith(".py"))]
    for fn in prod:
        if _git("cat-file", "-e", f"{commit}:{fn}") == "" and _git(
                "ls-tree", "--name-only", f"{commit}", "--", fn):
            rels.append(fn)
    h = hashlib.sha256()
    for rel in rels:
        blob = _git("show", f"{commit}:{rel}", binary=True)
        if not blob:
            return None, 0
        h.update(rel.encode())
        h.update(blob)
    return h.hexdigest(), len(rels)


def run(log=print):
    from research import provenance as P
    commits = _git("rev-list", f"--max-count={MAX_COMMITS}", "HEAD").splitlines()
    # oldest first, so "first commit that matches" means the earliest one
    commits = list(reversed(commits))
    by_sha = {}
    for c in commits:
        sha, n = sha_at(c, P._PROD)
        if sha and sha not in by_sha:
            by_sha[sha] = {"commit": c[:7], "source_files": n}
    out = {"meta": {"stage": "provenance verification: which commit reconstructs the source",
                    "commits_scanned": len(commits),
                    "head": _git("rev-parse", "HEAD")[:7],
                    "what_null_means": (
                        "no commit in the scanned range recomputes the stamped "
                        "source_sha, so the exact source snapshot that produced the "
                        "artifact is NOT reconstructible from Git. Expected for an "
                        "artifact run from an uncommitted tree; an honest answer, not "
                        "a failure."),
                    "why": ("provenance.commit is the HEAD the tree sat on. Before the "
                            "2026-09-12 contract repair, dirty() ignored untracked files "
                            "while source_sha() hashed them, so commit+dirty could not "
                            "establish that the stamped commit contained the code.")},
           "artifacts": {}}
    for a in ARTIFACTS:
        p = os.path.join(DATA, a)
        if not os.path.exists(p):
            continue
        prov = json.load(open(p))["meta"]["provenance"]
        hit = by_sha.get(prov["source_sha"])
        out["artifacts"][a] = {
            "stamped_commit": prov["commit"],
            "stamped_dirty": prov["dirty"],
            "stamped_source_sha": prov["source_sha"],
            "stamped_source_files": prov["source_files"],
            "source_commit_verified": (hit or {}).get("commit"),
            "source_files_at_verified": (hit or {}).get("source_files"),
            "reconstructible_from_git": bool(hit)}
        r = out["artifacts"][a]
        log(f"[PV] {a:18s} stamped {r['stamped_commit']:>8} dirty={r['stamped_dirty']!s:5} "
            f"-> verified {r['source_commit_verified'] or 'NONE (not reconstructible)'}")
    with open(os.path.join(DATA, "prov_verify.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    run()
    print("provenance verification written")
