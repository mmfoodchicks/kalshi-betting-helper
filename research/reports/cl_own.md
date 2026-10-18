# An ownership-driven classic field: FAILED its test; a lead for week 7

*2026-10-18. Harness `research/cl_own.py` (sha256 `9f7eb12a…`, hashed before
the fit); data `research/data/cl_own.json`. The inputs are
`research/data/cl_own_w6.json` (the week-6 board and real ownership) and
`research/data/cl_field.json` (week 1). The week-7 test is
`research/cl_own_w7.py`, written before week 7 was played.*

## Why

The week-6 Millionaire export (158,819 lineups, the owner's upload) showed how
far off the board's field model was:

- **Ownership:** the top-40 ownership MAE was 8.3 points. Isaiah Likely (TE,
  $4,800) was 35% owned in reality against 5% in the model; the Panthers (DST,
  $2,500) 32% against 6.5%; Bijan Robinson 24% against 39%.
- **Salary:** half the real field sat at exactly $50,000 (mean $49,879); the
  model's field averaged $49,110.
- **TE at FLEX:** 40% of real lineups had a tight end at FLEX, which the
  sampler never builds. Week 1 also showed this (21%, and 50% of lineups at
  the cap).

## The model tested, and the verdict

The sampler's structure was kept, with a tight end eligible at FLEX
(`research/cl_mech.py`'s variant). Each position got its own pull on
projection and on value (points per $1,000): ten numbers, fit on week 1 only
by Nelder-Mead (491 evaluations).

MAE is in points, against real any-slot ownership:

| | week 1 (fit) | week 6 (test) |
|---|---|---|
| all players, fitted | 1.81 | 3.35 |
| all players, served | 2.16 | 3.42 |
| top 40, fitted | 5.15 | **8.95** |
| top 40, served | 5.89 | **8.29** |

The rule needed a 25% gain on both metrics; the result was +2.2% and -8.0%.
**It does not ship.**

What the fit learned did not carry over between weeks. In week 1 the public
paid up at RB (value pull -0.66: Gibbs 43%); in week 6 it chased value
(Likely, Bernard, Worthy). So the fitted field put Bijan at 55% (real 24%).

The sampler also missed in ways no pull fixes:
- **Salary to the cap:** only 20-37% of its lineups reached $50,000, against
  50% in reality.
- **TE at FLEX overshoot:** it put a tight end at FLEX in 45-68% of lineups,
  against 21-40% in reality.

## The lead: the public drafts like an optimizer

Every classic board already carries each player's share of the simulated
Sundays' exact best lineups (`opt_pct`; it sums to 900% like real ownership).
It beats the served field's ownership on both weeks:

| | week 1, all | week 1, top 40 | week 6, all | week 6, top 40 |
|---|---|---|---|---|
| served field (`field_pct`) | 2.16 | 5.89 | 3.42 | 8.29 |
| optimal-lineup share (`opt_pct`) | 1.83 | 5.02 | 2.62 | 6.27 |

**Both weeks were read to find this, so it is a hypothesis.**
`research/cl_own_w7.py` tests it on week 7 (2026-10-25), with the rule written
before that slate was played:
- **If `opt_pct` beats `field_pct` on both metrics:** build a field drawn the
  way optimizer users draft (lineups optimal under noisy projections, mixed
  with casual draws), with its own rule written before week 8.
- **Otherwise:** the lead is dropped.

## What the owner needs to do

After week 7's games, download the Millionaire's standings and drop the zip in
`pc-standings`, or send it here. The served board for that slate is archived
by the snapshot workflow at lock.

## Week 2 confirmed the lead, and the field shipped (later the same evening)

The owner then sent **week 2's** Millionaire export (contest 195648007, the
$3M of 2026-09-20, 172,692 lineups, final). Week 2 had not been read to find
the lead. `research/cl_own_w2.py` (sha256 `5f940094…`) was hashed before week
2's ownership was computed; the file had been opened only for its contest id,
row count and time remaining.

| week 2 | all players | top 40 |
|---|---|---|
| served field (`field_pct`) | 2.38 | 6.62 |
| optimal-lineup share (`opt_pct`) | **2.05** | **5.57** |

So the board's field is now the **optimizer field** (`dfs_tourney.FIELD_MODEL
= "optimizer"`, ENGINE 10).

- **Sampler:** the same one. Its per-player pulls are steered (`steer_field`,
  ten passes of 40,000 lineups) until each player's ownership sits at
  0.9 x his best-lineup share + 0.1 x a fresh draw of the calibrated field.
- **Casual share (0.1):** a sweep over weeks 1, 2 and 6 scored 0, 0.1, 0.2,
  0.3, 0.5 and 1 (all-player plus top-40 MAE, summed). 0.1 was best and 1,
  the calibrated field alone, was worst. Weeks 1 and 6 were already read, so
  this choice is in-sample.
- **Candidates:** drawn with the same pulls as the field.

The production sampler drew the steered field on each week's served board
(`research/data/cl_own_steer.json`):

| | all / top 40 | at the $50k cap | top copy share |
|---|---|---|---|
| week 1: steered vs served | 1.86 / 5.14 vs 2.16 / 5.89 | 47.3% | 0.004% |
| week 2: steered vs served | 2.09 / 5.64 vs 2.38 / 6.62 | 43.7% | 0.001% |
| week 6: steered vs served | 2.66 / 6.27 vs 3.42 / 8.29 | 43.5% | 0.003% |

The real field put about 50% of its lineups at the cap. Its duplicates are
still about 100x more common than the steered field's (pair collision
1.3e-06 to 3.9e-06 real, against 1.7e-08 to 2.9e-08). The real field's tight
end at FLEX (21-40%) is not drawn either.

**Week 7 keeps it or reverts it** (`research/cl_own_w7b.py`, sha256
`de53ebe4…`, written before week 7 and before the field shipped). Every board
now stamps `casual_pct`, the calibrated field it replaced, beside `field_pct`.
If the steered share does not beat the casual share on week 7's top 40,
`FIELD_MODEL` goes back to `"softmax"`.
