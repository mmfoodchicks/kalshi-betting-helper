"""Attach live Kalshi UFC fight prices + model edges to a fight-sim board.

Kalshi lists one market per fighter (KXUFCFIGHT), the yes-subtitle being the
fighter's name. We match each rated fighter to their market and hang the price
and edge (our simulated win % - the market ask) off them.
"""

import unicodedata

import kalshi


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def _market_map():
    """{normalized fighter name: {cents, ticker, close_time}} across all open
    KXUFCFIGHT markets. Ticker + close_time ride along so the prediction logger
    can grade the pick when the fight settles."""
    out, cursor = {}, ""
    for _ in range(6):
        url = f"{kalshi.BASE}/markets?series_ticker=KXUFCFIGHT&status=open&limit=1000"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            d = kalshi._get_json(url)
        except Exception:
            break
        for m in d.get("markets", []):
            nm = _norm(m.get("yes_sub_title"))
            cents = kalshi._cents(m.get("yes_ask_dollars"))
            if nm and cents is not None:
                rec = {"cents": cents, "ticker": m.get("ticker"),
                       "close_time": kalshi._parse_time(m.get("close_time"))}
                out[nm] = rec
                out.setdefault(nm.split()[-1], rec)   # last-name fallback
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    return out


def attach(board):
    """Mutate the board: add kalshi_cents, a confidence-blended fair win % and the
    edge to each fighter; return it.

    When both fighters have thin fight history our model defaults toward 50/50 and
    would show large *false* edges against a market that knows the fighters. So the
    'fair' win % blends our model toward the de-vig'd market price by confidence
    (how many fights we actually have) — a debut bout defers to the market (≈no
    edge), a data-rich bout keeps our independent read."""
    if not board or not board.get("bouts"):
        return board
    mkt = _market_map()
    priced = False
    for bt in board["bouts"]:
        fa, fb = bt["a"], bt["b"]
        ra = mkt.get(_norm(fa["name"])) or mkt.get(_norm(fa["name"]).split()[-1])
        rb = mkt.get(_norm(fb["name"])) or mkt.get(_norm(fb["name"]).split()[-1])
        ca = ra["cents"] if ra else None
        cb = rb["cents"] if rb else None
        # de-vig market win % per fighter (the two asks sum to >100 — the vig)
        mkt_a = mkt_b = None
        if ca is not None and cb is not None and (ca + cb) > 0:
            mkt_a = 100.0 * ca / (ca + cb)
            mkt_b = 100.0 * cb / (ca + cb)
        conf = min(fa.get("fights", 0), fb.get("fights", 0))
        w = conf / (conf + 4.0)                     # 0 fights -> all market, lots -> all model
        for f, c, mk, rec in ((fa, ca, mkt_a, ra), (fb, cb, mkt_b, rb)):
            f["kalshi_cents"] = c
            f["confidence"] = round(w, 2)
            if rec:
                f["ticker"] = rec.get("ticker")
                f["close_time"] = rec.get("close_time")
            if c is None:
                f["fair_win"], f["edge"] = f["win_pct"], None
                continue
            fair = round(w * f["win_pct"] + (1 - w) * (mk if mk is not None else f["win_pct"]), 1)
            f["fair_win_raw"] = fair
            # Reality-calibrate against graded UFC outcomes (prediction logger).
            # Log the RAW value; display/edge use the corrected one. No-op until
            # enough fights have settled.
            try:
                import calibrate
                fair = round(100 * calibrate.ufc(fair / 100.0), 1)
            except Exception:
                pass
            f["fair_win"] = fair
            f["edge"] = round(fair - c, 1)
            priced = True
    board["priced"] = priced
    return board
