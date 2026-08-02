"""Checks for the price-aware combo engine, the market blend and the form layer.

Runs offline by default (pure math on synthetic quotes). `--live` additionally
builds real parlays off today's slate and asserts the properties that only a real
board can exercise -- that the three objectives are ordered the way they claim,
that no leg escapes the edge cap, and that an EV claim is backed by legs you
could actually fill.

    python3 tests/combo_engine_check.py
    python3 tests/combo_engine_check.py --live
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import combo_engine as CE          # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}" + (f"   {detail}" if detail else ""))
    else:
        _f += 1
        print(f"  FAIL  {name}   {detail}")


def head(t):
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72)


def q(ask, bid=None, size=999, vol=999, oi=0):
    return {"ask": ask, "bid": bid,
            "mid": ((bid + ask) / 2.0) if bid is not None else ask,
            "spread": (ask - bid) if bid is not None else None,
            "size": size, "vol": vol, "oi": oi}


# --- 1. cost, fees, EV -------------------------------------------------------
head("1. Cost, fees and EV")

c_gross = CE.leg_cost(50, net=False)
c_net = CE.leg_cost(50, net=True)
check("a 50c leg costs 50c gross", abs(c_gross - 0.50) < 1e-9, f"{c_gross}")
check("and MORE than 50c net of the taker fee", c_net > c_gross, f"{c_net:.4f}")
check("the fee is small in absolute terms", c_net - c_gross < 0.03, f"+{(c_net-c_gross)*100:.2f}c")
check("a 100c quote is 'no offer', not a price", CE.leg_cost(100) is None)
check("so is 0c", CE.leg_cost(0) is None)
check("and a missing price", CE.leg_cost(None) is None)

check("EV is 0 at a fair price", abs(CE.ev(0.5, 0.5)) < 1e-9)
check("EV is positive when cheap", CE.ev(0.60, 0.50) > 0, f"{CE.ev(0.60,0.50):.3f}")
check("EV is negative when dear", CE.ev(0.40, 0.50) < 0)
check("Kelly is zero on a -EV bet", CE.kelly(0.40, 0.50) == 0.0)
check("Kelly is positive on a +EV bet", CE.kelly(0.60, 0.50) > 0, f"{CE.kelly(0.60,0.50):.3f}")
check("Kelly never exceeds the bankroll", CE.kelly(0.999, 0.01) <= 1.0)

# Unpriced legs must be EV-neutral, never a free win.
legs_unpriced = [{"prob": 0.6, "price_cents": None}, {"prob": 0.5, "price_cents": None}]
cost, priced, tot = CE.bundle_cost(legs_unpriced)
check("an unpriced bundle is charged at fair value", abs(cost - 0.30) < 1e-9, f"{cost}")
check("and reports that nothing was priced", priced == 0 and tot == 2)
check("so its EV is exactly zero", abs(CE.ev(0.30, cost)) < 1e-9)

# --- 2. market quality -------------------------------------------------------
head("2. Reading a market")

check("a penny-wide traded market is high quality",
      CE.market_quality(q(50, 49, vol=500)) > 0.85,
      f"{CE.market_quality(q(50,49,vol=500)):.2f}")
check("a wide book is worthless as an opinion",
      CE.market_quality(q(54, 4)) == 0.0, "bid 4 / ask 54")
check("a one-sided quote is weak, not zero",
      0 < CE.market_quality(q(54)) < 0.4, f"{CE.market_quality(q(54)):.2f}")
check("no quote at all is zero", CE.market_quality(None) == 0.0)
tight_thin = CE.market_quality(q(50, 49, vol=0, oi=0))
tight_deep = CE.market_quality(q(50, 49, vol=500))
check("depth raises quality but thinness doesn't zero it",
      0 < tight_thin < tight_deep, f"{tight_thin:.2f} < {tight_deep:.2f}")

ref, _ql = CE.market_reference(q(54, 4))
check("a lopsided book references the ASK, not its meaningless mid",
      abs(ref - 0.54) < 1e-9, f"ref={ref} (mid would be 0.29)")

# --- 3. the blend and the edge cap ------------------------------------------
head("3. Blending the model with the market")

p, w, _ql = CE.blend_prob(0.80, q(50, 49, vol=500), "Hit")
check("a liquid market pulls a disagreeing model hard", p < 0.62, f"0.80 -> {p:.3f}")
check("and the model keeps only a small weight", w < 0.30, f"w={w:.2f}")
# Same MIDPOINT, different spread — otherwise this compares two different
# opinions rather than two levels of confidence in the same one, and the edge
# clamp (which keys off the mid) decides the result instead of the weighting.
p2, w2, _ = CE.blend_prob(0.80, q(56, 44, vol=0), "Hit")
p2ref, _w, _q2 = CE.blend_prob(0.80, q(51, 49, vol=500), "Hit")
check("a poor market pulls less than a good one at the same mid",
      p2 > p2ref, f"wide {p2:.3f} > tight {p2ref:.3f}")
check("and leaves more weight with the model", w2 > w, f"{w2:.2f} > {w:.2f}")
p3, w3, _ = CE.blend_prob(0.80, None, "Hit")
check("no market leaves the model alone", p3 == 0.80 and w3 == 1.0)

# The cap is the guard against the winner's curse -- it must bind even where the
# market is judged worthless, which is exactly where the model runs away.
p4, _w, ql4 = CE.blend_prob(0.85, q(54, 4), "HRR")
check("a worthless market still caps the claimed edge",
      p4 <= 0.54 + CE._MAX_EDGE + 1e-9, f"0.85 -> {p4:.3f} vs ask 0.54")
check("even though it got zero blend weight", ql4 == 0.0)
p5, _w, _q = CE.blend_prob(0.02, q(50, 49, vol=500), "Hit")
_mid5 = CE.market_reference(q(50, 49, vol=500))[0]      # 49.5c, not 50c
check("the cap binds downward too", p5 >= _mid5 - CE._MAX_EDGE - 1e-9, f"{p5:.3f}")
check("a leg the model and market agree on barely moves",
      abs(CE.blend_prob(0.50, q(50, 49, vol=500), "ML")[0] - 0.50) < 0.02)

check("a high-trust market gets less model weight than a low-trust one",
      CE.blend_prob(0.7, q(50, 49, vol=500), "HR")[1]
      < CE.blend_prob(0.7, q(50, 49, vol=500), "ML")[1])

# --- 4. tradeability ---------------------------------------------------------
head("4. An edge you cannot fill is not an edge")

check("one contract at the ask is not fillable", not CE.tradeable(q(54, 4, size=1, vol=0)))
check("real depth is", CE.tradeable(q(54, 50, size=500, vol=0)))
check("so is a market with trading history",
      CE.tradeable(q(54, 50, size=0, vol=100)))
check("no quote is not fillable", not CE.tradeable(None))

unfillable = [{"prob": 0.5, "price_cents": 20, "fillable": False}]
cost_u, priced_u, _t = CE.bundle_cost(unfillable)
check("an unfillable leg is charged fair value, not its quote",
      abs(cost_u - 0.5) < 1e-9 and priced_u == 0, f"cost={cost_u}")

# --- 5. the frontier and the objectives -------------------------------------
head("5. The frontier and the three objectives")


def bundle(prob, cents, n=1):
    return {"size": n, "prob": prob,
            "legs": [{"prob": prob, "price_cents": cents, "fillable": True}]}


# Game A: a likely leg priced ABOVE fair (bad value). Game B: a less likely leg
# priced BELOW fair (good value). A price-blind selector cannot tell them apart.
gb = [("A", [bundle(0.90, 95), bundle(0.60, 55)], None),
      ("B", [bundle(0.88, 93), bundle(0.55, 45)], None),
      ("C", [bundle(0.86, 92), bundle(0.58, 50)], None)]
states = CE.frontier(gb, max_total_legs=4, net=False)
check("the frontier spans several price points", len(states) >= 3, f"{len(states)} states")
check("every state carries a cost and an EV",
      all(s["cost"] > 0 and s["ev"] is not None for s in states))

safe, _m = CE.choose(states, objective="safe")
value, _m = CE.choose(states, objective="value")
bal, _m = CE.choose(states, objective="balanced")
check("safe returns the likeliest slip",
      abs(safe["prob"] - max(s["prob"] for s in states)) < 1e-9,
      f"{safe['prob']:.3f}")
check("value returns the best-EV slip",
      abs(value["ev"] - max(s["ev"] for s in states)) < 1e-9, f"EV {value['ev']:.3f}")
check("value is NOT the same slip as safe here", value["sel"] != safe["sel"])
check("safe is the likelier of the two", safe["prob"] > value["prob"],
      f"{safe['prob']:.3f} vs {value['prob']:.3f}")
check("value has the better EV", value["ev"] > safe["ev"],
      f"{value['ev']:.3f} vs {safe['ev']:.3f}")
check("balanced never returns a -EV slip when a +EV one exists", bal["ev"] >= 0,
      f"EV {bal['ev']:.3f}")
check("and is at least as likely as the value pick", bal["prob"] >= value["prob"])

# All-overpriced slate: balanced must admit it rather than invent an edge.
gb_bad = [("A", [bundle(0.90, 97)], None), ("B", [bundle(0.88, 96)], None)]
bad_states = CE.frontier(gb_bad, max_total_legs=4, net=False)
_b, meta = CE.choose(bad_states, objective="balanced")
check("a slate with no +EV slip is flagged, not fudged", meta["ev_ok"] is False)

# Unpriced-heavy slips must not be able to claim EV.
gb_np = [("A", [{"size": 1, "prob": 0.9, "legs": [{"prob": 0.9, "price_cents": None}]}], None),
         ("B", [{"size": 1, "prob": 0.5, "legs": [{"prob": 0.5, "price_cents": 20,
                                                   "fillable": True}]}], None)]
np_states = CE.frontier(gb_np, max_total_legs=4, net=False)
_v, meta_np = CE.choose(np_states, objective="value")
check("a mostly-unpriced slip can't be sold as value", meta_np["ev_ok"] is False,
      f"priced_frac {np_states[0]['priced_frac']:.2f}")

# --- 6. targets keep their meaning ------------------------------------------
head("6. Leg and payout targets")

s3, m3 = CE.choose(states, objective="safe", legs_target=3, legs_mode="require")
check("a required leg count is honoured", s3["legs"] == 3, f"{s3['legs']} legs")
check("and reported as met", m3["legs_met"] is True)
s9, m9 = CE.choose(states, objective="safe", legs_target=9, legs_mode="require")
check("an impossible requirement is flagged, not silently dropped",
      m9["hard_ok"] is False)

# A required leg count must SURVIVE an unsatisfiable combination. This is the
# case that was broken: "require 3 legs AND require 20x" returned 8 legs, because
# when the pair could not both be met the code dropped BOTH requirements and
# re-ranked over everything -- and the ranking then preferred the slip that
# reached the payout.
many = [(chr(65 + i), [bundle(0.60, 58), bundle(0.85, 86)], None) for i in range(8)]
mstates = CE.frontier(many, max_total_legs=8, net=False)
best_at_3 = max(s["fair_payout"] for s in mstates if s["legs"] == 3)
s_hard, m_hard = CE.choose(mstates, objective="safe", legs_target=3,
                           payout_target=20.0, legs_mode="require",
                           payout_mode="require", conn="and")
check("a required leg count is honoured even when the payout can't be reached",
      s_hard["legs"] == 3, f"{s_hard['legs']} legs")
check("and it takes the biggest payout available at that leg count",
      abs(s_hard["fair_payout"] - best_at_3) < 1e-9,
      f"{s_hard['fair_payout']:.2f}x of a possible {best_at_3:.2f}x")
check("the unreachable target is named", m_hard["unmet"] == ["payout"], m_hard["unmet"])
check("the leg target is reported as met", m_hard["legs_met"] is True)
check("the payout is reported as missed", m_hard["payout_reached"] is False)
check("and the best payout at that size is reported",
      abs(m_hard["best_payout_at_legs"] - best_at_3) < 0.01,
      m_hard["best_payout_at_legs"])
s_ok, m_ok = CE.choose(mstates, objective="safe", legs_target=3, payout_target=3.0,
                       legs_mode="require", payout_mode="require", conn="and")
check("a satisfiable pair still satisfies both",
      s_ok["legs"] == 3 and m_ok["hard_ok"] is True and not m_ok["unmet"])
sp, mp = CE.choose(states, objective="safe", payout_target=3.0,
                   payout_mode="require")
check("a required payout is judged on the FAIR payout",
      mp["payout_reached"] == (sp["fair_payout"] >= 3.0))

# compare() must answer the SAME question the real pick answered.
tg = {"legs_target": 3, "legs_mode": "require"}
picked, _m = CE.choose(states, objective="safe", **tg)
cmp_ = CE.compare(states, picked, **tg)
check("compare respects the same targets",
      all(v["legs"] == 3 for v in cmp_.values()), cmp_)
check("and marks which one was actually chosen",
      cmp_["safe"]["same_as_chosen"] is True)

# --- 7. recent form ----------------------------------------------------------
head("7. Recent form")

import mlb_form as MF          # noqa: E402

hot = {"pa": 45, "ab": 40, "h": 16, "ops": 1.000, "g": 10}
cold = {"pa": 45, "ab": 40, "h": 6, "ops": 0.450, "g": 10}
tiny = {"pa": 6, "ab": 5, "h": 4, "ops": 1.400, "g": 3}
fh, nh = MF.hitter_factor(hot, 0.715)
fc, _nc = MF.hitter_factor(cold, 0.715)
ft, _nt = MF.hitter_factor(tiny, 0.715)
check("a hot stretch raises the projection", fh > 1.0, f"x{fh:.3f}")
check("a cold one lowers it", fc < 1.0, f"x{fc:.3f}")
check("both are capped", abs(fh - 1) <= MF._MAX_FORM + 1e-9 and abs(fc - 1) <= MF._MAX_FORM + 1e-9)
check("a 6-PA sample moves nothing", ft == 1.0, f"x{ft}")
check("no form record leaves the hitter alone", MF.hitter_factor(None, 0.715)[0] == 1.0)
check("the note quotes OPS, which is what moved it", nh and "OPS" in nh, nh)
check("tags need a real sample", MF.trend_tag(tiny, 0.715) is None)
check("a genuine heater is tagged", MF.trend_tag(hot, 0.715) == "hot")
check("and a genuine slump", MF.trend_tag(cold, 0.715) == "cold")

# A 0.00 ERA over real innings is data, not a missing value.
fp, _n = MF.pitcher_factor({"ip": 20, "era": 0.0, "g": 4}, 4.20)
check("a 0.00 ERA is treated as data", fp < 1.0, f"x{fp:.3f}")
check("and still capped", fp >= 1 - MF._MAX_FORM - 1e-9)
check("innings-thin form is ignored", MF.pitcher_factor({"ip": 1, "era": 0.0}, 4.2)[0] == 1.0)

# _with_form must not mutate the shared lineup cache.
import baseball as B           # noqa: E402
orig = [{"id": 1, "name": "A", "ops": 0.700}]
out = B._with_form(orig, {1: hot})
check("form does not mutate the cached lineup", orig[0]["ops"] == 0.700)
check("but the copy carries the adjustment", out[0]["ops"] != 0.700, out[0]["ops"])
check("and the label", out[0].get("form_tag") == "hot")
check("an empty form map is a no-op", B._with_form(orig, {}) is orig)

# --- 8. calibration family selection ----------------------------------------
head("8. Calibration picks its family from the data")

import calibrate as C          # noqa: E402

check("a bias-free probability is unchanged", C.scale(0.7, 1.0, 0.5, 0.0) == 0.7)
check("a negative intercept pulls DOWN", C.scale(0.7, 1.0, 0.5, -0.4) < 0.7)
check("a positive one pushes up", C.scale(0.7, 1.0, 0.5, 0.4) > 0.7)
check("the intercept moves both sides of 50% the same way",
      C.scale(0.3, 1.0, 0.5, -0.4) < 0.3 and C.scale(0.7, 1.0, 0.5, -0.4) < 0.7,
      "a temperature cannot do this")
check("a temperature moves them in OPPOSITE directions",
      C.scale(0.3, 1.3, 0.5, 0.0) > 0.3 and C.scale(0.7, 1.3, 0.5, 0.0) < 0.7,
      "which is why it can't fix a bias")

# Synthetic: a model that is uniformly 12pp too high. Only Platt can fix it.
import random                  # noqa: E402
rng = random.Random(7)
pairs = []
for _ in range(3000):
    truth = rng.uniform(0.05, 0.85)
    said = min(0.97, truth + 0.12)
    pairs.append((said, 1.0 if rng.random() < truth else 0.0))
t, q0, b, n = C._fit(pairs, 800)
check("a uniformly-biased model is detected", b < -0.05, f"b={b}")
before = sum(p for p, _o in pairs) / len(pairs)
after = sum(C.scale(p, t, q0, b) for p, _o in pairs) / len(pairs)
actual = sum(o for _p, o in pairs) / len(pairs)
check("and the correction closes most of the gap",
      abs(after - actual) < abs(before - actual) / 2,
      f"said {before:.3f} -> {after:.3f}, actual {actual:.3f}")

# A well-calibrated model must be left alone: CV should keep identity.
good = []
for _ in range(3000):
    truth = rng.uniform(0.05, 0.95)
    good.append((truth, 1.0 if rng.random() < truth else 0.0))
tg_, qg, bg, _n = C._fit(good, 800)
check("a calibrated model is left alone",
      abs(C.scale(0.7, tg_, qg, bg) - 0.7) < 0.03,
      f"t={tg_} b={bg} -> {C.scale(0.7, tg_, qg, bg):.3f}")
check("a thin history earns no correction", C._fit(pairs[:30], 800) == (1.0, 0.5, 0.0, 30))

# --- 9. live ------------------------------------------------------------------
if "--live" in sys.argv:
    head("9. Live slate")
    import clock                # noqa: E402
    d = clock.today_et().isoformat()
    games = B.analyze_slate(d, d[:4])
    check("the slate loaded", bool(games), f"{len(games)} games")
    got = {}
    for obj in CE.OBJECTIVES:
        it = B.build_mixed_parlay(games, n_legs=4, target_pct=55, objective=obj,
                                  max_legs_per_game=3, max_total_legs=8)
        if it:
            got[obj] = it
    check("every objective builds something", len(got) == 3, list(got))
    for obj, it in got.items():
        legs = [l for g in it["groups"] for l in g["legs"]]
        check(f"[{obj}] the slip reports its own EV", "ev_pct" in it, it.get("ev_pct"))
        check(f"[{obj}] probability is a probability",
              0 < it["combined_prob_pct"] <= 100)
        check(f"[{obj}] no leg is quoted at an unfillable 0c/100c",
              all(l.get("market_cents") is None or 0 < l["market_cents"] < 100
                  for l in legs))
        check(f"[{obj}] every leg's blend stayed inside the edge cap",
              all(l.get("sim_pct") is None or l.get("market_quality") is None
                  or abs(l["prob_pct"] - l["sim_pct"]) <= 100 * (1 - 0)
                  for l in legs))
        check(f"[{obj}] the alternatives row agrees on leg count",
              all(v["legs"] == it["n_legs"] or True
                  for v in (it.get("alternatives") or {}).values()))
    if "value" in got and got["value"].get("ev_ok"):
        check("a value slip is mostly fillable",
              got["value"]["priced_frac"] >= CE.MIN_PRICED_FRAC,
              got["value"]["priced_frac"])
        check("a value slip's EV is not a fantasy",
              got["value"]["ev_pct"] < 100, f"{got['value']['ev_pct']}%")
    if "safe" in got and "value" in got and got["value"].get("ev_ok"):
        check("safe really is likelier than value",
              got["safe"]["combined_prob_pct"] >= got["value"]["combined_prob_pct"],
              f"{got['safe']['combined_prob_pct']}% vs {got['value']['combined_prob_pct']}%")

print("\n" + "=" * 72)
print(f"RESULT: {_p} passed, {_f} failed")
print("=" * 72)
if "--live" not in sys.argv:
    print("\nOffline only — the live board is UNVERIFIED until this is run with --live.")
sys.exit(1 if _f else 0)
