"""Guards for the coaching engine's fifth revision (R5): nfl_coach_sim_r5 -- the
pull hazard with the trailing team's question, the second unit as drawn per-player
decisions -- and the owner's two checks on R4 and R3: the injury report is a
TRAINING LABEL only (never an input the simulator reads at play time), and R3's
random-stream offset leaves its registered endpoints untouched. Self-registered
(tests/guards_auto_*.py, the AUTO section); also runs alone:
python3 tests/guards_auto_nfl_coach_r5.py
Hermetic: no raw nflverse file is read. research/reports/nfl_coach_r5.md is the prose.
"""
import ast
import hashlib
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from research import registered_pins as REGP  # noqa: E402  (option (c), 2026-11-28)

PINNED = {
    "nfl_deep_sim.py": "22a221d7810495db3f8f7bb1bef7c4e16c3bbbf282902a95f133cecdabc29820",
    "nfl_deep_data.py": "dff6756fb9ceb5a746750557aaef3d8424a1b57e953ab6aef8cd0abd4375b022",
    "nfl_pbp.py": "660234badefc3e3b5f0e1417a48431e4ad8ff33aab5df8a207834faa4862865d",
    "research/data/nfl_deep_tables.json": "8c118b8fe741566ba1a0f7fb9aa8f41a27a5578b25e0d4988dda059c131b870f",
}
SUMS = (("car", "rush_att"), ("rush_yd", "rush_yds"), ("rush_td", "td_rush"), ("rec", "comp"),
        ("rec_yd", "pass_yds"), ("rec_td", "td_pass"), ("pass_att", "pass_att"), ("pass_cmp", "comp"),
        ("pass_yd", "pass_yds"), ("pass_td", "td_pass"), ("int", "int"), ("sacks", "sacks"),
        ("kneels", "kneels"), ("fum", "fum_lost"))
ENGINE_IMPORTS = {"json", "math", "os", "random", "nfl_coach_sim", "nfl_coach_sim_r2", "nfl_coach_sim_r3",
                  "nfl_coach_sim_r4", "nfl_deep_sim"}
# nothing a play-time input could be built from: the report files, the play descriptions, a season or week
FORBIDDEN = (".csv", ".gz", "injuries", "report_status", "desc", "play_id", "season", "week", "gsis")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _roster(t, backup=True):
    out = [dict(id=t + "QB", pos="QB", car=3.0, tgt=0, ypc=5.0)]
    if backup:
        out.append(dict(id=t + "QB2", pos="QB", car=0.0, tgt=0, ypc=3.5))
    return out + [dict(id=t + "RB1", pos="RB", car=15.0, tgt=3.5, ypc=4.3, ypr=7.5, cr=0.78),
                  dict(id=t + "RB2", pos="RB", car=6.0, tgt=2.0, ypc=4.1, ypr=7.0, cr=0.76),
                  dict(id=t + "WR1", pos="WR", car=0.4, tgt=8.5, ypc=7.0, ypr=13.0, cr=0.64),
                  dict(id=t + "WR2", pos="WR", car=0.0, tgt=6.0, ypr=12.5, cr=0.62),
                  dict(id=t + "WR3", pos="WR", car=0.0, tgt=4.0, ypr=12.0, cr=0.6),
                  dict(id=t + "TE1", pos="TE", car=0.0, tgt=5.0, ypr=10.0, cr=0.7)]


def static_inputs(rel):
    """What an engine module could read at play time: its imports, where it opens
    a file, the string constants outside docstrings, and the keys Tables reads."""
    tree = ast.parse(open(os.path.join(ROOT, rel)).read())
    imports, opens, consts, keys = set(), [], [], set()
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body and isinstance(
                node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            docs.add(id(node.body[0].value))
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                    opens.append(fn.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            consts.append(node.value)
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == "Tables":
            for node in ast.walk(cls):
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and \
                        isinstance(node.slice.value, str):
                    keys.add(node.slice.value)
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get" and node.args and \
                        isinstance(node.args[0], ast.Constant):
                    keys.add(node.args[0].value)
    return imports, opens, consts, keys


def first_states(log, pts=(0, 0)):
    """From the log alone (R3's post-game parser): per identified drive in order,
    (side, drive, margin at its first snap, game seconds at its first snap)."""
    import nfl_coach_sim_r3 as E3
    sn = E3.snaps(log, pts)
    first, ident, order = {}, set(), []
    for s in sn:
        side, kind, d = s[0], s[1], s[6]
        if d not in first:
            first[d] = (side, s[9], E3.game_seconds(s[7], s[8]))
        if kind in E3._IDENT and d not in ident:
            ident.add(d)
            order.append((first[d][0], d, first[d][1], first[d][2]))
    return order


def run(ck):
    import nfl_coach_sim as C
    import nfl_coach_sim_r2 as E2
    import nfl_coach_sim_r3 as E3
    import nfl_coach_sim_r4 as E4
    import nfl_coach_sim_r5 as E5
    from research import nfl_coach_r5_read as RR

    print("  -- N5. The coaching engine R5 (the trailing pull, the second unit drawn), R4's inputs traced, R3's "
          "stream offset checked, the registration")
    for rel, want in PINNED.items():
        ck(f"N5-0 {rel} is byte-identical to the file the deep weight's read sits on (or, once that read is committed, exactly a staged flip's bytes)", REGP.live_ok(rel, want))
    moved = []
    for reg in ("prereg_r1.json", "prereg_r2.json", "prereg_r3.json", "prereg_r4.json"):
        moved += REGP.moved("research/data/nfl_coach/" + reg)
    ck("N5-0a [option (c): pins checked at the registration commit; live, only research/registered_pins.py's named exceptions] every file R1's to R4's registrations pin is byte-identical (R5 is new files)", not moved, moved)
    src = open(os.path.join(ROOT, "nfl_coach_sim_r5.py")).read()
    ck("N5-0b R5 plays R1's own snaps, writes no predlog bucket, and no production module imports it",
       "def _snap" not in src and "C._snap(S, box, sides, T, rng)" in src and "log_shadow" not in src
       and not any("nfl_coach_sim_r5" in open(os.path.join(ROOT, f)).read()
                   for f in os.listdir(ROOT) if f.endswith(".py") and not f.startswith("nfl_coach")))

    # ---- T: the injury report is a training label, never a play-time input (R4 and R5)
    for rel, allowed_keys in (("nfl_coach_sim_r4.py", {"effects", "type_coef", "returns", "rate", "drives_out"}),
                              ("nfl_coach_sim_r5.py", {"coef", "spec", "second_unit", "era_from", "q_b", "q_w0",
                                                       "q_w1", "d_b", "d_w"})):
        imp, opens, consts, keys = static_inputs(rel)
        bad = [c for c in consts for f in FORBIDDEN if f in c.lower()]
        ck(f"N5-T1 {rel}: imports only the engines and the standard library (no research module, no "
           "loader), opens a file only in tables(), names no report, description, season or week, and its "
           "Tables reads only the fitted values",
           imp <= ENGINE_IMPORTS and set(opens) <= {"tables"} and not bad and keys <= allowed_keys,
           (sorted(imp - ENGINE_IMPORTS), opens, bad[:3], sorted(keys - allowed_keys)))
    raw4 = open(E4.TABLES_PATH).read()
    ck("N5-T2 R4's tables hold aggregates only: no player id, game id or play text (nothing a game could be "
       "looked up by)", "00-0" not in raw4 and "_20" not in raw4.replace("_2016", "") and "injured" not in raw4)
    lg = C.league_profile()
    rec = {"haz": [], "inj": []}
    h0, p0 = E3.hazard, E4.p_injury

    def _h(coef, sd, gsr):
        rec["haz"].append((sd, gsr))
        return h0(coef, sd, gsr)

    def _p(X, sd, gsr):
        rec["inj"].append((sd, gsr))
        return p0(X, sd, gsr)
    ok, n_pull = True, 0
    E3.hazard, E4.p_injury = _h, _p
    try:
        for seed in range(30):
            rec["haz"].clear()
            rec["inj"].clear()
            st = {"per": 3, "clk": 300, "pts": (21 if seed % 2 else 0, 0 if seed % 2 else 21), "off": seed % 2,
                  "down": 1, "dist": 10, "ytg": 75}
            r = E4.play_game(lg, lg, random.Random(seed), random.Random(seed + 99), start=st)
            order = first_states(r["log"], st["pts"])
            want_h = [(sd, g) for side, d, sd, g in order
                      if r["qb"][side]["pull"] is None or d <= r["qb"][side]["pull"]]
            want_i = [(sd, g) for side, d, sd, g in order if r["qb"][side]["pull"] == d]
            ok &= rec["haz"] == want_h and rec["inj"] == want_i
            n_pull += len(want_i)
    finally:
        E3.hazard, E4.p_injury = h0, p0
    ck("N5-T3 R4 at play time (30 games, %d pulls): the hazard and the kind of pull read exactly the margin and "
       "the game seconds at each identified drive's FIRST snap, as R3's post-game parser recovers them from "
       "the log, and nothing else -- the kind is drawn from the state's logistic, never a report" % n_pull,
       ok and n_pull > 5)

    # ---- the hazard's features
    grid = [(sd, g) for sd in (-35, -24, -17, -9, -3, 0, 3, 9, 17, 24, 35) for g in (0, 45, 300, 900, 1800, 3600)]
    ck("N5-1 s6 is R3's form exactly; p1-p4 nest on it; a regime term is zero before its change point",
       all(E5.features(sd, g, 3, "s6") == E3.features(sd, g) for sd, g in grid)
       and all(E5.features(sd, g, 2, b)[:len(E5.features(sd, g, 2, a))] == E5.features(sd, g, 2, a)
               for a, b in zip(E5.SPECS, E5.SPECS[1:]) for sd, g in grid)
       and all(E5.features(sd, g, 3, sp, 0.0, 0.0)[10:] == [0.0] * (len(E5.features(sd, g, 3, sp)) - 10)
               for sp in ("s6e0", "s6e1", "s6e2", "s6e3", "s6e4", "s6e5") for sd, g in grid))
    import math
    f = E5.features(-17, 300, 1, "p2")
    ck("N5-1a the possession term by hand: trailing 17 at 5:00 needs 3 scores in (300+30)/327 possessions; "
       "with one timeout the timeout terms are 1/3 and 1/3 x the need term",
       abs(f[10] - math.log1p(3 / (330 / 327.0))) < 1e-12 and abs(f[12] - 1 / 3) < 1e-12
       and abs(f[13] - f[10] / 3) < 1e-12)
    P = E5.tables()
    fitj = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", "r5_fit.json")))
    want_spec = fitj["era"]["chosen"]["spec"] if fitj["era"]["adopted"] else fitj["spec"]
    ck("N5-1b the tables are the fit's: the hazard spec the rule (or the regime search, only where its BIC beat "
       "R3's form) chose, its coefficients' length, the second unit's maximum-likelihood values",
       P.spec == want_spec and len(P.coef) == len(E5.features(-3, 600, 3, P.spec))
       and P.raw["second_unit"] == fitj["second_unit"]["fit"]["params"]
       and 0 < P.q_w0 < P.q_w1 < 1 and 0 < P.q_b < 1, (P.spec, want_spec))

    # ---- parity and conservation
    same = True
    for seed in range(12):
        rng1, rng2 = random.Random(seed), random.Random(seed)
        pl = (_roster("H"), _roster("A"))
        a = E4.play_game(lg, lg, rng1, random.Random(5), players=pl, pull=False,
                         deal_rngs=(random.Random(1), random.Random(2)))
        b = E5.play_game(lg, lg, rng2, random.Random(5), players=pl, pull=False,
                         deal_rngs=(random.Random(1), random.Random(2)))
        same &= a["h"] == b["h"] and a["a"] == b["a"] and a["log"] == b["log"] and a["lines"] == b["lines"] \
            and rng1.random() == rng2.random()
    a = E4.simulate(lg, lg, (_roster("H"), _roster("A")), n=150, seed=3, pull=False)
    b = E5.simulate(lg, lg, (_roster("H"), _roster("A")), n=150, seed=3, pull=False)
    ck("N5-2 no pull, no change: R5's game is R4's (and R1's) draw for draw, its log and its lines R4's "
       "(12 dealt games; 150 simulated, every line)", same and a["lines"] == b["lines"])
    U = E2.tables()
    team = E2.TeamDeal(_roster("H"), U)
    r = E4.play_game(lg, lg, random.Random(4), random.Random(4))
    sn = [s for s in E3.snaps(r["log"]) if s[0] == 0]
    X = E4.tables()
    eq = True
    for q in ({"pull": None, "back": None, "kind": None}, {"pull": 3, "back": None, "kind": "injury"},
              {"pull": 3, "back": 6, "kind": "injury"}):
        eq &= E5.deal(sn, r["h"], team, random.Random(9), "HQB2", q, X, U, P) == \
            E4.deal(sn, r["h"], team, random.Random(9), "HQB2", q, X, U)
    q = {"pull": 3, "back": None, "kind": "strategic"}
    eq &= E5.deal(sn, r["h"], team, random.Random(9), "HQB2", q, X, U, None) == \
        E4.deal(sn, r["h"], team, random.Random(9), "HQB2", q, X, U)
    ck("N5-3 only a strategic pull changes the deal: no pull, an injury pull (with and without the return), "
       "and a strategic pull with R5's second unit off are R4's lines exactly", eq)
    bad = []
    big = E5.simulate(lg, lg, (_roster("H"), _roster("A", False)), n=1200, seed=11,
                      start={"per": 3, "clk": 900, "pts": (21, 0), "off": 0, "down": 1, "dist": 10, "ytg": 75})
    n_str = sum(1 for g in big["qb"] for q in g if q["kind"] == "strategic")
    for res, lines in zip(big["games"], big["lines"]):
        for key, L in (("h", lines[0]), ("a", lines[1])):
            for mine, theirs in SUMS:
                if sum(v[mine] for v in L.values()) != res[key][theirs]:
                    bad.append((key, mine))
    ck("N5-4 conservation with the second unit drawn: every carry, catch, yard, touchdown, sack, kneel and lost "
       "fumble sums to the box (1,200 games from 21-0 at the half, %d strategic pulls)" % n_str,
       not bad and n_str > 100, bad[:3])

    # ---- timeouts and the hazard's inputs (R5)
    ok_to, ok_in, n_in = True, True, 0
    for seed in range(25):
        r = E5.play_game(lg, lg, random.Random(seed), random.Random(seed + 3))
        # an independent recount: three a half (two in a regular-season overtime) less the side's logged
        # timeouts so far in that half, at each snap event
        used, half, at_snap = [0, 0], None, []
        for e in r["log"]:
            per = e["q"]
            hh = 1 if per <= 2 else 2 if per <= 4 else 3
            if hh != half:
                half, used = hh, [0, 0]
            if e["k"] == "tmo":
                used[e["o"]] += 1
            if e["k"] in E2._SNAPS and "o" in e:
                base = 2 if hh == 3 else 3
                at_snap.append(base - used[e["o"]])
        sn = E3.snaps(r["log"])
        ok_to &= len(sn) == len(at_snap)
        first_idx = {}
        for j, s in enumerate(sn):
            first_idx.setdefault(s[6], j)
        order = first_states(r["log"])
        want = [(side, d, sd, g) for side, d, sd, g in order
                if r["qb"][side]["pull"] is None or d <= r["qb"][side]["pull"]]
        got = [(o, d, sd, g) for o, d, sd, g, _to in r["inputs"]]
        ok_in &= got == want
        ok_to &= all(to == at_snap[first_idx[d]] for _o, d, _sd, _g, to in r["inputs"])
        n_in += len(got)
    ck("N5-5 R5's hazard reads, at each identified drive, the margin and game seconds at its first snap (R3's "
       "parser on the log) and the offence's timeouts then -- recounted independently from the logged timeouts "
       "(three a half, two in a regular-season overtime): %d reads over 25 games" % n_in, ok_in and ok_to and n_in > 200)

    # ---- the second unit as drawn decisions
    drng = random.Random(77)
    s0 = drng.getstate()
    E5._sit_rng(drng)
    ck("N5-6 the sit decisions draw on their own generator, seeded from the deal generator's state without "
       "consuming it", drng.getstate() == s0)
    srng = random.Random(5)
    n = 20000
    cnt = {"b": 0, "w_b": 0, "nb": 0, "w_nb": 0, "b_wplay": 0, "nb_wsit": 0}
    for _ in range(n):
        d = E5.sit_decisions(team, P, srng)
        b = d["HRB1"]
        ws = [d["HWR1"], d["HWR2"]]
        cnt["b"] += b
        if b:
            cnt["w_b"] += sum(ws)
            cnt["b_wplay"] += not any(ws)
        else:
            cnt["nb"] += 1
            cnt["w_nb"] += sum(ws)
            cnt["nb_wsit"] += any(ws)
    pb = cnt["b"] / n
    pw1 = cnt["w_b"] / (2 * cnt["b"])
    pw0 = cnt["w_nb"] / (2 * cnt["nb"])
    se = lambda p, m: math.sqrt(p * (1 - p) / m)     # noqa: E731
    ck("N5-6a per-player decisions at the fitted rates (20,000 pulls): the back sits at q_b, each receiver at "
       "q_w1 after the back sat and q_w0 after he played -- and neither 'all together' nor 'never apart': a "
       "seated back with both receivers playing, and a playing back with a seated receiver, both occur",
       abs(pb - P.q_b) < 4 * se(P.q_b, n) and abs(pw1 - P.q_w1) < 4 * se(P.q_w1, 2 * cnt["b"])
       and abs(pw0 - P.q_w0) < 4 * se(P.q_w0, 2 * cnt["nb"]) and cnt["b_wplay"] > 0 and cnt["nb_wsit"] > 0,
       (pb, pw1, pw0))
    seat_ok, seen_sat, seen_play = True, 0, 0
    for seed in range(400):
        rr = E5.play_game(lg, lg, random.Random(seed), random.Random(seed),
                          start={"per": 3, "clk": 900, "pts": (28, 0), "off": 0, "down": 1, "dist": 10, "ytg": 75})
        q = rr["qb"][0]
        if q["kind"] != "strategic":
            continue
        s_ = [s for s in E3.snaps(rr["log"], (28, 0)) if s[0] == 0]
        dr = random.Random(seed)
        sits = E5.sit_decisions(team, P, E5._sit_rng(dr))
        full = E5.deal(s_, rr["h"], team, dr, "HQB2", q, X, U, P)
        pre = [s for s in s_ if s[6] < q["pull"]]
        part = E4.deal(pre, {"rush_yds": 0, "pass_yds": 0, "fum_lost": sum(s[5] for s in pre)}, team,
                       random.Random(seed), "HQB2", {"pull": None, "back": None, "kind": None}, X, U)
        for who, stat in (("HRB1", "car"), ("HWR1", "tgt"), ("HWR2", "tgt")):
            after = full[who][stat] - part[who][stat]
            if sits[who]:
                seen_sat += 1
                seat_ok &= after == 0
            else:
                seen_play += after > 0
    ck("N5-6b a seated player gets no carry or target after the strategic pull; players who play still do "
       "(400 games from 28-0 at the half, the deal less its pre-pull prefix)", seat_ok and seen_sat > 20
       and seen_play > 20, (seen_sat, seen_play))

    # ---- R3's random-stream offset: the registered endpoints never read the deal's draws
    r = E4.play_game(lg, lg, random.Random(12), random.Random(12), pull=False)
    sn = [s for s in E3.snaps(r["log"]) if s[0] == 0]
    team3 = E2.TeamDeal(_roster("H"), U)
    ln = [E3.deal(sn, r["h"], team3, random.Random(k), "HQB2", 5, U) for k in range(6)]
    keyf = [(x["HQB"]["pass_att"] + x["HQB"]["sacks"], x["HQB"]["pass_yd"], x["HQB2"]["pass_att"]) for x in ln]
    sc = json.load(open(os.path.join(ROOT, "research", "data", "nfl_coach", "r3_seed_check.json")))
    ck("N5-7 R3's deal assigns the quarterback's dropbacks and passing yards without a draw (six deal "
       "generators, one game: equal), so the offset seed cannot move R3's criteria 3 and 4; the replay "
       "(r3_seed_check.json) found every one of its draws' endpoints identical",
       len(set(keyf)) == 1 and sc["draws_endpoints_identical"] == sc["draws_compared"] > 100000
       and sc["endpoints"]["dropbacks_registered_c3"]["one_read"]["sd_ratio"] == 1.0)

    # ---- registration
    reg = RR.prereg()
    body = json.dumps({k: v for k, v in reg.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    ck("N5-8 the registration: its hash holds; the 2028 regular season (no registered window or development set "
       "holds it), on/after 2029-01-16 after R1's and R4's reads; the dependence stated",
       hashlib.sha256(body.encode()).hexdigest() == reg["block_sha256"]
       and reg["window"] == [{"season": 2028, "weeks": list(range(1, 19))}]
       and reg["not_before_utc"] == "2029-01-16T06:00:00Z" and reg["requires_reads"] == ["R1", "R4"]
       and all(x in reg["dependence"] for x in ("R1", "R4", "2027", "2028")))
    moved = REGP.moved("research/data/nfl_coach/prereg_r5.json")
    ck("N5-8b [option (c): pins checked at the registration commit; live, only research/registered_pins.py's named exceptions] every file the registration pins matches its sha256", not moved
       and "tests/guards_auto_nfl_coach_r5.py" in reg["files"] and "nfl_coach_sim_r4.py" in reg["files"], moved)
    # option (c), 2026-11-28: the read runs from its registration commit (research/reports/registered_reads_runbook.md),
    # so whether it WOULD run is asked of that commit's tree; the live tree may hold a staged fix.
    with REGP.registered_view("research/data/nfl_coach/prereg_r5.json", RR):
        w = [RR.refusal(now_utc="2028-06-01T00:00:00Z", dirty=False, reg=reg, earlier_read={"R1": 1, "R4": 1},
                        already=False),
             RR.refusal(now_utc="2029-02-01T00:00:00Z", dirty=True, reg=reg, earlier_read={"R1": 1, "R4": 1},
                        already=False),
             RR.refusal(now_utc="2029-02-01T00:00:00Z", dirty=False, reg=reg, earlier_read={"R1": 1}, already=False),
             RR.refusal(now_utc="2029-02-01T00:00:00Z", dirty=False, reg=reg, earlier_read={"R1": 1, "R4": 1},
                        already=True),
             RR.refusal(now_utc="2029-02-01T00:00:00Z", dirty=False, reg=reg, earlier_read={"R1": 1, "R4": 1},
                        already=False)]
    ck("N5-9 [would-run asked of the registration commit's tree, option (c)] the read refuses before its date, a dirty tree, before R4's read and a second read, and would run "
       "after", "before" in (w[0] or "") and "dirty" in (w[1] or "") and "R4" in (w[2] or "")
       and "already" in (w[3] or "") and w[4] is None, w)
    good = {"guards_exit": 0, "unit": {"gain_per_pulled_team_game": 0.9, "se": 0.2},
            "hazard": {"gain_per_team_game": 0.0, "se": 0.003}, "cell": {"pulls": 10, "r4_expected": 4.0,
                                                                       "r5_expected": 6.0}}
    weak = json.loads(json.dumps(good))
    weak["unit"]["gain_per_pulled_team_game"] = 0.3
    haz = json.loads(json.dumps(good))
    haz["hazard"]["gain_per_team_game"] = -0.007
    corner = json.loads(json.dumps(good))
    corner["cell"]["pulls"] = 6
    ck("N5-10 the verdict is the registered rule: all hold -> PASS; the second unit under 2 SE or the hazard "
       "worse by more than 2 SE -> FAIL; the corner is a secondary that never decides (10 pulls at 6.0 "
       "expected: p 0.084, not persisting; 14 would be)",
       RR.verdict(good)["pass"] and not RR.verdict(weak)["pass"] and not RR.verdict(haz)["pass"]
       and RR.verdict(corner)["pass"] and not RR.verdict(good)["secondary"]["corner_excess_persists"]
       and abs(RR.corner_p(10, 6.0) - 0.0839) < 0.001 and RR.corner_p(14, 6.0) < 0.05)
    regy = json.load(open(os.path.join(ROOT, "research", "data", "research_registry.json")))
    listed = {e["registration"] for e in regy}
    ck("N5-11 R5's registration is on the Research tab's registry", "research/data/nfl_coach/prereg_r5.json" in listed)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def _ck(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail and not cond else ""))
    run(_ck)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
