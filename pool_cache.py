"""The simulated worlds, cached on the PC's disk (2026-11-26; design and the
owner's requirements in research/reports/dfs_cache_evaluator.md, stage 1).

`nfl_dfs_sim.player_pool` already caches a slate's worlds for 30 minutes, in
memory, inside one process. Every PC tournament build -- the owner's picked
builds included -- is a fresh process, so each one simulated the same games
again (158 s of the Thanksgiving MEGA's 30-minute build), and a picked build
was never guaranteed the plain board's worlds. This layer keeps them on disk
under a key that changes with anything that could change them, and with
nothing else.

The key (`key`) is a sha256 over the call (season, week, n, preseason, teams,
model, seed, xside, discrete), CACHE_VERSION, the simulator's source code and
deep tables, and the INPUTS the worlds are drawn from: the week's games as
Sleeper projected them (usage-adjusted, injury tags in), the engine's week
context (deep profiles, drive levels, weight) and the defense and kicker
projections. A new projection, injury tag or line of simulator code is a new
key; nothing is invalidated by the clock.

An entry is a directory: manifest.json (the call, the hashes, the engine
report, every player's fields but his array, the projection snapshot, the
arrays' dtype / scale / shape / sha256) and arrays.npy (players x worlds).
Scores are stored as int32 hundredths when every value round-trips exactly --
checked on write, element by element -- and as float64 otherwise; int32
hundredths hold +/-21 million points, so nothing a box score can produce
overflows, and a captain's 1.5x is applied downstream on the floats as it
always was. Writes are atomic (a temporary directory, then a rename); every
load checks the arrays' sha256 and treats a mismatch as a miss (ledgered
POOLCACHE-integrity, the entry deleted); KEEP_ENTRIES and MAX_BYTES bound the
store, oldest pruned on each write; `clear()` empties it. The arrays are read
memory-mapped.

Only the PC sets VIGIL_POOL_CACHE_DIR, so the server never writes or reads a
matrix (Render's memory is the constraint every gotcha in CLAUDE.md is about).

Cached worlds are not extended in place (asked 2026-11-26 for the build
profiles, pc_profiles: "extend cached worlds with new independent worlds
rather than regenerating"). An entry of n worlds cannot become the first n of
a larger one without changing what a call returns, for three reasons in the
simulator itself: (1) nfl_dfs_engine.simulate_game re-levels the two offences
(the anchor) with the call's own generator before it plays a world, so worlds
appended by a second call are played at a second, slightly different pair of
levels -- one board's worlds would no longer share one anchor, and the engine
report could name only one of them; (2) the box is dealt to the players with
draws sized to the whole count (deal_team on n at once), so a seeded call for
n2 worlds does not begin with the seeded call for n1's, and an extended seeded
pool would stop being reproducible draw for draw; (3) every player's floor,
ceiling, sim_mean and the defenses' and kickers' arrays are functions of all n
worlds. What a larger build saves instead is per GAME: build_ckpt keeps each
simulated game as it is played, so an interrupted generation resumes, and the
entry of each count is still simulated once and shared by every build at that
count. The disk limits are `limits()` (vigil-pc.cfg through the PC worker).
"""
import hashlib
import json
import os
import shutil
import time

import errlog

try:
    import numpy as np
except ImportError:                      # the server: the cache is the PC's only
    np = None

CACHE_VERSION = 1
KEEP_ENTRIES = 24
MAX_BYTES = 12 * 1024 ** 3               # 12 GB of the PC's disk at most
_CODE_FILES = ("nfl_dfs_sim.py", "nfl_dfs_engine.py", "nfl_deep_sim.py", "nfl_dfs_csim.py", "nfl_usage.py",
               "pool_cache.py", os.path.join("research", "data", "nfl_deep_tables.json"),
               os.path.join("research", "data", "csim_params.json"))
_HERE = os.path.dirname(os.path.abspath(__file__))
_code_sig = {}


def cache_dir():
    return os.environ.get("VIGIL_POOL_CACHE_DIR") or None


def enabled():
    return bool(cache_dir()) and np is not None


def code_sig():
    """sha256 over the simulator's source and data files, once per process."""
    if "sig" not in _code_sig:
        h = hashlib.sha256()
        for rel in _CODE_FILES:
            p = os.path.join(_HERE, rel)
            h.update(rel.encode())
            try:
                with open(p, "rb") as f:
                    h.update(f.read())
            except OSError:
                h.update(b"<missing>")
        _code_sig["sig"] = h.hexdigest()
    return _code_sig["sig"]


def _canon(obj):
    """A JSON text that is the same for equal data whatever its dict order;
    anything JSON cannot say (a numpy number, a tuple) by its repr/str."""
    def norm(x):
        if isinstance(x, dict):
            return {str(k): norm(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [norm(v) for v in x]
        if np is not None and isinstance(x, np.ndarray):
            return {"__nd__": x.dtype.str, "shape": list(x.shape), "sha": hashlib.sha256(x.tobytes()).hexdigest()}
        if np is not None and isinstance(x, np.generic):
            return x.item()
        if isinstance(x, float) and x != x:
            return "nan"
        return x
    return json.dumps(norm(obj), sort_keys=True, default=str, separators=(",", ":"))


def inputs_hash(**inputs):
    return hashlib.sha256(_canon(inputs).encode()).hexdigest()


def key(call, inputs_sig):
    """The entry's name: the call, the cache version, the code and the inputs."""
    h = hashlib.sha256()
    h.update(_canon({"v": CACHE_VERSION, "call": call}).encode())
    h.update(code_sig().encode())
    h.update(inputs_sig.encode())
    return h.hexdigest()[:40]


def _sha_file(path, block=1 << 24):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class Hundredths:
    """One player's worlds held as int32 hundredths while a COMPACT pool is being built
    (nfl_dfs_sim.player_pool(compact=True)): 4 bytes a score against a Python float's ~32 in the list the
    pool used to carry. Made only by `squeeze`, and only when every value round-trips exactly, so
    `values()` is the float64 array it was made from, bit for bit."""
    __slots__ = ("k",)

    def __init__(self, k):
        self.k = k

    def __len__(self):
        return len(self.k)

    def values(self):
        return self.k / 100.0


def squeeze(a):
    """A float64 array of scores rounded to 2 dp -> Hundredths when it round-trips exactly (encode's own
    test), else the float64 array as it is."""
    a = np.asarray(a, dtype=np.float64)
    k = np.rint(a * 100.0)
    if np.all(np.abs(k) < 2 ** 31 - 1) and np.array_equal(k / 100.0, a):
        return Hundredths(k.astype(np.int32))
    return a


def as_float32(a):
    """A pool array of any form (a list of floats, a float64 array, Hundredths) as the float32 row the
    tournament's score matrix X holds -- the same values np.asarray(list, dtype=float32) gives."""
    if isinstance(a, Hundredths):
        return a.values().astype(np.float32)
    return np.asarray(a, dtype=np.float32)


def has_array(a):
    """Whether a pool entry carries worlds: a non-empty list, array or Hundredths (an array's truth value
    is ambiguous, so `if p.get("arr")` cannot read a compact pool)."""
    return a is not None and len(a) > 0


def encode(arrs):
    """(matrix, dtype, scale) for equal-length score rows (lists, float64 arrays or Hundredths): int32
    hundredths when every value round-trips exactly, else float64.

    Row by row (2026-11-26): the old form built the whole float64 matrix, its rint and its k/100 at once --
    three full float64 copies of every score beside the pool, ~7 GB of a deep build's peak -- for the
    same answer. The result is the old function's exactly: a row that does not round-trip sends every
    row to float64, as the whole-matrix test did."""
    rows = list(arrs)
    if not rows:
        return np.asarray([], dtype=np.float64).astype(np.int32), "int32", 100
    n = len(rows[0])
    mat = np.empty((len(rows), n), dtype=np.int32)
    exact = True
    for i, r in enumerate(rows):
        if isinstance(r, Hundredths):
            if len(r) != n:
                raise ValueError("score rows of unequal length")
            mat[i] = r.k
            continue
        a = np.asarray(r, dtype=np.float64)
        if a.shape != (n,):
            raise ValueError("score rows of unequal length")
        k = np.rint(a * 100.0)
        if not (np.all(np.abs(k) < 2 ** 31 - 1) and np.array_equal(k / 100.0, a)):
            exact = False
            break
        mat[i] = k
    if exact:
        return mat, "int32", 100
    del mat
    m = np.empty((len(rows), n), dtype=np.float64)
    for i, r in enumerate(rows):
        m[i] = r.values() if isinstance(r, Hundredths) else np.asarray(r, dtype=np.float64)
    return m, "float64", 1


def decode_row(row, dtype, scale):
    """One player's array back as the list of floats the pool carries."""
    if dtype == "int32":
        return (np.asarray(row, dtype=np.float64) / float(scale)).tolist()
    return np.asarray(row, dtype=np.float64).tolist()


def decode_f32(row, dtype, scale):
    """One player's array as a compact pool carries it: the float32 row of X, the same values as
    np.asarray(decode_row(...), dtype=float32)."""
    if dtype == "int32":
        return (np.asarray(row, dtype=np.float64) / float(scale)).astype(np.float32)
    return np.asarray(row, dtype=np.float64).astype(np.float32)


def save(k, pool, call, engine_report=None, snapshot=None):
    """Write the pool under key `k`, atomically; prune past the bounds.
    Returns the entry's size in bytes, or None when nothing was written (no
    cache dir, no numpy, players with arrays of unequal length)."""
    d = cache_dir()
    if not d or np is None or not pool:
        return None
    names = [nm for nm, p in pool.items() if has_array(p.get("arr"))]
    lens = {len(pool[nm]["arr"]) for nm in names}
    if len(lens) != 1:
        errlog.note("POOLCACHE-shape", msg=f"unequal array lengths {sorted(lens)[:5]}: not cached")
        return None
    os.makedirs(d, exist_ok=True)
    final = os.path.join(d, k)
    if os.path.isdir(final):
        return None
    tmp = f"{final}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
    try:
        os.makedirs(tmp)
        mat, dtype, scale = encode([pool[nm]["arr"] for nm in names])
        apath = os.path.join(tmp, "arrays.npy")
        np.save(apath, mat, allow_pickle=False)
        meta = {"version": CACHE_VERSION, "key": k, "call": call, "created": time.time(),
                "code_sig": code_sig(), "dtype": dtype, "scale": scale, "shape": list(mat.shape),
                "sha256": _sha_file(apath), "engine_report": engine_report,
                "players": [{"name": nm, **{f: v for f, v in pool[nm].items() if f != "arr"}} for nm in names],
                "no_array": {nm: dict(p) for nm, p in pool.items() if not has_array(p.get("arr"))},   # as it was
                "snapshot": snapshot}
        with open(os.path.join(tmp, "manifest.json"), "w") as f:
            f.write(_canon(meta))
        os.replace(tmp, final)
    except Exception as e:
        errlog.note("POOLCACHE-write", e, path=k)
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    prune()
    return entry_bytes(final)


def load(k, compact=False):
    """(pool, engine_report, manifest) for key `k`, or None on a miss or a
    failed integrity check (the entry is then removed). `compact`: each
    player's worlds as the float32 row the tournament reads (decode_f32),
    straight off the memory-mapped matrix, never a list of floats."""
    d = cache_dir()
    if not d or np is None:
        return None
    path = os.path.join(d, k)
    mpath, apath = os.path.join(path, "manifest.json"), os.path.join(path, "arrays.npy")
    if not (os.path.isfile(mpath) and os.path.isfile(apath)):
        return None
    try:
        with open(mpath) as f:
            meta = json.load(f)
        if meta.get("version") != CACHE_VERSION or meta.get("key") != k:
            raise ValueError("manifest does not match its key")
        if _sha_file(apath) != meta.get("sha256"):
            raise ValueError("arrays sha256 mismatch")
        mat = np.load(apath, mmap_mode="r", allow_pickle=False)
        if list(mat.shape) != list(meta["shape"]) or len(meta["players"]) != mat.shape[0]:
            raise ValueError("arrays shape does not match the manifest")
        pool = {}
        for i, p in enumerate(meta["players"]):
            rec = {f: v for f, v in p.items() if f != "name"}
            rec["arr"] = (decode_f32 if compact else decode_row)(mat[i], meta["dtype"], meta["scale"])
            pool[p["name"]] = rec
        for nm, rec in (meta.get("no_array") or {}).items():
            pool[nm] = dict(rec)
        del mat
        os.utime(path, None)                 # recently used: pruned last
        return pool, meta.get("engine_report"), meta
    except Exception as e:
        errlog.note("POOLCACHE-integrity", e, path=k)
        shutil.rmtree(path, ignore_errors=True)
        return None


def entry_bytes(path):
    tot = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                tot += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    return tot


def entries():
    """[(path, mtime, bytes)] of the complete entries, newest first."""
    d = cache_dir()
    if not d or not os.path.isdir(d):
        return []
    out = []
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if ".tmp-" in name or not os.path.isdir(p) or not os.path.isfile(os.path.join(p, "manifest.json")):
            continue
        out.append((p, os.path.getmtime(p), entry_bytes(p)))
    out.sort(key=lambda r: -r[1])
    return out


def limits():
    """(entries, bytes) the store is held to: KEEP_ENTRIES and MAX_BYTES unless
    the PC worker set VIGIL_POOL_CACHE_ENTRIES / VIGIL_POOL_CACHE_GB from
    vigil-pc.cfg ("cache_entries", "cache_gb", or the build profile's: a
    550,000-world deep pool is ~1.1 GB an entry against a standard one's
    0.36). An unreadable value is the default, never zero."""
    keep, mb = KEEP_ENTRIES, MAX_BYTES
    try:
        v = int(os.environ.get("VIGIL_POOL_CACHE_ENTRIES") or 0)
        keep = v if v > 0 else keep
    except ValueError:
        pass
    try:
        g = float(os.environ.get("VIGIL_POOL_CACHE_GB") or 0)
        mb = int(g * 1024 ** 3) if g > 0 else mb
    except ValueError:
        pass
    return keep, mb


def prune(keep=None, max_bytes=None):
    """Drop the oldest entries past the limits (`limits`), and any temporary
    directory an interrupted write left more than an hour ago."""
    keep = limits()[0] if keep is None else keep
    max_bytes = limits()[1] if max_bytes is None else max_bytes
    d = cache_dir()
    if not d or not os.path.isdir(d):
        return 0
    gone = 0
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if ".tmp-" in name and time.time() - os.path.getmtime(p) > 3600:
            shutil.rmtree(p, ignore_errors=True)
            gone += 1
    total = 0
    for i, (p, _mt, b) in enumerate(entries()):
        total += b
        if i >= keep or total > max_bytes:
            shutil.rmtree(p, ignore_errors=True)
            gone += 1
    return gone


def clear():
    """Explicit invalidation: every entry gone."""
    d = cache_dir()
    if d and os.path.isdir(d):
        for name in os.listdir(d):
            shutil.rmtree(os.path.join(d, name), ignore_errors=True)
