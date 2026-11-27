# Classic opponent field, second pass (2026-11-26/27)

Modules `research/cl_field2.py` (the measurements) and `research/cl_field2_fields.py`
(the candidate fields and the prospective harness). Aggregates
`research/data/cl_field2/`. Protocol `research/reports/cl_field2_protocol.md`
(registered, hashed). Capture `dfs_field_capture.py`. Research only: no board, payout or
EV figure changes, and `dfs_tourney.py` is only called.

**Everything measured here is DEVELOPMENT data on n = 2 slates** (the week-10 and week-11
$2.5M Millionaires, 146,985 and 147,015 entries). `dk_mech` already read week 11 as its
validation contest, so neither week can validate anything again. Every figure below is a
hypothesis until the protocol's contests are read.

**Summary.**

1. The 15-29 point score deficit is a per-player ownership quantity, exactly. Of it, a
   steady **7.1 and 7.4 points are on our own projection** (the public builds a better
   field by OUR numbers: 4.7-4.8 from better players at the same price, 2.6 from salary,
   nothing net from the FLEX mix), and the rest (21.8 and 7.8) is the public's players
   beating our projection, mostly one running back at 65% and 71% ownership whom the
   served field held at 17% and 29%.
2. The served field's ownership is flat because its steering target is flat. A
   sigma-0.25 exact optimizer on Sleeper's frozen line moved by Kalshi's frozen prop means
   puts that back at 66% and 73% without being told, and production's sampler steered onto
   that share (F1c) reads, in sample: score gap 28.9 -> 6.0 and 15.2 -> 2.8, sum of
   squared ownership 1.51 and 1.63 (real 1.53, 1.65), top-40 MAE 5.05 and 5.91 (served
   6.37, 7.25), collision 0.40x and 0.066x of real (served 0.0023x, 0.0015x).
3. **No free projection source names the public's builds.** Sleeper, ESPN, their
   midpoint, the Kalshi-moved line, DraftKings' FPPG and its price list each found 0 or 1
   of the real 20 most-copied builds. The owner's criteria 1 and 2 still fail, and F1c is
   registered expecting them to.
4. An optimizer without a stack rule plays a naked quarterback 72-87% of the time (the
   public: 13-16%); a shared passing-game shock only brings it to 49-69%. The cluster
   (F2) added nothing and is recorded as failed.

## 0. Earlier work, not repeated

`dk_mech.md` section 0 lists the failed experiments (cl_field, cl_sampler, cl_mech's
per-player rankings, cl_own's per-position pulls, slot v1, field_refit, the showdown
studies) and its own two mixtures. Nothing here re-runs them. What this pass adds is
`dk_mech`'s recommendations 1-3: a public line captured before lock, the low-noise
optimizer share as a steering target, and a stack mechanism for the cluster. Failed in
this pass, and kept: the optimizer share at sigma 0.10 and 0.15 (far too concentrated:
sum of squared ownership 2.0-3.8, top-40 MAE 7-15; `targets_first_run.log`,
`targets_w*.json`); the passing shock as a stack rule; the cluster F2; FPPG and the price
list as projections; the consensus midpoint as "the public's projection".

## 1. The score deficit (`diagnose_w10.json`, `diagnose_w11.json`)

Definitions. o_i, s_i: a player's any-slot ownership in the real field and in the served
field's replica. A lineup's score is a sum over its players, so
**D = mean real points (real field) - mean (served field) = sum_i (o_i - s_i) x fpts_i,
exactly** (checked: 28.89 and 15.18 both ways). Stacking and correlation cannot enter the
mean; they move the spread, and the spread is not the problem: the gap is the same shift
at every quantile (week 10: P10 25.3, P50 29.7, P90 31.1, P99 31.1; week 11: 13.0, 15.5,
16.9, 14.6), score SD 31.7 against 29.4 and 23.7 against 22.6.

| | week 10 | week 11 |
|---|---|---|
| D, real DraftKings points | 28.89 | 15.18 |
| D_proj = sum (o - s) x our projection | 7.08 | 7.40 |
| D_resid = sum (o - s) x (fpts - projection) | 21.80 | 7.78 |
| D_resid by chance (residuals shuffled within position, 20,000 times): SD; share at least this large | 5.1; 0.0001 | 4.3; 0.039 |
| players the board did not project | 0.01 | 0.01 |

D_proj split by position (n the mean count in a lineup, m the mean salary of a pick, p
its mean value): allocation = sum (n_real - n_served) x p_served; salary = sum n_real x b
x (m_real - m_served), b the within-position slope of value on salary; selection = the
rest.

| on our projection | week 10 | week 11 |
|---|---|---|
| allocation (the FLEX mix) | -0.19 | +0.01 |
| salary | +2.58 | +2.56 |
| selection (better players at the same price) | +4.69 | +4.83 |
| of which running backs | +3.69 | +3.76 |

- **Salary (2.6 points, both weeks)** is receivers and defenses: the public's receiver
  costs $216 and $189 more, its defense $427 and $504 more ($3,063 against $2,636; $3,223
  against $2,719). The served field leaves $1,100+ on 20.5% and 22.7% of lineups; the
  public on 0.9% and 0.7%.
- **Allocation nets to nothing.** The public plays 2.46 and 2.56 backs, 3.31 and 3.29
  receivers and 1.23 and 1.15 tight ends; the sampler 2.39 / 3.61 / 1.00 and 2.47 / 3.53
  / 1.00. Fewer receivers costs about what the extra back and tight end earn.
- **Selection (4.7-4.8 points) is the chalk**, and it is running backs.
- **In real points** the same split reads 25.8 and 11.6 of selection, RB selection 24.0
  and 11.2. The real top-10 owned players carry 36.6 and 33.4 points of gross
  contribution; **one back carries 18.2 of week 10's 28.9 and 11.4 of week 11's 15.2**
  ($4,700 and $6,000; real 64.8% and 70.7%, served 17.1% and 28.8%; he scored 38.1 and
  27.3 on a 14.5 and 19.1 projection).

**Which mechanism carries most of it.** Chalk concentration on running backs: about two
thirds of the projected gap and nearly all of the realised one. Then salary. Positional
allocation and stacking carry none of the mean. "Projection quality" needs care: D_resid
is unlikely to be chance on week 10 (0.0001) and marginal on week 11 (0.039), but it is
dominated by one player in both weeks, the same player, so n is closer to 1 than to 2.
No source projected that back's points better (section 3); what the public had was
conviction. On week 10 our board held him at 14.5 where Sleeper's own frozen line read
16.7 and ESPN's 16.1: the board's usage blend (0.3 x trailing carry share) marked a new
starter down.

## 2. Ownership concentration (`targets_w*.json`, `choose.json`, `fit*.json`)

The board steers its field to 0.9 x `opt_pct` + 0.1 x casual, and `opt_pct` (each
player's share of the best lineup over the simulated Sundays) is flat because every
simulated Sunday has a different hero: sum of squared ownership 0.84-0.87, its most-owned
player 29%.

Tested, as `dk_mech` proposed: the **noisy-optimizer share** -- 10,000 exact optimal
lineups (`dfs_tourney.optimal_lineups`, DraftKings' rules only), each on a source x (1 +
sigma e) per player, and with tau > 0 also x (1 + tau h) per team on its QB, WR and TE.
It is a per-player target built from whole lineups, not a ranking of projection and price
(the family `cl_mech` falsified). Real: sum of squared ownership 1.53 and 1.65.

| target (week 10 / week 11) | top-40 MAE | sum own^2 | most owned | mean real points |
|---|---|---|---|---|
| board `field_pct` (served) | 6.39 / 7.23 | 0.86 / 0.88 | 30 / 29% | 127.4 / 122.8 |
| board `opt_pct` | 6.31 / 6.89 | 0.84 / 0.87 | 29 / 30% | 127.2 / 122.9 |
| board `casual_pct` | 7.75 / 9.57 | 1.20 / 1.13 | 38 / 38% | 139.0 / 129.3 |
| board projection, sigma 0.15 | 9.17 / 11.39 | 2.66 / 2.72 | 73 / 73% | 156.4 / 140.6 |
| board projection, sigma 0.25 | 6.47 / 8.86 | 1.75 / 1.76 | 53 / 55% | 148.6 / 134.5 |
| board projection, sigma 0.40 | 5.55 / 7.70 | 1.29 / 1.28 | 38 / 41% | 141.9 / 130.0 |
| Sleeper frozen, sigma 0.25 | 5.70 / 9.65 | 1.84 / 1.77 | 70 / 54% | 157.9 / 134.9 |
| ESPN frozen, sigma 0.25 | 6.48 / 6.35 | 1.65 / 1.69 | 56 / 55% | 151.9 / 138.0 |
| midpoint, sigma 0.25 | 4.96 / 7.51 | 1.65 / 1.61 | 66 / 56% | 155.6 / 137.1 |
| **Sleeper + Kalshi props, sigma 0.25** | **5.14 / 6.17** | **1.70 / 1.87** | 66 / 73% | 155.7 / 138.0 |
| DraftKings price list, sigma 0.25 | 7.79 / 10.54 | 0.87 / 0.82 | 30 / 36% | 125.3 / 117.5 |
| DraftKings FPPG, sigma 0.25 | 10.56 / 11.42 | 2.01 / 1.68 | 75 / 63% | 164.6 / 141.5 |
| real field | 0 | 1.53 / 1.65 | 65 / 71% | 156.1 / 137.7 |

- Concentration is easy and is not accuracy: sigma 0.15 is twice too concentrated on
  every source, and the board's own projection at the right concentration (sigma 0.25) is
  no better than the served field (6.47 / 8.86).
- Sleeper alone and ESPN alone each win one week and lose the other. Only the
  Kalshi-moved line beats the served field on both (the midpoint wins week 10 and
  loses week 11, 7.51 against 7.23). Kalshi's props were run at one cell; the comparison across sources is not a
  tournament, and the cell was chosen on the weeks it is scored on.
- **Chosen by the rule written in `run_choose`** (lowest mean top-40 MAE over 109 cells of
  source x sigma x tau x a blend alpha with the stamped ownership): the Kalshi-moved line,
  sigma 0.25, tau 0, alpha 0.5 -> 4.56 / 5.60 (served 6.39 / 7.23). Alpha 0.75 ties it to
  0.003 pp; alpha 1.0 is 0.57 pp behind on MAE and is the only one at the public's
  concentration.

**Steered through production's sampler** (`steer_field`, the board's beta and kappa; the
contest's own entry count):

| | week 10: F0 served | F1 (alpha 0.5) | F1c (alpha 1) | real | week 11: F0 | F1 | F1c | real |
|---|---|---|---|---|---|---|---|---|
| mean real points | 127.2 | 139.4 | 150.1 | 156.1 | 122.5 | 129.0 | 135.0 | 137.7 |
| score gap D | 28.9 | 16.7 | 6.0 | | 15.2 | 8.7 | 2.8 | |
| sum own^2 | 0.87 | 1.10 | 1.51 | 1.53 | 0.88 | 1.13 | 1.63 | 1.65 |
| top-40 MAE | 6.37 | 4.76 | 5.05 | | 7.25 | 5.58 | 5.91 | |
| most-owned player | 30% | 40% | 58% | 65% | 29% | 49% | 67% | 71% |
| C / real | 0.0023 | 0.021 | 0.40 | 1 | 0.0015 | 0.0060 | 0.066 | 1 |
| 8-of-9 pair share / real | 0.015 | 0.075 | 0.69 | 1 | 0.008 | 0.035 | 0.45 | 1 |
| entries in unique lineups | 99.8% | 98.8% | 89.2% | 89.3% | 99.8% | 99.3% | 93.8% | 86.3% |
| entries in 2-10 / 11+ copy builds | 0.16 / 0 | 1.2 / 0 | 10.4 / 0.43 | 9.3 / 1.4 | 0.20 / 0 | 0.75 / 0 | 6.2 / 0.02 | 11.3 / 2.4 |
| top-30 joint ownership MAE | 1.89 | 1.38 | 1.31 | | 2.26 | 1.72 | 1.65 | |
| salary mean | $49,267 | $49,331 | $49,392 | $49,868 | $49,220 | $49,220 | $49,076 | $49,874 |
| real top 20 builds found | 0 | 0 | 0 | | 0 | 0 | 0 | |

The owner's five criteria on the development weeks, F1c: 1 and 2 FAIL (0 of 20, both
weeks); 3 FAIL (0.40x misses the factor 2 narrowly on week 10, 0.066x on week 11); 4
passes the unique share and the 2-10 mass on week 10, fails the 11+ mass both weeks; 5
passes except the salary mean on week 11 ($798 off against the served $655). **Not
calibrated.** What it does is the part `dk_mech` called the larger payout error: field
strength and per-player concentration, with the sampler's stack rates untouched. The
sampler's salary shortfall (`cl_slot.md`) is not addressed and got slightly worse on week
11: joining slot v1's spend mechanism is still its own test.

**The cluster (F2) failed.** With 5, 10 or 20% of entries replaced by exact lineups of
the same pool, C fell (the sigma-0.25 pool holds no repeated lineup), the naked-QB share
rose from 17% to 24%, and the pre-stated loss (`dk_mech` B.6's seven errors, summed over
both weeks) read 35.7 at 0 against 37.2, 38.4 and 40.5. Not registered.

## 3. Projection and ownership sources

What each offers before lock, and whether it may be read by a program. Robots files were
fetched on 2026-11-26; "terms not read" means exactly that, and such a source is not used
until the owner or the terms say it may be.

| Source | Before lock | Access and terms | Used here |
|---|---|---|---|
| Sleeper weekly projections | every skill player, components | public read API (`api.sleeper.app`, robots allows; already this app's projection) | yes: frozen by `nfl_props_log` |
| ESPN fantasy projections | QB/RB/WR/TE components; D/ST and K totals | unofficial read endpoint this app already reads (N1); no published API terms | yes: frozen by `nfl_props_log`; D/ST by the new capture |
| ESPN start / roster percentages | share of season-long leagues starting each player this week | same endpoint | captured from now on; no history exists, so UNTESTED |
| Kalshi player props | ladders -> implied means for yards, catches, touchdowns | the app's own market data | yes (frozen); one ladder read 301 rushing yards, so a mean is used only within 2x of Sleeper's (22 and 24 means refused) |
| DraftKings salary, FPPG, opponent rank, status | the draft screen itself | the pool endpoint this app already reads; DraftKings' robots disallows /lineup/, /contest/, /draft/ pages (not read) | salary and FPPG tested as projections: both worse than the served field. Captured from now on |
| DraftKings ownership | not published before lock | -- | no |
| FantasyPros consensus | consensus ranks and projections | robots disallows /api/, /ajax/, /json/, /xml/; "ai-train=no"; a paid API exists | NOT used |
| RotoGrinders / Stokastic / FantasyLabs / Establish The Run / 4for4 / DraftSharks | projections AND projected ownership: what the public's optimizers actually run on | subscription products; RotoGrinders' and Stokastic's robots disallow /api/ (and .csv); terms not read | NOT used. The owner's call: a licence that allows a program to read one is the single input most likely to pass criteria 1 and 2 |
| FanDuel research (numberFire) | projections | robots disallows /api and /research/api | NOT used |
| Yahoo, CBS, NFL.com fantasy | projections | terms not read; CBS disallows /data/, NFL.com's fantasy API has moved | NOT used |
| nflverse / ffverse | open data, published after games | open licences (already used, with credit) | not a pre-lock projection |

**Does a consensus predict the public's ownership better than ours?** In sample, on two
weeks: through the same optimizer the Sleeper-ESPN midpoint reads 4.96 / 7.51 on the
top-40 MAE and the Kalshi-moved line 5.14 / 6.17, against the served field's 6.39 / 7.23
and the board's own projection's 6.47 / 8.86 at the same sigma. So yes on week 10 and
no on week 11 for the midpoint, yes on both for the market-moved line.

**Does any of them name the public's builds?** No. The real 20 most-copied builds sit a
median 8.8 / 11.7 points under the board's optimum, 8.4 / 14.1 under Sleeper's, 11.3 /
9.4 under ESPN's, 6.5 / 8.7 under the midpoint's; of every pool's 200 most frequent
lineups, 0 or 1 is one of those 20, on every source, sigma and tau. The share of real
entries that are **one-swap optimal** (no same-position swap inside the salary left
raises the projection) is 0.2-3.0% on any source; among week 10's 21+-copy builds it is
51% on the midpoint against 4.7% on the board's or Sleeper's and 0% on ESPN's, but week
11's same class reads 3.4% (ESPN 5.9%). One week's hint, carried by a handful of builds.

**No source projects points better.** MAE against real points over players projected 3+:
5.37-5.55 on week 10 and 4.62-4.68 on week 11 across the board, Sleeper, ESPN, the
midpoint and the Kalshi-moved line; nothing separates them on 175 players a week.

## 4. Salary efficiency and optimizer behaviour (`diagnose_w*.json`, `behaviour`)

| real field | week 10 | week 11 | served field |
|---|---|---|---|
| exactly $50,000 | 47.5% | 48.5% | 37.2 / 35.6% |
| $100-300 left | 42.6% | 42.5% | 29.5 / 28.1% |
| $1,100+ left | 0.9% | 0.7% | 20.5 / 22.7% |
| at the cap, by copies of the lineup: 1 / 2-5 / 6-20 / 21+ | 46 / 58 / 65 / 81% | 47 / 57 / 61 / 69% | |
| naked quarterback, by the entrant's entries: 1 / 2-19 / 20-149 / 150 | 23.6 / 16.1 / 10.0 / 7.9% | 20.8 / 13.9 / 7.1 / 4.8% | 17.4% |
| two or more of the QB's catchers, same classes | 18.7 / 30.8 / 39.2 / 38.7% | 22.3 / 34.4 / 46.2 / 48.7% | 33.6% |
| mean projection one swap would add (board's projection) | 3.3 | 3.1 | 5.8 / 5.4 |
| median projection gap to the optimum (board's) | 17.7 | 19.3 | 24.8 / 26.8 |

- **The cap is universal, not an optimizer's signature**: 90% of every entrant class is
  within $300 of it, single-entry players included. What rises with copies is exact $0.
- **Stacking is a sophistication gradient.** Single-entry players go naked 21-24% of the
  time; 150-max players 5-8%, and they double-stack twice as often. The sampler's single
  17% / 34% is an average over entrant kinds it does not model.
- **The public is not running an optimizer on any line we hold**: under 3% of entries are
  one-swap optimal on any source. But its lineups are much nearer optimal than the
  sampler's (3.1-3.3 points to gain from one swap against 5.4-5.8).
- **An optimizer needs a stack rule.** The exact optimizer on any source plays a naked
  quarterback in 72-87% of lineups; a shared passing shock of 0.15 brings that to 49-69%.
  The public's 13-16% is a rule, not a by-product of correlation at this size. Not
  re-registered here: a constrained solver is a change to `dfs_tourney`, out of scope.

## 5. The protocol and the capture

- `research/reports/cl_field2_protocol.md`, sha256
  `de67363429072342fcda4d8945a631a469680be7f1f14d41c8fdd09fadcdaa2a`, registered with the
  frozen candidates in `research/data/cl_field2/protocol.json` by commit `3d9194f` (in
  this worktree; the author time is the registration). Validation: the first two Sunday
  main-slate Millionaires locking after it. Primary F1c, secondary F1, F0 as production.
  The owner's five criteria are `dk_mix_classic.OWNER`, the same object; three
  diagnostics (score gap closes 3+ points; sum of squared ownership within 1.33x; top-40
  MAE 0.5+ pp better) make the smaller claim.
- **Existing logs suffice for the registered candidates**: the served board export and
  the props log (Sleeper, ESPN, Kalshi, each game frozen five minutes before its kickoff)
  were already kept. `dfs_field_capture.py` adds what nothing kept and cannot be rebuilt:
  DraftKings' FPPG, opponent rank and status, ESPN's start and roster percentages and its
  defense projections, inside the same weekly store, frozen the same way, exported by the
  existing route. It is the next development round's input, read by no candidate.

## 6. For the owner

1. **The validation needs the standings exports** of the next two Sunday Millionaires
   (2026-11-29 and 12-06), and the capture needs this branch deployed before a lock to
   start keeping the new readings (the registered candidates do not need it).
2. **"Week 13+"**: by ESPN's and Sleeper's count 2026-11-29 is week 12. The protocol goes
   by lock time and takes it; say so if it should start a week later.
3. **A licensed projection-and-ownership feed** is the one input that could pass the
   individual-build criteria. No free source does.
4. **The board's usage blend marked a new starter down 2.2 points** under both public
   lines the week he was 65% owned. One case; worth a look before a test.
5. If the protocol's diagnostics pass, the change to weigh is the steering target of
   `steer_field` (and with it, slot v1's spend mechanism). Neither is touched here.
