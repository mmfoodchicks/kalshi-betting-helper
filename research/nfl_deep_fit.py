"""Fit the NFL deep engine's tables from real play-by-play (nfl_pbp records).
RESEARCH: writes research/data/nfl_deep_tables.json, which nfl_deep_sim loads.

Everything the engine samples is an EMPIRICAL table from the fit seasons, in
the buckets nfl_pbp defines (so a cell means the same thing here and in the
simulation): the play call by situation, the pass outcome chain (sack ->
interception -> completion -> yards), rush yards, the no-play penalty, the
fourth-down decision, the field-goal curve, the punt and kickoff field
position, the clock between snaps, the extra point and the two-point decision.
Nothing is a smooth parametric guess except the field-goal curve, which is a
two-parameter logistic in kick distance.

The matchup exponents -- how much of an offence's and a defence's season
rate carries into one game -- are measured leave-one-out: every team-game's
yards per play (and pass / rush / points / the per-play rates) regressed on
its own offence's rate over the OTHER games of the season and the opponent
defence's, both shrunk toward the league by a K fitted from the within-game
and between-team variances. Reported raw and shrunk; the engine uses the
shrunk pair with the same K, so it believes a six-game rate exactly as much
as the fit found a six-game rate to be worth.

Usage:
  python3 -m research.nfl_deep_fit <store dir with nfl_pbp_<season>.pkl> [--seasons 2023,2024,2025,2026]
       [--prior-season 2025] [--out research/data/nfl_deep_tables.json]
The store directory can be deep_cache.CACHE_DIR (production's own cache).
"""
import collections
import datetime
import json
import math
import os
import pickle
import sys

import nfl_pbp as P

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_deep_tables.json")
# Kickoff rules changed twice in the fit window: 2024 brought the dynamic
# kickoff (touchback to the 30), 2025 moved the touchback to the 35. The
# engine plays under the CURRENT rules, so its kickoff table is 2025+ only.
KICKOFF_ERA = {2023: "2023", 2024: "2024"}


def _era(season):
    return KICKOFF_ERA.get(int(season), "2025+")


def load_stores(store_dir, seasons):
    out = []
    for s in seasons:
        p = os.path.join(store_dir, f"nfl_pbp_{s}.pkl")
        if not os.path.exists(p):
            print(f"[fit] missing {p}", file=sys.stderr)
            continue
        with open(p, "rb") as fh:
            st = pickle.load(fh)
        if isinstance(st, dict) and "payload" in st:      # a deep_cache record
            st = st["payload"]
        out.append((int(s), st))
    return out


def _hist(d, key, v):
    h = d.setdefault(key, {})
    h[str(v)] = h.get(str(v), 0) + 1


def _cnt(d, key, hit):
    c = d.setdefault(key, [0, 0])
    c[1] += 1
    c[0] += 1 if hit else 0


def _tempo(p):
    """0 normal, 1 hurry-up (behind or level late, or the half's last two
    minutes), 2 clock-killing (ahead late)."""
    per, clk, sd = p[P.I_PER], p[P.I_CLK], p[P.I_SD]
    ts = P.time_state(per, clk)
    if per == 2 and clk <= 120:
        return 1
    if ts == 2:
        return 1 if sd <= 0 else 2
    if ts == 1 and per == 4 and sd <= -9:
        return 1
    return 0


def _clock_class(p):
    """What the clock does after this snap: 'run' (in bounds, clock runs),
    'inc' (incomplete / spike: stopped until the snap), 'oob' (out of bounds:
    restarts on the ready-for-play outside two minutes), 'score', 'to'
    (change of possession), 'pen' (a no-play penalty). One pooled 'stop' class
    (17 s) over-charged a touchdown and an incompletion with the out-of-bounds
    runner's restart and ran the game ~12% short of plays."""
    code, fl = p[P.I_CODE], p[P.I_FLAGS]
    if fl & P.F_TD:
        return "score"
    if code == P.PENALTY:
        return "pen"
    if code in (P.INT, P.FUMBLE_LOST, P.SAFETY) or fl & P.F_TURNOVER:
        return "to"
    if code in (P.PASS_INC, P.SPIKE):
        return "inc"
    if fl & P.F_OOB:
        return "oob"
    return "run"


def build_tables(stores, prior_season=None):
    T = {"call": {}, "call_marg": {}, "call_dd": {}, "sack": {}, "int": {}, "comp": {},
         "comp_yds": {}, "comp_yds_dz": {}, "sack_yds": {}, "rush_yds": {}, "rush_yds_dz": {}, "fumble_lost": {},
         "pen_rate": {}, "pen_out": {}, "fourth": {}, "fourth_zdt": {}, "fourth_zd": {},
         "fourth_z": {}, "fg_bins": {}, "fg_block": {}, "punt": {}, "punt_td": {},
         "punt_block": {}, "kickoff": {}, "kickoff_td": {}, "clock": {}, "xp": {},
         "two_att": {}, "two_good": {}, "kneel_rate": {}, "int_ret": {}, "oob": {},
         "fum_spot": {}}
    pts_ypp = []
    fg_rows = []
    n_games = n_plays = 0
    val = {"pts": [], "margin": [], "total": [], "home_win": 0, "ot": 0, "tie": 0, "games": 0,
           "drives": 0, "snaps": 0, "td_drives": 0, "fg_drives": 0, "punt_drives": 0,
           "to_drives": 0, "three_out": 0, "reg_games": 0}
    for season, st in stores:
        era = _era(season)
        for rec in st["games"].values():
            plays = list(P.iter_plays(rec))
            if not plays:
                continue
            n_games += 1
            n_plays += len(plays)
            if rec.get("st") == 2:
                val["reg_games"] += 1
                val["pts"].extend((rec["home_pts"], rec["away_pts"]))
                val["margin"].append(rec["home_pts"] - rec["away_pts"])
                val["total"].append(rec["home_pts"] + rec["away_pts"])
                val["home_win"] += rec["home_pts"] > rec["away_pts"]
                val["ot"] += bool(rec.get("ot"))
                val["tie"] += rec["home_pts"] == rec["away_pts"]
                for dr in rec["drives"]:
                    res = dr[6]
                    if res in ("END OF GAME", "END OF HALF"):
                        continue
                    val["drives"] += 1
                    val["td_drives"] += res.startswith("TD")
                    val["fg_drives"] += res == "FG"
                    val["punt_drives"] += res.startswith("PUNT")
                    val["to_drives"] += res.startswith(("INT", "FUMBLE", "DOWNS"))
                    val["three_out"] += (res.startswith("PUNT") and dr[4] <= 3)
            for per_, _clk, _off, kind, sd6 in rec.get("scoring") or []:
                late = 1 if per_ >= 4 else 0
                if kind in (1, 2):
                    _cnt(T["xp"], "all", kind == 1)
                    _cnt(T["two_att"], f"{max(-16, min(16, sd6))},{late}", False)
                elif kind in (3, 4):
                    _cnt(T["two_good"], "all", kind == 3)
                    _cnt(T["two_att"], f"{max(-16, min(16, sd6))},{late}", True)
            if rec.get("st") == 2:
                gs = _game_sides(rec)
                for side in (0, 1):
                    s_ = gs[side]
                    if s_["plays"] > 20 and s_["pts"] > 0:
                        pts_ypp.append((math.log(s_["pts"]),
                                        math.log(max(1.0, (s_["cyds"] + s_["ryds"] - s_["syds"]) / s_["plays"])), 0.0))
            for i, p in enumerate(plays):
                per, clk, down, dist, ytg = p[P.I_PER], p[P.I_CLK], p[P.I_DOWN], p[P.I_DIST], p[P.I_YTG]
                code, yds, fl, sd = p[P.I_CODE], p[P.I_YDS], p[P.I_FLAGS], p[P.I_SD]
                ts, ss = P.time_state(per, clk), P.score_state(sd)
                db, zn = P.dist_bucket(max(1, dist)), P.zone(max(1, ytg))
                # ---- the clock after a kick (kickoff / punt / field goal row -> the
                # next snap) and after a touchdown (the scoring snap -> the kickoff)
                if code in (P.KICKOFF, P.PUNT, P.PUNT_BLOCK, P.FG_GOOD, P.FG_MISS, P.FG_BLOCK) or (fl & P.F_TD):
                    j = i + 1
                    while j < len(plays) and plays[j][P.I_CODE] in (P.TIMEOUT, P.TWO_MIN):
                        j += 1
                    if j < len(plays) and plays[j][P.I_PER] == per and 0 <= clk - plays[j][P.I_CLK] <= 60:
                        nxt = plays[j][P.I_CODE]
                        if (fl & P.F_TD) and nxt == P.KICKOFF:
                            _hist(T["clock"], f"score,{_tempo(p)}", clk - plays[j][P.I_CLK])
                        elif nxt in P.SNAPS and not (fl & P.F_TD):
                            _hist(T["clock"], f"{'kickoff' if code == P.KICKOFF else 'punt' if code in (P.PUNT, P.PUNT_BLOCK) else 'fg'},0",
                                  clk - plays[j][P.I_CLK])
                # ---- kicks and special teams
                if code == P.KICKOFF:
                    if fl & P.F_TD:
                        _cnt(T["kickoff_td"], era, True)
                    elif p[P.I_EYTG] > 0:
                        _cnt(T["kickoff_td"], era, False)
                        _hist(T["kickoff"], era, min(99, p[P.I_EYTG]))
                    continue
                if code in (P.PUNT, P.PUNT_BLOCK):
                    _cnt(T["punt_block"], "all", code == P.PUNT_BLOCK)
                    if code == P.PUNT:
                        _cnt(T["punt_td"], "all", bool(fl & P.F_TD))
                        if p[P.I_EYTG] > 0 and not (fl & P.F_TD):
                            _hist(T["punt"], str(min(95, max(30, ytg // 5 * 5))), min(99, p[P.I_EYTG]))
                if code in (P.FG_GOOD, P.FG_MISS, P.FG_BLOCK):
                    kd = ytg + 17
                    _cnt(T["fg_block"], "all", code == P.FG_BLOCK)
                    if code != P.FG_BLOCK:
                        fg_rows.append((kd, code == P.FG_GOOD))
                        _cnt(T["fg_bins"], str(min(65, max(15, kd // 5 * 5))), code == P.FG_GOOD)
                # ---- fourth-down decision (the choice, before its outcome)
                if down == 4 and code in (P.PUNT, P.PUNT_BLOCK, P.FG_GOOD, P.FG_MISS, P.FG_BLOCK,
                                          P.RUSH, P.PASS_COMP, P.PASS_INC, P.SACK, P.INT,
                                          P.FUMBLE_LOST, P.SAFETY, P.SPIKE):
                    choice = (1 if code in (P.PUNT, P.PUNT_BLOCK)
                              else 2 if code in (P.FG_GOOD, P.FG_MISS, P.FG_BLOCK) else 0)
                    z5 = min(19, max(0, (ytg - 1) // 5))
                    for key, tbl in ((f"{z5},{db},{ss},{ts}", T["fourth"]), (f"{z5},{db},{ts}", T["fourth_zdt"]),
                                     (f"{z5},{db}", T["fourth_zd"]), (f"{z5}", T["fourth_z"])):
                        c = tbl.setdefault(key, [0, 0, 0])
                        c[choice] += 1
                if code not in P.SNAPS:
                    continue
                val["snaps"] += 1 if rec.get("st") == 2 else 0
                if code == P.INT and p[P.I_EYTG] > 0 and not (fl & P.F_TD):
                    # the new offence's yards to go, relative to the line of scrimmage
                    _hist(T["int_ret"], "all", max(-20, min(60, (100 - ytg) - p[P.I_EYTG])))
                elif code == P.INT and fl & P.F_TD:
                    _hist(T["int_ret"], "all", 99)
                if code == P.FUMBLE_LOST and p[P.I_EYTG] > 0 and not (fl & P.F_TD):
                    _hist(T["fum_spot"], "all", max(-20, min(60, (100 - ytg) - p[P.I_EYTG])))
                if code == P.PASS_COMP:
                    _cnt(T["oob"], "comp", bool(fl & P.F_OOB))
                elif code == P.RUSH:
                    _cnt(T["oob"], "rush", bool(fl & P.F_OOB))
                # ---- clock between this snap and the next one in the quarter
                j = i + 1
                while j < len(plays) and plays[j][P.I_CODE] in (P.TIMEOUT, P.TWO_MIN):
                    j += 1
                if j < len(plays) and plays[j][P.I_PER] == per and plays[j][P.I_CODE] != P.END_PERIOD:
                    dt = clk - plays[j][P.I_CLK]
                    if 0 <= dt <= 90:
                        nxt = plays[j][P.I_CODE]
                        if nxt in P.SNAPS:
                            _hist(T["clock"], f"{_clock_class(p)},{_tempo(p)}", min(60, dt))
                        elif nxt in (P.PUNT, P.PUNT_BLOCK):
                            _hist(T["clock"], f"prepunt,{_tempo(p)}", min(60, dt))
                        elif nxt in (P.FG_GOOD, P.FG_MISS, P.FG_BLOCK):
                            _hist(T["clock"], f"prefg,{_tempo(p)}", min(60, dt))
                # kick rows to the next snap: the kickoff / punt / FG itself
                if code == P.KNEEL:
                    _cnt(T["kneel_rate"], "all", True)
                    continue
                if code == P.PENALTY:
                    _cnt(T["pen_rate"], str(down), True)
                    _hist(T["pen_out"], str(down), f"{max(-25, min(25, yds))},{1 if p[P.I_EDOWN] == 1 and yds > 0 else 0}")
                    continue
                _cnt(T["pen_rate"], str(down), False)
                if code == P.SPIKE:
                    continue
                # ---- the play call and its outcome
                _cnt(T["call"], f"{ts},{ss},{down},{db},{zn}", P.is_pass(p))
                _cnt(T["call_marg"], f"{down},{db},{zn}", P.is_pass(p))
                _cnt(T["call_dd"], f"{down},{db}", P.is_pass(p))
                dd = f"{down},{db}"
                dz = f"{db},{zn}"
                ddz = f"{down},{db},{zn}"
                # EFFECTIVE yards: where the next snap is, penalties enforced. A
                # defensive hold tacked onto a six-yard run is a first down the
                # stat yardage does not show (74 of 74 such first-and-tens in 120
                # games of 2025 were penalties on the play); the sim converts on
                # yards >= distance, so a play the chains moved on is stored as
                # at least the distance.
                first = bool(fl & P.F_FIRST)
                if (p[P.I_EYTG] > 0 and not (fl & (P.F_TURNOVER | P.F_TD))
                        and code in (P.RUSH, P.PASS_COMP, P.PASS_INC, P.SACK)):
                    yds_eff = ytg - p[P.I_EYTG]
                    if first:
                        yds_eff = max(yds_eff, dist)
                elif (fl & P.F_TD) and p[P.I_PTS] > 0 and code in (P.RUSH, P.PASS_COMP) and ytg <= 20:
                    # A touchdown is stored as "scores" (99), not as its yardage.
                    # Real yardage is truncated at the goal line, so a cell pooling
                    # plays from the 6 and the 10 held every six-yard touchdown as
                    # six yards; sampled from the 10 that was fourth-and-goal, and
                    # the engine scored on 52.6% of red-zone trips against the real
                    # 58%. The sentinel makes P(touchdown) within a cell the cell's
                    # own rate; the engine caps the yards at the goal line anyway.
                    # Inside the 20 only: from deeper, a touchdown's real yardage is
                    # the honest draw (a 45-yard score from the 45 is 45 yards from
                    # the 60, not a touchdown), and the sentinel there inflated
                    # yards per carry to 4.49 against 4.25.
                    yds_eff = 99
                else:
                    yds_eff = yds
                if P.is_pass(p):
                    _cnt(T["sack"], dd, code == P.SACK)
                    if code == P.SACK:
                        _hist(T["sack_yds"], "all", max(-25, min(30, yds_eff)))
                        _cnt(T["fumble_lost"], "sack", bool(fl & P.F_FUMBLE_LOST))
                        continue
                    _cnt(T["int"], dd, code == P.INT)
                    if code == P.INT:
                        continue
                    # "complete" here means the pass play advanced the ball -- a
                    # catch, or an incompletion with a defensive penalty that did
                    # the same; the box-score completion rate reads ~1% high for it
                    comp = (code == P.PASS_COMP or (code == P.FUMBLE_LOST and fl & P.F_PASS_PLAY)
                            or (code == P.PASS_INC and first))
                    _cnt(T["comp"], dd, comp)
                    if comp:
                        # yards by DOWN as well as distance and field position: a
                        # third-and-seven throw is aimed at the sticks, a
                        # first-and-ten throw is not, and one pooled histogram
                        # under-converted third downs (34.6% against the real 39%)
                        # while matching yards per completion to the yard
                        _hist(T["comp_yds"], ddz, max(-15, min(99, yds_eff)))
                        _hist(T["comp_yds_dz"], dz, max(-15, min(99, yds_eff)))
                        _cnt(T["fumble_lost"], "comp", bool(fl & P.F_FUMBLE_LOST))
                else:
                    _hist(T["rush_yds"], ddz, max(-15, min(99, yds_eff)))
                    _hist(T["rush_yds_dz"], dz, max(-15, min(99, yds_eff)))
                    _cnt(T["fumble_lost"], "rush", bool(fl & P.F_FUMBLE_LOST))
    T["fg_logit"] = _fit_fg_logit(fg_rows)
    # Points per game against yards per play, log-log: the elasticity the level
    # anchor uses to turn "this team should be N points better" into a yardage
    # multiplier (nfl_deep_data).
    T["pts_vs_ypp"] = _ols1([(y, x) for y, x, _z in pts_ypp])
    T["fit"] = {"seasons": [s for s, _ in stores], "games": n_games, "plays": n_plays,
                "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    T["validation"] = _summarize_validation(val)
    units = {}
    for season, st in stores:
        units[str(season)] = P.team_units(st, seasontype=2)
    T["league"] = _league(units, [s for s, _ in stores])
    T["matchup"] = _matchup_exponents(stores)
    T["persistence"] = _persistence(units, [s for s, _ in stores])
    if prior_season is not None and str(prior_season) in units:
        T["team_prior"] = {"season": int(prior_season), "units": units[str(prior_season)]}
    return T


def _summarize_validation(v):
    n = len(v["margin"]) or 1

    def sd(xs):
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    d = v["drives"] or 1
    return {"reg_games": v["reg_games"], "pts_per_team_game": round(sum(v["pts"]) / (2 * n), 3),
            "margin_sd": round(sd(v["margin"]), 3), "total_sd": round(sd(v["total"]), 3),
            "home_margin_mean": round(sum(v["margin"]) / n, 3),
            "home_win_rate": round(v["home_win"] / n, 4), "ot_rate": round(v["ot"] / n, 4),
            "tie_rate": round(v["tie"] / n, 4), "drives_per_team_game": round(d / (2 * n), 3),
            "snaps_per_team_game": round(v["snaps"] / (2 * n), 3),
            "td_per_drive": round(v["td_drives"] / d, 4), "fg_per_drive": round(v["fg_drives"] / d, 4),
            "punt_per_drive": round(v["punt_drives"] / d, 4), "to_per_drive": round(v["to_drives"] / d, 4),
            "three_and_out_per_drive": round(v["three_out"] / d, 4)}


def _fit_fg_logit(rows):
    """P(good) = 1/(1+exp(-(a + b*dist))) by Newton's method; pure Python."""
    a, b = 6.0, -0.1
    for _ in range(40):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in rows:
            z = a + b * x
            pz = 1.0 / (1.0 + math.exp(-z)) if z > -30 else 0.0
            r = (1.0 if y else 0.0) - pz
            w = pz * (1 - pz)
            g0 += r; g1 += r * x
            h00 += w; h01 += w * x; h11 += w * x * x
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (-h01 * g0 + h00 * g1) / det
        a += da; b += db
        if abs(da) + abs(db) < 1e-9:
            break
    return [round(a, 5), round(b, 6), len(rows)]


def _rates(u):
    r = P.unit_rates(u)
    r["ypc_pass"] = r["ypcomp"]          # the league table's historical key
    return r


def _league(units, seasons):
    tot = {"off": P._unit(), "g": 0, "pf": 0}
    for s in seasons:
        for ab, t in units.get(str(s), {}).items():
            tot["g"] += t["g"]; tot["pf"] += t["pf"]
            for k in P._UNIT_KEYS:
                tot["off"][k] += t["off"][k]
            for k in ("punt", "ko_ret", "clock"):
                tot["off"][k][0] += t["off"][k][0]; tot["off"][k][1] += t["off"][k][1]
            for key, c in t["off"]["call"].items():
                cc = tot["off"]["call"].setdefault(key, [0, 0]); cc[0] += c[0]; cc[1] += c[1]
            for key, c in t["off"]["fg"].items():
                cc = tot["off"]["fg"].setdefault(key, [0, 0]); cc[0] += c[0]; cc[1] += c[1]
    r = _rates(tot["off"])
    g = tot["g"] or 1
    r.update({"team_games": tot["g"], "pts_pg": tot["pf"] / g,
              "snaps_pg": tot["off"]["snaps"] / g,
              "plays_pg": (tot["off"]["pass"] + tot["off"]["rush"]) / g,
              "punt_net_ytg": tot["off"]["punt"][1] / tot["off"]["punt"][0] if tot["off"]["punt"][0] else None,
              "ko_ret_ytg": tot["off"]["ko_ret"][1] / tot["off"]["ko_ret"][0] if tot["off"]["ko_ret"][0] else None,
              "fg_bins": tot["off"]["fg"], "call": tot["off"]["call"]})
    return r


_PERSIST_STATS = ("pass_rate", "comp", "ypcomp", "sack", "int", "ypc", "fum", "pen", "ypp",
                  "sec_per_snap", "punt_ytg", "ko_ytg")


def _persistence(units, seasons):
    """How much of a team's deviation from the league in one season survives
    into the next: the slope of next season's rate on this season's, both as
    deviations from their league means, offence and defence separately, over
    every consecutive pair of FULL seasons (17 games a team). This is the
    weight the profile builder gives last season before any game is played;
    the fit's K then says how fast this season's games take over."""
    out = {}
    full = [s for s in seasons if all(t["g"] >= 16 for t in units.get(str(s), {}).values())
            and len(units.get(str(s), {})) == 32]
    for side in ("off", "def"):
        for stat in _PERSIST_STATS:
            rows = []
            for a, b in zip(full, full[1:]):
                if int(b) != int(a) + 1:
                    continue                  # persistence is a ONE-year slope
                ra = {ab: P.unit_rates(t[side]).get(stat) for ab, t in units[str(a)].items()}
                rb = {ab: P.unit_rates(t[side]).get(stat) for ab, t in units[str(b)].items()}
                ra = {k: v for k, v in ra.items() if v is not None}
                rb = {k: v for k, v in rb.items() if v is not None}
                if len(ra) < 20 or len(rb) < 20:
                    continue
                ma = sum(ra.values()) / len(ra); mb = sum(rb.values()) / len(rb)
                for ab in ra:
                    if ab in rb:
                        rows.append((rb[ab] - mb, ra[ab] - ma))
            fit = _ols1(rows)
            out[f"{side}.{stat}"] = {"slope": fit[1] if fit else None, "n": len(rows), "pairs": len(full) - 1}
    return out


def _ols1(rows):
    """rows of (y, x) -> (c, slope, r2, n), pure Python."""
    n = len(rows)
    if n < 20:
        return None
    mx = sum(x for _, x in rows) / n; my = sum(y for y, _ in rows) / n
    sxx = sum((x - mx) ** 2 for _, x in rows) or 1e-12
    sxy = sum((x - mx) * (y - my) for y, x in rows)
    b = sxy / sxx; c = my - b * mx
    ss_tot = sum((y - my) ** 2 for y, _ in rows) or 1.0
    ss_res = sum((y - c - b * x) ** 2 for y, x in rows)
    return (round(c, 5), round(b, 4), round(1 - ss_res / ss_tot, 4), n)


def _ols(rows):
    """rows of (y, x1, x2) -> (c, a, b, r2) by normal equations, pure Python."""
    n = len(rows)
    if n < 10:
        return None
    sy = sum(r[0] for r in rows); s1 = sum(r[1] for r in rows); s2 = sum(r[2] for r in rows)
    m_y, m1, m2 = sy / n, s1 / n, s2 / n
    s11 = sum((r[1] - m1) ** 2 for r in rows); s22 = sum((r[2] - m2) ** 2 for r in rows)
    s12 = sum((r[1] - m1) * (r[2] - m2) for r in rows)
    s1y = sum((r[1] - m1) * (r[0] - m_y) for r in rows); s2y = sum((r[2] - m2) * (r[0] - m_y) for r in rows)
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-12:
        return None
    a = (s22 * s1y - s12 * s2y) / det
    b = (s11 * s2y - s12 * s1y) / det
    c = m_y - a * m1 - b * m2
    ss_tot = sum((r[0] - m_y) ** 2 for r in rows) or 1.0
    ss_res = sum((r[0] - (c + a * r[1] + b * r[2])) ** 2 for r in rows)
    return (round(c, 5), round(a, 4), round(b, 4), round(1 - ss_res / ss_tot, 4), n)


def _game_sides(rec):
    """Per-side counts for one game: (plays, pass att, comp, comp yds, sacks, sack yds,
    ints, rushes, rush yds, points) for home and away offences."""
    keys = ("plays", "att", "comp", "cyds", "sacks", "syds", "ints", "rush", "ryds", "fum", "pen", "snaps", "passes")
    out = {0: dict.fromkeys(keys, 0), 1: dict.fromkeys(keys, 0)}
    for p in P.iter_plays(rec):
        code, fl = p[P.I_CODE], p[P.I_FLAGS]
        if code == P.PENALTY:
            out[p[P.I_OFF]]["pen"] += 1
            out[p[P.I_OFF]]["snaps"] += 1
            continue
        if code not in P.PLAYS or code in (P.KNEEL, P.SPIKE):
            continue
        s = out[p[P.I_OFF]]
        s["plays"] += 1
        s["snaps"] += 1
        if fl & P.F_FUMBLE_LOST:
            s["fum"] += 1
        if P.is_pass(p):
            s["passes"] += 1
        if P.is_pass(p):
            if code == P.SACK:
                s["sacks"] += 1; s["syds"] += -p[P.I_YDS]
            else:
                s["att"] += 1
                if code == P.INT:
                    s["ints"] += 1
                elif code == P.PASS_COMP or (code == P.FUMBLE_LOST and fl & P.F_PASS_PLAY):
                    s["comp"] += 1; s["cyds"] += p[P.I_YDS]
        else:
            s["rush"] += 1; s["ryds"] += p[P.I_YDS]
    out[0]["pts"], out[1]["pts"] = rec["home_pts"], rec["away_pts"]
    return out


_STATS = {
    # name: (numerator keys, denominator keys, log or logit)
    "ypp": (("cyds", "ryds", "-syds"), ("plays",), "log"),
    "ypa": (("cyds",), ("att",), "log"),
    "ypc": (("ryds",), ("rush",), "log"),
    "pts": (("pts",), None, "log"),
    "ypcomp": (("cyds",), ("comp",), "log"),
    "comp": (("comp",), ("att",), "logit"),
    "fum": (("fum",), ("plays",), "logit"),
    "pen": (("pen",), ("snaps",), "logit"),
    "pass_rate": (("passes",), ("plays",), "logit"),
    "sack": (("sacks",), ("att", "sacks"), "logit"),
    "int": (("ints",), ("att",), "logit"),
}


def _val(side, num, den):
    n = 0.0
    for k in num:
        n += -side[k[1:]] if k.startswith("-") else side[k]
    if den is None:
        return n, 1.0
    d = float(sum(side[k] for k in den))
    return n, d


def _full_season(st):
    """True when every team played at least 16 regular-season games in the store."""
    g = collections.Counter()
    for r in st["games"].values():
        if r.get("st") == 2:
            g[r["home"]] += 1
            g[r["away"]] += 1
    return len(g) >= 32 and min(g.values()) >= 16


def _matchup_exponents(stores):
    """Leave-one-out, per stat: the game value of an offence regressed on its own
    season rate over the other games and the opposing defence's rate over its
    other games, log-log (or logit-logit). Raw, and shrunk toward the league
    mean with K = within-game variance / between-team variance (in games)."""
    out = {}
    for name, (num, den, link) in _STATS.items():
        rows_raw, rows_shr = [], []
        for season, st in stores:
            games = [r for r in st["games"].values() if r.get("st") == 2]
            sides = {r["id"]: _game_sides(r) for r in games}
            # season totals per team offence / defence
            off_tot = collections.defaultdict(lambda: [0.0, 0.0, 0])
            def_tot = collections.defaultdict(lambda: [0.0, 0.0, 0])
            per_game = []
            lg_n = lg_d = 0.0
            for r in games:
                s = sides[r["id"]]
                for o_i, ab, opp in ((0, r["home"], r["away"]), (1, r["away"], r["home"])):
                    n_, d_ = _val(s[o_i], num, den)
                    off_tot[ab][0] += n_; off_tot[ab][1] += d_; off_tot[ab][2] += 1
                    def_tot[opp][0] += n_; def_tot[opp][1] += d_; def_tot[opp][2] += 1
                    lg_n += n_; lg_d += d_
                    per_game.append((ab, opp, n_, d_))
            if lg_d <= 0:
                continue
            mu = lg_n / lg_d
            # between-team and within variance of the per-game rate, for K
            rates = [n_ / d_ for _, _, n_, d_ in per_game if d_ > 0]
            team_means = {ab: v[0] / v[1] for ab, v in off_tot.items() if v[1] > 0}
            var_within = sum((n_ / d_ - team_means[ab]) ** 2 for ab, _, n_, d_ in per_game if d_ > 0) / max(1, len(rates) - len(team_means))
            var_between = max(1e-9, sum((m - mu) ** 2 for m in team_means.values()) / max(1, len(team_means) - 1)
                              - var_within / max(1.0, len(rates) / max(1, len(team_means))))
            K = var_within / var_between if var_between > 0 else 50.0
            for ab, opp, n_, d_ in per_game:
                if d_ <= 0:
                    continue
                y = n_ / d_
                o_n, o_d, _g = off_tot[ab]; d_n, d_d, _g2 = def_tot[opp]
                o_n -= n_; o_d -= d_; d_n -= n_; d_d -= d_      # leave this game out
                g_o, g_d = off_tot[ab][2] - 1, def_tot[opp][2] - 1
                if o_d <= 0 or d_d <= 0 or g_o < 4 or g_d < 4:
                    continue
                xo, xd = o_n / o_d, d_n / d_d
                xo_s = (g_o * xo + K * mu) / (g_o + K)
                xd_s = (g_d * xd + K * mu) / (g_d + K)
                if link == "log":
                    if y <= 0 or xo <= 0 or xd <= 0:
                        continue
                    f = math.log
                    rows_raw.append((f(y), f(xo), f(xd)))
                    rows_shr.append((f(y), f(xo_s), f(xd_s)))
                else:
                    eps = 0.5 / d_
                    y_c = min(1 - eps, max(eps, y))
                    lg_ = lambda q: math.log(q / (1 - q))
                    if not (0 < xo < 1 and 0 < xd < 1):
                        continue
                    rows_raw.append((lg_(y_c), lg_(xo), lg_(xd)))
                    rows_shr.append((lg_(y_c), lg_(xo_s), lg_(xd_s)))
            rec = out.setdefault(name, {})
            rec.setdefault("K_by_season", {})[str(season)] = round(K, 2)
            rec.setdefault("league_by_season", {})[str(season)] = round(mu, 5)
            rec.setdefault("_full", []).append(_full_season(st))
        # K and the league rate are POOLED across the full seasons, not taken
        # from whichever season the loop met last. They used to be assigned
        # inside the season loop, so the shipped K was 2026's alone -- five games
        # a team, and 2-10x off the full seasons for most stats (interceptions 14
        # against 20-143, yards per carry 6.75 against 12-30; Opus review,
        # review_opus/r6_k_by_season.py). The median is used because the
        # method-of-moments K is unstable when the between-team variance is near
        # zero (penalties 2025: 1.2 million games).
        rec = out[name]
        full = [s_ for s_, ok in zip(rec["K_by_season"], rec.pop("_full")) if ok]
        use = full or list(rec["K_by_season"])
        ks = sorted(rec["K_by_season"][s_] for s_ in use)
        rec["K_games"] = round(ks[len(ks) // 2] if len(ks) % 2 else 0.5 * (ks[len(ks) // 2 - 1] + ks[len(ks) // 2]), 2)
        rec["K_seasons"] = [int(s_) for s_ in use]
        ls = [rec["league_by_season"][s_] for s_ in use]
        rec["league"] = round(sum(ls) / len(ls), 5)
        rec["raw"] = _ols(rows_raw)
        rec["shrunk"] = _ols(rows_shr)
    return out


def report(T):
    L = []
    f = T["fit"]; v = T["validation"]; lg = T["league"]
    L.append(f"# NFL deep engine tables -- fit report ({f['built']})")
    L.append(f"Seasons {f['seasons']}: {f['games']} games, {f['plays']:,} play rows.")
    L.append("")
    L.append("## Real-game validation targets (regular season)")
    for k, val in v.items():
        L.append(f"- {k}: {val}")
    L.append("")
    L.append("## League rates (offence)")
    for k in ("pts_pg", "snaps_pg", "plays_pg", "pass_rate", "comp", "ypa", "ypc_pass", "sack", "int", "ypc",
              "fum", "pen", "ypp", "sec_per_snap", "punt_net_ytg", "ko_ret_ytg"):
        val = lg.get(k)
        L.append(f"- {k}: {round(val, 4) if isinstance(val, float) else val}")
    a, b, n = T["fg_logit"]
    L.append("")
    L.append(f"## Field goals: logit P(good) = {a} + {b} x distance (n={n}); blocked {T['fg_block'].get('all')}")
    for kd in sorted(T["fg_bins"], key=int):
        m, att = T["fg_bins"][kd]
        L.append(f"- {kd}-{int(kd)+4} yds: {m}/{att} = {m/att:.3f}" if att else f"- {kd}: none")
    L.append("")
    L.append(f"## Extra points {T['xp'].get('all')}  two-point success {T['two_good'].get('all')}  kneels {T['kneel_rate'].get('all')}  punt return TD {T['punt_td'].get('all')}  punts blocked {T['punt_block'].get('all')}")
    L.append("")
    L.append("## Matchup exponents (own offence, opposing defence); K = games of shrinkage")
    for name, m in T["matchup"].items():
        L.append(f"- {name}: league {m.get('league')}  K={m.get('K_games')} (median of {m.get('K_seasons')}; by season {m.get('K_by_season')})  raw (c,a,b,r2,n)={m.get('raw')}  shrunk={m.get('shrunk')}")
    L.append(f"- points vs yards/play elasticity (c, slope, r2, n): {T.get('pts_vs_ypp')}")
    L.append("")
    L.append("## Year-over-year persistence of team unit rates (slope of next season's deviation on this season's)")
    for k, v in T.get("persistence", {}).items():
        L.append(f"- {k}: slope {v['slope']}  n={v['n']} team-pairs over {v['pairs']} season pairs")
    L.append("")
    L.append("## Kickoff field position by rules era (receiver's yards to go, mean)")
    for era, h in T["kickoff"].items():
        n_ = sum(h.values()); mean = sum(int(k) * c for k, c in h.items()) / n_ if n_ else 0
        L.append(f"- {era}: n={n_} mean ytg {mean:.2f}  return TD {T['kickoff_td'].get(era)}")
    return "\n".join(L)


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__); return 2
    store_dir = argv[0]
    seasons = [2023, 2024, 2025, 2026]
    prior = 2025
    out = OUT_DEFAULT
    i = 1
    while i < len(argv):
        if argv[i] == "--seasons":
            seasons = [int(x) for x in argv[i + 1].split(",")]; i += 2
        elif argv[i] == "--prior-season":
            prior = int(argv[i + 1]); i += 2
        elif argv[i] == "--out":
            out = argv[i + 1]; i += 2
        else:
            i += 1
    stores = load_stores(store_dir, seasons)
    if not stores:
        print("no stores found", file=sys.stderr); return 1
    T = build_tables(stores, prior_season=prior)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(T, fh, separators=(",", ":"), sort_keys=True)
    print(report(T))
    print(f"\nwrote {out} ({os.path.getsize(out)//1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
