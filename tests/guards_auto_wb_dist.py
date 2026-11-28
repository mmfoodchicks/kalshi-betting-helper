"""Guards for the registered replacement test of the NFL week board's player distributions (2026-11-27;
research/nfl_wb_dist.py, research/data/nfl_wb_dist/, research/reports/nfl_wb_dist.md). What each proves:

  * WB1 the candidates were written before scoring and the harness runs exactly them (grids, knots, prior);
  * WB2 the registration: its own files unchanged since registration, the rule the harness applies, registered
    before the first prospective kickoff, the window prospective only, the read date after week 18 + 48 h;
  * WB3 development never reads a sealed week; only the read may;
  * WB4 production untouched: no app module imports the harness; candidate B's hook at 1.0 is the allocator
    draw for draw; candidate C's map at the identity changes nothing, and otherwise keeps every world's support
    and rank order;
  * WB5 the statistics: the game-cluster ratio estimator, Holm in order of z, each veto;
  * WB6 the read refuses before its date and on a changed frozen file;
  * WB7 the registry lists it, and the report quotes the development figures beside their definitions.

numpy-free checks always run (CI's runner has no numpy); the numpy ones say so when skipped.

    python3 tests/guards_auto_wb_dist.py       # exits 1 on any failed check
"""
import calendar
import glob
import hashlib
import json
import os
import re
import sys
import time

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "research"))
_D = os.path.join(_ROOT, "research", "data", "nfl_wb_dist")
# 2026 week 14's first kickoff (Thursday 2026-12-03, 8:15 pm ET); the registration must precede it
_WEEK14_FIRST_KICK = calendar.timegm((2026, 12, 4, 1, 15, 0))


def _sha(rel):
    with open(os.path.join(_ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(ck):
    reg = json.load(open(os.path.join(_D, "prereg.json")))
    cand = json.load(open(os.path.join(_D, "candidates.json")))
    dev = json.load(open(os.path.join(_D, "dev.json")))
    hsrc = open(os.path.join(_ROOT, "research", "nfl_wb_dist.py"), encoding="utf-8").read()
    rep = open(os.path.join(_ROOT, "research", "reports", "nfl_wb_dist.md"), encoding="utf-8").read()

    # ---- WB1 ---------------------------------------------------------------------------------------------
    ids = [c["id"] for c in cand["candidates"]]
    ck("WB1 the candidates were written down before any was scored (committed alone, its commit named in the "
       "registration) and the harness runs exactly them: A the constrained simulator unrefitted, B's grid 0.0-1.0 "
       "by 0.1, C's knots 0, 0.05, ..., 1 with a prior of 20",
       ids == ["A", "B", "C"] and 'CANDIDATES = ("A", "B", "C")' in hsrc
       and "K_GRID = [j / 10 for j in range(11)]" in hsrc and "KNOTS = [j / 20 for j in range(21)]" in hsrc
       and "PRIOR = 20.0" in hsrc and "0.0, 0.1, ..., 1.0" in json.dumps(cand) and "(count + 20 x t) / (N + 20)" in json.dumps(cand)
       and reg["candidates_commit"] == "88a3fed" and "BEFORE any candidate below was scored" in cand["status"])

    # ---- WB2 ---------------------------------------------------------------------------------------------
    own = ("research/nfl_wb_dist.py", "research/data/nfl_wb_dist/frozen.json", "research/data/nfl_wb_dist/candidates.json",
           "nfl_discrete_alloc.py")
    changed = [p for p in own if _sha(p) != reg["frozen_sha256"][p]]
    rule_src = re.search(r"^RULE = (\{.*\})$", hsrc, re.M)
    ck("WB2 the registration: the test's own files are as registered (never edit them -- a change is a new "
       "registration), the rule in the file is the harness's, registered before 2026 week 14's first kickoff, the "
       "window 2026 weeks 14-18 only (week 13 had started), the read not before week 18 + 48 h",
       not changed and rule_src is not None and json.loads(rule_src.group(1)) == {"primary": reg["primary_rule"], "veto": reg["veto"]}
       and reg["registered_ts"] < _WEEK14_FIRST_KICK and reg["sample"]["window"] == {"2026": [14, 18]}
       and reg["sample"]["extension"] == {"2027": [1, 6]} and reg["sample"]["n_min_games"] == 60
       and reg["read"]["not_before_ts"] == calendar.timegm((2027, 1, 13, 6, 0, 0))
       and set(own) <= set(reg["frozen_sha256"]), str(changed))

    # ---- WB3 ---------------------------------------------------------------------------------------------
    ck("WB3 development never reads a sealed week: the loader asserts it unless the read asks, only read() asks, and "
       "the development windows stop at 2026 week 5 (all the 2026 table held); the development report says the "
       "games were already seen",
       'assert allow_sealed or not (s in SEALED_FROM and w >= SEALED_FROM[s]), "a sealed week was read"' in hsrc
       and hsrc.count("allow_sealed=True") == 2 and hsrc.index("allow_sealed=True") > hsrc.index("def read(")
       and 'DEV_TEST = {"2026": range(1, 6)}' in hsrc and 'DEV = {"2025": range(1, 13), "2026": range(1, 6)}' in hsrc
       and "already read by the integer-stats study" in dev["meta"]["note"])

    # ---- WB4 (numpy-free part) ---------------------------------------------------------------------------
    importers = [os.path.basename(p) for p in glob.glob(os.path.join(_ROOT, "*.py"))
                 if re.search(r"^\s*(?:import|from)\s+nfl_wb_dist\b", open(p, encoding="utf-8").read(), re.M)]
    ck("WB4 production untouched: no app module imports the harness (the week board stays as it is, labelled "
       "overconfident; DA4 pins it byte for byte)", not importers, str(importers))

    # ---- WB7 ---------------------------------------------------------------------------------------------
    regy = {e["id"]: e for e in json.load(open(os.path.join(_ROOT, "research", "data", "research_registry.json")))}
    e = regy.get("nfl_wb_dist")
    oos = dev["out_of_sample"]["compare"]
    ck("WB7 the registry lists it with its registration, script, date and report; the report quotes the development "
       "figures beside their definitions (the per-line log-loss gain over production pooled over every scored line, "
       "clustered by game) and names the development games as seen",
       e is not None and e["registration"] == "research/data/nfl_wb_dist/prereg.json" and e["script"] == "research/nfl_wb_dist.py"
       and e["not_before"] == reg["read"]["not_before_utc"] and e["report"] == "research/reports/nfl_wb_dist.md"
       and all(f"{oos[c]['gain_per_line']}" in rep and f"{oos[c]['z']}" in rep for c in ("A", "B", "C"))
       and "per-line log-loss gain over production, pooled over every scored line and clustered by game" in rep
       and "already read by the integer-stats study" in rep)

    try:
        import numpy as np
    except ImportError:
        ck("WB4b-WB6 skipped: numpy is not installed here (the PC and the dev box run them)", True)
        return
    import nfl_discrete_alloc as D
    import nfl_wb_dist as W

    g = {"label": "B @ A", "teams": ["A", "B"], "players": [
        {"name": f"{t} {p}", "pos": p, "team": t, "opp": o, "proj_pts": 12.0,
         "means": {"pass_yd": 240.0 if p == "QB" else 0.0, "pass_td": 1.6 if p == "QB" else 0.0, "int": 0.8 if p == "QB" else 0.0,
                   "rush_yd": 14.0 if p == "QB" else 0.0, "rush_td": 0.1 if p == "QB" else 0.0,
                   "rec": 6.0 if p == "WR" else 0.0, "rec_yd": 80.0 if p == "WR" else 0.0, "rec_td": 0.5 if p == "WR" else 0.0,
                   "fum": 0.1, "pass_att": 34.0 if p == "QB" else 0.0, "pass_cmp": 22.0 if p == "QB" else 0.0,
                   "rec_tgt": 9.0 if p == "WR" else 0.0, "rush_att": 3.0 if p == "QB" else 0.0}}
        for t, o in (("A", "B"), ("B", "A")) for p in ("QB", "WR")]}
    a1 = D.simulate_game(g, n=500, rng=np.random.default_rng(5))
    a2 = D.simulate_game(g, n=500, rng=np.random.default_rng(5), pass_latent_scale=1.0)
    same = all((x["comps"][k] == y["comps"][k]).all() for x, y in zip(a1["players"], a2["players"]) for k in x["comps"])
    wP = W.worlds_P(g, 400, 11)
    ident = {f"{p['pos']}|{s}": list(W.KNOTS) for p in g["players"] for s in wP[0]}
    wC = W.worlds_C(wP, g, ident)
    unchanged = all(np.array_equal(np.sort(a[s]), np.sort(b[s])) and np.array_equal(a[s], b[s])
                    for a, b in zip(wP, wC) for s in a)
    real = W.g_values(list(np.linspace(0.0, 0.3, 50)) + [0.9] * 10)     # a PIT piled low: the map must move mass
    x = np.asarray([0, 0, 1, 1, 1, 2, 3, 3, 4, 7] * 40, dtype=np.float64)
    y = W.worlds_C([{"rec_td": x}], {"players": [{"pos": "WR"}]}, {"WR|rec_td": real})[0]["rec_td"]
    order = np.argsort(x, kind="stable")
    ck("WB4b candidate B's hook at 1.0 is the allocator draw for draw; candidate C's map at the identity returns "
       "production's worlds unchanged, and a fitted map keeps every world's value inside the original support "
       "(integers stay integers), keeps the worlds' order (production's copula) and does move the mass",
       same and unchanged and set(np.unique(y)) <= set(np.unique(x)) and bool(np.all(np.diff(y[order]) >= 0))
       and not np.array_equal(np.sort(y), np.sort(x)) and all(b >= a for a, b in zip(real, real[1:])))

    # ---- WB5 ---------------------------------------------------------------------------------------------
    dg = [(1.0, 10), (3.0, 10), (2.0, 20)]
    Dv, se = W.ratio(dg)
    M = 40
    hand = (6.0 / M, (sum((d - 0.15 * m) ** 2 for d, m in dg) * 3 / 2) ** 0.5 / M)

    class Cl:
        def __init__(self, n, c80):
            self.n, self.cov = n, {80: c80}

    def cells(cov):
        return {p: {"WR|rec": Cl(100, int(100 * cov[p]))} for p in cov}

    def cmp_(zs, cellz=None):
        return {c: {"z": z, "cells": {"WR|rec": {"z": (cellz or {}).get(c, 1.0)}}} for c, z in zs.items()}
    cor = {"WR-WR": {"P": 0.33, "A": 0.01, "B": 0.0, "C": 0.48, "observed": -0.05}}
    rule = {"primary": {"holm_z": [2.128, 1.960, 1.645]}, "veto": {"cell_z": 2.0, "cover80_band": [0.70, 0.90]}}
    base_cov = {"P": 0.45, "A": 0.78, "B": 0.76, "C": 0.62}
    v1 = W.decide(cmp_({"A": 7.0, "B": 30.0, "C": 27.0}), cells(base_cov), cor, rule)
    v2 = W.decide(cmp_({"A": 2.05, "B": 2.1, "C": 1.0}), cells(base_cov), cor, rule)
    v3 = W.decide(cmp_({"A": 7.0, "B": 30.0, "C": 27.0}, {"B": -2.5}), cells(base_cov), cor, rule)
    ck("WB5 the primary is sum(gain) / sum(lines) with its game-cluster SE (by hand); Holm tests in order of z and "
       "stops at the first failure (z 2.1 first misses 2.128, so the next, z 2.05, fails though it clears its own "
       "1.960); a cell "
       "worse by 2 SE vetoes; coverage must sit in 0.70-0.90 AND nearer 0.80 than production; correlations no "
       "further from the observed than production's",
       abs(Dv - hand[0]) < 1e-12 and abs(se - hand[1]) < 1e-12
       and v1["recommended"] == "B" and v1["A"]["passes"] and not v1["C"]["passes"] and not v1["C"]["cover_ok"]
       and not v1["C"]["corr_ok"] and not v2["B"]["primary_pass"] and not v2["A"]["primary_pass"]
       and v3["recommended"] == "A" and v3["B"]["cells_worse_2se"] == ["WR|rec"], str([v1, v2["A"], v3["recommended"]]))

    # ---- WB6 ---------------------------------------------------------------------------------------------
    early = W.read(now=reg["read"]["not_before_ts"] - 60)
    saved = W._sha
    W._sha = lambda p: "0" * 64 if p == "nfl_dfs_sim.py" else saved(p)
    try:
        moved = W.read(now=reg["read"]["not_before_ts"] + 60)
    finally:
        W._sha = saved
    ck("WB6 the read refuses before its date (reading nothing) and when a frozen file has changed (it is then run "
       "from the registration commit's tree)",
       "not before" in early.get("refused", "") and "frozen file changed" in moved.get("refused", "")
       and "nfl_dfs_sim.py" in moved["refused"], str([early, moved]))
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="wbread-")
    stand = dict(reg, frozen_sha256={}, worlds=200,
                 sample=dict(reg["sample"], window={"2026": [1, 1]}, n_min_games=5))
    json.dump(stand, open(os.path.join(tmp, "prereg.json"), "w"))
    shutil.copy(os.path.join(_D, "frozen.json"), tmp)
    saved_out = W.OUT
    W.OUT = tmp
    try:
        dry = W.read(now=reg["read"]["not_before_ts"] + 60)
    finally:
        W.OUT = saved_out
        shutil.rmtree(tmp, ignore_errors=True)
    vd = dry.get("verdict") or {}
    ck("WB6b the read runs end to end on a stand-in (2026 week 1, a development week, 200 worlds): every candidate "
       "gets its z, Holm threshold, the three vetoes and a pass flag, and one recommendation (or none)",
       dry.get("games", 0) >= 5 and all(c in vd and {"z", "holm_threshold", "cells_worse_2se", "cover_ok", "corr_ok",
                                                      "passes"} <= set(vd[c]) for c in ("A", "B", "C"))
       and "recommended" in vd, str(dry.get("refused") or list(vd)))


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    t0 = time.time()
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed ({time.time() - t0:.1f}s)")
    sys.exit(1 if FAIL else 0)
