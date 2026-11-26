"""Guards for the portfolio controls and the owner's roster restrictions (2026-11-26; dfs_portfolio.py,
dfs_tourney.greedy_cover's `limits`, the boards' rule masks, nfl_dfs's pick `rules` / `portfolio` and the
sheet's search, static/dfspick.js's controls and dfsConcentration). The owner's priorities 5 and 6: exposure,
team and overlap caps on a portfolio, concentration shown on every portfolio, and roster rules (the stack, the
bring-back, a team maximum, a minimum salary) for OUR entries -- never the field's.

What each group proves:
  * the cover: uncapped, the new greedy is the old one line for line (a frozen copy of the old loop is the
    reference); capped, every cap holds at every reported size, and each step takes the BEST FEASIBLE candidate
    (a brute-force reference that counts by hand); a cover the caps starve stops short and says which caps;
  * the rules: the masks equal a per-row evaluation; the sheet's search under rules equals a brute force on the
    picker's own optimizer pool (forced players too); a board keeps its FIELD exactly with rules and caps set,
    every listed and portfolio lineup keeps the rules, the caps hold, and a board without options is today's;
  * the pick: shapes, ranges, conflicts refused with the rule named, the queue round trip, the stamp;
  * the screen: no inline handlers, names escaped, the payload, and the JavaScript concentration equal to the
    Python one on the same rows (rounding included).

Hermetic: DraftKings, Sleeper's pool and the depth gate are stubs (tests/guards_picker.py's), nothing is fetched.
numpy-free checks always run (CI's runner has no numpy); the numpy ones say so when skipped.

    python3 tests/guards_auto_portfolio.py       # exits 1 on any failed check
"""
import inspect
import itertools
import json
import os
import random
import shutil
import subprocess
import sys

os.environ.setdefault("VIGIL_NO_BG", "1")
os.environ.setdefault("VIGIL_POOL_CACHE_DIR", "")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _guarded(ck, label, fn, *a):
    try:
        fn(*a)
    except Exception as e:
        import traceback
        ck(label + " (the checks raised)", False, f"{type(e).__name__}: {e} " + traceback.format_exc()[-700:])


# ---- the cover ------------------------------------------------------------------------------------------------
def _old_greedy(np, P, k):
    """greedy_cover as it stood at 5696b7d, verbatim but for the name: the reference the uncapped cover must equal."""
    P = np.asarray(P)
    K, N = P.shape
    if not K or not N:
        return [], []
    rows_at_once = max(1, int(4_000_000 // max(N, 1)))
    chosen = []
    cov = np.zeros(N, dtype=np.float64)
    p_any = []
    for _ in range(min(k, K)):
        gain = np.empty(K, dtype=np.float64)
        for a in range(0, K, rows_at_once):
            blk = P[a:a + rows_at_once].astype(np.float64, copy=False)
            gain[a:a + rows_at_once] = np.maximum(blk - cov[None, :], 0.0).sum(axis=1)
        if chosen:
            gain[chosen] = -1.0
        j = int(np.argmax(gain))
        chosen.append(j)
        cov = np.maximum(cov, P[j].astype(np.float64))
        p_any.append(float(cov.mean()))
    return chosen, p_any


def _cap(pct, s):
    """The cap arithmetic written out on its own (dfs_portfolio.cap_count): floor(pct x s / 100), never 0 above 0%."""
    return 0 if pct <= 0 else max(1, (pct * s) // 100)


def _brute_capped(P, k, members, lead, pcap, tcap, max_ov, sizes):
    """The capped cover by hand: at step n every candidate is checked by counting the chosen ones (no running
    totals), and the best gain among the feasible is taken (lowest index on a tie). Returns the chosen list."""
    K, N = len(P), len(P[0])
    sizes = sorted(set(s for s in sizes if s <= k) | {k})
    chosen, cov = [], [0.0] * N
    for n in range(1, min(k, K) + 1):
        s = next(x for x in sizes if x >= n)
        best, bj = None, None
        for c in range(K):
            if c in chosen:
                continue
            ok = True
            for pl in members[c]:
                if pcap[pl] >= 0 and sum(1 for j in chosen if pl in members[j]) + 1 > _cap(pcap[pl], s):
                    ok = False
            if tcap[lead[c]] >= 0 and sum(1 for j in chosen if lead[j] == lead[c]) + 1 > _cap(tcap[lead[c]], s):
                ok = False
            if max_ov is not None and any(len(set(members[c]) & set(members[j])) > max_ov for j in chosen):
                ok = False
            if not ok:
                continue
            g = sum(max(0.0, P[c][w] - cov[w]) for w in range(N))
            if best is None or g > best + 1e-12:
                best, bj = g, c
        if bj is None:
            break
        chosen.append(bj)
        cov = [max(cov[w], P[bj][w]) for w in range(N)]
    return chosen


def _cover_checks(ck, np, T, D):
    rng = np.random.default_rng(20261126)
    same = []
    for K, N, k in ((40, 300, 8), (200, 50, 20), (7, 9, 10), (30, 1, 5)):
        P = rng.random((K, N)).astype(np.float32)
        P[3] = P[5]                                          # a twin pair: the tie-break must hold too
        same.append(T.greedy_cover(P, k) == _old_greedy(np, P, k))
        # caps that never bind: 100% everywhere, an overlap of the whole roster
        mem = rng.integers(0, 60, size=(K, 6))
        L = D.Limits(mem, rng.integers(0, 4, size=K), [100] * 60, [100] * 4, 6, (5, k))
        same.append(T.greedy_cover(P, k, limits=L) == _old_greedy(np, P, k) and not L.reports[-1]["short"])
    zero = T.greedy_cover(np.zeros((0, 5)), 3) == ([], []) and T.greedy_cover(np.zeros((4, 0)), 3) == ([], [])
    ck("the cover with no caps is the old cover line for line: every shape tried (twins included) chooses the same "
       "entries in the same order with the same coverage as a frozen copy of the 5696b7d loop, and caps that cannot "
       "bind (100%, an overlap of the whole roster) change nothing",
       all(same) and zero, str(same))

    # caps that bind: player 40%, team 50%, overlap 3, held at 5, 10 and 20 entries
    holds, best, shorts = [], [], []
    for trial in range(6):
        K, N, k, S, NP, NT = 90, 40, 20, 6, 30, 4
        P = rng.random((K, N))
        mem = np.stack([rng.choice(NP, size=S, replace=False) for _ in range(K)])
        lead = rng.integers(0, NT, size=K)
        pcap = [40] * NP
        pcap[0] = -1                                          # a forced man: exempt
        pcap[1] = 0                                           # a 0% override: in no entry
        pcap[2] = 100                                         # a 100% override: free
        tcap = [50, 50, -1, 50]
        L = D.Limits(mem, lead, pcap, tcap, 3, (5, 10, 20))
        ch, pa = T.greedy_cover(P, k, limits=L)
        ok = True
        for s in (5, 10, 20):
            pre = ch[:s]
            if not pre:
                continue
            for pl in range(NP):
                c = sum(1 for j in pre if pl in mem[j])
                if pcap[pl] >= 0 and c > _cap(pcap[pl], s):
                    ok = False
            for t in range(NT):
                if tcap[t] >= 0 and sum(1 for j in pre if lead[j] == t) > _cap(tcap[t], s):
                    ok = False
        if any(len(set(mem[a]) & set(mem[b])) > 3 for a, b in itertools.combinations(ch, 2)):
            ok = False
        if any(1 in mem[j] for j in ch):
            ok = False
        truth = [float(P[ch[:i + 1]].max(axis=0).mean()) for i in range(len(ch))]
        ok = ok and all(abs(a - b) < 1e-12 for a, b in zip(pa, truth))
        holds.append(ok)
        best.append(ch == _brute_capped(P.tolist(), k, mem.tolist(), lead.tolist(), pcap, tcap, 3, (5, 10, 20)))
        shorts.append(L.reports[-1])
    ck("capped, every cap holds at every reported size (a player at 40% of 5, 10 and 20 entries, a team leading at "
       "most half, two entries sharing at most 3; a forced man exempt, a 0% override in no entry, a 100% one free), "
       "the coverage reported is still the best chosen entry's chance per world, and EVERY step takes the best "
       "candidate the caps allow -- the same entries in the same order as a brute force that counts each cap by hand",
       all(holds) and all(best), f"holds={holds} best={best} reports={shorts[:2]}")

    # a cover the caps starve: one player in every candidate, capped at 20% of 10 -> two entries, then nothing
    P = rng.random((25, 30))
    mem = np.concatenate([np.zeros((25, 1), dtype=int), np.stack([rng.choice(np.arange(1, 40), size=5, replace=False)
                                                                  for _ in range(25)])], axis=1)
    pc = [20] + [-1] * 39
    L = D.Limits(mem, np.zeros(25, dtype=int), pc, [-1], None, (10,))
    ch, _pa = T.greedy_cover(P, 10, limits=L)
    rep = L.reports[-1]
    ck("caps that leave too few candidates stop the cover short of k and say so: two entries of ten (a player in "
       "every candidate, capped at 20%), the report short, naming the player cap with every candidate left refused "
       "by it -- never a cap quietly broken to fill the portfolio",
       len(ch) == 2 and rep["short"] and rep["entries"] == 2 and rep["stopped_by"] == {"player": 23}
       and rep["candidates"] == 23, str(rep))
    src = inspect.getsource(T.greedy_cover)
    ck("the caps are a constraint INSIDE the greedy step (the blocked candidates set below every feasible gain before "
       "the argmax), not a filter on the uncapped answer; portfolio_vs_field and portfolio pass `limits` through, "
       "None by default",
       "gain[limits.blocked(len(chosen) + 1)] = -2.0" in src
       and inspect.signature(T.portfolio_vs_field).parameters["limits"].default is None
       and inspect.signature(T.portfolio).parameters["limits"].default is None
       and "greedy_cover(P[gi], k, limits=limits)" in inspect.getsource(T.portfolio_vs_field))


# ---- the rule masks -----------------------------------------------------------------------------------------
def _mask_checks(ck, np, T, D):
    g = random.Random(4)
    teams = [("AAA", "BBB"), ("BBB", "AAA"), ("CCC", "DDD"), ("DDD", "CCC")]
    players = []
    for tm, op in teams:
        for pos, n in (("QB", 1), ("RB", 3), ("WR", 5), ("TE", 2), ("DST", 1)):
            for j in range(n):
                players.append({"name": f"{pos}{j} {tm}", "pos": pos, "team": tm, "opp": op,
                                "salary": g.randrange(3000, 9000, 100), "cpt_salary": 0})
    by = {}
    for i, p in enumerate(players):
        by.setdefault(p["pos"], []).append(i)
    rows = []
    for _ in range(3000):
        rb = g.sample(by["RB"], 2)
        wr = g.sample(by["WR"], 3)
        fl = g.choice([x for x in by["RB"] + by["WR"] if x not in rb + wr])
        rows.append([g.choice(by["QB"]), *rb, *wr, g.choice(by["TE"]), fl, g.choice(by["DST"])])
    idx = np.asarray(rows)
    rules = D.Rules(2, "required", 3, 47000)

    def by_hand(r):
        lu = [players[i] for i in r]
        qb = lu[0]
        catch = [players[i] for i in r[3:8]]
        stack = sum(1 for p in catch if p["team"] == qb["team"] and p["pos"] in ("WR", "TE"))
        bring = any(p["team"] == qb["opp"] and p["pos"] in ("WR", "TE") for p in catch)
        per = max(sum(1 for p in lu if p["team"] == t) for t in {p["team"] for p in lu})
        return [stack >= 2, bring, per <= 3, sum(p["salary"] for p in lu) >= 47000]
    masks = D.classic_rules_mask(idx, players, rules)
    hand = [by_hand(r) for r in rows]
    ok_c = len(masks) == 4 and all(bool(masks[k][1][i]) == hand[i][k] for k in range(4) for i in range(len(rows)))
    forb = D.classic_rules_mask(idx, players, D.Rules(None, "forbidden", None, None))[0][1]
    ok_f = all(bool(forb[i]) == (not hand[i][1]) for i in range(len(rows)))
    srows = np.asarray([g.sample(range(len(players)), 6) for _ in range(2000)])
    for p in players:
        p["cpt_salary"] = int(round(1.5 * p["salary"]))
    sm = D.showdown_rules_mask(srows, players, D.Rules(None, None, 3, 40000))
    ok_s = len(sm) == 2 and all(
        bool(sm[0][1][i]) == (max(sum(1 for j in r if players[j]["team"] == t) for t in {players[j]["team"] for j in r}) <= 3)
        and bool(sm[1][1][i]) == (players[r[0]]["cpt_salary"] + sum(players[j]["salary"] for j in r[1:]) >= 40000)
        for i, r in enumerate(srows.tolist()))
    run, first = D.apply_masks(np.ones(4, dtype=bool), [("a", np.array([1, 1, 0, 0], bool)), ("b", np.array([0, 0, 1, 1], bool)),
                                                        ("c", np.array([1, 1, 1, 1], bool))])
    ck("the boards' rule masks equal the rules read lineup by lineup: on 3,000 classic rows the stack (QB + 2 of his "
       "receivers and tight ends), the bring-back required and forbidden, a team maximum (the defense counts for its "
       "team) and a minimum salary; on 2,000 showdown rows the team maximum and the salary with the captain at his "
       "price; and the masks are read in order, the first that leaves nothing named",
       ok_c and ok_f and ok_s and not run.any() and first == "b", str((ok_c, ok_f, ok_s, first)))
    import nfl_dfs as N
    ck("the sheet's queue check reads the board's team cap by value (the web worker has no numpy) and the two agree",
       N._BOARD_TEAM_CAP == T.CL_MAX_PER_TEAM, str((N._BOARD_TEAM_CAP, T.CL_MAX_PER_TEAM)))


# ---- the pick ---------------------------------------------------------------------------------------------------
def _pick_checks(ck, N, D):
    import guards_picker as G
    import simulate
    csv_c, _pool_c, _g = G._classic_slate()
    rows_c = simulate.parse_dk_csv(csv_c)
    csv_s, _pool_s, _gs = G._showdown_slate()
    rows_s = simulate.parse_dk_csv(csv_s)
    f = lambda nm, tm: {"name": nm, "team": tm}

    def bad(raw):
        try:
            N.pick_parse(raw)
            return False
        except ValueError:
            return True
    plain = N.pick_parse({"force": [f("Ace Hawk", "AAA")]})
    full = N.pick_parse({"rules": {"stack": 2, "bring": "required", "team_max": "4", "min_salary": 49000},
                         "portfolio": {"player_pct": 40, "team_pct": 50, "max_overlap": 6,
                                       "players": [{"name": "Ace Hawk", "team": "aaa", "max_pct": 25}]}})
    ck("a pick carries the owner's roster rules and portfolio caps beside the names: a players-only pick parses to "
       "exactly the old three keys, a pick of rules and caps alone is a pick, unset values (any, 100%, 0 salary) are "
       "no rule, out-of-range and malformed ones are refused, and the parse is idempotent through the queue's JSON",
       sorted(plain) == ["captain", "exclude", "force"]
       and full["rules"] == {"stack": 2, "bring": "required", "team_max": 4, "min_salary": 49000}
       and full["portfolio"]["players"] == [("Ace Hawk", "AAA", "", 25)]
       and N.pick_parse({"rules": {"bring": "any", "min_salary": 0}, "portfolio": {"player_pct": 100}}) is None
       and bad({"rules": {"stack": 5}}) and bad({"rules": {"bring": "maybe"}}) and bad({"rules": {"team_max": 2.5}})
       and bad({"portfolio": {"player_pct": 0}}) and bad({"portfolio": {"max_overlap": 9}})
       and bad({"portfolio": {"players": [{"name": "Ace Hawk"}]}}) and bad({"rules": "stack"})
       and N.pick_parse(json.loads(json.dumps(N.pick_dump(full)))) == full
       and N.pick_parse(json.loads(json.dumps(N.pick_dump(plain)))) == plain, str(full))

    # a two-game slate priced $3,000-$3,900 a man: its dearest lineup spends $34,100, under a $40,000 minimum
    cheap_rows = []
    for gi, (a, h) in enumerate((("AAA", "BBB"), ("CCC", "DDD"))):
        game = f"{a}@{h} 11/01/2026 01:00PM ET"
        for tm in (a, h):
            for pos, n in (("QB", 1), ("RB", 2), ("WR", 3), ("TE", 1), ("DST", 1)):
                for j in range(n):
                    pid = 9000 + len(cheap_rows)
                    nm = f"{pos}{j} {tm}"
                    cheap_rows.append(G._row(pos, nm, pid, pos if pos in ("QB", "TE", "DST") else f"{pos}/FLEX",
                                             3000 + 100 * (len(cheap_rows) % 10), game, tm, 10))
    cheap = simulate.parse_dk_csv(G._HEAD + "\n" + "\n".join(cheap_rows))

    def res(raw, mode="classic", rows=None, board=False):
        pk, why = N.pick_resolve(N.pick_parse(raw), rows or rows_c, mode, board=board)
        return pk, why or ""
    ok_pk, _ = res({"rules": {"stack": 2, "team_max": 3}, "portfolio": {"player_pct": 50, "players": [
        {"name": "Cal Hawk", "team": "AAA", "max_pct": 20}]}})
    cases = {
        "team max under forced": res({"force": [f("Ace Hawk", "AAA"), f("Cal Hawk", "AAA"), f("Gus Hawk", "AAA")],
                                      "rules": {"team_max": 2}})[1],
        "stack over team max": res({"rules": {"stack": 3, "team_max": 3}})[1],
        "house stack over team max": res({"rules": {"team_max": 1}}, board=True)[1],
        "board house cap": res({"rules": {"stack": 3}}, board=True)[1],
        "board house cap forced": res({"rules": {"stack": 3}, "force": [f("Ace Hawk", "AAA"), f("Cal Hawk", "AAA"),
                                                                         f("Gus Hawk", "AAA"), f("Hal Hawk", "AAA")]},
                                      board=True)[1],
        "cap on forced": res({"force": [f("Cal Hawk", "AAA")], "portfolio": {"players": [
            {"name": "Cal Hawk", "team": "AAA", "max_pct": 30}]}})[1],
        "cap unknown": res({"portfolio": {"players": [{"name": "Nobody Here", "max_pct": 30}]}})[1],
        "min salary": res({"rules": {"min_salary": 40000}}, rows=cheap)[1],
        "sd stack": res({"rules": {"stack": 1}}, "showdown", rows_s)[1],
        "sd team max": res({"rules": {"team_max": 2}}, "showdown", rows_s)[1],
        "sd overlap": res({"portfolio": {"max_overlap": 6}}, "showdown", rows_s)[1],
        "bring forbidden": res({"force": [f("Ace Hawk", "AAA"), f("Finn Bear", "BBB")],
                                "rules": {"bring": "forbidden"}})[1],
    }
    ck("conflicts among the owner's own choices are refused at the queue by arithmetic, the rule named, never resolved "
       "quietly: a team maximum under his forced count of one team, a stack the team maximum cannot seat, a stack of "
       "three on a board whose house rule holds three a team (allowed when he forces them), a cap on a man he forced "
       "into every lineup, a cap naming nobody on the slate, a minimum salary above the dearest lineup around his "
       "picks, the classic-only rules on a showdown, a showdown team maximum under three, an overlap past the roster, "
       "a forced receiver facing his forced quarterback under no bring-back; a sound set resolves with both attached",
       ok_pk is not None and ok_pk.rules == D.Rules(2, None, 3, None) and ok_pk.port.players == {"Cal Hawk": 20}
       and "over your maximum of 2" in cases["team max under forced"] and "3 players from AAA" in cases["team max under forced"]
       and "4 players from one team, over your maximum of 3" in cases["stack over team max"]
       and "1 of his receivers is 2 players from one team" in cases["house stack over team max"]
       and "the board holds 3 players from one team" in cases["board house cap"]
       and cases["board house cap forced"] == ""
       and "forced into every lineup" in cases["cap on forced"] and "not on this slate" in cases["cap unknown"]
       and "spends $34,100, under your $40,000 minimum" in cases["min salary"] and "classic rules" in cases["sd stack"]
       and "leaves no showdown lineup" in cases["sd team max"] and "at most 5" in cases["sd overlap"]
       and "no bring-back" in cases["bring forbidden"], json.dumps(cases))
    st0 = N.pick_stamp(N.Pick(frozenset({"A"}), frozenset({"B"}), None))
    st1 = N.pick_stamp(ok_pk)
    ck("the stamp a build carries: a players-only pick's stamp is the old five keys exactly; with rules and caps it "
       "adds the rules (and in words) and the caps, nothing else",
       sorted(st0) == ["captain", "exclude", "force", "notes", "waived"]
       and sorted(set(st1) - set(st0)) == ["portfolio", "rules", "rules_text"]
       and st1["rules_text"] == ["the quarterback stacked with 2+ of his receivers and tight ends",
                                 "at most 3 players from one team"]
       and st1["portfolio"] == {"player_pct": 50, "players": {"Cal Hawk": 20}, "team_pct": None, "max_overlap": None},
       str(st1))


# ---- the sheet ---------------------------------------------------------------------------------------------------
def _brute(N, players, objective, sm, bm, rules, must=()):
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
                    if N._lineup_ok(lu, 50000, sm, bm, None, rules):
                        v = sum(N._value(p, objective) for p in lu)
                        best = v if best is None else max(best, v)
    return best


def _sheet_checks(ck, N, D):
    import guards_picker as G
    pool = G._fixture_pool6()
    N._set_ownership(pool)
    names = [p["name"] for p in pool]
    real = lambda lu, obj: sum(N._value({k: v for k, v in p.items() if k != "_pk"}, obj) for p in lu)
    g = random.Random(11)
    out, seen = [], 0
    for _ in range(34):
        obj, sm, bm = g.choice([("projection", 1, 0), ("ceiling", 2, 1), ("leverage", 2, 1), ("projection", 2, 0)])
        tm, ms, nb = g.choice([None, 3, 4]), g.choice([None, 45000, 48000, 49500]), g.random() < 0.3
        if nb:
            bm = 0
        rules = N.OptRules(tm, ms, nb) if (tm or ms or nb) else N.OptRules(3, None, False)
        must = tuple(g.sample(names, g.choice([0, 0, 1, 2])))
        ex = _brute(N, pool, obj, sm, bm, rules, must)
        fx = [dict(p, _pk=True) for p in pool if p["name"] in must]
        lu = N.optimize([p for p in pool if p["name"] not in must] + fx, 50000, obj, stack_min=sm, bring_min=bm,
                        rules=rules, fixed=fx or None)
        if ex is None:
            out.append(lu is None or not set(must) <= {p["name"] for p in lu})
            continue
        seen += 1
        out.append(bool(lu) and set(must) <= {p["name"] for p in lu} and abs(real(lu, obj) - ex) < 1e-6
                   and N._lineup_ok([{k: v for k, v in p.items() if k != "_pk"} for p in lu], 50000, sm, bm, None, rules))
    ck("the sheet's search under the owner's rules equals a brute force on the picker's own optimizer pool (four "
       "teams, a defense barring a quarter of it: the hardest terrain): random sets of a team maximum, a minimum "
       "salary and no bring-back, over every objective's stack, with and without forced men -- exact every time, "
       "every answer keeping every rule, and none where none exists (a QB + 2 stack under a maximum of 3 completed no "
       "fill for one stack team until the reserve, the owed-place check and a stack's own second round)",
       all(out) and seen >= 25, f"{out} seen={seen}")

    # the showdown sheet: the team maximum and the salary floor, brute-forced on the picker's showdown game
    sd = G._sd_sheet_pool(N)
    sd_ok = []
    for rl in (D.Rules(None, None, 4, None), D.Rules(None, None, None, 49000), D.Rules(None, None, 3, 47000)):
        pk = N.Pick(frozenset(), frozenset(), None, rl, None)
        best = None
        for c in sd:
            for fl in itertools.combinations([p for p in sd if p is not c], 5):
                lu = [c, *fl]
                tot = c["cpt_salary"] + sum(p["salary"] for p in fl)
                teams = [p["team"] for p in lu]
                if tot > 50000 or len(set(teams)) < 2 or (rl.min_salary and tot < rl.min_salary) \
                        or (rl.team_max and max(teams.count(t) for t in set(teams)) > rl.team_max):
                    continue
                got, ok = [], True
                for p in fl:
                    if not N._sd_allowed(p, c, got):
                        ok = False
                        break
                    got.append(p)
                if ok:
                    v = 1.5 * c["proj"] + sum(p["proj"] for p in fl)
                    best = v if best is None else max(best, v)
        bl = N.optimize_showdown_multi(sd, 50000, "projection", 3, rng=random.Random(1), pick=pk)
        top = 1.5 * bl[0][0]["proj"] + sum(q["proj"] for q in bl[0][1]) if bl else None
        keeps = all((not rl.min_salary or x[2] >= rl.min_salary)
                    and (not rl.team_max or max([q["team"] for q in [x[0]] + x[1]].count(t) for t in ("SEA", "NE")) <= rl.team_max)
                    for x in bl)
        sd_ok.append(best is not None and top is not None and abs(top - best) < 1e-6 and keeps)
    ck("the showdown sheet under a team maximum and a minimum salary equals a brute force of the same game (the "
       "captain counted for his team and at his price), and every entry it returns keeps both",
       all(sd_ok), str(sd_ok))

    # the sheet's multi-entry under the caps (Sleeper's pool and the depth gate stubbed, as the picker's guards do)
    import dfslog
    import nfl_dfs_sim
    csv_c, pool_c, gated_c = G._classic_slate()
    arrs = G._fake_pool(pool_c, 1500)
    with G._patched((nfl_dfs_sim, "player_pool", lambda week, n=3000, preseason=False, **kw: arrs),
                    (N, "_apply_depth", G._fake_depth(gated_c)), (dfslog, "adjust", lambda sport, players: 0)):
        kw = dict(week=8, objective="ceiling", n_lineups=6, uniq=2)
        base = G._safe(N.build, csv_c, **kw)
        capped = G._safe(N.build, csv_c, pick={"rules": {"stack": 2, "team_max": 4, "min_salary": 49000},
                                               "portfolio": {"player_pct": 50, "team_pct": 34, "max_overlap": 5}}, **kw)
        one = G._safe(N.build, csv_c, pick={"portfolio": {"player_pct": 50}}, week=8, objective="ceiling", n_lineups=1)
    lus = [[r for r in lu["lineup"]] for lu in (capped.get("lineups") or [])]
    teams_of = {nm: v["team"] for nm, v in pool_c.items()}
    sal_of = {nm: v for nm, v in ((r["name"], r["salary"]) for lu in lus for r in lu)}
    n = len(lus)
    expo_ok = n >= 2 and all(sum(1 for lu in lus if nm in {r["name"] for r in lu}) <= D.cap_count(50, 6)
                             for nm in {r["name"] for lu in lus for r in lu})
    lead_ok = all(sum(1 for lu in lus if next(r["team"] for r in lu if r["slot"] == "QB") == t) <= D.cap_count(34, 6)
                  for t in set(teams_of.values()))
    ov_ok = all(len({r["name"] for r in a} & {r["name"] for r in b}) <= 5 for a, b in itertools.combinations(lus, 2))
    rule_ok = all(sum(sal_of[r["name"]] for r in lu) >= 49000
                  and max(sum(1 for r in lu if teams_of[r["name"]] == t) for t in set(teams_of.values())) <= 4
                  and sum(1 for r in lu if r["pos"] in ("WR", "TE") and r["team"] == next(q["team"] for q in lu if q["slot"] == "QB")) >= 2
                  for lu in lus)
    conc = capped.get("concentration") or {}
    ck("a sheet Run of six lineups under the owner's rules and caps: every lineup keeps the rules (a QB + 2 stack, at "
       "most 4 a team, $49,000+), no player in more than 3 of the 6 (50%), no team leading more than 2 (34%), no two "
       "lineups sharing more than 5; the stack replacing the objective's is not news (2 is the ceiling's own); the "
       "card carries the concentration and the stamp the caps; the unpicked Run carries the concentration too "
       "(display only); a one-lineup Run says the caps are for a multi-entry",
       not capped.get("error") and expo_ok and lead_ok and ov_ok and rule_ok
       and conc.get("entries") == n and (capped.get("pick") or {}).get("portfolio", {}).get("player_pct") == 50
       and (base.get("concentration") or {}).get("entries") == len(base.get("lineups") or [])
       and any("multi-entry" in x for x in (one.get("pick") or {}).get("notes") or []),
       str((capped.get("error"), n, expo_ok, lead_ok, ov_ok, rule_ok, conc.get("flags"), (one.get("pick") or {}).get("notes"))))


# ---- concentration, Python and JavaScript ----------------------------------------------------------------------
def _rows(lineups, lead_slot="QB"):
    out = []
    for lu in lineups:
        out.append({"lineup": [{"name": nm, "team": tm, "slot": (lead_slot if i == 0 else "FLEX")}
                               for i, (nm, tm) in enumerate(lu)]})
    return out


def _conc_checks(ck, D):
    rows = _rows([[("Q1", "AAA"), ("W1", "AAA"), ("W2", "BBB")], [("Q1", "AAA"), ("W1", "AAA"), ("W3", "BBB")],
                  [("Q2", "BBB"), ("W1", "AAA"), ("W3", "BBB")], [("Q1", "AAA"), ("W4", "CCC"), ("W5", "CCC")]])
    c = D.concentration(rows, forced=("W1",))
    c0 = D.concentration(rows[:2])
    ck("concentration: each player's exposure by player, the team each entry is built around (its quarterback's or "
       "captain's) and players per entry, the most players two entries share and which two; a player above the "
       "threshold flagged (50% unless the owner set a cap) but never a forced man; two entries one player apart "
       "flagged; nothing flagged under three entries",
       c["entries"] == 4 and c["size"] == 3
       and [(p["name"], p["n"], p["pct"], p["flag"], p["forced"]) for p in c["players"][:3]]
       == [("Q1", 3, 75.0, True, False), ("W1", 3, 75.0, False, True), ("W3", 2, 50.0, False, False)]
       and [(t["team"], t["lead_n"], t["lead_pct"], t["players_avg"]) for t in c["teams"]]
       == [("AAA", 3, 75.0, 1.5), ("BBB", 1, 25.0, 1.0), ("CCC", 0, 0.0, 0.5)]
       and c["max_overlap"] == 2 and c["overlap_pair"] == [1, 2]
       and c["flags"] == ["Q1 is in 3 of 4 entries (75%), above 50%", "entries 1 and 2 share 2 of 3 players"]
       and c0["flags"] == [] and D.concentration(rows, warn_pct=80)["flags"] == ["entries 1 and 2 share 2 of 3 players"],
       json.dumps(c))
    if not shutil.which("node"):
        ck("(skipped for a stated reason: node is not on this machine's path; CI's runner has it) the JavaScript "
           "concentration equals the Python one", True)
        return
    g = random.Random(8)
    sets = []
    for n_e, size, lead in ((16, 9, "QB"), (7, 6, "CPT"), (20, 9, "QB"), (3, 6, "CPT"), (1, 9, "QB")):
        pool = [(f"P{i}", g.choice(["AAA", "BBB", "CCC"])) for i in range(14)]
        lus = [g.sample(pool, size) for _ in range(n_e)]
        if n_e > 2:
            lus[2] = list(lus[1][:-1]) + [next(p for p in pool if p not in lus[1])]
        sets.append((_rows(lus, lead), ["P0"], g.choice([None, 30, 50])))
    py = [D.concentration(r, forced=f, warn_pct=w) for r, f, w in sets]
    js_path = os.path.join(_ROOT, "static", "dfspick.js")
    harness = ("const vm = require('vm'); const fs = require('fs');"
               "const doc = {getElementById: () => null, querySelector: () => null, addEventListener: () => {}};"
               "const ctx = {document: doc, localStorage: {getItem: () => null, setItem: () => {}}, console, Map, Set, JSON,"
               " Math, String, Object, Array, Number, parseInt};"
               "vm.createContext(ctx); vm.runInContext(fs.readFileSync(" + json.dumps(js_path) + ", 'utf8'), ctx);"
               "ctx.__in = " + json.dumps([[r, f, w] for r, f, w in sets]) + ";"
               "vm.runInContext('globalThis.__out = __in.map(([r, f, w]) => dfsConcentration(r, f, w))', ctx);"
               "console.log(JSON.stringify(ctx.__out));")
    try:
        r = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=60)
        js = json.loads(r.stdout.strip().splitlines()[-1])
        same = js == json.loads(json.dumps(py))
        detail = "" if same else json.dumps([(a, b) for a, b in zip(js, py) if a != b])[:900]
    except Exception as e:
        same, detail = False, repr(e)
    ck("the tab's concentration (dfsConcentration, for whatever prefix it shows) equals dfs_portfolio.concentration "
       "on the same rows to the digit -- sixteen entries included, where Python's round() goes to even on 6.25 and "
       "JavaScript's up (both now round half up in integers) -- with forced men, both thresholds and a "
       "near-duplicate pair",
       same, detail)


# ---- the screen and the queue ----------------------------------------------------------------------------------
def _screen_checks(ck, N):
    js_path = os.path.join(_ROOT, "static", "dfspick.js")
    js = open(js_path, encoding="utf-8").read()
    app_js = open(os.path.join(_ROOT, "static", "app.js"), encoding="utf-8").read()
    inline = [h for h in ("onclick=", "onchange=", "oninput=", "onload=", "onerror=") if h in js]
    ck("the controls carry no inline handlers (one delegated change listener reads data-pk-set / data-pk-cap); the "
       "board shows the concentration of the portfolio rows on screen with the caps' report, and the sheet shows a "
       "classic multi-lineup Run's lineups (it showed the first only) and the concentration of its entries",
       not inline and 'data-pk-set="${grp}.${key}"' in js and "getAttribute(\"data-pk-set\")" in js
       and "dfsConcentrationHtml(dfsConcentration(portRows," in app_js and "_conc(d.lineups)" in app_js
       and "_conc(d.entries)" in app_js, str(inline))
    if not shutil.which("node"):
        ck("(skipped for a stated reason: node is not on this machine's path; CI's runner has it) the controls under node",
           True)
        return
    evil = "<img src=x onerror=alert(1)>"
    head = "Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame,Status\\n"
    csv_c = head + f"QB,{evil} (1),{evil},1,QB,7000,AAA@BBB,AAA,20,ACTIVE\\nWR,Bo Bear (2),Bo Bear,2,WR/FLEX,5000,AAA@BBB,BBB,12,ACTIVE"
    csv_s = head + (f"QB,{evil} (1),{evil},1,CPT,9000,AAA@BBB,AAA,20,ACTIVE\\nQB,{evil} (2),{evil},2,FLEX,6000,AAA@BBB,AAA,20,ACTIVE\\n"
                    "WR,Bo Bear (3),Bo Bear,3,CPT,7500,AAA@BBB,BBB,12,ACTIVE\\nWR,Bo Bear (4),Bo Bear,4,FLEX,5000,AAA@BBB,BBB,12,ACTIVE")
    harness = """
const vm = require('vm'); const fs = require('fs');
const els = {}; const L = {};
const mk = (id) => (els[id] = els[id] || {id, innerHTML: '', value: '', focus() {}, setSelectionRange() {}, getAttribute: () => null});
const doc = {getElementById: mk, querySelector: () => null, addEventListener: (t, fn) => { (L[t] = L[t] || []).push(fn); }};
const store = {};
const ctx = {document: doc, localStorage: {getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; }},
  console, fetch: async () => ({json: async () => ({})}), Map, Set, JSON, Math, String, Object, Array, Number, parseInt};
vm.createContext(ctx); vm.runInContext(fs.readFileSync(JS_PATH, 'utf8'), ctx);
mk('dfsSport').value = 'nfl'; mk('dfsPickSheet'); mk('dfsCsv').value = "CSV_C";
const ev = (attrs, value) => ({target: {value, getAttribute: (k) => (k in attrs ? attrs[k] : null)}});
const fire = (t, e) => (L[t] || []).forEach((fn) => fn(e));
const out = {};
vm.runInContext('dfsPickLoadSheet()', ctx);
out.none = vm.runInContext('JSON.stringify(dfsPickPayload("sheet"))', ctx);
fire('change', ev({'data-pk-set': 'rules.stack', 'data-pk-ctx': 'sheet'}, '2'));
fire('change', ev({'data-pk-set': 'rules.bring', 'data-pk-ctx': 'sheet'}, 'forbidden'));
fire('change', ev({'data-pk-set': 'rules.min_salary', 'data-pk-ctx': 'sheet'}, '49000'));
fire('change', ev({'data-pk-set': 'port.player_pct', 'data-pk-ctx': 'sheet'}, '40'));
fire('change', ev({'data-pk-set': 'port.max_overlap', 'data-pk-ctx': 'sheet'}, '6'));
vm.runInContext("_pkAct('sheet', 'cap', _PK.sheet.pool.findIndex((r) => r.name.includes('img')))", ctx);
fire('change', ev({'data-pk-cap': String(vm.runInContext("_PK.sheet.pool.findIndex((r) => r.name.includes('img'))", ctx)), 'data-pk-ctx': 'sheet'}, '25'));
out.cl = vm.runInContext('JSON.stringify(dfsPickPayload("sheet"))', ctx);
out.html = els.dfsPickSheet.innerHTML;
fire('change', ev({'data-pk-set': 'port.player_pct', 'data-pk-ctx': 'sheet'}, ''));
out.unset = vm.runInContext('JSON.stringify(dfsPickPayload("sheet").portfolio)', ctx);
els.dfsCsv.value = "CSV_S";
(L.input || []).forEach((fn) => fn({target: els.dfsCsv}));
fire('change', ev({'data-pk-set': 'rules.stack', 'data-pk-ctx': 'sheet'}, '2'));
fire('change', ev({'data-pk-set': 'rules.team_max', 'data-pk-ctx': 'sheet'}, '4'));
out.sd = vm.runInContext('JSON.stringify(dfsPickPayload("sheet"))', ctx);
out.banner = vm.runInContext("dfsPickBanner({pick: {force: [], exclude: [], captain: null, waived: [], notes: [], rules_text: ['EVIL'], portfolio: {player_pct: 40, players: {'EVIL': 10}, team_pct: null, max_overlap: 5}}})".replace(/EVIL/g, EVILS), ctx);
out.conc = vm.runInContext("dfsConcentrationHtml(dfsConcentration([{lineup: [{name: 'EVIL', team: 'AAA', slot: 'QB'}]}, {lineup: [{name: 'EVIL', team: 'AAA', slot: 'QB'}]}, {lineup: [{name: 'EVIL', team: 'AAA', slot: 'QB'}]}], [], null), {short: true, entries: 1, k: 5, stopped_by: {player: 3}})".replace(/EVIL/g, EVILS), ctx);
console.log(JSON.stringify(out));
"""
    for k, v in (("JS_PATH", json.dumps(js_path)), ("CSV_C", csv_c), ("CSV_S", csv_s), ("EVILS", json.dumps(evil.replace("'", "")))):
        harness = harness.replace(k, v)
    try:
        r = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=60)
        o = json.loads(r.stdout.strip().splitlines()[-1])
        cl = json.loads(o["cl"] or "null") or {}
        sd = json.loads(o["sd"] or "null") or {}
        parts = {"none": o["none"] == "null",
                 "rules": cl.get("rules") == {"stack": 2, "bring": "forbidden", "min_salary": 49000},
                 "caps": cl.get("portfolio", {}).get("player_pct") == 40 and cl["portfolio"].get("max_overlap") == 6
                 and [p["max_pct"] for p in cl["portfolio"].get("players") or []] == [25],
                 "names": cl.get("exclude") == [] and cl.get("force") == [],
                 "unset": "player_pct" not in (json.loads(o["unset"]) or {}),
                 "showdown": sd.get("rules") == {"team_max": 4},
                 "escaped": "<img" not in o["html"] and "<img" not in o["banner"] and "<img" not in o["conc"],
                 "report": "refused by the player cap" in o["conc"],
                 "controls": 'data-pk-set="rules.stack"' in o["html"]}
        ok = all(parts.values())
        detail = "" if ok else json.dumps({k: v for k, v in parts.items() if not v}) + " " + json.dumps(
            {k: o[k] for k in ("unset", "sd", "conc")})[:900]
    except Exception as e:
        ok, detail = False, repr(e)
    ck("the controls under node with the page's own listeners: nothing set sends no pick; a stack, no bring-back, a "
       "minimum salary, a player-wide cap, an overlap cap and a per-player cap (set on its chip) ride the payload "
       "beside empty name lists, as numbers; a cleared box leaves the payload; a showdown pool sends the team "
       "maximum and never the classic-only stack; a name that is an <img onerror> is escaped in the controls, the "
       "banner and the concentration, whose caps' report names the cap that starved it",
       ok, detail)


def _route_checks(ck, N):
    import app
    import dk
    import guards_picker as G
    csv_c, _p, _g = G._classic_slate()
    with G._sandbox(), G._patched((dk, "slate_for", lambda sport, draft_group_id=None, exclude_games=None:
                                   {"csv": csv_c, "draft_group_id": draft_group_id}),
                                  (dk, "contests", lambda sport, draft_group_id=None: [{"prize_pool": 2.0e6}]),
                                  (app, "_tourney_slate_start", lambda sport, dg: None)):
        body = {"sport": "nfl", "dg": 999, "kind": "classic",
                "pick": {"rules": {"stack": 2, "min_salary": 48500},
                         "portfolio": {"player_pct": 40, "players": [{"name": "Ace Hawk", "team": "AAA", "max_pct": 20}]}}}
        with app.app.test_request_context("/api/dfs/tourney/request", method="POST", json=body):
            a1 = app.api_dfs_tourney_request()
        with app.app.test_request_context("/api/dfs/tourney/request", method="POST",
                                          json=dict(body, pick={"rules": {"stack": 3, "team_max": 3}})):
            a2 = G._safe(app.api_dfs_tourney_request)
        reqs = app._tourney_requests()
    j1 = a1.get_json() if hasattr(a1, "get_json") else a1[0].get_json()
    j2, c2 = ((a2, 500) if isinstance(a2, dict) else (a2[0].get_json(), a2[1]) if isinstance(a2, tuple)
              else (a2.get_json(), 200))
    q = (reqs.get("999") or {}).get("pick") or {}
    ck("Build on the PC queues the whole pick -- the rules and the caps beside the names, in the JSON the PC parses "
       "again -- and answers with the stamp (the rules in words); a self-contradicting set is a 400 naming the rule",
       j1.get("queued") and q.get("rules") == {"stack": 2, "min_salary": 48500}
       and q.get("portfolio", {}).get("players") == [{"name": "Ace Hawk", "team": "AAA", "id": "", "max_pct": 20}]
       and N.pick_parse(q) is not None and N.pick_parse(q)["portfolio"]["player_pct"] == 40
       and "rules_text" in (j1.get("pick") or {}) and c2 == 400 and "over your maximum of 3" in (j2.get("error") or ""),
       str((j1, q, c2, j2)))


# ---- the boards -------------------------------------------------------------------------------------------------
def _board_checks(ck, np, T, N, D):
    import guards_picker as G
    f = lambda nm, tm: {"name": nm, "team": tm}
    csv_c, pool_c, gated_c = G._classic_slate()
    detail = {"id": 1, "name": "Guard Millionaire", "prize_pool": 2.0e6, "max_entries": 40000, "entry_fee": 20.0,
              "first_prize": 200000, "places_paid": 8000, "entered": 1000, "max_entries_per_user": 150,
              "payouts": [{"from": 1, "to": 1, "prize": 200000.0}, {"from": 2, "to": 10, "prize": 10000.0},
                          {"from": 11, "to": 8000, "prize": 40.0}]}
    slate = {"starts": "2026-11-01T18:00:00.0000000Z", "draft_group_id": 999,
             "contests": [{"id": 1, "prize_pool": 2.0e6, "entries": 1000}], "n_players": 104}
    kw = dict(n_sims=1000, opt_worlds=400, field_n=3000, cand_n=3000, cal_n=1000, week=8, preseason=False, seed=7,
              workers=1, probes=[], model="legacy", log=lambda *a: None, top=12, k_port=12, candidates=160)
    spied = []
    real_cover = T.greedy_cover

    def spy(P, k, limits=None):
        got = real_cover(P, k, limits=limits)
        if limits is None:
            spied.append(got == _old_greedy(np, P, k))
        return got
    rules = {"stack": 2, "bring": "required", "team_max": 3, "min_salary": 47000}
    port = {"player_pct": 50, "team_pct": 50, "max_overlap": 6,
            "players": [{"name": "Ace Hawk", "team": "AAA", "max_pct": 0}]}
    # the steering passes are shortened for the guard (both boards alike); the field is still the board's own
    with G._patched(*G._board_mocks(T, N, csv_c, pool_c, gated_c, slate, detail), (T, "OPT_FIELD_N", 3000),
                    (T, "OPT_FIELD_ITERS", 2), (T, "greedy_cover", spy)):
        base = T.build_nfl_classic(999, **kw)
        ruled = G._safe(T.build_nfl_classic, 999, pick=N.pick_parse({"rules": rules, "portfolio": port}), **kw)
        # a bring-back required for a forced quarterback whose opponent's starting catchers are all excluded: the queue
        # passes it (DraftKings lists the WR4/WR5/TE2), but they are the depth gate's, never ours -- no candidate
        none = G._safe(T.build_nfl_classic, 999, pick=N.pick_parse(
            {"force": [f("Ace Hawk", "AAA")], "exclude": [f(nm, "BBB") for nm in ("Finn Bear", "Gus Bear", "Hal Bear", "Kip Bear")],
             "rules": {"bring": "required"}}), **kw)
        none_why = T.last_refusal()
    ok_b = lambda b: isinstance(b, dict) and "players" in b
    pf0 = ((base or {}).get("results") or {}).get("1", {}).get("portfolio") or {}
    ck("a classic board built with no options is today's: its cover chose exactly what the frozen old cover chooses on "
       "the same matrix, its portfolio block carries only the added display-only concentration beside the old two "
       "keys, and there is no pick, no limits, no second solve",
       ok_b(base) and spied and all(spied) and sorted(pf0) == ["concentration", "entries", "p_any_top1_pct"]
       and "pick" not in base and pf0["concentration"]["entries"] == len(pf0["entries"]),
       str((spied, sorted(pf0))))
    pf = ((ruled or {}).get("results") or {}).get("1", {}).get("portfolio") or {}
    tm = {nm: v["team"] for nm, v in pool_c.items()}
    tm.update({p["name"]: p["team"] for p in (base or {}).get("players") or []})
    opp = {p["name"]: p.get("opp") for p in (base or {}).get("players") or []}
    sal = {p["name"]: p["salary"] for p in (base or {}).get("players") or []}
    posn = {p["name"]: p["pos"] for p in (base or {}).get("players") or []}

    def keeps(r):
        names = r["names"]
        qb = names[0]
        catch = names[3:8]
        st = sum(1 for nm in catch if tm[nm] == tm[qb] and posn[nm] in ("WR", "TE"))
        br = any(tm[nm] == opp[qb] and posn[nm] in ("WR", "TE") for nm in catch)
        per = max(sum(1 for nm in names if tm[nm] == t) for t in set(tm[nm] for nm in names))
        return st >= 2 and br and per <= 3 and sum(sal[nm] for nm in names) >= 47000

    def listed(b):
        rs = b["results"]["1"]
        for k in ("top_win", "top_ev", "top_top1", "top_top01", "top_money"):
            yield from rs[k]
        yield from rs["portfolio"]["entries"]
    ents = pf.get("entries") or []
    caps_ok = True
    for s in (5, 10, 12):
        pre = ents[:s]
        for nm in {n for r in pre for n in r["names"]}:
            c = sum(1 for r in pre if nm in r["names"])
            if c > D.cap_count(0 if nm == "Ace Hawk" else 50, s):
                caps_ok = False
        for t in set(tm.values()):
            if sum(1 for r in pre if tm[r["names"][0]] == t) > D.cap_count(50, s):
                caps_ok = False
    ov_ok = all(len(set(a["names"]) & set(b["names"])) <= 6 for a, b in itertools.combinations(ents, 2))
    field_same = ok_b(ruled) and ok_b(base) and (
        sorted((p["name"], p["field_pct"], p["opt_pct"], p.get("casual_pct")) for p in ruled["players"])
        == sorted((p["name"], p["field_pct"], p["opt_pct"], p.get("casual_pct")) for p in base["players"])
        and ruled["field_model"] == base["field_model"] and ruled["optimal_distinct"] == base["optimal_distinct"])
    ck("a classic board with the owner's rules and caps keeps the FIELD exactly (every player's field, best-lineup and "
       "casual share, the field's whole receipt); every listed and portfolio lineup keeps the rules (QB + 2 stack, a "
       "bring-back, at most 3 a team, $47,000+); the portfolio keeps the caps at 5, 10 and 12 entries (half the "
       "entries a player, a 0% override in none, half a team) and no two entries share more than 6; it reports what "
       "the caps did and the stamp carries both; with nothing forced or excluded there is no second solve",
       ok_b(ruled) and field_same and ents and all(keeps(r) for r in listed(ruled)) and caps_ok and ov_ok
       and isinstance(pf.get("limits"), dict) and "caps_changed_picks" in pf["limits"]
       and ruled["pick"]["rules"] == rules and ruled["pick"]["portfolio"]["players"] == {"Ace Hawk": 0}
       and ruled["pick"]["second_solve"] == 0,
       str(((ruled or {}).get("error"), field_same, caps_ok, ov_ok, pf.get("limits"), len(ents))))
    ck("rules that leave no candidate we may enter are refused naming the rule that emptied the board, never "
       "published as a board of empty lists",
       none is None and "meets your rules: a bring-back" in (none_why or "") and "left none" in (none_why or ""),
       str((none_why, (none or {}).get("error") if isinstance(none, dict) else None)))

    # the showdown board
    csv_s, pool_s, gated_s = G._showdown_slate()
    detail_s = {"id": 2, "name": "Guard Showdown", "prize_pool": 1.0e6, "max_entries": 30000, "entry_fee": 10.0,
                "first_prize": 100000, "places_paid": 6000, "entered": 500, "max_entries_per_user": 150,
                "starts": "2026-11-03T01:15:00.0000000Z",
                "payouts": [{"from": 1, "to": 1, "prize": 100000.0}, {"from": 2, "to": 10, "prize": 5000.0},
                            {"from": 11, "to": 6000, "prize": 20.0}]}
    slate_s = {"starts": "2026-11-03T01:15:00.0000000Z", "draft_group_id": 777,
               "contests": [{"id": 2, "prize_pool": 1.0e6, "entries": 1000}], "n_players": 48}
    kws = dict(n_sims=600, week=9, preseason=False, seed=3, workers=1, model="legacy", log=lambda *a: None, top=12,
               portfolio_sizes=(4, 8))
    with G._patched(*G._board_mocks(T, N, csv_s, pool_s, gated_s, slate_s, detail_s)):
        sbase = T.build_nfl_showdown(777, **kws)
        spk = G._safe(T.build_nfl_showdown, 777, pick=N.pick_parse(
            {"rules": {"team_max": 4, "min_salary": 46000},
             "portfolio": {"player_pct": 50, "team_pct": 75, "max_overlap": 4}}), **kws)
    own = lambda b: sorted((p["name"], p["field_cpt_pct"], p["field_flex_pct"], p["opt_cpt_pct"], p["opt_flex_pct"])
                           for p in b["players"])
    sd_ok = ok_b(spk) and ok_b(sbase)
    team_s = {nm: v["team"] for nm, v in pool_s.items()}
    sd_caps = sd_ok
    if sd_ok:
        for s in (4, 8):
            ents_s = spk["portfolio"][str(s)]["entries"]
            for nm in {p["name"] for r in ents_s for p in r["lineup"]}:
                if sum(1 for r in ents_s if nm in {p["name"] for p in r["lineup"]}) > D.cap_count(50, s):
                    sd_caps = False
            for t in ("AAA", "BBB"):
                if sum(1 for r in ents_s if r["captain_team"] == t) > D.cap_count(75, s):
                    sd_caps = False
            if any(len({p["name"] for p in a["lineup"]} & {p["name"] for p in b["lineup"]}) > 4
                   for a, b in itertools.combinations(ents_s, 2)):
                sd_caps = False

    def srows(b):
        for k in ("top_win", "top_ev", "top_top1", "top_money"):
            yield from b[k]
        for port_ in (b.get("portfolio") or {}).values():
            yield from port_.get("entries") or []
    sd_rules = sd_ok and all(r["salary"] >= 46000 and max(sum(1 for p in r["lineup"] if team_s[p["name"]] == t)
                                                         for t in ("AAA", "BBB")) <= 4 for r in srows(spk))
    sd_same = sd_ok and own(spk) == own(sbase) and spk["lineups_legal"] == sbase["lineups_legal"] \
        and spk["field_model"]["beta"] == sbase["field_model"]["beta"] \
        and spk["field_model"]["chalk"]["lineup"] == sbase["field_model"]["chalk"]["lineup"] \
        and spk["lineups_allowed"] < sbase["lineups_allowed"]
    base_ports = (sbase or {}).get("portfolio") or {}
    ck("a showdown board with the owner's rules and caps narrows only what we may enter -- the universe, the field's "
       "weights and chalk and the best-lineup shares are the plain board's -- every listed and portfolio lineup keeps "
       "the team maximum and the salary floor (the captain at his price), and both reported portfolios keep the "
       "caps; the plain board's portfolios carry the display-only concentration and nothing else new",
       sd_ok and sd_same and sd_rules and sd_caps and all("limits" in spk["portfolio"][k] for k in ("4", "8"))
       and all(sorted(v) == ["concentration", "entries", "p_any_basis", "p_any_top1_pct"] for v in base_ports.values()),
       str(((spk or {}).get("error"), sd_same, sd_rules, sd_caps, [sorted(v) for v in base_ports.values()])))


def run(ck):
    import dfs_portfolio as D
    import nfl_dfs as N
    _guarded(ck, "the pick's rules and caps", _pick_checks, ck, N, D)
    _guarded(ck, "concentration", _conc_checks, ck, D)
    _guarded(ck, "the sheet under rules and caps", _sheet_checks, ck, N, D)
    _guarded(ck, "the controls", _screen_checks, ck, N)
    _guarded(ck, "the Build queue", _route_checks, ck, N)
    try:
        import numpy as np
    except ImportError:
        np = None
    import dfs_tourney as T
    if np is None or not T.available():
        ck("(skipped for a stated reason: numpy is not installed here; the boards are the PC's) the capped cover, the "
           "rule masks and the boards", True)
        return
    _guarded(ck, "the capped cover", _cover_checks, ck, np, T, D)
    _guarded(ck, "the rule masks", _mask_checks, ck, np, T, D)
    _guarded(ck, "the boards", _board_checks, ck, np, T, N, D)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
