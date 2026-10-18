# Weather and the DFS projections: tested, nothing shipped; shown on the tab

Run 2026-10-18 at the owner's request ("if you can scrape the weather
information from baseball we already had that ... go for it"). Two rules, each
in its harness's docstring before any number was computed:
- stage 1: `research/nfl_weather_validation.py`, written 13:30 UTC;
- stage 2: `research/nfl_weather_stage2.py`, written 13:41 UTC after stage 1
  failed, to be read after 2026 week 18.

Results are in `research/data/nfl_weather_validation.json`. The
play-by-play files (about 19 MB a season) are not committed; they come from
`https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_<season>.csv.gz`.

## Stage 1: wind, rain and cold against Sleeper's team lines and the field-goal curve

**Data.** nflverse's play-by-play gives each game's roof, its temperature and
wind, and the gamebook's weather text at kickoff. Readable outdoor games: 187,
188, 178 and 179 in 2022-2025, and 56 in 2026. Domes and closed roofs carry
no weather and sit outside the fit and the test.

**Features.**
- `wind_x` = mph over 10.
- precipitation: the text names rain, snow, showers, drizzle, sleet or
  flurries, and not "chance" or "possible".
- `cold_x` = degrees under 40, per 10.

**A. Team lines.**
- Outcome: `r = log(actual / Sleeper's projected team line)` for pass
  attempts, pass yards, rush attempts and rush yards.
- Fit: least squares on 2022-2024.
- Test: 2025, units with some weather.
- Comparison: the intercept, Sleeper's general bias, is kept out, so the test
  is the weather's alone.

| outcome | wind_x | precip | cold_x | 2025 d (n 132) | 2026 d (n 32) | ships |
|---|---|---|---|---|---|---|
| pass attempts | -0.0020 | -0.054 | -0.026 | -0.0017 +/- 0.0018 | +0.0038 +/- 0.0033 | no |
| pass yards | -0.0066 | -0.194 | -0.035 | -0.0014 +/- 0.0061 | -0.0092 +/- 0.0145 | no |
| rush attempts | -0.0003 | +0.028 | +0.001 | +0.0004 +/- 0.0005 | +0.0003 +/- 0.0018 | no |
| rush yards | -0.0042 | +0.030 | +0.005 | +0.0023 +/- 0.0016 | +0.0014 +/- 0.0027 | no |

**B. Field goals.** 3,237 attempts in 2022-2024. A logistic curve in
distance, with wind, wind x distance, rain and cold added. The fitted terms
point the expected way:
- wind -0.05 logit per mph over 10;
- rain -0.12;
- cold -0.23 per 10 F under 40.

The weather model was still worse than the distance curve alone on 2025's
218 weathered attempts: +0.0040 +/- 0.0054 log loss. On 2026's 69 it was
+0.0020 +/- 0.0028. **Nothing ships.**

## Why it failed, looked at afterwards (not a test)

Wind does move passing in every season. Team passing yards in wind over 15
mph ran further under Sleeper's projection than calm games did, in log ratio:

| | over 15 mph | calm |
|---|---|---|
| 2022 | -0.118 (n 26) | -0.052 |
| 2023 | -0.064 (n 12) | +0.015 |
| 2024 | -0.140 (n 18) | -0.003 |
| 2025 | -0.231 (n 28) | -0.059 |

Rain and cold did not hold a sign:
- rain without wind, passing yards against dry, calm, mild games: -0.03,
  -0.27, -0.22, then +0.05 in 2025;
- cold: +0.02, -0.06, +0.02, -0.02.

The three-term model spread the wind's effect across terms that flipped. The
2025 test then paid for the rain term. A wind-only model would likely have
passed on 2025, but that is said after looking. So it is judged only on
games not yet played.

## Stage 2: wind alone, on 2026 weeks 6-18

`research/nfl_weather_stage2.py` was written before any week-6 team line had
been computed. The player-week table ends at week 5. The one week-6 game in
the nflverse file (the Thursday game) entered stage 1 only through the 2026
field-goal aggregate.

- **Fit:** `r = a + b wind_x`, on 2022-2025, for pass attempts and pass
  yards.
- **Read:** once, after week 18.
- **Ships only if both hold:**
  - the gamebook wind beats Sleeper by two standard errors, on 30+ windy
    team-games;
  - the same test with the wind from the forecast frozen before kickoff
    comes out below zero. Production would read the forecast, which is
    noisier than the gamebook.

## What shipped: the forecast on the DFS tab, as information

- **The fetch.** `nfl_weather.py` holds a stadium table keyed by ESPN's venue
  names, with each stadium's coordinates and its roof (open-air, dome,
  retractable). The international venues are included. Forecasts come from
  the baseball board's own `weather.get_weather`: NOAA's hourly forecast, and
  Open-Meteo where NOAA has none.
- **The schedule.** The recorder's prop-log pass (`nfl_props_log`, every half
  hour, on the owner worker) fetches the forecast for every game inside 72
  hours. It freezes the forecast five minutes before kickoff, so the log
  keeps each game's last pre-game forecast. Stage 2 needs exactly that.
- **The panel.** `/api/nfl/weather` reads the log and never fetches. The DFS
  tab's weather panel lists each game:
  - its kickoff in Mountain time;
  - the stadium's roof, or the wind, temperature and chance of rain;
  - a flag for the conditions the test looked for: wind over 10 mph, a 50%+
    chance of rain or snow, or under 40 F.

  It says that no projection reads the forecast.
- **Cost, measured on week 6.** One pass of 12 games took 13.5 s of network
  time, almost all of it NOAA, on the background thread.
