"""S4: does selecting a Showdown portfolio for Top 0.1% beat selecting for Top 1%?

Answered out of sample, because the production engine answers it in sample. A
served board scores every lineup over all N worlds, shortlists the 1,200 best by
Top 1% over those worlds, greedily covers those worlds, and reports the greedy's
own coverage over them. Every portfolio coverage figure on a served board is
therefore in-sample, and the in-sample preview of this very question is loud: the
best lineup BY Top 0.1% scores 2.476% on that metric where the best lineup by
Top 1% scores 2.012%. That gap is exactly what a leaked evaluation would
manufacture, so it is worth nothing until the worlds that chose a portfolio are
forbidden from grading it.

DESIGN

  folds          one simulated pool, first half = A, second half = B, disjoint
  both ways      select on A score on B, select on B score on A, reported apart
  shortlist      FOLLOWS THE OBJECTIVE. The Top-1% experiment shortlists by
                 Top 1% on its selection fold; the Top-0.1% experiment
                 shortlists by Top 0.1% on its selection fold. Running both
                 through a Top-1% shortlist would hand the tail objective a
                 candidate set preselected for its opponent, which could
                 suppress the very structures the experiment exists to find.
  universe       candidate ENUMERATION is world-independent (proved by guard:
                 rewriting every simulated score leaves the legal universe
                 byte-identical), so only the shortlist and the greedy need a
                 fold.
  opponents      a submission of exactly k entries faces C - k public opponents.
                 The grid for size k is built at C - k + 1 entries so its
                 internal count is exactly C - k, and the qualifying place
                 counts are asserted unchanged. The final 20-entry portfolio
                 uses C - 20 throughout selection AND held-out scoring, so the
                 probability matrix does not shift as the greedy grows.
  ties           evaluated under both EXACT endpoints of the tie convention --
                 optimistic (production: only mass strictly above counts) and
                 pessimistic (every tied opponent finishes ahead) -- because a
                 conclusion that survives both cannot be reversed by the
                 convention. A half-tie-mass point is reported as a labelled
                 centre APPROXIMATION: halving a Poisson intensity is thinning,
                 which conditional on T tied opponents puts Binomial(T, 1/2)
                 ahead of us rather than the Uniform{0..T} a true random place
                 in the block would give. Same mean, different distribution, and
                 near a cutoff the distribution is what matters. The exact
                 fractional credit is available in closed form if the comparison
                 ever lands close enough to need it:
                     E[min(1, d/(T+1))] = P(T <= d-1) + (d/mu) P(T >= d+1)

Nothing here changes production. The money gate stays shut; no dollar figure
decides anything below.

    python3 -m research.s4_crossfit <feed dir> [dg ...]
"""
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")

WORLDS = 8000                 # 4,000 per fold
CANDIDATES = 1200             # production's shortlist size
SIZES = (1, 5, 10, 20)
PORT_K = 20
OBJECTIVES = ("top1", "top01")
ENDPOINTS = ("optimistic", "pessimistic")      # the two EXACT conventions
CENTRE = "half_mass"                           # a labelled approximation
SEEDS = (20260912, 771033)
DGS = (153086, 153085)
BOOT = 2000                   # paired bootstrap resamples over scoring worlds
BOOT_SEED = 11                # named so the provenance stamp records it too


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def equiv_mass(strict, level, convention):
    """The field mass to index the payout table at, under each tie convention.

    Opponents strictly above are Poisson(lamA) with lamA = -(C-1) ln(1-strict);
    opponents exactly level are Poisson(lamT), lamT = (C-1) level; independent
    Poissons add. So "the tied block finishes ahead of us" is the SAME table read
    at the mass whose lamA equals lamA + lamT, which is
    1 - (1-strict) exp(-level). Exact, and it cannot disagree with production
    because it is production's own table.
    """
    if convention == "optimistic":
        return strict
    if convention == "pessimistic":
        return 1.0 - (1.0 - strict) * np.exp(-level)
    if convention == CENTRE:
        return 1.0 - (1.0 - strict) * np.exp(-0.5 * level)
    raise ValueError(convention)


def topq(grid, qname, strict, level, convention):
    F, tab = grid["F"], grid[qname]
    fe = np.clip(equiv_mass(strict, level, convention), 0.0, 1.0)
    return tab[np.minimum(np.searchsorted(F, fe), len(F) - 1)]


def grids_by_size(contest, C, sizes=SIZES):
    """One grid per submission size k, with exactly C - k public opponents."""
    import dfs_tourney as T
    pay = contest.get("payouts") or []
    fee = float(contest.get("entry_fee") or 1.0)
    places = int(contest.get("places_paid") or max(1, C // 5))
    out, meta = {}, {}
    p1_real, p01_real = max(1, int(0.01 * C)), max(1, int(0.001 * C))
    for k in sizes:
        ce = C - k + 1                    # so the grid's own C-1 is exactly C-k
        out[k] = T.payout_grid(ce, pay, fee, places)
        p1, p01 = max(1, int(0.01 * ce)), max(1, int(0.001 * ce))
        meta[k] = {"public_opponents": int(C - k), "grid_entry_count": int(ce),
                   "top1_places": p1, "top01_places": p01,
                   "places_match_real_contest": bool(p1 == p1_real and p01 == p01_real)}
    return out, meta


def _field_pass(T, W, X, f, rows, worlds, want_all=None, grid=None, convs=(), qnames=()):
    """ONE pass over the field for a set of worlds.

    Two modes, both driven by the same histogram so the field is scanned once:

      rows given      -> returns (strict, level) matrices for those lineups
      want_all given  -> accumulates per-lineup SUMS of topq for every
                         (convention, qname) over `want_all` rows, and returns
                         no matrix at all. This is the shortlist path: it keeps
                         O(lineups) scalars instead of O(lineups x worlds), after
                         an innocent-looking vectorisation of the latter asked
                         for 43 GiB.
    """
    fw = np.asarray(f, dtype=np.float64)
    WT = np.ascontiguousarray(W.T)
    sel = rows if rows is not None else want_all
    WkT = np.ascontiguousarray(W[sel].T)
    nb = T._NB
    chunk = T.chunk_for(W.shape[0], 500)
    if rows is not None:
        strict = np.zeros((len(rows), len(worlds)), dtype=np.float32)
        level = np.zeros((len(rows), len(worlds)), dtype=np.float32)
    else:
        acc = {(c, q): np.zeros(len(want_all), dtype=np.float64)
               for c in convs for q in qnames}
    for a in range(0, len(worlds), chunk):
        ws = worlds[a:a + chunk]
        Xc = np.ascontiguousarray(X[:, ws].T)
        Bf = T._buckets(Xc @ WT)
        Bk = T._buckets(Xc @ WkT)
        for j in range(Bf.shape[0]):
            hist = np.bincount(Bf[j], weights=fw, minlength=nb)
            above = np.cumsum(hist[::-1])[::-1] - hist
            st, lv = above[Bk[j]], hist[Bk[j]]
            if rows is not None:
                strict[:, a + j] = st
                level[:, a + j] = lv
            else:
                for c in convs:
                    for q in qnames:
                        acc[(c, q)] += topq(grid, q, st, lv, c)
    if rows is not None:
        return strict, level
    return {k: v / len(worlds) for k, v in acc.items()}


def coverage(P, order, sizes=SIZES):
    """Shared-field union coverage for each prefix of a selected portfolio.

    One contest, one realised field: inside a world our entries are NESTED, so
    P(at least one makes it | world) is the best entry's probability, the row-wise
    max -- never 1 - prod(1 - p).
    """
    out = {}
    for k in sizes:
        if k > len(order):
            continue
        out[k] = float(np.max(P[order[:k]], axis=0).mean())
    return out


def paired_bootstrap(Pa, Pb, order_a, order_b, k, n=BOOT, seed=BOOT_SEED):
    """Paired bootstrap of (coverage_b - coverage_a) over SCORING worlds.

    The two portfolios are graded on the same worlds, so resampling worlds --
    not lineups -- keeps the pairing and removes the part of the noise both
    share. That is the whole reason the difference can be read when each level
    on its own cannot.
    """
    ca = np.max(Pa[order_a[:k]], axis=0)
    cb = np.max(Pb[order_b[:k]], axis=0)
    d = cb - ca
    rng = np.random.default_rng(seed)
    N = len(d)
    idx = rng.integers(0, N, size=(n, N))
    boots = d[idx].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"point": float(d.mean()), "ci_lo": float(lo), "ci_hi": float(hi),
             "sign_stable": bool(lo > 0 or hi < 0),
             "share_positive": float((boots > 0).mean())}


def one_direction(T, ents, W, ok, X, f, grids, gmeta, C, sel, sco, log=print):
    """Select on `sel`, grade on `sco`. Returns everything S4 asks for.

    The scoring fold is never touched until selection is finished: the shortlist
    and the greedy read `sel` only, and `sco` columns are not even constructed
    before that point. Guarded.
    """
    out = {"selection_worlds": [int(sel[0]), int(sel[-1])], "n_selection": int(len(sel)),
           "scoring_worlds": [int(sco[0]), int(sco[-1])], "n_scoring": int(len(sco)),
           "folds_disjoint": bool(not (set(sel.tolist()) & set(sco.tolist()))),
           "enterable": int(len(ok)), "shortlist_size": int(CANDIDATES),
           "shortlist_is_binding": bool(CANDIDATES < len(ok)),
           "by_objective": {}}
    # ---- shortlist, per objective, on the SELECTION fold only ---------------
    t0 = time.time()
    means = _field_pass(T, W, X, f, None, sel, want_all=ok, grid=grids[PORT_K],
                        convs=("optimistic",), qnames=OBJECTIVES)
    log(f"[S4X]   selection-fold ranking pass {time.time() - t0:.0f}s")
    shortlists = {}
    for obj in OBJECTIVES:
        m = means[("optimistic", obj)]
        shortlists[obj] = ok[np.argsort(-m)[:CANDIDATES]]
    # ---- greedy on the SELECTION fold, one portfolio per objective ----------
    rows = np.concatenate([shortlists[o] for o in OBJECTIVES])
    t0 = time.time()
    st_sel, lv_sel = _field_pass(T, W, X, f, rows, sel)
    log(f"[S4X]   selection-fold candidate matrix {time.time() - t0:.0f}s")
    chosen, ports = {}, {}
    for i, obj in enumerate(OBJECTIVES):
        sl = slice(i * CANDIDATES, (i + 1) * CANDIDATES)
        P = topq(grids[PORT_K], obj, st_sel[sl], lv_sel[sl], "optimistic")
        c, p_any = T.greedy_cover(P, PORT_K)
        chosen[obj] = c
        ports[obj] = shortlists[obj][np.asarray(c, dtype=np.int64)]
        out["by_objective"][obj] = {
            "in_sample_coverage": {str(k): round(float(p_any[k - 1]), 6) for k in SIZES},
            "shortlist_rank_of_picks": [int(x) for x in c]}
    del st_sel, lv_sel
    # ---- grade BOTH portfolios on the SCORING fold, one pass ----------------
    allrows = np.concatenate([ports[o] for o in OBJECTIVES])
    t0 = time.time()
    st_sco, lv_sco = _field_pass(T, W, X, f, allrows, sco)
    log(f"[S4X]   scoring-fold grade pass {time.time() - t0:.0f}s")
    order = {o: np.arange(i * PORT_K, (i + 1) * PORT_K) for i, o in enumerate(OBJECTIVES)}
    for conv in tuple(ENDPOINTS) + (CENTRE,):
        for metric in OBJECTIVES:
            for obj in OBJECTIVES:
                cov = {}
                for k in SIZES:
                    Pk = topq(grids[k], metric, st_sco, lv_sco, conv)
                    cov[str(k)] = round(float(np.max(Pk[order[obj][:k]], axis=0).mean()), 6)
                out["by_objective"][obj].setdefault("held_out", {}).setdefault(conv, {})[metric] = cov
    # paired difference at k=20, the quantity of interest
    out["paired"] = {}
    for conv in tuple(ENDPOINTS) + (CENTRE,):
        for metric in OBJECTIVES:
            P = topq(grids[PORT_K], metric, st_sco, lv_sco, conv)
            out["paired"].setdefault(conv, {})[metric] = paired_bootstrap(
                P, P, order["top1"], order["top01"], PORT_K)
    # ---- the C-1 vs C-k approximation, measured rather than assumed --------
    P20 = topq(grids[PORT_K], "top01", st_sco, lv_sco, "optimistic")
    P1 = topq(grids[1], "top01", st_sco, lv_sco, "optimistic")
    out["opponent_count_effect"] = {
        "note": ("C-1 treats our other 19 entries as public opponents. For an at-least-one "
                 "event they cannot outrank our best, so C-1 UNDERSTATES coverage. Measured "
                 "here as the 20-entry top-0.1% coverage read off the C-20 grid minus the "
                 "C-1 grid."),
        "top01_cov_C_minus_20": round(float(np.max(P20[order["top01"]], axis=0).mean()), 6),
        "top01_cov_C_minus_1": round(float(np.max(P1[order["top01"]], axis=0).mean()), 6)}
    e = out["opponent_count_effect"]
    e["absolute"] = round(e["top01_cov_C_minus_20"] - e["top01_cov_C_minus_1"], 6)
    e["relative_pct"] = (round(100.0 * e["absolute"] / e["top01_cov_C_minus_1"], 3)
                         if e["top01_cov_C_minus_1"] else None)
    out["portfolio"] = {o: [int(r) for r in ports[o]] for o in OBJECTIVES}
    out["grid_contract"] = gmeta
    return out, ports


def composition(ents, W, idx, ports):
    """What the two objectives actually picked, side by side."""
    from collections import Counter
    import nfl_dfs
    def rows(sel):
        cap, flex, struct, ks, ds, sal = Counter(), Counter(), Counter(), 0, 0, []
        for r in sel:
            row = idx[int(r)]
            c = ents[int(row[0])]
            cap[c["name"]] += 1
            legs = [ents[int(row[j])] for j in range(1, 6)]
            for p in legs:
                flex[p["name"]] += 1
            teams = Counter([c.get("team")] + [p.get("team") for p in legs])
            struct["-".join(str(x) for x in sorted(teams.values(), reverse=True))] += 1
            ks += sum(1 for p in [c] + legs if p["pos"] == "K")
            ds += sum(1 for p in [c] + legs if p["pos"] == "DST")
            sal.append(int(c["cpt_salary"] + sum(p["salary"] for p in legs)))
        return {"captains": dict(cap.most_common()), "flex_top": dict(flex.most_common(10)),
                "structures": dict(struct), "k_slots": int(ks), "dst_slots": int(ds),
                "salary_left_median": int(nfl_dfs.CAP - int(np.median(sal))),
                "salary_left_min": int(nfl_dfs.CAP - max(sal))}
    a, b = set(int(x) for x in ports["top1"]), set(int(x) for x in ports["top01"])
    return {"top1": rows(ports["top1"]), "top01": rows(ports["top01"]),
            "overlap": len(a & b), "jaccard": round(len(a & b) / max(len(a | b), 1), 4),
            "duplicates_top1": PORT_K - len(a), "duplicates_top01": PORT_K - len(b)}


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
    f, beta = T.field_weights(ents, idx, cpt_mult=1.5)
    C = int(contest.get("max_entries") or contest.get("entered") or 0) or 10000
    grids, gmeta = grids_by_size(contest, C)
    N = min(WORLDS, min(len(e["arr"]) for e in ents))
    X = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    ok = np.where(allowed)[0]
    half = N // 2
    A, B = np.arange(0, half), np.arange(half, N)
    log(f"[S4X] dg {dg} seed {seed}: {len(idx):,} lineups, {len(ok):,} enterable, "
        f"{N:,} worlds -> A[0:{half}] B[{half}:{N}], {C:,} entries, beta {beta:.4f}")
    res = {}
    ports_by_dir = {}
    for name, sel, sco in (("A_select_B_score", A, B), ("B_select_A_score", B, A)):
        log(f"[S4X]  {name}")
        r, ports = one_direction(T, ents, W, ok, X, f, grids, gmeta, C, sel, sco, log=log)
        r["composition"] = composition(ents, W, idx, ports)
        res[name] = r
        ports_by_dir[name] = {o: [int(x) for x in ports[o]] for o in OBJECTIVES}
        for conv in (ENDPOINTS[0], ENDPOINTS[1]):
            d = r["paired"][conv]["top01"]
            log(f"[S4X]    {conv:12s} held-out top0.1%: top1-selected "
                f"{r['by_objective']['top1']['held_out'][conv]['top01']['20']:.5f} vs "
                f"top0.1-selected "
                f"{r['by_objective']['top01']['held_out'][conv]['top01']['20']:.5f}  "
                f"delta {d['point']:+.5f} [{d['ci_lo']:+.5f},{d['ci_hi']:+.5f}] "
                f"{'STABLE' if d['sign_stable'] else 'not stable'}")
        log(f"[S4X]    portfolio overlap {r['composition']['overlap']}/{PORT_K}, "
            f"jaccard {r['composition']['jaccard']}")
    # fold-to-fold selection stability WITHIN each objective
    stab = {}
    for o in OBJECTIVES:
        a = set(ports_by_dir["A_select_B_score"][o])
        b = set(ports_by_dir["B_select_A_score"][o])
        stab[o] = {"overlap": len(a & b),
                   "jaccard": round(len(a & b) / max(len(a | b), 1), 4)}
    log(f"[S4X]  fold-to-fold stability: top1 {stab['top1']['overlap']}/{PORT_K}, "
        f"top01 {stab['top01']['overlap']}/{PORT_K}")
    return {"draft_group_id": int(dg), "contest": contest.get("name"), "entries": int(C),
            "legal_lineups": int(len(idx)), "enterable": int(len(ok)), "worlds": int(N),
            "field_beta": round(float(beta), 4), "seed": int(seed),
            "sd_engine": T.SD_ENGINE, "simulator": S.sim_stamp(N, False, S.SD_DISCRETE),
            "directions": res, "fold_stability": stab, "portfolios": ports_by_dir}


TOP_SHARES = (0.001, 0.002, 0.004)      # flatter, production, more concentrated


def sensitivity(feed_dir, dg, seed, log=print):
    """S4.7: does the conclusion survive a differently-concentrated public field?

    The field model is a placeholder -- one knob, beta, solved so the most popular
    build holds FIELD_TOP_SHARE of the field, from a single published contest. A
    conclusion that only holds at that one arbitrary value is not a conclusion, so
    the same football worlds are re-scored against a flatter field and a more
    concentrated one. These are SENSITIVITY SCENARIOS and none of them may become
    a default; the point is not to find a better beta.
    """
    import dfs_tourney as T
    from research import sd_board
    S = sd_board.feeds(feed_dir)
    S._cache.clear()
    slate, contest = sd_board.slate_and_contest(dg)
    ents, _s, _r = sd_board.ents_for(slate, sd_board.WEEK, S.SD_DISCRETE,
                                    n_sims=WORLDS, seed=seed, log=lambda *_a, **_k: None)
    idx, W, allowed = sd_board.universe(ents)
    C = int(contest.get("max_entries") or contest.get("entered") or 0) or 10000
    grids, gmeta = grids_by_size(contest, C)
    N = min(WORLDS, min(len(e["arr"]) for e in ents))
    X = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    ok = np.where(allowed)[0]
    half = N // 2
    A, B = np.arange(0, half), np.arange(half, N)
    out = {}
    for ts in TOP_SHARES:
        f, beta = T.field_weights(ents, idx, cpt_mult=1.5, top_share=ts)
        cells = {}
        for name, sel, sco in (("A_select_B_score", A, B), ("B_select_A_score", B, A)):
            r, _ports = one_direction(T, ents, W, ok, X, f, grids, gmeta, C, sel, sco,
                                      log=lambda *_a, **_k: None)
            cells[name] = {c: {m: r["paired"][c][m] for m in OBJECTIVES} for c in ENDPOINTS}
        out[str(ts)] = {"top_share": ts, "beta": round(float(beta), 4),
                        "scenario": ("flatter field" if ts < 0.002 else
                                     "production" if ts == 0.002 else "more concentrated"),
                        "directions": cells}
        for name in cells:
            for c in ENDPOINTS:
                d = cells[name][c]["top01"]
                log(f"[S4S] top_share {ts:.3f} beta {beta:.4f} {name[:3]} {c:12s} "
                    f"top0.1% delta {d['point']:+.5f} "
                    f"[{d['ci_lo']:+.5f},{d['ci_hi']:+.5f}] "
                    f"{'STABLE' if d['sign_stable'] else 'not stable'}")
    return {"draft_group_id": int(dg), "seed": int(seed), "worlds": int(N),
            "note": ("sensitivity scenarios only; no value here may become a production "
                     "default, and none of them is a calibrated alternative"),
            "by_top_share": out}


def run(feed_dir, dgs=DGS, seeds=SEEDS, log=print):
    out = {"meta": {
        "stage": "S4 portfolio objective cross-fit (RESEARCH; nothing promoted)",
        "question": ("does selecting for Top 0.1% beat selecting for Top 1% on HELD-OUT "
                     "extreme-tail coverage, or is the in-sample gap an artifact of the "
                     "leakage S4.1 found?"),
        "worlds_total": WORLDS, "candidates": CANDIDATES, "portfolio_k": PORT_K,
        "sizes": list(SIZES), "seeds": list(seeds), "boards": list(dgs),
        "objectives": list(OBJECTIVES),
        "tie_conventions": {
            "optimistic": "EXACT. only field mass strictly above counts (production today)",
            "pessimistic": "EXACT. every tied opponent finishes ahead of us",
            "half_mass": ("APPROXIMATION, reported as a centre point only. Halving the tie "
                          "intensity is Poisson thinning, which conditional on T tied "
                          "opponents puts Binomial(T, 1/2) ahead of us rather than the "
                          "Uniform{0..T} a true random place in the block gives -- same "
                          "mean, different distribution. The decision rests on the two "
                          "exact endpoints. Exact fractional credit, if ever needed: "
                          "E[min(1, d/(T+1))] = P(T<=d-1) + (d/mu) P(T>=d+1).")},
        "opponent_count_contract": (
            "a submission of exactly k entries faces C - k public opponents; the grid for "
            "size k is built at C - k + 1 entries so its internal C-1 is exactly C - k, and "
            "the qualifying place counts are asserted unchanged. The 20-entry portfolio uses "
            "C - 20 throughout selection AND held-out scoring so the matrix does not shift "
            "as the greedy grows."),
        "money": "gate CLOSED; no payout figure is computed or cited in this stage",
        "provenance": _prov(worlds=WORLDS, model="legacy-latent-discrete",
                                        seed={"boards": list(SEEDS), "bootstrap": BOOT_SEED})},
        "boards": {}}
    for dg in dgs:
        out["boards"][str(dg)] = {}
        for seed in seeds:
            out["boards"][str(dg)][str(seed)] = board(feed_dir, dg, seed, log=log)
    log("[S4S] field-concentration sensitivity (one board, one seed, both directions)")
    out["field_sensitivity"] = sensitivity(feed_dir, dgs[0], seeds[0], log=log)
    with open(os.path.join(DATA, "s4_crossfit.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "feeds")
    dl = tuple(int(x) for x in sys.argv[2:]) or DGS
    run(fd, dl)
    print("S4 cross-fit written")
