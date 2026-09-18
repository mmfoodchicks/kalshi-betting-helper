"""The dependence DST-OPP actually turns on, measured from history and from the
served simulator, so the S5 rule is held for a number rather than a phrase.

DST-OPP bans rostering the defense that faces your captain. Captain Mahomes
plus the DEN defense: DEN DST's DraftKings score is a function of KC's OWN
offensive output -- the interceptions and fumbles KC gives away, the points KC
allows to be scored against it. So the covariance the rule turns on is a
side's offensive production against that same side's giveaways. Not "cross-side
covariance" in general (S5 section O, reason 2, rewritten 2026-09-18).

The served simulator draws interceptions and fumbles at their raw mean with no
game, quarterback or script factor (nfl_dfs_sim.simulate_game: ints =
_pois(m["int"]), fums = _pois(m["fum"])), so in every simulated world a
quarterback's giveaways are independent of how his offense is doing. The only
coupling left is DraftKings' own -1 per interception and per lost fumble, which
pulls his points DOWN when he gives the ball away -- a scoring identity, not a
football dependence. This module measures the served value directly, from a
components-bearing simulation of the pinned DEN @ KC game, beside the history.

What the history can and cannot say. research/data/player_weeks_2022_2025.csv.gz
carries, per player-week, actual passing attempts and yards, rushing and
receiving yards, DraftKings points, interceptions thrown and fumbles lost, with
team, opponent and game key. It has NO sacks, NO points allowed and NO defense
rows, so the DST score itself cannot be reconstructed here; that needs the
team-week source. What CAN be measured is the giveaway half of the
dependence: does an offense that produces more give the ball away more, or
less, in the same game -- at the team-week level and for its lead quarterback.
An empty interception or fumble cell means zero (the export omits zeros; a
quarterback with 33 attempts and an empty cell threw none that week).

Descriptive, all four seasons: 2025 was the blind holdout for Stage 2E and is
used here only to describe a relationship, never to fit one.

    python3 -m research.dst_dependence
"""
import csv
import gzip
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
TABLE = os.path.join(DATA, "player_weeks_2022_2025.csv.gz")
FEEDS = os.path.join(DATA, "feeds")

OFFENSE = ("QB", "RB", "WR", "TE", "FB")
BOOT = 2000
SEED = 20260918


def _f(v):
    try:
        return float(v) if v not in ("", None) else 0.0
    except ValueError:
        return 0.0


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


def load():
    rows = []
    with gzip.open(TABLE, "rt") as fh:
        for r in csv.DictReader(fh):
            if r["has_act"] != "1" or r["keep"] != "1" or r["pos"] not in OFFENSE:
                continue
            rows.append(r)
    return rows


def team_weeks(rows):
    """{(season, week, team): aggregates} plus the lead quarterback's line."""
    tw = {}
    for r in rows:
        k = (r["season"], r["week"], r["team"])
        t = tw.setdefault(k, {"opp": r["opp"], "game_key": r["game_key"], "pass_att": 0.0, "pass_yd": 0.0,
                              "rush_yd": 0.0, "rec_yd": 0.0, "off_dk": 0.0, "ints": 0.0, "fums": 0.0,
                              "qb": None})
        t["pass_att"] += _f(r["act_pass_att"])
        t["pass_yd"] += _f(r["act_pass_yd"])
        t["rush_yd"] += _f(r["act_rush_yd"])
        t["rec_yd"] += _f(r["act_rec_yd"])
        t["off_dk"] += _f(r["act_dk"])
        t["ints"] += _f(r["act_pass_int"])
        t["fums"] += _f(r["act_fum_lost"])
        if r["pos"] == "QB" and _f(r["act_pass_att"]) > 0:
            q = {"name": r["name"], "att": _f(r["act_pass_att"]), "yd": _f(r["act_pass_yd"]),
                 "dk": _f(r["act_dk"]), "int": _f(r["act_pass_int"]), "fum": _f(r["act_fum_lost"])}
            if t["qb"] is None or q["att"] > t["qb"]["att"]:
                t["qb"] = q
    for t in tw.values():
        t["giveaways"] = t["ints"] + t["fums"]
        t["total_yd"] = t["pass_yd"] + t["rush_yd"]
    return tw


def _pearson(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    return float(np.corrcoef(x, y)[0, 1])


def spearman(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    # ties: average ranks
    for arr, r in ((x, rx), (y, ry)):
        vals, inv, cnt = np.unique(arr, return_inverse=True, return_counts=True)
        if (cnt > 1).any():
            sums = np.zeros(len(vals)); np.add.at(sums, inv, r)
            r[:] = (sums / cnt)[inv]
    return float(np.corrcoef(rx, ry)[0, 1])


def boot_ci(x, y, fn, n=BOOT, seed=SEED):
    rng = np.random.default_rng(seed)
    x, y = np.asarray(x, float), np.asarray(y, float)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(x), len(x))
        vals.append(fn(x[i], y[i]))
    return [round(float(np.percentile(vals, 2.5)), 4), round(float(np.percentile(vals, 97.5)), 4)]


def quartile_means(x, y, labels=("Q1 low", "Q2", "Q3", "Q4 high")):
    x, y = np.asarray(x, float), np.asarray(y, float)
    qs = np.percentile(x, [25, 50, 75])
    bins = np.digitize(x, qs)
    return {labels[b]: {"n": int((bins == b).sum()), "x_mean": round(float(x[bins == b].mean()), 2),
                        "y_mean": round(float(y[bins == b].mean()), 4)} for b in range(4)}


def slope(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    b = float(np.polyfit(x, y, 1)[0])
    return b


def historical(log=print):
    rows = load()
    tw = team_weeks(rows)
    keys = [k for k, t in tw.items() if t["pass_att"] >= 10 and t["qb"]]
    T = [tw[k] for k in keys]
    opp = {}
    for k in keys:
        t = tw[k]
        ok = (k[0], k[1], t["opp"])
        if ok in tw and tw[ok]["game_key"] == t["game_key"]:
            opp[k] = tw[ok]
    paired = [(tw[k], opp[k]) for k in keys if k in opp]
    log(f"[DST] {len(rows):,} player-weeks -> {len(T):,} team-weeks with a passing offense, "
        f"{len(paired):,} with the opponent's line in the same game")

    def rel(xname, x, yname, y):
        return {"x": xname, "y": yname, "n": len(x),
                "pearson": round(_pearson(x, y), 4), "pearson_ci95": boot_ci(x, y, _pearson),
                "spearman": round(spearman(x, y), 4), "spearman_ci95": boot_ci(x, y, spearman),
                "slope_per_unit_x": round(slope(x, y), 5),
                "by_quartile_of_x": quartile_means(x, y)}

    off_dk = [t["off_dk"] for t in T]
    give = [t["giveaways"] for t in T]
    ints = [t["ints"] for t in T]
    att = [t["pass_att"] for t in T]
    yd = [t["total_yd"] for t in T]
    q_dk = [t["qb"]["dk"] for t in T]
    q_int = [t["qb"]["int"] for t in T]
    q_give = [t["qb"]["int"] + t["qb"]["fum"] for t in T]
    q_att = [t["qb"]["att"] for t in T]
    q_yd = [t["qb"]["yd"] for t in T]
    out = {
        "n_player_weeks": len(rows), "n_team_weeks": len(T), "n_paired_games": len(paired),
        "seasons": sorted({k[0] for k in keys}),
        # the dependence DST-OPP turns on: a side's production vs its own giveaways
        "team": {
            "giveaways_vs_offense_dk": rel("team offensive DK points", off_dk, "team giveaways (INT + lost FUM)", give),
            "giveaways_vs_pass_attempts": rel("team passing attempts", att, "team giveaways", give),
            "giveaways_vs_total_yards": rel("team pass + rush yards", yd, "team giveaways", give),
            "ints_vs_pass_attempts": rel("team passing attempts", att, "team interceptions", ints),
        },
        "qb1": {
            "giveaways_vs_dk": rel("QB1 DraftKings points", q_dk, "QB1 giveaways (INT + lost FUM)", q_give),
            "ints_vs_attempts": rel("QB1 passing attempts", q_att, "QB1 interceptions", q_int),
            "ints_vs_yards": rel("QB1 passing yards", q_yd, "QB1 interceptions", q_int),
            "ints_vs_dk": rel("QB1 DraftKings points", q_dk, "QB1 interceptions", q_int),
        },
        # the cross-side channel: does a side give the ball away more when the
        # OTHER offense is producing (trailing, throwing to catch up)?
        "cross_side": {
            "giveaways_vs_opp_offense_dk": rel("opponent offensive DK points", [o["off_dk"] for _, o in paired],
                                               "team giveaways", [t["giveaways"] for t, _ in paired]),
            "offense_dk_vs_opp_offense_dk": rel("opponent offensive DK points", [o["off_dk"] for _, o in paired],
                                                "team offensive DK points", [t["off_dk"] for t, _ in paired]),
        },
        "not_measurable_here": ["sacks (no column)", "points allowed (no column)",
                                "the DST score itself (no defense rows); needs the team-week source"],
    }
    return out


def served_model(n=6000, seed=SEED, log=print):
    """The same relationships inside the SERVED simulator, on the pinned DEN @ KC
    game, from a components-bearing run (research-only flag with_components)."""
    from research import sd_board
    S = sd_board.feeds(FEEDS)
    S._cache.clear()
    games = S.weekly_games(sd_board.SEASON, sd_board.WEEK) or {}
    game = None
    for g in games.values():
        teams = {str(t).upper() for t in (g.get("teams") or [])}
        if teams == {"DEN", "KC"}:
            game = g
            break
    if game is None:
        return {"error": "DEN @ KC not found in the pinned week feed", "games": len(games)}
    S._random.seed(int(seed))
    sim = S.simulate_game(game, n=int(n), with_samples=True, discrete=True, with_components=True)
    out = {"game": "DEN @ KC (pinned feeds)", "worlds": int(n), "model": sim.get("model", "legacy-latent-discrete"),
           "mechanism": ('ints = _pois(m["int"]); fums = _pois(m["fum"]) -- drawn at the raw mean with no '
                         "env, quarterback or script factor; the only remaining coupling to production "
                         "is DraftKings' -1 per interception and per lost fumble inside the score itself")}
    by_team = {}
    for p in sim["players"]:
        c = p.get("comps") or {}
        if not c:
            continue
        team = p.get("team")
        d = by_team.setdefault(team, {"dk": None, "give": None, "qb": None})
        arr = np.asarray(p["arr"], float)
        give = np.asarray(c["int"], float) + np.asarray(c["fum"], float)
        d["dk"] = arr if d["dk"] is None else d["dk"] + arr
        d["give"] = give if d["give"] is None else d["give"] + give
        if p.get("pos") == "QB" and (d["qb"] is None or float(np.mean(arr)) > float(np.mean(d["qb"]["arr"]))):
            d["qb"] = {"name": p["name"], "arr": arr, "int": np.asarray(c["int"], float),
                       "give": give, "att_proxy": np.asarray(c["pass_yd"], float)}
    res = {}
    for team, d in by_team.items():
        if d["dk"] is None:
            continue
        r = {"team_giveaways_vs_offense_dk": round(_pearson(d["dk"], d["give"]), 4)}
        if d["qb"] is not None:
            q = d["qb"]
            r["qb1"] = q["name"]
            r["qb1_giveaways_vs_dk"] = round(_pearson(q["arr"], q["give"]), 4)
            r["qb1_ints_vs_pass_yards"] = round(_pearson(q["att_proxy"], q["int"]), 4)
            # the mechanical part: what -1 per giveaway alone does to that correlation
            r["qb1_giveaways_vs_dk_with_penalty_removed"] = round(_pearson(q["arr"] + q["give"], q["give"]), 4)
        res[team] = r
    out["by_team"] = res
    return out


def run(log=print):
    hist = historical(log)
    sim = served_model(log=log)
    key = hist["qb1"]["ints_vs_attempts"]
    tg = hist["team"]["giveaways_vs_offense_dk"]
    out = {"meta": {"stage": "the DST dependence DST-OPP uses: history vs the served simulator",
                    "question": ("does an offense that produces more in a game give the ball away "
                                 "more or less in that same game -- the covariance a captain-plus-"
                                 "opposing-defense build turns on"),
                    "source": os.path.relpath(TABLE, ROOT), "bootstrap": BOOT, "seed": SEED,
                    "descriptive_only": "2025 was the Stage 2E holdout; nothing here is fitted",
                    "provenance": _prov(model="history vs legacy-latent-discrete", seed=SEED)},
           "historical": hist, "served_model": sim,
           "reading": {
               "qb1_ints_vs_attempts": {"pearson": key["pearson"], "ci95": key["pearson_ci95"],
                                        "per_10_attempts": round(10 * key["slope_per_unit_x"], 4)},
               "team_giveaways_vs_offense_dk": {"pearson": tg["pearson"], "ci95": tg["pearson_ci95"],
                                                "per_10_dk_points": round(10 * tg["slope_per_unit_x"], 4)},
               "served_model_team_value": {t: r["team_giveaways_vs_offense_dk"] for t, r in (sim.get("by_team") or {}).items()},
               "what_it_means_for_dst_opp": (
                   "if real giveaways RISE with volume/production, the opposing defense scores more in "
                   "exactly the worlds the captain does, so captain-plus-opposing-DST builds are "
                   "UNDER-valued by a simulator that draws giveaways independently, and DST-OPP's "
                   "+0.047 is if anything low; if giveaways FALL with production, those builds are "
                   "OVER-valued and the rule is vindicated. The sign and size are above; the DST score "
                   "itself (sacks, points allowed) is not measurable from this table.")}}
    with open(os.path.join(DATA, "dst_dependence.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[DST] QB1 INT vs attempts: r={key['pearson']:+.3f} CI {key['pearson_ci95']} "
        f"({10 * key['slope_per_unit_x']:+.3f} INT per 10 attempts); "
        f"team giveaways vs offense DK: r={tg['pearson']:+.3f} CI {tg['pearson_ci95']}; "
        f"served model team value: {out['reading']['served_model_team_value']}")
    return out


if __name__ == "__main__":
    run()
    print("dst dependence written")
