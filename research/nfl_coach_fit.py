"""Fit the experimental coaching engine's tables (nfl_coach_sim) from nflverse
play-by-play. RESEARCH: writes research/data/nfl_coach_tables.json, which only
nfl_coach_sim and the research scripts beside this one load.

  python3 -m research.nfl_coach_fit <dir of play_by_play_<season>.csv.gz>
          [--out research/data/nfl_coach_tables.json]

SEASONS (research/reports/nfl_coaching_audit.md 7.1): fitted on 2016-2023; the
three smoothing masses chosen on 2024; 2025 printed as a check that can only
block; 2026 weeks 1-12 are development and are not read here; 2026 week 13 on
is sealed (nfl_coach_data refuses it).

Two parts:

  base   production's own fit (research.nfl_deep_fit.build_tables, imported,
         not edited) run on these seasons, so the CONTROL arm -- nfl_deep_sim
         on `base` -- is the production method on the same data. Three things
         differ from research/data/nfl_deep_tables.json and are stated in the
         report: a change of possession is flagged (the fourth-down end-spot
         defect cannot arise), the fumble spot is measured from the end of the
         play, and the kickoff table is kept per rules era (2016-2023, 2024,
         2025+; the two later eras hold ONLY kickoff field position from those
         seasons -- a rule of the game, shared by both arms, not a behaviour).
  coach  the count tables of the clock decisions and the seconds to the next
         snap with timeouts taken out, plus the ridge play-call terms.

Data credit: nflverse-data (CC BY 4.0).
"""
import datetime
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nfl_coach_sim as C                     # noqa: E402
import nfl_deep_sim as D                      # noqa: E402
import nfl_pbp as P                           # noqa: E402
from research import nfl_coach_data as CD     # noqa: E402
from research import nfl_deep_fit as F        # noqa: E402

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach_tables.json")
FIT = tuple(range(2016, 2024))
SELECT = 2024
BLOCK = 2025
ONSIDE_FROM = 2018                # the kickoff formation rule that made recoveries rare
GRID = {"rate_n": (5.0, 20.0, 80.0), "hist_k": (10.0, 40.0, 160.0), "lam": (2.0, 8.0, 32.0)}
_PLAY = (P.RUSH, P.PASS_COMP, P.PASS_INC, P.SACK, P.INT)
_SNAP = _PLAY + (P.KNEEL, P.SPIKE, P.PENALTY)
_ADMIN = (P.TIMEOUT, P.TWO_MIN, P.END_PERIOD)
_KICK = (P.PUNT, P.PUNT_BLOCK, P.FG_GOOD, P.FG_MISS, P.FG_BLOCK)
SPIKE_PHASES = (3, 4, 5, 11, 12, 13)


# ---- base: production's fit on these seasons -----------------------------------
def base_tables(by_season):
    """{season: games} -> nfl_deep_sim's table dict."""
    saved = dict(F.KICKOFF_ERA)
    try:
        F.KICKOFF_ERA.clear()
        F.KICKOFF_ERA.update({s: "2016-2023" for s in FIT})
        F.KICKOFF_ERA[2024] = "2024"
        T = F.build_tables([(s, CD.pbp_store(by_season[s])) for s in FIT])
        # the later kickoff eras: field position only, from that era's own kickoffs
        for s in (2024, 2025):
            if s in by_season:
                E = F.build_tables([(s, CD.pbp_store(by_season[s]))])
                for era, h in E["kickoff"].items():
                    T["kickoff"][era] = h
                    T["kickoff_td"][era] = E["kickoff_td"][era]
    finally:
        F.KICKOFF_ERA.clear()
        F.KICKOFF_ERA.update(saved)
    # the spot the defence takes a fumble over at, from the END of the play
    spot = {}
    for s in FIT:
        for g in by_season[s]:
            for p in g["rows"]:
                if (p[CD.R_FLAGS] & P.F_FUMBLE_LOST and not p[CD.R_FLAGS] & P.F_TD
                        and p[CD.R_EYTG] > 0 and p[CD.R_CODE] in (P.RUSH, P.PASS_COMP, P.SACK)):
                    v = max(-20, min(60, (100 - (p[CD.R_YTG] - p[CD.R_YDS])) - p[CD.R_EYTG]))
                    spot[str(v)] = spot.get(str(v), 0) + 1
    T["fum_spot"] = {"all": spot}
    T.pop("team_prior", None)
    T["fit"]["source"] = "nflverse-data play-by-play (CC BY 4.0)"
    return T


# ---- the clock transitions of one game -----------------------------------------
def transitions(g):
    """One dict per offensive snap: the state, the clock class, and what came
    before the next snap (timeouts by side, the seconds, what the next row was)."""
    rows = g["rows"]
    n = len(rows)
    for i, p in enumerate(rows):
        code = p[CD.R_CODE]
        if code not in _SNAP:
            continue
        per, clk, off, fl = p[CD.R_PER], p[CD.R_CLK], p[CD.R_OFF], p[CD.R_FLAGS]
        tob = [0, 0]
        j = i + 1
        ended = False
        while j < n and rows[j][CD.R_CODE] in _ADMIN:
            q = rows[j]
            if q[CD.R_CODE] == P.TIMEOUT and q[CD.R_TOTEAM] >= 0 and q[CD.R_PER] == per:
                tob[q[CD.R_TOTEAM]] += 1
            if q[CD.R_CODE] == P.END_PERIOD:
                ended = True
                break
            j += 1
        nxt = None
        if j >= n or ended or rows[j][CD.R_PER] != per:
            ended = True
        else:
            nxt = rows[j]
        if code == P.INT or fl & P.F_TURNOVER:
            cls = "to"
        elif fl & P.F_TD:
            cls = "score"
        elif code == P.PENALTY:
            cls = "pen"
        elif code in (P.PASS_INC, P.SPIKE):
            cls = "inc"
        elif code == P.KNEEL:
            cls = "knl"
        elif fl & P.F_OOB:
            cls = "oob"
        else:
            cls = "run"
        sd = p[CD.R_SD]
        toff, tdef = (p[CD.R_TOH], p[CD.R_TOA]) if off == 0 else (p[CD.R_TOA], p[CD.R_TOH])
        gap = None
        if ended:
            if per in (2, 4):                  # the half ran out: the gap was at least the clock
                gap = min(60, clk + 1)
        else:
            dt = clk - nxt[CD.R_CLK]
            if 0 <= dt <= 90:
                gap = min(60, dt)
        yield {"p": p, "per": per, "clk": clk, "off": off, "sd": sd, "cls": cls, "toff": toff, "tdef": tdef,
               "to_off": tob[off], "to_def": tob[1 - off], "next": nxt, "ended": ended, "gap": gap,
               "ph": C.phase(per, clk)}


def _cnt(d, key, hit):
    c = d.setdefault(",".join(str(x) for x in key), [0, 0])
    c[1] += 1
    c[0] += 1 if hit else 0


def _hist(d, key, v):
    h = d.setdefault(",".join(str(x) for x in key), {})
    h[str(v)] = h.get(str(v), 0) + 1


def collect(games, onside_from=ONSIDE_FROM):
    """The coach count tables of a set of games, and the play-call rows."""
    T = {"to_def": {}, "to_off": {}, "to_stop": {}, "spike": {}, "kneel": {}, "nh": {}, "oob": {},
         "onside": {}, "gap": {}, "onside_rec": [0, 0]}
    aux = {"vf": [0, 0], "kneel_out": 0, "kneels": 0, "knl_gap": {}, "ytg_kept": [0, 0], "ytg_lost": [0, 0],
           "onside_surprise": 0, "timeouts": 0, "team_halves": 0}
    call = []
    for g in games:
        if g["st"] != 2:
            continue
        aux["team_halves"] += 4
        rows = g["rows"]
        for i, p in enumerate(rows):
            if p[CD.R_CODE] == P.TIMEOUT and p[CD.R_TOTEAM] >= 0 and p[CD.R_PER] <= 4:
                aux["timeouts"] += 1
            if p[CD.R_CODE] != P.KICKOFF:
                continue
            kicker_deficit = p[CD.R_SD]            # posteam is the receiver
            if p[CD.R_PER] == 4 and kicker_deficit > 0:
                _cnt(T["onside"], C.onside_key(p[CD.R_CLK], kicker_deficit), p[CD.R_ONSIDE] > 0)
                if p[CD.R_ONSIDE] and g["season"] >= onside_from:
                    T["onside_rec"][1] += 1
                    T["onside_rec"][0] += p[CD.R_ONSIDE] == 2
                if p[CD.R_ONSIDE]:
                    j = i + 1
                    while j < len(rows) and rows[j][CD.R_CODE] in _ADMIN:
                        j += 1
                    if j < len(rows) and rows[j][CD.R_CODE] in _SNAP and rows[j][CD.R_PER] == 4:
                        kept = rows[j][CD.R_OFF] != p[CD.R_OFF]
                        a = aux["ytg_kept" if kept else "ytg_lost"]
                        a[0] += rows[j][CD.R_YTG]
                        a[1] += 1
            elif p[CD.R_ONSIDE]:
                aux["onside_surprise"] += 1
        for t in transitions(g):
            p, per, clk, ph, cls = t["p"], t["per"], t["clk"], t["ph"], t["cls"]
            code, fl, sd = p[CD.R_CODE], p[CD.R_FLAGS], t["sd"]
            sc = P.score_state(sd)
            nxt = t["next"]
            down = p[CD.R_DOWN]
            # ---- kneel
            if code == P.KNEEL:
                aux["kneels"] += 1
            if code in _PLAY or code == P.KNEEL:
                safe, key = C.kneel_key(per, clk, down, sd, p[CD.R_YTG], t["tdef"])
                if key is None:
                    aux["kneel_out"] += code == P.KNEEL
                elif safe:
                    aux["vf"][1] += 1
                    aux["vf"][0] += code == P.KNEEL
                else:
                    _cnt(T["kneel"], key, code == P.KNEEL)
            # ---- the call, and out of bounds
            if code in _PLAY and 1 <= down <= 4:
                call.append((1 if fl & P.F_PASS_PLAY else 0, ph, C.s9(sd), down,
                             P.dist_bucket(max(1, p[CD.R_DIST])), P.zone(max(1, p[CD.R_YTG])),
                             min(2, t["toff"]), P.time_state(per, clk), sc))
                if not fl & (P.F_TD | P.F_FUMBLE_LOST):
                    if code == P.PASS_COMP:
                        _cnt(T["oob"], (0, ph, sc), fl & P.F_OOB)
                    elif code == P.RUSH:
                        _cnt(T["oob"], (1, ph, sc), fl & P.F_OOB)
            # ---- the clock after the play
            if per >= 5:
                ph_ok = True
            else:
                ph_ok = ph not in C.GAP_PHASE
            gap = t["gap"]
            nx_code = nxt[CD.R_CODE] if nxt is not None else None
            if cls in ("run", "knl"):
                if nx_code == P.KICKOFF:
                    continue                           # a safety or a score the flags missed
                d_to, o_to = t["to_def"] > 0, t["to_off"] > 0
                if t["tdef"] > 0:
                    _cnt(T["to_def"], (ph, sc, t["tdef"]), d_to)
                if not d_to and t["toff"] > 0 and cls == "run":
                    _cnt(T["to_off"], (ph, sc, t["toff"]), o_to)
                kind, nh = None, 0
                if d_to:
                    kind = "rtoD"
                elif o_to:
                    kind = "rtoO"
                elif nx_code in _KICK:
                    kind = "prepunt" if nx_code in (P.PUNT, P.PUNT_BLOCK) else "prefg"
                else:
                    snap_next = nx_code in _SNAP
                    if (cls == "run" and ph in SPIKE_PHASES and (t["ended"] or snap_next)
                            and (t["ended"] or nxt[CD.R_DOWN] <= 3)):
                        _cnt(T["spike"], (ph, C.s3(sd), min(2, t["toff"])), nx_code == P.SPIKE)
                    if nx_code == P.SPIKE:
                        kind = "spk"
                    elif cls == "knl":
                        if gap is not None and not t["ended"]:
                            _hist(aux["knl_gap"], ("all",), gap)
                    elif snap_next or t["ended"]:
                        if snap_next:
                            nh = 1 if nxt[CD.R_FLAGS] & P.F_NOHUDDLE else 0
                            _cnt(T["nh"], (ph, sc), nh)
                        kind = "run"
                if kind and gap is not None and ph_ok:
                    _hist(T["gap"], (kind, ph, sc, nh), gap)
            else:
                if cls in ("inc", "oob", "pen"):
                    if t["toff"] > 0:
                        _cnt(T["to_stop"], (ph, 0), t["to_off"] > 0)
                    if t["tdef"] > 0:
                        _cnt(T["to_stop"], (ph, 1), t["to_def"] > 0)
                if gap is None or not ph_ok:
                    continue
                if cls == "score":
                    if nx_code == P.KICKOFF or t["ended"]:
                        _hist(T["gap"], ("score", ph, P.score_state(sd + 6), 0), gap)
                elif cls == "to":
                    _hist(T["gap"], ("to", ph, sc, 0), gap)
                elif nx_code in _SNAP or nx_code in _KICK or t["ended"]:
                    _hist(T["gap"], (cls, ph, sc, 0), gap)
    k, n_ = aux["ytg_kept"]
    l, m_ = aux["ytg_lost"]
    T["onside_ytg"] = {"kept": k / n_ if n_ else 55.0, "lost": l / m_ if m_ else 45.0}
    return T, aux, call


# ---- the play call: ridge effects on the base cell's logit ----------------------
def _call_arrays(call, base):
    a = np.array(call, dtype=np.int64)
    off = np.array([D._logit(base.call_marg.get((r[3], r[4], r[5]), base.league["pass_rate"])) for r in call])
    gA = a[:, 1] * 9 + a[:, 2]
    s5 = np.array(C._S9_TO_S5)[a[:, 2]]
    gB = (a[:, 1] * 5 + s5) * 4 + (a[:, 3] - 1)
    gC = a[:, 1] * 3 + a[:, 6]
    return a[:, 0].astype(float), off, (gA, gB, gC), (C.N_PHASE * 9, C.N_PHASE * 20, C.N_PHASE * 3)


def fit_call(call, base, lam, rounds=12):
    """Backfitting: each group effect solves its own penalized score equation
    (ridge `lam` = prior precision; a thin cell stays at the base cell)."""
    y, off, G, sizes = _call_arrays(call, base)
    eff = [np.zeros(s) for s in sizes]
    for _ in range(rounds):
        for t in range(3):
            rest = off + sum(eff[u][G[u]] for u in range(3) if u != t)
            b = eff[t]
            for _n in range(4):
                pr = 1.0 / (1.0 + np.exp(-(rest + b[G[t]])))
                grad = np.bincount(G[t], weights=y - pr, minlength=sizes[t]) - lam * b
                hess = np.bincount(G[t], weights=pr * (1 - pr), minlength=sizes[t]) + lam
                b = b + grad / hess
            eff[t] = b
    A, B, Cc = {}, {}, {}
    for ph in range(C.N_PHASE):
        for sc in range(9):
            A[f"{ph},{sc}"] = round(float(eff[0][ph * 9 + sc]), 4)
        for sc in range(5):
            for down in (1, 2, 3, 4):
                B[f"{ph},{sc},{down}"] = round(float(eff[1][(ph * 5 + sc) * 4 + down - 1]), 4)
        for tb in range(3):
            Cc[f"{ph},{tb}"] = round(float(eff[2][ph * 3 + tb]), 4)
    return {"A": A, "B": B, "C": Cc, "lam": lam, "n": len(call)}


def call_logloss(call, T):
    """(coach model, the base engine's own (time state, score state) cell) mean log loss."""
    a = b = 0.0
    for y, ph, sc9, down, db, zn, tob, ts, ss in call:
        p = D._sig(T.p_pass(ph, sc9, down, db, zn, tob))
        q = T.base.p_pass(ts, ss, down, db, zn)
        a -= math.log(max(1e-9, p if y else 1 - p))
        b -= math.log(max(1e-9, q if y else 1 - q))
    n = len(call) or 1
    return a / n, b / n


def rates_logloss(counts, T):
    """Total log loss and n of held-out decision counts under T's smoothed rates."""
    tot, n = 0.0, 0
    for name in ("to_def", "to_off", "to_stop", "spike", "kneel", "nh", "oob", "onside"):
        tab = getattr(T, name)
        for key, (k, m) in counts[name].items():
            p = min(1 - 1e-6, max(1e-6, T.rate(tab, tuple(int(x) for x in key.split(",")))))
            tot -= k * math.log(p) + (m - k) * math.log(1 - p)
            n += m
    return tot, n


def gaps_logloss(counts, T):
    tot, n = 0.0, 0
    cache = {}
    for key, hist in counts["gap"].items():
        kind, ph, sc, nh = key.split(",")
        mix = T.gap_mix(kind, int(ph), int(sc), int(nh))
        if mix is None:
            continue
        pm = cache.get(id(mix))
        if pm is None:
            pm = cache[id(mix)] = mix.pmf()
        for v, c in hist.items():
            tot -= c * math.log(max(1e-4, pm.get(int(v), 0.0)))
            n += c
    return tot, n


def build(dirname, log=print):
    by = {s: CD.cached(dirname, s) for s in FIT + (SELECT, BLOCK)}
    base = base_tables(by)
    fit_games = [g for s in FIT for g in by[s]]
    coach, aux, call = collect(fit_games)
    sel_counts, _sa, sel_call = collect(by[SELECT])
    blk_counts, _ba, blk_call = collect(by[BLOCK])
    baseT = D.Tables(dict(base, kickoff={"2016-2023": base["kickoff"]["2016-2023"]},
                          kickoff_td={"2016-2023": base["kickoff_td"]["2016-2023"]}))
    # ---- selection on 2024: one mass at a time (they do not interact)
    sel = {"grid": GRID, "season": SELECT, "scores": {}}
    calls = {lam: fit_call(call, baseT, lam) for lam in GRID["lam"]}
    best = {}
    for name, vals in GRID.items():
        scores = {}
        for v in vals:
            hy = {"rate_n": 20.0, "hist_k": 40.0}
            cc = dict(coach, call=calls[v if name == "lam" else 8.0])
            if name != "lam":
                hy[name] = v
            T = C.Tables({"base": base, "coach": cc}, era="2024", hyper=hy)
            if name == "rate_n":
                tot, n = rates_logloss(sel_counts, T)
            elif name == "hist_k":
                tot, n = gaps_logloss(sel_counts, T)
            else:
                tot, n = call_logloss(sel_call, T)[0], 1
            scores[str(v)] = round(tot / n, 6)
        best[name] = float(min(scores, key=scores.get))
        sel["scores"][name] = scores
        log(f"[select {SELECT}] {name}: {scores} -> {best[name]}")
    coach["call"] = calls[best["lam"]]
    coach["hyper"] = {"rate_n": best["rate_n"], "hist_k": best["hist_k"], "lam": best["lam"]}
    raw = {"base": base, "coach": coach}
    # ---- held-out decision checks: 2024 (selected on) and 2025 (can only block)
    checks = {}
    for season, cnts, cl in ((SELECT, sel_counts, sel_call), (BLOCK, blk_counts, blk_call)):
        T = C.Tables(raw, era="2024" if season == 2024 else "2025+")
        a, b = call_logloss(cl, T)
        rt, rn = rates_logloss(cnts, T)
        gt, gn = gaps_logloss(cnts, T)
        checks[str(season)] = {"call_logloss_coach": round(a, 5), "call_logloss_base": round(b, 5),
                               "call_n": len(cl), "decision_logloss": round(rt / rn, 5), "decision_n": rn,
                               "gap_logloss": round(gt / gn, 5), "gap_n": gn}
        log(f"[check {season}] {checks[str(season)]}")
    kg = aux["knl_gap"].get("all", {})
    kn = sum(kg.values()) or 1
    raw["meta"] = {
        "version": C.VERSION, "fit_seasons": list(FIT), "select_season": SELECT, "block_season": BLOCK,
        "sealed": f"{CD.SEAL_SEASON} week {CD.SEAL_WEEK} on: never read here",
        "source": "nflverse-data play-by-play (CC BY 4.0)",
        "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "games": sum(1 for g in fit_games if g["st"] == 2), "call_rows": len(call),
        "selection": sel, "checks": checks,
        "measured": {
            "victory_formation_share": [aux["vf"][0], aux["vf"][1]],
            "kneels": aux["kneels"], "kneels_outside_window": aux["kneel_out"],
            "kneel_gap_mean": round(sum(int(k) * c for k, c in kg.items()) / kn, 2), "kneel_gap_n": kn,
            "onside_recovery": coach["onside_rec"], "onside_recovery_from": ONSIDE_FROM,
            "onside_ytg": coach["onside_ytg"], "onside_not_trailing_q4": aux["onside_surprise"],
            "timeouts_per_team_half": round(aux["timeouts"] / max(1, aux["team_halves"]), 3)}}
    return raw


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    out = OUT_DEFAULT
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    raw = build(argv[0])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(raw, fh, separators=(",", ":"), sort_keys=True)
    print(json.dumps(raw["meta"]["measured"], indent=1))
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
