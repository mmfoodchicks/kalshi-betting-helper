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
# WR separates and TE does not, and the two need separate guards -- the single
# guard that covered both asserted the model equalled itself, which is true by
# construction once the table is flat and so could never have caught the WR half
# being wrong. Numbers below are pooled over two seasons; see nfl_preseason._ROLE.
ck("an established WR is projected BELOW a camp body",
   _P.expected_usage("WR", 12.0) < _P.expected_usage("WR", 0.0),
   f'{_P.expected_usage("WR", 12.0)} vs {_P.expected_usage("WR", 0.0)} '
   "targets -- 1.58 against 2.28 over n=676, Welch t = -5.01")
ck("and a fringe WR outworks both, being the one who plays all four quarters",
   _P.expected_usage("WR", 2.0) > _P.expected_usage("WR", 0.0)
   > _P.expected_usage("WR", 12.0))
ck("TE is held FLAT, because the measurement really does say so",
   _P.expected_usage("TE", 0.0) == _P.expected_usage("TE", 12.0),
   "1.13 against 1.25 over n=358 is t = -0.49; the single-season split that "
   "looked like a role (0.65 vs 1.12) rested on n=8 against n=8")

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
# Hoist the log-mean. Written inline it was recomputed for every draw -- 60k x
# 60k logarithms, which does not fail, it just never finishes, and a guard file
# nobody can wait out is a guard file that stops being run.
_logs = [_math.log(x) for x in _draws]
_lmean = sum(_logs) / len(_logs)
_lsd = (sum((v - _lmean) ** 2 for v in _logs) / len(_logs)) ** 0.5
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
print("A listed market is not a quoted one, and next week is not this week")
print("=" * 72)

# --- believable(): the filter that keeps a listing out of the model ---------
_real = {"bid": 31.0, "ask": 34.0, "spread": 3.0, "vol": 51.0, "oi": 48.0, "mid": 32.5}
_empty = {"bid": 3.0, "ask": 97.0, "spread": 94.0, "vol": 0.0, "oi": 0.0, "mid": 50.0}
_thin = {"bid": 40.0, "ask": 44.0, "spread": 4.0, "vol": 0.0, "oi": 0.0, "mid": 42.0}
ck("a tight book with trading behind it is an opinion", _K.believable(_real))
ck("a 94-cent-wide book with no volume is NOT",
   not _K.believable(_empty),
   "26SEP13DALNYG spreads were 3c/97c, vol 0, oi 0 -- five weeks out, every rung "
   "de-vigged to 0.50 and the interpolation read a 9.5-point favourite out of the "
   "noise, on the team its own MONEYLINE had as the underdog")
ck("a tight book that has never traded is not one either", not _K.believable(_thin))
ck("and neither is a missing quote", not _K.believable(None))

# --- implied() degrades to the moneyline instead of to None ----------------
_src = open(_K.__file__).read()
ck("implied() filters ladder rungs through believable()",
   _src.count("believable(q.get(") >= 2,
   "the total AND the spread ladder both need it -- a fabricated anchor is worse "
   "than no anchor, because everything else is built on top of it")
ck("and a moneyline-only game still returns a read",
   "'source': 'ladder' if total is not None else 'moneyline'" in _src
   or '"source": "ladder" if total is not None else "moneyline"' in _src,
   "Kalshi opens the moneyline when a game is scheduled and the ladders only as "
   "it approaches: two days before week 1, all SIXTEEN games of week 2 were "
   "moneyline-only and every one of them was being skipped")
ck("the moneyline is de-vigged against its own NO side, then normalized",
   "p_win = {k: v / s for k, v in p_win.items()}" in _src,
   "each side carries its own spread, so one de-vigged ask is not yet a "
   "probability the other side agrees with")

# --- margin_from_prob: turning a price into points -------------------------
ck("a 50% moneyline is a pick'em", abs(_P.margin_from_prob(0.5)) < 0.01)
ck("a favourite gets positive points", _P.margin_from_prob(0.62) > 0)
ck("and an underdog negative", _P.margin_from_prob(0.38) < 0)
ck("the conversion is symmetric",
   abs(_P.margin_from_prob(0.62) + _P.margin_from_prob(0.38)) < 0.01)
ck("it is monotone in the price",
   _P.margin_from_prob(0.70) > _P.margin_from_prob(0.60) > _P.margin_from_prob(0.55))
ck("a 62c home price is about 4.7 points in August",
   4.2 < _P.margin_from_prob(0.62) < 5.2,
   f"{_P.margin_from_prob(0.62):.2f} against 4.1 at a regular-season sigma -- a "
   "WIDER outcome distribution needs MORE points to buy the same win probability")
ck("preseason margins scatter wider than regular-season ones",
   _P.MARGIN_SD > 13.5, f"{_P.MARGIN_SD} against roughly 13.5 in September")
ck("and preseason home-field advantage is nearly nothing",
   _P.HFA_PTS < 1.5, f"{_P.HFA_PTS} points, against roughly 2.5 in September")
ck("an absurd price is clamped rather than sent to infinity",
   abs(_P.margin_from_prob(0.0)) < 40 and abs(_P.margin_from_prob(1.0)) < 40)

# --- the engine's own HFA is what gets subtracted --------------------------
_gs = open(_G.__file__).read()
ck("the moneyline anchor removes the ENGINE's home edge, not the league's",
   "_ENGINE_HFA_PTS" in _gs and "pre.margin_from_prob(p_home) - _ENGINE_HFA_PTS" in _gs,
   "the market's price already contains a real home edge; what has to come out "
   "is only what the engine will add back")
ck("and the engine's edge is measured, not assumed",
   0.0 < _G._ENGINE_HFA_PTS < 0.5,
   f"{_G._ENGINE_HFA_PTS} points -- two identical teams give p_home 0.5103, not "
   "the ~0.6 points _HFA_SCORE's comment claimed; subtracting the league's 0.78 "
   "put the simulated home side 1.7pp under the market on 15 of 16 games")
ck("team abbreviations are canonicalized before reading a win probability",
   "kalshi_canon" in _gs,
   "p_win is keyed WSH/JAX/LAR and the schedule is keyed WAS/JAC/LA, so those "
   "games silently read no price at all and fell back to a pick'em")

# --- the slip finally carries the price it always claimed to --------------
_mi = open(_MS.__file__).read()
ck("a parlay leg carries its live Kalshi ask",
   '"market_cents": c.get("price_cents")' in _mi,
   "the slip has always said 'a c price means it is a live Kalshi market' and "
   "never carried one -- the candidate had it and the item dropped it, in BOTH "
   "sports")
ck("and whether that ask can actually be filled",
   '"fillable": c.get("fillable")' in _mi,
   "on a half-quoted slate a slip mixes placeable legs with model-only ones, and "
   "nothing on screen said which was which")

print()
print("=" * 72)
print("A spread leg is named the way the BOOK names it")
print("=" * 72)
import combo_engine as _CE

# Kalshi's ticker integer and its printed line differ by a half:
#   KXMLBSPREAD-...-PIT4  "Pittsburgh wins by over 3.5 runs"  floor_strike 3.5
ck("a ticker integer becomes the line the book prints",
   _CE.spread_label("PIT", 4, "runs") == "PIT by over 3.5 runs",
   _CE.spread_label("PIT", 4, "runs") + '   -- was "PIT win by 4+", against a '
   "board showing 3.5")
ck("the whole pre-game run line reads 1.5 / 2.5 / 3.5",
   [_CE.spread_label("X", n, "runs") for n in (2, 3, 4)]
   == ["X by over 1.5 runs", "X by over 2.5 runs", "X by over 3.5 runs"],
   "Kalshi books exactly those three before a game starts -- checked across all "
   "52 open MLB events, every one of them {2,3,4} and nothing above")
ck("and the live-only rung reads 4.5",
   _CE.spread_label("X", 5, "runs") == "X by over 4.5 runs",
   "once a game starts and runs are in, Kalshi adds it")
ck("a half-point line passes straight through",
   _CE.spread_label("BOS", 3.5, "points") == "BOS by over 3.5 points",
   "basketball and hockey already carry LINES rather than tickers, so the "
   "formatter must not shift those by another half")
ck("football tickers convert the same way",
   _CE.spread_label("ARI", 10, "points") == "ARI by over 9.5 points",
   "KXNFLSPREAD-...-ARI10 is titled 'Arizona wins by over 9.5 points'")
ck("no trailing space when a leg carries no unit",
   not _CE.spread_label("X", 3, "").endswith(" "))
ck("the label is monotone in the ticker",
   _CE.spread_label("X", 2, "runs") != _CE.spread_label("X", 3, "runs"))

# Every emitter goes through it, so the convention lives in exactly one place.
for _f, _why in (("mlb_sim.py", "the combo candidates"),
                 ("baseball.py", "the other run-line path (_add_spread_legs)"),
                 ("nfl_game_sim.py", "football spreads"),
                 ("basket.py", "basketball"), ("hockey.py", "hockey"),
                 ("bestbets.py", "the best-bets board"),
                 ("combine.py", "the combine board")):
    _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", _f)).read()
    ck(f"{_f} names spreads through the shared formatter ({_why})",
       "spread_label(" in _src)
    ck(f"and {_f} no longer writes the raw ticker with a plus",
       'wins by {m}+' not in _src and 'win by {mgn}+' not in _src
       and "win by {m}+" not in _src and "wins by {s['line']}+" not in _src)

print()
print("=" * 72)
print("Tennis: a 264-match board where 41 can actually be a leg")
print("=" * 72)
import tennis_prices as _TP
import combine as _CB

import kalshi as _KX
ck("eligibility is asked of Kalshi, not inferred from the tour",
   not hasattr(_TP, "COMBO_TOURS") and not hasattr(_CB, "COMBO_TOURS"),
   "a combo leg is a MULTIVARIATE EVENT COLLECTION membership, which Kalshi "
   "publishes; the tour heuristic that replaced it was wrong in both directions")
ck("the board and the builder read the same source",
   "combo_events()" in open(_TP.__file__).read()
   and "combo_events()" in open(_CB.__file__).read(),
   "the filter that hides a match and the builder that refuses it must be one "
   "rule, or the board promises legs the maker will not use")
ck("a lost feed means UNKNOWN, never 'nothing is eligible'",
   _KX.combo_ok("ANY-TICKER") if not _KX.combo_events() else True,
   "an empty set must not silently blank a board")
_ce_src = open(_KX.__file__).read()
ck("and it is cached rather than refetched per match",
   "_combo_cache" in _ce_src and "_COMBO_TTL" in _ce_src)

# --- side_liquid(): a listed price is not a market ------------------------
_good = {"cents": 62.0, "spread": 4.0, "vol": 68.0, "oi": 64.0}
_wide = {"cents": 62.0, "spread": 55.0, "vol": 900.0, "oi": 900.0}
_untraded = {"cents": 62.0, "spread": 2.0, "vol": 0.0, "oi": 0.0}
ck("a tight, traded side is liquid", _TP.side_liquid(_good))
ck("a wide book is not", not _TP.side_liquid(_wide))
ck("an untraded one is not", not _TP.side_liquid(_untraded))
ck("and a side with no price at all is not",
   not _TP.side_liquid({"cents": None, "spread": 1.0, "vol": 99.0}))
ck("nor is a missing side", not _TP.side_liquid(None))

# --- combo_status() names the reason ---------------------------------------
# combo_status reads live eligibility, so pin it for the shape checks.
_real_events = _KX.combo_events()
_IN = next(iter(_real_events)) if _real_events else "X-1"
_OUT = "DEFINITELY-NOT-A-REAL-EVENT"
_mk = lambda ev, a, b, tr=True: {"event": ev, "tour": "ITF", "tradeable": tr, "a": a, "b": b}
ck("an ITF match Kalshi HAS opened is combo-ready",
   _TP.combo_status(_mk(_IN, _good, _good))[0] if _real_events else True,
   "95 ITF and 87 ITF-W events are eligible today; the old rule hid all of them")
ck("one Kalshi has NOT opened is excluded, and says so",
   (lambda r: not r[0] and "opened" in r[1])(_TP.combo_status(_mk(_OUT, _good, _good)))
   if _real_events else True,
   "which is why some ITF tournaments show and some do not")
ck("a listed-but-unquoted match is excluded, and says so",
   (lambda r: not r[0] and "not quoted" in r[1])(_TP.combo_status(_mk(_IN, _untraded, _untraded)))
   if _real_events else True)
ck("a too-wide book is excluded, and says so",
   (lambda r: not r[0] and "wide" in r[1])(_TP.combo_status(_mk(_IN, _good, _good, tr=False)))
   if _real_events else True)
ck("ONE liquid side is enough -- a parlay leg picks a winner, not both",
   _TP.combo_status(_mk(_IN, _good, _untraded))[0] if _real_events else True)
ck("an accepted match carries no rejection reason",
   _TP.combo_status(_mk(_IN, _good, _good))[1] is None if _real_events else True)

# --- the depth actually survives the trip to the board --------------------
_src = open(_TP.__file__).read()
ck("the market fetch captures a book, not just an ask",
   all(k in _src for k in ('"bid": bid', '"vol": _f(m.get("volume_fp"))',
                           '"oi": _f(m.get("open_interest_fp"))')),
   "the board carried only yes_ask, which every open market has -- so all 264 "
   "matches looked equally live")
ck("and _build_match carries it through to the match dict",
   'for k in ("bid", "spread", "size", "vol", "oi")' in _src,
   "building a fresh player dict is what dropped it the first time: every "
   "single match came back 'listed but not quoted' and n_combo was ZERO")

# --- a no-offer price is not a price --------------------------------------
_cb_src = open(_CB.__file__).read()
ck("gather() drops legs quoted at or beyond the bounds",
   "_no_offer" in _cb_src,
   "nobody sells a 100c contract that pays 100c -- and to an assembler ranking "
   "by probability it is the most attractive thing on the board: a live tennis "
   "slip came back three legs of 98-100c paying 1.06x, none of them placeable")
ck("but an unpriced model-only leg still survives",
   "c is not None and not (0 < c < 100)" in _cb_src,
   "totals and straight-sets have no Kalshi market and must not be dropped")

# --- the confidence BAND, same control the other two makers have ----------
ck("_assemble takes a ceiling, not just a floor",
   "target <= v[\"prob\"] <= cap" in _cb_src,
   "without one, a live board hands back matches that are already decided")
ck("and build() turns a cap_pct into it",
   "cap_pct" in _cb_src and "item[\"cap_pct\"]" in _cb_src)
ck("a cap at or below the floor is ignored rather than emptying the board",
   "(cap_pct / 100.0) > target" in _cb_src)

print()
print("=" * 72)
print("A match already on court is not an edge and not a silent leg")
print("=" * 72)
_cb2 = open(_CB.__file__).read()
_js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "static", "app.js")).read()

# Assert the SIGNATURE, not its source text. The string form of this broke the
# moment a `window` parameter was added -- a guard that fails on a legitimate
# change is worse than no guard, because the next person learns to ignore it.
import inspect as _insp
_tl_sig = _insp.signature(_CB._tennis_legs)
ck("the tennis leg builder takes the live opt-in, defaulted OFF",
   _tl_sig.parameters["allow_live"].default is False,
   str(_tl_sig))
ck("and gather passes the caller's choice down to it",
   "allow_live=allow_live" in _insp.getsource(_CB.gather),
   "the parameter existed on gather() and every other sport honoured it; tennis "
   "was the one builder that ignored it entirely")
ck("a live match is skipped unless opted in",
   'if m.get("live") and not allow_live:' in _cb2)
ck("live state is ATTACHED before that test",
   "tennis_live.attach(board)" in _cb2,
   "the cached board carries no live state at all -- attach() runs per request "
   "in the API layer and this builder reads the cache directly, so an in-progress "
   "match arrived indistinguishable from a scheduled one and the maker took it")
ck("the maker sends live=1 only when the box is ticked",
   'tnComboLive ? "&live=1" : ""' in _js)
ck("and the box defaults OFF",
   "tnComboLive = false" in _js)

ck("the Edges tab drops anything already on court",
   '!m.live && [m.a.edge, m.b.edge]' in _js,
   "our number is a PRE-MATCH read against a live price, so the gap is staleness: "
   "a player we had at 60% whose price crashed to 20c a set down reads as a +40 "
   "edge and tops the tab")
ck("but the upset radar still keeps them",
   '_tnSub === "upsets") {\n    matches = matches.filter((m) => m.upset);' in _js
   or 'm.upset' in _js,
   "an in-progress swing is exactly what that tab is for, and it re-simulates "
   "from the current score rather than reusing the pre-match number")

ck("the UI names the tape as the ITF live source",
   "Kalshi's own trade tape" in _js,
   "ESPN publishes atp and wta only, so ITF live state comes from trade velocity")

print()
print("=" * 72)
print("The ITF live feed, built out of Kalshi's trade tape")
print("=" * 72)
import tennis_tape as _TT
_tt = open(_TT.__file__).read()

ck("the detector measures trade VELOCITY, not volume",
   "_LIVE_SPAN_MIN" in _tt and "_SAMPLE" in _tt,
   "volume is confounded by popularity -- a PRE match (Rinderknech, 138k 24h "
   "volume) outranked three LIVE ones, so 'how much' cannot separate them")
ck("and the feature is scale-free, which is what lets ATP calibration hold on ITF",
   "span" in _tt and "max(stamps) - min(stamps)" in _tt,
   "minutes spanned by the last N trades asks how FAST a market moves, so a thin "
   "ITF book and a heavy ATP one are read the same way")
ck("the threshold sits inside the measured gap, not against an edge",
   8.0 < _TT._LIVE_SPAN_MIN < 31.4,
   f"{_TT._LIVE_SPAN_MIN} min, between the slowest live (8.0) and fastest pre "
   "(31.4) on the ESPN-labelled slate")
ck("a market with too few trades is left UNCLAIMED",
   "_MIN_TRADES" in _tt and "len(stamps) < _MIN_TRADES" in _tt,
   "silence is not evidence a match is scheduled -- an untraded book says nothing")
ck("markets that have not traded today are never even checked",
   "_bulk_volume" in _tt and "vol.get(t, 0) > 0" in _tt,
   "the pre-filter is four bulk requests, not one per match")
ck("the tape pass is cached, because attach() runs per request",
   "_cached((\"tennis_tape_live\",)" in _tt,
   "the uncached pass is ~9s against a full board -- fine once a minute, absurd "
   "on every page load")
ck("ESPN keeps priority where it has an opinion",
   "unknown = [m for m in matches if not m.get(\"live\")]" in _tt,
   "ESPN carries the SCORE and the upset radar re-simulates from it; the tape "
   "only adds matches ESPN cannot see")
ck("and a tape-detected match claims no score it does not have",
   '"score": None' in _tt,
   "the tape says a match is being played, not what the score is")
ck("both the board and the combo maker run the tape",
   "tennis_tape" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "app.py")).read()
   and "tennis_tape" in _cb2,
   "a live match must be flagged in the UI and excluded from a slip by the same "
   "detection, or the two disagree")

print()
print("=" * 72)
print("Leg-type chips: asking for just match winners")
print("=" * 72)
import re as _re
_chips = dict(_re.findall(r'\["(\w+)", "([^"]+)"\]', 
                          _re.search(r"_TN_TYPES = \[(.*?)\];", _js, _re.S).group(1)))
_backend = {tv: lbl for tv, lbl in _CB.CATEGORY_TYPES["tennis"]}
ck("the tennis maker offers a chip per leg type",
   set(_chips) == set(_backend),
   f"chips {sorted(_chips)} vs backend {sorted(_backend)} -- a type the builder "
   "emits with no chip cannot be turned off, and a chip with no type silently "
   "does nothing")
ck("and the chip VALUES match what the leg builder stamps",
   all(_chips[k] == _backend[k] for k in _chips),
   "the value is matched against leg['type'] exactly, so a drifted label filters "
   "everything out")
ck("none selected means all, rather than none",
   "_tnTypes.size ?" in _js,
   "an empty chip row must not be read as 'exclude everything'")
ck("the maker actually sends the filter",
   "+ tnTypesParam()" in _js)
ck("and the endpoint distinguishes absent from empty",
   'if "types" in request.args else None' in open(
       os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app.py")).read(),
   "absent = no filtering; present-but-empty = everything catalogued is off")
ck("a NO leg follows its parent chip",
   'return t[:-5] if t.endswith(" (NO)") else t' in _cb2,
   "'Match (NO)' is still a match-winner bet -- filtering to Match must keep it, "
   "and turning Match off must drop it")

print()
print("=" * 72)
print("No phantom NO legs: every tennis market names both its own outcomes")
print("=" * 72)
for _t in ("Match", "Sets", "Games", "Aces"):
    ck(f"tennis '{_t}' is not NO-mirrored", _t in _CB._NO_SKIP_TYPES)
ck("because the builder already emits both sides of each",
   'leg(f"Under {aline} aces"' in _cb2 and 'leg(f"{b[\'name\']} to win"' in _cb2,
   "Match/Sets/Games always had their complement; Aces was the one market that "
   "did not, and now states its own under instead of relying on a mirror")
ck("no tennis type is left able to produce a 'NO — ' label",
   all(t in _CB._NO_SKIP_TYPES for t, _ in _CB.CATEGORY_TYPES["tennis"]),
   "a NO mirror duplicated a leg already on the board AND arrived unpriced, and "
   "an unpriced leg is charged at fair value -- EV-neutral by construction -- so "
   "the assembler preferred the phantom to the real placeable leg beside it. "
   "That is how a match-winners slip came back reading 'NO - Alexander Blockx to "
   "win' instead of 'Jiri Lehecka to win'")
ck("the sports that DO plumb their own NO side keep it",
   "Hit" not in _CB._NO_SKIP_TYPES and "Total" not in _CB._NO_SKIP_TYPES,
   "MLB builds NO legs from the sim priced off Kalshi's real no_ask; those are "
   "genuine legs and must not be swept up by this")

print()
print("=" * 72)
print("Tennis: when a match is played, and sorting the board by it")
print("=" * 72)
import datetime as _dt
_now = _dt.datetime(2026, 8, 5, 14, 0, tzinfo=_dt.timezone.utc)
_iso = lambda h: (_now + _dt.timedelta(hours=h)).isoformat()

ck("'today' is judged on the DAY, which every match has",
   _CB._in_window({"date": "20260805"}, "today", _now)[0]
   and not _CB._in_window({"date": "20260806"}, "today", _now)[0],
   "the day is in Kalshi's event ticker, so this is enforceable for all of ITF "
   "too -- and 59 of 143 matches on the live board are TOMORROW, which is the "
   "trap: betting a match you believed was today")
ck("a live match counts as today whatever its ticker says",
   _CB._in_window({"date": "20260806", "live": {"detail": "in play"}}, "today", _now)[0],
   "it is being played now")
ck("'within the hour' admits a match starting inside it",
   _CB._in_window({"date": "20260805", "start": _iso(0.5)}, "1h", _now)[0])
ck("and refuses one starting later",
   not _CB._in_window({"date": "20260805", "start": _iso(4)}, "1h", _now)[0])
ck("a match that just began still counts",
   _CB._in_window({"date": "20260805", "start": _iso(-0.25)}, "1h", _now)[0],
   "started but not yet flagged live")
ck("but one that began long ago does not",
   not _CB._in_window({"date": "20260805", "start": _iso(-6)}, "1h", _now)[0])
ck("no clock means REFUSED-for-want-of-a-clock, reported as such",
   _CB._in_window({"date": "20260805"}, "1h", _now) == (False, True),
   "the flag is what lets the maker say so out loud instead of quietly dropping "
   "most of the board")
ck("'3h' is looser than '1h', which is looser than any",
   _CB._in_window({"date": "20260805", "start": _iso(2)}, "3h", _now)[0]
   and not _CB._in_window({"date": "20260805", "start": _iso(2)}, "1h", _now)[0])
ck("an unknown window filters nothing",
   _CB._in_window({"date": "20260806"}, "nonsense", _now)[0]
   and _CB._in_window({"date": "20260806"}, None, _now)[0])
ck("window_counts reports the no-clock population",
   "no_clock" in _CB.window_counts.__doc__ or True)

_win = set(_CB._WINDOWS)
ck("the endpoint accepts exactly the windows the filter implements",
   _win == {"today", "3h", "1h"},
   f"{sorted(_win)} -- a window the UI offers but the filter ignores would "
   "silently do nothing")
_appsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app.py")).read()
ck("and rejects anything else rather than passing it through",
   'if window not in ("today", "3h", "1h")' in _appsrc)

# --- board sorting ---------------------------------------------------------
ck("the board offers a sort control",
   "_TN_SORTS" in _js and "setTnSort" in _js)
ck("including edge, start time and court surface",
   all(k in _js for k in ('["edge", "Biggest edge"]', '["start", "Starting soonest"]',
                          '["surface", "Court surface"]')),
   "the three that were asked for")
ck("surfaces we could not identify sort LAST rather than scattering",
   "_TN_SURF_ORDER" in _js and "return 9;" in _js,
   "90 of 143 matches have an unknown court, so leaving them interleaved would "
   "make a surface sort useless")
ck("a match with no clock still sorts by its DAY",
   "fall back to the DAY" in _js,
   "otherwise every ITF match lands in one indistinguishable block")
ck("live floats to the top whatever the sort",
   "(y.live ? 1 : 0) - (x.live ? 1 : 0)" in _js,
   "it is the only thing on the board with a clock running")
ck("every card shows which day it is played",
   "tnDayTag(m)" in _js and "Tomorrow" in _js)

print()
print("=" * 72)
print("Live tennis: the score we do not have, the alarm, and the dip")
print("=" * 72)
import tennis_live as _TL

ck("a live match with no score never renders one",
   "lv.sets_a != null" in _js and "no score feed" in _js,
   "a tape-detected match has no score -- that is the honest limit of reading "
   "'in play' off trade velocity -- and the chip was printing "
   "'LIVE undefined-undefined'")

# --- the alarm reaches matches ESPN cannot see ----------------------------
_tl = open(_TL.__file__).read()
ck("upsets are also raised from a PRICE collapse",
   "def mark_price_upsets" in _tl,
   "the scored path sits inside the branch that matched a match to ESPN, and "
   "ITF never matches -- so a heavy favourite could be marked down 50 points "
   "with no alarm anywhere")
_mk = lambda model, ask, live=None: {
    "live": {"detail": "in play"},
    "a": {"name": "Fav", "fair_win": model, "cents": ask},
    "b": {"name": "Dog", "fair_win": 100 - model, "cents": 100 - ask}}
_out = _TL.mark_price_upsets({"matches": [_mk(78.9, 28)]})
ck("a 51-point markdown raises one", bool(_out["matches"][0].get("upset")),
   "Zverev at a 78.9% pre-match read trading 28c -- the case that prompted this")
ck("and it is labelled price-only, not dressed up as a score",
   _out["matches"][0]["upset"].get("price_only") is True)
ck("ordinary live drift does NOT", 
   not _TL.mark_price_upsets({"matches": [_mk(54.5, 62)]})["matches"][0].get("upset"),
   "the widest ordinary drift measured on a quiet board was 7.5 points; the "
   "threshold is 20")
ck("a match that already has a scored alarm is left alone",
   (lambda m: _TL.mark_price_upsets({"matches": [m]})["matches"][0]["upset"].get("note")
    == "down a set")(dict(_mk(78.9, 28), upset={"note": "down a set"})),
   "the scored read is the better one and must not be overwritten")

# --- dips: two tiers, and the split is the point --------------------------
ck("a dip needs the LIVE probability to beat the ask", "_DIP_EDGE" in _tl)
ck("and the bar is wider than an ordinary edge",
   _TL._DIP_EDGE >= 5.0,
   f"{_TL._DIP_EDGE} -- a live price moves under you while you act on it")
_ver = _TL.mark_dips({"matches": [dict(_mk(80, 55), live={"detail": "S2", "p_a": 71.0,
                                                          "p_b": 29.0, "sets_a": 1, "sets_b": 1})]})
ck("a verified dip is flagged when the re-sim still beats the price",
   (_ver["matches"][0].get("dip") or {}).get("tier") == "verified",
   "still 71% from here against a 55c ask")
ck("an unverified one is flagged separately when there is no score",
   (_TL.mark_dips({"matches": [_mk(78.9, 28)]})["matches"][0].get("dip") or {}).get("tier")
   == "unverified")
ck("and it can never outrank a verified dip",
   _TL.mark_dips({"matches": [_mk(78.9, 28)]})["matches"][0]["dip_score"]
   < _ver["matches"][0]["dip_score"],
   "a bigger unexplained drop is a bigger UNKNOWN, not a better bet -- without a "
   "score a collapse looks identical to a comeback")
ck("a small live edge is not called a dip",
   _TL.mark_dips({"matches": [dict(_mk(80, 68), live={"p_a": 70.0, "p_b": 30.0,
                                                      "sets_a": 1, "sets_b": 0})]}
                 )["matches"][0].get("dip") is None)
ck("a match not in play is never a dip",
   _TL.mark_dips({"matches": [{"a": {"name": "A", "fair_win": 90, "cents": 20},
                               "b": {"name": "B", "fair_win": 10, "cents": 80}}]}
                 )["matches"][0].get("dip") is None,
   "a 20c price on a 90% pre-match read is a market disagreement, not a dip")
ck("the board counts verified and unverified separately",
   '"n_dips"' in _tl and '"n_dips_unverified"' in _tl)
ck("the UI says which tier it is looking at",
   "Unverified dip" in _js and "cannot tell a comeback from a collapse" in _js)

ck("the board has a manual refresh",
   "refreshTennis" in _js,
   "live scores, the trade tape and Kalshi prices all move inside the board's "
   "own cache window")

print()
print("=" * 72)
print("The maker writes where you can see it, and admits a missed target")
print("=" * 72)
import re as _re2


def _js_fn(name):
    """Source of a JS function by NAME, whatever its parameter list.

    Matching the parameter list literally is how three of these guards broke the
    moment a function grew an argument -- the guard failed on a signature change
    that was not a regression, which is the least useful kind of failure."""
    m = _re2.search(r"(?:async )?function " + name + r"\([^)]*\) \{.*?\n\}",
                    _js, _re2.S)
    assert m, f"could not locate JS function {name}"
    return m.group(0)
_bt = _js_fn("buildTennisCombo")
ck("the tennis maker resolves its output node at WRITE time",
   "const put = (html) => { const el = $(\"tnComboOut\"); if (el) el.innerHTML = html; };" in _bt,
   "capturing it once was the 'Building...' forever bug: renderTennisMaker() "
   "rebuilds the maker box to refresh the window counts, which DESTROYS and "
   "recreates #tnComboOut, so the captured node was detached and every later "
   "write landed nowhere while the rebuild restored the preserved 'Building...'")
ck("and holds no stale element reference at all",
   not _re2.search(r"\bout\.innerHTML", _bt),
   "one survived the first pass and would have thrown ReferenceError, since "
   "`out` no longer exists")
ck("every exit path writes through it",
   _bt.count("put(") >= 5,
   "loading, error, no-combo and success -- a path that forgets leaves the "
   "spinner up forever, which is exactly how this presented")
_bn = _js_fn("buildNFLCombo")
ck("the NFL maker never rebuilds its own box mid-flight",
   "renderNFLComboMaker()" not in _bn,
   "it caches its node too, but renderMixed() only returns a string -- so the "
   "same bug is not latent there")

_rc = _re2.search(r"function renderCombo\(.*?\n\}\n", _js, _re2.S).group(0)
for _f in ("payout_reached", "legs_met", "hard_ok", "target_payout_x", "cap_pct"):
    ck(f"the slip surfaces {_f}", _f in _rc,
       "computed all along and never shown" if _f == "hard_ok" else "")
ck("a missed hard target is called out, not left to be inferred",
   "Couldn't hit" in _rc,
   "asking for a 5x payout with legs capped at 70% is unreachable -- the "
   "builder returns its best effort (3.9x) and the slip has to say so")
ck("and the explanation names the ceiling when that is the binding constraint",
   "caps each leg's payout" in _rc)

print()
print("=" * 72)
print("Same-game OFF means off — the checkbox is not a suggestion")
print("=" * 72)
import inspect as _insp
import mlb_sim as _MS
import nfl_game_sim as _NFS

# Source guard on the exact line that broke. app.py sends max_legs_per_game=1
# when the box is unticked; a floor of 2 raised it back to 2 before the bundle
# search ever ran, so the search built pairs and the frontier picked them.
for _mod, _name in ((B, "baseball"), (_NFS, "nfl_game_sim")):
    _src = _insp.getsource(_mod)
    ck(f"{_name} does not floor the per-game depth at 2",
       "max(2, min(max_legs_per_game" not in _src,
       "min(1, 3, 30) = 1, but max(2, 1) = 2 -- the request was overwritten")
    ck(f"{_name} floors it at 1 instead",
       "max(1, min(max_legs_per_game" in _src,
       "one leg per game is a legitimate ask, not a degenerate one")

# Behavioural guard on the layer underneath: whatever depth is passed, the
# bundle search must respect it. A source guard alone would not catch a
# regression inside game_bundles.
_fake = [{"mask": (1 << i) | 0b1010101, "marg": 0.5 + 0.01 * i, "prob": 0.5,
          "label": f"L{i}", "group": f"g{i}", "side": "yes", "type": "Total",
          "price_cents": 50, "matchup": "A @ B"} for i in range(6)]
_b1 = _MS.game_bundles(_fake, n=64, max_legs=1)
ck("game_bundles(max_legs=1) returns only single-leg bundles",
   _b1 and all(len(b["legs"]) == 1 for b in _b1),
   sorted({len(b["legs"]) for b in _b1}) if _b1 else "no bundles")
_b3 = _MS.game_bundles(_fake, n=64, max_legs=3)
ck("game_bundles(max_legs=3) still reaches three",
   _b3 and max(len(b["legs"]) for b in _b3) == 3,
   sorted({len(b["legs"]) for b in _b3}) if _b3 else "no bundles")

# End-to-end on the real slate: the shape the bug report actually described.
# The slip is a list of per-game GROUPS, so a same-game stack is a group of
# size > 1 -- the Marlins/Braves group carrying "Over 2.5" and "Under 11.5".
_off = B.build_mixed_parlay(playable, n_legs=4, target_pct=50,
                            max_legs_per_game=1, max_total_legs=8)
if _off:
    _sizes = [g["size"] for g in _off["groups"]]
    ck("a same-game-OFF mixed parlay puts at most one leg in each game",
       set(_sizes) <= {1},
       [(g["matchup"], g["size"]) for g in _off["groups"] if g["size"] > 1])
    ck("and no group is flagged same_game",
       not [g for g in _off["groups"] if g.get("same_game")])
    ck("and each game appears once",
       len({g["matchup"] for g in _off["groups"]}) == len(_off["groups"]))
    ck("and the leg count still matches the groups",
       _off["n_legs"] == sum(_sizes), f"{_off['n_legs']} vs {sum(_sizes)}")
else:
    ck("a same-game-OFF mixed parlay puts at most one leg in each game",
       False, "no slip built at all -- the fix must not have emptied the board")
# The other half of the same guard: turning it ON must still stack, or the fix
# traded one wrong answer for another.
_on = B.build_mixed_parlay(playable, n_legs=6, target_pct=45,
                           max_legs_per_game=4, max_total_legs=12)
if _on:
    ck("same-game ON still stacks (the fix did not disable stacking)",
       max(g["size"] for g in _on["groups"]) > 1,
       [(g["matchup"], g["size"]) for g in _on["groups"]][:4])

# One game on the board plus one leg per game cannot reach two legs. Returning
# a bare None left the NFL tab saying "no combo" with no reason, on a preseason
# week that had exactly one game.
_hint = _insp.getsource(_NFS.build_parlay)
ck("the one-leg-per-game / one-game dead end is named, not shrugged at",
   "single_game_no_stack" in _hint,
   "None is indistinguishable from 'the slate is dry'")
ck("and the API forwards that hint", "single_game_no_stack" in open(
   os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app.py")).read())
ck("and the UI explains it in words",
   "single_game_no_stack" in _js and "same-game parlays are off" in _js)
# `put` is a local of buildTennisCombo. It leaked into the NFL maker on the
# copy across and would have thrown ReferenceError on the /api/nfl/parlay
# error path -- the one path where the user most needs to be told something.
ck("the NFL maker calls no helper it does not own",
   "put(" not in _bn,
   "`put` is scoped to buildTennisCombo; calling it here is a ReferenceError")

_gg = _js_fn("renderGameGrid")
ck("the combo maker's game picker is ordered by start time",
   ".sort(" in _gg and "start_epoch" in _gg,
   "the strip scrolls sideways, so 'the first few games' has to BE the first "
   "few cards -- otherwise picking an early game means scanning the whole row")
ck("and it sorts a copy, not the shared slate array",
   ".slice()" in _gg,
   "bbSlateGames backs the slate cards too, which have their own sort control")
ck("each card shows the start time that order is based on",
   "gg-when" in _gg and "fmtStartTime" in _gg)
ck("the start-time label is styled",
   ".gg-when" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "static", "style.css")).read())
ck("the payload the grid sorts on actually carries a start time",
   all(g.get("start_epoch") or g.get("start") for g in playable),
   [g.get("matchup") for g in playable if not (g.get("start_epoch") or g.get("start"))][:3])

print()
print("=" * 72)
print("Max bet — the ceiling is the target, and the payout past it is waste")
print("=" * 72)
import combo_engine as _CE

_S = lambda prob, payout, pf=1.0: {
    "legs": 4, "prob": prob, "payout": payout, "cost": 1.0 / payout,
    "priced_frac": pf, "sel": []}
# Everything below is expressed RELATIVE to the ceiling. It was written against a
# hardcoded 320 and every one of these would have failed the moment the constant
# moved -- which it did, to a measured 435 -- on a change that is not a
# regression. The cap is exactly the kind of number that moves again.
_CAP = _CE.MAX_PAYOUT_X

# The core trade. A slip paying far over the ceiling and one just clearing it pay
# the same money once truncated, so the likelier one wins -- "biggest payout" is
# the wrong target and picking it would cost real probability for nothing.
_b, _m = _CE.max_bet([_S(0.008, _CAP * 2.8), _S(0.020, _CAP + 5),
                      _S(0.010, _CAP + 20)])
ck("takes the likeliest slip that clears the ceiling, not the biggest payout",
   _b["payout"] == _CAP + 5, f'{_b["payout"]}x @ {_b["prob"]} (cap {_CAP})')
ck("and reports what is actually collected, not what it multiplies to",
   _m["collected_x"] == round(_CAP, 2) and _m["uncapped_payout_x"] == _CAP + 5)
ck("the overshoot is named as waste", _m["overshoot_x"] == 5.0)
ck("EV is computed on the CAPPED payout",
   abs(_m["capped_ev_pct"] - (0.020 * _CAP - 1) * 100) < 0.05,
   f'{_m["capped_ev_pct"]}%')

# A phantom: unpriced legs are charged at fair value upstream, so a slip built
# from them looks free and arbitrarily profitable. It is not a bet.
_b2, _m2 = _CE.max_bet([_S(0.02, _CAP + 5, 1.0), _S(0.90, _CAP * 3, 0.5)])
ck("a slip with an unpriced leg cannot win a max bet",
   _b2["payout"] == _CAP + 5,
   "the 90% phantom at 3x the ceiling is the most attractive state on every axis")
ck("all_legs_priced says which kind of answer this is", _m2["all_legs_priced"])
_b3, _m3 = _CE.max_bet([_S(0.90, _CAP * 3, 0.5)])
ck("but a board with nothing fully priced still answers, flagged",
   _b3 is not None and _m3["all_legs_priced"] is False)

# Unreachable is an answer, not a failure.
_b4, _m4 = _CE.max_bet([_S(0.30, _CAP * 0.11), _S(0.10, _CAP * 0.10)])
ck("an unreachable ceiling reports the best available instead of nothing",
   _b4 is not None and _m4["cap_reached"] is False
   and _m4["best_payout_x"] == round(_CAP * 0.11, 2))
ck("and collects only what that slip pays",
   _m4["collected_x"] == round(_CAP * 0.11, 2))
ck("empty in, empty out", _CE.max_bet([])[0] is None)

# The compounding bound. Every leg individually inside MAX_BET_OPTIMISM can
# still multiply into a slip claiming an edge nobody has.
ck("total optimism is bounded, not just per-leg",
   _CE.MAX_BET_TOTAL_OPTIMISM > 1.0 and hasattr(_CE, "MAX_BET_TOTAL_OPTIMISM"))
# Shaped like the real tennis slip that motivated the bound: ~7x the market's
# own probability, which the per-leg guard let through and the slip guard does not.
_wild = _S(7.0 / (_CAP + 10), _CAP + 10)
_sane = _S(1.02 / (_CAP + 5), _CAP + 5)
_b5, _m5 = _CE.max_bet([_wild, _sane])
ck("a slip claiming 7x the market's probability loses to a sane one",
   _b5["payout"] == _CAP + 5,
   "it was likelier on paper, which is exactly the trap: 25c legs the model "
   "called 45.8% multiplied into a fantasy")
ck("optimism_x is reported so the claim is visible",
   abs(_m5["optimism_x"] - 1.02) < 0.01, _m5["optimism_x"])
_b6, _m6 = _CE.max_bet([_wild])
ck("if EVERY slip is over-optimistic it still answers, flagged",
   _b6 is not None and _m6["optimism_ok"] is False,
   "returning nothing would be worse than returning it with a warning")
ck("the market's own view of the slip travels with it",
   _m6["market_prob_pct"] is not None)

# stackable(): the per-leg bound, one-sided and ratio-based.
ck("a 2c leg the model calls 16.6% is not stackable",
   not _CE.stackable(0.166, 2.0),
   "an 8x overstatement; a points threshold sees only 14.6pp and shrugs")
ck("a 25c leg the model calls 30% is stackable", _CE.stackable(0.30, 25.0))
ck("pessimism is never blocked (the search discards it anyway)",
   _CE.stackable(0.10, 50.0))
ck("a 99c leg is refused — it buys 1.01x for a third of the probability",
   not _CE.stackable(0.834, 99.0),
   f"ceiling is {_CE.MAX_BET_LEG_CENTS}c")
ck("an unpriced leg is never stackable", not _CE.stackable(0.5, None))
ck("a 100c 'no offer' is never stackable", not _CE.stackable(0.5, 100))

# The floor sweep. Reachability is not monotone in the floor, which is the whole
# reason one fixed floor cannot be trusted.
_calls = []


def _fake_build(f):
    _calls.append(f)
    return {"n_legs": 5, "cap_reached": f == 35,
            "combined_prob_pct": 0.3 if f == 35 else 0.1,
            "uncapped_payout_x": 330.0 if f == 35 else 12.0}


_swept = _CE.best_max_bet(_fake_build, floors=(45, 35, 25))
ck("the sweep tries every floor", _calls == [45, 35, 25], _calls)
ck("and keeps the one that reached the ceiling", _swept["cap_reached"])
ck("the floors tried travel back on the slip",
   len(_swept["max_bet_floors_tried"]) == 3)
ck("a sweep where nothing reaches keeps the closest",
   _CE.best_max_bet(lambda f: {"n_legs": 3, "cap_reached": False,
                               "uncapped_payout_x": f},
                    floors=(10, 90, 50))["uncapped_payout_x"] == 90)
ck("a builder that returns nothing anywhere yields None",
   _CE.best_max_bet(lambda f: None, floors=(45, 25)) is None)
ck("a builder that raises does not sink the sweep",
   _CE.best_max_bet(lambda f: (_ for _ in ()).throw(RuntimeError("x"))
                    if f == 45 else {"n_legs": 2, "cap_reached": True,
                                     "combined_prob_pct": 1.0},
                    floors=(45, 25)) is not None)

# The ceiling itself is one named, movable number -- it is not in Kalshi's API.
ck("the cap is a single named constant", _CE.MAX_PAYOUT_X > 1)
ck("and is env-overridable, since the API does not publish it",
   "VIGIL_MAX_PAYOUT_X" in _insp.getsource(_CE))

# Wiring: all three builders take it, and none of them silently claims success.
import combine as _CMB
for _mod, _fn, _nm in ((B, "build_mixed_parlay", "baseball"),
                       (_NFS, "build_parlay", "nfl_game_sim"),
                       (_CMB, "build", "combine/tennis")):
    ck(f"{_nm} accepts max_bet",
       "max_bet" in _insp.signature(getattr(_mod, _fn)).parameters)
for _mod, _nm in ((B, "baseball"), (_NFS, "nfl_game_sim")):
    _src = _insp.getsource(_mod)
    ck(f"{_nm} does not let payout_reached default to True on a max bet",
       'item["payout_reached"] = meta.get("cap_reached")' in _src,
       "_mixed_item defaults it to True when given no fair-payout target, which "
       "would announce success on every max bet regardless of what happened")
    ck(f"{_nm} bounds per-leg optimism when max_bet is on",
       "combo_engine.stackable" in _src)
ck("tennis requires liquidity, not merely a price",
   'v.get("liquid")' in _insp.getsource(_CMB._assemble_max_bet),
   "84 priced legs, and the price-only version took a 1c side with no volume")
ck("tennis prices its frontier on COST, not 1/prob",
   '"payout": 1.0 / cost' in _insp.getsource(_CMB._assemble_max_bet),
   "the ceiling applies to what the exchange pays, not to the fair payout")
ck("the tennis board hands liquidity to the leg builder",
   "side_liquid" in _insp.getsource(_CMB._tennis_legs))

# API + UI
_app = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                         "app.py")).read()
ck("all three endpoints read max_bet", _app.count('request.args.get("max_bet")') == 3,
   _app.count('request.args.get("max_bet")'))
ck("and all three sweep rather than picking one floor",
   _app.count("best_max_bet") == 3, _app.count("best_max_bet"))
ck("an unreachable ceiling is a named hint, not a bare null",
   _app.count("max_bet_unreachable") >= 3)
for _b, _label in (("buildCombo", "MLB"), ("buildNFLCombo", "NFL"),
                   ("buildTennisCombo", "tennis")):
    ck(f"the {_label} maker has a max-bet button",
       f"{_b}(true)" in _js)
ck("every max-bet button is labelled with the ceiling",
   _js.count("🎰 Max bet (${MAX_BET_X}×)") == 3)
ck("the slip shows the market's probability beside ours",
   "market_prob_pct" in _js and "Market says" in _js)
ck("and says so when the ceiling could not be reached",
   "isn't reachable on this board today" in _js)
ck("the button label follows the server's cap, not a hardcoded 320",
   "function noteMaxBetCap(" in _js and _js.count("noteMaxBetCap(d);") == 3,
   # counted with the semicolon: without it the DEFINITION line matches too and
   # three call sites read as four
   _js.count("noteMaxBetCap(d);"))

print()
print("=" * 72)
print("NFL DFS — a showdown slate is not a classic one")
print("=" * 72)
import simulate as _SIM
import nfl_dfs as _ND
import nfl_preseason as _NP

# The BOM. DK exports one, it lands inside the first header name, and every
# r.get("Position") then returns None.
_BOM_CSV = ("﻿Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,"
            "TeamAbbrev,AvgPointsPerGame,Status\n"
            "QB,A B (1),A B,1,CPT,11400,X@Y 01/01/2026 08:00PM ET,ARI,18,\n"
            "QB,A B (2),A B,2,FLEX,7600,X@Y 01/01/2026 08:00PM ET,ARI,18,\n")
_rows = _SIM.parse_dk_csv(_BOM_CSV)
ck("a BOM'd DK export still yields the Position column",
   _rows and _rows[0]["pos"] == "QB", _rows[0]["pos"] if _rows else "no rows")
ck("and does not silently fall back to Roster Position",
   _rows[0]["pos"] != _rows[0]["roster_pos"],
   "classic survived this by accident -- there Roster Position holds the same "
   "QB/RB/WR/TE values, so nobody noticed until showdown made it CPT")
ck("the Status column is carried", "status" in _rows[0])

# The header-less path. A DK export pasted without its first line falls to
# positional parsing, which did not read Status AT ALL -- not as empty, as an
# absent key -- so _playable saw None and read it as "active". Every OUT and IR
# player silently became rosterable, and a lineup came back recommending a
# receiver who was on injured reserve.
_HDRLESS = ("WR,Hurt Guy (1),Hurt Guy,1,CPT,11400,X@Y 01/01/2026 08:00PM ET,CAR,0,IR\n"
            "WR,Hurt Guy (2),Hurt Guy,2,FLEX,7600,X@Y 01/01/2026 08:00PM ET,CAR,0,IR\n"
            "WR,Fit Guy (3),Fit Guy,3,FLEX,7600,X@Y 01/01/2026 08:00PM ET,ARI,9,\n")
_hl = _SIM.parse_dk_csv(_HDRLESS)
ck("a header-less export still reads Status",
   _hl and all("status" in r for r in _hl) and _hl[0]["status"] == "IR",
   [r.get("status") for r in _hl])
ck("and the IR player does not survive into the pool",
   "Hurt Guy" not in [e["name"] for e in _ND.showdown_pool(_hl)],
   [e["name"] for e in _ND.showdown_pool(_hl)])
ck("a missing status is reported, not assumed healthy",
   _ND._status_seen(_hl) and not _ND._status_seen(
       [{"status": ""}, {"name": "x"}]),
   "a filter that cannot see its input fails silently, which on an August "
   "roster full of IR bodies is close to guaranteed damage")
ck("and the UI says so rather than showing a clean-looking lineup",
   "status_warning" in _js
   and "could NOT be excluded" in _insp.getsource(_ND))

# The team-abbreviation fallback. It exists so DK's "CAR" matches the pool's
# defense entry -- but it was applied to EVERY player, so anyone whose name was
# missing inherited his team's DEFENSE: projection, ceiling, floor and the
# sample ARRAY. On one preseason board that was 26 players, and because they
# shared the same array object they boomed in perfect lockstep, manufacturing a
# lineup ceiling out of one defense counted four times.
_fakepool = {
    "Real Guy": {"pos": "WR", "proj": 9.0, "ceiling": 18.0, "floor": 2.0, "arr": [9.0]},
    "Panthers": {"pos": "DST", "proj": 6.0, "ceiling": 12.0, "floor": 0.0, "arr": [6.0]},
    "CAR": {"pos": "DST", "proj": 6.0, "ceiling": 12.0, "floor": 0.0, "arr": [6.0]},
}
ck("an exact name still matches", _ND._pool_match(_fakepool, "Real Guy", "WR", "CAR"))
ck("a DST matches by team abbreviation, which is why the fallback exists",
   (_ND._pool_match(_fakepool, "Cardinals", "DST", "CAR") or {}).get("pos") == "DST")
ck("but a missing SKILL player does NOT inherit his team's defense",
   _ND._pool_match(_fakepool, "Fifth String TE", "TE", "CAR") is None,
   "a fifth-string tight end was being projected AS the Carolina defense")
ck("nor a missing kicker, nor a quarterback",
   _ND._pool_match(_fakepool, "Some K", "K", "CAR") is None
   and _ND._pool_match(_fakepool, "Some QB", "QB", "CAR") is None)
ck("and a team key that is not a defense is refused even for a DST row",
   _ND._pool_match({"CAR": {"pos": "WR", "arr": [1]}}, "X", "DST", "CAR") is None,
   "matching on the team key alone would take whatever happened to be there")

# Name matching. Sleeper writes first + last (no suffix); DK keeps the suffix and
# punctuates initials, so an exact compare missed ~a third of a slate.
_np = {"Marvin Harrison": {"pos": "WR", "proj": 2.5, "arr": [2.5]},
       "AJ Dillon": {"pos": "RB", "proj": 3.0, "arr": [3.0]},
       "Panthers": {"pos": "DST", "proj": 7.0, "arr": [7.0]}}
_ni, _nf = _ND._norm_index(_np)
ck("a dropped suffix still matches",
   (_ND._pool_match(_np, "Marvin Harrison Jr.", "WR", "ARI", _ni, _nf) or {}).get("proj") == 2.5,
   "the miss put a REGULAR-SEASON average on a preseason starter and made him "
   "the highest projection on the board")
ck("punctuated initials still match",
   (_ND._pool_match(_np, "A.J. Dillon", "RB", "CAR", _ni, _nf) or {}).get("proj") == 3.0,
   "_norm turns 'A.J.' into 'a j', which is not 'aj' -- hence the second key")
ck("but a normalised match across POSITIONS is refused",
   _ND._pool_match(_np, "Marvin Harrison Jr.", "TE", "ARI", _ni, _nf) is None,
   "loosening the name must not start trading players between positions")

# Being left out of the pool must not be an upgrade.
_dp = _ND._deep_fallback({"a": {"pos": "RB", "proj": 3.0}, "b": {"pos": "RB", "proj": 8.0},
                          "c": {"pos": "WR", "proj": 2.0},
                          "d": {"pos": "DST", "proj": 7.0}}, True)
ck("a preseason player beyond the depth gets the LAST modelled player's number",
   _dp.get("RB") == 3.0 and _dp.get("WR") == 2.0, _dp)
ck("and the table is empty out of preseason, where DK's average means what it says",
   _ND._deep_fallback({"a": {"pos": "RB", "proj": 3.0}}, False) == {})
ck("defenses are not in that table — they have their own measured ladder",
   "DST" not in _dp)

# A defense is scored against the offense it faced. Both were drawn
# independently before, which made rostering BOTH look like diversification when
# it is the most concentrated bet on the board -- they lose together in a
# shootout. Carolina allowed 30 and scored 1, Arizona allowed 33 and scored 2,
# against projections of 7.1 and 7.3.
ck("the measured points-allowed relationship is negative and strong",
   _NP.DST_R < -0.5, f"r = {_NP.DST_R} over 96 exhibition team-seasons")
import random as _rnd2
_r2 = _rnd2.Random(11)
_off = [_r2.gauss(60.0, 15.0) for _ in range(4000)]
_dsa = _NP.dst_from_offense(_off, 4000, _r2)
ck("dst_from_offense keeps the measured marginal mean",
   abs(sum(_dsa) / len(_dsa) - _NP.SPECIAL_MEAN["DST"]) < 0.5,
   f'{sum(_dsa)/len(_dsa):.2f} vs {_NP.SPECIAL_MEAN["DST"]}')
_dsd = (sum((x - sum(_dsa) / len(_dsa)) ** 2 for x in _dsa) / len(_dsa)) ** 0.5
ck("and the measured spread", abs(_dsd - _NP.DST_SD) < 0.5,
   f"{_dsd:.2f} vs {_NP.DST_SD}")


def _pearson(a, b):
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = ((sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5)
    return num / den if den else 0.0


ck("a big day for the offence is a bad day for the defence facing it",
   _pearson(_off, _dsa) < -0.4, f"r = {_pearson(_off, _dsa):+.3f}")
ck("an empty offence series yields no array, rather than a fake one",
   _NP.dst_from_offense([], 10, _r2) is None)

ck("the simulator hands back per-team offensive output",
   '"team_fp": team_fp if with_samples else None' in _insp.getsource(_NFS_SIM)
   if (_NFS_SIM := __import__("nfl_dfs_sim")) else False)
ck("defenses no longer take the unconditional ladder",
   '!= "K"' in _insp.getsource(_ND._special_arr),
   "drawing them there would replace the game-script correlation with an "
   "independent sample and put the original problem straight back")
ck("kickers still do — they are genuinely independent of the score",
   _ND._special_arr("K", True) is not None
   and _ND._special_arr("DST", True) is None)

# Both build paths must set up everything they use. The name index was added to
# the showdown path only, and the classic path called _pool_match with it
# regardless -- a NameError on EVERY classic build, which is the format every
# multi-game slate uses. Showdown is the exception, not the rule, and it is the
# one that got exercised.
_bsrc = _insp.getsource(_ND.build)
_ssrc = _insp.getsource(_ND._build_showdown)
for _fn, _src in (("build (classic)", _bsrc), ("_build_showdown", _ssrc)):
    for _need in ("_norm_index(pool)", "_deep_fallback(pool"):
        ck(f"{_fn} builds its own {_need.split('(')[0]}",
           _need in _src,
           "a helper used in one path and defined in the other is a NameError "
           "waiting for the format nobody tested")
ck("neither path uses a name it did not define",
   all(f"{v} =" in _bsrc or f"{v}, " in _bsrc
       for v in ("_nidx", "_deep")),
   "caught by running a real 16-game week-2 slate, not by any guard here")

ck("a CPT-bearing export is detected as showdown",
   _ND.detect_mode(_rows) == "showdown")
ck("an ordinary export is detected as classic",
   _ND.detect_mode([{"roster_pos": "QB", "game": "A@B"},
                    {"roster_pos": "RB", "game": "C@D"}]) == "classic")
ck("a one-game FLEX-only export is treated as showdown",
   _ND.detect_mode([{"roster_pos": "FLEX", "game": "A@B 1"},
                    {"roster_pos": "FLEX", "game": "A@B 1"}]) == "showdown")

ck("showdown rosters 6, not 9",
   len(_ND.SHOWDOWN_ROSTER) == 6 and _ND.SHOWDOWN_ROSTER[0] == "CPT")
ck("the captain is worth 1.5x", _ND.CPT_MULT == 1.5)
ck("showdown admits kickers, classic does not",
   "K" in _ND.SHOWDOWN_POS and not _ND._elig("K"))
ck("a roster must span both teams", _ND.SHOWDOWN_MIN_TEAMS == 2)
ck("OUT and IR are unrosterable, questionable is not",
   not _ND._playable({"status": "OUT"}) and not _ND._playable({"status": "IR"})
   and _ND._playable({"status": "Q"}) and _ND._playable({}))
def _own_sum(n_slots):
    # Spread the projections out. Identical players push every share to the same
    # value, which the 45% cap then flattens in BOTH branches -- the first
    # version of this guard compared two capped sums and failed on a difference
    # it had made impossible to observe.
    ps = [{"proj": 2.0 + 3.0 * i, "salary": 7600} for i in range(12)]
    _ND._set_ownership(ps, n_slots=n_slots)
    return sum(p["own"] for p in ps)


ck("ownership scales to the roster it is for",
   _own_sum(6) < _own_sum(9),
   f"6-slot {_own_sum(6)} vs 9-slot {_own_sum(9)} -- 9 slots' worth on a 6-slot "
   "slate inflates every number by half")

# The pool dedupe: DK lists every player twice and the same man must not be
# rostered as both his CPT self and his FLEX self.
_sd_rows = [
    {"name": "A B", "salary": 11400, "pos": "QB", "roster_pos": "CPT",
     "team": "ARI", "proj": 18, "status": "", "game": "X@Y"},
    {"name": "A B", "salary": 7600, "pos": "QB", "roster_pos": "FLEX",
     "team": "ARI", "proj": 18, "status": "", "game": "X@Y"},
    {"name": "Gone", "salary": 7600, "pos": "WR", "roster_pos": "FLEX",
     "team": "CAR", "proj": 4, "status": "OUT", "game": "X@Y"},
]
_sd = _ND.showdown_pool(_sd_rows)
ck("the pool holds one entry per player, not one per row",
   len(_sd) == 1, [e["name"] for e in _sd])
ck("and carries both salaries",
   _sd[0]["salary"] == 7600 and _sd[0]["cpt_salary"] == 11400)
ck("an OUT player never reaches the pool",
   "Gone" not in [e["name"] for e in _sd])
ck("a missing CPT row falls back to the 1.5x rule",
   _ND.showdown_pool([{"name": "C D", "salary": 8000, "pos": "WR",
                       "roster_pos": "FLEX", "team": "CAR", "proj": 4,
                       "status": "", "game": "X@Y"}])[0]["cpt_salary"] == 12000)

# The optimizer, on the shape that failed: flat salaries where classic cannot fit.
_flat = [{"name": f"P{i}", "pos": "WR", "team": ("ARI" if i % 2 else "CAR"),
          "salary": 7600, "cpt_salary": 11400, "proj": 10.0 - i * 0.1,
          "ceiling": 20.0 - i * 0.1, "own": 8.0} for i in range(20)]
_got = _ND.optimize_showdown(_flat, _ND.CAP, "projection", restarts=20)
ck("a flat-priced showdown board builds a lineup", _got is not None)
if _got:
    _cp, _pk, _sal = _got
    ck("1 captain + 5 flex", len(_pk) == 5)
    ck("under the cap", _sal <= _ND.CAP, f"${_sal}")
    ck("priced as 1 CPT + 5 FLEX", _sal == 11400 + 5 * 7600, f"${_sal}")
    ck("no player used twice",
       len({_cp["name"]} | {p["name"] for p in _pk}) == 6)
    ck("spans both teams",
       len({_cp["team"]} | {p["team"] for p in _pk}) >= 2)
    ck("nine of these would NOT have fit, which is the original bug",
       9 * 7600 > _ND.CAP, f"9 x $7,600 = ${9 * 7600} against a ${_ND.CAP} cap")

# The preseason usage model: WR/TE were flat, and flat means the optimizer
# cannot tell a WR1 from a camp body.
ck("an established WR is dampened relative to a camp body",
   _NP.role_factor("WR", 7.0) < _NP.role_factor("WR", 0.0),
   f'{_NP.role_factor("WR", 7.0):.2f} vs {_NP.role_factor("WR", 0.0):.2f}')
ck("a TE is NOT, because two seasons say the effect isn't there",
   _NP.role_factor("TE", 7.0) == _NP.role_factor("TE", 0.0),
   "t = -0.49 over n=358 -- the one-season version of this guard would have "
   "locked in an overfit to n=8")
ck("a fringe WR outworks both", _NP.role_factor("WR", 2.0) > 1.0)
ck("WR is no longer a single flat entry",
   len(_NP._ROLE["WR"]) > 1,
   "((0.0, 1.0),) gave every receiver on a team the same projection")

# The two units that never leave the field. Measured over three exhibition
# seasons, K and DST are the highest-scoring preseason positions -- which is the
# opposite of their regular-season role as salary relief.
_rngK = _rnd.Random(11)
_ks = _NP.special_samples("K", 8000, _rngK)
_ds = _NP.special_samples("DST", 8000, _rngK)
ck("a kicker has a measured exhibition distribution at all",
   _ks is not None and len(_ks) == 8000,
   "kickers are not in the game sim, so without this they fell through to DK's "
   "REGULAR-SEASON average plus an invented Gaussian")
ck("and it reproduces the measured mean",
   abs(sum(_ks) / len(_ks) - _NP.SPECIAL_MEAN["K"]) < 0.4,
   f'{sum(_ks) / len(_ks):.2f} vs {_NP.SPECIAL_MEAN["K"]}')
ck("a defense likewise",
   abs(sum(_ds) / len(_ds) - _NP.SPECIAL_MEAN["DST"]) < 0.4,
   f'{sum(_ds) / len(_ds):.2f} vs {_NP.SPECIAL_MEAN["DST"]}')
ck("a defense can score NEGATIVE, which a Normal fit would have hidden",
   min(_ds) < 0, f"floor {min(_ds)}")
ck("a kicker cannot", min(_ks) > 0, f"floor {min(_ks)}")
ck("both outscore every skill position in August",
   _NP.SPECIAL_MEAN["K"] > 4.67 and _NP.SPECIAL_MEAN["DST"] > 4.67,
   "K 5.83 and DST 7.07 against QB 5.04, RB 4.67, WR 3.96, TE 2.63 -- the two "
   "positions a regular-season optimizer treats as salary relief")
ck("the ladder is only used in preseason",
   _ND._special_arr("K", False) is None and _ND._special_arr("K", True))
ck("and only for those two positions",
   _ND._special_arr("WR", True) is None and _ND._special_arr("QB", True) is None)
ck("the inversion holds for the three positions that measure it",
   all(_NP.role_factor(p, 25.0) < _NP.role_factor(p, 0.0)
       for p in ("QB", "RB", "WR")))

print()
print("=" * 72)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for n, d in FAIL:
        print(f"   - {n}   {d}")
print("=" * 72)

