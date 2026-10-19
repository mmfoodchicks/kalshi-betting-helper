# The NFL combo maker, audited

*2026-10-19, on the live slates: Monday night (WAS @ SF, week 6) and week 7.
Every slip below came from `nfl_game_sim.build_parlay`, the function the tab
calls, against Kalshi's live book.*

## Working as it should

- **It builds.** Week 6 and week 7 both built end to end. A cold slate
  simulates 14 games in 6-13 seconds, and each build after that takes about a
  second. The error ledger shows no NFL combo failures in the 7 days to
  2026-10-19 04:37 UTC.
- **Started games are out.** On Monday, 13 of week 6's 14 games were
  excluded and only tonight's game was offered.
- **Legs are stamped.** Each leg carries its Kalshi ticker and kickoff, so
  the slip ledger can grade it.
- **The payout math matches combo_engine.** The payout is the product of the
  asks, with Kalshi's one combo fee taken on the basket.
- **Moneylines.**
  - Week 7: the model and Kalshi differ by 3.3 points on average.
  - The model is somewhat flatter than Kalshi on the big favourites: SF @ ATL
    59% against 69%, LAR @ LV 61% against 69%, DEN @ ARI 59% against 66%.
  - Tonight's game: identical.
  - Replayed over 2026 weeks 1-6 (92 games), the model's log loss is 0.645,
    against 0.688 for a flat 54% home pick. Caveat: the replay used today's
    defensive ratings, so it has a small look-ahead.
- **Spreads.** Week 7: +0.1 on average, 2.9 points of probability apart per
  rung.

## Fixed

1. **Same-game stacks were paid for their own correlation.**
   - **The problem:** a stack's chance came from the sim's correlated joint,
     but its price was the product of the legs' asks. Kalshi's market maker
     prices that correlation into his quote (measured 2026-09-08: asks that
     multiplied to ~1,600x were quoted 133x). So every stack showed its
     correlation as edge.
   - **Tonight's example:** the default 4-leg slip read **+25.9% EV at
     1.80x**, with every leg's own number under its ask: Daniels 125+ pass
     yards, McCaffrey 15+ receiving yards and 3+ catches, over 41.5. The legs
     hit together 70% of the time, against 47% if they were independent. All
     of the "edge" was that gap.
   - **The fix** (`combo_engine.stack_lift`): a stack now costs its asks
     times its correlation (its joint chance over the product of its legs'
     chances). That is the quote a maker who sees the correlation exactly as
     the sim does would give. It is the most generous quote possible, so a
     real quote lands at or under it.
   - **After:** every Monday-night slip reads negative EV. The default is
     −11.6%, and the slip shows its "nothing clears break-even" warning. A
     stacked slip now shows the correlation-priced quote beside what the asks
     multiply to.
   - **Scope:** this is the shared engine, so stacked slips in every sport's
     maker are priced the same way.
2. **Legs Kalshi doesn't list could sit on a Kalshi slip.**
   - Baseball, college and UFC already drop them; the NFL maker kept them at
     fair value. Week 7's default 4-leg slip under an 80% cap carried "NO -
     Kenyon Sadiq anytime TD", which has no market.
   - Now the pool holds only listed legs while Kalshi's index answers. The
     slip says how many were left out: 702 on week 7 this morning, mostly
     props not posted yet. With an empty index the maker falls back to
     model-only legs and says so.
3. **The sim's totals ran 2-3 points low.** The 2025 trims assume Sleeper
   over-projects, and 2026's Sleeper doesn't. Refit by a rule written first;
   week 7's gap to Kalshi went from −2.55 to −0.26 points a game. Details in
   `research/reports/nfl_trim.md`.
   - **Why it mattered:** combined with fix 1, it had turned the maker into
     an under-and-NO machine. Every slip was unders and NOs, each "edge" being
     the sim's low scoring leaking through the blend.
   - **After both fixes,** week 7's slips mix overs, unders and spreads, at
     +0.1% to +6% EV.
4. **On Tuesdays the tab opened on the finished week.** `current_week`
   compared the UTC date in ESPN's kickoff string with today in Eastern time.
   Monday night's 8:15 pm ET kickoff is 00:15Z Tuesday, so all of Tuesday
   week 6 still counted as current. It now reads each kickoff's Eastern date.
5. **Failed Kalshi reads while pricing legs were silent.** They are now
   ledgered as `NFLG-price-idx` and `NFLG-price-leg`.

Each fix has a guard in `tests/combo_audit_guards.py` that fails on the old
code.

## Still true, not changed

- **The EV on a stacked slip is an upper bound.** The one measured quote came
  in well under even the sim's own fair price (133x against 200x).
- **The quote on one-leg-a-game slips is unmeasured.** `QUOTE_ROOM` assumes
  it lands a little under the product of the asks. Nobody has reported one
  yet.
- **Tonight's props still read 3-6 points under Kalshi after the refit.**
  That is Sleeper's view of particular players (McCaffrey's receiving, for
  one). The blend gives most of the weight to the market on those legs.
- **Only moneylines have a graded NFL record.** Totals, spreads and props run
  on their trust priors until the slip ledger grades enough of them.
