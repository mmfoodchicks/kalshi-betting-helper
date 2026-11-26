"""Guards for the projection-sensitivity framework (dfs_sensitivity, 2026-11-26;
research/reports/dfs_sensitivity.md), section AUTO of the suite.

What is pinned, each with a known answer:
  SN1  the scenario spec is strict and its signatures say which scenarios share worlds;
  SN2-4 the lines layer conserves what it claims (team totals in `means` AND `recon`,
       per-touch rates, other teams, the public's proj_pts) and the script shift is exact;
  SN5  the production deal (nfl_dfs_engine.deal_team) conserves the box world for world
       under a shifted line, and the shifted player's share moves as intended;
  SN6  end to end through nfl_dfs_sim.player_pool and pool_cache with the REAL engine: a
       repeat is a cache hit, a share change re-simulates one game and leaves every
       world's box score identical (common random numbers), a script change moves the
       anchor's target by exactly d;
  SN7  the field tilt reaches its targets and keeps the group totals; the identity is exact;
  SN8  the scorer equals dfs_evaluate.score_lineup and a brute force, figure for figure;
  SN9  zeroing a player drops every lineup holding him (exact per-world arithmetic) and
       leaves every other lineup's figures unchanged to the bit;
  SN10 identical scenarios give identical results;
  SN11 conditions partition the worlds exactly;
  SN12-13 the statistics (jackknife against the binomial, rank correlations against brute
       force) and the paired SE's calibration across independent replications;
  SN14-17 hygiene, the presets and the runtime estimate, the harness's outputs, a weight-0
       diagnostic kept out of the robustness figures.

Hermetic: temporary cache directories; the deep engine plays league-average
profiles on a made-up slate; nothing is fetched.

    python3 tests/guards_auto_sensitivity.py      # exits 1 on any failed check
"""
import copy
import json
import os
import re
import shutil
import sys
import tempfile

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)


def run(ck):
    try:
        import numpy as np
    except ImportError:
        ck("(numpy absent: sensitivity runs are the PC's)", True)
        return
    tmp = tempfile.mkdtemp()
    saved_env = os.environ.get("VIGIL_POOL_CACHE_DIR")
    import pool_cache
    saved_bounds = (pool_cache.KEEP_ENTRIES, pool_cache.MAX_BYTES)
    try:
        _spec(ck)
        _lines(ck)
        _deal(ck, np)
        _engine(ck, np, tmp)
        _field(ck, np)
        _scoring(ck, np)
        _stats(ck, np)
        _calibration(ck, np)
        _hygiene(ck, np, tmp)
    finally:
        if saved_env is None:
            os.environ.pop("VIGIL_POOL_CACHE_DIR", None)
        else:
            os.environ["VIGIL_POOL_CACHE_DIR"] = saved_env
        pool_cache.KEEP_ENTRIES, pool_cache.MAX_BYTES = saved_bounds
        shutil.rmtree(tmp, ignore_errors=True)


# ---- SN1 the spec -----------------------------------------------------------------------------
def _spec(ck):
    import dfs_sensitivity as DS
    bad = [{"name": "a", "ops": [{"kind": "nope"}]},
           {"name": "a", "ops": [{"kind": "share", "comp": "rush_att", "mult": 0.8, "select": {"rol": "RB1"}}]},
           {"name": "a", "ops": [{"kind": "share", "comp": "rush_att", "mult": 0.8, "select": {}}]},
           {"name": "a", "ops": [{"kind": "share", "comp": "rush", "mult": 0.8, "select": {"pos": "RB"}}]},
           {"name": "a", "ops": [{"kind": "share", "comp": "rush_att", "mult": -1, "select": {"pos": "RB"}}]},
           {"name": "a", "ops": [{"kind": "condition", "game": "AAA", "stat": "total"}]},
           {"name": "a", "ops": [{"kind": "field", "model": "tilt", "target": "x"}]},
           {"name": "a", "ops": [{"kind": "field"}, {"kind": "field", "model": "casual"}]},
           {"name": "a", "ops": [{"kind": "points", "select": {"role": "RB1"}, "mult": 0}]},
           {"name": "", "ops": []}, {"name": "a", "opps": []}]
    refused = 0
    for b in bad:
        try:
            DS.normalize(b)
        except ValueError:
            refused += 1
    a1 = {"name": "x", "ops": [{"kind": "share", "comp": "rush_att", "mult": 0.8, "select": {"role": "RB1", "team": "AAA"}}]}
    a2 = {"ops": [{"select": {"team": "AAA", "role": "RB1"}, "mult": 0.8, "comp": "rush_att", "kind": "share"}], "name": "y"}
    a3 = copy.deepcopy(a1)
    a3["ops"][0]["mult"] = 0.75
    base = DS.scenario("base")
    fld = DS.scenario("f", [{"kind": "field", "model": "tilt", "target": "concentrate"}])
    cond = DS.scenario("c", [{"kind": "condition", "game": "AAA", "stat": "total", "lo": 50}])
    pts = DS.scenario("p", [{"kind": "points", "select": {"names": ["X"]}, "mult": 0}])
    ck("SN1 the scenario spec is strict -- an unknown op, key, selector, component, out-of-range number, a "
       "condition with no bound, two field ops, a role on the points layer each refused -- and its signatures say "
       "which scenarios read the same worlds: dict order and names change nothing, a changed multiplier is a new "
       "scenario, and field, condition and points scenarios share the base's worlds",
       refused == len(bad) and DS.sig(a1) == DS.sig(a2) and DS.sig(a1) != DS.sig(a3)
       and DS.sim_sig(base) == DS.sim_sig(fld) == DS.sim_sig(cond) == DS.sim_sig(pts) != DS.sim_sig(a1)
       and not DS.needs_resim(fld) and DS.needs_resim(a1) and DS.needs_resim(DS.scenario("r", reseed=1))
       and DS.field_sig(base) != DS.field_sig(fld) and DS.points_sig(base) != DS.points_sig(pts),
       f"refused {refused}/{len(bad)}")


# ---- SN2-4 the lines layer ---------------------------------------------------------------------
def _slate(n_games=2, recon=True):
    from research import dfs_sensitivity_run as R
    return R.synthetic_slate(n_games=n_games, seed=0, recon=recon)


def _team_tot(games, team, comp, which):
    return sum(float((p.get(which) or {}).get(comp) or 0.0) for g in games.values() for p in g["players"]
               if p["team"] == team and isinstance(p.get(which), dict))


def _lines(ck):
    import dfs_sensitivity as DS
    import nfl_dfs_engine as ENG
    sl = _slate()
    g0 = sl["games"]
    snap = json.dumps(g0, sort_keys=True)
    op = DS.normalize({"name": "s", "ops": [{"kind": "share", "comp": "rush_att", "mult": 0.75, "absorb": "position",
                                             "select": {"role": "RB1"}}]})["ops"]
    g1, rec = DS.apply_games(g0, op)
    teams = sl["teams"]
    cons = all(abs(_team_tot(g1, t, c, w) - _team_tot(g0, t, c, w)) < 1e-9
               for t in teams for c in ("rush_att", "rec_tgt") for w in ("means", "recon"))
    pl0 = {p["name"]: p for g in g0.values() for p in g["players"]}
    pl1 = {p["name"]: p for g in g1.values() for p in g["players"]}
    rb1 = [n for n in pl0 if n.endswith("Back1")]
    share_ok = all(abs(rec[0]["teams"][t]["share_after"] - 0.75 * rec[0]["teams"][t]["share_before"]) < 2e-6 for t in teams)
    rate_ok = all(abs(pl1[n][w]["rush_yd"] / pl1[n][w]["rush_att"] - pl0[n][w]["rush_yd"] / pl0[n][w]["rush_att"]) < 1e-9
                  and abs(pl1[n][w]["rush_att"] - 0.75 * pl0[n][w]["rush_att"]) < 1e-9
                  for n in rb1 for w in ("means", "recon"))
    qb_same = all(pl1[n]["means"] == pl0[n]["means"] and pl1[n]["recon"] == pl0[n]["recon"]
                  for n in pl0 if pl0[n]["pos"] in ("QB", "WR", "TE"))
    proj_same = all(pl1[n]["proj_pts"] == pl0[n]["proj_pts"] for n in pl0)
    ck("SN2 a share op keeps every team's targets and carries to 1e-9 in BOTH the mean and the reconciled line (the "
       "engine deals by the reconciled one), moves the selected back's share by exactly the multiplier with his "
       "yards per carry unchanged, gives his carries to the other backs only (absorb=position: the quarterback, "
       "receivers and tight end untouched), never moves the public's proj_pts, and leaves the input unchanged",
       cons and share_ok and rate_ok and qb_same and proj_same and json.dumps(g0, sort_keys=True) == snap
       and all(r["conserved"] for r in rec[0]["teams"].values()), json.dumps(rec[0]["teams"])[:400])
    one = DS.normalize({"name": "s", "ops": [{"kind": "share", "comp": "rec_tgt", "mult": 0.8, "absorb": "team",
                                              "select": {"names": ["AAA Receiver1"], "team": "AAA"}}]})["ops"]
    g2, rec2 = DS.apply_games(g0, one)
    others_same = all(p == pl0[p["name"]] for g in g2.values() for p in g["players"] if p["team"] != "AAA")
    noise = DS.normalize({"name": "n", "ops": [{"kind": "usage_noise", "sd": 0.10, "seed": 3}]})["ops"]
    gn, recn = DS.apply_games(_slate(n_games=6)["games"], noise)
    gn2, _ = DS.apply_games(_slate(n_games=6)["games"], noise)
    gn3, _ = DS.apply_games(_slate(n_games=6)["games"], DS.normalize({"name": "n", "ops": [dict(noise[0], seed=4)]})["ops"])
    s6 = _slate(n_games=6)
    cons_n = all(abs(_team_tot(gn, t, c, w) - _team_tot(s6["games"], t, c, w)) < 1e-9
                 for t in s6["teams"] for c in ("rush_att", "rec_tgt") for w in ("means", "recon"))
    import math
    p6 = {p["name"]: p for g in s6["games"].values() for p in g["players"]}
    logs = [math.log(p["recon"]["rec_tgt"] / p6[p["name"]]["recon"]["rec_tgt"]) for g in gn.values()
            for p in g["players"] if p6[p["name"]]["recon"]["rec_tgt"] > 0]
    sd_hat = DS._sd(logs)
    ck("SN3 usage noise keeps every team's totals in both lines, is reproducible on its seed and different on "
       "another, and moves shares by about the requested 10% (log SD of the realised factors); a named-player op "
       "touches his team only",
       cons_n and gn == gn2 and gn != gn3 and 0.06 < sd_hat < 0.14 and others_same
       and rec2[0]["teams"]["AAA"]["conserved"] and set(rec2[0]["teams"]) == {"AAA"}, f"sd {sd_hat:.4f}")
    ctx = sl["ctx"]
    sc = DS.normalize({"name": "s", "ops": [{"kind": "script", "team": "AAA", "d_pts": 7.0, "att_mult": 1.1},
                                            {"kind": "script", "team": "BBB", "d_pts": -7.0}]})["ops"]
    ctx2, rc = DS.apply_ctx(ctx, sc)
    deep = (23.3, 19.8)
    t0 = ENG.blend_targets((ctx["drive"]["AAA"], ctx["drive"]["BBB"]), deep, ctx["weight"])
    t1 = ENG.blend_targets((ctx2["drive"]["AAA"], ctx2["drive"]["BBB"]), deep, ctx2["weight"])
    gs, _ = DS.apply_games(g0, sc)
    a0 = ENG.sleeper_attempts([p for g in g0.values() for p in g["players"]], "AAA")
    a1 = ENG.sleeper_attempts([p for g in gs.values() for p in g["players"]], "AAA")
    ck("SN4 a script shift moves the engine's points target by exactly d (the drive level by d / (1 - deep "
       "weight), read through nfl_dfs_engine.blend_targets) and att_mult moves the passing-volume anchor "
       "(sleeper_attempts) by exactly its factor; the other drive levels are untouched",
       abs(t1[0] - t0[0] - 7.0) < 1e-9 and abs(t1[1] - t0[1] + 7.0) < 1e-9 and abs(a1 / a0 - 1.1) < 1e-9
       and all(ctx2["drive"][t] == ctx["drive"][t] for t in ctx["drive"] if t not in ("AAA", "BBB"))
       and ctx["drive"] != ctx2["drive"] and len(rc) == 2, f"{t0} -> {t1}")


# ---- SN5 the production deal under a shifted line ---------------------------------------------------
def _deal(ck, np):
    import dfs_sensitivity as DS
    import nfl_dfs_engine as ENG
    sl = _slate()
    op = DS.normalize({"name": "s", "ops": [{"kind": "share", "comp": "rush_att", "mult": 0.6, "absorb": "position",
                                             "select": {"role": "RB1"}}]})["ops"]
    g1, _ = DS.apply_games(sl["games"], op)
    team = "AAA"
    p0 = [p for p in sl["games"]["g1"]["players"] if p["team"] == team]
    p1 = [p for p in g1["g1"]["players"] if p["team"] == team]
    n = 20000
    r = np.random.default_rng(1)
    att = r.poisson(34, n)
    comp = r.binomial(att, 0.65)
    box = {"pts": r.poisson(22, n), "pass_att": att, "comp": comp, "pass_yds": comp * 11 + r.integers(-20, 20, n),
           "td_pass": r.binomial(comp, 0.07), "int": r.binomial(att - comp, 0.06), "rush_att": r.poisson(26, n),
           "fum_lost": r.binomial(3, 0.1, n), "kneels": r.binomial(3, 0.2, n)}
    box["rush_yds"] = box["rush_att"] * 4 + r.integers(-15, 15, n)
    box["td_rush"] = np.minimum(r.poisson(0.9, n), box["rush_att"])
    idx = list(range(len(p0)))
    d0 = ENG.deal_team(p0, idx, box, np.random.default_rng(9))
    d1 = ENG.deal_team(p1, idx, box, np.random.default_rng(9))

    def tot(d, k):
        return sum(d[i].get(k, np.zeros(n, dtype=np.int64)) for i in d)
    exact = all(np.array_equal(tot(d, "rush_att"), box["rush_att"] + box["kneels"])
                and np.array_equal(tot(d, "rush_yd"), box["rush_yds"] - box["kneels"])
                and np.array_equal(tot(d, "rec"), box["comp"]) and np.array_equal(tot(d, "rec_yd"), box["pass_yds"])
                and np.array_equal(tot(d, "rec_td"), box["td_pass"]) and np.array_equal(tot(d, "rush_td"), box["td_rush"])
                for d in (d0, d1))
    i_rb = next(i for i, p in enumerate(p0) if p["name"].endswith("Back1"))
    i_rb2 = next(i for i, p in enumerate(p0) if p["name"].endswith("Back2"))
    s0 = d0[i_rb]["rush_att"].sum() / box["rush_att"].sum()
    s1 = d1[i_rb]["rush_att"].sum() / box["rush_att"].sum()
    line0 = DS.line_val(p0[i_rb], "rush_att") / sum(DS.line_val(p, "rush_att") for p in p0)
    line1 = DS.line_val(p1[i_rb], "rush_att") / sum(DS.line_val(p, "rush_att") for p in p1)
    ck("SN5 the production deal (nfl_dfs_engine.deal_team) on a shifted line keeps the box world for world -- "
       "carries, rushing yards and TDs, receptions, receiving yards and TDs all sum to the team's box in every one "
       "of 20,000 worlds -- and the lead back's dealt share of carries moves with his line's share (the deal's "
       "competition noise aside), the second back taking them",
       exact and abs(s1 / s0 - line1 / line0) < 0.05 and d1[i_rb2]["rush_att"].sum() > d0[i_rb2]["rush_att"].sum(),
       f"dealt {s0:.4f} -> {s1:.4f} (ratio {s1 / s0:.4f}); line {line0:.4f} -> {line1:.4f} (ratio {line1 / line0:.4f})")


# ---- SN6 end to end through player_pool, pool_cache and the real engine ----------------------------
def _engine(ck, np, tmp):
    import dfs_sensitivity as DS
    import nfl_dfs_engine as ENG
    import nfl_dfs_sim as S
    from research import dfs_sensitivity_run as R
    R.use_own_cache(os.path.join(tmp, "pc"))
    sl = _slate()
    sl["ctx"]["anchor_n"], sl["ctx"]["anchor_iters"] = 120, 1
    orig = (S.weekly_games, ENG.week_context, ENG.simulate_game)
    with R.synthetic_inputs(sl):
        ew = DS.EngineWorlds(12, sl["teams"], 400, seed=77, season="2026", workers=1, games=sl["games"], ctx=sl["ctx"],
                             log=lambda *a: None)
        b = ew([])
        b2 = ew([])
        sh = ew(DS.normalize({"name": "s", "ops": [{"kind": "share", "comp": "rush_att", "mult": 0.7, "absorb": "position",
                                                    "select": {"role": "RB1", "team": "AAA"}}]})["ops"])
        sc = ew(DS.normalize({"name": "c", "ops": [{"kind": "script", "team": "CCC", "d_pts": 7.0},
                                                   {"kind": "script", "team": "DDD", "d_pts": -7.0}]})["ops"])
    sim = lambda o: {k: v["simulated"] for k, v in o["report"]["games"].items()}     # noqa: E731
    same_arr = lambda a, c, names: all(a["pool"][x]["arr"] == c["pool"][x]["arr"] for x in names)  # noqa: E731
    g2_names = [p["name"] for p in sl["games"]["g2"]["players"]]
    ck("SN6a end to end (nfl_dfs_sim.player_pool on the engine model, pool_cache, the real deep engine): the base "
       "simulates both games, a repeat reads both from the cache with every array identical, and the patched "
       "inputs are restored after each call",
       all(sim(b).values()) and not any(sim(b2).values()) and b["pool"] == b2["pool"]
       and (S.weekly_games, ENG.week_context, ENG.simulate_game) == orig
       and not any(k.startswith(DS.PTS_PREFIX) for k in b["pool"]) and len(b["team_pts"]) == 4,
       f"{sim(b)} / {sim(b2)}")
    pts_same = all(np.array_equal(np.asarray(b["team_pts"][t]), np.asarray(sh["team_pts"][t])) for t in b["team_pts"])
    m = lambda o, x: float(np.mean(o["pool"][x]["arr"]))      # noqa: E731
    ck("SN6b a share change on one team re-simulates that game only (the other from the cache, array for array) "
       "and leaves every world's box score identical -- all four teams' points equal world for world: the same "
       "games with the ball dealt differently -- while the lead back loses points and his backup gains them",
       sim(sh) == {"BBB @ AAA": True, "DDD @ CCC": False} and same_arr(b, sh, g2_names) and pts_same
       and m(sh, "AAA Back1") < m(b, "AAA Back1") - 1.0 and m(sh, "AAA Back2") > m(b, "AAA Back2") + 1.0,
       f"{sim(sh)}; back1 {m(b, 'AAA Back1'):.2f} -> {m(sh, 'AAA Back1'):.2f}")
    a0 = b["report"]["games"]["DDD @ CCC"]["anchor"]
    a1 = sc["report"]["games"]["DDD @ CCC"]["anchor"]
    dp = [float(np.mean(sc["team_pts"][t]) - np.mean(b["team_pts"][t])) for t in ("CCC", "DDD")]
    ck("SN6c a script shift re-simulates its game only and moves the engine's points target by exactly +7 / -7 "
       "(the anchor's own report); the simulated points follow (within the anchor's noise at 120 measuring games)",
       sim(sc) == {"BBB @ AAA": False, "DDD @ CCC": True}
       and abs(a1["target"][0] - a0["target"][0] - 7.0) < 0.011 and abs(a1["target"][1] - a0["target"][1] + 7.0) < 0.011
       and 3.0 < dp[0] < 11.0 and -11.0 < dp[1] < -3.0, f"target {a0['target']} -> {a1['target']}; points {dp}")


# ---- toy worlds for the scoring and statistics guards --------------------------------------------
def _toy(np, seed=3, W=4000, M=1500, zero_owned=None):
    rng = np.random.default_rng(seed)
    pos_list = ["QB"] * 4 + ["RB"] * 6 + ["WR"] * 9 + ["TE"] * 4 + ["DST"] * 4
    players = []
    for i, pos in enumerate(pos_list):
        team = ["AAA", "BBB", "CCC", "DDD"][i % 4]
        players.append({"name": f"{pos}{i}", "pos": pos, "team": team, "opp": {"AAA": "BBB", "BBB": "AAA", "CCC": "DDD", "DDD": "CCC"}[team],
                        "game": "AAA@BBB" if team in ("AAA", "BBB") else "CCC@DDD", "salary": 4000,
                        "cpt_salary": 6000, "proj": 10.0, "field_only": False})
    by = {p: [i for i, e in enumerate(players) if e["pos"] == p] for p in ("QB", "RB", "WR", "TE", "DST")}
    field = []
    for _ in range(M):
        wr = [i for i in by["WR"] if i != zero_owned]
        field.append(np.concatenate([rng.choice(by["QB"], 1), rng.choice(by["RB"], 2, replace=False),
                                     rng.choice(wr, 4, replace=False), rng.choice(by["TE"], 1),
                                     rng.choice(by["DST"], 1)]))
    field = np.asarray(field, dtype=np.int64)
    contest = {"id": 1, "name": "Toy GPP", "entry_fee": 20.0, "places_paid": 200, "capacity": 1000,
               "payouts": [{"from": 1, "to": 1, "prize": 5000.0}, {"from": 2, "to": 5, "prize": 500.0},
                           {"from": 6, "to": 50, "prize": 80.0}, {"from": 51, "to": 200, "prize": 30.0}]}
    meta = {"id": "toy", "kind": "classic", "week": 12, "teams": ["AAA", "BBB", "CCC", "DDD"], "players": players,
            "contests": [contest]}
    return meta, field, by, rng


def _lattice(np, rng, shape, scale=5.0):
    """Scores on DraftKings' 0.02-point lattice (continuous test data flips a bucket on summation order)."""
    return np.round(rng.gamma(2.0, scale, size=shape) / 0.02) * 0.02


def _stub(np, meta, worlds_by_reseed, team_pts=None, games=None, calls=None):
    """A simulate() that hands back fixed worlds per reseed (sim ops are not this stub's business)."""
    def sim(ops, reseed=0):
        if calls is not None:
            calls.append((len(ops), reseed))
        X = worlds_by_reseed[reseed]
        return {"pool": {p["name"]: {"arr": X[i].tolist(), "pos": p["pos"], "team": p["team"]}
                         for i, p in enumerate(meta["players"])},
                "team_pts": team_pts or {}, "games": games or {}, "report": {}}
    return sim


def _lineups(by, k, rng, np, must=None, avoid=None):
    out = []
    for j in range(k):
        wr = [i for i in by["WR"] if i not in (avoid or ()) and i != must]
        sel = list(rng.choice(wr, 4 if must is None else 3, replace=False))
        if must is not None:
            sel = [must] + sel
        lu = [by["QB"][j % 4], by["RB"][j % 6], by["RB"][(j + 1) % 6]] + sel[:3] + [by["TE"][j % 4], sel[3], by["DST"][j % 4]]
        out.append([int(x) for x in lu])
    return out


# ---- SN7 the field -------------------------------------------------------------------------------
def _field(ck, np):
    import dfs_sensitivity as DS
    meta, field, by, rng = _toy(np, M=6000)
    pl = meta["players"]
    P = len(pl)
    # a skewed field, as real ownership is (0.5% to 40%): near-uniform ownership barely moves under a power
    # transform (0.26 / 0.25 to the 12th is 1.6x), so the concentrate target could not be tested on one
    pull = rng.normal(0.0, 0.6, P)
    w0 = np.exp(pull[field].sum(axis=1)) * rng.dirichlet(np.ones(len(field)) * 5.0)
    w0 /= w0.sum()
    cols, C = DS.columns(field, P, "classic")
    own = DS.ownership(cols, w0, C)
    grp = DS.groups(pl, "classic")
    w_id, r_id = DS.tilt(cols, w0, C, own)
    tc, rc = DS.targets_concentrate(own, grp, 1.08 * float((own ** 2).sum()))
    w_c, r_c = DS.tilt(cols, w0, C, tc)
    a_c = DS.ownership(cols, w_c, C)
    tn, rn = DS.targets_noise(own, grp, seed=2, mae=2.0)
    w_n, r_n = DS.tilt(cols, w0, C, tn)
    a_n = DS.ownership(cols, w_n, C)
    keep_groups = all(abs(a_c[g].sum() - own[g].sum()) < 1e-6 and abs(a_n[g].sum() - own[g].sum()) < 1e-6 for g in grp)
    # each position is rescaled to its own total, so the order is kept WITHIN a position (across positions it
    # need not be: a 30% back and a 30% receiver concentrate against different totals)
    order = all(np.all(np.diff(tc[g][np.argsort(own[g], kind="stable")]) >= -1e-12) for g in grp)
    ck("SN7 the field tilt: re-weighting to the field's own ownership is the identity, a concentrated target "
       "(sum of own^2 +8%, each position's order kept) and a random ownership error of a stated top-40 size are "
       "both reached to 0.02 points on every player with each position's total kept, and the receipt carries the "
       "effective sample size it cost",
       np.allclose(w_id, w0 / w0.sum(), atol=1e-12) and r_id["iters"] == 1
       and rc["reached"] and r_c["converged"] and abs((a_c ** 2).sum() - 1.08 * (own ** 2).sum()) < 1e-3 and order
       and r_n["converged"] and abs(rn["top40_mae_pts"] - 2.0) < 0.05 and np.abs(a_n - tn).max() < 1e-3
       and keep_groups and r_c["ess"] < len(field) and 0 < r_c["ess_share"] < 1,
       f"{rc} {r_c} | {r_n} | order {order}")
    sd_players = [dict(p, proj=float(i % 7 + 3)) for i, p in enumerate(pl[:12])]
    import itertools
    uni = np.asarray([(c,) + f for c in range(6) for f in itertools.combinations([x for x in range(12) if x != c], 5)][:3000])
    f0 = np.full(len(uni), 1.0 / len(uni))
    _i, f1, rec = DS.field_for({"kind": "field", "model": "sd_top_share", "top_share": 0.01}, uni, f0, sd_players, "showdown")
    _i, ft, rect = DS.field_for({"kind": "field", "model": "tilt", "target": "scale", "top_k": 3, "mult": 1.5}, uni, f0,
                                sd_players, "showdown")
    ck("SN7b showdown: the duplication knob re-weights the universe so its most popular lineup holds the stated "
       "share (dfs_tourney.field_weights), and a tilt works on captain and flex columns separately (each group's "
       "total kept: one captain and five flex a lineup)",
       abs(float(f1.max()) - 0.01) < 1e-4 and abs(f1.sum() - 1) < 1e-9 and rect["converged"]
       and abs(DS.ownership(DS.columns(uni, 12, "showdown")[0], ft, 24)[:12].sum() - 1.0) < 1e-6
       and abs(DS.ownership(DS.columns(uni, 12, "showdown")[0], ft, 24)[12:].sum() - 5.0) < 1e-6,
       f"{rec} | {rect}")


# ---- SN8-11 scoring ----------------------------------------------------------------------------
def _brute(np, X, field, w, lu, contest):
    import dfs_evaluate as DE
    import dfs_tourney as DT
    Xf = X.astype(np.float32)
    fb = np.clip(np.floor(Xf[field].sum(axis=1, dtype=np.float32).astype(np.float64) * DT._RES + 0.5), 0, DT._NB - 1)
    b = np.clip(np.floor(Xf[lu].sum(axis=0, dtype=np.float32).astype(np.float64) * DT._RES + 0.5), 0, DT._NB - 1)
    above = ((fb > b[None, :]) * w[:, None]).sum(axis=0)
    C = DE._capacity(contest) if contest.get("max_entries") else contest["capacity"]
    out = {"top1": float(np.mean(above <= 0.01 + 1e-12)), "cash": float(np.mean(above <= (contest["places_paid"] - 1) / (C - 1) + 1e-12))}
    prize = np.zeros(X.shape[1])
    got = np.zeros(X.shape[1], dtype=bool)
    for q, p in DE.tier_fractions(contest):
        hit = (~got) & (above <= q + 1e-12)
        prize[hit] = p
        got |= hit
    out["ev"] = float(prize.mean()) - contest["entry_fee"]
    return out


def _scoring(ck, np):
    import dfs_evaluate as DE
    import dfs_sensitivity as DS
    meta, field, by, rng = _toy(np)
    P = len(meta["players"])
    X = _lattice(np, rng, (P, 2500)).astype(np.float32)
    lus_idx = _lineups(by, 12, rng, np)
    lineups = [{"label": f"L{j}", "names": [meta["players"][i]["name"] for i in lu]} for j, lu in enumerate(lus_idx)]
    w = rng.dirichlet(np.ones(len(field)))
    calls = []
    runner = DS.Runner(meta, (field, w), lineups, _stub(np, meta, {0: X}, calls=calls), log=lambda *a: None)
    raw = runner.run([DS.scenario("base"), DS.scenario("base_copy", [{"kind": "field", "model": "board"}])])
    T, _dev = DS.field_T(X, field, w / w.sum(), P, "classic", runner.qs)
    exact, brute_ok = True, True
    for j, lu in enumerate(runner.lus):
        ref = DE.score_lineup(X, T, runner.qs, meta, lu["idx"])
        c0 = ref["contests"][0]
        # a Python float before round(), as score_lineup does: round() on a numpy float64 rounds the scaled
        # value half-to-even (22.075 -> 22.08) where Python's rounds the binary value (22.07)
        tot = lambda k: float(raw["sums"][k][0, j].sum() / raw["counts"][0].sum())      # noqa: E731
        mine = {"top01_pct": round(100.0 * tot("0:top01"), 3), "top1_pct": round(100.0 * tot("0:top1"), 3),
                "cash_pct": round(100.0 * tot("0:cash"), 2), "ev_experimental": round(tot("0:prize") - 20.0, 2)}
        # the mean points: score_lineup averages in float32, the block sums in float64 -- equal to the cent
        exact &= all(mine[k] == c0[k] for k in mine) and abs(round(tot("score"), 2) - ref["mean"]) <= 0.011
        if j < 3:
            bf = _brute(np, X, field, w / w.sum(), lu["idx"], meta["contests"][0])
            brute_ok &= abs(tot("0:top1") - bf["top1"]) < 1e-12 and abs(tot("0:cash") - bf["cash"]) < 1e-12 \
                and abs(tot("0:prize") - 20.0 - bf["ev"]) < 1e-9
    ck("SN8 the scorer is the evaluator's: every lineup's top 0.1%, top 1%, cash, no-tie EV and mean points from "
       "the block sums equal dfs_evaluate.score_lineup on the same worlds and thresholds, and a brute force (the "
       "weighted field's mass strictly above, counted lineup by lineup) to 1e-12",
       exact and brute_ok and len(calls) == 1 and not runner.bad and len(runner.lus) == 12, f"calls {calls}")
    s = DS.summarize(raw)
    v = s["metrics"]["top1"]["by_scenario"]["base_copy"]
    ck("SN10 identical scenarios give identical results: a second scenario with the same worlds and field "
       "(another name) reads the cached worlds (one simulate call), its sums equal to the bit, every paired "
       "difference and its SE exactly 0, its rank correlation 1",
       all(np.array_equal(raw["sums"][k][0], raw["sums"][k][1]) for k in raw["sums"])
       and all(x == 0 for x in v["d"]) and all(x == 0 for x in v["se_d"]) and v["spearman"] == 1.0
       and v["n_z2"] == 0)
    # SN9: zero a dominant player the field never holds
    meta, field, by, rng = _toy(np, seed=5, zero_owned=by["WR"][0])
    Z = by["WR"][0]
    X = _lattice(np, rng, (P, 3000)).astype(np.float32)
    X[Z] = (40.0 + np.round(rng.gamma(2.0, 2.0, 3000) / 0.02) * 0.02).astype(np.float32)
    with_z = _lineups(by, 5, rng, np, must=Z)
    without = _lineups(by, 7, rng, np, avoid={Z})
    lineups = ([{"label": f"Z{j}", "names": [meta["players"][i]["name"] for i in lu]} for j, lu in enumerate(with_z)]
               + [{"label": f"N{j}", "names": [meta["players"][i]["name"] for i in lu]} for j, lu in enumerate(without)])
    runner = DS.Runner(meta, (field, None), lineups, _stub(np, meta, {0: X}), log=lambda *a: None)
    zname = meta["players"][Z]["name"]
    raw = runner.run([DS.scenario("base"), DS.scenario("scratch", [{"kind": "points", "mult": 0.0, "select": {"names": [zname]}}])])
    s = DS.summarize(raw, top_n=5)
    t1 = s["metrics"]["top1"]
    base_rank = {r["lineup"]: r["base_rank"] for r in t1["by_lineup"]}
    sc = t1["by_scenario"]["scratch"]
    mean_b, mean_s = t1["by_scenario"]["base"]["mean"], sc["mean"]
    rank_s = {l: 12 + 1 - r for l, r in enumerate(DS.rank_avg(mean_s))}
    X0, _r = DS.apply_points(X, meta["players"], DS.normalize({"name": "s", "ops": [{"kind": "points", "mult": 0.0,
                                                                                     "select": {"names": [zname]}}]})["ops"])
    Bb, Sb = DS.lineup_buckets(X, runner.lus)
    Bs, Ss = DS.lineup_buckets(X0, runner.lus)
    arith = (not runner.bad and len(runner.lus) == 12
             and all(np.allclose(Ss[j], Sb[j] - X[Z], atol=1e-3) for j in range(5)) and np.array_equal(Ss[5:], Sb[5:]))
    ck("SN9 a scenario that zeroes a player drops every lineup holding him: his worlds are all 0, each of those "
       "lineups scores exactly its base minus his points in every world, each one's P(top 1%) falls and none stays "
       "in the top 5 (all five were the top 5 before) -- while every lineup without him keeps its figures to the "
       "bit (a field that never held him has the same thresholds), paired difference and SE exactly 0",
       not X0[Z].any() and arith and all(base_rank[l] <= 5 for l in range(5)) and all(rank_s[l] > 5 for l in range(5))
       and all(mean_s[l] < mean_b[l] for l in range(5)) and all(sc["d"][l] == 0 and sc["se_d"][l] == 0 for l in range(5, 12)),
       f"base ranks {[base_rank[l] for l in range(5)]}, scenario ranks {[rank_s[l] for l in range(5)]}")
    # SN11: conditions partition the worlds
    meta, field, by, rng = _toy(np, seed=8)
    X = _lattice(np, rng, (P, 2500)).astype(np.float32)
    tp = {"AAA": rng.poisson(24, 2500).astype(float), "BBB": rng.poisson(20, 2500).astype(float)}
    games = {"g1": {"label": "BBB @ AAA", "teams": ["AAA", "BBB"], "home": "AAA", "away": "BBB"}}
    lus = _lineups(by, 8, rng, np)
    lineups = [{"label": f"L{j}", "names": [meta["players"][i]["name"] for i in lu]} for j, lu in enumerate(lus)]
    runner = DS.Runner(meta, (field, None), lineups, _stub(np, meta, {0: X}, team_pts=tp, games=games), log=lambda *a: None)
    raw = runner.run([DS.scenario("base"),
                      DS.scenario("hi", [{"kind": "condition", "game": "AAA", "stat": "margin_fav", "lo": 7}]),
                      DS.scenario("lo", [{"kind": "condition", "game": "AAA", "stat": "margin_fav", "hi": 6.5}]),
                      DS.scenario("all", [{"kind": "condition", "game": "BBB", "stat": "total", "lo": -1}])])
    part = all(np.array_equal(raw["sums"][k][1] + raw["sums"][k][2], raw["sums"][k][0]) for k in raw["sums"]) \
        and np.array_equal(raw["counts"][1] + raw["counts"][2], raw["counts"][0])
    same_all = all(np.array_equal(raw["sums"][k][3], raw["sums"][k][0]) for k in raw["sums"])
    kept = raw["receipts"]["hi"]["conditions"][0]
    ck("SN11 conditions partition the worlds exactly: the favourite winning by 7+ and by 6 or less (the favourite "
       "read off the worlds' mean points) sum to the base in every block of every figure, an always-true condition "
       "IS the base, and the receipt's kept share is the counted one",
       part and same_all and abs(kept["kept_share"] - float(((tp["AAA"] - tp["BBB"]) >= 7).mean())) < 1e-5
       and "AAA minus BBB" in kept["of"], json.dumps(kept))


# ---- SN12-13 statistics ------------------------------------------------------------------------------
def _stats(ck, np):
    import dfs_sensitivity as DS
    rng = np.random.default_rng(4)
    W, nb, L = 40000, 40, 30
    p = np.linspace(0.005, 0.2, L)
    A = rng.random((L, W)) < p[:, None]
    edges = DS.block_edges(W, W // nb)
    sums = DS.block_sums(A, np.ones(W, dtype=bool), edges)
    counts = np.add.reduceat(np.ones(W), edges)
    mean, se = DS.mean_se(sums, counts)
    binom = np.sqrt(mean * (1 - mean) / W)
    ratio = se / binom
    d0, s0 = DS.diff_se(sums, counts, sums, counts)
    a = rng.integers(0, 6, 25).astype(float)
    b = a + rng.integers(-2, 3, 25)

    def kendall_naive(x, y):
        c = d = tx = ty = 0
        for i in range(len(x)):
            for j in range(i + 1, len(x)):
                sx, sy = np.sign(x[i] - x[j]), np.sign(y[i] - y[j])
                if sx and sy:
                    c += sx == sy
                    d += sx != sy
                elif sx:
                    ty += 1
                elif sy:
                    tx += 1
        return (c - d) / np.sqrt((c + d + tx) * (c + d + ty))

    def spearman_naive(x, y):
        def rk(v):
            return np.asarray([np.mean([k + 1 for k, u in enumerate(sorted(v)) if u == t]) for t in v])
        return float(np.corrcoef(rk(x), rk(y))[0, 1])
    ck("SN12 the statistics: the block jackknife SE matches the binomial SE (root-mean-square ratio within 10% "
       "over 30 probabilities from 0.5% to 20%, none off by 40%), the paired SE of a scenario against itself is "
       "exactly 0, tau-b and "
       "Spearman equal brute-force tie-corrected versions, and the worlds-needed formula is var / SE^2 (P(top "
       "1%) = 1% to 0.1 points takes 9,900 worlds)",
       0.9 < float(np.sqrt(np.mean(ratio ** 2))) < 1.1 and 0.6 < ratio.min() and ratio.max() < 1.4
       and not np.any(d0) and not np.any(s0)
       and abs(DS.kendall(a, b) - kendall_naive(a, b)) < 1e-12 and abs(DS.spearman(a, b) - spearman_naive(a, b)) < 1e-12
       and abs(DS.worlds_needed(0.01 * 0.99, 0.001) - 9900) < 1e-6,
       f"SE ratio {ratio.min():.3f}-{ratio.max():.3f}")


def _calibration(ck, np):
    """The paired SE against the truth, twice. (a) The statistics alone, 300 replications at once: a base
    indicator and a scenario's, the same worlds with 3% of them flipped (common random numbers: the two agree
    almost everywhere), 40 blocks; the spread of the 300 paired differences against the RMS jackknife SE.
    (b) Through the Runner: 16 replications of base vs one player's points x0.85 on the same worlds. The field's
    thresholds cost ~0.3 ms a world (40,001 buckets), so (b) is kept small; (a) carries the precision."""
    import dfs_sensitivity as DS
    rng = np.random.default_rng(21)
    R, W, nb = 300, 2000, 40
    A = rng.random((R, W)) < 0.10
    B = A ^ (rng.random((R, W)) < 0.03)
    edges = DS.block_edges(W, W // nb)
    ones = np.ones(W, dtype=bool)
    sa, sb = DS.block_sums(A, ones, edges), DS.block_sums(B, ones, edges)
    cnt = np.add.reduceat(np.ones(W), edges)
    d, se = DS.diff_se(sb, cnt, sa, cnt)
    unpaired = np.sqrt(DS.mean_se(sa, cnt)[1] ** 2 + DS.mean_se(sb, cnt)[1] ** 2)
    ratio_a = float(np.std(d, ddof=1) / np.sqrt(np.mean(se ** 2)))
    meta, field, by, rng = _toy(np, seed=11, M=400)
    P = len(meta["players"])
    lus = _lineups(by, 6, rng, np)
    lineups = [{"label": f"L{j}", "names": [meta["players"][i]["name"] for i in lu]} for j, lu in enumerate(lus)]
    tgt = meta["players"][lus[0][3]]["name"]
    ds, ses = [], []
    for r in range(16):
        X = _lattice(np, np.random.default_rng(1000 + r), (P, 800)).astype(np.float32)
        runner = DS.Runner(meta, (field, None), lineups, _stub(np, meta, {0: X}), log=lambda *a: None)
        raw = runner.run([DS.scenario("base"), DS.scenario("s", [{"kind": "points", "mult": 0.85, "select": {"names": [tgt]}}])])
        v = DS.summarize(raw, metrics=("cash",))["metrics"]["cash"]["by_scenario"]["s"]
        ds.append(v["d"][0])
        ses.append(v["se_d"][0])
    ratio_b = float(np.std(ds, ddof=1) / np.sqrt(np.mean(np.square(ses))))
    ck("SN13 the paired SE is calibrated: over 300 replications the spread of a paired difference on common worlds "
       "matches the jackknife SE (ratio within 0.85-1.15) and sits far below the unpaired SE (what pairing buys), "
       "and through the Runner, 16 replications of one player's points x0.85 (the field holding him too) agree "
       "(ratio within 0.6-1.6: 16 replications pin it no tighter)",
       0.85 < ratio_a < 1.15 and float(np.mean(se)) < 0.5 * float(np.mean(unpaired)) and 0.6 < ratio_b < 1.6,
       f"(a) ratio {ratio_a:.3f}, paired SE {np.mean(se):.5f} vs unpaired {np.mean(unpaired):.5f}; (b) ratio {ratio_b:.3f}")


# ---- SN14-16 hygiene, presets, estimate, outputs -----------------------------------------------------
def _hygiene(ck, np, tmp):
    import dfs_evaluate as DE
    import dfs_sensitivity as DS
    from research import dfs_sensitivity_run as R
    src = open(os.path.join(_ROOT, "dfs_sensitivity.py")).read()
    rsrc = open(os.path.join(_ROOT, "research", "dfs_sensitivity_run.py")).read()
    servers = [f for f in os.listdir(_ROOT) if f.endswith(".py") and f != "dfs_sensitivity.py"]
    imported = [f for f in servers if re.search(r"\bdfs_sensitivity\b", open(os.path.join(_ROOT, f)).read())]
    ck("SN14 hygiene: no production module imports the framework (research only, nothing on the server); no silent "
       "except-pass; its worlds seed is neither the board's nor the evaluator's final one; the harness files its "
       "worlds in a cache directory of its own (never the boards' 24-entry cache) and every output is written by "
       "write_outputs alone",
       not imported and not re.search(r"except Exception:\s*\n\s*pass", src + rsrc)
       and DS.sens_seed("x-1") != DE.final_seed("x-1") and 'SENS_CACHE_SUBDIR = os.path.join("sensitivity", "poolcache")' in rsrc
       and "use_own_cache()" in rsrc and rsrc.count("json.dump(") == 2, f"imported by {imported}")
    meta, field, by, rng = _toy(np, seed=2)
    lus = [{"idx": lu, "cap": None} for lu in _lineups(by, 10, rng, np)]
    sc = DS.preset(meta, lus, kind="standard")
    names = [s["name"] for s in sc]
    kinds = {o["kind"] for s in sc for o in s["ops"]}
    fields = {o.get("model") for s in sc for o in s["ops"] if o["kind"] == "field"}
    secs, parts = DS.estimate(sc, 40000, 13, 300000, workers=12, gpu=False)
    secs_g, _pg = DS.estimate(sc, 40000, 13, 300000, workers=12, gpu=True)
    one = DS.scenario("x", [{"kind": "share", "comp": "rush_att", "mult": 0.8, "select": {"names": ["a"], "team": "AAA"}}])
    allr = DS.scenario("y", [{"kind": "share", "comp": "rush_att", "mult": 0.8, "select": {"role": "RB1"}}])
    ck("SN15 the presets answer the owner's six questions -- usage off by 10%, a back's carries short, a receiver's "
       "targets overestimated, a blowout (and its relatives), ownership off, another field model -- plus the noise "
       "reference and a scratch, every name unique, the base first; the estimate counts one game for a named "
       "player and every game for a role-wide op, and the GPU only shortens the thresholds",
       names[0] == "base" and "noise_reference" in names and len(set(names)) == len(names)
       and any(n.startswith("usage_noise") for n in names) and any(n.startswith("usage_down10") for n in names)
       and any(n.startswith("rb_carries_down") for n in names) and any(n.startswith("wr_targets_down") for n in names)
       and any(n.startswith("blowout_fav") for n in names) and any(n.startswith("own_error") for n in names)
       and "field_casual" in names and "own_concentrated" in names and any(n.startswith("scratch") for n in names)
       and {"share", "usage_noise", "condition", "script", "field", "points"} <= kinds and {"casual", "tilt"} <= fields
       and DS.games_touched(one, 13) == 1 and DS.games_touched(allr, 13) == 13 and secs_g < secs
       and parts["simulation_s"] > 0, f"{len(sc)} scenarios; {secs / 60:.0f} min {parts}")
    # the harness: the board's lineups, the outputs
    row = lambda names, slot0="QB": {"lineup": [{"slot": slot0 if k == 0 else "X", "name": n} for k, n in enumerate(names)]}  # noqa: E731
    nm = [p["name"] for p in meta["players"]]
    board = {"kind": "classic", "results": {"1": {"top_top1": [row(nm[:9]), row(nm[1:10])], "top_money": [row(nm[:9])],
                                                  "portfolio": {"entries": [row(nm[2:11])]}, "chalk": row(nm[3:12])}}}
    bl = R.board_lineups(board)
    sdb = {"kind": "showdown", "top_top1": [row(nm[:6], "CPT")], "portfolio": {"3": {"entries": [row(nm[:6], "CPT"), row(nm[1:7], "CPT")]}}}
    sl = R.board_lineups(sdb)
    ok_bl = (len(bl) == 4 and bl[0]["sources"] == ["top_top1#1", "top_money#1"] and bl[-1]["label"] == "Vigil chalk"
             and len(sl) == 2 and sl[0]["captain"] == nm[0] and sl[0]["sources"] == ["top_top1#1", "portfolio3#1"])
    X = _lattice(np, rng, (len(meta["players"]), 2000)).astype(np.float32)
    lineups = [{"label": f"L{j}", "names": [nm[i] for i in lu["idx"]]} for j, lu in enumerate(lus)]
    runner = DS.Runner(meta, (field, None), lineups, _stub(np, meta, {0: X, 1: _lattice(np, np.random.default_rng(99), X.shape).astype(np.float32)}), log=lambda *a: None)
    raw = runner.run([DS.scenario("base"), DS.scenario("noise_reference", reseed=1)])
    raw["artifact"] = "toy"
    summ = DS.summarize(raw)
    d = R.write_outputs(raw, summ, meta, 999, False, out_root=os.path.join(tmp, "out"), extra={"t": 1})
    files = sorted(os.listdir(d))
    share = open(os.path.join(d, "share.json")).read()
    ck("SN16 the harness lists the board's own lineups once each with every list they came from (classic: the first "
       "contest's lists, the portfolio, the chalk; showdown: the captain kept), and writes summary.json, share.json, "
       "blocks.npz and digest.md -- the share digest carrying lineups and scenarios, nothing per world or per entrant",
       ok_bl and files == ["blocks.npz", "digest.md", "share.json", "summary.json"]
       and not re.search(r"entrant|EntryId|username|handle", share) and '"scenarios"' in share
       and len(share) < 200000, f"{files}; {bl[:1]}")
    raw0 = runner.run([DS.scenario("base"), DS.scenario("noise_reference", reseed=1, weight=0.0)])
    s0 = DS.summarize(raw0)["metrics"]["top1"]
    mb = s0["by_scenario"]["base"]["mean"]
    nr = next(s for s in sc if s["name"] == "noise_reference")
    ck("SN17 a weight-0 scenario is a diagnostic: the noise reference (the preset's, weight 0) still gets its own "
       "row and z-scores, but the across-scenario figures -- min, max, worst regret, top-N share, the robust ranks "
       "-- are the weighted scenarios' alone (here the base's own)",
       nr["weight"] == 0.0 and "noise_reference" in s0["by_scenario"]
       and all(r["min"] == r["max"] == mb[r["lineup"]] and r["worst_regret_scenario"] == "base" for r in s0["by_lineup"])
       and s0["by_scenario"]["noise_reference"]["mean"] != mb
       and s0["seed_check"]["scenarios"] == ["noise_reference"]
       and abs(s0["seed_check"]["mean_z2"] - float(np.mean(np.square(s0["by_scenario"]["noise_reference"]["z"])))) < 1e-3)
    ew = DS.EngineWorlds(12, ["AAA"], 100, seed=5, season="2026", games={}, ctx={}, anchor_n=3200)
    ck("SN18 a non-default anchor size is mixed into the worlds' seed (pool_cache's key does not carry it, so an "
       "entry of one anchor size can never answer for another) and reaches the engine's context",
       ew.seed != 5 and ew.anchor_n == 3200 and DS.EngineWorlds(12, ["AAA"], 100, seed=5, season="2026").seed == 5
       and 'ctx["anchor_n"] = self.anchor_n' in src)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
