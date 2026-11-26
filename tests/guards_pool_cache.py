"""Guards for the simulated-worlds disk cache (pool_cache, 2026-11-26;
research/reports/dfs_cache_evaluator.md stage 1).

The owner's requirements, each pinned below: deterministic, versioned keys
(the call, the code, every input); exact round trip (the loaded pool equals
the built one, float for float); integer storage only where every value
round-trips; integrity verification; atomic writes; retention limits;
explicit invalidation; the server never touches a matrix. And end to end:
`nfl_dfs_sim.player_pool` on the engine model, built once, read back from
disk by a fresh call without simulating a single game, the pool identical.

Hermetic: a temporary cache directory, the week's games, the engine's
context and its simulated game are stubs; nothing is fetched.

    python3 tests/guards_pool_cache.py       # exits 1 on any failed check
    from tests.guards_pool_cache import run  # run(ck) inside the main suite
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


def _games():
    def pl(name, pos, team, opp, proj):
        return {"name": name, "pos": pos, "team": team, "opp": opp, "proj_pts": proj,
                "means": {"rec_tgt": 5.0}, "recon": {"rec_tgt": 5.0}}
    return {"g1": {"label": "AAA @ BBB", "teams": ["AAA", "BBB"], "home": "BBB", "away": "AAA",
                   "players": [pl("Qa", "QB", "AAA", "BBB", 18.0), pl("Wa", "WR", "AAA", "BBB", 12.5),
                               pl("Qb", "QB", "BBB", "AAA", 19.0), pl("Rb", "RB", "BBB", "AAA", 14.1)]}}


def run(ck):
    try:
        import numpy as np
    except ImportError:
        ck("(numpy absent: the worlds cache runs where the boards are built)", True)
        return
    import pool_cache as PC
    tmp = tempfile.mkdtemp()
    old = os.environ.get("VIGIL_POOL_CACHE_DIR")
    os.environ["VIGIL_POOL_CACHE_DIR"] = tmp
    try:
        _run(ck, np, PC, tmp)
    finally:
        if old is None:
            os.environ.pop("VIGIL_POOL_CACHE_DIR", None)
        else:
            os.environ["VIGIL_POOL_CACHE_DIR"] = old
        shutil.rmtree(tmp, ignore_errors=True)


def _run(ck, np, PC, tmp):
    rng = np.random.default_rng(7)
    # ---- encoding: integer hundredths only where exact ------------------------
    lattice = [list(np.round(rng.normal(12, 8, 500), 2)) for _ in range(6)]
    m, dt, sc = PC.encode(lattice)
    back = [PC.decode_row(m[i], dt, sc) for i in range(len(lattice))]
    ck("PC1 scores on the hundredth lattice are stored as int32 hundredths and come back as the very same floats "
       "(every element, negatives included)", dt == "int32" and sc == 100
       and all(a == b for r0, r1 in zip(lattice, back) for a, b in zip(r0, r1)))
    off = [[1.0, 2.005, 3.3333333], [0.1, 0.2, 0.3]]
    m2, dt2, _ = PC.encode(off)
    ck("PC2 a value off the hundredth lattice keeps the whole entry float64, so nothing is rounded on the way in",
       dt2 == "float64" and [PC.decode_row(m2[i], dt2, 1) for i in range(2)] == off)
    big = [[2.0e7, -2.0e7, 0.01]]
    m3, dt3, sc3 = PC.encode(big)
    ck("PC3 the int32 range holds +/-20 million points exactly (no overflow on any score a box can produce)",
       dt3 == "int32" and PC.decode_row(m3[0], dt3, sc3) == big[0])
    # ---- keys -----------------------------------------------------------------------
    call = {"season": "2026", "week": 12, "n": 500, "teams": ["AAA", "BBB"], "model": "engine", "seed": 11}
    g = _games()
    k1 = PC.key(call, PC.inputs_hash(games=g, profiles={"A": 1}, drive={"AAA": 21.0}))
    g_reord = {"g1": dict(reversed(list(g["g1"].items())))}
    k2 = PC.key(dict(reversed(list(call.items()))), PC.inputs_hash(drive={"AAA": 21.0}, profiles={"A": 1}, games=g_reord))
    g_inj = json.loads(json.dumps(g))
    g_inj["g1"]["players"][1]["injury_status"] = "Questionable"
    k3 = PC.key(call, PC.inputs_hash(games=g_inj, profiles={"A": 1}, drive={"AAA": 21.0}))
    k4 = PC.key(dict(call, seed=12), PC.inputs_hash(games=g, profiles={"A": 1}, drive={"AAA": 21.0}))
    k5 = PC.key(call, PC.inputs_hash(games=g, profiles={"A": 1}, drive={"AAA": 21.5}))
    ck("PC4 the key is deterministic (dict order changes nothing) and moves with any input -- an injury tag, the "
       "seed, a drive level -- and it carries the simulator's code and the cache version",
       k1 == k2 and len({k1, k3, k4, k5}) == 4 and "code_sig()" in inspect.getsource(PC.key)
       and '"v": CACHE_VERSION' in inspect.getsource(PC.key)
       and all(f in PC._CODE_FILES for f in ("nfl_dfs_sim.py", "nfl_dfs_engine.py", "nfl_deep_sim.py")))
    # ---- save / load ------------------------------------------------------------------
    pool = {f"P{i}": {"pos": "WR", "team": "AAA", "opp": "BBB", "proj": 10.0 + i, "ceiling": 20.0, "floor": 3.0,
                      "arr": [float(x) for x in np.round(rng.normal(10, 6, 400), 2)], "sim_mean": 10.1, "rec_tgt": 4.0}
            for i in range(5)}
    pool["Empty"] = {"pos": "DST", "team": "BBB", "opp": None, "proj": 5.0, "arr": []}
    size = PC.save(k1, pool, call, engine_report={"games": {"g1": {"x": 1}}}, snapshot=g)
    hit = PC.load(k1)
    ck("PC5 a saved pool loads back identical: every player, every field, every array float for float, the entry "
       "with no array kept, the engine report and the projection snapshot in the manifest",
       size and hit is not None and hit[0] == pool and hit[1] == {"games": {"g1": {"x": 1}}}
       and hit[2]["snapshot"] == json.loads(PC._canon(g)) and hit[2]["dtype"] == "int32")
    ck("PC6 the write is atomic: the entry appears only complete (no temporary directory left behind) and a second "
       "save under the same key does not overwrite it",
       not any(".tmp-" in n for n in os.listdir(tmp)) and PC.save(k1, pool, call) is None)
    # integrity: a flipped byte is a miss, and the entry is removed
    apath = os.path.join(tmp, k1, "arrays.npy")
    with open(apath, "r+b") as f:
        f.seek(-3, 2)
        b = f.read(1)
        f.seek(-3, 2)
        f.write(bytes([b[0] ^ 0xFF]))
    ck("PC7 integrity: one flipped byte in the arrays fails the sha256 check, reads as a miss and the entry is "
       "deleted (never served corrupt)", PC.load(k1) is None and not os.path.isdir(os.path.join(tmp, k1)))
    # retention and explicit invalidation
    keys = []
    for i in range(5):
        kk = PC.key(dict(call, seed=100 + i), "x")
        PC.save(kk, pool, dict(call, seed=100 + i))
        os.utime(os.path.join(tmp, kk), (1_000_000 + i, 1_000_000 + i))
        keys.append(kk)
    PC.prune(keep=3)
    left = sorted(os.path.basename(p) for p, _m, _b in PC.entries())
    stale = os.path.join(tmp, "dead.tmp-1-1")
    os.makedirs(stale)
    os.utime(stale, (1_000_000, 1_000_000))
    PC.prune(keep=3)
    ck("PC8 retention keeps the newest entries (by last use) and removes the rest, and a temporary directory an "
       "interrupted write left behind is swept after an hour",
       left == sorted(keys[2:]) and not os.path.exists(stale))
    PC.prune(keep=10, max_bytes=1)
    ck("PC9 the byte cap holds: past MAX_BYTES the oldest go first", len(PC.entries()) <= 1)
    PC.clear()
    ck("PC10 clear() is explicit invalidation: nothing is left", PC.entries() == [] and os.listdir(tmp) == [])
    # ---- end to end: player_pool on the engine model ------------------------------------
    import nfl_dfs_engine as ENG
    import nfl_dfs_sim as S
    calls = {"sim": 0}
    saved = {f: getattr(S, f) for f in ("weekly_games", "dst_projections", "kicker_projections")}
    saved_eng = {f: getattr(ENG, f) for f in ("week_context", "simulate_game")}

    def fake_sim(game, n, rng_, ctx):
        calls["sim"] += 1
        out = []
        for p in game["players"]:
            arr = np.round(rng_.normal(p["proj_pts"], 7, n), 2)
            out.append({"name": p["name"], "pos": p["pos"], "team": p["team"], "opp": p["opp"],
                        "proj_pts": p["proj_pts"], "ceiling": float(np.percentile(arr, 90)),
                        "floor": float(np.percentile(arr, 10)), "arr": arr, "sim_mean": float(arr.mean())})
        return {"players": out, "dst": {t: np.round(rng_.normal(6, 4, n), 2) for t in ("AAA", "BBB")},
                "k": {t: np.round(rng_.normal(8, 3, n), 0) for t in ("AAA", "BBB")}, "anchor": {"ok": 1},
                "team_def": {}}
    try:
        S.weekly_games = lambda season, week: _games()
        S.dst_projections = lambda season, week: {"AAA": {"nickname": "Aces", "proj": 6.0},
                                                  "BBB": {"nickname": "Bees", "proj": 5.5}}
        S.kicker_projections = lambda season, week: {"AAA": {"name": "Ka", "pts": 8.0}, "BBB": {"name": "Kb", "pts": 7.5}}
        ENG.week_context = lambda season, week, workers=1: {"T": {}, "profiles": {"AAA": {}, "BBB": {}},
                                                             "drive": {"AAA": 21.0, "BBB": 23.0}, "weight": 0.3}
        ENG.simulate_game = fake_sim
        S._cache.clear()
        p1 = S.player_pool(12, n=300, season="2026", teams={"AAA", "BBB"}, model="engine", seed=5)
        n_first = calls["sim"]
        S._cache.clear()                     # a fresh process: nothing in memory
        p2 = S.player_pool(12, n=300, season="2026", teams={"AAA", "BBB"}, model="engine", seed=5)
        n_second = calls["sim"] - n_first
        S._cache.clear()
        p3 = S.player_pool(12, n=300, season="2026", teams={"AAA", "BBB"}, model="engine", seed=6)
        n_third = calls["sim"] - n_first - n_second
        ck("PC11 end to end: player_pool on the engine model simulates once, files the worlds, and a fresh call "
           "(memory cleared, as in a new process) reads them back WITHOUT simulating a game -- the pool identical "
           "player for player, array for array; another seed is another entry and simulates again",
           n_first == 1 and n_second == 0 and p1 == p2 and n_third == 1 and p3 != p1 and len(PC.entries()) == 2,
           f"sims {n_first}/{n_second}/{n_third}, equal {p1 == p2}, entries {len(PC.entries())}")
        os.environ.pop("VIGIL_POOL_CACHE_DIR")
        S._cache.clear()
        before = calls["sim"]
        S.player_pool(12, n=300, season="2026", teams={"AAA", "BBB"}, model="engine", seed=5)
        ck("PC12 without VIGIL_POOL_CACHE_DIR (the server) the cache is never consulted: the game is simulated",
           calls["sim"] == before + 1 and not PC.enabled())
        os.environ["VIGIL_POOL_CACHE_DIR"] = tmp
    finally:
        for f, v in saved.items():
            setattr(S, f, v)
        for f, v in saved_eng.items():
            setattr(ENG, f, v)
        S._cache.clear()
    src = inspect.getsource(S.player_pool)
    ck("PC13 only reproducible worlds are filed: a game that fell back to the unseeded legacy model keeps its "
       "slate out of the cache, and only the engine model is cached",
       "if pc_key and pool and not eng_fallback:" in src and 'if model == "engine" and eng_ctx and eng_ctx.get("profiles"):' in src)
    ck("PC14 the PC worker points the cache inside pc-simcache (gitignored); nothing else sets it",
       'os.environ.setdefault("VIGIL_POOL_CACHE_DIR"' in open(os.path.join(_ROOT, "pc_worker.py")).read()
       and "VIGIL_POOL_CACHE_DIR" not in open(os.path.join(_ROOT, "app.py")).read()
       and "pc-simcache/" in open(os.path.join(_ROOT, ".gitignore")).read())


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
