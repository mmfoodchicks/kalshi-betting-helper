"""The NBA deep engine: one basketball game played possession by possession
on the real clock, both teams' units against each other, sampled from tables
measured on the play-by-play (research/nba_deep_fit.py ->
research/data/nba_deep_tables.json). The basketball twin of nfl_deep_sim.

A possession draws its length from the measured distribution for how it
began (after a make, a defensive rebound, a steal, a dead-ball turnover, made
free throws, a period's start) and the game situation (normal; the last two
minutes of a close game, offence leading / trailing / level; garbage time),
scaled by both teams' pace. It is then played as SHOT OPPORTUNITIES until the
ball changes hands:

  opportunity   a turnover (live-ball steal or dead ball), a free-throw trip
                with no field goal attempt on the record (a shooting foul on
                a missed shot -- which the box score never counts as an
                attempt -- the bonus, a flagrant), or a field goal attempt
  attempt       a zone (rim / 4-13 ft / long two / three) from the measured
                mix for a first or a second chance, made with the zone's
                rate times the shooter's and the defence's units, an and-one
                in the measured share of makes
  miss          rebounded by the offence with the zone's measured rate (a
                second chance) or the defence (the possession ends)
  free throws   each made at the offence's rate; a missed last one is
                rebounded like a shot

If the drawn length runs past the period's clock the possession is the
period's last: it is played out with the measured share of buzzer
possessions that never get a shot away. Overtime is five-minute periods until
someone leads.

A team is a PROFILE of multipliers on the league (nba_deep_data):
  tov_off / tov_def         turnovers committed / forced
  fts_off / fts_def         free-throw trips without an attempt drawn / conceded
  three_off / three_def     the share of attempts from three, taken / allowed
  rim_off / rim_def         the share at the rim
  mk_rim / mk_two / mk_three  shooting by zone, the offence's  (x the defence's
  dmk_rim / dmk_two / dmk_three  allowed)
  oreb_off / oreb_def       offensive rebounding / defensive rebounding allowed
  ft                        free-throw shooting
  pace                      possession length (a multiplier on the seconds)
  level_off                 a lever on every make rate, 1.0 (no market anchor yet)
"""
import json
import os
import random

TABLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", "data", "nba_deep_tables.json")
_T = {"tables": None, "path": None}

PROFILE_KEYS = ("tov_off", "tov_def", "fts_off", "fts_def", "three_off", "three_def", "rim_off", "rim_def",
                "mk_rim", "mk_two", "mk_three", "dmk_rim", "dmk_two", "dmk_three", "oreb_off", "oreb_def",
                "ft", "pace")
STARTS = ("period", "make", "dreb", "steal", "dead_tov", "ft", "other")
SITS = ("norm", "cl+", "cl-", "cl0", "garbage")
QTR = 720
OT = 300
_K_SIT = 400.0                 # pseudo-counts pulling a thin situation's cells toward "norm"
# Home court, on the make rates. Fitted so the engine's REALIZED home edge
# matches the league's (2024-25, two league-average sides, 6,000 games a
# value): 1.0072 (the measured ratio's square root, used as a lever) gave a
# home win rate of .528, a realized points-a-possession ratio of 1.0085 and
# a mean margin of +0.96; 1.012 gives .544 / 1.0144 / +1.62 against the
# league's .544 / 1.0144 / +1.6 (net of the teams); 1.016 overshoots (.559).
_HFA_MAKE = 1.012


def situation(per, clk, sd):
    if per >= 4 and clk <= 120 and abs(sd) <= 8:
        return "cl+" if sd > 0 else ("cl-" if sd < 0 else "cl0")
    if per == 4 and abs(sd) >= 20:
        return "garbage"
    return "norm"


def _cum(pairs):
    tot = float(sum(w for _k, w in pairs)) or 1.0
    acc, out = 0.0, []
    for k, w in pairs:
        acc += w / tot
        out.append((acc, k))
    return out


def _pick(cum, rng):
    x = rng.random()
    for c, k in cum:
        if x < c:
            return k
    return cum[-1][1]


class Tables:
    def __init__(self, raw):
        self.raw = raw
        dm = int(raw.get("dur_max") or 30)
        # ---- possession lengths: (sit, start) -> seconds, smoothed toward the norm cell
        dur = {}
        for k, n in raw["dur"].items():
            sit, start, d = k.split("|")
            dur.setdefault((sit, start), {})[int(d)] = n
        self.dur = {}
        for (sit, start), h in dur.items():
            base = dur.get(("norm", start)) or h
            bt = float(sum(base.values())) or 1.0
            ht = float(sum(h.values()))
            w = ht / (ht + _K_SIT) if sit != "norm" else 1.0
            mix = {d: w * h.get(d, 0) / max(1.0, ht) + (1 - w) * base.get(d, 0) / bt for d in range(dm + 1)}
            self.dur[(sit, start)] = _cum(sorted(mix.items()))
        # ---- the opportunity mix: (sit, start-or-"oreb") -> tov1 / tov2 / fts / fga
        opp = {}
        for k, n in raw["opp"].items():
            sit, start, kind = k.split("|")
            opp.setdefault((sit, start), {})[kind] = n
        self.opp = {}
        for key, h in opp.items():
            base = opp.get(("norm", key[1])) or h
            ht, bt = float(sum(h.values())), float(sum(base.values())) or 1.0
            w = ht / (ht + _K_SIT) if key[0] != "norm" else 1.0
            self.opp[key] = {kk: w * h.get(kk, 0) / max(1.0, ht) + (1 - w) * base.get(kk, 0) / bt
                             for kk in ("tov1", "tov2", "fts", "fga")}
        # ---- zones and makes: (sit, chance) -> zone mix; (sit, chance, zone) -> make
        # keyed (sit, chance, start group): a first chance's shot depends on
        # how the ball came (transition / live rebound / dead ball), a second
        # chance's ("-") does not
        zone = {}
        for k, n in raw["zone"].items():
            sit, ch, sg, z = k.split("|")
            zone.setdefault((sit, int(ch), sg), {})[int(z)] = n
        self.zone = {}
        for key, h in zone.items():
            base = zone.get(("norm", key[1], key[2])) or h
            ht, bt = float(sum(h.values())), float(sum(base.values())) or 1.0
            w = ht / (ht + _K_SIT) if key[0] != "norm" else 1.0
            self.zone[key] = [w * h.get(z, 0) / max(1.0, ht) + (1 - w) * base.get(z, 0) / bt for z in range(4)]
        self.make = {}
        for k, (m, n) in raw["make"].items():
            sit, ch, sg, z = k.split("|")
            bm, bn = raw["make"].get(f"norm|{ch}|{sg}|{z}") or (m, n)
            prior = bm / bn if bn else 0.45
            self.make[(sit, int(ch), sg, int(z))] = (m + 200.0 * prior) / (n + 200.0)
        self.and1 = {int(k): v[0] / v[1] if v[1] else 0.03 for k, v in raw["and1"].items()}
        self.oreb = {}
        for k, (o, n) in raw["oreb"].items():
            sit, z = k.split("|")
            bo, bn = raw["oreb"].get(f"norm|{z}") or (o, n)
            prior = bo / bn if bn else 0.3
            self.oreb[(sit, int(z))] = (o + 200.0 * prior) / (n + 200.0)
        fn = {}
        for k, n in raw.get("fts_n", {}).items():
            sit, c = k.split("|")
            fn.setdefault(sit, []).append((int(c), n))
        self.fts_n = {sit: _cum(sorted(v)) for sit, v in fn.items()}
        self.ft = raw["ft"][0] / raw["ft"][1] if raw["ft"][1] else 0.78
        self.ft_oreb = raw["ft_oreb"][0] / raw["ft_oreb"][1] if raw["ft_oreb"][1] else 0.11
        # a period's final possession, begun with the shot clock off: its
        # points by how much clock it began with (the heave, the hold)
        fin = {}
        for k, n in (raw.get("final") or {}).items():
            b, pts = k.split("|")
            fin.setdefault(b, []).append((int(pts), n))
        self.final = {b: _cum(sorted(v)) for b, v in fin.items()}
        hp = raw["home"]["pts"]
        hn = raw["home"]["poss"]
        hppp, appp = hp[0] / hn[0], hp[1] / hn[1]
        # the REALIZED home edge (points a possession, home over away) and the
        # engine's lever, which is larger: the pull back toward an even game
        # takes back part of any edge, so the measured ratio as a lever
        # realized only 60% of itself (home win .528 against .544)
        self.home_ratio = hppp / appp if appp else 1.0
        self.home_eff = _HFA_MAKE
        self.lg_pts = raw["pts"] / raw["games"] if raw["games"] else 226.0
        # the pull back toward an even game (nba_deep_fit.score_slopes):
        # points a possession per point of the offence's lead, by quarter,
        # applied to the make rates as the share of a possession's points
        # that field goals carry
        self.score_slope = {int(k): v for k, v in (raw.get("score_slope") or {}).items()}
        fg_pts = 0.0
        tot_pts = float(raw["pts"]) or 1.0
        for k, (m, n) in raw["make"].items():
            z = int(k.split("|")[3])
            fg_pts += m * (3 if z == 3 else 2)
        poss = float(sum(raw["home"]["poss"])) or 1.0
        self.ppp = tot_pts / poss
        self.fg_ppp = fg_pts / poss


def tables(path=None):
    path = path or TABLES_PATH
    if _T["tables"] is None or _T["path"] != path:
        with open(path) as fh:
            _T["tables"] = Tables(json.load(fh))
        _T["path"] = path
    return _T["tables"]


def league_profile(abbr="LG"):
    return dict({k: 1.0 for k in PROFILE_KEYS}, abbr=abbr, level_off=1.0)


def _box():
    return {"pts": 0, "poss": 0, "fga": 0, "fgm": 0, "tpa": 0, "tpm": 0, "fta": 0, "ftm": 0, "oreb": 0,
            "dreb": 0, "tov": 0, "stl": 0, "rim_a": 0, "rim_m": 0, "pts_by_per": [], "by_sit": {}}


def _start_group(start):
    if start == "steal":
        return "trans"
    if start == "dreb":
        return "live"
    return "dead"


def _possession(T, sit, start, me, op, rng, hfac, smult=1.0):
    """One possession for `me` against `op`; returns (points, next start,
    events) -- events mutate the boxes through the caller."""
    pts = 0
    chance = 0
    ev = []
    while True:
        key = (sit, start if chance == 0 else "oreb")
        mix = T.opp.get(key) or T.opp.get(("norm", key[1])) or T.opp[("norm", "make")]
        p_tov1 = mix["tov1"] * me["tov_off"] * op["tov_def"]
        p_tov2 = mix["tov2"] * me["tov_off"] * op["tov_def"]
        p_fts = mix["fts"] * me["fts_off"] * op["fts_def"]
        tot = p_tov1 + p_tov2 + p_fts + mix["fga"]
        x = rng.random() * tot
        if x < p_tov1:
            ev.append(("tov", 1))
            return pts, "steal", ev
        if x < p_tov1 + p_tov2:
            ev.append(("tov", 2))
            return pts, "dead_tov", ev
        if x < p_tov1 + p_tov2 + p_fts:
            cum = T.fts_n.get(sit) or T.fts_n.get("norm")
            nft = _pick(cum, rng) if cum else 2
            pft = min(0.99, T.ft * me["ft"])
            last = 0
            for _ in range(nft):
                last = 1 if rng.random() < pft else 0
                pts += last
                ev.append(("ft", last))
            if last:
                return pts, "ft", ev
            if rng.random() < T.ft_oreb * me["oreb_off"] * op["oreb_def"]:
                ev.append(("oreb", 0))
                chance = 1
                continue
            ev.append(("dreb", 0))
            return pts, "dreb", ev
        # a field goal attempt
        sg = _start_group(start) if chance == 0 else "-"
        ch = min(chance, 1)
        zm = T.zone.get((sit, ch, sg)) or T.zone[("norm", ch, sg)]
        w = [zm[0] * me["rim_off"] * op["rim_def"], zm[1], zm[2], zm[3] * me["three_off"] * op["three_def"]]
        z = _pick(_cum(list(enumerate(w))), rng)
        pm = T.make.get((sit, ch, sg, z)) or T.make[("norm", ch, sg, z)]
        unit = (me["mk_rim"] * op["dmk_rim"] if z == 0 else me["mk_three"] * op["dmk_three"] if z == 3
                else me["mk_two"] * op["dmk_two"])
        pm = min(0.97, pm * unit * me.get("level_off", 1.0) * hfac * smult)
        made = rng.random() < pm
        val = 3 if z == 3 else 2
        ev.append(("fga", z, 1 if made else 0))
        if made:
            pts += val
            if rng.random() < T.and1.get(z, 0.03):
                m1 = 1 if rng.random() < min(0.99, T.ft * me["ft"]) else 0
                pts += m1
                ev.append(("ft", m1))
                if not m1:
                    if rng.random() < T.ft_oreb * me["oreb_off"] * op["oreb_def"]:
                        ev.append(("oreb", 0))
                        chance = 1
                        continue
                    ev.append(("dreb", 0))
                    return pts, "dreb", ev
            return pts, "make", ev
        po = T.oreb.get((sit, z)) or T.oreb[("norm", z)]
        if rng.random() < min(0.9, po * me["oreb_off"] * op["oreb_def"]):
            ev.append(("oreb", 0))
            chance = 1
            continue
        ev.append(("dreb", 0))
        return pts, "dreb", ev


def _apply(box, obox, ev):
    for e in ev:
        k = e[0]
        if k == "fga":
            z, made = e[1], e[2]
            box["fga"] += 1
            box["fgm"] += made
            if z == 3:
                box["tpa"] += 1
                box["tpm"] += made
            if z == 0:
                box["rim_a"] += 1
                box["rim_m"] += made
        elif k == "ft":
            box["fta"] += 1
            box["ftm"] += e[1]
        elif k == "oreb":
            box["oreb"] += 1
        elif k == "dreb":
            obox["dreb"] += 1
        elif k == "tov":
            box["tov"] += 1
            if e[1] == 1:
                obox["stl"] += 1


def play_game(hp, ap, rng, T=None, neutral=False):
    """One game: {"h": box, "a": box, "score": (h, a), "ot": periods past four,
    "winner": 0 home / 1 away}."""
    T = T or tables()
    profs = (hp, ap)
    boxes = (_box(), _box())
    score = [0, 0]
    hf = 1.0 if neutral else T.home_eff
    hfac = (hf, 1.0 / hf)
    # the opening tip; the other side opens the 2nd and 3rd, the tip-winner the 4th
    tip = 0 if rng.random() < 0.5 else 1
    per = 0
    ot = 0
    while True:
        per += 1
        if per > 4:
            if score[0] != score[1]:
                break
            ot += 1
        clock = float(QTR if per <= 4 else OT)
        # overtime opens with a jump ball
        off = tip if per in (1, 4) else (1 - tip if per <= 4 else (0 if rng.random() < 0.5 else 1))
        start = "period"
        per_pts = [0, 0]
        while clock > 0:
            sd = score[off] - score[1 - off]
            sit = situation(per, clock, sd)
            me, op = profs[off], profs[1 - off]
            cum = T.dur.get((sit, start)) or T.dur.get(("norm", start)) or T.dur[("norm", "make")]
            # the measured lengths are differences of whole-second clocks,
            # unbiased as they stand (a half-second of jitter on top cost 2.4
            # possessions a team-game)
            d = float(_pick(cum, rng))
            d *= (me["pace"] * op["pace"]) ** 0.5
            if d >= clock and clock <= 24 and T.final:
                # the period's final possession: drawn whole from the measured
                # finals for this much clock (heaves, holds, the last shot)
                b = "le5" if clock <= 5 else ("6-12" if clock <= 12 else "13-24")
                pts = _pick(T.final.get(b) or T.final["le5"], rng)
                boxes[off]["poss"] += 1
                boxes[off]["pts"] += pts
                score[off] += pts
                per_pts[off] += pts
                bs = boxes[off]["by_sit"].setdefault("final", [0, 0])
                bs[0] += pts
                bs[1] += 1
                break
            if d >= clock:
                d = clock
            slope = T.score_slope.get(min(per, 4), 0.0)
            smult = 1.0 + slope * max(-20, min(20, sd)) / T.fg_ppp if T.fg_ppp else 1.0
            pts, nxt, ev = _possession(T, sit, start, me, op, rng, hfac[off], smult)
            boxes[off]["poss"] += 1
            bs = boxes[off]["by_sit"].setdefault(sit, [0, 0])
            bs[0] += pts
            bs[1] += 1
            _apply(boxes[off], boxes[1 - off], ev)
            boxes[off]["pts"] += pts
            score[off] += pts
            per_pts[off] += pts
            clock -= d
            off = 1 - off
            start = nxt
        boxes[0]["pts_by_per"].append(per_pts[0])
        boxes[1]["pts_by_per"].append(per_pts[1])
        if per >= 4 and score[0] != score[1]:
            break
    return {"h": boxes[0], "a": boxes[1], "score": (score[0], score[1]), "ot": ot,
            "winner": 0 if score[0] > score[1] else 1}


def play_many(hp, ap, n, rng=None, T=None, neutral=False):
    rng = rng or random.Random()
    T = T or tables()
    return [play_game(hp, ap, rng, T, neutral) for _ in range(n)]
