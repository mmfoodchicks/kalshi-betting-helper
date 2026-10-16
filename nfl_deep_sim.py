"""Play-by-play NFL engine -- the football twin of deep_sim (the pitch-by-pitch
MLB engine). Every simulated game is walked SNAP BY SNAP: down, distance, yards
to the end zone, the clock, the score and who has the ball, with an offence's
unit rates set against the defence it is actually facing.

Nothing in the engine is a drive-level rate. The play call, the pass chain
(sack -> interception -> completion -> yards), the rush yardage, the no-play
penalty, the fourth-down decision, the field-goal curve, the punt and kickoff
field position, the clock between snaps, the extra point and the two-point
decision are all EMPIRICAL tables from real play-by-play (research/
nfl_deep_fit.py over 2023-2026, ~190,000 play rows; the file is
research/data/nfl_deep_tables.json). Touchdowns, field goals, third downs,
turnovers on downs, blowouts, comebacks and ties are therefore emergent, not
parameters -- the validation section of the fit report lists the real-game
figures the engine is checked against (points, margin SD, drives, snaps,
touchdowns and punts per drive, overtime and tie rates).

MATCHUPS. A team profile carries its offence's and its defence's rates in
league units (completion %, yards per completion, sack and interception rates,
yards per carry, pass rate, penalties, pace) plus its kicker and punter. For a
play the offence's rate and the defence's ALLOWED rate are combined in log
(yards) or log-odds (probabilities) space with the exponents measured
leave-one-out in the fit: with both rates shrunk toward the league by the
fitted K, an offence carries ~1.0 of its own deviation into a game and a
defence ~0.7 of its own (yards per play 1.08 / 0.76, completions 1.11 / 0.91,
sacks 1.01 / 0.36, interceptions 0.39 / 0.28, penalties 0.48 / 0.52; fumbles
lost carry nothing measurable -- the between-team variance is indistinguishable
from zero in every season, a shrinkage K of 50 to 147,000 games -- so every team
fumbles at the league rate). A per-game zero-sum form tilt and a home-field yardage edge are
the only fitted free parameters; both are set against the measured margin SD
and home win rate (see _FORM_SD, _HFA_YDS).

RULES that are rules, not tables: two-minute warning, end of quarter and
half, the kneel-out when the lead is safe, overtime (regular season: ten
minutes, both teams possess, then sudden death, ties stand; postseason:
fifteen-minute periods until decided), the late-game fourth-down overrides a
situation table cannot see (a team down four with a minute left does not
punt), the onside kick when trailing late, and the field position after a
missed or blocked kick.

Speed: ~1.1 ms a game in pure Python (measured, this box), so a 2,000-game
matchup is two seconds and a 4,000-season deep run is ~750,000 games.
"""
import bisect
import json
import math
import os
import random
import zlib

import nfl_pbp as P

TABLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "research", "data", "nfl_deep_tables.json")
# Home field as a yardage edge: the home offence's positive yards x_HFA_YDS, the
# visitor's / _HFA_YDS, on every rush and completion. Fitted so two league-
# average teams give p_home ~0.540 against the measured 0.5402 home win rate
# (2023-2026 regular season, n=1,152). Refitted 2026-10-16 after the yardage
# multipliers moved to unbiased rounding (_scale): form 0.075, 20,000 games a
# point, 1.012 -> 0.5204, 1.016 -> 0.5300, 1.020 -> 0.5379, 1.024 -> 0.5446;
# at form 0.08, 40,000 games each, 1.019 -> 0.5372 and 1.021 -> 0.5374, and
# 1.022 -> 0.5450 over 60,000 -- the response is shallow and noisy here (SE
# ~0.0025 a point); a line through all of them crosses 0.5402 at ~1.021.
# That realizes ~+1.2 points of home margin against a real +2.04, deliberately --
# the same choice as the drive engine's _HFA_SCORE: real margins are right-skewed
# by blowouts, matching the mean overshoots the win rate, and the moneyline is
# the market that gets logged, calibrated and built into combos.
_HFA_YDS = 1.021
# Zero-sum game-day tilt on each side's yardage, e^{+/-g}, g ~ N(0, _FORM_SD),
# mean-preserving. The snap-level dice alone leave the engine's margin SD at
# 12.03 against the real conditional 13.3 (unconditional 14.43 less the
# spread's own ~5.4 SD), exactly as they did in the drive engine; the missing
# variance is anti-correlated between the sides, so it is added as a tilt
# rather than a symmetric widening that would ruin totals. Refitted with the
# unbiased rounding (10,000 league-average games a point, no home edge):
# 0.00 -> 12.03, 0.03 -> 12.30, 0.05 -> 12.49, 0.07 -> 13.12; 0.08 with the home
# edge -> 13.26 over 60,000 games. The total SD does not move with it
# (12.80 / 12.98 / 12.74 / 12.77).
_FORM_SD = 0.080
# Prior mass for the hierarchical smoothing of the situation tables.
_CALL_PRIOR = 20.0
_FOURTH_PRIOR = 8.0
_TWO_PRIOR = 6.0
_KNEEL_SNAP_S = 40          # a kneel burns the play clock
# A loss that reaches the offence's own end zone is a safety this often; the
# rest of the time the ball is held up at the 1 (the quarterback throws it
# away, the back is stopped short). The yardage tables are sampled without
# regard to the goal line behind the offence, so without this every such draw
# was a safety -- 0.6% of drives against the real 0.2%.
_SAFETY_SHARE = 0.33
_ONSIDE_RECOVERY = 0.12     # league-typical since the 2018 rule change; a rule, not fitted
_KO_MAX_YTG = 99

_T = {"tables": None, "path": None}


class _H:
    """Histogram sampler: integer values with counts, O(log n) draw."""
    __slots__ = ("vals", "cum", "total", "mean")

    def __init__(self, hist):
        items = sorted(((int(k), int(c)) for k, c in hist.items()), key=lambda kv: kv[0])
        self.vals = [k for k, _ in items]
        self.cum = []
        t = 0
        for _, c in items:
            t += c
            self.cum.append(t)
        self.total = t
        self.mean = (sum(k * c for k, c in items) / t) if t else 0.0

    def draw(self, rng):
        if self.total <= 0:
            return 0
        return self.vals[bisect.bisect_right(self.cum, rng.random() * self.total)]


def _logit(p):
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def _sig(z):
    if z > 35:
        return 1.0
    if z < -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def _stoch(x, rng):
    """x rounded to an integer at random, E[result] = x exactly."""
    i = math.floor(x)
    return i + (1 if rng.random() < x - i else 0)


def _scale(yds, m, rng):
    """A positive gain scaled by a team multiplier, rounded at RANDOM so the mean
    scales by exactly m. The old int(round(yds * m)) erased small multipliers:
    at m=1.02 a run of under 25 yards was unchanged, 18% of the intended change
    in rushing yards survived (43% for completions), the anchor's levels
    delivered 9-72% of their target margin and a third-and-two run converted at
    63.85% for every m between 0.85 and 1.15 (Opus review 2026-10-16,
    review_opus/r3_rounding.py, r4_level.py). Losses and the red-zone
    touchdown sentinel (>= 99) pass through untouched."""
    if yds <= 0 or yds >= 99:
        return yds
    return max(1, _stoch(yds * m, rng))


def _rate(cell, prior_p, prior_n):
    """Smoothed rate from a [hits, n] cell toward a parent rate."""
    if not cell:
        return prior_p
    k, n = cell
    return (k + prior_p * prior_n) / (n + prior_n)


class Tables:
    """The fitted JSON, precomputed into samplers and smoothed rates."""

    def __init__(self, raw):
        self.raw = raw
        self.league = raw["league"]
        m = raw["matchup"]

        def exps(name, default):
            s = (m.get(name) or {}).get("shrunk")
            if not s:
                return default
            a, b = s[1], s[2]
            return (min(1.3, max(0.0, a)), min(1.3, max(0.0, b)))
        self.exp = {"ypcomp": exps("ypcomp", (1.0, 0.7)), "ypc": exps("ypc", (1.0, 0.7)),
                    "comp": exps("comp", (1.0, 0.9)), "sack": exps("sack", (1.0, 0.35)),
                    "int": exps("int", (0.4, 0.3)), "pen": exps("pen", (0.5, 0.5)),
                    "pass_rate": exps("pass_rate", (1.0, 0.5)),
                    # fumbles: K ~ 150,000 games -- nothing carries; league rate for all
                    "fum": (0.0, 0.0)}
        self.K = {k: (m.get(k) or {}).get("K_games") for k in m}
        # ---- play call, smoothed down the hierarchy
        call_dd = {k: v for k, v in raw["call_dd"].items()}
        call_marg = raw["call_marg"]
        self.call = {}
        lg_pass = self.league["pass_rate"]
        for key, cell in raw["call"].items():
            ts, ss, down, db, zn = key.split(",")
            p_dd = _rate(call_dd.get(f"{down},{db}"), lg_pass, _CALL_PRIOR)
            p_m = _rate(call_marg.get(f"{down},{db},{zn}"), p_dd, _CALL_PRIOR)
            self.call[(int(ts), int(ss), int(down), int(db), int(zn))] = _rate(cell, p_m, _CALL_PRIOR)
        self.call_marg = {}
        for key, cell in call_marg.items():
            down, db, zn = key.split(",")
            p_dd = _rate(call_dd.get(f"{down},{db}"), lg_pass, _CALL_PRIOR)
            self.call_marg[(int(down), int(db), int(zn))] = _rate(cell, p_dd, _CALL_PRIOR)
        # ---- pass chain by (down, dist bucket)
        self.sack = {tuple(int(x) for x in k.split(",")): _rate(v, self.league["sack"], 30) for k, v in raw["sack"].items()}
        self.int = {tuple(int(x) for x in k.split(",")): _rate(v, self.league["int"], 60) for k, v in raw["int"].items()}
        self.comp = {tuple(int(x) for x in k.split(",")): _rate(v, self.league["comp"], 30) for k, v in raw["comp"].items()}
        # yards by (down, distance bucket, zone), the (distance, zone) marginal behind it
        self.comp_yds = {tuple(int(x) for x in k.split(",")): _H(v) for k, v in raw["comp_yds"].items()}
        self.rush_yds = {tuple(int(x) for x in k.split(",")): _H(v) for k, v in raw["rush_yds"].items()}
        self.comp_yds_dz = {tuple(int(x) for x in k.split(",")): _H(v) for k, v in raw["comp_yds_dz"].items()}
        self.rush_yds_dz = {tuple(int(x) for x in k.split(",")): _H(v) for k, v in raw["rush_yds_dz"].items()}
        self.sack_yds = _H(raw["sack_yds"]["all"])
        self.int_ret = _H(raw["int_ret"]["all"])
        self.fum_spot = _H(raw["fum_spot"]["all"])
        fl = raw["fumble_lost"]
        self.fum = {k: (v[0] / v[1] if v and v[1] else 0.0) for k, v in fl.items()}
        oob = raw["oob"]
        self.oob = {k: (v[0] / v[1] if v and v[1] else 0.0) for k, v in oob.items()}
        # ---- penalties
        self.pen_rate = {int(k): (v[0] / v[1] if v[1] else 0.0) for k, v in raw["pen_rate"].items()}
        self.pen_out = {}
        for down, hist in raw["pen_out"].items():
            pairs = sorted(((tuple(int(x) for x in k.split(",")), c) for k, c in hist.items()), key=lambda kv: kv[0])
            cum, t = [], 0
            for _, c in pairs:
                t += c
                cum.append(t)
            self.pen_out[int(down)] = ([pr for pr, _ in pairs], cum, t)
        # ---- fourth down
        self.fourth = {tuple(int(x) for x in k.split(",")): v for k, v in raw["fourth"].items()}
        self.fourth_zdt = {tuple(int(x) for x in k.split(",")): v for k, v in raw["fourth_zdt"].items()}
        self.fourth_zd = {tuple(int(x) for x in k.split(",")): v for k, v in raw["fourth_zd"].items()}
        self.fourth_z = {int(k): v for k, v in raw["fourth_z"].items()}
        # ---- kicking
        self.fg_a, self.fg_b = raw["fg_logit"][0], raw["fg_logit"][1]
        fb = raw["fg_block"].get("all") or [0, 1]
        self.fg_block = fb[0] / fb[1] if fb[1] else 0.015
        self.xp = (raw["xp"]["all"][0] / raw["xp"]["all"][1]) if raw["xp"].get("all") else 0.95
        self.two_good = (raw["two_good"]["all"][0] / raw["two_good"]["all"][1]) if raw["two_good"].get("all") else 0.48
        self.two_att = {tuple(int(x) for x in k.split(",")): v for k, v in raw["two_att"].items()}
        self.punt = {int(k): _H(v) for k, v in raw["punt"].items()}
        pb = raw["punt_block"].get("all") or [0, 1]
        self.punt_block = pb[0] / pb[1] if pb[1] else 0.005
        pt = raw["punt_td"].get("all") or [0, 1]
        self.punt_td = pt[0] / pt[1] if pt[1] else 0.0035
        # The NEWEST kickoff-rules era the fit carries ("2025+" > "2024" > "2023"):
        # the touchback moved twice in the fit window and the engine plays under
        # the current rules. It used to fall back to the OLDEST era when 2025+
        # was absent -- a 2023-24 refit kicked off under 2023 rules (74.5 yards
        # to go against 69.9) and ran totals ~2 points low.
        era = max(raw["kickoff"])
        self.kickoff = _H(raw["kickoff"][era])
        self.kickoff_era = era
        kt = raw["kickoff_td"].get(era) or [0, 1]
        self.ko_td = kt[0] / kt[1] if kt[1] else 0.002
        # ---- clock
        self.clock = {}
        for key, hist in raw["clock"].items():
            cls, tempo = key.split(",")
            self.clock[(cls, int(tempo))] = _H(hist)

    # -- lookups with fallbacks ---------------------------------------------
    def p_pass(self, ts, ss, down, db, zn):
        p = self.call.get((ts, ss, down, db, zn))
        if p is None:
            p = self.call_marg.get((down, db, zn), self.league["pass_rate"])
        return p

    def fourth_choice(self, z5, db, ss, ts):
        """(p_go, p_punt, p_fg) smoothed down the hierarchy."""
        base = self.fourth_z.get(z5) or [1, 1, 1]
        tot = float(sum(base)) or 1.0
        p = [c / tot for c in base]
        for cell in (self.fourth_zd.get((z5, db)), self.fourth_zdt.get((z5, db, ts)),
                     self.fourth.get((z5, db, ss, ts))):
            if cell:
                n = float(sum(cell))
                p = [(cell[i] + _FOURTH_PRIOR * p[i]) / (n + _FOURTH_PRIOR) for i in range(3)]
        return p

    def p_two(self, sd_after6, late):
        cell = self.two_att.get((max(-16, min(16, sd_after6)), late))
        return _rate(cell, 0.06, _TWO_PRIOR)

    def clock_draw(self, cls, tempo, rng):
        h = self.clock.get((cls, tempo)) or self.clock.get((cls, 0))
        return h.draw(rng) if h else 30


def tables(path=None):
    """The module-level Tables singleton (loaded once per process)."""
    path = path or TABLES_PATH
    if _T["tables"] is None or _T["path"] != path:
        with open(path) as fh:
            _T["tables"] = Tables(json.load(fh))
        _T["path"] = path
    return _T["tables"]


# ---- Profiles -----------------------------------------------------------------
PROFILE_KEYS = ("pass_rate", "comp", "ypcomp", "sack", "int", "ypc", "fum", "pen", "sec_per_snap")


def league_profile(T=None, abbr="LG", name="League average"):
    """A team that is exactly the league in every unit -- the fallback and the
    reference opponent the level anchor measures teams against."""
    T = T or tables()
    L = T.league
    unit = {"pass_rate": L["pass_rate"], "comp": L["comp"], "ypcomp": L["ypc_pass"],
            "sack": L["sack"], "int": L["int"], "ypc": L["ypc"], "fum": L["fum"],
            "pen": L["pen"], "sec_per_snap": L["sec_per_snap"]}
    return {"abbr": abbr, "name": name, "g": 0, "off": dict(unit), "def": dict(unit),
            "st": {"fg_shift": 0.0, "xp": T.xp, "punt_shift": 0.0, "ko_shift": 0.0},
            "level": 1.0}


class _Side:
    """Per-matchup precomputation: one offence against the other's defence."""
    __slots__ = ("m_pass", "m_rush", "d_comp", "d_sack", "d_int", "d_pen", "d_call", "tempo",
                 "fg_shift", "xp", "punt_shift", "ko_shift", "abbr", "fum_mult")

    def __init__(self, me, opp, T, home_edge):
        L = T.league
        o, d = me["off"], opp["def"]
        ea, eb = T.exp["ypcomp"]
        # A profile's `level` is the anchor's (half on its offence, half on its
        # defence); `level_off` / `level_def` override one side -- the weekly
        # roster factor is the offence's alone.
        lvl = ((me.get("level_off") or me.get("level") or 1.0)
               / (opp.get("level_def") or opp.get("level") or 1.0))
        self.m_pass = (o["ypcomp"] / L["ypc_pass"]) ** ea * (d["ypcomp"] / L["ypc_pass"]) ** eb * home_edge * lvl
        ea, eb = T.exp["ypc"]
        self.m_rush = (o["ypc"] / L["ypc"]) ** ea * (d["ypc"] / L["ypc"]) ** eb * home_edge * lvl

        def shift(key, lg):
            a, b = T.exp[key]
            return a * (_logit(o[key]) - _logit(lg)) + b * (_logit(d[key]) - _logit(lg))
        self.d_comp = shift("comp", L["comp"])
        self.d_sack = shift("sack", L["sack"])
        self.d_int = shift("int", L["int"])
        self.d_pen = shift("pen", L["pen"])
        self.d_call = shift("pass_rate", L["pass_rate"])
        a, b = T.exp["fum"]
        self.fum_mult = (o["fum"] / L["fum"]) ** a * (d["fum"] / L["fum"]) ** b if L["fum"] else 1.0
        self.tempo = (o.get("sec_per_snap") or L["sec_per_snap"]) / L["sec_per_snap"]
        st = me.get("st") or {}
        self.fg_shift = st.get("fg_shift", 0.0)
        self.xp = st.get("xp", T.xp)
        self.punt_shift = st.get("punt_shift", 0.0)
        self.ko_shift = st.get("ko_shift", 0.0)
        self.abbr = me.get("abbr")


def _box():
    return {"pts": 0, "q": [0, 0, 0, 0, 0], "plays": 0, "pass_att": 0, "comp": 0, "pass_yds": 0,
            "sacks": 0, "sack_yds": 0, "int": 0, "rush_att": 0, "rush_yds": 0, "td_pass": 0,
            "td_rush": 0, "td_ret": 0, "fg_made": 0, "fg_att": 0, "punts": 0, "fum_lost": 0,
            "first": 0, "drives": 0, "pen": 0, "top": 0, "kneels": 0, "fourth_go": 0,
            "third_att": 0, "third_conv": 0, "fourth_conv": 0, "rz_trips": 0, "rz_td": 0,
            "conv_att": [0, 0, 0, 0, 0], "conv": [0, 0, 0, 0, 0],
            # how this side's drives ended (diagnostics; summed, not averaged)
            "ends": {"td": 0, "fg": 0, "fgm": 0, "punt": 0, "int": 0, "fum": 0, "downs": 0,
                     "safety": 0, "clock": 0}}


def play_game(home, away, rng, T=None, neutral=False, playoff=False, form_sd=None):
    """One game. Returns {"h": box, "a": box, "ot": bool, "tie": bool}."""
    T = T or tables()
    if form_sd is None:
        form_sd = _FORM_SD
    hfa = 1.0 if neutral else _HFA_YDS
    g = rng.gauss(0.0, form_sd) if form_sd else 0.0
    th, ta = math.exp(g - form_sd * form_sd / 2.0), math.exp(-g - form_sd * form_sd / 2.0)
    sides = (_Side(home, away, T, hfa * th), _Side(away, home, T, ta / hfa))
    box = (_box(), _box())
    S = {"per": 1, "clk": 900, "off": None, "down": 1, "dist": 10, "ytg": 75,
         "ot_poss": [False, False], "sudden": False, "over": False, "kick_pending": None,
         "drive_live": False, "playoff": playoff}
    first_recv = rng.randrange(2)
    S["half2_recv"] = 1 - first_recv
    _kickoff(S, box, sides, T, rng, kicking=1 - first_recv)
    # A kickoff the RULES owe (the second half, an overtime period) is queued by
    # _end_period and played here, before the next snap -- never inline. Inline,
    # whatever ended the period (a punt, a turnover, a missed kick) went on to
    # hand the ball over AFTER the half-time kickoff had already been set up, and
    # overrode it: in 4.2% of 20,000 league-average games the wrong team opened
    # the second half (Opus review, 2026-10-16, review_opus/r1_period_bugs.py).
    while not S["over"]:
        k = S["kick_pending"]
        if k is not None:
            S["kick_pending"] = None
            _kickoff(S, box, sides, T, rng, kicking=k)
            continue
        _snap(S, box, sides, T, rng, playoff)
    h, a = box
    return {"h": h, "a": a, "ot": S["per"] > 4, "tie": h["pts"] == a["pts"]}


# ---- Mechanics ------------------------------------------------------------------
def _score(S, box, side, pts):
    box[side]["pts"] += pts
    box[side]["q"][min(4, S["per"] - 1)] += pts


def _sd(S, box):
    o = S["off"]
    return box[o]["pts"] - box[1 - o]["pts"]


def _new_drive(S, box, off, ytg):
    S["off"] = off
    S["down"], S["dist"], S["ytg"] = 1, min(10, ytg), max(1, min(99, ytg))
    box[off]["drives"] += 1
    S["drive_live"] = True
    S["rz"] = False
    # The fourth-down choice drawn when a down arose belongs to THAT down. When
    # the snap never came (the half ran out, the clock-expiring kick fired) it
    # used to survive into the next drive and decide some later fourth down --
    # 16% of games carried one.
    S.pop("fourth_pre", None)
    if S["per"] >= 5:
        S["ot_poss"][off] = True


def _kickoff(S, box, sides, T, rng, kicking):
    """Kickoff by `kicking`; the receiver's first snap is set up. Onside when the
    kicking team trails late."""
    recv = 1 - kicking
    sd_k = box[kicking]["pts"] - box[recv]["pts"]
    if S["per"] == 4 and S["clk"] <= 180 and sd_k < 0 and sd_k >= -16:
        # onside: recovered by the kicker ~12% (post-2018 formation rules)
        if rng.random() < _ONSIDE_RECOVERY:
            _new_drive(S, box, kicking, 55)
        else:
            _new_drive(S, box, recv, 45)
        S["clk"] = max(0, S["clk"] - 4)
        return
    if rng.random() < T.ko_td:
        box[recv]["td_ret"] += 1
        _return_td(S, box, sides, T, rng, recv)
        return
    ytg = T.kickoff.draw(rng) - _stoch(sides[recv].ko_shift, rng)
    _new_drive(S, box, recv, max(1, min(_KO_MAX_YTG, ytg)))
    S["clk"] = max(0, S["clk"] - T.clock_draw("kickoff", 0, rng))


def _try_points(S, box, sides, T, rng, side):
    """The try after a touchdown (six already on the board): two or kick. Untimed,
    so it is scored BEFORE any end-of-period ruling."""
    sd6 = box[side]["pts"] - box[1 - side]["pts"]
    late = 1 if S["per"] >= 4 else 0
    if rng.random() < T.p_two(sd6, late):
        if rng.random() < T.two_good:
            _score(S, box, side, 2)
    elif rng.random() < sides[side].xp:
        _score(S, box, side, 1)


def _return_td(S, box, sides, T, rng, scorer):
    """A touchdown by the side WITHOUT the ball (kick or punt return, pick-six,
    fumble return): six, the try, then whatever the rules owe next."""
    _score(S, box, scorer, 6)
    S["drive_live"] = False
    if S["per"] >= 5 and S["sudden"]:
        S["over"] = True
        return
    _try_points(S, box, sides, T, rng, scorer)
    _after_score(S, box, sides, T, rng, scorer=scorer)


def _after_score(S, box, sides, T, rng, scorer):
    """After any score is final (try included, clock already run): overtime
    decided -> over; a kickoff the rules already owe (second half, overtime)
    supersedes; a period the play ran out ends now; otherwise the scorer kicks.
    The kickoff after a score at the END of the first or third quarter used to
    be skipped (the scorer kept the ball on fourth down; 0.85% of games)."""
    S["drive_live"] = False
    if S["over"]:
        return
    if S["per"] >= 5 and _ot_decided(S, box):
        S["over"] = True
        return
    if S["kick_pending"] is not None:
        return
    if S["clk"] <= 0:
        _end_period(S, box, sides, T, rng)
        if S["over"] or S["kick_pending"] is not None:
            return
    _kickoff(S, box, sides, T, rng, kicking=scorer)


def _ot_decided(S, box):
    h, a = box[0]["pts"], box[1]["pts"]
    if S["sudden"]:
        return h != a
    # both have possessed -> sudden death from here; decided if not level
    if S["ot_poss"][0] and S["ot_poss"][1]:
        S["sudden"] = True
        return h != a
    return False


def _change(S, box, sides, T, rng, new_ytg):
    """Change of possession at `new_ytg` for the new offence -- unless the period
    that just ended owes a kickoff (half-time, overtime), which supersedes it."""
    S["drive_live"] = False
    if S["over"] or S["kick_pending"] is not None:
        return
    if S["per"] >= 5 and _ot_decided(S, box):
        S["over"] = True
        return
    _new_drive(S, box, 1 - S["off"], new_ytg)


def _end_period(S, box, sides, T, rng):
    per = S["per"]
    if per == 1 or per == 3:
        S["per"] += 1
        S["clk"] = 900
        return
    if per == 2:
        if S["drive_live"] and S["off"] is not None:
            box[S["off"]]["ends"]["clock"] += 1
        S["per"], S["clk"] = 3, 900
        S["drive_live"] = False
        S["kick_pending"] = 1 - S["half2_recv"]           # played by play_game's loop
        return
    h, a = box[0]["pts"], box[1]["pts"]
    if S["drive_live"] and S["off"] is not None:
        box[S["off"]]["ends"]["clock"] += 1
    if per == 4:
        if h != a:
            S["over"] = True
            return
        # overtime: a fresh coin toss, both teams get a possession
        S["per"], S["clk"] = 5, (900 if S.get("playoff") else 600)
        S["ot_poss"] = [False, False]
        S["sudden"] = False
        S["drive_live"] = False
        S["kick_pending"] = 1 - rng.randrange(2)
        return
    # end of an overtime period
    if h != a or not S.get("playoff"):
        S["over"] = True                      # regular season: a tie stands
        return
    S["per"] += 1
    S["clk"] = 900
    if not S["drive_live"]:
        S["kick_pending"] = 1 - rng.randrange(2)


def _tempo(S, box):
    per, clk, sd = S["per"], S["clk"], _sd(S, box)
    ts = P.time_state(per, clk)
    if per == 2 and clk <= 120:
        return 1
    if ts == 2:
        return 1 if sd <= 0 else 2
    if ts == 1 and per == 4 and sd <= -9:
        return 1
    return 0


def _run_clock(S, box, sides, T, rng, cls, tempo_mult=1.0, defer_end=False):
    """Consume the time to the next snap; stop at the two-minute warning and the
    end of the period. `defer_end` reports a period that ran out without ending
    it, so an untimed try can be scored first."""
    tempo = _tempo(S, box)
    dt = T.clock_draw(cls, tempo, rng)
    if tempo == 0 and cls in ("run", "oob"):
        dt = int(round(dt * tempo_mult))
    clk = S["clk"]
    if S["per"] in (2, 4) and clk > 120 and clk - dt < 120:
        dt = clk - 120
    S["clk"] = max(0, clk - dt)
    box[S["off"]]["top"] += min(dt, clk)
    if S["clk"] <= 0:
        if not defer_end:
            _end_period(S, box, sides, T, rng)
        return True
    return False


def _fourth_decision(S, box, sides, T, rng, playoff):
    """0 go, 1 punt, 2 field goal. The table knows the situation; the rules
    cover what it cannot: the trailing team with no time, overtime, a kick
    beyond range."""
    per, clk, ytg, dist = S["per"], S["clk"], S["ytg"], S["dist"]
    sd = _sd(S, box)
    ts, ss = P.time_state(per, clk), P.score_state(sd)
    kd = ytg + 17
    makeable = kd <= 66
    if per >= 5:
        if sd == 0 and makeable and S["sudden"]:
            return 2                              # the winner
        if sd < 0:
            return 2 if (makeable and sd >= -3) else 0
    # Trailing late: inside three minutes a team down more than a field goal
    # does not punt, and down three or less it kicks when the kick is there
    # (the situation table has too few of these snaps to know it)
    late = per == 4 and clk <= 180
    if late and sd < 0:
        if sd >= -3 and makeable and (clk <= 150 or ytg > 4):
            return 2
        if sd < -3 or not makeable:
            return 0
    if per == 4 and clk <= 40 and sd < 0:
        return 0 if (sd < -3 or not makeable) else 2
    p_go, p_punt, p_fg = T.fourth_choice(min(19, max(0, (ytg - 1) // 5)), P.dist_bucket(max(1, dist)), ss, ts)
    if not makeable:
        p_go, p_punt, p_fg = p_go + p_fg * (0.4 if ytg <= 55 else 0.0), p_punt + p_fg * (0.6 if ytg <= 55 else 1.0), 0.0
    r = rng.random() * (p_go + p_punt + p_fg)
    return 0 if r < p_go else 1 if r < p_go + p_punt else 2


def _punt(S, box, sides, T, rng):
    off = S["off"]
    box[off]["punts"] += 1
    box[off]["ends"]["punt"] += 1
    ytg = S["ytg"]
    if rng.random() < T.punt_block:
        _run_clock(S, box, sides, T, rng, "punt")
        _change(S, box, sides, T, rng, max(1, 100 - ytg - 5))
        return
    if rng.random() < T.punt_td:
        box[1 - off]["td_ret"] += 1
        _run_clock(S, box, sides, T, rng, "punt", defer_end=True)
        _return_td(S, box, sides, T, rng, 1 - off)
        return
    h = T.punt.get(min(95, max(30, ytg // 5 * 5))) or T.punt[65]
    recv_ytg = h.draw(rng) + _stoch(sides[off].punt_shift, rng)
    # a period that ends on the punt: the receiver starts the next quarter at the
    # spot; at half-time / regulation the owed kickoff supersedes (_change)
    _run_clock(S, box, sides, T, rng, "punt")
    _change(S, box, sides, T, rng, max(1, min(99, recv_ytg)))


def _field_goal(S, box, sides, T, rng):
    off = S["off"]
    ytg = S["ytg"]
    kd = ytg + 17
    box[off]["fg_att"] += 1
    if rng.random() < T.fg_block:
        box[off]["ends"]["fgm"] += 1
        _run_clock(S, box, sides, T, rng, "fg")
        _change(S, box, sides, T, rng, max(1, 100 - ytg - 7))
        return
    p = _sig(T.fg_a + T.fg_b * kd + sides[off].fg_shift)
    if rng.random() < p:
        box[off]["fg_made"] += 1
        box[off]["ends"]["fg"] += 1
        _score(S, box, off, 3)
        S["drive_live"] = False
        if S["per"] >= 5 and _ot_decided(S, box):
            S["over"] = True
            return
        _run_clock(S, box, sides, T, rng, "fg")
        _after_score(S, box, sides, T, rng, scorer=off)
        return
    # missed: the defence takes over at the spot of the kick (touchback inside the 20)
    box[off]["ends"]["fgm"] += 1
    _run_clock(S, box, sides, T, rng, "fg")
    _change(S, box, sides, T, rng, max(1, 100 - max(20, ytg + 7)))


def _snap(S, box, sides, T, rng, playoff):
    S["playoff"] = playoff
    off = S["off"]
    me = sides[off]
    bx = box[off]
    per, clk, down, dist, ytg = S["per"], S["clk"], S["down"], S["dist"], S["ytg"]
    sd = _sd(S, box)
    # ---- kneel-out: the lead is safe and the clock can be burned
    if per == 4 and sd > 0 and clk <= _KNEEL_SNAP_S * (4 - down) + 5 and down <= 4:
        bx["kneels"] += 1
        bx["plays"] += 1
        dt = min(clk, _KNEEL_SNAP_S + rng.randint(-2, 1))
        S["clk"] = clk - dt
        bx["top"] += dt
        S["down"] += 1
        S["ytg"] = min(99, ytg + 1)
        if S["clk"] <= 0:
            _end_period(S, box, sides, T, rng)
        elif S["down"] > 4:
            _change(S, box, sides, T, rng, max(1, 100 - S["ytg"]))
        return
    # ---- the clock is about to expire with a kick on the table: take it now,
    # whatever the down (the end of the half; the end of the game when a field
    # goal ties or wins). Without this the half ran out with the ball in range
    # and the second quarter scored 6.1 a side against the real 6.93.
    if clk <= 20 and (down >= 2 or clk <= 8) and ytg + 17 <= 62 and (
            per == 2 or (per == 4 and -3 <= sd <= 0) or (per >= 5 and sd == 0)):
        _field_goal(S, box, sides, T, rng)
        return
    # ---- fourth down: punt or kick first (the choice was drawn when the down
    # arose, so the clock before it already matched the choice)
    if down == 4:
        choice = S.pop("fourth_pre", None)
        if choice is None:
            choice = _fourth_decision(S, box, sides, T, rng, playoff)
        if choice == 1:
            _punt(S, box, sides, T, rng)
            return
        if choice == 2:
            _field_goal(S, box, sides, T, rng)
            return
        bx["fourth_go"] += 1
    # ---- a no-play penalty before the snap counts
    p_pen = T.pen_rate.get(down, 0.035)
    p_pen = _sig(_logit(p_pen) + me.d_pen)
    if rng.random() < p_pen:
        bx["pen"] += 1
        pairs, cum, tot = T.pen_out.get(down) or T.pen_out[1]
        yds, first = pairs[bisect.bisect_right(cum, rng.random() * tot)]
        ytg2 = max(1, min(99, ytg - yds))
        if first:
            S["down"], S["dist"], S["ytg"] = 1, min(10, ytg2), ytg2
        else:
            S["ytg"] = ytg2
            S["dist"] = max(1, dist - yds) if yds > 0 else dist - yds
        _run_clock(S, box, sides, T, rng, "pen", me.tempo)
        return
    # ---- the play
    ts, ss = P.time_state(per, clk), P.score_state(sd)
    db, zn = P.dist_bucket(max(1, dist)), P.zone(max(1, ytg))
    p_pass = _sig(_logit(T.p_pass(ts, ss, down, db, zn)) + me.d_call)
    bx["plays"] += 1
    if down == 3:
        bx["third_att"] += 1
    bx["conv_att"][down] += 1
    if ytg <= 20 and not S.get("rz"):
        S["rz"] = True
        bx["rz_trips"] += 1
    cls = "run"                    # what the clock does after the play
    yds = 0
    turnover_ytg = None            # the defence's yards to go after a turnover
    to_kind = None
    td = False
    if rng.random() < p_pass:
        # sack -> interception -> completion -> yards
        if rng.random() < _sig(_logit(T.sack.get((down, db), T.league["sack"])) + me.d_sack):
            bx["sacks"] += 1
            yds = T.sack_yds.draw(rng)
            bx["sack_yds"] += -yds
            if rng.random() < T.fum["sack"] * me.fum_mult:
                bx["fum_lost"] += 1
                turnover_ytg = max(1, min(99, 100 - (ytg - yds) - T.fum_spot.draw(rng)))
                to_kind = "fum"
        else:
            bx["pass_att"] += 1
            if rng.random() < _sig(_logit(T.int.get((down, db), T.league["int"])) + me.d_int):
                bx["int"] += 1
                adv = T.int_ret.draw(rng)
                if adv >= 99 or 100 - ytg - adv <= 0:
                    # pick-six
                    box[1 - off]["td_ret"] += 1
                    _run_clock(S, box, sides, T, rng, "to", me.tempo, defer_end=True)
                    _return_td(S, box, sides, T, rng, 1 - off)
                    return
                turnover_ytg = max(1, min(99, 100 - ytg - adv))
                to_kind = "int"
            elif rng.random() < _sig(_logit(T.comp.get((down, db), T.league["comp"])) + me.d_comp):
                bx["comp"] += 1
                h = T.comp_yds.get((down, db, zn)) or T.comp_yds_dz.get((db, zn)) or T.comp_yds_dz[(2, 3)]
                yds = _scale(h.draw(rng), me.m_pass, rng)
                if yds >= ytg:
                    yds = ytg
                    td = True
                    bx["td_pass"] += 1
                bx["pass_yds"] += yds
                if not td and rng.random() < T.fum["comp"] * me.fum_mult:
                    bx["fum_lost"] += 1
                    turnover_ytg = max(1, min(99, 100 - (ytg - yds) - T.fum_spot.draw(rng)))
                    to_kind = "fum"
                elif rng.random() < T.oob["comp"]:
                    cls = "oob"
            else:
                cls = "inc"            # incomplete
    else:
        bx["rush_att"] += 1
        h = T.rush_yds.get((down, db, zn)) or T.rush_yds_dz.get((db, zn)) or T.rush_yds_dz[(2, 3)]
        yds = _scale(h.draw(rng), me.m_rush, rng)
        if yds >= ytg:
            yds = ytg
            td = True
            bx["td_rush"] += 1
        bx["rush_yds"] += yds
        if not td and rng.random() < T.fum["rush"] * me.fum_mult:
            bx["fum_lost"] += 1
            turnover_ytg = max(1, min(99, 100 - (ytg - yds) - T.fum_spot.draw(rng)))
            to_kind = "fum"
        elif rng.random() < T.oob["rush"]:
            cls = "oob"
    # ---- resolve
    if td:
        _score(S, box, off, 6)
        bx["ends"]["td"] += 1
        if down == 3:
            bx["third_conv"] += 1
        bx["first"] += 1
        bx["conv"][down] += 1
        if S.get("rz"):
            bx["rz_td"] += 1
        S["drive_live"] = False
        if S["per"] >= 5 and S["sudden"]:
            S["over"] = True                  # sudden death: no try
            return
        # The play's time comes off, the untimed try is scored, and only THEN
        # does a period that ran out end -- so a touchdown that ties the game at
        # the gun is decided by the kick, and a touchdown as the first quarter
        # expires no longer advanced the period twice (skipping a quarter).
        ended = _run_clock(S, box, sides, T, rng, "score", me.tempo, defer_end=True)
        _try_points(S, box, sides, T, rng, off)
        if ended:
            _end_period(S, box, sides, T, rng)
        _after_score(S, box, sides, T, rng, scorer=off)
        return
    ytg2 = ytg - yds
    if ytg2 >= 100 and rng.random() < _SAFETY_SHARE:
        # tackled in the end zone: safety, then a free kick to the scorers
        _score(S, box, 1 - off, 2)
        bx["ends"]["safety"] += 1
        S["drive_live"] = False
        if S["per"] >= 5 and _ot_decided(S, box):
            S["over"] = True
            return
        _run_clock(S, box, sides, T, rng, "to", me.tempo)
        if S["over"] or S["kick_pending"] is not None:
            return
        # the free kick to the scorers (also after a safety that ended the 1st/3rd)
        h = T.punt[80]
        _new_drive(S, box, 1 - off, max(1, min(99, h.draw(rng) + 8)))
        return
    if turnover_ytg is not None:
        bx["ends"][to_kind or "fum"] += 1
        # The old guard here ("per == 3 and clk == 900") missed every half-time
        # kickoff that took time off the clock, and overtime entirely; _change
        # now defers to an owed kickoff itself.
        _run_clock(S, box, sides, T, rng, "to", me.tempo)
        _change(S, box, sides, T, rng, turnover_ytg)
        return
    if ytg2 >= 100:
        ytg2 = 99                 # held up at the goal line / thrown away instead
    S["ytg"] = ytg2
    if yds >= dist:
        bx["first"] += 1
        bx["conv"][down] += 1
        if down == 3:
            bx["third_conv"] += 1
        elif down == 4:
            bx["fourth_conv"] += 1
        S["down"], S["dist"] = 1, min(10, ytg2)
    else:
        S["down"] += 1
        S["dist"] = dist - yds
    if S["down"] > 4:
        # turnover on downs
        bx["ends"]["downs"] += 1
        _run_clock(S, box, sides, T, rng, "to", me.tempo)
        _change(S, box, sides, T, rng, max(1, 100 - ytg2))
        return
    # the clock to the next snap depends on what the next snap is
    if S["down"] == 4:
        nxt = _fourth_decision(S, box, sides, T, rng, playoff)
        S["fourth_pre"] = nxt
        if nxt == 1:
            cls = "prepunt"
        elif nxt == 2:
            cls = "prefg"
    _run_clock(S, box, sides, T, rng, cls, me.tempo)


# ---- Monte Carlo over one matchup ---------------------------------------------
def simulate(home, away, n=2000, seed=None, neutral=False, playoff=False, T=None, form_sd=None):
    """n games of away @ home. Returns the summary the boards read: win
    probabilities for BOTH sides (and the tie), score means, margin and total
    distributions, team box means."""
    T = T or tables()
    # crc32, not hash(): Python randomizes str hashes per process, so the same
    # matchup gave different numbers on every worker and on the PC
    rng = random.Random(seed if seed is not None
                        else zlib.crc32(f"{home.get('abbr')}|{away.get('abbr')}".encode()))
    hw = aw = ties = ot = 0
    margins, totals = [], []
    acc = ({}, {})
    h1 = [0, 0]
    for _ in range(n):
        g = play_game(home, away, rng, T, neutral=neutral, playoff=playoff, form_sd=form_sd)
        h, a = g["h"], g["a"]
        if h["pts"] > a["pts"]:
            hw += 1
        elif a["pts"] > h["pts"]:
            aw += 1
        else:
            ties += 1
        ot += g["ot"]
        margins.append(h["pts"] - a["pts"])
        totals.append(h["pts"] + a["pts"])
        h1[0] += h["q"][0] + h["q"][1]
        h1[1] += a["q"][0] + a["q"][1]
        for i, b in enumerate((h, a)):
            A = acc[i]
            for k, v in b.items():
                if k in ("q", "conv_att", "conv", "ends"):
                    continue
                A[k] = A.get(k, 0) + v
    mean_box = [{k: round(v / n, 2) for k, v in A.items()} for A in acc]
    mm = sum(margins) / n
    mt = sum(totals) / n
    sd_m = math.sqrt(sum((x - mm) ** 2 for x in margins) / max(1, n - 1))
    sd_t = math.sqrt(sum((x - mt) ** 2 for x in totals) / max(1, n - 1))
    spread_ladder = {"home": {}, "away": {}}
    for m in (1, 3, 4, 7, 10, 14):
        spread_ladder["home"][str(m)] = round(100 * sum(1 for x in margins if x >= m) / n, 1)
        spread_ladder["away"][str(m)] = round(100 * sum(1 for x in margins if x <= -m) / n, 1)
    total_ladder = []
    base = round(mt)
    for line in [base + d + 0.5 for d in range(-8, 9)]:
        over = sum(1 for t in totals if t > line) / n
        if 0.04 <= over <= 0.96:
            total_ladder.append({"line": line, "over_pct": round(over * 100, 1),
                                 "under_pct": round((1 - over) * 100, 1)})
    # Kalshi's two-way moneyline: a tie splits (the market voids / refunds a
    # tie, so each side's probability is conditional on a decision)
    dec = (hw + aw) or 1
    p_home, p_home_raw, p_tie = round(hw / dec, 4), round(hw / n, 4), round(ties / n, 4)
    return {"home": home.get("abbr"), "away": away.get("abbr"), "n_sims": n,
            # the complements are taken AFTER rounding so each pair / triple sums to one exactly
            "p_home": p_home, "p_away": round(1 - p_home, 4),
            "p_home_raw": p_home_raw, "p_away_raw": round(1 - p_home_raw - p_tie, 4), "p_tie": p_tie,
            "p_ot": round(ot / n, 4),
            "exp_home": round(mean_box[0]["pts"], 2), "exp_away": round(mean_box[1]["pts"], 2),
            "exp_total": round(mt, 2), "mean_margin": round(mm, 2), "sd_margin": round(sd_m, 2),
            "sd_total": round(sd_t, 2), "exp_home_1h": round(h1[0] / n, 2), "exp_away_1h": round(h1[1] / n, 2),
            "spread_ladder": spread_ladder, "total_ladder": total_ladder,
            "box_home": mean_box[0], "box_away": mean_box[1], "engine": "play"}
