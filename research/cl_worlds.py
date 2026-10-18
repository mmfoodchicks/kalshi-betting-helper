"""More simulations for the classic board: which dial buys better lineups?

The owner (2026-10-18), once the classic build fell from 45 minutes to a few:
"can we add a lot more simulations?" A board has three sampling dials --
the evaluation WORLDS (40,000 of the 60,000), the sampled FIELD (300,000
lineups standing in for the contest's entries) and the field's calibration
draw -- and on the one comparison read so far (the same worlds, a fresh field
sample and calibration) the top win% list kept 4 of its 20, the top-1% list
17 and the money list 18. This measures, before anything is turned up,
whether selecting with more worlds or a bigger field picks lineups that do
better on worlds and a field the selection never saw.

WRITTEN BEFORE ANY NUMBER WAS READ (hashed into the scratch prereg log):

Inputs. Week 1's pinned classic slate (dg 151307, contest 193028206, the
$3.5M Millionaire, 832,342 entries), the real build_nfl_classic run to its
scoring pass: model "constrained", seed 7, 300,000 worlds. The first 20,000
generate the candidates exactly as the board's do; the 280,000 evaluation
worlds are cut in order into a pre-screen (40,000), selection (160,000, four
blocks of 40,000) and a reference (80,000). The field's beta and kappa are
the build's own calibration; nine more 300,000-lineup fields are drawn with
them through FieldSampler on their own seeds: F0 for the pre-screen, Q1-Q4
for selection, R1-R4 for the reference.

Shortlist. Every allowed candidate in the top 2,000 of the pre-screen (F0,
40,000 worlds) by any of the five list metrics. Every configuration selects
from the same shortlist.

Configurations (selection worlds x field):
  P     40k x 300k  -- production: block b with field Qb (4 replicates)
  W2    80k x 300k  -- blocks 1+2 with Q1, 3+4 with Q3 (2)
  W4   160k x 300k  -- all four blocks with Qq (4; the worlds shared)
  F4    40k x 1.2M  -- block b with Q1-Q4 pooled (4; the field shared)
  W2F4  80k x 1.2M  -- the block pairs with the pool (2)
  W4F4 160k x 1.2M  -- everything (1)
A pooled field is the four quarter-fields' score histograms averaged world
by world: exactly a 1.2M-lineup field at weight 1/1.2M each.

Outcome. For each list -- top_win (win_sole), top_top1 (top1), top_top01
(top01), top_money (money, ties by ev) and top_ev (ev), the board's own
sorts -- a replicate's top 20 allowed shortlist lineups by the list's metric,
valued at the REFERENCE (80,000 worlds against R1-R4 pooled, 1.2M) in the
same metric; a configuration's value is the mean over its replicates of the
mean over the 20.

Rule. A configuration beats production on a list when its value exceeds
production's by more than two standard errors, SE = sqrt(sd_P^2/4 +
sd_X^2/n_X) over replicates, with sd_X taken as sd_P when n_X < 2 (its own
spread cannot be read, and with more data it is at most production's). The
recommendation is the cheapest configuration (scoring work = worlds x
(candidates + field lineups)) that beats production on top_top1 or
top_money, the board's two primary lists (win and EV are labelled
experimental on the board). If none does, the board keeps 40,000 x 300,000,
and the report says so. The win, top-0.1% and EV lists are reported beside,
never decisive. Replicates sharing worlds or a field (W4, F4) understate
their own spread; the report says which.

Descriptive, not part of the rule: top-20 overlap between replicates of a
configuration; how deep in the pre-screen the selected lineups sat (a check
that the 2,000 cut did not bind); four independent calibrations (20,000
draws each) -- their beta, kappa, top ownership and mean salary.

The roster pinned in <feed dir> may be a later capture than week 1's (the
week-1 roster capture holds 104 players); the pool is a real one and the
question is sampling noise, not week 1's result. Its sha256 is recorded.

python3 -m research.cl_worlds <feed dir> [workers]
"""
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "cl_worlds.json")
CONTEST = 193028206
N_SIMS, OPT_WORLDS, SEED = 300000, 20000, 7
PRE, BLOCK, N_BLOCKS, REF = 40000, 40000, 4, 80000
FIELD = 300000
SHORT = 2000
TOP = 20
LISTS = {"top_win": "win_sole", "top_top1": "top1", "top_top01": "top01", "top_money": "money", "top_ev": "ev"}
PRIMARY = ("top_top1", "top_money")
FIELD_SEEDS = {"F0": 9100, "Q1": 9101, "Q2": 9102, "Q3": 9103, "Q4": 9104,
               "R1": 9201, "R2": 9202, "R3": 9203, "R4": 9204}
CAL_SEEDS = (9301, 9302, 9303, 9304)
# CLW_SMOKE=1 runs the same code on a tenth of everything, to its own file,
# so a bug cannot cost the hour the real run takes; the real run never reads it
SMOKE = os.environ.get("CLW_SMOKE") == "1"
if SMOKE:
    N_SIMS, PRE, BLOCK, REF, FIELD, SHORT = 42000, 4000, 4000, 8000, 30000, 200
    OUT = os.path.join(ROOT, "research", "data", "cl_worlds_smoke.json")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def capture(feed_dir, workers, log=print):
    """The real build to its scoring pass: (ents, beta, kappa, Wc, allowed, Xe, grids)."""
    from research import sd_board, s6_capture, cl_field as C
    import dk
    import kalshi_nfl
    import dfs_tourney as T
    sd_board.feeds(feed_dir, season="2026", week=1)
    slate, details = s6_capture.replay(C.DG, capdir=C.CAPDIR)
    dk.slate_for = lambda sport, draft_group_id=None, **k: slate
    dk.contest_detail = lambda cid: details.get(str(cid))
    kalshi_nfl.index = lambda *a, **k: {}
    got = {}
    cal0, allowed0, run0 = T.calibrate_field, T.classic_allowed, T.run_vs_field

    def cal(players, rng, **kw):
        out = cal0(players, rng, **kw)
        got.update(ents=players, beta=out[0], kappa=out[1], cal=out[2:4])
        return out

    def allowed(idx, players, **kw):
        a = allowed0(idx, players, **kw)
        got["allowed"] = a
        return a

    class Stop(Exception):
        pass

    def run(Wc, Wf, wf, X, grids, **kw):
        got.update(Wc=Wc, Xe=X, grids=grids)
        raise Stop()
    T.calibrate_field, T.classic_allowed, T.run_vs_field = cal, allowed, run
    try:
        T.build_nfl_classic(C.DG, contest_ids=[CONTEST], n_sims=N_SIMS, opt_worlds=OPT_WORLDS, week=1,
                            model="constrained", seed=SEED, workers=workers, log=log)
    except Stop:
        pass
    finally:
        T.calibrate_field, T.classic_allowed, T.run_vs_field = cal0, allowed0, run0
    return got


def _tables(hist, F_grid, E_log, nE):
    """_world_tables from a score histogram (field mass per bucket)."""
    import numpy as np
    import dfs_tourney as T
    strictly = np.cumsum(hist[::-1])[::-1] - hist
    pos = np.searchsorted(F_grid, np.clip(strictly, 0.0, 1.0))
    iF = np.minimum(pos, len(F_grid) - 1)
    iE = T._log_index(E_log, np.clip(hist, 0.0, T.TIE_E_MAX))
    return iF, iF * nE + iE


def _score_variants(job):
    """One world range: run_vs_field's sums against each quarter field and
    their pool, (V, K, cols) and (V, K, 2), V = quarters + 1."""
    import numpy as np
    import dfs_tourney as T
    a, b, chunk = job
    P = T._PAR
    WcT, WfT, X = P["WcT"], P["WfT"], P["X"]
    G, G2, F_grid, E_log, nE, Mq, nq = P["G"], P["G2"], P["F_grid"], P["E_log"], P["nE"], P["Mq"], P["nq"]
    K = WcT.shape[1]
    acc = np.zeros((nq + 1, K, G.shape[1]), dtype=np.float64)
    acc2 = np.zeros((nq + 1, K, G2.shape[1]), dtype=np.float64)
    for start in range(a, b, chunk):
        Xc = np.ascontiguousarray(X[:, start:min(b, start + chunk)].T)
        Bc = T._buckets(Xc @ WcT)
        Bf = T._buckets(Xc @ WfT)
        for j in range(Bc.shape[0]):
            hs = [np.bincount(Bf[j, q * Mq:(q + 1) * Mq], minlength=T._NB) / Mq for q in range(nq)]
            hs.append(sum(hs) / nq)
            bj = Bc[j]
            for v, h in enumerate(hs):
                iF, iFE = _tables(h, F_grid, E_log, nE)
                acc[v] += G[iF[bj]]
                acc2[v] += G2[iFE[bj]]
    return acc, acc2


def score_variants(Wc, Wfs, X, grids, workers):
    """Sums over X's worlds against each field in Wfs (equal sizes) and their pool."""
    import numpy as np
    import dfs_tourney as T
    Mq = Wfs[0].shape[0]
    Wf = np.concatenate(Wfs, axis=0)
    G = T._grid_table(grids)
    G2, nE = T._tie_table(grids)
    w = max(1, int(workers))
    ch = T.chunk_for(Wf.shape[0] * w, 500)
    arrays = {"WcT": np.ascontiguousarray(Wc.T, dtype=np.float32),
              "WfT": np.ascontiguousarray(Wf.T, dtype=np.float32),
              "X": np.ascontiguousarray(X, dtype=np.float32)}
    extra = {"G": G, "G2": G2, "F_grid": grids[0]["F"], "E_log": np.log(np.maximum(grids[0]["E"], 1e-300)),
             "nE": nE, "Mq": Mq, "nq": len(Wfs)}
    parts = T._par_map(_score_variants, T._range_jobs(X.shape[1], w, ch), w, arrays, extra)
    return sum(p[0] for p in parts), sum(p[1] for p in parts)


def metrics(acc, acc2, N):
    """run_vs_field's first-contest metric dict from sums over N worlds."""
    cols, tie = acc / N, acc2 / N
    return {"win": cols[:, 0], "top1": cols[:, 1], "top01": cols[:, 2], "cash": cols[:, 3],
            "ev_notie": cols[:, 4], "money": cols[:, 5], "win_sole": tie[:, 0], "ev": tie[:, 1]}


def top_of(m, key, ok, k=TOP):
    """The board's sort of shortlist positions `ok` by list metric `key`."""
    import numpy as np
    if key == "money":
        order = np.lexsort((-m["ev"][ok], -np.round(m["money"][ok], 6)))
    else:
        order = np.argsort(-m[key][ok], kind="stable")
    return ok[order][:k]


def main(feed_dir, workers=4, log=print):
    import numpy as np
    import dfs_tourney as T
    t0 = time.time()
    stamp = {"git": subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
             "dirty": bool(subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--untracked-files=no"],
                                          capture_output=True, text=True).stdout.strip()),
             "feeds": {f: _sha(os.path.join(feed_dir, f)) for f in sorted(os.listdir(feed_dir)) if f.endswith(".json")}}
    got = capture(feed_dir, workers, log=log)
    ents, beta, kappa, grids = got["ents"], got["beta"], got["kappa"], got["grids"]
    Wc_all, allowed, Xe = got["Wc"], np.asarray(got["allowed"]), got["Xe"]
    P = len(ents)
    log(f"[worlds] captured: {Wc_all.shape[0]:,} candidates ({int(allowed.sum()):,} allowed), {Xe.shape[1]:,} "
        f"evaluation worlds, beta {beta:.4f} kappa {kappa:.4f} ({time.time() - t0:.0f}s)")
    assert Xe.shape[1] == PRE + N_BLOCKS * BLOCK + REF, Xe.shape
    timing = {"capture_s": round(time.time() - t0, 1)}
    # --- the fields ---
    t1 = time.time()
    draw = T.FieldSampler(ents, workers)
    try:
        W = {}
        for name, s in FIELD_SEEDS.items():
            rep = {}
            idx = draw(FIELD, np.random.default_rng(s), beta, kappa, report=rep)
            T.check_completion(rep, name)
            W[name] = T.lineup_matrix(idx, P)
    finally:
        draw.close()
    timing["fields_s"] = round(time.time() - t1, 1)
    # --- the pre-screen and the shortlist ---
    t1 = time.time()
    wf = np.full(FIELD, 1.0 / FIELD)
    pre, _o, _n = T.run_vs_field(Wc_all, W["F0"], wf, np.ascontiguousarray(Xe[:, :PRE]), grids, workers=workers)
    pre = pre[0]
    ok_all = np.where(allowed)[0]
    pre_rank = {}
    short = set()
    for lst, key in LISTS.items():
        order = top_of(pre, key, ok_all, k=len(ok_all))
        pre_rank[lst] = {int(i): r for r, i in enumerate(order)}
        short.update(int(i) for i in order[:SHORT])
    short = np.asarray(sorted(short), dtype=np.int64)
    Wc = Wc_all[short]
    ok = np.arange(len(short))
    timing["prescreen_s"] = round(time.time() - t1, 1)
    log(f"[worlds] shortlist {len(short):,} lineups ({timing['prescreen_s']:.0f}s)")
    # --- selection: four blocks against Q1-Q4 and their pool ---
    sums = []
    for bi in range(N_BLOCKS):
        t1 = time.time()
        a = PRE + bi * BLOCK
        sums.append(score_variants(Wc, [W[f"Q{q}"] for q in range(1, 5)], Xe[:, a:a + BLOCK], grids, workers))
        log(f"[worlds] selection block {bi + 1}/{N_BLOCKS} ({time.time() - t1:.0f}s)")
        timing[f"block{bi + 1}_s"] = round(time.time() - t1, 1)
    # the variant scorer IS run_vs_field: block 1 against Q1, both ways
    t1 = time.time()
    chk, _o, _n = T.run_vs_field(Wc, W["Q1"], wf, np.ascontiguousarray(Xe[:, PRE:PRE + BLOCK]), grids, workers=workers)
    mine = metrics(sums[0][0][0], sums[0][1][0], BLOCK)
    check = max(float(np.abs(chk[0][k] - mine[k]).max()) for k in mine)
    timing["check_s"] = round(time.time() - t1, 1)
    log(f"[worlds] variant scorer vs run_vs_field, block 1 / Q1: max difference {check:.2e}")
    # --- the reference ---
    t1 = time.time()
    a = PRE + N_BLOCKS * BLOCK
    ra, ra2 = score_variants(Wc, [W[f"R{q}"] for q in range(1, 5)], Xe[:, a:a + REF], grids, workers)
    ref = metrics(ra[4], ra2[4], REF)
    ref_q = [metrics(ra[q], ra2[q], REF) for q in range(4)]
    timing["reference_s"] = round(time.time() - t1, 1)
    log(f"[worlds] reference ({timing['reference_s']:.0f}s)")

    def combo(blocks, v):
        acc = sum(sums[b][0][v] for b in blocks)
        acc2 = sum(sums[b][1][v] for b in blocks)
        return metrics(acc, acc2, BLOCK * len(blocks))
    POOL = 4
    configs = {
        "P": [combo([b], b) for b in range(4)],
        "W2": [combo([0, 1], 0), combo([2, 3], 2)],
        "W4": [combo([0, 1, 2, 3], q) for q in range(4)],
        "F4": [combo([b], POOL) for b in range(4)],
        "W2F4": [combo([0, 1], POOL), combo([2, 3], POOL)],
        "W4F4": [combo([0, 1, 2, 3], POOL)],
    }
    K_prod = int(Wc_all.shape[0])
    shape = {"P": (40000, FIELD), "W2": (80000, FIELD), "W4": (160000, FIELD),
             "F4": (40000, 4 * FIELD), "W2F4": (80000, 4 * FIELD), "W4F4": (160000, 4 * FIELD)}
    cost = {c: round(n * (K_prod + m) / (40000 * (K_prod + FIELD)), 2) for c, (n, m) in shape.items()}
    shared = {"W4": "the four replicates share their worlds", "F4": "the four replicates share their field"}
    out = {"configs": {}}
    for c, reps in configs.items():
        row = {"worlds": shape[c][0], "field": shape[c][1], "replicates": len(reps), "cost_x": cost[c],
               "shared": shared.get(c), "lists": {}}
        for lst, key in LISTS.items():
            picks = [top_of(m, key, ok) for m in reps]
            vals = [float(ref[key][p].mean()) for p in picks]
            sel = [float(m[key][p].mean()) for m, p in zip(reps, picks)]
            ov = [len(set(map(int, x)) & set(map(int, y))) for i, x in enumerate(picks) for y in picks[i + 1:]]
            deep = [pre_rank[lst][int(short[i])] for p in picks for i in p]
            row["lists"][lst] = {"ref_values": vals, "selected_values": sel,
                                 "ref_mean": float(np.mean(vals)), "ref_sd": float(np.std(vals, ddof=1)) if len(vals) > 1 else None,
                                 "overlap_mean": float(np.mean(ov)) if ov else None,
                                 "prescreen_rank_max": int(max(deep)), "prescreen_rank_over_1000": int(sum(r >= 1000 for r in deep)),
                                 "picks": [[int(short[i]) for i in p] for p in picks]}
        out["configs"][c] = row
    # --- the rule ---
    verdict = {}
    for lst in LISTS:
        base = out["configs"]["P"]["lists"][lst]
        sd_p = base["ref_sd"]
        verdict[lst] = {}
        for c in configs:
            if c == "P":
                continue
            r = out["configs"][c]["lists"][lst]
            n_x = out["configs"][c]["replicates"]
            sd_x = r["ref_sd"] if n_x >= 2 else sd_p
            se = float(np.sqrt(sd_p ** 2 / 4 + sd_x ** 2 / n_x))
            d = r["ref_mean"] - base["ref_mean"]
            verdict[lst][c] = {"diff": d, "se": se, "beats": bool(d > 2 * se)}
    winners = sorted((cost[c], c) for c in configs if c != "P"
                     and any(verdict[lst][c]["beats"] for lst in PRIMARY))
    out["verdict"] = verdict
    out["recommendation"] = winners[0][1] if winners else "P"
    # --- descriptive: the calibration's own spread ---
    t1 = time.time()
    cals = []
    draw = T.FieldSampler(ents, workers)
    try:
        for s in CAL_SEEDS:
            b_, k_, own, mean, _c, _t = T.calibrate_field(ents, np.random.default_rng(s), n=20000, sample=draw)
            cals.append({"beta": b_, "kappa": k_, "top_own": own, "mean_salary": mean})
    finally:
        draw.close()
    timing["calibrations_s"] = round(time.time() - t1, 1)
    timing["total_s"] = round(time.time() - t0, 1)
    out.update({"smoke": SMOKE, "stamp": stamp, "timing": timing, "check_max_diff": check, "build_beta": beta, "build_kappa": kappa,
                "build_cal": list(got["cal"]), "calibrations": cals, "shortlist": int(len(short)),
                "candidates": K_prod, "allowed": int(allowed.sum()),
                "reference_quarters_top1_of_P_picks": [
                    [float(rq["top1"][p].mean()) for p in (top_of(m, "top1", ok) for m in configs["P"])] for rq in ref_q],
                "inputs": {"contest": CONTEST, "n_sims": N_SIMS, "opt_worlds": OPT_WORLDS, "seed": SEED,
                           "prescreen": PRE, "block": BLOCK, "blocks": N_BLOCKS, "reference": REF, "field": FIELD,
                           "shortlist_per_list": SHORT, "top": TOP, "field_seeds": FIELD_SEEDS, "cal_seeds": CAL_SEEDS}})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    log(f"[worlds] recommendation {out['recommendation']}; wrote {OUT} ({timing['total_s']:.0f}s)")
    return out


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    os.environ.setdefault("VIGIL_NO_BG", "1")
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 4)
