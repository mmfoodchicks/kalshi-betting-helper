"""The game-engine DFS worlds, "engine-deep": every simulated world is one
football game, played snap by snap.

Baseball's DFS boards come out of its deep engine: each slate game is played
with the real lineups and every batter's and pitcher's DraftKings points are
read off the box score of that simulated game, so a stack, its opposing
pitcher and the game total are one event. Football's never did. The legacy
model (nfl_dfs_sim) draws each player's line around his own projection with a
few shared latents, and measured on 2025 it puts two receivers of one team
at +0.54 correlation where real games show 0.02; the constrained model
(nfl_dfs_csim) balances each team's books but leaves the two offences nearly
independent (quarterback against quarterback 0.03, real 0.24). Neither knows
what the other team did in the same world, and neither has a scoreboard: a
defense was scored against touchdowns counted from player draws and field
goals drawn from a league rate.

Here the game is played first, then dealt:

  1. LEVEL. Each team's expected points are the Sleeper drive model's
     (nfl_game_sim: this week's projections, personnel and calibration) and
     the deep engine's own (the units against each other), blended at
     nfl_game_sim._DEEP_WEIGHT -- the weight the slate's headline already uses
     and the out-of-sample record supports (research/reports/
     nfl_deep_validation.md). The deep engine's offences are re-levelled
     until it plays at that target (the `level_off` lever, elasticity
     nfl_deep_data._ENGINE_PTS_ELASTICITY).
  2. GAME. nfl_deep_sim.play_game, once per world: possessions, the clock,
     fourth downs, the script, both offences against both defences. Each
     world's box score carries every quantity DraftKings pays on: attempts,
     completions, yards, touchdowns by type, interceptions, fumbles, sacks,
     field goals by distance, tries, return touchdowns, safeties, blocks.
  3. DEAL. The box is shared out among the players by Sleeper's projected
     usage (the reconciled lines, nfl_recon) with the constrained model's
     fitted competition noise (target shares, carry shares, big-play
     yardage; research/data/csim_params.json, fit on 2022-2024): every
     completion is a catch by someone, every touchdown a catch or a carry,
     the quarterback's line is his receivers' line, team yards are split to
     the yard.
  4. SCORE. DraftKings' own scorer on every integer line; the defense from
     what the OTHER offence did in that same game (points it scored with the
     ball, sacks, takeaways) plus its own return touchdowns, safeties and
     blocks; the kicker from his own field goals by distance and his tries.

Player means are therefore what the game says, not pinned to Sleeper's
number (Sleeper's projection stays the `proj` the field reads, as in every
model). Needs numpy (the PC builds the boards); the server never runs it."""
import random
import zlib

import dk_scoring
import errlog

try:
    import numpy as np
except ImportError:                     # the server: no numpy, never this model
    np = None

SIM_MODEL = "engine-deep"
SIM_MODEL_VERSION = 1

# Anchoring the deep engine to the blended level: one measuring pass at the
# profiles' own level (which IS the deep engine's expectation for the
# matchup), then two corrective passes. 800 games put the mean of a team's
# points within about +/-0.35 of where it is (sd ~10 a side), and the lever is
# smooth, so two steps land inside that noise.
ANCHOR_N = 800
ANCHOR_ITERS = 2
# A one-step level correction is capped: no profile is moved more than this
# per step, so one odd measuring pass cannot fling a team.
_STEP_CLAMP = (0.75, 1.33)
# The passing-VOLUME anchor (owner, 2026-10-16: "anchor passing volume"): each
# team's mean pass attempts are moved to (1 - ATT_WEIGHT) x the engine's own +
# ATT_WEIGHT x Sleeper's projected team attempts, through a pass-rate shift on
# the logit of every play call (nfl_deep_sim profile key `call_shift`),
# iterated with the points anchor. The engine throws too often: team-game
# attempts, engine / Sleeper / real, 2024 33.64 / 31.89 / 32.81 (+2.5% on
# reality; the flawed first runs read 34.15, ~4%, on worlds drawn from the
# wrong tables -- see play_worlds_parallel).
# 1 since 2026-10-16: the weight the 2024 rule chose (ATT_WEIGHT_CHOSEN,
# below), shipped because the rule written before the 2026 test passed every
# link on its 63 games (weeks 2-5; tables fit on 2023-2025; run at 2577f9e):
#
#                       offence CRPS   pair RMSE   DST CRPS   K CRPS
#   unanchored (w 0)    3.8595         0.1395      2.8644     2.5678
#   anchored (w 1)      3.8535         0.1359      2.8344     2.5687
#
# Paired, anchored minus unanchored (SE clustered by game): offence -0.0060
# +/- 0.0150, defenses -0.030 +/- 0.026, kickers +0.001 +/- 0.022 -- inside
# the noise of 63 games; the rule decided on the point estimates, as written.
# The sign is the same on all three seasons (2024 -0.0009 +/- 0.0104, where it
# was chosen; 2025 -0.0172 +/- 0.0096, SEEN). Stated against it: 2026's teams
# have thrown MORE than Sleeper projected (33.56 attempts against 31.11), so
# on this season the anchor moves volume away from what happened (31.21 against
# the unanchored 32.86) and its WR1 further below the real 15.31 (13.56
# against 14.07). These 63 games had been read once before, by the flawed
# first pass, whose verdict was the same (research/reports/nfl_dfs_engine.md).
ATT_WEIGHT = 1.0
# The 2024 rule's choice, frozen BEFORE the 2026 test reads a game: the
# harness refuses any other weight for seasons >= 2025. On 2024 (256 games,
# the same seeds; the w = 0 arm reproduces the ENV grid's 0.14 arm exactly),
# the corrected run (b292fad):
#
#   w     offence CRPS   PIT KS   pair RMSE   cross-team RMSE   DST CRPS   K CRPS   attempts
#   0     3.9252         0.0291   0.0515      0.0259            2.9011     2.5220   33.64
#   0.5   3.9288         0.0299   0.0502      0.0250            2.9016     2.5248   32.71
#   1     3.9242         0.0337   0.0482      0.0250            2.9026     2.5285   31.83
#
# (real 32.81, Sleeper 31.89.) The rule (lowest offence CRPS among weights
# whose pair and cross-team RMSE stay within 0.01 of w = 0's) chose 1, by
# 0.0010: paired against w = 0 its offence CRPS is -0.0009 +/- 0.0104 (SE
# clustered by game) -- no measurable gain on 2024; the rule decides on the
# point estimate, as written. The flawed first run had chosen 0.5 (3.9222
# against 3.9328). Pulling attempts to Sleeper's moves them BELOW 2024's real
# volume (31.83 against 32.81), which is what its PIT pays for, and why the
# decision belongs to a season it was not chosen on.
ATT_WEIGHT_CHOSEN = 1.0
# A pass-rate step is capped on the logit, for the same reason.
_CALL_STEP = 0.6
# The game's shared passing environment (nfl_deep_sim.play_game env_sd): one
# draw per game moving both offences' yards per completion and completion
# rate together -- wind, rain, a dome, a crew. The engine already matched the
# real game on everything else across the two teams (2023-2025, 816 games:
# points -0.055 real vs -0.045, plays -0.53 vs -0.53, rushes -0.56 vs -0.59,
# drives +0.85 vs +0.85, attempts vs the opponent's points +0.29 vs +0.32)
# and missed exactly this: the two teams' passing yards correlate +0.18 in
# real games and +0.03 in the engine. Chosen on 2024 by the rule written in
# research/nfl_dfs_engine_eval.py before any 2025 run (the grid value with
# the smallest cross-team pair RMSE among those within 0.5% of env-0's CRPS).
# The corrected run (2026-10-16, b292fad: worlds on the refit 2023 tables;
# the first run drew them from the shipped ones -- play_worlds_parallel):
#
#   env    offence CRPS   pair RMSE   cross-team RMSE   QB1/opp QB1 (obs .176)   points corr in a game
#   0.00   3.9292         0.0946      0.1396            0.003                    -0.106
#   0.05   3.9339         0.0842      0.1193            0.032
#   0.08   3.9291         0.0698      0.0887            0.079
#   0.11   3.9279         0.0534      0.0499            0.144
#   0.14   3.9252         0.0515      0.0259            0.206                    +0.055
#
# (the constrained model: 3.9018 / 0.0686 / 0.0996 / 0.029, in-sample on
# 2024; legacy 4.0758 / 0.2715 / 0.1405 / 0.171). The grid's edge wins
# again, now clearly (the flawed run had it by a hair over 0.11, 0.0332
# against 0.0343); frozen at the rule's answer rather than re-gridded,
# because 2024 is the only season it may be chosen on. The last column is
# the median over games of the two teams' points correlation inside the
# worlds (world_summary): the term moves it from -0.106 to +0.055.
ENV_SD = 0.14

# The quantities read off each side's box, per world (counters of
# nfl_deep_sim._box); `safety_conceded` is the box's ends["safety"].
BOX_KEYS = ("pts", "pass_att", "comp", "pass_yds", "td_pass", "int", "sacks", "rush_att", "rush_yds",
            "td_rush", "fum_lost", "kneels", "td_ret", "ret_pts", "blk", "fg_made", "fg_0_39",
            "fg_40_49", "fg_50p", "xp_made", "two_made", "drives", "plays")


def available():
    return np is not None


# ---- the game -----------------------------------------------------------------
def _engine_pts_elasticity():
    import nfl_deep_data
    return float(nfl_deep_data._ENGINE_PTS_ELASTICITY)


def play_worlds(hp, ap, n, rng, T=None, neutral=False, env_sd=None):
    """n games of away @ home on the python Random `rng`; two dicts of int
    arrays (n,) keyed by BOX_KEYS plus `safety_conceded`, home first.
    `env_sd` None is this module's ENV_SD (the shared passing environment)."""
    import nfl_deep_sim as E
    T = T or E.tables()
    env = ENV_SD if env_sd is None else float(env_sd)
    cols = [{k: [0] * n for k in BOX_KEYS + ("safety_conceded",)} for _ in (0, 1)]
    for i in range(n):
        g = E.play_game(hp, ap, rng, T, neutral=neutral, env_sd=env)
        for side, b in ((0, g["h"]), (1, g["a"])):
            c = cols[side]
            for k in BOX_KEYS:
                c[k][i] = b[k]
            c["safety_conceded"][i] = b["ends"]["safety"]
    return tuple({k: np.asarray(v, dtype=np.int64) for k, v in c.items()} for c in cols)


def _worlds_chunk(args):
    """Process-pool entry: one slice of a game's worlds on its own seed, on
    the caller's tables (None = the shipped ones)."""
    hp, ap, n, seed, neutral, env_sd, T = args
    return play_worlds(hp, ap, n, random.Random(seed), T, neutral, env_sd)


def play_worlds_parallel(hp, ap, n, seed, neutral=False, workers=1, chunk=5000, env_sd=None, T=None):
    """play_worlds split across processes in seeded slices and concatenated.
    The slicing is deterministic in (seed, chunk), so a build is reproducible
    draw for draw whatever `workers` is.

    `T` is the SAME tables the anchor measured on. It used to be dropped here:
    every slice played on the shipped tables (fit on 2023-2026) while the
    anchor had levelled the teams on the caller's. Production never noticed
    (it passes the shipped tables), but the research replay did -- its worlds
    were drawn from tables that contain the season being scored, and on 2024
    they played 1.25 points a team above the level they were anchored to
    (found 2026-10-16; research/reports/nfl_dfs_engine.md). The shipped tables
    are not pickled into every slice: None stands for them."""
    import nfl_deep_sim as E
    tpass = None if (T is None or T is E._T.get("tables")) else T
    seeds = [zlib.crc32(f"{seed}|{j}".encode()) for j in range((n + chunk - 1) // chunk)]
    sizes = [min(chunk, n - j * chunk) for j in range(len(seeds))]
    jobs = [(hp, ap, m, s, neutral, env_sd, tpass) for m, s in zip(sizes, seeds)]
    parts = None
    if workers and workers > 1 and len(jobs) > 1:
        try:
            import concurrent.futures as cf
            with cf.ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as ex:
                parts = list(ex.map(_worlds_chunk, jobs))
        except Exception as e:
            errlog.note("NFLENG-pool", e)
            parts = None
    if parts is None:
        parts = [_worlds_chunk(j) for j in jobs]
    return tuple({k: np.concatenate([p[side][k] for p in parts]) for k in parts[0][side]} for side in (0, 1))


def _passing(box):
    """(mean attempts, mean plays, mean sacks) of one side's measuring pass."""
    return float(box["pass_att"].mean()), float(box["plays"].mean()), float(box["sacks"].mean())


def _call_step(att, plays, sacks, target):
    """The logit shift that moves mean attempts from `att` to `target`: the
    derivative of dropbacks in the shift is plays x p(1 - p) (p the dropback
    rate), times the share of dropbacks that are attempts, not sacks."""
    drop = att + sacks
    if plays <= 0 or drop <= 0:
        return 0.0
    p = min(0.95, max(0.05, drop / plays))
    slope = max(5.0, plays * p * (1.0 - p) * (att / drop))
    return min(_CALL_STEP, max(-_CALL_STEP, (float(target) - att) / slope))


def anchor(hp, ap, targets, rng, T=None, neutral=False, n=ANCHOR_N, iters=ANCHOR_ITERS, start=None,
           env_sd=None, att_targets=None, start_box=None):
    """Copies of the two profiles with each OFFENCE re-levelled so the engine's
    mean points land on `targets` (home, away) -- and, when `att_targets`
    (home, away) is given, its pass rate moved so its mean attempts land on
    those too. Returns (hp, ap, report); the report carries the engine's own
    expectation before anchoring (`deep`), the targets, and where the last
    measuring pass landed. `start` (and `start_box`, the two boxes it came
    from) is that first measurement when the caller already made it. With
    `att_targets` None nothing about the old path changes, draw for draw."""
    import nfl_deep_sim as E
    T = T or E.tables()
    hp, ap = dict(hp), dict(ap)
    el = _engine_pts_elasticity()
    if start is None:
        bh, ba = play_worlds(hp, ap, n, rng, T, neutral, env_sd)
        start = (float(bh["pts"].mean()), float(ba["pts"].mean()))
        start_box = (bh, ba)
    deep = got = tuple(start)
    gbox = start_box
    deep_att = [round(_passing(b)[0], 2) for b in gbox] if (att_targets and gbox) else None
    steps, att_steps = [], []
    for _ in range(int(iters)):
        for prof, m, tgt in ((hp, got[0], targets[0]), (ap, got[1], targets[1])):
            if m > 0 and tgt and tgt > 0:
                f = min(_STEP_CLAMP[1], max(_STEP_CLAMP[0], (float(tgt) / m) ** (1.0 / el)))
                base = prof.get("level_off") or prof.get("level") or 1.0
                # the defence keeps the level the opponent has always seen
                prof["level_def"] = prof.get("level_def") or prof.get("level") or 1.0
                prof["level_off"] = round(base * f, 5)
        if att_targets and gbox:
            for prof, b, tgt in ((hp, gbox[0], att_targets[0]), (ap, gbox[1], att_targets[1])):
                if tgt and tgt > 0:
                    prof["call_shift"] = round(float(prof.get("call_shift") or 0.0) + _call_step(*_passing(b), tgt), 5)
        bh, ba = play_worlds(hp, ap, n, rng, T, neutral, env_sd)
        got = (float(bh["pts"].mean()), float(ba["pts"].mean()))
        gbox = (bh, ba)
        steps.append([round(got[0], 2), round(got[1], 2)])
        if att_targets:
            att_steps.append([round(_passing(bh)[0], 2), round(_passing(ba)[0], 2)])
    rep = {"deep": [round(deep[0], 2), round(deep[1], 2)],
           "target": [round(float(targets[0]), 2), round(float(targets[1]), 2)],
           "landed": steps[-1] if steps else [round(deep[0], 2), round(deep[1], 2)],
           "level_off": [hp.get("level_off"), ap.get("level_off")], "n": int(n), "iters": int(iters)}
    if att_targets:
        rep.update(att_deep=deep_att, att_target=[(round(float(t), 2) if t else None) for t in att_targets],
                   att_landed=(att_steps[-1] if att_steps else deep_att),
                   call_shift=[hp.get("call_shift"), ap.get("call_shift")])
    return hp, ap, rep


def blend_targets(drive, deep, weight=None):
    """Each team's points target: the drive model's and the deep engine's at
    the slate's deep weight. Either missing -> the other alone."""
    if weight is None:
        import nfl_game_sim
        weight = float(nfl_game_sim._DEEP_WEIGHT)
    out = []
    for d, e in zip(drive or (None, None), deep):
        out.append(e if d is None else (d if e is None else (1.0 - weight) * float(d) + weight * float(e)))
    return tuple(out)


# ---- the deal -------------------------------------------------------------------
def _split_w(total, w):
    """Integer split of a per-world total (n,) over k players by weights
    (k, n), largest remainder, so the parts are whole and sum to the total
    exactly -- for negative totals too (a team can lose yards). A world whose
    weights are all zero splits evenly."""
    total = np.asarray(total).astype(np.int64)
    k, n = w.shape
    if k == 0:
        return np.zeros((0, n), dtype=np.int64)
    ws = w.sum(axis=0)
    share = np.where(ws > 0, w / np.where(ws > 0, ws, 1.0), 1.0 / k)
    exact = share * total[None, :].astype(np.float64)
    base = np.floor(exact).astype(np.int64)
    rem = total - base.sum(axis=0)
    order = np.argsort(-(exact - base), axis=0)
    cols = np.arange(n)
    for r in range(k):
        base[order[r], cols] += (rem > r)
    return base


def _noise(rng, sd, shape, per=None):
    """Mean-one lognormal multipliers; `per` (same shape) shrinks the spread
    as 1/sqrt(per) -- a man's yards per catch settle as his catches mount."""
    if sd <= 0:
        return np.ones(shape)
    if per is None:
        return np.exp(rng.normal(0.0, sd, size=shape) - 0.5 * sd * sd)
    s = sd / np.sqrt(np.maximum(per, 1))
    return np.exp(rng.normal(0.0, 1.0, size=shape) * s - 0.5 * s * s)


def _target_factor():
    try:
        import nfl_recon
        w = nfl_recon.WEIGHTS
        return float(w["weights"]["target_factor"]["value"]) if w else None
    except Exception as e:
        errlog.note("NFLENG-tf", e)
        return None


def deal_team(players, idx, box, rng, params=None, target_factor=None):
    """One team's box dealt to its players, every world at once. Returns
    {player index: {component: int array (n,)}}.

    Passing: every completion is a catch by a receiver (by target share x
    catch rate), every targeted incompletion is a target by one (by share x
    the miss rate), so receptions, targets, catch rates and the quarterback's
    completions agree in every world; the throws nobody was targeted on
    (throwaways, spikes) are the measured gap between attempts and targets
    (nfl_recon's target factor, 0.950). Team passing yards are split among
    the men who caught passes by catches x yards per catch x a big-play draw,
    to the yard; passing touchdowns among the men who caught passes. Rushing
    the same way off the engine's carries, yards and rushing touchdowns, the
    quarterback's kneel-downs his own (-1 yard each, as the box score keeps
    them). Interceptions to the quarterbacks by their share of the throws,
    lost fumbles to whoever carries the projected fumbling."""
    from nfl_dfs_csim import PARAMS, _line, _multinomial, _multinomial_capped, _split_int
    P = (params if params is not None else PARAMS)["params"]
    tf = target_factor if target_factor is not None else (_target_factor() or 0.95)
    n = len(box["pts"])
    lines = {i: _line(players[i]) for i in idx}
    out = {i: {} for i in idx}
    qbs = [i for i in idx if (players[i].get("pos") or "").upper() == "QB"]
    att, comp, pyd, ptd = box["pass_att"], box["comp"], box["pass_yds"], box["td_pass"]
    # ---- passing: completions and targeted incompletions among the receivers
    recv = [i for i in idx if lines[i]["rec_tgt"] > 0] or [i for i in idx if lines[i]["rec"] > 0
                                                          and (players[i].get("pos") or "").upper() != "QB"]
    if recv:
        base = np.asarray([max(lines[i]["rec_tgt"], lines[i]["rec"], 1e-3) for i in recv])[:, None]
        share = base * _noise(rng, P["share_sd"], (len(recv), n))
        cr = np.asarray([min(0.95, max(0.25, lines[i]["rec"] / max(lines[i]["rec_tgt"], 1e-9)))
                         if lines[i]["rec_tgt"] > 0 else 0.7 for i in recv])[:, None]
        rec = _multinomial(rng, comp, share * cr)
        inc = att - comp
        m_att, m_cmp = float(att.mean()), float(comp.mean())
        q = min(1.0, max(0.0, (tf * m_att - m_cmp) / (m_att - m_cmp))) if m_att > m_cmp else 0.0
        t_inc = rng.binomial(np.maximum(inc, 0), q)
        tgt = rec + _multinomial(rng, t_inc, share * (1.0 - cr))
        ypr = np.asarray([max(3.0, lines[i]["rec_yd"] / max(lines[i]["rec"], 0.25)) for i in recv])[:, None]
        yd_w = rec * ypr * _noise(rng, P["ypr_sd"], (len(recv), n), per=rec)
        rec_yd = _split_w(pyd, yd_w)
        tdr = np.asarray([lines[i]["rec_td"] / max(lines[i]["rec"], 0.25) for i in recv])[:, None]
        rec_td = _multinomial_capped(rng, ptd, rec * (tdr + 1e-3), rec)
        for k, i in enumerate(recv):
            out[i].update(rec_tgt=tgt[k], rec=rec[k], rec_yd=rec_yd[k], rec_td=rec_td[k])
    # ---- the quarterback's line is the team's passing line
    if qbs:
        sh = np.asarray([max(lines[i]["pass_att"], 1e-6) for i in qbs])
        sh = sh / sh.sum()
        for key, src in (("pass_att", att), ("pass_cmp", comp), ("pass_yd", pyd), ("pass_td", ptd),
                         ("int", box["int"])):
            parts = _split_int(np.asarray(src).astype(np.int64), sh)
            for k, i in enumerate(qbs):
                out[i][key] = parts[k]
    # ---- rushing: carries, yards, touchdowns off the engine's ground game
    rush = [i for i in idx if lines[i]["rush_att"] > 0] or qbs[:1]
    if rush:
        base = np.asarray([max(lines[i]["rush_att"], 1e-3) for i in rush])[:, None]
        car = _multinomial(rng, box["rush_att"], base * _noise(rng, P["rshare_sd"], (len(rush), n)))
        ypc = np.asarray([lines[i]["rush_yd"] / max(lines[i]["rush_att"], 0.25) for i in rush])[:, None]
        yd_w = car * np.maximum(ypc, 0.5) * _noise(rng, P["ypc_sd"], (len(rush), n), per=car)
        r_yd = _split_w(box["rush_yds"], yd_w)
        rtr = np.asarray([lines[i]["rush_td"] / max(lines[i]["rush_att"], 0.25) for i in rush])[:, None]
        r_td = _multinomial_capped(rng, box["td_rush"], car * (rtr + 1e-3), car)
        for k, i in enumerate(rush):
            out[i].update(rush_att=car[k], rush_yd=r_yd[k], rush_td=r_td[k])
    if qbs and "kneels" in box:
        q0 = qbs[0]
        kn = np.asarray(box["kneels"]).astype(np.int64)
        out[q0]["rush_att"] = out[q0].get("rush_att", np.zeros(n, dtype=np.int64)) + kn
        out[q0]["rush_yd"] = out[q0].get("rush_yd", np.zeros(n, dtype=np.int64)) - kn
    # ---- lost fumbles: to the projected fumblers, else by touches
    fw = np.asarray([max(lines[i]["fum"], 0.0) for i in idx])
    touches = np.asarray([lines[i]["rush_att"] + lines[i]["rec"] + 0.1 * lines[i]["pass_att"] for i in idx])
    fw = fw if fw.sum() > 0 else touches
    if fw.sum() > 0:
        fum = _multinomial(rng, box["fum_lost"], np.repeat((fw + 1e-9)[:, None], n, axis=1))
        for k, i in enumerate(idx):
            out[i]["fum"] = fum[k]
    return out


def dk_offense(c, n):
    """DraftKings classic points for one player's integer components."""
    S = dk_scoring.NFL_OFF
    z = np.zeros(n, dtype=np.int64)
    g = lambda k: c.get(k, z)          # noqa: E731
    pts = (g("pass_yd") * S["pass_yd"] + g("pass_td") * S["pass_td"] + g("int") * S["int"]
           + g("rush_yd") * S["rush_yd"] + g("rush_td") * S["rush_td"]
           + g("rec") * S["rec"] + g("rec_yd") * S["rec_yd"] + g("rec_td") * S["rec_td"]
           + g("fum") * S["fumble_lost"])
    pts = (pts + S["pass_300"] * (g("pass_yd") >= 300) + S["rush_100"] * (g("rush_yd") >= 100)
           + S["rec_100"] * (g("rec_yd") >= 100))
    return np.round(pts.astype(np.float64), 2)


def off_points(box, opp_box):
    """The points a side scored WITH the ball -- what the other defense
    'allowed' in DraftKings' sense: everything but its return touchdowns
    (and their tries) and the safeties its defense scored."""
    return box["pts"] - box["ret_pts"] - 2 * opp_box["safety_conceded"]


def dk_dst(me, opp):
    """DraftKings DST points for the side whose box is `me`, per world:
    points allowed off the other offence's own scoring, sacks it took,
    interceptions it threw, fumbles it lost, this side's return and
    defensive touchdowns, the safeties this defense scored, its blocks."""
    D = dk_scoring.NFL_DST
    pa = off_points(opp, me)
    tiers = np.full(len(pa), -4, dtype=np.int64)
    for lo, hi, p in reversed(dk_scoring.NFL_DST_PA):
        tiers = np.where((pa >= lo) & (pa <= hi), p, tiers)
    pts = (tiers + D["sack"] * opp["sacks"] + D["int"] * opp["int"] + D["fumble_rec"] * opp["fum_lost"]
           + D["return_td"] * me["td_ret"] + D["safety"] * opp["safety_conceded"] + D["blocked_kick"] * me["blk"])
    return pts.astype(np.float64)


def dk_kicker(me):
    """DraftKings kicker points: field goals by DK's distance tiers, tries."""
    K = dk_scoring.NFL_K
    return (K["fg_0_39"] * me["fg_0_39"] + K["fg_40_49"] * me["fg_40_49"] + K["fg_50p"] * me["fg_50p"]
            + K["xp"] * me["xp_made"]).astype(np.float64)


# ---- one game -------------------------------------------------------------------
def simulate_game(game, n, rng, ctx):
    """One DFS game, n worlds. `game` is weekly_games' shape (players with
    their lines; `home`/`away`/`neutral` from nfl_dfs_sim._orient); `rng` a
    numpy Generator; `ctx` {T, profiles (ESPN abbr -> deep profile), drive
    (team -> drive-model expected points), weight, workers}. Returns the
    contract nfl_dfs_sim.player_pool reads, plus the defenses' and kickers'
    arrays (`dst`, `k`), the per-world components and the anchor report.
    None when either side has no deep profile (the caller falls back)."""
    if np is None:
        raise RuntimeError("numpy is required for the engine model")
    import nfl_dfs_sim
    import nfl_deep_sim as E
    T = ctx.get("T") or E.tables()
    players = game["players"]
    teams = list(game.get("teams") or [])
    home, away = game.get("home"), game.get("away")
    neutral = bool(game.get("neutral"))
    if not (home and away):
        if len(teams) < 2:
            return None
        home, away, neutral = teams[0], teams[1], True     # orientation unknown: no home field
    profs = ctx.get("profiles") or {}
    hp, ap = profs.get(nfl_dfs_sim.espn_abbr(home)), profs.get(nfl_dfs_sim.espn_abbr(away))
    if not hp or not ap:
        return None
    seed = int(rng.integers(0, 2 ** 31 - 1))
    drive = ctx.get("drive") or {}
    py = random.Random(seed)
    # the deep engine's own expectation is the first measuring pass of the anchor
    targets_fn = (lambda deep: blend_targets((drive.get(home), drive.get(away)), deep, ctx.get("weight")))
    w_att = ATT_WEIGHT if ctx.get("att_weight") is None else float(ctx.get("att_weight"))
    att_fn = None
    if w_att > 0:
        sl = (sleeper_attempts(players, home), sleeper_attempts(players, away))
        att_fn = (lambda deep_att: tuple(((1.0 - w_att) * e + w_att * s_) if s_ > 0 else e
                                         for e, s_ in zip(deep_att, sl)))
    hp2, ap2, rep = _anchor_blended(hp, ap, targets_fn, py, T, neutral, ctx, att_fn=att_fn)
    if w_att > 0:
        rep["att_weight"] = w_att
    bh, ba = play_worlds_parallel(hp2, ap2, n, seed, neutral, workers=int(ctx.get("workers") or 1),
                                  env_sd=ctx.get("env_sd"), T=T)
    boxes = {home: bh, away: ba}
    opp = {home: away, away: home}
    by_team = {}
    for i, p in enumerate(players):
        if p.get("actual_only"):
            continue
        by_team.setdefault(p.get("team"), []).append(i)
    comps = {i: {} for i in range(len(players))}
    for t, idx in by_team.items():
        if t not in boxes:
            continue
        comps.update(deal_team(players, idx, boxes[t], rng))
    pts = {}
    for i in range(len(players)):
        pts[i] = dk_offense(comps[i], n) if comps[i] else np.zeros(n)
    out = []
    for i, p in enumerate(players):
        arr = pts[i]
        proj = float(p.get("proj_pts") or 0.0)
        srt = np.sort(arr)
        q = lambda f: float(srt[min(n - 1, int(f * n))])          # noqa: E731
        out.append({"name": p["name"], "pos": p.get("pos"), "team": p.get("team"), "opp": p.get("opp"),
                    "proj_pts": proj, "sim_mean": round(float(arr.mean()), 2),
                    "floor": round(q(0.10), 1), "median": round(q(0.50), 1), "ceiling": round(q(0.90), 1),
                    "boom_pct": round(100.0 * float((arr >= 1.5 * proj).mean()), 1) if proj > 0 else None,
                    "bust_pct": round(100.0 * float((arr <= 0.5 * proj).mean()), 1) if proj > 0 else None,
                    "arr": arr})
    dst = {t: dk_dst(boxes[t], boxes[opp[t]]) for t in (home, away)}
    kick = {t: dk_kicker(boxes[t]) for t in (home, away)}
    team_def = {t: {"td": boxes[t]["td_pass"] + boxes[t]["td_rush"], "gv": boxes[t]["int"] + boxes[t]["fum_lost"],
                    "yd": boxes[t]["pass_yds"] + boxes[t]["rush_yds"], "pa": boxes[t]["pass_yds"]} for t in (home, away)}
    summary = {t: {k: round(float(boxes[t][k].mean()), 2) for k in ("pts", "pass_att", "comp", "pass_yds", "td_pass",
                                                                    "rush_att", "rush_yds", "td_rush", "int", "fum_lost",
                                                                    "sacks", "fg_made", "xp_made", "td_ret")}
               for t in (home, away)}
    rep.update(home=home, away=away, neutral=neutral, seed=seed, box=summary,
               corr_pts=round(float(np.corrcoef(bh["pts"], ba["pts"])[0, 1]), 3) if n > 2 else None)
    return {"label": game.get("label"), "teams": teams, "players": out, "props": [], "stacks": [], "n_sims": n,
            "team_fp": {t: sum(pts[i] for i in by_team.get(t, [])) for t in (home, away)},
            "team_def": team_def, "dst": dst, "k": kick, "components": comps, "anchor": rep,
            "boxes": boxes if ctx.get("keep_boxes") else None}


def _anchor_blended(hp, ap, targets_fn, py, T, neutral, ctx, att_fn=None):
    """anchor() with the targets known only after the first measuring pass
    (they blend the deep engine's own expectation, points and -- with
    `att_fn` -- attempts)."""
    n = int(ctx.get("anchor_n") or ANCHOR_N)
    bh, ba = play_worlds(hp, ap, n, py, T, neutral, ctx.get("env_sd"))
    deep = (float(bh["pts"].mean()), float(ba["pts"].mean()))
    targets = targets_fn(deep)
    att_targets = att_fn((_passing(bh)[0], _passing(ba)[0])) if att_fn else None
    return anchor(hp, ap, targets, py, T, neutral, n=n, iters=int(ctx.get("anchor_iters") or ANCHOR_ITERS),
                  start=deep, env_sd=ctx.get("env_sd"), att_targets=att_targets, start_box=(bh, ba))


def sleeper_attempts(players, team):
    """Sleeper's projected pass attempts for one team: the sum of its
    quarterbacks' lines (the reconciled `recon` where present, nfl_recon)."""
    from nfl_dfs_csim import _line
    return float(sum(_line(p)["pass_att"] for p in players
                     if p.get("team") == team and not p.get("actual_only")
                     and (p.get("pos") or "").upper() == "QB"))


# ---- the week's context -----------------------------------------------------------
def week_context(season, week, workers=1):
    """What simulate_game needs for a week: the deep tables, this week's deep
    profiles (the nightly run's, the roster factor folded in), and the drive
    model's expected points for every game (Sleeper team projections through
    nfl_game_sim's engine at the board's own 2,400 games). Missing drive
    figures leave the deep engine's own level for that game."""
    import nfl_deep_sim as E
    import nfl_game_sim
    T = E.tables()
    profiles = nfl_game_sim._deep_profiles(season, week) or {}
    drive = {}
    try:
        import nfl_data
        for h, a in nfl_data.week_games(str(season), week) or []:
            sim = nfl_game_sim.simulate_game(h, a, n=2400,
                                             seed=zlib.crc32(f"{season}|{week}|{h['abbr']}|{a['abbr']}".encode()))
            drive[_sleeper_abbr(h["abbr"])] = float(sim["exp_home"])
            drive[_sleeper_abbr(a["abbr"])] = float(sim["exp_away"])
    except Exception as e:
        errlog.note("NFLENG-drive", e)
    return {"T": T, "profiles": profiles, "drive": drive, "weight": float(nfl_game_sim._DEEP_WEIGHT),
            "workers": int(workers or 1)}


def _sleeper_abbr(ab):
    """ESPN's WSH back to Sleeper's and DraftKings' WAS (the pool's keys)."""
    t = str(ab or "").upper()
    return {"WSH": "WAS"}.get(t, t)


def sim_stamp(n=None, seed=None, ctx=None):
    """What a board built on these worlds records."""
    import nfl_deep_sim as E
    import nfl_game_sim
    from nfl_dfs_csim import PARAMS
    T = (ctx or {}).get("T") or E.tables()
    return {"model": SIM_MODEL, "version": SIM_MODEL_VERSION, "projections": "sleeper-weekly (usage shares), "
            "drive model + deep engine (team level)",
            "deep_weight": (ctx or {}).get("weight") or float(nfl_game_sim._DEEP_WEIGHT),
            "anchor": {"n": ANCHOR_N, "iters": ANCHOR_ITERS},
            "engine": {"form_sd": E._FORM_SD, "hfa_yds": E._HFA_YDS, "env_sd": ENV_SD,
                       "kickoff_era": getattr(T, "kickoff_era", None)},
            "passing_volume": {"att_weight": ATT_WEIGHT,
                               "target": "(1 - w) x the engine's own attempts + w x Sleeper's projected team attempts"},
            "validation": "research/reports/nfl_dfs_engine.md (2025 blind, rules pre-registered)",
            "deal_params": {k: PARAMS["params"][k] for k in ("share_sd", "ypr_sd", "rshare_sd", "ypc_sd")},
            "deal_params_hash": PARAMS["hash"][:16], "deal_params_fitted": bool(PARAMS["fitted"]),
            "touchdowns": "the engine's, allocated among the men who caught (carried) the ball",
            "marginals": "not pinned: each player's mean is what the simulated game deals him",
            "dst": "DraftKings DST scored off the same simulated game (points allowed by the other offence, "
                   "sacks, takeaways, return TDs, safeties, blocks)",
            "kicker": "field goals by distance and tries off the same simulated game",
            "seed": (int(seed) if seed is not None else None), "n": (int(n) if n else None)}
