"""Payout-governed parlay selection.

When you target a big multiplier (say 350x), this picks the legs that reach that
payout with the HIGHEST combined probability -- i.e. the least overshoot. It
prefers your requested leg count, and only adds legs when that count physically
can't reach the target (rather than forcing in a near-zero "punt" leg).

Honest note baked into the result: for legs with no separate market price (most
props), the parlay's win chance equals 1/payout no matter how you slice it --
6 low-confidence legs or 14 high-confidence legs that both multiply to 350x have
the same ~1-in-350 chance. The leg count mainly controls how close you land to
the target (granularity) and how much per-leg market edge you can stack. The
selector maximizes combined probability either way, so it's optimal regardless.
"""

import math


def leg_mult(leg):
    """Per-leg payout multiplier: from the market price if priced, else fair (1/p)."""
    pc = leg.get("price_cents")
    if pc and pc > 0:
        return 100.0 / pc
    p = leg.get("prob") or 0.0
    return (1.0 / p) if 0 < p < 1 else 1.0


def payout_combo(groups, n_legs, target_payout, max_legs=12, res=0.04):
    """Pick a parlay reaching `target_payout` with max combined probability.

    groups: list (one per event) of candidate leg dicts; at most one leg is taken
    per event. Each leg needs 'prob' and optionally 'price_cents'.

    Returns {legs, n_used, reached, expanded, requested_legs, combined_prob,
    payout, all_priced} or None. Prefers n_legs; expands the count only when
    n_legs can't reach the target.
    """
    groups = [g for g in groups if g]
    if len(groups) < 2 or not target_payout or target_payout <= 1:
        return None
    max_legs = max(2, min(max_legs, len(groups)))
    n_legs = max(2, min(n_legs, max_legs))
    T = math.log(target_payout)
    cap_b = int((T + 3.5) / res) + 1            # stop tracking far past the target

    # dp[k] : bucket(of total log-payout) -> (sum_log_prob, actual_log_payout, legs)
    dp = [dict() for _ in range(max_legs + 1)]
    dp[0][0] = (0.0, 0.0, ())
    for variants in groups:
        for k in range(max_legs - 1, -1, -1):   # descending => one leg per event
            if not dp[k]:
                continue
            for b, (slp, slm, sel) in list(dp[k].items()):
                for leg in variants:
                    p = leg.get("prob") or 0.0
                    if not (0 < p < 1):
                        continue
                    # Target the FAIR payout (1/prob) so it matches the displayed
                    # fair-payout number. Market edge on priced legs is reported
                    # separately, not folded into the target.
                    nlp = slp + math.log(p)
                    nlm = slm - math.log(p)
                    nb = min(cap_b, int(nlm / res))
                    cur = dp[k + 1].get(nb)
                    if cur is None or nlp > cur[0]:
                        dp[k + 1][nb] = (nlp, nlm, sel + (leg,))

    def best_reaching(k):
        best = None
        for slp, slm, sel in dp[k].values():
            if slm >= T - 1e-9 and (best is None or slp > best[0]):
                best = (slp, slm, sel)
        return best

    chosen, reached = None, False
    for k in range(n_legs, max_legs + 1):        # prefer n_legs, then expand up
        b = best_reaching(k)
        if b:
            chosen, reached = b, True
            break
    if not chosen:                               # can't reach: take max payout we can
        best = None
        for k in range(2, max_legs + 1):
            for slp, slm, sel in dp[k].values():
                if best is None or slm > best[1]:
                    best = (slp, slm, sel)
        chosen = best
    if not chosen:
        return None

    slp, slm, sel = chosen
    return {
        "legs": list(sel),
        "n_used": len(sel),
        "reached": reached,
        "expanded": reached and len(sel) > n_legs,
        "requested_legs": n_legs,
        "combined_prob": math.exp(slp),
        "payout": math.exp(slm),
        "all_priced": all(l.get("price_cents") for l in sel),
    }
