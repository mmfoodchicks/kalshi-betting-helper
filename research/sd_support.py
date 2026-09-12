"""Is a discrete showdown score actually legal, all the way to the lineup total?

The earlier pass measured that 51% of simulated showdown scores sat on a grid
DraftKings cannot print, and that rebuilding the offense on integer yards and
whole catches took that to zero. This asks the harder question the promotion
decision turns on, and asks it of the WHOLE pool rather than the skill players:

  1. does every base player score recompute, exactly, from an integer stat
     line through DraftKings' own scorer;
  2. is the captain's score exactly 1.5x his base;
  3. do the final six-player lineup totals occupy only legally attainable
     support;
  4. how often do two final lineup totals land exactly equal -- which is the
     quantity a split first-place payout actually turns on, and is NOT the
     same as the player-pair tie rate the last pass measured.

Two findings came out of writing it, and both are in the report:

  * `discrete` as shipped in f41ea02 covered the OFFENSE only. The kicker was
    still multiplied by projection/raw and the defense still shifted by a
    fractional number of points, so 47% and 44% of their scores were
    unprintable and any lineup holding one was off the lattice. Fixed at the
    source (DISCRETE_VERSION 2) and measured here.
  * the legality of a TOTAL is not the legality of its parts. A base score
    lives on 0.02, a captain's on 0.03, and a total on 0.01; the engine
    buckets at exactly 0.01, so the arithmetic has to be checked in integer
    hundredths rather than trusted to float32.

    python3 -m research.sd_support <feed dir> [draft_group_id]
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")

WORLDS = 4000          # the support property is per-world, so depth buys nothing
LINEUPS = 4000         # sampled from the real legal universe
DG_DEFAULT = 153086    # DEN @ KC, the week-2 Monday night $1.5M
# Named so the provenance stamp reads the same constants the run seeds with,
# instead of a null that claims the stage was unseeded.
BOARD_SEED = 20260912
LINEUPS_SEED = 7


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def _hundredths(a):
    """Scores as integer hundredths of a point, with the worst float drift seen
    on the way. Everything downstream is integer arithmetic: the engine buckets
    at 0.01 (dfs_tourney._RES), so that is the unit the support lives in."""
    h = np.rint(np.asarray(a, dtype=np.float64) * 100.0)
    drift = float(np.max(np.abs(np.asarray(a, dtype=np.float64) * 100.0 - h))) if h.size else 0.0
    return h.astype(np.int64), drift


# ---- 1 + 2: the base scores, recomputed from the box score ------------------
def offense_chain(S, game, discrete, n=WORLDS, log=print):
    """Recompute every offensive score from the stat line that produced it."""
    sim = S.simulate_game(game, n=n, with_samples=True, discrete=discrete,
                         with_components=True)
    rows = []
    for p in sim["players"]:
        c = p["comps"]
        want = np.asarray([S._ppr(c["pass_yd"][i], c["pass_td"][i], c["int"][i],
                                  c["rush_yd"][i], c["rush_td"][i], c["rec"][i],
                                  c["rec_yd"][i], c["rec_td"][i], c["fum"][i])
                           for i in range(n)], dtype=np.float64)
        raw = np.asarray(p["arr_raw"], dtype=np.float64)     # before any pinning
        served = np.asarray(p["arr"], dtype=np.float64)      # what the board gets
        ints = all(float(v).is_integer() for k in ("pass_td", "int", "rush_td", "rec_td", "fum")
                   for v in c[k][:200])
        yds_int = all(float(v).is_integer() for k in ("pass_yd", "rush_yd", "rec_yd", "rec")
                      for v in c[k][:200])
        h, drift = _hundredths(served)
        rows.append({
            "name": p["name"], "pos": p["pos"], "team": p["team"],
            "recompute_max_abs_err_raw": float(np.max(np.abs(want - raw))),
            "recompute_max_abs_err_served": float(np.max(np.abs(want - served))),
            "counts_integral": bool(ints), "yards_integral": bool(yds_int),
            "served_on_0_02": bool(np.all(h % 2 == 0)),
            "served_off_0_02_pct": round(100.0 * float(np.mean(h % 2 != 0)), 3),
            "float_drift_hundredths": round(drift, 9),
            "mean": float(served.mean()), "proj": float(p["proj_pts"]),
            "sd": float(served.std())})
    bad_rc = [r for r in rows if r["recompute_max_abs_err_served"] > 1e-9]
    bad_lat = [r for r in rows if not r["served_on_0_02"]]
    log(f"[SUP] offense {'discrete' if discrete else 'legacy  '}: "
        f"{len(rows) - len(bad_rc)}/{len(rows)} scores recompute from the box score, "
        f"{len(rows) - len(bad_lat)}/{len(rows)} wholly on the 0.02 lattice")
    return {"players": rows, "n_players": len(rows),
            "recompute_exact": len(rows) - len(bad_rc),
            "on_lattice": len(rows) - len(bad_lat),
            "worst_recompute_err": (max(r["recompute_max_abs_err_served"] for r in rows)
                                    if rows else 0.0),
            "max_float_drift_hundredths": (max(r["float_drift_hundredths"] for r in rows)
                                           if rows else 0.0)}


# ---- the legality test, per position class ----------------------------------
# DraftKings pays an offense in multiples of 0.02 (0.04 a passing yard, 0.1 a
# rushing or receiving yard, whole receptions, whole touchdowns, whole
# bonuses). It pays a kicker and a defense only in whole points -- every
# category in dk_scoring.NFL_K and NFL_DST is an integer, and so is every
# points-allowed tier. A defense's floor is -4, the 35+ tier; nothing else
# subtracts.
def pool_legality(pool, log=print):
    out, worst = {}, []
    for nm, p in sorted(pool.items()):
        h, drift = _hundredths(p["arr"])
        pos = p["pos"]
        if pos == "K":
            ok = (h % 100 == 0) & (h >= 0)
            rule = "whole points, not negative"
        elif pos == "DST":
            ok = (h % 100 == 0) & (h >= -400)
            rule = "whole points, floor -4"
        else:
            ok = (h % 2 == 0)
            rule = "multiple of 0.02"
        bad = float(np.mean(~ok))
        out[nm] = {"pos": pos, "rule": rule, "illegal_pct": round(100.0 * bad, 2),
                   "float_drift_hundredths": round(drift, 9),
                   "mean": round(float(np.mean(p["arr"])), 3), "proj": p["proj"]}
        if bad > 0:
            worst.append((bad, nm, pos))
    worst.sort(reverse=True)
    by_pos = {}
    for nm, r in out.items():
        d = by_pos.setdefault(r["pos"], {"n": 0, "illegal_players": 0, "worst_pct": 0.0})
        d["n"] += 1
        if r["illegal_pct"] > 0:
            d["illegal_players"] += 1
        d["worst_pct"] = max(d["worst_pct"], r["illegal_pct"])
    log("[SUP] pool legality by slot: " + ", ".join(
        f"{k} {v['n'] - v['illegal_players']}/{v['n']} clean" for k, v in sorted(by_pos.items())))
    return {"by_pos": by_pos, "players": out,
            "illegal_players": len(worst),
            "worst": [{"name": nm, "pos": ps, "illegal_pct": round(100.0 * b, 2)}
                      for b, nm, ps in worst[:10]]}


# ---- 2 + 3 + 4: captain, totals, ties --------------------------------------
def lineup_support(ents, idx, f, n_worlds, n_lineups=LINEUPS, seed=LINEUPS_SEED, log=print):
    """Final six-player totals in integer hundredths, and what they tie on.

    DraftKings pays a captain exactly 1.5x, and the engine puts exactly 1.5 in
    the weight matrix (1.5 is a binary fraction, so that multiply is exact at
    float32). What is NOT exact is whether the PRODUCT can be represented where
    the engine counts ties, which is a 0.01 bucket (dfs_tourney._RES = 100):

      * a legal DK offensive score is an even number of hundredths, and
        1.5 x (even/100) = 3 x (even/2) hundredths -- an integer, so the bucket
        holds it exactly;
      * a legacy score sits on 0.01, odd hundredths included, and
        1.5 x (odd/100) needs HALF a hundredth. The engine has nowhere to put
        it, so it rounds -- 0.005 of a point, on every captain, silently.

    That is a second defect of the old path, separate from the illegal support,
    and it lands directly on the tie arithmetic the split payout is computed
    from. Both are measured below.
    """
    rng = np.random.default_rng(seed)
    P = len(ents)
    A = np.zeros((P, n_worlds), dtype=np.float64)
    for i, e in enumerate(ents):
        A[i] = np.asarray(e["arr"][:n_worlds], dtype=np.float64)
    H, drift = _hundredths(A)
    even = bool(np.all(H % 2 == 0))
    odd_pct = 100.0 * float(np.mean(H % 2 != 0))
    # the captain: is the 1.5x multiplier exact, and does its product land on
    # the grid the engine counts ties on?
    cpt = 1.5 * A
    cpt_grid_err = float(np.max(np.abs(cpt * 100.0 - np.rint(cpt * 100.0))))
    W1 = np.zeros((1, P), dtype=np.float32)
    W1[0, 0] = 1.5
    mult_exact = bool(float(W1[0, 0]) == 1.5)          # 1.5 is exact in float32
    L = len(idx)
    take = min(n_lineups, L)
    w = np.asarray(f, dtype=np.float64)
    w = w / float(w.sum())
    # Sampled WITHOUT replacement in both cases. A real field holds duplicate
    # lineups and those tie trivially; counting them here would measure the
    # duplication rate instead of the scoring tie rate, which is the thing the
    # lattice changes.
    #
    # Two honest limits on what `field_weighted` is. Drawing without
    # replacement with unequal weights is successive sampling, so the inclusion
    # probabilities are NOT proportional to the field model -- it is a sample
    # concentrated where the field is, not the field. And `top_shared_pct`
    # below is the chance the best of these few thousand lineups is tied, not
    # the chance first place in an 88,000-entry contest is shared; that one
    # needs the analytic field mass and is measured in research/sd_live_ab as
    # win_any minus win_sole.
    uni = rng.choice(L, size=take, replace=False)
    pop = rng.choice(L, size=take, replace=False, p=w)
    out = {"n_worlds": int(n_worlds), "n_lineups_sampled": int(take),
           "universe_lineups": int(L),
           "base_all_even_hundredths": even,
           "base_odd_hundredth_pct": round(odd_pct, 2),
           "base_float_drift_hundredths": round(drift, 9),
           "captain_multiplier_exact_1_5": mult_exact,
           "captain_product_on_engine_grid": bool(cpt_grid_err < 1e-9),
           "captain_grid_err_max_hundredths": round(cpt_grid_err, 9),
           "captain_grid_err_points": round(cpt_grid_err / 100.0, 9)}
    for tag, rows in (("uniform", uni), ("field_weighted", pop)):
        # the exact total, in hundredths: 3 x (base/2) for the captain when the
        # base is even, else the engine's own rounded captain bucket
        cpt_h = (3 * (H[idx[rows, 0]] // 2) if even
                 else np.rint(1.5 * A[idx[rows, 0]] * 100.0).astype(np.int64))
        T = (cpt_h + H[idx[rows, 1]] + H[idx[rows, 2]] + H[idx[rows, 3]]
             + H[idx[rows, 4]] + H[idx[rows, 5]])
        # the same total the engine computes, float32 through the weight matrix
        W = np.zeros((take, P), dtype=np.float32)
        ar = np.arange(take)
        for k in range(1, 6):
            W[ar, idx[rows, k]] = 1.0
        W[ar, idx[rows, 0]] = 1.5
        F = (W @ A.astype(np.float32)).astype(np.float64)
        Fb = np.rint(F * 100.0).astype(np.int64)
        tied_pairs = same_top = 0
        pairs = take * (take - 1) // 2
        for wi in range(T.shape[1]):
            col = T[:, wi]
            cnt = np.bincount(col - col.min())
            cnt = cnt[cnt > 1]
            tied_pairs += int(np.sum(cnt * (cnt - 1) // 2))
            same_top += int((col == col.max()).sum() > 1)
        out[tag] = {
            # A total is legal exactly when every base it is built from is: the
            # captain transform and the five additions are integer arithmetic
            # in hundredths, so they cannot leave the support.
            "total_legal_by_construction": even,
            "total_min_hundredths": int(T.min()), "total_max_hundredths": int(T.max()),
            "engine_bucket_matches_exact_pct": round(100.0 * float(np.mean(Fb == T)), 4),
            "engine_bucket_max_abs_diff_hundredths": int(np.max(np.abs(Fb - T))),
            "exact_tie_rate_pairwise": round(tied_pairs / max(pairs * T.shape[1], 1), 8),
            "top_shared_pct": round(100.0 * same_top / max(T.shape[1], 1), 3),
            "distinct_totals_per_world_median": int(np.median(
                [len(np.unique(T[:, wi])) for wi in range(T.shape[1])]))}
        log(f"[SUP] lineups {tag:14s}: engine bucket matches exact "
            f"{out[tag]['engine_bucket_matches_exact_pct']:.3f}%, "
            f"pairwise exact-tie {out[tag]['exact_tie_rate_pairwise']:.2e}, "
            f"top shared {out[tag]['top_shared_pct']:.2f}% of worlds")
    return out


def run(feed_dir, dg=DG_DEFAULT, log=print):
    from research import sd_board
    S = sd_board.feeds(feed_dir)
    S._cache.clear()
    games = S.weekly_games(sd_board.SEASON, sd_board.WEEK)
    slate, contest = sd_board.slate_and_contest(dg)
    if not slate or not contest:
        raise SystemExit(f"draft group {dg} has no slate or contest")
    import simulate
    teams = {(r.get("team") or "").upper() for r in simulate.parse_dk_csv(slate["csv"])}
    gid = next((g for g, v in games.items()
                if teams & {str(t).upper() for t in (v.get("teams") or [])}), None)
    if gid is None:
        raise SystemExit(f"no simulated game matches {sorted(teams)}")
    game = games[gid]
    out = {"meta": {"stage": "showdown support validation (NOT promoted)",
                    "draft_group_id": int(dg), "contest": contest.get("name"),
                    "game": game.get("label"), "worlds": WORLDS,
                    "discrete_version": S.DISCRETE_VERSION,
                    "provenance": _prov(worlds=WORLDS, model="legacy vs legacy-discrete",
                                        seed={"board": BOARD_SEED, "lineups": LINEUPS_SEED})},
           "modes": {}}
    log(f"[SUP] {game.get('label')} / dg {dg} / {contest.get('name')}")
    for mode, disc in (("legacy", False), ("discrete", True)):
        S._cache.clear()
        sec = {"offense": offense_chain(S, game, disc, log=log)}
        ents, secs, rss = sd_board.ents_for(slate, sd_board.WEEK, disc, n_sims=WORLDS,
                                           seed=BOARD_SEED, log=log)
        sec["pool"] = pool_legality({e["name"]: e for e in ents}, log=log)
        idx, W, allowed = sd_board.universe(ents)
        import dfs_tourney as T
        f, beta = T.field_weights(ents, idx, cpt_mult=1.5)
        sec["lineups"] = lineup_support(ents, idx, f, WORLDS, log=log)
        sec["build"] = {"pool_seconds": round(secs, 1), "peak_rss_mb": round(rss, 1),
                        "legal_lineups": int(len(idx)), "enterable": int(allowed.sum()),
                        "field_beta": round(float(beta), 4)}
        out["modes"][mode] = sec
    lg, dc = out["modes"]["legacy"], out["modes"]["discrete"]
    out["verdict"] = {
        "offense_recomputes_exactly": {"legacy": lg["offense"]["recompute_exact"] == lg["offense"]["n_players"],
                                       "discrete": dc["offense"]["recompute_exact"] == dc["offense"]["n_players"]},
        "whole_pool_legal": {"legacy": lg["pool"]["illegal_players"] == 0,
                             "discrete": dc["pool"]["illegal_players"] == 0},
        "captain_multiplier_exact": {"legacy": lg["lineups"]["captain_multiplier_exact_1_5"],
                                     "discrete": dc["lineups"]["captain_multiplier_exact_1_5"]},
        "captain_product_representable": {"legacy": lg["lineups"]["captain_product_on_engine_grid"],
                                         "discrete": dc["lineups"]["captain_product_on_engine_grid"]},
        "totals_legal": {"legacy": lg["lineups"]["base_all_even_hundredths"],
                         "discrete": dc["lineups"]["base_all_even_hundredths"]},
        "lineup_tie_rate_ratio": (
            round(dc["lineups"]["field_weighted"]["exact_tie_rate_pairwise"]
                  / max(lg["lineups"]["field_weighted"]["exact_tie_rate_pairwise"], 1e-12), 2)
            if lg["lineups"]["field_weighted"]["exact_tie_rate_pairwise"] > 0 else None)}
    log("[SUP] verdict: " + json.dumps(out["verdict"]))
    with open(os.path.join(DATA, "sd_support.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "feeds")
    run(fd, int(sys.argv[2]) if len(sys.argv) > 2 else DG_DEFAULT)
    print("showdown support validation written")
