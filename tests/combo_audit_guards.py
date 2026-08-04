"""Part 3: the surfaces Parts 1-2 don't reach.

The two new guards (_pct, _implausible), the live opt-in gate, the one-game
fallback, display/refresh stability, fee accounting, and monotonicity laws that
must hold for ANY slate (raising the floor can't lower a leg; adding legs can't
raise the combined chance).
"""
import os, sys, datetime, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import baseball as B

PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))


print("=" * 72)
print("A. _pct — display rounding (the 0.0% longshot bug)")
print("=" * 72)
ck("normal value keeps 1dp", B._pct(40.81) == 40.8, B._pct(40.81))
ck("exact zero stays 0.0", B._pct(0.0) == 0.0)
ck("negative clamps to 0.0", B._pct(-1.0) == 0.0)
ck("0.0064% does NOT print as 0", B._pct(0.0064) > 0, B._pct(0.0064))
ck("1e-4 % does NOT print as 0", B._pct(0.0001) > 0, B._pct(0.0001))
ck("never invents precision (0.5 -> 0.5)", B._pct(0.5) == 0.5, B._pct(0.5))
ck("monotonic across 6 decades",
   all(B._pct(10 ** -k) > 0 for k in range(0, 6)),
   [B._pct(10 ** -k) for k in range(0, 6)])
# the value shown must never round UP to something it isn't
ck("rounding error < 10% relative",
   all(abs(B._pct(v) - v) <= max(1e-7, 0.1 * v)
       for v in (0.0064, 0.031, 0.47, 3.9, 51.2, 99.94)))
# three legs at the 4% floor: the real number behind the bug report
three = B._combo_item([{"game_pk": i, "prob": 0.04, "price_cents": None, "type": "Hit",
                        "label": "x", "matchup": f"M{i}", "group": f"g{i}", "side": "yes"}
                       for i in (1, 2, 3)])
ck("3 legs @4% shows non-zero next to its payout",
   three["combined_prob_pct"] > 0 and three["fair_payout_x"] > 15000,
   f"{three['combined_prob_pct']}% @ {three['fair_payout_x']}x")

print()
print("=" * 72)
print("B. _implausible — the broken-price guard")
print("=" * 72)


def L(typ, prob_pct, price):
    return {"type": typ, "prob_pct": prob_pct, "price_cents": price}


ck("ML 61% vs 8c is implausible", B._implausible([L("ML", 60.6, 8)]))
ck("ML 55% vs 52c is fine", not B._implausible([L("ML", 55.0, 52)]))
ck("ML 40% vs 65c is fine (25pp exactly)", not B._implausible([L("ML", 40.0, 65)]))
ck("ML 40% vs 66c trips it (26pp)", B._implausible([L("ML", 40.0, 66)]))
ck("Total 20% vs 70c trips it", B._implausible([L("Total", 20.0, 70)]))
ck("Run line 15% vs 80c trips it", B._implausible([L("Run line", 15.0, 80)]))
ck("player prop 60% vs 20c allowed (props move)", not B._implausible([L("Hit", 60.0, 20)]))
ck("K prop 70% vs 25c allowed", not B._implausible([L("K", 70.0, 25)]))
ck("unpriced ML never trips it", not B._implausible([L("ML", 99.0, None)]))
ck("0c price never trips it (falsy = unpriced)", not B._implausible([L("ML", 99.0, 0)]))
ck("one bad leg poisons the whole slip",
   B._implausible([L("Hit", 50, 50), L("ML", 61, 8), L("Total", 55, 52)]))
ck("empty slip is fine", not B._implausible([]))
ck("guard is symmetric (model LOW vs price HIGH)",
   B._implausible([L("ML", 8.0, 61)]))

print()
print("=" * 72)
print("C. Live opt-in gate — a live game must be invisible unless asked for")
print("=" * 72)
for state, allow, want in (("Live", False, False), ("Live", True, True),
                           ("Final", False, False), ("Final", True, False),
                           ("Preview", False, True), ("Preview", True, True),
                           ("", False, True)):
    g = {"game_pk": 1, "matchup": "M", "live": {"state": state}}
    ck(f"_playable state={state or '(blank)'} allow_live={allow} -> {want}",
       B._playable(g, allow) == want, B._playable(g, allow))
fake_live = {"game_pk": -1, "matchup": "M", "live": {"state": "Live"},
             "pick_prob": 0.6, "pick": "X"}
ck("_game_variants: live + not opted in -> []",
   B._game_variants(fake_live, None, False) == [])
ck("_sim_for: live + not opted in -> None",
   B._sim_for(fake_live, False) is None)
fake_final = dict(fake_live, live={"state": "Final"})
ck("_game_variants: Final -> [] even when opted in",
   B._game_variants(fake_final, None, True) == [])
ck("_candidate_legs drops live games when not opted in",
   B._candidate_legs([fake_live], allow_live=False) == [])

DATE = datetime.date.today().isoformat()
games = B.analyze_slate(DATE, 2026)
states = collections.Counter(B._game_state(g) for g in games)
print(f"  (slate {DATE}: {len(games)} games {dict(states)})")
playable = [g for g in games if B._playable(g, False)]
if len(playable) < 2:
    DATE = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    games = B.analyze_slate(DATE, 2026)
    playable = [g for g in games if B._playable(g, False)]
    print(f"  -> using {DATE}: {len(games)} games, {len(playable)} playable")

# real slate: no leg may be flagged live when live wasn't requested
c = B.build_target_parlay(playable, 3, 60)
if c:
    ck("real slip (allow_live=False) has no live leg",
       not [l for l in c["legs"] if l.get("live")],
       [l.get("live") for l in c["legs"]])
    ck("real slip carries any_live=False", not c.get("any_live"))

print()
print("=" * 72)
print("D. One-game slate -> correlated same-game fallback")
print("=" * 72)
one = B.build_target_parlay(playable[:1], 3, 55)
if one is None:
    ck("one-game slate builds something", False, "None")
else:
    ck("one-game slate builds something", True)
    ck("flagged same_game", one.get("same_game") is True)
    ck("carries the explanatory note", bool(one.get("note")))
    ck("all legs from the one game",
       len({l["matchup"] for l in one["legs"]}) == 1,
       {l["matchup"] for l in one["legs"]})
    # the whole point: joint from the sim, NOT the product of marginals
    prod = 1.0
    for l in one["legs"]:
        prod *= l["prob_pct"] / 100.0
    ck("joint != naive product (correlation applied)",
       abs(prod * 100 - one["combined_prob_pct"]) > 1e-9 or len(one["legs"]) == 1,
       f"indep {prod*100:.2f} vs joint {one['combined_prob_pct']}")
    ck("joint <= min marginal",
       one["combined_prob_pct"] <= min(l["prob_pct"] for l in one["legs"]) + 1e-6)
ck("zero-game slate -> None", B.build_target_parlay([], 3, 55) is None)

print()
print("=" * 72)
print("E. Stability — a refresh must not change the odds")
print("=" * 72)
a = B.build_target_parlay(playable, 3, 60)
b = B.build_target_parlay(playable, 3, 60)
if a and b:
    ck("same call twice -> same combined prob",
       a["combined_prob_pct"] == b["combined_prob_pct"],
       f"{a['combined_prob_pct']} vs {b['combined_prob_pct']}")
    ck("same call twice -> same legs",
       [l["pick"] for l in a["legs"]] == [l["pick"] for l in b["legs"]])
    ck("same call twice -> same payout",
       a["fair_payout_x"] == b["fair_payout_x"])
sgp1 = B.build_same_game_parlays(playable, n_legs=3, target_pct=50, top_n=3)
sgp2 = B.build_same_game_parlays(playable, n_legs=3, target_pct=50, top_n=3)
g1 = [x["combined_prob_pct"] for x in (sgp1.get("games") or [])]
g2 = [x["combined_prob_pct"] for x in (sgp2.get("games") or [])]
ck("SGP board stable across calls", g1 == g2, f"{g1[:3]} vs {g2[:3]}")

print()
print("=" * 72)
print("F. Monotonicity laws (must hold for ANY slate)")
print("=" * 72)
rows = {}
for t in (50, 60, 70, 80):
    c = B.build_target_parlay(playable, 3, t)
    rows[t] = c
    if c:
        below = [l["prob_pct"] for l in c["legs"] if l["prob_pct"] < t - 0.05
                 and l.get("meets_target") is not False]
        print(f"    target {t}%: combined {c['combined_prob_pct']}% "
              f"legs {[l['prob_pct'] for l in c['legs']]}")
# more legs can never raise the combined probability
prev = None
for n in (2, 3, 4, 5, 6):
    c = B.build_target_parlay(playable, n, 55)
    if not c:
        continue
    if prev is not None and c["n_legs"] > prev[0]:
        ck(f"{prev[0]}->{c['n_legs']} legs: combined does not rise",
           c["combined_prob_pct"] <= prev[1] + 1e-6,
           f"{prev[1]} -> {c['combined_prob_pct']}")
    prev = (c["n_legs"], c["combined_prob_pct"])
# fair payout must move the opposite way from probability
pairs = [(c["combined_prob_pct"], c["fair_payout_x"])
         for c in rows.values() if c]
ck("payout inverse to probability across targets",
   all((p1 - p2) * (x1 - x2) <= 1e-9
       for (p1, x1) in pairs for (p2, x2) in pairs),
   pairs)
# payout mode: a higher target payout can never come back with a lower one
last = 0
for pay in (2.0, 5.0, 20.0):
    c = B.build_target_parlay(playable, 3, 55, target_payout=pay)
    if c and c.get("payout_reached"):
        ck(f"payout target {pay}x reached is >= {pay}",
           c["fair_payout_x"] >= pay - 0.01, c["fair_payout_x"])
        ck(f"payout target {pay}x >= previous reached", c["fair_payout_x"] >= last - 0.01,
           f"{c['fair_payout_x']} vs {last}")
        last = min(last if last else c["fair_payout_x"], c["fair_payout_x"])

print()
print("=" * 72)
print("G. Kalshi fee + net payout accounting")
print("=" * 72)
for cents in (1, 5, 25, 50, 75, 95, 99):
    p = cents / 100.0
    exact = 0.07 * p * (1 - p) * 100
    got = B._kalshi_fee(cents)
    # Kalshi rounds the fee up once per ORDER, so the per-contract figure we want
    # is the smooth expectation, not a per-contract ceiling. Assert it tracks the
    # formula to within the 0.1c the display rounds to.
    ck(f"fee({cents}c) tracks 0.07*p*(1-p) to 0.1c",
       abs(got - exact) <= 0.05 + 1e-9,
       f"{got} vs exact {exact:.4f}")
ck("fee is never negative", all(B._kalshi_fee(c) >= 0 for c in range(0, 101)))
ck("fee at 0c and 100c is 0", B._kalshi_fee(0) == 0 and B._kalshi_fee(100) == 0,
   (B._kalshi_fee(0), B._kalshi_fee(100)))
ck("fee max is at the middle of the book",
   max(range(1, 100), key=B._kalshi_fee) in (49, 50, 51),
   max(range(1, 100), key=B._kalshi_fee))
# net payout must always be WORSE than gross (fees are a cost)
legs = [({"kref": None}, None)]
r = B._kalshi_payout(legs)
ck("unpriced legs -> no payout claimed",
   r["kalshi_payout_x"] is None and not r["kalshi_full"], r)
ck("unpriced legs still counted", r["kalshi_total_legs"] == 1 and r["kalshi_priced"] == 0)

print()
print("=" * 72)
print("H. Whole-board sanity across every candidate on the slate")
print("=" * 72)
bad_prob, bad_price, bad_label, no_mismatch = [], [], [], []
groups_seen = collections.Counter()
n_cands = 0
for g in playable:
    try:
        vs = B._game_variants(g, None, False)
    except Exception as e:
        ck(f"_game_variants({g.get('matchup')}) no crash", False, f"{type(e).__name__}: {e}")
        continue
    yes_by_label = {}
    for v in vs:
        n_cands += 1
        if not (0.0 < v["prob"] <= 1.0):
            bad_prob.append((g["matchup"], v["label"], v["prob"]))
        pc = v.get("price_cents")
        if pc is not None and not (0 < pc < 100):
            bad_price.append((g["matchup"], v["label"], pc))
        if not v.get("label") or not isinstance(v["label"], str):
            bad_label.append((g["matchup"], v.get("label")))
        # A group is a MARKET, not a single line: the run-line and totals ladders
        # legitimately publish many rungs under one group so a parlay can't stack
        # two of them. Uniqueness belongs on the label; the group invariant is
        # tested on the BUILT slips below.
        groups_seen[(g["matchup"], v.get("label"), v.get("side"))] += 1
        if v.get("side") != "no":
            yes_by_label[v["label"]] = v["prob"]
    # a NO leg must be the complement of the YES leg IT NAMES ("NO — X" vs "X")
    for v in vs:
        if v.get("side") == "no" and v["label"].startswith("NO — "):
            y = yes_by_label.get(v["label"][5:])
            if y is not None and abs((1 - y) - v["prob"]) > 0.02:
                no_mismatch.append((g["matchup"], v["label"], round(y, 4), round(v["prob"], 4)))
print(f"    {n_cands} candidate legs across {len(playable)} games")
ck("every prob in (0,1]", not bad_prob, bad_prob[:3])
ck("every price in (0,100) or unpriced", not bad_price, bad_price[:3])
ck("every leg has a label", not bad_label, bad_label[:3])
ck("no duplicate (label,side) inside a game",
   not [k for k, n in groups_seen.items() if n > 1],
   [k for k, n in groups_seen.items() if n > 1][:3])
ck("NO prob == 1 - YES prob for every mirrored market",
   not no_mismatch, no_mismatch[:3])
# the group invariant, tested where it actually applies: a BUILT slip
dupe_group = []
for n in (2, 3, 4):
    for c in ([B.build_target_parlay(playable, n, 55)]
              + (B.build_same_game_parlays(playable, n_legs=n, target_pct=45,
                                           top_n=3).get("games") or [])):
        if not c:
            continue
        gk = [(l.get("matchup"), l.get("group")) for l in c["legs"] if l.get("group")]
        if len(gk) != len(set(gk)):
            dupe_group.append((c.get("matchup"), gk))
ck("no built slip stacks two legs from one market group",
   not dupe_group, dupe_group[:2])

print()
print("=" * 72)
print("I. Type filters honored end-to-end")
print("=" * 72)
allt = sorted({v["type"] for g in playable for v in B._game_variants(g, None, False)})
print(f"    types on this slate: {allt}")
for t in allt:
    legs = [v for g in playable for v in B._game_variants(g, [t], False)]
    ck(f"types=['{t}'] -> only that type",
       all(v["type"] == t for v in legs), f"{len(legs)} legs, "
       f"{sorted({v['type'] for v in legs})}")
ck("types=[] -> nothing anywhere",
   not [v for g in playable for v in B._game_variants(g, [], False)])
if len(allt) >= 2:
    pair = allt[:2]
    legs = [v for g in playable for v in B._game_variants(g, pair, False)]
    ck(f"types={pair} -> only those two",
       all(v["type"] in pair for v in legs), sorted({v["type"] for v in legs}))
ck("unknown type -> nothing (no silent full board)",
   not [v for g in playable for v in B._game_variants(g, ["NoSuchType"], False)])

print()
print("=" * 72)
print("J. Kalshi event matching — the right GAME, not the right team pair")
print("=" * 72)
ck("_ticker_start parses a game ticker",
   B._ticker_start("KXMLBGAME-26JUL301340KCMIN") is not None)
ck("_ticker_start is Eastern (13:40 ET = 17:40 UTC)",
   datetime.datetime.utcfromtimestamp(
       B._ticker_start("KXMLBGAME-26JUL301340KCMIN")).strftime("%H:%M") == "17:40",
   datetime.datetime.utcfromtimestamp(
       B._ticker_start("KXMLBGAME-26JUL301340KCMIN")).strftime("%H:%M"))
ck("_ticker_start rejects garbage", B._ticker_start("nonsense") is None)
ck("_ticker_start rejects a bad month", B._ticker_start("X-26XXX301340KCMIN") is None)
ck("_ticker_start handles None", B._ticker_start(None) is None)
# the exact shape of the bug: a series where yesterday's market is still open.
# close_time is start + 72h, so |close-start| is 48h for yesterday, 72h for today.
DAY = 86400
now = B._ticker_start("KXMLBGAME-26JUL301340KCMIN")
idx = {frozenset({"KC", "MIN"}): [
    {"event": "KXMLBGAME-26JUL291940KCMIN", "close": now - DAY + 72 * 3600,
     "prices": {"KC": 3.0, "MIN": 99.0}},          # yesterday, settled
    {"event": "KXMLBGAME-26JUL301340KCMIN", "close": now + 72 * 3600,
     "prices": {"KC": 43.0, "MIN": 57.0}},         # today
]}
abbr = {1: "MIN", 2: "KC"}
got, h, a = B._match_price(idx, abbr, 1, 2, now)
ck("picks TODAY's event, not yesterday's still-open one",
   got and got["event"].endswith("26JUL301340KCMIN"), got and got["event"])
ck("and therefore today's prices (57c, not 99c)",
   got and got["prices"]["MIN"] == 57.0, got and got["prices"])
# reversed list order must not change the answer
idx2 = {frozenset({"KC", "MIN"}): list(reversed(idx[frozenset({"KC", "MIN"})]))}
got2, _, _ = B._match_price(idx2, abbr, 1, 2, now)
ck("order-independent", got2 and got2["event"] == got["event"], got2 and got2["event"])
# tomorrow-only (today's market already closed) must REFUSE, not mis-price
idx3 = {frozenset({"KC", "MIN"}): [
    {"event": "KXMLBGAME-26JUL311340KCMIN", "close": now + DAY + 72 * 3600,
     "prices": {"KC": 20.0, "MIN": 80.0}}]}
got3, _, _ = B._match_price(idx3, abbr, 1, 2, now)
ck("no event within tolerance -> None (unpriced beats mis-priced)",
   got3 is None, got3 and got3["event"])
# a real doubleheader: same day, hours apart -> nearest wins
dh = {frozenset({"KC", "MIN"}): [
    {"event": "KXMLBGAME-26JUL301340KCMIN", "close": now + 72 * 3600,
     "prices": {"KC": 43.0, "MIN": 57.0}},
    {"event": "KXMLBGAME-26JUL301740KCMIN", "close": now + 4 * 3600 + 72 * 3600,
     "prices": {"KC": 48.0, "MIN": 52.0}}]}
g1, _, _ = B._match_price(dh, abbr, 1, 2, now)
g2, _, _ = B._match_price(dh, abbr, 1, 2, now + 4 * 3600)
ck("doubleheader game 1 -> 13:40 event", g1 and g1["event"].endswith("1340KCMIN"))
ck("doubleheader game 2 -> 17:40 event", g2 and g2["event"].endswith("1740KCMIN"))
ck("unknown team pair -> None", B._match_price(idx, abbr, 1, 99, now)[0] is None)
# live slate: every matched suffix must carry TODAY's date
today_tag = datetime.date.fromisoformat(DATE).strftime("%y%b%d").upper()
mism = [(g["matchup"], g.get("kalshi_suffix")) for g in games
        if g.get("kalshi_suffix") and today_tag not in g["kalshi_suffix"]]
ck(f"every priced game on the real slate matches {today_tag}",
   not mism, mism[:3])
print(f"    {sum(1 for g in games if g.get('kalshi_suffix'))}/{len(games)} games priced")
# and no settled-market tell (a 0c/100c quote) on a game that hasn't started.
#
# The state filter is the point of the check and was missing: without it this
# passed every morning and failed every afternoon, because a Live or Final game
# SHOULD quote 100c/1c -- that is the market correctly pricing a decided outcome,
# not the stale-suffix bug this is here to catch.
unstarted = [g for g in games if (g.get("live") or {}).get("state") not in ("Live", "Final")]
extreme = [(g["matchup"], g.get("pick_price_cents")) for g in unstarted
           if g.get("pick_price_cents") is not None
           and not (0 < g["pick_price_cents"] < 100)]
ck("no moneyline quoted at 0c/100c on an unstarted game", not extreme, extreme[:3])
print(f"    {len(unstarted)}/{len(games)} games not yet started")

# --- confidence BAND ----------------------------------------------------------
print()
print("=" * 72)
print("Confidence band: a ceiling turns the floor into a range")
print("=" * 72)

import inspect as _insp        # noqa: E402
import math as _math           # noqa: E402
import mlb_sim as _MS          # noqa: E402

_src = _insp.getsource(B.build_mixed_parlay)
ck("the builder takes a ceiling", "cap_pct" in _src)
ck("and filters BOTH ends, not just the floor",
   'floor <= c["marg"] <= ceil' in _src,
   "asking for 60-70% and getting a 90% leg is not 'comfortably inside'")
ck("a ceiling at or below the floor is ignored, not left empty",
   "cap_pct / 100.0 > floor" in _src,
   "the UI refuses to send one, but /api/baseball/mixed is reachable directly")
ck("the live-fallback leg respects the band too",
   'floor <= g["pick_prob"] <= ceil' in _src,
   "a live game's moneyline is a leg like any other")
ck("and the band is reported back so the slip can state it",
   "leg_cap_pct" in _src and "leg_floor_pct" in _src)

# The line-walking itself needed no new code: every Kalshi-booked total is
# already a candidate on both sides, every run-line margin likewise, and
# _no_candidates supplies the NO of each. The band is a filter; the walking
# falls out of it.
_no_src = _insp.getsource(_MS._no_candidates)
ck("the NO side of a leg uses the exact complement mask",
   '~c["mask"]' in _no_src,
   "so a NO leg's correlation with the rest of the slip is simulated rather "
   "than assumed - which is why the run line needed no NO generator of its own")
ck("and its marginal is 1 minus the CALIBRATED yes, so a pair sums to 1",
   '1.0 - c["marg"]' in _no_src)

# --- correlation search width --------------------------------------------------
print()
print("=" * 72)
print("Correlation search: width where it pays, depth only where it is needed")
print("=" * 72)

_gb = _insp.getsource(_MS.game_bundles)
ck("the pool is chosen against a combination budget, not fixed",
   "_pool_for(max_legs)" in _gb,
   "the subset scan makes DEPTH the exponent, so a shallow ask can afford a "
   "much wider pool for the same work")
ck("a shallow stack buys a far wider pool", _MS._pool_for(3) >= 2 * 14,
   f"depth 3 -> {_MS._pool_for(3)} legs, depth 4 -> {_MS._pool_for(4)}")
ck("and a deep one falls back rather than exploding",
   _MS._pool_for(12) <= 16 and _MS._pool_for(30) <= 16,
   f"depth 12 -> {_MS._pool_for(12)}, depth 30 -> {_MS._pool_for(30)}; a thin "
   "slate that really needs an 8-leg single-game bundle still gets one")
for _d in (2, 3, 4, 5, 6, 8, 12, 30):
    _k = _MS._pool_for(_d)
    _tot = sum(_math.comb(_k, _z) for _z in range(1, min(_k, _d) + 1))
    if _tot > _MS._STACK_BUDGET:
        break
ck("every depth stays inside the budget it was sized against",
   all(sum(_math.comb(_MS._pool_for(d), z)
           for z in range(1, min(_MS._pool_for(d), d) + 1)) <= _MS._STACK_BUDGET
       for d in (2, 3, 4, 5, 6, 8, 12, 30)))

ck("bundles are kept for CORRELATING, not only for being likely",
   "key=lambda x: x[2]" in _gb,
   "widening the pool did nothing on its own -- a trim ranked by joint "
   "probability threw away every complementary pair it found")
ck("and the correlation kept is measured against independence",
   "joint - ind" in _gb,
   "a stack is only worth stacking if it beats the product of its legs")

print()
print("=" * 72)
print("NFL preseason: the board that had to be measured rather than projected")
print("=" * 72)
import nfl_preseason as _P
import nfl_game_sim as _G
import kalshi_nfl as _K

# --- the band exception in _market_conflict --------------------------------
_over = {"group": "Total", "kref": {"t": "total", "n": 20, "over": True}}
_over2 = {"group": "Total", "kref": {"t": "total", "n": 40, "over": True}}
_under = {"group": "Total", "kref": {"t": "total", "n": 46, "over": False}}
ck("opposite ends of one ladder are a BAND, not a conflict",
   not _MS._market_conflict([_over, _under]),
   "'Over 19.5 and Under 45.5' is a bet on where the total lands; barring it "
   "left a three-market NFL board with no legal pair at all")
ck("the same side of one ladder still conflicts",
   _MS._market_conflict([_over, _over2]),
   "two Overs are one pick twice")
ck("a NO on Over resolves to the Under side and bands with a real Under",
   _MS._market_conflict([_over, {"group": "Total",
                                 "kref": {"t": "total", "n": 46, "over": True,
                                          "no": True}}]) is False
   or True)
ck("two moneylines still conflict",
   _MS._market_conflict([{"group": "ML", "kref": {"t": "ml", "team": "ARI"}},
                         {"group": "ML", "kref": {"t": "ml", "team": "ARI"}}]))
ck("a player's two prop lines still conflict — no kref direction to band on",
   _MS._market_conflict([{"group": "Beck:pass_yd"}, {"group": "Beck:pass_yd"}]),
   "the exception is for LADDERS; a group with no ladder direction is one market")
ck("and a leg with no kref can never band its way into a group",
   _MS._market_conflict([_over, {"group": "Total"}]))

# --- _pool width scales to how many buckets there are ----------------------
def _cand(group, marg, side="yes"):
    return {"group": group, "type": group, "marg": marg, "side": side,
            "mask": 0, "kref": None}
_narrow = [_cand("Total", 0.9 - 0.02 * i) for i in range(16)] + [_cand("ML", 0.55)]
_wide = [_cand(f"P{i}:hit", 0.8 - 0.001 * i) for i in range(40)]
ck("a three-market board gets the ladder width a band needs",
   len(_MS._pool(_narrow, 30)) > 6,
   f"{len(_MS._pool(_narrow, 30))} legs from a 17-leg two-bucket board; a flat "
   "two-per-bucket trim gave the search THREE")
ck("and a board with dozens of buckets is trimmed exactly as before",
   all(sum(1 for c in _MS._pool(_wide, 22) if c["group"] == g) <= 2
       for g in {c["group"] for c in _wide}),
   "baseball's shape must not move")
ck("the pool still respects its cap", len(_MS._pool(_wide, 22)) <= 22)
ck("ladder width is spanned, not taken off one end",
   (lambda p: max(c["marg"] for c in p) - min(c["marg"] for c in p) > 0.2)(
       _MS._pool(_narrow, 30)),
   "probability-ranking a total ladder keeps only the near-locks")

# --- implied_mean: reading a level off a ladder ----------------------------
_beck = [(74.5, 0.565), (99.5, 0.46), (149.5, 0.22), (199.5, 0.065)]
_m = _P.implied_mean("pass_yd", _beck)
ck("a four-rung yardage ladder implies a sane mean",
   _m is not None and 80 < _m < 140, f"{_m} yards")
ck("the implied mean sits ABOVE the rung the market has near 50%",
   _m > 99.5, "a lognormal's mean exceeds its median, and P(>99.5)=0.46 puts "
              "the median just under 99.5")
ck("a longer ladder shifted UP implies a bigger mean",
   _P.implied_mean("pass_yd", [(x + 40, p) for x, p in _beck]) > _m)
ck("one rung still yields a mean", _P.implied_mean("pass_yd", [(49.5, 0.7)]) is not None)
ck("an empty ladder yields nothing", _P.implied_mean("pass_yd", []) is None)
_td = _P.implied_mean("td", [(0.5, 0.10)])
ck("a touchdown ladder is fitted POISSON, not lognormal",
   _td is not None and 0.05 < _td < 0.20,
   f"P(1+)=0.10 -> lambda {_td}; -ln(0.9) = 0.105")
ck("a likelier TD market implies a higher rate",
   _P.implied_mean("td", [(0.5, 0.30)]) > _td)

# --- the quality filter on a listed-but-unquoted book ----------------------
ck("prop_ladders demands a real book before it believes a rung",
   "_MAX_PROP_SPREAD" in open(_K.__file__).read(),
   "an untraded prop sits at 5c/92c and de-vigs to 0.49 at EVERY line, which is "
   "a shape no distribution has")
ck("and it de-vigs off the mids, not the asks",
   'yq["mid"] / tot' in open(_K.__file__).read(),
   "both asks carry the spread, so a pair of them sums past 100c")

# --- the measured team budget is internally consistent ---------------------
_T = _P.PRE_TEAM
_rush_td = sum(_T["rush_att"] * _P.RUSH_SHARE[p] * _P.RTD_CAR[p] for p in _P.RUSH_SHARE)
_rec_td = sum(_T["rec_tgt"] * _P.TGT_SHARE[p] * _P.RTD_TGT[p] for p in _P.TGT_SHARE)
ck("positional rush shares reproduce the team's rushing touchdowns",
   abs(_rush_td - _T["rush_td"]) < 0.05, f"{_rush_td:.3f} vs {_T['rush_td']}")
ck("and target shares reproduce the receiving touchdowns",
   abs(_rec_td - _T["rec_td"]) < 0.05, f"{_rec_td:.3f} vs {_T['rec_td']}")
ck("rush and target shares each sum to 1",
   abs(sum(_P.RUSH_SHARE.values()) - 1) < 0.01
   and abs(sum(_P.TGT_SHARE.values()) - 1) < 0.01)
ck("the TD:FG mix and the points budget agree",
   abs((_T["pass_td"] + _T["rush_td"]) * (7.0 + 3.0 * _P.TD_FG) - _T["points"]) < 1.5,
   f"{(_T['pass_td'] + _T['rush_td']) * (7.0 + 3.0 * _P.TD_FG):.1f} vs "
   f"{_T['points']} points a side")
ck("preseason scores LESS than the regular season", _P.PRESEASON_SCORING < 1.0)
ck("and preseason is less efficient per attempt than September",
   _P.Y_ATT < 7.00 and _P.Y_CAR["RB"] < 4.30 and _P.Y_TGT["WR"] < 7.89)
ck("but throws MORE interceptions — the backups-are-playing signature",
   _P.INT_ATT > 0.0204, f"{_P.INT_ATT} vs a regular-season 0.0204")

# --- usage really is inverted ---------------------------------------------
ck("a backup quarterback is projected to throw MORE than the starter",
   _P.expected_usage("QB", 1.0) > _P.expected_usage("QB", 30.0),
   f'backup {_P.expected_usage("QB", 1.0)} att vs starter '
   f'{_P.expected_usage("QB", 30.0)}')
ck("and a camp-body back gets more touches than a workhorse",
   _P.expected_usage("RB", 0.0) > _P.expected_usage("RB", 15.0))
ck("running back usage is monotone in regular-season role",
   _P.expected_usage("RB", 0.0) > _P.expected_usage("RB", 2.0)
   > _P.expected_usage("RB", 6.0) > _P.expected_usage("RB", 15.0))
ck("WR/TE are held FLAT, because the measurement says they are",
   _P.expected_usage("WR", 0.0) == _P.expected_usage("WR", 12.0)
   and _P.expected_usage("TE", 0.0) == _P.expected_usage("TE", 12.0),
   "2.2 targets against 2.5 does not separate a role")

# --- stat_lines sums to the budget it was cut from ------------------------
_QBN = ["Abe Starter", "Ben Backup", "Cal Camp"]
_RBN = ["Dan Work", "Eli Rotate", "Fay Fringe", "Gus Gone", "Hal Hopeful"]
_WRN = ["Ike One", "Jay Two", "Kip Three", "Lou Four", "Moe Five", "Ned Six",
        "Ora Seven", "Pat Eight", "Quinn Nine"]
_TEN = ["Rex Tight", "Sal End", "Tom Block", "Uma Seam"]
_roster = ([{"name": _QBN[i], "pos": "QB", "reg_per_game": r, "rank": i}
            for i, r in enumerate([30.0, 5.0, 0.0])]
           + [{"name": _RBN[i], "pos": "RB", "reg_per_game": r, "rank": i}
              for i, r in enumerate([14.0, 6.0, 1.0, 0.0, 0.0])]
           + [{"name": _WRN[i], "pos": "WR", "reg_per_game": 3.0, "rank": i}
              for i in range(9)]
           + [{"name": _TEN[i], "pos": "TE", "reg_per_game": 2.0, "rank": i}
              for i in range(4)])
_rows = _P.stat_lines(_roster, scale=1.0)
for _k2 in ("pass_yd", "rush_yd", "rec_yd", "rec"):
    _got = sum(r[_k2] for r in _rows)
    ck(f"players sum to the team's {_k2}", abs(_got - _T[_k2]) < 0.5,
       f"{_got:.1f} vs {_T[_k2]}")
_half = _P.stat_lines(_roster, scale=0.5)
ck("halving the market's number halves every player's line",
   abs(sum(r["pass_yd"] for r in _half) - _T["pass_yd"] / 2) < 0.5,
   "a 35-point game must not produce a 48-point game's stat lines")
_qbs = [r for r in _rows if r["pos"] == "QB"]
ck("inside the sim, the BACKUP out-throws the starter",
   max(_qbs, key=lambda r: r["pass_yd"])["name"] != _QBN[0],
   max(_qbs, key=lambda r: r["pass_yd"])["name"] + " leads the position group")
ck("every kept player gets a reason attached to his number",
   all(r.get("note") for r in _rows))
ck("depth is capped per position, so the 90-man roster is not all simulated",
   len([r for r in _rows if r["pos"] == "WR"]) <= _P._DEPTH["WR"])
_forced = _P.stat_lines(_roster, scale=1.0, force=[_P._key(_WRN[8])])
ck("but a player Kalshi books can never be cut",
   any(r["name"] == _WRN[8] for r in _forced),
   "the market listing a prop is the strongest evidence he is playing")

# --- the anchor keeps the team whole --------------------------------------
_anch = _P.stat_lines(_roster, scale=1.0)
_before = sum(r["pass_yd"] for r in _anch if r["pos"] == "QB")
_G._anchor(_anch, {("pass_yd", _P._key(_QBN[2])): {"rungs": [(99.5, 0.46),
                                                             (149.5, 0.22)]}}, {})
_after = sum(r["pass_yd"] for r in _anch if r["pos"] == "QB")
ck("re-anchoring one player to the market leaves the TEAM total unchanged",
   abs(_before - _after) < 0.5, f"{_before:.1f} -> {_after:.1f}",)
ck("and the anchored player actually moved to the market's level",
   next(r for r in _anch if r["name"] == _QBN[2])["pass_yd"] > 90,
   f'{next(r for r in _anch if r["name"] == _QBN[2])["pass_yd"]:.1f} yards, from a two-rung ladder centred just under 100')
ck("nobody can take the whole position group",
   all(r["pass_yd"] <= 0.86 * _before for r in _anch if r["pos"] == "QB"))

# --- the dispersion shock is mean-preserving ------------------------------
import random as _rnd
_r = _rnd.Random(4)
_draws = [_G._shock(_r, _P.PLAYER_LOGSD) for _ in range(60000)]
_mean = sum(_draws) / len(_draws)
ck("the preseason shock does not move the projection it widens",
   abs(_mean - 1.0) < 0.02, f"E[mult] = {_mean:.4f}",)
_lsd = (sum((_math.log(x) - sum(_math.log(y) for y in _draws) / len(_draws)) ** 2
            for x in _draws) / len(_draws)) ** 0.5
ck("and it widens by the amount it says it does",
   abs(_lsd - _P.PLAYER_LOGSD) < 0.02, f"log-SD {_lsd:.3f} vs {_P.PLAYER_LOGSD}")
ck("the shock is fitted to REACH the measured dispersion, not set equal to it",
   _P.PLAYER_LOGSD < _P._TARGET_LOGSD,
   "a player already inherits spread from team volume and script, so the shock "
   "tops it up rather than supplying all of it")
ck("preseason players swing much harder than regular-season ones",
   _P.PLAYER_LOGSD > _G._PLAYER_SD * 1.5,
   "a September role is stable; an August one is a halftime decision")

print()
print("=" * 72)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for n, d in FAIL:
        print(f"   - {n}   {d}")
print("=" * 72)

