"""Stage 4: the three decisions the engine has been holding open, measured
on held-out worlds against the real slate.

  4A  which objective a portfolio should cover -- the top 1%, the top
      0.1%, or the tie-aware payout -- at 1, 2, 3, 5, 10 and 20 entries,
      cross-fit: the portfolio is chosen on one half of the worlds and
      judged on the other, so a cover cannot be graded on the worlds it
      was fitted to;
  4B  whether a pass-catching back counts as the quarterback's stack
      partner (dfs_tourney.RB_STACK_TARGETS: never, or from three, four
      or five projected targets), by what it does to the candidate
      population and to the portfolio's held-out numbers;
  4C  the per-game cap (four, five, six, none), the same way.

Everything is judged on worlds the choice never saw, and every number is
the tie-aware payout of Stage 3B. The field, the candidates and the world
split are shared across the three studies so the comparisons differ only
in the knob.

    python3 -m research.stage4 <dir with ents.json and X.npy>
"""
import json
import os
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
REPORTS = os.path.join(ROOT, "research", "reports")
BASE_SEED = 20260911
FIELD_N = 150_000
CAND_N = 60_000
SIZES = (1, 2, 3, 5, 10, 20)
CONTEST = {"id": 1, "name": "research millionaire", "entry_fee": 5.0, "max_entries": 832_000,
           "places_paid": 166_400, "first_prize": 1_000_000.0,
           "payouts": [{"from": 1, "to": 1, "prize": 1_000_000.0}, {"from": 2, "to": 2, "prize": 100_000.0},
                       {"from": 3, "to": 3, "prize": 75_000.0}, {"from": 4, "to": 5, "prize": 50_000.0},
                       {"from": 6, "to": 10, "prize": 25_000.0}, {"from": 11, "to": 25, "prize": 10_000.0},
                       {"from": 26, "to": 50, "prize": 5_000.0}, {"from": 51, "to": 100, "prize": 2_000.0},
                       {"from": 101, "to": 250, "prize": 1_000.0}, {"from": 251, "to": 1_000, "prize": 200.0},
                       {"from": 1_001, "to": 5_000, "prize": 50.0}, {"from": 5_001, "to": 20_000, "prize": 20.0},
                       {"from": 20_001, "to": 60_000, "prize": 10.0}, {"from": 60_001, "to": 166_400, "prize": 8.0}]}


def _commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def prep(slate_dir, log=print):
    """The shared board: field, candidates, the world split and the grid."""
    import dfs_tourney as T
    ents = json.load(open(os.path.join(slate_dir, "ents.json")))
    X = np.load(os.path.join(slate_dir, "X.npy"))
    P, N = X.shape
    rng = np.random.default_rng(BASE_SEED)
    beta, kappa, max_own, mean_sal, coll, top_share = T.calibrate_field(ents, rng, n=20000)
    log(f"[4] {P} players x {N:,} worlds; beta {beta:.3f} kappa {kappa:.3f}; field {FIELD_N:,}...")
    rep = {}
    idx_f = T.classic_sample(ents, FIELD_N, np.random.default_rng(BASE_SEED + 1), beta, kappa, report=rep)
    T.check_completion(rep, "stage 4 field")
    rep = {}
    fo = np.asarray([bool(e.get("_field_only")) for e in ents])
    idx_c = T.classic_sample(ents, CAND_N, np.random.default_rng(BASE_SEED + 2), beta, kappa,
                             rules=True, exclude=fo, report=rep)
    T.check_completion(rep, "stage 4 candidates")
    # the hindsight-optimal lineup of the first worlds joins the candidates,
    # exactly as a board builds them
    gen = range(0, N // 4)
    keep = T._solver_pool(X, [e["pos"] for e in ents], gen)
    sal = np.asarray([e["salary"] for e in ents])
    opt_L, _ = T.optimal_lineups(X[keep][:, gen.start:gen.stop], sal[keep], [ents[i]["pos"] for i in keep], chunk=64)
    opt = np.unique(np.asarray([[int(keep[i]) for i in L] for L in opt_L if L], dtype=np.int32), axis=0)
    idx_c = np.concatenate([T._slot_rows([tuple(r) for r in opt], [e["pos"] for e in ents]), idx_c], axis=0)
    idx_c = np.unique(np.sort(idx_c, axis=1), axis=0)
    idx_c = T._slot_rows([tuple(r) for r in idx_c], [e["pos"] for e in ents])
    log(f"[4] {len(idx_c):,} candidates ({len(opt):,} from hindsight); splitting {N:,} worlds in half...")
    ev_worlds = np.arange(N // 4, N)
    half = len(ev_worlds) // 2
    return {"ents": ents, "X": X, "idx_f": idx_f, "idx_c": idx_c, "beta": beta, "kappa": kappa,
            "build_worlds": ev_worlds[:half], "score_worlds": ev_worlds[half:], "N": N, "P": P}


def score(board, idx_c, worlds, chunk=400):
    """Every candidate's numbers on a set of worlds."""
    import dfs_tourney as T
    ents, X = board["ents"], board["X"]
    P = len(ents)
    Wc = T.lineup_matrix(idx_c.astype(np.int64), P)
    Wf = T.lineup_matrix(board["idx_f"].astype(np.int64), P)
    wf = np.full(len(board["idx_f"]), 1.0 / len(board["idx_f"]))
    grid = T.payout_grid(CONTEST["max_entries"], CONTEST["payouts"], CONTEST["entry_fee"], CONTEST["places_paid"])
    Xw = np.ascontiguousarray(X[:, worlds])
    res, opt, n = T.run_vs_field(Wc, Wf, wf, Xw, [grid], chunk=chunk)
    return res[0], grid, Wc, Wf, wf, Xw


def portfolio_study(board, log=print):
    """4A: cover the top 1%, cover the top 0.1%, or take the best by the
    tie-aware payout -- chosen on the build worlds, judged on the others."""
    import dfs_tourney as T
    ents = board["ents"]
    idx_c = board["idx_c"]
    allowed = T.classic_allowed(idx_c, ents)
    idx_a = idx_c[allowed]
    log(f"[4A] {len(idx_a):,} allowed candidates; scoring the build worlds...")
    rb, grid, Wc, Wf, wf, Xb = score(board, idx_a, board["build_worlds"])
    top = np.argsort(-rb["top1"])[:4000]                  # the cover searches the strongest 4,000
    out = {}
    picks = {}
    for name in ("top1", "top01", "ev"):
        if name == "ev":
            picks[name] = [int(i) for i in np.argsort(-rb["ev"])[:max(SIZES)]]
        else:
            chosen, _ = T.portfolio_vs_field(Wc, Wf, wf, Xb, [grid], top, max(SIZES), rank=name)[0]
            picks[name] = [int(top[j]) for j in chosen]
    log("[4A] scoring the held-out worlds...")
    rs, grid2, Wc2, Wf2, wf2, Xs = score(board, idx_a, board["score_worlds"])
    for name, sel in picks.items():
        rows = []
        for k in SIZES:
            take = np.asarray(sel[:k])
            p_any1, p_any01 = _p_any(board, idx_a[take], grid2, Wf2, wf2, Xs)
            rows.append({"size": k, "p_any_top1": p_any1, "p_any_top01": p_any01,
                         "ev_total": float(rs["ev"][take].sum()), "ev_notie_total": float(rs["ev_notie"][take].sum()),
                         "roi_pct": 100.0 * (float(rs["ev"][take].sum()) - k * CONTEST["entry_fee"]) / (k * CONTEST["entry_fee"]),
                         "win_sole_any_pct": 100.0 * float(1.0 - np.prod(1.0 - rs["win_sole"][take]))})
        out[name] = rows
    out["_picks"] = {k: [[ents[i]["name"] for i in idx_a[j]] for j in v[:3]] for k, v in picks.items()}
    out["_overlap"] = {f"{a} vs {b}": len(set(picks[a][:20]) & set(picks[b][:20]))
                       for a, b in (("top1", "top01"), ("top1", "ev"), ("top01", "ev"))}
    return out


def _p_any(board, rows, grid, Wf, wf, Xs, chunk=400):
    """P(at least one of these entries makes the top 1% / top 0.1%) on the
    scoring worlds, world by world so the entries' overlap counts."""
    import dfs_tourney as T
    P = len(board["ents"])
    Wk = T.lineup_matrix(rows.astype(np.int64), P)
    F_grid = grid["F"]
    N = Xs.shape[1]
    miss1 = np.ones(N)
    miss01 = np.ones(N)
    WfT = np.ascontiguousarray(Wf.T)
    WkT = np.ascontiguousarray(Wk.T)
    ch = T.chunk_for(max(len(rows), Wf.shape[0]), chunk)
    for start in range(0, N, ch):
        Xc = np.ascontiguousarray(Xs[:, start:start + ch].T)
        Bf = T._buckets(Xc @ WfT)
        Bk = T._buckets(Xc @ WkT)
        for j in range(Bf.shape[0]):
            pos = T._world_tables(Bf[j], wf, F_grid)
            miss1[start + j] = float(np.prod(1.0 - grid["top1"][pos][Bk[j]]))
            miss01[start + j] = float(np.prod(1.0 - grid["top01"][pos][Bk[j]]))
    return float(1.0 - miss1.mean()), float(1.0 - miss01.mean())


def unstacked_pool(board, log=print):
    """A candidate population the stack rule has NOT already filtered: the
    sampler is asked for draws with no forced stack (rules off, field-only
    players still excluded), so lineups whose only tie to the quarterback is
    a pass-catching back exist to be judged. Without this the 4B question
    cannot even be put -- every rule-abiding draw already carries a receiver
    from the quarterback's team, so the knob changes nothing.""" 
    import dfs_tourney as T
    ents = board["ents"]
    fo = np.asarray([bool(e.get("_field_only")) for e in ents])
    rep = {}
    idx = T.classic_sample(ents, CAND_N, np.random.default_rng(BASE_SEED + 9), board["beta"], board["kappa"],
                           rules=False, exclude=fo, report=rep)
    T.check_completion(rep, "stage 4B unstacked draws")
    idx = T._slot_rows([tuple(r) for r in np.unique(np.sort(idx, axis=1), axis=0)], [e["pos"] for e in ents])
    # keep only the rows that are legal but for the stack rule, so the knob is
    # the only thing that decides them
    strict = T.classic_allowed(idx, ents)
    loose = T.classic_allowed(idx, ents, rb_stack_targets=0.0)
    log(f"[4B] {len(idx):,} unforced draws: {int(strict.sum()):,} already carry a receiver stack, "
        f"{int((loose & ~strict).sum()):,} are legal only if a back may count")
    return idx, strict, loose


def knob_study(board, knob, values, log=print, idx_pool=None):
    """4B and 4C: for each value of one rule knob, the allowed candidate
    population and the held-out numbers of the portfolio it produces."""
    import dfs_tourney as T
    ents = board["ents"]
    idx_c = board["idx_c"] if idx_pool is None else idx_pool
    out = []
    for v in values:
        kw = {knob: v}
        allowed = T.classic_allowed(idx_c, ents, **kw)
        idx_a = idx_c[allowed]
        if len(idx_a) < 200:
            out.append({"value": v, "allowed": int(len(idx_a)), "note": "too few candidates"})
            continue
        rb, grid, Wc, Wf, wf, Xb = score(board, idx_a, board["build_worlds"])
        top = np.argsort(-rb["top1"])[:4000]
        chosen, _ = T.portfolio_vs_field(Wc, Wf, wf, Xb, [grid], top, 20, rank="top1")[0]
        sel = np.asarray([int(top[j]) for j in chosen])
        rs, grid2, Wc2, Wf2, wf2, Xs = score(board, idx_a, board["score_worlds"])
        p1, p01 = _p_any(board, idx_a[sel[:20]], grid2, Wf2, wf2, Xs)
        best = int(np.argmax(rs["top1"]))
        out.append({"value": v, "allowed": int(len(idx_a)), "share_allowed": float(allowed.mean()),
                    "best_top1_pct": 100.0 * float(rs["top1"][best]),
                    "best_top01_pct": 100.0 * float(rs["top01"][best]),
                    "mean_top1_pct_of_20": 100.0 * float(rs["top1"][sel[:20]].mean()),
                    "p_any_top1_20": p1, "p_any_top01_20": p01,
                    "ev_total_20": float(rs["ev"][sel[:20]].sum()),
                    "roi_pct_20": 100.0 * (float(rs["ev"][sel[:20]].sum()) - 20 * CONTEST["entry_fee"]) / (20 * CONTEST["entry_fee"]),
                    "example": [ents[i]["name"] for i in idx_a[sel[0]]]})
        log(f"[4] {knob}={v}: {len(idx_a):,} allowed, P(any of 20 in the top 1%) {p1:.4f}")
    return out


def run(slate_dir, log=print):
    t0 = time.time()
    board = prep(slate_dir, log=log)
    port = portfolio_study(board, log=log)
    log("[4B] the receiving back as a stack partner...")
    idx_rb, strict_rb, loose_rb = unstacked_pool(board, log=log)
    rb = knob_study(board, "rb_stack_targets", [None, 3.0, 4.0, 5.0], log=log, idx_pool=idx_rb)
    rb_counts = {"unforced_draws": int(len(idx_rb)), "already_stacked": int(strict_rb.sum()),
                 "legal_only_if_a_back_counts": int((loose_rb & ~strict_rb).sum())}
    log("[4C] the per-game cap...")
    cap = knob_study(board, "max_per_game", [4, 5, 6, None], log=log)
    st = {"meta": {"stage": "4", "commit": _commit(), "base_seed": BASE_SEED, "slate_dir": os.path.basename(slate_dir.rstrip("/")),
                   "players": board["P"], "worlds": board["N"], "field": FIELD_N, "candidates": int(len(board["idx_c"])),
                   "build_worlds": int(len(board["build_worlds"])), "score_worlds": int(len(board["score_worlds"])),
                   "contest": {k: CONTEST[k] for k in ("name", "entry_fee", "max_entries", "places_paid", "first_prize")},
                   "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "seconds": round(time.time() - t0, 1)},
          "portfolio_objective": port, "rb_stack_rule": rb, "rb_stack_counts": rb_counts, "game_cap": cap}
    st["meta"]["seconds"] = round(time.time() - t0, 1)
    json.dump(st, open(os.path.join(DATA, "stage4_stats.json"), "w"), indent=1, sort_keys=True)
    write_report(st)
    return st


def write_report(st):
    m = st["meta"]
    L = ["# Stage 4: the portfolio's objective, the receiving-back stack rule and the game cap\n",
         f"Commit {m['commit']}, base seed {m['base_seed']}, the real Sunday main slate ({m['players']} players, {m['worlds']:,} simulated worlds). "
         f"Field {m['field']:,}, {m['candidates']:,} candidates. Every choice is made on {m['build_worlds']:,} worlds and judged on {m['score_worlds']:,} it never saw. "
         f"Contest: {m['contest']['max_entries']:,} entries at ${m['contest']['entry_fee']:.0f}, {m['contest']['places_paid']:,} paid, ${m['contest']['first_prize']:,.0f} for first. "
         f"Payouts are the tie-aware ones of Stage 3B. Built {m['built']} ({m['seconds']}s).\n",
         "## 4A: which objective the portfolio should cover\n",
         "| Objective | Entries | P(one in the top 1%) | P(one in the top 0.1%) | P(one wins outright) | Expected payout | Ignoring ties | ROI |",
         "|---|---|---|---|---|---|---|---|"]
    for name in ("top1", "top01", "ev"):
        for r in st["portfolio_objective"][name]:
            L.append(f"| {name} | {r['size']} | {r['p_any_top1']:.4f} | {r['p_any_top01']:.5f} | {r['win_sole_any_pct']:.4f}% | ${r['ev_total']:.2f} | ${r['ev_notie_total']:.2f} | {r['roi_pct']:+.0f}% |")
    L.append(f"\nOverlap of the top twenty between objectives: {json.dumps(st['portfolio_objective']['_overlap'])}.\n")
    L.append("## 4B: does a pass-catching back count as the stack partner\n")
    rc = st.get("rb_stack_counts") or {}
    if rc:
        L.append(f"The question needs a candidate population the stack rule has not already filtered, so these draws are taken with the stack requirement off: "
                 f"{rc['unforced_draws']:,} distinct lineups, {rc['already_stacked']:,} of which carry a receiver from the quarterback's team anyway and "
                 f"{rc['legal_only_if_a_back_counts']:,} of which are legal only if a back may count. (Asked on the ordinary rule-abiding draws the knob changes "
                 "nothing at all, because every one of those already carries a receiver stack -- that run is what showed the experiment had to be built this way.)\n")
    L.append("| Projected targets to count | Candidates allowed | share | P(one of 20 in the top 1%) | P(one in the top 0.1%) | best single top 1% | expected payout of 20 | ROI |\n|---|---|---|---|---|---|---|---|")
    for r in st["rb_stack_rule"]:
        if r.get("note"):
            L.append(f"| {r['value']} | {r['allowed']} | - | {r['note']} | | | | |")
            continue
        lab = "never" if r["value"] is None else f"{r['value']:g}"
        L.append(f"| {lab} | {r['allowed']:,} | {r['share_allowed']:.3f} | {r['p_any_top1']:.4f} | {r['p_any_top01']:.5f} | {r['best_top1_pct']:.2f}% | ${r['ev_total_20']:.2f} | {r['roi_pct_20']:+.0f}% |")
    L.append("\n## 4C: the per-game cap\n")
    L.append("| Cap | Candidates allowed | share | P(one of 20 in the top 1%) | P(one in the top 0.1%) | best single top 1% | expected payout of 20 | ROI |\n|---|---|---|---|---|---|---|---|")
    for r in st["game_cap"]:
        if r.get("note"):
            L.append(f"| {r['value']} | {r['allowed']} | - | {r['note']} | | | | |")
            continue
        L.append(f"| {'none' if r['value'] is None else r['value']} | {r['allowed']:,} | {r['share_allowed']:.3f} | {r['p_any_top1']:.4f} | {r['p_any_top01']:.5f} | {r['best_top1_pct']:.2f}% | ${r['ev_total_20']:.2f} | {r['roi_pct_20']:+.0f}% |")
    L.append("\nA knob is only worth changing if it wins on worlds it never saw. The numbers above are the whole case for whatever the engine ends up set to.\n")
    with open(os.path.join(REPORTS, "stage4_report.md"), "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "research", "data", "slate")
    st = run(d)
    print("stage 4 written")
