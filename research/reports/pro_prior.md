# Last season as the prior for the NBA and NHL board ratings: not shipped

`research/pro_prior_validation.py` → `research/data/pro_prior_nba.json`,
`pro_prior_nhl.json`. Run 2026-10-17. Both leagues: production stays.

## What was tested

The boards rate every team from this season's points (goals) for and
against a game, regressed toward the LEAGUE AVERAGE by `regress_g` (NBA 12)
/ `_REGRESS_G` (NHL 10) games. On opening night every team is average. The
candidate pulls toward LAST SEASON'S final rating instead:

    prior  = league_now * (1 + rho * (last_season / league_last_season - 1))
    rating = (g * this_season + K * prior) / (g + K)

`rho` the share of last season's edge carried over, `K` the games the
prior is worth. `rho = 0, K = R` is production to the digit.

## The rule, written before any number was computed

Grid K ∈ {R, 2R, 3R, 5R}, ρ ∈ {0, 0.4, 0.6, 0.8} (16 variants, production
among them). Select on 2024-25 by mean log loss on the home side's win over
every regular-season game; ship on 2025-26 only if the chosen variant beats
production by more than two standard errors of the per-game difference.
Each league on its own. Every game priced the way the board would have
priced it that morning (a day's results enter only after the whole day is
priced); one simulation seed per game for every variant.

## Result

| | chosen on 2024-25 | 2024-25 | 2025-26 chosen | 2025-26 production | difference | verdict |
|---|---|---|---|---|---|---|
| NBA | K 12, ρ 0.8 | 0.60971 vs 0.61655 | 0.60209 | 0.60867 | −0.00657 ± 0.00334 (1.97 SE) | **not shipped** |
| NHL | K 50, ρ 0.8 | 0.66574 vs 0.67591 | 0.69456 | 0.69282 | +0.00175 ± 0.00395 | **not shipped** |

**NBA** missed by a hair: 1.97 standard errors against the 2 the rule asks.
Every K = 12 variant with a prior beat production in both seasons, and the
gain sits in the opening weeks (games where either side had played fewer
than 15: 0.59075 against 0.62140, n 233). Under the rule as written it is
not shipped; it is the first change to judge on 2026-27 games.

**NHL** did not hold up. The prior that won 2024-25 by 0.010 lost 2025-26.
2025-26 was a season production itself barely read (0.69282, against 0.69300
for the home rate alone; `hockey.py`'s header). The no-persistence variants
(ρ 0) with a larger K did better on 2025-26 (K 50: −0.00540 ± 0.00256), but
no rule chose them, so they stay a note.

Both chosen variants sit on the grid's edge (ρ 0.8). A wider grid would be
a second look after the test was read, so it was not run.

## What did ship: the regular season only

The same morning the NBA board was rating every team off the PRESEASON.
ESPN's bare `/standings` answers with the current season type, and from
the first exhibition until opening night that is the preseason. On
2026-10-17 it read "New York 1-4, 106 a game", and five exhibition games
carried 5/17 of each rating. Both boards now ask for `seasontype=2`. A club
that has not played is rated the league average, which is what the
validated model does on opening night (the harness's ρ = 0, K = R). Until
the opener every NBA game is the home-court edge alone, so read the price.
