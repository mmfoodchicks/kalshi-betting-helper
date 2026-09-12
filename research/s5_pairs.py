"""S5.9 -- pairwise rule removals, triggered by a residual the singles do not explain.

S5.8 found the full-DK-legal portfolio gains +0.061 held-out Top 1% on average
while the sum of all single-rule ablations explains only +0.049 of it. The
residual is +0.0122 on average and +0.0341 in the worst cell, and in one cell
the singles sum NEGATIVE while full-legal is positive. Something only two rules
coming off together can reach.

The subsumption matrix from S5.1 says exactly what. `CPT-POS` (no kicker or
defense as captain) is 100% subsumed by `CPT-SALARY` (the captain salary floor)
on both boards -- a kicker never costs $11,000 at captain price -- so removing
either alone admits nothing, and a K/DST captain becomes enterable ONLY when
both come off. Same shape for `CPT-POOL`. That is a prediction from structure,
not a pattern noticed afterwards, which is why these pairs are declared here
before the run rather than chosen from its output.

PAIRS IS PRE-DECLARED AND CLOSED. Four pairs, each with its reason. Testing
every pair would be 36 universes a cell and would turn a hypothesis test into a
search; testing the ones the subsumption structure implicates is the test.
Higher-order combinations (three or more rules off together) remain UNTESTED and
the report says so -- the full-legal counterfactual is the only evidence about
them and it cannot attribute.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")

from research import s4_crossfit as X4      # noqa: E402
from research import s5_ablate as A5        # noqa: E402
from research import s5_rules as R5         # noqa: E402

#: (pair, why it is here). Declared from S5.1's subsumption matrix and S5.8's
#: single-rule results, both of which already existed when this was written.
PAIRS = (
    (("CPT-SALARY", "CPT-POS"),
     "CPT-POS is 100% subsumed by CPT-SALARY on both boards, so a kicker or "
     "defense captain is enterable only when BOTH come off. The largest "
     "structurally predicted interaction."),
    (("CPT-SALARY", "CPT-POOL"),
     "same subsumption, at the depth gate: a depth-dropped captain is also "
     "always under the salary floor."),
    (("CPT-SALARY", "DST-OPP"),
     "the only two rules with a non-zero single-rule effect, so their "
     "combination is where additivity is most directly testable."),
    (("CPT-SALARY", "FLEX-POOL"),
     "the two largest marginal-admission sets (37k and 47k on DEN @ KC), so "
     "the pair with the most room to interact by volume."),
)

# PUNT-ROLE is deliberately NOT paired. Its single-rule effect is negative
# (-0.0019 mean) and stable in 1 of 8 cells, i.e. noise-dominated, and adding it
# would double the pair count for a rule with no signal to interact with.
EXCLUDED = {"PUNT-ROLE": "single-rule effect noise-dominated (-0.0019, 1/8 stable)",
            "MAX-KDST": "546 marginal admissions, zero single-rule effect",
            "MAX-PUNT": "zero marginal admissions; 100% subsumed by PUNT-ROLE",
            "MAX-TE-TEAM": "zero marginal admissions"}


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def one_direction(T, W, X, f, masks, grids, sel, sco, log=print):
    """Baseline, each pre-declared pair, and full-legal, on one fold direction."""
    import numpy as np
    allowed = R5.shadow_allowed(masks)
    ok = np.where(allowed)[0]
    sc = A5.screen(T, W, X, f, grids[A5.PORT_K], sel, log=log)

    universes = {"baseline": ok}
    admitted = {}
    for pair, _why in PAIRS:
        m = R5.shadow_allowed(masks, pair) & ~allowed
        key = "pair:" + "+".join(pair)
        admitted[key] = np.where(m)[0]
        universes[key] = np.concatenate([ok, admitted[key]])
    universes["full_legal"] = np.arange(W.shape[0], dtype=np.int64)

    short = {k: A5.top_of(sc, p, A5.CANDIDATES) for k, p in universes.items()}
    keys = list(short)
    rows = np.concatenate([short[k] for k in keys])
    bounds, pos = {}, 0
    for k in keys:
        bounds[k] = slice(pos, pos + len(short[k]))
        pos += len(short[k])
    st, lv = X4._field_pass(T, W, X, f, rows, sel)
    ports = {}
    for k in keys:
        ch, _pa = A5.greedy_on(T, grids[A5.PORT_K], st, lv, bounds[k])
        ports[k] = short[k][ch]
    del st, lv

    allrows = np.concatenate([ports[k] for k in keys])
    gb = {k: np.arange(i * A5.PORT_K, (i + 1) * A5.PORT_K) for i, k in enumerate(keys)}
    st2, lv2 = X4._field_pass(T, W, X, f, allrows, sco)
    fb = A5.football(W[allrows], X, sco)
    out = {"selection_worlds": [int(sel[0]), int(sel[-1])],
           "scoring_worlds": [int(sco[0]), int(sco[-1])],
           "folds_disjoint": bool(not (set(sel.tolist()) & set(sco.tolist()))),
           "portfolios": {}}
    base = gb["baseline"]
    for conv in ("optimistic", "pessimistic"):
        P1 = X4.topq(grids[A5.PORT_K], "top1", st2, lv2, conv)
        P01 = X4.topq(grids[A5.PORT_K], "top01", st2, lv2, conv)
        for k in keys:
            row = out["portfolios"].setdefault(k, {})
            row.setdefault("held_out", {})[conv] = {
                "top1": round(float(np.max(P1[gb[k]], axis=0).mean()), 6),
                "top01": round(float(np.max(P01[gb[k]], axis=0).mean()), 6)}
            if k != "baseline":
                row.setdefault("paired", {})[conv] = {
                    "top1": X4.paired_bootstrap(P1, P1, base, gb[k], A5.PORT_K),
                    "top01": X4.paired_bootstrap(P01, P01, base, gb[k], A5.PORT_K)}
    for k in keys:
        o = gb[k]
        out["portfolios"][k]["football_selected"] = {
            "mean": round(float(np.mean([fb["mean"][i] for i in o])), 4),
            "p99": round(float(np.mean([fb["p99"][i] for i in o])), 4)}
        out["portfolios"][k]["overlap_with_baseline"] = int(
            len(set(ports[k].tolist()) & set(ports["baseline"].tolist())))
        if k.startswith("pair:"):
            adm = set(admitted[k].tolist())
            out["portfolios"][k]["admitted"] = int(len(adm))
            out["portfolios"][k]["newly_admitted_selected"] = sum(
                1 for x in ports[k] if int(x) in adm)
    del st2, lv2
    return out


def board(feed_dir, dg, seed, log=print):
    import numpy as np

    import dfs_tourney as T
    from research import sd_board
    S = sd_board.feeds(feed_dir)
    S._cache.clear()
    slate, contest = sd_board.slate_and_contest(dg)
    ents, _s, _r = sd_board.ents_for(slate, sd_board.WEEK, S.SD_DISCRETE,
                                     n_sims=A5.WORLDS, seed=seed, log=log)
    idx, W, allowed = sd_board.universe(ents)
    masks = R5.reason_masks(ents, idx)
    mism = int((R5.shadow_allowed(masks) != allowed).sum())
    if mism:
        raise SystemExit(f"shadow classifier disagrees on {mism} lineups -- STOP")
    f, beta = T.field_weights(ents, idx, cpt_mult=1.5)
    C = int(contest.get("max_entries") or contest.get("entered") or 0) or 10000
    grids, _gm = X4.grids_by_size(contest, C)
    N = min(A5.WORLDS, min(len(e["arr"]) for e in ents))
    Xm = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    half = N // 2
    A, B = np.arange(0, half), np.arange(half, N)
    log(f"[S5P] dg {dg} seed {seed}: {len(idx):,} legal, {int(allowed.sum()):,} allowed")
    res = {}
    for name, sel, sco in (("A_select_B_score", A, B), ("B_select_A_score", B, A)):
        res[name] = one_direction(T, W, Xm, f, masks, grids, sel, sco, log=log)
        for k, p in res[name]["portfolios"].items():
            if k == "baseline":
                continue
            d = p["paired"]["optimistic"]["top1"]
            log(f"[S5P]   {name[:3]} {k:34s} d_top1 {d['point']:+.5f} "
                f"[{d['ci_lo']:+.5f},{d['ci_hi']:+.5f}] "
                f"{'STABLE' if d['sign_stable'] else 'not stable'} "
                f"newly {p.get('newly_admitted_selected', '-')}")
    return {"draft_group_id": int(dg), "seed": int(seed), "worlds": int(N),
            "classifier_mismatches": mism, "directions": res}


def run(feed_dir, dgs=A5.DGS, seeds=A5.SEEDS, log=print):
    out = {"meta": {"stage": "S5.9 pairwise rule removals",
                    "trigger": ("S5.8's full-legal gain exceeds the sum of single-rule "
                                "ablations by +0.0122 on average and +0.0341 in the worst "
                                "cell, so an interaction exists that no single ablation "
                                "can reach"),
                    "pairs": [{"pair": list(p), "why": w} for p, w in PAIRS],
                    "excluded": EXCLUDED,
                    "predeclared": ("PAIRS is closed and was written from S5.1's "
                                    "subsumption matrix before this ran, not chosen from "
                                    "its output"),
                    "untested": ("three-or-more-rule combinations. The full-legal "
                                 "counterfactual is the only evidence about them and it "
                                 "cannot attribute."),
                    "money": "CLOSED -- no payout metric appears in this stage",
                    "boards": list(dgs), "seeds": list(seeds),
                    "provenance": _prov(worlds=A5.WORLDS, model="legacy-latent-discrete",
                                        seed={"boards": list(seeds),
                                              "bootstrap": X4.BOOT_SEED})},
           "boards": {}}
    for dg in dgs:
        out["boards"][str(dg)] = {}
        for sd in seeds:
            out["boards"][str(dg)][str(sd)] = board(feed_dir, dg, sd, log=log)
            with open(os.path.join(DATA, "s5_pairs.json"), "w") as fh:
                json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "feeds")
    run(fd)
    print("S5 pairwise written")
