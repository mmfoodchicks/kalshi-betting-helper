# Orchestrator review: prop_value / prop_dist / kalshi_nba_props

Independent of the Fable review (research/reviews/prop_math.md). 2026-10-23 ~01:40 UTC.

Checked:
- Guards: 187 passed, 0 failed (standalone). pyflakes clean. Imports: stdlib + kalshi, kalshi_nfl,
  errlog (no numpy) -- AGREE.
- Fee per order vs ceil(0.07 x C x P x (1-P)) to the cent, prices 1/10/44/50/66/99c x 1/7/100
  contracts: identical -- AGREE.
- EV at p=0.60, YES ask 55c / NO ask 47c: the module gives YES +3.27c, NO -8.74c, interval YES
  [-1.73, +8.27], NO [-13.74, -3.74] over p in [0.55, 0.65]; the direction of the NO interval is
  right (p_hi -> NO's low end) -- AGREE.

Findings:
- O1 (minor, integration): with `contracts=None` the fee is the exact per-contract amount (1.73c at
  55c) rather than the order's rounded-up cent (2c for a 1-contract order), so EV is up to ~1c
  optimistic for very small orders. Integration must pass an explicit contract basis and the UI
  must say what it assumes.
- O2 (major, integration): `positive_ev` fires on an ask-only (one-sided) book. Buying at the ask
  is executable, so the EV is not wrong, but a one-sided or stale book is weak price evidence.
  Integration gates the value labels on a believable, fresh quote (kalshi_nba_props.believable and
  a quote age limit) and shows price quality beside the label.

Verdict (orchestrator): AGREE AFTER the two integration conditions above. Final status waits for
Fable's review and reconciliation in the agreement log.
