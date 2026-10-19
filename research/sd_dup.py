"""The showdown field's flatness, and what it does to tonight's lists (2026-10-19).

The owner, the morning of WAS @ SF on Monday night: "anything else we can do
before Monday night's game?" Sunday night's real field (DAL @ GB, the $1M
Sunday Night Showdown, 78,189 lineups; research/data/dk_fields/
dk_field_196786588.json) was read against the board served 27 minutes before
its lock (research/data/sd_dup_snf_board.json, its pool copied from the
snapshot workflow's errors/tourney-154959.json). The served field rebuilds
exactly from that pool (780,492 lineups, beta 0.4731), and it was flatter
than the real one: a distinct-lineup pair collision of 2.07e-4 against
5.05e-4, the most-copied build 156 copies against 314, salary spent $48,658
against $49,265. S7 found the same failures on three older showdowns
(research/reports/s7_beta.md: 3.7-11x too many effective lineups, too much
salary left, the captain mix off), so this is not news about the family; it
is one more game, and that game was READ before this was written.

DESCRIPTIVE. Nothing here changes a board, and nothing here is the S7
prospective test (research/s7_prospective.py; tonight's slate was captured
for it at 2026-10-19 13:07 UTC, 11.1 h before lock, and is scored only after
the game). What runs, decided before any of tonight's numbers were read:

  1. SNF: the served field against the real one -- collision, distinct
     lineups, the share of entries in duplicated lineups, the top build's
     copies, salary, team split, and captain / flex ownership MAE for the
     board's field share and its best-lineup share (opt_*_pct) over the
     board's players. And the one-knob value (FIELD_TOP_SHARE) whose softmax
     pair collision equals SNF's real one: the hot knob.
  2. Tonight: the real build_nfl_showdown on dg 154960 to its scoring pass
     (60,000 games, the board's count, seed 7). The lineups we may enter are
     scored twice against the same games, once against the served field
     (0.2%) and once against the field at the hot knob. For each list --
     top_win (win_sole), top_top1, top_top01, top_money (money, ties by ev),
     top_ev -- the top 20 under each field, their overlap, and the served
     picks' figures read under the hot field beside their own.
  What this can say: which lists move when the field is as concentrated as
  Sunday's real one was, within the softmax family S7 showed is the wrong
  shape. What it cannot say: what the real field will be tonight.

python3 -m research.sd_dup [dg] [workers]
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "data", "sd_dup.json")
SNF_BOARD = os.path.join(ROOT, "research", "data", "sd_dup_snf_board.json")
SNF_FIELD = os.path.join(ROOT, "research", "data", "dk_fields", "dk_field_196786588.json")
DG, N_SIMS, SEED, TOP = 154960, 60000, 7, 20
LISTS = {"top_win": "win_sole", "top_top1": "top1", "top_top01": "top01", "top_money": "money", "top_ev": "ev"}


def snf(log=print):
    import numpy as np
    import dfs_tourney as T
    import dk_standings as DS
    b = json.load(open(SNF_BOARD))
    d = json.load(open(SNF_FIELD))
    P = b["players"]
    idx, _W, _ok = T.enumerate_showdown(P, 50000, cpt_mult=1.5)
    f, beta = T.field_weights(P, idx, cpt_mult=1.5)
    assert len(idx) == b["lineups_legal"] and round(beta, 4) == b["beta"], (len(idx), beta)
    N = int(d["with_lineup"])
    sal = np.asarray([p["salary"] for p in P])
    csal = np.asarray([p["cpt_salary"] for p in P])
    lsal = csal[idx[:, 0]] + sal[idx[:, 1:]].sum(axis=1)
    o = np.argsort(lsal, kind="stable")
    team = np.asarray([p["team"] for p in P])
    n0 = (team[idx] == sorted(set(team))[0]).sum(axis=1)
    big = np.maximum(n0, 6 - n0)

    def shape(f):
        return {"pair_collision": float((f ** 2).sum()),
                "distinct_expected": round(float((1.0 - (1.0 - f) ** N).sum())),
                "entries_in_duplicated_pct": round(100.0 * float((f * (1.0 - (1.0 - f) ** (N - 1))).sum()), 2),
                "top_copies": round(N * float(f.max()), 1),
                "salary_mean": round(float((f * lsal).sum())),
                "salary_median": int(lsal[o][np.searchsorted(np.cumsum(f[o]), 0.5)]),
                "team_split_pct": {k: round(100.0 * float(f[big == v].sum()), 2) for k, v in (("3-3", 3), ("4-2", 4), ("5-1", 5))}}
    real_c = float(d["duplicates"]["distinct_pair_collision"])
    lo, hi = 0.002, 0.02
    for _ in range(50):                     # collision rises with the knob
        mid = 0.5 * (lo + hi)
        fm, _b = T.field_weights(P, idx, cpt_mult=1.5, top_share=mid)
        lo, hi = (mid, hi) if float((fm ** 2).sum()) < real_c else (lo, mid)
    hot = round(0.5 * (lo + hi), 5)
    f_hot, beta_hot = T.field_weights(P, idx, cpt_mult=1.5, top_share=hot)
    real = {DS._norm(r["name"]): r for r in d["ownership"]}
    R = [real.get(DS._norm(p["name"])) for p in P]
    rc = [r["cpt_pct"] if r else 0.0 for r in R]
    rf = [(r["pct"] - r["cpt_pct"]) if r else 0.0 for r in R]

    def mae(x, y):
        return round(sum(abs(a - c) for a, c in zip(x, y)) / len(P), 3)
    out = {"served": shape(f), "hot_knob": hot, "hot_beta": round(beta_hot, 4), "hot": shape(f_hot),
           "real": {"pair_collision": real_c, "distinct": d["duplicates"]["distinct_lineups"],
                    "entries_in_duplicated_pct": d["duplicates"]["entries_in_duplicated_lineups_pct"],
                    "top_copies": d["duplicates"]["max_copies"], "salary_mean": d["shape"]["salary_mean"],
                    "salary_median": d["shape"]["salary_median"], "team_split_pct": d["shape"]["team_split_pct"]},
           "entries": N, "players": len(P), "players_outside_real_top40": sum(1 for r in R if r is None),
           "ownership_mae": {"cpt": {"field": mae([p["field_cpt_pct"] for p in P], rc), "opt": mae([p["opt_cpt_pct"] for p in P], rc)},
                             "flex": {"field": mae([p["field_flex_pct"] for p in P], rf), "opt": mae([p["opt_flex_pct"] for p in P], rf)}}}
    log(f"[sdd] SNF: collision served {out['served']['pair_collision']:.2e} real {real_c:.2e}; hot knob {hot}")
    return out


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


def top_of(m, key, pos, k=TOP):
    import numpy as np
    if key == "money":
        order = np.lexsort((-m["ev"][pos], -np.round(m["money"][pos], 6)))
    else:
        order = np.argsort(-m[key][pos], kind="stable")
    return pos[order][:k]


def tonight(hot, dg=DG, workers=4, log=print):
    import numpy as np
    import dfs_tourney as T
    import nfl_dfs
    t0 = time.time()
    got = capture(dg, workers, log=log)
    W, X, f, grid, ents, idx = got["W"], got["X"], got["f"], got["grid"], got["ents"], got["idx"]
    ok = np.where(np.asarray(got["allowed"]))[0]
    f_hot, beta_hot = T.field_weights(ents, idx, cpt_mult=nfl_dfs.CPT_MULT, top_share=hot)
    timing = {"capture_s": round(time.time() - t0, 1)}
    Wc = W[ok]
    res = {}
    for name, ff in (("served", f), ("hot", f_hot)):
        t1 = time.time()
        r, _o, _n = T.run_vs_field(Wc, W, ff, X, [grid], workers=workers)
        res[name] = r[0]
        timing[f"score_{name}_s"] = round(time.time() - t1, 1)
        log(f"[sdd] scored against the {name} field ({timing[f'score_{name}_s']:.0f}s)")
    pos = np.arange(len(ok))
    C = float(grid["C"])

    def names(i):
        return [ents[idx[i, 0]]["name"] + " (CPT)"] + [ents[idx[i, k]]["name"] for k in range(1, 6)]
    out = {"lists": {}}
    for lst, key in LISTS.items():
        ps, ph = top_of(res["served"], key, pos), top_of(res["hot"], key, pos)
        out["lists"][lst] = {
            "overlap_top20": len(set(ps.tolist()) & set(ph.tolist())),
            "overlap_top5": len(set(ps[:5].tolist()) & set(ph[:5].tolist())),
            "served_picks": {"under_served": float(res["served"][key][ps].mean()), "under_hot": float(res["hot"][key][ps].mean())},
            "hot_picks": {"under_served": float(res["served"][key][ph].mean()), "under_hot": float(res["hot"][key][ph].mean())},
            "served_top5": [names(int(ok[i])) for i in ps[:5]], "hot_top5": [names(int(ok[i])) for i in ph[:5]],
            "served_top5_copies": [[round(C * float(f[ok[i]]), 1), round(C * float(f_hot[ok[i]]), 1)] for i in ps[:5]]}
    timing["total_s"] = round(time.time() - t0, 1)
    out.update({"dg": dg, "n_sims": int(X.shape[1]), "seed": SEED, "lineups_legal": int(len(idx)), "lineups_allowed": int(len(ok)),
                "beta_hot": round(beta_hot, 4), "hot_knob": hot, "timing": timing,
                "fee": float(grid["entry_fee"]), "contest_capacity": int(C)})
    return out


def main(dg=DG, workers=4, log=print):
    s = snf(log=log)
    t = tonight(s["hot_knob"], dg=dg, workers=workers, log=log)
    out = {"snf": s, "tonight": t, "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)
    log(f"[sdd] wrote {OUT}")
    return out


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    os.environ.setdefault("VIGIL_NO_BG", "1")
    main(int(sys.argv[1]) if len(sys.argv) > 1 else DG, int(sys.argv[2]) if len(sys.argv) > 2 else 4)
