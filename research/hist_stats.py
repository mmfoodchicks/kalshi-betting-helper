"""Statistics on the historical player-week table (research.hist_data):
projection residuals, role-pair co-movement with clustered bootstraps,
marginal and component distributions by role, target competition, the
touchdown record, Sleeper's own accounting inconsistencies, stack totals
against their projections, and the receiving-back buckets. Every function
takes the seasons it may read, so the fitting stages pass the training
seasons and the validation stage passes 2025 once parameters are frozen.

Co-movement is measured on projection residuals standardised by role
(actual minus projected DraftKings points over the role's residual sd),
so a pair correlation says whether two players beat or miss their
projections TOGETHER, not that good players score more; the within-pair
demeaned variant (pairs seen six or more times) removes any pair-level
bias as well. Numpy only."""
import collections
import numpy as np

TRAIN = (2022, 2023, 2024)
ROLES = ("QB1", "RB1", "RB2", "WR1", "WR2", "WR3", "TE1")
TEAMMATE_PAIRS = (("QB1", "WR1"), ("QB1", "WR2"), ("QB1", "WR3"), ("QB1", "TE1"), ("QB1", "RB1"), ("QB1", "RB2"),
                  ("WR1", "WR2"), ("WR1", "TE1"), ("WR1", "RB1"), ("WR2", "TE1"), ("RB1", "RB2"))
OPPONENT_PAIRS = (("QB1", "QB1"), ("QB1", "WR1"), ("WR1", "WR1"), ("RB1", "RB1"), ("QB1", "RB1"))


def scored(rows, seasons):
    """Rows usable for residuals: kept, a real projection, played."""
    return [r for r in rows if r["season"] in seasons and r["keep"] and r["has_proj"] and r["has_act"]
            and (r["proj_dk"] or 0) > 0 and (r.get("act_gp") or 0) >= 1]


def _q(a, ps):
    a = np.asarray(a, dtype=np.float64)
    return [float(np.quantile(a, p)) for p in ps] if len(a) else [None] * len(ps)


def role_sd(rows):
    """Residual sd per role, the standardisation scale."""
    out = {}
    for role in ROLES:
        r = np.asarray([x["act_dk"] - x["proj_dk"] for x in rows if x["role"] == role])
        out[role] = float(r.std()) if len(r) > 10 else 1.0
    return out


def _index(rows):
    by = collections.defaultdict(dict)          # (season, week, team) -> {role: row}
    for r in rows:
        if r["role"]:
            by[(r["season"], r["week"], r["team"])][r["role"]] = r
    return by


def pair_obs(rows, a, b, opponent=False):
    """Observations for one relationship: (za, zb, pair_key, cluster_key, season)."""
    sd = role_sd(rows)
    by = _index(rows)
    out = []
    for (season, week, team), roles in by.items():
        ra = roles.get(a)
        if ra is None:
            continue
        if opponent:
            other = by.get((season, week, ra["opp"]))
            rb = other.get(b) if other else None
            if rb is None or rb["opp"] != team:
                continue
            if a == b and team > ra["opp"]:
                continue                    # each game once
        else:
            rb = roles.get(b)
            if rb is None or rb is ra:
                continue
        za = (ra["act_dk"] - ra["proj_dk"]) / sd[a]
        zb = (rb["act_dk"] - rb["proj_dk"]) / sd[b]
        pair_key = (ra["player_id"], rb["player_id"])
        cluster = (season, team)
        out.append((za, zb, pair_key, cluster, season))
    return out


def _corr(x, y):
    x = np.asarray(x, dtype=np.float64); y = np.asarray(y, dtype=np.float64)
    if len(x) < 8:
        return None
    x = x - x.mean(); y = y - y.mean()
    d = np.sqrt((x * x).sum() * (y * y).sum())
    return float((x * y).sum() / d) if d > 0 else None


def relationship(obs, rng, boots=400, min_pair=6):
    """Pooled and per-season correlation, the within-pair demeaned variant,
    counts, and a clustered bootstrap interval (clusters: team-season, so the
    repeated weeks of one offense are resampled together)."""
    if not obs:
        return {"n": 0}
    za = np.asarray([o[0] for o in obs]); zb = np.asarray([o[1] for o in obs])
    pairs = [o[2] for o in obs]; clusters = [o[3] for o in obs]; seasons = [o[4] for o in obs]
    pooled = _corr(za, zb)
    per_season = {}
    for s in sorted(set(seasons)):
        m = np.asarray([x == s for x in seasons])
        per_season[str(s)] = _corr(za[m], zb[m])
    # within-pair demeaned
    grp = collections.defaultdict(list)
    for i, k in enumerate(pairs):
        grp[k].append(i)
    xs, ys = [], []
    for k, ix in grp.items():
        if len(ix) >= min_pair:
            xs += list(za[ix] - za[ix].mean()); ys += list(zb[ix] - zb[ix].mean())
    demeaned = _corr(xs, ys)
    # clustered bootstrap
    cl = sorted(set(clusters))
    members = collections.defaultdict(list)
    for i, c in enumerate(clusters):
        members[c].append(i)
    boot = []
    for _ in range(boots):
        pick = rng.choice(len(cl), size=len(cl), replace=True)
        ix = np.concatenate([np.asarray(members[cl[j]]) for j in pick])
        c = _corr(za[ix], zb[ix])
        if c is not None:
            boot.append(c)
    lo, hi = (float(np.quantile(boot, 0.05)), float(np.quantile(boot, 0.95))) if boot else (None, None)
    return {"n": int(len(obs)), "pairs": int(len(grp)), "pairs_6plus": int(sum(1 for ix in grp.values() if len(ix) >= min_pair)),
            "team_seasons": int(len(cl)), "teams": int(len({c[1] for c in cl})),
            "pooled": pooled, "demeaned": demeaned, "per_season": per_season,
            "boot_lo": lo, "boot_hi": hi, "boot_sd": float(np.std(boot)) if boot else None}


def all_relationships(rows, seasons, rng, boots=400):
    sc = scored(rows, seasons)
    out = {}
    for a, b in TEAMMATE_PAIRS:
        out[f"{a}/{b}"] = relationship(pair_obs(sc, a, b), rng, boots)
    for a, b in OPPONENT_PAIRS:
        out[f"{a}/opp {b}"] = relationship(pair_obs(sc, a, b, opponent=True), rng, boots)
    # team offense against the opposing offense: residual sums over the roles
    by = _index(sc)
    tot = {}
    for key, roles in by.items():
        tot[key] = sum(r["act_dk"] - r["proj_dk"] for r in roles.values())
    obs = []
    for (season, week, team), v in tot.items():
        opp = next((r["opp"] for r in by[(season, week, team)].values()), None)
        if opp and (season, week, opp) in tot and team < opp:
            obs.append((v, tot[(season, week, opp)], (team, opp), (season, team), season))
    out["team offense/opp offense"] = relationship(obs, rng, boots)
    return out


def marginals(rows, seasons):
    sc = scored(rows, seasons)
    out = {}
    for role in ROLES:
        rr = [r for r in sc if r["role"] == role]
        if len(rr) < 30:
            continue
        act = np.asarray([r["act_dk"] for r in rr]); proj = np.asarray([r["proj_dk"] for r in rr])
        ratio = act / proj
        res = act - proj
        m3 = float(((res - res.mean()) ** 3).mean() / (res.std() ** 3)) if res.std() > 0 else None
        out[role] = {"n": int(len(rr)), "proj_mean": float(proj.mean()), "act_mean": float(act.mean()),
                     "bias_pts": float(res.mean()), "resid_sd": float(res.std()), "ratio_mean": float(ratio.mean()),
                     "ratio_sd": float(ratio.std()), "cv_actual": float(act.std() / act.mean()), "resid_skew": m3,
                     "ratio_q": dict(zip(("p10", "p50", "p90", "p95", "p99"), _q(ratio, (0.1, 0.5, 0.9, 0.95, 0.99)))),
                     "p_under_half": float((ratio < 0.5).mean()), "p_over_1_5": float((ratio > 1.5).mean()),
                     "p_over_2": float((ratio > 2.0).mean())}
    return out


def _dist(vals, kmax=5):
    vals = np.asarray(vals)
    return {str(k) if k < kmax else f"{kmax}+": float((vals == k).mean() if k < kmax else (vals >= kmax).mean()) for k in range(kmax + 1)}


def components(rows, seasons):
    sc = scored(rows, seasons)
    out = {}
    def ratio_stats(rr, comp):
        a = np.asarray([r[f"act_{comp}"] or 0.0 for r in rr]); p = np.asarray([r[f"proj_{comp}"] or 0.0 for r in rr])
        m = p > 0
        if m.sum() < 30:
            return None
        ratio = a[m] / p[m]
        return {"n": int(m.sum()), "proj_mean": float(p[m].mean()), "act_mean": float(a[m].mean()),
                "resid_sd": float((a[m] - p[m]).std()), "ratio_sd": float(ratio.std()),
                "ratio_q": dict(zip(("p10", "p50", "p90", "p99"), _q(ratio, (0.1, 0.5, 0.9, 0.99))))}
    qb = [r for r in sc if r["role"] == "QB1"]
    out["QB1"] = {"pass_yd": ratio_stats(qb, "pass_yd"), "pass_att": ratio_stats(qb, "pass_att"), "rush_yd": ratio_stats(qb, "rush_yd"),
                  "pass_td_dist": _dist([r["act_pass_td"] or 0 for r in qb]), "pass_td_proj_mean": float(np.mean([r["proj_pass_td"] or 0 for r in qb])),
                  "int_dist": _dist([r["act_pass_int"] or 0 for r in qb], 3), "int_proj_mean": float(np.mean([r["proj_pass_int"] or 0 for r in qb]))}
    for role in ("RB1", "RB2"):
        rr = [r for r in sc if r["role"] == role]
        out[role] = {c: ratio_stats(rr, c) for c in ("rush_att", "rec_tgt", "rec", "rush_yd", "rec_yd")}
        out[role]["rush_td_dist"] = _dist([r["act_rush_td"] or 0 for r in rr], 3)
        out[role]["rec_td_dist"] = _dist([r["act_rec_td"] or 0 for r in rr], 3)
    for role in ("WR1", "WR2", "WR3", "TE1"):
        rr = [r for r in sc if r["role"] == role]
        out[role] = {c: ratio_stats(rr, c) for c in ("rec_tgt", "rec", "rec_yd")}
        out[role]["rec_td_dist"] = _dist([r["act_rec_td"] or 0 for r in rr], 3)
    return out


def target_competition(rows, seasons):
    """Team-week target shares, actual against projected, by role; the
    co-movement of the roles' share residuals; what happens to the others
    when WR1 beats his target projection; week-to-week share volatility."""
    sc = scored(rows, seasons)
    by = _index(sc)
    roles = ("WR1", "WR2", "WR3", "TE1", "RB1")
    resid = {r: [] for r in roles}
    tgt_resid = {r: [] for r in roles}
    keys = []
    team_ratio = []
    for key, rr in by.items():
        catchers = [r for r in rr.values() if r["pos"] in ("WR", "TE", "RB") and (r["proj_rec_tgt"] or 0) > 0]
        t_act = sum((r["act_rec_tgt"] or 0) for r in catchers); t_proj = sum((r["proj_rec_tgt"] or 0) for r in catchers)
        if t_proj < 15 or t_act < 10 or not all(x in rr for x in roles):
            continue
        keys.append(key)
        team_ratio.append(t_act / t_proj)
        for role in roles:
            r = rr[role]
            resid[role].append((r["act_rec_tgt"] or 0) / t_act - (r["proj_rec_tgt"] or 0) / t_proj)
            tgt_resid[role].append((r["act_rec_tgt"] or 0) - (r["proj_rec_tgt"] or 0))
    n = len(keys)
    R = np.asarray([resid[r] for r in roles])
    C = np.corrcoef(R) if n > 30 else None
    share_corr = {f"{a}/{b}": float(C[i, j]) for i, a in enumerate(roles) for j, b in enumerate(roles) if i < j} if C is not None else {}
    T = np.asarray([tgt_resid[r] for r in roles])
    wr1 = T[0]
    hot = wr1 >= 3.0
    cold = wr1 <= -3.0
    when_wr1 = {r: {"hot_mean_resid": float(T[i][hot].mean()) if hot.any() else None,
                    "cold_mean_resid": float(T[i][cold].mean()) if cold.any() else None,
                    "slope_on_wr1": float(np.polyfit(wr1, T[i], 1)[0]) if n > 30 else None} for i, r in enumerate(roles) if i > 0}
    # week-to-week share volatility per player, by role
    vol = collections.defaultdict(list)
    per_player = collections.defaultdict(list)
    for key, rr in by.items():
        catchers = [r for r in rr.values() if r["pos"] in ("WR", "TE", "RB")]
        t_act = sum((r["act_rec_tgt"] or 0) for r in catchers)
        if t_act < 10:
            continue
        for r in catchers:
            if r["role"] in roles:
                per_player[(r["player_id"], r["role"])].append((r["act_rec_tgt"] or 0) / t_act)
    for (pid, role), sh in per_player.items():
        if len(sh) >= 6:
            vol[role].append(float(np.std(sh)))
    share_mean = {r: float(np.mean([(by[k][r]["act_rec_tgt"] or 0) for k in keys]) / np.mean([sum((x["act_rec_tgt"] or 0) for x in by[k].values() if x["pos"] in ("WR", "TE", "RB")) for k in keys])) for r in roles} if n else {}
    return {"team_weeks": n, "team_target_ratio_mean": float(np.mean(team_ratio)) if n else None,
            "team_target_ratio_sd": float(np.std(team_ratio)) if n else None,
            "share_mean_actual": share_mean,
            "share_resid_sd": {r: float(np.std(resid[r])) for r in roles} if n else {},
            "share_resid_corr": share_corr, "when_wr1_beats_targets_by_3": when_wr1,
            "week_to_week_share_sd_by_role": {r: (float(np.mean(v)) if v else None) for r, v in vol.items()},
            "players_with_6plus_weeks": {r: len(v) for r, v in vol.items()}}


def touchdowns(rows, seasons):
    sc = [r for r in rows if r["season"] in seasons and r["keep"] and r["has_act"] and (r.get("act_gp") or 0) >= 1]
    by = collections.defaultdict(list)
    for r in sc:
        by[(r["season"], r["week"], r["team"])].append(r)
    pass_td, rush_td, identity_ok, multi = [], [], 0, []
    rec_by_role = collections.Counter(); rec_tot = 0
    rush_by_role = collections.Counter(); rush_tot = 0
    for key, rr in by.items():
        ptd = sum((r["act_pass_td"] or 0) for r in rr); rtd = sum((r["act_rush_td"] or 0) for r in rr)
        rectd = sum((r["act_rec_td"] or 0) for r in rr)
        pass_td.append(ptd); rush_td.append(rtd)
        identity_ok += int(ptd == rectd)
        scorers = sum(1 for r in rr if (r["act_rec_td"] or 0) > 0)
        if ptd >= 2:
            multi.append(int(scorers >= 2))
        for r in rr:
            if (r["act_rec_td"] or 0) > 0:
                rec_by_role[r["role"] or ("other " + r["pos"])] += r["act_rec_td"]; rec_tot += r["act_rec_td"]
            if (r["act_rush_td"] or 0) > 0:
                rush_by_role[r["role"] or ("other " + r["pos"])] += r["act_rush_td"]; rush_tot += r["act_rush_td"]
    n = len(by)
    return {"team_weeks": n, "pass_td_mean": float(np.mean(pass_td)), "pass_td_dist": _dist(pass_td, 5),
            "rush_td_mean": float(np.mean(rush_td)), "rush_td_dist": _dist(rush_td, 4),
            "identity_pass_td_equals_sum_rec_td": float(identity_ok / n),
            "p_two_or_more_receivers_score_given_2plus_pass_td": float(np.mean(multi)) if multi else None,
            "rec_td_share_by_role": {k: float(v / rec_tot) for k, v in rec_by_role.most_common()},
            "rush_td_share_by_role": {k: float(v / rush_tot) for k, v in rush_by_role.most_common()}}


def sleeper_audit(rows, seasons):
    """Projections only (no outcomes): the accounting identities Sleeper's
    component means do not keep, per team-week."""
    pr = [r for r in rows if r["season"] in seasons and r["keep"] and r["has_proj"] and (r["proj_dk"] or 0) > 0]
    by = collections.defaultdict(list)
    for r in pr:
        by[(r["season"], r["week"], r["team"])].append(r)
    out = {}
    for name, qb_key, sum_key in (("pass_yd vs sum rec_yd", "proj_pass_yd", "proj_rec_yd"),
                                  ("pass_td vs sum rec_td", "proj_pass_td", "proj_rec_td"),
                                  ("pass_cmp vs sum rec", "proj_pass_att", "proj_rec")):
        diffs, rel, worst, by_season = [], [], [], collections.defaultdict(list)
        for key, rr in by.items():
            qbs = [r for r in rr if r["pos"] == "QB"]
            if not qbs:
                continue
            qv = sum((r[qb_key] or 0) for r in qbs)
            if name.startswith("pass_cmp"):
                qv = sum(((r["proj_pass_att"] or 0) * 0.65) for r in qbs)   # attempts x a 65% completion rate, a proxy
            sv = sum((r[sum_key] or 0) for r in rr if r["pos"] in ("RB", "WR", "TE"))
            if qv <= 0:
                continue
            d = sv - qv
            diffs.append(d); rel.append(d / qv); by_season[key[0]].append(d / qv)
            worst.append((abs(d), key, round(qv, 1), round(sv, 1)))
        diffs = np.asarray(diffs); rel = np.asarray(rel)
        worst.sort(reverse=True)
        out[name] = {"team_weeks": int(len(diffs)), "diff_median": float(np.median(diffs)), "diff_mean": float(diffs.mean()),
                     "rel_median": float(np.median(rel)), "rel_mean": float(rel.mean()),
                     "rel_p10": float(np.quantile(rel, 0.1)), "rel_p90": float(np.quantile(rel, 0.9)),
                     "abs_rel_p95": float(np.quantile(np.abs(rel), 0.95)), "share_sum_above_qb": float((diffs > 0).mean()),
                     "by_season_rel_mean": {str(s): float(np.mean(v)) for s, v in sorted(by_season.items())},
                     "worst": [{"season_week_team": list(k), "qb": q, "sum": s2, "abs_diff": round(a, 1)} for a, k, q, s2 in worst[:5]]}
    return out


STACKS = (("QB+WR1", ("QB1", "WR1"), ()), ("QB+WR2", ("QB1", "WR2"), ()), ("QB+TE1", ("QB1", "TE1"), ()),
          ("QB+RB1", ("QB1", "RB1"), ()), ("QB+WR1+WR2", ("QB1", "WR1", "WR2"), ()), ("QB+WR1+TE1", ("QB1", "WR1", "TE1"), ()),
          ("QB+WR1+RB1", ("QB1", "WR1", "RB1"), ()), ("QB+WR1+oppWR1", ("QB1", "WR1"), ("WR1",)),
          ("QB+WR1+WR2+oppWR1 (bring-back)", ("QB1", "WR1", "WR2"), ("WR1",)),
          ("game 5: QB+WR1+WR2+oppWR1+oppWR2", ("QB1", "WR1", "WR2"), ("WR1", "WR2")),
          ("game 6: +RB1", ("QB1", "WR1", "WR2", "RB1"), ("WR1", "WR2")))


def stacks(rows, seasons, receiving_rb_targets=4.0):
    sc = scored(rows, seasons)
    by = _index(sc)
    out = {}
    for name, own, opp in STACKS:
        ratios = []
        for (season, week, team), rr in by.items():
            if not all(x in rr for x in own):
                continue
            if name == "QB+RB1" and (rr["RB1"]["proj_rec_tgt"] or 0) < receiving_rb_targets:
                continue
            other = by.get((season, week, rr["QB1"]["opp"])) if opp else {}
            if opp and not all(x in (other or {}) for x in opp):
                continue
            a = sum(rr[x]["act_dk"] for x in own) + sum(other[x]["act_dk"] for x in opp)
            p = sum(rr[x]["proj_dk"] for x in own) + sum(other[x]["proj_dk"] for x in opp)
            if p > 0:
                ratios.append(a / p)
        r = np.asarray(ratios)
        if len(r) < 30:
            out[name] = {"n": int(len(r))}
            continue
        out[name] = {"n": int(len(r)), "ratio_mean": float(r.mean()), "ratio_var": float(r.var()),
                     "ratio_q": dict(zip(("p10", "p50", "p90", "p95", "p99"), _q(r, (0.1, 0.5, 0.9, 0.95, 0.99)))),
                     "p_over_1_5": float((r > 1.5).mean()), "p_over_2": float((r > 2.0).mean()), "p_under_0_5": float((r < 0.5).mean())}
    return out


def rb_buckets(rows, seasons):
    sc = scored(rows, seasons)
    sd = role_sd(sc)
    by = _index(sc)
    edges = ((0, 2), (2, 3), (3, 4), (4, 5), (5, 99))
    obs = collections.defaultdict(list)
    share_obs = collections.defaultdict(list)
    for key, rr in by.items():
        qb = rr.get("QB1")
        if qb is None:
            continue
        for role in ("RB1", "RB2", "RB3"):
            rb = rr.get(role)
            if rb is None:
                continue
            t = rb["proj_rec_tgt"] or 0.0
            zq = (qb["act_dk"] - qb["proj_dk"]) / sd["QB1"]; zr = (rb["act_dk"] - rb["proj_dk"]) / sd.get(role, sd["RB1"])
            for lo, hi in edges:
                if lo <= t < hi:
                    obs[f"targets {lo}-{hi if hi < 99 else '+'}"].append((zq, zr, qb["act_dk"] / qb["proj_dk"], rb["act_dk"] / rb["proj_dk"]))
            recv = ((rb["proj_rec"] or 0) + 0.1 * (rb["proj_rec_yd"] or 0) + 6 * (rb["proj_rec_td"] or 0)) / rb["proj_dk"] if rb["proj_dk"] else 0
            b = "recv share <20%" if recv < 0.2 else "20-35%" if recv < 0.35 else "35-50%" if recv < 0.5 else "50%+"
            share_obs[b].append((zq, zr, qb["act_dk"] / qb["proj_dk"], rb["act_dk"] / rb["proj_dk"]))
    def summar(lst):
        if len(lst) < 40:
            return {"n": len(lst)}
        a = np.asarray(lst)
        zq, zr = a[:, 0], a[:, 1]
        top = lambda v: v >= np.quantile(v, 0.9)
        joint_top = float((top(zq) & top(zr)).mean())
        boom = float(((a[:, 2] >= 1.5) & (a[:, 3] >= 1.5)).mean())
        return {"n": int(len(lst)), "corr": _corr(zq, zr), "joint_top_decile": joint_top, "joint_top_decile_if_independent": 0.01,
                "joint_boom_1_5x": boom, "p_qb_boom": float((a[:, 2] >= 1.5).mean()), "p_rb_boom": float((a[:, 3] >= 1.5).mean())}
    return {"by_projected_targets": {k: summar(v) for k, v in sorted(obs.items())},
            "by_receiving_share_of_projection": {k: summar(v) for k, v in share_obs.items()}}
