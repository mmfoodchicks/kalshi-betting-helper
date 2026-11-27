"""Guards for the C3 season-simulator replay (2026-11-27; research/c3_season_replay.py, accuracy_gaps.md 13.2).

    python3 tests/guards_auto_c3.py      # exits 1 on any failed check

What they pin: the replay reads nothing from the future (a projection is unchanged bit for bit when every later
result, ranking and outcome is scrambled -- and changes when an earlier one is); the two arms see identical
inputs and identical non-game draws; the engine arm IS production's run_season draw for draw, both sports; the
analytic arm is Phi(margin / sigma) on the same margin; the registered rule's arithmetic on fixtures; and the
committed result's verdict against what production now does.
"""
import copy
import inspect
import json
import math
import os
import random
import sys

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)


def _scramble_after(inp, sport, season, w, seed=11):
    """A copy whose every result of week >= w, every ranking released at or after week w's first kickoff and
    every season outcome is changed."""
    import research.c3_season_replay as C
    x = copy.deepcopy(inp)
    rng = random.Random(seed)
    d = x[sport][str(season)]
    for g in d["games"]:
        if g["week"] >= w:
            g["hs"], g["as"] = float(rng.randint(0, 60)), float(rng.randint(0, 60))
    if sport == "cfb":
        kick = C.first_kickoff(d["games"], w)
        for r in d["rankings"]:
            if C._iso(r["date"]) >= C._iso(kick):
                r["ranks"] = list(reversed(r["ranks"]))
        d["cfp_field"] = d["order"][:12]
        d["champion"] = d["order"][0]
    else:
        d["seeds"] = {ab: 16 for ab in d["seeds"]}
    return x


def run(ck):
    import cfb
    import nfl_season
    import mp_season
    import research.c3_season_replay as C
    inp = C.load_inputs()

    # ---- C3a: as of --------------------------------------------------------------------------------------
    ok_cfb = []
    for s, w in ((2024, 6), (2025, 11), (2025, 15)):
        A0 = C.cfb_asof(inp["cfb"][str(s)], w)
        A1 = C.cfb_asof(_scramble_after(inp, "cfb", s, w)["cfb"][str(s)], w)
        r0 = C.sim_cfb(A0, "engine", 6, 3)
        r1 = C.sim_cfb(A1, "engine", 6, 3)
        played_ok = all(g["week"] < w for g in A0["sched"] if g["final"]) and \
            all(g["week"] >= w for g in A0["sched"] if not g["final"])
        ccg_ok = all(not g["ccg"] or g["week"] <= w for g in A0["sched"])
        kick = C.first_kickoff(inp["cfb"][str(s)]["games"], w)
        rk = next((r for r in inp["cfb"][str(s)]["rankings"] if r["week"] == A0["ranking_week"]), None)
        rank_ok = A0["ranking"] is None or (rk is not None and C._iso(rk["date"]) < C._iso(kick))
        ok_cfb.append(C.digest(A0) == C.digest(A1) and r0["counts"] == r1["counts"] and played_ok and ccg_ok and rank_ok)
    ok_nfl = []
    for s, w in ((2024, 5), (2025, 14)):
        A0 = C.nfl_asof(inp["nfl"][str(s)], w)
        A1 = C.nfl_asof(_scramble_after(inp, "nfl", s, w)["nfl"][str(s)], w)
        r0, r1 = C.sim_nfl(A0, "engine", 6, 3), C.sim_nfl(A1, "engine", 6, 3)
        ok_nfl.append(C.digest(A0) == C.digest(A1) and r0["counts"] == r1["counts"]
                      and all(g["week"] < w for g in A0["sched"] if g["final"]))
    # teeth: changing a result BEFORE the week must change the as-of ratings
    x = copy.deepcopy(inp)
    g = next(g for g in x["cfb"]["2024"]["games"] if g["week"] == 5 and g["home"] in x["cfb"]["2024"]["teams"]
             and g["away"] in x["cfb"]["2024"]["teams"])
    g["hs"], g["as"] = g["as"], g["hs"] + 1
    y = copy.deepcopy(inp)
    gn = next(g for g in y["nfl"]["2024"]["games"] if g["week"] == 4)
    gn["hs"], gn["as"] = gn["as"], gn["hs"] + 1
    teeth = (C.cfb_asof(x["cfb"]["2024"], 6)["R"] != C.cfb_asof(inp["cfb"]["2024"], 6)["R"]
             and C.nfl_asof(y["nfl"]["2024"], 5)["R"] != C.nfl_asof(inp["nfl"]["2024"], 5)["R"])
    ck("C3a the replay reads nothing from the future: a week's projection (college weeks 6, 11, 15; NFL 5, 14) is "
       "bit-identical when every later result, every committee ranking released at or after that week's first "
       "kickoff and every season outcome is scrambled; played rows are all earlier weeks, a conference title "
       "game is scheduled only from its own week, the ranking read predates the kickoff -- and an EARLIER "
       "result does move the ratings (the check has teeth)",
       all(ok_cfb) and all(ok_nfl) and teeth, f"cfb {ok_cfb} nfl {ok_nfl} teeth {teeth}")

    # ---- C3b: identical inputs, identical non-game draws -----------------------------------------------------
    A = C.cfb_asof(inp["cfb"]["2025"], 9)
    e, a = C.sim_cfb(A, "engine", 8, 5), C.sim_cfb(A, "analytic", 8, 5)
    e2 = C.sim_cfb(A, "engine", 8, 5)
    An = C.nfl_asof(inp["nfl"]["2025"], 9)
    ne, na = C.sim_nfl(An, "engine", 8, 5), C.sim_nfl(An, "analytic", 8, 5)
    src = inspect.getsource(C._job)
    ck("C3b both arms on identical inputs: one as-of build feeds both (the job builds it once from the season and "
       "week, never from the arm), the input digests and college's season-form draws are equal across arms, the "
       "arm is the only argument that differs, and a rerun is identical",
       e["inputs"] == a["inputs"] and e["form"] == a["form"] and e["counts"] == e2["counts"]
       and ne["inputs"] == na["inputs"] and "arm" not in src.split("sim_cfb(")[0].split("cfb_asof(")[1].split(")")[0]
       and C.seed_for("cfb", 2025, 9) == C.seed_for("cfb", 2025, 9) and e["counts"] != a["counts"],
       f"{e['inputs']} {a['inputs']} {e['form']} {a['form']}")

    # ---- C3c: the engine arm is production's run_season, draw for draw ------------------------------------------
    saved = {(cfb, k): getattr(cfb, k) for k in ("teams", "ratings", "schedule")}
    saved.update({(nfl_season, k): getattr(nfl_season, k) for k in ("full_schedule", "_ratings", "_team_name")})
    saved[(mp_season, "run")] = mp_season.run
    try:
        mp_season.run = lambda *a_, **k_: None
        A = C.cfb_asof(inp["cfb"]["2024"], 8)
        tm = {t: {"name": t, "abbr": t, "location": t, "nick": t, "conf": A["conf"][t], "w": 0, "l": 0}
              for t in A["order"]}
        cfb.teams = lambda season=None, div="fbs": tm
        cfb.ratings = lambda season=None, div="fbs": dict(A["prior"])
        cfb.schedule = lambda season=None, div="fbs": [dict(g) for g in A["sched"]]
        prod = cfb.run_season(2024, n=25, seed=7)
        mine = C.sim_cfb(A, "engine", 25, 7, single=True, use_committee=False)
        rows = {r["team_id"]: r for r in prod["teams"]}
        cfb_same = all(rows[t]["proj_wins"] == round(c[1] / 25, 1) and rows[t]["playoff_pct"] == round(100.0 * c[3] / 25, 1)
                       and rows[t]["champ_pct"] == round(100.0 * c[4] / 25, 2) for t, c in mine["counts"].items())
        cfb_real = sum(c[4] for c in mine["counts"].values()) == 25 and len({r["proj_wins"] for r in prod["teams"]}) > 5
        An = C.nfl_asof(inp["nfl"]["2025"], 7)
        nfl_season.full_schedule = lambda season: [dict(g) for g in An["sched"]]
        nfl_season._ratings = lambda season: dict(An["R"])
        nfl_season._team_name = lambda ab: ab
        prod_n = nfl_season.run_season(2025, n=30, seed=9)
        mine_n = C.sim_nfl(An, "engine", 30, 9, single=True)
        rn = {r["abbr"]: r for r in prod_n["teams"]}
        nfl_same = all(rn[ab]["proj_wins"] == round(c[0] / 30, 1) and rn[ab]["p_playoffs"] == round(100.0 * c[1] / 30, 1)
                       and rn[ab]["p_division"] == round(100.0 * c[2] / 30, 1)
                       and rn[ab]["p_pennant"] == round(100.0 * c[3] / 30, 1)
                       and rn[ab]["p_ws"] == round(100.0 * c[4] / 30, 1) for ab, c in mine_n["counts"].items())
    finally:
        for (mod, k), v in saved.items():
            setattr(mod, k, v)
    form_src = "_FORM_SD = %s" % C.CFB_FORM_SD in inspect.getsource(cfb.run_season)
    ck("C3c the engine arm IS production: with one generator for every draw and production's CFP metric, the "
       "replay's college routine reproduces cfb.run_season (fed the same as-of teams, prior and schedule; its own "
       "inseason_ratings fit) and the NFL routine nfl_season.run_season, every team's projected wins, playoff, "
       "division, pennant and title figure equal, 25 and 30 seasons; the season form SD is the source's 2.6",
       cfb_same and cfb_real and nfl_same and form_src, f"cfb {cfb_same}/{cfb_real} nfl {nfl_same} form {form_src}")

    # ---- C3d: the analytic arm ---------------------------------------------------------------------------------
    class _Fixed:
        def __init__(self, u):
            self.u = u

        def random(self):
            return self.u
    res = C.cfb_resolver("analytic")
    p = 0.5 * (1 + math.erf(((4.0 - 1.0) + 3.0) / 18.0 / math.sqrt(2)))
    pn = 0.5 * (1 + math.erf(((10.0 - 8.0) * 2.25 + 2.2) / 13.2 / math.sqrt(2)))
    ck("C3d the analytic arm resolves a game at Phi(margin / sigma) on the engine's own margin: college "
       "(R_h - R_a) + 3.0 x host over 18, NFL (R_h - R_a) x 2.25 + 2.2 x host over 13.2 -- the home side wins "
       "exactly when the uniform draw is under that probability",
       res(4.0, 1.0, 1, _Fixed(p - 1e-9)) and not res(4.0, 1.0, 1, _Fixed(p + 1e-9))
       and abs(C.nfl_prep("analytic", 10.0, 8.0, 1) - pn) < 1e-15
       and C.nfl_play("analytic", pn, _Fixed(pn - 1e-9)) and not C.nfl_play("analytic", pn, _Fixed(pn + 1e-9))
       and C.SIGMA == {"cfb": cfb._SIGMA, "nfl": 13.2}, f"p {p} pn {pn}")

    # ---- C3e: the registered rule's arithmetic -------------------------------------------------------------------
    d = [0.3, 0.1, 0.5, 0.2, 0.4, 0.0, 0.6, 0.3]
    cl = ["a", "a", "b", "b", "c", "c", "d", "d"]
    st = C.paired(d, cl)
    m = sum(d) / len(d)
    sums = {}
    for t, c in zip(d, cl):
        sums[c] = sums.get(c, 0.0) + t - m
    se = math.sqrt(sum(v * v for v in sums.values()) * 4 / 3) / len(d)

    def mk(mean, se_):
        return {"mean": mean, "se": se_}
    win, short, lose = mk(0.10, 0.04), mk(0.076, 0.04), mk(-0.05, 0.01)
    v_pass = C.verdict({"2024": {"mae": win, "ll": win}, "2025": {"mae": win, "ll": win}})
    v_short = C.verdict({"2024": {"mae": win, "ll": win}, "2025": {"mae": win, "ll": short}})
    v_split = C.verdict({"2024": {"mae": win, "ll": lose}, "2025": {"mae": win, "ll": win}})
    v_one = C.verdict({"2024": {"mae": win, "ll": win}})
    v_nan = C.verdict({"2024": {"mae": win, "ll": win}, "2025": {"mae": win, "ll": mk(0.1, float("nan"))}})
    ck("C3e the registered rule's arithmetic: the paired SE is clustered by team (between-team variance of team "
       "sums, g/(g-1)); PASS only when BOTH seasons win BOTH scores by more than 2 SE -- a season at 1.9 SE, a "
       "split score, a missing season or an undefined SE is not established; a zero count reads (k + 0.5)/(n + 1)",
       abs(st["se"] - se) < 1e-12 and abs(st["mean"] - m) < 1e-12 and v_pass == "PASS" and v_short != "PASS"
       and v_split != "PASS" and v_one != "PASS" and v_nan != "PASS"
       and abs(C.prob(0, 4000) - 0.5 / 4001) < 1e-15 and C.ll(C.prob(4000, 4000), 1.0) > 0,
       f"se {st['se']} vs {se}; {v_pass} {v_short} {v_split} {v_one} {v_nan}")

    # ---- C3f: the committed inputs ------------------------------------------------------------------------------
    c24, c25 = inp["cfb"]["2024"], inp["cfb"]["2025"]
    slots = len(set(c24["cfp_field"])) + len(set(c25["cfp_field"]))
    ck("C3f the committed inputs: 24 CFP slots (12 a season) with each champion among them, nine title games a "
       "season flagged, committee rankings from week 11, 272 NFL games and 14 seeded playoff teams a season, and "
       "no FBS team without a prior",
       slots == 24 and c24["champion"] in c24["cfp_field"] and c25["champion"] in c25["cfp_field"]
       and all(sum(g["ccg"] for g in inp["cfb"][s]["games"]) == 9 for s in ("2024", "2025"))
       and all(min(r["week"] for r in inp["cfb"][s]["rankings"]) == 11 for s in ("2024", "2025"))
       and all(len(inp["nfl"][s]["games"]) == 272 and sum(v <= 7 for v in inp["nfl"][s]["seeds"].values()) == 14
               for s in ("2024", "2025"))
       and all(len(C.cfb_prior(inp["cfb"][s])) == len(inp["cfb"][s]["order"]) for s in ("2024", "2025")),
       f"slots {slots}")

    # ---- C3h: the 2.25 investigation's numbers, recomputed from their definition ------------------------------
    import research.c3_margin_scale as M
    ms = json.load(open(M.OUT))
    seed, games = M.setup()
    b225, _ = M.slope(seed, M.analytic(seed, games, 2.25))
    b_k, _ = M.slope(seed, M.analytic(seed, games, ms["analytic_k_for_slope_1"]))
    r = ms["readers"]
    ck("C3h the 2.25 investigation (research/c3_margin_scale.py): the analytic resolution's returned-wins slope "
       "at 2.25 and the points-per-win that returns slope 1 recompute exactly from their definition (exact "
       "expectations over 2025's schedule, the seed vector 2025's wins shrunk halfway); the committed figures "
       "say what the report says -- production's drive sim keeps the contract at 2.25, the analytic arm and the "
       "deep anchor over-spread it",
       abs(b225 - r["analytic_k2.25"]["slope"]) < 1e-4 and abs(b_k - 1.0) < 1e-3
       and abs(r["drive_k2.25"]["slope"] - 1.0) < 2.5 * r["drive_k2.25"]["se"]
       and r["analytic_k2.25"]["slope"] > 1.1 and r["deep_k2.25"]["slope"] - 1.0 > 4 * r["deep_k2.25"]["se"]
       and 0.97 < r["deep_k2.25"]["realized_margin_on_anchored"] < 1.03,
       f"analytic {b225} vs {r['analytic_k2.25']['slope']}; k {ms['analytic_k_for_slope_1']} -> {b_k}")

    # ---- C3i: C3-N, the registered follow-up on fresh games -------------------------------------------------------
    import hashlib
    import research.c3_nfl_rescaled as N
    import research_registry as RR
    reg = json.load(open(N.PREREG))
    shas = {p: hashlib.sha256(open(os.path.join(_ROOT, p), "rb").read()).hexdigest() for p in reg["sha256"]}
    ent = next((e for e in RR.REGISTRY if e["id"] == "c3_nfl_rescaled"), {})
    An = C.nfl_asof(inp["nfl"]["2025"], 10)
    pre = C.nfl_prep
    e1, e2 = N.sim(An, "engine", 6, 4), C.sim_nfl(An, "engine", 6, 4)
    rs = N.sim(An, "rescaled", 6, 4)
    want = 0.5 * (1 + math.erf(((10.0 - 8.0) * 1.842 + 2.2) / 13.2 / math.sqrt(2)))
    ck("C3i C3-N is registered before its data: prereg.json pins the read script, C3's machinery and the "
       "measurement its 1.842 came from by sha256 (never edit them), the Research tab lists it sealed until "
       "2028-01-12, the rescaled arm is Phi(((Ra - Rb) x 1.842 + 2.2 x host) / 13.2) and leaves the engine arm "
       "and C3's own analytic prep untouched, and C3's result is not superseded",
       all(shas[p] == v for p, v in reg["sha256"].items()) and N.K_NEW == reg["k_new"] == ms["analytic_k_for_slope_1"]
       and ent.get("registration") == "research/data/c3_nfl_rescaled/prereg.json"
       and RR.status(ent)["status"] == "sealed" and RR.status(ent)["not_before"] == N.NOT_BEFORE
       and abs(N.rescaled_prep(10.0, 8.0, 1) - want) < 1e-15 and C.nfl_prep is pre
       and e1["counts"] == e2["counts"] and rs["counts"] != e1["counts"] and rs["inputs"] == e1["inputs"]
       and min(N.WINDOWS[2026]) == 13 and os.path.exists(C.RESULT) and not os.path.exists(N.RESULT),
       f"{ {p: shas[p] == v for p, v in reg['sha256'].items()} } {ent.get('registration')}")

    # ---- C3g: the read and what production does with it ------------------------------------------------------------
    if os.path.exists(C.RESULT):
        r = json.load(open(C.RESULT))
        import hashlib
        same_inputs = r.get("inputs_sha256") == hashlib.sha256(open(C.INPUTS, "rb").read()).hexdigest()
        recomputed = {sp: C.verdict({s: {"mae": x["mae"], "ll": x["ll"]} for s, x in r["sports"][sp]["seasons"].items()})
                      for sp in ("nfl", "cfb")}
        stored = {sp: r["sports"][sp]["verdict"] for sp in ("nfl", "cfb")}
        cfb_src = inspect.getsource(cfb.run_season)
        nfl_src = inspect.getsource(nfl_season.run_season)
        prod_engine = {"cfb": "_play(rh, ra, rng)" in cfb_src and "_rates_for(R[h] + form[h]" in cfb_src,
                       "nfl": "_play(rh, ra, rng)" in nfl_src and "_drive_rates(eh)" in nfl_src}
        consistent = all((stored[sp] == "PASS") != prod_engine[sp] for sp in stored)
        import research_registry as RR
        entry = next(e for e in RR.REGISTRY if e["id"] == "acc_c3")
        ck("C3g the C3 read: the committed result was scored on the committed inputs, its verdicts are the rule's "
           "arithmetic on its own figures, production's season sims resolve games by the drive engine exactly "
           "where the verdict is not PASS, and the Research tab's C3 entry points at this result",
           same_inputs and recomputed == stored and consistent
           and entry.get("result") == os.path.relpath(C.RESULT, _ROOT) and RR.status(entry)["status"] == "read",
           f"inputs {same_inputs} {recomputed} vs {stored} engine {prod_engine} entry {entry.get('result')}")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
