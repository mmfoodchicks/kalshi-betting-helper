"""Resumable tournament builds (2026-11-26). The owner: "Make expensive builds
safely resumable after closing vigil-pc.bat, including simulation generation,
candidate scoring and portfolio optimization." He closes the batch file
whenever he needs the PC, and a full classic board is the better part of an
hour (far more on a deep profile, pc_profiles).

What is kept, all on the PC's disk under VIGIL_CKPT_DIR (set by pc_worker
only, inside pc-simcache; the server and the guard suite never have it, so
every path below is the old code exactly when it is unset):

  games/<worlds key>/<game>.ck    one simulated game of a slate's worlds, as
      the engine returned it (nfl_dfs_sim.player_pool). The whole pool goes to
      pool_cache when the last game is played and these are then removed; a
      build stopped after six of ten games plays four on restart. The worlds
      key is pool_cache's (the call, the simulator's code, every input).
  builds/<build key>/             one tournament build:
      rng.json            the entropy the build's generator was started from.
          Production boards are UNSEEDED, so a restarted build used to draw a
          different field and different candidates, and nothing scored
          against them could be reused. Pinned here, the restart redraws the
          SAME field and candidates from the same cached worlds (FieldSampler
          is the same sample on any process count), and the passes below find
          their inputs unchanged. A seeded build keeps its seed.
      memo-<name>-<sig>.ck   a whole stage's answer (the best lineup of every
          generation world), keyed on a hash of everything the stage read.
      score-<sig>-<a>-<b>.ck   one world range of the scoring pass: its
          running (acc, acc2, opt) and the next world to score. Written by the
          worker that owns the range every SAVE_EVERY_S and at its end. A
          resumed range loads the sums and carries on from that world with
          the same chunking, so the additions happen in the same order and the
          result is the uninterrupted run's bit for bit.
      makeit-<sig>-<a>-<b>.ck  one finished world range of the portfolio
          pass's make-it matrix (float32, as computed).
  inputs/<request key>/inputs.ck   the build's FROZEN INPUTS (2026-11-27):
      everything it read live -- DraftKings' slate and contest details, the
      week, the worlds' inputs (Sleeper's lines, the deep profiles, the drive
      levels, the defenses and kickers) and the worlds cache key they make,
      the depth gate's answer, the roster state and the status classes --
      under a key of the REQUEST alone (the counts, the seed, the model, the
      pick, the code), never of a live input. The keys above are of the
      inputs, correctly, so before this a restart re-fetched inputs that had
      moved (Sleeper's lines move by the minute on a Saturday) and resumed
      nothing: the PC's deep probe of 2026-11-27 read `resumed: {}` after a
      kill at 40% of scoring and redid the 7,129-second build. See
      `inputs_usable` for when frozen inputs are used.

<sig> hashes every input of the pass -- the candidate, field and world
matrices byte for byte, the field's weights, the payout grids, the world
ranges and their chunking, and the code (dfs_tourney.py and this file) -- so a
changed input is a different file name: nothing stale can be read. Every file
is `VCK1` + the sha256 of its payload + the payload, written to a temporary
name and renamed; a file whose digest does not match (a torn write, a bad
sector) is ledgered CKPT-integrity, deleted and recomputed, never trusted.

Bounds: KEEP_BUILDS builds, MAX_BYTES in all and MAX_AGE_S, pruned oldest
first whenever a build opens ("checkpoint_gb" in vigil-pc.cfg sets
VIGIL_CKPT_GB). A pass whose checkpoint would not fit the budget is simply
not checkpointed (the make-it matrix of a research-profile board). A build's
directory is removed when its board lands (pc_worker calls `landed`).
"""
import hashlib
import io
import json
import os
import pickle
import shutil
import time

import errlog

try:
    import numpy as np
except ImportError:                      # the server: checkpoints are the PC's only
    np = None

CKPT_VERSION = 1
INPUTS_VERSION = 1
KEEP_BUILDS = 6
MAX_BYTES = 8 * 1024 ** 3                # 8 GB unless vigil-pc.cfg says otherwise
MAX_AGE_S = 8 * 86400                    # a slate is built inside its last eight days
SAVE_EVERY_S = 60.0                      # a scoring range's running sums: at most a minute of one core lost
_MAGIC = b"VCK1"
_HERE = os.path.dirname(os.path.abspath(__file__))
_CODE_FILES = ("dfs_tourney.py", "build_ckpt.py")
_code_sig = {}
# The owner's two operations (2026-11-27), named as he named them, on every
# checkpointed board's stamp (`checkpoint.inputs.operation`) and in its log.
RESUME = "Resume existing build"         # reuse frozen inputs, cached worlds and completed scoring chunks
FRESH = "Start fresh build"              # fetch current projections, injury information, salaries and market data
# Frozen inputs are used for at most six hours after the build first read
# them. Why six: the scheduled main-slate build's window is Saturday 18:00 to
# 23:59 MT (pc_worker._CL_WEEK_WINDOWS), so a build that window started can be
# resumed anywhere inside it; the longest build measured (the deep profile,
# 7,129-7,163 s on the PC, 2026-11-27) leaves four hours of interruption; and
# the board a resumed build lands is at most this much older than the inputs
# a fresh build would have read, against ~45 min-2 h for any build anyway.
# The 85-40-minute pre-lock rebuild never reads them (pc_worker passes
# fresh_inputs), and INACTIVES_S below holds every other build to the same.
INPUTS_MAX_AGE_S = 6 * 3600
# NFL inactives post 90 minutes before kickoff: inputs frozen before that are
# never used after it, whichever build asks (the pre-lock flag is the
# explicit form of the same rule for the build after the inactives).
INACTIVES_S = 90 * 60
KEEP_INPUTS = KEEP_BUILDS
# The files that turn those live reads into a build's inputs: a changed line
# in any is a new request key, so frozen inputs are never read by other code.
_INPUT_CODE_FILES = ("dk.py", "nfl_dfs.py", "nfl_dfs_sim.py", "simulate.py", "nfl_adp.py", "dfs_portfolio.py")
_POOL_KEYS = ("worlds_key", "games", "profiles", "drive", "weight", "dst", "kickers")
_SNAP_KEYS = ("as_of", "lock_ts", "slate", "details", "week", "pool")


def root():
    return os.environ.get("VIGIL_CKPT_DIR") or None


def enabled():
    return bool(root()) and np is not None


def max_bytes():
    """The disk budget: VIGIL_CKPT_GB (pc_worker sets it from vigil-pc.cfg's
    "checkpoint_gb") or MAX_BYTES."""
    try:
        gb = float(os.environ.get("VIGIL_CKPT_GB") or 0)
    except ValueError:
        gb = 0.0
    return int(gb * 1024 ** 3) if gb > 0 else MAX_BYTES


def code_sig():
    """sha256 over the scoring code, once per process: a changed line of
    dfs_tourney.py or of this file is a different checkpoint."""
    if "sig" not in _code_sig:
        h = hashlib.sha256()
        for rel in _CODE_FILES:
            h.update(rel.encode())
            try:
                with open(os.path.join(_HERE, rel), "rb") as f:
                    h.update(f.read())
            except OSError:
                h.update(b"<missing>")
        _code_sig["sig"] = h.hexdigest()
    return _code_sig["sig"]


def inputs_code_sig():
    """sha256 over the code that reads a build's inputs (_INPUT_CODE_FILES), once per process."""
    if "inputs" not in _code_sig:
        h = hashlib.sha256()
        for rel in _INPUT_CODE_FILES:
            h.update(rel.encode())
            try:
                with open(os.path.join(_HERE, rel), "rb") as f:
                    h.update(f.read())
            except OSError:
                h.update(b"<missing>")
        _code_sig["inputs"] = h.hexdigest()
    return _code_sig["inputs"]


def sig(*parts):
    """A sha256 over arrays (dtype, shape and every byte) and plain values,
    in order. The code signature and the version are always in."""
    h = hashlib.sha256()
    h.update(f"v{CKPT_VERSION}|{code_sig()}".encode())

    def feed(x):
        if np is not None and isinstance(x, np.ndarray):
            a = np.ascontiguousarray(x)
            h.update(f"<nd {a.dtype.str} {a.shape}>".encode())
            if a.size:
                h.update(a.reshape(-1).view(np.uint8))       # the bytes in place: no copy of a gigabyte matrix
        elif isinstance(x, dict):
            h.update(b"{")
            for k in sorted(x, key=str):
                h.update(str(k).encode() + b":")
                feed(x[k])
            h.update(b"}")
        elif isinstance(x, (list, tuple, range)):
            h.update(b"[")
            for v in x:
                feed(v)
                h.update(b",")
            h.update(b"]")
        elif np is not None and isinstance(x, np.generic):
            feed(x.item())
        else:
            h.update(f"<{type(x).__name__} {x!r}>".encode())
    for p in parts:
        feed(p)
        h.update(b"|")
    return h.hexdigest()


# ---- one verified file ----------------------------------------------------------
def write_file(path, payload):
    """`payload` (bytes) under `path`, atomically, its sha256 in the header.
    Never raises: a checkpoint that cannot be written costs only the resume."""
    tmp = f"{path}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(_MAGIC)
            f.write(hashlib.sha256(payload).digest())
            f.write(payload)
        os.replace(tmp, path)
        return True
    except Exception as e:
        errlog.note("CKPT-write", e, path=os.path.basename(path))
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def read_file(path):
    """The payload, or None when the file is absent; a file that fails its
    digest is ledgered, removed and read as absent."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            head = f.read(len(_MAGIC) + 32)
            payload = f.read()
        if head[:len(_MAGIC)] != _MAGIC or hashlib.sha256(payload).digest() != head[len(_MAGIC):]:
            raise ValueError("sha256 mismatch (a torn or damaged checkpoint)")
        return payload
    except Exception as e:
        errlog.note("CKPT-integrity", e, path=os.path.basename(path))
        try:
            os.remove(path)
        except OSError:
            pass
        return None


def _arrays_bytes(arrays):
    buf = io.BytesIO()
    np.savez(buf, **arrays)
    return buf.getvalue()


def _arrays_from(payload):
    with np.load(io.BytesIO(payload), allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


# ---- world ranges of a pass (called inside the scoring processes) ---------------
def pass_spec(build, name, pass_sig, save_bytes=0):
    """What a range function needs to checkpoint itself (a small picklable
    dict for _par_map's `extra`), or None when the build is not checkpointed
    or the pass's files (`save_bytes` in all) would not fit the budget."""
    if build is None:
        return None
    if save_bytes and save_bytes > max(0, max_bytes() - dir_bytes(build.path)):
        build.note(name, "not checkpointed: its files would not fit the checkpoint budget")
        return None
    return {"dir": build.path, "tag": f"{name}-{pass_sig[:32]}", "every": SAVE_EVERY_S}


def _range_path(spec, a, b):
    return os.path.join(spec["dir"], f"{spec['tag']}-{int(a)}-{int(b)}.ck")


def range_load(spec, a, b, chunk):
    """{next, arrays...} of a range's checkpoint, or None. The chunking is
    part of the answer's order of additions, so a file written under another
    chunk is not this range's (the pass signature already holds the ranges;
    this is the second lock)."""
    payload = read_file(_range_path(spec, a, b))
    if payload is None:
        return None
    try:
        d = _arrays_from(payload)
        meta = d.pop("_meta")
        if [int(v) for v in meta[:3]] != [int(a), int(b), int(chunk)] or not (a <= int(meta[3]) <= b):
            raise ValueError("range header does not match")
        d["next"] = int(meta[3])
        return d
    except Exception as e:
        errlog.note("CKPT-integrity", e, path=os.path.basename(_range_path(spec, a, b)))
        try:
            os.remove(_range_path(spec, a, b))
        except OSError:
            pass
        return None


def range_save(spec, a, b, chunk, nxt, **arrays):
    arrays["_meta"] = np.asarray([a, b, chunk, nxt], dtype=np.int64)
    return write_file(_range_path(spec, a, b), _arrays_bytes(arrays))


def pass_progress(spec, jobs):
    """(worlds already done, ranges finished, ranges in all) from the files on
    disk, read from their names and headers alone -- for the log and the
    board's stamp."""
    done = full = 0
    if spec:
        for a, b, chunk in jobs:
            d = range_load(spec, a, b, chunk)
            if d is not None:
                done += d["next"] - a
                full += int(d["next"] >= b)
    return done, full, len(jobs)


# ---- a build ---------------------------------------------------------------------
class Build:
    """One tournament build's checkpoint directory."""

    def __init__(self, key, path, fresh):
        self.key, self.path, self.fresh = key, path, fresh
        self.resumed = {}                # what this run took from disk, for the board's stamp
        self.inputs = None               # the frozen inputs' stamp (dfs_tourney._BuildInputs.stamp)

    def note(self, name, what):
        self.resumed[name] = what

    def rng_entropy(self):
        """The entropy this build's generator starts from: drawn once (the
        operating system's, as default_rng(None) draws it) and kept, so a
        restart redraws the same field and candidates."""
        p = os.path.join(self.path, "rng.json")
        payload = read_file(p)
        if payload is not None:
            try:
                ent = int(json.loads(payload.decode())["entropy"])
                self.note("rng", "the interrupted build's own")
                return ent
            except Exception as e:
                errlog.note("CKPT-integrity", e, path="rng.json")
        ent = int(np.random.SeedSequence().entropy)
        write_file(p, json.dumps({"entropy": str(ent), "created": time.time()}).encode())
        return ent

    def memo(self, name, memo_sig, fn):
        """fn()'s answer, from disk when this build already computed it on
        the same inputs. Plain Python and numpy only (pickled; the digest in
        the header is what makes a damaged file a miss)."""
        p = os.path.join(self.path, f"memo-{name}-{memo_sig[:32]}.ck")
        payload = read_file(p)
        if payload is not None:
            try:
                out = pickle.loads(payload)
                self.note(name, "from the checkpoint")
                return out
            except Exception as e:
                errlog.note("CKPT-integrity", e, path=os.path.basename(p))
        out = fn()
        try:
            write_file(p, pickle.dumps(out, protocol=4))
        except Exception as e:
            errlog.note("CKPT-write", e, path=os.path.basename(p))
        return out

    def stamp(self):
        return {"key": self.key, "resumed": dict(self.resumed), **({"inputs": self.inputs} if self.inputs else {})}


def open_build(*key_parts):
    """The checkpoint of the build these inputs name (created when new), or
    None where checkpoints are off. Opening prunes the store."""
    if not enabled():
        return None
    try:
        key = sig("build", *key_parts)[:40]
        path = os.path.join(root(), "builds", key)
        fresh = not os.path.isdir(path)
        os.makedirs(path, exist_ok=True)
        os.utime(path, None)             # in use: pruned last
        prune(keep_key=key)
        return Build(key, path, fresh)
    except Exception as e:
        errlog.note("CKPT-open", e)
        return None


def landed(board_or_key):
    """The build's board stands: its checkpoint is no longer needed. Takes the
    board (its `checkpoint` stamp) or the key."""
    key, rkey = board_or_key, None
    if isinstance(board_or_key, dict):
        stamp = board_or_key.get("checkpoint") or {}
        key, rkey = stamp.get("key"), (stamp.get("inputs") or {}).get("key")
    r = root()
    if r and _safe_key(rkey):
        inputs_drop(rkey)                # its frozen inputs go with it
    if not (r and _safe_key(key)):
        return False
    path = os.path.join(r, "builds", key)
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
        return True
    return False


def _safe_key(key):
    return bool(key) and isinstance(key, str) and key.isalnum()


def dir_bytes(path):
    tot = 0
    for rt, _dirs, files in os.walk(path):
        for fn in files:
            try:
                tot += os.path.getsize(os.path.join(rt, fn))
            except OSError:
                pass
    return tot


def _entries():
    """[(path, mtime, bytes, sub)] of every build, every slate's games and every
    build's frozen inputs, newest first."""
    out = []
    r = root()
    for sub in ("builds", "games", "inputs"):
        d = os.path.join(r, sub) if r else None
        if d and os.path.isdir(d):
            for name in os.listdir(d):
                p = os.path.join(d, name)
                if os.path.isdir(p):
                    out.append((p, os.path.getmtime(p), dir_bytes(p), sub))
    out.sort(key=lambda t: -t[1])
    return out


def prune(keep=None, limit=None, max_age=None, keep_key=None):
    """Oldest first past KEEP_BUILDS entries, the byte budget or MAX_AGE_S;
    the entry named `keep_key` (the build that is running) is never removed.
    Frozen inputs (a few MB a build) count toward the bytes, are held to
    KEEP_INPUTS on their own count -- so they never push a build or a slate's
    games out of the six -- and go once older than INPUTS_MAX_AGE_S, past
    which nothing may read them."""
    keep = KEEP_BUILDS if keep is None else keep
    limit = max_bytes() if limit is None else limit
    max_age = MAX_AGE_S if max_age is None else max_age
    gone, total, now = 0, 0, time.time()
    n_main = n_inp = 0
    for p, mt, b, sub in _entries():
        total += b
        if sub == "inputs":
            i, n_inp, age_cap, cap_n = n_inp, n_inp + 1, min(max_age, INPUTS_MAX_AGE_S), KEEP_INPUTS
        else:
            i, n_main, age_cap, cap_n = n_main, n_main + 1, max_age, keep
        if os.path.basename(p) == keep_key:
            continue
        if i >= cap_n or total > limit or now - mt > age_cap:
            shutil.rmtree(p, ignore_errors=True)
            total -= b
            gone += 1
    return gone


def total_bytes():
    return sum(b for _p, _m, b, _s in _entries())


# ---- a started build's frozen inputs (2026-11-27) ----------------------------------
def request_key(*parts):
    """The key of a build REQUEST: what was asked for (kind, draft group,
    contests, every count, the seed, the model, the pick) and the code -- this
    file's and the scoring code's (sig) and the code that reads the inputs
    (inputs_code_sig) -- and nothing read live. Two presses of the same Build
    are one request; a picked build and a plain one, or a deep profile and a
    standard one, are two."""
    return sig("request", inputs_code_sig(), *parts)[:40]


def _inputs_path(rkey):
    return os.path.join(root(), "inputs", str(rkey), "inputs.ck")


def inputs_save(rkey, snap):
    """Freeze `snap` (plain Python data) under the request key. Never raises."""
    if not (enabled() and _safe_key(rkey)):
        return False
    try:
        ok = write_file(_inputs_path(rkey), pickle.dumps(snap, protocol=4))
        prune(keep_key=str(rkey))
        return ok
    except Exception as e:
        errlog.note("CKPT-write", e, path="inputs.ck")
        return False


def inputs_load(rkey):
    """(the frozen inputs, None) or (None, why not). A file that fails its
    digest is ledgered CKPT-integrity and removed (read_file); one that does
    not unpickle, or is another request's or another version's, is ledgered
    CKPT-inputs and removed."""
    if not (enabled() and _safe_key(rkey)):
        return None, "checkpoints are off"
    path = _inputs_path(rkey)
    if not os.path.isfile(path):
        return None, "no frozen inputs for this request"
    payload = read_file(path)
    if payload is None:
        return None, "the frozen inputs failed their sha256 (removed)"
    try:
        snap = pickle.loads(payload)
        if not isinstance(snap, dict) or snap.get("v") != INPUTS_VERSION or snap.get("key") != rkey:
            raise ValueError("not this request's frozen inputs")
        return snap, None
    except Exception as e:
        errlog.note("CKPT-inputs", e, path="inputs.ck")
        inputs_drop(rkey)
        return None, f"the frozen inputs could not be read ({type(e).__name__}; removed)"


def inputs_drop(rkey):
    r = root()
    if r and _safe_key(rkey):
        shutil.rmtree(os.path.join(r, "inputs", str(rkey)), ignore_errors=True)


def inputs_usable(snap, now=None, fresh=False):
    """(True, None) when a build may RESUME on these frozen inputs, else
    (False, why). Never when the caller asked for fresh inputs (the pre-lock
    rebuild), when they are older than INPUTS_MAX_AGE_S, when the slate's lock
    is unreadable or has passed, when they were frozen before the inactives
    posted (INACTIVES_S before lock) and the inactives are now out, or when
    any part is missing -- a resume is all of them or none."""
    now = time.time() if now is None else float(now)
    if fresh:
        return False, ("fresh inputs asked for: the pre-lock rebuild after the inactives always fetches "
                       "current information")
    if not isinstance(snap, dict):
        return False, "no frozen inputs for this request"
    missing = [k for k in _SNAP_KEYS if k not in snap] + \
              [f"pool.{k}" for k in _POOL_KEYS if k not in (snap.get("pool") or {})]
    if missing or not (snap.get("pool") or {}).get("worlds_key"):
        return False, f"the frozen inputs are incomplete ({', '.join(missing) or 'no worlds key'})"
    try:
        as_of = float(snap["as_of"])
        lock = float(snap["lock_ts"]) if snap.get("lock_ts") is not None else None
    except (TypeError, ValueError):
        return False, "the frozen inputs carry no readable time"
    age = now - as_of
    if age > INPUTS_MAX_AGE_S:
        return False, (f"the frozen inputs are {age / 3600:.1f} h old (frozen inputs are used for "
                       f"{INPUTS_MAX_AGE_S / 3600:.0f} h)")
    if age < -300:
        return False, "the frozen inputs are dated in the future (a clock moved)"
    if lock is None:
        return False, "the frozen slate has no readable lock"
    if now >= lock:
        return False, "the slate has locked"
    if as_of < lock - INACTIVES_S <= now:
        return False, "the frozen inputs predate the inactives (posted 90 minutes before lock)"
    return True, None


# ---- a slate's games while its worlds are still being simulated -------------------
_GAME_KEEP = ("label", "teams", "players", "n_sims", "team_def", "dst", "k", "anchor")


def _game_path(worlds_key, gid):
    safe = "".join(c if c.isalnum() else "_" for c in str(gid))[:60]
    return os.path.join(root(), "games", str(worlds_key), f"{safe}.ck")


def game_load(worlds_key, gid, n):
    """One game's simulated worlds from an interrupted generation of the pool
    `worlds_key`, or None."""
    if not (enabled() and worlds_key):
        return None
    payload = read_file(_game_path(worlds_key, gid))
    if payload is None:
        return None
    try:
        sim = pickle.loads(payload)
        if sim.get("_gid") != str(gid) or int(sim.get("n_sims") or -1) != int(n) \
                or any(len(p["arr"]) != int(n) for p in sim["players"]):
            raise ValueError("not this game's worlds")
        return sim
    except Exception as e:
        errlog.note("CKPT-integrity", e, path=f"game {gid}")
        try:
            os.remove(_game_path(worlds_key, gid))
        except OSError:
            pass
        return None


def game_save(worlds_key, gid, sim):
    """Keep what player_pool reads of one simulated game (the players and
    their arrays, the defenses, the kickers, the anchor report, the offense's
    components) -- never the per-player components or the boxes."""
    if not (enabled() and worlds_key and sim):
        return False
    try:
        keep = {k: sim.get(k) for k in _GAME_KEEP}
        keep["_gid"] = str(gid)
        payload = pickle.dumps(keep, protocol=4)
        d = os.path.dirname(_game_path(worlds_key, gid))
        # the slate's own directory is never pruned while it is being played,
        # so it is held to the budget here: past it the later games are simply
        # not kept (a research-profile slate is ~1 GB a game)
        if len(payload) + dir_bytes(d) > max_bytes():
            return False
        os.makedirs(d, exist_ok=True)
        ok = write_file(_game_path(worlds_key, gid), payload)
        prune(keep_key=str(worlds_key))
        return ok
    except Exception as e:
        errlog.note("CKPT-write", e, path=f"game {gid}")
        return False


def games_any(worlds_key):
    """Whether an interrupted generation of the pool `worlds_key` left any game on disk."""
    if not (enabled() and worlds_key):
        return False
    d = os.path.dirname(_game_path(worlds_key, "x"))
    try:
        return any(n.endswith(".ck") for n in os.listdir(d))
    except OSError:
        return False


def games_clear(worlds_key):
    """The pool is filed in pool_cache: its per-game checkpoints go."""
    r = root()
    if r and worlds_key:
        shutil.rmtree(os.path.join(r, "games", str(worlds_key)), ignore_errors=True)
