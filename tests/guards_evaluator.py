"""Guards for the custom lineup evaluator (dfs_evaluate, 2026-11-26;
research/reports/dfs_cache_evaluator.md stages 2-4).

A synthetic two-game classic slate and a synthetic showdown, each with a
saved artifact (players, contests and payout tables, the field), final worlds
passed in, and an independent brute force -- every field lineup scored in
every world, the field's mass strictly above the lineup counted directly --
that the evaluator's figures must equal exactly: top 0.1% / 1%, cash, the
finish ladder, the payout tier reached and so EV. Plus legality, the source
labels, the looks counter, the artifact's atomic write and retention.

Hermetic: a temporary cache directory; nothing simulated or fetched.

    python3 tests/guards_evaluator.py       # exits 1 on any failed check
    from tests.guards_evaluator import run  # run(ck) inside the main suite
"""
import os
import shutil
import sys
import tempfile

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)


def run(ck):
    try:
        import numpy as np
    except ImportError:
        ck("(numpy absent: the evaluator runs on the PC)", True)
        return
    tmp = tempfile.mkdtemp()
    old = os.environ.get("VIGIL_POOL_CACHE_DIR")
    os.environ["VIGIL_POOL_CACHE_DIR"] = tmp
    try:
        _classic(ck, np)
        _showdown(ck, np)
    finally:
        if old is None:
            os.environ.pop("VIGIL_POOL_CACHE_DIR", None)
        else:
            os.environ["VIGIL_POOL_CACHE_DIR"] = old
        shutil.rmtree(tmp, ignore_errors=True)


def _brute(np, X, idx_field, w_field, lineup_idx, contest, qs_named, cpt=None, field_cpt=False):
    """Per world: the field's mass strictly above the lineup, by direct count
    on 0.01-point buckets, then the tier and the named fractions."""
    import dfs_tourney
    Xf = X.astype(np.float32)
    W = X.shape[1]
    f_scores = Xf[idx_field].sum(axis=1, dtype=np.float32)            # (M, W) by gather
    if field_cpt:
        f_scores = f_scores + np.float32(0.5) * Xf[idx_field[:, 0]]
    fb = np.clip(np.floor(f_scores.astype(np.float64) * dfs_tourney._RES + 0.5), 0, dfs_tourney._NB - 1)
    s = Xf[lineup_idx].sum(axis=0, dtype=np.float32)
    if cpt is not None:
        s = s + np.float32(0.5) * Xf[cpt]
    b = np.clip(np.floor(s.astype(np.float64) * dfs_tourney._RES + 0.5), 0, dfs_tourney._NB - 1)
    above = ((fb > b[None, :]) * w_field[:, None]).sum(axis=0)          # (W,)
    out = {k: float(np.mean(above <= q + 1e-12)) for k, q in qs_named.items()}
    import dfs_evaluate as DE
    prize = np.zeros(W)
    got = np.zeros(W, dtype=bool)
    for q, p in DE.tier_fractions(contest):
        hit = (~got) & (above <= q + 1e-12)
        prize[hit] = p
        got |= hit
    out["ev"] = float(prize.mean()) - contest["entry_fee"]
    return out


def _classic(ck, np):
    import dfs_evaluate as DE
    rng = np.random.default_rng(3)
    pos_list = ["QB"] * 4 + ["RB"] * 6 + ["WR"] * 8 + ["TE"] * 4 + ["DST"] * 4
    ents = []
    for i, pos in enumerate(pos_list):
        team = ["AAA", "BBB", "CCC", "DDD"][i % 4]
        ents.append({"name": f"{pos}{i}", "pos": pos, "team": team, "game": "AAA@BBB" if team in ("AAA", "BBB") else "CCC@DDD",
                     "salary": int(rng.integers(25, 55)) * 100, "proj": float(rng.uniform(5, 20)),
                     "_field_only": i == 25})
    by = {p: [i for i, e in enumerate(ents) if e["pos"] == p] for p in ("QB", "RB", "WR", "TE", "DST")}
    M = 1500
    field = np.stack([np.concatenate([rng.choice(by["QB"], 1, replace=False), rng.choice(by["RB"], 2, replace=False),
                                      rng.choice(by["WR"], 4, replace=False), rng.choice(by["TE"], 1, replace=False),
                                      rng.choice(by["DST"], 1, replace=False)]) for _ in range(M)])
    contest = {"id": 1, "name": "Test GPP", "entry_fee": 20.0, "places_paid": 200, "max_entries": 1000,
               "payouts": [{"from": 1, "to": 1, "prize": 5000.0}, {"from": 2, "to": 5, "prize": 500.0},
                           {"from": 6, "to": 50, "prize": 80.0}, {"from": 51, "to": 200, "prize": 30.0}]}
    art = DE.save_artifact("classic", 999001, False, ents, field, None, [contest], 12, False, "engine", 3000,
                           {"AAA", "BBB", "CCC", "DDD"}, 1_795_000_000)
    path, meta = DE.load_artifact(999001)
    ck("EV1 the build's artifact is written atomically and read back: the board's players in its order with "
       "salaries and the field-only flag, the contest with its FULL payout table, the field's rows",
       art == "999001-1795000000" and meta["players"][25]["field_only"] and meta["contests"][0]["payouts"][0]["prize"] == 5000.0
       and np.array_equal(np.load(os.path.join(path, "field_idx.npy")), field)
       and not any(".tmp-" in n for n in os.listdir(os.path.dirname(path))))
    W = 2500
    X = np.round(rng.gamma(2.0, 5.0, size=(len(ents), W)), 2)
    pool = {e["name"]: {"pos": e["pos"], "team": e["team"], "proj": e["proj"], "arr": X[i].tolist()} for i, e in enumerate(ents)}
    DE.prepare(path, meta, pool=pool, log=lambda *_: None)
    lu = [by["QB"][0], by["RB"][0], by["RB"][1], by["WR"][0], by["WR"][1], by["WR"][2], by["TE"][0], by["WR"][3], by["DST"][0]]
    names = [ents[i]["name"] for i in lu]
    res = DE.evaluate(path, meta, [{"label": "mine", "names": names}])
    row = res["lineups"][0]
    C = DE._capacity(contest)
    named = {"top01": 0.001, "top1": 0.01, "cash": (200 - 1) / (C - 1), "top_0.2": 0.20}
    bf = _brute(np, X, field, np.full(M, 1.0 / M), lu, meta["contests"][0], named)
    c0 = row["contests"][0]
    ok = (abs(c0["top01_pct"] - 100 * bf["top01"]) < 1e-9 and abs(c0["top1_pct"] - 100 * bf["top1"]) < 5e-4
          and abs(c0["cash_pct"] - round(100 * bf["cash"], 2)) < 1e-9
          and abs(row["finish"]["top_0.2"] - round(100 * bf["top_0.2"], 3)) < 1e-9
          and abs(c0["ev_experimental"] - round(bf["ev"], 2)) < 1e-9)
    ck("EV2 the evaluator equals a brute force on every figure: top 0.1%, top 1%, cash, the finish ladder and the "
       "payout tier reached (so EV), the field's mass above counted lineup by lineup in every world",
       ok and row["legal"] and row["source"] == "custom",
       f"eval {c0} | brute {bf}")
    s = X[lu].sum(axis=0)
    ck("EV3 the score distribution is the lineup's own on the final worlds (mean and percentiles) and the "
       "correlation matrix is 9 x 9 with ones on the diagonal",
       abs(row["mean"] - round(float(s.mean()), 2)) < 0.011 and abs(row["p90"] - round(float(np.percentile(s, 90)), 2)) < 0.011
       and len(row["corr"]) == 9 and all(abs(row["corr"][i][i] - 1.0) < 1e-6 for i in range(9)))
    bad_cap = DE.check_lineup(meta, [ents[i]["name"] for i in [by["QB"][0], by["QB"][1]] + lu[2:]])
    one_game = [i for i in lu]
    ck("EV4 legality is DraftKings': a second QB with no FLEX room, a name not in the pool, a duplicate, an "
       "over-cap salary each named; a field-only player is legal but noted as outside Vigil's depth gate",
       any("QB" in p for p in bad_cap[1]) and DE.check_lineup(meta, names[:8] + ["Nobody"])[1]
       and DE.check_lineup(meta, names[:8] + [names[0]])[1]
       and DE.check_lineup(meta, [ents[i]["name"] for i in one_game[:8]] + [ents[25]["name"]])[2]
       and DE.check_lineup(dict(meta, players=[dict(p, salary=9000) for p in meta["players"]]), names)[1])
    board = {"kind": "classic", "results": {"1": {"top_top1": [{"lineup": [{"name": n} for n in names]}],
                                                  "top_money": [], "top_win": []}}}
    res2 = DE.evaluate(path, meta, [{"label": "mine", "names": names}], board=board)
    srcs = [r["source"] for r in res2["lineups"]]
    same = [r for r in res2["lineups"] if r["names"] == names]
    ck("EV5 a hand-built lineup is labelled custom and the board's own optimizer:<list>, both scored on the same "
       "final worlds (equal lineups, equal figures); a second request is counted as a second look and flagged "
       "as development data",
       srcs == ["custom", "optimizer:top_top1"] and same[0]["contests"] == same[1]["contests"]
       and res["looks"] == 1 and res2["looks"] == 2 and "development_note" in res2 and "development_note" not in res)
    for k in range(4):
        DE.save_artifact("classic", 999001, False, ents, field, None, [contest], 12, False, "engine", 3000,
                         {"AAA"}, 1_795_000_100 + k)
    left = sorted(os.listdir(os.path.dirname(path)))
    ck("EV6 retention keeps the newest three artifacts of a slate", len(left) == DE.KEEP_PER_SLATE
       and left[-1] == "999001-1795000103")


def _showdown(ck, np):
    import dfs_evaluate as DE
    rng = np.random.default_rng(9)
    ents = []
    for i in range(12):
        team = "AAA" if i < 6 else "BBB"
        pos = ["QB", "RB", "WR", "WR", "TE", "K"][i % 6]
        ents.append({"name": f"S{i}", "pos": pos, "team": team, "salary": int(rng.integers(20, 70)) * 100,
                     "cpt_salary": None, "proj": float(rng.uniform(3, 20))})
    L = 3000
    field = np.stack([rng.choice(12, 6, replace=False) for _ in range(L)])
    wts = rng.dirichlet(np.ones(L))
    contest = {"id": 2, "name": "SD", "entry_fee": 10.0, "places_paid": 100, "max_entries": 500,
               "payouts": [{"from": 1, "to": 1, "prize": 1000.0}, {"from": 2, "to": 100, "prize": 15.0}]}
    DE.save_artifact("showdown", 999002, False, ents, field, wts, [contest], 12, False, "engine", 2000,
                     {"AAA", "BBB"}, 1_795_000_000)
    path, meta = DE.load_artifact(999002)
    W = 2000
    X = np.round(rng.gamma(2.0, 4.0, size=(12, W)), 2)
    pool = {e["name"]: {"pos": e["pos"], "team": e["team"], "proj": e["proj"], "arr": X[i].tolist()} for i, e in enumerate(ents)}
    DE.prepare(path, meta, pool=pool, log=lambda *_: None)
    lu = [0, 1, 2, 6, 7, 8]
    names = [f"S{i}" for i in lu]
    row = DE.evaluate(path, meta, [{"label": "sd", "names": names, "captain": "S6"}])["lineups"][0]
    C = DE._capacity(contest)
    bf = _brute(np, X, field, wts, lu, meta["contests"][0], {"top1": 0.01, "cash": 99 / (C - 1)}, cpt=6, field_cpt=True)
    c0 = row["contests"][0]
    ck("EV7 showdown: the captain counts 1.5x in the lineup and in every field lineup (the universe's first "
       "column), the field weighted by its own weights -- the evaluator equals the brute force on top 1%, cash "
       "and EV",
       row["legal"] and abs(c0["top1_pct"] - round(100 * bf["top1"], 3)) < 1e-9
       and abs(c0["cash_pct"] - round(100 * bf["cash"], 2)) < 1e-9 and abs(c0["ev_experimental"] - round(bf["ev"], 2)) < 1e-9,
       f"eval {c0} | brute {bf}")
    one_team = DE.check_lineup(meta, [f"S{i}" for i in range(6)], "S0")
    ck("EV8 showdown legality: six players, a named captain among them, both teams, the captain's salary at 1.5x",
       any("both teams" in p for p in one_team[1]) and DE.check_lineup(meta, names, None)[1]
       and DE.check_lineup(meta, names, "S6")[3] == sum(meta["players"][i]["salary"] for i in lu)
       + meta["players"][6]["cpt_salary"] - meta["players"][6]["salary"])


def run_gpu(ck):
    """The evaluator's thresholds on the PC's GPU (2026-11-26): equal to the original one-world loop, verified on
    the first chunk, every failure back on the CPU. CuPy is not here, so numpy stands in for it -- the code path
    and its arithmetic are what is checked; the owner's RTX 4070 SUPER measured the same kernel equal to the CPU
    on every one of 75,000,000 buckets and 160,000 thresholds (research/gpu_bench.py)."""
    import inspect
    import types
    import numpy as np
    import dfs_evaluate as E
    import dfs_tourney as T
    rng = np.random.default_rng(2)
    P, W, M = 60, 700, 20000
    X = (np.rint(rng.gamma(2.0, 4.0, size=(P, W)) * 50.0) / 50.0).astype(np.float32)    # DraftKings' 0.02 lattice
    idx = np.stack([rng.choice(P, 9, replace=False) for _ in range(M)])
    w = rng.dirichlet(np.ones(M) * 0.5)                                                  # very uneven field weights
    WfT = np.ascontiguousarray(T.lineup_matrix(idx, P).T)
    qs = np.asarray([0.0, 0.001, 0.01, 0.1, 0.2], dtype=np.float64)
    chunk = 128
    ref = np.zeros((W, len(qs)), np.int32)
    for a in range(0, W, chunk):                                      # the pre-GPU loop, verbatim
        B = T._buckets(np.ascontiguousarray(X[:, a:a + chunk].T) @ WfT)
        for j in range(B.shape[0]):
            hist = np.bincount(B[j], weights=w, minlength=T._NB)
            above = np.cumsum(hist[::-1])[::-1] - hist
            ref[a + j] = np.searchsorted(-above, -(qs + 1e-12), side="left")
    fake = types.SimpleNamespace(**{k: getattr(np, k) for k in dir(np) if not k.startswith("__")})
    fake.asnumpy = np.asarray
    fake.get_default_memory_pool = lambda: types.SimpleNamespace(free_all_blocks=lambda: None)
    Tc, dc = E.field_thresholds(X, WfT, w, qs, chunk)
    Tg, dg = E.field_thresholds(X, WfT, w, qs, chunk, xp=fake)
    bad = types.SimpleNamespace(**vars(fake))
    bad.count_nonzero = lambda a, axis=None: np.count_nonzero(a, axis=axis) + 1
    Tb, db = E.field_thresholds(X, WfT, w, qs, chunk, xp=bad)
    boom = types.SimpleNamespace(**vars(fake))
    boom.tile = lambda *a: (_ for _ in ()).throw(MemoryError("out of VRAM"))
    Tx, dx = E.field_thresholds(X, WfT, w, qs, chunk, xp=boom)
    saved = os.environ.pop("VIGIL_GPU", None)
    off = E._gpu() is None
    if saved is not None:
        os.environ["VIGIL_GPU"] = saved
    ck("EV13 the evaluator's thresholds: the CPU path is the old one-world loop to the cell, and the GPU path "
       "(several worlds a bincount, a count per fraction) gives the same cells on DraftKings' lattice with "
       "uneven field weights", (Tc == ref).all() and dc == "CPU" and (Tg == ref).all() and dg.startswith("GPU"),
       f"{dc} {dg} cpu {int((Tc != ref).sum())} gpu {int((Tg != ref).sum())}")
    ck("EV14 the GPU never answers unchecked: a first chunk that differs from the CPU in one cell, or any error "
       "(out of memory), sends the whole job to the CPU (ledgered EVAL-gpu-mismatch / EVAL-gpu) with the right "
       "answer; with no switch the GPU is never tried",
       db == "CPU" and (Tb == ref).all() and dx == "CPU" and (Tx == ref).all() and off, f"{db} {dx} {off}")
    pw = open(os.path.join(_ROOT, "pc_worker.py")).read()
    ex = open(os.path.join(_ROOT, "vigil-pc.cfg.example")).read()
    ck("EV15 the switch is the owner's: \"gpu\": true in vigil-pc.cfg sets VIGIL_GPU=1 in the evaluator's lane only "
       "(the example ships it false), and the evaluator's log names the device that answered",
       'if _cfg_flag("gpu"):' in pw and 'os.environ["VIGIL_GPU"] = "1"' in pw and '"gpu": false' in ex
       and inspect.getsource(E.prepare).count("field_thresholds(X, WfT, w, qs, chunk)") == 1
       and "on the {device}" in inspect.getsource(E.prepare))


def run_wiring(ck):
    """The request path and the tab (no numpy needed): the routes' shapes, the
    PC's doors, the PC lane, the page's script."""
    import inspect
    js = open(os.path.join(_ROOT, "static", "dfseval.js")).read()
    app_js = open(os.path.join(_ROOT, "static", "app.js")).read()
    html = open(os.path.join(_ROOT, "templates", "index.html")).read()
    inline = [h for h in ("onclick=", "onchange=", "oninput=") if h in js]
    ck("EV9 the evaluator's script has no inline handlers, escapes every server string it draws, never polls a "
       "hidden screen, and is loaded by the page and called under every tournament board",
       not inline and js.count("_dxeEsc(") >= 15 and "if (document.hidden)" in js
       and '<script src="/static/dfseval.js?v={{ v }}"></script>' in html
       and 'if (typeof dfsEvalRender === "function") dfsEvalRender(d);' in app_js, str(inline))
    os.environ.setdefault("VIGIL_NO_BG", "1")
    import app as A
    c = A.app.test_client()
    bad = [c.post("/api/dfs/eval", json=b).status_code for b in (
        {"dg": "x", "lineups": [{"names": ["A"]}]}, {"dg": 1, "lineups": []},
        {"dg": 1, "lineups": [{"names": ["A"] * 10}]}, {"dg": 1, "lineups": [{"names": ["A", "B"], "captain": "C"}]})]
    doors = [c.get("/api/dfs/eval/requests").status_code, c.post("/api/dfs/eval/result", json={"id": "x"}).status_code]
    src = inspect.getsource(A._dfseval_clean)
    ck("EV10 the routes refuse a bad draft group, no lineups, more than nine names, a captain outside the lineup; "
       "the PC's two doors want its token; anything the tab sends is labelled custom (the tab cannot pass a lineup "
       "off as the optimizer's)", bad == [400, 400, 400, 400] and doors == [403, 403] and '"source": "custom"' in src,
       f"{bad} {doors}")
    pw = open(os.path.join(_ROOT, "pc_worker.py")).read()
    pl = open(os.path.join(_ROOT, "pc_loop.py")).read()
    ck("EV11 the PC answers on a lane of its own (pc_loop keeps one `pc_worker.py --eval` poller, stopped by an "
       "update like the boards pass), at low priority, and only scores the served board's lineups when that board "
       "is the build the artifact came from",
       '"--eval"' in pl and "_stop_eval()" in pl and "threading.Thread(target=_eval_loop, daemon=True).start()" in pl
       and "os.nice(10)" in pw and 'board.get("eval_artifact") not in (None, meta["id"])' in pw)
    import dfs_tourney as T
    ck("EV12 both tournament builds leave the artifact and stamp the board with its id (and the server, without "
       "numpy or a cache directory, writes nothing)",
       inspect.getsource(T.build_nfl_classic).count("dfs_evaluate.save_artifact(") == 1
       and inspect.getsource(T.build_nfl_showdown).count("dfs_evaluate.save_artifact(") == 1
       and '"eval_artifact": _eval_art' in inspect.getsource(T.build_nfl_classic)
       and '"eval_artifact": _eval_art' in inspect.getsource(T.build_nfl_showdown))


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    run_gpu(ck)
    run_wiring(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
