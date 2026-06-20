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
    "golf":   {"label": "⛳ Golf (PGA H2H)", "series": ["KXPGAH2H"]},
    "ufc":    {"label": "🥊 UFC", "series": ["KXUFCFIGHT"]},
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
        asks = [o["yes_ask"] for o in e["outcomes"] if o["yes_ask"]]
        total = sum(asks)
        for o in e["outcomes"]:
            o["fair_pct"] = round(100 * o["yes_ask"] / total, 1) if (total and o["yes_ask"]) else None
        e["overround_pct"] = round(total - 100, 1) if total else None
        # Arbitrage: if the outcome prices sum to < 100¢, buying them all is a
        # guaranteed profit (exactly one pays 100¢). Free money from stale quotes.
        e["arbitrage_pct"] = round(100 - total, 1) if (total and total < 100) else None
        e["outcomes"].sort(key=lambda o: (o["fair_pct"] is None, -(o["fair_pct"] or 0)))
        out.append(e)

    out.sort(key=lambda e: (e["close_time"] is None, e["close_time"] or 0))
    return out
