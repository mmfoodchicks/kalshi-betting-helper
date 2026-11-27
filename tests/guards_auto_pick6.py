"""Guards for the Pick6 paper ledger (2026-11-27; pick6_ledger.py, static/pick6log.js, the /api/pick6/ledger
routes; the owner's backlog item 7) and the NFL week sim's any-line stat histograms (nfl_dfs_sim.stat_hist).
What each group proves:

  * P1-P2 entry validation: 2-6 picks, two different players, each player and stat once, the stat one the
    sport offers (or a hand-graded one with its market kind), a line with at most two decimals, a positive
    stake, a schedule the entry can reach with DraftKings' payout for all correct -- each refusal names its field;
  * P3-P5 the rules DraftKings states (its pages, read by the owner 2026-11-27): an exact line is VOID, More or
    Less; the entry's paper result off the owner's schedule; an entry with a pick in review stays open;
  * P6 participation per sport from the data each grader reads (NFL snaps or a recorded stat, never gp; NBA
    ESPN's DNP flag, minutes, stats; MLB plate appearances / pitches), the specialized-stat borderline, and
    manual review with the evidence wherever the box cannot establish it; P7 when each box is final;
  * PD the Pardon: a losing More pick on a full-game stat is voided where the box establishes the qualifying
    exit (MLB one plate appearance), a loss where it establishes it does not (NFL second-half yards or an
    excluded position, NBA time after halftime in ESPN's play-by-play, MLB 2+ PA or a pitcher, an event-based
    or part-game market, a sport the policy does not list), and review naming what is missing otherwise;
  * P8 Vigil's figure at entry (any half-point line off the histograms, the board's line without them);
  * P9-P10 end to end on a scratch ledger, the owner's review decisions, the record and the research split
    (outright / non-void accuracy, voids by cause, Wilson intervals) against hand computations; SQL sums only;
  * D1-D5 the histograms: exact against the samples on adversarial floats and on a simulated game (combos
    included), the board unchanged by asking for them, built only where the PC builds, read by the pricer;
  * P11 the routes stay behind the app's basic auth; P12 the screen; P13 the rules and the background owner.

Hermetic: every box is a fixture, every board a stub, the store a scratch file; nothing is fetched.

    python3 tests/guards_auto_pick6.py       # exits 1 on any failed check
"""
import base64
import datetime
import inspect
import math
import os
import random
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


def _sl(first, last, pos, **stats):
    return {"player": {"first_name": first, "last_name": last, "position": pos}, "stats": stats}


def _nba_summary(rows, plays=None):
    """An ESPN NBA summary in the shape nba_matchup_data.parse_box and nba_pbp.parse_summary read. rows:
    (team, id, name, dnp, starter, min, pts, reb, ast, tpm); plays: (period, clock, type, [ids], shooting)."""
    keys = ["minutes", "points", "rebounds", "assists", "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
            "steals", "blocks", "turnovers"]
    teams = {}
    for team, pid, name, dnp, starter, mn, pts, reb, ast, tpm in rows:
        ath = {"athlete": {"id": str(pid), "displayName": name, "position": {"abbreviation": "G"}},
               "starter": starter, "didNotPlay": dnp,
               "stats": [] if dnp else [str(mn), str(pts), str(reb), str(ast), f"{tpm}-{tpm + 2}", "0", "0", "0"]}
        teams.setdefault(team, []).append(ath)
    d = {"boxscore": {"players": [{"team": {"id": str(t)}, "statistics": [{"keys": keys, "athletes": a}]}
                                  for t, a in teams.items()]},
         "header": {"competitions": [{"competitors": [
             {"homeAway": "home", "score": "100", "team": {"id": "1"}},
             {"homeAway": "away", "score": "98", "team": {"id": "2"}}]}]}}
    if plays:
        d["plays"] = [{"period": {"number": per}, "clock": {"displayValue": clk}, "type": {"text": ty},
                       "text": ty, "shootingPlay": shoot, "team": {"id": "1"},
                       "participants": [{"athlete": {"id": str(x)}} for x in ids]}
                      for per, clk, ty, ids, shoot in plays]
    return d


def _game(seed_names=("A", "B")):
    """A synthetic week-sim game: a QB, a back and two receivers a side."""
    pl = []
    for t, o in (seed_names, seed_names[::-1]):
        pl += [{"name": f"{t} QB", "pos": "QB", "team": t, "opp": o, "proj_pts": 18.0,
                "means": {"pass_yd": 245.0, "rush_yd": 14.0, "rec_yd": 0.0, "rec": 0.0, "pass_td": 1.6,
                          "rush_td": 0.1, "rec_td": 0.0, "int": 0.8, "fum": 0.2}},
               {"name": f"{t} RB", "pos": "RB", "team": t, "opp": o, "proj_pts": 13.0,
                "means": {"pass_yd": 0.0, "rush_yd": 62.0, "rec_yd": 21.0, "rec": 2.7, "pass_td": 0.0,
                          "rush_td": 0.45, "rec_td": 0.1, "int": 0.0, "fum": 0.1}},
               {"name": f"{t} WR1", "pos": "WR", "team": t, "opp": o, "proj_pts": 14.0,
                "means": {"pass_yd": 0.0, "rush_yd": 2.0, "rec_yd": 71.0, "rec": 5.4, "pass_td": 0.0,
                          "rush_td": 0.0, "rec_td": 0.45, "int": 0.0, "fum": 0.05}},
               {"name": f"{t} TE", "pos": "TE", "team": t, "opp": o, "proj_pts": 8.0,
                "means": {"pass_yd": 0.0, "rush_yd": 0.0, "rec_yd": 38.0, "rec": 3.6, "pass_td": 0.0,
                          "rush_td": 0.0, "rec_td": 0.3, "int": 0.0, "fum": 0.02}}]
    return {"label": f"{seed_names[1]} @ {seed_names[0]}", "teams": list(seed_names), "players": pl}


def run(ck):
    import pick6_ledger as L
    import store

    # ---- P1-P2 validation -------------------------------------------------------------------------------
    e = L.validate(_good(), today=_TODAY)
    ck("P1 a good entry validates: the side normalized, each stat's label the registry's (a full-game stat), the "
       "schedule sorted as [k, n, x], dated ahead of today so not historical",
       e["picks"][0]["side"] == "More" and e["picks"][0]["stat_label"] == "Receiving yards"
       and all(p["kind"] == "full" for p in e["picks"])
       and e["schedule"] == [[3, 3, 6.0], [2, 3, 1.0]] and e["historical"] == 0 and e["stake"] == 10.0, str(e))
    one = _good(picks=_good()["picks"][:1])
    seven = _good(picks=[{"player": f"P{i}", "stat": "rec", "line": 3.5, "side": "More"} for i in range(7)])
    same = _good(picks=[{"player": "Josh Allen", "stat": "pass_yd", "line": 244.5, "side": "More"},
                        {"player": "Josh  Allen", "stat": "rush_yd", "line": 30.5, "side": "More"}], schedule="2/2=3")
    dup = _good(picks=[{"player": "Josh Allen", "stat": "pass_yd", "line": 244.5, "side": "More"},
                       {"player": "josh allen", "stat": "pass_yd", "line": 250.5, "side": "Less"},
                       {"player": "Saquon Barkley", "stat": "rush_yd", "line": 89.5, "side": "More"}])
    p0 = _good()["picks"]
    refusals = {
        "one pick": _refused(L, one, "2 to 6 picks"),
        "seven picks": _refused(L, seven, "2 to 6 picks"),
        "one player": _refused(L, same, "two different players"),
        "player+stat twice": _refused(L, dup, "repeats a player and stat"),
        "bad sport": _refused(L, _good(sport="cricket"), "sport must be"),
        "bad stat": _refused(L, _good(picks=[dict(p0[0], stat="tackles")] + p0[1:]), "stat is not one of NFL"),
        "bad side": _refused(L, _good(picks=[dict(p0[0], side="Over")] + p0[1:]), "More or Less"),
        "three decimals": _refused(L, _good(picks=[dict(p0[0], line=74.555)] + p0[1:]), "two decimals"),
        "NaN stake": _refused(L, _good(stake="nan"), "stake must be more than"),
        "zero stake": _refused(L, _good(stake=0), "stake must be more than"),
        "no full-count payout": _refused(L, _good(schedule="2/3=1"), "payout for all 3 correct"),
        "row past the picks": _refused(L, _good(schedule="3/3=6, 4/4=10"), "<= 3"),
        "k over n": _refused(L, _good(schedule="3/3=6, 3/2=1"), "correct <= live"),
        "row twice": _refused(L, _good(schedule="3/3=6, 3=5"), "appears twice"),
        "not k/n=x": _refused(L, _good(schedule="six pays 25"), "is not k/n=x"),
        "far future": _refused(L, _good(game_date="2027-03-01"), "within the last 400 days"),
        "bad date": _refused(L, _good(game_date="11/29/2026"), "YYYY-MM-DD"),
        "control char": _refused(L, _good(picks=[dict(p0[0], player="Ja\x07Marr")] + p0[1:]), "control character"),
        "bad kind": _refused(L, _good(sport="golf", schedule="2/2=3", picks=[
            {"player": "A", "stat": "Birdies", "line": 4.5, "side": "More", "kind": "weird"},
            {"player": "B", "stat": "Strokes", "line": 70.5, "side": "Less"}]), "market kind must be"),
    }
    ck("P1b validation refuses, naming the field: 1 or 7 picks, one player twice, a player and stat twice, an "
       "unknown sport, stat or side, a line with three decimals, a NaN or zero stake, a schedule without the "
       "full-count payout, a row past the pick count, k over n, a duplicate row, a row that is not k/n=x, a date "
       "out of range, a control character, an unknown market kind",
       all(refusals.values()), str({k: v for k, v in refusals.items() if not v}))
    lol = L.validate(_good(sport="lol", schedule="2/2=3", picks=[
        {"player": "Faker", "stat": "kills", "line": 3.5, "side": "More"},
        {"player": "Chovy", "stat": "custom", "stat_label": "Longest kill streak", "line": 3.5, "side": "Less",
         "kind": "event"}]), today=_TODAY)
    golf = L.validate(_good(sport="golf", game_date="2026-11-20", schedule="2/2=3", picks=[
        {"player": "A Golfer", "stat": "Birdies or better", "line": 4.5, "side": "More"},
        {"player": "B Golfer", "stat": "Strokes", "line": 70.5, "side": "Less", "kind": "part"}]), today=_TODAY)
    ck("P1c a hand-graded stat keeps the label typed and the market kind named (full by default; a sport with no "
       "registry takes any stat as custom); an entry dated before today is historical",
       lol["picks"][1]["stat"] == "custom" and lol["picks"][1]["stat_label"] == "Longest kill streak"
       and lol["picks"][1]["kind"] == "event" and golf["picks"][0]["kind"] == "full" and golf["picks"][1]["kind"] == "part"
       and golf["picks"][0]["stat_label"] == "Birdies or better" and golf["historical"] == 1,
       f"{lol['picks']} {golf['picks']}")
    ck("P2 the schedule's shorthand k=x is k of all n picks; a trailing x is allowed; rows sort by live count",
       L.parse_schedule("5=20, 6=25x, 5/5 = 10", 6) == [[6, 6, 25.0], [5, 6, 20.0], [5, 5, 10.0]])

    # ---- P3-P5 the stated rules ---------------------------------------------------------------------------
    o = L.outcome
    ck("P3 a stat against a line: over a More hits, under it misses, a Less the other way, and a stat exactly "
       "on the line is a tie",
       (o(75, 74.5, "More"), o(74, 74.5, "More"), o(74, 74.5, "Less"), o(75, 74.5, "Less"), o(3, 3, "More"),
        o(3.0, 3, "Less")) == ("hit", "miss", "hit", "miss", "tie", "tie"))
    sc = L.scores
    ck("P4 a tie is VOID (TIE_RULE 'void': DraftKings' rules pages, read by the owner 2026-11-27, More or Less) and, "
       "like a void, a pending pick or one in review, adds nothing to the record; a hit at 0.6 is -ln 0.6 and Brier "
       "0.16, a miss -ln 0.4 and 0.36; no Vigil figure, no Vigil score",
       L.TIE_RULE == "void" and sc("tie", 0.6) == (None, None, None) and sc("void", 0.6) == (None, None, None)
       and sc("review", 0.6) == (None, None, None) and sc("pending", 0.6) == (None, None, None)
       and sc("hit", 0.6) == (1, round(-math.log(0.6), 6), 0.16) and sc("miss", 0.6) == (0, round(-math.log(0.4), 6), 0.36)
       and sc("hit", None) == (1, None, None))
    st = L.settle
    six = [[6, 6, 25], [5, 6, 2]]
    r_all = st(6, ["hit"] * 6, six)
    r_miss = st(6, ["hit"] * 5 + ["miss"], six)
    r_tie = st(6, ["hit"] * 5 + ["tie"], six)
    r_tie_typed = st(6, ["hit"] * 5 + ["tie"], six + [[5, 5, 10]])
    r_two = st(6, ["hit"] * 4 + ["miss"] * 2, six)
    r_void = st(6, ["hit"] * 5 + ["void"], six)
    r_void_typed = st(6, ["hit"] * 5 + ["void"], six + [[5, 5, 10]])
    r_void_miss_aon = st(6, ["hit"] * 4 + ["miss", "void"], [[6, 6, 25]])
    r_void_desc = st(6, ["hit"] * 4 + ["miss", "void"], six + [[5, 5, 10]])
    r_pend = st(6, ["hit"] * 5 + ["pending"], six)
    r_rev = st(6, ["hit"] * 5 + ["review"], six)
    r_none = st(2, ["void", "void"], [[2, 2, 3]])
    ck("P5 the entry's paper result off the owner's schedule: 6/6 pays 25x, 5/6 2x, 4/6 nothing; a TIE leaves the "
       "entry like a void (5 live, waiting for DraftKings' 5/5 figure, then paying it as credits); a void with no "
       "5/5 row waits, pays the typed row once given; an all-or-nothing schedule with a live miss pays 0 even "
       "reduced; a count the rows describe pays only what they list; a pick pending or IN REVIEW leaves it open "
       "(saying how many wait for the owner); every pick void waits",
       r_all[0] == "settled" and r_all[3] == 25 and r_miss[3] == 2 and r_two[3] == 0.0
       and r_tie[0] == "awaiting_payout" and "5/5" in r_tie[5] and r_tie_typed[3] == 10 and r_tie_typed[4] == 1
       and r_void[0] == "awaiting_payout" and r_void_typed == ("settled", 5, 5, 10.0, 1, None)
       and r_void_miss_aon == ("settled", 5, 4, 0.0, 0, None) and r_void_desc[3] == 0.0
       and r_pend[0] == "open" and r_rev[0] == "open" and "1 pick waiting for your review" in r_rev[5]
       and r_none[0] == "awaiting_payout" and r_all[4] == 0,
       str([r_tie, r_void_typed, r_void_miss_aon, r_rev, r_none]))

    # ---- P6 participation, P7 readiness -------------------------------------------------------------------
    def gp(box, sport, pl, stat, line, side, kind="full"):
        return L.grade_pick(box, {"sport": sport, "player": pl, "stat": stat, "line": line, "side": side,
                                  "kind": kind}, "the box")
    sl = L.parse_sleeper([
        _sl("Ja'Marr", "Chase", "WR", gp=1, rec=7, rec_yd=75, rush_yd=3, h1_rec_yd=40),
        _sl("Josh", "Allen", "QB", gp=1, off_snp=0, st_snp=0, pass_yd=0),
        _sl("Taysom", "Hill", "TE", gp=1, off_snp=0, st_snp=4),
        _sl("Bench", "Back", "RB", gms_active=1, pos_rank_ppr=999),
        _sl("Mike", "Williams", "WR", gp=1, rec_yd=40),
        _sl("Mike", "Williams", "WR", gp=1, rec_yd=12),
        _sl("Buffalo", "Bills", "DEF", gp=1)])
    n_chase = gp(sl, "nfl", "Ja'Marr Chase", "rec_yd", 74.5, "More")
    n_rr = gp(sl, "nfl", "Ja'Marr Chase", "rush_rec_yd", 78, "More")
    n_zero = gp(sl, "nfl", "Josh Allen", "pass_yd", 244.5, "Less")
    n_st = gp(sl, "nfl", "Taysom Hill", "rush_yd", 9.5, "Less")
    n_act = gp(sl, "nfl", "Bench Back", "rush_yd", 9.5, "Less")
    n_miss = gp(sl, "nfl", "Saquon Barkley", "rush_yd", 89.5, "More")
    n_two = gp(sl, "nfl", "Mike Williams", "rec_yd", 30.5, "More")
    ck("P6 NFL participation (one snap): Sleeper's snap counts decide where present (0 + 0 snaps is VOID, cause "
       "dnp; a special-teams snap is a snap), else a recorded stat is one; an active row with gp 0 and no stat goes "
       "to REVIEW with its evidence (gms_active shown, gp never read as a snap); a name not in the box or two "
       "matches go to review; an exact line is a tie; team defenses are not players",
       n_chase[:2] == ("hit", 75.0) and n_rr[0] == "tie" and n_rr[1] == 78.0
       and n_zero[0] == "void" and n_zero[5] == "dnp" and "snaps 0" in n_zero[2]
       and n_st[0] == "hit" and n_st[4]["snaps"] == 4
       and n_act[0] == "review" and n_act[3] == "participation" and "gms_active 1" in n_act[2]
       and n_act[4]["recorded"] == [] and n_miss[0] == "review" and n_miss[3] == "participation"
       and n_two[0] == "review" and "2 matches" in n_two[2] and "buffalo bills" not in sl,
       str([n_chase[:4], n_zero[:4], n_act[:4], n_miss[:4], n_two[:4]]))
    box = L.parse_mlb_box({"teams": {
        "home": {"players": {
            "a": {"person": {"fullName": "Shohei Ohtani"}, "stats": {"batting": {
                "plateAppearances": 4, "atBats": 3, "hits": 2, "runs": 1, "rbi": 2, "totalBases": 5, "homeRuns": 1},
                "pitching": {}}},
            "b": {"person": {"fullName": "Bench Bat"}, "stats": {"batting": {}, "pitching": {}}},
            "c": {"person": {"fullName": "Pinch Runner"}, "stats": {"batting": {
                "plateAppearances": 0, "atBats": 0, "runs": 1, "stolenBases": 1}, "pitching": {}}},
            "d": {"person": {"fullName": "Old Feed"}, "stats": {"batting": {"atBats": 2, "hits": 1}}},
            "e": {"person": {"fullName": "Old Feed Two"}, "stats": {"batting": {"runs": 0}}}}},
        "away": {"players": {
            "f": {"person": {"fullName": "Tarik Skubal"}, "stats": {"batting": {}, "pitching": {
                "numberOfPitches": 98, "strikeOuts": 8, "outs": 19, "hits": 4, "earnedRuns": 1}}},
            "g": {"person": {"fullName": "Pickoff Arm"}, "stats": {"pitching": {"numberOfPitches": 0, "outs": 1}}},
            "h": {"person": {"fullName": "Old Arm"}, "stats": {"pitching": {"outs": 3, "strikeOuts": 1}}}}}}})
    m = {k: gp(box, "mlb", *a) for k, a in {
        "hrr": ("Shohei Ohtani", "hrr", 4.5, "More"), "bench": ("Bench Bat", "hit", 0.5, "More"),
        "pr_hit": ("Pinch Runner", "hit", 0.5, "Less"), "pr_run": ("Pinch Runner", "run", 0.5, "More"),
        "pr_sb": ("Pinch Runner", "sb", 0.5, "More"), "old": ("Old Feed", "hit", 0.5, "More"),
        "old2": ("Old Feed Two", "run", 0.5, "Less"), "k": ("Tarik Skubal", "p_k", 7.5, "More"),
        "kbat": ("Tarik Skubal", "hit", 0.5, "Less"), "pick0": ("Pickoff Arm", "p_outs", 0.5, "More"),
        "oldarm": ("Old Arm", "p_k", 0.5, "More")}.items()}
    ck("P6b MLB participation: batting needs a plate appearance, pitching a pitch. An empty block is VOID (bench; a "
       "pitcher on a batting stat); a pinch runner with 0 PA is VOID on hits (a standard stat) but goes to REVIEW on "
       "runs and stolen bases (special categories may have unlisted exceptions); an old feed without the count is "
       "met on an at-bat, else review; a pitching line with 0 pitches is void; without the count, an out is met",
       m["hrr"][:2] == ("hit", 5.0) and m["bench"][0] == "void" and m["bench"][5] == "dnp"
       and m["pr_hit"][0] == "void" and m["pr_run"][0] == "review" and m["pr_sb"][0] == "review"
       and m["pr_run"][3] == "participation" and m["old"][0] == "hit" and m["old2"][0] == "review"
       and m["k"][:2] == ("hit", 8.0) and m["kbat"][0] == "void" and m["pick0"][0] == "void"
       and m["oldarm"][0] == "hit", str({k: v[:4] for k, v in m.items()}))
    nb = L.parse_nba_summary(_nba_summary([(1, 101, "Nikola Jokic", False, True, 36, 28, 12, 10, 1),
                                           (1, 102, "Bench Guy", True, False, 0, 0, 0, 0, 0),
                                           (1, 103, "Garbage Time", False, False, 0, 0, 0, 0, 0),
                                           (2, 104, "One Rebound", False, False, 0, 0, 1, 0, 0),
                                           (2, 105, "Luka Doncic", False, True, 38, 33, 8, 9, 4)]))
    b = {k: gp(nb, "nba", *a) for k, a in {
        "pra": ("Nikola Jokic", "pra", 49.5, "More"), "dnp": ("Bench Guy", "pts", 4.5, "Less"),
        "secs": ("Garbage Time", "pts", 0.5, "Less"), "reb": ("One Rebound", "reb", 0.5, "More"),
        "tpm": ("Luka Doncic", "tpm", 3.5, "Less")}.items()}
    ck("P6c NBA participation (one second): ESPN's did-not-play flag is VOID; minutes or any stat is met (one "
       "rebound at 0 MIN played); an ACTIVE line at 0 MIN with no stats could be seconds on the floor and goes to "
       "REVIEW; points + rebounds + assists sums, threes read the made half of 3PM-3PA",
       b["pra"][:2] == ("hit", 50.0) and b["dnp"][0] == "void" and b["dnp"][5] == "dnp"
       and b["secs"][0] == "review" and b["secs"][3] == "participation" and b["reb"][:2] == ("hit", 1.0)
       and b["tpm"][:2] == ("miss", 4.0), str({k: v[:4] for k, v in b.items()}))
    rd = L.ready
    ck("P7 a box is read only once final: football the Tuesday after the game date (a Thursday game is not ready "
       "on Monday, a Monday game is on Tuesday), baseball and basketball the next day",
       not rd("nfl", "2026-11-26", datetime.date(2026, 11, 30)) and rd("nfl", "2026-11-26", datetime.date(2026, 12, 1))
       and rd("nfl", "2026-11-30", datetime.date(2026, 12, 1)) and not rd("nfl", "2026-12-01", datetime.date(2026, 12, 1))
       and not rd("mlb", "2026-09-01", datetime.date(2026, 9, 1)) and rd("nba", "2026-11-26", _TODAY))

    # ---- PD the Pardon --------------------------------------------------------------------------------
    hand = {k: L.decide(*a) for k, a in {
        "less": ("nfl", "", 74.5, "Less", 80, "met", {"pos": "WR", "half_split": False}),
        "more_hit": ("nfl", "", 74.5, "More", 80, "met", {"pos": "WR", "half_split": False}),
        "mlb_1pa": ("mlb", "bat", 0.5, "More", 0, "met", {"pa": 1, "ab": 1}),
        "mlb_2pa": ("mlb", "bat", 0.5, "More", 0, "met", {"pa": 2, "ab": 2}),
        "mlb_nopa": ("mlb", "bat", 0.5, "More", 0, "met", {"pa": None, "ab": 1}),
        "mlb_pit": ("mlb", "pit", 5.5, "More", 3, "met", {"pitches": 40}),
        "nfl_k": ("nfl", "", 0.5, "More", 0, "met", {"pos": "K", "half_split": True}),
        "nfl_2h": ("nfl", "", 74.5, "More", 60, "met", {"pos": "WR", "half_split": True, "second_half": "rec +12"}),
        "nfl_nosplit": ("nfl", "", 74.5, "More", 60, "met", {"pos": "WR", "half_split": False}),
        "nfl_same": ("nfl", "", 74.5, "More", 60, "met", {"pos": "WR", "half_split": True, "second_half": None}),
        "nba_2h": ("nba", "", 20.5, "More", 12, "met", {"first_half": True, "second_half": "Q3 7:41"}),
        "nba_1h": ("nba", "", 20.5, "More", 12, "met", {"first_half": True, "second_half": None}),
        "nba_nopbp": ("nba", "", 20.5, "More", 12, "met", {"first_half": None}),
        "nhl": ("nhl", "", 2.5, "More", 1, "met", None),
        "wnba": ("wnba", "", 12.5, "More", 8, "met", None),
        "golf": ("golf", "", 4.5, "More", 3, "met", None)}.items()}
    ev_ = {k: L.decide(*a, kind=kd) for k, a, kd in (
        ("event", ("mlb", "bat", 30.5, "More", 12, "met", {"pa": 1}), "event"),
        ("part", ("mlb", "bat", 0.5, "More", 0, "met", {"pa": 1}), "part"))}
    ck("PD1 the Pardon voids only an UNSUCCESSFUL MORE pick on a FULL-GAME stat where the box establishes the exit: "
       "a Less miss and a More hit grade normally; MLB, a hitter with exactly one plate appearance is VOID (cause "
       "pardon), two are a loss, no count is review, a pitcher is excluded (loss); an event-based or part-game "
       "market is never pardoned; a sport the policy does not list is not pardoned",
       hand["less"][0] == "miss" and hand["more_hit"][0] == "hit"
       and hand["mlb_1pa"][0] == "void" and hand["mlb_1pa"][4] == "pardon" and "exactly one plate" in hand["mlb_1pa"][2]
       and hand["mlb_2pa"][0] == "miss" and "2 plate appearances" in hand["mlb_2pa"][2]
       and hand["mlb_nopa"][0] == "review" and hand["mlb_nopa"][3] == "pardon"
       and hand["mlb_pit"][0] == "miss" and "pitchers are excluded" in hand["mlb_pit"][2]
       and ev_["event"][0] == "miss" and "not a full-game stat" in ev_["event"][2] and ev_["part"][0] == "miss"
       and hand["golf"][0] == "miss" and "lists no Golf rule" in hand["golf"][2],
       str({k: v[:3] for k, v in {**hand, **ev_}.items()}))
    ck("PD2 NFL and NBA: a loss only where the data establishes the pick does not qualify -- a kicker (excluded), "
       "second-half yards in Sleeper's half split, ESPN's play-by-play showing time after halftime -- and REVIEW "
       "naming what is missing everywhere else: Sleeper's weekly stats carry no snaps by half, a half split with no "
       "second-half yards proves nothing, ESPN does not log who starts a period, no play-by-play; NHL and WNBA have "
       "no grader that reads ice time by period or playing time by half",
       hand["nfl_k"][0] == "miss" and "kickers are excluded" in hand["nfl_k"][2]
       and hand["nfl_2h"][0] == "miss" and "rec +12" in hand["nfl_2h"][2]
       and hand["nfl_nosplit"][0] == "review" and "no half split" in hand["nfl_nosplit"][2]
       and hand["nfl_same"][0] == "review" and "not established" in hand["nfl_same"][2]
       and hand["nba_2h"][0] == "miss" and "Q3 7:41" in hand["nba_2h"][2]
       and hand["nba_1h"][0] == "review" and "who starts a period" in hand["nba_1h"][2]
       and hand["nba_nopbp"][0] == "review" and "no ESPN play-by-play" in hand["nba_nopbp"][2]
       and hand["nhl"][0] == "review" and "ice time by period" in hand["nhl"][2]
       and hand["wnba"][0] == "review" and "playing time by half" in hand["wnba"][2]
       and all(hand[k][3] == "pardon" for k in ("nfl_nosplit", "nfl_same", "nba_1h", "nhl", "wnba")),
       str({k: v[:3] for k, v in hand.items() if k.startswith(("nfl", "nba", "nhl", "wnba"))}))
    # ESPN's play-by-play: a shot in Q3 shows Jokic after halftime; Bench In enters and leaves in Q2 (first half
    # only); Luka's Q3 in-and-out at one dead ball plays no time and his Q4 technical names the bench
    pbp = _nba_summary([(1, 201, "Nikola Jokic", False, True, 30, 18, 9, 7, 0),
                        (1, 202, "Bench In", False, False, 6, 4, 1, 0, 0),
                        (2, 203, "Luka Doncic", False, True, 20, 15, 3, 4, 2)],
                       plays=[(1, "11:40", "Jump Shot", [201], True), (2, "5:00", "Substitution", [202, 203], False),
                              (2, "2:00", "Substitution", [203, 202], False), (3, "7:41", "Jump Shot", [201], True),
                              (3, "4:00", "Substitution", [203, 999], False), (3, "4:00", "Substitution", [999, 203], False),
                              (4, "1:00", "Technical Foul", [203], False)])
    halves = L.nba_halves(pbp)
    ck("PD3 nba_halves reads ESPN's play-by-play through nba_pbp's own parse: a play that shows him on the floor or a "
       "substitution with time on both sides proves time after halftime (Jokic's Q3 shot); an in-and-out at one "
       "dead ball and a technical foul prove nothing (Luka); a bench player's Q2 stint is first-half only",
       halves is not None and halves[1] == {201: "Q3 7:41"} and {201, 202, 203} <= halves[0]
       and L.nba_halves({"boxscore": {}}) is None, str(halves))
    nbp = L.parse_nba_summary(pbp)
    pj = gp(nbp, "nba", "Nikola Jokic", "pts", 20.5, "More")
    pb = gp(nbp, "nba", "Bench In", "pts", 6.5, "More")
    ck("PD4 ...and the grader reads it: Jokic's losing More is a loss (on the floor after halftime, Q3 7:41), the "
       "bench player's waits for review with the reason (ESPN does not log who starts a period)",
       pj[0] == "miss" and "Q3 7:41" in pj[2] and pb[0] == "review" and pb[3] == "pardon"
       and "who starts a period" in pb[2], str([pj[:4], pb[:4]]))

    # ---- P8 Vigil's figure at entry -------------------------------------------------------------------
    import boardshare
    import nfl_dfs_sim
    import nfl_game_sim
    saved = (boardshare.get, nfl_game_sim.current_week)
    board = {"props": [{"player": "Ja'Marr Chase", "stat": "rec yds", "line": 74.5, "side": "More", "prob": 61.0}]}
    pieces = {"board": (board, 600), "dists": (None, None)}
    boardshare.get = lambda name, max_age: (pieces["board"] if name.startswith("nfl_sim_board_")
                                            else pieces["dists"] if name.startswith("nfl_sim_dists_") else (None, None))
    nfl_game_sim.current_week = lambda preseason=False, day=None: 13
    try:
        pk = lambda **kw: dict({"player": "JaMarr Chase", "stat": "rec_yd", "stat_label": "Receiving yards",  # noqa: E731
                                "line": 74.5, "side": "More"}, **kw)
        m_same = L.model_prob("nfl", "2026-11-29", pk())
        m_other = L.model_prob("nfl", "2026-11-29", pk(side="Less"))
        m_line = L.model_prob("nfl", "2026-11-29", pk(line=79.5))
        m_hist = L.model_prob("nfl", "2026-11-20", pk(), historical=True)
        m_stat = L.model_prob("nfl", "2026-11-29", pk(stat="pass_td"))
        m_nba = L.model_prob("nba", "2026-11-29", pk(stat="pts"))
        xs = [60.2, 74.5, 74.6, 80.0, 91.3, 70.0, 75.1, 74.4]
        pieces["dists"] = ({"n": len(xs), "dists": {"Ja'Marr Chase": {
            "rec_yd": nfl_dfs_sim.stat_hist(xs), "rush_rec_yd": nfl_dfs_sim.stat_hist([x + 3 for x in xs])}}}, 300)
        d_any = L.model_prob("nfl", "2026-11-29", pk(line=79.5))
        d_less = L.model_prob("nfl", "2026-11-29", pk(line=74.5, side="Less"))
        d_combo = L.model_prob("nfl", "2026-11-29", pk(stat="rush_rec_yd", line=77.5))
        d_whole = L.model_prob("nfl", "2026-11-29", pk(line=75))
        d_none = L.model_prob("nfl", "2026-11-29", pk(stat="rec", stat_label="Receptions", line=4.5))
    finally:
        boardshare.get, nfl_game_sim.current_week = saved
    ck("P8 without the histograms Vigil's figure is the NFL Pick 6 board's at EXACTLY the owner's line (the other "
       "side its complement), never another line (it names the line it does price), never for an entry saved after "
       "its game date, a stat it does not price, or a sport with no Pick 6 model",
       m_same[0] == 0.61 and "week 13" in m_same[1] and m_other[0] == 0.39 and m_line[0] is None
       and "74.5 only" in m_line[1] and m_hist == (None, "saved after its game date: no pre-game figure")
       and m_stat[0] is None and m_nba[0] is None and "no NBA Pick 6 model" in m_nba[1],
       str([m_same, m_other, m_line, m_stat, m_nba]))
    ck("P8a with the week sim's histograms ANY half-point line is priced exactly (79.5: 2 of 8 worlds over; the Less "
       "side at 74.5: 4 of 8 at or under), the yardage sums too (rush + rec at 77.5), never a whole-number line (the "
       "sim's yards are continuous and a real tie is void), and a stat the sim gives him none of says so",
       d_any[0] == 0.25 and "any line" in d_any[1] and d_less[0] == 0.5 and d_combo[0] == 0.5
       and d_whole[0] is None and "half-point lines only" in d_whole[1] and d_none[0] is None
       and "gives him no receptions" in d_none[1], str([d_any, d_less, d_combo, d_whole, d_none]))
    src = inspect.getsource(L._nfl_prob) + inspect.getsource(L._mlb_prob) + inspect.getsource(L._lol_prob)
    ck("P8b the figure is READ off the shared pieces (boardshare.get, the cached slate), never built: no board(), "
       "analyze_slate only cached_only, no _game_sim",
       "boardshare.get(" in src and ".board(" not in src and "cached_only=True" in src and "_game_sim(" not in src)

    # ---- D the any-line histograms (nfl_dfs_sim.stat_hist) ------------------------------------------------
    up = lambda x: math.nextafter(x, math.inf)     # noqa: E731
    dn = lambda x: math.nextafter(x, -math.inf)    # noqa: E731
    adv = [74.5, up(74.5), dn(74.5), -0.5, up(-0.5), dn(-0.5), 0.0, 1e-300, -1e-300, 3.0, 2.5, up(2.5), 120.25,
           -7.75, 0.4999999999999999, 0.5000000000000001, 250.5]
    h = nfl_dfs_sim.stat_hist(adv)
    lines = [m_ + 0.5 for m_ in range(-10, 260)]
    bad = [ln for ln in lines if nfl_dfs_sim.hist_over(h, len(adv), ln) != sum(1 for x in adv if x > ln) / len(adv)]
    # the lattice map itself far out, where a subtraction would round: x > m + 0.5 exactly when j(x) >= m + 1
    big = [1e15 + 0.5, up(1e15 + 0.5), dn(1e15 + 0.5), 2.0 ** 51 + 0.5]
    lat_bad = [(x, m_) for x in big for m_ in (int(x) - 2, int(x) - 1, int(x), int(x) + 1)
               if (nfl_dfs_sim._lattice(x) >= m_ + 1) != (x > m_ + 0.5)]
    ck("D1 a stat histogram gives P(stat > L) EXACTLY at every half-point line, on adversarial floats (values on a "
       "line, one ulp either side of it, signed zeros' neighbours, negatives), and the lattice map holds at 1e15 + 0.5 "
       "and 2^51 + 0.5 and their neighbours: comparisons only, no subtraction to round; a whole-number line is "
       "refused, an all-zero stat is no histogram",
       not bad and not lat_bad and nfl_dfs_sim.hist_over(h, len(adv), 3.0) is None
       and nfl_dfs_sim.stat_hist([0.0, 0.0]) is None and nfl_dfs_sim.hist_over(None, 4, 0.5) == 0.0,
       str(bad[:5]) + str(lat_bad[:3]))
    g = _game()
    random.seed(11)
    plain = nfl_dfs_sim.simulate_game(g, n=600)
    random.seed(11)
    both = nfl_dfs_sim.simulate_game(g, n=600, with_dists=True, with_components=True)
    random.seed(11)
    plain2 = nfl_dfs_sim.simulate_game(g, n=600, with_components=True)
    worst = []
    for row in both["players"]:
        dd = both["dists"].get(row["name"]) or {}
        comps = row["comps"]
        for k in nfl_dfs_sim.DIST_STATS + tuple(nfl_dfs_sim.DIST_COMBOS):
            xs = comps[k] if k in comps else [a + b2 for a, b2 in zip(*(comps[s] for s in nfl_dfs_sim.DIST_COMBOS[k]))]
            if not any(xs):
                if k in dd:
                    worst.append((row["name"], k, "zero stat kept"))
                continue
            lo, hi = math.floor(min(xs)) - 2, math.ceil(max(xs)) + 2
            for m_ in range(lo, hi):
                if nfl_dfs_sim.hist_over(dd.get(k), 600, m_ + 0.5) != sum(1 for x in xs if x > m_ + 0.5) / 600:
                    worst.append((row["name"], k, m_ + 0.5))
                    break
    ck("D2 on a simulated game every player's histogram (passing, rushing, receiving yards, receptions, and the "
       "rush + rec / pass + rush sums drawn world by world) equals the full worlds' count at every half-point line "
       "from below his minimum to above his maximum",
       not worst and len(both["dists"]) == 8 and "rush_rec_yd" in both["dists"]["A RB"], str(worst[:5]))
    props_ok = all(abs(100 * L._side_prob(nfl_dfs_sim.hist_over(
        both["dists"][p["player"]][{"pass yds": "pass_yd", "rush yds": "rush_yd", "rec yds": "rec_yd",
                                    "receptions": "rec"}[p["stat"]]], 600, p["line"]), "More", p["side"]) - p["prob"])
                   <= 0.05 + 1e-9 for p in both["props"])
    ck("D3 asking for the histograms changes nothing else: the same seed gives the same props, players and stacks "
       "with and without them (no draw consumed), a call without them carries no `dists` key, and every board prop's "
       "figure is the histogram's at that prop's line",
       plain["props"] == both["props"] and plain2["players"] == both["players"] and plain["stacks"] == both["stacks"]
       and "dists" not in plain and props_ok and both["props"])
    bsrc = inspect.getsource(nfl_dfs_sim.board)
    bbsrc = inspect.getsource(nfl_dfs_sim._build_board)
    ck("D4 only the PC builds them (VIGIL_PC_WORKER, set where pc_worker runs: never an extra pass in a web worker), "
       "into a piece of their own -- popped off the board before it is cached or shared, so /api/nfl/sim's JSON "
       "never carries bytes and the board is the board it was",
       'pc = _os.environ.get("VIGIL_PC_WORKER") == "1"' in bsrc
       and "val = _build_board(season, week, preseason=preseason, with_dists=pc)" in bsrc
       and 'dists = val.pop("_dists", None) if val else None' in bsrc
       and "boardshare.put(dists_name(season, week, preseason), dists)" in bsrc
       and bsrc.index('val.pop("_dists"') < bsrc.index("_cache[key] = (_time.time(), val)")
       and 'if with_dists:          # popped by board()' in bbsrc
       and nfl_dfs_sim.dists_name("2026", 13) == "nfl_sim_dists_2026_w13_0")
    import pickle
    size = len(pickle.dumps(both["dists"], protocol=4))
    ck("D5 the pieces are small: one game of eight players at 600 worlds pickles under 20 KB (the measured week: "
       "387 players, 1,609 player-stats, 92,008 uint16 bins, 210 KB, +1.8 s on a 6.7 s week, on the PC)", size < 20000, str(size))

    # ---- P9-P10 end to end on a scratch ledger -----------------------------------------------------------
    tmp = tempfile.mkdtemp(prefix="p6l-")
    saved_db, saved_ens = store.DB_PATH, L._ensured[0]
    store.DB_PATH, L._ensured[0] = os.path.join(tmp, "ledger.db"), False
    try:
        prices = {"Ja'Marr Chase": 0.6, "Josh Allen": 0.7}
        pricer = lambda sport, gd, p, historical=False, now=None: (  # noqa: E731
            (prices[p["player"]], "stub") if p["player"] in prices else (None, "no model figure"))
        a = L.add_entry(_good(), now=1_800_000_000, today=_TODAY, pricer=pricer)
        bb = L.add_entry(_good(stake=5, schedule="3/3=6", picks=[
            {"player": "Ja'Marr Chase", "stat": "rec", "line": 6.5, "side": "More"},
            {"player": "Josh Allen", "stat": "pass_td", "line": 1.5, "side": "More"},
            {"player": "Derrick Henry", "stat": "rush_yd", "line": 90.5, "side": "More"}]),
            now=1_800_000_100, today=_TODAY, pricer=pricer)
        cc = L.add_entry(_good(stake=2, schedule="2/2=3", picks=[
            {"player": "Josh Allen", "stat": "rush_yd", "line": 20.5, "side": "More"},
            {"player": "Saquon Barkley", "stat": "rush_yd", "line": 99.5, "side": "More"}]),
            now=1_800_000_150, today=_TODAY, pricer=pricer)
        sleeper = L.parse_sleeper([
            _sl("Ja'Marr", "Chase", "WR", gp=1, rec=6, rec_yd=80, h1_rec_yd=50),
            _sl("Josh", "Allen", "QB", gp=1, pass_yd=230, pass_td=2, pass_att=31),
            _sl("Saquon", "Barkley", "RB", gp=1, rush_yd=102, rush_att=20),
            _sl("Derrick", "Henry", "RB", gms_active=1, off_snp=0, st_snp=0)])
        calls = []
        stub = lambda sport, gd, budget: (calls.append((sport, gd)), sleeper)[1]  # noqa: E731
        early = L.grade_due(now=1_800_000_200, today=datetime.date(2026, 11, 30), boxes=stub)
        n = L.grade_due(now=1_800_000_300, today=datetime.date(2026, 12, 1), boxes=stub)
        ea, eb, ec = L.entry(a["id"]), L.entry(bb["id"]), L.entry(cc["id"])
        ck("P9 end to end: three entries priced at entry, nothing graded before the Tuesday, then one box read for "
           "the week: A 3/3 settles at 6x ($60 on $10); B's losing More on Chase is a loss (Sleeper's half split shows "
           "second-half yards, so no pardon), Henry's 0 snaps a void (dnp), and B settles at 0; C's losing More on "
           "Allen (no half split for him) waits in review, so C stays open and its pick counts nowhere yet",
           early == 0 and n == 7 and calls == [("nfl", "2026-11-29")]
           and [p["result"] for p in ea["picks"]] == ["hit", "hit", "hit"] and ea["payout"] == 60.0
           and [p["result"] for p in eb["picks"]] == ["miss", "hit", "void"] and eb["picks"][2]["void_cause"] == "dnp"
           and "rec +30" in eb["picks"][0]["note"] and eb["status"] == "settled" and eb["payout"] == 0.0
           and ec["picks"][0]["result"] == "review" and ec["picks"][0]["review"] == "pardon"
           and ec["picks"][0]["decisions"] == ["loss", "pardoned"] and "evidence" in ec["picks"][0]
           and ec["status"] == "open" and "waiting for your review" in (ec["why"] or ""),
           f"{early} {n} {[p['result'] for p in eb['picks']]} {[(p['result'], p['note']) for p in ec['picks']]}")
        allen_c = ec["picks"][0]["id"]
        wrong = None
        try:
            L.set_pick(allen_c, decision="met")
        except ValueError as ex:
            wrong = str(ex)
        pard = L.set_pick(allen_c, decision="pardoned", now=1_800_000_350)
        L.set_pick(allen_c, reset=True, now=1_800_000_360)
        again = L.grade_due(now=1_800_000_370, today=datetime.date(2026, 12, 1), boxes=stub)
        lost = L.set_pick(allen_c, decision="loss", now=1_800_000_380)
        ck("PD5 the owner's review: an answer that does not fit the review is refused; 'pardoned' voids the pick "
           "(cause pardon) and C waits for DraftKings' 1/1 figure; re-graded it is back in review; 'loss' settles C at "
           "0 (1 of 2)",
           wrong and "does not answer this pick" in wrong and pard["picks"][0]["result"] == "void"
           and pard["picks"][0]["void_cause"] == "pardon" and pard["status"] == "awaiting_payout" and again == 0
           and lost["picks"][0]["result"] == "miss" and lost["status"] == "settled" and lost["n_hit"] == 1
           and lost["payout"] == 0.0, f"{wrong} {pard['status']} {again} {lost['status']}")
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
        ck("P9b the owner's hand: a void reduces A to 2 live picks and WAITS for DraftKings' 2/2 figure (cause owner), "
           "the typed row settles it (credits, 3x), a reset sends the pick back to the auto-grader (6x again), a "
           "typed final stat grades a pick by hand (B's miss becomes a hit; B 2/2 live, no 2/2 row: waits) and a "
           "non-number is refused",
           ov["status"] == "awaiting_payout" and "2/2" in ov["why"] and ov["picks"][1]["void_cause"] == "owner"
           and typed["status"] == "settled" and typed["payout_x"] == 3.0 and typed["credits"] == 1
           and back["status"] == "open" and regraded == 1 and L.entry(a["id"])["payout_x"] == 6.0
           and by_hand["picks"][0]["result"] == "hit" and by_hand["picks"][0]["graded_by"] == "owner"
           and by_hand["status"] == "awaiting_payout" and bad == "the final stat must be a number",
           f"{ov['status']} {typed['status']} {back['status']} {regraded} {by_hand['status']} {bad}")
        rec = L.record()
        # priced and counted: A Chase .6 hit, A Allen .7 hit, B Chase .6 hit (by hand), B Allen .7 hit, C Allen .7 miss
        want_ll = (-math.log(0.6) * 2 - math.log(0.7) * 2 - math.log(0.3)) / 5
        want_br = (0.16 * 2 + 0.09 * 2 + 0.49) / 5
        cal = rec["calibration_all"]
        chase = next(h_ for h_ in rec["hit_rate"] if h_["stat"] == "Receiving yards")
        rush = next(h_ for h_ in rec["hit_rate"] if h_["stat"] == "Rushing yards")
        ra = rec["roi_all"]
        ck("P10 the record against a hand computation: hit rate by sport / stat / side (a void counts nowhere, small "
           "samples flagged), Vigil's mean figure, log loss and Brier on the priced picks only, paper ROI on SETTLED "
           "entries only (B waits for its payout row)",
           cal["n"] == 5 and abs(cal["log_loss"] - round(want_ll, 4)) < 1e-9 and abs(cal["brier"] - round(want_br, 4)) < 1e-9
           and cal["vigil_pct"] == 66.0 and cal["hit_pct"] == 80.0 and cal["flag"] == "small sample"
           and chase["n"] == 1 and chase["hits"] == 1 and rush["n"] == 3 and rush["voids"] == 1 and rush["hits"] == 2
           and ra["n"] == 2 and ra["staked"] == 12.0 and ra["returned"] == 60.0 and ra["pnl"] == 48.0
           and ra["roi_pct"] == 400.0 and rec["status"]["awaiting_payout"]["n"] == 1 and rec["in_review"] == {},
           f"{cal} {chase} {rush} {ra} {rec['status']}")
        res = {(r_["sport"], r_["side"]): r_ for r_ in rec["research"]}
        mo, le, al = res[("nfl", "More")], res[("nfl", "Less")], res[("all", "More")]
        ck("P10a the research split, by sport and side (and all sports): OUTRIGHT accuracy counts a void as not a hit "
           "(More: 5 of 7), accuracy CONDITIONAL on a non-void outcome leaves it out (5 of 6), and the void rate by "
           "cause (1 did-not-play of 7), each with its n and a 95% Wilson interval",
           mo["outright"]["k"] == 5 and mo["outright"]["n"] == 7 and mo["conditional"]["n"] == 6
           and mo["void"]["k"] == 1 and mo["void_by_cause"]["dnp"]["k"] == 1 and mo["void_by_cause"]["pardon"]["k"] == 0
           and le["outright"]["k"] == 1 and le["outright"]["n"] == 1 and al["outright"] == mo["outright"]
           and (mo["outright"]["pct"], mo["outright"]["lo"], mo["outright"]["hi"]) == L.wilson(5, 7)
           and L.wilson(5, 7) == (71.4, 35.9, 91.8) and L.wilson(0, 5)[1] == 0.0 and L.wilson(5, 5)[2] == 100.0
           and L.wilson(5, 5)[1] > 50 and mo["flag"] == "small sample", f"{mo} {le}")
        rsrc = inspect.getsource(L.record) + inspect.getsource(L._calib_row) + inspect.getsource(L._roi_row) \
            + inspect.getsource(L._research_row)
        lsrc = inspect.getsource(L.listing)
        ck("P10b the record path is SQL sums -- no fetchall anywhere in it, every figure a SUM/COUNT with GROUP BY, "
           "nothing in review or pending counted -- and the listing is a page (LIMIT, capped at 100)",
           "fetchall" not in rsrc and rsrc.count("SUM(") >= 20 and rsrc.count("GROUP BY") >= 6
           and rsrc.count("result NOT IN ('pending', 'review')") >= 2
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
        ck("P10c a date the weekly feed cannot grade (no regular-season game that day: current_week answers 18 for "
           "every playoff date) leaves its picks pending with the reason, never a grade",
           n_why == 0 and [p["result"] for p in pw["picks"]] == ["pending", "pending"]
           and all(p["note"] == why for p in pw["picks"]) and pw["status"] == "open"
           and "nfl_live.schedule(week, season, seasontype=2)" in fsrc and "_et_day(" in fsrc and '"_why"' in fsrc,
           f"{n_why} {[(p['result'], p['note']) for p in pw['picks']]}")
        L.delete_entry(po["id"])
        ok_del = L.delete_entry(bb["id"])
        ck("P10d a deleted entry takes its picks with it", ok_del and L.entry(bb["id"]) is None
           and L.record()["calibration_all"]["n"] == 3)

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
            bad_dec = c.post(f"/api/pick6/ledger/pick/{ea['picks'][0]['id']}", json={"decision": "pardoned"}, headers=hdr)
        finally:
            setattr(A, gate, saved_gate)
        auth_src = inspect.getsource(A._auth)
        ck("P11 every ledger route is behind the app's basic auth (401 without it; only /healthz and /robots.txt are "
           "open, unchanged), the read returns entries, record, sports, rules, the Pardon and the market kinds, and bad "
           "input is a 400 naming the field (a review answer to a pick not in review too) or a 404",
           anon == [401] * 5 and got.status_code == 200
           and {"entries", "record", "sports", "rules", "pardon", "kinds"} <= set(body)
           and set(body["pardon"]) == {"nfl", "nba", "wnba", "mlb", "nhl"}
           and bad_add.status_code == 400 and "game date" in (bad_add.get_json() or {}).get("error", "")
           and bad_pick.status_code == 404 and bad_rows.status_code == 400 and bad_dec.status_code == 400
           and 'request.path in ("/healthz", "/robots.txt")' in auth_src and "pick6" not in auth_src,
           f"{anon} {got.status_code} {bad_add.status_code} {bad_pick.status_code} {bad_rows.status_code} {bad_dec.status_code}")
    finally:
        store.DB_PATH, L._ensured[0] = saved_db, saved_ens

    # ---- P12 the screen -----------------------------------------------------------------------------------
    js = open(os.path.join(_ROOT, "static", "pick6log.js"), encoding="utf-8").read()
    html = open(os.path.join(_ROOT, "templates", "index.html"), encoding="utf-8").read()
    appjs = open(os.path.join(_ROOT, "static", "app.js"), encoding="utf-8").read()
    inline = [h_ for h_ in ("onclick=", "onchange=", "oninput=", "onsubmit=") if h_ in js]
    raw = re.findall(r"\$\{(?!_p6lEsc\()[^}]*\.(?:player|stat_label|note|model_src|entry_type|why|text|source|label|"
                     r"stat|bin|evidence|basis|review)\}", js)
    box_ = html[html.index('<div id="nflSimBox"'):html.index('<div id="nflWeekBox"')]
    ck("P12 the card has no inline handlers, escapes every name and server string it draws (no raw ${x.player}, "
       "${p.evidence} and the like), offers the review answers and a market kind for a hand-typed stat, prints a tie "
       "as void, lives inside the NFL sim box under the Pick 6 view, loads after the page script, opens its intro "
       "as a p.sub, and times are Mountain",
       not inline and not raw and js.count("_p6lEsc(") >= 40 and 'id="p6LedgerBox"' in box_
       and 'data-p6l-decide="${p.id}"' in js and '{ decision: ds.p6lD }' in js and 'data-p6l-f="kind"' in js
       and '"tie (void)"' in js and "Outright counts every decided pick and a void as NOT a hit" in js
       and '<p class="sub">' in box_[box_.index('id="p6LedgerBox"'):]
       and '<script src="/static/pick6log.js?v={{ v }}"></script>' in html
       and html.index("/static/app.js?v=") < html.index("/static/pick6log.js?v=")
       and '$("p6LedgerBox").classList.toggle("hidden", s !== "pick6");' in appjs
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
    stated = ("played", "special", "tie")
    ck("P13 the card's rules are the grader's: participation, the specialized-stat exception and the tie are "
       "'stated by DraftKings, confirmed by the owner 2026-11-27' citing the rules pages, the Pardon 'stated by "
       "DraftKings, read by the owner' citing the Pardon Policy, nothing left UNVERIFIED; every rule has a basis; the "
       "background owner grades an hour at a time, ledgered on failure",
       set(rules) == {"played", "special", "tie", "pardon", "missing", "payout", "picks", "timing", "model"}
       and all(rules[k]["basis"].startswith("stated by DraftKings, confirmed by the owner 2026-11-27") for k in stated)
       and all("pick6-rules-and-scoring-nfl" in rules[k]["source"] for k in stated)
       and rules["pardon"]["basis"] == "stated by DraftKings, read by the owner 2026-11-27"
       and "pardon-policy" in rules["pardon"]["source"] and "VOID" in rules["tie"]["text"]
       and all(r.get("basis") and r["verified"] for r in L.RULES) and "UNVERIFIED" not in str(L.RULES)
       and "one snap" in rules["played"]["text"] and "one second" in rules["played"]["text"]
       and "pick6_ledger.tick()" in rec_src and 'errlog.note("MREC-pick6", _e)' in rec_src
       and L._GRADE_EVERY_S == 3600)
    ck("P13b the Pardon's rules are data beside the code that reads them: the five sports the policy names, the "
       "NFL's offensive positions, MLB's batting block only, and the three market kinds (only 'full' pardonable)",
       set(L.PARDON) == {"nfl", "nba", "wnba", "mlb", "nhl"} and L.PARDON["nfl"]["eligible"] == ("QB", "RB", "WR", "TE", "FB")
       and L.PARDON["mlb"]["blocks"] == ("bat",) and set(L.KINDS) == {"full", "event", "part"}
       and "exactly one plate appearance" in L.PARDON["mlb"]["exit"] and "goalies" in L.PARDON["nhl"]["excluded"])


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
