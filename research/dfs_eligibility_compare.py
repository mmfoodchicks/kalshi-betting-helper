"""The eligibility modes and the house rules, compared on one showdown's worlds (DESCRIPTIVE; 2026-11-27,
research/reports/dfs_eligibility.md). Nothing here chooses a production rule: the default is decided only by
research/data/dfs_eligibility/prereg.json on slates after its registration.

The slate: DEN @ PIT, DraftKings draft group 157076 (locked 2026-11-27 20:00 UTC). Its inputs were recorded
BEFORE lock by this session (pre-game only; the game's result is never read here): the production board's own
record (sim-history errors/tourney-157076.json, the 19:14 UTC refresh: players, salaries, projections, depth
tags, the gate's exclusions, the field's universe), DraftKings' pool (salaries of the six gated players the
board does not list), the contest's payout table, Sleeper's week-12 projection rows and box history, and
60,000 worlds of the game from the production simulator (nfl_dfs_sim.player_pool, model "engine", seed
20261127 -- production boards are unseeded, so these are NOT the board's worlds, but the same simulator on the
same inputs).

Every variant plays the SAME worlds against the SAME field: the field is the production board's (its 24
players -- the gate's 20 and the four field-only extras -- and field_weights over their legal lineups; the
script checks it holds exactly the board's 790,222 lineups and that the conservative rule set allows exactly
the board's 47,644). Worlds 0..29,999 CHOOSE (the board's lists and the greedy portfolio, production's own
run_vs_field / portfolio_vs_field); worlds 30,000..59,999 are INDEPENDENT and only score what was chosen, at
the field's per-world thresholds (dfs_evaluate's own threshold code), so every comparison is paired by world.

Variants
  conservative   today: the depth gate (QB1, RB1-2, WR1-3, TE1-2) and the house rules.
  adaptive       + the rank-gated players dfs_eligibility admits (Pittman, Mims, Hurleman, Adkins here; Adkins
                 is not a field extra, so production's adaptive -- which widens only from the field's extras --
                 admits the first three), the punt-role rule lifted for them.
  unrestricted   + every field extra (production's unrestricted), the punt-role rule lifted.
  unrestricted_all  + every gated player with a projection, also the six under 1.0 the field never plays.
  lift:<rule>    conservative with one house rule lifted (the audit); lift:all every house rule.
The house rules (nfl_dfs._sd_allowed and the board's cpt_ok): dst_vs_cpt (no defense beside the opposing
team's captain), kdst_cpt (no kicker or defense captain), cpt_salary ($11,000+ captain when three such exist),
kdst_max (at most two kickers/defenses), punt_max (at most two $2,000-or-under players), te_team (one tight end
a team), punt_role (a punt must be RB1-2, WR1-3 or TE1).

    python3 research/dfs_eligibility_compare.py <recorded inputs dir> [--workers 4]
writes research/data/dfs_eligibility/compare_157076.json
"""
import argparse
import gzip
import hashlib
import json
import os
import pickle
import resource
import subprocess
import sys
import time

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

import numpy as np  # noqa: E402

DG = 157076
CONTEST = 198174394
N_GEN = 30000
TOP = 20
CANDIDATES = 1200
SIZES = (5, 10, 20)
COVER_N = 10000
RULES = ("dst_vs_cpt", "kdst_cpt", "cpt_salary", "kdst_max", "punt_max", "te_team", "punt_role")
OUT = os.path.join(_ROOT, "research", "data", "dfs_eligibility", f"compare_{DG}.json")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:16]


# the files a recording holds: research/dfs_eligibility_record.py's names (with its meta.json), else the DEN @ PIT
# recording this study made by hand before that lock
_RECORDED = {"board": "board.json", "pool": "pool_engine.pkl", "dk": "dk_players.json", "contest": "contest.json",
             "sleeper": "sleeper_proj.json.gz", "usage": "usage_hist.json.gz", "roster": "roster.pkl"}
_DENPIT = {"board": "tourney-157076.json", "pool": "pool_engine.pkl", "dk": "dk_players_157076.json",
           "contest": "contest_198174394.json", "sleeper": "sleeper_proj_2026_w12.json.gz",
           "usage": "usage_hist_2026_w12.json.gz", "roster": "roster.pkl"}
_DENPIT_META = {"dg": DG, "contest": CONTEST, "seed": 20261127, "model": "engine", "lock_ts": 1795809600,
                "recorded_ts": 1795809114, "recorded_before_lock": True,
                "note": "recorded by this session 19:43-19:52 UTC (the board: its 19:14 build, read from sim-history at 19:41), before the 20:00 UTC lock"}


def names_for(d):
    if os.path.exists(os.path.join(d, "meta.json")):
        return _RECORDED, json.load(open(os.path.join(d, "meta.json")))
    return _DENPIT, dict(_DENPIT_META)


def load(d):
    fn, meta = names_for(d)
    board = json.load(open(os.path.join(d, fn["board"])))
    pool = pickle.load(open(os.path.join(d, fn["pool"]), "rb"))
    dkp = json.load(open(os.path.join(d, fn["dk"])))
    contest = json.load(open(os.path.join(d, fn["contest"])))
    return board, pool, dkp, contest, {f: sha(os.path.join(d, f)) for f in fn.values()
                                       if os.path.exists(os.path.join(d, f))}


def players(board, pool, dkp):
    """The 30 players: the board's 24 (its projections and tags, the field's universe) and the gated six it does
    not list (DraftKings' salaries, the pool's projection)."""
    dk_by = {p["name"]: p for p in dkp}
    ents = []
    for p in board["players"]:
        ents.append({"name": p["name"], "pos": p["pos"], "team": p["team"], "salary": int(p["salary"]),
                     "cpt_salary": int(p["cpt_salary"]), "proj": float(p["proj"]), "depth": p.get("depth"),
                     "field": True, "gated": bool(p.get("field_only"))})
    have = {e["name"] for e in ents}
    for d in board["excluded"]:
        if "by depth chart" not in (d.get("why") or "") or d["name"] in have:
            continue
        k = dk_by[d["name"]]
        ents.append({"name": d["name"], "pos": d["pos"], "team": d["team"], "salary": int(k["salary"]),
                     "cpt_salary": int(k["cpt_salary"]), "proj": round(float(pool[d["name"]]["proj"]), 1),
                     "depth": d["why"].split(" ")[0], "field": False, "gated": True})
    X = np.asarray([np.asarray(pool[e["name"]]["arr"], dtype=np.float32) for e in ents])
    return ents, X


def rule_masks(ents, idx, ours):
    """{rule: (L,) bool -- the row passes it} over every row, vectorised (the rules count, so order-free:
    exact to nfl_dfs._sd_allowed and the board's cpt_ok), and the `ours` mask. `ours` is a set of names."""
    import nfl_dfs
    pos = np.array([e["pos"] for e in ents])
    team = np.array([e["team"] for e in ents])
    sal = np.array([e["salary"] for e in ents])
    csal = np.array([e["cpt_salary"] for e in ents])
    tag = np.array([(e.get("depth") or "").split("·")[0] for e in ents])
    cap, fl = idx[:, 0], idx[:, 1:]
    P, Pf = pos[cap], pos[fl]
    kd = np.isin(Pf, ("K", "DST"))
    out = {}
    out["ours"] = np.isin(np.array([e["name"] for e in ents]), list(ours))[idx].all(axis=1)
    out["dst_vs_cpt"] = ~(((Pf == "DST") & (team[fl] != team[cap][:, None])).any(axis=1) & (P != "DST"))
    out["kdst_cpt"] = np.isin(P, list(nfl_dfs._SD_GPP_CPT_POS))
    rich = sum(1 for e in ents if e["name"] in ours and e["pos"] in nfl_dfs._SD_GPP_CPT_POS
               and e["cpt_salary"] >= nfl_dfs._SD_MIN_CPT_SALARY) >= 3
    out["cpt_salary"] = (csal[cap] >= nfl_dfs._SD_MIN_CPT_SALARY) if rich else np.ones(len(idx), bool)
    out["kdst_max"] = (np.isin(P, ("K", "DST")).astype(int) + kd.sum(axis=1)) <= nfl_dfs._SD_MAX_KDST
    punt_f = sal[fl] <= nfl_dfs._SD_PUNT_SALARY
    out["punt_max"] = (punt_f.any(axis=1) == False) | (  # noqa: E712 -- a punt FLEX counts the captain's FLEX salary
        ((sal[cap] <= nfl_dfs._SD_PUNT_SALARY).astype(int) + punt_f.sum(axis=1)) <= nfl_dfs._SD_MAX_PUNTS)
    te = (pos[idx] == "TE")
    te_ok = np.ones(len(idx), bool)
    for t in sorted(set(team)):
        te_ok &= (te & (team[idx] == t)).sum(axis=1) <= nfl_dfs._SD_MAX_TE_PER_TEAM
    out["te_team"] = te_ok
    role = np.array([(not t) or (t in nfl_dfs._SD_PUNT_ROLES) for t in tag])
    out["_role"] = role
    out["punt_role"] = ~(punt_f & ~role[fl]).any(axis=1)
    # the salary floor with kickers and defenses exempt: the only way a kicker or defense captain can exist on a
    # slate where the floor binds (research/data/dfs_house_rules/prereg.json, H2)
    out["_cpt_salary_xkd"] = out["cpt_salary"] | np.isin(P, ("K", "DST"))
    return out


def allowed(masks, lift=(), role_names=None, ents=None, idx=None):
    """Rows we may enter: `ours` and every house rule not lifted. `role_names` count as holding a punt role."""
    ok = masks["ours"].copy()
    for r in RULES:
        if r in lift:
            continue
        if r == "punt_role" and role_names:
            import nfl_dfs
            sal = np.array([e["salary"] for e in ents])
            role = masks["_role"] | np.isin(np.array([e["name"] for e in ents]), list(role_names))
            fl = idx[:, 1:]
            ok &= ~((sal[fl] <= nfl_dfs._SD_PUNT_SALARY) & ~role[fl]).any(axis=1)
            continue
        ok &= masks[r]
    return ok


def finals(X, WfT, f, qs, chunk=200):
    import dfs_evaluate
    return dfs_evaluate._cpu_chunks(X, WfT, f, np.asarray(qs, dtype=np.float64), chunk)


def buckets(W_rows, X):
    import dfs_tourney
    s = (W_rows @ X).astype(np.float64)
    return np.clip(np.floor(s * dfs_tourney._RES + 0.5), 0, dfs_tourney._NB - 1).astype(np.int64)


def paired(a, b):
    """mean(a - b), its SE over worlds, for per-world indicators/values on the same worlds."""
    d = np.asarray(a, float) - np.asarray(b, float)
    return round(float(d.mean()), 6), round(float(d.std(ddof=1) / np.sqrt(len(d))), 6)


def timing_child(args):
    """One mode's scoring pass, alone in this process: wall time and peak memory (the board's run_subset cost)."""
    import dfs_tourney
    z = np.load(args.timing, allow_pickle=False)
    W, Wf, f, X = z["Wc"], z["Wf"], z["f"], z["X"]
    grid = pickle.load(open(args.timing + ".grid", "rb"))
    t0 = time.time()
    dfs_tourney.run_vs_field(W, Wf, f, X, [grid], workers=args.workers)
    print(json.dumps({"seconds": round(time.time() - t0, 1),
                      "maxrss_mb_self": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                      "maxrss_mb_children": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="?")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=None, help="the result file (default research/data/dfs_eligibility/compare_<dg>.json; the registered read takes research/data/dfs_eligibility/prospective/)")
    ap.add_argument("--timing", default=None)
    ap.add_argument("--timing-worlds", type=int, default=3000)
    args = ap.parse_args()
    if args.timing:
        return timing_child(args)
    import dfs_eligibility as E
    import dfs_evaluate
    import dfs_portfolio
    import dfs_tourney
    import nfl_adp
    import nfl_dfs
    t_start = time.time()
    board, pool, dkp, contest, shas = load(args.inputs)
    ents, X = players(board, pool, dkp)
    names = [e["name"] for e in ents]
    N = X.shape[1]
    Xg, Xf = X[:, :N_GEN], X[:, N_GEN:]
    log = []

    def say(m):
        log.append(m)
        print(m, flush=True)

    t0 = time.time()
    idx, W, _ = dfs_tourney.enumerate_showdown(ents, nfl_dfs.CAP, cpt_mult=nfl_dfs.CPT_MULT)
    say(f"universe {len(idx):,} legal lineups over {len(ents)} players ({time.time() - t0:.0f}s)")
    field_names = {e["name"] for e in ents if e["field"]}
    frows = np.isin(np.array(names), list(field_names))[idx].all(axis=1)
    f_sub, beta = dfs_tourney.field_weights(ents, idx[frows], cpt_mult=nfl_dfs.CPT_MULT)
    Wf = W[frows]
    # ---- the eligibility verdicts, from the recorded Sleeper rows and history (pre-lock) ----
    fnames, meta = names_for(args.inputs)
    hist = json.load(gzip.open(os.path.join(args.inputs, fnames["usage"]), "rt"))["hist"]
    hist = {int(k): v for k, v in hist.items()}
    rows = json.load(gzip.open(os.path.join(args.inputs, fnames["sleeper"]), "rt"))["rows"]
    ids = {}
    for r in rows:
        p = r.get("player") or {}
        nm = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        ids.setdefault(nfl_adp._norm(nm), (str(r["player_id"]), r.get("team")))
    ctx = {"ids": ids, "hist": hist}
    kept = [e for e in ents if not e["gated"]]
    exc = [dict(d, proj=next((e["proj"] for e in ents if e["name"] == d["name"]), None)) for d in board["excluded"]]
    model = E.load_model()
    verdict = {m: E.assess(kept, exc, m, ctx, model) for m in E.MODES}
    adm = set(verdict["adaptive"]["admitted"])
    base = {e["name"] for e in ents if not e["gated"]}
    extras = {e["name"] for e in ents if e["gated"] and e["field"]}
    variants = {"conservative": (base, (), None),
                "adaptive": (base | (adm & extras), (), adm & extras),
                "adaptive_all": (base | adm, (), adm),
                "unrestricted": (base | extras, ("punt_role",), None),
                "unrestricted_all": (set(names), ("punt_role",), None)}
    for r in RULES:
        variants[f"lift:{r}"] = (base, (r,), None)
    variants["lift:all"] = (base, RULES, None)
    # the house-rule registration's combined primaries (research/data/dfs_house_rules/prereg.json)
    variants["lift:kdst_captain"] = (base, ("kdst_cpt", "cpt_salary"), None)
    variants["lift:punts"] = (base, ("punt_max", "punt_role"), None)
    variants["everything_off"] = (set(names), RULES, None)
    masks_by = {}
    for v, (ours, lift, role) in variants.items():
        mk = rule_masks(ents, idx, ours)
        masks_by[v] = allowed(mk, lift=lift, role_names=role, ents=ents, idx=idx)
        if v == "lift:kdst_captain":
            masks_by[v] &= mk["_cpt_salary_xkd"]          # the floor still binds every other captain
    n_cons = int(masks_by["conservative"].sum())
    check = {"field_rows": int(frows.sum()), "board_legal": board["lineups_legal"],
             "conservative_allowed": n_cons, "board_allowed": board["lineups_allowed"],
             "field_beta": round(beta, 4), "board_beta": board["field_model"]["beta"]}
    say(f"check {check}")
    if check["board_legal"] is not None and (check["field_rows"] != check["board_legal"]
                                             or n_cons != check["board_allowed"]):
        raise SystemExit("the rebuilt universe or rule set is not the board's: refusing to compare")
    # ---- the field's per-world thresholds, once per world set (dfs_evaluate's own code) ----
    C = dfs_tourney.contest_capacity(contest)
    tiers = dfs_evaluate.tier_fractions({"capacity": C, "payouts": contest["payouts"]})
    cash_q = round((int(contest["places_paid"]) - 1) / (C - 1), 12)
    qs = sorted({0.0, 0.001, 0.01, cash_q} | {round(q, 12) for q, _p in tiers})
    qi = {round(q, 12): i for i, q in enumerate(qs)}
    fee = float(contest["entry_fee"])
    WfT = np.ascontiguousarray(Wf.T)
    t0 = time.time()
    cache_T = os.path.join(args.inputs, "thresholds_" + hashlib.sha256(json.dumps(shas, sort_keys=True).encode()).hexdigest()[:12] + ".npz")
    if os.path.exists(cache_T):              # the field thresholds are a pure function of the recording (a restart reuses them)
        z = np.load(cache_T)
        Tg, Tf = z["Tg"], z["Tf"]
    else:
        Tg = finals(Xg, WfT, f_sub, qs)
        Tf = finals(Xf, WfT, f_sub, qs)
        np.savez(cache_T, Tg=Tg, Tf=Tf)
    say(f"field thresholds on {Xg.shape[1]:,} choosing and {Xf.shape[1]:,} independent worlds "
        f"({time.time() - t0:.0f}s)")

    def reach01(rows_, Xw, T):
        """(k, worlds) bool: the top-1% line reached (the choosing pass ranks on this alone, as the board's
        portfolio shortlist does)."""
        return buckets(W[rows_], Xw) >= T[:, qi[0.01]][None, :]

    def per_world(rows_, Xw, T):
        """(reach {q: (k, worlds) bool}, prize (k, worlds)): the evaluator's convention (ties not split)."""
        B = buckets(W[rows_], Xw)
        reach = {q: (B >= T[:, qi[round(q, 12)]][None, :]) for q in (0.0, 0.001, 0.01, cash_q)}
        prize = np.zeros(B.shape, dtype=np.float64)
        got = np.zeros(B.shape, dtype=bool)
        for q, p in tiers:
            hit = (~got) & (B >= T[:, qi[round(q, 12)]][None, :])
            prize[hit] = p
            got |= hit
        return reach, prize

    # ---- choosing: every row any choosing variant may enter, ranked on the choosing worlds ----
    choose = [v for v in variants if v != "everything_off"]
    union = np.zeros(len(idx), bool)
    for v in choose:
        union |= masks_by[v]
    cand = np.where(union)[0]
    t0 = time.time()
    top1 = np.zeros(len(cand))
    for a in range(0, len(cand), 2000):
        top1[a:a + 2000] = reach01(cand[a:a + 2000], Xg, Tg).mean(axis=1)
    say(f"scored {len(cand):,} candidate rows on the choosing worlds ({time.time() - t0:.0f}s)")
    pos_of = np.full(len(idx), -1, dtype=np.int64)
    pos_of[cand] = np.arange(len(cand))
    chosen = {}
    for v in choose:
        ci = pos_of[np.where(masks_by[v])[0]]
        order = ci[np.argsort(-top1[ci], kind="stable")]
        cands = cand[order[:CANDIDATES]]
        t0 = time.time()
        pick, p_any = dfs_tourney.greedy_cover(reach01(cands, Xg, Tg), max(SIZES))
        chosen[v] = {"top1": [int(cand[j]) for j in order[:TOP]],
                     "portfolio": [int(cands[j]) for j in pick], "p_any_gen": [round(float(x), 5) for x in p_any],
                     "allowed": int(masks_by[v].sum()), "best_top1_gen": round(float(top1[order[0]]), 5)}
        say(f"  {v}: {chosen[v]['allowed']:,} allowed; best top1 {top1[order[0]]:.4f}; cover {time.time() - t0:.0f}s")
    # ---- the independent worlds score what was chosen ----
    ind = {}
    for v in choose:
        out = {}
        for grp, row in (("best", chosen[v]["top1"][:1]),):
            reach, prize = per_world(row, Xf, Tf)
            out[grp] = {"top1": reach[0.01][0], "top01": reach[0.001][0], "cash": reach[cash_q][0],
                        "first": reach[0.0][0], "net": prize[0] - fee}
        for k in SIZES:
            rp, pz = per_world(chosen[v]["portfolio"][:k], Xf, Tf)
            out[f"p{k}"] = {"top1": rp[0.01].any(axis=0), "top01": rp[0.001].any(axis=0),
                            "cash": rp[cash_q].mean(axis=0), "net": pz.mean(axis=0) - fee}
        ind[v] = out
    summary = {}
    for v in choose:
        s = {"allowed": chosen[v]["allowed"], "best_top1_gen": chosen[v]["best_top1_gen"]}
        for grp, d in ind[v].items():
            s[grp] = {m: round(float(np.mean(a)), 6) for m, a in d.items()}
            if v != "conservative":
                s[grp + "_vs_conservative"] = {m: paired(a, ind["conservative"][grp][m]) for m, a in d.items()}
        port_rows = []
        for r in chosen[v]["portfolio"][:max(SIZES)]:
            port_rows.append({"lineup": [{"name": names[idx[r, 0]], "team": ents[idx[r, 0]]["team"], "slot": "CPT"}]
                              + [{"name": names[idx[r, k]], "team": ents[idx[r, k]]["team"], "slot": "FLEX"}
                                 for k in range(1, 6)]})
        s["diversity_20"] = {"concentration": dfs_portfolio.concentration(port_rows),
                             "distinct_players": len({p["name"] for r in port_rows for p in r["lineup"]})}
        s["gated_players_in_portfolio_20"] = sorted({names[j] for r in chosen[v]["portfolio"][:max(SIZES)]
                                                     for j in idx[r] if ents[j]["gated"]})
        s["best_lineup"] = [names[j] for j in idx[chosen[v]["top1"][0]]]
        s["p_any_top1_gen_20"] = chosen[v]["p_any_gen"][-1]
        # the entries themselves, captain first, so their REAL points can be read once the game is final
        s["portfolio_20"] = [[names[j] for j in idx[r]] for r in chosen[v]["portfolio"][:max(SIZES)]]
        summary[v] = s
    # ---- optimal-lineup coverage on the independent worlds: the best of ALL legal lineups (DraftKings' rules
    # only, every gated player) against the best each variant may enter ----
    t0 = time.time()
    K = 400
    cover = {v: 0 for v in variants}
    lost = {v: [] for v in variants}
    violates = {r: 0 for r in RULES}
    needs_gated = 0
    opt_hold = {}
    mk_all = rule_masks(ents, idx, set(names))
    base_rows = np.isin(np.array(names), list(base))[idx].all(axis=1)
    Wt = np.ascontiguousarray(W.T)
    NC = min(COVER_N, Xf.shape[1])         # the first COVER_N independent worlds (a share to ~0.5%)
    for a in range(0, NC, 50):
        S = Xf[:, a:a + 50].T @ Wt                                 # (c, L)
        top = np.argpartition(-S, K, axis=1)[:, :K]
        for j in range(S.shape[0]):
            tj = top[j][np.argsort(-S[j, top[j]], kind="stable")]
            g = S[j, tj[0]]
            best_rows = tj[S[j, tj] >= g - 1e-4]
            r0 = tj[0]
            for r in RULES:
                violates[r] += int(not mk_all[r][r0])
            needs_gated += int(not base_rows[r0])
            for jj in idx[r0]:
                opt_hold[names[jj]] = opt_hold.get(names[jj], 0) + 1
            for v in variants:
                m = masks_by[v]
                if m[best_rows].any():
                    cover[v] += 1
                    lost[v].append(0.0)
                else:
                    inm = tj[m[tj]]
                    lost[v].append(float(g - S[j, inm[0]]) if len(inm) else float("nan"))
    nf = NC
    coverage = {v: {"share": round(cover[v] / nf, 5),
                    "mean_points_lost": round(float(np.nanmean(lost[v])), 3),
                    "lost_beyond_top400": int(np.isnan(lost[v]).sum())} for v in variants}
    say(f"coverage over {nf:,} worlds ({time.time() - t0:.0f}s)")
    # ---- runtime and memory per mode: production's scoring pass alone (run_vs_field), a fresh process each ----
    timing = {}
    tmp = os.path.join(args.inputs, "timing.npz")
    pickle.dump(dfs_tourney.payout_grid(C, contest["payouts"], fee, int(contest["places_paid"])),
                open(tmp + ".grid", "wb"))
    for v in ("conservative", "adaptive", "unrestricted", "unrestricted_all"):
        rows_v = np.where(masks_by[v])[0]
        np.savez(tmp, Wc=W[rows_v], Wf=Wf, f=f_sub, X=np.ascontiguousarray(Xg[:, :args.timing_worlds]))
        got = subprocess.run([sys.executable, os.path.abspath(__file__), "--timing", tmp, "--workers",
                              str(args.workers)], capture_output=True, text=True, cwd=_ROOT)
        try:
            timing[v] = json.loads(got.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            timing[v] = {"error": (got.stderr or "")[-400:]}
        timing[v]["rows"] = int(len(rows_v))
        timing[v]["worlds"] = args.timing_worlds
        say(f"  timing {v}: {timing[v]}")
    for p in (tmp, tmp + ".grid"):
        if os.path.exists(p):
            os.remove(p)
    out = {"meta": meta, "slate": {"dg": meta["dg"], "contest": meta["contest"], "teams": board["slate"]["teams"],
                                   "week": board["slate"]["week"],
                                   "board_built_ts": board["built_ts"], "lock_ts": meta["lock_ts"]},
           "inputs_sha256_16": shas, "worlds": {"total": int(N), "choosing": N_GEN, "independent": int(N - N_GEN),
                                                 "model": meta["model"], "seed": meta["seed"]},
           "convention": "per-world field thresholds (dfs_evaluate._cpu_chunks), ties not split; the greedy cover "
                         "(dfs_tourney.greedy_cover) on per-world top-1% indicators; EV and net are per entry, "
                         "experimental (the evaluator's)",
           "check": check, "players": [{k: e[k] for k in ("name", "pos", "team", "salary", "cpt_salary", "proj",
                                                         "depth", "field", "gated")} for e in ents],
           "eligibility": {m: {"admitted": sorted(verdict[m]["admitted"]), "flags": verdict[m]["flags"],
                               "info": verdict[m]["info"], "note": verdict[m]["note"]} for m in E.MODES},
           "variants": {v: {"ours": sorted(variants[v][0]), "lifted": list(variants[v][1]),
                            "role_names": sorted(variants[v][2] or [])} for v in variants},
           "summary": summary, "coverage": coverage,
           "optimum_violates_rule_share": {r: round(violates[r] / nf, 5) for r in RULES},
           "optimum_needs_gated_player_share": round(needs_gated / nf, 5),
           "optimum_holds_player_share": {k: round(v / nf, 5) for k, v in sorted(opt_hold.items(),
                                                                                key=lambda x: -x[1])},
           "timing": timing, "log": log, "seconds": round(time.time() - t_start, 1)}
    dest = args.out or os.path.join(os.path.dirname(OUT), f"compare_{meta['dg']}.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True, default=float)
    print("wrote", dest)


if __name__ == "__main__":
    main()
