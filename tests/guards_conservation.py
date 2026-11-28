"""Guards: football's conservation laws (backlog Phase 1.2) -- the engine's box
dealt to the players, the touchdown allocator, DraftKings' 0.02-point lattice
and the order a lineup's points are summed in.

    python3 tests/guards_conservation.py        # exits 1 on any failed check
    from tests.guards_conservation import run   # run(ck) inside the main suite

The engine (nfl_dfs_engine) plays a game and deals its box to the players; the
DFS boards are built on those worlds. Every quantity the box counts is a
budget: what the players are dealt must add back to it in EVERY world, or a
lineup is scored on touchdowns, catches or yards the game never produced. The
existing deal guard (combo_audit_guards, "the deal holds every box-score
identity in every world") checks one synthetic box; these check the engine's
OWN boxes, the allocator's contract on the edge cases the owner listed (zero
budgets, exact capacity, insufficient capacity, extreme weights, zero-capacity
players, unusually large budgets), the scorers by an integer route that shares
no code with dk_scoring, and the scoring pass's independence of summation
order on DraftKings' lattice.

Expected values are computed here by a route that shares no code with the
function under test: DraftKings points in integer 0.02-point units from DK's
published card, lineup buckets as integer sums of those units.

Written by an Opus audit (2026-11-26, research/sprint_2026_11_26/conservation),
which found three defects, none reaching a board that day: the allocator
dropped touchdowns where a man had room but no weight (A8) and past twelve
overflow rounds (A9), and a team with two quarterbacks could hand one of them
a line no game produces (E2). All three are fixed in the same commit; A12 and
E0 hold the fix to the shipped code draw for draw wherever neither defect
arises (every board built so far).
"""
import inspect
import os
import random
import sys
import tempfile

os.environ.setdefault("VIGIL_NO_BG", "1")
os.environ.setdefault("ERRLOG_DB", os.path.join(tempfile.gettempdir(), "conservation_guards_errlog.db"))
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if not os.path.exists(os.path.join(_ROOT, "nfl_dfs_engine.py")):       # run from outside tests/
    _ROOT = os.environ.get("VIGIL_ROOT", _ROOT)
sys.path.insert(0, _ROOT)

# DraftKings' classic NFL card in integer 0.02-point units (DK's published
# table, typed here, not read from dk_scoring): 0.04 a passing yard = 2 units,
# 0.1 a rushing or receiving yard = 5, a catch 50, passing TD 200, rushing or
# receiving TD 300, interception and lost fumble -50, each 300/100/100 bonus 150.
_U = {"pass_yd": 2, "pass_td": 200, "int": -50, "rush_yd": 5, "rush_td": 300, "rec": 50, "rec_yd": 5,
      "rec_td": 300, "fum": -50}
_BONUS_U = 150


def _units(c, n, np):
    """A player's DK points in 0.02 units from his integer components."""
    z = np.zeros(n, dtype=np.int64)
    g = lambda k: np.asarray(c.get(k, z)).astype(np.int64)          # noqa: E731
    u = sum(_U[k] * g(k) for k in _U)
    return (u + _BONUS_U * (g("pass_yd") >= 300) + _BONUS_U * (g("rush_yd") >= 100)
            + _BONUS_U * (g("rec_yd") >= 100))


def _pool(team):
    """A team's DFS pool in the shape weekly_games builds (Sleeper means):
    one quarterback, two backs, three receivers, two tight ends, and a
    fullback whose only line is a carry -- he can be dealt carries, never a
    catch."""
    rows = [("QB", {"pass_att": 34, "pass_cmp": 22, "pass_yd": 240, "pass_td": 1.6, "int": 0.7,
                    "rush_att": 3, "rush_yd": 14, "rush_td": 0.1, "fum": 0.15}),
            ("RB", {"rush_att": 16, "rush_yd": 70, "rush_td": 0.5, "rec_tgt": 4, "rec": 3, "rec_yd": 22, "rec_td": 0.1, "fum": 0.1}),
            ("RB", {"rush_att": 7, "rush_yd": 30, "rush_td": 0.2, "rec_tgt": 2, "rec": 1.5, "rec_yd": 10, "rec_td": 0.05}),
            ("WR", {"rec_tgt": 9, "rec": 6, "rec_yd": 80, "rec_td": 0.5, "fum": 0.05}),
            ("WR", {"rec_tgt": 7, "rec": 4.5, "rec_yd": 55, "rec_td": 0.35}),
            ("WR", {"rec_tgt": 3, "rec": 2.0, "rec_yd": 24, "rec_td": 0.12, "rush_att": 0.3, "rush_yd": 2}),
            ("TE", {"rec_tgt": 5, "rec": 3.6, "rec_yd": 38, "rec_td": 0.3}),
            ("TE", {"rec_tgt": 1.5, "rec": 1.1, "rec_yd": 10, "rec_td": 0.1}),
            ("RB", {"rush_att": 0.6, "rush_yd": 2, "rush_td": 0.05})]
    return [{"name": f"{team}-{pos}{j}", "pos": pos, "team": team, "opp": None, "means": dict(m),
             "proj_pts": 10.0} for j, (pos, m) in enumerate(rows)]


def _books(deal, idx, box, np):
    """{identity: failing worlds} for one team's deal against its box."""
    n = len(box["pts"])
    z = np.zeros(n, dtype=np.int64)
    S = lambda k: sum((np.asarray(deal[i].get(k, z)).astype(np.int64) for i in idx), z)   # noqa: E731
    qb = [i for i in idx if "pass_att" in deal[i]]
    Q = lambda k: sum((np.asarray(deal[i][k]).astype(np.int64) for i in qb), z)            # noqa: E731
    kn = np.asarray(box.get("kneels", z)).astype(np.int64)
    out = {
        "receptions = completions": S("rec") != box["comp"],
        "receiving yards = passing yards": S("rec_yd") != box["pass_yds"],
        "receiving TDs = passing TDs (the touchdown budget)": S("rec_td") != box["td_pass"],
        "QB attempts / completions / yards / TDs / INTs = the box": (
            (Q("pass_att") != box["pass_att"]) | (Q("pass_cmp") != box["comp"]) | (Q("pass_yd") != box["pass_yds"])
            | (Q("pass_td") != box["td_pass"]) | (Q("int") != box["int"])),
        "carries = rush attempts + kneels": S("rush_att") != box["rush_att"] + kn,
        "rushing yards = team rushing yards - kneels": S("rush_yd") != box["rush_yds"] - kn,
        "rushing TDs = team rushing TDs (the touchdown budget)": S("rush_td") != box["td_rush"],
        "lost fumbles = the box": S("fum") != box["fum_lost"],
        "targets between completions and attempts": (S("rec_tgt") < box["comp"]) | (S("rec_tgt") > box["pass_att"]),
    }
    per = z.astype(bool)
    for i in idx:
        d = deal[i]
        if "rec" in d:
            per = per | (d["rec_td"] > d["rec"]) | (d["rec"] > d["rec_tgt"]) | (d["rec"] < 0)
        if "rush_att" in d:
            car = d["rush_att"] - (kn if i == (qb[0] if qb else -1) else 0)     # the QB's kneels carry no TD
            per = per | (d.get("rush_td", z) > car) | (car < 0)
    out["no TD without a catch or a carry, no catch without a target"] = per
    whole = all(np.issubdtype(np.asarray(v).dtype, np.integer) for i in idx for v in deal[i].values())
    return {k: int(np.asarray(v).sum()) for k, v in out.items()}, whole


def run(ck):
    try:
        import numpy as np
    except ImportError:
        ck("(numpy absent: the conservation checks run where the boards are built)", True)
        return
    import dfs_tourney as T
    import dk_scoring
    import nfl_deep_sim as E
    import nfl_dfs_csim as C
    import nfl_dfs_engine as G

    print("=" * 72)
    print("A. The touchdown allocator (nfl_dfs_csim._multinomial_capped; the engine's deal calls it too)")
    print("=" * 72)
    # The contract in its docstring: allocated = min(drawn, physical capacity),
    # drawn = allocated + impossible surplus (_capped_short), no man above his
    # capacity, nothing negative, whole numbers. One helper reads all of it.
    def contract(N, w, cap, seed=0):
        N = np.asarray(N, dtype=np.int64)
        w = np.asarray(w, dtype=np.float64)
        cap = np.asarray(cap, dtype=np.int64)
        a = C._multinomial_capped(np.random.default_rng(seed), N, w, cap)
        want = np.minimum(N, cap.sum(axis=0))
        short = C._capped_short(N, cap)
        lost = int((want - a.sum(axis=0)).sum())
        ok = (lost == 0 and bool((a <= cap).all()) and bool((a >= 0).all())
              and np.issubdtype(a.dtype, np.integer) and bool(np.array_equal(N, a.sum(axis=0) + short)))
        return ok, a, lost, short

    n = 2000
    col = lambda v: np.tile(np.asarray(v, dtype=np.float64)[:, None], (1, n))   # noqa: E731
    ok, a, lost, _ = contract(np.zeros(n), col([1, 2, 3]), col([2, 2, 2]))
    ck("A1 zero budget: nobody is dealt a touchdown and the books balance at zero", ok and int(a.sum()) == 0, f"lost {lost}")
    ok, a, lost, _ = contract(np.full(n, 6), col([1, 5, 0.2]), col([1, 2, 3]))
    ck("A2 exact capacity (six touchdowns, catches 1 + 2 + 3): every man is filled to his catches exactly, whatever the weights",
       ok and bool(np.array_equal(a, col([1, 2, 3]).astype(np.int64))), f"lost {lost}")
    _rg = np.random.default_rng(4)
    _cap = _rg.integers(0, 4, size=(6, n))
    ok, a, lost, _ = contract(_cap.sum(axis=0), _rg.lognormal(0, 2, size=(6, n)), _cap, seed=5)
    ck("A2b exact capacity on random catches per world (budget = the team's catches): every catch is a touchdown",
       ok and bool(np.array_equal(a, _cap)), f"lost {lost}")
    ok, a, lost, short = contract(np.full(n, 10), col([1, 1, 1]), col([1, 2, 3]))
    ck("A3 insufficient capacity (ten drawn, six catches): six land, the four that cannot are the reported surplus, drawn = allocated + surplus",
       ok and int(a.sum(axis=0).max()) == 6 and bool((short == 4).all()), f"lost {lost}")
    ok, a, lost, _ = contract(np.full(n, 3), col([1e9, 1, 1]), col([0, 2, 2]))
    ck("A4 a zero-capacity man with a billion-to-one weight is dealt nothing; the three land on the men who caught passes",
       ok and int(a[0].sum()) == 0 and bool((a.sum(axis=0) == 3).all()), f"lost {lost}")
    ok, a, lost, short = contract(np.full(n, 3), col([1, 1]), col([0, 0]))
    ck("A4b nobody caught a pass: nothing is allocated and all three are the reported surplus (never forced onto the field)",
       ok and int(a.sum()) == 0 and bool((short == 3).all()), f"lost {lost}")
    ok1, _, l1, _ = contract(np.full(n, 3), col([1e300, 1e-300]), col([1, 5]))
    ok2, _, l2, _ = contract(np.full(n, 3), col([1e-13, 1e-13]), col([2, 2]))
    ok3, _, l3, _ = contract(np.full(n, 4), col([1e12, 1.0, 1e-12]), col([1, 1, 5]))
    ck("A5 extreme weights (1e300 against 1e-300, both below the 1e-12 floor, twelve orders apart): the books still balance",
       ok1 and ok2 and ok3, f"lost {l1} {l2} {l3}")
    ok1, a1, l1, _ = contract(np.full(n, 10 ** 6), col([1, 2, 3]), col([10 ** 6] * 3))
    ok2, a2, l2, s2 = contract(np.full(n, 10 ** 6), col([1, 2, 3]), col([5, 5, 5]))
    ck("A6 an unusually large budget (a million): dealt in full where the room exists, cut to the room (15) with the rest reported where it does not",
       ok1 and ok2 and int(a1.sum(axis=0).min()) == 10 ** 6 and bool((s2 == 10 ** 6 - 15).all()), f"lost {l1} {l2}")
    # The regime production lives in: positive weight wherever there is room,
    # at most twelve men with room. A random sweep over it.
    bad = 0
    for t in range(300):
        k = int(_rg.integers(1, 13))
        cap = _rg.integers(0, 6, size=(k, 50))
        w = _rg.lognormal(0, 3, size=(k, 50)) * (cap > 0) + 1e-9 * (cap > 0)
        okt, _, _, _ = contract(_rg.integers(0, 31, size=50), w, cap, seed=t)
        bad += (not okt)
    ck("A7 300 random teams (1-12 men, catches 0-5, budgets 0-30, weights spanning ~8 orders): the contract holds in every world",
       bad == 0, f"{bad} of 300 failed")
    # DEFECT A: a man with room but zero weight. The loop stops when every man
    # with weight is full (`live` is False) and the rest of the budget simply
    # is not dealt -- and _capped_short reports a surplus of 0, so the
    # drawn = allocated + surplus books do NOT balance. Both production callers
    # floor the weight of every man with a catch (1e-9, 1e-3), so it does not
    # fire today; the contract in the docstring is unconditional.
    ok, a, lost, short = contract(np.full(n, 3), col([1.0, 0.0]), col([1, 5]))
    ck("A8 room without weight (catches 1 and 5, weights 1 and 0, three touchdowns): all three are dealt -- until 2026-11-26 one was and two vanished, reported as no surplus",
       ok, f"allocated {int(a.sum(axis=0)[0])} of 3, surplus reported {int(short[0])}, lost {lost} over {n} worlds")
    # DEFECT B: the fixed twelve rounds. Every round that overflows fills at
    # least one man, so twelve rounds always suffice for twelve men; with
    # thirteen men with room and weights that put each round's budget on one
    # man, the thirteenth touchdown is never dealt. Unreachable on a real box
    # (it needs 13+ passing touchdowns), but the owner's "unusually large
    # budgets" case, and the engine's deal hands the QB the box total, so
    # it would show as QB TDs != receivers' TDs with nothing recording why.
    ok, a, lost, short = contract(np.full(n, 13), np.tile((10.0 ** (-3 * np.arange(13)))[:, None], (1, n)),
                                  np.ones((13, n), dtype=np.int64))
    ck("A9 thirteen men with one catch each and thirteen touchdowns, weights 1e-3 apart: all thirteen are dealt -- until 2026-11-26 twelve fixed rounds dealt twelve",
       ok, f"lost {lost} touchdowns over {n} worlds (allocated {int(a.sum(axis=0).min())}-{int(a.sum(axis=0).max())} of 13)")
    # A12: the fix changes nothing the shipped allocator got right. The
    # shipped form (2026-10 to 2026-11-26), kept here as the reference: on
    # inputs where every man with room has weight (both production callers
    # floor it) and twelve rounds suffice, the two deal draw for draw alike.
    def _shipped(rng, N, shares, cap, rounds=12):
        cap = np.asarray(cap)
        owed = np.minimum(np.asarray(N).astype(np.int64), cap.sum(axis=0).astype(np.int64))
        alloc = np.zeros(cap.shape, dtype=np.int64)
        for _ in range(rounds):
            if not owed.any():
                break
            room = cap - alloc
            w = np.where(room > 0, shares, 0.0)
            live = (owed > 0) & (w.sum(axis=0) > 0)
            if not live.any():
                break
            take = np.minimum(C._multinomial(rng, np.where(live, owed, 0), w), room)
            alloc = alloc + take
            owed = owed - take.sum(axis=0)
        return alloc
    _rs12, same12 = np.random.default_rng(12), 0
    for t in range(200):
        k = int(_rs12.integers(1, 12))
        cap12 = _rs12.integers(0, 6, size=(k, 300))
        w12 = _rs12.random((k, 300)) * _rs12.choice([1e-6, 1.0, 1e3], size=(k, 1)) * cap12 + 1e-3 * (cap12 > 0)
        N12 = _rs12.integers(0, 9, size=300)
        same12 += int(np.array_equal(C._multinomial_capped(np.random.default_rng(t), N12, w12, cap12),
                                     _shipped(np.random.default_rng(t), N12, w12, cap12)))
    ck("A12 wherever the shipped allocator was right (every man with room has weight, twelve rounds enough) the fixed one deals draw for draw "
       "the same, so no board built before the fix would read differently: 200 random teams of 1-11 men",
       same12 == 200, f"{same12} of 200 identical")
    # the uncapped multinomial the deal draws catches and carries with
    m_bad = 0
    for w in (col([0, 0, 0]), col([1e-13, 1e-14, 0]), col([1e300, 1, 1e-300]), _rg.lognormal(0, 4, size=(9, n))):
        Nm = _rg.integers(0, 60, size=n)
        m_bad += int((C._multinomial(np.random.default_rng(1), Nm, w).sum(axis=0) != Nm).sum())
    ck("A10 the catch/carry multinomial deals exactly its N in every world, all-zero, tiny and extreme weights included",
       m_bad == 0, f"{m_bad} worlds off")
    tot = np.asarray([7, -3, 0, 250, -1])
    sw = G._split_w(tot, np.asarray([[0.0, 1.0, 0.0, 3.0, 2.0], [2.0, 0.0, 0.0, 1.0, 0.0], [1.0, 2.0, 0.0, 0.0, 0.0]]))
    ck("A11 team yards split to the yard: parts sum to the total (negative totals too), a man with no weight in a world gets nothing unless nobody has any",
       bool(np.array_equal(sw.sum(axis=0), tot)) and int(sw[0, 0]) == 0 and int(sw[1, 1]) == 0 and int(sw[2, 3]) == 0
       and int(sw[1, 4]) == 0 and int(sw[2, 4]) == 0, str(sw.tolist()))

    print("=" * 72)
    print("B. The engine's own boxes, dealt (nfl_dfs_engine.play_worlds -> deal_team)")
    print("=" * 72)
    Tb = E.tables()
    L = E.league_profile(Tb)
    ng = 1500
    bh, ba = G.play_worlds(L, dict(L, abbr="B"), ng, random.Random(23), Tb, env_sd=G.ENV_SD)
    cap_bad = sum(int(((b["td_pass"] > b["comp"]) | (b["td_rush"] > b["rush_att"]) | (b["comp"] > b["pass_att"])
                       | (b["plays"] != b["pass_att"] + b["sacks"] + b["rush_att"] + b["kneels"])
                       | (b["fg_made"] != b["fg_0_39"] + b["fg_40_49"] + b["fg_50p"])).sum()) for b in (bh, ba))
    ck("B1 the engine's touchdown budget never exceeds its capacity -- handled upstream, every passing TD is a completion and every rushing TD a carry -- "
       "and its plays are its attempts + sacks + rushes + kneels, its field goals its three distance tiers, in all 3,000 team-games",
       cap_bad == 0, f"{cap_bad} team-games break a box identity")
    pl = _pool("A") + _pool("B")
    books = {}
    whole = True
    for t, box, seed in (("A", bh, 31), ("B", ba, 32)):
        idx = [i for i, p in enumerate(pl) if p["team"] == t]
        d = G.deal_team(pl, idx, box, np.random.default_rng(seed))
        b, w_ = _books(d, idx, box, np)
        whole = whole and w_
        for k, v in b.items():
            books[k] = books.get(k, 0) + v
    ck("B2 dealt off the engine's own boxes, every budget is conserved in every world: catches, receiving yards and receiving TDs are the QB's, "
       "carries/yards/TDs the team's (kneels the QB's at -1 each), lost fumbles the team's, no TD without a catch or carry -- all whole numbers, no TD lost or created",
       whole and all(v == 0 for v in books.values()), str({k: v for k, v in books.items() if v}))
    # The edges of the budget through the real deal: every catch a touchdown
    # (exact capacity), worlds with no completion at all (zero budget), and a
    # box far beyond any real game (sixty completions, twenty-five touchdowns).
    nb = 3000
    rg = np.random.default_rng(41)
    comp = rg.integers(0, 61, nb)
    comp[: nb // 3] = 0
    att = comp + rg.integers(0, 15, nb)
    ra = rg.integers(0, 61, nb)
    edge = {"pts": np.zeros(nb, dtype=np.int64), "pass_att": att, "comp": comp,
            "pass_yds": comp * rg.integers(-1, 20, nb), "int": np.minimum(att - comp, rg.integers(0, 3, nb)),
            "td_pass": np.where(rg.random(nb) < 0.5, comp, np.minimum(comp, rg.integers(0, 26, nb))),
            "rush_att": ra, "rush_yds": ra * rg.integers(-2, 9, nb),
            "td_rush": np.where(rg.random(nb) < 0.5, ra, np.minimum(ra, rg.integers(0, 21, nb))),
            "fum_lost": rg.integers(0, 4, nb), "kneels": rg.integers(0, 4, nb)}
    idx = [i for i, p in enumerate(pl) if p["team"] == "A"]
    d = G.deal_team(pl, idx, edge, np.random.default_rng(43))
    b, w_ = _books(d, idx, edge, np)
    ck("B3 the deal's edges: zero completions (a third of the worlds), every catch and every carry a touchdown (half), up to sixty completions "
       "and twenty-five TDs -- every touchdown lands on a man who caught (carried) it, none lost, none created",
       w_ and all(v == 0 for v in b.values()), str({k: v for k, v in b.items() if v}))
    rep = open(os.path.join(_ROOT, "research", "reports", "nfl_dfs_engine.md")).read()
    ck("B4 the one documented exception stays documented: the box's two-point conversions and offensive return TDs reach no player "
       "(the team's points identity counts them; DraftKings pays them) -- dealt, or the engine report says they are not",
       ("two_point" in inspect.getsource(G.dk_offense))
       or "Two-point conversions and offensive return touchdowns are not dealt to players" in rep)

    print("=" * 72)
    print("C. DraftKings scoring off the dealt box (integer 0.02-point units, DK's card typed here)")
    print("=" * 72)
    sc_bad = lat_bad = 0
    for t, box, seed in (("A", bh, 31), ("B", ba, 32)):
        idx = [i for i, p in enumerate(pl) if p["team"] == t]
        d = G.deal_team(pl, idx, box, np.random.default_rng(seed))
        for i in idx:
            pts = G.dk_offense(d[i], ng)
            u = _units(d[i], ng, np)
            sc_bad += int((np.abs(pts - u / 50.0) > 1e-9).sum())
            lat_bad += int((np.abs(pts * 50 - np.rint(pts * 50)) > 1e-6).sum())
    ck("C1 every engine offensive score is DraftKings' own number for its integer line (0.04/pass yd, 0.1/rush and rec yd, 1/catch, 4/6/6 TDs, -1 INT and fumble, "
       "the 300/100/100 bonuses) and sits on the 0.02 lattice",
       sc_bad == 0 and lat_bad == 0, f"{sc_bad} off DK's score, {lat_bad} off the lattice")
    tiers = [(0, 10), (1, 7), (6, 7), (7, 4), (13, 4), (14, 1), (20, 1), (21, 0), (27, 0), (28, -1), (34, -1), (35, -4), (70, -4)]
    one = lambda v: np.asarray([v])                                          # noqa: E731
    z1 = {k: one(0) for k in ("td_ret", "blk", "sacks", "int", "fum_lost", "pts", "ret_pts", "td_def", "safety_conceded")}
    tier_ok = all(float(G.dk_dst(z1, dict(z1, pts=one(pa)))[0]) == pts_ for pa, pts_ in tiers)
    dst_bad = k_bad = 0
    for me, op in ((bh, ba), (ba, bh)):
        pa = op["pts"] - 6 * op["td_def"] - 2 * me["safety_conceded"]
        tier = np.select([pa == 0, pa <= 6, pa <= 13, pa <= 20, pa <= 27, pa <= 34], [10, 7, 4, 1, 0, -1], -4)
        want = tier + op["sacks"] + 2 * op["int"] + 2 * op["fum_lost"] + 6 * me["td_ret"] + 2 * op["safety_conceded"] + 2 * me["blk"]
        dst_bad += int((G.dk_dst(me, op) != want).sum()) + int((pa < 0).sum())
        k_bad += int((G.dk_kicker(me) != 3 * me["fg_0_39"] + 4 * me["fg_40_49"] + 5 * me["fg_50p"] + me["xp_made"]).sum())
    ck("C2 the defense: DK's points-allowed tiers at every edge (0 / 1-6 / 7-13 / 14-20 / 21-27 / 28-34 / 35+), and on the engine's games its score is "
       "the tier + every sack, takeaway, return TD, safety and block of that same game (points allowed never negative)",
       tier_ok and dst_bad == 0, f"tiers {tier_ok}, {dst_bad} defense-worlds off")
    # C2b: DraftKings' points-allowed rule, verified by the owner on the official
    # classic and showdown rules (2026-11-26): a kickoff or punt return TD counts
    # against the other D/ST in full; the six of a pick-six (or a defensive fumble
    # TD) does not, its try does; the safeties this defense scored stay out.
    def dst(op_pts, op_td_def=0, me_blk=0, me_safety_conceded=0, me_td_ret=0):
        me = dict(z1, blk=one(me_blk), safety_conceded=one(me_safety_conceded), td_ret=one(me_td_ret))
        op = dict(z1, pts=one(op_pts), td_def=one(op_td_def))
        return float(G.dk_dst(me, op)[0]), int(G.off_points(op, me)[0])
    cases = {
        "offence 14": (dst(14), (1.0, 14)),
        "offence 14 + kickoff return TD + XP": (dst(21), (0.0, 21)),
        "offence 14 + punt return TD + two-point try": (dst(22), (0.0, 22)),
        "offence 14 + pick-six + XP (the try counts)": (dst(21, op_td_def=1), (1.0, 15)),
        "offence 14 + pick-six, try missed": (dst(20, op_td_def=1), (1.0, 14)),
        "only a pick-six, try missed: a shutout": (dst(6, op_td_def=1), (10.0, 0)),
        "pick-six and a defensive fumble TD, both tries made": (dst(14, op_td_def=2), (7.0, 2)),
        "a safety this defense scored stays out (+2 for it)": (dst(16, me_safety_conceded=1), (1.0, 14)),
        "a blocked kick (+2) and this unit's own return TD (+6)": (dst(14, me_blk=1, me_td_ret=1), (9.0, 14)),
        "the 34/35 edge": (dst(35), (-4.0, 35)),
    }
    bad = {k: v for k, v in cases.items() if v[0] != v[1]}
    ck("C2b DraftKings' points allowed by event (the owner's verified rule): kickoff and punt return TDs count in full, "
       "a pick-six's (or defensive fumble TD's) six do not but its try does, a shutout of the offence survives an "
       "opponent's return six, the safeties this defense scored stay out, blocks and this unit's own return TDs pay, "
       "and the tiers hold at their edges", not bad, str(bad))
    # 2026-11-28: from nfl_deep_sim.ENGINE_VERSION 2 (the staged fumble-return TD, research/reports/
    # fumble_td_fix.md) a lost fumble can be returned for a touchdown too; before it a pick-six is the only kind,
    # and that stronger claim is still held while version 1 is production.
    import nfl_deep_sim as _E_c2c
    v2 = getattr(_E_c2c, "ENGINE_VERSION", 1) >= 2
    td_bad = 0
    for me, op in ((bh, ba), (ba, bh)):
        takeaways = op["int"] + (op["fum_lost"] if v2 else 0)
        td_bad += int(((me["td_def"] > me["td_ret"]) | (me["td_def"] > takeaways)).sum())
    ck("C2c on the engine's own games every defensive touchdown is one of this side's return touchdowns and one of "
       "the other offence's interceptions (engine version 1: a pick-six is the only kind it plays) or, from "
       "version 2, its interceptions and lost fumbles", td_bad == 0, f"{td_bad} worlds off (engine v{2 if v2 else 1})")
    ck("C3 the kicker is his own field goals by DK's distance tiers (3/4/5) plus his tries, in every world",
       k_bad == 0, f"{k_bad} worlds off")
    # what one unit loses the other gains: an interception costs the QB a point
    # and pays the other defense two -- the same event, counted once each side
    x_bad = 0
    for t, box, opp, seed in (("A", bh, ba, 31), ("B", ba, bh, 32)):
        idx = [i for i, p in enumerate(pl) if p["team"] == t]
        d = G.deal_team(pl, idx, box, np.random.default_rng(seed))
        ints = sum(d[i].get("int", 0) for i in idx)
        fums = sum(d[i].get("fum", 0) for i in idx)
        x_bad += int((np.asarray(ints) != box["int"]).sum()) + int((np.asarray(fums) != box["fum_lost"]).sum())
    ck("C4 every takeaway is one event on both sides: the interceptions and lost fumbles dealt to an offence are exactly the ones the other defense is paid for",
       x_bad == 0, f"{x_bad} worlds off")
    # end to end through simulate_game, the function the boards call
    gm = {"label": "BUF @ KC", "teams": ["KC", "BUF"], "home": "KC", "away": "BUF", "neutral": False,
          "players": [dict(p, team=tm, name=p["name"].replace("A-", tm + "-", 1)) for tm in ("KC", "BUF") for p in _pool("A")]}
    nsim = 1200
    sim = G.simulate_game(gm, nsim, np.random.default_rng(7),
                          {"T": Tb, "profiles": {"KC": L, "BUF": dict(L, abbr="BUF")}, "drive": {}, "workers": 1,
                           "anchor_n": 200, "anchor_iters": 1, "keep_boxes": True})
    e_bad = []
    for i, p in enumerate(sim["players"]):
        c = sim["components"][i]
        if not np.array_equal(p["arr"], G.dk_offense(c, nsim)) or (np.abs(p["arr"] - _units(c, nsim, np) / 50.0) > 1e-9).any():
            e_bad.append(p["name"])
    bx = sim["boxes"]
    for tm in ("KC", "BUF"):
        idx = [i for i, p in enumerate(gm["players"]) if p["team"] == tm]
        b, w_ = _books(sim["components"], idx, bx[tm], np)
        if not w_ or any(b.values()):
            e_bad.append(f"{tm} books {b}")
        if not np.allclose(sim["team_fp"][tm], sum(sim["players"][i]["arr"] for i in idx), atol=1e-9):
            e_bad.append(f"{tm} team_fp")
        o = "BUF" if tm == "KC" else "KC"
        if not (np.array_equal(sim["dst"][tm], G.dk_dst(bx[tm], bx[o])) and np.array_equal(sim["k"][tm], G.dk_kicker(bx[tm]))):
            e_bad.append(f"{tm} dst/k")
    ck("C5 end to end (simulate_game, the boards' call): each player's array is DraftKings' score of his own dealt line, both teams' books balance "
       "against the box the game played, team output is the sum of its players, the defense and kicker are scored off that same game",
       not e_bad, str(e_bad[:4]))

    print("=" * 72)
    print("D. A lineup's points do not depend on the order they are added in (DraftKings' lattice)")
    print("=" * 72)
    rs = np.random.default_rng(7)
    P, Nw, K = 60, 300, 2500
    u = rs.integers(-150, 2200, size=(P, Nw))                 # 0.02-point units: -3 to 44 points a man
    u[-6:] = rs.integers(-4, 30, size=(6, Nw)) * 50           # defenses and kickers: whole points
    X = np.asarray([[round(float(v) * 0.02, 2) for v in row] for row in u], dtype=np.float32)   # player_pool's lists
    idx9 = np.stack([rs.choice(P, 9, replace=False) for _ in range(K)]).astype(np.int32)
    W9 = T.lineup_matrix(idx9, P)
    want9 = np.clip(2 * u[idx9].sum(axis=1), 0, T._NB - 1).T                     # (Nw, K) 0.01 buckets, exact
    perm = rs.permutation(P)
    b_a = T._buckets(np.ascontiguousarray(X.T) @ np.ascontiguousarray(W9.T))
    b_b = T._buckets(np.ascontiguousarray(X[perm].T) @ np.ascontiguousarray(W9[:, perm].T))
    b_c = np.concatenate([T._buckets(np.ascontiguousarray(X[:, a:a + 37].T) @ np.ascontiguousarray(W9.T)) for a in range(0, Nw, 37)])
    ck("D1 classic, nine men: the scoring pass's 0.01 bucket of every lineup in every world is the exact integer sum of its men's 0.02 units, "
       "whatever the player order or the world chunking the float32 product runs in",
       bool(np.array_equal(b_a, want9)) and bool(np.array_equal(b_b, want9)) and bool(np.array_equal(b_c, want9)))
    idx6 = np.stack([rs.choice(P, 6, replace=False) for _ in range(K)]).astype(np.int32)
    W6 = T.lineup_matrix(idx6, P, cpt_mult=1.5)
    want6 = np.clip(3 * u[idx6[:, 0]] + 2 * u[idx6[:, 1:]].sum(axis=1), 0, T._NB - 1).T
    s_a = T._buckets(np.ascontiguousarray(X.T) @ np.ascontiguousarray(W6.T))
    s_b = T._buckets(np.ascontiguousarray(X[perm].T) @ np.ascontiguousarray(W6[:, perm].T))
    ck("D2 showdown, the captain at 1.5x: 1.5 x a 0.02-lattice score is a whole hundredth, so every lineup's bucket is exact (3 x the captain's units + 2 x the flex units) in any order",
       bool(np.array_equal(s_a, want6)) and bool(np.array_equal(s_b, want6)))
    hi = np.asarray([[round(2222 * 0.02, 2)] * 9], dtype=np.float32).T            # nine 44.44s: 399.96, the top bucket
    ck("D3 at the top of the board's range (399.96 points, where float32 spacing is 0.0039 after the x100) the bucket is still exact",
       int(T._buckets(np.ascontiguousarray(hi.T) @ np.ones((9, 1), dtype=np.float32))[0, 0]) == 39996)
    # Why the lattice is load-bearing: a captain at 1.5x a 0.01-grid score sits
    # on a HALF bucket, and there float32 summation order decides the bucket.
    v = np.asarray([17.865, 13.635, 15.13, 12.635, 16.49, 1.34], dtype=np.float32)
    f1 = np.float32(0)
    for x in v:
        f1 = np.float32(f1 + x)
    f2 = np.float32(0)
    for x in v[::-1]:
        f2 = np.float32(f2 + x)
    bf = T._buckets(np.asarray([[f1, f2]], dtype=np.float32))[0]
    ck("D4 and it is the lattice, not luck: six off-lattice scores (0.005 grid, a captain on a 0.01 grid) summing to 77.095 land in bucket 7709 one way round and 7710 the other",
       int(bf[0]) != int(bf[1]), str(bf.tolist()))
    gsrc = open(os.path.join(_ROOT, "tests", "combo_audit_guards.py")).read()
    ck("D5 the guards that hold the parallel classic paths to the old ones still feed them lattice scores (0.02 for scoring and portfolio, the tournament's own 0.02 grid)",
       "_fa_Xs = (_np_fa.round(_fa_rng.gamma(2.0, 5.0, size=(_fa_P, _fa_N)) * 50) / 50).astype(_np_fa.float32)" in gsrc
       and "_X_tn = _np_tn.round(_X_tn * 50) / 50" in gsrc
       and "the scoring pass split over processes returns the serial pass's numbers" in gsrc)
    # the scorer the boards' arrays go through keeps the lattice (no rescale on the engine path)
    pp = inspect.getsource(__import__("nfl_dfs_sim").player_pool)
    ck("D6 the engine's arrays reach the pool rounded to the hundredth and never rescaled; its defenses and kickers are whole numbers off the same game",
       'p["arr"] = _np.round(p["arr"], 2).tolist()' in pp and "eng_dst[t] = [round(float(x), 2) for x in a]" in pp
       and dk_scoring.NFL_OFF["pass_yd"] == 0.04 and dk_scoring.NFL_OFF["rec_yd"] == 0.1 and dk_scoring.NFL_OFF["rush_yd"] == 0.1)

    print("=" * 72)
    print("E. Two quarterbacks on one team (nfl_dfs_csim._split_int, the engine's QB line)")
    print("=" * 72)
    qa = [{"name": "Q1", "pos": "QB", "team": "A", "means": {"pass_att": 18, "pass_cmp": 12, "pass_yd": 120, "pass_td": 0.8, "int": 0.4}},
          {"name": "Q2", "pos": "QB", "team": "A", "means": {"pass_att": 82, "pass_cmp": 55, "pass_yd": 560, "pass_td": 3.6, "int": 1.6}},
          {"name": "W", "pos": "WR", "team": "A", "means": {"rec_tgt": 9, "rec": 6, "rec_yd": 80, "rec_td": 0.5}},
          {"name": "R", "pos": "RB", "team": "A", "means": {"rush_att": 16, "rush_yd": 70, "rush_td": 0.5}}]
    one = lambda v: np.asarray([v], dtype=np.int64)                         # noqa: E731
    qbox = {"pts": one(0), "pass_att": one(30), "comp": one(25), "pass_yds": one(200), "td_pass": one(0), "int": one(3),
            "rush_att": one(20), "rush_yds": one(80), "td_rush": one(0), "fum_lost": one(0), "kneels": one(0)}
    dq = G.deal_team(qa, [0, 1, 2, 3], qbox, np.random.default_rng(1))
    team_ok = all(int(dq[0][k][0] + dq[1][k][0]) == int(qbox[s][0])
                  for k, s in (("pass_att", "pass_att"), ("pass_cmp", "comp"), ("pass_yd", "pass_yds"), ("int", "int")))
    # E0: one quarterback, nearly every team: his line is the box's, untouched
    # (the two-quarterback split below never runs for him).
    one_q = [qa[1], qa[2], qa[3]]
    d1q = G.deal_team(one_q, [0, 1, 2], qbox, np.random.default_rng(1))
    ck("E0 one quarterback: his attempts, completions, yards, touchdowns and interceptions are the box's own, untouched",
       all(int(d1q[0][k][0]) == int(qbox[s][0]) for k, s in (("pass_att", "pass_att"), ("pass_cmp", "comp"),
                                                              ("pass_yd", "pass_yds"), ("pass_td", "td_pass"), ("int", "int"))))
    ck("E1 two quarterbacks: the team's attempts, completions, yards and interceptions are split between them to the whole throw and add back exactly",
       team_ok)
    q1 = {k: int(dq[0][k][0]) for k in ("pass_att", "pass_cmp", "pass_yd", "int")}
    # Until 2026-11-26 each stat was split by largest remainder ON ITS OWN, so
    # a quarterback could be handed more interceptions than his incompletions
    # (5 attempts, 5 completions, 1 interception on this box) or passing yards
    # with no completion. Low: Sleeper's 2026 feeds read had no team with two
    # projected quarterbacks, but a starter in doubt brings one.
    ck("E2 each quarterback's own line is one a game can produce: interceptions within his incompletions, no yards without a completion",
       q1["int"] <= q1["pass_att"] - q1["pass_cmp"] and (q1["pass_cmp"] > 0 or q1["pass_yd"] == 0), str(q1))
    # E3: the same on the engine's own boxes, every world, two and three
    # quarterbacks, the team's passing line exact.
    e3_bad, e3_n = 0, 0
    for nq in (2, 3):
        qs = [dict(qa[1], name=f"Q{j}", means=dict(qa[1]["means"], pass_att=10 + 30 * j)) for j in range(nq)]
        pool_q = qs + [qa[2], qa[3]]
        dq3 = G.deal_team(pool_q, list(range(len(pool_q))), bh, np.random.default_rng(40 + nq))
        lines_q = [dq3[j] for j in range(nq)]
        att3, cmp3, yd3, td3, in3 = (sum(np.asarray(l[k]) for l in lines_q) for k in ("pass_att", "pass_cmp", "pass_yd", "pass_td", "int"))
        e3_bad += int((att3 != bh["pass_att"]).sum() + (cmp3 != bh["comp"]).sum() + (yd3 != bh["pass_yds"]).sum()
                      + (td3 != bh["td_pass"]).sum() + (in3 != bh["int"]).sum())
        for l in lines_q:
            a_, c_, y_, t_, i_ = (np.asarray(l[k]) for k in ("pass_att", "pass_cmp", "pass_yd", "pass_td", "int"))
            e3_bad += int(((c_ > a_) | (i_ > a_ - c_) | (t_ > c_) | ((c_ == 0) & (y_ != 0)) | (a_ < 0)).sum())
        e3_n += len(bh["pts"])
    ck("E3 on the engine's own boxes (two and three quarterbacks, every world): each man's completions sit within his attempts, his "
       "interceptions within his incompletions, his touchdowns within his completions, no yards without a completion, and the "
       "team's attempts, completions, yards, touchdowns and interceptions add back exactly",
       e3_bad == 0, f"{e3_bad} violations over {e3_n} team-games")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if (detail and not cond) else ""))

    run(ck)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
