# The showdown field is flatter than the real one; tonight's lists barely care

*2026-10-19, the morning of WAS @ SF. Harness `research/sd_dup.py`, data
`research/data/sd_dup.json`. Descriptive: nothing here changes a board.*

## Sunday night, served against real

The board served for DAL @ GB 27 minutes before lock rebuilds exactly from its
pool (`research/data/sd_dup_snf_board.json`: 780,492 lineups, beta 0.4731).
Against the $1M Sunday Night Showdown's 78,189 real lineups
(`research/data/dk_fields/dk_field_196786588.json`):

| | served field | real field |
|---|---|---|
| pair collision (two entries, same lineup) | 2.07e-4 | **5.05e-4** |
| distinct lineups (expected / counted) | 19,422 | 12,627 |
| entries in a duplicated lineup | 88.3% | 93.5% |
| most-copied build | 156 copies | 314 |
| salary, mean / median | $48,658 / $49,100 | $49,265 / $49,600 |
| team split 3-3 / 4-2 / 5-1 | 39.9 / 49.9 / 10.2% | 37.8 / 48.1 / 14.1% |

So the real field was about 2.4x more concentrated than the served one, and it
spent about $600 more. S7 found the same on three older showdowns
(`research/reports/s7_beta.md`: 3.7-11x too many effective lineups, too much
salary left, the captain mix off). This is one more game in the same
direction, not a new finding.

Ownership MAE over the board's 24 players:

| | captain | flex |
|---|---|---|
| served field share | 2.07 | 5.74 |
| best-lineup share (`opt_*_pct`) | **1.61** | **4.75** |

The best-lineup share wins here too, as it did on the classic boards
(`research/reports/cl_own.md`). But this is one game, and it was read
before this was written. Per S7's rules it changes nothing.

The field's biggest misses:
- **Punts were over-owned.** Sturdivant was 12.4% in the field against 2.1%
  real; Goodson 13.5% against 3.5%.
- **QB captains were over-owned.** Love was 20.6% against 13.5%; Prescott
  13.6% against 5.8%.
- **Pickens was under-owned.** 13.6% in the field against 36.9% real.

## Tonight's board under a field as hot as Sunday's

The one knob (`FIELD_TOP_SHARE`, 0.2% served) whose pair collision matches
Sunday's real field is **0.468%**. On tonight's slate that raises beta from
0.350 to 0.447.

The run used the real `build_nfl_showdown` on dg 154960 (60,000 games, seed 7,
309,849 legal lineups, the 22,866 we may enter). Those lineups were scored
twice on the same games: once against the served field, once against the hot
one.

| list | top-20 shared | top-5 shared | served picks' figure: served field → hot field |
|---|---|---|---|
| sole win | 16 | 5 | 0.03% → 0.05% |
| top 1% | 16 | 4 | 4.21% → 4.19% |
| top 0.1% | 17 | 5 | 0.89% → 0.99% |
| money (paid above the fee) | 18 | 4 | **39.4% → 35.8%** |
| EV | 17 | 4 | $231 → $354 (fee $20; dollar figures assume the sim is the truth) |

What this says:

- **The picks barely move.** Every list keeps 16-18 of its 20 lineups. The
  board's picks do not depend on the knob.
- **The money list's cash rate reads about 3.6 points high** if tonight's
  field is as concentrated as Sunday's. Its lineups are the chalky ones: the
  top five hold 34-138 expected copies against the served field and 51-302
  against the hot one.
- **The EV and sole-win figures rise under a hotter field.** Those lineups
  hold about one copy or fewer either way, and a hotter field crowds the
  chalk instead of them.

What it cannot say: what tonight's real field will be. The knob only moves
along the softmax family, and S7 showed that family has the wrong shape
(salary, captain mix, the duplication curve).

## The S7 prospective test

`research/s7_prospective.py` scores a frozen field family on showdowns that
were captured before lock. It has no prospective slate yet. Tonight's slate
was captured at 2026-10-19 13:07 UTC, 11.1 hours before lock
(`research/data/prospective/154960/20261019T130736Z`). A second capture is due
after the inactives; under S7's rules it would be the primary.

After the game, the $1.5M Monday Night Showdown's standings (contest
196786690) score it. The export itself never enters git: it is frozen
locally and only the scored artifact is committed.

## Timing

Scoring only the lineups we may enter against the full field took 145 seconds
per pass here, on four cores. The PC's board scores all 309,849 lineups, and
that took 457 seconds. This is the allowed-only speedup noted in CLAUDE.md;
it is not shipped, because tonight's builds already land before lock.
