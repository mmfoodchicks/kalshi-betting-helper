"""The constrained team simulator, "constrained-team" (Stage 2D): an
ALTERNATE mode beside the production legacy-latent model, never the
default until it passes the blind 2025 validation (Stage 2E).

The legacy model draws every player's stat line around his own
projection and rescales his points to the Sleeper mean afterwards; a
team's receivers can all have a big day at once, and a quarterback's
yards need not equal his receivers'. This model builds each simulated
world the way a game is played:

  game environment (shared pace)  ->  each team's passing volume and
  game script  ->  targets shared out among the receivers by noisy
  shares (target competition: one man's extra targets are another's
  lost ones)  ->  receptions by each man's catch rate on a team-wide
  accuracy day  ->  yards per reception on a team-wide efficiency day
  with per-player big-play noise  ->  the team's passing touchdowns,
  allocated among the men who caught passes  ->  the same for carries,
  rushing yards and rushing touchdowns  ->  DraftKings scoring per world
  ->  the defense from what the opposing offense did in that world.

The quarterback's line IS the sum of his receivers' lines in every world
(completions, yards, touchdowns), so the reconciled projections
(nfl_recon, `recon` on every player) are the inputs, and no player's
points are rescaled afterwards: the mean of a player's array is what
the model says, not what Sleeper said. Every parameter below is a
named, fitted quantity (research/data/csim_params.json, Stage 2E,
hashed); until that artifact exists the module carries provisional
values and stamps them "unfitted". Vectorised over worlds with numpy
(the PC builds the boards; the server has no numpy and never runs
this). Seeded: a board built by this model is reproducible draw for
draw from its stamp."""
import hashlib
import json
import os

import dk_scoring
import errlog

try:
    import numpy as np
except ImportError:                     # the server: no numpy, never this model
    np = None

SIM_MODEL = "constrained-team"
SIM_MODEL_VERSION = 1
PARAMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", "data", "csim_params.json")

# Provisional values, replaced by the fitted artifact when it exists.
# Each is a named quantity with one job:
#   env_sd       shared game pace: lognormal sd of the multiplier both teams' passing volume rides on
#   env_rush     how much of that pace reaches the running game (exponent; 0 = none)
#   script       game script on passing: the leading team throws less, the trailing team more
#   script_rush  game script on rushing: the leading team runs more
#   att_sd       a team's own passing-volume day (lognormal sd on attempts)
#   share_sd     target competition: lognormal noise on each receiver's target share before renormalising
#   cmp_sd       the team's accuracy day: shift on the logit of every receiver's catch rate
#   eff_sd       the team's efficiency day: lognormal sd on yards per reception for everyone
#   ypr_sd       a receiver's own big-play noise on yards per reception (shrinks with his receptions)
#   td_vol       how team passing touchdowns scale with the volume ratio (exponent)
#   td_eff       how they scale with the efficiency day (exponent)
#   rush_sd      a team's rushing-volume day
#   rshare_sd    carry competition among the backs
#   ypc_sd       a rusher's own yards-per-carry noise (shrinks with carries)
#   rtd_vol      how team rushing touchdowns scale with the rushing-volume ratio
DEFAULT_PARAMS = {"env_sd": 0.12, "env_rush": 0.5, "script": 0.10, "script_rush": 0.12, "att_sd": 0.16, "share_sd": 0.35, "cmp_sd": 0.25,
                  "eff_sd": 0.15, "ypr_sd": 0.45, "td_vol": 1.0, "td_eff": 0.5, "rush_sd": 0.20, "rshare_sd": 0.30, "ypc_sd": 0.50, "rtd_vol": 1.0}
TARGET_FACTOR_FALLBACK = 0.95           # team targets over attempts when the reconciliation weights are absent
COMPONENTS = ("pass_att", "pass_cmp", "pass_yd", "pass_td", "int", "rush_att", "rush_yd", "rush_td", "rec_tgt", "rec", "rec_yd", "rec_td", "fum")


def params_hash(params):
    return hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _load_params(path=PARAMS_PATH):
    """The fitted artifact, or the provisional values marked as such."""
    try:
        with open(path) as fh:
            art = json.load(fh)
        if params_hash(art["params"]) != art.get("hash"):
            errlog.note("CSIM-params-hash", msg=f"{path}: content does not match its hash")
            return {"params": dict(DEFAULT_PARAMS), "fitted": False, "hash": params_hash(DEFAULT_PARAMS), "meta": {}}
        return {"params": {k: float(v) for k, v in art["params"].items()}, "fitted": True, "hash": art["hash"], "meta": art.get("meta") or {}}
    except FileNotFoundError:
        return {"params": dict(DEFAULT_PARAMS), "fitted": False, "hash": params_hash(DEFAULT_PARAMS), "meta": {}}
    except Exception as e:
        errlog.note("CSIM-params", e)
        return {"params": dict(DEFAULT_PARAMS), "fitted": False, "hash": params_hash(DEFAULT_PARAMS), "meta": {}}


PARAMS = _load_params()


def available():
    return np is not None


def sim_stamp(n=None, seed=None, params=None, preseason=False):
    """What a board built by this model records: name, version, the
    parameter set (fitted or provisional, with its hash and the fit's
    seasons), the reconciliation stamp, the seed, and the fact that no
    marginal is pinned to anything."""
    import nfl_recon
    P = params if params is not None else PARAMS
    return {"model": SIM_MODEL, "version": SIM_MODEL_VERSION, "projections": "sleeper-weekly, reconciled (nfl_recon)",
            "params": dict(P["params"]), "params_fitted": bool(P["fitted"]), "params_hash": P["hash"][:16],
            "params_fit": {k: P["meta"].get(k) for k in ("train_seasons", "commit", "base_seed", "built")} if P["fitted"] else None,
            "reconciliation": nfl_recon.recon_stamp(),
            "touchdowns": "team Poisson on volume and efficiency, allocated among the men who caught (carried) the ball",
            "receiving_noise": "shared game, team volume, team accuracy and team efficiency days; noisy target shares; per-player big-play noise",
            "marginals": "not pinned: each player's mean is what the model produces from the reconciled line",
            "dst": "components of the opposing offense in the same world, shifted to the Sleeper mean (nfl_dfs_sim)",
            "seed": (int(seed) if seed is not None else None), "n": (int(n) if n else None), "preseason": bool(preseason)}


def _line(p):
    """The input line: the reconciled components where present, else the means."""
    m = p.get("recon") or p.get("means") or {}
    base = p.get("means") or {}
    out = {}
    for c in COMPONENTS:
        v = m.get(c)
        if v is None:
            v = base.get(c, 0.0)
        out[c] = max(0.0, float(v or 0.0))
    return out


def _split_int(total, shares):
    """Split an integer team total across k quarterbacks by largest
    remainder, so every share is a whole number and the k shares still sum
    to the team total exactly. A fractional split (total * share) put a
    quarterback on 147.3 passing yards, which is not a box-score line and
    knocks the DraftKings score off its real lattice; with one quarterback,
    which is nearly every team, this returns the total untouched."""
    total = np.asarray(total)
    k = len(shares)
    if k == 1:
        return [total.astype(np.int64)]
    exact = np.outer(np.asarray(shares, dtype=np.float64), total.astype(np.float64))
    base = np.floor(exact).astype(np.int64)
    rem = total.astype(np.int64) - base.sum(axis=0)
    frac = exact - base
    order = np.argsort(-frac, axis=0)
    for r in range(k):                      # hand the leftovers to the biggest fractions
        rows = order[r]
        base[rows, np.arange(base.shape[1])] += (rem > r)
    return [base[j] for j in range(k)]


def _multinomial_capped(rng, N, shares, cap, rounds=12):
    """Counts (k, n) of N trials over k categories with weights (k, n), where
    category i can hold at most cap[i]. The budget is first cut to the room
    that exists, then dealt out in rounds: each round allocates what is still
    owed among the categories that still have room, and whatever overflows a
    category is carried to the next round rather than thrown away.

    The contract, stated so "nothing is lost" cannot be read as "the budget
    is always honoured":

        allocated = min(drawn_budget, physical_capacity)
        drawn_budget = allocated + impossible_surplus

    A budget larger than the catches and carries available is NOT forced onto
    the field; the part that cannot fit is surplus, reported through
    `_capped_short` as `td_short`, and the two numbers always add back to what
    was drawn. Zero lost means nothing vanished unrecorded, not that every
    drawn touchdown was scored.

    The previous form was `np.minimum(_multinomial(...), cap)`, which simply
    DELETED the overflow. Measured on a synthetic two-team game against the
    frozen parameters, 100,000 worlds: that silently destroyed 0.28% of the
    drawn passing-touchdown budget and 0.08% of the rushing budget. The
    quarterback was then handed the post-clipping team total, so the
    QB-equals-receivers identity still held and the invariant checker saw
    nothing wrong -- the touchdowns were gone before it looked. A team's
    drawn touchdowns now either land on a player who can hold them or are
    impossible for want of catches and carries, and `_capped_short` reports
    which."""
    cap = np.asarray(cap)
    room_total = cap.sum(axis=0)
    owed = np.minimum(np.asarray(N).astype(np.int64), room_total.astype(np.int64))
    alloc = np.zeros(cap.shape, dtype=np.int64)
    for _ in range(rounds):
        if not owed.any():
            break
        room = cap - alloc
        w = np.where(room > 0, shares, 0.0)
        live = (owed > 0) & (w.sum(axis=0) > 0)
        if not live.any():
            break
        take = np.minimum(_multinomial(rng, np.where(live, owed, 0), w), room)
        alloc = alloc + take
        owed = owed - take.sum(axis=0)
    return alloc


def _capped_short(N, cap):
    """The impossible surplus: `drawn_budget - physical_capacity` where that
    is positive, per world. A team cannot score four receiving touchdowns on
    three catches, so the surplus is a real constraint rather than a bug, and
    reporting it is what makes `drawn = allocated + surplus` checkable."""
    return np.maximum(np.asarray(N).astype(np.int64) - np.asarray(cap).sum(axis=0).astype(np.int64), 0)


def _multinomial(rng, N, shares):
    """Counts (k, n) of N (n,) trials over k categories with weights (k, n),
    by sequential binomials so nothing depends on numpy's broadcasting
    rules; the last positive-weight category takes whatever is left."""
    k = shares.shape[0]
    out = np.zeros(shares.shape, dtype=np.int64)
    rem = N.astype(np.int64).copy()
    rem_w = shares.sum(axis=0)
    for i in range(k):
        p = np.where(rem_w > 1e-12, shares[i] / np.maximum(rem_w, 1e-12), 0.0)
        p = np.clip(p, 0.0, 1.0)
        out[i] = rng.binomial(rem, p)
        rem = rem - out[i]
        rem_w = np.maximum(rem_w - shares[i], 0.0)
    if rem.any():                                   # rounding left a few: to the biggest share
        j = np.argmax(shares, axis=0)
        out[j, np.arange(len(rem))] += rem
    return out


def _lognormal(rng, sd, size):
    """Mean-one lognormal multiplier."""
    if sd <= 0:
        return np.ones(size)
    return np.exp(rng.normal(0.0, sd, size=size) - 0.5 * sd * sd)


def simulate_game(game, n=4000, rng=None, params=None, target_factor=None, xside=0.0):
    """One game, n worlds. Returns the contract nfl_dfs_sim.player_pool
    reads: players with their point arrays (`arr`), the per-world team
    output (`team_fp`) and the DraftKings components each offense
    produced (`team_def`), plus per-player component arrays for the
    invariant checks and the research fits (`components`)."""
    if np is None:
        raise RuntimeError("numpy is required for the constrained simulator")
    P = (params if params is not None else PARAMS)["params"]
    rng = rng if rng is not None else np.random.default_rng()
    if target_factor is None:
        import nfl_recon
        w = nfl_recon.WEIGHTS
        target_factor = float(w["weights"]["target_factor"]["value"]) if w else TARGET_FACTOR_FALLBACK
    players = game["players"]
    teams = list(game.get("teams") or [])
    for p in players:
        if p.get("team") not in teams and p.get("team"):
            teams.append(p["team"])
    lines = [_line(p) for p in players]
    S = dk_scoring.NFL_OFF
    # ---- shared world latents ----
    env = _lognormal(rng, P["env_sd"], n)
    script = rng.normal(0.0, 1.0, size=n)
    # `xside` is a RESEARCH KNOB and is not part of the frozen fit. This
    # model's quarterback-against-opposing-quarterback correlation comes out
    # near 0.03 where the holdout observed 0.235 -- its one disclosed
    # weakness. The first attempt at a knob here simply SHARED the existing
    # per-team efficiency latent across the game; measured on the live slate,
    # sharing it completely only reached 0.074, because that latent's fitted
    # spread is too small to carry the covariance. The architecture cannot
    # reach the observed figure by re-weighting what it already has, which is
    # itself a finding: it needs a new term.
    #
    # So `xside` is the standard deviation of a game-level scoring latent
    # applied to BOTH offences on top of their own efficiency days -- the
    # shared game latent proposed in section V of the audit report. At the
    # default 0.0 nothing is drawn and the frozen path is bit-identical; no
    # fitted artifact carries this and nothing may be promoted from a sweep
    # over it. It exists so the sensitivity of a strategy conclusion to a
    # known, already-measured miss can itself be measured.
    xside = float(xside or 0.0)
    eff_game = (np.exp(xside * rng.normal(0.0, 1.0, size=n) - 0.5 * xside * xside)
                if xside > 0 else None)
    comps = {i: {} for i in range(len(players))}
    pts = [np.zeros(n) for _ in players]
    team_out = {}
    short = {}          # budget the opportunities could not hold
    budget = {}         # the team touchdown budget as drawn, before allocation
    for ti, t in enumerate(teams):
        s = script if ti == 0 else -script           # the two scripts are opposite
        idx = [i for i, p in enumerate(players) if p.get("team") == t]
        if not idx:
            continue
        qbs = [i for i in idx if (players[i].get("pos") or "").upper() == "QB"]
        # ---- passing volume: attempts -> targets ----
        att_mean = sum(lines[i]["pass_att"] for i in qbs)
        pass_vol = env * _lognormal(rng, P["att_sd"], n) * np.clip(1.0 - P["script"] * s, 0.2, None)
        recv = [i for i in idx if lines[i]["rec_tgt"] > 0]
        n_tgt = rng.poisson(np.maximum(att_mean * target_factor * pass_vol, 0.0)) if att_mean > 0 and recv else np.zeros(n, dtype=np.int64)
        # ---- targets by noisy shares ----
        if recv:
            base = np.asarray([lines[i]["rec_tgt"] for i in recv])[:, None]
            noise = np.exp(rng.normal(0.0, P["share_sd"], size=(len(recv), n)))
            shares = base * noise
            tgt = _multinomial(rng, n_tgt, shares)
            # ---- receptions: each man's catch rate on the team's accuracy day ----
            crate = np.asarray([min(0.95, max(0.25, lines[i]["rec"] / lines[i]["rec_tgt"])) for i in recv])[:, None]
            logit = np.log(crate / (1 - crate)) + rng.normal(0.0, P["cmp_sd"], size=n)[None, :]
            cr = 1.0 / (1.0 + np.exp(-logit))
            rec = rng.binomial(tgt, cr)
            # ---- yards: yards per reception on the team's efficiency day, per-player big plays ----
            ypr = np.asarray([max(3.0, lines[i]["rec_yd"] / max(lines[i]["rec"], 0.25)) for i in recv])[:, None]
            eff = _lognormal(rng, P["eff_sd"], n)[None, :]
            if eff_game is not None:
                eff = eff * eff_game[None, :]      # the game's own day, shared by both offences
            big = np.exp(rng.normal(0.0, P["ypr_sd"], size=(len(recv), n)) / np.sqrt(np.maximum(rec, 1)) - 0.5 * P["ypr_sd"] ** 2 / np.maximum(rec, 1))
            # An NFL box score is integers. Continuous yardage put scores on an
            # arbitrary grid that rounding to two decimals did not repair, so
            # the model could say nothing honest about tie rates. Rounding
            # here, per player, is what a real stat line does: the team total
            # is the sum of nine integers, not a rounded sum.
            rec_yd = np.rint(rec * ypr * eff * big)
            # ---- passing touchdowns: the team's, on volume and efficiency, among the men who caught ----
            td_mean = sum(lines[i]["rec_td"] for i in recv)
            vol_ratio = np.maximum(n_tgt, 0) / max(att_mean * target_factor, 1e-9)
            lam = td_mean * np.power(np.maximum(vol_ratio, 1e-9), P["td_vol"]) * np.power(eff[0], P["td_eff"])
            n_ptd = rng.poisson(np.maximum(lam, 0.0)); n_ptd_drawn = n_ptd
            td_rate = np.asarray([lines[i]["rec_td"] / max(lines[i]["rec"], 0.25) for i in recv])[:, None]
            td_w = td_rate * np.maximum(rec, 0) + 1e-9 * (rec > 0)
            # a touchdown catch is a catch: a man cannot score more often than
            # he caught, and a budget that will not fit is carried, not binned
            rec_td = _multinomial_capped(rng, n_ptd, td_w, rec)
            ptd_short = _capped_short(n_ptd, rec)
            cmp_team = rec.sum(axis=0); yd_team = rec_yd.sum(axis=0); ptd_team = rec_td.sum(axis=0)
        else:
            tgt = rec = rec_yd = rec_td = np.zeros((0, n))
            cmp_team = np.zeros(n); yd_team = np.zeros(n); ptd_team = np.zeros(n, dtype=np.int64)
            ptd_short = np.zeros(n, dtype=np.int64); n_ptd_drawn = np.zeros(n, dtype=np.int64)
        # ---- rushing: carries by shares, yards per carry, the team's rushing touchdowns ----
        rush = [i for i in idx if lines[i]["rush_att"] > 0]
        if rush:
            ra_mean = sum(lines[i]["rush_att"] for i in rush)
            rush_vol = np.power(env, P["env_rush"]) * _lognormal(rng, P["rush_sd"], n) * np.clip(1.0 + P["script_rush"] * s, 0.2, None)
            n_ra = rng.poisson(np.maximum(ra_mean * rush_vol, 0.0))
            base = np.asarray([lines[i]["rush_att"] for i in rush])[:, None]
            shares = base * np.exp(rng.normal(0.0, P["rshare_sd"], size=(len(rush), n)))
            car = _multinomial(rng, n_ra, shares)
            ypc = np.asarray([lines[i]["rush_yd"] / max(lines[i]["rush_att"], 0.25) for i in rush])[:, None]
            big = np.exp(rng.normal(0.0, P["ypc_sd"], size=(len(rush), n)) / np.sqrt(np.maximum(car, 1)) - 0.5 * P["ypc_sd"] ** 2 / np.maximum(car, 1))
            rush_yd = np.rint(car * ypc * big)      # integers, as a box score is
            rtd_mean = sum(lines[i]["rush_td"] for i in rush)
            lam = rtd_mean * np.power(np.maximum(n_ra / max(ra_mean, 1e-9), 1e-9), P["rtd_vol"])
            n_rtd = rng.poisson(np.maximum(lam, 0.0)); n_rtd_drawn = n_rtd
            rtd_rate = np.asarray([lines[i]["rush_td"] / max(lines[i]["rush_att"], 0.25) for i in rush])[:, None]
            rush_td = _multinomial_capped(rng, n_rtd, rtd_rate * np.maximum(car, 0) + 1e-9 * (car > 0), car)
            rtd_short = _capped_short(n_rtd, car)
            rtd_team = rush_td.sum(axis=0); ryd_team = rush_yd.sum(axis=0)
        else:
            car = rush_yd = rush_td = np.zeros((0, n))
            rtd_team = np.zeros(n, dtype=np.int64); ryd_team = np.zeros(n)
            rtd_short = np.zeros(n, dtype=np.int64); n_rtd_drawn = np.zeros(n, dtype=np.int64)
        # ---- turnovers ----
        ints = {i: rng.poisson(lines[i]["int"] * pass_vol) for i in qbs}
        fums = {i: rng.poisson(np.full(n, lines[i]["fum"])) if lines[i]["fum"] > 0 else np.zeros(n, dtype=np.int64) for i in idx}
        # ---- the quarterback's line is the sum of the receivers' ----
        qb_share = np.asarray([lines[i]["pass_att"] for i in qbs]) / max(att_mean, 1e-9) if qbs else np.zeros(0)
        gv = np.zeros(n, dtype=np.int64)
        # attempts: the targets plus the measured throwaways, as whole throws
        att_team = np.rint(n_tgt / target_factor).astype(np.int64)
        sh = qb_share / max(float(np.sum(qb_share)), 1e-12) if len(qb_share) else qb_share
        a_s = _split_int(att_team, sh); c_s = _split_int(cmp_team.astype(np.int64), sh)
        y_s = _split_int(yd_team.astype(np.int64), sh); t_s = _split_int(ptd_team.astype(np.int64), sh)
        for k, i in enumerate(qbs):
            c = comps[i]
            c["pass_att"] = a_s[k].astype(np.float64)
            c["pass_cmp"] = c_s[k].astype(np.float64); c["pass_yd"] = y_s[k].astype(np.float64)
            c["pass_td"] = t_s[k].astype(np.float64)
            c["int"] = ints[i]
            gv = gv + ints[i]
        for k, i in enumerate(recv):
            c = comps[i]
            c["rec_tgt"] = tgt[k]; c["rec"] = rec[k]; c["rec_yd"] = rec_yd[k]; c["rec_td"] = rec_td[k]
        for k, i in enumerate(rush):
            c = comps[i]
            c["rush_att"] = car[k]; c["rush_yd"] = rush_yd[k]; c["rush_td"] = rush_td[k]
        for i in idx:
            c = comps[i]
            c["fum"] = fums[i]
            gv = gv + fums[i]
            z = np.zeros(n)
            fp = (c.get("pass_yd", z) * S["pass_yd"] + c.get("pass_td", z) * S["pass_td"] + c.get("int", z) * S["int"]
                  + c.get("rush_yd", z) * S["rush_yd"] + c.get("rush_td", z) * S["rush_td"]
                  + c.get("rec", z) * S["rec"] + c.get("rec_yd", z) * S["rec_yd"] + c.get("rec_td", z) * S["rec_td"]
                  + c["fum"] * S["fumble_lost"])
            fp = fp + S["pass_300"] * (c.get("pass_yd", z) >= 300) + S["rush_100"] * (c.get("rush_yd", z) >= 100) + S["rec_100"] * (c.get("rec_yd", z) >= 100)
            pts[i] = fp
        team_out[t] = {"td": (ptd_team + rtd_team).astype(np.int64), "gv": gv, "yd": yd_team + ryd_team, "pa": yd_team,
                       "fp": sum(pts[i] for i in idx)}
        # what the opportunities could not physically hold, so its size is
        # known rather than absorbed: a drawn budget is never quietly binned
        short[t] = {"pass_td": ptd_short, "rush_td": rtd_short}
        budget[t] = {"pass_td": np.asarray(n_ptd_drawn), "rush_td": np.asarray(n_rtd_drawn)}
    out = []
    for i, p in enumerate(players):
        arr = pts[i]
        proj = float(p.get("proj_pts") or 0.0)
        srt = np.sort(arr)
        q = lambda f: float(srt[min(n - 1, int(f * n))])
        out.append({"name": p["name"], "pos": p["pos"], "team": p["team"], "opp": p.get("opp"),
                    "proj_pts": proj, "sim_mean": round(float(arr.mean()), 2),
                    "floor": round(q(0.10), 1), "median": round(q(0.50), 1), "ceiling": round(q(0.90), 1),
                    "boom_pct": round(100.0 * float((arr >= 1.5 * proj).mean()), 1) if proj > 0 else None,
                    "bust_pct": round(100.0 * float((arr <= 0.5 * proj).mean()), 1) if proj > 0 else None,
                    "arr": arr})
    return {"label": game.get("label"), "teams": teams, "players": out, "props": [], "stacks": [], "n_sims": n,
            "team_fp": {t: d["fp"] for t, d in team_out.items()},
            "team_def": {t: {"td": d["td"], "gv": d["gv"], "yd": d["yd"], "pa": d["pa"]} for t, d in team_out.items()},
            "td_budget": budget, "td_short": short,
            "components": comps}


def check_invariants(sim, players, tol=1e-6):
    """Per-world identities on a simulate_game result: each team's
    quarterbacks' completions, yards and touchdowns equal the receivers'
    sums; receptions never exceed targets nor touchdowns receptions;
    nothing negative; DraftKings points recompute from the components.
    Returns {name: True/False} with counts of failing worlds."""
    comps = sim["components"]
    out = {}
    teams = {}
    for i, p in enumerate(players):
        teams.setdefault(p.get("team"), []).append(i)
    for t, idx in teams.items():
        qbs = [i for i in idx if (players[i].get("pos") or "").upper() == "QB" and "pass_yd" in comps[i]]
        recv = [i for i in idx if "rec" in comps[i]]
        if not qbs or not recv:
            continue
        for qc, rc in (("pass_cmp", "rec"), ("pass_yd", "rec_yd"), ("pass_td", "rec_td")):
            a = sum(comps[i][qc] for i in qbs); b = sum(comps[i][rc] for i in recv)
            bad = int((np.abs(a - b) > tol).sum())
            out[f"{t}:{qc}=sum {rc}"] = (bad == 0, bad)
        bad_rec = sum(int((comps[i]["rec"] > comps[i]["rec_tgt"]).sum()) for i in recv)
        bad_td = sum(int((comps[i]["rec_td"] > comps[i]["rec"]).sum()) for i in recv)
        out[f"{t}:receptions within targets"] = (bad_rec == 0, bad_rec)
        out[f"{t}:touchdowns within receptions"] = (bad_td == 0, bad_td)
    neg = sum(int((v < -tol).sum()) for c in comps.values() for v in c.values())
    out["nothing negative"] = (neg == 0, neg)
    # The check that was missing: the allocation against the budget that was
    # DRAWN, not against the post-clipping total the quarterback was handed.
    # Clipping used to delete the overflow before this function looked, so
    # every identity here passed while touchdowns went missing.
    for t, b in (sim.get("td_budget") or {}).items():
        sh = (sim.get("td_short") or {}).get(t, {})
        for key, comp in (("pass_td", "rec_td"), ("rush_td", "rush_td")):
            drawn = np.asarray(b[key]).astype(np.int64)
            allocated = sum(comps[i][comp] for i in teams[t] if comp in comps[i])
            allocated = np.asarray(allocated).astype(np.int64) if np.ndim(allocated) else np.zeros_like(drawn)
            impossible = np.asarray(sh.get(key, np.zeros_like(drawn))).astype(np.int64)
            # drawn_budget = allocated + impossible_surplus, exactly
            lost = int((drawn - impossible - allocated).sum())
            out[f"{t}:{key} drawn = allocated + impossible surplus"] = (lost == 0, lost)
    # And the lattice: a box score is integers, so every component is whole.
    for i, c in comps.items():
        for k, v in c.items():
            v = np.asarray(v)
            bad = int((np.abs(v - np.rint(v)) > 1e-9).sum())
            if bad:
                out[f"player {i}:{k} whole numbers"] = (False, bad)
    out.setdefault("every component is a whole number", (True, 0))
    return out
