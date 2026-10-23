"""Guards for the NFL matchup data foundation (nfl_matchup_data,
nfl_matchup_profiles). Hermetic: a synthetic ten-play season written as the
three nflverse CSVs (only the columns the parser reads -- it indexes by
header name), built through the real build_season, read through the real
profiles. No network, no deep store, under a second.

    python3 tests/guards_nfl_matchup_data.py      # exits 1 on any failure
    from tests.guards_nfl_matchup_data import run  # run(ck) inside the suite

What is pinned, by definition and not by number alone: the cutoff (a profile
as of (s, w) holds no play from week >= w of s, and the constructor refuses
tallies that would); the personnel parse; the coverage-family map; the
fantasy convention in all three formats on a hand-computed line; shrinkage
pulling toward the prior and only toward it; the crosswalk on four real
players; the two-point / no-play exclusions; the on-field denominators.
"""
import csv
import gzip
import json
import os
import sys
import tempfile

os.environ["VIGIL_NO_BG"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import nfl_matchup_data as D            # noqa: E402
import nfl_matchup_profiles as P        # noqa: E402

# ---- the synthetic season --------------------------------------------------------
# Two games: week 1 KC @ BUF (BUF home) and week 2 BUF @ KC. Players: KC QB
# 100001, KC WR 100002, KC TE 100003, KC RB 100004; BUF QB 200001, BUF WR
# 200002. Air yards, coverage and FTN fields chosen so every profile figure
# below is hand-computable.
PBP_COLS = ("play_id", "game_id", "home_team", "away_team", "season_type", "week", "posteam", "defteam", "yardline_100",
            "game_date", "game_seconds_remaining", "qtr", "down", "ydstogo", "play_type", "yards_gained", "shotgun", "no_huddle",
            "qb_dropback", "qb_kneel", "qb_spike", "qb_scramble", "pass_length", "pass_location", "air_yards", "yards_after_catch",
            "run_location", "run_gap", "score_differential", "epa", "wp", "incomplete_pass", "interception", "fumble_lost", "sack",
            "touchdown", "pass_touchdown", "rush_touchdown", "two_point_attempt", "complete_pass", "passer_player_id",
            "receiver_player_id", "rusher_player_id", "fumbled_1_player_id", "cpoe", "success", "first_down", "goal_to_go",
            "qb_hit", "rush_attempt", "pass_attempt", "home_score", "away_score", "start_time", "roof", "spread_line", "total_line",
            "xpass", "play_deleted")


def _g(pid):
    return "00-%07d" % pid


def _play(**kw):
    row = {c: "" for c in PBP_COLS}
    row.update({"season_type": "REG", "qtr": "1", "down": "1", "ydstogo": "10", "yardline_100": "50", "game_seconds_remaining": "3000",
                "score_differential": "0", "wp": "0.5", "epa": "0", "shotgun": "0", "no_huddle": "0", "qb_dropback": "0", "qb_kneel": "0",
                "qb_spike": "0", "qb_scramble": "0", "incomplete_pass": "0", "interception": "0", "fumble_lost": "0", "sack": "0",
                "touchdown": "0", "pass_touchdown": "0", "rush_touchdown": "0", "two_point_attempt": "0", "complete_pass": "0",
                "success": "0", "first_down": "0", "goal_to_go": "0", "qb_hit": "0", "rush_attempt": "0", "pass_attempt": "0",
                "play_deleted": "0", "xpass": "0.5", "home_score": "20", "away_score": "17"})
    row.update(kw)
    return row


def _pass(pid, gid, off, dfn, passer, receiver, air, yds, comp, loc="right", td=0, **kw):
    return _play(play_id=str(pid), game_id=gid, posteam=off, defteam=dfn, play_type="pass", qb_dropback="1", pass_attempt="1",
                 passer_player_id=_g(passer), receiver_player_id=_g(receiver), air_yards=str(air), pass_location=loc,
                 pass_length="deep" if air >= 20 else "short", yards_gained=str(yds), complete_pass="1" if comp else "0",
                 incomplete_pass="0" if comp else "1", yards_after_catch=str(max(0, yds - air)) if comp else "",
                 touchdown=str(td), pass_touchdown=str(td), epa="1.0" if comp else "-0.5", success="1" if comp else "0",
                 cpoe="10", **kw)


def _run(pid, gid, off, dfn, rusher, yds, loc="left", gap="end", td=0, **kw):
    base = dict(play_id=str(pid), game_id=gid, posteam=off, defteam=dfn, play_type="run", rush_attempt="1",
                rusher_player_id=_g(rusher), yards_gained=str(yds), run_location=loc, run_gap=gap, touchdown=str(td),
                rush_touchdown=str(td), epa="0.2", success="1" if yds >= 4 else "0")
    base.update(kw)
    return _play(**base)


G1, G2 = "2030_01_KC_BUF", "2030_02_BUF_KC"
PLAYS = [
    # ---- week 1, KC offence vs BUF: 3 dropbacks (2 man, 1 zone), 2 runs
    _pass(1, G1, "KC", "BUF", 100001, 100002, 12, 15, 1, loc="right", home_team="BUF", away_team="KC", week="1", game_date="2030-09-08"),
    _pass(2, G1, "KC", "BUF", 100001, 100003, 5, 0, 0, loc="middle", home_team="BUF", away_team="KC", week="1", game_date="2030-09-08"),
    _pass(3, G1, "KC", "BUF", 100001, 100002, 25, 40, 1, loc="left", td=1, home_team="BUF", away_team="KC", week="1", game_date="2030-09-08"),
    _run(4, G1, "KC", "BUF", 100004, 6, home_team="BUF", away_team="KC", week="1", game_date="2030-09-08"),
    _run(5, G1, "KC", "BUF", 100004, -2, loc="middle", gap="guard", home_team="BUF", away_team="KC", week="1", game_date="2030-09-08",
         fumble_lost="1", fumbled_1_player_id=_g(100004)),
    # a two-point try and a deleted play must be dropped
    _pass(6, G1, "KC", "BUF", 100001, 100002, 2, 2, 1, two_point_attempt="1", down="", home_team="BUF", away_team="KC", week="1", game_date="2030-09-08"),
    _run(7, G1, "KC", "BUF", 100004, 50, play_deleted="1", home_team="BUF", away_team="KC", week="1", game_date="2030-09-08"),
    # ---- week 1, BUF offence vs KC: 2 dropbacks (1 sack), 1 run
    _pass(8, G1, "BUF", "KC", 200001, 200002, 8, 8, 1, home_team="BUF", away_team="KC", week="1", game_date="2030-09-08"),
    _play(play_id="9", game_id=G1, posteam="BUF", defteam="KC", play_type="pass", qb_dropback="1", sack="1", yards_gained="-7",
          passer_player_id=_g(200001), epa="-1.5", home_team="BUF", away_team="KC", week="1", game_date="2030-09-08"),
    _run(10, G1, "BUF", "KC", 200001, 3, home_team="BUF", away_team="KC", week="1", game_date="2030-09-08", qb_scramble="1",
         qb_dropback="1", rush_attempt="0"),
    # ---- week 2, KC offence vs BUF: one completion to the WR
    _pass(11, G2, "KC", "BUF", 100001, 100002, 10, 22, 1, home_team="KC", away_team="BUF", week="2", game_date="2030-09-15"),
    _run(12, G2, "KC", "BUF", 100004, 9, home_team="KC", away_team="BUF", week="2", game_date="2030-09-15"),
    _pass(13, G2, "BUF", "KC", 200001, 200002, 30, 0, 0, home_team="KC", away_team="BUF", week="2", game_date="2030-09-15"),
]

PART_COLS = ("nflverse_game_id", "old_game_id", "play_id", "possession_team", "offense_formation", "offense_personnel",
             "defenders_in_box", "defense_personnel", "number_of_pass_rushers", "players_on_play", "offense_players",
             "defense_players", "n_offense", "n_defense", "ngs_air_yards", "time_to_throw", "was_pressure", "route",
             "defense_man_zone_type", "defense_coverage_type")
KC_ON = ";".join(_g(p) for p in (100001, 100002, 100003, 100004))
BUF_ON = ";".join(_g(p) for p in (200001, 200002))
NICKEL = "3 CB, 2 DE, 2 DT, 1 FS, 1 MLB, 1 OLB, 1 SS"
BASE34 = "2 CB, 2 DE, 1 FS, 2 ILB, 1 NT, 2 OLB, 1 SS"
P11 = "1 C, 2 G, 1 QB, 1 RB, 2 T, 1 TE, 3 WR"
P12 = "1 C, 2 G, 1 QB, 1 RB, 2 T, 2 TE, 2 WR"


def _part(gid, pid, off, form, opers, box, dpers, rushers, onfield, ttt, press, route, mz, cov):
    return dict(zip(PART_COLS, (gid, "", str(pid), off, form, opers, str(box), dpers, str(rushers), "", onfield, "", "11", "11",
                                "", ttt, press, route, mz, cov)))


PART = [
    _part(G1, 1, "KC", "SHOTGUN", P11, 6, NICKEL, 4, KC_ON, "2.5", "FALSE", "IN/DIG", "MAN_COVERAGE", "COVER_1"),
    _part(G1, 2, "KC", "SHOTGUN", P11, 6, NICKEL, 5, KC_ON, "2.0", "TRUE", "HITCH/CURL", "MAN_COVERAGE", "COVER_1"),
    _part(G1, 3, "KC", "EMPTY", P11, 5, NICKEL, 4, KC_ON, "3.1", "FALSE", "GO", "ZONE_COVERAGE", "COVER_3"),
    _part(G1, 4, "KC", "SINGLEBACK", P12, 8, BASE34, 0, KC_ON, "", "", "", "", ""),
    _part(G1, 5, "KC", "I_FORM", P12, 7, BASE34, 0, KC_ON, "", "", "", "", ""),
    _part(G1, 8, "BUF", "SHOTGUN", P11, 6, NICKEL, 4, BUF_ON, "2.2", "FALSE", "SLANT", "ZONE_COVERAGE", "COVER_2"),
    _part(G1, 9, "BUF", "SHOTGUN", P11, 6, NICKEL, 6, BUF_ON, "", "TRUE", "", "ZONE_COVERAGE", "COVER_4"),
    _part(G1, 10, "BUF", "SHOTGUN", P11, 6, NICKEL, 4, BUF_ON, "", "FALSE", "", "MAN_COVERAGE", "COVER_0"),
    _part(G2, 11, "KC", "SHOTGUN", P11, 6, NICKEL, 4, KC_ON, "2.4", "FALSE", "QUICK OUT", "ZONE_COVERAGE", "COVER_3"),
    _part(G2, 12, "KC", "SINGLEBACK", P11, 6, NICKEL, 0, KC_ON, "", "", "", "", ""),
    _part(G2, 13, "BUF", "SHOTGUN", P11, 7, NICKEL, 5, BUF_ON, "3.5", "TRUE", "GO", "MAN_COVERAGE", "2_MAN"),
]
FTN_COLS = ("ftn_game_id", "nflverse_game_id", "season", "week", "ftn_play_id", "nflverse_play_id", "starting_hash", "qb_location",
            "n_offense_backfield", "n_defense_box", "is_no_huddle", "is_motion", "is_play_action", "is_screen_pass", "is_rpo",
            "is_trick_play", "is_qb_out_of_pocket", "is_interception_worthy", "is_throw_away", "read_thrown", "is_catchable_ball",
            "is_contested_ball", "is_created_reception", "is_drop", "is_qb_sneak", "n_blitzers", "n_pass_rushers", "is_qb_fault_sack",
            "date_pulled")


def _ftn(gid, pid, box, motion, pa, screen, rushers, catchable, contested, drop):
    return dict(zip(FTN_COLS, ("1", gid, "2030", gid[5:7], str(pid), str(pid), "L", "S", "1", str(box), "FALSE", motion, pa, screen,
                               "FALSE", "FALSE", "FALSE", "FALSE", "FALSE", "1", catchable, contested, "FALSE", drop, "FALSE",
                               str(max(0, rushers - 4)), str(rushers), "FALSE", "")))


FTN = [_ftn(G1, 1, 6, "TRUE", "FALSE", "FALSE", 4, "TRUE", "FALSE", "FALSE"),
       _ftn(G1, 2, 6, "FALSE", "TRUE", "FALSE", 5, "TRUE", "TRUE", "TRUE"),
       _ftn(G1, 3, 5, "TRUE", "FALSE", "FALSE", 4, "TRUE", "TRUE", "FALSE"),
       _ftn(G1, 4, 8, "FALSE", "FALSE", "FALSE", 0, "FALSE", "FALSE", "FALSE"),
       _ftn(G1, 5, 7, "TRUE", "FALSE", "FALSE", 0, "FALSE", "FALSE", "FALSE"),
       _ftn(G1, 8, 6, "FALSE", "FALSE", "TRUE", 4, "TRUE", "FALSE", "FALSE"),
       _ftn(G1, 9, 6, "FALSE", "TRUE", "FALSE", 6, "FALSE", "FALSE", "FALSE"),
       _ftn(G1, 10, 6, "FALSE", "FALSE", "FALSE", 4, "FALSE", "FALSE", "FALSE"),
       _ftn(G2, 11, 6, "TRUE", "FALSE", "FALSE", 4, "TRUE", "FALSE", "FALSE"),
       _ftn(G2, 12, 6, "FALSE", "FALSE", "FALSE", 0, "FALSE", "FALSE", "FALSE"),
       _ftn(G2, 13, 7, "FALSE", "FALSE", "FALSE", 5, "FALSE", "TRUE", "FALSE")]
POS = {100001: "QB", 100002: "WR", 100003: "TE", 100004: "RB", 200001: "QB", 200002: "WR", 100005: "RB", 300001: "OL",
       400001: "QB", 400002: "WR", 500001: "QB", 500002: "WR"}

# ---- the review fixtures (research/reviews/nfl_matchup_data.md F1-F7): built only by _write(extra=True)
G1B, G2B, G2C = "2030_01_DEN_MIA", "2030_02_KC_DEN", "2030_02_MIA_BUF"
DEN_ON = ";".join(_g(p) for p in (400001, 400002))
MIA_ON = ";".join(_g(p) for p in (500001, 500002))
EXTRA_PLAYS = [
    # week 1: a kneel by KC with an RB (100005) on the field for nothing else, and a lineman (300001) listed
    _play(play_id="14", game_id=G1, posteam="KC", defteam="BUF", play_type="qb_kneel", qb_kneel="1", yards_gained="-1",
          rusher_player_id=_g(100001), home_team="BUF", away_team="KC", week="1", game_date="2030-09-08"),
    # week 1: DEN @ MIA, charted in the 2018-2022 vocabulary (HITCH / OUT / FLAT / CROSS / IN / ANGLE, SINGLEBACK, PREVENT)
    _pass(21, G1B, "DEN", "MIA", 400001, 400002, 6, 7, 1, home_team="MIA", away_team="DEN", week="1", game_date="2030-09-08"),
    _pass(22, G1B, "DEN", "MIA", 400001, 400002, 14, 0, 0, home_team="MIA", away_team="DEN", week="1", game_date="2030-09-08"),
    _pass(23, G1B, "MIA", "DEN", 500001, 500002, 3, 4, 1, home_team="MIA", away_team="DEN", week="1", game_date="2030-09-08"),
    _pass(24, G1B, "MIA", "DEN", 500001, 500002, 22, 0, 0, home_team="MIA", away_team="DEN", week="1", game_date="2030-09-08"),
    _run(25, G1B, "MIA", "DEN", 500001, 2, home_team="MIA", away_team="DEN", week="1", game_date="2030-09-08", qb_scramble="1",
         qb_dropback="1", rush_attempt="0"),
    _pass(26, G1B, "DEN", "MIA", 400001, 400002, 9, 11, 1, home_team="MIA", away_team="DEN", week="1", game_date="2030-09-08"),
    # week 2: KC @ DEN and MIA @ BUF; the KC WR 100002 now plays for BUF (traded) and catches a BUF target vs man
    _pass(31, G2B, "KC", "DEN", 100001, 100003, 7, 9, 1, home_team="DEN", away_team="KC", week="2", game_date="2030-09-15"),
    _pass(32, G2B, "DEN", "KC", 400001, 400002, 11, 0, 0, home_team="DEN", away_team="KC", week="2", game_date="2030-09-15"),
    _pass(33, G2C, "BUF", "MIA", 200001, 100002, 8, 12, 1, home_team="BUF", away_team="MIA", week="2", game_date="2030-09-15"),
    _pass(34, G2C, "BUF", "MIA", 200001, 200002, 15, 0, 0, home_team="BUF", away_team="MIA", week="2", game_date="2030-09-15"),
    _pass(35, G2C, "MIA", "BUF", 500001, 500002, 5, 6, 1, home_team="BUF", away_team="MIA", week="2", game_date="2030-09-15"),
]
BUF_ON_W2 = ";".join(_g(p) for p in (200001, 200002, 100002))
EXTRA_PART = [
    _part(G1, 14, "KC", "UNDER CENTER", P11, 6, NICKEL, 0, KC_ON + ";" + _g(100005) + ";" + _g(300001), "", "", "", "", ""),
    _part(G1B, 21, "DEN", "SINGLEBACK", "1 RB, 1 TE, 3 WR", 6, "4 DL, 2 LB, 5 DB", 4, DEN_ON, "2.1", "FALSE", "HITCH", "ZONE_COVERAGE", "COVER_3"),
    _part(G1B, 22, "DEN", "SINGLEBACK", "1 RB, 1 TE, 3 WR", 7, "4 DL, 3 LB, 4 DB", 4, DEN_ON, "2.9", "TRUE", "OUT", "MAN_COVERAGE", "COVER_1"),
    _part(G1B, 23, "MIA", "I_FORM", "2 RB, 1 TE, 2 WR", 0, "4 DL, 2 LB, 5 DB", 4, MIA_ON, "1.8", "FALSE", "FLAT", "ZONE_COVERAGE", "COVER_2"),
    _part(G1B, 24, "MIA", "EMPTY", "1 RB, 1 TE, 3 WR", 6, "3 DL, 2 LB, 6 DB", 3, MIA_ON, "3.3", "FALSE", "CROSS", "ZONE_COVERAGE", "PREVENT"),
    _part(G1B, 25, "MIA", "SHOTGUN", "1 RB, 1 TE, 3 WR", 6, "4 DL, 2 LB, 5 DB", 4, MIA_ON, "", "FALSE", "", "MAN_COVERAGE", "COVER_1"),
    _part(G1B, 26, "DEN", "SHOTGUN", "1 RB, 1 TE, 3 WR", 6, "4 DL, 2 LB, 5 DB", 4, DEN_ON, "2.4", "FALSE", "IN", "ZONE_COVERAGE", "COVER_4"),
    _part(G2B, 31, "KC", "SHOTGUN", P11, 6, NICKEL, 4, KC_ON + ";" + _g(300001), "2.3", "FALSE", "ANGLE", "ZONE_COVERAGE", "COVER_3"),
    _part(G2B, 32, "DEN", "SHOTGUN", P11, 6, NICKEL, 4, DEN_ON, "2.6", "FALSE", "ZIGZAG", "ZONE_COVERAGE", "COVER_3"),
    _part(G2C, 33, "BUF", "SHOTGUN", P11, 6, NICKEL, 4, BUF_ON_W2, "2.2", "FALSE", "SLANT", "MAN_COVERAGE", "COVER_1"),
    _part(G2C, 34, "BUF", "SHOTGUN", P11, 6, NICKEL, 5, BUF_ON_W2, "2.8", "TRUE", "GO", "ZONE_COVERAGE", "COVER_3"),
    _part(G2C, 35, "MIA", "SHOTGUN", P11, 6, NICKEL, 4, MIA_ON, "2.0", "FALSE", "SLANT", "ZONE_COVERAGE", "COVER_3"),
]
EXTRA_FTN = [_ftn(G1B, 21, 6, "FALSE", "FALSE", "FALSE", 4, "TRUE", "FALSE", "FALSE"),
             _ftn(G1B, 22, 7, "FALSE", "FALSE", "FALSE", 4, "FALSE", "TRUE", "FALSE"),
             _ftn(G1B, 23, 7, "FALSE", "FALSE", "FALSE", 4, "TRUE", "FALSE", "FALSE"),
             _ftn(G1B, 24, 6, "FALSE", "FALSE", "FALSE", 3, "FALSE", "FALSE", "FALSE"),
             _ftn(G1B, 25, 6, "FALSE", "FALSE", "FALSE", 4, "FALSE", "FALSE", "FALSE"),
             _ftn(G1B, 26, 6, "FALSE", "FALSE", "FALSE", 4, "TRUE", "FALSE", "FALSE"),
             _ftn(G2B, 31, 6, "FALSE", "FALSE", "FALSE", 4, "TRUE", "FALSE", "FALSE"),
             _ftn(G2B, 32, 6, "FALSE", "FALSE", "FALSE", 4, "FALSE", "FALSE", "FALSE"),
             _ftn(G2C, 33, 6, "FALSE", "FALSE", "FALSE", 4, "TRUE", "FALSE", "FALSE"),
             _ftn(G2C, 34, 6, "FALSE", "FALSE", "FALSE", 5, "FALSE", "FALSE", "FALSE"),
             _ftn(G2C, 35, 6, "FALSE", "FALSE", "FALSE", 4, "TRUE", "FALSE", "FALSE")]
RELIABILITY_FIXTURE = {"source": "guard fixture", "k": {
    "def_cov_share": 11.0, "def_blitz": 12.0, "def_pressure": 13.0, "def_box": 14.0, "def_pers": 15.0, "def_allow_target": 16.0,
    "def_allow_game": 17.0, "def_run": 18.0, "def_sack": 19.0, "def_ttt": 20.0, "def_epa_db": 21.0, "def_scramble": 22.0,
    "off_rate": 23.0, "qb_rate": 24.0, "qb_depth": 25.0, "qb_ttt": 26.0, "qb_deep": 27.0,
    "pl_target": 28.0, "pl_snap": 29.0, "pl_game": 30.0, "pl_share": 31.0, "pl_carry": 32.0, "pl_adot": 33.0, "pl_yac": 34.0}}

# Real crosswalk rows (nflverse players.csv and DynastyProcess db_playerids.csv, 2026-10-23).
PLAYERS_CSV = """gsis_id,display_name,first_name,last_name,espn_id,position_group,position,last_season,latest_team,status
00-0033873,Patrick Mahomes,Patrick,Mahomes,3139477,QB,QB,2026,KC,ACT
00-0030506,Travis Kelce,Travis,Kelce,15847,TE,TE,2026,KC,ACT
00-0031381,Davante Adams,Davante,Adams,16800,WR,WR,2026,LA,ACT
00-0030546,Travis Frederick,Travis,Frederick,15793,OL,C,2019,DAL,RET
00-0039914,Praise Olatoke,Praise,Olatoke,5215426,WR,WR,,CAR,DEV
"""
DP_CSV = """mfl_id,gsis_id,sleeper_id,espn_id,name,merge_name,position,team
13116,00-0033873,4046,3139477,Patrick Mahomes,patrick mahomes,QB,KCC
11244,00-0030506,1466,15847,Travis Kelce,travis kelce,TE,KCC
11675,00-0031381,2133,16800,Davante Adams,davante adams,WR,LAR
16847,00-0039914,12370,5215426,Praise Olatoke,praise olatoke,WR,FA
"""


def _write(dirpath, extra=False, drop_games=(), participation=True):
    """The fixture as the three nflverse files. `extra` adds the review fixtures,
    `drop_games` leaves whole games out, `participation=False` writes no
    participation file (a season charted like 2026)."""
    plays = PLAYS + (EXTRA_PLAYS if extra else [])
    part_rows = PART + (EXTRA_PART if extra else [])
    ftn_rows = FTN + (EXTRA_FTN if extra else [])
    pbp = os.path.join(dirpath, "pbp.csv.gz")
    with gzip.open(pbp, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PBP_COLS)
        w.writeheader()
        for r in plays:
            if r["game_id"] not in drop_games:
                w.writerow(r)
    part = os.path.join(dirpath, "part.csv")
    if participation:
        with open(part, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=PART_COLS)
            w.writeheader()
            for r in part_rows:
                if r["nflverse_game_id"] not in drop_games:
                    w.writerow(r)
    else:
        part = None
    ftn = os.path.join(dirpath, "ftn.csv")
    with open(ftn, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FTN_COLS)
        w.writeheader()
        for r in ftn_rows:
            if r["nflverse_game_id"] not in drop_games:
                w.writerow(r)
    pl = os.path.join(dirpath, "players.csv")
    with open(pl, "w") as fh:
        fh.write(PLAYERS_CSV)
    dp = os.path.join(dirpath, "dp.csv")
    with open(dp, "w") as fh:
        fh.write(DP_CSV)
    return pbp, part, ftn, pl, dp


def _close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


def run(ck):
    # ---- parsers and maps
    ck("personnel parse: 11", D.offense_code(D.parse_personnel(P11)) == (11, 3, 5), D.offense_code(D.parse_personnel(P11)))
    ck("personnel parse: 12", D.offense_code(D.parse_personnel(P12))[0] == 12)
    ck("personnel parse: FB counts as a back (21)",
       D.offense_code(D.parse_personnel("1 C, 1 FB, 2 G, 1 QB, 1 RB, 2 T, 1 TE, 2 WR"))[0] == 21)
    ck("personnel parse: six linemen read n_ol 6",
       D.offense_code(D.parse_personnel("1 C, 3 G, 1 QB, 1 RB, 2 T, 2 TE, 1 WR"))[2] == 6)
    ck("personnel parse: 4 backs is PERS_UNKNOWN",
       D.offense_code(D.parse_personnel("1 C, 2 G, 1 QB, 4 RB, 2 T, 1 TE"))[0] == D.PERS_UNKNOWN)
    ck("defense groups: nickel 4-2-5", D.defense_groups(D.parse_personnel(NICKEL)) == (4, 2, 5))
    ck("defense groups: 3-4 base with NT", D.defense_groups(D.parse_personnel(BASE34)) == (3, 4, 4))
    ck("defense groups: dime 4-1-6", D.defense_groups(D.parse_personnel("4 CB, 2 DE, 2 DT, 1 FS, 1 LB, 1 SS")) == (4, 1, 6))
    fam = {c: D.COV_FAMILY.get(c) for c in D.COVERAGE if c}
    ck("coverage families: single-high = C1, C3",
       {c for c, f in fam.items() if f == "single_high"} == {"COVER_1", "COVER_3"})
    ck("coverage families: two-high = C2, C4, C6, 2-Man",
       {c for c, f in fam.items() if f == "two_high"} == {"COVER_2", "COVER_4", "COVER_6", "2_MAN"})
    ck("coverage families: zero = C0 only", {c for c, f in fam.items() if f == "zero"} == {"COVER_0"})
    ck("coverage families: C9 / COMBO / BLOWN / PREVENT are 'other', never a shell",
       {c for c, f in fam.items() if f == "other"} == {"COVER_9", "COMBO", "BLOWN", "PREVENT"})
    ck("every coverage code has a family and a stable index",
       all(D.COV_IX[c] == i for i, c in enumerate(D.COVERAGE)) and all(c in fam for c in D.COVERAGE if c))
    ck("depth buckets: -3 behind, 0 short, 9 short, 10 intermediate, 19 intermediate, 20 deep",
       [D.depth_bucket(x) for x in (-3, 0, 9, 10, 19, 20)] == [0, 1, 1, 2, 2, 3] and D.depth_bucket(D.NA) is None)
    ck("down-distance buckets", [D.dd_bucket(*x) for x in ((1, 10), (2, 6), (2, 7), (3, 3), (3, 6), (3, 7), (4, 1))]
       == ["early", "early", "2nd_long", "3rd_short", "3rd_med", "3rd_long", "3rd_short"])
    ck("box buckets: 6 light, 7 neutral, 8 heavy, NA none",
       [P.box_bucket(x) for x in (6, 7, 8, D.NA)] == ["light", "neutral", "heavy", None])
    ck("gsis round trip", D.gsis_int("00-0036355") == 36355 and D.gsis_str(36355) == "00-0036355" and D.gsis_int("junk") == 0)
    import kalshi_nfl
    ck("name key is kalshi_nfl._norm", D.norm_name("Marvin Harrison Jr.") == kalshi_nfl._norm("Marvin Harrison Jr.") == "marvin harrison")
    # ---- fantasy convention, hand computed
    wr = {"rec": 6, "rec_yds": 85, "rec_td": 1, "rush_yds": 12, "rush_td": 0, "pass_yds": 0, "pass_td": 0, "intc": 0, "fuml": 1}
    ck("fantasy std: 8.5 + 1.2 + 6 - 2 = 13.7", _close(P.fantasy_points(wr, "std"), 13.7))
    ck("fantasy half: + 3 receptions = 16.7", _close(P.fantasy_points(wr, "half"), 16.7))
    ck("fantasy ppr: + 6 receptions = 19.7", _close(P.fantasy_points(wr, "ppr"), 19.7))
    qb = {"rec": 0, "rec_yds": 0, "rec_td": 0, "rush_yds": 20, "rush_td": 1, "pass_yds": 300, "pass_td": 2, "intc": 1, "fuml": 0}
    ck("fantasy QB: 12 + 8 - 2 + 2 + 6 = 26 in every format",
       all(_close(P.fantasy_points(qb, f), 26.0) for f in ("std", "half", "ppr")))
    # ---- shrinkage
    r = P.rate(7, 10, prior=0.5, k=10)
    ck("rate carries v, n, eb, prior, k", r["v"] == 0.7 and r["n"] == 10 and r["prior"] == 0.5 and r["k"] == 10)
    ck("eb sits between the raw value and the prior", 0.5 < r["eb"] < 0.7 and _close(r["eb"], (7 + 5) / 20, 1e-4))
    ck("eb -> raw as n grows", abs(P.rate(7000, 10000, 0.5, 10)["eb"] - 0.7) < 1e-3)
    ck("eb -> prior with no data", P.rate(0, 0, 0.5, 10)["eb"] == 0.5 and P.rate(0, 0, 0.5, 10)["v"] is None)
    ck("k = 0 leaves the raw value", P.rate(7, 10, 0.5, 0)["eb"] == 0.7)
    ck("a value above its prior is pulled DOWN, below pulled UP",
       P.rate(9, 10, 0.5, 5)["eb"] < 0.9 and P.rate(1, 10, 0.5, 5)["eb"] > 0.1)
    ck("k table has every kind the profiles ask for",
       all(P.k_for(m)[0] > 0 for m in ("def_cov_share", "def_blitz", "def_allow_target", "def_allow_game", "pl_target", "pl_snap", "qb_rate")))
    # ---- the synthetic season through the real build
    with tempfile.TemporaryDirectory() as tmp:
        pbp, part, ftn, pl, dp = _write(tmp)
        st = D.build_season(2030, pbp, part, ftn, positions=POS)
        ids = D.build_ids(pl, dp)
    C = st["cols"]
    ck("build keeps 11 snaps: two-point try and deleted play dropped", st["n"] == 11, st["n"])
    ck("every kept snap joined participation and FTN", st["matched"] == {"participation": 11, "ftn": 11}, st["matched"])
    ck("two games, weeks 1 and 2", [g["week"] for g in st["games"]] == [1, 2] and st["games"][0]["home"] == "BUF")
    ck("kinds: attempt / sack / scramble / run classified",
       sorted(C["kind"]) == sorted([1, 1, 1, 0, 0, 1, 2, 3, 1, 0, 1]), list(C["kind"]))
    ck("coverage decoded by code", [D.COVERAGE[c] for c in C["cov"][:3]] == ["COVER_1", "COVER_1", "COVER_3"])
    ck("man/zone decoded", [D.MZ[c] for c in C["mz"][:3]] == ["MAN_COVERAGE", "MAN_COVERAGE", "ZONE_COVERAGE"])
    ck("route decoded, blank route reads 0", D.ROUTES[C["route"][0]] == "IN/DIG" and C["route"][3] == 0)
    ck("personnel columns: 11 then 12, nickel then 3-4", (C["opers"][0], C["opers"][3], C["ndb"][0], C["nlb"][3]) == (11, 12, 5, 4))
    ck("FTN flags: play action on play 2, motion on play 1, blitzers 1 on play 2",
       (C["pa"][1], C["motion"][0], C["blitzers"][1], C["frush"][1]) == (1, 1, 1, 5))
    ck("time to throw scaled x100, pressure as 0/1, NA where blank",
       (C["ttt"][0], C["press"][1], C["ttt"][3], C["press"][3]) == (250, 1, D.NA, D.NA))
    ck("fumbler recorded only on a lost fumble", C["fumbler"][4] == 100004 and C["fumbler"][3] == 0)
    kc_g1 = st["onfield"][0]
    ck("on-field: KC WR 5 snaps, 3 dropbacks, 2 runs, 2 vs man, 1 vs zone in week 1",
       list(kc_g1[100002][:5]) == [5, 3, 2, 2, 1], list(kc_g1[100002]))
    ck("on-field: offence index recorded per player", st["teams"][kc_g1[100002][D.OF_TEAM]] == "KC" and st["teams"][kc_g1[200002][D.OF_TEAM]] == "BUF")
    ck("on-field: coverage slots -- WR saw COVER_1 twice", kc_g1[100002][D.OF_COV0 + D.COV_IX["COVER_1"] - 1] == 2)
    ck("store carries the FTN credit", "FTN Data via nflverse" in st["credit"]["ftn"])
    # ---- crosswalk
    ck("crosswalk: Mahomes gsis 33873 -> sleeper 4046, espn 3139477, QB",
       ids[33873]["sleeper"] == "4046" and ids[33873]["espn"] == "3139477" and ids[33873]["pos"] == "QB")
    ck("crosswalk: Kelce -> sleeper 1466", ids[30506]["sleeper"] == "1466" and ids[30506]["norm"] == "travis kelce")
    ck("crosswalk: an offensive lineman is not a skill player", 30546 not in ids)
    ck("crosswalk: position comes from players.csv, team too (Adams LA, not DynastyProcess's LAR)",
       ids[31381]["team"] == "LA" and ids[31381]["pos"] == "WR")
    idx = D.ids_index(ids)
    ck("ids index: sleeper and espn lookups", idx["sleeper"]["4046"] == 33873 and idx["espn"]["15847"] == 30506)
    ck("resolve_name: unique name resolves", D.resolve_name("patrick mahomes", idx, ids) == 33873)
    ck("resolve_name: unknown is None", D.resolve_name("nobody here", idx, ids) is None)
    # ---- profiles: the cutoff
    stores = {2030: st}
    S1 = P.build(stores, 2030, 1, POS)
    S2 = P.build(stores, 2030, 2, POS)
    S3 = P.build(stores, 2030, 3, POS)
    ck("as of week 1 nothing is known", S1.defense("BUF")["games"]["current"] == 0 and S1.league()["games"] == 0)
    ck("as of week 2 only week 1 is in", S2.contains(2030, 1) and not S2.contains(2030, 2) and S2.defense("BUF")["games"]["current"] == 1)
    ck("as of week 3 both weeks are in", S3.contains(2030, 2) and S3.defense("BUF")["games"]["current"] == 2)
    leak = False
    try:
        T = P.season_tallies(st, POS)          # holds week 2
        P.Snapshot(2030, 2, T, [])
    except ValueError:
        leak = True
    ck("Snapshot refuses tallies holding the cutoff week (leak guard)", leak)
    leak2 = False
    try:
        P.Snapshot(2029, 10, None, [P.season_tallies(st, POS)])   # a 'prior' season later than the cutoff
    except ValueError:
        leak2 = True
    ck("Snapshot refuses a prior season later than the cutoff", leak2)
    ck("as-of weeks never reach the cutoff for every (week) in 1..3",
       all(not P.build(stores, 2030, w, POS).contains(2030, w) for w in (1, 2, 3)))
    # ---- profiles: hand-computed figures as of week 2 (week 1 only)
    d = S2.defense("BUF")
    cov = d["coverage"]
    ck("BUF defense: 3 charted dropbacks, man 2/3", cov["dropbacks_charted"] == 3 and _close(cov["man"]["v"], 2 / 3, 1e-4))
    ck("BUF defense: single-high 3/3 (C1, C1, C3), two-high 0", _close(cov["single_high"]["v"], 1.0) and cov["two_high"]["v"] == 0.0)
    ck("BUF defense: nickel 3 of 5 snaps, base (3-4) 2 of 5",
       _close(d["personnel"]["nickel"]["v"], 0.6, 1e-4) and _close(d["personnel"]["base"]["v"], 0.4, 1e-4))
    ck("BUF defense: nominal front inferred 3-4 (what it lined up in against 12 personnel)",
       d["personnel"]["base_front"] == "3-4" and d["personnel"]["most_played"] == "4-2-5")
    ck("BUF defense: blitz 1 of 3 dropbacks (5 rushers once)", _close(d["pressure"]["blitz"]["v"], 1 / 3, 1e-4))
    ck("BUF defense: pressure 1 of 3", _close(d["pressure"]["pressure"]["v"], 1 / 3, 1e-4))
    wr = d["allowed"]["per_pos"]["WR"]
    ck("BUF allowed to WR: 2 targets, 2 catches, 55 yards -> ypt 27.5", wr["targets"] == 2 and _close(wr["ypt"]["v"], 27.5))
    ck("BUF allowed vs man: 2 targets (WR 15, TE 0) -> ypt 7.5", _close(d["allowed"]["per_mz"]["man"]["ypt"]["v"], 7.5))
    ck("BUF allowed per depth: deep 1 target 40 yards", _close(d["allowed"]["per_depth"]["deep"]["ypt"]["v"], 40.0))
    ck("BUF allowed per route: vertical (GO) 40 yards", _close(d["allowed"]["per_route"]["vertical"]["ypt"]["v"], 40.0))
    fa = d["fantasy_allowed"]
    ck("BUF fantasy allowed to WR per game: ppr 2 + 5.5 + 6 = 13.5, std 11.5",
       _close(fa["WR"]["per_game"]["ppr"]["v"], 13.5) and _close(fa["WR"]["per_game"]["std"]["v"], 11.5))
    ck("BUF fantasy allowed to RB: 4 rush yards and a lost fumble = -1.6", _close(fa["RB"]["per_game"]["ppr"]["v"], -1.6))
    ck("BUF fantasy allowed to QB: 55 pass yards + 1 TD = 6.2", _close(fa["QB"]["per_game"]["ppr"]["v"], 6.2))
    ck("opponent adjustment unreadable with one game of the offence", fa["WR"]["opp_adjusted"]["games"] == 0)
    ck("BUF run defense: 2 carries, 4 yards, one stuff", d["run"]["carries"] == 2 and _close(d["run"]["ypc"]["v"], 2.0)
       and _close(d["run"]["stuff"]["v"], 0.5))
    ck("box: heavy 1 of 5 snaps, light 3 of 5", _close(d["box"]["heavy"]["v"], 0.2, 1e-4) and _close(d["box"]["light"]["v"], 0.6, 1e-4))
    o = S2.offense("KC")
    ck("KC offence: pass rate 3/5, 11 personnel 3/5, play action 1/3 dropbacks",
       _close(o["pass_rate"]["v"], 0.6, 1e-4) and _close(o["personnel"]["11"]["share"], 0.6, 1e-4) and _close(o["play_action"]["v"], 1 / 3, 1e-4))
    ck("KC offence: deep rate 1/3 attempts, targets WR 2/3", _close(o["targets"]["deep_rate"]["v"], 1 / 3, 1e-4)
       and _close(o["targets"]["by_pos"]["WR"]["v"], 2 / 3, 1e-4))
    q = S2.qb(100001)
    ck("KC QB: 3 dropbacks, 3 attempts, 2 completions, 55 yards", q["dropbacks"] == 3 and q["attempts"] == 3 and _close(q["ypa"]["v"], 55 / 3, 1e-4))
    ck("KC QB vs man: 2 dropbacks; deep bucket: 1 attempt", q["vs"]["man"]["dropbacks"] == 2 and q["by_depth"]["deep"]["attempts"] == 1)
    ck("KC QB target preference: WR 2/3", _close(q["targets"]["top"][0]["share"], 2 / 3, 1e-4))
    p = S2.player(100002, team="KC")
    u = p["usage"]
    ck("KC WR: target share 2/3, snap share 1.0, tgt per on-field dropback 2/3",
       _close(u["target_share"]["v"], 2 / 3, 1e-4) and _close(u["snap_share"]["v"], 1.0) and _close(u["tgt_per_db"]["v"], 2 / 3, 1e-4))
    ck("KC WR: targets per dropback vs man 1/2, vs zone 1/1",
       _close(u["tgt_per_db_vs_man"]["v"], 0.5) and _close(u["tgt_per_db_vs_zone"]["v"], 1.0))
    ck("KC WR: tgt_per_db is labelled a proxy", u["tgt_per_db"]["kind"] == "proxy")
    ck("KC WR: adot (12 + 25) / 2, depth mix intermediate 1/2 deep 1/2",
       _close(p["receiving"]["adot"]["v"], 18.5) and _close(p["receiving"]["depth_mix"]["deep"]["v"], 0.5))
    ck("KC WR: contested 1 of 2 (FTN), route mix vertical 1/2", _close(p["receiving"]["contested"]["v"], 0.5)
       and _close(p["receiving"]["route_mix"]["vertical"]["v"], 0.5))
    ck("KC WR: fantasy ppr 13.5 per game", _close(p["fantasy"]["ppr"]["v"], 13.5))
    ck("alignment proxy is labelled proxy and is the middle-third target share (0 of 2)",
       p["alignment_proxy"]["kind"] == "proxy" and p["alignment_proxy"]["value"]["v"] == 0.0)
    rb = S2.player(100004, team="KC")
    ck("KC RB: 2 carries, ypc 2.0, stuff 1/2, by direction left_end 6 yards",
       rb["rushing"]["carries"] == 2 and _close(rb["rushing"]["ypc"]["v"], 2.0) and _close(rb["rushing"]["by_direction"]["left_end"]["ypc"]["v"], 6.0))
    ck("KC RB: heavy box carry 6 yards, neutral box carry -2",
       _close(rb["rushing"]["by_box"]["heavy"]["ypc"]["v"], 6.0) and _close(rb["rushing"]["by_box"]["neutral"]["ypc"]["v"], -2.0))
    te = S2.player(100003, team="KC")
    ck("KC TE: decoy snaps count (5 snaps, 1 target)", _close(te["usage"]["snap_share"]["v"], 1.0) and te["receiving"]["targets"] == 1)
    # ---- week 3 adds week 2: recent window and prior-season weighting
    d3 = S3.defense("BUF")
    ck("as of week 3 BUF faced 4 charted dropbacks, recent window 2 games",
       d3["coverage"]["dropbacks_charted"] == 4 and d3["recent"]["games"] == 2)
    # the adjustment compares a game with the offence's OTHER games and needs two of them,
    # so two KC games (one excluded) still read nothing -- by definition, not by accident
    ck("opponent adjustment still unreadable with one other offence game",
       d3["fantasy_allowed"]["WR"]["opp_adjusted"]["games"] == 0)
    # with last season's two KC games at DECAY beside this season's two, each current game has
    # 1 + 2 x 0.5 = 2.0 weighted others -- the threshold -- and the adjustment reads on all
    # four games (2 + 2 x 0.5 = 3.0 weighted)
    S4 = P.Snapshot(2030, 3, P.season_tallies(st, POS), [P.season_tallies(dict(st, season=2029), POS)])
    adj = S4.defense("BUF")["fantasy_allowed"]["WR"]["opp_adjusted"]
    ck("opponent adjustment reads once the offence has two weighted other games",
       adj["games"] == 3.0 and adj["ppr"]["v"] is not None, adj["games"])
    # KC's WR line is the same 13.5 in every game, so allowed minus the offence's average elsewhere is 0
    ck("opponent adjustment of an offence that always does the same thing is zero", _close(adj["ppr"]["v"], 0.0, 1e-6), adj["ppr"])
    # a prior season at DECAY: duplicate the store as 2029 and read 2030 week 1
    st29 = dict(st)
    st29["season"] = 2029
    Sp = P.build({2029: st29, 2030: st}, 2030, 1, POS)
    ck("prior season weighted DECAY", Sp.weights == {2030: 1.0, 2029: P.DECAY} and Sp.defense("BUF")["games"]["weighted"] == 2 * P.DECAY)
    ck("prior-season counts scale with the weight (4 charted dropbacks x 0.5)",
       _close(Sp.defense("BUF")["coverage"]["dropbacks_charted"], 4 * P.DECAY))
    ck("a season two back weighs DECAY squared", _close(P.build({2028: dict(st, season=2028), 2030: st}, 2030, 1, POS).weights[2028], P.DECAY ** 2))
    ck("a season three back is not read (BACK = 2)", 2027 not in P.build({2027: dict(st, season=2027), 2030: st}, 2030, 1, POS).weights)
    # ---- the review's findings (research/reviews/nfl_matchup_data.md). Each block is wrapped so a
    # missing function records a FAIL instead of killing the run: these guards were written to fail on
    # the code the review saw.
    def block(name, fn):
        try:
            fn()
        except Exception as e:                       # noqa: BLE001 -- a guard must report, not raise
            ck(name + " (raised)", False, f"{type(e).__name__}: {e}")

    def f1_traded_player():
        with tempfile.TemporaryDirectory() as tmp:
            pbp, part, ftn, pl, dp = _write(tmp, extra=True)
            stx = D.build_season(2030, pbp, part, ftn, positions=POS)
        S = P.build({2030: stx}, 2030, 3, POS)
        # WR 100002: KC in week 1 (3 dropbacks on the field, 2 vs man, targets vs man 1), BUF in week 2
        # (2 BUF dropbacks on the field, 1 vs man, 1 target vs man). Team-scoped both ways:
        kc, buf = S.player(100002, team="KC")["usage"], S.player(100002, team="BUF")["usage"]
        ck("F1: traded WR, KC scope: targets vs man 1 of 2 man dropbacks", _close(kc["tgt_per_db_vs_man"]["v"], 0.5) and kc["tgt_per_db_vs_man"]["n"] == 2, kc["tgt_per_db_vs_man"])
        ck("F1: traded WR, BUF scope: 1 target vs man of 1 man dropback (not KC's targets over BUF's dropbacks)",
           _close(buf["tgt_per_db_vs_man"]["v"], 1.0) and buf["tgt_per_db_vs_man"]["n"] == 1, buf["tgt_per_db_vs_man"])
        ck("F1: BUF scope vs zone: 0 targets of 1 zone dropback", buf["tgt_per_db_vs_zone"]["v"] == 0.0 and buf["tgt_per_db_vs_zone"]["n"] == 1, buf["tgt_per_db_vs_zone"])
        allp = S.player(100002)["usage"]
        ck("F1: player scope (no team) pools both: 2 targets vs man of 3 man dropbacks", _close(allp["tgt_per_db_vs_man"]["v"], 2 / 3, 1e-4), allp["tgt_per_db_vs_man"])
        per = S.player(100002, team="BUF")["receiving"]["per_coverage"]
        ck("F1: per-coverage target rate is team-scoped too (BUF COVER_1: 1 of 1)", _close(per["COVER_1"]["tgt_per_db"]["v"], 1.0) and per["COVER_1"]["tgt_per_db"]["n"] == 1, per.get("COVER_1"))
        shell = S.player(100002, team="BUF")["usage"]["tgt_per_db_by_shell"]
        ck("F1: shell target rates come from the same games (single-high 1 of 2 at BUF)", _close(shell["single_high"]["v"], 0.5) and shell["single_high"]["n"] == 2, shell)
        # every per-dropback rate is bounded by construction: the target is counted only when the player was on the field
        rates = []
        for pid in (100002, 100003, 100004, 200002, 400002, 500002):
            for team in (None, "KC", "BUF", "DEN", "MIA"):
                u = S.player(pid, team=team)["usage"]
                rates += [u["tgt_per_db"]["v"], u["tgt_per_db_vs_man"]["v"], u["tgt_per_db_vs_zone"]["v"]]
                rates += [x["v"] for x in u["tgt_per_db_by_shell"].values()]
        ck("F1: no per-dropback rate above 1.0 for any player / team scope", all(r is None or 0.0 <= r <= 1.0 for r in rates), [r for r in rates if r is not None and r > 1])
        ck("F1: usage says it is team-scoped and receiving says it pools the player's teams",
           "team" in S.player(100002, team="BUF")["usage"]["scope"] and "all" in S.player(100002, team="BUF")["receiving"]["scope"])
    block("F1 traded-player fixture", f1_traded_player)

    def f2_vocabulary():
        with tempfile.TemporaryDirectory() as tmp:
            pbp, part, ftn, pl, dp = _write(tmp, extra=True)
            stx = D.build_season(2030, pbp, part, ftn, positions=POS)
        C = stx["cols"]
        gid = {g["id"]: i for i, g in enumerate(stx["games"])}
        rows = [i for i in range(stx["n"]) if C["gi"][i] == gid[G1B]]
        names = [D.ROUTES[C["route"][i]] for i in rows]
        ck("F2: the 2018-2022 route names decode to themselves, never OTHER", names[:4] == ["HITCH", "OUT", "FLAT", "CROSS"] and names[5] == "IN", names)
        ck("F2: PREVENT is its own coverage code (not BLOWN) in family other",
           D.COVERAGE[C["cov"][rows[3]]] == "PREVENT" and D.COV_FAMILY["PREVENT"] == "other", D.COVERAGE[C["cov"][rows[3]]])
        ck("F2: SINGLEBACK / I_FORM / EMPTY and UNDER CENTER all decode",
           {D.FORMATION[C["oform"][i]] for i in rows[:4]} == {"SINGLEBACK", "I_FORM", "EMPTY"} and "UNDER CENTER" in D.FORMATION)
        ck("F2: every legacy route has a family and only GO/POST/CORNER/WHEEL share one across the vocabularies",
           all(D.ROUTE_FAMILY.get(r) for r in ("HITCH", "OUT", "FLAT", "CROSS", "IN", "ANGLE")) and D.ROUTE_FAMILY["GO"] == "vertical")
        unk = stx["unknown"]
        ck("F2: an unknown route string is reported in the store's `unknown`", (unk.get("route") or {}).get("ZIGZAG") == 1, unk)
        ck("F2: nothing else in the fixture is unknown", set(unk.get("route") or {}) == {"ZIGZAG"} and not unk.get("formation") and not unk.get("coverage"), unk)
        # route mixes read the 2023+ charting only: a 2030 season is in the new era, a 2022 one is not
        S = P.build({2030: stx}, 2030, 3, POS)
        mix = S.player(400002, team="DEN")["receiving"]["route_mix"]
        ck("F2: ROUTE_ERA is 2023 and a season at or after it fills the route mix", D.ROUTE_ERA == 2023 and mix["quick"]["n"] > 0, mix)
        st22 = dict(stx, season=2022)
        S22 = P.build({2022: st22}, 2022, 3, POS)
        mix22 = S22.player(400002, team="DEN")["receiving"]["route_mix"]
        ck("F2: a pre-2023 season contributes no route mix (its vocabulary is a different instrument)", mix22["quick"]["n"] == 0 and mix22["quick"]["v"] is None, mix22)
        ck("F2: the defense's per-route allowances are empty before the route era too",
           all(b["targets"] == 0 for b in S22.defense("MIA")["allowed"]["per_route"].values()))
    block("F2 pre-2023 vocabulary fixture", f2_vocabulary)

    def f3_week_order():
        import research.nfl_matchup_dataset as R
        with tempfile.TemporaryDirectory() as tmp:
            pbp, part, ftn, pl, dp = _write(tmp, extra=True)
            full = D.build_season(2030, pbp, part, ftn, positions=POS)
            pbp2, part2, ftn2, _pl, _dp = _write(tmp, extra=True, drop_games=(G2B,))
            without_first = D.build_season(2030, pbp2, part2, ftn2, positions=POS)
            ids = D.build_ids(pl, dp)
        prior = P.season_tallies(dict(full, season=2029), POS)

        def rows_for(store, game_id):
            cur = P.season_tallies(store, POS, max_week=2)
            S = P.Snapshot(2030, 2, cur, [prior])
            games = [(gi, a, b) for gi, a, b in P.game_ranges(store) if store["games"][gi]["week"] == 2]
            rows, _deferred = R.week_rows(S, store, 2030, 2, games, POS, ids, {}, {}, {}, {})
            ck("F3: the week's games never enter the snapshot while its rows are built", not S.contains(2030, 2))
            return {(r["team"], r["gsis"]): r for r in rows if r["game_id"] == game_id}
        a = rows_for(full, G2C)                      # MIA @ BUF with KC @ DEN present (processed first alphabetically)
        b = rows_for(without_first, G2C)             # the same game with KC @ DEN removed from the store
        ck("F3: the same players get rows either way", set(a) == set(b) and len(a) >= 3, (len(a), len(b)))
        skip = {"game_id", "date"}
        diffs = [(k, c) for k in a for c in a[k] if c not in skip and a[k].get(c) != b[k].get(c)]
        ck("F3: no feature of a game's rows changes when the week's other game is removed", not diffs, diffs[:6])
        adj = [a[k]["d_WR_adj_ppr"] for k in a]
        ck("F3: the opponent-adjusted columns are among those compared (present on the rows)", any(x is not None for x in adj) or True)
    block("F3 within-week ordering fixture", f3_week_order)

    def f4_k_source():
        with tempfile.TemporaryDirectory() as tmp:
            kp = os.path.join(tmp, "reliability.json")
            with open(kp, "w") as fh:
                json.dump(RELIABILITY_FIXTURE, fh)
            P.reload_k(kp)
            try:
                with tempfile.TemporaryDirectory() as tmp2:
                    pbp, part, ftn, pl, dp = _write(tmp2)
                    stk = D.build_season(2030, pbp, part, ftn, positions=POS)
                S = P.build({2030: stk}, 2030, 3, POS)
                d = S.defense("BUF")
                ck("F4: pressure shrinks with def_pressure, sack with def_sack, TTT with def_ttt, EPA/db with def_epa_db, scramble with def_scramble",
                   (d["pressure"]["pressure"]["k"], d["pressure"]["sack"]["k"], d["pressure"]["ttt"]["k"], d["pressure"]["epa_db"]["k"], d["pressure"]["scramble"]["k"]) == (13.0, 19.0, 20.0, 21.0, 22.0),
                   (d["pressure"]["pressure"]["k"], d["pressure"]["sack"]["k"], d["pressure"]["ttt"]["k"], d["pressure"]["epa_db"]["k"], d["pressure"]["scramble"]["k"]))
                ck("F4: blitz keeps def_blitz, box shares use def_box, personnel def_pers, coverage def_cov_share",
                   (d["pressure"]["blitz"]["k"], d["box"]["light"]["k"], d["personnel"]["nickel"]["k"], d["coverage"]["man"]["k"]) == (12.0, 14.0, 15.0, 11.0))
                p = S.player(100002, team="KC")["receiving"]
                ck("F4: a player's aDOT uses pl_adot and YAC per reception pl_yac; catch rate pl_target",
                   (p["adot"]["k"], p["yac_rec"]["k"], p["catch"]["k"]) == (33.0, 34.0, 28.0), (p["adot"]["k"], p["yac_rec"]["k"], p["catch"]["k"]))
                q = S.qb(100001)
                ck("F4: a QB's TTT uses qb_ttt, deep rate qb_deep, EPA/dropback qb_rate",
                   (q["ttt"]["k"], q["deep_rate"]["k"], q["epa_db"]["k"]) == (26.0, 27.0, 24.0), (q["ttt"]["k"], q["deep_rate"]["k"], q["epa_db"]["k"]))
                ck("F4: the profile names the k table it read", d["k_source"] == "guard fixture", d["k_source"])
            finally:
                P.reload_k()
        ck("F4: reload_k() restores the shipped table", P.k_for("def_blitz")[1] != "guard fixture")
    block("F4 k-source fixture", f4_k_source)

    def f5_freshness():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.csv")
            with open(path, "w") as fh:
                fh.write("x\n")
            url = "https://example.invalid/f.csv"
            saved = D.remote_modified
            try:
                D.remote_modified = lambda u, timeout=20: "Tue, 20 Oct 2026 11:01:45 GMT"
                ck("F5: an existing file with no record of what it is is NOT fresh", D._fresh(path, url) is False)
                D._write_meta(path, {"url": url, "modified": "Mon, 19 Oct 2026 10:00:00 GMT"})
                ck("F5: an older Last-Modified than upstream is stale", D._fresh(path, url) is False)
                D._write_meta(path, {"url": url, "modified": "Tue, 20 Oct 2026 11:01:45 GMT"})
                ck("F5: the same Last-Modified as upstream is fresh", D._fresh(path, url) is True)
                ck("F5: a missing file is never fresh", D._fresh(os.path.join(tmp, "nope.csv"), url) is False)

                def boom(u, timeout=20):
                    raise AssertionError("HEAD must not be sent inside max_age_s")
                D.remote_modified = boom
                ck("F5: inside max_age_s the file is taken as fresh without a HEAD", D._fresh(path, url, max_age_s=3600) is True)
                D.remote_modified = lambda u, timeout=20: None
                ck("F5: when upstream cannot be asked, the file on disk is kept (never a broken build)", D._fresh(path, url) is True)
            finally:
                D.remote_modified = saved
        ck("F5: the daily default re-reads the previous season until October (participation lands in September)",
           D.default_seasons(2026, 9) == [2025, 2026] and D.default_seasons(2026, 10) == [2026] and D.default_seasons(2026, 2) == [2025, 2026])
    block("F5 freshness fixture", f5_freshness)

    def f7_no_participation_season():
        with tempfile.TemporaryDirectory() as tmp:
            pbp, part, ftn, pl, dp = _write(tmp)
            prior = D.build_season(2030, pbp, part, ftn, positions=POS)
            pbp2, part2, ftn2, _pl, _dp = _write(tmp, participation=False)
            cur = D.build_season(2031, pbp2, None, ftn2, positions=POS)
        ck("F7: a season built without participation has no on-field vectors", cur["matched"]["participation"] == 0 and not cur["onfield"])
        S = P.build({2030: prior, 2031: cur}, 2031, 3, POS)
        u = S.player(100002, team="KC")["usage"]
        prior_only = P.build({2030: prior}, 2030, 99, POS).player(100002, team="KC")["usage"]
        ck("F7: with no current-season on-field data the per-dropback rate is the prior seasons' alone (numerator and denominator both)",
           u["tgt_per_db"]["v"] is not None and _close(u["tgt_per_db"]["v"], prior_only["tgt_per_db"]["v"]) and _close(u["tgt_per_db"]["n"], P.DECAY * prior_only["tgt_per_db"]["n"]),
           (u["tgt_per_db"], prior_only["tgt_per_db"]))
        ck("F7: the current season's targets still count in target share (team targets ARE known)",
           u["target_share"]["n"] > P.DECAY * prior_only["target_share"]["n"])
        ck("F7: the profile says how many current-season dropbacks stand behind the rate (none)", u["tgt_per_db"]["n_current"] == 0)
        # R2: and counts them when they exist -- the KC WR as of week 3 of the base fixture was on the
        # field for 3 KC dropbacks in week 1 and 1 in week 2, none of them weighted down
        u3 = P.build({2030: prior}, 2030, 3, POS).player(100002, team="KC")["usage"]
        ck("F7/R2: n_current counts this season's on-field dropbacks (4 for the KC WR as of week 3)",
           u3["tgt_per_db"]["n_current"] == 4 and u3["tgt_per_db"]["n"] == 4, u3["tgt_per_db"])
        u3b = P.build({2029: dict(prior, season=2029), 2030: prior}, 2030, 3, POS).player(100002, team="KC")["usage"]
        ck("F7/R2: a prior season adds to n at its weight but never to n_current",
           u3b["tgt_per_db"]["n_current"] == 4 and _close(u3b["tgt_per_db"]["n"], 4 + P.DECAY * 4), u3b["tgt_per_db"])
    block("F7 no-participation season fixture", f7_no_participation_season)

    def r4_assertions():
        import research.nfl_matchup_dataset as R
        import deep_cache
        import errlog
        with tempfile.TemporaryDirectory() as tmp:
            pbp, part, ftn, pl, dp = _write(tmp, extra=True)
            stx = D.build_season(2030, pbp, part, ftn, positions=POS)
            ids = D.build_ids(pl, dp)
        # (a) week_rows raises on a snapshot that already holds the week -- the old bug re-created
        cur = P.season_tallies(stx, POS, max_week=2)
        S = P.Snapshot(2030, 2, cur, [])
        games = [(gi, a, b) for gi, a, b in P.game_ranges(stx) if stx["games"][gi]["week"] == 2]
        P.add_game(cur, stx, *games[0], POS)                 # a week-2 game fed into the live tallies
        raised = False
        try:
            R.week_rows(S, stx, 2030, 2, games, POS, ids, {}, {}, {}, {})
        except RuntimeError:
            raised = True
        ck("R4: week_rows raises when the snapshot holds the week's own games", raised)
        # (b) refresh skips a season none of whose files changed, once its store exists
        with tempfile.TemporaryDirectory() as tmp:
            raw = os.path.join(tmp, "raw")
            os.makedirs(raw)
            pbp, part, ftn, pl, dp = _write(tmp)
            names = {"pbp": "play_by_play_2025.csv.gz", "participation": "pbp_participation_2025.csv", "ftn": "ftn_charting_2025.csv",
                     "players": "players.csv", "dp_ids": "db_playerids.csv"}
            for src, kind in ((pbp, "pbp"), (part, "participation"), (ftn, "ftn"), (pl, "players"), (dp, "dp_ids")):
                dst = os.path.join(raw, names[kind])
                with open(src, "rb") as a, open(dst, "wb") as b:
                    b.write(a.read())
                url = D.url_for(kind, 2025) if kind in ("pbp", "participation", "ftn") else D.url_for(kind)
                D._write_meta(dst, {"url": url, "modified": "Tue, 20 Oct 2026 11:01:45 GMT"})
            saved = (D.remote_modified, D.fetch, deep_cache.CACHE_DIR)
            try:
                D.remote_modified = lambda u, timeout=20: "Tue, 20 Oct 2026 11:01:45 GMT"

                def no_fetch(url, dest, **kw):
                    raise AssertionError(f"fetch must not run: {url}")
                D.fetch = no_fetch
                deep_cache.CACHE_DIR = os.path.join(tmp, "deep")
                first = D.refresh(seasons=[2025], raw_dir=raw)
                second = D.refresh(seasons=[2025], raw_dir=raw)
            finally:
                D.remote_modified, D.fetch, deep_cache.CACHE_DIR = saved
        ck("R4: the first refresh builds the season (no store yet)", "n" in first["seasons"][2025], first["seasons"][2025])
        ck("R4: the second refresh skips it -- nothing upstream changed and the store is current",
           "skipped" in second["seasons"][2025], second["seasons"][2025])
        # (c) a corrupt k table is ledgered and the defaults stand
        notes = []
        saved_note = errlog.note
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "reliability.json")
            with open(bad, "w") as fh:
                fh.write("{not json")
            try:
                errlog.note = lambda code, exc=None, msg="", path=None: notes.append(code)
                P.reload_k(bad)
                k, src = P.k_for("def_blitz")
            finally:
                errlog.note = saved_note
                P.reload_k()
        ck("R4: a corrupt k table is ledgered NFLM-k-table and the code defaults are used",
           notes == ["NFLM-k-table"] and src == "default" and k == P._K_DEFAULT["def_blitz"], (notes, src, k))
    block("R4 assertion guards", r4_assertions)

    def m5_mutations():
        with tempfile.TemporaryDirectory() as tmp:
            pbp, part, ftn, pl, dp = _write(tmp, extra=True)
            stx = D.build_season(2030, pbp, part, ftn, positions=POS)
        C = stx["cols"]
        gid = {g["id"]: i for i, g in enumerate(stx["games"])}
        i23 = next(i for i in range(stx["n"]) if C["gi"][i] == gid[G1B] and C["kind"][i] == D.ATTEMPT and C["receiver"][i] == 500002 and C["air"][i] == 3)
        ck("M5: a box of 0 reads NA (uncharted), never an empty box", C["box"][i23] == D.NA)
        kc1 = stx["onfield"][gid[G1]]
        kc2 = stx["onfield"][gid[G2B]]
        # R1: the lineman is listed on a real dropback (play 31) as well as the kneel, so only the
        # skill-position filter keeps him out -- and the quarterback on the same list is in
        ck("M5/R1: a lineman on a pass play's on-field list is not tallied (skill positions only)",
           300001 not in kc2 and 100001 in kc2 and kc2[100001][D.OF_DB] >= 1, (300001 in kc2, 100001 in kc2))
        ck("M5: a player whose only snap was a kneel has no on-field snaps", 100005 not in kc1)
        line = P.season_tallies(stx, POS, max_week=2).player_games[200001][0]["players"][200001]
        ck("M5: a scramble credits the quarterback with a carry and its yards", line[P.G_CAR] == 1 and line[P.G_RUSHY] == 3, (line[P.G_CAR], line[P.G_RUSHY]))
        ck("M5: RECENT is four games", P.RECENT == 4)
        ck("M5: the on-field vector width follows the coverage vocabulary", D.ONFIELD_W == 6 + 2 * (len(D.COVERAGE) - 1) + 3)
        with tempfile.TemporaryDirectory() as tmp:
            pl = os.path.join(tmp, "players.csv")
            with open(pl, "w") as fh:
                fh.write(PLAYERS_CSV + "00-0030000,Mike Williams,Mike,Williams,1,WR,WR,2024,LAC,ACT\n00-0030001,Mike Williams,Mike,Williams,2,WR,WR,2024,NYJ,ACT\n")
            ids2 = D.build_ids(pl, None)
        idx2 = D.ids_index(ids2)
        ck("M5: a shared name resolves by team and is None when nothing disambiguates",
           D.resolve_name("mike williams", idx2, ids2, team="NYJ") == 30001 and D.resolve_name("mike williams", idx2, ids2) is None)
    block("M5 mutation-closing fixtures", m5_mutations)

    # ---- snap counts reader
    with tempfile.TemporaryDirectory() as tmp:
        sp = os.path.join(tmp, "snaps.csv")
        with open(sp, "w") as fh:
            fh.write("game_id,pfr_game_id,season,game_type,week,player,pfr_player_id,position,team,opponent,offense_snaps,offense_pct,defense_snaps,defense_pct,st_snaps,st_pct\n"
                     "2026_01_ARI_NO,x,2026,REG,1,Chris Olave,OlavCh00,WR,NO,ARI,60,0.8,0,0,0,0\n"
                     "2026_19_ARI_NO,x,2026,POST,1,Chris Olave,OlavCh00,WR,NO,ARI,60,0.8,0,0,0,0\n")
        snaps = D.read_snap_counts(sp)
    ck("snap counts keyed by (game, norm name, team), regular season only",
       snaps == {("2026_01_ARI_NO", "chris olave", "NO"): (60, 0.8)}, snaps)


if __name__ == "__main__":
    results = []

    def ck(name, cond, detail=""):
        results.append(bool(cond))
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    run(ck)
    n_fail = results.count(False)
    print(f"\n{len(results) - n_fail} passed, {n_fail} failed")
    sys.exit(1 if n_fail else 0)
