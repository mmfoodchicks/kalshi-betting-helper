"""Guards for the Pick6 paper ledger (2026-11-27; pick6_ledger.py, static/pick6log.js, the /api/pick6/ledger
routes; the owner's backlog item 7). What each group proves:

  * P1-P2 entry validation: 2-6 picks, two different players, each player and stat once, the stat one the
    sport offers (or a hand-graded one), a line with at most two decimals, a positive stake, a schedule the
    entry can reach with DraftKings' payout for all correct -- each refusal names its field;
  * P3-P5 the grading rules on fixtures: a hit, a miss, an exact line (TIE_RULE 'loss', the conservative
    reading, until DraftKings' rule is confirmed), a void (DraftKings' minimum-play rule), and the entry's
    paper result off the owner's schedule, including a reduced entry that waits for DraftKings' figure;
  * P6-P7 the box parsers (Sleeper's weekly rows, MLB's statsapi box, ESPN's NBA summary through the
    Matchups engine's own parser) and when each box is final;
  * P8 Vigil's figure at entry: the exact line only, the complement for the other side, none after the game
    date or off any other line;
  * P9-P10 end to end on a scratch ledger: entered, graded, overridden, re-graded, the reduced payout typed,
    and the record's arithmetic against a hand computation; the record path is SQL sums (no fetchall);
  * P11 the routes stay behind the app's basic auth and refuse bad input; P12 the screen: no inline
    handlers, every name escaped, loaded under the Pick 6 view; P13 the background owner grades.

Hermetic: every box is a fixture and the store is a scratch file; nothing is fetched.

    python3 tests/guards_auto_pick6.py       # exits 1 on any failed check
"""
import base64
import datetime
import inspect
import math
import os
import re
import sys
import tempfile

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)

_TODAY = datetime.date(2026, 11, 27)


def _good(**over):
    d = {"sport": "nfl", "game_date": "2026-11-29", "stake": 10, "entry_type": "Standard",
         "schedule": "3/3=6, 2/3=1",
         "picks": [{"player": "Ja'Marr Chase", "stat": "rec_yd", "line": 74.5, "side": "more"},
                   {"player": "Josh Allen", "stat": "pass_yd", "line": 244.5, "side": "Less"},
                   {"player": "Saquon Barkley", "stat": "rush_yd", "line": 89.5, "side": "More"}]}
    d.update(over)
    return d


def _refused(L, d, needle):
    try:
        L.validate(d, today=_TODAY)
    except ValueError as e:
        return needle in str(e)
    return False


def _nba_summary(rows):
    """An ESPN NBA summary in the shape nba_matchup_data.parse_box reads."""
    keys = ["minutes", "points", "rebounds", "assists", "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
            "steals", "blocks", "turnovers"]
    teams = {}
    for i, (team, name, dnp, mn, pts, reb, ast, tpm) in enumerate(rows, 1):
        ath = {"athlete": {"id": str(1000 + i), "displayName": name, "position": {"abbreviation": "G"}},
               "starter": not dnp, "didNotPlay": dnp,
               "stats": [] if dnp else [str(mn), str(pts), str(reb), str(ast), f"{tpm}-{tpm + 2}", "1", "0", "2"]}
        if dnp:
            ath["reason"] = "COACH'S DECISION"
        teams.setdefault(team, []).append(ath)
    return {"boxscore": {"players": [{"team": {"id": str(t)}, "statistics": [{"keys": keys, "athletes": a}]}
                                     for t, a in teams.items()]}}


def run(ck):
    import pick6_ledger as L
    import store

    # ---- P1-P2 validation -------------------------------------------------------------------------------
    e = L.validate(_good(), today=_TODAY)
    ck("P1 a good entry validates: the side normalized, each stat's label the registry's, the schedule sorted "
       "as [k, n, x], dated ahead of today so not historical",
       e["picks"][0]["side"] == "More" and e["picks"][0]["stat_label"] == "Receiving yards"
       and e["schedule"] == [[3, 3, 6.0], [2, 3, 1.0]] and e["historical"] == 0 and e["stake"] == 10.0,
       str(e))
    one = _good(picks=_good()["picks"][:1])
    seven = _good(picks=[{"player": f"P{i}", "stat": "rec", "line": 3.5, "side": "More"} for i in range(7)])
    same = _good(picks=[{"player": "Josh Allen", "stat": "pass_yd", "line": 244.5, "side": "More"},
                        {"player": "Josh  Allen", "stat": "rush_yd", "line": 30.5, "side": "More"}], schedule="2/2=3")
    dup = _good(picks=[{"player": "Josh Allen", "stat": "pass_yd", "line": 244.5, "side": "More"},
                       {"player": "josh allen", "stat": "pass_yd", "line": 250.5, "side": "Less"},
                       {"player": "Saquon Barkley", "stat": "rush_yd", "line": 89.5, "side": "More"}])
    refusals = {
        "one pick": _refused(L, one, "2 to 6 picks"),
        "seven picks": _refused(L, seven, "2 to 6 picks"),
        "one player": _refused(L, same, "two different players"),
        "player+stat twice": _refused(L, dup, "repeats a player and stat"),
        "bad sport": _refused(L, _good(sport="cricket"), "sport must be"),
        "bad stat": _refused(L, _good(picks=[dict(_good()["picks"][0], stat="tackles")] + _good()["picks"][1:]),
                             "stat is not one of NFL"),
        "bad side": _refused(L, _good(picks=[dict(_good()["picks"][0], side="Over")] + _good()["picks"][1:]),
                             "More or Less"),
        "three decimals": _refused(L, _good(picks=[dict(_good()["picks"][0], line=74.555)] + _good()["picks"][1:]),
                                   "two decimals"),
        "NaN stake": _refused(L, _good(stake="nan"), "stake must be more than"),
        "zero stake": _refused(L, _good(stake=0), "stake must be more than"),
        "no full-count payout": _refused(L, _good(schedule="2/3=1"), "payout for all 3 correct"),
        "row past the picks": _refused(L, _good(schedule="3/3=6, 4/4=10"), "<= 3"),
        "k over n": _refused(L, _good(schedule="3/3=6, 3/2=1"), "correct <= live"),
        "row twice": _refused(L, _good(schedule="3/3=6, 3=5"), "appears twice"),
        "not k/n=x": _refused(L, _good(schedule="six pays 25"), "is not k/n=x"),
        "far future": _refused(L, _good(game_date="2027-03-01"), "within the last 400 days"),
        "bad date": _refused(L, _good(game_date="11/29/2026"), "YYYY-MM-DD"),
        "control char": _refused(L, _good(picks=[dict(_good()["picks"][0], player="Ja\x07Marr")] + _good()["picks"][1:]),
                                 "control character"),
    }
    ck("P1b validation refuses, naming the field: 1 or 7 picks, one player twice, a player and stat twice, an "
       "unknown sport, stat or side, a line with three decimals, a NaN or zero stake, a schedule without the "
       "full-count payout, a row past the pick count, k over n, a duplicate row, a row that is not k/n=x, a date "
       "out of range, a control character",
       all(refusals.values()), str({k: v for k, v in refusals.items() if not v}))
    lol = L.validate(_good(sport="lol", schedule="2/2=3", picks=[
        {"player": "Faker", "stat": "kills", "line": 3.5, "side": "More"},
        {"player": "Chovy", "stat": "custom", "stat_label": "Fantasy score", "line": 30.5, "side": "Less"}]),
        today=_TODAY)
    golf = L.validate(_good(sport="golf", game_date="2026-11-20", schedule="2/2=3", picks=[
        {"player": "A Golfer", "stat": "Birdies or better", "line": 4.5, "side": "More"},
        {"player": "B Golfer", "stat": "Strokes", "line": 70.5, "side": "Less"}]), today=_TODAY)
    ck("P1c a hand-graded stat keeps the label typed (a sport with no registry takes any stat as custom); an "
       "entry dated before today is historical",
       lol["picks"][1]["stat"] == "custom" and lol["picks"][1]["stat_label"] == "Fantasy score"
       and golf["picks"][0]["stat"] == "custom" and golf["picks"][0]["stat_label"] == "Birdies or better"
       and golf["historical"] == 1, f"{lol['picks']} {golf['historical']}")
    ck("P2 the schedule's shorthand k=x is k of all n picks; a trailing x is allowed; rows sort by live count",
       L.parse_schedule("5=20, 6=25x, 5/5 = 10", 6) == [[6, 6, 25.0], [5, 6, 20.0], [5, 5, 10.0]])

    # ---- P3-P5 grading rules ----------------------------------------------------------------------------
    o = L.outcome
    ck("P3 a stat against a line: over a More hits, under it misses, a Less the other way, and a stat exactly "
       "on the line is a tie",
       (o(75, 74.5, "More"), o(74, 74.5, "More"), o(74, 74.5, "Less"), o(75, 74.5, "Less"), o(3, 3, "More"),
        o(3.0, 3, "Less")) == ("hit", "miss", "hit", "miss", "tie", "tie"))
    sc = L.scores
    ck("P4 the scores written at grade time: a hit at 0.6 is -ln 0.6 and Brier 0.16, a miss -ln 0.4 and 0.36; "
       "a tie is a loss under TIE_RULE 'loss' (the conservative reading); a void and a pending pick count nowhere; "
       "no Vigil figure, no Vigil score",
       L.TIE_RULE == "loss"
       and sc("hit", 0.6) == (1, round(-math.log(0.6), 6), 0.16) and sc("miss", 0.6) == (0, round(-math.log(0.4), 6), 0.36)
       and sc("tie", 0.6)[0] == 0 and sc("void", 0.6) == (None, None, None) and sc("pending", 0.6) == (None, None, None)
       and sc("hit", None) == (1, None, None))
    st = L.settle
    six = [[6, 6, 25], [5, 6, 2]]
    r_all = st(6, ["hit"] * 6, six)
    r_miss = st(6, ["hit"] * 5 + ["miss"], six)
    r_tie = st(6, ["hit"] * 5 + ["tie"], six)
    r_two = st(6, ["hit"] * 4 + ["miss"] * 2, six)
    r_void = st(6, ["hit"] * 5 + ["void"], six)
    r_void_typed = st(6, ["hit"] * 5 + ["void"], six + [[5, 5, 10]])
    r_void_miss_aon = st(6, ["hit"] * 4 + ["miss", "void"], [[6, 6, 25]])
    r_void_desc = st(6, ["hit"] * 4 + ["miss", "void"], six + [[5, 5, 10]])
    r_pend = st(6, ["hit"] * 5 + ["pending"], six)
    r_none = st(2, ["void", "void"], [[2, 2, 3]])
    ck("P5 the entry's paper result off the owner's schedule: 6/6 pays 25x, 5/6 pays 2x (a tie is a live miss), "
       "4/6 pays nothing; a void with the rest correct and no 5/5 row WAITS for DraftKings' figure, pays the typed "
       "5/5 row once given (flagged as credits, DraftKings' void payout); an all-or-nothing schedule with a live "
       "miss pays 0 even reduced; a count the rows describe pays only what they list; a pending pick leaves it "
       "open; every pick void waits",
       r_all[0] == "settled" and r_all[3] == 25 and r_miss[3] == 2 and r_tie[3] == 2 and r_two[3] == 0.0
       and r_void[0] == "awaiting_payout" and "5/5" in r_void[5]
       and r_void_typed[0] == "settled" and r_void_typed[3] == 10 and r_void_typed[4] == 1
       and r_void_miss_aon == ("settled", 5, 4, 0.0, 0, None) and r_void_desc[3] == 0.0
       and r_pend[0] == "open" and r_none[0] == "awaiting_payout" and r_all[4] == 0,
       str([r_all, r_miss, r_void, r_void_typed, r_void_miss_aon, r_void_desc, r_none]))

    # ---- P6-P7 the box parsers and readiness ---------------------------------------------------------------
    sl = L.parse_sleeper([
        {"player": {"first_name": "Ja'Marr", "last_name": "Chase", "position": "WR"},
         "stats": {"gp": 1, "rec": 7, "rec_yd": 74.5 + 0.5, "rush_yd": 3}},
        {"player": {"first_name": "Josh", "last_name": "Allen", "position": "QB"},
         "stats": {"gp": 0}},
        {"player": {"first_name": "Mike", "last_name": "Williams", "position": "WR"}, "stats": {"gp": 1, "rec_yd": 40}},
        {"player": {"first_name": "Mike", "last_name": "Williams", "position": "WR"}, "stats": {"gp": 1, "rec_yd": 12}},
        {"player": {"first_name": "Buffalo", "last_name": "Bills", "position": "DEF"}, "stats": {"gp": 1}}])
    gp = lambda pl, stat, line, side, sport="nfl": L.grade_pick(sl if sport == "nfl" else box,  # noqa: E731
                                                                 {"sport": sport, "player": pl, "stat": stat,
                                                                  "line": line, "side": side}, "the box")
    g_chase = gp("Ja'Marr Chase", "rec_yd", 74.5, "More")
    g_rr = gp("Ja'Marr Chase", "rush_rec_yd", 78, "More")
    g_allen = gp("Josh Allen", "pass_yd", 244.5, "Less")
    g_miss = gp("Saquon Barkley", "rush_yd", 89.5, "More")
    g_two = gp("Mike Williams", "rec_yd", 30.5, "More")
    ck("P6 NFL off Sleeper's weekly rows: a played row grades on its stat (a sum stat sums, a total on the line is a "
       "tie), a row with no game played is VOID (DraftKings' minimum-play rule), a name not in the box WAITS for the "
       "owner, two players sharing a name wait too; team defenses are not players",
       g_chase == ("hit", 75.0, None) and g_rr[0] == "tie" and g_rr[1] == 78.0 and g_allen[0] == "void"
       and g_miss[0] == "pending" and "if he did not play mark it void" in g_miss[2]
       and g_two[0] == "pending" and "2 matches" in g_two[2] and "buffalo bills" not in sl,
       str([g_chase, g_rr, g_allen, g_miss, g_two]))
    box = L.parse_mlb_box({"teams": {
        "home": {"players": {
            "ID1": {"person": {"fullName": "Shohei Ohtani"},
                    "stats": {"batting": {"hits": 2, "runs": 1, "rbi": 2, "totalBases": 5, "homeRuns": 1},
                              "pitching": {}}},
            "ID2": {"person": {"fullName": "Bench Bat"}, "stats": {"batting": {}, "pitching": {}}}}},
        "away": {"players": {
            "ID3": {"person": {"fullName": "Tarik Skubal"},
                    "stats": {"batting": {}, "pitching": {"strikeOuts": 8, "outs": 19, "hits": 4, "earnedRuns": 1}}}}}}})
    b_hrr = gp("Shohei Ohtani", "hrr", 4.5, "More", "mlb")
    b_bench = gp("Bench Bat", "hit", 0.5, "More", "mlb")
    b_k = gp("Tarik Skubal", "p_k", 7.5, "More", "mlb")
    b_kbat = gp("Tarik Skubal", "hit", 0.5, "Less", "mlb")
    ck("P6b MLB off the statsapi box: hits + runs + RBIs sums the batting line, a bench player with no batting line "
       "is VOID for a batter's stat, pitcher strikeouts read the pitching line, and a pitcher who never batted is "
       "void on a batting stat",
       b_hrr == ("hit", 5.0, None) and b_bench[0] == "void" and b_k == ("hit", 8.0, None) and b_kbat[0] == "void",
       str([b_hrr, b_bench, b_k, b_kbat]))
    box = L.parse_nba_summary(_nba_summary([(1, "Nikola Jokic", False, 36, 28, 12, 10, 1),
                                            (1, "Bench Guy", True, 0, 0, 0, 0, 0),
                                            (2, "Luka Doncic", False, 38, 33, 8, 9, 4)]))
    n_pra = gp("Nikola Jokic", "pra", 49.5, "More", "nba")
    n_dnp = gp("Bench Guy", "pts", 4.5, "Less", "nba")
    n_3 = gp("Luka Doncic", "tpm", 3.5, "Less", "nba")
    ck("P6c NBA off ESPN's summary through the Matchups engine's parser (nba_matchup_data.parse_box): points + "
       "rebounds + assists sums, a did-not-play row is VOID, threes read the made half of 3PM-3PA",
       n_pra == ("hit", 50.0, None) and n_dnp[0] == "void" and n_3 == ("miss", 4.0, None),
       str([n_pra, n_dnp, n_3]))
    rd = L.ready
    ck("P7 a box is read only once final: football the Tuesday after the game date (a Thursday game is not ready "
       "on Monday, a Monday game is on Tuesday), baseball and basketball the next day",
       not rd("nfl", "2026-11-26", datetime.date(2026, 11, 30)) and rd("nfl", "2026-11-26", datetime.date(2026, 12, 1))
       and rd("nfl", "2026-11-30", datetime.date(2026, 12, 1)) and not rd("nfl", "2026-12-01", datetime.date(2026, 12, 1))
       and not rd("mlb", "2026-09-01", datetime.date(2026, 9, 1)) and rd("nba", "2026-11-26", _TODAY))

    # ---- P8 Vigil's figure at entry ------------------------------------------------------------------------
    import boardshare
    import nfl_game_sim
    saved = (boardshare.get, nfl_game_sim.current_week)
    board = {"props": [{"player": "Ja'Marr Chase", "stat": "rec yds", "line": 74.5, "side": "More", "prob": 61.0}]}
    boardshare.get = lambda name, max_age: (board, 600) if name.startswith("nfl_sim_board_") else (None, None)
    nfl_game_sim.current_week = lambda preseason=False, day=None: 13
    try:
        pk = lambda **kw: dict({"player": "JaMarr Chase", "stat": "rec_yd", "line": 74.5, "side": "More"}, **kw)  # noqa: E731
        m_same = L.model_prob("nfl", "2026-11-29", pk())
        m_other = L.model_prob("nfl", "2026-11-29", pk(side="Less"))
        m_line = L.model_prob("nfl", "2026-11-29", pk(line=79.5))
        m_hist = L.model_prob("nfl", "2026-11-20", pk(), historical=True)
        m_stat = L.model_prob("nfl", "2026-11-29", pk(stat="pass_td"))
        m_nba = L.model_prob("nba", "2026-11-29", pk(stat="pts"))
    finally:
        boardshare.get, nfl_game_sim.current_week = saved
    ck("P8 Vigil's figure is the NFL Pick 6 board's at EXACTLY the owner's line (the other side its complement, a "
       "half-point line has no tie), never another line (it names the line it does price), never for an entry saved "
       "after its game date, a stat the board does not price, or a sport with no Pick 6 model",
       m_same[0] == 0.61 and "week 13" in m_same[1] and m_other[0] == 0.39 and m_line[0] is None
       and "74.5 only" in m_line[1] and m_hist == (None, "saved after its game date: no pre-game figure")
       and m_stat[0] is None and m_nba[0] is None and "no NBA Pick 6 model" in m_nba[1],
       str([m_same, m_other, m_line, m_stat, m_nba]))
    src = inspect.getsource(L._nfl_prob) + inspect.getsource(L._mlb_prob) + inspect.getsource(L._lol_prob)
    ck("P8b the figure is READ off the shared boards (boardshare.get, the cached slate), never built: no board(), "
       "analyze_slate only cached_only, no _game_sim",
       "boardshare.get(" in src and ".board(" not in src and "cached_only=True" in src and "_game_sim(" not in src)

    # ---- P9-P10 end to end on a scratch ledger -------------------------------------------------------------
    tmp = tempfile.mkdtemp(prefix="p6l-")
    saved_db, saved_ens = store.DB_PATH, L._ensured[0]
    store.DB_PATH, L._ensured[0] = os.path.join(tmp, "ledger.db"), False
    try:
        prices = {"Ja'Marr Chase": 0.6, "Josh Allen": 0.7}
        pricer = lambda sport, gd, p, historical=False, now=None: (  # noqa: E731
            (prices[p["player"]], "stub") if p["player"] in prices else (None, "no model figure"))
        a = L.add_entry(_good(), now=1_800_000_000, today=_TODAY, pricer=pricer)
        b = L.add_entry(_good(stake=5, schedule="3/3=6", picks=[
            {"player": "Ja'Marr Chase", "stat": "rec", "line": 6.5, "side": "More"},
            {"player": "Josh Allen", "stat": "pass_td", "line": 1.5, "side": "More"},
            {"player": "Derrick Henry", "stat": "rush_yd", "line": 90.5, "side": "More"}]),
            now=1_800_000_100, today=_TODAY, pricer=pricer)
        sleeper = L.parse_sleeper([
            {"player": {"first_name": "Ja'Marr", "last_name": "Chase", "position": "WR"},
             "stats": {"gp": 1, "rec": 6, "rec_yd": 80}},
            {"player": {"first_name": "Josh", "last_name": "Allen", "position": "QB"},
             "stats": {"gp": 1, "pass_yd": 230, "pass_td": 2}},
            {"player": {"first_name": "Saquon", "last_name": "Barkley", "position": "RB"},
             "stats": {"gp": 1, "rush_yd": 102}},
            {"player": {"first_name": "Derrick", "last_name": "Henry", "position": "RB"}, "stats": {"gp": 0}}])
        calls = []
        stub = lambda sport, gd, budget: (calls.append((sport, gd)), sleeper)[1]  # noqa: E731
        early = L.grade_due(now=1_800_000_200, today=datetime.date(2026, 11, 30), boxes=stub)
        n = L.grade_due(now=1_800_000_300, today=datetime.date(2026, 12, 1), boxes=stub)
        ea, eb = L.entry(a["id"]), L.entry(b["id"])
        ck("P9 end to end: two entries priced at entry (a pick with no figure says so), nothing graded before the "
           "Tuesday, then every pick graded from one box read for the week; entry A 3/3 settles at 6x ($60 back on "
           "$10), entry B (a miss and a DNP void) at 0 by its all-or-nothing schedule",
           early == 0 and n == 6 and calls == [("nfl", "2026-11-29")]
           and [p["result"] for p in ea["picks"]] == ["hit", "hit", "hit"]
           and ea["picks"][2]["model_src"] == "no model figure" and ea["picks"][2]["p_model"] is None
           and ea["status"] == "settled" and ea["payout"] == 60.0 and ea["pnl"] == 50.0
           and [p["result"] for p in eb["picks"]] == ["miss", "hit", "void"]
           and eb["status"] == "settled" and eb["payout"] == 0.0 and eb["n_live"] == 2,
           f"{early} {n} {calls} {ea['status']} {[p['result'] for p in eb['picks']]} {eb['status']}")
        # the owner's overrides: DraftKings voided Allen's pick in A (a stat correction, say)
        allen_a = ea["picks"][1]["id"]
        ov = L.set_pick(allen_a, void=True, now=1_800_000_400)
        typed = L.add_schedule_rows(a["id"], "2/2=3", now=1_800_000_500)
        back = L.set_pick(allen_a, reset=True, now=1_800_000_600)
        regraded = L.grade_due(now=1_800_000_700, today=datetime.date(2026, 12, 1), boxes=stub)
        by_hand = L.set_pick(eb["picks"][0]["id"], actual=7, now=1_800_000_800)
        bad = None
        try:
            L.set_pick(eb["picks"][0]["id"], actual="lots")
        except ValueError as ex:
            bad = str(ex)
        ck("P9b the owner's hand: a void reduces A to 2 live picks and WAITS for DraftKings' 2/2 figure, the typed "
           "row settles it (credits, 3x), a reset sends the pick back to the auto-grader (which grades it again, "
           "6x), a typed final stat grades a pick by hand (B's miss becomes a hit; B still 2/2 live, no 2/2 row: "
           "waits) and a non-number is refused",
           ov["status"] == "awaiting_payout" and "2/2" in ov["why"] and typed["status"] == "settled"
           and typed["payout_x"] == 3.0 and typed["credits"] == 1 and back["status"] == "open"
           and regraded == 1 and L.entry(a["id"])["payout_x"] == 6.0
           and by_hand["picks"][0]["result"] == "hit" and by_hand["picks"][0]["graded_by"] == "owner"
           and by_hand["status"] == "awaiting_payout" and bad == "the final stat must be a number",
           f"{ov['status']} {typed['status']} {back['status']} {regraded} {by_hand['status']} {bad}")
        rec = L.record()
        # by hand: A's three picks hit (Chase 0.6, Allen 0.7 priced); B: Chase rec now hit by hand (0.6),
        # Allen pass_td hit (0.7), Henry void. Priced and counted: four hits at 0.6, 0.7, 0.6, 0.7.
        want_ll = (-math.log(0.6) * 2 - math.log(0.7) * 2) / 4
        want_br = (0.16 * 2 + 0.09 * 2) / 4
        cal = rec["calibration_all"]
        chase = next(h for h in rec["hit_rate"] if h["stat"] == "Receiving yards")
        henry = next(h for h in rec["hit_rate"] if h["stat"] == "Rushing yards")
        ra = rec["roi_all"]
        ck("P10 the record's arithmetic against a hand computation: hit rate by sport / stat / side (a void counts "
           "nowhere, small samples flagged), Vigil's mean figure, log loss and Brier on the priced picks only, paper "
           "ROI on SETTLED entries only (B waits for its payout row: not in the ROI)",
           cal["n"] == 4 and abs(cal["log_loss"] - round(want_ll, 4)) < 1e-9 and abs(cal["brier"] - round(want_br, 4)) < 1e-9
           and cal["vigil_pct"] == 65.0 and cal["hit_pct"] == 100.0 and cal["flag"] == "small sample"
           and chase["n"] == 1 and chase["hits"] == 1 and henry["n"] == 1 and henry["voids"] == 1 and henry["hits"] == 1
           and ra["n"] == 1 and ra["staked"] == 10.0 and ra["returned"] == 60.0 and ra["pnl"] == 50.0
           and ra["roi_pct"] == 500.0 and rec["status"]["awaiting_payout"]["n"] == 1,
           f"{cal} {chase} {henry} {ra} {rec['status']}")
        rsrc = inspect.getsource(L.record) + inspect.getsource(L._calib_row) + inspect.getsource(L._roi_row)
        lsrc = inspect.getsource(L.listing)
        ck("P10b the record path is SQL sums -- no fetchall anywhere in it, every figure a SUM/COUNT with GROUP BY -- "
           "and the listing is a page (LIMIT, capped at 100), so neither grows with the table",
           "fetchall" not in rsrc and rsrc.count("SUM(") >= 15 and rsrc.count("GROUP BY") >= 4
           and "LIMIT ? OFFSET ?" in lsrc and "min(int(limit), 100)" in lsrc and "fetchall" not in lsrc)
        po = L.add_entry(_good(game_date="2026-11-24", stake=2, schedule="2/2=3", picks=[
            {"player": "Josh Allen", "stat": "pass_yd", "line": 244.5, "side": "More"},
            {"player": "Saquon Barkley", "stat": "rush_yd", "line": 89.5, "side": "More"}]),
            now=1_800_000_900, today=datetime.date(2026, 11, 23), pricer=pricer)
        why = "no regular-season NFL game on 2026-11-24 (test)"
        n_why = L.grade_due(now=1_800_001_000, today=datetime.date(2026, 12, 1),
                            boxes=lambda sport, gd, budget: {"_why": why} if gd == "2026-11-24" else sleeper)
        pw = L.entry(po["id"])
        fsrc = inspect.getsource(L._fetch_box)
        ck("P10a a date the weekly feed cannot grade (no regular-season game that day: current_week answers 18 for "
           "every playoff date, and a playoff pick read off week 18's line would be graded on the wrong game) leaves "
           "its picks pending with the reason, never a grade",
           n_why == 0 and [p["result"] for p in pw["picks"]] == ["pending", "pending"]
           and all(p["note"] == why for p in pw["picks"]) and pw["status"] == "open"
           and "nfl_live.schedule(week, season, seasontype=2)" in fsrc and "_et_day(" in fsrc and '"_why"' in fsrc,
           f"{n_why} {[(p['result'], p['note']) for p in pw['picks']]}")
        L.delete_entry(po["id"])
        ok_del = L.delete_entry(b["id"])
        ck("P10c a deleted entry takes its picks with it", ok_del and L.entry(b["id"]) is None
           and L.record()["calibration_all"]["n"] == 2)

        # ---- P11 the routes ------------------------------------------------------------------------------
        import app as A
        c = A.app.test_client()
        # Switch the login on for this block with a throwaway test value. The module global is named off the
        # names _auth itself reads (the one beside APP_USER), which also pins that _auth is what gates them.
        gate = next(nm for nm in A._auth.__code__.co_names if nm.startswith("APP_") and nm != "APP_USER")
        saved_gate = getattr(A, gate)
        setattr(A, gate, "p6l-test")
        try:
            hdr = {"Authorization": "Basic " + base64.b64encode(f"{A.APP_USER}:p6l-test".encode()).decode()}
            anon = [c.get("/api/pick6/ledger").status_code, c.post("/api/pick6/ledger", json=_good()).status_code,
                    c.post("/api/pick6/ledger/grade").status_code, c.delete(f"/api/pick6/ledger/{a['id']}").status_code,
                    c.post("/api/pick6/ledger/pick/1", json={"void": True}).status_code]
            got = c.get("/api/pick6/ledger", headers=hdr)
            body = got.get_json() or {}
            bad_add = c.post("/api/pick6/ledger", json={"sport": "nfl", "picks": []}, headers=hdr)
            bad_pick = c.post("/api/pick6/ledger/pick/999999", json={"void": True}, headers=hdr)
            bad_rows = c.post(f"/api/pick6/ledger/{a['id']}/schedule", json={"text": "9/9=1"}, headers=hdr)
        finally:
            setattr(A, gate, saved_gate)
        auth_src = inspect.getsource(A._auth)
        ck("P11 every ledger route is behind the app's basic auth (401 without it; only /healthz and /robots.txt are "
           "open, unchanged), the read returns entries, record, sports and rules, and bad input is a 400 naming the "
           "field or a 404",
           anon == [401] * 5 and got.status_code == 200 and {"entries", "record", "sports", "rules"} <= set(body)
           and bad_add.status_code == 400 and "game date" in (bad_add.get_json() or {}).get("error", "")
           and bad_pick.status_code == 404 and bad_rows.status_code == 400
           and 'request.path in ("/healthz", "/robots.txt")' in auth_src and "pick6" not in auth_src,
           f"{anon} {got.status_code} {bad_add.status_code} {bad_pick.status_code} {bad_rows.status_code}")
    finally:
        store.DB_PATH, L._ensured[0] = saved_db, saved_ens

    # ---- P12 the screen -----------------------------------------------------------------------------------
    js = open(os.path.join(_ROOT, "static", "pick6log.js"), encoding="utf-8").read()
    html = open(os.path.join(_ROOT, "templates", "index.html"), encoding="utf-8").read()
    appjs = open(os.path.join(_ROOT, "static", "app.js"), encoding="utf-8").read()
    inline = [h for h in ("onclick=", "onchange=", "oninput=", "onsubmit=") if h in js]
    raw = re.findall(r"\$\{(?!_p6lEsc\()[^}]*\.(?:player|stat_label|note|model_src|entry_type|why|text|source|label|stat|bin)\}", js)
    box = html[html.index('<div id="nflSimBox"'):html.index('<div id="nflWeekBox"')]
    ck("P12 the card has no inline handlers, escapes every name and server string it draws (no raw ${x.player} and "
       "the like), lives inside the NFL sim box under the Pick 6 view (shown only there), loads after the page "
       "script, opens its intro as a p.sub (the readability pass folds it to two lines), and times are Mountain",
       not inline and not raw and js.count("_p6lEsc(") >= 30 and 'id="p6LedgerBox"' in box
       and '<p class="sub">' in box[box.index('id="p6LedgerBox"'):]
       and '<script src="/static/pick6log.js?v={{ v }}"></script>' in html
       and html.index("/static/app.js?v=") < html.index("/static/pick6log.js?v=")
       and '$("p6LedgerBox").classList.toggle("hidden", s !== "pick6");' in appjs
       and 'if (s === "pick6" && typeof p6LedgerShow === "function") p6LedgerShow();' in appjs
       and js.count('"America/Denver"') == 2 and "addEventListener(\"click\", _p6lOnClick)" in js,
       f"inline={inline} raw={raw[:5]}")
    css = open(os.path.join(_ROOT, "static", "style.css"), encoding="utf-8").read()
    ck("P12b at phone width: the form's fields wrap (auto-fit grid, wrapping pick rows), the record's tables scroll "
       "inside their own box, never the page",
       ".p6l-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));" in css
       and ".p6l-pick { display: flex; flex-wrap: wrap;" in css and ".p6l-scroll { overflow-x: auto; max-width: 100%;" in css)

    # ---- P13 the rules and the background owner -----------------------------------------------------------
    rules = {r["id"]: r for r in L.RULES}
    rec_src = open(os.path.join(_ROOT, "mlb_recorder.py"), encoding="utf-8").read()
    ck("P13 the card's rules are the grader's: DraftKings' void-on-minimum-play stated with its source, the tie and "
       "the per-sport play requirement marked UNVERIFIED with the conservative reading named; the background owner "
       "grades an hour at a time, ledgered on failure",
       set(rules) == {"dnp", "played", "tie", "missing", "payout", "picks", "timing", "model"}
       and rules["dnp"]["verified"] and "2024-08-23" in rules["dnp"]["source"]
       and not rules["tie"]["verified"] and "LOSS" in rules["tie"]["text"] and not rules["played"]["verified"]
       and "pick6_ledger.tick()" in rec_src and 'errlog.note("MREC-pick6", _e)' in rec_src
       and L._GRADE_EVERY_S == 3600)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
