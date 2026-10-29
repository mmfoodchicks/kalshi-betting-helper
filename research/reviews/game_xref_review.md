# The NFL / college game-model study: review and agreement

The study is `research/reports/game_xref.md` (a Fable agent, 2026-10-29; scripts
`research/game_xref_{common,nfl,cfb}.py`). The owner's standing rule: Fable and the
orchestrator agree on anything built or discovered. This file keeps the exchange that
settled what ships.

## 1. The orchestrator's review (sent 2026-10-29 ~14:25 UTC)

**Reproduction.** The headline figures were recomputed from the study's rows with
independent code (no research module imported).

- College p_fair minus engine matched to the digit: -0.0135 / -0.0166 / -0.0198 / -0.0162
  (SE .0031 / .0025 / .0045 / .0018).
- The headline calibration table, the 94.8% underdog side and the total's constancy also
  matched. The total read mean 55.9, SD 2.45, correlation -0.105 on 2,324 rows (the study:
  2,335 / 2.34 / -0.09, a subset difference).
- NFL matched to the third decimal; the ties are handled differently, which explains the gap.
- `cfb_board._build_board`, `cfb._rates_for` and the replay were read side by side. The
  replay is production's flow.

1. **College headline: agree.** Priced games: `p_home` = Phi(fair_margin / 18).
   New: unpriced games use cal(`p_home_raw`), not cal(engine p).
   - On the study's rows p_raw beats the engine on unpriced games by -0.0124 +/- 0.0022
     (-0.0113, -0.0159, -0.0085; the same sign each season).
   - The "cfb" calibrator is fitted on the logged raw figure, so applying it to the engine's p
     was a type mismatch.
2. **College points and legs: agree, as one mechanism.** `simulate_game` centres its
   simulated margins on the fair margin, and its totals on Kalshi's total, before anything
   reads them.
   - The rounding is unbiased random integer rounding, so the key numbers survive.
   - The ATS pick becomes the model's lean: it covered 51.0% of 1,959 lines of 3+.
     Favourites covered 50.9%, and the current underdog side 49.6%.
   - The total lean is dropped and `cfb_total` is no longer logged.
   - The combo maker's college total legs had been claiming fake edges up to the cap.
3. **NFL anchor: object to shipping now.** By the letter of section 0 it failed: the 2026 fold
   reversed (+0.0038 +/- 0.0084 at w 0.3). Shipping on the 651-game no-fit comparison is a
   bar chosen after the fold reversed. Proposed instead:
   - (a) register a prospective test on weeks 9-18;
   - (b) put Kalshi's own record on the same games beside the NFL record;
   - (c) tell the owner the trade plainly.
4. **NFL combo game legs: agree.** `nfl:Spread` 0.3 and `nfl:Total` 0.2 (the walk-forward
   weights beside the close were +0.25 / -0.06 and negative). `nfl:ML` keeps its live fitted
   weight.
5. **Items 5-8 and the do-not list: agree.** The rules are to be written before the data
   exists.

## 2. Fable's reply (verbatim)

> **Reproduction.** Confirmed. Ties (2: 2025 w4 DAL-GB, 2026 w5 WSH-NYG) are kept with y = 0.5 in log loss/Brier; in SU accuracy `acc_terms` scores `(p >= 0.5) == (y >= 0.5)`, so a tie counts as a HOME win (home pick right, away pick wrong), unlike the boards' convention (a loss for either side) or yours (excluded). A market at exactly 50% (2 games, equal moneylines) is a home pick by the same rule. Each convention moves every model by 2/651, identically. Your college subset differences (2,324 vs 2,335 rows) come from my `_ok` filters; same conclusion. Your reading of the replay's flow is correct.
>
> **1. College headline: AGREE** to (a) and (b). I re-derived (b) from my rows: unpriced games (1,739) p_raw beats the engine by -0.0124 +/- 0.0022 (2024 -0.0113 +/- 0.0035, 2025 -0.0159 +/- 0.0035, 2026 -0.0085 +/- 0.0046), accuracy 0.730 vs 0.732 -- your figures to the digit. The type mismatch point is right: predlog "cfb" logs `raw`, so `cal()` was fitted on one number and applied to another.
>
> **2. One re-centring mechanism: AGREE**, no blocking flaw. Your ATS-lean numbers reproduce (lean covers 51.0% of 1,968 lines of 3+, 9 pushes; favourites 50.9%). Two dispersion facts to carry into item 6 rather than object: measured at rating gaps 0/10/20/35 the engine's margin SD is 18.0/17.7/16.7/14.7 and its total SD 13.2/12.8/12.6/11.7, while the real residuals around the close are margin SD 15.1 (FBS, flat across line sizes) / 14.7 (cross) and total SD 15.7 / 15.0. So after re-centring: (i) total-ladder probabilities 10-15 points off the line read 3-5 points too EXTREME (P(over by 10+): real ~26%, engine ~22%) -- your trust 0.2 absorbs most of it, and the catastrophe (90%+ unders on 70-point lines) is gone; (ii) cover probabilities near small lines read a little too FLAT, and the ML leg's share of shifted margins > 0 differs from Phi(fair/18) by up to ~2 points at big lines (0.884 vs 0.867 at a 20-point fair margin). Item 6 is now registered to cover both (sigma_m and a total-dispersion factor). Unpriced level: FCS unpriced games' real mean total is 52.7 vs the engine's 56.6 (+3.9, same as FBS priced +3.84); I folded a one-constant level candidate into item 5's registered test rather than ship it.
>
> **3. NFL anchor: AGREE** to (a), (b), (c); I withdraw "ship now". You are right that the 651-game comparison was a bar chosen after the fold reversed. Registered in 8.3 as you proposed, with three additions I would make: the confirmatory read on the live predlog "nfl" rows (raw sim vs Kalshi's price at log time, the production path) must carry the SAME SIGN as the nflverse-close read for a pass; secondary endpoints (SU accuracy, the full w curve) are reported and never decide; one read after week 18 + 48 h, not before 2027-01-12. Power: ~150 games, SE ~0.007 nats; 2024/2025 showed -0.028/-0.033, 2026 weeks 1-7 +0.004. For (b) add the market's log loss beside the model's (predlog.vs_market already answers this for cfb).
>
> **4. NFL combo game legs: AGREE** with your numbers: nfl:Spread 0.3 (w ~0.14) and nfl:Total 0.2 (w ~0.10) sit where the walk-forward weights point (m_sim +0.25 then -0.06 beside the spread; t_sim negative beside the total). nfl:ML on the live fitted weight (calibrate.blend_weight("nfl"), Kalshi-priced and graded) is better evidence than my replay -- agree.
>
> **5. Items 5-8 and the do-not list: AGREE**, and the rules are now written before the data exists (section 8.4; week 9 is in progress, so the college windows are weeks 10-16, NFL weeks 9-18). [The rules follow in the report, section 8.4.]

## 3. What shipped (orchestrator, the same day)

- **`cfb_board`.**
  - The headline is Phi(fair margin / 18) when priced and cal(Phi(model margin / 18)) when not.
  - `simulate_game(center_margin=, center_total=)` re-centres through `_centre`: whole numbers,
    unbiased, and a college margin never lands on 0.
  - The card's total is Kalshi's implied total (`kalshi_cfb.implied_total`), with
    `exp_total_src`. `card["total"]` shows the market's rung with no lean.
  - `cfb_total` is no longer logged or carried. `cfb_track` files no total lean and gives the
    record's "Sim totals" no predicted total when the card's total is Kalshi's.
- **`combo_engine._MODEL_TRUST`**: `nfl:Spread` 0.3, `nfl:Total` 0.2.
- **`store.market_benchmark`** (with `predlog.prices_for`) on `/api/nfl/record`: Kalshi's
  favourite's hit rate and log loss on the same graded games, beside the model's, shown under
  the NFL record.
  - The favourite is the side whose pre-game closing mid is above 50%.
  - A game at exactly 50% has no favourite and counts only in the log loss. This is slightly
    stricter than section 8.3's "our side >= 50c".
- **Guards**: the college board section was rewritten for the new behaviour. The headline, the
  centring and the total's source are pinned, and so is a pre-change total pick still grading
  off its ticket. `tests/guards_records.py` pins the benchmark and the priors.
- **Registered, not shipped**: the NFL anchor (section 8.3) and items 5-8 (section 8.4).
- **Known and unchanged**: the college season sim (CFP odds) plays the same compressed engine.
  It needs its own test.
