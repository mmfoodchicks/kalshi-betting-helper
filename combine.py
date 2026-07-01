"""Cross-category combo maker: build a single parlay spanning MLB, daily crypto,
UFC, tennis, golf, World Cup soccer, and WNBA.

Each leg needs a probability. Where we have our own model we use it (MLB props /
moneyline; crypto fair value from the GBM model); for the other sports we use
the de-vig'd market probability (a legitimate probability, just not an
independent edge). One leg per event keeps the legs ~independent so the combined
chance is a clean product. The builder tunes each leg's line to a target
confidence, exactly like the baseball combo maker.

Crypto deliberately uses only DAILY markets — 15-minute and hourly are too
volatile/efficient to belong in a multi-leg parlay.
"""

import time as _t

import baseball
import kalshi
import odds
import prices
import sports

CRYPTO_COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]
# Only the categories Kalshi actually allows in multi-leg parlays.
SPORT_KEYS = {"ufc", "tennis", "wta", "golf", "soccer", "wnba"}

CATEGORIES = {
    "mlb": "⚾ Baseball",
    "crypto": "⚡ Crypto (daily)",
    "soccer": "⚽ World Cup",
    "wnba": "🏀 WNBA",
    "golf": "⛳ PGA",
    "tennis": "🎾 Tennis (ATP)",
    "wta": "🎾 Tennis (WTA)",
    "ufc": "🥊 UFC",
}


def _mlb_legs(date, season):
    legs = []
    try:
        games = baseball.analyze_slate(date, season)
    except Exception:
        return legs
    for g in games:
        for v in baseball._game_variants(g):
            legs.append({"category": "⚾ MLB", "event_id": f"mlb_{g['game_pk']}",
                         "label": v["label"], "matchup": v["matchup"], "prob": v["prob"],
                         "price_cents": v.get("price_cents"), "type": v["type"]})
    return legs


def _crypto_legs():
    legs = []
    for coin in CRYPTO_COINS:
        try:
            ms = kalshi.get_open_markets(coin, "daily")
            if not ms:
                continue
            spot = prices.get_spot(coin)
            candles = prices.get_candles(coin, granularity=60)
        except Exception:
            continue
        now = _t.time()
        for m in ms:
            if not m.get("yes_ask") or m["yes_ask"] >= 100 or not m.get("yes_bid"):
                continue
            mins = max(0.0, (m["close_time"] - now) / 60.0) if m["close_time"] else 0.0
            sig = odds.kalshi_signal(spot, candles, m, mins)
            if sig["fair_yes_cents"] >= sig["fair_no_cents"]:
                side, prob, price = "YES", sig["fair_yes_cents"] / 100.0, m["yes_ask"]
            else:
                side, prob, price = "NO", sig["fair_no_cents"] / 100.0, m["no_ask"]
            # One leg per coin: group by coin so the target-tuner picks the
            # single strike nearest your target (not five deep-ITM ~100% ones).
            legs.append({"category": "⚡ Crypto", "event_id": f"crypto_{coin}",
                         "label": f"{coin} {side}: {m.get('subtitle') or m['ticker']}",
                         "matchup": coin, "prob": prob, "price_cents": price, "type": "Crypto"})
    return legs


def _worldcup_legs():
    """World Cup legs from OUR simulator (model probabilities, not de-vig). One
    event per upcoming match, with candidate legs the target-tuner picks from:
    3-way result, over/under 2.5 goals, both-teams-to-score. Champion futures are
    added as their own low-probability events (useful in payout mode)."""
    legs = []
    try:
        import worldcup
        data = worldcup.board()
    except Exception:
        return legs
    if not data:
        return legs
    cat = "⚽ World Cup"
    for m in data.get("matches", []):
        ev = f"wc_{m['home']}_{m['away']}_{m['date']}"
        mk = m.get("markets") or {}
        w = mk.get("winner") or {}
        matchup = f"{m['home']} v {m['away']}"

        def leg(label, prob_pct, cents, typ):
            legs.append({"category": cat, "event_id": ev, "label": label,
                         "matchup": matchup, "prob": prob_pct / 100.0,
                         "price_cents": cents, "type": typ})
        leg(f"{m['home']} to win", m["p_home"], (w.get("home") or {}).get("cents"), "WC Result")
        leg("Draw", m["p_draw"], (w.get("draw") or {}).get("cents"), "WC Result")
        leg(f"{m['away']} to win", m["p_away"], (w.get("away") or {}).get("cents"), "WC Result")
        leg("Over 2.5 goals", m["over25"], (mk.get("over25") or {}).get("cents"), "WC Total")
        leg("Under 2.5 goals", round(100 - m["over25"], 1), None, "WC Total")
        leg("Both teams to score", m["btts_pct"], (mk.get("btts") or {}).get("cents"), "WC BTTS")
    for t in data.get("teams", []):
        if t["champion_pct"] < 1:
            continue
        cm = (t.get("champion_market") or {}).get("kalshi") or {}
        legs.append({"category": cat, "event_id": f"wc_champ_{t['name']}",
                     "label": f"{t['name']} to win the World Cup", "matchup": "Champion",
                     "prob": t["champion_pct"] / 100.0, "price_cents": cm.get("cents"),
                     "type": "WC Champion"})
    return legs


def _ufc_legs():
    """UFC moneyline legs from OUR fight simulator (model win %, not de-vig). One
    event per bout with both fighters as candidate legs + the live Kalshi price."""
    legs = []
    try:
        import ufc_sim
        import ufc_prices
        board = ufc_sim.board()
        if not board:
            return legs
        ufc_prices.attach(board)
    except Exception:
        return legs
    for bt in board.get("bouts", []):
        ev = f"ufc_{bt['a']['id']}_{bt['b']['id']}"
        matchup = f"{bt['a']['name']} vs {bt['b']['name']}"
        for side in ("a", "b"):
            f = bt[side]
            # confidence-blended fair win (defers to market when history is thin)
            prob = f.get("fair_win", f["win_pct"]) / 100.0
            legs.append({"category": "🥊 UFC", "event_id": ev,
                         "label": f"{f['name']} to win", "matchup": matchup,
                         "prob": prob, "price_cents": f.get("kalshi_cents"),
                         "type": "UFC ML"})
    return legs


def _poisson_over(mean, line):
    """P(X > line) for X ~ Poisson(mean), line a half-integer. Used for the ace
    over/under, whose count is well-modelled as Poisson around the expected total."""
    import math
    if mean <= 0:
        return 0.0
    k = int(math.floor(line)) + 1            # P(X >= k)
    cdf = 0.0
    term = math.exp(-mean)
    for i in range(0, k):
        if i > 0:
            term *= mean / i
        cdf += term
    return max(0.0, min(1.0, 1.0 - cdf))


_TENNIS_CAT = {"ATP": "🎾 Tennis", "WTA": "🎾 Tennis (WTA)",
               "ITF": "🎾 Tennis (ITF)", "ITF-W": "🎾 Tennis (ITF-W)"}


def _tennis_legs(tours=("ATP", "WTA", "ITF", "ITF-W")):
    """Tennis legs from OUR match simulator (model probabilities, not de-vig). One
    event per match with both players as winner legs, plus the coherent derived
    markets -- total games over/under, total sets, aces -- the sim prices together,
    so a tennis parlay stays internally consistent. Each leg carries a short `why`
    so the recommended combos can explain themselves."""
    legs = []
    try:
        import tennis_prices
        board = tennis_prices.board()
        if not board:
            return legs
    except Exception:
        return legs
    for m in board.get("matches", []):
        if m.get("tour") not in tours:
            continue
        a, b = m["a"], m["b"]
        if a.get("fair_win") is None and a.get("model_win") is None:
            continue
        ev = f"tennis_{m['event']}"
        cat = _TENNIS_CAT.get(m["tour"], "🎾 Tennis")
        mu = f"{a['name']} vs {b['name']}"
        # one short reason for the match -> the favoured side's winner leg
        insights = m.get("insights") or []
        why = insights[0] if insights else None

        def leg(label, prob_pct, cents, typ, why=None):
            if prob_pct is None:
                return
            d = {"category": cat, "event_id": ev, "label": label, "matchup": mu,
                 "prob": max(0.01, min(0.99, prob_pct / 100.0)),
                 "price_cents": cents, "type": typ}
            if why:
                d["why"] = why
            legs.append(d)

        fav_is_a = (a.get("fair_win") or a.get("model_win") or 0) >= (b.get("fair_win") or b.get("model_win") or 0)
        # winner legs use the confidence-blended fair win% (falls back to model)
        leg(f"{a['name']} to win", a.get("fair_win") if a.get("fair_win") is not None else a.get("model_win"),
            a.get("cents"), "Match", why if fav_is_a else None)
        leg(f"{b['name']} to win", b.get("fair_win") if b.get("fair_win") is not None else b.get("model_win"),
            b.get("cents"), "Match", why if not fav_is_a else None)
        # total sets / goes the distance (Bo3) -- model only
        ts = m.get("total_sets") or {}
        if m.get("best_of") == 3 and ts.get("3") is not None:
            leg("Match goes 3 sets", ts["3"], None, "Sets")
            leg("Match in straight sets", round(100 - ts["3"], 1), None, "Sets")
        # total games over/under at the line nearest the model mean
        ladder = m.get("games_ladder") or {}
        mean_g = m.get("mean_games")
        if ladder and mean_g is not None:
            line = min(ladder.keys(), key=lambda s: abs(float(s) - mean_g))
            p_over = ladder[line]
            leg(f"Over {line} games", p_over, None, "Games")
            leg(f"Under {line} games", round(100 - p_over, 1), None, "Games")
        # total aces over (Poisson around the model mean)
        ace_mean = m.get("aces_total")
        if ace_mean and ace_mean > 4:
            aline = round(ace_mean) - 0.5
            leg(f"Over {aline} aces", round(100 * _poisson_over(ace_mean, aline), 1), None, "Aces")
    return legs


def _sport_legs(key):
    legs = []
    try:
        events = sports.get_events(key)  # returns a list of events
    except Exception:
        return legs
    label = sports.SPORTS[key]["label"]
    for ev in events:
        for o in ev["outcomes"]:
            if o.get("fair_pct") is None:
                continue
            legs.append({"category": label, "event_id": ev["event"],
                         "label": o["name"], "matchup": ev.get("title", ""),
                         "prob": o["fair_pct"] / 100.0, "price_cents": o.get("yes_ask"),
                         "type": label})
    return legs


def gather(cats, date, season):
    legs = []
    if "mlb" in cats:
        legs += _mlb_legs(date, season)
    if "crypto" in cats:
        legs += _crypto_legs()
    if "soccer" in cats:
        legs += _worldcup_legs()                 # our World Cup model, not de-vig
    if "ufc" in cats:
        legs += _ufc_legs()                      # our UFC fight model, not de-vig
    if "tennis" in cats or "wta" in cats:        # our tennis match model, not de-vig
        tours = []
        if "tennis" in cats:                      # men: charted ATP + live ITF men
            tours += ["ATP", "ITF"]
        if "wta" in cats:                         # women: charted WTA + live ITF women
            tours += ["WTA", "ITF-W"]
        legs += _tennis_legs(tuple(tours))
    for k in SPORT_KEYS:
        if k in ("soccer", "ufc", "tennis", "wta") or k not in cats:
            continue
        legs += _sport_legs(k)
    return legs


def _fee_cents(cents):
    """Expected Kalshi taker fee per contract at `cents`: 0.07 x p x (1-p) x 100."""
    p = cents / 100.0
    return 7.0 * p * (1.0 - p)


def _item(combo):
    prob = 1.0; cost = 1.0; cost_net = 1.0; priced = True
    for l in combo:
        prob *= l["prob"]
        if l.get("price_cents"):
            c = l["price_cents"]
            cost *= c / 100.0
            cost_net *= min(99.9, c + _fee_cents(c)) / 100.0   # each leg pays a taker fee
        else:
            priced = False
    item = {
        "legs": [{"pick": l["label"], "matchup": l["matchup"], "type": l["type"],
                  "category": l["category"], "prob_pct": round(l["prob"] * 100, 1),
                  "price_cents": l.get("price_cents"), "why": l.get("why")} for l in combo],
        "n_legs": len(combo),
        "combined_prob_pct": round(prob * 100, 1),
        "fair_payout_x": round(1 / prob, 2) if prob > 0 else None,
    }
    if priced and cost > 0:
        payout = 1 / cost
        item["parlay_payout_x"] = round(payout, 2)
        item["parlay_cost_cents"] = round(cost * 100, 1)
        item["ev_pct"] = round((prob * payout - 1) * 100, 1)
        # EV net of Kalshi's per-leg taker fees -- the number you actually bank.
        item["ev_net_pct"] = round((prob * (1 / cost_net) - 1) * 100, 1)
    return item


def _assemble(by_event, target, legs_target, payout_target,
              legs_mode="prefer", payout_mode="off", conn="or", max_legs=12):
    """Pick the legs under two optional, combinable targets -- a leg count and a
    fair payout -- exactly like the baseball mixed maker. Each target is "require"
    (hard), "prefer" (a recommendation that nudges but never blocks), or "off";
    when combined as "require", `conn` ('and'/'or') says whether both must hold or
    just one.

    Method: each event contributes at most one leg (legs ≥ the per-leg floor are
    eligible). A DP builds the most-likely parlay at every leg count and payout
    level (the frontier); we then pick the state that best satisfies the active
    targets, breaking ties toward the safest parlay -- or, when chasing an unmet
    payout, toward the bigger payout. Returns (legs, meta) or ([], None)."""
    import math
    # eligible legs per event (those clearing the per-leg confidence floor)
    groups = []
    for vs in by_event.values():
        ok = [v for v in vs if v["prob"] >= target]
        if ok:
            groups.append(ok)
    # best-effort: if the floor is too strict to field 2 legs, fall back to each
    # event's best leg so the maker still returns something (legs flagged below).
    if len(groups) < 2:
        groups = []
        for vs in by_event.values():
            if vs:
                groups.append([max(vs, key=lambda v: v["prob"])])
    if len(groups) < 2:
        return [], None

    RES = 0.05
    dp = {(0, 0): (0.0, [])}                    # (n_legs, bucket) -> (-log prob, legs)
    for legs in groups:
        nd = dict(dp)                           # skipping this event is allowed
        for (nlegs, _bk), (w, sel) in dp.items():
            for l in legs:
                nl = nlegs + 1
                if nl > max_legs:
                    continue
                nw = w - math.log(max(0.01, l["prob"]))
                key = (nl, int(nw / RES))
                if key not in nd or nw < nd[key][0]:
                    nd[key] = (nw, sel + [l])
        dp = nd
    states = []
    for (nlegs, _bk), (w, sel) in dp.items():
        if nlegs < 2 or not sel:
            continue
        prob = math.exp(-w)
        states.append({"legs": nlegs, "prob": prob,
                       "payout": (1.0 / prob if prob > 0 else None), "sel": sel})
    if not states:
        return [], None

    want_legs = legs_mode in ("require", "prefer")
    want_payout = payout_mode in ("require", "prefer") and bool(payout_target and payout_target > 1)
    X = max(2, min(legs_target or 2, max_legs))
    Y = payout_target or 0
    meets_legs = lambda s: s["legs"] == X
    meets_payout = lambda s: s["payout"] is not None and s["payout"] >= Y

    reqs = []
    if legs_mode == "require":
        reqs.append(meets_legs)
    if payout_mode == "require" and want_payout:
        reqs.append(meets_payout)
    feasible, hard_ok = states, True
    if reqs:
        combine_fn = all if conn == "and" else any
        feas = [s for s in states if combine_fn(r(s) for r in reqs)]
        if feas:
            feasible = feas
        else:
            hard_ok = False                     # unsatisfiable -> best effort

    def rank(s):
        primary = (1 if want_payout and meets_payout(s) else 0) \
                  + (1 if want_legs and meets_legs(s) else 0)
        secondary = s["payout"] if (want_payout and not meets_payout(s)) else s["prob"]
        return (primary, secondary)

    best = max(feasible, key=rank)
    chosen = [dict(l, meets=(l["prob"] >= target)) for l in best["sel"]]
    return chosen, {"state": best, "X": X, "Y": Y, "want_legs": want_legs,
                    "want_payout": want_payout, "meets_legs": meets_legs(best),
                    "meets_payout": meets_payout(best), "hard_ok": hard_ok}


def _leg_edge(l):
    """Edge in cents = our prob - the Kalshi ask, or None if the leg isn't priced."""
    return (l["prob"] * 100 - l["price_cents"]) if l.get("price_cents") else None


def recommended(cats, date, season, max_legs=12):
    """Auto-built recommended parlays from the checked sports -- the same idea as
    the baseball tab's safest / best-value / best combos, across categories:

      - safest:     the most-likely legs (one per event), best chance to cash.
      - best_value: only legs where OUR model beats the Kalshi price (+EV), by
                    descending edge -- the parlay the market is mispricing.
      - best:       the all-arounder -- best-edge legs among reasonably likely
                    ones (>= 55%), balancing payout and hit rate.
    """
    legs = gather(cats, date, season)
    counts = {}
    for l in legs:
        counts[l["category"]] = counts.get(l["category"], 0) + 1
    if not legs:
        return {"safest": None, "best": None, "best_value": None, "counts": counts}
    by_event = {}
    for l in legs:
        by_event.setdefault(l["event_id"], []).append(l)

    def pack(chosen, **extra):
        if len(chosen) < 2:
            return None
        it = _item(chosen)
        it.update(extra)
        tot_edge = sum(_leg_edge(l) or 0 for l in chosen)
        it["total_edge_cents"] = round(tot_edge, 1)
        # a couple of plain-English angles for the combo, from its legs
        reasons = []
        for l in chosen:
            if l.get("why") and l["why"] not in reasons:
                reasons.append(l["why"])
        it["reasons"] = reasons[:3]
        return it

    # SAFEST: the highest-probability leg per event, the safest few.
    safe = sorted((max(vs, key=lambda v: v["prob"]) for vs in by_event.values()),
                  key=lambda l: -l["prob"])
    safest = pack(safe[:min(4, len(safe))])

    # BEST VALUE: best +EV leg per event, only positive-edge, by descending edge.
    val = []
    for vs in by_event.values():
        priced = [v for v in vs if v.get("price_cents")]
        if priced:
            top = max(priced, key=lambda v: _leg_edge(v))
            if (_leg_edge(top) or 0) > 0:
                val.append(top)
    val.sort(key=lambda l: -(_leg_edge(l) or 0))
    best_value = pack(val[:min(5, len(val))])

    # BEST all-arounder: best-edge (then prob) leg per event among >=55% legs.
    pool = []
    for vs in by_event.values():
        ok = [v for v in vs if v["prob"] >= 0.55]
        if ok:
            pool.append(max(ok, key=lambda v: ((_leg_edge(v) or 0), v["prob"])))
    pool.sort(key=lambda v: ((_leg_edge(v) or 0), v["prob"]), reverse=True)
    best = pack(pool[:min(4, len(pool))])

    return {"safest": safest, "best": best, "best_value": best_value, "counts": counts}


def build(cats, n_legs, target_pct, date, season, target_payout=None, max_legs=12,
          legs_mode="prefer", payout_mode=None, conn="or"):
    legs = gather(cats, date, season)
    counts = {}
    for l in legs:
        counts[l["category"]] = counts.get(l["category"], 0) + 1
    if not legs:
        return {"combo": None, "counts": counts}
    # Back-compat default: a payout target with no explicit mode means "require it"
    # (the old behavior was payout-governed when payout > 1).
    if payout_mode is None:
        payout_mode = "require" if (target_payout and target_payout > 1) else "off"
    target = max(0.05, min(0.97, target_pct / 100.0))
    by_event = {}
    for l in legs:
        by_event.setdefault(l["event_id"], []).append(l)
    chosen, meta = _assemble(by_event, target, n_legs, target_payout,
                             legs_mode=legs_mode, payout_mode=payout_mode,
                             conn=conn, max_legs=max_legs)
    if not chosen:
        return {"combo": None, "counts": counts}
    item = _item(chosen)
    item["target_pct"] = round(target * 100, 1)
    item["legs_meeting_target"] = sum(1 for v in chosen if v.get("meets"))
    item["legs_used"] = len(chosen)
    item["requested_legs"] = n_legs
    item["legs_mode"] = legs_mode
    item["payout_mode"] = payout_mode
    item["conn"] = conn
    item["hard_ok"] = meta["hard_ok"]
    if meta["want_legs"]:
        item["legs_target"] = meta["X"]
        item["legs_met"] = meta["meets_legs"]
        item["expanded"] = len(chosen) != n_legs
    if meta["want_payout"]:
        item["target_payout_x"] = target_payout
        item["payout_reached"] = meta["meets_payout"]
    return {"combo": item, "counts": counts}
