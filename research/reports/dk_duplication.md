# Duplication: the real fields against the board's field model (2026-11-26)

The owner's priority 2 asks for the exact metric, sample sizes and uncertainty, and keeps every affected
output experimental. The review added: "do not scale duplication by 387". Script:
`research/dk_duplication.py`, which re-runs from the committed summaries and the data branch. Guards:
`tests/guards_dk_dup.py`, section DD.

## 1. The metric, exactly

`distinct_pair_collision` (dk_standings.summarize) is computed as

    C = sum over distinct lineups of n(n-1) / (N(N-1))

- **N** is the entries that carry a lineup.
- **n** is how many of them hold that lineup.
- **A lineup** is its set of nine players (classic, slots ignored), or its captain plus the set of its five
  flex (showdown).

C is the chance that two DIFFERENT entries, drawn at random, hold the same lineup. If the entries were
independent draws from a distribution p over lineups, C would be an unbiased estimate of sum p^2.

**It is an aggregate, never a per-lineup multiplier.** n(n-1) counts a lineup copied 234 times
about 27,000 times more than one copied twice. C is therefore set by the handful of most-copied builds and says nothing
about the copies of any other lineup. Scaling every lineup's expected copies by the real-to-model ratio would
put the chalk's duplication onto contrarian lineups that have none. It is not done here or anywhere.

## 2. The real fields (8 contests, the owner's standings exports)

| contest | kind | entries | distinct lineups | entries in a duplicated lineup | most copies | 150-max entries | C | one pair in | sampling SE (bound) |
|---|---|---|---|---|---|---|---|---|---|
| 196653900 wk 6 Millionaire | classic | 158,904 | 150,173 | 8.42% | 150 | 17.4% | 3.87e-6 | 258,335 | <= 3.0e-7 (7.8%) |
| 197645153 wk 10 Millionaire | classic | 147,058 | 136,658 | 10.74% | 144 | 16.3% | 4.79e-6 | 208,642 | <= 3.6e-7 (7.5%) |
| 197882014 wk 11 Millionaire | classic | 147,058 | 133,222 | 13.67% | 234 | 16.1% | 1.00e-5 | 99,586 | <= 6.6e-7 (6.6%) |
| 196786588 DAL @ GB | showdown | 78,431 | 12,627 | 93.49% | 314 | 27.7% | 5.05e-4 | 1,980 | <= 9.5e-6 (1.9%) |
| 196786690 WAS @ SF | showdown | 87,465 | 14,097 | 93.12% | 747 | 25.2% | 6.32e-4 | 1,583 | <= 1.5e-5 (2.4%) |
| 197912905 IND @ HOU | showdown | 65,359 | 11,603 | 91.72% | 502 | 25.7% | 8.04e-4 | 1,244 | <= 1.9e-5 (2.3%) |
| 198005475 CIN @ WAS | showdown | 70,588 | 12,351 | 91.77% | 760 | 26.8% | 9.06e-4 | 1,104 | <= 2.3e-5 (2.5%) |
| 198150093 GB @ LAR | showdown | 47,058 | 10,932 | 88.44% | 296 | 32.2% | 5.70e-4 | 1,754 | <= 1.7e-5 (2.9%) |

**Uncertainty, two kinds.**

- **Sampling, within one contest.** These are bounds on the SE of C as an estimate of sum p^2 under
  independent entries. In the U-statistic's variance (`se_bound`), sum p^3 is bounded by the largest lineup's
  share times sum p^2, because the summaries kept no per-lineup counts. The bound is 2-8% of C. Real entries
  are not independent: 150-entry players spread their lineups on purpose, and the public copies the same
  optimizer. So this is a floor, not the whole uncertainty. From this commit the summaries keep the copy-count
  histogram (`copies_histogram`), so the same SE becomes a figure (`se_plugin`), not a bound.
- **Between contests (the honest spread):**
  - **classic:** geometric mean 5.7e-6, log-scale SE 0.29 (95%: 3.2e-6 to 1.0e-5) on 3 contests. Range
    3.9e-6 to 1.0e-5, a 2.6x spread. Week 1's 831,028-entry Millionaire read 1.30e-6 (`cl_field.md`), lower
    again.
  - **showdown:** geometric mean 6.7e-4, log-scale SE 0.11 (95%: 5.4e-4 to 8.3e-4) on 5 contests. Range 5.1e-4
    to 9.1e-4.

  The spread between contests is many times any one contest's sampling floor. A field model is judged against
  that spread.

## 3. The model

**Classic.** The board's field is a 300,000-row sample, and its collision has been stamped as
`field_model.collision` (the same estimator, `dfs_tourney._collision`) since the sampler study. Three of the
real contests have their board on the data branch:

| contest | board | model C | real C | real / model |
|---|---|---|---|---|
| wk 6 | tourney-154941 | 1.00e-8 | 3.87e-6 | 387 |
| wk 10 | tourney-156416 | 1.42e-8 | 4.79e-6 | 339 |
| wk 11 | tourney-156561 | 1.94e-8 | 1.00e-5 | 516 |

- The model's own figure rests on about 450-875 colliding pairs in its sample, so its sampling SE is about
  3-5%. **The 340-520x gap is the model, not noise in either estimate.** This settles the question the sprint
  left open: the gap is a field-model deficiency, not rare-event sampling.
- **A separate issue remains:** the board's per-lineup copy counts. A sample of 300,000 cannot resolve a
  lineup whose share is 1e-6 (0.3 expected copies in the sample). That is the link `money_gate` keeps shut
  (`MONEY_DUP_EXACT = False`).
- **Why the gap is so large in classic and not in showdown.** The classic sampler draws players by a softmax
  on projection and price. It has no way to put mass on a handful of specific complete lineups. The public
  does exactly that, because many entrants run the same optimizer on the same projections. In the real fields
  86-92% of entries are unique, and the excess sits in the few percent that are duplicated.
- The hand-set `CL_FIELD_COLLISION` (1.2e-7) is a reported constant, not a fitted one. It sits 30-80x under
  the real fields too.

**Showdown.**

- The field is the whole enumerated universe with softmax weights, so its collision is sum p^2 exactly.
- The one served field rebuilt exactly (`sd_dup.md`, DAL @ GB) read 2.07e-4 against 5.05e-4 real: **2.4x too
  flat**, at the shipped knob `FIELD_TOP_SHARE` 0.2%.
- The knob that matched Sunday (0.468%) read Monday's real figure within 11%, but it was descriptive and never
  shipped.
- No other served showdown field carries its figure. **From this commit every showdown board stamps it**
  (`field_duplication`, exact), so each new contest gets a real-against-model row with no rebuild.

## 4. What stays experimental, and what would change that

**Unchanged:** expected copies, the tie-split EV and first place on both board kinds. `money_gate` already
marks them non-authoritative on every board (`duplicates_exact: false`), and nothing here moves that.

**Registered, not built:** a field model may be called calibrated on duplication for a format only when all
three hold:

1. At least 5 NEW contests of that format (built after this commit), each with the board's stamped figure.
2. Every one has the model within a factor of 2 of the real C (|log ratio| < log 2).
3. The real 20 most-copied builds (`most_copied`, kept from this commit; players only, never an entrant) are
   among the field model's most probable lineups at a rate the rule states before the data.

Until then no constant is moved on these numbers. The showdown knob (0.468%) and any classic lineup-level
mechanism (an "optimizer cluster" in the field: some share of entrants submitting the projection-optimal and
near-optimal builds) are changes to test on new contests, not to set by hand.

## 5. What is new in the data from this commit

- `dk_standings.summarize` adds two fields to `duplicates` on every new export:
  - `most_copied`: the 20 most-copied builds, with copies;
  - `copies_histogram`: copies to the number of lineups with that many.

  The 8 committed summaries predate it. The owner's raw exports stay on the PC and never enter git, so
  re-summarising them there is the only way to back-fill.
- Showdown boards stamp `field_duplication`. Classic boards stamp it too, the same figure as
  `field_model.collision`, so both kinds answer under one key.
