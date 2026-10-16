"""The NHL deep engine: one hockey game played event by event in continuous
time, both teams' units against each other, sampled from tables measured on
the league's play-by-play (research/nhl_deep_fit.py ->
research/data/nhl_deep_tables.json). The hockey twin of nfl_deep_sim.

The game is a race of exponential clocks. In every stretch of play each side
has a shot-attempt rate and a penalty rate, read off the tables for the
strength state it is in (five-on-five, a power play, an extra attacker...),
its goal difference and whether it is the last five minutes of the third --
score effects are the tables' own, not a knob -- then scaled by the two teams'
units. The next event is whichever clock rings first, unless the state
changes before it does (a penalty expiring, the period ending, a trailing
coach pulling his goalie), and a memoryless clock simply restarts after the
change.

  attempt    blocked with the state's measured probability; an unblocked one
             draws its distance bucket and rebound flag from the state's own
             distribution and goes in with the table's probability for that
             (state, distance, rebound) cell, times the shooters' finishing
             and the goalie's (the empty net has its own cells)
  penalty    an incident that leaves a side a man down: a minor (two
             minutes), a double minor (two stacked) or a major (five, served
             in full), in the measured mix; matching calls and fights leave
             nobody short and are not drawn. The measured share is preceded
             by a delayed call, during which the fouled side attacks with its
             net empty (a goal waives a minor). At most two of a side in the
             box, the rest queued; a power-play goal ends the earliest minor
  the pull   a side trailing in the third pulls its goalie at a time drawn
             from the measured pull times for its deficit, with the measured
             probability of pulling at all; a goal resets it
  overtime   regular season: five minutes of three-on-three, sudden death,
             at the measured three-on-three rates, then the shootout at the
             measured conversion; playoffs: twenty-minute five-on-five
             periods until someone scores

A team is a PROFILE of multipliers on the league (nhl_deep_data builds them
from the season's games, shrunk by the measured reliability):
  att_off / att_def   even-strength attempt generation / suppression
  xg_off / xg_def     shot quality for / against (on the goal probability)
  fin / gk            finishing above the shot's quality / goaltending
  pp / pk             the power play's and the penalty kill's goal rate
  pen_take / pen_draw penalties taken / drawn
  level_off           the market anchor's lever (as nfl_deep_sim's)
"""
import json
import math
import os
import random

TABLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", "data", "nhl_deep_tables.json")
_T = {"tables": None, "path": None}

PROFILE_KEYS = ("att_off", "att_def", "xg_off", "xg_def", "fin", "gk", "pp", "pk", "pen_take", "pen_draw")
REG_SECS = 1200
OT_SECS_REG = 300
# Smoothing weights (pseudo-counts) of a cell toward its parent.
_K_ATT_SECS = 3600.0          # an hour of a state's time
_K_GOAL = 150.0               # unblocked attempts
_K_UNBL = 300.0
_K_PEN_SECS = 7200.0
_K_CONV = 60.0                # goals


def _sk(st):
    return ",".join(str(x) for x in st)


class Tables:
    """The fitted counts, turned into the rates and probabilities the game
    samples, every cell smoothed toward its parent so a thin cell (a
    five-on-three while trailing by three) still answers."""

    def __init__(self, raw):
        self.raw = raw
        self.dist_edges = raw.get("dist_edges") or [10, 20, 30, 40, 55]
        # ---- attempt and penalty rates per second: (state, sd, late) -> (state, late) -> state
        self.att = self._rates(raw["att"], raw["time"], _K_ATT_SECS)
        self.pen = self._rates(raw["pen"], raw["time"], _K_PEN_SECS)
        # ---- unblocked share by state, toward the league's
        tot_u = sum(v[0] for v in raw["unbl"].values())
        tot_a = sum(v[1] for v in raw["unbl"].values())
        lg_u = tot_u / tot_a if tot_a else 0.75
        self.unbl = {k: (v[0] + _K_UNBL * lg_u) / (v[1] + _K_UNBL) for k, v in raw["unbl"].items()}
        self.lg_unbl = lg_u
        # ---- goal probability per unblocked attempt, smoothed up a hierarchy
        # that keeps the EMPTY NET apart: (state, db, reb) -> (net, db, reb)
        # -> (net), net = goalie in or not. One parent pooled over every
        # state dragged the empty net's 51% (2025-26) toward the 6% of a
        # guarded one and halved the empty-net goals.
        def net(st):
            return "en" if st.split(",")[3] == "0" else "gi"
        by_net = {}
        by_db = {}
        for k, (g, n) in raw["goal"].items():
            st, db, reb = k.split("|")
            for kk, d in (((net(st), db, reb), by_db), ((net(st),), by_net)):
                row = d.setdefault(kk, [0, 0])
                row[0] += g
                row[1] += n
        top = {kk[0]: (g / n if n else 0.07) for kk, (g, n) in by_net.items()}
        self.goal_db = {kk: (g + _K_GOAL * top.get(kk[0], 0.07)) / (n + _K_GOAL) for kk, (g, n) in by_db.items()}
        self.goal = {}
        for k, (g, n) in raw["goal"].items():
            st, db, reb = k.split("|")
            prior = self.goal_db.get((net(st), db, reb), top.get(net(st), 0.07))
            self.goal[k] = (g + _K_GOAL * prior) / (n + _K_GOAL)
        # ---- conversion by game state: real goals over this table's own
        # expected goals, by (goal difference, late), guarded nets pooled,
        # smoothed toward 1 by _K_CONV goals. Tied or trailing in the last
        # five minutes converts ~10% under the cell table, leading over it.
        conv = {}
        for k, (g, n) in (raw.get("goal_sd") or {}).items():
            st, db, reb, sd, late = k.split("|")
            if st.split(",")[3] == "0":
                continue
            p = self.goal.get(f"{st}|{db}|{reb}")
            if p is None:
                continue
            row = conv.setdefault((int(sd), int(late)), [0.0, 0.0])
            row[0] += g
            row[1] += n * p
        self.conv = {k: (g + _K_CONV) / (x + _K_CONV) for k, (g, x) in conv.items()}
        # ---- the (distance, rebound) mix of a state's unblocked attempts
        self.mix = {}
        for k, (g, n) in raw["goal"].items():
            st, db, reb = k.split("|")
            self.mix.setdefault(st, []).append(((int(db), int(reb)), n))
        self.mix_cum = {}
        for st, rows in self.mix.items():
            rows.sort()
            cum, t = [], 0
            for (cell, n) in rows:
                t += n
                cum.append((t, cell))
            self.mix_cum[st] = (cum, t)
        # ---- of the unblocked attempts that did not score, the share on goal
        sg = raw.get("sog") or {}
        tot_s = sum(v[0] for v in sg.values())
        tot_n = sum(v[1] for v in sg.values())
        self.lg_sog = tot_s / tot_n if tot_n else 0.6
        self.sog = {k: (v[0] + 200.0 * self.lg_sog) / (v[1] + 200.0) for k, v in sg.items()}
        # ---- penalties: an INCIDENT is a same-second group of calls that
        # leaves one side a man down by net minutes (research/nhl_deep_fit.py
        # groups them: matching minors and fights leave nobody short and are
        # not incidents); the kind mix and the share a delayed call preceded
        mix = raw.get("pen_mix") or {"minor": 1}
        tot = float(sum(mix.values())) or 1.0
        self.pen_mix = [(k, mix.get(k, 0) / tot) for k in ("minor", "double", "major")]
        pdl = raw.get("pen_delayed") or [0, 1]
        self.p_delayed = pdl[0] / pdl[1] if pdl[1] else 0.4
        # ---- the goalie pull: P(pull | trailing by d at that deficit's
        # checkpoint) and the seconds left when the pulls happened
        self.pull_p, self.pull_secs = {}, {}
        for d in ("1", "2", "3"):
            row = raw["pull_opp"].get(d) or [0, 0, 0]
            hit, neither = row[0], row[1]
            # the intention: a goal that came first is the engine's to play
            self.pull_p[int(d)] = hit / (hit + neither) if (hit + neither) else 0.0
            rows = raw["pull"].get(d) or []
            # [seconds left at the pull, seconds left when the deficit began]
            self.pull_secs[int(d)] = sorted((r[0], r[1]) if isinstance(r, list) else (r, 2400) for r in rows) or [(60, 2400)]
        # ---- the delayed penalty: the fouled side attacks with its net empty
        # until the whistle; a goal in it waives a minor
        D = raw.get("delayed") or {}
        secs = sorted(D.get("secs") or [8])
        self.delay_secs = secs
        tot = float(sum(secs)) or 1.0
        self.delay_att_rate = (D.get("att") or 0) / tot
        self.delay_unbl = (D.get("unbl") or 0) / max(1, D.get("att") or 0) if D.get("att") else 0.7
        self.delay_goal = (D.get("goals") or 0) / max(1, D.get("unbl") or 0) if D.get("unbl") else 0.15
        self.delay_sog = (D.get("sog") or 0) / max(1, (D.get("unbl") or 0) - (D.get("goals") or 0)) if D.get("unbl") else 0.6
        # ---- three-on-three overtime and the shootout
        ot_att = sum(raw["ot_att"])
        self.ot_att_rate = (ot_att / 2.0) / raw["ot_time"] if raw["ot_time"] else 0.012
        self.ot_unbl = raw["ot_unbl"] / ot_att if ot_att else 0.8
        self.ot_goal = raw["ot_goal"] / raw["ot_unbl"] if raw["ot_unbl"] else 0.11
        self.so_goal = raw["so"][0] / raw["so"][1] if raw["so"][1] else 0.32
        # ---- home ice on attempts and penalties (home share of each)
        ha, hp = raw["home"]["att"], raw["home"]["pen"]
        self.home_att = math.sqrt(ha[0] / ha[1]) if ha[1] else 1.0
        self.home_pen = math.sqrt(hp[0] / hp[1]) if hp[1] else 1.0
        self.lg_goals = raw["goals"] / raw["games"] / 2.0 if raw["games"] else 3.0

    @staticmethod
    def _rates(counts, time, k):
        """{(state, sd, late): per-second rate} smoothed toward (state, late),
        then the state, then the all-state rate."""
        agg = {}
        for key, secs in time.items():
            st, sd, late = key.split("|")
            for kk in ((st, sd, late), (st, "*", late), (st, "*", "*"), ("*", "*", "*")):
                row = agg.setdefault(kk, [0.0, 0.0])
                row[1] += secs
        for key, n in counts.items():
            st, sd, late = key.split("|")
            for kk in ((st, sd, late), (st, "*", late), (st, "*", "*"), ("*", "*", "*")):
                row = agg.setdefault(kk, [0.0, 0.0])
                row[0] += n
        out = {}

        def rate(kk, parent):
            n, s = agg.get(kk, (0.0, 0.0))
            return (n + k * parent) / (s + k)
        top = agg[("*", "*", "*")][0] / max(1.0, agg[("*", "*", "*")][1])
        for kk in agg:
            if kk[0] == "*":
                continue
            st, sd, late = kk
            if sd != "*" or late != "*":
                continue
            out[(st, "*", "*")] = rate((st, "*", "*"), top)
        for kk in agg:
            st, sd, late = kk
            if st == "*" or sd != "*" or late == "*":
                continue
            out[(st, "*", late)] = rate(kk, out.get((st, "*", "*"), top))
        for kk in agg:
            st, sd, late = kk
            if st == "*" or sd == "*":
                continue
            out[kk] = rate(kk, out.get((st, "*", late), out.get((st, "*", "*"), top)))
        out[("*", "*", "*")] = top
        return out

    def rate(self, table, st, sd, late):
        s = _sk(st)
        r = table.get((s, str(sd), str(late)))
        if r is None:
            r = table.get((s, "*", str(late))) or table.get((s, "*", "*")) or table[("*", "*", "*")]
        return r

    def on_target(self, st):
        """P(on goal | an unblocked attempt that did not score) in state st."""
        return self.sog.get(_sk(st), self.lg_sog)

    def draw_shot(self, st, rng):
        """(distance bucket, rebound) for an unblocked attempt in state st."""
        cum, t = self.mix_cum.get(_sk(st)) or self.mix_cum.get("5,5,1,1")
        x = rng.random() * t
        for c, cell in cum:
            if x < c:
                return cell
        return cum[-1][1]

    def p_goal(self, st, db, reb):
        p = self.goal.get(f"{_sk(st)}|{db}|{reb}")
        if p is None:
            p = self.goal_db.get(("en" if not st[3] else "gi", str(db), str(reb)), 0.07)
        return p


def tables(path=None):
    path = path or TABLES_PATH
    if _T["tables"] is None or _T["path"] != path:
        with open(path) as fh:
            _T["tables"] = Tables(json.load(fh))
        _T["path"] = path
    return _T["tables"]


def league_profile(abbr="LG"):
    return dict({k: 1.0 for k in PROFILE_KEYS}, abbr=abbr, level_off=1.0)


# ---- the game ---------------------------------------------------------------------
class _Side:
    __slots__ = ("p", "box", "serving", "queue", "goalie_in", "pull_at")

    def __init__(self, prof):
        self.p = prof
        self.box = {"goals": 0, "sog": 0, "att": 0, "unbl": 0, "pp_goals": 0, "pp_opp": 0, "sh_goals": 0,
                    "en_goals": 0, "delayed_goals": 0, "pim": 0, "pens": 0, "goals_by_per": [0, 0, 0, 0],
                    "pulled_secs": 0.0, "secs": {}}
        self.serving = []          # [(expires at game second, kind)] at most two
        self.queue = []            # kinds waiting for a box slot
        self.goalie_in = 1
        self.pull_at = None        # game second at which the goalie comes out


def _skaters(sd_):
    return 5 - min(2, len(sd_.serving))


def _state(me, opp):
    """(own skaters, opp skaters, own goalie in, opp goalie in); a pulled
    goalie is an extra skater."""
    own = _skaters(me) + (0 if me.goalie_in else 1)
    oth = _skaters(opp) + (0 if opp.goalie_in else 1)
    return (own, oth, me.goalie_in, opp.goalie_in)


def _expire(side, now):
    """Release every penalty that has expired by `now`, starting queued ones."""
    changed = False
    while side.serving:
        side.serving.sort()
        exp, kind = side.serving[0]
        if exp > now:
            break
        side.serving.pop(0)
        changed = True
        if kind == "double2":                     # the second half of a double minor
            side.serving.append((exp + 120, "minor"))
        while side.queue and len(side.serving) < 2:
            k = side.queue.pop(0)
            side.serving.append((exp + (300 if k == "major" else 120), "double2" if k == "double" else k))
    return changed


def _take_penalty(side, kind, now):
    side.box["pens"] += 1
    side.box["pim"] += {"minor": 2, "double": 4, "major": 5}[kind]
    if len(side.serving) >= 2:
        side.queue.append(kind)
        return True
    side.serving.append((now + (300 if kind == "major" else 120), "double2" if kind == "double" else kind))
    return True


def _pp_goal_release(opp, now):
    """A power-play goal ends the earliest-expiring MINOR of the shorthanded side
    (a double minor's current half); majors are served in full."""
    mins = [(e, k) for e, k in opp.serving if k in ("minor", "double2")]
    if not mins:
        return
    e, k = min(mins)
    opp.serving.remove((e, k))
    if k == "double2":
        opp.serving.append((now + 120, "minor"))
    while opp.queue and len(opp.serving) < 2:
        kk = opp.queue.pop(0)
        opp.serving.append((now + (300 if kk == "major" else 120), "double2" if kk == "double" else kk))


def _pick_kind(T, rng):
    x = rng.random()
    acc = 0.0
    for k, p in T.pen_mix:
        acc += p
        if x < acc:
            return k
    return "minor"


# The window a planned pull is drawn in, seconds left, by deficit. The feed's
# earlier "pulls" for a one- or two-goal deficit are seconds long -- an empty
# net on a power play or a delayed call the ten-second filter misses -- and
# real pulled time before the last five minutes is 0.2 and 0.1 s a game
# (2025-26); a planned pull here lasts until a goal, so drawing those early
# times put 9.6 and 6.3 s a game there. Three down, teams do go early.
_PULL_WINDOW = {1: 300, 2: 300, 3: 600}


def _plan_pull(T, side, deficit, now_in_per, per, rng):
    """When (game second) a side trailing by `deficit` in the third pulls its
    goalie, or None: with the measured probability for that deficit, at a
    time drawn from the measured pulls CONDITIONED on the moment -- teams
    whose deficit already stood at this many seconds left and who had not
    yet pulled. The pooled pull times lean late (many deficits arrive in the
    last minute), and drawing from them for a team trailing all period
    pulled it later than such teams do. With no such pull on record, a few
    seconds from now."""
    if per != 3 or deficit <= 0:
        return None
    d = min(3, deficit)
    if rng.random() >= T.pull_p.get(d, 0.0):
        return None
    left = REG_SECS - now_in_per
    cap = _PULL_WINDOW[d]
    cands = [rem for rem, since in T.pull_secs[d] if since >= left and rem <= min(left, cap)]
    if not cands:
        return now_in_per + 5 + 2 * REG_SECS
    rem = cands[int(rng.random() * len(cands))]
    return max(now_in_per + 5, REG_SECS - rem) + 2 * REG_SECS


def play_game(hp, ap, rng, T=None, neutral=False, playoff=False, hfa=None):
    """One game. Returns {"h": home box, "a": away box, "ot": bool, "so": bool,
    "winner": 0 home / 1 away}. `hfa` overrides the home-ice factor (1.0 for
    none); a neutral site has none."""
    T = T or tables()
    sides = (_Side(hp), _Side(ap))
    ha = 1.0 if neutral else (T.home_att if hfa is None else hfa)
    hpn = 1.0 if neutral else T.home_pen
    score = [0, 0]
    snap5 = None

    def mult_att(i, st):
        me, op = sides[i].p, sides[1 - i].p
        m = (me.get("att_off", 1.0) * op.get("att_def", 1.0)) * (ha if i == 0 else 1.0 / ha)
        return m

    def p_goal_mult(i, st):
        me, op = sides[i].p, sides[1 - i].p
        m = me.get("xg_off", 1.0) * op.get("xg_def", 1.0) * me.get("fin", 1.0) * me.get("level_off", 1.0)
        if st[3]:                                     # the opponent's goalie is in
            m *= op.get("gk", 1.0)
        if st[0] > st[1] and st[3]:                   # a power play
            m *= me.get("pp", 1.0) * op.get("pk", 1.0)
        return m

    for per in (1, 2, 3):
        t = 0.0
        if per == 3:
            for i in (0, 1):
                sides[i].pull_at = _plan_pull(T, i, score[1 - i] - score[i], 0, 3, rng)
        while True:
            g0 = (per - 1) * REG_SECS
            now = g0 + t
            for s in sides:
                _expire(s, now)
            for i in (0, 1):
                s = sides[i]
                if s.pull_at is not None and s.goalie_in and now >= s.pull_at:
                    s.goalie_in = 0
            late = 1 if (per == 3 and t >= REG_SECS - 300) else 0
            if late and snap5 is None:
                snap5 = (score[0], score[1])
            sts = [_state(sides[0], sides[1]), _state(sides[1], sides[0])]
            sd = [max(-3, min(3, score[0] - score[1])), max(-3, min(3, score[1] - score[0]))]
            ra = [T.rate(T.att, sts[i], sd[i], late) * mult_att(i, sts[i]) for i in (0, 1)]
            rp = [T.rate(T.pen, sts[i], sd[i], late) * sides[i].p.get("pen_take", 1.0)
                  * sides[1 - i].p.get("pen_draw", 1.0) * (hpn if i == 0 else 1.0 / hpn) for i in (0, 1)]
            R = ra[0] + ra[1] + rp[0] + rp[1]
            dt = rng.expovariate(R) if R > 0 else 1e9
            # the next deterministic change: the period's end, a penalty
            # expiring, a planned pull
            nxt = g0 + REG_SECS
            if per == 3 and t < REG_SECS - 300:
                # the last five minutes are their own cells: the switch is a
                # boundary, or the step that crosses it would be charged (and
                # rated) as earlier play -- it cost 30 s of the 300 a game
                nxt = min(nxt, g0 + REG_SECS - 300)
            for s in sides:
                for e, _k in s.serving:
                    nxt = min(nxt, e)
                if s.pull_at is not None and s.goalie_in:
                    nxt = min(nxt, s.pull_at)
            step = min(dt, nxt - now)
            for j, s_ in enumerate(sides):
                if not s_.goalie_in:
                    s_.box["pulled_secs"] += step
                k = (_sk(sts[j]), sd[j], late)
                s_.box["secs"][k] = s_.box["secs"].get(k, 0.0) + step
            if now + dt >= nxt:
                t = nxt - g0
                if t >= REG_SECS:
                    break
                continue
            t += dt
            now = g0 + t
            x = rng.random() * R
            if x < ra[0] + ra[1]:
                i = 0 if x < ra[0] else 1
                me, op = sides[i], sides[1 - i]
                st = sts[i]
                me.box["att"] += 1
                if rng.random() >= T.unbl.get(_sk(st), T.lg_unbl):
                    continue
                me.box["unbl"] += 1
                db, reb = T.draw_shot(st, rng)
                p = T.p_goal(st, db, reb) * p_goal_mult(i, st)
                if st[3]:
                    p *= T.conv.get((sd[i], late), 1.0)
                p = min(0.95, p)
                if rng.random() < p:
                    score[i] += 1
                    me.box["goals"] += 1
                    me.box["sog"] += 1
                    me.box["goals_by_per"][per - 1] += 1
                    if not st[3]:
                        me.box["en_goals"] += 1
                    if st[0] > st[1] and op.serving:
                        me.box["pp_goals"] += 1
                        _pp_goal_release(op, now)
                    elif st[0] < st[1] and st[2] and st[3]:
                        me.box["sh_goals"] += 1
                    # a goal resets the extra attacker: the net comes back,
                    # and whoever trails now plans afresh
                    for s in sides:
                        s.goalie_in = 1
                        s.pull_at = None
                    if per == 3:
                        for j in (0, 1):
                            sides[j].pull_at = _plan_pull(T, j, score[1 - j] - score[j], t, 3, rng)
                elif st[3]:
                    # a saved shot or a miss, in the state's measured share
                    if rng.random() < T.on_target(st):
                        me.box["sog"] += 1
            else:
                i = 0 if x < ra[0] + ra[1] + rp[0] else 1
                kind = _pick_kind(T, rng)
                # the delayed penalty: the fouled side attacks with its net
                # empty until the whistle; the clock runs
                f = 1 - i
                win = (T.delay_secs[int(rng.random() * len(T.delay_secs))]
                       if rng.random() < T.p_delayed else 0.0)
                win = min(win, max(0.0, REG_SECS - t - 0.5))
                scored = False
                tw = 0.0
                if T.delay_att_rate > 0 and win > 0:
                    while True:
                        tw += rng.expovariate(T.delay_att_rate)
                        if tw >= win:
                            break
                        fs, os_ = sides[f], sides[i]
                        fs.box["att"] += 1
                        if rng.random() >= T.delay_unbl:
                            continue
                        fs.box["unbl"] += 1
                        m = (fs.p.get("xg_off", 1.0) * os_.p.get("xg_def", 1.0) * fs.p.get("fin", 1.0)
                             * os_.p.get("gk", 1.0) * fs.p.get("level_off", 1.0))
                        if rng.random() < min(0.95, T.delay_goal * m):
                            scored = True
                            score[f] += 1
                            fs.box["goals"] += 1
                            fs.box["sog"] += 1
                            fs.box["goals_by_per"][per - 1] += 1
                            fs.box["delayed_goals"] += 1
                            break
                        if rng.random() < T.delay_sog:
                            fs.box["sog"] += 1
                    t += min(tw, win)
                    now = g0 + t
                if scored:
                    for s in sides:
                        s.goalie_in = 1
                        s.pull_at = None
                    if per == 3:
                        for j in (0, 1):
                            sides[j].pull_at = _plan_pull(T, j, score[1 - j] - score[j], t, 3, rng)
                    if kind == "minor":
                        sides[i].box["pens"] += 1
                        sides[i].box["pim"] += 2
                        continue
                    if kind == "double":
                        kind = "minor"            # the first half is washed out
                _take_penalty(sides[i], kind, now)
                sides[f].box["pp_opp"] += 1
    ot = so = False
    if score[0] == score[1]:
        ot = True
        for s in sides:
            s.goalie_in = 1
            s.pull_at = None
            s.serving, s.queue = [], []
        if playoff:
            winner = _playoff_ot(T, sides, score, rng, mult_att, p_goal_mult)
        else:
            winner = _reg_ot(T, sides, score, rng, ha)
            if winner is None:
                so = True
                winner = _shootout(T, sides, rng)
                score[winner] += 1
    else:
        winner = 0 if score[0] > score[1] else 1
    return {"h": sides[0].box, "a": sides[1].box, "score": tuple(score), "ot": ot, "so": so, "winner": winner,
            "score_at_5": snap5}


def _reg_ot(T, sides, score, rng, ha):
    """Five minutes of three-on-three, sudden death; None if nobody scores."""
    t = 0.0
    r = [T.ot_att_rate * sides[i].p.get("att_off", 1.0) * sides[1 - i].p.get("att_def", 1.0)
         * (ha if i == 0 else 1.0 / ha) for i in (0, 1)]
    while True:
        R = r[0] + r[1]
        t += rng.expovariate(R)
        if t >= OT_SECS_REG:
            return None
        i = 0 if rng.random() * R < r[0] else 1
        me, op = sides[i], sides[1 - i]
        me.box["att"] += 1
        if rng.random() >= T.ot_unbl:
            continue
        me.box["unbl"] += 1
        m = me.p.get("xg_off", 1.0) * op.p.get("xg_def", 1.0) * me.p.get("fin", 1.0) * op.p.get("gk", 1.0) \
            * me.p.get("level_off", 1.0)
        if rng.random() < min(0.9, T.ot_goal * m):
            score[i] += 1
            me.box["goals"] += 1
            me.box["sog"] += 1
            me.box["goals_by_per"][3] += 1
            return i
        if rng.random() < T.on_target((3, 3, 1, 1)):
            me.box["sog"] += 1


def _playoff_ot(T, sides, score, rng, mult_att, p_goal_mult):
    """Twenty-minute five-on-five periods until a goal."""
    st = (5, 5, 1, 1)
    while True:
        t = 0.0
        r = [T.rate(T.att, st, 0, 0) * mult_att(i, st) for i in (0, 1)]
        while True:
            R = r[0] + r[1]
            t += rng.expovariate(R)
            if t >= REG_SECS:
                break
            i = 0 if rng.random() * R < r[0] else 1
            me = sides[i]
            me.box["att"] += 1
            if rng.random() >= T.unbl.get("5,5,1,1", T.lg_unbl):
                continue
            me.box["unbl"] += 1
            db, reb = T.draw_shot(st, rng)
            if rng.random() < min(0.95, T.p_goal(st, db, reb) * p_goal_mult(i, st)):
                score[i] += 1
                me.box["goals"] += 1
                me.box["sog"] += 1
                me.box["goals_by_per"][3] += 1
                return i


def _shootout(T, sides, rng):
    """Three rounds, then sudden death, at the measured conversion scaled by
    each shooter side's finishing against the other's goaltending."""
    p = [min(0.8, T.so_goal * sides[i].p.get("fin", 1.0) * sides[1 - i].p.get("gk", 1.0)) for i in (0, 1)]
    g = [0, 0]
    for rnd in range(3):
        for i in (1, 0):                         # the visitor shoots first by default
            g[i] += rng.random() < p[i]
        if abs(g[0] - g[1]) > 3 - (rnd + 1):
            break
    if g[0] != g[1]:
        return 0 if g[0] > g[1] else 1
    while True:
        a = rng.random() < p[1]
        h = rng.random() < p[0]
        if a != h:
            return 0 if h else 1


def play_many(hp, ap, n, rng=None, T=None, neutral=False, playoff=False):
    """n games: per-side arrays of goals, plus the OT / SO flags and winners."""
    rng = rng or random.Random()
    T = T or tables()
    out = {"h": [], "a": [], "ot": 0, "so": 0, "home_wins": 0, "boxes": []}
    for _ in range(n):
        g = play_game(hp, ap, rng, T, neutral=neutral, playoff=playoff)
        out["h"].append(g["score"][0])
        out["a"].append(g["score"][1])
        out["ot"] += g["ot"]
        out["so"] += g["so"]
        out["home_wins"] += g["winner"] == 0
        out["boxes"].append((g["h"], g["a"]))
    return out
