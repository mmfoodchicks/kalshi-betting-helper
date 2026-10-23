# Prop math: value, distributions, and the Kalshi NBA reader

Three pure-stdlib modules the NFL and NBA matchup engines share. Nothing here is
fitted from data and nothing was tested on a held-out split, so there is no
pre-registered rule: the numbers below are definitions and live verifications,
and every default threshold is labelled a convention.

Files: `prop_value.py` (pricing and value), `prop_dist.py` (alternate-line
probabilities), `kalshi_nba_props.py` (the NBA market reader),
`tests/guards_prop_math.py` (218 hermetic checks, 0.8 s, standalone or `run(ck)`).
All times UTC. Live checks ran 2026-10-23 00:54-01:18 UTC.

## 1. Conventions (pinned by the guards)

- Probabilities and edges are fractions. Kalshi prices, fees and EV are **cents per
  contract** (the repo's native unit); ROI is a fraction of the all-in cost; stakes
  are dollars (`*_usd`). `line_value` ships a `units` entry so a cent is never read
  as a dollar.
- `P(X >= k)` is the event a Kalshi "k+" market pays on; a fractional k reads as
  `ceil(k)`. NegBin dispersion `alpha` is the variance excess, `Var = mu + alpha mu^2`
  (below `alpha = 1e-6` it is evaluated as Poisson).
- Zero inflation: `mu` is the **base** mean, the overall mean is `(1-pi) mu`
  (`ZeroInflated.from_overall_mean` goes the other way).
- Yards: point mass `p0` at 0 plus a gamma / lognormal positive part fitted by moments,
  rounded to whole yards with the `k - 0.5` correction; `yards_from_moments` rejects
  moments whose positive-part variance would be negative instead of clamping.
- Copula correlation is that of the **latent normals**, not the Pearson correlation
  of the counts (smaller in magnitude for discrete marginals).

## 2. Odds and no-vig

American <-> decimal <-> probability for both signs (invalid input raises
`ValueError`: 0, |a| < 100, nan, bool, strings). **No-vig default is multiplicative**
(`p / sum p`): it is what the repo already does, needs no parameter, and always
returns a proper pair. Its bias is that it takes the same proportional margin off
favourite and longshot; the **power** method (`p^k`, `sum = 1`, bisection) puts more
on the longshot (on -150/+130 multiplicative gives the favourite 69/119 = 0.5798 and
the longshot 0.4202; power, k = 1.053, gives 0.5840 and 0.4160; the guard pins that
ordering and that `p_a^k + p_b^k = 1`). Neither is "true"; there is no closing
line here to score them against, so the choice is a parameter. A **one-sided**
market returns the implied probability unchanged with `vig_known: False`.

A Kalshi binary is one book seen from two sides: the fair value is the **YES mid**
(`combo_engine.market_reference`'s reference), with the de-vigged asks reported
beside it. A book wider than 20c (`combo_engine._SPREAD_WIDE`) is reported
one-sided on the ask, not as a mid.

## 3. Kalshi fees

Live `/series/<ticker>` read 2026-10-23 00:54 UTC (`last_updated_ts`
2026-10-22T19:23:52Z for all five):

| series | fee_type | fee_multiplier |
|---|---|---|
| KXNBAPTS, KXNBAREB, KXNBAAST, KXNBA3PT, KXNBAPRA | quadratic | 1 |

The other 13 series the reader lists (PR, PA, RA, STL, BLK, STOCK, FTM, 2D, 3D and the
four PROT twins) are `quadratic` x 1 in a full `/series` listing pulled 00:47 UTC.
`/series/fee_changes` was empty at 00:57. `kalshi_nba_props._fee_for` re-reads the
series record (12 h cache) and ledgers `KNBA-fee-changed` if it ever disagrees with
the table.

**Formula** (Kalshi's general schedule, `round_up(0.07 x C x P x (1-P))`, peak 1.75 per
100 contracts at 50c, 0.07 per 100 at 1c / 99c): the PDF itself
(kalshi.com/docs/kalshi-fee-schedule.pdf) answers this host with a bot-checkpoint
HTTP 429, so the formula is cross-checked against `kalshi.taker_fee_cents` and a web
search summary of the schedule, **not read from the PDF**. **Rounding** is from
docs.kalshi.com/getting_started/fee_rounding (read 01:0x UTC): per fill
`trade_fee = ceil_6dp(model fee)`; the balance is floored to the member's grid
(cent for retail / FCM-cleared, 0.0001 for direct members); the shortfall is a
"rounding fee" added to a per-ORDER accumulator that rebates whole grid units on
later fills. For cent-grid fills that collapses to
`order fee = ceil_cent( sum ceil_6dp(fill fees) )`.

Pinned: the docs' own worked example (-$0.055 revenue, model fee $0.00363825 -> $0.005,
rounding fee $0.001361 carried) and accumulator table (three fills adding $0.004 ->
nets 1c, 1c, 0c, $0.002 carried) both reproduce exactly. A sweep of 25 @ 44c + 10 @ 45c
costs **61c** by the accumulator and 62c by independent per-fill round-ups.
Float traps: 4 @ 50c is exactly 7c (float math gives 7.000000000000001 and rounds to 8),
so the fee is computed in `Fraction`s; checked against `ceil(7 C c (100-c) / 10000)`
for every integer price x 8 sizes at multipliers 1 and 0.5.

`contracts=None` uses the smooth per-contract expectation (kalshi.py's size-free edge
math); `contracts=N` uses the exact fee of an N-contract fill (1 contract at 50c is a
whole 2c, not 1.75c). **Not verified:** no real fill was available, so the model is
checked against Kalshi's documentation, not against a statement.

## 4. Value, intervals, labels

`line_value(p, market, p_lo, p_hi, ...)` returns the payload contract's `value`:
`edge` (p minus the no-vig YES reference), `edge_exec` per side (p minus the all-in
break-even), `price_used` and `fee` (executable **ask**, never the mid), `ev_yes`,
`ev_no`, `roi_*`, `ev_interval` per side, `best_side`, `positive_at_lower_bound`, an
optional `stake` block. The NO interval runs the other way (its bad end is the HIGH
P(YES)). `line_labels` returns the contract's list; definitions only from inputs:

| label | defined as | default |
|---|---|---|
| high_probability | P(chosen side wins) >= `high_p`; says nothing about price | 0.70 |
| positive_ev | fee-adjusted EV > 0 at the executable ask | - |
| positive_ev_robust | EV > 0 at the unfavourable end of the probability interval | needs interval |
| strong_fit | passed in as a bool by the matchup model; only formatted | - |
| low_evidence | `n_eff < min_n_eff` or band wider than `max_width` or neither supplied | 20, 0.25 |
| thin_book (qualifier) | replaces `positive_ev` / `positive_ev_robust` when the YES book is not believable: one-sided, wider than 20c, or nothing traded or open (`book_is_believable`, `kalshi_nfl.believable`'s rule) | 20c |
| stale_quote (qualifier) | replaces them when the quote is older than `max_quote_age_s`, or has no timestamp (unknown age is not fresh) | 600 s |

The defaults are **conventions, not fitted**. With no side given, labels follow
the best-EV side (guard: p .90 against YES 95c / NO 6c labels NO, `positive_ev`).
The word "lock" appears in no label or reason over a 90-case grid. `line_value`
carries `believable`, `quote_ts`, `quote_age_s` and a `book` block (kind, spread,
volume, open interest) so the qualifiers are explainable. A qualifier appears only
IN PLACE OF an EV label the numbers earned: a negative-EV thin book gets none. Review
finding F3: on the real 2c / 68c zero-volume listing, a model saying .80 used to
earn `positive_ev` and `positive_ev_robust` (EV +10.5c "against a listing"); it now
earns `thin_book`. NOTE for the UI: `thin_book` and `stale_quote` are labels outside
the five the payload contract names; a renderer that whitelists names will drop them.

`refund_adjusted_sides` handles "settles to a fair price" (section 6) as an
expected-payoff adjustment. The fair price is **unpublished and unmeasured**: the
settled markets seen do not show it tracking the last traded price (Nesmith 10+
settled 0.71 against a last trade of 0.00, 15+ 0.39 against 0.84; Monk 10+ 0.51
against 0.46), so `fair_p` is a guess the caller must say it made and sweep. An earlier
draft of this report and of the docstring said the values "sit near the pre-game
price"; that was an impression, not a measurement, and is withdrawn.

## 5. Distributions and fitting

Closed forms (Poisson = regularised lower incomplete gamma; NegBin = incomplete beta
`I_{mu/(r+mu)}(k, r)`; gamma survival = regularised upper incomplete gamma), series /
Lentz continued fractions, `lgamma` prefactors. Guard evidence: Poisson vs summed
recurrence pmf, 4 means x 7 thresholds, max error < 1e-13; NegBin vs an independent
recurrence, 5 (mean, alpha) x 7 thresholds, < 1e-11; yards (gamma and lognormal) vs
Simpson integration of the density, < 1e-7. `enforce_monotone`: PAVA by default
(`[.50,.60,.30,.35]` -> `[.55,.55,.325,.325]`), running-min optional.

Fitting: `negbin_fit_mom` (unbiased reliability-weighted variance; underdispersed ->
alpha 0) and `negbin_fit_mle` (mean profiled out; the score collapses to
`sum_j T_j/(r+j) + W ln(r/(r+m))` for integer counts; bisection in log r). The MLE
equals the grid argmax of the log-likelihood on a 12-game sample (0.3268 vs 0.327);
on 4,000 seeded NegBin(6, 0.25) draws both recover alpha within 0.04. Degenerate
samples (n < min_n, constant, all zeros, negative, nan, non-integer for the MLE)
return `ok: False` with a reason and **no alpha**. `shrink_alpha` is a placeholder
shrinkage whose `k` must come from measured reliability.

`NormalCC` defaults to `lower=0` (review finding F1): sampled untruncated, its negative
mass has no index to live on, and `cdf_table` used to force `cum[-1] = 1.0` and hand
all of it to k = 20,000 (NormalCC(1, 3): 30.6% of 20,000 copula draws, mean 6,130 for
a mu of 1). `cdf_table` now raises `ValueError` (ledgered `PDIST-cdf-table`) when it
exhausts `kmax` with mass left, `NormalCC(lower=None)` with real negative mass refuses
at once, and `mean()` / `var()` are the truncated variable's moments.

Copula: pure-Python Gaussian copula over any `Dist` marginals (PSD-tolerant Cholesky,
so rho = 1 works), seeded `random.Random`, cdf-table sampling. Checks: independent
Poisson(4)+(6)+(3) sum matches Poisson(13) within 4.5 SE at 4 thresholds; rho = 1
identical marginals give `S = 2X` exactly; rho .6 fattens the tail by > 3 SE. SE is
the binomial `sqrt(p(1-p)/n)` of the simulation (a never-hit threshold gets a nonzero
SE). 20,000 x 3 draws is a fraction of a second here: still a precompute job.

## 6. Kalshi NBA markets (live, 2026-10-23)

Open markets per series at 00:54-00:56 UTC: **PTS 392, REB 427, AST 332, 3PT 467, PRA
162, STL 68, BLK 53, STOCK (steals+blocks) 27, 2D 66, 3D 20, PTSPROT 385, REBPROT 424,
ASTPROT 330, 3PTPROT 467**. **PR, PA, RA, FTM, STOCKS, LADDER*, ESCALATOR*: zero open**
(PR / PA / RA / FTM are in the map, flagged unobserved; the ladder / escalator / ONE3PT
products are not game over-lines and are not read).

**Price fields.** The list endpoint carries no integer-cent fields: `yes_ask_dollars`
("0.2400"), `yes_bid_dollars`, `no_*_dollars`, `last_price_dollars`, `volume_fp`,
`open_interest_fp`, `volume_24h_fp`, `yes_ask_size_fp`, `yes_bid_size_fp` (3,630 of
3,630 markets). `0` bid and `1.0000` ask are empty-book sentinels. No `no_*_size_fp`
(the NO ask's size is the YES bid's). The reader takes dollars first and falls back to
legacy cents. At 01:09 UTC of 3,617 markets 2,096 had a two-sided book, 1,429 ask-only,
92 bid-only, and 619 passed `believable` (the rest are first listings at 2c / 68c).

**Ticker grammar** (3,620 of 3,620 game markets parse):
`<SERIES>-<YYMONDD><AWAY><HOME>-<TEAM><NAME><JERSEY?>-<K>`, K = `floor_strike + 0.5`
(0 mismatches over 3,534 threshold markets). 2D / 3D carry no threshold. 44 markets have
no jersey in the code (`DETIJOESF`): the code is opaque, the name comes from the title.
**Date is the US Eastern game date.** **Order is AWAY then HOME**, checked against ESPN's
scoreboard for all 14 games listed: 14 / 14 agree, none reversed (the reader re-checks
on every `attach_games` and reports `order_ok`). Kalshi's codes differ from ESPN's for
six clubs (NYK, GSW, NOP, SAS, UTA, WAS -> NY, GS, NO, SA, UTAH, WSH). Kalshi's
`occurrence_datetime` = tip + 3 h (3 / 3 games checked: CLE@PHI 23:00Z -> 02:00Z,
DEN@OKC 01:30 -> 04:30, CHI@MIL 20:00 -> 23:00); `start_est_ts` carries that estimate.
**Live markets stay listed** (CLE@PHI was in progress): `pregame()` is True only for
ESPN "pre" with the tip ahead, None when ESPN was not consulted.

**Settlement** (from `rules_primary` / `rules_secondary` on every market; the series
object carries `contract_terms_url` only: BASKETBALLENTITYSTAT.pdf for the standard
series, and a different document, NBAENTITYSTATCOVERED.pdf, for all four *PROT
series, read off `/series/<t>` 2026-10-23 -- the protected contract's own terms are
worth reading before trading it):

- Standard: "If <player> records k+ <stat> in the <away> vs <home> ... game originally
  scheduled for <date>, then Yes." **Inactive, or active but never takes the court:
  settles to a fair price** (result `scalar`). Once he enters, stats decide: a player
  hurt after two minutes settles NO on every unreachable over. Postponed < 48 h stays
  open; cancelled / not started in 48 h: fair price. The market closes early when the
  event occurs. Double / triple double: 10+ in two / three of PTS, REB, AST, STL, BLK.
- **Protected (*PROT)**: if the player enters in the first half but does not enter
  after halftime (incl. OT), it resolves to the **fair price before the opening tip**
  unless he already reached the number; injury / "out" is not required. So a
  protected over cannot be lost to a first-half injury or a blowout benching; the
  YES ask carries the premium. It is a different contract from the standard one with
  the same title: records carry `protected` and ladders never merge them.
- **DNP frequency** (recounted from the file, review finding F7): the settled pull holds
  697 KXNBAPTS markets over 180 player-games in 18 event tickers (2026-10-05 to 10-22).
  18 markets (2.6%) settled scalar, in **4 event tickers on 3 dates** (26OCT05 and
  26OCT08, both ESPN season type 1 = preseason, and 26OCT21), for **5 player-games**
  (2.8% of 180): James, George and Tatum in preseason, Monk and Nesmith in the regular
  season (2 of 174 regular-season player-games, 1.1%). Six of the 18 markets carry
  expiration_value "Confirmed DNP", twelve are blank. Example values: Monk 0.51 / 0.22 /
  0.08 for 10+ / 15+ / 20+. The file was pulled by a sibling agent (`scratchpad/data/
  nba_probe/pts_settled_all.json`, 00:56 UTC); I read it, I did not re-fetch it. An
  earlier version of this report said "18 events ... two preseason"; that was wrong.
  The fair price is not published and **not assumed near the last price** (see section 4).

**Player matching** (live, 01:09 UTC, 14 games, 30 ESPN rosters of 516 players):
**137 / 137 distinct players matched (100%; 3,617 / 3,617 markets), every one by exact
normalised name on the club's roster, no misses.** 131 are string-identical to ESPN;
6 differ only by accents (Jokic, Schroder, Diabate, Doncic, Vucevic, Nurkic), which the
normaliser folds. **Jersey is not a reliable key**: 133 / 135 agree, the two that do
not (Kuminga 0 vs 24, Mathurin 9 vs 24) are why jersey is a fallback. The other match
methods (squashed key, jersey + similarity >= 0.75, surname + initial, league-wide
unique) are exercised only on synthetic cases in the guards; the live set never needed
them. Index build: 28 s from this sandbox (~30 market pages + 18 series lookups),
2.9 MB pickled; rosters 7.8 s for 30 GETs.

**Order book**: `GET /markets/{ticker}/orderbook` returns
`{"orderbook_fp": {"yes_dollars": [["0.4200","13.00"], ...], "no_dollars": ...}}`,
bids only, ascending, best bid last; a NO bid at y is a YES ask at 100 - y. Read live
01:10 UTC (Jalen Williams 6+ assists: best YES ask 36c x 1,413). `walk_book` fills each
level at its own price and charges the accumulator fee; a 30-contract buy of Kalshi's
published example book is 17 @ 44c + 13 @ 55c = 1,463c + 52c fee (53c by per-fill
round-up).

## 7. What the guards do and do not prove

218 checks, 0.8 s. Expected values are computed by a route that shares no code with
the function (recurrence pmfs, Simpson integrals, integer ceil on cents, `Fraction`s,
Kalshi's published example). Mutation test: 13 deliberate bugs were each caught (fee 6dp ceil removed, fee
dropped from the cost, NO interval ends swapped, ask not flipped to 100 - bid,
no-vig denominator removed, NegBin tail argument inverted, yards continuity
correction dropped, PAVA disabled, MoM alpha divided by m not m^2, threshold read as
floor - 0.5, a failed page returning the partial series, the ESPN order check
disabled, the player's team read from the wrong ticker group); a no-op control
replacement changed nothing, as it should.

The independent review (research/reviews/prop_math.md, 44 mutations, 40 caught) found
three surviving mutations (plus the reviewer's intentional control) and two blocking defects; every fix has a guard that fails on the code
it fixed, shown by re-applying the old behaviour and re-running: F1 `cdf_table` / untruncated
`NormalCC` (3 guards), F2 `ladders()` keyed by game as well as stat and player (a
SYNTHETIC second Brunson game built from the real rungs), F3 believability / quote age
on `line_value` and the two qualifiers (7 guards), F4 a complete-but-empty index
published (store redirected to a temp dir), F5 fractional k = `ceil(k)` in five
families, F6 the exact-fee stake adjustment ($0.518 buys nothing, $51.235 buys 98
not 99), F10 a crossed book, F12 two same-named players in one game, and the F7
docstring claim. F8-F12 are done too (F8: both contract-terms documents named; F9: the false "/ 71" dropped from a guard's text; F10: a crossed-book guard; F11: the forbidden-word check now runs over LABELS and QUALIFIERS once at import, where it can fire, instead of over a fixed tuple at runtime; F12: `by_line_team` and `market_for_line(team=)`). Nothing from the review is left open.

Not proven: the protected-contract price, the fair-price rule, the label thresholds,
fee agreement with an actual fill, and the four unlisted series' (PR, PA, RA, FTM)
ticker shape, which is assumed to match the others and was parsed from no live market.
