"""Shared compute artifacts — the contract between the server and the PC worker.

Everything heavy this app computes lands as a flat pickle file in one of three
stores, each read by freshness (mtime) wherever it is consumed:

  gamesim  {pk}.pkl               per-game MLB sims (combo maker, warm loop)
  boards   {name}.pkl             every boardshare board (NFL sims/boards, golf,
                                  LoL, NBA, NHL, tennis, UFC, futures, kalshi idx)
  deep     {key}.pkl              deep_cache: the nightly 4,000-season run
                                  ("mlb_deep"), day-history records, futures sims

That uniformity is the whole offload design: the PC runs the SAME builder
functions against its own local stores, then uploads any file fresher than the
server's copy. Adoption is automatic — boardshare.get, deep_cache.load and
_sim_disk_get already prefer the freshest file on disk, and deep_cache's
nightly scheduler judges "already ran today" from the saved file's timestamp,
so a PC-uploaded run makes the server skip its own multi-hour rebuild with no
scheduler changes at all.

SCHEMA is the version gate for the whole contract: the server refuses uploads
whose schema differs, so a stale (or ahead-of-deploy) PC checkout is ignored,
never adopted. BUMP IT in the same commit as any change to what these pickles
contain.
"""

import os
import re

SCHEMA = 1

_NAME_RE = re.compile(r"^[\w.,@=+-]{1,140}\.pkl$")


def dir_for(kind):
    """The store directory for an artifact kind, honoring the same env vars the
    consumers read. None for an unknown kind."""
    if kind == "gamesim":
        import baseball
        return baseball._SIM_DISK
    if kind == "boards":
        import boardshare
        return boardshare._DIR
    if kind == "deep":
        import deep_cache
        return deep_cache.CACHE_DIR
    return None


def valid_name(name):
    """A bare flat filename — no separators, no traversal, bounded length."""
    return bool(name) and bool(_NAME_RE.match(name)) and "/" not in name and "\\" not in name


def ages(kind):
    """{filename: age_seconds} for every artifact in a store."""
    import time
    d = dir_for(kind)
    out = {}
    if not d or not os.path.isdir(d):
        return out
    now = time.time()
    for name in os.listdir(d):
        if not valid_name(name):
            continue
        try:
            out[name] = round(now - os.stat(os.path.join(d, name)).st_mtime, 1)
        except OSError:
            continue
    return out


def write_raw(kind, name, data):
    """Adopt an externally-computed artifact: atomic temp+rename into the store
    the consumers already read. Caller has authenticated and schema-checked."""
    import tempfile
    d = dir_for(kind)
    if not d or not valid_name(name):
        raise ValueError("bad kind/name")
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, os.path.join(d, name))
