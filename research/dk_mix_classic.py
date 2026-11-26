"""The classic noisy-optimizer cluster: one development week, one validation
week (2026-11-26, Phase B.6). Pre-registered in research/reports/dk_mech.md
section B.6, committed before this module was run on any contest.

The mechanism, the one research/reports/cl_own.md proposed and nobody built:
an entry is, with probability w, the EXACT optimum (DraftKings' rules only;
production's dfs_tourney.optimal_lineups) of the board's projections each
multiplied by 1 + sigma x a standard normal drawn per player per entry -- a
public optimizer run with "randomness" sigma -- and otherwise a draw of the
served optimizer field (dk_mech.classic_replica). Two parameters.

It is NOT a per-player ranking (research/reports/cl_mech.md falsified that
family): every optimizer entry is a whole lineup solved under the cap, so
the chalk, the salary and the duplicates all come from one construction.

    python3 -m research.dk_mix_classic fit <exports...> [--boards DIR]       # week 10 only
    python3 -m research.dk_mix_classic validate <exports...> [--boards DIR]  # week 11, once
"""
import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import dk_mech as M      # noqa: E402

DEV, VAL = 197645153, 197882014
SIGMAS = (0.03, 0.06, 0.10, 0.15, 0.20, 0.30)
WEIGHTS = tuple(round(0.1 * i, 1) for i in range(11))
CELL_N = 60000
FIT = os.path.join(M.OUT, "mix_classic_fit.json")
VALID = os.path.join(M.OUT, "mix_classic_validation.json")
ACCEPT = {"collision_factor": 2.0, "copies_factor": 3.0, "copies_min_of_20": 10,
          "margins": {"own_top40_mae": 0.25, "joint_mae": 0.10, "salary_mean_abs_err": 50.0, "stack_l1": 2.0, "score_w1": 2.0}}


def optimizer_pool(c, sigma, n, seed, workers=4):
    """n optimizer entries at randomness sigma, in the contest's player ids."""
    import dfs_tourney as T
    P = [p for p in c.proj_players if p["name"] in c.nid and float(p.get("proj") or 0.0) > 0 and int(p["salary"]) > 0]
    proj = np.asarray([float(p["proj"]) for p in P], dtype=np.float64)
    rng = np.random.default_rng((M.SEED, int(round(1000 * sigma)), seed))
    X = (proj[:, None] * (1.0 + sigma * rng.standard_normal((len(P), n)))).astype(np.float32)
    lineups, _v = T.optimal_lineups(X, [int(p["salary"]) for p in P], [p["pos"] for p in P], workers=workers)
    m = np.asarray([c.nid[p["name"]] for p in P], dtype=np.int32)
    return np.sort(m[np.asarray([lu for lu in lineups if lu], dtype=np.int64)], axis=1)


def real_side(c):
    own, _c = M.ownership(c.A, "classic", c.P)
    top40 = np.argsort(-own, kind="stable")[:40]
    jid = np.argsort(-own, kind="stable")[:30].tolist()
    st = M.structure(c, c.A)
    return {"own": own, "top40": top40, "jid": jid, "J": M.joint(c.A, c.P, jid), "near": M.near(c.A, "classic", c.P),
            "salary_mean": st["salary_mean"], "stack": st["stack_pct"], "st": st,
            "score_q": np.quantile(M.scores(c, c.A), np.linspace(0.0005, 0.9995, 1000))}


def measure(c, A, R):
    """One model field against the real one: the seven fitted errors' raw
    values, plus the score distance (validation only)."""
    own, _c = M.ownership(A, "classic", c.P)
    nr = M.near(A, "classic", c.P)["pair_share_overlap"]
    st = M.structure(c, A)
    iu = np.triu_indices(len(R["jid"]), 1)
    J = M.joint(A, c.P, R["jid"])
    sq = np.quantile(M.scores(c, A), np.linspace(0.0005, 0.9995, 1000))
    return {"C": nr["9_of_9"], "P8": nr["8_of_9"], "P7": nr["7_of_9"],
            "own_top40_mae": round(100 * float(np.abs(own[R["top40"]] - R["own"][R["top40"]]).mean()), 3),
            "joint_mae": round(100 * float(np.abs(J[iu] - R["J"][iu]).mean()), 3),
            "salary_mean": st["salary_mean"], "salary_mean_abs_err": round(abs(st["salary_mean"] - R["salary_mean"]), 1),
            "stack_pct": st["stack_pct"], "stack_l1": round(float(sum(abs(a - b) for a, b in zip(st["stack_pct"], R["stack"]))), 2),
            "salary_at_cap_pct": st["salary_at_cap_pct"], "bring_back_pct": st["bring_back_pct"],
            "mean_overlap": round(M.mean_overlap(A, "classic", c.P), 4),
            "score_w1": round(float(np.abs(sq - R["score_q"]).mean()), 2),
            "entries_in_duplicated_lineups_pct": round(100 * float((M.duplication(A, "classic", c.P)[1] > 1).mean()), 3)}


def loss(m, R, base):
    """The pre-registered sum of seven normalised errors (section B.6)."""
    rn = R["near"]["pair_share_overlap"]

    def l2(a, b):
        return abs(np.log2(max(a, 1e-12) / b))
    parts = {"C": l2(m["C"], rn["9_of_9"]), "P8": l2(m["P8"], rn["8_of_9"]), "P7": l2(m["P7"], rn["7_of_9"]),
             "own_top40_mae": m["own_top40_mae"] / max(base["own_top40_mae"], 1e-9),
             "joint_mae": m["joint_mae"] / max(base["joint_mae"], 1e-9),
             "salary_mean_abs_err": m["salary_mean_abs_err"] / max(base["salary_mean_abs_err"], 1e-9),
             "stack_l1": m["stack_l1"] / max(base["stack_l1"], 1e-9)}
    return round(float(sum(parts.values())), 4), {k: round(float(v), 4) for k, v in parts.items()}


def mix(opt, cas, w, n):
    k = int(round(w * n))
    return np.concatenate([opt[:k], cas[:n - k]], axis=0)


def run_fit(args, log=print):
    ex = M.find_exports(args.paths)
    t0 = time.time()
    c = M.load(DEV, ex[DEV], args.boards, log=lambda *_a: None)
    R = real_side(c)
    cas, _flex, rep = M.classic_replica(c, CELL_N)
    base = measure(c, cas, R)
    log(f"[mixc] served field replica: C {base['C']:.2e} (real {R['near']['pair_share_overlap']['9_of_9']:.2e})")
    cells = []
    for s in SIGMAS:
        opt = optimizer_pool(c, s, CELL_N, 0)
        log(f"[mixc] sigma {s}: {len(opt):,} optimizer entries ({time.time() - t0:.0f}s)")
        for w in WEIGHTS:
            m = measure(c, mix(opt, cas, w, CELL_N), R)
            L, parts = loss(m, R, base)
            cells.append({"sigma": s, "w": w, "loss": L, "parts": parts, **m})
    best = min(cells, key=lambda r: (r["loss"], r["sigma"], r["w"]))
    out = {"development": DEV, "cell_n": CELL_N, "real": {"C": R["near"]["pair_share_overlap"]["9_of_9"],
                                                           "P8": R["near"]["pair_share_overlap"]["8_of_9"],
                                                           "P7": R["near"]["pair_share_overlap"]["7_of_9"],
                                                           "salary_mean": R["salary_mean"], "stack_pct": R["stack"],
                                                           "mean_overlap": round(M.mean_overlap(c.A, "classic", c.P), 4)},
           "served_replica": dict(base, replica=rep), "chosen": {"sigma": best["sigma"], "w": best["w"], "loss": best["loss"]},
           "cells": cells, "accept": ACCEPT, "seconds": round(time.time() - t0)}
    with open(FIT, "w") as f:
        json.dump(out, f, indent=1)
    log(f"[mixc] chosen sigma {best['sigma']} w {best['w']} (loss {best['loss']}); wrote {FIT}")
    return out


def run_validate(args, log=print):
    with open(FIT) as f:
        fit = json.load(f)
    if os.path.exists(VALID) and not args.again:
        raise SystemExit(f"{VALID} exists: the validation contest is read once (--again re-runs the SAME frozen cell)")
    ex = M.find_exports(args.paths)
    s, w = fit["chosen"]["sigma"], fit["chosen"]["w"]
    c = M.load(VAL, ex[VAL], args.boards, log=lambda *_a: None)
    R = real_side(c)
    cas, _flex, rep = M.classic_replica(c, c.N)
    base = measure(c, cas, R)
    opt = optimizer_pool(c, s, int(round(w * c.N)), 1) if w > 0 else cas[:0]
    A = mix(opt, cas, w, c.N) if w > 0 else cas
    m = measure(c, A, R)
    # the individual builds: the real 20 most-copied, their copies in the model field
    dup, _per, _inv = M.duplication(c.A, "classic", c.P)
    rf, _rc = M._hash_words(c.P)
    hm = rf[A].sum(axis=1, dtype=np.uint64)
    hb = rf[cas].sum(axis=1, dtype=np.uint64)
    rows = []
    for row, copies in dup["_top_rows"]:
        h = rf[np.asarray(row)].sum(dtype=np.uint64)
        rows.append({"lineup": [c.names[i] for i in row], "real_copies": copies, "model_copies": int((hm == h).sum()),
                     "served_copies": int((hb == h).sum())})
    f3 = ACCEPT["copies_factor"]
    within = sum(1 for r in rows if r["model_copies"] and 1 / f3 <= r["model_copies"] / r["real_copies"] <= f3)
    ratio = m["C"] / R["near"]["pair_share_overlap"]["9_of_9"]
    v = {"A1_aggregate_collision_within_2x": bool(0.5 <= ratio <= 2.0),
         "A2_real_top20_within_3x": bool(within >= ACCEPT["copies_min_of_20"])}
    for k, mg in ACCEPT["margins"].items():
        v[f"A4_{k}_non_inferior"] = bool(m[k] <= base[k] + mg)
    v["duplication_calibrated_classic_one_week"] = bool(all(v.values()))
    out = {"validation": VAL, "chosen": fit["chosen"], "entries": c.N,
           "real": {"C": R["near"]["pair_share_overlap"]["9_of_9"], "P8": R["near"]["pair_share_overlap"]["8_of_9"],
                    "P7": R["near"]["pair_share_overlap"]["7_of_9"], "salary_mean": R["salary_mean"], "stack_pct": R["stack"],
                    "mean_overlap": round(M.mean_overlap(c.A, "classic", c.P), 4),
                    "entries_in_duplicated_lineups_pct": dup["entries_in_duplicated_lineups_pct"]},
           "served_replica": dict(base, replica=rep), "model": m, "collision_model_over_real": round(ratio, 4),
           "real_top20_builds": rows, "real_top20_within_factor": within, "verdict": v}
    with open(VALID, "w") as f:
        json.dump(out, f, indent=1)
    log(f"[mixc] validation: C model/real {ratio:.3f}; {within} of 20 builds within 3x; wrote {VALID}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=("fit", "validate"))
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--boards", default=None)
    ap.add_argument("--again", action="store_true")
    args = ap.parse_args()
    (run_fit if args.mode == "fit" else run_validate)(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
