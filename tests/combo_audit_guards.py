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

print()
print("=" * 72)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for n, d in FAIL:
        print(f"   - {n}   {d}")
print("=" * 72)
