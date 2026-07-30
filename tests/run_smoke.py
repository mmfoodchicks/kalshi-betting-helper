#!/usr/bin/env python3
"""Vigil smoke suite — ground-truth checks for the paths where a silent bug
costs money. Run BEFORE shipping any change to the sim, grading, DFS
optimizer, or store schema:

    python3 tests/run_smoke.py            # offline checks (fast, no network)
    python3 tests/run_smoke.py --online   # + live checks against today's slate

Every test here encodes a bug class that actually shipped once:
  - accumulator key drift        (deep rerun KeyError: 'sb')
  - optimizer boundary math      (DFS lineup $20 over the cap)
  - grading semantics            (DNP graded as a loss; remainder shown as
                                  season-end; picks missing home_won)
  - mask/joint-odds correctness  (everything the combo builder prices)
Zero dependencies beyond the app itself. Exit code 0 = all passed.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# The whole run uses a throwaway DB so tests can write picks/props freely.
# Must happen before anything imports store.
_TMP = tempfile.mkdtemp(prefix="vigil-smoke-")
os.environ["KALSHI_DB"] = os.path.join(_TMP, "smoke.db")

PASS, FAIL = 0, []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL {name} — {detail}")


# ---------------------------------------------------------------- offline --
def t_imports():
    """Every module must import (catches syntax + import-time errors)."""
    import glob
    import importlib
    bad = []
    root = os.path.dirname(HERE)
    for f in sorted(glob.glob(os.path.join(root, "*.py"))):
        m = os.path.basename(f)[:-3]
        if m == "app":
            continue          # imports last (registers routes, heavier)
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append((m, repr(e)[:80]))
    check("all modules import", not bad, bad[:3])


def t_accumulator_keys():
    """Deep-season accumulator factories must cover every key _accum_box folds
    and every key deep_sim's box lines carry — the KeyError:'sb' class."""
    import inspect
    import deep_season
    import deep_sim
    src = inspect.getsource(deep_season._sim_one_season)
    import re
    tuples = re.findall(r'dict\.fromkeys\(\s*\(([^)]*)\)', src)
    fact_bat = {k.strip().strip('"\'') for k in tuples[0].split(",") if k.strip()}
    fact_pit = {k.strip().strip('"\'') for k in tuples[1].split(",") if k.strip()}
    acc = inspect.getsource(deep_season._accum_box)
    fold = re.findall(r'for k in \(([^)]*)\)', acc)
    fold_bat = {k.strip().strip('"\'') for k in fold[0].split(",") if k.strip()}
    fold_pit = {k.strip().strip('"\'') for k in fold[1].split(",") if k.strip()}
    check("bat accumulator covers folded keys", fold_bat <= fact_bat,
          f"missing {fold_bat - fact_bat}")
    check("pit accumulator covers folded keys", fold_pit <= fact_pit,
          f"missing {fold_pit - fact_pit}")
    check("game bat line covers folded keys",
          fold_bat <= set(deep_sim._new_bat_line()),
          f"missing {fold_bat - set(deep_sim._new_bat_line())}")
    check("game pit line covers folded keys",
          fold_pit <= set(deep_sim._new_pit_line()),
          f"missing {fold_pit - set(deep_sim._new_pit_line())}")


def t_fee_consistency():
    """All Kalshi taker-fee implementations must agree."""
    import baseball
    import bestbets
    import odds
    import combine
    fns = [odds.taker_fee_cents, bestbets._fee, baseball._kalshi_fee]
    if hasattr(combine, "_kalshi_fee"):
        fns.append(combine._kalshi_fee)
    bad = []
    for c in (1, 10, 35, 50, 65, 90, 99):
        vals = [round(fn(c), 1) for fn in fns]
        if len(set(vals)) != 1:
            bad.append((c, vals))
    check("taker fee identical across implementations", not bad, bad)


def t_dfs_cap_and_exclusivity():
    """The knapsack must never exceed the cap (odd salaries included) and must
    never take two players from one exclusive group (UFC bout)."""
    import random
    import simulate
    rng = random.Random(11)
    over = 0
    for trial in range(30):
        players = [{"name": f"p{i}", "salary": rng.randrange(3111, 11987),
                    "proj": rng.uniform(5, 45), "game": f"g{i // 2}"}
                   for i in range(24)]
        lu = simulate.dfs_optimize(players, 6, 50000, key="proj")
        if lu and sum(p["salary"] for p in lu) > 50000:
            over += 1
    check("knapsack never over the cap (odd salaries)", over == 0, f"{over}/30 over")
    # Bout-exclusivity bait: both fighters of g0 are absurdly good + cheap.
    players = [{"name": "trapA", "salary": 5000, "proj": 99, "game": "g0"},
               {"name": "trapB", "salary": 5000, "proj": 98, "game": "g0"}]
    players += [{"name": f"f{i}", "salary": 6000 + i * 137, "proj": 20 - i * 0.3,
                 "game": f"g{1 + i // 2}"} for i in range(20)]
    lu = simulate.dfs_optimize(players, 6, 50000, key="proj",
                               exclusive_group=lambda p: p["game"])
    games = [p["game"] for p in (lu or [])]
    check("exclusive group never doubled (UFC bout)",
          lu is not None and games.count("g0") <= 1,
          f"lineup games: {games}")


def t_store_lifecycle():
    """Record -> grade -> report on a temp DB: pick grading with home_won,
    the model_split three-way, DNP props voiding out of accuracy."""
    import random
    import store
    store.init_db()
    rng = random.Random(3)
    # 60 synthetic games where the deep read is sharper than the factor read.
    for i in range(60):
        truth = rng.random() < 0.5
        pm = min(0.95, max(0.05, (0.62 if truth else 0.38) + rng.uniform(-0.18, 0.18)))
        pd = min(0.95, max(0.05, (0.62 if truth else 0.38) + rng.uniform(-0.06, 0.06)))
        blend = 0.65 * pm + 0.35 * pd
        side = "home" if blend >= 0.5 else "away"
        store.record_mlb_pick(9000 + i, "2026-07-01", side, f"T{i}",
                              blend if side == "home" else 1 - blend, 50,
                              p_home_model=pm, p_home_deep=pd)
        pick_won = 1 if (truth and side == "home") or (not truth and side == "away") else 0
        store.set_mlb_grade(9000 + i, pick_won, "W", home_won=1 if truth else 0)
    rec = store.mlb_record()
    ms = rec.get("model_split") or {}
    check("model_split has factor/deep/blend",
          all((ms.get(k) or {}).get("n") == 60 for k in ("factor", "deep", "blend")),
          {k: (ms.get(k) or {}).get("n") for k in ("factor", "deep", "blend")})
    check("deep_grades returns 60 rows", len(store.deep_grades()) == 60,
          len(store.deep_grades()))
    # Blend tuner: with deep deliberately sharper, tuned weight must be >= prior.
    import baseball
    baseball._DEEP_W_CACHE.update({"t": 0.0, "w": None})
    w = baseball._deep_wp_weight()
    check("blend tuner raises weight when deep is sharper", w >= 0.35, w)
    # Prop void: a scratched player must leave the queue without touching accuracy.
    with store._lock, store._conn() as c:
        c.execute("INSERT INTO prop_log (date, game_pk, player_id, name, stat, line, graded)"
                  " VALUES ('2026-07-01', 1, 42, 'Scratch', 'hits', 1, 0)")
        pid = c.execute("SELECT id FROM prop_log WHERE player_id=42").fetchone()["id"]
    store.grade_prop_void(pid)
    with store._lock, store._conn() as c:
        row = dict(c.execute("SELECT graded, actual FROM prop_log WHERE id=?", (pid,)).fetchone())
    check("DNP prop voids (graded=2, no result)",
          row["graded"] == 2 and row["actual"] is None, row)
    check("void prop leaves the pending queue",
          all(p["id"] != pid for p in store.ungraded_props()))


def t_pick6_rules():
    """DK reality: batter stats are More-only; only pitcher Ks offer Less."""
    import baseball
    check("Pick 6 Less allowed only on Ks", baseball._PICK6_LESS_OK == {"Ks"},
          baseball._PICK6_LESS_OK)


def t_clock():
    """The app clock must be Eastern (UTC-4 or UTC-5)."""
    import datetime
    import clock
    off = clock.now_et().utcoffset().total_seconds() / 3600
    check("clock is Eastern (UTC-4/-5)", off in (-4.0, -5.0), off)
    mid = clock.midnight_et_epoch()
    import time
    check("ET midnight is within the last 24h", 0 <= time.time() - mid < 86400,
          time.time() - mid)


# ----------------------------------------------------------------- online --
def t_mask_math_live():
    """Brute-force the combo engine's joint odds on a real game: the popcount
    joint must equal counting sims by hand; marginals must match masks; prop
    ladders must be monotonic."""
    import random
    import baseball
    import clock
    import mlb_sim
    date = clock.today_et().isoformat()
    games = baseball.analyze_slate(date, date[:4])
    g = next((x for x in games if (x.get("live") or {}).get("state") == "Preview"),
             games[0] if games else None)
    if g is None:
        check("mask math (live)", False, "no games on today's slate")
        return
    gs = baseball._game_sim(g)
    n = gs["sim"]["n"]
    full = (1 << n) - 1
    cands = [c for c in gs["cands"] if c.get("mask") is not None]
    rng = random.Random(7)
    joint_bad = marg_bad = 0
    for _ in range(50):
        legs = rng.sample(cands, k=rng.randint(2, 5))
        jm = full
        for c in legs:
            jm &= c["mask"]
        brute = sum(1 for i in range(n)
                    if all(c["mask"] & (1 << i) for c in legs))
        if mlb_sim._popcount(jm) != brute:
            joint_bad += 1
    # build_candidates temperature-scales the moneyline and batter props against
    # the graded record, so for those types marg is deliberately NOT the raw mask
    # fraction. The types left raw must still match it exactly.
    raw_types = {"Total", "Run line", "RFI", "Ks"}
    cal_bad = []
    for c in cands:
        raw = mlb_sim._popcount(c["mask"]) / n
        if c["type"] in raw_types:
            if abs(c["marg"] - raw) > 1e-9:
                marg_bad += 1
        # Calibration must stay a sane monotone nudge, never a rewrite.
        elif not (0.0 < c["marg"] < 1.0) or abs(c["marg"] - raw) > 0.15:
            cal_bad.append((c["label"], round(raw, 4), round(c["marg"], 4)))
    from collections import defaultdict
    lad = defaultdict(list)
    for c in cands:
        kr = c.get("kref") or {}
        if kr.get("player") and kr.get("line") is not None:
            # A NO leg shares its twin's (type, player, line), so bucketing
            # without the side compared a market against its own complement and
            # called every YES/NO pair a monotonicity break.
            lad[(kr["t"], kr["player"], c.get("side", "yes"))].append(
                (kr["line"], c["marg"]))
    # "n+" ladders only go one way: a higher line can never be likelier. On the
    # NO side the sense flips (NO 3+ hits is likelier than NO 1+ hits).
    mono_bad = 0
    for (t, player, side), seq in lad.items():
        s = sorted(seq)
        for (l1, p1), (l2, p2) in zip(s, s[1:]):
            if l2 == l1:
                continue
            if (p2 > p1 + 1e-9) if side != "no" else (p2 < p1 - 1e-9):
                mono_bad += 1
    check(f"joint odds == brute force ({g['matchup']})", joint_bad == 0, joint_bad)
    check("uncalibrated marginals match masks", marg_bad == 0, marg_bad)
    check("calibration is a bounded nudge", not cal_bad, cal_bad[:3])
    check("prop ladders monotonic (per side)", mono_bad == 0, mono_bad)
    # The same-game joint must be quoted on the same calibrated scale as its legs,
    # or a slip's headline probability disagrees with the legs printed under it.
    item = mlb_sim.best_same_game([c for c in cands], n, 3, 0.45, 0, 3)
    if item:
        floor = min(l["prob_pct"] for l in item["legs"])
        check("same-game joint <= its smallest leg",
              item["combined_prob_pct"] <= floor + 1e-6,
              f"{item['combined_prob_pct']} vs {floor}")
        by_label = {c["label"]: c for c in cands}
        raw_ind, raw_j = 1.0, None
        masks = []
        for l in item["legs"]:
            c = by_label.get(l["pick"])
            if not c:
                masks = []
                break
            raw_ind *= mlb_sim._popcount(c["mask"]) / n
            masks.append(c["mask"])
        if masks:
            jm = masks[0]
            for m in masks[1:]:
                jm &= m
            raw_j = mlb_sim._popcount(jm) / n
            true_corr = (raw_j - raw_ind) * 100
            check("corr_delta reports correlation, not the calibration gap",
                  abs(item["corr_delta_pct"] - true_corr) <= 1.0,
                  f"reported {item['corr_delta_pct']} vs true {true_corr:.2f}")


def t_season_end_projection_live():
    """Team futures: no projected counting stat may sit below the real one."""
    import deep_cache
    import deep_season
    import clock
    payload, _ts = deep_cache.load("mlb_deep")
    agg = (payload or {}).get("agg")
    if not agg:
        check("season-end >= current (skipped)", True, "no cached deep run")
        return
    season = (payload or {}).get("season") or str(clock.today_et().year)
    det = deep_season.team_detail(agg, season, 147)
    bad = []
    for r in det.get("pitching") or []:
        rp = r.get("real") or {}
        if r.get("has_sim") and (r.get("k") or 0) < (rp.get("k") or 0):
            bad.append(("K", r["name"]))
    for b in det.get("batting") or []:
        rb = b.get("real") or {}
        if b.get("has_sim") and (b.get("h") or 0) < (rb.get("h") or 0):
            bad.append(("H", b["name"]))
    check("season-end projection never below current", not bad, bad[:4])


def main():
    online = "--online" in sys.argv
    print("== offline ==")
    t_imports()
    t_accumulator_keys()
    t_fee_consistency()
    t_dfs_cap_and_exclusivity()
    t_store_lifecycle()
    t_pick6_rules()
    t_clock()
    if online:
        print("== online (live data) ==")
        t_mask_math_live()
        t_season_end_projection_live()
    print(f"\n{PASS} passed, {len(FAIL)} failed"
          + ("" if online else "  (offline only — add --online for live checks)"))
    for name, detail in FAIL:
        print(f"  FAILED: {name} — {detail}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
