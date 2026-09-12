# S6: Showdown multi-contest scoring

**Baseline** `9761084`. S1–S5 frozen. No owner rule changed, no objective changed,
discrete-v2 not reopened, the field model not calibrated, the football model
untouched, S7–S9 not started.

**The job.** One expensive Showdown simulation and lineup universe should serve
several real contests on the same DraftKings slate without any contest borrowing
another's identity, field size, thresholds or payout economics.

---

## A. The old single-contest architecture

`dfs_tourney.build_nfl_showdown(dg, contest_id=None, ...)` picks **one** contest
and welds the whole build to it:

```
contest = dk.contest_detail(contest_id)  or  the richest GPP with >= 100 entries
...
C    = contest.max_entries or contest.entered or 10000
grid = payout_grid(C, contest.payouts, contest.entry_fee, contest.places_paid)
res  = run(W, X, f, grid, ...)                     # the expensive scoring pass
...
return {..., "contest": {...}, "portfolio": ports, "experimental": sd_money_gate(...)}
```

The simulation is not wrong. The problem is that `C` and `grid` — two small
contest-shaped values — are consumed *inside* the one pass that costs 300–550
seconds, so a second contest on the same slate means rebuilding the slate: the
football simulation, the 777,056-lineup enumeration, the field weights, all of it.
And the returned artifact has exactly one `contest` block, so it cannot even
*represent* a second contest.

## B. The common-versus-contest dependency audit

Read off the source, not assumed. **The notable finding is in B3.**

### B1. Slate / common — independent of any contest

| computation | why it is contest-independent |
|---|---|
| `dk.slate_for` → csv, players, teams | a property of the draft group |
| `nfl_dfs.showdown_pool(csv)` → `ents` | prices and positions only |
| `nfl_dfs_sim.player_pool(week, n, …)` | **the expensive one.** Football worlds; no contest input |
| `_pool_match` → `proj/ceiling/floor/arr` | per player |
| `_apply_depth` → `ents`, field-only extras | owner depth gate, slate-level |
| `enumerate_showdown(ents, CAP, entry_ok, cpt_ok)` → `idx, W, allowed` | cap and owner rules only |
| `X` (players × worlds) | the simulated scores |
| `field_weights(ents, idx)` → `f, beta` | softmax over projected points across `idx`; **no contest argument exists** (S5 already proved this by measurement) |
| `field_ownership(ents, idx, f)` → `own_c, own_f` | derived from `f` |
| `chalk_i = argmax(f)` | derived from `f` |
| `opt` (hindsight-optimal tally) | `argmax` over lineup scores per world — no grid |
| `chunk_for(len(idx), chunk)` | memory arithmetic |
| `pool_sig_rich`, `status_sig`, `sim_stamp` | slate identity |

### B2. Contest-dependent

| computation | the contest input it needs |
|---|---|
| `C` | field size (`max_entries` / `entered`) |
| `payout_grid(C, payouts, fee, places_paid)` | field size **and** the whole ladder |
| every `res` column except `opt` | the grid: `win`, `win_sole`, `top1`, `top01`, `cash`, `ev`, `ev_notie` |
| `copies = C * f` | field size |
| `roi = (ev − fee) / fee` | fee |
| `by_win` / `by_ev` / `by_top1` | rank by contest-dependent columns |
| the portfolio (shortlist, greedy, `p_any`) | the grid, and the entry cap |
| `sd_money_gate(n_legal, C)` | field size |
| the `contest` block | identity |

### B3. The engine was already built for this; only the builder was not

The boundary is sharper than it looks, and the machinery to exploit it already
exists. `run_vs_field` takes **a list of grids**, and its table builders say so
outright:

- `_grid_table(grids)` — *"Every contest's above-only curves side by side,
  (n_grid, 5 × contests), so a world costs one gather however many contests share
  the slate."*
- `_tie_table(grids)` — *"float32 to keep a six-contest slate near 30 MB."*
- `portfolio_vs_field(..., grids, ...)` — *"Returns, **per contest**, (chosen
  candidate positions best first, P(at least one entry makes it) …)"*

`run()` and `portfolio()` are single-grid *wrappers*. The Classic path already
passes several contests through `probe_rows`. So the expensive per-world work —
bucketing 777,056 lineup scores, building the field histogram, deriving the
strict/level masses — is shared across contests by construction, and a second
contest costs **one extra gather per world**, not a second pass.

This changes what S6 is. It is not "make the simulation reusable"; it is
**plumbing an existing capability through the Showdown builder and the artifact**,
plus the identity discipline that makes it safe.

One consequence for the portfolio, which is *not* free: `portfolio_vs_field`
takes a single `cand_idx` shared by all grids, but S6.10 requires each contest to
shortlist on **its own** Top-1% metric. Two contests with different field sizes
rank candidates differently, so their shortlists differ and a shared call would
silently give one contest the other's candidate set. Per-contest shortlists mean
one field pass per distinct shortlist. Measured in section H rather than assumed
away.

## C. Captured inputs, and what is now pinned

`research/s6_capture.py` closes the DraftKings half of the reproducibility gap
S5 recorded. Sleeper and DraftKings hashes are kept in **separate** provenance
blocks, because one source being pinned says nothing about the other — conflating
them is how the earlier overclaim happened.

Per draft group: the whole `dk.slate_for` payload, and the full `dk.contests`
lobby listing plus `dk.contest_detail` for a selected subset. Each file carries
sha256, byte size, record count, retrieval timestamp, the endpoint it came from,
and a normalisation version. A `_scan` refuses to write any snapshot containing a
credential-shaped key.

**Why a subset.** Draft group `153086` lists **881 contests**. A detail call each
is slow and rude to DK, so `select()` takes the richest twelve plus one contest
from every entry-limit bucket plus both field-size extremes — deterministic given
the lobby listing, so a recapture picks the same set. Taking only the richest
would have sampled only the 150-entry-max end, and section J is the reason that
would have been a bad mistake.

## D–N

*In progress. Sections D (new architecture), E (identity contract), F (synthetic
contamination tests), G (real same-slate results), H (runtime/memory), I (primary
compatibility), J (portfolio semantics), K (money gate), L (engine decision),
M (provenance), N (limitations) follow the implementation.*

## J (advance finding). The entry-limit trap is the majority case, not an edge

Recorded here early because it changes what S6 must do rather than merely
reporting on it.

`dk.contests` already returns `max_entries_per_user` on **every lobby row**, so
the information production needs has been available all along and unused. On
draft group `153086`:

| contest id | fee | field | max per user |
|---|---|---|---|
| `195526287` $1.5M Monday Night Showdown | $20 | 88,235 | 150 |
| `195526250` $700K Field General | $444 | 1,751 | 52 |
| `195526253` $300K Play-Action | $3 | 118,906 | 20 |
| `195526252` $150K Wildcat | $333 | 500 | **5** |
| `195526232` $402K Luxury Box | $3,180 | 134 | **4** |
| `195526230` $100K Huddle | $5 | 23,781 | **1** |
| `195526265` $100K Spy | $100 | 1,111 | **1** |
| `195519380` $100K Heavy Hitter | $26,200 | 4 | **1** |

And across all 881 contests on that one draft group, the distribution of
`max_entries_per_user` is:

| limit | 1 | 7 | 2 | 3 | 10 | 20 | 5 | 150 |
|---|---|---|---|---|---|---|---|---|
| contests | **751** | 33 | 30 | 20 | 15 | 9 | 7 | 5 |

**751 of 881 are single entry.** The primary GPP's 150-entry allowance is the
rarest shape on the slate. So a multi-contest build that reused the primary's
20-entry portfolio shape would attach "62% chance of at least one top-1% finish
with 20 entries" to contests where **one** lineup may be submitted — a number that
is arithmetically correct and operationally meaningless, which is the exact class
of error this audit has been finding. The portfolio size is therefore
`min(configured, contest max_entries_per_user)`, and a contest whose limit cannot
be read gets lineup metrics with the portfolio **withheld** rather than a
fabricated cap.
