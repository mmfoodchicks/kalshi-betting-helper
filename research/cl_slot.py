"""Slot-allocation v1: the pre-registered candidate classic field family.
RESEARCH ONLY. Fitted on week 1 alone; frozen before the week-2 main slate
locks (draft group 153428, 2026-09-20 17:00 UTC); graded on week 2 beside
the untouched current family. No production change.

Why this family (the reviewer, 2026-09-20). Three artifacts on the week-1
$3.5M Millionaire say the served sampler's failure is architectural: the
public behaves as if it allocates salary by roster role, while the current
sampler asks one global player score, beta * (proj - kappa * salary), to
explain everything. Its whole kappa grid left the real top-50 ordering
where it was and made salary use worse; none of eleven enumerated
per-player statistics orders the real top 50 (best 0.30) while Sleeper's
line orders players within RB, WR and TE at 0.93-0.94. The public's chalk
looks like a construction pattern: the projection leaders at the expensive
slots plus the cheapest serviceable tight end and defenses, and a lineup
that spends out.

The family, deliberately small (four things, three fitted numbers):

  1. FLEX position first: each lineup draws which position its FLEX holds
     (RB, WR or TE) from fixed probabilities, the week-1 maximum-likelihood
     shares of the observed FLEX counts (research/data/cl_field.json,
     empirical.flex_position_pct). A tight end is therefore permitted at
     FLEX, and only the drawn position may fill it.
  2. Within each required position, the EXISTING projection softmax: the
     production sampler's own construction (quarterback, drawn stack and
     bring-back, cap-aware fill, the defense last) with pull beta * proj and
     NO global price coefficient (kappa = 0).
  3. One shared cheap-enabler coefficient eta, applied to TE and DST only:
     their pull is beta * proj - eta * salary / 1000 (eta > 0 prefers the
     cheaper tight end or defense per projected point). Implemented as a
     projection offset of -(eta / beta) * salary / 1000 on TE and DST
     entrants, which is algebraically that pull with the sampler untouched.
  4. A completed-lineup salary-left penalty delta: a finished lineup is kept
     with probability exp(-delta * salary_left / 1000) (rejection sampling;
     no duplication is manufactured), which controls how completely the
     field spends toward the cap. Because it reweights finished lineups, it
     moves the realised FLEX mix away from the drawn week-1 shares (a tight
     end at FLEX leaves more salary on the table and is kept less often);
     that interaction is a property of the family, reported, not corrected.

The sampler is production's classic_sample with five source substitutions
(the FLEX eligibility becomes the drawn position per row, the FLEX salary
floor follows it, a pick of the drawn position may overflow into FLEX, the
eligibility combine accepts a per-row mask, and the function takes the
per-row FLEX position), executed in production's namespace; everything else
is production's by construction, the substitutions and both hashes are
stamped.

The fit, on week 1 only (the export pinned by hash; the served pre-lock pool
of 240; nothing from week 2):

  objective     slot-specific ownership cross-entropy: for each of the six
                roster-slot groups (QB, RB, WR, TE, FLEX, DST) the real
                per-pick share of each pool player at that slot (from the
                export's roster-slot column; RB and WR groups have two and
                three picks per lineup) against the model's per-pick share,
                summed over the six groups with equal weight. Real mass on
                players outside the pool is dropped and its size stamped.
  smoothing     one fixed rule for zero model counts: model share =
                (count + 0.5) / (picks + 0.5 * eligible players in the
                group), add-half per group.
  excluded      duplication, collision, stack and bring-back rates, winner
                lineups, any week-2 information: none touches the fit.
  search        pass 1: the beta x eta grid at delta = 0, 40,000 accepted
                lineups per point, the minimum objective (ties to the
                smaller beta then the smaller eta); delta 1: bisection on
                [0, 8] per $1,000 left, 12 steps, so the mean salary used of
                40,000 accepted lineups at (beta1, eta1) lands on the week-1
                mean ($49,849.9); pass 2: the grid again at delta 1; delta 2:
                the bisection again at (beta2, eta2); stop. Nothing after.
  grid          beta in (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50);
                eta in (0, 0.5, 1.0, 1.5, 2.0, 3.0) points per $1,000
  seeds         every fit evaluation draws from numpy SeedSequence
                (20260920, pass, i, j); the week-1 realisation from
                20260913; both fixed here
  freeze        research/data/cl_slot_frozen.json, written once, refused
                after: parameters, FLEX shares, grid, search trace, smoothing
                rule, seeds, the export's and the pool's hashes, the source
                hash of this module's producing functions, the week-2
                protocol

Week 2 (the held-out test; nothing refitted):

  pool          the latest valid pre-lock production pool of draft group
                153428: a served-board snapshot whose build stamp AND fetch
                both precede 17:00 UTC (a post-lock fetch is archival only,
                whatever its cron); failing that, the pool reconstructed
                from the pre-lock captures of research/data/prospective/153428
  arms          the untouched current family (production's own calibration
                and sampler, re-run on that pool at the field's size) and
                slot-allocation v1 with the frozen parameters, same pool,
                same size
  co-primary    lower slot-level cross-entropy; a closer salary-used
                distribution by 1-D Wasserstein distance
  out of        top-20 MAE and bias, top-50 Spearman, top-10/20/50 mass
  objective     capture, FLEX mix, punt rates, stack and bring-back,
                unique-lineup share, distinct-entry collision, duplicate tail
  reading       transfers on slots and salary but stays diffuse: the next
                problem is concentration and user-level behaviour; fails
                slot ownership: the week-1 allocation reading was slate-
                specific; fixes salary but not the chalk: richer role-
                specific choice behaviour is needed. No result is rescued by
                refitting week 2.

    python3 -m research.cl_slot fit <standings csv>          # week 1 only; writes the fit artifact and the frozen file
    python3 -m research.cl_slot score <board json> <fetched-utc> <standings csv> <sha256> <dg> <lock-utc>   # week 2, after the field exists
"""
import datetime
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
CAP = 50000
FLEX_CODES = {"RB": 1, "WR": 2, "TE": 3}
GROUPS = {"QB": (0,), "RB": (1, 2), "WR": (3, 4, 5), "TE": (6,), "FLEX": (7,), "DST": (8,)}
ELIGIBLE = {"QB": ("QB",), "RB": ("RB",), "WR": ("WR",), "TE": ("TE",), "FLEX": ("RB", "WR", "TE"), "DST": ("DST",)}
BETA_GRID = (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)
ETA_GRID = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0)
DELTA_BRACKET = (0.0, 8.0)
DELTA_STEPS = 12
FIT_N = 40000
FIT_SEED = 20260920
REAL_SEED = 20260913
CHUNK = 100000
MAX_RAW_FACTOR = 20
SMOOTHING = "add-half: model share = (count + 0.5) / (picks + 0.5 * eligible players in the group)"
QUANTILES = 1000
FROZEN = os.path.join(DATA, "cl_slot_frozen.json")

SUBSTITUTIONS = (
    ('                   report=None):', '                   report=None, flex_kind=None):'),
    ('                cols = (pc == 1) | (pc == 2)\n                room = need["FLEX"] > 0',
     '                cols = (pc[None, :] == flex_kind[:, None])\n                room = need["FLEX"] > 0'),
    ('            e |= (cols[None, :] & fit & room[:, None] & rows[:, None])',
     '            e |= ((cols if cols.ndim == 2 else cols[None, :]) & fit & room[:, None] & rows[:, None])'),
    ('            if kind in ("RB", "WR"):\n                room = room | (need["FLEX"] > 0)',
     '            if kind in ("RB", "WR", "TE"):\n                room = room | ((need["FLEX"] > 0) & (flex_kind == _CL_CODE[kind]))'),
    ('        rel["FLEX"] = np.minimum(nxt["RB"], nxt["WR"])',
     '        rel["FLEX"] = np.where(flex_kind == 1, nxt["RB"], np.where(flex_kind == 2, nxt["WR"], nxt["TE"]))'),
)


def slot_sampler():
    """production's classic_sample with the five substitutions, in production's
    namespace. Returns (function, receipt)."""
    import dfs_tourney as T
    src = inspect.getsource(T.classic_sample)
    for old, new in SUBSTITUTIONS:
        if src.count(old) != 1:
            raise SystemExit(f"a substitution target occurs {src.count(old)} times, not once; refusing to build the variant:\n{old}")
        src = src.replace(old, new)
    src = src.replace("def classic_sample(", "def classic_sample_slot(", 1)
    ns = dict(vars(T))
    exec(compile(src, "<classic_sample_slot>", "exec"), ns)
    return ns["classic_sample_slot"], {"base": "dfs_tourney.classic_sample", "base_sha256": hashlib.sha256(inspect.getsource(T.classic_sample).encode()).hexdigest(),
                                       "substitutions": [{"from": o, "to": n} for o, n in SUBSTITUTIONS],
                                       "variant_sha256": hashlib.sha256(src.encode()).hexdigest()}


def sample(ents, n, rng, beta, eta, delta, flex_probs, sampler=None, chunk=CHUNK, max_raw_factor=MAX_RAW_FACTOR):
    """`n` accepted lineups of the family. flex_probs = (P(RB), P(WR), P(TE))
    for the FLEX slot. Returns (idx (n, 9) int32, receipt)."""
    sampler = sampler or slot_sampler()[0]
    if beta <= 0:
        raise ValueError("beta must be positive")
    adj = [dict(e, proj=float(e["proj"]) - (eta / beta) * float(e["salary"]) / 1000.0) if e["pos"] in ("TE", "DST") else e for e in ents]
    sal = np.asarray([int(e["salary"]) for e in ents], dtype=np.int64)
    p = np.asarray(flex_probs, dtype=np.float64)
    p = p / p.sum()
    parts, got, raw, rejected, incomplete = [], 0, 0, 0, 0
    while got < n and raw < max_raw_factor * n:
        k = int(min(chunk, max(2 * (n - got), 5000)))
        fk = rng.choice(np.asarray([1, 2, 3]), size=k, p=p)
        rep = {}
        idx = sampler(adj, k, rng, beta, 0.0, report=rep, flex_kind=fk)
        incomplete += int(rep.get("rejected") or 0)
        left = CAP - sal[idx].sum(axis=1)
        keep = rng.random(len(idx)) < np.exp(-delta * left / 1000.0)
        rejected += int((~keep).sum())
        parts.append(idx[keep])
        got += int(keep.sum())
        raw += k
    idx = np.concatenate(parts, axis=0)[:n] if parts else np.zeros((0, 9), dtype=np.int32)
    return idx, {"requested": int(n), "accepted": int(len(idx)), "raw_draws": int(raw), "incomplete_rows": int(incomplete),
                 "rejected_by_delta": int(rejected), "acceptance_rate": (round(len(idx) / raw, 4) if raw else None), "short": len(idx) < n}


def real_slot_shares(by_slot_pct, ents):
    """{group: (q (P,), dropped_mass)}: the real per-pick share of each pool
    player at each roster-slot group, from the export's slot column."""
    names = [e["name"] for e in ents]
    out = {}
    for g, cols in GROUPS.items():
        k = len(cols)
        q = np.asarray([float((by_slot_pct.get(nm) or {}).get(g, 0.0)) for nm in names]) / (100.0 * k)
        total_in_pool = float(q.sum())
        out[g] = {"q": q / total_in_pool if total_in_pool > 0 else q, "pool_mass": round(total_in_pool, 5), "dropped_mass": round(1.0 - total_in_pool, 5)}
    return out


def model_slot_shares(idx, ents):
    """{group: m (P,)}: smoothed per-pick model shares (SMOOTHING)."""
    P = len(ents)
    pos = np.asarray([e["pos"] for e in ents])
    out = {}
    for g, cols in GROUPS.items():
        counts = np.bincount(np.asarray(idx)[:, list(cols)].ravel(), minlength=P).astype(np.float64)
        elig = np.isin(pos, ELIGIBLE[g])
        picks = float(len(idx) * len(cols))
        m = (counts + 0.5) / (picks + 0.5 * float(elig.sum()))
        out[g] = m
    return out


def slot_cross_entropy(idx, ents, real_shares):
    """{group: {ce, entropy, kl}} and the totals; CE = -sum q log m over q > 0."""
    m = model_slot_shares(idx, ents)
    out, tot_ce, tot_h = {}, 0.0, 0.0
    for g in GROUPS:
        q = real_shares[g]["q"]
        mask = q > 0
        ce = float(-(q[mask] * np.log(m[g][mask])).sum())
        h = float(-(q[mask] * np.log(q[mask])).sum())
        out[g] = {"cross_entropy": round(ce, 5), "real_entropy": round(h, 5), "kl": round(ce - h, 5), "players_with_real_mass": int(mask.sum())}
        tot_ce += ce
        tot_h += h
    out["total"] = {"cross_entropy": round(tot_ce, 5), "real_entropy": round(tot_h, 5), "kl": round(tot_ce - tot_h, 5)}
    return out


def quantiles(values, k=QUANTILES):
    v = np.sort(np.asarray(values, dtype=np.float64))
    u = (np.arange(k) + 0.5) / k
    return np.quantile(v, u).tolist()


def wasserstein1(qa, qb):
    """1-D Wasserstein distance between two distributions given as equal-count
    quantile lists (the mean absolute difference of matched quantiles)."""
    a, b = np.asarray(qa, dtype=np.float64), np.asarray(qb, dtype=np.float64)
    return round(float(np.abs(a - b).mean()), 3)


def real_from_export(path, ents, pool_slate):
    """The week's real side, from a pinned export: per-slot shares, the
    salary-used distribution, any-slot ownership, entries."""
    man = json.load(open(os.path.join(DATA, "dk_standings", "manifest.json")))["files"]
    key = next((k for k, v in man.items() if os.path.basename(path).replace(".gz", "") in k or v.get("csv_sha256") == hashlib.sha256(open(path, "rb").read()).hexdigest()), None)
    if key is None:
        raise SystemExit(f"{path}: not pinned in research/data/dk_standings/manifest.json; pin it first (research.s7_prospective standings)")
    std = C.load_standings(path, man[key]["csv_sha256"])
    cnt, sal_used, own = {}, [], {}
    n = 0
    for e in std["entries"]:
        lu = e["lineup"]
        if not lu or any(nm not in pool_slate for _, nm in lu):
            continue
        n += 1
        sal_used.append(sum(pool_slate[nm]["salary"] for _, nm in lu))
        for slot, nm in lu:
            cnt.setdefault(nm, {}).setdefault(slot, 0)
            cnt[nm][slot] += 1
            own[nm] = own.get(nm, 0) + 1
    by_slot = {nm: {s: round(100.0 * c / n, 4) for s, c in d.items()} for nm, d in cnt.items()}
    return {"sha256": std["sha256"], "active_lineups": n, "by_slot_pct": by_slot, "salary_quantiles": quantiles(sal_used), "salary_mean": round(float(np.mean(sal_used)), 1),
            "ownership_any_slot_pct": {nm: round(100.0 * c / n, 4) for nm, c in own.items()}, "shares": real_slot_shares(by_slot, ents)}


def grade_field(idx, ents, real, pool_g):
    """Everything, co-primary first: slot cross-entropy and salary W1; then the
    out-of-objective diagnostics through the earlier artifacts' functions."""
    sal = np.asarray([int(e["salary"]) for e in ents], dtype=np.int64)
    used = sal[idx].sum(axis=1)
    emp, own, receipts = S.grade(idx, ents, pool_g)
    return {"co_primary": {"slot_cross_entropy": slot_cross_entropy(idx, ents, real["shares"]),
                           "salary_wasserstein1": wasserstein1(quantiles(used), real["salary_quantiles"]),
                           "salary_mean": round(float(used.mean()), 1), "real_salary_mean": real["salary_mean"]},
            "out_of_objective": {"vs_real": S.compare_ownership(own, real["ownership_any_slot_pct"], ents),
                                 "diagnostics": S.ownership_diagnostics(own, real["ownership_any_slot_pct"], ents),
                                 "empirical": emp, "production_receipts": receipts},
            "ownership_any_slot_pct": own}


def _objective(sampler, ents, real, beta, eta, delta, flex_probs, seed_seq):
    rng = np.random.default_rng(seed_seq)
    idx, rec = sample(ents, FIT_N, rng, beta, eta, delta, flex_probs, sampler=sampler)
    ce = slot_cross_entropy(idx, ents, real["shares"])["total"]["cross_entropy"]
    sal = np.asarray([int(e["salary"]) for e in ents], dtype=np.int64)
    return ce, float(sal[idx].sum(axis=1).mean()), rec


def _grid_pass(sampler, ents, real, delta, flex_probs, pass_no, log):
    rows = []
    for i, beta in enumerate(BETA_GRID):
        for j, eta in enumerate(ETA_GRID):
            ce, mean_sal, rec = _objective(sampler, ents, real, beta, eta, delta, flex_probs, np.random.SeedSequence([FIT_SEED, pass_no, i, j]))
            rows.append({"beta": beta, "eta": eta, "delta": delta, "cross_entropy": round(ce, 5), "salary_mean": round(mean_sal, 1), "acceptance_rate": rec["acceptance_rate"], "short": rec["short"]})
            log(f"[SLOT] pass {pass_no} beta {beta:.2f} eta {eta:.1f} delta {delta:.4f}: CE {ce:.5f} salary ${mean_sal:,.0f} acc {rec['acceptance_rate']}")
    best = min(rows, key=lambda r: (r["cross_entropy"], r["beta"], r["eta"]))
    return rows, best


def _delta_bisect(sampler, ents, real, beta, eta, flex_probs, pass_no, log):
    lo, hi = DELTA_BRACKET
    trace = []
    for s in range(DELTA_STEPS):
        delta = 0.5 * (lo + hi)
        _ce, mean_sal, rec = _objective(sampler, ents, real, beta, eta, delta, flex_probs, np.random.SeedSequence([FIT_SEED, pass_no, 100 + s]))
        trace.append({"delta": round(delta, 5), "salary_mean": round(mean_sal, 1), "acceptance_rate": rec["acceptance_rate"]})
        log(f"[SLOT] delta pass {pass_no} step {s}: delta {delta:.4f} salary ${mean_sal:,.0f} (target ${real['salary_mean']:,.0f}) acc {rec['acceptance_rate']}")
        if mean_sal < real["salary_mean"]:
            lo = delta
        else:
            hi = delta
    return 0.5 * (lo + hi), trace


def fit(standings_csv, log=print):
    from research import provenance, s6_capture
    if os.path.exists(FROZEN):
        raise SystemExit(f"{FROZEN} exists; the frozen file is written once and never rewritten")
    d, man = S.served_board()
    ents = S.served_pool(d)
    pool_g = S.grading_pool(ents)
    slate, _ = s6_capture.replay(S.DG, capdir=S.CAPDIR)
    real = real_from_export(standings_csv, ents, C.pool_rows(slate))
    art1 = S.real_side()
    fp = art1["empirical"]["flex_position_pct"]
    flex_probs = [fp.get("RB", 0.0) / 100.0, fp.get("WR", 0.0) / 100.0, fp.get("TE", 0.0) / 100.0]
    sampler, receipt = slot_sampler()
    t0 = time.time()
    rows1, best1 = _grid_pass(sampler, ents, real, 0.0, flex_probs, 1, log)
    delta1, tr1 = _delta_bisect(sampler, ents, real, best1["beta"], best1["eta"], flex_probs, 1, log)
    rows2, best2 = _grid_pass(sampler, ents, real, delta1, flex_probs, 2, log)
    delta2, tr2 = _delta_bisect(sampler, ents, real, best2["beta"], best2["eta"], flex_probs, 2, log)
    params = {"beta": best2["beta"], "eta": best2["eta"], "delta": round(delta2, 5), "flex_probs_rb_wr_te": [round(x, 5) for x in flex_probs]}
    t1 = time.time()
    log(f"[SLOT] frozen: {params} ({t1 - t0:.0f}s); realising {S.FIELD_N:,}...")
    idx, rec = sample(ents, S.FIELD_N, np.random.default_rng(REAL_SEED), params["beta"], params["eta"], params["delta"], flex_probs, sampler=sampler)
    graded = grade_field(idx, ents, real, pool_g)
    src = open(os.path.join(ROOT, "research", "cl_slot.py")).read()
    prov = provenance.stamp(worlds=S.FIELD_N, model="slot-allocation v1 (research), fitted on week 1 only", seed={"fit": FIT_SEED, "realisation": REAL_SEED})
    frozen = {"family": "slot-allocation v1", "frozen_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
              "parameters": params,
              "fit": {"data": {"week1_export_sha256": real["sha256"], "week1_active_lineups": real["active_lineups"], "served_board_sha256": man["sha256"], "first_artifact": "research/data/cl_field.json",
                               "dropped_real_mass_by_group": {g: real["shares"][g]["dropped_mass"] for g in GROUPS}},
                      "objective": "sum over QB, RB, WR, TE, FLEX, DST of the cross-entropy between the real per-pick share and the smoothed model per-pick share; equal weight",
                      "smoothing": SMOOTHING, "excluded": ["duplication", "collision", "stack rates", "bring-back", "winner lineups", "week-2 information"],
                      "search": {"beta_grid": list(BETA_GRID), "eta_grid": list(ETA_GRID), "delta_bracket": list(DELTA_BRACKET), "delta_steps": DELTA_STEPS, "fit_n": FIT_N,
                                 "order": "grid at delta 0 -> delta bisection to the week-1 mean salary -> grid at that delta -> delta bisection -> stop", "tie_break": "smaller beta, then smaller eta",
                                 "seeds": {"fit": FIT_SEED, "realisation": REAL_SEED, "per_evaluation": "numpy SeedSequence([FIT_SEED, pass, i, j]); delta steps [FIT_SEED, pass, 100 + step]"}},
                      "trace": {"pass1": rows1, "best1": best1, "delta1": {"value": round(delta1, 5), "trace": tr1}, "pass2": rows2, "best2": best2, "delta2": {"value": round(delta2, 5), "trace": tr2}}},
              "sampler": receipt, "source_sha256": hashlib.sha256(src.encode()).hexdigest(),
              "week2_protocol": {"draft_group": 153428, "lock_utc": "2026-09-20 17:00:00 UTC",
                                 "pool": "the latest valid pre-lock production pool: a served-board snapshot whose build stamp and fetch both precede lock; else the pool reconstructed from research/data/prospective/153428",
                                 "arms": ["untouched current family, re-run on that pool at the field's size", "slot-allocation v1 with these parameters, same pool, same size"],
                                 "co_primary": ["lower slot-level cross-entropy", "closer salary-used distribution by 1-D Wasserstein distance"],
                                 "out_of_objective": ["top-20 MAE and bias", "top-50 Spearman", "top-10/20/50 mass capture", "FLEX mix", "punt rates", "stack and bring-back", "unique-lineup share", "distinct-entry collision", "duplicate tail"],
                                 "no_refit": True},
              "production_unchanged": True, "provenance": prov}
    with open(FROZEN, "w") as fh:
        json.dump(frozen, fh, indent=1, sort_keys=True)
    art = {"meta": {"stage": "slot-allocation v1: the week-1 fit (in-sample) and its realisation at the field's size; the frozen parameters are in research/data/cl_slot_frozen.json",
                    "frozen_sha256": hashlib.sha256(open(FROZEN, "rb").read()).hexdigest(), "in_sample": True, "fit_free": False,
                    "what_is_fitted": "beta, eta, delta on week 1 by the pre-registered search; FLEX shares are the week-1 MLE", "provenance": prov},
           "parameters": params, "realisation": {"n": int(len(idx)), "seed": REAL_SEED, "receipt": rec, "seconds": round(time.time() - t1, 1)},
           "graded": graded, "real": {k: real[k] for k in ("sha256", "active_lineups", "salary_quantiles", "salary_mean")}}
    with open(os.path.join(DATA, "cl_slot_fit.json"), "w") as fh:
        json.dump(art, fh, indent=1, sort_keys=True)
    cp = graded["co_primary"]
    log(f"[SLOT] in-sample: slot CE {cp['slot_cross_entropy']['total']['cross_entropy']} (real entropy {cp['slot_cross_entropy']['total']['real_entropy']}), salary W1 ${cp['salary_wasserstein1']}, mean ${cp['salary_mean']:,.0f}; "
        f"top-50 rho {graded['out_of_objective']['vs_real']['top50_by_real']['spearman']}, top-20 bias {graded['out_of_objective']['vs_real']['top20_by_real']['bias_pp']}, FLEX {graded['out_of_objective']['empirical']['flex_position_pct']}")
    return frozen, art


def score(board_path, fetched_utc, standings_csv, dg, lock_utc, log=print):
    """Week 2: both arms on the same pre-lock pool, graded by the frozen
    protocol. `fetched_utc` is when the snapshot was pulled (from its
    sim-history commit); it and the board's build stamp must precede lock."""
    from research import provenance
    import dfs_tourney as T
    frozen = json.load(open(FROZEN))
    board = json.load(open(board_path))
    lock = datetime.datetime.strptime(lock_utc[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
    fetched = datetime.datetime.strptime(fetched_utc[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
    if board.get("draft_group_id") != int(dg) or float(board["built_ts"]) >= lock or fetched >= lock:
        raise SystemExit("the board is not that draft group's, or it was built or fetched at or after lock: archival only, refused as the primary")
    ents = S.served_pool(board)
    pool_g = S.grading_pool(ents)
    pool_slate = {e["name"]: {"salary": e["salary"]} for e in ents}
    real = real_from_export(standings_csv, ents, pool_slate)
    n = real["active_lineups"]
    p = frozen["parameters"]
    sampler, receipt = slot_sampler()
    idx_c, rec_c = sample(ents, n, np.random.default_rng(REAL_SEED), p["beta"], p["eta"], p["delta"], p["flex_probs_rb_wr_te"], sampler=sampler)
    rng = np.random.default_rng(REAL_SEED)
    beta0, kappa0, _o, _m, _c, _t = T.calibrate_field(ents, rng, n=S.CAL_N)
    parts, done = [], 0
    while done < n:
        k = min(CHUNK, n - done)
        rep = {}
        idx = T.classic_sample(ents, k, rng, beta0, kappa0, report=rep)
        parts.append(idx)
        done += len(idx)
    idx_0 = np.concatenate(parts, axis=0)[:n]
    out = {"meta": {"stage": "slot-allocation v1 against the untouched current family on the week-2 held-out slate, the frozen protocol", "frozen": frozen["parameters"],
                    "frozen_sha256": hashlib.sha256(open(FROZEN, "rb").read()).hexdigest(), "board": {"path": os.path.relpath(board_path, ROOT), "built_ts": board["built_ts"], "fetched_utc": fetched_utc, "lock_utc": lock_utc},
                    "provenance": provenance.stamp(worlds=n, model="slot-allocation v1 (frozen) and the current family, week 2", seed=REAL_SEED)},
           "real": {k: real[k] for k in ("sha256", "active_lineups", "salary_quantiles", "salary_mean")},
           "current_family": {"beta": round(float(beta0), 6), "kappa": round(float(kappa0), 6), "graded": grade_field(idx_0, ents, real, pool_g)},
           "candidate": {"receipt": rec_c, "graded": grade_field(idx_c, ents, real, pool_g)}}
    path = os.path.join(DATA, f"cl_slot_week2_{dg}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[SLOT] written {path}")
    return out


def week1_reference(standings_csv, log=print):
    """The untouched current family on the week-1 pool at the field's size,
    graded exactly as the candidate was (in-sample reference for the
    co-primary and out-of-objective readings; the family itself unchanged)."""
    from research import provenance, s6_capture
    import dfs_tourney as T
    d, man = S.served_board()
    ents = S.served_pool(d)
    pool_g = S.grading_pool(ents)
    slate, _ = s6_capture.replay(S.DG, capdir=S.CAPDIR)
    real = real_from_export(standings_csv, ents, C.pool_rows(slate))
    n = real["active_lineups"]
    rng = np.random.default_rng(REAL_SEED)
    t0 = time.time()
    beta0, kappa0, _o, _m, _c, _t = T.calibrate_field(ents, rng, n=S.CAL_N)
    parts, done = [], 0
    while done < n:
        k = min(CHUNK, n - done)
        rep = {}
        idx = T.classic_sample(ents, k, rng, beta0, kappa0, report=rep)
        T.check_completion(rep, "week-1 reference")
        parts.append(idx)
        done += len(idx)
    idx = np.concatenate(parts, axis=0)[:n]
    out = {"meta": {"stage": "the untouched current family on the week-1 pool at the field's size, graded by the frozen protocol's functions: the in-sample reference beside slot-allocation v1's fit",
                    "family": "dfs_tourney.calibrate_field + classic_sample, the constants as served", "seed": REAL_SEED, "n": int(n),
                    "frozen_sha256": hashlib.sha256(open(FROZEN, "rb").read()).hexdigest(), "provenance": provenance.stamp(worlds=n, model="the current classic family, week-1 reference", seed=REAL_SEED)},
           "calibration": {"beta": round(float(beta0), 6), "kappa": round(float(kappa0), 6), "seconds": round(time.time() - t0, 1)},
           "graded": grade_field(idx, ents, real, pool_g), "real": {k: real[k] for k in ("sha256", "active_lineups", "salary_mean")}}
    path = os.path.join(DATA, "cl_slot_week1_reference.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    cp = out["graded"]["co_primary"]
    log(f"[SLOT] current family, week 1: slot CE {cp['slot_cross_entropy']['total']['cross_entropy']}, salary W1 ${cp['salary_wasserstein1']}, mean ${cp['salary_mean']:,.0f}; written {path}")
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "fit" and len(a) == 2:
        fit(a[1])
    elif a and a[0] == "reference" and len(a) == 2:
        week1_reference(a[1])
    elif a and a[0] == "score" and len(a) == 7:
        score(a[1], a[2], a[3], int(a[5]), a[6])
    else:
        raise SystemExit("usage: python3 -m research.cl_slot fit <standings csv> | reference <standings csv> | score <board json> <fetched-utc> <standings csv> <sha256> <dg> <lock-utc>")
