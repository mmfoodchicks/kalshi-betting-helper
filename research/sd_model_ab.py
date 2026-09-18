"""Showdown legacy-vs-constrained STRUCTURAL A/B on the two pinned boards.

NOT a promotion test. The question is narrow and the fences are the point:

    Does removing legacy's known same-team over-correlation materially change
    the lineups and the portfolio Showdown recommends?

The blind-2025 validation (research/data/stage2e_validation.json, 272 games)
put the served simulator's WR1/WR2 correlation at 0.544 against 0.024 observed
and WR1/TE1 at 0.520 against -0.006; its mean absolute pair error is 0.267
within a team and 0.105 across sides. The constrained research model fixes the
within-team half (0.033) and is KNOWN DEFICIENT on the other (0.124; QB against
opposing QB 0.033 vs 0.235 observed, team offense against opposing offense
0.082 vs 0.262). The served Showdown portfolios from the live A/B are 17 to 19
of 20 five-one stacks. Nobody had run the counterfactual.

So: same pinned DraftKings slate and contest (research/data/dk_capture, S6.0),
same pinned Sleeper feeds (research/data/feeds), same seeds, same legal
universe, same public-field representation, same payout grid, same scoring
engine. ONE thing changes between arms: which football model made the worlds.

  discrete       legacy-latent-discrete, what the board serves today
  discrete_b     the same, one seed later: the Monte Carlo floor for the served arm
  constrained    nfl_dfs_csim, the Stage 2E model, research-only
  constrained_b  the same, one seed later: the floor for the constrained arm

Beside every arm's portfolio sits its SIMULATED CORRELATION TABLE, in the same
moment names the blind validation used, so a change in 5-1/4-2/3-3 cannot be
casually attributed to the wrong covariance change (the reviewer's condition
of 2026-09-18). And the interpretation is split on purpose: within-team
structures are where constrained has historical support; opposing-side
structures are where it is known wrong; a change in structure is informative
about SENSITIVITY to the football dependence model and is not, by itself,
evidence that either model's portfolio is right.

Nothing here is promoted, nothing here is economic, and finishing this does
not resume the S6 plumbing. Each arm runs in its own process so peak RSS is
that build's alone.

    python3 -m research.sd_model_ab all [dg ...]
    python3 -m research.sd_model_ab arm <dg> <discrete|discrete_b|constrained|constrained_b>
"""
import json
import os
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
FEEDS = os.path.join(DATA, "feeds")

from research import sd_live_ab as AB      # noqa: E402  structure/se/compare, the constants

#: the same depth the served board runs on, unless a smoke test says otherwise
N_SIMS = int(os.environ.get("SD_MODEL_AB_SIMS") or AB.N_SIMS)
SEED = AB.SEED
DGS = (153086, 153085)
#: the primary contest per pinned draft group: the richest GPP, as the builder picks
PRIMARY = {153086: 195526287, 153085: 195526138}
ARMS = ("discrete", "discrete_b", "constrained", "constrained_b")
MODEL_OF = {"discrete": "legacy", "discrete_b": "legacy",
            "constrained": "constrained", "constrained_b": "constrained"}

#: the fences, verbatim, carried in the artifact so no reader can lose them
FENCES = (
    "same pinned slate inputs (S6 capture) and pinned Sleeper feeds",
    "same seeds per arm pair; the _b arm is the Monte Carlo floor",
    "same public-field representation: softmax(beta x projection) over the same legal universe",
    "the football model is the ONLY changed variable",
    "constrained-v1 remains research-only",
    "this is not a promotion test",
    "do not use it to declare either model economically correct",
    "do not resume S6 production plumbing because this finished",
)

# The moments the blind validation named, so this board's simulated values sit
# beside the season-wide observed and modelled ones. Within-team pairs are
# (role, role); cross-side pairs are (role, "opp " + role); "team offense" is
# the sum of a side's offensive players' arrays.
WITHIN = (("QB1", "WR1"), ("QB1", "WR2"), ("QB1", "WR3"), ("QB1", "TE1"), ("QB1", "RB1"),
          ("QB1", "RB2"), ("WR1", "WR2"), ("WR1", "TE1"), ("WR1", "RB1"), ("WR2", "TE1"),
          ("RB1", "RB2"))
CROSS = (("QB1", "opp QB1"), ("QB1", "opp WR1"), ("WR1", "opp WR1"), ("RB1", "opp RB1"),
         ("QB1", "opp RB1"), ("team offense", "opp offense"))
#: the dependence DST-OPP actually turns on (S5 section O reason 2, rewritten
#: 2026-09-18): a side's offense against the defense it faces
DST = (("QB1", "opp DST"), ("WR1", "opp DST"), ("team offense", "opp DST"), ("K", "QB1"))
OFFENSE_POS = ("QB", "RB", "WR", "TE")


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def _corr(x, y):
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def roles(ents):
    """{team: {role: entry}} with roles by projection within team and position:
    QB1, RB1, RB2, WR1, WR2, WR3, TE1, K, DST -- the blind validation's roles."""
    by = {}
    for e in ents:
        if e.get("_field_only"):
            continue
        by.setdefault(e.get("team"), {}).setdefault(e.get("pos"), []).append(e)
    out = {}
    for team, groups in by.items():
        r = {}
        for pos, lst in groups.items():
            lst = sorted(lst, key=lambda e: -float(e.get("proj") or 0.0))
            if pos in ("QB", "TE", "K", "DST"):
                r[pos + ("1" if pos in ("QB", "TE") else "")] = lst[0]
            elif pos == "RB":
                for i, e in enumerate(lst[:2]):
                    r[f"RB{i + 1}"] = e
            elif pos == "WR":
                for i, e in enumerate(lst[:3]):
                    r[f"WR{i + 1}"] = e
        out[team] = r
    return out


def correlation_table(ents, N):
    """Every named moment, per side, from the arm's own simulated arrays."""
    R = roles(ents)
    teams = sorted(R)
    if len(teams) != 2:
        return {"error": f"expected two teams, found {teams}"}
    arrs = {}
    for team in teams:
        for role, e in R[team].items():
            arrs[(team, role)] = np.asarray(e["arr"][:N], dtype=np.float64)
        off = [np.asarray(e["arr"][:N], dtype=np.float64) for e in ents
               if e.get("team") == team and e.get("pos") in OFFENSE_POS and not e.get("_field_only")]
        arrs[(team, "team offense")] = np.sum(off, axis=0) if off else None

    def get(team, name):
        if name.startswith("opp "):
            other = teams[1] if team == teams[0] else teams[0]
            return arrs.get((other, name[4:]))
        return arrs.get((team, name))

    def block(pairs):
        rows = {}
        for a, b in pairs:
            per = {}
            for team in teams:
                xa, xb = get(team, a), get(team, b)
                per[team] = (None if xa is None or xb is None else _corr(xa, xb))
            vals = [v for v in per.values() if v is not None]
            rows[f"pair {a}/{b}"] = {"by_side": {t: (None if v is None else round(v, 4)) for t, v in per.items()},
                                     "mean": (round(float(np.mean(vals)), 4) if vals else None)}
        return rows

    return {"within_team": block(WITHIN), "cross_side": block(CROSS), "dst": block(DST),
            "roles": {t: {r: e["name"] for r, e in R[t].items()} for t in teams},
            "worlds": int(N)}


def validated_moments():
    """The blind-2025 row for each named pair: observed, legacy, constrained."""
    v = json.load(open(os.path.join(DATA, "stage2e_validation.json")))
    return {r["moment"]: {"observed_2025": round(r["obs"], 4), "legacy": round(r["legacy"], 4),
                          "constrained": round(r["constrained"], 4)} for r in v["pairs"]}


def arm(dg, mode, log=print):
    import resource
    import dfs_tourney as T
    from research import s6_capture, sd_board
    model = MODEL_OF[mode]
    discrete = (model == "legacy")           # the served scorer; ignored under constrained
    seed = SEED + (1 if mode.endswith("_b") else 0)
    S = sd_board.feeds(FEEDS)
    S._cache.clear()
    slate, details = s6_capture.replay(dg)
    contest = details.get(str(PRIMARY[int(dg)]))
    if not slate or not contest:
        raise SystemExit(f"pinned capture for {dg} lacks the slate or contest {PRIMARY[int(dg)]}")
    ents, pool_secs, _ = sd_board.ents_for(slate, sd_board.WEEK, discrete, n_sims=N_SIMS,
                                          seed=seed, log=log, model=model)
    idx, W, allowed = sd_board.universe(ents)
    if idx is None:
        raise SystemExit("no legal lineups")
    f, beta = T.field_weights(ents, idx, cpt_mult=1.5)
    grid, C = sd_board.grid_for(contest)
    N = min(len(e["arr"]) for e in ents)
    X = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    chunk = T.chunk_for(len(idx), 500)
    log(f"[MAB] {mode:13s} dg {dg}: {len(idx):,} lineups, {int(allowed.sum()):,} enterable, "
        f"{N:,} worlds, beta {beta:.4f}, {chunk} worlds a chunk")
    t0 = time.time()
    res = T.run(W, X, f, grid, chunk=chunk)
    score_secs = time.time() - t0
    ok = np.where(allowed)[0]
    top1, top01 = np.asarray(res["top1"]), np.asarray(res["top01"])
    ev, evn = np.asarray(res["ev"]), np.asarray(res["ev_notie"])
    win, sole = np.asarray(res["win"]), np.asarray(res["win_sole"])
    order = ok[np.argsort(-top1[ok])]
    cand = order[:AB.CANDIDATES]
    t1 = time.time()
    chosen, p_any = T.portfolio(W, X, f, grid, cand, AB.PORT_K, chunk=chunk)
    port_secs = time.time() - t1
    port = [int(cand[j]) for j in chosen[:AB.PORT_K]]
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    def row(i):
        cap = ents[int(idx[i, 0])]
        return {"captain": cap["name"], "captain_pos": cap["pos"], "captain_team": cap["team"],
                "flex": sorted(ents[int(idx[i, k])]["name"] for k in range(1, 6)),
                "structure": AB.structure(ents, idx[i]),
                "salary": int(cap["cpt_salary"] + sum(ents[int(idx[i, k])]["salary"] for k in range(1, 6))),
                "k_dst": sum(1 for k in range(6) if ents[int(idx[i, k])]["pos"] in ("K", "DST")),
                "top1_pct": round(100.0 * float(top1[i]), 3),
                "top01_pct": round(100.0 * float(top01[i]), 4),
                "win_any_pct": round(100.0 * float(win[i]), 5),
                "win_sole_pct": round(100.0 * float(sole[i]), 5),
                "ev": round(float(ev[i]), 2), "ev_notie": round(float(evn[i]), 2)}

    from collections import Counter
    top = [row(int(i)) for i in order[:AB.TOPN]]
    pr = [row(i) for i in port]
    cpt_port = Counter(r["captain"] for r in pr)
    expo = Counter()
    for i in port:
        for k in range(6):
            expo[ents[int(idx[i, k])]["name"]] += 1
    # the portfolio's football tail on ITS OWN worlds -- an in-sample statistic,
    # reported only at the quantiles 12,000 worlds resolve (mean, p90, p95)
    Sp = (W[port] @ X).astype(np.float64)
    wmax = Sp.max(axis=0)
    best = int(order[0])
    out = {"mode": mode, "model": model, "draft_group_id": int(dg),
           "contest": contest.get("name"), "contest_id": int(contest["id"]),
           "entries": int(C), "entry_fee": float(contest.get("entry_fee") or 0.0),
           "players": len(ents), "legal_lineups": int(len(idx)),
           "enterable": int(allowed.sum()), "worlds": int(N),
           "field_beta": round(float(beta), 4), "seed": int(seed),
           "sim_stamp": T.simulator_stamp(model, N, seed) if model == "constrained"
           else S.sim_stamp(N, False, True),
           "build": {"pool_seconds": round(pool_secs, 1), "score_seconds": round(score_secs, 1),
                     "portfolio_seconds": round(port_secs, 1), "peak_rss_mb": round(rss, 1),
                     "chunk_worlds": int(chunk)},
           "best": {"top1_pct": round(100.0 * float(top1[best]), 3),
                    "top1_se_pct": round(100.0 * AB.se(float(top1[best]), N), 3),
                    "top01_pct": round(100.0 * float(top01[best]), 4),
                    "top01_se_pct": round(100.0 * AB.se(float(top01[best]), N), 4),
                    "ev": round(float(ev[best]), 2), "ev_notie": round(float(evn[best]), 2),
                    "split_haircut_pct": (round(100.0 * (1.0 - float(ev[best]) / float(evn[best])), 3)
                                          if evn[best] else None)},
           "tie": {"mean_shared_first_pct_top200": round(100.0 * float(np.mean(win[order[:200]] - sole[order[:200]])), 6),
                   "mean_any_first_pct_top200": round(100.0 * float(np.mean(win[order[:200]])), 6),
                   "mean_sole_first_pct_top200": round(100.0 * float(np.mean(sole[order[:200]])), 6)},
           "top20": top,
           "portfolio": {"k": AB.PORT_K, "p_any_top1_pct": round(100.0 * float(p_any[AB.PORT_K - 1]), 2),
                         "p_any_basis": "in-sample: selection and evaluation share these worlds",
                         "entries": pr,
                         "captains": [{"name": n, "entries": c} for n, c in cpt_port.most_common()],
                         "exposure": [{"name": n, "slots": c} for n, c in expo.most_common(12)],
                         "structures": dict(Counter(r["structure"] for r in pr)),
                         "k_dst_slots": int(sum(r["k_dst"] for r in pr)),
                         "salary_left_mean": round(float(np.mean([50000 - r["salary"] for r in pr])), 1),
                         "football": {"worldwise_max_mean": round(float(wmax.mean()), 4),
                                      "worldwise_max_p90": round(float(np.percentile(wmax, 90)), 4),
                                      "worldwise_max_p95": round(float(np.percentile(wmax, 95)), 4),
                                      "note": ("in-sample, on the arm's own worlds; p99 and above are not "
                                               "reported because 12,000 worlds do not resolve them")}},
           "top20_structures": dict(Counter(r["structure"] for r in top)),
           "top20_k_dst_slots": int(sum(r["k_dst"] for r in top)),
           "enterable_structures": dict(Counter(AB.structure(ents, idx[int(i)]) for i in ok)),
           "correlations": correlation_table(ents, N)}
    with open(os.path.join(DATA, f"_ab_{dg}_{mode}.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[MAB] {mode:13s} dg {dg}: best top1 {out['best']['top1_pct']:.2f}% "
        f"(+-{out['best']['top1_se_pct']:.2f}) portfolio {out['portfolio']['structures']} "
        f"WR1/WR2 {out['correlations']['within_team']['pair WR1/WR2']['mean']} "
        f"| pool {pool_secs:.0f}s score {score_secs:.0f}s RSS {rss:,.0f} MB")
    return out


def _rank_corr(a, b):
    """Spearman over the union of the two arms' top-20 keys, by top-1% rank
    position (missing from an arm = rank 21)."""
    ka = [(r["captain"], tuple(r["flex"])) for r in a["top20"]]
    kb = [(r["captain"], tuple(r["flex"])) for r in b["top20"]]
    keys = list(dict.fromkeys(ka + kb))
    pa = {k: i for i, k in enumerate(ka)}
    pb = {k: i for i, k in enumerate(kb)}
    ra = np.asarray([pa.get(k, len(ka)) for k in keys], dtype=np.float64)
    rb = np.asarray([pb.get(k, len(kb)) for k in keys], dtype=np.float64)
    return (round(float(np.corrcoef(ra, rb)[0, 1]), 3) if len(keys) > 2 else None)


def corr_delta(a, b):
    out = {}
    for blk in ("within_team", "cross_side", "dst"):
        rows = {}
        for name, ra in a["correlations"][blk].items():
            rb = b["correlations"][blk].get(name) or {}
            if ra.get("mean") is None or rb.get("mean") is None:
                continue
            rows[name] = {"a": ra["mean"], "b": rb["mean"], "delta": round(rb["mean"] - ra["mean"], 4)}
        vals = [abs(r["delta"]) for r in rows.values()]
        out[blk] = {"pairs": rows, "mean_abs_delta": (round(float(np.mean(vals)), 4) if vals else None)}
    return out


def compare_arms(a, b):
    d = AB.compare(a, b)
    d["portfolio_structures"] = {a["mode"]: a["portfolio"]["structures"], b["mode"]: b["portfolio"]["structures"]}
    d["top20_structures"] = {a["mode"]: a["top20_structures"], b["mode"]: b["top20_structures"]}
    d["top20_rank_spearman"] = _rank_corr(a, b)
    d["portfolio_k_dst_slots"] = [a["portfolio"]["k_dst_slots"], b["portfolio"]["k_dst_slots"]]
    d["portfolio_salary_left_mean"] = [a["portfolio"]["salary_left_mean"], b["portfolio"]["salary_left_mean"]]
    d["portfolio_football"] = {a["mode"]: a["portfolio"]["football"], b["mode"]: b["portfolio"]["football"]}
    d["correlations"] = corr_delta(a, b)
    return d


def run_all(dgs=DGS, log=print):
    out = {"meta": {"stage": "Showdown legacy-vs-constrained STRUCTURAL A/B (NOT a promotion test)",
                    "question": ("does removing legacy's known same-team over-correlation materially "
                                 "change the lineups and portfolio Showdown recommends?"),
                    "fences": list(FENCES),
                    "n_sims": N_SIMS, "seed": SEED, "boards": list(dgs),
                    "primary_contests": {str(k): v for k, v in PRIMARY.items()},
                    "inputs": {"draftkings": "research/data/dk_capture (S6.0 capture, sha256 in its manifest)",
                               "sleeper": "research/data/feeds (pinned)"},
                    "interpretation_split": {
                        "within_team": ("constrained has blind-2025 support here (mean abs error 0.033 "
                                        "vs legacy 0.267); a change in same-team stacking is the "
                                        "sensitivity this study asks about"),
                        "cross_side": ("constrained-v1 is KNOWN DEFICIENT here (0.124 vs legacy 0.105; "
                                       "QB/opp QB 0.033 vs 0.235 observed); a change in cross-side "
                                       "structure under constrained is NOT evidence its portfolio is right"),
                        "dst": ("the dependence DST-OPP turns on; neither model represents giveaways "
                                "conditional on offensive output, so read these as description only")},
                    "validated_moments_2025": validated_moments(),
                    "money_gate": "CLOSED -- every payout column here is a diagnostic",
                    "provenance": _prov(worlds=N_SIMS, model="legacy-discrete vs constrained", seed=SEED)},
           "boards": {}}
    for dg in dgs:
        got = {}
        for mode in ARMS:
            path = os.path.join(DATA, f"_ab_{dg}_{mode}.json")
            cmd = [sys.executable, "-m", "research.sd_model_ab", "arm", str(dg), mode]
            log(f"[MAB] spawning {' '.join(cmd[2:])}")
            r = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "VIGIL_NO_BG": "1"})
            if r.returncode != 0 or not os.path.exists(path):
                raise SystemExit(f"{mode} build for {dg} failed (rc {r.returncode})")
            got[mode] = json.load(open(path))
        out["boards"][str(dg)] = {
            "arms": got,
            "delta_model": compare_arms(got["discrete"], got["constrained"]),
            "floor_discrete": compare_arms(got["discrete"], got["discrete_b"]),
            "floor_constrained": compare_arms(got["constrained"], got["constrained_b"])}
        d = out["boards"][str(dg)]["delta_model"]
        log(f"[MAB] dg {dg}: portfolio structures {d['portfolio_structures']}; "
            f"portfolio overlap {d['portfolio_overlap']}/{AB.PORT_K}; top-20 overlap {d['top20_overlap']}/20; "
            f"WR1/WR2 {d['correlations']['within_team']['pairs'].get('pair WR1/WR2')}")
    with open(os.path.join(DATA, "sd_model_ab.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    for dg in dgs:
        for mode in ARMS:
            p = os.path.join(DATA, f"_ab_{dg}_{mode}.json")
            if os.path.exists(p):
                os.remove(p)
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "arm":
        arm(int(a[1]), a[2])
    elif a and a[0] == "all":
        run_all(tuple(int(x) for x in a[1:]) or DGS)
        print("model A/B written")
    else:
        print(__doc__)
