"""The moment harness for the constrained simulator (Stages 2D, 2E):
the training seasons' games rebuilt as game dicts from the table, the
simulator run on them with seeded worlds, and one moment function that
turns EITHER a simulated sample (K worlds per game) OR the observed
sample (the one world that happened) into the same statistics --
role-pair correlations of standardised projection residuals, marginal
ratio distributions by role, component ratio dispersions, target
competition, team touchdown distributions and stack ratios -- so the fit
compares like with like and no definition can drift between the two
sides. The observed moments are cross-checked against the Stage 2B
artifact (research.stage2d does this) before anything is fitted.

Numpy only. Roles come from the table (pregame projections)."""
import collections
import numpy as np

from research import hist_data as H
from research import seeds as SEEDS

ROLES = ("QB1", "RB1", "RB2", "WR1", "WR2", "WR3", "TE1")
TEAMMATE_PAIRS = (("QB1", "WR1"), ("QB1", "WR2"), ("QB1", "WR3"), ("QB1", "TE1"), ("QB1", "RB1"), ("QB1", "RB2"),
                  ("WR1", "WR2"), ("WR1", "TE1"), ("WR1", "RB1"), ("WR2", "TE1"), ("RB1", "RB2"))
OPPONENT_PAIRS = (("QB1", "QB1"), ("QB1", "WR1"), ("WR1", "WR1"), ("RB1", "RB1"), ("QB1", "RB1"))
COMP_MOMENTS = (("QB1", "pass_yd"), ("QB1", "pass_att"), ("RB1", "rush_att"), ("RB1", "rush_yd"), ("RB1", "rec_tgt"), ("RB1", "rec_yd"),
                ("WR1", "rec_tgt"), ("WR1", "rec"), ("WR1", "rec_yd"), ("WR2", "rec_tgt"), ("WR2", "rec_yd"), ("WR3", "rec_yd"), ("TE1", "rec_tgt"), ("TE1", "rec_yd"))
SHARE_ROLES = ("WR1", "WR2", "WR3", "TE1", "RB1")
STACKS = (("QB+WR1", ("QB1", "WR1"), ()), ("QB+WR2", ("QB1", "WR2"), ()), ("QB+TE1", ("QB1", "TE1"), ()), ("QB+RB1", ("QB1", "RB1"), ()),
          ("QB+WR1+WR2", ("QB1", "WR1", "WR2"), ()), ("QB+WR1+TE1", ("QB1", "WR1", "TE1"), ()), ("QB+WR1+RB1", ("QB1", "WR1", "RB1"), ()),
          ("QB+WR1+oppWR1", ("QB1", "WR1"), ("WR1",)), ("game 5: QB+WR1+WR2+oppWR1+oppWR2", ("QB1", "WR1", "WR2"), ("WR1", "WR2")),
          ("game 6: +RB1", ("QB1", "WR1", "WR2", "RB1"), ("WR1", "WR2")))
COMPS = ("pass_att", "pass_cmp", "pass_yd", "pass_td", "rush_att", "rush_yd", "rush_td", "rec_tgt", "rec", "rec_yd", "rec_td")
RATIO_PROJ_FLOOR = 3.0        # DraftKings points; below it a ratio says nothing about the model


def games_from_table(rows, seasons, with_actuals=True):
    """Game dicts (the weekly_games shape plus `row`) for the kept, really
    projected player-weeks of the seasons, both teams of each game together,
    reconciled (nfl_recon attaches `recon`)."""
    import nfl_recon
    by = collections.defaultdict(list)
    for r in rows:
        real = r["has_proj"] and (r["proj_dk"] or 0) > 0
        played = with_actuals and r["has_act"] and (r.get("act_gp") or 0) >= 1
        if r["season"] in seasons and r["keep"] and r["team"] and (real or played):
            by[(r["season"], r["week"], r["game_key"])].append(r)
    games = {}
    for key, rr in sorted(by.items()):
        teams = sorted({r["team"] for r in rr if r["has_proj"] and (r["proj_dk"] or 0) > 0})
        if len(teams) != 2:
            continue
        players = []
        for r in rr:
            if not (r["has_proj"] and (r["proj_dk"] or 0) > 0):
                # played without a projection (the backup who came in): nothing
                # for the simulator, but his box score belongs to the team's
                # actual totals, as it does in the Stage 2B statistics
                players.append({"name": r["name"], "pos": r["pos"], "team": r["team"], "opp": r["opp"], "role": "",
                                "means": {}, "proj_pts": 0.0, "row": r, "player_id": r["player_id"], "actual_only": True})
                continue
            means = {"pass_yd": r["proj_pass_yd"] or 0.0, "pass_td": r["proj_pass_td"] or 0.0, "int": r["proj_pass_int"] or 0.0,
                     "rush_yd": r["proj_rush_yd"] or 0.0, "rush_td": r["proj_rush_td"] or 0.0, "rec": r["proj_rec"] or 0.0,
                     "rec_yd": r["proj_rec_yd"] or 0.0, "rec_td": r["proj_rec_td"] or 0.0, "fum": r["proj_fum_lost"] or 0.0,
                     "pass_att": r["proj_pass_att"] or 0.0, "pass_cmp": r["proj_pass_cmp"] or 0.0, "rec_tgt": r["proj_rec_tgt"] or 0.0,
                     "rush_att": r["proj_rush_att"] or 0.0}
            players.append({"name": r["name"], "pos": r["pos"], "team": r["team"], "opp": r["opp"], "role": r["role"],
                            "means": means, "proj_pts": float(r["proj_dk"]), "row": r if with_actuals else None,
                            "player_id": r["player_id"]})
        games[key] = {"label": f"{teams[1]} @ {teams[0]}", "teams": teams, "players": players, "season": key[0], "week": key[1], "game_key": key[2]}
    nfl_recon.reconcile_games(games)
    return games


def simulate_sample(games, params, K, base_seed, mode="constrained"):
    """The simulator on every game, K worlds each, child-seeded per game.
    Returns the sample: per game, the players and (P, K) arrays of points
    and components (float32)."""
    import nfl_dfs_csim as C
    sample = []
    for key, g in games.items():
        seed = SEEDS.child_seed(base_seed, g["season"], g["week"], g["game_key"], mode)
        rng = np.random.default_rng(seed)
        sim = C.simulate_game(g, n=K, rng=rng, params=params)
        P = len(g["players"])
        pts = np.asarray([p["arr"] for p in sim["players"]], dtype=np.float32)
        comps = {}
        for c in COMPS:
            a = np.zeros((P, K), dtype=np.float32)
            for i in range(P):
                v = sim["components"][i].get(c)
                if v is not None:
                    a[i] = v
            comps[c] = a
        sample.append({"key": key, "players": g["players"], "teams": g["teams"], "pts": pts, "comps": comps, "K": K})
    return sample


def observed_sample(games):
    """The one world that happened, in the sample's shape (K = 1); players
    without a box score are masked with NaN."""
    sample = []
    for key, g in games.items():
        P = len(g["players"])
        pts = np.full((P, 1), np.nan, dtype=np.float32)
        comps = {c: np.full((P, 1), np.nan, dtype=np.float32) for c in COMPS}
        for i, p in enumerate(g["players"]):
            r = p["row"]
            if r and r["has_act"] and (r.get("act_gp") or 0) >= 1:
                pts[i, 0] = r["act_dk"]
                for c in COMPS:
                    comps[c][i, 0] = r.get(f"act_{c}") or 0.0
        sample.append({"key": key, "players": g["players"], "teams": g["teams"], "pts": pts, "comps": comps, "K": 1})
    return sample


def _q(a, ps):
    a = np.asarray(a, dtype=np.float64)
    return [float(np.nanquantile(a, p)) for p in ps]


def moments(sample):
    """The statistics on a sample (simulated or observed): every value a
    plain float; NaN worlds (no box score) are dropped where they occur."""
    # ---- gather per (game, team) the role rows ----
    resid = collections.defaultdict(list)          # role -> list of arrays (K,) of residuals
    ratio = collections.defaultdict(list)          # role -> arrays of act/proj
    comp_ratio = collections.defaultdict(list)     # (role, comp) -> arrays
    team_roles = []                                # per (game, team): {role: (pts K, proj)} plus opp key
    tgt_team = []                                  # per team: (sum act tgt over share roles K, sum proj tgt)
    shares = collections.defaultdict(list)         # role -> arrays of share residuals
    share_pairs = collections.defaultdict(list)
    td_pass, td_rush, two_scorers = [], [], []
    stacks = collections.defaultdict(list)
    team_off = []                                  # per game: (team A pts K, team B pts K) residual sums
    for gm in sample:
        pts, comps, K = gm["pts"], gm["comps"], gm["K"]
        by_team = {}
        for i, p in enumerate(gm["players"]):
            by_team.setdefault(p["team"], []).append(i)
        troles = {}
        for t, idx in by_team.items():
            roles = {}
            for i in idx:
                role = gm["players"][i].get("role") or ""
                if role and role not in roles:
                    roles[role] = i
            troles[t] = roles
            for role, i in roles.items():
                if role not in ROLES:
                    continue
                pj = gm["players"][i]["proj_pts"]
                resid[role].append(pts[i] - pj)
                # ratios need a real projection under them: Sleeper once gave a
                # starting quarterback 26.5 attempts and 0.3 points (Ehlinger,
                # 2022 week 10), and one such row at 200 worlds would own the
                # role's ratio sd. The floor applies to both sides alike.
                ratio[role].append(pts[i] / pj if pj >= RATIO_PROJ_FLOOR else np.full(K, np.nan))
                for (rr, c) in COMP_MOMENTS:
                    if rr == role:
                        pm = float((gm["players"][i]["means"].get(c) or 0.0))
                        if pm > 0:
                            comp_ratio[(role, c)].append(comps[c][i] / pm)
            # target competition (the Stage 2B definition): team totals over
            # every catcher with a projected target, at least 15 projected
            # and 10 actual, the five roles all present; a catcher without a
            # box score is simply absent from the actual total
            catchers = [i for i in idx if gm["players"][i]["pos"] in ("WR", "TE", "RB") and (gm["players"][i]["means"].get("rec_tgt") or 0.0) > 0
                        and not np.isnan(comps["rec_tgt"][i]).all()]      # projected and played (every simulated man plays)
            if all(r in roles for r in SHARE_ROLES) and catchers:
                pt_all = sum(gm["players"][i]["means"].get("rec_tgt") or 0.0 for i in catchers)
                at_all = np.nansum(np.stack([comps["rec_tgt"][i] for i in catchers]), axis=0)
                ok = (pt_all >= 15) & (at_all >= 10)
                if ok.any():
                    ii = [roles[r] for r in SHARE_ROLES]
                    pt = np.asarray([gm["players"][i]["means"].get("rec_tgt") or 0.0 for i in ii])
                    at = np.stack([comps["rec_tgt"][i] for i in ii])       # (5, K)
                    sel = ok & ~np.isnan(at).any(axis=0)
                    if sel.any():
                        tgt_team.append(at_all[sel] / pt_all)
                        sr = at[:, sel] / at_all[sel][None, :] - (pt / pt_all)[:, None]
                        for k, r in enumerate(SHARE_ROLES):
                            shares[r].append(sr[k])
                        for a_ in range(5):
                            for b_ in range(a_ + 1, 5):
                                share_pairs[(SHARE_ROLES[a_], SHARE_ROLES[b_])].append((sr[a_], sr[b_]))
            # team touchdowns (the Stage 2B definition): every passer's and
            # every rusher's, the scorers counted over the whole team
            ptd = np.nansum(np.stack([comps["pass_td"][i] for i in idx]), axis=0)
            rtd = np.nansum(np.stack([comps["rush_td"][i] for i in idx]), axis=0)
            played = ~np.isnan(np.stack([pts[i] for i in idx])).all(axis=0)
            if played.any():
                td_pass.append(ptd[played]); td_rush.append(rtd[played])
                sc = np.nansum(np.stack([comps["rec_td"][i] > 0 for i in idx]), axis=0)
                two_scorers.append((ptd[played] >= 2, sc[played] >= 2))
            team_roles.append((gm["key"], t, roles))
        # opponent pairs and stacks need both teams
        ts = list(by_team)
        if len(ts) == 2:
            A, B = ts
            ra, rb = troles[A], troles[B]
            # team offense (the Stage 2B definition): residuals summed over the roled players who played
            oa = np.nansum(np.stack([pts[i] - gm["players"][i]["proj_pts"] for i in by_team[A] if gm["players"][i].get("role")]), axis=0)
            ob = np.nansum(np.stack([pts[i] - gm["players"][i]["proj_pts"] for i in by_team[B] if gm["players"][i].get("role")]), axis=0)
            team_off.append((oa, ob))
            for name, mine, theirs in STACKS:
                for me, opp in ((ra, rb), (rb, ra)):
                    if all(r in me for r in mine) and all(r in opp for r in theirs):
                        ii = [me[r] for r in mine] + [opp[r] for r in theirs]
                        pj = sum(gm["players"][i]["proj_pts"] for i in ii)
                        if pj > 0:
                            stacks[name].append(sum(pts[i] for i in ii) / pj)
    out = {}
    # ---- standardisation: residual sd per role over the whole sample ----
    sd = {}
    for role in ROLES:
        a = np.concatenate(resid[role]) if resid[role] else np.zeros(0)
        sd[role] = float(np.nanstd(a)) if len(a) > 10 else 1.0
    # ---- pairs ----
    key_of = {(k, t): r for k, t, r in team_roles}
    game_teams = collections.defaultdict(list)
    for k, t, _ in team_roles:
        game_teams[k].append(t)
    pts_of = {gm["key"]: gm for gm in sample}
    def z(gm, i, role):
        return (gm["pts"][i] - gm["players"][i]["proj_pts"]) / sd[role]
    for a, b in TEAMMATE_PAIRS:
        xs, ys = [], []
        for k, t, roles in team_roles:
            if a in roles and b in roles:
                gm = pts_of[k]
                xs.append(z(gm, roles[a], a)); ys.append(z(gm, roles[b], b))
        out[f"pair {a}/{b}"] = _corr(np.concatenate(xs), np.concatenate(ys)) if xs else None
    for a, b in OPPONENT_PAIRS:
        xs, ys = [], []
        for k, ts in game_teams.items():
            if len(ts) != 2:
                continue
            gm = pts_of[k]
            for me, opp in ((ts[0], ts[1]), (ts[1], ts[0])):
                if a == b and me > opp:
                    continue
                ra, rb = key_of[(k, me)], key_of[(k, opp)]
                if a in ra and b in rb:
                    xs.append(z(gm, ra[a], a)); ys.append(z(gm, rb[b], b))
        out[f"pair {a}/opp {b}"] = _corr(np.concatenate(xs), np.concatenate(ys)) if xs else None
    if team_off:
        out["pair team offense/opp offense"] = _corr(np.concatenate([a for a, _ in team_off]), np.concatenate([b for _, b in team_off]))
    # ---- marginals ----
    for role in ROLES:
        r = np.concatenate(ratio[role]) if ratio[role] else np.zeros(0)
        r = r[~np.isnan(r)]
        if len(r) < 30:
            continue
        q = _q(r, (0.1, 0.5, 0.9, 0.95, 0.99))
        out[f"marg {role} ratio_sd"] = float(r.std())
        out[f"marg {role} p10"] = q[0]; out[f"marg {role} p50"] = q[1]; out[f"marg {role} p90"] = q[2]; out[f"marg {role} p99"] = q[4]
        out[f"marg {role} p_under_half"] = float((r < 0.5).mean()); out[f"marg {role} p_over_1_5"] = float((r > 1.5).mean()); out[f"marg {role} p_over_2"] = float((r > 2.0).mean())
        out[f"marg {role} n"] = int(len(r))
    # ---- components ----
    for (role, c), lst in comp_ratio.items():
        r = np.concatenate(lst); r = r[~np.isnan(r)]
        if len(r) >= 30:
            out[f"comp {role} {c} ratio_sd"] = float(r.std())
            out[f"comp {role} {c} p10"] = float(np.quantile(r, 0.1)); out[f"comp {role} {c} p90"] = float(np.quantile(r, 0.9))
    # ---- target competition ----
    if tgt_team:
        tt = np.concatenate(tgt_team); tt = tt[~np.isnan(tt)]
        out["tgt team_ratio_sd"] = float(tt.std()); out["tgt team_ratio_mean"] = float(tt.mean())
        for r in SHARE_ROLES:
            s = np.concatenate(shares[r]); s = s[~np.isnan(s)]
            out[f"tgt share_resid_sd {r}"] = float(s.std())
        for (a, b), lst in share_pairs.items():
            xs = np.concatenate([x for x, _ in lst]); ys = np.concatenate([y for _, y in lst])
            out[f"tgt share_corr {a}/{b}"] = _corr(xs, ys)
    # ---- touchdowns ----
    if td_pass:
        tp = np.concatenate(td_pass); tp = tp[~np.isnan(tp)]
        tr = np.concatenate(td_rush); tr = tr[~np.isnan(tr)]
        for k in range(5):
            out[f"td pass {k}"] = float((np.round(tp) == k).mean())
        out["td pass 5+"] = float((np.round(tp) >= 5).mean()); out["td pass mean"] = float(tp.mean())
        for k in range(4):
            out[f"td rush {k}"] = float((np.round(tr) == k).mean())
        out["td rush 4+"] = float((np.round(tr) >= 4).mean()); out["td rush mean"] = float(tr.mean())
        c2 = np.concatenate([c for c, _ in two_scorers]); s2 = np.concatenate([s for _, s in two_scorers])
        out["td two_scorers_given_2plus"] = float(s2[c2].mean()) if c2.any() else None
    # ---- stacks ----
    for name, lst in stacks.items():
        r = np.concatenate(lst); r = r[~np.isnan(r)]
        if len(r) >= 30:
            out[f"stack {name} ratio_var"] = float(r.var()); out[f"stack {name} p_over_1_5"] = float((r > 1.5).mean())
            out[f"stack {name} p_over_2"] = float((r > 2.0).mean()); out[f"stack {name} p_under_0_5"] = float((r < 0.5).mean())
            out[f"stack {name} ratio_mean"] = float(r.mean())
    out["_role_sd"] = sd
    return out


def _corr(x, y):
    m = ~(np.isnan(x) | np.isnan(y))
    x, y = x[m], y[m]
    if len(x) < 10 or x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


# ---- the objective ----------------------------------------------------------
# Which moments the fit matches, with the scale each mismatch is measured
# against (a correlation's clustered-bootstrap sd is ~0.02 on these
# seasons; a ratio sd is trusted to ~5%; a touchdown probability to 0.01).
def objective_terms(sim_m, obs_m):
    """[(name, sim, obs, scale)] for every moment the fit uses."""
    terms = []
    for k, v in obs_m.items():
        if k.startswith("_") or v is None or sim_m.get(k) is None:
            continue
        if k.startswith("pair "):
            scale = 0.02
        elif k.startswith("marg ") and k.endswith("ratio_sd"):
            scale = 0.05 * v
        elif k.startswith("marg ") and ("p_over" in k or "p_under" in k):
            scale = 0.015
        elif k.startswith("comp ") and k.endswith("ratio_sd"):
            scale = 0.06 * v
        elif k.startswith("tgt share_resid_sd") or k == "tgt team_ratio_sd":
            scale = 0.05 * v
        elif k.startswith("tgt share_corr"):
            scale = 0.03
        elif k.startswith("td ") and k not in ("td pass mean", "td rush mean") and "two" not in k:
            scale = 0.012
        elif k == "td two_scorers_given_2plus":
            scale = 0.02
        elif k.startswith("stack ") and k.endswith("ratio_var"):
            scale = 0.08 * v
        else:
            continue
        terms.append((k, float(sim_m[k]), float(v), float(scale)))
    return terms


def objective(sim_m, obs_m):
    t = objective_terms(sim_m, obs_m)
    return float(sum(((s - o) / sc) ** 2 for _, s, o, sc in t)) / max(1, len(t)), t


def nelder_mead(f, x0, step, iters=200, tol=1e-4, log=None):
    """A plain Nelder-Mead in numpy (no scipy here): reflection, expansion,
    contraction, shrink. Returns (best x, best f, evaluations)."""
    n = len(x0)
    simplex = [np.asarray(x0, dtype=float)]
    for i in range(n):
        x = np.asarray(x0, dtype=float).copy(); x[i] += step[i]
        simplex.append(x)
    vals = [f(x) for x in simplex]
    evals = n + 1
    for it in range(iters):
        order = np.argsort(vals)
        simplex = [simplex[i] for i in order]; vals = [vals[i] for i in order]
        if log:
            log(it, vals[0], simplex[0])
        if abs(vals[-1] - vals[0]) < tol * max(1e-9, abs(vals[0])):
            break
        centroid = np.mean(simplex[:-1], axis=0)
        xr = centroid + (centroid - simplex[-1]); fr = f(xr); evals += 1
        if fr < vals[0]:
            xe = centroid + 2.0 * (centroid - simplex[-1]); fe = f(xe); evals += 1
            if fe < fr:
                simplex[-1], vals[-1] = xe, fe
            else:
                simplex[-1], vals[-1] = xr, fr
        elif fr < vals[-2]:
            simplex[-1], vals[-1] = xr, fr
        else:
            xc = centroid + 0.5 * (simplex[-1] - centroid); fc = f(xc); evals += 1
            if fc < vals[-1]:
                simplex[-1], vals[-1] = xc, fc
            else:
                for i in range(1, len(simplex)):
                    simplex[i] = simplex[0] + 0.5 * (simplex[i] - simplex[0]); vals[i] = f(simplex[i]); evals += 1
    i = int(np.argmin(vals))
    return simplex[i], vals[i], evals


def roles_for_game(players):
    """Pregame roles for a live game dict (weekly_games shape), the table's
    rule: QB1 by projected attempts; RB1-3 by carries plus targets; WR1-4
    and TE1-2 by targets with receiving yards as the tie-break."""
    by_team = collections.defaultdict(list)
    for p in players:
        by_team[p.get("team")].append(p)
    for t, pls in by_team.items():
        rows = [{"pos": p["pos"], "has_proj": True, "proj_dk": p.get("proj_pts") or 0.0, "pid": id(p),
                 "p": {"pass_att": (p.get("means") or {}).get("pass_att"), "pass_yd": (p.get("means") or {}).get("pass_yd"),
                       "rush_att": (p.get("means") or {}).get("rush_att"), "rec_tgt": (p.get("means") or {}).get("rec_tgt"),
                       "rec_yd": (p.get("means") or {}).get("rec_yd")}} for p in pls]
        roles = H._roles(rows)
        for p, r in zip(pls, rows):
            p["role"] = roles.get(r["pid"], "")
    return players
