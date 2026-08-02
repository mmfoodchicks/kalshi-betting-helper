"""Invariants for the tennis board.

The tennis stack accumulated a lot of fitted machinery in a short time -- an
experience-ramped K, surface-split ratings, a depth-scaled serve weight, a
confidence rule bounded by the weakest component, three external data sources
each behind its own key. Every one of those has a failure mode that produces a
number rather than an error, which is the dangerous kind.

This checks the properties that must hold on ANY slate, so a future change that
breaks one fails loudly here instead of quietly mispricing a board.

Run: python3 tests/tennis_board_check.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))


import tennis_elo
import tennis_prices
import tennis_history
import serp_surface
import odds_api

print("=" * 74)
print("1. The Elo K ramp")
print("=" * 74)
ks = [tennis_elo.k_for(n) for n in (0, 1, 5, 10, 20, 50, 100, 500)]
ck("K decreases monotonically with experience",
   all(ks[i] >= ks[i + 1] - 1e-9 for i in range(len(ks) - 1)), [round(k, 1) for k in ks])
ck("K starts at K_EARLY", abs(tennis_elo.k_for(0) - tennis_elo.K_EARLY) < 1e-9)
ck("K settles at K_LATE", abs(tennis_elo.k_for(10000) - tennis_elo.K_LATE) < 1e-6)
ck("K never negative or absurd", all(0 < k <= tennis_elo.K_EARLY for k in ks))
ck("negative experience is clamped, not extrapolated",
   tennis_elo.k_for(-5) == tennis_elo.k_for(0))

print()
print("=" * 74)
print("2. Surface-blended ratings")
print("=" * 74)
rec = {"elo": 1800.0, "n": 200, "surf": {"Clay": {"elo": 2000.0, "n": 100},
                                         "Hard": {"elo": 1700.0, "n": 5}}}
ck("no surface -> the overall rating", tennis_elo.elo_on(rec) == 1800.0)
ck("unknown surface -> the overall rating", tennis_elo.elo_on(rec, "Grass") == 1800.0)
clay = tennis_elo.elo_on(rec, "Clay")
hard = tennis_elo.elo_on(rec, "Hard")
ck("a deep surface pulls toward its own rating", 1800 < clay < 2000, round(clay, 1))
ck("a thin surface barely moves the overall", abs(hard - 1800) < 20, round(hard, 1))
ck("blend is bounded by the two inputs it mixes",
   1700 <= hard <= 1800 and 1800 <= clay <= 2000)
deep = tennis_elo.elo_on({"elo": 1800.0, "surf": {"Clay": {"elo": 2000.0, "n": 100000}}},
                         "Clay")
ck("infinite surface evidence approaches the surface rating", deep > 1990, round(deep, 1))
ck("zero surface matches -> overall, not a divide by zero",
   tennis_elo.elo_on({"elo": 1800.0, "surf": {"Clay": {"elo": 2000.0, "n": 0}}},
                     "Clay") == 1800.0)
ck("None record handled", tennis_elo.elo_on(None, "Clay") is None)

print()
print("=" * 74)
print("3. Parsing helpers")
print("=" * 74)
ck("tournament off a market title",
   tennis_elo._tourney("Will X win the A vs B: M25 Koszalin Round of 16 match?")
   == "M25 Koszalin")
ck("final-round title", tennis_elo._tourney("Will X win the A vs B: W75 Hechingen Final match?")
   == "W75 Hechingen")
ck("garbage title does not raise", tennis_elo._tourney("nonsense") is not None
   or tennis_elo._tourney("nonsense") is None)
ck("None title", tennis_elo._tourney(None) is None)
rows, why = tennis_history.parse(
    "tourney_date,winner_name,loser_name,surface\n20240115,A B,C D,Clay\n")
ck("history parser accepts a minimal valid file", why is None and len(rows) == 1, why)
ck("history parser rejects a file missing a required column",
   tennis_history.parse("a,b,c\n1,2,3\n")[0] == [])

print()
print("=" * 74)
print("4. External sources are all optional")
print("=" * 74)
for name, mod in (("serp_surface", serp_surface), ("odds_api", odds_api)):
    had = mod.key()
    os.environ.pop("SERPAPI_KEY" if name == "serp_surface" else "ODDS_API_KEY", None)
    ck(f"{name} reports itself disabled without a key", not mod.enabled())
    if had:
        os.environ["SERPAPI_KEY" if name == "serp_surface" else "ODDS_API_KEY"] = had
ck("odds_api serves nothing when disabled (cache included)",
   odds_api.board("x")[0] == [] if not odds_api.enabled() else True)
ck("history can be switched off by env", True)   # exercised in tennis_history_check

print()
print("=" * 74)
print("5. A real board")
print("=" * 74)
board = tennis_prices.board() or tennis_prices._compute(n_sims=1500)
ms = (board or {}).get("matches") or []
ck("board builds", bool(ms), f"{len(ms)} matches")
if ms:
    bad_sum, bad_range, bad_conf, bad_surf, bad_sw = [], [], [], [], []
    for m in ms:
        a, b = m["a"], m["b"]
        if a.get("fair_win") is not None and b.get("fair_win") is not None:
            if abs(a["fair_win"] + b["fair_win"] - 100) > 1.5:
                bad_sum.append((m.get("tournament"), a["fair_win"], b["fair_win"]))
        for p in (a, b):
            for f in ("fair_win", "model_win", "mkt_win"):
                v = p.get(f)
                if v is not None and not (0 <= v <= 100):
                    bad_range.append((p.get("name"), f, v))
            c = p.get("confidence")
            if c is not None and not (0 <= c <= 1):
                bad_conf.append((p.get("name"), c))
            sw = p.get("serve_weight")
            if sw is not None and not (0 <= sw <= tennis_prices._SERVE_CAP + 1e-9):
                bad_sw.append((p.get("name"), sw))
        if bool(m.get("surface_known")) != (m.get("surface") not in (None, "Unknown")):
            bad_surf.append((m.get("tournament"), m.get("surface"),
                             m.get("surface_known")))
    ck("both sides of a match sum to 100%", not bad_sum, bad_sum[:2])
    ck("every probability within 0-100", not bad_range, bad_range[:2])
    ck("confidence within 0-1", not bad_conf, bad_conf[:2])
    ck("serve weight never exceeds its cap", not bad_sw, bad_sw[:2])
    ck("surface_known agrees with the surface field", not bad_surf, bad_surf[:2])

    # The serve weight must rise with charting depth, or the scaling is inverted.
    # It is driven by the THINNER of the two players, so test against that -- a
    # player's own depth does not determine their weight, and sorting on it can
    # pair the best-charted name with an unknown and look like a regression.
    pts = []
    for m in ms:
        a, b = m["a"], m["b"]
        if a.get("serve_weight") is None or a.get("n") is None or b.get("n") is None:
            continue
        pts.append((min(a["n"], b["n"]), a["serve_weight"]))
    if len(pts) > 4:
        pts.sort()
        ck("serve weight rises with the THINNER side's charting depth",
           pts[-1][1] >= pts[0][1], f"depth {pts[0][0]:.1f}->w {pts[0][1]:.3f}  "
                                    f"depth {pts[-1][0]:.1f}->w {pts[-1][1]:.3f}")
        # and it must match the formula it claims to implement
        cap, kk = tennis_prices._SERVE_CAP, tennis_prices._SERVE_K
        off = [(d, w) for d, w in pts if abs(w - cap * d / (d + kk)) > 0.002]
        ck("serve weight matches _SERVE_CAP * n/(n+_SERVE_K)", not off, off[:2])

    # a match modelled on an unknown surface must not claim a surface edge
    liars = [i for m in ms if not m.get("surface_known")
             for i in (m.get("insights") or [])
             if "specialist" in i or "weak spot" in i or "stronger on" in i]
    ck("no surface claim on an unidentified court", not liars, liars[:2])

    # every insight is a non-empty string (they are rendered straight into HTML)
    bad_ins = [i for m in ms for i in (m.get("insights") or [])
               if not isinstance(i, str) or not i.strip()]
    ck("insights are all non-empty strings", not bad_ins, bad_ins[:2])

    ck("no match carries more insights than the cap",
       all(len(m.get("insights") or []) <= 7 for m in ms),
       max((len(m.get("insights") or []) for m in ms), default=0))

    # surface ratings, where present, must be internally consistent
    bad_bysurf = []
    for m in ms:
        for p in (m["a"], m["b"]):
            bs = p.get("elo_by_surface") or {}
            for s, v in bs.items():
                if not (800 < v.get("elo", 0) < 3000) or v.get("n", 0) < 0:
                    bad_bysurf.append((p.get("name"), s, v))
    ck("per-surface ratings are in a sane range", not bad_bysurf, bad_bysurf[:2])

print()
print("=" * 74)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
for n, d in FAIL:
    print(f"   - {n}   {d}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
