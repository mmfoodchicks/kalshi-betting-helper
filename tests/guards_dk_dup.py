"""Guards for the duplication study (2026-11-26, the owner's priority 2:
"exact metric definition, sample sizes, uncertainty"; research/reports/
dk_duplication.md and research/dk_duplication.py): the board's model figure
in the real standings' own definition, the summaries' new per-lineup copy
counts (players only, never an entrant), and the study's uncertainty.

    python3 tests/guards_dk_dup.py       # exits 1 on any failed check
    from tests.guards_dk_dup import run  # run(ck) inside the main suite
"""
import collections
import inspect
import math
import os
import random
import sys

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "research"))


def run(ck):
    import numpy as np
    import dfs_tourney as T
    rng = np.random.default_rng(11)
    base = np.stack([rng.choice(60, 9, replace=False) for _ in range(4000)])
    # duplicates in other slot orders count as the same lineup (classic: the same nine players)
    a = np.vstack([base, base[:50][:, ::-1], base[:5], base[:5][:, [1, 0, 2, 3, 4, 5, 6, 7, 8]]])
    got = T.field_duplication(a, None, "classic")
    cnt = collections.Counter(tuple(sorted(r)) for r in a.tolist())
    M = len(a)
    want = sum(v * (v - 1) for v in cnt.values()) / (M * (M - 1))
    ck("DD1 a classic board's model figure is the real standings' own statistic on its field sample: sum n(n-1) "
       "over M(M-1), a lineup the SET of its nine players (the same nine in other slots are one lineup, as "
       "dk_standings.summarize keys the real field) -- equal to a brute force and to field_model.collision's "
       "estimator", got is not None and abs(got["model_pair_collision"] - want) < 1e-15
       and abs(got["model_pair_collision"] - T._collision(a)) < 1e-15 and got["rows"] == M
       and got["experimental"] is True, f"{got} {want}")
    w = rng.random(5000)
    sd = T.field_duplication(np.zeros((5000, 6), dtype=int), w, "showdown")
    ck("DD2 a showdown board's is exact: its field is every legal lineup with its weight, so the chance two "
       "entries match is sum p^2 of the normalised weights; no weights, one row or zero mass returns None (never "
       "a number made up)", sd is not None and abs(sd["model_pair_collision"] - float(((w / w.sum()) ** 2).sum())) < 1e-15
       and T.field_duplication(np.zeros((5, 6)), None, "showdown") is None
       and T.field_duplication(np.zeros((1, 9)), None, "classic") is None
       and T.field_duplication(np.zeros((3, 6)), np.zeros(3), "showdown") is None)
    src = open(os.path.join(_ROOT, "dfs_tourney.py")).read()
    ck("DD3 both builds stamp it and nothing reads it: a description of the field model beside the real figure, "
       "never an input to a number on the board",
       '"field_duplication": field_duplication(idx_f, None, "classic"),' in inspect.getsource(T.build_nfl_classic)
       and '"field_duplication": field_duplication(idx, f, "showdown"),' in inspect.getsource(T.build_nfl_showdown)
       and src.count("field_duplication(") == 3)

    import dk_standings as DS
    l1 = "QB Alan Quade RB Bob Reyes RB Cal Reed WR Dan Wolfe WR Ed Wynn WR Fred Ward TE Gus Tate FLEX Hal West DST Bills"
    l1b = "QB Alan Quade RB Cal Reed RB Bob Reyes WR Ed Wynn WR Dan Wolfe WR Hal West TE Gus Tate FLEX Fred Ward DST Bills"
    l2 = "QB Alan Quade RB Bob Reyes RB Cal Reed WR Dan Wolfe WR Ed Wynn WR Fred Ward TE Gus Tate FLEX Hal West DST Chiefs"
    l3 = "QB Alan Quade RB Bob Reyes RB Cal Reed WR Dan Wolfe WR Ed Wynn WR Fred Ward TE Gus Tate FLEX Ivan Hart DST Chiefs"
    hdr = "Rank,EntryId,EntryName,TimeRemaining,Points,Lineup,,Player,Roster Position,%Drafted,FPTS\n"
    lines = [l1, l1b, l1, l1, l2, l2, l3]
    rows = [f"{i + 1},{100 + i},secretuser{i} (1/150),0,{150 - i}.0,{L},,,,," for i, L in enumerate(lines)]
    try:
        sm = DS.summarize(DS.parse(hdr + "\n".join(rows) + "\n"), cid=1, sha="x")
        du = sm["duplicates"]
    except Exception as e:                          # a summary that cannot be made fails here, never skips
        sm, du = {}, {"error": repr(e)}
    names1 = sorted(["Alan Quade", "Bob Reyes", "Cal Reed", "Dan Wolfe", "Ed Wynn", "Fred Ward", "Gus Tate",
                     "Hal West", "Bills"])
    names2 = sorted(names1[:] + ["Chiefs"])
    names2.remove("Bills")
    hist = du.get("copies_histogram") or {}
    ck("DD4 a standings summary now keeps the 20 most-copied builds (players and counts) and the copy-count "
       "histogram -- what a field model must reproduce, and what turns the collision's SE from a bound into a "
       "figure -- with no entrant anywhere in it",
       du.get("most_copied") == [{"lineup": names1, "copies": 4}, {"lineup": names2, "copies": 2}]
       and hist == {"1": 1, "2": 1, "4": 1} and sum(hist.values()) == du.get("distinct_lineups")
       and sum(int(k) * v for k, v in hist.items()) == 7
       and abs(du.get("distinct_pair_collision", 0) - (4 * 3 + 2 * 1) / (7 * 6)) < 1e-12
       and "secretuser" not in repr(sm), str(du))

    import dk_duplication as DD
    r = random.Random(5)
    K = 1500
    wts = [1.0 / (i + 1) ** 1.2 for i in range(K)]
    tot = sum(wts)
    p = [x / tot for x in wts]
    N = 2000
    cum, s_ = [], 0.0
    for x in p:
        s_ += x
        cum.append(s_)
    import bisect
    c = collections.Counter(bisect.bisect_left(cum, r.random() * cum[-1]) for _ in range(N))
    C = sum(v * (v - 1) for v in c.values()) / (N * (N - 1))
    h = {str(k): v for k, v in collections.Counter(c.values()).items()}
    b, pl = DD.se_bound(C, N, max(c.values())), DD.se_plugin(C, N, h)
    S2, S3 = sum(x * x for x in p), sum(x ** 3 for x in p)
    true_se = math.sqrt(4.0 * (N - 2) / (N * (N - 1.0)) * (S3 - S2 * S2) + 2.0 / (N * (N - 1.0)) * (S2 - S2 * S2))
    ck("DD5 the study's uncertainty: the plug-in SE (from the histogram) lands near the U-statistic's true SE and "
       "the bound (the largest lineup's share standing in for sum p^3) sits above the plug-in -- read as the "
       "SAMPLING floor under independent entries, which real entries are not",
       b is not None and pl is not None and b >= pl and 0.6 < pl / true_se < 1.6, f"bound {b} plug-in {pl} true {true_se}")
    g = DD.geo([1e-6, 4e-6])
    ck("DD6 across contests: the geometric mean and the log-scale SE (the honest spread, larger than any one "
       "contest's sampling floor)", abs(g["geo_mean"] - 2e-6) < 1e-15 and abs(g["log_se"] - math.log(4) / math.sqrt(2) / math.sqrt(2)) < 1e-12,
       str(g))


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
