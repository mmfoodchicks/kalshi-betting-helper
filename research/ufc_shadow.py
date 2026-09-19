"""UFC shadow universe, on identical CORRECTED worlds: the current rule (never
both fighters of one bout) against the full DraftKings-legal universe.

The rule lives in simulate.dfs_build as `exclusive_group` on the bout key, so
the optimizer's legal universe cannot contain both sides of a fight; DraftKings
itself allows it. Whether stacking a fight is smart is a strategy question, so
nothing here changes production. This script answers the reviewer's question
for one card: does any same-fight pair enter the strongest lineups, and in
particular the five-round fights, when the rule is lifted?

"Identical corrected worlds": the captured board's index-aligned samples are
re-blended with the per-bout SHUFFLE (the reviewer's fix for the cross-bout
correlation the served reblend manufactures), in memory only, and both
universes are scored on exactly those arrays. The optimizer's objectives are
per-player (projection, ceiling), so the rule only bites where two sides of
one bout both project well enough to displace another fighter; the joint
worlds then say what such a pair is really worth (one side always loses).

    python3 -m research.ufc_shadow <capture dir>
"""
import copy
import hashlib
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ROSTER, CAP, CV = 6, 50000, 0.6
PORTFOLIO_N = 20


def shuffled_reblend(fa, fb, seed=0):
    """simulate._reblend_bout with the one-line fix: the index list shuffled
    once per bout, the same list applied to both fighters."""
    aw = fa.get("won_arr")
    aa, ba = fa.get("dk_arr"), fb.get("dk_arr")
    if not (aw and aa and ba) or len(aw) != len(aa) or len(aa) != len(ba):
        return False
    target = fa.get("fair_win")
    if target is None or fa.get("kalshi_cents") is None:
        return False
    win_idx = [i for i, w in enumerate(aw) if w]
    loss_idx = [i for i, w in enumerate(aw) if not w]
    if not win_idx or not loss_idx:
        return False
    n = len(aa)
    n_win = max(0, min(n, int(round(n * (target / 100.0)))))
    rng = random.Random(seed)
    idx = ([win_idx[rng.randrange(len(win_idx))] for _ in range(n_win)]
           + [loss_idx[rng.randrange(len(loss_idx))] for _ in range(n - n_win)])
    rng.shuffle(idx)
    for f, arr in ((fa, aa), (fb, ba)):
        res = [arr[i] for i in idx]
        s = sorted(res)
        q = lambda t: s[min(len(s) - 1, int(t * len(s)))]  # noqa: E731
        f["blend_proj"] = round(sum(s) / len(s), 1)
        f["blend_ceil"] = round(q(0.9), 1)
        f["blend_floor"] = round(q(0.1), 1)
        f["blend_arr"] = [round(v, 1) for v in res]
    return True


def _names(lu):
    return [p["name"] for p in lu]


def _pairs(lu, five_round):
    games = {}
    for p in lu:
        games.setdefault(p.get("game"), []).append(p["name"])
    both = [{"game": g, "fighters": v, "five_round": g in five_round} for g, v in games.items() if len(v) == 2]
    return both


def describe(lu, five_round, tag):
    import simulate
    if not lu:
        return {"tag": tag, "lineup": None}
    js = simulate.dfs_sim(lu, n=20000, cv=CV)
    return {"tag": tag, "lineup": _names(lu), "salary": sum(int(p["salary"]) for p in lu),
            "proj_sum": round(sum(float(p.get("proj") or 0) for p in lu), 1),
            "joint": js, "same_bout_pairs": _pairs(lu, five_round)}


def run(capdir, log=print):
    import simulate
    import ufc_sim
    slate = json.load(open(os.path.join(capdir, "dk_slate.json")))
    board = json.load(open(os.path.join(capdir, "board.json")))
    five_round = set()
    bout_of = {}
    for bt in board["bouts"]:
        for side in ("a", "b"):
            bout_of[simulate._norm_name(bt[side]["name"])] = bt
    # corrected worlds, research-only: the served reblend replaced in memory
    simulate._reblend_bout = shuffled_reblend
    ufc_sim.board = lambda n=15000, _b=board: copy.deepcopy(_b)
    players = simulate.parse_dk_csv(slate["slate"]["csv"])
    status = simulate.apply_ufc(players)
    for p in players:
        bt = bout_of.get(simulate._norm_name(p["name"])) or bout_of.get(simulate._norm_name(p["name"]).split()[-1])
        if bt and bt.get("rounds") == 5:
            five_round.add(p.get("game"))
    unmatched = [p["name"] for p in players if not p.get("arr")]
    exclusive = lambda p: p.get("game")  # noqa: E731  (dfs_build's own rule)
    out = {"capture_dir": os.path.basename(capdir), "apply_ufc": status, "players": len(players), "unmatched_fall_back_to_csv": unmatched,
           "five_round_games": sorted(five_round), "worlds": "the captured board's samples re-blended with the per-bout shuffle (research only)",
           "universes": {"rule": "exclusive_group = bout (production)", "dk_legal": "no exclusive group (DraftKings allows both sides)"},
           "results": {}}
    simulate._estimate_ownership(players, ROSTER, "ufc")
    for objective in ("projection", "ceiling"):
        simulate._set_values(players, objective, CV)
        out["results"][f"single_{objective}"] = {
            "rule": describe(simulate.dfs_optimize(players, ROSTER, CAP, exclusive_group=exclusive), five_round, "rule"),
            "dk_legal": describe(simulate.dfs_optimize(players, ROSTER, CAP, exclusive_group=None), five_round, "dk_legal")}
        log(f"[shadow] {objective}: rule {out['results'][f'single_{objective}']['rule']['lineup']} | dk_legal {out['results'][f'single_{objective}']['dk_legal']['lineup']}")
    out["results"]["leverage"] = {
        "rule": describe(simulate._leverage_lineup(players, ROSTER, CAP, CV, exclusive), five_round, "rule"),
        "dk_legal": describe(simulate._leverage_lineup(players, ROSTER, CAP, CV, None), five_round, "dk_legal")}
    port = {}
    for tag, ex in (("rule", exclusive), ("dk_legal", None)):
        lus = simulate._portfolio(players, ROSTER, CAP, "projection", CV, PORTFOLIO_N, 60.0, 1, ex) or []
        rows = [describe(lu, five_round, tag) for lu in lus]
        port[tag] = {"n": len(rows), "with_same_bout_pair": sum(1 for r in rows if r["same_bout_pairs"]),
                     "with_five_round_pair": sum(1 for r in rows if any(x["five_round"] for x in r["same_bout_pairs"])),
                     "pairs_seen": sorted({x["game"] for r in rows for x in r["same_bout_pairs"]}),
                     "median_of_joint_ceiling": (sorted(r["joint"]["ceiling"] for r in rows)[len(rows) // 2] if rows else None),
                     "lineups": rows}
    out["results"][f"portfolio_{PORTFOLIO_N}"] = port
    # every bout's own pair, valued in the joint worlds: what stacking a fight is worth at all
    by = {simulate._norm_name(p["name"]): p for p in players}
    pairs = []
    for bt in board["bouts"]:
        pa = by.get(simulate._norm_name(bt["a"]["name"])) or by.get(simulate._norm_name(bt["a"]["name"]).split()[-1])
        pb = by.get(simulate._norm_name(bt["b"]["name"])) or by.get(simulate._norm_name(bt["b"]["name"]).split()[-1])
        if not (pa and pb and pa.get("arr") and pb.get("arr")):
            continue
        L = min(len(pa["arr"]), len(pb["arr"]))
        joint = sorted(pa["arr"][i] + pb["arr"][i] for i in range(L))
        q = lambda t: joint[min(L - 1, int(t * L))]  # noqa: E731
        pairs.append({"game": pa.get("game"), "five_round": bt.get("rounds") == 5, "fighters": [pa["name"], pb["name"]],
                      "salary": int(pa["salary"]) + int(pb["salary"]), "proj_sum": round(float(pa["proj"]) + float(pb["proj"]), 1),
                      "joint_floor": round(q(0.1), 1), "joint_median": round(q(0.5), 1), "joint_ceiling": round(q(0.9), 1),
                      "sum_of_solo_ceilings": round(float(pa.get("ceil_proj") or 0) + float(pb.get("ceil_proj") or 0), 1),
                      "proj_per_1k": round((float(pa["proj"]) + float(pb["proj"])) / ((int(pa["salary"]) + int(pb["salary"])) / 1000.0), 2)})
    pairs.sort(key=lambda r: -r["joint_ceiling"])
    out["bout_pairs_in_joint_worlds"] = pairs
    path = os.path.join(capdir, "shadow.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True, default=str)
    raw = open(path, "rb").read()
    man_path = os.path.join(capdir, "manifest.json")
    man = json.load(open(man_path))
    man["files"]["shadow"] = {"file": "shadow.json", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    with open(man_path, "w") as fh:
        json.dump(man, fh, indent=1, sort_keys=True, default=str)
    log(f"[shadow] written {path}")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python3 -m research.ufc_shadow <capture dir>")
    run(sys.argv[1])
