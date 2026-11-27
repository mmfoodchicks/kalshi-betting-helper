"""Guards for the build's memory work (2026-11-26; research/hp_memory.py measures it). The owner, before buying
RAM: "investigate whether Vigil actually needs to hold that much information in memory simultaneously". Every
change here must leave a board exactly as it was:

  * the compact pool (nfl_dfs_sim.player_pool(compact=True)): each player's worlds as the float32 row X holds,
    not a list of Python floats; int32 hundredths while it is built; the disk cache files the same bytes;
  * pool_cache.encode row by row, not three whole float64 copies;
  * nfl_dfs_sim._cached drops expired entries when a new one comes in, and keeps no second copy of a compact
    pool the disk cache holds;
  * the portfolio pass's make-it matrix filled in place, and past its memory budget a file on disk.

    python3 tests/guards_auto_memory.py      # exits 1 on any failed check
"""
import contextlib
import os
import sys
import tempfile

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@contextlib.contextmanager
def _env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _old_encode(np, arrs):
    """pool_cache.encode as it was before 2026-11-26, the reference."""
    m = np.asarray(arrs, dtype=np.float64)
    k = np.rint(m * 100.0)
    if np.all(np.abs(k) < 2 ** 31 - 1) and np.array_equal(k / 100.0, m):
        return k.astype(np.int32), "int32", 100
    return m, "float64", 1


def _same_enc(a, b):
    return a[1] == b[1] and a[2] == b[2] and a[0].dtype == b[0].dtype and a[0].shape == b[0].shape \
        and a[0].tobytes() == b[0].tobytes()


def run(ck):
    try:
        import numpy as np
    except ImportError:
        ck("MEM numpy present", True, "skipped: no numpy")
        return
    import pool_cache as PC
    rng = np.random.default_rng(11)
    lat = [np.round(rng.normal(12, 7, 500), 2).tolist() for _ in range(6)]
    off = [x[:] for x in lat]
    off[3][7] = 1.0 / 3.0
    nan = [x[:] for x in lat]
    nan[2][0] = float("nan")
    big = [x[:] for x in lat]
    big[0][0] = 3.0e7
    cases = [lat, off, nan, big, [lat[0]]]
    enc_ok = all(_same_enc(PC.encode(c), _old_encode(np, c)) for c in cases)
    mixed = [PC.squeeze(np.asarray(r)) for r in lat[:3]] + [np.asarray(r) for r in lat[3:5]] + [lat[5]]
    mixed_off = [PC.squeeze(np.asarray(r)) for r in off[:3]] + [np.asarray(r) for r in off[3:]]
    ck("MEM1 pool_cache.encode, now row by row, gives the old whole-matrix answer byte for byte (int32 hundredths "
       "when every value round-trips, else float64 of every row; off-lattice, NaN and out-of-range values), and a "
       "compact pool's rows (Hundredths, float64 arrays, lists) encode to the same matrix as the list pool's",
       enc_ok and _same_enc(PC.encode(mixed), _old_encode(np, lat))
       and _same_enc(PC.encode(mixed_off), _old_encode(np, off))
       and isinstance(PC.squeeze(np.asarray(lat[0])), PC.Hundredths)
       and not isinstance(PC.squeeze(np.asarray(off[3])), PC.Hundredths))
    negz = [x[:] for x in lat]
    negz[1][3] = -0.0                                   # np.round(-0.001, 2): a negative zero int32 cannot hold
    f32 = [np.asarray(r, dtype=np.float32) for r in lat + off + negz]
    via = [PC.as_float32(PC.squeeze(np.asarray(r))) for r in lat + off + negz]
    m, dt, sc = PC.encode(lat)
    ck("MEM2 a compact pool's float32 rows are exactly np.asarray(list, float32) -- the rows X was built from the "
       "lists, bit for bit, a negative zero included (such a row is never squeezed to int32) -- whether made from "
       "Hundredths, a float64 array or read back off the cache's matrix (decode_f32)",
       all(a.dtype == np.float32 and a.tobytes() == b.tobytes() for a, b in zip(via, f32))
       and not isinstance(PC.squeeze(np.asarray(negz[1])), PC.Hundredths)
       and all(PC.decode_f32(m[i], dt, sc).tobytes() == np.asarray(PC.decode_row(m[i], dt, sc), dtype=np.float32).tobytes()
               for i in range(len(lat)))
       and PC.has_array([1.0]) and not PC.has_array([]) and not PC.has_array(None)
       and PC.has_array(np.zeros(3, np.float32)) and not PC.has_array(np.zeros(0)))

    import nfl_dfs_sim as S
    saved_cache = dict(S._cache)
    try:
        S._cache.clear()
        S._cached(("a",), 0.0, lambda: 1)              # expired the moment it is stored
        S._cached(("b",), 1000.0, lambda: 2)
        evicted = ("a",) not in S._cache and ("b",) in S._cache
        S._cached(("c",), 1000.0, lambda: 3, keep=lambda: False)
        not_kept = ("c",) not in S._cache
        calls = []
        S._cached(("b",), 1000.0, lambda: calls.append(1) or 9)
        hit_ok = not calls
    finally:
        S._cache.clear()
        S._cache.update(saved_cache)
    ck("MEM3 the pool's in-process cache lets an expired entry go when a new one comes in (it used to keep every "
       "pool a PC cycle built until the cycle ended), stores nothing a caller says not to keep, and still answers "
       "a live key without building", evicted and not_kept and hit_ok)

    _pool_checks(ck, np, PC, S)
    _makeit_checks(ck, np)
    _board_checks(ck, np)
    _probe_checks(ck)


def _probe_checks(ck):
    """The PC measures what its builds hold (memprobe) and stamps it beside the estimate."""
    import inspect
    import json as _json
    import memprobe
    import pc_worker as W
    w = memprobe.Watch(every=0.05)
    blob = bytearray(64 * 1024 * 1024)
    import time as _t
    _t.sleep(0.2)
    got = w.stop()
    del blob
    posts = []
    saved = W._api
    art = {"sims": 180000, "players": [{"name": str(i)} for i in range(300)], "contests": [{}, {}], "candidates": 150000}
    try:
        W._api = lambda url, tok, path, data=None, headers=None, timeout=90: posts.append((path, _json.loads(data)))
        prof = {"name": "standard", "generation": 20000, "ranking": 160000, "final": 40000, "field": 300000}
        W._mem_report("u", "t", art, prof, memprobe.Watch(every=0.05), 4242, "classic")

        def boom(*a, **k):
            raise OSError("server down")
        W._api = boom
        art2 = {}
        W._mem_report("u", "t", art2, None, memprobe.Watch(every=0.05), 4243, "classic", "RuntimeError: x")
        survived = True
    except Exception:
        survived = False
    finally:
        W._api = saved
    rep = posts[0][1] if posts else {}
    src = inspect.getsource(W._task_classic_tourney)
    psrc = inspect.getsource(W._picked_builds)
    ck("MEM7 every PC tournament build is measured while it runs (memprobe: this process's resident memory and "
       "the machine's memory in use, sampled each second, standard library only) and the board carries it beside "
       "pc_profiles' estimate for that build; one build_memory report goes to /api/pc/bench; a failed build is "
       "reported too, and nothing here can stop a build",
       got["process_peak_gb"] is not None and got["process_peak_gb"] >= got["process_start_gb"]
       and got["system_total_gb"] and "memory" in art and art["memory"].get("estimate_gb", 0) > 0
       and art["memory"]["worlds"] == 180000 and posts and posts[0][0] == "/api/pc/bench"
       and rep.get("kind") == "build_memory" and rep["results"][0]["dg"] == 4242 and survived
       and src.index("mem = _mem_watch()") < src.index("build_nfl_classic(")
       and src.index('_mem_report(url, tok, art, prof, mem, dg, "classic")') < src.index("boardshare.put(name, art)")
       and psrc.index("mem = _mem_watch()") < psrc.index("build_nfl_classic(")
       and psrc.count("_mem_report(") == 2, str(got))


def _pool_checks(ck, np, PC, S):
    """player_pool(compact=True) against the list pool, through the engine path with the engine stubbed."""
    import nfl_dfs_engine as ENG

    def pl(name, pos, team, opp, proj):
        return {"name": name, "pos": pos, "team": team, "opp": opp, "proj_pts": proj,
                "means": {"rec_tgt": 5.0}, "recon": {"rec_tgt": 5.0}}
    games = {f"g{i}": {"label": f"{a} @ {b}", "teams": [a, b], "home": b, "away": a,
                       "players": [pl(f"Q{a}", "QB", a, b, 18.0), pl(f"W{a}", "WR", a, b, 12.5),
                                   pl(f"Q{b}", "QB", b, a, 19.0), pl(f"R{b}", "RB", b, a, 14.1)]}
             for i, (a, b) in enumerate((("AAA", "BBB"), ("CCC", "DDD")))}

    def fake_sim(game, n, rng_, ctx):
        out = []
        for p in game["players"]:
            arr = rng_.normal(p["proj_pts"], 7, n)
            if p["name"] == "QBBB":
                arr[5] = 1.0 / 3.0 * 100.0001         # one row off the 0.01 lattice after rounding: float64 path
            out.append({"name": p["name"], "pos": p["pos"], "team": p["team"], "opp": p["opp"],
                        "proj_pts": p["proj_pts"], "ceiling": float(np.percentile(arr, 90)),
                        "floor": float(np.percentile(arr, 10)), "arr": arr, "sim_mean": float(arr.mean())})
        return {"label": game["label"], "teams": game["teams"], "players": out, "n_sims": n,
                "dst": {t: np.round(rng_.normal(6, 4, n), 2) for t in game["teams"]},
                "k": {t: np.round(rng_.normal(8, 3, n), 0) for t in game["teams"]}, "anchor": {"ok": game["label"]},
                "team_def": {}, "components": {}, "boxes": None}
    names = ("weekly_games", "dst_projections", "kicker_projections")
    saved = {f: getattr(S, f) for f in names}
    saved_eng = {f: getattr(ENG, f) for f in ("week_context", "simulate_game")}
    tmp = tempfile.mkdtemp()
    res = {}
    try:
        S.weekly_games = lambda season, week: games
        S.dst_projections = lambda season, week: {t: {"nickname": t.title(), "proj": 6.0} for g in games.values() for t in g["teams"]}
        S.kicker_projections = lambda season, week: {t: {"name": "K" + t, "pts": 8.0} for g in games.values() for t in g["teams"]}
        ENG.week_context = lambda season, week, workers=1: {"T": {}, "profiles": {t: {} for g in games.values() for t in g["teams"]},
                                                             "drive": {"AAA": 21.0}, "weight": 0.3}
        ENG.simulate_game = fake_sim
        call = dict(season="2026", model="engine", seed=5)
        with _env(VIGIL_POOL_CACHE_DIR="", VIGIL_CKPT_DIR=None):
            S._cache.clear()
            res["lists"] = S.player_pool(12, n=300, **call)
            S._cache.clear()
            res["compact"] = S.player_pool(12, n=300, compact=True, **call)
            res["kept_mem"] = any(k[-1] == "compact" for k in S._cache if isinstance(k, tuple) and k[0] == "nfl_pool")
        for tag in ("lists", "compact"):
            with _env(VIGIL_POOL_CACHE_DIR=os.path.join(tmp, tag), VIGIL_CKPT_DIR=None):
                S._cache.clear()
                S.player_pool(12, n=300, compact=(tag == "compact"), **call)
                res["disk_kept_" + tag] = any(isinstance(k, tuple) and k[0] == "nfl_pool" for k in S._cache)
                ent = PC.entries()
                res["file_" + tag] = open(os.path.join(ent[0][0], "arrays.npy"), "rb").read() if len(ent) == 1 else None
                S._cache.clear()
                res["hit_" + tag] = S.player_pool(12, n=300, compact=True, **call)
    finally:
        for f, v in saved.items():
            setattr(S, f, v)
        for f, v in saved_eng.items():
            setattr(ENG, f, v)
        S._cache.clear()
    L, C = res["lists"], res["compact"]

    def same_pool(a, b, exact_bytes=True):
        # A pool read back off the disk cache carries +0.0 where a fresh one carried -0.0 (int32 hundredths
        # have no signed zero; the cache has always done this, list pools too, and no score or bucket can
        # tell them apart), so a read-back is compared by value and a fresh pool byte for byte.
        if set(a) != set(b):
            return False
        for nm in a:
            pa, pb = a[nm], b[nm]
            if {f: v for f, v in pa.items() if f != "arr"} != {f: v for f, v in pb.items() if f != "arr"}:
                return False
            want = np.asarray(pa["arr"], dtype=np.float32)
            if not (isinstance(pb["arr"], np.ndarray) and pb["arr"].dtype == np.float32
                    and (pb["arr"].tobytes() == want.tobytes() if exact_bytes else np.array_equal(pb["arr"], want))):
                return False
        return True
    ck("MEM4 the compact pool is the list pool exactly: every field but `arr` equal, every player's (and every "
       "defense's and kicker's) worlds the float32 row np.asarray(list, float32) gives -- including a row off the "
       "0.01 lattice, held as float64 while it was built; the disk cache files byte-identical matrices from either; "
       "a compact read of either file equals it (by value: the cache never kept a signed zero); a compact pool the disk holds is not also kept in memory, and one "
       "the disk does not hold still is",
       same_pool(L, C) and res["file_lists"] is not None and res["file_lists"] == res["file_compact"]
       and same_pool(L, res["hit_lists"], False) and same_pool(L, res["hit_compact"], False)
       and res["kept_mem"] and not res["disk_kept_compact"] and res["disk_kept_lists"],
       f"{sorted(L)[:3]} kept_mem={res['kept_mem']} disk_kept={res['disk_kept_compact']}/{res['disk_kept_lists']}")


def _makeit_checks(ck, np):
    """The make-it matrix in memory and on disk against the old concatenation. Since 2026-11-27 the matrix is
    packed rows (dfs_tourney.PackedRows) and the old pass itself is the frozen reference
    (tests/guards_auto_portfolio_speed.py, which also proves the packed pass equal to it on every route)."""
    import guards_auto_portfolio_speed as PS
    import guards_auto_resume_profiles as RP
    T, Wc, Wf, wf, X, grids = RP._lattice_case(np)          # the checkpoint guards' case: two contests, 0.02 lattice
    cand = np.arange(0, Wc.shape[0], 3)
    ref = PS.old_portfolio(np, T, Wc, Wf, wf, X, grids, cand, 5, chunk=16)
    scratch = tempfile.mkdtemp()
    with _env(VIGIL_SCRATCH_DIR=None, VIGIL_MAKEIT_RAM_GB=None):
        in_ram = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 5, chunk=16)
        in_ram2 = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 5, chunk=16, workers=2)
    seen = []
    real_open = np.lib.format.open_memmap

    def spy(path, *a, **k):
        seen.append(path)
        return real_open(path, *a, **k)
    np.lib.format.open_memmap = spy
    try:
        with _env(VIGIL_SCRATCH_DIR=scratch, VIGIL_MAKEIT_RAM_GB="0.0000001"):
            on_disk = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 5, chunk=16)
            on_disk2 = T.portfolio_vs_field(Wc, Wf, wf, X, grids, cand, 5, chunk=16, workers=2)
    finally:
        np.lib.format.open_memmap = real_open
    left = os.listdir(scratch)
    ck("MEM5 the portfolio pass's make-it matrix, held as packed rows range by range (no dense matrix, no "
       "concatenation) and, past its memory budget, files in the scratch directory mapped back, picks the same "
       "entries with the same cover float for float as the old pass's concatenation -- one process and two -- and "
       "the files are gone afterwards",
       in_ram == ref and in_ram2 == ref and on_disk == ref and on_disk2 == ref and left == []
       and len(seen) >= 2 and all(os.path.dirname(p) == scratch for p in seen)
       and T.makeit_file(10) is None and T.MAKEIT_RAM_BYTES >= 3 * 1024 ** 3,
       f"{len(seen)} maps, left {left}")


def _board_checks(ck, np):
    """A whole classic build from a list pool and from the same pool compact: the same board."""
    import guards_picker as GP
    import dfs_tourney as T
    import nfl_dfs as N
    import nfl_dfs_sim
    csv_c, pool_c, gated_c = GP._classic_slate()
    detail = {"id": 1, "name": "Guard Millionaire", "prize_pool": 2.0e6, "max_entries": 40000, "entry_fee": 20.0,
              "first_prize": 200000, "places_paid": 8000, "entered": 1000, "max_entries_per_user": 150,
              "payouts": [{"from": 1, "to": 1, "prize": 200000.0}, {"from": 2, "to": 10, "prize": 10000.0},
                          {"from": 11, "to": 8000, "prize": 40.0}]}
    slate = {"starts": "2026-11-01T18:00:00.0000000Z", "draft_group_id": 999,
             "contests": [{"id": 1, "prize_pool": 2.0e6, "entries": 1000}], "n_players": 104}
    kw = dict(n_sims=600, opt_worlds=200, field_n=3000, cand_n=2000, cal_n=1000, week=8, preseason=False,
              workers=1, probes=[], model="legacy", log=lambda *a: None, top=15, k_port=4, candidates=200, chunk=64,
              seed=17)
    base = {nm: dict(p, arr=[round(round(x / 0.02) * 0.02, 2) for x in p["arr"]])
            for nm, p in GP._fake_pool(pool_c, 600).items()}
    form = ["lists"]
    seen_kw = []

    def pool_fn(week, n=3000, **k):
        seen_kw.append(k.get("compact"))
        if form[0] == "lists":
            return {nm: dict(p) for nm, p in base.items()}
        return {nm: dict(p, arr=np.asarray(p["arr"], dtype=np.float32)) for nm, p in base.items()}
    mocks = [m for m in GP._board_mocks(T, N, csv_c, pool_c, gated_c, slate, detail)
             if not (m[0] is nfl_dfs_sim and m[1] == "player_pool")] + [(nfl_dfs_sim, "player_pool", pool_fn)]
    real_steer = T.steer_field
    small_steer = lambda draw, players, rng, beta, kappa, targets: real_steer(draw, players, rng, beta, kappa,
                                                                              targets, n=4000, iters=3)
    with GP._patched(*mocks, (T, "steer_field", small_steer), (T, "OPT_FIELD_N", 4000)):
        with _env(VIGIL_CKPT_DIR=None):
            a = T.build_nfl_classic(999, **kw)
            form[0] = "compact"
            b = T.build_nfl_classic(999, **kw)
    strip = lambda d: {k: v for k, v in (d or {}).items() if k not in ("built_ts", "timing", "timings", "eval_artifact")}
    ck("MEM6 a whole classic build from a compact pool (float32 rows) is the board built from the list pool, "
       "lineup for lineup and figure for figure, and the tournament asks player_pool for the compact form",
       bool(a) and strip(a) == strip(b) and seen_kw and all(v is True for v in seen_kw),
       f"{bool(a)} {bool(b)} {seen_kw[:2]} {[k for k in strip(a) if strip(a).get(k) != strip(b).get(k)][:6]}")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
