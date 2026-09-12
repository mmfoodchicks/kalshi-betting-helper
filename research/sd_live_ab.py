"""The same live showdown board, built twice: legacy and legacy-latent-discrete.

A controlled board made of fourteen hand-picked players and four thousand
arbitrary lineups can show that the lattice defect exists. It cannot say what
the defect costs, because the thing that decides that is the shape of a REAL
field over a REAL legal universe: 777,056 lineups on DEN @ KC, 427,048 on
DAL @ NYG, the salary cap, the captain rules, the depth gate, and an 88,000
entry contest's payout table. So this runs the production pipeline -- the same
`enumerate_showdown`, `field_weights`, `payout_grid` and `run_vs_field` the
served board is built from -- and changes exactly one thing: whether the player
pool came out of the discrete scorer.

Each (board, mode) runs in its OWN process, so peak RSS is that build's and not
a high-water mark left behind by the other mode.

Nothing here is promoted, and no column here is authoritative: the showdown
money gate is shut on the field model independently of the lattice, so expected
payout and the split-payout haircut below are diagnostics for the size of the
change, not forecasts. Rank on top 1% and top 0.1%.

    python3 -m research.sd_live_ab all  <feed dir> [dg ...]
    python3 -m research.sd_live_ab one  <feed dir> <dg> <legacy|discrete>
"""
import json
import os
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")

N_SIMS = 12000          # nfl_dfs._SD_SIMS: what a served showdown board runs on
SEED = 20260912
TOPN = 20
PORT_K = 20
CANDIDATES = 1200
DGS = (153086, 153085)


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def structure(ents, row):
    """5-1, 4-2 or 3-3: how a six-man showdown lineup splits across the two
    teams. It is the first thing anyone looks at on a single-game roster, and a
    scoring change that moved it would be a strategy change, not a money one."""
    from collections import Counter
    c = Counter(ents[int(i)].get("team") for i in row)
    n = sorted(c.values(), reverse=True)
    return "-".join(str(x) for x in n)


def key(ents, row):
    return (ents[int(row[0])]["name"],
            tuple(sorted(ents[int(i)]["name"] for i in row[1:])))


def se(p, n):
    """Binomial standard error of a per-world mean. Every probability below is
    an average of indicators over `n` worlds, so this is the resolution the
    comparison has -- quoted so no difference smaller than it gets read as real."""
    return float(np.sqrt(max(p, 0.0) * max(1.0 - p, 0.0) / max(n, 1)))


def one(feed_dir, dg, mode, log=print):
    import resource
    import dfs_tourney as T
    from research import sd_board
    discrete = (mode == "discrete")
    # The ORDERING question cannot be read without a noise floor. "legacy_b" is
    # legacy again at the same depth on a different generator state, so the
    # top-20 reshuffling it produces is what Monte Carlo alone does to this
    # board -- and anything the discrete mode does inside that is not a
    # strategy change. Same discipline as the correlation control in
    # research/sd_outliers; "legacy" and "discrete" are untouched by it.
    seed = SEED + (1 if mode == "legacy_b" else 0)
    S = sd_board.feeds(feed_dir)
    S._cache.clear()
    slate, contest = sd_board.slate_and_contest(dg)
    if not slate or not contest:
        raise SystemExit(f"draft group {dg} has no slate or contest")
    ents, pool_secs, _rss0 = sd_board.ents_for(slate, sd_board.WEEK, discrete,
                                              n_sims=N_SIMS, seed=seed, log=log)
    idx, W, allowed = sd_board.universe(ents)
    if idx is None:
        raise SystemExit("no legal lineups")
    f, beta = T.field_weights(ents, idx, cpt_mult=1.5)
    grid, C = sd_board.grid_for(contest)
    N = min(len(e["arr"]) for e in ents)
    X = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    chunk = T.chunk_for(len(idx), 500)
    log(f"[AB] {mode:8s} dg {dg}: {len(idx):,} lineups, {int(allowed.sum()):,} enterable, "
        f"{N:,} worlds, beta {beta:.4f}, {chunk} worlds a chunk")
    t0 = time.time()
    res = T.run(W, X, f, grid, chunk=chunk)
    score_secs = time.time() - t0
    ok = np.where(allowed)[0]
    top1, top01 = np.asarray(res["top1"]), np.asarray(res["top01"])
    ev, evn = np.asarray(res["ev"]), np.asarray(res["ev_notie"])
    win, sole = np.asarray(res["win"]), np.asarray(res["win_sole"])
    order = ok[np.argsort(-top1[ok])]
    cand = order[:CANDIDATES]
    t1 = time.time()
    chosen, p_any = T.portfolio(W, X, f, grid, cand, PORT_K, chunk=chunk)
    port_secs = time.time() - t1
    port = [int(cand[j]) for j in chosen[:PORT_K]]
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    def row(i):
        return {"captain": ents[int(idx[i, 0])]["name"],
                "captain_pos": ents[int(idx[i, 0])]["pos"],
                "captain_team": ents[int(idx[i, 0])]["team"],
                "flex": sorted(ents[int(idx[i, k])]["name"] for k in range(1, 6)),
                "structure": structure(ents, idx[i]),
                "top1_pct": round(100.0 * float(top1[i]), 3),
                "top01_pct": round(100.0 * float(top01[i]), 4),
                "win_any_pct": round(100.0 * float(win[i]), 5),
                "win_sole_pct": round(100.0 * float(sole[i]), 5),
                "ev": round(float(ev[i]), 2), "ev_notie": round(float(evn[i]), 2)}

    from collections import Counter
    top = [row(int(i)) for i in order[:TOPN]]
    pr = [row(i) for i in port]
    cpt_port = Counter(r["captain"] for r in pr)
    expo = Counter()
    for i in port:
        for k in range(6):
            expo[ents[int(idx[i, k])]["name"]] += 1
    # Two DIFFERENT tie quantities, and conflating them cost a report revision
    # (2026-09-12), so they are named apart here.
    #
    # best.split_haircut_pct is a PAYOUT haircut on ONE lineup: the share of the
    # best lineup's untied expected payout that tie-splitting removes. It runs
    # 84-97% on a live primetime board. It is NOT an incidence and must never be
    # quoted as "first place is shared X% of the time" -- it was, and it became
    # the argument that the lattice defect was economically cheap.
    #
    # tie.mean_shared_first_pct_top200 is a CANDIDATE-LEVEL probability: for each
    # of THIS build's own top-200 lineups by top-1% rank, the mass on a shared
    # first place, averaged. It is about 200 hand-picked lineups, not about the
    # contest. Nothing in this module estimates the contest-wide probability that
    # first place is shared; that needs an estimator over the whole field and
    # there isn't one. Both are experimental, like every money column here.
    best = int(order[0])
    tie_share = [(float(win[i]) - float(sole[i])) for i in order[:200]]
    out = {"mode": mode, "draft_group_id": int(dg), "contest": contest.get("name"),
           "entries": int(C), "entry_fee": float(contest.get("entry_fee") or 0.0),
           "players": len(ents), "legal_lineups": int(len(idx)),
           "enterable": int(allowed.sum()), "worlds": int(N),
           "field_beta": round(float(beta), 4), "seed": int(seed),
           "discrete_version": S.DISCRETE_VERSION,
           "sim_stamp": S.sim_stamp(N, False, discrete),
           "build": {"pool_seconds": round(pool_secs, 1),
                     "score_seconds": round(score_secs, 1),
                     "portfolio_seconds": round(port_secs, 1),
                     "peak_rss_mb": round(rss, 1),
                     "chunk_worlds": int(chunk)},
           "best": {"top1_pct": round(100.0 * float(top1[best]), 3),
                    "top1_se_pct": round(100.0 * se(float(top1[best]), N), 3),
                    "top01_pct": round(100.0 * float(top01[best]), 4),
                    "top01_se_pct": round(100.0 * se(float(top01[best]), N), 4),
                    "ev": round(float(ev[best]), 2),
                    "ev_notie": round(float(evn[best]), 2),
                    "split_haircut_pct": (round(100.0 * (1.0 - float(ev[best]) / float(evn[best])), 3)
                                          if evn[best] else None)},
           "tie": {"mean_shared_first_pct_top200": round(100.0 * float(np.mean(tie_share)), 6),
                   "max_shared_first_pct_top200": round(100.0 * float(np.max(tie_share)), 6),
                   "mean_any_first_pct_top200": round(100.0 * float(np.mean(win[order[:200]])), 6),
                   "mean_sole_first_pct_top200": round(100.0 * float(np.mean(sole[order[:200]])), 6)},
           "top20": top,
           "portfolio": {"k": PORT_K, "p_any_top1_pct": round(100.0 * float(p_any[PORT_K - 1]), 2),
                         "entries": pr,
                         "captains": [{"name": n, "entries": c} for n, c in cpt_port.most_common()],
                         "exposure": [{"name": n, "slots": c} for n, c in expo.most_common(12)],
                         "structures": dict(Counter(r["structure"] for r in pr))},
           "top20_structures": dict(Counter(r["structure"] for r in top)),
           # the shape of the whole enterable universe, as a reference for what
           # the top twenty and the portfolio picked out of it
           "enterable_structures": dict(Counter(structure(ents, idx[int(i)]) for i in ok))}
    with open(os.path.join(DATA, f"_ab_{dg}_{mode}.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[AB] {mode:8s} dg {dg}: best top1 {out['best']['top1_pct']:.2f}% "
        f"(+-{out['best']['top1_se_pct']:.2f}) top0.1% {out['best']['top01_pct']:.3f}% "
        f"EV ${out['best']['ev']:,.2f} split haircut {out['best']['split_haircut_pct']}% "
        f"| pool {pool_secs:.0f}s score {score_secs:.0f}s RSS {rss:,.0f} MB")
    return out


def compare(a, b):
    """What changed between two builds of the same board."""
    ka = [(r["captain"], tuple(r["flex"])) for r in a["top20"]]
    kb = [(r["captain"], tuple(r["flex"])) for r in b["top20"]]
    pos_b = {k: i for i, k in enumerate(kb)}
    moves = [abs(i - pos_b[k]) for i, k in enumerate(ka) if k in pos_b]
    pa = [(r["captain"], tuple(r["flex"])) for r in a["portfolio"]["entries"]]
    pb = [(r["captain"], tuple(r["flex"])) for r in b["portfolio"]["entries"]]
    cap_a = {c["name"]: c["entries"] for c in a["portfolio"]["captains"]}
    cap_b = {c["name"]: c["entries"] for c in b["portfolio"]["captains"]}
    caps = sorted(set(cap_a) | set(cap_b))
    return {
        "top20_slots_identical": sum(1 for x, y in zip(ka, kb) if x == y),
        "top20_overlap": len(set(ka) & set(kb)),
        "top20_rank_move_median": (round(float(np.median(moves)), 1) if moves else None),
        "top20_rank_move_max": (int(max(moves)) if moves else None),
        "top20_same_captain_slots": sum(1 for x, y in zip(a["top20"], b["top20"])
                                        if x["captain"] == y["captain"]),
        "top1_best_abs_diff_pp": round(abs(b["best"]["top1_pct"] - a["best"]["top1_pct"]), 3),
        "top1_best_se_pp": round(max(a["best"]["top1_se_pct"], b["best"]["top1_se_pct"]), 3),
        "top01_best_abs_diff_pp": round(abs(b["best"]["top01_pct"] - a["best"]["top01_pct"]), 4),
        "top01_best_se_pp": round(max(a["best"]["top01_se_pct"], b["best"]["top01_se_pct"]), 4),
        "ev_best_pct_diff": (round(100.0 * (b["best"]["ev"] - a["best"]["ev"]) / a["best"]["ev"], 2)
                             if a["best"]["ev"] else None),
        "split_haircut_pp": (round(b["best"]["split_haircut_pct"] - a["best"]["split_haircut_pct"], 3)
                             if a["best"]["split_haircut_pct"] is not None else None),
        "shared_first_ratio_top200": (
            round(b["tie"]["mean_shared_first_pct_top200"]
                  / a["tie"]["mean_shared_first_pct_top200"], 2)
            if a["tie"]["mean_shared_first_pct_top200"] else None),
        "portfolio_overlap": len(set(pa) & set(pb)),
        "portfolio_captains": {c: [cap_a.get(c, 0), cap_b.get(c, 0)] for c in caps},
        "portfolio_structures": {"legacy": a["portfolio"]["structures"],
                                 "discrete": b["portfolio"]["structures"]},
        "top20_structures": {"legacy": a["top20_structures"], "discrete": b["top20_structures"]},
        "build": {"pool_seconds": [a["build"]["pool_seconds"], b["build"]["pool_seconds"]],
                  "score_seconds": [a["build"]["score_seconds"], b["build"]["score_seconds"]],
                  "peak_rss_mb": [a["build"]["peak_rss_mb"], b["build"]["peak_rss_mb"]]}}


def run_all(feed_dir, dgs=DGS, log=print):
    out = {"meta": {"stage": "live showdown A/B, legacy vs legacy-latent-discrete (NOT promoted)",
                    "n_sims": N_SIMS, "seed": SEED, "boards": list(dgs),
                    "money_gate": "CLOSED -- payout columns are diagnostics, not forecasts",
                    "provenance": _prov(worlds=N_SIMS, model="legacy vs legacy-discrete",
                                        seed=SEED)},
           "boards": {}}
    for dg in dgs:
        got = {}
        for mode in ("legacy", "discrete"):
            path = os.path.join(DATA, f"_ab_{dg}_{mode}.json")
            cmd = [sys.executable, "-m", "research.sd_live_ab", "one", feed_dir, str(dg), mode]
            log(f"[AB] spawning {' '.join(cmd[2:])}")
            r = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "VIGIL_NO_BG": "1"})
            if r.returncode != 0 or not os.path.exists(path):
                raise SystemExit(f"{mode} build for {dg} failed (rc {r.returncode})")
            got[mode] = json.load(open(path))
        out["boards"][str(dg)] = {"legacy": got["legacy"], "discrete": got["discrete"],
                                 "delta": compare(got["legacy"], got["discrete"])}
        d = out["boards"][str(dg)]["delta"]
        log(f"[AB] dg {dg}: top-20 identical in {d['top20_slots_identical']}/20 slots, "
            f"overlap {d['top20_overlap']}/20, portfolio overlap {d['portfolio_overlap']}/{PORT_K}, "
            f"best EV {d['ev_best_pct_diff']}%, top200 mean shared-first mass "
            f"x{d['shared_first_ratio_top200']}")
    with open(os.path.join(DATA, "sd_live_ab.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    # the per-mode files were the handoff between processes; everything in them
    # is in the combined artifact, and leaving them behind invites someone to
    # read a stale one from an earlier depth
    for dg in dgs:
        for mode in ("legacy", "discrete"):
            path = os.path.join(DATA, f"_ab_{dg}_{mode}.json")
            if os.path.exists(path):
                os.remove(path)
    return out


def run_control(feed_dir, dg, log=print):
    """Legacy against legacy, same depth, different generator state.

    This is the floor every ordering claim in the A/B has to clear. A top-20
    that reshuffles under the discrete mode means nothing until you know how
    much it reshuffles when NOTHING changes but the draws -- the lineups at the
    top of a 777,056-lineup universe are separated by far less than the
    Monte Carlo error on each one, so some reshuffling is arithmetic, not
    strategy."""
    got = {}
    for mode in ("legacy", "legacy_b"):
        path = os.path.join(DATA, f"_ab_{dg}_{mode}.json")
        if mode == "legacy" and os.path.exists(path):
            log(f"[AB] reusing the A/B's own legacy arm for {dg}")
        else:
            cmd = [sys.executable, "-m", "research.sd_live_ab", "one", feed_dir, str(dg), mode]
            log(f"[AB] spawning {' '.join(cmd[2:])}")
            r = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "VIGIL_NO_BG": "1"})
            if r.returncode != 0 or not os.path.exists(path):
                raise SystemExit(f"{mode} build for {dg} failed (rc {r.returncode})")
        got[mode] = json.load(open(path))
    out = {"meta": {"stage": "live showdown Monte Carlo control: legacy vs legacy (NOT promoted)",
                    "n_sims": N_SIMS, "board": int(dg),
                    "seeds": [got["legacy"].get("seed"), got["legacy_b"].get("seed")],
                    "why": ("the noise floor for every ordering and payout claim in "
                            "sd_live_ab.json: same model, same depth, different draws"),
                    "provenance": _prov(worlds=N_SIMS, model="legacy vs legacy")},
           "delta": compare(got["legacy"], got["legacy_b"]),
           "legacy": got["legacy"], "legacy_b": got["legacy_b"]}
    d = out["delta"]
    log(f"[AB] CONTROL dg {dg}: top-20 identical in {d['top20_slots_identical']}/20 slots, "
        f"overlap {d['top20_overlap']}/20, portfolio overlap {d['portfolio_overlap']}/{PORT_K}, "
        f"best EV {d['ev_best_pct_diff']}%, top200 mean shared-first mass "
        f"x{d['shared_first_ratio_top200']}")
    with open(os.path.join(DATA, "sd_live_ab_control.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    for mode in ("legacy", "legacy_b"):
        path = os.path.join(DATA, f"_ab_{dg}_{mode}.json")
        if os.path.exists(path):
            os.remove(path)
    return out


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what == "one":
        one(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    elif what == "control":
        run_control(sys.argv[2], int(sys.argv[3]))
        print("live showdown Monte Carlo control written")
    else:
        fd = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DATA, "feeds")
        dgs = tuple(int(x) for x in sys.argv[3:]) or DGS
        run_all(fd, dgs)
        print("live showdown A/B written")
