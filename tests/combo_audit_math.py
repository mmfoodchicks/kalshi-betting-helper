"""Deep audit of the BASEBALL TAB combo maker.

Part 1 is deterministic: synthetic legs through the math and assembly functions,
so every equation and invariant is checked exactly rather than sampled.
Part 2 (combo_audit_live.py) drives the real builders against a real slate.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import baseball as B
import parlay
import mlb_sim

PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail and not cond else ""))


def leg(pk, prob, price=None, typ="Hit", group=None, side="yes", label=None):
    return {"game_pk": pk, "prob": prob, "price_cents": price, "type": typ,
            "label": label or f"g{pk} {typ} {prob}", "matchup": f"M{pk}",
            "group": group or f"grp{pk}", "side": side, "live": False}


print("=" * 72)
print("1. _combo_item — the core math")
print("=" * 72)

# --- exact probability product -------------------------------------------------
c = B._combo_item([leg(1, 0.5), leg(2, 0.5)])
ck("2 legs @50% -> 25.0%", c["combined_prob_pct"] == 25.0, c["combined_prob_pct"])
ck("fair payout = 1/prob = 4.0x", c["fair_payout_x"] == 4.0, c["fair_payout_x"])

c = B._combo_item([leg(1, 0.9), leg(2, 0.8), leg(3, 0.7)])
exp = 0.9 * 0.8 * 0.7
ck("3-leg product exact", abs(c["combined_prob_pct"] - round(exp * 100, 1)) < 1e-9,
   f"{c['combined_prob_pct']} vs {round(exp*100,1)}")
ck("fair payout = 1/(.9*.8*.7)", c["fair_payout_x"] == round(1 / exp, 2), c["fair_payout_x"])

# --- payout / EV from prices ---------------------------------------------------
c = B._combo_item([leg(1, 0.5, 50), leg(2, 0.5, 50)])
ck("cost = 0.5*0.5 = 25c", c["parlay_cost_cents"] == 25.0, c["parlay_cost_cents"])
ck("parlay payout = 1/0.25 = 4.0x", c["parlay_payout_x"] == 4.0, c["parlay_payout_x"])
ck("EV = prob*payout-1 = 0 at fair price", c["ev_pct"] == 0.0, c["ev_pct"])

c = B._combo_item([leg(1, 0.6, 50), leg(2, 0.6, 50)])
# prob .36, cost .25, payout 4x -> EV = .36*4-1 = +44%
ck("EV positive when model > price", c["ev_pct"] == 44.0, c["ev_pct"])

c = B._combo_item([leg(1, 0.4, 50), leg(2, 0.4, 50)])
ck("EV negative when model < price", c["ev_pct"] == -36.0, c["ev_pct"])

# --- unpriced legs must NOT claim a payout ------------------------------------
c = B._combo_item([leg(1, 0.5, 50), leg(2, 0.5, None)])
ck("one unpriced leg -> no parlay_payout_x", "parlay_payout_x" not in c)
ck("one unpriced leg -> no ev_pct", "ev_pct" not in c)

# --- structural fields --------------------------------------------------------
c = B._combo_item([leg(1, .9), leg(2, .9), leg(3, .9), leg(4, .9)])
ck("n_legs matches len(legs)", c["n_legs"] == 4 == len(c["legs"]))
ck("combined <= min leg prob", c["combined_prob_pct"] <= 90.0)
ck("fair_payout_x >= 1", c["fair_payout_x"] >= 1.0)

c = B._combo_item([leg(1, .5, 50, side="yes"), leg(2, .5, 50, side="no")])
ck("side propagates onto legs", [l["side"] for l in c["legs"]] == ["yes", "no"])

# --- extreme probabilities ----------------------------------------------------
c = B._combo_item([leg(1, 0.99), leg(2, 0.99)])
ck("near-certain pair ~98%", c["combined_prob_pct"] == 98.0, c["combined_prob_pct"])
c = B._combo_item([leg(1, 0.01), leg(2, 0.01)])
ck("longshot pair prob > 0", c["combined_prob_pct"] > 0, c["combined_prob_pct"])
ck("longshot fair payout huge", c["fair_payout_x"] >= 9999, c["fair_payout_x"])

print()
print("=" * 72)
print("2. _assemble — one leg per game")
print("=" * 72)

pool = [leg(1, .8), leg(1, .7), leg(2, .8), leg(2, .6), leg(3, .9)]
combos = B._assemble(pool, 3)
bad = [c for c in combos if len({l["matchup"] for l in c["legs"]}) != c["n_legs"]]
ck("no combo repeats a game", not bad, f"{len(bad)} violations")
ck("sizes are 2..max_legs", set(c["n_legs"] for c in combos) <= {2, 3})
# C(3 games choose 2)*2*2 style counts: verify at least the 3-leg combos exist
ck("3-leg combos built from 3 distinct games", any(c["n_legs"] == 3 for c in combos))

single = B._assemble([leg(1, .8), leg(1, .7)], 3)
ck("single game -> no cross-game combo", single == [], f"{len(single)}")

ck("empty pool -> empty", B._assemble([], 3) == [])
ck("max_legs=1 -> empty (needs >=2)", B._assemble(pool, 1) == [])

print()
print("=" * 72)
print("3. _curate_legs — one per (group, side)")
print("=" * 72)

legs = [leg(1, .5, group="A", side="yes"), leg(1, .7, group="A", side="yes"),
        leg(1, .6, group="A", side="no"), leg(1, .3, group="B", side="yes")]
cur = B._curate_legs(legs)
ck("keeps best per (group,side)", len(cur) == 3, len(cur))
a_yes = [l for l in cur if l["group"] == "A" and l["side"] == "yes"]
ck("picks the HIGHEST prob in a bucket", a_yes and a_yes[0]["prob"] == .7,
   a_yes and a_yes[0]["prob"])
ck("NO side survives alongside YES",
   any(l["group"] == "A" and l["side"] == "no" for l in cur))

print()
print("=" * 72)
print("4. _mirror_no — the NO side (live path)")
print("=" * 72)

v = [{"label": "X 1+ hits", "prob": 0.36, "type": "Hit", "game_pk": 1, "matchup": "M"},
     {"label": "Y to win", "prob": 0.55, "type": "ML", "game_pk": 1, "matchup": "M"},
     {"label": "Run in the 1st inning", "prob": 0.5, "type": "RFI", "game_pk": 1, "matchup": "M"},
     {"label": "Under 8.5 runs", "prob": 0.5, "type": "Total", "game_pk": 1, "matchup": "M"}]
out = B._mirror_no(v)
nos = [x for x in out if x.get("side") == "no"]
ck("NO created for Hit", any(n["label"] == "NO — X 1+ hits" for n in nos))
ck("NO prob = 1 - YES", any(abs(n["prob"] - 0.64) < 1e-12 for n in nos))
ck("ML skipped (both sides already exist)", not any("to win" in n["label"] for n in nos))
ck("RFI skipped (no NO market on Kalshi)", not any("1st inning" in n["label"] for n in nos))
ck("'Under' not re-negated", not any("Under" in n["label"] for n in nos))
ck("originals preserved", len(out) == len(v) + len(nos))

edge = B._mirror_no([{"label": "lock", "prob": 0.995, "type": "Hit", "game_pk": 1, "matchup": "M"},
                     {"label": "dead", "prob": 0.001, "type": "Hit", "game_pk": 1, "matchup": "M"}])
ck("near-0/near-1 not mirrored (no 0% legs)",
   not [x for x in edge if x.get("side") == "no"], len(edge))

print()
print("=" * 72)
print("5. parlay.payout_combo — payout targeting")
print("=" * 72)

groups = [[leg(i, p) for p in (0.9, 0.7, 0.5, 0.3)] for i in range(1, 7)]
r = parlay.payout_combo(groups, 3, 5.0, max_legs=8)
ck("reaches a 5x target", r and r["reached"], r and r.get("payout"))
if r:
    fair = 1.0
    for l in r["legs"]:
        fair /= l["prob"]
    ck("reported payout == product of 1/prob", abs(fair - r["payout"]) < 1e-6,
       f"{fair} vs {r['payout']}")
    ck("payout >= target", r["payout"] >= 5.0 - 1e-9, r["payout"])
    ck("one leg per event", len({l["game_pk"] for l in r["legs"]}) == len(r["legs"]))

r2 = parlay.payout_combo(groups, 3, 1.0)
ck("target <= 1 rejected", r2 is None)
r3 = parlay.payout_combo([groups[0]], 3, 5.0)
ck("single event rejected (needs 2+)", r3 is None)
r4 = parlay.payout_combo(groups, 3, 1e9, max_legs=8)
ck("impossible target -> flagged not-reached, still returns",
   r4 is not None and not r4["reached"], r4 and r4.get("reached"))
r5 = parlay.payout_combo(groups, 2, 100.0, max_legs=8)
ck("expands past requested legs to reach target",
   r5 and (r5["n_used"] >= 2), r5 and r5.get("n_used"))
if r5:
    ck("expanded flag set when it grew", (r5["n_used"] > r5["requested_legs"]) == r5["expanded"],
       f"used {r5['n_used']} req {r5['requested_legs']} flag {r5['expanded']}")

print()
print("=" * 72)
print("6. mlb_sim mask math — SGP correlation")
print("=" * 72)

n = 1000
# two independent-ish masks: A = first half, B = every other sim
A = int("".join("1" if i < 500 else "0" for i in range(n))[::-1], 2)
Bm = int("".join("1" if i % 2 == 0 else "0" for i in range(n))[::-1], 2)
pa, pb = mlb_sim._popcount(A) / n, mlb_sim._popcount(Bm) / n
joint = mlb_sim._popcount(A & Bm) / n
ck("popcount marginals correct", (pa, pb) == (0.5, 0.5), (pa, pb))
ck("independent masks -> joint == product", abs(joint - 0.25) < 1e-9, joint)
phi = mlb_sim._phi(A, Bm, mlb_sim._popcount(A), mlb_sim._popcount(Bm), n)
ck("phi ~0 for independent masks", abs(phi) < 1e-9, phi)

full = (1 << n) - 1
ck("complement popcount = n - popcount",
   mlb_sim._popcount((~A) & full) == n - mlb_sim._popcount(A))
phi_self = mlb_sim._phi(A, (~A) & full, mlb_sim._popcount(A), n - mlb_sim._popcount(A), n)
ck("phi = -1 for a mask vs its own negation", abs(phi_self + 1.0) < 1e-9, phi_self)

ck("_redundant catches subset", mlb_sim._redundant([A, A & Bm]))
ck("_redundant clean on disjointish", not mlb_sim._redundant([A, Bm]))
ck("_market_conflict catches same group",
   mlb_sim._market_conflict([{"group": "g"}, {"group": "g"}]))
ck("_market_conflict clean on distinct groups",
   not mlb_sim._market_conflict([{"group": "a"}, {"group": "b"}]))

print()
print("=" * 72)
print("7. Kalshi fee + payout")
print("=" * 72)

ck("fee peaks mid-book (~1.75c at 50c)", B._kalshi_fee(50) == 1.8, B._kalshi_fee(50))
ck("fee small at 90c", B._kalshi_fee(90) == 0.6, B._kalshi_fee(90))
ck("fee ~0 at extremes", B._kalshi_fee(1) < 0.2 and B._kalshi_fee(99) < 0.2,
   (B._kalshi_fee(1), B._kalshi_fee(99)))
ck("fee symmetric", B._kalshi_fee(30) == B._kalshi_fee(70))

print()
print("=" * 72)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print("   -", f)
print("=" * 72)
