# Football conservation audit (Opus agent, 2026-11-26; backlog Phase 1.2)

**Outcome (orchestrator, same day):** defects A, B and C below are fixed in the commit that adds this report; the draft guards became `tests/guards_conservation.py` (section CS of the suite) with A8, A9 and E2 now passing, plus A12 (the fixed allocator deals draw for draw as the shipped one wherever the shipped one was right), E0 (one quarterback untouched) and E3 (two and three quarterbacks on the engine's own boxes). The one-quarterback deal was checked identical to the shipped code on 8 engine boxes x 4,000 worlds. The DraftKings return-TD question in points allowed is open: it needs DraftKings' own rules page, not a third-party summary.

## Summary

The board's production path (`nfl_dfs_engine`) keeps every budget it deals: no touchdown is lost or created in any world on the engine's own simulated boxes. I found three defects, none of which affects production output today:

- **A (latent):** in the shared touchdown allocator, a player with catches but zero weight loses touchdowns, and the shortfall is reported as zero.
- **B (latent):** the allocator's fixed 12 rounds drops touchdowns once 13 or more players have room.
- **C (low):** on a team with two QBs, one QB's own line can be impossible, though team totals still add up.

DraftKings scores sit exactly on the 0.02 lattice, and on it the lineup buckets do not depend on summation order.

I could not write `report.md`: the harness refuses report `.md` files from a subagent. The full report is below. Everything else is in `research/sprint_2026_11_26/conservation/`.

## Defects (minimal repros)

**A.** `nfl_dfs_csim.py:186-193`: when every player with weight is full but others still have room, the loop breaks with touchdowns still owed. `_capped_short` (`:200-205`) then reports 0, so `drawn = allocated + surplus` fails.
- `_multinomial_capped(rng, N=[3], shares=[[1],[0]], cap=[[1],[5]])` returns 1 of 3: 2 touchdowns lost, 0 reported.
- Latent: both callers give a floor weight to every man with a catch or carry (csim `+1e-9`, engine `+1e-3`).

**B.** Each overflowing round fills at least one player, so 12 rounds always suffice for 12 men; with more men than rounds the rest is dropped.
- 13 players with 1 catch each, weights `1e-3**i`, 13 touchdowns: 12 are allocated, and 1,899 touchdowns were lost over 2,000 worlds.
- Latent: it needs 13 or more touchdowns in one team-game.
- Unlike csim, the engine hands the QB the box total (`nfl_dfs_engine.py:400`) and records no budget or surplus. A loss would show only as QB TDs ≠ receivers' TDs, with nothing explaining it.

**Proposed fix for A and B** (tested in scratch only, by monkeypatch):
- Loop `while owed.any()`; this ends in at most k+1 rounds.
- When the weighted players are full but room remains, deal the rest in proportion to room.
- With the fix, A8 and A9 pass, and output is bit-identical to today's on the engine deal of real boxes and on csim's 20,000-world adversarial offence.

**C.** `_split_int` (`nfl_dfs_csim.py:131-150`, used at `nfl_dfs_engine.py:397-404`) splits each stat on its own. Team totals stay exact, but one QB can get more interceptions than incompletions, or passing yards with no completion.
- Shares 0.18/0.82 on a box of 30 attempts, 25 completions, 3 INT: QB1 gets 5 attempts, 5 completions, 1 INT.
- Low: Sleeper's 2026 week 1 and 2 feeds have no team with two projected QBs.

**Rule question, not conservation; confirm with DraftKings.** `off_points` (`nfl_dfs_engine.py:447-451`) leaves every opponent return score out of points allowed, including kick and punt return TDs. A third-party summary (https://rotogrinders.com/pages/nfl-site-scoring-comparison-357433, undated) lists those as counting on DraftKings. If so, engine defenses read slightly rich.

## Property table

CAG = `tests/combo_audit_guards.py`; Draft = the new guard file.

| Property | Where computed | Conserved? | Already guarded? | Evidence / draft check |
|---|---|---|---|---|
| Pass yds = Σ rec yds | engine `:389-391`, `_split_w` `:309-327` | yes | CAG:18898 (one synthetic box) | Draft B2/B3/C5 on engine boxes: 0 bad worlds |
| Pass TDs = Σ rec TDs | engine `:392-393`, capped allocator | yes on real inputs; allocator has latent A, B | CAG:18898; csim CAG:15575, 15629 | Draft A1-A11, B2, B3 |
| Completions = Σ catches; targets between completions and attempts | engine `:383-388`, `_multinomial` | yes | CAG:18898 | Draft A10, B2 |
| QB line = box | engine `:397-404` | team yes; per-QB defect C | CAG:18898 (one QB) | Draft E1, E2 |
| Carries = rush_att + kneels; rush yds = rush_yds − kneels | engine `:406-421` | yes (documented: kneels kept out of the box's rush_att, `nfl_deep_sim.py:770-773`) | CAG:18898 | Draft B2 |
| Rush TDs = Σ, TD ≤ carries | engine `:413-414` | yes | CAG:18898 | Draft B2/B3 |
| TD budget ≤ capacity | deep sim `:889-895`, `:907-913` | yes, upstream by construction | missing | Draft B1 |
| Plays = att + sacks + rushes + kneels | deep sim `:773`, `:830` | yes | missing | Draft B1 |
| INT / fumbles: offense = other DST | engine `:401`, `:423-429`, `:464` | yes | offense side only | Draft C4 (both sides) |
| Sacks to DST | engine `:464` | yes | CAG:18910 (one world) | Draft C2 |
| Scrambles | not modelled | n/a | — | — |
| Two-point conversions, offensive return TDs | box `two_made`; no player gets them | no: documented exception (`research/reports/nfl_dfs_engine.md`, Limits) | box points identity CAG:18855 | Draft B4: dealt, or still documented |
| Return/defensive TDs, safeties, blocks to DST | engine `:465` | yes | CAG:18855, 18910 | Draft C2 |
| Kicker: FG tiers + XP | deep sim `:741-742`, engine `:469-473` | yes | CAG:18855, 18910 | Draft B1/C3 |
| Engine DK scores on 0.02 lattice, bonuses | engine `:433-444`; pool `nfl_dfs_sim.py:1018-1022` | yes | only csim's (CAG:15651/15654) | Draft C1/C5/D6 |
| Classic 9-man sum order | `dfs_tourney.py:993` + `_buckets` `:813` | yes on lattice (float32 error 5.6e-5 vs 0.005 margin) | CAG:22203, 22229 | Draft D1/D3/D5 |
| Showdown CPT 1.5x sum order | `dfs_tourney.py:212`, `:2640` | yes (1.5 × 0.02k is a whole hundredth) | CAG:23246 | Draft D2 |
| Legacy sim | `nfl_dfs_sim.py:429+` | n/a by design ("poisson, independent per player", `:365`) | — | — |

**Off-lattice scores flip buckets.** Six 0.005-grid scores summing to 77.095 land in bucket 7709 one way round and 7710 the other; Draft D4 pins this example. That is why the guards must keep lattice data.

## Existing guard suite

I ran it in a scratch clone at `6c0194e`:
- **First run:** crashed at CAG:17817 because my clone was shallow and lacked commit `0e4c20a`.
- **Second run:** killed by the coordinator's `pkill`, with 2,701 pass and 0 fail by then.
- **Third run:** 3,733 pass, 1 fail, exit 1. Every lattice and conservation guard passed.
- **The one failure** is "an expired index serves last-good INSTANTLY" (CAG:8631). It is a timing check on the Kalshi index, unrelated to football, and it passed in the first two runs. It is most likely flaky under load from the other suite runs.

## The draft

`guards_conservation_draft.py` follows the repo's guard style (`run(ck)`, exit 1 on any failure):
- **Run:** 30 checks, about 4.5 s on one core; pyflakes clean.
- **Result:** 27 pass, 3 fail, exit 1. The failures are exactly A8 and A9 (marked `[DEFECT]`) and E2 (`[DEFECT, low]`).
- **Mutation tests** (`probe_mutate.py`):
  - The old `np.minimum` allocator turns 9 checks red.
  - Splitting yards by rounding, bonuses at `>` instead of `>=`, and kneels at 0 yards each turn B2/B3/C5 or C1/C5 red.
  - With the proposed fix applied, only E2 still fails.
- **To adopt:** copy it to `tests/guards_conservation.py`. It finds the repo from `tests/`, or from `VIGIL_ROOT` when run elsewhere.

Files are in `research/sprint_2026_11_26/conservation/`:
- guards_conservation_draft.py
- draft_run.log
- probe_mutate.py
- full_suite3.log
- probe_alloc.py, probe_split2.py, probe_sum.py, probe_engine.py, probe_engine_deal.py, probe_fum.py

The repo was not modified.
