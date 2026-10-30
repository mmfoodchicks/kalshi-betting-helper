"""Breadth for the DFS player picker's sheet search (research/reports/dfs_picker.md section 2): random picks on
the guard file's own pools (tests/guards_picker.py) against a constrained brute force over every legal lineup.

    python3 research/dfs_picker_stress.py classic 7 150     # seed, picks
    python3 research/dfs_picker_stress.py showdown 1 60

Classic (`nfl_dfs.optimize(fixed=)` on the suite's 34-man optimizer pool): one to four forced, up to three
excluded, the three objectives with their stack and bring-back rules. A pick the brute force finds no legal lineup
for must come back without one holding it (`refused`); every other must come back holding it at the brute force's
value exactly (`exact`), else `short` (a legal lineup below the optimum) or `miss` (none holding the pick).
`contradiction` counts a lineup the search found where the brute force found none (a rule the two read
differently). Showdown (`nfl_dfs.optimize_showdown_multi(pick=)` on the guard's 24-man game, the projection
objective, three entries): one to three forced, a forced captain a quarter of the time, up to four excluded; the
best entry holding the pick against the brute force under the showdown house rules for the free slots.

Pure Python (the sheet's search runs in the web worker without numpy); seconds to a minute a run."""
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.join(_HERE, "..", "tests"))
os.environ.setdefault("VIGIL_NO_BG", "1")

import nfl_dfs as N  # noqa: E402
import guards_picker as G  # noqa: E402


def classic(seed, n):
    pool = G._fixture_pool6()
    names = [p["name"] for p in pool]
    R = random.Random(seed)
    real = lambda lu, obj: sum(N._value({k: v for k, v in p.items() if k != "_pk"}, obj) for p in lu)
    st = {"exact": 0, "short": 0, "miss": 0, "refused": 0, "contradiction": 0}
    bad = []
    for _case in range(n):
        obj, sm, bm = R.choice([("projection", 1, 0), ("ceiling", 2, 1), ("leverage", 2, 1)])
        must = tuple(R.sample(names, R.choice([1, 2, 2, 3, 3, 4])))
        excl = tuple(R.sample([x for x in names if x not in must], R.choice([0, 0, 1, 2, 3])))
        for p in pool:
            p.pop("own", None)
        N._set_ownership(pool)
        avail = [p for p in pool if p["name"] not in excl]
        ex = G._brute_classic(N, avail, obj, sm, bm, must)
        fixed = [dict(p, _pk=True) for p in avail if p["name"] in must]
        lu = N.optimize([p for p in avail if p["name"] not in must] + fixed, 50000, obj, stack_min=sm, bring_min=bm,
                        fixed=fixed)
        names_lu = {p["name"] for p in lu or []}
        holds = bool(lu) and set(must) <= names_lu and not (set(excl) & names_lu)
        if ex is None:
            ok_lu = holds and N._lineup_ok([{k: v for k, v in p.items() if k != "_pk"} for p in lu], 50000, sm, bm)
            st["contradiction" if ok_lu else "refused"] += 1
        elif not holds:
            st["miss"] += 1
            bad.append(("MISS", obj, must, excl, round(ex, 2)))
        elif abs(real(lu, obj) - ex) < 1e-6:
            st["exact"] += 1
        else:
            st["short"] += 1
            bad.append(("SHORT", obj, must, excl, round(ex, 2), round(real(lu, obj), 2)))
    return st, bad


def showdown(seed, n):
    pool = G._sd_sheet_pool(N)
    names = [p["name"] for p in pool]
    R = random.Random(seed)
    st = {"exact": 0, "short": 0, "miss": 0, "refused": 0, "contradiction": 0}
    bad = []
    for case in range(n):
        force = set(R.sample(names, R.choice([1, 1, 2, 3])))
        cap = R.choice([None, None, None, R.choice(sorted(force))])
        excl = set(R.sample([x for x in names if x not in force], R.choice([0, 1, 2, 4])))
        pk = N.Pick(frozenset(excl), frozenset(force), cap)
        ex = G._sd_brute(N, [p for p in pool if p["name"] not in excl], pk)
        fixed = [dict(p, _pk=True) for p in pool if p["name"] in force]
        ours = [p for p in pool if p["name"] not in excl and p["name"] not in force] + fixed
        got = N.optimize_showdown_multi(ours, 50000, "projection", 3, rng=random.Random(case), pick=pk)
        got = [x for x in got if force <= ({x[0]["name"]} | {q["name"] for q in x[1]}) and (not cap or x[0]["name"] == cap)
               and not (excl & ({x[0]["name"]} | {q["name"] for q in x[1]}))]
        top = (1.5 * got[0][0]["proj"] + sum(q["proj"] for q in got[0][1])) if got else None
        if ex is None:
            st["contradiction" if got else "refused"] += 1
        elif not got:
            st["miss"] += 1
            bad.append(("MISS", sorted(force), cap, sorted(excl), round(ex, 2)))
        elif abs(top - ex) < 1e-6:
            st["exact"] += 1
        else:
            st["short"] += 1
            bad.append(("SHORT", sorted(force), cap, sorted(excl), round(ex, 2), round(top, 2)))
    return st, bad


if __name__ == "__main__":
    kind, seed, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    t0 = time.time()
    st, bad = (classic if kind == "classic" else showdown)(seed, n)
    print(kind, f"seed {seed}, {n} picks:", st, f"{time.time() - t0:.0f}s")
    for b in bad[:12]:
        print(b)
    sys.exit(1 if (st["short"] or st["miss"] or st["contradiction"]) else 0)
