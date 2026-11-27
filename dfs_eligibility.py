"""Who the DFS boards may put in OUR lineups: the eligibility modes (2026-11-27;
research/reports/dfs_eligibility.md, the fit research/dfs_eligibility_fit.py).

The owner: "Michael Pittman Jr. is a perfect example. Sleeper lists him as WR4
following an extended injury absence, but he has returned. Vigil automatically
excludes him from recommended lineups while allowing the simulated field to
roster him." The depth-chart gate (nfl_dfs._apply_depth) keeps QB1, RB1-2,
WR1-3 and TE1-2 by Sleeper's depth order; everyone below becomes a field-only
extra (dfs_tourney._field_extras) -- the public plays him, we never do.

Three modes, the production default unchanged:
  conservative  today's gate, byte for byte (the default; nothing here runs).
  adaptive      the gate's players PLUS any rank-gated player whose projected
                role gives a credible chance of a starter-level game: the
                probability that his DraftKings points reach L_pos (the median
                score of the last rank the gate keeps at his position), from a
                per-position logistic on his projection, his recent share of
                the team's targets + carries over his last games played, and
                whether he is new to the team's box or returning from an
                absence (his share is then his PRE-absence share), at least
                tau_pos -- the 5th percentile of the same probability among the
                players the gate already keeps. Fitted on 2022-2024 Sleeper
                player-weeks, tested on 2025 (research/dfs_eligibility_fit.py,
                the rule written before the fit); never hand-set.
  unrestricted  every rank-gated player with a projection.
Both widen OUR pool only from the field's own extras, so the field, the lineup
universe and every field figure are the conservative board's exactly: only
which of those lineups we may enter changes (dfs_tourney applies it).

Hard exclusions hold in every mode: not on any roster Sleeper knows, a roster
status other than Active, an out-type injury tag (Out, Doubtful, IR, PUP,
suspended, NA, COV), DraftKings' own unavailable statuses, no depth-chart
entry (practice squad), and no projection this week -- the simulator cannot
deal a player it never projected, and eligibility NEVER changes a projection:
the simulator's own numbers decide whether an eligible player is any good.

Flags (display only; they never change eligibility or a projection), carried on
an experimental board's players and shown on the tab: "returning" (played for
this team earlier this season, missed the team's last game(s), back in the
pool), "uncertain" (returning, Questionable on either source, fewer than two
games for this team, or a teammate at his position newly out), "promoted" (a
teammate at his position who played the team's last game is now out).

PFR snap counts and NGS are display-only under the licence default (CLAUDE.md,
the NFL rushing model) and nothing here reads them; practice participation
(nflverse injury reports) is not read either -- Sleeper's injury tag and
DraftKings' status are what the gate sees.
"""
import json
import math
import os
import time

import errlog

MODES = ("conservative", "adaptive", "unrestricted")
DEFAULT = "conservative"
POS = ("QB", "RB", "WR", "TE")
RECENT_N = 3                    # games played a recent share is read over (the rule)
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", "data", "dfs_eligibility",
                          "model.json")
# the depth-chart rank gate's reasons that a mode may lift (nfl_dfs._apply_depth's words); every other reason is a
# hard exclusion in every mode
RANK_GATE = "by depth chart"


def mode_of(m):
    """A mode name, or ValueError -- a typo must never build an unrestricted board."""
    m = (m or DEFAULT).strip().lower()
    if m not in MODES:
        raise ValueError(f"eligibility mode {m!r}: one of {', '.join(MODES)}")
    return m


# ---- the model: features and probability (shared with the fit, so production reads what was tested) ----

def features(proj, share, has_hist, returning):
    """The rule's design row: 1, ln(1 + projected DK points), recent share of the team's targets + carries,
    no earlier game for this team, returning from an absence, returning x share (the pre-absence share)."""
    s = float(share or 0.0)
    r = 1.0 if returning else 0.0
    return [1.0, math.log1p(max(0.0, float(proj or 0.0))), s, 0.0 if has_hist else 1.0, r, r * s]


def prob(coef, x):
    z = sum(c * v for c, v in zip(coef, x))
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def team_played(hist, team, w):
    """Did `team` play in week `w`? Sleeper's box lists a team on its bye with nothing, so a week counts only
    when the team's targets + carries are positive."""
    t = (hist.get(w) or {}).get("tot", {}).get(team)
    return bool(t) and (float(t[0]) + float(t[1])) > 0


def usage_row(pid, team, hist, n=RECENT_N):
    """His recent role from a {week: compact week} history (nfl_usage.compact's shape, strictly earlier weeks):
    {share, has_hist, returning, missed, games, last_played}. `share` is his targets + carries over his last `n`
    games played FOR THIS TEAM over the team's in those weeks (a returning player's is his pre-absence share);
    `missed` the team's most recent games he did not play, consecutively."""
    pid = str(pid)
    tw = sorted(w for w in hist if team_played(hist, team, w))
    played = [w for w in tw if (hist[w]["pl"].get(pid) or [None])[0] == team]
    out = {"share": 0.0, "has_hist": bool(played), "returning": False, "missed": 0,
           "games": len(played), "last_played": played[-1] if played else None}
    if not played:
        return out
    recent = played[-n:]
    num = sum(float(hist[w]["pl"][pid][1]) + float(hist[w]["pl"][pid][2]) for w in recent)
    den = sum(float(hist[w]["tot"][team][0]) + float(hist[w]["tot"][team][1]) for w in recent)
    out["share"] = (num / den) if den > 0 else 0.0
    missed = 0
    for w in reversed(tw):
        if w in played:
            break
        missed += 1
    out["missed"] = missed
    out["returning"] = missed > 0
    return out


_MODEL = {"m": None, "mtime": None}


def load_model(path=None):
    """The fitted model (research/data/dfs_eligibility/model.json), or None when it is missing or unreadable --
    adaptive then admits nobody beyond the gate and the board says why."""
    p = path or MODEL_PATH
    try:
        mt = os.path.getmtime(p)
        if path is None and _MODEL["m"] is not None and _MODEL["mtime"] == mt:
            return _MODEL["m"]
        with open(p) as fh:
            m = json.load(fh)
        if not all(pos in (m.get("positions") or {}) for pos in POS):
            return None
        if path is None:
            _MODEL.update(m=m, mtime=mt)
        return m
    except (OSError, ValueError) as e:
        errlog.note("ELIG-model", e)
        return None


def p_relevant(model, pos, proj, row):
    """P(his DK points reach L_pos), or None for a position the model does not cover (K, DST)."""
    spec = (model or {}).get("positions", {}).get(pos)
    if not spec:
        return None
    return prob(spec["coef"], features(proj, row["share"], row["has_hist"], row["returning"]))


# ---- production: the week's name -> Sleeper id map and history, read once a week per process ----

_WEEK = {}
_WEEK_TTL_S = 3600


def week_context(season, week, fetch=None, now=None):
    """{"ids": {normalised name: (player id, team)}, "hist": {week: compact week}} for a board's week, or
    {"error": ...}: Sleeper's projection rows (the names the pool was built from) and nfl_usage's stored box
    history. Cached an hour per (season, week)."""
    now = time.time() if now is None else now
    key = (int(season), int(week))
    got = _WEEK.get(key)
    if got and now - got[0] < _WEEK_TTL_S and fetch is None:
        return got[1]
    try:
        import nfl_adp
        import nfl_dfs_sim
        import nfl_usage
        f = fetch or nfl_dfs_sim._get
        q = "&".join(f"position[]={p}" for p in POS)
        rows = f(f"{nfl_dfs_sim._PROJ.format(season=key[0], week=key[1])}?season_type=regular&{q}&order_by=pts_ppr")
        ids = {}
        for r in rows or []:
            p = r.get("player") or {}
            nm = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            if nm and r.get("player_id") is not None:
                k = nfl_adp._norm(nm)
                ids.setdefault(k, (str(r["player_id"]), r.get("team")))
                ids.setdefault(k.replace(" ", ""), (str(r["player_id"]), r.get("team")))
        hist = nfl_usage.history(key[0], key[1], fetch=fetch)
        if hist is None:
            ctx = {"error": "the season's box history could not be read (nfl_usage)"}
        else:
            ctx = {"ids": ids, "hist": hist}
    except Exception as e:
        errlog.note("ELIG-context", e)
        ctx = {"error": f"{type(e).__name__}: {e}"}
    if fetch is None:
        _WEEK[key] = (now, ctx)
    return ctx


def assess(players, excluded, mode, ctx, model):
    """The mode's verdict on a gated pool. `players` are the gate's kept rows, `excluded` its excluded list
    (nfl_dfs._apply_depth); returns {"mode", "admitted": {name: info}, "flags": {name: [flag, ...]},
    "info": {name: info}, "note"}. Nothing is mutated; conservative admits nobody. Only a rank-gated player
    ("WR4 by depth chart ...") can be admitted -- every other exclusion is hard."""
    mode = mode_of(mode)
    out = {"mode": mode, "admitted": {}, "flags": {}, "info": {}, "note": None}
    if mode == "conservative":
        return out
    import nfl_adp
    ids = (ctx or {}).get("ids") or {}
    hist = (ctx or {}).get("hist")
    if hist is None:
        out["note"] = f"no usage history ({(ctx or {}).get('error') or 'not read'}): flags unavailable" + (
            "; adaptive admits nobody beyond the depth chart" if mode == "adaptive" else "")
    if mode == "adaptive" and model is None:
        out["note"] = "the eligibility model is missing (research/data/dfs_eligibility/model.json): adaptive " \
                      "admits nobody beyond the depth chart"
    # a teammate is "out" for the promoted flag only on an injury tag or an inactive roster status: "not on any roster
    # Sleeper knows" is a lookup miss (DEN @ PIT's fullback), not an absence
    hard_out = {}
    for d in excluded or []:
        if (d.get("why") or "").startswith(("listed ", "roster status")):
            hard_out.setdefault((d.get("team"), (d.get("pos") or "").upper()), []).append(d["name"])

    def info_for(name, pos, team, proj):
        k = nfl_adp._norm(name or "")
        hit = ids.get(k) or ids.get(k.replace(" ", ""))
        if hist is None or hit is None:
            return None
        row = usage_row(hit[0], team, hist)
        p = p_relevant(model, pos, proj, row) if model else None
        spec = (model or {}).get("positions", {}).get(pos) or {}
        return {"pos": pos, "share": round(row["share"], 4), "games": row["games"], "missed": row["missed"],
                "returning": row["returning"], "has_hist": row["has_hist"],
                "p": None if p is None else round(p, 4), "tau": spec.get("tau"), "level": spec.get("L")}

    for p in players or []:
        pos = (p.get("pos") or "").upper()
        if pos not in POS:
            continue
        inf = info_for(p.get("name"), pos, p.get("team"), p.get("proj"))
        if inf is not None:
            out["info"][p["name"]] = inf
    for d in excluded or []:
        if RANK_GATE not in (d.get("why") or ""):
            continue
        pos = (d.get("pos") or "").upper()
        proj = d.get("proj")
        inf = info_for(d.get("name"), pos, d.get("team"), proj)
        if inf is not None:
            out["info"][d["name"]] = inf
        if mode == "unrestricted":
            out["admitted"][d["name"]] = inf or {}
        elif model is not None and inf is not None and inf["p"] is not None and inf["tau"] is not None \
                and inf["p"] >= float(inf["tau"]):
            out["admitted"][d["name"]] = inf
    for name, inf in out["info"].items():
        fl = []
        if inf.get("returning"):
            # a quarterback's targets + carries share says nothing about his role, so it is not printed for one
            fl.append(f"returning: missed the team's last {inf['missed']} game{'s' if inf['missed'] != 1 else ''}"
                      + ("" if inf.get("pos") == "QB" else f"; pre-absence share {100 * inf['share']:.0f}%"))
        if not inf.get("has_hist"):
            fl.append("no game for this team this season")
        elif inf.get("games", 0) < 2:
            fl.append("one game for this team this season")
        if fl:
            out["flags"][name] = fl
    for p in list(players or []) + [d for d in (excluded or []) if RANK_GATE in (d.get("why") or "")]:
        pos = (p.get("pos") or "").upper()
        mates = [m for m in hard_out.get((p.get("team"), pos), []) if m != p.get("name")]
        recent = []
        for m in mates:
            mi = None
            if hist is not None:
                k = nfl_adp._norm(m)
                hit = ids.get(k) or ids.get(k.replace(" ", ""))
                mi = usage_row(hit[0], p.get("team"), hist) if hit else None
            if mi and mi["games"] and mi["missed"] == 0:
                recent.append(m)
        if recent:
            out["flags"].setdefault(p["name"], []).append(f"promoted: {', '.join(sorted(recent))} out")
    return out


def is_uncertain(flags, questionable=False):
    """The tab's 'uncertain workload' mark: returning, new to the team's box, one game, promoted, or Questionable."""
    return bool(questionable) or any(f.startswith(("returning", "no game", "one game", "promoted")) for f in flags or [])
