#!/usr/bin/env python3
"""Vigil smoke suite — ground-truth checks for the paths where a silent bug
costs money. Run BEFORE shipping any change to the sim, grading, DFS
optimizer, or store schema:

    python3 tests/run_smoke.py            # offline checks (fast, no network)
    python3 tests/run_smoke.py --online   # + live checks against today's slate

Every test here encodes a bug class that actually shipped once:
  - accumulator key drift        (deep rerun KeyError: 'sb')
  - optimizer boundary math      (DFS lineup $20 over the cap)
  - grading semantics            (DNP graded as a loss; remainder shown as
                                  season-end; picks missing home_won)
  - mask/joint-odds correctness  (everything the combo builder prices)
Zero dependencies beyond the app itself. Exit code 0 = all passed.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# The whole run uses a throwaway DB so tests can write picks/props freely.
# Must happen before anything imports store.
_TMP = tempfile.mkdtemp(prefix="vigil-smoke-")
os.environ["KALSHI_DB"] = os.path.join(_TMP, "smoke.db")

PASS, FAIL = 0, []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL {name} — {detail}")


# ---------------------------------------------------------------- offline --
def t_imports():
    """Every module must import (catches syntax + import-time errors)."""
    import glob
    import importlib
    bad = []
    root = os.path.dirname(HERE)
    for f in sorted(glob.glob(os.path.join(root, "*.py"))):
        m = os.path.basename(f)[:-3]
        if m == "app":
            continue          # imports last (registers routes, heavier)
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append((m, repr(e)[:80]))
    check("all modules import", not bad, bad[:3])


def t_accumulator_keys():
    """Deep-season accumulator factories must cover every key _accum_box folds
    and every key deep_sim's box lines carry — the KeyError:'sb' class."""
    import inspect
    import deep_season
    import deep_sim
    src = inspect.getsource(deep_season._sim_one_season)
    import re
    tuples = re.findall(r'dict\.fromkeys\(\s*\(([^)]*)\)', src)
    fact_bat = {k.strip().strip('"\'') for k in tuples[0].split(",") if k.strip()}
    fact_pit = {k.strip().strip('"\'') for k in tuples[1].split(",") if k.strip()}
    acc = inspect.getsource(deep_season._accum_box)
    fold = re.findall(r'for k in \(([^)]*)\)', acc)
    fold_bat = {k.strip().strip('"\'') for k in fold[0].split(",") if k.strip()}
    fold_pit = {k.strip().strip('"\'') for k in fold[1].split(",") if k.strip()}
    check("bat accumulator covers folded keys", fold_bat <= fact_bat,
          f"missing {fold_bat - fact_bat}")
    check("pit accumulator covers folded keys", fold_pit <= fact_pit,
          f"missing {fold_pit - fact_pit}")
    check("game bat line covers folded keys",
          fold_bat <= set(deep_sim._new_bat_line()),
          f"missing {fold_bat - set(deep_sim._new_bat_line())}")
    check("game pit line covers folded keys",
          fold_pit <= set(deep_sim._new_pit_line()),
          f"missing {fold_pit - set(deep_sim._new_pit_line())}")


def t_fee_consistency():
    """All Kalshi taker-fee implementations must agree."""
    import baseball
    import bestbets
    import odds
    import combine
    fns = [odds.taker_fee_cents, bestbets._fee, baseball._kalshi_fee]
    if hasattr(combine, "_kalshi_fee"):
        fns.append(combine._kalshi_fee)
    bad = []
    for c in (1, 10, 35, 50, 65, 90, 99):
        vals = [round(fn(c), 1) for fn in fns]
        if len(set(vals)) != 1:
            bad.append((c, vals))
    check("taker fee identical across implementations", not bad, bad)


def t_dfs_cap_and_exclusivity():
    """The knapsack must never exceed the cap (odd salaries included) and must
    never take two players from one exclusive group (UFC bout)."""
    import random
    import simulate
    rng = random.Random(11)
    over = 0
    for trial in range(30):
        players = [{"name": f"p{i}", "salary": rng.randrange(3111, 11987),
                    "proj": rng.uniform(5, 45), "game": f"g{i // 2}"}
                   for i in range(24)]
        lu = simulate.dfs_optimize(players, 6, 50000, key="proj")
        if lu and sum(p["salary"] for p in lu) > 50000:
            over += 1
    check("knapsack never over the cap (odd salaries)", over == 0, f"{over}/30 over")
    # Bout-exclusivity bait: both fighters of g0 are absurdly good + cheap.
    players = [{"name": "trapA", "salary": 5000, "proj": 99, "game": "g0"},
               {"name": "trapB", "salary": 5000, "proj": 98, "game": "g0"}]
    players += [{"name": f"f{i}", "salary": 6000 + i * 137, "proj": 20 - i * 0.3,
                 "game": f"g{1 + i // 2}"} for i in range(20)]
    lu = simulate.dfs_optimize(players, 6, 50000, key="proj",
                               exclusive_group=lambda p: p["game"])
    games = [p["game"] for p in (lu or [])]
    check("exclusive group never doubled (UFC bout)",
          lu is not None and games.count("g0") <= 1,
          f"lineup games: {games}")


def t_store_lifecycle():
    """Record -> grade -> report on a temp DB: pick grading with home_won,
    the model_split three-way, DNP props voiding out of accuracy."""
    import random
    import store
    store.init_db()
    rng = random.Random(3)
    # 60 synthetic games where the deep read is sharper than the factor read.
    for i in range(60):
        truth = rng.random() < 0.5
        pm = min(0.95, max(0.05, (0.62 if truth else 0.38) + rng.uniform(-0.18, 0.18)))
        pd = min(0.95, max(0.05, (0.62 if truth else 0.38) + rng.uniform(-0.06, 0.06)))
        blend = 0.65 * pm + 0.35 * pd
        side = "home" if blend >= 0.5 else "away"
        store.record_mlb_pick(9000 + i, "2026-07-01", side, f"T{i}",
                              blend if side == "home" else 1 - blend, 50,
                              p_home_model=pm, p_home_deep=pd)
        pick_won = 1 if (truth and side == "home") or (not truth and side == "away") else 0
        store.set_mlb_grade(9000 + i, pick_won, "W", home_won=1 if truth else 0)
    rec = store.mlb_record()
    ms = rec.get("model_split") or {}
    check("model_split has factor/deep/blend",
          all((ms.get(k) or {}).get("n") == 60 for k in ("factor", "deep", "blend")),
          {k: (ms.get(k) or {}).get("n") for k in ("factor", "deep", "blend")})
    check("deep_grades returns 60 rows", len(store.deep_grades()) == 60,
          len(store.deep_grades()))
    # Blend tuner: with deep deliberately sharper, tuned weight must be >= prior.
    import baseball
    baseball._DEEP_W_CACHE.update({"t": 0.0, "w": None})
    w = baseball._deep_wp_weight()
    check("blend tuner raises weight when deep is sharper", w >= 0.35, w)
    # Prop void: a scratched player must leave the queue without touching accuracy.
    with store._lock, store._conn() as c:
        c.execute("INSERT INTO prop_log (date, game_pk, player_id, name, stat, line, graded)"
                  " VALUES ('2026-07-01', 1, 42, 'Scratch', 'hits', 1, 0)")
        pid = c.execute("SELECT id FROM prop_log WHERE player_id=42").fetchone()["id"]
    store.grade_prop_void(pid)
    with store._lock, store._conn() as c:
        row = dict(c.execute("SELECT graded, actual FROM prop_log WHERE id=?", (pid,)).fetchone())
    check("DNP prop voids (graded=2, no result)",
          row["graded"] == 2 and row["actual"] is None, row)
    check("void prop leaves the pending queue",
          all(p["id"] != pid for p in store.ungraded_props()))


def t_pick6_rules():
    """DK reality: batter stats are More-only; only pitcher Ks offer Less."""
    import baseball
    check("Pick 6 Less allowed only on Ks", baseball._PICK6_LESS_OK == {"Ks"},
          baseball._PICK6_LESS_OK)


def t_clock():
    """The app clock must be Eastern (UTC-4 or UTC-5)."""
    import datetime
    import clock
    off = clock.now_et().utcoffset().total_seconds() / 3600
    check("clock is Eastern (UTC-4/-5)", off in (-4.0, -5.0), off)
    mid = clock.midnight_et_epoch()
    import time
    check("ET midnight is within the last 24h", 0 <= time.time() - mid < 86400,
          time.time() - mid)


def t_sim_worker_sizing():
    """The deep sim must size its pool to the CONTAINER, not the host.

    multiprocessing.cpu_count() reports the machine's cores, so on a small plan
    the sim would start eight workers on half a core against a 512 MB cap and be
    OOM-killed -- while the web app survives, so it looks like the run merely
    never finishes."""
    import os
    import deep_season as ds

    n = ds.default_workers()
    check("worker count is sane on this box", 1 <= n <= 8, n)

    had = os.environ.get("VIGIL_SIM_WORKERS")
    try:
        os.environ["VIGIL_SIM_WORKERS"] = "1"
        check("VIGIL_SIM_WORKERS pins the pool", ds.default_workers() == 1)
        os.environ["VIGIL_SIM_WORKERS"] = "not-a-number"
        check("a bad override falls back instead of crashing",
              1 <= ds.default_workers() <= 8)
    finally:
        os.environ.pop("VIGIL_SIM_WORKERS", None)
        if had is not None:
            os.environ["VIGIL_SIM_WORKERS"] = had

    orig = ds._cgroup_limit
    try:
        def small(v2, v1, parse):
            return parse("50000 100000") if "cpu" in v2 else parse(str(512 * 1024 * 1024))
        ds._cgroup_limit = small
        check("a 512 MB / half-core container gets 1 worker",
              ds.default_workers() == 1, ds.default_workers())

        def big(v2, v1, parse):
            return parse("400000 100000") if "cpu" in v2 else parse(str(4 << 30))
        ds._cgroup_limit = big
        check("a 4 GB / 4-core container gets 4 workers",
              ds.default_workers() == 4, ds.default_workers())

        def memtight(v2, v1, parse):
            # plenty of CPU, but only enough RAM for one worker
            return parse("800000 100000") if "cpu" in v2 else parse(str(400 * 1024 * 1024))
        ds._cgroup_limit = memtight
        check("memory binds before CPU when it is the tighter limit",
              ds.default_workers() == 1, ds.default_workers())

        def nolimits(v2, v1, parse):
            return None
        ds._cgroup_limit = nolimits
        check("no cgroup limits -> still bounded, never unbounded",
              1 <= ds.default_workers() <= 8)
    finally:
        ds._cgroup_limit = orig


def t_deep_run_data_quality():
    """A deep run built on incomplete data must not overwrite a good one.

    The MLB roster call hydrates season AND career stats together; career is what
    regresses a player toward true talent. When that hydration comes back partial
    -- observed live, where the deployed host produced a materially different
    board from the same code on a workstation -- every rate is taken at face
    value and the projection tracks each team's RECORD instead of its talent. It
    renders as a confident board, which is the worst way for this to fail."""
    import deep_season as ds

    def prof(players, career):
        return {"_quality": {"players": players, "with_career": career, "xstats": 1}}

    healthy = {i: prof(45, 43) for i in range(30)}
    q = ds.profile_quality(healthy)
    check("healthy profiles report near-full career coverage",
          q["career_frac"] > 0.9 and q["teams_ok"] == 30, q)

    broken = {i: prof(45, 1) for i in range(30)}
    qb = ds.profile_quality(broken)
    check("partial hydration is visible as low coverage", qb["career_frac"] < 0.1, qb)
    check("and no team passes its own bar", qb["teams_ok"] == 0, qb)

    mixed = {i: prof(45, 43 if i < 10 else 1) for i in range(30)}
    qm = ds.profile_quality(mixed)
    check("a partly-degraded run is caught too", qm["career_frac"] < 0.4, qm)
    check("teams_ok counts only the teams that loaded", qm["teams_ok"] == 10, qm)

    check("empty profiles do not divide by zero", ds.profile_quality({})["career_frac"] == 0.0)

    import app as _app
    thr = _app._MIN_CAREER_FRAC
    check("the publish gate sits below healthy but above degraded",
          qb["career_frac"] < thr < q["career_frac"], thr)


def t_tennis_memory_guards():
    """The tennis pool build is the biggest single memory event in the app, and
    on a small host it was the thing killing the instance.

    Measured: eight years of archive peaks at 436 MB to rebuild -- more than a
    512 MB container has -- while the finished pools are ~3 MB pickled. Held only
    in memory, every restart paid the full rebuild, which OOM-killed the instance,
    which restarted and rebuilt again. Two guards: persist the result so a restart
    reads it, and size the archive depth to the host so the rare rebuild fits
    (measured 213 MB at three years)."""
    import inspect
    import os as _os
    import tennis_elo
    import tennis_history as th

    src = inspect.getsource(tennis_elo._pools_from_disk_or_build)
    check("built pools are persisted, not just memoised",
          "deep_cache.save" in src and "deep_cache.load" in src)
    check("pools() goes through the persisted path",
          "_pools_from_disk_or_build" in inspect.getsource(tennis_elo.pools))

    had = _os.environ.get("VIGIL_TENNIS_YEARS")
    try:
        _os.environ["VIGIL_TENNIS_YEARS"] = "4"
        check("VIGIL_TENNIS_YEARS pins the depth", th.default_years() == 4)
        _os.environ["VIGIL_TENNIS_YEARS"] = "junk"
        check("a bad override falls back rather than crashing",
              1 <= th.default_years() <= 20)
    finally:
        _os.environ.pop("VIGIL_TENNIS_YEARS", None)
        if had is not None:
            _os.environ["VIGIL_TENNIS_YEARS"] = had

    import builtins
    real_open = builtins.open

    def fake(limit_mb):
        def _o(path, *a, **k):
            if "memory.max" in str(path):
                import io
                return io.StringIO(str(int(limit_mb * 1024 * 1024)))
            return real_open(path, *a, **k)
        return _o
    try:
        builtins.open = fake(512)
        small = th.default_years()
        builtins.open = fake(4096)
        big = th.default_years()
    finally:
        builtins.open = real_open
    check("a 512 MB host pulls a shallower archive than a 4 GB one",
          small < big, f"{small} vs {big}")
    check("even the small host pulls something", small >= 1, small)

    # the row cache must not serve a deeper archive than was asked for
    check("the row cache key carries the depth",
          "_CACHE_PREFIX" in inspect.getsource(th.results)
          and "years" in inspect.getsource(th.results))


def t_pool_build_isolation():
    """The tennis pool build must be isolated WITHOUT multiprocessing.

    It has to run out-of-process: in-process it leaves ~215 MB of unreleasable
    pymalloc arenas behind and a 512 MB container then has nothing left for the
    season sim. But both multiprocessing start methods are traps here. 'fork' can
    inherit a lock held by another thread of a threaded server. 'spawn'
    re-imports the parent's __main__, so any SCRIPT that reaches this -- every
    file in this directory -- gets re-run inside the child, calls back in, and
    spawns again. That is not hypothetical: it hung tennis_board_check.
    A plain subprocess on a fixed command has neither failure mode."""
    import inspect
    import tennis_elo

    import ast
    import textwrap

    src = inspect.getsource(tennis_elo._build_isolated)
    # Check the CODE, not the prose. The docstring names fork, spawn and
    # multiprocessing to explain why each is wrong here, so scanning raw source
    # would flag the explanation as the offence. Parse and drop the docstring.
    fn = ast.parse(textwrap.dedent(src)).body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        fn.body = fn.body[1:]
    body = ast.unparse(fn)
    check("the pool build is isolated out-of-process", "subprocess" in body)
    check("it does NOT use multiprocessing (fork/spawn are both unsafe here)",
          "multiprocessing" not in body and "get_context" not in body,
          [l.strip() for l in body.splitlines()
           if "multiprocessing" in l or "get_context" in l])
    check("it falls back to an in-process build rather than returning nothing",
          "_build()" in body)
    check("the child entry point exists and is module level",
          callable(getattr(tennis_elo, "_build_blob", None)))
    # pools() -> _pools_from_disk_or_build() -> _build_isolated(). Follow the
    # chain rather than assuming which link calls the builder, so inserting the
    # persistence layer does not read as the isolation having been lost.
    chain = (inspect.getsource(tennis_elo.pools)
             + inspect.getsource(tennis_elo._pools_from_disk_or_build))
    check("pools() reaches the isolated build", "_build_isolated" in chain)


def t_render_blueprint():
    """render.yaml must PARSE and carry every store, or the platform silently
    ignores it. Uncommenting the disk block by hand is easy to get wrong -- drop
    the '#' but keep its trailing space and every line is one column too deep,
    which yields a file that does not parse and a paid disk that never mounts."""
    import yaml
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    try:
        d = yaml.safe_load(open(os.path.join(root, "render.yaml")))
    except Exception as e:
        check("render.yaml parses", False, str(e).splitlines()[0])
        return
    check("render.yaml parses", True)
    svc = (d.get("services") or [{}])[0]
    keys = {e.get("key") for e in (svc.get("envVars") or [])}
    if svc.get("disk"):
        # With a disk attached, every store that must survive a restart has to be
        # pointed at it -- missing one means paying for a disk it never touches.
        for k in ("KALSHI_DB", "PREDLOG_DB", "DEEP_CACHE_DIR"):
            check(f"disk is mounted, so {k} points at it", k in keys, sorted(keys))
        check("the disk mounts where those paths expect it",
              (svc["disk"] or {}).get("mountPath") == "/data", svc.get("disk"))
    check("a health check path is set", bool(svc.get("healthCheckPath")),
          svc.get("healthCheckPath"))


def t_boot_is_survivable():
    """A fresh instance must not take itself down before it is healthy.

    Two failures, both seen for real on a first deploy: the platform's health
    check was the first request, so it triggered the whole bootstrap and timed
    out; and the scheduler's "stagger" only waited for a job to START, so every
    stale job piled up at once and starved the container. Restart, empty cache,
    repeat."""
    import time as _t
    import threading
    import app as _app
    import deep_cache as dc

    c = _app.app.test_client()
    _app._rec_started = False
    for path in ("/healthz", "/robots.txt"):
        t0 = _t.time()
        r = c.get(path)
        check(f"{path} answers fast", r.status_code == 200 and (_t.time() - t0) < 1.0,
              f"{r.status_code} in {_t.time()-t0:.2f}s")
    check("a platform probe does not trigger the bootstrap", not _app._rec_started)

    # heavy jobs must run one at a time no matter how many are stale
    saved_jobs, saved_started = dict(dc._jobs), dc._sched_started
    saved_grace = dc.STARTUP_GRACE
    try:
        dc._jobs.clear()
        dc._sched_started = False
        dc.STARTUP_GRACE = 0
        live = {"n": 0, "peak": 0}
        lock = threading.Lock()

        def mk():
            def run():
                with lock:
                    live["n"] += 1
                    live["peak"] = max(live["peak"], live["n"])
                _t.sleep(0.4)
                with lock:
                    live["n"] -= 1
                return {"ok": 1}
            return run
        for k in "abcdef":
            dc.register(k, mk())
            dc._jobs[k]["cadence"] = "age"
            dc._jobs[k]["max_age"] = 0
        dc.start_scheduler(check_every=9999)
        _t.sleep(4.0)
        check("stale jobs run ONE at a time, not all at once",
              live["peak"] == 1, f"peak concurrent = {live['peak']}")
    finally:
        dc._jobs.clear()
        dc._jobs.update(saved_jobs)
        dc._sched_started = saved_started
        dc.STARTUP_GRACE = saved_grace

    check("there is a startup grace before any heavy work", dc.STARTUP_GRACE > 0,
          dc.STARTUP_GRACE)

    had = os.environ.get("VIGIL_SIMS")
    try:
        os.environ["VIGIL_SIMS"] = "off"
        import importlib
        importlib.reload(dc)
        check("VIGIL_SIMS=off is a real kill switch", not dc.enabled())
        dc.register("z", lambda: {"a": 1})
        dc._jobs["z"]["cadence"] = "age"
        dc._jobs["z"]["max_age"] = 0
        check("switched off, the scheduler starts nothing", dc.run_job("z") is False)
        check("but a manual rerun still forces one through",
              dc.run_job("z", force=True) is True)
    finally:
        os.environ.pop("VIGIL_SIMS", None)
        if had is not None:
            os.environ["VIGIL_SIMS"] = had
        import importlib
        importlib.reload(dc)


# ----------------------------------------------------------------- online --
def t_mask_math_live():
    """Brute-force the combo engine's joint odds on a real game: the popcount
    joint must equal counting sims by hand; marginals must match masks; prop
    ladders must be monotonic."""
    import random
    import baseball
    import clock
    import mlb_sim
    date = clock.today_et().isoformat()
    games = baseball.analyze_slate(date, date[:4])
    g = next((x for x in games if (x.get("live") or {}).get("state") == "Preview"),
             games[0] if games else None)
    if g is None:
        check("mask math (live)", False, "no games on today's slate")
        return
    gs = baseball._game_sim(g)
    n = gs["sim"]["n"]
    full = (1 << n) - 1
    cands = [c for c in gs["cands"] if c.get("mask") is not None]
    rng = random.Random(7)
    joint_bad = marg_bad = 0
    for _ in range(50):
        legs = rng.sample(cands, k=rng.randint(2, 5))
        jm = full
        for c in legs:
            jm &= c["mask"]
        brute = sum(1 for i in range(n)
                    if all(c["mask"] & (1 << i) for c in legs))
        if mlb_sim._popcount(jm) != brute:
            joint_bad += 1
    # build_candidates temperature-scales the moneyline and batter props against
    # the graded record, so for those types marg is deliberately NOT the raw mask
    # fraction. The types left raw must still match it exactly.
    raw_types = {"Total", "Run line", "RFI", "Ks"}
    cal_bad = []
    for c in cands:
        raw = mlb_sim._popcount(c["mask"]) / n
        if c["type"] in raw_types:
            if abs(c["marg"] - raw) > 1e-9:
                marg_bad += 1
        # Calibration must stay a sane monotone nudge, never a rewrite.
        elif not (0.0 < c["marg"] < 1.0) or abs(c["marg"] - raw) > 0.15:
            cal_bad.append((c["label"], round(raw, 4), round(c["marg"], 4)))
    from collections import defaultdict
    lad = defaultdict(list)
    for c in cands:
        kr = c.get("kref") or {}
        if kr.get("player") and kr.get("line") is not None:
            # A NO leg shares its twin's (type, player, line), so bucketing
            # without the side compared a market against its own complement and
            # called every YES/NO pair a monotonicity break.
            lad[(kr["t"], kr["player"], c.get("side", "yes"))].append(
                (kr["line"], c["marg"]))
    # "n+" ladders only go one way: a higher line can never be likelier. On the
    # NO side the sense flips (NO 3+ hits is likelier than NO 1+ hits).
    mono_bad = 0
    for (t, player, side), seq in lad.items():
        s = sorted(seq)
        for (l1, p1), (l2, p2) in zip(s, s[1:]):
            if l2 == l1:
                continue
            if (p2 > p1 + 1e-9) if side != "no" else (p2 < p1 - 1e-9):
                mono_bad += 1
    check(f"joint odds == brute force ({g['matchup']})", joint_bad == 0, joint_bad)
    check("uncalibrated marginals match masks", marg_bad == 0, marg_bad)
    check("calibration is a bounded nudge", not cal_bad, cal_bad[:3])
    check("prop ladders monotonic (per side)", mono_bad == 0, mono_bad)
    # The same-game joint must be quoted on the same calibrated scale as its legs,
    # or a slip's headline probability disagrees with the legs printed under it.
    item = mlb_sim.best_same_game([c for c in cands], n, 3, 0.45, 0, 3)
    if item:
        floor = min(l["prob_pct"] for l in item["legs"])
        check("same-game joint <= its smallest leg",
              item["combined_prob_pct"] <= floor + 1e-6,
              f"{item['combined_prob_pct']} vs {floor}")
        by_label = {c["label"]: c for c in cands}
        raw_ind, raw_j = 1.0, None
        masks = []
        for l in item["legs"]:
            c = by_label.get(l["pick"])
            if not c:
                masks = []
                break
            raw_ind *= mlb_sim._popcount(c["mask"]) / n
            masks.append(c["mask"])
        if masks:
            jm = masks[0]
            for m in masks[1:]:
                jm &= m
            raw_j = mlb_sim._popcount(jm) / n
            true_corr = (raw_j - raw_ind) * 100
            check("corr_delta reports correlation, not the calibration gap",
                  abs(item["corr_delta_pct"] - true_corr) <= 1.0,
                  f"reported {item['corr_delta_pct']} vs true {true_corr:.2f}")


def t_season_end_projection_live():
    """Team futures: no projected counting stat may sit below the real one."""
    import deep_cache
    import deep_season
    import clock
    payload, _ts = deep_cache.load("mlb_deep")
    agg = (payload or {}).get("agg")
    if not agg:
        check("season-end >= current (skipped)", True, "no cached deep run")
        return
    season = (payload or {}).get("season") or str(clock.today_et().year)
    det = deep_season.team_detail(agg, season, 147)
    bad = []
    for r in det.get("pitching") or []:
        rp = r.get("real") or {}
        if r.get("has_sim") and (r.get("k") or 0) < (rp.get("k") or 0):
            bad.append(("K", r["name"]))
    for b in det.get("batting") or []:
        rb = b.get("real") or {}
        if b.get("has_sim") and (b.get("h") or 0) < (rb.get("h") or 0):
            bad.append(("H", b["name"]))
    check("season-end projection never below current", not bad, bad[:4])


def main():
    online = "--online" in sys.argv
    print("== offline ==")
    t_imports()
    t_accumulator_keys()
    t_fee_consistency()
    t_dfs_cap_and_exclusivity()
    t_store_lifecycle()
    t_pick6_rules()
    t_clock()
    t_sim_worker_sizing()
    t_deep_run_data_quality()
    t_tennis_memory_guards()
    t_pool_build_isolation()
    t_render_blueprint()
    t_boot_is_survivable()
    if online:
        print("== online (live data) ==")
        t_mask_math_live()
        t_season_end_projection_live()
    print(f"\n{PASS} passed, {len(FAIL)} failed"
          + ("" if online else "  (offline only — add --online for live checks)"))
    for name, detail in FAIL:
        print(f"  FAILED: {name} — {detail}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
