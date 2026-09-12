"""Which player, which pair, and is it noise?

`research/sd_discrete` reported three summary numbers for the discrete legacy
scorer: player means move 0.80% at the median and 16.5% at the worst, and the
worst pair correlation moves 0.106. Summary numbers cannot tell a structural
regression from the sampling noise of a Monte Carlo, and they cannot say whether
the pair that moved is one a showdown lineup is built on. This names them and
measures the noise floor they have to beat.

The noise floor is the whole point. Two LEGACY runs of the same game, same size,
different generator state, disagree about a pair correlation by some amount
purely because each saw a finite sample. If legacy-vs-discrete disagreement is
the same size as legacy-vs-legacy disagreement, there is no regression to
explain -- the measurement is at its resolution limit. So every delta below is
reported beside a paired legacy-vs-legacy control at identical depth.

One methodological correction to the earlier pass, found while writing this:
sd_discrete built its pair list as `[n for n in common if std > 1.0][:14]` over
a SORTED name list, so "the worst correlation delta" was the worst among the
alphabetically first fourteen players. That is not the worst pair, and the
number it produced should not have been described as one. Both are reported:
the original truncated set, so the published 0.106 reproduces, and the full set,
which is the honest answer.

    python3 -m research.sd_outliers <feed dir> [draft_group_id]
"""
import itertools
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")

WORLDS = 20000          # the depth sd_discrete used, so its numbers reproduce
DG_DEFAULT = 153086     # DEN @ KC, the week-2 Monday night $1.5M
BANDS = ((1.0, 5.0), (5.0, 10.0), (10.0, 15.0), (15.0, 1e9))
ROSTERABLE = 4.0        # DK points below which a showdown play is a punt
# The two seed tables, named so the provenance stamp records what the runs
# were actually seeded with. legacy and discrete share a seed on purpose --
# that is what makes them the SAME draw scored two ways; legacy_b is the
# reseed control.
POOL_SEEDS = (("legacy", False, 303), ("legacy_b", False, 404),
              ("discrete", True, 303))
GAME_SEEDS = (("legacy", False, 101), ("legacy_b", False, 202),
              ("discrete", True, 101))
STRATEGIC = ("qb_own_pass_catcher", "qb_opposing_qb", "qb_opposing_pass_catcher",
             "rb_own_qb", "same_team_pass_catchers", "k_own_qb", "dst_opposing_qb",
             "dst_own_offense")


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def relationship(a, b):
    """What a showdown lineup is actually made of. Returns a tag, or None for a
    pair nobody builds around.

    These are the couplings a single-game roster is a bet on: the quarterback
    with his own receivers (the stack), with the other quarterback (the
    shootout, and the bring-back that pays for it), his own back (who eats the
    pass volume), two receivers sharing a target tree, the kicker riding his
    offense, and the defense against the arm it faces. If the discrete mode
    broke one of these, the strategy changes. If it moved a pair of punt
    receivers on opposite sides, it did not."""
    pa, pb = a["pos"], b["pos"]
    same = a["team"] == b["team"]
    rec = {"WR", "TE"}
    if {pa, pb} == {"QB"} and not same:
        return "qb_opposing_qb"
    if "QB" in (pa, pb):
        other = b if pa == "QB" else a
        if other["pos"] in rec:
            return "qb_own_pass_catcher" if same else "qb_opposing_pass_catcher"
        if other["pos"] == "RB" and same:
            return "rb_own_qb"
        if other["pos"] == "K":
            return "k_own_qb" if same else None
        if other["pos"] == "DST":
            return "dst_own_offense" if same else "dst_opposing_qb"
    if pa in rec and pb in rec and same:
        return "same_team_pass_catchers"
    if "DST" in (pa, pb) and same:
        return "dst_own_offense"
    return None


def _corr(x, y):
    return float(np.corrcoef(x, y)[0, 1])


def pair_table(A, B, meta, names):
    """Every pair's correlation under A and under B, with its relationship."""
    rows = []
    for n1, n2 in itertools.combinations(names, 2):
        rows.append({"a": n1, "b": n2,
                     "a_pos": meta[n1]["pos"], "b_pos": meta[n2]["pos"],
                     "a_team": meta[n1]["team"], "b_team": meta[n2]["team"],
                     "rel": relationship(meta[n1], meta[n2]),
                     "corr_a": round(_corr(A[n1], A[n2]), 4),
                     "corr_b": round(_corr(B[n1], B[n2]), 4)})
    for r in rows:
        r["delta"] = round(r["corr_b"] - r["corr_a"], 4)
        r["abs_delta"] = abs(r["delta"])
    return rows


def _fmt(r):
    return (f"{r['a']} ({r['a_pos']} {r['a_team']}) x {r['b']} ({r['b_pos']} {r['b_team']}): "
            f"{r['corr_a']:+.4f} -> {r['corr_b']:+.4f}  delta {r['delta']:+.4f}"
            f"  [{r['rel'] or 'not a build'}]")


POOL_WORLDS = 8000


def side_pairs(S, slate, n=POOL_WORLDS, log=print):
    """Kicker and defense couplings, under both modes, against a legacy control.

    DISCRETE_VERSION 2 changed both of these models: the kicker's level now
    comes from its field-goal rate instead of a multiply, and the defense's
    Sleeper shift is spent as whole points plus a coin. Either change could
    have cost the coupling to the game, which is the only reason those two
    slots are worth rostering in a showdown at all."""
    from research import sd_board
    runs, meta = {}, {}
    for tag, disc, seed in POOL_SEEDS:
        S._cache.clear()
        ents, _secs, _rss = sd_board.ents_for(slate, sd_board.WEEK, disc, n_sims=n,
                                             seed=seed, log=lambda *_a, **_k: None)
        runs[tag] = {e["name"]: np.asarray(e["arr"][:n], dtype=np.float64) for e in ents}
        for e in ents:
            meta[e["name"]] = {"pos": e["pos"], "team": e.get("team"),
                               "proj": float(e.get("proj") or 0.0)}
    common = sorted(set(runs["legacy"]) & set(runs["discrete"]) & set(runs["legacy_b"]))
    movers = [n for n in common if runs["legacy"][n].std() > 1.0]
    full = pair_table(runs["legacy"], runs["discrete"], meta, movers)
    noise = pair_table(runs["legacy"], runs["legacy_b"], meta, movers)
    want = {"k_own_qb", "dst_opposing_qb", "dst_own_offense"}
    sides = [r for r in full if r["rel"] in want]
    sides.sort(key=lambda r: -r["abs_delta"])
    per = {}
    for tag in sorted(want):
        g = [r for r in full if r["rel"] == tag]
        gn = [r for r in noise if r["rel"] == tag]
        if not g:
            continue
        per[tag] = {"n_pairs": len(g),
                    "legacy_corr_median": round(float(np.median([r["corr_a"] for r in g])), 4),
                    "discrete_corr_median": round(float(np.median([r["corr_b"] for r in g])), 4),
                    "abs_delta_median": round(float(np.median([r["abs_delta"] for r in g])), 4),
                    "abs_delta_max": round(float(max(r["abs_delta"] for r in g)), 4),
                    "mc_noise_abs_delta_median": (round(float(np.median([r["abs_delta"] for r in gn])), 4)
                                                  if gn else None),
                    "mc_noise_abs_delta_max": (round(float(max(r["abs_delta"] for r in gn)), 4)
                                               if gn else None)}
        log(f"[OUT] {tag:20s} n={per[tag]['n_pairs']:2d} "
            f"{per[tag]['legacy_corr_median']:+.4f} -> {per[tag]['discrete_corr_median']:+.4f}  "
            f"|d| p50 {per[tag]['abs_delta_median']:.4f} max {per[tag]['abs_delta_max']:.4f}  "
            f"| noise p50 {per[tag]['mc_noise_abs_delta_median']} "
            f"max {per[tag]['mc_noise_abs_delta_max']}")
    # the mean the two side models are supposed to land on
    lev = []
    for n in common:
        if meta[n]["pos"] not in ("K", "DST"):
            continue
        lm, dm = float(runs["legacy"][n].mean()), float(runs["discrete"][n].mean())
        lev.append({"name": n, "pos": meta[n]["pos"], "proj": meta[n]["proj"],
                    "legacy_mean": round(lm, 3), "discrete_mean": round(dm, 3),
                    "legacy_err_pts": round(lm - meta[n]["proj"], 3),
                    "discrete_err_pts": round(dm - meta[n]["proj"], 3)})
    for r in lev:
        log(f"[OUT] level {r['pos']:4s} {r['name'][:20]:20s} proj {r['proj']:6.2f}  "
            f"legacy {r['legacy_mean']:6.2f} ({r['legacy_err_pts']:+.2f})  "
            f"discrete {r['discrete_mean']:6.2f} ({r['discrete_err_pts']:+.2f})")
    return {"worlds": n, "by_relationship": per, "largest": sides[:10], "levels": lev}


def run(feed_dir, dg=DG_DEFAULT, log=print):
    from research import sd_board
    S = sd_board.feeds(feed_dir)
    S._cache.clear()
    games = S.weekly_games(sd_board.SEASON, sd_board.WEEK)
    slate, contest = sd_board.slate_and_contest(dg)
    if not slate:
        raise SystemExit(f"draft group {dg} has no slate")
    import simulate
    teams = {(r.get("team") or "").upper() for r in simulate.parse_dk_csv(slate["csv"])}
    gid = next((g for g, v in games.items()
                if teams & {str(t).upper() for t in (v.get("teams") or [])}), None)
    if gid is None:
        raise SystemExit(f"no simulated game matches {sorted(teams)}")
    game = games[gid]
    log(f"[OUT] {game.get('label')}: {len(game['players'])} players x {WORLDS:,} worlds x 3 runs")
    runs = {}
    meta = {}
    for tag, disc, seed in GAME_SEEDS:
        S._random.seed(seed)
        sim = S.simulate_game(game, n=WORLDS, with_samples=True, discrete=disc)
        runs[tag] = {p["name"]: np.asarray(p["arr"], dtype=np.float64) for p in sim["players"]}
        for p in sim["players"]:
            meta[p["name"]] = {"pos": p["pos"], "team": p["team"], "proj": float(p["proj_pts"]),
                               "pos_": p["pos"]}
        log(f"[OUT]   {tag} done")
    la, lb, da = runs["legacy"], runs["legacy_b"], runs["discrete"]
    common = sorted(set(la) & set(da) & set(lb))

    # ---- A: the mean residuals, banded -------------------------------------
    rows = []
    for n in common:
        proj = meta[n]["proj"]
        lm, dm, bm = float(la[n].mean()), float(da[n].mean()), float(lb[n].mean())
        rows.append({"name": n, "pos": meta[n]["pos"], "team": meta[n]["team"], "proj": proj,
                     "legacy_mean": round(lm, 3), "legacy_b_mean": round(bm, 3),
                     "discrete_mean": round(dm, 3),
                     "abs_err_pts": round(dm - lm, 4),
                     "rel_err_pct": (round(100.0 * (dm - lm) / lm, 2) if lm > 0 else None),
                     "noise_abs_pts": round(bm - lm, 4),
                     "noise_rel_pct": (round(100.0 * (bm - lm) / lm, 2) if lm > 0 else None)})
    scored = [r for r in rows if r["legacy_mean"] > 1.0]
    worst = max(scored, key=lambda r: abs(r["rel_err_pct"] or 0.0))
    log("[OUT] worst mean residual: " + json.dumps(worst))
    bands = []
    for lo, hi in BANDS:
        g = [r for r in rows if lo <= r["proj"] < hi]
        if not g:
            continue
        ae = np.abs([r["abs_err_pts"] for r in g])
        re = np.abs([r["rel_err_pct"] for r in g if r["rel_err_pct"] is not None])
        nae = np.abs([r["noise_abs_pts"] for r in g])
        nre = np.abs([r["noise_rel_pct"] for r in g if r["noise_rel_pct"] is not None])
        bands.append({"band": f"{lo:g}-{'inf' if hi > 1e8 else f'{hi:g}'} proj pts",
                      "n_players": len(g),
                      "abs_err_pts_median": round(float(np.median(ae)), 3),
                      "abs_err_pts_max": round(float(ae.max()), 3),
                      "rel_err_pct_median": (round(float(np.median(re)), 2) if re.size else None),
                      "rel_err_pct_max": (round(float(re.max()), 2) if re.size else None),
                      "mc_noise_abs_pts_median": round(float(np.median(nae)), 3),
                      "mc_noise_abs_pts_max": round(float(nae.max()), 3),
                      "mc_noise_rel_pct_median": (round(float(np.median(nre)), 2) if nre.size else None),
                      "mc_noise_rel_pct_max": (round(float(nre.max()), 2) if nre.size else None)})
        log(f"[OUT] band {bands[-1]['band']:18s} n={len(g):2d}  "
            f"abs {bands[-1]['abs_err_pts_median']:.3f} (max {bands[-1]['abs_err_pts_max']:.3f})  "
            f"rel {bands[-1]['rel_err_pct_median']}% (max {bands[-1]['rel_err_pct_max']}%)  "
            f"| MC noise abs {bands[-1]['mc_noise_abs_pts_median']:.3f} "
            f"rel {bands[-1]['mc_noise_rel_pct_median']}%")

    # ---- B: the correlations ------------------------------------------------
    movers = [n for n in common if la[n].std() > 1.0]
    orig14 = movers[:14]                       # the truncated set sd_discrete used
    full = pair_table(la, da, meta, movers)
    trunc = pair_table(la, da, meta, orig14)
    noise = pair_table(la, lb, meta, movers)   # legacy vs legacy, the floor
    worst_full = max(full, key=lambda r: r["abs_delta"])
    worst_trunc = max(trunc, key=lambda r: r["abs_delta"])
    strat = [r for r in full if r["rel"] in STRATEGIC]
    strat.sort(key=lambda r: -r["abs_delta"])
    # The decision-relevant subset: BOTH players rosterable. A 0.165 move
    # between two receivers projected at 1.3 points is a fact about rounding a
    # five-yard line, not about a lineup anyone builds. ROSTERABLE is set at 4
    # DK points because below it a showdown play is a punt whose correlation
    # nobody is buying.
    rost = [r for r in full if r["rel"] in STRATEGIC
            and meta[r["a"]]["proj"] >= ROSTERABLE and meta[r["b"]]["proj"] >= ROSTERABLE]
    rost.sort(key=lambda r: -r["abs_delta"])
    rost_noise = [r for r in noise if r["rel"] in STRATEGIC
                  and meta[r["a"]]["proj"] >= ROSTERABLE and meta[r["b"]]["proj"] >= ROSTERABLE]
    nd = np.asarray([r["abs_delta"] for r in noise])
    fd = np.asarray([r["abs_delta"] for r in full])
    log("[OUT] worst pair, truncated set (reproduces sd_discrete): " + _fmt(worst_trunc))
    log("[OUT] worst pair, FULL set:                              " + _fmt(worst_full))
    log(f"[OUT] |delta| legacy-vs-discrete: p50 {np.median(fd):.4f} p95 "
        f"{np.percentile(fd, 95):.4f} max {fd.max():.4f}  ({len(full)} pairs)")
    log(f"[OUT] |delta| legacy-vs-legacy  : p50 {np.median(nd):.4f} p95 "
        f"{np.percentile(nd, 95):.4f} max {nd.max():.4f}  (the noise floor)")
    if rost:
        rn = np.asarray([r["abs_delta"] for r in rost_noise]) if rost_noise else np.zeros(1)
        rd = np.asarray([r["abs_delta"] for r in rost])
        log(f"[OUT] BOTH players rosterable (>= {ROSTERABLE:g} proj), {len(rost)} pairs: "
            f"|delta| p50 {np.median(rd):.4f} max {rd.max():.4f}  "
            f"| noise p50 {np.median(rn):.4f} max {rn.max():.4f}")
        log("[OUT]   worst: " + _fmt(rost[0]))
    log(f"[OUT] strategically important pairs, 10 largest of {len(strat)}:")
    for r in strat[:10]:
        mn = next((x for x in noise if x["a"] == r["a"] and x["b"] == r["b"]), None)
        log(f"[OUT]   {_fmt(r)}   noise {mn['delta'] if mn else None:+.4f}"
            if mn else f"[OUT]   {_fmt(r)}")
    per_rel = {}
    for tag in STRATEGIC:
        g = [r for r in full if r["rel"] == tag]
        if not g:
            continue
        gn = [r for r in noise if r["rel"] == tag]
        per_rel[tag] = {
            "n_pairs": len(g),
            "legacy_corr_median": round(float(np.median([r["corr_a"] for r in g])), 4),
            "discrete_corr_median": round(float(np.median([r["corr_b"] for r in g])), 4),
            "abs_delta_median": round(float(np.median([r["abs_delta"] for r in g])), 4),
            "abs_delta_max": round(float(max(r["abs_delta"] for r in g)), 4),
            "mc_noise_abs_delta_median": (round(float(np.median([r["abs_delta"] for r in gn])), 4)
                                          if gn else None),
            "mc_noise_abs_delta_max": (round(float(max(r["abs_delta"] for r in gn)), 4)
                                       if gn else None)}
    # ---- C: the kicker and the defense -------------------------------------
    # simulate_game returns offensive players only, so the pairs that matter
    # most to the code this pass CHANGED -- the kicker riding his own offense,
    # the defense against the arm it faces -- are invisible above. They are
    # measured on the real pool instead, at lower depth because a correlation
    # does not need 20,000 worlds to be read to three decimals.
    pool_pairs = None
    try:
        pool_pairs = side_pairs(S, slate, log=log)
    except Exception as e:          # a pool build is the most fragile step here
        log(f"[OUT] pool pass failed: {type(e).__name__}: {e}")
        pool_pairs = {"error": f"{type(e).__name__}: {e}"}

    out = {"meta": {"stage": "discrete outlier attribution (NOT promoted)",
                    "game": game.get("label"), "draft_group_id": int(dg),
                    "contest": (contest or {}).get("name"),
                    "worlds": WORLDS, "discrete_version": S.DISCRETE_VERSION,
                    "note": ("every delta is reported beside a legacy-vs-legacy control at the "
                             "same depth; a delta inside the control is at the measurement's "
                             "resolution limit, not a regression"),
                    "provenance": _prov(worlds=WORLDS, model="legacy x2 vs legacy-discrete",
                                        seed={"pool": POOL_SEEDS, "game": GAME_SEEDS})},
           "means": {"players": sorted(rows, key=lambda r: -(r["proj"] or 0)),
                     "worst_residual": worst, "bands": bands,
                     "n_movers": len(movers)},
           "pairs": {"worst_truncated_set": worst_trunc, "worst_full_set": worst_full,
                     "truncated_set_players": orig14,
                     "n_pairs_full": len(full),
                     "delta_p50": round(float(np.median(fd)), 4),
                     "delta_p95": round(float(np.percentile(fd, 95)), 4),
                     "delta_max": round(float(fd.max()), 4),
                     "mc_noise_p50": round(float(np.median(nd)), 4),
                     "mc_noise_p95": round(float(np.percentile(nd, 95)), 4),
                     "mc_noise_max": round(float(nd.max()), 4),
                     "strategic_top10": strat[:10],
                     "rosterable_min_proj": ROSTERABLE,
                     "rosterable_n_pairs": len(rost),
                     "rosterable_delta_p50": (round(float(np.median([r["abs_delta"] for r in rost])), 4)
                                              if rost else None),
                     "rosterable_delta_max": (round(float(max(r["abs_delta"] for r in rost)), 4)
                                              if rost else None),
                     "rosterable_noise_p50": (round(float(np.median([r["abs_delta"] for r in rost_noise])), 4)
                                              if rost_noise else None),
                     "rosterable_noise_max": (round(float(max(r["abs_delta"] for r in rost_noise)), 4)
                                              if rost_noise else None),
                     "rosterable_top10": rost[:10],
                     "all_pairs": full,
                     "by_relationship": per_rel},
           "kicker_and_defense": pool_pairs}
    with open(os.path.join(DATA, "sd_outliers.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "feeds")
    run(fd, int(sys.argv[2]) if len(sys.argv) > 2 else DG_DEFAULT)
    print("discrete outlier attribution written")
