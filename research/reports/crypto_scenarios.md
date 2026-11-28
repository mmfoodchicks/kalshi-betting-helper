# Up, sideways or down -- and when a direction lean is a coin flip (crypto terminal UI v3)

2026-11-28. Script `research/crypto_scenarios.py`; the rule is its docstring, committed in 7ddb640 before any
held-out figure was computed. Figures: `research/data/crypto/scenarios.json`. The owner asked for upward /
sideways / downward scenarios with calibrated probabilities, "Direction uncertain" wherever a lean cannot be
told from 50%, and the random walk's figures kept apart from validated ones everywhere.

**Data.** `research/crypto_moves.py`'s own: Coinbase one-minute candles for BTC, ETH, SOL, XRP and DOGE,
development 2026-10-16..11-12, held-out 11-13..11-26, its causal features. The held-out fortnight had been read
once already, for the direction test (moves.json). The scenario question is a different one, scored once here,
but the two verdicts share their held-out minutes and are not independent evidence.

## A. Scenarios (the rule's verdict: VALIDATED at 3 minutes, 5 minutes and to the 15-minute close)

"Sideways" means ending within one meaningful move of the price now: 2 x the EWMA one-minute SD, the unit the
terminal already uses for swings and dips. "Up" and "down" mean ending at least that far away. The null is the
random walk at production's scale (`VOL_MULT` 0.9). Because the band is 2 SDs, the null's odds depend on the
horizon alone: 10.0 / 80.1 / 10.0% at 3 minutes and 16.0 / 68.0 / 16.0% at 5. The challenger is a three-class
softmax on the null's log-odds plus the twelve direction features (lambda 1000 chosen on development at every
horizon).

| horizon | held-out rows | real up / side / down | null's mean | gain over the null, nats a row | z | coins better |
|---|---|---|---|---|---|---|
| 3 min | 100,800 | 9.7 / 80.3 / 10.1% | 10.0 / 80.1 / 10.0% | +0.00565 +/- 0.00122 | 4.6 | 5 of 5 |
| 5 min | 100,800 | 14.6 / 70.0 / 15.4% | 16.0 / 68.0 / 16.0% | +0.00588 +/- 0.00122 | 4.8 | 5 of 5 |
| to the 15-minute close (1-14 min) | 94,080 | 15.4 / 68.4 / 16.2% | 18.5 / 63.0 / 18.5% | +0.01357 +/- 0.00301 | 4.5 | 5 of 5 |

All three pass by the rule's letter (more than 2 SE, more than 0.0005 nats, at least 4 of 5 coins). The walk
overstates meaningful moves, most at the close horizon: there, by fifths, its sideways figures said 46 / 52 / 60
/ 74 / 93% where 52 / 57 / 67 / 80 / 94% held. The challenger's bins sit within about 1-2 points of what happened (one exception:
its least-sideways fifth at 3 minutes said 75.2% where 72.6% held).

**How far, not which way (descriptive, written after the verdict, `crypto_scenarios.py decompose`).** The gain
splits into how far the price moves (a meaningful move or sideways) and which way it goes, given that it moved:

| horizon | how far | which way | which way past 2 SE? |
|---|---|---|---|
| 3 min | +0.00551 +/- 0.00112 | +0.00014 +/- 0.00030 | no |
| 5 min | +0.00532 +/- 0.00128 | +0.00056 +/- 0.00046 | no |
| to the close | +0.01126 +/- 0.00318 | +0.00231 +/- 0.00086 | yes (z 2.7) |

At 3 and 5 minutes the validated skill is volatility: knowing when a meaningful move is more or less likely. It is
not knowing which way. The page therefore splits the moved probability evenly between up and down at those
horizons and says why. Only to the close does it show the model's own up/down split, labelled "validated: how
far and which way". That direction effect is small: the windows' short-horizon mean reversion, the same effect
the direction model found.

**Paths are not drawn; the page shows probabilities only.** A scenario's own median path is a bridge from now
into its band. With no validated drift at 3 or 5 minutes, it is a straight line the eye reads as a forecast. To
the close it would add nothing the probability does not already say, and it would invite timing a turn. The
owner allowed either form; probabilities alone are the honest one.

## B. Direction uncertain (the rule's verdict: no threshold, so every direction bar reads "Direction uncertain")

The direction model passed its own held-out test at 1, 3 and 5 minutes (moves.json). The rule then asks a
second question: from which lean size |p - 0.5| does EVERY lean beat a coin flip by 2 SE, measured on
development's forward-chained predictions? Development bins, hit rate of the favoured side +/- SE (by day):

| |p - 0.5| | 1 min | 3 min | 5 min |
|---|---|---|---|
| 0-0.005 | 49.7 +/- 0.4 | 50.0 +/- 0.5 | 50.3 +/- 0.3 |
| 0.005-0.01 | 50.9 +/- 0.4 | 51.1 +/- 0.6 | 51.1 +/- 0.5 |
| 0.01-0.02 | 51.3 +/- 0.3 | 51.2 +/- 0.3 | 51.5 +/- 0.5 |
| 0.02-0.03 | 51.2 +/- 0.4 | 52.6 +/- 0.6 | 53.3 +/- 0.5 |
| 0.03-0.05 | 53.8 +/- 0.7 | 54.2 +/- 0.8 | 54.2 +/- 0.7 |
| 0.05-0.1 | 54.6 +/- 1.7 | 54.8 +/- 1.5 | 56.0 +/- 1.5 |
| 0.1+ | 57.6 +/- 7.9 (66 rows) | 48.6 +/- 2.9 (247) | 54.6 +/- 5.0 (696) |

In every column the top bin, a few hundred rows at most, fails its 2 SE. So by the rule's "every bin" clause
there is no threshold, and the page shows "Direction uncertain" at 1, 3 and 5 minutes, with the model's own
reading in small print. The middle bins (0.02-0.1) do beat a coin flip: the clause was stricter than the
effect. A rule that drops tiny bins, or pools the tail, is a change for fresh minutes (after 2026-11-28), never
a re-read of these.

## C. 30 seconds

Untested: no sub-minute history is held, which is moves.json's own note. The page shows "Direction uncertain
(untested)" there.

## What the page shows (UI v3)

The **Next movement outlook** card has one bar per horizon: 30 s, 1, 3 and 5 minutes, then "At settlement:
above target".
- Only horizons that fit inside the time left appear.
- With no validated lean, a bar is grey and reads "Direction uncertain", with the reason.
- The settlement bar is Vigil's random walk, labelled "random walk (null)", with Kalshi's price beside it.

The **Up, sideways or down** card shows the scenarios above, each labelled "validated: how far only",
"validated: how far and which way" or "random walk".

The final-reversal and target-crossing figures keep their own card. Each is labelled validated (the challenger)
or random walk, unvalidated. A "meaningful" new low (2 one-minute SDs past the old low) sits beside the
any-size figure, because a cent-past-the-low new low is not a reversal.
