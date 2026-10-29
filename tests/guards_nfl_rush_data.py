"""Guards for the rushing matchup data layer (nfl_rush_data, nfl_rush_profiles,
research/nfl_rush_dataset's builder and measurement). Hermetic: a synthetic
six-week season written as the nflverse files the code reads (pbp,
participation, FTN, PFR rush / def / pass, NGS, injuries, snap counts), built
through the AGREED build_season and this layer's build_aux, read through the
real profiles and the real dataset builder. No network; the deep store is
pointed at a temp dir for the refresh checks. A few seconds.

    python3 tests/guards_nfl_rush_data.py      # exits 1 on any failure
    from tests.guards_nfl_rush_data import run  # run(ck) inside the suite

Every section runs on its own: one that raises reports "<section>: runs to the
end" as a failure and the next section still runs.

Pinned by definition (each with the hand-computed figure that would move if
the definition did): sacks are never rushes (even with a rusher id on the
row); kneels and aborted snaps ARE official rushes and never designed runs;
an RPO that became a pass is not a rush, an RPO keep is; scrambles are the
scrambler's dropbacks; the designed + sneak + scramble + kneel + aborted
partition; the sneak rule only where FTN has no row; pressure / coverage
scramble splits only from 2023; a traded back's usage team-scoped like the
agreed F1 fix; the as-of cutoff (the snapshot refuses the week, the builder
raises on it, a game's rows do not move when the week's other game goes);
k applied == k measured (fixture table, shipped table == code defaults, every
stamped metric measured, the full path -> metric map); the line-yards
convention; the defense's leave-one-out expected (two seasons, each game at
its own weight); the pbp-extras alignment refusing a mismatched row; NGS
season rows dropped; the injury report and the OL starters.

The data-layer review's fixes (research/reviews/nfl_rush_data_*.md), each
guarded here: B1 RB units are RB/FB only (a WR with carries and 15+ targets a
half never becomes one; his carries still count for the team and the
defense); B2 the team's snaps are the most any player took (a 1-snap 1%
back never makes it 100); M1 a week with no injury report reads "unknown",
never "active", ledgered once; M3 every profile stamps its aux status, one
season's bad file never stops the refresh, a short block is listed; M4 the
schema's kinds read the field definitions; m1 share priors clamped to [0, 1];
m2 act_any_snap (special teams count); m3 the dataset's k frames. Plus the
mutation survivors both reviewers found: team scoping (recent, snap share,
the QB team filter, the builder's rb(pid, team)), the defense and team
context read off the right side, the split-prior offset sign, the
multiplicative scramble-split prior, the two-season leave-one-out weight,
the reliability stamp, the nickel / lead / trail / short-yardage / status
thresholds, scramble yards and TDs, two-QB exposure, laterals, and the
measurement's unit gate, k formula and split. Round 3 (the re-checks): a
failed download leaves an aux that says it is incomplete, stamps
"incomplete" and is retried; a season with no pbp file saves nothing; a team
the week's report lists nobody from reads "unknown"; K_FRAMES pinned; the
QB-rate k inside the hit + sack OE pinned; every column's source text names
each part of its source tag.
"""
import copy
import csv
import gzip
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile

os.environ["VIGIL_NO_BG"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import nfl_matchup_data as D            # noqa: E402
import nfl_rush_data as R               # noqa: E402
import nfl_rush_profiles as RP          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SHIPPED_K = os.path.join(HERE, "..", "research", "data", "nfl_rush", "reliability.json")

# ---- the synthetic season --------------------------------------------------------------------
PBP_COLS = ("play_id", "game_id", "home_team", "away_team", "season_type", "week", "posteam", "defteam", "yardline_100",
            "game_date", "game_seconds_remaining", "qtr", "down", "ydstogo", "play_type", "yards_gained", "shotgun", "no_huddle",
            "qb_dropback", "qb_kneel", "qb_spike", "qb_scramble", "pass_length", "pass_location", "air_yards", "yards_after_catch",
            "run_location", "run_gap", "score_differential", "epa", "wp", "incomplete_pass", "interception", "fumble_lost", "sack",
            "touchdown", "pass_touchdown", "rush_touchdown", "two_point_attempt", "complete_pass", "passer_player_id",
            "receiver_player_id", "rusher_player_id", "fumbled_1_player_id", "cpoe", "success", "first_down", "goal_to_go",
            "qb_hit", "rush_attempt", "pass_attempt", "home_score", "away_score", "start_time", "roof", "spread_line", "total_line",
            "xpass", "play_deleted", "aborted_play", "rushing_yards")
G1, G1B = "2030_01_KC_BUF", "2030_01_DEN_MIA"
G2, G2B = "2030_02_BUF_KC", "2030_02_MIA_DEN"
G3, G3B = "2030_03_BUF_DEN", "2030_03_KC_MIA"
G4 = "2030_04_KC_DEN"
G5 = "2030_05_KC_BUF"
G6 = "2030_06_KC_MIA"
DATES = {1: "2030-09-08", 2: "2030-09-15", 3: "2030-09-22", 4: "2030-09-29", 5: "2030-10-06", 6: "2030-10-13"}
# KC: QB 100001 (backup 100007), WR 100002, TE 100003, RB 100004 (to DEN after week 1), RB 100006, RB 100009 (one
# snap), RB 100010 (special teams only); BUF: QB 200001, WR 200002, RB 200004; DEN: QB 400001, RB 400004, C 400011;
# MIA: QB 500001, RB 500004. KC linemen 300001-300006 (PFR ids KCOL1-6).
POS = {100001: "QB", 100002: "WR", 100003: "TE", 100004: "RB", 100006: "RB", 100007: "QB", 100009: "RB", 100010: "RB", 100011: "RB",
       200001: "QB", 200002: "WR", 200004: "RB", 400001: "QB", 400004: "RB", 500001: "QB", 500004: "RB"}
PFR = {"QB1": 100001, "RB4": 100004, "RB6": 100006, "RB9": 100009, "RB10": 100010, "QB7": 100007, "QB2": 200001,
       "RB24": 200004, "QB4": 400001, "RB44": 400004, "DENOL1": 400011, **{f"KCOL{i}": 300000 + i for i in range(1, 7)}}


def _g(pid):
    return "00-%07d" % pid if pid else ""


def _game(gid):
    wk = int(gid[5:7])
    away, home = gid[8:].split("_")
    return {"game_id": gid, "week": str(wk), "game_date": DATES[wk], "home_team": home, "away_team": away}


def _play(pid, gid, off, dfn, **kw):
    row = {c: "" for c in PBP_COLS}
    row.update({"season_type": "REG", "qtr": "1", "down": "1", "ydstogo": "10", "yardline_100": "60",
                "game_seconds_remaining": "3000", "score_differential": "0", "wp": "0.5", "epa": "0", "shotgun": "0",
                "no_huddle": "0", "qb_dropback": "0", "qb_kneel": "0", "qb_spike": "0", "qb_scramble": "0", "incomplete_pass": "0",
                "interception": "0", "fumble_lost": "0", "sack": "0", "touchdown": "0", "pass_touchdown": "0", "rush_touchdown": "0",
                "two_point_attempt": "0", "complete_pass": "0", "success": "0", "first_down": "0", "goal_to_go": "0", "qb_hit": "0",
                "rush_attempt": "0", "pass_attempt": "0", "play_deleted": "0", "xpass": "0.5", "home_score": "20", "away_score": "17",
                "aborted_play": "0", "play_id": str(pid), "posteam": off, "defteam": dfn})
    row.update(_game(gid))
    row.update(kw)
    return row


def _run(pid, gid, off, dfn, rusher, yds, loc="left", gap="end", **kw):
    base = dict(play_type="run", rush_attempt="1", rusher_player_id=_g(rusher), yards_gained=str(yds), rushing_yards=str(yds),
                run_location=loc, run_gap=gap if loc != "middle" else "", epa="0.3" if yds >= 4 else "-0.4",
                success="1" if yds >= 4 else "0")
    base.update(kw)
    return _play(pid, gid, off, dfn, **base)


def _pass(pid, gid, off, dfn, passer, receiver, air, yds, comp, **kw):
    base = dict(play_type="pass", qb_dropback="1", pass_attempt="1", passer_player_id=_g(passer), receiver_player_id=_g(receiver),
                air_yards=str(air), pass_location="right", pass_length="short", yards_gained=str(yds if comp else 0),
                complete_pass="1" if comp else "0", incomplete_pass="0" if comp else "1",
                yards_after_catch=str(max(0, yds - air)) if comp else "", epa="0.8" if comp else "-0.5",
                success="1" if comp else "0", cpoe="5")
    base.update(kw)
    return _play(pid, gid, off, dfn, **base)


def _scr(pid, gid, off, dfn, qb, yds, **kw):
    base = dict(play_type="run", qb_scramble="1", qb_dropback="1", rush_attempt="1", rusher_player_id=_g(qb),
                yards_gained=str(yds), rushing_yards=str(yds), run_location="right", run_gap="end", epa="0.6", success="1")
    base.update(kw)
    return _play(pid, gid, off, dfn, **base)


PLAYS = [
    # ---- week 1, KC @ BUF: KC offence
    _run(1, G1, "KC", "BUF", 100004, 6, "left", "end"),
    _run(2, G1, "KC", "BUF", 100004, -2, "middle", "", down="2"),
    _run(3, G1, "KC", "BUF", 100004, 12, "right", "tackle", shotgun="1", first_down="1"),
    _pass(4, G1, "KC", "BUF", 100001, 100004, 1, 8, 1, qb_hit="1"),                     # hit as he threw: a hit, not a sack
    # a sack with a rusher id on the row (a data quirk): still a sack, never a rush
    _play(5, G1, "KC", "BUF", play_type="pass", qb_dropback="1", sack="1", yards_gained="-7", passer_player_id=_g(100001),
          rusher_player_id=_g(100001), rushing_yards="-7", epa="-1.6"),
    _play(6, G1, "KC", "BUF", play_type="qb_kneel", qb_kneel="1", rush_attempt="1", rusher_player_id=_g(100001),
          yards_gained="-1", rushing_yards="-1", qtr="4", game_seconds_remaining="30"),
    _run(7, G1, "KC", "BUF", 100001, 5, "right", "end", shotgun="1"),                     # an RPO keep (FTN)
    _pass(8, G1, "KC", "BUF", 100001, 100002, 9, 0, 0),                                   # an RPO that became a pass (FTN)
    _run(9, G1, "KC", "BUF", 100001, 1, "middle", "", down="3", ydstogo="1", first_down="1"),   # a sneak (FTN)
    _run(10, G1, "KC", "BUF", 100001, -3, "", "", aborted_play="1"),                      # an aborted snap
    _run(11, G1, "KC", "BUF", 100006, 3, "left", "guard", yardline_100="15"),
    # a deleted play and a two-point try: build_season drops both, so the extras' row filter must too
    _run(15, G1, "KC", "BUF", 100004, 50, "left", "end", play_deleted="1"),
    _pass(16, G1, "KC", "BUF", 100001, 100002, 2, 2, 1, two_point_attempt="1", down=""),
    # BUF offence
    _scr(12, G1, "BUF", "KC", 200001, 9),
    _pass(13, G1, "BUF", "KC", 200001, 200002, 6, 10, 1),
    _run(14, G1, "BUF", "KC", 200004, 4, "left", "tackle"),
    # ---- week 1, DEN @ MIA
    _scr(21, G1B, "DEN", "MIA", 400001, 3),
    _run(22, G1B, "DEN", "MIA", 400004, 5, "right", "guard"),
    _pass(23, G1B, "MIA", "DEN", 500001, 500004, 0, 6, 1),
    _run(24, G1B, "MIA", "DEN", 500004, 2, "middle", ""),
    # ---- week 2, BUF @ KC
    _run(31, G2, "KC", "BUF", 100006, 4, "left", "end"),
    _pass(32, G2, "BUF", "KC", 200001, 200002, 12, 0, 0),
    _pass(33, G2, "BUF", "KC", 200001, 200004, 0, 4, 1),
    # ---- week 2, MIA @ DEN: the KC back now carries for DEN
    _run(41, G2B, "DEN", "MIA", 100004, 7, "left", "end"),
    _run(42, G2B, "DEN", "MIA", 400004, 1, "middle", ""),
    _run(45, G2B, "DEN", "MIA", 400004, 2, "right", "guard"),
    _pass(43, G2B, "DEN", "MIA", 400001, 100004, 1, 5, 1),
    _pass(44, G2B, "MIA", "DEN", 500001, 500004, 2, 0, 0),
    # ---- week 3, BUF @ DEN and KC @ MIA
    _run(51, G3, "DEN", "BUF", 100004, 3, "right", "tackle"),
    _scr(52, G3, "BUF", "DEN", 200001, 6),
    _scr(53, G3, "BUF", "DEN", 200001, 11, touchdown="1", rush_touchdown="1"),          # a scramble TD
    _pass(54, G3, "DEN", "BUF", 400001, 400004, 0, 3, 1),
    _run(55, G3, "DEN", "BUF", 400004, 2, "left", "tackle"),
    _run(61, G3B, "KC", "MIA", 100006, 2, "left", "end"),
    _pass(62, G3B, "KC", "MIA", 100001, 100002, 8, 12, 1),
    _run(63, G3B, "MIA", "KC", 500004, 4, "right", "end"),
    # ---- week 4, KC @ DEN (read as of week 5): short yardage, a lateral, two quarterbacks, leading and trailing
    _run(71, G4, "KC", "DEN", 100006, 3, "left", "end"),
    _run(72, G4, "DEN", "KC", 400004, 2, "middle", ""),
    _run(73, G4, "KC", "DEN", 100001, 4, "right", "end"),                                 # a QB designed run, not an RPO
    _run(74, G4, "KC", "DEN", 100006, 3, "left", "tackle", down="3", ydstogo="2", first_down="1"),   # 3rd & 2: short yardage
    _run(75, G4, "KC", "DEN", 100006, 4, "left", "tackle", down="3", ydstogo="3", first_down="1"),   # 3rd & 3: is not
    _run(76, G4, "KC", "DEN", 100006, 10, "left", "tackle", rushing_yards="6"),           # a lateral: 6 of the 10 are his
    _pass(77, G4, "KC", "DEN", 100001, 100002, 5, 9, 1),
    _pass(78, G4, "KC", "DEN", 100001, 100002, 5, 0, 0),
    _pass(79, G4, "KC", "DEN", 100007, 100002, 5, 0, 0),                                  # the backup's one dropback
    _run(80, G4, "KC", "DEN", 100006, 2, "left", "end", score_differential="10"),         # leading by 10
    _pass(81, G4, "KC", "DEN", 100001, 100002, 5, 0, 0, score_differential="-10"),        # trailing by 10
    # ---- week 5, KC @ BUF: a WR jet sweep and a TE carry (team and defense totals, never RB units)
    _run(91, G5, "KC", "BUF", 100006, 2, "left", "end"),
    _run(92, G5, "BUF", "KC", 200004, 3, "right", "end"),
    _run(93, G5, "KC", "BUF", 100002, 5, "left", "end"),
    _run(94, G5, "KC", "BUF", 100003, 1, "middle", ""),
    *[_pass(101 + i, G5, "KC", "BUF", 100001, 100002, 6, 8, 1) for i in range(15)],
    # ---- week 6, KC @ MIA: the WR's even-week half (15 more targets, another carry)
    _run(136, G6, "KC", "MIA", 100002, 4, "right", "end"),
    *[_pass(121 + i, G6, "KC", "MIA", 100001, 100002, 6, 8, 1) for i in range(15)],
]

PART_COLS = ("nflverse_game_id", "old_game_id", "play_id", "possession_team", "offense_formation", "offense_personnel",
             "defenders_in_box", "defense_personnel", "number_of_pass_rushers", "players_on_play", "offense_players",
             "defense_players", "n_offense", "n_defense", "ngs_air_yards", "time_to_throw", "was_pressure", "route",
             "defense_man_zone_type", "defense_coverage_type")
NICKEL = "3 CB, 2 DE, 2 DT, 1 FS, 1 MLB, 1 OLB, 1 SS"          # 4 DL, 2 LB, 5 DB
BASE34 = "2 CB, 2 DE, 1 FS, 2 ILB, 1 NT, 2 OLB, 1 SS"          # 3 DL, 4 LB, 4 DB
P11 = "1 C, 2 G, 1 QB, 1 RB, 2 T, 1 TE, 3 WR"
P12 = "1 C, 2 G, 1 QB, 1 RB, 2 T, 2 TE, 2 WR"


def _on(*pids):
    return ";".join(_g(p) for p in pids)


def _part(gid, pid, off, onfield, box=6, dpers=NICKEL, opers=P11, rushers=4, press="", mz="", cov=""):
    return dict(zip(PART_COLS, (gid, "", str(pid), off, "SHOTGUN", opers, str(box), dpers, str(rushers), "", onfield, "", "11", "11",
                                "", "", press, "", mz, cov)))


KC4 = _on(100001, 100002, 100004)
KC6 = _on(100001, 100002, 100006)
PART = [
    _part(G1, 1, "KC", KC4, box=6, dpers=NICKEL), _part(G1, 2, "KC", KC4, box=8, dpers=BASE34, opers=P12),
    _part(G1, 3, "KC", KC4, box=7, dpers=NICKEL), _part(G1, 4, "KC", KC4, press="FALSE", mz="ZONE_COVERAGE", cov="COVER_3"),
    _part(G1, 5, "KC", KC4, press="TRUE", mz="MAN_COVERAGE", cov="COVER_1"), _part(G1, 6, "KC", KC4),
    _part(G1, 7, "KC", KC6, box=6), _part(G1, 8, "KC", KC4, press="FALSE", mz="ZONE_COVERAGE", cov="COVER_2"),
    _part(G1, 9, "KC", KC6, box=8, dpers=BASE34, opers=P12), _part(G1, 10, "KC", KC4),
    _part(G1, 11, "KC", KC6, box=7),
    _part(G1, 12, "BUF", _on(200001, 200002, 200004), rushers=5, press="TRUE", mz="MAN_COVERAGE", cov="COVER_1"),
    _part(G1, 13, "BUF", _on(200001, 200002, 200004), press="FALSE", mz="ZONE_COVERAGE", cov="COVER_3"),
    _part(G1, 14, "BUF", _on(200001, 200002, 200004), box=7),
    _part(G1B, 21, "DEN", _on(400001, 400004), press="FALSE", mz="ZONE_COVERAGE", cov="COVER_3"),
    _part(G1B, 22, "DEN", _on(400001, 400004)), _part(G1B, 23, "MIA", _on(500001, 500004), press="FALSE", mz="ZONE_COVERAGE", cov="COVER_3"),
    _part(G1B, 24, "MIA", _on(500001, 500004)),
    _part(G2, 31, "KC", KC6), _part(G2, 32, "BUF", _on(200001, 200002, 200004), rushers=5, press="TRUE", mz="MAN_COVERAGE", cov="COVER_1"),
    _part(G2, 33, "BUF", _on(200001, 200002, 200004), press="FALSE", mz="ZONE_COVERAGE", cov="COVER_2"),
    _part(G2B, 41, "DEN", _on(400001, 400004, 100004)), _part(G2B, 42, "DEN", _on(400001, 400004, 100004)),
    _part(G2B, 45, "DEN", _on(400001, 400004)),
    _part(G2B, 43, "DEN", _on(400001, 400004, 100004), press="FALSE", mz="MAN_COVERAGE", cov="COVER_1"),
    _part(G2B, 44, "MIA", _on(500001, 500004), press="FALSE", mz="ZONE_COVERAGE", cov="COVER_3"),
    _part(G3, 51, "DEN", _on(400001, 100004)),
    _part(G3, 52, "BUF", _on(200001, 200002), press="FALSE", mz="ZONE_COVERAGE", cov="COVER_3"),
    _part(G3, 53, "BUF", _on(200001, 200002), rushers=5, press="TRUE", mz="MAN_COVERAGE", cov="COVER_1"),
    _part(G3, 54, "DEN", _on(400001, 400004, 100004), press="FALSE", mz="ZONE_COVERAGE", cov="COVER_3"),
    _part(G3, 55, "DEN", _on(400001, 400004)),
    _part(G3B, 61, "KC", KC6), _part(G3B, 62, "KC", KC6, press="FALSE", mz="ZONE_COVERAGE", cov="COVER_3"),
    _part(G3B, 63, "MIA", _on(500001, 500004)),
    _part(G4, 71, "KC", KC6), _part(G4, 72, "DEN", _on(400001, 400004)),
    *[_part(G4, p, "KC", KC6) for p in (73, 74, 75, 76, 77, 78, 80, 81)],
    _part(G4, 79, "KC", _on(100007, 100002, 100006)),
]
FTN_COLS = ("ftn_game_id", "nflverse_game_id", "season", "week", "ftn_play_id", "nflverse_play_id", "starting_hash", "qb_location",
            "n_offense_backfield", "n_defense_box", "is_no_huddle", "is_motion", "is_play_action", "is_screen_pass", "is_rpo",
            "is_trick_play", "is_qb_out_of_pocket", "is_interception_worthy", "is_throw_away", "read_thrown", "is_catchable_ball",
            "is_contested_ball", "is_created_reception", "is_drop", "is_qb_sneak", "n_blitzers", "n_pass_rushers", "is_qb_fault_sack",
            "date_pulled")


def _ftn(gid, pid, box=6, rpo="FALSE", sneak="FALSE", screen="FALSE", read="", rushers=4):
    return dict(zip(FTN_COLS, ("1", gid, "2030", gid[5:7], str(pid), str(pid), "M", "S", "1", str(box), "FALSE", "FALSE", "FALSE",
                               screen, rpo, "FALSE", "FALSE", "FALSE", "FALSE", read, "TRUE", "FALSE", "FALSE", "FALSE", sneak,
                               str(max(0, rushers - 4)), str(rushers), "FALSE", "")))


FTN = [_ftn(p["game_id"], int(p["play_id"])) for p in PLAYS if p["play_id"] not in ("4", "7", "8", "9", "12", "32", "53")]
FTN += [_ftn(G1, 4, screen="TRUE", read="CHK"), _ftn(G1, 7, rpo="TRUE"), _ftn(G1, 8, rpo="TRUE"), _ftn(G1, 9, box=8, sneak="TRUE"),
        _ftn(G1, 12, rushers=5), _ftn(G2, 32, rushers=5), _ftn(G3, 53, rushers=5)]

PFR_RUSH_COLS = ("game_id", "pfr_game_id", "season", "week", "game_type", "team", "opponent", "pfr_player_name", "pfr_player_id",
                 "carries", "rushing_yards_before_contact", "rushing_yards_before_contact_avg", "rushing_yards_after_contact",
                 "rushing_yards_after_contact_avg", "rushing_broken_tackles", "receiving_broken_tackles")
PFR_RUSH = [(G1, "KC", "RB4", 3, 6, 10, 1, 0), (G1, "KC", "QB1", 4, 0, 2, 0, 0), (G1, "KC", "RB6", 1, 2, 1, 0, 0),
            (G2B, "DEN", "RB4", 1, 2, 5, 0, 0), (G3, "DEN", "RB4", 1, 1, 2, 1, 0), (G1, "KC", "ZZZ", 1, 0, 0, 0, 0)]
PFR_DEF_COLS = ("game_id", "season", "week", "game_type", "team", "opponent", "pfr_player_name", "pfr_player_id", "def_tackles_combined",
                "def_missed_tackles", "def_pressures", "def_times_hurried", "def_times_hitqb", "def_sacks", "def_times_blitzed")
PFR_DEF = [(G1, "BUF", 5, 1, 1, 1, 0, 1, 0), (G1, "BUF", 3, 2, 0, 0, 0, 0, 1), (G1, "KC", 4, 0, 1, 1, 0, 0, 0)]
PFR_PASS_COLS = ("game_id", "season", "week", "game_type", "team", "opponent", "pfr_player_name", "pfr_player_id", "times_pressured",
                 "times_hurried", "times_hit", "times_blitzed", "times_sacked")
NGS_COLS = ("season", "season_type", "week", "player_display_name", "player_position", "team_abbr", "efficiency",
            "percent_attempts_gte_eight_defenders", "avg_time_to_los", "rush_attempts", "rush_yards", "avg_rush_yards", "rush_touchdowns",
            "player_gsis_id", "expected_rush_yards", "rush_yards_over_expected")
NGS = [("2030", "REG", "0", "Kc Back", "RB", "KC", "4.0", "10.0", "2.9", "40", "200", "5", "1", _g(100004), "150", "50"),
       ("2030", "REG", "1", "Kc Back", "RB", "KC", "3.5", "33.33", "2.5", "3", "16", "5.3", "0", _g(100004), "10", "6"),
       ("2030", "REG", "2", "Kc Back", "RB", "KC", "3.5", "0", "2.5", "1", "7", "7", "0", _g(100004), "4", "3"),   # wrong team: DEN
       ("2030", "POST", "1", "Kc Back", "RB", "KC", "3.5", "0", "2.5", "9", "9", "1", "0", _g(100004), "9", "0")]
INJ_COLS = ("season", "season_type", "game_type", "team", "week", "gsis_id", "position", "full_name", "report_status", "practice_status")
INJ = [("3", "KC", 300002, "G", "Out", "Did Not Participate In Practice"), ("3", "KC", 100006, "RB", "Questionable",
                                                                                "Limited Participation in Practice"),
       ("4", "KC", 300003, "G", "Out", "Did Not Participate In Practice"), ("4", "KC", 100006, "RB", "Out", ""),
       ("2", "DEN", 400004, "RB", "Doubtful", "Did Not Participate In Practice"),
       # week 5: a Doubtful starter counts as out, a Questionable one does not. No week 1 or week 6 report.
       ("5", "KC", 300001, "T", "Doubtful", "Did Not Participate In Practice"),
       ("5", "KC", 300002, "G", "Questionable", "Limited Participation in Practice"),
       ("5", "KC", 300006, "T", "Out", "Did Not Participate In Practice")]     # a lineman out of the as-of five
SNAP_COLS = ("game_id", "pfr_game_id", "season", "game_type", "week", "player", "pfr_player_id", "position", "team", "opponent",
             "offense_snaps", "offense_pct", "defense_snaps", "defense_pct", "st_snaps", "st_pct")
# KC's line by game (PFR id KCOL<i> -> snaps). OL5 starts G1 and G4, OL6 G2, G3B and G5 on fewer snaps: as of week 4
# the five are OL1-4 + OL6 (65 > 60); as of week 5 OL1-5 over the last FOUR games (110 > 65); as of week 6 the last
# four (G2, G3B, G4, G5) put OL6 back (105 > 50) where a five-game window would keep OL5 (110 > 105). OL4 plays 20 of
# G4's 50 snaps: under half -- he "missed" the game.
KC_OL = ((G1, {1: 60, 2: 60, 3: 60, 4: 60, 5: 60}), (G2, {1: 58, 2: 58, 3: 58, 4: 58, 6: 30}),
         (G3B, {1: 61, 2: 61, 3: 61, 4: 61, 6: 35}), (G4, {1: 50, 2: 50, 3: 50, 4: 20, 5: 50}),
         (G5, {1: 50, 2: 50, 3: 50, 4: 50, 6: 40}))


def _snaps():
    rows = []

    def add(gid, team, name, pfr, pos, off_n, off_pct, st_n=0):
        rows.append((gid, "", "2030", "REG", gid[5:7], name, pfr, pos, team, "", str(off_n), str(off_pct), "0", "0", str(st_n), "0"))
    for gid, ol in KC_OL:
        team_n = max(ol.values())
        for i, n in ol.items():
            add(gid, "KC", f"Kc Line{i}", f"KCOL{i}", "G" if i in (2, 3) else "T", n, round(n / team_n, 4))
    add(G1, "KC", "Kc Back", "RB4", "RB", 45, 0.75)
    add(G1, "KC", "Kc Back2", "RB6", "RB", 15, 0.25)
    add(G1, "KC", "Kc Back9", "RB9", "RB", 1, 0.01)          # the review's B2: 1 snap at 1% read the team total as 100
    add(G1, "KC", "Kc Back10", "RB10", "RB", 0, 0.0, 5)      # special teams only (m2)
    add(G2B, "DEN", "Den Center", "DENOL1", "C", 40, 1.0)
    add(G2B, "DEN", "Kc Back", "RB4", "RB", 20, 0.5)
    add(G3, "DEN", "Kc Back", "RB4", "RB", 30, 0.6)          # no player at 99.5%+: the total falls back to 30 / 0.6 = 50
    return rows


def _write(d, name, cols, rows, gz=False):
    p = os.path.join(d, name)
    op = gzip.open if gz else open
    with op(p, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols] if isinstance(r, dict) else list(r))
    return p


def _pfr_rush_rows(skip=()):
    return ([(g, "", "2030", g[5:7], "REG", t, "", "Nobody" if p == "ZZZ" else "x", p, c, ybc, "", yac, "", btk, rb)
             for g, t, p, c, ybc, yac, btk, rb in PFR_RUSH if g not in skip]
            + [(G1, "", "2030", "1", "WC", "KC", "", "x", "RB4", 30, 30, "", 30, "", 9, 0)])


def _files(d, plays=PLAYS):
    paths = {"pbp": _write(d, "pbp.csv", PBP_COLS, plays), "participation": _write(d, "part.csv", PART_COLS, PART),
             "ftn": _write(d, "ftn.csv", FTN_COLS, FTN)}
    paths["pfr_rush"] = _write(d, "pfr_rush.csv", PFR_RUSH_COLS, _pfr_rush_rows())
    paths["pfr_def"] = _write(d, "pfr_def.csv", PFR_DEF_COLS, [(g, "2030", g[5:7], "REG", t, "", "d", "d", *v) for g, t, *v in PFR_DEF])
    paths["pfr_pass"] = _write(d, "pfr_pass.csv", PFR_PASS_COLS, [(G1, "2030", "1", "REG", "KC", "BUF", "q", "QB1", 1, 1, 0, 1, 1)])
    paths["injuries"] = _write(d, "inj.csv", INJ_COLS, [("2030", "REG", "REG", t, w, _g(g), p, "x", s, pr) for w, t, g, p, s, pr in INJ])
    paths["snaps"] = _write(d, "snaps.csv", SNAP_COLS, _snaps())
    ngs = _write(d, "ngs.csv.gz", NGS_COLS, NGS, gz=True)
    return paths, ngs


IDS = {pid: {"name": f"P{pid}", "norm": f"p{pid}", "pos": p, "team": "", "last": 2030, "sleeper": "", "espn": ""} for pid, p in POS.items()}
IDS[100010]["sleeper"] = "SL10"
# Sleeper projects the special-teams-only back to carry once in week 1: his row exists without an offensive snap
SLEEPER = {(2030, 1, "SL10"): {"team": "KC", "proj": {"rush_att": 1.0}, "act": None, "role": "", "name": "x", "pos": "RB"}}


def _build(d, season=2030, plays=PLAYS, with_ftn=True, with_extras=True):
    paths, ngs_path = _files(d, plays)
    store = D.build_season(season, paths["pbp"], paths["participation"], paths["ftn"] if with_ftn else None, positions=POS)
    ngs = R.read_ngs_rush(ngs_path, [2030], IDS, {})
    if season != 2030:
        ngs = {season: ngs.get(2030, {})}
    p2 = dict(paths)
    if not with_extras:
        p2.pop("pbp")
    aux = R.build_aux(season, store, p2, PFR, IDS, ngs)
    return store, aux, paths


def _snap(store, aux, week):
    T = RP.season_tallies(store, aux, POS, max_week=week)
    return RP.RushSnapshot(store["season"], week, T, [], aux={store["season"]: aux}), T


def _close(a, b, tol=1e-3):
    return a is not None and b is not None and abs(a - b) <= tol


def _rec(T, team, gid, side="off"):
    return [r for sd, r in T.team_games[team] if sd == side and r["id"] == gid][0]


class _Notes:
    """errlog.note captured (and kept out of the real ledger) for the whole run."""

    def __enter__(self):
        import errlog
        self.mod, self.saved, self.codes = errlog, errlog.note, []
        errlog.note = lambda code, exc=None, msg="", path=None: self.codes.append(code)
        return self

    def __exit__(self, *a):
        self.mod.note = self.saved


class _X:
    """What the sections share."""


def run(ck):
    with tempfile.TemporaryDirectory() as d:
        RP.reload_k(_fixture_k(d))
        try:
            with _Notes() as notes:
                _run_all(ck, d, notes)
        finally:
            RP.reload_k()


def _fixture_k(d, name="k.json", **over):
    """A k table: every shipped metric at a fixture value, a few pinned, one
    measured unsupported (None)."""
    k = {m: 10.0 for m in RP._K_DEFAULT}
    k.update({"qb_scr_rate": 0.0, "rb_ypc": 50.0, "def_scrambles_oe": 4.0, "rb_dir_ypc_dev": None, "def_rb_ypc": 30.0})
    k.update(over)
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"k": k, "source": "fixture"}, fh)
    return p


def _run_all(ck, d, notes):
    X = _X()
    X.d, X.notes = d, notes
    X.store, X.aux, X.paths = _build(d)
    import research.nfl_rush_dataset as M
    X.M = M
    saved_log = M.log
    M.log = lambda msg: None
    sections = (("aux store", _s_aux), ("snap counts (B2)", _s_snaps), ("definitions", _s_defs), ("bookkeeping", _s_book),
                ("eras and missing files", _s_eras), ("as-of cutoff", _s_cutoff), ("traded back", _s_traded),
                ("k applied == k measured", _s_k), ("path -> metric map", _s_metric_map), ("shrinkage priors", _s_priors),
                ("QB and the defense's OE", _s_qbdef), ("two-season leave-one-out", _s_loo), ("OL and the injury report", _s_ol),
                ("no injury report (M1)", _s_noreport), ("week 4 counting", _s_week4), ("RB units RB/FB only (B1)", _s_units),
                ("measurement code", _s_measure), ("aux status (M3)", _s_auxstat), ("coverage (M3)", _s_coverage),
                ("share clamp (m1)", _s_clamp), ("source tags (F14a)", _s_sources), ("hit + sack proxy (F14b)", _s_hitsack),
                ("OL listed out (F14c)", _s_ol_listed), ("PFR snap lines move no unrestricted field (M8)", _s_pfr_invariance), ("dataset builder", _s_builder), ("refresh", _s_refresh))
    try:
        for name, fn in sections:
            try:
                fn(ck, X)
            except Exception as e:      # a section that raises is a failure; the next section still runs
                ck(f"{name}: runs to the end", False, f"{type(e).__name__}: {e}")
    finally:
        M.log = saved_log


# ---- the auxiliary store ---------------------------------------------------------------------------
def _s_aux(ck, X):
    store, aux = X.store, X.aux
    C = store["cols"]
    n_kept = store["n"]
    ex = aux["extras"]
    st = aux["joins"]["pbp_extras"]
    ck("extras: every store row aligned (same filter, same order)", st["rows_aligned"] == n_kept and st["rows_mismatch"] == 0,
       str(st))
    i10 = [i for i in range(n_kept) if ex["play_id"][i] == 10][0]
    ck("extras: the aborted snap is flagged, nothing else is", ex["aborted"][i10] == 1 and sum(1 for x in ex["aborted"] if x == 1) == 1)
    ck("aux_matches: the aux was built on this store", R.aux_matches(aux, store))
    other_n = dict(aux, store_n=store["n"] + 1)
    ck("aux_matches: the same build time with another row count is another store", not R.aux_matches(other_n, store))
    bad = copy.deepcopy(PLAYS)
    bad[1]["yards_gained"] = "-5"                       # the file says -5 where the store says -2
    _ex, st2 = R.align_extras(store, R.pbp_kept(_write(X.d, "bad.csv", PBP_COLS, bad)))
    i2 = [i for i in range(n_kept) if C["yds"][i] == -2][0]
    ck("extras: a row the store and the file disagree on is left NA, not guessed",
       st2["rows_mismatch"] == 1 and _ex["aborted"][i2] == D.NA and _ex["play_id"][i2] == -1, str(st2))
    short = [p for p in PLAYS if p["play_id"] != "13"]
    _ex, st3 = R.align_extras(store, R.pbp_kept(_write(X.d, "short.csv", PBP_COLS, short)))
    ck("extras: a game whose row count differs is left NA whole", st3["games_count_mismatch_n"] == 1
       and st3["rows_aligned"] == n_kept - 14, str(st3))
    pr = aux["pfr_rush"]
    ck("PFR rush: regular season only (a WC row is dropped) and keyed to gsis", pr[G1][100004] == (3, 6, 10, 1, 0))
    ck("PFR rush: an unjoinable row is counted, never invented", aux["joins"]["pfr_rush"]["unjoined"] == 1
       and 0 not in pr[G1], str(aux["joins"]["pfr_rush"]))
    ck("PFR def: defenders summed per team-game (missed 1 + 2)", aux["pfr_def"][G1]["BUF"][2] == 3.0 and aux["pfr_def"][G1]["BUF"][0] == 2)
    ck("NGS: week-0 season rows and POST rows never stored", set(aux["ngs"]) == {1, 2} and aux["ngs"][1][100004][1] == 3)
    ck("injuries: report status decoded (Out 3, Questionable 1, Doubtful 2)",
       aux["injuries"][3]["KC"][300002][1] == 3 and aux["injuries"][3]["KC"][100006][1] == 1 and aux["injuries"][2]["DEN"][400004][1] == 2)
    sp = aux["sit"]["players"]
    gids = [g["id"] for g in store["games"]]
    gi1 = gids.index(G1)
    ck("sit: kneel and spike plays are not on-field plays (KC charted plays G1 = 10)", aux["sit"]["teams"][gi1][store["teams"].index("KC")][0] == 10)
    ck("sit: the back's on-field count over the same plays (100004: 7 of KC's 10 -- plays 1-5, 8, 10)", sp[gi1][100004][1] == 7)
    ck("sit: red-zone on-field count (100006 on the field for the one KC red-zone play)", sp[gi1][100006][1 + R.SIT.index("red_zone")] == 1)
    gi4 = gids.index(G4)
    ck("sit: short yardage is 3rd/4th and 1-2 (100006 on the field for 3rd & 2, not for 3rd & 3)",
       sp[gi4][100006][1 + R.SIT.index("short_yardage")] == 1 and sp[gi4][100006][1 + R.SIT.index("third")] == 2)


# ---- PFR snap counts (B2, m2) -----------------------------------------------------------------------
def _s_snaps(ck, X):
    aux = X.aux
    ck("snaps: OL keyed by gsis through the PFR crosswalk, team snaps = the most any player took",
       aux["snaps"][G1]["KC"]["ol"][300001] == ("T", 60) and aux["snaps"][G1]["KC"]["team"] == 60)
    ck("snaps (B2): a back with 1 snap at 1% never makes the team total 100 (KC G1 = 60, its linemen's)",
       aux["snaps"][G1]["KC"]["team"] == 60, str(aux["snaps"][G1]["KC"]["team"]))
    ck("snaps (B2): with nobody at 99.5%+ the total is the max-snap player's snaps / share (DEN G3: 30 / 0.6 = 50)",
       aux["snaps"][G3]["DEN"]["team"] == 50 and aux["snaps"][G2B]["DEN"]["team"] == 40)
    stx = aux["joins"].get("snaps_team") or {}
    ck("snaps (B2): the fallback games are counted (1 of 7 team-games)", stx.get("team_fallback") == 1 and stx.get("team_games") == 7,
       str(stx))
    sk = aux["snaps"][G1]["KC"]["skill"]
    ck("snaps (m2): a special-teams-only back is kept with 0 offensive snaps and his special-teams snaps",
       100010 in sk and sk[100010][0] == 0 and sk[100010][2] == 5, str(sk.get(100010)))
    ck("snaps: the skill rows carry offense snaps, share and special-teams snaps", sk[100004] == (45, 0.75, 0), str(sk.get(100004)))
    S5, _T5 = _snap(X.store, aux, 5)
    den, kc, allp = S5.rb(100004, "DEN"), S5.rb(100004, "KC"), S5.rb(100004)
    ck("snap share: team-scoped (DEN: 20 + 30 of 40 + 50)", _close(den["usage"]["snap_share"]["raw"], 50 / 90),
       str(den["usage"]["snap_share"]))
    ck("snap share: KC's game reads the true total (45 of 60, not 45 of 100)", _close(kc["usage"]["snap_share"]["raw"], 0.75),
       str(kc["usage"]["snap_share"]))
    ck("snap share: no team = all his games (95 of 150)", _close(allp["usage"]["snap_share"]["raw"], 95 / 150))
    ck("recent: the snap share and carry share are team-scoped too (DEN 50 / 90; 2 of DEN's 5 designed runs)",
       _close(den["recent"]["snap_share"]["raw"], 50 / 90) and _close(den["recent"]["carry_share"]["raw"], 0.4)
       and _close(allp["recent"]["carry_share"]["raw"], 5 / 11), str((den["recent"]["carry_share"], allp["recent"]["carry_share"])))
    T = RP.season_tallies(X.store, aux, POS, max_week=2)
    pl10 = _rec(T, "KC", G1)["players"].get(100010)
    ck("snaps (m2): his game line holds the special-teams snaps and no offensive-share denominator",
       pl10 is not None and pl10[RP.PL["pfr_st_snaps"]] == 5 and pl10[RP.PL["pfr_snaps"]] == 0 and pl10[RP.PL["pfr_team_snaps"]] == 0)


# ---- definitions ----------------------------------------------------------------------------------------
def _s_defs(ck, X):
    ck("line yards: the stated convention at every breakpoint",
       all(_close(RP.line_yards(y), v) for y, v in ((-3, -3.6), (-1, -1.2), (0, 0.0), (3, 3.0), (4, 4.0), (5, 4.5), (7, 5.5),
                                                     (10, 7.0), (11, 7.0), (40, 7.0))))
    ck("two-minute: last 120 s of either half only", R.two_minute(2, 1900) and R.two_minute(4, 100) and not R.two_minute(2, 1930)
       and not R.two_minute(3, 100))
    ck("neutral: WP 20-80% outside the two-minute drill (wp x 1000; 0 = none)", R.neutral(500, 1, 3000) and not R.neutral(150, 1, 3000)
       and not R.neutral(500, 4, 60) and not R.neutral(0, 1, 3000))
    ck("neutral: the band's edges (22% and 78% in, 19% and 81% out)", R.neutral(220, 1, 3000) and R.neutral(780, 1, 3000)
       and not R.neutral(190, 1, 3000) and not R.neutral(810, 1, 3000))
    ck("sneak rule: under center, 1-2 to go, middle or guard", RP.sneak_rule(0, 1, 2, 0) and RP.sneak_rule(0, 2, 1, 3)
       and not RP.sneak_rule(1, 1, 2, 0) and not RP.sneak_rule(0, 3, 2, 0) and not RP.sneak_rule(0, 1, 1, 1))
    ck("direction: run_location x run_gap -> cells", RP.DIRS[RP.dir_ix(1, 1)] == "left_end" and RP.DIRS[RP.dir_ix(2, 0)] == "middle"
       and RP.DIRS[RP.dir_ix(3, 3)] == "right_guard" and RP.dir_ix(0, 0) == 0 and RP.dir_ix(1, 0) == 0)
    ck("personnel faced: 4 or fewer DBs base, 5 nickel, 6+ dime, none uncharted",
       (RP.dpers_ix(4), RP.dpers_ix(3), RP.dpers_ix(5), RP.dpers_ix(6), RP.dpers_ix(7), RP.dpers_ix(-1), RP.dpers_ix(None))
       == (1, 1, 2, 3, 3, 0, 0))
    ck("box: <=6 light, 7 neutral, 8+ heavy, none uncharted",
       (RP.box_ix(5), RP.box_ix(6), RP.box_ix(7), RP.box_ix(8), RP.box_ix(9), RP.box_ix(0), RP.box_ix(None)) == (1, 1, 2, 3, 3, 0, 0))
    ck("status codes: the report's words map to Questionable 1, Doubtful 2, Out 3, anything else 0",
       (R.STATUS_IX.get("QUESTIONABLE"), R.STATUS_IX.get("DOUBTFUL"), R.STATUS_IX.get("OUT"), R.STATUS_IX.get("NOTE", 0)) == (1, 2, 3, 0))


# ---- rushing bookkeeping (as of week 2: week 1 only) -------------------------------------------------
def _s_book(ck, X):
    store, aux, paths = X.store, X.aux, X.paths
    _S2, T2 = _snap(store, aux, 2)
    ck("counts: one sack carried a rusher id and was not a rush", T2.counts["sacks_with_rusher_id"] == 1)
    r1 = _rec(T2, "KC", G1)
    qb = r1["qbs"][100001]
    pl = r1["players"][100001]
    ck("QB official rushes: kneel + RPO keep + sneak + aborted = 4; the sack is not one", pl[RP.PL["rush"]] == 4)
    ck("QB official rushing yards: -1 + 5 + 1 - 3 = 2 (the -7 sack excluded)", pl[RP.PL["rush_yds"]] == 2)
    ck("partition: designed 1 + sneak 1 + scramble 0 + kneel 1 + aborted 1 = official rushes",
       (qb[RP.QL["dsg"]], qb[RP.QL["sneak"]], qb[RP.QL["scr"]], qb[RP.QL["kneel"]], qb[RP.QL["abort"]]) == (1, 1, 0, 1, 1)
       and sum(qb[RP.QL[x]] for x in ("dsg", "sneak", "scr", "kneel", "abort")) == pl[RP.PL["rush"]])
    ck("kneel: -1 yard counted as a rush, never a team play or a designed run",
       pl[RP.PL["kneel"]] == 1 and pl[RP.PL["kneel_yds"]] == -1 and r1["team"][RP.TL["snaps"]] == 10 and r1["team"][RP.TL["des"]] == 6)
    ck("aborted snap: an official rush, never a designed run", pl[RP.PL["abort"]] == 1 and pl[RP.PL["des"]] == 2)
    ck("QB dropbacks: attempt + sack + attempt = 3 (the sack is a dropback, not a rush)", qb[RP.QL["db"]] == 3 and qb[RP.QL["sack"]] == 1)
    ck("RPO: the keep is a designed run and an RPO keep; the RPO pass is a dropback, not a rush",
       qb[RP.QL["rpo"]] == 1 and r1["team"][RP.TL["rpo_passes"]] == 1 and r1["team"][RP.TL["rpo_runs"]] == 1)
    ck("sneak from FTN where FTN charted the play", T2.counts["sneak_ftn"] == 1 and T2.counts["sneak_rule"] == 0)
    rb1 = _rec(T2, "BUF", G1)
    q2 = rb1["qbs"][200001]
    ck("scramble: the scrambler's dropback and rush (nflverse leaves the passer empty)", q2[RP.QL["db"]] == 2 and q2[RP.QL["scr"]] == 1
       and rb1["players"][200001][RP.PL["rush"]] == 1)
    ck("QB exposure: team snaps x his dropback share (KC 10 x 3/3)", abs(qb[RP.QL["expo"]] - 10.0) < 1e-9)
    pr4 = r1["players"][100004]
    ck("back: designed runs 3, yards 16, line yards 5.0 - 2.4 + 7.0 = 9.6",
       pr4[RP.PL["des"]] == 3 and pr4[RP.PL["des_yds"]] == 16 and _close(r1["team"][RP.TL["rb_line_yds"]], 9.6 + 3.0))
    ck("back: PFR and NGS joined to his game (NGS team must match)", pr4[RP.PL["pfr_ybc"]] == 6 and pr4[RP.PL["ngs_att"]] == 3
       and pr4[RP.PL["ngs_exp"]] == 10)
    s_b = D.build_season(2030, paths["pbp"], paths["participation"], paths["ftn"], positions=POS)
    s_b["built"] = store["built"] + 1.0
    T_b = RP.season_tallies(s_b, aux, POS, max_week=2)
    ck("aux built on another store: its row extras are ignored (aborted snaps unknown), never laid on foreign rows",
       not R.aux_matches(aux, s_b) and T_b.counts["aborted_unknown"] > 0 and T_b.counts["aborted"] == 0)
    ck("back: a screen read CHK targeted to him (FTN)", pr4[RP.PL["screen_tgt"]] == 1 and pr4[RP.PL["chk_tgt"]] == 1)


def _s_eras(ck, X):
    """The sneak rule only without FTN; scramble splits only from 2023; no pbp extras."""
    d2 = os.path.join(X.d, "noftn")
    os.makedirs(d2)
    s_nf, a_nf, _p = _build(d2, with_ftn=False)
    T_nf = RP.season_tallies(s_nf, a_nf, POS, max_week=2)
    ck("sneak rule: used where FTN has no row (and only there)", T_nf.counts["sneak_rule"] == 1 and T_nf.counts["sneak_ftn"] == 0)
    d3 = os.path.join(X.d, "s2022")
    os.makedirs(d3)
    s22, a22, _p = _build(d3, season=2022)
    T22 = RP.season_tallies(s22, a22, POS, max_week=2)
    q22 = _rec(T22, "BUF", G1)["qbs"][200001]
    _S2, T2 = _snap(X.store, X.aux, 2)
    q2 = _rec(T2, "BUF", G1)["qbs"][200001]
    ck("scramble splits: a pre-2023 season adds nothing to pressure / coverage (scrambles uncharted then)",
       q22[RP.QL["press_n"]] == 0 and q22[RP.QL["mz_n"]] == 0 and q2[RP.QL["press_n"]] == 2 and q2[RP.QL["scr_press"]] == 1)
    ck("scramble splits: blitz counts read FTN's rusher count in any season", q22[RP.QL["blitz_n"]] == 2 and q22[RP.QL["scr_blitz"]] == 1)
    d4 = os.path.join(X.d, "noex")
    os.makedirs(d4)
    s_ne, a_ne, _p = _build(d4, with_extras=False)
    T_ne = RP.season_tallies(s_ne, a_ne, POS, max_week=2)
    ck("no extras: aborted snaps unknown and counted (stated), designed runs then include them",
       T_ne.counts["aborted_unknown"] > 0 and T_ne.counts["aborted"] == 0)


# ---- the as-of cutoff -----------------------------------------------------------------------------------
def _s_cutoff(ck, X):
    store, aux = X.store, X.aux
    _S2, T2 = _snap(store, aux, 2)
    _S4, T4 = _snap(store, aux, 4)
    ck("cutoff: tallies as of week w hold weeks < w only", T2.weeks == {(2030, 1)} and T4.weeks == {(2030, 1), (2030, 2), (2030, 3)})
    try:
        RP.RushSnapshot(2030, 2, RP.season_tallies(store, aux, POS, max_week=3), [])
        ck("cutoff: the snapshot refuses tallies holding the cutoff week", False)
    except ValueError:
        ck("cutoff: the snapshot refuses tallies holding the cutoff week", True)
    try:
        RP.RushSnapshot(2029, 18, None, [RP.season_tallies(store, aux, POS)])
        ck("cutoff: a later season is never a prior", False)
    except ValueError:
        ck("cutoff: a later season is never a prior", True)
    ck("snapshot: prior seasons at DECAY ** back", RP.RushSnapshot(2032, 1, None, [RP.season_tallies(store, aux, POS)]).weights
       == {2030: round(RP.DECAY ** 2, 4)})
    Sb = RP.build({2030: store}, {2030: aux}, 2030, 3, POS)
    ck("build: the cutoff season is the current tallies (weeks < 3), never one of its own priors",
       Sb.weights == {2030: 1.0} and len(Sb.tallies) == 1 and Sb.tallies[0][1].weeks == {(2030, 1), (2030, 2)}, str(Sb.weights))
    d29 = os.path.join(X.d, "s2029_b")
    os.makedirs(d29)
    s29, a29, _p = _build(d29, season=2029)
    Sb2 = RP.build({2029: s29, 2030: store}, {2029: a29, 2030: aux}, 2030, 3, POS)
    ck("build: the season before is a prior at DECAY (and the cutoff season once)",
       Sb2.weights == {2030: 1.0, 2029: round(RP.DECAY, 4)} and len(Sb2.tallies) == 2, str(Sb2.weights))


# ---- the traded back: usage team-scoped (F1), efficiency his -------------------------------------------
def _s_traded(ck, X):
    S4, _T4 = _snap(X.store, X.aux, 4)
    den = S4.rb(100004, "DEN")
    kc = S4.rb(100004, "KC")
    allp = S4.rb(100004)
    ck("traded back: DEN carry share = his DEN carries / DEN's designed runs in those games (2 / 5)",
       _close(den["usage"]["carry_share"]["raw"], 2 / 5) and den["usage"]["carry_share"]["n"] == 5, str(den["usage"]["carry_share"]))
    ck("traded back: KC carry share from KC games only (3 / 6)", _close(kc["usage"]["carry_share"]["raw"], 0.5))
    ck("traded back: no team = all his games, both sides (5 / 11)", _close(allp["usage"]["carry_share"]["raw"], 5 / 11)
       and allp["usage"]["carry_share"]["n"] == 11)
    ck("NGS: a weekly row joins only the game of the team it names (his week-2 row says KC; he played for DEN)",
       den["rushing"]["back"]["ryoe"]["n"] == 3 and den["rushing"]["back"]["ryoe_games"] == 1)
    ck("traded back: efficiency pools every team (5 carries, 26 yards)", den["rushing"]["carries"] == 5
       and _close(den["rushing"]["ypc"]["raw"], 26 / 5))
    ck("traded back: the DEN on-field target rate counts DEN plays only (1 target of 2 dropbacks)",
       _close(den["usage"]["tgt_per_db"]["raw"], 0.5) and _close(kc["usage"]["tgt_per_db"]["raw"], 1 / 3))
    shares = [f["raw"] for p_ in (den, kc, allp) for k_, f in RP.flatten(p_["usage"]).items()
              if f.get("unit") == "share" and f.get("raw") is not None]
    ck("every usage share of every scope lies in [0, 1]", shares and all(0 <= x <= 1 for x in shares), str(shares))
    ck("scopes are named", "team-scoped" in den["scope"]["usage"] and "every team" in den["scope"]["efficiency"])


# ---- k applied == k measured ----------------------------------------------------------------------------
def _s_k(ck, X):
    d = X.d
    S4, _T4 = _snap(X.store, X.aux, 4)
    den = S4.rb(100004, "DEN")
    f_ypc = den["rushing"]["ypc"]
    ck("k: the field carries its own metric's k from the table (rb_ypc 50)", f_ypc["metric"] == "rb_ypc" and f_ypc["k"] == 50.0)
    L = S4.league()
    lg = L["rb"][RP.RV["yds"]] / L["rb"][RP.RV["n"]]
    ck("k: value = (sum + k x prior) / (n + k), prior = the league's RB ypc",
       _close(f_ypc["prior"], lg) and _close(f_ypc["value"], (26 + 50.0 * lg) / (5 + 50.0)) and f_ypc["kind"] == "modelled")
    ck("k: the reliability stamp is n / (n + k) (5 / 55)", f_ypc.get("reliability") == round(5 / 55, 3), str(f_ypc.get("reliability")))
    cell = den["rushing"]["by_direction"]["left_end"]["ypc"]
    ck("k: a metric measured unsupported (None) keeps its prior and reads reliability 0",
       cell["k"] is None and cell["value"] == cell["prior"] and cell["reliability"] == 0.0)
    flat = {}
    for prof in (den, S4.qb(200001), S4.defense_run("KC"), S4.defense_qb("KC"), S4.offense_line("KC"), S4.team_context("KC")):
        flat.update({k_: v_ for k_, v_ in RP.flatten(prof).items()})
    bad_k = [k_ for k_, f in flat.items() if f.get("metric") and f.get("k") != (None if _fk(f["metric"]) is None else round(_fk(f["metric"]), 2))]
    ck("k: every stamped k equals the table entry of its metric", not bad_k, str(bad_k[:5]))
    stamped = {f["metric"] for f in flat.values() if f.get("metric")}
    ck("k: every metric the profiles stamp is in the code defaults", stamped <= set(RP._K_DEFAULT), str(stamped - set(RP._K_DEFAULT)))
    try:
        with open(SHIPPED_K, encoding="utf-8") as fh:
            shipped = json.load(fh)["k"]
        ck("k: the code defaults ARE the shipped measured table", shipped == RP._K_DEFAULT,
           str({m: (shipped.get(m), RP._K_DEFAULT.get(m)) for m in set(shipped) | set(RP._K_DEFAULT) if shipped.get(m) != RP._K_DEFAULT.get(m)}))
    except (OSError, ValueError) as e:
        ck("k: the code defaults ARE the shipped measured table", False, str(e))
    with _Notes() as nn:
        try:
            RP.reload_k(os.path.join(d, "missing.json"))
            k0, src = RP.k_for("rb_ypc")
        finally:
            RP.reload_k(os.path.join(d, "k.json"))
    ck("k: a missing table falls back to the defaults and is ledgered", k0 == RP._K_DEFAULT["rb_ypc"] and src == "default"
       and "RUSH-k-table" in nn.codes)
    # each field's metric is the quantity it holds (a field stamped with another quantity's name would take
    # that quantity's k -- the agreed review's F4 in this design)
    pinned = {("rb", "rushing.ypc"): "rb_ypc", ("rb", "rushing.success"): "rb_success", ("rb", "rushing.stuff"): "rb_stuff",
              ("rb", "rushing.by_direction.left_end.ypc"): "rb_dir_ypc_dev", ("rb", "rushing.by_direction.middle.share"): "rb_dir_share",
              ("rb", "rushing.by_box.heavy.success"): "rb_box_success_dev", ("rb", "rushing.line.ybc"): "rb_ybc",
              ("rb", "rushing.back.yac"): "rb_yac", ("rb", "rushing.back.ryoe"): "rb_ryoe", ("rb", "usage.carry_share"): "rb_carry_share",
              ("rb", "usage.tgt_per_db"): "rb_tgt_per_db", ("rb", "receiving.ypt"): "rb_ypt",
              ("qb", "scramble.rate"): "qb_scr_rate", ("qb", "scramble.vs.pressure"): "qb_scr_press_dev", ("qb", "designed.rate"): "qb_dsg_rate",
              ("qb", "designed.ypc"): "qb_dsg_ypc", ("qb", "sneak.rate"): "qb_sneak_rate", ("qb", "scramble.yds_per_db"): "qb_scr_yds_db",
              ("def", "vs_rb.ypc"): "def_rb_ypc", ("def", "vs_rb.by_side.edge.success"): "def_side_success_dev",
              ("def", "box.heavy"): "def_heavy_share", ("def", "box.by_offense_personnel.12.heavy"): "def_box_op_heavy_dev",
              ("def", "box.by_offense_personnel.12.light"): "def_box_op_light_dev", ("def", "contact.missed_tackle_rate"): "def_mtkl_rate",
              ("def", "front_proxy.dl3_or_fewer"): "def_dl3_share", ("dq", "scrambles.oe_ratio"): "def_scrambles_oe",
              ("dq", "designed_runs.oe_ratio"): "def_designed_runs_oe", ("dq", "pressure"): "def_pressure",
              ("ol", "line_yds"): "ol_line_yds", ("ol", "by_gap.edge.success"): "ol_gap_success_dev", ("tc", "neutral_run_rate"): "tm_neutral_run"}
    profs = {"rb": RP.flatten(den), "qb": RP.flatten(S4.qb(200001)), "def": RP.flatten(S4.defense_run("KC")),
             "dq": RP.flatten(S4.defense_qb("KC")), "ol": RP.flatten(S4.offense_line("KC")), "tc": RP.flatten(S4.team_context("KC"))}
    wrong = {k_: (profs[k_[0]].get(k_[1]) or {}).get("metric") for k_, m in pinned.items() if (profs[k_[0]].get(k_[1]) or {}).get("metric") != m}
    ck("k: each field is stamped with its own quantity's metric (pinned map)", not wrong, str(wrong))


# ---- the full path -> metric map (the reviews' mutation gap: one cell restamped survived the pinned map) ----
# Generated from the fixed code and read field by field against each basis (report §7). <dir> / <side> / <box> /
# <pers> / <form> / <op> stand for every cell of that split; None = an unshrunk, unmeasured field.
_SUBS = ((r"\.(left_end|left_tackle|left_guard|middle|right_guard|right_tackle|right_end)\.", ".<dir>."),
         (r"by_side\.(edge|tackle|interior)\.", "by_side.<side>."), (r"by_box\.(light|neutral|heavy)\.", "by_box.<box>."),
         (r"by_personnel_faced\.(base|nickel|dime)\.", "by_personnel_faced.<pers>."),
         (r"by_formation\.(shotgun|under_center)\.", "by_formation.<form>."),
         (r"by_offense_personnel\.(11|12|21|13|22|10)\.", "by_offense_personnel.<op>."), (r"by_gap\.(edge|tackle|interior)\.", "by_gap.<side>."))
EXPECTED_METRICS = {
    "rb": {
        "any_snap.carries_pg": None, "any_snap.carry_share": None, "any_snap.games": None, "any_snap.recent_carry_share": None,
        "any_snap.target_share": None, "any_snap.targets_pg": None,
        "receiving.adot": 'rb_adot', "receiving.catch": 'rb_catch', "receiving.checkdown_share": 'rb_chk_share',
        "receiving.screen_share": 'rb_screen_share', "receiving.yac_rec": 'rb_yac_rec', "receiving.ypt": 'rb_ypt',
        "receiving.ypt_vs_man": None, "receiving.ypt_vs_zone": None, "recent.carries_pg": None, "recent.carry_share": None,
        "recent.rz_carry_share": None, "recent.snap_share": None, "recent.target_share": None,
        "rushing.back.broken_tackles": 'rb_btk', "rushing.back.eight_box_share_ngs": None, "rushing.back.ryoe": 'rb_ryoe',
        "rushing.back.ryoe_carry_coverage": None, "rushing.back.time_to_los": None, "rushing.back.yac": 'rb_yac',
        "rushing.by_box.<box>.epa": 'rb_box_epa_dev', "rushing.by_box.<box>.expl10": 'rb_box_expl10_dev',
        "rushing.by_box.<box>.expl20": 'rb_box_expl20_dev', "rushing.by_box.<box>.stuff": 'rb_box_stuff_dev',
        "rushing.by_box.<box>.success": 'rb_box_success_dev', "rushing.by_box.<box>.ypc": 'rb_box_ypc_dev',
        "rushing.by_direction.<dir>.epa": 'rb_dir_epa_dev', "rushing.by_direction.<dir>.expl10": 'rb_dir_expl10_dev',
        "rushing.by_direction.<dir>.expl20": 'rb_dir_expl20_dev', "rushing.by_direction.<dir>.share": 'rb_dir_share',
        "rushing.by_direction.<dir>.stuff": 'rb_dir_stuff_dev', "rushing.by_direction.<dir>.success": 'rb_dir_success_dev',
        "rushing.by_direction.<dir>.ypc": 'rb_dir_ypc_dev', "rushing.by_formation.<form>.epa": 'rb_sg_epa_dev',
        "rushing.by_formation.<form>.expl10": 'rb_sg_expl10_dev', "rushing.by_formation.<form>.expl20": 'rb_sg_expl20_dev',
        "rushing.by_formation.<form>.stuff": 'rb_sg_stuff_dev', "rushing.by_formation.<form>.success": 'rb_sg_success_dev',
        "rushing.by_formation.<form>.ypc": 'rb_sg_ypc_dev', "rushing.by_personnel_faced.<pers>.epa": 'rb_pers_epa_dev',
        "rushing.by_personnel_faced.<pers>.expl10": 'rb_pers_expl10_dev',
        "rushing.by_personnel_faced.<pers>.expl20": 'rb_pers_expl20_dev',
        "rushing.by_personnel_faced.<pers>.stuff": 'rb_pers_stuff_dev',
        "rushing.by_personnel_faced.<pers>.success": 'rb_pers_success_dev',
        "rushing.by_personnel_faced.<pers>.ypc": 'rb_pers_ypc_dev', "rushing.by_side.<side>.epa": 'rb_side_epa_dev',
        "rushing.by_side.<side>.expl10": 'rb_side_expl10_dev', "rushing.by_side.<side>.expl20": 'rb_side_expl20_dev',
        "rushing.by_side.<side>.share": 'rb_side_share', "rushing.by_side.<side>.stuff": 'rb_side_stuff_dev',
        "rushing.by_side.<side>.success": 'rb_side_success_dev', "rushing.by_side.<side>.ypc": 'rb_side_ypc_dev',
        "rushing.epa": 'rb_epa', "rushing.expl10": 'rb_expl10', "rushing.expl20": 'rb_expl20',
        "rushing.first_down": 'rb_fd', "rushing.line.ybc": 'rb_ybc', "rushing.line_yds": 'rb_line_yds',
        "rushing.short_yardage_conv": 'rb_sy_conv', "rushing.shotgun_share": 'rb_sg_share', "rushing.stuff": 'rb_stuff',
        "rushing.success": 'rb_success', "rushing.td_rate": 'rb_td', "rushing.ypc": 'rb_ypc',
        "usage.carries_pg": 'rb_carries_pg', "usage.carry_share": 'rb_carry_share', "usage.early_onfield": 'rb_sit_early',
        "usage.early_touch_share": 'rb_early_touch_share', "usage.gl_carry_share": 'rb_gl_share',
        "usage.goal_line_onfield": 'rb_sit_goal_line', "usage.neutral_onfield": 'rb_sit_neutral',
        "usage.onfield_plays_current": None, "usage.rb_carry_share": 'rb_rb_carry_share',
        "usage.red_zone_onfield": 'rb_sit_red_zone', "usage.rz_carry_share": 'rb_rz_share',
        "usage.short_yardage_onfield": 'rb_sit_short_yardage', "usage.snap_share": 'rb_snap_share',
        "usage.snap_share_charted": 'rb_sit_all', "usage.target_share": 'rb_target_share',
        "usage.targets_pg": 'rb_targets_pg', "usage.tgt_per_db": 'rb_tgt_per_db',
        "usage.tgt_per_db_vs_man": 'rb_tgt_per_db_vs_man_dev', "usage.tgt_per_db_vs_zone": 'rb_tgt_per_db_vs_zone_dev',
        "usage.third_onfield": 'rb_sit_third', "usage.third_touch_share": 'rb_third_touch_share',
        "usage.two_minute_onfield": 'rb_sit_two_minute', "usage.two_minute_touch_share": 'rb_two_minute_touch_share',
    },
    "qb": {
        "aborted.per_game": None, "aborted.yds_per_game": None, "designed.by_side.edge": None,
        "designed.by_side.interior": None, "designed.by_side.tackle": None, "designed.expl10": 'qb_dsg_expl10',
        "designed.per_game": None, "designed.rate": 'qb_dsg_rate', "designed.read_option": None,
        "designed.rpo_keeps_rate": 'qb_rpo_rate', "designed.rpo_share_of_designed": None,
        "designed.success": 'qb_dsg_success', "designed.yds_rate": 'qb_dsg_yds_rate', "designed.ypc": 'qb_dsg_ypc', "hit_sack_rate": 'qb_hit_sack_rate',
        "kneel.per_game": 'qb_kneel_pg', "kneel.yds_per_game": None, "pressure_pfr": None,
        "scoring.gl_rush_rate": 'qb_gl_rate', "scoring.rush_td_pg": None, "scoring.rush_td_rate": 'qb_td_rate',
        "scoring.rz_rush_rate": 'qb_rz_rate', "scoring.x10_rate": 'qb_x10_rate', "scramble.expl10": 'qb_scr_expl10',
        "scramble.man_rate_faced": None, "scramble.per_game": None, "scramble.pressure_rate_faced": None,
        "scramble.rate": 'qb_scr_rate', "scramble.share_under_pressure": None, "scramble.vs.blitz": 'qb_scr_blitz_dev',
        "scramble.vs.man": 'qb_scr_man_dev', "scramble.vs.no_blitz": 'qb_scr_noblitz_dev',
        "scramble.vs.no_pressure": 'qb_scr_nopress_dev', "scramble.vs.pressure": 'qb_scr_press_dev',
        "scramble.vs.single_high": 'qb_scr_single_dev', "scramble.vs.two_high": 'qb_scr_two_dev',
        "scramble.vs.zone": 'qb_scr_zone_dev', "scramble.yds_per_db": 'qb_scr_yds_db',
        "scramble.yds_per_pressured_db": 'qb_scr_yds_press', "scramble.ypa": 'qb_scr_ypa', "sneak.per_game": None,
        "sneak.rate": 'qb_sneak_rate', "sneak.short_yardage_conv": 'qb_sneak_conv', "totals.rushes_pg": None,
        "totals.yds_pg": None,
    },
    "def": {
        "box.by_formation.<form>.heavy": 'def_box_sg_dev', "box.by_formation.<form>.mean_box": None,
        "box.by_offense_personnel.<op>.base": None, "box.by_offense_personnel.<op>.dime": None,
        "box.by_offense_personnel.<op>.heavy": 'def_box_op_heavy_dev',
        "box.by_offense_personnel.<op>.light": 'def_box_op_light_dev', "box.by_offense_personnel.<op>.mean_box": None,
        "box.by_offense_personnel.<op>.nickel": None, "box.heavy": 'def_heavy_share', "box.light": 'def_light_share',
        "box.mean": 'def_box_mean', "box.on_designed_runs.heavy": None, "box.on_designed_runs.mean_box": None,
        "contact.missed_tackle_rate": 'def_mtkl_rate', "contact.missed_tackles_pg": 'def_mtkl_pg',
        "contact.yac_allowed": 'def_yac', "contact.ybc_allowed": 'def_ybc', "front_proxy.dl3_or_fewer": 'def_dl3_share',
        "front_proxy.dl4": 'def_dl4_share', "front_proxy.dl5_plus": None, "per_game.opp_plays": None,
        "per_game.rb_carries": None, "per_game.rb_rush_yds": None, "personnel.base": 'def_base_share',
        "personnel.dime": 'def_dime_share', "personnel.nickel": 'def_nickel_share', "vs_qb_designed.success": None,
        "vs_qb_designed.ypc": None, "vs_rb.by_box.<box>.epa": 'def_box_epa_dev',
        "vs_rb.by_box.<box>.expl10": 'def_box_expl10_dev', "vs_rb.by_box.<box>.expl20": 'def_box_expl20_dev',
        "vs_rb.by_box.<box>.stuff": 'def_box_stuff_dev', "vs_rb.by_box.<box>.success": 'def_box_success_dev',
        "vs_rb.by_box.<box>.ypc": 'def_box_ypc_dev', "vs_rb.by_direction.<dir>.epa": 'def_dir_epa_dev',
        "vs_rb.by_direction.<dir>.expl10": 'def_dir_expl10_dev', "vs_rb.by_direction.<dir>.expl20": 'def_dir_expl20_dev',
        "vs_rb.by_direction.<dir>.stuff": 'def_dir_stuff_dev', "vs_rb.by_direction.<dir>.success": 'def_dir_success_dev',
        "vs_rb.by_direction.<dir>.ypc": 'def_dir_ypc_dev', "vs_rb.by_side.<side>.epa": 'def_side_epa_dev',
        "vs_rb.by_side.<side>.expl10": 'def_side_expl10_dev', "vs_rb.by_side.<side>.expl20": 'def_side_expl20_dev',
        "vs_rb.by_side.<side>.stuff": 'def_side_stuff_dev', "vs_rb.by_side.<side>.success": 'def_side_success_dev',
        "vs_rb.by_side.<side>.ypc": 'def_side_ypc_dev', "vs_rb.epa": 'def_rb_epa', "vs_rb.expl10": 'def_rb_expl10',
        "vs_rb.expl20": 'def_rb_expl20', "vs_rb.first_down": 'def_rb_fd', "vs_rb.line_yds": 'def_rb_line_yds',
        "vs_rb.short_yardage_conv": 'def_rb_sy_conv', "vs_rb.stuff": 'def_rb_stuff', "vs_rb.success": 'def_rb_success',
        "vs_rb.ypc": 'def_rb_ypc', "vs_rb_receiving.catch": 'def_rb_catch', "vs_rb_receiving.tgt_per_db": 'def_rb_tgt_db',
        "vs_rb_receiving.yac_rec": 'def_rb_yac_rec', "vs_rb_receiving.ypt": 'def_rb_ypt', "vs_sneak.conv": None,
    },
    "dq": {
        "blitz": 'def_blitz', "containment_proxy.oe_per_opp": None, "hit_sack_rate.oe_per_opp": None,
        "hit_sack_rate.oe_ratio": 'def_hit_sack_rate_oe', "hit_sack_rate.raw_rate": None,
        "containment_proxy.oe_ratio": 'def_containment_proxy_oe', "containment_proxy.raw_rate": None,
        "designed_runs.oe_per_opp": None, "designed_runs.oe_ratio": 'def_designed_runs_oe', "designed_runs.raw_rate": None,
        "designed_yds.oe_per_opp": None, "designed_yds.oe_ratio": 'def_designed_yds_oe', "designed_yds.raw_rate": None,
        "explosive_runs.oe_per_opp": None, "explosive_runs.oe_ratio": 'def_explosive_runs_oe',
        "explosive_runs.raw_rate": None, "pressure": 'def_pressure', "pressure_pfr": 'def_pressure_pfr',
        "red_zone_rushes.oe_per_opp": None, "red_zone_rushes.oe_ratio": 'def_red_zone_rushes_oe',
        "red_zone_rushes.raw_rate": None, "rush_tds.oe_per_opp": None, "rush_tds.oe_ratio": 'def_rush_tds_oe',
        "rush_tds.raw_rate": None, "scramble_rate_raw": None, "scramble_yds.oe_per_opp": None,
        "scramble_yds.oe_ratio": 'def_scramble_yds_oe', "scramble_yds.raw_rate": None, "scramble_ypa_allowed": None,
        "scrambles.oe_per_opp": None, "scrambles.oe_ratio": 'def_scrambles_oe', "scrambles.raw_rate": None,
    },
    "ol": {
        "by_gap.<side>.line_yds": None, "by_gap.<side>.success": 'ol_gap_success_dev', "line_yds": 'ol_line_yds', "ol_listed_out": None,
        "starters.continuity": None, "starters.missed_last_game": None, "starters.out_report": None, "stuff": 'ol_stuff',
        "success": 'ol_success', "ybc": 'ol_ybc',
    },
    "tc": {
        "designed_runs_pg": None, "early_down_run_rate": 'tm_early_run', "leading_run_rate": 'tm_lead_run',
        "neutral_run_rate": 'tm_neutral_run', "plays_pg": 'tm_plays_pg', "qb_keep_share": 'tm_qb_keep',
        "rb_carries_pg": None, "red_zone_run_rate": 'tm_rz_run', "rpo_rate": 'tm_rpo', "trailing_run_rate": 'tm_trail_run',
    },
}


def _compress(path):
    for pat, rep in _SUBS:
        path = re.sub(pat, rep, path)
    return path


def _s_metric_map(ck, X):
    S4, _T4 = _snap(X.store, X.aux, 4)
    profs = {"rb": S4.rb(100004, "DEN"), "qb": S4.qb(200001), "def": S4.defense_run("KC"), "dq": S4.defense_qb("KC"),
             "ol": S4.offense_line("KC"), "tc": S4.team_context("KC")}
    for kind, prof in profs.items():
        got, clash = {}, []
        for path, f in RP.flatten(prof).items():
            key = _compress(path)
            m = f.get("metric")
            if key in got and got[key] != m:
                clash.append((path, got[key], m))
            got[key] = m
        exp = EXPECTED_METRICS[kind]
        diff = {k_: (got.get(k_, "<absent>"), exp.get(k_, "<absent>")) for k_ in set(got) | set(exp) if got.get(k_, "<absent>") != exp.get(k_, "<absent>")}
        ck(f"metric map ({kind}): every field path carries exactly the metric of its definition ({len(exp)} paths)",
           not diff and not clash, str(sorted(diff.items())[:4]) + str(clash[:2]))


# ---- shrinkage priors ------------------------------------------------------------------------------------
def _s_priors(ck, X):
    S4, _T4 = _snap(X.store, X.aux, 4)
    den = S4.rb(100004, "DEN")
    L = S4.league()
    # the league's RB designed-run ypc, counted straight off the fixture's plays (weeks 1-3): RB rushers only,
    # no scramble, kneel, aborted snap, deleted play or two-point try
    ys = [int(p["yards_gained"]) for p in PLAYS if p["play_type"] == "run" and p["qb_scramble"] == "0" and p["aborted_play"] == "0"
          and p["play_deleted"] == "0" and p["two_point_attempt"] == "0" and int(p["week"]) < 4
          and POS.get(int(p["rusher_player_id"][3:])) == "RB"]
    ck("league prior: the RB ypc is RB designed runs only (55 yards on 15, straight off the plays)",
       len(ys) == 15 and sum(ys) == 55 and _close(den["rushing"]["ypc"]["prior"], 55 / 15), str((len(ys), sum(ys), den["rushing"]["ypc"]["prior"])))
    own = den["rushing"]["ypc"]
    lg_all = L["rb"][RP.RV["yds"]] / L["rb"][RP.RV["n"]]
    le = L["rb_dir"][1]
    off = le[RP.RV["yds"]] / le[RP.RV["n"]] - lg_all
    cell = den["rushing"]["by_direction"]["left_end"]["ypc"]
    ck("split prior: his overall + (the league's split - the league's overall), the sign kept (left end)",
       abs(off) > 0.5 and _close(cell["prior"], own["value"] + off) and _close(cell["base"], own["raw"] + off),
       str((cell.get("prior"), own["value"], off)))
    p2 = _fixture_k(X.d, "k2.json", qb_scr_rate=2.0)
    RP.reload_k(p2)
    try:
        q = S4.qb(200001)
    finally:
        RP.reload_k(os.path.join(X.d, "k.json"))
    lq = L["qb_line"]
    lg_sr = lq[RP.QL["scr"]] / lq[RP.QL["db"]]
    lgc = lq[RP.QL["scr_press"]] / lq[RP.QL["press"]]
    sr = q["scramble"]["rate"]
    pr = q["scramble"]["vs"]["pressure"]
    own_sr = (3 + 2.0 * lg_sr) / (6 + 2.0)
    ck("scramble split prior: his SHRUNK rate x the league's pressured / overall ratio (multiplicative); base the raw rate's",
       _close(sr["value"], own_sr) and _close(pr["prior"], own_sr * lgc / lg_sr) and _close(pr["base"], 0.5 * lgc / lg_sr),
       str((sr["value"], own_sr, pr.get("prior"), pr.get("base"), lgc, lg_sr)))


# ---- QB profile and the defense's observed-over-expected ------------------------------------------
def _s_qbdef(ck, X):
    S4, T4 = _snap(X.store, X.aux, 4)
    q = S4.qb(200001)
    ck("QB scramble rate: 3 scrambles / 6 dropbacks (k 0 in the fixture -> raw)", _close(q["scramble"]["rate"]["value"], 0.5))
    ck("QB scramble vs pressure: 2 of his 3 pressured dropbacks scrambled (2023+ charting)",
       _close(q["scramble"]["vs"]["pressure"]["raw"], 2 / 3) and q["scramble"]["vs"]["pressure"]["n"] == 3)
    ck("QB scramble vs no pressure: 1 of 3", _close(q["scramble"]["vs"]["no_pressure"]["raw"], 1 / 3))
    ck("QB partition reported", q["totals"]["partition"] == {"designed": 0, "sneak": 0, "scramble": 3, "kneel": 0, "aborted": 0})
    ck("QB scramble yards: 9 + 6 + 11 on 3 scrambles", _close(q["scramble"]["ypa"]["raw"], 26 / 3))
    ck("QB scramble TD: a scramble that scores is a rushing TD (1 in 3 games)", _close(q["scoring"]["rush_td_pg"]["raw"], 1 / 3),
       str(q["scoring"]["rush_td_pg"]))
    ck("scrambles: the scrambler's game line (2 in G3)", _rec(T4, "BUF", G3)["players"][200001][RP.PL["scr"]] == 2
       and _rec(T4, "BUF", G3)["players"][200001][RP.PL["scr_yds"]] == 17)
    ck("QB team filter: his BUF games hold his 6 dropbacks, a team he never played for none",
       S4.qb(200001, "BUF")["totals"]["dropbacks"] == 6 and S4.qb(200001, "KC")["totals"]["dropbacks"] == 0
       and S4.qb(200001, "KC")["games"]["current"] == 0)
    dq = S4.defense_qb("KC")
    sc = dq["scrambles"]
    # KC faced BUF's QB in weeks 1 and 2: G1 1 scramble of 2, G2 0 of 2, G3 (vs DEN) 2 of 2. Leave-one-out:
    # G1 expected 2 x (2/4) = 1.0, G2 expected 2 x (3/4) = 1.5 -> 2.5 expected, 1 observed
    ck("defense OE: expected from each QB's leave-one-out rate (1.0 + 1.5), observed 1", _close(sc["expected"], 2.5)
       and _close(sc["observed"], 1.0), str(sc))
    ck("defense OE: ratio shrunk toward 1 by its own k ((1 + 4) / (2.5 + 4))", _close(sc["oe_ratio"]["raw"], 0.4)
       and _close(sc["oe_ratio"]["value"], 5 / 6.5) and sc["oe_ratio"]["k"] == 4.0)
    ck("containment is labelled a proxy, every leaf of it (M4)", dq["containment_proxy"]["kind"] == "proxy"
       and all(dq["containment_proxy"][k_]["kind"] == "proxy" for k_ in ("oe_ratio", "oe_per_opp", "raw_rate")),
       str({k_: dq["containment_proxy"][k_]["kind"] for k_ in ("oe_ratio", "oe_per_opp", "raw_rate")}))
    ck("defense: yards per scramble allowed, raw (BUF's 9 against KC)", _close(dq["scramble_ypa_allowed"]["raw"], 9.0))
    dr = S4.defense_run("BUF")
    ck("defense vs RB runs: BUF allowed 7 RB carries, 28 yards (6 - 2 + 12 + 3; 4; 3 + 2) -- QB runs and the aborted snap out",
       dr["vs_rb"]["carries"] == 7 and _close(dr["vs_rb"]["ypc"]["raw"], 28 / 7), str(dr["vs_rb"]["carries"]))
    ck("defense: PFR missed tackles per game (3 in G1, none charted later)", _close(dr["contact"]["missed_tackles_pg"]["raw"], 3.0))
    ck("defense: front proxy labelled proxy", dr["front_proxy"]["dl4"]["kind"] == "proxy")
    ck("defense: box usage by offence personnel is a share of THOSE snaps (KC 12 personnel vs BUF: 2 snaps, both 8-man)",
       _close(dr["box"]["by_offense_personnel"]["12"]["heavy"]["raw"], 1.0) and dr["box"]["by_offense_personnel"]["12"]["heavy"]["n"] == 2)
    ck("defense: personnel faced (BUF: the base 3-4 on 2 of its 14 charted snaps, nickel on 12, dime none)",
       _close(dr["personnel"]["base"]["raw"], 2 / 14) and _close(dr["personnel"]["nickel"]["raw"], 12 / 14)
       and dr["personnel"]["dime"]["raw"] == 0.0, str(dr.get("personnel")))
    ck("QB designed run: read-option keeps are not available (no free source)", q["designed"]["read_option"]["kind"] == "not_available")


def _s_loo(ck, X):
    """Two seasons: each game's leave-one-out takes the game out at the weight it carries."""
    d29 = os.path.join(X.d, "s2029")
    os.makedirs(d29)
    s29, a29, _p = _build(d29, season=2029)
    T29 = RP.season_tallies(s29, a29, POS)
    T30 = RP.season_tallies(X.store, X.aux, POS, max_week=4)
    S = RP.RushSnapshot(2030, 4, T30, [T29], aux={2030: X.aux, 2029: a29})
    w = S.weights[2029]
    tot_scr, tot_db = 3 + 3 * w, 6 + 6 * w           # 200001's scrambles and dropbacks, weighted, both seasons
    games = ((w, 2, 1), (w, 2, 0), (1.0, 2, 1), (1.0, 2, 0))   # (weight, dropbacks, scrambles) of each game KC faced him
    exp = sum(wg * db * (tot_scr - wg * scr) / (tot_db - wg * db) for wg, db, scr in games)
    wrong = sum(wg * db * (tot_scr - scr) / (tot_db - db) for wg, db, scr in games)
    sc = S.defense_qb("KC")["scrambles"]
    ck("defense OE (two seasons): a prior-season game leaves its rate at its own weight (DECAY), not 1",
       abs(wrong - exp) > 0.02 and _close(sc["expected"], exp, 0.006) and _close(sc["observed"], 1 + w), str((sc, exp, wrong)))
    # the facing QB's leave-one-out rate is shrunk toward the league's by the QB metric's own k (2 here)
    lq = S.league()["qb_line"]
    lg = lq[RP.QL["scr"]] / lq[RP.QL["db"]]
    exp_k = sum(wg * db * (tot_scr - wg * scr + 2.0 * lg) / (tot_db - wg * db + 2.0) for wg, db, scr in games)
    RP.reload_k(_fixture_k(X.d, "k3.json", qb_scr_rate=2.0))
    try:
        sck = S.defense_qb("KC")["scrambles"]
    finally:
        RP.reload_k(os.path.join(X.d, "k.json"))
    ck("defense OE: each facing QB's leave-one-out rate is shrunk by HIS metric's k (qb_scr_rate 2 -> expected moves)",
       abs(exp_k - exp) > 0.02 and _close(sck["expected"], exp_k, 0.006), str((sck["expected"], exp_k, exp)))


# ---- OL starters and the injury report ------------------------------------------------------------
def _s_ol(ck, X):
    store, aux = X.store, X.aux
    S2, _T2 = _snap(store, aux, 2)
    S3, _T3 = _snap(store, aux, 3)
    S4, _T4 = _snap(store, aux, 4)
    ol3 = S3.offense_line("KC")["starters"]
    ck("OL starters as of week 3: the five with most snaps over the last games (OL1-5)",
       sorted(ol3["five"]) == sorted(D.gsis_str(300000 + i) for i in (1, 2, 3, 4, 5)), str(ol3["five"]))
    ck("OL: week 3's report lists one starter Out (pre-kickoff)", ol3["out_report"]["value"] == 1)
    ck("OL: one starter missed the last game (off the report: IR)", ol3["missed_last_game"]["value"] == 1)
    ol4 = S4.offense_line("KC")["starters"]
    ck("OL as of week 4: OL6 replaced OL5, week 4's report lists OL3 out, nobody missed G3",
       D.gsis_str(300006) in ol4["five"] and ol4["out_report"]["value"] == 1 and ol4["missed_last_game"]["value"] == 0)
    ol_w1 = RP.RushSnapshot(2031, 1, None, [RP.season_tallies(store, aux, POS)], aux={2030: aux}).offense_line("KC")["starters"]
    ck("OL before a team's first game: no starters read off last season (week 18 rests them)",
       ol_w1["five"] == [] and ol_w1["missed_last_game"]["value"] is None and ol_w1["out_report"]["value"] is None)
    S5, _T5 = _snap(store, aux, 5)
    ol5 = S5.offense_line("KC")["starters"]
    ck("OL as of week 5: the five over the team's last FOUR games (OL5's 110 snaps beat OL6's 65)",
       D.gsis_str(300005) in ol5["five"] and D.gsis_str(300006) not in ol5["five"] and ol5["games_read"] == 4, str(ol5["five"]))
    ck("OL as of week 5: OL4's 20 of 50 snaps is a missed game (under half)", ol5["missed_last_game"]["value"] == 1)
    ck("OL as of week 5: a Doubtful starter is out on the report, a Questionable one is not (1)", ol5["out_report"]["value"] == 1,
       str(ol5["out_report"]))
    S6, _T6 = _snap(store, aux, 6)
    ol6 = S6.offense_line("KC")["starters"]
    ck("OL as of week 6: the last four games only (OL6's 105 over G2-G5 beat OL5's 50; five games would keep OL5)",
       D.gsis_str(300006) in ol6["five"] and D.gsis_str(300005) not in ol6["five"] and ol6["games_read"] == 4, str(ol6["five"]))
    st3 = S3.status(100006, "KC")
    ck("status: the cutoff week's report (questionable, limited practice)", st3["value"] == "questionable" and st3["practice"] == "Limited")
    ck("status: doubtful and out read as such (DEN week 2, KC week 4)", S2.status(400004, "DEN")["value"] == "doubtful"
       and S4.status(100006, "KC")["value"] == "out")
    ck("status: a player of a team on the week's report who is not listed is active", S3.status(100001, "KC")["value"] == "active")
    n0 = X.notes.codes.count("RUSH-noreport")
    st_den = S3.status(100004, "DEN")
    ck("status (G4): a team the week's report lists NOBODY from reads unknown, not active (a playing team always has rows), "
       "ledgered once", st_den["value"] == "unknown" and "lists nobody from DEN" in st_den["source"]
       and S3.status(400004, "DEN")["value"] == "unknown" and X.notes.codes.count("RUSH-noreport") - n0 == 1, str(st_den))
    room = S4.rb_room_out("KC")
    ck("RB room: the carry share of backs listed Out this week (100006: 3 of KC's 8 designed runs in its last games)",
       _close(room["value"], 3 / 8), str(room))


def _s_noreport(ck, X):
    """M1: a week the injury block does not hold is "unknown", never "active"; ledgered once."""
    S6, _T6 = _snap(X.store, X.aux, 6)
    seen = getattr(RP, "_NOREPORT_SEEN", set())
    seen.discard((2030, 6))
    n0 = X.notes.codes.count("RUSH-noreport")
    st = S6.status(100006, "KC")
    st2 = S6.status(100004, "DEN")
    ck("no report for the week: status reads unknown (not active)", st["value"] == "unknown" and st2["value"] == "unknown", str(st))
    ol6 = S6.offense_line("KC")["starters"]
    ck("no report for the week: the OL's out count and the RB room's share read None, never 0",
       ol6["out_report"]["value"] is None and S6.rb_room_out("KC")["value"] is None, str((ol6["out_report"], S6.rb_room_out("KC"))))
    ck("no report for the week: ledgered RUSH-noreport once per (season, week) a process",
       X.notes.codes.count("RUSH-noreport") - n0 == 1, str(X.notes.codes[-6:]))


def _s_week4(ck, X):
    """As of week 5: the counting the reviews' mutants got past."""
    S5, T5 = _snap(X.store, X.aux, 5)
    r4 = _rec(T5, "KC", G4)
    ck("two QBs: each one's exposure is the team's snaps x HIS dropback share (10 x 3/4 and 10 x 1/4)",
       _close(r4["qbs"][100001][RP.QL["expo"]], 7.5) and _close(r4["qbs"][100007][RP.QL["expo"]], 2.5),
       str((r4["qbs"][100001][RP.QL["expo"]], r4["qbs"].get(100007, [None] * RP.QL_W)[RP.QL["expo"]])))
    pl = r4["players"][100006]
    ck("lateral: official rushing yards read nflverse rushing_yards (18), designed-run yards yards_gained (22)",
       pl[RP.PL["rush_yds"]] == 18 and pl[RP.PL["des_yds"]] == 22, str((pl[RP.PL["rush_yds"]], pl[RP.PL["des_yds"]])))
    tc = S5.team_context("KC")
    ck("team context: leading by 8+ the one play ran, trailing by 8+ it threw (lead 1.0, trail 0.0)",
       _close(tc["leading_run_rate"]["raw"], 1.0) and _close(tc["trailing_run_rate"]["raw"], 0.0),
       str((tc["leading_run_rate"]["raw"], tc["trailing_run_rate"]["raw"])))
    sy = S5.rb(100006)["rushing"]["short_yardage_conv"]
    ck("short yardage: 3rd & 2 is one, 3rd & 3 is not (1 run, converted)", sy["n"] == 1 and _close(sy["raw"], 1.0), str(sy))
    q = S5.qb(100001)
    ck("RPO share of his designed runs: 1 RPO keep of 2 (the week-4 run was not an RPO)",
       _close(q["designed"]["rpo_share_of_designed"]["raw"], 0.5), str(q["designed"]["rpo_share_of_designed"]))


# ---- B1: RB units are RB/FB only -----------------------------------------------------------------------
def _s_units(ck, X):
    M = X.M
    T = RP.season_tallies(X.store, X.aux, POS)
    r5 = _rec(T, "KC", G5)
    ck("a WR's and a TE's carries count for the team (KC G5: 3 designed runs) and are theirs (1 each)",
       r5["team"][RP.TL["des"]] == 3 and r5["players"][100002][RP.PL["des"]] == 1 and r5["players"][100003][RP.PL["des"]] == 1)
    wr = RP.RPOS.index("WR")
    ck("a WR's carry counts in the defense's run tally (BUF faced it), keyed WR, never in its RB-allowed figures",
       any(k_[4] == wr for k_ in T.dfn["BUF"]["run"]))
    ck("a WR / TE carry never enters the back tallies (T.rb holds RB/FB runs only)", 100002 not in T.rb and 100003 not in T.rb
       and 100006 in T.rb, str(sorted(T.rb)))
    A, B = M.half_tallies(X.store, X.aux, POS, "odd_even")
    tg = [sum(r["players"][100002][RP.PL["tgt"]] for r in Th.player_games[100002] if 100002 in r["players"]) for Th in (A, B)]
    cr = [sum(r["players"][100002][RP.PL["des"]] for r in Th.player_games[100002] if 100002 in r["players"]) for Th in (A, B)]
    F = RP.RushSnapshot(2030, 99, T, [])
    UA = M.unit_profiles(RP.RushSnapshot(2030, 99, A, []), F, POS)
    UB = M.unit_profiles(RP.RushSnapshot(2030, 99, B, []), F, POS)
    ck("measurement units (B1): a WR with carries and 15+ targets in each half is never an RB unit",
       min(tg) >= 15 and min(cr) >= 1 and ("rb", 100002) not in UA and ("rb", 100002) not in UB and ("rb", 100003) not in UA
       and ("rb", 100006) in UA and ("rb", 100006) in UB, str((tg, cr, sorted(k_ for k_ in UA if k_[0] == "rb"))))
    U = M.unit_profiles(F, F, POS)
    ck("unit_profiles: the position filter holds even if a non-RB lands in T.rb",
       ("rb", 100002) not in M.unit_profiles(_with_rb(F, 100002), F, POS) and ("rb", 100006) in U)


def _with_rb(S, pid):
    """A copy of the snapshot whose tallies list `pid` in T.rb (the pre-fix tallies did, for a WR)."""
    T = copy.copy(S.tallies[0][1])
    T.rb = dict(T.rb)
    T.rb[pid] = next(iter(S.tallies[0][1].rb.values()))
    return RP.RushSnapshot(S.season, S.week, T, [])


# ---- the measurement's own code ----------------------------------------------------------------------
def _s_measure(ck, X):
    M = X.M
    A, B = M.half_tallies(X.store, X.aux, POS, "odd_even")
    ck("split: odd vs even weeks, disjoint, together the season",
       A.weeks == {(2030, w) for w in (1, 3, 5)} and B.weeks == {(2030, w) for w in (2, 4, 6)}, str((A.weeks, B.weeks)))
    A2, B2 = M.half_tallies(X.store, X.aux, POS, "first_second")
    A3, B3 = M.half_tallies(X.store, X.aux, POS, "fixed_mix")
    ck("disclosure splits: weeks 1-9 vs 10-18; the fixed mix vs the rest",
       B2.weeks == set() and len(A2.weeks) == 6 and A3.weeks == {(2030, w) for w in (1, 4, 5)} and B3.weeks == {(2030, w) for w in (2, 3, 6)})
    v = {"car": 10, "tgt": 30}
    ck("unit gate: an RB efficiency metric needs 25 carries a half; receiving needs 15 targets; usage either",
       not M._gate("rb", "rb_ypc", v, v) and M._gate("rb", "rb_catch", v, v) and M._gate("rb", "rb_carry_share", v, v)
       and M._gate("rb", "rb_ypc", {"car": 25}, {"car": 25}) and not M._gate("rb", "rb_ypc", {"car": 25}, {"car": 24})
       and not M._gate("rb", "rb_catch", {"tgt": 14}, {"tgt": 30}))
    ck("unit gate: a QB needs 100 dropbacks a half (200 a season for persistence)",
       not M._gate("qb", "qb_scr_rate", {"db": 99}, {"db": 150}) and M._gate("qb", "qb_scr_rate", {"db": 100}, {"db": 100})
       and not M._gate("qb", "qb_scr_rate", {"db": 150}, {"db": 199}, half=False))
    ck("DECAY rule as written: 0.25 under r 0.3, 0.5 to under 0.6, 0.75 from 0.6",
       (M.decay_rule(0.29), M.decay_rule(0.3), M.decay_rule(0.599), M.decay_rule(0.6), M.decay_rule(0.95)) == (0.25, 0.5, 0.5, 0.75, 0.75))
    ck("the shipped DECAY is the rule applied to the measured tendency r", _decay_ok(M), _decay_detail())
    if importlib.util.find_spec("numpy") is None:     # CI and the server install none
        ck("(numpy is not installed here -- the k formula guard runs where it is)", True)
        return
    rows = [(0.1 * i + (0.05 if i % 2 else -0.05), 40.0, 0.1 * i, 60.0, "u", "p") for i in range(10)]
    rel = M._rel(rows)
    r = M.pearson([x[0] for x in rows], [x[2] for x in rows])
    ck("k formula: k = n(1 - r) / r with n the mean half denominator (50)", rel["k"] == round(50.0 * (1 - r) / r, 2)
       and rel["mean_n_half"] == 50.0 and rel["reliability_full_season"] == round(100.0 / (100.0 + 50.0 * (1 - r) / r), 3), str(rel))
    ck("k formula: r <= 0.02 measures no signal (k None)", M._rel([(a, na, -b, nb, u, p) for a, na, b, nb, u, p in rows])["k"] is None
       and M._rel(rows[:5])["k"] is None)


def _decay_ok(M):
    try:
        with open(os.path.join(HERE, "..", "research", "data", "nfl_rush", "persistence.json"), encoding="utf-8") as fh:
            pers = json.load(fh)
        return M.decay_rule(pers["tendency_pooled_r"]) == RP.DECAY == pers["decay_chosen"]
    except (OSError, ValueError, KeyError):
        return False


def _decay_detail():
    try:
        with open(os.path.join(HERE, "..", "research", "data", "nfl_rush", "persistence.json"), encoding="utf-8") as fh:
            pers = json.load(fh)
        return f"tendency r {pers.get('tendency_pooled_r')}, chosen {pers.get('decay_chosen')}, code {RP.DECAY}"
    except (OSError, ValueError) as e:
        return str(e)


# ---- M3: every profile says what aux it read ------------------------------------------------------------
def _s_auxstat(ck, X):
    store, aux, paths = X.store, X.aux, X.paths
    S4, _T4 = _snap(store, aux, 4)
    ck("aux stamp: a profile built on its own aux reads aligned", S4.rb(100004, "DEN").get("aux") == {2030: "aligned"}
       and S4.defense_qb("KC").get("aux") == {2030: "aligned"})
    s_b = D.build_season(2030, paths["pbp"], paths["participation"], paths["ftn"], positions=POS)
    s_b["built"] = store["built"] + 1.0
    n0 = X.notes.codes.count("RUSH-aux")
    Sst = RP.RushSnapshot(2030, 3, RP.season_tallies(s_b, aux, POS, max_week=3), [], aux={2030: aux})
    ck("aux stamp: an aux built on another store reads stale on every profile, ledgered RUSH-aux",
       Sst.rb(100004).get("aux") == {2030: "stale"} and Sst.team_context("KC").get("aux") == {2030: "stale"}
       and X.notes.codes.count("RUSH-aux") - n0 == 1, str(Sst.rb(100004).get("aux")))
    Smi = RP.RushSnapshot(2030, 3, RP.season_tallies(store, None, POS, max_week=3), [])
    ck("aux stamp: no aux reads missing", Smi.offense_line("KC").get("aux") == {2030: "missing"})


def _s_coverage(ck, X):
    store, aux, paths = X.store, X.aux, X.paths
    cov = aux.get("coverage") or {}
    ck("coverage: every block's reach is recorded against the store (pfr_rush 3 of 9 games, injuries weeks 2-5 of 1-6)",
       cov.get("pfr_rush", {}).get("covered") == 3 and cov.get("pfr_rush", {}).get("games") == 9
       and cov.get("injuries", {}).get("short") == [1, 6], str({k_: (v.get("covered"), v.get("short")) for k_, v in cov.items()}))
    p2 = dict(paths)
    p2["pfr_rush"] = _write(X.d, "pfr_rush_cut.csv", PFR_RUSH_COLS, _pfr_rush_rows(skip=(G3,)))
    ngs = R.read_ngs_rush(os.path.join(X.d, "ngs.csv.gz"), [2030], IDS, {})
    a2 = R.build_aux(2030, store, p2, PFR, IDS, ngs)
    c2 = a2["coverage"]["pfr_rush"]
    ck("coverage: a truncated PFR file shows its lost game as short and in `missing`",
       c2["n_short"] == cov["pfr_rush"]["n_short"] + 1 and G3 in c2["short"]
       and any(m_.startswith("pfr_rush:") for m_ in a2["missing"]), str((c2, a2["missing"][-3:])))


def _s_clamp(ck, X):
    f = RP.fld(1, 1, "share", "rb_tgt_per_db_vs_zone_dev", prior=1.24)
    g = RP.fld(0, 2, "share", "def_box_op_heavy_dev", prior=-0.048, base=-0.048)
    ck("share priors (m1): an additive offset past 1 or under 0 is clamped, and so is the value; raw stays measured",
       f["prior"] == 1.0 and f["value"] <= 1.0 and g["prior"] == 0.0 and g["base"] == 0.0 and g["value"] >= 0.0 and g["raw"] == 0.0,
       str((f, g)))
    big = RP.fld(5, 1, "share", "rb_tgt_per_db_vs_zone_dev", prior=0.9)
    ck("share values (m1): a raw past 1 (NGS attempts over designed runs reach 1.4) shrinks to at most 1; raw stays 5",
       big["raw"] == 5.0 and big["value"] == 1.0, str(big))
    h = RP.fld(3, 2, "ratio", "def_scrambles_oe", prior=1.0)
    ck("a ratio is never clamped (only shares are)", h["raw"] == 1.5 and h["value"] > 1.0)


# ---- F14: the licence -- every field says where it comes from; non-PFR stand-ins --------------------------
_TAG = re.compile(r"^(pbp|participation|ftn|injuries|pfr|pfr_snaps|ngs|none|derived:[a-z_]+(\+[a-z_]+)+)$")


def _profiles4(X):
    S4, _T4 = _snap(X.store, X.aux, 4)
    return {"rb": S4.rb(100004, "DEN"), "qb": S4.qb(200001), "def": S4.defense_run("KC"), "dq": S4.defense_qb("KC"),
            "ol": S4.offense_line("KC"), "tc": S4.team_context("KC")}


def _s_sources(ck, X):
    profs = {k_: RP.flatten(v) for k_, v in _profiles4(X).items()}
    bad = [(k_, p_, f.get("source")) for k_, fl in profs.items() for p_, f in fl.items() if not _TAG.match(str(f.get("source")))]
    ck("sources: every leaf of every profile carries a source tag", not bad and sum(len(fl) for fl in profs.values()) > 400, str(bad[:4]))
    ck("sources: the leaf's tag is SOURCE_OF(kind, path), the API the model filters on",
       all(f.get("source") == RP.SOURCE_OF(k_, p_) for k_, fl in profs.items() for p_, f in fl.items()))
    pinned = {("rb", "rushing.line.ybc"): "pfr", ("rb", "rushing.back.yac"): "pfr", ("rb", "rushing.back.broken_tackles"): "pfr",
              ("rb", "rushing.back.ryoe"): "ngs", ("rb", "rushing.back.eight_box_share_ngs"): "ngs", ("rb", "rushing.back.time_to_los"): "ngs",
              ("rb", "rushing.back.ryoe_carry_coverage"): "derived:ngs+pbp", ("rb", "usage.snap_share"): "pfr_snaps",
              ("rb", "recent.snap_share"): "pfr_snaps", ("def", "contact.ybc_allowed"): "pfr", ("def", "contact.yac_allowed"): "pfr",
              ("def", "contact.missed_tackles_pg"): "pfr", ("def", "contact.missed_tackle_rate"): "pfr", ("dq", "pressure_pfr"): "pfr",
              ("qb", "pressure_pfr"): "pfr", ("ol", "ybc"): "pfr", ("ol", "starters.missed_last_game"): "pfr_snaps",
              ("ol", "starters.continuity"): "pfr_snaps", ("ol", "starters.out_report"): "derived:injuries+pfr_snaps",
              ("ol", "ol_listed_out"): "injuries", ("qb", "hit_sack_rate"): "pbp", ("dq", "hit_sack_rate.oe_ratio"): "pbp",
              ("rb", "rushing.ypc"): "pbp", ("rb", "usage.carry_share"): "derived:participation+pbp",
              ("rb", "recent.carries_pg"): "derived:participation+pbp", ("rb", "usage.snap_share_charted"): "participation",
              ("dq", "blitz"): "derived:ftn+pbp", ("def", "vs_rb.by_box.heavy.ypc"): "derived:participation+ftn+pbp",
              ("qb", "designed.read_option"): "none"}
    wrong = {k_: profs[k_[0]].get(k_[1], {}).get("source") for k_, t in pinned.items() if profs[k_[0]].get(k_[1], {}).get("source") != t}
    ck("sources: every PFR- and NGS-derived field is tagged so (contact, broken tackles, missed tackles, PFR pressure, snap "
       "shares, the OL starters' snap fields, RYOE and the NGS box share)", not wrong, str(wrong))
    pfr_re, ngs_re = re.compile(r"\bPFR\b"), re.compile(r"\bNGS (rush|share|average|attempts|weekly)")
    leak = [(k_, p_) for k_, fl in profs.items() for p_, f in fl.items()
            if (pfr_re.search(f.get("basis") or "") and not RP.restricted(f["source"]))
            or (ngs_re.search(f.get("basis") or "") and "ngs" not in f["source"])]
    ck("sources: no field whose basis reads PFR or NGS data escapes the restricted tags", not leak, str(leak[:4]))
    ck("restricted(): pfr / pfr_snaps / ngs and any mix holding one; pbp, participation, ftn, injuries never",
       RP.restricted("pfr") and RP.restricted("pfr_snaps") and RP.restricted("derived:ngs+pbp") and RP.restricted("derived:injuries+pfr_snaps")
       and not RP.restricted("pbp") and not RP.restricted("derived:participation+ftn+pbp") and not RP.restricted("injuries"))


def _s_hitsack(ck, X):
    S2, _T2 = _snap(X.store, X.aux, 2)
    S4, _T4 = _snap(X.store, X.aux, 4)
    hs = S2.qb(100001)["hit_sack_rate"]
    ck("hit + sack (F14b): the sack and a hit on a completed pass are 2 of his 3 dropbacks; a pbp proxy with its own metric",
       _close(hs["raw"], 2 / 3) and hs["n"] == 3 and hs["kind"] == "proxy" and hs["source"] == "pbp" and hs["metric"] == "qb_hit_sack_rate", str(hs))
    dq = S4.defense_qb("BUF")["hit_sack_rate"]
    ck("hit + sack allowed (F14b): BUF's opponents hit or sacked on 2 of 4 dropbacks, against the QBs' own leave-one-out rates",
       dq["observed"] == 2 and _close(dq["raw_rate"]["raw"], 0.5) and dq["opportunities"] == 4 and dq["oe_ratio"]["kind"] == "proxy"
       and dq["oe_ratio"]["metric"] == "def_hit_sack_rate_oe", str(dq))
    # G3: the expected inside it reads each facing QB's leave-one-out hit + sack rate shrunk by qb_hit_sack_rate's OWN k:
    # KC's QB (G1: 2 of 3; 0 of 1 in G3B) and DEN's (G3: 0 of 1; 0 of 2 elsewhere), league 2 of 15
    lq = S4.league()["qb_line"]
    lg = lq[RP.QL["hs"]] / lq[RP.QL["db"]]

    def exp_hs(k):
        return 3 * (2 - 2 + k * lg) / (4 - 3 + k) + 1 * (0 + k * lg) / (3 - 1 + k)
    RP.reload_k(_fixture_k(X.d, "k4.json", qb_hit_sack_rate=2.0))
    try:
        dq2 = S4.defense_qb("BUF")["hit_sack_rate"]
    finally:
        RP.reload_k(os.path.join(X.d, "k.json"))
    ck("hit + sack allowed (G3): each facing QB's leave-one-out rate is shrunk by qb_hit_sack_rate's own k (10 -> 0.475, 2 -> 0.333)",
       _close(lg, 2 / 15) and _close(dq["expected"], exp_hs(10.0), 0.006) and _close(dq2["expected"], exp_hs(2.0), 0.006),
       str((dq["expected"], exp_hs(10.0), dq2["expected"], exp_hs(2.0))))


def _s_ol_listed(ck, X):
    S3, _T3 = _snap(X.store, X.aux, 3)
    S5, _T5 = _snap(X.store, X.aux, 5)
    S6, _T6 = _snap(X.store, X.aux, 6)
    o5 = S5.offense_line("KC")
    ck("OL listed out (F14c): every T / G / C Out or Doubtful on the week's report, starter or not (OL1 Doubtful + OL6 Out = 2; "
       "Questionable OL2 not), where the starters' count reads 1", o5["ol_listed_out"]["value"] == 2 and o5["starters"]["out_report"]["value"] == 1
       and o5["ol_listed_out"]["source"] == "injuries", str(o5["ol_listed_out"]))
    S2, _T2 = _snap(X.store, X.aux, 2)
    ck("OL listed out: None with no report for the week or none for the team (G4); 0 when the team's report lists no lineman",
       S6.offense_line("KC")["ol_listed_out"]["value"] is None and S3.offense_line("DEN")["ol_listed_out"]["value"] is None
       and S2.offense_line("DEN")["ol_listed_out"]["value"] == 0 and S3.offense_line("KC")["ol_listed_out"]["value"] == 1)


def _s_pfr_invariance(ck, X):
    """Round 4 (the rule review's M8) and its confirmation (Opus O4f, Sonnet R8): a special-teams-only PFR snap line
    for a back (100006 in G6, where pbp and participation never show him) must move no field that is not tagged
    pfr / pfr_snaps. G6 here carries participation-charted KC plays, so a sum over PFR-listed games
    (`onfield_plays_current`, the on-field shares) would move; and back 100011 is on the field per participation for
    all of them with no touch, so the participation clause of an appearance is pinned too."""
    part_x = PART + [_part(G6, int(p["play_id"]), "KC", _on(100001, 100002, 100011)) for p in PLAYS if p["game_id"] == G6]
    px = dict(X.paths)
    px["participation"] = _write(X.d, "part_g6.csv", PART_COLS, part_x)
    store_x = D.build_season(2030, px["pbp"], px["participation"], px["ftn"], positions=POS)
    ngs = R.read_ngs_rush(os.path.join(X.d, "ngs.csv.gz"), [2030], IDS, {})
    a1 = R.build_aux(2030, store_x, px, PFR, IDS, ngs)
    rows = _snaps() + [(G6, "", "2030", "REG", "06", "Kc Back2", "RB6", "RB", "KC", "", "0", "0.0", "0", "0", "4", "0")]
    p2 = dict(px)
    p2["snaps"] = _write(X.d, "snaps_st_only.csv", SNAP_COLS, rows)
    a2 = R.build_aux(2030, store_x, p2, PFR, IDS, ngs)
    S_a, _Ta = _snap(store_x, a1, 7)
    S_b, T_b = _snap(store_x, a2, 7)
    g6 = _rec(T_b, "KC", G6)
    ck("PFR snap lines (O4f / R8): the planted game carries participation-charted team plays (16) the back was not on the field for",
       g6["team"][RP.TL["sit_all"]] == 16 and g6["players"][100006][RP.PL["sit_all"]] == 0 and g6["players"][100006][RP.PL["app"]] == 0,
       str((g6["team"][RP.TL["sit_all"]], g6["players"].get(100006, [None] * RP.PL_W)[RP.PL["sit_all"]])))

    def profs(S):
        return {"rb": S.rb(100006, "KC"), "rb_all": S.rb(100006), "qb": S.qb(100001), "def": S.defense_run("MIA"),
                "dq": S.defense_qb("MIA"), "ol": S.offense_line("KC"), "tc": S.team_context("KC")}
    pa, pb = profs(S_a), profs(S_b)
    moved, restricted_moved = [], []
    for k_ in pa:
        fa, fb = RP.flatten(pa[k_]), RP.flatten(pb[k_])
        for path, f in fa.items():
            g = fb.get(path) or {}
            if any(f.get(x) != g.get(x) for x in ("value", "raw", "n", "prior", "reliability")):
                (restricted_moved if RP.restricted(f["source"]) else moved).append((k_, path, f.get("raw"), g.get("raw")))
    ck("PFR snap lines (M8): a special-teams-only PFR line moves no field that is not tagged pfr / pfr_snaps -- "
       "onfield_plays_current and the on-field shares included", not moved and pa["rb"]["games"] == pb["rb"]["games"]
       and pa["rb_all"]["games"] == pb["rb_all"]["games"]
       and pa["rb"]["usage"]["onfield_plays_current"]["value"] == pb["rb"]["usage"]["onfield_plays_current"]["value"], str(moved[:4]))
    ck("PFR snap lines (M8): it does move the restricted display block (any_snap.games 5 -> 6, any_snap.carry_share)",
       _close(pa["rb"]["any_snap"]["games"]["raw"], 5.0) and _close(pb["rb"]["any_snap"]["games"]["raw"], 6.0)
       and pb["rb"]["any_snap"]["carry_share"]["raw"] < pa["rb"]["any_snap"]["carry_share"]["raw"]
       and all(RP.restricted(f["source"]) for f in RP.flatten(pa["rb"]["any_snap"]).values()),
       str((pa["rb"]["any_snap"]["games"], pb["rb"]["any_snap"]["games"], restricted_moved[:3])))
    on = S_b.rb(100011, "KC")
    ck("appearance (M9 / R8): a back on the field per participation with no touch IS an appearance (1 game, 16 of 16 charted plays)",
       g6["players"][100011][RP.PL["app"]] == 1 and on["games"]["current"] == 1 and _close(on["usage"]["snap_share_charted"]["raw"], 1.0)
       and on["usage"]["onfield_plays_current"]["value"] == 16 and _close(on["usage"]["carries_pg"]["raw"], 0.0),
       str((on["games"], on["usage"]["snap_share_charted"], on["usage"]["onfield_plays_current"])))
    S5, T5 = _snap(X.store, X.aux, 5)
    g1 = _rec(T5, "KC", G1)["players"]
    ck("appearance (M8): a back PFR lists only (1 offensive snap, no play, no participation line; special teams only) "
       "does not appear; one with a play does", g1[100009][RP.PL["app"]] == 0 and g1[100010][RP.PL["app"]] == 0
       and g1[100004][RP.PL["app"]] == 1 and S5.rb(100009, "KC")["games"]["current"] == 0
       and S5.rb(100009, "KC")["any_snap"]["games"]["raw"] == 1.0, str((S5.rb(100009, "KC")["games"], g1[100009][RP.PL["app"]])))


# ---- the dataset builder's own path: frozen weeks, rows independent of the week's other games ---------------
def _s_builder(ck, X):
    M = X.M
    d = X.d

    def build_ds(name, plays, k_frames=False):
        dd = os.path.join(d, name)
        os.makedirs(dd)
        st_, ax_, _p = _build(dd, plays=plays)
        meta, schema = M.run_build({2030: st_}, {2030: ax_}, POS, IDS, SLEEPER, dd, seasons=(2030,), max_week={2030: 3},
                                   k_frames=k_frames)
        return dd, meta, schema

    def rows(dd, kind, gid):
        with gzip.open(os.path.join(dd, f"{kind}_games_2023_2026.csv.gz"), "rt", newline="") as fh:
            return [r for r in csv.DictReader(fh) if r["game_id"] == gid]
    full, meta, schema = build_ds("ds_full", PLAYS)
    # week 3 holds G3 (first in file order) and G3B (second): each game's rows must not move when the
    # other is removed -- the leak the agreed review found (F3) shows only in the game processed second
    for gone, kept in ((G3B, G3), (G3, G3B)):
        part_dd, _m, _s = build_ds(f"ds_no_{gone}", [p for p in PLAYS if p["game_id"] != gone])
        for kind in ("rb", "qb"):
            a = rows(full, kind, kept)
            b = rows(part_dd, kind, kept)
            ck(f"builder ({kind}): {kept}'s rows do not move when the week's other game {gone} is removed", a and a == b,
               str([k_ for k_ in (a[0] if a else {}) if b and a[0].get(k_) != b[0].get(k_)][:5]))
    w1 = rows(full, "rb", G1)
    r100004 = [r for r in w1 if r["gsis"] == D.gsis_str(100004)][0]
    ck("builder: week-1 rows read no week-1 play (no prior season in the fixture: blank profile)",
       r100004["p_games_cur"] == "0" and r100004["act_rush"] == "3" and r100004["p_rushing_carries"] == "0")
    r10 = [r for r in w1 if r["gsis"] == D.gsis_str(100010)]
    ck("builder (m2): a special-teams-only back's row reads act_played 0, act_any_snap 1",
       len(r10) == 1 and r10[0].get("act_played") == "0" and r10[0].get("act_any_snap") == "1" and r10[0].get("in_sleeper") == "1",
       str({k_: r10[0].get(k_) for k_ in ("act_played", "act_any_snap", "in_sleeper")} if r10 else "no row"))
    g3 = [r for r in rows(full, "rb", G3) if r["gsis"] == D.gsis_str(100004)][0]
    ck("builder: the back's usage is scoped to the team he plays for (DEN: 1 of 3, not all his games' 4 of 9)",
       _close(float(g3["p_usage_carry_share_raw"]), 1 / 3), g3["p_usage_carry_share_raw"])
    ck("builder: d_ columns read the OPPONENT's defense (BUF allowed 5 RB carries), tc_ the team's context (DEN 2 a game)",
       float(g3["d_vs_rb_carries"]) == 5 and _close(float(g3["tc_designed_runs_pg"]), 2.0) and g3["opp"] == "BUF",
       str((g3["d_vs_rb_carries"], g3["tc_designed_runs_pg"])))
    q3 = [r for r in rows(full, "qb", G3) if r["gsis"] == D.gsis_str(200001)][0]
    ck("builder: a QB row reads his own rate as of the week (1 scramble in 4 dropbacks)", _close(float(q3["q_scramble_rate_raw"]), 0.25))
    ck("builder: aux status stamped on the rows (week 3: 2030 aligned; week 1 read no games, so no season)",
       g3.get("aux_status") == "2030:aligned" and r100004.get("aux_status") == "", str((g3.get("aux_status"), r100004.get("aux_status"))))
    try:
        S_bad = RP.RushSnapshot(2030, 99, RP.season_tallies(X.store, X.aux, POS), [])
        M.week_rows(S_bad, X.store, X.aux, 2030, 3, [], POS, IDS, {}, {})
        ck("builder: week_rows raises when the snapshot holds the week", False)
    except RuntimeError:
        ck("builder: week_rows raises when the snapshot holds the week", True)
    # M4 / m5: the schema reads the field definitions
    sch = {kind: {e["name"]: e for e in schema[kind]} for kind in ("rb", "qb")}
    ck("schema: a shrunk column is modelled though its week-1 rows had no prior (q_scramble_vs_pressure)",
       sch["qb"]["q_scramble_vs_pressure"]["kind"] == "modelled" and sch["rb"]["p_rushing_ypc"]["kind"] == "modelled",
       str(sch["qb"]["q_scramble_vs_pressure"]["kind"]))
    ck("schema: proxies and the unavailable read as such (containment leaves, read option)",
       sch["qb"]["dq_containment_proxy_oe_ratio"]["kind"] == "proxy" and sch["qb"]["dq_containment_proxy_raw_rate"]["kind"] == "proxy"
       and sch["qb"]["q_designed_read_option"]["kind"] == "not_available")
    ck("schema: act_played (offense) and act_any_snap are outcomes; Sleeper's columns say post-week",
       sch["rb"]["act_played"]["kind"].startswith("actual") and "offensive" in sch["rb"]["act_played"]["meaning"]
       and sch["rb"]["act_any_snap"]["kind"].startswith("actual") and "post-week" in sch["rb"]["in_sleeper"]["kind"]
       and "post-week" in sch["qb"]["sl_qb1"]["kind"] and "post-week" in sch["rb"]["sl_proj_rush_att"]["kind"])
    ck("schema: the OL starters' out count names the injury report, as meaning and source",
       "injury report" in sch["rb"]["ol_starters_out_report"]["meaning"] and "injury report" in sch["rb"]["ol_starters_out_report"]["source"])
    no_unit = [c for kind in sch for c, e in sch[kind].items() if not e["unit"] and e["kind"] != "key"]
    ck("schema: every column has a unit (keys excepted)", not no_unit, str(no_unit[:6]))
    ck("schema: the license (CC BY-SA, FTN Data via nflverse) and the k frames are stated",
       "CC BY-SA" in json.dumps(schema.get("license")) and "FTN Data via nflverse" in json.dumps(schema.get("license"))
       and "k_fit_only" in (schema.get("k_note") or ""))
    tags = {kind: {c: e.get("source_tag") for c, e in sch[kind].items()} for kind in sch}
    ck("schema (F14a): every column carries a source tag; PFR / NGS columns carry theirs",
       all(t for kind in tags for t in tags[kind].values()) and tags["rb"]["p_rushing_back_ryoe"] == "ngs"
       and tags["rb"]["act_pfr_ybc"] == "pfr" and tags["rb"]["d_contact_missed_tackles_pg"] == "pfr"
       and tags["rb"]["p_usage_snap_share_raw"] == "pfr_snaps" and tags["rb"]["p_usage_snap_share_n"] == "pfr_snaps"
       and tags["rb"]["p_rushing_ypc"] == "pbp" and tags["qb"]["q_pressure_pfr"] == "pfr" and tags["rb"]["sl_proj_rush_att"] == "sleeper",
       str({c: tags["rb"].get(c) for c in ("p_rushing_back_ryoe", "act_pfr_ybc", "p_usage_snap_share_n")}))
    kw = {"pbp": "pbp", "participation": "participation", "ftn": "FTN", "injuries": "injury report", "pfr": "Pro Football Reference",
          "pfr_snaps": "snap counts", "ngs": "Next Gen Stats", "sleeper": "Sleeper", "key": "key", "none": "not available"}
    drift = [(kind, c, e["source_tag"], e["source"]) for kind in sch for c, e in sch[kind].items()
             if any(kw[x] not in e["source"] for x in (e["source_tag"][8:].split("+") if e["source_tag"].startswith("derived:")
                                                       else [e["source_tag"]]))]
    ck("schema (G5): every column's human-readable source names each part of its source_tag (none still says pbp alone)",
       not drift and "participation" in sch["qb"]["dq_containment_proxy_oe_ratio"]["source"]
       and "Sleeper" in sch["rb"]["in_sleeper"]["source"] and "snap counts" in sch["rb"]["act_played"]["source"], str(drift[:3]))
    ck("schema (M8): the games counts are pbp / participation appearances; the PFR-inclusive count is its own restricted column",
       tags["rb"]["p_games_cur"] == "derived:participation+pbp" and tags["rb"]["p_any_snap_games"] == "derived:pbp+pfr_snaps"
       and [c for c in sch["rb"] if c.startswith("p_any_snap_")] == ["p_any_snap_games"],
       str((tags["rb"].get("p_games_cur"), tags["rb"].get("p_any_snap_games"))))
    ck("schema (F14b, c): the RB rows carry the hit + sack allowed and the OL listed out", "dq_hit_sack_rate_oe_ratio" in sch["rb"]
       and "ol_ol_listed_out" in sch["rb"] and sch["rb"]["ol_ol_listed_out"]["source_tag"] == "injuries" and "q_hit_sack_rate" in sch["qb"])
    ck("k frames (G3): 2023-2025 rows shrink with k_fit_only (2018-2024), 2026 rows with the full table -- the constant pinned",
       M.K_FRAMES == {2023: "fit_only", 2024: "fit_only", 2025: "fit_only", 2026: "full"}, str(M.K_FRAMES))
    # m3: the k frames -- a fit-only season reads k_fit_only, and the loaded table is restored afterwards
    _dd, meta2, _s2 = build_ds("ds_kframe", PLAYS, k_frames={2030: "fit_only"})
    ck("k frames (m3): a fit-only season's rows read k_fit_only; the table is restored after",
       "k_fit_only" in str(meta2.get("k_frames", {}).get(2030)) and RP.k_for("rb_ypc")[0] == 50.0 and "k_note" in meta2,
       str(meta2.get("k_frames")))


# ---- refresh -------------------------------------------------------------------------------------------
def _raw_copy(src, dest):
    with open(src, "rb") as a_:
        data = a_.read()
    if dest.endswith(".gz"):
        data = gzip.compress(data, mtime=0)
    with open(dest, "wb") as b_:
        b_.write(data)


def _s_refresh(ck, X):
    """refresh: an unchanged season is skipped; one season's bad file never stops the next (no network: fetch
    stubbed, freshness pinned)."""
    import deep_cache
    d, store, paths = X.d, X.store, X.paths
    saved = (deep_cache.CACHE_DIR, D._fresh, D.fetch)
    try:
        deep_cache.CACHE_DIR = os.path.join(d, "deep")
        deep_cache.save(D.KEY.format(season=2030), store)
        deep_cache.save(D.IDS_KEY, {"schema": D.SCHEMA, "ids": IDS})
        raw = os.path.join(d, "raw")
        os.makedirs(raw)
        for kind, src in (("pbp", paths["pbp"]), ("participation", paths["participation"]), ("pfr_rush", paths["pfr_rush"]),
                          ("pfr_def", paths["pfr_def"]), ("pfr_pass", paths["pfr_pass"]), ("injuries", paths["injuries"]),
                          ("snaps", paths["snaps"])):
            shutil.copyfile(src, os.path.join(raw, os.path.basename(R.url_for(kind, 2030))))
        shutil.copyfile(os.path.join(d, "ngs.csv.gz"), os.path.join(raw, os.path.basename(R.url_for("ngs_rush"))))
        D._fresh = lambda path, url, max_age_s=None: os.path.exists(path)

        def _nofetch(url, dest, **kw):
            raise RuntimeError("no network in guards")
        D.fetch = _nofetch
        s1 = R.refresh(seasons=[2030], raw_dir=raw)
        s2 = R.refresh(seasons=[2030], raw_dir=raw)
        ck("refresh: builds a missing aux store from the files on disk", s1["seasons"][2030].get("built") is True, str(s1))
        ck("refresh: the second run skips it (nothing changed, the aux matches the store)", "skipped" in s2["seasons"][2030], str(s2))
        ck("refresh: a season with no matchup store is an error, never a guess",
           "error" in R.refresh(seasons=[2031], raw_dir=raw)["seasons"][2031])
        # M3 / O3: 2025's pbp file is cut mid-stream, 2026's is whole -- 2026 must still build
        for s in (2025, 2026):
            st_s = D.build_season(s, paths["pbp"], paths["participation"], paths["ftn"], positions=POS)
            deep_cache.save(D.KEY.format(season=s), st_s)
            for kind in ("pbp", "pfr_rush", "pfr_def", "pfr_pass", "injuries", "snaps"):
                _raw_copy(paths[kind], os.path.join(raw, os.path.basename(R.url_for(kind, s))))
        cut = os.path.join(raw, os.path.basename(R.url_for("pbp", 2025)))
        with open(cut, "rb") as fh:
            blob = fh.read()
        with open(cut, "wb") as fh:
            fh.write(blob[:len(blob) // 2])
        n0 = X.notes.codes.count("RUSH-build")
        s3 = R.refresh(seasons=[2025, 2026], raw_dir=raw)
        ck("refresh: a truncated file fails its season with the error in the summary, ledgered RUSH-build",
           "error" in s3["seasons"].get(2025, {}) and X.notes.codes.count("RUSH-build") - n0 == 1, str(s3["seasons"].get(2025)))
        ck("refresh: the next season still builds", s3["seasons"].get(2026, {}).get("built") is True, str(s3["seasons"].get(2026)))
        _refresh_g1(ck, X, raw, paths)
    finally:
        deep_cache.CACHE_DIR, D._fresh, D.fetch = saved


def _refresh_g1(ck, X, raw, paths):
    """G1 (the data re-checks' Sonnet R1 / Opus R3): a source that fails to download leaves an aux that says it is
    incomplete (and stays due); a season with no pbp file saves nothing."""
    import deep_cache
    for s in (2023, 2024):
        deep_cache.save(D.KEY.format(season=s), D.build_season(s, paths["pbp"], paths["participation"], paths["ftn"], positions=POS))
        for kind in ("pbp", "participation", "pfr_rush", "pfr_def", "pfr_pass", "injuries", "snaps"):
            if (s, kind) in ((2024, "pfr_rush"), (2023, "pbp")):
                continue            # the failed downloads: the stubbed fetch raises, nothing is on disk
            _raw_copy(paths[kind], os.path.join(raw, os.path.basename(R.url_for(kind, s))))
    n0 = X.notes.codes.count("RUSH-incomplete")
    s4 = R.refresh(seasons=[2024], raw_dir=raw)["seasons"][2024]
    a24 = R.load_aux(2024)
    ck("refresh (G1): a failed PFR download saves an aux that says so -- complete False, the source absent, ledgered",
       s4.get("built") is True and a24 is not None and a24.get("complete") is False and a24.get("absent") == ["pfr_rush"]
       and s4.get("absent") == ["pfr_rush"] and not R.aux_complete(a24) and X.notes.codes.count("RUSH-incomplete") - n0 == 1, str(s4))
    T = RP.season_tallies(D.load_season(2024), a24, POS)
    na = X.notes.codes.count("RUSH-aux")
    S = RP.RushSnapshot(2024, 99, T, [], aux={2024: a24})
    ck("refresh (G1): such a season stamps 'incomplete' on every profile (not 'aligned'), ledgered RUSH-aux, and still reads "
       "what it has", S.rb(100004).get("aux") == {2024: "incomplete"} and X.notes.codes.count("RUSH-aux") - na == 1
       and S.rb(100004)["rushing"]["carries"] > 0, str(S.rb(100004).get("aux")))
    _raw_copy(paths["pfr_rush"], os.path.join(raw, os.path.basename(R.url_for("pfr_rush", 2024))))
    s4b = R.refresh(seasons=[2024], raw_dir=raw)["seasons"][2024]
    ck("refresh (G1): an incomplete aux is never skipped as done -- once the file is there the retry builds it complete",
       s4b.get("built") is True and R.aux_complete(R.load_aux(2024)), str(s4b))
    s3 = R.refresh(seasons=[2023], raw_dir=raw)["seasons"][2023]
    ck("refresh (G1): a season with no pbp file saves NOTHING and reports the error (the extras cannot align without it)",
       "error" in s3 and "play-by-play" in s3["error"] and R.load_aux(2023) is None, str(s3))
    # the NGS file is one file for every season: with it absent, every season from 2016 is incomplete
    deep_cache.save(D.KEY.format(season=2022), D.build_season(2022, paths["pbp"], paths["participation"], paths["ftn"], positions=POS))
    for kind in ("pbp", "participation", "pfr_rush", "pfr_def", "pfr_pass", "injuries", "snaps"):
        _raw_copy(paths[kind], os.path.join(raw, os.path.basename(R.url_for(kind, 2022))))
    ngs_raw = os.path.join(raw, os.path.basename(R.url_for("ngs_rush")))
    os.rename(ngs_raw, ngs_raw + ".gone")
    try:
        s2 = R.refresh(seasons=[2022], raw_dir=raw)["seasons"][2022]
    finally:
        os.rename(ngs_raw + ".gone", ngs_raw)
    ck("refresh (G1): a failed NGS download makes the season incomplete too (absent ['ngs_rush'])",
       s2.get("built") is True and s2.get("absent") == ["ngs_rush"] and not R.aux_complete(R.load_aux(2022)), str(s2))
    ck("expected sources by season: pfr_pass from 2021, participation through 2025, NGS from 2016",
       R.expected_sources(2020) == ["pbp", "participation", "pfr_rush", "pfr_def", "injuries", "snaps", "ngs_rush"]
       and R.expected_sources(2026) == ["pbp", "pfr_rush", "pfr_def", "pfr_pass", "injuries", "snaps", "ngs_rush"]
       and R.aux_complete({"complete": True}) and not R.aux_complete({}) and not R.aux_complete(None))


def _fk(metric):
    return RP.k_for(metric)[0]


def main():
    results = []

    def ck(name, cond, detail=""):
        results.append((name, bool(cond), detail))
    run(ck)
    bad = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -- {detail}"))
    print(f"{len(results) - len(bad)} passed, {len(bad)} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
