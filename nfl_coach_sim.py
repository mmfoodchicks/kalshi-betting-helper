"""The EXPERIMENTAL coaching / clock engine (R1 of the owner's "NFL coaching
intelligence and adaptive play-by-play engine"; research/reports/
nfl_coach_r1.md, the audit in nfl_coaching_audit.md). RESEARCH AND PC ONLY:
no board, viewer, DFS world or predlog bucket reads it, and it never writes
to the `nfl_deep` shadow bucket. nfl_deep_sim.py, nfl_deep_data.py, nfl_pbp.py
and research/data/nfl_deep_tables.json are imported or left alone, never
edited (tests/guards_auto_nfl_coach.py pins their sha256).

What it is: nfl_deep_sim's snap loop with the clock made explicit. The pass
chain, the yardage, the penalties, the kicks and the matchup arithmetic are
nfl_deep_sim's own classes and samplers, read from THIS engine's tables
(research/data/nfl_coach_tables.json: production's fit run on nflverse
2016-2023, so the control arm is the same fit on the same seasons). What is
new, all learned at league level from nflverse play-by-play:

  timeouts    each side holds three a half (two in a regular-season overtime,
              three per two periods in the postseason). After an in-bounds
              play the defence, then the offence, may stop the clock at the
              rate teams did in that phase x score x inventory; a stopped
              clock can still cost a timeout (the delay-of-game save, the
              lost challenge) at its own measured rate.
  the clock   the seconds to the next snap are drawn from histograms WITHOUT
              the timeouts folded in: by fifteen clock phases (the first
              half's two-minute drill apart from the fourth quarter), five
              score states and no-huddle or not. nfl_deep_sim's three tempo
              classes mixed a 42-second runoff with a 6-second timeout and
              burned 27 seconds a play for every leader.
  the call    pass or run = nfl_deep_sim's (down, distance, zone) cell plus
              ridge-shrunk effects of phase x score (nine score bins),
              phase x score x down and phase x the offence's timeouts.
  kneels      victory formation is a RULE: leading in the fourth quarter,
              when the remaining kneels outlast the clock net of the
              opponent's timeouts and the two-minute warning, the offence
              kneels. Every other kneel (before half-time, tied, a lead not
              yet safe) is drawn at the rate teams knelt.
  spikes      with the clock running and no timeout called, at the measured
              rate; a spike costs a down and stops the clock.
  onside      kicked at the measured rate by deficit and clock, recovered at
              the measured rate (the owner, 2026-11-26: this engine only).
  overtime    both rule sets, with the overtime timeouts and warnings.

Every decision probability is a count table smoothed down a hierarchy
(Tables._rates: a cell is pulled toward its parent with prior mass `rate_n`;
a histogram draw falls through to its parent with probability K/(n+K)).
The masses were chosen on 2024 (research/nfl_coach_fit.py); nothing here was
set by hand except the rules named as rules.

`play_game(start=)` plays from any state (period, clock, ball, score,
timeouts), which the registered R1 read needs for its late-state
win-probability criterion; `control_from` does the same for nfl_deep_sim
without editing it.

Data credit: nflverse-data (CC BY 4.0).
"""
import bisect
import json
import math
import os
import random
import zlib

import nfl_deep_sim as D
import nfl_pbp as P

VERSION = "coach-r1.0"          # this engine's own version; nothing in production reads it
TABLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "research", "data", "nfl_coach_tables.json")
# The form tilt, refit for THIS engine by nfl_deep_sim's own procedure
# (research/nfl_coach_eval.py --free, research/data/nfl_coach/free_r1.json):
# with each game anchored to its closing line, the margin's spread about the
# line is 13.04 with NO tilt against the real 12.79 (2016-2023, 2,094 games),
# and 13.26 / 13.64 / 14.06 at 0.04 / 0.065 / 0.08 -- so the tilt is 0. The
# control arm on the same tables reads 13.01 at 0 and is run at 0 as well
# (production's 0.08 was fitted on ESPN-built tables and another anchor).
# The home edge is production's, not refit: every research game here is
# anchored to its line on a neutral field, where it is never applied.
_FORM_SD = 0.0
_HFA_YDS = 1.021
N_PHASE = 15
_RUNNING = ("run", "knl")
_KNEEL_WINDOW_S = 150           # kneels are looked for inside this (99.6% of real ones fall there)
_KNEEL_RUNOFF = 40              # the play clock a kneeling leader lets run
_T = {"tables": None, "key": None}
_sig, _logit, _stoch, _scale = D._sig, D._logit, D._stoch, D._scale


# ---- States shared by the fit and the engine ------------------------------------
def phase(per, clk):
    """Fifteen clock phases. 0 first quarter and the second above 5:00; 1-5 the
    second quarter 5:00-2:45, 2:45-2:00 (the warning is coming: a free
    stoppage), 2:00-1:00, 1:00-0:30, the last 0:30; 6 the third quarter; 7-13
    the fourth 15-10, 10-5, 5:00-2:45, 2:45-2:00, 2:00-1:00, 1:00-0:30, the
    last 0:30; 14 overtime."""
    if per >= 5:
        return 14
    if per == 1:
        return 0
    if per == 3:
        return 6
    if per == 2:
        return (0 if clk > 300 else 1 if clk > 165 else 2 if clk > 120 else 3 if clk > 60
                else 4 if clk > 30 else 5)
    return (7 if clk > 600 else 8 if clk > 300 else 9 if clk > 165 else 10 if clk > 120
            else 11 if clk > 60 else 12 if clk > 30 else 13)


# A gap that would cross the two-minute warning is cut short in the data, so
# the two pre-warning phases borrow their neighbour's histograms and the
# engine applies the warning itself.
GAP_PHASE = {2: 1, 10: 9}
_S9_TO_S5 = (0, 0, 1, 1, 2, 3, 3, 4, 4)


def s9(sd):
    """Offence minus defence in nine bins: -17 or worse, -16..-9, -8..-4,
    -3..-1, level, 1..3, 4..8, 9..16, 17+."""
    if sd < 0:
        return 0 if sd <= -17 else 1 if sd <= -9 else 2 if sd <= -4 else 3
    if sd == 0:
        return 4
    return 5 if sd <= 3 else 6 if sd <= 8 else 7 if sd <= 16 else 8


def s3(sd):
    return 0 if sd < 0 else 1 if sd == 0 else 2


def warn_period(per, playoff):
    """Does this period stop at 2:00? Both halves; a regular-season overtime;
    every second postseason overtime period (they run as halves)."""
    if per in (2, 4):
        return True
    if per >= 5:
        return (per % 2 == 0) if playoff else per == 5
    return False


def kneel_key(per, clk, down, sd, ytg, dto):
    """(safe, key) for a kneel decision on downs 1-3, or (False, None) outside
    the window. `safe`: the kneels left (downs up to third) outlast the clock
    even if the defence spends every timeout and the warning stops it once --
    forty seconds for a kneel the defence cannot stop, two for one it can,
    three for the last snap."""
    if clk > _KNEEL_WINDOW_S or down > 3 or not (per == 2 or (per == 4 and sd >= 0)):
        return False, None
    n = 4 - down
    tm = min(n, dto + (1 if clk > 120 else 0))
    slack = clk - ((_KNEEL_RUNOFF + 1) * (n - tm) + 2 * tm + 3)
    sb = 0 if slack <= 0 else 1 if slack <= 20 else 2 if slack <= 45 else 3 if slack <= 90 else 4
    if per == 4:
        lead = 0 if sd == 0 else 1 if sd <= 8 else 2 if sd <= 16 else 3
        return (slack <= 0 and sd > 0), (1, sb, lead, 0, min(2, dto))
    zc = 0 if ytg > 70 else 1 if ytg > 50 else 2
    return False, (0, sb, s3(sd), zc, min(2, dto))


def onside_key(clk, deficit):
    cb = 0 if clk <= 60 else 1 if clk <= 120 else 2 if clk <= 180 else 3 if clk <= 300 else 4 if clk <= 600 else 5
    return (cb, 0 if deficit <= 8 else 1 if deficit <= 16 else 2)


# ---- Tables ---------------------------------------------------------------------
class _Mix:
    """A histogram that falls through to its parent with probability K/(n+K)."""
    __slots__ = ("h", "w", "parent")

    def __init__(self, hist, k, parent):
        self.h = D._H(hist)
        self.parent = parent
        self.w = (self.h.total / (self.h.total + k)) if parent is not None else 1.0

    def draw(self, rng):
        m = self
        while m.parent is not None and rng.random() >= m.w:
            m = m.parent
        return m.h.draw(rng)

    def pmf(self):
        """{value: probability} of a draw (the fit scores held-out gaps with it)."""
        out = {}
        m, mass = self, 1.0
        while m is not None:
            w = m.w if m.parent is not None else 1.0
            prev = 0
            for v, c in zip(m.h.vals, m.h.cum):
                out[v] = out.get(v, 0.0) + mass * w * (c - prev) / (m.h.total or 1)
                prev = c
            mass *= 1.0 - w
            m = m.parent
        return out


class Tables:
    """research/data/nfl_coach_tables.json: `base` is nfl_deep_sim's own table
    format (read by nfl_deep_sim.Tables, so both arms sample the same pass
    chain, yards and kicks); `coach` is this engine's count tables, smoothed
    here. `era` picks the kickoff rules both arms play under (the newest by
    default, as production does); `hyper` overrides the stored masses (the
    fit's selection uses it)."""

    def __init__(self, raw, era=None, hyper=None):
        self.raw = raw
        base = dict(raw["base"])
        eras = sorted(base["kickoff"])
        era = era or eras[-1]
        base["kickoff"] = {era: raw["base"]["kickoff"][era]}
        base["kickoff_td"] = {era: raw["base"]["kickoff_td"][era]}
        self.era = era
        self.base = D.Tables(base)
        c = raw["coach"]
        hy = dict(c.get("hyper") or {})
        hy.update(hyper or {})
        self.hyper = hy
        rn, hk = float(hy.get("rate_n", 20.0)), float(hy.get("hist_k", 40.0))
        self.to_def = self._rates(c["to_def"], rn)        # (phase, s5, defence's timeouts 1-3)
        self.to_off = self._rates(c["to_off"], rn)        # (phase, s5, offence's timeouts 1-3)
        self.to_stop = self._rates(c["to_stop"], rn)      # (phase, role 0 offence / 1 defence)
        self.spike = self._rates(c["spike"], rn)          # (phase, s3, offence's timeouts 0-2+)
        self.kneel = self._rates(c["kneel"], rn)          # kneel_key
        self.nh = self._rates(c["nh"], rn)                # (phase, s5)
        self.oob = self._rates(c["oob"], rn)              # (0 completion / 1 rush, phase, s5)
        self.onside = self._rates(c["onside"], rn)        # onside_key
        k, n = c["onside_rec"]
        self.onside_rec = k / n if n else 0.0
        self.onside_ytg = (int(round(c["onside_ytg"]["kept"])), int(round(c["onside_ytg"]["lost"])))
        # ---- seconds to the next snap: (kind, phase, s5, no-huddle) -> _Mix
        lv = [{}, {}, {}, {}]
        for key, hist in c["gap"].items():
            kind, ph, sc, nh = key.split(",")
            full = (kind, int(ph), int(sc), int(nh))
            for depth in range(4):
                tgt = lv[depth].setdefault(full[:depth + 1], {})
                for v, cnt in hist.items():
                    tgt[v] = tgt.get(v, 0) + cnt
        self.gap = {}
        for depth in range(4):
            for key, hist in lv[depth].items():
                self.gap[key] = _Mix(hist, hk, self.gap.get(key[:-1]) if depth else None)
        # ---- play call: the logit shift on the base (down, distance, zone) cell
        call = c["call"]
        A = {tuple(int(x) for x in k.split(",")): v for k, v in call["A"].items()}
        B = {tuple(int(x) for x in k.split(",")): v for k, v in call["B"].items()}
        C = {tuple(int(x) for x in k.split(",")): v for k, v in call["C"].items()}
        self.call_parts = (A, B, C)
        self.call_delta = {}
        for ph in range(N_PHASE):
            for sc in range(9):
                for down in (1, 2, 3, 4):
                    for tb in (0, 1, 2):
                        self.call_delta[(ph, sc, down, tb)] = (
                            A.get((ph, sc), 0.0) + B.get((ph, _S9_TO_S5[sc], down), 0.0) + C.get((ph, tb), 0.0))
        self.call_base = {k: _logit(v) for k, v in self.base.call_marg.items()}
        self.call_lg = _logit(self.base.league["pass_rate"])

    @staticmethod
    def _rates(cells, prior_n):
        """{"a,b,c": [hits, n]} -> {(a, b, c): p, (a, b): p, (a,): p, (): p},
        every level smoothed toward the one above it."""
        counts = {}
        for key, (k, n) in cells.items():
            full = tuple(int(x) for x in key.split(","))
            for depth in range(len(full) + 1):
                c = counts.setdefault(full[:depth], [0, 0])
                c[0] += k
                c[1] += n
        out = {}
        for key in sorted(counts, key=len):
            k, n = counts[key]
            if not key:
                out[key] = k / n if n else 0.0
            else:
                out[key] = (k + prior_n * out[key[:-1]]) / (n + prior_n)
        return out

    @staticmethod
    def rate(tab, key):
        while key not in tab:
            key = key[:-1]
        return tab[key]

    def p_pass(self, ph, sc9, down, db, zn, toff):
        return (self.call_base.get((down, db, zn), self.call_lg)
                + self.call_delta[(ph, sc9, down, 2 if toff > 2 else toff)])

    def gap_mix(self, kind, ph, sc5, nh=0):
        ph = GAP_PHASE.get(ph, ph)
        g = self.gap
        return g.get((kind, ph, sc5, nh)) or g.get((kind, ph, sc5)) or g.get((kind, ph)) or g.get((kind,))


def tables(path=None, era=None):
    path = path or TABLES_PATH
    if _T["tables"] is None or _T["key"] != (path, era):
        with open(path) as fh:
            _T["tables"] = Tables(json.load(fh), era=era)
        _T["key"] = (path, era)
    return _T["tables"]


def league_profile(T=None):
    return D.league_profile((T or tables()).base)


def _box():
    b = D._box()
    # counters this engine adds (nothing draws on them): spikes, timeouts used
    # by half (first, second, overtime), no-huddle snaps, onside kicks tried
    b.update({"spikes": 0, "tmo": [0, 0, 0], "nh": 0, "onside": 0})
    return b


# ---- The game -------------------------------------------------------------------
def play_game(home, away, rng, T=None, neutral=False, playoff=False, form_sd=None, env_sd=None,
              log=None, start=None, hfa=None):
    """One game -> {"h": box, "a": box, "ot": bool, "tie": bool}.

    `start` (default: the opening kickoff) is a state to play from:
      per, clk          the period and its seconds left
      pts               (home, away)
      to                (home, away) timeouts left this half
      off, down, dist, ytg   the ball -- or `kick`: the side (0 home) kicking off
      half2_recv        who receives the second-half kickoff (needed before it)
      pending           "run": the last play ended in bounds and the seconds to
                        this snap are still to be played (timeouts, a spike)
    The form tilt, the environment and the coin are drawn first whatever the
    start, so the opening state plays the very game `start=None` plays.

    `log` is nfl_deep_sim's passive log with three more event kinds ("tmo" a
    timeout, "spike", and "kneel" carries `vf` for victory formation); it draws
    nothing and takes no branch."""
    T = T or tables()
    B = T.base
    if form_sd is None:
        form_sd = _FORM_SD
    hfa = 1.0 if neutral else (_HFA_YDS if hfa is None else hfa)
    g = rng.gauss(0.0, form_sd) if form_sd else 0.0
    th, ta = math.exp(g - form_sd * form_sd / 2.0), math.exp(-g - form_sd * form_sd / 2.0)
    sides = (D._Side(home, away, B, hfa * th), D._Side(away, home, B, ta / hfa))
    if env_sd:
        z = rng.gauss(0.0, 1.0)
        pe = math.exp(env_sd * z - env_sd * env_sd / 2.0)
        for sd_ in sides:
            sd_.m_pass *= pe
            sd_.d_comp += env_sd * z
    box = (_box(), _box())
    S = {"per": 1, "clk": 900, "off": None, "down": 1, "dist": 10, "ytg": 75,
         "ot_poss": [False, False], "sudden": False, "over": False, "kick_pending": None,
         "drive_live": False, "playoff": playoff, "log": log, "to": [3, 3], "spike_next": False,
         "nh": 0, "elapsed": 0}
    first_recv = rng.randrange(2)
    S["half2_recv"] = 1 - first_recv
    if start is None or (start.get("off") is None and start.get("kick") is None):
        if start:
            _apply_start(S, box, start)
        _kickoff(S, box, sides, T, rng, kicking=1 - first_recv)
    else:
        _apply_start(S, box, start)
        if start.get("kick") is not None:
            S["kick_pending"] = int(start["kick"])
        else:
            off = int(start["off"])
            S["off"], S["down"] = off, int(start.get("down") or 1)
            S["ytg"] = max(1, min(99, int(start.get("ytg") or 75)))
            S["dist"] = max(1, min(S["ytg"], int(start.get("dist") or 10)))
            S["drive_live"], S["rz"] = True, S["ytg"] <= 20
            box[off]["drives"] += 1
            if S["per"] >= 5:
                S["ot_poss"][off] = True
            if start.get("pending") == "run":
                _gap(S, box, sides, T, rng, "run", sides[off].tempo)
    while not S["over"]:
        k = S["kick_pending"]
        if k is not None:
            S["kick_pending"] = None
            _kickoff(S, box, sides, T, rng, kicking=k)
            continue
        _snap(S, box, sides, T, rng)
    h, a = box
    if log is not None:
        D._ev(S, box, "fin", ot=int(S["per"] > 4))
    return {"h": h, "a": a, "ot": S["per"] > 4, "tie": h["pts"] == a["pts"],
            "elapsed": S["elapsed"], "per": S["per"], "clk": S["clk"]}


def _apply_start(S, box, st):
    S["per"], S["clk"] = int(st.get("per", 1)), int(st.get("clk", 900))
    pts = st.get("pts") or (0, 0)
    box[0]["pts"], box[1]["pts"] = int(pts[0]), int(pts[1])
    to = st.get("to")
    if to is None:
        to = (2, 2) if (S["per"] >= 5 and not S["playoff"]) else (3, 3)
    S["to"] = [max(0, min(3, int(to[0]))), max(0, min(3, int(to[1])))]
    if st.get("half2_recv") is not None:
        S["half2_recv"] = int(st["half2_recv"])
    if S["per"] >= 5:
        S["sudden"] = bool(st.get("sudden", False))
        S["ot_poss"] = list(st.get("ot_poss") or [False, False])


def control_from(home, away, rng, T, start=None, neutral=False, playoff=False, form_sd=None, log=None):
    """nfl_deep_sim (the CONTROL arm) played from the same kind of state, by
    its own functions: play_game's body with the state filled in. It has no
    timeouts, so `to` and `pending` mean nothing to it. With start=None this
    is nfl_deep_sim.play_game draw for draw (a guard compares the boxes)."""
    B = T.base if isinstance(T, Tables) else T
    if form_sd is None:
        form_sd = D._FORM_SD
    hfa = 1.0 if neutral else D._HFA_YDS
    g = rng.gauss(0.0, form_sd) if form_sd else 0.0
    th, ta = math.exp(g - form_sd * form_sd / 2.0), math.exp(-g - form_sd * form_sd / 2.0)
    sides = (D._Side(home, away, B, hfa * th), D._Side(away, home, B, ta / hfa))
    box = (D._box(), D._box())
    S = {"per": 1, "clk": 900, "off": None, "down": 1, "dist": 10, "ytg": 75,
         "ot_poss": [False, False], "sudden": False, "over": False, "kick_pending": None,
         "drive_live": False, "playoff": playoff, "log": log}
    first_recv = rng.randrange(2)
    S["half2_recv"] = 1 - first_recv
    st = start or {}
    if st:
        S["per"], S["clk"] = int(st.get("per", 1)), int(st.get("clk", 900))
        pts = st.get("pts") or (0, 0)
        box[0]["pts"], box[1]["pts"] = int(pts[0]), int(pts[1])
        if st.get("half2_recv") is not None:
            S["half2_recv"] = int(st["half2_recv"])
        if S["per"] >= 5:
            S["sudden"] = bool(st.get("sudden", False))
            S["ot_poss"] = list(st.get("ot_poss") or [False, False])
    if st.get("kick") is not None:
        S["kick_pending"] = int(st["kick"])
    elif st.get("off") is not None:
        off = int(st["off"])
        S["off"], S["down"] = off, int(st.get("down") or 1)
        S["ytg"] = max(1, min(99, int(st.get("ytg") or 75)))
        S["dist"] = max(1, min(S["ytg"], int(st.get("dist") or 10)))
        S["drive_live"], S["rz"] = True, S["ytg"] <= 20
        box[off]["drives"] += 1
        if S["per"] >= 5:
            S["ot_poss"][off] = True
    else:
        D._kickoff(S, box, sides, B, rng, kicking=1 - first_recv)
    while not S["over"]:
        k = S["kick_pending"]
        if k is not None:
            S["kick_pending"] = None
            D._kickoff(S, box, sides, B, rng, kicking=k)
            continue
        D._snap(S, box, sides, B, rng, playoff)
    h, a = box
    if log is not None:
        D._ev(S, box, "fin", ot=int(S["per"] > 4))
    return {"h": h, "a": a, "ot": S["per"] > 4, "tie": h["pts"] == a["pts"]}


# ---- Mechanics (nfl_deep_sim's, with the inventory and the new clock) -----------
_ev, _score, _sd, _ot_decided = D._ev, D._score, D._sd, D._ot_decided


def _half_idx(per):
    return 0 if per <= 2 else 1 if per <= 4 else 2


def _new_drive(S, box, off, ytg):
    S["spike_next"] = False
    D._new_drive(S, box, off, ytg)


def _timeout(S, box, side):
    S["to"][side] -= 1
    box[side]["tmo"][_half_idx(S["per"])] += 1
    if S["log"] is not None:
        _ev(S, box, "tmo", o=side, left=S["to"][side])


def _tick(S, box, dt):
    """Take `dt` seconds off the clock, stopping at the two-minute warning
    (logged once, at exactly 2:00). Every second the game spends goes through
    here, kickoffs included -- nfl_deep_sim's kickoff could run through 2:00
    without the stop. Returns the seconds actually spent."""
    clk = S["clk"]
    if clk > 120 and clk - dt <= 120 and warn_period(S["per"], S["playoff"]):
        dt = clk - 120
        S["clk"] = 120
        if S["log"] is not None:
            _ev(S, box, "2mw")
    else:
        dt = min(dt, clk)
        S["clk"] = clk - dt
    S["elapsed"] += dt
    return dt


def _gap(S, box, sides, T, rng, cls, tempo_mult=1.0, defer_end=False, nxt=0):
    """The seconds to the next snap after a play of clock class `cls` by
    S["off"]; `nxt` 1 a punt is coming, 2 a field goal. Timeouts, the spike
    decision and the no-huddle draw happen here, where the clock is. Returns
    True when the period ran out (ended here unless `defer_end`)."""
    per, clk, off = S["per"], S["clk"], S["off"]
    if cls in ("punt", "fg"):
        dt = T.base.clock_draw(cls, 0, rng)
    else:
        ph = phase(per, clk)
        sc = P.score_state(_sd(S, box))
        to = S["to"]
        kind, nh = cls, 0
        if cls in _RUNNING:
            de = 1 - off
            if to[de] and rng.random() < T.rate(T.to_def, (ph, sc, to[de])):
                _timeout(S, box, de)
                kind = "rtoD"
            elif cls == "run" and to[off] and rng.random() < T.rate(T.to_off, (ph, sc, to[off])):
                # (never after a kneel: the offence that knelt wants the clock gone)
                _timeout(S, box, off)
                kind = "rtoO"
            elif nxt:
                kind = "prepunt" if nxt == 1 else "prefg"
            elif (cls == "run" and ph in (3, 4, 5, 11, 12, 13) and S["down"] <= 3
                  and rng.random() < T.rate(T.spike, (ph, s3(_sd(S, box)), min(2, to[off])))):
                S["spike_next"] = True
                kind = "spk"
            elif cls == "run":
                nh = 1 if rng.random() < T.rate(T.nh, (ph, sc)) else 0
                S["nh"] = nh
        elif cls in ("inc", "oob", "pen") and (to[0] or to[1]):
            # a stopped clock: a timeout here moves the inventory, not the time
            u = rng.random()
            po = T.rate(T.to_stop, (ph, 0)) if to[off] else 0.0
            if u < po:
                _timeout(S, box, off)
            elif to[1 - off] and u < po + T.rate(T.to_stop, (ph, 1)):
                _timeout(S, box, 1 - off)
        if kind == "knl":
            dt = _KNEEL_RUNOFF + rng.randint(1, 3)
        else:
            dt = T.gap_mix(kind, ph, sc, nh).draw(rng)
            # the team's own pace, where pace is a choice: an unhurried huddle
            if tempo_mult != 1.0 and kind in ("run", "oob") and not nh and ph in (0, 1, 6, 7, 8):
                dt = int(round(dt * tempo_mult))
    box[off]["top"] += _tick(S, box, dt)
    if S["clk"] <= 0:
        if not defer_end:
            _end_period(S, box, sides, T, rng)
        return True
    return False


def _kickoff(S, box, sides, T, rng, kicking):
    recv = 1 - kicking
    B = T.base
    sd_k = box[kicking]["pts"] - box[recv]["pts"]
    if S["per"] == 4 and sd_k < 0 and rng.random() < T.rate(T.onside, onside_key(S["clk"], -sd_k)):
        box[kicking]["onside"] += 1
        if rng.random() < T.onside_rec:
            _new_drive(S, box, kicking, T.onside_ytg[0])
        else:
            _new_drive(S, box, recv, T.onside_ytg[1])
        if S["log"] is not None:
            _ev(S, box, "ko", o=kicking, res="onside_kept" if S["off"] == kicking else "onside_lost", y2=S["ytg"])
        _tick(S, box, 4)
        return
    if rng.random() < B.ko_td:
        box[recv]["td_ret"] += 1
        if S["log"] is not None:
            _ev(S, box, "ko", o=kicking, res="td")
        _return_td(S, box, sides, T, rng, recv)
        return
    ytg = B.kickoff.draw(rng) - _stoch(sides[recv].ko_shift, rng)
    _new_drive(S, box, recv, max(1, min(D._KO_MAX_YTG, ytg)))
    if S["log"] is not None:
        _ev(S, box, "ko", o=kicking, res="spot", y2=S["ytg"])
    _tick(S, box, B.clock_draw("kickoff", 0, rng))


def _return_td(S, box, sides, T, rng, scorer):
    _score(S, box, scorer, 6)
    box[scorer]["ret_pts"] += 6
    if S["log"] is not None:
        _ev(S, box, "rtd", o=scorer)
    S["drive_live"] = False
    if S["per"] >= 5 and S["sudden"]:
        S["over"] = True
        return
    box[scorer]["ret_pts"] += D._try_points(S, box, sides, T.base, rng, scorer)
    _after_score(S, box, sides, T, rng, scorer=scorer)


def _after_score(S, box, sides, T, rng, scorer):
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


def _change(S, box, sides, T, rng, new_ytg):
    S["drive_live"] = False
    if S["over"] or S["kick_pending"] is not None:
        return
    if S["per"] >= 5 and _ot_decided(S, box):
        S["over"] = True
        return
    _new_drive(S, box, 1 - S["off"], new_ytg)


def _end_period(S, box, sides, T, rng):
    per = S["per"]
    S["spike_next"] = False
    if S["log"] is not None:
        _ev(S, box, "end")
    if per == 1 or per == 3:
        S["per"] += 1
        S["clk"] = 900
        return
    if per == 2:
        if S["drive_live"] and S["off"] is not None:
            box[S["off"]]["ends"]["clock"] += 1
        S["per"], S["clk"] = 3, 900
        S["drive_live"] = False
        S["to"] = [3, 3]
        S["kick_pending"] = 1 - S["half2_recv"]
        return
    h, a = box[0]["pts"], box[1]["pts"]
    if S["drive_live"] and S["off"] is not None:
        box[S["off"]]["ends"]["clock"] += 1
    if per == 4:
        if h != a:
            S["over"] = True
            return
        # overtime: a fresh toss; two timeouts a side in the regular season,
        # three for the postseason's first two periods
        S["per"], S["clk"] = 5, (900 if S["playoff"] else 600)
        S["ot_poss"] = [False, False]
        S["sudden"] = False
        S["drive_live"] = False
        S["to"] = [3, 3] if S["playoff"] else [2, 2]
        S["kick_pending"] = 1 - rng.randrange(2)
        if S["log"] is not None:
            _ev(S, box, "ot", kick=S["kick_pending"])
        return
    if h != a or not S["playoff"]:
        S["over"] = True                      # regular season: a tie stands
        return
    S["per"] += 1
    S["clk"] = 900
    if per % 2 == 0:
        # the postseason's overtime runs in halves: after every second period
        # a kickoff and three fresh timeouts; between the two, only the ends change
        S["to"] = [3, 3]
        S["drive_live"] = False
        S["kick_pending"] = 1 - rng.randrange(2)
        if S["log"] is not None:
            _ev(S, box, "ot", kick=S["kick_pending"])


def _fourth_decision(S, box, sides, T, rng):
    """0 go, 1 punt, 2 field goal, 3 run the clock out. nfl_deep_sim's rules
    and table (this engine's own fit of it), plus the end of a half: with time
    for one snap nobody punts."""
    per, clk, ytg, dist = S["per"], S["clk"], S["ytg"], S["dist"]
    sd = _sd(S, box)
    ts, ss = P.time_state(per, clk), P.score_state(sd)
    makeable = ytg + 17 <= 66
    if per >= 5:
        if sd == 0 and makeable and S["sudden"]:
            return 2
        if sd < 0:
            return 2 if (makeable and sd >= -3) else 0
    if per == 4 and sd > 0 and clk <= 3:
        return 3
    if per == 2 and clk <= 6:
        return 2 if makeable else 0 if ytg <= 60 else 3
    late = per == 4 and clk <= 180
    if late and sd < 0:
        if sd >= -3 and makeable and (clk <= 150 or ytg > 4):
            return 2
        if sd < -3 or not makeable:
            return 0
    if per == 4 and clk <= 40 and sd < 0:
        return 0 if (sd < -3 or not makeable) else 2
    p_go, p_punt, p_fg = T.base.fourth_choice(min(19, max(0, (ytg - 1) // 5)), P.dist_bucket(max(1, dist)), ss, ts)
    if not makeable:
        p_go, p_punt, p_fg = (p_go + p_fg * (0.4 if ytg <= 55 else 0.0),
                              p_punt + p_fg * (0.6 if ytg <= 55 else 1.0), 0.0)
    r = rng.random() * (p_go + p_punt + p_fg)
    return 0 if r < p_go else 1 if r < p_go + p_punt else 2


def _punt(S, box, sides, T, rng):
    off = S["off"]
    B = T.base
    box[off]["punts"] += 1
    box[off]["ends"]["punt"] += 1
    ytg = S["ytg"]
    lg = S["log"] is not None
    if rng.random() < B.punt_block:
        box[1 - off]["blk"] += 1
        if lg:
            _ev(S, box, "punt", o=off, d=S["down"], t=S["dist"], y=ytg, res="blocked", y2=max(1, 100 - ytg - 5))
        _gap(S, box, sides, T, rng, "punt")
        _change(S, box, sides, T, rng, max(1, 100 - ytg - 5))
        return
    if rng.random() < B.punt_td:
        box[1 - off]["td_ret"] += 1
        if lg:
            _ev(S, box, "punt", o=off, d=S["down"], t=S["dist"], y=ytg, res="td")
        _gap(S, box, sides, T, rng, "punt", defer_end=True)
        _return_td(S, box, sides, T, rng, 1 - off)
        return
    h = B.punt.get(min(95, max(30, ytg // 5 * 5))) or B.punt[65]
    recv_ytg = h.draw(rng) + _stoch(sides[off].punt_shift, rng)
    if lg:
        _ev(S, box, "punt", o=off, d=S["down"], t=S["dist"], y=ytg, res="spot", y2=max(1, min(99, recv_ytg)))
    _gap(S, box, sides, T, rng, "punt")
    _change(S, box, sides, T, rng, max(1, min(99, recv_ytg)))


def _field_goal(S, box, sides, T, rng):
    off = S["off"]
    B = T.base
    ytg = S["ytg"]
    kd = ytg + 17
    box[off]["fg_att"] += 1
    lg = S["log"] is not None
    x = S.pop("_fgx", 0) if lg else 0
    if rng.random() < B.fg_block:
        box[1 - off]["blk"] += 1
        box[off]["ends"]["fgm"] += 1
        if lg:
            _ev(S, box, "fg", o=off, d=S["down"], t=S["dist"], y=ytg, kd=kd, res="blocked", x=x,
                y2=max(1, 100 - ytg - 7))
        _gap(S, box, sides, T, rng, "fg")
        _change(S, box, sides, T, rng, max(1, 100 - ytg - 7))
        return
    if rng.random() < _sig(B.fg_a + B.fg_b * kd + sides[off].fg_shift):
        box[off]["fg_made"] += 1
        box[off]["fg_0_39" if kd <= 39 else "fg_40_49" if kd <= 49 else "fg_50p"] += 1
        box[off]["ends"]["fg"] += 1
        _score(S, box, off, 3)
        if lg:
            _ev(S, box, "fg", o=off, d=S["down"], t=S["dist"], y=ytg, kd=kd, res="good", x=x)
        S["drive_live"] = False
        if S["per"] >= 5 and _ot_decided(S, box):
            S["over"] = True
            return
        _gap(S, box, sides, T, rng, "fg")
        _after_score(S, box, sides, T, rng, scorer=off)
        return
    box[off]["ends"]["fgm"] += 1
    if lg:
        _ev(S, box, "fg", o=off, d=S["down"], t=S["dist"], y=ytg, kd=kd, res="miss", x=x,
            y2=max(1, 100 - max(20, ytg + 7)))
    _gap(S, box, sides, T, rng, "fg")
    _change(S, box, sides, T, rng, max(1, 100 - max(20, ytg + 7)))


def _kneel(S, box, sides, T, rng, vf):
    off = S["off"]
    bx = box[off]
    bx["kneels"] += 1
    bx["plays"] += 1
    if S["log"] is not None:
        _ev(S, box, "kneel", o=off, d=S["down"], t=S["dist"], y=S["ytg"], yd=-1, vf=int(vf))
    S["down"] += 1
    S["dist"] += 1
    S["ytg"] = min(99, S["ytg"] + 1)
    if S["down"] > 4:
        # a fourth-down kneel only ever runs the clock out
        bx["top"] += _tick(S, box, S["clk"])
        _end_period(S, box, sides, T, rng)
        return
    _gap(S, box, sides, T, rng, "knl")


def _snap(S, box, sides, T, rng):
    off = S["off"]
    me = sides[off]
    bx = box[off]
    B = T.base
    per, clk, down, dist, ytg = S["per"], S["clk"], S["down"], S["dist"], S["ytg"]
    sd = _sd(S, box)
    lg = S["log"] is not None
    # ---- the spike decided when the clock was running
    if S["spike_next"]:
        S["spike_next"] = False
        bx["spikes"] += 1
        bx["pass_att"] += 1
        bx["plays"] += 1
        if lg:
            _ev(S, box, "spike", o=off, d=down, t=dist, y=ytg, yd=0)
        S["down"] += 1
        _gap(S, box, sides, T, rng, "inc")
        return
    # ---- kneels: victory formation by rule, every other kneel at its rate
    if clk <= _KNEEL_WINDOW_S and down <= 3 and (per == 2 or (per == 4 and sd >= 0)):
        safe, key = kneel_key(per, clk, down, sd, ytg, S["to"][1 - off])
        if safe:
            _kneel(S, box, sides, T, rng, True)
            return
    else:
        key = None
    # ---- the clock is about to expire with a kick on the table (nfl_deep_sim's
    # rule); it comes before the learned kneel, whose thin cells pool the 25
    # with midfield
    if clk <= 20 and (down >= 2 or clk <= 8) and ytg + 17 <= 62 and (
            per == 2 or (per == 4 and -3 <= sd <= 0) or (per >= 5 and sd == 0)):
        if lg:
            S["_fgx"] = 1
        _field_goal(S, box, sides, T, rng)
        return
    if key is not None:
        p = T.rate(T.kneel, key)
        if p > 0.0 and rng.random() < p:
            _kneel(S, box, sides, T, rng, False)
            return
    if down == 4:
        choice = S.pop("fourth_pre", None)
        # the choice was drawn when the down arose; a punt chosen then is taken
        # again if the clock has since run down to one snap
        if choice is None or (choice == 1 and ((per == 2 and clk <= 6) or (per == 4 and sd > 0 and clk <= 3))):
            choice = _fourth_decision(S, box, sides, T, rng)
        if choice == 1:
            _punt(S, box, sides, T, rng)
            return
        if choice == 2:
            _field_goal(S, box, sides, T, rng)
            return
        if choice == 3:
            _kneel(S, box, sides, T, rng, per == 4)
            return
        bx["fourth_go"] += 1
    # ---- a no-play penalty
    p_pen = _sig(_logit(B.pen_rate.get(down, 0.035)) + me.d_pen)
    if rng.random() < p_pen:
        bx["pen"] += 1
        pairs, cum, tot = B.pen_out.get(down) or B.pen_out[1]
        yds, first = pairs[bisect.bisect_right(cum, rng.random() * tot)]
        ytg2 = max(1, min(99, ytg - yds))
        if first:
            S["down"], S["dist"], S["ytg"] = 1, min(10, ytg2), ytg2
        else:
            S["ytg"] = ytg2
            S["dist"] = max(1, dist - yds) if yds > 0 else dist - yds
        if lg:
            _ev(S, box, "pen", o=off, d=down, t=dist, y=ytg, yd=yds, fd=int(bool(first)), y2=S["ytg"])
        _gap(S, box, sides, T, rng, "pen", me.tempo)
        return
    # ---- the play
    ph = phase(per, clk)
    db, zn = P.dist_bucket(max(1, dist)), P.zone(max(1, ytg))
    p_pass = _sig(T.p_pass(ph, s9(sd), down, db, zn, S["to"][off]) + me.d_call)
    bx["plays"] += 1
    bx["nh"] += S["nh"]
    S["nh"] = 0
    if down == 3:
        bx["third_att"] += 1
    bx["conv_att"][down] += 1
    if ytg <= 20 and not S.get("rz"):
        S["rz"] = True
        bx["rz_trips"] += 1
    cls = "run"
    yds = 0
    turnover_ytg = None
    to_kind = None
    td = False
    kind = "run"
    ret = 0
    oob_t = None                     # 0 a completion, 1 a rush: the out-of-bounds draw
    if rng.random() < p_pass:
        if rng.random() < _sig(_logit(B.sack.get((down, db), B.league["sack"])) + me.d_sack):
            kind = "sack"
            bx["sacks"] += 1
            yds = B.sack_yds.draw(rng)
            if yds >= ytg:
                yds = ytg - 1
            bx["sack_yds"] += -yds
            if rng.random() < B.fum["sack"] * me.fum_mult:
                bx["fum_lost"] += 1
                turnover_ytg = max(1, min(99, 100 - (ytg - yds) - B.fum_spot.draw(rng)))
                to_kind = "fum"
        else:
            bx["pass_att"] += 1
            if rng.random() < _sig(_logit(B.int.get((down, db), B.league["int"])) + me.d_int):
                kind = "int"
                bx["int"] += 1
                adv = B.int_ret.draw(rng)
                ret = adv
                if adv >= 99 or 100 - ytg - adv <= 0:
                    box[1 - off]["td_ret"] += 1
                    box[1 - off]["td_def"] += 1
                    if lg:
                        _ev(S, box, "int", o=off, d=down, t=dist, y=ytg, yd=0, ret=ret, td=0, six=1, to="int")
                    _gap(S, box, sides, T, rng, "to", me.tempo, defer_end=True)
                    _return_td(S, box, sides, T, rng, 1 - off)
                    return
                turnover_ytg = max(1, min(99, 100 - ytg - adv))
                to_kind = "int"
            elif rng.random() < _sig(_logit(B.comp.get((down, db), B.league["comp"])) + me.d_comp):
                kind = "cmp"
                bx["comp"] += 1
                h = B.comp_yds.get((down, db, zn)) or B.comp_yds_dz.get((db, zn)) or B.comp_yds_dz[(2, 3)]
                yds = _scale(h.draw(rng), me.m_pass, rng)
                if yds >= ytg:
                    yds = ytg
                    td = True
                    bx["td_pass"] += 1
                bx["pass_yds"] += yds
                if not td and rng.random() < B.fum["comp"] * me.fum_mult:
                    bx["fum_lost"] += 1
                    turnover_ytg = max(1, min(99, 100 - (ytg - yds) - B.fum_spot.draw(rng)))
                    to_kind = "fum"
                else:
                    oob_t = 0
            else:
                kind = "inc"
                cls = "inc"
    else:
        bx["rush_att"] += 1
        h = B.rush_yds.get((down, db, zn)) or B.rush_yds_dz.get((db, zn)) or B.rush_yds_dz[(2, 3)]
        yds = _scale(h.draw(rng), me.m_rush, rng)
        if yds >= ytg:
            yds = ytg
            td = True
            bx["td_rush"] += 1
        bx["rush_yds"] += yds
        if not td and rng.random() < B.fum["rush"] * me.fum_mult:
            bx["fum_lost"] += 1
            turnover_ytg = max(1, min(99, 100 - (ytg - yds) - B.fum_spot.draw(rng)))
            to_kind = "fum"
        else:
            oob_t = 1
    if oob_t is not None and rng.random() < T.rate(T.oob, (oob_t, ph, P.score_state(sd))):
        cls = "oob"
    # ---- resolve
    if td:
        _score(S, box, off, 6)
        if lg:
            _ev(S, box, kind, o=off, d=down, t=dist, y=ytg, yd=yds, td=1, fd=1)
        bx["ends"]["td"] += 1
        if down == 3:
            bx["third_conv"] += 1
        bx["first"] += 1
        bx["conv"][down] += 1
        if S.get("rz"):
            bx["rz_td"] += 1
        S["drive_live"] = False
        if S["per"] >= 5 and S["sudden"]:
            S["over"] = True
            return
        ended = _gap(S, box, sides, T, rng, "score", me.tempo, defer_end=True)
        D._try_points(S, box, sides, B, rng, off)
        if ended:
            _end_period(S, box, sides, T, rng)
        _after_score(S, box, sides, T, rng, scorer=off)
        return
    ytg2 = ytg - yds
    if ytg2 >= 100 and rng.random() < D._SAFETY_SHARE:
        _score(S, box, 1 - off, 2)
        if lg:
            _ev(S, box, kind, o=off, d=down, t=dist, y=ytg, yd=yds, sf=1)
        bx["ends"]["safety"] += 1
        S["drive_live"] = False
        if S["per"] >= 5 and _ot_decided(S, box):
            S["over"] = True
            return
        _gap(S, box, sides, T, rng, "to", me.tempo)
        if S["over"] or S["kick_pending"] is not None:
            return
        _new_drive(S, box, 1 - off, max(1, min(99, B.punt[80].draw(rng) + 8)))
        if lg:
            _ev(S, box, "fk", o=off, y2=S["ytg"])
        return
    if turnover_ytg is not None:
        if lg:
            _ev(S, box, kind, o=off, d=down, t=dist, y=ytg, yd=yds, ret=ret, to=to_kind, ty=turnover_ytg)
        bx["ends"][to_kind or "fum"] += 1
        _gap(S, box, sides, T, rng, "to", me.tempo)
        _change(S, box, sides, T, rng, turnover_ytg)
        return
    if ytg2 >= 100:
        ytg2 = 99
    S["ytg"] = ytg2
    fd = 0
    if yds >= dist:
        fd = 1
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
        if lg:
            _ev(S, box, kind, o=off, d=down, t=dist, y=ytg, yd=yds, to="downs", ty=max(1, 100 - ytg2),
                ob=int(cls == "oob"))
        bx["ends"]["downs"] += 1
        _gap(S, box, sides, T, rng, "to", me.tempo)
        _change(S, box, sides, T, rng, max(1, 100 - ytg2))
        return
    if lg:
        _ev(S, box, kind, o=off, d=down, t=dist, y=ytg, yd=yds, fd=fd, y2=ytg2, ob=int(cls == "oob"))
    nxt = 0
    if S["down"] == 4:
        nxt = _fourth_decision(S, box, sides, T, rng)
        S["fourth_pre"] = nxt
        if nxt not in (1, 2):
            nxt = 0
    _gap(S, box, sides, T, rng, cls, me.tempo, nxt=nxt)


# ---- Monte Carlo ----------------------------------------------------------------
def simulate(home, away, n=2000, seed=None, neutral=False, playoff=False, T=None, form_sd=None,
             start=None, hfa=None):
    """n games (from `start` when given) -> win probabilities, score means and
    SDs. A research summary: no board reads it."""
    T = T or tables()
    rng = random.Random(seed if seed is not None
                        else zlib.crc32(f"{home.get('abbr')}|{away.get('abbr')}|coach".encode()))
    hw = aw = ties = ot = 0
    margins, totals = [], []
    for _ in range(n):
        g = play_game(home, away, rng, T, neutral=neutral, playoff=playoff, form_sd=form_sd,
                      start=start, hfa=hfa)
        m = g["h"]["pts"] - g["a"]["pts"]
        hw += m > 0
        aw += m < 0
        ties += m == 0
        ot += g["ot"]
        margins.append(m)
        totals.append(g["h"]["pts"] + g["a"]["pts"])
    mm, mt = sum(margins) / n, sum(totals) / n
    dec = (hw + aw) or 1
    return {"n_sims": n, "p_home": hw / dec, "p_home_raw": hw / n, "p_tie": ties / n, "p_ot": ot / n,
            "exp_total": mt, "mean_margin": mm,
            "sd_margin": math.sqrt(sum((x - mm) ** 2 for x in margins) / max(1, n - 1)),
            "sd_total": math.sqrt(sum((x - mt) ** 2 for x in totals) / max(1, n - 1)),
            "engine": VERSION}
