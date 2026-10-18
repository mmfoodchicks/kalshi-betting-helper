"""Tonight's showdown board, checked on games it never saw (2026-10-18).

The owner, before DAL @ GB on Sunday night: "what about for a single
showdown ... I wanna test it for the packers game tonight." The classic test
(research/cl_worlds.py) found the board's own first-place and EV figures read
3-4x high in-sample and that more worlds helped the noisy lists a little. A
showdown board differs in one way that matters here: its field is the whole
legal universe, weighted, not a sample, so every bit of noise in its lists
comes from the simulated games. This measures, on tonight's real slate and
the board's own code path, how much its lists' figures overstate and how
steady its picks are.

DESCRIPTIVE, written before any number was read: nothing here changes a
board. The real build_nfl_showdown runs to its scoring pass at 180,000 games
(model the board's, seed 7); its lineups, field weights and payout grid are
kept and the games cut into three blocks of 60,000 (the board's own count).
For each list -- top_win (win_sole), top_top1, top_top01, top_money (money,
ties by ev), top_ev -- the top 20 of the lineups we may enter are picked on
block A, on block B and on A+B together, and every pick is valued on block C.
Reported: each pick's value on C beside its value where it was picked (the
inflation), the overlap of A's and B's picks (the steadiness), and A+B's
value on C against A's and B's (what doubling the games buys, one replicate,
so a hint, never a verdict). The 20-entry portfolio is picked on A as the
board picks it and its P(any entry in the top 1%) read on A and on C.

python3 -m research.sd_worlds [dg] [workers]
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "sd_worlds.json")
DG, N_SIMS, BLOCK, SEED, TOP = 154959, 180000, 60000, 7, 20
LISTS = {"top_win": "win_sole", "top_top1": "top1", "top_top01": "top01", "top_money": "money", "top_ev": "ev"}
SMOKE = os.environ.get("SDW_SMOKE") == "1"
if SMOKE:
    N_SIMS, BLOCK = 6000, 2000
    OUT = os.path.join(ROOT, "research", "data", "sd_worlds_smoke.json")


def capture(dg, workers, log=print):
    import dfs_tourney as T
    got = {}
    enum0, run0 = T.enumerate_showdown, T.run

    def enum(ents, *a, **k):
        out = enum0(ents, *a, **k)
        got.update(ents=ents, idx=out[0], allowed=out[2])
        return out

    class Stop(Exception):
        pass

    def run(W, X, f, grid, **k):
        got.update(W=W, X=X, f=f, grid=grid)
        raise Stop()
    T.enumerate_showdown, T.run = enum, run
    try:
        T.build_nfl_showdown(dg, n_sims=N_SIMS, workers=workers, seed=SEED, log=log)
    except Stop:
        pass
    finally:
        T.enumerate_showdown, T.run = enum0, run0
    return got


def top_of(m, key, ok, k=TOP):
    import numpy as np
    if key == "money":
        order = np.lexsort((-m["ev"][ok], -np.round(m["money"][ok], 6)))
    else:
        order = np.argsort(-m[key][ok], kind="stable")
    return ok[order][:k]


def main(dg=DG, workers=4, log=print):
    import numpy as np
    import dfs_tourney as T
    t0 = time.time()
    got = capture(dg, workers, log=log)
    W, X, f, grid, allowed = got["W"], got["X"], got["f"], got["grid"], np.asarray(got["allowed"])
    N = X.shape[1]
    assert N >= 3 * BLOCK, N
    timing = {"capture_s": round(time.time() - t0, 1)}
    ok = np.where(allowed)[0]
    Wc = W[ok]
    pos = np.arange(len(ok))
    blocks = {}
    for name, b in (("A", 0), ("B", 1), ("C", 2)):
        t1 = time.time()
        res, _o, _n = T.run_vs_field(Wc, W, f, np.ascontiguousarray(X[:, b * BLOCK:(b + 1) * BLOCK]), [grid],
                                     workers=workers)
        blocks[name] = res[0]
        timing[f"block_{name}_s"] = round(time.time() - t1, 1)
        log(f"[sdw] block {name} scored ({timing[f'block_{name}_s']:.0f}s)")
    AB = {k: 0.5 * (blocks["A"][k] + blocks["B"][k]) for k in blocks["A"]}
    C = blocks["C"]
    out = {"lists": {}}
    for lst, key in LISTS.items():
        row = {}
        for sel_name, m in (("A", blocks["A"]), ("B", blocks["B"]), ("AB", AB)):
            p = top_of(m, key, pos)
            row[sel_name] = {"shown": float(m[key][p].mean()), "fresh": float(C[key][p].mean()),
                             "picks": [int(ok[i]) for i in p]}
        row["overlap_AB"] = len(set(row["A"]["picks"]) & set(row["B"]["picks"]))
        best_C = top_of(C, key, pos)
        row["C_own_top20"] = float(C[key][best_C].mean())       # in-sample on C: an upper reference, biased up
        out["lists"][lst] = row
    # the portfolio, picked on A the board's way, read on A and on C
    t1 = time.time()
    cand = pos[np.argsort(-blocks["A"]["top1"])][:1200]
    XA = np.ascontiguousarray(X[:, :BLOCK])
    XC = np.ascontiguousarray(X[:, 2 * BLOCK:3 * BLOCK])
    (chosen, p_any_A), = T.portfolio_vs_field(Wc, W, f, XA, [grid], cand, 20, workers=workers)
    picked = cand[np.asarray(chosen, dtype=np.int64)]
    (_ch, p_any_C), = T.portfolio_vs_field(Wc, W, f, XC, [grid], picked, len(picked), workers=workers)
    out["portfolio"] = {"p_any_top1_A": [float(v) for v in p_any_A], "p_any_top1_C_all20": float(p_any_C[-1]),
                        "picks": [int(ok[i]) for i in picked]}
    timing["portfolio_s"] = round(time.time() - t1, 1)
    timing["total_s"] = round(time.time() - t0, 1)
    ents, idx = got["ents"], got["idx"]

    def names(i):
        return [ents[idx[i, 0]]["name"] + " (CPT)"] + [ents[idx[i, k]]["name"] for k in range(1, 6)]
    for lst in out["lists"]:
        out["lists"][lst]["A_top5_names"] = [names(i) for i in out["lists"][lst]["A"]["picks"][:5]]
    out.update({"smoke": SMOKE, "dg": dg, "n_sims": N, "block": BLOCK, "seed": SEED,
                "lineups_legal": int(len(idx)), "lineups_allowed": int(len(ok)), "timing": timing,
                "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)
    log(f"[sdw] wrote {OUT} ({timing['total_s']:.0f}s)")
    return out


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    os.environ.setdefault("VIGIL_NO_BG", "1")
    main(int(sys.argv[1]) if len(sys.argv) > 1 else DG, int(sys.argv[2]) if len(sys.argv) > 2 else 4)
