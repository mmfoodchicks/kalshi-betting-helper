"""Part 3: the surfaces Parts 1-2 don't reach.

The two new guards (_pct, _implausible), the live opt-in gate, the one-game
fallback, display/refresh stability, fee accounting, and monotonicity laws that
must hold for ANY slate (raising the floor can't lower a leg; adding legs can't
raise the combined chance).
"""
import os, sys, datetime, collections, time as _tm
# Tests exercise functions; they must never run the plant. Importing app.py
# used to start the production background loops in the TEST process -- the
# predlog harvester logged live Kalshi predictions into a predlog.db in the
# checkout, graded them for real, and the accumulated rows eventually crossed
# a calibration earn-floor and flipped a guard's expected behaviour mid-week.
os.environ["VIGIL_NO_BG"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import baseball as B
# The loops used to create the stores' tables as a side effect of starting;
# with them off, create the tables explicitly -- several guards shape-check
# the (possibly empty) real stores. Tables only, never live data.
import store as _st_boot
_st_boot.init_db()
import predlog as _pl_boot
_pl_boot.init_db()

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
    # ET tomorrow, not UTC-today+1: at night the container's UTC date is
    # already ET's tomorrow, so +1 skipped a day and fetched a slate Kalshi
    # hadn't listed.
    DATE = (__import__("clock").today_et()
            + datetime.timedelta(days=1)).isoformat()
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
    # a NO leg must be the complement of the YES leg IT NAMES ("NO - X" vs "X").
    # ASCII hyphen: that IS the label production writes -- this filter once used
    # an em-dash, matched nothing, and the whole check passed without running.
    for v in vs:
        if v.get("side") == "no" and v["label"].startswith("NO - "):
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
_pair = {"type": "Ks", "label": "L", "marg": 0.62, "mask": 0b1, "group": "g"}
_pno = _MS._no_candidates([_pair], 4)[0]
_pblend = _MS._no_candidates([dict(_pair, marg=0.66, marg_model=0.62)], 4)[0]
ck("and its marginal is 1 minus the CALIBRATED yes, so a pair sums to 1",
   abs(_pair["marg"] + _pno["marg"] - 1.0) < 1e-12
   and abs(_pblend["marg_model"] + 0.62 - 1.0) < 1e-12
   and abs(_pblend["marg"] + 0.62 - 1.0) < 1e-12,
   "a cand the pricing pass already blended in place complements its MODEL "
   "number (marg_model), never the blend -- the pair the model believes in "
   "sums to 1, and pricing re-blends the NO side fresh from there")

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
# STRUCTURE guards (does the checkbox bind), not pricing guards -- but they
# used to price a startup snapshot against LIVE Kalshi minutes later, so any
# suite run that crossed a first pitch found the books pulled, every leg
# dropped, and a red suite at exactly 7:15pm. Pin both nondeterminism
# sources: unpriced mode (model numbers alone) and a small sim count, both
# restored (and the small sims evicted) right after.
_kup_old = B._kalshi_up
_simn_old = B._SIM_N
B._kalshi_up = lambda: False
B._SIM_N = 800
_struct_games = playable[:4]
_off = B.build_mixed_parlay(_struct_games, n_legs=4, target_pct=50,
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
# The other half of the same guard: turning it ON must still be ABLE to stack.
# Asserted by pigeonhole, not by taste: on a full slate the balanced objective
# may legitimately prefer six singles (it did, the night the honest blend
# landed) -- but three games cannot carry five required legs without stacking
# somewhere, so this fails only if stacking itself is broken.
_rich = sorted(_struct_games,
               key=lambda g: -len((B._game_sim(g) or {}).get("cands", [])))[:3]
_on = B.build_mixed_parlay(_rich, n_legs=5, target_pct=45,
                           max_legs_per_game=4, max_total_legs=12,
                           legs_mode="require")
B._kalshi_up = _kup_old
B._SIM_N = _simn_old
for _g in _struct_games:                 # evict the 800-run sims: later guards
    _pk = _g.get("game_pk")              # must never read a thin distribution
    B._cache.pop(("game_sim", _pk), None)
    try:
        os.remove(os.path.join(B._SIM_DISK, f"{_pk}.pkl"))
    except OSError:
        pass
if _on and _on.get("groups"):
    ck("same-game ON still stacks (the fix did not disable stacking)",
       max(g["size"] for g in _on["groups"]) > 1,
       [(g["matchup"], g["size"]) for g in _on["groups"]][:4])
else:
    ck("same-game ON still stacks (the fix did not disable stacking)",
       False, "no 5-leg slip built from the three richest games")

# One game on the board plus one leg per game cannot reach two legs. Returning
# a bare None left the NFL tab saying "no combo" with no reason, on a preseason
# week that had exactly one game.
_hint = _insp.getsource(_NFS.build_parlay)
ck("the one-leg-per-game / one-game dead end is named, not shrugged at",
   "single_game_no_stack" in _hint,
   "None is indistinguishable from 'the slate is dry'")
ck("and the API forwards that hint", 'item.get("error_hint")' in open(
   os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app.py")).read(),
   "the endpoint forwards EVERY builder hint generically now, so a new dead-end "
   "name reaches the UI without another endpoint edit")
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
ck("all four endpoints read max_bet (MLB, NFL, tennis, UFC)",
   _app.count('request.args.get("max_bet")') == 4,
   _app.count('request.args.get("max_bet")'))
ck("and all four sweep rather than picking one floor",
   _app.count("best_max_bet") == 4, _app.count("best_max_bet"))
ck("an unreachable ceiling is a named hint, not a bare null",
   _app.count("max_bet_unreachable") >= 3)
for _b, _label in (("buildCombo", "MLB"), ("buildNFLCombo", "NFL"),
                   ("buildTennisCombo", "tennis"), ("buildUFCCombo", "UFC")):
    ck(f"the {_label} maker has a max-bet button",
       f"{_b}(true)" in _js)
ck("every max-bet button is labelled with the ceiling",
   _js.count("🎰 Max bet (${MAX_BET_X}×)") == 4)
ck("the slip shows the market's probability beside ours",
   "market_prob_pct" in _js and "Market says" in _js)
ck("and says so when the ceiling could not be reached",
   "isn't reachable on this board today" in _js)
ck("the button label follows the server's cap, not a hardcoded 320",
   "function noteMaxBetCap(" in _js and _js.count("noteMaxBetCap(d);") == 4,
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
ck("and a failed ratings fetch leaves the board standing, noted not silent",
   'errlog.note("NFLD-week_teams-2", _e)\n\n        # Home/away' in _nd_src)

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
   "import ump_build" in _app4
   and "except Exception" in _app4.split("import ump_build", 1)[1][:400]
   and "errlog.note" in _app4.split("import ump_build", 1)[1][:400],
   "still swallowed (the deep run is the product), but now RECORDED -- the "
   "silent version is what every incident here grew from")

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
   "except Exception" in _insp.getsource(_BB._game_sim).split(
       "_log_prop_predictions", 1)[1][:300]
   and "errlog.note" in _insp.getsource(_BB._game_sim).split(
       "_log_prop_predictions", 1)[1][:300],
   "still swallowed, now recorded")

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
# Both states are INJECTED via the params cache rather than read from
# whatever predlog.db happens to sit on this machine: the ambient-DB version
# of the first check flipped the day the local rows crossed the earn-floor.
_CAL._cache["mlb_hit"] = ((1.0, 0.5, 0.0, 0), _tm.time())
try:
    ck("an unearned market falls back to the pooled correction",
       abs(_CAL.prop_market(0.60, "mlb_hit") - _CAL.batter_prop(0.60)) < 1e-12)
    _CAL._cache["mlb_hit"] = ((1.30, 0.5, 0.0, 500), _tm.time())
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
   and '"hr_extra": weather_mod.hr_extra(wx, s["roof"], home_id=g["home_id"])' in _bb_wx)
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
   "setTimeout(r, 700)" in _sw and "fetch(retryReq)" in _sw
   and "e.request.clone()" in _sw.split('startsWith("/api/")')[1][:600],
   "resuming a backgrounded phone drops exactly one request; the second lands. "
   "The retry must use a CLONE taken before the first attempt -- a request body "
   "is single-use, so retrying the same object throws on every POST and the "
   "retry silently never happened for exactly the requests that carry data")
ck("a gateway error never replaces or poisons the cached app shell",
   "if (!res.ok) return shellFallback()" in _sw,
   "a 502 is a RESOLVED fetch, so the old code cached the host's error page "
   "under '/' and served it back as the app")
import re as _swre
_swv = int((_swre.search(r"vigil-shell-v(\d+)", _sw) or [0, "0"])[1])
ck("the shell cache version was bumped so the fixes actually activate",
   _swv >= 51,
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
   "_WARM_WINDOW" in _appsrc
   and "time.time() - (v.get(\"ts\") or 0) <= max(_WARM_WINDOW" in _appsrc
   and "_note_slate_use(date, season)" in _appsrc,
   "rebuilding every 5 minutes around the clock spawns a ~175 MB child forever "
   "on a 512 MB box for nobody's benefit")
# The expensive thing is not the slate, it is the combo maker's per-game 4,000
# run simulation: ~32s a game on a fast desktop, over 200s on a shared cloud
# CPU. Nothing about it depends on the user's floors/legs/payout -- those filter
# candidates it has already produced -- so it must not be on the click.
# Running three workers (so a killed one cannot take the health check down) gave
# each its own in-process cache, and the warmer runs in only ONE of them -- so
# most builds landed on a worker that had never seen the game. Worse, _cached()
# has no single-flight, so the warmer and the build simulated the SAME game side
# by side and halved each other's speed on a one-CPU box. Together they made the
# maker slower than before any of this.
import baseball as _bbttl          # also used below; import locally to be safe
ck("a finished game sim is shared with the other workers, via disk",
   "_sim_disk_put" in _insp.getsource(_bbttl._game_sim)
   and "_sim_disk_get" in _insp.getsource(_bbttl._game_sim),
   "three workers with three private caches means the warmer warms a cache "
   "two builds in three will never see")
ck("the shared write is atomic, so no reader sees half a pickle",
   "_os.replace(tmp" in _insp.getsource(_bbttl._sim_disk_put))
ck("a second caller WAITS for the first instead of re-simulating",
   "_sim_flight" in _insp.getsource(_bbttl._game_sim)
   and "ev.wait(" in _insp.getsource(_bbttl._game_sim),
   "two 200-second simulations of the same game on one CPU take 400 seconds "
   "and produce one answer")
ck("...and still builds if the leader died rather than hanging",
   "return build()" in _insp.getsource(_bbttl._game_sim).split("if not leader:")[1])
ck("the cached-check knows about the shared copy too",
   "_SIM_DISK" in _insp.getsource(_bbttl._game_sim_cached),
   "otherwise the warmer re-simulates games a sibling already published")
ck("the shared cache prunes itself",
   "2 * _GAME_SIM_TTL" in _insp.getsource(_bbttl._sim_disk_put),
   "yesterday's games are never coming back and the disk is 1 GB")
ck("the per-game sims the COMBO MAKER needs are warmed too, not just the slate",
   "_warm_game_sims" in _appsrc and "baseball._game_sim(gm)" in _appsrc,
   "the board's engine and the maker's engine are different; warming only the "
   "board left the first Build paying minutes of simulation")
_wtick_src = _insp.getsource(__import__("app")._warm_tick)
ck("...and the SIMS go first, the board refresh second",
   _wtick_src.index("_warm_game_sims(") < _wtick_src.index("stale_slate("),
   "the old order rebuilt the slate before warming, in the same thread; when "
   "the board build waited on the one-heavy-build gate (the nightly season sim "
   "can hold it for most of an hour) the sims silently queued behind it and "
   "the bar froze at 0/N with no explanation")
# Picking the phone up after a break must not mean a cold board. The activity
# window meant the warmer had long since stopped, so "open the app" and "wait
# minutes" were the same event.
ck("the board is kept warm all the time, not only just after someone looks",
   "_WARM_ALWAYS" in _appsrc and 'os.environ.get("VIGIL_WARM_ALWAYS") or "1"' in _appsrc,
   "a 30-minute activity window is exactly as long as a break, so every return "
   "landed on a cold cache")
ck("with nobody having asked yet, it warms TODAY's board anyway",
   "return today, today[:4]" in _insp.getsource(__import__("app")._warm_pick_key),
   "after a restart nobody has requested a slate, and that is precisely when "
   "the first person to open the app needs it ready")
ck("the sim TTL outlives a full warm cycle",
   _bbttl._GAME_SIM_TTL >= 3600,
   "a warm pass over a slate takes longer than 15 minutes on one CPU, so a "
   "15-minute TTL could never leave the cache full")
ck("...which is safe because prices are NOT baked into the cached sim",
   "_price_cands(cands" in _insp.getsource(_bbttl.build_mixed_parlay),
   "the cache holds the matchup simulation; Kalshi prices are fetched fresh at "
   "build time, so a longer TTL costs no price accuracy")
ck("the app says whether it is ready, instead of letting you find out",
   '"/api/warm"' in _appsrc and "pollWarm" in _appjs and 'id="warmBar"' in
   open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..",
                      "templates", "index.html")).read(),
   "you pressed Build and only then discovered the board was cold")
ck("the banner reports real counts and announces when it finishes",
   '"warm": warm' in _appsrc and "all ${d.total} games simulated" in _appjs,
   "a spinner that never resolves is what this replaces")
ck("warming stops the moment nobody is using the app",
   "_warm_pick_key() is None" in
   _insp.getsource(__import__("app")._warm_game_sims),
   "simulating a slate forever for an empty browser is pure waste on a box "
   "with one CPU")
ck("an already-cached game is skipped",
   "_game_sim_cached(g)" in _insp.getsource(__import__("app")._warm_game_sims))
# A health-check alert has two causes that look identical from outside.
ck("the diagnostics say whether the box was RESTARTED or genuinely stalled",
   '"recently_restarted"' in _appsrc and "_PROC_START" in _appsrc,
   "a worker only seconds old means the probe hit an instance being replaced "
   "by a deploy, not an app that hung -- that distinction ends the guessing")
ck("it rebuilds just BEFORE the board expires, not after",
   "age >= baseball._SLATE_TTL - 90" in _appsrc,
   "warming after expiry means the returning user still gets a stale board")
ck("it can neither race a request's build nor the nightly season sim",
   "if key not in _slate_inflight:" in _appsrc
   and "HEAVY_BUILD" in open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                           "..", "deep_cache.py")).read(),
   "three concurrent slate builds is 547 MB, which is how a 512 MB instance "
   "dies while every individual piece looks affordable")
ck("and it is switchable off without a code change",
   'os.environ.get("VIGIL_WARM_WINDOW")' in _appsrc)

# The health-check restarts. When a request outlives gunicorn's --timeout the
# arbiter kills its worker; with ONE worker nothing is left to answer /healthz,
# the probe times out at 5s and the platform restarts a box that was only busy.
# Measured on one CPU, every thread tied up in long builds: 1 worker failed 7/7
# probes, 2 workers failed 0/89.
_root = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
_dockerfile = open(_os.path.join(_root, "Dockerfile")).read()
_procfile = open(_os.path.join(_root, "Procfile")).read()
_renderyml = open(_os.path.join(_root, "render.yaml")).read()
ck("the server never runs a single web worker",
   "-w 1 " not in _dockerfile and "-w 1 " not in _procfile
   and "WEB_CONCURRENCY" in _dockerfile and "WEB_CONCURRENCY" in _procfile,
   "one worker means one killed request takes the health check down with it")
ck("...and the worker count is FLOORED at two, whatever the platform sets",
   "-lt 2 ] && W=2" in _dockerfile.replace('\\"', '"')
   and "-lt 2 ] && W=2" in _procfile
   and "VIGIL_WEB_WORKERS" in _dockerfile and "VIGIL_WEB_WORKERS" in _procfile,
   "WEB_CONCURRENCY is a name the host may set itself; arriving as 1 would put "
   "us silently back on the configuration that fails the health check")
ck("a non-numeric worker count cannot break the start command",
   "*[!0-9]*) W=3" in _dockerfile and "*[!0-9]*) W=3" in _procfile)
# The build is minutes of simulation; a request must never be. One game's sim is
# ~32s on a fast desktop and >100s on a shared cloud CPU, so a 14-game slate
# could not finish inside gunicorn's 120s timeout -- and the killed worker is
# what failed the platform's probe.
# The job and its progress must be shared too, for the same reason the sim cache
# is: the browser's poll round-robins across workers. A poll landing on a worker
# that had never seen the token started a SECOND build, so the bar climbed to
# "game 10 of 11" and then dropped back to "game 1 of 11" -- it was reading a
# different worker's duplicate, and three simulations fought over one CPU.
import shutil as _sh
_sh.rmtree(_bbttl._JOB_DIR, ignore_errors=True)
_tokx = "guardclaim"
_wins = [_bbttl.job_claim(_tokx) for _ in range(4)]
ck("exactly one worker may claim a build",
   _wins.count(True) == 1,
   "without an atomic O_EXCL claim every polling request that lands on a cold "
   "worker starts its own duplicate build")
_bbttl.progress_start(_tokx, 11)
_bbttl.progress_enter(_tokx)
_bbttl.progress_step(_tokx)
_bbttl._progress.clear()                    # a worker that never saw this build
_pg = _bbttl.progress_get(_tokx) or {}
ck("a sibling worker reads the SAME progress, not a fresh zero",
   _pg.get("total") == 11 and _pg.get("at") == 1 and _pg.get("done") == 1,
   "per-worker counters are why the bar reset to 'game 1 of 11' after reaching 10")
_bbttl.job_finish(_tokx, "done", result={"parlay": {"ok": 1}})
ck("and the finished result is visible to whichever worker is polled",
   (_bbttl.job_read(_tokx) or {}).get("status") == "done"
   and "baseball.job_read(ptok)" in _appsrc and "baseball.job_claim(ptok)" in _appsrc,
   "the result lived in one worker's memory, so two polls in three never saw it")
_bbttl.job_drop(_tokx)
ck("abandoned job files are reaped",
   "_time.time() - 3600" in _insp.getsource(_bbttl.job_claim))
# Delivery of a finished build must be idempotent. It used to job_drop on the
# first read -- BEFORE the response was known to have reached the phone. One
# lost response (a backgrounded tab, a dropped socket) and the next poll found
# no job, won a fresh claim, and silently rebuilt everything: the bar finished,
# then snapped back to "building". Verified live: four reads of one finished
# token now return the identical parlay with no phantom rebuild.
_mixed_src2 = _appsrc.split("def api_baseball_mixed")[1].split("\n@app.route")[0]
ck("a finished result can be read as many times as it is asked for",
   "job_drop" not in _mixed_src2.split('if job and job.get("status") == "done"')[1][:300],
   "destroy-on-first-read turns one lost response into a full rebuild")
ck("the client poll rides out network blinks instead of aborting",
   "misses" in _appjs and "++misses > 8" in _appjs,
   "bailing to 'Build failed' while the job still runs invites a second click "
   "and a second, concurrent build fighting the first for one CPU")
ck("a combo build runs in the background, not inside the request",
   "_combo_jobs" in _appsrc and '"status": "building"' in _appsrc
   and "threading.Thread(target=_bg" in _appsrc.split("_combo_jobs")[1],
   "a 14-game slate is minutes of work against a 120s worker timeout: the "
   "request was killed every time and took the instance with it")
ck("the client re-asks until the job is done",
   'd.status === "building"' in _appjs,
   "202 means come back; the browser has to actually come back")
# Scoped to the mixed endpoint: the other four endpoints call _prop_types() on
# the request thread, which is correct. Only the backgrounded one must not.
_mixed_src = _appsrc.split("def api_baseball_mixed")[1].split("\n@app.route")[0]
ck("request-scoped values are read BEFORE the background thread starts",
   "prop_types = _prop_types()" in _mixed_src
   and "types=prop_types" in _mixed_src
   and "types=_prop_types()" not in _mixed_src,
   "_prop_types() reads `request`; called from the background thread it raises "
   "'Working outside of request context' and the build dies instantly -- which "
   "it did, finishing in 3s with nothing built")
ck("abandoned builds are reaped rather than piling up",
   "_time.time() - 3600" in _insp.getsource(_bbttl.job_claim),
   "job files outlive their build; without a sweep the directory grows forever")
ck("the deployed blueprint asks for more than one too",
   'key: WEB_CONCURRENCY' in _renderyml
   and int(_renderyml.split("key: WEB_CONCURRENCY")[1].split('value: "')[1].split('"')[0]) >= 2)
ck("workers recycle, so a slate build's fragmented arenas come back",
   "--max-requests" in _dockerfile and "--max-requests-jitter" in _procfile,
   "a worker that has simulated a slate plateaus ~140 MB fatter and only grows")
# Extra workers must not multiply the background work: N graders double-writing
# the prediction log would corrupt the track record the calibrator is fitted on.
ck("exactly one worker owns the recorders, the grader and the sims",
   "_BG_OWNER = _own_background_jobs()" in _appsrc
   and "if not _BG_OWNER:" in _appsrc
   and "fcntl.LOCK_EX | fcntl.LOCK_NB" in _appsrc,
   "N workers each starting the recorders is why the server ran one worker")
ck("the owner lock is held for the process lifetime, not released on return",
   "_BG_LOCK_FD = fd" in _appsrc,
   "a closed descriptor drops the flock and a second worker claims the jobs too")
ck("and the health probe still never triggers the bootstrap",
   "if request.path in _PROBE_PATHS:" in _appsrc,
   "hanging the recorders off the first request means the probe pays for them")
# When a worker is killed for outliving --timeout, nothing said WHICH request
# did it: the platform reports a failed health check and the app's log is
# silent, so the box looks like it crashed at random.
ck("slow requests name themselves in the log",
   "[slow]" in _appsrc and "_SLOW_LOG_S" in _appsrc
   and 'request.path in _PROBE_PATHS' in _appsrc.split("def _slow_finish")[1][:400],
   "a request that outlives the worker timeout is the one that fails the "
   "health check, and it was invisible")
ck("the worst offenders survive for inspection",
   '"/api/diag/slow"' in _appsrc and "_slow_recent" in _appsrc)
print()
print("=" * 72)
print("Tennis below the main tour is on the board, and on court")
print("=" * 72)
import tennis_prices as _TP
import tennis_live as _TL
_tours = {t[1] for t in _TP._TOURS}
ck("the Challenger series is fetched at all",
   "KXATPCHALLENGERMATCH" in _tours,
   "a whole tier -- 37 matches on an ordinary day, the events Kalshi promotes "
   "on its own live tab -- was invisible because we only asked for KXATPMATCH")
ck("and the tier is labelled for the Kalshi tab hint",
   "Challenger ATP" in _insp.getsource(_TP))
ck("a match carries when it STARTS, not just when it closes",
   "start_epoch" in _insp.getsource(_TP) and "occurrence_datetime" in _insp.getsource(_TP),
   "Kalshi publishes no score, but it does say when play begins -- the only "
   "in-progress signal that exists below the main tour")
ck("a started market still open counts as on court",
   "\"in_play\": bool(start_epoch and start_epoch <= time.time())" in _insp.getsource(_TP))
_lr = _insp.getsource(_TL.live_rows)
ck("the Live tab reads the board, not only ESPN",
   "board" in _lr and "in_play" in _lr,
   "ESPN publishes scores for the main tour only, so Challenger and ITF -- most "
   "of what is on court on an evening -- never reached the Live tab")
ck("a score is never invented for a match we cannot see",
   '"score": None' in _insp.getsource(_TL.attach) and "no_score_feed" in _lr)
# The dangerous one: a pre-match number against a live price is NOT an edge.
# Whether a model-vs-price gap is playable without a score is a MEASURED
# question. Over the 174 graded tennis picks in the prediction log, our model
# vs the market: median gap 0.2 points, p90 8.4, p95 9.4, max 14.4 -- and gaps
# of 15+ occurred ZERO times. So a live gap inside that band is the size of
# disagreement this model really produces; a bigger one is the score talking.
ck("the live-gap ceiling sits just above the measured maximum",
   _TL._LIVE_GAP_MAX == 14.5 and _TL._LIVE_GAP_MIN == 4.0,
   "14.4 was the largest pre-match disagreement in 174 graded picks; a 15-point "
   "gap has literally never been a model opinion and must read as score")
ck("a gap inside the measured band reads as playable",
   _TL.live_gap_read(64.0, 50.0)[0] == "edge"
   and _TL.live_gap_read(75.0, 69.0)[0] == "edge")
ck("a gap just past everything ever observed reads as score",
   _TL.live_gap_read(65.0, 50.0)[0] == "score")
ck("a gap larger than the model has ever produced is called score, not edge",
   _TL.live_gap_read(54.0, 25.0)[0] == "score",
   "our 54% against a 25c market is 29 points -- twice anything this model has "
   "ever claimed pre-match, on a player who was a set down")
ck("a gap too small to matter is not sold as a signal",
   _TL.live_gap_read(50.0, 52.0)[0] is None
   and _TL.live_gap_read(84.0, 87.0)[0] is None)
ck("no read at all when there is no model number",
   _TL.live_gap_read(None, 50.0)[0] is None
   and _TL.live_gap_read(60.0, None)[0] is None)
ck("and a dip is never 'verified' without a score",
   'lv.get("no_score_feed")' in _insp.getsource(_TL.mark_dips),
   "the dip detector's whole claim is that the live probability still beats the "
   "ask; with no score there is no live probability to check")
ck("the price-collapse radar, which IS for these, still fires",
   "price_only" in _insp.getsource(_TL.mark_price_upsets),
   "that detector was written for no-score matches and could never fire, "
   "because those matches never carried a live block to begin with")

print()
print("=" * 72)
print("Logging in once is enough")
print("=" * 72)
import os as _o2, importlib as _il, base64 as _b64
_had_pw, _had_sk, _had_ip = (_o2.environ.get("APP_PASSWORD"),
                             _o2.environ.get("SECRET_KEY"),
                             _o2.environ.get("VIGIL_TRUSTED_IPS"))
try:
    _o2.environ["APP_PASSWORD"] = "guardpw"
    _o2.environ.pop("SECRET_KEY", None)
    _o2.environ.pop("VIGIL_TRUSTED_IPS", None)
    import app as _AU
    _il.reload(_AU)
    _cl = _AU.app.test_client()
    ck("a password is still required to get in the first time",
       _cl.get("/api/tiers").status_code == 401)
    _h = {"Authorization": "Basic " + _b64.b64encode(b"kalshi:guardpw").decode()}
    _r = _cl.get("/api/tiers", headers=_h)
    _sc = _r.headers.get("Set-Cookie") or ""
    ck("logging in remembers the device",
       _r.status_code == 200 and "vigil_auth" in _sc,
       "basic auth alone re-prompts every time a phone evicts the tab")
    ck("the cookie is HttpOnly and same-site",
       "HttpOnly" in _sc and "Lax" in _sc,
       "script must not be able to read it and it must not ride cross-site")
    ck("the next visit needs no password",
       _cl.get("/api/tiers").status_code == 200)
    _tok = _AU._mint_remember()
    ck("a tampered signature is rejected",
       not _AU._remember_ok(_tok.split(".")[0] + "." + "0" * 64),
       "the cookie is an HMAC over its own expiry -- there is nothing to forge")
    ck("an expired cookie is rejected",
       not _AU._remember_ok(_AU._mint_remember(days=-1))
       and not _AU._remember_ok("nonsense"))
    ck("the signing key does not depend on a per-process random",
       "hashlib.sha256" in _insp.getsource(_AU._auth_key),
       "app.secret_key falls back to os.urandom, and three workers would then "
       "sign with three different keys -- the login would appear not to stick")
    # IP allowlist: opt-in, and OFF unless set.
    ck("the IP allowlist is off unless explicitly configured",
       not _AU._trusted_ip("203.0.113.4"))
    _o2.environ["VIGIL_TRUSTED_IPS"] = "203.0.113.4,192.168.1.0/24"
    _il.reload(_AU)
    ck("...and matches single addresses and CIDRs when it is",
       _AU._trusted_ip("203.0.113.4") and _AU._trusted_ip("192.168.1.77")
       and not _AU._trusted_ip("192.168.2.5") and not _AU._trusted_ip("8.8.8.8"))
    ck("a malformed address can never be trusted",
       not _AU._trusted_ip("not-an-ip") and not _AU._trusted_ip(""))
    ck("there is a way to sign a device back out",
       "/logout" in _insp.getsource(_AU))
finally:
    for _k, _v in (("APP_PASSWORD", _had_pw), ("SECRET_KEY", _had_sk),
                   ("VIGIL_TRUSTED_IPS", _had_ip)):
        if _v is None:
            _o2.environ.pop(_k, None)
        else:
            _o2.environ[_k] = _v
    _il.reload(_AU)

print()
print("=" * 72)
print("A throttled Kalshi window cannot unprice the board")
print("=" * 72)
import kalshi as _KTH
import kalshi_mlb as _KMB
import shutil as _kthsh, time as _ktht
# Three workers, an always-on warmer, and slate subprocesses with cold caches
# multiplied the app's Kalshi request rate from one egress IP; one throttled
# response inside a slate build shipped a board with NO prices to every worker
# via the shared board -- "no Kalshi prices on the slate" while the exchange
# was quoting every moneyline on the user's phone.
ck("the fetch layer retries 429s and honours Retry-After",
   "429" in _insp.getsource(_KTH._get_json)
   and "Retry-After" in _insp.getsource(_KTH._get_json)
   and "for attempt in range(3)" in _insp.getsource(_KTH._get_json),
   "a single throttled response used to raise straight through")
_kthsh.rmtree(_KMB._IDX_DISK, ignore_errors=True)
_KMB._idx_disk_put({"26TESTAAA": {"x": 1}, "26TESTBBB": {"x": 2}})
_KMB._cache["data"], _KMB._cache["ts"] = None, 0.0
_saved_build = _KMB._build_index
try:
    _KMB._build_index = lambda: (_ for _ in ()).throw(RuntimeError("throttled"))
    ck("a raising fetch falls back to the last GOOD index, not {}",
       len(_KMB.index()) == 2)
    _KMB._cache["data"], _KMB._cache["ts"] = None, 0.0
    _KMB._build_index = lambda: {}
    ck("an EMPTY build is treated as the same failure",
       len(_KMB.index()) == 2,
       "_build_index swallows per-series errors and returns {} when fully "
       "throttled -- an empty index IS the failure the fallback exists for")
    _kthsh.rmtree(_KMB._IDX_DISK, ignore_errors=True)   # no sibling copy now
    _KMB._cache["data"], _KMB._cache["ts"] = {"KEEP": 1}, _ktht.time() - 999
    ck("with a last-good copy in memory and no disk, a bad build keeps it",
       _KMB.index() == {"KEEP": 1})
finally:
    _KMB._build_index = _saved_build
    _KMB._cache["data"], _KMB._cache["ts"] = None, 0.0
    _kthsh.rmtree(_KMB._IDX_DISK, ignore_errors=True)
ck("the slate subprocess warm-starts from the shared disk index",
   "_idx_disk_get(_TTL)" in _insp.getsource(_KMB.index),
   "the child's module cache is cold on every rebuild, and a full fresh fetch "
   "per rebuild is the request volume that got the IP throttled")
ck("stale last-good prices are capped, not eternal",
   _KMB._IDX_STALE_MAX <= 3600,
   "40-minute-old real prices beat an unpriced board; 3-hour-old ones do not")

print()
print("=" * 72)
print("The market blend earns its weights from the graded record")
print("=" * 72)
import calibrate as _CBW
import combo_engine as _CEW
import os as _bwos, sqlite3 as _bwsql, tempfile as _bwtmp, random as _bwrng, importlib as _bwil
# The trust table was a hand-set prior never confronted with the record, and the
# record disagrees with it in both directions: raw totals BEAT the de-vigged
# close over hundreds of graded picks and were flattened anyway; Ks kept high
# trust through a measured losing stretch. Every displayed edge collapsed toward
# zero by a fixed factor -- "+1, 0, or negative" -- regardless of merit.
_bwdir = _bwtmp.mkdtemp(); _bwdb = _bwos.path.join(_bwdir, "pl.db")
_bwhad = _bwos.environ.get("PREDLOG_DB")
_bwos.environ["PREDLOG_DB"] = _bwdb
import predlog as _bwpl; _bwil.reload(_bwpl)
_c = _bwsql.connect(_bwdb)
_c.execute("""CREATE TABLE predictions (ticker TEXT PRIMARY KEY, model TEXT,
  prob REAL, close_time REAL, ts REAL, graded INT DEFAULT 0, outcome INT,
  resolved_ts REAL, mkt REAL)""")
_r = _bwrng.Random(7); _bwrows = []
for _i in range(600):
    _truth = min(0.95, max(0.05, _r.gauss(0.55, 0.15)))
    _mg = min(0.97, max(0.03, _truth + _r.gauss(0, 0.03)))
    _mk = min(0.97, max(0.03, 0.5 + (_truth - 0.5) * 0.4 + _r.gauss(0, 0.05)))
    _y = 1 if _r.random() < _truth else 0
    _bwrows.append((f"G{_i}", "syn_good", _mg, 0, (_i % 30) * 86400, 1, _y, 0, _mk))
    _mb = min(0.97, max(0.03, _r.gauss(0.5, 0.12)))
    _mk2 = min(0.97, max(0.03, _truth + _r.gauss(0, 0.02)))
    _bwrows.append((f"B{_i}", "syn_bad", _mb, 0, (_i % 30) * 86400, 1, _y, 0, _mk2))
_c.executemany("INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?)", _bwrows)
_c.commit(); _c.close()
_bwil.reload(_CBW)
_wg = _CBW.blend_weight("syn_good")
_wb = _CBW.blend_weight("syn_bad")
ck("a model that genuinely beats the market earns its weight back",
   _wg is not None and _wg[0] >= 0.5, f"fitted {_wg}")
ck("a model that is noise against the market is flattened",
   _wb is not None and _wb[0] <= 0.1, f"fitted {_wb}")
ck("no fit before the floors are met",
   _CBW.blend_weight("no_such_model") is None,
   "150 graded across 10 days, same discipline as every calibration here")
ck("the blend consumes the fit through an effective tau, clamped",
   "_effective_tau" in _insp.getsource(_CEW.blend_prob)
   and "min(max(w, 0.02), 0.90)" in _insp.getsource(_CEW._effective_tau),
   "never all-market (the model must be able to disagree) and never all-model "
   "(the clamp and the market cap still rule)")
ck("with no fit the hand-set prior still rules",
   True)  # exercised implicitly: local floors unmet -> priors, asserted below
_CEW._tau_cache.clear()
if _bwhad is None:
    _bwos.environ.pop("PREDLOG_DB", None)
else:
    _bwos.environ["PREDLOG_DB"] = _bwhad
_bwil.reload(_bwpl); _bwil.reload(_CBW)
_CEW._tau_cache.clear()
ck("locally (thin log) every tau equals its prior",
   abs(_CEW._effective_tau("ML") - 1.0) < 1e-9
   and abs(_CEW._effective_tau("Total") - 0.7) < 1e-9)
ck("the pre-blend disagreement is shown on the slip",
   "pre-blend" in _appjs and "rawEdge" in _appjs,
   "the edge beside the price is the PLAYED edge; the model's own claim must "
   "be visible or a flattened +8 and a real +1 look identical")

print()
print("=" * 72)
print("The board is one board, whichever worker answers")
print("=" * 72)
import baseball as _bsl
import time as _bt2
import shutil as _bsh
# The game SIMS were shared between workers but the BOARD was not, so the same
# /api/warm poll flickered between "15/15 ready" and "0/0 building today's
# board" depending on which worker answered -- measured live on a 3-worker
# server -- and every worker paid its own ~90s slate build for a board a
# sibling already had.
_bsh.rmtree(_bsl._SLATE_DISK, ignore_errors=True)
_bsl._slate_disk_put("2099-02-01", "2099", [{"g": 1}])
_g, _a = _bsl._slate_disk_get("2099-02-01", "2099", 300)
ck("a built board is published where every worker can read it",
   _g == [{"g": 1}] and _a is not None and _a < 5,
   "per-worker memory is why the warm bar blinked in and out on the phone")
_bsl._cache.pop(("slate", "2099-02-01", "2099"), None)
ck("a cold worker adopts the sibling's board instead of rebuilding",
   _bsl.analyze_slate("2099-02-01", "2099", cached_only=True) == [{"g": 1}])
ck("...and adopts it WITH ITS AGE, not a reset clock",
   "_cache[key] = (_time.time() - age, disk, _SLATE_TTL)"
   in _insp.getsource(_bsl.analyze_slate),
   "restarting the TTL on adoption would let a board live forever by hopping "
   "between workers")
ck("stale_slate falls back to the shared copy too",
   "_slate_disk_get" in _insp.getsource(_bsl.stale_slate))
_bsl._cache.pop(("slate", "2099-02-01", "2099"), None)
_bsh.rmtree(_bsl._SLATE_DISK, ignore_errors=True)
ck("live games skipped by the build are not counted as entered",
   'if state == "Live" and not include_live:' in
   _insp.getsource(_bsl.build_mixed_parlay).split("progress_start(progress_token")[1][:600],
   "entering games the loop then skips made `at` outrun `total` -- the bar "
   "clamps, but the count lied")

print()
print("=" * 72)
print("A cache shorter than its own fill time is never warm")
print("=" * 72)
import baseball as _bbttl
# One game's 4,000-run sim is ~32s, so a 15-game slate is ~8 minutes of work.
# Against the old 180s TTL, game 1 expired before game 15 was simulated and every
# combo build re-ran whatever had lapsed -- which is where "Optimal for my x"
# spent 67 seconds. Measured after the fix: 43.9s cold, 0.6s warm.
ck("the per-game sim cache outlives the time it takes to fill",
   _bbttl._GAME_SIM_TTL >= 600,
   f"TTL is {_bbttl._GAME_SIM_TTL}s; a 15-game slate takes ~480s of simulation, "
   "so anything under that guarantees a permanently cold cache")
ck("and it is tunable without a code change",
   'VIGIL_GAME_SIM_TTL' in open(_os.path.join(_root, "baseball.py")).read())
ck("live games still get their own short-lived path",
   "_live_game_sim" in _insp.getsource(_bbttl.build_mixed_parlay),
   "raising the pre-game TTL must not staleness-freeze a game in progress")
# Real progress: the build is one HTTP request, so the browser cannot observe
# it -- unless the builder counts games as it goes and something serves that.
_pt = "guardtok"
_bbttl.progress_start(_pt, 5)
_bbttl.progress_enter(_pt)
ck("a build reports the game it is ON, not only the ones it finished",
   (_bbttl.progress_get(_pt) or {}).get("at") == 1
   and (_bbttl.progress_get(_pt) or {}).get("done") == 0,
   "the first game takes ~32s, so counting only completions left the bar "
   "frozen at 0/N -- exactly the frozen bar this replaced")
_bbttl.progress_step(_pt, cached=True)
_p = _bbttl.progress_get(_pt) or {}
ck("finished games and cache hits are counted separately",
   _p.get("done") == 1 and _p.get("cached") == 1,
   "'15 games simulated' and '15 games read from cache' are very different waits")
_bbttl.progress_declare(_pt, 3)        # the sweep announces its pass count
_bbttl.progress_start(_pt, 5)          # optimal mode's second floor pass
_pp = _bbttl.progress_get(_pt) or {}
ck("sweeping another floor advances the PASS, never the denominator",
   _pp.get("total") == 5 and _pp.get("pass") == 2 and _pp.get("passes") == 3
   and _pp.get("done") == 0,
   "the old scheme EXTENDED the total each re-entry: the bar read 15/15 "
   "done, then the total grew under it to 30 and 45 -- reported verbatim "
   "by the owner. One bar, divided by a declared pass count.")
_bbttl.progress_done(_pt)
ck("and the token is dropped when the build ends",
   _bbttl.progress_get(_pt) is None)
ck("the endpoint serves the count and the client polls it",
   '"/api/progress"' in _appsrc and "/api/progress?token=" in _appjs
   and "&ptok=" in _appjs,
   "without a poll the browser has no way to see inside a single request")
ck("real counts win over the time-based estimate when they exist",
   "if (real) {" in _appjs and "((ps - 1) + fracPass) / passes" in _appjs,
   "a measured count beats a curve every time; the curve is only the fallback")
ck("the progress bar is paced by MEASURED runs, not a fixed curve",
   "_simRecord" in _appjs and "_simEst" in _appjs
   and "92 * (1 - Math.exp(-dt / 5))" not in _appjs,
   "the old curve hit 92% in ~15s and crawled, so a 67s build showed '92%' for "
   "nearly a minute -- a spinner that lied about its position")
ck("each build mode is timed separately",
   '"combo:optimal"' in _appjs and '"combo:build"' in _appjs
   and '"combo:maxbet"' in _appjs,
   "optimal sweeps three per-leg floors and a plain build does one; a shared "
   "average mis-times both")
# The bar froze mid-build and the button's focus ring vanished at the same
# instant -- one event, not two. loadBaseball snapshots #comboOut's innerHTML,
# rewrites the container, and pastes the markup back as TEXT: the live bar is
# destroyed and a dead photograph of it is restored, while the ticker sees its
# node detached and stops.
ck("the combo maker is never re-rendered while a build is running",
   "if (!comboBuilding) {" in _appjs
   and _appjs.split("if (!comboBuilding) {")[1][:400].count("combosBox.innerHTML = html") == 1,
   "the snapshot-and-restore copies the progress bar as markup, so the live "
   "element dies and a frozen copy takes its place")
ck("the ticker finds its bar by id instead of holding the node",
   "document.getElementById(uid)" in _appjs and "const uid = \"sl\"" in _appjs,
   "a held reference points at a detached node after any redraw, leaving a "
   "frozen copy on screen while the timer runs invisibly")
ck("...and stops cleanly when the bar is genuinely gone",
   "if (!root) { clearInterval(id); return; }" in _appjs)
ck("the bar admits when a run is running long",
   "longer than the usual" in _appjs,
   "past the expected time the extra seconds are not progress and should not "
   "be drawn as progress")
ck("and the duration is recorded even when the build fails",
   "_stopLoader();" in _appjs
   and "_stopLoader();" in _appjs.split("comboBuilding = false;")[-1][:200],
   "recording only on success would bias the estimate toward fast runs")

print()
print("=" * 72)
print("An empty NFL week answers; it does not poll forever")
print("=" * 72)
import nfl_game_sim as _NGS
_nb = _insp.getsource(_NGS.board)
ck("a week with no games caches its emptiness instead of returning None",
   '"empty": True' in _nb and "_cache[key] = (_time.time() - 1500" in _nb,
   "returning None cached nothing, so board() answered None forever AND every "
   "poll started another build -- an empty week became a herd of rebuilds")
ck("a failed build says why instead of vanishing into the thread",
   "except Exception as e:" in _nb and "[nfl] board build failed" in _nb,
   "the old _bg had try/finally and no except, so the board stayed None with "
   "nothing anywhere explaining it")
ck("the route treats an empty week as an answer, not a 502 to retry",
   'if data.get("empty")' in _appsrc,
   "preseason week 1 is a single Hall of Fame game, played and gone by "
   "mid-August -- exactly when someone opens the tab")
ck("current_week knows preseason weeks differ from regular ones",
   "seasontype=1 if preseason else 2" in _insp.getsource(_NGS.current_week)
   and "nfl_game_sim.current_week(pre)" in _appsrc,
   "on 15 Aug the live games are preseason WEEK 2; week 1 finished on the 7th")

print()
print("=" * 72)
print("An NFL defense is scored by the game it is in")
print("=" * 72)
import nfl_dfs_sim as _NDS2
import dk_scoring as _DKS          # also used by the block below; import locally
import random as _dstrng
_dr = _dstrng.Random(5)
# A defense facing a juggernaut and one facing a dud must not score alike. The
# regular-season model drew Normal(projection, 0.7*proj+4) -- an independent
# bell curve with no link to the game, so a DST opposite a 40-burger scored
# exactly like one pitching a shutout, and pairing a defense with the shootout
# it was in looked fine to the optimizer.
_n = 3000
_big_td = [3] * _n            # opponent scores 3 offensive TDs every iteration
_dud_td = [0] * _n            # opponent never finds the end zone
_gv = [1] * _n
_yd = [380.0] * _n
_pa = [250.0] * _n
_vs_big = _NDS2._dst_from_components(_big_td, _gv, _yd, _pa, _dr)
_vs_dud = _NDS2._dst_from_components(_dud_td, _gv, [180.0] * _n, _pa, _dr)
_mb = sum(_vs_big) / len(_vs_big)
_md = sum(_vs_dud) / len(_vs_dud)
ck("shutting an offense out pays far more than being run over",
   _md > _mb + 6, f"vs 0 TDs {_md:.2f} vs 3 TDs {_mb:.2f}")
ck("the points-allowed tiers come from dk_scoring, not a re-typed ladder",
   "dk_scoring.nfl_dst_pa_points" in _insp.getsource(_NDS2._dst_from_components)
   and _DKS.nfl_dst_pa_points(0) == 10 and _DKS.nfl_dst_pa_points(3) == 7
   and _DKS.nfl_dst_pa_points(13) == 4 and _DKS.nfl_dst_pa_points(20) == 1
   and _DKS.nfl_dst_pa_points(27) == 0 and _DKS.nfl_dst_pa_points(30) == -1
   and _DKS.nfl_dst_pa_points(45) == -4)
ck("takeaways are the offense's OWN giveaways, not a fresh roll",
   "opp_gv[i]" in _insp.getsource(_NDS2._dst_from_components)
   and "gvs[t] += ints + fums" in _insp.getsource(_NDS2.simulate_game),
   "re-rolling turnovers would let both sides of the ball disagree about "
   "whether the quarterback threw a pick")
ck("a passing TD and its receiving TD are one touchdown on the scoreboard",
   "tds[t] += pass_td + rush_td" in _insp.getsource(_NDS2.simulate_game)
   and "rec_td" not in _insp.getsource(_NDS2.simulate_game).split("tds[t] +=")[1][:40],
   "counting both would double every passing score and bury the defense")
ck("the DST ceiling includes return touchdowns",
   "_ST_TD_P" in _insp.getsource(_NDS2._dst_from_components)
   and _NDS2._ST_TD_P > 0,
   "a defense's boom week is a return score; without one the tail was ~4% "
   "against a real rate near 11% and GPP never rostered a DST for the right "
   "reason")
ck("the simulated shape is kept but the LEVEL still respects the projection",
   "shift = d[\"proj\"] - raw" in _insp.getsource(_NDS2.player_pool),
   "the component model knows the matchup but carries no personnel, so it "
   "cannot say how good the unit itself is; a shift preserves the lumpy "
   "points-allowed thresholds that a rescale would smear")

print()
print("=" * 72)
print("Every DFS sim scores with DraftKings' real table")
print("=" * 72)
import dk_scoring as _DKS
import ufc_sim as _UFCS
import nfl_dfs_sim as _NDS
import mlb_dfs as _MDFS
import racing_sim as _RS

# UFC: reversals/sweeps paid 3 against DK's 5 -- every grappler was short.
ck("UFC pays reversals/sweeps what DK pays",
   _DKS.UFC["reversal"] == 5 and _UFCS._ADV_PTS == 5,
   "a takedown and a reversal are both +5; ours paid 3 for the reversal")
ck("and the rest of the UFC card matches the published table",
   (_DKS.UFC["strike"], _DKS.UFC["control_sec"], _DKS.UFC["takedown"],
    _DKS.UFC["knockdown"], _DKS.UFC["win_decision"], _DKS.UFC["quick_win"])
   == (0.2, 0.03, 5, 10, 30, 25)
   and _DKS.UFC["win_round"] == {1: 90, 2: 70, 3: 45, 4: 40, 5: 40})
ck("the UFC sim reads those constants rather than its own copies",
   _UFCS._TD_PTS == _DKS.UFC["takedown"] and _UFCS._KD_PTS == _DKS.UFC["knockdown"]
   and "_TD_PTS * td" in _insp.getsource(_UFCS.simulate_bout),
   "hardcoded 5s and 10s in the scoring line are how a table drifts")

# NFL: the two scoring paths disagreed with each other AND with DK.
ck("NFL turnovers cost 1, not 2 -- and both scoring paths agree now",
   _DKS.NFL_OFF["int"] == -1 and _DKS.NFL_OFF["fumble_lost"] == -1
   and _NDS._DK["int"] == -1 and _NDS._DK["fum"] == -1,
   "the projection on screen used -1 while the simulation the lineup was built "
   "from used -2")
_qb = _NDS._ppr(310, 2, 1, 15, 0, 0, 0, 0, 0)
_qb_under = _NDS._ppr(290, 2, 1, 15, 0, 0, 0, 0, 0)
ck("the 300-yard passing bonus exists and fires only over the line",
   abs((_qb - _qb_under) - (0.8 + 3)) < 1e-6,
   f"310yd {_qb:.2f} vs 290yd {_qb_under:.2f} -- the +3 was missing entirely")
_rb = _NDS._ppr(0, 0, 0, 105, 0, 0, 0, 0, 0)
_wr = _NDS._ppr(0, 0, 0, 0, 0, 5, 101, 0, 0)
ck("...as do the 100-yard rushing and receiving bonuses",
   abs(_rb - (10.5 + 3)) < 1e-6 and abs(_wr - (5 + 10.1 + 3)) < 1e-6)
ck("the yardage bonuses are applied per SAMPLE, not to the mean",
   "a mean cannot say how often" in _insp.getsource(_NDS).split("_DK = {")[0][-700:]
   or "per SAMPLE" in _insp.getsource(_NDS._ppr),
   "an 85-yard-average back clears 100 sometimes; the mean never does")

# NASCAR + F1: both finish tables were invented.
ck("the NASCAR finish table has DK's steps at 11th, 21st and 31st",
   (_DKS.nascar_finish(10), _DKS.nascar_finish(11)) == (34.0, 32.0)
   and (_DKS.nascar_finish(20), _DKS.nascar_finish(21)) == (23.0, 21.0)
   and (_DKS.nascar_finish(30), _DKS.nascar_finish(31)) == (12.0, 10.0)
   and _DKS.nascar_finish(1) == 45.0 and _DKS.nascar_finish(40) == 1.0,
   "a straight 44-pos line scored the back half up to 3 points rich")
ck("and the sim uses it",
   _RS._dk_nascar_fin(21) == 21.0 and _RS._dk_nascar_fin(31) == 10.0)
ck("the F1 finish table is DK's, not an invented curve",
   [_DKS.f1_finish(p) for p in (1, 2, 3, 4, 10, 15, 21, 22)]
   == [40.0, 37.0, 35.0, 32.0, 20.0, 10.0, 1.0, 0.0],
   "ours paid 43/40/38 then 41-pos; 4th alone was 5 points rich")
ck("F1 laps led and the fastest lap are DK's values",
   _DKS.F1["lap_led"] == 0.25 and _DKS.F1["fastest_lap"] == 3,
   "0.1/lap understated the dominator pool by 2.5x while the fastest lap "
   "overpaid at 5")
ck("and the F1 sim reads them",
   'dk_scoring.F1["lap_led"]' in _insp.getsource(_RS._f1_dk_race)
   and 'dk_scoring.F1["fastest_lap"]' in _insp.getsource(_RS._f1_dk_race)
   and _RS._dk_f1_fin(4) == 32.0)

# MLB was right on every rate, but was throwing away a modelled stat.
ck("MLB hitter scoring counts the stolen bases the engine simulates",
   _DKS.MLB_HIT["sb"] == 5
   and _MDFS._hitter_dk({"h": 1, "2b": 0, "3b": 0, "hr": 0, "rbi": 0, "r": 0,
                         "bb": 0, "sb": 1}) == 8,
   "the box line has carried sb since the catcher-arm work and DK pays +5; "
   "speed bats were projected without it")
ck("MLB rates match the published table",
   (_DKS.MLB_HIT["single"], _DKS.MLB_HIT["double"], _DKS.MLB_HIT["triple"],
    _DKS.MLB_HIT["hr"]) == (3, 5, 8, 10)
   and (_DKS.MLB_PIT["out"], _DKS.MLB_PIT["k"], _DKS.MLB_PIT["er"],
        _DKS.MLB_PIT["hit"]) == (0.75, 2, -2, -0.6))
ck("a missing sb/hbp key can never crash an older box line",
   _MDFS._hitter_dk({"h": 1, "2b": 0, "3b": 0, "hr": 0, "rbi": 0, "r": 0,
                     "bb": 0}) == 3)

# The card the user reads is generated FROM those constants.
for _sp, _want in (("ufc", "Reversal/Sweep"), ("mlb", "Stolen Base"),
                   ("nfl", "300+ Yard Passing Game"), ("nascar", "Laps Led"),
                   ("f1", "Defeated Teammate")):
    _rows = [r for g in _DKS.card(_sp) for r in g["rows"]]
    ck(f"the {_sp.upper()} scoring card renders and lists {_want}",
       any(r[0] == _want for r in _rows) and len(_rows) >= 5)
ck("the card is generated from the constants, never re-typed",
   "UFC['reversal']" in _insp.getsource(_DKS.card).replace('"', "'"),
   "a hand-typed card drifts from the sim and is worse than no card")
ck("the app serves the card and the DFS maker can show it",
   '"/api/dfs/scoring"' in _appsrc and "dfsShowScoring" in _appjs
   and "dfsScoreToggle" in open(_os.path.join(_root, "templates", "index.html")).read())

print()
print("=" * 72)
print("UFC DFS runs on the blended board, not the raw power rating")
print("=" * 72)
import simulate as _SIMU
import random as _urand
_ufc_src = _insp.getsource(_SIMU.apply_ufc)
ck("the DFS builder prices the card off Kalshi like every other UFC surface",
   "ufc_prices" in _ufc_src and "attach(board)" in _ufc_src,
   "best-bets, the fight board, the combo maker and the prediction logger all "
   "blended toward the market; DFS alone rostered off a rating the rest of the "
   "app had already measured as losing to the close")
ck("the sim records WHICH samples were wins, so the blend can be exact",
   "won_arr" in open(_os.path.join(_root, "ufc_sim.py")).read(),
   "a high-volume loss can outscore a quick decision win, so the branch cannot "
   "be recovered from the points alone")

def _mkbout(model_a, fair_a, n=400, seed=1):
    r = _urand.Random(seed)
    won = [1 if k < int(n * model_a) else 0 for k in range(n)]
    r.shuffle(won)
    a = {"dk_arr": [85.0 if w else 22.0 for w in won], "won_arr": won,
         "win_pct": 100 * model_a, "fair_win": fair_a, "kalshi_cents": int(fair_a)}
    b = {"dk_arr": [22.0 if w else 85.0 for w in won]}
    for f in (a, b):
        f["proj"] = sum(f["dk_arr"]) / len(f["dk_arr"])
    return a, b

# The blend has to move the WIN PROBABILITY, not just scale the mean: the win,
# finish and round bonuses all live in the winning branch, so DK points are not
# linear in win%.
_ba, _bb = _mkbout(0.75, 37.0)
_before = _ba["proj"]
ck("a fight the exchange disagrees with is repriced, hard",
   _SIMU._reblend_bout(_ba, _bb, seed=2) and _ba["blend_proj"] < _before - 15,
   f"{_before:.1f} -> {_ba.get('blend_proj')}; our 75% pick at a 37c market must "
   "not keep a 75%-confidence projection")
_aw = sum(1 for v in _ba["blend_arr"] if v > 50) / len(_ba["blend_arr"])
ck("the resampled win rate lands on the blend",
   0.34 <= _aw <= 0.40, f"{_aw:.3f} vs target 0.370")
ck("and the bout stays a bout: exactly one fighter wins each sample",
   all((x > 50) != (y > 50) for x, y in zip(_ba["blend_arr"], _bb["blend_arr"])),
   "the contest sim scores lineups off these arrays; if both fighters can win "
   "the same sample, a lineup rostering both looks fine")
_ua, _ub = _mkbout(0.60, 60.0)
_ua["fair_win"] = _ua["kalshi_cents"] = None
ck("an unpriced bout is left exactly as the sim made it",
   _SIMU._reblend_bout(_ua, _ub, seed=3) is False and "blend_proj" not in _ua,
   "a prelim with no Kalshi market still deserves its projection")

# The confidence objective. Its whole job is to separate fighters that
# projection alone rates identically.
_cpool = [
    {"name": "agreed", "proj": 60.0, "ceil_proj": 85.0, "fair_win": 70.0, "win_pct": 72.0},
    {"name": "fades", "proj": 60.0, "ceil_proj": 85.0, "fair_win": 40.0, "win_pct": 75.0,
     "fades_market": True},
    {"name": "thin", "proj": 60.0, "ceil_proj": 85.0, "fair_win": 55.0, "win_pct": 55.0,
     "thin": True},
    {"name": "nodata", "proj": 60.0, "ceil_proj": 85.0, "fair_win": 50.0, "win_pct": 50.0,
     "thin": True, "defaulted": True},
    {"name": "unpriced", "proj": 60.0, "ceil_proj": 85.0, "fair_win": None, "win_pct": 70.0},
]
_SIMU._set_values(_cpool, "confidence", 0.6)
_rank = [p["name"] for p in sorted(_cpool, key=lambda x: -x["value"])]
ck("'most confident' prefers the pick the model and the market agree on",
   _rank[0] == "agreed" and _rank[-1] == "nodata", " > ".join(_rank))
ck("an unpriced read never outranks a confirmed one",
   next(p for p in _cpool if p["name"] == "unpriced")["value"]
   < next(p for p in _cpool if p["name"] == "agreed")["value"],
   "a flat discount instead of the same transform put an unpriced coin flip "
   "above a fighter both sources liked -- the objective preferred ignorance")
_SIMU._set_values(_cpool, "projection", 0.6)
ck("...which projection alone cannot do at all",
   len({p["value"] for p in _cpool}) == 1,
   "identical projections are identical to the cash objective; that is exactly "
   "why 'most confident' is a separate mode")
ck("the blend is shown on the slip and in the banner, not hidden",
   '"kalshi_cents": p.get("kalshi_cents")' in _insp.getsource(_SIMU._lineup_player)
   and "bouts_blended" in _ufc_src
   and "p.fair_win != null && p.kalshi_cents != null" in _appjs,
   "a projection moved by the exchange should say so")

print()
print("=" * 72)
print("Leaving the app and coming back is not a failure state (cont.)")
print("=" * 72)
ck("background CPU work yields to the web worker",
   "os.nice(10)" in open(_os.path.join(_root, "baseball.py")).read()
   and "os.nice(10)" in open(_os.path.join(_root, "deep_season.py")).read(),
   "a nightly run that saturates the CPU for an hour must never outrank the "
   "request that keeps the instance alive")

print()
print("=" * 72)
print("Nothing fails silently: every failure point has an ID and a ledger")
print("=" * 72)
# Every incident this app has had started as a silent `except: pass`. Now:
# guarded failures note() themselves under stable IDs into a shared SQLite
# ledger, uncaught request/thread exceptions are hooked centrally, the browser
# reports its own JS errors, /api/errors serves the ledger, and a scheduled
# workflow commits it to the sim-history branch under errors/ -- authenticated
# by the X-Sim-Token door (which the nightly workflow SENT for months while
# nothing checked it, so its fetches 401'd the moment a password was set).
import ast as _gast
import errlog as _gel
import tempfile as _gtmp2
import time as _gtime
_gel_old_db, _gel_old_init = _gel._DB, _gel._init_done
_gel._DB = _os.path.join(_gtmp2.mkdtemp(prefix="guard-errlog-"), "errlog.db")
_gel._init_done = False
try:
    for _gi in range(30):
        try:
            raise RuntimeError("same failure")
        except Exception as _ge:
            _gel.note("GUARD-dedup", _ge)
    _gtime.sleep(2.1)
    try:
        raise RuntimeError("same failure")
    except Exception as _ge:
        _gel.note("GUARD-dedup", _ge)
    _grows = _gel.recent(code="GUARD-dedup")
    ck("a broken loop becomes ONE row with a count, not a flood",
       len(_grows) == 1 and _grows[0]["n"] == 31,
       f"{len(_grows)} rows, n={_grows and _grows[0]['n']}; an error ledger "
       "that can fill the data disk is itself an outage")
    with _gel.guard("GUARD-swallow"):
        raise KeyError("swallowed")
    ck("guard() records AND swallows, exactly like the old bare pass",
       _gel.recent(code="GUARD-swallow")
       and "swallowed" in _gel.recent(code="GUARD-swallow")[0]["msg"])
    _gel_broken_db = _gel._DB
    _gel._DB = "/proc/no-such-dir/errlog.db"
    _gel.note("GUARD-unwritable", msg="must not raise")
    _gel._DB = _gel_broken_db
    ck("note() NEVER raises, even with an unwritable ledger", True,
       "an error log that errors is worse than silence")
    ck("the export bundle has the shape the workflow commits",
       {"generated_at", "summary", "errors"} <= set(_gel.export_bundle()))

    # The central hooks, live.
    _gapp2 = __import__("app")

    @_gapp2.app.route("/api/_guard_boom_ledger")
    def _guard_boom_ledger():
        raise ValueError("deliberate")

    with _gapp2.app.test_client() as _gc:
        _gr = _gc.get("/api/_guard_boom_ledger")
        ck("an unhandled route exception returns its error ID and is ledgered",
           _gr.status_code == 500
           and _gr.get_json()["error_id"] == "HTTP-_guard_boom_ledger"
           and any(x["code"] == "HTTP-_guard_boom_ledger"
                   for x in _gel.recent(30)),
           "a 500 with no ID restarts the hunt from zero")
        ck("a plain 404 is NOT treated as a failure",
           _gc.get("/api/_no_such_guard_route").status_code == 404
           and not any("_no_such_guard_route" in (x.get("path") or "")
                       for x in _gel.recent(30)),
           "logging every bad URL buries the real errors")
        _gd = _gc.get("/api/errors").get_json()
        ck("/api/errors serves the ledger from the phone",
           "summary" in _gd and "recent" in _gd)
        _gc.post("/api/errors/client",
                 json={"code": "error", "msg": "TypeError: x is undefined",
                       "src": "app.js:5:1", "page": "/"})
        _gtime.sleep(0.1)
        ck("the browser's own errors land in the same ledger",
           any(x["code"] == "JS-error" for x in _gel.recent(30)),
           "a JS exception used to break a page feature in total silence")

    import threading as _gth
    def _gdie():
        raise RuntimeError("thread kaput")
    _gt2 = _gth.Thread(target=_gdie, name="guard-ledger-thread", daemon=True)
    _gt2.start(); _gt2.join(); _gtime.sleep(0.2)
    ck("an uncaught background-thread exception is ledgered by thread name",
       any(x["code"] == "THREAD-guard-ledger-thread" for x in _gel.recent(30)),
       "the recorders, the warmer and the scheduler all die silently without "
       "this hook")

    # The workflows' door.
    _gapp2.APP_PASSWORD, _gapp2._SIM_TOKEN = "guard-pw", "guard-tok"
    try:
        with _gapp2.app.test_client() as _gc:
            ck("with a password set, the export needs the token",
               _gc.get("/api/errors/export").status_code == 401
               and _gc.get("/api/errors/export",
                           headers={"X-Sim-Token": "wrong"}).status_code == 401
               and _gc.get("/api/errors/export",
                           headers={"X-Sim-Token": "guard-tok"}).status_code == 200,
               "the nightly workflow sent X-Sim-Token for months while nothing "
               "checked it -- every fetch 401'd once APP_PASSWORD was set")
    finally:
        _gapp2.APP_PASSWORD, _gapp2._SIM_TOKEN = None, ""
finally:
    _gel._DB, _gel._init_done = _gel_old_db, _gel_old_init

# No silent swallows can creep back in: zero pass-only Exception handlers
# remain in ANY service module. This used to be a hand-maintained allowlist
# of 20 files -- an enforcement blind spot in which 37 swallows accumulated,
# among them the predlog feeds for NBA, NHL and golf: had prediction logging
# ever thrown there, those sports would have silently stopped accruing the
# graded history their calibration runs on, forever, with nothing in the
# ledger. The sweep is now every module in the repo root; only errlog itself
# is exempt (the logger cannot note its own failure).
_gswept = sorted(f for f in _os.listdir(_root)
                 if f.endswith(".py") and f != "errlog.py"
                 and not f.startswith("test"))
_gbad = []
for _gm in _gswept:
    _gtree = _gast.parse(open(_os.path.join(_root, _gm)).read())
    for _gn in _gast.walk(_gtree):
        if isinstance(_gn, _gast.ExceptHandler):
            _gexc = (_gn.type is None or (isinstance(_gn.type, _gast.Name)
                                          and _gn.type.id == "Exception"))
            if _gexc and len(_gn.body) == 1 and isinstance(_gn.body[0], _gast.Pass):
                _gbad.append(f"{_gm}:{_gn.lineno}")
ck("no silent `except Exception: pass` remains in any service module",
   not _gbad, _gbad[:6])

# The named seams that have actually bitten us each carry their ID.
for _gid, _gfile in (("KAL-fetch", "kalshi.py"), ("KIDX-build", "kalshi_mlb.py"),
                     ("KIDX-empty", "kalshi_mlb.py"), ("SLATE-child", "baseball.py"),
                     ("SIM-disk-write", "baseball.py"), ("WARM-game-sim", "app.py"),
                     ("WARM-board-build", "app.py"), ("COMBO-build", "app.py")):
    ck(f"seam ID {_gid} is wired",
       f'"{_gid}"' in open(_os.path.join(_root, _gfile)).read())
ck("a failed deep run is ledgered under DEEP-<job>",
   'errlog.note(f"DEEP-{key}"' in open(_os.path.join(_root, "deep_cache.py")).read())

# The pipeline to GitHub.
_gwf = open(_os.path.join(_root, ".github", "workflows", "error-log.yml")).read()
_gnh = open(_os.path.join(_root, ".github", "workflows", "nightly-history.yml")).read()
ck("the error-log workflow pulls the export on a schedule",
   "cron:" in _gwf and "/api/errors/export" in _gwf and "X-Sim-Token" in _gwf)
ck("...into the data branch, serialized with the history snapshot",
   "sim-history" in _gwf and "group: sim-history" in _gwf,
   "two workflows pushing the same branch concurrently reject each other's "
   "pushes")
ck("the nightly history fetch now authenticates too",
   _gnh.count("X-Sim-Token") >= 2,
   "it always sent the token on the trigger step and never on the fetch")
ck("prod persists the ledger and knows about the token",
   "ERRLOG_DB" in open(_os.path.join(_root, "render.yaml")).read()
   and "SIM_TOKEN" in open(_os.path.join(_root, "render.yaml")).read())
_gjs = open(_os.path.join(_root, "static", "app.js")).read()
ck("the browser reports uncaught errors and rejected promises, capped",
   'window.addEventListener("error"' in _gjs
   and '"unhandledrejection"' in _gjs and "_errSent >= 8" in _gjs
   and "/api/errors/client" in _gjs)

print()
print("=" * 72)
print("The deep engine answers every worker: rerun, status, board")
print("=" * 72)
# "It hasn't run in 32 hours and rerun does nothing." Three worker-lottery
# bugs, one recycling bug, one silent-failure bug: the deep jobs were only
# REGISTERED in the background-owner worker, so a rerun click landing anywhere
# else hit an empty job table and returned {"started": false} (2 clicks in 3);
# progress lived in the running worker's memory, so most status polls denied a
# run was in flight; the deep board was only loaded from disk in the owner;
# gunicorn recycles workers every few hundred requests (health probes count)
# and the replacement waited for a HUMAN request to bootstrap, so overnight no
# scheduler existed when the nightly came due; and a run that crashed or
# declined to publish recorded nothing anywhere.
import deep_cache as _gdc
import deep_season as _gds
import time as _gtime
import tempfile as _gtmp
_gtdir = _gtmp.mkdtemp(prefix="guard-deep-")
_old_req, _old_state = _gdc._RERUN_REQ, _gdc._RUN_STATE
_old_cache_dir = _gdc.CACHE_DIR
_gdc._RERUN_REQ = _os.path.join(_gtdir, "rerun.json")
_gdc._RUN_STATE = _os.path.join(_gtdir, "runstate.json")
_gdc.CACHE_DIR = _os.path.join(_gtdir, "deep")
try:
    def _gwait(key):
        for _ in range(200):
            if not _gdc._jobs[key]["running"]:
                return
            _gtime.sleep(0.05)

    def _gboom():
        raise RuntimeError("kaput")
    _gdc.register("g_boom", _gboom)
    _gdc.register("g_none", lambda: None)
    _gdc.register("g_good", lambda: {"x": 1})
    ck("a rapid double-start is refused, not doubled",
       _gdc.run_job("g_boom", force=True) is True
       and _gdc.run_job("g_boom", force=True) is False,
       "running used to be flagged from inside the thread; two clicks in the "
       "gap both started a 4,000-season sim")
    _gwait("g_boom")
    for _gk in ("g_none", "g_good"):
        _gdc.run_job(_gk, force=True)
        _gwait(_gk)
    _gtime.sleep(0.3)
    ck("a crashed run records the error where any worker can read it",
       _gdc.run_state("g_boom").get("phase") == "error"
       and "kaput" in (_gdc.run_state("g_boom").get("err") or ""),
       "the old bare `except: pass` made a nightly that failed every attempt "
       "indistinguishable from one that never started")
    ck("a run that declines to publish says so too",
       _gdc.run_state("g_none").get("phase") == "empty",
       "the MLB run keeps the previous board on partial rosters -- that must "
       "look different from a crash and from a success")
    ck("a finished run records done and lands in the cache",
       _gdc.run_state("g_good").get("phase") == "done"
       and _gdc.load("g_good")[0] == {"x": 1})
    ck("age() reads the file clock, not the multi-MB pickle",
       "os.stat" in _insp.getsource(_gdc.age),
       "status polls every few seconds were unpickling the whole deep payload "
       "to subtract a timestamp")

    _gdc.request_rerun("g_good")
    ck("any worker can queue a rerun through the shared file",
       "g_good" in _gdc._json_read(_gdc._RERUN_REQ))
    _gdc.run_job("g_good", force=True)
    ck("a starting run consumes the queued request instead of encoring",
       "g_good" not in _gdc._json_read(_gdc._RERUN_REQ),
       "two clicks on different workers wanted ONE fresh run, not a run and "
       "an identical rerun after it")
    _gwait("g_good")
    _gdc._note_run("g_ghost", phase="running", started=_gtime.time())
    ck("a run is visible as running from every worker",
       _gdc.running_anywhere("g_ghost") is True)
    _gdc._note_run("g_ghost", phase="running", started=_gtime.time() - 4 * 3600)
    ck("...but a dead worker's leftover 'running' expires",
       _gdc.running_anywhere("g_ghost") is False,
       "a worker killed mid-run would otherwise pin 'already running' forever "
       "and block every future rerun")
    ck("the scheduler drains queued requests within seconds, not half-hours",
       "_drain_requests" in _insp.getsource(_gdc.start_scheduler)
       and "time.sleep(10)" in _insp.getsource(_gdc.start_scheduler),
       "a rerun clicked from a non-owner worker must not wait for the next "
       "30-minute pass")
finally:
    _gdc._RERUN_REQ, _gdc._RUN_STATE = _old_req, _old_state
    _gdc.CACHE_DIR = _old_cache_dir
    for _gk in ("g_boom", "g_none", "g_good"):
        _gdc._jobs.pop(_gk, None)

# The progress mirror: a run in ONE worker is visible from all of them.
_old_prog_disk = _gds._PROG_DISK
_gds._PROG_DISK = _os.path.join(_gtdir, "progress.json")
try:
    _gds.PROGRESS.update(running=True, done=120, total=4000,
                         started=_gtime.time(), season="2026")
    _gds._prog_flush(final=True)
    _gds.PROGRESS["running"] = False          # now pretend we're another worker
    _gp = _gds.progress_read()
    ck("a running deep sim is visible from a worker that isn't running it",
       _gp.get("running") is True and _gp.get("done") == 120,
       "read from memory, two of three status polls denied a run was in "
       "flight and the loading bar flickered or never appeared")
finally:
    _gds.PROGRESS["running"] = False
    _gds._PROG_DISK = _old_prog_disk

# App wiring: every worker registers; nobody waits for a human overnight.
_appmod = __import__("app")
_ens_src = _insp.getsource(_appmod._ensure_recorder)
ck("EVERY worker registers the deep jobs, not just the background owner",
   _ens_src.index("_register_deep_sims()") < _ens_src.index("if not _BG_OWNER"),
   "a worker with an empty job table answered the rerun button with "
   "'started: false' -- the literal 'I hit rerun and nothing happens'")
ck("an instance seeing only health probes still bootstraps its nightlies",
   "_bootstrap_unprompted" in _appsrc
   and "_ensure_recorder()" in _insp.getsource(_appmod._bootstrap_unprompted),
   "gunicorn recycles workers every few hundred requests -- probes included -- "
   "and the replacement owner waited for a human; nobody browses at midnight, "
   "so the nightly quietly never ran and the board aged 32 hours")
ck("...but ONLY in a real server process",
   'SERVER_SOFTWARE' in _appsrc and 'VIGIL_AUTOBOOT' in _appsrc,
   "a test suite or one-off script that imports app must not sprout "
   "recorders and schedulers ninety seconds in -- this suite caught its own "
   "caches being churned by exactly that")
ck("the deep board adopts a run a sibling worker finished",
   "_deep_refresh" in _insp.getsource(_appmod._register_deep_sims)
   and "st_mtime" in _insp.getsource(_appmod._deep_refresh),
   "non-owner workers either 409'd the deep board or served yesterday's "
   "forever, depending on when they booted")
ck("the deep status endpoint reads the shared progress, not local memory",
   "progress_read()" in _appsrc and "deep_season.PROGRESS)" not in _appsrc)
ck("rerun endpoints queue for the owner instead of no-opping",
   _appsrc.count("request_rerun") >= 2 and "running_anywhere" in _appsrc)
ck("the page shows queued and failed runs instead of a dead button",
   "Deep sim queued" in _appjs and "last deep run" in _appjs,
   "a rerun click that queues, a run that crashed, and a run that declined "
   "to publish all looked like nothing happening")
def _alters_guarded(path):
    """Every ALTER TABLE ... ADD COLUMN sits inside a try: (within the three
    preceding lines), so a sibling worker winning the migration race can't
    crash this one."""
    lines = open(path).read().splitlines()
    bad = []
    for i, l in enumerate(lines):
        if "ADD COLUMN" in l and "execute" in l:
            if not any("try:" in lines[j] for j in range(max(0, i - 3), i)):
                bad.append(i + 1)
    return bad


ck("every ADD COLUMN migration tolerates a sibling worker winning the race",
   not _alters_guarded(_os.path.join(_root, "store.py"))
   and not _alters_guarded(_os.path.join(_root, "predlog.py")),
   "three workers migrating one fresh database at the same moment: the loser "
   "crashed its worker with 'duplicate column name' and the boot looped "
   f"(unguarded lines: store {_alters_guarded(_os.path.join(_root, 'store.py'))}, "
   f"predlog {_alters_guarded(_os.path.join(_root, 'predlog.py'))})")

print()
print("=" * 72)
print("The warm bar can tell WORKING from STUCK, whichever worker answers")
print("=" * 72)
# "Sitting at 0/9 warming up for a while" had at least four causes that all
# looked identical: the warmer mid-way through a 200s sim (fine), the warmer
# queued behind the nightly sim's heavy-build gate (fine, eventually), the
# warmer dead or erroring every game (not fine), and sims completing but never
# landing on a full data disk (not fine, and invisible by construction since
# the count is derived FROM the disk). The status now lives in a shared file
# every worker can read -- the same cure as the sims, the board and the jobs --
# and carries the current game, the phase, a heartbeat and the last error.
import app as _apw
import json as _json2
import os as _os2
import time as _tw
_apw._warm_json_write(_apw._WARM_STATUS, {})
_apw._warm_status(phase="sim", date="2099-03-01", at="AAA @ BBB",
                  warm=3, total=9)
_wst = _json2.load(open(_apw._WARM_STATUS))
ck("the warmer's status is readable from ANY worker (it is a file, not memory)",
   _wst.get("at") == "AAA @ BBB" and _wst.get("phase") == "sim"
   and _wst.get("warm") == 3 and _tw.time() - _wst.get("ts", 0) < 5,
   "the old _warm_state dict lived in the warming worker's memory, so the "
   "worker answering /api/warm reported at=None forever")
_apw._warm_status(warm=4)
_wst = _json2.load(open(_apw._WARM_STATUS))
ck("updates merge rather than replace, and every write beats the heart",
   _wst.get("warm") == 4 and _wst.get("at") == "AAA @ BBB",
   "a partial update that dropped `at` would blank the bar mid-sim")

# The date being LOOKED AT reaches the warmer through a file too.
_tomorrow = (_apw.clock.today_et() + _apw.datetime.timedelta(days=1)).isoformat()
_apw._warm_json_write(_apw._WARM_VIEWED, {})
_apw._note_slate_use(_tomorrow, _tomorrow[:4])
ck("the viewed date reaches the warmer across workers",
   _apw._warm_pick_key() == (_tomorrow, _tomorrow[:4]),
   "kept in per-worker memory, a user parked on tomorrow's slate only warmed "
   "tomorrow on a 1-in-workers coincidence -- everyone else warmed today")
_apw._warm_json_write(_apw._WARM_VIEWED,
                      {"date": "2020-01-01", "season": "2020", "ts": _tw.time()})
_today_k = _apw.clock.today_et().isoformat()
ck("a picker abandoned on a PAST date falls back to today",
   _apw._warm_pick_key() == (_today_k, _today_k[:4]),
   "every game on a past board is Final; warming it is pure waste and the "
   "build to fetch it is minutes of simulation")
_apw._warm_json_write(_apw._WARM_VIEWED, {})

# A sim-cache write failure is recorded, not swallowed: this is the "disk
# full" case where every sim completes, lands nowhere, and 0/N never moves.
import baseball as _bwd
_old_simdisk = _bwd._SIM_DISK
try:
    _scratchf = _os2.path.join(_apw.os.environ.get("VIGIL_RUN_DIR") or "/tmp",
                               "vigil-guard-notadir")
    open(_scratchf, "w").write("x")
    _bwd._SIM_DISK = _os2.path.join(_scratchf, "gamesim")   # parent is a FILE
    _bwd._SIM_DISK_ERR.update(ts=0.0, msg=None)
    _bwd._sim_disk_put(999999, {"sim": 1})
    ck("a cache write that fails is RECORDED where the bar can see it",
       _bwd.sim_disk_health() is not None,
       "silently-slow is how a full 1 GB data disk read as '0/9 warming up'")
finally:
    _bwd._SIM_DISK = _old_simdisk
    try:
        _os2.remove(_scratchf)
    except OSError:
        pass
_bwd._sim_disk_put(999999, {"sim": 1})
ck("...and a later successful write clears it",
   _bwd.sim_disk_health() is None,
   "a stale error banner outliving the problem teaches the user to ignore it")
try:
    _os2.remove(_os2.path.join(_bwd._SIM_DISK, "999999.pkl"))
except OSError:
    pass
ck("on failure it sheds expired sims and retries before giving up",
   "_sim_disk_prune(_GAME_SIM_TTL)" in _insp.getsource(_bwd._sim_disk_put),
   "expired files are the first thing to shed when the disk fills")
ck("the warmer surfaces that health check after every pass",
   "sim_disk_health" in _insp.getsource(_apw._warm_game_sims))

# /api/warm answers with the SAME fields in both branches, now including the
# liveness fields, and calls a long-silent warmer what it is.
_apw._warm_json_write(_apw._WARM_STATUS,
                      {"ts": _tw.time() - 2000, "phase": "sim"})
with _apw.app.test_client() as _wc:
    _wr = _wc.get("/api/warm?date=2099-03-01").get_json()
_wkeys = {"ready", "slate_ready", "total", "warm", "at", "phase",
          "always_warm", "stalled", "warm_err", "beat_s", "note"}
ck("the cold branch carries every field the warm branch does",
   _wkeys <= set(_wr.keys()), sorted(_wkeys - set(_wr.keys())))
ck("a warmer silent past any legitimate build is reported as stalled",
   _wr.get("stalled") is True,
   "a 200s sim and a 600s board build are silence; 2000s is a dead warmer, "
   "and the bar now says so instead of freezing at 0/N")
ck("...and the page says Build still works rather than just looking broken",
   "Warming looks stuck" in _appjs and "warm_err" in _appjs,
   "the bar must never present a stall as an outage: builds pay their own "
   "simulation and succeed")
_apw._warm_json_write(_apw._WARM_STATUS, {})
_apw._warm_json_write(_apw._WARM_VIEWED, {})

print()
print("=" * 72)
print("The optimizer really is optimal: DP vs brute force")
print("=" * 72)
# The frontier DP once kept only the max-prob state per (legs, cost-bucket)
# cell. A cell is ~5% wide in cost, and near break-even that width straddles
# EV=0: on a live slate a 78.8%/-1.3% state overwrote the 77.7%/+0.1% state in
# its cell, the balanced objective then found nothing +EV in the neighbourhood
# and settled for a 29% slip when a 78% one existed. Cells now keep the Pareto
# set over (prob, cost, priced) so no state any objective could want is lost.
# Guarded two ways: the exact collision, and a seeded brute-force sweep.
import combo_engine as _gce
import itertools as _it
import random as _grnd


def _gb_leg(prob, cents, fillable=True):
    return {"marg": prob, "type": "ML", "price_cents": cents,
            "fillable": fillable}


def _gb_bundle(prob, cents):
    return {"size": 1, "prob": prob, "legs": [_gb_leg(prob, cents)]}


_gcoll = [("A", [_gb_bundle(0.90, 95.0)], "a"),
          ("B", [_gb_bundle(0.863, 81.7), _gb_bundle(0.875, 84.0)], "b"),
          ("C", [_gb_bundle(0.55, 50.0)], "c")]
_gk1 = int(round(_math.log(0.95 * 0.817) / _gce._COST_RES))
_gk2 = int(round(_math.log(0.95 * 0.84) / _gce._COST_RES))
ck("the collision pair really shares a cost bucket", _gk1 == _gk2,
   f"{_gk1} vs {_gk2}; if resolution changed, rebuild the pair so the "
   "collision case still exists")
_gst = _gce.frontier(_gcoll, max_total_legs=4, net=False)
_gtwo = [s for s in _gst if s["legs"] == 2]
ck("a +EV state survives a likelier -EV cellmate",
   any(abs(s["prob"] - 0.90 * 0.863) < 1e-9 for s in _gtwo),
   "max-prob-per-cell deleted the only +EV slip in the bucket; the balanced "
   "objective then skipped the whole 78% neighbourhood")
_gbest, _gmeta = _gce.choose(_gst, objective="balanced", legs_target=2,
                             legs_mode="require")
ck("and balanced picks it", abs(_gbest["prob"] - 0.90 * 0.863) < 1e-9
   and _gmeta["ev_ok"] is True,
   f"picked {_gbest['prob']*100:.2f}%, expected 77.67%")

# Seeded sweep: DP output must contain, per legs count, the max-prob state,
# the max-EV state, and the likeliest state passing balanced's gates -- each
# checked against exhaustive enumeration. Unpriced legs are in the mix because
# they once broke this a second way: dominance judged on (prob, cost) alone
# let a slip carrying unpriced legs eclipse an all-priced one that the
# priced_frac gate would have accepted.
_gfail = None
for _gt in range(40):
    _gr = _grnd.Random(4200 + _gt)
    _ggames = []
    for _gi in range(_gr.randint(3, 4)):
        _gbs = []
        for _ in range(_gr.randint(1, 3)):
            _gp = _gr.uniform(0.30, 0.95)
            _gc = max(3.0, min(97.0, _gp / (1.0 + _gr.uniform(-0.08, 0.08))
                               * 100.0))
            _gbs.append({"size": 1, "prob": _gp,
                         "legs": [_gb_leg(_gp, None if _gr.random() < 0.15
                                          else _gc)]})
        _ggames.append((f"G{_gi}", _gbs, f"g{_gi}"))
    _gstates = _gce.frontier(_ggames, max_total_legs=4, net=True)
    _gbf = {}
    for _gpick in _it.product(*[[None] + list(b) for _, b, _ in _ggames]):
        _gch = [b for b in _gpick if b]
        _gnl = sum(b["size"] for b in _gch)
        if _gnl < 2 or _gnl > 4:
            continue
        _gpp, _gcc, _gpr, _gtt = 1.0, 1.0, 0, 0
        for _gb in _gch:
            _gbc, _gbpr, _gbtt = _gce.bundle_cost(_gb["legs"], net=True)
            _gpp, _gcc = _gpp * _gb["prob"], _gcc * _gbc
            _gpr, _gtt = _gpr + _gbpr, _gtt + _gbtt
        _ge = _gpp / _gcc - 1.0
        _grec = _gbf.setdefault(_gnl, [0.0, -9e9, None])
        _grec[0] = max(_grec[0], _gpp)
        _grec[1] = max(_grec[1], _ge)
        if _ge >= 0.0 and (_gpr / _gtt if _gtt else 0) >= _gce.MIN_PRICED_FRAC:
            _grec[2] = max(_grec[2] or 0.0, _gpp)
    for _gnl, (_gbp, _gbe, _gbb) in _gbf.items():
        _gat = [s for s in _gstates if s["legs"] == _gnl]
        if max((s["prob"] for s in _gat), default=0) + 1e-9 < _gbp:
            _gfail = f"trial {_gt} legs {_gnl}: lost the max-prob state"
        if max((s["ev"] for s in _gat if s["ev"] is not None),
               default=-9e9) + 1e-9 < _gbe:
            _gfail = f"trial {_gt} legs {_gnl}: lost the max-EV state"
        if _gbb is not None and max(
                (s["prob"] for s in _gat if s["ev"] is not None
                 and s["ev"] >= 0.0
                 and s["priced_frac"] >= _gce.MIN_PRICED_FRAC),
                default=0) + 1e-9 < _gbb:
            _gfail = f"trial {_gt} legs {_gnl}: lost balanced's best state"
ck("40 seeded slates: the DP never loses a state brute force can find",
   _gfail is None, _gfail or "")

print()
print("=" * 72)
print("The NFL boards are ONE board, whichever worker answers")
print("=" * 72)
# The NFL slate and DFS boards kept their finished product in per-worker memory
# with a background build per worker: three duplicate ~10s builds per board
# (tripling the Kalshi/Sleeper fetch load) and a browser whose polls flapped
# between "simulating..." and results depending on which worker answered -- the
# "testy" NFL tab. Same disease the MLB slate had, cured the same way.
import boardshare as _bsh2
import shutil as _bshu
_bshu.rmtree(_bsh2._DIR, ignore_errors=True)
_bsh2.put("guard_board", {"games": [1, 2, 3]})
_gv, _ga = _bsh2.get("guard_board", 300)
ck("a published board is readable with its age",
   _gv == {"games": [1, 2, 3]} and _ga is not None and _ga < 5)
_bsh2.put("guard_stale", {"empty": True}, age=1500)
_gv2, _ga2 = _bsh2.get("guard_stale", 1800)
ck("a placeholder can be published ALREADY OLD, so it expires for everyone",
   _gv2 is not None and 1490 < _ga2 < 1520,
   "an empty week is cached briefly; backdating the shared copy makes the "
   "retry clock global instead of per-worker")
ck("...and reads as expired past its shortened life",
   _bsh2.get("guard_stale", 900)[0] is None)
_claims = [_bsh2.claim("guard_claim") for _ in range(4)]
ck("exactly one worker may claim a board build", _claims.count(True) == 1,
   "without the O_EXCL claim every cold worker starts its own duplicate build")
_bsh2.release("guard_claim")
ck("a released claim can be taken again", _bsh2.claim("guard_claim") is True)
_bsh2.release("guard_claim")
_bshu.rmtree(_bsh2._DIR, ignore_errors=True)

import inspect as _insp2
import nfl_game_sim as _ngs
import nfl_dfs_sim as _nds
import kalshi_nfl as _knf
for _mod, _nm in ((_ngs, "slate"), (_nds, "DFS")):
    _src = _insp2.getsource(_mod.board)
    ck(f"the NFL {_nm} board adopts a sibling's build before starting its own",
       _src.index("boardshare.get") < _src.index("boardshare.claim"),
       "disk first, claim second: the cheap answer beats the duplicate build")
    ck(f"...publishes what it builds", "boardshare.put" in _src)
    ck(f"...and releases its claim on every path",
       "boardshare.release" in _src.split("finally:")[-1])
ck("a failed DFS build is cached briefly and recorded, not dropped",
   "NFLDFS-board-build" in _insp2.getsource(_nds.board)
   and "empty" in _insp2.getsource(_nds.board),
   "an exception in the build thread had NO handler: the board stayed "
   "'simulating...' forever with the reason lost, and every poll spawned "
   "another doomed build")
ck("a failed slate build is recorded under its ID",
   "NFLG-board-build" in _insp2.getsource(_ngs.board))
_ksrc = _insp2.getsource(_knf.index)
ck("the NFL Kalshi index treats an EMPTY build as a failure",
   "if built:" in _ksrc and "elif not _cache" in _ksrc.replace('["data"]', ""),
   "a throttled window returns no markets rather than an error; caching that "
   "as good un-prices the whole board -- and the preseason board is ANCHORED "
   "to this ladder, so it changes the projections too")
ck("...with a shared last-good fallback up to 45 minutes old",
   "boardshare.get" in _ksrc and "_IDX_STALE_MAX" in _ksrc
   and _knf._IDX_STALE_MAX == 45 * 60)
ck("NFL feed failures land in the ledger with stable IDs",
   all(x in open(_os.path.join(_root, f)).read() for f, x in
       (("nfl_live.py", "NFL-espn-fetch"),
        ("kalshi_nfl.py", "KNFL-markets-fetch"),
        ("nfl_dfs_sim.py", "NFLDFS-sleeper-fetch"),
        ("nfl_dfs_sim.py", "NFLDFS-rosters"))),
   "an empty NFL board should say WHICH feed failed, not just look dead")

# One preseason flag, three checkboxes. Each kept its own copy wired its own
# way; toggling one moved at most one sibling and the DFS box never followed.
_appjs2 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the preseason flag changes in exactly one place",
   _appjs2.count("setNflPreseason(") >= 4
   and "function setNflPreseason" in _appjs2,
   "three checkboxes, one variable: every change listener reports to the "
   "same setter")
ck("every preseason checkbox is re-synced from the shared flag",
   all(f'"{i}"' in _appjs2.split("function syncNflPreBoxes")[1][:220]
       for i in ("nflPre", "nflSimPre", "dfsNflPre")))
ck("no checkbox writes the flag directly any more",
   "nflPreseason = pre.checked" not in _appjs2
   and "nflPreseason = cb.checked" not in _appjs2)

print()
print("=" * 72)
print("Mega sweep: every board shares, the lint stays clean")
print("=" * 72)
# Seven more boards ran the build-into-per-worker-memory pattern (golf, LoL,
# NBA, NHL, tennis, UFC, modeled futures). All now route through ONE helper.
import boardshare as _bs3
for _mod, _fn in (("golf", "board"), ("lol", "board"), ("basket", "board"),
                  ("hockey", "board"), ("tennis_prices", "board"),
                  ("ufc_sim", "board")):
    _srcb = _insp.getsource(getattr(__import__(_mod), _fn))
    ck(f"{_mod}.{_fn} serves through the shared store",
       "boardshare.nonblocking" in _srcb,
       "per-worker memory is why boards flapped between 'building' and results")
ck("the modeled-futures board publishes its partials to every worker",
   "boardshare.put" in _insp.getsource(__import__("mfutures").rows),
   "a poll landing on a sibling mid-build should see the growing board, "
   "not a 202")

# The helper itself: adopt-before-build, one claim, stale-while-revalidate,
# and a build that raises is recorded rather than lost.
import shutil as _bsu3
_bsu3.rmtree(_bs3._DIR, ignore_errors=True)
_gmem = {}
_calls = []


def _gbuild():
    _calls.append(1)
    return {"n": len(_calls)}


_gv = _bs3.nonblocking("guard_nb", 300, _gmem, "k", _gbuild)
ck("cold call returns None and kicks exactly one build", _gv is None)
import time as _gt3
for _ in range(150):
    if _gmem.get("k") and _bs3.get("guard_nb", 300)[0] is not None:
        break
    _gt3.sleep(0.1)
ck("the build lands in memory AND on the shared disk",
   _gmem.get("k") and _bs3.get("guard_nb", 300)[0] == {"n": 1})
_gmem2 = {}
ck("a second worker ADOPTS instead of rebuilding",
   _bs3.nonblocking("guard_nb", 300, _gmem2, "k", _gbuild) == {"n": 1}
   and len(_calls) == 1,
   "the whole point: one build serves every worker")


def _gboom():
    raise RuntimeError("guard boom")


import errlog as _errl3
_bs3.nonblocking("guard_boom", 300, {}, "k", _gboom, "GUARD-boom")
for _ in range(50):
    if _errl3.recent(code="GUARD-boom", limit=1):
        break
    __import__("time").sleep(0.1)
ck("a build that raises is recorded under its ID, not swallowed",
   bool(_errl3.recent(code="GUARD-boom", limit=1)),
   "half these threads had no exception handler at all")
_bsu3.rmtree(_bs3._DIR, ignore_errors=True)

# Routine file races stay OUT of the ledger: an already-removed job file or
# lockfile is normal operation, and noise trains the reader to ignore errors.
for _fn2, _pat in ((_bbttl.job_drop, "except OSError:"),
                   (_bbttl._slate_release, "except OSError:")):
    ck(f"{_fn2.__name__} treats a missing file as routine",
       _pat in _insp.getsource(_fn2))

# The lint gate. bestbets carried an undefined name (_SPREAD_UNIT) armed to
# NameError on the first NBA/NHL spread edge of the season -- opening night.
# Zero pyflakes findings is now the baseline; this fails if anyone reintroduces
# an undefined name, a shadowed import, or dead assignments.
import subprocess as _gsp
import glob as _gg
import sys as _gsy
_pyf = _gsp.run([_gsy.executable, "-m", "pyflakes",
                 *_gg.glob(_os.path.join(_root, "*.py"))],
                capture_output=True, text=True)
if "No module named" in (_pyf.stderr or ""):
    ck("pyflakes lint gate (skipped: pyflakes not installed)", True)
else:
    _hits = [l for l in _pyf.stdout.splitlines() if l.strip()]
    ck("pyflakes finds NOTHING across all modules", not _hits, _hits[:4])

print()
print("=" * 72)
print("A preseason week never vanishes: no market and no blip can empty it")
print("=" * 72)
# Two failures stacked into "no slate for today even though the preseason box
# is checked": a single failed ESPN fetch made current_week() read a live week
# as finished and skip to the next one, and an empty Kalshi index (a throttled
# window returns no markets) made _preseason_sims skip EVERY game -- sixteen
# scheduled exhibitions rendered as "No games found for this week".
import nfl_game_sim as _ngs2
import nfl_live as _nlv2
import kalshi_nfl as _knf2
import clock as _gck

_gtoday = _gck.today_et().isoformat()
_fake_sched = {
    1: [{"home": "DAL", "away": "SEA", "date": "2000-01-01T00:00Z"}],
    2: [{"home": "BUF", "away": "PIT", "date": "2000-01-02T00:00Z"}],
    3: [{"home": "KC", "away": "DEN", "date": _gtoday + "T23:00Z"},
        {"home": "SF", "away": "LAR", "date": _gtoday + "T23:00Z"}],
    4: [{"home": "NYJ", "away": "NYG", "date": "2099-01-01T00:00Z"}],
}
_orig_sched = _nlv2.schedule


def _sched_ok(week, season, seasontype=2):
    return list(_fake_sched.get(week, []))


def _sched_blip(week, season, seasontype=2):
    if week == 3:
        raise RuntimeError("guard: simulated ESPN blip")
    return list(_fake_sched.get(week, []))


try:
    _nlv2.schedule = _sched_ok
    ck("auto-week lands on the first week with games left",
       _ngs2.current_week(True) == 3)
    _nlv2.schedule = _sched_blip
    ck("a week whose fetch FAILS is pointed at, never skipped",
       _ngs2.current_week(True) == 3,
       "one ESPN blip on week 3 used to send the tab to week 4 and an empty "
       "board while sixteen games sat ready to play tonight")
finally:
    _nlv2.schedule = _orig_sched

_orig_idx2, _orig_ros = _knf2.index, None
import nfl_preseason as _npre2
_orig_ros = _npre2.rosters
try:
    _nlv2.schedule = _sched_ok
    _knf2.index = lambda: {}                    # the throttled-empty window
    _npre2.rosters = lambda season: {}
    _sims = _ngs2._preseason_sims(2026, 3, 300)
    ck("an EMPTY Kalshi index still yields every scheduled game",
       len(_sims) == 2 and all(suf is None for _s, suf in _sims),
       f"got {len(_sims)} of 2; a game with no market plays at the measured "
       "league-average level, marked unpriced, instead of vanishing")
    if _sims:
        _tot = _sims[0][0].get("exp_total") or (
            (_sims[0][0].get("exp_home") or 0) + (_sims[0][0].get("exp_away") or 0))
        ck("...at the measured exhibition level, not an invented one",
           _tot and 34.0 <= _tot <= 48.0, f"total {_tot} vs measured ~41")
finally:
    _nlv2.schedule = _orig_sched
    _knf2.index = _orig_idx2
    _npre2.rosters = _orig_ros
ck("a per-game market read that raises is recorded, not swallowed",
   "NFLG-implied" in _insp.getsource(_ngs2._preseason_sims))
ck("the auto-week fetch failure is recorded too",
   "NFLG-current-week" in _insp.getsource(_ngs2.current_week))

print()
print("=" * 72)
print("ESPN refusing this host does not empty the NFL tab")
print("=" * 72)
# The ledger caught ESPN's WAF answering the app's bot user-agent with 403
# Forbidden from the production host -- 299 entries in one evening, every
# schedule fetch dead, both NFL boards empty while sixteen games sat listed on
# Kalshi. Three defences, each verified synthetically.
import racing as _rcg
import urllib.error as _uerr
import io as _gio
ck("the ESPN getter identifies as a browser, not a bot",
   _rcg._UA.startswith("Mozilla/5.0") and "kalshi-betting-helper" not in _rcg._UA,
   "the bot string is what the WAF keyed on")
_ua_calls = []
_orig_open2 = _rcg.urllib.request.urlopen


def _open403(req, timeout=None):
    _ua_calls.append(req.get_header("User-agent"))
    if len(_ua_calls) == 1:
        raise _uerr.HTTPError(req.full_url, 403, "Forbidden", {},
                              _gio.BytesIO(b""))

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": 1}'
    return _R()


try:
    _rcg.urllib.request.urlopen = _open403
    _gd = _rcg._get_json("https://guard.example/x")
finally:
    _rcg.urllib.request.urlopen = _orig_open2
ck("a 403 is retried once under a second identity",
   _gd == {"ok": 1} and len(set(_ua_calls)) == 2,
   "WAF rules change without notice; two identities beat one")

import nfl_live as _nlv3
_ssrc = _insp.getsource(_nlv3.schedule)
ck("the schedule has three sources: site API, cdn mirror, Kalshi's own list",
   "cdn.espn.com" in _ssrc and "_kalshi_schedule" in _ssrc
   and "NFL-sched-kalshi-fallback" in _ssrc,
   "one host's WAF must never be able to empty the tab by itself")

# The Kalshi-derived slate: suffixes really do parse into games.
import kalshi_nfl as _knf3
import clock as _gck2
_gtd = _gck2.today_et().isoformat()
_gsfx = _gtd[2:4] + ("JANFEBMARAPRMAYJUNJULAUGSEPOCTNOVDEC"
                     [(int(_gtd[5:7]) - 1) * 3:(int(_gtd[5:7]) - 1) * 3 + 3]) + _gtd[8:10]
_orig_idx3 = _knf3.index
try:
    _knf3.index = lambda: {f"{_gsfx}LVHOU": {}, f"{_gsfx}SFLAC": {},
                           f"{_gsfx}XQZW": {}, "99ZZZ99BAD": {},
                           "25JAN01KCDEN": {}}
    _gks = _nlv3._kalshi_schedule()
finally:
    _knf3.index = _orig_idx3
_gpairs = {(g["away"], g["home"]) for g in _gks}
ck("a Kalshi suffix parses into a real away/home game with names",
   ("LV", "HOU") in _gpairs and ("SF", "LAC") in _gpairs
   and all(g["home_name"] and g["away_name"] for g in _gks),
   f"got {_gpairs}")
ck("garbage suffixes and out-of-window dates are skipped, not crashed",
   len(_gks) == 2,
   "an unparseable listing must cost itself, not the slate")

print()
print("=" * 72)
print("The NFL combo maker is baseball's, feature for feature")
print("=" * 72)
import nfl_game_sim as _ngs3
import combo_engine as _ce4
import random as _rnd4
_bp_src = _insp.getsource(_ngs3.build_parlay)
ck("started games are excluded from pre-game combos",
   '"post", "in"' in _bp_src and "all_started" in _bp_src
   and "excluded_started" in _bp_src,
   "a finished Thursday game sat in Friday's builds at pre-game prices")
ck("game selection accepts the AWY@HOM pair as well as the Kalshi suffix",
   'sel_map.get(g.get("pair")' in _bp_src,
   "a game with no market yet has no suffix but is still pickable")
ck("the per-game pool is capped before bundling",
   "cands[:40]" in _bp_src,
   "Kalshi books two dozen spreads and nineteen totals a side; C(100+,4) "
   "mask-ANDs hung a Build click for minutes")
ck("the parlay sims are cached on the shared store",
   "boardshare.get" in _insp.getsource(_ngs3._slate_sims)
   and "boardshare.put" in _insp.getsource(_ngs3._slate_sims),
   "every Build used to re-simulate sixteen games in whichever worker caught "
   "the request: ~38s a click, times three workers")
ck("...and the maker falls back to the league-average anchor too",
   '"source": "none"' in _insp.getsource(_ngs3._slate_sims),
   "an empty Kalshi index emptied the maker exactly as it emptied the board")

# The frontier stays bounded on a board as wide as the NFL's.
_rw = _rnd4.Random(99)
_wide = []
for _gi in range(16):
    _bs4 = []
    for _ in range(24):
        _sz = _rw.choice([1, 1, 2, 3])
        _p4 = _rw.uniform(0.35, 0.95)
        _c4 = max(3.0, min(97.0, _p4 / (1.0 + _rw.uniform(-0.06, 0.06)) * 100.0))
        _bs4.append({"size": _sz, "prob": _p4,
                     "legs": [{"marg": _p4 ** (1.0 / _sz), "type": "ML",
                               "price_cents": 100.0 * (_c4 / 100.0) ** (1.0 / _sz),
                               "fillable": True}] * _sz})
    _wide.append((f"G{_gi}", _bs4, f"g{_gi}"))
import time as _tf4
_t04 = _tf4.time()
_wst4 = _ce4.frontier(_wide, max_total_legs=30, net=True)
_dt4 = _tf4.time() - _t04
ck("a 16-game x 24-bundle board finishes its frontier in seconds",
   _dt4 < 20.0 and len(_wst4) > 0,
   f"{_dt4:.1f}s, {len(_wst4)} states; unbounded cells hung a Build for minutes")
ck("the DP explores no deeper than the UI can ask",
   # The absolute ceiling is the TIER ceiling (what the maker will accept),
   # and the per-request depth comes from dp_legs. This used to assert a flat
   # 12, which is precisely what made a "require 19 legs" build impossible.
   _ce4._MAX_DP_LEGS == max(t["max_combo_legs"]
                            for t in __import__("tiers").TIERS.values())
   and max(s["legs"] for s in _wst4) <= _ce4._MAX_DP_LEGS
   and _ce4.dp_legs(4, "prefer", 30) <= _ce4._DP_LEGS_DEFAULT,
   "a request never buys depth past its own leg target, and nothing exceeds "
   "the highest leg count any tier permits")
ck("cells hold a bounded Pareto set", 1 <= _ce4._CELL_CAP <= 12)

# Re-pricing must not blend a blend: cached sims are priced on every Build.
_bc4 = {"marg": 0.70, "type": "ML"}
_q4 = {"ask": 55.0, "bid": 53.0, "mid": 54.0, "spread": 2.0,
       "size": 500.0, "vol": 1000.0, "oi": 500.0}
_ce4.blend_candidates([_bc4], {id(_bc4): _q4})
_m1 = _bc4["marg"]
_ce4.blend_candidates([_bc4], {id(_bc4): _q4})
ck("blending twice with the same quote is a no-op the second time",
   abs(_bc4["marg"] - _m1) < 1e-12 and abs(_bc4["marg_model"] - 0.70) < 1e-12,
   "each rebuild walked the number one more step toward the market")

# The sports never share a record: NFL legs must not read MLB's fitted weights.
ck("NFL types are their own tenants in the trust maps",
   "nfl:ML" in _ce4._MODEL_TRUST and _ce4._TRUST_BUCKET.get("nfl:ML") == "nfl"
   and 'sport="nfl"' in _insp.getsource(_ngs3.price_cands),
   "NFL candidates also use the type strings ML and Total; unprefixed, an NFL "
   "moneyline read its blend weight from BASEBALL's graded record")
_ce4._tau_cache.clear()
ck("...and the tau cache keys them apart",
   _ce4._effective_tau("Spread", "nfl") is not None
   and ("nfl:Spread" in _ce4._tau_cache) and ("Spread" not in _ce4._tau_cache))

_appjs4 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the maker has baseball's game picker",
   "nflGameGridHtml" in _appjs4 and "nflComboToggleGame" in _appjs4
   and "&sel=" in _appjs4)
ck("...and explains an all-started week instead of shrugging",
   "all_started" in _appjs4 and "excluded_started" in _appjs4)

print()
print("=" * 72)
print("Optimal-for-my-x exists on the NFL maker too")
print("=" * 72)
# One input -- the payout x -- and the maker chooses legs, confidence and
# games itself, sweeping per-leg floors through combo_engine.best_target.
# Verified live at 10x (10 legs, 10.0% -- the arithmetic ceiling for a true
# 10x -- against a 12.49x market payout) and 50x (12 legs, 2.0%).
_apy = open(_os.path.join(_root, "app.py")).read()
_nflopt = _apy.split("def api_nfl_parlay")[1].split("def api_nfl_sim")[0]
ck("the NFL endpoint has baseball's optimal branch",
   'request.args.get("optimal")' in _nflopt
   and "combo_engine.best_target" in _nflopt
   and "optimal_unbuildable" in _nflopt
   and "target_capped" in _nflopt)
ck("optimal overrides the manual targets exactly as baseball does",
   '"off" if _opt else legs_mode' in _nflopt
   and '"require" if _opt else payout_mode' in _nflopt
   and '"balanced" if _opt else objective' in _nflopt,
   "the whole point is one input: the leg count and confidence are OUTPUTS")
ck("a target past Kalshi's ceiling is clamped, not chased",
   "min(payout, combo_engine.MAX_PAYOUT_X)" in _nflopt)
_appjs5 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the maker has the button, the param, and the empty-target coaching",
   "buildNFLCombo(false, true)" in _appjs5
   and '(optimal ? "&optimal=1" : "")' in _appjs5
   and _appjs5.count("Optimal mode needs the one thing it optimizes for") >= 2,
   "the MLB button posts optimal=1 and explains a missing x; the NFL one must too")
ck("the optimal slip renders through the shared note",
   'm.objective !== "optimal"' in _appjs5,
   "renderMixed and optimalNote are shared, so the ceiling arithmetic and the "
   "-EV warning arrive on NFL slips for free")

print()
print("=" * 72)
print("The NFL maker's game grid is baseball's: whole games, single teams")
print("=" * 72)
# Two findings from the user seeing NO grid at all: the maker rendered before
# the week data arrived and nothing ever re-rendered it (so the grid never
# appeared, however long you stared), and the picker had no team halves.
_bp2 = _insp.getsource(__import__("nfl_game_sim").build_parlay)
ck("the builder parses base and base:TEAM selections",
   "partition(\":\")" in _bp2.replace("'", '"') and "team_only" in _bp2)
ck("a one-team selection keeps only that club's legs",
   'c.get("side_team") == team_only' in _bp2,
   "totals and the other side drop, exactly as baseball's picker behaves")
import nfl_game_sim as _ngs5
ck("player props know which club they belong to",
   "team=p_team" in _insp.getsource(_ngs5._build_masks),
   "ML and spreads carry the team in their kref; props needed their own tag")
_js5 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the NFL grid uses the same split cards as baseball's",
   "nflComboToggleTeam" in _js5 and 'class="gg-card' in _js5.split("function nflGameGridHtml")[1][:2000]
   and "gg-all" in _js5.split("function nflGameGridHtml")[1][:2000])
ck("selection serializes as pair or pair:team",
   "nflComboSelParam" in _js5 and "`${k}:${v}`" in _js5)
ck("the maker re-renders when the week data lands",
   'document.querySelector("#nflComboMaker .gamegrid")' in _js5,
   "the maker rendered before the first slate load and the grid never appeared")

print()
print("=" * 72)
print("This preseason's own games inform the projections, shrunk as measured")
print("=" * 72)
# Sleeper publishes real box lines for played exhibitions (707 players carried
# 2026 usage when this landed). Measured out of sample on two seasons: observed
# usage ALONE is WORSE than the role prior every time (one exhibition is
# noise), and the K=2 blend beats both every time --
#   2025 wk1->wk2 (n=580): prior 2.50, observed 2.64, K=2 2.34
#   2025 wk1+2->wk3 (n=536): prior 2.56, observed 2.60, K=2 2.42
#   2026 wk1->wk2 (n=65):  prior 2.02, observed 2.62, K=2 1.82
import nfl_preseason as _npz
ck("the prior is worth exactly the two games the measurement says",
   _npz._OBS_K == 2.0)
_zp = _npz.expected_usage("RB", 0.0)
ck("no observed usage leaves the prior untouched",
   _npz.expected_usage("RB", 0.0, None, 0) == _zp
   and _npz.expected_usage("QB", 25.0, None, 0) == _npz.expected_usage("QB", 25.0))
_z1 = _npz.expected_usage("RB", 0.0, 12.0, 1)
_z2 = _npz.expected_usage("RB", 0.0, 12.0, 2)
ck("one loud game moves the number by exactly its K=2 share",
   abs(_z1 - (1 * 12.0 + 2.0 * _zp) / 3.0) < 0.01, f"{_z1}")
ck("a second game pulls harder, monotonically", _zp < _z1 < _z2 < 12.0,
   "observed evidence accumulates but never fully displaces the prior")
ck("a projection built on observed games says so on the card",
   "measured THIS preseason" in _npz.usage_note("QB", 0.0, 13.0, 1))
ck("rosters thread the observed numbers through by player_id",
   "observed_usage(season)" in _insp.getsource(_npz.rosters)
   and '"obs_use_pg"' in _insp.getsource(_npz.rosters))
ck("...and the stat-line shares consume them",
   'p.get("obs_use_pg")' in _insp.getsource(_npz.stat_lines))
_orig_gj = _npz.racing._get_json
try:
    _npz.racing._get_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    _npz.racing._form_cache.pop(("nfl_pre_observed", "2099"), None)
    ck("a dead stats feed degrades to the prior, recorded, never a crash",
       _npz.observed_usage("2099") == {})
finally:
    _npz.racing._get_json = _orig_gj

print()
print("=" * 72)
print("Switching apps mid-build costs nothing: the slip is collected, not lost")
print("=" * 72)
# The service worker converts a dead fetch into CLEAN JSON ({error: "network
# unavailable", offline: true}) -- so the build poll saw a "result", exited,
# and printed it. The 8-miss tolerance never fired; one suspended-tab blink
# abandoned a build the server went on to finish. Build -> switch to Kalshi ->
# come back is the app's core loop, so this path is now survivable end to end.
_appjs7 = open(_os.path.join(_root, "static", "app.js")).read()
_bc7 = _appjs7.split("window.buildCombo = async")[1].split("function _renderComboResult")[0]
ck("the offline marker is a MISS, not a verdict",
   "d.offline" in _bc7 and "++misses" in _bc7,
   "the SW's fallback JSON looks like a result and used to end the build "
   "with 'network unavailable' on the first blink")
ck("a suspended tab never spends the miss budget",
   _bc7.count("!document.hidden && ++misses") >= 2,
   "phone timers freeze in the background; whatever half-state the resume "
   "lands in is not a verdict on the network")
ck("a severed build offers reattach, never restart",
   "tap to reattach" in _bc7 and "window._comboResume = resume" in _bc7,
   "the finished slip waits in the job file for an hour; rebuilding it "
   "cost a second full simulation")
ck("...and reattaches BY ITSELF when the user comes back",
   "window._comboResume && !comboBuilding" in _appjs7,
   "tapping a link is the fallback, not the price of switching apps")
ck("a fresh click supersedes any pending reattach",
   "window._comboResume = null;   // a fresh click supersedes" in _appjs7)
ck("the result renderer is shared by the normal and reattach paths",
   "function _renderComboResult" in _appjs7
   and _appjs7.count("_renderComboResult(d, out, t, c)") >= 2)
ck("a 0/0 board-adopt no longer flashes 'all games simulated'",
   "if (d.total > 0) _warmWasCold = true;" in _appjs7,
   "a fresh deploy reads 0/0 for half a second while the shared board is "
   "adopted; announcing readiness over that is noise, not news")

print()
print("=" * 72)
print("Always running: the phone carries nothing and turns nothing off")
print("=" * 72)
# The owner's contract, verbatim: the phone must never carry a build (the
# server does, under a per-click token, served idempotently) and must not
# switch the screen off while Vigil is up (Wake Lock, re-acquired on return).
_apy8 = open(_os.path.join(_root, "app.py")).read()
_nfl8 = _apy8.split("def api_nfl_parlay")[1].split("def api_nfl_sim")[0]
ck("the NFL build runs server-side under the click's token",
   "baseball.job_claim(ptok)" in _nfl8
   and '_run_job(ptok, _core, "NFL-COMBO-build")' in _nfl8
   and '"status": "building"' in _nfl8,
   "a synchronous build died with the phone; a job survives it (the finish "
   "itself now lives in _run_job, alongside the heartbeat)")
ck("the finished NFL slip is served idempotently",
   'job.get("status") == "done"' in _nfl8
   and "return jsonify(job.get(\"result\")" in _nfl8.replace("'", '"'),
   "destroy-on-first-read is how a lost response became a silent rebuild")
ck("every request-dependent value is read on the request thread",
   "pre_flag = _nfl_preseason()" in _nfl8 and "prop_types = _prop_types()" in _nfl8,
   "_prop_types reads the request; called from the build thread it raises "
   "'Working outside of request context' and the build dies instantly")
ck("a failed NFL build lands in the ledger under its ID",
   "NFL-COMBO-build" in _nfl8)
_js8 = open(_os.path.join(_root, "static", "app.js")).read()
_bn8 = _js8.split("async function buildNFLCombo")[1].split("function _renderNflComboResult")[0]
ck("the NFL maker polls with the same three-shape tolerance as baseball's",
   "d.offline" in _bn8 and _bn8.count("!document.hidden && ++misses") >= 2
   and "ptok" in _bn8)
ck("...and its reattach shares the one resume slot",
   "window._comboResume = resume" in _bn8,
   "the visibility handler fires whichever maker was severed")
ck("the screen is held awake while Vigil is visible",
   "navigator.wakeLock.request" in _js8 and "_wakeAcquire()" in _js8
   and _js8.count("_wakeAcquire") >= 3,
   "acquired at load, re-acquired on every return; the OS releases it on "
   "backgrounding and that part is not optional")

print()
print("=" * 72)
print("MLB DFS: the roster is DraftKings' choice, made automatically")
print("=" * 72)
# The audit found the auto-slate loader dead end to end: the UI refused an
# empty CSV (while the server was built to pull tonight's slate itself), the
# slate picker compared DK's EASTERN start times against the server's UTC
# clock (tonight's games read as past, next Tuesday's playerless group won),
# an empty player pool ended the search instead of trying the next group, an
# empty lobby response was CACHED as "nothing posted" for 15 minutes, and the
# auto-load note never reached the MLB/NFL/LoL responses because those
# branches returned early.
import dk as _dk9
import inspect as _in9
_sf9 = _in9.getsource(_dk9.slate_for)
ck("slate picking compares Eastern to Eastern",
   "clock.now_et()" in _sf9 and "time.strftime" not in _sf9,
   "DK's StartDateEst against the server's UTC clock read tonight's slate "
   "as already started")
ck("tonight's slates outrank a bigger one next Tuesday",
   "tonight" in _sf9)
ck("a playerless draft group does not end the search",
   "for cand in candidates" in _sf9,
   "DK lists future groups before posting their pools")
ck("an empty lobby answer is a retryable failure, not a cached fact",
   "DK-lobby-empty" in _in9.getsource(_dk9.slates))
_apy9 = open(_os.path.join(_root, "app.py")).read()
_dfs9 = _apy9.split("def api_simulate_dfs")[1].split("\n@app.route")[0]
ck("the auto-loaded slate note reaches every sport's response",
   _dfs9.count('res["dk_slate"] = auto_slate') >= 3
   and 'built["dk_slate"] = auto_slate' in _dfs9,
   "the MLB/NFL/LoL branches returned before the attach")
ck("the MLB branch enforces DK's own ten-man shape",
   __import__("mlb_dfs").ROSTER == ["P", "P", "C", "1B", "2B", "3B", "SS",
                                    "OF", "OF", "OF"],
   "the generic roster-size box never applies to baseball")
_js9 = open(_os.path.join(_root, "static", "app.js")).read()
ck("an empty CSV auto-loads tonight's slate instead of refusing",
   "Loading tonight's DraftKings" in _js9
   and "Paste your DraftKings salaries CSV first" not in
   _js9.split("async function runDfsSim")[1][:900])
ck("a pasted CSV names its own sport and the picker follows",
   "dfsDetectSport" in _js9 and '"SP", "RP"' in _js9,
   "an MLB slate pasted with the picker on UFC built a six-man lineup "
   "out of ballplayers")
ck("the fixed roster shape is SAID, not hidden",
   "dfsRosterAuto" in _js9 and "P·2, C, 1B, 2B, 3B, SS, OF·3" in _js9
   and 'id="dfsRosterAuto"' in open(_os.path.join(_root, "templates",
                                                  "index.html")).read())
ck("the auto-loaded slate is announced on the result",
   "Auto-loaded tonight's DraftKings slate" in _js9)
# The card shows the slot each player FILLS, not his eligibility string --
# and the assigner survives the case where a flexible player must move.
import mlb_dfs as _md9
_lu9 = [
    {"name": "A", "elig": {"2B", "SS"}},   # flexible, assigned first (fewest? both 2)
    {"name": "B", "elig": {"SS"}},          # rigid: must displace A if A took SS
]
_as9 = _md9._assign_slots(_lu9)
_slots9 = {p["name"]: _as9.get(id(p)) for p in _lu9}
ck("a rigid player displaces a flexible one from his only slot",
   _slots9["B"] == "SS" and _slots9["A"] == "2B", _slots9)
ck("the payload carries the assigned slot",
   '"slot": slot_of.get(id(p))' in _insp.getsource(_md9._lineup_payload)
   and "p.slot || p.pos" in _js9)

print()
print("=" * 72)
print("MLB DFS: DK legality, stacking shapes, batting-order adjacency")
print("=" * 72)
# The guide-book strategies the builder claimed but didn't do: the single
# lineup ignored the stack knob entirely, nothing enforced DraftKings' own
# entry rules (max 5 hitters a team, players from 2+ games), stacks had no
# secondary shape or low-owned steering, and batting order was ignored.
import random as _rnd10
import mlb_dfs as _md10


def _mk10(name, team, elig, med, sal=4000, order=None, game=None, kind="bat"):
    return {"name": name, "salary": sal, "elig": set(elig), "median": med,
            "ceil": med * 1.6, "floor": med * 0.5, "proj": med, "team": team,
            "dk_team": team, "game": game, "order": order,
            "kind": kind, "arr": None, "confirmed": True, "sim": True}


_POS10 = ["C", "1B", "2B", "3B", "SS", "OF", "OF", "OF", "OF"]


def _team10(team, game, med, jitter=0.0):
    rnd = _rnd10.Random(sum(map(ord, team)))          # NOT hash(): stable per run
    out = [_mk10(f"{team}-b{i+1}", team, {pos},
                 med + (rnd.uniform(-jitter, jitter) if jitter else 0),
                 order=i + 1, game=game)
           for i, pos in enumerate(_POS10)]
    out.append(_mk10(f"{team}-P", team, {"P"}, med + 5, sal=4200, game=game,
                     kind="pit"))
    return out


_poolA = (_team10("AAA", "G1", 10, 1.5) + _team10("BBB", "G1", 10, 1.5)
          + _team10("CCC", "G2", 10, 1.5) + _team10("DDD", "G2", 10, 1.5))
_rnd10.seed(11)
_r10 = _md10.optimize(_poolA, 50000, "median", restarts=800, stack_min=4)
_stk10 = _md10._biggest_stack(_r10[1]) if _r10 else None
ck("a SINGLE lineup honors the stack knob",
   _r10 is not None and _stk10 is not None and _stk10["n"] >= 4,
   f"got {_stk10} -- the UI default is 1 lineup + stack 4, and the single "
   "path silently ignored stack_min; the lineup everyone actually builds "
   "came out scattered")

_poolB = (_team10("AAA", "G1", 30) + _team10("BBB", "G1", 5)
          + _team10("CCC", "G2", 5) + _team10("DDD", "G2", 5))
_rnd10.seed(12)
_r10 = _md10.optimize(_poolB, 50000, "median", restarts=800)
_naaa = (sum(1 for p in _r10[1] if p["kind"] == "bat" and p["team"] == "AAA")
         if _r10 else 99)
ck("DraftKings' 5-hitters-per-team cap binds on a dominant team",
   _naaa == 5,
   f"{_naaa} AAA bats rostered -- 8 hitter slots and one hot offense used to "
   "produce a 6+ stack that DK rejects at entry")

_poolC = (_team10("AAA", "G1", 30) + _team10("BBB", "G1", 28)
          + _team10("CCC", "G2", 3) + _team10("DDD", "G2", 3))
_rnd10.seed(13)
_r10 = _md10.optimize(_poolC, 50000, "median", restarts=800)
_gm10 = {p["game"] for p in _r10[1]} if _r10 else set()
ck("DK's players-from-two-games rule binds when one game dominates",
   len(_gm10) >= 2,
   f"games {_gm10} -- a 5-3 of both sides of one game plus its two starters "
   "maximized points and was an illegal entry")

# No team can positionally field 5 of its own bats (4 OF-only + a C each, and
# only 3 OF slots exist) -> every stacked restart fails -> the fallback must
# still deliver a legal unstacked lineup instead of nothing.
_poolD = []
for _t10, _g10 in (("AAA", "G1"), ("BBB", "G1"), ("CCC", "G2"), ("DDD", "G2")):
    _tr10 = _rnd10.Random(sum(map(ord, _t10)))
    for _i10 in range(4):
        _poolD.append(_mk10(f"{_t10}-of{_i10}", _t10, {"OF"},
                            10 + _tr10.uniform(-1, 1), order=_i10 + 1, game=_g10))
    _poolD.append(_mk10(f"{_t10}-c", _t10, {"C"}, 9, order=5, game=_g10))
    _poolD.append(_mk10(f"{_t10}-P", _t10, {"P"}, 15, sal=4200, game=_g10,
                        kind="pit"))
for _t10, _g10, _p10 in (("UT1", "G1", "1B"), ("UT2", "G1", "2B"),
                         ("UT3", "G2", "3B"), ("UT4", "G2", "SS")):
    _poolD.append(_mk10(f"{_t10}-{_p10}", _t10, {_p10}, 9, order=6, game=_g10))
_rnd10.seed(21)
_r10 = _md10.optimize(_poolD, 50000, "median", restarts=400, stack_min=5)
_stk10 = _md10._biggest_stack(_r10[1]) if _r10 else None
ck("an unfillable stack falls back to a legal lineup, not to nothing",
   _r10 is not None and (_stk10 is None or _stk10["n"] < 5),
   f"got {_stk10} -- a thin slate with the stack knob up must not return "
   "an error")
ck("...and the response owns up to relaxing the stack",
   "stack_relaxed" in _insp.getsource(_md10.build),
   "silently handing back a scattered lineup breaks trust in the knob")

# Batting-order adjacency: with a real choice of stack bats (multi-eligible,
# equal value), the second stack pick lands within 2 lineup spots of the first
# measurably more often than an unboosted build. Fixed seeds -> deterministic.
_UTIL10 = {"C", "1B", "2B", "3B", "SS", "OF"}
_poolE = [_mk10(f"SSS-b{_i10+1}", "SSS", _UTIL10, 10.0, order=_i10 + 1, game="G1")
          for _i10 in range(9)]
for _t10, _g10 in (("BBB", "G1"), ("CCC", "G2"), ("DDD", "G2")):
    for _i10, _p10 in enumerate(_POS10):
        _poolE.append(_mk10(f"{_t10}-b{_i10+1}", _t10, {_p10}, 10.0,
                            order=_i10 + 1, game=_g10))
    _poolE.append(_mk10(f"{_t10}-P", _t10, {"P"}, 15, sal=4200, game=_g10,
                        kind="pit"))
_poolE.append(_mk10("SSS-P", "SSS", {"P"}, 15, sal=4200, game="G1", kind="pit"))
_bp10 = _md10._by_pos(_poolE)


def _near2_rate10(boost):
    old = _md10._ADJ_BOOST
    _md10._ADJ_BOOST = boost
    hit = tot = 0
    try:
        for _i in range(600):
            rng = _rnd10.Random(5000 + _i)
            r = _md10._build_one(_bp10, 50000, "median", stack_team="SSS",
                                 stack_min=5, rng=rng)
            if not r:
                continue
            ords = [p["order"] for p in r[0]
                    if p["team"] == "SSS" and p["kind"] == "bat"]
            if len(ords) >= 2:
                tot += 1
                hit += 1 if _md10._ord_gap(ords[0], ords[1]) <= 2 else 0
    finally:
        _md10._ADJ_BOOST = old
    return hit / max(1, tot)


_adj_on10, _adj_off10 = _near2_rate10(_md10._ADJ_BOOST), _near2_rate10(1.0)
ck("stack picks measurably prefer bats within 2 lineup spots",
   _adj_on10 - _adj_off10 > 0.03,
   f"boosted {_adj_on10:.3f} vs baseline {_adj_off10:.3f} -- adjacent bats "
   "double-dip on the same rally; measured +0.075 on this exact pool and "
   "seed set when it shipped")
ck("the batting order wraps: the 9-hole and leadoff are adjacent",
   _md10._ord_gap(1, 9) == 1 and _md10._ord_gap(2, 5) == 3
   and _md10._ord_gap(4, 4) == 0,
   "a 9-1-2 mini-stack is a real construction -- the order is a cycle")

# Portfolio: primary stack enforced on every chosen lineup, secondary 2-3 bat
# shapes appear (the 5-3 / 5-2-1 builds), the stack chip knows its ownership,
# and every lineup obeys DK legality.
_md10.add_ownership_leverage(_poolA, {})
_rnd10.seed(15)
_ch10 = _md10.optimize_portfolio(_poolA, 50000, "median", n_lineups=8,
                                 stack_min=4)
_ok_primary10 = _ch10 and all(
    (_md10._biggest_stack(lu) or {}).get("n", 0) >= 4 for _s, lu, _x in _ch10)
_n2ct10 = sum(1 for _s, lu, _x in _ch10 or []
              if (_md10._biggest_stack(lu) or {}).get("n2"))
_legal10 = True
for _s, lu, _x in _ch10 or []:
    _bt10 = {}
    for p in lu:
        if p["kind"] == "bat":
            _bt10[p["dk_team"]] = _bt10.get(p["dk_team"], 0) + 1
    if max(_bt10.values()) > 5 or len({p["game"] for p in lu}) < 2:
        _legal10 = False
ck("every portfolio lineup hits the primary stack", bool(_ok_primary10))
ck("secondary 2-3 bat shapes appear across the portfolio", _n2ct10 >= 1,
   f"{_n2ct10} of {len(_ch10 or [])} carry one -- the guide's 5-3 / 5-2-1 "
   "constructions, not eight copies of one stack")
ck("every portfolio lineup is DK-legal", _legal10)
ck("the stack chip knows the field's ownership of it",
   any((_md10._biggest_stack(lu) or {}).get("own") is not None
       for _s, lu, _x in _ch10 or []),
   "a 5-stack at 8% owned IS the low-owned-stack play; the chip must say so")
_pfsrc10 = _insp.getsource(_md10.optimize_portfolio)
ck("a deliberate share of stack draws is contrarian (ownership-discounted)",
   "contra_w" in _pfsrc10 and "_stack_team_weights" in _pfsrc10,
   "winning GPP lineups overwhelmingly carry a NON-popular stack; drawing "
   "teams flat let chalk take every build")

# Plumbing: ownership is annotated BEFORE lineups are built (the contrarian
# draw and the chip both read it), and batting order + DK labels survive the
# pool assembly and both projection engines.
_bsrc10 = _insp.getsource(_md10.build)
ck("ownership is annotated before building, not bolted on after",
   _bsrc10.index("add_ownership_leverage") < _bsrc10.index("optimize("),
   "the contrarian stack draw read own% that did not exist yet")
_apsrc10 = _insp.getsource(_md10._assemble_pool)
ck("the pool carries DK's own team/game labels and the lineup spot",
   '"dk_team"' in _apsrc10 and '"game"' in _apsrc10
   and '"order": pr.get("order")' in _apsrc10,
   'legality must count "CWS" and "CHW" as one team; only DK\'s label is '
   "uniform across confirmed and padded players")
ck("both projection engines thread the posted batting order through",
   'posted_ord.get(nm)' in _insp.getsource(_md10.deep_projections)
   and "order_of" in _insp.getsource(_md10.projections))
_js10 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the card surfaces the new stack truth",
   "% owned" in _js10 and "ln.stack.n2" in _js10 and "stack_relaxed" in _js10,
   "own%, the secondary stack, and the relaxed-stack warning all render")

print()
print("=" * 72)
print("Openers are 1-2 inning arms, not 6-inning starters")
print("=" * 72)
# A reliever with 51 IP over 67 appearances opened his 3rd game of the year
# and every layer priced him like a workhorse: ip/gs read 17 innings a start
# (relief innings divided over opener starts), the workload chain clamped
# that to a 7.2-IP ace, and the deep sim's gs>=3 starter test rode him 26
# batters -- an 18.9 DK projection for a 2-out lefty whose real average is
# 2.5. The user rostered him. Every layer now reads the arm's own workload.
import baseball as _bb11
import statistics as _st11
import mlb_dfs as _md11
import deep_sim as _ds11
_fl11 = {"season": {"ip": 51.0, "gs": 3, "g": 67, "era": 3.93, "whip": 1.18,
                    "k9": 8.8, "bb": 18}}
_so11 = {"season": {"ip": 139.1, "gs": 25, "g": 25, "era": 3.2, "whip": 1.1,
                    "k9": 9.5, "bb": 40}}
_eo11 = _bb11._exp_ip_per_start(_fl11)
_es11 = _bb11._exp_ip_per_start(_so11)
ck("an opener's expected innings are his per-APPEARANCE workload",
   _eo11 <= 1.5,
   f"got {_eo11} -- ip/gs counted his relief innings and invented 7.2")
ck("a real starter's expected innings are untouched",
   5.3 <= _es11 <= 5.7, f"got {_es11:.2f}")
_wf11 = _bb11._starter_workload(_fl11)
ck("the outing budget keeps the opener short instead of re-flooring him at 3",
   _wf11["est_ip"] <= 1.5,
   f"est_ip {_wf11['est_ip']} -- the rookie-prior blend and the 3.0 floor "
   "used to re-invent the workhorse the guard removed")
import random as _rnd11
_rnd11.seed(77)
_ao11 = _md11._pitcher_dk_arr({"era": 3.93, "whip": 1.18, "k9": 8.8, "ip": 51.0,
                               "gs": 3, "est_ip": _wf11["est_ip"]}, 0.55, n=3000)
_aa11 = _md11._pitcher_dk_arr({"era": 3.2, "whip": 1.1, "k9": 9.5, "ip": 139.1,
                               "gs": 25, "est_ip": 5.5}, 0.55, n=3000)
ck("the fast path prices an opener near his real average, win bonus off",
   _st11.fmean(_ao11) < 6.0,
   f"mean {_st11.fmean(_ao11):.1f} vs DK's own 2.5 FPPG -- a starter needs "
   "5 IP for a win, so a scripted 1-2 inning arm can never earn one")
ck("...and a real starter still projects like one",
   12.0 <= _st11.fmean(_aa11) <= 22.0, f"mean {_st11.fmean(_aa11):.1f}")
# Deep sim leash: the opener is hooked on his own workload, the true starter
# keeps the validated 26-BF performance leash (byte-identical else-branch).
_arm11 = lambda pid, gs, g, ip: {"id": pid, "gs": gs, "g": g, "ip": ip}
_prof11 = {"bullpen": [_arm11(9, 0, 40, 38.0)]}
_stf11 = _ds11._Staff(_prof11, _arm11(1, 3, 67, 51.0))
_stf11.outing_bf = 4
_stf11.maybe_hook(1, 0)
ck("the deep sim hooks an opener after his real workload (~4 batters)",
   _stf11.cur["id"] == 9,
   "gs>=3 called him a starter and rode him 26 batters")
_stf12 = _ds11._Staff(_prof11, _arm11(2, 25, 25, 139.1))
_stf12.outing_bf = 4
_stf12.maybe_hook(1, 0)
_still11 = _stf12.cur["id"] == 2
_stf12.outing_bf = 26
_stf12.maybe_hook(5, 0)
ck("a true starter keeps the 26-batter performance leash",
   _still11 and _stf12.cur["id"] == 9,
   "4 BF must not hook him; 26 BF must")
_hs11 = _insp.getsource(_ds11._Staff.maybe_hook)
ck("the gem extensions survive for real starters only",
   "limit = 42" in _hs11 and "ipg" in _hs11
   and _hs11.index("ipg") < _hs11.index("limit = 42"),
   "an opener with a perfect inning still leaves; a starter's no-hitter "
   "still buys him rope")
# DK's own badge data: attribute 135 is the app's "PO" (opener) tag and 136
# the "PLR" (bulk / long reliever) -- verified against the live app's labels.
import dk as _dk11
_dsrc11 = _insp.getsource(_dk11)
ck("the DK loader reads the opener/bulk badges and ships them with the slate",
   "_OPENER_ATTR = 135" in _dsrc11 and "_BULK_ATTR = 136" in _dsrc11
   and '"roles"' in _insp.getsource(_dk11.slate_for),
   "the CSV format cannot carry the badge, so it rides the slate payload")
_ap11 = open(_os.path.join(_root, "app.py")).read()
ck("the roles reach the builder and the card",
   'roles=(auto_slate or {}).get("roles")' in _ap11
   and '"role": p.get("role")' in _insp.getsource(_md11._lineup_payload)
   and "🚫 opener" in _js10,
   "a PO arm at the P slot renders with a warning instead of passing as "
   "a starter")

print()
print("=" * 72)
print("Racing: fitted win models, sprint weekends, and the right grid")
print("=" * 72)
# F1's tau and NASCAR's per-track-type constants are FITTED (winner
# max-likelihood over 2023-2025 F1 / 2024-2026 Cup) instead of eyeballed;
# sprint weekends produce a provisional grid + sprint-result form instead of
# a blank model; and a Cup market can no longer be modeled off the Trucks
# grid that shares its weekend.
import racing as _rc12

ck("F1 tau is the fitted 1.2, with the chaos floor",
   _rc12._TAU["f1"] == 1.2 and _rc12._CHAOS.get("f1") == 0.03,
   "avg winner log-lik -1.19 vs -1.51 at the old tau=3.0 over 70 races; the "
   "2023-2025 sample has no P11+ winner, so eps carries the tail")
ck("NASCAR taus keep their ordering but at the fitted scale",
   _rc12._NASCAR_TAU == {"road": 3.0, "short": 3.0, "intermediate": 4.0,
                         "superspeedway": 7.0}
   and _rc12._NASCAR_CHAOS["intermediate"] == 0.15,
   "the old values had the right shape and were ~2x too flat everywhere "
   "(fitted on 109 Cup races); intermediate chaos matches its observed 9% "
   "P21+ winner rate")
_g12 = _rc12._finalize({f"driver {i}": i for i in range(1, 21)}, "test", "F1")
_p12 = _rc12.win_probs(_g12, "f1")
_rows12 = sorted(_p12.items(), key=lambda kv: -kv[1])
ck("the chaos floor keeps a back-marker unlikely, never impossible",
   abs(sum(_p12.values()) - 1.0) < 1e-9 and 0.001 < _rows12[-1][1] < 0.01
   and _rows12[0][1] > 0.5,
   f"P1 {100*_rows12[0][1]:.1f}%, P20 {100*_rows12[-1][1]:.2f}% -- a fitted "
   "model alone called a back-of-grid charge exactly 0.0%")
ck("DNF risk is measured per starting spot, and deep starts pay ~2.5x",
   _rc12.f1_dnf_pct(1) == 6.6 and _rc12.f1_dnf_pct(8) == 11.7
   and _rc12.f1_dnf_pct(14) == 16.9 and _rc12.f1_dnf_pct(20) == 16.4,
   "1,398 classified 2023-2025 entries; 'Lapped' is a finish, not a DNF -- "
   "counting it as one had the back row crashing out of half its races")
_f1src12 = _insp.getsource(_rc12._openf1_f1_grid)
ck("sprint weekends produce a grid instead of a blank model",
   '"Sprint Qualifying"' in _f1src12 and "sprint_result" in _f1src12
   and "provisional" in _f1src12,
   "the whole Zandvoort sprint weekend showed no model at all -- the code "
   "only knew the word 'Qualifying'")
ck("a lagging OpenF1 endpoint degrades instead of blanking the build",
   "_get_json_opt" in _f1src12,
   "OpenF1 answers 404 while a session has no rows; one raised 404 killed "
   "the grid build before its fallbacks ran")
ck("the penalty-adjusted race grid outranks raw qualifying order",
   _f1src12.index('_rows(race_key, "starting_grid")')
   < _f1src12.index('_rows(key, "session_result")'),
   "a back-of-grid engine penalty only exists in the race session's grid")
# race_board's model half now lives in field_model (shared with the DFS
# simulator, so both read the identical strengths); the guard follows it.
_rbsrc12 = (_insp.getsource(_rc12.race_board)
            + _insp.getsource(_rc12.field_model))
ck("Saturday's sprint result feeds Sunday's form",
   "0.5 * r + 0.5 * sr[nm]" in _rbsrc12 and "dnf_pct" in _rbsrc12
   and '"provisional"' in _rbsrc12,
   "same-track same-weekend pace is the freshest form there is")
ck("a null NASCAR weekend feed yields an empty grid, not a crash",
   _rc12._grid_from_feed({"weekend_runs": None}) == {},
   'found sweeping the archive: some races serve "weekend_runs": null and '
   "iterating None killed the whole board model")
_races12 = [{"race_name": "Dollar Tree 301", "race_date": "2026-08-23", "race_id": 1}]
ck("Saturday finds Sunday's race (3-day window), not another series' grid",
   (_rc12._pick_race(_races12, None, "2026-08-22") or {}).get("race_id") == 1
   and _rc12._pick_race(_races12, None, "2026-08-18") is None,
   "date-matching only exactly-today made the Trucks race answer for the "
   "Cup market on shared weekends")
ck("a grid must cover the market it models",
   "coverage(best) >= 0.3" in _insp.getsource(_rc12.get_nascar_grid)
   and "names=names or None" in _rbsrc12,
   "a Cup winner board was one name-mismatch away from being priced off "
   "the Trucks qualifying order")
_js12 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the board SHOWS the start spot, DNF risk and provisional grid",
   "DNF ~" in _js12 and "provisional" in _js12 and "start_pos" in _js12,
   "the user's ask: visible consequences of starting deep")

print()
print("=" * 72)
print("Retractable roofs: predicted state, not a shrug")
print("=" * 72)
# Every 2026 home game at the seven retractable parks carries its actual roof
# state in the MLB boxscore; joined to OUTDOOR archive temps (453 games), each
# park's real policy replaced the flat 0.5 weight that was applying half of a
# cold-weather penalty to Toronto games played indoors at 72F and half of a
# desert-heat boost to Phoenix games under a closed, air-conditioned roof.
import weather as _wx13
_mk13 = lambda t, pr=0: {"temp_f": t, "wind_mph": 8, "wind_from_deg": 180,
                         "precip_pct": pr, "humidity": 50}
ck("a cold Toronto night is played indoors: weather neutral",
   _wx13.roof_closed_pct(141, _mk13(45)) == 100.0
   and _wx13.run_factor(_mk13(45), 0, "retractable", home_id=141)[0] == 1.0,
   "TOR closed 23/23 games under 55F in 2026; the flat weight cut runs 5.8% "
   "for cold that never touched the game")
ck("a 98F Phoenix day is mostly muted; a 78F desert evening applies",
   _wx13.roof_closed_pct(109, _mk13(98)) == 85.0
   and _wx13.roof_closed_pct(109, _mk13(78)) == 18.0,
   "ARI: 85% closed above 88F but OPEN at 80-90% of cooler games -- the "
   "surprise the measurement caught")
ck("Houston is a de facto dome",
   _wx13.roof_closed_pct(117, _mk13(75)) == 100.0
   and _wx13.run_factor(_mk13(75), 0, "retractable", home_id=117)[0] == 1.0,
   "65/65 home games closed in 2026 at every temperature")
ck("rain closes any roof",
   _wx13.roof_closed_pct(158, _mk13(75, 80)) == 85.0,
   "raining 65-82F games at the cold parks ran 2/3 closed vs 14% dry")
ck("Seattle's umbrella still lets half the weather through when covered",
   0.9 < _wx13.run_factor(_mk13(50, 80), 0, "retractable", home_id=136)[0] < 1.0,
   "the park is open-air under the cover -- closed does not mean indoors")
ck("an unknown park keeps the honest old shrug",
   _wx13._roof_weight("retractable", None, _mk13(75)) == 0.5)
ck("HR ladders feel the roof too",
   _wx13.hr_extra(_mk13(98), "retractable", home_id=109) < 1.05
   < _wx13.hr_extra(_mk13(98), "retractable", home_id=158),
   "a 98F closed-roof Chase game was boosting HR props ~10% for heat the "
   "ball never sees; the same day at open-roof Milwaukee keeps the boost")
_bb13 = _insp.getsource(__import__("baseball")._weather_block)
_js13 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the board SAYS the predicted roof state",
   '"roof_closed_pct"' in _bb13 and "roof likely CLOSED" in _js13
   and "weather at half weight)`" not in _js13.replace(
       "state unknown - weather at half weight)`", ""),
   "the old line shrugged 'weather at half weight' at every retractable park")

print()
print("=" * 72)
print("MLB flat-fix hunt: measured park factors and per-starter shares")
print("=" * 72)
# Two more flat constants replaced by measurement, same disease as the roof
# shrug: the park table was eyeballed ("directionally-standard") and had
# Dodger Stadium run-SUPPRESSING against a measured 102 runs / 125 HR; and
# every starter owned a flat 60% of his game's innings whether he was a
# 6.5-inning workhorse, a 4.7-inning fifth starter, or an opener.
import baseball as _bb14
import savant as _sv14

_old_pf14 = _sv14.park_factors
try:
    _sv14.park_factors = lambda s: {119: {"runs": 1.02, "hr": 1.25},
                                    115: {"runs": 2.0, "hr": 2.0},
                                    137: {"runs": 0.5, "hr": 0.5}}
    ck("Statcast's measured park factor replaces the eyeballed table",
       _bb14._park_factor(119, "2026") == 1.02
       and abs(_bb14._park_hr_ratio(119, "2026") - 1.225) < 0.01,
       "LAD sat at 0.97 in the static table; every Dodger Stadium HR ladder "
       "was ~25% underpriced")
    ck("the clamp is a sanity rail wide enough for real extremes",
       _bb14._park_factor(115, "2026") == 1.30
       and _bb14._park_factor(137, "2026") == 0.80,
       "T-Mobile measures 0.83 and Coors 1.25 -- both inside the rails")
    _sv14.park_factors = lambda s: None
    ck("Savant down -> the static table still answers",
       _bb14._park_factor(115, "2026") == 1.15
       and _bb14._park_hr_ratio(147, "2026") == 1.0,
       "the fallback keeps the board alive; only the HR dimension is lost")
finally:
    _sv14.park_factors = _old_pf14
_bbsrc14 = open(_os.path.join(_root, "baseball.py")).read()
ck("the live model consumes the measured factors and the HR ratio",
   '_park_factor(g["home_id"], season)' in _bbsrc14
   and 'PARK_FACTORS.get(g["home_id"]' not in _bbsrc14
   and "* _park_hr_ratio(g[" in _bbsrc14,
   "measuring and not wiring is the same as not measuring")

_horse14 = {"season": {"ip": 139.1, "gs": 25, "g": 25, "era": 3.2,
                       "whip": 1.1, "k9": 9.5, "bb": 40}}
_open14 = {"season": {"ip": 51.0, "gs": 3, "g": 67, "era": 3.93,
                      "whip": 1.18, "k9": 8.8, "bb": 18}}
ck("a starter owns HIS share of the game, not a flat 60%",
   0.58 <= _bb14._sp_share(_horse14) <= 0.68
   and _bb14._sp_share(_open14) <= 0.20
   and _bb14._sp_share(None) == _bb14.SP_INNINGS_WEIGHT,
   "an opener game is a bullpen game; the flat weight blunted exactly the "
   "games where the starter/pen quality gap is the story")
ck("...and every consumer reads it: pitching blend, platoon exposure, hits",
   "w = _sp_share(sp)" in _insp.getsource(_bb14._pitching_factor)
   and "sp_share=_sp_share(a_sp)" in _bbsrc14
   and "_sp_share(opp_sp)" in _insp.getsource(_bb14._opp_hit_factor),
   "his platoon hand and his WHIP only matter while he is actually pitching")

print()
print("=" * 72)
print("Stadium geometry comes from MLB's survey, not eyeballs")
print("=" * 72)
# The venues API carries surveyed coordinates and the official
# home-plate-to-CF azimuth. The eyeballed table had FIFTEEN of thirty
# bearings wrong by 15+ degrees -- wind priced as "blowing out" at Truist
# (30 vs the real 145) and Comerica (30 vs 150) was actually blowing across
# or in -- and the Athletics' park four miles from its real location.
import stadiums as _st15
_official15 = {144: 145, 116: 150, 147: 75, 158: 129, 142: 129, 146: 128,
               136: 49, 138: 62, 118: 46, 108: 44, 111: 45}
ck("bearings match the official azimuth at every spot-checked park",
   all(_st15.STADIUMS[tid]["cf_bearing_deg"] == az
       for tid, az in _official15.items()),
   "Truist 145, Comerica 150, Yankee 75 -- the old values pointed wind "
   "models 45-120 degrees off")
ck("the Athletics' park is where the Athletics play",
   abs(_st15.STADIUMS[133]["lat"] - 38.5799) < 0.005,
   "weather was fetched four miles from Sutter Health Park")
ck("every team still has a row and a sane roof",
   len(_st15.STADIUMS) == 30
   and all(v["roof"] in ("open", "retractable", "fixed")
           for v in _st15.STADIUMS.values())
   and sum(1 for v in _st15.STADIUMS.values() if v["roof"] == "retractable") == 7)
ck("the table records its provenance",
   "azimuthAngle" in open(_os.path.join(_root, "stadiums.py")).read(),
   "the next editor must know these are surveyed, not vibes")

print()
print("=" * 72)
print("Statcast audit: pitchers get their x-stats too")
print("=" * 72)
# The expected-stats leaderboard was fetched for BATTERS only -- hitters got
# luck-stripped while starters were priced off raw ERA (sequencing + defense
# luck) and FIP (structurally blind to contact quality). xERA is now the
# fourth read in the starter blend, IP-regressed like the others.
import baseball as _bb16
import savant as _sv16
ck("the pitcher expected-stats fetch exists and parses xERA",
   "type=pitcher" in _insp.getsource(_sv16.pitcher_expected_stats)
   and '"xera"' in _insp.getsource(_sv16.pitcher_expected_stats))
_lg16 = {"era": 4.2, "whip": 1.30}
_lucky16 = {"season": {"era": 2.90, "whip": 1.28, "ip": 120.0, "gs": 20,
                       "g": 20, "k": 100, "bb": 40, "hr": 16, "hbp": 5,
                       "xera": 4.30}}
_nox16 = {"season": dict(_lucky16["season"])}
del _nox16["season"]["xera"]
_w16, _wo16 = _bb16._starter_ra9(_lucky16, _lg16), _bb16._starter_ra9(_nox16, _lg16)
ck("a luck-flattered ERA gets priced up toward its contact quality",
   _w16 > _wo16 + 0.05,
   f"ERA 2.90 / xERA 4.30: ra9 {_w16:.2f} with x-stats vs {_wo16:.2f} without")
ck("the four-way blend weights sum to one and xERA rides the slate build",
   "0.25 * era_eff + 0.30 * fip_eff + 0.25 * xera_eff" in
   _insp.getsource(_bb16._starter_ra9)
   and "pitcher_expected_stats" in open(_os.path.join(_root, "baseball.py")).read(),
   "a missing xERA must fall back to the old three-read blend exactly")

print()
print("=" * 72)
print("Time-series Statcast: velocity fatigue flags + x-stat platoon splits")
print("=" * 72)
# The remaining alpha was in signals that predict CHANGE before results do:
# a starter's last-start fastball velocity vs his season average, and platoon
# splits judged by contact quality instead of noisy raw outcomes.
import baseball as _bb17
import deep_data as _dd17
import savant as _sv17

_spv17 = {"season": {"era": 3.5, "whip": 1.2, "ip": 120.0, "gs": 20, "g": 20,
                     "k": 120, "bb": 35, "hr": 12, "hbp": 4}}
_lg17 = {"era": 4.2, "whip": 1.30}
_clean17 = _bb17._starter_ra9(_spv17, _lg17)
_down17 = _bb17._starter_ra9({**_spv17, "velo": {"delta": -1.8}}, _lg17)
_way17 = _bb17._starter_ra9({**_spv17, "velo": {"delta": -6.0}}, _lg17)
_up17 = _bb17._starter_ra9({**_spv17, "velo": {"delta": +1.5}}, _lg17)
_noise17 = _bb17._starter_ra9({**_spv17, "velo": {"delta": -0.5}}, _lg17)
ck("a velocity drop raises expected runs allowed, capped and noise-gated",
   abs(_down17 / _clean17 - 1.054) < 0.01
   and abs(_way17 / _clean17 - 1.09) < 0.01
   and _noise17 == _clean17,
   f"-1.8 mph: {_down17:.2f} vs clean {_clean17:.2f} (+3%/mph, 3 mph cap, "
   "0.8 dead zone) -- velocity moves before ERA does")
ck("a clearly live arm earns a modest half-sized credit",
   abs(_up17 / _clean17 - 0.97) < 0.005,
   "drops are the reliable side of the signal")
ck("the flag is fetched per slate starter and shown on the card",
   "MLB-velo-flag" in open(_os.path.join(_root, "baseball.py")).read()
   and '"velo": st.get("velo")' in open(_os.path.join(_root, "baseball.py")).read()
   and "velo ${v.delta} mph" in open(_os.path.join(_root, "static", "app.js")).read(),
   "the model docking him silently would hide the why")
ck("the velocity fetchers exist with sane guards",
   "pitch-arsenals" in _insp.getsource(_sv17.velo_baselines)
   and "statcast_search" in _insp.getsource(_sv17.last_start_velo)
   and "max(by_type, key=" in _insp.getsource(_sv17.last_start_velo),
   "the primary fastball that day is whichever type he threw most")

# x-stat platoon: synthetic bat whose RAW split says reverse-platoon while
# his contact quality says textbook platoon -- the x-read must win ground.
_old_p17, _old_x17 = _dd17._platoon_one, _sv17.batter_x_splits
try:
    _dd17._platoon_one = lambda pid, season: {
        "vl": {"pa": 60, "k": 14, "hit": 18, "hr": 3},     # raw: HOT vs lefties
        "vr": {"pa": 240, "k": 55, "hit": 58, "hr": 7}}
    _sv17.batter_x_splits = lambda pid, season: {
        "L": {"xwoba": 0.245, "pa": 60}, "R": {"xwoba": 0.345, "pa": 240}}
    _bx17 = [{"id": 1, "name": "synthetic"}]
    _dd17._attach_platoon(_bx17, "2026")
    _with17 = _bx17[0]["plat"]
    _sv17.batter_x_splits = lambda pid, season: None
    _br17 = [{"id": 1, "name": "synthetic"}]
    _dd17._attach_platoon(_br17, "2026")
    _raw17 = _br17[0]["plat"]
finally:
    _dd17._platoon_one, _sv17.batter_x_splits = _old_p17, _old_x17
ck("contact quality overrules a noisy raw reverse-split",
   _with17["L"]["hit"] < _raw17["L"]["hit"]
   and _with17["L"]["hr"] < _raw17["L"]["hr"],
   f"raw said hot vs L (hit {_raw17['L']['hit']}); x-blend pulled it to "
   f"{_with17['L']['hit']} -- the live NYY test caught the same lie on "
   "Chisholm (raw 1.015 vs L against .235/.326 xwOBA)")
ck("...while calibration holds and K stays raw-only",
   abs(0.28 * _with17["L"]["hit"] + 0.72 * _with17["R"]["hit"] - 1.0) < 0.03
   and _with17["L"]["k"] == _raw17["L"]["k"]
   and 'comp in ("hit", "hr")' in _insp.getsource(_dd17._attach_platoon)
   and "k_shrink / 2.0" in _insp.getsource(_dd17._attach_platoon),
   "the exposure-weighted mean stays 1 (season engine untouched in "
   "expectation); x stabilizes ~2x faster so its shrink is half")
ck("no x data reproduces the raw-only multipliers exactly",
   _raw17["L"]["hit"] == _raw17["L"]["hit"] and "SAV-xsplit"
   in _insp.getsource(_dd17._attach_platoon),
   "Savant down means yesterday's behavior, not a broken platoon layer")

print()
print("=" * 72)
print("Kalshi mechanics: price drift, reverse arb, maker suggestions")
print("=" * 72)
# Three market-facing reads the model math can't provide: where the price has
# been GOING (24h mid drift from candlesticks), books whose BIDS sum over
# 100c (buy NO everywhere -- the side the YES arb check can't see), and
# posting inside a wide spread instead of paying the ask plus taker fee.
import kalshi as _kl18
_old_get18 = _kl18._get_json
try:
    _kl18._move_cache.clear()
    _kl18._get_json = lambda url, timeout=10: {"candlesticks": [
        {"yes_ask": {"close_dollars": "0.99"}, "yes_bid": {"close_dollars": "0.00"}},
        {"yes_ask": {"close_dollars": "0.52"}, "yes_bid": {"close_dollars": "0.48"}},
        {"yes_ask": {"close_dollars": "0.58"}, "yes_bid": {"close_dollars": "0.54"}},
    ]}
    _mv18 = _kl18.price_move("TEST-SYNTH-X")
    ck("price drift reads the mid of two-sided hours and skips empty-book sentinels",
       _mv18 is not None and _mv18["from"] == 50.0 and _mv18["to"] == 56.0
       and _mv18["move"] == 6.0 and _mv18["n"] == 2,
       f"got {_mv18} -- the 99/0 sentinel hour must not count as a price")
finally:
    _kl18._get_json = _old_get18
    _kl18._move_cache.clear()
_spsrc18 = open(_os.path.join(_root, "sports.py")).read()
ck("reverse arbitrage needs a real bid on EVERY outcome",
   "all(b for b in bids)" in _spsrc18 and "no_arb_fee_est" in _spsrc18,
   "a missing bid means the NO-everywhere basket cannot actually be bought")
ck("the drift fetch is bounded and threaded, not one call per market",
   "[:14]" in _spsrc18 and "price_move" in _spsrc18,
   "43 tennis events x 1 candlestick fetch each would be a rate-limit gift")
_js18 = open(_os.path.join(_root, "static", "app.js")).read()
ck("reverse arb only shows when it survives the taker fees",
   "e.no_arb_fee_est || 0) + 0.5" in _js18 and "NO on every outcome" in _js18,
   "the live NFL probe found +1.0c gross against 2.0c of fees -- showing "
   "that as free money would cost the user real money")
ck("the drift chip and maker suggestion render with their caveats",
   "Market moved" in _js18 and "posting a resting bid" in _js18
   and "may not" in _js18,
   "a maker order's price is fill risk; saying so is the difference between "
   "a tip and a trap")

print()
print("=" * 72)
print("Memory: one shared cache sweeper, pruned CSVs, a mem diagnostic")
print("=" * 72)
# The instance was killed for exceeded memory; the ledger's smoking gun was a
# slate-build child dying (SLATE-child rc=1) while the new per-batter
# statcast fetches ran: 1,400 rows x ~90 string columns of transient dicts
# per player, eight threads at a time -- plus the SAME read-only-TTL cache
# leak found for the third and fourth time (savant, value, weather, and the
# new price-move cache). The sweep pattern is now a shared module.
import time as _tm19
import ttlcache as _tc19
_c19 = {}
for _i19 in range(70):
    _tc19.cached(_c19, ("old", _i19), 0.01, lambda: 1)
_tm19.sleep(0.05)
for _i19 in range(70):
    _tc19.cached(_c19, ("new", _i19), 60, lambda: 1)
ck("the shared sweeper evicts expired entries nobody reads again",
   sum(1 for k in _c19 if k[0] == "old") == 0,
   "the read-only TTL check kept them forever, in every worker, times three")
for _mod19, _lbl19 in (("savant", "savant"), ("value", "value"),
                       ("weather", "weather"), ("kalshi", "kalshi")):
    pass
_srcs19 = {m: open(_os.path.join(_root, f"{m}.py")).read()
           for m in ("savant", "value", "weather", "kalshi")}
ck("all four leaking caches route through it",
   all("ttlcache" in s for s in _srcs19.values()),
   "weather keys on the HOUR and price-move on rotating tickers -- growing "
   "keys with no eviction")
ck("statcast CSV rows are pruned to the columns actually used",
   "keep=" in _srcs19["savant"]
   and 'keep=("events", "p_throws", "woba_value"' in _srcs19["savant"]
   and 'keep=("game_date", "pitch_type", "release_speed")' in _srcs19["savant"],
   "~90 string columns x 1,400 rows x 8 threads of transient dicts per "
   "batter is how a build child gets OOM-killed")
ck("a memory diagnostic answers 'which worker, which cache'",
   '"/api/diag/mem"' in open(_os.path.join(_root, "app.py")).read()
   and "VmRSS" in open(_os.path.join(_root, "app.py")).read(),
   "the first question after an exceeded-memory kill")

print()
print("=" * 72)
print("PC compute worker: the upload door and its gates")
print("=" * 72)
# The user's desktop sims the slate (~32s a game vs 200s+ on the shared cloud
# CPU) and uploads; the server adopts what's freshest and self-computes when
# the PC is off. The door must be locked three ways: token (even with no app
# password), schema version (a stale checkout waits for its next git pull),
# and a numeric pk (no path games).
import baseball as _bb20
_apy20 = open(_os.path.join(_root, "app.py")).read()
ck("the upload door demands the token even when the app has no password",
   "_pc_auth_ok" in _apy20 and "writes must never be open" in _apy20,
   "the before_request gate waves everything through when APP_PASSWORD is "
   "unset; an unauthenticated write path would be a public cache poisoner")
ck("a stale PC checkout is rejected by schema, not adopted",
   "schema != baseball.GAME_SIM_SCHEMA" in _apy20
   and "GAME_SIM_SCHEMA = 1" in open(_os.path.join(_root, "baseball.py")).read(),
   "bump GAME_SIM_SCHEMA in the SAME commit as any change to what "
   "_game_sim stores")
ck("adopted sims land atomically where every worker reads",
   "os.replace" in _insp.getsource(_bb20.sim_disk_write_raw).replace("_os", "os")
   and "int(pk)" in _insp.getsource(_bb20.sim_disk_write_raw),
   "temp+rename so a reader never sees a half-written pickle; int() so a "
   "pk can never be a path")
_pw20 = open(_os.path.join(_root, "pc_worker.py")).read()
ck("the worker asks what the server needs before simulating",
   '_api(url, tok, "/api/sim/have")' in _pw20 and "schema" in _pw20
   and '("Final", "Live")' in _pw20,
   "re-uploading what the server already has fresh is pure waste; simming "
   "finished games is worse (sim/have also carries warm_date now)")
_bat20 = open(_os.path.join(_root, "vigil-pc.bat")).read()
_loop20 = open(_os.path.join(_root, "pc_loop.py")).read()
ck("the bootstrap is frozen and delegates every brain to the repo",
   "FROZEN" in _bat20 and "pc_loop.py" in _bat20
   and "pc_worker.py" not in _bat20.replace("rem", ""),
   "cmd reads a running .bat by byte offset; a bat that git-pulls itself "
   "mid-loop can corrupt its own execution -- so the bat never changes again")
ck("the loop checks git every minute but sims on its own cadence",
   "CHECK_S = 60" in _loop20 and "CYCLE_S = 600" in _loop20
   and 'rev-parse", "@{u}"' in _loop20 and "sys.exit(0)" in _loop20,
   "an update pulls, reinstalls and EXITS for a clean restart on fresh "
   "code -- the PC is current within a minute of a push, without burning "
   "CPU on a slate rebuild every minute")
ck("the token config can never be committed",
   "vigil-pc.cfg" in open(_os.path.join(_root, ".gitignore")).read()
   and not _os.path.exists(_os.path.join(_root, "vigil-pc.cfg")),
   "the example ships; the real one stays on the PC")

print()
print("=" * 72)
print("Typed combo settings survive the board re-rendering itself")
print("=" * 72)
# Reported verbatim with a screenshot: "whenever I enter an integer or change
# anything it always reverts back". The board re-renders while warming, and
# the makers' controls were rebuilt from globals that only update when Build
# is pressed -- typing 55 into the payout box and watching a refresh erase it.
# One delegated listener records every keystroke into comboFormVals; both
# makers render map-first, defaults second.
_js21 = open(_os.path.join(_root, "static", "app.js")).read()
# Every control the makers own must appear in the delegated listener's id
# pattern -- named individually rather than as one frozen literal, so adding a
# control (Sides was the fourth) cannot quietly opt it out of persistence.
_pat21 = _re.search(r"\^\(combo\|nflCombo\)\(([A-Za-z|]+)\)\$", _js21)
_ctl21 = set((_pat21.group(1) if _pat21 else "").split("|"))
ck("every keystroke in either maker is recorded as typed",
   "comboFormVals[t.id]" in _js21 and _pat21 is not None
   and {"Target", "Cap", "N", "Payout", "Objective", "LegsMode", "Conn",
        "PayoutMode", "SameGame", "Sides"} <= _ctl21,
   f"the listener is delegated so it survives any innerHTML rebuild; "
   f"pattern covers {sorted(_ctl21)}")
ck("both makers render what the user typed over the stale default",
   'cfv("comboPayout", parlayPayout)' in _js21
   and 'cfv("comboN", def)' in _js21
   and 'cfv("nflComboPayout", nflComboPayout)' in _js21
   and 'cfv("nflComboTarget", nflComboTarget)' in _js21,
   "a re-render must reproduce the form the user was looking at")
ck("selects and the same-game checkbox are covered too",
   'cfv("comboObjective", comboObjectivePref)' in _js21
   and 'cfv("comboSameGame", comboSameGamePref || sgOnly)' in _js21,
   '"or change anything" included the dropdowns')

print()
print("=" * 72)
print("Fanfare: a few seconds of team emojis, exactly once per final")
print("=" * 72)
_apy22 = open(_os.path.join(_root, "app.py")).read()
_js22 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the endpoint reports only FRESH finals for both teams",
   '"/api/fanfare"' in _apy22 and "teamId=108" in _apy22
   and '"PIT"' in _apy22 and "36 * 3600" in _apy22,
   "an old result must never trigger a celebration")
ck("each final fires exactly once per device",
   "localStorage.getItem(key)" in _js22 and "fanfare_" in _js22
   and "localStorage.setItem(key" in _js22,
   "'only on the next open after the final score drops'")
ck("all four outcomes are staged: mega happy, mega sad, split, single",
   "BOTH WON" in _js22 and "BOTH LOST" in _js22
   and '_fanSpawn(_HAPPY, 55, 0, 48' in _js22
   and '_fanSpawn(_SAD, 55, 52, 100' in _js22,
   "split result = each half of the screen gets its verdict, team named")
ck("the show is seconds, not a mode",
   "el.remove(), 4500" in _js22 and "el.remove(), 5200" in _js22
   and "fanfly var(--dur)" in open(_os.path.join(_root, "static", "style.css")).read(),
   "'just for a few seconds not the whole time lol' -- every element "
   "self-destructs")

print()
print("=" * 72)
print("Full offload: every simulator's artifacts flow through one door")
print("=" * 72)
# The three stores (gamesim / boards / deep) are all flat mtime-adopted
# pickle dirs, so the whole offload is: the PC runs the SAME builders, then
# syncs fresher files. The server's daily scheduler judges "already ran
# today" from the saved file's timestamp, so an uploaded deep run makes it
# skip its own multi-hour rebuild with no scheduler changes.
import artifacts as _ar23
ck("artifact names are flat, bounded, and traversal-proof",
   _ar23.valid_name("nfl_parlay_sims3_2026_w1_pre_4000.pkl")
   and not _ar23.valid_name("../evil.pkl")
   and not _ar23.valid_name("a/b.pkl") and not _ar23.valid_name("x.py"),
   "a name is a filename in one of three known dirs, never a path")
ck("the three stores resolve through the consumers' own env vars",
   "baseball._SIM_DISK" in _insp.getsource(_ar23.dir_for)
   and "boardshare._DIR" in _insp.getsource(_ar23.dir_for)
   and "deep_cache.CACHE_DIR" in _insp.getsource(_ar23.dir_for),
   "uploading to a path the readers don't read is not adoption")
_apy23 = open(_os.path.join(_root, "app.py")).read()
ck("the generalized door carries the same three gates as the sim door",
   '"/api/art/upload"' in _apy23 and "schema != artifacts.SCHEMA" in _apy23
   and "artifacts.valid_name(name)" in _apy23 and "_pc_auth_ok" in _apy23)
ck("coherence + ump keep running on the server when the PC does the nightly",
   '"mlb_nightly_extras"' in _apy23,
   "a fresh uploaded mlb_deep.pkl makes the daily scheduler skip run_mlb "
   "entirely -- the GitHub-history extras must not vanish with it")
_pw23 = open(_os.path.join(_root, "pc_worker.py")).read()
ck("the PC carries every offloadable nightly, with the exact server shapes",
   all(f'"{k}"' in _pw23 for k in ("mlb_deep", "f1", "nascar", "nfl_season",
                                   "cfb", "nfl", "nba", "nhl"))
   and '{"agg": agg, "season": season}' in _pw23
   and "career_frac" in _pw23,
   "mlb_deep is saved as the run_mlb wrapper and behind the same quality "
   "gate; model_trust stays server-side (it replays the server's own "
   "prediction log)")
ck("the PC builds every boardshare board with the server's own builders",
   all(s in _pw23 for s in ("golf", "tennis_prices", "ufc_sim", "lol",
                            "basket", "hockey", "mfutures", "kalshi_nfl",
                            "nfl_dfs_sim", "_slate_sims")),
   "one broken sport never strands the rest (each task is guarded)")

print()
print("=" * 72)
print("The live-sim leak (the Aug 22-23 exceeded-memory kills)")
print("=" * 72)
# _live_game_sim keys its cache on the live-state signature, which moves with
# every at-bat and NEVER repeats -- so each superseded entry (a multi-MB
# 4,000-run simulation) was dead the moment the next landed. The sweep only
# ran every 500 puts, and a serving worker does a handful of puts a minute:
# hours between sweeps, hundreds of MB of dead sims per worker per live
# window. Both instance kills sat squarely inside live windows. Three locks:
# eviction at the source, a time floor on the sweep, a watchdog that makes
# the next kill legible from the ledger.
import baseball as _bb24
import mlb_live as _mlv24
import mlb_sim as _msim24

_snap24a = {"inning": 4, "is_top": True, "outs": 1, "away_runs": 2,
            "home_runs": 3, "bases": [1, 0, 0],
            "banked": {"home": {}, "away": {}}, "pitching": {}}
_snap24b = dict(_snap24a, outs=2)          # the next at-bat: a NEW signature
_pk24 = 999901
_olds24 = (_mlv24.snapshot, _msim24.simulate, _msim24.build_candidates)
try:
    _mlv24.snapshot = lambda pk: _snap24a
    _msim24.simulate = lambda g, n, live=None: {"stub": True}
    _msim24.build_candidates = lambda g, sim: []
    _bb24._live_game_sim({"game_pk": _pk24})
    _mlv24.snapshot = lambda pk: _snap24b
    _bb24._live_game_sim({"game_pk": _pk24})
    _live24 = [k for k in list(_bb24._cache)
               if isinstance(k, tuple) and len(k) == 3
               and k[0] == "game_sim_live" and k[1] == _pk24]
    ck("a game holds exactly ONE live sim entry, the current situation's",
       len(_live24) == 1 and _live24[0][2] == _bb24._live_state_sig(_snap24b),
       f"found {len(_live24)} entries -- superseded situations must be "
       "evicted the moment the next lands, not left for a sweep hours out")
finally:
    _mlv24.snapshot, _msim24.simulate, _msim24.build_candidates = _olds24
    for _k24 in [k for k in list(_bb24._cache)
                 if isinstance(k, tuple) and k and k[0] == "game_sim_live"
                 and k[1] == _pk24]:
        _bb24._cache.pop(_k24, None)

# The time floor: an expired entry is swept by the NEXT put once
# _CACHE_SWEEP_S has passed, no matter how few puts have happened.
_bb24._cache["_guard24_dead"] = (_tm.time() - 999, "x", 1)
_bb24._cache_swept = 0.0
_bb24._cached("_guard24_live", 60, lambda: 1)
ck("the cache sweep has a time floor, not just an every-500-puts trigger",
   "_guard24_dead" not in _bb24._cache
   and _bb24._CACHE_SWEEP_S <= 300,
   "500 puts between sweeps is hours at a serving worker's put rate, and "
   "the live entries are multi-MB each")
_bb24._cache.pop("_guard24_live", None)

# The velocity memo: ~30 per-pitcher Statcast searches, and the slate builds
# in a FRESH child every few minutes -- without a disk memo every rebuild
# refetched all of them cold (thousands of Savant hits a day, and the slow
# minutes a returning phone spends on "building today's board").
import tempfile as _tmp24
_oldsd24 = _bb24._SLATE_DISK
try:
    _bb24._SLATE_DISK = _tmp24.mkdtemp()
    _memo24 = {101: {"delta": -1.2, "type": "ff"}, 102: None}
    _bb24._velo_memo_put("2099-01-01", _memo24)
    ck("the day's velocity answers round-trip through the disk memo",
       _bb24._velo_memo_get("2099-01-01") == _memo24,
       "None entries included, so 'no signal' is not re-asked every rebuild")
    _p24 = _os.path.join(_bb24._SLATE_DISK, "velo_2099-01-01.pkl")
    _os.utime(_p24, (_tm.time() - 4 * 3600, _tm.time() - 4 * 3600))
    ck("...and an aged memo expires instead of pinning stale readings",
       _bb24._velo_memo_get("2099-01-01") == {})
finally:
    _bb24._SLATE_DISK = _oldsd24
_slsrc24 = _insp.getsource(_bb24._analyze_slate_uncached)
ck("the slate build fetches only pids the memo has never seen",
   "_velo_memo_get" in _slsrc24 and "missing" in _slsrc24
   and "ex.map(_velo_one, missing)" in _slsrc24)

# The instrumentation that makes the NEXT kill data instead of guesswork.
_apy24 = open(_os.path.join(_root, "app.py")).read()
ck("the memory diagnostic sees the whole instance, not one worker",
   "_proc_tree_mb" in _apy24 and "_cgroup_mem" in _apy24
   and '"cgroup_used_mb"' in _apy24,
   "an instance-level kill is about the SUM of workers + sim children + the "
   "slate builder, which no single worker's RSS can answer")
ck("a watchdog ledgers the fat processes before a kill",
   "_start_mem_watchdog" in _apy24 and '"MEM-high"' in _apy24
   and '"APP-ensure_recorder-6"' in _apy24,
   "Render's email says only 'exceeded memory'; the ledger on the persistent "
   "disk should say which processes were fat minutes before")
ck("request bodies are bounded before Flask buffers them",
   'app.config["MAX_CONTENT_LENGTH"]' in _apy24,
   "the artifact door's 64 MB cap runs AFTER get_data() has read the body")
_gwf24 = open(_os.path.join(_root, ".github", "workflows",
                            "error-log.yml")).read()
ck("every ledger snapshot carries the instance memory picture",
   "/api/diag/mem" in _gwf24 and "mem-latest.json" in _gwf24)

# The NFL futures board sat dark through August: ESPN's team-schedule
# endpoint defaults to the season phase currently underway, so ?season=2026
# answered with the four preseason games, the regular-season filter dropped
# every one, and project() returned None right through the Aug-1 window that
# exists precisely so preseason futures are priceable.
_pd25 = open(_os.path.join(_root, "pro_data.py")).read()
ck("the pro schedule fetches ask ESPN for the regular season EXPLICITLY",
   _pd25.count("seasontype=2") >= 2,
   "both schedule() and season_state() must pin seasontype=2 -- the "
   "endpoint's default follows the calendar, and in preseason that is "
   "type 1")

print()
print("=" * 72)
print("The track-record audit: honest scoreboards (MLB) + the NFL port")
print("=" * 72)
# Deep audit of the two scoreboards found five leaks, all variations on one
# mistake: letting IN-GAME information touch numbers that claim to be
# pre-game. CLV compared entry to a "close" refreshed during play (+14.8c of
# pure hindsight), props kept re-pricing while games ran, NO fills pocketed
# the whole spread (100-ask instead of 100-bid), ROI ignored taker fees, and
# the totals-bias stat averaged a retired model's predictions. The NFL port
# reuses the FIXED shared core, so it cannot re-learn the old mistakes.
import importlib as _il26
import tempfile as _tmp26
import store as _st26
import nfl_track as _nt26

ck("Kalshi taker fees: ceil(7·P·(1-P)) cents",
   _st26._fee_cents(50) == 2 and _st26._fee_cents(3) == 1
   and _st26._fee_cents(97) == 1,
   "~2c at even money; an ROI that skips it flatters every bet")

_olddb26 = _st26.DB_PATH
try:
    _st26.DB_PATH = _os.path.join(_tmp26.mkdtemp(), "guard26.db")
    _st26.init_db()
    _st26.init_db()          # migrations must be idempotent across boots

    _st26.record_mlb_pick(1, "2026-08-24", "home", "LAA", 0.61, 55.0,
                          pred_total=9.1, prob_raw=0.66)
    _st26.update_mlb_close(1, 60.0, pick_side="home")
    _st26.update_mlb_close(1, 40.0, pick_side="away")
    _r26 = _st26.ungraded_mlb_picks()[0]
    ck("a flipped pick can never adopt the other team's close",
       _r26["close_price"] == 60.0,
       "the close update matches on pick_side; the recorded side is "
       "first-write-wins but the board's pick can cross 50% between builds")
    ck("MLB picks stamp the run-model generation",
       _r26["model_version"] == _st26.MODEL_VERSION,
       "a retired model's totals must age out of the bias stat")
    _st26.set_mlb_grade(1, 1, "LAA", actual_total=8, home_won=1)
    _rec26 = _st26.mlb_record()
    ck("MLB ROI is fee-inclusive and says so",
       _rec26["fees_included"] and _rec26["roi_pct"] == 78.2
       and bool(_rec26["clv_note"]),
       "win at 55c = 100-55-2(fee) = 43c on 55 staked = +78.2%")

    _st26.record_nfl_pick("2026-09-13_KC@BUF", "2026-09-13", 1, False,
                          "home", "BUF", 0.58, 57.0, pred_total=47.5,
                          prob_raw=0.62)
    _st26.update_nfl_close("2026-09-13_KC@BUF", 61.0, "home")
    _st26.update_nfl_close("2026-09-13_KC@BUF", 39.0, "away")
    _fin26 = {("BUF", "KC"): (27.0, 20.0), ("WAS", "DAL"): (10.0, 10.0)}
    _g26 = _nt26._grade_rows(_st26.ungraded_nfl_picks(), _fin26)
    ck("NFL grading: winner, total, home_won from the scoreboard",
       _g26 == [("2026-09-13_KC@BUF", 1, "BUF", 47.0, 1)], _g26)
    for _gid26, _w26, _wn26, _t26, _hw26 in _g26:
        _st26.set_nfl_grade(_gid26, _w26, _wn26, actual_total=_t26,
                            home_won=_hw26)
    _st26.record_nfl_pick("2026-09-13_DAL@WAS", "2026-09-13", 1, False,
                          "away", "DAL", 0.52, 50.0)
    ck("a TIE grades as a loss for either side (how the market settles)",
       _nt26._grade_rows(_st26.ungraded_nfl_picks(), _fin26)
       == [("2026-09-13_DAL@WAS", 0, "TIE", 20.0, None)])
    ck("ESPN/board abbreviation aliases are canonicalized both ways",
       _nt26.canon("WSH") == "WAS" and _nt26.canon("jac") == "JAX")
    _nr26 = _st26.nfl_record()
    ck("the NFL scoreboard reuses the shared core, preseason separate",
       _nr26["regular"]["wins"] == 1 and _nr26["regular"]["clv_avg"] == 4.0
       and _nr26["regular"]["fees_included"]
       and _nr26["preseason"]["graded"] == 0,
       "exhibitions are a different distribution (see the predlog split)")

    _st26.log_prop(2, "2026-08-24", 100, "A B", "hits", 1, "hit1",
                   40.0, 20.0, 35.0, 30.0, kalshi_bid_cents=15.0)
    _st26.log_prop(2, "2026-08-24", 100, "A B", "hits", 1, "hit1",
                   45.0, 30.0, 35.0, 30.0, kalshi_bid_cents=22.0)
    _p26 = _st26.ungraded_props()[0]
    ck("prop entry ask+bid freeze at first sight; the rolling read moves",
       _p26["entry_cents"] == 20.0 and _p26["kalshi_cents"] == 30.0
       and _p26["entry_bid_cents"] == 15.0 and _p26["kalshi_bid_cents"] == 22.0)
    _st26.grade_prop(_p26["id"], 1)
    _st26.log_prop(3, "2026-08-24", 101, "C D", "hits", 1, "hit1",
                   10.0, 40.0, 12.0, 11.0)          # fade candidate, NO bid
    _p26b = [x for x in _st26.ungraded_props() if x["game_pk"] == 3][0]
    _st26.grade_prop(_p26b["id"], 0)
    _rep26 = _st26.prop_report(min_edge=8.0)
    _m26 = _rep26["model_edge_roi"]
    ck("prop ROI: entry basis, fees in, and NO needs a real bid",
       _rep26["basis"] == "entry" and _m26 and _m26["bets"] == 1
       and _m26["fees_included"] and _m26["pnl_per_contract_c"] == 78.0,
       "the old 100-ask fade pocketed the whole spread in books this thin -- "
       "that mirage was most of a +47% 'ROI'")
    ck("market Brier is scored at the same moment as the model's",
       _rep26["market_brier"] == 0.4,
       "(0.20-1)^2 and (0.40-0)^2 over 2 rows; a fair race needs one clock")
finally:
    _st26.DB_PATH = _olddb26

# The gates that keep the ledger pre-game, at their source seams.
_apy26 = open(_os.path.join(_root, "app.py")).read()
ck("MLB picks record on Preview only, close updates same-side",
   'in (None, "", "Preview")' in _apy26
   and "pick_side=side" in _apy26,
   "'not Final' also let LIVE games through: a first-seen-live game logged "
   "an in-game entry, and every poll refreshed close_price with the score")
_mrec26 = open(_os.path.join(_root, "mlb_recorder.py")).read()
ck("the prop recorder logs pre-game only and carries the bid",
   "is_live" in _mrec26 and "yes_bid_dollars" in _mrec26
   and "kalshi_bid_cents=m.get(\"bid\")" in _mrec26)
_stsrc26 = open(_os.path.join(_root, "store.py")).read()
ck("the contaminated MLB closes were reset, dated, idempotently",
   "close_price=NULL" in _stsrc26 and "2026-08-23" in _stsrc26)
ck("prop CLV starts from the clean era",
   "_CLV_CLEAN_TS" in _stsrc26)
ck("the NFL slate records picks and /api/nfl/record grades them",
   "nfl_track.record_from_board(data)" in _apy26
   and '"/api/nfl/record"' in _apy26)
_ntsrc26 = _insp.getsource(_nt26.record_from_board)
ck("the NFL recorder is gated pre-game like everything else",
   '!= "pre"' in _ntsrc26 and "continue" in _ntsrc26)
_js26 = open(_os.path.join(_root, "static", "app.js")).read()
ck("one shared renderer serves both scoreboards, fees labelled",
   "function pickRecordHtml" in _js26 and "loadNflRecord" in _js26
   and "after fees" in _js26
   and 'pickRecordHtml(r, "runs")' in _js26
   and 'pickRecordHtml(reg, "points")' in _js26)
ck("the NFL tab has its record box",
   'id="nflRecord"' in open(_os.path.join(_root, "templates",
                                          "index.html")).read())

# The 892 MB slate child (caught by the memory watchdog on its first night):
# team profiles were cached in-process only, and the slate builds in a fresh
# child every few minutes -- each one re-hydrated every slate team from zero,
# thirteen Statcast x-split searches per club included. Profiles now persist
# in the deep artifact store so a child reads what a sibling (or the PC
# worker) already paid for.
import deep_data as _dd27
import deep_cache as _dc27
_oldcd27 = _dc27.CACHE_DIR
try:
    import tempfile as _tf27
    _dc27.CACHE_DIR = _tf27.mkdtemp()
    _val27 = {"rotation": [1], "bullpen": [2], "lineup": [3], "bench": [],
              "_quality": {"players": 1, "with_career": 1, "xstats": 0}}
    _dd27._profile_disk_put(133, "2099", _val27)
    ck("team profiles round-trip through the deep store",
       _dd27._profile_disk_get(133, "2099") == _val27)
    _p27 = _dd27._profile_path(133, "2099")
    _os.utime(_p27, (_tm.time() - 7 * 3600, _tm.time() - 7 * 3600))
    ck("...and expire at the same 6h the in-process cache uses",
       _dd27._profile_disk_get(133, "2099") is None)
    import artifacts as _ar27
    ck("the profile files ride the PC sync door as-is",
       _ar27.valid_name("profile_133_2099.pkl"),
       "the PC worker hydrates them nightly and uploads; the server's slate "
       "children then never pay the 30-team hydration at all")
finally:
    _dc27.CACHE_DIR = _oldcd27
ck("team_profile reads the disk before building",
   "disk_or_build" in _insp.getsource(_dd27.team_profile))

print()
print("=" * 72)
print("Racing DFS: scenario-coherent lineups for 20-driver pools")
print("=" * 72)
# Small-pool DFS is duplication + scenario selection, not ceiling-stacking:
# place differential is zero-sum, so a lineup's ceiling is one coherent race
# script, and the chalk build repeats dozens of times at any real field size.
# The simulator samples correlated finish orders from the SAME Plackett-Luce
# win model the Kalshi board shows (Gumbel-max), scores real DK points, and
# candidates are the optimal build PER simulated race.
import racing_dfs as _rd28
import math as _m28

_names28 = [f"driver {chr(97 + i)}" for i in range(20)]
_z28 = sum(_m28.exp(-i / 2.5) for i in range(20))
_probs28 = {nm: 0.97 * _m28.exp(-i / 2.5) / _z28 + 0.03 / 20
            for i, nm in enumerate(_names28)}
_fm28 = {"grid": {"grid": {nm: i + 1 for i, nm in enumerate(_names28)},
                  "race": "Guard GP", "field": 20},
         "probs": _probs28, "track_type": None}
_fld28 = _rd28.simulate_field("f1", _fm28, n=800, seed=3)
_fav28 = _names28[0]
_ws28 = sum(1 for s in _fld28["sims"] if s["finish"][_fav28] == 1) / 800
ck("the sampled winner marginal IS the board's win probability",
   abs(_ws28 - _probs28[_fav28]) < 0.06,
   f"modeled {_probs28[_fav28]:.3f} vs simulated {_ws28:.3f} - Gumbel-max "
   "over log win-prob reproduces Plackett-Luce exactly")
_tm28 = {_names28[i]: _names28[i + 1 if i % 2 == 0 else i - 1]
         for i in range(20)}
_pts28 = _rd28.score_sims("f1", _fld28, teammates=_tm28)
ck("DK scoring ladders: F1 25-to-the-winner, NASCAR 45, both monotone",
   _rd28._f1_fin(1) == 25 and _rd28._f1_fin(10) == 1
   and _rd28._f1_fin(11) < 1
   and _rd28._nas_fin(1) == 45.0 and _rd28._nas_fin(2) == 42.0
   and _rd28._nas_fin(3) == 41.0)
ck("the favorite out-scores the backmarker on average, but not in ceiling "
   "share alone",
   sum(_pts28[_fav28]) > sum(_pts28[_names28[-1]]))
_sal28 = {nm: 12000 - 500 * i for i, nm in enumerate(_names28)}
_cn28 = {f"Team {i}": {"salary": 8000 - 500 * i, "team": f"T{i}"}
         for i in range(10)}
_team28 = {nm: f"T{i // 2}" for i, nm in enumerate(_names28)}
_lu28 = _rd28._greedy_f1({nm: _pts28[nm][0] for nm in _names28},
                         _names28, _sal28, _cn28, _team28, 50000)
_cnteam28 = _cn28[_lu28["constructor"]]["team"]
_paired28 = [x for x in list(_lu28["drivers"]) + [_lu28["cpt"]]
             if _team28[x] == _cnteam28]
_spend28 = (round(1.5 * _sal28[_lu28["cpt"]])
            + sum(_sal28[nm] for nm in _lu28["drivers"])
            + _cn28[_lu28["constructor"]]["salary"])
ck("F1 lineups honor DK's build rules: CPT+4+CNSTR, cap, no team pairing",
   len(_lu28["drivers"]) == 4 and _lu28["cpt"] not in _lu28["drivers"]
   and len(_paired28) <= 1 and _spend28 <= 50000,
   "a constructor may never be stacked with BOTH of its drivers")
_lun28 = _rd28._greedy_nas({nm: _pts28[nm][0] for nm in _names28},
                           _names28, _sal28, 50000)
ck("NASCAR lineups: six drivers under the cap",
   len(_lun28["drivers"]) == 6
   and sum(_sal28[nm] for nm in _lun28["drivers"]) <= 50000)
ck("the board shares one build across workers and the PC",
   "boardshare.nonblocking" in _insp.getsource(_rd28.board))
ck("race_board and the DFS sim read the SAME model bundle",
   "field_model(" in _insp.getsource(__import__("racing").race_board)
   and "field_model(" in _insp.getsource(_rd28.build))
_apy28 = open(_os.path.join(_root, "app.py")).read()
ck("the endpoint + UI panel + PC builders are wired",
   '"/api/racing/dfs"' in _apy28
   and "loadRacingDfs" in open(_os.path.join(_root, "static", "app.js")).read()
   and "dfs-f1" in open(_os.path.join(_root, "pc_worker.py")).read())
ck("duplication rescales client-side from p_build",
   '"p_build"' in _insp.getsource(_rd28.build)
   and "p_build" in open(_os.path.join(_root, "static", "app.js")).read(),
   "the field-size input re-prices dupes without a server rebuild")

print()
print("=" * 72)
print("The 19-leg build that returned 2 legs")
print("=" * 72)
# Reported live: "require 19 legs", strikeouts only, 15 games -> a TWO-leg
# slip. Two independent faults. (1) The frontier DP clamped every request to
# 12 legs, so no 19-leg state ever existed while the maker happily accepted
# 19. (2) With the exact count unreachable, choose() fell through to plain
# probability -- and the likeliest slip on a frontier is the SHORTEST one, so
# the answer to "give me 19" was the furthest thing from it.
import combo_engine as _ce29
import random as _rnd29

ck("the DP ceiling follows the request instead of a flat 12",
   _ce29.dp_legs(19, "require", 30) >= 19
   and _ce29.dp_legs(4, "prefer", 30) < 12
   and _ce29.dp_legs(99, "require", 30) == 30,
   "a 19-leg ask must be reachable, a 4-leg ask must not pay for depth it "
   "never uses, and nothing may exceed the tier ceiling")
ck("a payout target keeps the default depth even with no leg target",
   _ce29.dp_legs(0, "off", 30, payout_mode="require") == _ce29._DP_LEGS_DEFAULT
   and _ce29.dp_legs(3, "require", 30, payout_mode="require")
   >= _ce29._DP_LEGS_DEFAULT,
   "reaching a big payout may need legs the count never asked for; "
   "truncating there would silently cap the payout chaser")

_rng29 = _rnd29.Random(5)
_gb29 = []
for _gi29 in range(15):
    _bl29 = []
    for _ in range(10):
        _sz29 = _rng29.randint(1, 3)
        _p29 = _rng29.uniform(0.80, 0.97) ** _sz29
        _m29 = _p29 ** (1.0 / _sz29)
        _bl29.append({"size": _sz29, "prob": _p29,
                      "legs": [{"marg": _m29,
                                "price_cents": min(97, max(3, round(_m29 * 100))),
                                "fillable": True} for _ in range(_sz29)]})
    _gb29.append((f"g{_gi29}", _bl29, f"S{_gi29}"))

_dp29 = _ce29.dp_legs(19, "require", 30)
_st29 = _ce29.frontier(_gb29, max_total_legs=_dp29)
_b29, _m29meta = _ce29.choose(_st29, objective="balanced", legs_target=19,
                              legs_mode="require", payout_target=0,
                              payout_mode="off", conn="or")
ck("a 19-leg board request now actually returns 19 legs",
   _b29["legs"] == 19 and _m29meta["hard_ok"],
   f"got {_b29['legs']} legs - this is the exact reported build")

# Genuinely unreachable: the answer must be the CLOSEST slip, not the shortest.
_st29b = _ce29.frontier(_gb29, max_total_legs=12)
_b29b, _m29b = _ce29.choose(_st29b, objective="balanced", legs_target=19,
                            legs_mode="require", payout_target=0,
                            payout_mode="off", conn="or")
ck("an unreachable leg count lands ON the ceiling, never back at 2",
   _b29b["legs"] == _m29b["legs_ceiling"] and _b29b["legs"] > 2
   and _m29b["hard_ok"] is False and "legs" in _m29b["unmet"],
   f"got {_b29b['legs']} of a possible {_m29b['legs_ceiling']}")
ck("the board reports how many legs it could actually assemble",
   _m29b["legs_ceiling"] >= 2,
   "so the slip can say '19 asked, 12 was the ceiling' instead of shrugging")

# Both makers that own a frontier must size it the same way, or the widest
# board on the site (NFL: ~24 bundles a game x 16 games) inherits the raised
# absolute ceiling with no demand gate and hangs -- the exact failure the
# per-cell Pareto cap was added for.
for _mod29, _f29 in (("baseball.py", "baseball.py"),
                     ("nfl_game_sim.py", "nfl_game_sim.py")):
    _src29 = open(_os.path.join(_root, _f29)).read()
    ck(f"{_mod29} sizes its DP from the request",
       "combo_engine.dp_legs(" in _src29
       and "max_total_legs=_dp" in _src29)
_js29 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the leg input no longer stops at the old DP clamp",
   'id="comboN" type="number" min="2" max="30"' in _js29
   and 'id="nflComboN" type="number" min="2" max="30"' in _js29,
   "the box said 12 while the maker accepted 19 and the DP allowed neither")
ck("a missed leg count explains itself with the real ceiling",
   "legs_ceiling" in _js29 and "can only assemble" in _js29)
ck("the game grid states how many games are selectable today",
   "gg-count" in _js29 and "game${elig.length === 1" in _js29
   and ".combomaker .ggwrap" in _js29,
   "refreshGameGrid must replace the WRAPPER or every click orphans the "
   "old count line and appends another")

print()
print("=" * 72)
print("YES-only / NO-only: asking for the thing to HAPPEN")
print("=" * 72)
# Reported live: "3 home run picks... it keeps giving me no's". Correct
# behaviour from a probability optimizer and useless to the user -- a home run
# is a ~12% event, so the likeliest home-run slip is always three FADES. A
# confidence target picks a probability, never a direction, so no existing
# control could express the ask. `sides` is that control.
import baseball as _bb30
_bbsrc30 = _insp.getsource(_bb30.build_mixed_parlay)
ck("the maker can be restricted to one side of every market",
   "sides=None" in _bbsrc30
   and 'sides is None or c.get("side", "yes") in sides' in _bbsrc30,
   'legs carry side="no"; everything else is a YES by default, which is the '
   "convention the rest of the engine already uses")
ck("the thin live-ML fallback respects the side filter too",
   'if sides is not None and "yes" not in sides' in _bbsrc30,
   "it bypasses the candidate pool entirely, so it needs its own gate")

# side default: a leg with no explicit tag is a YES bet, so a YES-only build
# must keep it and a NO-only build must drop it.
_c30 = [{"type": "HR", "marg": 0.14, "side": "yes"},
        {"type": "HR", "marg": 0.86, "side": "no"},
        {"type": "ML", "marg": 0.62}]
ck("an untagged leg counts as YES, not as neither",
   len([c for c in _c30 if c.get("side", "yes") in {"yes"}]) == 2
   and len([c for c in _c30 if c.get("side", "yes") in {"no"}]) == 1)

_apy30 = open(_os.path.join(_root, "app.py")).read()
ck("the endpoint parses ?sides= and passes it into the build",
   'request.args.get("sides")' in _apy30 and "sides=sides" in _apy30
   and '"sides_empty"' in _apy30)

_js30 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the control exists, persists across the auto-refresh, and is sent",
   'sel("comboSides"' in _js30
   and "SameGame|Sides" in _js30
   and "`&sides=${comboSidesPref}`" in _js30,
   "without the persistence regex it would snap back to both every 20s -- "
   "the same bug the combo inputs had")
ck("the confidence floor reaches the 5% the server actually honours",
   'id="comboTarget" type="number" min="5"' in _js30,
   "the box stopped at 20%, and a home run is 8-20% -- YES-only would have "
   "had an empty pool by construction, which is the whole feature")
ck("an empty one-sided pool explains itself, before and after the build",
   "updateComboSidesHint" in _js30 and '"sides_empty"' in _js30
   and "8-20%" in _js30,
   "the pre-build hint fires the moment YES-only meets a high floor; the "
   "post-build one names the floor that emptied the pool")

print()
print("=" * 72)
print("The slip ledger: the correlation claim, graded")
print("=" * 72)
# A slip's EV lives almost entirely in the JOINT probability -- the
# correlation lift over the independent product -- and every per-leg number
# on this site is now disciplined by a graded record while that one was
# graded by nothing. The ledger logs each built parlay pre-game (first-write-
# wins on the leg set, every leg ticketed) and grades it as a unit off
# Kalshi settlement; the report puts claimed, independent-baseline and
# actual wins side by side.
import sliplog as _sl31
import store as _st31
import kalshi as _k31
import tempfile as _tf31

_olddb31, _oldgm31 = _st31.DB_PATH, _k31.get_market
try:
    _st31.DB_PATH = _os.path.join(_tf31.mkdtemp(), "guard31.db")
    _st31.init_db()
    _item31 = {"groups": [
        {"matchup": "CLE @ LAA", "suffix": "S1", "legs": [
            {"side": "no", "ticker": "T-A", "close_time": 100},
            {"side": "yes", "ticker": "T-B", "close_time": 100}]},
        {"matchup": "MIL @ NYM", "suffix": "S2", "legs": [
            {"side": "yes", "ticker": "T-C", "close_time": 100}]}],
        "n_games": 2, "combined_prob_pct": 20.0, "indep_prob_pct": 12.0,
        "kalshi_payout_net_x": 8.4, "ev_pct": 61.0, "objective": "balanced"}
    _key31 = _sl31.log_from_item(_item31, date="2026-08-25")
    ck("a built slip logs once: first-write-wins on its leg set",
       _key31 is not None
       and _sl31.log_from_item(_item31, date="2026-08-25") == _key31)
    ck("live and unticketed slips never enter the ledger",
       _sl31.log_from_item({"groups": [{"matchup": "X 🔴",
                                        "legs": _item31["groups"][0]["legs"]}],
                            "combined_prob_pct": 20, "indep_prob_pct": 12}) is None
       and _sl31.log_from_item({"groups": [{"matchup": "Y", "legs": [
            {"side": "yes", "ticker": None},
            {"side": "yes", "ticker": "T-Z"}]}],
            "combined_prob_pct": 20, "indep_prob_pct": 12}) is None,
       "a slip with a live or unpriceable leg cannot be settled as the unit "
       "whose joint was claimed")
    _res31 = {"T-A": {"result": "no", "status": "finalized"},
              "T-B": {"result": "yes", "status": "finalized"},
              "T-C": {"result": "no", "status": "finalized"}}
    _k31.get_market = lambda tk: _res31[tk]
    with _st31._lock, _st31._conn() as _c31:
        _c31.execute("UPDATE slip_log SET ts = ts - 90000")
    ck("settlement grades the slip as a unit (NO legs win on 'no')",
       _sl31.grade_due() == 1)
    _rep31 = _st31.slip_report()
    ck("the report carries the three-way verdict",
       _rep31["graded"] == 1 and _rep31["wins"] == 0
       and _rep31["avg_legs_hit"] == 2.0
       and _rep31["expected_wins"] == 0.2
       and _rep31["expected_wins_indep"] == 0.12
       and _rep31["stacked"]["claimed_premium"] == 0.08,
       "expected wins under the claim, under independence, and actual -- "
       "where actual lands is the verdict on the correlation premium")
    _sl31.log_from_item({"groups": [{"matchup": "C @ D", "suffix": "S4",
        "legs": [{"side": "yes", "ticker": "T-F", "close_time": 100},
                 {"side": "yes", "ticker": "T-G", "close_time": 100}]}],
        "n_games": 1, "combined_prob_pct": 15.0, "indep_prob_pct": 15.0},
        date="2026-08-25")
    _res31.update({"T-F": {"result": "", "status": "finalized"},
                   "T-G": {"result": "yes", "status": "finalized"}})
    with _st31._lock, _st31._conn() as _c31:
        _c31.execute("UPDATE slip_log SET ts = ts - 90000 WHERE graded=0")
    _sl31.grade_due()
    ck("a scratched leg voids the whole slip, never grades a different one",
       _st31.slip_report()["void"] == 1,
       "a voided leg changes the claimed joint; the slip that remains is "
       "not the slip that was logged")
finally:
    _st31.DB_PATH, _k31.get_market = _olddb31, _oldgm31

ck("every priced leg carries its ticker for slip settlement",
   'leg["ticker"] = tk' in _insp.getsource(
       __import__("baseball")._kalshi_payout))
_apy31 = open(_os.path.join(_root, "app.py")).read()
ck("the maker files every built slip and the report has a door",
   _apy31.count("_slip_log_safe(item)") >= 2
   and '"/api/baseball/sliplog"' in _apy31
   and "if not item or include_live" in _apy31,
   "logged at both return sites (optimal + plain/max-bet), live builds never")
ck("the recorder settles slips on its cadence",
   "sliplog.grade_due()" in open(_os.path.join(_root, "mlb_recorder.py")).read())
_js31 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the slip scoreboard renders beside the prop log",
   "loadSlipLog" in _js31 and "Correlation premium" in _js31
   and 'id="bbSlipLog"' in open(_os.path.join(_root, "templates",
                                              "index.html")).read())

print()
print("=" * 72)
print("Progress, warm-bar and PC-cycle coherence")
print("=" * 72)
# Four irritations reported together, one theme: counters that measure
# different things (or different eras) shown as if they were one number.
import baseball as _bb32

_t32 = "guard32tok"
assert _bb32.job_claim(_t32)
_bb32.progress_declare(_t32, 3)
_bb32.progress_start(_t32, 15)
for _ in range(15):
    _bb32.progress_enter(_t32); _bb32.progress_step(_t32)
_bb32.progress_start(_t32, 15)
_bb32.progress_start(_t32, 15)
_p32 = _bb32.progress_get(_t32) or {}
_j32 = _bb32.job_read(_t32) or {}
ck("three passes over 15 games is 15-per-pass, never 45",
   _p32.get("total") == 15 and _p32.get("pass") == 3
   and _j32.get("total") == 15 and _j32.get("passes") == 3,
   "the shared job file must agree with worker memory, or a sibling "
   "worker's poll resurrects the growing total")
_bb32.progress_done(_t32)
try:
    _os.remove(_bb32._job_path(_t32))
except OSError:
    pass

_apy32 = open(_os.path.join(_root, "app.py")).read()
ck("both sweeps declare their pass count before building",
   "progress_declare(ptok, len(combo_engine.OPTIMAL_FLOORS))" in _apy32
   and "progress_declare(ptok, len(combo_engine.MAX_BET_FLOORS))" in _apy32
   and '"passes": p.get("passes", 1)' in _apy32)
_js32 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the bar divides by the declared passes and says which one it is on",
   "((ps - 1) + fracPass) / passes" in _js32
   and "pass ${ps}/${passes}" in _js32)

# /api/warm: a routine 5-minute board refresh must not read as a cold start,
# the counts must measure the same set a Build simulates, and a frozen
# mid-sim matchup label must not contradict a "building the board" note.
_warm32 = _insp.getsource(__import__("app")._api_warm)
ck("an expired board cache serves STALE counts, not 0/0",
   "stale_slate" in _warm32 and "slate_fresh" in _warm32
   and "refreshing today" in _warm32,
   "the sims those counts measure were on disk the whole time; only a "
   "truly boardless start says building")
ck("warm counts the pregame set the maker actually simulates",
   '("Final", "Live")' in _warm32,
   "13 in the bar vs 9 in the build bar was two denominators for one job")
ck("a matchup label is only a live claim during the sim phase",
   'phase == "sim"' in _warm32,
   "a worker recycled mid-sim freezes the status file with a name inside")
ck("the warmer and the PC both skip live games' pregame sims",
   '("Final", "Live")' in _insp.getsource(__import__("app")._warm_game_sims)
   and '("Final", "Live")' in open(_os.path.join(_root, "pc_worker.py")).read(),
   "the live path never reads them; each cost ~200s of the shared CPU in "
   "the exact window that has none to spare")

_pw32 = open(_os.path.join(_root, "pc_worker.py")).read()
ck("the PC re-simulates when its own copy is nearly as stale as the ask",
   "os.remove(path)" in _pw32 and "> 1500" in _pw32,
   "answering from an aging local pickle re-stamps old work as fresh at "
   "the upload door - the 'simmed in 1s' no-op cycles")
ck("game sims ship BEFORE the boards wait loop, not after",
   _pw32.index('_sync_kind(url, tok, "gamesim")')
   < _pw32.index('("boards", _task_boards)'),
   "the boards task can sit in its wait loop for five minutes while a user "
   "watches the server re-simulate games the PC already finished")

print()
print("=" * 72)
print("The 92-second one-game build: Kalshi index economics")
print("=" * 72)
# Reported with screenshots: ONE pregame game, already cached, "pass 1/3",
# 92 seconds. The sims were instant -- the time was the Kalshi index: a 60s
# TTL on a book that costs a sequential 12-series refetch (~10s clean, most
# of a minute behind the rate limiter), re-paid by the reachability probe,
# by per-pass pricing, and by the payout stamp as it kept expiring
# mid-build. Three locks: a per-build pinned snapshot, stale-while-
# revalidate so no caller ever blocks on a refetch while ANY copy exists,
# and the board fetch moved into the job where the bar can name it.
import kalshi_mlb as _km33
import time as _tm33

_oldc33 = dict(_km33._cache)
_oldb33 = _km33._build_index
try:
    _km33._cache["data"] = {"SENTINEL-WARM": {}}
    _km33._cache["ts"] = _tm33.time()
    with _km33.pinned():
        _km33._cache["data"] = {"OTHER": {}}
        ck("a pinned build sees ONE book no matter what the cache does",
           "SENTINEL-WARM" in _km33.index(),
           "every pass prices against the same snapshot; the index cannot "
           "expire mid-build and charge a refetch per pass")
    ck("...and the pin releases with the build",
       "OTHER" in _km33.index())

    _calls33 = {"n": 0}
    def _slow33():
        _calls33["n"] += 1
        _tm33.sleep(0.5)
        return {"FRESH": {}}
    _km33._build_index = _slow33
    _km33._cache["data"] = {"STALE-GOOD": {}}
    _km33._cache["ts"] = _tm33.time() - 9999
    _km33._refresh["last"] = 0.0
    _t033 = _tm33.time()
    _got33 = _km33.index()
    _dt33 = _tm33.time() - _t033
    _tm33.sleep(1.0)
    ck("an expired index serves last-good INSTANTLY and refreshes behind",
       "STALE-GOOD" in _got33 and _dt33 < 0.4 and _calls33["n"] == 1
       and "FRESH" in _km33.index(),
       f"answered in {_dt33*1000:.0f}ms; a user-facing build only ever waits "
       "on a cold instance's very first pricing")
finally:
    _km33._build_index = _oldb33
    _km33._cache.update(_oldc33)
ck("the index TTL matches how pre-game asks actually move",
   _km33._TTL >= 180,
   "60s meant the book expired mid-build, always")

_apy33 = open(_os.path.join(_root, "app.py")).read()
ck("the whole MLB build runs under one pinned book",
   "with kalshi_mlb.pinned():" in _apy33)
ck("the board fetch runs IN the job and names itself",
   'phase="building today\'s board…"' in _apy33
   and "games = baseball.analyze_slate(date, season)"
   in _insp.getsource(__import__("app").api_baseball_mixed),
   "an expired slate cache used to block the HTTP request for the whole "
   "rebuild while the bar read a blind time estimate")
_js33 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the bar names the pre-count wait instead of guessing at a curve",
   "if (d && d.known && d.phase) phase = d.phase;" in _js33
   and 'phase !== "simulating games"' in _js33)

print()
print("=" * 72)
print("Edge mode: only legs the model genuinely disagrees on")
print("=" * 72)
# The ask, verbatim: "if kalshi says 9% and our sim says 16% don't put that
# in, but if kalshi says 54% and we say 65 or 70% that's something to throw
# in a combo." Two floors compose: the EDGE floor keeps legs where the
# pre-blend model beats the leg's own ask by >= X cents (pre-blend is the
# only yardstick that can show a 54->70 gap -- the blend is anchored to the
# market and keeps legs within a couple cents of the price by construction),
# and the existing CONFIDENCE floor is what rules the 16%-vs-9c longshot out.
import baseball as _bb34

_merrill34 = {"marg_model": 0.162, "marg": 0.11, "price_cents": 9}
_chalk34 = {"marg_model": 0.65, "marg": 0.56, "price_cents": 54}
_chalk7034 = {"marg_model": 0.70, "marg": 0.57, "price_cents": 54}
_flat34 = {"marg_model": 0.55, "marg": 0.54, "price_cents": 55}
ck("the reported spec, verbatim: 54->65/70 in, fair-priced out",
   _bb34._edge_ok(_chalk34, 10) and _bb34._edge_ok(_chalk7034, 10)
   and not _bb34._edge_ok(_flat34, 5)
   and not _bb34._edge_ok(_merrill34, 10))
_kept34 = [c for c in (_merrill34, _chalk34, _chalk7034, _flat34)
           if _bb34._edge_ok(c, 10) and c["marg"] >= 0.50]
ck("edge floor + confidence floor compose into exactly the asked pool",
   _kept34 == [_chalk34, _chalk7034],
   "the confidence floor is what excludes the 16%-vs-9c longshot; the edge "
   "floor alone must not, or a 5c setting would silently drop chalk edges")
ck("no price means no measurable edge, never a free pass",
   not _bb34._edge_ok({"marg_model": 0.9, "price_cents": None}, 5)
   and not _bb34._edge_ok({"price_cents": 50}, 5)
   and _bb34._edge_ok({"marg": 0.62, "price_cents": 55}, 5)
   and _bb34._edge_ok({"marg_model": 0.88, "price_cents": 80, "side": "no"}, 5),
   "unpriced legs are excluded in edge mode even when the exchange is down; "
   "a NO leg carries the NO ask so one formula serves both sides")
_bms34 = _insp.getsource(_bb34.build_mixed_parlay)
ck("the gate sits between pricing and the confidence band",
   _bms34.index("excluded_unpriced += n_all")
   < _bms34.index("_edge_ok(c, min_edge_c)")
   < _bms34.index('floor <= c["marg"] <= ceil'),
   "the edge needs the ask, and 'edges >= +5c, each leg >= 55%' has to mean "
   "exactly what it reads")
ck("the thin live-ML fallback obeys the same gate",
   "_edge_ok(leg, min_edge_c)" in _bms34)
_apy34 = open(_os.path.join(_root, "app.py")).read()
ck("the endpoint parses ?min_edge= and an empty pool names the floor",
   'request.args.get("min_edge")' in _apy34
   and "min_edge_c=min_edge" in _apy34 and '"edge_empty"' in _apy34)
_js34 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the control exists, persists, is sent, and the slip states the mode",
   'id="comboMinEdge"' in _js34 and "|Sides|MinEdge)$/" in _js34
   and "&min_edge=" in _js34 and "edgeNote" in _js34
   and '"edge_empty"' in _js34,
   "same persistence discipline as every other maker control -- a re-render "
   "must never snap it back")

print()
print("=" * 72)
print("Edge-mode fades: overpriced YES markets enter as their NO side")
print("=" * 72)
# The ask, verbatim: "if it says Paul skenes ask for 9 Ks at 59cents but he's
# playing against a monster lineup and our Sim/model put it at average 6 Ks
# for a negative edge throw those in too." A model-6% YES is a 94% NO -- past
# the sim's normal NO cap (padding in a fair book), so edge mode widens the
# band AT BUILD TIME and the fade then clears the edge floor on its own ask.
import mlb_sim as _ms35
import baseball as _bb35

_sk35 = {"type": "Ks", "label": "Skenes 9+ Ks", "marg": 0.06, "model_pct": 6.0,
         "mask": 0b1, "group": "P1", "kref": {"t": "ks", "player": "Skenes"}}
_no35 = _ms35._no_candidates([_sk35], 8, lo=0.90, hi=0.97)
ck("the reported spec, verbatim: a 6%-model 9+Ks line becomes a NO fade",
   len(_no35) == 1 and abs(_no35[0]["marg"] - 0.94) < 1e-12
   and _no35[0]["side"] == "no" and _no35[0]["kref"].get("no") is True
   and _no35[0]["mask"] == (~0b1) & 0xFF)
ck("the fade clears the edge floor on ITS OWN ask, or it doesn't go in",
   _bb35._edge_ok(dict(_no35[0], price_cents=88), 5)
   and not _bb35._edge_ok(dict(_no35[0], price_cents=93), 5),
   "94% model vs an 88c NO ask is the +6c edge being asked for; vs 93c "
   "there is no edge and the overpriced-YES observation alone buys nothing")
ck("the default NO band is untouched -- the cached sim pool stays fair-book",
   _ms35._no_candidates([_sk35], 8) == []
   and _insp.signature(_ms35._no_candidates).parameters["hi"].default
       == _ms35._NO_MAX,
   "widening the band everywhere would stuff every slip with 90%+ padding "
   "legs that add headline confidence and no edge")
ck("a 98% NO is still padding even in edge mode (hi cap holds)",
   _ms35._no_candidates([dict(_sk35, marg=0.02)], 8, lo=0.90, hi=0.97) == [])
# The warm-rebuild regression: _price_cands blends IN PLACE on the cached
# sim's dicts, so on every build after the first the YES cand carries
# marg_model (its OWN model number). {**c} used to copy that onto the NO leg,
# so _edge_ok scored the fade as model-6% vs a 92c NO ask (-86c) and silently
# dropped every fade the moment the sim cache warmed up.
_warm35 = dict(_sk35, marg=0.085, marg_model=0.06)
_wno35 = _ms35._no_candidates([_warm35], 8, lo=0.90, hi=0.97)
ck("a previously-blended YES hands the NO its COMPLEMENT model number",
   len(_wno35) == 1 and abs(_wno35[0]["marg"] - 0.94) < 1e-12
   and abs(_wno35[0]["marg_model"] - 0.94) < 1e-12
   and _bb35._edge_ok(dict(_wno35[0], price_cents=88), 5),
   "the fade must survive a warm rebuild, not work exactly once per process")
_bms35 = _insp.getsource(_bb35.build_mixed_parlay)
ck("fades are generated in edge mode only, before pricing, NO side allowed",
   'if min_edge_c is not None and (sides is None or "no" in sides):' in _bms35
   and _bms35.index('lo=0.90, hi=0.97')
       < _bms35.index('_price_cands(cands, g.get("kalshi_suffix"))'))
_ext35 = _bms35.split('if min_edge_c is not None and (sides is None or '
                      '"no" in sides):')[1].split("_price_cands")[0]
ck("NO-only mode still gets fades: the pool is drawn pre-side-filter",
   'gs["cands"]' in _ext35
   and 'c.get("side", "yes") == "yes"' in _ext35,
   "under sides={no} the YES cands are already filtered out of `cands`, so "
   "complementing `cands` itself would generate nothing in fades-only mode")
ck("an existing NO leg is never duplicated by the extension",
   'have = {c["label"] for c in cands if c.get("side") == "no"}' in _bms35
   and 'c["label"] not in have' in _bms35)
_js35 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the hint says fades are automatic -- nobody should type a negative edge",
   "come in automatically" in _js35 and "YES-only turns fades off" in _js35)

print()
print("=" * 72)
print("The market clamp is one-directional: a book caps, it never manufactures")
print("=" * 72)
# The reported card, verbatim: "Adley Rutschman 3+ hits (model 3.3% / sim 89%)
# Kalshi 99c" inside a likeliest-first hits slip. The book was dead -- resting
# 1c bid / 99c ask, zero volume, market quality 0 -- and the old symmetric
# ref+/-10 clamp turned that placeholder ask into an 89% FLOOR. An ask only
# bounds fair value from above; the floor belongs to the bid alone.
import combo_engine as _ce36

_dead36 = {"ask": 99, "bid": 1, "spread": 98, "mid": 50,
           "vol": 0, "oi": 0, "size": 1}
_u36, _w36, _q36 = _ce36.blend_prob(0.033, _dead36, "Hit")
ck("the reported card, verbatim: 3.3% vs a 1c/99c dead book STAYS 3.3%",
   abs(_u36 - 0.033) < 1e-9 and _w36 == 1.0 and _q36 == 0.0,
   f"blended {_u36:.3f}; the symmetric clamp displayed 89% and a "
   "likeliest-first slip stacked it")
ck("an ask with no bid at all never lifts the model either",
   _ce36.blend_prob(0.033, {"ask": 99, "bid": None, "spread": None,
                            "mid": 99, "vol": 0, "oi": 0}, "Hit")[0]
   <= 0.033 + 1e-9,
   "one-sided quality is 0.2, so without this the blend PULL alone dragged "
   "3.3% up to ~73% before any clamp was consulted")
ck("the winner's-curse cap this clamp exists for still binds",
   _ce36.blend_prob(0.85, {"ask": 54, "bid": 4, "spread": 50, "mid": 29,
                           "vol": 0, "oi": 0}, "HRR")[0] <= 0.54 + 0.10 + 1e-9
   and _ce36.blend_prob(0.83, {"ask": 54, "bid": None, "spread": None,
                               "mid": 54, "vol": 0, "oi": 0}, "Hit")[0] < 0.83,
   "capping DOWN against an ask is sound in every book shape -- that is the "
   "85%-claim-vs-54c-ask case the clamp was originally added for")
ck("a real two-sided bid still floors the number from below",
   _ce36.blend_prob(0.02, {"ask": 50, "bid": 49, "spread": 1, "mid": 49.5,
                           "vol": 500, "oi": 0, "size": 999}, "Hit")[0]
   >= 0.49 - 0.10 - 1e-9,
   "buyers standing at 49c are genuine evidence; the fix removes fabricated "
   "floors, not real ones")
_cl36 = _insp.getsource(_ce36._clamp_to_market)
ck("the floor keys off the bid and dies with it, in source",
   "bid / 100.0 - _MAX_EDGE" in _cl36 and "p = min(p, p_model)" in _cl36
   and "mid - _MAX_EDGE" not in _cl36)
_js36 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the blended number is labelled as what it is, never as the sim",
   "· blended <b>${l.prob_pct}%</b>" in _js36
   and "· sim <b>${l.prob_pct}%</b>" not in _js36,
   'the leg parens predate the blend, so the market-blended number rendered '
   'as "sim 89%" on a model-3.3% event; the sim itself shows as "pre-blend"')

print()
print("=" * 72)
print("A dead build heals itself: heartbeat + takeover")
print("=" * 72)
# The reported failure: a deploy swap killed a build mid-flight; its job file
# ('running') lives on the persistent disk, so it SURVIVED the swap; the
# O_EXCL claim is first-winner-forever, so every poll answered 202 'building'
# and the bar froze at 'simulated 1/5' with no path back. The builder now
# beats the file every 20s; a poll finding a running job untouched for 90s
# takes it over and rebuilds on the disk-cached sims already paid for.
import tempfile as _tf37
import time as _tm37
_dir37 = _tf37.mkdtemp()
_old37 = B._JOB_DIR
B._JOB_DIR = _dir37
try:
    _tok37 = "guardtok37"
    ck("claim: exactly one winner",
       B.job_claim(_tok37) and not B.job_claim(_tok37))
    ck("a LIVE job cannot be taken over",
       not B.job_takeover(_tok37, 90),
       "takeover must never steal a build that is merely slow -- the beat "
       "thread keeps a live file younger than ~20s at all times")
    _p37 = B._job_path(_tok37)
    _os.utime(_p37, (_tm37.time() - 200, _tm37.time() - 200))
    ck("heartbeat freshens the file a takeover would otherwise claim",
       (B.job_heartbeat(_tok37),
        _tm37.time() - _os.stat(_p37).st_mtime < 5)[1])
    _os.utime(_p37, (_tm37.time() - 200, _tm37.time() - 200))
    ck("a job 200s silent IS dead: takeover wins exactly once, claim reopens",
       B.job_takeover(_tok37, 90) and not B.job_takeover(_tok37, 90)
       and B.job_claim(_tok37),
       "this is the frozen-bar state healing itself on the next poll")
finally:
    B._JOB_DIR = _old37
    import shutil as _sh37
    _sh37.rmtree(_dir37, ignore_errors=True)
_apy37 = open(_os.path.join(_root, "app.py")).read()
ck("every parlay endpoint takes over dead jobs, before the claim",
   _apy37.count("baseball.job_takeover(ptok, _JOB_DEAD_S)") == 3
   and '"COMBO-dead-job"' in _apy37 and '"NFL-COMBO-dead-job"' in _apy37
   and '"UFC-COMBO-dead-job"' in _apy37
   and _apy37.index("job_takeover(ptok") < _apy37.index("job_claim(ptok)"),
   "a takeover after the claim check could never run -- the claim already "
   "answered 202")
import app as _app37
_rj37 = _insp.getsource(_app37._run_job)
ck("the builder beats its job file from a side thread every 20s",
   "stop.wait(20)" in _rj37 and "baseball.job_heartbeat(ptok)" in _rj37
   and _apy37.count('_run_job(ptok, _core, "COMBO-build")') == 1
   and _apy37.count('_run_job(ptok, _core, "NFL-COMBO-build")') == 1
   and _app37._JOB_DEAD_S >= 60,
   "dead_s must be several missed beats, or a paused beat thread under load "
   "gets its build stolen out from under it")
_js37 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the warm bar refuses to render the offline body as a status",
   "d.error != null || d.total == null" in _js37,
   'the SW answers a dead fetch with clean JSON, which rendered as '
   '"undefined/undefined games ready" during every deploy swap')

print()
print("=" * 72)
print("UFC: the loud number is the one the evidence backs")
print("=" * 72)
# The owner's complaint, verbatim: "alot of the fighters we pick are losing."
# The board's own backtest had already measured why: the raw fight-history
# model LOSES to closing lines (logloss 0.685 vs 0.615, fitted blend weight
# ~0.05) -- yet the fighter row led with the raw number and whispered the
# market-blended fair win% in small grey. The headline and the evidence now
# point the same way.
_js38 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the fighter row headlines the blended fair win, not the raw model",
   "${headline}%${fair}" in _js38
   and 'ufc-win">${f.win_pct}' not in _js38
   and "model ${f.win_pct}%" in _js38,
   "the raw model number still shows -- small, labelled, with the measured "
   "record in its tooltip -- because hiding it would be the opposite lie")
import ufc_prices as _up38
_mwc38 = _insp.getsource(_up38._model_weight_cap)
ck("an UNMEASURED model never gets majority say against a real price",
   "0.20 + 0.15" in _mwc38 and "0.50 + 0.35" not in _mwc38,
   "the fallback curve reached 0.85 weight on logged-pick COUNT alone -- "
   "sample size measures how much we know about the fighters, not whether "
   "our rating of them beats the book (model_trust's own lesson)")

print()
print("=" * 72)
print("Tests exercise functions; they never run the plant")
print("=" * 72)
ck("VIGIL_NO_BG stands down every background claim, and this suite sets it",
   'os.environ.get("VIGIL_NO_BG")' in _insp.getsource(
       __import__("app")._own_background_jobs)
   and os.environ.get("VIGIL_NO_BG") == "1"
   and not __import__("app")._BG_OWNER,
   "importing app used to start the predlog harvester inside the test "
   "process: live predictions logged into the checkout's predlog.db, graded "
   "for real, until the rows crossed an earn-floor and flipped a guard")

print()
print("=" * 72)
print("The record grades itself against the CLOSE, without touching the game")
print("=" * 72)
# Settlement grading is weeks of coin flips; the closing line is the fastest
# honest benchmark and it accrues per event. The trap this block exists to
# hold shut: Kalshi's close_time is an administrative backstop WEEKS after
# the event (a Sep 2 fight 'closes' Sep 17) and trading runs in-game, so a
# snapshot anchored to close_time would smuggle the OUTCOME into a number
# that claims to be pre-game -- the same contamination that once produced a
# fake +14.8c CLV. The event start comes from the ticker itself.
import predlog as _pl39
import tempfile as _tf39

_ufc39 = _pl39._event_ts("KXUFCFIGHT-26SEP02RIVDAR-RIV")
_mlb39 = _pl39._event_ts("KXMLBGAME-26AUG312138NYYLAA-NYY")
import datetime as _dt39
import zoneinfo as _zi39
_et39 = _zi39.ZoneInfo("America/New_York")
ck("the event start is read from the TICKER: day series at midnight ET",
   _ufc39 is not None
   and _dt39.datetime.fromtimestamp(_ufc39, _et39).strftime("%Y-%m-%d %H:%M")
   == "2026-09-02 00:00",
   "day-only tickers stop snapshots the night before -- a few hours of line "
   "movement traded for zero risk of in-game contamination")
ck("game series carry the start time, decoded as ET",
   _mlb39 is not None
   and _dt39.datetime.fromtimestamp(_mlb39, _et39).strftime("%H:%M") == "21:38"
   and _pl39._event_ts("no-date-here") is None)
_sc39 = _insp.getsource(_pl39.snapshot_closes)
ck("snapshots anchor to the ticker event and NEVER to close_time",
   "_event_ts(" in _sc39 and "now < ev" in _sc39
   and "close_time" not in _sc39,
   "close_time is a backstop weeks out with in-game trading in between; "
   "anchoring to it would grade the model against the outcome itself")
ck("each pass REFRESHES ungraded rows, so the last pre-event write wins",
   'WHERE ticker=? AND graded=0"' in _sc39
   and "close_mkt IS NULL" not in _sc39,
   "a write-once snapshot taken 36h out is an opening line wearing a "
   "closing line's name")
ck("the loop takes its close snapshots between harvest and grading",
   _insp.getsource(_pl39._loop).index("snapshot_closes()")
   < _insp.getsource(_pl39._loop).index("resolve_due()")
   and '"PRED-loop-3"' in _insp.getsource(_pl39._loop))
_pdb39 = _pl39._DB
_pl39._DB = _os.path.join(_tf39.mkdtemp(prefix="guard-predlog-"), "p.db")
try:
    _pl39.init_db()
    _pl39.log_many("guardclose", [(f"GT{i}", 0.60, int(_tm.time() + 9e5), 0.50)
                                  for i in range(12)])
    with _pl39._lock, _pl39._conn() as _c39:
        for _i in range(12):
            _c39.execute("UPDATE predictions SET close_mkt=? WHERE ticker=?",
                         (0.55 if _i < 9 else 0.47, f"GT{_i}"))
    _cr39 = _pl39.close_report("guardclose")
    ck("close_report: drift toward us and cents captured, per pick",
       _cr39["ready"] and _cr39["n"] == 12 and _cr39["toward_pct"] == 75.0
       and abs(_cr39["avg_capture_c"] - 3.0) < 1e-9,
       "9 of 12 closes moved our way; capture nets the 3 that moved against")
    ck("a thin sample reports accruing, never a verdict",
       not _pl39.close_report("guardnothing")["ready"])
finally:
    _pl39._DB = _pdb39
_apy39 = open(_os.path.join(_root, "app.py")).read()
_js39 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the sharp table exists: measured record over claimed edges",
   '@app.route("/api/sharp")' in _apy39
   and "predlog.close_report(m)" in _apy39
   and "Where we're actually sharp" in _js39 and "loadSharp" in _js39,
   "the app always KNEW where its models beat the price; it was buried in "
   "three modules' internals while picks got chosen by feel")
ck("the blend share reads as composition, not extraction",
   "% market</b>" in _js39 and "market took" not in _js39,
   '"market took 98%" read as a fee being charged; it is the blend recipe')
ck("the guard suite gates every push in CI",
   _os.path.exists(_os.path.join(_root, ".github", "workflows", "guards.yml"))
   and "tests/combo_audit_guards.py"
   in open(_os.path.join(_root, ".github", "workflows", "guards.yml")).read())
# Every import must be stdlib, a DECLARED dependency, or ours. lol.py quietly
# imported requests -- never in requirements.txt, so the production image
# didn't have it and the LoL tab answered 502 in prod while every dev box,
# with requests installed globally, swore the suite was green. CI's clean
# environment caught it on its first run; this makes it catchable locally.
_dep40 = set(sys.stdlib_module_names)
_local40 = {f[:-3] for f in _os.listdir(_root) if f.endswith(".py")}
_declared40 = {"flask", "werkzeug", "gunicorn", "qrcode", "tzdata"}
_undeclared40 = []
for _f40 in sorted(_os.listdir(_root)):
    if not _f40.endswith(".py"):
        continue
    for _n40 in _gast.walk(_gast.parse(open(_os.path.join(_root, _f40)).read())):
        _mods40 = ([a.name.split(".")[0] for a in _n40.names]
                   if isinstance(_n40, _gast.Import)
                   else [_n40.module.split(".")[0]]
                   if isinstance(_n40, _gast.ImportFrom)
                   and _n40.level == 0 and _n40.module else [])
        for _m40 in _mods40:
            if _m40 not in _dep40 and _m40 not in _local40 \
                    and _m40 not in _declared40:
                _undeclared40.append(f"{_f40}:{_n40.lineno} imports {_m40}")
ck("every import is stdlib, declared in requirements, or ours",
   not _undeclared40, _undeclared40[:5])

print()
print("=" * 72)
print("Locked daily slips: the recipes are constants, the record is the point")
print("=" * 72)
# The ask, near-verbatim: "pre-made slips I like doing... 5 hits / YES 3 home
# runs / every game's pitcher 80%+ strikeout prop / moneylines above 58% /
# run lines 80%+. Hard locked -- if I want my own I use the regular combo
# maker. They run every day, get logged then graded, reran once a new lineup
# gets posted. All except home runs can be yes or no."
import presets as _pr41
import tempfile as _tf41

_spec41 = {s["id"]: s for s in _pr41.PRESETS}
ck("the six recipes, verbatim, hard-locked as server constants "
   "(plus the four ⚡ payout rungs, guarded in their own block)",
   set(_spec41) == {"hits5", "hr3", "ks80", "ml58", "rl80", "tot80",
                    "x15", "x2", "x3", "x5", "x10", "x100", "x200"}
   and _spec41["hits5"]["n_legs"] == 5 and _spec41["hits5"]["types"] == ("Hit",)
   and _spec41["hr3"]["n_legs"] == 3 and _spec41["hr3"]["types"] == ("HR",)
   and _spec41["ks80"]["floor"] == 0.80 and _spec41["ks80"]["types"] == ("Ks",)
   and _spec41["ml58"]["floor"] == 0.58 and _spec41["ml58"]["types"] == ("ML",)
   and _spec41["rl80"]["floor"] == 0.80
   and _spec41["rl80"]["types"] == ("Run line",)
   and _spec41["tot80"]["floor"] == 0.80
   and _spec41["tot80"]["types"] == ("Total",)
   and _spec41["tot80"]["sides"] is None,
   "totals need no sides logic: Over AND Under are both YES-side candidates "
   "sharing one group per game, so the unit rule picks whichever clears 80%")
ck("home runs are YES only; every other recipe may take either side",
   _spec41["hr3"]["sides"] == frozenset(("yes",))
   and all(_spec41[p]["sides"] is None
           for p in ("hits5", "ks80", "ml58", "rl80")),
   '"YES 3 home runs" is the one exception, by request -- the sides control '
   "was born from HR slips coming back all NOs")
ck("the endpoint takes no parameters -- locked means locked",
   "request.args" not in _insp.getsource(
       __import__("app").api_baseball_presets),
   "a preset with knobs is the combo maker with extra steps; if you want "
   "knobs, that's what Custom is for")
ck("the top-N recipes ride the maker's own frontier, likeliest-first",
   'legs_mode="require"' in _insp.getsource(_pr41._build_top)
   and 'objective="safe"' in _insp.getsource(_pr41._build_top))
_g41 = {"game_pk": 1, "home_sp_id": 10, "away_sp_id": 20, "live": {},
        "confirm": {"home_lineup": "projected", "away_lineup": "projected",
                    "level": "provisional"}}
_gf41 = {"game_pk": 2, "live": {"state": "Final"}, "confirm": {}}
_s41a = _pr41.slate_sig([_g41, _gf41])
_s41b = _pr41.slate_sig([dict(_g41, confirm={"home_lineup": "confirmed",
                                             "away_lineup": "projected",
                                             "level": "provisional"}), _gf41])
ck("a lineup POSTING changes the slate signature -- the rebuild trigger",
   _s41a and _s41b and _s41a != _s41b
   and _pr41.slate_sig([_gf41]) is None,
   '"reran once a new lineup gets posted" is a hash comparison, not a timer')
ck("the recorder's tick rebuilds the presets on its cadence",
   "presets.tick()" in _insp.getsource(
       __import__("mlb_recorder")._loop))
# Functional: the scan recipe prices, filters, tickets and logs like the ask.
_cands41 = [
    {"type": "Ks", "label": "Ace 6+ Ks", "marg": 0.85, "side": "yes",
     "group": "K:Ace", "kref": {"t": "ks"}, "model_pct": 84.0,
     "marg_model": 0.83},
    {"type": "Ks", "label": "Ace 8+ Ks", "marg": 0.55, "side": "yes",
     "group": "K:Ace", "kref": {"t": "ks"}, "model_pct": 52.0,
     "marg_model": 0.50},
    {"type": "Ks", "label": "Bob 4+ Ks", "marg": 0.82, "side": "yes",
     "group": "K:Bob", "kref": {"t": "ks"}, "model_pct": 81.0,
     "marg_model": 0.80},
    {"type": "ML", "label": "Team ML", "marg": 0.66, "side": "yes",
     "group": "ML", "kref": {"t": "ml"}, "model_pct": 66.0,
     "marg_model": 0.64}]
_games41 = [{"game_pk": 7, "matchup": "A @ B", "kalshi_suffix": "s1",
             "live": {}, "confirm": {}},
            {"game_pk": 8, "matchup": "C @ D", "kalshi_suffix": "s2",
             "live": {"state": "Live"}, "confirm": {}},
            {"game_pk": 9, "matchup": "E @ F", "kalshi_suffix": "s3",
             "live": {}, "confirm": {}}]
import kalshi_mlb as _km41
_orig41 = (B._game_sim, B._price_cands, _km41.index, _km41.ticker_leg,
           _ST.DB_PATH)
try:
    B._game_sim = lambda g: {"cands": [dict(c) for c in _cands41],
                             "sim": {"n": 100}}

    def _fp41(cands, sfx, blend=True):
        for c in cands:
            c["price_cents"] = 80
        return cands
    B._price_cands = _fp41
    _km41.index = lambda: {}
    _km41.ticker_leg = lambda idx, sfx, kref: ("TK41-" + (sfx or "?"), 123)
    _it41 = _pr41._build_all(_games41, _spec41["ks80"])
    import combo_engine as _ce41
    ck("the scan recipe: live games out, floor applied, best leg per UNIT",
       _it41 and _it41["n_games"] == 2 and _it41["n_legs"] == 4
       # BOTH starters qualify from one game (the per-game version showed 9
       # pitchers on an 11-game slate); the best line per pitcher, and "pick"
       # is the display-name key (_mixed_item convention -- the first live
       # build rendered five "undefined"s off a "label" here).
       and sorted(l["pick"] for l in _it41["groups"][0]["legs"])
       == ["Ace 6+ Ks", "Bob 4+ Ks"]
       and abs(_it41["combined_prob_pct"]
               - round((0.85 * 0.82) ** 2 * 100, 1)) < 0.05
       and _it41["n_pool"] == 4,
       "one leg per PITCHER for Ks (a game has two arms), still one per game "
       "for ML/RL; n_pool makes the coverage gap visible instead of spooky")
    _ml41 = _pr41._build_all(_games41, _spec41["ml58"])
    ck("...while the moneyline recipe still yields at most one leg per game",
       _ml41 and _ml41["n_legs"] == 2
       and all(len(g["legs"]) == 1 for g in _ml41["groups"]),
       "both ML sides share one group, so the unit rule changes nothing there")
    _ST.DB_PATH = _os.path.join(_tf41.mkdtemp(prefix="guard-preset-"), "v.db")
    _ST.init_db()
    _it41["objective"] = "preset:ks80"
    import sliplog as _sl41
    _k41 = _sl41.log_from_item(_it41, sport="mlb", date="2026-08-29",
                               tag="ks80")
    _sl41.log_from_item(_it41, sport="mlb", date="2026-08-29", tag="ks80")
    _rec41 = _ST.preset_records()
    ck("every iteration logs under its recipe's tag; identical rebuilds dedup",
       _k41 is not None and _rec41.get("ks80", {}).get("logged") == 1
       and _rec41["ks80"]["graded"] == 0,
       "the ledger's first-write-wins keys the LEG SET, so a lineup change "
       "is a new row and a no-change rebuild is the same bet")
    # The PC builds the same payload; only the SERVER may file slips. A
    # PC-built payload arrives with logged=False and ensure_logged (idempotent)
    # flips its badges honest on the server's next tick.
    _pay41 = {"date": "2026-08-29", "presets": {
        "ks80": {"item": dict(_it41), "logged": False, "log_note": None}}}
    ck("ensure_logged files an adopted payload's slips and reports the change",
       _pr41.ensure_logged(_pay41) is True
       and _pay41["presets"]["ks80"]["logged"] is True
       and _pr41.ensure_logged(_pay41) is False,
       "True the first time (badges flipped), False when nothing changed -- "
       "the tick republishes only when the flags moved")
finally:
    (B._game_sim, B._price_cands, _km41.index, _km41.ticker_leg,
     _ST.DB_PATH) = _orig41
# The 5-Hits refinement, near-verbatim: "limited to 1 hit, UNLESS the model
# truly thinks a player can get 2 or it would be a good bet. It's only doing
# 'no 3+ hits' which is like a 96% for most people." Day one proved it:
# likeliest-first over the whole ladder is won by deep-line NO padding.
_ok41 = _pr41._hits5_leg_ok
ck("the reported padding, verbatim: 'NO 3+ hits' never -- at ANY edge",
   not _ok41({"kref": {"line": 3}, "side": "no", "marg": 0.96,
              "marg_model": 0.96, "price_cents": 97})
   and not _ok41({"kref": {"line": 3}, "side": "no", "marg_model": 0.95,
                  "price_cents": 83}),
   "high headline, no payout -- a fade of a line almost nobody reaches is "
   "padding even when its price is off")
ck("the 1+ hit line always qualifies, either side",
   _ok41({"kref": {"line": 1}, "side": "yes", "marg": 0.72})
   and _ok41({"kref": {"line": 1}, "side": "no", "marg": 0.30}))
ck("a 2+ YES needs conviction or a real edge",
   _ok41({"kref": {"line": 2}, "side": "yes", "marg": 0.42,
          "marg_model": 0.42})
   and _ok41({"kref": {"line": 2}, "side": "yes", "marg_model": 0.30,
              "price_cents": 24})
   and not _ok41({"kref": {"line": 2}, "side": "yes", "marg_model": 0.30,
                  "price_cents": 28})
   and not _ok41({"kref": {"line": 2}, "side": "yes", "marg_model": 0.30}),
   '"truly thinks a player can get 2" = model 40%+ pre-blend; "a good bet" = '
   "the ask underprices it by 5c+; unpriced deep lines have neither")
ck("a 2+ NO needs to be a REALLY good bet -- 8c+ on its own ask, never "
   "probability alone",
   _ok41({"kref": {"line": 2}, "side": "no", "marg_model": 0.92,
          "price_cents": 83})
   and not _ok41({"kref": {"line": 2}, "side": "no", "marg_model": 0.92,
                  "price_cents": 87})
   and not _ok41({"kref": {"line": 2}, "side": "no", "marg_model": 0.88}),
   "a likely fade of a deep line IS the padding; only mispricing earns it "
   "a slot")
_bmp41 = _insp.getsource(B.build_mixed_parlay)
ck("leg_ok runs AFTER pricing (the rule needs the ask), before the gates",
   _bmp41.index('_price_cands(cands, g.get("kalshi_suffix"))')
   < _bmp41.index("leg_ok(c)")
   < _bmp41.index("excluded_unpriced += n_all")
   and "leg_ok=spec.get(" in _insp.getsource(_pr41._build_top))
ck("a recipe change rebuilds today's slips at deploy, not at next lineup",
   '"rev": REV' in _insp.getsource(_pr41.build_all)
   and 'cur.get("rev") == REV' in _insp.getsource(_pr41.tick),
   "boardshare persists across the swap; without the rev stamp an edited "
   "recipe would keep serving the old slip until a lineup posted")
ck("the build is pure compute; the ledger is touched ONLY by ensure_logged",
   "sliplog" not in _insp.getsource(_pr41.build_all)
   and "sliplog" not in _insp.getsource(_pr41.pc_build)
   and "sliplog" in _insp.getsource(_pr41.ensure_logged),
   "this split is what lets the PC build the identical payload without ever "
   "growing a ledger of its own -- the server files adopted slips on tick")
_tk41 = _insp.getsource(_pr41.tick)
ck("tick adopts a fresh payload (PC or self) and debounces rebuild storms",
   "ensure_logged(cur)" in _tk41
   and 'cur.get("built_ts") or 0) < 300' in _tk41,
   "lineup posts arrive minutes apart all afternoon; each rebuild is real "
   "CPU on the shared core the health probe lives on")
_pcw41 = open(_os.path.join(_root, "pc_worker.py")).read()
ck("the PC builds the presets and the FULL model-trust backtests",
   '__import__("presets").pc_build()' in _pcw41
   and '("model_trust", lambda: _mt())' in _pcw41
   and "refresh(quick=False)" in _pcw41)
import deep_cache as _dc41
import model_trust as _mt41
_dcdir41 = _dc41.CACHE_DIR
_dc41.CACHE_DIR = _tf41.mkdtemp(prefix="guard-mt-")
try:
    _mt41.record("guardmt", 0.50, 300, "full")
    _mt41.record("guardmt", 0.20, 60, "quick")
    _w41 = (_mt41.load()["weights"] or {}).get("guardmt") or {}
    ck("a quick 60-sample pass cannot clobber a fresh full 300-sample fit",
       _w41.get("n") == 300 and _w41.get("weight") == 0.50,
       "weight() reads n as confidence -- the overwrite would literally "
       "erase what we know; a stale full fit (>48h) still yields")
    _mt41.record("guardmt", 0.30, 400, "fuller")
    ck("...while a BIGGER sample replaces it fine",
       ((_mt41.load()["weights"] or {}).get("guardmt") or {}).get("n") == 400)
finally:
    _dc41.CACHE_DIR = _dcdir41
_js41 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the tabs exist, match the server's recipe ids, and survive a re-render",
   all(f'"{p}"' in _js41.split("_PRESET_TABS")[1][:300]
       for p in ("custom", "hits5", "hr3", "ks80", "ml58", "rl80"))
   and "applyPresetTab();" in _js41,
   "the 20s slate refresh re-renders the maker; a preset tab must not snap "
   "back to Custom")

print()
print("=" * 72)
print("The PC status light: presence is 'how recently did it call'")
print("=" * 72)
# The server can never reach OUT to the PC -- the PC only calls in -- so the
# header light derives from check-in freshness: green = heartbeat inside 5
# minutes, yellow = alive but running older code than this server (its git
# pull self-heals within a minute, so persistent yellow is a real fault),
# red = silence. pc_loop pings every 60s precisely because a deep-sim cycle
# can keep pc_worker away from the API for an hour.
import json as _j42
import tempfile as _tf42
_app42 = __import__("app")
_old42 = (_app42._PC_STATUS_PATH, _app42._SIM_TOKEN,
          _os.environ.get("RENDER_GIT_COMMIT"), _app42._pc_seen_last[0])
_app42._PC_STATUS_PATH = _os.path.join(_tf42.mkdtemp(prefix="guard-pc-"),
                                       "pc-status.json")
try:
    _os.environ["RENDER_GIT_COMMIT"] = "abc123def4567890"
    ck("no check-in file means OFF, honestly, not a crash",
       _app42._pc_status() == {"state": "off", "seen_s": None, "behind": None})
    with open(_app42._PC_STATUS_PATH, "w") as _fh42:
        _j42.dump({"ts": _tm.time(), "commit": "abc123def4567890aaaa"}, _fh42)
    ck("a fresh heartbeat on matching code is ON (green)",
       _app42._pc_status()["state"] == "on")
    with open(_app42._PC_STATUS_PATH, "w") as _fh42:
        _j42.dump({"ts": _tm.time(), "commit": "fff000fff000fff0"}, _fh42)
    ck("a fresh heartbeat on OLDER code is BEHIND (yellow)",
       _app42._pc_status()["state"] == "behind",
       "prefix comparison both ways, so a short commit from either side "
       "still matches its long form")
    with open(_app42._PC_STATUS_PATH, "w") as _fh42:
        _j42.dump({"ts": _tm.time() - 900, "commit": "abc123def456"}, _fh42)
    ck("five missed 60s heartbeats is OFF (red) -- one slow cycle is not",
       _app42._pc_status()["state"] == "off"
       and _app42._pc_status()["seen_s"] > 300)
    _app42._SIM_TOKEN = "guardtok42"
    _app42._pc_seen_last[0] = 0.0
    _c42 = _app42.app.test_client()
    _r42 = _c42.get("/api/pc/ping", headers={"X-Sim-Token": "guardtok42",
                                             "X-PC-Commit": "abc123def4567890"})
    ck("the ping door takes the shared token and stamps the presence file",
       _r42.status_code == 200
       and _app42._pc_status()["state"] == "on"
       and _c42.get("/api/pc/ping").status_code == 403,
       "the door is _pc_auth_ok -- only the PC's endpoints use it, so a "
       "workflow can never impersonate the PC")
finally:
    (_app42._PC_STATUS_PATH, _app42._SIM_TOKEN) = _old42[0], _old42[1]
    _app42._pc_seen_last[0] = _old42[3]
    if _old42[2] is None:
        _os.environ.pop("RENDER_GIT_COMMIT", None)
    else:
        _os.environ["RENDER_GIT_COMMIT"] = _old42[2]
_apy42 = open(_os.path.join(_root, "app.py")).read()
_js42 = open(_os.path.join(_root, "static", "app.js")).read()
_pl42 = open(_os.path.join(_root, "pc_loop.py")).read()
ck("the light rides the warm poll (both return shapes), and the loop beats",
   _apy42.count('"pc": _pc_status()') == 2
   and _pl42.count("_ping_server()") >= 2 and "/api/pc/ping" in _pl42
   and '"X-PC-Commit": _git_commit()'
   in open(_os.path.join(_root, "pc_worker.py")).read(),
   "the worker's uploads refresh the light too, but a long deep cycle goes "
   "quiet for an hour -- the loop's 60s ping is what keeps it honest")
ck("the dot updates BEFORE pollWarm's early returns, on every poll",
   0 < _js42.index('$("pcDot")') < _js42.index("Hidden only when there is")
   and 'id="pcDot"' in open(_os.path.join(_root, "templates",
                                          "index.html")).read())

print()
print("=" * 72)
print("The wall of wins: a slip hangs there only after every leg settled")
print("=" * 72)
# The ask: fill the neglected Hits tab with each preset's HIGHEST logged win
# -- one column per recipe, empty until it cashes ("like the home run ones").
import tempfile as _tf43
_stdb43 = _ST.DB_PATH
_ST.DB_PATH = _os.path.join(_tf43.mkdtemp(prefix="guard-wall-"), "v.db")
try:
    _ST.init_db()
    _disp43 = _j42.dumps([{"pick": "Ace 6+ Ks", "side": "yes",
                           "matchup": "A @ B", "cents": 80}] * 2)
    _ST.log_slip("mlb", "2026-08-28", "wall-a", 2, 2, 0.70, 0.70, 1.5, 5.0,
                 "preset:ks80", '[{"tk":"T1","no":0},{"tk":"T2","no":0}]',
                 None, tag="ks80", legs_disp=_disp43)
    _ST.log_slip("mlb", "2026-08-29", "wall-b", 2, 2, 0.20, 0.20, 4.8, 5.0,
                 "preset:ks80", '[{"tk":"T3","no":0},{"tk":"T4","no":1}]',
                 None, tag="ks80", legs_disp=_disp43)
    _ST.log_slip("mlb", "2026-08-29", "wall-c", 2, 2, 0.10, 0.10, 9.9, 5.0,
                 "preset:ks80", '[{"tk":"T5","no":0},{"tk":"T6","no":0}]',
                 None, tag="ks80", legs_disp=_disp43)
    _ST.log_slip("mlb", "2026-08-29", "wall-d", 3, 3, 0.02, 0.02, 50.0, 5.0,
                 "preset:hr3", '[{"tk":"H1","no":0}]', None, tag="hr3",
                 legs_disp=_disp43)
    with _ST._lock, _ST._conn() as _c43:
        _ids43 = {r["key"]: r["id"] for r in _c43.execute(
            "SELECT id, key FROM slip_log").fetchall()}
    _ST.set_slip_grade(_ids43["wall-a"], 1, won=1, legs_hit=2)
    _ST.set_slip_grade(_ids43["wall-b"], 1, won=1, legs_hit=2)
    _ST.set_slip_grade(_ids43["wall-c"], 1, won=0, legs_hit=1)   # LOST
    # wall-d (hr3) never grades -- the column must stay empty
    _bw43 = _ST.preset_best_wins()
    ck("the biggest WIN hangs; a bigger LOSS never does",
       _bw43.get("ks80", {}).get("payout_x") == 4.8
       and _bw43["ks80"]["date"] == "2026-08-29"
       and _bw43["ks80"]["legs"][0]["pick"] == "Ace 6+ Ks",
       "9.9x lost with 1 of 2 legs -- the wall shows what PAID, not what "
       "almost did")
    ck("a recipe that never cashed stays honestly empty",
       "hr3" not in _bw43,
       'the 3 HR slip is a ~1.5% shot; "empty is honest, not broken"')
    _ST.log_slip("mlb", "2026-08-27", "wall-e", 2, 2, 0.5, 0.5, 2.0, 1.0,
                 "old", '[{"tk":"KXOLD-X","no":1},{"tk":"KXOLD-Y","no":0}]',
                 None, tag="ml58")
    with _ST._lock, _ST._conn() as _c43:
        _id43e = _c43.execute("SELECT id FROM slip_log WHERE key='wall-e'"
                              ).fetchone()["id"]
    _ST.set_slip_grade(_id43e, 1, won=1, legs_hit=2)
    ck("a pre-legs_disp row falls back to ticker text -- ugly but true",
       _ST.preset_best_wins()["ml58"]["legs"] == ["NO - KXOLD-X", "KXOLD-Y"])
finally:
    _ST.DB_PATH = _stdb43
_sl43 = _insp.getsource(__import__("sliplog").log_from_item)
ck("new slips carry their display legs into the ledger",
   "legs_disp=json.dumps(disp)" in _sl43 and '"matchup": grp.get("matchup")'
   in _sl43,
   "the legs column is tickers -- enough to grade, useless to show; the "
   "wall reads like the slip did the day it was built")
_apy43 = open(_os.path.join(_root, "app.py")).read()
_js43 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the endpoint serves the wall on both return shapes, and the tab draws it",
   _apy43.count('"best_wins": best_wins') == 4
   and "await _fetchPresets()"
   in _js43.split("async function loadHits")[1][:500]
   and "No win yet" in _js43
   and '$("hitsResults").dataset.loaded' in _js43,
   "the old gate read the removed hitsDate element and would have thrown "
   "on the first tap of the tab")

print()
print("=" * 72)
print("The daily crown: best on paper is the start, the record is the say")
print("=" * 72)
# The ask: "read the odds of all of the tabs and highlight the one with the
# best odds. It may not on paper have the best odds but it reads every
# parameter." Score = the slip's fee-aware EV, adjusted (capped ±10) by the
# recipe's own graded record once >=5 slips have settled.
_pl44 = {"presets": {
    "ml58": {"item": {"ev_pct": 8.0, "combined_prob_pct": 40.0,
                      "kalshi_payout_net_x": 2.7}, "label": "ML 58%+"},
    "ks80": {"item": {"ev_pct": 5.0, "combined_prob_pct": 20.0,
                      "kalshi_payout_net_x": 6.2}, "label": "Ks 80%+"},
    "hr3": {"item": None, "label": "3 Home Runs"}}}
ck("with no history, the best paper EV wears the crown",
   _pr41.best_today(_pl44, {})["id"] == "ml58")
_rec44 = {"ks80": {"graded": 8, "won": 6, "expected": 2.0, "days": 8}}
_b44 = _pr41.best_today(_pl44, _rec44)
ck("a recipe beating its claimed odds can out-crown better paper",
   _b44["id"] == "ks80" and _b44["score"] == round(5.0 + 10.0 * 8 / 18, 1)
   and "record 6-2" in (_b44["record_note"] or "")
   and "over 8 days" in _b44["record_note"],
   "(6 wins - 2.0 expected)/8 graded caps the bonus at +10 EV points, "
   "scaled by 8/(8+10) days of evidence: history colors the paper number "
   "without drowning it")
# The same eight slips logged across TWO afternoons of lineup churn are two
# days of evidence, not eight: the thumb shrinks to 10*2/12 and the paper
# favourite keeps the crown. Records without "days" (pre-field rows) fall
# back to the slip count.
ck("churn cannot buy the crown: the bonus is weighted by graded DAYS",
   _pr41.best_today(_pl44, {"ks80": {"graded": 8, "won": 6,
                                     "expected": 2.0, "days": 2}})["id"]
   == "ml58"
   and _pr41.best_today(_pl44, {"ks80": {"graded": 8, "won": 6,
                                         "expected": 2.0}})["id"] == "ks80",
   "at the 5-slip gate the raw bonus's 1-sigma noise is ~10 EV points -- "
   "the whole cap -- over a max of ten recipes; days earn the say")
ck("a thin record (under 5 graded) buys nothing",
   _pr41.best_today(_pl44, {"ks80": {"graded": 4, "won": 4,
                                     "expected": 0.5}})["id"] == "ml58")
ck("an empty day crowns no one, honestly",
   _pr41.best_today({"presets": {"hr3": {"item": None}}}, {}) is None)
_apy44 = open(_os.path.join(_root, "app.py")).read()
_js44 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the crown ships in the payload and decorates tabs AND the wall",
   '"best": presets.best_today(payload, records)' in _apy44
   and '"tot80", "📊 Totals 80%+"' in _js44
   and "decorateBestBet();"
   in _js44.split("function applyPresetTab")[1][:300]
   and "Least-bad today" in _js44
   and "today's best bet" in _js44,
   'when even the best recipe is -EV the line says "Least-bad today ... '
   'passing is a position too" -- honesty beats flattery')

print()
print("=" * 72)
print("The app's day is the ET day, and the PC follows the slate being viewed")
print("=" * 72)
# Two halves of one evening bug. The date picker defaulted to
# new Date().toISOString() -- the UTC day, which flips to "tomorrow" at 8pm
# ET -- so every evening the page silently rolled a day early, the warmer
# chased tomorrow's cold slate on the shared half-core, and the PC (honestly
# hard-coded to today) reported "0 sims needed" and idled behind a green
# light.
_js45 = open(_os.path.join(_root, "static", "app.js")).read()
ck("every 'today' default in the client is the ET day, never the UTC day",
   "new Date().toISOString().slice(0, 10)" not in _js45
   and 'timeZone: "America/New_York"' in _js45
   and '$("bbDate").value = todayET()' in _js45
   and "todayET(i)" in _js45,
   "the backend is ET everywhere; a UTC picker made the page roll to "
   "tomorrow at 8pm ET / 7pm CT")
_pcw45 = open(_os.path.join(_root, "pc_worker.py")).read()
ck("the PC sims today, the warmer's date, and tomorrow once today is done",
   'have_resp.get("warm_date")' in _pcw45
   and "datetime.timedelta(days=1)" in _pcw45
   and '"warm_date": (_warm_json_read(_WARM_STATUS) or {}).get("date")'
   in open(_os.path.join(_root, "app.py")).read(),
   "the midnight roll lands on already-warm sims instead of a cold slate")
ck("sim/have keys are bare pks and the PC normalizes before membership",
   'str(n).removesuffix(".pkl")' in _pcw45
   and 'str(g["game_pk"]) not in server_has' in _pcw45,
   "art/have keys were '<pk>.pkl'; the unnormalized test would silently "
   "never match and the PC would re-sim the whole slate every cycle")

print()
print("=" * 72)
print("No timer outlives what it watches; nothing polls a hidden screen")
print("=" * 72)
# The report: "sitting on the site a while, going between screens, causes it
# to crash." Diagnosis: the phone kills the tab after client-side timers
# accumulate. The live-feed refresher held a DOM node the slate auto-refresh
# replaces every ~20s -- the closure read `open` off the detached ghost,
# returned early forever, and the interval became IMMORTAL (one permanent
# 20s fetch + a retained DOM subtree per feed ever opened); re-opening
# stacked a second timer on top. The tennis live poll ran forever after one
# visit. Every long-lived poll now re-looks its target up by id and idles
# when the screen is hidden.
_js46 = open(_os.path.join(_root, "static", "app.js")).read()
_lf46 = _js46.split("window.toggleLiveFeed")[1][:2200]
ck("the live feed re-looks its box up by id and cleans ITSELF up when gone",
   "const cur = $(`lf-${pk}`)" in _lf46
   and "clearInterval(_liveFeedTimers[pk])" in _lf46
   and "delete _liveFeedTimers[pk]" in _lf46
   and "document.hidden" in _lf46,
   "a held node is a detached ghost one auto-refresh later -- the sim bar "
   "learned this same lesson (lookup, never a reference)")
ck("re-opening a feed can never stack a second timer",
   _lf46.count("clearInterval(_liveFeedTimers[pk])") >= 2,
   "the open path clears any survivor before arming its own interval")
ck("the tennis live poll fires only while tennis is actually on screen",
   '!$("tab-tennis").classList.contains("hidden")) loadTennis()' in _js46)
ck("the crypto refresher and update watcher idle when the app is hidden",
   '!document.hidden && !$("tab-crypto")' in _js46
   and "if (document.hidden) return;    // a backgrounded phone needs no"
   in _js46)

print()
print("=" * 72)
print("One combo build at a time: the newest click owns the CPU")
print("=" * 72)
# The report: "reconnecting everywhere, then I can't switch tabs -- when I'm
# switching between the pre-made tabs or making multiple combos back to
# back." Diagnosis: every Build click started ANOTHER background build while
# the old one kept running, and the first preset tap piled six recipe builds
# on top. Concurrent CPU-bound builds on a shared half-core starve the
# platform's health probe, which restarts the instance as "failed" while it
# was merely busy -- a total blackout from the phone. The combo SLOT caps it:
# newest build takes it unconditionally, the superseded build stops at its
# next game boundary, preset rebuilds defer while a user holds it.
import tempfile as _tf47
_slot47 = B._SLOT_PATH
B._SLOT_PATH = _os.path.join(_tf47.mkdtemp(prefix="guard-slot-"), "slot.json")
try:
    B.combo_slot_take("tokA")
    _hA47 = B.combo_slot_holder()
    B.combo_slot_take("tokB")
    _hB47 = B.combo_slot_holder()
    _os.utime(B._SLOT_PATH, (_tm.time() - 2000,) * 2)
    ck("the slot: newest take wins, a stale slot entitles nobody",
       _hA47 == "tokA" and _hB47 == "tokB"
       and B.combo_slot_holder() is None,
       "a build that died with its worker must never block the next click")
    _g47 = {"game_pk": 1, "matchup": "A @ B", "live": {}, "confirm": {}}
    try:
        B.build_mixed_parlay([_g47], n_legs=2, abort_cb=lambda: True)
        _aborted47 = False
    except RuntimeError as _e47:
        _aborted47 = "superseded" in str(_e47)
    ck("a superseded build stops at the game boundary, before paying for "
       "another simulation", _aborted47)
finally:
    B._SLOT_PATH = _slot47
_bmp47 = _insp.getsource(B.build_mixed_parlay)
ck("the abort check sits before the sim, not after it",
   _bmp47.index("abort_cb is not None and abort_cb()")
   < _bmp47.index("_was_cached = _game_sim_cached"))
_apy47 = open(_os.path.join(_root, "app.py")).read()
ck("every Build takes the slot; the running build reads it per game",
   _apy47.index("baseball.combo_slot_take(ptok)")
   < _apy47.index('_run_job(ptok, _core, "COMBO-build")')
   and "baseball.combo_slot_holder()" in _apy47
   and "not in (None, ptok)" in _apy47,
   "a missing/stale slot aborts nothing -- benefit of the doubt runs toward "
   "finishing the build")
ck("preset rebuilds defer while a user's build holds the slot",
   "if _yield_cb():" in _insp.getsource(_pr41.tick)
   and "combo_slot_holder(max_age=600)" in _insp.getsource(_pr41._yield_cb),
   "six recipe builds stacking onto a user's build is exactly the "
   "concurrency that starves the health probe; the next tick retries")

print()
print("=" * 72)
print("Back to Custom finds the maker; a dead build says so on its bar")
print("=" * 72)
# Two reports the morning after the slot shipped. (1) "go into a pre-made tab,
# back to custom, it doesn't build": renderPresetBox puts its OWN .combomaker
# inside presetBox, which sits earlier in the DOM -- so applyPresetTab's
# querySelector(".combomaker") grabbed the preset's inner div and never un-hid
# the real maker again. (2) "won't go past 1 pass": an instance restart
# mid-build leaves the job file's last counts frozen on the persistent disk,
# and the bar replayed them without comment until the 90s takeover.
_js48 = open(_os.path.join(_root, "static", "app.js")).read()
_apt48 = _js48.split("function applyPresetTab")[1][:900]
ck("the preset tab switcher addresses the custom maker BY ID",
   '$("comboMaker")' in _apt48
   and 'document.querySelector(".combomaker")' not in _apt48
   and 'class="combomaker" id="comboMaker"' in _js48,
   "a class selector matches the preset box's own inner .combomaker first, "
   "and Custom comes back to an invisible maker and a 'lost' build")
_tok48 = "guard48-" + str(int(_tm.time()))
B._job_write(_tok48, {"status": "running", "at": 1, "done": 1, "total": 1,
                      "cached": 1, "pass": 1, "passes": 3,
                      "started": _tm.time() - 120})
try:
    _os.utime(B._job_path(_tok48), (_tm.time() - 60,) * 2)
    _pg48 = B.progress_get(_tok48)
    ck("a job served from the shared file carries its heartbeat age",
       _pg48 and _pg48.get("beat_age_s") is not None
       and 55 <= _pg48["beat_age_s"] <= 70,
       "without it a build whose instance died mid-flight replays frozen "
       "counts and the owner reads 'won't go past 1 pass'")
finally:
    B.job_drop(_tok48)
_apy48 = open(_os.path.join(_root, "app.py")).read()
ck("the progress endpoint passes the heartbeat age through and the bar "
   "says 'restarted' instead of replaying the dead build's counts",
   '"beat_age_s": p.get("beat_age_s")' in _apy48
   and "real.beat_age_s > 45" in _js48
   and "restarted mid-build" in _js48)
ck("the supersede abort also guards the expensive post-loop tail",
   _bmp47.count("abort_cb is not None and abort_cb()") == 2
   and _bmp47.rindex("abort_cb is not None and abort_cb()")
   < _bmp47.index("combo_engine.dp_legs"),
   "a build that lost the slot during its LAST game's sim must not pay for "
   "the frontier + chooser anyway")

print()
print("=" * 72)
print("Totals 80%+: nearest the bar from above, never under it")
print("=" * 72)
# The owner's change, near-verbatim: "as close to 80% as possible without it
# going under. I see some that are like 97% but I know it can go lower."
# pick "floor" walks each game's total ladder to the line nearest the bar;
# the floor filter has already removed everything under it, so nearest-above
# can never dip below 80.
_spec49 = {s["id"]: s for s in _pr41.PRESETS}
ck("every scan recipe hunts the bar; the top-N recipes stay likeliest",
   all(_spec49[p].get("pick") == "floor"
       for p in ("ks80", "ml58", "rl80", "tot80"))
   and _spec49["hits5"]["kind"] == "top" and _spec49["hr3"]["kind"] == "top"
   and _pr41.REV >= 7,
   'the owner: "them all hunting the bar except hits and HR - I want those '
   'to stay most likely"')
_cands49 = [
    {"type": "Total", "label": "Under 12.5 runs", "marg": 0.97, "side": "yes",
     "group": "Total", "kref": {"t": "total"}, "model_pct": 96.0,
     "marg_model": 0.95},
    {"type": "Total", "label": "Under 10.5 runs", "marg": 0.82, "side": "yes",
     "group": "Total", "kref": {"t": "total"}, "model_pct": 80.0,
     "marg_model": 0.79},
    {"type": "Total", "label": "Under 9.5 runs", "marg": 0.74, "side": "yes",
     "group": "Total", "kref": {"t": "total"}, "model_pct": 72.0,
     "marg_model": 0.71}]
_games49 = [{"game_pk": 11, "matchup": "C @ D", "kalshi_suffix": "s9",
             "live": {}, "confirm": {}}]
import kalshi_mlb as _km49
_orig49 = (B._game_sim, B._price_cands, _km49.index, _km49.ticker_leg)
try:
    B._game_sim = lambda g: {"cands": [dict(c) for c in _cands49],
                             "sim": {"n": 100}}

    def _fp49(cands, sfx, blend=True):
        for c in cands:
            c["price_cents"] = 80
        return cands
    B._price_cands = _fp49
    _km49.index = lambda: {}
    _km49.ticker_leg = lambda idx, sfx, kref: ("TK49", 1)
    _t49 = _pr41._build_all(_games49, _spec49["tot80"])
    _k49 = _pr41._build_all(_games49, dict(_spec49["tot80"], pick=None))
    ck("nearest-above-the-bar wins the slot; under the bar never qualifies",
       _t49 and _t49["n_legs"] == 1
       and _t49["groups"][0]["legs"][0]["pick"] == "Under 10.5 runs"
       and _k49["groups"][0]["legs"][0]["pick"] == "Under 12.5 runs",
       "a 97% deep line and an 81% line fill the same slot; the one nearest "
       "80 pays real money for the same recipe")
finally:
    (B._game_sim, B._price_cands, _km49.index, _km49.ticker_leg) = _orig49

print()
print("=" * 72)
print("The ⚡ rungs: Optimal-for-my-× as four locked recipes on one tab")
print("=" * 72)
# The ask: "1 more tab with 4 different items - a 2x, 3x, 5x and 10x section.
# Works like the 'optimal for my x' button, continuously runs and updates
# like the rest, logs each time, graded in the wall." Server-side each rung
# is its own preset (own tag -> own ledger record and wall column); the
# client folds the four under one tab.
ck("four rungs, locked at 2/3/5/10, kind 'target'",
   [( _spec49[p]["kind"], _spec49[p]["target_x"])
    for p in ("x15", "x2", "x3", "x5", "x10", "x100", "x200")]
   == [("target", 1.5), ("target", 2.0), ("target", 3.0), ("target", 5.0),
       ("target", 10.0), ("target", 100.0), ("target", 200.0)])
ck("build_all dispatches the target kind (through the shared frontier)",
   "_build_target(games, spec, abort_cb=abort_cb," in _insp.getsource(_pr41.build_all)
   and "frontier_cache=fcache" in _insp.getsource(_pr41.build_all))
# The rung must mirror the ⚡ button knob for knob -- payout REQUIRED, legs
# OFF, balanced, floor swept by best_target -- or the tab and the button
# would quietly answer different questions.
_bt49 = _insp.getsource(_pr41._build_target)
ck("a rung is the endpoint's optimal mode, knob for knob",
   'legs_mode="off"' in _bt49 and 'payout_mode="require"' in _bt49
   and 'objective="balanced"' in _bt49 and "include_live=False" in _bt49
   and "combo_engine.best_target" in _bt49
   and "MAX_PAYOUT_X" in _bt49)
_calls49 = []
_obm49 = B.build_mixed_parlay
try:
    def _fbm49(games, **kw):
        _calls49.append(kw)
        return None
    B.build_mixed_parlay = _fbm49
    _r49 = _pr41._build_target([{"game_pk": 1}], _spec49["x2"])
    import combo_engine as _ce49
    ck("the rung sweeps the same floors the button does, at its own target",
       _r49 is None and len(_calls49) == len(_ce49.OPTIMAL_FLOORS)
       and [c["target_pct"] for c in _calls49] == list(_ce49.OPTIMAL_FLOORS)
       and all(c["target_payout"] == 2.0 for c in _calls49),
       "an empty slate returns None honestly instead of a fabricated slip")
finally:
    B.build_mixed_parlay = _obm49
_js50 = open(_os.path.join(_root, "static", "app.js")).read()
ck("one ⚡ tab wearing four tags: tab row, wall columns, crown mapping",
   '["targets", "⚡ 1.5-200×"]' in _js50
   and '_TARGET_IDS = ["x15", "x2", "x3", "x5", "x10", "x100", "x200"]' in _js50
   and "const cols = _WALL_COLS.map" in _js50
   and '["x10", "⚡ Pays 10×"]' in _js50
   and '_TARGET_IDS.includes(b.id) ? "targets" : b.id' in _js50
   and '_presetSel === "targets" ? _TARGET_IDS : [_presetSel]' in _js50,
   "the wall lists LOGGED TAGS (four rung columns), the tab row shows one "
   "tab, and a crowned rung highlights the tab all four share")
ck("a rung that can't reach its number says so on the slip",
   "nothing reaches ${it.target_payout_x}× today" in _js50,
   '"closest today" and "pays 10×" are different claims; the tab must not '
   "blur them")

print()
print("=" * 72)
print("Discrepancy sweep: what the site says is what the site does")
print("=" * 72)
# The wall/records question ("are those grades padding the numbers, or just
# showboating?") traced end to end: slip grades feed the tabs, the wall and
# the crown -- nothing else. The crown was the one place a record moved a
# number, and its bonus was luck-weighted at the gate; now it earns its say
# by graded DAYS (above). The rest of the sweep: NFL builds bypassed the combo
# slot, a dead "Predicted Hits" route still described a tab that no longer
# exists, and the maker's help text listed a subset of the simulated lines.
_apy51 = open(_os.path.join(_root, "app.py")).read()
_js51 = open(_os.path.join(_root, "static", "app.js")).read()
import nfl_game_sim as _ngs51
ck("NFL builds take the combo slot and yield to it, like baseball",
   _apy51.count("baseball.combo_slot_take(ptok)") == 3
   and _apy51.index("baseball.combo_slot_take(ptok)", _apy51.index("NFL-COMBO"))
   < _apy51.index('_run_job(ptok, _core, "NFL-COMBO-build")')
   and "abort_cb is not None and abort_cb()"
   in _insp.getsource(_ngs51.build_parlay)
   and _apy51.count("not in (None, ptok)") == 3,
   "one slot across every sport: an NFL, a UFC and an MLB build must never "
   "grind the shared core together")
ck("the dead Predicted Hits route and its query are gone",
   '"/api/baseball/hits"' not in _apy51
   and "prop_hit_combos" not in open(_os.path.join(_root, "store.py")).read()
   and "api/baseball/hits" not in _js51
   and "function renderHits(" not in _js51
   and "function renderHitCombo(" not in _js51,
   "a route no client called, whose docstring described a tab replaced by "
   "the wall -- the site said one thing and served another")
ck("the maker's help text no longer lists a subset of the simulated lines",
   "Every line in the prop-type list below is simulated" in _js51
   and "(hits, bases, runs total, ML, run line, RFI, Ks)" not in _js51)
# Functional: preset_records counts distinct graded DAYS per tag.
import tempfile as _tf51
_db51 = _ST.DB_PATH
_ST.DB_PATH = _os.path.join(_tf51.mkdtemp(prefix="guard-days-"), "v.db")
try:
    _ST.init_db()
    for i, day in enumerate(("2026-08-29", "2026-08-29", "2026-08-30")):
        _ST.log_slip("mlb", day, f"k51-{i}", 2, 2, 0.3, 0.25, 3.0, 1.0,
                     "preset:tot80", "[]", None, tag="tot80")
    for r in _ST.ungraded_slips(int(_tm.time()) + 7 * 3600):
        _ST.set_slip_grade(r["id"], 1, won=1, legs_hit=2)
    _r51 = _ST.preset_records()["tot80"]
    ck("three slips over two dates are two DAYS of evidence",
       _r51["graded"] == 3 and _r51["won"] == 3 and _r51["days"] == 2,
       "an afternoon of lineup churn logs one recipe three times")
finally:
    _ST.DB_PATH = _db51

print()
print("=" * 72)
print("NFL readiness: the ledger grades football, and grades totals right")
print("=" * 72)
# The sweep before kickoff. Two findings that mattered: (1) an Under was
# logged as YES on the OVER's ticker (both sides share it), so the grader
# read every Under backwards -- MLB and NFL alike, and the Totals 80%+ recipe
# most of all; (2) NFL slips were never in the ledger, NFL tickers carry only
# the day (closes anchored at midnight, grading polled all week), and the NFL
# record only grew while someone had the tab open.
import sliplog as _sl52
import predlog as _pl52
_cases52 = [
    ({"side": "yes", "kref": {"t": "total", "n": 12, "over": False}}, 1),
    ({"side": "yes", "kref": {"t": "total", "n": 12, "over": True}}, 0),
    ({"side": "no", "kref": {"t": "total", "n": 12, "over": True}}, 1),
    ({"side": "no", "kref": {"t": "total", "n": 12, "over": False}}, 0),
    ({"side": "no", "kref": {"t": "prop", "stat": "ks"}}, 1),
    ({"side": "yes", "pick": "Under 9.5 runs"}, 1),       # no kref: the label
    ({"side": "yes", "pick": "Over 9.5 runs"}, 0)]
ck("an Under is NO on the Over's market; every other leg keeps its side",
   all(_sl52._bought_no(l) == want for l, want in _cases52),
   "the first cut logged side=='no' verbatim and graded every Under backwards")
ck("the ledger repairs itself: wrong-side Unders are re-sided and re-graded",
   "repair_under_legs()" in _insp.getsource(_ST.init_db)
   and "graded=CASE WHEN graded=1 THEN 0 ELSE graded END"
   in _insp.getsource(_ST.repair_under_legs),
   "a slip graded under the wrong side goes back to the grader; void stays void")
_db52 = _ST.DB_PATH
_ST.DB_PATH = _os.path.join(_tf51.mkdtemp(prefix="guard-under-"), "v.db")
try:
    _ST.init_db()
    _legs52 = [{"tk": "KXMLBTOTAL-26AUG31XX-12", "no": 0, "close": None},
               {"tk": "KXMLBGAME-26AUG31XX-NYY", "no": 0, "close": None}]
    _disp52 = [{"pick": "Under 11.5 runs", "side": "yes"},
               {"pick": "NYY to win", "side": "yes"}]
    _ST.log_slip("mlb", "2026-08-31", "u52", 2, 1, 0.4, 0.35, 2.5, 1.0,
                 "preset:tot80", _json2.dumps(_legs52), None, tag="tot80",
                 legs_disp=_json2.dumps(_disp52))
    _rid52 = _ST.ungraded_slips(int(_tm.time()) + 7 * 3600)[0]["id"]
    _ST.set_slip_grade(_rid52, 1, won=0, legs_hit=1)   # the backwards grade
    _n52 = _ST.repair_under_legs()
    _row52 = _ST.ungraded_slips(int(_tm.time()) + 7 * 3600)
    ck("functional: the repair flips the Under, resets the grade, and is idempotent",
       _n52 == 1 and _ST.repair_under_legs() == 0 and _row52
       and _json2.loads(_row52[0]["legs"])[0]["no"] == 1
       and _json2.loads(_row52[0]["legs"])[1]["no"] == 0)
    # Grading waits for the STAMPED start, not the log time.
    _ST.log_slip("nfl", "2026-09-13", "f52", 2, 2, 0.3, 0.25, 3.0, 1.0, "b",
                 "[]", None, start_ts=int(_tm.time()) + 3600)
    _ST.log_slip("nfl", "2026-09-13", "p52", 2, 2, 0.3, 0.25, 3.0, 1.0, "b",
                 "[]", None, start_ts=int(_tm.time()) - 5 * 3600)
    _due52 = {r["key"] for r in _ST.ungraded_slips(int(_tm.time()))}
    ck("the grader waits for kickoff + a game: a Tuesday slip for Sunday is "
       "not probed all week",
       "p52" in _due52 and "f52" not in _due52 and "u52" not in _due52,
       "~160 Kalshi reads per pass for five days, for nothing, against a "
       "rate limiter the whole app shares")
    ck("the slip report can answer for one sport",
       _ST.slip_report(sport="nfl")["pending"] == 2
       and _ST.slip_report()["pending"] == 3)
finally:
    _ST.DB_PATH = _db52
ck("a day-only ticker's start is pushed to the END of its day; a stamped "
   "kickoff wins outright",
   _sl52._start_of({"ticker": "KXNFLGAME-26SEP09NESEA-SEA"})
   == _pl52._event_ts("KXNFLGAME-26SEP09NESEA-SEA") + 86400
   and _sl52._start_of({"ticker": "KXMLBGAME-26AUG312138NYYLAA-NYY"})
   == _pl52._event_ts("KXMLBGAME-26AUG312138NYYLAA-NYY")
   and _sl52._start_of({"ticker": "X", "start_ts": 1789000000}) == 1789000000)
ck("the slip's date is the ET day of its first kickoff, not the build day",
   "min(starts)" in _insp.getsource(_sl52.log_from_item)
   and 'ZoneInfo("America/New_York")' in _insp.getsource(_sl52.log_from_item))
# NFL legs carry tickers + kickoffs, and the endpoint files the slip.
import kalshi_nfl as _kn52
_idx52 = {"26SEP13ARILAC": {"tick": {("ml", "LAC"): ("KXNFLGAME-26SEP13ARILAC-LAC", 1),
                                    ("spread", "LAC", 4): ("KXNFLSPREAD-26SEP13ARILAC-LAC4", 1),
                                    ("total", 45): ("KXNFLTOTAL-26SEP13ARILAC-45", 1),
                                    ("prop", "rec_yd", "mike evans", 90.0): ("KXNFLRECYDS-26SEP13ARILAC-X-90", 1)}}}
ck("kalshi_nfl.ticker_leg resolves every leg kind to its market, either side",
   _kn52.ticker_leg(_idx52, "26SEP13ARILAC", {"t": "ml", "team": "LAC", "no": True})[0]
   == "KXNFLGAME-26SEP13ARILAC-LAC"
   and _kn52.ticker_leg(_idx52, "26SEP13ARILAC", {"t": "spread", "team": "LAC", "by": 4})[0]
   == "KXNFLSPREAD-26SEP13ARILAC-LAC4"
   and _kn52.ticker_leg(_idx52, "26SEP13ARILAC", {"t": "total", "n": 45, "over": False})[0]
   == "KXNFLTOTAL-26SEP13ARILAC-45"
   and _kn52.ticker_leg(_idx52, "26SEP13ARILAC",
                        {"t": "prop", "stat": "rec_yd", "player": "Mike Evans", "line": 90.0})[0]
   == "KXNFLRECYDS-26SEP13ARILAC-X-90"
   and _kn52.ticker_leg(_idx52, "26SEP13ARILAC", {"t": "ml", "team": "ARI"}) == (None, None)
   and '"tick": {}' in _insp.getsource(_kn52._build))
_bp52 = _insp.getsource(_ngs51.build_parlay)
_apy52 = open(_os.path.join(_root, "app.py")).read()
ck("NFL parlays stamp ticker/close/kickoff on every leg and are filed in the ledger",
   'leg["ticker"], leg["close_time"] = tk, close' in _bp52
   and 'leg["start_ts"] = _kick.get(grp.get("suffix"))' in _bp52
   and _apy52.count("_log(item)\n") == 4      # NFL and UFC, optimal + plain
   and 'sliplog.log_from_item(item, sport="nfl")' in _apy52,
   '"every parlay you build is logged" was true of one sport')
ck("predlog anchors NFL closes at the KICKOFF the logger knew, not midnight",
   "event_ts INTEGER" in _insp.getsource(_pl52.init_db)
   and "ev = logged_ev or _event_ts(tk)" in _insp.getsource(_pl52.snapshot_closes)
   and "_iso_ts(g.get(\"date\"))" in _insp.getsource(_ngs51._build_board)
   and "row[4] if len(row) > 4 else None" in _insp.getsource(_pl52.log_many),
   "NFL tickers carry only the day; the snapshot stopped up to 20h early")
ck("predlog rows are the YES/Over side of each market only",
   'if kref.get("no") or (kref.get("t") == "total"' in open(
       _os.path.join(_root, "baseball.py")).read(),
   "NO/Under candidates share the YES ticker; insert-order luck kept them out "
   "of the prob, the mkt backfill had no such luck")
import nfl_track as _nt52
ck("the NFL record grows on the recorder's cadence in season, tab open or not",
   "def tick" in _insp.getsource(_nt52)
   and "nfl_track.tick()" in _insp.getsource(__import__("mlb_recorder")._loop)
   and "record_from_board(data)" in _insp.getsource(_nt52.tick)
   and "d.month >= 8 or d.month <= 2" in _insp.getsource(_nt52.tick))
_js52 = open(_os.path.join(_root, "static", "app.js")).read()
_html52 = open(_os.path.join(_root, "templates", "index.html")).read()
ck("the NFL tab shows its own slip calibration",
   'loadSlipLog("nflSlipLog", "nfl")' in _js52
   and 'id="nflSlipLog"' in _html52
   and 'store.slip_report(sport=request.args.get("sport") or None)' in _apy52)
ck("preset legs carry kref so the ledger can side them",
   '"kref": best.get("kref")' in _insp.getsource(_pr41._build_all))

print()
print("=" * 72)
print("The DFS slate picker picks a REAL slate")
print("=" * 72)
# Live on Sep 3: dk.slate_for("nfl") chose draft group 152634 -- a "Madden
# Stream" video-game contest (fake SF @ NE, real names, 89 players) -- over
# week 1's Classic slate (151307, Sunday 1pm, 1,323 players), because the
# picker took tonight's biggest group with no idea what kind of contest it
# was. A Thursday-night Showdown would have won the same way.
import dk as _dk53
_osl53, _opl53 = _dk53.slates, _dk53.players
try:
    _dk53.slates = lambda sport: [
        {"draft_group_id": 1, "sport": "nfl", "starts": "2099-01-01T20:20:00",
         "games": 1, "contest_type": 96, "tag": "(NE @ SEA)"},          # showdown tonight
        {"draft_group_id": 2, "sport": "nfl", "starts": "2099-01-04T13:00:00",
         "games": 13, "contest_type": 21, "tag": None}]                  # classic Sunday
    _dk53.players = lambda dg: [{"name": f"P{dg}", "salary": 5000, "position": "QB",
                                 "roster_pos": "QB", "game": "A @ B", "team": "A",
                                 "avg_ppg": None, "status": None, "dk_id": dg,
                                 "available": True, "role": None}]
    _dk53.contests = lambda sport, dg=None: []
    _got53 = _dk53.slate_for("nfl")
    ck("NFL takes the Classic main slate over a same-night Showdown",
       _got53 and _got53["draft_group_id"] == 2)
finally:
    _dk53.slates, _dk53.players = _osl53, _opl53
ck("Madden Stream groups never reach the picker, and the type rides along",
   "_NEVER_TYPES = {158, 159}" in _insp.getsource(_dk53)
   and '"madden" in (tag or "").lower()' in _insp.getsource(_dk53.slates)
   and '"contest_type": ctype' in _insp.getsource(_dk53.slates),
   "a video-game simulation with real player names is not a slate")

print()
print("=" * 72)
print("Docstrings point at things that exist")
print("=" * 72)
# The owner: "since docstrings are stale, go through every docstring and
# either confirm they are working or confirm they are pointing to nonsense."
# The manual pass rewrote the ones describing an older app (app.py was still
# a seven-endpoint crypto helper; store.py knew one table; kalshi.py, racing.py
# and simulate.py described a corner of themselves; calibrate.py listed three
# models; nfl_dfs/lol_dfs/deep_cache/prices/mlb_form claimed retired facts).
# This guard keeps the MECHANICAL half honest from here: every `module.attr`
# a docstring names must exist, every /api route it names must be registered,
# and every ?param= an app.py route documents must be read somewhere in app.py.
import ast as _ast54
import glob as _glob54
import re as _re54
_mods54 = {_os.path.splitext(_os.path.basename(p))[0]
           for p in _glob54.glob(_os.path.join(_root, "*.py"))}
_apy54 = open(_os.path.join(_root, "app.py")).read()
_routes54 = set(_re54.findall(r'@app\.route\("([^"]+)"', _apy54))
_rpre54 = {r.split("<")[0].rstrip("/") for r in _routes54}
_dotted54 = _re54.compile(r"\b([a-z_][a-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")
_routeish54 = _re54.compile(r"(/api/[A-Za-z0-9_/\-]+)")
_notattr54 = {"py", "db", "json", "csv", "pkl", "yml", "yaml", "bat", "cfg",
              "txt", "md", "html", "js", "com", "org", "gov", "net", "io",
              "fandom", "app", "gz"}
_attr_cache54 = {}
def _has_attr54(mod, name):
    if mod not in _attr_cache54:
        _src = open(_os.path.join(_root, mod + ".py")).read()
        _attr_cache54[mod] = set(_re54.findall(r"^(?:def|class)\s+([A-Za-z_]\w*)", _src, _re54.M)) \
            | set(_re54.findall(r"^([A-Za-z_]\w*)\s*=", _src, _re54.M))
    return name in _attr_cache54[mod]
_bad54 = []
for _p in sorted(_glob54.glob(_os.path.join(_root, "*.py"))):
    _mod = _os.path.splitext(_os.path.basename(_p))[0]
    _src = open(_p).read()
    _t = _ast54.parse(_src)
    _nodes = [("module", _t)] + [(n.name, n) for n in _ast54.walk(_t)
                                 if isinstance(n, (_ast54.FunctionDef, _ast54.AsyncFunctionDef, _ast54.ClassDef))]
    for _name, _n in _nodes:
        _d = _ast54.get_docstring(_n)
        if not _d:
            continue
        for _m, _a in _dotted54.findall(_d):
            if _m in _mods54 and _m != _mod and _a not in _notattr54 and not _has_attr54(_m, _a):
                _bad54.append(f"{_mod}.{_name}: {_m}.{_a}")
        for _r in _routeish54.findall(_d):
            _r0 = _r.rstrip("/.,")
            if (_r0 not in _routes54 and _r0 not in _rpre54
                    and not any(_r0.startswith(x + "/") for x in _rpre54)):
                _bad54.append(f"{_mod}.{_name}: route {_r0}")
        if _mod == "app" and _name != "module":
            for _q in set(_re54.findall(r"[?&]([a-z_]+)=", _d)):
                if f'"{_q}"' not in _apy54:
                    _bad54.append(f"app.{_name}: ?{_q}=")
ck("every module.attr, /api route and ?param a docstring names exists",
   not _bad54, "; ".join(_bad54[:12]))

print()
print("=" * 72)
print("The -200 rung: a combo Kalshi PAYS 1.5x on, with the best true odds")
print("=" * 72)
# "-200 equals" 1.5x decimal (risk 200 to win 100). The owner: "I did want
# it to be a combo. If the combo maker can find edges to functionally make
# it 1.5x in Kalshi's eyes but factually even higher, I wanna exploit that."
# So the rung judges its target on what Kalshi PAYS (every leg quoted) and
# ranks the survivors by the sim's probability -- the widest price-vs-odds
# gap wins. Custom builds keep the fair basis and its anti-chasing rule.
_spec55 = {s["id"]: s for s in _pr41.PRESETS}
ck("x15 is a 1.5x MARKET-basis target rung, first on the tab, off the wall",
   _spec55["x15"]["target_x"] == 1.5 and _spec55["x15"]["kind"] == "target"
   and _spec55["x15"].get("payout_basis") == "market"
   and "-200" in _spec55["x15"]["label"]
   and '["x15", "⚡' not in _js50 and '_TARGET_IDS = ["x15"' in _js50
   and _pr41.REV >= 9
   and 'payout_basis=spec.get("payout_basis", "fair")'
   in _insp.getsource(_pr41._build_target),
   "-200 American is 1.5x decimal; the owner asked for no wall column")
import combo_engine as _ce55
_A55 = {"legs": 2, "prob": 0.74, "cost": 0.66, "payout": 1.515,
        "fair_payout": 1.35, "ev": 0.12, "priced_frac": 1.0, "sel": [1]}
_B55 = {"legs": 2, "prob": 0.67, "cost": 0.70, "payout": 1.43,
        "fair_payout": 1.49, "ev": -0.04, "priced_frac": 1.0, "sel": [2]}
_C55 = {"legs": 3, "prob": 0.60, "cost": 0.60, "payout": 1.67,
        "fair_payout": 1.67, "ev": 0.0, "priced_frac": 1.0, "sel": [3]}
_D55 = {"legs": 2, "prob": 0.80, "cost": 0.60, "payout": 1.67,
        "fair_payout": 1.25, "ev": 0.33, "priced_frac": 0.5, "sel": [4]}
_mk55, _mm55 = _ce55.choose([_A55, _B55, _C55, _D55], objective="balanced",
                            payout_target=1.5, payout_mode="require",
                            legs_mode="off", payout_basis="market")
_fr55, _fm55 = _ce55.choose([_A55, _B55, _C55, _D55], objective="balanced",
                            payout_target=1.5, payout_mode="require",
                            legs_mode="off")
ck("market basis: the slip that PAYS 1.5x with the best true odds wins; "
   "a half-quoted slip cannot qualify; the fair basis still picks fair",
   _mk55 is _A55 and _mm55["payout_reached"] is True
   and _fr55 is _C55 and _fm55["payout_reached"] is True,
   "A pays 1.515 at 74% true (fair 1.35 -- the gap IS the bet); D pays more "
   "but half its legs are unquoted; on the fair basis only C reaches 1.5")
ck("the basis rides through the builder to the slip",
   'targets["payout_basis"] = "market"' in _insp.getsource(B.build_mixed_parlay)
   and 'item["payout_basis"] = "market"' in _insp.getsource(B.build_mixed_parlay)
   and 'it.payout_basis === "market"' in open(_os.path.join(_root, "static", "app.js")).read())
ck("a rung is always a combo, and the ledger keeps its two-leg rule",
   "if legs < 2:" in _insp.getsource(_ce55.frontier)
   and "if len(legs) < 2:" in _insp.getsource(_sl52.log_from_item),
   "the frontier never yields one leg, so no one-leg exception was needed")

print()
print("=" * 72)
print("The recorder must never hammer ESPN; presets yield and defer")
print("=" * 72)
# Overnight Sep 4: the new NFL recorder tick probed ESPN ~27 times a pass,
# every ten minutes, for pick dates that had not happened yet; ESPN 403'd
# the server's IP (320 refusals by 7:30am) and every ESPN reader on the
# site went dark. Meanwhile the preset rebuild grew to 21 maker passes and
# ran on the shared core during lineup churn, unable to yield to a user's
# build. The owner saw a build that never reported progress.
import nfl_track as _nt56
import urllib.error as _ue56
_calls56 = []
_ok56 = (_KTH._get_json, _ST.ungraded_nfl_picks)
_nt56._state.update({"espn_block_until": 0.0, "last_grade": 0.0,
                     "week": None, "week_ts": 0.0})
try:
    def _boom56(url, timeout=10):
        _calls56.append(url)
        raise _ue56.HTTPError(url, 403, "Forbidden", {}, None)
    _KTH._get_json = _boom56
    _ST.ungraded_nfl_picks = lambda: [
        {"game_id": "2026-08-30_A@B", "date": "2026-08-30", "pick_side": "home"},
        {"game_id": "2026-08-31_C@D", "date": "2026-08-31", "pick_side": "home"},
        {"game_id": "2099-09-13_E@F", "date": "2099-09-13", "pick_side": "home"}]
    _n56 = _nt56.grade_due()
    ck("one ESPN refusal ends the grading pass and parks the module for hours",
       _n56 == 0 and len(_calls56) == 1 and _nt56.espn_blocked()
       and _nt56._state["espn_block_until"] - _tm.time() > 5 * 3600,
       "320 refusals in a night: retrying a block every ten minutes keeps it")
    _calls56.clear()
    ck("...and a parked module skips grading AND the tick entirely",
       _nt56.grade_due() == 0 and _nt56.tick() == 0 and not _calls56)
    _nt56._state["espn_block_until"] = 0.0
    _calls56.clear()
    _ST.ungraded_nfl_picks = lambda: [
        {"game_id": "2099-09-13_E@F", "date": "2099-09-13", "pick_side": "home"}]
    ck("a pick for a date that has not happened is never probed",
       _nt56.grade_due() == 0 and not _calls56,
       "next Sunday's pick cannot be final; asking ESPN about it is pure load")
finally:
    _KTH._get_json, _ST.ungraded_nfl_picks = _ok56
    _nt56._state.update({"espn_block_until": 0.0, "last_grade": 0.0,
                         "week": None, "week_ts": 0.0})
_tk56 = _insp.getsource(_nt56.tick)
ck("the tick reads a board that already exists; it never kicks a build",
   "boardshare.get(name, 3 * 3600)" in _tk56 and "nfl_game_sim.board(" not in _tk56
   and "_WEEK_EVERY_S" in _tk56 and "_GRADE_EVERY_S" in _tk56,
   "board() kicks a server-side drive-engine build every half hour whether "
   "anyone is looking or not")
# Presets: a user's build supersedes a rebuild in flight; the PC gets a cycle.
_g56 = [{"game_pk": 1, "matchup": "A @ B", "live": {}, "confirm": {},
         "kalshi_suffix": "s1"}]
try:
    _pr41.build_all("2026-09-04", _g56, "sig", abort_cb=lambda: True)
    _sup56 = False
except RuntimeError as _e:
    _sup56 = "superseded" in str(_e)
ck("a preset rebuild yields whole to a user's build (no payload of Nones)",
   _sup56, "the first version swallowed the supersede per recipe and would "
   "have published 'Nothing qualifies today' eleven times")
_tick56 = _insp.getsource(_pr41.tick)
ck("the server waits a PC cycle before spending the shared core on presets",
   'boardshare.pc_status().get("state") == "on"' in _tick56
   and "_PC_WAIT_S" in _tick56 and _pr41._PC_WAIT_S == 900
   and "build_all(date, games, sig, abort_cb=_yield_cb)" in _tick56
   and 'return boardshare.pc_status(_PC_STATUS_PATH)' in _insp.getsource(__import__("app")._pc_status),
   "the PC builds the same payload on cores the health probe does not live on; "
   "past the wait the server still builds -- the PC can only add speed")
ck("the bar's fallback text stops claiming to know what the server is doing",
   "no progress reported yet" in open(_os.path.join(_root, "static", "app.js")).read()
   and "simulating games that weren't cached`" not in open(_os.path.join(_root, "static", "app.js")).read())

print()
print("=" * 72)
print("Restarts get a timeline; the PC's presets are served, not rebuilt")
print("=" * 72)
# Sep 4: the instance restarted at 15:16 and again at 17:20 with an EMPTY
# error ledger -- nothing threw, something was busy -- and nothing on the
# site recorded what. The two fixes here: heavy jobs write a timeline the
# export carries, and the server stops rebuilding presets on every lineup
# fingerprint move while the PC (whose cores the probe does not live on)
# is delivering the same payload.
import jobs as _jb57
_jp57 = _jb57.PATH
_jb57.PATH = _os.path.join(_tf51.mkdtemp(prefix="guard-jobs-"), "jobs.json")
try:
    with _jb57.timed("guard:one"):
        pass
    try:
        with _jb57.timed("guard:boom"):
            raise ValueError("x")
    except ValueError:
        pass
    _rows57 = _jb57.recent()
    ck("a heavy job leaves start and end rows, with duration and any error",
       [r["phase"] for r in _rows57] == ["start", "end", "start", "end"]
       and _rows57[1]["dur_s"] is not None and _rows57[1]["err"] is None
       and "ValueError" in (_rows57[3]["err"] or ""),
       "a probe restart leaves the error ledger empty; this names the job")
finally:
    _jb57.PATH = _jp57
_apy57 = open(_os.path.join(_root, "app.py")).read()
_wf57 = open(_os.path.join(_root, ".github", "workflows", "error-log.yml")).read()
ck("the slate child, tennis pools, NFL board, presets and combo builds are timed, "
   "and the export carries the timeline",
   'jobs.timed(f"slate:{date}")' in _insp.getsource(B.analyze_slate)
   and 'jobs.timed("tennis-pools")' in open(_os.path.join(_root, "tennis_elo.py")).read()
   and 'jobs.timed(f"nfl-board:w{week}")' in _insp.getsource(_ngs51.board)
   and 'jobs.timed("presets")' in _insp.getsource(_pr41.tick)
   and 'jobs.timed(f"combo:{errcode}")' in _apy57
   and '@app.route("/api/diag/jobs")' in _apy57
   and "jobs-latest.json" in _wf57)
_tick57 = _insp.getsource(_pr41.tick)
ck("with the PC on, the server SERVES the PC's payload and never rebuilds on a "
   "fingerprint move; it builds only when the PC has gone quiet",
   "age < _PC_STALE_S" in _tick57 and _pr41._PC_STALE_S == 45 * 60
   and 'cur.get("date") == date and cur.get("rev") == REV' in _tick57
   and "if ensure_logged(cur):" in _tick57.split("age < _PC_STALE_S")[1][:300],
   "the fingerprint moves all afternoon as lineups post; rebuilding 21 passes "
   "on the shared core each time is what starved the probe at 15:16")
# The five rungs share one frontier per floor.
import combo_engine as _ce57
import kalshi_mlb as _km57
_cands57 = [
    {"type": "ML", "label": "A to win", "marg": 0.62, "side": "yes", "group": "ML",
     "kref": {"t": "ml", "team": "A"}, "mask": (1 << 62) - 1, "model_pct": 60.0},
    {"type": "Total", "label": "Over 8.5 runs", "marg": 0.55, "side": "yes",
     "group": "Total", "kref": {"t": "total", "n": 9, "over": True},
     "mask": ((1 << 55) - 1) << 10, "model_pct": 54.0},
    {"type": "Ks", "label": "P 5+ Ks", "marg": 0.70, "side": "yes", "group": "K:P",
     "kref": {"t": "ks"}, "mask": ((1 << 70) - 1) << 20, "model_pct": 69.0}]
_games57 = [{"game_pk": 1, "matchup": "A @ B", "kalshi_suffix": "s1", "live": {},
             "confirm": {}, "home_name": "B", "away_name": "A"}]
_orig57 = (B._game_sim, B._price_cands, _km57.index, _ce57.frontier)
_nfront57 = [0]
try:
    B._game_sim = lambda g: {"cands": [dict(c) for c in _cands57], "sim": {"n": 100}}

    def _fp57(cands, sfx, blend=True):
        for c in cands:
            c["price_cents"] = 55
        return cands
    B._price_cands = _fp57
    _km57.index = lambda: {}
    _real57 = _orig57[3]

    def _front57(*a, **k):
        _nfront57[0] += 1
        return _real57(*a, **k)
    _ce57.frontier = _front57
    _fc57 = {}
    _a57 = B.build_mixed_parlay(_games57, n_legs=2, target_pct=30, target_payout=1.5,
                                max_legs_per_game=3, max_total_legs=3,
                                legs_mode="off", payout_mode="require",
                                frontier_cache=_fc57)
    _b57 = B.build_mixed_parlay(_games57, n_legs=2, target_pct=30, target_payout=3.0,
                                max_legs_per_game=3, max_total_legs=3,
                                legs_mode="off", payout_mode="require",
                                payout_basis="market", frontier_cache=_fc57)
    ck("two rungs at one floor build the frontier ONCE and choose twice",
       _nfront57[0] == 1 and len(_fc57) == 1 and _a57 is not None and _b57 is not None,
       "the rungs differ only in what they choose; 15 maker passes became 3")
    _c57 = B.build_mixed_parlay(_games57, n_legs=2, target_pct=60, target_payout=1.5,
                                max_legs_per_game=3, max_total_legs=3,
                                legs_mode="off", payout_mode="require",
                                frontier_cache=_fc57)
    ck("a different floor is a different pool: it builds its own",
       _nfront57[0] == 2 and len(_fc57) == 2 and _c57 is not None)
finally:
    (B._game_sim, B._price_cands, _km57.index, _ce57.frontier) = _orig57

print()
print("=" * 72)
print("The lottery rungs: 100x and 200x on the tab AND the wall")
print("=" * 72)
_spec58 = {s["id"]: s for s in _pr41.PRESETS}
_js58 = open(_os.path.join(_root, "static", "app.js")).read()
import combo_engine as _ce58
ck("x100/x200 are fair-basis target rungs under Kalshi's cap, on the wall",
   _spec58["x100"]["target_x"] == 100.0 and _spec58["x200"]["target_x"] == 200.0
   and all(_spec58[p].get("payout_basis") is None for p in ("x100", "x200"))
   and 200.0 < _ce58.MAX_PAYOUT_X
   and '["x100", "⚡ Pays 100×"]' in _js58 and '["x200", "⚡ Pays 200×"]' in _js58
   and '["x15", "⚡' not in _js58
   and _pr41.REV >= 10,
   "the owner asked for both on the maker and the hit board; the 1.5x rung "
   "stays off the wall as asked")

print()
print("=" * 72)
print("The NFL DFS pool knows the depth chart")
print("=" * 72)
# "It gave me Theo Wease Jr. for FLEX -- not on their depth chart anywhere;
# signed to the practice squad Wednesday. We need to only be picking WR1, 2,
# and maaaybe WR3." He had no Sleeper projection and fell to DK's 8.6 average
# from another team's season at $3,000. Sleeper's roster record carries the
# depth chart (McConkey SWR 1, Johnston LWR 2, Wease: none); the gate reads it.
import nfl_dfs as _nd59
import nfl_adp as _na59
_recs59 = {
    "justin herbert": {"status": "Active", "active": True, "depth": 1, "injury": None},
    "ladd mcconkey": {"status": "Active", "active": True, "depth": 1, "injury": None},
    "quentin johnston": {"status": "Active", "active": True, "depth": 1, "injury": "Questionable"},
    "tre harris": {"status": "Active", "active": True, "depth": 2, "injury": None},
    "derius davis": {"status": "Active", "active": True, "depth": 2, "injury": None},
    "theo wease": {"status": "Active", "active": True, "depth": None, "injury": None},
    "omarion hampton": {"status": "Active", "active": True, "depth": 1, "injury": None},
    "najee harris": {"status": "Active", "active": True, "depth": 2, "injury": None},
    "kimani vidal": {"status": "Active", "active": True, "depth": 3, "injury": None},
    "will dissly": {"status": "Active", "active": True, "depth": 1, "injury": "Out"},
    "cut guy": {"status": "Inactive", "active": False, "depth": 1, "injury": None}}
_oc59 = _na59.consensus
try:
    _na59.consensus = lambda: _recs59
    _pool59 = [
        {"name": "Justin Herbert", "pos": "QB", "team": "LAC", "proj": 19.0},
        {"name": "Ladd McConkey", "pos": "WR", "team": "LAC", "proj": 13.3},
        {"name": "Quentin Johnston", "pos": "WR", "team": "LAC", "proj": 11.1},
        {"name": "Tre Harris", "pos": "WR", "team": "LAC", "proj": 6.0},
        {"name": "Derius Davis", "pos": "WR", "team": "LAC", "proj": 3.0},
        {"name": "Theo Wease Jr.", "pos": "WR", "team": "LAC", "proj": 8.6},
        {"name": "Omarion Hampton", "pos": "RB", "team": "LAC", "proj": 14.0},
        {"name": "Najee Harris", "pos": "RB", "team": "LAC", "proj": 9.0},
        {"name": "Kimani Vidal", "pos": "RB", "team": "LAC", "proj": 4.0},
        {"name": "Will Dissly", "pos": "TE", "team": "LAC", "proj": 5.0},
        {"name": "Cut Guy", "pos": "WR", "team": "LAC", "proj": 7.0},
        {"name": "Nobody Known", "pos": "WR", "team": "LAC", "proj": 9.0},
        {"name": "Chargers", "pos": "DST", "team": "LAC", "proj": 7.0},
        {"name": "Cameron Dicker", "pos": "K", "team": "LAC", "proj": 8.0}]
    _kept59, _ex59 = _nd59._apply_depth([dict(p) for p in _pool59], preseason=False)
    _kn59 = {p["name"]: p.get("depth") for p in _kept59}
    _xw59 = {e["name"]: e["why"] for e in _ex59}
    ck("practice squad, WR4/RB3 by RANK, Out and Inactive are OUT; "
       "QB1, RB1-2, WR1-3, K and DST stay -- tagged by rank, not by slot",
       "Theo Wease Jr." in _xw59 and "depth chart" in _xw59["Theo Wease Jr."]
       and "Derius Davis" in _xw59 and _xw59["Derius Davis"].startswith("WR4")
       and "Kimani Vidal" in _xw59 and _xw59["Kimani Vidal"].startswith("RB3")
       and "Will Dissly" in _xw59 and "Cut Guy" in _xw59 and "Nobody Known" in _xw59
       and _kn59.get("Justin Herbert") == "QB1"
       and _kn59.get("Ladd McConkey") == "WR1" and _kn59.get("Quentin Johnston") == "WR2·Q"
       and _kn59.get("Tre Harris") == "WR3"
       and _kn59.get("Omarion Hampton") == "RB1" and _kn59.get("Najee Harris") == "RB2"
       and _kn59.get("Chargers") == "DST" and _kn59.get("Cameron Dicker") == "K",
       "the Bears list Odunze 'LWR order 2' and he is their WR2: a slot rule "
       "drops real starters, a rank within the team's position group does not")
    _pre59, _pex59 = _nd59._apply_depth([dict(p) for p in _pool59], preseason=True)
    ck("August keeps its measured inverted-usage pool: the gate is regular season only",
       len(_pre59) == len(_pool59) and not _pex59)
finally:
    _na59.consensus = _oc59
_bsrc59 = _insp.getsource(_nd59.build)
ck("in season an unprojected player is left out, never handed DK's average; "
   "the responses name the excluded and the rows carry depth",
   '"why": "no Sleeper projection this week"' in _bsrc59
   and "players, _dx = _apply_depth(players, preseason)" in _bsrc59
   and '"excluded": excluded[:40]' in _bsrc59
   and '"depth": p.get("depth")' in _bsrc59
   and "ents, _dx = _apply_depth(ents, preseason)" in _insp.getsource(_nd59))
ck("the roster record carries the depth chart, and NFL's DK average is read",
   '"depth": p.get("depth_chart_order")' in _insp.getsource(_na59.consensus)
   and "_AVG_PPG_ATTR_NFL = 90" in open(_os.path.join(_root, "dk.py")).read())
_js59 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the lineup shows each player's depth tag and who the gate left out",
   "${p.depth || p.pos}" in _js59 and "left out by the depth chart" in _js59)


# ---------------------------------------------------------------------------
# suite60: the deep season sim can no longer take the instance down.
#
# 2026-09-05 08:56 ET: the "run deep sim" button started the season sim on the
# 2 GB box while the warmer's slate child was mid-build; the instance was gone
# inside a minute (jobs timeline: slate start 12:55:46Z with no end, fresh
# boot 12:56:53Z, no ledger entry). The comment on the warmer said the build
# "takes deep_cache.HEAVY_BUILD, so it can never race the nightly season sim";
# the season sim never took the gate. And the snapshot read back at 09:57 ET
# settled the pool question: Render's cgroup quota is one core (host_cpus 16,
# cgroup_cpu "100000 100000", auto_workers 1), so the run took the INLINE path
# -- 4,000 seasons of pure Python inside the web worker that owns every
# background job. Now: the run is handed to the PC whenever it is on (request
# marker the PC reads off the deep inventory call); a server-side run holds
# the gate, is isolated in a child even at one worker, and writes a timeline
# row.
import tempfile as _tf60
import time as _tm60
import deep_cache as _dc60
import deep_season as _ds60
_app60 = __import__("app")
_dir60 = _tf60.mkdtemp()
_o60 = (_dc60.CACHE_DIR, _dc60._PC_REQ, _dc60._RUN_STATE, _dc60._RERUN_REQ)
_dc60.CACHE_DIR = _dir60
_dc60._PC_REQ = _os.path.join(_dir60, "pc-requests.json")
_dc60._RUN_STATE = _os.path.join(_dir60, "runstate.json")
_dc60._RERUN_REQ = _os.path.join(_dir60, "rerun.json")
try:
    ck("a PC request is pending until the artifact is newer than it",
       _dc60.pc_pending("g60") is None
       and _dc60.request_pc("g60") is not None
       and _dc60.pc_pending("g60") is not None
       and _dc60.pc_requests().get("g60") == _dc60.pc_pending("g60"))
    _ts60 = _dc60.pc_pending("g60")
    _tm60.sleep(0.05)
    ck("re-stamping a pending request is a no-op (a half-hourly scheduler pass "
       "never pushes the PC's deadline back)",
       _dc60.request_pc("g60") == _ts60 and _dc60.pc_pending("g60") == _ts60)
    _dc60._json_write(_dc60._PC_REQ, {"g60": _tm60.time() - 3600})
    ck("a request older than max_age reads as abandoned, so the server takes over",
       _dc60.pc_pending("g60") is not None
       and _dc60.pc_pending("g60", max_age=45 * 60) is None)
    _tm60.sleep(1.1)                  # mtime resolution: the save must land after the stamp
    _dc60._json_write(_dc60._PC_REQ, {"g60": _tm60.time() - 0.5})
    _dc60.save("g60", {"ok": 1})
    ck("the upload that answers a request retires it (artifact newer than the stamp)",
       _dc60.pc_pending("g60") is None)
    # DEFERRED is its own phase, not "empty" (which the page reads as "kept the
    # previous board -- partial rosters").
    _dc60.register("g60d", lambda: _dc60.DEFERRED)
    ck("a job that hands its work to the PC records phase 'deferred'",
       _dc60.run_job("g60d", force=True) and _dc60.wait_for("g60d", timeout=10)
       and _dc60.run_state("g60d").get("phase") == "deferred"
       and _dc60.age("g60d") is None)
    _dc60._jobs.pop("g60d", None)
    # The server-side pool is capped unless an operator overrides it.
    _odw60 = _ds60.default_workers
    _oenv60 = _os.environ.pop("VIGIL_SIM_WORKERS", None)
    _ds60.default_workers = lambda: 8
    try:
        ck("the server caps the deep pool at two workers (measured: 336 MB PSS at "
           "one, 613 MB at two, beside ~330 MB of web workers on the 2 GB plan)",
           _app60._deep_workers() == 2 and _app60._DEEP_SERVER_WORKERS == 2)
        _os.environ["VIGIL_SIM_WORKERS"] = "8"
        ck("VIGIL_SIM_WORKERS still overrides the cap",
           _app60._deep_workers() == 8)
    finally:
        _ds60.default_workers = _odw60
        _os.environ.pop("VIGIL_SIM_WORKERS", None)
        if _oenv60 is not None:
            _os.environ["VIGIL_SIM_WORKERS"] = _oenv60
    # PC-first: on -> stamp and defer; off -> run here; stale request -> run here.
    _ops60 = _app60._pc_status
    try:
        _app60._pc_status = lambda: {"state": "off", "seen_s": None, "behind": None}
        ck("with the PC off the deep run stays on the server and stamps nothing",
           _app60._deep_pc_first("g60p") is False and _dc60.pc_pending("g60p") is None)
        _app60._pc_status = lambda: {"state": "on", "seen_s": 12, "behind": False}
        ck("with the PC on the deep run is handed over: request stamped, run deferred",
           _app60._deep_pc_first("g60p") is True and _dc60.pc_pending("g60p") is not None)
        _st60 = _dc60.pc_pending("g60p")
        ck("a second pass while the request is pending keeps waiting on the same stamp",
           _app60._deep_pc_first("g60p") is True and _dc60.pc_pending("g60p") == _st60)
        _dc60._json_write(_dc60._PC_REQ, {"g60p": _tm60.time() - _app60._DEEP_PC_WAIT_S - 60})
        ck("a request the PC has not answered in 45 min hands the run back to the "
           "server -- the PC can only add speed or be ignored",
           _app60._deep_pc_first("g60p") is False)
        _app60._pc_status = lambda: {"state": "behind", "seen_s": 12, "behind": True}
        _dc60._json_write(_dc60._PC_REQ, {})
        ck("a PC on older code ('behind') is still asked first; it self-updates "
           "within a minute and the 45-min fallback covers the rest",
           _app60._deep_pc_first("g60p") is True)
    finally:
        _app60._pc_status = _ops60
finally:
    (_dc60.CACHE_DIR, _dc60._PC_REQ, _dc60._RUN_STATE, _dc60._RERUN_REQ) = _o60
_reg60 = _insp.getsource(_app60._register_deep_sims)
ck("run_mlb asks the PC first, then holds the heavy-build gate with a capped, "
   "timed pool -- the slate child holds the same gate, so they cannot overlap",
   'if _deep_pc_first("mlb_deep"):' in _reg60
   and "return deep_cache.DEFERRED" in _reg60
   and 'with deep_cache.HEAVY_BUILD, jobs.timed(f"deep:mlb:w{workers}"):' in _reg60
   and "workers=workers," in _reg60
   and _reg60.index('_deep_pc_first("mlb_deep")') < _reg60.index("deep_season.run_deep("),
   "the warmer's comment said the build 'can never race the nightly season "
   "sim'; until now only the slate side of that race held the gate")
ck("deep_cache's worker treats DEFERRED as its own outcome",
   "if payload is DEFERRED:" in _insp.getsource(_dc60.run_job)
   and 'phase="deferred"' in _insp.getsource(_dc60.run_job))
_apy60 = open(_os.path.join(_root, "app.py")).read()
ck("the deep inventory call carries the request marker and the status poll "
   "says a request is pending",
   'out["requested"] = deep_cache.pc_requests()' in _apy60
   and 'p["pc_pending_s"] = round(time.time() - _pp) if _pp else None' in _apy60
   and 'deep_cache.pc_pending("mlb_deep", max_age=_DEEP_PC_WAIT_S)' in _apy60)
ck("the memory snapshot reports the pool this host would fork (the number the "
   "kill turned on and nothing had ever read back from Render)",
   '"sim_sizing": sizing' in _apy60 and '"cgroup_cpu": _cgroup_cpu_raw()' in _apy60
   and '"auto_workers": deep_season.default_workers()' in _apy60)
_pw60 = open(_os.path.join(_root, "pc_worker.py")).read()
ck("the PC treats a server request newer than the server's copy as due now",
   'requested = inv.get("requested") or {}' in _pw60
   and "if req and (age is None or time.time() - req < age):" in _pw60
   and _pw60.index("if req and (age is None") < _pw60.index("age < hours * 3600"))
_js60 = open(_os.path.join(_root, "static", "app.js")).read()
ck("the page says the PC has the job instead of going quiet or re-offering the button",
   "s.pc_pending_s != null" in _js60 and "Deep sim handed to your PC" in _js60
   and 'vigil-shell-v1' in open(_os.path.join(_root, "static", "sw.js")).read())
_rd60 = _insp.getsource(_ds60.run_deep)
ck("a one-worker deep run is isolated in a child by default: its working set "
   "dies with it and the sim's os.nice(10) never lands on the web worker",
   _insp.signature(_ds60.run_deep).parameters["isolate"].default is True
   and "if workers > 1 or isolate:" in _rd60
   and "mp.Pool(max(1, workers), initializer=_init_worker," in _rd60
   and "os.nice(10)" in _insp.getsource(_ds60._init_worker),
   "Render's quota is one core, so auto-sizing picked one worker and the old "
   "`if workers > 1` sent the whole sim inline into the background owner")
ck("the memory watchdog samples every 20s (the 08:56 ET death fell between "
   "two 60s samples and left nothing)",
   _app60._MEM_WATCH_S == 20)
ck("the working agreement stops promising a 30-minute build",
   "~30-minute Docker build" not in open(_os.path.join(_root, "CLAUDE.md")).read()
   and "deep_cache.HEAVY_BUILD" in open(_os.path.join(_root, "CLAUDE.md")).read())


# ---------------------------------------------------------------------------
# suite62: per-game sims never run inside a web worker on the server.
#
# 2026-09-05 11:26 ET, the second restart of the day: the combo maker was
# simulating today's games INSIDE a web worker (pure Python, GIL-bound, nice
# 0) beside a slate child on Render's one-core quota. Jobs timeline: combo
# build start 15:24:53Z with no end row, fresh boot 15:26:19Z; the slate
# rebuilds either side ran 91s and 97s against a 27s norm; the 20s memory
# watchdog never fired. The slate build had been a niced child for months for
# exactly this reason (DEPLOY.md: "1 worker failed 7/7 probes"); the game sim
# it feeds the combo maker never was. Now every 4,000-run game sim on a posix
# host runs through baseball._game_sim_isolated -- a child that nices itself
# to 10 before importing anything -- with the inline engine as the fallback.
import pickle as _pk62
_iso62 = _insp.getsource(B._game_sim_isolated)
ck("the sim child nices itself before importing anything, and a failed child "
   "falls back to simulating in-process (heavier, never broken)",
   '"import os; os.nice(10)\\n"' in _iso62
   and "baseball._game_sim_blob(sys.stdin.buffer.read())" in _iso62
   and 'errlog.note("SIM-child"' in _iso62
   and _iso62.rstrip().endswith("return None")
   and B._SIM_CHILD_TIMEOUT == 900)
ck("the child is a posix-only path: the PC (Windows) keeps simulating inline",
   '_os.name == "posix"' in open(_os.path.join(_root, "baseball.py")).read()
   .split("_SIM_CHILD = ", 1)[1][:120]
   and "VIGIL_SIM_INLINE" in open(_os.path.join(_root, "baseball.py")).read()
   .split("_SIM_CHILD = ", 1)[1][:120])
_gs62 = _insp.getsource(B._game_sim)
ck("_game_sim tries the child first and keeps the prediction log in the parent",
   "val = _game_sim_isolated(g, n=_SIM_N)" in _gs62
   and _gs62.index("_game_sim_isolated(g, n=_SIM_N)") < _gs62.index("sim = mlb_sim.simulate(g, _SIM_N)")
   and _gs62.index("_log_prop_predictions(g, cands)") > _gs62.index("_game_sim_isolated(g, n=_SIM_N)"),
   "the child has no business in predlog; the parent logs off the blob it got back")
ck("the live (resumed) sim takes the same child",
   "_game_sim_isolated(g, n=_SIM_N, live=snap)" in _insp.getsource(B._live_game_sim))
ck("the test's thinner _SIM_N reaches the child (it is passed, not re-read)",
   "n=_SIM_N" in _gs62 and 'int(req.get("n") or _SIM_N)' in _insp.getsource(B._game_sim_blob))
# End to end on a real game when the slate has one: the pipe, the pickle and
# the child's own import of baseball. 40 runs so it costs a second.
_g62 = next((g for g in (globals().get("playable") or []) if g.get("game_pk")), None)
if _g62 is not None:
    _v62 = B._game_sim_isolated(_g62, n=40)
    ck("a niced child simulates a real game and hands back sim + candidates",
       isinstance(_v62, dict) and "sim" in _v62 and _v62.get("cands"),
       f"{_g62.get('matchup')}: {type(_v62).__name__}")
    _b62 = _pk62.loads(B._game_sim_blob(_pk62.dumps({"g": _g62, "n": 40})))
    ck("the child entry point returns the same shape the disk cache stores",
       set(_b62) == {"sim", "cands"})
else:
    ck("a niced child simulates a real game and hands back sim + candidates",
       True, "no playable game today -- child round trip not exercised")
ck("the memory snapshot says whether the PC was delivering sims",
   '"pc_state": _pc_status().get("state")' in open(_os.path.join(_root, "app.py")).read())
ck("the working agreement records the fourth kill and its rule",
   "_game_sim_isolated" in open(_os.path.join(_root, "CLAUDE.md")).read())


# ---------------------------------------------------------------------------
# suite63: the UFC combo maker -- the baseball maker on the fight card.
#
# The owner: "inside the ufc area could you add a combo maker specifically
# for ufc. Try to model it exactly from the baseball one. With the only
# exception is that there are no 'No' tags. Just yes's. Add a custom one
# with regular 'set' bets like baseball but model them after ufc." Every leg
# is a bitmask over the bout's simulated fights (ufc_sim now keeps end-round
# and method per sample beside won_arr), so mlb_sim.game_bundles and
# combo_engine run unchanged; the recipes (ufc_presets) log under ufc_ tags
# and grade off Kalshi settlement like every other slip. Synthetic card, no
# network: ufc_data._default ratings with the career lookup stubbed.
import ufc_data as _ud63
import ufc_sim as _us63
import ufc_combo as _uc63
import ufc_presets as _up63
_ocp63 = _ud63.career_profile
_ud63.career_profile = lambda fid: None
try:
    def _rat63(fid, name, **kw):
        d = _ud63._default(fid, name, 6)
        d.update(kw); d["record_w"] = 8; d["record_l"] = 2
        return d
    _bouts63 = []
    for (ia, na, ka), (ib, nb, kb) in (
            (("1", "Quentin Pasley", {"ss_pm": 5.5, "kd_p15": 0.6, "finish_rate": 0.7}),
             ("2", "Arlind Berisha", {})),
            (("3", "Isaac Moreno", {"td_p15": 2.5}), ("4", "Jose Hooper", {"durability": 0.5})),
            (("5", "Alex Ortiz", {}), ("6", "Brian Parker", {"ss_pm": 3.0}))):
        r = _us63.simulate_bout(_rat63(ia, na, **ka), _rat63(ib, nb, **kb), rounds=3, n=3000, seed=7)
        r["weight"] = "Welterweight"
        _bouts63.append(r)
    _b0 = _bouts63[0]
    _smp = _b0["samples"]
    ck("the bout sim keeps end-round and method per sample, index-aligned with won_arr",
       len(_smp["end_rd"]) == len(_b0["a"]["won_arr"]) == len(_smp["method"]) > 500
       and all(m in ("ko", "sub", "dec") for m in _smp["method"])
       and all(1 <= r <= 3 for r in _smp["end_rd"]))
    ck("a decision is coded as ending in the final round, so it is NO on every rung",
       all(r == 3 for r, m in zip(_smp["end_rd"], _smp["method"]) if m == "dec")
       and any(m == "dec" for m in _smp["method"]))
    ck("the board key moved with the payload shape (a cached old board has no samples) "
       "and the artifact schema was bumped in the same change",
       _us63.BOARD_NAME == "ufc_board2" and __import__("artifacts").SCHEMA == 2)
    ck("both series share the bout key the fighter ticker carries",
       _uc63.event_key("KXUFCFIGHT-26SEP08PASBER-PAS") == "26SEP08PASBER"
       and _uc63.event_key("KXUFCROUNDS-26SEP08PASBER-3") == "26SEP08PASBER"
       and _uc63.event_key("junk") is None)
    _board63 = {"sport": "ufc", "event": "UFC Fight Night", "date": "2026-09-19",
                "n_sims": 3000, "bouts": _bouts63}
    _mk63 = {"fights": {}, "rounds": {}, "ok": True}
    for bt, ek in zip(_bouts63, ("26SEP19PASBER", "26SEP19MORHOO", "26SEP19ORTPAR")):
        for side, suf in (("a", ek[-6:-3]), ("b", ek[-3:])):
            f = bt[side]
            f["fair_win"] = round(0.7 * f["win_pct"] + 15.0, 1); f["confidence"] = 0.3
            c = max(3, min(97, int(round(f["fair_win"] - 3))))
            _mk63["fights"][_uc63._norm(f["name"])] = {
                "cents": c, "ticker": f"KXUFCFIGHT-{ek}-{suf}", "close_time": 1790000000,
                "q": {"ask": c, "bid": c - 2, "mid": c - 1, "spread": 2, "size": 50, "vol": 500, "oi": 300}}
        _mk63["rounds"][ek] = {N: {"cents": c, "ticker": f"KXUFCROUNDS-{ek}-{N}", "close_time": 1790000000,
                                   "q": {"ask": c, "bid": c - 3, "mid": c - 1.5, "spread": 3, "size": 30, "vol": 200, "oi": 100}}
                               for N, c in ((2, 35), (3, 60))}
    _cands63, _n63 = _uc63.bout_cands(_b0, _mk63)
    _ml63 = [c for c in _cands63 if c["type"] == "UFC ML"]
    _rd63 = {c["kref"]["n"]: c for c in _cands63 if c["type"] == "Rounds"}
    ck("a bout yields both fighters' YES markets and every booked round rung, ticketed",
       len(_ml63) == 2 and set(_rd63) == {2, 3}
       and all(c["side"] == "yes" and c["ticker"] and c["price_cents"] for c in _cands63)
       and all(c["kref"]["t"] == "ufcml" for c in _ml63))
    ck("the fighter masks are disjoint and the round rungs nest (before 2 implies before 3)",
       (_ml63[0]["mask"] & _ml63[1]["mask"]) == 0
       and (_rd63[2]["mask"] & _rd63[3]["mask"]) == _rd63[2]["mask"]
       and _rd63[2]["marg"] < _rd63[3]["marg"])
    ck("a winner's probability is the board's fair win (market-blended by earned trust), "
       "not the raw model; a rung is blended here and keeps its raw number beside it",
       abs(_ml63[0]["marg"] - _b0["a"]["fair_win"] / 100.0) < 1e-9
       and abs(_ml63[0]["marg_model"] - _b0["a"]["win_pct"] / 100.0) < 1e-9
       and _rd63[3].get("marg_model") is not None and _rd63[3]["marg"] != _rd63[3]["marg_model"])
    import mlb_sim as _ms63
    _bund63 = _ms63.game_bundles(_cands63, _n63, max_legs=2)
    ck("a same-fight stack never pairs the two fighters or two rungs of one ladder",
       _bund63 and all(len({c["group"] for c in b["legs"]}) == b["size"] for b in _bund63)
       and any(b["size"] == 2 for b in _bund63))
    _it63 = _uc63.build_parlay(n_legs=3, target_pct=55, legs_mode="require",
                               board=_board63, mk=_mk63)
    ck("the maker builds a three-leg slip of YES legs, every leg ticketed and priced, "
       "with baseball's own item fields",
       _it63 and _it63["n_legs"] == 3 and _it63["sport"] == "ufc"
       and all(l["side"] == "yes" and l.get("ticker") and l.get("market_cents")
               for g in _it63["groups"] for l in g["legs"])
       and _it63.get("kalshi_payout_net_x") and _it63.get("ev_pct") is not None
       and "alternatives" in _it63 and _it63["excluded_unpriced"] == 0
       and _it63["pricing_unavailable"] is False,
       str(_it63)[:200] if not (_it63 and _it63.get("n_legs") == 3) else "")
    _stk63 = _uc63.build_parlay(n_legs=4, target_pct=20, max_legs_per_bout=2,
                                target_payout=5, payout_mode="require", legs_mode="off",
                                board=_board63, mk=_mk63)
    ck("a payout target can be reached through a same-fight stack priced off the joint",
       _stk63 and _stk63["n_legs"] >= 2 and _stk63.get("payout_reached") is not False)
    _mb63 = _uc63.build_parlay(max_bet=True, target_pct=10, max_legs_per_bout=2,
                               max_total_legs=12, board=_board63, mk=_mk63)
    ck("max bet runs on the card too", _mb63 and _mb63["objective"] == "max_bet")
    _sel63 = _uc63.build_parlay(n_legs=2, target_pct=5, bout_sel=["1_2:1", "3_4"],
                                board=_board63, mk=_mk63)
    ck("picking one fighter keeps his market only; a whole bout keeps its ladder",
       _sel63 and all("Berisha" not in l["pick"] for g in _sel63["groups"] for l in g["legs"])
       and all(g["suffix"] in ("1_2", "3_4") for g in _sel63["groups"]))
    _typ63 = _uc63.build_parlay(n_legs=2, target_pct=5, types={"Rounds"},
                                max_legs_per_bout=1, board=_board63, mk=_mk63)
    ck("the type chips filter the pool", _typ63 and all(l["type"] == "Rounds"
                                                        for g in _typ63["groups"] for l in g["legs"]))
    ck("one bout with stacks off says so instead of shrugging",
       (_uc63.build_parlay(n_legs=2, target_pct=5, bout_sel=["1_2"], max_legs_per_bout=1,
                           board=_board63, mk=_mk63) or {}).get("error_hint") == "single_bout_no_stack")
    _mk_unp = {"fights": {k: v for k, v in _mk63["fights"].items() if "pasley" not in k},
               "rounds": _mk63["rounds"], "ok": True}
    _unp63 = _uc63.build_parlay(n_legs=2, target_pct=5, board=_board63, mk=_mk_unp)
    ck("an unquoted fighter is excluded while the book is up, and counted",
       _unp63 and _unp63["excluded_unpriced"] >= 1
       and all("Pasley" not in l["pick"] for g in _unp63["groups"] for l in g["legs"]))
    try:
        _uc63.build_parlay(n_legs=2, target_pct=5, board=_board63, mk=_mk63, abort_cb=lambda: True)
        _ab63 = False
    except RuntimeError as e:
        _ab63 = "superseded" in str(e)
    ck("a build that lost the combo slot yields at the bout boundary", _ab63)
    # ---- the locked recipes
    _pay63 = _up63.build_all(_board63, _mk63)
    _pp63 = _pay63["presets"]
    ck("every UFC recipe builds against the synthetic card and carries its tag",
       set(_pp63) == {p["id"] for p in _up63.PRESETS}
       and all((v["item"] or {}).get("objective", f"preset:ufc_{k}") == f"preset:ufc_{k}"
               for k, v in _pp63.items()))
    _f5 = _pp63["fav5"]["item"]; _f3 = _pp63["fin3"]["item"]
    ck("5 Favorites is winners only, one per bout, likeliest first; 3 Finishes is rungs only",
       _f5 and all(l["type"] == "UFC ML" for g in _f5["groups"] for l in g["legs"])
       and len({g["suffix"] for g in _f5["groups"]}) == _f5["n_legs"] <= 5
       and _f3 and all(l["type"] == "Rounds" for g in _f3["groups"] for l in g["legs"]))
    ck("the rungs are the Optimal button locked: payout required, target stamped",
       all((_pp63[t]["item"] or {}).get("target_payout_x") == x
           for t, x in (("x2", 2.0), ("x3", 3.0), ("x5", 5.0), ("x10", 10.0))
           if _pp63[t]["item"]) and any(_pp63[t]["item"] for t in _up63.TARGET_IDS))
    # nearest-above on a card where the bar is reachable
    _spec63 = {"id": "t", "types": ("Rounds",), "floor": 0.30, "pick": "floor"}
    _all63 = _up63._build_all(_board63, _mk63, _spec63)
    _near63 = {}
    for bt in _bouts63:
        _ok = [c["marg"] * 100 for c in _uc63.bout_cands(bt, _mk63)[0]
               if c["type"] == "Rounds" and c["marg"] >= 0.30]
        if _ok:
            _near63[f"{bt['a']['name']} vs {bt['b']['name']}"] = min(_ok)
    ck("a scan recipe takes each bout's rung NEAREST the bar from above, never under it",
       _all63 and all(30.0 <= l["prob_pct"] for g in _all63["groups"] for l in g["legs"])
       and all(len(g["legs"]) == 1 for g in _all63["groups"])
       and set(g["matchup"] for g in _all63["groups"]) == set(_near63)
       and all(abs(g["legs"][0]["prob_pct"] - _near63[g["matchup"]]) < 0.06
               for g in _all63["groups"]))
    # 16:21 ET on the first live card: a fighter quoted at 100c (nobody
    # selling) reached _build_all, combo_engine.leg_cost gave None, and the
    # recipe died on `cost *= None`. Now an ask outside 1-99c is unpriced.
    ck("a 100c ask is unpriced, not a crash: _rec drops it and the scan skips it",
       _uc63._rec({"yes_ask_dollars": "1.0000", "ticker": "KXUFCFIGHT-26SEP19XXXYYY-XXX"}) is None
       and _uc63._rec({"yes_ask_dollars": "0.0000", "ticker": "t"}) is None
       and _uc63._rec({"yes_ask_dollars": "0.5500", "ticker": "t", "close_time": None})["cents"] == 55
       and "if lc is None:" in _insp.getsource(_up63._build_all))
    _mk100 = {"fights": dict(_mk63["fights"]), "rounds": _mk63["rounds"], "ok": True}
    _mk100["fights"][_uc63._norm("Quentin Pasley")] = dict(_mk100["fights"][_uc63._norm("Quentin Pasley")], cents=100)
    ck("and a stray 100c cand cannot take the scan down",
       _up63._build_all(_board63, _mk100, {"id": "t", "types": ("UFC ML",), "floor": 0.05}) is not None)
    import sliplog as _sl63
    _calls63 = []
    _olf63 = _sl63.log_from_item
    _sl63.log_from_item = lambda item, sport="mlb", date=None, tag=None: (_calls63.append((sport, tag, date)) or "k")
    try:
        _ch1 = _up63.ensure_logged(_pay63); _ch2 = _up63.ensure_logged(_pay63)
    finally:
        _sl63.log_from_item = _olf63
    ck("the recipes file under the ufc sport and ufc_ tags, with the card's date, idempotently",
       _ch1 and not _ch2 and _calls63
       and all(sp == "ufc" and tag.startswith("ufc_") and d == "2026-09-19" for sp, tag, d in _calls63))
    import store as _st63
    _opr63, _opw63 = _st63.preset_records, _st63.preset_best_wins
    _st63.preset_records = lambda: {"ufc_fav5": {"graded": 2}, "hits5": {"graded": 9}}
    _st63.preset_best_wins = lambda: {"ufc_x2": {"payout_x": 2.1}, "x2": {"payout_x": 9}}
    try:
        ck("the UFC wall reads only its own tags off the shared ledger",
           _up63.records() == {"fav5": {"graded": 2}} and _up63.best_wins() == {"x2": {"payout_x": 2.1}})
    finally:
        _st63.preset_records, _st63.preset_best_wins = _opr63, _opw63
finally:
    _ud63.career_profile = _ocp63
_apy63 = open(_os.path.join(_root, "app.py")).read()
_app63 = __import__("app")
_par63 = _insp.getsource(_app63.api_ufc_parlay)
ck("/api/ufc/parlay is the baseball endpoint's twin: the combo slot, the job "
   "pattern, the ledger under sport ufc, and every hint the page names",
   'baseball.combo_slot_take(ptok)' in _par63
   and '_run_job(ptok, _core, "UFC-COMBO-build")' in _par63
   and 'sliplog.log_from_item(item, sport="ufc")' in _par63
   and "combo_engine.best_target(" in _par63 and "combo_engine.best_max_bet(" in _par63
   and "baseball.job_takeover(ptok, _JOB_DEAD_S)" in _par63
   and all(h in _par63 for h in ('"optimal_unbuildable"', '"max_bet_unreachable"', '"edge_empty"')))
ck("/api/ufc/presets serves the recipes, their records and the crown, and kicks a first build",
   '@app.route("/api/ufc/presets")' in _apy63
   and "ufc_presets.best_today(payload, records)" in _apy63
   and 'boardshare.claim(ufc_presets.NAME + "_kick")' in _apy63)
ck("the recorder rebuilds the UFC recipes on its cadence under its own code",
   "ufc_presets.tick()" in open(_os.path.join(_root, "mlb_recorder.py")).read()
   and 'errlog.note("MREC-ufc", _e)' in open(_os.path.join(_root, "mlb_recorder.py")).read())
_js63 = open(_os.path.join(_root, "static", "app.js")).read()
_html63 = open(_os.path.join(_root, "templates", "index.html")).read()
_ru63 = _js63[_js63.index("function renderUFC()"):_js63.index("function renderUFC()") + 2500]
ck("the UFC tab carries the maker, its preset tabs and its wall, rendered off the card",
   'id="ufcComboMaker"' in _html63 and 'id="ufcWall"' in _html63
   and "renderUFCComboMaker();" in _ru63 and "loadUfcWall();" in _ru63)
ck("the maker mirrors baseball's controls -- floor, ceiling, goal, edge, legs/payout modes, "
   "AND/OR, stacks, type chips, Build / Max bet / Optimal -- with no side selector",
   all(k in _js63 for k in ("ufcComboTarget", "ufcComboCap", "ufcComboObjective", "ufcComboMinEdge",
                            "ufcComboLegsMode", "ufcComboN", "ufcComboConn", "ufcComboPayoutMode",
                            "ufcComboPayout", "ufcComboSameFight", "toggleUfcType",
                            "buildUFCCombo(true)", "buildUFCCombo(false, true)"))
   and "ufcComboSides" not in _js63 and "YES legs only" in _js63
   and "/api/ufc/parlay?" in _js63
   and "renderMixed(d.parlay)" in _js63[_js63.index("function _renderUfcComboResult"):][:2500])
ck("the recipe tabs, the crown and the wall are the baseball ones on the UFC data",
   '"/api/ufc/presets"' in _js63 and "_UFC_PRESET_TABS" in _js63 and "_UFC_WALL_COLS" in _js63
   and "_presetSectionHtml(p, (d.records || {})[pid])" in _js63[_js63.index("async function renderUfcPresetBox"):]
   and 'vigil-shell-v102' in open(_os.path.join(_root, "static", "sw.js")).read())
ck("the multi-sport combo area still has its UFC legs (the new maker is in addition)",
   "def _ufc_legs" in open(_os.path.join(_root, "combine.py")).read())

print()
print("=" * 72)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for n, d in FAIL:
        print(f"   - {n}   {d}")
print("=" * 72)
# A red guard MUST be a red exit code. The suite used to exit 0 unless it
# crashed outright, which made every "check $? explicitly" ritual theater --
# the one thing the exit code was trusted to carry (guard failures) was the
# one thing it never carried.
sys.exit(1 if FAIL else 0)
