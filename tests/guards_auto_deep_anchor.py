"""Guards for the deep engine's market-anchor fix, STAGED as a patch (2026-11-27; research/deep_anchor_patch.py,
research/data/deep_anchor_flip.patch, research/reports/deep_anchor_fix.md).

    python3 tests/guards_auto_deep_anchor.py      # exits 1 on any failed check

The owner: "Fix the deep-engine market anchor immediately after the registered 100-game read, preserving its
original result and model version." nfl_deep_data.py is frozen until that read (the coaching guards pin it
byte for byte: "the deep weight's read sits on" it), so the fix is a patch the flip applies.

What these pin:
- production is the read's file and converts at 2.25;
- the patch cannot land before the read's own result file is committed;
- the patched file is the measured fix and meets its stated tolerance;
- the flip versions everything a world or payload could outlive it in;
- no file a registered read pins was touched.
"""
import hashlib
import json
import os
import subprocess
import sys

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)

READ_RESULT = os.path.join("research", "data", "nfl_deep_weight_read.json")


def _sha(rel):
    return hashlib.sha256(open(os.path.join(_ROOT, rel), "rb").read()).hexdigest()


def _tracked(rel):
    r = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=_ROOT, capture_output=True, text=True)
    return r.returncode == 0


def run(ck):
    import inspect
    import nfl_deep_data as D
    import nfl_season
    import pool_cache
    from research import deep_anchor_patch as Pt
    ladder = {ab: 8.5 + (i % 9 - 4) * 0.7 for i, ab in enumerate(sorted(nfl_season._DIV_OF))}
    here = _sha("nfl_deep_data.py")
    flipped = here == Pt.DEEP_DATA_FLIPPED_SHA
    orig_mw = nfl_season._market_wins
    try:
        nfl_season._market_wins = lambda: dict(ladder)
        mm, src = D.market_margins(2026, {})
    finally:
        nfl_season._market_wins = orig_mw
    old = {ab: (w - 8.5) * 2.25 for ab, w in nfl_season.market_pace(ladder, {}).items()}
    ck("DA1 production is the file the deep-weight read grades (nfl_deep_data.py at its pinned sha256) and converts "
       "Kalshi's pace at nfl_season._PTS_PER_WIN (2.25) -- or, after the flip, exactly the patched file",
       (here == Pt.DEEP_DATA_PINNED_SHA and src == "kalshi_win_totals_rest_of_season" and mm == old
        and not hasattr(D, "ANCHOR_V2")) or flipped, here[:16])

    gated = (not flipped) or (_tracked(READ_RESULT)
                              and bool(json.load(open(os.path.join(_ROOT, READ_RESULT))).get("triggered")))
    ok_apply, why = Pt.check()
    ck("DA2 the flip cannot land before the registered deep-weight read is committed: the patched nfl_deep_data.py "
       "may stand only beside research/data/nfl_deep_weight_read.json, tracked and triggered (the read writes it "
       "only after its 100th game); until then the patch still applies cleanly to the frozen file and yields "
       "exactly the pinned flipped bytes",
       gated and (flipped or (ok_apply and Pt.flipped_sha() == Pt.DEEP_DATA_FLIPPED_SHA)),
       f"flipped {flipped} read tracked {_tracked(READ_RESULT)} apply {ok_apply} {why}")

    M2, M1 = Pt.load(True), Pt.load(False)
    from research import deep_anchor_scale as S
    m = json.load(open(S.OUT))
    data = json.load(open(S.SEASONS_PATH))["seasons"]
    # the analytic reference's definition, re-evaluated at its stored K (one pass, not the bisection)
    from research import c3_margin_scale as MS
    k_an = m["analytic_reference"]["k_slope1"]
    sl, ma = [], []
    for s in S.SEASONS:
        sd, games, G = S.season_setup(data[str(s)])
        mmg = S.margins(sd, G, k_an)
        ew = S.expected_wins(sd, games, [MS._phi((mmg[h] - mmg[a] + 2.2) / 13.2) for h, a in games])
        sl.append(S.slope(sd, ew))
        ma.append(sum(abs(ew[t] - sd[t]) for t in sd) / len(sd))
    mae_an = sum(ma) / len(ma)
    k_ok = abs(sum(sl) / len(sl) - 1.0) < 1e-3
    v = m["verify"]
    ck("DA3 the patch ships the MEASURED constant (research/deep_anchor_scale.py: 24 real seasons, each its own "
       "schedule, the deep engine anchored through production's apply_anchor): K* by interpolation, its bootstrap "
       "interval holding the shipped value, and the verification on fresh seeds within the stated tolerance -- "
       "slope 1 +/- 0.03 with SE <= 0.01, win error at most the exact analytic reference + 0.10 (re-evaluated "
       "here from the committed seasons)",
       M2.ANCHOR_V2 is True and M2.MARKET_PTS_PER_WIN_V2 == m["k_ship"] and m["passes"] is True
       and m["k_star_95"][0] <= m["k_ship"] <= m["k_star_95"][1] + 0.005
       and abs(v["slope"] - 1.0) <= 0.03 and v["se"] <= 0.01 and v["mae_wins"] <= mae_an + 0.10
       and k_ok and abs(mae_an - m["analytic_reference"]["mae_wins"]) < 1e-3
       and len(data) == 24 and m["replicates"].startswith("seasons 2002-2025"),
       f"k {M2.MARKET_PTS_PER_WIN_V2} vs {m.get('k_ship')}; verify {v}; analytic {mae_an:.4f} k_ok {k_ok}")

    # the flip's plumbing: the patched module both ways, swapped in for the production loader's import
    import deep_cache
    import nfl_deep_sim as E
    import nfl_game_sim as G
    T = E.tables()
    L = E.league_profile(T)

    def anchored(mod):
        profs = {ab: json.loads(json.dumps(dict(L, abbr=ab, g=0))) for ab in nfl_season._DIV_OF}
        sv = nfl_season._market_wins
        try:
            nfl_season._market_wins = lambda: dict(ladder)
            mod.apply_anchor(profs, 2026, T, recs={})
        finally:
            nfl_season._market_wins = sv
        return profs
    p1, p2 = anchored(M1), anchored(M2)
    a1, a2 = p1["KC"]["anchor"], p2["KC"]["anchor"]
    h1 = pool_cache.inputs_hash(games=[], profiles=p1, drive={}, weight=0.3, dst={}, kickers={})
    h2 = pool_cache.inputs_hash(games=[], profiles=p2, drive={}, weight=0.3, dst={}, kickers={})
    # a nightly payload of the other version is refused by the slate / DFS loader once the patch is in
    unstamped = anchored(M1)                           # a nightly payload from before the patch: no version stamp
    for _p in unstamped.values():
        for _k in ("version", "pts_per_win"):
            (_p.get("anchor") or {}).pop(_k, None)
    sv = (deep_cache.load, dict(G._deep_prof_cache), sys.modules["nfl_deep_data"])
    built = []
    try:
        for mod in (M1, M2):
            mod.roster_factors = lambda season, week: {}
            mod.build_profiles = (lambda b: (lambda season, **kw: (built.append(b) or (p2 if b == 2 else p1), {})))(
                mod.ANCHOR_VERSION)
        deep_cache.load = lambda key: ({"profiles": unstamped, "season": 2026}, 9e18) if key == "nfl_deep" else (None, None)
        sys.modules["nfl_deep_data"] = M2
        G._deep_prof_cache.update(key=None, profiles=None)
        got_v2 = G._deep_profiles(2026, 12)
        sys.modules["nfl_deep_data"] = M1
        G._deep_prof_cache.update(key=None, profiles=None)
        got_v1 = G._deep_profiles(2026, 12)
    finally:
        deep_cache.load = sv[0]
        G._deep_prof_cache.clear()
        G._deep_prof_cache.update(sv[1])
        sys.modules["nfl_deep_data"] = sv[2]
    ck("DA4 the flip versions everything that could outlive it: the patched file stamps every anchored profile's "
       "version and points per win (so the pool cache's inputs hash changes and no version-1 world is reused); "
       "nfl_deep_data.py is in the pool cache's code files; the deep season payload stamps the version; the "
       "slate / DFS loader serves today's unstamped nightly payload while version 1 holds and refuses it once "
       "version 2 is in; v2 levels are flatter than v1's",
       a1["version"] == 1 and a1["pts_per_win"] == 2.25 and a2["version"] == 2 and a2["pts_per_win"] == 1.86
       and h1 != h2 and "nfl_deep_data.py" in pool_cache._CODE_FILES
       and "anchor_version" in inspect.getsource(__import__("nfl_deep_season").run_deep)
       and G.anchor_version(unstamped) == 1 and G.anchor_version(p2) == 2 and G.anchor_version({}) is None
       and got_v2 is p2 and built == [2] and got_v1 is unstamped
       and abs(p2["KC"]["level"] - 1) <= abs(p1["KC"]["level"] - 1),
       f"a1 {a1} a2 {a2} built {built}")

    dfs_pin = json.load(open(os.path.join(_ROOT, "research", "data", "nfl_coach", "prereg_dfs.json")))["files"]
    r1_pin = json.load(open(os.path.join(_ROOT, "research", "data", "nfl_coach", "prereg_r1.json"))).get("files") or {}
    # every coaching registration that pins an engine file (R1-R6, C4, the DFS read): none may have moved.
    # 2026-11-28, option (c) (research/registered_pins.py): checked at each registration commit, where its read
    # runs; live, the engine files are the read's bytes or -- behind the deep-weight read's gate -- exactly a
    # staged flip's (this anchor's, the fumble-return TD's).
    from research import registered_pins as REGP
    moved = {f: m for f in REGP.REGISTRATIONS for m in [REGP.moved(f)] if m}
    pins_ok = not moved
    ev = open(os.path.join(_ROOT, "research", "nfl_dfs_engine_eval.py")).read()
    va = open(os.path.join(_ROOT, "research", "nfl_deep_validate.py")).read()
    ck("DA5 no pinned file was touched and the PD-anchored research harnesses cannot see the switch: "
       "nfl_dfs_engine.py and nfl_deep_sim.py match the coaching DFS registration's sha256 (and nfl_deep_sim.py "
       "R1's) -- at the registration commits, and live unless a staged flip landed behind the read's gate "
       "(option (c)) -- and nfl_dfs_engine_eval / nfl_deep_validate convert at nfl_season._PTS_PER_WIN with "
       "market_margins replaced, never at market_pts_per_win",
       pins_ok and REGP.live_ok("nfl_dfs_engine.py", dfs_pin["nfl_dfs_engine.py"])
       and REGP.live_ok("nfl_deep_sim.py", dfs_pin["nfl_deep_sim.py"])
       and (not r1_pin.get("nfl_deep_sim.py") or REGP.live_ok("nfl_deep_sim.py", r1_pin["nfl_deep_sim.py"]))
       and "nfl_season._PTS_PER_WIN" in ev and "D.market_margins = lambda" in ev and "market_pts_per_win" not in ev
       and "nfl_season._PTS_PER_WIN" in va and "market_pts_per_win" not in va,
       f"a pinned file or a research harness changed {moved}")

    # The flip against the worlds cache and the frozen inputs of a resumable build (build_ckpt, 2026-11-27):
    # the pool cache's code signature over a tree holding the PATCHED nfl_deep_data.py must differ from
    # today's. A pinned build recomputes its worlds key from its frozen inputs under the current code and
    # raises PinMiss (a fresh build) when it differs, so no resumed build carries version-1 profiles past the
    # flip.
    import shutil
    import tempfile
    import nfl_dfs_sim
    # 2026-11-28: both sides are built, so the check reads the same before and after the flip -- "before" is the
    # live code with the READ's nfl_deep_data.py (from git, R1's registration commit, sha-checked), "after" the
    # live code with the patched one. (Comparing against the live tree went blind once the flip had landed.)
    from research import registered_pins as REGP
    old = subprocess.run(["git", "show", REGP.REGISTRATIONS["research/data/nfl_coach/prereg_r1.json"][0]
                          + ":nfl_deep_data.py"], cwd=_ROOT, capture_output=True).stdout
    assert hashlib.sha256(old).hexdigest() == Pt.DEEP_DATA_PINNED_SHA
    call = {"season": "2026", "week": 13, "n": 1000}
    sv_here, sv_sig = pool_cache._HERE, dict(pool_cache._code_sig)
    sigs = []
    try:
        for text in (old, Pt.patched_source().encode()):
            tmp = tempfile.mkdtemp(prefix="deep_anchor_sig_")
            try:
                for rel in pool_cache._CODE_FILES:
                    dst = os.path.join(tmp, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy(os.path.join(_ROOT, rel), dst)
                with open(os.path.join(tmp, "nfl_deep_data.py"), "wb") as fh:
                    fh.write(text)
                pool_cache._HERE = tmp
                pool_cache._code_sig.clear()
                sigs.append((pool_cache.code_sig(), pool_cache.key(call, "x")))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    finally:
        pool_cache._HERE = sv_here
        pool_cache._code_sig.clear()
        pool_cache._code_sig.update(sv_sig)
    (before, k_before), (after, k_after) = sigs
    src = inspect.getsource(nfl_dfs_sim.player_pool)
    ck("DA7 the flip invalidates the worlds cache AND a resumable build's frozen inputs: with the patched "
       "nfl_deep_data.py in the tree the pool cache's code signature, hence every worlds key, changes; a pinned "
       "build recomputes its key from its frozen inputs under the current code and raises PinMiss (restarts "
       "fresh) when it differs",
       before != after and k_before != k_after
       and 'if pinned and pc_key != pin["worlds_key"]' in src and "raise PinMiss" in src
       and "pool_cache.key(pc_call, pool_cache.inputs_hash(" in src,
       f"{before[:12]} {after[:12]}")

    ds_path = os.path.join(_ROOT, "research", "data", "deep_anchor_downstream.json")
    ds = json.load(open(ds_path)) if os.path.exists(ds_path) else {}
    wk = ds.get("weeks") or {}
    bd = ds.get("board") or {}
    ck("DA6 the downstream evaluation was run on the shipped constant, both weeks (current and past), both "
       "anchors on shared seeds, with game, season and DFS figures, and the classic board both ways beside its "
       "own seed-to-seed yardstick (same lists)",
       ds.get("k_v2") == M2.MARKET_PTS_PER_WIN_V2 and ds.get("k_v1") == 2.25 and set(wk) == {"current", "past"}
       and all(w["v1"]["anchor_version"] == 1 and w["v2"]["anchor_version"] == 2 and "season" in w["v1"]
               and w.get("dfs_v1", {}).get("mean_by_pos") for w in wk.values())
       and bd.get("lists") and set(bd.get("noise_v1_seed_vs_seed") or {}) == set(bd["lists"]),
       f"{ds.get('k_v2')} {list(wk)} board {sorted(bd)}")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
