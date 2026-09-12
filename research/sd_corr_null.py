"""A null distribution for the worst correlation move, not a single control run.

The readiness study compared legacy against legacy-latent-discrete and found the
worst correlation move among the 32 pairs where BOTH players are rosterable to
be 0.0209, against a single legacy-vs-legacy control whose maximum was 0.0188.
Those are close, and that is the problem: one observed maximum is not a noise
threshold. The maximum of 32 correlated deltas is itself a random variable with
a spread, and 0.0209 could sit comfortably inside its distribution or outside it.

So this runs the control many times. Several legacy runs at the same depth on
the same game, every pairwise combination of them scored over the SAME 32 pairs
-- read from research/data/sd_outliers.json rather than recomputed, so there is
no chance of comparing a different pair set -- giving a null sample of

    max |correlation(run A) - correlation(run B)|

and where 0.0209 falls among them. Cheap on purpose: no board is rebuilt, no
contest is scored, nothing is enumerated. Only the game simulation runs.

WHAT THIS IS NOT. Six independent runs give fifteen pairwise comparisons, but
those fifteen are NOT fifteen independent observations -- each run appears in
five of them, so they share variance. The fraction of controls at or above the
observed value is therefore an EMPIRICAL TAIL FRACTION, not a p-value from
independent samples, and it is named that way in the artifact. It answers the
question that was actually asked -- is 0.0209 ordinary simulation-scale
variation for this statistic -- and it does not license an inferential claim
beyond that. A real p-value would need disjoint control pairs, which would cost
several times the compute for a conclusion this already supports.

    python3 -m research.sd_corr_null <feed dir> [draft_group_id] [runs]
"""
import itertools
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")

WORLDS = 20000          # the depth the measurement being tested was made at
RUNS = 6                # 6 legacy runs -> 15 pairwise controls
DG_DEFAULT = 153086


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def rosterable_pairs():
    """The EXACT 32 pairs the 0.0209 came from, read off the artifact."""
    src = json.load(open(os.path.join(DATA, "sd_outliers.json")))
    p = src["pairs"]
    pairs = [(r["a"], r["b"]) for r in p["rosterable_top10"]]
    # rosterable_top10 is only the worst ten; rebuild the full set from all_pairs
    # under the same two conditions the artifact recorded.
    from research.sd_outliers import ROSTERABLE, STRATEGIC
    means = {r["name"]: r["proj"] for r in src["means"]["players"]}
    full = [(r["a"], r["b"]) for r in p["all_pairs"]
            if r["rel"] in STRATEGIC
            and (means.get(r["a"]) or 0.0) >= ROSTERABLE
            and (means.get(r["b"]) or 0.0) >= ROSTERABLE]
    assert len(full) == p["rosterable_n_pairs"], (len(full), p["rosterable_n_pairs"])
    assert set(pairs) <= set(full)
    return full, float(p["rosterable_delta_max"]), float(p["rosterable_delta_p50"])


def run(feed_dir, dg=DG_DEFAULT, runs=RUNS, log=print):
    from research import sd_board
    S = sd_board.feeds(feed_dir)
    S._cache.clear()
    games = S.weekly_games(sd_board.SEASON, sd_board.WEEK)
    slate, _contest = sd_board.slate_and_contest(dg)
    import simulate
    teams = {(r.get("team") or "").upper() for r in simulate.parse_dk_csv(slate["csv"])}
    gid = next((g for g, v in games.items()
                if teams & {str(t).upper() for t in (v.get("teams") or [])}), None)
    game = games[gid]
    pairs, observed, observed_p50 = rosterable_pairs()
    log(f"[NULL] {game.get('label')}: {len(pairs)} rosterable pairs, {runs} legacy runs "
        f"x {WORLDS:,} worlds, testing observed max |delta| = {observed}")
    arrs = []
    for i in range(runs):
        S._random.seed(9000 + 17 * i)
        sim = S.simulate_game(game, n=WORLDS, with_samples=True, discrete=False)
        arrs.append({p["name"]: np.asarray(p["arr"], dtype=np.float64) for p in sim["players"]})
        log(f"[NULL]   legacy run {i + 1}/{runs} done")
    corr = []
    for A in arrs:
        corr.append({(a, b): float(np.corrcoef(A[a], A[b])[0, 1]) for a, b in pairs
                     if a in A and b in A})
    nulls, p50s = [], []
    for i, j in itertools.combinations(range(runs), 2):
        d = [abs(corr[i][k] - corr[j][k]) for k in corr[i] if k in corr[j]]
        nulls.append(max(d))
        p50s.append(float(np.median(d)))
    nulls = np.asarray(sorted(nulls))
    p50s = np.asarray(sorted(p50s))
    # Where does the observed value sit? The share of control comparisons whose
    # own maximum is at least as large as the observed one. NOT a p-value: the
    # comparisons share underlying runs (see the module docstring).
    ge = int((nulls >= observed).sum())
    frac = ge / len(nulls)
    out = {"meta": {"stage": "null distribution for the worst rosterable correlation move",
                    "game": game.get("label"), "draft_group_id": int(dg),
                    "worlds": WORLDS, "legacy_runs": int(runs),
                    "pairwise_controls": int(len(nulls)),
                    "n_pairs": len(pairs),
                    "note": ("a single control's observed maximum is not a threshold: the maximum "
                             "of 32 correlated deltas is itself random. These are pairwise "
                             "legacy-vs-legacy controls over the SAME pairs, read off "
                             "sd_outliers.json rather than recomputed."),
                    "provenance": _prov(worlds=WORLDS, model="legacy x N")},
           "pairs": [list(p) for p in pairs],
           "observed": {"max_abs_delta": observed, "median_abs_delta": observed_p50,
                        "source": "research/data/sd_outliers.json (legacy vs legacy-latent-discrete)"},
           "null_max_abs_delta": {"values": [round(float(x), 4) for x in nulls],
                                  "min": round(float(nulls.min()), 4),
                                  "p50": round(float(np.median(nulls)), 4),
                                  "p90": round(float(np.percentile(nulls, 90)), 4),
                                  "max": round(float(nulls.max()), 4)},
           "null_median_abs_delta": {"p50": round(float(np.median(p50s)), 4),
                                     "max": round(float(p50s.max()), 4)},
           "verdict": {"controls_at_or_above_observed": ge,
                       "controls": int(len(nulls)),
                       "independent_runs": int(runs),
                       "empirical_tail_fraction": round(frac, 4),
                       "not_a_p_value": ("the pairwise controls share underlying runs, so this is "
                                         "an empirical tail fraction, not a p-value from "
                                         "independent samples"),
                       "inside_null": bool(observed <= nulls.max()),
                       "reading": ("inside the null: the observed worst move is no larger than "
                                   "what identical runs produce" if observed <= nulls.max() else
                                   "outside every control: a small real correlation consequence, "
                                   "to be documented rather than dismissed")}}
    log(f"[NULL] null max |delta| over {len(nulls)} controls: "
        f"min {nulls.min():.4f} p50 {np.median(nulls):.4f} p90 "
        f"{np.percentile(nulls, 90):.4f} max {nulls.max():.4f}")
    log(f"[NULL] observed {observed:.4f} -> {ge}/{len(nulls)} control comparisons at or above it "
        f"(empirical tail fraction {frac:.3f}, NOT a p-value: {runs} runs share across "
        f"{len(nulls)} pairs)  ({out['verdict']['reading']})")
    with open(os.path.join(DATA, "sd_corr_null.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "feeds")
    run(fd, int(sys.argv[2]) if len(sys.argv) > 2 else DG_DEFAULT,
        int(sys.argv[3]) if len(sys.argv) > 3 else RUNS)
    print("correlation null distribution written")
