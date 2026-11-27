"""The candidate classic fields of research/reports/cl_field2.md, and the
harness the prospective protocol (research/reports/cl_field2_protocol.md)
runs on contests that had not locked when it was registered.

Three fields, every one built by production's own code (dfs_tourney is only
called, never edited):

  F0  the served field's replica (dk_mech.classic_replica): production's
      sampler steered onto the ownership the board stamped.
  F1  the SAME sampler steered instead onto
          T = alpha x (noisy-optimizer share on FROZEN["source"], sigma, tau)
              + (1 - alpha) x the board's stamped ownership
      -- a per-player ownership target that comes from whole lineups solved
      under the cap, not from a ranking of projection and price
      (research/reports/cl_mech.md falsified that family).
  F1c the same at alpha_c (1.0): the whole target is the optimizer share. The
      concentration candidate -- `choose` put it 0.57 pp behind F1 on the
      top-40 MAE, but only it reaches the public's sum of squared ownership.
  F2  F1 with a share w of its entries replaced by exact lineups of the same
      noisy-optimizer pool: dk_mech's cluster, on the candidate source. `fit`
      chose w = 0 (at sigma 0.25 the pool repeats nothing, so the cluster
      only cost stack accuracy): F2 is recorded as failed, not registered.

    python3 -m research.cl_field2_fields choose   --scratch DIR          # the per-player target (development)
    python3 -m research.cl_field2_fields fit      <exports> [...]        # the fields on weeks 10 and 11 (development)
    python3 -m research.cl_field2_fields validate <exports> --contest CID --dg DG --week W [...]   # once a contest

`choose` and `fit` read weeks 10 and 11 only, and BOTH are development data
(dk_mech already read week 11 as a validation contest). `validate` refuses
those two contests, refuses a contest it has already read, refuses a tree
whose protocol or harness differs from the registration, and judges with the
owner's five criteria exactly as research/dk_mix_classic.py coded them.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import cl_field2 as C         # noqa: E402
from research import dk_mech as M           # noqa: E402
from research import dk_mix_classic as MC   # noqa: E402

OUT = C.OUT
OWNER = MC.OWNER            # the owner's five criteria, dk_mech.md B.7: the same object, never a copy
DEVELOPMENT = (197645153, 197882014)
ALPHAS = (0.25, 0.5, 0.75, 1.0)
WS = (0.0, 0.05, 0.10, 0.20)
PROTOCOL = os.path.join(ROOT, "research", "reports", "cl_field2_protocol.md")
REGISTRATION = os.path.join(OUT, "protocol.json")
#: The candidates, frozen by `choose` and `fit` on the development weeks and
#: copied into research/data/cl_field2/protocol.json at registration (a guard
#: holds the two equal). Do not edit: a change is a new protocol.
FROZEN = {"source": "kalshi", "sigma": 0.25, "tau": 0.0, "alpha": 0.5, "alpha_c": 1.0, "w": 0.0, "n_pool_min": 10000}
#: The diagnostics' thresholds (protocol section 4), fixed with the candidates.
DIAG = {"score_gain_min_points": 3.0, "sum_own_sq_factor": 1.33, "top40_gain_min_pp": 0.5}


# ---- the per-player target (development) ---------------------------------------
def run_choose(args, log=print):
    """Every (source, sigma, tau, alpha) target against both weeks' real
    ownership; the cell with the lowest mean real-top-40 MAE is the
    candidate. Reads the shares `cl_field2 targets` saved."""
    rows = {}
    for week in sorted(C.WEEKS):
        z = np.load(os.path.join(args.scratch, f"cl2_shares_w{week}.npz"))
        o, base = z["real"], z["base_field_pct"]
        top40 = np.argsort(-o, kind="stable")[:40]
        rows.setdefault("served", {})[week] = {"top40_mae": 100 * float(np.abs(base[top40] - o[top40]).mean()),
                                               "sum_own_sq": float((base ** 2).sum()), "real_sum_own_sq": float((o ** 2).sum())}
        for k in z.files:
            if not k.startswith("t_"):
                continue
            for a in ALPHAS:
                T = a * z[k] + (1 - a) * base
                rows.setdefault(f"{k[2:]}|{a}", {})[week] = {"top40_mae": 100 * float(np.abs(T[top40] - o[top40]).mean()),
                                                              "sum_own_sq": float((T ** 2).sum())}
    table = []
    for k, v in rows.items():
        if len(v) == len(C.WEEKS):
            table.append({"cell": k, "mean_top40_mae": round(float(np.mean([x["top40_mae"] for x in v.values()])), 3),
                          **{f"w{w}_top40_mae": round(x["top40_mae"], 3) for w, x in v.items()},
                          **{f"w{w}_sum_own_sq": round(x["sum_own_sq"], 4) for w, x in v.items()}})
    table.sort(key=lambda r: (r["mean_top40_mae"], r["cell"]))
    out = {"role": "development (weeks 10 and 11, in sample: the cell is chosen on the same two weeks it is scored on)",
           "cells": len(table), "served": next(r for r in table if r["cell"] == "served"),
           "real_sum_own_sq": {str(w): round(rows["served"][w]["real_sum_own_sq"], 4) for w in rows["served"]},
           "best": table[0], "table": table}
    with open(os.path.join(OUT, "choose.json"), "w") as f:
        json.dump(out, f, indent=1)
    for r in table[:12]:
        log(r)
    log("served", out["served"])
    return 0


# ---- the fields -----------------------------------------------------------------
def target(c, S, n_pool, seed, workers=4, frozen=None):
    """(T at alpha, T at alpha_c, the optimizer pool)."""
    fz = frozen or FROZEN
    B = C.optimizer_pool(c, S[fz["source"]], fz["sigma"], fz["tau"], int(n_pool), seed, workers=workers)
    t = C._own(B, c.P)
    b = {p["name"]: float(p.get("field_pct") or 0.0) / 100.0 for p in c.proj_players}
    base = np.asarray([b.get(n, 0.0) for n in c.names])
    return fz["alpha"] * t + (1 - fz["alpha"]) * base, fz["alpha_c"] * t + (1 - fz["alpha_c"]) * base, B


def steered(c, T, n, seed=M.SEED, workers=4):
    """Production's sampler steered onto T (fractions per contest player)."""
    tg = [100.0 * float(T[c.nid[M.DS._norm(p["name"])]]) for p in c.board["players"]]
    A, _flex, rep = M.classic_replica(c, n, seed=seed, workers=workers, targets=tg)
    return A, rep


def with_cluster(A1, B, w, n):
    k = int(round(w * n))
    return np.concatenate([B[:k], A1[:n - k]], axis=0) if k else A1[:n]


def diagnostics(c, A, o, sc_real):
    """The study's own figures for one field (protocol section 4)."""
    s = C._own(A, c.P)
    top40 = np.argsort(-o, kind="stable")[:40]
    st = M.structure(c, A)
    sc = M.scores(c, A)
    return {"mean_real_points": round(float(sc.mean()), 2), "D_real_minus_field": round(float(sc_real.mean() - sc.mean()), 2),
            "sum_own_sq": round(float((s ** 2).sum()), 4), "top_owned_pct": round(100 * float(s.max()), 1),
            "top40_mae": round(100 * float(np.abs(s[top40] - o[top40]).mean()), 3),
            "at_cap_pct": st["salary_at_cap_pct"], "salary_mean": st["salary_mean"],
            "naked_qb_pct": st["stack_pct"][0], "bring_back_pct": st["bring_back_pct"]}


def judge(c, fields, log=print):
    """Every field on the owner's five criteria (dk_mix_classic's code and
    thresholds) and the diagnostics, the served replica F0 as production."""
    R = MC.real_side(c)
    dup, _per, _inv = M.duplication(c.A, "classic", c.P)
    o, sc_real = C._own(c.A, c.P), M.scores(c, c.A)
    realC = R["near"]["pair_share_overlap"]["9_of_9"]
    o_real = MC.owner_side(c, c.A, dup["_top_rows"])
    base = MC.measure(c, fields["F0"], R)
    o_base = MC.owner_side(c, fields["F0"], dup["_top_rows"])
    d0 = diagnostics(c, fields["F0"], o, sc_real)
    out = {"entries": int(c.N), "real": {"C": realC, "sum_own_sq": round(float((o ** 2).sum()), 4),
                                         "mean_real_points": round(float(sc_real.mean()), 2), "owner_side": o_real,
                                         "salary_mean": R["salary_mean"], "stack_pct": R["stack"],
                                         "at_cap_pct": R["st"]["salary_at_cap_pct"]},
           "fields": {}}
    for name, A in fields.items():
        m = MC.measure(c, A, R)
        os_ = MC.owner_side(c, A, dup["_top_rows"])
        ratio = m["C"] / realC
        v, det = MC.owner_verdict(os_, o_base, o_real, m, base, ratio)
        d = diagnostics(c, A, o, sc_real)
        f = DIAG["sum_own_sq_factor"]
        dv = {"D1_score_gap_closes_3_points": bool(abs(d["D_real_minus_field"]) <= abs(d0["D_real_minus_field"]) - DIAG["score_gain_min_points"]),
              "D2_sum_own_sq_within_1.33x": bool(1 / f <= d["sum_own_sq"] / out["real"]["sum_own_sq"] <= f),
              "D3_top40_mae_better_by_0.5pp": bool(d["top40_mae"] <= d0["top40_mae"] - DIAG["top40_gain_min_pp"])}
        dv["all"] = bool(all(dv.values()))
        out["fields"][name] = {"measure": m, "collision_over_real": round(ratio, 5), "owner_side": os_,
                               "owner_verdict": v, "owner_detail": det, "diagnostics": d, "diagnostic_verdict": dv}
        log(f"[cl2f] {name}: C/real {ratio:.4f} top40 MAE {d['top40_mae']} sum own^2 {d['sum_own_sq']} "
            f"D {d['D_real_minus_field']} owner all {v['all']} diag all {dv['all']}")
    return out, R, base


def _args_for(args, week):
    a = argparse.Namespace(**vars(args))
    a.weeks = [week]
    return a


def run_fit(args, log=print):
    """F0, F1 and F2 at every w on the two development weeks; w is the one
    minimising dk_mech B.6's seven-error loss summed over both weeks."""
    fz = dict(FROZEN)
    assert fz["source"], "run `choose` and freeze the target first"
    res, losses = {}, {w: 0.0 for w in WS}
    for week in sorted(C.WEEKS):
        t0 = time.time()
        c, S, _info = C.load(week, args)
        n_pool = max(fz["n_pool_min"], int(round(max(WS) * c.N)))
        T, _Tc, B = target(c, S, n_pool, week, workers=args.workers, frozen=fz)
        A0, _f, _rep = M.classic_replica(c, c.N, workers=args.workers)
        A1, rep1 = steered(c, T, c.N, workers=args.workers)
        fields = {"F0": A0, "F1": A1}
        for w in WS[1:]:
            fields[f"F2_w{w}"] = with_cluster(A1, B, w, c.N)
        out, R, base = judge(c, fields, log=log)
        for w in WS:
            L, parts = MC.loss(out["fields"]["F1" if w == 0 else f"F2_w{w}"]["measure"], R, base)
            out["fields"]["F1" if w == 0 else f"F2_w{w}"]["b6_loss"] = {"loss": L, "parts": parts}
            losses[w] += L
        o = C._own(c.A, c.P)
        top = np.argsort(-o)[:15]
        s0, s1 = C._own(A0, c.P), C._own(A1, c.P)
        out.update({"week": week, "role": "development", "frozen": fz, "steer": rep1, "pool": int(len(B)),
                    "target_sum_own_sq": round(float((T ** 2).sum()), 4),
                    "top15_real_owned": [{"name": c.names[i], "real_pct": round(100 * o[i], 1), "served_pct": round(100 * s0[i], 1),
                                          "target_pct": round(100 * T[i], 1), "F1_pct": round(100 * s1[i], 1)} for i in top],
                    "seconds": round(time.time() - t0)})
        res[week] = out
        with open(os.path.join(OUT, f"fit_w{week}.json"), "w") as f:
            json.dump(out, f, indent=1)
    best = min(WS, key=lambda w: (losses[w], w))
    with open(os.path.join(OUT, "fit.json"), "w") as f:
        json.dump({"role": "development", "b6_loss_sum_over_weeks": {str(w): round(v, 4) for w, v in losses.items()},
                   "chosen_w": best, "frozen_target": fz}, f, indent=1)
    log(f"[cl2f] loss by w {losses}; chosen w {best}")
    return 0


def run_fit_c(args, log=print):
    """F1c (the target at alpha_c) on the two development weeks: the
    concentration candidate, run after `fit` chose w = 0."""
    fz = dict(FROZEN)
    for week in sorted(C.WEEKS):
        c, S, _info = C.load(week, args)
        _T, Tc, _B = target(c, S, fz["n_pool_min"], week, workers=args.workers, frozen=fz)
        A0, _f, _rep = M.classic_replica(c, c.N, workers=args.workers)
        A1c, rep = steered(c, Tc, c.N, workers=args.workers)
        out, _R, _base = judge(c, {"F0": A0, "F1c": A1c}, log=log)
        o, s1 = C._own(c.A, c.P), C._own(A1c, c.P)
        top = np.argsort(-o)[:15]
        out.update({"week": week, "role": "development", "frozen": fz, "steer": rep,
                    "target_sum_own_sq": round(float((Tc ** 2).sum()), 4),
                    "top15_real_owned": [{"name": c.names[i], "real_pct": round(100 * o[i], 1), "target_pct": round(100 * Tc[i], 1),
                                          "F1c_pct": round(100 * s1[i], 1)} for i in top]})
        with open(os.path.join(OUT, f"fit_c_w{week}.json"), "w") as f:
            json.dump(out, f, indent=1)
    return 0


# ---- the prospective read ----------------------------------------------------------
def _registered():
    with open(REGISTRATION) as f:
        R = json.load(f)
    with open(PROTOCOL, "rb") as f:
        h = hashlib.sha256(f.read()).hexdigest()
    if R.get("protocol_sha256") != h:
        raise SystemExit("the protocol file is not the one that was registered (sha256 differs): refusing to read a contest")
    if R.get("frozen") != FROZEN:
        raise SystemExit("FROZEN differs from the registration: refusing to read a contest")
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "research/cl_field2.py", "research/cl_field2_fields.py",
                            "research/dk_mech.py", "research/dk_mix_classic.py", "research/reports/cl_field2_protocol.md",
                            "dfs_tourney.py", "dk_standings.py"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        raise SystemExit("uncommitted changes in the harness or the sampler: refusing to read a contest\n" + dirty)
    return R


def run_validate(args, log=print):
    cid, dg, week = int(args.contest), int(args.dg), int(args.week)
    if cid in DEVELOPMENT or cid in M.CONTESTS:
        raise SystemExit(f"contest {cid} is DEVELOPMENT data (read by dk_mech or by this study): it can validate nothing")
    R = _registered()
    path = os.path.join(OUT, f"validation_{cid}.json")
    if os.path.exists(path):
        raise SystemExit(f"{path} exists: a validation contest is read once")
    M.CONTESTS[cid] = {"kind": "classic", "dg": dg, "label": f"week {week} Millionaire", "role": "validation (cl_field2 protocol)"}
    C.WEEKS[week] = (cid, dg)
    c, S, info = C.load(week, _args_for(args, week))
    if c.board is None:
        raise SystemExit(f"no served board for draft group {dg} on the data branch: nothing to compare against")
    n_pool = max(FROZEN["n_pool_min"], int(round(FROZEN["w"] * c.N)))
    T, Tc, B = target(c, S, n_pool, week, workers=args.workers)
    A0, _f, _rep = M.classic_replica(c, c.N, workers=args.workers)
    A1, rep1 = steered(c, T, c.N, workers=args.workers)
    A1c, _rep1c = steered(c, Tc, c.N, workers=args.workers)
    fields = {"F0": A0, "F1": A1, "F1c": A1c}
    if FROZEN["w"]:
        fields["F2"] = with_cluster(A1, B, FROZEN["w"], c.N)
    out, _R, _base = judge(c, fields, log=log)
    out.update({"contest": cid, "draft_group": dg, "week": week, "role": "validation (prospective; read once)",
                "frozen": FROZEN, "protocol_sha256": R["protocol_sha256"], "sources": info, "steer": rep1,
                "board_built_ts": c.board.get("built_ts"), "primary": "F1c", "secondary": "F1", "diag_thresholds": DIAG,
                "read_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    log(f"[cl2f] wrote {path}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=("choose", "fit", "fit_c", "validate"))
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--boards", default=None)
    ap.add_argument("--props", default=None)
    ap.add_argument("--scratch", default=os.environ.get("TMPDIR", "/tmp"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--contest")
    ap.add_argument("--dg")
    ap.add_argument("--week")
    args = ap.parse_args()
    args.weeks = sorted(C.WEEKS)
    return {"choose": run_choose, "fit": run_fit, "fit_c": run_fit_c, "validate": run_validate}[args.mode](args)


if __name__ == "__main__":
    sys.exit(main())
