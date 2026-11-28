"""EXPERIMENTAL discrete-stat allocation for the NFL week board (2026-11-27; the
owner: "investigate why the player simulator generates continuous reception
counts ... evaluate an experimental discrete-stat allocation mechanism ...
preserve the production engine until any alternative passes validation").

NOT a production path. Nothing in the app imports this module (a guard pins
it): the week board, the Pick 6 view and the ledger's histograms all run the
legacy model, nfl_dfs_sim.simulate_game, unchanged. It exists for
research/nfl_discrete_alloc_eval.py, which measures it against the legacy
model on the same Sleeper projections (research/reports/nfl_discrete_alloc.md).

Why the legacy board is continuous (nfl_dfs_sim.simulate_game): a player's
yards and receptions are his Sleeper MEAN times shared latent multipliers --
`pass_yd = m["pass_yd"] * pass_f`, `rec = m["rec"] * rec_f` with
`rec_f = env * ql * (1 - _SCRIPT_PASS * sc)` and, for rushing, a per-player
`max(0, gauss(1, _RUSH_SD))` -- so 4.37 catches is a scaled mean, not a count
of events: no target, catch or carry is ever drawn. Touchdowns, interceptions
and lost fumbles are Poisson draws (integers), independent of the yardage.
Attempts, completions, targets and carries are not simulated at all. The
showdown experiment's `discrete=True` rounds the scaled means to whole numbers
(`int(x + 0.5)`) -- an approximation applied to the continuous value, not an
event count.

This model keeps the legacy model's latents EXACTLY (the same environment,
quarterback day, game script and rusher volume factors, so its correlation
structure starts from the same place and its means are the legacy means in
expectation) and makes every count an event count:

  * targets: Poisson(target mean x the team's passing factor) per receiver --
    equivalent in law to a Poisson team total dealt multinomially by target
    share, so the receivers' counts sum to the team's exactly;
  * receptions: Binomial(targets, catch rate);
  * receiving yards: one integer draw PER CATCH, mean-exact (a gamma-shaped
    yards-per-catch, shape YPR_SHAPE, turned into an integer by unbiased
    random rounding: floor + Bernoulli(fraction), so E[yards] is the mean to
    the decimal), summed over the catches;
  * receiving touchdowns: Binomial(catches, TD rate per catch);
  * the quarterback's completions, passing yards and passing touchdowns ARE
    the sums over his receivers' catches (plus an "other receivers" bucket for
    the part of his projection Sleeper does not assign to a listed player), in
    every world; attempts are targets plus untargeted throws (nfl_recon's
    target factor);
  * carries: Poisson(carry mean x the rusher's legacy factor); rushing yards
    one integer draw PER CARRY (a gamma shifted to allow losses, shape
    YPC_SHAPE, floor RUSH_FLOOR, mean-exact by unbiased rounding); rushing
    touchdowns Binomial(carries, TD rate per carry);
  * interceptions and lost fumbles: the legacy Poisson draws.

Every distribution shape below is STATED, not fitted: this is a development
experiment, and its parameters would need a registered test before any board
reads it. Numpy (the PC; the server never runs it)."""
try:
    import numpy as np
except ImportError:                     # CI's runner has no numpy; the guards say when they skip
    np = None

# the switch nothing sets: the module is reached only by the research harness and its guards
ENABLED_IN_PRODUCTION = False

TARGET_FACTOR = 0.95    # targets per pass attempt: nfl_recon's measured target factor (0.950)
CATCH_DEFAULT = 0.65    # catch rate when Sleeper projects receptions but no targets
YPC_DEFAULT = 4.3       # yards per carry when Sleeper projects rushing yards but no carries
YPR_SHAPE = 1.5         # gamma shape of yards per catch: CV 0.82 (stated, not fitted)
YPC_SHAPE = 1.2         # gamma shape of (yards per carry - RUSH_FLOOR) (stated, not fitted)
RUSH_FLOOR = -3.0       # the most a carry can lose, before rounding (stated, not fitted)


def _legacy():
    import nfl_dfs_sim as S
    return S


def _ur(rng, y):
    """Unbiased random rounding: floor(y) + Bernoulli(frac(y)), so E is y exactly."""
    f = np.floor(y)
    return (f + (rng.random(y.shape) < (y - f))).astype(np.int64)


def _per_event_sum(rng, counts, mean, shape, floor=0.0):
    """Sum over each world's `counts` events of an integer draw per event with
    mean `mean`: floor + (mean - floor) * Gamma(shape, 1/shape), unbiased-rounded."""
    counts = np.asarray(counts, dtype=np.int64)
    m = int(counts.max()) if counts.size else 0
    if m == 0 or mean is None:
        return np.zeros(counts.shape, dtype=np.int64)
    g = rng.gamma(shape, 1.0 / shape, size=(counts.size, m))
    y = floor + (mean - floor) * g
    yi = _ur(rng, y)
    mask = np.arange(m)[None, :] < counts[:, None]
    return (yi * mask).sum(axis=1)


def _dk(c):
    """DraftKings classic points for component arrays (dk_scoring.NFL_OFF, the
    legacy scorer's table, bonuses per world)."""
    import dk_scoring
    o = dk_scoring.NFL_OFF
    g = lambda k: c.get(k, 0)  # noqa: E731
    return (o["pass_yd"] * g("pass_yd") + o["pass_td"] * g("pass_td") + o["int"] * g("int")
            + o["pass_300"] * (np.asarray(g("pass_yd")) >= 300)
            + o["rush_yd"] * g("rush_yd") + o["rush_td"] * g("rush_td") + o["rush_100"] * (np.asarray(g("rush_yd")) >= 100)
            + o["rec"] * g("rec") + o["rec_yd"] * g("rec_yd") + o["rec_td"] * g("rec_td")
            + o["rec_100"] * (np.asarray(g("rec_yd")) >= 100) + o["fumble_lost"] * g("fum"))


def simulate_game(game, n=4000, rng=None, pass_latent_scale=1.0):
    """One game, n worlds, on the legacy model's inputs (nfl_dfs_sim.weekly_games'
    game dict). Returns {"players": [{name, pos, team, comps {stat: int (n,)},
    dk (n,)}], "conservation": {team: {"cmp", "pass_yd", "pass_td"}: worlds
    where the quarterback's line differs from his receivers' sums -- 0 by
    construction}}.

    pass_latent_scale (the registered test's candidate B, research/nfl_wb_dist.py):
    the team passing multiplier's deviation from 1 is scaled by it. The per-event
    draws already carry the noise the legacy multiplier stood in for, so the
    quarterback's yards ran twice too wide (realized over model 0.473); 1.0 is the
    allocator exactly as it was, draw for draw."""
    if np is None:
        raise RuntimeError("numpy is required for the experimental allocator")
    S = _legacy()
    rng = rng if rng is not None else np.random.default_rng()
    players = game["players"]
    teams = list(game.get("teams") or [])
    for p in players:
        if p.get("team") and p["team"] not in teams:
            teams.append(p["team"])
    # ---- the legacy latents, drawn the same way (nfl_dfs_sim.simulate_game)
    env = np.maximum(0.4, rng.normal(1.0, S._ENV_SD, n))
    ql = {t: np.maximum(0.0, rng.normal(1.0, S._QB_SD, n)) for t in teams}
    sc = {t: rng.normal(0.0, S._SCRIPT_SD, n) for t in teams}
    if len(teams) >= 2:
        sc[teams[1]] = -sc[teams[0]]
    comps = [{k: np.zeros(n, dtype=np.int64) for k in ("pass_att", "pass_cmp", "pass_yd", "pass_td", "int",
                                                       "rush_att", "rush_yd", "rush_td", "rec_tgt", "rec",
                                                       "rec_yd", "rec_td", "fum")} for _ in players]
    cons = {}
    for t in teams:
        idx = [i for i, p in enumerate(players) if p.get("team") == t]
        pass_f = env * ql[t] * (1 - S._SCRIPT_PASS * sc[t])
        if pass_latent_scale != 1.0:
            pass_f = np.maximum(0.0, 1.0 + pass_latent_scale * (pass_f - 1.0))
        qbs = [i for i in idx if (players[i].get("pos") or "").upper() == "QB"
               and (players[i]["means"].get("pass_yd") or 0) > 0]
        q = max(qbs, key=lambda i: players[i]["means"]["pass_yd"]) if qbs else None
        # receivers: (index or None for the other bucket, targets, catch rate, yards/catch, TD/catch)
        recv = []
        for i in idx:
            m = players[i]["means"]
            if i == q or (m.get("rec") or 0) <= 0:
                continue
            tau = m["rec_tgt"] if (m.get("rec_tgt") or 0) > 0 else m["rec"] / CATCH_DEFAULT
            recv.append((i, tau, min(0.95, max(0.05, m["rec"] / tau)), m["rec_yd"] / m["rec"],
                         min(0.5, max(0.0, (m.get("rec_td") or 0) / m["rec"]))))
        if q is not None:
            mq = players[q]["means"]
            t_qb = (mq.get("pass_att") or 0) * TARGET_FACTOR or sum(r[1] for r in recv)
            tau_o = max(0.0, t_qb - sum(r[1] for r in recv))
            rec_o = max(0.0, (mq.get("pass_cmp") or 0) - sum(r[1] * r[2] for r in recv))
            if tau_o > 0 and rec_o > 0:
                yd_o = max(0.0, mq["pass_yd"] - sum(players[r[0]]["means"]["rec_yd"] for r in recv))
                td_o = max(0.0, (mq.get("pass_td") or 0) - sum(players[r[0]]["means"].get("rec_td") or 0 for r in recv))
                recv.append((None, tau_o, min(0.9, max(0.3, rec_o / tau_o)), max(3.0, yd_o / rec_o),
                             min(0.5, td_o / rec_o)))
        parts = []                                  # every receiver's (tgt, rec, yd, td), the other bucket too
        for i, tau, cr, ypr, tdr in recv:
            tgt = rng.poisson(tau * pass_f)
            rec = rng.binomial(tgt, cr)
            yd = _per_event_sum(rng, rec, ypr, YPR_SHAPE)
            td = rng.binomial(rec, tdr)
            parts.append((tgt, rec, yd, td))
            if i is not None:
                comps[i].update(rec_tgt=tgt, rec=rec, rec_yd=yd, rec_td=td)
        if q is not None:
            zero = np.zeros(n, dtype=np.int64)
            tgt_t, rec_t, yd_t, td_t = (sum((pt[j] for pt in parts), zero) for j in range(4))
            comps[q].update(pass_att=tgt_t + rng.binomial(tgt_t, 1.0 - TARGET_FACTOR),
                            pass_cmp=rec_t, pass_yd=yd_t, pass_td=td_t)
            # checked against the receivers' arrays as stored, the other bucket's beside them
            other = [pt for (i, *_), pt in zip(recv, parts) if i is None]
            listed = [comps[i] for i, *_ in recv if i is not None]

            def summed(key, j):
                return sum((c[key] for c in listed), zero) + sum((pt[j] for pt in other), zero)
            cons[t] = {"cmp": int((comps[q]["pass_cmp"] != summed("rec", 1)).sum()),
                       "pass_yd": int((comps[q]["pass_yd"] != summed("rec_yd", 2)).sum()),
                       "pass_td": int((comps[q]["pass_td"] != summed("rec_td", 3)).sum())}
        # rushing: per rusher, the legacy rusher factor
        for i in idx:
            m = players[i]["means"]
            if (m.get("rush_yd") or 0) <= 0 and (m.get("rush_att") or 0) <= 0:
                continue
            rho = m["rush_att"] if (m.get("rush_att") or 0) > 0 else max(m["rush_yd"], 0.0) / YPC_DEFAULT
            if rho <= 0:
                continue
            rush_f = env * (1 + S._SCRIPT_RUSH * sc[t]) * np.maximum(0.0, rng.normal(1.0, S._RUSH_SD, n))
            car = rng.poisson(rho * rush_f)
            ypc = (m.get("rush_yd") or 0) / rho
            comps[i].update(rush_att=car, rush_yd=_per_event_sum(rng, car, ypc, YPC_SHAPE, RUSH_FLOOR),
                            rush_td=rng.binomial(car, min(0.5, max(0.0, (m.get("rush_td") or 0) / rho))))
        for i in idx:                                   # the legacy Poisson draws
            m = players[i]["means"]
            comps[i]["int"] = rng.poisson(m.get("int") or 0, n) if i == q else comps[i]["int"]
            comps[i]["fum"] = rng.poisson(m.get("fum") or 0, n)
    out = [{"name": p["name"], "pos": p.get("pos"), "team": p.get("team"), "comps": comps[i], "dk": _dk(comps[i])}
           for i, p in enumerate(players)]
    return {"players": out, "conservation": cons, "n": n}


def mtl(values, line):
    """{"more", "tie", "less"} of integer event counts at a line: EXACT here --
    each world's stat is a count, so a tie is a world that lands on the line."""
    v = np.asarray(values)
    more, tie = int((v > line).sum()), int((v == line).sum())
    return {"more": more / v.size, "tie": tie / v.size, "less": (v.size - more - tie) / v.size,
            "counts": (more, tie, v.size - more - tie)}
