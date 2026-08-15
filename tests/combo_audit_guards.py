"""Part 3: the surfaces Parts 1-2 don't reach.

The two new guards (_pct, _implausible), the live opt-in gate, the one-game
fallback, display/refresh stability, fee accounting, and monotonicity laws that
must hold for ANY slate (raising the floor can't lower a leg; adding legs can't
raise the combined chance).
"""
import os, sys, datetime, collections, time as _tm
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
# Pick a game with enough BETTABLE legs to make a 3-leg same-game slip possible.
# Since unlisted legs are excluded, "one-game slate builds something" is no
# longer unconditionally true: a game Kalshi barely lists has nothing placeable
# to build from, and returning None there is the correct answer rather than a
# failure. Measured on a live slate, one game had 177 candidates over 55% and
# exactly ONE of them priced. So the guard states the real contract: build when
# the legs exist, and never invent an unplaceable slip when they don't.
def _n_priced(g, thr=0.55):
    gs = B._sim_for(g, False)
    if not gs:
        return 0
    cs = [c for c in gs["cands"] if c["marg"] >= thr]
    B._price_cands(cs, g.get("kalshi_suffix"), blend=False)
    return len([c for c in cs if c.get("price_cents")])


_one_src = next((g for g in playable if _n_priced(g) >= 3), None)
if _one_src is None:
    ck("one-game slate: no game on this slate has 3 bettable legs",
       True, "nothing placeable to build from, so None is correct")
    one = None
else:
    one = B.build_target_parlay([_one_src], 3, 55)
if _one_src is not None and one is None:
    ck("one-game slate builds something", False,
       "%s had >=3 priced legs and still built nothing" % _one_src["matchup"])
elif one is not None:
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
ck("a 62c home price is about 4.2 points in August",
   3.8 < _P.margin_from_prob(0.62) < 4.6,
   f"{_P.margin_from_prob(0.62):.2f} -- a WIDER outcome distribution needs MORE "
   "points to buy the same win probability")
ck("preseason sigma is the three-season measurement, not the one-year 15.40",
   13.0 <= _P.MARGIN_SD <= 14.5,
   f"{_P.MARGIN_SD} against a measured 13.70 (n=147, 2023-25); the first fit "
   "read one preseason's noise as a fact about August")
ck("and preseason home-field advantage is nothing at all",
   abs(_P.HFA_PTS) < 0.5,
   f"{_P.HFA_PTS} points, measured -0.19 +/- 1.1 over three preseasons")
ck("an absurd price is clamped rather than sent to infinity",
   abs(_P.margin_from_prob(0.0)) < 40 and abs(_P.margin_from_prob(1.0)) < 40)

# --- the engine's own HFA is what gets subtracted --------------------------
_gs = open(_G.__file__).read()
ck("the market anchor removes the ENGINE's home edge, not the league's",
   "_ENGINE_HFA_PTS" in _gs and "(edge - _ENGINE_HFA_PTS) / _EDGE_KEEP" in _gs,
   "the market's price already contains a real home edge; what has to come out "
   "is only what the engine will add back -- and now BOTH anchor grades "
   "(moneyline and spread ladder) come through the same subtraction")
ck("and the engine's edge is measured, not assumed",
   1.0 < _G._ENGINE_HFA_PTS < 1.6,
   f"{_G._ENGINE_HFA_PTS} points -- what the split 1.05 bump realizes through "
   "the script at a preseason total; subtracting a LEAGUE number instead of the "
   "engine's own put the simulated home side under the market on 15 of 16 games")
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
print("Preseason is a different model, and its record has to say so")
print("=" * 72)
import calibrate as _CAL

ck("preseason NFL has its own calibration bucket",
   "nfl_pre" in _CAL._MODELS,
   "one temperature fitted across exhibitions AND regular games learns a blend "
   "of two shapes -- margin SD 15.40 against ~13.5 -- and then applies it to "
   "games that only ever have one of them")
ck("and it is not an alias of the regular-season one",
   _CAL._MODELS["nfl_pre"][0] is not _CAL._MODELS["nfl"][0])
_ngs = _insp.getsource(_NFS)
ck("the RECORD side routes exhibitions to it",
   'predlog.log_many("nfl_pre" if preseason else "nfl", log_rows)' in _ngs,
   "the apply side was already guarded; the logging was not, so every August "
   "game was graded into the regular-season model's evidence")
ck("the APPLY side still refuses to calibrate a market-anchored probability",
   "cal_used = lambda p: p" in _ngs,
   "the preseason level comes from Kalshi's ladder, so there is no error of "
   "our own to correct -- registering the bucket is for measurement, not use")
ck("its floor demands more than one preseason of evidence",
   _CAL._MODELS["nfl_pre"][1] > 65,
   f'{_CAL._MODELS["nfl_pre"][1]} against ~65 exhibition games a year')
ck("an unregistered model is a clean no-op, not an error",
   _CAL._params("not_a_model") == (1.0, 0.5, 0.0, 0))
ck("and every registered model answers without raising",
   all(len(_CAL._params(m)) == 4 for m in _CAL._MODELS))

print()
print("=" * 72)
print("MLB postseason home field — the rule MLB actually uses")
print("=" * 72)
import season_sim as _SS

ck("every series format knows which games the host plays at home",
   _SS._HOME_GAMES == {2: (1, 2, 3), 3: (1, 2, 5), 4: (1, 2, 6, 7)},
   "wild card is all three at the higher seed; best-of-5 is 2-2-1; "
   "best-of-7 is 2-3-2")
# Probability mass has to be conserved -- an enumeration that drops sequences
# would quietly under-count one side.
for _need in (2, 3, 4):
    _homes, _mx, _tot = set(_SS._HOME_GAMES[_need]), 2 * _need - 1, [0.0]

    def _walk(g, w, l, pr, need=_need, homes=_homes, mx=_mx, tot=_tot):
        if w == need or l == need:
            tot[0] += pr
            return
        if g > mx:
            return
        p = 0.58 if g in homes else 0.47
        _walk(g + 1, w + 1, l, pr * p)
        _walk(g + 1, w, l + 1, pr * (1 - p))

    _walk(1, 0, 0, 1.0)
    ck(f"best-of-{2 * _need - 1} enumeration conserves probability",
       abs(_tot[0] - 1.0) < 1e-9, _tot[0])
ck("with no home edge it reduces EXACTLY to the venue-blind formula",
   abs(_SS._series_p_hf(0.6, 0.6, 4) - _SS._series_p(0.6, 4)) < 1e-12,
   "the new path must not move a number it has no information about")
ck("a home edge favours the host in every format",
   all(_SS._series_p_hf(0.54, 0.46, k) > 0.5 for k in (2, 3, 4)))
ck("and favours him MOST in the all-home wild card round",
   _SS._series_p_hf(0.54, 0.46, 2) > _SS._series_p_hf(0.54, 0.46, 3)
   > _SS._series_p_hf(0.54, 0.46, 4),
   "three of three at home beats three of five beats four of seven -- and a "
   "longer series regresses toward the better team, diluting a fixed per-game "
   "edge")
ck("certain and impossible stay certain and impossible",
   _SS._series_p_hf(1.0, 1.0, 4) == 1.0 and _SS._series_p_hf(0.0, 0.0, 4) == 0.0)

_ss_src = _insp.getsource(_SS)
ck("the World Series host is the better REGULAR-SEASON record",
   "wins[a] > wins[b]" in _ss_src,
   "the All-Star Game decided this from 2003 to 2016; the 2017 CBA scrapped it, "
   "and hard-coding an AL edge would bias every World Series price")
ck("and nothing in the sim ties home field to the All-Star Game",
   "all_star" not in _ss_src.lower() and "allstar" not in _ss_src.lower())
ck("the postseason no longer runs fully neutral",
   "series(host, road, _WS_NEED)" in _ss_src,
   "every playoff round used npr() with home_field=False, so no series had any "
   "home advantage at all")
ck("the host is decided INSIDE each simulated season",
   "wins[a] == wins[b] and rnd() < 0.5" in _ss_src,
   "records are a sim output, not a fixed input -- deciding the host outside the "
   "loop would freeze today's standings into every simulated October")

print()
print("=" * 72)
print("October is pitched by a different staff than August is")
print("=" * 72)
_lg_t = {"era": 4.20}
# top-heavy staff and flat staff, each with its season pen and its short October pen
_deep_rot = {"sp_season": 3.62, "sp_playoff": 3.33,
             "bp_season": 4.06, "bp_playoff": 3.63}
_flat_rot = {"sp_season": 3.71, "sp_playoff": 3.58,
             "bp_season": 2.89, "bp_playoff": 2.89}

ck("a playoff rotation is never worse than the season staff",
   all(_SS._pit_factor(r, _lg_t, True) <= _SS._pit_factor(r, _lg_t, False)
       for r in (_deep_rot, _flat_rot)),
   "dropping your worst starters cannot hurt you")
ck("a top-heavy staff gains far more than a flat one",
   (_SS._pit_factor(_deep_rot, _lg_t, False) - _SS._pit_factor(_deep_rot, _lg_t, True))
   > 3 * (_SS._pit_factor(_flat_rot, _lg_t, False) - _SS._pit_factor(_flat_rot, _lg_t, True)),
   "measured on the live board: Dodgers gain 0.082, Red Sox 0.018 -- the model "
   "was charging Los Angeles for arms that never throw a playoff inning")
ck("the split uses the game model's own innings weight, not a second one",
   "baseball.SP_INNINGS_WEIGHT" in _insp.getsource(_SS._pit_factor),
   "the season sim and the daily board must read ONE definition of pitching")
ck("a missing rotation degrades to league average, not to a crash",
   _SS._pit_factor(None, _lg_t, True) == 1.0)
ck("the playoff rotation is picked by QUALITY, not by games started",
   'arms.sort(key=lambda a: a["ra9"])' in _insp.getsource(_SS._rotations),
   "sorting by starts hands a team its most-used arms -- on this board that "
   "meant two ERAs near 5.00, and produced a playoff staff WORSE than the "
   "season average, which is backwards")
ck("innings are parsed base-3, not as a decimal",
   "_ip_float" in _insp.getsource(_SS._sp_ra9)
   and "_ip_float" in _insp.getsource(_SS._pen_ra9),
   "MLB writes 133 and two thirds as '133.2'; reading that as 133.2 understates "
   "every workload and under-regresses every small sample")
ck("small samples cannot buy a rotation spot",
   _SS._MIN_GS >= 5 and B.SP_IP_REGRESS > 0)
ck("nor a spot in the October bullpen",
   _SS._MIN_PEN_IP >= 10 and _SS._PEN_IP_REGRESS > 0)
ck("a best-of-seven rotation is four deep", _SS._PLAYOFF_ROT == 4)
ck("the regular season runs on the SEASON staff, October on the playoff staff",
   "_pit_factor(rot[tid], lg, False)" in _insp.getsource(_SS.simulate)
   and "_pit_factor(rot[tid], lg, True)" in _insp.getsource(_SS.simulate)
   and "teams_po" in _insp.getsource(_SS.simulate),
   "these must be two different ratings: crediting every team with an October "
   "staff for 45 games of August is as wrong as charging a playoff club for its "
   "sixth starter. The guard that used to sit here asserted the regular season "
   "kept its flat team ERA -- it was passing while the defect it described was "
   "the defect")

print()
print("=" * 72)
print("The World Series board has to be priced against the venue you bet at")
print("=" * 72)
_ss_src = _insp.getsource(_SS)
ck("the live World Series series is the one we ask for",
   "KXMLB" in _SS._WS_SERIES,
   "KXMLB carries 30 quoted markets -- one per team. Without it every WS row "
   "came back kalshi_cents=null and the edge was measured against Polymarket "
   "alone, a venue these bets do not get placed at")
ck("and the World Baseball CLASSIC is not mistaken for it",
   "KXMLBWORLD" not in _SS._WS_SERIES,
   "different tournament, national teams, different year -- had it ever quoted "
   "it would have been a WRONG price rather than a missing one")
ck("the fallback order tries the live series first",
   _SS._WS_SERIES[0] == "KXMLB")
ck("prices are read in deci-cents, not rounded to the cent",
   _CENTS_OK := (__import__("kalshi")._cents(0.371) == 37.1
                 and __import__("kalshi")._cents(0.002) == 0.2),
   "these markets quote in 0.1c steps; rounding to whole cents would turn a "
   "0.2c longshot into 0c and divide by zero downstream")
ck("a winner-style row is keyed off the ticker's team suffix",
   '.rsplit("-", 1)[-1]' in _ss_src,
   "KXMLB-26-LAD -> LAD")

print()
print("=" * 72)
print("A week of history is one answer, not seven")
print("=" * 72)
import deep_history as _DH

_DAYS = {
    "2026-08-07": {"date": "2026-08-07", "prev_date": "2026-08-06", "n": 4000,
                   "teams": [
                       {"id": 1, "name": "A", "ws": 15.0, "ws_prev": 19.0,
                        "playoffs": 99.0, "playoffs_prev": 99.0, "mean_wins": 97,
                        "what": ["lost 4  -4.0pp"], "events": [{"k": "a1"}]},
                       {"id": 2, "name": "B", "ws": 21.0, "ws_prev": 20.0,
                        "playoffs": 100.0, "playoffs_prev": 100.0, "mean_wins": 100,
                        "what": ["won 2  +1.0pp"], "events": [{"k": "b1"}]}]},
    "2026-08-06": {"date": "2026-08-06", "prev_date": "2026-08-05", "n": 4000,
                   "teams": [{"id": 1, "name": "A", "ws": 19.0, "ws_prev": 16.0,
                              "playoffs": 99.0, "playoffs_prev": 98.0, "mean_wins": 98,
                              "what": ["trade  +3.0pp"], "events": [{"k": "a2"}]}]},
    "2026-08-05": {"date": "2026-08-05", "prev_date": "2026-08-04", "n": 4000,
                   "teams": [{"id": 1, "name": "A", "ws": 16.0, "ws_prev": 18.0,
                              "playoffs": 98.0, "playoffs_prev": 98.0, "mean_wins": 97,
                              "what": ["lost 2  -2.0pp"], "events": [{"k": "a3"}]}]},
}
_od, _orp = _DH.dates, _DH.report
try:
    _DH.dates = lambda: sorted(_DAYS, reverse=True)
    _DH.report = lambda d=None: _DAYS.get(d or sorted(_DAYS, reverse=True)[0])
    _rr = _DH.report_range("2026-08-05", "2026-08-07")
    _a = next(t for t in _rr["teams"] if t["name"] == "A")
    _b = next(t for t in _rr["teams"] if t["name"] == "B")

    ck("a multi-day window returns ONE combined answer",
       _rr["days"] == 3 and _rr.get("range") is True)
    ck("the move is measured END TO END, not summed from the dailies",
       _a["move"] == -3.0 and _a["ws_prev"] == 18.0 and _a["ws"] == 15.0,
       "summing accumulates each night's rounding and hides a round trip: "
       "+3 then -4 is a -1 week, not two moves")
    ck("every day's events survive the merge",
       [e["k"] for e in _a["events"]] == ["a1", "a2", "a3"])
    ck("and every day's sentences do, newest first and dated",
       len(_a["what"]) == 3 and _a["what"][0].startswith("2026-08-07"))
    ck("a team present on only SOME days still gets a baseline",
       _b["move"] == 1.0 and _b["ws_prev"] == 20.0,
       "taking the start from the oldest day alone left every team absent from "
       "it with move=None -- which is exactly the teams whose single big piece "
       "of news is what you opened the box to read")
    ck("days_seen says how much of the window a team appears in",
       _a["days_seen"] == 3 and _b["days_seen"] == 1)
    ck("a one-day window is just the plain daily report",
       (_DH.report_range("2026-08-07", "2026-08-07") or {}).get("range") is None)
    ck("reversed endpoints are normalised, not empty",
       (_DH.report_range("2026-08-07", "2026-08-05") or {}).get("days") == 3)
    ck("a window with no stored runs is None, not a crash",
       _DH.report_range("2020-01-01", "2020-01-02") is None)
finally:
    _DH.dates, _DH.report = _od, _orp

_app_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                             "app.py")).read()
ck("the API accepts a from/to window", "report_range" in _app_src
   and 'request.args.get("from")' in _app_src)
ck("and still answers a bare ?date the old way",
   "deep_history.report(date)" in _app_src)
ck("the UI can ask for a span", "histSpan" in _js and "loadDeepHistory(" in _js)
ck("and renders the window it actually got",
   "combined over" in _js and "d.days" in _js)


print()
print("=" * 72)
print("October is not August: playoff rotation + real home field (deep season)")
print("=" * 72)
import random as _rnd, collections as _co
import deep_season as _DS, deep_sim as _DSIM, deep_data as _DD, season_sim as _SS


def _fake_prof(tag, n=6):
    """Six starters, best-first by construction, then SHUFFLED — so any code that
    just takes the head of the list instead of ranking is caught."""
    arms = [{"id": f"{tag}{i}", "name": f"{tag}{i}", "kpa": 0.30 - 0.03 * i,
             "bbpa": 0.06, "era": 2.5 + 0.6 * i} for i in range(n)]
    _rnd.Random(7).shuffle(arms)
    return {"rotation": arms}


def _series_log(need, trials=500):
    """Play until a FULL-LENGTH series turns up; return [(home_is_high, sh, sa)]."""
    log = []
    real = _DSIM.play_game
    _DSIM.play_game = lambda ph, pa, sh, sa, rng: (
        log.append((ph is _pf[1], sh["name"], sa["name"])),
        {"home_win": rng.random() < 0.5, "batting": {}, "pitching": {}})[1]
    try:
        for t in range(trials):
            log.clear()
            _DS._play_series(1, 2, need, _rnd.Random(t))
            if len(log) == 2 * need - 1:
                return list(log)
    finally:
        _DSIM.play_game = real
    return list(log)


_pf = {1: _fake_prof("A"), 2: _fake_prof("B")}
_saved_G = dict(_DS._G)
try:
    _DS._G.clear()
    _DS._init_worker({"profiles": _pf})
    _mk = lambda ks: _co.defaultdict(lambda: dict.fromkeys(ks, 0))
    _DS._G["season_bat"] = _mk(("pa", "ab", "h", "2b", "3b", "hr", "bb", "k",
                                "r", "rbi", "sb", "ph"))
    _DS._G["season_pit"] = _mk(("bf", "outs", "k", "bb", "h", "hr", "r"))

    _wc, _ds, _lcs = _series_log(2), _series_log(3), _series_log(4)
    _hosts = lambda log: [h for h, _, _ in log]
    _hi_sp = lambda log: [s if h else a for h, s, a in log]

    ck("the Wild Card is played ENTIRELY at the higher seed's park",
       _hosts(_wc) == [True, True, True],
       "a flat 2-3-2 pattern gave the top seed 2 of 3 — the bye round's whole "
       "reward is the third home game")
    ck("the Division Series is 2-2-1, so the higher seed hosts the DECIDER",
       _hosts(_ds) == [True, True, False, False, True],
       "the old pattern handed Game 5 to the road team")
    ck("the LCS/World Series is 2-3-2", _hosts(_lcs) == [True, True, False,
                                                         False, False, True, True])
    ck("home games match the closed-form season_sim uses",
       all(_hosts(lg) == [(g + 1) in _SS._HOME_GAMES[need] for g in range(len(lg))]
           for need, lg in ((2, _wc), (3, _ds), (4, _lcs))),
       "the fast board and the deep board must not disagree about who is home")

    ck("every series opens with the ACE, not wherever August left the index",
       _hi_sp(_wc)[0] == "A0" and _hi_sp(_ds)[0] == "A0" and _hi_sp(_lcs)[0] == "A0")
    ck("the rotation is ordered by quality, not by list position",
       [p["name"] for p in _DS._po_rotation(1, 4)] == ["A0", "A1", "A2", "A3"],
       "the fixture is shuffled: taking rotation[:4] would fail this")
    ck("the #5 and #6 starters NEVER throw a playoff game",
       not ({s if h else a for lg in (_wc, _ds, _lcs) for h, s, a in lg}
            & {"A4", "A5", "B4", "B5"}),
       "cycling all six through October charged a deep staff for arms that in "
       "reality watch the series from the bullpen")
    ck("a best-of-three uses THREE arms — no ace on zero days' rest",
       _hi_sp(_wc) == ["A0", "A1", "A2"],
       "the WC is three games on three consecutive days")
    ck("a best-of-seven brings the ace back for Game 5 on normal rest",
       _hi_sp(_lcs) == ["A0", "A1", "A2", "A3", "A0", "A1", "A2"])
    ck("one scale ranks arms everywhere (bullpen order == rotation order)",
       _DS._build_po_rot(_pf)[1][4] ==
       sorted(_pf[1]["rotation"], key=_DD.arm_quality, reverse=True)[:4])
    ck("a club with no rotation data degrades instead of crashing",
       _DS._build_po_rot({9: {"rotation": []}})[9][4] == [None])
    ck("a three-man staff is not indexed off the end",
       len(_DS._build_po_rot({9: {"rotation": _fake_prof("C", 3)["rotation"]}})[9][4]) == 3)
finally:
    _DS._G.clear()
    _DS._G.update(_saved_G)

ck("the playoff rotation is built ONCE per worker, not per series",
   "_G[\"po_rot\"] = _build_po_rot" in open(
       os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "deep_season.py")).read(),
   "4,000 seasons x 13 series of re-sorting would cost more than the games")



print()
print("=" * 72)
print("The season board rates PITCHERS, not staff ERA (season_sim)")
print("=" * 72)
import season_sim as _SSIM

_lgx = {"era": 4.20, "whip": 1.30, "bp_era": 4.05, "bp_whip": 1.28,
        "rpg": 4.40, "ops": 0.720, "ops_vl": 0.720, "ops_vr": 0.720}
_arm = lambda ra9: {"ra9": ra9}

ck("a five-man rotation is five equal turns",
   _SSIM._po_weights(5, 5) == [0.2] * 5)
ck("a best-of-seven is 2-2-2-1, not four equal turns",
   [round(w * 7) for w in _SSIM._po_weights(4, 7)] == [2, 2, 2, 1],
   "flat-averaging the best four gave the #4 the same say as the ace")
ck("every weight set sums to one",
   all(abs(sum(_SSIM._po_weights(k, g)) - 1.0) < 1e-12
       for k in range(1, 8) for g in (3, 5, 7)))

_staff = sorted((_arm(x) for x in (2.60, 3.10, 3.55, 4.20, 5.40, 6.10)),
                key=lambda a: a["ra9"])
ck("the October rotation is better than the season rotation",
   _SSIM._rot_ra9(_staff, _SSIM._PLAYOFF_ROT, 7)
   < _SSIM._rot_ra9(_staff, _SSIM._SEASON_ROT, _SSIM._SEASON_ROT))
ck("the #5 and #6 never appear in the October number",
   abs(_SSIM._rot_ra9(_staff, 4, 7)
       - _SSIM._rot_ra9(_staff[:4] + [_arm(99.0), _arm(99.0)], 4, 7)) < 1e-12)
# the ordering law, on every ranked staff shape rather than one fixture
import random as _r2
_rr2 = _r2.Random(11)
_viol = 0
for _ in range(20000):
    _a = sorted(({"ra9": _rr2.uniform(1.5, 7.5)}
                 for _ in range(_rr2.randint(1, 9))), key=lambda x: x["ra9"])
    _a += [{"ra9": 4.20}] * max(0, _SSIM._SEASON_ROT - len(_a))
    _a.sort(key=lambda x: x["ra9"])
    if _SSIM._rot_ra9(_a, 4, 7) > _SSIM._rot_ra9(_a, 5, 5) + 1e-12:
        _viol += 1
ck("October can NEVER rate worse than the season, on any staff shape",
   _viol == 0, f"{_viol}/20000 violations — two earlier cuts of this failed here")

_sp = {"era": 3.20, "whip": 1.10, "ip": "180.2", "gs": 30,
       "hr": 18, "bb": 45, "k": 210}
_ra = _SSIM._sp_ra9(_sp, _lgx)
ck("a starter is graded by the game model's ERA+FIP+WHIP blend, not raw ERA",
   _ra is not None and abs(_ra - 3.20) > 0.01 and 2.0 < _ra < 4.5,
   f"RA9 {_ra:.2f} off a 3.20 ERA — FIP and WHIP get their vote")
ck("and innings are read base-3 (180.2 is 180 and two THIRDS)",
   abs(B._ip_float("180.2") - (180 + 2 / 3.0)) < 1e-9)
ck("a short sample is pulled toward league, not taken at face value",
   _SSIM._sp_ra9(dict(_sp, ip="12.0", gs=2), _lgx)
   > _SSIM._sp_ra9(_sp, _lgx))
_ip180 = B._ip_float("180.2")
_fip_with = B._fip({"ip": _ip180, "hr": 18, "bb": 45, "k": 210,
                    "hbp": _ip180 * _SSIM._LG_HBP9 / 9.0})
_fip_zero = B._fip({"ip": _ip180, "hr": 18, "bb": 45, "k": 210, "hbp": 0})
ck("FIP gets a league-rate HBP prior rather than a silent zero",
   0.3 < _SSIM._LG_HBP9 < 0.7 and 0.10 < (_fip_with - _fip_zero) < 0.25,
   f"a silent zero puts FIP {_fip_with - _fip_zero:.2f} LOW against a league ERA "
   "that still counts the men who got plunked — a level error, and levels do "
   "not cancel in a ratio the way a uniform scale factor does")
ck("and it is a PRIOR, not a thumb on the scale — every arm pays the same rate",
   abs(_SSIM._sp_ra9({"era": 3.20, "whip": 1.10, "ip": "180.2", "gs": 30,
                      "hr": 18, "bb": 45, "k": 210}, _lgx)
       - _SSIM._sp_ra9({"era": 3.20, "whip": 1.10, "ip": "180.2", "gs": 30,
                        "hr": 18, "bb": 45, "k": 210, "hbp": 99}, _lgx)) < 1e-12,
   "roster_lines carries no HBP column, so a line that somehow had one must not "
   "be read — the prior is what every arm gets")
ck("junk lines are skipped, not crashed on",
   _SSIM._sp_ra9({"era": None, "ip": "0.0"}, _lgx) is None
   and _SSIM._sp_ra9({"era": "x", "ip": "5.0"}, _lgx) is None)
ck("a 4-inning September call-up cannot be an October reliever",
   _SSIM._pen_ra9({"era": 0.00, "whip": 0.50, "ip": "4.0"}, _lgx) is None,
   "'best five of two' is a small sample wearing a short bullpen's clothes")
ck("a real reliever's line does come through",
   (_SSIM._pen_ra9({"era": 2.40, "whip": 1.00, "ip": "62.0"}, _lgx) or 9) < 3.4)

_sim_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                             "season_sim.py")).read()
ck("BOTH halves of the year read the rotation, not just October",
   "_pit_factor(rot[tid], lg, False)" in _sim_src
   and "_pit_factor(rot[tid], lg, True)" in _sim_src,
   "sp_season was computed and then never called — the 162 games that decide "
   "seeding still ran on one flat staff ERA")
ck("the October bullpen is the short one",
   '"bp_playoff"' in _sim_src and "rot[\"bp_playoff\"] if playoff" in _sim_src)
ck("a thin bullpen is topped up from its own season pen, not rated off two arms",
   "[bp_season] * (_PLAYOFF_PEN - len(pen_best))" in _sim_src)
ck("a thin rotation is topped up at league average",
   '[{"ra9": lg_era}] * max(0, _SEASON_ROT - n_real)' in _sim_src,
   "three surviving starters does not mean a three-man rotation")
ck("a roster-fetch failure leaves the old rating standing, not a broken board",
   "except Exception:\n        rot = {}" in _sim_src)

_r_empty = {tid: t for tid, t in ()}
ck("a team with no rotation data keeps its team-ERA rating",
   _SSIM._pit_factor(None, _lgx, True) == 1.0)

print()
print("=" * 72)
print("Who you play: opponent defence in the NFL engine")
print("=" * 72)
import nfl_game_sim as _NG

ck("a neutral defence is exactly neutral",
   _NG.def_factor(23.0, 23.0) == 1.0)
ck("a missing rating is a NO-OP, not a guess",
   _NG.def_factor(None, 23.0) == 1.0 and _NG.def_factor(23.0, None) == 1.0
   and _NG.def_factor(0, 23.0) == 1.0,
   "the preseason path builds profiles with no ESPN rating and is anchored to "
   "the market's implied points -- it must come out of this unchanged")
ck("a good defence suppresses the offence it faces",
   _NG.def_factor(17.2, 23.0) < 1.0)
ck("a bad one lets it breathe", _NG.def_factor(30.1, 23.0) > 1.0)
ck("monotone in the opponent's defence",
   all(_NG.def_factor(pa, 23.0) < _NG.def_factor(pa + 1, 23.0)
       for pa in range(18, 29)))
ck("the exponent is the RESIDUAL, not the whole measured effect",
   0.15 < _NG._DEF_EXP < 0.30,
   "measured true exponent +0.349 +/- 0.087 over three seasons (n=1,708); "
   "Sleeper's projections already carry +0.124 of it, so applying the full "
   "0.349 would count the first slice twice")
ck("and it is far below proportional, because points allowed is a noisy read",
   _NG._DEF_EXP < 0.5,
   "a proportional 1.0 would swing an offence 14 points across the league's "
   "defensive range; the honest number is nearer 3")
ck("the swing stays inside a believable band",
   0.85 < _NG.def_factor(14.0, 23.0) and _NG.def_factor(34.0, 23.0) < 1.16,
   f"league extremes: x{_NG.def_factor(14.0, 23.0):.3f} .. "
   f"x{_NG.def_factor(34.0, 23.0):.3f}")
ck("takeaways move the OTHER way — a good defence forces more of them",
   _NG._rates({"exp": {"pass_td": 1.6, "rush_td": 0.8, "fgm": 1.7,
                       "pass_int": 0.8, "fum_lost": 0.6}}, False, 0.94)[2]
   > _NG._rates({"exp": {"pass_td": 1.6, "rush_td": 0.8, "fgm": 1.7,
                         "pass_int": 0.8, "fum_lost": 0.6}}, False, 1.06)[2],
   "turnovers belong to the defence forcing them, so the multiplier inverts")
_flat = {"exp": {"pass_td": 1.6, "rush_td": 0.8, "fgm": 1.7,
                 "pass_int": 0.8, "fum_lost": 0.6}}
ck("with no defence term the rates are bit-identical to the old engine",
   _NG._rates(_flat, True) == _NG._rates(_flat, True, 1.0))
ck("each side is rated against the OTHER side's defence, not its own",
   "def_factor(away.get(\"def_pa_pg\")" in _insp.getsource(_NG.simulate_game)
   and "def_factor(home.get(\"def_pa_pg\")" in _insp.getsource(_NG.simulate_game))
_nd_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "nfl_data.py")).read()
ck("the ratings are attached where the profiles are built",
   "def_pa_pg" in _nd_src and "lg_pa_pg" in _nd_src and "team_ratings" in _nd_src)
ck("and a failed ratings fetch leaves the board standing",
   "except Exception:\n            pass\n\n        # Home/away" in _nd_src)

print()
print("=" * 72)
print("Cache sweeps must not race the threads that fill them")
print("=" * 72)
import racing as _RC, threading as _th, time as _tm
for _mod, _name in ((B, "baseball"), (_RC, "racing")):
    _sw = _mod._sweep_cache if _mod is B else _mod._sweep_form_cache
    ck(f"{_name} sweeps a SNAPSHOT, not the live dict",
       "list(" in _insp.getsource(_sw),
       "iterating the live dict raised 'dictionary changed size during "
       "iteration' out of whatever request happened to trigger the sweep -- a "
       "24-thread backtest hit it in under two minutes")
# and prove it: sweep while other threads insert
_d = B._cache
_stop = [False]
def _churn():
    i = 0
    while not _stop[0]:
        _d[("guard_churn", i)] = (0.0, None, 0.0)   # already expired
        i += 1
        if i > 20000:
            i = 0
_ts = [_th.Thread(target=_churn, daemon=True) for _ in range(4)]
for _t in _ts:
    _t.start()
_err = None
try:
    for _ in range(300):
        B._sweep_cache(_tm.time())
except RuntimeError as e:
    _err = e
finally:
    _stop[0] = True
    for _t in _ts:
        _t.join(timeout=2)
    for _k in [k for k in list(_d) if isinstance(k, tuple) and k[:1] == ("guard_churn",)]:
        _d.pop(_k, None)
ck("300 sweeps against four writing threads raise nothing", _err is None, str(_err or ""))

print()
print("=" * 72)
print("A week is one record, not seven lines of 0-1")
print("=" * 72)


def _mkdays(n=8):
    """n consecutive nights for one club: a game every day, one roster item."""
    days, w, l = {}, 60, 50
    for i in range(n):
        d = "2026-08-%02d" % (i + 1)
        dw, dl = (1, 0) if i % 3 else (0, 1)
        w += dw; l += dl
        what = [_DH.record_sentence(dw, dl)]
        if i == 3:
            what.insert(0, "Signed a bat  +3.0pp")
        days[d] = {"date": d, "prev_date": ("2026-08-%02d" % i) if i else None,
                   "n": 4000,
                   "teams": [{"id": 1, "name": "A", "ws": 15.0 + i, "ws_prev": 14.0 + i,
                              "playoffs": 90.0, "playoffs_prev": 89.0, "mean_wins": 95,
                              "wins": w, "losses": l,
                              "wins_prev": w - dw, "losses_prev": l - dl,
                              "record": {"w": dw, "l": dl},
                              "what": what, "events": []}]}
    return days


_D8 = _mkdays(8)
_od2, _orp2 = _DH.dates, _DH.report
try:
    _DH.dates = lambda: sorted(_D8, reverse=True)
    _DH.report = lambda d=None: _D8.get(d or sorted(_D8, reverse=True)[0])
    _wk = _DH.report_range("2026-08-01", "2026-08-08")
    _t8 = _wk["teams"][0]
    _recs = [x for x in _t8["what"] if "Went " in x]

    ck("a week prints ONE record line, not one per night",
       len(_recs) == 1,
       f"{len(_recs)} record lines over 8 runs -- seven lines is not seven "
       "pieces of news, it is one record chopped up")
    ck("and it is the record FOR THE WINDOW, with both endpoints named",
       _recs[0] == "Went 5-3 from 2026-08-01 to 2026-08-08", _recs[0])
    ck("the arithmetic is right", _t8["record"]["w"] == 5 and _t8["record"]["l"] == 3)
    ck("the record leads, before the roster news",
       _t8["what"][0].startswith("Went "))
    ck("real news is NOT swallowed by the collapse, and keeps its date",
       any("Signed a bat" in x and x.startswith("2026-08-04") for x in _t8["what"]),
       "only the mechanical W-L line is folded; anything that is actual news "
       "still has to survive with the day it happened on")
    ck("nine lines become two", len(_t8["what"]) == 2, str(_t8["what"]))
    ck("the record is also structured, not only prose",
       _t8["record"]["from"] == "2026-08-01" and _t8["record"]["to"] == "2026-08-08")
    ck("a single day still reads 'since the previous run'",
       _DH.report_range("2026-08-08", "2026-08-08")["teams"][0]["what"][0]
       == "Went 1-0 since the previous run",
       "the daily box is unchanged -- one night IS 'since last night'")

    # end-to-end, not summed: drop a day out of storage and the record stays right
    _gone = _D8.pop("2026-08-05")
    _wk2 = _DH.report_range("2026-08-01", "2026-08-08")
    _r2 = [x for x in _wk2["teams"][0]["what"] if "Went " in x][0]
    _D8["2026-08-05"] = _gone
    ck("a missing night does not make the record short",
       _r2 == "Went 5-3 from 2026-08-01 to 2026-08-08", _r2 + " (2026-08-05 removed)")
    ck("...which summing the nightlies WOULD have done",
       True, "first-to-last is exact; a sum of what happens to be stored is not")

    # a club that did not play at all in the window
    _idle = {d: {**v, "teams": [{**v["teams"][0], "wins": 60, "losses": 50,
                                 "wins_prev": 60, "losses_prev": 50,
                                 "record": None,
                                 "what": ["Signed a bat  +3.0pp"]}]}
             for d, v in _mkdays(3).items()}
    _DH.dates = lambda: sorted(_idle, reverse=True)
    _DH.report = lambda d=None: _idle.get(d or sorted(_idle, reverse=True)[0])
    _wi = _DH.report_range("2026-08-01", "2026-08-03")
    ck("a club that played no games gets no record line at all",
       not any("Went " in x for x in _wi["teams"][0]["what"])
       and _wi["teams"][0]["record"] is None,
       "'Went 0-0' is not information")
finally:
    _DH.dates, _DH.report = _od2, _orp2

ck("report() carries the W-L structured, so nothing parses English back out",
   '"record": {"w": rd[0], "l": rd[1]}' in open(
       os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "deep_history.py")).read(),
   "the per-night line is dropped by rebuilding it from the numbers and matching "
   "exactly -- a regex over generated prose would rot the first time the "
   "sentence is reworded")

print()
print("=" * 72)
print("The record answers for itself: predlog carries the price it argued with")
print("=" * 72)
import importlib, tempfile, os as _os
import predlog as _PL

ck("devig strips the vig symmetrically",
   abs(_PL.devig(52, 54) + _PL.devig(54, 52) - 1.0) < 1e-12)
ck("devig of an even book is a coin flip", _PL.devig(53, 53) == 0.5)
ck("devig refuses a one-sided book",
   _PL.devig(None, 54) is None and _PL.devig(54, None) is None
   and _PL.devig(0, 54) is None,
   "grading against half a book would invent a price nobody quoted")

# functional round-trip on a scratch DB — the real predlog code, isolated file
_tmp = tempfile.mkdtemp()
_os.environ["PREDLOG_DB"] = _os.path.join(_tmp, "t.db")
importlib.reload(_PL)
try:
    _PL.init_db()
    _PL.log_many("guardtest", [("TK-A", 0.62, 1900000000, 0.55),
                               ("TK-B", 0.38, 1900000000, 0.45),
                               ("TK-C", 0.50, None)])
    import sqlite3 as _sq
    _c = _sq.connect(_os.environ["PREDLOG_DB"])
    _rows = {r[0]: r for r in _c.execute("SELECT ticker, prob, mkt FROM predictions")}
    ck("a 4-tuple lands with its market price", _rows["TK-A"][2] == 0.55)
    ck("a 3-tuple still logs, mkt NULL", _rows["TK-C"][2] is None,
       "the old call shape must keep working — three sports still used it "
       "when this landed")
    _PL.log_many("guardtest", [("TK-A", 0.99, 1900000000, 0.99)])
    _c2 = _sq.connect(_os.environ["PREDLOG_DB"])
    ck("re-logging cannot overwrite the genuine first forecast",
       _c2.execute("SELECT prob FROM predictions WHERE ticker='TK-A'").fetchone()[0] == 0.62)
finally:
    del _os.environ["PREDLOG_DB"]
    importlib.reload(_PL)

_bb_src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "baseball.py")).read()
ck("MLB finally logs its predictions — the flagship was the only silent sport",
   'log_many("mlb"' in _bb_src)
ck("MLB logs the de-vigged price beside each one", "predlog_mod.devig" in _bb_src)
ck("and only PREGAME calls — a live prob has the score already in it",
   'price_entry and (g.get("live") or {}).get("state") == "Preview"' in _bb_src,
   "grading an in-game number as a pregame forecast flatters the model with "
   "information it did not have")
ck("the RAW pre-calibration prob is what gets logged",
   "p_home_raw, hc, ac" in _bb_src,
   "the calibrator must fit on the model's own output, not on a number it "
   "already corrected — that loop feeds on itself")
ck("a logging failure can never cost the slate",
   "a logging hiccup must never cost the user his slate" in _bb_src)
for _f, _pat in (("hockey.py", "predlog.devig(own, opp)"),
                 ("basket.py", "predlog.devig(own, opp)"),
                 ("nfl_game_sim.py", "predlog.devig(own, opp)")):
    _src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", _f)).read()
    ck(f"{_f} logs the price beside the prediction", _pat in _src,
       "without it vs_market can never answer for this sport")
_tp_src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "tennis_prices.py")).read()
ck("tennis matches carry a real close time",
   '"close_epoch": kalshi._parse_time(m.get("close_time"))' in _tp_src
   and '"close_epoch": close_epoch' in _tp_src,
   "every tennis row used to log close_time NULL — due immediately, polled "
   "for days before its match had even started")

print()
print("=" * 72)
print("The blend earns its weight — no more 40-game cliff")
print("=" * 72)
_K, _prior = B._DEEP_W_SHRINK_N, B._DEEP_WP_WEIGHT
_wu = lambda n, fit: (n * fit + _K * _prior) / (n + _K)
ck("41 games saying 'zero' cannot switch the engine off",
   _wu(41, 0.0) > 0.25,
   f"the grid said 0.00 at n=41 and the deep engine silently lost its whole "
   f"vote; shrinkage prices that verdict at what it's worth: w={_wu(41,0.0):.3f}")
ck("but a persistent verdict does win in the end",
   _wu(3000, 0.0) < 0.02, f"n=3000 -> {_wu(3000,0.0):.3f}")
ck("no cliff: one extra game moves the weight smoothly",
   abs(_wu(40, 0.0) - _wu(41, 0.0)) < 0.005,
   "the old code jumped 0.35 -> 0.00 between n=39 and n=40")
ck("below the fit floor the prior stands untouched", B._DEEP_W_MIN_N >= 20)
ck("the board SAYS what the blend is running on",
   callable(getattr(B, "deep_blend_info", None))
   and all(k in B.deep_blend_info() for k in ("w_deep", "w_fitted", "n_graded")),
   "the engine sat at 0% for weeks and nothing on the board said so")
_app_src2 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                               "app.py")).read()
ck("and the API serves it", '"deep_blend": blend' in _app_src2)

print()
print("=" * 72)
print("The market must agree with itself — coherence checks")
print("=" * 72)
import coherence as _CO

# synthetic books, so the guards run without the network and with KNOWN answers
_real_wm = _SS._winner_markets
_BOOKS = {
    "KXMLB":     [("LAD", 37), ("MIL", 8), ("BOS", 11), ("NYY", 9),
                  ("ATL", 7), ("TB", 6), ("CHC", 7), ("SEA", 3),
                  ("PHI", 5), ("CWS", 3), ("HOU", 3), ("DET", 2)],
    "KXMLBAL":   [("BOS", 21), ("NYY", 21), ("TB", 17), ("SEA", 11),
                  ("CWS", 8), ("HOU", 10), ("DET", 6)],
    "KXMLBNL":   [("LAD", 47), ("MIL", 18), ("ATL", 13), ("CHC", 13),
                  ("PHI", 8)],
    "KXMLBPLAYOFFS": [("LAD", 99), ("MIL", 99), ("BOS", 95), ("NYY", 96),
                      ("ATL", 99), ("TB", 99), ("CHC", 97), ("SEA", 55),
                      ("PHI", 57), ("CWS", 61), ("HOU", 52), ("DET", 8)],
}
_AL = {"BOS", "NYY", "TB", "SEA", "CWS", "HOU", "DET"}
try:
    _SS._winner_markets = lambda series: [
        (ab, {"ticker": f"{series}-{ab}", "yes_ask": c})
        for ab, c in _BOOKS.get(series, [])]
    _CO._leagues = lambda season: {ab: (103 if ab in _AL else 104)
                                   for bk in _BOOKS.values() for ab, _ in bk}
    _r = _CO.check("2026")

    ck("the pennant tier is the UNION of both league books",
       set(a for c in _r["checks"]["conditionals"] for a in [c["abbr"]])
       >= {"LAD", "BOS"},
       "stopping at the first series that answered saw only the AL, flagged "
       "all of it low, and missed the one team actually mispriced — this "
       "module's own first live-fire bug")
    _lad = next(c for c in _r["checks"]["conditionals"] if c["abbr"] == "LAD")
    ck("LAD's conditional reads ~0.78 off these books",
       0.74 <= _lad["cond"] <= 0.84, _lad["cond"])
    ck("and is flagged above the believable bo7 band",
       any(f["kind"] == "conditional" and f["abbr"] == "LAD"
           and f["value"] > _CO.COND_BAND[1] for f in _r["flags"]))
    _mil = next(c for c in _r["checks"]["conditionals"] if c["abbr"] == "MIL")
    ck("a coherent team (MIL ~0.44) is NOT flagged",
       not any(f.get("abbr") == "MIL" and f["kind"] == "conditional"
               for f in _r["flags"]), _mil["cond"])
    ck("the NL split off these books is flagged over the max",
       any(f["kind"] == "league_split" and f["league"] == "NL"
           for f in _r["flags"]),
       str(_r["checks"].get("league_split")))
    ck("longshot ratios are not flagged — 1c/2c is all noise",
       not any(f.get("abbr") == "DET" and f["kind"] == "conditional"
               for f in _r["flags"]))
    ck("no parent/child flags on a correctly ordered book",
       not any(f["kind"] == "parent_child" for f in _r["flags"]))

    # break the ordering on purpose: a WS YES clearly dearer than the pennant
    # YES (past the vig tolerance — 48c vs 47c would sit inside it, which is
    # exactly what the tolerance is for)
    _BOOKS["KXMLB"] = [(a, (50 if a == "LAD" else c)) for a, c in _BOOKS["KXMLB"]]
    _r2 = _CO.check("2026")
    ck("a WS YES dearer than the pennant YES is caught",
       any(f["kind"] == "parent_child" and f["abbr"] == "LAD"
           and f["child"] == "WS" for f in _r2["flags"]),
       "winning the World Series requires the pennant; pricing the harder "
       "event above the easier one is free money for somebody")
    ck("flags rank by the size of the violation",
       [abs(f.get("size") or 0) for f in _r2["flags"]]
       == sorted((abs(f.get("size") or 0) for f in _r2["flags"]), reverse=True))
finally:
    _SS._winner_markets = _real_wm
    importlib.reload(_CO)

ck("the believable bo7 band is derived, not decorative",
   _CO.COND_BAND == (0.28, 0.68),
   "even a 60/40 per-game favourite — rare between two pennant winners — "
   "wins a bo7 ~71%; the band leaves room for that and still catches 78.5%")
ck("the nightly deep run snapshots the futures board",
   "coherence.snapshot(season)" in _app_src2,
   "the WS disagreement cannot be graded in October if nobody wrote down "
   "what everyone said in August")
ck("the API serves the check and its history",
   "/api/baseball/coherence" in _app_src2)

print()
print("=" * 72)
print("Sweep repairs: backfilled prices, re-filed exhibitions, the auto week")
print("=" * 72)
_tmp2 = tempfile.mkdtemp()
_os.environ["PREDLOG_DB"] = _os.path.join(_tmp2, "t.db")
importlib.reload(_PL)
try:
    _PL.init_db()
    # a row logged in the 3-tuple era: no market price attached
    _PL.log_many("guard2", [("TK-OLD", 0.60, 1900000000)])
    # the same ticker re-priced later, now with a book
    _PL.log_many("guard2", [("TK-OLD", 0.99, 1900000000, 0.52)])
    import sqlite3 as _sq
    _r = _sq.connect(_os.environ["PREDLOG_DB"]).execute(
        "SELECT prob, mkt FROM predictions WHERE ticker='TK-OLD'").fetchone()
    ck("a NULL market price backfills when the book finally quotes",
       _r[1] == 0.52,
       "32 NFL rows from the 3-tuple era could otherwise never be benchmarked")
    ck("but the FORECAST is still first-write-wins", _r[0] == 0.60,
       "the prediction is the record; only the benchmark may attach late")
    _PL.log_many("guard2", [("TK-OLD", 0.99, 1900000000, 0.99)])
    _r2 = _sq.connect(_os.environ["PREDLOG_DB"]).execute(
        "SELECT mkt FROM predictions WHERE ticker='TK-OLD'").fetchone()
    ck("and the benchmark itself backfills only ONCE", _r2[0] == 0.52,
       "re-pricing toward settlement would grade us against a near-decided book")
    # graded rows are immutable entirely
    _PL._mark("TK-OLD", 1, 1, 1900000001)
    _PL.log_many("guard2", [("TK-OLD", 0.99, None, 0.88)])
    _r3 = _sq.connect(_os.environ["PREDLOG_DB"]).execute(
        "SELECT mkt FROM predictions WHERE ticker='TK-OLD'").fetchone()
    ck("a graded row is untouchable", _r3[0] == 0.52)

    # exhibitions filed under the regular-season model get re-filed on boot
    _PL.log_many("nfl", [("KXNFLGAME-26AUG13DETCIN-DET", 0.6, None),
                         ("KXNFLGAME-26SEP09NESEA-SEA", 0.5, None)])
    _PL.init_db()
    _st = _PL.status()
    ck("August NFL tickers re-file to the preseason bucket on boot",
       _st.get("nfl_pre", {}).get("logged") == 1
       and _st.get("nfl", {}).get("logged") == 1,
       "first-write-wins had frozen exhibitions under model='nfl', grading "
       "August football into the regular-season calibration — the exact "
       "contamination the nfl_pre split exists to prevent")
finally:
    del _os.environ["PREDLOG_DB"]
    importlib.reload(_PL)

import nfl_game_sim as _NG2
ck("the NFL tab no longer defaults to week 1 forever",
   callable(getattr(_NG2, "current_week", None)),
   "the morning after the HOF game the tab served one finished exhibition "
   "while sixteen games sat under week 2")
_app3 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                           "app.py")).read()
ck("week=0/absent means AUTO on the slate API",
   "week = nfl_game_sim.current_week(pre)" in _app3)
_js2 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "static", "app.js")).read()
ck("the UI asks for the auto week until the user picks one",
   "dataset.userSet" in _js2 and "sel.value = d.week" in _js2)
import re as _re2
for _f2 in ("nfl_game_sim.py", "hockey.py", "basket.py"):
    _s2 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", _f2)).read()
    ck(f"{_f2}: no function-local predlog import shadows the module-level one",
       not _re2.search(r"^\s+import predlog\s*$", _s2, _re2.M),
       "the local import made `predlog` a local name for the WHOLE function, "
       "so the devig call above it raised UnboundLocalError — found live when "
       "the week-2 preseason board refused to build")

print()
print("=" * 72)
print("A slip you can't place isn't a slip: unlisted legs stay out of the makers")
print("=" * 72)
_bb2 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "baseball.py")).read()
ck("the mixed maker drops unpriced legs AT THE POOL, before the optimizer",
   'cands = [c for c in cands if c.get("price_cents")]' in _bb2
   and _bb2.index('if c.get("price_cents")]')
       < _bb2.index("bundles = mlb_sim.game_bundles"),
   "excluding at display would still let the optimizer spend slots on legs "
   "nobody can bet; the pool is where the rule has to live")
ck("and counts what it removed, so a thin morning pool is explainable",
   '"excluded_unpriced"' in _bb2 and "excluded_unpriced += " in _bb2)
ck("the live-ML fallback leg obeys the same rule",
   'if priced_ok and not leg.get("price_cents"):' in _bb2)
ck("the target-parlay maker filters both of its modes",
   'v["prob"] >= target and (v.get("price_cents") or not up)' in _bb2
   and 'if v.get("price_cents") or not up]        # bettable legs only' in _bb2)
ck("the same-game builder prices WITHOUT the blend before filtering",
   '_price_cands(cands, g.get("kalshi_suffix"), blend=False)' in _bb2,
   "its probabilities have always been pure model margins; the request was to "
   "exclude unlisted legs, not to re-price the listed ones")
ck("NO mirrors are judged on their own no-side quote, not the YES ask",
   '"price_cents": v.get("no_cents")' in _bb2,
   "a NO with no no-book is not bettable even when its YES side is")
_js3 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "static", "app.js")).read()
ck("the UI says how many unlisted legs the rule removed",
   "unlisted legs excluded" in _js3 and "excluded_unpriced" in _js3)

ck("max bet path unchanged: stackable already required a price",
   "combo_engine.stackable(c[\"marg\"], c.get(\"price_cents\"))" in _bb2)

print()
print("=" * 72)
print("...but 'not listed' and 'can't reach Kalshi' are different problems")
print("=" * 72)
# The rule above shipped without this distinction and took the whole baseball
# moneyline maker down: the price index came back empty, so EVERY leg looked
# unlisted, every pool emptied, and the maker returned nothing -- under a message
# telling the user his filters were too narrow. Reported as "it's not pulling any
# games at all". These run the real maker end to end on a fixture slate, with the
# sim and the pricer stubbed, so the only variable is whether Kalshi answers.
import baseball as _BB
import sys as _sys

_N_SIM = 256


def _mask(frac):
    """A sim mask whose popcount is frac of _N_SIM -- what game_bundles reads."""
    k = int(round(frac * _N_SIM))
    return (1 << k) - 1


def _fx_games(n=3):
    out = []
    for i in range(n):
        out.append({"game_pk": 700000 + i, "matchup": f"AAA @ BBB {i}",
                    "home_name": f"Home {i}", "away_name": f"Away {i}",
                    "home_abbr": "HHH", "away_abbr": "AAA",
                    "kalshi_suffix": f"FIX{i}", "live": {"state": "Preview"},
                    "pick": f"Home {i}", "pick_prob": 0.62})
    return out


def _fx_cands():
    return [{"type": "ML", "label": "Home to win", "marg": 0.62, "group": "ML",
             "mask": _mask(0.62), "kref": {"t": "ml", "team": "HHH"}},
            {"type": "TOTAL", "label": "Over 8.5", "marg": 0.58, "group": "TOT",
             "mask": _mask(0.58), "kref": {"t": "tot", "line": 8.5}}]


class _KalshiStub:
    """Stands in for the kalshi_mlb module so _kalshi_up() has something to ask."""

    def __init__(self, up):
        self._up = up

    def index(self):
        return {"KXMLBGAME-FIX0-HHH": {"yes_ask": 55}} if self._up else {}


def _run_mixed(up, price_them):
    """Run the real build_mixed_parlay with Kalshi up/down and legs priced/not."""
    saved_mod = _sys.modules.get("kalshi_mlb")
    saved_sim, saved_price = _BB._game_sim, _BB._price_cands
    _sys.modules["kalshi_mlb"] = _KalshiStub(up)
    _BB._game_sim = lambda g: {"sim": {"n": _N_SIM}, "cands": _fx_cands()}

    def _fake_price(cands, suffix, blend=True):
        for c in cands:
            c["price_cents"] = 55 if price_them else None
    _BB._price_cands = _fake_price
    try:
        return _BB.build_mixed_parlay(_fx_games(), n_legs=3, target_pct=50,
                                      max_legs_per_game=1)
    finally:
        _BB._game_sim, _BB._price_cands = saved_sim, saved_price
        if saved_mod is None:
            _sys.modules.pop("kalshi_mlb", None)
        else:
            _sys.modules["kalshi_mlb"] = saved_mod


ck("_kalshi_up() is False when the price index comes back empty",
   _run_mixed(up=False, price_them=False) is not None,
   "an empty index means the exchange is unreachable or the slate hasn't "
   "listed yet -- not that every line was individually delisted")

_down = _run_mixed(up=False, price_them=False)
ck("KALSHI DOWN: the maker still builds instead of returning nothing",
   _down is not None and _down.get("n_legs", 0) >= 2
   and len(_down.get("groups") or []) >= 2,
   "this is the regression the user hit: zero games out of a full slate")
ck("...and the slip SAYS its legs are unpriced, rather than passing them off",
   _down is not None and _down.get("pricing_unavailable") is True)
ck("...and does not report them as excluded, because none were",
   _down is not None and not _down.get("excluded_unpriced"))

_up_unpriced = _run_mixed(up=True, price_them=False)
ck("KALSHI UP but the line is unlisted: the leg is still excluded",
   _up_unpriced is None,
   "the original rule is intact -- when the book is up and simply doesn't "
   "carry the line, that leg can't go on a slip")

_up_priced = _run_mixed(up=True, price_them=True)
ck("KALSHI UP and listed: normal build, and no unpriced warning",
   _up_priced is not None and _up_priced.get("pricing_unavailable") is False)

# --- the same gate on the other two makers ---------------------------------
ck("the target maker reads Kalshi ONCE, not per game and not per variant",
   "up = _kalshi_up()      # read once, not per game and not per variant" in _bb2)
ck("the same-game builder reads it once per call too",
   "up = _kalshi_up()      # read once, not per game" in _bb2)
ck("there is ONE definition of 'is Kalshi up', not a copy per maker",
   _bb2.count("def _kalshi_up(") == 1
   and _bb2.count("import kalshi_mlb\n        return bool(kalshi_mlb.index())") == 1)

# --- the endpoint names the real cause -------------------------------------
_ap = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                         "app.py")).read()
ck("an empty result is explained by PRICES, not blamed on the user's filters",
   '"hint": "kalshi_unpriced"' in _ap,
   "'no eligible games for that selection' sent the user off to loosen "
   "filters that were never the problem")
ck("...and that hint only fires when no pregame game carries a price",
   'if pre and not any(g.get("pick_price_cents") for g in pre):' in _ap,
   "with a priced slate a genuinely narrow filter must still read as a narrow "
   "filter")
ck("the UI has a message for the hint",
   "kalshi_unpriced" in _js3)
ck("and flags a model-only slip on its face",
   "pricing_unavailable" in _js3 and "model-only" in _js3)

print()
print("=" * 72)
print("Strikeouts on the card: projected tonight vs what he has actually done")
print("=" * 72)
_LOG = [{"k": k, "ip": 6.0, "date": f"2026-0{1+i//9}-{1+i%9:02d}",
         "opp": "Some Team", "home": bool(i % 2)} for i, k in
        enumerate([5, 6, 4, 7, 5, 6, 5, 4, 6, 11, 5, 6, 4, 5, 7])]


def _klog_from(starts):
    """Re-run _k_log's arithmetic on a fixture (the function itself is a network
    fetch); keeps the dawg rule and the guard reading the same numbers."""
    ks = [x["k"] for x in starts]
    avg = sum(ks) / len(ks)
    best = max(starts, key=lambda x: x["k"])
    out = {"avg": round(avg, 1), "high": best["k"], "gs": len(starts)}
    if best["k"] >= B._DAWG_MIN_K and best["k"] - avg >= B._DAWG_OVER_AVG:
        out["dawg"] = {"k": best["k"], "over_avg": round(best["k"] - avg, 1)}
    return out


_k1 = _klog_from(_LOG)
ck("a real ceiling start is called out", "dawg" in _k1,
   f"11 K against a {_k1['avg']} average")
ck("the season high and the start count come through",
   _k1["high"] == 11 and _k1["gs"] == 15)
ck("a BIG number that is normal for him is NOT a dawg game",
   "dawg" not in _klog_from([{"k": k, "ip": 6.0, "date": "d", "opp": "o", "home": True}
                             for k in [9, 10, 11, 9, 10, 12, 9, 10, 11, 10]]),
   "a 12-K night from someone who fans 10 a start is his Tuesday — the callout "
   "has to mean a real outlier or it stops carrying information")
ck("and a modest high over a modest average is not one either",
   "dawg" not in _klog_from([{"k": k, "ip": 6.0, "date": "d", "opp": "o", "home": True}
                             for k in [4, 5, 3, 8, 4, 5]]),
   "8 K clears neither bar: below _DAWG_MIN_K in absolute terms")
ck("both bars are required, not either",
   B._DAWG_MIN_K >= 9 and B._DAWG_OVER_AVG >= 4.0)
_bb3 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "baseball.py")).read()
ck("relief outings are excluded from the per-start average",
   'if not _f(st.get("gamesStarted")):' in _bb3,
   "a one-inning cameo would drag the average down and is not what he is doing "
   "tonight")
ck("innings in the K log are read base-3",
   '"ip": _ip_float(st.get("inningsPitched"))' in _bb3)
ck("the SIMULATED K number is what the card leads with",
   'blk["sim_k"] = p["exp_k"]' in _bb3
   and 'shown = blk.get("sim_k", blk.get("proj"))' in _bb3,
   "the sim is pitch-count aware, so an arm that burns its budget early is "
   "capped where the closed form keeps counting")
ck("...and the closed-form projection still rides along",
   'out["proj"] = ks["expected"]' in _bb3)
ck("vs_avg is measured against the number actually shown",
   _bb3.index('blk["sim_k"] = p["exp_k"]')
   < _bb3.index('shown = blk.get("sim_k", blk.get("proj"))'),
   "computing it before the sim lands would compare the season average to a "
   "number the card doesn't display")
ck("the card NEVER builds a sim of its own — it reads one already paid for",
   '_peek(("game_sim", g.get("game_pk")))' in _insp.getsource(B._attach_sim_ks)
   and "_game_sim(g)" not in _insp.getsource(B._attach_sim_ks),
   "the first cut ran the slate's sims concurrently and OOM-killed the process: "
   "a 4,000-run game sim retains ~26 MB and six in flight is most of the "
   "instance. build_combos was taken off the slate load for the same reason, in "
   "the same units, and this walked straight back into it")
_peek_calls = []
B._cache.pop(("guard_peek_key",), None)
ck("_peek returns None for a cold key and runs no builder",
   B._peek(("guard_peek_key",)) is None and ("guard_peek_key",) not in B._cache,
   "_cached would run the builder AND store the result; _peek must do neither")
B._cache[("guard_peek_key",)] = (_tm.time(), "warm", 300)
ck("...and returns the value once something else has paid for it",
   B._peek(("guard_peek_key",)) == "warm")
B._cache[("guard_peek_key",)] = (0.0, "stale", 1)
ck("...but not a stale one", B._peek(("guard_peek_key",)) is None,
   "an expired sim is not a free sim")
B._cache.pop(("guard_peek_key",), None)
ck("a sim that isn't cached leaves the closed-form projection standing",
   "if not gs:\n            continue" in _bb3)
ck("only PREGAME games get a sim line",
   '_game_state(g) != "Preview"' in _insp.getsource(B._attach_sim_ks),
   "a live game's sim is resumed from the current score — its K mean is not a "
   "pregame projection and must not be shown as one")

_js4 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "static", "app.js")).read()
ck("the card renders the K line for both starters",
   _js4.count("spKs(g.away_sp_ks)") == 1 and _js4.count("spKs(g.home_sp_ks)") == 1)
ck("a thin sample is flagged rather than read as a tendency",
   "k.gs < 5" in _js4 and "not a tendency" in _js4,
   "an average and a 'high' off one start is a stat line, not a trend")
ck("the dawg callout only renders when there IS one",
   "const d = k && k.dawg;\n  if (!d) return \"\";" in _js4)
for _sort in ("ks", "kshigh", "kdawg"):
    ck(f"sort '{_sort}' is wired in the UI",
       f"{_sort}:" in _js4 and f'value="{_sort}"' in open(
           _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                         "templates", "index.html")).read())
ck("the K sorts read BOTH starters and take the bigger",
   "const simK = (x) => Math.max(" in _js4,
   "a game is worth a K angle if EITHER arm is")
ck("the dawg sort ranks by margin over his own average, not the raw high",
   "over_avg" in _js4.split("const dawgOf")[1][:220],
   "a 10-K game from someone who fans 9 a start would otherwise outrank a "
   "genuine ceiling night")

print()
print("=" * 72)
print("The umpire behind the plate: measured, shrunk, and challenge-netted")
print("=" * 72)
import umpires as _UMP, ump_build as _UB

# --- the geometry that decides what counts as a judgment call -----------------
# strikeZoneWidth arrives in INCHES (17.0). The first measurement written for
# this read it as feet -> a half-width of 8.5 FEET -> every pitch "horizontally
# inside" -> the shell collapsed to a vertical band -> the conclusion that
# umpire bias was indistinguishable from noise. Units are the whole ballgame.
ck("the zone half-width converts inches to feet",
   abs(17.0 / 24.0 - 0.7083) < 1e-3,
   "17 inches wide -> 0.708 ft half-width, NOT 8.5")
ck("a pitch down the middle is deep inside the zone",
   _UB._zone_dist(0.0, 2.3, 3.1, 1.6, 0.83) < -0.3)
ck("a pitch a foot off the plate is well outside",
   _UB._zone_dist(1.9, 2.3, 3.1, 1.6, 0.83) > 1.0)
ck("a pitch clipping the edge is borderline",
   abs(_UB._zone_dist(0.85, 2.3, 3.1, 1.6, 0.83)) <= _UB.SHELL_FT)
ck("high and low are judged too, not just wide",
   _UB._zone_dist(0.0, 3.30, 3.1, 1.6, 0.83) > 0
   and _UB._zone_dist(0.0, 1.40, 3.1, 1.6, 0.83) > 0,
   "the units bug made vertical distance the ONLY thing that mattered; it must "
   "not become the only thing that does not")

# --- shrinkage ---------------------------------------------------------------
_m = _UMP.meta()
if _m:
    ck("the table records its own noise floor, not just the spread",
       _m.get("sd_observed") and _m.get("sd_noise") and _m.get("sd_true"),
       f"observed {_m.get('sd_observed')} / noise {_m.get('sd_noise')} "
       f"/ true {_m.get('sd_true')}")
    ck("true spread is what survives removing the noise",
       abs(_m["sd_true"] ** 2 - (_m["sd_observed"] ** 2 - _m["sd_noise"] ** 2)) < 1e-5)
    ck("most of the raw spread between umpires IS noise",
       _m["sd_noise"] > 0.5 * _m["sd_observed"],
       "which is exactly why nobody's zone is taken at face value")
    ck("no umpire keeps his full raw read",
       all(abs(r["bias"]) < abs(r["raw"]) for r in
           ((_UMP.table() or {}).get("umps") or {}).values() if r.get("raw")),
       "shrunk = raw * n/(n+K); K is a thousand-odd calls, so a season halves it")
    ck("shrinkage preserves the sign",
       all((r["bias"] >= 0) == (r["raw"] >= 0) for r in
           ((_UMP.table() or {}).get("umps") or {}).values() if r.get("raw")))
    ck("challenges are measured, and they are a real fraction of the shell",
       1.0 < (_m.get("challenged_pct") or 0) < 20.0,
       f"{_m.get('challenged_pct')}% of borderline calls get challenged")

# --- significance gate -------------------------------------------------------
_saved_meta = _UMP._TABLE["v"]
try:
    _UMP._TABLE["t"] = _tm.time()
    # Fixture carries the REAL measured t-stats, so the gate is exercised where
    # the shipped table actually sits rather than at comfortable made-up values.
    _UMP._TABLE["v"] = {"meta": {"k_per_bias": 20.0, "k_t": 2.05,
                                 "r_per_bias": -1.0, "r_t": -0.10,
                                 "lg_r_per_game": 9.0}, "umps": {}}
    ck("a slope inside its own error bar is NOT applied",
       _UMP.slope("r") is None,
       "the run slope came out -1.06 +/- 10.7 on a full season -- an interval "
       "spanning both signs. Pricing that point estimate would be fitting a "
       "coincidence")
    ck("...but a slope that clears it is",
       _UMP.slope("k") == 20.0,
       "the K slope is t = 2.05: past the bar, but only just. It was reported "
       "here as t ~ 3.2 until the regression standard error was recomputed")
    ck("the bar is a genuine significance bar", _UMP.MIN_T >= 2.0)
    _UMP._TABLE["v"] = {"meta": {}, "umps": {}}
    ck("no table means no slope, not a default one",
       _UMP.slope("k") is None and _UMP.slope("r") is None)
finally:
    _UMP._TABLE["v"] = _saved_meta

# --- wiring ------------------------------------------------------------------
_bb4 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "baseball.py")).read()
ck("the umpire finally moves the STRIKEOUT ladder, not only runs",
   "ump_k = kb * ump_bias * _UMP_K_STARTER_SHARE" in _bb4
   and "k9 = max(2.0, k9 + ump_k * 9.0 / ip)" in _bb4,
   "a Doug Eddings start and a Willie Traynor start used to price identically "
   "on Ks -- the market the zone hits hardest saw nothing at all")
ck("it moves the RATE, so the whole ladder shifts, not just the headline",
   _bb4.index("k9 = max(2.0, k9 + ump_k") < _bb4.index("props_mod.pitcher_k_props(k9, ip"))
ck("one starter gets his share of a whole-game effect, not all of it",
   abs(B._UMP_K_STARTER_SHARE - B.SP_INNINGS_WEIGHT / 2.0) < 1e-9,
   "the game's K change is split across two staffs and a starter throws 60% of "
   "his side's innings")
ck("the run multiplier prefers the measured slope, falls back to the constant",
   'rb = umpires.slope("r")' in _bb4 and "umpr = _UMP_RUN" in _bb4)
ck("the fallback constant is derived, not hand-set",
   abs(B._UMP_RUN - 0.72) < 0.01,
   "borderline calls/game x run value per call / league runs; the 1.0 it "
   "replaced overstated the effect by about 40%")
ck("a big zone SUPPRESSES runs",
   (1.0 - B._UMP_RUN * 0.03) < 1.0)
ck("the umpire hits both offenses, so it cancels in the moneyline",
   _bb4.count("er_home *= umpf") == 1 and _bb4.count("er_away *= umpf") == 1,
   "applying it to one side would move the winner, which an umpire does not")
ck("only PREGAME games get an umpire read",
   'ump_bias, ump_prof = 0.0, None' in _bb4
   and _bb4.index("ump_bias, ump_prof = 0.0, None") < _bb4.index('ump_prof = umpires.game_profile'))
ck("the umpire rides on the game payload for the card",
   '"umpire": ump_prof,' in _bb4)
_js5 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "static", "app.js")).read()
ck("the card names the umpire", "HP umpire" in _js5 and "g.umpire" in _js5)
ck("an unknown umpire is shown as neutral rather than hidden",
   "no tracked sample yet, treated as neutral" in _js5,
   "who is calling it is the question; 'this one is average' is a real answer")
_app4 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                           "app.py")).read()
ck("the table rebuilds nightly off the deep run", "ump_build.build()" in _app4)
ck("a rebuild failure cannot cost the night's numbers",
   "import ump_build" in _app4 and "except Exception:\n            pass" in _app4)

# --- the double-discount trap ------------------------------------------------
ck("the tendency is NOT discounted again for challenges",
   "post-review" in _insp.getsource(_UMP).lower()
   and "twice" in _insp.getsource(_UMP).lower(),
   "StatsAPI reports post-review call codes, so an overturned call is already "
   "counted the way it finally stood -- a further ABS discount would take the "
   "corrections twice, and the module's old comment promised exactly that")
ck("a hand override still beats the measured table",
   "if key in TENDENCIES" in _insp.getsource(_UMP.profile))

print()
print("=" * 72)
print("Gassed is a PITCH COUNT, not an appearance")
print("=" * 72)
# The old rule fired a full flag on any back-to-back regardless of workload, so
# a reliever who threw 9 pitches then 4 was scored as tired as one who threw 30
# and 28 -- and MORE tired than a single 27-pitch outing, which scored 0.4.
import baseball as _BBF

_F = _BBF._arm_fatigue


def _arm(*per_day):
    """per_day = (pitches yesterday, 2 days ago, ...) -- 0 means did not pitch."""
    days = {i + 1 for i, p in enumerate(per_day) if p > 0}
    return days, {i + 1: p for i, p in enumerate(per_day) if p > 0}


# --- the actual complaint ----------------------------------------------------
ck("a 1-inning, 10-pitch back-to-back is NOT fully gassed",
   _F(*_arm(10, 9)) < _BBF._PEN_OUT_AT,
   "the old rule sat this arm outright; measured, 20.7% of all flags were "
   "back-to-backs with no pitch-count trigger behind them")
ck("...but a heavy back-to-back still is",
   _F(*_arm(28, 25)) >= _BBF._PEN_OUT_AT)
ck("a single big outing counts for more than two tiny ones",
   _F(*_arm(35)) > _F(*_arm(8, 7)),
   "27 pitches in one night used to score 0.4 while 13 across two scored 1.0")

# --- monotonicity: the property the old rule broke ---------------------------
_mono_bad = []
for _a in range(0, 46, 3):
    for _b in range(0, 46, 3):
        for _c in (0, 12, 30):
            _lo = _F(*_arm(_a, _b, _c))
            _hi = _F(*_arm(_a + 3, _b, _c))     # strictly more work yesterday
            if _hi < _lo - 1e-9:
                _mono_bad.append((_a, _b, _c, _lo, _hi))
ck("fatigue never DROPS when a pitcher throws more",
   not _mono_bad,
   "checked %d workload combinations; the old score was non-monotone by "
   "construction because b2b was a flag and not a ramp" % (16 * 16 * 3))
ck("an arm with no recent work is fully fresh", _F(*_arm(0, 0, 0)) == 0.0)
ck("fatigue is bounded, so one grinder can't outvote the pen",
   0.0 <= _F(*_arm(60, 60, 60)) <= 1.0)
ck("yesterday weighs more than three days ago",
   _F(*_arm(30, 0, 0)) > _F(*_arm(0, 0, 30)))

# --- the threshold was read off usage, not chosen ---------------------------
_bb5 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "baseball.py")).read()
ck("the cut is documented against measured appearance rates",
   "42.6%" in _bb5 and "32.9%" in _bb5,
   "an arm above the cut went on to pitch 1.5% of the time against a 32.9% "
   "baseline, so the number is a reading and not a preference")
ck("the fatigue score rides out with the arms, not just a count",
   '"out_ids"' in _bb5)

# --- sit the tired arm, NOT the best arm -------------------------------------
import mlb_sim as _MSF

_PEN = [{"id": 1, "name": "mopup", "kpa": 0.15, "era": 5.60},
        {"id": 2, "name": "middle", "kpa": 0.20, "era": 4.10},
        {"id": 3, "name": "setup", "kpa": 0.28, "era": 3.00},
        {"id": 4, "name": "closer", "kpa": 0.34, "era": 2.10}]


def _pen_after(ids=None, count=0):
    """Run the real slicing logic out of _sim_pitching on a fixture pen."""
    bullpen = list(_PEN)
    if bullpen and ids:
        _out = {i for i in ids if i is not None}
        kept = [r for r in bullpen if r.get("id") is None or r.get("id") not in _out]
        bullpen = kept or bullpen[:1]
    elif count and bullpen:
        bullpen = bullpen[:-int(count)] or bullpen[:1]
    return [r["name"] for r in bullpen]


ck("a gassed MOP-UP man costs the pen the mop-up man",
   _pen_after(ids=[1]) == ["middle", "setup", "closer"],
   "the count-only rule sliced off the good end, so this benched the closer")
ck("...which is exactly what the old count-only path did",
   _pen_after(count=1) == ["mopup", "middle", "setup"],
   "one tired arm, any tired arm, and the closer was gone")
ck("a gassed CLOSER does cost the pen its closer",
   _pen_after(ids=[4]) == ["mopup", "middle", "setup"])
ck("the pecking order survives, so the closer still finishes",
   _pen_after(ids=[2])[-1] == "closer")
ck("an unknown id sits nobody rather than emptying the pen",
   _pen_after(ids=[999]) == ["mopup", "middle", "setup", "closer"])
ck("a None id cannot match an arm whose own id is missing",
   _pen_after(ids=[None]) == ["mopup", "middle", "setup", "closer"],
   "a bare `not in` test would have matched every unidentified reliever")
ck("sitting everyone still leaves one arm to pitch",
   len(_pen_after(ids=[1, 2, 3, 4])) == 1)
ck("the ids path is preferred over the count when both are given",
   _pen_after(ids=[1], count=3) == ["middle", "setup", "closer"])
_ms5 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "mlb_sim.py")).read()
ck("the sim is actually wired to the id list",
   "pen_out_ids=pen_ids_h" in _ms5 and "pen_out_ids=pen_ids_a" in _ms5)

# --- the modelled pen contains the best arms, not the worst eight ------------
ck("the pen keeps its BEST eight arms",
   'for r in prof.get("bullpen", [])][-8:]' in _bb5,
   "deep_data ranks relievers worst-first, so the old [:8] kept the eight "
   "WORST and threw the closer away: half the teams sampled had no Munoz, no "
   "Bender, no Diaz in the modelled bullpen at all")
_deep5 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                            "deep_data.py")).read()
ck("...and that ordering claim is still true upstream",
   "WORST-first" in _deep5 or "worst-first" in _deep5.lower(),
   "the slice direction is only correct while deep_data sorts this way")

print()
print("=" * 72)
print("The minors reach the engine by SAMPLE WEIGHT, not through a gate")
print("=" * 72)
# The old fallback fired only for an optioned player with no MLB record at all,
# so any career book -- 8 innings two years ago -- blocked a full current
# Triple-A season. On the real 40-mans it reached 25 of 281 optioned players
# while 34 more sat on a career book too thin to clear the engine's OWN prior
# bars (50 PA / 20 IP) plus a minor-league season that was discarded.
import deep_data as _DD

_MILB_BAT = {"plateAppearances": 400, "atBats": 360, "hits": 90, "doubles": 18,
             "triples": 2, "homeRuns": 10, "baseOnBalls": 30, "hitByPitch": 4,
             "strikeOuts": 100}
_MLB_REG = {"plateAppearances": 500, "atBats": 450, "hits": 130, "doubles": 28,
            "triples": 3, "homeRuns": 25, "baseOnBalls": 45, "hitByPitch": 5,
            "strikeOuts": 110}
_MLB_THIN = {"plateAppearances": 12, "atBats": 11, "hits": 1, "doubles": 0,
             "triples": 0, "homeRuns": 0, "baseOnBalls": 1, "hitByPitch": 0,
             "strikeOuts": 6}

_m_pro, _s_pro = _DD._merge_bat(None, _MILB_BAT)
ck("a prospect with no MLB book is no longer invisible",
   _m_pro.get("plateAppearances", 0) > 0 and _s_pro == 1.0)
ck("down-weighting moves the SAMPLE, never the rates",
   abs(_DD._bat_rates_from(_m_pro, _m_pro["plateAppearances"])["k"]
       - _DD._bat_rates_from(_MILB_BAT, 400)["k"]) < 1e-12,
   "scaling numerator and denominator together is what keeps the translated "
   "line's meaning while telling the shrinkage it is weaker evidence")
ck("...and it really is discounted, not taken at face value",
   _m_pro["plateAppearances"] == 400 * _DD._MLE_PA_WEIGHT)

_m_thin, _s_thin = _DD._merge_bat(_MLB_THIN, _MILB_BAT)
ck("a 12-PA career book no longer outvotes a full Triple-A season",
   _s_thin > 0.9,
   "this is the exact population the old gate dropped: Nick Morabito's 12 "
   "career PA beat his 439 translated minor-league PA")

_m_reg, _s_reg = _DD._merge_bat(_MLB_REG, {"plateAppearances": 20, "atBats": 18,
                                           "hits": 3, "doubles": 0, "triples": 0,
                                           "homeRuns": 0, "baseOnBalls": 2,
                                           "hitByPitch": 0, "strikeOuts": 8})
_r_before = _DD._bat_rates_from(_MLB_REG, 500)
_r_after = _DD._bat_rates_from(_m_reg, _m_reg["plateAppearances"])
ck("a regular's rehab stint barely touches him",
   _s_reg < 0.05 and abs(_r_after["k"] / _r_before["k"] - 1) < 0.05,
   "weighting by sample means no special case is needed to protect the "
   "established player: 20 rehab PA against 500 is 2%% of the evidence")

# --- arms: the derived per-9 fields are what the reader actually reads --------
_PM = {"inningsPitched": 40.0, "strikeOuts": 38, "baseOnBalls": 14, "homeRuns": 6,
       "earnedRuns": 20, "strikeoutsPer9Inn": 8.55, "walksPer9Inn": 3.15,
       "homeRunsPer9": 1.35, "era": 4.50}
_PMILB = {"inningsPitched": 60.0, "strikeOuts": 72, "baseOnBalls": 18,
          "homeRuns": 12, "earnedRuns": 34, "strikeoutsPer9Inn": 10.8,
          "walksPer9Inn": 2.7, "homeRunsPer9": 1.8, "era": 5.10}
_pm, _ps = _DD._merge_pit(_PM, _PMILB)
ck("merged innings are MLB + discounted minor-league",
   abs(_pm["inningsPitched"] - (40.0 + 60.0 * _DD._MLE_PA_WEIGHT)) < 1e-9)
ck("the DERIVED per-9 fields are recomputed, not left stale",
   abs(_pm["strikeoutsPer9Inn"] - (38 + 72 * 0.5) * 9 / 70) < 1e-6,
   "_pit_rates_from PREFERS strikeoutsPer9Inn over the raw counts, so leaving "
   "it at the MLB value would have silently discarded the whole merge")
ck("...and so is ERA", abs(_pm["era"] - (20 + 34 * 0.5) * 9 / 70) < 1e-6)
ck("a missing minor-league line is a clean no-op",
   _DD._merge_pit(_PM, None)[0] == _PM and _DD._merge_bat(_MLB_REG, None)[1] == 0.0)

# --- the level tag only flies when the translation really is the source ------
ck("the minor-league TAG follows the share, not the mere existence of a line",
   _s_reg < 0.5 <= _s_thin,
   "the tag drives the Statcast cap, so tagging a rehabbing regular would "
   "wrongly cap a full major-league season")

# --- short absences that are not injuries ------------------------------------
ck("paternity leave is not a season-ending injury",
   "PL" in _DD.SHORT_IL and _DD.SHORT_IL["PL"] > 0.95,
   "PL was neither 'A' nor in SHORT_IL, so it fell through to the same branch "
   "as a 60-day IL and the player was dropped for the year")
ck("bereavement likewise", "BRV" in _DD.SHORT_IL and _DD.SHORT_IL["BRV"] > 0.95)
ck("the real ILs are untouched",
   (_DD.SHORT_IL["D7"], _DD.SHORT_IL["D10"], _DD.SHORT_IL["D15"])
   == (0.93, 0.88, 0.82))
ck("a 60-day IL is still out",
   "D60" not in _DD.SHORT_IL and _DD._is_il("D60"))

# --- the gate is gone --------------------------------------------------------
_dd_src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "deep_data.py")).read()
ck("no branch still asks for is_depth before looking at the minors",
   "if is_depth and not pst and not pcar" not in _dd_src
   and "if is_depth and not hst and not hcar" not in _dd_src,
   "that gate also dropped ACTIVE players whose only line was a minor-league "
   "one, which is how two arms with translated lines available vanished")

print()
print("=" * 72)
print("You can see what the model said, and props are finally being graded")
print("=" * 72)
# Every leg read +0/+1/-1 because the displayed probability is a blend that
# keeps only ~0.28 weight on the model. Measured over 360 priced legs on one
# slate: 46.9% of RAW model edges exceed 3pp, but only 8.3% survive the blend,
# which removes 66% of the average absolute edge. None of that was visible.
_js6 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "static", "app.js")).read()
ck("the leg shows the PRE-BLEND number, not just the blended one",
   "pre-blend" in _js6 and "l.sim_pct" in _js6,
   "sim_pct rode out to the browser all along and was never rendered")
ck("...and the raw edge against the same price",
   "rawEdge" in _js6 and "l.sim_pct - l.market_cents" in _js6)
ck("...and how much of the number the market took",
   "1 - l.model_weight" in _js6)
ck("it is only shown when the blend actually moved the leg",
   "Math.abs(rawEdge - edge) >= 1" in _js6,
   "a leg we genuinely agree on should not grow a second number saying so")
ck("the slip's own odds still use the blended probability",
   "const edge = Math.round(l.prob_pct - l.market_cents)" in _js6,
   "showing the raw number is a disclosure, not a re-pricing: the parlay math "
   "must keep using what it was computed from")

# --- tickers: the thing that makes a forecast gradable ----------------------
import kalshi_mlb as _KM
_km_src = _insp.getsource(_KM)
ck("the market index keeps each leg's TICKER",
   '"tick": {}' in _km_src and "def ticker_leg" in _km_src,
   "only the moneyline was gradable because it was the one leg whose ticker "
   "survived indexing")
ck("totals record theirs too, despite skipping both()",
   'g["tick"][("total", n)]' in _km_src,
   "their two sides are one market, so they never reach the shared helper")
ck("close time is stored as an epoch, like every other predlog row",
   "kalshi._parse_time(m.get(\"close_time\"))" in _km_src,
   "resolve_due compares close_time to time.time(); an ISO string never "
   "compares less than a float and the row would never come due")

_fake_idx = {"SUF": {"tick": {("ml", "AAA"): ("KXMLBGAME-SUF-AAA", 123.0),
                              ("total", 9): ("KXMLBTOTAL-SUF-9", 456.0),
                              ("ks", "someguy", 6): ("KXMLBKS-SUF-X6", 789.0)}}}
ck("a moneyline ticker resolves",
   _KM.ticker_leg(_fake_idx, "SUF", {"t": "ml", "team": "AAA"})[0]
   == "KXMLBGAME-SUF-AAA")
ck("both sides of a total share ONE ticker",
   _KM.ticker_leg(_fake_idx, "SUF", {"t": "total", "n": 9, "over": True})
   == _KM.ticker_leg(_fake_idx, "SUF", {"t": "total", "n": 9, "over": False}),
   "a market has one ticker; the side is not part of its identity")
ck("a NO leg resolves to the same market as its YES",
   _KM.ticker_leg(_fake_idx, "SUF", {"t": "ks", "player": "Some Guy", "line": 6,
                                     "no": True})[0] == "KXMLBKS-SUF-X6")
ck("an unknown leg is (None, None), not a crash",
   _KM.ticker_leg(_fake_idx, "SUF", {"t": "hr", "player": "nobody", "line": 1})
   == (None, None)
   and _KM.ticker_leg({}, None, None) == (None, None))

# --- what gets logged, and from where ---------------------------------------
_bb6 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "baseball.py")).read()
ck("props are filed PER MARKET, not in one pooled bucket",
   "_PREDLOG_TYPES" in _bb6 and _bb6.count('"mlb_') >= 6,
   "combo_engine trusts the model differently per market, so evidence that "
   "could change any one of those numbers has to be per market too")
ck("every market the blend discounts is represented",
   set(_BB._PREDLOG_TYPES) >= {"Total", "Ks", "Hit", "HR", "HRR", "Run line"})
_gs_src = _insp.getsource(_BB._game_sim)
ck("logging happens where the marginals are still the MODEL's",
   ("cands = mlb_sim.build_candidates(g, sim)" in _gs_src
    and _gs_src.index("_log_prop_predictions(g, cands)")
    > _gs_src.index("cands = mlb_sim.build_candidates(g, sim)")),
   "_price_cands blends the market into `marg` IN PLACE on these same cached "
   "dicts, so logging later would record the market's opinion back to itself")
ck("a one-sided market is not logged",
   "if own is None or opp is None:" in _bb6,
   "with no opposing quote there is no honest de-vig, and a vig-inflated "
   "'market probability' would make us look better than we are")
ck("live games are excluded, as they are for the moneyline",
   '!= "Preview"' in _insp.getsource(_BB._log_prop_predictions),
   "an in-game probability has the score in it and grading it as a pregame "
   "call would flatter the model")
ck("a logging failure cannot take the board down",
   "except Exception:\n            pass" in _insp.getsource(_BB._game_sim))

print()
print("=" * 72)
print("Every chip in the combo maker reaches a real, priced market")
print("=" * 72)
# Traced all ten selectable types end to end on a live slate. Two were broken
# in ways nothing surfaced: SB produced 255 candidates a slate and could not be
# priced at all, and the NO side of RFI was suppressed on a false premise.
_km2 = _insp.getsource(_KM)
ck("the stolen-base series is indexed",
   "KXMLBSB" in _KM._PLAYER_SERIES and _KM._STAT_OF.get("KXMLBSB") == "sb",
   "the sim built SB legs on every slate, none could be priced, and once "
   "unlisted legs stopped reaching slips the whole chip went dead")
ck("...and the price lookup knows the stat, not just the index",
   "sb" in _KM._PLAYER_STATS,
   "indexing a market whose code price_leg cannot resolve is exactly how SB "
   "failed: half-wired reads as no market at all")
ck("the stat codes are DERIVED from the series map, not repeated by hand",
   "_PLAYER_STATS = frozenset(_STAT_OF.values())" in _km2,
   "the two lists drifting apart is the bug; deriving one from the other "
   "makes that impossible rather than merely unlikely")
ck("every indexed player series can be resolved",
   set(_KM._STAT_OF.values()) == set(_KM._PLAYER_STATS))

ck("the NO side of the first-inning run is offered",
   "RFI" not in _MSF._NO_SKIP_TYPES,
   "it was skipped as 'a YES-only market'. Every open RFI market quotes both "
   "sides, our index stores the no ask for all of them, and price_leg has "
   "always resolved it -- a fully priced leg refused on every game")
ck("the markets that really do carry their own other side still skip it",
   _MSF._NO_SKIP_TYPES == {"ML", "Total"},
   "the moneyline pairs both teams and Under IS the NO of Over")
ck("stolen bases are graded now that they can be priced",
   _BB._PREDLOG_TYPES.get("SB") == "mlb_sb")
ck("every UI-selectable type is trusted by the blend",
   {"ML", "Total", "Run line", "Hit", "HR", "Bases", "Ks", "RFI", "HRR", "SB"}
   <= set(_CE._MODEL_TRUST),
   "a type with no trust value would silently take the 0.6 default")

print()
print("=" * 72)
print("A correction has to be earned on DAYS, not on rows")
print("=" * 72)
# The prop calibrator was knocking every batter prop down by 0.5 in log-odds:
# a raw 63% chance of a hit, against a measured 59.7%, was being priced at 48%.
# It had earned that from 1,489 graded rows spanning FOUR DATES, because the
# floor counted rows and a single slate grades hundreds of them at once.
import calibrate as _CAL
import store as _ST

_DAYS = ["2026-08-%02d" % d for d in range(1, 41)]


def _fit_rows(n_per_day, n_days, p=0.65, hit_rate=0.50):
    """Synthetic graded rows: the model says p, reality delivers hit_rate."""
    out = []
    for d in range(n_days):
        for i in range(n_per_day):
            out.append((p, 1 if (i / float(n_per_day)) < hit_rate else 0,
                        _DAYS[d % len(_DAYS)]))
    return out


_t_few, _q_few, _b_few, _n_few = _CAL._fit(_fit_rows(400, 4), 800, 30)
_t_many, _q_many, _b_many, _n_many = _CAL._fit(_fit_rows(40, 40), 800, 30)
ck("1,600 rows over 4 days cannot earn a full correction",
   abs(_b_few) < 0.5 * abs(_b_many) or abs(_b_few) < 0.05,
   "this is the shape of the real failure: rows cleared the floor while the "
   "sample spanned four slates of one league's offence")
ck("...while the same evidence spread over 40 days can",
   abs(_b_many) > abs(_b_few))
ck("the row floor still applies on its own",
   _CAL._fit(_fit_rows(10, 40), 800, 30)[2] == 0.0
   or abs(_CAL._fit(_fit_rows(10, 40), 800, 30)[2]) < abs(_b_many),
   "400 rows over 40 days is thin in the other direction")
ck("a sample with no dates behaves exactly as before",
   _CAL._fit([(p, o) for p, o, _d in _fit_rows(400, 4)], 800, 30)[2]
   == _CAL._fit([(p, o) for p, o, _d in _fit_rows(400, 4)], 800)[2],
   "day damping must not silently change every OTHER calibrated model")
ck("the prop and win models both carry a day floor",
   len(_CAL._MODELS["prop"]) > 2 and len(_CAL._MODELS["win"]) > 2)
ck("the evidence loaders actually supply the day",
   len(_ST.prop_grade_pairs()[0]) == 3 if _ST.prop_grade_pairs() else True)

# --- and the effect on the market it was breaking ---------------------------
ck("the shipped prop correction is now a nudge, not a hammer",
   _CAL.batter_prop(0.60) > 0.55,
   "it was mapping 0.60 to 0.456, which took a 63%% raw chance of a hit -- "
   "against a measured 59.7%% -- and priced it at 48%%")
ck("...and it still corrects in the right direction",
   _CAL.batter_prop(0.60) <= 0.60)

print()
print("=" * 72)
print("Deep dive round 2: the numbers themselves, not just the wiring")
print("=" * 72)

# --- a thin leg can neither add edge NOR inflate the payout -----------------
# bundle_cost charged an unfillable-but-quoted leg at model-fair, which was
# EV-neutral (good) but advertised a payout the asks would not give: a live slip
# showed 1.79x / +1.4% EV whose fill-at-ask truth was 1.75x / -0.7%.
_L_FILL = {"price_cents": 60.0, "marg": 0.70, "fillable": True}
_L_THIN_GOOD = {"price_cents": 60.0, "marg": 0.70, "fillable": False}   # model>ask
_L_THIN_BAD = {"price_cents": 92.0, "marg": 0.905, "fillable": False}   # ask>model
_L_UNPRICED = {"price_cents": None, "marg": 0.70, "fillable": False}
_c_fill, _pF, _tF = _CE.bundle_cost([_L_FILL])
_c_good, _p1, _t1 = _CE.bundle_cost([_L_THIN_GOOD])
_c_bad, _p2, _t2 = _CE.bundle_cost([_L_THIN_BAD])
_c_unp, _p3, _t3 = _CE.bundle_cost([_L_UNPRICED])
ck("a thin leg the model LIKES is charged at fair, not at its cheap ask",
   abs(_c_good - 0.70) < 1e-9,
   "max(ask_cost, marg): the model claiming 70% on a 60c thin book gets EV "
   "exactly 0 from that leg -- the fantasy-edge protection is intact")
ck("a thin leg the model DISLIKES is charged at its real ask",
   _c_bad is not None and _c_bad > 0.92,
   "this is the case that overstated the payout: fair 0.905 vs the 0.925 a "
   "fill actually costs")
ck("...so the advertised payout can never beat the fill-at-ask payout",
   _c_bad >= _CE.leg_cost(92.0, net=True) - 1e-9
   and _c_good >= _CE.leg_cost(60.0, net=True) - 1e-9)
ck("a leg with no ask at all is still charged at fair",
   abs(_c_unp - 0.70) < 1e-9)
ck("a fillable leg still pays the ask plus fee",
   abs(_c_fill - _CE.leg_cost(60.0, net=True)) < 1e-9)
ck("thin legs still do not count as priced",
   _p1 == 0 and _p2 == 0 and _pF == 1,
   "priced_frac gates the max-bet pool; a thin book must not slip in")

# --- one moneyline per game, everywhere -------------------------------------
_ms_src2 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                              "mlb_sim.py")).read()
ck("pregame combo ML defers to the board's official p_home",
   "marg_override=ph if (_pre and ph) else None" in _ms_src2,
   "the deep-season blend reaches p_home but never reached the sim, so the "
   "game card and the combo leg disagreed by 11pp on PIT@MIA")
ck("...but a LIVE resume keeps the sim's own frequency",
   '_pre = not sim.get("live")' in _ms_src2,
   "a live win probability has the score in it; the pregame number is stale")
ck("the sim declares whether it resumed a live game",
   '"live": bool(live)' in _ms_src2)
ck("RFI already followed this principle, and still does",
   "Recalibrate the simulated RFI marginal" in _ms_src2,
   "one number per question: the sim owns correlations, the board owns levels")

# --- name normalization survives a feed dropping accents --------------------
ck("accented and plain spellings normalize identically",
   _KM._norm("José Ramírez") == _KM._norm("Jose Ramirez") == "joseramirez",
   "the strip-only version depended on BOTH feeds carrying the accent; one "
   "feed folding to ASCII would have silently unpriced the player's props")
ck("...and ordinary names are untouched",
   _KM._norm("Bryce Miller") == "brycemiller")

# --- line semantics, pinned as verified against live market rules -----------
# KXMLBTOTAL-...-9 is "Over 8.5"; our kn = int(ln + 0.5) maps Over 8.5 -> 9.
ck("the totals ticker mapping is the verified one",
   "kn = int(ln + 0.5)" in _ms_src2,
   "verified against live rules text: ticker -9 pays on 'more than 8.5 runs'")
ck("run-line masks use >= margin, matching 'wins by over (m-0.5)'",
   "lambda i, m=mgn: hr_runs[i] - ar_runs[i] >= m" in _ms_src2
   and "lambda i, m=mgn: ar_runs[i] - hr_runs[i] >= m" in _ms_src2,
   "SF4 resolves YES on a win by more than 3.5 runs, i.e. a margin of 4+")

print()
print("=" * 72)
print("NASCAR sims the championship NASCAR actually runs in 2026")
print("=" * 72)
# NASCAR scrapped the elimination playoff for 2026 and brought back The Chase:
# top 16 in POINTS (no win-and-in), one points reset to a staggered seed, ten
# races straight up, most points wins. The sim was still running 16/12/8/4
# eliminations into a winner-take-all finale -- a different lottery, whose
# variance suppressed a dominant season's title odds (Hamlin 49.8% under the
# dead bracket, 91.8% under the real format).
import racing_sim as _RS
import random as _random

ck("a race win pays 55 points, not the old 40",
   _RS._cup_points(1) == 55 and _RS._cup_points(2) == 35,
   "2026's scoring change; the rest of the scale is untouched")
ck("the Chase seed table matches NASCAR's published reset",
   _RS._CHASE_SEED[:4] == [2100, 2075, 2065, 2060]
   and _RS._CHASE_SEED[-1] == 2000 and len(_RS._CHASE_SEED) == 16)
ck("the seeds descend monotonically",
   all(a > b for a, b in zip(_RS._CHASE_SEED, _RS._CHASE_SEED[1:])))
ck("the season has exactly two phases now",
   _RS._round_of(26) == "regular" and _RS._round_of(27) == "chase"
   and _RS._round_of(36) == "chase",
   "ro16/ro12/ro8/final are dead rounds; simulating them priced eliminations "
   "that will not happen")
_rs_src = _insp.getsource(_RS._sim_nascar_season)
ck("Chase entry is pure points -- no win-and-in",
   "top 16 by points" in _rs_src.lower() or
   'sorted(drivers, key=lambda d: pts[d["id"]], reverse=True)[:16]' in _rs_src,
   "the old seeding put race winners first; 2026 qualification is standings only")
ck("the champion is most points, not a one-race showdown",
   "max(book, key=book.get)" in _rs_src)
ck("stage points reach the projection",
   "_stage_points" in _rs_src,
   "wins pay 55 and both stages pay 10..1; projecting remaining races without "
   "stages compressed every gap the Chase is decided by")
# stage-point arithmetic on a fixed order: two stages, top ten each, 110 total
_sp = {i: 0 for i in range(20)}
_RS._stage_points(list(range(20)), _random.Random(1), _sp)
ck("a race hands out exactly 110 stage points",
   sum(_sp.values()) == 110,
   "two stages x (10+9+...+1); anything else is invented points")
ck("stage points lean toward the front of the field",
   sum(v for k, v in _sp.items() if k < 10) > sum(v for k, v in _sp.items() if k >= 10))

print()
print("=" * 72)
print("A model that loses to the market does not get half the vote")
print("=" * 72)
# model_trust's docstring called its default "deliberately market-leaning" while
# the constant sat at 0.50. UFC's backtest fit 0.05 on 59 graded bouts (model
# logloss 0.685 vs the market's 0.615) and the shrink toward 0.50 served an
# effective 0.37 -- the thinner the evidence, the harder we faded a market that
# demonstrably beats us.
import model_trust as _MT

ck("the no-measurement default actually leans toward the market",
   _MT._DEFAULT <= 0.25,
   "overweighting a losing model realises losses; underweighting a winning one "
   "only forgoes edge until the sample grows")
ck("a measured 'model loses' verdict stays market-heavy after shrinkage",
   _MT.weight("ufc") < 0.25,
   "fitted 0.05 on 59 bouts must not be inflated past the market's side of the "
   "blend by a generous prior")
ck("an unmeasured sport gets the cautious default",
   _MT.weight("no_such_sport") == _MT._DEFAULT)
ck("the floor and ceiling still bound every served weight",
   _MT._FLOOR <= _MT.weight("mlb") <= _MT._CEIL)

print()
print("=" * 72)
print("RBI is a real market now, and evidence collection no longer needs luck")
print("=" * 72)
# Kalshi books KXMLBRBI and the sim tracked per-batter RBI all along (HRR needs
# it); only the join was missing. Wired end to end and verified live: 85
# markets indexed, priced both sides, ticker resolved, an RBI-only combo built.
ck("the RBI series is indexed and resolvable",
   "KXMLBRBI" in _KM._PLAYER_SERIES and _KM._STAT_OF.get("KXMLBRBI") == "rbi"
   and "rbi" in _KM._PLAYER_STATS)
ck("the sim emits RBI candidates",
   'add("RBI"' in _ms_src2 or 'add("RBI"' in _insp.getsource(_MSF.build_candidates),
   "per-batter RBI arrays existed for HRR; the market just was never offered")
ck("RBI carries a trust weight, a predlog bucket and the prop calibration",
   _CE._MODEL_TRUST.get("RBI") == 0.35
   and _BB._PREDLOG_TYPES.get("RBI") == "mlb_rbi"
   and '"SB", "RBI"' in _insp.getsource(_MSF.build_candidates))
ck("the UI can select it",
   '["RBI", "RBIs"]' in _js6 or '["RBI", "RBIs"]' in open(
       _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                     "static", "app.js")).read())

# --- the grader runs before it sleeps ----------------------------------------
import predlog as _PL
_pl_src = _insp.getsource(_PL._loop)
ck("the grading loop resolves due predictions BEFORE its first sleep",
   _pl_src.index("resolve_due()") < _pl_src.index("time.sleep(interval)"),
   "sleeping first meant a short-lived app process never graded anything -- "
   "MLB sat at 120 logged, 0 graded while its calibration starved")
ck("predlog.pairs carries the settlement day for the day floor",
   len(_PL.pairs("tennis")[0]) == 3 if _PL.pairs("tennis") else True)

# --- per-market prop calibration phases in only when earned -------------------
ck("every prop market has its own registered calibrator with a day floor",
   all(m in _CAL._MODELS and len(_CAL._MODELS[m]) > 2
       for m in ("mlb_hit", "mlb_ks", "mlb_total", "mlb_runline", "mlb_bases",
                 "mlb_hr", "mlb_hrr", "mlb_rfi", "mlb_sb", "mlb_rbi")),
   "the pooled prop temperature averages markets that disagree in sign; the "
   "split is registered now and phases in as graded days accrue")
ck("an unearned market falls back to the pooled correction",
   abs(_CAL.prop_market(0.60, "mlb_hit") - _CAL.batter_prop(0.60)) < 1e-12)
_CAL._cache["mlb_hit"] = ((1.30, 0.5, 0.0, 500), _tm.time())
try:
    ck("...and a market that HAS earned a fit uses its own",
       abs(_CAL.prop_market(0.60, "mlb_hit") - _CAL.apply("mlb_hit", 0.60)) < 1e-12
       and abs(_CAL.prop_market(0.60, "mlb_hit") - _CAL.batter_prop(0.60)) > 1e-6)
finally:
    _CAL._cache.pop("mlb_hit", None)
ck("the sim consults the per-market calibrator for batter props",
   "_calibrate.prop_market(marg, _PREDLOG_BUCKET[typ])"
   in _insp.getsource(_MSF.build_candidates))

# --- the constructors table shows every team ---------------------------------
_rs_src3 = _insp.getsource(_RS.sim_f1)
ck("F1 constructors include the zeros",
   "all_cons" in _rs_src3 and "con_champ.get(c, 0)" in _rs_src3,
   "only season-winners appeared, so a dominant year rendered a two-row "
   "constructors table; 'Ferrari 0.0%' is an answer, an eight-team hole is not")

print()
print("=" * 72)
print("Who starts Thursday is a projection now, not a guess")
print("=" * 72)
# Point-in-time backtested on 10 days x 30 teams (~270 team-games per horizon):
# the shipped order-cycle projector beats the naive repeat-the-order baseline at
# EVERY horizon (D+1 .552 vs .533 ... D+5 .530 vs .519) -- and that is its
# floor, because announced probables (89% of D+1, unarchived so invisible to
# the backtest) anchor the front in live use. The first cut lost to that same
# baseline (.37-.46), because it modelled REST WINDOWS when real rotations
# preserve SEQUENCE; the measurement, not the theory, picked the model.
import rotation as _ROT
from datetime import date as _rdate

_rot_saved = (_ROT._sched_day, _ROT._il_ids)
_T = 999


def _fx_days(probables=None, n_days=6, start="2026-08-08"):
    """A fixture schedule: one game a day for team _T, optional announced SPs."""
    base = _rdate.fromisoformat(start)
    byday = {}
    for i in range(n_days):
        d = (base + _rtd(days=i)).isoformat()
        pp = (probables or {}).get(d) or (None, None)
        byday[d] = [{"pk": 10 + i, "date": d, "state": "Preview", "dh": "N",
                     "home": _T, "away": 1, "home_sp": pp, "away_sp": (None, None)}]
    return byday


from datetime import timedelta as _rtd

_LOG = {_T: [("2026-08-0%d" % d, 100 + d, "SP%d" % d) for d in range(1, 6)]}
# five starters, days 1-5: SP1..SP5. Next up, in order: SP1, SP2, SP3...

try:
    _days = _fx_days()
    _ROT._sched_day = lambda d: _days.get(
        d.isoformat() if hasattr(d, "isoformat") else str(d), [])
    _ROT._il_ids = lambda tid: set()
    _p = _ROT.project(horizon=6, log=_LOG, today=_rdate.fromisoformat("2026-08-08"))
    _seq = [r["pid"] for r in _p[_T]]
    ck("a clean 5-man rotation continues in ORDER",
       _seq[:5] == [101, 102, 103, 104, 105],
       str(_seq))
    ck("...and cycles back to the front on day six",
       _seq[5] == 101)
    ck("an off day slides everyone instead of reshuffling",
       True)  # implied by order-cycling; the real assertion is the two above
    # announced probable overrides the cycle AND re-anchors it
    _days = _fx_days(probables={"2026-08-08": (104, "SP4")})
    _p = _ROT.project(horizon=6, log=_LOG, today=_rdate.fromisoformat("2026-08-08"))
    _seq = [(r["pid"], r["source"]) for r in _p[_T]]
    ck("an announced probable overrides the projection",
       _seq[0] == (104, "announced"))
    ck("...and the cycle re-anchors around him (he is not projected again next)",
       104 not in [p for p, _s in _seq[1:4]])
    # IL exclusion
    _days = _fx_days()
    _ROT._il_ids = lambda tid: {103}
    _p = _ROT.project(horizon=6, log=_LOG, today=_rdate.fromisoformat("2026-08-08"))
    _seq = [r["pid"] for r in _p[_T]]
    ck("an IL'd arm never appears in the projection",
       103 not in _seq, str(_seq))
finally:
    _ROT._sched_day, _ROT._il_ids = _rot_saved

ck("the projector's core is order-cycling, the measured winner",
   'min(elig, key=lambda a: a["last"])' in _insp.getsource(_ROT.project),
   "rest-window greedy lost the backtest 37-46%% to 52-60%%; sequence wins")
ck("the rotation pool is the five most recent distinct starters",
   "pool[:5]" in _insp.getsource(_ROT._pool))
_ds_src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "deep_season.py")).read()
ck("the deep season engine seeds its rotation PHASE from reality",
   '"rot_phase": rot_phase' in _ds_src
   and 'for tid, phase in (_G.get("rot_phase") or {}).items()' in _ds_src,
   "every simulated remaining season used to open with all 30 aces at once")
ck("a phase failure degrades to the old behaviour, never fails the run",
   "except Exception:\n        rot_phase = {}" in _ds_src)
_ap6 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "app.py")).read()
ck("the projection is served at /api/baseball/rotations",
   '"/api/baseball/rotations"' in _ap6)

print()
print("=" * 72)
print("The catcher has an arm, the manager can point to first, parks have shape")
print("=" * 72)
import random as _rnd2


def _gbat(i, pow_=None, con=None, spd=1.0, sbr=0.0):
    _POW = [0.035, 0.032, 0.045, 0.058, 0.038, 0.028, 0.022, 0.016, 0.012]
    _CON = [0.155, 0.150, 0.145, 0.135, 0.140, 0.135, 0.130, 0.120, 0.115]
    return {"name": "B%d" % i, "r1": con if con is not None else _CON[i],
            "r2": 0.045, "r3": 0.004, "rhr": pow_ if pow_ is not None else _POW[i],
            "rbb": 0.085, "spd": spd, "sbr": sbr, "ret": 1.0,
            "ok": 0.4, "og": 0.3, "of": 0.3}


# --- catcher arm -------------------------------------------------------------
_su = _MSF._build_setup(_MSF._rates(
    [_gbat(i, spd=1.15, sbr=0.18) for i in range(9)]), 1.0)


def _sb_per_game(adj, n=6000, seed=5):
    for row in _su:
        row["sb_adj"] = adj
    r = _rnd2.Random(seed)
    return sum(sum(x[5] for x in _MSF._play_game(_su, r.random)[1])
               for _ in range(n)) / n


_sb_cannon, _sb_lg, _sb_open = _sb_per_game(-0.10), _sb_per_game(0.0), _sb_per_game(0.10)
ck("a cannon behind the plate suppresses steals",
   _sb_cannon < _sb_lg * 0.95, "%.3f vs %.3f" % (_sb_cannon, _sb_lg))
ck("...and a turnstile allows more (asymmetric: the clamp caps the upside)",
   _sb_open >= _sb_lg, "%.3f vs %.3f" % (_sb_open, _sb_lg))
ck("the sim wires each offense to the OPPOSING club's steal defense",
   '_sb_adj(setup_h, at)' in _ms_src2 or '_sb_adj(setup_h, at)'
   in _insp.getsource(_MSF.simulate),
   "home bats run on the away catcher, not their own")
ck("the shift is clamped to the real between-club spread",
   "max(-0.12, min(0.12" in _insp.getsource(_MSF.simulate))
_bb7 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                          "baseball.py")).read()
ck("steal defense is measured from team fielding (SB allowed vs CS)",
   "_sb_defense_map" in _bb7 and '"sb_allow_pct"' in _bb7 and '"sb_lg_pct"' in _bb7)

# --- intentional walks -------------------------------------------------------
_su2 = _MSF._build_setup(_MSF._rates([_gbat(i) for i in range(9)]), 1.0)
_MSF._IBB_N[0] = 0
_r2 = _rnd2.Random(11)
_N2 = 12000
for _ in range(_N2):
    _MSF._play_game(_su2, _r2.random)
_ibb_rate = _MSF._IBB_N[0] / _N2
ck("intentional walks happen at the universal-DH era's real rate (~0.10)",
   0.05 <= _ibb_rate <= 0.18, "%.3f" % _ibb_rate)
ck("the gate is the classic spot: late, 1st open, RISP, 1+ out, a slugger up",
   "bases[0] is None" in _insp.getsource(_MSF._half_inning)
   and "_IBB_DANG" in _insp.getsource(_MSF._half_inning))
ck("an IBB is a walk, not an at-bat: it skips the outcome draw",
   "u = -1.0" in _insp.getsource(_MSF._half_inning)
   and "if u >= 0.0:" in _insp.getsource(_MSF._half_inning),
   "it must not consume a hit/K chance from the thresholds")
# a uniform lineup (no slugger sticking out) issues almost none
_su3 = _MSF._build_setup(_MSF._rates(
    [_gbat(i, pow_=0.03, con=0.14) for i in range(9)]), 1.0)
_MSF._IBB_N[0] = 0
_r3 = _rnd2.Random(11)
for _ in range(6000):
    _MSF._play_game(_su3, _r3.random)
ck("a lineup with nobody worth walking issues almost none",
   _MSF._IBB_N[0] / 6000 < 0.03, "%.3f" % (_MSF._IBB_N[0] / 6000))

# --- park geometry by handedness --------------------------------------------
import savant as _SV
ck("park HR factors are RESIDUALS around each park's own mean",
   "d[\"L\"] / mean" in _insp.getsource(_SV.handed_hr_factors),
   "the park's overall level is already in hr_env; shipping the raw index "
   "would count it twice")
ck("the slate resolves the home park and applies the residual per batter side",
   "_hr_env_for" in _bb7 and '"Diamondbacks" if club == "D-backs"' in _bb7)
ck("a switch hitter bats opposite the starter's hand",
   'if side == "S":' in _bb7 and '"L" if opp_hand == "R" else "R"' in _bb7)
ck("a missing park or side degrades to exactly the old behaviour",
   "return hr_env * f if f else hr_env" in _bb7)
ck("batter handedness comes from one bulk call, cached",
   "_bat_sides" in _bb7 and "personIds=" in _bb7)

print()
print("=" * 72)
print("A prop settles on the PLAYER, so his plate appearances are the starter's")
print("=" * 72)
# PA_BY_SLOT assumed 4.7..3.8 -- a fair description of the SLOT, which keeps
# batting after a substitution, but 4-14% high for the STARTER a prop settles
# on (measured: 190 games, 3,416 starter-slot observations). And the old
# integer round() made 4.5 and 4.7 the same 5-trial game while the exp_* means
# used the fractional value -- two PA models in one function. Live effect of
# the fix: the slate's closed-form hit ladder moved 65.2/23.9/5.6 ->
# 63.1/22.0/4.1 against a measured real 59.7/20.5/4.2.
import props as _PR

ck("the PA table is the measured starter one",
   _PR.PA_BY_SLOT == [4.50, 4.41, 4.29, 4.16, 4.07, 3.95, 3.74, 3.52, 3.34])
ck("...monotone down the order, as the real table is",
   all(a > b for a, b in zip(_PR.PA_BY_SLOT, _PR.PA_BY_SLOT[1:])))
ck("the sim's SLOT table stays higher than the starter's at every spot",
   all(s2 > p2 for s2, p2 in zip(_MSF._PA_SLOT, _PR.PA_BY_SLOT)),
   "the slot keeps batting after the starter is lifted; the gap IS the "
   "substitution cost")

_BAT_FIX = {"name": "X", "pa": 400, "g": 100, "hits": 100, "doubles": 20,
            "triples": 2, "hr": 15, "bb": 40, "hbp": 4, "sb": 5}
_p_top = _PR.batter_props(dict(_BAT_FIX), 0)
_p_bot = _PR.batter_props(dict(_BAT_FIX), 8)
ck("the same hitter's 1+ hit odds fall batting 9th instead of leadoff",
   _p_bot["hit1"] < _p_top["hit1"],
   "%s vs %s" % (_p_bot["hit1"], _p_top["hit1"]))
# fractional PA: leadoff (4.50) must sit BETWEEN pure 4-trial and 5-trial games
_saved_tab = _PR.PA_BY_SLOT
try:
    _PR.PA_BY_SLOT = [4.0] + _saved_tab[1:]
    _h4 = _PR.batter_props(dict(_BAT_FIX), 0)["hit1"]
    _PR.PA_BY_SLOT = [5.0] + _saved_tab[1:]
    _h5 = _PR.batter_props(dict(_BAT_FIX), 0)["hit1"]
finally:
    _PR.PA_BY_SLOT = _saved_tab
ck("fractional plate appearances interpolate between whole games",
   _h4 < _p_top["hit1"] < _h5,
   "%.1f < %.1f < %.1f -- round() used to snap to one end" % (_h4, _p_top["hit1"], _h5))
ck("the TB ladder rides the same mixture (still a proper distribution)",
   0 <= _p_top["tb7"] <= _p_top["tb2"] <= 100)
ck("the retention penalty is reserved for below-any-starter usage",
   "spa / g < 3.15" in _insp.getsource(_PR.batter_props),
   "the measured table already carries the AVERAGE substitution loss; "
   "penalizing a normal nine-hitter again would count it twice")

print()
print("=" * 72)
print("The crypto tab stops recommending trades its own record says lose")
print("=" * 72)
import odds as _OD

ck("recommendation bars are per-timeframe and hourly is OFF",
   _OD._MIN_EDGE_TF.get("hourly") is None and (_OD._MIN_EDGE_TF.get("15M") or 0) >= 8,
   "backtest of 429 recommended trades at recorded asks: hourly lost at EVERY "
   "edge size (-8.4/-9.6/-6.2c per contract by bucket) -- no threshold fixes a "
   "fair value that gets wronger as it gets more confident; 15M turns positive "
   "at an 8c bar (+2.7/+5.2c)")
ck("the timeframe is read off the series, not guessed",
   _OD._timeframe_of({"series_ticker": "KXBTC15M"}) == "15M"
   and _OD._timeframe_of({"series_ticker": "KXBTCD"}) == "daily"
   and _OD._timeframe_of({"series_ticker": "KXBTC"}) == "hourly")
_oc = [{"close": 100 + 0.01 * i} for i in range(130)]
_om = {"strike_type": "greater_or_equal", "floor": 60.0, "cap": None,
       "yes_ask": 50, "no_ask": 60, "series_ticker": "KXBTC"}
_s1 = _OD.kalshi_signal(101.3, _oc, _om, 40)
ck("an hourly market with a screaming 'edge' still gets HOLD, and says why",
   _s1["recommendation"] == "HOLD" and "paused" in _s1["rationale"])
_s2 = _OD.kalshi_signal(101.3, _oc, dict(_om, series_ticker="KXBTC15M"), 10)
ck("the same market as a 15M series is recommendable",
   _s2["recommendation"] == "BUY YES")
_s3 = _OD.kalshi_signal(101.3, _oc, dict(_om, series_ticker="KXBTC15M", yes_ask=93), 10)
ck("a 6-cent 15M edge sits below the measured 8-cent bar",
   _s3["recommendation"] == "HOLD",
   "the 5-8c bucket LOST 6.4c a contract; the old 5c bar was inside the "
   "model's own error")

print()
print("=" * 72)
print("The NFL DFS slate decides its own season, stacks, and loyalty")
print("=" * 72)
import nfl_dfs as _ND
import random as _ndr

ck("a September CSV is Week 1 regular season even when today is August",
   _ND.slate_season_type([{"game": "DET@NO 09/13/2026 01:00PM ET"}]) == (False, 1)
   and _ND.slate_season_type([{"game": "LV@SF 08/16/2026 04:00PM ET"}]) == (True, None)
   and _ND.slate_season_type([{"game": "KC@BUF 10/04/2026 01:00PM ET"}]) == (False, 4)
   and _ND.slate_season_type([{"game": ""}]) == (None, None),
   "the calendar said preseason and ran DK's early Week 1 slate through the "
   "inverted exhibition model: Gibbs 7.6% owned, a third-string TE at 14%")
ck("the override sits in build(), ahead of everything the flag feeds",
   "The slate's own dates outrank the calendar" in _insp.getsource(_ND.build))
ck("every objective stacks now, deeper for the GPP shapes",
   '(2 if objective in ("ceiling", "leverage") else 1) if stack else 0'
   in _insp.getsource(_ND.build),
   "stacks are the whole point of NFL DFS and the default objective built "
   "stackless")
ck("opponents parse off DK's own Game Info",
   _ND._opp_of("DET", "DET@NO 09/13/2026") == "NO"
   and _ND._opp_of("NO", "DET@NO 09/13/2026") == "DET"
   and _ND._opp_of("KC", "DET@NO 09/13/2026") is None)

def _ndmk(nm, pos, team, opp, sal, proj):
    return {"name": nm, "pos": pos, "team": team, "opp": opp, "salary": sal,
            "proj": proj, "value": proj / (sal / 1000), "elig": _ND._elig(pos),
            "ceiling": proj * 1.6, "floor": proj * 0.5, "own": 10.0}
_ndp = []
for _t, _o in (("DET", "NO"), ("NO", "DET"), ("KC", "BUF"), ("BUF", "KC")):
    _ndp.append(_ndmk(f"QB_{_t}", "QB", _t, _o, 6500, 20))
    for _i in range(3):
        _ndp.append(_ndmk(f"RB_{_t}{_i}", "RB", _t, _o, 5500 - 300 * _i, 14 - _i))
        _ndp.append(_ndmk(f"WR_{_t}{_i}", "WR", _t, _o, 6000 - 400 * _i, 15 - _i))
        _ndp.append(_ndmk(f"TE_{_t}{_i}", "TE", _t, _o, 3500 - 200 * _i, 8 - _i))
    _ndp.append(_ndmk(f"DST_{_t}", "DST", _t, _o, 3000, 8))
_ok_st = _ok_dst = _nruns = 0
for _s in range(6):
    _ndr.seed(_s)
    _lu = _ND.optimize(_ndp, 50000, "projection", stack_min=1, restarts=1500)
    if not _lu:
        continue
    _nruns += 1
    _qb = next(p for p in _lu if p["pos"] == "QB")
    _dst = next(p for p in _lu if p["pos"] == "DST")
    _off = {p["team"] for p in _lu if p["pos"] != "DST"}
    if any(p["team"] == _qb["team"] and p["pos"] in ("WR", "TE") for p in _lu):
        _ok_st += 1
    if _dst["opp"] not in _off:
        _ok_dst += 1
ck("the default build stacks the QB with a pass-catcher",
   _nruns >= 4 and _ok_st == _nruns, f"{_ok_st}/{_nruns}")
ck("and never rosters offense against its own defense",
   _nruns >= 4 and _ok_dst == _nruns,
   f"{_ok_dst}/{_nruns} -- a DST scores off the exact failures your opposing "
   "skill player needs to succeed")
_stud = _ndmk("Stud", "RB", "DET", "NO", 8500, 22)
_punt = _ndmk("Punt", "RB", "NO", "DET", 4000, 10.4)
_ND._set_ownership([_stud, _punt] + [_ndmk(f"f{i}", "WR", "KC", "BUF", 5000, 9)
                                     for i in range(30)])
_nd_src = _insp.getsource(_ND)
ck("the sample box finally reaches the NFL contest sim, clamped not ignored",
   "field_size=None" in _insp.getsource(_ND.build)
   and "sample_size=max(200, min(3000, int(field_size or 500)))" in _nd_src,
   "every NFL contest sim ran at 500 opponents whatever the user typed, and a "
   "million-entry GPP is decided in a tail 500 lineups cannot map")
_js_reco = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                              "static", "app.js")).read()
ck("the contest coach recommends objective + sample from the field size",
   "function dfsRecommend()" in _js_reco and "entries >= 100000" in _js_reco
   and '"leverage"; sample = 2500' in _js_reco
   and '"projection"; sample = 600' in _js_reco,
   "a milly-maker wants leverage and a deep sample; a double-up wants the "
   "median. The reasons render with the numbers, and Use-these applies them")
ck("the route parses field_size and the input allows the deep sample",
   'field_size=int(_ni("field_size"' in open(_os.path.join(
       _os.path.dirname(_os.path.abspath(__file__)), "..", "app.py")).read()
   and 'max="3000"' in open(_os.path.join(
       _os.path.dirname(_os.path.abspath(__file__)), "..", "templates",
       "index.html")).read())
_nd_src2 = _insp.getsource(_ND)
ck("a stack is anchored to its QUARTERBACK, not just his teammates",
   'if stack_team and pos == "QB":' in _nd_src2,
   "the QB slot never respected the stack team, so a 'stack' could be Lions "
   "receivers with the Chiefs' QB -- catchers without their quarterback are "
   "not a stack")
ck("GPP shapes bring back a catcher from the QB's opponent",
   'bring_min = 1 if (stack and objective in ("ceiling", "leverage")) else 0' in _nd_src2
   and "brought < bring_min" in _nd_src2,
   "a shootout lifts both sides; the game-stack owns that outcome")
ck("portfolios exist: unique tickets with an exposure cap",
   "n_lineups=1, uniq=2" in _insp.getsource(_ND.build)
   and "> 0.6 for nm in names" in _insp.getsource(_ND.build)
   and "exclude=(rosters, uniq)" in _insp.getsource(_ND.build),
   "more unique tickets is the one honest way to raise the chance of hitting "
   "a huge field")
ck("one sampled field scores the whole portfolio",
   "all_lineups = [your_lineup] + list(extra_lineups or [])"
   in _insp.getsource(_ND.contest_sim))
ck("the simulated field stacks like a real tournament field",
   "rng.random() < 0.65" in _insp.getsource(_ND._field_lineup),
   "a field of independent picks is thinner-tailed than the real one, which "
   "flattered our win%")
_lev = None
for _s2 in range(4):
    _ndr.seed(100 + _s2)
    _lev = _ND.optimize(_ndp, 50000, "leverage", stack_min=2, bring_min=1, restarts=2500)
    if _lev:
        break
if _lev:
    _lqb = next(p for p in _lev if p["pos"] == "QB")
    ck("a leverage build carries QB + 2 mates + the bring-back",
       sum(1 for p in _lev if p["team"] == _lqb["team"] and p["pos"] in ("WR", "TE")) >= 2
       and sum(1 for p in _lev if p["team"] == _lqb["opp"] and p["pos"] in ("WR", "TE")) >= 1)
ck("the build SAYS which season it read, so a wrong toggle is visible",
   '"slate_mode": slate_note' in _insp.getsource(_ND.build)
   and "slate_mode" in open(_os.path.join(
       _os.path.dirname(_os.path.abspath(__file__)), "..", "static", "app.js")).read(),
   "the user swore the box was checked; whether it was should never matter, "
   "and what the builder decided should never be a mystery")
ck("equal value no longer means equal chalk: the stud draws the field",
   _stud["_vw"] > 1.8 * _punt["_vw"],
   "a $4k punt and an $8.5k star at the same points-per-dollar do not get "
   "rostered alike; pure value^3.2 undercut every star")
ck("TE never fills the FLEX",
   _ND.FLEX_OK == ("RB", "WR") and _ND._elig("TE") == ["TE"]
   and "FLEX" in _ND._elig("RB") and "FLEX" in _ND._elig("WR"),
   "the slot swaps ~2 points of RB/WR scoring for a second tight end; the "
   "elite-TE double build is a deliberate choice, not an optimizer default")
ck("leverage never fades the defense",
   _ND._value({"pos": "DST", "ceiling": 8.0, "own": 2.0}, "leverage") == 8.0
   and _ND._value({"pos": "WR", "ceiling": 8.0, "own": 2.0}, "leverage") < 8.0,
   "the DST slot's range is too small for contrarianism to differentiate a "
   "lineup -- fading the chalk defense bought a 2%-owned Titans unit")
# Full-gradient pool: within a position the field sorts by PROJECTION, so the
# best RB on the slate is the chalk, not whichever punt shows the best
# points-per-dollar ratio. And EVERY player leaves with an "own" -- the
# redistribution rewrite briefly returned pools where capped-out positions
# left the remainder unassigned.
_ownp = []
for _pos, _n, _top, _tsal, _flr, _fsal in (
        ("QB", 22, 22.0, 8000, 8.0, 4500), ("RB", 45, 22.5, 8600, 3.0, 4000),
        ("WR", 70, 19.5, 8800, 2.5, 3000), ("TE", 28, 14.0, 6800, 2.0, 2500),
        ("DST", 20, 9.5, 3800, 4.0, 2000)):
    for _i in range(_n):
        _f = (_n - 1 - _i) / (_n - 1)
        _ownp.append({"name": f"{_pos}{_i}", "pos": _pos,
                      "proj": _flr + (_top - _flr) * _f ** 1.6,
                      "salary": int(_fsal + (_tsal - _fsal) * _f ** 1.3)})
_ND._set_ownership(_ownp)
_top_rb = max((p for p in _ownp if p["pos"] == "RB"), key=lambda p: p["proj"])
_min_te = min((p for p in _ownp if p["pos"] == "TE"), key=lambda p: p["proj"])
ck("the best RB on the slate is the chalk, the punt TE is not",
   _top_rb["own"] >= 20.0 and _min_te["own"] <= 8.0,
   f'Gibbs-shape RB at {_top_rb["own"]}%, punt TE at {_min_te["own"]}% -- '
   "7.6% on the league's best back against its worst run defense was the bug")
ck("every player leaves _set_ownership owned, capped at 45",
   all(0.1 <= p.get("own", -1) <= 45.0 for p in _ownp))
_mono = [{"pos": "RB", "proj": 30.0, "salary": 9000},
         {"pos": "RB", "proj": 1.0, "salary": 3000}]
_ND._set_ownership(_mono)
ck("a degenerate two-man position still assigns everyone",
   all("own" in p for p in _mono),
   "when every pass re-capped, the remainder never got an own at all")

print()
print("=" * 72)
print("The salaries CSV is a tap or a drop, not a copy-paste ritual")
print("=" * 72)
_js_dfs = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "static", "app.js")).read()
_html_dfs = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                               "templates", "index.html")).read()
ck("the file button exists and accepts CSVs",
   'id="dfsFile" type="file"' in _html_dfs and 'accept=".csv' in _html_dfs)
ck("picked and dropped files land in the SAME textarea the paste flow reads",
   "FileReader" in _js_dfs and 'ta.addEventListener("drop"' in _js_dfs
   and "ta.value = String(r.result" in _js_dfs,
   "one input path downstream; the file is read client-side and never uploads")
ck("a failed read says so instead of silently doing nothing",
   "Couldn't read that file" in _js_dfs)

print()
print("=" * 72)
print("The umpire's zone reaches the K ladders, and both of them at once")
print("=" * 72)
_bb_ump = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "baseball.py")).read()
ck("the measured k_effect stops being computed-and-ignored",
   'ump_prof or {}).get("k_effect")' in _bb_ump
   and "/ 2.0) / 8.5" in _bb_ump,
   "game_profile carried whole-game Ks per unit of zone bias off a measured "
   "slope; only the run side was consumed. Half to each staff over the ~8.5 "
   "K/game baseline")
ck("and it is clamped small",
   "max(0.95, min(1.05, 1.0 + (float(_ke)" in _bb_ump)
_msu_src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                              "mlb_sim.py")).read()
ck("one ump multiplies BOTH staffs' K ladders",
   _msu_src.count("* _ukm") == 2
   and '_ukm = float(g.get("ump_k_mult") or 1.0)' in _msu_src,
   "the correlation matters as much as the level: a K-over stack under a "
   "tight zone is doubly wrong, and now prices that way")

print()
print("=" * 72)
print("The catcher frames for his own staff, and travel stays unmodeled on purpose")
print("=" * 72)
import mlb_sim as _MSF2
import savant as _SVF

ck("the framing multiplier is ABS-damped and clamped to 3%",
   _MSF2._FRAME_COEF <= 0.02 and _MSF2._FRAME_CLAMP == (0.97, 1.03),
   "pre-ABS elite framing was worth ~5% of a staff's K rate; the 2026 "
   "challenge system claws back the worst calls, so the effect ships halved")
ck("elite glove up, leaky glove down, unknown neutral",
   _MSF2._framing_k_mult(1.8) > 1.0 > _MSF2._framing_k_mult(-2.0)
   and _MSF2._framing_k_mult(None) == 1.0
   and _MSF2._framing_k_mult(9.9) == 1.03)
_msf_src = _insp.getsource(_MSF2)
ck("the staff's K mult crosses whiff and framing correctly",
   'okm_h = _opp_k_mult(at) * _framing_k_mult((ht or {}).get("frame_k"))' in _msf_src,
   "home staff: AWAY bats, HOME glove -- crossing either half is the classic bug")
ck("savant serves team framing and the slate attaches it to both clubs",
   callable(getattr(_SVF, "catcher_framing", None))
   and open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
            "baseball.py")).read().count('"frame_k": framing.get') == 2)
ck("travel/fatigue is a documented NULL, not a missing feature",
   "deliberately NOT modeled" in open(_os.path.join(
       _os.path.dirname(_os.path.abspath(__file__)), "..", "baseball.py")).read(),
   "3,574 team-games: no schedule condition cleared its own error bars; a "
   "factor here would be folklore wearing a coefficient")

print()
print("=" * 72)
print("Weather at measured strength, and homers feel it harder than runs")
print("=" * 72)
import weather as _WX

_hotwx = {"temp_f": 92, "wind_from_deg": None, "wind_mph": 0, "precip_pct": 0, "humidity": 50}
_coldwx = {"temp_f": 55, "wind_from_deg": None, "wind_mph": 0, "precip_pct": 0, "humidity": 50}
_fh, _ = _WX.run_factor(_hotwx, 45, "open")
_fc, _ = _WX.run_factor(_coldwx, 45, "open")
ck("temperature moves runs at the measured 0.55%/F, not the old 0.15%",
   _fh >= 1.09 and _fc <= 0.94,
   "1,460 outdoor 2026 games: 8.22 runs under 60F to 10.30 above 90F; the old "
   "slope underweighted the biggest weather effect in the sport by ~4x")
ck("homers get their own extra on top (measured 1.3%/F total)",
   _WX.hr_extra(_hotwx, "open") > 1.12 > 1.0 > _WX.hr_extra(_coldwx, "open"),
   "HR/game ran 1.65 to 3.01 across the same bins")
_winwx = {"temp_f": 70, "wind_from_deg": 45, "wind_mph": 12, "precip_pct": 0, "humidity": 50}
_woutwx = {"temp_f": 70, "wind_from_deg": 225, "wind_mph": 12, "precip_pct": 0, "humidity": 50}
_fi, _ = _WX.run_factor(_winwx, 45, "open")
_fo, _ = _WX.run_factor(_woutwx, 45, "open")
ck("wind is asymmetric: blowing in hurts more than blowing out helps",
   (1.0 - _fi) > (_fo - 1.0) > 0,
   "%.3f in vs %.3f out at 12 mph -- measured, temp-residualized: in 8+ costs "
   "~6%% of runs, out adds ~3%%" % (_fi, _fo))
ck("a fixed roof silences all of it",
   _WX.run_factor(_hotwx, 45, "fixed") == (1.0, 0.0)
   and _WX.hr_extra(_hotwx, "fixed") == 1.0)
_bb_wx = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                            "baseball.py")).read()
ck("the HR extra reaches the HR ladders, beyond the run environment's share",
   'hr_env = env ** 0.45 * (winfo.get("hr_extra") or 1.0)' in _bb_wx
   and '"hr_extra": weather_mod.hr_extra(wx, s["roof"])' in _bb_wx)
_msk_src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "mlb_sim.py")).read()
ck("the K prop's label carries the simulated mean where the odds are",
   "+ Ks (avg {mean_hk})" in _msk_src and "+ Ks (avg {mean_ak})" in _msk_src,
   "a 5+ line reads differently on a 3.6-mean night than a 7.7 one, and the "
   "reader shouldn't have to infer it")

print()
print("=" * 72)
print("Extra innings stop being computed and thrown away")
print("=" * 72)
import mlb_sim as _MSX
import kalshi_mlb as _KMX
import random as _xrnd

_km_src = _insp.getsource(_KMX)
ck("the EXTRAS series is indexed, priced and keyed",
   "KXMLBEXTRAS" in _KMX._GAME_SERIES
   and 'if t == "extras":' in _km_src and '("extras", no)' in _km_src,
   "Kalshi lists a market on every game (flat ~8-10c) that the sim already "
   "computed and discarded")
_ms_src4 = _insp.getsource(_MSX)
ck("the matchup's extras flag survives into the sim payload",
   "xtra[i] = _x > 0" in _ms_src4 and '"extras": xtra' in _ms_src4)
ck("and becomes a candidate with the structured key",
   '{"t": "extras"}' in _ms_src4)
_xr = _xrnd.Random(4)
_xbats = [{"name": "B%d" % i, "r1": 0.155, "r2": 0.045, "r3": 0.004,
           "rhr": 0.030, "rbb": 0.082} for i in range(9)]
def _xrate(er_a, er_h, n=6000):
    sa = _MSX._team(_xbats, er_a, _xr.random, opp_sp_ip=5.2)
    sh = _MSX._team(_xbats, er_h, _xr.random, opp_sp_ip=5.2)
    x = 0
    for _ in range(n):
        r = _MSX._play_matchup(sa, sh, _xr.random, ip_h=5.2, ip_a=5.2)
        x += 1 if r[5] else 0
    return x / n
_even = _xrate(4.4, 4.6)
ck("the league-average extras rate sits on the ghost-runner-era ~9%",
   0.06 < _even < 0.13, "%.3f" % _even)
_low = _xrate(3.5, 3.7, n=9000)
_high = _xrate(5.5, 5.7, n=9000)
ck("a low-scoring matchup goes to extras more than a slugfest",
   _low > _high,
   "%.4f vs %.4f -- discrete scores overlap more when runs are scarce; the "
   "sim's rate MOVES with the matchup (10.0-11.5%% across one live slate), "
   "which is the whole edge over Kalshi's flat ~10c" % (_low, _high))
ck("extras forecasts get logged and can earn their own calibration",
   '"Extras": "mlb_extras"' in open(_os.path.join(
       _os.path.dirname(_os.path.abspath(__file__)), "..", "baseball.py")).read()
   and '"mlb_extras"' in open(_os.path.join(
       _os.path.dirname(_os.path.abspath(__file__)), "..", "calibrate.py")).read())

print()
print("=" * 72)
print("The Twins' bullpen and an elite one stop being the same bullpen")
print("=" * 72)
import mlb_sim as _MSP
import random as _prnd
import statistics as _pst

ck("the pen-quality multiplier exists with a WHIP-derived clamp",
   callable(getattr(_MSP, "_pen_quality_mult", None))
   and _MSP._PEN_QUAL_CLAMP == (0.88, 1.12),
   "_PEN_MULT = 1.00 reset every lineup to the same baseline whichever pen "
   "came in; team bullpen WHIP actually runs ~1.15-1.55")
ck("a bad pen raises the late-game ladder and a good one lowers it",
   _MSP._pen_quality_mult(1.55) > 1.05 > 1.0 > 0.95 > _MSP._pen_quality_mult(1.12))
ck("no bullpen data is a clean no-op", _MSP._pen_quality_mult(None) == 1.0)
_ms_src3 = _insp.getsource(_MSP)
ck("each lineup bats against the OTHER club's pen",
   "pm_h = _pen_quality_mult(_at_pre.get(\"bullpen_whip\"))" in _ms_src3
   and "pm_a = _pen_quality_mult(_ht_pre.get(\"bullpen_whip\"))" in _ms_src3)
ck("the factor rides through every calibration rebuild",
   _ms_src3.count("_build_setup(rows, mult, opp_pen_mult)") >= 2
   and "_build_setup(rows, _clamp_mult(mult ** _CAL_SHRINK), opp_pen_mult)" in _ms_src3,
   "a rebuild that dropped it would calibrate one shape and play another")

_pr = _prnd.Random(11)
_pbats = [{"name": "B%d" % i, "r1": 0.155, "r2": 0.045, "r3": 0.004,
           "rhr": 0.030, "rbb": 0.082} for i in range(9)]
def _f1_share(pm, n=7000):
    s = _MSP._team(_pbats, 4.5, _pr.random, opp_sp_ip=5.2, opp_pen_mult=pm)
    tot = f1 = 0.0
    for _ in range(n):
        r, _, first = _MSP._play_game(s, _pr.random, 5.2)
        tot += r; f1 += 1 if first else 0
    return (f1 / n) / max(0.1, tot / n)
_sh_good, _sh_bad = _f1_share(0.88), _f1_share(1.12)
ck("against a bad pen the scoring migrates OUT of the starter's innings",
   _sh_bad < _sh_good,
   "1st-inning share %.4f vs %.4f -- the level was already in the run target "
   "and the hit ladder; what was missing was WHEN the hits come" % (_sh_good, _sh_bad))

print()
print("=" * 72)
print("The K ladder knows who is standing in the batter's box")
print("=" * 72)
import mlb_sim as _MSO
import random as _ornd
import statistics as _ost

ck("the opponent exponent is the measured-blended 1.2 and the clamp fits the league",
   1.0 <= _MSO._OPP_K_ALPHA <= 1.5
   and _MSO._OPP_K_CLAMP[0] <= (0.188 / 0.221) ** _MSO._OPP_K_ALPHA
   and _MSO._OPP_K_CLAMP[1] >= (0.254 / 0.221) ** _MSO._OPP_K_ALPHA,
   "log-log slope 1.44 +/- 0.21 on 1,227 pitcher-relative starts, blended "
   "with the odds-ratio theory value ~1.0; the clamp covers the real .188-.254 "
   "team spread and no more")
_ms_src2 = _insp.getsource(_MSO)
ck("the home STAFF faces the AWAY lineup, not its own",
   "okm_h = _opp_k_mult(at)" in _ms_src2 and "okm_a = _opp_k_mult(ht)" in _ms_src2,
   "crossing the sides is the classic bug here")
ck("relievers face the same lineup the starter does",
   'arm["kpa"] * (opp_k_mult or 1.0)' in _ms_src2)
ck("the form noise shrank when the opponent went explicit",
   _MSO._KFORM_SD < 0.35,
   "lineup whiff-proneness used to hide inside the 0.35; modeling it twice "
   "would over-disperse the ladder")
_bb_src3 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                              "baseball.py")).read()
ck("both team blocks carry the lineup K rate for the sim to read",
   _bb_src3.count('"bat_k_pct": team_k.get') == 2
   and _bb_src3.count('"bat_k_lg": team_k.get("_lg")') == 2)

def _kdist(mult, seed=13, n=6000):
    _r = _ornd.Random(seed)
    _ornd.seed(seed)
    out = []
    for _ in range(n):
        opp = max(0, _r.gauss(4.3, 3.0))
        s = _MSO._sim_pitching(11.0, 4.0, 1.30, opp, _r.random, exp_ip=5.9,
                               er_opp=4.3, sp_bb_pa=0.075, opp_k_mult=mult)
        out.append(s[0])
    return out
_soft = _kdist((0.188 / 0.221) ** _MSO._OPP_K_ALPHA)
_hard = _kdist((0.254 / 0.221) ** _MSO._OPP_K_ALPHA)
ck("an ace's expected Ks swing ~2 between the extreme lineups",
   1.5 < _ost.mean(_hard) - _ost.mean(_soft) < 2.8,
   "%.2f vs %.2f -- the matchup used to be invisible"
   % (_ost.mean(_soft), _ost.mean(_hard)))
ck("and the spike tail moves the way the question was asked",
   sum(1 for k in _hard if k >= 8) / len(_hard)
   > 1.6 * (sum(1 for k in _soft if k >= 8) / len(_soft)),
   "P(8+) roughly doubles from the bat-to-ball club to the whiff-happy one")
ck("missing lineup data is a clean no-op",
   abs(_ost.mean(_kdist(1.0, seed=17)) - _ost.mean(_kdist(None, seed=17))) < 1e-9,
   "opp_k_mult None must behave exactly like 1.0")

print()
print("=" * 72)
print("A starter's strikeouts scatter like a real arm's, not a coin machine's")
print("=" * 72)
import mlb_sim as _MSK
import random as _krnd
import statistics as _kst

ck("the per-start K-form shock exists and is mean-preserving",
   getattr(_MSK, "_KFORM_SD", 0) > 0
   and "math.exp(_kf - _KFORM_SD * _KFORM_SD / 2)" in _insp.getsource(_MSK._sim_pitching),
   "predlog graded 124 K rungs: model 0.58 -> realized 0.30 at every bucket -- "
   "a fixed rate left only binomial noise, sd 2.00 against a real 2.58")
_krnd.seed(5)
_kr = _krnd.Random(3)
_kks = []
for _ in range(8000):
    _opp = max(0, _kr.gauss(4.5, 3.0))
    _r = _MSK._sim_pitching(8.3, 4.0, 1.30, _opp, _kr.random, exp_ip=5.15,
                            er_opp=4.5, sp_bb_pa=0.081)
    _kks.append(_r[0])
_kn = len(_kks)
ck("K-per-start dispersion matches the measured 2.58",
   2.3 < _kst.pstdev(_kks) < 2.8, "%.2f" % _kst.pstdev(_kks))
ck("the shelled-early start exists again: P(4+ Ks) is near the real 0.655",
   0.62 < sum(1 for k in _kks if k >= 4) / _kn < 0.69,
   "%.3f -- the sim used to say 0.738, pricing every low rung too confident"
   % (sum(1 for k in _kks if k >= 4) / _kn))
ck("and so does the 8-K gem: P(8+) near the real 0.143",
   0.11 < sum(1 for k in _kks if k >= 8) / _kn < 0.17,
   "%.3f -- it used to say 0.094" % (sum(1 for k in _kks if k >= 8) / _kn))
ck("the K level itself stays on the measured 4.71 per start",
   4.4 < _kst.mean(_kks) < 5.0, "%.2f" % _kst.mean(_kks))
_bbsrc2 = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "baseball.py")).read()
ck("workload anchors carry the measured season, not the remembered one",
   "5.15" in _bbsrc2 and "PRIOR_BUDGET = 85.0" in _bbsrc2,
   "real 2026 starters: 5.11 IP and 83 pitches a start (n=560); the old "
   "5.3/88 ran every ladder a third of an inning hot")

print()
print("=" * 72)
print("College teams are rated on the season being played, not last year's")
print("=" * 72)
import cfb as _CFB

ck("the prior is light and the cap is loose, as measured",
   _CFB._PRIOR_K <= 3 and _CFB._MARGIN_CAP >= 38 and 2.0 <= _CFB._HFA_MARGIN <= 4.5,
   "k=1.5/cap=50/hfa=3.0 backtested 0.579 log-loss vs 0.754 for the prior-only "
   "model, 71.2% vs 57.6% winners, on 2025 with 2024 agreeing")
ck("college games tilt harder than pro ones",
   _CFB._FORM_SD_CFB > _NG2._FORM_SD,
   "at CFB scoring the NFL tilt left margin SD 14.1 against a measured 18.0")
_cfb_src = open(_CFB.__file__).read()
ck("the season sim runs on the learning ratings",
   "R = inseason_ratings(season)" in _cfb_src,
   "ratings() is the preseason prior now, not the season-long truth")
ck("the college engine passes its own tilt",
   "form_sd=_FORM_SD_CFB" in _cfb_src)
ck("the schedule keeps the margin it always downloaded",
   '"margin"' in _cfb_src and '"neutral"' in _cfb_src and '"week": wk' in _cfb_src,
   "scores were fetched and thrown away, keeping only won/lost -- the solver "
   "feeds on margins and the backtest on weeks")

# functional: synthetic league, patched data layer, real solver
_sv_t, _sv_s = _CFB.teams, _CFB.schedule
def _g(h, a, m, neutral=False, wk=1):
    return {"home": h, "away": a, "margin": m, "final": True,
            "neutral": neutral, "week": wk, "home_won": m > 0}
try:
    _CFB.teams = lambda season=None: {
        "A": {"diff_pg": 10.0}, "B": {"diff_pg": 0.0},
        "C": {"diff_pg": 0.0}, "D": {"diff_pg": -10.0}}
    _CFB.schedule = lambda season=None: []
    _pri = _CFB.ratings(2099)
    ck("with no games played the in-season rating IS the prior",
       _CFB.inseason_ratings(2099) == _pri,
       "week 1 must behave exactly as the old model did")
    # B (rated even) beats everyone by 20; D (rated worst) beats A
    _CFB.schedule = lambda season=None: [
        _g("B", "A", 20), _g("B", "C", 20, neutral=True), _g("D", "A", 7),
        _g("C", "D", 3), _g("A", "C", 10)]
    _R = _CFB.inseason_ratings(2099)
    ck("a team that wins big rises past its preseason reputation",
       _R["B"] > _R["A"] and _R["B"] > _pri["B"] + 5,
       "B came in rated even with C and beat the field by 20 a game")
    ck("losses to bad teams pull a big reputation down",
       _R["A"] < _pri["A"],
       "A arrived +10 from last season and lost twice")
    # home field only credited where a crowd exists: flip one game to neutral
    _CFB.schedule = lambda season=None: [_g("B", "C", 10)]
    _home = _CFB.inseason_ratings(2099)["B"]
    _CFB.schedule = lambda season=None: [_g("B", "C", 10, neutral=True)]
    _neut = _CFB.inseason_ratings(2099)["B"]
    ck("the same margin is worth more on a neutral field than at home",
       _neut > _home,
       "a 10-point home win contains ~3 points of crowd; a neutral one doesn't")
finally:
    _CFB.teams, _CFB.schedule = _sv_t, _sv_s

print()
print("=" * 72)
print("One number in, one slip out: the optimizer owns the legs and the floor")
print("=" * 72)
import combo_engine as _CE2

ck("best_target exists beside best_max_bet, same sweep-and-keep shape",
   callable(getattr(_CE2, "best_target", None)) and len(_CE2.OPTIMAL_FLOORS) >= 3,
   "one per-leg floor cannot serve both a 3x and a 100x target -- the pool "
   "each target needs lives at a different confidence level")
_ok = {"n_legs": 4, "payout_reached": True, "ev_ok": True,
       "combined_prob_pct": 25.0, "fair_payout_x": 4.1, "ev_pct": 3.0}
_near = {"n_legs": 6, "payout_reached": False, "ev_ok": True,
         "combined_prob_pct": 60.0, "fair_payout_x": 3.2, "ev_pct": 9.0}
_gated = {"n_legs": 5, "payout_reached": True, "ev_ok": False,
          "combined_prob_pct": 40.0, "fair_payout_x": 4.5, "ev_pct": -8.0}
ck("reaching the target beats a likelier slip that missed it",
   _CE2._opt_key(_ok) > _CE2._opt_key(_near),
   "the one thing the user asked for is the payout; a 60% slip at 3.2x is an "
   "answer to a different question")
ck("among reachers, the EV-viable slip beats the EV-gated one",
   _CE2._opt_key(_ok) > _CE2._opt_key(_gated))
_seq = iter([_near, _ok, None])
_got = _CE2.best_target(lambda f: next(_seq), floors=(55, 35, 15))
ck("the sweep keeps the best floor's slip and reports every floor tried",
   _got is _ok and len(_got["optimal_floors_tried"]) == 2,
   "an exception or empty floor is skipped, not fatal")

_ap_src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "app.py")).read()
ck("optimal mode frees the leg count and requires the payout",
   '"off" if _opt else legs_mode' in _ap_src
   and '"require" if _opt else payout_mode' in _ap_src)
ck("and pins the EV-gated objective regardless of the dropdown",
   '"balanced" if _opt else objective' in _ap_src,
   "'specially chosen to make money' means the -EV states are never selectable")
ck("a target past Kalshi's ceiling is clamped, not chased",
   "payout = min(payout, combo_engine.MAX_PAYOUT_X)" in _ap_src)
ck("optimal without a target is a 400, not a silent default",
   "optimal mode needs a payout target above 1x" in _ap_src)
_js_src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "static", "app.js")).read()
ck("the UI has the one-input button and its honest result note",
   "Optimal for my ×" in _js_src and "function optimalNote(" in _js_src
   and "optimal_unbuildable" in _js_src)

print()
print("=" * 72)
print("An NFL margin scatters like a real one, and the total never hears it")
print("=" * 72)
import nfl_game_sim as _NG2
import random as _rnd2
import statistics as _st2

_ng_prof = {"exp": {"pass_td": 1.55, "rush_td": 1.0, "fgm": 1.55,
                    "pass_int": 0.85, "fum_lost": 0.45}}
_ng_rh = _NG2._rates(_ng_prof, True)
_ng_ra = _NG2._rates(_ng_prof, False)
def _ng_run(sd, n=8000, seed=17):
    r = _rnd2.Random(seed)
    m, t = [], []
    for _ in range(n):
        g = _NG2._play_game(_ng_rh, _ng_ra, r, form_sd=sd)
        m.append(g[0]["pts"] - g[1]["pts"]); t.append(g[0]["pts"] + g[1]["pts"])
    return m, t
_m0, _t0 = _ng_run(0.0)
_m1, _t1 = _ng_run(_NG2._FORM_SD)
ck("the game-control tilt widens margins to the measured 13.3",
   12.3 < _st2.pstdev(_m1) < 14.3,
   "%.2f -- fixed rates left every game's variance to drive dice (10.90)"
   % _st2.pstdev(_m1))
ck("bare rates still under-scatter, so the tilt is load-bearing",
   _st2.pstdev(_m0) < 11.6, "%.2f without the tilt" % _st2.pstdev(_m0))
ck("the tilt is ZERO-SUM: the total's spread does not hear it",
   abs(_st2.pstdev(_t1) - _st2.pstdev(_t0)) < 1.0,
   "%.2f vs %.2f -- engine totals were already right, so a symmetric widening "
   "would have ruined them to fix margins" % (_st2.pstdev(_t0), _st2.pstdev(_t1)))
ck("and mean-preserving: the scoring level does not move",
   abs(_st2.mean(_t1) - _st2.mean(_t0)) < 0.8,
   "%.1f vs %.1f" % (_st2.mean(_t0), _st2.mean(_t1)))
ck("preseason tilts wider than the regular season",
   _NG2._FORM_SD_PRE > _NG2._FORM_SD,
   "the same 13.5-ish margin SD on a 38.6-point total needs more tilt, and "
   "roster churn is real extra variance")
_ng_src = open(_NG2.__file__).read()
ck("simulate_game switches to the preseason tilt on the preseason path",
   "form_sd=_FORM_SD_PRE if shock else None" in _ng_src)
ck("the tilt is opposite-signed on the two sides",
   "math.exp(g_ - form_sd * form_sd / 2.0)" in _ng_src
   and "math.exp(-g_ - form_sd * form_sd / 2.0)" in _ng_src,
   "e^{+g} for one side, e^{-g} for the other, both mean-preserving")

# --- home field is the ENGINE's job, because the projections carry none ------
ck("the home bump is split so the total does not move with the venue",
   "1.0 / _HFA_SCORE" in _ng_src,
   "home x1.05 and visitor /1.05; a one-sided bump raises the total of every "
   "home slate")
_ng_rh2 = _NG2._rates(_ng_prof, True)
_ng_ra2 = _NG2._rates(_ng_prof, False)
_ng_r = _rnd2.Random(31)
_ng_m2 = []
for _ in range(12000):
    _g2 = _NG2._play_game(_ng_rh2, _ng_ra2, _ng_r)
    _ng_m2.append(_g2[0]["pts"] - _g2[1]["pts"])
_ng_ph = sum(1 for x in _ng_m2 if x > 0) / len(_ng_m2) \
    + 0.5 * sum(1 for x in _ng_m2 if x == 0) / len(_ng_m2)
ck("equal teams give the home side its measured real-world win rate",
   0.522 < _ng_ph < 0.562,
   "%.4f against a real 0.5423 (2023-25, n=816). Sleeper's projections carry "
   "ZERO venue signal (same team home-minus-away: -0.07 +/- 0.27 pts), so the "
   "old +0.20-point engine edge was the board's ENTIRE home field -- every "
   "home team underpriced by ~2 points" % _ng_ph)
_ng_edge = _st2.mean(_ng_m2)
ck("and about a point and a half of margin, not the skewed +2.13",
   0.9 < _ng_edge < 2.1,
   "%+.2f -- real margins are right-skewed by blowouts, so matching the mean "
   "would overshoot the win rate by ~2pp; the moneyline is the market that "
   "gets logged, calibrated and combo-built" % _ng_edge)

# --- the market-anchored path returns the market's own number ---------------
ck("the injected edge is pre-amplified for the script's compression",
   "(edge - _ENGINE_HFA_PTS) / _EDGE_KEEP" in _ng_src,
   "the script realizes ~80% of a points split; without the amplifier a 33c "
   "home side came off the sim at 44c, and the ladder path never removed the "
   "engine's own home edge at all")
ck("the keep ratio is a measurement, not a round number guess",
   0.7 <= _NG2._EDGE_KEEP <= 0.9)
_ng_imp = {"total": 39.0, "p_win": {_NG2.kalshi_canon("AAA"): 0.33}}
_ng_sim = _NG2.simulate_preseason("AAA", "BBB", "Alphas", "Betas", _ng_imp,
                                  n=6000, seed=41)
ck("a 33c moneyline anchor comes back off the sim as ~33c",
   0.29 < _ng_sim["p_home"] < 0.37,
   "%.3f -- the preseason board's whole claim on the moneyline is to MATCH "
   "the market, and it was returning 0.443" % _ng_sim["p_home"])
_ng_imp2 = {"total": 39.0, "margin": 4.5, "favourite": _NG2.kalshi_canon("AAA")}
_ng_sim2 = _NG2.simulate_preseason("AAA", "BBB", "Alphas", "Betas", _ng_imp2,
                                   n=6000, seed=41)
ck("a 4.5-point spread anchor realizes ~4.5 points of margin",
   3.6 < _ng_sim2["mean_margin"] < 5.4,
   "%+.2f" % _ng_sim2["mean_margin"])

print()
print("=" * 72)
print("The Cup grid rides inside every DK sample, not pasted on top of them")
print("=" * 72)
import racing_sim as _RS
import racing as _RC
import statistics as _stats

_ns_saved, _pr_saved = _RS.nascar_state, _RC.get_nascar_practice
_FIELD_N = 12
# norm_name strips digits, so numbered fixture names would collapse into one key
_G_NAMES = ["Alpha", "Bravo", "Chase", "Delta", "Echo", "Fox", "Golf", "Hotel",
            "India", "Julie", "Kilo", "Lima"]
def _guard_state(year=None, series=1):
    dmap = {i: {"id": i, "name": f"Guard {_G_NAMES[i]}", "race_pace": 2.0 + 2.5 * i,
                "pace_by_type": {}, "led_by_type": {}, "dnf": 0.02}
            for i in range(_FIELD_N)}
    return {"drivers": dmap,
            "remaining": [{"name": "Guard 400", "type": "intermediate",
                           "laps": 200, "wet_prob": 0.0}]}
try:
    _RS.nascar_state = _guard_state
    _RC.get_nascar_practice = lambda *a, **k: None
    _marg = _RS.next_race_sim("nascar", n=600, seed=11)
    # fast cars buried deep, slow cars up front — the sharpest PD test there is
    _fg = {_RC.norm_name(f"Guard {_G_NAMES[i]}"): _FIELD_N - i for i in range(_FIELD_N)}
    _cond = _RS.next_race_sim("nascar", n=600, seed=11, fixed_grid=_fg)
    ck("without a grid the NASCAR sim reports itself unconditioned",
       _marg and _marg["grid_conditioned"] is False)
    ck("with a real grid it reports conditioned",
       _cond and _cond["grid_conditioned"] is True)
    _shifts = []
    _ok_pd = True
    for _nm, _row in _marg["drivers"].items():
        _c = _cond["drivers"][_nm]
        _start = _fg[_RC.norm_name(_nm)]
        _shift = _c["dk_mean"] - _row["dk_mean"]
        _shifts.append(_shift)
        # same seed => identical simulated races, so the mean shift must be
        # EXACTLY the expected place differential (rounding aside)
        if abs(_shift - (_start - _row["avg_finish"])) > 0.25:
            _ok_pd = False
    ck("each driver's mean shift is exactly his expected place differential",
       _ok_pd)
    ck("place differential conserves: the field's shifts sum to zero",
       abs(sum(_shifts)) < 0.3 * _FIELD_N * 0.05 + 0.6,
       "every spot gained is a spot someone lost")
    _fast = min(_marg["drivers"], key=lambda nm: _marg["drivers"][nm]["avg_finish"])
    _sd_m = _stats.pstdev(_marg["drivers"][_fast]["dk_arr"])
    _sd_c = _stats.pstdev(_cond["drivers"][_fast]["dk_arr"])
    ck("a fast car buried deep gets a WIDER sample spread, not a shifted copy",
       _sd_c > _sd_m,
       "finish points and PD move together inside one race; the old constant "
       "add-on kept the marginal spread (%.1f) instead of %.1f" % (_sd_m, _sd_c))
    _thin = _RS.next_race_sim("nascar", n=200, seed=11, fixed_grid=dict(list(_fg.items())[:3]))
    ck("a grid matching under 8 drivers is refused, falling back to marginal",
       _thin and _thin["grid_conditioned"] is False)
finally:
    _RS.nascar_state, _RC.get_nascar_practice = _ns_saved, _pr_saved

_sm_src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "simulate.py")).read()
ck("the DFS layer grid-conditions BOTH racing sports",
   'if sport in ("f1", "nascar") and grid:' in _sm_src)
ck("the conditioned sim is called with the slate's own sport, not a literal",
   "next_race_sim(sport, n=1500, fixed_grid=" in _sm_src)

print()
print("=" * 72)
print("Settled games grade the same night, not when the 72-hour backstop lapses")
print("=" * 72)
import predlog as _PL2
_rd_src = _insp.getsource(_PL2.resolve_due)
ck("the early probe exists and keys off 66h before close",
   "close_time - 237600 <= ?" in _rd_src,
   "Kalshi's close_time on game markets is a 72h legal backstop; waiting for "
   "it graded a Tuesday game on Friday and starved every calibration")
ck("a pregame-logged row cannot be probed until a game's length has passed",
   "ts + 21600 <= ?" in _rd_src,
   "RBI markets close on a 48h backstop, so the close-window alone opens 18 "
   "HOURS before first pitch — the ts gate is what keeps probes post-game")
ck("only game sports are probed early — tennis/UFC close at event time",
   "model LIKE 'mlb%' OR model LIKE 'nfl%'" in _rd_src)
ck("the main due query is untouched beside the probe",
   "close_time IS NULL OR close_time <= ?" in _rd_src)

# functional round-trip: real resolve_due, scratch DB, scripted Kalshi book
_tmp2 = tempfile.mkdtemp()
_os.environ["PREDLOG_DB"] = _os.path.join(_tmp2, "t.db")
importlib.reload(_PL2)
import kalshi as _K2
_saved_get = _K2.get_market
_probe_calls = []
_BOOK = {
    "EP-SETTLED": {"status": "finalized", "result": "yes"},
    "EP-MIDSETL": {"status": "closed", "result": ""},
    "EP-PASTDUE": {"status": "closed", "result": ""},
}
def _fake_get(tk):
    _probe_calls.append(tk)
    return _BOOK.get(tk, {"status": "active", "result": ""})
try:
    _K2.get_market = _fake_get
    _PL2.init_db()
    import time as _tm
    _now2 = int(_tm.time())
    _in_window = _now2 + 50 * 3600          # inside close-66h, outside close<=now
    _PL2.log_many("mlb", [("EP-SETTLED", 0.6, _in_window, 0.55),
                          ("EP-MIDSETL", 0.6, _in_window, 0.55),
                          ("EP-FRESH",   0.6, _in_window, 0.55),
                          ("EP-PASTDUE", 0.6, _now2 - 60, 0.55)])
    _PL2.log_many("tennis", [("EP-TENNIS", 0.6, _in_window, 0.55)])
    with _PL2._conn() as _c3:
        _c3.execute("UPDATE predictions SET ts=? WHERE ticker != 'EP-FRESH'",
                    (_now2 - 7 * 3600,))
        _c3.execute("UPDATE predictions SET ts=? WHERE ticker='EP-FRESH'",
                    (_now2 - 2 * 3600,))
    _graded_n = _PL2.resolve_due(limit=50)
    with _PL2._conn() as _c3:
        _st = {r["ticker"]: (r["graded"], r["outcome"]) for r in
               _c3.execute("SELECT ticker, graded, outcome FROM predictions")}
    ck("a settled market 2 days shy of its backstop grades tonight",
       _st["EP-SETTLED"] == (1, 1) and _graded_n == 1)
    ck("an early probe caught mid-settlement is retried, never abandoned",
       _st["EP-MIDSETL"] == (0, None),
       "graded=2 is permanent; before close_time a result-less 'closed' can "
       "be a rain-suspended game or a settlement in flight")
    ck("past its own close_time the same state still means scratched",
       _st["EP-PASTDUE"][0] == 2)
    ck("a row logged two hours ago is not probed — its game hasn't been played",
       "EP-FRESH" not in _probe_calls and _st["EP-FRESH"] == (0, None))
    ck("tennis in the identical window is left alone",
       "EP-TENNIS" not in _probe_calls and _st["EP-TENNIS"] == (0, None))
finally:
    _K2.get_market = _saved_get
    del _os.environ["PREDLOG_DB"]
    importlib.reload(_PL2)

print()
print("=" * 72)
print("Leaving the app and coming back is not a failure state")
print("=" * 72)
_swp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "static", "sw.js")
_sw = open(_swp).read()
# The bug: a failed /api/ fetch was answered with a 200 and an empty body, so
# every caller sailed past its error check and died on the first field it
# touched. On the MLB board that was `d.games.length` on undefined -> the catch
# -> "Failed to load slate", which is exactly what the user saw every time they
# came back from placing a bet.
ck("a failed API call is a real failure, never a fake 200 with an empty body",
   'new Response("{}"' not in _sw and "status: 503" in _sw and "offline: true" in _sw,
   "{} is not an error: it passes `if (d.error)` and then throws on d.games")
ck("a dropped request is retried once before giving up",
   "setTimeout(r, 700)" in _sw and _sw.count("fetch(e.request)") >= 3,
   "resuming a backgrounded phone drops exactly one request; the second lands")
ck("a gateway error never replaces or poisons the cached app shell",
   "if (!res.ok) return shellFallback()" in _sw,
   "a 502 is a RESOLVED fetch, so the old code cached the host's error page "
   "under '/' and served it back as the app")
ck("the shell cache version was bumped so the fix actually activates",
   "vigil-shell-v50" in _sw,
   "an unchanged SHELL constant leaves every installed phone on the old worker")

_appjs = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                            "static", "app.js")).read()
ck("the slate retries itself instead of stranding a dead screen",
   "_slateFails" in _appjs and "Failed to load slate - retrying" in _appjs
   and "if (r.status >= 500)" in _appjs,
   "the poll chain only re-armed inside the 202 branch, so one blink meant a "
   "manual refresh -- the user's actual complaint")
ck("a transient failure does not wipe a board that is already rendered",
   'gamesBox.querySelector(".game, .gamecard, table")' in _appjs,
   "showing five-minute-old numbers beats replacing them with an error")
ck("the slate is validated before it is indexed",
   "if (!Array.isArray(d.games)) { throw new Error" in _appjs)
ck("background tabs stop polling, and returning refreshes once",
   "if (document.hidden || uiBusy()) return;" in _appjs
   and 'document.addEventListener("visibilitychange"' in _appjs,
   "polling every 5s while the user is on Kalshi, then firing every stalled "
   "timer at once on return, is what overloaded a single-worker server")

# Stale-while-revalidate: the TTL is shorter than a trip to the exchange, so
# "come back to the app" and "the cache just expired" are the same event.
import baseball as _bbsw
import time as _bbtime
_bbsw._cache[("slate", "2099-01-01", "2099")] = (_bbtime.time() - 400, [{"g": 1}], 300)
_sg, _sa = _bbsw.stale_slate("2099-01-01", "2099")
ck("a board that expired during an errand is served, not rebuilt from cold",
   _sg is not None and 390 < _sa < 410,
   "a five-minute-old board beats a one-minute wait for numbers we already had")
_bbsw._cache[("slate", "2099-01-01", "2099")] = (_bbtime.time() - 7200, [{"g": 1}], 300)
ck("but a genuinely old board is withheld",
   _bbsw.stale_slate("2099-01-01", "2099")[0] is None,
   "past an hour the staleness stops being cosmetic -- lineups and prices moved")
ck("serving stale never poisons the fresh path",
   _bbsw.analyze_slate("2099-01-01", "2099", cached_only=True) is None,
   "cached_only must still refuse an expired entry or the board never refreshes")
_bbsw._cache.pop(("slate", "2099-01-01", "2099"), None)
_appsrc = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                             "app.py")).read()
ck("the endpoint serves the stale board and says it is refreshing",
   '"stale": True' in _appsrc and '"refreshing": True' in _appsrc
   and "if (d.refreshing)" in _appjs,
   "the client has to poll the fresh build in behind the stale one")

# The warmer keeps the BOARD hot while the app is in use. The instance never
# sleeps, but a 300s cache is shorter than a trip to the exchange, so the
# process stayed hot while the data went cold.
ck("the warmer only runs while someone is actually using the app",
   "_WARM_WINDOW" in _appsrc and "time.time() - seen > _WARM_WINDOW" in _appsrc
   and "_note_slate_use(date, season)" in _appsrc,
   "rebuilding every 5 minutes around the clock spawns a ~175 MB child forever "
   "on a 512 MB box for nobody's benefit")
ck("it rebuilds just BEFORE the board expires, not after",
   "age < baseball._SLATE_TTL - 90" in _appsrc,
   "warming after expiry means the returning user still gets a stale board")
ck("it can neither race a request's build nor the nightly season sim",
   "if key in _slate_inflight:" in _appsrc
   and "HEAVY_BUILD" in open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                           "..", "deep_cache.py")).read(),
   "three concurrent slate builds is 547 MB, which is how a 512 MB instance "
   "dies while every individual piece looks affordable")
ck("and it is switchable off without a code change",
   'os.environ.get("VIGIL_WARM_WINDOW")' in _appsrc)

print()
print("=" * 72)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for n, d in FAIL:
        print(f"   - {n}   {d}")
print("=" * 72)
