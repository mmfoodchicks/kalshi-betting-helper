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
    for k in SPORT_KEYS:
        if k in cats:
            legs += _sport_legs(k)
    return legs


def _item(combo):
    prob = 1.0; cost = 1.0; priced = True
    for l in combo:
        prob *= l["prob"]
        if l.get("price_cents"):
            cost *= l["price_cents"] / 100.0
        else:
            priced = False
    item = {
        "legs": [{"pick": l["label"], "matchup": l["matchup"], "type": l["type"],
                  "category": l["category"], "prob_pct": round(l["prob"] * 100, 1),
                  "price_cents": l.get("price_cents")} for l in combo],
        "n_legs": len(combo),
        "combined_prob_pct": round(prob * 100, 1),
        "fair_payout_x": round(1 / prob, 2) if prob > 0 else None,
    }
    if priced and cost > 0:
        payout = 1 / cost
        item["parlay_payout_x"] = round(payout, 2)
        item["parlay_cost_cents"] = round(cost * 100, 1)
        item["ev_pct"] = round((prob * payout - 1) * 100, 1)
    return item


def select_legs(by_event, target_pct, n_legs, target_payout=None, max_legs=12):
    """Pick the legs for the combo.

    Payout mode: the target multiplier governs -- reach it with the highest
    combined probability, preferring n_legs and only expanding the count when it
    can't be reached (see parlay.payout_combo). Returns (legs, target, meta).
    Confidence mode: best leg per event at the target, safest n_legs."""
    target = max(0.05, min(0.97, target_pct / 100.0))
    if target_payout and target_payout > 1:
        import parlay
        # Confidence floor required: only legs >= target are eligible; the
        # selector adds as many qualifying legs as needed to reach the payout.
        groups = []
        for vs in by_event.values():
            ok = [v for v in vs if v["prob"] >= target]
            if ok:
                groups.append(ok)
        res = parlay.payout_combo(groups, n_legs, target_payout, max_legs=max_legs)
        if not res:
            return [], target, None
        return res["legs"], target, res
    chosen = []
    for vs in by_event.values():
        meeting = [v for v in vs if v["prob"] >= target]
        pick = (min(meeting, key=lambda v: v["prob"]) if meeting
                else max(vs, key=lambda v: v["prob"]))
        pick = dict(pick)
        pick["meets"] = bool(meeting)
        chosen.append(pick)
    chosen.sort(key=lambda v: (v["meets"], v["prob"]), reverse=True)
    n = max(2, min(n_legs, len(chosen)))
    return chosen[:n], target, None


def build(cats, n_legs, target_pct, date, season, target_payout=None, max_legs=12):
    legs = gather(cats, date, season)
    counts = {}
    for l in legs:
        counts[l["category"]] = counts.get(l["category"], 0) + 1
    if not legs:
        return {"combo": None, "counts": counts}
    by_event = {}
    for l in legs:
        by_event.setdefault(l["event_id"], []).append(l)
    chosen, target, meta = select_legs(by_event, target_pct, n_legs, target_payout,
                                       max_legs=max_legs)
    if not chosen:
        return {"combo": None, "counts": counts}
    item = _item(chosen)
    item["target_pct"] = round(target * 100, 1)
    item["legs_meeting_target"] = sum(1 for v in chosen if v.get("meets"))
    if target_payout:
        item["target_payout_x"] = target_payout
        if meta:
            item["payout_reached"] = meta["reached"]
            item["legs_used"] = meta["n_used"]
            item["requested_legs"] = meta["requested_legs"]
            item["expanded"] = meta["expanded"]
        else:
            item["payout_reached"] = (item.get("fair_payout_x") or 0) >= target_payout
    return {"combo": item, "counts": counts}
