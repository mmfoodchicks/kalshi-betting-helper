"""Mutation tests of the draft (scratch only; the repo is untouched): each
mutant is a monkeypatch, and the draft must turn red on it. Then the proposed
allocator fix: the draft's A8/A9 go green and the production paths draw for
draw identical."""
import sys, os, io, contextlib, random
os.environ.setdefault("VIGIL_NO_BG", "1")
os.environ.setdefault("ERRLOG_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "errlog.db"))
os.environ["VIGIL_ROOT"] = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))  # the repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import nfl_dfs_csim as C, nfl_dfs_engine as G
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests"))
import guards_conservation as D  # the draft, adopted as tests/guards_conservation.py

def run_draft():
    res = {}
    def ck(name, cond, detail=""):
        res[name.split(" ")[0]] = bool(cond)
    with contextlib.redirect_stdout(io.StringIO()):
        D.run(ck)
    return res

base = run_draft()
print("baseline failing:", sorted(k for k, v in base.items() if not v))
orig_mc, orig_sw, orig_off, orig_deal = C._multinomial_capped, G._split_w, G.dk_offense, G.deal_team

def old_capped(rng, N, shares, cap, rounds=12):           # the pre-fix form: overflow deleted
    return np.minimum(C._multinomial(rng, np.asarray(N).astype(np.int64), np.where(np.asarray(cap) > 0, shares, 0.0)), cap)

def rint_split(total, w):                                  # yards by rint, not largest remainder
    ws = w.sum(axis=0)
    share = np.where(ws > 0, w / np.where(ws > 0, ws, 1.0), 1.0 / w.shape[0])
    return np.rint(share * np.asarray(total)[None, :]).astype(np.int64)

def off_gt(c, n):                                         # bonus at > 300 / > 100
    S = __import__("dk_scoring").NFL_OFF
    z = np.zeros(n, dtype=np.int64); g = lambda k: c.get(k, z)
    pts = (g("pass_yd") * S["pass_yd"] + g("pass_td") * S["pass_td"] + g("int") * S["int"] + g("rush_yd") * S["rush_yd"]
           + g("rush_td") * S["rush_td"] + g("rec") * S["rec"] + g("rec_yd") * S["rec_yd"] + g("rec_td") * S["rec_td"]
           + g("fum") * S["fumble_lost"])
    pts = pts + S["pass_300"] * (g("pass_yd") > 300) + S["rush_100"] * (g("rush_yd") > 100) + S["rec_100"] * (g("rec_yd") > 100)
    return np.round(pts.astype(np.float64), 2)

def deal_no_kneel_yards(players, idx, box, rng, params=None, target_factor=None):
    b = dict(box); out = orig_deal(players, idx, b, rng, params, target_factor)
    qbs = [i for i in idx if (players[i].get("pos") or "").upper() == "QB"]
    if qbs:
        out[qbs[0]]["rush_yd"] = out[qbs[0]]["rush_yd"] + np.asarray(box["kneels"])   # kneels at 0 yards
    return out

for name, setup, teardown in (
        ("capped -> np.minimum (the old TD-deleting form)", lambda: setattr(C, "_multinomial_capped", old_capped), lambda: setattr(C, "_multinomial_capped", orig_mc)),
        ("yards split by rint", lambda: setattr(G, "_split_w", rint_split), lambda: setattr(G, "_split_w", orig_sw)),
        ("bonus thresholds > not >=", lambda: setattr(G, "dk_offense", off_gt), lambda: setattr(G, "dk_offense", orig_off)),
        ("kneels at 0 yards", lambda: setattr(G, "deal_team", deal_no_kneel_yards), lambda: setattr(G, "deal_team", orig_deal))):
    setup()
    try:
        r = run_draft()
    finally:
        teardown()
    print(f"mutant [{name}]: newly failing {sorted(k for k, v in r.items() if not v and base.get(k))}")

# ---- the proposed fix -------------------------------------------------------
def fixed_capped(rng, N, shares, cap, rounds=12):
    cap = np.asarray(cap)
    owed = np.minimum(np.asarray(N).astype(np.int64), cap.sum(axis=0).astype(np.int64))
    alloc = np.zeros(cap.shape, dtype=np.int64)
    while owed.any():                      # each round either finishes or fills a man: <= k + 1 rounds
        room = cap - alloc
        w = np.where(room > 0, shares, 0.0)
        dead = (owed > 0) & ~(w.sum(axis=0) > 0)
        if dead.any():                     # room but no weight on it: deal by the room itself
            w = np.where(dead[None, :] & (room > 0), room.astype(np.float64), w)
        live = (owed > 0) & (w.sum(axis=0) > 0)
        if not live.any():
            break
        take = np.minimum(C._multinomial(rng, np.where(live, owed, 0), w), room)
        alloc = alloc + take
        owed = owed - take.sum(axis=0)
    return alloc

C._multinomial_capped = fixed_capped
r = run_draft()
print("with the proposed fix, failing:", sorted(k for k, v in r.items() if not v))
# draw-for-draw identity on the production paths
import nfl_deep_sim as E
T = E.tables(); L = E.league_profile(T)
bh, ba = G.play_worlds(L, dict(L, abbr="B"), 1500, random.Random(23), T, env_sd=G.ENV_SD)
pl = D._pool("A")
same = True
for mc in (orig_mc, fixed_capped):
    pass
C._multinomial_capped = orig_mc
d0 = G.deal_team(pl, list(range(len(pl))), bh, np.random.default_rng(31))
C._multinomial_capped = fixed_capped
d1 = G.deal_team(pl, list(range(len(pl))), bh, np.random.default_rng(31))
same = all(np.array_equal(d0[i][k], d1[i][k]) for i in d0 for k in d0[i])
print("engine deal on real boxes, fix vs current, identical:", same)
# csim on the guard-style adversarial tiny offence (budgets that cannot fit)
def tiny(tag, mult):
    qb = {"name": f"{tag}QB", "pos": "QB", "team": tag, "means": {"pass_att": 12.0, "pass_cmp": 7.0, "pass_yd": 90.0, "pass_td": 4.0 * mult, "int": 0.5,
          "rush_att": 2.0, "rush_yd": 6.0, "rush_td": 1.5 * mult, "rec": 0.0, "rec_yd": 0.0, "rec_td": 0.0, "rec_tgt": 0.0, "fum": 0.1}}
    out = [qb]
    for nm, po, tg, rc, yd, td, ra, ry, rt in (("WR1", "WR", 5.0, 3.0, 45.0, 2.0 * mult, 0, 0, 0), ("TE1", "TE", 3.0, 2.0, 25.0, 1.5 * mult, 0, 0, 0),
                                               ("RB1", "RB", 2.0, 1.5, 12.0, 0.5 * mult, 3.0, 12.0, 1.2 * mult)):
        out.append({"name": f"{tag}{nm}", "pos": po, "team": tag, "means": {"rec_tgt": tg, "rec": rc, "rec_yd": yd, "rec_td": td, "rush_att": ra, "rush_yd": ry,
                    "rush_td": rt, "int": 0.0, "fum": 0.02, "pass_yd": 0.0, "pass_td": 0.0, "pass_att": 0.0, "pass_cmp": 0.0}})
    return out
gm = {"label": "B @ A", "teams": ["A", "B"], "players": tiny("A", 4.0) + tiny("B", 4.0)}
C._multinomial_capped = orig_mc
s0 = C.simulate_game(gm, n=20000, rng=np.random.default_rng(5))
C._multinomial_capped = fixed_capped
s1 = C.simulate_game(gm, n=20000, rng=np.random.default_rng(5))
print("csim adversarial offence, fix vs current, identical arrays:", all(np.array_equal(a["arr"], b["arr"]) for a, b in zip(s0["players"], s1["players"])),
      "invariants all hold:", all(v[0] for v in C.check_invariants(s1, gm["players"]).values()))
C._multinomial_capped = orig_mc
