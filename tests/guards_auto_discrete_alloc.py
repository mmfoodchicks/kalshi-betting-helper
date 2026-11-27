"""Guards for the EXPERIMENTAL discrete allocation (2026-11-27; nfl_discrete_alloc.py,
research/nfl_discrete_alloc_eval.py, research/reports/nfl_discrete_alloc.md). What each proves:

  * DA1 conservation: the quarterback's completions, passing yards and passing touchdowns ARE his receivers'
    sums in every world (checked independently on a game whose projections leave no "other" bucket);
  * DA2 every stat is an integer event count, yards are per-catch / per-carry sums (no catches, no yards), and
    the player means are the legacy model's means (same latents, same expectation);
  * DA3 More / tie / Less on counts is exact: a tie is a world that lands on the line;
  * DA4 production is untouched: nothing in the app imports the module, its switch is off, and the legacy
    week-board simulator gives the pinned output for a seeded game (byte-identical);
  * DA5 the committed evaluation is the harness's output and the report states its numbers beside their
    definitions; the harness never reads a sealed week.

numpy-free checks always run (CI's runner has no numpy); the numpy ones say so when skipped.

    python3 tests/guards_auto_discrete_alloc.py       # exits 1 on any failed check
"""
import glob
import hashlib
import json
import os
import random
import re
import sys

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)

# sha256 of the legacy simulator's props and players (no sample arrays) for _game() at random.seed(42),
# n=500 -- pinned 2026-11-27 when this experiment was added beside it. A deliberate change to the legacy
# engine re-pins this with a note; an accidental one fails here.
LEGACY_DIGEST = "755004be9748a19c9a1d95fffce9cb999ebee799eeb80a700d91223386489c3c"


def _game():
    pl = []
    for t, o in (("A", "B"), ("B", "A")):
        pl += [{"name": f"{t} QB", "pos": "QB", "team": t, "opp": o, "proj_pts": 18.0,
                "means": {"pass_yd": 240.0, "pass_td": 1.6, "int": 0.8, "rush_yd": 14.0, "rush_td": 0.1, "rec": 0.0,
                          "rec_yd": 0.0, "rec_td": 0.0, "fum": 0.2, "pass_att": 34.0, "pass_cmp": 22.0, "rec_tgt": 0.0,
                          "rush_att": 3.0}},
               {"name": f"{t} RB", "pos": "RB", "team": t, "opp": o, "proj_pts": 13.0,
                "means": {"pass_yd": 0.0, "pass_td": 0.0, "int": 0.0, "rush_yd": 62.0, "rush_td": 0.45, "rec": 3.0,
                          "rec_yd": 20.0, "rec_td": 0.1, "fum": 0.1, "pass_att": 0.0, "pass_cmp": 0.0, "rec_tgt": 4.0,
                          "rush_att": 15.0}},
               {"name": f"{t} WR", "pos": "WR", "team": t, "opp": o, "proj_pts": 14.0,
                "means": {"pass_yd": 0.0, "pass_td": 0.0, "int": 0.0, "rush_yd": 0.0, "rush_td": 0.0, "rec": 11.0,
                          "rec_yd": 150.0, "rec_td": 1.0, "fum": 0.05, "pass_att": 0.0, "pass_cmp": 0.0,
                          "rec_tgt": 16.0, "rush_att": 0.0}},
               {"name": f"{t} TE", "pos": "TE", "team": t, "opp": o, "proj_pts": 8.0,
                "means": {"pass_yd": 0.0, "pass_td": 0.0, "int": 0.0, "rush_yd": 0.0, "rush_td": 0.0, "rec": 8.0,
                          "rec_yd": 70.0, "rec_td": 0.5, "fum": 0.02, "pass_att": 0.0, "pass_cmp": 0.0,
                          "rec_tgt": 12.3, "rush_att": 0.0}}]
    # the receivers' projections are the quarterback's exactly (22 catches, 240 yards, 1.6 TDs; 32.3 targets
    # = 34 attempts x 0.95), so the "other receivers" bucket is empty and the sums can be checked directly
    return {"label": "B @ A", "teams": ["A", "B"], "players": pl}


def legacy_digest():
    import nfl_dfs_sim
    random.seed(42)
    s = nfl_dfs_sim.simulate_game(_game(), n=500)
    body = {"props": s["props"], "players": s["players"], "stacks": s["stacks"]}

    def r4(x):                          # a last-bit libm difference between machines must not move the digest
        if isinstance(x, float):
            return round(x, 4)
        if isinstance(x, dict):
            return {k: r4(v) for k, v in x.items()}
        if isinstance(x, list):
            return [r4(v) for v in x]
        return x
    return hashlib.sha256(json.dumps(r4(body), sort_keys=True).encode()).hexdigest()


def run(ck):
    import nfl_discrete_alloc as D
    try:
        import numpy as np
    except ImportError:
        np = None

    # ---- DA4 production untouched (numpy-free) -------------------------------------------------------
    importers = []
    for p in glob.glob(os.path.join(_ROOT, "*.py")):
        if os.path.basename(p) == "nfl_discrete_alloc.py":
            continue
        src = open(p, encoding="utf-8").read()
        if re.search(r"\bnfl_discrete_alloc\b", src):
            importers.append(os.path.basename(p))
    dg = legacy_digest()
    ck("DA4 production is untouched: no app module names nfl_discrete_alloc (only the research harness and these "
       "guards do), its switch ENABLED_IN_PRODUCTION is False, and the legacy week-board simulator gives the pinned "
       "output for a seeded game, byte for byte",
       not importers and D.ENABLED_IN_PRODUCTION is False and dg == LEGACY_DIGEST, f"{importers} {dg}")

    # ---- DA5 the committed evaluation and the report (numpy-free) ------------------------------------
    ev = json.load(open(os.path.join(_ROOT, "research", "data", "nfl_discrete_alloc_eval.json")))
    rep = open(os.path.join(_ROOT, "research", "reports", "nfl_discrete_alloc.md"), encoding="utf-8").read()
    hsrc = open(os.path.join(_ROOT, "research", "nfl_discrete_alloc_eval.py"), encoding="utf-8").read()
    rec = ev["moments"]["rec"]
    ck("DA5 the committed evaluation: the discrete path's conservation never broke, the report quotes its numbers "
       "beside their definitions (the realized-over-model variance ratio, the half-line log losses, the exact-count "
       "log score, the DraftKings-point PIT), and the harness never reads a sealed week (2026 week 13 on)",
       ev["conservation"]["discrete_violations"] == {"cmp": 0, "pass_td": 0, "pass_yd": 0} and ev["meta"]["worlds"] == 2000
       and f"{rec['spread']['legacy']['realized_over_model']}" in rep and f"{rec['spread']['discrete']['realized_over_model']}" in rep
       and "realized squared error of the box score around the model's mean, over the model's variance" in rep
       and f"{ev['lines']['discrete']['rec']['log_loss']}" in rep and f"{ev['lines']['legacy']['rec']['log_loss']}" in rep
       and f"{ev['exact_count_log_score']['discrete']['mean']}" in rep and f"{ev['dk']['legacy']['pit_ks']}" in rep
       and 'SEALED_FROM = {"2026": 13}' in hsrc and 'assert not (s in SEALED_FROM and w >= SEALED_FROM[s])' in hsrc
       and 'WINDOW = {"2025": range(1, 13)' in hsrc)

    if np is None:
        ck("DA1-DA3 skipped: numpy is not installed here (the PC and the dev box run them)", True)
        return
    sim = D.simulate_game(_game(), n=6000, rng=np.random.default_rng(3))
    P = {r["name"]: r["comps"] for r in sim["players"]}
    ok = all((P[f"{t} QB"]["pass_cmp"] == sum(P[f"{t} {x}"]["rec"] for x in ("RB", "WR", "TE"))).all()
             and (P[f"{t} QB"]["pass_yd"] == sum(P[f"{t} {x}"]["rec_yd"] for x in ("RB", "WR", "TE"))).all()
             and (P[f"{t} QB"]["pass_td"] == sum(P[f"{t} {x}"]["rec_td"] for x in ("RB", "WR", "TE"))).all()
             and (P[f"{t} QB"]["pass_att"] >= P[f"{t} QB"]["pass_cmp"]).all() for t in ("A", "B"))
    ck("DA1 conservation, every world: the quarterback's completions, passing yards and passing touchdowns equal his "
       "receivers' catches, yards and touchdowns (no other bucket on this game), attempts never fewer than "
       "completions, and the module's own count of violations is 0",
       ok and all(v == 0 for c in sim["conservation"].values() for v in c.values()), str(sim["conservation"]))
    ints = all(v.dtype.kind == "i" for c in P.values() for v in c.values())
    no_catch_no_yards = all(((c["rec"] == 0) <= (c["rec_yd"] == 0)).all() for c in P.values())
    no_carry_no_yards = all(((c["rush_att"] == 0) <= (c["rush_yd"] == 0)).all() for c in P.values())
    import nfl_dfs_sim
    random.seed(9)
    leg = {r["name"]: r["comps"] for r in nfl_dfs_sim.simulate_game(_game(), n=6000, with_components=True)["players"]}
    worst = []
    for nm in ("A WR", "B TE", "A RB"):
        for k in ("rec", "rec_yd", "rush_yd"):
            dv, lv = P[nm][k].astype(float), np.asarray(leg[nm][k], dtype=float)
            if lv.mean() <= 0:
                continue
            se = (dv.var() / dv.size + lv.var() / lv.size) ** 0.5
            if abs(dv.mean() - lv.mean()) > 4 * se:
                worst.append((nm, k, round(dv.mean(), 2), round(lv.mean(), 2)))
    ck("DA2 every stat is an integer count; a player with no catch has no receiving yards and no carry no rushing "
       "yards (yards are sums over events); and each player's mean receptions, receiving and rushing yards is the "
       "legacy model's within 4 SE (the same latents carry the same expectation)",
       ints and no_catch_no_yards and no_carry_no_yards and not worst, str(worst))
    v = P["A WR"]["rec"]
    m = D.mtl(v, 11)
    ck("DA3 More / tie / Less on counts is exact: the tie is the worlds that land on the line, the three sum to n",
       m["counts"] == (int((v > 11).sum()), int((v == 11).sum()), int((v < 11).sum())) and sum(m["counts"]) == v.size
       and abs(m["more"] + m["tie"] + m["less"] - 1) < 1e-12 and m["tie"] > 0)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
