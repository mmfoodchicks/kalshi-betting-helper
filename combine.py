"""Cross-category combo maker: build a single parlay spanning MLB, daily crypto,
UFC, tennis and golf.

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

CRYPTO_COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BCH", "LTC", "AVAX", "LINK"]
# Only the categories Kalshi actually allows in multi-leg parlays.
SPORT_KEYS = {"ufc", "tennis", "wta", "golf"}

CATEGORIES = {
    "mlb": "⚾ Baseball",
    "nfl": "🏈 NFL",
    "crypto": "⚡ Crypto (daily)",
    "nba": "🏀 NBA",
    "nhl": "🏒 NHL",
    "golf": "⛳ PGA",
    "tennis": "🎾 Tennis (ATP/WTA)",   # combos: main tours only — see COMBO_TOURS
    "ufc": "🥊 UFC",
}

# The leg TYPES each category can contribute, as [type_value, chip_label]. The
# type_value must match exactly what that category's leg builder stamps on
# `leg["type"]`; the UI shows a chip per type only while its sport is checked, so
# you can't pick a type a selected sport doesn't offer (e.g. "KO/TKO" only when
# UFC is checked). Keep this in lockstep with the _*_legs builders above.
CATEGORY_TYPES = {
    "mlb": [["ML", "Moneyline"], ["Total", "Totals"], ["Run line", "Run line"],
            ["Hit", "Hits"], ["HR", "Home runs"], ["Bases", "Total bases"],
            ["Ks", "Strikeouts"], ["RFI", "1st-inning run"], ["HRR", "H+R+RBI"],
            ["SB", "Stolen bases"]],
    "nfl": [["ML", "Moneyline"], ["Total", "Totals"], ["Pass Yds", "Passing yds"],
            ["Rush Yds", "Rushing yds"], ["Rec Yds", "Receiving yds"],
            ["Receptions", "Receptions"], ["Anytime Td", "Anytime TD"]],
    "crypto": [["Crypto", "Price up/down"]],
    "nba": [["ML", "Moneyline"], ["Spread", "Spread"], ["Total", "Totals"],
            ["Points", "Points"], ["Rebounds", "Rebounds"], ["Assists", "Assists"]],
    "nhl": [["ML", "Moneyline"], ["Spread", "Spread"], ["Total", "Totals"],
            ["Anytime Goal", "Anytime goal"], ["1+ Point", "1+ point"]],
    "golf": [["Make Cut", "Make the cut"], ["Matchup", "Matchups (H2H)"],
             ["Top 10", "Top 10"], ["Outright", "Outright win"]],
    "tennis": [["Match", "Match winner"], ["Sets", "Sets / straight-sets"],
               ["Games", "Total games"], ["Aces", "Total aces"]],
    "ufc": [["UFC ML", "Moneyline"], ["KO/TKO", "KO/TKO"],
            ["Submission", "Submission"], ["Decision", "Decision"]],
}
# Every type the catalog knows about — a leg whose type isn't here is never
# filtered out (so an unmapped type can't silently vanish from a build).
_ALL_TYPES = {tv for lst in CATEGORY_TYPES.values() for tv, _ in lst}

# Kalshi does not allow ITF matches as parlay legs — only the main tours combine.
# The tennis BOARD still prices ITF (it's fine as a single bet); this restriction
# applies to multi-leg combos only, so the maker can't build a slip that Kalshi
# will refuse to accept.
COMBO_TOURS = ("ATP", "WTA")



# Types whose NO side is either meaningless or already covered by a sibling leg.
# ML pairs both teams already; Under is the NO of Over; RFI has no NO market on
# Kalshi (the user confirmed there's no yes/no slot on run-in-first-inning).
_NO_SKIP_TYPES = {"RFI", "ML", "UFC ML", "Crypto", "Outright"}
_NO_SKIP_WORDS = ("under ", "not ", " no ", "does not")


def _no_legs(legs):
    """Mirror each YES leg into its NO side.

    We were only ever offering the YES half of a market. If the model says a
    batter hits 1+ only 36% of the time, then "NO hit" is a 64% leg sitting
    right there on the same Kalshi market — a better parlay leg than most of the
    YES side, and previously invisible to the maker.

    The NO leg keeps the SAME event_id, so the assembler (one leg per event) can
    never put a YES and its own NO in the same slip — they're mutually exclusive
    and would guarantee a loss.

    Price: Kalshi quotes a separate no_ask, which the leg builders don't carry
    through yet. Rather than fake it as 100 - yes_ask (that ignores the spread
    and would overstate EV), an un-plumbed NO leg is priced None — model-only.
    It still contributes its probability to the combo; it just can't claim an
    edge it hasn't verified.
    """
    # Legs whose NO side already exists. MLB builds its own from the sim, priced
    # off Kalshi's real no_ask; mirroring those again would shadow each one with
    # an unpriced duplicate that the assembler might pick instead.
    have_no = {(l.get("event_id"), l.get("label")) for l in legs
               if l.get("side") == "no"}
    out = []
    for l in legs:
        typ = l.get("type") or ""
        if typ in _NO_SKIP_TYPES:
            continue
        if l.get("side") == "no":
            continue                     # don't negate a NO leg back into a YES
        if (l.get("event_id"), f"NO — {l.get('label')}") in have_no:
            continue                     # its NO side is already on the board
        lab = (l.get("label") or "").lower()
        if any(w in lab for w in _NO_SKIP_WORDS):
            continue
        p = l.get("prob")
        if p is None or not (0.02 < p < 0.98):
            continue
        out.append({**l,
                    "label": f"NO — {l.get('label')}",
                    "prob": 1.0 - p,
                    "price_cents": l.get("no_cents"),
                    "type": f"{typ} (NO)",
                    "side": "no",
                    "sim_avg": l.get("sim_avg"), "avg_unit": l.get("avg_unit")})
    return out


def _filter_types(legs, types):
    """Drop legs whose (catalogued) type the user turned off. `types` is the
    allowed set; None means no filtering, [] means everything catalogued is off."""
    if types is None:
        return legs
    allow = set(types)

    def base(t):
        # A NO leg follows its parent's chip: turning off "Hits" must also drop
        # "Hit (NO)", or the filter silently leaks the side you excluded.
        return t[:-5] if t.endswith(" (NO)") else t
    return [l for l in legs
            if base(l.get("type") or "") not in _ALL_TYPES
            or base(l.get("type") or "") in allow]


def _mlb_legs(date, season, allow_live=False):
    legs = []
    try:
        games = baseball.analyze_slate(date, season)
    except Exception:
        return legs
    for g in games:
        for v in baseball._game_variants(g, allow_live=allow_live):
            legs.append({"category": "⚾ MLB", "event_id": f"mlb_{g['game_pk']}",
                         "label": v["label"], "matchup": v["matchup"], "prob": v["prob"],
                         "price_cents": v.get("price_cents"), "type": v["type"],
                         "side": v.get("side", "yes"), "live": bool(v.get("live")),
                         "sim_avg": v.get("sim_avg"), "avg_unit": v.get("avg_unit")})
    return legs


# Timeframes pulled for crypto edges. Daily is the steadiest read; hourly adds
# the fast intraday opportunities (the ones the scanner surfaces). 15-min is too
# noisy for a "best bets" board, so it's left to the live scanner.
_CRYPTO_TIMEFRAMES = ("hourly", "daily")
# Minimum minutes-to-close so we don't price a near-settled hourly market (thin,
# whippy books in the last few minutes give false edges).
_CRYPTO_MIN_MINS = {"hourly": 8.0, "daily": 30.0}
_TF_LABEL = {"hourly": "hourly", "daily": "daily", "15M": "15-min"}


def _crypto_legs():
    legs = []
    for coin in CRYPTO_COINS:
        now = _t.time()
        ctx = {}   # lazily-fetched {spot, candles}, only when a market exists

        def _price():
            if not ctx:
                ctx["spot"] = prices.get_spot(coin)
                ctx["candles"] = prices.get_candles(coin, granularity=60)
            return ctx["spot"], ctx["candles"]

        for tf in _CRYPTO_TIMEFRAMES:
            try:
                ms = kalshi.get_open_markets(coin, tf)
            except Exception:
                continue
            if not ms:
                continue
            try:
                spot, candles = _price()
            except Exception:
                break                              # no price feed for this coin
            for m in ms or []:
                if not m.get("yes_ask") or m["yes_ask"] >= 100 or not m.get("yes_bid"):
                    continue
                mins = max(0.0, (m["close_time"] - now) / 60.0) if m["close_time"] else 0.0
                if mins < _CRYPTO_MIN_MINS.get(tf, 0.0):
                    continue                      # too close to settlement -> noise
                sig = odds.kalshi_signal(spot, candles, m, mins, calibrated=True)
                if sig["fair_yes_cents"] >= sig["fair_no_cents"]:
                    side, prob, price = "YES", sig["fair_yes_cents"] / 100.0, m["yes_ask"]
                else:
                    side, prob, price = "NO", sig["fair_no_cents"] / 100.0, m["no_ask"]
                # One leg per (coin, timeframe): grouped so the combo tuner treats
                # a coin's hourly and daily reads as separate events (both eligible)
                # rather than collapsing them.
                legs.append({
                    "category": "⚡ Crypto", "event_id": f"crypto_{coin}_{tf}",
                    "label": f"{coin} {_TF_LABEL.get(tf, tf)} {side}: {m.get('subtitle') or m['ticker']}",
                    "matchup": coin, "prob": prob, "price_cents": price,
                    "type": "Crypto", "timeframe": tf})
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
        # Method-of-victory legs from the sim's method distribution (model-only).
        # One per bout event, so the combo tuner never stacks a fighter's ML with
        # the same fight's method as if independent.
        meth = bt.get("method") or {}
        for mk, typ, how in (("ko", "KO/TKO", "by KO/TKO"),
                             ("sub", "Submission", "by submission"),
                             ("dec", "Decision", "by decision")):
            pct = meth.get(mk)
            if pct:
                legs.append({"category": "🥊 UFC", "event_id": ev,
                             "label": f"Fight ends {how}", "matchup": matchup,
                             "prob": max(0.01, min(0.99, pct / 100.0)),
                             "price_cents": None, "type": typ})
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
        # Where to find it on Kalshi (series + tournament), so a combo leg isn't
        # a mystery match buried in one of the dozens of tennis tabs.
        where = " · ".join(x for x in (m.get("kalshi_series"), m.get("tournament")) if x)
        # one short reason for the match -> the favoured side's winner leg
        insights = m.get("insights") or []
        why = insights[0] if insights else None

        def leg(label, prob_pct, cents, typ, why=None, avg=None, unit=None):
            if prob_pct is None:
                return
            d = {"category": cat, "event_id": ev, "label": label, "matchup": mu,
                 "prob": max(0.01, min(0.99, prob_pct / 100.0)),
                 "price_cents": cents, "type": typ,
                 "sim_avg": avg, "avg_unit": unit, "where": where or None}
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
            leg(f"Over {line} games", p_over, None, "Games", avg=round(mean_g, 1), unit="games")
            leg(f"Under {line} games", round(100 - p_over, 1), None, "Games",
                avg=round(mean_g, 1), unit="games")
        # total aces over (Poisson around the model mean)
        ace_mean = m.get("aces_total")
        if ace_mean and ace_mean > 4:
            aline = round(ace_mean) - 0.5
            leg(f"Over {aline} aces", round(100 * _poisson_over(ace_mean, aline), 1), None, "Aces",
                avg=round(ace_mean, 1), unit="aces")
    return legs


def _board_legs(b, category, prefix):
    """Model legs from any engine board with the shared game shape (basketball /
    hockey): moneylines, Kalshi exact-line spreads/totals, top player props —
    all at the SIM's probability, not the de-vig market read."""
    legs = []
    for g in (b or {}).get("games") or []:
        if g.get("state") == "post":
            continue
        mu = f"{g.get('away_name') or g['away']} @ {g.get('home_name') or g['home']}"
        eid = f"{prefix}-{g['away']}-{g['home']}"
        kx = g.get("kalshi") or {}

        def leg(label, prob_pct, cents, typ, avg=None, unit=None):
            legs.append({"category": category, "event_id": eid, "label": label,
                         "matchup": mu, "prob": max(0.01, min(0.99, prob_pct / 100.0)),
                         "price_cents": cents, "type": typ,
                         "sim_avg": avg, "avg_unit": unit})
        for side in ("home", "away"):
            nm = g.get(side + "_name") or g[side]
            leg(f"{nm} to win", g[f"p_{side}"] * 100.0, kx.get(side + "_cents"), "ML",
                avg=g.get("mean_margin") if side == "home" else None, unit="margin")
        for s in (g.get("spread_edges") or [])[:2]:
            import combo_engine as _ce
            leg(_ce.spread_label(s["team"], s["line"]).replace(" runs", ""),
                s["model_pct"], s["cents"], "Spread")
        for t in (g.get("total_edges") or [])[:2]:
            leg(f"Over {t['line']}", t["model_pct"], t["cents"], "Total",
                avg=g.get("exp_total"), unit="points")
        for p in (g.get("props") or [])[:3]:
            line_txt = "" if p["line"] == 0.5 else f"{p['line']}+ "
            leg(f"{p['player']} {line_txt}{p['stat']}", p["over_pct"], None,
                p["stat"].title())
    return legs


def _nba_legs():
    import basket
    return _board_legs(basket.board("nba"), "🏀 NBA", "nba")


def _nhl_legs():
    import hockey
    return _board_legs(hockey.board(), "🏒 NHL", "nhl")


def _nfl_week():
    """Best-effort current NFL regular-season week from the calendar (Week 1 ≈ the
    week of Sept 8). Clamps to 1–18; preseason falls back to Week 1 (empty slate)."""
    import datetime
    import clock
    t = clock.today_et()
    season = t.year if t.month >= 3 else t.year - 1
    kickoff = datetime.date(season, 9, 8)
    return max(1, min(18, 1 + max(0, (t - kickoff).days // 7)))


def _nfl_legs():
    """NFL legs from the drive-engine slate board: moneylines (+ live Kalshi
    price), the total at the sim's line, and top correlated player props."""
    legs = []
    try:
        import nfl_game_sim
        b = nfl_game_sim.board(_nfl_week())
    except Exception:
        return legs
    for g in (b or {}).get("games") or []:
        if g.get("state") == "post":
            continue
        mu = f"{g.get('away_name') or g['away']} @ {g.get('home_name') or g['home']}"
        eid = f"nfl-{g['away']}-{g['home']}"
        kx = g.get("kalshi") or {}

        def leg(label, prob_pct, cents, typ, avg=None, unit=None):
            legs.append({"category": "🏈 NFL", "event_id": eid, "label": label,
                         "matchup": mu, "prob": max(0.01, min(0.99, prob_pct / 100.0)),
                         "price_cents": cents, "type": typ,
                         "sim_avg": avg, "avg_unit": unit})
        for side in ("home", "away"):
            nm = g.get(side + "_name") or g[side]
            leg(f"{nm} to win", g[f"p_{side}"] * 100.0, kx.get(side + "_cents"), "ML",
                avg=g.get("mean_margin") if side == "home" else None, unit="margin")
        tl = g.get("total_ladder") or []
        if tl:
            mid = tl[len(tl) // 2]
            leg(f"Over {mid['line']}", mid["over_pct"], None, "Total",
                avg=g.get("exp_total"), unit="points")
        for p in (g.get("props") or [])[:4]:
            line_txt = "" if p.get("line") == 0.5 else f"{p['line']}+ "
            leg(f"{p['player']} {line_txt}{p['stat']}", p["over_pct"], None,
                p["stat"].title(), avg=p.get("line"), unit=p["stat"])
    return legs


def _golf_legs():
    """Golf legs from OUR tournament simulator (model probabilities, not de-vig):
    make-the-cut and head-to-head matchups priced against Kalshi, plus top-10 and
    outright as model-only legs. Make-cut and matchups are independent events;
    outright (mutually exclusive) and top-10 (correlated) each share one event so
    a combo never stacks contradictory golf legs."""
    legs = []
    try:
        import golf
        b = golf.board()
    except Exception:
        return legs
    if not b:
        return legs
    ev = b.get("event") or "Golf"
    cat = "⛳ Golf"

    def leg(eid, label, prob_pct, cents, typ, matchup=None):
        legs.append({"category": cat, "event_id": eid, "label": label,
                     "matchup": matchup or ev,
                     "prob": max(0.01, min(0.99, prob_pct / 100.0)),
                     "price_cents": cents, "type": typ})
    for r in b.get("make_cut", []):
        leg(f"golf_mc_{r['player']}", f"{r['player']} to make the cut",
            r["model_pct"], r.get("cents"), "Make Cut")
    for r in b.get("h2h", []):
        leg(f"golf_h2h_{r['a']}_{r['b']}", f"{r['a']} to beat {r['b']}",
            r["model_pct"], r.get("cents"), "Matchup", f"{r['a']} vs {r['b']}")
    for p in b.get("players", [])[:25]:
        if (p.get("top10_pct") or 0) >= 5:
            leg("golf_top10", f"{p['name']} top-10", p["top10_pct"], None, "Top 10")
    for p in b.get("players", [])[:15]:
        if (p.get("win_pct") or 0) >= 1:
            leg("golf_win", f"{p['name']} to win", p["win_pct"], None, "Outright")
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


def gather(cats, date, season, allow_live=False):
    """All legs for the checked categories. Each leg is tagged with its source
    category KEY (`cat_key`) so the maker can budget legs per sport, independent
    of the display label (tennis, for instance, spans several tour labels)."""
    legs = []

    def add(key, fn):
        try:
            new = fn() or []
        except Exception:
            new = []
        for l in new:
            l.setdefault("cat_key", key)
        legs.extend(new)

    if "mlb" in cats:
        add("mlb", lambda: _mlb_legs(date, season, allow_live=allow_live))
    if "nfl" in cats:
        add("nfl", _nfl_legs)
    if "crypto" in cats:
        add("crypto", _crypto_legs)
    if "ufc" in cats:
        add("ufc", _ufc_legs)                    # our UFC fight model, not de-vig
    if "tennis" in cats:
        add("tennis", lambda: _tennis_legs(COMBO_TOURS))
    if "nba" in cats:
        add("nba", _nba_legs)
    if "nhl" in cats:
        add("nhl", _nhl_legs)
    if "golf" in cats:          # tournament-simulator legs, de-vig browse fallback
        add("golf", lambda: _golf_legs() or _sport_legs("golf"))
    # Every market has two sides; offer both. NO legs share their YES leg's
    # event_id, so the two can never land in the same slip.
    legs += _no_legs(legs)
    return legs


def _fee_cents(cents):
    """Expected Kalshi taker fee per contract — see kalshi.taker_fee_cents."""
    return kalshi.taker_fee_cents(cents)


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
                  "price_cents": l.get("price_cents"), "why": l.get("why"),
                  "sim_avg": l.get("sim_avg"), "avg_unit": l.get("avg_unit"),
                  "where": l.get("where")} for l in combo],
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


def recommended(cats, date, season, max_legs=12, types=None, allow_live=False):
    """Auto-built recommended parlays from the checked sports -- the same idea as
    the baseball tab's safest / best-value / best combos, across categories:

      - safest:     the most-likely legs (one per event), best chance to cash.
      - best_value: only legs where OUR model beats the Kalshi price (+EV), by
                    descending edge -- the parlay the market is mispricing.
      - best:       the all-arounder -- best-edge legs among reasonably likely
                    ones (>= 55%), balancing payout and hit rate.

    `types`, when given, restricts which leg types are eligible (the UI's per-
    sport type chips).
    """
    legs = _filter_types(gather(cats, date, season, allow_live), types)
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

    # Same-game fallback: on a thin slate the checked sports may only offer legs
    # from a single event (e.g. the one MLB game the day back from the All-Star
    # break). Cross-event packing needs ≥2 events, so nothing forms — but a
    # same-game parlay is the right tool there. Build one for that lone MLB game,
    # priced with the correlation-aware sim (not a naive product).
    same_game_only = False
    if not (safest or best or best_value) and len(by_event) < 2:
        sg = _mlb_same_game_items(date, season)
        if sg:
            safest = sg[0]
            best = sg[0]
            best_value = next((s for s in sg if (s.get("ev_pct") or 0) > 0), None)
            same_game_only = True

    return {"safest": safest, "best": best, "best_value": best_value,
            "counts": counts, "same_game_only": same_game_only}


def _mlb_same_game_items(date, season):
    """Correlation-aware same-game parlays for a single MLB game, mapped into the
    combine item shape (so renderCombo displays them like any other combo)."""
    try:
        games = baseball.analyze_slate(date, season)
    except Exception:
        return []
    live = [g for g in games if (g.get("live") or {}).get("state") != "Final"]
    if len(live) != 1:
        return []
    out, seen = [], set()
    for nl in (3, 2, 4):
        try:
            res = baseball.build_same_game_parlays(live, n_legs=nl, target_pct=45,
                                                   max_legs=nl, top_n=4)
        except Exception:
            continue
        for it in (res.get("games") or []):
            legs = []
            for lg in it.get("legs", []):
                legs.append({"pick": lg.get("pick"), "matchup": it.get("matchup"),
                             "type": lg.get("type"), "category": "⚾ MLB",
                             "prob_pct": lg.get("prob_pct"),
                             "price_cents": lg.get("market_cents"),
                             "sim_avg": lg.get("sim_avg"), "avg_unit": lg.get("avg_unit")})
            key = tuple(sorted(l["pick"] or "" for l in legs))
            if key in seen or len(legs) < 2:
                continue
            seen.add(key)
            out.append({"legs": legs, "n_legs": it.get("n_legs") or len(legs),
                        "combined_prob_pct": it.get("combined_prob_pct"),
                        "fair_payout_x": it.get("fair_payout_x"),
                        "same_game": True, "reasons": ["Same-game parlay — one game on the slate today; legs are correlated, so this is priced with the correlation-aware sim, not a naive product."]})
    out.sort(key=lambda c: -(c.get("combined_prob_pct") or 0))
    return out


def _pick_k_for_payout(elig, k, target_log):
    """Choose exactly k legs (one per event, from `elig` sorted by prob) whose
    combined payout best reaches the target. Because payout = 1/prob, the safest
    combo that still reaches the target is the one whose total −log(prob) is the
    SMALLEST value at or above target_log. A small DP over (count, payout bucket)
    finds it and remembers the legs. Returns the chosen leg list."""
    import math
    RES = 0.05
    pool = elig[:250]                                   # bound the DP; plenty of spread
    # dp[(cnt, bucket)] = (sum_logp, chosen_legs) — best (highest prob) per state.
    dp = {(0, 0): (0.0, ())}
    for l in pool:
        lp = math.log(max(0.01, min(0.99, l["prob"])))
        cb = int(round(-lp / RES))
        nd = dict(dp)
        for (cnt, bk), (s, sel) in dp.items():
            if cnt + 1 > k:
                continue
            key = (cnt + 1, bk + cb)
            cand = (s + lp, sel + (l,))
            if key not in nd or cand[0] > nd[key][0]:
                nd[key] = cand
        dp = nd
    states = [(bk, s, sel) for (cnt, bk), (s, sel) in dp.items() if cnt == k]
    if not states:
        return list(elig[:k])
    tb = target_log / RES
    reaching = [st for st in states if st[0] >= tb]
    if reaching:
        best = min(reaching, key=lambda st: st[0])      # smallest payout ≥ target = safest
    else:
        best = max(states, key=lambda st: st[0])        # can't reach -> most payout
    return list(best[2])


def _logpay(l):
    import math
    return -math.log(max(0.01, min(0.99, l["prob"])))


def _assemble_by_cat(legs, per_cat, target, max_legs, payout_target=None, payout_mode="off"):
    """Per-sport budget: take N legs (0 = all) from each named sport, one per
    event, clearing the per-leg floor. With no payout target it takes each sport's
    most likely legs ('all the baseball moneylines + 2 easy tennis'); with a
    payout target it aims the whole slip at that payout as safely as possible —
    sports whose count leaves no choice (e.g. all four baseball moneylines) are
    LOCKED first, and only the sports with room to choose (the tennis 'supplement')
    are steered toward the payout still needed. Legs are tagged in gather()."""
    import math
    from collections import defaultdict
    by = defaultdict(lambda: defaultdict(list))       # cat_key -> event -> [legs]
    for l in legs:
        by[l.get("cat_key")][l["event_id"]].append(l)
    want_payout = (payout_mode in ("require", "prefer")
                   and payout_target and payout_target > 1)
    # Eligible pool + requested count per sport (floor-cleared, best per event).
    pools = {}
    for cat, n in per_cat.items():
        events = by.get(cat) or {}
        best = [max(vs, key=lambda v: v["prob"]) for vs in events.values()]
        elig = [l for l in best if l["prob"] >= target] or best   # honor the floor
        elig.sort(key=lambda v: -v["prob"])
        k = len(elig) if not n else min(max(0, n), len(elig))     # n==0 -> all
        pools[cat] = (elig, k)

    chosen, per_used = [], {}
    if not want_payout:
        for cat, (elig, k) in pools.items():
            take = elig[:k]
            per_used[cat] = len(take)
            chosen.extend(take)
    else:
        # Lock the sports with no choice; they contribute a fixed payout.
        fixed_log, flex = 0.0, []
        for cat, (elig, k) in pools.items():
            if k >= len(elig):                          # forced -> take them all
                per_used[cat] = k
                chosen.extend(elig[:k])
                fixed_log += sum(_logpay(l) for l in elig[:k])
            else:
                flex.append(cat)
        # Aim the flexible sports at the REMAINING payout, sequentially so each
        # adapts to what the previous ones actually contributed.
        remaining = max(0.0, math.log(payout_target) - fixed_log)
        left = len(flex)
        for cat in flex:
            elig, k = pools[cat]
            take = _pick_k_for_payout(elig, k, remaining / max(1, left))
            got = sum(_logpay(l) for l in take)
            remaining = max(0.0, remaining - got)
            left -= 1
            per_used[cat] = len(take)
            chosen.extend(take)
    chosen.sort(key=lambda v: -v["prob"])
    if len(chosen) > max_legs:                          # safety cap: keep the best
        chosen = chosen[:max_legs]
    chosen = [dict(l, meets=(l["prob"] >= target)) for l in chosen]
    return chosen, per_used


def build(cats, n_legs, target_pct, date, season, target_payout=None, max_legs=12,
          legs_mode="prefer", payout_mode=None, conn="or", types=None, per_cat=None,
          allow_live=False):
    legs = _filter_types(gather(cats, date, season, allow_live), types)
    counts = {}
    for l in legs:
        counts[l["category"]] = counts.get(l["category"], 0) + 1
    if not legs:
        return {"combo": None, "counts": counts}
    target = max(0.05, min(0.97, target_pct / 100.0))
    # Per-sport budget mode: the combo is built entirely from the requested count
    # per sport (a sport with no count contributes nothing), so leg counts differ
    # by sport instead of one global floor deciding everything.
    if per_cat:
        pmode = payout_mode if payout_mode in ("require", "prefer") else (
            "require" if (target_payout and target_payout > 1) else "off")
        chosen, per_used = _assemble_by_cat(legs, per_cat, target, max_legs,
                                            payout_target=target_payout, payout_mode=pmode)
        if len(chosen) < 2:
            return {"combo": None, "counts": counts, "per_cat_used": per_used}
        item = _item(chosen)
        item["target_pct"] = round(target * 100, 1)
        item["legs_meeting_target"] = sum(1 for v in chosen if v.get("meets"))
        item["legs_used"] = len(chosen)
        item["per_cat"] = True
        item["per_cat_used"] = per_used
        item["capped"] = len(chosen) >= max_legs
        if target_payout and target_payout > 1 and pmode != "off":
            item["target_payout_x"] = target_payout
            item["payout_reached"] = (item.get("fair_payout_x") or 0) >= target_payout
        return {"combo": item, "counts": counts, "per_cat_used": per_used}
    # Back-compat default: a payout target with no explicit mode means "require it"
    # (the old behavior was payout-governed when payout > 1).
    if payout_mode is None:
        payout_mode = "require" if (target_payout and target_payout > 1) else "off"
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
