"""Guards for resumable tournament builds (build_ckpt) and the PC's build
profiles (pc_profiles), 2026-11-26.

The owner: "Make expensive builds safely resumable after closing vigil-pc.bat,
including simulation generation, candidate scoring and portfolio optimization"
-- and a resumed build must give the answers of the uninterrupted standard
build on the same inputs and worlds. Every equality below is EXACT
(np.array_equal on float64 sums) on DraftKings' 0.02-point lattice, the way
the scoring guards compare the serial and parallel passes.

RS: the checkpoint file (sha256, torn and flipped files), the input
signature, the scoring pass interrupted mid-range and between ranges, the
make-it matrix, a changed input, a damaged checkpoint, the stage memo, the
pinned generator, a whole classic build interrupted and resumed, the worlds'
per-game checkpoints, the disk bounds, deletion when the board lands, and
that nothing is on where VIGIL_CKPT_DIR is not set.
PF: standard is production number for number; the config's parsing; the
deadline, memory and window rules; the three world sets stay disjoint; the
worker's calls; the stamp.

    python3 tests/guards_auto_resume_profiles.py   # exits 1 on any failed check
"""
import inspect
import json
import os
import shutil
import sys
import tempfile

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class _Stop(BaseException):
    """The batch file closed: not an Exception, so no handler in the build
    swallows it (a KeyboardInterrupt's shape)."""


def run(ck):
    _profile_checks(ck)
    try:
        import numpy as np
    except ImportError:
        ck("(numpy absent: resumable builds run where the boards are built)", True)
        return
    import build_ckpt as BC
    tmp = tempfile.mkdtemp()
    saved = {k: os.environ.get(k) for k in ("VIGIL_CKPT_DIR", "VIGIL_CKPT_GB", "VIGIL_POOL_CACHE_DIR")}
    every = BC.SAVE_EVERY_S
    try:
        os.environ.pop("VIGIL_CKPT_GB", None)
        os.environ.pop("VIGIL_CKPT_DIR", None)
        _off_checks(ck, np, BC)
        os.environ["VIGIL_CKPT_DIR"] = os.path.join(tmp, "ckpt")
        BC.SAVE_EVERY_S = 0.0                # every chunk is a save: an interruption can land anywhere
        _file_checks(ck, np, BC, tmp)
        _pass_checks(ck, np, BC, tmp)
        _build_checks(ck, np, BC, tmp)
        _worlds_checks(ck, np, BC, tmp)
        _bounds_checks(ck, np, BC, tmp)
    finally:
        BC.SAVE_EVERY_S = every
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


# ---- RS ------------------------------------------------------------------------------
def _lattice_case(np, seed=3, K=60, M=400, P=24, N=230):
    """Candidates, a field, worlds on the 0.02 lattice and two contests."""
    import dfs_tourney as T
    rng = np.random.default_rng(seed)

    def lineups(n):
        W = np.zeros((n, P), dtype=np.float32)
        for i in range(n):
            W[i, rng.choice(P, 6, replace=False)] = 1.0
        return W
    Wc, Wf = lineups(K), lineups(M)
    X = (np.round(rng.gamma(2.0, 4.0, (P, N)) / 0.02) * 0.02).astype(np.float32)
    wf = np.full(M, 1.0 / M)
    pay = [{"from": 1, "to": 1, "prize": 1000.0}, {"from": 2, "to": 20, "prize": 50.0},
           {"from": 21, "to": 400, "prize": 8.0}]
    grids = [T.payout_grid(2000, pay, 5.0, 400), T.payout_grid(900, pay[:2], 3.0, 20)]
    return T, Wc, Wf, wf, X, grids


def _same(np, a, b):
    """run_vs_field answers equal to the last bit."""
    return (a[2] == b[2] and np.array_equal(a[1], b[1]) and len(a[0]) == len(b[0])
            and all(np.array_equal(x[k], y[k]) for x, y in zip(a[0], b[0]) for k in x))


def _interrupt_after(BC, k):
    """build_ckpt.range_save that stops the process's work at its k-th save
    (the save itself lands first, as a real one does before the kill)."""
    real, n = BC.range_save, [0]

    def save(*a, **kw):
        out = real(*a, **kw)
        n[0] += 1
        if n[0] >= k:
            raise _Stop()
        return out
    return real, save, n


def _count_worlds(T):
    real, n = T._world_tables, [0]

    def counted(*a, **kw):
        n[0] += 1
        return real(*a, **kw)
    return real, counted, n


def _off_checks(ck, np, BC):
    T, Wc, Wf, wf, X, grids = _lattice_case(np)
    src_w = open(os.path.join(_ROOT, "pc_worker.py")).read()
    body = src_w[src_w.index("def _apply_limits("):src_w.index("def _cl_profile(")]
    main = src_w[src_w.index('if __name__ == "__main__":'):]
    ck("RS1 checkpoints are the PC's only: with VIGIL_CKPT_DIR unset (the server, this suite, research) no build "
       "is opened, the passes get no spec and write nothing; the worker sets the directory where it RUNS "
       "(inside pc-simcache, gitignored), never at import -- the suite imports pc_worker and must build its "
       "boards the old way -- and \"checkpoints\": false in vigil-pc.cfg switches them off",
       not BC.enabled() and BC.open_build("classic", 1) is None and T._CKPT[0] is None
       and T._pass_checkpoint("score", None, [(0, 10, 5)], 10, 0, X) is None
       and src_w.count('"VIGIL_CKPT_DIR"') == 2 and 'env.setdefault("VIGIL_CKPT_DIR"' in body
       and '_cfg_value("checkpoints") is False' in body and "_apply_limits()" in main
       and "pc-simcache/" in open(os.path.join(_ROOT, ".gitignore")).read())


def _file_checks(ck, np, BC, tmp):
    p = os.path.join(tmp, "f", "one.ck")
    payload = bytes(range(256)) * 40
    ok = BC.write_file(p, payload)
    back = BC.read_file(p)
    with open(p, "r+b") as f:
        f.seek(-7, 2)
        b = f.read(1)
        f.seek(-7, 2)
        f.write(bytes([b[0] ^ 1]))
    flipped = BC.read_file(p)
    gone = not os.path.exists(p)
    BC.write_file(p, payload)
    with open(p, "r+b") as f:
        f.truncate(os.path.getsize(p) - 100)
    torn = BC.read_file(p)
    ck("RS2 a checkpoint file is its payload behind a sha256: it reads back byte for byte, written through a "
       "temporary name (none left behind); one flipped bit or a torn tail is a miss and the file is REMOVED, "
       "never trusted",
       ok and back == payload and flipped is None and gone and torn is None and not os.path.exists(p)
       and not any(".tmp-" in n for n in os.listdir(os.path.dirname(p))))
    a = np.arange(12, dtype=np.float32).reshape(3, 4)
    a2 = a.copy()
    a2[2, 3] += 1e-3
    s0 = BC.sig("x", a, {"g": [1, 2.5]}, [(0, 5, 2)])
    ck("RS3 the input signature: equal data is the same key whatever the dict order; one element of a matrix, its "
       "dtype, its shape, a grid value, a world range or the pass's name is a different key; the scoring code's "
       "own source is in every key",
       s0 == BC.sig("x", a.copy(), {"g": [1, 2.5]}, [(0, 5, 2)])
       and BC.sig("x", {"b": 1, "a": a}) == BC.sig("x", dict([("a", a), ("b", 1)]))
       and len({s0, BC.sig("x", a2, {"g": [1, 2.5]}, [(0, 5, 2)]), BC.sig("x", a.astype(np.float64), {"g": [1, 2.5]}, [(0, 5, 2)]),
                BC.sig("x", a.reshape(4, 3), {"g": [1, 2.5]}, [(0, 5, 2)]), BC.sig("x", a, {"g": [1, 2.6]}, [(0, 5, 2)]),
                BC.sig("x", a, {"g": [1, 2.5]}, [(0, 5, 3)]), BC.sig("y", a, {"g": [1, 2.5]}, [(0, 5, 2)])}) == 7
       and "dfs_tourney.py" in BC._CODE_FILES and "build_ckpt.py" in BC._CODE_FILES
       and "code_sig()" in inspect.getsource(BC.sig))


def _pass_checks(ck, np, BC, tmp):
    T, Wc, Wf, wf, X, grids = _lattice_case(np)
    N = X.shape[1]
    ref = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=16)                     # the standard pass: no checkpoint
    # --- one range (one worker, as the standard serial pass), stopped after its 5th chunk ---
    b1 = BC.open_build("guard", "one-range")
    real, save, _n = _interrupt_after(BC, 5)
    stopped = False
    BC.range_save = save
    try:
        T.run_vs_field(Wc, Wf, wf, X, grids, chunk=16, checkpoint=b1)
    except _Stop:
        stopped = True
    finally:
        BC.range_save = real
    realw, counted, nw = _count_worlds(T)
    T._world_tables = counted
    try:
        b1r = BC.open_build("guard", "one-range")
        res1 = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=16, checkpoint=b1r)
    finally:
        T._world_tables = realw
    ck("RS4 the scoring pass, interrupted inside a world range and resumed: every figure of every contest and the "
       "best-lineup tallies equal the uninterrupted standard pass to the last bit (0.02-point lattice), and only "
       "the worlds after the last saved chunk are scored again (5 chunks of 16 were kept)",
       stopped and _same(np, res1, ref) and nw[0] == N - 5 * 16 and b1.fresh and not b1r.fresh
       and "80 of 230 worlds" in (b1r.resumed.get("score") or ""), f"worlds rescored {nw[0]} of {N}; {b1r.resumed}")
    # --- several ranges (the PC's shape: a few per worker), stopped in the third ---
    jobs_real = T._range_jobs
    T._range_jobs = lambda n, w, ch, per=3: jobs_real(n, 2, ch, per=2)        # 4 ranges, run serially here
    try:
        ref4 = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=16)
        jobs = T._range_jobs(N, 1, 16)
        b2 = BC.open_build("guard", "four-ranges")
        per_range = -(-(jobs[0][1] - jobs[0][0]) // 16)
        real, save, _n = _interrupt_after(BC, 2 * per_range + 1)
        BC.range_save = save
        try:
            T.run_vs_field(Wc, Wf, wf, X, grids, chunk=16, checkpoint=b2)
            stopped4 = False
        except _Stop:
            stopped4 = True
        finally:
            BC.range_save = real
        realw, counted, nw4 = _count_worlds(T)
        T._world_tables = counted
        try:
            b2r = BC.open_build("guard", "four-ranges")
            res4 = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=16, checkpoint=b2r)
            first_resume = nw4[0]
            again = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=16, checkpoint=BC.open_build("guard", "four-ranges"))
        finally:
            T._world_tables = realw
        done_before = 2 * (jobs[0][1] - jobs[0][0]) + 16
        ck("RS5 interrupted after k world ranges and one chunk of the next (4 ranges; 2 finished): the resumed pass "
           "equals the uninterrupted run on the same ranges to the last bit, scores only the worlds that were "
           "left, and a pass whose every range is on disk scores none",
           len(jobs) == 4 and stopped4 and _same(np, res4, ref4) and first_resume == N - done_before
           and nw4[0] == first_resume and _same(np, again, ref4),
           f"{len(jobs)} ranges; rescored {first_resume} of {N}, then {nw4[0] - first_resume}")
        mine = sorted(f for f in os.listdir(b2.path) if f.startswith("score-"))     # this pass's four ranges
        # --- a changed input is a different checkpoint ---
        X2 = X.copy()
        X2[3, 17] += np.float32(0.02)
        wf2 = wf.copy()
        wf2[0], wf2[1] = wf2[0] * 2, 0.0
        g2 = [T.payout_grid(2000, [{"from": 1, "to": 1, "prize": 1001.0}], 5.0, 1), grids[1]]
        Wc2 = Wc.copy()
        Wc2[0] = Wc[1]
        fresh = lambda *a, **k: T.run_vs_field(*a, chunk=16, **k)
        bb = lambda: BC.open_build("guard", "four-ranges")
        ck("RS6 a changed input is never answered from the checkpoint: one world's score moved by 0.02, the field's "
           "weights, a payout table, one candidate lineup, the chunk size or the candidate rows read -- each "
           "resumes nothing and equals a fresh pass on the changed input",
           _same(np, fresh(Wc, Wf, wf, X2, grids, checkpoint=bb()), fresh(Wc, Wf, wf, X2, grids))
           and not _same(np, fresh(Wc, Wf, wf, X2, grids), ref4)
           and _same(np, fresh(Wc, Wf, wf2, X, grids, checkpoint=bb()), fresh(Wc, Wf, wf2, X, grids))
           and _same(np, fresh(Wc, Wf, wf, X, g2, checkpoint=bb()), fresh(Wc, Wf, wf, X, g2))
           and _same(np, fresh(Wc2, Wf, wf, X, grids, checkpoint=bb()), fresh(Wc2, Wf, wf, X, grids))
           and _same(np, T.run_vs_field(Wc, Wf, wf, X, grids, chunk=8, checkpoint=bb()),
                     T.run_vs_field(Wc, Wf, wf, X, grids, chunk=8))
           and _same(np, fresh(Wf, Wf, wf, X, grids, cand_idx=[3, 9, 200], checkpoint=bb()),
                     fresh(Wf, Wf, wf, X, grids, cand_idx=[3, 9, 200])))
        # --- a damaged range file is recomputed ---
        with open(os.path.join(b2.path, mine[1]), "r+b") as f:
            f.seek(60)
            f.write(b"\x00\x01\x02\x03")
        realw, counted, nwd = _count_worlds(T)
        T._world_tables = counted
        try:
            healed = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=16, checkpoint=bb())
        finally:
            T._world_tables = realw
        ck("RS7 a damaged range checkpoint is detected by its sha256 and that range alone is scored again: the "
           "pass still equals the uninterrupted run",
           len(mine) == 4 and _same(np, healed, ref4) and 0 < nwd[0] <= jobs[1][1] - jobs[1][0], f"rescored {nwd[0]}")
        # --- the portfolio pass's make-it matrix ---
        cand = np.asarray([5, 9, 14, 22, 31, 40, 47, 58])
        pref = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 4, chunk=16)
        b3 = BC.open_build("guard", "makeit")
        real, save, _n = _interrupt_after(BC, 2)
        BC.range_save = save
        try:
            T._CKPT[0] = b3                       # the portfolio pass reads the build's checkpoint from the context
            T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 4, chunk=16)
            stopped_p = False
        except _Stop:
            stopped_p = True
        finally:
            BC.range_save = real
        # the portfolio pass's worlds are _make_it_world's since 2026-11-27 (packed rows; it reads the field's top
        # alone and never calls _world_tables)
        realw, nwp = T._make_it_world, [0]

        def counted_p(*a, **kw):
            nwp[0] += 1
            return realw(*a, **kw)
        T._make_it_world = counted_p
        try:
            T._CKPT[0] = BC.open_build("guard", "makeit")
            pres = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 4, chunk=16)
        finally:
            T._make_it_world = realw
            T._CKPT[0] = None
        os.environ["VIGIL_CKPT_GB"] = "0.000001"       # ~1 KB: the make-it matrix cannot fit
        try:
            b4 = BC.open_build("guard", "makeit-small")
            T._CKPT[0] = b4
            psmall = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 4, chunk=16)
            small_files = [f for f in os.listdir(b4.path) if f.startswith("makeit-")] if os.path.isdir(b4.path) else []
        finally:
            T._CKPT[0] = None
            os.environ.pop("VIGIL_CKPT_GB", None)
        ck("RS8 the portfolio pass: each finished range of the make-it matrix is kept, a pass stopped after two of "
           "four resumes from them (only the other two ranges' worlds are scored) and picks the same entries with "
           "the same cover, float for float; a matrix that would not fit the checkpoint budget is simply not "
           "checkpointed, and says so",
           stopped_p and pres == pref and nwp[0] == N - 2 * (jobs[0][1] - jobs[0][0]) and psmall == pref
           and small_files == [] and "would not fit" in (b4.resumed.get("makeit") or "")
           # the S4 pin's prefix: the rank default and the workers argument as they were (the portfolio caps
           # appended `limits=None` after them, 2026-11-26)
           and 'rank="top1", workers=1' in inspect.getsource(T.portfolio_vs_field),
           f"rescored {nwp[0]}; {b4.resumed}")
    finally:
        T._range_jobs = jobs_real
    # --- real processes: the pool's workers write and read their own ranges ---
    refp = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=16, workers=2)
    b5 = BC.open_build("guard", "pool")
    runp = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=16, workers=2, checkpoint=b5)
    n_files = len([f for f in os.listdir(b5.path) if f.startswith("score-")])
    b5r = BC.open_build("guard", "pool")
    againp = T.run_vs_field(Wc, Wf, wf, X, grids, chunk=16, workers=2, checkpoint=b5r)
    ck("RS9 on a process pool each worker checkpoints its own ranges through the spec it was started with: the "
       "checkpointed pass equals the plain parallel pass to the last bit, and a second run reads every range "
       "from disk and equals it too",
       _same(np, runp, refp) and _same(np, againp, refp) and n_files == len(T._range_jobs(N, 2, 16))
       and f"{N} of {N} worlds" in (b5r.resumed.get("score") or ""), f"{n_files} files; {b5r.resumed}")
    # --- the stage memo and the generator ---
    calls = []
    b6 = BC.open_build("guard", "memo")
    fn = lambda: (calls.append(1), ([(1, 2, 3), ()], np.asarray([1.5, 2.5])))[1]
    m1 = b6.memo("optimal", BC.sig("optimal", X), fn)
    m2 = BC.open_build("guard", "memo").memo("optimal", BC.sig("optimal", X), fn)
    m3 = b6.memo("optimal", BC.sig("optimal", X[:, :-1]), fn)
    e1 = b6.rng_entropy()
    b6r = BC.open_build("guard", "memo")
    e2 = b6r.rng_entropy()
    e3 = BC.open_build("guard", "memo-other").rng_entropy()
    ck("RS10 a whole stage's answer (the best lineup of every generation world) is computed once a build and read "
       "back equal; other inputs compute again. The build's generator: its entropy is drawn once and a restart "
       "gets the same one (so the field and candidates are redrawn identically), another build another",
       len(calls) == 2 and m1[0] == m2[0] and np.array_equal(m1[1], m2[1]) and m3[0] == m1[0]
       and e1 == e2 and e1 != e3 and e1 > 2 ** 64 and b6r.resumed.get("rng")
       and np.random.default_rng(e1).random() == np.random.default_rng(e2).random())


def _build_checks(ck, np, BC, tmp):
    """A whole classic build, unseeded as production's are, stopped in its
    scoring pass and restarted."""
    import guards_picker as GP
    import dfs_tourney as T
    import nfl_dfs as N
    csv_c, pool_c, gated_c = GP._classic_slate()
    detail = {"id": 1, "name": "Guard Millionaire", "prize_pool": 2.0e6, "max_entries": 40000, "entry_fee": 20.0,
              "first_prize": 200000, "places_paid": 8000, "entered": 1000, "max_entries_per_user": 150,
              "payouts": [{"from": 1, "to": 1, "prize": 200000.0}, {"from": 2, "to": 10, "prize": 10000.0},
                          {"from": 11, "to": 8000, "prize": 40.0}]}
    slate = {"starts": "2026-11-01T18:00:00.0000000Z", "draft_group_id": 999,
             "contests": [{"id": 1, "prize_pool": 2.0e6, "entries": 1000}], "n_players": 104}
    # small on purpose (three and a bit builds; the steering passes are a fixed cost of each)
    kw = dict(n_sims=750, opt_worlds=250, field_n=3000, cand_n=2000, cal_n=1000, week=8, preseason=False,
              workers=1, probes=[], model="legacy", log=lambda *a: None, top=15, k_port=4, candidates=200, chunk=64)
    mocks = list(GP._board_mocks(T, N, csv_c, pool_c, gated_c, slate, detail))
    import nfl_dfs_sim
    made = {}

    def lattice_pool(week, n=3000, **k):
        if n not in made:
            made[n] = {nm: dict(p, arr=[round(round(x / 0.02) * 0.02, 2) for x in p["arr"]])
                       for nm, p in GP._fake_pool(pool_c, n).items()}
        return made[n]
    mocks = [m for m in mocks if not (m[0] is nfl_dfs_sim and m[1] == "player_pool")] + [(nfl_dfs_sim, "player_pool", lattice_pool)]
    real_opt, n_opt = T.optimal_lineups, [0]

    def counted_opt(*a, **k):
        n_opt[0] += 1
        return real_opt(*a, **k)
    # the optimizer field's steering is a fixed 10 x 40,000 draws a build (24 s of a 32 s guard build): the
    # same path and the same generator, on 3 x 4,000 here
    real_steer = T.steer_field
    small_steer = lambda draw, players, rng, beta, kappa, targets: real_steer(draw, players, rng, beta, kappa,
                                                                              targets, n=4000, iters=3)
    with GP._patched(*mocks, (T, "optimal_lineups", counted_opt), (T, "steer_field", small_steer),
                     (T, "OPT_FIELD_N", 4000)):
        real, save, n_saves = _interrupt_after(BC, 4)
        BC.range_save = save
        try:
            T.build_nfl_classic(999, **kw)
            stopped = False
        except _Stop:
            stopped = True
        finally:
            BC.range_save = real
        opt_first = n_opt[0]
        key = T._CKPT[0].key if T._CKPT[0] is not None else None
        realw, counted, nw = _count_worlds(T)
        # the scoring pass's worlds are _world_tables', the portfolio pass's _make_it_world's (since 2026-11-27)
        realm = T._make_it_world

        def counted_m(*a, **k):
            nw[0] += 1
            return realm(*a, **k)
        T._world_tables, T._make_it_world = counted, counted_m
        try:
            resumed = T.build_nfl_classic(999, **kw)
        finally:
            T._world_tables, T._make_it_world = realw, realm
        opt_resumed = n_opt[0] - opt_first
        stamp = (resumed or {}).get("checkpoint") or {}
        ent = int(json.loads(BC.read_file(os.path.join(BC.root(), "builds", str(key), "rng.json")).decode())["entropy"])
        cleared = T._CKPT[0] is None
        # the reference: no checkpoint machinery at all, the generator started from the same entropy
        os.environ.pop("VIGIL_CKPT_DIR")
        try:
            plain = T.build_nfl_classic(999, seed=ent, **kw)
        finally:
            os.environ["VIGIL_CKPT_DIR"] = os.path.join(tmp, "ckpt")
        # and an input that moved overnight (a salary) is another build: nothing of the old one is read
        csv_moved = csv_c.replace(",5000,", ",5100,", 1) if ",5000," in csv_c else csv_c
        # (stopped at its first save and on the plain softmax field: the key is opened before either matters)
        real, save, _n = _interrupt_after(BC, 1)
        BC.range_save = save
        try:
            with GP._patched((mocks[0][0], "slate_for", lambda sport, draft_group_id=None, exclude_games=None: dict(slate, csv=csv_moved)),
                             (T, "FIELD_MODEL", "softmax")):
                T.build_nfl_classic(999, **kw)
        except _Stop:
            pass
        finally:
            BC.range_save = real
        moved = T._CKPT[0]
        T._CKPT[0] = None
    ev = len(T.world_split(kw["n_sims"], kw["opt_worlds"])[1])
    ck("RS11 a whole classic build, UNSEEDED as production's are, stopped in its scoring pass (four chunks kept) "
       "and started again: the restart takes the generator's entropy and the best lineups of the generation "
       "worlds from the checkpoint, redraws the same field and candidates, scores only the worlds that were left, "
       "and every list, portfolio and player figure equals the uninterrupted build with no checkpoint at all "
       "drawn from the same generator; the board says what it resumed",
       stopped and resumed is not None and plain is not None and resumed["results"] == plain["results"]
       and resumed["players"] == plain["players"] and resumed["candidates"] == plain["candidates"]
       and resumed["field_model"]["achieved"] == plain["field_model"]["achieved"]
       and opt_first >= 1 and opt_resumed == opt_first - 1 and "checkpoint" not in plain and cleared
       and stamp.get("key") == key and f"256 of {ev} worlds" in (stamp.get("resumed") or {}).get("score", "")
       and (stamp.get("resumed") or {}).get("optimal") == "from the checkpoint"
       and (stamp.get("resumed") or {}).get("rng") and nw[0] == 2 * ev - 256,
       f"stopped {stopped}; optimal solves {opt_first} then {opt_resumed}; stamp {stamp}; worlds {nw[0]} of {2 * ev}; "
       f"equal {resumed is not None and plain is not None and resumed['results'] == plain['results']}")
    ck("RS12 a changed input is a different build: one salary moved and the restart resumes nothing (a new key, a "
       "new generator, every world scored); when the board lands the worker removes its checkpoint",
       csv_moved != csv_c and moved is not None and moved.key != key and moved.fresh and moved.resumed == {}
       and os.path.isdir(os.path.join(BC.root(), "builds", key)) and BC.landed(resumed)
       and not os.path.isdir(os.path.join(BC.root(), "builds", key)) and not BC.landed({"checkpoint": {"key": "../x"}}),
       f"{moved and (moved.key, moved.fresh, moved.resumed)}")
    src_cl, src_sd = inspect.getsource(T.build_nfl_classic), inspect.getsource(T.build_nfl_showdown)
    src_w = open(os.path.join(_ROOT, "pc_worker.py")).read()
    ck("RS13 wired: both tournament builders open their checkpoint on the worlds and the pool actually used, a "
       "seeded build keeps its seed, the showdown board (nothing drawn) keys on its universe and weights; the "
       "worker removes a checkpoint only after the board is on disk, on all three build paths",
       src_cl.count("build_ckpt.open_build(") == 1 and src_sd.count("build_ckpt.open_build(") == 1
       and "if ck_b is not None and seed is None:" in src_cl and "rng = np.random.default_rng(ck_b.rng_entropy())" in src_cl
       and src_cl.index("_CKPT[0] = ck_b") < src_cl.index("optimal_lineups(") and "idx, np.asarray(f, dtype=np.float64), rows, X)" in src_sd
       and src_w.count("_ckpt_landed(art)") == 3
       and all(seg.index("boardshare.put(name, art)") < seg.index("_ckpt_landed(art)")
               for seg in src_w.split("def ")[1:] if "_ckpt_landed(art)" in seg and "boardshare.put(name, art)" in seg))


def _worlds_checks(ck, np, BC, tmp):
    """The worlds: a slate's games are kept one by one while they are played."""
    import nfl_dfs_engine as ENG
    import nfl_dfs_sim as S
    import pool_cache as PC
    os.environ["VIGIL_POOL_CACHE_DIR"] = os.path.join(tmp, "pool")

    def pl(name, pos, team, opp, proj):
        return {"name": name, "pos": pos, "team": team, "opp": opp, "proj_pts": proj,
                "means": {"rec_tgt": 5.0}, "recon": {"rec_tgt": 5.0}}
    games = {f"g{i}": {"label": f"{a} @ {b}", "teams": [a, b], "home": b, "away": a,
                       "players": [pl(f"Q{a}", "QB", a, b, 18.0), pl(f"W{a}", "WR", a, b, 12.5),
                                   pl(f"Q{b}", "QB", b, a, 19.0), pl(f"R{b}", "RB", b, a, 14.1)]}
             for i, (a, b) in enumerate((("AAA", "BBB"), ("CCC", "DDD"), ("EEE", "FFF")))}
    played, stop_at = [], [None]

    def fake_sim(game, n, rng_, ctx):
        if stop_at[0] is not None and len(played) >= stop_at[0]:
            raise _Stop()
        played.append(game["label"])
        out = []
        for p in game["players"]:
            arr = np.round(rng_.normal(p["proj_pts"], 7, n), 2)
            out.append({"name": p["name"], "pos": p["pos"], "team": p["team"], "opp": p["opp"],
                        "proj_pts": p["proj_pts"], "ceiling": float(np.percentile(arr, 90)),
                        "floor": float(np.percentile(arr, 10)), "arr": arr, "sim_mean": float(arr.mean())})
        return {"label": game["label"], "teams": game["teams"], "players": out, "n_sims": n,
                "dst": {t: np.round(rng_.normal(6, 4, n), 2) for t in game["teams"]},
                "k": {t: np.round(rng_.normal(8, 3, n), 0) for t in game["teams"]}, "anchor": {"ok": game["label"]},
                "team_def": {}, "components": {"big": np.zeros(n)}, "boxes": None}
    names = ("weekly_games", "dst_projections", "kicker_projections")
    saved = {f: getattr(S, f) for f in names}
    saved_eng = {f: getattr(ENG, f) for f in ("week_context", "simulate_game")}
    try:
        S.weekly_games = lambda season, week: games
        S.dst_projections = lambda season, week: {t: {"nickname": t.title(), "proj": 6.0} for g in games.values() for t in g["teams"]}
        S.kicker_projections = lambda season, week: {t: {"name": "K" + t, "pts": 8.0} for g in games.values() for t in g["teams"]}
        ENG.week_context = lambda season, week, workers=1: {"T": {}, "profiles": {t: {} for g in games.values() for t in g["teams"]},
                                                             "drive": {"AAA": 21.0}, "weight": 0.3}
        ENG.simulate_game = fake_sim
        call = dict(season="2026", model="engine", seed=5)
        S._cache.clear()
        stop_at[0] = 2
        try:
            S.player_pool(12, n=300, **call)
            stopped = False
        except _Stop:
            stopped = True
        gdir = os.path.join(BC.root(), "games")
        kept = [f for d in os.listdir(gdir) for f in os.listdir(os.path.join(gdir, d))] if os.path.isdir(gdir) else []
        stop_at[0] = None
        first = list(played)
        S._cache.clear()
        resumed = S.player_pool(12, n=300, **call)
        after = played[len(first):]
        left = os.listdir(gdir) if os.path.isdir(gdir) else []
        entries = len(PC.entries())
        # the reference: the same seeded pool with no cache and no checkpoint
        os.environ["VIGIL_POOL_CACHE_DIR"] = ""
        S._cache.clear()
        plain = S.player_pool(12, n=300, **call)
        os.environ["VIGIL_POOL_CACHE_DIR"] = os.path.join(tmp, "pool")
        S._cache.clear()
        n_before = len(played)
        S.player_pool(12, n=400, **call)                # another world count: another pool, nothing reused
        other_n = len(played) - n_before
    finally:
        for f, v in saved.items():
            setattr(S, f, v)
        for f, v in saved_eng.items():
            setattr(ENG, f, v)
        S._cache.clear()
        os.environ["VIGIL_POOL_CACHE_DIR"] = ""
    ck("RS14 the worlds: a slate's generation stopped after two of three games keeps those two on disk (under the "
       "worlds cache's own key: the call, the simulator's code, every input), the restart plays only the third, "
       "and the pool equals the uninterrupted seeded pool player for player, array for array; once the pool is "
       "filed in the worlds cache the per-game files are gone; another world count reuses none of them",
       stopped and len(first) == 2 and len(kept) == 2 and after == ["EEE @ FFF"] and resumed == plain
       and left == [] and entries == 1 and other_n == 3, f"{first} then {after}; kept {kept}; left {left}")
    src = inspect.getsource(S.player_pool)
    ck("RS15 the per-game checkpoint keeps what the pool reads and nothing else (never the per-player components "
       "or the boxes), is only consulted under a worlds-cache key, and a file that is not this game's worlds "
       "(another count, another game) is a miss",
       "components" not in BC._GAME_KEEP and "boxes" not in BC._GAME_KEEP
       and all(k in BC._GAME_KEEP for k in ("players", "dst", "k", "anchor", "team_def"))
       and "sim = _ckpt.game_load(pc_key, gid, n) if pc_key else None" in src
       and "_ckpt.games_clear(pc_key)" in src and BC.game_load("nokey", "g0", 300) is None)
    ck("RS16 cached worlds are NOT extended in place, on purpose, and the reason is written where the next reader "
       "will look: each call re-levels the two offences with its own generator before it plays a world, and a "
       "player's floor, ceiling and allocation draws are functions of the whole count",
       "not extended" in (inspect.getdoc(PC) or "") and "anchor" in (inspect.getdoc(PC) or ""))


def _bounds_checks(ck, np, BC, tmp):
    root = BC.root()
    shutil.rmtree(root, ignore_errors=True)
    made = []
    for i in range(5):
        b = BC.open_build("guard", "bounds", i)
        BC.write_file(os.path.join(b.path, "score-x-0-1.ck"), b"z" * 4000)
        os.utime(b.path, (2_000_000_000 + i, 2_000_000_000 + i))
        made.append(b.key)
    names = lambda: sorted(os.listdir(os.path.join(root, "builds")))
    BC.prune(keep=3, max_age=1e12)
    kept3 = names()
    BC.prune(keep=10, limit=9000, max_age=1e12)
    kept_b = names()
    BC.prune(keep=10, limit=10 ** 9, max_age=1e12, keep_key=made[4])
    os.utime(os.path.join(root, "builds", made[3]), (1_000_000, 1_000_000))
    BC.prune(keep=10, limit=10 ** 9)
    kept_age = names()
    os.environ["VIGIL_CKPT_GB"] = "3"
    gb3 = BC.max_bytes()
    os.environ["VIGIL_CKPT_GB"] = "nonsense"
    bad = BC.max_bytes()
    os.environ.pop("VIGIL_CKPT_GB")
    BC.prune(keep=1, limit=1, max_age=1e12, keep_key=made[4])
    ck("RS17 the checkpoint store is bounded: the newest builds by last use are kept and the rest removed, past the "
       "byte budget the oldest go first, anything older than eight days goes, and the build that is running is "
       "never pruned; the budget is 8 GB unless vigil-pc.cfg's \"checkpoint_gb\" (VIGIL_CKPT_GB) says otherwise, "
       "and an unreadable value is the default",
       kept3 == sorted(made[2:]) and kept_b == sorted(made[3:]) and kept_age == [made[4]]
       and names() == [made[4]] and gb3 == 3 * 1024 ** 3 and bad == BC.MAX_BYTES == 8 * 1024 ** 3
       and BC.MAX_AGE_S == 8 * 86400, f"{kept3} {kept_b} {kept_age}")


# ---- PF ------------------------------------------------------------------------------
def _profile_checks(ck):
    import dfs_evaluate
    import dfs_tourney as T
    import pc_profiles as PP
    import pc_worker as W
    import pool_cache as PC
    std = PP.PROFILES["standard"]
    sig = inspect.signature(T.build_nfl_classic).parameters
    ck("PF1 standard IS production, number for number: 20,000 generation + 160,000 ranking worlds (the worker's "
       "180,000 and the builder's own defaults), a 300,000-lineup field, the evaluator's 40,000 final worlds, the "
       "worlds cache's 24 entries / 12 GB, no VRAM limit, every core",
       std["generation"] + std["ranking"] == W._CL_SIMS_FULL == 180000 and std["generation"] == sig["opt_worlds"].default == 20000
       and std["field"] == sig["field_n"].default == 300000 and std["final"] == dfs_evaluate.FINAL_N == 40000
       and std["cache_entries"] == PC.KEEP_ENTRIES == 24 and std["cache_gb"] * 1024 ** 3 == PC.MAX_BYTES
       and std["gpu_vram_fraction"] is None and PP.CHECKPOINT_GB == 8.0)
    p0, n0 = PP.from_cfg({})
    p1, n1 = PP.from_cfg({"app_url": "x", "gpu": True})
    pbad, nbad = PP.from_cfg({"profile": "ludicrous"})
    pnum, nnum = PP.from_cfg({"profile": 7})
    ck("PF2 defaults do not change: no \"profile\" key (every PC today) is standard with nothing to say, the worker "
       "then makes its old call unchanged, and an unknown or malformed profile is standard WITH a note -- a typo "
       "never stops the worker and never runs something the owner did not ask for",
       p0["name"] == p1["name"] == "standard" and n0 == n1 == [] and PP.is_standard_counts(p0) and p0["workers"] is None
       and pbad["name"] == pnum["name"] == "standard" and len(nbad) == 1 and "ludicrous" in nbad[0] and len(nnum) == 1
       and PP.choose(p0, 1e9, 0.0, None, ram_gb=64)["name"] == "standard")
    want = {"deep": (50_000, 500_000, 160_000, 300_000), "maximum": (100_000, 1_000_000, 500_000, 1_000_000),
            "research": (250_000, 2_000_000, 1_000_000, 3_000_000)}
    ok_counts = all(tuple(PP.from_cfg({"profile": n})[0][k] for k in ("generation", "ranking", "final", "field")) == v
                    for n, v in want.items())
    splits = []
    for n in PP.ORDER:
        p = PP.from_cfg({"profile": n})[0]
        gen, ev = T.world_split(PP.n_sims(p), p["generation"])
        kw = PP.build_kwargs(p)
        splits.append(len(gen) == p["generation"] and len(ev) == p["ranking"] and gen.stop == ev.start
                      and kw == {"n_sims": p["generation"] + p["ranking"], "opt_worlds": p["generation"], "field_n": p["field"]})
    ck("PF3 the owner's research targets (deep 50k / 500k / 160k; maximum 100k / 1M / 500k, a 1M field; research "
       "250k / 2M / 1M, a 3M field), and the three world sets stay independent on every profile: the builder's own "
       "split gives exactly `generation` worlds to find the candidates and the next `ranking` worlds to score "
       "them, disjoint, and the final worlds are the evaluator's separate pool on its own seed",
       ok_counts and all(splits) and "final_seed(meta[\"id\"])" in inspect.getsource(dfs_evaluate.prepare)
       and [PP.PROFILES[n]["cache_gb"] for n in PP.ORDER] == sorted(PP.PROFILES[n]["cache_gb"] for n in PP.ORDER))
    c, cn = PP.from_cfg({"profile": "custom", "profile_custom": {"generation_worlds": 30000, "ranking_worlds": 240000,
                                                                 "final_worlds": 80000, "field": 450000, "workers": 6,
                                                                 "gpu_vram_fraction": 0.5, "cache_gb": 30}})
    cb, cbn = PP.from_cfg({"profile": "custom", "profile_custom": {"generation_worlds": "many", "ranking_worlds": 50,
                                                                   "field": True, "final_worlds": 60000}})
    ch, chn = PP.from_cfg({"profile": "custom", "profile_custom": {"generation_worlds": 90000, "ranking_worlds": 100000}})
    ov, ovn = PP.from_cfg({"profile": "deep", "workers": 10, "cache_gb": 50, "cache_entries": 8, "checkpoint_gb": 20,
                           "gpu_vram_fraction": 0.4})
    ovb, ovbn = PP.from_cfg({"profile": "deep", "workers": "all", "cache_gb": -1, "gpu_vram_fraction": 3})
    ck("PF4 \"custom\" reads explicit numbers (profile_custom: generation_worlds, ranking_worlds, final_worlds, field, "
       "workers, gpu_vram_fraction, cache_gb); a number missing, of the wrong type or out of range is standard's, "
       "with a note; generation worlds are held to half the ranking worlds (the builder's split would otherwise "
       "cut them silently). The top-level workers, cache_gb, cache_entries, checkpoint_gb and gpu_vram_fraction "
       "override any profile's, and a bad one is ignored with a note",
       (c["generation"], c["ranking"], c["final"], c["field"], c["workers"], c["gpu_vram_fraction"], c["cache_gb"])
       == (30000, 240000, 80000, 450000, 6, 0.5, 30.0) and cn == []
       and (cb["generation"], cb["ranking"], cb["field"], cb["final"]) == (20000, 160000, 300000, 60000) and len(cbn) == 3
       and ch["generation"] == 50000 and len(chn) == 1
       and (ov["workers"], ov["cache_gb"], ov["cache_entries"], ov["checkpoint_gb"], ov["gpu_vram_fraction"]) == (10, 50.0, 8, 20.0, 0.4)
       and ovn == [] and ovb["workers"] is None and ovb["cache_gb"] == 24.0 and ovb["gpu_vram_fraction"] == 0.6 and len(ovbn) == 3)
    # --- the deadline: scaled from the PC's own measured stages ---
    board = {"sims": 180000, "opt_worlds": 20000, "eval_worlds": 160000, "candidates": 150000,
             "field_model": {"n": 300000}, "players": [0] * 480, "contests": [0, 0],
             "timings": {"sims_s": 600.0, "optimal_s": 150.0, "field_s": 250.0, "candidates_s": 100.0,
                         "score_s": 1000.0, "portfolio_s": 300.0, "total_s": 2500.0}}
    deep, mx = PP.from_cfg({"profile": "deep"})[0], PP.from_cfg({"profile": "maximum"})[0]
    e_std = PP.estimate_s(PP.from_cfg({})[0], board)[0]
    e_deep = PP.estimate_s(deep, board)[0]
    e_max = PP.estimate_s(mx, board)[0]
    exp_deep = 200.0 + 600.0 * 550 / 180 + 150.0 * 2.5 + 250.0 + 1000.0 * 3.125 + 300.0 * 3.125
    exp_max = (200.0 + 600.0 * 1100 / 180 + 150.0 * 5 + 250.0 * (10 / 3) + 1000.0 * 6.25 * (1150 / 450)
               + 300.0 * 6.25 * (10 / 3))
    e_none = PP.estimate_s(deep, None)[0]
    ck("PF5 a profile's run time is scaled from the last timed board's own stages, as _cl_sims' estimate is: the "
       "sims by the worlds simulated, the best lineups by the generation worlds, the field by its size, the "
       "scoring pass by ranking worlds x (candidates + field), the portfolio pass by ranking worlds x field, the "
       "rest fixed -- a standard board reproduces its own time; a picked build's second solve is counted; with no "
       "timed board a 45-minute standard build is scaled by the larger ratio",
       abs(e_std - 2500.0) < 1e-6 and abs(e_deep - exp_deep) < 1e-6 and abs(e_max - exp_max) < 1e-6
       and abs(PP.estimate_s(deep, board, picked=True)[0] - (exp_deep + 150.0 * 2.5)) < 1e-6
       and abs(e_none - 2700.0 * 3.125) < 1e-6, f"{e_std} {e_deep} {exp_deep} {e_max} {exp_max} {e_none}")
    now = 1_800_000_000.0
    need = lambda e: PP.TIME_MARGIN * e + PP.TIME_PAD_S
    fits = PP.choose(mx, now + need(e_max) + 1, now, board, ram_gb=256)
    down = PP.choose(mx, now + need(e_max) - 1, now, board, ram_gb=256)
    floor = PP.choose(mx, now + need(e_deep) - 1, now, board, ram_gb=256)
    sat = PP.choose(deep, now + 15 * 3600, now, board, window="2026-11-28 saturday night", ram_gb=64)
    pre = PP.choose(deep, now + 10 ** 7, now, board, window="2026-11-29 pregame", ram_gb=512)
    ck("PF6 deadline-aware: a profile runs only when its estimate, a quarter over and fifteen minutes more, fits "
       "before lock; otherwise the next profile down is tried and standard is the floor (where _cl_sims still "
       "picks 180,000 or 60,000 by its own rule) -- and the board is told why. The Saturday-night window may run "
       "deep or above; the 85-40-minute pre-lock rebuild is ALWAYS standard, whatever the time and memory",
       fits["name"] == "maximum" and down["name"] == "deep" and "maximum would take" in down["why"]
       and floor["name"] == "standard" and "deep would take" in floor["why"] and floor["requested"] == "maximum"
       and PP.is_standard_counts(floor) and sat["name"] == "deep" and sat["estimate_s"] == round(e_deep)
       and pre["name"] == "standard" and "inactives" in pre["why"] and pre["requested"] == "deep"
       and PP.PREGAME in inspect.getsource(W._cl_window_key), f"{fits['name']} {down['name']} {floor['why']}")
    # re-measured 2026-11-26 (research/hp_memory.py; tests/guards_auto_memory.py): the pool travels compact
    # (4 bytes a score, not ~38 in a Python list) and the make-it matrix moves to disk past a quarter of the
    # PC's memory, so deep and maximum fit a 32 GB PC; research's 3-million-lineup field does not
    m_std, m_deep, m_max, m_res = (PP.memory_gb(PP.from_cfg({"profile": n})[0], ram_gb=32) for n in PP.ORDER)
    small = PP.choose(mx, None, now, None, ram_gb=16)                 # the default slate shape (500 players)
    on32 = PP.choose(PP.from_cfg({"profile": "research"})[0], None, now, None, ram_gb=32)
    unknown = PP.choose(deep, None, now, board, ram_gb=None)
    ck("PF7 memory-aware: a build's peak is estimated from what it holds (the compact pool beside X, X and its "
       "shared copy, three copies of the lineup matrices, the make-it matrix while it fits a quarter of the "
       "memory) and must fit 80% of the PC's memory -- standard fits 16 GB, deep and maximum 32, research (its "
       "3,000,000-lineup field) falls to maximum on 32 and says what it needed; on 16 GB maximum falls to deep or "
       "standard; a PC whose memory cannot be read runs nothing above standard, never a guess",
       m_std < 0.8 * 16 and m_deep < 0.8 * 32 and m_max < 0.8 * 32 < m_res < 0.8 * 64
       and PP._SCORE_BYTES == 4.0 and small["name"] in ("deep", "standard")
       and "maximum needs ~" in small["why"] and "this PC has 16" in small["why"]
       and on32["name"] == "maximum" and "research needs ~" in on32["why"]
       and unknown["name"] == "standard" and "ram_gb" in unknown["why"]
       and PP.MEM_FRACTION == 0.8, f"{m_std:.1f} {m_deep:.1f} {m_max:.1f} {m_res:.1f}; {small['why']}; {on32['why']}")
    f_ok = PP.final_worlds(deep, now + 10 * 3600, now, board, ram_gb=64)
    f_late = PP.final_worlds(deep, now + 1200, now, board, ram_gb=64)
    f_unk = PP.final_worlds(deep, None, now, board, ram_gb=64)
    f_std = PP.final_worlds(PP.from_cfg({})[0], now + 10, now, None)
    ck("PF8 the final-evaluation worlds follow the profile only when the slate's lock is known and leaves room for "
       "the first evaluation to simulate them (and the memory holds them); otherwise the evaluator's own 40,000 "
       "-- and standard never asks",
       f_ok[0] == 160000 and f_late[0] == 40000 and "min to lock" in f_late[1] and f_unk[0] == 40000 and f_std[0] == 40000
       and W._eval_final_worlds(None)[0] == dfs_evaluate.FINAL_N
       and "dfs_evaluate.prepare(path, meta, n=n_final, log=log)" in inspect.getsource(W._eval_one))
    # --- the worker ---
    import guards_picker as GP
    import boardshare
    import dk
    calls = []
    main = {"draft_group_id": 4242, "games": 10, "starts": "2026-11-29T18:00:00.0000000Z", "contest_type": 21}

    def fake_build(dg, **kw):
        calls.append(kw)
        return {"kind": "classic", "built_ts": int(now), "candidates": 1, "worlds": kw.get("n_sims"), "sims": kw.get("n_sims"),
                "contests": [], "timings": {}, "checkpoint": {"key": "abc", "resumed": {}}}
    landed = []

    def task(cfg, window, ram=64.0, left=20 * 3600):
        import time as _t
        state = {}
        with GP._sandbox(), GP._patched(
                (T, "available", lambda: True), (dk, "slates", lambda sport: [main]),
                (dk, "slate_for", lambda sport, draft_group_id=None: {"csv": "x"}),
                (W, "_tourney_requests", lambda url, tok: {}), (W, "_main_slate", lambda slates: main),
                (W, "_cl_window_key", lambda st, now=None: window), (W, "_tourney_state", lambda: state),
                (W, "_tourney_state_save", lambda s: None), (W, "_report_attempt", lambda *a, **k: None),
                (W, "_ship_boards", lambda url, tok: None), (W, "_yield_point", lambda what: None),
                (W, "_tourney_resave", lambda *a: False), (W, "_dt_ts", lambda s: _t.time() + left),
                (W, "_cfg_all", lambda: cfg), (W, "_ram_gb", lambda: ram), (W, "_ckpt_landed", lambda art: landed.append(1)),
                (T, "build_nfl_classic", fake_build)):
            W._task_classic_tourney("http://x", "t")
            b = boardshare.get("cl_tourney_nfl_4242", None)[0] or {}
        return calls[-1], b.get("profile") or {}
    k_std, s_std = task({}, "2026-11-28 saturday night")
    k_deep, s_deep = task({"profile": "deep", "workers": 12}, "2026-11-28 saturday night")
    k_pre, s_pre = task({"profile": "deep"}, "2026-11-29 pregame")
    k_ram, s_ram = task({"profile": "research"}, "2026-11-28 saturday night", ram=8.0)
    k_late, s_late = task({"profile": "deep"}, "2026-11-28 saturday night", left=3 * 3600)
    src_task = inspect.getsource(W._task_classic_tourney)
    ck("PF9 the worker: with no profile configured the classic task makes production's call exactly (n_sims from "
       "_cl_sims, nothing else); under \"deep\" on Saturday night it passes the profile's worlds, split and field "
       "and the configured processes; in the pre-lock window, on a PC without the memory, or too near the lock it "
       "makes production's call again (the pre-lock one with fresh_inputs: always current information) -- and "
       "EVERY board is stamped with the profile it ran, the one asked for and why, so research can compare boards "
       "by the worlds behind them",
       k_std == {"min_pool": W._CL_MIN_POOL, "n_sims": 180000, "log": print} and s_std["name"] == "standard"
       and s_std["requested"] == "standard" and (s_std["generation"], s_std["ranking"]) == (20000, 160000)
       and k_deep == {"min_pool": W._CL_MIN_POOL, "log": print, "n_sims": 550000, "opt_worlds": 50000,
                      "field_n": 300000, "workers": 12}
       and s_deep["name"] == "deep" and s_deep["workers"] == 12 and "fits" in s_deep["why"]
       and k_pre == dict(k_std, fresh_inputs=True) and s_pre["name"] == "standard" and s_pre["requested"] == "deep"
       and "inactives" in s_pre["why"]
       and k_ram == k_std and s_ram["requested"] == "research" and "needs ~" in s_ram["why"]
       and k_late == k_std and "would take" in s_late["why"] and len(landed) == 5
       and "build_nfl_classic(dg, min_pool=_CL_MIN_POOL, n_sims=n_sims, log=print, **fresh)" in src_task
       and src_task.index("_stamp_profile(art, prof)") < src_task.index("boardshare.put(name, art)"),
       f"{k_deep} {s_deep} {s_late}")
    env = {}
    with GP._patched((W, "_cfg_all", lambda: {"profile": "maximum", "workers": 8, "checkpoint_gb": 16}),
                     (W, "_cfg_value", lambda key: {"checkpoints": None}.get(key))):
        notes = W._apply_limits(env)
    env_std = {}
    with GP._patched((W, "_cfg_all", lambda: {}), (W, "_cfg_value", lambda key: None)):
        W._apply_limits(env_std)
    env_off = {}
    with GP._patched((W, "_cfg_all", lambda: {}), (W, "_cfg_value", lambda key: False if key == "checkpoints" else None)):
        W._apply_limits(env_off)
    saved_env = {k: os.environ.get(k) for k in ("VIGIL_POOL_CACHE_GB", "VIGIL_POOL_CACHE_ENTRIES", "VIGIL_TOURNEY_WORKERS")}
    try:
        os.environ.update(VIGIL_POOL_CACHE_GB="48", VIGIL_POOL_CACHE_ENTRIES="9", VIGIL_TOURNEY_WORKERS="3")
        lim, wk, wk_arg = PC.limits(), T._workers(None), T._workers(2)
        os.environ.update(VIGIL_POOL_CACHE_GB="x", VIGIL_POOL_CACHE_ENTRIES="-4", VIGIL_TOURNEY_WORKERS="many")
        lim_bad, wk_bad = PC.limits(), T._workers(None)
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    ck("PF10 the limits reach the code that enforces them: the worker sets the worlds cache's disk limit and entry "
       "count, the checkpoint budget, the process count and the VRAM fraction in its environment (so every child "
       "inherits them); pool_cache.limits, dfs_tourney._workers and the evaluator's GPU read them; standard sets "
       "today's 12 GB / 24 entries, no process cap and no VRAM limit; an unreadable value is the default",
       notes == [] and env["VIGIL_POOL_CACHE_GB"] == "48" and env["VIGIL_CKPT_GB"] == "16" and env["VIGIL_TOURNEY_WORKERS"] == "8"
       and env["VIGIL_GPU_VRAM_FRAC"] == "0.8" and env["VIGIL_CKPT_DIR"].endswith("ckpt")
       and env_std["VIGIL_POOL_CACHE_GB"] == "12" and env_std["VIGIL_POOL_CACHE_ENTRIES"] == "24"
       and "VIGIL_TOURNEY_WORKERS" not in env_std and "VIGIL_GPU_VRAM_FRAC" not in env_std and env_off["VIGIL_CKPT_DIR"] == ""
       and lim == (9, 48 * 1024 ** 3) and wk == 3 and wk_arg == 2 and lim_bad == (PC.KEEP_ENTRIES, PC.MAX_BYTES)
       and wk_bad == max(1, os.cpu_count() or 1)
       and "set_limit(fraction=frac)" in inspect.getsource(dfs_evaluate._gpu) and "VIGIL_GPU_VRAM_FRAC" in inspect.getsource(dfs_evaluate._gpu),
       f"{env} {lim} {wk}")
    res_board = dict(board, timings=dict(board["timings"], total_s=300.0, score_s=20.0, resumed=True))
    art = {"sims": 180000, "timings": {"total_s": 300.0}, "checkpoint": {"key": "k", "resumed": {"score": "x"}}}
    art0 = {"sims": 180000, "timings": {"total_s": 300.0}, "checkpoint": {"key": "k", "resumed": {}}}
    W._stamp_profile(art, PP.choose(PP.from_cfg({})[0], None, now, None))
    W._stamp_profile(art0, None)
    ck("PF12 a resumed build's stage times are the restart's, not a build's: the worker marks them on the board and "
       "neither the standard rule's estimate (_cl_full_estimate) nor a profile's is scaled from them -- both fall "
       "back to the 45-minute guess, so a five-minute resume never promises a five-minute build before lock",
       art["timings"].get("resumed") is True and "resumed" not in art0["timings"] and "profile" in art and "profile" not in art0
       and W._cl_full_estimate(dict(board, timings={"total_s": 300.0, "resumed": True})) == float(W._CL_FULL_GUESS_S)
       and W._cl_full_estimate(dict(board, timings={"total_s": 300.0})) == 300.0
       and PP.estimate_s(deep, res_board)[0] == e_none and PP.final_worlds(deep, now + 10 * 3600, now, res_board, ram_gb=64)[0] == 40000)
    ex = json.load(open(os.path.join(_ROOT, "vigil-pc.cfg.example")))
    md = open(os.path.join(_ROOT, "CLAUDE.md")).read()
    bat = os.path.join(_ROOT, "vigil-pc.bat")
    ck("PF11 the example config ships \"profile\": \"standard\" (and stays valid JSON with no secret in it); the "
       "working agreement documents resumable builds and the profiles under The PC offload; the picked build "
       "follows the plain board's profile rule so the two can share cached worlds",
       ex.get("profile") == "standard" and "PASTE" in ex.get("sim_token", "")
       and "build_ckpt" in md and "pc_profiles" in md and md.index("## The PC offload") < md.index("build_ckpt")
       and "pk_window = _cl_window_key(st)" in inspect.getsource(W._picked_builds)
       and "prof = _cl_profile(st, plain, pk_window, picked=True)" in inspect.getsource(W._picked_builds)
       and (not os.path.exists(bat) or "profile" not in open(bat, errors="replace").read().lower()))


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
