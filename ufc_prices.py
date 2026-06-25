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
    """{normalized fighter name: yes_ask_cents} across all open KXUFCFIGHT markets."""
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
                out[nm] = cents
                out.setdefault(nm.split()[-1], cents)   # last-name fallback
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
        ca = mkt.get(_norm(fa["name"])) or mkt.get(_norm(fa["name"]).split()[-1])
        cb = mkt.get(_norm(fb["name"])) or mkt.get(_norm(fb["name"]).split()[-1])
        # de-vig market win % per fighter (the two asks sum to >100 — the vig)
        mkt_a = mkt_b = None
        if ca is not None and cb is not None and (ca + cb) > 0:
            mkt_a = 100.0 * ca / (ca + cb)
            mkt_b = 100.0 * cb / (ca + cb)
        conf = min(fa.get("fights", 0), fb.get("fights", 0))
        w = conf / (conf + 4.0)                     # 0 fights -> all market, lots -> all model
        for f, c, mk in ((fa, ca, mkt_a), (fb, cb, mkt_b)):
            f["kalshi_cents"] = c
            f["confidence"] = round(w, 2)
            if c is None:
                f["fair_win"], f["edge"] = f["win_pct"], None
                continue
            fair = w * f["win_pct"] + (1 - w) * (mk if mk is not None else f["win_pct"])
            f["fair_win"] = round(fair, 1)
            f["edge"] = round(fair - c, 1)
            priced = True
    board["priced"] = priced
    return board
