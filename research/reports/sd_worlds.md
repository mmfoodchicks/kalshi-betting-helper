# Tonight's showdown board on games it never saw (DAL @ GB, 2026-10-18)

*Harness `research/sd_worlds.py` (sha256 `462081fb…`, hashed before the run);
data `research/data/sd_worlds.json`. Descriptive only: nothing here changes a board.*

## Question

The owner asked about the Sunday night showdown: what has been done for a
single showdown, and will it work for tonight? The classic test
(`research/reports/cl_worlds.md`) found the classic board's own first-place and
EV figures read 3-4x high in-sample. This checks the showdown board the same
way, on tonight's real slate and through the board's own code.

## Setup

- **Contest:** dg 154959, the $1M Sunday Night Showdown (78,431 entries,
  $15). 780,492 legal lineups; 26,282 we may enter.
- **Run:** the real `build_nfl_showdown` with the engine model and seed 7.
  Built to its scoring pass at 180,000 games, the inputs live as of 21:31 UTC
  (3:31 MT).
- **Blocks:** the games are cut into three 60,000-game blocks (the board's own
  count).
  - Each list's top 20 is picked on block A, on block B and on A+B.
  - Every pick is then valued on block C.
- **Field:** the field is the whole legal universe, weighted, so no figure
  here carries field-sampling noise.

## Results

"Shown" is the pick's figure where it was picked; "fresh" is the same 20
lineups on block C.

| list | A shown | A fresh | B shown | B fresh | A+B shown | A+B fresh | A/B overlap |
|---|---|---|---|---|---|---|---|
| first place (%) | 0.0652 | 0.0683 | 0.0635 | 0.0674 | 0.0636 | 0.0689 | 15 / 20 |
| top 1% (%) | 4.53 | 4.58 | 4.56 | 4.57 | 4.54 | 4.58 | 18 / 20 |
| top 0.1% (%) | 1.157 | 1.191 | 1.175 | 1.195 | 1.163 | 1.195 | 16 / 20 |
| money (%) | 36.6 | 36.6 | 36.5 | 36.6 | 36.5 | 36.6 | 19 / 20 |
| EV ($) | 273.0 | 283.5 | 268.2 | 273.1 | 266.6 | 282.9 | 14 / 20 |

The 20-entry portfolio, picked on A the board's way, covers the top 1% in
55.7% of A's games and 55.2% of C's.

## Reading

- **No selection inflation at 60,000 games.** Every list's figures hold on
  fresh games (shown/fresh 0.92-1.00). Block C reads slightly high for every
  pick, so it is a luckier block, not a deflation.
- **Why it differs from classic.** The classic inflation came largely from
  scoring against a 300,000-lineup sample of an 832,342-entry field, whose
  extreme tail is thin. Showdown's field is exhaustive.
- **The picks are steady.** Two independent 60,000-game draws share 14-19 of
  each list's 20.
- **Doubling the games bought nothing visible** (one replicate; a hint, not a
  verdict). The board's 60,000 is enough for this contest.
- **What this cannot see.** Everything here is inside the model: the engine's
  games and the field model's weights. Whether the model is right is what
  tonight's real result and the standings export test.
- **A speedup for later (not shipped before lock).** Scoring only the 26,282
  lineups we may enter against the full weighted field takes 230-240 s per
  60,000 games on 4 cores, against ~15 minutes for the board's own pass, which
  scores all 780,492. Every list and the portfolio read only enterable
  lineups. The `opt_worlds` column (best of ALL lineups) would need its own
  pass.
