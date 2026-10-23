"""Pricing and value math for player-prop bets -- pure stdlib, every convention stated.

This is the arithmetic between "the model says 41%" and "is that a bet": odds
conversions, de-vigging, what Kalshi really charges, what an order of N
contracts really costs when it has to walk the book, and the value figures built
on those. It holds no model and no data; the matchup engine hands it a
probability (and, if it has one, an interval) and a quote, and gets numbers back.

UNITS (one rule, no exceptions)

  * Probabilities and edges are fractions (0.043 = 4.3 points).
  * Kalshi PRICES, FEES and EV are CENTS per contract (a contract pays 100c),
    exactly as kalshi.py and the quote dicts in kalshi_nfl / kalshi_nba_props
    carry them. Names say so: `price_c`, `fee_c`, `ev_c`.
  * ROI is a fraction of the all-in cost (0.081 = +8.1%).
  * Stakes and expected profits for a stake are DOLLARS (`stake`, `*_usd`).

WHAT KALSHI CHARGES (verified 2026-10-23, report research/reports/prop_math.md)

  Taker fee on a fill = 0.07 x multiplier x C x P x (1 - P) dollars, for C
  contracts at price P (dollars), the multiplier being the SERIES' fee_multiplier
  (live /series/KXNBAPTS, ...PRA, ...REB, ...AST, ...3PT: fee_type "quadratic",
  multiplier 1). Rounding, from docs.kalshi.com/getting_started/fee_rounding:
  each fill's fee is rounded UP to $0.000001; then, for a member whose balance
  precision is a cent (every retail / FCM-cleared account), the balance is
  aligned to the cent and the shortfall is charged as a "rounding fee", with a
  per-ORDER accumulator that rebates whole cents of accumulated overpayment on
  later fills, so the order's total converges to one equivalent fill's. For fills
  whose revenue is on the cent grid (every integer-cent price) that collapses to

        order fee = ceil_to_cent( sum over fills of ceil_6dp(model fee) )

  which for ONE fill is the schedule's "round up to the next cent" and for a
  sweep of several book levels is NOT the sum of per-level round-ups. Both are
  here: `fee_fills_c` follows the docs' algorithm literally (and is the
  default), `rounding="fill"` charges every fill its own round-up (an upper
  bound). kalshi.taker_fee_cents is the SMOOTH per-contract expectation
  7 p (1-p) c; it is what the edge math uses for a size-free figure
  (`contracts=None`) and what combo_engine charges, and it is exact in the
  limit of large orders. At one contract the real fee is a whole cent
  (2c at 50c vs 1.75c smooth) -- `contracts=1` shows that cost honestly.

  The Kalshi fee-schedule PDF itself (kalshi.com/docs/kalshi-fee-schedule.pdf)
  sits behind a bot checkpoint that answers this host HTTP 429; the formula and
  the 1.75-per-100-contracts-at-50c / 0.07-at-1c-and-99c figures are cross-
  checked in tests/guards_prop_math.py against the docs' own worked example and
  against kalshi.taker_fee_cents.

NO-VIG CONVENTIONS

  * Two-sided odds: MULTIPLICATIVE (proportional) normalisation by default,
    p_i / sum(p). It is the convention the rest of this repo already uses
    (kalshi_nfl.prop_ladders, the soccer and tennis devigs), it needs no
    parameter, and it always returns a proper probability pair. Its known bias:
    it takes the same proportional margin off the favourite and the longshot,
    where the favourite-longshot bias says the longshot carries MORE. The POWER
    method (p_i^k with sum = 1, k solved by bisection) puts more of the margin
    on the longshot; offered as method="power". Neither is "correct" -- there
    is no truth to check against without the closing line -- so the default is
    the simple one and the choice is a parameter, not a hidden constant.
  * A ONE-SIDED market (an ask with nobody bidding, one sportsbook side) has an
    unknown margin: `novig` returns the implied probability unchanged and says
    `vig_known: False, two_sided: False`. It is never silently treated as fair.
  * A Kalshi binary is ONE order book seen from two sides, so its fair value is
    the YES mid (yes_bid + yes_ask) / 2 -- the same reference as
    combo_engine.market_reference. `kalshi_reference` returns it with the
    de-vigged asks beside it, and refuses to call a lopsided book a mid.

LABELS (descriptive, defined only from the inputs; never a score, never "lock")

    high_probability    P(side wins) >= `high_p`. Says nothing about price.
    positive_ev         fee-adjusted EV > 0 at the executable ask.
    positive_ev_robust  fee-adjusted EV > 0 at the unfavourable end of the
                        model's probability interval (needs the interval).
    strong_fit          passed in by the caller from a measured quantity; this
                        module only formats it.
    low_evidence        n_eff < `min_n_eff`, OR interval width > `max_width`,
                        OR neither was supplied (no evidence given is not
                        evidence). The three thresholds are CONVENTIONS, not
                        fitted values -- see LABEL_DEFAULTS.
"""
import math
import time
from fractions import Fraction

import kalshi

# ---------------------------------------------------------------------------
# Odds conversions
# ---------------------------------------------------------------------------
def _num(x, name):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f"{name} must be a number, got {type(x).__name__}")
    x = float(x)
    if not math.isfinite(x):
        raise ValueError(f"{name} must be finite")
    return x


def american_to_decimal(a):
    """American odds -> decimal (total return per 1 staked, stake included).

    +150 -> 2.50 ; -200 -> 1.50. Valid American odds are >= +100 or <= -100;
    0 and anything strictly between -100 and +100 are not odds and raise."""
    a = _num(a, "american odds")
    if -100.0 < a < 100.0:
        raise ValueError("american odds must be >= +100 or <= -100")
    return 1.0 + a / 100.0 if a > 0 else 1.0 + 100.0 / (-a)


def decimal_to_american(d):
    """Decimal odds (> 1) -> American. 2.00 -> +100 (not -100); returns a float,
    unrounded, so a round trip is exact."""
    d = _num(d, "decimal odds")
    if d <= 1.0:
        raise ValueError("decimal odds must be > 1")
    return (d - 1.0) * 100.0 if d >= 2.0 else -100.0 / (d - 1.0)


def decimal_to_prob(d):
    """Implied probability 1/d (vig still in it)."""
    d = _num(d, "decimal odds")
    if d <= 1.0:
        raise ValueError("decimal odds must be > 1")
    return 1.0 / d


def prob_to_decimal(p):
    p = _num(p, "probability")
    if not (0.0 < p < 1.0):
        raise ValueError("probability must be in (0, 1)")
    return 1.0 / p


def american_to_prob(a):
    """Implied probability of American odds: -110 -> 0.5238, +150 -> 0.40."""
    return decimal_to_prob(american_to_decimal(a))


def prob_to_american(p):
    return decimal_to_american(prob_to_decimal(p))


def odds_to_prob(odds, fmt="american"):
    """Implied probability from `fmt` in {"american", "decimal", "prob"}."""
    if fmt == "american":
        return american_to_prob(odds)
    if fmt == "decimal":
        return decimal_to_prob(odds)
    if fmt == "prob":
        p = _num(odds, "probability")
        if not (0.0 < p < 1.0):
            raise ValueError("probability must be in (0, 1)")
        return p
    raise ValueError("fmt must be 'american', 'decimal' or 'prob'")


# ---------------------------------------------------------------------------
# No-vig probabilities
# ---------------------------------------------------------------------------
def devig_two_way(p_a, p_b, method="multiplicative"):
    """Remove the margin from the two implied probabilities of one market.

    Returns {"p_a", "p_b", "overround", "method", "power_k", "vig_known": True,
    "two_sided": True}; p_a + p_b == 1. overround = p_a + p_b - 1 (negative is an
    underround / arbitrage and is normalised the same way)."""
    p_a, p_b = _num(p_a, "p_a"), _num(p_b, "p_b")
    if not (0.0 < p_a < 1.0 and 0.0 < p_b < 1.0):
        raise ValueError("implied probabilities must be in (0, 1)")
    over = p_a + p_b - 1.0
    k = None
    if method == "multiplicative":
        t = p_a + p_b
        qa, qb = p_a / t, p_b / t
    elif method == "power":
        # f(k) = p_a^k + p_b^k - 1 falls from +1 (k=0) to -1 (k->inf): one root.
        lo, hi = 1e-9, 1.0
        while p_a ** hi + p_b ** hi - 1.0 > 0.0:
            hi *= 2.0
            if hi > 1e6:
                raise ValueError("power devig did not bracket a root")
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if p_a ** mid + p_b ** mid - 1.0 > 0.0:
                lo = mid
            else:
                hi = mid
        k = 0.5 * (lo + hi)
        qa, qb = p_a ** k, p_b ** k
        s = qa + qb
        qa, qb = qa / s, qb / s
    else:
        raise ValueError("method must be 'multiplicative' or 'power'")
    return {"p_a": qa, "p_b": qb, "overround": over, "method": method, "power_k": k,
            "vig_known": True, "two_sided": True}


def novig(p_a, p_b=None, method="multiplicative"):
    """No-vig probability. Two-sided -> devig_two_way. One-sided (p_b None) ->
    the implied probability back UNCHANGED, flagged `vig_known: False`,
    `two_sided: False`: its margin is unknown, so it is an upper bound on the
    fair price of that side, not a fair price."""
    if p_b is None:
        p_a = _num(p_a, "p_a")
        if not (0.0 < p_a < 1.0):
            raise ValueError("implied probability must be in (0, 1)")
        return {"p_a": p_a, "p_b": None, "overround": None, "method": "none", "power_k": None,
                "vig_known": False, "two_sided": False}
    return devig_two_way(p_a, p_b, method)


def novig_odds(odds_a, odds_b=None, fmt="american", method="multiplicative"):
    """novig() on odds in `fmt` (odds_b None = one-sided)."""
    return novig(odds_to_prob(odds_a, fmt),
                 None if odds_b is None else odds_to_prob(odds_b, fmt), method)


# ---------------------------------------------------------------------------
# Kalshi quotes
# ---------------------------------------------------------------------------
def _px(c):
    """A usable Kalshi price in cents, or None. Both 0 and 100 are the book's
    'nothing there' sentinels (a 100c ask is a bid-only book, a 0c bid is an
    ask-only book), not prices -- a contract costing 100c to win 100c is not a
    bet (same rule as kalshi_nfl._quote)."""
    if c is None:
        return None
    try:
        c = float(c)
    except (TypeError, ValueError):
        return None
    return c if (math.isfinite(c) and 0.0 < c < 100.0) else None


MAX_MID_SPREAD_C = 20.0     # combo_engine._SPREAD_WIDE and kalshi_nfl._MAX_PROP_SPREAD: past 20c a mid is two unrelated orders


def _yes_book(market):
    """(yes_bid, yes_ask) in cents or None. A consistent book has no_ask = 100 -
    yes_bid and no_bid = 100 - yes_ask; whichever side is missing is filled from
    the other, so a feed that carries only one side's fields still reads."""
    yb, ya = _px(market.get("yes_bid")), _px(market.get("yes_ask"))
    nb, na = _px(market.get("no_bid")), _px(market.get("no_ask"))
    if yb is None and na is not None:
        yb = _px(100.0 - na)
    if ya is None and nb is not None:
        ya = _px(100.0 - nb)
    return yb, ya


def book_is_believable(market, max_spread_c=20.0):
    """Is this market block an opinion or just a listing? kalshi_nfl.believable's
    rule on the YES book: a real bid AND ask, bid <= ask, spread <= max_spread_c
    cents, and some volume or open interest (`market["volume"]`, `market["oi"]`;
    absent counts as zero). A first listing sits at 2c / 68c with nothing traded
    and de-vigs to a number nobody believes. Parity with
    kalshi_nfl.believable on the same quote is pinned in the guards."""
    yb, ya = _yes_book(market or {})
    if yb is None or ya is None or yb > ya or (ya - yb) > max_spread_c:
        return False
    return ((market.get("volume") or 0) + (market.get("oi") or 0)) > 0


def kalshi_reference(market, method="mid"):
    """The no-vig reference for a Kalshi market block (cents).

    Returns {"kind": "two_sided" | "one_sided" | "none", "p": P(YES) or None,
    "method", "spread_c", "vig_known", "p_mid", "p_ask_devig", "note"}.

    two_sided   a real YES bid AND ask: p is the YES mid ("mid", default), or
                the multiplicative / power de-vig of the YES and NO ASKS
                ("ask_devig" / "ask_power"). `p_mid` and `p_ask_devig` are
                both always reported so a caller can see how much the choice
                matters (a few tenths of a point on a tight book).
    one_sided   only one side of the book exists (the other is the 0 / 100
                sentinel): p is that side's price as a probability of YES, an
                upper bound for an ask and a lower bound for a bid, with
                vig_known False.
    none        no usable price at all.
    The mid of a book wider than 20c is an artifact of two unrelated resting
    orders (combo_engine._SPREAD_WIDE), so a spread beyond MAX_MID_SPREAD_C is
    reported as kind "one_sided" on the ask: that is the only real number."""
    yb, ya = _yes_book(market)
    na = _px(market.get("no_ask"))
    if na is None and yb is not None:
        na = _px(100.0 - yb)
    out = {"kind": "none", "p": None, "method": method, "spread_c": None, "vig_known": False,
           "p_mid": None, "p_ask_devig": None, "note": ""}
    if yb is not None and ya is not None and yb <= ya:
        out["spread_c"] = ya - yb
        out["p_mid"] = (yb + ya) / 200.0
        if na is not None:
            out["p_ask_devig"] = devig_two_way(ya / 100.0, na / 100.0)["p_a"]
        if out["spread_c"] > MAX_MID_SPREAD_C:
            out.update(kind="one_sided", p=ya / 100.0, method="ask",
                       note=f"spread {out['spread_c']:.0f}c: the mid is not a fair value, the ask is the one real number")
            return out
        if method == "mid":
            p = out["p_mid"]
        elif method in ("ask_devig", "ask_power") and na is not None:
            p = devig_two_way(ya / 100.0, na / 100.0,
                              "multiplicative" if method == "ask_devig" else "power")["p_a"]
        else:
            raise ValueError("method must be 'mid', 'ask_devig' or 'ask_power'")
        out.update(kind="two_sided", p=p, vig_known=True)
        return out
    if ya is not None:
        out.update(kind="one_sided", p=ya / 100.0, method="ask",
                   note=("crossed book (bid above ask): not a two-sided market, the ask is the one price used"
                         if yb is not None else
                         "ask only: no bid, margin unknown (an upper bound on the fair price)"))
    elif yb is not None:
        out.update(kind="one_sided", p=yb / 100.0, method="bid",
                   note="bid only: no offer, margin unknown (a lower bound on the fair price)")
    return out



# ---------------------------------------------------------------------------
# Kalshi fees
# ---------------------------------------------------------------------------
_COEF = Fraction(7, 100)
_SIX_DP = Fraction(1, 10 ** 6)
_UNIT = {"cent": Fraction(1, 100), "centicent": Fraction(1, 10000)}


def fee_params(series=None, fee_type=None, fee_multiplier=None, lookup=True):
    """{"fee_type", "fee_multiplier", "known", "multiplier_source"} for a series.

    Explicit `fee_type` / `fee_multiplier` win. Otherwise the series' own record
    is read (kalshi.series_info: cached 12 h; one GET per series). `known` is
    False when neither source gave an answer or the fee type is not the
    quadratic family -- the schedule is then the standard curve at multiplier 1
    (kalshi.fee_for_market's rule) and the caller is told it is an assumption.
    Never raises on a failed lookup (series_info ledgers it as KAL-series)."""
    ft, mult = fee_type, fee_multiplier
    src = "explicit" if fee_multiplier is not None else None
    if (ft is None or mult is None) and series and lookup:
        info = kalshi.series_info(series) or {}
        ft = ft if ft is not None else info.get("fee_type")
        if mult is None and info.get("fee_multiplier") is not None:
            mult, src = info.get("fee_multiplier"), "series"
    known = ft is not None and str(ft).startswith("quadratic")
    try:
        mult = float(mult) if mult is not None else 1.0
    except (TypeError, ValueError):
        mult = 1.0
    return {"fee_type": ft, "fee_multiplier": mult, "known": bool(known),
            "multiplier_source": src or "default"}


def fee_per_contract_c(price_c, mult=1.0):
    """The SMOOTH taker fee for one contract at `price_c`, in cents:
    kalshi.taker_fee_cents(price) x mult (no rounding). 0.0 outside (0, 100)."""
    return kalshi.taker_fee_cents(price_c) * float(mult)


def _frac(x):
    return x if isinstance(x, Fraction) else Fraction(x).limit_denominator(1000000)


def _ceil_to(x, unit):
    return -((-x) // unit) * unit


def _model_fee_usd(price_c, qty, mult):
    """Kalshi's model fee in dollars for one fill: 0.07 x mult x C x P(1-P)."""
    p = _frac(price_c) / 100
    return _COEF * _frac(mult) * _frac(qty) * p * (1 - p)


def fee_fills_c(fills, mult=1.0, precision="cent", rounding="order"):
    """Exact fee, in cents, for one ORDER that filled as `fills` = [(price_c, qty), ...].

    rounding "order" (default) follows docs.kalshi.com/getting_started/fee_rounding
    literally, per fill: trade_fee = ceil_6dp(model fee); the buyer's balance
    change (-price x qty - trade_fee) is floored to the balance grid (`precision`
    "cent" for retail / FCM-cleared members, "centicent" for direct members);
    the difference is a rounding fee added to the order's accumulator, whole
    grid units of which are rebated at once, capped so a fill's net fee is
    never negative. rounding "fill" charges each fill its own round-up to the
    grid with no accumulator (an upper bound, and what a one-fill-at-a-time
    reading of the schedule gives).

    Returns {"fee_c": total, "per_fill_c": [...], "carry_c": accumulator left
    over, "rounding", "precision"}. Money is Fraction inside; floats out."""
    if precision not in _UNIT:
        raise ValueError("precision must be 'cent' or 'centicent'")
    if rounding not in ("order", "fill"):
        raise ValueError("rounding must be 'order' or 'fill'")
    u = _UNIT[precision]
    acc = Fraction(0)
    per, total = [], Fraction(0)
    for price_c, qty in fills:
        if _px(price_c) is None or not (qty > 0):
            raise ValueError("a fill needs a price in (0, 100) cents and a positive quantity")
        rev = -(_frac(price_c) / 100) * _frac(qty)
        tf = _ceil_to(_model_fee_usd(price_c, qty, mult), _SIX_DP)
        change = rev - tf
        aligned = (change // u) * u
        rounding_fee = change - aligned
        if rounding == "fill":
            net = tf + rounding_fee
        else:
            acc += rounding_fee
            rebate = min((acc // u) * u, tf + rounding_fee)
            acc -= rebate
            net = tf + rounding_fee - rebate
        per.append(float(net * 100))
        total += net
    return {"fee_c": float(total * 100), "per_fill_c": per, "carry_c": float(acc * 100),
            "rounding": rounding, "precision": precision}


def fee_order_c(price_c, contracts, mult=1.0, precision="cent"):
    """Exact fee in cents for ONE fill of `contracts` at `price_c`:
    ceil_to_grid(ceil_6dp(0.07 x mult x C x P x (1-P))). 100 contracts at 50c ->
    175c; 1 contract at 50c -> 2c (the schedule's cent round-up)."""
    return fee_fills_c([(price_c, contracts)], mult, precision)["fee_c"]


# ---------------------------------------------------------------------------
# Expected value
# ---------------------------------------------------------------------------
def ev_buy(p_win, price_c, mult=1.0, contracts=None):
    """EV of buying one side at its executable ask, fee in.

    p_win        probability the side you buy pays 100c
    price_c      that side's ASK in cents (never the mid)
    contracts    None -> the smooth per-contract fee (size-free edge math);
                 N -> the exact fee of an N-contract single fill, spread per
                 contract (so contracts=1 shows the whole-cent round-up)
    Returns None when there is no offer (price missing / 0 / 100), else
    {"price_c", "fee_c", "cost_c", "ev_c", "roi", "breakeven_p"}:
        cost = price + fee ;  ev = 100 p - cost ;  roi = ev / cost ;
        breakeven_p = cost / 100 (the probability that makes ev zero)."""
    pc = _px(price_c)
    if pc is None:
        return None
    p_win = _num(p_win, "p_win")
    if not (0.0 <= p_win <= 1.0):
        raise ValueError("p_win must be in [0, 1]")
    if contracts is None:
        fee = fee_per_contract_c(pc, mult)
    else:
        if contracts <= 0:
            raise ValueError("contracts must be positive")
        fee = fee_order_c(pc, contracts, mult) / contracts
    cost = pc + fee
    ev = 100.0 * p_win - cost
    return {"price_c": pc, "fee_c": fee, "cost_c": cost, "ev_c": ev, "roi": ev / cost,
            "breakeven_p": cost / 100.0}


def ev_interval(p_lo, p_hi, price_c, side="yes", mult=1.0, contracts=None):
    """EV (cents) over a probability interval for P(YES) = (p_lo, p_hi).

    side "yes": EV rises with p, so the band is [EV(p_lo), EV(p_hi)].
    side "no":  the NO side wins with 1 - p, so the band is
                [EV_no(1 - p_hi), EV_no(1 - p_lo)] -- the unfavourable end for
                a NO buyer is the HIGH probability of YES.
    `price_c` is the ask of the side being bought. None when there is no offer.
    The band carries whatever the interval carries -- the caller's statement of
    model uncertainty, nothing this function adds."""
    if not (0.0 <= p_lo <= p_hi <= 1.0):
        raise ValueError("need 0 <= p_lo <= p_hi <= 1")
    if side == "yes":
        a, b = ev_buy(p_lo, price_c, mult, contracts), ev_buy(p_hi, price_c, mult, contracts)
    elif side == "no":
        a, b = ev_buy(1.0 - p_hi, price_c, mult, contracts), ev_buy(1.0 - p_lo, price_c, mult, contracts)
    else:
        raise ValueError("side must be 'yes' or 'no'")
    if a is None or b is None:
        return None
    return {"lo_c": a["ev_c"], "hi_c": b["ev_c"], "roi_lo": a["roi"], "roi_hi": b["roi"]}


def positive_at_lower_bound(p_lo, p_hi, price_c, side="yes", mult=1.0, contracts=None):
    """True when fee-adjusted EV is still > 0 at the unfavourable end of the
    probability interval -- the 'robust' test. False when there is no offer."""
    iv = ev_interval(p_lo, p_hi, price_c, side, mult, contracts)
    return bool(iv and iv["lo_c"] > 0.0)


def stake_profile(stake, p_win, price_c, mult=1.0, p_lo=None, p_hi=None):
    """What a dollar `stake` buys at `price_c` and what it is expected to earn.

    contracts = the most whole contracts whose exact all-in cost (price plus the
    exact single-fill fee) fits in the stake. Returns None if even one contract
    does not fit or there is no offer; else {"contracts", "cost_usd", "fee_usd",
    "expected_profit_usd", "profit_if_win_usd", "loss_if_lose_usd",
    "expected_profit_interval_usd" (when p_lo / p_hi given)}. P(win) is for the
    side bought; for a NO bet pass 1 - p (and 1 - p_hi, 1 - p_lo)."""
    pc = _px(price_c)
    if pc is None or stake <= 0:
        return None

    def total_c(n):
        return pc * n + fee_order_c(pc, n, mult)

    stake_c = stake * 100.0
    n = int(stake_c // (pc + fee_per_contract_c(pc, mult)))
    while n > 0 and total_c(n) > stake_c:
        n -= 1
    while total_c(n + 1) <= stake_c:
        n += 1
    if n < 1:
        return None
    cost_c = total_c(n)
    fee_c = fee_order_c(pc, n, mult)
    out = {"contracts": n, "cost_usd": cost_c / 100.0, "fee_usd": fee_c / 100.0,
           "expected_profit_usd": (100.0 * n * p_win - cost_c) / 100.0,
           "profit_if_win_usd": (100.0 * n - cost_c) / 100.0, "loss_if_lose_usd": -cost_c / 100.0}
    if p_lo is not None and p_hi is not None:
        out["expected_profit_interval_usd"] = [(100.0 * n * p_lo - cost_c) / 100.0,
                                               (100.0 * n * p_hi - cost_c) / 100.0]
    return out


# ---------------------------------------------------------------------------
# Refunds ("settles to a fair price")
# ---------------------------------------------------------------------------
def refund_adjusted_sides(p_yes, p_refund, fair_p):
    """Expected payoff fractions (pay_yes, pay_no) of the two sides of a Kalshi
    prop that can REFUND at a "fair price" instead of settling on the stat.

    p_yes     P(the market settles YES on the player's recorded stat)
    p_refund  P(it settles to the fair price instead), disjoint from p_yes
    fair_p    the fair price as a fraction of $1 (a YES contract pays fair_p,
              a NO contract 1 - fair_p). The rules do not publish it and it is
              UNMEASURED here: it is the exchange's own determination, and the
              settled markets seen do NOT show it tracking the last traded price
              (KXNBAPTS, 2026-10-21: Nesmith 10+ settled 0.71 against a last
              trade of 0.00, 15+ 0.39 against 0.84; Monk 10+ 0.51 against 0.46,
              15+ 0.22 against 0.34 -- last prices of thin or in-game books may
              be stale, so these are counterexamples to "near the last price",
              not a measurement of the rule). Whatever you pass -- the pre-tip YES
              mid is the obvious guess -- is a GUESS: say so wherever the
              result is shown, and look at how much the EV moves across a range
              of fair_p before trusting it.
    pay_yes = p_yes + p_refund x fair_p ; pay_no = p_no + p_refund x (1 - fair_p),
    p_no = 1 - p_yes - p_refund, and pay_yes + pay_no == 1. Feed them to
    ev_buy as the side's `p_win`: EV is linear in the expected payoff.

    Standard NBA props refund when the player is inactive or never takes the
    court (5 of 180 settled KXNBAPTS player-games, 2.8%; 3 of the 5 were
    preseason games, 2 of 174 regular-season ones). Protected props also
    refund when the player plays only the first half and has not reached k,
    so p_refund there includes P(exits at half and short) -- see
    kalshi_nba_props's module docstring."""
    for name, v in (("p_yes", p_yes), ("p_refund", p_refund), ("fair_p", fair_p)):
        v = _num(v, name)
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"{name} must be in [0, 1]")
    if p_yes + p_refund > 1.0 + 1e-12:
        raise ValueError("p_yes + p_refund must not exceed 1")
    p_no = max(0.0, 1.0 - p_yes - p_refund)
    return p_yes + p_refund * fair_p, p_no + p_refund * (1.0 - fair_p)


# ---------------------------------------------------------------------------
# Walking the book
# ---------------------------------------------------------------------------
def asks_from_orderbook(orderbook, side="yes"):
    """The ask ladder for BUYING `side`, [(price_c, size), ...] cheapest first,
    from a Kalshi /markets/{ticker}/orderbook payload.

    The book lists BIDS only, ascending with the best bid LAST; a NO bid at y is
    the same order as a YES ask at 100 - y (and vice versa), so buying YES lifts
    the NO bids. Reads the current shape ({"orderbook_fp": {"yes_dollars":
    [["0.4200", "13.00"], ...], "no_dollars": ...}}) and the legacy one
    ({"orderbook": {"yes": [[42, 13], ...], "no": ...}} in cents). Prices
    outside (0, 100) are dropped; sizes are floats."""
    if side not in ("yes", "no"):
        raise ValueError("side must be 'yes' or 'no'")
    book = orderbook.get("orderbook_fp") or orderbook.get("orderbook") or orderbook
    other = "no" if side == "yes" else "yes"
    rows = book.get(f"{other}_dollars")
    scale = 100.0
    if rows is None:
        rows, scale = book.get(other), 1.0
    out = []
    for r in rows or []:
        try:
            bid_c, size = float(r[0]) * scale, float(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        ask = round(100.0 - bid_c, 6)
        if _px(ask) is not None and size > 0:
            out.append((ask, size))
    out.sort(key=lambda x: x[0])
    return out


def walk_book(asks, contracts, p_win, mult=1.0, precision="cent", rounding="order"):
    """Cost and EV of buying `contracts` by sweeping an ask ladder.

    asks        [(price_c, size), ...] (any order; sorted cheapest first here)
    Each level is filled at ITS OWN price. The fee is the exact order fee for
    the resulting fills (fee_fills_c: per-fill round-up to 1e-6 with the order's
    rebate accumulator; rounding="fill" for the no-accumulator upper bound).
    Returns {"requested", "filled", "shortfall", "fills": [{"price_c", "qty",
    "fee_c"}], "principal_c", "fee_c", "cost_c", "avg_price_c", "avg_cost_c",
    "ev_c" (total), "ev_per_contract_c", "roi", "marginal": {"price_c",
    "fee_c" (smooth), "ev_c"} of the LAST level touched}. Nothing is filled
    from an empty book (filled 0, roi None)."""
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    p_win = _num(p_win, "p_win")
    book = sorted(((float(p), float(q)) for p, q in asks if _px(p) is not None and q > 0))
    want, fills = float(contracts), []
    for price, size in book:
        if want <= 1e-12:
            break
        take = min(size, want)
        fills.append((price, take))
        want -= take
    filled = sum(q for _, q in fills)
    out = {"requested": float(contracts), "filled": filled, "shortfall": max(0.0, float(contracts) - filled),
           "fills": [], "principal_c": 0.0, "fee_c": 0.0, "cost_c": 0.0, "avg_price_c": None,
           "avg_cost_c": None, "ev_c": None, "ev_per_contract_c": None, "roi": None, "marginal": None}
    if not fills:
        return out
    fee = fee_fills_c(fills, mult, precision, rounding)
    principal = sum(p * q for p, q in fills)
    cost = principal + fee["fee_c"]
    ev = 100.0 * p_win * filled - cost
    last_p = fills[-1][0]
    last_fee = fee_per_contract_c(last_p, mult)
    out.update(
        fills=[{"price_c": p, "qty": q, "fee_c": f} for (p, q), f in zip(fills, fee["per_fill_c"])],
        principal_c=principal, fee_c=fee["fee_c"], cost_c=cost,
        avg_price_c=principal / filled, avg_cost_c=cost / filled,
        ev_c=ev, ev_per_contract_c=ev / filled, roi=ev / cost,
        marginal={"price_c": last_p, "fee_c": last_fee, "ev_c": 100.0 * p_win - last_p - last_fee})
    return out


def max_size_positive_ev(asks, p_win, mult=1.0):
    """How many contracts the book will sell before the NEXT one stops being
    +EV: levels are taken while 100 p - price - smooth fee > 0. Cost per
    contract (price + fee) rises with price on (0, 100) at any multiplier below
    14, so marginal EV only falls as the ladder is climbed and the first
    non-positive level ends it. Returns {"contracts", "levels", "avg_price_c",
    "limit_price_c"} (contracts 0 when even the best ask is not +EV). The
    smooth fee is used for the marginal test; price the resulting size with
    walk_book for the exact order cost."""
    book = sorted(((float(p), float(q)) for p, q in asks if _px(p) is not None and q > 0))
    n, cost, levels, limit = 0.0, 0.0, 0, None
    for price, size in book:
        if 100.0 * p_win - price - fee_per_contract_c(price, mult) <= 0.0:
            break
        n += size
        cost += price * size
        levels += 1
        limit = price
    return {"contracts": n, "levels": levels, "avg_price_c": (cost / n) if n else None,
            "limit_price_c": limit}


# ---------------------------------------------------------------------------
# The line value (the payload contract's `value` field) and labels
# ---------------------------------------------------------------------------
LABELS = ("high_probability", "positive_ev", "positive_ev_robust", "strong_fit", "low_evidence")
# Qualifiers are not value judgements: they say WHY a positive-EV label was
# withheld. They appear only in place of a positive_ev / positive_ev_robust the
# numbers would otherwise have earned. thin_book: the YES book is not an
# opinion (book_is_believable false: one-sided, wider than 20c, or nothing
# traded). stale_quote: the quote is older than `max_quote_age_s`, or carries no
# timestamp (an unknown age is not a fresh one).
QUALIFIERS = ("thin_book", "stale_quote")
# Conventions, NOT fitted values: 0.70 is where "likely" starts in plain speech;
# 20 effective games is where a hit rate's Wilson half-width drops below ~20
# points; 0.25 is an interval that spans a quarter of the probability range.
# Change them by passing arguments, and record why where you do.
LABEL_DEFAULTS = {"high_p": 0.70, "min_n_eff": 20.0, "max_width": 0.25, "max_quote_age_s": 600.0}
# 600 s: the index is rebuilt every 120 s and a board is built from it within
# seconds, so a quote ten minutes old means a cached index or a stalled
# builder, not a normal build. A convention, like the others, not a fitted value.
_FORBIDDEN = ("lock",)
for _lab in LABELS + QUALIFIERS:          # an edit to either tuple fails at import, not in production
    if any(_w in _lab for _w in _FORBIDDEN):
        raise ValueError(f"label {_lab!r} contains a forbidden word")


def line_value(p, market, p_lo=None, p_hi=None, fee_mult=None, series=None, contracts=None,
               stake=None, ref_method="mid", lookup_fee=False, now=None, max_spread_c=20.0):
    """Everything the UI shows under a line's `value`, from a model probability
    and a market block. Drops straight into the payload contract's `value`.

    p             model P(stat >= k) = P(YES), calibrated, 0..1
    market        kalshi_nba_props' market block (cents): yes_bid, yes_ask,
                  no_bid, no_ask; optional ticker, fee_type, fee_multiplier
    p_lo, p_hi    the model's probability band (both or neither); p must sit
                  inside it
    fee_mult      override; else market["fee_multiplier"]; else (lookup_fee
                  True) the series' own via kalshi.series_info; else 1.0 with
                  `fee_assumed: True`. All five NBA prop series are 1.0
                  (verified 2026-10-23).
    contracts     None -> smooth fee (size-free); N -> exact N-contract fee
    stake         dollars; adds a "stake" block (contracts, cost, expected profit)
    now           epoch seconds the quote's age is measured at (default: the
                  current time); max_spread_c is book_is_believable's limit

    Returns (all money in CENTS per contract, see the module units):
      p, p_lo, p_hi
      market_kind     "two_sided" | "one_sided" | "none"
      p_market        the no-vig reference for YES (kalshi_reference), or None
      edge            p - p_market (None without a reference). A reference from
                      a one-sided book is an upper bound on an ask: read the
                      sign with market_kind in view.
      edge_exec       {"yes": p - (ask+fee)/100, "no": (1-p) - (ask+fee)/100}:
                      the margin over the all-in break-even on each side
      price_used      {"yes": ask_c, "no": ask_c}  (None = no offer)
      fee             {"yes": fee_c, "no": fee_c}
      ev_yes, ev_no   cents per contract, fee in (None = no offer)
      roi_yes, roi_no, roi (best side's)
      ev_interval     {"yes": [lo, hi], "no": [lo, hi]} cents, or None per side
      best_side       "yes" | "no" | None  (larger EV among offered sides)
      ev_best, ev_lo_best (best side's EV at the unfavourable end of the band)
      positive_at_lower_bound   bool for the best side (False without a band)
      believable      book_is_believable(market, max_spread_c): is the YES book
                      an opinion? line_labels withholds positive_ev on False.
      quote_ts, quote_age_s   market["ts"] and `now` minus it (None when the
                      block has no timestamp); line_labels withholds positive_ev
                      on a quote older than max_quote_age_s or of unknown age.
      book            {"kind", "spread_c", "volume", "oi"} behind `believable`
      fee_type, fee_multiplier, fee_assumed, contracts_basis, units, stake?"""
    p = _num(p, "p")
    if not (0.0 <= p <= 1.0):
        raise ValueError("p must be in [0, 1]")
    if (p_lo is None) != (p_hi is None):
        raise ValueError("give both p_lo and p_hi or neither")
    if p_lo is not None and not (0.0 <= p_lo <= p <= p_hi <= 1.0):
        raise ValueError("need 0 <= p_lo <= p <= p_hi <= 1")
    market = market or {}
    fp = fee_params(series or str(market.get("ticker") or "").split("-")[0] or None,
                    market.get("fee_type"),
                    fee_mult if fee_mult is not None else market.get("fee_multiplier"),
                    lookup=lookup_fee)
    mult = float(fee_mult) if fee_mult is not None else fp["fee_multiplier"]
    assumed = fp["multiplier_source"] == "default"
    ref = kalshi_reference(market, ref_method)
    ts = market.get("ts")
    ts = float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts) else None
    age = None if ts is None else (time.time() if now is None else float(now)) - ts

    ya = _px(market.get("yes_ask"))
    na = _px(market.get("no_ask"))
    if na is None and _px(market.get("yes_bid")) is not None:
        na = _px(100.0 - _px(market.get("yes_bid")))      # NO ask = 100 - YES bid
    if ya is None and _px(market.get("no_bid")) is not None:
        ya = _px(100.0 - _px(market.get("no_bid")))

    legs = {"yes": (ya, p, p_lo, p_hi), "no": (na, 1.0 - p, None if p_hi is None else 1.0 - p_hi,
                                                 None if p_lo is None else 1.0 - p_lo)}
    ev, price, fee, ivs, edge_exec = {}, {}, {}, {}, {}
    for side, (ask, pw, lo, hi) in legs.items():
        e = ev_buy(pw, ask, mult, contracts)
        ev[side] = e
        price[side] = e["price_c"] if e else None
        fee[side] = e["fee_c"] if e else None
        edge_exec[side] = (pw - e["breakeven_p"]) if e else None
        if e and lo is not None:
            iv = ev_interval(p_lo, p_hi, ask, side, mult, contracts)
            ivs[side] = [iv["lo_c"], iv["hi_c"]]
        else:
            ivs[side] = None
    offered = [s for s in ("yes", "no") if ev[s] is not None]
    best = max(offered, key=lambda s: ev[s]["ev_c"]) if offered else None
    out = {
        "p": p, "p_lo": p_lo, "p_hi": p_hi,
        "market_kind": ref["kind"], "p_market": ref["p"], "p_market_method": ref["method"],
        "edge": None if ref["p"] is None else p - ref["p"],
        "edge_exec": edge_exec,
        "price_used": price, "fee": fee,
        "ev_yes": ev["yes"]["ev_c"] if ev["yes"] else None,
        "ev_no": ev["no"]["ev_c"] if ev["no"] else None,
        "roi_yes": ev["yes"]["roi"] if ev["yes"] else None,
        "roi_no": ev["no"]["roi"] if ev["no"] else None,
        "roi": ev[best]["roi"] if best else None,
        "ev_interval": ivs,
        "best_side": best,
        "ev_best": ev[best]["ev_c"] if best else None,
        "ev_lo_best": (ivs[best][0] if (best and ivs[best]) else None),
        "positive_at_lower_bound": bool(best and ivs[best] and ivs[best][0] > 0.0),
        "believable": book_is_believable(market, max_spread_c),
        "quote_ts": ts, "quote_age_s": age,
        "book": {"kind": ref["kind"], "spread_c": ref["spread_c"],
                 "volume": market.get("volume"), "oi": market.get("oi")},
        "fee_type": fp["fee_type"], "fee_multiplier": mult, "fee_assumed": bool(assumed),
        "contracts_basis": contracts,
        "units": {"prob": "fraction", "price_used": "cents", "fee": "cents per contract",
                  "ev": "cents per contract", "roi": "fraction of all-in cost"},
    }
    if stake is not None and best:
        pw, lo, hi = legs[best][1], legs[best][2], legs[best][3]
        out["stake"] = stake_profile(stake, pw, price[best], mult, lo, hi)
    return out


def labels_from(*, p_side, ev_c=None, ev_lo_c=None, width=None, n_eff=None, strong_fit=None,
                high_p=None, min_n_eff=None, max_width=None, book_ok=True, quote_fresh=True):
    """The label list from explicit numbers (the engine of line_labels).

    p_side     probability the evaluated side wins
    ev_c       fee-adjusted EV (cents) of that side at the executable ask, or
               None (no offer: neither EV label can apply)
    ev_lo_c    that EV at the unfavourable end of the probability interval, or
               None (no interval: positive_ev_robust cannot apply)
    width      p_hi - p_lo, or None;  n_eff  effective sample size, or None.
               positive_ev_robust is refused on a band KNOWN to have no width: a zero-width band
               (p_lo = p = p_hi) makes "still positive at the band's low end" the
               same statement as "positive" -- the first NFL board (2026-10-25)
               carried such bands on 99% of its lines and called all 786 of its
               +EV lines robust.
    strong_fit bool from the matchup model (formatted, never computed here)
    book_ok, quote_fresh   False withholds positive_ev / positive_ev_robust and
               returns the qualifier(s) thin_book / stale_quote in their place --
               only when the numbers would otherwise have earned an EV label.
    Order follows LABELS, then QUALIFIERS. Thresholds default to LABEL_DEFAULTS."""
    hp = LABEL_DEFAULTS["high_p"] if high_p is None else high_p
    mn = LABEL_DEFAULTS["min_n_eff"] if min_n_eff is None else min_n_eff
    mw = LABEL_DEFAULTS["max_width"] if max_width is None else max_width
    got = set()
    if p_side is not None and p_side >= hp:
        got.add("high_probability")
    ev_earned = set()
    if ev_c is not None and ev_c > 0.0:
        ev_earned.add("positive_ev")
    if ev_lo_c is not None and ev_lo_c > 0.0 and (width is None or width > 1e-9):
        ev_earned.add("positive_ev_robust")
    if ev_earned and not (book_ok and quote_fresh):
        if not book_ok:
            got.add("thin_book")
        if not quote_fresh:
            got.add("stale_quote")
    else:
        got |= ev_earned
    if strong_fit is True:
        got.add("strong_fit")
    if (n_eff is not None and n_eff < mn) or (width is not None and width > mw) \
            or (n_eff is None and width is None):
        got.add("low_evidence")
    return [lab for lab in LABELS + QUALIFIERS if lab in got]


def line_labels(value, *, side=None, strong_fit=None, n_eff=None, **thresholds):
    """The payload contract's `labels` list for a line_value() result.

    side defaults to the value's best_side (the side with the larger EV), else
    "yes" for the descriptive probability label only. high_probability reads
    the chosen side's own probability (1 - p for NO). The EV labels read that
    same side's EV / interval, so a NO label never rides on YES numbers.

    positive_ev / positive_ev_robust are awarded only on a BELIEVABLE book
    (value["believable"]) whose quote is no older than `max_quote_age_s`
    (default LABEL_DEFAULTS, 600 s; an unknown age counts as stale). Otherwise
    the qualifier "thin_book" and/or "stale_quote" is returned in place of the
    label the numbers would have earned: a model's edge over a 2c / 68c listing
    is not an edge. `thresholds` may carry high_p, min_n_eff, max_width,
    max_quote_age_s."""
    side = side or value.get("best_side") or "yes"
    max_age = thresholds.pop("max_quote_age_s", None)
    max_age = LABEL_DEFAULTS["max_quote_age_s"] if max_age is None else max_age
    age = value.get("quote_age_s")
    p = value["p"]
    p_side = p if side == "yes" else 1.0 - p
    ev = value.get("ev_yes") if side == "yes" else value.get("ev_no")
    iv = (value.get("ev_interval") or {}).get(side)
    lo, hi = value.get("p_lo"), value.get("p_hi")
    return labels_from(p_side=p_side, ev_c=ev, ev_lo_c=(iv[0] if iv else None),
                       width=(None if lo is None else hi - lo), n_eff=n_eff,
                       strong_fit=strong_fit, book_ok=bool(value.get("believable")),
                       quote_fresh=(age is not None and age <= max_age), **thresholds)


def label_reasons(value, *, side=None, strong_fit=None, n_eff=None, **thresholds):
    """{label: plain-language reason with the numbers} for the labels line_labels
    awards, so a UI can show why. Same inputs, same thresholds."""
    th = dict(LABEL_DEFAULTS)
    th.update({k: v for k, v in thresholds.items() if v is not None})
    side = side or value.get("best_side") or "yes"
    p = value["p"] if side == "yes" else 1.0 - value["p"]
    ev = value.get("ev_yes") if side == "yes" else value.get("ev_no")
    iv = (value.get("ev_interval") or {}).get(side)
    lo, hi = value.get("p_lo"), value.get("p_hi")
    why = {}
    max_age = th["max_quote_age_s"]
    for lab in line_labels(value, side=side, strong_fit=strong_fit, n_eff=n_eff, **thresholds):
        if lab == "high_probability":
            why[lab] = f"{side.upper()} wins in {p:.1%} of the model's outcomes (>= {th['high_p']:.0%}); says nothing about price"
        elif lab == "positive_ev":
            why[lab] = f"{side.upper()} at {value['price_used'][side]:.0f}c + {value['fee'][side]:.2f}c fee: EV {ev:+.2f}c per contract"
        elif lab == "positive_ev_robust":
            why[lab] = f"EV stays {iv[0]:+.2f}c at the unfavourable end of the probability band"
        elif lab == "strong_fit":
            why[lab] = "flagged by the matchup model's own measured fit"
        elif lab == "thin_book":
            bk = value.get("book") or {}
            kind = {"two_sided": "two-sided", "one_sided": "one-sided", "none": "empty"}.get(bk.get("kind"), "unknown")
            sp = bk.get("spread_c")
            traded = (bk.get('volume') or 0) + (bk.get('oi') or 0)
            why[lab] = (f"EV label withheld: {kind} book"
                        + (f", {sp:.0f}c wide" if sp is not None else "")
                        + f", {traded:g} contracts traded or open"
                        # a one-sided book that has traded is real, just not two-sided; an
                        # untraded first listing is not an opinion at all (Fable re-check R-1)
                        + (" -- no offer on the other side" if traded > 0 and bk.get("kind") == "one_sided"
                           else " -- a listing, not an opinion"))
        elif lab == "stale_quote":
            age = value.get("quote_age_s")
            why[lab] = ("EV label withheld: the quote has no timestamp" if age is None
                        else f"EV label withheld: the quote is {age:.0f}s old (> {max_age:g}s)")
        elif lab == "low_evidence":
            bits = []
            if n_eff is not None and n_eff < th["min_n_eff"]:
                bits.append(f"effective sample {n_eff:.1f} < {th['min_n_eff']:g}")
            if lo is not None and hi - lo > th["max_width"]:
                bits.append(f"probability band {hi - lo:.2f} wide > {th['max_width']:g}")
            if n_eff is None and lo is None:
                bits.append("no sample size or interval supplied")
            why[lab] = "; ".join(bits)
    return why


def combo_ev(p_joint, p_gross):
    """EV per $1 staked on a Kalshi COMBO priced by the maker at basket price
    `p_gross` (a fraction of $1), through combo_engine.combo_cost -- the
    exchange's single fee on the basket (0.07 x P(1-P), fee_type
    "quadratic_with_combo_maker_fees"), not a fee per leg. Returns
    {"cost", "ev"} or None for a price that is not a price."""
    import combo_engine
    cost = combo_engine.combo_cost(p_gross)
    if cost is None or not (0.0 < p_gross < 1.0):
        return None
    return {"cost": cost, "ev": combo_engine.ev(p_joint, cost)}
