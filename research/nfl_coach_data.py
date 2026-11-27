"""nflverse play-by-play -> the rows the experimental coaching engine is fitted
on (nfl_coach_sim, research/reports/nfl_coach_r1.md). RESEARCH: nothing in
production imports this.

Input: a directory of nflverse-data play_by_play_<season>.csv.gz files (CC BY
4.0, release tag "pbp" -- the files nfl_matchup_data.refresh fetches). They
are never committed; .gitignore holds the local cache directory.

One row a play, 23 ints. The FIRST FOURTEEN are nfl_pbp's play record (same
codes, same flags, same meaning), so research.nfl_deep_fit.build_tables runs
on these games unchanged and the CONTROL arm is production's own fit on the
same seasons. Two deliberate differences from the ESPN records, both because
nflverse says what ESPN does not:

  * A change of possession is always flagged F_TURNOVER, a failed fourth down
    included. ESPN does not flag a turnover on downs, so nfl_deep_fit read the
    NEXT team's spot as the play's end spot (nfl_viewer.md 6.2: 30-yard sack
    "gains", 15-yard fourth-down "losses"). With the flag the fit falls back to
    the play's own yards. The production tables are NOT refitted here (the
    owner: after the deep weight's 100-game read); these tables are this
    engine's own.
  * A fumble lost keeps its play's code (rush, completion, sack) with the
    fumble flag; the spot the defence takes over is measured from the END of
    the play (fum_spot is rebuilt in nfl_coach_fit, not by build_tables).

The other nine fields are what the clock work needs and ESPN's records lack:
each side's timeouts before the row, who called a timeout, the half, the
score, an onside kick and its recovery, the play clock, a scramble.

THE SEAL. 2026 week 13 on (and every later season) is the R1 test set
(research/data/nfl_coach/prereg_r1.json). `load_season` drops those rows
before a single field of them is parsed unless `unseal` is the registered
read's own token; only research/nfl_coach_r1_read.py passes it, after its
date and clean-tree checks.
"""
import csv
import gzip
import os
import pickle

import nfl_pbp as P

SEAL_SEASON, SEAL_WEEK = 2026, 13
_UNSEAL_TOKEN = "nfl_coach_r1_read"       # research/nfl_coach_r1_read.py only

(R_PER, R_CLK, R_OFF, R_DOWN, R_DIST, R_YTG, R_CODE, R_YDS, R_FLAGS, R_SD, R_EDOWN, R_EDIST,
 R_EYTG, R_PTS, R_TOH, R_TOA, R_TOTEAM, R_HALF, R_SH, R_SA, R_ONSIDE, R_PCLK, R_SCR) = range(23)
ROW_W = 23

_DRIVE = {"Touchdown": "TD", "Field goal": "FG", "Punt": "PUNT", "Turnover": "INT",
          "Turnover on downs": "DOWNS", "Missed field goal": "MISSED FG",
          "End of half": "END OF HALF", "Safety": "SAFETY", "Opp touchdown": "INT TD"}


def sealed(season, week, season_type="REG"):
    """True for a game of the R1 test set."""
    season = int(season)
    if season > SEAL_SEASON:
        return True
    if season < SEAL_SEASON:
        return False
    return season_type != "REG" or int(week) >= SEAL_WEEK


def _i(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def _f(v, d=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return d
    return d if x != x else x


def path(dirname, season):
    return os.path.join(dirname, f"play_by_play_{int(season)}.csv.gz")


def load_season(dirname, season, unseal=None, only_sealed=False, weeks=None):
    """[game, ...] for one season, in file order. Sealed games are skipped
    unless `unseal` is the read's token; `only_sealed` (with the token) keeps
    ONLY them; `weeks` keeps a set of weeks."""
    if only_sealed and unseal != _UNSEAL_TOKEN:
        raise RuntimeError("the sealed R1 games are read by research/nfl_coach_r1_read.py only")
    games, cur, raw = [], None, None
    with gzip.open(path(dirname, season), "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            is_sealed = sealed(row["season"], row["week"], row["season_type"])
            if is_sealed and unseal != _UNSEAL_TOKEN:
                continue
            if only_sealed and not is_sealed:
                continue
            if weeks is not None and _i(row["week"]) not in weeks:
                continue
            if cur is None or row["game_id"] != cur["id"]:
                if cur is not None:
                    _finish(cur, raw)
                    games.append(cur)
                cur = {"id": row["game_id"], "season": _i(row["season"]), "week": _i(row["week"]),
                       "st": 2 if row["season_type"] == "REG" else 3,
                       "home": row["home_team"], "away": row["away_team"],
                       "home_pts": _i(row["home_score"]), "away_pts": _i(row["away_score"]),
                       "spread": _f(row.get("spread_line")), "total_line": _f(row.get("total_line")),
                       "date": row.get("game_date") or "", "neutral": row.get("location") == "Neutral",
                       "scoring": []}
                raw = []
            raw.append(row)
    if cur is not None:
        _finish(cur, raw)
        games.append(cur)
    return games


def _code(r):
    """nfl_pbp's play class for one nflverse row, or None for a row that is not
    a play (a try, a quarter marker, an administrative no-play)."""
    pt = r["play_type"]
    desc = r["desc"] or ""
    if r["two_point_attempt"] == "1" or pt == "extra_point":
        return None
    if pt == "no_play" or pt == "":
        if r["timeout"] == "1" and not r["posteam"]:
            return P.TIMEOUT
        if desc.startswith("Timeout"):
            return P.TIMEOUT
        low = desc.lower()
        if low.startswith("two-minute warning"):
            return P.TWO_MIN
        if low.startswith(("end quarter", "end game", "end of")):
            return P.END_PERIOD
        if pt == "no_play" and r["penalty"] == "1" and r["down"] not in ("", "NA"):
            return P.PENALTY
        return None
    if pt == "kickoff":
        return P.KICKOFF
    if pt == "punt":
        return P.PUNT_BLOCK if r["punt_blocked"] == "1" else P.PUNT
    if pt == "field_goal":
        res = r["field_goal_result"]
        return P.FG_GOOD if res == "made" else P.FG_BLOCK if res == "blocked" else P.FG_MISS
    if r["down"] in ("", "NA"):
        return None
    if pt == "qb_kneel":
        return P.KNEEL
    if pt == "qb_spike":
        return P.SPIKE
    if pt == "pass":
        if r["sack"] == "1":
            return P.SACK
        if r["interception"] == "1":
            return P.INT
        if r["complete_pass"] == "1":
            return P.PASS_COMP
        if r["incomplete_pass"] == "1":
            return P.PASS_INC
        return P.PASS_COMP if _i(r["yards_gained"]) != 0 else P.PASS_INC
    if pt == "run":
        return P.RUSH
    return None


_STATE = frozenset((P.RUSH, P.PASS_COMP, P.PASS_INC, P.SACK, P.INT, P.PENALTY, P.KNEEL, P.SPIKE,
                    P.PUNT, P.PUNT_BLOCK, P.FG_GOOD, P.FG_MISS, P.FG_BLOCK))


def _finish(g, raw):
    """Raw csv rows of one game -> g["rows"] (tuples), g["scoring"], g["drives"]."""
    home = g["home"]
    rows = []
    toh = toa = 3
    sh = sa = 0
    half = 1
    drives = {}
    for r in raw:
        qtr = _i(r["qtr"])
        hf = 1 if qtr <= 2 else 2 if qtr <= 4 else 3
        if hf != half:
            half = hf
        if r["home_timeouts_remaining"] not in ("", "NA"):
            toh, toa = _i(r["home_timeouts_remaining"]), _i(r["away_timeouts_remaining"])
        pos = r["posteam"]
        off = -1 if not pos or pos == "NA" else (0 if pos == home else 1)
        if off >= 0 and r["posteam_score"] not in ("", "NA"):
            ps, ds = _i(r["posteam_score"]), _i(r["defteam_score"])
            sh, sa = (ps, ds) if off == 0 else (ds, ps)
        # the try: nfl_pbp's scoring record (kind 1-4), offence-minus-defence after the six
        if r["extra_point_result"] not in ("", "NA") or r["two_point_attempt"] == "1":
            if off >= 0:
                sd6 = (sh - sa) if off == 0 else (sa - sh)
                if r["two_point_attempt"] == "1":
                    kind = 3 if r["two_point_conv_result"] == "success" else 4
                else:
                    kind = 1 if r["extra_point_result"] == "good" else 2
                g["scoring"].append((qtr, _i(r["quarter_seconds_remaining"]), off, kind, sd6))
            continue
        code = _code(r)
        if code is None:
            continue
        clk = _i(r["quarter_seconds_remaining"])
        if code in (P.TIMEOUT, P.TWO_MIN, P.END_PERIOD):
            tt = r["timeout_team"]
            tteam = -1 if code != P.TIMEOUT or not tt or tt == "NA" else (0 if tt == home else 1)
            rows.append([qtr, clk, max(0, off), 0, 0, 0, code, 0, 0, 0, -1, -1, -1, 0,
                         toh, toa, tteam, half, sh, sa, 0, 0, 0])
            continue
        if off < 0:
            continue
        fl = 0
        td = r["touchdown"] == "1"
        td_off = td and r["td_team"] == pos
        if td:
            fl |= P.F_TD | P.F_SCORING
        if r["first_down"] == "1" and not td:
            fl |= P.F_FIRST
        if r["penalty"] == "1":
            fl |= P.F_PEN_ON_PLAY
        if r["out_of_bounds"] == "1":
            fl |= P.F_OOB
        if r["no_huddle"] == "1":
            fl |= P.F_NOHUDDLE
        if r["shotgun"] == "1":
            fl |= P.F_SHOTGUN
        if r["fumble_lost"] == "1":
            fl |= P.F_FUMBLE | P.F_FUMBLE_LOST | P.F_TURNOVER
        if code == P.INT:
            fl |= P.F_TURNOVER
        if code in (P.PASS_COMP, P.PASS_INC, P.SACK, P.INT, P.SPIKE):
            fl |= P.F_PASS_PLAY
        if r["fourth_down_failed"] == "1":
            fl |= P.F_TURNOVER
        sd = (sh - sa) if off == 0 else (sa - sh)
        pts = 0
        if r["posteam_score_post"] not in ("", "NA"):
            pts = (_i(r["posteam_score_post"]) - _i(r["defteam_score_post"])) - sd
        if td and not td_off and pts > 0:
            pts = -pts
        onside = 0
        if code == P.KICKOFF and "onside" in (r["desc"] or "").lower():
            onside = 2 if r["own_kickoff_recovery"] == "1" else 1
        rows.append([qtr, clk, off, _i(r["down"]), _i(r["ydstogo"]), _i(r["yardline_100"]), code,
                     _i(r["yards_gained"]), fl, sd, -1, -1, -1, pts,
                     toh, toa, -1, half, sh, sa, onside, _i(r["play_clock"]),
                     1 if r["qb_scramble"] == "1" else 0])
        dk = r["fixed_drive"]
        if dk not in ("", "NA") and code in P.SNAPS:
            d = drives.setdefault(dk, [off, 0, r["fixed_drive_result"]])
            if code in P.PLAYS:
                d[1] += 1
    # end states: the next row that carries a down (same half), nfl_pbp's meaning
    n = len(rows)
    for i, p in enumerate(rows):
        code = p[R_CODE]
        if code in (P.TIMEOUT, P.TWO_MIN, P.END_PERIOD):
            continue
        j = i + 1
        while j < n and rows[j][R_CODE] in (P.TIMEOUT, P.TWO_MIN, P.END_PERIOD):
            j += 1
        if j >= n or rows[j][R_HALF] != p[R_HALF] or rows[j][R_CODE] not in _STATE:
            continue
        q = rows[j]
        same = q[R_OFF] == p[R_OFF]
        if code == P.KICKOFF:
            # nflverse's posteam on a kickoff is the RECEIVER; an onside kick is the engine's own table
            if same and not p[R_ONSIDE] and not (p[R_FLAGS] & P.F_TD):
                p[R_EYTG] = q[R_YTG]
            continue
        if code in (P.PUNT, P.PUNT_BLOCK, P.INT, P.FG_MISS, P.FG_BLOCK):
            if not same:
                p[R_EDOWN], p[R_EDIST], p[R_EYTG] = q[R_DOWN], q[R_DIST], q[R_YTG]
            continue
        if code == P.FG_GOOD:
            continue
        if not same:
            p[R_FLAGS] |= P.F_TURNOVER          # downs, a fumble, a muffed exchange
        p[R_EDOWN], p[R_EDIST], p[R_EYTG] = q[R_DOWN], q[R_DIST], q[R_YTG]
        if code == P.PENALTY:
            # the no-play's net yards for the offence (the engine moves the spot by it)
            p[R_YDS] = (p[R_YTG] - q[R_YTG]) if same else 0
            if same and q[R_DOWN] == 1 and p[R_YDS] > 0:
                p[R_FLAGS] |= P.F_FIRST
    g["rows"] = [tuple(p) for p in rows]
    g["drives"] = [(d[0], 0, 0, -1, d[1], 0, _DRIVE.get(d[2], (d[2] or "").upper()), 0)
                   for d in drives.values()]
    g["ot"] = any(p[R_PER] >= 5 for p in rows)


def cached(dirname, season, cache_dir=None):
    """load_season through a pickle beside the csv (unsealed rows only)."""
    cache_dir = cache_dir or dirname
    pk = os.path.join(cache_dir, f"nfl_coach_rows_{int(season)}.pkl")
    src = path(dirname, season)
    if os.path.exists(pk) and os.path.getmtime(pk) >= os.path.getmtime(src):
        with open(pk, "rb") as fh:
            return pickle.load(fh)
    games = load_season(dirname, season)
    with open(pk, "wb") as fh:
        pickle.dump(games, fh, protocol=4)
    return games


def pbp_store(games):
    """The games as an nfl_pbp season store (what nfl_deep_fit.build_tables and
    nfl_pbp.team_units read): the first fourteen fields of every row."""
    import array
    out = {}
    for g in games:
        a = array.array("i")
        for p in g["rows"]:
            a.extend(p[:P.PLAY_W])
        out[g["id"]] = {"id": g["id"], "season": g["season"], "st": g["st"], "week": g["week"],
                        "home": g["home"], "away": g["away"], "home_pts": g["home_pts"],
                        "away_pts": g["away_pts"], "ot": g["ot"], "plays": a, "drives": g["drives"],
                        "scoring": g["scoring"], "neutral": g["neutral"]}
    return {"games": out}
