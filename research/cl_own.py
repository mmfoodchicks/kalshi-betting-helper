"""An ownership-driven classic field: does a field fitted to how the public
really drafted one week predict who it drafts the next?

The owner (2026-10-18), after the week-6 Millionaire export showed the
board's field badly off -- top-40 ownership MAE 8.3 points; Isaiah Likely
(TE, $4,800) 35% real against 5% modelled, the Panthers (DST, $2,500) 32%
against 6.5%, Bijan Robinson 24% against 39%; half the real field at the
$50,000 cap against a modelled mean of $49,110; 40% of real lineups with a
tight end at FLEX, which the sampler never builds -- "Let's get it."

The served field (dfs_tourney.classic_sample under calibrate_field) gives
every player one pull, beta x (projection - kappa x salary/1000): one
sharpness, one price sensitivity, for quarterbacks and defenses alike, set so
the most-owned player sits at 38% and the mean salary at $49,400. The public
does not draft that way: it pays for points at some positions and for value
(points per $1,000) at others. This model keeps the sampler's structure (the
quarterback first, the stack and bring-back rates, the cap-aware fill) and
gives each position its own pull on projection and on value, with a tight
end eligible at FLEX as DraftKings allows.

WRITTEN BEFORE ANY FIT WAS RUN (hashed into the scratch prereg log):

Model. The field is classic_sample with a tight end eligible at FLEX
(research/cl_mech.py's three substitutions, built from production's source
in production's namespace), every player's pull
    u_i = a[pos_i] x proj_i + b[pos_i] x proj_i / (salary_i / 1000)
for pos in QB, RB, WR, TE, DST (ten numbers), passed to the sampler as its
projection with beta 1 and kappa 0. Stack, bring-back and defense rates stay
the shipped CL_* constants.

Fit. Week 1 only: the served board's players for dg 151307
(research/data/dk_classic/served_board_151307.json: their projections and
salaries as the board read them) against the real any-slot ownership of the
$3.5M Millionaire (research/data/cl_field.json, 831,028 lineups). Loss: the
sum over the board's players of (sampled ownership - real ownership)^2, in
percentage points, the field sampled at 30,000 lineups on one fixed seed
(common random numbers). Nelder-Mead from a = 0.2, b = 0, at most 600
evaluations.

Test. Week 6 (research/data/cl_own_w6.json: the board served for dg 154941
and the $2.75M Millionaire's real ownership, 158,819 lineups). DECLARED: week
6's ownership was read before this rule -- its value-play pattern is why
value is a feature and a tight end may play FLEX; the fitted numbers never
see it. The fitted model's field is sampled at 300,000 lineups (seed fixed)
on week 6's players.
  Metrics: MAE in points over the board's players, and over the 40 players
  the real field owned most; for the fitted field and for the served board's
  own field (its field_pct).
  The fitted field SHIPS to the board when on week 6 it beats the served
  field on BOTH metrics by at least 25%, and the paired bootstrap (the 40
  players resampled, 2,000 draws, seed 7) puts the top-40 MAE difference's
  95% interval above zero. Otherwise the served field stays and the report
  says so.
  Confirmation, prospective: week 7's Sunday main-slate Millionaire (the
  largest classic GPP with an export, 2026-10-25). The same two metrics, the
  shipped model against the old model run offline on week 7's board players;
  if the shipped model loses on the top-40 metric there, it reverts.

Descriptive, not decisive: the in-sample week-1 fit beside the served week-1
field; both fields' salary mean and share at the cap, stack mix, bring-back,
tight end at FLEX, and the most-copied lineup's share, beside the real ones.

The CL_* constants research/field_refit.py decides from week 7 on keep that
rule; if this field ships, CL_FIELD_MAX_OWN and CL_SALARY_USED stop steering
the board (the pulls do), so field_refit reports them rather than ships them.

python3 -m research.cl_own [workers]
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")
import numpy as np                                  # noqa: E402

OUT = os.path.join(ROOT, "research", "data", "cl_own.json")
W1_BOARD = os.path.join(ROOT, "research", "data", "dk_classic", "served_board_151307.json")
W1_REAL = os.path.join(ROOT, "research", "data", "cl_field.json")
W6 = os.path.join(ROOT, "research", "data", "cl_own_w6.json")
POS = ("QB", "RB", "WR", "TE", "DST")
FIT_N, TEST_N, FIT_SEED, TEST_SEED = 30000, 300000, 20261018, 20261019
MAX_EVALS, BLOCK = 600, 1000
START = [0.2] * 5 + [0.0] * 5
SHIP_GAIN, BOOT, BOOT_SEED = 0.25, 2000, 7
# CLW-style smoke: the same code on a sliver, to its own ignored file
if os.environ.get("OWN_SMOKE") == "1":
    FIT_N, TEST_N, MAX_EVALS = 3000, 6000, 15
    OUT = os.path.join(ROOT, "research", "data", "cl_own_smoke.json")

from research.cl_mech import flex_te_sampler        # noqa: E402
SAMPLER, SAMPLER_RECEIPT = flex_te_sampler()
_W = {}


def _init(players):
    _W["base"] = players
    _W["theta"] = None


def _block(job):
    """One seeded block of the field at pulls theta (computed once per theta per process)."""
    seed, n, theta = job
    if _W["theta"] != theta:
        _W["players"], _W["theta"] = pulls(_W["base"], theta), theta
    return SAMPLER(_W["players"], n, np.random.default_rng(seed), 1.0, 0.0, report={})


def pulls(players, theta):
    """The players with their pull as the sampler's projection."""
    a = dict(zip(POS, theta[:5]))
    b = dict(zip(POS, theta[5:]))
    out = []
    for p in players:
        proj, sal = float(p.get("proj") or 0.0), max(1.0, float(p["salary"]))
        q = dict(p)
        q["proj"] = a[p["pos"]] * proj + b[p["pos"]] * proj / (sal / 1000.0)
        out.append(q)
    return out


def sample(ex, theta, n, seed):
    """n field lineups at pulls theta, in seeded blocks over the pool (the same
    rows on any process count; the pool's initializer holds the players)."""
    theta = tuple(float(x) for x in theta)
    jobs = [((seed, k), min(BLOCK, n - s), theta) for k, s in enumerate(range(0, n, BLOCK))]
    parts = list(ex.map(_block, jobs)) if ex is not None else [_block(j) for j in jobs]
    return np.concatenate(parts, axis=0)


def ownership(idx, P):
    return 100.0 * np.bincount(idx.ravel(), minlength=P) / max(1, len(idx))


def shape(idx, players):
    """Salary, cap share, stacks, bring-back, TE at FLEX and the top copy share of a field sample."""
    import dfs_tourney as T
    fs = T.field_stats(idx, players)
    sal = np.asarray([int(p["salary"]) for p in players])[idx].sum(axis=1)
    pc = np.asarray([p["pos"] for p in players])
    rows = np.sort(idx, axis=1)
    _u, counts = np.unique(rows, axis=0, return_counts=True)
    return {"salary_mean": float(sal.mean()), "at_cap_pct": float(100 * (sal == 50000).mean()),
            "stack_pct": [round(100 * x, 2) for x in fs["stack_dist"]], "bring_back_pct": round(100 * fs["bring_back"], 2),
            "te_at_flex_pct": float(100 * (pc[idx[:, 7]] == "TE").mean()),
            "top_copy_share_pct": float(100 * counts.max() / len(idx))}


def nelder_mead(f, x0, max_evals, step=0.1):
    n = len(x0)
    pts = [np.asarray(x0, dtype=float)]
    for i in range(n):
        x = np.asarray(x0, dtype=float).copy()
        x[i] += step
        pts.append(x)
    vals = [f(x) for x in pts]
    evals = n + 1
    while evals < max_evals:
        order = np.argsort(vals)
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        c = np.mean(pts[:-1], axis=0)
        xr = c + (c - pts[-1])
        fr = f(xr); evals += 1
        if fr < vals[0]:
            xe = c + 2.0 * (c - pts[-1])
            fe = f(xe); evals += 1
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = c + 0.5 * (pts[-1] - c)
            fc = f(xc); evals += 1
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                for i in range(1, len(pts)):
                    pts[i] = pts[0] + 0.5 * (pts[i] - pts[0])
                    vals[i] = f(pts[i]); evals += 1
        if max(vals) - min(vals) < 1e-3:
            break
    i = int(np.argmin(vals))
    return pts[i], vals[i], evals


def load():
    b1 = json.load(open(W1_BOARD))
    b1 = b1.get("board", b1)
    real1 = json.load(open(W1_REAL))["empirical"]["ownership_any_slot_pct"]
    w6 = json.load(open(W6))
    keep = ("name", "pos", "team", "opp", "salary", "proj", "depth", "field_only", "field_pct")
    p1 = [{k: p.get(k) for k in keep} for p in b1["players"]]
    return p1, real1, w6["players"], w6["real_ownership_any_slot_pct"]


def mae(a, b):
    return float(np.mean(np.abs(np.asarray(a) - np.asarray(b))))


def main(workers=4, log=print):
    import concurrent.futures as cf
    t0 = time.time()
    p1, real1, p6, real6 = load()
    r1 = np.asarray([float(real1.get(p["name"], 0.0)) for p in p1])
    r6 = np.asarray([float(real6.get(p["name"], 0.0)) for p in p6])
    out = {"sampler": SAMPLER_RECEIPT, "inputs": {"fit_n": FIT_N, "test_n": TEST_N, "fit_seed": FIT_SEED,
                                                  "test_seed": TEST_SEED, "max_evals": MAX_EVALS, "start": START}}
    trace = []
    with cf.ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(p1,)) as ex1:
        def loss(theta):
            own = ownership(sample(ex1, theta, FIT_N, FIT_SEED), len(p1))
            v = float(((own - r1) ** 2).sum())
            trace.append([round(float(x), 5) for x in theta] + [round(v, 3)])
            if len(trace) % 25 == 0:
                log(f"[own] {len(trace)} evaluations, best loss {min(t[-1] for t in trace):.1f} ({time.time() - t0:.0f}s)")
            return v
        theta, best, evals = nelder_mead(loss, START, MAX_EVALS)
    out["fit"] = {"theta": {"a": dict(zip(POS, map(float, theta[:5]))), "b": dict(zip(POS, map(float, theta[5:])))},
                  "loss": best, "evaluations": evals, "trace_tail": trace[-20:]}
    log(f"[own] fitted after {evals} evaluations: {out['fit']['theta']} ({time.time() - t0:.0f}s)")
    # in-sample week 1, and the test on week 6
    res = {}
    for wk, players, real, served_key in (("w1", p1, r1, "field_pct"), ("w6", p6, r6, "field_pct")):
        pl = pulls(players, theta)
        with cf.ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(players,)) as ex:
            idx = sample(ex, theta, TEST_N, TEST_SEED)
        own = ownership(idx, len(pl))
        served = np.asarray([float(p.get(served_key) or 0.0) for p in players])
        top = np.argsort(-real)[:40]
        res[wk] = {"mae_all": {"fitted": mae(own, real), "served": mae(served, real)},
                   "mae_top40": {"fitted": mae(own[top], real[top]), "served": mae(served[top], real[top])},
                   "shape_fitted": shape(idx, pl),
                   "players": [{"name": players[i]["name"], "pos": players[i]["pos"], "salary": players[i]["salary"],
                                "proj": players[i]["proj"], "real": round(float(real[i]), 2),
                                "fitted": round(float(own[i]), 2), "served": round(float(served[i]), 2)}
                               for i in np.argsort(-real)[:60]]}
        if wk == "w6":
            rng = np.random.default_rng(BOOT_SEED)
            d = []
            for _ in range(BOOT):
                s = rng.choice(top, size=len(top), replace=True)
                d.append(mae(served[s], real[s]) - mae(own[s], real[s]))
            lo, hi = np.percentile(d, [2.5, 97.5])
            res[wk]["boot_top40_served_minus_fitted"] = {"lo": float(lo), "hi": float(hi), "mean": float(np.mean(d))}
        log(f"[own] {wk}: MAE all fitted {res[wk]['mae_all']['fitted']:.2f} vs served {res[wk]['mae_all']['served']:.2f}; "
            f"top-40 fitted {res[wk]['mae_top40']['fitted']:.2f} vs served {res[wk]['mae_top40']['served']:.2f}")
    out["weeks"] = res
    w6 = res["w6"]
    gain_all = 1.0 - w6["mae_all"]["fitted"] / w6["mae_all"]["served"]
    gain_top = 1.0 - w6["mae_top40"]["fitted"] / w6["mae_top40"]["served"]
    ships = bool(gain_all >= SHIP_GAIN and gain_top >= SHIP_GAIN and w6["boot_top40_served_minus_fitted"]["lo"] > 0)
    out["verdict"] = {"gain_all": gain_all, "gain_top40": gain_top, "ships": ships}
    out["smoke"] = os.environ.get("OWN_SMOKE") == "1"
    out["timing_s"] = round(time.time() - t0, 1)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    log(f"[own] verdict: gain all {gain_all:+.1%}, top-40 {gain_top:+.1%}, ships {ships}; wrote {OUT}")
    return out


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 4)
