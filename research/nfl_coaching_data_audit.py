"""What nflverse's play-by-play holds for a coaching / clock-management engine,
and how big the signals are. RESEARCH, read-only: it measures, fits nothing
and feeds no production path (research/reports/nfl_coaching_audit.md).

Input: a directory of nflverse-data play_by_play_<season>.csv.gz files (CC BY
4.0; release tag "pbp", the files nfl_matchup_data.refresh already fetches and
then drops). Pure Python, one streaming pass a season, ~15 s a season.

  python3 -m research.nfl_coaching_data_audit <dir> [--seasons 2023,2024,2025,2026]
          [--out research/data/nfl_coaching_audit.json]

What is measured (regular season unless said):
  columns   presence and fill rate of every field the owner's phases name
  coaches   head coaches per season, mid-season changes; coordinators: no column
  timeouts  used per team-half, when, and the trailing defence's use after an
            in-bounds play late in the fourth quarter
  kneel     nfl_deep_sim's kneel rule (fourth quarter, leading,
            clk <= 40 x (4 - down) + 5) against what teams did, by the
            defence's timeouts left -- the rule cannot see them
  urgency   pass rate and seconds to the next snap in finer clock x score
            cells than the engine's three time states and three tempos
  coach     split-half reliability and year-over-year persistence (same head
            coach against a new one) of neutral pass rate over expected,
            fourth-down go rate over the league cell, no-huddle rate and pace
  misc      spikes, two-point tries, onside kicks (and the engine's onside
            rule), overtime, delay of game, snaps and points inside 2:00

The engine's own figures for the same end-of-half statistics come from
research/nfl_coaching_engine_probe.py.
"""
import collections
import csv
import gzip
import json
import math
import os
import sys

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coaching_audit.json")
FIELDS = ("home_coach", "away_coach", "home_timeouts_remaining", "away_timeouts_remaining",
          "posteam_timeouts_remaining", "defteam_timeouts_remaining", "timeout", "timeout_team",
          "no_huddle", "shotgun", "qb_kneel", "qb_spike", "qb_scramble", "qb_dropback",
          "air_yards", "pass_location", "pass_length", "run_location", "run_gap",
          "two_point_attempt", "two_point_conv_result", "kickoff_attempt", "own_kickoff_recovery",
          "play_clock", "half_seconds_remaining", "game_seconds_remaining", "score_differential",
          "wp", "xpass", "pass_oe", "out_of_bounds", "penalty_type", "goal_to_go",
          "end_clock_time", "time_of_day", "drive_play_count", "series_result", "roof", "temp", "wind",
          "div_game", "spread_line", "total_line",
          # not in the file -- listed so the table says so
          "offensive_coordinator", "defensive_coordinator", "offense_personnel", "offense_formation",
          "defenders_in_box", "onside_kick")
USE = ("game_id", "season_type", "week", "home_team", "away_team", "posteam", "defteam", "qtr", "down",
       "ydstogo", "yardline_100", "quarter_seconds_remaining", "half_seconds_remaining",
       "score_differential", "play_type", "desc", "pass", "rush", "qb_kneel", "qb_spike", "no_huddle",
       "timeout", "timeout_team", "home_timeouts_remaining", "away_timeouts_remaining",
       "posteam_timeouts_remaining", "defteam_timeouts_remaining", "out_of_bounds", "touchdown",
       "interception", "fumble_lost", "penalty", "wp", "xpass", "two_point_attempt",
       "extra_point_attempt", "kickoff_attempt", "own_kickoff_recovery", "home_coach", "away_coach",
       "posteam_score_post", "defteam_score_post", "play_clock", "penalty_type", "complete_pass",
       "sack", "field_goal_attempt", "punt_attempt", "game_half", "qb_scramble",
       "rusher_player_id", "receiver_player_id", "passer_player_id")


def _i(v, d=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _ss(sd):
    """nfl_pbp.score_state."""
    return 0 if sd <= -9 else 1 if sd < 0 else 2 if sd == 0 else 3 if sd <= 8 else 4


def _q4bin(clk):
    return ("15:00-10:00" if clk > 600 else "10:00-5:00" if clk > 300 else "5:00-3:00" if clk > 180
            else "3:00-2:00" if clk > 120 else "2:00-1:00" if clk > 60 else "1:00-0:00")


def _q2bin(clk):
    return ("15:00-5:00" if clk > 300 else "5:00-2:00" if clk > 120 else "2:00-1:00" if clk > 60
            else "1:00-0:00")


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy), 3)


def _acc(d, key, num, den=1.0):
    c = d.setdefault(key, [0.0, 0.0])
    c[0] += num
    c[1] += den


def _go_cell(ytg, y100):
    return (1 if ytg <= 1 else 2 if ytg == 2 else 3 if ytg <= 4 else 4 if ytg <= 6 else 5 if ytg <= 9 else 6,
            min(9, (y100 - 1) // 10))


def _scripts(x):
    """The scripts a scrimmage play belongs to (a play can be in several)."""
    qtr, down = _i(x["qtr"], 0), _i(x["down"], 0)
    sd, hs = _i(x["score_differential"], 0), _i(x["half_seconds_remaining"], 0)
    wp = _f(x["wp"])
    out = ["all"]
    if wp is not None and 0.2 <= wp <= 0.8 and hs > 120 and qtr <= 4:
        out.append("neutral")
    if qtr == 4 and sd >= 9:
        out.append("leading 9+, Q4")
    if qtr >= 3 and sd <= -9:
        out.append("trailing 9+, 2nd half")
    if hs <= 120 and qtr in (2, 4):
        out.append("inside 2:00")
    if down == 3 and _i(x["ydstogo"], 0) >= 5:
        out.append("3rd and 5+")
    if _i(x["yardline_100"], 99) <= 5:
        out.append("inside the 5")
    return out


def _usage(games, season, usage, role_games):
    """Who gets the ball by script. A 'back' is a non-quarterback with more
    season carries than targets; a team's lead back its top carrier among
    them, its top target its most-targeted player. League-pooled shares, and
    per team-game the lead back's share of the backs' touches in passing
    situations (3rd and 5+, inside 2:00, trailing 9+ after half-time)."""
    car, tgt, qb = collections.Counter(), collections.Counter(), collections.Counter()
    plays = []
    gi_of = collections.Counter()
    for rows in games.values():
        if rows[0]["season_type"] != "REG":
            continue
        gix = {}
        for t in (rows[0]["home_team"], rows[0]["away_team"]):
            gix[t] = gi_of[t]
            gi_of[t] += 1
        for x in rows:
            if x["play_type"] not in ("run", "pass") or x["two_point_attempt"] == "1" or not x["posteam"]:
                continue
            t = x["posteam"]
            if x["passer_player_id"]:
                qb[(t, x["passer_player_id"])] += 1
            if x["rusher_player_id"]:
                car[(t, x["rusher_player_id"])] += 1
            if x["receiver_player_id"]:
                tgt[(t, x["receiver_player_id"])] += 1
            plays.append((t, gix.get(t, 0), x["rusher_player_id"], x["receiver_player_id"], _scripts(x)))
    backs = {k for k, n in car.items() if qb[k] < 20 and n > tgt[k]}
    lead, top = {}, {}
    for (t, pid), n in car.items():
        if (t, pid) in backs and n > car.get((t, lead.get(t)), 0):
            lead[t] = pid
    for (t, pid), n in tgt.items():
        if n > tgt.get((t, top.get(t)), 0):
            top[t] = pid
    for t, gi, ru, rc, scripts in plays:
        passing = any(s in scripts for s in ("3rd and 5+", "inside 2:00", "trailing 9+, 2nd half"))
        for sc in scripts:
            if ru and (t, ru) in backs:
                _acc(usage, ("lead back share of backs' carries", sc), ru == lead.get(t))
            if rc:
                _acc(usage, ("backs' share of targets", sc), (t, rc) in backs)
                _acc(usage, ("lead back share of targets", sc), rc == lead.get(t))
                _acc(usage, ("top target's share of targets", sc), rc == top.get(t))
        pid = ru or rc
        if passing and pid and (t, pid) in backs:
            g = role_games.setdefault((season, t), {}).setdefault(gi, [0.0, 0.0])
            g[0] += pid == lead.get(t)
            g[1] += 1


def read_season(path):
    """Rows of one season, the columns in USE, grouped by game in file order;
    and the fill table over scrimmage plays."""
    games = collections.OrderedDict()
    fill = {f: [0, 0] for f in FIELDS}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        r = csv.reader(fh)
        hdr = next(r)
        ix = {h: i for i, h in enumerate(hdr)}
        present = {f: (f in ix) for f in FIELDS}
        use = [(c, ix[c]) for c in USE if c in ix]
        fi = [(f, ix[f]) for f in FIELDS if f in ix]
        pt_i, st_i = ix["play_type"], ix["season_type"]
        for row in r:
            if row[st_i] == "REG" and row[pt_i] in ("run", "pass"):
                for f, i in fi:
                    c = fill[f]
                    c[1] += 1
                    c[0] += row[i] not in ("", "NA")
            d = {c: row[i] for c, i in use}
            games.setdefault(d["game_id"], []).append(d)
    return games, present, fill, len(hdr)


def audit(pbp_dir, seasons):
    out = {"seasons": {}, "source": "nflverse-data play_by_play (CC BY 4.0)"}
    # pooled accumulators
    kneel = {}            # (rule, def timeouts) -> [kneels, snaps]
    kneel_q = collections.Counter()
    to_def = {}           # (clock bin, timeouts left, lead bucket) -> [timeouts, chances]
    to_used = collections.Counter()
    to_when = collections.Counter()
    to_role = collections.Counter()
    q4_pass, q4_dt, q2_pass, q2_dt = {}, {}, {}, {}
    onside = collections.Counter()
    misc = collections.Counter()
    win = {"q2_snaps": 0, "q4_snaps": 0, "q2_pts": 0, "q4_pts": 0, "games": 0}
    fourth_rows = []      # (season, team, game index, cell, go)
    neutral = {}          # (season, team) -> {game index: [sum pass-xpass, n, nohuddle, dt sum, dt n]}
    coach_of = {}         # (season, team) -> Counter of head coaches
    usage = {}            # (what, script) -> [numerator, denominator], league-pooled
    role_games = {}       # (season, team) -> {game index: [lead back's passing-situation touches, backs']}
    for season in seasons:
        path = os.path.join(pbp_dir, f"play_by_play_{season}.csv.gz")
        if not os.path.exists(path):
            print(f"[audit] missing {path}", file=sys.stderr)
            continue
        games, present, fill, ncol = read_season(path)
        S = out["seasons"][str(season)] = {
            "columns": ncol, "games": len(games),
            "fields": {f: (None if not present[f] else round(fill[f][0] / fill[f][1], 4) if fill[f][1] else 0.0)
                       for f in FIELDS}}
        s_cnt = collections.Counter()
        coaches = set()
        gi_of = collections.Counter()
        for gid, rows in games.items():
            reg = rows[0]["season_type"] == "REG"
            home, away = rows[0]["home_team"], rows[0]["away_team"]
            if any(_i(x["qtr"], 0) >= 5 for x in rows):
                s_cnt["ot_reg" if reg else "ot_post"] += 1
            if not reg:
                continue
            s_cnt["reg_games"] += 1
            win["games"] += 1
            for team, col in ((home, "home_coach"), (away, "away_coach")):
                c = rows[0].get(col) or ""
                coaches.add(c)
                coach_of.setdefault((season, team), collections.Counter())[c] += 1
            gix = {home: gi_of[home], away: gi_of[away]}
            gi_of[home] += 1
            gi_of[away] += 1
            # ---- timeouts used per team-half
            for half in ("Half1", "Half2"):
                hr = [x for x in rows if x["game_half"] == half]
                for col in ("home_timeouts_remaining", "away_timeouts_remaining"):
                    v = [_i(x[col]) for x in hr if _i(x[col]) is not None]
                    if v:
                        to_used[(half, 3 - min(v))] += 1
            # ---- points and snaps inside 2:00 of each half
            for half, key in (("Half1", "q2"), ("Half2", "q4")):
                before = after = None
                for x in rows:
                    if x["game_half"] != half or _i(x["qtr"], 0) >= 5:
                        continue
                    a, b = _i(x["posteam_score_post"]), _i(x["defteam_score_post"])
                    hs = _i(x["half_seconds_remaining"])
                    if a is None or b is None or hs is None:
                        continue
                    if hs > 120:
                        before = a + b
                    after = a + b
                    if hs <= 120 and x["play_type"] in ("run", "pass") and x["two_point_attempt"] != "1":
                        win[key + "_snaps"] += 1
                if before is not None and after is not None:
                    win[key + "_pts"] += after - before
            for k, x in enumerate(rows):
                pt = x["play_type"]
                qtr = _i(x["qtr"], 0)
                clk = _i(x["quarter_seconds_remaining"], 0)
                down = _i(x["down"], 0)
                sd = _i(x["score_differential"])
                off, dfn = x["posteam"], x["defteam"]
                # ---- timeouts: when, and by whom
                if x["timeout"] == "1" and x["timeout_team"]:
                    s_cnt["timeouts"] += 1
                    hs = _i(x["half_seconds_remaining"], 0)
                    to_when[(x["game_half"], "<=2:00" if hs <= 120 else "2:00-5:00" if hs <= 300 else ">5:00")] += 1
                    nxt = next((y for y in rows[k + 1:k + 4] if y["posteam"]), None)
                    if nxt is not None:
                        role = "offence" if nxt["posteam"] == x["timeout_team"] else "defence"
                        nsd = _i(nxt["score_differential"], 0)
                        mine = nsd if role == "offence" else -nsd
                        to_role[(role, "trailing" if mine < 0 else "tied" if mine == 0 else "leading",
                                 "<=2:00" if hs <= 120 else ">2:00")] += 1
                if x["penalty_type"] == "Delay of Game":
                    s_cnt["delay_of_game"] += 1
                if x["kickoff_attempt"] == "1":
                    s_cnt["kickoffs"] += 1
                    is_on = "onside" in (x["desc"] or "").lower()
                    # posteam on a kickoff is the RECEIVING team: the kicker trails when sd > 0
                    rule = qtr == 4 and clk <= 180 and sd is not None and 1 <= sd <= 16
                    onside[("onside" if is_on else "deep", "rule" if rule else "no_rule")] += 1
                    if is_on:
                        onside["recovered"] += x["own_kickoff_recovery"] == "1"
                if x["two_point_attempt"] == "1":
                    s_cnt["two_point"] += 1
                if x["extra_point_attempt"] == "1":
                    s_cnt["xp"] += 1
                if x["qb_spike"] == "1":
                    s_cnt["spikes"] += 1
                    misc[("spike_q", qtr)] += 1
                if x["qb_kneel"] == "1":
                    s_cnt["kneels"] += 1
                    kneel_q[qtr] += 1
                if pt not in ("run", "pass", "qb_kneel", "qb_spike", "punt", "field_goal") or sd is None or not off:
                    continue
                if x["two_point_attempt"] == "1" or down < 1:
                    continue
                # ---- the engine's kneel rule against the play
                if qtr == 4 and sd > 0 and pt in ("run", "pass", "qb_kneel"):
                    rule = clk <= 40 * (4 - down) + 5
                    dto = _i(x["defteam_timeouts_remaining"], -1)
                    _acc(kneel, ("rule" if rule else "no_rule", dto), x["qb_kneel"] == "1")
                # ---- fourth-down choice
                if down == 4 and pt in ("run", "pass", "punt", "field_goal") and x["qb_kneel"] != "1":
                    wp = _f(x["wp"])
                    if wp is not None and 0.1 <= wp <= 0.9 and not (qtr == 4 and clk <= 300) and qtr <= 4:
                        fourth_rows.append((season, off, gix.get(off, 0),
                                            _go_cell(_i(x["ydstogo"], 10), _i(x["yardline_100"], 50)),
                                            1 if pt in ("run", "pass") else 0))
                if pt not in ("run", "pass"):
                    continue
                is_pass = x["pass"] == "1"
                # ESPN (and so nfl_deep_sim) files a scramble as a rush: the urgency
                # tables use that definition so the engine probe compares like with like
                is_pass_e = is_pass and x["qb_scramble"] != "1"
                # ---- seconds to the next snap after a play that leaves the clock running
                dt = None
                if (x["out_of_bounds"] != "1" and x["touchdown"] != "1" and x["interception"] != "1"
                        and x["fumble_lost"] != "1" and x["penalty"] != "1"
                        and (x["rush"] == "1" or x["complete_pass"] == "1" or x["sack"] == "1")
                        and k + 1 < len(rows)):
                    y = rows[k + 1]
                    if (y["posteam"] == off and y["play_type"] in ("run", "pass", "qb_spike")
                            and _i(y["qtr"], 0) == qtr and y["timeout"] != "1"):
                        c2 = _i(y["quarter_seconds_remaining"], -1)
                        if 0 <= clk - c2 <= 60 and not (qtr in (2, 4) and clk > 120 >= c2):
                            dt = clk - c2
                if qtr == 4:
                    key = (_q4bin(clk), _ss(sd))
                    _acc(q4_pass, key, is_pass_e)
                    if dt is not None:
                        _acc(q4_dt, key, dt)
                elif qtr == 2:
                    key = (_q2bin(clk), _ss(sd))
                    _acc(q2_pass, key, is_pass_e)
                    if dt is not None:
                        _acc(q2_dt, key, dt)
                # ---- the trailing defence's timeout after an in-bounds play, late
                if (qtr == 4 and clk <= 300 and sd > 0 and x["out_of_bounds"] != "1" and x["touchdown"] != "1"
                        and x["interception"] != "1" and x["fumble_lost"] != "1" and x["penalty"] != "1"
                        and (x["rush"] == "1" or x["complete_pass"] == "1" or x["sack"] == "1")
                        and k + 1 < len(rows)):
                    dto = _i(x["defteam_timeouts_remaining"], -1)
                    y = rows[k + 1]
                    called = y["timeout"] == "1" and y["timeout_team"] == dfn
                    _acc(to_def, (_q4bin(clk), min(dto, 1) if dto >= 0 else -1, "1-8" if sd <= 8 else "9-16" if sd <= 16 else "17+"),
                         called)
                # ---- neutral-script coach signals
                wp, xp_ = _f(x["wp"]), _f(x["xpass"])
                hs = _i(x["half_seconds_remaining"], 0)
                if wp is not None and xp_ is not None and 0.2 <= wp <= 0.8 and hs > 120 and down <= 3 and qtr <= 4:
                    g = neutral.setdefault((season, off), {}).setdefault(gix.get(off, 0), [0.0, 0, 0, 0.0, 0])
                    g[0] += (1.0 if is_pass else 0.0) - xp_
                    g[1] += 1
                    g[2] += x["no_huddle"] == "1"
                    if dt is not None:
                        g[3] += dt
                        g[4] += 1
        S["counts"] = dict(s_cnt)
        _usage(games, season, usage, role_games)
        S["head_coaches"] = len(coaches - {""})
        S["mid_season_changes"] = sorted(t for (s, t), c in coach_of.items() if s == season and len(c) > 1)
    # ---- pooled tables
    def table(d, digits=3):
        return {"|".join(str(p) for p in k): [round(v[0] / v[1], digits) if v[1] else None, int(v[1])]
                for k, v in sorted(d.items(), key=lambda kv: str(kv[0]))}
    out["kneel_rule"] = table(kneel)
    out["kneels_by_quarter"] = {str(k): v for k, v in sorted(kneel_q.items())}
    out["defence_timeout_after_inbounds"] = table(to_def)
    out["timeouts_used_per_team_half"] = {f"{h}|{n}": c for (h, n), c in sorted(to_used.items())}
    out["timeouts_when"] = {f"{h}|{w}": c for (h, w), c in sorted(to_when.items())}
    out["timeouts_role"] = {"|".join(k): c for k, c in sorted(to_role.items())}
    out["q4_pass_rate"] = table(q4_pass)
    out["q4_sec_to_snap"] = table(q4_dt, 1)
    out["q2_pass_rate"] = table(q2_pass)
    out["q2_sec_to_snap"] = table(q2_dt, 1)
    out["onside"] = {(k if isinstance(k, str) else "|".join(k)): v for k, v in onside.items()}
    out["spikes_by_quarter"] = {str(k[1]): v for k, v in sorted(misc.items()) if k[0] == "spike_q"}
    g = max(1, win["games"])
    out["inside_two_minutes_per_game"] = {k: round(v / g, 3) for k, v in win.items() if k != "games"}
    out["inside_two_minutes_per_game"]["games"] = win["games"]
    # ---- coach signals: split-half and year over year
    cell = {}
    for _s, _t, _g, c, go in fourth_rows:
        _acc(cell, c, go)
    goe = {}
    for s, t, gi, c, go in fourth_rows:
        d = goe.setdefault((s, t), {}).setdefault(gi, [0.0, 0])
        d[0] += go - cell[c][0] / cell[c][1]
        d[1] += 1
    head = {k: c.most_common(1)[0][0] for k, c in coach_of.items()}

    def halves(per_game, num, den, min_n):
        a, b, full = {}, {}, {}
        for key, gm in per_game.items():
            sa = sb = na = nb = 0.0
            for gi, v in gm.items():
                if gi % 2:
                    sa += v[num]
                    na += v[den]
                else:
                    sb += v[num]
                    nb += v[den]
            if na >= min_n and nb >= min_n:
                a[key], b[key] = sa / na, sb / nb
                full[key] = (sa + sb) / (na + nb)
        return a, b, full

    def signal(per_game, num, den, min_n):
        a, b, full = halves(per_game, num, den, min_n)
        keys = sorted(a)
        r = _pearson([a[k] for k in keys], [b[k] for k in keys])
        same = [(full[(s, t)], full[(s + 1, t)]) for (s, t) in full
                if (s + 1, t) in full and head.get((s, t)) == head.get((s + 1, t))]
        new = [(full[(s, t)], full[(s + 1, t)]) for (s, t) in full
               if (s + 1, t) in full and head.get((s, t)) != head.get((s + 1, t))]
        # a head coach who took another team: his old team's figure against his new one's
        moved = []
        for (s, t), v in full.items():
            for (s2, t2), v2 in full.items():
                if s2 > s and t2 != t and head.get((s, t)) and head.get((s, t)) == head.get((s2, t2)):
                    moved.append((v, v2))
        vals = list(full.values())
        m = sum(vals) / len(vals) if vals else 0.0
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / max(1, len(vals) - 1)) if vals else 0.0
        return {"team_seasons": len(keys), "split_half_r": r,
                "full_season_reliability": (round(2 * r / (1 + r), 3) if r is not None and r > -1 else None),
                "sd_between_team_seasons": round(sd, 4),
                "yoy_same_coach": [_pearson([x for x, _ in same], [y for _, y in same]), len(same)],
                "yoy_new_coach": [_pearson([x for x, _ in new], [y for _, y in new]), len(new)],
                "coach_changed_teams": [_pearson([x for x, _ in moved], [y for _, y in moved]), len(moved)]}
    out["coach_signal"] = {
        "neutral_pass_over_expected": signal(neutral, 0, 1, 80),
        "no_huddle_rate_neutral": signal(neutral, 2, 1, 80),
        "sec_to_snap_neutral": signal(neutral, 3, 4, 40),
        "fourth_down_go_over_league_cell": signal(goe, 0, 1, 12)}
    out["fourth_down"] = {"rows": len(fourth_rows),
                          "go_rate": round(sum(r_[4] for r_ in fourth_rows) / max(1, len(fourth_rows)), 4)}
    out["head_coach_stints"] = len({(t, c) for (s, t), c in head.items()})
    out["usage_by_script"] = table(usage)
    a, b, _full = halves(role_games, 0, 1, 15)
    keys = sorted(a)
    out["lead_back_share_of_backs_passing_situation_touches"] = {
        "team_seasons": len(keys), "split_half_r": _pearson([a[k] for k in keys], [b[k] for k in keys])}
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    seasons = [2023, 2024, 2025, 2026]
    outp = OUT_DEFAULT
    for i, a in enumerate(argv):
        if a == "--seasons":
            seasons = [int(x) for x in argv[i + 1].split(",")]
        elif a == "--out":
            outp = argv[i + 1]
    res = audit(argv[1], seasons)
    with open(outp, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
