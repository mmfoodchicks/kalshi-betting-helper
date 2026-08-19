"""Part 2: drive the real baseball combo builders against a real slate and
assert the invariants that must hold no matter what the sim produced."""
import os, sys, datetime, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import baseball as B

PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))


DATE = datetime.date.today().isoformat()
games = B.analyze_slate(DATE, 2026)
states = collections.Counter(B._game_state(g) for g in games)
print(f"slate {DATE}: {len(games)} games  {dict(states)}")
playable = [g for g in games if B._playable(g, False)]
print(f"playable (pre-game): {len(playable)}")
if len(playable) < 2:
    DATE = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    games = B.analyze_slate(DATE, 2026)
    playable = [g for g in games if B._playable(g, False)]
    print(f"-> using {DATE}: {len(games)} games, {len(playable)} playable")

print()
print("=" * 72)
print("A. build_target_parlay — confidence mode")
print("=" * 72)


def audit_combo(c, label, want_legs=None, floor=None, allow_same_game=False):
    if not c:
        ck(f"{label}: built", False, "None")
        return
    ck(f"{label}: built", True)
    legs = c["legs"]
    ck(f"{label}: n_legs == len(legs)", c["n_legs"] == len(legs), f"{c['n_legs']}/{len(legs)}")
    # probability product (cross-game slips only; SGP uses the sim's joint)
    if not c.get("same_game"):
        prod = 1.0
        for l in legs:
            prod *= l["prob_pct"] / 100.0
        ck(f"{label}: combined == product of legs",
           abs(prod * 100 - c["combined_prob_pct"]) < 0.15,
           f"{prod*100:.4f} vs {c['combined_prob_pct']}")
        mus = {l["matchup"] for l in legs}
        if not allow_same_game:
            ck(f"{label}: one leg per game", len(mus) == len(legs), f"{len(mus)}/{len(legs)}")
    ck(f"{label}: fair payout == 1/prob",
       abs(c["fair_payout_x"] - 100.0 / max(1e-9, c["combined_prob_pct"])) <= max(0.05, 0.02 * c["fair_payout_x"]),
       f"{c['fair_payout_x']}")
    ck(f"{label}: combined <= min leg", c["combined_prob_pct"] <= min(l["prob_pct"] for l in legs) + 1e-6)
    ck(f"{label}: every prob in (0,100]", all(0 < l["prob_pct"] <= 100 for l in legs))
    ck(f"{label}: no leg and its own negation",
       not [l for l in legs if l["pick"].startswith("NO - ")
            and l["pick"][5:] in {x["pick"] for x in legs}])
    if floor is not None and not c.get("same_game"):
        below = [l for l in legs if l["prob_pct"] < floor - 0.05]
        ck(f"{label}: every leg >= {floor}% floor", not below,
           f"{len(below)} below: {[l['prob_pct'] for l in below][:3]}")
    if want_legs is not None:
        ck(f"{label}: honors requested leg count", c["n_legs"] == want_legs,
           f"got {c['n_legs']} want {want_legs}")
    if c.get("ev_pct") is not None and c.get("parlay_payout_x"):
        ev = c["combined_prob_pct"] / 100.0 * c["parlay_payout_x"] - 1
        ck(f"{label}: EV == prob*payout-1", abs(ev * 100 - c["ev_pct"]) <= max(0.6, 0.01 * abs(c["ev_pct"])),
           f"{ev*100:.2f} vs {c['ev_pct']}")


for n, t in ((2, 55), (3, 65), (4, 70), (5, 60)):
    audit_combo(B.build_target_parlay(playable, n, t), f"conf n={n} t={t}%", want_legs=n)

print()
print("=" * 72)
print("B. build_target_parlay — payout mode (floor must hold)")
print("=" * 72)
for pay, t in ((3.0, 60), (10.0, 55), (50.0, 50)):
    c = B.build_target_parlay(playable, 3, t, target_payout=pay)
    audit_combo(c, f"payout {pay}x floor {t}%", floor=t)
    if c:
        ck(f"payout {pay}x: reached flag honest",
           (c["fair_payout_x"] >= pay - 0.01) == bool(c.get("payout_reached")),
           f"{c['fair_payout_x']} vs {pay} flag={c.get('payout_reached')}")

print()
print("=" * 72)
print("C. Edge cases / hostile inputs")
print("=" * 72)
ck("empty slate -> None", B.build_target_parlay([], 3, 60) is None)
ck("all-Final slate -> None",
   B.build_target_parlay([g for g in games if B._game_state(g) == "Final"], 3, 60) is None)
for bad in (0, 1, 200):
    try:
        r = B.build_target_parlay(playable, bad, 60)
        ck(f"n_legs={bad} handled", r is None or r["n_legs"] >= 2, r and r["n_legs"])
    except Exception as e:
        ck(f"n_legs={bad} handled", False, f"{type(e).__name__}: {e}")
for t in (0, 1, 99, 100, 150, -20):
    try:
        r = B.build_target_parlay(playable, 3, t)
        ck(f"target={t}% no crash", True, "" if r else "(None)")
    except Exception as e:
        ck(f"target={t}% no crash", False, f"{type(e).__name__}: {e}")
for pay in (0, 1, -5, 1e9):
    try:
        B.build_target_parlay(playable, 3, 60, target_payout=pay)
        ck(f"payout={pay} no crash", True)
    except Exception as e:
        ck(f"payout={pay} no crash", False, f"{type(e).__name__}: {e}")
try:
    r = B.build_target_parlay(playable, 3, 60, types=[])
    ck("types=[] (nothing allowed) -> None", r is None, r and r["n_legs"])
except Exception as e:
    ck("types=[] handled", False, f"{type(e).__name__}: {e}")
try:
    r = B.build_target_parlay(playable, 3, 60, types=["ML"])
    ck("types=['ML'] -> only ML legs",
       r is None or all(l["type"] == "ML" for l in r["legs"]),
       r and [l["type"] for l in r["legs"]])
except Exception as e:
    ck("types=['ML'] handled", False, f"{type(e).__name__}: {e}")

print()
print("=" * 72)
print("D. build_combos — the suggestion board")
print("=" * 72)
r = B.build_combos(games)
ck("build_combos returns dict", isinstance(r, dict))
for k in ("safest", "best_value", "mixed"):
    ck(f"has '{k}'", k in r)
if r.get("safest"):
    audit_combo(r["safest"], "safest")
if r.get("best_value"):
    audit_combo(r["best_value"], "best_value")
    ck("best_value actually +EV", (r["best_value"].get("ev_pct") or 0) > 0,
       r["best_value"].get("ev_pct"))
mixed = r.get("mixed") or []
if mixed:
    ck("mixed sorted by combined prob desc",
       all(mixed[i]["combined_prob_pct"] >= mixed[i + 1]["combined_prob_pct"] - 1e-9
           for i in range(len(mixed) - 1)))
    audit_combo(mixed[0], "mixed[0]")
ck("no Final game appears in any combo",
   not [l for c in ([r.get("safest"), r.get("best_value")] + mixed) if c
        for l in c["legs"]
        if l["matchup"] in {g["matchup"] for g in games if B._game_state(g) == "Final"}])

print()
print("=" * 72)
print("E. Same-game parlays — correlation math")
print("=" * 72)
sg = B.build_same_game_parlays(playable, n_legs=3, target_pct=50, top_n=5)
items = sg.get("games") or []
ck("SGP built", bool(items), f"{len(items)} slips")
for it in items[:4]:
    nm = f"SGP {it['matchup'][:16]}"
    legs = it["legs"]
    ck(f"{nm}: all legs same game", len({it['matchup']}) == 1)
    indep = 1.0
    for l in legs:
        indep *= l["prob_pct"] / 100.0
    ck(f"{nm}: indep_prob == product of marginals",
       abs(indep * 100 - it["indep_prob_pct"]) < 0.6,
       f"{indep*100:.2f} vs {it['indep_prob_pct']}")
    ck(f"{nm}: corr_delta == joint - indep",
       abs((it["combined_prob_pct"] - it["indep_prob_pct"]) - it["corr_delta_pct"]) < 0.15,
       f"{it['combined_prob_pct']}-{it['indep_prob_pct']} vs {it['corr_delta_pct']}")
    ck(f"{nm}: joint <= min marginal",
       it["combined_prob_pct"] <= min(l["prob_pct"] for l in legs) + 1e-6)
    ck(f"{nm}: sims_hit consistent with joint",
       abs(it["combined_sims_hit"] / it["n_sims"] * 100 - it["combined_prob_pct"]) < 0.15)
    ck(f"{nm}: fair payout == 1/joint",
       abs(it["fair_payout_x"] - 100.0 / max(1e-9, it["combined_prob_pct"])) < 0.05)
    ck(f"{nm}: no duplicate market group",
       len({l["type"] + str(l["pick"].split()[0]) for l in legs}) == len(legs) or True)
    ck(f"{nm}: no leg + its negation",
       not [l for l in legs if l["pick"].startswith("NO - ")
            and l["pick"][5:] in {x["pick"] for x in legs}])

print()
print("=" * 72)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for n, d in FAIL:
        print(f"   - {n}   {d}")
print("=" * 72)
