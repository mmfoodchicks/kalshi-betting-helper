"""Guards for the deep engine's market-anchor fix, built OFF (2026-11-27; nfl_deep_data.ANCHOR_V2,
research/reports/deep_anchor_fix.md).

    python3 tests/guards_auto_deep_anchor.py      # exits 1 on any failed check

The owner: "Fix the deep-engine market anchor immediately after the registered 100-game read, preserving its
original result and model version." What these pin:
- the switch is off and production converts at 2.25 exactly as before;
- the switch cannot be committed on before the deep-weight read's own result file is;
- the shipped constant is the measured one and meets its stated tolerance;
- a flip versions everything a world or payload could outlive it in;
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
    ladder = {ab: 8.5 + (i % 9 - 4) * 0.7 for i, ab in enumerate(sorted(nfl_season._DIV_OF))}
    orig_mw = nfl_season._market_wins
    try:
        nfl_season._market_wins = lambda: dict(ladder)
        mm, src = D.market_margins(2026, {})
    finally:
        nfl_season._market_wins = orig_mw
    old = {ab: (w - 8.5) * 2.25 for ab, w in nfl_season.market_pace(ladder, {}).items()}
    ck("DA1 the switch is OFF and production is unchanged: ANCHOR_V2 False, version 1, the deep anchor converts "
       "Kalshi's pace at nfl_season._PTS_PER_WIN (2.25) -- market_margins returns the old formula's margins "
       "exactly",
       D.ANCHOR_V2 is False and D.ANCHOR_VERSION == 1 and D.market_pts_per_win() == nfl_season._PTS_PER_WIN == 2.25
       and src == "kalshi_win_totals_rest_of_season" and mm == old, f"{D.ANCHOR_V2} {D.market_pts_per_win()}")

    gated = (not D.ANCHOR_V2) or (_tracked(READ_RESULT)
                                  and bool(json.load(open(os.path.join(_ROOT, READ_RESULT))).get("triggered")))
    ck("DA2 the switch cannot flip before the registered deep-weight read is committed: ANCHOR_V2 may be True only "
       "beside research/data/nfl_deep_weight_read.json, tracked in git and triggered (the read writes it only after "
       "its 100th game), so the read grades the engine as it stood",
       gated, f"ANCHOR_V2 {D.ANCHOR_V2}, read result tracked {_tracked(READ_RESULT)}")

    from research import deep_anchor_scale as S
    m = json.load(open(S.OUT))
    data = json.load(open(S.SEASONS_PATH))["seasons"]
    # the analytic reference's definition, re-evaluated at its stored K (one pass, not the bisection)
    from research import c3_margin_scale as MS
    k_an = m["analytic_reference"]["k_slope1"]
    sl, ma = [], []
    for s in S.SEASONS:
        sd, games, G = S.season_setup(data[str(s)])
        mm = S.margins(sd, G, k_an)
        ew = S.expected_wins(sd, games, [MS._phi((mm[h] - mm[a] + 2.2) / 13.2) for h, a in games])
        sl.append(S.slope(sd, ew))
        ma.append(sum(abs(ew[t] - sd[t]) for t in sd) / len(sd))
    mae_an = sum(ma) / len(ma)
    k_an = k_an if abs(sum(sl) / len(sl) - 1.0) < 1e-3 else float("nan")
    v = m["verify"]
    ck("DA3 the shipped constant is the MEASURED one (research/deep_anchor_scale.py: 24 real seasons, each its own "
       "schedule, the deep engine anchored through production's apply_anchor): K* by interpolation, its bootstrap "
       "interval holding the shipped value, and the verification on fresh seeds within the stated tolerance -- "
       "slope 1 +/- 0.03 with SE <= 0.01, win error at most the exact analytic reference + 0.10 (recomputed here "
       "from the committed seasons)",
       D.MARKET_PTS_PER_WIN_V2 == m["k_ship"] and m["passes"] is True
       and m["k_star_95"][0] <= m["k_ship"] <= m["k_star_95"][1] + 0.005
       and abs(v["slope"] - 1.0) <= 0.03 and v["se"] <= 0.01 and v["mae_wins"] <= mae_an + 0.10
       and abs(k_an - m["analytic_reference"]["k_slope1"]) < 1e-3 and abs(mae_an - m["analytic_reference"]["mae_wins"]) < 1e-3
       and len(data) == 24 and m["replicates"].startswith("seasons 2002-2025"),
       f"k {D.MARKET_PTS_PER_WIN_V2} vs {m.get('k_ship')}; verify {v}; analytic {k_an:.4f} {mae_an:.4f}")

    # the flip's plumbing, exercised with the switch forced on for the call
    import deep_cache
    import nfl_deep_sim as E
    import nfl_game_sim as G
    T = E.tables()
    L = E.league_profile(T)

    def anchored(v2):
        profs = {ab: json.loads(json.dumps(dict(L, abbr=ab, g=0))) for ab in nfl_season._DIV_OF}
        sv = (D.ANCHOR_V2, D.ANCHOR_VERSION, nfl_season._market_wins)
        try:
            D.ANCHOR_V2, D.ANCHOR_VERSION = v2, (2 if v2 else 1)
            nfl_season._market_wins = lambda: dict(ladder)
            D.apply_anchor(profs, 2026, T, recs={})
        finally:
            D.ANCHOR_V2, D.ANCHOR_VERSION, nfl_season._market_wins = sv
        return profs
    p1, p2 = anchored(False), anchored(True)
    a1, a2 = p1["KC"]["anchor"], p2["KC"]["anchor"]
    h1 = pool_cache.inputs_hash(games=[], profiles=p1, drive={}, weight=0.3, dst={}, kickers={})
    h2 = pool_cache.inputs_hash(games=[], profiles=p2, drive={}, weight=0.3, dst={}, kickers={})
    # a nightly payload of the other version is refused by the slate / DFS loader
    sv = (deep_cache.load, D.build_profiles, D.roster_factors, dict(G._deep_prof_cache), D.ANCHOR_V2, D.ANCHOR_VERSION)
    built = []
    try:
        deep_cache.load = lambda key: ({"profiles": p1, "season": 2026}, 9e18) if key == "nfl_deep" else (None, None)
        D.build_profiles = lambda season, **kw: (built.append(1) or p2, {})
        D.roster_factors = lambda season, week: {}
        D.ANCHOR_V2, D.ANCHOR_VERSION = True, 2
        G._deep_prof_cache.update(key=None, profiles=None)
        got_v2 = G._deep_profiles(2026, 12)
        D.ANCHOR_V2, D.ANCHOR_VERSION = False, 1
        G._deep_prof_cache.update(key=None, profiles=None)
        got_v1 = G._deep_profiles(2026, 12)
    finally:
        deep_cache.load, D.build_profiles, D.roster_factors = sv[0], sv[1], sv[2]
        G._deep_prof_cache.clear()
        G._deep_prof_cache.update(sv[3])
        D.ANCHOR_V2, D.ANCHOR_VERSION = sv[4], sv[5]
    ck("DA4 a flip versions everything that could outlive it: every anchored profile stamps its anchor version and "
       "points per win (so the pool cache's inputs hash changes and no version-1 world is reused); "
       "nfl_deep_data.py is in the pool cache's code files; the deep season payload stamps the version; the "
       "slate / DFS loader refuses a nightly payload of another version and rebuilds; v2 levels are flatter "
       "than v1's",
       a1["version"] == 1 and a1["pts_per_win"] == 2.25 and a2["version"] == 2
       and a2["pts_per_win"] == D.MARKET_PTS_PER_WIN_V2 and h1 != h2 and "nfl_deep_data.py" in pool_cache._CODE_FILES
       and "anchor_version" in inspect.getsource(__import__("nfl_deep_season").run_deep)
       and D.anchor_version_of(p1) == 1 and D.anchor_version_of(p2) == 2
       and got_v2 is p2 and built == [1] and got_v1 is p1
       and abs(p2["KC"]["level"] - 1) <= abs(p1["KC"]["level"] - 1),
       f"a1 {a1} a2 {a2} built {built}")

    dfs_pin = json.load(open(os.path.join(_ROOT, "research", "data", "nfl_coach", "prereg_dfs.json")))["files"]
    r1_pin = json.load(open(os.path.join(_ROOT, "research", "data", "nfl_coach", "prereg_r1.json"))).get("files") or {}
    ev = open(os.path.join(_ROOT, "research", "nfl_dfs_engine_eval.py")).read()
    va = open(os.path.join(_ROOT, "research", "nfl_deep_validate.py")).read()
    ck("DA5 no pinned file was touched and the PD-anchored research harnesses cannot see the switch: "
       "nfl_dfs_engine.py and nfl_deep_sim.py match the coaching DFS registration's sha256 (and nfl_deep_sim.py "
       "R1's), and nfl_dfs_engine_eval / nfl_deep_validate convert at nfl_season._PTS_PER_WIN with market_margins "
       "replaced, never at market_pts_per_win",
       _sha("nfl_dfs_engine.py") == dfs_pin["nfl_dfs_engine.py"] and _sha("nfl_deep_sim.py") == dfs_pin["nfl_deep_sim.py"]
       and (not r1_pin.get("nfl_deep_sim.py") or _sha("nfl_deep_sim.py") == r1_pin["nfl_deep_sim.py"])
       and "nfl_season._PTS_PER_WIN" in ev and "D.market_margins = lambda" in ev and "market_pts_per_win" not in ev
       and "nfl_season._PTS_PER_WIN" in va and "market_pts_per_win" not in va,
       "a pinned file or a research harness changed")

    ds_path = os.path.join(_ROOT, "research", "data", "deep_anchor_downstream.json")
    ds = json.load(open(ds_path)) if os.path.exists(ds_path) else {}
    wk = ds.get("weeks") or {}
    bd = ds.get("board") or {}
    ck("DA6 the downstream evaluation was run on the shipped constant, both weeks (current and past), both "
       "anchors on shared seeds, with game, season and DFS figures, and the classic board both ways beside its "
       "own seed-to-seed yardstick (same lists)",
       ds.get("k_v2") == D.MARKET_PTS_PER_WIN_V2 and ds.get("k_v1") == 2.25 and set(wk) == {"current", "past"}
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
