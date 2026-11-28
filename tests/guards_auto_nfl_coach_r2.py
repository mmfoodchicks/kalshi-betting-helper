"""Guards for the coaching engine's second revision (R2): nfl_coach_sim_r2 (who
gets the ball, by situation), its tables, the seal on the test games, the
decision-level adaptation hypothesis and the registered read. Self-registered
(tests/guards_auto_*.py run in combo_audit_guards' AUTO section); also runs
alone: python3 tests/guards_auto_nfl_coach_r2.py

Hermetic: nothing here reads nflverse's raw files (they are never committed);
the seal is checked on a synthetic file. research/reports/nfl_coach_r2_evidence.md
is the prose.
"""
import csv
import gzip
import hashlib
import json
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from research import registered_pins as REGP  # noqa: E402  (option (c), 2026-11-28)

# Production's engine files: byte-identical (the deep weight's read sits on them).
PINNED = {
    "nfl_deep_sim.py": "22a221d7810495db3f8f7bb1bef7c4e16c3bbbf282902a95f133cecdabc29820",
    "nfl_deep_data.py": "dff6756fb9ceb5a746750557aaef3d8424a1b57e953ab6aef8cd0abd4375b022",
    "nfl_pbp.py": "660234badefc3e3b5f0e1417a48431e4ad8ff33aab5df8a207834faa4862865d",
    "research/data/nfl_deep_tables.json": "8c118b8fe741566ba1a0f7fb9aa8f41a27a5578b25e0d4988dda059c131b870f",
}
BOX_SUMS = (("car", "rush_att"), ("rush_yd", "rush_yds"), ("rush_td", "td_rush"), ("rec", "comp"),
            ("rec_yd", "pass_yds"), ("rec_td", "td_pass"), ("pass_att", "pass_att"), ("pass_cmp", "comp"),
            ("pass_yd", "pass_yds"), ("pass_td", "td_pass"), ("int", "int"), ("sacks", "sacks"),
            ("kneels", "kneels"), ("fum", "fum_lost"))


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _roster(t, shape="full"):
    full = [dict(id=t + "QB", pos="QB", car=3.0, tgt=0, ypc=5.0),
            dict(id=t + "RB1", pos="RB", car=15.0, tgt=3.5, ypc=4.3, ypr=7.5, cr=0.78),
            dict(id=t + "RB2", pos="RB", car=6.0, tgt=2.0, ypc=4.1, ypr=7.0, cr=0.76),
            dict(id=t + "RB3", pos="RB", car=1.0, tgt=0.4, ypc=4.0, ypr=6.0, cr=0.75),
            dict(id=t + "WR1", pos="WR", car=0.4, tgt=8.5, ypc=7.0, ypr=13.0, cr=0.64),
            dict(id=t + "WR2", pos="WR", car=0.0, tgt=6.0, ypr=12.5, cr=0.62),
            dict(id=t + "WR3", pos="WR", car=0.0, tgt=4.0, ypr=12.0, cr=0.6),
            dict(id=t + "TE1", pos="TE", car=0.0, tgt=5.0, ypr=10.0, cr=0.7),
            dict(id=t + "TE2", pos="TE", car=0.0, tgt=1.0, ypr=9.0, cr=0.68)]
    if shape == "full":
        return full
    if shape == "two_qb":
        return full[:1] + [dict(id=t + "QB2", pos="QB", car=0.5, tgt=0, ypc=4.0)] + full[1:]
    if shape == "no_targets":
        return [dict(p, tgt=0) for p in full]
    if shape == "qb_only":
        return [dict(id=t + "QB", pos="QB", car=0, tgt=0)]
    raise ValueError(shape)


def _conserved(res, lines):
    bad = []
    for key, L in (("h", lines[0]), ("a", lines[1])):
        box = res[key]
        for mine, theirs in BOX_SUMS:
            if sum(v[mine] for v in L.values()) != box[theirs]:
                bad.append((key, mine, sum(v[mine] for v in L.values()), box[theirs]))
        if sum(v["tgt"] for v in L.values()) > box["pass_att"]:
            bad.append((key, "tgt>att"))
        for v in L.values():
            if v["rec"] > v["tgt"] or v["car"] != sum(v["car_b"]):
                bad.append((key, "line"))
    return bad


def run(ck):
    import nfl_coach_sim as C
    import nfl_coach_sim_r2 as E2
    from research import nfl_coach_r2_data as RD
    from research import nfl_coach_r2_adapt as AD
    from research import nfl_coach_r2_read as RR

    print("  -- NR. The coaching engine R2 (who gets the ball): production and R1 untouched, conservation, "
          "the situations, the seal, the registration")
    for rel, want in PINNED.items():
        ck(f"NR0 {rel} is byte-identical to the file the deep weight's read sits on (or, once that read is committed, exactly a staged flip's bytes)", REGP.live_ok(rel, want))
    moved = REGP.moved("research/data/nfl_coach/prereg_r1.json")
    ck("NR0a [option (c): pins checked at the registration commit; live, only research/registered_pins.py's named exceptions] every file R1's registration pins is still byte-identical (R2 is new files only)", not moved, moved)
    src = open(os.path.join(ROOT, "nfl_coach_sim_r2.py")).read()
    ck("NR0b R2 writes no predlog bucket, and no production module imports it",
       "log_shadow" not in src and "import predlog" not in src
       and not any("nfl_coach_sim_r2" in open(os.path.join(ROOT, f)).read()
                   for f in os.listdir(ROOT) if f.endswith(".py") and not f.startswith("nfl_coach")))
    ck("NR0c R2 has its own version and tables file, and plays R1's engine (imports it, never a copy)",
       E2.VERSION == "coach-r2.0" and E2.TABLES_PATH.endswith("nfl_coach_r2_tables.json")
       and "import nfl_coach_sim as C" in src and "def _snap" not in src)

    U = E2.tables()
    raw = U.raw
    ck("NR1 the tables: fitted on 2016-2023, the engine's buckets and roles, credit and the seal stated",
       raw["fit_seasons"] == list(range(2016, 2024)) and raw["buckets"] == list(E2.BUCKETS)
       and raw["roles"]["car"] == list(E2.CARRY_ROLES) and raw["roles"]["tgt"] == list(E2.TGT_ROLES)
       and "CC BY 4.0" in raw["credit"] and "2026 week 13" in raw["seal"])
    ck("NR1b every multiplier is positive and every bucket has a mix (a zero multiplier scored log 0 once)",
       all(x > 0 for k in ("car", "tgt") for v in U.M[k].values() for x in v)
       and all(len(U.mix[k]) == E2.NB and min(U.mix[k]) > 0 for k in ("car", "tgt"))
       and 0.85 < U.p_tgt_inc < 0.97 and 0.95 < U.p_tgt_int <= 1.0)

    # ---- NR2: R2's game is R1's game draw for draw
    lg = C.league_profile()
    same = True
    for seed in range(40):
        r1g = random.Random(seed)
        r2g = random.Random(seed)
        a = C.play_game(lg, lg, r1g)
        b = E2.play_game(lg, lg, r2g, (_roster("H"), _roster("A")), random.Random(999))
        same &= a["h"] == b["h"] and a["a"] == b["a"] and r1g.random() == r2g.random()
    ck("NR2 R2 plays R1's game draw for draw: 40 seeds, every box and the generator after it equal", same)

    # ---- NR3: conservation, every game, both arms, four roster shapes
    bad = []
    for shape in ("full", "two_qb", "no_targets", "qb_only"):
        sim = E2.simulate(lg, lg, (_roster("H", shape), _roster("A", "full")), n=500 if shape == "full" else 150,
                          seed=17)
        for arm in ("r2", "static"):
            for res, lines in zip(sim["games"], sim[arm]):
                b = _conserved(res, lines)
                if b:
                    bad.append((shape, arm, b[:2]))
    ck("NR3 conservation: carries, rushing yards and TDs, receptions, receiving yards and TDs, the passing line, "
       "sacks, kneels and lost fumbles sum to the box; targets <= attempts; catches <= targets "
       "(1,950 games x 2 arms, four roster shapes)", not bad, bad[:3])

    # ---- NR4: the calibration and the mean
    rl = {"q": "QB", "a": "RB1", "b": "RB2", "c": "RB3", "w": "WR"}
    w = {"q": 3.0, "a": 15.0, "b": 6.0, "c": 1.0, "w": 0.5}
    c = E2.calibrate(w, rl, U.M["car"], U.mix["car"])
    f = [m / sum(U.mix["car"]) for m in U.mix["car"]]
    avg = {i: sum(f[b] * E2.probs(w, c, rl, U.M["car"], b)[i] for b in range(E2.NB)) for i in w}
    ck("NR4 calibrate: each player's share averaged over the mix is the share he was given (to 1e-9)",
       all(abs(avg[i] - w[i] / sum(w.values())) < 1e-9 for i in w))
    sim = E2.simulate(lg, lg, (_roster("H"), _roster("A")), n=3000, seed=23)
    m = {arm: sum(ln[0]["HRB1"]["car"] for ln in sim[arm]) / 3000.0 for arm in ("r2", "static")}
    tot = {arm: sum(sum(v["car"] for v in ln[0].values()) for ln in sim[arm]) for arm in ("r2", "static")}
    ck("NR4b on the matchup's own mix the lead back's mean carries are the static deal's (within 2%), the team's "
       "carries identical (the same games)", abs(m["r2"] / m["static"] - 1) < 0.02 and tot["r2"] == tot["static"],
       m)

    # ---- the situations: football sense
    B = {b: i for i, b in enumerate(E2.BUCKETS)}
    td = E2.TeamDeal(_roster("H"), U)
    p = {b: td.p("car", B[b]) for b in E2.BUCKETS}
    q = {b: td.p("tgt", B[b]) for b in E2.BUCKETS}
    ck("SC1 inside the 2 the quarterback carries more than in neutral play (sneaks), a receiver less",
       p["goal_line"]["HQB"] > 1.5 * p["neutral"]["HQB"] and p["goal_line"]["HWR1"] < p["neutral"]["HWR1"])
    ck("SC2 leading big late the lead back carries less and the backups more than in neutral play",
       p["lead_big"]["HRB1"] < p["neutral"]["HRB1"] - 0.05 and p["lead_big"]["HRB2"] > p["neutral"]["HRB2"]
       and p["lead_big"]["HRB3"] > p["neutral"]["HRB3"])
    ck("SC3 passing downs: the quarterback runs more (scrambles); the second back is targeted more in the two-minute "
       "drill than in neutral play (the third-down back)",
       p["pass_down"]["HQB"] > 2 * p["neutral"]["HQB"] and q["two_min"]["HRB2"] > q["neutral"]["HRB2"])
    ck("SC4 inside the 2 the tight ends are targeted more and the lead back less than in neutral play",
       q["goal_line"]["HTE1"] > q["neutral"]["HTE1"] and q["goal_line"]["HTE2"] > q["neutral"]["HTE2"]
       and q["goal_line"]["HRB1"] < q["neutral"]["HRB1"])
    ck("SC5 every situation's probabilities sum to 1 and the static deal is the given share in every bucket",
       all(abs(sum(v.values()) - 1) < 1e-12 for v in p.values())
       and all(abs(E2.TeamDeal(_roster("H"), U, situational=False).p("car", b)["HRB1"] - 15.0 / 25.4) < 1e-12
               for b in range(E2.NB)))
    # lead back's share of carries by final margin over whole games
    share = {"r2": [[0, 0], [0, 0]], "static": [[0, 0], [0, 0]]}
    for arm in ("r2", "static"):
        for res, ln in zip(sim["games"], sim[arm]):
            mg = res["h"]["pts"] - res["a"]["pts"]
            k = 0 if abs(mg) <= 7 else 1 if mg >= 17 else None
            if k is None:
                continue
            share[arm][k][0] += ln[0]["HRB1"]["car"]
            share[arm][k][1] += sum(v["car"] for v in ln[0].values())
    s = {arm: [a / b for a, b in share[arm]] for arm in share}
    ck("SC6 whole games: winning by 17+ the lead back's share of carries falls below his close-game share "
       "(real 2024-26: 0.52/0.49/0.57 vs 0.56/0.57/0.58); the static deal's does not move by more than noise",
       s["r2"][1] < s["r2"][0] - 0.01 and abs(s["static"][1] - s["static"][0]) < 0.012, s)
    one = E2.simulate(lg, lg, (_roster("H", "qb_only"), _roster("A")), n=50, seed=3)
    ck("SC7 a roster of one quarterback takes every carry, catch and yard of his team",
       all(ln[0]["HQB"]["car"] == res["h"]["rush_att"] and ln[0]["HQB"]["rec"] == res["h"]["comp"]
           for res, ln in zip(one["games"], one["r2"])))

    # ---- the buckets, pinned by hand
    cases = [((1, 900, 0, 1, 2, 2), "goal_line"), ((3, 600, 21, 1, 5, 5), "inside5"),
             ((2, 120, 0, 1, 10, 60), "two_min"), ((3, 800, 17, 1, 10, 60), "lead_big"),
             ((4, 500, 14, 2, 5, 60), "lead_big"), ((4, 500, 9, 2, 5, 60), "lead_late"),
             ((3, 800, -14, 1, 10, 60), "trail_big"), ((1, 800, 0, 3, 7, 60), "pass_down"),
             ((1, 800, 0, 2, 10, 60), "pass_down"), ((1, 800, 0, 3, 1, 60), "short_yd"),
             ((1, 800, 0, 1, 10, 60), "neutral"), ((1, 121, 0, 1, 10, 60), "neutral"),
             ((4, 121, 0, 1, 10, 9), "inside10")]
    ck("NR5 the buckets: field zone first, then the clock, the score, the down (13 hand cases)",
       all(E2.BUCKETS[E2.bucket(*a)] == want for a, want in cases),
       [(a, E2.BUCKETS[E2.bucket(*a)]) for a, want in cases if E2.BUCKETS[E2.bucket(*a)] != want])
    log = [{"k": "ko", "q": 1, "c": 900, "s": (0, 0)},
           {"k": "run", "q": 1, "c": 880, "o": 0, "d": 1, "t": 10, "y": 60, "yd": 60, "td": 1, "s": (6, 0)},
           {"k": "try", "q": 1, "c": 870, "o": 0, "s": (7, 0)},
           {"k": "run", "q": 3, "c": 800, "o": 1, "d": 1, "t": 10, "y": 50, "yd": 3, "s": (7, 0)},
           {"k": "cmp", "q": 4, "c": 300, "o": 0, "d": 1, "t": 10, "y": 50, "yd": 9, "s": (7, 0)}]
    sn = E2.snaps(log)
    ck("NR6 the log's score is read BEFORE each snap (the touchdown run is dealt at 0-0, not 6-0)",
       [x[2] for x in sn] == [0, 0, 0] and [x[0] for x in sn] == [0, 1, 0] and sn[0][4] == 1,
       sn)

    # ---- NR7: the seal, on a synthetic file
    with tempfile.TemporaryDirectory() as tdir:
        cols = ["play_id", "game_id", "season", "week", "season_type", "home_team", "away_team", "posteam",
                "home_coach", "away_coach", "qtr", "game_seconds_remaining", "half_seconds_remaining",
                "score_differential", "down", "ydstogo", "yardline_100", "play_type", "two_point_attempt",
                "play_deleted", "aborted_play", "qb_kneel", "qb_spike", "qb_scramble", "sack", "no_huddle",
                "shotgun", "xpass", "rusher_player_id", "receiver_player_id", "passer_player_id",
                "yards_gained", "epa", "success", "rush_touchdown", "pass_touchdown", "complete_pass",
                "fumble_lost", "interception", "wp", "drive", "two_point_conv_result", "goal_to_go",
                "home_score", "away_score", "spread_line", "total_line", "game_date"]
        with gzip.open(os.path.join(tdir, "play_by_play_2026.csv.gz"), "wt", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=cols)
            wr.writeheader()
            for wk, rid in ((12, "SEEN"), (13, "SEALED")):
                row = dict.fromkeys(cols, "")
                row.update(play_id="1", game_id=f"2026_{wk}_A_B", season="2026", week=str(wk), season_type="REG",
                           home_team="B", away_team="A", posteam="B", qtr="1", down="1", ydstogo="10",
                           yardline_100="75", play_type="run", rusher_player_id=rid, yards_gained="4",
                           xpass="0.5", wp="0.5", game_date="2026-11-20")
                wr.writerow(row)
            row.update(game_id="2026_19_A_B", week="19", season_type="POST", rusher_player_id="POST")
            wr.writerow(row)
        d = RD.load_season(tdir, 2026)
        ok_cache = os.path.exists(RD.cache_path(tdir, 2026))
        try:
            RD.load_season(tdir, 2026, only_sealed=True)
            refused = False
        except RuntimeError:
            refused = True
        os.remove(RD.cache_path(tdir, 2026))
        allr = RD.load_season(tdir, 2026, unseal=RD._UNSEAL_TOKEN)
        no_cache = not os.path.exists(RD.cache_path(tdir, 2026))
        ck("NR7 R2's loader keeps R1's seal: week 12 read, week 13 and the postseason dropped before parsing; "
           "the sealed set is refused without the read's token; an unsealed read never writes the row cache",
           d["players"] == ["SEEN"] and [g["week"] for g in d["games"]] == [12] and refused and ok_cache
           and sorted(allr["players"]) == ["POST", "SEALED", "SEEN"] and no_cache)

    # ---- NR8: the adaptation hypothesis' frozen coefficients
    ad = json.load(open(AD.OUT))
    ck("NR8 the adaptation call model: fitted on 2016-2023, its gap term negative (a team leans toward what "
       "worked), the baseline nested in it",
       ad["fit_seasons"] == list(range(2016, 2024)) and ad["coef_candidate"][2] < -0.5
       and len(ad["coef_baseline"]) == 2 and ad["lean_k"] == AD.LEAN_K and ad["gap_k"] == AD.GAP_K)
    g = {"season": 2026, "players": [], "games": []}
    plays = []
    for n in range(14):
        k = RD.K_RUN if n % 2 == 0 else RD.K_PASS
        row = [0] * RD.ROW_W
        row[RD.Q_PER], row[RD.Q_HSR], row[RD.Q_OFFH], row[RD.Q_DOWN], row[RD.Q_KIND] = 1, 1500, 1, 1, k
        row[RD.Q_XP], row[RD.Q_WP], row[RD.Q_SUCC] = 500, 500, 1 if k == RD.K_RUN else 0
        plays.append(row)
    for wk in range(1, 5):
        g["games"].append({"st": 2, "week": wk, "home": "H", "away": "A", "plays": [list(r) for r in plays]})
    rws = AD.rows(g)
    last = [x for x in rws if x[0][1] == 4][0][1]
    gap10 = (5 - 0.42 * 5) / 15.0 - (0 - 0.46 * 5) / 15.0
    ck("NR8b the gap is this game's earlier snaps only, shrunk as written (10 snaps in: runs 5/5, passes 0/5)",
       abs(last[0][3] - gap10) < 1e-12 and all(x[0][1] > 3 for x in rws))

    # ---- NR9-NR11: the registration and the read
    reg = RR.prereg()
    body = json.dumps({k: v for k, v in reg.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    h1, h2 = reg["hypotheses"]["H1"], reg["hypotheses"]["H2"]
    ck("NR9 the registration: its own hash holds, H1 reads 2026 weeks 13-18 on/after 2027-01-06 after R1's read, "
       "H2 2026 weeks 13-18 + 2027 weeks 1-18 on/after 2028-01-13",
       hashlib.sha256(body.encode()).hexdigest() == reg["block_sha256"]
       and h1["window"] == [{"season": 2026, "weeks": list(range(13, 19))}]
       and h1["not_before_utc"] == "2027-01-06T06:00:00Z" and reg["requires_r1_read"]
       and h2["window"] == [{"season": 2026, "weeks": list(range(13, 19))},
                            {"season": 2027, "weeks": list(range(1, 19))}]
       and h2["not_before_utc"] == "2028-01-13T06:00:00Z")
    moved = REGP.moved("research/data/nfl_coach/prereg_r2.json")
    ck("NR9b [option (c): pins checked at the registration commit; live, only research/registered_pins.py's named exceptions] every file the registration pins matches its sha256 (the engine, tables, loader, measures, "
       "the read, this file, R1's engine and production's deep engine)",
       not moved and "tests/guards_auto_nfl_coach_r2.py" in reg["files"] and "nfl_coach_sim.py" in reg["files"],
       moved)
    # option (c), 2026-11-28: the read runs from its registration commit (research/reports/registered_reads_runbook.md),
    # so whether it WOULD run is asked of that commit's tree; the live tree may hold a staged fix.
    with REGP.registered_view("research/data/nfl_coach/prereg_r2.json", RR):
        w1 = RR.refusal("H1", now_utc="2026-12-01T00:00:00Z", dirty=False, reg=reg)
        w2 = RR.refusal("H1", now_utc="2027-02-01T00:00:00Z", dirty=True, reg=reg, r1_read=True, already=False)
        w3 = RR.refusal("H1", now_utc="2027-02-01T00:00:00Z", dirty=False, reg=reg, r1_read=False, already=False)
        w4 = RR.refusal("H2", now_utc="2027-06-01T00:00:00Z", dirty=False, reg=reg, r1_read=True)
        w5 = RR.refusal("H1", now_utc="2027-02-01T00:00:00Z", dirty=False, reg=reg, r1_read=True, already=True)
        w6 = RR.refusal("H1", now_utc="2027-02-01T00:00:00Z", dirty=False, reg=reg, r1_read=True, already=False)
    ck("NR10 [would-run asked of the registration commit's tree, option (c)] the read refuses before its date, a dirty tree, before R1's read, a second read; and would run "
       "on a clean tree after both",
       "before" in (w1 or "") and "dirty" in (w2 or "") and "R1" in (w3 or "") and "before" in (w4 or "")
       and "already" in (w5 or "") and w6 is None, [w1, w2, w3, w4, w5, w6])
    good = {"guards_exit": 0,
            "stage1": {"both": {"gain_per_team_game": 0.8, "se": 0.15}, "car": {"gain_per_team_game": 0.7, "se": 0.14},
                       "tgt": {"gain_per_team_game": 0.1, "se": 0.05}},
            "realism": {"carries_crps": {"r2_minus_static_per_team_game": 0.01, "se": 0.02},
                        "targets_crps": {"r2_minus_static_per_team_game": 0.0, "se": 0.02},
                        "td_logscore": {"r2_minus_static_per_team_game": -0.03, "se": 0.02}},
            "team_games": 190}
    bad_tgt = json.loads(json.dumps(good))
    bad_tgt["stage1"]["tgt"]["gain_per_team_game"] = -0.06
    weak = json.loads(json.dumps(good))
    weak["stage1"]["both"]["gain_per_team_game"] = 0.29
    worse = json.loads(json.dumps(good))
    worse["realism"]["carries_crps"]["r2_minus_static_per_team_game"] = 0.03
    ck("NR11 H1's verdict is the registered rule: all pass -> PASS; targets worse by more than 1 SE, the joint gain "
       "under 2 SE, or carries' CRPS worse by more than 1 SE -> FAIL",
       RR.verdict_h1(good)["pass"] and not RR.verdict_h1(bad_tgt)["pass"] and not RR.verdict_h1(weak)["pass"]
       and not RR.verdict_h1(worse)["pass"])
    ck("NR11b H2's verdict: gain over 2 SE with the guards green -> PASS; at 1.9 SE -> FAIL",
       RR.verdict_h2({"guards_exit": 0, "gain_per_team_game": 0.05, "se": 0.02, "team_games": 600})["pass"]
       and not RR.verdict_h2({"guards_exit": 0, "gain_per_team_game": 0.038, "se": 0.02, "team_games": 600})["pass"])


if __name__ == "__main__":
    PASS, FAIL = [], []

    def _ck(name, cond, detail=""):
        (PASS if cond else FAIL).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail and not cond else ""))
    run(_ck)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
