"""Task 8, third artifact: the current classic field family's TRADE-OFF
SURFACE on the pinned week-1 pool. Research only; nothing is fitted to the
real field, which only grades; no production change.

The second artifact showed the served sampler misses the public where
tournament economics are decided, and offered one reading of the mechanism:
the sampler's one price coefficient does two conflicting jobs. In
classic_sample a player's pull is

    logit = beta * (proj - kappa * salary / 1000)

and calibrate_field moves kappa to land the lineup's MEAN SALARY. On this
pool it drove kappa negative (-1.88 to -2.23 across seeds), which makes
expensive players individually more attractive in order to spend more; the
public instead takes cheap value chalk and spends the savings elsewhere. Two
behaviours, one knob. Before any replacement family is proposed, this
artifact measures whether the CURRENT family has a region that gets both.

Preregistered arms (written before any run; not chosen after looking):

  served          beta 0.194 / kappa -1.9844 exactly as the served build
                  calibrated them; the reference (artifact 2's arm)
  kappa grid      kappa in (0, 0.5, 1.0, 2.0) points per $1,000 (positive =
                  the cheaper player per projected point preferred; 0 = no
                  price preference), with BETA CALIBRATED TO THE OWNERSHIP
                  TARGET ALONE (CL_FIELD_MAX_OWN, the existing 0.38, by
                  bisection on 20,000-draw samples) and kappa never used to
                  chase salary
  DK-complete FLEX each of the five above with a tight end eligible at FLEX,
                  which DraftKings allows and the sampler never does: three
                  substitutions in the sampler's own source (the FLEX
                  eligibility columns, the FLEX salary floor, and the
                  overflow of a tight-end pick into FLEX once the TE slot is
                  full), everything else byte-identical to production

Ten arms, one seed each at the real field's size (831,028 draws). Artifact 2
measured the seed noise of this family at 0.02 pp of per-player MAE and
0.004 of top-50 Spearman; the arm effects asked about here are ten to a
hundred times that, so one seed per arm is enough to see the surface and is
stated as such. Every arm is graded with the first artifact's own function
(research.cl_field.empirical), the second artifact's ownership comparison and
diagnostics, and production's own receipts (field_stats, _collision).

The question: can correcting legal FLEX support and removing the
expensive-player pressure improve chalk ordering without recreating the
salary failure somewhere else?

    python3 -m research.cl_mech arm <index> <scratch dir>    # one arm, its partial
    python3 -m research.cl_mech assemble <scratch dir>      # the artifact from the partials
"""
import hashlib
import inspect
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import cl_field as C  # noqa: E402
from research import cl_sampler as S  # noqa: E402

DATA = C.DATA
SEED = 20260913
FIELD_N = S.FIELD_N
CAL_N = 20000
CHUNK = 100000
KAPPA_GRID = (0.0, 0.5, 1.0, 2.0)
ARMS = tuple({"kappa": k, "flex_te": f} for f in (False, True) for k in ("served",) + KAPPA_GRID)
BETA_BRACKET = (0.0, 3.0)
BETA_STEPS = 12

# The three substitutions that make a tight end eligible at FLEX. Each must
# occur exactly once in production's source, or the variant refuses to build.
FLEX_TE_SUBSTITUTIONS = (
    ('                cols = (pc == 1) | (pc == 2)\n                room = need["FLEX"] > 0',
     '                cols = (pc == 1) | (pc == 2) | (pc == 3)\n                room = need["FLEX"] > 0'),
    ('        rel["FLEX"] = np.minimum(nxt["RB"], nxt["WR"])',
     '        rel["FLEX"] = np.minimum(np.minimum(nxt["RB"], nxt["WR"]), nxt["TE"])'),
    ('            if kind in ("RB", "WR"):\n                room = room | (need["FLEX"] > 0)',
     '            if kind in ("RB", "WR", "TE"):\n                room = room | (need["FLEX"] > 0)'),
)


def flex_te_sampler():
    """classic_sample with a tight end eligible at FLEX, built from
    production's own source by the three substitutions above and executed in
    production's namespace, so everything else is production's by
    construction. Returns (function, receipt)."""
    import dfs_tourney as T
    src = inspect.getsource(T.classic_sample)
    for old, new in FLEX_TE_SUBSTITUTIONS:
        if src.count(old) != 1:
            raise SystemExit(f"the FLEX substitution target occurs {src.count(old)} times, not once; refusing to build the variant:\n{old}")
        src = src.replace(old, new)
    src = src.replace("def classic_sample(", "def classic_sample_flex_te(", 1)
    ns = dict(vars(T))
    exec(compile(src, "<classic_sample_flex_te>", "exec"), ns)
    return ns["classic_sample_flex_te"], {"base": "dfs_tourney.classic_sample", "base_sha256": hashlib.sha256(inspect.getsource(T.classic_sample).encode()).hexdigest(),
                                          "substitutions": [{"from": o, "to": n} for o, n in FLEX_TE_SUBSTITUTIONS],
                                          "variant_sha256": hashlib.sha256(src.encode()).hexdigest()}


def calibrate_beta(sample, ents, rng, kappa, max_own, n=CAL_N, bracket=BETA_BRACKET, steps=BETA_STEPS):
    """beta so the most-owned player holds `max_own` of a fresh n-draw sample
    at this fixed kappa: bisection over the bracket (ownership rises with
    beta). kappa is never moved; the salary target is not a target here."""
    P = len(ents)
    lo, hi = bracket
    trace = []
    for _ in range(steps):
        beta = 0.5 * (lo + hi)
        idx = sample(ents, n, rng, beta, kappa, report={})
        own = float((np.bincount(idx.ravel(), minlength=P) / max(1, len(idx))).max()) if len(idx) else 0.0
        trace.append({"beta": round(beta, 5), "max_own": round(own, 5)})
        if own > max_own:
            hi = beta
        else:
            lo = beta
    beta = 0.5 * (lo + hi)
    idx = sample(ents, n, rng, beta, kappa, report={})
    own = float((np.bincount(idx.ravel(), minlength=P) / max(1, len(idx))).max())
    return beta, {"target_max_own": max_own, "achieved_on_final_draw": round(own, 5), "n": n, "bracket": list(bracket), "steps": steps, "trace": trace}


def run_arm(i, outdir, log=print):
    import dfs_tourney as T
    from research import provenance
    arm = ARMS[i]
    d, man = S.served_board()
    ents = S.served_pool(d)
    pool_g = S.grading_pool(ents)
    real = S.real_side()
    real_own = real["empirical"]["ownership_any_slot_pct"]
    sample, receipt = (flex_te_sampler() if arm["flex_te"] else (T.classic_sample, {"base": "dfs_tourney.classic_sample", "substitutions": []}))
    rng = np.random.default_rng(SEED)
    t0 = time.time()
    if arm["kappa"] == "served":
        beta, kappa = float(d["field_model"]["beta"]), float(d["field_model"]["kappa"])
        cal = {"how": "the served build's own calibration (calibrate_field: kappa to the salary target, beta to the ownership target)", "beta": beta, "kappa": kappa}
    else:
        kappa = float(arm["kappa"])
        beta, cal = calibrate_beta(sample, ents, rng, kappa, T.CL_FIELD_MAX_OWN)
        cal["how"] = "beta by bisection to CL_FIELD_MAX_OWN alone at this fixed kappa; the salary target is dropped"
    t1 = time.time()
    log(f"[MECH] arm {i} kappa {arm['kappa']} flex_te {arm['flex_te']}: beta {beta:.4f} kappa {kappa:.4f} ({t1 - t0:.0f}s); sampling {FIELD_N:,}...")
    parts, done = [], 0
    while done < FIELD_N:
        k = min(CHUNK, FIELD_N - done)
        rep = {}
        idx = sample(ents, k, rng, beta, kappa, report=rep)
        T.check_completion(rep, f"mech arm {i} chunk at {done:,}")
        parts.append(idx)
        done += len(idx)
    idx = np.concatenate(parts, axis=0)
    t2 = time.time()
    emp, own, receipts = S.grade(idx, ents, pool_g)
    out = {"arm": i, "kappa": arm["kappa"], "flex_te": arm["flex_te"], "beta": round(beta, 6), "kappa_used": round(kappa, 6), "seed": SEED,
           "calibration": cal, "sampler": receipt, "sample": {"n": int(len(idx)), "chunk": CHUNK, "seconds": round(t2 - t1, 1)},
           "empirical": emp, "ownership_any_slot_pct": own, "production_receipts": receipts,
           "vs_real": S.compare_ownership(own, real_own, ents), "diagnostics": S.ownership_diagnostics(own, real_own, ents),
           "provenance": provenance.stamp(worlds=FIELD_N, model=f"classic_sample{'_flex_te' if arm['flex_te'] else ''}, kappa {arm['kappa']}, beta {'served' if arm['kappa'] == 'served' else 'calibrated to the ownership target alone'}", seed=SEED)}
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"cl_mech_arm_{i}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    v = out["vs_real"]
    log(f"[MECH] arm {i}: top-50 Spearman {v['top50_by_real']['spearman']}, top-20 bias {v['top20_by_real']['bias_pp']}, MAE {v['mae_pp']}; "
        f"salary ${emp['salary_used']['mean']:,.0f}, at cap {emp['salary_used']['share_at_cap_pct']}%, FLEX {emp['flex_position_pct']}, pairs {emp['collision']['distinct_pairs']:.2e}; written {path}")
    return out


def _row(p, real):
    e, v, g = p["empirical"], p["vs_real"], p["diagnostics"]
    return {"arm": p["arm"], "kappa": p["kappa"], "flex_te": p["flex_te"], "beta": p["beta"], "kappa_used": p["kappa_used"],
            "max_own_pct": e["max_ownership"]["any_slot_pct"], "max_own_player": e["max_ownership"]["player"],
            "top20_mae_pp": g["top20_by_real"]["mae_pp"], "top20_bias_pp": g["top20_by_real"]["bias_pp"], "top50_spearman": v["top50_by_real"]["spearman"],
            "all_spearman": v["spearman"], "all_mae_pp": v["mae_pp"],
            "overlap_10_20_50": [g["real_mass_captured_by_samplers_top"][k]["overlap_players"] for k in ("top10", "top20", "top50")],
            "capture_10_20_50": [g["real_mass_captured_by_samplers_top"][k]["capture_ratio"] for k in ("top10", "top20", "top50")],
            "salary_mean": e["salary_used"]["mean"], "salary_median": e["salary_used"]["median"], "salary_p10_p25_p75_p90": e["salary_used"]["p10_p25_p75_p90"],
            "share_at_cap_pct": e["salary_used"]["share_at_cap_pct"], "share_ge_49500_pct": e["salary_used"]["share_ge_49500_pct"], "share_le_48000_pct": e["salary_used"]["share_le_48000_pct"],
            "flex_rb_wr_te_pct": [e["flex_position_pct"].get(k, 0.0) for k in ("RB", "WR", "TE")],
            "punts_0_1_2plus_pct": [sum(vv for kk, vv in e["punts_under_3000_pct"].items() if (int(kk) >= 2 if k == 2 else int(kk) == k)) for k in (0, 1, 2)],
            "dst_vs_own_qb_pct": e["dst_vs_own_qb_pct"]["real"], "dst_vs_own_rb_pct": e["dst_vs_own_rb_pct"],
            "stack_0_1_2_3plus_pct": e["stack_pct"]["0_1_2_3plus"], "bring_back_pct": e["bring_back_pct"]["all_entries"],
            "unique_lineup_entry_share_pct": e["duplicates"]["1-1"]["entry_share_pct"], "distinct_lineups": e["distinct_lineups"], "max_lineup_copies": e["max_lineup_copies"],
            "collision_distinct_pairs": e["collision"]["distinct_pairs"], "hhi": e["collision"]["sum_p2"],
            "largest_over": v["largest_over"][:5], "largest_under": v["largest_under"][:5],
            "calibration_bins": g["calibration_by_modeled_ownership"]}


def assemble(outdir, log=print):
    from research import provenance, s6_capture
    d, man = S.served_board()
    real_art = S.real_side()
    real = real_art["empirical"]
    parts = []
    for i in range(len(ARMS)):
        path = os.path.join(outdir, f"cl_mech_arm_{i}.json")
        if not os.path.exists(path):
            raise SystemExit(f"missing {path}: run `python3 -m research.cl_mech arm {i} {outdir}` first")
        p = json.load(open(path))
        if p["kappa"] != ARMS[i]["kappa"] or p["flex_te"] != ARMS[i]["flex_te"] or p["seed"] != SEED or p["sample"]["n"] != FIELD_N:
            raise SystemExit(f"arm {i}'s partial does not match the preregistered arm: {p['kappa']}, {p['flex_te']}, seed {p['seed']}, n {p['sample']['n']}")
        parts.append(p)
    commits = {p["provenance"]["commit"] for p in parts}
    if len(commits) != 1 or any(p["provenance"]["dirty"] for p in parts):
        raise SystemExit(f"the arms were not all run on one clean commit: {commits}")
    here = provenance.stamp(worlds=FIELD_N, model="the classic sampler family's trade-off surface: kappa grid x DK-complete FLEX, beta to the ownership target alone", seed=SEED,
                            arm_runs_commit=list(commits)[0])
    if here["dirty"] or here["commit"] not in commits:
        raise SystemExit(f"assembly must run clean on the arms' commit ({commits}); here {here['commit']} dirty {here['dirty']}")
    real_row = {"max_own_pct": real["max_ownership"]["any_slot_pct"], "salary_mean": real["salary_used"]["mean"], "salary_median": real["salary_used"]["median"],
                "salary_p10_p25_p75_p90": real["salary_used"]["p10_p25_p75_p90"], "share_at_cap_pct": real["salary_used"]["share_at_cap_pct"],
                "share_ge_49500_pct": real["salary_used"]["share_ge_49500_pct"], "share_le_48000_pct": real["salary_used"]["share_le_48000_pct"],
                "flex_rb_wr_te_pct": [real["flex_position_pct"].get(k, 0.0) for k in ("RB", "WR", "TE")],
                "punts_0_1_2plus_pct": [sum(vv for kk, vv in real["punts_under_3000_pct"].items() if (int(kk) >= 2 if k == 2 else int(kk) == k)) for k in (0, 1, 2)],
                "dst_vs_own_qb_pct": real["dst_vs_own_qb_pct"]["real"], "dst_vs_own_rb_pct": real["dst_vs_own_rb_pct"],
                "stack_0_1_2_3plus_pct": real["stack_pct"]["0_1_2_3plus"], "bring_back_pct": real["bring_back_pct"]["all_entries"],
                "unique_lineup_entry_share_pct": real["duplicates"]["1-1"]["entry_share_pct"], "distinct_lineups": real["distinct_lineups"], "max_lineup_copies": real["max_lineup_copies"],
                "collision_distinct_pairs": real["collision"]["distinct_pairs"], "hhi": real["collision"]["sum_p2"]}
    rows = [_row(p, real) for p in parts]
    flex = flex_te_sampler()[1]
    out = {"meta": {"stage": "Task 8, third artifact: the current classic field family's trade-off surface on the pinned week-1 pool (kappa grid x DK-complete FLEX, beta to the ownership target alone), research only, the real field grading only",
                    "preregistered": {"arms": list(ARMS), "kappa_grid": list(KAPPA_GRID), "beta": "bisection to CL_FIELD_MAX_OWN alone at each fixed kappa; the served arm keeps the served beta and kappa",
                                      "seed": SEED, "field_n": FIELD_N, "cal_n": CAL_N, "one_seed_per_arm": "artifact 2 measured seed noise at 0.02 pp MAE and 0.004 top-50 Spearman for this family",
                                      "question": "can correcting legal FLEX support and removing the expensive-player pressure improve chalk ordering without recreating the salary failure somewhere else?",
                                      "not_done": "no kappa is picked after looking at the field; no arm is a model; no production change"},
                    "flex_te_variant": flex, "constants_as_served": {"CL_FIELD_MAX_OWN": __import__("dfs_tourney").CL_FIELD_MAX_OWN, "CL_SALARY_USED": __import__("dfs_tourney").CL_SALARY_USED},
                    "inputs": {"served_board": man, "first_artifact": "research/data/cl_field.json", "second_artifact": "research/data/cl_sampler.json",
                               "draftkings": s6_capture.dk_hashes(S.DG, capdir=S.CAPDIR)},
                    "fit_free": True, "provenance": here},
           "real": real_row, "arms": rows, "per_arm": parts}
    path = os.path.join(DATA, "cl_mech.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[MECH] written {path}")
    for r in rows:
        log(f"[MECH] kappa {str(r['kappa']):7} flex_te {str(r['flex_te']):5} beta {r['beta']:.4f} | top50 rho {r['top50_spearman']:.3f} top20 bias {r['top20_bias_pp']:+.2f} MAE {r['top20_mae_pp']:.2f} | overlap {r['overlap_10_20_50']} "
            f"| salary ${r['salary_mean']:,.0f} cap {r['share_at_cap_pct']}% le48k {r['share_le_48000_pct']}% | FLEX {r['flex_rb_wr_te_pct']} | punts {r['punts_0_1_2plus_pct']} | pairs {r['collision_distinct_pairs']:.2e}")
    return out


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "arm":
        run_arm(int(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) >= 3 and sys.argv[1] == "assemble":
        assemble(sys.argv[2])
    else:
        raise SystemExit("usage: python3 -m research.cl_mech arm <index> <scratch dir> | assemble <scratch dir>")
