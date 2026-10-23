"""Hermetic guards for the NBA matchup engine (nba_matchup_data /
nba_matchup_profiles / nba_matchup). No network, tiny fixtures, a few seconds.

  python3 tests/guards_nba_matchup.py        # exits 1 on any failure

Each check pins a DEFINITION, not just a number: the stint accounting is
checked against a hand-walked fixture (who was on the floor for which
seconds, whose rebound chance a miss was), the as-of rule by adding a later
game and seeing the earlier snapshot unchanged, the distributions by their
own identities (a survival function starts at 1, never rises, its pmf sums
to 1 and has the stated mean and variance).
"""
import array
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

import nba_pbp as P                     # noqa: E402
import nba_matchup_data as D            # noqa: E402
import nba_matchup_profiles as M        # noqa: E402
import nba_matchup as NM                # noqa: E402


# ---- fixtures ------------------------------------------------------------------
def _play(per, clk, team, code, val=0, made=0, dist=-1, stype=-1, p1=0, p2=0, hs=0, as_=0, flags=0):
    return (per, clk, team, code, val, made, dist, stype, p1, p2, hs, as_, flags)


def _box(pid, side, starter, pos="G", mn=0, pts=0, played=1, **kw):
    b = {"pid": pid, "side": side, "name": f"P{pid}", "pos": pos, "pos_g": D.pos_group(pos), "starter": starter,
         "played": played, "dnp_reason": "", "min": mn, "pts": pts, "fgm": 0, "fga": 0, "tpm": 0, "tpa": 0,
         "ftm": 0, "fta": 0, "oreb": 0, "dreb": 0, "reb": 0, "ast": 0, "tov": 0, "stl": 0, "blk": 0, "pf": 0, "pm": 0}
    b.update(kw)
    return b


def fixture_game(gid=1):
    """One 12-minute period, hand-walked:

      700  home #1 misses a 20-footer             home five: OREB chance; away five: DREB chance
      698  home #5 offensive rebound
      696  home #5 makes at the rim (#2 assists)  possession 1 (home) ends; 2 pts
      600  home #6 enters for #1                  #1 played 720-600 = 120 s
      590  away #11 misses a three
      588  home #6 defensive rebound              away five: OREB chance; home five (with #6) DREB chance
                                                  possession 2 (away) ends
      580  home #6 makes a three (#2 assists)     possession 3 (home) ends; 3 pts
      579  away #12 lost ball, #3 steals          possession 4 (away) ends (live)
      577  home #3 makes a layup                  early clock: 2 s after a steal; possession 5 (home) ends
        0  end of period                          possession 6 (away) ends at the buzzer
    """
    plays = [
        _play(1, 700, 0, P.FGA, 2, 0, 20, 5, p1=1),
        _play(1, 698, 0, P.OREB, p1=5),
        _play(1, 696, 0, P.FGA, 2, 1, 1, 1, p1=5, p2=2, hs=2, flags=P.F_ASSISTED),
        _play(1, 600, 0, P.SUB, p1=6, p2=1, hs=2),
        _play(1, 590, 1, P.FGA, 3, 0, 25, 5, p1=11, hs=2),
        _play(1, 588, 0, P.DREB, p1=6, hs=2),
        _play(1, 580, 0, P.FGA, 3, 1, 25, 5, p1=6, p2=2, hs=5, flags=P.F_ASSISTED),
        _play(1, 579, 1, P.TOV, p1=12, p2=3, hs=5, flags=P.F_STEAL),
        _play(1, 577, 0, P.FGA, 2, 1, 1, 1, p1=3, hs=7),
        _play(1, 0, -1, P.END_PERIOD, hs=7),
    ]
    a = array.array("i")
    for p in plays:
        a.extend(p)
    xt = array.array("i", [D.shot_bits("Jump Shot"), 0, D.shot_bits("Cutting Layup Shot"), 0,
                           D.shot_bits("Jump Shot"), 0, D.shot_bits("Pullup Jump Shot"), 0,
                           D.shot_bits("Driving Layup Shot"), 0])
    box = [_box(1, 0, 1, mn=2), _box(2, 0, 1, mn=12), _box(3, 0, 1, mn=12, pts=2), _box(4, 0, 1, "F", mn=12),
           _box(5, 0, 1, "C", mn=12, pts=2), _box(6, 0, 0, "F", mn=10, pts=3),
           _box(11, 1, 1, mn=12), _box(12, 1, 1, mn=12), _box(13, 1, 1, "F", mn=12), _box(14, 1, 1, "F", mn=12),
           _box(15, 1, 1, "C", mn=12), _box(16, 1, 0, "C", mn=0, played=0)]
    return {"id": gid, "season": 2024, "st": 2, "date": "2024-11-01T00:00Z", "home": "AAA", "away": "BBB",
            "home_id": "1", "away_id": "2", "home_score": 7, "away_score": 0, "periods": 1, "neutral": False,
            "plays": a, "xt": xt, "box": box, "players": {b["pid"]: b["side"] for b in box}}


def _entry_row(pid, side, pos_g, mn, pts, **kw):
    r = array.array("i", [0] * len(D.PF))
    r[D.PI["pid"]], r[D.PI["side"]], r[D.PI["pos_g"]], r[D.PI["played"]] = pid, side, pos_g, 1 if mn > 0 else 0
    r[D.PI["min"]], r[D.PI["pts"]], r[D.PI["secs"]] = mn, pts, mn * 60
    for k, v in kw.items():
        r[D.PI[k]] = v
    return r


def synthetic_entry(gid, day, home, away, pts_home=10, pts_away=8):
    """A minimal cut game for the Book: two players a side."""
    T = []
    for s in (0, 1):
        t = array.array("i", [0] * len(D.TF))
        t[D.TI["gid"]], t[D.TI["side"]] = gid, s
        t[D.TI["poss"]], t[D.TI["secs"]] = 100, 2880
        t[D.TI["pts"]] = (pts_home, pts_away)[s] * 10
        t[D.TI["fga"]], t[D.TI["a_3"]], t[D.TI["a_rim"]] = 88, 35, 30
        T.append(t)
    players = {
        100 + gid * 0: _entry_row(100, 0, 0, 30, pts_home, fga=10, a_3=4, a_rim=3, a_mid=3, poss_off=60, poss_def=60, tm_fga=50),
        101: _entry_row(101, 0, 2, 25, 6, oreb=3, dreb=7, oreb_ch=20, dreb_ch=25),
        200: _entry_row(200, 1, 0, 32, pts_away, fga=12, a_3=6, a_rim=2, a_mid=4),
        201: _entry_row(201, 1, 1, 28, 9),
    }
    for r in players.values():
        r[D.PI["gid"]] = gid
    return {"id": str(gid), "date": day, "season": 2024, "home": home, "away": away, "players": players,
            "teams": (T[0], T[1]), "names": {}}


def run(ck):
    # ---- the parse helpers --------------------------------------------------
    b = D.shot_bits("Running Pullup Jump Shot")
    ck("shot bits: 'Running Pullup Jump Shot' is a pull-up jumper, not a spot-up",
       bool(b & D.X_PULLUP) and bool(b & D.X_JUMP) and bool(b & D.X_RUN) and not D.is_spot_jumper(b))
    ck("shot bits: plain 'Jump Shot' is the catch-and-shoot proxy; 'Step Back Jump Shot' is not",
       D.is_spot_jumper(D.shot_bits("Jump Shot")) and not D.is_spot_jumper(D.shot_bits("Step Back Jump Shot"))
       and not D.is_spot_jumper(D.shot_bits("Heave Jump Shot")))
    ck("shot bits: putbacks and tips share the putback bit; alley-oops are not cuts by the word alone",
       bool(D.shot_bits("Layup Shot Putback") & D.X_PUTBACK) and bool(D.shot_bits("Tip Dunk Shot") & D.X_PUTBACK)
       and bool(D.shot_bits("Alley Oop Dunk Shot") & D.X_ALLEY) and not D.shot_bits("Alley Oop Dunk Shot") & D.X_CUT)
    ck("positions group to G / F / C (finer ESPN codes and hybrids included)",
       [D.pos_group(x) for x in ("PG", "SG", "G", "SF", "PF", "F", "C", "G-F", "F-C", "", None)]
       == [0, 0, 0, 1, 1, 1, 2, 0, 1, -1, -1])

    # ---- stints, chances, possessions on the hand-walked fixture -----------
    rec = fixture_game()
    R, T, q = D.cut_game(rec)
    secs = {pid: R[pid][D.PI["secs"]] for pid in R}
    ck("stints: #1 played 120 s, #6 600 s, every other starter the full 720 s, the scratch 0",
       secs[1] == 120 and secs[6] == 600 and all(secs[p] == 720 for p in (2, 3, 4, 5, 11, 12, 13, 14, 15))
       and secs[16] == 0, str(secs))
    ck("stints: each side's on-floor seconds are five players' worth of the clock",
       q["secs_team"] == [3600, 3600] and T[0][D.TI["secs"]] == 720 and T[1][D.TI["secs"]] == 720, str(q))
    ck("rebound chances: the home miss gave the home five an OREB chance and the away five a DREB chance; "
       "the away miss the reverse, with #6 (not #1) on the floor",
       R[1][D.PI["oreb_ch"]] == 1 and R[1][D.PI["dreb_ch"]] == 0 and R[6][D.PI["oreb_ch"]] == 0
       and R[6][D.PI["dreb_ch"]] == 1 and R[2][D.PI["oreb_ch"]] == 1 and R[2][D.PI["dreb_ch"]] == 1
       and R[11][D.PI["oreb_ch"]] == 1 and R[11][D.PI["dreb_ch"]] == 1,
       str({p: (R[p][D.PI["oreb_ch"]], R[p][D.PI["dreb_ch"]]) for p in (1, 2, 6, 11)}))
    ck("possessions: three a side, credited to the five on the floor at each one's last play",
       T[0][D.TI["poss"]] == 3 and T[1][D.TI["poss"]] == 3 and R[1][D.PI["poss_off"]] == 1
       and R[6][D.PI["poss_off"]] == 2 and R[2][D.PI["poss_off"]] == 3 and R[6][D.PI["poss_def"]] == 3
       and R[1][D.PI["poss_def"]] == 0,
       f"{T[0][D.TI['poss']]} {T[1][D.TI['poss']]} {[R[p][D.PI['poss_off']] for p in (1, 2, 6)]}")
    ck("shot profile: #5's rim make is assisted and a cut (the type text), a second-chance shot after the OREB",
       R[5][D.PI["a_rim"]] == 1 and R[5][D.PI["m_rim"]] == 1 and R[5][D.PI["ast_m2"]] == 1
       and R[5][D.PI["cut_a"]] == 1 and R[5][D.PI["sc_a"]] == 1 and R[5][D.PI["sc_pts"]] == 2)
    ck("shot profile: #6's pull-up three is self-created by its words AND assisted by the flag (both kept)",
       R[6][D.PI["pull3_a"]] == 1 and R[6][D.PI["pull3_m"]] == 1 and R[6][D.PI["ast_m3"]] == 1
       and R[6][D.PI["spot3_a"]] == 0)
    ck("early clock: #3's layup 2 s after the steal counts; #1's first shot of a period start does not",
       R[3][D.PI["tr_a"]] == 1 and R[3][D.PI["tr_pts"]] == 2 and R[1][D.PI["tr_a"]] == 0
       and T[0][D.TI["live_starts"]] == 2, f"tr {R[3][D.PI['tr_a']]} live {T[0][D.TI['live_starts']]}")
    ck("assists from the feed: #2 two, one on a rim make, one on a three",
       R[2][D.PI["pbp_ast"]] == 2 and R[2][D.PI["ast_rim"]] == 1 and R[2][D.PI["ast_3"]] == 1)
    ck("points from the feed reproduce the box (the quality gate's own test)",
       all(R[p][D.PI["pbp_pts"]] == R[p][D.PI["pts"]] for p in R))

    # ---- the old-feed shot value patch ---------------------------------------
    js = {"header": {"id": "9", "season": {"year": 2022, "type": 2},
                     "competitions": [{"date": "2021-11-01T00:00Z", "competitors": [
                         {"homeAway": "home", "score": "3", "team": {"id": "1", "abbreviation": "BOS"}},
                         {"homeAway": "away", "score": "0", "team": {"id": "2", "abbreviation": "NY"}}]}]},
          "boxscore": {"players": [{"team": {"id": "1"}, "statistics": [{"keys": ["minutes", "points"], "athletes": [
              {"athlete": {"id": "7", "displayName": "A", "position": {"abbreviation": "G"}}, "starter": True,
               "stats": ["30", "3"]}]}]}]},
          "plays": [{"type": {"text": "Step Back Jump Shot"}, "text": "A makes 25-foot step back jumpshot",
                     "shootingPlay": True, "scoreValue": 3, "pointsAttempted": 0, "period": {"number": 1},
                     "clock": {"displayValue": "11:00"}, "team": {"id": "1"},
                     "participants": [{"athlete": {"id": "7"}}], "homeScore": 3, "awayScore": 0}]}
    g = D.parse_game(js)
    ck("2021-22 feed: a made three without 'three point' in the text takes its scoreValue",
       g is not None and g["plays"][P.I_VAL] == 3 and P.points_check(g) == (3, 0) and g["box"][0]["min"] == 30)

    # ---- the book: as-of, never the same day ---------------------------------
    bk = M.Book()
    bk.roll(2024)
    e1 = synthetic_entry(1, "2024-11-01", "AAA", "BBB", 10, 8)
    e2 = synthetic_entry(2, "2024-11-03", "BBB", "AAA", 20, 4)
    bk.add_date([e1])
    s_before = bk.player(100, "2024-11-03")
    t_before = bk.team("BBB")
    bk2 = M.Book()
    bk2.roll(2024)
    bk2.add_date([e1])
    bk2.add_date([e2])
    s_after_future = bk2.player(100, "2024-11-05")
    ck("as-of: a snapshot for 11-03 holds only 11-01's game (pts 10 in one game)",
       s_before["cur"][M.PS["pts"]] == 10 and s_before["cur"][M.PS["g"]] == 1 and s_before["days"] == 2)
    ck("as-of: the later game changes the later snapshot, never the earlier one",
       s_after_future["cur"][M.PS["pts"]] == 30 and s_before["cur"][M.PS["pts"]] == 10)
    ck("as-of: the team's concessions before 11-03 are the 80 points BBB allowed... i.e. AAA's 100 offense on 11-01",
       t_before["def"][0][M.TS["pts"]] == 100 and t_before["off"][0][M.TS["pts"]] == 80 and t_before["g"] == 1)
    # the research walk snapshots a date BEFORE adding it: same-day games never see each other
    import importlib
    W = importlib.import_module("research.nba_matchup_walk")
    seen = {}

    def on_date(book, day, games):
        for gm in games:
            for pid in gm["players"]:
                sn = book.player(pid, day)
                seen[(day, pid)] = None if sn is None else sn["cur"][M.PS["g"]]
        return None
    e1w = dict(e1, day="2024-11-01", season=2024)
    e1b = dict(synthetic_entry(3, "2024-11-01", "CCC", "DDD"), day="2024-11-01", season=2024)
    e2w = dict(e2, day="2024-11-03", season=2024)
    W.walk([e1w, e1b, e2w], on_date, log=None)
    ck("as-of: on 11-01 neither of that date's two games is visible (the fixture reuses ids, so #100 'plays' "
       "both); both are by 11-03",
       seen[("2024-11-01", 100)] is None and seen[("2024-11-03", 100)] == 2, str(seen))
    ck("game day is the Mountain-time date (a 9:30 pm MT tip is 03:30 UTC the next day)",
       W.game_day("2024-11-02T03:30Z") == "2024-11-01" and W.game_day("2024-11-01T23:00Z") == "2024-11-01")

    # ---- season roll ------------------------------------------------------------
    bk3 = M.Book()
    bk3.roll(2023)
    bk3.add_date([dict(e1, season=2023)])
    bk3.roll(2024)
    sn = bk3.player(100, "2024-10-25")
    ck("season roll: last season's sums move to prev, this season's start at zero, the recent EWMA carries",
       sn["prev"][M.PS["pts"]] == 10 and sn["cur"][M.PS["pts"]] == 0 and sn["rec"][M.PS["pts"]] == 10)

    # ---- role features on a fixture -------------------------------------------
    snap = {"cur": [0.0] * len(M.PSUM), "prev": [0.0] * len(M.PSUM), "rec": [0.0] * len(M.PSUM), "lg_pos": None,
            "lg_pos_cur": None}
    for k, v in (("a_rim", 30), ("a_short", 10), ("a_mid", 10), ("a_3", 50), ("pull_a", 20), ("pull3_a", 15),
                 ("spot3_a", 30), ("fga", 100), ("fta", 25), ("fgm", 45), ("ast_m2", 10), ("ast_m3", 20),
                 ("m_3", 20), ("oreb", 5), ("oreb_ch", 100), ("min", 300), ("g", 10)):
        snap["cur"][M.PS[k]] = v
    rl = M.role(snap, role_K={n: 0.0 for n in M.ROLE_NAMES})
    ck("role (K = 0 is the raw share): three 0.50, rim 0.30, pull-up 0.20, spot-up threes 0.60, FT rate 0.25, "
       "assisted 2/3, assisted threes 1.0, OREB% 0.05",
       abs(rl["three_share"]["value"] - 0.5) < 1e-12 and abs(rl["rim_share"]["value"] - 0.3) < 1e-12
       and abs(rl["pullup_share"]["value"] - 0.2) < 1e-12 and abs(rl["spot3_share"]["value"] - 0.6) < 1e-12
       and abs(rl["ft_rate"]["value"] - 0.25) < 1e-12 and abs(rl["assisted_share"]["value"] - 30 / 45) < 1e-12
       and abs(rl["assisted3_share"]["value"] - 1.0) < 1e-12 and abs(rl["oreb_pct"]["value"] - 0.05) < 1e-12)
    rl2 = M.role(snap, role_K={n: 100.0 for n in M.ROLE_NAMES}, pos_means={"three_share": 0.3})
    ck("role shrinkage: K = 100 FGA pulls a 0.50 three share on 100 FGA halfway to the prior 0.30",
       abs(rl2["three_share"]["value"] - 0.4) < 1e-12 and rl2["spot3_share"]["kind"] == "proxy"
       and rl2["pullup_share"]["kind"] == "observed")

    # ---- distributions ------------------------------------------------------------
    for mu, phi, kappa in ((0.7, 1.0, 0.0), (6.0, 1.15, 0.0), (22.0, 3.0, 0.0), (8.0, 1.0, 0.04)):
        sf = NM.nb_sf_all(mu, phi, kappa, 400)
        pmf = [sf[k] - sf[k + 1] for k in range(400)]
        m1 = sum(k * p for k, p in enumerate(pmf))
        v1 = sum((k - m1) ** 2 * p for k, p in enumerate(pmf))
        mono = all(sf[k + 1] <= sf[k] + 1e-15 for k in range(400))
        ck(f"negative binomial mu {mu} phi {phi} kappa {kappa}: sf starts at 1, never rises, pmf sums to 1, "
           "mean mu and variance phi*mu + kappa*mu^2",
           sf[0] == 1.0 and mono and abs(sum(pmf) - 1) < 1e-9 and abs(m1 - mu) < 1e-6
           and abs(v1 - max(mu, phi * mu + kappa * mu * mu)) < 1e-4 * max(1.0, v1), f"mean {m1} var {v1}")
    mix = NM.mixture_sf(0.6, 30.0, [0.5, 0.9, 1.0, 1.1, 1.2], 3.0, 0.0, 60)
    ck("minutes mixture: monotone in k, P(>= 0) = 1, the mean of its nodes' means",
       mix[0] == 1.0 and all(mix[k + 1] <= mix[k] for k in range(60))
       and abs(sum(mix[1:]) - 0.6 * 30.0 * sum([0.5, 0.9, 1.0, 1.1, 1.2]) / 5) < 1e-3)
    ck("DK points: 25 pts, 3 threes, 11 reb, 4 ast, 1 stl, 0 blk, 2 tov = 25 + 1.5 + 13.75 + 6 + 2 - 1 + 1.5 (DD)",
       abs(NM.dk_points({"pts": 25, "tpm": 3, "reb": 11, "ast": 4, "stl": 1, "blk": 0, "tov": 2}) - 48.75) < 1e-9)
    ck("DK points: a triple-double earns the double-double bonus too (+4.5)",
       abs(NM.dk_points({"pts": 10, "reb": 10, "ast": 10}) - (10 + 12.5 + 15 + 4.5)) < 1e-9
       and NM.dk_points({"pts": 30}) == 30.0)

    # ---- comparables: only strictly earlier days, every outcome kept ------------------
    hist = [(10, [0.0, 0.0], 0.5, 30, "a"), (20, [3.0, 3.0], -0.5, 30, "b"), (30, [0.0, 0.0], 9.0, 30, "same day")]
    sc, neff, sw, used = NM.comparables(hist, [0.0, 0.0], 30, "pts")
    w_a = math.exp(0.0) * 0.5 ** (20 / NM.COMP_TAU)
    w_b = math.exp(-9.0 / (2 * NM.COMP_H ** 2)) * 0.5 ** (10 / NM.COMP_TAU)
    b_a, b_b = 0.5 ** (20 / NM.COMP_TAU), 0.5 ** (10 / NM.COMP_TAU)
    want = (w_a * 0.5 + w_b * -0.5) / (w_a + w_b + NM.COMP_K) - (b_a * 0.5 + b_b * -0.5) / (b_a + b_b + NM.COMP_K)
    ck("comparables: the same-day game is excluded; the score is the similarity-weighted residual minus the "
       "recency-weighted one, each shrunk by COMP_K",
       len(used) == 2 and abs(sc - want) < 1e-12 and all(u[2] != "same day" for u in used), f"{sc} vs {want}")

    # ---- the numpy twins (research) equal the production functions -------------------
    try:
        import numpy as np
        from research import nba_matchup_fit as F
        pmf, sf = F.nb_tables(np.array([5.0, 20.0]), 2.5, 0.01, 60)
        ok = all(abs(sf[i, k] - NM.nb_sf_all(mu, 2.5, 0.01, 60)[k]) < 1e-9 for i, mu in enumerate((5.0, 20.0))
                 for k in range(61))
        ck("research's vectorized negative binomial equals production's", ok)
    except ImportError:
        ck("numpy absent: the research twin check is skipped (production needs no numpy)", True)

    # ---- the committed fit, when present, is self-consistent ---------------------------
    Pm = NM.params()
    if Pm.get("coefs"):
        ok = all(set(Pm["coefs"][m]) - {"_icpt"} <= set(sum((list(NM.RUNGS[k][m]) for k in NM.RUNGS), []))
                 for m in NM.MARKETS)
        ck("committed coefficients name only features the rungs define", ok)
        shipped = Pm.get("shipped") or {}
        ck("a market the held-out test rejected carries no matchup coefficient (weight 0, not a hand value)",
           all(set(Pm["coefs"][m]) <= {"_icpt", "pace", "home"} for m in NM.MARKETS
               if shipped.get(m, {}).get("rungs") == [1]))

    # ---- the pre-registration was never edited after it was frozen -------------------------
    try:
        from research import nba_matchup_fit as F
        ck("the report's pre-registered rule hashes to the recorded sha256 (validate / test refuse otherwise)",
           F.prereg_hash() == F._recorded_hash(), f"{F.prereg_hash()} vs {F._recorded_hash()}")
        if Pm.get("prereg_sha256"):
            ck("the committed fit was frozen under that same rule", Pm["prereg_sha256"] == F.prereg_hash())
    except ImportError:
        ck("numpy absent: the pre-registration hash check is skipped", True)

    # ---- the payload contract on a synthetic league -------------------------------------
    run_payload(ck)

    # ---- the 2026-10-23 reviews' fixes and guard gaps ---------------------------------------
    run_review_fixes(ck)
    run_recheck(ck)


# ---- a synthetic league for the payload checks ------------------------------------
def synthetic_league(n_days=14):
    """Four clubs, six players a side, deterministic varied stat lines: enough
    for the Book, the replay and a board."""
    import random
    rng = random.Random(7)
    teams = ("AAA", "BBB", "CCC", "DDD")
    roster = {t: [1000 + 10 * i + j for j in range(6)] for i, t in enumerate(teams)}
    pos = ("G", "G", "F", "F", "C", "G")
    entries = []
    gid = 1
    for d in range(n_days):
        day = f"2024-11-{d + 1:02d}"
        pairs = (("AAA", "BBB"), ("CCC", "DDD")) if d % 2 == 0 else (("AAA", "CCC"), ("BBB", "DDD"))
        for home, away in pairs:
            players, T = {}, []
            for s_, ab in ((0, home), (1, away)):
                t = array.array("i", [0] * len(D.TF))
                t[D.TI["gid"]], t[D.TI["side"]] = gid, s_
                t[D.TI["poss"]], t[D.TI["secs"]] = 98 + rng.randint(0, 6), 2880
                tot = {"pts": 0, "fga": 0, "fgm": 0, "a_3": 0, "a_rim": 0, "fta": 0, "tov": 0, "oreb": 0, "dreb": 0,
                       "ast": 0}
                for j, pid in enumerate(roster[ab]):
                    mn = (34, 32, 30, 28, 26, 14)[j]
                    pts = rng.randint(4, 30) if j < 5 else rng.randint(0, 8)
                    fga = max(1, pts // 2 + rng.randint(0, 4))
                    r = _entry_row(pid, s_, D.pos_group(pos[j]), mn, pts, fga=fga, fgm=pts // 2, tpm=rng.randint(0, 4),
                                   tpa=rng.randint(2, 8), fta=rng.randint(0, 6), oreb=rng.randint(0, 3),
                                   dreb=rng.randint(1, 9), ast=rng.randint(0, 9), tov=rng.randint(0, 4),
                                   stl=rng.randint(0, 2), blk=rng.randint(0, 2), a_rim=rng.randint(0, 6),
                                   a_short=rng.randint(0, 4), a_mid=rng.randint(0, 3), a_3=rng.randint(1, 7),
                                   pull_a=rng.randint(0, 4), spot3_a=rng.randint(0, 4), ast_m2=rng.randint(0, 3),
                                   ast_m3=rng.randint(0, 2), m_3=rng.randint(0, 3), poss_off=60, poss_def=60,
                                   tm_fga=55, tm_fta=12, tm_tov=8, tm_fgm=26, oreb_ch=30, dreb_ch=30)
                    r[D.PI["starter"]] = 1 if j < 5 else 0
                    r[D.PI["gid"]] = gid
                    players[pid] = r
                    for k in tot:
                        tot[k] += r[D.PI[k]]
                for k, v in tot.items():
                    if k in D.TI:
                        t[D.TI[k]] = v
                t[D.TI["miss"]] = tot["fga"] - tot["fgm"]
                t[D.TI["oreb_ch"]] = t[D.TI["miss"]]
                t[D.TI["live_starts"]] = 30
                T.append(t)
            names = {pid: f"Player {pid}" for pid in players}
            entries.append({"id": str(gid), "date": day + "T01:00Z", "day": day, "season": 2024, "st": 2,
                            "home": home, "away": away, "players": players, "teams": (T[0], T[1]), "names": names})
            gid += 1
    return entries


def synthetic_params():
    nodes = [0.55 + 0.05 * i for i in range(20)]
    bands = [{"lo": lo, "hi": hi, "n": 100, "nodes": nodes} for lo, hi in
             ((0.0, 12.0), (12.0, 16.0), (16.0, 20.0), (20.0, 24.0), (24.0, 28.0), (28.0, 32.0), (32.0, 36.0),
              (36.0, 99.0))]
    disp = {m: [1.2, 0.01] for m in NM.MARKETS + NM.AUX}
    disp.update({m: [2.0, 0.0] for m in NM.COMBOS})
    disp["pts"] = [2.5, 0.0]
    disp["dk"] = [3.0, 0.0]
    coefs = {}
    for m in NM.MARKETS:
        c = {"_icpt": 0.02, "pace": 0.9, "home": 0.01, "oe_all": 0.5}
        for name in NM.RUNGS[4][m][:2]:
            c[name] = 0.7
        c["tm_out"] = 0.0          # a rung held at weight 0: shown, mult 1
        coefs[m] = c
    for a in NM.AUX:
        coefs[a] = {"_icpt": 0.0, "pace": 1.0, "home": 0.0}
    base = {m: {"_icpt": 0.0, "pace": 1.0, "home": 0.01} for m in NM.MARKETS}
    base.update({a: coefs[a] for a in NM.AUX})
    return {"rate_W": {s: [0.5, 1.0, 50.0] for s in M.RATE_STATS}, "team_K": {}, "rho": {}, "role_K": {},
            "oe_K": {}, "oe_rho": 0.3, "comp_sd": {},
            "min_beta": [2.0, 0.2, 0.3, 0.3, 0.1, 0.0, 0.0, 2.0, -0.5, 0.0, 0.0, -1.0, 0.0],
            "min_dist": bands, "min_beta6": [2.0, 0.2, 0.3, 0.3, 0.1, 0.0, 0.0, 2.0, -0.5, 0.0, 0.0, -1.0, 0.0,
                                             5.0, 2.0, -3.0, -1.0], "min_dist6": bands,
            "disp": disp, "coefs": coefs, "coefs_base": base,
            "shipped": dict({m: {"rungs": [1, 2, 4]} for m in NM.MARKETS}, minutes="m6", dk_fp_replaces_avg=True)}


def run_payload(ck):
    entries = synthetic_league()
    P = synthetic_params()
    saved = NM._PARAMS
    NM._PARAMS = P
    try:
        book, logs = NM.replay(entries, P)
        state = {"book": book, "logs": logs, "through": "2024-11-14", "built": 0}
        out_pid = 1000          # AAA's lead guard ruled out
        games = [{"game_id": "900", "home": "AAA", "away": "DDD", "start_ts": 0.0}]
        b = NM.board("2024-11-16", games=games, statuses={out_pid: {"value": "out", "source": "test", "ts": 0}},
                     state=state, with_market=False, min_minutes=0.0)
        pls = [p for p in b["players"] if p["markets"]]
        outs = [p for p in b["players"] if p["status"]["value"] == "out"]
        ck("payload: the ruled-out player is listed 'out' with no markets; the rest are priced",
           len(outs) == 1 and outs[0]["player_id"] == str(out_pid) and len(pls) == 11, f"{len(pls)} {len(outs)}")
        mono = all(all(ln["p"] >= nxt["p"] for ln, nxt in zip(mk["lines"], mk["lines"][1:])) for p in pls
                   for mk in p["markets"])
        band = all(0.0 <= ln["p_lo"] <= ln["p"] <= ln["p_hi"] <= 1.0 for p in pls for mk in p["markets"]
                   for ln in mk["lines"])
        ck("payload: every market's P(stat >= k) is non-increasing in k, inside [0, 1], inside its band", mono and band)
        ok = True
        detail = ""
        for p in pls:
            for mk in p["markets"]:
                pr = mk["dist"]["params"]
                if pr.get("rate_raw") is None:
                    continue
                st = {"pts": "pts", "reb": "reb", "ast": "ast", "fg3m": "fg3m"}[mk["stat"]]
                fs = [f for f in p["factors"] if f["effect"]["stat"] == st]
                prod = math.exp(pr["intercept"])
                for f in fs:
                    prod *= math.exp(f["detail"]["coef"] * f["detail"]["x"])
                want = pr["rate_adj"] / pr["rate_raw"]
                if abs(prod / want - 1.0) > 1e-4:
                    ok = False
                    detail = f"{p['name']} {st} {prod} vs {want}"
        ck("payload: a market's adjusted rate is its as-of rate x exp(intercept) x the product of its factors' "
           "exp(coef x value) -- no decorative factor, none missing", ok, detail)
        dec = [f"{p['name']} {f['factor']} {f['effect']['mult']}" for p in pls for f in p["factors"]
               if f["effect"]["weight"] == "shipped"
               and abs(f["effect"]["mult"] / math.exp(f["detail"]["coef"] * f["detail"]["x"]) - 1.0) > 1e-5]
        ck("payload: a shipped row's effect.mult IS exp(coef x value) (never decorative; re-check R3)",
           not dec and any(f["effect"]["weight"] == "shipped" for p in pls for f in p["factors"]), str(dec[:2]))
        zero = [f for p in pls for f in p["factors"] if str(f["effect"]["weight"]).startswith("0")]
        ck("payload: a weight-0 rung is ONE row per market, mult 1, effect 0, its measured channels in the detail "
           "(rung 6's teammates-out value non-zero for the out player's teammates)",
           zero and all(f["effect"]["mult"] == 1.0 and f["effect"]["delta_mean"] == 0.0 for f in zero)
           and all(sum(1 for f in p["factors"] if str(f["effect"]["weight"]).startswith("0")
                       and f["effect"]["stat"] == "pts" and f["detail"]["rung"] == 6) == 1 for p in pls)
           and any(abs(_tm_out_value(p, "pts")) > 0 for p in pls if p["team"] == "AAA"))
        ck("payload: evidence carries no None-valued key",
           all(v is not None for p in pls for f in p["factors"] for v in (f.get("evidence") or {}).values()))
        def walk(o):
            if isinstance(o, float):
                return math.isfinite(o)
            if isinstance(o, dict):
                return all(walk(v) for v in o.values())
            if isinstance(o, list):
                return all(walk(v) for v in o)
            return True
        ck("payload: no NaN / inf anywhere", walk(b))
        ck("payload: every role and opponent-profile fact carries a unit (share drawn as %, others as numbers)",
           all("unit" in f for p in pls for f in list(p["role"].values()) + list(p["opponent_profile"].values()))
           and all(p["role"]["rate_ast"]["unit"] == "per_min" and p["role"]["three_share"]["unit"] == "share"
                   and p["opponent_profile"]["pace"]["rate_unit"] == "poss_per_48" for p in pls))
        dn = M._day("2024-11-16")
        ck("payload: comparables are strictly earlier games",
           all(c["date"] < "2024-11-16" for p in pls for c in p["comparables"]) and dn > 0)
        dk = [mk for p in pls for mk in p["markets"] if mk["stat"] == "dk_fp"]
        ck("payload: dk_fp is priced for every player, its SD positive",
           len(dk) == len(pls) and all(mk["dist"]["params"]["sd"] > 0 for mk in dk))
        res = NM.dfs_projections("2024-11-16", [{"name": "Player 1001", "team": "AAA"}, {"name": "Nobody", "team": "AAA"},
                                                {"bad": 1}], state=state, statuses={}, games=games)
        r0 = res[0]
        ck("dfs hook: a pool player matched by name + team gets proj / sd / monotone p / quantiles; the unmatched None",
           r0 and r0["proj"] > 0 and r0["sd"] > 0 and res[1] is None and res[2] is None
           and all(r0["p"][a] >= r0["p"][b] for a, b in zip(NM.DK_LINES, NM.DK_LINES[1:]))
           and r0["q"]["10"] <= r0["q"]["50"] <= r0["q"]["90"], str(r0))
        bad = NM.dfs_projections("2024-11-16", [{"name": "x"}], state={"book": None}, statuses={}, games=games)
        ck("dfs hook: a broken state never raises into the builder (all None)", bad == [None])
    finally:
        NM._PARAMS = saved


def _tm_out_value(pl, stat):
    """A player's measured teammates-out value for one market, wherever the
    payload keeps it (a shipped row's detail, or a weight-0 rung's channels)."""
    for f in pl.get("factors") or []:
        if f["effect"]["stat"] != stat:
            continue
        d = f.get("detail") or {}
        if d.get("feature") == "tm_out":
            return d.get("x") or 0.0
        for c in d.get("channels") or []:
            if c.get("feature") == "tm_out":
                return c.get("value") or 0.0
    return 0.0


def _safe(ck, name, fn):
    """A check that records FAIL (not a crash) when the code under test lacks
    what it reads -- so this file can be run against older modules."""
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    ck(name, bool(ok), detail)


def fixture_asym():
    """Possessions 3 home / 2 away, a period-start shot inside 7 s, a quick
    shot after a defensive rebound, a 13-ft and a 14-ft two:

      715  home #1 makes a 13-footer      period start + 5 s: NOT early (no live-ball start); short zone
      690  away #11 misses                 -> 688 home #2 defensive rebound (away possession 1 ends)
      683  home #2 makes a 14-ft two       5 s after the live rebound: EARLY; mid zone (14 ft+)
      670  away #12 loses it, #3 steals    (away possession 2 ends)
      650  home #3 misses                  the period ends with home holding the ball: home possession 3
    """
    plays = [
        _play(1, 715, 0, P.FGA, 2, 1, 13, 5, p1=1, hs=2),
        _play(1, 690, 1, P.FGA, 2, 0, 18, 5, p1=11, hs=2),
        _play(1, 688, 0, P.DREB, p1=2, hs=2),
        _play(1, 683, 0, P.FGA, 2, 1, 14, 5, p1=2, hs=4),
        _play(1, 670, 1, P.TOV, p1=12, p2=3, hs=4, flags=P.F_STEAL),
        _play(1, 650, 0, P.FGA, 2, 0, 20, 5, p1=3, hs=4),
        _play(1, 0, -1, P.END_PERIOD, hs=4),
    ]
    a = array.array("i")
    for p in plays:
        a.extend(p)
    xt = array.array("i", [D.shot_bits("Jump Shot"), D.shot_bits("Jump Shot"), 0, D.shot_bits("Jump Shot"), 0,
                           D.shot_bits("Jump Shot"), 0])
    box = [_box(i, 0, 1, mn=12) for i in (1, 2, 3, 4, 5)] + [_box(i, 1, 1, mn=12) for i in (11, 12, 13, 14, 15)]
    return {"id": 2, "season": 2024, "st": 2, "date": "2024-11-01T00:00Z", "home": "AAA", "away": "BBB",
            "home_id": "1", "away_id": "2", "home_score": 4, "away_score": 0, "periods": 1, "neutral": False,
            "plays": a, "xt": xt, "box": box, "players": {b["pid"]: b["side"] for b in box}}


def _tsnap(cur, prev, lg_cur, lg_prev):
    """A team snapshot dict for concessions(): each argument {TSUM field: value}."""
    def arr(d):
        a = [0.0] * len(M.TSUM)
        for k, v in d.items():
            a[M.TS[k]] = float(v)
        return a
    z = arr({})
    return {"def": [arr(cur), arr(prev), z], "off": [z, z, z], "oe": [[0.0] * 56] * 3, "g": 1, "tg": 1,
            "lg": [arr(lg_cur), arr(lg_prev)]}


def _team_entry(gid, day, home, away, rows_home, rows_away):
    """A cut game with the given player rows {pid: (pos_g, minutes, pts)} a side."""
    players = {}
    for side, rows in ((0, rows_home), (1, rows_away)):
        for pid, (pg, mn, pts) in rows.items():
            r = _entry_row(pid, side, pg, mn, pts)
            r[D.PI["gid"]] = gid
            players[pid] = r
    T = []
    for s_ in (0, 1):
        t = array.array("i", [0] * len(D.TF))
        t[D.TI["gid"]], t[D.TI["side"]], t[D.TI["poss"]], t[D.TI["secs"]] = gid, s_, 100, 2880
        T.append(t)
    return {"id": str(gid), "date": day, "season": 2024, "home": home, "away": away, "players": players,
            "teams": (T[0], T[1]), "names": {}}


def run_review_fixes(ck):
    """The fixes and guard gaps of the 2026-10-23 reviews (research/reviews/
    nba_matchup.md F1-F13, nba_matchup_orchestrator.md O1-O3)."""
    # -- F3: team possessions, early clock at a period start, the 13 / 14 ft zone line ------
    def asym():
        R, T, _q = D.cut_game(fixture_asym())
        return (T[0][D.TI["poss"]] == 3 and T[1][D.TI["poss"]] == 2 and R[1][D.PI["poss_off"]] == 3
                and R[11][D.PI["poss_off"]] == 2), f"team poss {T[0][D.TI['poss']]}/{T[1][D.TI['poss']]}"
    _safe(ck, "possessions on an ASYMMETRIC fixture: 3 home / 2 away, each credited to the side with the ball", asym)

    def early():
        R, T, _q = D.cut_game(fixture_asym())
        return (R[1][D.PI["tr_a"]] == 0 and R[2][D.PI["tr_a"]] == 1 and T[0][D.TI["tr_a"]] == 1), \
            f"#1 {R[1][D.PI['tr_a']]} #2 {R[2][D.PI['tr_a']]}"
    _safe(ck, "early clock: a shot 5 s after a PERIOD start is not early (no live-ball start); 5 s after a "
              "defensive rebound is", early)

    def zones():
        R, _T, _q = D.cut_game(fixture_asym())
        return (R[1][D.PI["a_short"]] == 1 and R[1][D.PI["a_mid"]] == 0 and R[2][D.PI["a_mid"]] == 1
                and R[2][D.PI["a_short"]] == 0), ""
    _safe(ck, "zones: a 13-ft two is 'short' (4-13 ft), a 14-ft two is 'mid' (14 ft+)", zones)

    # -- F3: observed-over-expected sums only the OPPONENTS' players --------------------------
    def oe_sums():
        bk = M.Book()
        bk.roll(2024)
        e = _team_entry(1, "2024-11-01", "AAA", "BBB", {100: (0, 30, 20), 101: (2, 30, 10)},
                        {200: (0, 30, 7), 201: (1, 30, 5)})
        ex = {"1": {100: {"pts": 15.0}, 101: {"pts": 12.0}, 200: {"pts": 9.0}, 201: {"pts": 4.0}}}
        bk.add_date([e], expected=ex)
        j = M.oe_idx("pts", 0)
        b_ = bk.team("BBB")["oe"][0]
        a_ = bk.team("AAA")["oe"][0]
        jg = M.oe_idx("pts", 1)
        return (b_[j] == 30 and b_[j + 1] == 27.0 and a_[j] == 12 and a_[j + 1] == 13.0
                and b_[jg] == 20 and b_[jg + 1] == 15.0), f"BBB {b_[j]}/{b_[j + 1]} AAA {a_[j]}/{a_[j + 1]}"
    _safe(ck, "observed-over-expected: a defense's sums hold only the players who FACED it (BBB: AAA's 30 pts on "
              "27 expected; by position, AAA's guard 20 on 15)", oe_sums)

    # -- F3: concessions shrink with K toward last season x rho ---------------------------
    def conc():
        t = _tsnap({"pts": 110, "poss": 100}, {"pts": 120, "poss": 100}, {"pts": 1000, "poss": 1000},
                   {"pts": 1000, "poss": 1000})
        c = M.concessions(t, "def", team_K={"def:pts": 100.0}, rho={"def:pts": 0.5})["pts"]
        want = math.exp(0.5 * math.log(1.1) + 0.5 * (0.5 * 0.5 * math.log(1.2)))
        return abs(c["ratio"] - want) < 1e-12 and abs(c["raw_ratio"] - 1.1) < 1e-12, f"{c['ratio']} vs {want}"
    _safe(ck, "concessions: 110 pts on 100 poss (1.10x) with K = 100 and last season 1.20x on 100 poss at rho 0.5 "
              "-> exp(0.5 ln 1.10 + 0.5 x 0.5 x 0.5 ln 1.20)", conc)

    # -- F3: parse_box's 'played' rule ----------------------------------------------------
    def box_rule():
        d = {"boxscore": {"players": [{"team": {"id": "1"}, "statistics": [{
            "keys": ["minutes", "points", "rebounds"],
            "athletes": [
                {"athlete": {"id": "1", "displayName": "Starter", "position": {"abbreviation": "G"}}, "starter": True,
                 "stats": ["30", "12", "4"]},
                {"athlete": {"id": "2", "displayName": "Dnp", "position": {"abbreviation": "F"}},
                 "didNotPlay": True, "reason": "COACH'S DECISION", "stats": ["0", "0", "0"]},
                {"athlete": {"id": "3", "displayName": "Zero", "position": {"abbreviation": "C"}}, "stats": ["0", "0", "0"]},
                {"athlete": {"id": "4", "displayName": "Cameo", "position": {"abbreviation": "G"}}, "stats": ["0", "0", "1"]},
            ]}]}]}}
        b = {r["pid"]: r for r in D.parse_box(d, {"1": 0})}
        return ([b[i]["played"] for i in (1, 2, 3, 4)] == [1, 0, 0, 1] and b[2]["dnp_reason"] == "COACH'S DECISION"), \
            str([b[i]["played"] for i in (1, 2, 3, 4)])
    _safe(ck, "parse_box: played = not DNP and a stat line with minutes or any stat (a DNP row with zeros, an "
              "all-zero row: not played; a 0-minute rebound: played)", box_rule)

    # -- F3: the rotation window ----------------------------------------------------------
    def rot():
        bk = M.Book()
        bk.roll(2024)
        seen = []
        for g in range(1, 7):
            home = {101: (0, 32, 10)}
            if g == 1:
                home[100] = (0, 36, 25)
            bk.add_date([_team_entry(g, f"2024-11-{g:02d}", "AAA", "ZZZ", home, {900 + g: (0, 30, 5)})])
            seen.append((g, 100 in bk.rotation("AAA"), 101 in bk.rotation("AAA")))
        return seen[3] == (4, True, True) and seen[4] == (5, False, True) and seen[5] == (6, False, True), str(seen)
    _safe(ck, "rotation: a regular stays in it for three team games after his last, then leaves (his absence "
              "must not count as 'teammate out' for weeks)", rot)

    # -- F3 / F10: ONE production game day ----------------------------------------------------
    def gday():
        W = __import__("research.nba_matchup_walk", fromlist=["game_day"])
        return (NM.game_day("2024-11-02T03:30Z") == "2024-11-01" and NM.game_day("2024-11-01T23:00Z") == "2024-11-01"
                and NM.game_day("2024-11-02T06:30Z") == "2024-11-02" and W.game_day is NM.game_day), ""
    _safe(ck, "production game day is the Mountain-time date (03:30 UTC is the evening before), and research's "
              "is the same function", gday)

    # -- F3: the M6 minutes model reads its context; the minutes clamp ---------------------------
    def m6():
        row = {"mf": {"e2": 30.0, "e6": 30.0, "e15": 30.0, "avg_cur": 30.0, "avg_prev": 30.0, "has_prev": 1.0,
                      "st_e": 1.0, "b2b": 0.0, "long_rest": 0.0, "g_cur": 10.0}, "missed_last": 0.0, "traded": 0.0,
               "m6": [0.0, 0.0, 0.0, 0.0]}
        beta6 = [0.0] * 13 + [10.0, 0.0, 0.0, 0.0]
        beta6[2] = 1.0
        a = NM.pred_minutes(row, beta6, m6=True)
        b = NM.pred_minutes(dict(row, m6=[0.5, 0.0, 0.0, 0.0]), beta6, m6=True)
        hi = NM.pred_minutes(dict(row, m6=[9.0, 0.0, 0.0, 0.0]), beta6, m6=True)
        lo = NM.pred_minutes(dict(row, m6=[-9.0, 0.0, 0.0, 0.0]), beta6, m6=True)
        return a == 30.0 and b == 35.0 and hi == 46.0 and lo == 1.0, f"{a} {b} {hi} {lo}"
    _safe(ck, "minutes: the M6 model reads its context terms (+0.5 x 10 = +5 min) and every projection is "
              "clamped to 1-46", m6)

    # -- the synthetic league: strong_fit, the past date, the band, questionable, the minutes floor ------
    entries = synthetic_league()
    saved = NM._PARAMS
    try:
        P = synthetic_params()
        NM._PARAMS = P
        book, logs = NM.replay(entries, P)
        state = {"book": book, "logs": logs, "through": "2024-11-14", "built": 0}
        games = [{"game_id": "900", "home": "AAA", "away": "DDD", "start_ts": 0.0}]

        def fit_unit():
            beta = P["coefs"]["pts"]["oe_all"]
            fm = NM.fit_multiplier(P, "pts", {"oe_all": math.log(0.92) / beta, "tm_out": 0.0, "pace": 0.3})
            lab = NM._labels(0.6, 0.55, 0.65, 50.0, NM.fit_side(fm))
            up = NM.fit_side(NM.fit_multiplier(P, "pts", {"oe_all": math.log(1.08) / beta}))
            return (abs(fm - 0.92) < 1e-12 and NM.fit_side(fm) == "no" and "strong_fit" in lab and up == "yes"
                    and NM.fit_side(1.049) is None and NM.fit_side(0.951) is None), f"{fm} {NM.fit_side(fm)} {up}"
        _safe(ck, "strong_fit: an 8% unfavourable defense shift is fit side NO (never YES), +8% is YES, inside "
                  "+/-5% nothing; rung 1's pace never counts", fit_unit)

        def tm_alone():
            P2 = synthetic_params()
            P2["coefs"]["pts"]["tm_out"] = 3.0
            fm = NM.fit_multiplier(P2, "pts", {"tm_out": 0.4})
            return fm == 1.0 and NM.fit_side(fm) is None, str(fm)
        _safe(ck, "strong_fit: teammates out alone never earns it (tm_out is context, not the defense)", tm_alone)

        def fit_payload(Pv, want_no, label):
            NM._PARAMS = Pv
            b = NM.board("2024-11-16", games=games, statuses={1000: {"value": "out", "source": "t", "ts": 0}},
                         state=state, with_market=False, min_minutes=0.0)
            NM._PARAMS = P
            bad, n_yes, n_no = [], 0, 0
            for pl in b["players"]:
                for mk in pl["markets"]:
                    st = mk["stat"]
                    fm = mk.get("fit_multiplier")
                    if st in ("pts", "reb", "ast", "fg3m"):
                        # the definition, written out here (not read from the module under test)
                        mk_ = {"fg3m": "tpm"}.get(st, st)
                        names = {n for k in (2, 3, 4, 5) for n in NM.RUNGS[k][mk_]} | {"oe_form"}
                        prod = 1.0
                        for f in pl["factors"]:
                            if f["effect"]["stat"] == st and f["detail"].get("feature") in names:
                                prod *= math.exp(f["detail"]["coef"] * f["detail"]["x"])
                        if fm is None or abs(prod / fm - 1) > 1e-4:
                            bad.append(f"{pl['name']} {st} multiplier {fm} vs factors {prod}")
                    for ln in mk["lines"]:
                        if "fit_side" not in ln:
                            bad.append("a line without fit_side")
                            break
                        side = ln["fit_side"]
                        has = "strong_fit" in (ln.get("labels") or [])
                        want = None if fm is None else ("yes" if fm >= 1.05 else ("no" if fm <= 0.95 else None))
                        if has != (side is not None) or side != want:
                            bad.append(f"{pl['name']} {st} {ln['k']} label {has} side {side} mult {fm}")
                        n_yes += side == "yes"
                        n_no += side == "no"
            ok = not bad and (n_no > 0 if want_no else n_yes + n_no == 0)
            return ok, f"{label}: {bad[:2]} yes {n_yes} no {n_no}"

        Pd = synthetic_params()
        for m in NM.MARKETS:
            Pd["coefs"][m]["oe_all"] = 6.0
        _safe(ck, "strong_fit on the payload: every line carries fit_side; the label sits exactly where the "
                  "DEFENSE terms (rungs 2-5 + form, read back off the factor rows) move the mean 5%+, on the side "
                  "they favour -- an unfavourable defense puts it on NO", lambda: fit_payload(Pd, True, "defense"))
        Pt = synthetic_params()
        for m in NM.MARKETS:
            Pt["coefs"][m] = {k: v for k, v in Pt["coefs"][m].items() if k in ("_icpt", "pace", "home")}
            Pt["coefs"][m]["tm_out"] = 3.0
            Pt["shipped"][m] = {"rungs": [1, 6]}
        _safe(ck, "strong_fit on the payload: a big teammates-out effect with no defense term never earns it "
                  "(AAA's lead guard ruled out)", lambda: fit_payload(Pt, False, "teammates"))

        # -- F2: a past date --------------------------------------------------------------------
        def past_board():
            b = NM.board("2024-11-14", games=games, statuses={}, state=state, with_market=False, min_minutes=0.0)
            r = NM.dfs_projections("2024-11-10", [{"name": "Player 1001", "team": "AAA"}], state=state, statuses={},
                                   games=games)
            return (not b["players"] and any("already holds" in n for n in b["notes"]) and r == [None]), \
                f"{len(b['players'])} players; dfs {r}"
        _safe(ck, "a past date: board() for a date the book already holds prices nothing and says why; the DFS "
                  "hook returns None", past_board)

        def past_hits():
            day = "2024-11-10"
            sn = book.player(1001, day)
            own_t = book.team("AAA")
            ctx = NM.team_context(book, "AAA", day, set(), P, own_t)
            pl = NM.player_payload(1001, "Player 1001", "AAA", "DDD", "900", True, sn, book, P, ctx, logs[1001], day,
                                   {"value": "active"}, NM._rank_table(book, P, ["AAA", "BBB", "CCC", "DDD"]))
            n_before = sum(1 for h in logs[1001] if h[0] < M._day(day))
            ns = {ln["hit_all"]["n"] for mk in pl["markets"] for ln in mk["lines"]}
            nr = {ln["hit_recent"]["n"] for mk in pl["markets"] for ln in mk["lines"]}
            return ns == {n_before} and nr == {min(10, n_before)} and n_before < len(logs[1001]), \
                f"hit_all n {ns} vs {n_before} earlier of {len(logs[1001])}"
        _safe(ck, "a past date: the hit tables count only games BEFORE the date", past_hits)

        # -- F3: band strictly around p; questionable is not out; the minutes floor -------------
        b0 = NM.board("2024-11-16", games=games, statuses={}, state=state, with_market=False, min_minutes=0.0)

        def band():
            lines = [ln for pl in b0["players"] for mk in pl["markets"] for ln in mk["lines"] if 0.02 < ln["p"] < 0.98]
            return lines and all(ln["p_lo"] < ln["p"] < ln["p_hi"] for ln in lines), f"{len(lines)} lines"
        _safe(ck, "band: away from 0 and 1 every line's p_lo < p < p_hi strictly (a collapsed band is a bug)", band)

        def quest():
            bq = NM.board("2024-11-16", games=games, statuses={1001: {"value": "questionable", "source": "t", "ts": 0}},
                          state=state, with_market=False, min_minutes=0.0)
            aaa = [pl for pl in bq["players"] if pl["team"] == "AAA" and pl["markets"]]
            return aaa and all(_tm_out_value(pl, "pts") == 0.0 for pl in aaa) \
                and not any("Teammates ruled out" in n for pl in aaa for n in pl["notes"]), ""
        _safe(ck, "a QUESTIONABLE teammate is not 'out': his teammates' rung-6 value stays 0", quest)

        def floor():
            hi = NM.board("2024-11-16", games=games, statuses={}, state=state, with_market=False, min_minutes=28.0)
            pr = [pl["minutes"]["projected"] for pl in hi["players"] if pl["markets"]]
            all_ = [pl["minutes"]["projected"] for pl in b0["players"] if pl["markets"]]
            return pr and all(x >= 28.0 for x in pr) and len(pr) < len(all_), f"{len(pr)} of {len(all_)}"
        _safe(ck, "the board's minutes floor: min_minutes 28 lists only players projected 28+", floor)

        # -- F13: replaces_avg is the shipped verdict's value ------------------------------------
        def rep_avg():
            out = []
            for flag in (False, True):
                Pf = synthetic_params()
                Pf["shipped"]["dk_fp_replaces_avg"] = flag
                NM._PARAMS = Pf
                r = NM.dfs_projections("2024-11-16", [{"name": "Player 1001", "team": "AAA"}], state=state,
                                       statuses={}, games=games)
                out.append(r[0]["replaces_avg"] if r[0] else None)
            NM._PARAMS = P
            return out == [False, True], str(out)
        _safe(ck, "dfs hook: replaces_avg is shipped.dk_fp_replaces_avg's VALUE (False passes through as False)",
              rep_avg)
    finally:
        NM._PARAMS = saved


def run_recheck(ck):
    """The re-check round (research/reviews/nba_matchup.md, RE-CHECK R1-R3)."""
    import inspect
    import shutil
    import tempfile
    import deep_cache
    saved, saved_dir = NM._PARAMS, deep_cache.CACHE_DIR
    tmp = tempfile.mkdtemp(prefix="nbam_guard_")
    try:
        P = synthetic_params()
        NM._PARAMS = P

        # -- R1: the replay never takes in the owner's current MT day --------------------------
        def same_day():
            deep_cache.CACHE_DIR = tmp
            games = {}
            for e in synthetic_league():
                g = dict(e, date=e["day"] + "T20:00Z")          # an evening tip: MT day = its day
                games[g["id"]] = g
            early = _team_entry(990, "2024-11-16T19:00Z", "AAA", "BBB", {1000: (0, 30, 20)}, {1010: (0, 30, 9)})
            early.update(st=2, season=2024)
            games["990"] = early
            deep_cache.save(D.STATE_KEY.format(season=2024), {"schema": D.SCHEMA, "season": 2024, "games": games})
            kw = {"today": "2024-11-16"} if "today" in inspect.signature(NM.rebuild).parameters else {}
            st = NM.rebuild(2024, **kw)
            state = NM.load_book(2024)
            b = NM.board("2024-11-16", games=[{"game_id": "991", "home": "CCC", "away": "DDD", "start_ts": 0.0}],
                         statuses={}, state=state, with_market=False, min_minutes=0.0)
            priced = [p for p in b["players"] if p["markets"]]
            return (st["through"] == "2024-11-14" and st["n_games"] == 28 and len(priced) == 12
                    and not any("already holds" in n for n in b["notes"])), \
                f"through {st['through']}, {st['n_games']} games, {len(priced)} priced, notes {b['notes'][-1:]}"
        _safe(ck, "the replay never takes in the owner's current MT day: with today's early game final in the data "
                  "store, the book stops at yesterday and the board for today still prices the late game (re-check R1)",
              same_day)
        deep_cache.CACHE_DIR = saved_dir

        # -- R2: the combos' and dk_fp's fit multipliers -----------------------------------------
        def combo_fit():
            Pd = synthetic_params()
            for m in NM.MARKETS:
                Pd["coefs"][m]["oe_all"] = 6.0
            NM._PARAMS = Pd
            book, logs = NM.replay(synthetic_league(), Pd)
            state = {"book": book, "logs": logs, "through": "2024-11-14", "built": 0}
            b = NM.board("2024-11-16", games=[{"game_id": "900", "home": "AAA", "away": "DDD", "start_ts": 0.0}],
                         statuses={}, state=state, with_market=False, min_minutes=0.0)
            NM._PARAMS = P
            base = {"pts": "pts", "reb": "reb", "ast": "ast", "fg3m": "tpm"}
            bad, spread = [], 0.0
            for pl in b["players"]:
                mk = {m["stat"]: m for m in pl["markets"]}
                if not mk:
                    continue
                ra = {base[k]: mk[k]["dist"]["params"]["rate_adj"] for k in base}
                fm = {base[k]: mk[k]["fit_multiplier"] for k in base}
                for c, parts in NM.COMBOS.items():
                    want = sum(ra[x] for x in parts) / sum(ra[x] / fm[x] for x in parts)
                    spread = max(spread, max(fm[x] for x in parts) / min(fm[x] for x in parts) - 1.0)
                    if abs(mk[c]["fit_multiplier"] / want - 1.0) > 1e-4:
                        bad.append(f"{pl['name']} {c} {mk[c]['fit_multiplier']} vs {want}")
                xs = {f["detail"]["feature"]: f["detail"]["x"] for f in pl["factors"]
                      if f["effect"]["stat"] == "pts" and f["detail"].get("feature") in ("pace", "home")}
                r = dict(ra)
                for a_ in NM.AUX:
                    cf = Pd["coefs"][a_]
                    r[a_] = pl["role"][f"rate_{a_}"]["value"] * math.exp(
                        cf.get("_icpt", 0.0) + cf.get("pace", 0.0) * xs["pace"] + cf.get("home", 0.0) * xs["home"])
                lin = sum(w * r[k] for k, w in NM.DK_W.items())
                lin0 = sum(w * r[k] / fm.get(k, 1.0) for k, w in NM.DK_W.items())
                if abs(mk["dk_fp"]["fit_multiplier"] / (lin / lin0) - 1.0) > 2e-4:
                    bad.append(f"{pl['name']} dk_fp {mk['dk_fp']['fit_multiplier']} vs {lin / lin0}")
            return not bad and spread > 0.02, f"{bad[:2]} largest component spread {spread:.3f}"
        _safe(ck, "fit multipliers of the combos = adjusted rate / sum(component rate / component multiplier); "
                  "dk_fp's the DK-weighted linear rate over the same with steals / blocks / turnovers at 1 (re-check R2)",
              combo_fit)
    finally:
        NM._PARAMS = saved
        deep_cache.CACHE_DIR = saved_dir
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    fails = []
    n = [0]

    def ck(name, cond, detail=""):
        n[0] += 1
        if not cond:
            fails.append((name, detail))
            print(f"FAIL {name} {detail}")
    run(ck)
    print(f"guards_nba_matchup: {n[0] - len(fails)}/{n[0]} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
