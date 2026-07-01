"""Multi-sport browser over live Kalshi markets (soccer, tennis, golf, UFC).

These sports don't have a rich free stats API to model from (unlike MLB), so
instead of a fake model we do something genuinely useful and math-sound: pull
the live Kalshi markets and **de-vig** them.

Kalshi's prices for the outcomes of an event sum to more than 100% -- the extra
is the house margin (the "overround"/vig). Normalizing the prices so they sum to
100% gives the **no-vig fair probability**, a sharper estimate of each outcome's
true chance, and the overround tells you how much edge the house is taking.

Everything here is read-only public data; bets can be logged to the ledger.
"""

import kalshi

SPORTS = {
    "soccer": {"label": "⚽ World Cup", "series": ["KXWCGAME"]},
    "tennis": {"label": "🎾 Tennis (ATP)", "series": ["KXATPMATCH"]},
    "wta":    {"label": "🎾 Tennis (WTA)", "series": ["KXWTAMATCH"]},
    "itf":    {"label": "🎾 Tennis (ITF)", "series": ["KXITFMATCH", "KXITFWMATCH"]},
    "golf":   {"label": "⛳ Golf (PGA H2H)", "series": ["KXPGAH2H"]},
    "ufc":    {"label": "🥊 UFC", "series": ["KXUFCFIGHT"]},
    "boxing": {"label": "🥊 Boxing", "series": ["KXBOXINGFIGHT"]},
    "wnba":   {"label": "🏀 WNBA", "series": ["KXWNBAGAME"]},
    "cricket": {"label": "🏏 Cricket (T20)", "series": ["KXT20MATCH"]},
    "cfl":    {"label": "🏈 CFL", "series": ["KXCFLGAME"]},
    "nfl":    {"label": "🏈 NFL", "series": ["KXNFLGAME"]},
    "ncaaf":  {"label": "🏈 College FB", "series": ["KXNCAAFGAME"]},
    "mls":    {"label": "⚽ MLS", "series": ["KXMLSGAME"]},
    "f1":     {"label": "🏎️ F1", "series": ["KXF1"]},
    "nascar": {"label": "🏁 NASCAR", "series": ["KXNASCARRACE"]},
    "motogp": {"label": "🏍️ MotoGP", "series": ["KXMOTOGP"]},
}


def get_events(sport_key, limit=200):
    cfg = SPORTS.get(sport_key)
    if not cfg:
        raise ValueError(f"unknown sport '{sport_key}'")

    events = {}
    for st in cfg["series"]:
        url = f"{kalshi.BASE}/markets?series_ticker={st}&status=open&limit={limit}"
        data = kalshi._get_json(url)
        for m in data.get("markets", []):
            ev = m.get("event_ticker")
            if not ev:
                continue
            e = events.setdefault(ev, {
                "event": ev, "series": st,
                "title": m.get("title") or ev,
                "close_time": kalshi._parse_time(m.get("close_time")),
                "outcomes": [],
            })
            e["outcomes"].append({
                "name": m.get("yes_sub_title") or m.get("subtitle") or m.get("ticker"),
                "ticker": m.get("ticker"),
                "yes_ask": kalshi._cents(m.get("yes_ask_dollars")),
                "yes_bid": kalshi._cents(m.get("yes_bid_dollars")),
                "volume": m.get("volume_fp"),
            })

    out = []
    for e in events.values():
        # De-vig: fair_i = price_i / sum(prices). Sum > 100 => house margin.
        # Price each outcome at the bid-ask MIDPOINT when a bid exists (the ask
        # alone carries half the spread, which on thin books skews the fair %),
        # falling back to the ask on one-sided quotes.
        def mid(o):
            a, b = o.get("yes_ask"), o.get("yes_bid")
            if a is None:
                return None
            return (a + b) / 2.0 if b else a
        mids = {id(o): mid(o) for o in e["outcomes"]}
        total = sum(v for v in mids.values() if v)
        for o in e["outcomes"]:
            v = mids[id(o)]
            o["fair_pct"] = round(100 * v / total, 1) if (total and v) else None
            # Bid-ask spread: how wide/uncertain the quote is. A wide spread means
            # the "fair %" is a guess off a stale quote, not a real tradeable price.
            o["spread"] = (round(o["yes_ask"] - o["yes_bid"], 1)
                           if (o["yes_ask"] is not None and o["yes_bid"] is not None) else None)
        # Overround/arbitrage stay on the ASKS -- that's what you'd actually pay
        # to buy every outcome; the mid-based fair % above is just the estimate.
        ask_total = sum(o["yes_ask"] for o in e["outcomes"] if o["yes_ask"])
        e["overround_pct"] = round(ask_total - 100, 1) if ask_total else None
        # Arbitrage: if the outcome prices sum to < 100¢, buying them all is a
        # guaranteed profit (exactly one pays 100¢). Free money from stale quotes.
        e["arbitrage_pct"] = round(100 - ask_total, 1) if (ask_total and ask_total < 100) else None
        e["outcomes"].sort(key=lambda o: (o["fair_pct"] is None, -(o["fair_pct"] or 0)))
        # Liquidity read for the whole event: is this actually tradeable, or a thin
        # untraded book where the fair % / edge / arbitrage can't be trusted? Based
        # on total contracts traded and the favorite's bid-ask width.
        vol = 0.0
        for o in e["outcomes"]:
            try:
                vol += float(o.get("volume") or 0)
            except (TypeError, ValueError):
                pass
        top = e["outcomes"][0] if e["outcomes"] else None
        top_spread = top.get("spread") if top else None
        if top_spread is None or vol <= 0:
            e["liquidity"] = "none"      # no two-sided quote or no trades at all
        elif top_spread >= 10 or vol < 50:
            e["liquidity"] = "thin"      # wide/lightly-traded: treat numbers as soft
        else:
            e["liquidity"] = "ok"
        e["volume"] = vol
        # "Buy this one": the de-vig favorite (market lean, not an independent edge).
        e["pick"] = {"name": top["name"], "fair_pct": top["fair_pct"], "yes_ask": top["yes_ask"]} \
            if (top and top.get("fair_pct") is not None) else None
        out.append(e)

    out.sort(key=lambda e: (e["close_time"] is None, e["close_time"] or 0))
    return out
