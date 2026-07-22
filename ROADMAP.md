# Roadmap

## Football (when the NFL season starts)

**Goal:** replicate the entire baseball stack for football, 1:1 — same simulators,
same props, same combo maker, same value finder, same recorder. No conceptual
difference; just a new sport's data layer and outcome model plugged into the
existing, sport-agnostic plumbing.

**Status: core delivered.** `nfl_data.py` (Sleeper matchup-adjusted team/player
layer) + `nfl_game_sim.py` (drive-level correlated engine: game script, short
fields, OT; player lines dealt from the simulated team game) + `kalshi_nfl.py`
(KXNFLGAME moneyline edges, spread/total series auto-light-up) feed
`/api/nfl/slate` and the Week tab: win probs vs Kalshi, total/spread ladders,
correlated props, same-game parlays via the shared MLB bitmask machinery, and
NFL predictions logged/graded for the site-wide calibrator. Still open below:
deeper alternate-line ladders on Kalshi props once they list them, and folding
the slate legs into the cross-sport combo maker.

### What carries over unchanged (already generalized)
- `parlay.py` — payout-governed combo selection with the confidence floor.
- Same-game correlation engine — bitmask joint-probability over simulated games.
- Tier framework (`tiers.py`) — gating, caps, owner/god mode.
- Recorder + grading pipeline (`store.py` prop_log + `mlb_recorder.py` pattern) —
  logs model % vs Kalshi price vs recent form, grades vs real results, reports
  Brier / calibration / edge ROI in aggregate.
- Closed-form vs simulation split: independent legs use exact math, correlated
  (same-game) legs use the simulator. The full alternate-line ladders.

### What is net-new for football
- `football.py` — slate + an expected-points / win-probability model
  (analogous to baseball's expected-runs model).
- `football_sim.py` — a drive/play-level simulator producing correlated player
  stat lines (the hard part: a QB's passing yards correlate with his WR1's
  receiving yards, with the team total, and with game script). Get this right so
  same-game parlays stay honest, the way the MLB lineup sim does.
- Props: passing / rushing / receiving yards, receptions, TDs, completions,
  interceptions, sacks, longest play, etc. — closed-form where independent.
- Data sources: an NFL play-by-play / stats feed (ESPN, nflverse-style data, or
  whatever's reachable, mirroring how baseball uses the MLB Stats API + Savant)
  plus the Kalshi football series tickers.
- A Football tab mirroring the Baseball tab (slate, value finder, combos, sim).

### Principle (unchanged)
Honest edge. Compare Kalshi prices to a sharper reference, surface the gap, and
validate everything in aggregate (Brier, CLV, calibration, edge ROI) — a single
game is noise. Do not ship it until there's live data and markets to test it on;
untested guesswork helps no one.
