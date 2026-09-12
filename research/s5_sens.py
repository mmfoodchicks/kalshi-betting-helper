"""S5.10 -- field-concentration sensitivity, for candidate-harmful rules only.

S4 learned that the size of a Top-q effect rides on `beta`, the one knob of a
placeholder ownership model taken from a single published 2021 contest. A rule
recommendation that only survives at that one arbitrary value is not a
recommendation.

So this re-runs the ablation at three concentrations on the SAME football
worlds. It is deliberately NOT run for every rule: six of the nine admit nothing
at all when switched off alone, and a sweep over rules that provably cannot
change the portfolio would be three times the compute for three times zero.
Only rules that looked materially harmful under production beta are swept, and
which those are is decided by s5_ablate's output, not in advance.

SCENARIOS ONLY. No value here may become a production default and none of them
is a calibrated alternative -- the gate link `field_model_calibrated` stays open
whatever this says.
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")

from research import s4_crossfit as X4      # noqa: E402
from research import s5_ablate as A5        # noqa: E402
from research import s5_rules as R5         # noqa: E402

TOP_SHARES = (0.001, 0.002, 0.004)          # flatter, production, concentrated
SENS_SEED = A5.SEEDS[0]


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def candidate_rules(ablate_json, min_delta=0.005):
    """The rules worth sweeping: any rule whose removal moved held-out Top 1%
    by at least `min_delta` in at least one cell, at either exact convention.

    A deliberately loose screen. It decides what gets MORE scrutiny, never what
    gets recommended, so erring wide costs compute and erring narrow could hide
    the finding.
    """
    hits = {}
    for dg, byseed in ablate_json["boards"].items():
        for sd, b in byseed.items():
            for dname, d in b["directions"].items():
                base = d["portfolios"]["baseline"]["held_out"]
                for k, p in d["portfolios"].items():
                    if not k.startswith("ablate:"):
                        continue
                    rid = k.split(":", 1)[1]
                    for conv in ("optimistic", "pessimistic"):
                        dd = p["held_out"][conv]["top1"] - base[conv]["top1"]
                        if abs(dd) >= min_delta:
                            hits.setdefault(rid, []).append(
                                {"board": dg, "seed": sd, "direction": dname,
                                 "convention": conv, "delta_top1": round(dd, 6)})
    return hits


def sweep(feed_dir, dg, rules, seed=SENS_SEED, log=print):
    import dfs_tourney as T
    from research import sd_board
    S = sd_board.feeds(feed_dir)
    S._cache.clear()
    slate, contest = sd_board.slate_and_contest(dg)
    ents, _s, _r = sd_board.ents_for(slate, sd_board.WEEK, S.SD_DISCRETE,
                                     n_sims=A5.WORLDS, seed=seed,
                                     log=lambda *_a, **_k: None)
    idx, W, allowed = sd_board.universe(ents)
    masks = R5.reason_masks(ents, idx)
    if int((R5.shadow_allowed(masks) != allowed).sum()):
        raise SystemExit("shadow classifier disagrees with production -- STOP")
    C = int(contest.get("max_entries") or contest.get("entered") or 0) or 10000
    grids, _gm = X4.grids_by_size(contest, C)
    N = min(A5.WORLDS, min(len(e["arr"]) for e in ents))
    Xm = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    half = N // 2
    A, B = np.arange(0, half), np.arange(half, N)
    ok = np.where(allowed)[0]
    out = {}
    for ts in TOP_SHARES:
        f, beta = T.field_weights(ents, idx, cpt_mult=1.5, top_share=ts)
        cells = {}
        for name, sel, sco in (("A_select_B_score", A, B), ("B_select_A_score", B, A)):
            sc = A5.screen(T, W, Xm, f, grids[A5.PORT_K], sel,
                           log=lambda *_a, **_k: None)
            universes = {"baseline": ok}
            for rid in rules:
                m = R5.shadow_allowed(masks, (rid,)) & ~allowed
                if m.any():
                    universes[f"ablate:{rid}"] = np.concatenate([ok, np.where(m)[0]])
            universes["full_legal"] = np.arange(W.shape[0], dtype=np.int64)
            short = {k: A5.top_of(sc, p, A5.CANDIDATES) for k, p in universes.items()}
            keys = list(short)
            rows = np.concatenate([short[k] for k in keys])
            bounds, pos = {}, 0
            for k in keys:
                bounds[k] = slice(pos, pos + len(short[k]))
                pos += len(short[k])
            st, lv = X4._field_pass(T, W, Xm, f, rows, sel)
            ports = {}
            for k in keys:
                ch, _pa = A5.greedy_on(T, grids[A5.PORT_K], st, lv, bounds[k])
                ports[k] = short[k][ch]
            del st, lv
            allrows = np.concatenate([ports[k] for k in keys])
            gb = {k: np.arange(i * A5.PORT_K, (i + 1) * A5.PORT_K)
                  for i, k in enumerate(keys)}
            st2, lv2 = X4._field_pass(T, W, Xm, f, allrows, sco)
            cell = {}
            for conv in ("optimistic", "pessimistic"):
                P1 = X4.topq(grids[A5.PORT_K], "top1", st2, lv2, conv)
                P01 = X4.topq(grids[A5.PORT_K], "top01", st2, lv2, conv)
                base = gb["baseline"]
                for k in keys:
                    if k == "baseline":
                        continue
                    cell.setdefault(k, {})[conv] = {
                        "top1": X4.paired_bootstrap(P1, P1, base, gb[k], A5.PORT_K),
                        "top01": X4.paired_bootstrap(P01, P01, base, gb[k], A5.PORT_K)}
            cells[name] = cell
            del st2, lv2
        out[str(ts)] = {"top_share": ts, "beta": round(float(beta), 4),
                        "scenario": ("flatter field" if ts < 0.002 else
                                     "production" if ts == 0.002 else "more concentrated"),
                        "directions": cells}
        for name, cell in cells.items():
            for k, byconv in cell.items():
                d = byconv["optimistic"]["top1"]
                log(f"[S5S] ts {ts:.3f} beta {beta:.4f} {name[:3]} {k:20s} "
                    f"top1 delta {d['point']:+.5f} "
                    f"[{d['ci_lo']:+.5f},{d['ci_hi']:+.5f}] "
                    f"{'STABLE' if d['sign_stable'] else 'not stable'}")
    return {"draft_group_id": int(dg), "seed": int(seed), "worlds": int(N),
            "rules_swept": list(rules),
            "note": ("sensitivity scenarios only; no value here may become a production "
                     "default, and none of them is a calibrated alternative"),
            "by_top_share": out}


def run(feed_dir, dg=A5.DGS[0], log=print):
    ab = json.load(open(os.path.join(DATA, "s5_ablate.json")))
    cand = candidate_rules(ab)
    rules = sorted(cand)
    log(f"[S5S] candidate-harmful rules from s5_ablate: {rules or '(none)'}")
    out = {"meta": {"stage": "S5.10 field-concentration sensitivity",
                    "screen": ("rules whose removal moved held-out Top 1% by >= 0.005 in "
                               "at least one cell at either exact convention"),
                    "candidates": cand,
                    "money": "CLOSED -- no payout metric appears in this stage",
                    "provenance": _prov(worlds=A5.WORLDS, model="legacy-latent-discrete",
                                        seed=SENS_SEED)},
           "sweep": sweep(feed_dir, dg, rules, log=log) if rules else None}
    with open(os.path.join(DATA, "s5_sens.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "feeds")
    dgx = int(sys.argv[2]) if len(sys.argv) > 2 else A5.DGS[0]
    run(fd, dgx)
    print("S5 sensitivity written")
