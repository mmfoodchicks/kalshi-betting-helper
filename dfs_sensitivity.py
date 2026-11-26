"""Projection sensitivity: how a DFS board's lineups hold up when the
assumptions under them are wrong (2026-11-26; research/reports/dfs_sensitivity.md).

The owner: "Develop a framework for testing lineup robustness against uncertain
player usage, ownership and game scripts ... A lineup that performs well across
several plausible assumptions may be more robust than one that dominates a single
modeled scenario." Research only: nothing here changes a board, and nothing on
the server imports it (numpy, the engine and the large matrices are the PC's).

A SCENARIO is a small declarative dict -- a name and a list of ops -- and every
op acts on exactly one layer of the pipeline the board itself runs:

  layer      ops                         what changes                 cost
  ---------  --------------------------  ---------------------------  -------------------------
  lines      share, usage_noise,         Sleeper's lines the engine   the touched games are
             efficiency, script(att)     deals by (means AND recon)   re-simulated (per-game cache)
  level      script(d_pts)               the engine's points target   that game re-simulated
  worlds     points                      a player's simulated points  none (a transform)
  mask       condition                   which worlds count           none (a subset)
  field      field                       the opponents' lineups       the field re-scored

Conservation, and what is claimed where:
  * share / usage_noise move SHARES. Each team's line totals of targets and
    carries are unchanged in `means` and in `recon` (checked to 1e-9 and stamped
    in the receipt), the moved players' catch rate, yards per touch and
    touchdowns per touch unchanged (every carried stat moves by the same
    factor, as nfl_usage does). In the engine the team's volume is the GAME's
    (nfl_dfs_engine.deal_team splits the box's totals by these shares), so a
    share op leaves every world's box score -- team points, attempts, carries,
    yards -- untouched: the worlds are the same games with the ball dealt
    differently (the guards check this world for world through the real engine).
  * efficiency moves yards/TDs per touch only; the team's yards are still the
    box's, so teammates give up what he gains.
  * script d_pts moves a team's points TARGET by exactly d (the drive level by
    d / (1 - deep weight); nfl_dfs_engine.blend_targets); the anchor lands it
    within its own noise and the receipt shows target and landed. att_mult
    moves the passing-volume anchor (the team's QB lines).
  * points and the field are NOT conserved by design: `points` is a stress (a
    late scratch whose replacement is not modelled; a mean shift that nobody
    absorbs), and the field is the public's, not the game's.
  * Sleeper's `proj_pts` -- what the public read -- never moves: a scenario is
    the truth differing from the projection, so the field the board scored
    against is still the field.

The RUNNER (`Runner`) re-simulates each scenario's worlds through the production
path (`EngineWorlds`: nfl_dfs_sim.player_pool on the engine model, one game a
call, so pool_cache files each game's worlds under its own inputs and a
scenario that touches one game re-simulates one game), on a SEED OF ITS OWN
(`sens_seed`, never the board's unseeded worlds nor the evaluator's final
ones), the same seed for every scenario: common random numbers, so scenario
differences are paired world by world and their Monte Carlo error is the
paired error, usually far below either figure's own. It scores the lineups
with dfs_evaluate's machinery (the field's per-world rank thresholds,
`field_thresholds`, brute-force-checked; a lineup's bucket exactly as
`score_lineup` takes it) and keeps BLOCK SUMS (worlds in blocks of 1,000), so
every figure carries a delete-one-block jackknife SE and any two scenarios can
be differenced pairwise afterwards.

The METRICS (`summarize`): per lineup and scenario the probability of the top
0.1% / 1% / cash and the no-tie EV with SEs; the paired change against the base
scenario with its SE and z; rank stability of the lineup set (Spearman and
Kendall tau-b against the base, beside the split-half NOISE FLOOR -- the same
correlation between two halves of the base's own worlds); regret against each
scenario's best (in-sample, its paired SE, and cross-fitted on halves, which
the winner's curse cannot inflate); the share of scenarios a lineup stays in
the top N; the spread of each figure across scenarios; the worlds a scenario
needs for a stated precision.
"""
import contextlib
import copy
import hashlib
import json
import math
import random
import time
import zlib

import errlog

try:
    import numpy as np
except ImportError:                       # the server: sensitivity runs are the PC's
    np = None

VERSION = 1
PTS_PREFIX = "__team_pts__:"             # the captured team points ride in the pool as pseudo-players
BLOCK = 1000                             # worlds a jackknife block holds when a run names one
# Blocks a run's worlds are cut into when it does not: the jackknife SE has blocks - 1 degrees of freedom,
# and on the smoke run 3 blocks of 1,000 put 4 of 40 lineups beyond 3 SE on a pure reseed (independent
# worlds, where about 0.1 should be): an SE from 2 degrees of freedom is not one. 40 blocks of 1,000 at
# 40,000 worlds.
NBLOCKS = 40
SIM_KINDS = ("share", "usage_noise", "efficiency", "script")
OP_KINDS = SIM_KINDS + ("points", "condition", "field")
# a target or carry moves with the stats that hang off it (nfl_usage._COMPONENTS)
CARRIED = {"rec_tgt": ("rec_tgt", "rec", "rec_yd", "rec_td"), "rush_att": ("rush_att", "rush_yd", "rush_td")}
EFFICIENCY = {"rec": ("rec_yd", "rec_td"), "rush": ("rush_yd", "rush_td")}
PASSING = ("pass_att", "pass_cmp", "pass_yd", "pass_td", "int")
ROLES = {"QB1": ("QB", "pass_att", 0), "RB1": ("RB", "rush_att", 0), "RB2": ("RB", "rush_att", 1),
         "WR1": ("WR", "rec_tgt", 0), "WR2": ("WR", "rec_tgt", 1), "WR3": ("WR", "rec_tgt", 2),
         "TE1": ("TE", "rec_tgt", 0)}
POSITIONS = ("QB", "RB", "WR", "TE", "DST", "K")
CONDITION_STATS = ("margin_fav", "margin_home", "total", "team_pts")
FIELD_MODELS = ("board", "casual", "tilt", "sd_top_share")
TILT_TARGETS = ("concentrate", "noise", "scale", "casual")
# The field's ownership is measured off by about this much: the steered field's top-40 MAE against the real
# Millionaires, 5.14 / 5.57 / 6.27 points on weeks 1, 2, 6 (CLAUDE.md, "Ownership"). The ownership-error
# scenarios draw errors of that size, so "ownership differs substantially" means "as wrong as we have been".
OWN_MAE_MEASURED = 5.5
# The real classic fields' ownership concentration, sum of own^2 1.5-1.65 against the served field's 0.87
# (research/reports/dk_mech.md); the concentrated-field scenario takes the low end.
OWN_SUMSQ_REAL = 1.5
# Measured costs the runtime estimate uses (estimate()): the deep engine plays ~1.0 ms a game on one core
# (this sandbox, 500 games 0.52 s); the field's thresholds ran 102.5 s CPU / 13.1 s GPU for 20,000 worlds x
# 300,000 lineups on the owner's PC (research/gpu_bench.py, dfs_evaluate's notes).
GAME_MS = 1.0
THRESH_S_PER_CELL_CPU = 102.5 / (20000 * 300000)
THRESH_S_PER_CELL_GPU = 13.1 / (20000 * 300000)


# ---- 1. the scenario specification -----------------------------------------------------------
def _canon(obj):
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def _sha(obj):
    return hashlib.sha256(_canon(obj).encode()).hexdigest()[:16]


def _num(v, what, lo=None, hi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: {v!r} is not a number")
    if x != x or (lo is not None and x < lo) or (hi is not None and x > hi):
        raise ValueError(f"{what}: {x} outside [{lo}, {hi}]")
    return x


def _keys(d, allowed, what):
    bad = sorted(set(d) - set(allowed))
    if bad:
        raise ValueError(f"{what}: unknown key(s) {bad} (allowed: {sorted(allowed)})")


def _selector(sel, what):
    """{"names": [...]} / "team" / "teams" / "pos" / "role", ANDed. An empty
    selector selects nothing (never everyone by accident)."""
    if not isinstance(sel, dict) or not sel:
        raise ValueError(f"{what}: a selector needs at least one of names, team, teams, pos, role")
    _keys(sel, ("names", "team", "teams", "pos", "role"), what)
    out = {}
    if "names" in sel:
        out["names"] = sorted(str(n) for n in (sel["names"] or []))
        if not out["names"]:
            raise ValueError(f"{what}: names is empty")
    if "team" in sel:
        out["teams"] = [str(sel["team"]).upper()]
    if "teams" in sel:
        out["teams"] = sorted(set(out.get("teams", [])) | {str(t).upper() for t in sel["teams"]})
    if "pos" in sel:
        p = str(sel["pos"]).upper()
        if p not in POSITIONS:
            raise ValueError(f"{what}: pos {p!r} not in {POSITIONS}")
        out["pos"] = p
    if "role" in sel:
        r = str(sel["role"]).upper()
        if r not in ROLES:
            raise ValueError(f"{what}: role {r!r} not in {sorted(ROLES)}")
        out["role"] = r
    return out


def _norm_op(op, i):
    what = f"op {i}"
    if not isinstance(op, dict) or op.get("kind") not in OP_KINDS:
        raise ValueError(f"{what}: kind must be one of {OP_KINDS}, got {op!r}")
    k = op["kind"]
    if k == "share":
        _keys(op, ("kind", "comp", "select", "mult", "absorb"), what)
        if op.get("comp") not in CARRIED:
            raise ValueError(f"{what}: comp must be one of {sorted(CARRIED)}")
        absorb = op.get("absorb", "team")
        if absorb not in ("team", "position"):
            raise ValueError(f"{what}: absorb must be team or position")
        return {"kind": k, "comp": op["comp"], "select": _selector(op.get("select"), what),
                "mult": _num(op.get("mult"), f"{what} mult", 0.0, 10.0), "absorb": absorb}
    if k == "usage_noise":
        _keys(op, ("kind", "comps", "sd", "seed", "teams"), what)
        comps = sorted(op.get("comps") or ("rec_tgt", "rush_att"))
        if any(c not in CARRIED for c in comps):
            raise ValueError(f"{what}: comps must be among {sorted(CARRIED)}")
        return {"kind": k, "comps": comps, "sd": _num(op.get("sd", 0.10), f"{what} sd", 0.0, 2.0),
                "seed": int(op.get("seed") or 0),
                "teams": sorted(str(t).upper() for t in op["teams"]) if op.get("teams") else None}
    if k == "efficiency":
        _keys(op, ("kind", "comp", "select", "mult"), what)
        if op.get("comp") not in EFFICIENCY:
            raise ValueError(f"{what}: comp must be one of {sorted(EFFICIENCY)}")
        return {"kind": k, "comp": op["comp"], "select": _selector(op.get("select"), what),
                "mult": _num(op.get("mult"), f"{what} mult", 0.0, 10.0)}
    if k == "script":
        _keys(op, ("kind", "team", "d_pts", "att_mult"), what)
        if not op.get("team"):
            raise ValueError(f"{what}: script needs a team")
        return {"kind": k, "team": str(op["team"]).upper(),
                "d_pts": _num(op.get("d_pts", 0.0), f"{what} d_pts", -40.0, 40.0),
                "att_mult": _num(op.get("att_mult", 1.0), f"{what} att_mult", 0.3, 3.0)}
    if k == "points":
        _keys(op, ("kind", "select", "mult", "add"), what)
        sel = _selector(op.get("select"), what)
        if "role" in sel:
            raise ValueError(f"{what}: points acts on the board's players, which carry no role; name them")
        return {"kind": k, "select": sel, "mult": _num(op.get("mult", 1.0), f"{what} mult", 0.0, 10.0),
                "add": _num(op.get("add", 0.0), f"{what} add", -100.0, 100.0)}
    if k == "condition":
        _keys(op, ("kind", "game", "stat", "team", "lo", "hi"), what)
        if op.get("stat") not in CONDITION_STATS:
            raise ValueError(f"{what}: stat must be one of {CONDITION_STATS}")
        if op.get("lo") is None and op.get("hi") is None:
            raise ValueError(f"{what}: a condition needs lo or hi")
        if op["stat"] == "team_pts" and not op.get("team"):
            raise ValueError(f"{what}: team_pts needs a team")
        return {"kind": k, "game": str(op.get("game") or op.get("team") or "").upper(), "stat": op["stat"],
                "team": str(op["team"]).upper() if op.get("team") else None,
                "lo": None if op.get("lo") is None else _num(op["lo"], f"{what} lo"),
                "hi": None if op.get("hi") is None else _num(op["hi"], f"{what} hi")}
    # field
    _keys(op, ("kind", "model", "target", "sumsq", "mae", "sd", "seed", "select", "mult", "top_k", "top_share",
               "n", "cal_n"), what)
    model = op.get("model", "board")
    if model not in FIELD_MODELS:
        raise ValueError(f"{what}: field model must be one of {FIELD_MODELS}")
    out = {"kind": k, "model": model}
    if model == "tilt":
        tgt = op.get("target")
        if tgt not in TILT_TARGETS:
            raise ValueError(f"{what}: tilt target must be one of {TILT_TARGETS}")
        out["target"] = tgt
        if tgt == "concentrate":
            out["sumsq"] = _num(op.get("sumsq", OWN_SUMSQ_REAL), f"{what} sumsq", 0.01, 9.0)
        elif tgt == "noise":
            if op.get("sd") is not None:
                out["sd"] = _num(op["sd"], f"{what} sd", 0.0, 3.0)
            else:
                out["mae"] = _num(op.get("mae", OWN_MAE_MEASURED), f"{what} mae", 0.0, 50.0)
            out["seed"] = int(op.get("seed") or 0)
        elif tgt == "scale":
            out["mult"] = _num(op.get("mult", 1.5), f"{what} mult", 0.0, 10.0)
            if op.get("select"):
                out["select"] = _selector(op["select"], what)
            else:
                out["top_k"] = int(_num(op.get("top_k", 10), f"{what} top_k", 1, 500))
    elif model == "casual":
        out["n"] = int(op["n"]) if op.get("n") else None
        out["cal_n"] = int(op.get("cal_n") or 20000)
        out["seed"] = int(op.get("seed") or 0)
    elif model == "sd_top_share":
        out["top_share"] = _num(op.get("top_share"), f"{what} top_share", 1e-6, 0.5)
    return out


def normalize(spec):
    """The scenario, validated and filled in. Strict: an unknown op, key or
    selector raises, because a typo that silently did nothing would read as
    'this lineup is robust to that'."""
    if not isinstance(spec, dict):
        raise ValueError(f"a scenario is a dict, got {type(spec).__name__}")
    _keys(spec, ("name", "label", "ops", "reseed", "weight", "note"), "scenario")
    name = str(spec.get("name") or "").strip()
    if not name:
        raise ValueError("a scenario needs a name")
    ops = [_norm_op(o, i) for i, o in enumerate(spec.get("ops") or [])]
    if sum(1 for o in ops if o["kind"] == "field") > 1:
        raise ValueError(f"{name}: one field op per scenario")
    return {"name": name, "label": str(spec.get("label") or name), "ops": ops,
            "reseed": int(spec.get("reseed") or 0), "weight": _num(spec.get("weight", 1.0), "weight", 0.0),
            "note": spec.get("note")}


def sig(spec):
    """The whole scenario's identity (name and label excluded: two scenarios
    that do the same thing are the same scenario)."""
    s = normalize(spec)
    return _sha({"v": VERSION, "ops": s["ops"], "reseed": s["reseed"]})


def sim_ops(spec):
    return [o for o in normalize(spec)["ops"] if o["kind"] in SIM_KINDS]


def sim_sig(spec):
    """Scenarios with equal sim_sig read the very same worlds."""
    s = normalize(spec)
    return _sha({"v": VERSION, "sim": [o for o in s["ops"] if o["kind"] in SIM_KINDS], "reseed": s["reseed"]})


def points_sig(spec):
    return _sha([o for o in normalize(spec)["ops"] if o["kind"] == "points"])


def field_op(spec):
    return next((o for o in normalize(spec)["ops"] if o["kind"] == "field"), {"kind": "field", "model": "board"})


def field_sig(spec):
    return _sha(field_op(spec))


def needs_resim(spec):
    s = normalize(spec)
    return bool(sim_ops(s)) or s["reseed"] != 0


def scenario(name, ops=(), label=None, weight=1.0, reseed=0, note=None):
    return normalize({"name": name, "label": label or name, "ops": list(ops), "weight": weight,
                      "reseed": reseed, "note": note})


# ---- 2. the lines layer (pure Python: the transforms run anywhere) -----------------------------
def _norm_name(s):
    try:
        import nfl_adp
        return nfl_adp._norm(s)
    except Exception:                  # nfl_adp missing in a stripped checkout: case and spacing only
        return " ".join(str(s or "").lower().split())


def _dicts(p):
    return [p[k] for k in ("means", "recon") if isinstance(p.get(k), dict)]


def line_val(p, comp):
    """The value the engine deals by: the reconciled line where present, else
    the mean (nfl_dfs_csim._line)."""
    r = p.get("recon") or {}
    v = r.get(comp)
    if v is None:
        v = (p.get("means") or {}).get(comp)
    return max(0.0, float(v or 0.0))


def _teams_of(games):
    out = {}
    for g in games.values():
        for p in g.get("players") or []:
            out.setdefault(str(p.get("team") or "").upper(), []).append(p)
    return out


def _roles(members):
    """{id(player): role} for one team's players, on the engine's line."""
    out = {}
    for role, (pos, comp, k) in ROLES.items():
        cand = sorted((p for p in members if (p.get("pos") or "").upper() == pos and line_val(p, comp) > 0),
                      key=lambda p: (-line_val(p, comp), p.get("name") or ""))
        if len(cand) > k:
            out.setdefault(id(cand[k]), set()).add(role)
    return out


def resolve_names(names, people):
    """The ids of `people` a selector's names pick: each name by exact match
    where any player carries it exactly, else by the normalised form (a
    DraftKings name against Sleeper's: suffixes, punctuation, accents).
    Exact first because the normalised form also drops numerals and can merge
    two players -- on the guards' toy pool "WR10" normalised to "wr" and a
    points op zeroed every receiver on the slate."""
    exact = {}
    for p in people:
        exact.setdefault(str(p.get("name")), []).append(id(p))
    norm = None
    out = set()
    for n in names or ():
        if str(n) in exact:
            out.update(exact[str(n)])
            continue
        if norm is None:
            norm = {}
            for p in people:
                norm.setdefault(_norm_name(p.get("name")), []).append(id(p))
        out.update(norm.get(_norm_name(n), []))
    return out


def _match(p, sel, roles, name_ids=None):
    if "teams" in sel and str(p.get("team") or "").upper() not in sel["teams"]:
        return False
    if "pos" in sel and (p.get("pos") or "").upper() != sel["pos"]:
        return False
    if "role" in sel and sel["role"] not in roles.get(id(p), ()):
        return False
    if "names" in sel and id(p) not in (name_ids or ()):
        return False
    return True


def _scale(p, keys, f):
    for d in _dicts(p):
        for k in keys:
            if k in d and d[k] is not None:
                d[k] = float(d[k]) * f


def _totals(members, comp):
    out = {}
    for name in ("means", "recon"):
        vals = [float((p.get(name) or {}).get(comp) or 0.0) for p in members if isinstance(p.get(name), dict)]
        if vals:
            out[name] = sum(vals)
    return out


def _share_team(members, op, roles, name_ids=None):
    comp, keys, m = op["comp"], CARRIED[op["comp"]], op["mult"]
    elig = [p for p in members if line_val(p, comp) > 0]
    S = [p for p in elig if _match(p, op["select"], roles, name_ids)]
    if not S:
        return None
    sel_ids = {id(p) for p in S}
    rest = [p for p in elig if id(p) not in sel_ids]
    R = rest
    note = None
    if op["absorb"] == "position":
        pos = {(p.get("pos") or "").upper() for p in S}
        R = [p for p in rest if (p.get("pos") or "").upper() in pos]
        if not R:
            R, note = rest, "no teammate at the position: the whole team absorbs"
    before = _totals(members, comp)
    tot_line = sum(line_val(p, comp) for p in elig)
    share_before = sum(line_val(p, comp) for p in S) / tot_line if tot_line > 0 else 0.0
    applied = m
    if m != 1.0 and not R:
        return {"selected": sorted(p.get("name") for p in S), "skipped": "no teammate to absorb the change: "
                "the team total could not be kept, so nothing moved", "conserved": True,
                "share_before": round(share_before, 6), "share_after": round(share_before, 6)}
    for name in ("means", "recon"):
        hold = [p for p in members if isinstance(p.get(name), dict)]
        if not hold:
            continue
        v = {id(p): float(p[name].get(comp) or 0.0) for p in hold}
        s_sum = sum(v.get(id(p), 0.0) for p in S if id(p) in v)
        r_sum = sum(v.get(id(p), 0.0) for p in R if id(p) in v)
        mm = m
        if r_sum > 0 and s_sum > 0 and r_sum + (1.0 - mm) * s_sum < 0:
            mm = 1.0 + r_sum / s_sum             # the most the teammates can give: every one of them to zero
            applied = min(applied, mm)
            note = (note + "; " if note else "") + f"mult clamped to {mm:.4f} in {name} (teammates exhausted)"
        r_fac = 1.0 + (1.0 - mm) * s_sum / r_sum if r_sum > 0 else 1.0
        for p in S:
            if id(p) in v:
                for k in keys:
                    if k in p[name] and p[name][k] is not None:
                        p[name][k] = float(p[name][k]) * mm
        if r_sum > 0:
            for p in R:
                if id(p) in v:
                    for k in keys:
                        if k in p[name] and p[name][k] is not None:
                            p[name][k] = float(p[name][k]) * r_fac
    after = _totals(members, comp)
    tot_after = sum(line_val(p, comp) for p in elig)
    rec = {"selected": sorted(p.get("name") for p in S), "absorbed_by": sorted(p.get("name") for p in R),
           "mult": op["mult"], "mult_applied": round(applied, 6),
           "share_before": round(share_before, 6),
           "share_after": round(sum(line_val(p, comp) for p in S) / tot_after if tot_after > 0 else 0.0, 6),
           "totals_before": {k: round(v, 6) for k, v in before.items()},
           "totals_after": {k: round(v, 6) for k, v in after.items()},
           "conserved": all(abs(after.get(k, 0.0) - v) <= 1e-9 * max(1.0, abs(v)) for k, v in before.items())}
    if note:
        rec["note"] = note
    return rec


def _noise_team(team, members, op):
    recs = {}
    for comp in op["comps"]:
        elig = [p for p in members if line_val(p, comp) > 0]
        if len(elig) < 2:
            continue
        sd = op["sd"]
        z = {}
        for p in elig:
            # one draw per (seed, team, component, player), whatever order the players come in
            r = random.Random(f"vigil-sens-noise|{op['seed']}|{team}|{comp}|{_norm_name(p.get('name'))}")
            z[id(p)] = math.exp(r.gauss(0.0, sd) - 0.5 * sd * sd)
        before = _totals(members, comp)
        for name in ("means", "recon"):
            hold = [p for p in elig if isinstance(p.get(name), dict)]
            v = {id(p): float(p[name].get(comp) or 0.0) for p in hold}
            T = sum(v.values())
            Tz = sum(v[i] * z[i] for i in v)
            if T <= 0 or Tz <= 0:
                continue
            for p in hold:
                f = z[id(p)] * T / Tz
                for k in CARRIED[comp]:
                    if k in p[name] and p[name][k] is not None:
                        p[name][k] = float(p[name][k]) * f
        after = _totals(members, comp)
        recs[comp] = {"players": len(elig), "log_factor_sd": round(float(_sd([math.log(z[id(p)]) for p in elig])), 4),
                      "conserved": all(abs(after.get(k, 0.0) - v) <= 1e-9 * max(1.0, abs(v)) for k, v in before.items())}
    return recs


def _sd(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def apply_games(games, ops):
    """(a deep copy of `games` with every lines-layer op applied in order,
    the receipts). `games` is weekly_games' shape; `proj_pts` is never touched."""
    g2 = copy.deepcopy(games)
    by_team = _teams_of(g2)
    everyone = [p for ms in by_team.values() for p in ms]
    receipts = []
    for i, op in enumerate(ops):
        k = op["kind"]
        if k not in SIM_KINDS:
            continue
        rec = {"op": i, "kind": k, "teams": {}}
        name_ids = resolve_names(op["select"].get("names"), everyone) if "select" in op else None
        if k == "share":
            for team, members in sorted(by_team.items()):
                r = _share_team(members, op, _roles(members), name_ids)
                if r is not None:
                    rec["teams"][team] = r
            if not rec["teams"]:
                rec["note"] = "the selector matched no player with a line in this component: nothing moved"
        elif k == "usage_noise":
            for team, members in sorted(by_team.items()):
                if op["teams"] and team not in op["teams"]:
                    continue
                rec["teams"][team] = _noise_team(team, members, op)
        elif k == "efficiency":
            for team, members in sorted(by_team.items()):
                roles = _roles(members)
                hit = [p for p in members if _match(p, op["select"], roles, name_ids)]
                for p in hit:
                    _scale(p, EFFICIENCY[op["comp"]], op["mult"])
                if hit:
                    rec["teams"][team] = {"selected": sorted(p.get("name") for p in hit), "mult": op["mult"],
                                          "note": "yards and TDs per touch; the team's yards stay the box's"}
        elif k == "script" and op["att_mult"] != 1.0:
            qbs = [p for p in by_team.get(op["team"], []) if (p.get("pos") or "").upper() == "QB"]
            for p in qbs:
                _scale(p, PASSING, op["att_mult"])
            rec["teams"][op["team"]] = {"qbs": sorted(p.get("name") for p in qbs), "att_mult": op["att_mult"]}
        receipts.append(rec)
    return g2, receipts


def apply_ctx(ctx, ops):
    """(the engine's week context with every script op's points shift on its
    drive level, the receipts). The blended target is (1 - w) drive + w deep,
    so moving drive by d / (1 - w) moves the target by exactly d."""
    ctx2 = dict(ctx or {})
    drive = dict((ctx or {}).get("drive") or {})
    w = (ctx or {}).get("weight")
    if w is None:
        try:
            import nfl_dfs_engine
            w = nfl_dfs_engine.DEEP_WEIGHT
        except Exception:
            w = 0.30
    w = float(w)
    recs = []
    for op in ops:
        if op["kind"] != "script" or op["d_pts"] == 0.0:
            continue
        t = op["team"]
        key = next((k for k in drive if str(k).upper() == t), None)
        if key is None or w >= 1.0:
            recs.append({"team": t, "d_pts": op["d_pts"], "skipped": "no drive level for this team (the engine's "
                         "own level stands alone, so its target cannot be moved this way)"})
            continue
        old = float(drive[key])
        drive[key] = old + op["d_pts"] / (1.0 - w)
        recs.append({"team": t, "d_pts": op["d_pts"], "drive_before": round(old, 4),
                     "drive_after": round(drive[key], 4), "deep_weight": w})
    ctx2["drive"] = drive
    return ctx2, recs


# ---- 3. the worlds layer: points ops and conditions -----------------------------------------
def select_players(players, sel):
    """Indices of the board's players (artifact order) a selector picks."""
    out = []
    name_ids = resolve_names(sel.get("names"), players) if sel.get("names") else None
    for i, p in enumerate(players):
        if "teams" in sel and str(p.get("team") or "").upper() not in sel["teams"]:
            continue
        if "pos" in sel and (p.get("pos") or "").upper() != sel["pos"]:
            continue
        if name_ids is not None and id(p) not in name_ids:
            continue
        out.append(i)
    return out


def apply_points(X, players, ops):
    """(X with every points op applied to the selected rows, receipts). A copy
    when anything moves; X itself otherwise."""
    pts = [o for o in ops if o["kind"] == "points"]
    if not pts:
        return X, []
    X2 = np.array(X, dtype=np.float32, copy=True)
    recs = []
    for op in pts:
        idx = select_players(players, op["select"])
        if idx:
            X2[idx] = X2[idx] * np.float32(op["mult"]) + np.float32(op["add"])
        recs.append({"players": [players[i]["name"] for i in idx], "mult": op["mult"], "add": op["add"],
                     "conserved": False, "note": "a stress: nobody absorbs the change" if idx else "matched nobody"})
    return X2, recs


def _game_pair(games, key):
    """(team a, team b, home or None) of the game a condition names (a team
    abbreviation in it, or its label)."""
    key = str(key or "").upper()
    for g in games.values():
        teams = [str(t).upper() for t in (g.get("teams") or [])[:2]]
        if key in teams or key == str(g.get("label") or "").upper():
            if len(teams) == 2:
                return teams[0], teams[1], (str(g["home"]).upper() if g.get("home") else None)
    return None


def world_mask(team_pts, games, ops, W):
    """(bool mask over the W worlds, receipts): the worlds every condition
    holds in. Team points are the captured box scores (EngineWorlds)."""
    mask = np.ones(W, dtype=bool)
    recs = []
    for op in (o for o in ops if o["kind"] == "condition"):
        pair = _game_pair(games, op["game"] or op["team"])
        if not pair or pair[0] not in team_pts or pair[1] not in team_pts:
            recs.append({"op": op, "skipped": "no captured team points for this game: the condition cannot be read",
                         "kept_share": None})
            mask[:] = False
            continue
        a, b, home = pair
        pa, pb = np.asarray(team_pts[a][:W], dtype=np.float64), np.asarray(team_pts[b][:W], dtype=np.float64)
        st = op["stat"]
        if st == "margin_fav":
            fav, dog = (a, b) if pa.mean() >= pb.mean() else (b, a)
            x = (pa - pb) if fav == a else (pb - pa)
            what = f"{fav} minus {dog} (the favourite by these worlds' mean points)"
        elif st == "margin_home":
            h = home or a
            x = (pa - pb) if h == a else (pb - pa)
            what = f"{h} minus the other (home)" if home else f"{a} minus {b} (no home side known)"
        elif st == "total":
            x, what = pa + pb, f"{a} + {b}"
        else:
            t = op["team"]
            if t not in (a, b):
                recs.append({"op": op, "skipped": f"{t} is not in {a}/{b}", "kept_share": None})
                mask[:] = False
                continue
            x, what = (pa if t == a else pb), f"{t} points"
        m = np.ones(W, dtype=bool)
        if op["lo"] is not None:
            m &= x >= op["lo"]
        if op["hi"] is not None:
            m &= x <= op["hi"]
        mask &= m
        recs.append({"stat": st, "of": what, "lo": op["lo"], "hi": op["hi"], "mean": round(float(x.mean()), 3),
                     "kept_share": round(float(m.mean()), 5)})
    return mask, recs


# ---- 4. the field layer --------------------------------------------------------------------
def columns(idx, P, kind):
    """(rows, k) ownership column of every slot: the player for classic; for
    showdown the captain in [0, P) and each flex in [P, 2P)."""
    idx = np.asarray(idx, dtype=np.int64)
    if kind == "showdown":
        c = idx.copy()
        c[:, 1:] += P
        return c, 2 * P
    return idx, P


def ownership(cols, w, C):
    """Each column's share of the (weighted) field."""
    k = cols.shape[1]
    return np.bincount(cols.ravel(), weights=np.repeat(np.asarray(w, dtype=np.float64), k), minlength=C)


def groups(players, kind):
    """The column groups whose totals a tilt keeps: positions (classic -- one
    QB and one DST a lineup, the FLEX moving RB/WR/TE between them only as the
    board's field does), captain and flex (showdown)."""
    P = len(players)
    if kind == "showdown":
        return [np.arange(P), np.arange(P, 2 * P)]
    pos = np.asarray([(p.get("pos") or "").upper() for p in players])
    return [np.where(pos == q)[0] for q in sorted(set(pos.tolist()))]


def _renorm(t, own, grp):
    """Each group's targets rescaled to the board's group total, capped at 1
    (a player is in a lineup at most once a slot class), the excess handed to
    the uncapped in proportion."""
    t = np.maximum(np.asarray(t, dtype=np.float64), 0.0).copy()
    for g in grp:
        tot = float(own[g].sum())
        if tot <= 0 or float(t[g].sum()) <= 0:
            t[g] = own[g]
            continue
        x = t[g] * tot / float(t[g].sum())
        for _ in range(50):
            over = x > 1.0
            if not over.any():
                break
            excess = float((x[over] - 1.0).sum())
            x[over] = 1.0
            free = ~over & (x > 0)
            if not free.any():
                break
            x[free] += excess * x[free] / float(x[free].sum())
        t[g] = x
    return t


def targets_concentrate(own, grp, sumsq):
    """own^gamma within each group (its total kept), gamma bisected so the
    sum of squared ownership lands on `sumsq`. The board's order is kept."""
    own = np.asarray(own, dtype=np.float64)
    pos = own > 0

    def f(gm):
        t = np.zeros_like(own)
        t[pos] = own[pos] ** gm
        t = _renorm(t, own, grp)
        return t, float((t ** 2).sum())
    lo, hi = 0.05, 12.0
    if f(lo)[1] > sumsq or f(hi)[1] < sumsq:
        g = lo if f(lo)[1] > sumsq else hi
        return f(g)[0], {"gamma": g, "reached": False, "sumsq": round(f(g)[1], 4)}
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if f(mid)[1] < sumsq:
            lo = mid
        else:
            hi = mid
    t, s = f(0.5 * (lo + hi))
    return t, {"gamma": round(0.5 * (lo + hi), 5), "reached": True, "sumsq": round(s, 5)}


def _top40_mae(own, t):
    top = np.argsort(-own)[:40]
    return float(np.abs(100.0 * (t[top] - own[top])).mean())


def targets_noise(own, grp, seed, sd=None, mae=None):
    """Ownership times a mean-one lognormal error, renormalised per group.
    With `mae` the error's SD is bisected so the 40 most-owned columns move by
    that many points on average (the measured size of our ownership error)."""
    own = np.asarray(own, dtype=np.float64)
    z = np.random.default_rng(zlib.crc32(f"vigil-sens-own|{int(seed)}".encode())).normal(0.0, 1.0, len(own))

    def f(s):
        return _renorm(own * np.exp(s * z - 0.5 * s * s), own, grp)
    if sd is None:
        lo, hi = 0.0, 3.0
        for _ in range(50):
            mid = 0.5 * (lo + hi)
            if _top40_mae(own, f(mid)) < mae:
                lo = mid
            else:
                hi = mid
        sd = 0.5 * (lo + hi)
    t = f(sd)
    return t, {"sd": round(float(sd), 5), "top40_mae_pts": round(_top40_mae(own, t), 3)}


def targets_scale(own, grp, mult, cols_sel):
    t = np.asarray(own, dtype=np.float64).copy()
    t[cols_sel] *= mult
    return _renorm(t, own, grp), {"columns": int(len(cols_sel)), "mult": mult}


def tilt(cols, w0, C, targets, iters=400, damp=0.5, tol=2e-4):
    """Field weights w = w0 x exp(sum of the row's column multipliers), the
    multipliers moved by damp x log(target / achieved) until every column's
    ownership is within `tol` of its target (the maximum-entropy reweighting:
    the field's structure -- stacks, salary, the sampler's rules -- kept, its
    ownership moved). Returns (w, receipt with the achieved error and the
    Kish effective sample size)."""
    w0 = np.asarray(w0, dtype=np.float64)
    w0 = w0 / w0.sum()
    t = np.asarray(targets, dtype=np.float64)
    lam = np.zeros(C)
    lw0 = np.log(np.maximum(w0, 1e-300))
    eps = 1e-9
    w = w0
    err = float("inf")
    it = 0
    for it in range(1, int(iters) + 1):
        lw = lw0 + lam[cols].sum(axis=1)
        lw -= lw.max()
        w = np.exp(lw)
        w /= w.sum()
        a = ownership(cols, w, C)
        err = float(np.abs(a - t).max())
        if err < tol:
            break
        live = (t > 0) | (a > 0)
        lam[live] += damp * (np.log(t[live] + eps) - np.log(a[live] + eps))
    a = ownership(cols, w, C)
    ess = float(1.0 / (w ** 2).sum())
    return w, {"iters": it, "max_abs_err": round(float(np.abs(a - t).max()), 6),
               "mae_pts": round(100.0 * float(np.abs(a - t).mean()), 5), "converged": bool(err < tol),
               "ess": round(ess, 1), "ess_share": round(ess / len(w), 5),
               "sumsq_before": round(float((ownership(cols, w0, C) ** 2).sum()), 5),
               "sumsq_after": round(float((a ** 2).sum()), 5)}


def casual_field(players, n, seed, cal_n=20000, workers=1):
    """The calibrated softmax field the board's optimizer field replaced
    (dfs_tourney.FIELD_MODEL "softmax"): calibrate_field and the FieldSampler
    on the board's own players, drawn afresh. Classic only."""
    import dfs_tourney as DT
    pl = [dict(p) for p in players]
    rng = np.random.default_rng(zlib.crc32(f"vigil-sens-casual|{int(seed)}".encode()))
    draw = DT.FieldSampler(pl, int(workers or 1))
    try:
        beta, kappa, max_own, mean_sal, coll, top_share = DT.calibrate_field(pl, rng, n=int(cal_n), sample=draw)
        rep = {}
        idx = draw(int(n), rng, beta, kappa, report=rep)
    finally:
        draw.close()
    return np.asarray(idx, dtype=np.int64), {"beta": round(float(beta), 5), "kappa": round(float(kappa), 5),
                                             "achieved": DT.field_stats(idx, pl), "completion": rep}


def casual_by_name(board):
    """{name: casual_pct} off a classic board's players (the calibrated field's
    own share, stamped beside field_pct since ENGINE 10)."""
    return {p.get("name"): p.get("casual_pct") for p in ((board or {}).get("players") or [])
            if p.get("casual_pct") is not None}


def field_for(op, base_idx, base_w, players, kind, board=None, workers=1):
    """(idx, w, receipt) of the field a scenario scores against."""
    P = len(players)
    w0 = np.asarray(base_w, dtype=np.float64) if base_w is not None else np.full(len(base_idx), 1.0 / len(base_idx))
    w0 = w0 / w0.sum()
    model = op.get("model", "board")
    if model == "board":
        return base_idx, w0, {"model": "board", "rows": int(len(base_idx))}
    if model == "casual":
        if kind != "classic":
            raise ValueError("the casual redraw is classic's sampler; a showdown field is a weighting (sd_top_share)")
        n = int(op.get("n") or len(base_idx))
        idx, rep = casual_field(players, n, op.get("seed") or 0, cal_n=op.get("cal_n") or 20000, workers=workers)
        return idx, np.full(len(idx), 1.0 / len(idx)), dict(rep, model="casual", rows=int(len(idx)))
    if model == "sd_top_share":
        if kind != "showdown":
            raise ValueError("sd_top_share re-weights a showdown universe")
        import dfs_tourney as DT
        f, beta = DT.field_weights(players, np.asarray(base_idx), top_share=op["top_share"])
        return base_idx, f, {"model": "sd_top_share", "top_share": op["top_share"], "beta": round(beta, 5),
                             "pair_collision": float((f ** 2).sum()), "pair_collision_board": float((w0 ** 2).sum())}
    # tilt
    cols, C = columns(base_idx, P, kind)
    own = ownership(cols, w0, C)
    grp = groups(players, kind)
    tgt = op["target"]
    if tgt == "concentrate":
        t, trec = targets_concentrate(own, grp, op["sumsq"])
    elif tgt == "noise":
        t, trec = targets_noise(own, grp, op.get("seed") or 0, sd=op.get("sd"), mae=op.get("mae"))
    elif tgt == "scale":
        if op.get("select"):
            sel = select_players(players, op["select"])
            sel = sel + [i + P for i in sel] if kind == "showdown" else sel
        else:
            sel = np.argsort(-own)[:int(op["top_k"])].tolist()
        t, trec = targets_scale(own, grp, op["mult"], np.asarray(sel, dtype=np.int64))
    else:                                   # casual: the board's own stamp of the calibrated field's shares
        cp = casual_by_name(board)
        vals = [cp.get(p["name"]) for p in players]
        if kind != "classic" or any(v is None for v in vals):
            raise ValueError("tilt to casual needs the board's casual_pct for every player (classic boards since ENGINE 10)")
        t = _renorm(np.asarray(vals, dtype=np.float64) / 100.0, own, grp)
        trec = {"source": "board casual_pct"}
    w, rec = tilt(cols, w0, C, t)
    return base_idx, w, dict(rec, model="tilt", target=tgt, **trec)


# ---- 5. scoring (dfs_evaluate's machinery) ------------------------------------------------------
def resolve_lineups(meta, lineups):
    """[(label, source, idx, captain index or None)] for the legal lineups,
    and the problems of the rest (DraftKings' rules, dfs_evaluate.check_lineup)."""
    import dfs_evaluate as DE
    at = {p["name"]: i for i, p in enumerate(meta["players"])}
    ok, bad = [], []
    for lu in lineups:
        names = [str(n) for n in lu.get("names") or []]
        idx, probs, _notes, _sal = DE.check_lineup(meta, names, lu.get("captain"))
        if probs:
            bad.append({"label": lu.get("label"), "problems": probs})
            continue
        cap = at.get(lu.get("captain")) if meta["kind"] == "showdown" else None
        ok.append({"label": lu.get("label") or "lineup", "source": lu.get("source") or "custom",
                   "sources": lu.get("sources") or [lu.get("source") or "custom"], "names": names,
                   "captain": lu.get("captain"), "idx": [int(i) for i in idx], "cap": cap})
    return ok, bad


def lineup_buckets(X, lus):
    """(L, W) int64 buckets of every lineup's score in every world, exactly
    as dfs_evaluate.score_lineup takes one (float32 sum, a captain's extra
    half, the board's 0.01-point rounding)."""
    import dfs_tourney as DT
    B = np.empty((len(lus), X.shape[1]), dtype=np.int64)
    S = np.empty((len(lus), X.shape[1]), dtype=np.float32)
    for j, lu in enumerate(lus):
        s = X[lu["idx"]].sum(axis=0, dtype=np.float32)
        if lu.get("cap") is not None:
            s = s + np.float32(0.5) * X[lu["cap"]]
        S[j] = s
        B[j] = np.clip(np.floor(s.astype(np.float64) * DT._RES + 0.5), 0, DT._NB - 1).astype(np.int64)
    return B, S


def contest_hits(B, T, qs, contest):
    """{metric: (L, W)} per world for one contest, every figure the
    evaluator's: the top 0.1% / 1%, cash, paid more than the fee, first
    (experimental) and the tier's prize (no tie split -- experimental)."""
    import dfs_evaluate as DE
    qi = {round(float(q), 12): i for i, q in enumerate(qs)}

    def col(q):
        return T[:, qi[round(float(q), 12)]][None, :]
    C = max(2, int(contest["capacity"]))
    fee = float(contest["entry_fee"])
    tiers = DE.tier_fractions(contest)
    cash_q = (max(1, int(contest["places_paid"])) - 1) / (C - 1)
    money_q = max((q for q, p in tiers if p > fee), default=None)
    out = {"top01": B >= col(0.001), "top1": B >= col(0.01), "cash": B >= col(cash_q), "first": B >= col(0.0)}
    if money_q is not None:
        out["money"] = B >= col(money_q)
    prize = np.zeros(B.shape, dtype=np.float64)
    got = np.zeros(B.shape, dtype=bool)
    for q, p in tiers:
        hit = (~got) & (B >= col(q))
        prize[hit] = p
        got |= hit
    out["prize"] = prize
    return out


def block_edges(W, block=BLOCK):
    return np.arange(0, W, max(1, int(block)))


def block_sums(A, mask, edges):
    """(L, blocks) sums of A over the masked worlds of each block."""
    Am = np.where(mask[None, :], np.asarray(A, dtype=np.float64), 0.0)
    return np.add.reduceat(Am, edges, axis=1)


def field_T(X, idx, w, P, kind, qs):
    """The field's per-world threshold buckets (dfs_evaluate.field_thresholds:
    the CPU, or the PC's GPU verified on its first chunk)."""
    import dfs_evaluate as DE
    import dfs_tourney as DT
    Wf = DT.lineup_matrix(np.asarray(idx), P, cpt_mult=1.5 if kind == "showdown" else None)
    WfT = np.ascontiguousarray(Wf.T)
    del Wf
    chunk = max(16, int(DT._CHUNK_ELEMS // max(1, len(idx))))
    T, device = DE.field_thresholds(X, WfT, np.asarray(w, dtype=np.float64), np.asarray(qs, dtype=np.float64), chunk)
    return T, device


# ---- 6. statistics ---------------------------------------------------------------------------
def _loo(sums, counts):
    """Delete-one-block means (..., blocks)."""
    tot = sums.sum(axis=-1, keepdims=True)
    den = counts.sum() - counts
    with np.errstate(invalid="ignore", divide="ignore"):
        return (tot - sums) / np.where(den > 0, den, np.nan)


def _jk_se(loo):
    nb = loo.shape[-1]
    if nb < 2:
        return np.full(loo.shape[:-1], np.nan)
    m = np.nanmean(loo, axis=-1, keepdims=True)
    return np.sqrt((nb - 1) / nb * np.nansum((loo - m) ** 2, axis=-1))


def mean_se(sums, counts):
    """(mean, jackknife SE) over blocks of (..., blocks) sums with (blocks,) counts."""
    c = float(counts.sum())
    mean = sums.sum(axis=-1) / c if c > 0 else np.full(sums.shape[:-1], np.nan)
    return mean, _jk_se(_loo(sums, counts))


def diff_se(sa, ca, sb, cb):
    """(mean a - mean b, its paired jackknife SE): both delete the same block,
    so worlds shared by the two (common random numbers, or the very same
    worlds under a field or points change) cancel."""
    ma = sa.sum(axis=-1) / float(ca.sum())
    mb = sb.sum(axis=-1) / float(cb.sum())
    return ma - mb, _jk_se(_loo(sa, ca) - _loo(sb, cb))


def rank_avg(a):
    """Ascending ranks, ties averaged."""
    a = np.asarray(a, dtype=np.float64)
    order = np.argsort(a, kind="mergesort")
    r = np.empty(len(a))
    r[order] = np.arange(1, len(a) + 1)
    _v, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    return np.bincount(inv, weights=r)[inv] / cnt[inv]


def spearman(a, b):
    ra, rb = rank_avg(a), rank_avg(b)
    sa, sb = ra.std(), rb.std()
    if len(ra) < 3 or sa == 0 or sb == 0:
        return float("nan")
    return float(((ra - ra.mean()) * (rb - rb.mean())).mean() / (sa * sb))


def kendall(a, b):
    """Kendall's tau-b (ties in either ordering corrected)."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    n = len(a)
    if n < 3:
        return float("nan")
    iu = np.triu_indices(n, 1)
    da = np.sign(a[:, None] - a[None, :])[iu]
    db = np.sign(b[:, None] - b[None, :])[iu]
    n1, n2 = float((da != 0).sum()), float((db != 0).sum())
    if n1 == 0 or n2 == 0:
        return float("nan")
    return float((da * db).sum() / math.sqrt(n1 * n2))


def worlds_needed(var_world, se_target):
    """Worlds for a mean with per-world variance `var_world` to reach SE
    `se_target`: var / se^2 (for a probability p, var = p(1 - p))."""
    return float(var_world) / float(se_target) ** 2 if se_target > 0 else float("inf")


def _halves(nb, k, seed=0):
    """k random partitions of the blocks into two halves (the first the even
    blocks, the fixed split)."""
    out = [(np.arange(0, nb, 2), np.arange(1, nb, 2))]
    rng = np.random.default_rng(seed)
    for _ in range(max(0, k - 1)):
        p = rng.permutation(nb)
        out.append((np.sort(p[: nb // 2]), np.sort(p[nb // 2:])))
    return out


def _half_mean(sums, counts, sel):
    c = float(counts[sel].sum())
    return sums[..., sel].sum(axis=-1) / c if c > 0 else np.full(sums.shape[:-1], np.nan)


def summarize(raw, base="base", contest=0, top_n=10, metrics=("top1", "cash", "top01", "ev"), halves=16):
    """The robustness figures, from a run's block sums (Runner.run's `raw`)."""
    names = [s["name"] for s in raw["scenarios"]]
    if base not in names:
        raise ValueError(f"no base scenario {base!r}")
    b0 = names.index(base)
    wts = np.asarray([float(s.get("weight", 1.0)) for s in raw["scenarios"]])
    counts = np.asarray(raw["counts"], dtype=np.float64)                 # (S, nb)
    S, nb = counts.shape
    L = len(raw["lineups"])
    con = raw["contests"][contest]
    fee = float(con["entry_fee"])
    empty = [n for i, n in enumerate(names) if counts[i].sum() <= 0]
    if base in empty:
        raise ValueError("the base scenario kept no worlds")
    out = {"contest": {"name": con.get("name"), "entry_fee": fee, "capacity": con.get("capacity")},
           "base": base, "top_n": int(top_n), "blocks": int(nb), "block_worlds": int(raw.get("block") or BLOCK),
           "worlds": {n: int(counts[i].sum()) for i, n in enumerate(names)}, "metrics": {},
           "empty_scenarios": empty}
    keep = [i for i, n in enumerate(names) if n not in empty]
    names = [names[i] for i in keep]
    wts = wts[keep]
    counts = counts[keep]
    S = len(names)
    b0 = names.index(base)
    parts = _halves(nb, halves) if nb >= 4 else []
    for m in metrics:
        key = f"{contest}:{'prize' if m == 'ev' else m}"
        if key not in raw["sums"]:
            continue
        sums = np.asarray(raw["sums"][key], dtype=np.float64)[keep]        # (S, L, nb)
        shift = -fee if m == "ev" else 0.0
        mean = np.zeros((S, L))
        se = np.zeros((S, L))
        for s in range(S):
            mean[s], se[s] = mean_se(sums[s], counts[s])
        mean += shift
        by_s = {}
        regret = np.zeros((S, L))
        regret_se = np.zeros((S, L))
        ranks = np.zeros((S, L))
        for s in range(S):
            d, sd_ = diff_se(sums[s], counts[s], sums[b0], counts[b0])
            z = np.where(sd_ > 0, d / np.where(sd_ > 0, sd_, 1.0), 0.0)
            best = int(np.nanargmax(mean[s]))
            rg, rg_se = diff_se(np.repeat(sums[s][best][None, :], L, axis=0), counts[s], sums[s], counts[s])
            regret[s], regret_se[s] = rg, rg_se
            ranks[s] = L + 1 - rank_avg(mean[s])                           # 1 = best
            # cross-fitted regret: the best chosen on one half, measured on the other (no winner's curse)
            xf = []
            for h1, h2 in (parts[:1] + [(parts[0][1], parts[0][0])] if parts else []):
                m1, m2 = _half_mean(sums[s], counts[s], h1), _half_mean(sums[s], counts[s], h2)
                if np.isfinite(m1).any() and np.isfinite(m2).any():
                    xf.append(m2[int(np.nanargmax(m1))] - m2)
            nf, cx = [], []
            for h1, h2 in parts:
                b1, b2 = _half_mean(sums[b0], counts[b0], h1), _half_mean(sums[b0], counts[b0], h2)
                s1, s2 = _half_mean(sums[s], counts[s], h1), _half_mean(sums[s], counts[s], h2)
                nf.append(spearman(b1, b2))
                cx.append(0.5 * (spearman(b1, s2) + spearman(b2, s1)))
            W_eff = float(counts[s].sum())
            var_d = (sd_ ** 2) * W_eff
            with np.errstate(divide="ignore", invalid="ignore"):
                detect = np.where(np.abs(d) > 0, 4.0 * var_d / np.where(np.abs(d) > 0, d ** 2, 1.0), np.inf)
            fin = detect[np.isfinite(detect) & (np.abs(z) > 0)]
            by_s[names[s]] = {
                "mean": _r(mean[s]), "se": _r(se[s]), "d": _r(d), "se_d": _r(sd_), "z": _r(z, 2),
                "n_z2": int((np.abs(z) > 2).sum()), "n_z3": int((np.abs(z) > 3).sum()),
                "mean_z2": _r1(float(np.mean(z ** 2))),
                "spearman": _r1(spearman(mean[b0], mean[s])), "kendall": _r1(kendall(mean[b0], mean[s])),
                "split_noise_floor": _r1(_nanmean(nf)), "split_cross": _r1(_nanmean(cx)),
                "best": best, "regret": _r(rg), "regret_se": _r(rg_se),
                "regret_xfit": _r(np.mean(xf, axis=0)) if xf else None, "worlds": int(W_eff),
                "worlds_to_detect_median": (int(np.median(fin)) if len(fin) else None)}
        # across scenarios, per lineup: the weighted scenarios only (weight 0 marks a diagnostic -- the noise
        # reference is the base on another seed, not an assumption, and must not vote on robustness)
        ag = np.where(wts > 0)[0] if (wts > 0).any() else np.arange(S)
        wa = wts[ag] if (wts > 0).any() else np.ones(len(ag))
        wsum = float(wa.sum()) or 1.0
        an = [names[i] for i in ag]
        by_l = []
        mean_over = (wa[:, None] * mean[ag]).sum(axis=0) / wsum
        worst = mean[ag].min(axis=0)
        r_mean = L + 1 - rank_avg(mean_over)
        r_worst = L + 1 - rank_avg(worst)
        for l in range(L):
            zs = [abs(by_s[n]["z"][l]) for n in an]
            by_l.append({"lineup": l, "base": _r1(mean[b0, l]), "base_se": _r1(se[b0, l]),
                         "min": _r1(mean[ag, l].min()), "max": _r1(mean[ag, l].max()),
                         "sd_across": _r1(float(mean[ag, l].std())), "mean_across": _r1(mean_over[l]),
                         "worst_regret": _r1(regret[ag, l].max()),
                         "worst_regret_scenario": an[int(np.argmax(regret[ag, l]))],
                         "mean_regret": _r1(float((wa * regret[ag, l]).sum() / wsum)),
                         "top_n_share": _r1(float((wa * (ranks[ag, l] <= top_n)).sum() / wsum)),
                         "base_rank": _r1(ranks[b0, l]), "rank_by_mean": _r1(r_mean[l]),
                         "rank_by_worst": _r1(r_worst[l]), "max_abs_z": _r1(max(zs)),
                         "max_abs_z_scenario": an[int(np.argmax(zs))]})
        pb = mean[b0] if m != "ev" else None
        need = {}
        if pb is not None and m in ("top1", "top01", "cash"):
            var = np.clip(pb * (1.0 - pb), 0.0, None)
            for tgt in ((0.001, 0.0005) if m != "cash" else (0.005, 0.0025)):
                need[f"se_{tgt:g}"] = {"median": int(np.median([worlds_needed(v, tgt) for v in var])),
                                       "max": int(max(worlds_needed(v, tgt) for v in var))}
        # The seed check. A reseed scenario is the base on other worlds AND another anchor draw: the engine
        # levels each game's offences on ANCHOR_N measuring games, one draw a run shared by all its worlds,
        # which no within-run SE can see. Measured on a synthetic game at production settings (800 games, 2
        # passes, 8 seeds): a team's mean points moved 0.34-0.41 between seeds against a world-level SE of
        # 0.16 at 4,000 worlds (0.05 at 40,000). Independent worlds alone would put mean z^2 near 1; what it
        # reads above 1 is the variance an ABSOLUTE figure carries beyond its SE. Paired differences within one
        # run share the anchor (share, usage, field, points and condition scenarios leave it untouched), so
        # their SEs answer "given this run's anchor" and stay calibrated (SN13).
        rs = [names[i] for i in range(S) if raw["scenarios"][keep[i]].get("reseed")]
        seed = None
        if rs:
            z2 = [by_s[n]["mean_z2"] for n in rs if by_s[n]["mean_z2"] is not None]
            seed = {"scenarios": rs, "mean_z2": _r1(_nanmean(z2)) if z2 else None,
                    "share_beyond_2se": _r1(_nanmean([by_s[n]["n_z2"] / max(1, L) for n in rs])),
                    "expected_if_worlds_only": {"mean_z2": 1.0, "share_beyond_2se": 0.046},
                    "note": "the base on another seed: what exceeds 1 is the per-run anchor's variance, which an "
                            "absolute figure carries and a paired difference within one run does not"}
        out["metrics"][m] = {"by_scenario": by_s, "by_lineup": by_l, "worlds_needed": need, "seed_check": seed}
    return out


def _nanmean(xs):
    xs = [x for x in xs if x == x]
    return sum(xs) / len(xs) if xs else float("nan")


def _r(a, nd=6):
    return [None if (x != x) else round(float(x), nd) for x in np.asarray(a, dtype=np.float64).tolist()]


def _r1(x, nd=6):
    x = float(x)
    return None if x != x else round(x, nd)


# ---- 7. the production worlds (the PC) ---------------------------------------------------------
def sens_seed(art_id):
    """The sensitivity worlds' seed: never the board's (unseeded) nor the
    evaluator's final worlds (dfs_evaluate.final_seed), so a run spends
    neither the board's selection worlds nor the evaluator's held-out ones."""
    return zlib.crc32(f"vigil-sens|{art_id}".encode())


def _clear_pool_memo(S):
    for k in [k for k in list(S._cache) if isinstance(k, tuple) and k and k[0] == "nfl_pool"]:
        S._cache.pop(k, None)


def _capturing(real, counter):
    """simulate_game, wrapped: each side's box-score points ride out in the
    game's players as a pseudo-player (PTS_PREFIX + team), so they are filed
    with the worlds and come back with them on a cache hit."""
    def sim(game, n, rng, ctx):
        counter["games"] += 1
        out = real(game, n, rng, ctx)
        if out is not None:
            boxes = out.get("boxes") or {}
            teams = list(boxes)
            for t in teams:
                arr = np.asarray(boxes[t]["pts"], dtype=np.float64)
                out["players"].append({"name": PTS_PREFIX + str(t), "pos": "TEAMPTS", "team": t,
                                       "opp": next((u for u in teams if u != t), None), "proj_pts": 0.0,
                                       "sim_mean": round(float(arr.mean()), 3), "floor": 0.0, "ceiling": 0.0,
                                       "arr": arr})
            out["boxes"] = None
        return out
    sim.__wrapped__ = real
    return sim


@contextlib.contextmanager
def patched_inputs(games, ctx, teams):
    """player_pool's inputs swapped for a scenario's, in this process only:
    the week's lines (`games`), the engine's context (`ctx`, its drive levels
    narrowed to `teams` so another game's script never changes this game's
    cache key) and a capturing simulate_game. Restored on the way out."""
    import nfl_dfs_engine as ENG
    import nfl_dfs_sim as S
    saved = (S.weekly_games, ENG.week_context, ENG.simulate_game)
    counter = {"games": 0}
    want = {str(t).upper() for t in teams}

    def week_context(season, week, workers=1):
        c = dict(ctx)
        c["drive"] = {t: v for t, v in (ctx.get("drive") or {}).items() if str(t).upper() in want}
        c["keep_boxes"] = True
        c["workers"] = int(workers or 1)
        return c
    S.weekly_games = lambda season, week: games
    ENG.week_context = week_context
    ENG.simulate_game = _capturing(saved[2], counter)
    _clear_pool_memo(S)
    try:
        yield counter
    finally:
        S.weekly_games, ENG.week_context, ENG.simulate_game = saved
        _clear_pool_memo(S)


class EngineWorlds:
    """A scenario's worlds through the production path: nfl_dfs_sim.player_pool
    on the engine model, one game a call (so pool_cache keys each game on its
    own inputs), on `seed` (+ the scenario's reseed). Base inputs are read
    once (Sleeper's week, the engine's context) unless passed in."""

    def __init__(self, week, teams, n, seed, season=None, workers=1, games=None, ctx=None, log=print,
                 anchor_n=None):
        import nfl_dfs_sim
        self.week, self.n, self.seed, self.workers, self.log = int(week), int(n), int(seed), int(workers or 1), log
        # anchor_n: the engine's measuring games (production's ANCHOR_N, 800, when None). More shrinks the
        # per-run anchor error by sqrt; pool_cache's key does not carry it, so it is mixed into the seed --
        # a cached entry of one anchor size can never answer for another
        self.anchor_n = int(anchor_n) if anchor_n else None
        if self.anchor_n:
            self.seed = zlib.crc32(f"{self.seed}|anchor_n|{self.anchor_n}".encode())
        self.season = str(season or nfl_dfs_sim._season())
        self.teams = {str(t).upper() for t in teams}
        self._games, self._ctx = games, ctx

    def base_games(self):
        if self._games is None:
            import nfl_dfs_sim
            g = nfl_dfs_sim.weekly_games(self.season, self.week) or {}
            self._games = {gid: x for gid, x in g.items()
                           if self.teams & {str(t).upper() for t in (x.get("teams") or [])}}
        return self._games

    def base_ctx(self):
        if self._ctx is None:
            import nfl_dfs_engine
            self._ctx = nfl_dfs_engine.week_context(self.season, self.week, workers=self.workers)
        return self._ctx

    def __call__(self, ops, reseed=0):
        import nfl_dfs_sim as S
        games, rg = apply_games(self.base_games(), ops)
        ctx, rc = apply_ctx(self.base_ctx(), ops)
        if self.anchor_n:
            ctx["anchor_n"] = self.anchor_n
        seed = self.seed if not reseed else zlib.crc32(f"{self.seed}|reseed|{int(reseed)}".encode())
        pool, team_pts, per_game = {}, {}, {}
        for gid, g in sorted(games.items(), key=lambda kv: str(kv[0])):
            gt = [str(t).upper() for t in (g.get("teams") or [])[:2]]
            if len(gt) < 2:
                continue
            t0 = time.time()
            with patched_inputs(games, ctx, gt) as counter:
                p = S.player_pool(self.week, n=self.n, season=self.season, model="engine", seed=seed,
                                  teams=set(gt), workers=self.workers) or {}
                rep = (S.engine_reports.get((self.season, self.week, tuple(sorted(gt)), seed)) or {})
            got = 0
            for name, e in p.items():
                if str(e.get("team") or "").upper() not in gt:
                    continue                    # another team's stand-in defense: not this game's worlds
                if name.startswith(PTS_PREFIX):
                    team_pts[name[len(PTS_PREFIX):].upper()] = e.get("arr") or []
                else:
                    pool[name] = e
                    got += 1
            an = (rep.get("games") or {}).get(str(gid)) or {}
            per_game[str(g.get("label") or gid)] = {
                "simulated": counter["games"] > 0, "seconds": round(time.time() - t0, 1), "players": got,
                "fallback_legacy": list(rep.get("fallback_legacy") or []),
                "anchor": {k: an.get(k) for k in ("deep", "target", "landed", "att_target", "att_landed")}}
            self.log(f"[sens]   {g.get('label') or gid}: {'simulated' if counter['games'] else 'from the cache'} "
                     f"({time.time() - t0:.0f}s)")
        return {"pool": pool, "team_pts": team_pts, "games": games,
                "report": {"lines": rg, "levels": rc, "games": per_game, "seed": int(seed)}}


# ---- 8. the runner --------------------------------------------------------------------------
class Runner:
    """Every scenario's figures for a set of lineups on one board's artifact.

    `meta` is the evaluator artifact's meta (players in the board's order,
    contests with payout tables, kind); `field` (idx, w) the board's field;
    `simulate(ops, reseed)` returns {"pool", "team_pts", "games", "report"}
    (EngineWorlds on the PC; a stub in the guards)."""

    def __init__(self, meta, field, lineups, simulate, n=None, block=None, log=print, workers=1, board=None,
                 keep_worlds=3):
        if np is None:
            raise RuntimeError("numpy is required for a sensitivity run")
        import dfs_evaluate as DE
        # block None: NBLOCKS blocks of the first scenario's worlds, the same cut for every scenario after it
        self.meta, self.simulate, self.n, self.log = meta, simulate, n, log
        self.block = int(block) if block else None
        self.kind, self.P = meta["kind"], len(meta["players"])
        self.field_idx = np.asarray(field[0], dtype=np.int64)
        self.field_w = None if field[1] is None else np.asarray(field[1], dtype=np.float64)
        self.lus, self.bad = resolve_lineups(meta, lineups)
        if not self.lus:
            raise ValueError("no legal lineup to score")
        self.qs = np.asarray(DE._fractions(meta), dtype=np.float64)
        self.workers, self.board, self.keep_worlds = workers, board, int(keep_worlds)
        self._worlds, self._fields, self._T = {}, {}, {}
        self.timing = {}

    def _get_worlds(self, spec):
        import dfs_evaluate as DE
        k = sim_sig(spec)
        if k in self._worlds:
            return self._worlds[k]
        t0 = time.time()
        out = self.simulate(sim_ops(spec), reseed=spec["reseed"])
        rows, missing = DE._match_worlds(self.meta, out["pool"])
        W = min((len(r) for r in rows if r is not None), default=0)
        if self.n:
            W = min(W, int(self.n))
        if not W:
            raise RuntimeError(f"{spec['name']}: no worlds for this board's players")
        X = np.zeros((self.P, W), dtype=np.float32)
        for i, r in enumerate(rows):
            if r is not None:
                X[i] = np.asarray(r[:W], dtype=np.float32)
        tp = {t: np.asarray(a[:W], dtype=np.float64) for t, a in (out.get("team_pts") or {}).items() if len(a) >= W}
        rec = {"X": X, "team_pts": tp, "games": out.get("games") or {}, "report": out.get("report") or {},
               "missing": missing, "seconds": round(time.time() - t0, 1)}
        # P x W float32 a scenario (64 MB at 400 players x 40,000 worlds): the first worlds read (the base's)
        # stay, the rest are held newest-first up to keep_worlds; one dropped is re-read from pool_cache
        while len(self._worlds) >= max(2, self.keep_worlds):
            self._worlds.pop(list(self._worlds)[1])
        self._worlds[k] = rec
        return rec

    def _get_field(self, spec):
        k = field_sig(spec)
        if k not in self._fields:
            t0 = time.time()
            idx, w, rec = field_for(field_op(spec), self.field_idx, self.field_w, self.meta["players"], self.kind,
                                    board=self.board, workers=self.workers)
            rec["seconds"] = round(time.time() - t0, 1)
            base_k = field_sig({"name": "base"})
            while len(self._fields) >= 3:
                self._fields.pop(next(k_ for k_ in self._fields if k_ != base_k))
            self._fields[k] = (np.asarray(idx, dtype=np.int64), np.asarray(w, dtype=np.float64), rec)
        return self._fields[k]

    def score(self, spec):
        """One scenario: its block sums for every contest and metric, the
        lineups' mean points, and every receipt."""
        spec = normalize(spec)
        wr = self._get_worlds(spec)
        X, pts_rec = apply_points(wr["X"], self.meta["players"], spec["ops"])
        W = X.shape[1]
        mask, cond_rec = world_mask(wr["team_pts"], wr["games"], spec["ops"], W)
        fidx, fw, frec = self._get_field(spec)
        tk = (sim_sig(spec), points_sig(spec), field_sig(spec))
        t0 = time.time()
        if tk not in self._T:
            T, device = field_T(X, fidx, fw, self.P, self.kind, self.qs)
            if len(self._T) >= 4:
                self._T.pop(next(iter(self._T)))
            self._T[tk] = (T, device, round(time.time() - t0, 1))
        T, device, t_sec = self._T[tk]
        B, Sc = lineup_buckets(X, self.lus)
        if self.block is None:
            self.block = max(1, -(-W // NBLOCKS))
        edges = block_edges(W, self.block)
        sums = {}
        for ci, c in enumerate(self.meta["contests"]):
            for m, A in contest_hits(B, T, self.qs, c).items():
                sums[f"{ci}:{m}"] = block_sums(A, mask, edges)
        sums["score"] = block_sums(Sc, mask, edges)
        counts = np.add.reduceat(mask.astype(np.float64), edges)
        return {"sums": sums, "counts": counts, "W": int(W),
                "receipts": {"worlds": wr["report"], "missing_from_worlds": wr["missing"], "points": pts_rec,
                             "conditions": cond_rec, "field": frec,
                             "thresholds": {"device": device, "seconds": t_sec},
                             "worlds_seconds": wr["seconds"], "kept_worlds": int(mask.sum())}}

    def run(self, scenarios):
        """Every scenario in order (the base first); the raw block sums and
        the receipts. Scenarios are normalized here; a bad one raises before
        any world is simulated."""
        specs = [normalize(s) for s in scenarios]
        if len({s["name"] for s in specs}) != len(specs):
            raise ValueError("scenario names must be unique")
        per = []
        for s in specs:
            t0 = time.time()
            self.log(f"[sens] scenario {s['name']}: {len(s['ops'])} op(s)")
            try:
                per.append(self.score(s))
            except Exception as e:
                errlog.note("SENS-scenario", e, path=s["name"])
                raise
            self.log(f"[sens] scenario {s['name']} done ({time.time() - t0:.0f}s)")
        nb = max(len(p["counts"]) for p in per)
        keys = sorted(per[0]["sums"])
        sums = {k: np.stack([_pad(p["sums"][k], nb) for p in per]) for k in keys}
        counts = np.stack([_pad(p["counts"][None, :], nb)[0] for p in per])
        return {"version": VERSION, "scenarios": specs, "block": self.block, "counts": counts, "sums": sums,
                "lineups": [{k: lu[k] for k in ("label", "source", "sources", "names", "captain")} for lu in self.lus],
                "illegal_lineups": self.bad,
                "contests": [{k: c.get(k) for k in ("id", "name", "entry_fee", "capacity", "places_paid")}
                             for c in self.meta["contests"]],
                "receipts": {s["name"]: p["receipts"] for s, p in zip(specs, per)},
                "worlds": {s["name"]: p["W"] for s, p in zip(specs, per)}}


def _pad(a, nb):
    a = np.asarray(a, dtype=np.float64)
    if a.shape[-1] == nb:
        return a
    out = np.zeros(a.shape[:-1] + (nb,))
    out[..., :a.shape[-1]] = a
    return out


# ---- 9. exposure, presets and the runtime estimate ------------------------------------------------
def exposure(meta, lus):
    """{player index: share of the lineups holding him}, and per game."""
    cnt = {}
    for lu in lus:
        for i in lu["idx"]:
            cnt[i] = cnt.get(i, 0) + 1
    L = max(1, len(lus))
    by_p = {i: c / L for i, c in cnt.items()}
    by_g = {}
    for i, sh in by_p.items():
        g = meta["players"][i].get("game") or meta["players"][i].get("team")
        by_g[g] = by_g.get(g, 0.0) + sh
    return by_p, by_g


def preset(meta, lus, kind="standard", base_games=None):
    """The scenarios a PC run asks, built from the lineup set's own exposure
    (research/reports/dfs_sensitivity.md section 5 explains each)."""
    by_p, by_g = exposure(meta, lus)
    pl = meta["players"]
    top = sorted(by_p, key=lambda i: -by_p[i])

    def most(pos, k):
        return [pl[i] for i in top if (pl[i].get("pos") or "") == pos][:k]
    out = [scenario("base", note="the board's assumptions on independent worlds")]
    out.append(scenario("noise_reference", reseed=1, weight=0.0,
                        note="the base again on another seed: any difference here is Monte Carlo noise "
                             "(weight 0: a diagnostic, never an assumption the robustness figures average over)"))
    for s in range(1, (5 if kind == "standard" else 3)):
        out.append(scenario(f"usage_noise_{s}", [{"kind": "usage_noise", "sd": 0.10, "seed": s}],
                            note="every player's target and carry share off by a random ~10% (team totals kept)"))
    for p in most("RB", 3 if kind == "standard" else 1):
        out.append(scenario(f"rb_carries_down:{p['name']}", [{"kind": "share", "comp": "rush_att", "mult": 0.75,
                    "absorb": "position", "select": {"names": [p["name"]], "team": p["team"]}}],
                    note="this back's carries 25% short, his backfield mates take them"))
    out.append(scenario("rb1s_carries_down", [{"kind": "share", "comp": "rush_att", "mult": 0.80,
                        "absorb": "position", "select": {"role": "RB1"}}],
                        note="every lead back 20% short on carries"))
    for p in most("WR", 3 if kind == "standard" else 1):
        out.append(scenario(f"wr_targets_down:{p['name']}", [{"kind": "share", "comp": "rec_tgt", "mult": 0.80,
                    "absorb": "team", "select": {"names": [p["name"]], "team": p["team"]}}],
                    note="this receiver's target share overestimated by 20%"))
    out.append(scenario("wr1s_targets_down", [{"kind": "share", "comp": "rec_tgt", "mult": 0.85,
                        "absorb": "team", "select": {"role": "WR1"}}], note="every WR1's targets 15% short"))
    # "usage off by 10%" for the two most-used skill players (a quarterback's volume is the game's, so not here)
    for p in [pl[i] for i in top if (pl[i].get("pos") or "") in ("RB", "WR", "TE")][:2]:
        comp = "rush_att" if p["pos"] == "RB" else "rec_tgt"
        for mlt in (0.9, 1.1):
            out.append(scenario(f"usage_{'down' if mlt < 1 else 'up'}10:{p['name']}",
                                [{"kind": "share", "comp": comp, "mult": mlt, "absorb": "team",
                                  "select": {"names": [p["name"]], "team": p["team"]}}],
                                note=f"this player's usage {int(round(100 * (mlt - 1))):+d}%"))
    games = sorted(by_g, key=lambda g: -by_g[g])[: (2 if kind == "standard" else 1)]
    for g in games:
        team = next((p["team"] for p in pl if (p.get("game") or p.get("team")) == g), None)
        if not team:
            continue
        out.append(scenario(f"blowout_fav:{g}", [{"kind": "condition", "game": team, "stat": "margin_fav", "lo": 17}],
                            note="given the favourite won by 17+ (the base worlds, conditioned)"))
        out.append(scenario(f"upset:{g}", [{"kind": "condition", "game": team, "stat": "margin_fav", "hi": -7}],
                            note="given the underdog won by 7+"))
        out.append(scenario(f"shootout:{g}", [{"kind": "condition", "game": team, "stat": "total", "lo": 60}],
                            note="given 60+ total points"))
        out.append(scenario(f"low_scoring:{g}", [{"kind": "condition", "game": team, "stat": "total", "hi": 34}],
                            note="given 34 or fewer total points"))
    if games:
        g = games[0]
        tm = sorted({p["team"] for p in pl if (p.get("game") or p.get("team")) == g and p.get("team")})
        if len(tm) == 2:
            out.append(scenario(f"script_shift:{g}", [{"kind": "script", "team": tm[0], "d_pts": 7.0},
                                                       {"kind": "script", "team": tm[1], "d_pts": -7.0}],
                                note=f"{tm[0]} expected 7 more points and {tm[1]} 7 fewer: the line was 14 off"))
    if meta["kind"] == "classic":
        out.append(scenario("field_casual", [{"kind": "field", "model": "casual"}],
                            note="the calibrated softmax field the optimizer field replaced"))
    else:
        out.append(scenario("field_dup_matched", [{"kind": "field", "model": "sd_top_share", "top_share": 0.00468}],
                            note="the showdown knob that matched Sunday's real collision (research/reports/sd_dup.md)"))
    out.append(scenario("own_concentrated", [{"kind": "field", "model": "tilt", "target": "concentrate",
                                              "sumsq": OWN_SUMSQ_REAL}],
                        note="ownership as concentrated as the real fields' (sum of own^2 1.5)"))
    for s in range(1, (4 if kind == "standard" else 2)):
        out.append(scenario(f"own_error_{s}", [{"kind": "field", "model": "tilt", "target": "noise",
                                                "mae": OWN_MAE_MEASURED, "seed": s}],
                            note=f"ownership off by the measured {OWN_MAE_MEASURED} points (top 40), at random"))
    out.append(scenario("chalk_up", [{"kind": "field", "model": "tilt", "target": "scale", "top_k": 10, "mult": 1.5}],
                        note="the ten most-owned players 50% more owned"))
    if top:
        p = pl[top[0]]
        out.append(scenario(f"scratch:{p['name']}", [{"kind": "points", "mult": 0.0,
                                                      "select": {"names": [p["name"]], "team": p["team"]}}],
                            note="the most-used player scores nothing (a late scratch; nobody absorbs it)"))
    return out


def games_touched(spec, n_games):
    """How many games a scenario re-simulates, the base's worlds cached: a
    share, efficiency or script op naming players or a team touches its game;
    a role-wide op, usage noise or a reseed touches every game (an upper bound:
    a selector can also match on one team only)."""
    s = normalize(spec)
    if s["reseed"]:
        return n_games
    teams = set()
    for o in s["ops"]:
        if o["kind"] not in SIM_KINDS:
            continue
        if o["kind"] == "script":
            teams.add(o["team"])
        elif o["kind"] == "usage_noise":
            if not o["teams"]:
                return n_games
            teams |= set(o["teams"])
        elif o["select"].get("teams"):
            teams |= set(o["select"]["teams"])
        else:
            return n_games
    return min(n_games, len(teams))


def estimate(specs, n_worlds, n_games, field_rows, workers=12, gpu=False):
    """(seconds, the parts) a run should take on the PC: each re-simulated game
    is the anchor (2,400 games, serial) plus n worlds over min(workers, n/5,000)
    processes at GAME_MS a game, plus ~4 s of dealing and process start; the
    field's thresholds once per distinct (worlds, points, field) at the
    measured rate per world x field lineup; a casual field ~90 calibration
    draws of 20,000 and the field draw at 121 us a row over the workers."""
    per_game = 2.4 * GAME_MS + (n_worlds * GAME_MS / 1000.0) / max(1, min(int(workers), -(-n_worlds // 5000))) + 4.0
    cell = THRESH_S_PER_CELL_GPU if gpu else THRESH_S_PER_CELL_CPU
    sim_s = thr_s = field_s = 0.0
    seen_w, seen_t, first = set(), set(), True
    for s in specs:
        s = normalize(s)
        ws = sim_sig(s)
        if ws not in seen_w:
            seen_w.add(ws)
            sim_s += per_game * (n_games if first else games_touched(s, n_games))
            first = False
        tk = (ws, points_sig(s), field_sig(s))
        if tk not in seen_t:
            seen_t.add(tk)
            thr_s += cell * n_worlds * field_rows
            fo = field_op(s)
            if fo.get("model") == "casual":
                field_s += (90 * 20000 + field_rows) * 121e-6 / max(1, int(workers))
            elif fo.get("model") == "tilt":
                field_s += 10.0
    return sim_s + thr_s + field_s, {"simulation_s": round(sim_s), "thresholds_s": round(thr_s),
                                     "fields_s": round(field_s), "per_game_s": round(per_game, 1)}


def render_markdown(summary, raw, top=15):
    """A readable digest of a run (the PC writes it beside summary.json)."""
    L = raw["lineups"]
    lines = [f"# Sensitivity run: {raw.get('artifact', '')}", "",
             f"Contest: {summary['contest']['name']} (fee ${summary['contest']['entry_fee']:g}). "
             f"Worlds per scenario: {', '.join(f'{k} {v:,}' for k, v in list(summary['worlds'].items())[:3])} ... "
             f"({summary['blocks']} blocks of {summary['block_worlds']:,}). Every figure is on worlds of the run's "
             "own seed: none chose a lineup.", ""]
    m = summary["metrics"].get("top1")
    if m:
        lines += ["## Scenarios (P(top 1%))", "",
                  "| scenario | lineups moved >2 SE | >3 SE | Spearman vs base | noise floor (split) | cross (split) | worlds kept |",
                  "|---|---|---|---|---|---|---|"]
        for n, v in m["by_scenario"].items():
            lines.append(f"| {n} | {v['n_z2']} | {v['n_z3']} | {v['spearman']} | {v['split_noise_floor']} | "
                         f"{v['split_cross']} | {v['worlds']:,} |")
        lines += ["", "## Lineups, most robust first (mean P(top 1%) across scenarios)", "",
                  "| lineup | base % | min % | max % | worst regret (pts) | top-N share | base rank | rank by mean | rank by worst |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in sorted(m["by_lineup"], key=lambda r: r["rank_by_mean"])[:top]:
            lines.append(f"| {L[r['lineup']]['label']} | {100 * r['base']:.2f} | {100 * r['min']:.2f} | "
                         f"{100 * r['max']:.2f} | {100 * r['worst_regret']:.2f} | {r['top_n_share']:.2f} | "
                         f"{r['base_rank']:g} | {r['rank_by_mean']:g} | {r['rank_by_worst']:g} |")
        if m.get("worlds_needed"):
            lines += ["", "Worlds a scenario needs for P(top 1%) to a stated SE (from the base's own rates): "
                      + "; ".join(f"{k}: median {v['median']:,}, max {v['max']:,}" for k, v in m["worlds_needed"].items())]
    return "\n".join(lines) + "\n"
