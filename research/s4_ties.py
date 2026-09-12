"""S4.10: what does "Top 0.1%" mean when a score tie spans the cutoff?

The Showdown board's Top-q columns are read off a table indexed by the field
mass STRICTLY ABOVE a lineup (`dfs_tourney._world_tables` -> `strictly`). Mass
sitting EXACTLY LEVEL with the lineup is not in that index at all, so every tied
opponent is treated as finishing behind us -- while `win_sole` and `ev` in the
same grid are tie-EXACT, reading the level mass and splitting the prize the way
DraftKings does. The board pays ties honestly and ranks them optimistically.

That convention was never chosen; it fell out of indexing the payout table by
strict mass alone, and it mattered little while 47% of simulated scores were off
the DraftKings lattice, because a near-continuous support almost never produces
an exact tie. Discrete-v2 restored real ties on purpose, so it has to be checked
before Top 0.1% is used as a SELECTION objective: an optimiser maximising a
tie-optimistic metric is rewarded for landing ON the field rather than beating
it, which is the opposite of the intent.

THE ANSWER, MEASURED: the convention is not load-bearing on a live board.
DEN @ KC, 88,235 entries, 4,000 worlds, 23,820 enterable lineups:

    top 1%    (882 places)  best lineup 0.08658, bracket width 1.5% of it
    top 0.1%  ( 88 places)  best lineup 0.02476, bracket width 1.1% of it

The same lineup is best under both ends, the top twenty overlaps 20/20 and 18/20,
and the rank correlation is 0.995+. Tied blocks really are 20 entries wide at the
median and 103 at the p90 -- but qualifying for 88 places is dominated by worlds
where the count above us is nowhere near 88, and the band where a tie block
straddles the cutoff is a thin minority.

A 1,000-entry synthetic contest, where top 0.1% is ONE place, puts the swing at
150x. That figure is an artifact of the cutoff being a single seat and must not
be quoted as live severity; this is the same mistake the lattice pass made twice
before it measured real boards. The bracket below is the instrument precisely
because it can come back narrow.

CONSEQUENCE: the inconsistency is documented and the convention is stated, but
the engine is NOT bumped and no production number is changed for a 1.1% effect.
Top-q stays optimistic, consistently, and the report says so where the metric is
defined.

    python3 -m research.s4_ties <feed dir> [draft_group_id]
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")

WORLDS = 4000
DG_DEFAULT = 153086


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def topq_bracket(grid, qname, strict, level, C):
    """The exact BRACKET of defensible tie conventions, from production's own table.

    The count of opponents strictly above us is A ~ Poisson(lamA) with
    lamA = -(C-1) ln(1 - strict), which is exactly what `payout_grid` already
    assumes (and it switches to a Normal on the rank where that mean is large).
    The count exactly level with us is T ~ Poisson(lamT), lamT = (C-1) * level,
    independent of A.

    That gives both ends of the bracket for free, with no new machinery and no
    chance of disagreeing with production:

      optimistic  P(A <= k-1)          -- index the table at F = strict
      pessimistic P(A + T <= k-1)      -- sums of independent Poissons are
                                          Poisson, so index the SAME table at
                                          the equivalent mass
                                            F' = 1 - (1 - strict) * exp(-level)

    Every convention that treats a tie as neither better than winning nor worse
    than losing lies between them, so the width of this bracket IS the question
    "does the convention matter here".
    """
    F = grid["F"]
    tab = grid[qname]
    strict = np.clip(np.asarray(strict, dtype=np.float64), 0.0, 1.0)
    level = np.clip(np.asarray(level, dtype=np.float64), 0.0, 1.0)
    f_eq = np.clip(1.0 - (1.0 - strict) * np.exp(-level), 0.0, 1.0)
    i_opt = np.minimum(np.searchsorted(F, strict), len(F) - 1)
    i_pes = np.minimum(np.searchsorted(F, f_eq), len(F) - 1)
    return tab[i_opt], tab[i_pes]


def run(feed_dir, dg=DG_DEFAULT, log=print):
    import dfs_tourney as T
    from research import sd_board
    S = sd_board.feeds(feed_dir)
    S._cache.clear()
    slate, contest = sd_board.slate_and_contest(dg)
    if not slate or not contest:
        raise SystemExit(f"draft group {dg} has no slate or contest")
    ents, _s, _r = sd_board.ents_for(slate, sd_board.WEEK, S.SD_DISCRETE,
                                    n_sims=WORLDS, seed=20260912, log=log)
    idx, W, allowed = sd_board.universe(ents)
    f, beta = T.field_weights(ents, idx, cpt_mult=1.5)
    grid, C = sd_board.grid_for(contest)
    N = min(len(e["arr"]) for e in ents)
    X = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    ok = np.where(allowed)[0]
    log(f"[S4T] dg {dg}: {len(idx):,} lineups, {len(ok):,} enterable, {N:,} worlds, "
        f"{C:,} entries -> top1 = {max(1, int(0.01 * C))} places, "
        f"top0.1% = {max(1, int(0.001 * C))} places")
    # the field histogram per world, and our candidates' strict / level mass
    chunk = T.chunk_for(len(idx), 500)
    WT = np.ascontiguousarray(W.T)
    WkT = np.ascontiguousarray(W[ok].T)
    strict = np.zeros((len(ok), N), dtype=np.float32)
    level = np.zeros((len(ok), N), dtype=np.float32)
    fw = np.asarray(f, dtype=np.float64)
    for start in range(0, N, chunk):
        Xc = np.ascontiguousarray(X[:, start:start + chunk].T)
        Bf = T._buckets(Xc @ WT)
        Bk = T._buckets(Xc @ WkT)
        for j in range(Bf.shape[0]):
            hist = np.bincount(Bf[j], weights=fw, minlength=T._NB)
            above = np.cumsum(hist[::-1])[::-1] - hist
            strict[:, start + j] = above[Bk[j]]
            level[:, start + j] = hist[Bk[j]]
    log(f"[S4T] field mass level with an enterable lineup: "
        f"median {np.median(level):.3e}, p90 {np.percentile(level, 90):.3e}, "
        f"max {level.max():.3e}  (1/{C:,} = {1.0 / C:.3e})")
    lamT = (C - 1) * level.astype(np.float64)
    out = {"meta": {"stage": "S4.10 tie-at-cutoff semantics (measurement only)",
                    "draft_group_id": int(dg), "contest": contest.get("name"),
                    "entries": int(C), "worlds": int(N),
                    "enterable": int(len(ok)), "legal_lineups": int(len(idx)),
                    "field_beta": round(float(beta), 4),
                    "sd_engine": T.SD_ENGINE,
                    "simulator": S.sim_stamp(N, False, S.SD_DISCRETE)["model"],
                    "conventions": {
                        "optimistic": "every tied opponent finishes behind us (PRODUCTION today)",
                        "pessimistic": "every tied opponent finishes ahead of us",
                        "bracket": ("any convention that treats a tie as neither a win nor a loss "
                                    "lies between these two, so the width of the bracket is "
                                    "whether the convention matters at all")},
                    "tied_block_size": {
                        "median": round(float(np.median(lamT)), 2),
                        "p90": round(float(np.percentile(lamT, 90)), 2),
                        "p99": round(float(np.percentile(lamT, 99)), 2),
                        "max": round(float(lamT.max()), 2)},
                    "provenance": _prov(worlds=N, model="legacy-latent-discrete")},
           "by_q": {}}
    for qname, q in (("top1", 0.01), ("top01", 0.001)):
        places = max(1, int(q * C))
        # chunked over worlds so the temporary stays bounded
        s_opt = np.zeros(len(ok), dtype=np.float64)
        s_pes = np.zeros(len(ok), dtype=np.float64)
        wide = np.zeros(len(ok), dtype=np.float64)      # worlds where the bracket is wide
        for w0 in range(0, N, 200):
            w1 = min(N, w0 + 200)
            o, p_ = topq_bracket(grid, qname, strict[:, w0:w1], level[:, w0:w1], C)
            s_opt += o.sum(axis=1)
            s_pes += p_.sum(axis=1)
            wide += (np.abs(o - p_) > 0.01).sum(axis=1)
        per_opt, per_pes = s_opt / N, s_pes / N
        per_wide = wide / N
        ord_opt = np.argsort(-per_opt)
        ord_pes = np.argsort(-per_pes)
        out["by_q"][qname] = {
            "places": int(places),
            "mean_over_enterable": {"optimistic": round(float(per_opt.mean()), 6),
                                    "pessimistic": round(float(per_pes.mean()), 6)},
            "best_lineup_value": {"optimistic": round(float(per_opt.max()), 6),
                                  "pessimistic": round(float(per_pes.max()), 6)},
            "bracket_width_mean": round(float((per_opt - per_pes).mean()), 6),
            "bracket_width_at_best": round(float(per_opt.max() - per_pes[int(ord_opt[0])]), 6),
            "relative_bracket_at_best": (round(float((per_opt.max() - per_pes[int(ord_opt[0])])
                                                     / max(per_opt.max(), 1e-12)), 4)),
            "worlds_with_wide_bracket_pct": round(100.0 * float(per_wide.mean()), 3),
            "best_lineup_same_under_both": bool(int(ord_opt[0]) == int(ord_pes[0])),
            "top20_overlap": int(len(set(ord_opt[:20].tolist()) & set(ord_pes[:20].tolist()))),
            "rank_corr": round(float(np.corrcoef(np.argsort(np.argsort(-per_opt)),
                                                 np.argsort(np.argsort(-per_pes)))[0, 1]), 4)}
        d = out["by_q"][qname]
        log(f"[S4T] {qname} ({places} places): mean over enterable "
            f"{d['mean_over_enterable']['optimistic']:.5f} optimistic vs "
            f"{d['mean_over_enterable']['pessimistic']:.5f} pessimistic")
        log(f"[S4T]   best lineup {d['best_lineup_value']['optimistic']:.5f} -> bracket width "
            f"{d['bracket_width_at_best']:.5f} ({100 * d['relative_bracket_at_best']:.1f}% of it); "
            f"same best lineup? {d['best_lineup_same_under_both']}; "
            f"top-20 overlap {d['top20_overlap']}/20; rank corr {d['rank_corr']}")
    with open(os.path.join(DATA, "s4_ties.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "feeds")
    run(fd, int(sys.argv[2]) if len(sys.argv) > 2 else DG_DEFAULT)
    print("S4.10 tie semantics written")
