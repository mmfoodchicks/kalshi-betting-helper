"""Stage 2F: the two simulators on the CURRENT slate, side by side.

The blind 2025 validation (Stage 2E part two) says which model describes
a season better. This says what changing the model would do to the board
the owner actually reads: the two probe lineups, the candidate
population, where the big stacks stand, and how much of any of it
survives the uncertainty in the fitted parameters.

Nothing here decides anything on its own -- a model that loses the blind
validation does not get promoted because it produces prettier lineups
here, and the report says so.

    python3 -m research.stage2f <dir with ents.json> <dir with the cached Sleeper feeds>
"""
import collections
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
REPORTS = os.path.join(ROOT, "research", "reports")
BASE_SEED = 20260912
WORLDS = 20000
FIELD_N = 150_000
CAND_N = 60_000
DRAW_WORLDS = 6000        # the parameter-uncertainty draws rebuild a lighter board
DRAW_FIELD = 60_000
DRAW_CAND = 20_000
SEASON, WEEK = "2026", 1
DRAFT_GROUP = 151307        # the Sunday main slate the permanent probes belong to


def _probes():
    """The owner's two entered lineups, resolved from the ONE place they are
    defined. This module used to carry its own nine-name literals, and they
    were a different pair of lineups: four of nine names wrong in L1 and
    three of nine in L2. Stage 2F then reported those substitutes as the
    owner's, which is how "L1 falls to 2,182nd under the constrained model"
    came to be said about a lineup the owner never entered. A copied fixture
    cannot be kept in step, so there is no copy any more."""
    import dfs_tourney as T
    pr = T.PROBES.get(("nfl", DRAFT_GROUP)) or []
    if len(pr) != 2:
        raise RuntimeError(f"expected two canonical probes for {DRAFT_GROUP}, found {len(pr)}")
    return tuple(list(x) for x in pr)


PROBES = _probes()


def _commit():
    """Kept for the artifacts that only record a hash; `_prov()` is what new
    metadata blocks should carry. The commit alone was never enough: it names
    the tree the research code was sitting on, not the code that ran."""
    from research import provenance
    return provenance.commit()


def _prov(**extra):
    """commit, dirty flag, and a hash over the source that actually ran."""
    from research import provenance
    return provenance.stamp(**extra)


def pools(feed_dir, models=("legacy", "constrained"), n=WORLDS, seed=BASE_SEED, log=print):
    """Both simulators' pools for the same week, off the same cached feeds."""
    import nfl_dfs_sim as S
    raw = json.load(open(os.path.join(feed_dir, f"proj_{SEASON}_{WEEK}.json")))
    dfn = json.load(open(os.path.join(feed_dir, f"proj_{SEASON}_{WEEK}_def.json")))
    kck = json.load(open(os.path.join(feed_dir, f"proj_{SEASON}_{WEEK}_k.json")))
    S._get = lambda url: (dfn if "DEF" in url else kck if "position[]=K" in url else raw)
    out = {}
    for m in models:
        S._cache.clear()
        t0 = time.time()
        out[m] = S.player_pool(WEEK, n=n, season=SEASON, model=m, seed=(seed if m != "legacy" else None))
        log(f"[2F] {m}: {len(out[m])} players, {time.time() - t0:.0f}s")
    return out


def entries(ents, pool):
    """The slate's entries with this pool's point arrays; players the pool
    does not carry are dropped from BOTH sides by the caller."""
    import nfl_dfs as D
    nidx, norm = D._norm_index(pool)
    out = []
    for e in ents:
        sim = D._pool_match(pool, e["name"], e["pos"], e.get("team"), nidx, norm)
        if not (sim and sim.get("arr")):
            continue
        out.append(dict(e, arr=sim["arr"], proj=round(float(sim["proj"]), 1), rec_tgt=float(sim.get("rec_tgt") or 0.0)))
    return out


def board(ents, log=print, seed=BASE_SEED, field_n=FIELD_N, cand_n=CAND_N, worlds=None):
    """Field, candidates and scores for one pool: the board's own pipeline."""
    import dfs_tourney as T
    P = len(ents)
    N = min(len(e["arr"]) for e in ents)
    if worlds:
        N = min(N, int(worlds))
    X = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    rng = np.random.default_rng(seed)
    beta, kappa, max_own, mean_sal, coll, top_share = T.calibrate_field(ents, rng, n=20000)
    rep = {}
    idx_f = T.classic_sample(ents, int(field_n), np.random.default_rng(seed + 1), beta, kappa, report=rep)
    T.check_completion(rep, "2F field")
    rep = {}
    fo = np.asarray([bool(e.get("_field_only")) for e in ents])
    idx_c = T.classic_sample(ents, int(cand_n), np.random.default_rng(seed + 2), beta, kappa,
                             rules=True, exclude=fo, report=rep)
    T.check_completion(rep, "2F candidates")
    gen, ev = T.world_split(N, N // 3)
    keep = T._solver_pool(X, [e["pos"] for e in ents], gen)
    sal = np.asarray([e["salary"] for e in ents])
    opt_L, _ = T.optimal_lineups(X[keep][:, gen.start:gen.stop], sal[keep], [ents[i]["pos"] for i in keep], chunk=64)
    opt = [tuple(int(keep[i]) for i in L) for L in opt_L if L]
    cnt = collections.Counter(tuple(sorted(L)) for L in opt)
    idx_o = T._slot_rows(sorted({tuple(sorted(L)) for L in opt}), [e["pos"] for e in ents])
    idx_all = np.concatenate([idx_o, idx_c], axis=0)
    allowed = T.classic_allowed(idx_all, ents)
    Wc = T.lineup_matrix(idx_all.astype(np.int64), P)
    Wf = T.lineup_matrix(idx_f.astype(np.int64), P)
    wf = np.full(len(idx_f), 1.0 / len(idx_f))
    grid = T.payout_grid(832000, [{"from": 1, "to": 1, "prize": 1e6}, {"from": 2, "to": 10, "prize": 25000.0},
                                  {"from": 11, "to": 1000, "prize": 500.0}, {"from": 1001, "to": 166400, "prize": 10.0}],
                         5.0, 166400)
    Xe = np.ascontiguousarray(X[:, ev.start:ev.stop])
    res, optc, n = T.run_vs_field(Wc, Wf, wf, Xe, [grid], chunk=400)
    return {"ents": ents, "X": X, "Xe": Xe, "idx": idx_all, "allowed": allowed, "res": res[0], "grid": grid,
            "idx_f": idx_f, "Wf": Wf, "wf": wf, "beta": beta, "kappa": kappa, "opt_count": len(cnt),
            "n_worlds": int(N), "eval_worlds": int(Xe.shape[1])}


def probe_rows(b, names_list):
    import dfs_tourney as T
    out = []
    for names in names_list:
        ix, miss = T.probe_index(names, b["ents"])
        if ix is None:
            out.append({"names": names, "available": False, "missing": miss})
            continue
        row = np.asarray([ix], dtype=np.int64)
        Wk = T.lineup_matrix(row, len(b["ents"]))
        res, _, _ = T.run_vs_field(Wk, b["Wf"], b["wf"], b["Xe"], [b["grid"]], chunk=400)
        r = res[0]
        s = b["Xe"][ix].sum(axis=0)
        q = np.quantile(s, [0.1, 0.5, 0.9, 0.99])
        rank1 = int((b["res"]["top1"][b["allowed"]] > r["top1"][0]).sum()) + 1
        rank01 = int((b["res"]["top01"][b["allowed"]] > r["top01"][0]).sum()) + 1
        out.append({"names": names, "available": True, "top1_pct": 100.0 * float(r["top1"][0]),
                    "top01_pct": 100.0 * float(r["top01"][0]), "win_sole_pct": 100.0 * float(r["win_sole"][0]),
                    "ev": float(r["ev"][0]), "ev_notie": float(r["ev_notie"][0]),
                    "mean": float(s.mean()), "p10": float(q[0]), "median": float(q[1]), "p90": float(q[2]), "p99": float(q[3]),
                    "rank_top1": rank1, "rank_top01": rank01, "of": int(b["allowed"].sum())})
    return out


def stack_standing(b, top=200):
    """Where the big stacks sit in each model: the share of the strongest
    candidates that take four, five or six players from one game."""
    import dfs_tourney as T
    ents = b["ents"]
    teams = sorted({p.get("team") or "" for p in ents} | {p.get("opp") or "" for p in ents})
    tc = {t: i for i, t in enumerate(teams)}
    team = np.asarray([tc[p.get("team") or ""] for p in ents])
    opp = np.asarray([tc[p.get("opp") or ""] for p in ents])
    game = np.minimum(team, opp) * len(teams) + np.maximum(team, opp)
    idx = b["idx"][b["allowed"]]
    r = {k: v[b["allowed"]] for k, v in b["res"].items()}
    order = np.argsort(-r["top1"])[:top]
    G = game[idx[order]]
    per = np.max(np.stack([(G == G[:, [k]]).sum(axis=1) for k in range(9)], axis=1), axis=1)
    dist = {int(k): float((per == k).mean()) for k in range(2, 10) if (per == k).any()}
    T_ = T  # keep the import used
    del T_
    return {"top": top, "per_game_dist": dist, "mean_per_game": float(per.mean()),
            "share_5plus": float((per >= 5).mean()), "share_6plus": float((per >= 6).mean())}


def parameter_draws(feed_dir, ents_base, k=6, log=print):
    """How much of the constrained board survives the fit's own uncertainty:
    the parameters are redrawn inside their profile ranges and the probes
    and the strongest lineup are recomputed."""
    import nfl_dfs_csim as C
    import nfl_dfs_sim as S
    prof = {}
    try:
        st = json.load(open(os.path.join(DATA, "stage2e_fit_stats.json")))
        prof = st.get("profile") or {}
    except Exception:
        return {"note": "no fit profile on file; parameter uncertainty not drawn"}
    rng = np.random.default_rng(BASE_SEED + 77)
    base = dict(C.PARAMS["params"])
    # The profile step was too coarse to resolve most intervals (the report
    # says so), so a draw's half-width is the wider of the profile's own
    # half-width and the distance the second fit moved that parameter under
    # different common random numbers -- a measured uncertainty either way.
    alt = (st.get("alt_fit") or {}).get("diff") or {}
    rows = []
    for d in range(k):
        p = dict(base)
        for name, rg in prof.items():
            if name.startswith("_") or name not in p:
                continue
            lo, hi = float(rg["lo"]), float(rg["hi"])
            half = max(0.5 * (hi - lo), abs(float(alt.get(name, 0.0))))
            p[name] = float(max(0.0, rng.uniform(base[name] - half, base[name] + half)))
        C.PARAMS = {"params": p, "fitted": True, "hash": C.params_hash(p), "meta": C.PARAMS.get("meta", {})}
        S._cache.clear()
        pool = pools(feed_dir, models=("constrained",), n=DRAW_WORLDS, seed=BASE_SEED + 100 + d, log=lambda *_: None)["constrained"]
        e = entries(ents_base, pool)
        b = board(e, log=lambda *_: None, seed=BASE_SEED + 200 + d,
                  field_n=DRAW_FIELD, cand_n=DRAW_CAND, worlds=DRAW_WORLDS)
        pr = probe_rows(b, PROBES)
        best = int(np.argmax(b["res"]["top1"][b["allowed"]]))
        names = [b["ents"][i]["name"] for i in b["idx"][b["allowed"]][best]]
        rows.append({"draw": d, "params": {kk: round(vv, 4) for kk, vv in p.items()},
                     "probe_top1": [round(x.get("top1_pct", float("nan")), 3) for x in pr],
                     "probe_rank": [x.get("rank_top1") for x in pr], "best": names})
        log(f"[2F] parameter draw {d}: probe top 1% {[round(x.get('top1_pct', float('nan')), 2) for x in pr]}")
    C.PARAMS = C._load_params()
    return rows


def run(slate_dir, feed_dir, log=print):
    t0 = time.time()
    ents_base = json.load(open(os.path.join(slate_dir, "ents.json")))
    pl = pools(feed_dir, log=log)
    common = set(pl["legacy"]) & set(pl["constrained"])
    log(f"[2F] {len(common)} players in both pools")
    out = {}
    for m in ("legacy", "constrained"):
        e = entries(ents_base, pl[m])
        log(f"[2F] {m}: {len(e)} slate entries; building the board...")
        b = board(e, log=log, seed=BASE_SEED)
        r = {k: v[b["allowed"]] for k, v in b["res"].items()}
        idx = b["idx"][b["allowed"]]
        best = int(np.argmax(r["top1"]))
        out[m] = {"entries": len(e), "candidates": int(len(b["idx"])), "allowed": int(b["allowed"].sum()),
                  "distinct_optimal": b["opt_count"], "beta": b["beta"], "kappa": b["kappa"],
                  "worlds": b["n_worlds"], "eval_worlds": b["eval_worlds"],
                  "best": {"names": [b["ents"][i]["name"] for i in idx[best]],
                           "top1_pct": 100.0 * float(r["top1"][best]), "top01_pct": 100.0 * float(r["top01"][best]),
                           "win_sole_pct": 100.0 * float(r["win_sole"][best]), "ev": float(r["ev"][best])},
                  "top1_p90": float(np.quantile(r["top1"], 0.9)), "top1_max": float(r["top1"].max()),
                  "probes": probe_rows(b, PROBES), "stacks": stack_standing(b),
                  "mean_proj_of_top50": float(np.mean([sum(b["ents"][i]["proj"] for i in idx[j])
                                                       for j in np.argsort(-r["top1"])[:50]]))}
    log("[2F] parameter-uncertainty draws...")
    draws = parameter_draws(feed_dir, ents_base, log=log)
    st = {"meta": {"stage": "2F", "commit": _commit(), "provenance": _prov(seed=BASE_SEED, model="legacy + constrained", data_split=f"{SEASON} week {WEEK}, current slate"), "base_seed": BASE_SEED, "season": SEASON, "week": WEEK,
                   "worlds": WORLDS, "field": FIELD_N, "candidates_drawn": CAND_N,
                   "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "seconds": round(time.time() - t0, 1)},
          "models": out, "parameter_draws": draws}
    json.dump(st, open(os.path.join(DATA, "stage2f_stats.json"), "w"), indent=1, sort_keys=True)
    write_report(st)
    return st


def write_report(st):
    m = st["meta"]
    L = ["# Stage 2F: the two simulators on the current slate\n",
         f"Commit {m['commit']}, base seed {m['base_seed']}, {m['season']} week {m['week']}, {m['worlds']:,} worlds a game, "
         f"field {m['field']:,}, {m['candidates_drawn']:,} candidate draws. Built {m['built']} ({m['seconds']}s). "
         "Both models read the same slate, the same salaries and the same field model; only the point arrays differ.\n",
         "## The board each model builds\n",
         "| | legacy | constrained |\n|---|---|---|"]
    a, b = st["models"]["legacy"], st["models"]["constrained"]
    for label, key in (("slate entries", "entries"), ("candidates", "candidates"), ("allowed", "allowed"),
                       ("distinct hindsight-optimal lineups", "distinct_optimal")):
        L.append(f"| {label} | {a[key]:,} | {b[key]:,} |")
    L.append(f"| strongest candidate, top 1% | {a['best']['top1_pct']:.2f}% | {b['best']['top1_pct']:.2f}% |")
    L.append(f"| strongest candidate, top 0.1% | {a['best']['top01_pct']:.3f}% | {b['best']['top01_pct']:.3f}% |")
    L.append(f"| strongest candidate, outright win | {a['best']['win_sole_pct']:.4f}% | {b['best']['win_sole_pct']:.4f}% |")
    L.append(f"| mean projection of the top fifty | {a['mean_proj_of_top50']:.1f} | {b['mean_proj_of_top50']:.1f} |")
    L.append(f"| players from one game, mean of the top 200 | {a['stacks']['mean_per_game']:.2f} | {b['stacks']['mean_per_game']:.2f} |")
    L.append(f"| share of the top 200 with five from one game | {a['stacks']['share_5plus']:.3f} | {b['stacks']['share_5plus']:.3f} |")
    L.append(f"| share with six | {a['stacks']['share_6plus']:.3f} | {b['stacks']['share_6plus']:.3f} |")
    L.append(f"\nStrongest under legacy: {', '.join(a['best']['names'])}.\n\nStrongest under constrained: {', '.join(b['best']['names'])}.\n")
    L.append("## The two probe lineups under each model\n")
    L.append("| Probe | Model | top 1% | rank | top 0.1% | rank | outright win | expected payout | mean | p90 | p99 |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for i in range(len(PROBES)):
        for name, mm in (("legacy", a), ("constrained", b)):
            p = mm["probes"][i]
            if not p.get("available"):
                L.append(f"| L{i + 1} | {name} | not available ({p.get('missing')}) | | | | | | | | |")
                continue
            L.append(f"| L{i + 1} | {name} | {p['top1_pct']:.2f}% | #{p['rank_top1']} | {p['top01_pct']:.3f}% | #{p['rank_top01']} | "
                     f"{p['win_sole_pct']:.4f}% | ${p['ev']:.2f} | {p['mean']:.1f} | {p['p90']:.1f} | {p['p99']:.1f} |")
    L.append("\n## Parameter uncertainty\n")
    d = st["parameter_draws"]
    if isinstance(d, dict):
        L.append(d.get("note", ""))
    else:
        L.append(f"Each draw resamples every fitted parameter uniformly inside the wider of its profile half-width and the distance the second fit moved it under "
                 f"different common random numbers (the profile step was too coarse to resolve most intervals; Stage 2E's report says so), then rebuilds the whole board "
                 f"on a lighter grid ({DRAW_WORLDS:,} worlds, {DRAW_FIELD:,} field, {DRAW_CAND:,} candidate draws). The top-1% figure is the comparable number across "
                 "draws; the rank is against that draw's own smaller candidate pool, so it is not comparable with the full board's rank.\n")
        L.append("| Draw | probe L1 top 1% (rank) | probe L2 top 1% (rank) | strongest lineup |\n|---|---|---|---|")
        for r in d:
            L.append(f"| {r['draw']} | {r['probe_top1'][0]}% (#{r['probe_rank'][0]}) | {r['probe_top1'][1]}% (#{r['probe_rank'][1]}) | {', '.join(r['best'][:4])}... |")
    L.append("\nThis report does not promote anything. The blind 2025 validation decides which model is better; this only says what the change would look like on the board the owner reads.\n")
    with open(os.path.join(REPORTS, "stage2f_report.md"), "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    sd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "slate")
    fd = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DATA, "feeds")
    run(sd, fd)
    print("stage 2F written")
