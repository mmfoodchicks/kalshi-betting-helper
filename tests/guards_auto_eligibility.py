"""Guards for the DFS eligibility modes (2026-11-27; dfs_eligibility.py, research/reports/dfs_eligibility.md):
conservative is today's board byte for byte, the hard exclusions hold in every mode, eligibility never moves a
projection or the field, the fitted model is what its rule defines, and the tab escapes what it prints.

    python3 tests/guards_auto_eligibility.py      # exits 1 on any failed check
"""
import copy
import inspect
import json
import os
import subprocess
import sys

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "research"))


def _hist():
    """A five-week box history (nfl_usage.compact's shape): team AAA's WR "W4" played weeks 1-2 with a real
    share, missed 3-5 (returning); "W1".."W3" played every week; "Gone" played week 5 and is now out."""
    hist = {}
    for w in range(1, 6):
        pl = {"1": ["AAA", 8, 0], "2": ["AAA", 6, 0], "3": ["AAA", 4, 0], "9": ["AAA", 0, 15]}
        if w <= 2:
            pl["4"] = ["AAA", 7, 0]
        if w == 5:
            pl["5"] = ["AAA", 3, 2]
        tot = {"AAA": [sum(v[1] for v in pl.values()), sum(v[2] for v in pl.values())], "BBB": [30, 25]}
        hist[w] = {"pl": pl, "tot": tot}
    return hist


def _ctx():
    import nfl_adp
    ids = {nfl_adp._norm(n): (pid, "AAA") for n, pid in (("Wide One", "1"), ("Wide Two", "2"), ("Wide Three", "3"),
                                                          ("Wide Four", "4"), ("Gone Guy", "5"), ("Back One", "9"))}
    return {"ids": ids, "hist": _hist()}


def _gated():
    return [{"name": "Wide Four", "pos": "WR", "team": "AAA", "proj": 6.0, "why": "WR4 by depth chart (keep WR1-3)"},
            {"name": "Out Man", "pos": "WR", "team": "AAA", "proj": 9.0, "why": "listed Out"},
            {"name": "Gone Guy", "pos": "WR", "team": "AAA", "proj": 5.0, "why": "listed IR"},
            {"name": "Inact", "pos": "RB", "team": "AAA", "proj": 7.0, "why": "roster status Inactive"},
            {"name": "Nobody", "pos": "TE", "team": "AAA", "proj": 4.0, "why": "not on any roster Sleeper knows"},
            {"name": "Squad", "pos": "WR", "team": "AAA", "proj": 4.0,
             "why": "not on the depth chart (practice squad / inactive)"},
            {"name": "Noproj", "pos": "RB", "team": "AAA", "why": "no projection this week"}]


def _kept():
    return [{"name": n, "pos": "WR", "team": "AAA", "proj": p} for n, p in
            (("Wide One", 14.0), ("Wide Two", 10.0), ("Wide Three", 6.5))] + \
        [{"name": "Back One", "pos": "RB", "team": "AAA", "proj": 15.0}]


def _sd_pool():
    """A small showdown pool: the gate's players, two rank-gated field extras (one a $1,800 punt) and one extra
    the field plays because no roster knows him (a roster gate, never liftable)."""
    import numpy as np
    rng = np.random.default_rng(7)
    rows = [("QA", "QB", "AAA", 10000, "QB1"), ("RA", "RB", "AAA", 8000, "RB1"), ("WA", "WR", "AAA", 7000, "WR1"),
            ("TA", "TE", "AAA", 4000, "TE1"), ("KA", "K", "AAA", 4200, "K"), ("DA", "DST", "AAA", 3600, "DST"),
            ("QB_", "QB", "BBB", 9400, "QB1"), ("RB_", "RB", "BBB", 7600, "RB1"), ("WB", "WR", "BBB", 6400, "WR1"),
            ("TB", "TE", "BBB", 3000, "TE1"), ("KB", "K", "BBB", 4000, "K"), ("DB", "DST", "BBB", 3400, "DST")]
    ents = [{"name": n, "pos": p, "team": t, "salary": s, "cpt_salary": int(s * 1.5), "depth": d,
             "proj": float(rng.uniform(3, 18)), "arr": rng.gamma(2.0, 4.0, 200).astype(np.float32)}
            for n, p, t, s, d in rows]
    extra = [{"name": "Wide Four", "pos": "WR", "team": "AAA", "salary": 1800, "cpt_salary": 2700, "proj": 6.0,
              "arr": rng.gamma(1.5, 3.0, 200).astype(np.float32)},
             {"name": "Gated Five", "pos": "WR", "team": "BBB", "salary": 2600, "cpt_salary": 3900, "proj": 3.1,
              "arr": rng.gamma(1.2, 2.5, 200).astype(np.float32)},
             {"name": "Nobody", "pos": "TE", "team": "AAA", "salary": 2200, "cpt_salary": 3300, "proj": 4.0,
              "arr": rng.gamma(1.3, 2.0, 200).astype(np.float32)}]
    dx = [{"name": "Wide Four", "pos": "WR", "team": "AAA", "why": "WR4 by depth chart (keep WR1-3)"},
          {"name": "Gated Five", "pos": "WR", "team": "BBB", "why": "WR5 by depth chart (keep WR1-3)"},
          {"name": "Nobody", "pos": "TE", "team": "AAA", "why": "not on any roster Sleeper knows"}]
    by_name = {e["name"]: e for e in extra}
    return ents, by_name, dx


def run(ck):
    import numpy as np
    import dfs_eligibility as E
    import dfs_tourney as T
    import nfl_dfs
    # ---- EL1: the default is today's gate, and nothing is read or touched under it ----
    sig_sd = inspect.signature(T.build_nfl_showdown).parameters["eligibility"].default
    sig_cl = inspect.signature(T.build_nfl_classic).parameters["eligibility"].default
    kept, extra = copy.deepcopy(_kept()), [{"name": "Wide Four", "pos": "WR", "team": "AAA", "proj": 6.0,
                                            "_field_only": True}]
    before = copy.deepcopy((kept, extra))
    saved = E.week_context
    E.week_context = lambda *a, **k: (_ for _ in ()).throw(AssertionError("read under conservative"))
    try:
        got = [T._apply_eligibility(m, kept, extra, [], 12, False) for m in (None, "conservative", "CONSERVATIVE")]
    finally:
        E.week_context = saved
    callers = []
    import pc_worker as _pw
    for fn in ("pc_worker.py", "app.py", "pc_loop.py"):
        src = open(os.path.join(_ROOT, fn)).read()
        if fn == "pc_worker.py":
            # the owner's selector (approved 2026-11-27) reaches the builders only on the picked path
            src = src.replace(inspect.getsource(_pw._picked_builds), "")
        if "eligibility=" in src:
            callers.append(fn)
    ck("EL1 conservative is the default everywhere and touches nothing: the builders default to None, None and "
       "'conservative' return no stamp without reading the week, every row is unchanged and carries no eligibility "
       "key, and no scheduled build passes a mode -- only the owner's picked Build does (the board is today's byte for "
       "byte)",
       sig_sd is None and sig_cl is None and E.DEFAULT == "conservative" and got == [None, None, None]
       and (kept, extra) == before and T._elig_row(kept[0]) == {} and not callers,
       f"{sig_sd} {sig_cl} {got} {callers}")
    try:
        E.mode_of("adaptiv")
        typo = False
    except ValueError:
        typo = True
    ck("EL1b a mistyped mode is refused, never read as some other mode", typo)
    # ---- EL2: hard exclusions hold in every mode ----
    model = E.load_model()
    out = {m: E.assess(_kept(), _gated(), m, _ctx(), model) for m in E.MODES}
    rank_gated = {"Wide Four"}
    ck("EL2 only a depth-chart RANK exclusion can be lifted: Out, IR, an inactive roster status, no roster, no "
       "depth-chart entry and no projection stay out in every mode (unrestricted included)",
       all(set(o["admitted"]) <= rank_gated for o in out.values()) and out["conservative"]["admitted"] == {}
       and set(out["unrestricted"]["admitted"]) == rank_gated,
       {m: sorted(o["admitted"]) for m, o in out.items()})
    ents, by_name, dx = _sd_pool()
    ex = T._field_extras(by_name, dx)
    saved = E.week_context
    E.week_context = lambda *a, **k: {"error": "offline (guard)"}
    try:
        ex_u = copy.deepcopy(ex)
        st_u = T._apply_eligibility("unrestricted", copy.deepcopy(ents), ex_u, dx, 12, False)
    finally:
        E.week_context = saved
    nob = next(e for e in ex_u if e["name"] == "Nobody")
    ck("EL2b a field extra gated by a ROSTER lookup ('not on any roster Sleeper knows') stays field-only even in "
       "unrestricted; the rank-gated extras become enterable",
       nob.get("_field_only") is True and all(not e.get("_field_only") for e in ex_u if e["name"] != "Nobody")
       and st_u["admitted"] == ["Gated Five", "Wide Four"], st_u and st_u.get("admitted"))
    # ---- EL3: eligibility never moves a projection ----
    def proj_view(rows):
        return [(e["name"], e.get("proj"), e.get("ceiling"), e.get("floor"),
                 None if e.get("arr") is None else np.asarray(e["arr"]).tobytes()) for e in rows]
    ok3 = True
    for m in ("adaptive", "unrestricted"):
        ents, by_name, dx = _sd_pool()
        ex = T._field_extras(by_name, dx)
        b = proj_view(ents + ex)
        saved = E.week_context
        E.week_context = lambda *a, **k: _ctx()
        try:
            T._apply_eligibility(m, ents, ex, dx, 12, False)
        finally:
            E.week_context = saved
        ok3 &= proj_view(ents + ex) == b
    ck("EL3 no projection, ceiling, floor or world moves when a mode admits a player (the simulator's numbers decide)",
       ok3)
    # ---- EL4: the field and the universe are the conservative board's in every mode ----
    fields, allowed = {}, {}
    for m in E.MODES:
        ents, by_name, dx = _sd_pool()
        ex = T._field_extras(by_name, dx)
        saved = E.week_context
        E.week_context = lambda *a, **k: _ctx()
        try:
            T._apply_eligibility(m, ents, ex, dx, 12, False)
        finally:
            E.week_context = saved
        pool = ents + ex

        def entry_ok(cap_p, picked):                 # the board's own entry_ok, unpicked
            if cap_p.get("_field_only"):
                return False
            got = []
            for p in picked:
                if p.get("_field_only") or not nfl_dfs._sd_allowed(p, cap_p, got):
                    return False
                got.append(p)
            return True
        idx, W, al = T.enumerate_showdown(pool, nfl_dfs.CAP, cpt_mult=nfl_dfs.CPT_MULT, entry_ok=entry_ok)
        f, beta = T.field_weights(pool, idx, cpt_mult=nfl_dfs.CPT_MULT)
        fields[m] = (idx.tobytes(), f.tobytes(), beta)
        allowed[m] = al
    sub = all((~allowed["conservative"] | allowed["adaptive"])) and all((~allowed["adaptive"] | allowed["unrestricted"]))
    ck("EL4 every mode enumerates the same universe and weights the same field (identical bytes); what we may enter "
       "only widens: conservative within adaptive within unrestricted",
       fields["conservative"] == fields["adaptive"] == fields["unrestricted"] and sub
       and allowed["unrestricted"].sum() > allowed["conservative"].sum(),
       [int(a.sum()) for a in allowed.values()])
    src = inspect.getsource(T.build_nfl_showdown)
    ck("EL4b the showdown universe cap drops an admitted extra exactly as it drops a field-only one (`_elig_extra`), "
       "so the cap cannot build a different universe in a different mode",
       'e.get("_field_only") or e.get("_elig_extra")' in src and "_apply_eligibility(eligibility" in src
       and "_apply_eligibility(eligibility" in inspect.getsource(T.build_nfl_classic))
    p_role = {"name": "Wide Four", "pos": "WR", "team": "AAA", "salary": 1800, "depth": "WR4"}
    cap_p = {"name": "QA", "pos": "QB", "team": "AAA", "salary": 10000}
    ck("EL4c the punt-role rule: a WR4 punt is refused as before, and passes only when a mode marks his role "
       "(`_elig_role`)", (not nfl_dfs._sd_allowed(p_role, cap_p, []))
       and nfl_dfs._sd_allowed(dict(p_role, _elig_role=True), cap_p, []))
    # ---- EL5: the model is what its rule defines ----
    import dfs_eligibility_fit as F
    fr = F.frame(F.load())
    fit = [r for r in fr if r["season"] in F.FIT]
    spec = model["positions"]["WR"]
    lastk = [r["act"] for r in fit if r["pos"] == "WR" and r["rank"] == 3 and r["played"]]
    pk = [E.prob(spec["coef"], r["x"]) for r in fit if r["pos"] == "WR" and r["kept"]]
    ck("EL5 the WR model reads as its rule says: L is the median DraftKings score of the third-ranked receiver on the "
       "fit seasons (2022-2024) when he played, tau the 5th percentile of the fitted probability over the receivers "
       "the gate keeps -- recomputed here, not just the stored numbers",
       abs(float(np.median(lastk)) - spec["L"]) < 0.01 and abs(float(np.percentile(pk, 5.0)) - spec["tau"]) < 1e-5
       and model["fit_seasons"] == [2022, 2023, 2024] and model["tau_quantile"] == 5.0,
       f"{np.median(lastk)} {spec['L']} {np.percentile(pk, 5.0)} {spec['tau']}")
    row = E.usage_row("4", "AAA", _hist())
    ck("EL5b a returning player's share is his PRE-absence share over his last games played, and the team's games "
       "he missed are counted (byes are not games)",
       row["returning"] and row["missed"] == 3 and abs(row["share"] - 14 / 80) < 1e-9 and row["games"] == 2,
       row)
    # ---- EL6: the DEN @ PIT case as committed ----
    cmp_path = os.path.join(_ROOT, "research", "data", "dfs_eligibility", "compare_157076.json")
    c = json.load(open(cmp_path)) if os.path.exists(cmp_path) else {}
    chk = c.get("check") or {}
    pit = ((c.get("eligibility") or {}).get("adaptive") or {}).get("info", {}).get("Michael Pittman Jr.") or {}
    ck("EL6 the DEN @ PIT comparison rebuilt the production board exactly (790,222 legal lineups, 47,644 we may "
       "enter, field beta 0.3298) before comparing, and adaptive admits Pittman by his workload (p >= tau, returning)",
       chk.get("field_rows") == chk.get("board_legal") == 790222
       and chk.get("conservative_allowed") == chk.get("board_allowed") == 47644
       and "Michael Pittman Jr." in (c["eligibility"]["adaptive"]["admitted"]) and pit.get("returning")
       and pit.get("p", 0) >= pit.get("tau", 1), chk)
    # ---- EL8: the real-results audit as the report quotes it, by its definition ----
    hp = os.path.join(_ROOT, "research", "data", "dfs_eligibility", "history.json")
    h = json.load(open(hp)) if os.path.exists(hp) else {"showdown": {}, "classic": {}}

    def reach(kind, v):
        """slates whose best lineup variant v may enter scores what DraftKings' best over its whole pool scored"""
        return sum(1 for s in h[kind].values() if (s.get("best") or {}).get(v) and s["best"].get("dk_all")
                   and abs(s["best"][v]["points"] - s["best"]["dk_all"]["points"]) < 1e-6)
    sd = {v: reach("showdown", v) for v in ("conservative", "adaptive", "unrestricted", "unrestricted_all", "lift:all")}
    cl = {v: reach("classic", v) for v in ("conservative", "adaptive", "unrestricted")}
    enum_ok = all(abs((s["enumeration_check"]["search"] or 0) - (s["enumeration_check"]["enumeration"] or 0)) < 1e-6
                  for s in h["showdown"].values())
    rep = open(os.path.join(_ROOT, "research", "reports", "dfs_eligibility.md")).read()
    ck("EL8 the real-results audit reads as the report says, recomputed from history.json by its definition (the best "
       "lineup a variant may enter scored DraftKings' best over its whole pool, on the real points): showdowns 6 / 8 / "
       "10 / 13 of 20 (conservative / adaptive / unrestricted / + the sub-1.0 players), every house rule lifted 12; "
       "classic 6 / 9 / 12 of 12; the showdown search equals production's enumeration on every slate",
       len(h["showdown"]) == 20 and len(h["classic"]) == 12 and enum_ok
       and sd == {"conservative": 6, "adaptive": 8, "unrestricted": 10, "unrestricted_all": 13, "lift:all": 12}
       and cl == {"conservative": 6, "adaptive": 9, "unrestricted": 12}
       and "157076" not in h["showdown"] and "Conservative could build 6" in rep, f"{sd} {cl} {enum_ok}")
    # ---- EL7: the tab escapes what it prints and wires no handler ----
    js = open(os.path.join(_ROOT, "static", "app.js")).read()
    a, b = js.index("function dfsEligTag(p)"), js.index("function renderDfsTourney(d)")
    esc = js[js.index("function escapeHtml(x)"):]
    esc = esc[:esc.index("\n}\n") + 3]
    prog = esc + js[a:b] + """
const p = {elig: "admitted", elig_flags: ["returning: <img src=x onerror=alert(1)>"]};
const d = {eligibility: {mode: "adaptive<script>", admitted: ["A<b>"], outside_field: ["\\"q"], note: "<i>n</i>"}};
process.stdout.write(JSON.stringify([dfsEligTag(p), dfsEligNote(d), dfsEligNote({}), dfsEligTag({})]));
"""
    try:
        r = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=30)
        o = json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        o = [str(e)]
    seg = js[a:b]
    ck("EL7 the tab's eligibility marks escape every string (a name or flag carrying markup prints as text), print "
       "nothing for a conservative board, and wire no inline handler",
       len(o) == 4 and "<img" not in o[0] and "&lt;img" in o[0] and "<script>" not in o[1] and "A<b>" not in o[1]
       and "A&lt;b&gt;" in o[1] and o[2] == "" and o[3] == "" and "onclick" not in seg and "onerror" not in seg
       and js.count("${dfsEligTag(p)}") == 2 and "${eligHtml}" in js, o)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
