"""Reconciling Sleeper's component means before a simulation (Stage 2C).

Sleeper projects every player on his own, so the team's books do not
balance: on 2022-2024 the receivers' projected yards sat within 15% of the
quarterback's projected passing yards in 80% of team-weeks and 23% off at
the 95th percentile; touchdowns 39% off at the 95th. The box scores keep
these identities exactly (passing yards equal the teammates' receiving
yards in 97.9% of team-weeks, touchdowns 99.6%, completions 98.0%), and a
simulator that generates the team's passing day and hands it out by
shares needs inputs that keep them too. This module makes the four team
identities hold, moving the numbers as little as the evidence allows:

  team target       T = lambda * Q + (1 - lambda) * S
  quarterback side  Q -> T        receivers' side  S -> T

where Q is the quarterback's figure, S the receivers' sum and lambda the
weight, measured on 2022-2024, that makes the blend the best predictor of
the actual team quantity (research.stage2c fits it, with a clustered
bootstrap). The change on each side is then spread over its players in
proportion to the residual variance of that component for that player's
position at his projection level (also measured, a + b * projection), so
the least reliable numbers absorb the most change: minimising
sum(delta^2 / variance) subject to the sum of the deltas, with bounds
(nothing below zero, receptions at most targets, completions at most
attempts, touchdowns at most receptions). Targets are not an identity:
the box scores put team targets at a measured 0.956 of pass attempts
(throwaways, spikes, batted balls), so the quarterback side of that one is
attempts times the measured factor.

The originals are kept: every player carries `means` untouched and
`recon` beside it. The legacy simulator reads `means`; the constrained
simulator reads `recon`. Pure Python (the server has no numpy); the
weights are a committed artifact, research/data/reconcile_weights.json,
fitted on the training seasons only, hashed and stamped."""
import hashlib
import json
import os

import dk_scoring
import errlog

RECON_MODEL = "wls-identity"
RECON_VERSION = 1
WEIGHTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", "data", "reconcile_weights.json")

# (name, quarterback component, receiver component), in the order they are
# reconciled: attempts before completions (a receiver's receptions are
# bounded by his reconciled targets, a quarterback's completions by his
# reconciled attempts), completions before touchdowns (a receiver's
# touchdowns are bounded by his receptions).
IDENTITIES = (("pass_att", "pass_att", "rec_tgt"),
              ("pass_cmp", "pass_cmp", "rec"),
              ("pass_yd", "pass_yd", "rec_yd"),
              ("pass_td", "pass_td", "rec_td"))
RECEIVER_POS = ("RB", "WR", "TE", "QB")       # a quarterback who catches a trick-play pass counts on the receivers' side
# Beyond these relative gaps a team-week is flagged (`tripped`) and listed
# in the report; it is still reconciled. They sit near the 95th percentile
# of the absolute gap on 2022-2024 (0.23 yards, 0.23 completions, 0.39
# touchdowns), so a trip means "look at this offense": an injury split
# between two quarterbacks, a receiver projected against the wrong passer.
TRIPWIRE = {"pass_att": 0.30, "pass_cmp": 0.30, "pass_yd": 0.30, "pass_td": 0.50}
_DK = dk_scoring.NFL_OFF


def _load_weights(path=WEIGHTS_PATH):
    try:
        with open(path) as fh:
            w = json.load(fh)
    except Exception as e:
        errlog.note("RECON-weights", e)
        return None
    if weights_hash(w.get("weights") or {}) != w.get("hash"):
        errlog.note("RECON-weights-hash", msg=f"{path}: content does not match its hash")
        return None
    return w


def weights_hash(weights):
    """SHA-256 of the canonical JSON of the fitted weights, so the stamp on
    a board names exactly which numbers reconciled its inputs."""
    return hashlib.sha256(json.dumps(weights, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


WEIGHTS = _load_weights()


def recon_stamp(weights=None):
    """What a board records about its reconciliation."""
    w = weights if weights is not None else WEIGHTS
    if not w:
        return {"model": RECON_MODEL, "version": RECON_VERSION, "weights": None}
    m = w.get("meta") or {}
    lam = w["weights"]["lambda"]
    return {"model": RECON_MODEL, "version": RECON_VERSION, "weights_hash": w["hash"][:16],
            "train_seasons": m.get("train_seasons"), "fitted_commit": m.get("commit"),
            "lambda": {k: round(float(v["value"]), 4) for k, v in lam.items()},
            "target_factor": round(float(w["weights"]["target_factor"]["value"]), 4),
            "identities": [i[0] for i in IDENTITIES], "tripwire": dict(TRIPWIRE)}


def dk_mean(means):
    """DraftKings points of a mean stat line (no threshold bonuses: a mean
    cannot say how often a line clears 100)."""
    g = lambda k: float(means.get(k) or 0.0)
    return (_DK["pass_yd"] * g("pass_yd") + _DK["pass_td"] * g("pass_td") + _DK["int"] * g("int")
            + _DK["rush_yd"] * g("rush_yd") + _DK["rush_td"] * g("rush_td") + _DK["rec"] * g("rec")
            + _DK["rec_yd"] * g("rec_yd") + _DK["rec_td"] * g("rec_td") + _DK["fumble_lost"] * g("fum"))


def _variance(weights, pos, comp, m):
    """Residual variance of one component at projection level m for a
    position: a + b * m from the fit. A position or component the fit
    does not cover falls back to the projection itself (Poisson-like), so
    an unknown row still takes a change in proportion to its size."""
    v = ((weights.get("variance") or {}).get(pos) or {}).get(comp)
    if not v:
        return max(1e-6, float(m))
    return max(1e-6, float(v["a"]) + float(v["b"]) * float(m))


def allocate(means, variances, change, lo, hi):
    """Spread `change` over the entries so that sum(delta) == change and
    sum(delta^2 / variance) is least, within [lo, hi] per entry: the
    unconstrained share is delta_i = change * v_i / sum(v); an entry that
    would cross a bound is pinned there and the rest re-shared (an active
    set, at most n rounds). Returns (new values, change left over); a
    leftover means the bounds bind everywhere and the identity cannot be
    met -- the caller flags it rather than forcing a number."""
    x = [float(m) for m in means]
    free = list(range(len(x)))
    rem = float(change)
    for _ in range(len(x) + 1):
        if abs(rem) < 1e-12 or not free:
            break
        tv = sum(variances[i] for i in free)
        if tv <= 0:
            break
        pinned = []
        for i in free:
            y = x[i] + rem * variances[i] / tv
            if y < lo[i] - 1e-12:
                pinned.append((i, lo[i]))
            elif y > hi[i] + 1e-12:
                pinned.append((i, hi[i]))
        if not pinned:
            for i in free:
                x[i] += rem * variances[i] / tv
            rem = 0.0
            break
        for i, b in pinned:
            rem -= (b - x[i])
            x[i] = b
            free.remove(i)
    return x, rem


def reconcile_team(players, weights=None, team=None):
    """Enforce the four identities on one team's players (dicts with `pos`
    and `means`). Attaches `recon` to every player (a copy of `means` with
    the reconciled components; untouched players get an identical copy)
    and returns the team's report: each identity's quarterback figure,
    receivers' sum, blended target and relative gap, the flags, whether a
    tripwire fired, and the DraftKings-point shift per player."""
    w = weights if weights is not None else WEIGHTS
    for p in players:
        p["recon"] = dict(p.get("means") or {})
    rep = {"team": team, "identities": {}, "flags": [], "tripped": False, "players": len(players), "dk_shift": {}}
    if not w:
        rep["flags"].append("no_weights")
        return rep
    W = w["weights"]
    qbs = [p for p in players if (p.get("pos") or "").upper() == "QB" and float((p["recon"].get("pass_att") or 0.0)) > 0]
    if not qbs:
        rep["flags"].append("no_qb")
        return rep
    factor = float(W["target_factor"]["value"])
    for name, qc, rc in IDENTITIES:
        f = factor if name == "pass_att" else 1.0
        recs = [p for p in players if (p.get("pos") or "").upper() in RECEIVER_POS and float(p["recon"].get(rc) or 0.0) > 0]
        Q = f * sum(float(p["recon"].get(qc) or 0.0) for p in qbs)
        S = sum(float(p["recon"].get(rc) or 0.0) for p in recs)
        if Q <= 0 and S <= 0:
            continue
        if Q <= 0:
            rep["flags"].append(f"no_qb_{qc}")          # the quarterback carries no figure for this one; the receivers keep theirs
            continue
        if not recs:
            rep["flags"].append(f"no_receivers_{rc}")
            continue
        lam = float(W["lambda"][name]["value"])
        gap = (S - Q) / Q
        if abs(gap) > TRIPWIRE[name]:
            rep["tripped"] = True
            rep["flags"].append(f"gap_{name}")
        # the quarterback side: completions may not exceed the (reconciled) attempts
        q_means = [float(p["recon"].get(qc) or 0.0) for p in qbs]
        q_var = [_variance(W, "QB", qc, m) for m in q_means]
        q_hi = [(float(p["recon"].get("pass_att") or 0.0) if qc == "pass_cmp" else float("inf")) for p in qbs]
        if any(m > h + 1e-9 for m, h in zip(q_means, q_hi)):
            # the quarterback's own figure breaks its own bound (Sleeper once
            # projected 42.9 completions on 27.5 attempts): it is no evidence,
            # so the receivers' sum is the target and the flag says why
            lam = 0.0
            rep["flags"].append(f"qb_bound_{name}")
        # the receivers' side: receptions at most targets, touchdowns at most receptions
        r_means = [float(p["recon"].get(rc) or 0.0) for p in recs]
        r_var = [_variance(W, (p.get("pos") or "").upper(), rc, m) for p, m in zip(recs, r_means)]
        r_hi = [(float(p["recon"].get("rec_tgt") or 0.0) if rc == "rec" else
                 float(p["recon"].get("rec") or 0.0) if rc == "rec_td" else float("inf")) for p in recs]
        T = lam * Q + (1.0 - lam) * S
        # the target must be reachable by both sides within their bounds;
        # it is clamped into the feasible range, and when the two ranges do
        # not meet the identity is left as it was and flagged
        lo_t, hi_t = max(0.0, 0.0), min(f * sum(q_hi), sum(r_hi))
        if lo_t > hi_t + 1e-9:
            rep["flags"].append(f"infeasible_{name}")
            continue
        T = min(max(T, lo_t), hi_t)
        q_new, q_left = allocate(q_means, q_var, T / f - sum(q_means), [0.0] * len(qbs), q_hi)
        r_new, r_left = allocate(r_means, r_var, T - S, [0.0] * len(recs), r_hi)
        if abs(q_left) > 1e-9 or abs(r_left) > 1e-9:
            rep["flags"].append(f"infeasible_{name}")   # the bounds bind everywhere; leave this identity as it was
            continue
        for p, v in zip(qbs, q_new):
            p["recon"][qc] = v
        for p, v in zip(recs, r_new):
            p["recon"][rc] = v
        # `qb` is the quarterback's figure on the receivers' scale (attempts
        # times the target factor for the attempts identity), so the target
        # always sits between `qb` and `sum`; `qb_raw` is his own number
        rep["identities"][name] = {"qb": round(Q, 3), "qb_raw": round(Q / f, 3), "sum": round(S, 3), "target": round(T, 3),
                                   "gap_rel": round(gap, 4), "lambda": lam,
                                   "after_gap": round(sum(float(p["recon"].get(rc) or 0.0) for p in recs)
                                                      - f * sum(float(p["recon"].get(qc) or 0.0) for p in qbs), 6)}
    for p in players:
        d = dk_mean(p["recon"]) - dk_mean(p.get("means") or {})
        if abs(d) > 1e-9:
            rep["dk_shift"][p.get("name") or "?"] = round(d, 3)
    return rep


def reconcile_games(games, weights=None):
    """Reconcile every team in a weekly_games-shaped dict in place (each
    player gains `recon`; each game gains `recon` = {team: report}) and
    return the slate summary: teams, tripped teams and their gaps, and the
    DraftKings-point shift distribution by position."""
    w = weights if weights is not None else WEIGHTS
    summary = {"model": RECON_MODEL, "version": RECON_VERSION, "teams": 0, "tripped": [], "flags": {},
               "dk_shift_by_pos": {}, "weights_hash": (w or {}).get("hash", "")[:16] if w else None}
    shifts = {}
    for gid, g in (games or {}).items():
        by_team = {}
        for p in g.get("players") or []:
            by_team.setdefault(p.get("team"), []).append(p)
        g["recon"] = {}
        for team, pls in by_team.items():
            rep = reconcile_team(pls, w, team=team)
            g["recon"][team] = rep
            summary["teams"] += 1
            for fl in rep["flags"]:
                summary["flags"][fl] = summary["flags"].get(fl, 0) + 1
            if rep["tripped"]:
                summary["tripped"].append({"team": team, "game": gid,
                                           "gaps": {k: v["gap_rel"] for k, v in rep["identities"].items()
                                                    if abs(v["gap_rel"]) > TRIPWIRE[k]}})
            for p in pls:
                shifts.setdefault((p.get("pos") or "").upper(), []).append(
                    dk_mean(p["recon"]) - dk_mean(p.get("means") or {}))
    for pos, xs in shifts.items():
        a = sorted(abs(x) for x in xs)
        summary["dk_shift_by_pos"][pos] = {"n": len(xs), "mean": round(sum(xs) / len(xs), 3),
                                           "abs_median": round(a[len(a) // 2], 3),
                                           "abs_p90": round(a[min(len(a) - 1, int(0.9 * len(a)))], 3),
                                           "abs_max": round(a[-1], 3)}
    return summary
