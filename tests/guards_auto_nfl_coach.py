"""Guards for the experimental NFL coaching / clock engine (R1): nfl_coach_sim,
its tables, the seal on the test games and the registered read. Self-registered
(tests/guards_auto_*.py run in combo_audit_guards' AUTO section); also runs
alone: python3 tests/guards_auto_nfl_coach.py

The eleven deterministic scenario tests of research/reports/nfl_coaching_audit.md
7.9 are here (S1-S11), each a hand-built state played over seeds.
"""
import csv
import gzip
import hashlib
import json
import math
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# The deep weight's registered read (research/reports/nfl_deep_weight_rule.md)
# sits on these four files as they were at 5696b7d. R1 must not move a byte.
PINNED = {
    "nfl_deep_sim.py": "22a221d7810495db3f8f7bb1bef7c4e16c3bbbf282902a95f133cecdabc29820",
    "nfl_deep_data.py": "dff6756fb9ceb5a746750557aaef3d8424a1b57e953ab6aef8cd0abd4375b022",
    "nfl_pbp.py": "660234badefc3e3b5f0e1417a48431e4ad8ff33aab5df8a207834faa4862865d",
    "research/data/nfl_deep_tables.json": "8c118b8fe741566ba1a0f7fb9aa8f41a27a5578b25e0d4988dda059c131b870f",
}
SNAPS = ("run", "cmp", "inc", "sack", "int")


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(ck):
    import nfl_coach_sim as C
    import nfl_deep_sim as D
    import nfl_pbp as P
    from research import nfl_coach_data as CD

    print("  -- NC. The experimental coaching engine (R1): production untouched, the eleven scenarios, the seal")
    for rel, want in PINNED.items():
        ck(f"NC0 {rel} is byte-identical to the file the deep weight's read sits on", _sha(rel) == want, _sha(rel)[:16])
    src = open(os.path.join(ROOT, "nfl_coach_sim.py")).read()
    ck("NC0b the experimental engine writes to no predlog bucket and is imported by no production module",
       "predlog" not in src.replace("predlog bucket", "") and "log_shadow" not in src
       and not any("nfl_coach" in open(os.path.join(ROOT, f)).read()
                   for f in os.listdir(ROOT) if f.endswith(".py") and not f.startswith("nfl_coach")))
    ck("NC0c it has its own version constant and its own tables file",
       C.VERSION.startswith("coach-r1") and C.TABLES_PATH.endswith("nfl_coach_tables.json")
       and C.TABLES_PATH != D.TABLES_PATH)

    T = C.tables()
    lg = C.league_profile(T)
    meta = T.raw["meta"]
    ck("NC1 the tables are fitted on 2016-2023, selected on 2024, and say the test games were never read",
       meta["fit_seasons"] == list(range(2016, 2024)) and meta["select_season"] == 2024
       and "never read" in meta["sealed"] and "CC BY 4.0" in meta["source"], meta["sealed"])
    ck("NC1b onside recovery is the measured figure (this engine only; production's 12% untouched)",
       abs(T.onside_rec - meta["measured"]["onside_recovery"][0] / meta["measured"]["onside_recovery"][1]) < 1e-12
       and 0.04 < T.onside_rec < 0.11 and D._ONSIDE_RECOVERY == 0.12, round(T.onside_rec, 4))

    def play(start, seed, playoff=False, log=True, home=lg, away=lg):
        lg_ = [] if log else None
        res = C.play_game(home, away, random.Random(seed), T, neutral=True, playoff=playoff, log=lg_, start=start)
        return res, lg_

    # ---- S1: victory formation
    st = {"per": 4, "clk": 115, "off": 0, "down": 1, "dist": 10, "ytg": 70, "pts": (21, 20), "to": (3, 0)}
    bad = wins = 0
    for seed in range(300):
        res, log = play(st, seed)
        body = [e for e in log if e["k"] not in ("end", "fin", "2mw")]
        bad += any(e["k"] != "kneel" or not e.get("vf") for e in body) or not body
        wins += res["h"]["pts"] > res["a"]["pts"] and not res["ot"]
    ck("S1 leading by 1, first down, 1:55, opponent out of timeouts: a kneel every snap, no other play, the leader wins every seed",
       bad == 0 and wins == 300, f"bad {bad} wins {wins}")
    # ---- S2: the same against three timeouts
    st2 = dict(st, to=(3, 3))
    k1 = over = neg = 0
    for seed in range(600):
        res, log = play(st2, seed)
        first = next(e for e in log if e["k"] not in ("2mw",))
        k1 += first["k"] == "kneel"
        tm = [e for e in log if e["k"] == "tmo" and e["o"] == 1 and e["q"] == 4]
        over += len(tm) > 3
        neg += any(e["left"] < 0 for e in tm)
    ck("S2 the same with three timeouts against: (almost) no kneel on first down, never more than three timeouts, never below zero",
       k1 <= 30 and over == 0 and neg == 0, f"kneel on first down {k1}/600")
    # ---- S3: the hurry-up and the spike
    st3 = {"per": 4, "clk": 25, "off": 1, "down": 1, "dist": 10, "ytg": 50, "pts": (24, 20), "to": (0, 0),
           "pending": "run"}
    huddle = spikes = spike_bad = no_snap = 0
    for seed in range(800):
        res, log = play(st3, seed)
        first = next((e for e in log if e["k"] in SNAPS + ("spike", "fg", "kneel")), None)
        if first is None:
            no_snap += 1
            continue
        huddle += 25 - first["c"] > 24
        if first["k"] == "spike":
            spikes += 1
            i = log.index(first)
            nxt = next((e for e in log[i + 1:] if e["k"] in SNAPS + ("spike", "fg", "punt", "kneel", "pen")), None)
            # a spike costs a down and the clock is stopped after it: the next snap is seconds later
            spike_bad += nxt is not None and (nxt["d"] != first["d"] + 1 or first["c"] - nxt["c"] > 12)
    ck("S3 trailing by 4, 0:25, clock running, no timeouts: a spike or a hurried snap, never a huddle; the spike costs a down and stops the clock",
       huddle == 0 and spikes >= 40 and spike_bad == 0 and no_snap <= 200,
       f"spikes {spikes}/800 clock ran out first {no_snap} bad {spike_bad}")
    # ---- S4: the trailing defence's timeouts
    st4 = {"per": 4, "clk": 150, "off": 0, "down": 2, "dist": 6, "ytg": 60, "pts": (23, 20), "to": (3, 3),
           "pending": "run"}
    want = T.rate(T.to_def, (C.phase(4, 150), P.score_state(3), 3))
    n4, hit, over = 1500, 0, 0
    for seed in range(n4):
        res, log = play(st4, seed)
        hit += log[0]["k"] == "tmo" and log[0]["o"] == 1
        over += sum(1 for e in log if e["k"] == "tmo" and e["o"] == 1 and e["q"] == 4) > 3
    se = math.sqrt(want * (1 - want) / n4)
    ck("S4 the leader runs in bounds at 2:30: the trailing defence stops the clock at the learned rate, three times at most",
       abs(hit / n4 - want) < 4 * se + 1e-9 and over == 0 and 0.2 < want < 0.95, f"{hit / n4:.3f} vs learned {want:.3f}")
    none = 0
    for seed in range(300):
        res, log = play(dict(st4, to=(3, 0)), seed)
        none += any(e["k"] == "tmo" and e["o"] == 1 and e["q"] == 4 for e in log)
    ck("S4b and never once they are gone", none == 0, none)
    # ---- S5: the two-minute warning
    okw, firstw = True, 0
    for per in (2, 4):
        for seed in range(150):
            res, log = play({"per": per, "clk": 125, "off": 0, "down": 1, "dist": 10, "ytg": 60, "pts": (10, 10),
                             "to": (0, 0), "pending": "run", "half2_recv": 0}, seed)
            w = [e for e in log if e["k"] == "2mw" and e["q"] == per]
            okw = okw and len(w) == 1 and w[0]["c"] == 120
            firstw += log[0]["k"] == "2mw"
    ck("S5 a play ends in bounds at 2:05 of the second or fourth quarter: the clock stops at exactly 2:00, once "
       "(before the next snap unless that snap was hurried inside five seconds)", okw and firstw >= 285, firstw)
    # ---- S10 (and S5's other half, S11): whole games
    viol = {"tmo": 0, "clock": 0, "spot": 0, "score": 0, "warn": 0, "spike4": 0, "spike_stopped": 0, "logdiff": 0}
    n_ot = ties = tot_pts = 0
    N10 = 6000
    for seed in range(N10):
        res, log = play(None, 50000 + seed)
        if seed < 400:
            res2, _ = play(None, 50000 + seed, log=False)
            viol["logdiff"] += json.dumps([res2["h"], res2["a"]], sort_keys=True) != json.dumps(
                [res["h"], res["a"]], sort_keys=True)
        for side in ("h", "a"):
            t = res[side]["tmo"]
            viol["tmo"] += t[0] > 3 or t[1] > 3 or t[2] > 2
        used_ot = (600 - res["clk"]) if res["ot"] else 0
        viol["clock"] += res["elapsed"] != 3600 + used_ot
        n_ot += res["ot"]
        ties += res["tie"]
        tot_pts += res["h"]["pts"] + res["a"]["pts"]
        pts = [0, 0]
        warn = {}
        prev = None
        for e in log:
            k = e["k"]
            if k in SNAPS + ("kneel", "spike", "punt", "fg", "pen"):
                viol["spot"] += not 1 <= e["y"] <= 99
            if k == "2mw":
                warn[e["q"]] = warn.get(e["q"], 0) + 1
                viol["warn"] += e["c"] != 120
            if k == "spike":
                viol["spike4"] += e["d"] >= 4
                viol["spike_stopped"] += prev is None or prev["k"] not in ("run", "cmp", "sack") or bool(prev.get("ob"))
            if k in SNAPS and e.get("td"):
                pts[e["o"]] += 6
            elif k in SNAPS and e.get("sf"):
                pts[1 - e["o"]] += 2
            elif k == "rtd":
                pts[e["o"]] += 6
            elif k == "try" and e["ok"]:
                pts[e["o"]] += 2 if e["two"] else 1
            elif k == "fg" and e["res"] == "good":
                pts[e["o"]] += 3
            if k not in ("2mw",):
                prev = e
        viol["score"] += pts != [res["h"]["pts"], res["a"]["pts"]]
        viol["warn"] += warn.get(2) != 1 or warn.get(4) != 1 or 1 in warn or 3 in warn
    ck(f"S10 {N10} games: at most three timeouts a team-half (two in overtime), the clock adds to 3,600 s plus overtime, "
       "no snap at or past a goal line, the score is the sum of its scoring events",
       viol["tmo"] == 0 and viol["clock"] == 0 and viol["spot"] == 0 and viol["score"] == 0, str(viol))
    ck("S5b every game: one warning in the second quarter, one in the fourth, none in the first or third, each at 2:00",
       viol["warn"] == 0, viol["warn"])
    ck("S3b every game: no spike on fourth down, none with the clock stopped", viol["spike4"] == 0
       and viol["spike_stopped"] == 0, f"{viol['spike4']} {viol['spike_stopped']}")
    ck("S11 the passive log: every box identical with and without it (timeouts, spikes and kneels included)",
       viol["logdiff"] == 0, viol["logdiff"])
    ck("S10b the level is a football game's (40-49 points, 3-9% overtime, some ties)",
       40 < tot_pts / N10 < 49 and 0.03 < n_ot / N10 < 0.09 and 0 < ties < n_ot, f"{tot_pts / N10:.2f} {n_ot / N10:.4f} {ties}")
    # ---- S6 / S7: overtime by rule set
    st6 = {"per": 5, "clk": 600, "kick": 0, "pts": (20, 20)}
    okp = True
    ties = td_first = cont = to_over = 0
    for seed in range(1500):
        res, log = play(st6, seed)
        okp = okp and res["per"] == 5 and all(e["c"] <= 600 for e in log)
        ties += res["tie"]
        to_over += res["h"]["tmo"][2] > 2 or res["a"]["tmo"][2] > 2
        sc = [e for e in log if e["k"] in SNAPS and e.get("td")]
        ko = [e for e in log if e["k"] == "ko"]
        if sc and sc[0]["o"] == 1 and not any(e.get("to") for e in log[:log.index(sc[0])]) \
                and not any(e["k"] in ("punt", "fg") for e in log[:log.index(sc[0])]):
            td_first += 1
            cont += len(ko) >= 2
        if res["tie"]:
            okp = okp and res["clk"] == 0
    ck("S6 regular-season overtime: one 10:00 period, both teams possess (the game goes on after an opening touchdown), "
       "a level score at 0:00 is a tie, two timeouts a team",
       okp and ties > 0 and td_first > 50 and cont == td_first and to_over == 0,
       f"ties {ties} opening TDs {td_first} continued {cont}")
    st7 = {"per": 5, "clk": 900, "kick": 0, "pts": (20, 20)}
    ties = long_ = 0
    okq = True
    for seed in range(1500):
        res, log = play(st7, seed, playoff=True)
        ties += res["tie"]
        long_ += res["per"] >= 6
        okq = okq and any(e["c"] > 600 for e in log) and res["h"]["tmo"][2] <= 6
    ck("S7 postseason overtime: 15:00 periods, played until there is a winner, never a tie",
       ties == 0 and okq and long_ > 0, f"ties {ties} reached a second period {long_}")
    # ---- S8 / S9: the end of the half
    fg = 0
    for seed in range(300):
        res, log = play({"per": 2, "clk": 3, "off": 0, "down": 1, "dist": 10, "ytg": 25, "pts": (7, 7),
                         "to": (0, 0), "half2_recv": 0}, seed)
        fg += log[0]["k"] == "fg"
    ck("S8 half-time, 0:03, the ball at the opponent's 25: the field goal is attempted, every seed", fg == 300, fg)
    kn = punts = 0
    for seed in range(600):
        res, log = play({"per": 2, "clk": 20, "off": 0, "down": 1, "dist": 10, "ytg": 80, "pts": (14, 7),
                         "to": (1, 0), "half2_recv": 0}, seed)
        h1 = [e for e in log if e["q"] == 2]
        kn += h1[0]["k"] == "kneel"
        punts += any(e["k"] == "punt" and e["c"] <= 6 for e in h1)
    p4 = 0
    for seed in range(300):
        res, log = play({"per": 2, "clk": 5, "off": 0, "down": 4, "dist": 10, "ytg": 80, "pts": (14, 7),
                         "to": (0, 0), "half2_recv": 0}, seed)
        p4 += any(e["k"] == "punt" for e in log if e["q"] == 2)
    ck("S9 half-time, 0:20 at the own 20, leading: the kneel is taken at its learned rate, and on fourth down "
       "with time for one snap (0:06) nobody punts", kn > 300 and punts == 0 and p4 == 0,
       f"kneel first {kn}/600 punts {punts} {p4}")
    # ---- the drill: trailing with timeouts does not run the clock out
    st12 = {"per": 4, "clk": 90, "off": 1, "down": 1, "dist": 10, "ytg": 70, "pts": (24, 20), "to": (3, 3)}
    kneels = snaps = used = gaps = gapn = 0
    for seed in range(600):
        res, log = play(st12, seed)
        dr = []
        for e in log:
            if e["k"] in ("punt", "fg", "ko", "end") or (e["k"] in SNAPS + ("kneel",) and e["o"] != 1):
                break
            dr.append(e)
        kneels += any(e["k"] == "kneel" for e in dr)
        snaps += sum(1 for e in dr if e["k"] in SNAPS)
        used += any(e["k"] == "tmo" and e["o"] == 1 for e in dr)
        for a, b in zip(dr, dr[1:]):
            if a["k"] in ("run", "cmp", "sack") and not a.get("ob") and b["k"] in SNAPS:
                gaps += a["c"] - b["c"]
                gapn += 1
    ck("S12 trailing by 4 at 1:30 with three timeouts: never a kneel, the drive gets its snaps, timeouts are used, "
       "and an in-bounds play costs well under a huddle",
       kneels == 0 and snaps / 600 > 4.0 and used > 150 and gapn > 100 and gaps / gapn < 30,
       f"snaps {snaps / 600:.2f} drives using a timeout {used}/600 in-bounds gap {gaps / max(1, gapn):.1f}s")
    # ---- starts
    same = True
    for seed in range(60):
        a = C.play_game(lg, lg, random.Random(seed), T)
        b = C.play_game(lg, lg, random.Random(seed), T, start={"per": 1, "clk": 900, "pts": (0, 0), "to": (3, 3)})
        same = same and json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    ck("NC2 a play-out from the opening state is play_game, draw for draw", same)
    same = True
    for seed in range(60):
        a = D.play_game(lg, lg, random.Random(seed), T.base)
        b = C.control_from(lg, lg, random.Random(seed), T)
        same = same and json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    ck("NC3 the control arm from the opening state is nfl_deep_sim.play_game on the same tables, draw for draw", same)
    res = C.control_from(lg, lg, random.Random(1), T, start={"per": 4, "clk": 20, "off": 0, "down": 1, "dist": 10,
                                                             "ytg": 60, "pts": (30, 10)}, neutral=True)
    ck("NC3b and it plays from a late state (a 20-point lead with 0:20 left is a win)",
       res["h"]["pts"] >= 30 and res["h"]["pts"] > res["a"]["pts"])
    # ---- phases and smoothing
    ck("NC4 the first half's two-minute drill and the fourth quarter are different phases, and the pre-warning "
       "phases borrow their neighbours' clock", C.phase(2, 100) == 3 and C.phase(4, 100) == 11
       and C.phase(4, 400) == 8 and C.phase(2, 150) == 2 and C.GAP_PHASE == {2: 1, 10: 9}
       and len({C.phase(p, c) for p in (1, 2, 3, 4, 5) for c in range(0, 901, 5)}) == C.N_PHASE)
    r = C.Tables._rates({"1,1": [0, 0], "1,2": [9, 10], "2,1": [0, 4]}, 10.0)
    ck("NC5 a thin cell is pulled toward its parent, an empty one sits on it",
       abs(r[()] - 9 / 14) < 1e-12 and abs(r[(1, 1)] - r[(1,)]) < 1e-12 and r[(2,)] < r[()] < r[(1,)]
       and r[(1, 2)] < 0.9, r)
    p_q2 = D._sig(T.p_pass(C.phase(2, 45), C.s9(0), 1, 2, 5, 2))
    p_q4 = D._sig(T.p_pass(C.phase(4, 400), C.s9(12), 1, 2, 5, 2))
    p_old = T.base.p_pass(1, 4, 1, 2, 5)
    ck("NC6 first and ten at midfield: the last minute of the half throws, a nine-point leader in the fourth runs "
       "(the production cell pools the two)", p_q2 > 0.75 and p_q4 < 0.42 and p_q4 < p_old < p_q2,
       f"{p_q2:.3f} {p_q4:.3f} pooled {p_old:.3f}")
    lead = T.gap_mix("run", 9, 3, 0).h.mean
    ck("NC7 the leader's runoff has no timeout folded in (over 38 s; the control's clock-kill histogram under 30)",
       lead > 38 and T.base.clock[("run", 2)].mean < 30, f"{lead:.1f} vs {T.base.clock[('run', 2)].mean:.1f}")
    # ---- the seal
    ck("NC8 2026 week 13 on, the 2026 postseason and every later season are sealed; week 12 and 2025 are not",
       CD.sealed(2026, 13) and CD.sealed(2026, 18) and CD.sealed(2026, 1, "POST") and CD.sealed(2027, 1)
       and not CD.sealed(2026, 12) and not CD.sealed(2025, 22, "POST"))
    with tempfile.TemporaryDirectory() as td:
        cols = ["play_id", "game_id", "season", "week", "season_type", "home_team", "away_team", "home_score",
                "away_score", "spread_line", "total_line", "game_date", "location", "posteam", "defteam", "qtr",
                "quarter_seconds_remaining", "down", "ydstogo", "yardline_100", "play_type", "yards_gained", "desc",
                "timeout", "timeout_team", "home_timeouts_remaining", "away_timeouts_remaining", "no_huddle",
                "shotgun", "qb_kneel", "qb_spike", "qb_scramble", "out_of_bounds", "touchdown", "td_team",
                "first_down", "penalty", "fumble_lost", "interception", "sack", "complete_pass", "incomplete_pass",
                "posteam_score", "defteam_score", "posteam_score_post", "defteam_score_post", "extra_point_result",
                "two_point_attempt", "two_point_conv_result", "field_goal_result", "punt_blocked",
                "own_kickoff_recovery", "fixed_drive", "fixed_drive_result", "fourth_down_failed", "play_clock"]
        with gzip.open(os.path.join(td, "play_by_play_2026.csv.gz"), "wt", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for wk in (12, 13):
                row = dict.fromkeys(cols, "")
                row.update(game_id=f"2026_{wk}_AAA_BBB", season="2026", week=str(wk), season_type="REG",
                           home_team="BBB", away_team="AAA", home_score="20", away_score="17", posteam="BBB",
                           defteam="AAA", qtr="1", quarter_seconds_remaining="800", down="1", ydstogo="10",
                           yardline_100="70", play_type="run", yards_gained="4", desc="run", timeout="0",
                           home_timeouts_remaining="3", away_timeouts_remaining="3", posteam_score="0",
                           defteam_score="0", posteam_score_post="0", defteam_score_post="0", two_point_attempt="0")
                w.writerow(row)
        got = CD.load_season(td, 2026)
        refused = False
        try:
            CD.load_season(td, 2026, only_sealed=True)
        except RuntimeError:
            refused = True
        ck("NC9 the loader drops a sealed game before parsing it and refuses to hand the sealed set to anyone but the read",
           [g["week"] for g in got] == [12] and refused and len(got[0]["rows"]) == 1)
    # ---- the registration
    reg_p = os.path.join(ROOT, "research", "data", "nfl_coach", "prereg_r1.json")
    reg = json.load(open(reg_p))
    files_ok = all(_sha(rel) == h for rel, h in reg["files"].items())
    ck("NC10 the R1 registration pins the read, the engine, the tables and the measuring code by sha256, and they match",
       files_ok and {"research/nfl_coach_r1_read.py", "nfl_coach_sim.py", "research/data/nfl_coach_tables.json",
                     "research/nfl_coach_eval.py", "research/nfl_coach_data.py"} <= set(reg["files"]),
       [rel for rel, h in reg["files"].items() if _sha(rel) != h])
    body = json.dumps({k: v for k, v in reg.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    ck("NC11 the registration block's own hash holds, the window is 2026 weeks 13-18 and the read date is fixed",
       hashlib.sha256(body.encode()).hexdigest() == reg["block_sha256"] and reg["window"]["weeks"] == [13, 14, 15, 16, 17, 18]
       and reg["window"]["season"] == 2026 and reg["not_before_utc"] == "2027-01-06T06:00:00Z"
       and len(reg["criteria"]) == 5, reg["block_sha256"][:8])
    from research import nfl_coach_r1_read as RR
    why = RR.refusal(now_utc="2026-12-01T00:00:00Z", dirty=False)
    why2 = RR.refusal(now_utc="2027-01-08T00:00:00Z", dirty=True)
    ck("NC12 the read refuses before its date and refuses a dirty tree", bool(why) and "before" in why
       and bool(why2) and "dirty" in why2 and RR.refusal(now_utc="2027-01-08T00:00:00Z", dirty=False) is None,
       f"{why} | {why2}")
    v = RR.verdict({"games": 60,
                    "real": {"q2_snaps": [9.0, 0.3], "q2_pts": [4.7, 0.4], "q4_snaps": [5.4, 0.3], "q4_pts": [2.5, 0.3],
                             "total": [45.0, 1.7], "ot": [0.05, 0.03], "margin_resid_sd": 13.0, "total_resid_sd": 13.0,
                             "home_win": 0.55},
                    "control": {"q2_snaps": 6.7, "q2_pts": 3.1, "q4_snaps": 3.9, "q4_pts": 1.8, "total": 44.0, "ot": 0.05,
                                "margin_resid_sd": 13.5, "total_resid_sd": 12.5, "home_win": 0.55},
                    "coach": {"q2_snaps": 8.6, "q2_pts": 4.4, "q4_snaps": 5.2, "q4_pts": 2.6, "total": 44.2, "ot": 0.05,
                              "margin_resid_sd": 13.4, "total_resid_sd": 12.6, "home_win": 0.55},
                    "wp_late": {"n": 60, "diff": -0.02, "se": 0.008},
                    "pregame_logloss_diff": [0.001, 0.002], "total_crps_diff": [0.01, 0.02]}, scenarios_pass=True)
    v2 = RR.verdict({"games": 60,
                     "real": {"q2_snaps": [9.0, 0.3], "q2_pts": [4.7, 0.4], "q4_snaps": [5.4, 0.3], "q4_pts": [2.5, 0.3],
                              "total": [45.0, 1.7], "ot": [0.05, 0.03], "margin_resid_sd": 13.0, "total_resid_sd": 13.0,
                              "home_win": 0.55},
                     "control": {"q2_snaps": 6.7, "q2_pts": 3.1, "q4_snaps": 3.9, "q4_pts": 1.8, "total": 44.0, "ot": 0.05,
                                 "margin_resid_sd": 13.5, "total_resid_sd": 12.5, "home_win": 0.55},
                     "coach": {"q2_snaps": 8.6, "q2_pts": 4.4, "q4_snaps": 5.2, "q4_pts": 2.0, "total": 44.2, "ot": 0.05,
                               "margin_resid_sd": 13.4, "total_resid_sd": 12.6, "home_win": 0.55},
                     "wp_late": {"n": 60, "diff": -0.01, "se": 0.008},
                     "pregame_logloss_diff": [0.001, 0.002], "total_crps_diff": [0.01, 0.02]}, scenarios_pass=True)
    ck("NC13 the verdict is the registered rule: all five pass -> PASS; one inside-2:00 figure closer by less than its "
       "SE, or a late-state gain under 2 SE, fails", v["pass"] and all(v["criteria"].values())
       and not v2["pass"] and not v2["criteria"]["2_fidelity"] and not v2["criteria"]["4_late_state_wp"],
       f"{v['criteria']} | {v2['criteria']}")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def _ck(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    run(_ck)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
