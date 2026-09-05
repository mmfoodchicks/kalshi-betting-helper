"""UFC combo maker: the baseball maker's engine on the fight card.

Same machinery, different sport: every leg is a bitmask over the bout's
simulated fights (ufc_sim.simulate_bout keeps end-round and method per
sample beside each fighter's won_arr), so mlb_sim.game_bundles prices a
same-fight stack -- "Pasley wins AND it ends before round 3" -- off the
joint outcomes the sim actually produced, and combo_engine.frontier /
choose / max_bet / best_target pick the slip exactly as they do for
baseball and football. YES legs only, by request: Kalshi lists one market
per fighter and one "fight ends before round N" rung per bout, so the NO of
a leg is always either the other fighter's own market or a rung the maker
can already reach the other way, and a NO mirror would only duplicate the
board (combine._NO_SKIP_TYPES makes the same call for the moneyline).

Two leg types, both placeable:

  UFC ML   "<fighter> wins"                 KXUFCFIGHT, one market a fighter.
           Probability = ufc_prices' fair win (the model blended toward the
           book by the share it has EARNED on graded results -- backtested
           the raw model loses to the close, weight ~0.05-0.16).
  Rounds   "Fight ends before round N"      KXUFCROUNDS, one rung per N.
           Probability = the sim's finish distribution, market-blended by
           combo_engine.blend_candidates under the "ufc" trust bucket.

A leg with no Kalshi quote is excluded whenever the book is reachable (a
slip you cannot place is not a slip), counted in `excluded_unpriced`, and
the item carries the same fields baseball's does so renderMixed and the
slip ledger treat it identically; sliplog grades a UFC slip off Kalshi
settlement like any other.
"""

import time

import errlog
import kalshi

# Candidate types and their chip labels, in the order the maker shows them.
TYPES = (("UFC ML", "Fight winner"), ("Rounds", "Ends before round N"))

_MKT_TTL = 60
_mkt = {"ts": 0.0, "val": None}


def _norm(s):
    import ufc_prices
    return ufc_prices._norm(s)


def event_key(ticker):
    """The card-and-bout key both series share: KXUFCFIGHT-26SEP08PASBER-PAS
    and KXUFCROUNDS-26SEP08PASBER-3 are the same bout, so the fighter market
    is the bridge from a name to that bout's round ladder."""
    parts = (ticker or "").split("-")
    return parts[1] if len(parts) >= 3 else None


def _series(series):
    out, cursor = [], ""
    for _ in range(6):
        url = f"{kalshi.BASE}/markets?series_ticker={series}&status=open&limit=1000"
        if cursor:
            url += f"&cursor={cursor}"
        d = kalshi._get_json(url)
        out.extend(d.get("markets", []))
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    return out


def _rec(m):
    """One market as the maker needs it: the ask, the ticket, and the full
    quote the blend weighs (kalshi_mlb's quote shape, YES side)."""
    import kalshi_mlb
    cents = kalshi._cents(m.get("yes_ask_dollars"))
    if cents is None:
        return None
    return {"cents": cents, "ticker": m.get("ticker"),
            "close_time": kalshi._parse_time(m.get("close_time")),
            "q": kalshi_mlb._q(m, "yes")}


def markets():
    """{"fights": {normalized fighter: rec}, "rounds": {event_key: {N: rec}},
    "ok": whether Kalshi answered}. Cached a minute: a build re-reads it per
    floor and the exchange rate-limits the whole app."""
    now = time.time()
    if _mkt["val"] is not None and now - _mkt["ts"] < _MKT_TTL:
        return _mkt["val"]
    fights, rounds, ok = {}, {}, False
    try:
        for m in _series("KXUFCFIGHT"):
            rec = _rec(m)
            nm = _norm(m.get("yes_sub_title"))
            if rec and nm:
                fights[nm] = rec
                fights.setdefault(nm.split()[-1], rec)
        ok = True
    except Exception as e:
        errlog.note("UFCC-fights", e)
    try:
        for m in _series("KXUFCROUNDS"):
            rec = _rec(m)
            ek = event_key(m.get("ticker"))
            try:
                n = int((m.get("ticker") or "").rsplit("-", 1)[-1])
            except ValueError:
                continue
            if rec and ek:
                rounds.setdefault(ek, {})[n] = rec
    except Exception as e:
        errlog.note("UFCC-rounds", e)
    val = {"fights": fights, "rounds": rounds, "ok": ok}
    _mkt["ts"], _mkt["val"] = now, val
    return val


def bout_key(bt):
    return f"{bt['a']['id']}_{bt['b']['id']}"


def _mask(seq, pred):
    m = 0
    for i, v in enumerate(seq):
        if pred(v):
            m |= (1 << i)
    return m


def bout_cands(bt, mk):
    """Candidate legs for one bout in mlb_sim's candidate shape, and the
    sample count their masks are drawn over. ML legs are priced and given the
    board's fair win; round rungs come from the sim's end-round samples and are
    market-blended here. An older board without samples still yields the ML
    legs (won_arr has always been there) and simply has no round legs."""
    import combo_engine
    a, b = bt["a"], bt["b"]
    won_a, won_b = a.get("won_arr") or [], b.get("won_arr") or []
    n = min(len(won_a), len(won_b))
    if n < 50:
        return [], 0
    smp = bt.get("samples") or {}
    end_rd = (smp.get("end_rd") or [])[:n]
    fights = mk.get("fights") or {}
    cands, ekey = [], None
    for f, won in ((a, won_a[:n]), (b, won_b[:n])):
        nm = _norm(f.get("name"))
        rec = fights.get(nm) or fights.get(nm.split()[-1] if nm else "")
        if rec and not ekey:
            ekey = event_key(rec.get("ticker"))
        p_model = (f.get("win_pct") or 0) / 100.0
        p_fair = (f.get("fair_win") if f.get("fair_win") is not None
                  else f.get("win_pct") or 0) / 100.0
        if not (0.0 < p_fair < 1.0):
            continue
        cands.append({"type": "UFC ML", "label": f"{f['name']} wins",
                      "mask": _mask(won, lambda v: v), "marg": p_fair,
                      "marg_model": p_model, "group": "ML",
                      "model_pct": round(p_model * 100, 1),
                      "kref": {"t": "ufcml", "fid": f.get("id"), "name": f.get("name")},
                      "side": "yes", "side_fid": f.get("id"),
                      "price_cents": rec["cents"] if rec else None,
                      "ticker": rec["ticker"] if rec else None,
                      "close_time": rec["close_time"] if rec else None,
                      "fillable": combo_engine.tradeable(rec["q"]) if rec else False,
                      "model_weight": f.get("confidence"),
                      "sim_avg": None, "avg_unit": None})
    rounds = (mk.get("rounds") or {}).get(ekey) or {}
    rd_cands, quotes = [], {}
    if end_rd and len(end_rd) == n:
        for N in sorted(rounds):
            if N < 2 or N > int(bt.get("rounds") or 3):
                continue
            rec = rounds[N]
            mask = _mask(end_rd, lambda v, N=N: v < N)
            p = bin(mask).count("1") / n
            if not (0.02 <= p <= 0.98):
                continue
            c = {"type": "Rounds", "label": f"Fight ends before round {N}",
                 "mask": mask, "marg": p, "group": "Rounds",
                 "model_pct": round(p * 100, 1),
                 "kref": {"t": "ufcrd", "n": N}, "side": "yes", "side_fid": None,
                 "price_cents": rec["cents"], "ticker": rec["ticker"],
                 "close_time": rec["close_time"],
                 "sim_avg": None, "avg_unit": None}
            rd_cands.append(c)
            quotes[id(c)] = rec["q"]
        if rd_cands:
            combo_engine.blend_candidates(rd_cands, quotes, sport="ufc")
    return cands + rd_cands, n


def _board():
    import ufc_sim
    import ufc_prices
    board = ufc_sim.board()
    if not board or not board.get("bouts"):
        return None
    try:
        ufc_prices.attach(board)
    except Exception as e:
        errlog.note("UFCC-attach", e)
    return board


def _kalshi_summary(item):
    """The slip's real Kalshi payout off its legs' asks, fees in -- the same
    five fields baseball._kalshi_payout stamps, so kalshiPayout() renders it."""
    payout, net, priced, total = 1.0, 1.0, 0, 0
    for grp in item.get("groups") or []:
        for leg in grp.get("legs") or []:
            total += 1
            c = leg.get("market_cents")
            if c and 0 < c < 100:
                leg["market_payout_x"] = round(100.0 / c, 2)
                payout *= 100.0 / c
                net *= 100.0 / min(99.9, c + kalshi.taker_fee_cents(c))
                priced += 1
            else:
                leg["market_payout_x"] = None
    item.update({"kalshi_payout_x": round(payout, 2) if priced else None,
                 "kalshi_payout_net_x": round(net, 2) if priced else None,
                 "kalshi_priced": priced, "kalshi_total_legs": total,
                 "kalshi_full": priced == total and priced > 0})


def build_parlay(n_legs=3, target_pct=55, cap_pct=None, target_payout=0,
                 max_legs_per_bout=2, max_total_legs=8, legs_mode="prefer",
                 payout_mode="off", conn="or", objective="balanced", types=None,
                 bout_sel=None, max_bet=False, cap_x=None, abort_cb=None,
                 min_edge_c=None, payout_basis="fair", board=None, mk=None):
    """One parlay across the card, priced against Kalshi -- the fight twin of
    baseball.build_mixed_parlay, knob for knob: a per-leg floor (and optional
    ceiling), leg-count and payout targets each require/prefer/off joined by
    `conn`, an objective on the frontier, a type filter, a bout/fighter
    selection, edge mode, max bet and `payout_basis`. Returns the mixed item,
    None when nothing qualifies, or {"error_hint": ...} for a card the caller
    should explain rather than shrug at."""
    import combo_engine
    import mlb_sim
    board = board or _board()
    if not board:
        return {"error_hint": "no_card"}
    mk = mk or markets()
    priced_ok = bool(mk.get("ok"))
    floor = max(0.02, min(0.97, target_pct / 100.0))
    ceil, banded = 1.0, False
    if cap_pct is not None and cap_pct / 100.0 > floor:
        ceil, banded = min(1.0, cap_pct / 100.0), True
    sel_map = {}
    for tok in (bout_sel or ()):
        base, _, fid = str(tok).partition(":")
        if base:
            sel_map[base] = fid or True
    games_bundles, by_ref = [], {}
    excluded_unpriced = excluded_no_edge = 0
    for bt in board["bouts"]:
        if abort_cb is not None and abort_cb():
            raise RuntimeError("superseded by a newer build")
        key = bout_key(bt)
        fighter_only = None
        if sel_map:
            v = sel_map.get(key)
            if v is None:
                continue
            if v is not True:
                fighter_only = v
        cands, n = bout_cands(bt, mk)
        if types:
            cands = [c for c in cands if c["type"] in types]
        if fighter_only:
            # One fighter picked: his own market only. The round ladder is a
            # bout-level leg and drops with the other fighter, matching how
            # baseball drops totals when one club is selected.
            cands = [c for c in cands if c.get("side_fid") == fighter_only]
        if priced_ok:
            n_all = len(cands)
            cands = [c for c in cands if c.get("price_cents")]
            excluded_unpriced += n_all - len(cands)
        if min_edge_c is not None:
            n_all = len(cands)
            cands = [c for c in cands
                     if c.get("price_cents")
                     and (c.get("marg_model") if c.get("marg_model") is not None
                          else c["marg"]) * 100.0 - c["price_cents"] >= min_edge_c]
            excluded_no_edge += n_all - len(cands)
        cands = [c for c in cands if floor <= c["marg"] <= ceil]
        if max_bet:
            cands = [c for c in cands
                     if combo_engine.stackable(c["marg"], c.get("price_cents"))]
        if not cands:
            continue
        for c in cands:
            by_ref[(key, c["label"])] = c
        depth = max(1, min(max_legs_per_bout, max(n_legs, 2), max_total_legs))
        bundles = mlb_sim.game_bundles(cands, n, max_legs=depth)
        if bundles:
            matchup = f"{bt['a']['name']} vs {bt['b']['name']}"
            games_bundles.append((matchup, bundles, key))
    if not games_bundles:
        return None
    if len(games_bundles) < 2 and max_legs_per_bout <= 1:
        return {"error_hint": "single_bout_no_stack",
                "n_bouts_available": len(games_bundles)}
    _dp = combo_engine.dp_legs(
        n_legs, "off" if max_bet else legs_mode, max_total_legs,
        payout_mode="require" if max_bet else payout_mode)
    states = combo_engine.frontier(games_bundles, max_total_legs=_dp, net=True)
    if max_bet:
        targets = {}
        best, meta = combo_engine.max_bet(states, cap=cap_x)
    else:
        targets = {"legs_target": n_legs, "payout_target": target_payout,
                   "legs_mode": legs_mode, "payout_mode": payout_mode, "conn": conn}
        if payout_basis == "market":
            targets["payout_basis"] = "market"
        best, meta = combo_engine.choose(states, objective=objective, **targets)
    if not best:
        return None
    item = mlb_sim._mixed_item(best["sel"], games_bundles,
                               None if max_bet else
                               (target_payout if payout_mode != "off" else None))
    # Tickets on every leg, so the slip logs and grades like a baseball slip.
    for grp in item.get("groups") or []:
        for leg in grp.get("legs") or []:
            c = by_ref.get((grp.get("suffix"), leg.get("pick"))) or {}
            leg["ticker"] = c.get("ticker")
            leg["close_time"] = c.get("close_time")
    for k, v in meta.items():
        if k != "objective" and v is not None:
            item[k] = v
    item["objective"] = "max_bet" if max_bet else objective
    if payout_basis == "market":
        item["payout_basis"] = "market"
    item["legs_target"] = None if max_bet else (n_legs if legs_mode != "off" else None)
    if max_bet:
        item["payout_reached"] = meta.get("cap_reached")
        item["target_payout_x"] = None
    item["sport"] = "ufc"
    item["event"] = board.get("event")
    item["date"] = board.get("date")
    item["n_sims"] = board.get("n_sims")
    item["excluded_unpriced"] = excluded_unpriced
    item["excluded_no_edge"] = excluded_no_edge
    item["min_edge_c"] = min_edge_c
    item["pricing_unavailable"] = not priced_ok
    item["leg_floor_pct"] = round(floor * 100, 1)
    item["leg_cap_pct"] = round(ceil * 100, 1) if banded else None
    item["net_fees"] = True
    item["cost_x"] = round(best["cost"], 4)
    item["market_payout_x"] = round(best["payout"], 2) if best["payout"] else None
    item["ev_pct"] = round(best["ev"] * 100, 1) if best["ev"] is not None else None
    item["kelly_pct"] = round(combo_engine.kelly(best["prob"], best["cost"]) * 100, 2)
    item["priced_frac"] = round(best["priced_frac"], 2)
    item["priced_legs"] = best["priced"]
    if not max_bet:
        item["alternatives"] = combo_engine.compare(states, best, **targets)
    _kalshi_summary(item)
    return item
