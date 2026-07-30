"""Grade our model -- and Kalshi -- against a sharp bookmaker consensus.

tennis_backtest says in its own docstring that it has no market benchmark and so
"can't answer 'do we beat the price'". This is that benchmark: the de-vigged
consensus of 7-8 books, which is the sharpest free read available on a tennis
match.

It answers two different questions, and the second is the more useful one:

  1. How far is OUR number from the books?
  2. How far is KALSHI from the books? Kalshi is what we trade against and what
     every model here is scored on, so knowing whether it is sharp or soft is
     worth as much as knowing whether we are right. On the first run: Kalshi sat
     1.0pp from the consensus and we sat 4.0pp, which says Kalshi is sharp on
     tour-level tennis and our deference to it is well founded.

COST. One request per sport, cached for hours. A full run is 1-2 requests against
a 500/month free tier. Run it daily at most; it is a grader, not a feed.

Coverage is tour-level only -- the books do not price ITF, which is most of a
Kalshi tennis board -- so treat this as a check on the model's MACHINERY, not as
coverage of the slate.

Run:  ODDS_API_KEY=... python3 tests/odds_benchmark.py
"""
import os
import statistics
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import odds_api


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def main():
    if not odds_api.enabled():
        print("ODDS_API_KEY not set -- nothing to do.")
        return 0
    keys = odds_api.tennis_keys()          # free endpoint
    print(f"tennis sports listed: {keys or '(none in season)'}")
    print(f"credits remaining before: {odds_api.remaining()}")
    if not keys:
        print("No tennis priced by the books right now.")
        return 0

    rows = []
    for sk in keys:
        r, err = odds_api.board(sk)
        if err:
            print(f"  {sk}: {err}")
        rows += r
    print(f"consensus events: {len(rows)};  credits after: {odds_api.remaining()}")
    if not rows:
        return 0

    import tennis_prices
    board = tennis_prices.board() or tennis_prices._compute(n_sims=4000)
    ours = {}
    for m in (board or {}).get("matches", []):
        for s in (m["a"], m["b"]):
            if s.get("fair_win") is not None:
                ours[norm(s["name"])] = (s["fair_win"], s.get("mkt_win"),
                                         m.get("model_source"))

    print()
    print(f"{'player':<24s}{'books':>7s}{'kalshi':>8s}{'ours':>7s}"
          f"{'ours-books':>11s}  source")
    gaps, kgaps = [], []
    for r in rows:
        for nm, p in (r.get("probs") or {}).items():
            hit = ours.get(norm(nm))
            if not hit:
                continue
            fair, mkt, src = hit
            gaps.append((abs(fair - p), nm, p, mkt, fair, src))
            if mkt is not None:
                kgaps.append(abs(mkt - p))
    for _, nm, p, mkt, fair, src in sorted(gaps, reverse=True):
        print(f"{nm[:23]:<24s}{p:7.1f}{(mkt if mkt is not None else float('nan')):8.1f}"
              f"{fair:7.1f}{fair-p:+11.1f}  {src}")

    if not gaps:
        print("\nNo overlap between the books' slate and ours right now.")
        return 0
    om = statistics.mean(g[0] for g in gaps)
    print()
    print(f"  matched sides:            {len(gaps)}")
    print(f"  mean |ours   - books|:    {om:.1f}pp")
    if kgaps:
        km = statistics.mean(kgaps)
        print(f"  mean |kalshi - books|:    {km:.1f}pp")
        verdict = ("Kalshi is SHARP here -- deferring to it is well founded"
                   if km < om else
                   "we are closer to the books than Kalshi is -- our edge is real")
        print(f"  -> {verdict}")
    worst = max(gaps)
    if worst[0] >= 10:
        print(f"\n  BIGGEST MISS: {worst[1]} -- books {worst[2]:.1f}%, "
              f"ours {worst[4]:.1f}% ({worst[4]-worst[2]:+.1f}pp, {worst[5]})")
        print("  A double-digit gap against 7-8 books is USUALLY our bug, not an edge.")
        print("  But check the matchup before assuming so: books price injury news,")
        print("  late withdrawals and retirements that no results-based model can see.")
        print("  A slate here had eight books unanimously at 1.50 on a teenager over a")
        print("  top-20 seed -- that is not a rating error, it is information we do")
        print("  not have, and no amount of fitting will recover it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
