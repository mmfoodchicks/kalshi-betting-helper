"""nflverse play-by-play -> the compact PLAYER-level rows the coaching engine's
second revision is audited and fitted on (research/reports/nfl_coach_r2_evidence.md).
RESEARCH: nothing in production imports this.

R1's loader (research/nfl_coach_data.py) keeps 23 ints a play for the clock and
drops who had the ball; it is pinned by R1's registration and is not edited.
This module reads the same nflverse files (CC BY 4.0, release tag "pbp"; never
committed -- .gitignore holds the cache directory and the row caches) and keeps,
for every scrimmage snap, the ball carrier, the target, the passer, both head
coaches, the score, the clock, the field position and nflverse's expected pass
rate.

THE SEAL is R1's (`nfl_coach_data.sealed`: 2026 week 13 on, every postseason
and later season): a sealed row is skipped on its season, week and season-type
fields, before any other field of it is read, for every caller except R2's own
registered read (`unseal=_UNSEAL_TOKEN`, research/nfl_coach_r2_read.py after its
own date, clean-tree and R1-first checks). The row cache only ever holds unsealed
games: a sealed read never writes it.
"""
import csv
import gzip
import os
import pickle

from research import nfl_coach_data as CD

_UNSEAL_TOKEN = "nfl_coach_r2_read"      # research/nfl_coach_r2_read.py only
CACHE_VERSION = 4

# ---- one play: these ints, in this order --------------------------------------------
(Q_PER, Q_GSR, Q_HSR, Q_OFFH, Q_SD, Q_DOWN, Q_DIST, Q_YL, Q_KIND, Q_NH, Q_SG, Q_XP, Q_RUSH,
 Q_RECV, Q_PASSER, Q_YDS, Q_EPA, Q_SUCC, Q_TD, Q_CMP, Q_FUM, Q_INT, Q_WP, Q_DRIVE, Q_TWO,
 Q_GOAL, Q_PLAYID) = range(27)
ROW_W = 27

# kinds
K_RUN, K_SCR, K_PASS, K_SACK, K_KNEEL, K_SPIKE, K_PUNT, K_FG, K_TWO_RUN, K_TWO_PASS = range(1, 11)
SCRIMMAGE = (K_RUN, K_SCR, K_PASS, K_SACK)


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


def _kind(r):
    pt = r["play_type"]
    if r["two_point_attempt"] == "1":
        if pt == "run":
            return K_TWO_RUN
        if pt == "pass":
            return K_TWO_PASS
        return None
    if r["play_deleted"] == "1":
        return None
    if pt == "punt":
        return K_PUNT
    if pt == "field_goal":
        return K_FG
    if pt == "qb_kneel" or r["qb_kneel"] == "1":
        return K_KNEEL
    if pt == "qb_spike" or r["qb_spike"] == "1":
        return K_SPIKE
    if pt == "run":
        return K_SCR if r["qb_scramble"] == "1" else K_RUN
    if pt == "pass":
        if r["qb_scramble"] == "1":
            return K_SCR
        return K_SACK if r["sack"] == "1" else K_PASS
    return None


def cache_path(dirname, season):
    return os.path.join(dirname, f"nfl_coach_r2_plays_{int(season)}.pkl")


def load_season(dirname, season, unseal=None, only_sealed=False, use_cache=True):
    """{"games": [game, ...], "players": [gsis_id, ...]} for one season.

    A game: id, season, week, st (2 regular, 3 post), home, away, home_coach,
    away_coach, home_pts, away_pts, spread, total, date, plays (list of
    ROW_W-int lists). Player fields hold an index into "players" (-1 none).
    Sealed rows never pass unless `unseal` is R2's read token."""
    if only_sealed and unseal != _UNSEAL_TOKEN:
        raise RuntimeError("the sealed games are read by the registered reads only")
    cp = cache_path(dirname, season)
    cacheable = unseal is None and not only_sealed
    if cacheable and use_cache and os.path.exists(cp):
        with open(cp, "rb") as fh:
            d = pickle.load(fh)
        if d.get("v") == CACHE_VERSION:
            return d
    pid, players = {}, []

    def P(v):
        if not v or v == "NA":
            return -1
        k = pid.get(v)
        if k is None:
            k = pid[v] = len(players)
            players.append(v)
        return k

    games, cur = [], None
    with gzip.open(CD.path(dirname, season), "rt", newline="") as fh:
        rd = csv.reader(fh)
        hdr = next(rd)
        ix = {h: j for j, h in enumerate(hdr)}
        j_season, j_week, j_st = ix["season"], ix["week"], ix["season_type"]
        need = ("game_id", "home_team", "away_team", "posteam", "home_coach", "away_coach", "qtr",
                "game_seconds_remaining", "half_seconds_remaining", "score_differential", "down",
                "ydstogo", "yardline_100", "play_type", "two_point_attempt", "play_deleted",
                "aborted_play", "qb_kneel", "qb_spike", "qb_scramble", "sack", "no_huddle", "shotgun",
                "xpass", "rusher_player_id", "receiver_player_id", "passer_player_id", "yards_gained",
                "epa", "success", "rush_touchdown", "pass_touchdown", "complete_pass", "fumble_lost",
                "interception", "wp", "drive", "two_point_conv_result", "goal_to_go", "home_score",
                "away_score", "spread_line", "total_line", "game_date", "play_id")
        cols = [(k, ix[k]) for k in need]
        for raw in rd:
            # the seal: decided on these three fields, before anything else is read
            is_sealed = CD.sealed(raw[j_season], raw[j_week], raw[j_st])
            if is_sealed and unseal != _UNSEAL_TOKEN:
                continue
            if only_sealed and not is_sealed:
                continue
            r = {k: raw[j] for k, j in cols}
            if cur is None or r["game_id"] != cur["id"]:
                cur = {"id": r["game_id"], "season": _i(raw[j_season]), "week": _i(raw[j_week]),
                       "st": 2 if raw[j_st] == "REG" else 3, "home": r["home_team"],
                       "away": r["away_team"], "home_coach": r["home_coach"],
                       "away_coach": r["away_coach"], "home_pts": _i(r["home_score"]),
                       "away_pts": _i(r["away_score"]), "spread": _f(r["spread_line"]),
                       "total": _f(r["total_line"]), "date": r["game_date"], "plays": []}
                games.append(cur)
            k = _kind(r)
            if k is None or not r["posteam"] or r["posteam"] == "NA":
                continue
            xp = _f(r["xpass"])
            epa = _f(r["epa"])
            wp = _f(r["wp"])
            two = 0
            if k in (K_TWO_RUN, K_TWO_PASS):
                two = 1 if r["two_point_conv_result"] == "success" else 0
            cur["plays"].append([
                _i(r["qtr"]), _i(r["game_seconds_remaining"]), _i(r["half_seconds_remaining"]),
                1 if r["posteam"] == r["home_team"] else 0, _i(r["score_differential"]),
                _i(r["down"]), _i(r["ydstogo"]), _i(r["yardline_100"], -1), k,
                _i(r["no_huddle"]), _i(r["shotgun"]), -1 if xp is None else int(round(xp * 1000)),
                P(r["rusher_player_id"]), P(r["receiver_player_id"]), P(r["passer_player_id"]),
                _i(r["yards_gained"]), 0 if epa is None else int(round(epa * 1000)),
                _i(r["success"]), 1 if (r["rush_touchdown"] == "1" or r["pass_touchdown"] == "1") else 0,
                _i(r["complete_pass"]), _i(r["fumble_lost"]), _i(r["interception"]),
                -1 if wp is None else int(round(wp * 1000)), _i(r["drive"]), two,
                _i(r["goal_to_go"]), _i(r["play_id"])])
    d = {"v": CACHE_VERSION, "season": int(season), "games": games, "players": players}
    if cacheable and use_cache:
        tmp = cp + ".tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(d, fh, protocol=4)
        os.replace(tmp, cp)
    return d


def positions(players_csv):
    """{gsis_id: position} from nflverse's players.csv (CC BY 4.0, release tag
    "players"; never committed). FB is kept apart from RB."""
    out = {}
    with open(players_csv, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            g = r.get("gsis_id")
            if g:
                out[g] = (r.get("position") or r.get("position_group") or "").upper()
    return out
