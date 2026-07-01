"""Cross-sport Best Bets: every positive net-of-fee edge the app can find,
ranked on one screen.

Pulls from each model we own -- the MLB slate (moneylines + props via the Edge
Finder), MLB season futures (deep engine when a run is cached), the UFC fight
simulator, the tennis match simulator, daily crypto fair value -- plus any
outright arbitrage (outcome asks summing under 100¢) on the Kalshi sports books.

Every row is fee-aware: net_edge = model % - ask - Kalshi's taker fee, i.e. the
edge you'd actually bank. Sources are best-effort and independently degradable;
`sources` reports what loaded so the UI can be honest about coverage.
"""

import time
import datetime as _dt


def _fee(cents):
    p = cents / 100.0
    return 7.0 * p * (1.0 - p)


def _row(sport, kind, pick, matchup, our_pct, cents, trust, note=None):
    if cents is None or not (0 < cents < 100) or our_pct is None:
        return None
    edge = our_pct - cents
    net = round(edge - _fee(cents), 1)
    return {"sport": sport, "kind": kind, "pick": pick, "matchup": matchup,
            "our_pct": round(our_pct, 1), "cents": round(cents, 1),
            "payout_x": round(100.0 / cents, 2),
            "edge": round(edge, 1), "net_edge": net,
            "trust": trust, "note": note}


def _mlb_rows(date, season, sims):
    import baseball
    games = baseball.analyze_slate(date, season)
    fe = baseball.find_edges(games, n_sims=sims, min_edge=3.0, top_n=120)
    lean = {t for t, s in (fe.get("summary") or {}).items() if s.get("lean")}
    rows = []
    for r in fe["edges"]:
        if r["edge"] <= 0 or r.get("net_edge", 0) < 1.5 or r["confidence"] == "low":
            continue
        trust, note = r["confidence"], None
        if r["type"] in lean:
            # A whole market leaning one way is usually OUR bias, not free money.
            trust, note = "low", f"whole {r['type']} market leans one way — likely model bias"
        row = _row("⚾ MLB", r["type"], r["pick"], r["matchup"],
                   r["our_pct"], r["market_cents"], trust, note)
        if row:
            rows.append(row)
    return rows, len(fe["edges"])


def _mlb_futures_rows(season):
    import season_sim
    out = season_sim.futures_edges(season)
    rows = []
    for r in (out.get("rows") or out.get("edges") or []):
        if r.get("thin") or r.get("edge") is None or r["edge"] <= 0:
            continue
        trust = r.get("confidence") if r.get("confidence") in ("high", "med", "low") else "med"
        note = None
        # A 15¢+ "edge" on a futures book is almost never free money: it's a
        # stale/wide quote, or the season sim's tails running overconfident
        # (win-total tails especially). Surface it, but say what it likely is.
        if r["edge"] >= 15:
            trust = "low"
            note = "edge this large on a futures book is usually a stale quote or fat model tails — verify the live ask first"
        row = _row("⚾ MLB futures", r["type"].replace("_", " "), r["label"],
                   f"{r.get('best_book') or 'Kalshi'} · vol {r.get('volume') or 0}",
                   r["model_pct"], r.get("market_cents"), trust, note)
        if row:
            rows.append(row)
    rows.sort(key=lambda x: -x["net_edge"])
    return rows[:8]


def _ufc_rows():
    import ufc_sim
    import ufc_prices
    board = ufc_sim.board()
    if not board:
        return []
    ufc_prices.attach(board)
    rows = []
    for bt in board.get("bouts") or []:
        for side in ("a", "b"):
            f = bt[side]
            cents = f.get("kalshi_cents")
            fair = f.get("fair_win", f.get("win_pct"))
            if cents is None or fair is None:
                continue
            trust = "low" if (f.get("debut") or f.get("thin")) else "med"
            note = "sparse UFC history — rating heavily shrunk" if trust == "low" else None
            row = _row("🥊 UFC", "ML", f"{f['name']} to win",
                       f"{bt['a']['name']} vs {bt['b']['name']}",
                       fair, cents, trust, note)
            if row:
                rows.append(row)
    return rows


def _tennis_rows():
    import tennis_prices
    board = tennis_prices.board()
    rows = []
    for m in (board or {}).get("matches") or []:
        for side in ("a", "b"):
            p = m[side]
            fair = p.get("fair_win") if p.get("fair_win") is not None else p.get("model_win")
            row = _row("🎾 Tennis", m.get("tour", "Match"), f"{p['name']} to win",
                       f"{m['a']['name']} vs {m['b']['name']}",
                       fair, p.get("cents"), "med")
            if row:
                rows.append(row)
    return rows


def _crypto_rows():
    import combine
    rows = []
    for l in combine._crypto_legs():
        row = _row("⚡ Crypto", "Daily", l["label"], l["matchup"],
                   l["prob"] * 100.0, l.get("price_cents"), "med")
        if row:
            rows.append(row)
    rows.sort(key=lambda x: -x["net_edge"])
    return rows[:5]


def _arb_rows():
    """Outright arbitrage on the Kalshi sports books: outcome asks summing under
    100¢ mean buying every outcome locks a profit. Rare, small, real."""
    import sports
    rows = []
    for key in ("wnba", "golf", "soccer"):
        try:
            for ev in sports.get_events(key) or []:
                arb = ev.get("arbitrage_pct")
                if arb and arb >= 1.0 and not ev.get("thin"):
                    rows.append({"sport": "🔒 Arbitrage", "kind": sports.SPORTS[key]["label"],
                                 "pick": f"Buy ALL outcomes: {ev.get('title')}",
                                 "matchup": f"asks sum to {round(100 - arb, 1)}¢",
                                 "our_pct": 100.0, "cents": round(100 - arb, 1),
                                 "payout_x": None, "edge": round(arb, 1),
                                 "net_edge": round(arb - 2.0, 1),   # ~fees across the legs
                                 "trust": "high", "note": "guaranteed if all fills — check book depth"})
        except Exception:
            continue
    return [r for r in rows if r["net_edge"] > 0]


def board(date=None, season=None, sims=2500):
    date = date or _dt.date.today().isoformat()
    season = season or date[:4]
    rows, sources = [], {}

    def pull(name, fn, *a):
        try:
            got = fn(*a)
            if isinstance(got, tuple):
                got, extra = got
                sources[name] = {"ok": True, "rows": len(got), "scanned": extra}
            else:
                sources[name] = {"ok": True, "rows": len(got)}
            rows.extend(got)
        except Exception as e:
            sources[name] = {"ok": False, "error": str(e)[:80]}

    pull("mlb", _mlb_rows, date, season, sims)
    pull("mlb_futures", _mlb_futures_rows, season)
    pull("ufc", _ufc_rows)
    pull("tennis", _tennis_rows)
    pull("crypto", _crypto_rows)
    pull("arbitrage", _arb_rows)

    keep = [r for r in rows if r["net_edge"] > 0]
    # Soft rows sink below solid ones regardless of size — a "low" +40 is a
    # warning, not a better bet than a "med" +6.
    trust_rank = {"high": 0, "med": 1, "low": 2}
    keep.sort(key=lambda r: (trust_rank.get(r["trust"], 1), -r["net_edge"]))
    # Diversity guards: a single game/fight can flood the board with correlated
    # variants of one opinion (cap 3 per matchup), and one source can drown the
    # rest (cap 8 per sport).
    seen, per_sport, out = {}, {}, []
    for r in keep:
        k = (r["sport"], r["matchup"])
        seen[k] = seen.get(k, 0) + 1
        per_sport[r["sport"]] = per_sport.get(r["sport"], 0) + 1
        if seen[k] <= 3 and per_sport[r["sport"]] <= 8:
            out.append(r)
    return {"date": date, "rows": out[:30], "sources": sources,
            "generated_at": time.time(),
            "n_candidates": len(rows)}
