"""Sensitivity, NOT a fit: how much of L1's penalty is the missing cross-side coupling.

Stage 2F says the owner's six-man Detroit/New Orleans stack falls from 1st to
128th on the top 0.1% when the constrained simulator replaces the legacy one,
while L2 -- same-team concentration, no game stack -- falls only to 5th. It is
tempting to subtract one from the other and call the difference the cross-game
term. That is too neat: the two lineups hold different players with different
roles, shares and environments, so L2 is a comparator, not a controlled
counterfactual.

This measures the thing directly instead. Every fitted parameter is held
exactly as frozen. One knob is varied, `nfl_dfs_csim.simulate_game(xside=)`,
the standard deviation of a game-level scoring latent shared by both offences,
which is the structure section V of the audit report proposes for constrained
v2. The sweep spans the model's own quarterback-against-opposing-quarterback
correlation, about 0.03, up through the 0.235 the 2025 holdout observed.

If L1 climbs steeply as the coupling approaches what was observed while L2
stays put, most of L1's extra punishment is the model's known miss. If L1 sits
near 100th even at the observed coupling, the six-man build really is mostly
dead on its own merits.

NOTHING HERE MAY BE PROMOTED. No artifact is refitted, `csim_params.json` is
untouched, 2025 is not reopened, and no xside value becomes a default. The
justification for varying this particular knob is that the miss it addresses
was already established on the holdout, so measuring sensitivity to it is not
tuning against unseen data.

    python3 -m research.xside_sweep <dir with ents.json> <dir with the cached feeds>
"""
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
REPORTS = os.path.join(ROOT, "research", "reports")

from research import stage2f as F          # noqa: E402  the board, probe and stack helpers

BASE_SEED = 20260913
WORLDS = 6000          # the light grid Stage 2F's parameter draws use
FIELD_N = 60_000
CAND_N = 20_000
#: knob -> roughly the correlation it buys, calibrated on this slate
LEVELS = (0.0, 0.14, 0.20, 0.245, 0.28)


def qb_opp_corr(games, xside, n=6000, seed=31):
    """The pooled correlation between the two starting quarterbacks' points."""
    import nfl_dfs_csim as C
    out = []
    for gid, g in games.items():
        sim = C.simulate_game(g, n=n, rng=np.random.default_rng(seed + abs(hash(gid)) % 1000), xside=xside)
        by = {}
        for pl in sim["players"]:
            if (pl["pos"] or "").upper() == "QB":
                by.setdefault(pl["team"], []).append(pl)
        tt = [t for t in by if by[t]]
        if len(tt) != 2:
            continue
        q = [max(by[t], key=lambda x: x["proj_pts"]) for t in tt]
        out.append(float(np.corrcoef(np.asarray(q[0]["arr"]), np.asarray(q[1]["arr"]))[0, 1]))
    return (float(np.mean(out)) if out else None), len(out)


def run(slate_dir, feed_dir, log=print):
    import nfl_dfs_sim as S
    t0 = time.time()
    ents_base = json.load(open(os.path.join(slate_dir, "ents.json")))
    raw = json.load(open(os.path.join(feed_dir, f"proj_{F.SEASON}_{F.WEEK}.json")))
    dfn = json.load(open(os.path.join(feed_dir, f"proj_{F.SEASON}_{F.WEEK}_def.json")))
    kck = json.load(open(os.path.join(feed_dir, f"proj_{F.SEASON}_{F.WEEK}_k.json")))
    S._get = lambda url: (dfn if "DEF" in url else kck if "position[]=K" in url else raw)
    S._cache.clear()
    games = S.weekly_games(F.SEASON, F.WEEK)
    rows = []
    for xs in LEVELS:
        corr, ng = qb_opp_corr(games, xs)
        log(f"[XS] xside {xs:.3f}: QB vs opposing QB {corr:.4f} over {ng} games")
        S._cache.clear()
        pool = S.player_pool(F.WEEK, n=WORLDS, season=F.SEASON, model="constrained",
                             seed=BASE_SEED, xside=xs)
        ents = F.entries(ents_base, pool)
        b = F.board(ents, log=lambda m: None, seed=BASE_SEED,
                    field_n=FIELD_N, cand_n=CAND_N, worlds=WORLDS)
        pr = F.probe_rows(b, F.PROBES)
        st = F.stack_standing(b, top=200)
        rows.append({"xside": xs, "qb_opp_corr": corr, "games": ng,
                     "probes": pr, "stacks": st, "candidates": b.get("allowed")})
        for i, r in enumerate(pr):
            if r.get("available"):
                log("[XS]   L%d: top1 %.2f%% (#%s)  top0.1%% %.3f%% (#%s)  p99 %.1f"
                    % (i + 1, r["top1_pct"], format(r["rank_top1"], ","),
                       r["top01_pct"], format(r["rank_top01"], ","), r.get("p99") or 0.0))
        log(f"[XS]   5+ from one game {st['share_5plus']:.3f}  6+ {st['share_6plus']:.3f}")
    out = {"meta": {"stage": "xside sensitivity (NOT a fit, nothing promotable)",
                    "provenance": F._prov(seed=BASE_SEED, model="constrained + xside knob",
                                          data_split=f"{F.SEASON} week {F.WEEK}, current slate"),
                    "worlds": WORLDS, "field": FIELD_N, "candidates_drawn": CAND_N,
                    "observed_2025_holdout_qb_opp": 0.235,
                    "frozen_params_untouched": True,
                    "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                    "seconds": round(time.time() - t0, 1)},
           "levels": rows}
    import dfs_tourney as T
    with open(os.path.join(DATA, "xside_sweep.json"), "w") as fh:
        json.dump(T.plain(out), fh, indent=1, sort_keys=True)
    write_report(out)
    return out


def write_report(st):
    L = ["# Cross-side coupling sensitivity (not a fit)\n",
         "Every fitted parameter is frozen. One research knob is varied: the standard",
         "deviation of a game-level scoring latent shared by both offences. Nothing here",
         "is promotable and no artifact was refitted.\n",
         "| xside | QB vs opposing QB | L1 top 1% (rank) | L1 top 0.1% (rank) | L1 p99 | L2 top 1% (rank) | L2 top 0.1% (rank) | L2 p99 | 5+ one game | 6+ |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in st["levels"]:
        p = r["probes"]
        def cell(i, k, rk):
            return (f"{p[i][k]:.2f}% (#{p[i][rk]:,})" if p[i].get("available") else "-")
        L.append(f"| {r['xside']:.3f} | {r['qb_opp_corr']:.4f} | {cell(0,'top1_pct','rank_top1')} | "
                 f"{cell(0,'top01_pct','rank_top01')} | {p[0].get('p99', 0):.1f} | "
                 f"{cell(1,'top1_pct','rank_top1')} | {cell(1,'top01_pct','rank_top01')} | "
                 f"{p[1].get('p99', 0):.1f} | {r['stacks']['share_5plus']:.3f} | {r['stacks']['share_6plus']:.3f} |")
    L.append("\nThe 2025 holdout observed 0.235. Read the row nearest it against the top row.\n")
    with open(os.path.join(REPORTS, "xside_sweep.md"), "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    sd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "slate")
    fd = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DATA, "feeds")
    run(sd, fd)
    print("xside sweep written")
