"""S5.2-S5.9 -- what each owner rule actually costs, cross-fit.

The question is NOT "which rule rejects the most lineups". S5.1 already shows
why that question is empty: on DEN @ KC the no-K/DST-captain rule hits 131,321
legal lineups and admits ZERO if you switch it off alone, because every one of
those lineups also fails the captain salary floor. Counts are not cost.

So every rule is measured three ways (S5.2):

  all-hits              every legal lineup the rule rejects
  exclusive-hits        lineups it rejects that NO other rule rejects
  marginal-admissions   lineups that become allowed if ONLY that rule is off

For a SINGLE-rule ablation the last two are the same set by definition -- a
lineup becomes allowed when R is switched off exactly when R was its only
violation. They are reported separately anyway because they stop being equal
the moment two rules come off together (S5.9), and because a reader comparing
them is entitled to see the identity rather than take it on trust.

Then the part counts cannot answer (S5.7): for each rule with a non-empty
marginal set, shortlist and greedily build a 20-entry portfolio over
allowed + marginal_R on the SELECTION fold, and grade it on the HELD-OUT fold
against the baseline portfolio built the same way over allowed alone.

What a zero here does and does not mean, because an earlier revision of this
docstring overstated it: a rule that rejects 400,000 lineups and never
contributes one to the portfolio has ZERO MARGINAL ADMISSIONS UNDER THE CURRENT
CONJUNCTION OF RULES. It is not "inert", "redundant" or "strategically free" --
CPT-POS has zero marginal admissions alone and a +0.038 effect the moment
CPT-SALARY comes off beside it (research/s5_pairs.py). A zero means the rule's
effect is currently MASKED by another rule, and masking is a property of the
configuration, not of the rule.

Two metric families, because the field model is a placeholder (S5.5):

  FOOTBALL-ONLY   held-out mean, p90, p95, p99, p99.9 of the lineup's DK score.
                  No field model, no contest, no money.
  CONTEST         held-out Top 1% and Top 0.1% under the CURRENT placeholder
                  ownership model. MODEL-CONDITIONAL, labelled as such.

No EV, ROI or payout column is computed anywhere in this module. The money gate
is shut and a shut gate that still feeds a decision is not shut.

Nothing here is imported by the app, the PC worker or the guard suite's
production paths.
"""
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")

from research import s4_crossfit as X4      # noqa: E402  (path first)
from research import s5_rules as R5         # noqa: E402

WORLDS = 8000                 # 4,000 per fold, the S4 contract
CANDIDATES = 1200             # production's shortlist size, as in S4
PORT_K = 20
SEEDS = (20260912, 771033)
DGS = (153086, 153085)
FRONTIER_N = 100              # forbidden lineups carried to held-out grading
SCREEN_CHUNK = 40000          # lineups per football-metric chunk (S5.4)
PCTS = (90.0, 95.0, 99.0, 99.9)


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


# ---- S5.5 football-only metrics -------------------------------------------
def football(Wrows, X, worlds):
    """Held-out DK-score distribution for a few lineups. No field, no contest.

    This is the half of the evidence that cannot be moved by the uncalibrated
    ownership model, which is why a rule recommendation has to be compatible
    with it even though it can never decide one on its own.
    """
    S = (Wrows @ X[:, worlds]).astype(np.float64)          # (rows, worlds)
    out = {"mean": np.round(S.mean(axis=1), 4)}
    for q in PCTS:
        out[f"p{q:g}".replace(".", "_")] = np.round(np.percentile(S, q, axis=1), 4)
    return out


def _fb_row(fb, i):
    return {k: float(v[i]) for k, v in fb.items()}


# ---- S5.4 chunked screening over the WHOLE legal universe ------------------
def screen(T, W, X, f, grid, sel, log=print):
    """Selection-fold Top-1% for EVERY legal lineup, as scalars.

    One pass, O(lineups) memory. The tempting (lineups x worlds) matrix is
    777,056 x 4,000 float32 = 12.4 GB on one board and it is not needed: the
    screen only wants a scalar per lineup. An earlier stage asked for 43 GiB
    the same way and that is the reason this function exists rather than a
    convenient one-liner.
    """
    t0 = time.time()
    rows = np.arange(W.shape[0], dtype=np.int64)
    means = X4._field_pass(T, W, X, f, None, sel, want_all=rows, grid=grid,
                           convs=("optimistic",), qnames=("top1",))
    log(f"[S5X]   screened {W.shape[0]:,} lineups on {len(sel):,} selection worlds "
        f"in {time.time() - t0:.0f}s")
    return means[("optimistic", "top1")]


def top_of(scores, pool, n):
    """The n highest-scoring members of `pool` (row indices into the universe)."""
    if len(pool) == 0:
        return np.empty(0, dtype=np.int64)
    take = min(int(n), len(pool))
    return pool[np.argsort(-scores[pool])[:take]]


def greedy_on(T, grid, st, lv, rows_slice, k=PORT_K):
    """Greedy cover on the SELECTION fold, production's objective (Top 1%,
    optimistic convention -- what production actually serves)."""
    P = X4.topq(grid, "top1", st[rows_slice], lv[rows_slice], "optimistic")
    chosen, p_any = T.greedy_cover(P, k)
    return np.asarray(chosen, dtype=np.int64), p_any


# ---- one cross-fit cell ----------------------------------------------------
def one_direction(T, ents, idx, W, X, f, masks, grids, C, sel, sco, log=print):
    """Select on `sel`, grade on `sco`. Never reads `sco` before selection is
    finished, which is the property S4 established and S5 inherits."""
    n = W.shape[0]
    allowed = R5.shadow_allowed(masks)
    ok = np.where(allowed)[0]
    forb = np.where(~allowed)[0]
    out = {"selection_worlds": [int(sel[0]), int(sel[-1])],
           "scoring_worlds": [int(sco[0]), int(sco[-1])],
           "folds_disjoint": bool(not (set(sel.tolist()) & set(sco.tolist()))),
           "n_selection": int(len(sel)), "n_scoring": int(len(sco)),
           "legal": int(n), "allowed": int(len(ok)), "forbidden": int(len(forb)),
           "screen_chunk_lineups": int(SCREEN_CHUNK),
           "shortlist_size": int(CANDIDATES), "retained": {}}

    # ---- S5.4: one screening pass, scalars only -----------------------------
    sc = screen(T, W, X, f, grids[PORT_K], sel, log=log)

    # ---- S5.4 retention, per universe we will actually build a portfolio over
    universes = {"baseline": ok}
    marginal = {}
    for rid in R5.RULE_IDS:
        m = R5.shadow_allowed(masks, (rid,)) & ~allowed
        marginal[rid] = np.where(m)[0]
        if len(marginal[rid]):
            universes[f"ablate:{rid}"] = np.concatenate([ok, marginal[rid]])
    universes["full_legal"] = np.arange(n, dtype=np.int64)     # S5.8

    shortlists = {k: top_of(sc, pool, CANDIDATES) for k, pool in universes.items()}
    for k, v in shortlists.items():
        out["retained"][k] = {"universe": int(len(universes[k])), "shortlist": int(len(v))}

    # A zero ablation delta has two very different causes and the artifact has to
    # tell them apart: the rule discards nothing the model rates highly, or the
    # 1,200-lineup shortlist cut its admissions off at the knee. So record where
    # each rule's BEST marginal lineup actually ranks inside its own union. A
    # best rank of 40,000 says the zero is robust; a best rank of 1,201 says the
    # zero is an artifact of the shortlist size and would move if it grew.
    base_sorted = np.sort(sc[ok])[::-1]
    out["shortlist_cut_top1"] = round(
        float(base_sorted[min(CANDIDATES, len(base_sorted)) - 1]), 6)
    out["marginal_rank"] = {}
    for rid in R5.RULE_IDS:
        mg = marginal[rid]
        if not len(mg):
            out["marginal_rank"][rid] = {"marginal": 0, "best_rank_in_union": None,
                                         "best_top1": None, "inside_shortlist": 0}
            continue
        uni = universes[f"ablate:{rid}"]
        order = uni[np.argsort(-sc[uni])]
        inmg = np.isin(order, mg)
        out["marginal_rank"][rid] = {
            "marginal": int(len(mg)),
            "best_rank_in_union": int(np.argmax(inmg)) + 1,
            "best_top1": round(float(sc[mg].max()), 6),
            "inside_shortlist": int(inmg[:CANDIDATES].sum())}

    # the frontier sets S5.6 reports on, screened the same way
    frontier = {"forbidden_overall": top_of(sc, forb, FRONTIER_N)}
    for rid in R5.RULE_IDS:
        if len(marginal[rid]):
            frontier[f"marginal:{rid}"] = top_of(sc, marginal[rid], 20)

    # ---- selection-fold matrix for the shortlists only, then greedy ---------
    keys = list(shortlists)
    rows = np.concatenate([shortlists[k] for k in keys]) if keys else np.empty(0, np.int64)
    bounds, pos = {}, 0
    for k in keys:
        bounds[k] = slice(pos, pos + len(shortlists[k]))
        pos += len(shortlists[k])
    t0 = time.time()
    st_sel, lv_sel = X4._field_pass(T, W, X, f, rows, sel)
    log(f"[S5X]   selection-fold shortlist matrix {len(rows):,} rows "
        f"{time.time() - t0:.0f}s")
    ports, insample = {}, {}
    for k in keys:
        ch, p_any = greedy_on(T, grids[PORT_K], st_sel, lv_sel, bounds[k])
        ports[k] = shortlists[k][ch]
        insample[k] = round(float(p_any[PORT_K - 1]), 6)
    del st_sel, lv_sel

    # ---- held-out grading: portfolios + frontier, one pass ------------------
    grade_keys = list(ports)
    grade_rows = [ports[k] for k in grade_keys]
    fkeys = list(frontier)
    grade_rows += [frontier[k] for k in fkeys]
    allrows = np.concatenate(grade_rows)
    gb, pos = {}, 0
    for k in grade_keys:
        gb[k] = np.arange(pos, pos + PORT_K)
        pos += PORT_K
    fb_bounds = {}
    for k in fkeys:
        fb_bounds[k] = np.arange(pos, pos + len(frontier[k]))
        pos += len(frontier[k])
    t0 = time.time()
    st, lv = X4._field_pass(T, W, X, f, allrows, sco)
    log(f"[S5X]   held-out grade pass {len(allrows):,} rows {time.time() - t0:.0f}s")
    P1 = X4.topq(grids[PORT_K], "top1", st, lv, "optimistic")
    P01 = X4.topq(grids[PORT_K], "top01", st, lv, "optimistic")
    P1p = X4.topq(grids[PORT_K], "top1", st, lv, "pessimistic")
    P01p = X4.topq(grids[PORT_K], "top01", st, lv, "pessimistic")
    fb = football(W[allrows], X, sco)

    def cov(P, order):
        return round(float(np.max(P[order], axis=0).mean()), 6)

    base = gb["baseline"]
    out["portfolios"] = {}
    for k in grade_keys:
        o = gb[k]
        row = {"in_sample_top1": insample[k],
               "held_out": {"optimistic": {"top1": cov(P1, o), "top01": cov(P01, o)},
                            "pessimistic": {"top1": cov(P1p, o), "top01": cov(P01p, o)}},
               "overlap_with_baseline": int(len(set(ports[k].tolist())
                                                & set(ports["baseline"].tolist()))),
               "entries": [int(x) for x in ports[k]]}
        if k != "baseline":
            newly = universes[k]
            adm = set(np.setdiff1d(newly, ok).tolist())
            picked = [int(x) for x in ports[k] if int(x) in adm]
            row["newly_admitted_selected"] = len(picked)
            row["newly_admitted_rows"] = picked
            row["paired"] = {}
            for cname, Pa, Pb in (("optimistic_top1", P1, P1), ("optimistic_top01", P01, P01),
                                  ("pessimistic_top1", P1p, P1p),
                                  ("pessimistic_top01", P01p, P01p)):
                row["paired"][cname] = X4.paired_bootstrap(Pa, Pb, base, o, PORT_K)
            row["football_selected"] = {
                "mean": round(float(np.mean([fb["mean"][i] for i in o])), 4),
                "p99": round(float(np.mean([fb["p99"][i] for i in o])), 4)}
        else:
            row["football_selected"] = {
                "mean": round(float(np.mean([fb["mean"][i] for i in o])), 4),
                "p99": round(float(np.mean([fb["p99"][i] for i in o])), 4)}
        out["portfolios"][k] = row

    # ---- S5.6 the frontier, lineup by lineup --------------------------------
    def describe(row, gi):
        pl = [ents[int(j)] for j in idx[row]]
        cap = pl[0]
        sal = int(cap["cpt_salary"]) + sum(int(p["salary"]) for p in pl[1:])
        teams = {}
        for p in pl:
            teams[p.get("team") or "?"] = teams.get(p.get("team") or "?", 0) + 1
        return {"row": int(row), "captain": cap["name"], "captain_pos": cap.get("pos"),
                "captain_team": cap.get("team"),
                "captain_salary": int(cap["cpt_salary"]),
                "flex": sorted(p["name"] for p in pl[1:]),
                "salary_used": sal, "salary_left": int(50000 - sal),
                "team_split": "-".join(str(v) for v in sorted(teams.values(), reverse=True)),
                "k_count": sum(1 for p in pl if p.get("pos") == "K"),
                "dst_count": sum(1 for p in pl if p.get("pos") == "DST"),
                "punt_count": sum(1 for p in pl if int(p["salary"]) <= 2000),
                "projection": round(sum(float(p.get("proj") or 0) for p in pl[1:])
                                    + 1.5 * float(cap.get("proj") or 0), 2),
                "violates": [r for r in R5.RULE_IDS if bool(masks[r][row])],
                "selection_top1": round(float(sc[row]), 6),
                "held_out_top1": round(float(P1[gi].mean()), 6),
                "held_out_top01": round(float(P01[gi].mean()), 6),
                "football": _fb_row(fb, gi)}

    out["frontier"] = {}
    for k in fkeys:
        gi = fb_bounds[k]
        out["frontier"][k] = [describe(int(r), int(g))
                              for r, g in zip(frontier[k][:20], gi[:20])]

    # the best ALLOWED lineup held out, as the bar every forbidden one must clear
    best_allowed_rows = top_of(sc, ok, 20)
    t0 = time.time()
    st_a, lv_a = X4._field_pass(T, W, X, f, best_allowed_rows, sco)
    P1a = X4.topq(grids[PORT_K], "top1", st_a, lv_a, "optimistic")
    P01a = X4.topq(grids[PORT_K], "top01", st_a, lv_a, "optimistic")
    fba = football(W[best_allowed_rows], X, sco)
    out["best_allowed"] = {
        "held_out_top1_max": round(float(P1a.mean(axis=1).max()), 6),
        "held_out_top01_max": round(float(P01a.mean(axis=1).max()), 6),
        "football_p99_max": round(float(fba["p99"].max()), 4),
        "football_mean_max": round(float(fba["mean"].max()), 4)}
    b = out["best_allowed"]
    fo = out["frontier"].get("forbidden_overall") or []
    out["frontier_beats_allowed"] = {
        "any_top1": sum(1 for r in fo if r["held_out_top1"] > b["held_out_top1_max"]),
        "any_top01": sum(1 for r in fo if r["held_out_top01"] > b["held_out_top01_max"]),
        "any_p99": sum(1 for r in fo if r["football"]["p99"] > b["football_p99_max"]),
        "of": len(fo)}
    log(f"[S5X]   best allowed held-out top1 {b['held_out_top1_max']:.5f}; "
        f"forbidden beating it: {out['frontier_beats_allowed']['any_top1']}/{len(fo)}")
    return out


def board(feed_dir, dg, seed, log=print):
    import dfs_tourney as T
    from research import sd_board
    S = sd_board.feeds(feed_dir)
    S._cache.clear()
    slate, contest = sd_board.slate_and_contest(dg)
    if not slate or not contest:
        raise SystemExit(f"draft group {dg} has no slate or contest")
    ents, _s, _r = sd_board.ents_for(slate, sd_board.WEEK, S.SD_DISCRETE,
                                     n_sims=WORLDS, seed=seed, log=log)
    idx, W, allowed = sd_board.universe(ents)
    masks = R5.reason_masks(ents, idx)
    shadow = R5.shadow_allowed(masks)
    mism = int((shadow != allowed).sum())
    if mism:
        raise SystemExit(f"shadow classifier disagrees on {mism} lineups -- STOP")
    f, beta = T.field_weights(ents, idx, cpt_mult=1.5)
    C = int(contest.get("max_entries") or contest.get("entered") or 0) or 10000
    grids, gmeta = X4.grids_by_size(contest, C)
    N = min(WORLDS, min(len(e["arr"]) for e in ents))
    Xm = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    half = N // 2
    A, B = np.arange(0, half), np.arange(half, N)
    log(f"[S5X] dg {dg} seed {seed}: {len(idx):,} legal, {int(allowed.sum()):,} allowed, "
        f"{N:,} worlds -> A[0:{half}] B[{half}:{N}], {C:,} entries, beta {beta:.4f}")
    res = {}
    for name, sel, sco in (("A_select_B_score", A, B), ("B_select_A_score", B, A)):
        log(f"[S5X]  {name}")
        res[name] = one_direction(T, ents, idx, W, Xm, f, masks, grids, C, sel, sco, log=log)
    return {"draft_group_id": int(dg), "contest": contest.get("name"), "entries": int(C),
            "legal_lineups": int(len(idx)), "allowed": int(allowed.sum()),
            "worlds": int(N), "field_beta": round(float(beta), 4), "seed": int(seed),
            "sd_engine": T.SD_ENGINE, "simulator": S.sim_stamp(N, False, S.SD_DISCRETE),
            "grid_meta": {str(k): v for k, v in gmeta.items()},
            "classifier_mismatches": mism, "directions": res}


def run(feed_dir, dgs=DGS, seeds=SEEDS, log=print):
    out = {"meta": {"stage": "S5.2-S5.9 owner-rule ablation, cross-fit",
                    "question": ("which hard owner rule, if any, excludes lineups that "
                                 "remain strong when selected on one set of worlds and "
                                 "judged on another"),
                    "boards": list(dgs), "seeds": list(seeds),
                    "worlds_total": WORLDS, "candidates": CANDIDATES,
                    "portfolio_k": PORT_K, "frontier_n": FRONTIER_N,
                    "objective": "top1 (production), optimistic convention for selection",
                    "money": ("CLOSED -- no EV, ROI or payout value is computed in this "
                              "module, so none can reach a recommendation"),
                    "metric_families": {
                        "football_only": "held-out DK score mean and p90/p95/p99/p99.9; "
                                         "no field model",
                        "contest": "held-out Top 1% / Top 0.1% under the PLACEHOLDER "
                                   "ownership model -- MODEL-CONDITIONAL"},
                    "exclusive_equals_marginal": (
                        "for a SINGLE-rule ablation these are the same set by definition: "
                        "a lineup is admitted by switching off R exactly when R was its "
                        "only violation. Both are reported because they separate as soon "
                        "as two rules come off together."),
                    "provenance": _prov(worlds=WORLDS, model="legacy-latent-discrete",
                                        seed={"boards": list(seeds),
                                              "bootstrap": X4.BOOT_SEED})},
           "boards": {}}
    for dg in dgs:
        out["boards"][str(dg)] = {}
        for sd in seeds:
            out["boards"][str(dg)][str(sd)] = board(feed_dir, dg, sd, log=log)
            with open(os.path.join(DATA, "s5_ablate.json"), "w") as fh:
                json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "feeds")
    dl = tuple(int(x) for x in sys.argv[2:]) or DGS
    run(fd, dl)
    print("S5 ablation written")
