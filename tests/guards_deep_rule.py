"""Guards for the registered NFL deep-weight read (2026-11-26; the owner's
decision 1: freeze the weight, settle the rule's ambiguities, pair per game,
cluster by week; research/reports/nfl_deep_weight_rule.md and
research/nfl_deep_weight_read.py).

A guard pins a number; only a reader pins its meaning (CLAUDE.md). So these
pin the DEFINITIONS the report states beside the code that computes them: the
weight, the game count, the clip, the week, the order of the clauses, the
calibrator never borrowed from the future, the statistics withheld before
the trigger -- and the read script's sha256 against the one the report
registers, so the code cannot drift from the text without a logged reason.

    python3 tests/guards_deep_rule.py       # exits 1 on any failed check
    from tests.guards_deep_rule import run  # run(ck) inside the main suite
"""
import hashlib
import math
import os
import random
import re
import sys
import tempfile

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "research"))
_REPORT = os.path.join(_ROOT, "research", "reports", "nfl_deep_weight_rule.md")
_SCRIPT = os.path.join(_ROOT, "research", "nfl_deep_weight_read.py")


def _rows(n_games, graded_upto, seed=1, deep_sd=0.08, drive_sd=0.12, t0=1789000000, per_week=15):
    rng = random.Random(seed)
    rows = []
    for g in range(n_games):
        kick = t0 + (g // per_week) * 7 * 86400 + (g % per_week) * 3600
        q = rng.uniform(0.2, 0.8)
        y = 1 if rng.random() < q else 0
        pd = min(0.95, max(0.05, q + rng.gauss(0, drive_sd)))
        pp = min(0.95, max(0.05, q + rng.gauss(0, deep_sd)))
        gr = 1 if g < graded_upto else 0
        for side, p1, p2, yy in (("H", pd, pp, y), ("A", 1 - pd, 1 - pp, 1 - y)):
            rows.append({"ticker": f"KXNFLGAME-X{g:03d}-{side}", "prob": p1, "ts": kick - 86400, "mkt": 0.5,
                         "event_ts": kick, "close_time": kick + 86400, "graded": gr,
                         "outcome": yy if gr else None,
                         "shadow": {"nfl_deep": {"prob": p2, "ts": kick - 86400}}})
    return rows


def run(ck):
    import nfl_deep_weight_read as R
    src_game = open(os.path.join(_ROOT, "nfl_game_sim.py")).read()
    m = re.search(r"^_DEEP_WEIGHT = ([0-9.]+)", src_game, re.M)
    ck("DR1 the read's weight is production's frozen weight (nfl_game_sim._DEEP_WEIGHT 0.30): the read judges the "
       "number the headline uses, and moving either without the other fails here",
       m is not None and float(m.group(1)) == R.WEIGHT == 0.30, f"{m and m.group(1)} {R.WEIGHT}")
    import predlog
    import inspect
    ck("DR2 the unit, the bar and the clip are the report's: 100 GAMES (200 rows), 2 SE one-sided, and the record's "
       "own clip (predlog.vs_market's 0.001-0.999), so the read's log loss is the record's",
       R.N_GAMES == 100 and R.Z == 2.0 and R.CLIP == (0.001, 0.999)
       and "min(0.999, max(0.001, get(p, m)))" in inspect.getsource(predlog.vs_market))

    rows = _rows(130, 99)
    out = R.read(rows, [])
    leak = [k for k in out if k not in ("graded_games", "graded_rows", "pending_games", "needed_games", "triggered")]
    ck("DR3 before the trigger the read returns counts only -- no log loss, no difference, no direction (the "
       "interim record was looked at once, by the audit, and the rule is not steered by looking again)",
       out["triggered"] is False and out["graded_games"] == 99 and not leak, f"{out}")

    rows = _rows(130, 112)
    rows_p = [dict(r) for r in rows]
    for r in rows_p:
        if r["ticker"].startswith("KXNFLGAME-X010-"):
            r["graded"], r["outcome"] = 0, None
    rows_v = [dict(r) for r in rows]
    for r in rows_v:
        if r["ticker"].startswith("KXNFLGAME-X010-"):
            r["graded"], r["outcome"] = 2, None
    a, b, c = R.read(rows, []), R.read(rows_p, []), R.read(rows_v, [])
    ck("DR4 the dataset is the first 100 graded games BY KICKOFF, and it waits while any game kicked off at or "
       "before the 100th is unsettled (reading early would let grading order choose the games); a voided market "
       "neither counts nor blocks",
       a["triggered"] and len(a["dataset"]) == 100 and a["dataset"][0] == "KXNFLGAME-X000"
       and a["dataset"][-1] == "KXNFLGAME-X099" and b["triggered"] is False
       and c["triggered"] and "KXNFLGAME-X010" not in c["dataset"] and c["dataset"][-1] == "KXNFLGAME-X100",
       f"{a.get('dataset', [])[-1:]} {b['triggered']} {c.get('dataset', [])[-1:]}")

    st = lambda m_, se: {"mean": m_, "se": se}
    ck("DR5 the clauses in their registered order: the blend not ahead of the drive sim on its point estimate -> 0; "
       "else the deep engine ahead by more than 2 SE -> 0.30 stays, earned; else INCONCLUSIVE -> 0 as well (the "
       "owner, 2026-11-26: 'returning the production weight to zero, consistent with ... a null sends it to 0')",
       R.decide(st(0.0, 0.01), st(0.5, 0.01)) == R.TO_ZERO and R.decide(st(-0.01, 0.01), st(0.0, 0.01)) == R.TO_ZERO
       and R.decide(st(0.01, 0.01), st(0.021, 0.01)) == R.EARNED
       and R.decide(st(0.01, 0.01), st(0.02, 0.01)) == R.INCONCLUSIVE
       and R.decide(st(0.01, 0.01), st(-0.05, 0.01)) == R.INCONCLUSIVE
       and R.WEIGHT_AFTER == {R.TO_ZERO: 0.0, R.INCONCLUSIVE: 0.0, R.EARNED: 0.30})

    worst = 1.0
    for seed in range(20):
        g, _p = R.games(_rows(40, 40, seed=seed, deep_sd=0.2, drive_sd=0.05), [])
        for x in g:
            worst = min(worst, (x["drive"] - x["blend"]) - R.WEIGHT * (x["drive"] - x["deep"]))
    ck("DR6 the two clauses cannot both hold: log loss is convex, so on every game the blend's gain over the drive "
       "sim is at least 0.30 x the deep engine's (a deep engine ahead implies a blend ahead)", worst >= -1e-12,
       f"{worst}")

    hist = [(100.0, (0.8, 0.5, 0.0, 10)), (200.0, (0.9, 0.5, 0.0, 20))]
    ck("DR7 the production comparison calibrates each drive row with the newest fit made AT OR BEFORE the row was "
       "logged (identity before the first snapshot) -- a later fit was made on later games, this one's included",
       R.params_at(hist, 50.0) is None and R.params_at(hist, 100.0)[0] == 0.8 and R.params_at(hist, 199.9)[0] == 0.8
       and R.params_at(hist, 1e12)[0] == 0.9)

    sym = max(abs(R.scale(p, (0.814, 0.5, 0.0, 1)) + R.scale(1 - p, (0.814, 0.5, 0.0, 1)) - 1)
              for p in [i / 200 for i in range(1, 200)])
    ck("DR8 calibrating each side's row on its own equals the board's 1 - cal(home) for the away side (the stored "
       "fits are b = 0, symmetric about 0.5) -- so the per-row production blend is the headline's", sym < 1e-12,
       f"{sym}")

    ds = [{"week": w, "x": x, "y": 0.0} for w, x in (("a", 1.0), ("a", 3.0), ("b", -2.0), ("c", 4.0), ("c", 0.0))]
    got = R.clustered(ds, "x", "y")
    mm = 6.0 / 5
    res = {"a": (1 - mm) + (3 - mm), "b": -2 - mm, "c": (4 - mm) + (0 - mm)}
    want = math.sqrt(3 / 2 * sum(v * v for v in res.values())) / 5
    ck("DR9 the SE is clustered by week: K/(K-1) x the sum over weeks of the squared within-week residual sum, "
       "over G^2 -- the per-GAME differences, never the rows (a game's two rows are mirror images, so counting "
       "rows halved the SE the old text quoted)",
       abs(got["mean"] - mm) < 1e-12 and abs(got["se"] - want) < 1e-12 and got["weeks"] == 3 and got["games"] == 5)

    import calendar
    ts = lambda y, mo, d, h: calendar.timegm((y, mo, d, h, 0, 0))
    thu, mon, tue = ts(2026, 11, 27, 1), ts(2026, 12, 1, 1), ts(2026, 12, 2, 1)   # ET: Thu 26th, Mon 30th, Tue 1st
    ck("DR10 a week is the Tuesday-to-Monday window in US Eastern: Thanksgiving's Thursday night and the next "
       "Monday night share one (Monday night is after midnight UTC), and the Tuesday after starts the next",
       R.week_key(thu) == R.week_key(mon) == "2026-11-24" and R.week_key(tue) == "2026-12-01",
       f"{R.week_key(thu)} {R.week_key(mon)} {R.week_key(tue)}")

    # the prospective test: fresh games only, counts before the first look, O'Brien-Fleming boundaries
    import math as _m
    t_last = 1789000000 + 6 * 7 * 86400 + 9 * 3600           # the 100th game of _rows' schedule (game 99)
    pre = R.prospective(_rows(100 + 110, 210, seed=4), [], t_last)          # 110 fresh: before the first look
    full = R.prospective(_rows(100 + 400, 100 + 400, seed=4, deep_sd=0.0, drive_sd=0.45), [], t_last)
    # a null: both models the same figure, so every look's gain is exactly 0 -> futility at the second look
    same = _rows(100 + 400, 100 + 400, seed=4, deep_sd=0.0, drive_sd=0.0)
    null = R.prospective(same, [], t_last)
    ok_b = all(abs(b - round(2.004 * _m.sqrt(3 / k), 3)) < 1e-9 for k, b in enumerate(R.PRO_BOUNDS_Z, start=1))
    ok_t = (abs(R.t_ppf(0.975, 7) - 2.364624) < 1e-5 and abs(R.t_ppf(0.975, 23) - 2.068658) < 1e-5
            and abs(R.t_ppf(0.995, 15) - 2.946713) < 1e-5 and abs(R.t_ppf(0.975, 10 ** 6) - 1.959966) < 1e-4
            and abs(R.t_boundary(2.004, 26) - 2.110) < 2e-3)
    last = (full.get("looks") or [{}])[-1]
    ck("DR14 the prospective test (report section 6): only games after the read's last kickoff, looks at 130 / "
       "260 / 390 with O'Brien-Fleming's one-sided 2.5% boundaries (2.004 x sqrt(3/k)) converted to Student t on "
       "the look's weeks - 1 (the plain z boundaries over 8-26 week clusters measured 3.7% false positives), the t "
       "quantiles exact to table values, counts only before the first look, one candidate (0.30) on the "
       "PRODUCTION comparison; a deep engine far better than a noisy drive model crosses, a null stops for "
       "futility at the second look, and every look's statistic is kept",
       R.PRO_LOOKS == (130, 260, 390) and ok_b and ok_t and R.PRO_FUTILITY_LOOK == 2
       and pre["fresh_graded_games"] == 110 and set(pre) == {"fresh_graded_games", "fresh_pending_games",
                                                            "looks_at", "decision", "power_note"}
       and pre["decision"] == R.CONTINUE and full["decision"] == R.REINSTATE
       and last.get("t", 0) > last.get("boundary_t", 1e9) and [x["games"] for x in full["looks"]][:1] == [130]
       and null["decision"] == R.FUTILE and [x["games"] for x in null["looks"]] == [130, 260],
       f"{pre} {full.get('decision')} {[(x['games'], round(x['t'], 2), round(x['boundary_t'], 2)) for x in full.get('looks', [])]} "
       f"{null.get('decision')}")
    # a deep figure much noisier than the drive's: the gain is not established (seed 1), or the blend is
    # clearly worse and every value the data allow, by the repeated-confidence bound, is negligible (seed 5)
    noisy = R.prospective(_rows(100 + 400, 100 + 400, seed=1, deep_sd=0.25, drive_sd=0.10), [], t_last)
    worse = R.prospective(_rows(100 + 400, 100 + 400, seed=5, deep_sd=0.5, drive_sd=0.10), [], t_last)
    ck("DR15 failing to establish an improvement is never written as 'no improvement': a stop with the weight at 0 "
       "says NOT ESTABLISHED and carries the 95% upper bound on the gain the data still allow; only a bound under "
       f"{R.NEGLIGIBLE} nats may be called 'no meaningful improvement' (the owner: 'Preserve the distinction')",
       noisy["decision"] in (R.STAYS_ZERO, R.FUTILE) and "NOT ESTABLISHED" in noisy.get("conclusion", "")
       and "not evidence that there is none" in noisy.get("conclusion", "")
       and noisy["looks"][-1]["upper95_rci"] > R.NEGLIGIBLE
       and worse["decision"] == R.FUTILE and worse["conclusion"].startswith("no meaningful improvement")
       and worse["looks"][-1]["upper95_rci"] < R.NEGLIGIBLE
       and "no meaningful improvement" in R._conclusion(0.0, 0.004) and "NOT ESTABLISHED" in R._conclusion(0.0, 0.006),
       f"{noisy.get('decision')} {noisy.get('conclusion')} | {worse.get('decision')} {worse.get('conclusion')}")

    # the reported bound survives the looks and the stopping: the repeated-confidence constant covers all three
    # looks at once (simulated here on Brownian paths, as the constant was), and it is wider than the
    # fixed-sample bound at every look
    import numpy as _np
    _rg = _np.random.default_rng(5)
    _S = _rg.standard_normal((400000, 3)).cumsum(axis=1)
    _Zs = _S / _np.sqrt(_np.arange(1, 4))
    _miss = (_Zs > R.RCI_C * _np.sqrt(3 / _np.arange(1, 4))).any(axis=1).mean()
    _st = {"mean": 0.01, "se": 0.005, "weeks": 20}
    ck("DR16 the bound after a sequential stop is a REPEATED confidence bound (C = 1.709 x sqrt(3/k), t on weeks - 1): "
       "it misses the true gain at ANY look about 5% of the time under early stopping, wider than the fixed-sample "
       "bound at every look; every prospective output carries the weekly-dependence power caveat (80% / 58% / 45%)",
       0.045 < _miss < 0.055 and all(R.rci_upper(_st, k) > R.upper_bound(_st) for k in (1, 2, 3))
       and "58%" in R.POWER_NOTE and "45%" in R.POWER_NOTE and "power_note" in pre,
       f"miss {_miss}")

    rep = open(_REPORT).read() if os.path.exists(_REPORT) else ""
    pinned = re.findall(r"read script sha256: `([0-9a-f]{64})`", rep)
    actual = hashlib.sha256(open(_SCRIPT, "rb").read()).hexdigest()
    ck("DR11 the report registers the read script by sha256 and it is this file: an edit to the code needs a "
       "logged reason and a new hash in the report's registration log, before the read",
       bool(pinned) and pinned[-1] == actual, f"pinned {pinned[-1:] if pinned else None} actual {actual}")

    saved = predlog._DB
    tmp = tempfile.mkdtemp()
    try:
        predlog._DB = os.path.join(tmp, "predlog.db")
        predlog.init_db()
        predlog.log_many("nfl", [("KXNFLGAME-T1-AAA", 0.6, 1000, 0.55, 900), ("KXNFLGAME-T1-BBB", 0.4, 1000, 0.45, 900),
                                 ("KXNFLGAME-T2-CCC", 0.7, 1000, 0.6, 950)])
        predlog.log_shadow("nfl_deep", [("KXNFLGAME-T1-AAA", 0.65, 1000), ("KXNFLGAME-T1-BBB", 0.35, 1000)])
        predlog.log_shadow("nfl_book", [("KXNFLGAME-T2-CCC", 0.62, 1000)])
        predlog.log_many("nba", [("KXNBAGAME-T3-DDD", 0.5, 1000)])
        predlog.log_shadow("nfl_deep", [("KXNBAGAME-T3-DDD", 0.5, 1000)])
        got = predlog.paired_rows("nfl", ["nfl_deep", "nfl_book"])
        tk = [r["ticker"] for r in got]
        r0 = got[0] if got else {}
    finally:
        predlog._DB = saved
    ck("DR12 the export pairs each owning row with its shadow rows ticker by ticker (first logged figures and "
       "times, the Kalshi price, kickoff, settlement), only the owner's bucket, only tickers a shadow logged",
       tk == ["KXNFLGAME-T1-AAA", "KXNFLGAME-T1-BBB", "KXNFLGAME-T2-CCC"]
       and r0.get("shadow", {}).get("nfl_deep", {}).get("prob") == 0.65 and "nfl_book" not in r0.get("shadow", {})
       and r0.get("event_ts") == 900 and r0.get("mkt") == 0.55 and r0.get("graded") == 0, f"{tk} {r0}")

    import app as A
    cl = A.app.test_client()
    bad = cl.get("/api/predlog/paired?owner=nfl").status_code
    wf = open(os.path.join(_ROOT, ".github", "workflows", "error-log.yml")).read()
    ck("DR13 the export is a route behind the app's auth and the error-log workflow pulls it to the data branch "
       "(errors/nfl-deep-pairs-latest.json), where the read script looks for it -- so the read can be run from the "
       "data branch, which the audit found impossible",
       bad == 400 and '"nfl-deep-pairs:/api/predlog/paired?owner=nfl&shadow=nfl_deep,nfl_book"' in wf
       and R.PAIRS_PATH == "errors/nfl-deep-pairs-latest.json", f"{bad}")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
