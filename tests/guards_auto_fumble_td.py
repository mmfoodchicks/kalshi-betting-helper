"""Guards for the fumble-return touchdown fix, STAGED as a patch, and for option (c) (2026-11-28;
research/fumble_td_fit.py, research/fumble_td_patch.py, research/data/fumble_td_flip.patch,
research/registered_pins.py, research/reports/fumble_td_fix.md, research/reports/registered_reads_runbook.md).

    python3 tests/guards_auto_fumble_td.py      # exits 1 on any failed check

What these pin:
- production is the read's engine (no fumble-return TD) or exactly the patched one, never anything between;
- the patch cannot land before the deep-weight read's result is committed (FT2, DA2's twin);
- the patched engine plays the FITTED probability, by its definition (kind x the snap's zone), at the engine's
  own fumble sites, scores it as the pick-six is, conserves every point, and with its switch off is the old
  engine draw for draw;
- the flip versions what it must (worlds cache, frozen inputs, the board's simulator stamp);
- every registration that pins an engine file holds AT ITS REGISTRATION COMMIT, the checks fail when that
  version is lost or altered, and the reads themselves refuse a patched tree (the safety net).
"""
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import types

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)

FIT = os.path.join(_ROOT, "research", "data", "fumble_td_fit.json")
RULE = "research/fumble_td_fit.py"
RULE_COMMIT = "461abe014522d1c82df1380cb93c0b4d39021f8d"      # the rule, committed before the held-out read


def _git(*a):
    return subprocess.run(["git", *a], cwd=_ROOT, capture_output=True)


def _old_engine():
    """The engine the deep-weight read grades, from git (R1's registration commit holds its exact bytes)."""
    from research import fumble_td_patch as Pt
    from research import registered_pins as RP
    commit = RP.REGISTRATIONS["research/data/nfl_coach/prereg_r1.json"][0]
    src = _git("show", f"{commit}:nfl_deep_sim.py").stdout
    assert hashlib.sha256(src).hexdigest() == Pt.PINNED_SHA["nfl_deep_sim.py"]
    m = types.ModuleType("nfl_deep_sim")
    m.__file__ = os.path.join(_ROOT, "nfl_deep_sim.py")
    exec(compile(src.decode(), m.__file__, "exec"), m.__dict__)
    return m


def run(ck):
    import nfl_deep_sim as E
    import nfl_dfs_engine as G
    from research import fumble_td_fit as Fit
    from research import fumble_td_patch as Pt
    from research import registered_pins as RP

    print("  -- FT. The fumble-return touchdown (staged), and the registered reads at their commits (option (c))")
    st = Pt.state()
    live_src = open(os.path.join(_ROOT, "nfl_deep_sim.py")).read()
    ck("FT1 production is the read's engine (no fumble-return TD, SIM_MODEL_VERSION 2) or exactly the patched one "
       "(FUMBLE_TD on, ENGINE_VERSION 2, SIM_MODEL_VERSION 3), never anything between",
       (st == "unpatched" and not hasattr(E, "FUMBLE_TD") and G.SIM_MODEL_VERSION == 2
        and "the engine plays no fumble-return TD" in live_src)
       or (st == "flipped" and E.FUMBLE_TD is True and E.ENGINE_VERSION == 2 and G.SIM_MODEL_VERSION == 3), st)

    ok_apply, why = Pt.check()
    gate = RP.deep_read_committed()
    ck("FT2 the flip cannot land before the registered deep-weight read is committed: the patched engine may stand "
       "only beside research/data/nfl_deep_weight_read.json, tracked and triggered; until then the patch applies "
       "cleanly to the frozen files and yields exactly the pinned flipped bytes (the anchor's DA2 is the twin)",
       (st == "flipped" and gate) or (st == "unpatched" and ok_apply and Pt.flipped_shas() == Pt.FLIPPED_SHA),
       f"state {st} gate {gate} apply {ok_apply} {why}")

    # ---- the fit and its definition
    fit = json.load(open(FIT))
    ch, ho = fit["chosen"], fit.get("heldout") or {}
    rule_at = hashlib.sha256(_git("show", f"{RULE_COMMIT}:{RULE}").stdout).hexdigest()
    anc = _git("merge-base", "--is-ancestor", RULE_COMMIT, "HEAD").returncode == 0
    ck("FT3 the rule came first: research/fumble_td_fit.py is byte-identical to the commit that registered it "
       "(461abe0, an ancestor of HEAD), the fit and the held-out read ran under it, the held-out read ran ONCE "
       "from that commit and passed both of its checks, so the selected form ships (kind + 20-yard zones, "
       "2016-2024)",
       anc and rule_at == hashlib.sha256(open(os.path.join(_ROOT, RULE), "rb").read()).hexdigest()
       == fit["rule_sha256"] and ho.get("commit") == RULE_COMMIT and ho.get("a_pass") and ho.get("b_pass")
       and ho.get("ships") == ch["form"] == "M3" and ch["window"] == "ALL" and ch["seasons"] == [2016, 2024]
       and fit["best"] in fit["candidates"] and ho.get("n") == 375,
       f"anc {anc} rule {rule_at[:12]} heldout {ho.get('commit', '')[:9]} {ho.get('ships')}")

    P = Pt.load(True)
    worst = max(abs(P._fum_td(k, y) - Fit.predict("M3", ch["coef"], {"sack": "sack", "comp": "comp", "rush": "rush"}[k], y))
                for k in ("sack", "comp", "rush") for y in range(1, 100))
    c = ch["coef"]
    ck("FT4 the engine plays the fit by its definition, not just its numbers: for every kind and every yards-to-go "
       "1-99 the patched nfl_deep_sim._fum_td equals research/fumble_td_fit.predict (the same zones, the same "
       "base kind and zone), its constants the fitted coefficients",
       worst < 2e-6 and abs(P._FUM_TD_LOGIT - c[0]) < 1e-6 and abs(P._FUM_TD_KIND["comp"] - c[1]) < 1e-5
       and abs(P._FUM_TD_KIND["rush"] - c[2]) < 1e-5 and P._FUM_TD_ZONE[2] == 0.0
       and abs(P._FUM_TD_ZONE[4] - c[6]) < 1e-5, f"worst {worst:.2e}")

    # ---- mechanics on shared seeds: old (git, the read's bytes), patched with the switch off, patched on
    T = E.tables()
    L = E.league_profile(T)
    O = _old_engine()
    Poff = Pt.load(False)
    same_off = same_nofum = True
    bad_pts = bad_def = n_six = 0
    for s in range(1500):
        go = O.play_game(L, L, random.Random(s), T)
        gf = Poff.play_game(L, L, random.Random(s), T)
        gn = P.play_game(L, L, random.Random(s), T)
        same_off &= json.dumps(go, sort_keys=True) == json.dumps(gf, sort_keys=True)
        if go["h"]["fum_lost"] + go["a"]["fum_lost"] == 0:
            same_nofum &= json.dumps(go, sort_keys=True) == json.dumps(gn, sort_keys=True)
        for me, op in (("h", "a"), ("a", "h")):
            b, o = gn[me], gn[op]
            bad_pts += b["pts"] != (6 * (b["td_pass"] + b["td_rush"] + b["td_ret"]) + 3 * b["fg_made"] + b["xp_made"]
                                    + 2 * b["two_made"] + 2 * o["ends"]["safety"])
            bad_def += b["td_def"] > min(b["td_ret"], o["int"] + o["fum_lost"])
            n_six += b["td_def"]
    ck("FT5 with FUMBLE_TD off the patched file IS the read's engine, draw for draw (1,500 games, the old bytes "
       "from git); with it on, every game without a lost fumble is unchanged, every point is a touchdown, field "
       "goal, try or safety, and every defensive touchdown is one of this side's return TDs and one of the other "
       "offence's interceptions or lost fumbles",
       same_off and same_nofum and bad_pts == 0 and bad_def == 0 and n_six > 0,
       f"off {same_off} nofum {same_nofum} pts {bad_pts} def {bad_def} six {n_six}")

    # the draw happens at the model's probability at the engine's own sites (the passive log names kind and spot)
    kind_of = {"sack": "sack", "cmp": "comp", "run": "rush"}
    exp = var = obs = nfum = rtd_ok = 0
    for s in range(4000):
        lg = []
        P.play_game(L, L, random.Random(10 ** 6 + s), T, log=lg)
        for j, e in enumerate(lg):
            if e.get("to") == "fum":
                p = P._fum_td(kind_of[e["k"]], e["y"])
                exp, var, nfum = exp + p, var + p * (1 - p), nfum + 1
                if e.get("six"):
                    obs += 1
                    nxt = next(x for x in lg[j + 1:] if x["k"] != "2mw")      # the warning may sit between
                    rtd_ok += nxt["k"] == "rtd" and nxt["o"] == 1 - e["o"]
    z =(obs - exp) / max(1e-9, var ** 0.5)
    ck("FT6 every fumble-return TD is drawn at the fitted probability for ITS play (the log's kind -- sack, catch, "
       "run -- and yards to go at the snap): over 4,000 logged games the count sits within 3 SE of the sum of the "
       "model's probabilities, and every one is followed by the defense's touchdown",
       nfum > 1000 and abs(z) < 3 and obs > 0 and rtd_ok == obs, f"fumbles {nfum} six {obs} expected {exp:.1f} z {z:.2f}")

    # DraftKings' points allowed leave a fumble-return TD's six out, as a pick-six's (off_points subtracts td_def)
    hb = {"pts": G.np.asarray([13]), "td_def": G.np.asarray([1])}
    ck("FT7 a fumble-return TD counts in td_def, so nfl_dfs_engine.off_points leaves its six -- not its try -- out "
       "of the other D/ST's points allowed (the owner's rule, 2026-11-26), and pays this D/ST's return TD",
       float(G.off_points(hb, {"safety_conceded": G.np.asarray([0])})[0]) == 7.0
       and "box[1 - off][\"td_def\"] += 1" in Pt.patched_sources()["nfl_deep_sim.py"].split("if six:")[1][:600]
       and "box[1 - off][\"td_ret\"] += 1" in Pt.patched_sources()["nfl_deep_sim.py"].split("if six:")[1][:600])

    # ---- the flip versions what must change: the worlds cache key, the frozen inputs, the board's stamp
    import inspect
    import nfl_dfs_sim
    import pool_cache
    src = Pt.patched_sources()
    # both sides built (so the check reads the same before and after the flip): the live code with the READ's
    # two files (git, R1's registration commit, sha-checked) against the live code with the patched two
    commit = RP.REGISTRATIONS["research/data/nfl_coach/prereg_r1.json"][0]
    old = {rel: _git("show", f"{commit}:{rel}").stdout for rel in Pt.TARGETS}
    assert all(hashlib.sha256(old[rel]).hexdigest() == Pt.PINNED_SHA[rel] for rel in Pt.TARGETS)
    call = {"season": "2026", "week": 13, "n": 1000}
    sv_here, sv_sig = pool_cache._HERE, dict(pool_cache._code_sig)
    sigs = []
    try:
        for files in (old, {rel: text.encode() for rel, text in src.items()}):
            tmp = tempfile.mkdtemp(prefix="fumble_td_sig_")
            try:
                for rel in pool_cache._CODE_FILES:
                    dst = os.path.join(tmp, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy(os.path.join(_ROOT, rel), dst)
                for rel, data in files.items():
                    with open(os.path.join(tmp, rel), "wb") as fh:
                        fh.write(data)
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
    pp = inspect.getsource(nfl_dfs_sim.player_pool)
    ck("FT8 the flip invalidates the worlds cache and a resumable build's frozen inputs (both files are in "
       "pool_cache's code files: a new code signature, so every worlds key changes and a pinned build raises "
       "PinMiss), the board's simulator stamp says version 3, and the deep season payload stamps the engine's version",
       all(f in pool_cache._CODE_FILES for f in Pt.TARGETS) and before != after and k_before != k_after
       and 'if pinned and pc_key != pin["worlds_key"]' in pp and "raise PinMiss" in pp
       and "\nSIM_MODEL_VERSION = 3\n" in src["nfl_dfs_engine.py"] and "\nENGINE_VERSION = 2\n" in src["nfl_deep_sim.py"]
       and '"engine_version": getattr(E, "ENGINE_VERSION", 1)' in inspect.getsource(__import__("nfl_deep_season").run_deep),
       f"{before[:12]} {after[:12]}")

    # ---- option (c): the registrations at their commits
    probs = RP.verify_all()
    ck("FT9 every registration under research/data that pins a file a staged flip changes (nine: R1-R6, R5's "
       "amendment, C4, the coaching DFS read) is under option (c) and holds AT ITS REGISTRATION COMMIT: the commit "
       "added its prereg, the prereg is unedited since, and every pinned file there hashes to its pin; live, "
       "nothing frozen moved",
       set(RP.REGISTRATIONS) == RP.pinning(Pt.TARGETS + ("nfl_deep_data.py",)) and len(RP.REGISTRATIONS) == 9
       and not any(probs.values()), {k: v for k, v in probs.items() if v} or sorted(RP.pinning(Pt.TARGETS)))

    r1 = "research/data/nfl_coach/prereg_r1.json"
    sv_reg, sv_live, sv_gate = dict(RP.REGISTRATIONS), RP.live_sha, RP.deep_read_committed
    try:
        RP.REGISTRATIONS[r1] = ("0" * 40, sv_reg[r1][1])
        lost = RP.moved(r1)
        RP.REGISTRATIONS[r1] = (sv_reg["research/data/nfl_coach/prereg_r6.json"][0], sv_reg[r1][1])
        wrong = RP.moved(r1)
        RP.REGISTRATIONS[r1] = sv_reg[r1]
        RP.live_sha = lambda rel: "f" * 64 if rel == "nfl_coach_sim.py" else sv_live(rel)
        frozen = RP.live_problems(r1)
        RP.live_sha = lambda rel: Pt.FLIPPED_SHA["nfl_deep_sim.py"] if rel == "nfl_deep_sim.py" else sv_live(rel)
        RP.deep_read_committed = lambda: False
        closed = RP.live_problems(r1)
        RP.deep_read_committed = lambda: True
        opened = RP.live_problems(r1)
        RP.live_sha = lambda rel: "e" * 64 if rel == "nfl_deep_sim.py" else sv_live(rel)
        other = RP.live_problems(r1)
    finally:
        RP.REGISTRATIONS.clear()
        RP.REGISTRATIONS.update(sv_reg)
        RP.live_sha, RP.deep_read_committed = sv_live, sv_gate
    ck("FT10 the checks still FAIL when the registered version is lost or altered: a registration commit not in "
       "the clone (a shallow CI checkout, a rewritten history), a commit that did not add the prereg, a frozen "
       "engine moved live, the patched engine before the read's gate, any bytes but the staged flip's after it",
       lost and "not in this clone" in lost[0] and wrong and frozen == ["nfl_coach_sim.py"]
       and closed == ["nfl_deep_sim.py"] and opened == [] and other == ["nfl_deep_sim.py"],
       [lost, wrong, frozen, closed, opened, other])

    wf = open(os.path.join(_ROOT, ".github", "workflows", "guards.yml")).read()
    ck("FT11 CI checks out the whole history (fetch-depth 0): the pins are read at the registration commits",
       "fetch-depth: 0" in wf)

    # the safety net: each read, in a tree holding its registration's files with the engine patched, refuses
    from research import nfl_coach_c4_confirm as CF
    from research import nfl_coach_dfs_read as DR
    from research import nfl_coach_r1_read as R1
    from research import nfl_coach_r2_read as R2
    from research import nfl_coach_r3_read as R3
    from research import nfl_coach_r4_read as R4
    from research import nfl_coach_r5_read as R5
    from research import nfl_coach_r5_read_2027 as R5A
    from research import nfl_coach_r6_read as R6
    from research import nfl_coach_reg as RG

    class _All(dict):
        def get(self, k, d=None):
            return 1
    far, pre = "2099-01-01T00:00:00Z", "research/data/nfl_coach/"
    calls = {
        "prereg_r1.json": ((R1,), lambda: R1.refusal(now_utc=far, dirty=False)),
        "prereg_r2.json": ((R2,), lambda: R2.refusal("H1", now_utc=far, dirty=False, r1_read=True, already=False)),
        "prereg_r3.json": ((R3,), lambda: R3.refusal(now_utc=far, dirty=False, r1_read=True, already=False)),
        "prereg_r4.json": ((R4,), lambda: R4.refusal(now_utc=far, dirty=False, r1_read=True, already=False)),
        "prereg_r5.json": ((R5,), lambda: R5.refusal(now_utc=far, dirty=False, earlier_read=_All(), already=False)),
        "prereg_r5_2027.json": ((R5A.RR,), lambda: R5A.refusal(now_utc=far, dirty=False, earlier_read=_All(),
                                                                already=False)),
        "prereg_r6.json": ((R6,), lambda: R6.refusal("primary", now_utc=far, dirty=False, earlier_read=_All(),
                                                      already=False)),
        "prereg_c4.json": ((RG,), lambda: RG.common_refusal(CF.prereg(), far, "2000-01-01T00:00:00Z", False,
                                                            os.path.join(tempfile.gettempdir(), "never_c4.json"))),
        "prereg_dfs.json": ((RG,), lambda: DR.refusal(far, False, DR.prereg())),
    }
    runs, refuses = {}, {}
    for name, (mods, call) in calls.items():
        with RP.registered_view(pre + name, *mods) as d:
            runs[name] = call()
            for rel, text in src.items():
                if os.path.exists(os.path.join(d, rel)):
                    with open(os.path.join(d, rel), "w") as fh:
                        fh.write(text)
            refuses[name] = call()
    ck("FT12 the safety net: every registered read would run in its registration commit's tree, and REFUSES the "
       "same tree with the fumble-TD patch applied (nfl_deep_sim.py, and nfl_dfs_engine.py for the DFS read, "
       "differ from their registered sha256)",
       all(v is None for v in runs.values())
       and all(v and "differs from its registered sha256" in v and v.split()[0] in Pt.TARGETS for v in refuses.values()),
       {k: (runs[k], refuses[k]) for k in calls if runs[k] is not None or not refuses[k]})

    # ---- the evaluations the report quotes
    dst = json.load(open(os.path.join(_ROOT, "research", "data", "fumble_td_dst.json")))
    ac = json.load(open(os.path.join(_ROOT, "research", "data", "fumble_td_anchor_check.json")))
    ck("FT13 the D/ST tail was measured on the real games (2025 + 2026 weeks 1-12, 436) both ways -- production, "
       "the patched engine on the same profiles, and re-anchored -- and the anchor's 1.86 was re-verified with both "
       "patches in, on the committed verification's seeds, within its own stated tolerance",
       dst["games"] == 436 and set(dst["engine"]) == {"A", "B", "C"} and "minus_A" in dst["engine"]["C"]
       and dst["real_dk"]["mean"][0] > 0 and ac["k"] == 1.86 and ac["holds"] is True
       and ac["engine"]["nfl_deep_sim"].startswith("fumble-TD patched"),
       f"games {dst.get('games')} anchor {ac.get('verify')} holds {ac.get('holds')}")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
