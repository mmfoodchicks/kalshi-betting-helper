"""Guards for the DFS player picker (2026-10-30: nfl_dfs.pick_parse / pick_resolve and the sheet's search,
dfs_tourney's picked boards, the Build queue in app.py, pc_worker._picked_builds, static/dfspick.js; designed
and reviewed with a Fable agent, research/reports/dfs_picker.md): exclude / force in / force captain on the
owner's own builds, the automatic builds untouched, the FIELD never picked, every listed lineup holding the
pick, refusals that name their rule. Hermetic: DraftKings, Sleeper's pool and the depth gate are stubs, the
boards and the queue live in a temporary directory; nothing is fetched.

    python3 tests/guards_picker.py       # exits 1 on any failed check
    from tests.guards_picker import run  # run(ck) inside the main suite
"""
import contextlib
import inspect
import itertools
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)

_FIRST = ["Ace", "Bo", "Cal", "Dax", "Eli", "Finn", "Gus", "Hal", "Ike", "Jax", "Kip", "Lou", "Max", "Ned"]
_WORD = {"AAA": "Hawk", "BBB": "Bear", "CCC": "Lion", "DDD": "Wolf", "EEE": "Colt", "FFF": "Ram", "GGG": "Jet",
         "HHH": "Owl"}


@contextlib.contextmanager
def _sandbox():
    import boardshare
    tmp = tempfile.mkdtemp()
    saved = boardshare._DIR
    try:
        boardshare._DIR = os.path.join(tmp, "boards")
        os.makedirs(boardshare._DIR, exist_ok=True)
        yield tmp
    finally:
        boardshare._DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def _patched(*triples):
    old = [(o, n, getattr(o, n)) for o, n, _v in triples]
    try:
        for o, n, v in triples:
            setattr(o, n, v)
        yield
    finally:
        for o, n, v in reversed(old):
            setattr(o, n, v)


def _safe(fn, *a, **k):
    """fn's answer, or {"error": ...} when it raises: a mutant that breaks a build must fail a named check, never
    stop the file."""
    try:
        return fn(*a, **k)
    except Exception as e:
        import traceback
        return {"error": f"raised {type(e).__name__}: {e}", "tb": traceback.format_exc()[-400:]}


def _guarded(ck, label, fn, *a):
    try:
        fn(*a)
    except Exception as e:
        import traceback
        ck(label + " (the checks raised)", False, f"{type(e).__name__}: {e} " + traceback.format_exc()[-500:])


def _row(pos, name, pid, rp, sal, game, team, proj, status="ACTIVE"):
    return f"{pos},{name} ({pid}),{name},{pid},{rp},{sal},{game},{team},{proj},{status}"


_HEAD = "Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame,Status"


def _classic_slate(n_games=4, seed=42):
    """(csv, {name: {pos, team, proj}}, {name: depth why}) -- a synthetic classic slate whose names stay distinct
    under nfl_adp._norm (it drops digits: 'QB1 AAA' and 'QB2 AAA' were one man to _pool_match)."""
    g = random.Random(seed)
    teams = list(_WORD)[:2 * n_games]
    rows, pool, gated, pid = [], {}, {}, 1000
    for gi in range(n_games):
        away, home = teams[2 * gi], teams[2 * gi + 1]
        game = f"{away}@{home} 11/01/2026 01:00PM ET"
        for tm in (away, home):
            k = 0
            for pos, n, lo, hi in (("QB", 2, 5200, 7600), ("RB", 3, 4000, 8600), ("WR", 5, 3000, 8800),
                                   ("TE", 2, 2600, 6400), ("DST", 1, 2400, 3600)):
                for j in range(n):
                    nm = f"{_FIRST[k]} {_WORD[tm]}" if pos != "DST" else f"{_WORD[tm]}s"
                    k += 1
                    sal = int(g.uniform(lo, hi) // 100 * 100)
                    deep = (pos in ("QB", "TE") and j >= 1) or (pos == "RB" and j >= 2) or (pos == "WR" and j >= 3)
                    proj = round(max(0.5, sal / 420 + g.uniform(-3, 3) - (4 if deep else 0)), 1)
                    rp = pos if pos in ("QB", "TE", "DST") else f"{pos}/FLEX"
                    rows.append(_row(pos, nm, pid, rp, sal, game, tm, proj))
                    pool[nm] = {"pos": pos, "team": tm, "proj": proj}
                    if deep:
                        keep = 1 if pos in ("QB", "TE") else 2 if pos == "RB" else 3
                        gated[nm] = f"{pos}{j + 1} by depth chart (keep {pos}1{'-' + str(keep) if keep > 1 else ''})"
                    pid += 1
    return _HEAD + "\n" + "\n".join(rows), pool, gated


def _showdown_slate(seed=9):
    g = random.Random(seed)
    game = "AAA@BBB 11/02/2026 08:15PM ET"
    rows, pool, gated, pid = [], {}, {}, 5000
    for tm in ("AAA", "BBB"):
        k = 0
        for pos, n, lo, hi in (("QB", 2, 8000, 12000), ("RB", 3, 3000, 9000), ("WR", 5, 1000, 10500),
                               ("TE", 2, 1200, 6000), ("K", 1, 3800, 4400), ("DST", 1, 3200, 4200)):
            for j in range(n):
                nm = f"{_FIRST[k]} {_WORD[tm]}" if pos != "DST" else f"{_WORD[tm]}s"
                k += 1
                sal = 4000 if (pos == "QB" and j) else int(g.uniform(lo, hi) // 200 * 200)
                proj = round(max(0.4, sal / 600 + g.uniform(-3, 3) - (6 if (pos == "QB" and j) else 0)), 1)
                rows.append(_row(pos, nm, pid, "CPT", int(sal * 1.5), game, tm, proj))
                rows.append(_row(pos, nm, pid + 1, "FLEX", sal, game, tm, proj))
                pool[nm] = {"pos": pos, "team": tm, "proj": proj}
                if (pos == "QB" and j >= 1) or (pos == "WR" and j >= 4) or (pos == "RB" and j >= 2):
                    gated[nm] = f"{pos}{j + 1} by depth chart (keep {pos}1)"
                pid += 2
    return _HEAD + "\n" + "\n".join(rows), pool, gated


def _fake_pool(pool, n):
    """Sleeper's pool, stubbed: correlated arrays, pure Python (CI's runner has no numpy)."""
    rng = random.Random(1234)
    teams = sorted({p["team"] for p in pool.values()})
    lat = {tm: [rng.gauss(0, 1) for _ in range(n)] for tm in teams}
    out = {}
    for nm, p in sorted(pool.items()):
        sd = 0.55 * p["proj"] + 1.5
        a = [max(0.0, p["proj"] + sd * (0.6 * z + 0.8 * rng.gauss(0, 1))) for z in lat[p["team"]]]
        s = sorted(a)
        out[nm] = {"pos": p["pos"], "team": p["team"], "proj": p["proj"], "ceiling": s[int(0.9 * (n - 1))],
                   "floor": s[int(0.1 * (n - 1))], "arr": a, "rec_tgt": 5.0}
    return out


def _fake_depth(gated):
    def depth(players, preseason, seconds_to_lock=None):
        kept, dx = [], []
        for p in players:
            if p["name"] in gated:
                dx.append({"name": p["name"], "pos": p["pos"], "team": p.get("team"), "why": gated[p["name"]]})
            else:
                p["depth"] = f"{p['pos']}1"
                kept.append(p)
        return kept, dx
    return depth


def _fixture_pool6():
    """The guard suite's own optimizer pool (tests/combo_audit_guards.py, the sheet's exactness check)."""
    import nfl_dfs as N
    g = random.Random(20261016)
    pool = []
    for tm, op in (("AAA", "BBB"), ("BBB", "AAA"), ("CCC", "DDD"), ("DDD", "CCC")):
        if tm in ("AAA", "BBB"):
            pool.append({"name": f"QB {tm}", "pos": "QB", "team": tm, "opp": op, "salary": g.choice([6000, 6400, 7000])})
        else:
            pool.append({"name": f"DST {tm}", "pos": "DST", "team": tm, "opp": op, "salary": g.choice([2600, 3000, 3400])})
        if tm != "DDD":
            pool.append({"name": f"TE {tm}", "pos": "TE", "team": tm, "opp": op, "salary": g.choice([3000, 4200, 5000])})
            pool.append({"name": f"RB {tm}", "pos": "RB", "team": tm, "opp": op, "salary": g.choice([5000, 6200, 7800])})
        for k in range(4):
            pool.append({"name": f"WR{k} {tm}", "pos": "WR", "team": tm, "opp": op,
                         "salary": g.choice([3500, 4500, 5500, 6500, 7500, 8500])})
    for p in pool:
        p["proj"] = round(p["salary"] / 400.0 + g.uniform(-2, 2), 1)
        p["ceiling"] = round(p["proj"] * g.uniform(1.3, 1.8), 1)
        p["floor"] = round(p["proj"] * 0.5, 1)
        p["elig"] = N._elig(p["pos"])
    pool.append({"name": "Boom Wr CCC", "pos": "WR", "team": "CCC", "opp": "DDD", "salary": 3500,
                 "proj": 1.0, "ceiling": 40.0, "floor": 0.0, "elig": N._elig("WR")})
    return pool


def _fill_checks(N):
    """The picked fill's three mechanisms, read off nfl_dfs._build_one and optimize directly (the pool's own
    _by_pos and salary order, as optimize builds them): {"recount": (exact, brute, got), "seat": (all held, held,
    returned, cases), "reserve": [fills of 400 completed, per pick]}."""
    pool = _fixture_pool6()
    N._set_ownership(pool)
    names = [p["name"] for p in pool]
    real = lambda lu, obj: sum(N._value({k: v for k, v in p.items() if k != "_pk"}, obj) for p in lu)

    def prep(avail, must):
        fx = [dict(p, _pk=True) for p in avail if p["name"] in must]
        ours = [p for p in avail if p["name"] not in must] + fx
        by_pos = N._by_pos(ours)
        return fx, ours, by_pos, {pos: sorted(v, key=lambda p: (p["salary"], p["name"])) for pos, v in by_pos.items()}
    out = {}
    # an opponent's receiver forced as the bring-back, the quarterback left to the builder, every other catcher of
    # that team (and its quarterback) out of the pool
    avail = [p for p in pool if p["name"] not in ("QB BBB", "TE BBB", "WR1 BBB", "WR2 BBB", "WR3 BBB")]
    ex = _brute_classic(N, avail, "ceiling", 2, 1, ("WR0 BBB",))
    fx, ours, _bp, _asc = prep(avail, ("WR0 BBB",))
    lu = N.optimize(ours, 50000, "ceiling", stack_min=2, bring_min=1, fixed=fx)
    got = real(lu, "ceiling") if lu and "WR0 BBB" in {p["name"] for p in lu} else None
    out["recount"] = (ex is not None and got is not None and abs(got - ex) < 1e-6, ex, got)
    # the cheapest start on seeded random feasible picks
    g = random.Random(7)
    held = returned = cases = 0
    for _ in range(150):
        obj, sm, bm = g.choice([("projection", 1, 0), ("ceiling", 2, 1), ("leverage", 2, 1)])
        must = tuple(g.sample(names, g.choice([1, 2, 2, 3, 3, 4])))
        exc = tuple(g.sample([x for x in names if x not in must], g.choice([0, 0, 1, 2, 3])))
        avail = [p for p in pool if p["name"] not in exc]
        if _brute_classic(N, avail, obj, sm, bm, must) is None:
            continue
        fx, ours, by_pos, asc = prep(avail, must)
        fq = [p["team"] for p in fx if p["pos"] == "QB"]
        for tm in fq or sorted({p["team"] for p in ours if p["pos"] == "QB"}):
            cases += 1
            r = N._build_one(by_pos, 50000, obj, stack_team=tm, stack_min=sm, rng=random.Random(1), bring_min=bm,
                             fixed=fx, cheapest=True, asc=asc)
            if r:
                returned += 1
                held += set(must) <= {p["name"] for p in r[0]}
    out["seat"] = (returned >= 100 and held == returned, held, returned, cases)
    # the reserve's own contract: at a cap of exactly the forced salaries plus the open slots' cheapest fill
    # (_reserve's floor), stack and bring-back off so only the slot loop and the reserve act, random picked fills
    # still complete -- the money for the open slots is held back (round-2 review's pin: mid-range counts at a
    # loose cap moved with any edit to the fill)
    out["reserve"] = []
    for must in (("QB AAA", "RB AAA", "WR3 BBB"), ("RB AAA", "RB BBB", "TE AAA"), ("WR0 AAA", "WR1 AAA")):
        fx, ours, by_pos, asc = prep(pool, must)
        open_slots = list(N.ROSTER)
        for p in sorted(fx, key=lambda x: x["pos"] in N.FLEX_OK):
            open_slots.remove(p["pos"] if p["pos"] in open_slots else "FLEX")
        floor = sum(p["salary"] for p in fx) + N._reserve(asc, open_slots, {p["name"] for p in fx})
        rng = random.Random(3)
        out["reserve"].append(sum(1 for _ in range(400) if N._build_one(
            by_pos, floor, "ceiling", stack_team=None, stack_min=0, rng=rng, bring_min=0, fixed=fx, asc=asc)))
    return out


def _brute_classic(N, players, objective, sm, bm, must=()):
    best = None
    bp = {}
    for p in players:
        bp.setdefault(p["pos"], []).append(p)
    for qb, dst, te in itertools.product(bp["QB"], bp["DST"], bp["TE"]):
        for nrb in (2, 3):
            for rbs in itertools.combinations(bp["RB"], nrb):
                for wrs in itertools.combinations(bp["WR"], 6 - nrb):
                    lu = [qb, dst, te, *rbs, *wrs]
                    if sum(p["salary"] for p in lu) > 50000 or (must and not set(must) <= {p["name"] for p in lu}):
                        continue
                    if N._lineup_ok(lu, 50000, sm, bm):
                        v = sum(N._value(p, objective) for p in lu)
                        best = v if best is None else max(best, v)
    return best


def _sd_sheet_pool(N):
    """The showdown sheet's guard game: 24 men on two teams, pure Python (research/dfs_picker_stress.py plays its
    random picks on it too)."""
    sd = []
    gs = random.Random(5)
    for tm in ("SEA", "NE"):
        for pos, k, lo, hi in (("QB", 1, 10000, 12000), ("RB", 2, 4000, 9000), ("WR", 4, 2000, 10500),
                               ("TE", 2, 1200, 6000), ("K", 1, 3800, 4400), ("DST", 1, 3200, 4200)):
            for j in range(k):
                sal = int(gs.uniform(lo, hi) // 200 * 200)
                p = {"name": f"{pos}{j} {tm}", "pos": pos, "team": tm, "salary": sal, "cpt_salary": int(round(sal * 1.5)),
                     "proj": round(sal / 600 + gs.uniform(-3, 3), 1)}
                p.update(ceiling=round(p["proj"] * 1.6, 1), floor=round(p["proj"] * 0.4, 1),
                         arr=[max(0.0, gs.gauss(p["proj"], p["proj"] * 0.6 + 1)) for _ in range(300)],
                         depth=f"{pos}{j + 1}" if pos in ("RB", "WR", "TE") else None)
                sd.append(p)
    N._set_ownership(sd, n_slots=6)
    return sd


def _sd_brute(N, players, pk):
    """The best projection of any legal showdown lineup holding the pick: both teams, the cap, and the house rules
    on the FREE slots with the forced men counted against them (nfl_dfs._sd_allowed, as the search reads them)."""
    best = None
    for c in players:
        if pk.captain and c["name"] != pk.captain:
            continue
        for fl in itertools.combinations([p for p in players if p is not c], 5):
            names = {c["name"]} | {p["name"] for p in fl}
            if not pk.force <= names or names & pk.exclude or c["cpt_salary"] + sum(p["salary"] for p in fl) > 50000:
                continue
            if len({c["team"]} | {p["team"] for p in fl}) < 2:
                continue
            got, ok = [p for p in fl if p["name"] in pk.force], True
            for p in fl:
                if p["name"] not in pk.force:
                    if not N._sd_allowed(p, c, got):
                        ok = False
                        break
                    got.append(p)
            if ok:
                v = 1.5 * c["proj"] + sum(p["proj"] for p in fl)
                best = v if best is None else max(best, v)
    return best


def run(ck):
    import nfl_dfs as N
    # ==== the pick's own shape: parse, identity, the arithmetic refusals ==========================================
    try:
        N.pick_parse({"exclude": "Ace Hawk"})
        bad_shape = False
    except ValueError:
        bad_shape = True
    ck("a pick rides its own key and its own shape: {exclude, force, captain} lists of {name, team, id}; a malformed "
       "one is refused, an empty one is no pick (the sheet's body already uses `exclude` for GAMES)",
       bad_shape and N.pick_parse({}) is None and N.pick_parse({"exclude": [], "force": []}) is None
       and N.pick_parse({"force": [{"name": "Ace Hawk", "team": "aaa"}]})["force"] == [("Ace Hawk", "AAA", "")])
    csv_c, pool_c, gated_c = _classic_slate()
    import simulate
    rows_c = simulate.parse_dk_csv(csv_c)

    def res(pick, mode="classic", rows=None, **kw):
        pk, why = N.pick_resolve(N.pick_parse(pick), rows or rows_c, mode, **kw)
        return pk, why or ""
    f = lambda nm, tm: {"name": nm, "team": tm}
    cases = {
        "unknown": res({"force": [f("Zed Hawk", "AAA")]})[1],
        "both": res({"force": [f("Ace Hawk", "AAA")], "exclude": [f("Ace Hawk", "AAA")]})[1],
        "captain classic": res({"captain": f("Ace Hawk", "AAA")})[1],
        "two QBs": res({"force": [f("Ace Hawk", "AAA"), f("Ace Bear", "BBB")]})[1],
        "two TEs": res({"force": [f("Kip Hawk", "AAA"), f("Kip Bear", "BBB")]})[1],
        "dst vs offense": res({"force": [f("Bears", "BBB"), f("Cal Hawk", "AAA")]})[1],
        "stack gone": res({"force": [f("Ace Hawk", "AAA")],
                           "exclude": [f(n, "AAA") for n in ("Finn Hawk", "Gus Hawk", "Hal Hawk", "Ike Hawk", "Jax Hawk",
                                                             "Kip Hawk", "Lou Hawk")]})[1],
        "salary": res({"force": [f(n, "AAA") for n in ("Ace Hawk", "Cal Hawk", "Dax Hawk", "Finn Hawk", "Gus Hawk",
                                                       "Hal Hawk")]},
                      rows=simulate.parse_dk_csv("\n".join(r.replace(",5", ",9").replace(",6", ",9").replace(",7", ",9")
                                                            .replace(",8", ",9").replace(",4", ",9").replace(",3", ",9")
                                                            .replace(",2", ",9") if " Hawk (" in r else r
                                                            for r in csv_c.splitlines())))[1],
    }
    out_rows = [r.replace(",ACTIVE", ",OUT") if "Ace Bear (" in r else r for r in csv_c.splitlines()]
    cases["DK out"] = res({"force": [f("Ace Bear", "BBB")]}, rows=simulate.parse_dk_csv("\n".join(out_rows)))[1]
    want = {"unknown": "not on this slate", "both": "both excluded and forced", "captain classic": "showdown pick",
            "two QBs": "at most 1", "two TEs": "hold one tight end", "dst vs offense": "faces", "stack gone": "stacks",
            "salary": "over the $50,000 cap", "DK out": "DraftKings lists Ace Bear OUT"}
    board_dst = res({"force": [f("Bears", "BBB"), f("Gus Hawk", "AAA")]}, board=True)
    ck("a pick the slate cannot hold is refused with the rule named (unknown name, both excluded and forced, a captain "
       "on a classic slate, two quarterbacks, two tight ends (the builder's FLEX never takes one: said as its "
       "rule, not DraftKings'), a defense facing a forced offense, a forced quarterback "
       "with every catcher excluded, the salary with the open slots at their cheapest, DraftKings' OUT); the board's "
       "defense rule is its own (a receiver may face our defense there, never on the sheet)",
       all(want[k] in cases[k] for k in want) and board_dst[0] is not None
       # a refusal names what it held and what it excluded, never an empty list ("holding  under the cap": round 2)
       and N._pk_what(N.Pick(frozenset({"Al"}), frozenset(), None)) == "without Al"
       and N._pk_what(N.Pick(frozenset({"Al"}), frozenset({"Bo", "Cy"}), None)) == "holding Bo, Cy without Al"
       and "faces" in res({"force": [f("Bears", "BBB"), f("Gus Hawk", "AAA")]})[1],
       str({k: cases[k][:70] for k in cases if want[k] not in cases[k]}) + f" board {board_dst[1]}")
    csv_s, pool_s, gated_s = _showdown_slate()
    rows_s = simulate.parse_dk_csv(csv_s)
    six_one = res({"force": [f(n, "AAA") for n in ("Ace Hawk", "Cal Hawk", "Dax Hawk", "Eli Hawk", "Finn Hawk", "Gus Hawk")]},
                  "showdown", rows_s)[1]
    seven = res({"force": [f(n, "AAA") for n in ("Ace Hawk", "Cal Hawk", "Dax Hawk", "Eli Hawk")] +
                 [f(n, "BBB") for n in ("Ace Bear", "Cal Bear", "Dax Bear")]}, "showdown", rows_s)[1]
    # DraftKings' own showdown export: one man, two rows, two per-slot ids; the auto CSV: one id on both rows
    auto = "\n".join(r.replace("(5001),Ace Hawk,5001", "(5000),Ace Hawk,5000") for r in csv_s.splitlines())
    one_dk = res({"captain": {"name": "Ace Hawk", "team": "AAA", "id": "5001"}}, "showdown", rows_s)
    one_auto = res({"captain": {"name": "Ace Hawk", "team": "AAA", "id": "5000"}}, "showdown", simulate.parse_dk_csv(auto))
    twin = simulate.parse_dk_csv(_HEAD + "\nWR,Al Kay (1),Al Kay,1,WR/FLEX,5000,AAA@BBB 11/01/2026 01:00PM ET,AAA,9,ACTIVE"
                                 "\nWR,Al Kay Jr. (2),Al Kay Jr.,2,WR/FLEX,4000,AAA@BBB 11/01/2026 01:00PM ET,AAA,7,ACTIVE")
    exact = N._pk_find(("Al Kay Jr.", "AAA", ""), twin, lambda s: " ".join(str(s).lower().replace(".", "").replace(" jr", "").split()))
    ck("identity is DraftKings' own spelling and the team, the normalised name a fallback and the id a tiebreak: a "
       "showdown export's two rows (per-slot ids) and the auto CSV's (one id on both) resolve to one man; a name the "
       "normaliser would merge with another resolves exactly; six of one team and seven forced are refused",
       one_dk[0] is not None and one_dk[0].captain == "Ace Hawk" and one_auto[0] is not None
       and exact == ("Al Kay Jr.", None) and "both teams" in six_one and "holds 6" in seven,
       f"{one_dk} {one_auto} {exact} {six_one} {seven}")

    # ==== the sheet's search: forced men SEATED, a legal start, the answer checked (Fable B1) ===================
    pool6 = _fixture_pool6()
    real = lambda lu, obj: sum(N._value({k: v for k, v in p.items() if k != "_pk"}, obj) for p in lu)
    dev = []
    for obj, sm, bm, must in (("projection", 1, 0, ("QB AAA", "WR0 AAA", "WR1 AAA")),
                              ("ceiling", 2, 1, ("QB AAA", "WR0 AAA", "WR1 AAA")),
                              ("leverage", 2, 1, ("QB AAA", "WR0 AAA", "WR1 AAA")),
                              ("projection", 1, 0, ("Boom Wr CCC",)), ("ceiling", 2, 1, ("WR3 BBB", "WR2 CCC", "RB CCC")),
                              ("projection", 1, 0, ("DST CCC", "WR0 DDD"))):
        for p in pool6:
            p.pop("own", None)
        N._set_ownership(pool6)
        ex = _brute_classic(N, pool6, obj, sm, bm, must)
        fixed = [dict(p, _pk=True) for p in pool6 if p["name"] in must]
        lu = N.optimize([p for p in pool6 if p["name"] not in must] + fixed, 50000, obj, stack_min=sm, bring_min=bm,
                        fixed=fixed)
        holds = bool(lu) and set(must) <= {p["name"] for p in lu}
        dev.append((obj, must, ex is None and not holds or (ex is not None and holds and abs(real(lu, obj) - ex) < 1e-6)))
    for p in pool6:
        p.pop("own", None)
    N._set_ownership(pool6)
    a = N.optimize(pool6, 50000, "ceiling", stack_min=2, bring_min=1)
    b = N.optimize(pool6, 50000, "ceiling", stack_min=2, bring_min=1, fixed=None)
    ck("the sheet's search with forced players equals a constrained brute force: the owner's own shape (a quarterback "
       "and two of his receivers) under all three objectives -- the +1,000 bias alone answered 'no valid lineup' "
       "beside a legal 124.3 -- a receiver last by projection, three forced men on three teams with no quarterback, "
       "and an impossible pair refused; with nothing forced the search is the old one exactly",
       all(d[2] for d in dev) and [p["name"] for p in a] == [p["name"] for p in b],
       str([d for d in dev if not d[2]]))
    fc = _fill_checks(N)
    ck("the sheet's picked fill, mechanism by mechanism -- each one's mutant left the brute-force check above exact, "
       "the cheapest start and the polish repairing it on a pool this small (two swaps repair far less on a real "
       "slate): the cheapest start holds every forced man whenever it returns a lineup (the SEAT: without it 35 of "
       "139 did); an opponent's receiver forced as the bring-back, seated before the quarterback the builder "
       "chooses, IS the bring-back (the RECOUNT: without it the fill hunted a second one and, none left in the pool, "
       "the search answered nothing beside a legal 220.0); the salary RESERVE holds the open slots' money back: at a "
       "cap of exactly the forced salaries plus the open slots' cheapest fill, random picked fills still complete "
       "(188, 191 and 200 of 400 on three picks; 0, 0 and 4 without it)",
       fc["recount"][0] and fc["seat"][0] and min(fc["reserve"]) >= 100, str(fc))
    probe = {"name": "x", "pos": "WR", "proj": 7.0, "ceiling": 12.0, "own": 10.0}
    ck("the bias rides only a marked copy and never a reported number: an unmarked player's value is exactly the old "
       "formula on every objective, a marked one's is that plus _PK_BIAS",
       N._value(probe, "projection") == 7.0 and N._value(probe, "ceiling") == 12.0
       and N._value(probe, "leverage") == 12.0 * (1.0 - 0.007 * 10.0)
       and N._value(dict(probe, _pk=True), "ceiling") == 12.0 + N._PK_BIAS
       and N._value_sd(dict(probe, _pk=True), "leverage") == 12.0 * (1.0 - 0.007 * 10.0) + N._PK_BIAS)

    # ==== the showdown sheet: the forced captain's n distinct fills, the bias counted once ======================
    sd = _sd_sheet_pool(N)
    sd_ok = []
    for pk in (N.Pick(frozenset(), frozenset({"K0 NE", "WR1 SEA"}), "K0 NE"),
               N.Pick(frozenset({"QB0 SEA", "QB0 NE"}), frozenset({"TE1 NE"}), None),
               N.Pick(frozenset(), frozenset({"RB0 SEA", "RB1 SEA", "WR3 NE"}), None)):
        ex = _sd_brute(N, [p for p in sd if p["name"] not in pk.exclude], pk)
        fixed = [dict(p, _pk=True) for p in sd if p["name"] in pk.force]
        ours = [p for p in sd if p["name"] not in pk.exclude and p["name"] not in pk.force] + fixed
        bl = N.optimize_showdown_multi(ours, 50000, "projection", 4, rng=random.Random(1), pick=pk)
        top = 1.5 * bl[0][0]["proj"] + sum(q["proj"] for q in bl[0][1]) if bl else None
        holds = all(pk.force <= ({x[0]["name"]} | {q["name"] for q in x[1]}) for x in bl)
        distinct = len({(x[0]["name"], frozenset(q["name"] for q in x[1])) for x in bl}) == len(bl)
        sd_ok.append(ex is not None and top is not None and abs(top - ex) < 1e-6 and holds and distinct)
    cap = N.Pick(frozenset(), frozenset({"WR0 SEA"}), "WR0 SEA")
    fixed = [dict(p, _pk=True) for p in sd if p["name"] in cap.force]
    gpp = N.optimize_showdown_multi([p for p in sd if p["name"] not in cap.force] + fixed, 50000, "ceiling", 5,
                                    rng=random.Random(3), pick=cap)
    d1 = N.optimize_showdown_multi(sd, 50000, "ceiling", 3, rng=random.Random(2))
    d2 = N.optimize_showdown_multi(sd, 50000, "ceiling", 3, rng=random.Random(2), pick=None)
    ck("the showdown sheet with picks equals a brute force (a forced kicker captain, a forced FLEX with both "
       "quarterbacks out, three forced FLEX -- a forced man tried as captain once took the bias at 1.5x and outranked "
       "every other captain); a forced captain's multi-entry is his n best DISTINCT fills (one captain used to be one "
       "entry); with nothing picked the search is the old one",
       all(sd_ok) and len(gpp) == 5 and all(x[0]["name"] == "WR0 SEA" for x in gpp)
       and len({frozenset(q["name"] for q in x[1]) for x in gpp}) == 5
       and [(x[0]["name"], [q["name"] for q in x[1]]) for x in d1] == [(x[0]["name"], [q["name"] for q in x[1]]) for x in d2],
       f"{sd_ok} {len(gpp)}")
    ast_n = inspect.getsource(N._sd_allowed).count("return False")
    ck("the showdown house rules bind the free slots and are never edited: _sd_allowed still has its five rejections "
       "(S5's study hangs on them); a forced man is seated by _sd_fill(seed=) and counted against the free slots",
       ast_n == 5 and "seed=seed or None" in inspect.getsource(N._showdown_bests)
       and "range(len(picked), 5)" in inspect.getsource(N._sd_fill), str(ast_n))

    # ==== the sheet end to end (Sleeper's pool and the depth gate stubbed) ======================================
    import dfslog
    import nfl_dfs_sim

    def adjust(sport, players):            # the look-back correction's shape: proj scaled, arr replaced
        for p in players:
            p["proj"] = p["proj"] * 1.1
            p["arr"] = [x * 1.1 for x in p["arr"]]
        return len(players)
    pool_arrs = _fake_pool(pool_c, 1500)
    gated = dict(gated_c)
    gated["Bo Hawk"] = "Out (injury)"                           # a starter the gate rules out for a reason other than rank
    with _patched((nfl_dfs_sim, "player_pool", lambda week, n=3000, preseason=False, **kw: pool_arrs),
                  (N, "_apply_depth", _fake_depth(gated)), (dfslog, "adjust", adjust)):
        kw = dict(week=8, objective="ceiling", contest="gpp", field_size=300, n_lineups=4, uniq=6)
        base = _safe(N.build, csv_c, **kw)
        pk1 = _safe(N.build, csv_c, pick={"force": [f("Ace Hawk", "AAA"), f("Cal Hawk", "AAA"), f("Gus Hawk", "AAA"),
                                             f("Finn Hawk", "AAA")],
                                   "exclude": [f("Ace Lion", "CCC")]}, **kw)
        deep = _safe(N.build, csv_c, pick={"force": [f("Lou Hawk", "AAA")]}, **kw)        # TE2 by depth chart
        dead = _safe(N.build, csv_c, pick={"force": [f("Bo Hawk", "AAA")]}, **kw)          # gated Out
    rows_b = {r["name"]: r["proj"] for lu in (base.get("lineups") or [{"lineup": base.get("lineup") or []}]) for r in lu["lineup"]}
    rows_p = [r for lu in (pk1.get("lineups") or []) for r in lu["lineup"]]
    proj_ok = all(abs(r["proj"] - rows_b[r["name"]]) < 1e-9 for r in rows_p if r["name"] in rows_b)
    raw = {nm: v["proj"] for nm, v in pool_c.items()}
    corrected = all(abs(r["proj"] - round(raw[r["name"]], 1) * 1.1) < 1e-6 for r in rows_p)
    holds_p = all({"Ace Hawk", "Cal Hawk", "Gus Hawk", "Finn Hawk"} <= {r["name"] for r in lu["lineup"]}
                  and "Ace Lion" not in {r["name"] for r in lu["lineup"]} for lu in (pk1.get("lineups") or []))
    ck("a picked sheet build: every lineup holds the forced men and none excluded; its rows carry the CORRECTED "
       "projections, the unpicked build's for the same names (the copies are taken after the look-back correction, "
       "which rewrites proj and replaces arr); the result's projection is its rows' sum; the uniqueness clamp is said",
       not pk1.get("error") and holds_p and proj_ok and corrected and len(pk1.get("lineups") or []) >= 2
       and abs(pk1.get("proj", -99) - round(sum(r["proj"] for r in pk1.get("lineup") or []), 1)) < 0.11
       and any("differ by" in n for n in (pk1.get("pick") or {}).get("notes") or []),
       str((pk1.get("error"), holds_p, proj_ok, corrected, pk1.get("pick"))))
    ck("a picked sheet build meets the unpicked build's field: the contest sim's field is drawn from the field pool "
       "(the excluded man still in it) with its floor pinned to the UNPICKED optimum, so the field hash is the "
       "unpicked card's; the card names the unpicked best",
       (base.get("contest_sim") or {}).get("field_hash") is not None
       and (base.get("contest_sim") or {}).get("field_hash") == (pk1.get("contest_sim") or {}).get("field_hash")
       and pk1.get("unpicked_best") is not None and abs(pk1["unpicked_best"] - base.get("proj", -99)) < 0.11,
       str(((base.get("contest_sim") or {}).get("field_hash"), (pk1.get("contest_sim") or {}).get("field_hash"),
            pk1.get("unpicked_best"), base.get("proj"))))
    deep_in = all("Lou Hawk" in {r["name"] for r in lu["lineup"]} for lu in (deep.get("lineups") or [{"lineup": deep.get("lineup") or []}]))
    ck("a man forced past the depth chart's RANK is in every picked lineup and named as a waived rule; one the gate "
       "rules out for anything else (Out, not on a roster) is refused with its reason",
       not deep.get("error") and deep_in and any("depth chart for Lou Hawk" in w for w in (deep.get("pick") or {}).get("waived") or [])
       and dead.get("pick_refused") and "Out (injury)" in (dead.get("error") or ""),
       str((deep.get("error"), deep_in, (deep.get("pick") or {}).get("waived"), dead.get("error"))))

    # ==== the boards' rulebook and the graft (Fable R1, R2) =====================================================
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is None:
        ck("(skipped for a stated reason: numpy is not installed here; the boards are the PC's) the boards' picked "
           "rulebook, graft and builds", True)
    else:
        import dfs_tourney as T
        ents = []
        for tm, op, word in (("DAL", "PHI", "Cow"), ("PHI", "DAL", "Eag"), ("NYG", "WAS", "Gia"), ("WAS", "NYG", "Com"),
                             ("MIA", "BUF", "Dol"), ("BUF", "MIA", "Bil")):
            for pos, n in (("QB", 1), ("RB", 3), ("WR", 5), ("TE", 1), ("DST", 1)):
                for j in range(n):
                    ents.append({"name": f"{word} {pos}{j}", "pos": pos, "team": tm, "opp": op, "salary": 2800 if (pos == "WR" and j == 4) else 5000,
                                 "depth": "WR5" if (pos == "WR" and j == 4) else f"{pos}{j + 1}", "proj": 10.0})
        ix = {e["name"]: i for i, e in enumerate(ents)}
        lu = lambda *names: [ix[n] for n in names]
        # QB RB RB WR WR WR TE FLEX DST -- each row breaks at most the one rule it tests (the Bills' defense
        # faces Miami, whom no row rosters)
        four_cow = lu("Cow QB0", "Cow RB0", "Gia RB0", "Cow WR0", "Cow WR1", "Gia WR0", "Gia TE0", "Com WR0", "Bil DST0")
        four_cow_eag = lu("Cow QB0", "Cow RB0", "Eag RB0", "Cow WR0", "Cow WR1", "Eag WR0", "Eag TE0", "Eag WR1", "Bil DST0")
        punt2 = lu("Cow QB0", "Gia RB0", "Gia RB1", "Cow WR0", "Cow WR4", "Gia WR4", "Com TE0", "Com WR1", "Bil DST0")
        punt1 = lu("Cow QB0", "Gia RB0", "Gia RB1", "Cow WR0", "Cow WR4", "Gia WR0", "Com TE0", "Com WR1", "Bil DST0")
        idx = np.asarray([four_cow, four_cow_eag, punt2, punt1])
        fm = np.zeros(len(ents), dtype=bool)
        for nm in ("Cow QB0", "Cow RB0", "Cow WR0", "Cow WR1"):
            fm[ix[nm]] = True
        al = T.classic_allowed(idx, ents, forced=fm)
        fp = np.zeros(len(ents), dtype=bool)
        fp[ix["Cow WR4"]] = True
        fp[ix["Cow QB0"]] = True
        alp = T.classic_allowed(idx, ents, forced=fp)
        rng = np.random.default_rng(3)
        rnd = np.asarray([rng.choice(len(ents), 9, replace=False) for _ in range(400)])
        none_same = (T.classic_allowed(rnd, ents) == T.classic_allowed(rnd, ents, forced=np.zeros(len(ents), dtype=bool))).all()
        # a steady column's (quarterback, catcher) pair fits beside the forced men: since the forced men carry 4F the
        # solver drops a misfit pair's catcher rather than a forced man, so no board count sees a misfit pair -- it
        # only wastes its column (the mutation run found `fits` unguarded) -- and the pairs are pinned here
        pos_r = [e["pos"] for e in ents]
        mu_r = np.asarray([20.0 if p == "TE" else 10.0 for p in pos_r])          # each team's tight end its top catcher
        fte = np.zeros(len(ents), dtype=bool)
        fte[ix["Cow TE0"]] = True
        pr_te = T._safe_pairs(ents, pos_r, mu_r, fte)
        pr_none = T._safe_pairs(ents, pos_r, mu_r, np.zeros(len(ents), dtype=bool))
        # the forced tight end beside his own quarterback is a pair like any other; no OTHER tight end may be one
        pairs_fit = bool(pr_te) and all(pos_r[c] != "TE" or fte[c] for _q, c in pr_te) \
            and any(pos_r[c] == "TE" and not fte[c] for _q, c in pr_none)
        # ...and its receiver caps (round 5): four forced receivers leave no room for another (WR <= 4), three forced
        # backs and three forced receivers none either (RB + WR <= 6); receivers each team's top catchers here
        mu_w = np.asarray([20.0 if p == "WR" else 10.0 for p in pos_r])
        caps_ok = any(pos_r[c] == "WR" for _q, c in T._safe_pairs(ents, pos_r, mu_w, np.zeros(len(ents), dtype=bool)))
        for forced_names in (("Eag WR0", "Eag WR1", "Gia WR0", "Gia WR1"),
                             ("Eag RB0", "Eag RB1", "Gia RB0", "Eag WR0", "Gia WR0", "Com WR0")):
            fw_m = np.zeros(len(ents), dtype=bool)
            for nm in forced_names:
                fw_m[ix[nm]] = True
            pr_w = T._safe_pairs(ents, pos_r, mu_w, fw_m)
            caps_ok = caps_ok and bool(pr_w) and all(pos_r[c] != "WR" or fw_m[c] for _q, c in pr_w)
        pairs_fit = pairs_fit and caps_ok
        ck("the boards' rulebook with forced men binds the free slots: the team cap is per team (four forced Cowboys "
           "pass, a fourth Eagle beside them does not), a forced punt counts toward the one-punt cap with only his role "
           "waived (a second punt fails, the role-less forced one passes), and with nothing forced the mask path equals "
           "today's on random rows; a steady column's stack pair fits beside the forced men (a forced tight end: no "
           "OTHER tight end as a pair's catcher, where the solver's one tight-end slot could not hold both; four forced "
           "receivers, or three backs and three receivers: no other receiver)",
           bool(al[0]) and not bool(al[1]) and not bool(alp[2]) and bool(alp[3]) and bool(none_same)
           and not bool(T.classic_allowed(idx[3:4], ents)[0]) and pairs_fit, f"{al} {alp} {pr_te[:6]} {pr_none[:6]}")
        f1 = np.zeros(len(ents), dtype=bool)
        f1[ix["Eag WR2"]] = True
        rows_g = np.asarray([four_cow, lu("Cow QB0", "Gia RB0", "Gia RB1", "Cow WR0", "Eag WR2", "Gia WR0", "Com TE0",
                                           "Com WR1", "Bil DST0")])
        g1 = T.graft(rows_g, ents, f1)
        f3 = f1.copy()
        f3[ix["Gia QB0"]] = True
        f3[ix["Eag RB1"]] = True
        g3 = T.graft(rnd[:200], ents, f3)
        # a forced quarterback brings his best receiver over the DEAREST free receiver (Fable m1): the fewest
        # grafted rows go over the cap (over the cheapest, a $2,800 punt, the row costs $4,200 more)
        e2 = [dict(e) for e in ents]
        e2[ix["Com WR0"]]["salary"] = 7000
        fq = np.zeros(len(ents), dtype=bool)
        fq[ix["Cow QB0"]] = True
        gq = T.graft(np.asarray([lu("Gia QB0", "Gia RB0", "Gia RB1", "Gia WR0", "Gia WR4", "Com WR0", "Gia TE0",
                                    "Com WR1", "Bil DST0")]), e2, fq)
        brought = [set(r.tolist()) for r in gq if ix["Cow WR0"] in r]
        # his best receiver excluded, the bring takes the next ALLOWED one (round-2 review, MAJOR 6: forcing a
        # quarterback and excluding his best receiver brought that receiver into rows that could never be listed)
        exm = np.zeros(len(ents), dtype=bool)
        exm[ix["Cow WR0"]] = True
        gx = T.graft(np.asarray([lu("Gia QB0", "Gia RB0", "Gia RB1", "Gia WR0", "Gia WR4", "Com WR0", "Gia TE0",
                                    "Com WR1", "Bil DST0")]), e2, fq, excluded=exm)
        src_cl = inspect.getsource(T.build_nfl_classic)
        ck("the graft puts every forced man into every row without ever holding a man twice (a row already holding him "
           "keeps him -- grafted again it was an eight-man lineup in lineup_matrix) or displacing another forced man, "
           "and never runs over the cap: one forced man takes each slot his position allows; a forced quarterback's "
           "receiver replaces the dearest free receiver, and is never one the owner excluded (the next allowed one "
           "comes instead); the build passes the exclusion and lets only listable rows take the draw budget",
           T.distinct_rows(g1).all() and T.distinct_rows(g3).all() and (g1 == ix["Eag WR2"]).any(axis=1).all()
           and len(g1) == 1 + 4 and (f3[g3].sum(axis=1) == 3).all()
           and (np.asarray([[ents[i]["salary"] for i in r] for r in g3]).sum(axis=1) <= 50000).all()
           and len(brought) == 1 and ix["Com WR0"] not in brought[0] and ix["Gia WR4"] in brought[0]
           and not (gx == ix["Cow WR0"]).any() and (gx == ix["Cow WR1"]).any(axis=1).sum() == 1
           and "idx_c = graft(idx_c, ents, forced_m, excluded=excl_m)" in src_cl
           and "idx_c = idx_c[(forced_m[idx_c].sum(axis=1) == int(forced_m.sum())) & ~excl_m[idx_c].any(axis=1)]" in src_cl,
           f"{len(g1)} {len(g3)} {[sorted(ents[i]['name'] for i in b) for b in brought]} {gx.tolist()}")
        _guarded(ck, "a picked classic board at the unpicked board's seed keeps the FIELD", _board_checks, ck, np, T, N)

    # ==== the routes and the queue ==============================================================================
    _guarded(ck, "the sheet's Run carries the pick under its own key", _route_checks, ck, N, csv_c, csv_s)
    # ==== the PC: picked requests build only the picked board, first, and never consume a window ================
    _guarded(ck, "the schedule's own build calls carry no pick", _pc_checks, ck)
    _guarded(ck, "a picked build keeps the plain path's showdown window", _pc_pick_checks, ck)
    # ==== the readers that must not take a picked build for the builder's own ==================================
    import dfs_hindsight
    src_log = inspect.getsource(dfslog.log_build)
    src_pairs = inspect.getsource(dfslog._graded_pairs)
    src_rep = inspect.getsource(dfslog.report)
    ck("the look-back takes a picked build as the owner's choice, not the builder's: logged under 'picked:<objective>', "
       "kept out of the correction fit and the objective ranking; the hindsight record's board pattern skips the "
       "picked boards on purpose",
       'objective = "picked:" + objective' in src_log and "objective NOT LIKE 'picked:%'" in src_pairs
       and 'startswith("picked:")' in src_rep and dfs_hindsight._BOARD.match("cl_tourney_nfl_123_pick.pkl") is None
       and dfs_hindsight._BOARD.match("cl_tourney_nfl_123.pkl") is not None)
    _guarded(ck, "the picker is wired", _js_checks, ck)


def _board_mocks(T, N, csv, pool, gated, slate_kw, detail):
    import dk
    import nfl_dfs_sim
    pool_cache = {}

    def fake_pool(week, n=3000, **kw):
        if n not in pool_cache:
            pool_cache[n] = _fake_pool(pool, n)
        return pool_cache[n]
    return ((dk, "slate_for", lambda sport, draft_group_id=None, exclude_games=None: dict(slate_kw, csv=csv)),
            (dk, "contest_detail", lambda cid: dict(detail)), (nfl_dfs_sim, "player_pool", fake_pool),
            (N, "_apply_depth", _fake_depth(gated)), (T, "status_sig", lambda names: (None, {})),
            (T, "_engine_report", lambda *a, **k: {}))


def _board_checks(ck, np, T, N):
    csv_c, pool_c, gated_c = _classic_slate()
    pool_c["Lou Bear"]["proj"] = 0.6                         # a gated TE2 under 1.0: outside the field's extras
    detail = {"id": 1, "name": "Guard Millionaire", "prize_pool": 2.0e6, "max_entries": 40000, "entry_fee": 20.0,
              "first_prize": 200000, "places_paid": 8000, "entered": 1000, "max_entries_per_user": 150,
              "payouts": [{"from": 1, "to": 1, "prize": 200000.0}, {"from": 2, "to": 10, "prize": 10000.0},
                          {"from": 11, "to": 8000, "prize": 40.0}]}
    slate = {"starts": "2026-11-01T18:00:00.0000000Z", "draft_group_id": 999,
             "contests": [{"id": 1, "prize_pool": 2.0e6, "entries": 1000}], "n_players": 104}
    kw = dict(n_sims=1200, opt_worlds=500, field_n=6000, cand_n=4000, cal_n=2000, week=8, preseason=False, seed=7,
              workers=1, probes=[], model="legacy", log=lambda *a: None, top=15, k_port=4, candidates=200)
    f = lambda nm, tm: {"name": nm, "team": tm}
    with _patched(*_board_mocks(T, N, csv_c, pool_c, gated_c, slate, detail)):
        base = T.build_nfl_classic(999, **kw)
        pk_a = _safe(T.build_nfl_classic, 999, pick=N.pick_parse({"force": [f("Ace Hawk", "AAA"), f("Cal Hawk", "AAA"),
                                                                    f("Gus Hawk", "AAA")]}), **kw)
        pk_b = _safe(T.build_nfl_classic, 999, pick=N.pick_parse({"force": [f("Lou Lion", "CCC")],
                                                           "exclude": [f("Finn Lion", "CCC")]}), **kw)
        out = T.build_nfl_classic(999, pick=N.pick_parse({"force": [f("Lou Bear", "BBB")]}), **kw)
        out_why = T.last_refusal()
        # every catcher Ace Hawk has left is past the depth gate (WR4, WR5, TE2): DraftKings' side passes it (they
        # are on the slate), and no lineup we may enter stacks him
        empty = _safe(T.build_nfl_classic, 999, pick=N.pick_parse({"force": [f("Ace Hawk", "AAA")],
                                                                   "exclude": [f(n, "AAA") for n in ("Finn Hawk", "Gus Hawk",
                                                                                                     "Hal Hawk", "Kip Hawk")]}),
                      **kw)
        empty_why = T.last_refusal()
        # three dear forced non-quarterbacks ($24,700): a stack pair beside them overran the cap, and at +F the
        # solver dropped a forced man as readily as the pair's catcher (round-2 review, MAJOR 5)
        pk_c = _safe(T.build_nfl_classic, 999, pick=N.pick_parse({"force": [f("Hal Jet", "GGG"), f("Finn Colt", "EEE"),
                                                                    f("Dax Hawk", "AAA")]}), **kw)
    # a field extra the gate removed for a reason other than his RANK (off the depth chart: practice squad,
    # inactive) is refused on the board as on the sheet -- every lineup holding him would be a dead one
    deep_wr = max((n for n in gated_c if pool_c[n]["pos"] == "WR" and pool_c[n]["proj"] >= 1.0), key=lambda n: pool_c[n]["proj"])
    gated_x = dict(gated_c, **{deep_wr: "not on the depth chart (practice squad / inactive)"})
    with _patched(*_board_mocks(T, N, csv_c, pool_c, gated_x, slate, detail)):
        offd = _safe(T.build_nfl_classic, 999, pick=N.pick_parse({"force": [f(deep_wr, pool_c[deep_wr]["team"])]}), **kw)
        offd_why = T.last_refusal()

    def rows_of(b):
        for rs in b["results"].values():
            for k in ("top_win", "top_ev", "top_top1", "top_top01", "top_money"):
                yield from rs[k]
            yield from rs["portfolio"]["entries"]

    def lists(b):
        for r in rows_of(b):
            yield r["names"]
    ok_b = lambda b: isinstance(b, dict) and "players" in b and "pick" in b
    # exact, never a rounded proxy (Fable m6): the field's whole receipt -- its calibration, steering, completion,
    # achieved shape and collision, unrounded -- and the per-player shares
    same = lambda b: ok_b(b) and (sorted((p["name"], p["field_pct"], p["opt_pct"], p.get("casual_pct")) for p in b["players"])
                      == sorted((p["name"], p["field_pct"], p["opt_pct"], p.get("casual_pct")) for p in base["players"])
                      and b["slate"]["field_only"] == base["slate"]["field_only"]
                      and b["field_model"] == base["field_model"] and b["opt_worlds"] == base["opt_worlds"]
                      and b["optimal_distinct"] == base["optimal_distinct"])
    hold = lambda b: ok_b(b) and all(set(b["pick"]["force"]) <= set(n) and not (set(b["pick"]["exclude"]) & set(n)) and len(set(n)) == 9
                         for n in lists(b)) and any(True for _ in lists(b))
    ck("a picked classic board at the unpicked board's seed keeps the FIELD and the first solve exactly: every "
       "player's field share, best-lineup share and casual share, the field's extras and its whole receipt (calibration, "
       "steering, completion, collision, achieved shape -- unrounded) identical (a solve biased toward the forced men "
       "would move the field's ownership targets), and with nothing excluded our own draws are the unpicked board's "
       "too; every listed and portfolio lineup holds every forced man, no excluded one, nine different players; a "
       "forced field extra is named as a waived rule",
       base is not None and same(pk_a) and same(pk_b) and hold(pk_a) and hold(pk_b)
       and pk_a["candidate_draws"] == base["candidate_draws"]
       and any("depth chart for Lou Lion" in w for w in pk_b["pick"]["waived"])
       and "pick_s" in pk_a["timings"] and "pick" not in base,
       str((base is None, (pk_a or {}).get("error"), (pk_b or {}).get("error"), T.last_refusal())))
    m1 = lambda b: ok_b(b) and all(b["pick"].get(k, 0) > 0 for k in ("second_solve", "steady", "allowed")) \
        and b["pick"]["second_solve_held"] == b["pick"]["second_solve"] and b["pick"]["steady_held"] == b["pick"]["steady"]
    srcs = lambda b: {r.get("source") for r in rows_of(b)} if ok_b(b) else set()
    ck("a picked classic board's own candidates come from the SECOND solve over the picked pool, the forced rows raised "
       "in every world: every one of its answers and of the picked steady columns holds the pick (the counts are the "
       "receipt: with the force weakened the graft alone still lists rows) -- three dear forced non-quarterbacks too, "
       "where a stack pair beside them overruns the cap and a forced man at +F tied the pair's catcher -- a listed row "
       "says it came from the second solve, and every listed row names its source",
       m1(pk_a) and m1(pk_b) and m1(pk_c) and hold(pk_c) and same(pk_c) and "second solve" in srcs(pk_a)
       and srcs(pk_a) <= set(T._PICK_SOURCES) and srcs(pk_b) <= set(T._PICK_SOURCES),
       str(((pk_a or {}).get("pick"), (pk_b or {}).get("pick"), (pk_c or {}).get("pick") or (pk_c or {}).get("error"),
            srcs(pk_a))))
    ck("a man forced from outside the pool a classic board simulates is refused naming the board's rule -- adding his "
       "row would change the field's draw even masked out (measured) -- never built as a data miss; a pick that leaves "
       "no lineup we may enter (a forced quarterback whose catchers left are all past the depth gate) is refused naming "
       "the house rules, never published as a board of empty lists (Fable M2); a field extra the gate removed for "
       "anything but his rank (off the depth chart) is refused as the sheet refuses him -- the board built him in "
       "with a garbled depth waiver (round-2 review, MAJOR 4)",
       out is None and "outside the pool this board simulates" in out_why and "projected 0.6" in out_why
       and empty is None and "no candidate lineup holds your picks" in (empty_why or "")
       and offd is None and f"{deep_wr} cannot be forced: not on the depth chart" in (offd_why or ""),
       f"{out_why} | {empty} | {empty_why} | {offd_why}")
    csv_s, pool_s, gated_s = _showdown_slate()
    detail_s = {"id": 2, "name": "Guard Showdown", "prize_pool": 1.0e6, "max_entries": 30000, "entry_fee": 10.0,
                "first_prize": 100000, "places_paid": 6000, "entered": 500, "max_entries_per_user": 150,
                "starts": "2026-11-03T01:15:00.0000000Z",
                "payouts": [{"from": 1, "to": 1, "prize": 100000.0}, {"from": 2, "to": 10, "prize": 5000.0},
                            {"from": 11, "to": 6000, "prize": 20.0}]}
    slate_s = {"starts": "2026-11-03T01:15:00.0000000Z", "draft_group_id": 777,
               "contests": [{"id": 2, "prize_pool": 1.0e6, "entries": 1000}], "n_players": 48}
    kws = dict(n_sims=800, week=9, preseason=False, seed=3, workers=1, model="legacy", log=lambda *a: None, top=15,
               portfolio_sizes=(3,))
    with _patched(*_board_mocks(T, N, csv_s, pool_s, gated_s, slate_s, detail_s)):
        sbase = T.build_nfl_showdown(777, **kws)
        spk = _safe(T.build_nfl_showdown, 777, pick=N.pick_parse({"force": [f("Bears", "BBB"), f("Ace Hawk", "AAA")],
                                                          "captain": f("Ace Hawk", "AAA"),
                                                          "exclude": [f("Cal Bear", "BBB")]}), **kws)
        # the extra the unpicked board drops to hold its universe under the cap, forced: refused (dropping the next
        # extra instead built another universe and so another field; round-2 review, MAJOR 3)
        dropped = [d["name"] for d in (sbase or {}).get("excluded") or [] if "out of the field too" in (d.get("why") or "")]
        capped = T.build_nfl_showdown(777, pick=N.pick_parse({"force": [f(dropped[0], pool_s[dropped[0]]["team"])]}),
                                      **kws) if dropped else "no extra dropped"
        cap_why = T.last_refusal()
        # captain Ace Hawk with only the tight ends, kickers and defenses left free: every five of the six breaks a
        # house rule (two kickers/defenses at most, one tight end a team, never the captain's opponents' defense)
        free = {"Kip Hawk", "Lou Hawk", "Max Hawk", "Hawks", "Max Bear", "Bears", "Ace Hawk"}
        empty_s = _safe(T.build_nfl_showdown, 777, pick=N.pick_parse(
            {"captain": f("Ace Hawk", "AAA"), "exclude": [f(nm, p["team"]) for nm, p in sorted(pool_s.items()) if nm not in free]}),
            **kws)
        empty_s_why = T.last_refusal()
    # a field extra gated by a roster lookup (not his rank) is refused, as the sheet refuses him (MAJOR 4)
    rost = max((n for n in gated_s if pool_s[n]["proj"] >= 1.0 and n not in dropped), key=lambda n: pool_s[n]["proj"])
    with _patched(*_board_mocks(T, N, csv_s, pool_s, dict(gated_s, **{rost: "not on any roster Sleeper knows"}),
                                slate_s, detail_s)):
        offr = _safe(T.build_nfl_showdown, 777, pick=N.pick_parse({"force": [f(rost, pool_s[rost]["team"])]}), **kws)
        offr_why = T.last_refusal()
    own = lambda b: sorted((p["name"], p["field_cpt_pct"], p["field_flex_pct"], p["opt_cpt_pct"], p["opt_flex_pct"])
                           for p in b["players"])

    def srows(b):
        for k in ("top_win", "top_ev", "top_top1", "top_money"):
            yield from b[k]
        for port in (b.get("portfolio") or {}).values():
            yield from port.get("entries") or []
    sd_ok = isinstance(spk, dict) and "players" in spk
    sd_hold = sd_ok and any(True for _ in srows(spk)) and spk["lineups_allowed"] > 0 and all(
        {"Bears", "Ace Hawk"} <= {p["name"] for p in r["lineup"]} and "Cal Bear" not in {p["name"] for p in r["lineup"]}
        and next(p["name"] for p in r["lineup"] if p.get("slot") == "CPT") == "Ace Hawk" for r in srows(spk))
    # exact (Fable m6): the universe's size, the worlds, the field's sharpness and its top build, unrounded where kept
    sd_same = sd_ok and own(spk) == own(sbase) and all(spk[k] == sbase[k] for k in ("lineups_legal", "worlds")) \
        and all(spk["field_model"][k] == sbase["field_model"][k] for k in ("beta", "top_copies")) \
        and spk["field_model"]["chalk"]["lineup"] == sbase["field_model"]["chalk"]["lineup"] \
        and spk["lineups_allowed"] < sbase["lineups_allowed"]
    ck("a picked showdown board narrows only what we may enter: the universe, the worlds, the field's weights and "
       "chalk and the best-lineup shares are the unpicked board's exactly, fewer lineups are ours, every listed and "
       "portfolio entry (there are some) carries the forced captain and men and no excluded one, the house rules the "
       "picks break are named (a defense opposing his captain); a forced man the universe cap drops from the unpicked "
       "board's field is refused naming the cap (dropping the next extra instead changed the field: round-2 review); "
       "a field extra gated by a roster lookup, not his rank, is refused as on the sheet; a pick that leaves no lineup "
       "we may enter is refused naming the house rules, never published as a board of empty lists (Fable M2)",
       sbase is not None and sd_hold and sd_same
       and any("construction rules for Bears" in w for w in spk["pick"]["waived"])
       and capped is None and "him among them" in (cap_why or "") and f"{dropped[0]} is outside the pool" in (cap_why or "")
       and offr is None and f"{rost} cannot be forced: not on any roster Sleeper knows" in (offr_why or "")
       and empty_s is None and "no lineup holds your picks" in (empty_s_why or ""),
       str((sbase is None, (spk or {}).get("error"), sd_hold, sd_same, (spk or {}).get("pick"), dropped, cap_why,
            offr_why, empty_s, empty_s_why)))
    # a forced FLEX breaking a construction rule beside a FREE captain is named for the listed lineups, as the sheet
    # names it (round-2 review, m3): a forced defense beside the other team's quarterback at captain
    ents = [{"name": "QA", "pos": "QB", "team": "AAA", "salary": 11000}, {"name": "DB", "pos": "DST", "team": "BBB", "salary": 3000},
            {"name": "W1", "pos": "WR", "team": "AAA", "salary": 6000, "depth": "WR1"},
            {"name": "W2", "pos": "WR", "team": "BBB", "salary": 6000, "depth": "WR1"},
            {"name": "R1", "pos": "RB", "team": "AAA", "salary": 6000, "depth": "RB1"},
            {"name": "R2", "pos": "RB", "team": "BBB", "salary": 6000, "depth": "RB1"},
            {"name": "QB2", "pos": "QB", "team": "BBB", "salary": 10000}]
    rows_w = np.asarray([[0, 1, 2, 3, 4, 5], [6, 1, 2, 3, 4, 5], [2, 1, 3, 4, 5, 6]])
    fw1 = T._sd_free_captain_waivers(ents, rows_w, [0, 1], frozenset({"DB"}))
    fw = T._sd_free_captain_waivers(ents, rows_w, [0, 1, 2], frozenset({"DB"}))
    fw_b = T._sd_free_captain_waivers(ents, rows_w, [1], frozenset({"DB"}))
    ck("a forced FLEX breaking a construction rule beside a FREE captain is named, once, for the lineups the board "
       "lists (a forced defense beside the other team's quarterback at captain: the board listed it without a word, "
       "where the sheet names it -- round-2 review, m3), with every captain it was waived beside (one was named "
       "while he sat beside others too -- round 3), four at most; beside his own team's captain, nothing to name",
       fw1 == ["the showdown construction rules for DB beside captain QA"]
       and fw == ["the showdown construction rules for DB beside captains QA, W1"] and fw_b == []
       and N.sd_waiver_text("X", "FEDCBA") == "the showdown construction rules for X beside captains A, B, C, D and 2 more"
       and "pk_waived += _sd_free_captain_waivers(ents, idx, listed, forced)" in inspect.getsource(T.build_nfl_showdown),
       str((fw1, fw, fw_b)))


def _route_checks(ck, N, csv_c, csv_s):
    import app
    import dk
    cl = app.app.test_client()
    got = {}

    def fake_build(*a, **k):
        got["pick"] = k.get("pick")
        return {"mode": "classic", "lineup": [], "objective": "ceiling"}
    with _sandbox(), _patched((app, "_locked", lambda kind: None), (N, "build", fake_build),
                              (dk, "slate_for", lambda sport, draft_group_id=None, exclude_games=None:
                               (got.__setitem__("games", exclude_games) or {"csv": csv_c, "draft_group_id": 999,
                                                                             "n_players": 104, "n_dropped": 0,
                                                                             "dropped": [], "starts": None})),
                              (app, "_dfs_log", lambda *a, **k: None)):
        app.app.config["TESTING"] = True
        r = cl.post("/api/simulate/dfs", json={"sport": "nfl", "csv": "", "exclude": ["AAA@BBB"],
                                               "pick": {"exclude": [{"name": "Ace Hawk", "team": "AAA"}]}})
        bad = cl.post("/api/simulate/dfs", json={"sport": "nfl", "csv": csv_c, "pick": {"force": "nope"}})
    ck("the sheet's Run carries the pick under its own key: a body with `exclude` (GAMES, to the slate loader) and "
       "`pick.exclude` (players, to the builder) has both effects; a malformed pick is a 400 naming the picker",
       r.status_code == 200 and got.get("games") == ["AAA@BBB"]
       and got.get("pick", {}).get("exclude") == [("Ace Hawk", "AAA", "")] and bad.status_code == 400
       and "player picker" in ((bad.get_json() or {}).get("error") or ""), str((r.status_code, got, bad.status_code)))
    # the Build queue
    with _sandbox(), _patched((dk, "slate_for", lambda sport, draft_group_id=None, exclude_games=None:
                               {"csv": csv_s if int(draft_group_id or 0) == 777 else csv_c, "draft_group_id": draft_group_id}),
                              (dk, "contests", lambda sport, draft_group_id=None: [{"prize_pool": 2.0e6}]),
                              (app, "_tourney_slate_start", lambda sport, dg: None)):
        with app.app.test_request_context("/api/dfs/tourney/request", method="POST",
                                          json={"sport": "nfl", "dg": 777, "kind": "showdown", "label": "AAA vs BBB",
                                                "pick": {"captain": {"name": "Ace Hawk", "team": "AAA"}}}):
            a1 = app.api_dfs_tourney_request()
        with app.app.test_request_context("/api/dfs/tourney/request", method="POST",
                                          json={"sport": "nfl", "dg": 777, "kind": "showdown",
                                                "pick": {"force": [{"name": "Zed Hawk", "team": "AAA"}]}}):
            a2 = _safe(app.api_dfs_tourney_request)        # a crash is this check's failure, not the group's
        reqs = app._tourney_requests()
        pend = app._tourney_pending()
        import boardshare
        import time as _t
        boardshare.put("sd_tourney_nfl_777", {"kind": "showdown", "built_ts": int(_t.time()) + 60})
        pend_after_plain = app._tourney_pending()
        boardshare.put("sd_tourney_nfl_777_pick", {"kind": "showdown", "built_ts": int(_t.time()) + 60, "pick": {"force": ["Ace Hawk"]}})
        pend_after_pick = app._tourney_pending()
        with app.app.test_request_context("/api/dfs/tourney?sport=nfl&dg=777&pick=1"):
            b1 = app.api_dfs_tourney()
        with app.app.test_request_context("/api/dfs/tourney?sport=nfl&dg=777"):
            b0 = app.api_dfs_tourney()
    j1 = a1.get_json() if hasattr(a1, "get_json") else a1[0].get_json()
    j2, c2 = ((a2, 500) if isinstance(a2, dict) else (a2[0].get_json(), a2[1]) if isinstance(a2, tuple)
              else (a2.get_json(), 200))
    jb1 = b1.get_json() if hasattr(b1, "get_json") else b1[0].get_json()
    jb0 = b0.get_json() if hasattr(b0, "get_json") else b0[0].get_json()
    ck("a picked Build is checked against DraftKings' pool before it is queued (a name not on the slate: 400 with the "
       "reason), stored with the request, answered by the picked board ONLY (the plain board landing leaves it owed), "
       "and the answer says what was checked and that Sleeper's side is checked when the PC builds",
       j1.get("queued") and "Sleeper" in (j1.get("checked") or "") and c2 == 400 and "not on this slate" in (j2.get("error") or "")
       and (reqs.get("777") or {}).get("pick", {}).get("captain", {}).get("name") == "Ace Hawk"
       and [p["dg"] for p in pend] == [777] and [p["dg"] for p in pend_after_plain] == [777] and pend_after_pick == [],
       str((j1, j2, c2, reqs, pend, pend_after_plain, pend_after_pick)))
    ck("the board route serves the picked board on ?pick=1 and the plain answer points at it",
       jb1.get("picked") is True and (jb1.get("pick") or {}).get("force") == ["Ace Hawk"]
       and jb0.get("picked") is False and jb0.get("picked_built_ts") is not None, str((jb1, jb0)))


def _pc_checks(ck):
    import pc_worker as W
    src_cl = inspect.getsource(W._task_classic_tourney)
    src_sd = inspect.getsource(W._task_showdown_tourney)
    line = lambda src, call: src.split(call)[1].split("\n")[0]
    ck("the schedule's own build calls carry no pick (only _picked_builds passes one, on the request's branch), and a "
       "picked request never reaches the automatic paths (_split_picked hands them the plain requests only)",
       "pick" not in line(src_cl, "dfs_tourney.build_nfl_classic(") and "pick" not in line(src_sd, "dfs_tourney.build_nfl_showdown(")
       and "_split_picked(_tourney_requests(url, tok))" in src_cl and "_split_picked(_tourney_requests(url, tok))" in src_sd
       and "pick=pick" in inspect.getsource(W._picked_builds) and "_consume_window" not in inspect.getsource(W._picked_builds))
    import boardshare
    import dfs_tourney
    import dk
    calls, state = [], {}
    import time as _t
    now = _t.time()
    main = {"draft_group_id": 4242, "games": 10, "starts": "2026-11-01T18:00:00.0000000Z", "contest_type": 21,
            "top_pool": 2.0e6}
    req = {"dg": 4242, "kind": "classic", "ts": int(now) - 60, "pick": {"exclude": [], "force": [{"name": "Ace Hawk", "team": "AAA", "id": ""}], "captain": None}}

    def fake_build(dg, **kw):
        calls.append(("pick" in kw and kw["pick"] is not None, int(dg)))
        return {"kind": "classic", "built_ts": int(_t.time()), "candidates": 1, "worlds": 1, "contests": [], "timings": {}}
    with _sandbox(), _patched((dfs_tourney, "available", lambda: True), (dk, "slates", lambda sport: [main]),
                              (dk, "slate_for", lambda sport, draft_group_id=None: {"csv": "x"}),
                              (W, "_tourney_requests", lambda url, tok: {4242: req}),
                              (W, "_main_slate", lambda slates: main), (W, "_cl_window_key", lambda st: "2026-10-31 Saturday night"),
                              (W, "_tourney_state", lambda: state), (W, "_tourney_state_save", lambda s: None),
                              (W, "_report_attempt", lambda *a, **k: calls.append(("attempt",) + a[3:5])),
                              (W, "_ship_boards", lambda url, tok: None), (W, "_yield_point", lambda what: None),
                              (W, "_tourney_resave", lambda *a: False), (W, "_dt_ts", lambda s: now + 86400),
                              (dfs_tourney, "build_nfl_classic", fake_build)):
        W._task_classic_tourney("http://x", "t")
        boards = sorted(n for n, _m in boardshare.names("cl_tourney_nfl_"))
        by = {n: ((boardshare.get(n, None)[0] or {}).get("requested_by") or {}).get("by") for n in boards}
    ck("one slate owing a picked Build and a scheduled build in one cycle builds both, the request first: the picked "
       "board under `_pick`, the automatic board on its own reason, the scheduled window consumed by the plain build "
       "only (Fable R4)",
       calls[:2] == [(True, 4242), (False, 4242)] and state.get("cl_windows", {}).get("4242") == "2026-10-31 Saturday night"
       and any(n.endswith("4242_pick") and b == "PC request (picked)" for n, b in by.items())
       and any(n.endswith("4242") and b == "Schedule" for n, b in by.items()),
       str((calls, state, by)))


def _pc_pick_checks(ck):
    import time as _t
    import boardshare
    import dfs_tourney
    import pc_worker as W
    now = _t.time()
    starts = {"far": now + 9 * 86400, "near": now + 2 * 86400, "soon": now + 2000}
    listed = {5151: {"draft_group_id": 5151, "games": 1, "contest_type": 96, "starts": "far"},
              5252: {"draft_group_id": 5252, "games": 3, "contest_type": 21, "starts": "near"},
              5353: {"draft_group_id": 5353, "games": 1, "contest_type": 96, "starts": "near"},
              6161: {"draft_group_id": 6161, "games": 10, "contest_type": 21, "starts": "soon"}}
    req = lambda dg, kind: {"dg": dg, "kind": kind, "ts": int(now) - 60,
                            "pick": {"exclude": [], "force": [{"name": "Ace Hawk", "team": "AAA", "id": ""}], "captain": None}}
    rep, built = [], []

    def fake(kind):
        def build(dg, **kw):
            built.append((kind, int(dg), kw.get("pick") is not None, kw.get("n_sims")))
            return {"kind": kind, "built_ts": int(_t.time()), "timings": {}}
        return build
    # the plain board on file took 1,000 s, 900 of them the first solve: a picked build adds a second solve that size
    plain = {"kind": "classic", "built_ts": int(now) - 3600, "sims": W._CL_SIMS_FULL,
             "timings": {"total_s": 1000.0, "optimal_s": 900.0}}
    with _sandbox(), _patched((W, "_report_attempt", lambda url, tok, dg, rq, why, kind=None: rep.append((int(dg), why))),
                              (W, "_yield_point", lambda what: None), (W, "_ship_boards", lambda url, tok: None),
                              (W, "_dt_ts", lambda s: starts.get(s)),
                              (dfs_tourney, "build_nfl_showdown", fake("showdown")),
                              (dfs_tourney, "build_nfl_classic", fake("classic"))):
        W._picked_builds("http://x", "t", "showdown", {dg: req(dg, "showdown") for dg in (5151, 5252, 5353)}, listed)
        boardshare.put("cl_tourney_nfl_6161", plain)
        W._picked_builds("http://x", "t", "classic", {6161: req(6161, "classic")}, listed)
    why = dict(rep)
    ck("a picked showdown request keeps the plain path's window (Fable M3): a slate nine days out and one that is not "
       "a one-game showdown are reported, never built (the first answered every cycle with a fetch, an unprojected "
       "week's simulation and a refusal); a picked classic build near kickoff counts its second solve in the time "
       "estimate (Fable R5): 2,000 s out with a 1,000 s plain build, 900 s of it the first solve, the plain board "
       "would run full worlds and the picked one runs the quick count",
       [b[:3] for b in built] == [("showdown", 5353, True), ("classic", 6161, True)]
       and "more than eight days out" in why.get(5151, "") and "not a one-game showdown slate" in why.get(5252, "")
       and built[-1][3] == W._CL_SIMS_QUICK and W._cl_sims(now + 2000, plain, now=now) == W._CL_SIMS_FULL
       and W._cl_sims(now + 2000, plain, now=now, picked=True) == W._CL_SIMS_QUICK, str((built, rep)))
    # a press the builder refused is not rebuilt every cycle: most picker refusals come after the slate's
    # simulation, and every cycle repeated one ahead of the scheduled boards (round-2 review, MAJOR 2)
    tries, st_r = [], {}
    req_r = req(7171, "classic")
    listed_r = {7171: {"draft_group_id": 7171, "games": 10, "contest_type": 21, "starts": "near"}}

    def refuse(dg, **kw):
        tries.append(int(dg))
        return dfs_tourney._refuse("player picker: no candidate lineup holds your picks (guard)")
    with _sandbox(), _patched((W, "_report_attempt", lambda *a, **k: None), (W, "_yield_point", lambda what: None),
                              (W, "_ship_boards", lambda url, tok: None), (W, "_dt_ts", lambda s: starts.get(s)),
                              (W, "_tourney_state", lambda: st_r), (W, "_tourney_state_save", lambda s: None),
                              (dfs_tourney, "build_nfl_classic", refuse)):
        W._picked_builds("http://x", "t", "classic", {7171: req_r}, listed_r)               # refused
        W._picked_builds("http://x", "t", "classic", {7171: req_r}, listed_r)               # the next cycle: no
        again = dict(req_r, ts=req_r["ts"] + 5)
        W._picked_builds("http://x", "t", "classic", {7171: again}, listed_r)               # pressed again: yes
        n_press = len(tries)
        st_r["pk_refused"]["7171"]["at"] -= W._PICK_RETRY_S + 1
        W._picked_builds("http://x", "t", "classic", {7171: again}, listed_r)               # six hours on: yes
    # ...but only the PICKER's refusals wait, and never past half the time to lock; anything else (a roster lookup
    # down) is tried again next cycle as the plain builds are, and a request gone takes its refusal with it (round 3)
    tries2, st_2, why2 = [], {}, {"v": "the depth-chart gate's roster lookup is unavailable (NFLD-roster)"}

    def refuse2(dg, **kw):
        tries2.append(int(dg))
        return dfs_tourney._refuse(why2["v"])
    soon_l = {7272: {"draft_group_id": 7272, "games": 10, "contest_type": 21, "starts": "soon"}}
    req_s = req(7272, "classic")
    with _sandbox(), _patched((W, "_report_attempt", lambda *a, **k: None), (W, "_yield_point", lambda what: None),
                              (W, "_ship_boards", lambda url, tok: None), (W, "_dt_ts", lambda s: starts.get(s)),
                              (W, "_tourney_state", lambda: st_2), (W, "_tourney_state_save", lambda s: None),
                              (dfs_tourney, "build_nfl_classic", refuse2)):
        W._picked_builds("http://x", "t", "classic", {7272: req_s}, soon_l)                # a roster outage
        W._picked_builds("http://x", "t", "classic", {7272: req_s}, soon_l)                # tried again: yes
        n_transient = len(tries2)
        why2["v"] = "player picker: no candidate lineup holds your picks (guard)"
        W._picked_builds("http://x", "t", "classic", {7272: req_s}, soon_l)                # refused, 2,000 s to lock
        W._picked_builds("http://x", "t", "classic", {7272: req_s}, soon_l)                # waits
        n_wait = len(tries2)
        st_2["pk_refused"]["7272"]["at"] -= 1001                                             # half the time to lock
        W._picked_builds("http://x", "t", "classic", {7272: req_s}, soon_l)                # tried again: yes
        n_cap = len(tries2)
        W._picked_builds("http://x", "t", "classic", {}, soon_l)                           # the request gone
        pruned = st_2.get("pk_refused") == {}
    ck("a picked Build the picker refused is tried again only every six hours (or half the time left to lock, when "
       "sooner) or when the owner presses Build again, never every cycle -- each try re-simulated the slate, and a "
       "'no candidate lineup' refusal re-ran most of a classic build ahead of the scheduled boards; any other "
       "refusal (a roster lookup down) is tried again next cycle, as the plain builds are -- six hours of a Sunday "
       "outage would have held the pick past lock; a request gone takes its refusal record with it",
       n_press == 2 and len(tries) == 3 and W._PICK_RETRY_S == 6 * 3600 and n_transient == 2 and n_wait == 3
       and n_cap == 4 and pruned, str((tries, st_r, tries2, st_2)))


def _js_checks(ck):
    js_path = os.path.join(_ROOT, "static", "dfspick.js")
    js = open(js_path, encoding="utf-8").read()
    app_js = open(os.path.join(_ROOT, "static", "app.js"), encoding="utf-8").read()
    html = open(os.path.join(_ROOT, "templates", "index.html"), encoding="utf-8").read()
    sw = open(os.path.join(_ROOT, "static", "sw.js"), encoding="utf-8").read()
    app_src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    inline = [h for h in ("onclick=", "onchange=", "oninput=", "onload=", "onerror=") if h in js]
    ck("the picker is wired: its script on the page with the cache-buster, in the service worker's shell, its two "
       "containers (the sheet's under the CSV, the PC Build's under the dropdown), the pick in the Run body and the "
       "Build request, the banner on both results, the picked board's link with &pick=1; no inline handlers",
       not inline and '<script src="/static/dfspick.js?v={{ v }}"></script>' in html and 'id="dfsPickSheet"' in html
       and '"/static/dfspick.js"' in sw and '"dfspick.js"' in app_src and 'id="dfsPickPc"' in app_js
       and 'pick: (sp === "nfl" && typeof dfsPickPayload === "function") ? dfsPickPayload("sheet") : null' in app_js
       and 'pick: typeof dfsPickPayload === "function" ? dfsPickPayload("pc", dg) : null' in app_js and app_js.count("dfsPickBanner(d)") == 2
       and '"&pick=1"' in app_js, str(inline))
    node_ok, out = None, ""
    if shutil.which("node"):
        evil = "<img src=x onerror=alert(1)>"
        csv = ("Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame,Status\\n"
               f"QB,{evil} (1),{evil},1,CPT,9000,AAA@BBB,AAA,20,ACTIVE\\n"
               f"QB,{evil} (2),{evil},2,FLEX,6000,AAA@BBB,AAA,20,ACTIVE\\n"
               "WR,Bo Bear (3),Bo Bear,3,CPT,7500,AAA@BBB,BBB,12,OUT\\nWR,Bo Bear (4),Bo Bear,4,FLEX,5000,AAA@BBB,BBB,12,OUT")
        harness = (
            "const vm = require('vm'); const fs = require('fs');"
            "const els = {}; const mk = (id) => (els[id] = els[id] || {id, innerHTML: '', value: '', focus() {}, setSelectionRange() {}});"
            "const doc = {getElementById: mk, querySelector: () => null, addEventListener: () => {}};"
            "const store = {}; const ctx = {document: doc, localStorage: {getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; }},"
            " console, fetch: async () => ({json: async () => ({})}), Map, JSON, Math, String, Object, Array, parseInt};"
            "vm.createContext(ctx); vm.runInContext(fs.readFileSync(" + json.dumps(js_path) + ", 'utf8'), ctx);"
            "mk('dfsSport').value = 'nfl'; mk('dfsCsv').value = \"" + csv + "\"; mk('dfsPickSheet');"
            "vm.runInContext(`dfsPickLoadSheet(); _PK.sheet.q = 'img'; _pkRender('sheet');"
            " const i = _PK.sheet.pool.findIndex((r) => r.name.includes('img'));"
            " _pkAct('sheet', 'captain', i); const j = _PK.sheet.pool.findIndex((r) => r.name === 'Bo Bear'); _pkAct('sheet', 'exclude', j);"
            " _pkAct('sheet', 'force', j);"
            " globalThis.__p = dfsPickPayload('sheet'); globalThis.__sd = _PK.sheet.showdown; globalThis.__n = _PK.sheet.pool.length;"
            " globalThis.__b = dfsPickBanner({pick: {captain: '" + evil + "', force: ['" + evil + "'], exclude: [], waived: ['" + evil + "'], notes: []}});`, ctx);"
            "const html = els.dfsPickSheet.innerHTML;"
            "console.log(JSON.stringify({raw: html.includes('<img') || ctx.__b.includes('<img'), sd: ctx.__sd, n: ctx.__n, p: ctx.__p}));")
        try:
            r = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=60)
            out = (r.stdout + r.stderr)[-700:]
            res = json.loads(r.stdout.strip().splitlines()[-1])
            p = res["p"] or {}
            node_ok = (r.returncode == 0 and res["raw"] is False and res["sd"] is True and res["n"] == 2
                       and (p.get("captain") or {}).get("name", "").startswith("<img") and len(p.get("force") or []) == 2
                       and p.get("exclude") == [])
        except Exception as e:
            node_ok, out = False, repr(e)
    no_node = "" if shutil.which("node") else "(skipped for a stated reason: node is not on this machine's path; CI's runner has it) "
    ck(no_node + "the picker's own logic under node: a showdown export is read as one man a row pair, a captain is also "
       "forced, forcing a man takes him off the excluded list, the payload carries {name, team, id}, and a name that "
       "is an <img onerror> is written escaped in the picker and in the banner",
       node_ok is not False, out)
    # the round-2 review's screen items, under node with the page's own listeners: a paste or a programmatic fill
    # of the CSV box shows that pool (MAJOR 1: the picker showed the old pool, or none, until a Run); a new captain
    # takes the old one's forced entry with him (n5); Build on the PC never sends another slate's picks, not even
    # while the new slate's pool loads (m4)
    node2_ok, out2 = None, ""
    if shutil.which("node"):
        head = "Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame,Status\n"
        sd_row = lambda pos, nm, i, tm, sal: (f"{pos},{nm} ({i}),{nm},{i},CPT,{int(sal * 1.5)},AAA@BBB,{tm},10,ACTIVE\n"
                                              f"{pos},{nm} ({i + 1}),{nm},{i + 1},FLEX,{sal},AAA@BBB,{tm},10,ACTIVE\n")
        csv_a = head + sd_row("QB", "Old One", 1, "AAA", 9000) + sd_row("WR", "Old Two", 3, "BBB", 6000)
        csv_b = head + sd_row("QB", "Al Kay", 11, "AAA", 9000) + sd_row("WR", "Bo Bee", 13, "BBB", 6000) \
            + sd_row("RB", "Cy Dee", 15, "AAA", 5000)
        csv_pc = head + "QB,Pc Qb (21),Pc Qb,21,QB,7000,AAA@BBB,AAA,20,ACTIVE\nWR,Pc Wr (22),Pc Wr,22,WR/FLEX,5000,AAA@BBB,AAA,12,ACTIVE\n"
        harness2 = """
const vm = require('vm'); const fs = require('fs');
const els = {}; const L = {};
const mk = (id) => (els[id] = els[id] || {id, innerHTML: '', value: '', focus() {}, setSelectionRange() {}, getAttribute: () => null});
const doc = {getElementById: mk, querySelector: () => null, addEventListener: (t, fn) => { (L[t] = L[t] || []).push(fn); }};
const store = {};
const ctx = {document: doc, localStorage: {getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; }},
  console, fetch: async (u) => ({json: async () => ({csv: CSV_PC})}), Map, JSON, Math, String, Object, Array, parseInt};
vm.createContext(ctx); vm.runInContext(fs.readFileSync(JS_PATH, 'utf8'), ctx);
mk('dfsSport').value = 'nfl'; mk('dfsPickSheet'); mk('dfsPickPc'); mk('dfsCsv').value = CSV_A;
const out = {};
(async () => {
  vm.runInContext('dfsPickLoadSheet()', ctx);
  out.n_before = vm.runInContext('_PK.sheet.pool.length', ctx);
  els.dfsCsv.value = CSV_B;
  (L.input || []).forEach((fn) => fn({target: els.dfsCsv}));
  out.n_after = vm.runInContext('_PK.sheet.pool.length', ctx);
  vm.runInContext("_pkAct('sheet', 'captain', _PK.sheet.pool.findIndex((r) => r.name === 'Al Kay'));"
    + "_pkAct('sheet', 'captain', _PK.sheet.pool.findIndex((r) => r.name === 'Bo Bee'));", ctx);
  out.force_after = vm.runInContext('JSON.stringify(_PK.sheet.force.map((p) => p.name))', ctx);
  out.cap_after = vm.runInContext('(_PK.sheet.captain || {}).name', ctx);
  await vm.runInContext('dfsPickLoad("pc", 7)', ctx);
  vm.runInContext('_pkAct("pc", "force", 0)', ctx);
  out.p7 = vm.runInContext('JSON.stringify(dfsPickPayload("pc", 7))', ctx);
  out.p8_wrong = vm.runInContext('JSON.stringify(dfsPickPayload("pc", 8))', ctx);
  const pending = vm.runInContext('dfsPickLoad("pc", 8)', ctx);
  out.p8_loading = vm.runInContext('JSON.stringify(dfsPickPayload("pc", 8))', ctx);
  out.force_loading = vm.runInContext('_PK.pc.force.length', ctx);
  await pending;
  store['vigil.pick.dg.9'] = JSON.stringify({exclude: [], force: [{name: 'Pc Qb', team: 'AAA'}], captain: null});
  vm.runInContext('dfsPickLoad("pc", 9)', ctx);
  out.p9_before = vm.runInContext('JSON.stringify(dfsPickPayload("pc", 9))', ctx);
  await vm.runInContext('dfsPickReady(9)', ctx);
  out.p9_after = vm.runInContext('JSON.stringify(dfsPickPayload("pc", 9))', ctx);
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({error: String((e && e.stack) || e)})); });
"""
        for k, v in (("JS_PATH", json.dumps(js_path)), ("CSV_PC", json.dumps(csv_pc)), ("CSV_A", json.dumps(csv_a)),
                     ("CSV_B", json.dumps(csv_b))):
            harness2 = harness2.replace(k, v)
        try:
            r2 = subprocess.run(["node", "-e", harness2], capture_output=True, text=True, timeout=60)
            out2 = (r2.stdout + r2.stderr)[-700:]
            res2 = json.loads(r2.stdout.strip().splitlines()[-1])
            p7 = json.loads(res2.get("p7") or "null") or {}
            node2_ok = (r2.returncode == 0 and "error" not in res2 and res2["n_before"] == 2 and res2["n_after"] == 3
                        and json.loads(res2["force_after"]) == ["Bo Bee"] and res2["cap_after"] == "Bo Bee"
                        and [x["name"] for x in p7.get("force") or []] == ["Pc Qb"]
                        and res2["p8_wrong"] == "null" and res2["p8_loading"] == "null" and res2["force_loading"] == 0
                        and res2["p9_before"] == "null"
                        and [x["name"] for x in (json.loads(res2["p9_after"] or "null") or {}).get("force") or []] == ["Pc Qb"])
        except Exception as e:
            node2_ok, out2 = False, repr(e)
    # the two programmatic fills (a loaded or dropped file; a contest's pool) redraw the picker: neither fires `input`
    fills = sum(1 for pin in ('ta.value = String(r.result || "");\n      // a programmatic fill fires no input event: the '
                              'player picker reads the new pool here\n      if (typeof dfsPickLoadSheet === "function") '
                              'dfsPickLoadSheet();',
                              '$("dfsCsv").value = s.csv;\n        if (typeof dfsPickLoadSheet === "function") '
                              'dfsPickLoadSheet();') if pin in app_js)
    ck(no_node + "the picker follows the page (round-2 review): a paste in the CSV box, a contest's pool and a "
       "loaded file each show their own players at once (the picker showed the last pool, or none, until a Run); "
       "a new captain takes the old one's forced entry with him; Build on the PC never sends the picks made on "
       "another slate's pool, not even while the new slate's pool loads, and a Build pressed while it loads waits "
       "for it and the picks saved for that slate (it went out plain and replaced a queued picked one: round 3)",
       node2_ok is not False and fills == 2
       and 'if (typeof dfsPickReady === "function") await dfsPickReady(dg);' in app_js, f"{out2} fills={fills}")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
