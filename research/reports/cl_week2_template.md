# Week 2, held out: slot-allocation v1 against the untouched current family

Written as a template with blank cells BEFORE the week-2 field existed
(committed 2026-09-20, before the 17:00 UTC lock of draft group 153428), so
no metric can be added afterwards because we liked the answer. The harness
`research/cl_week2.py` fills the cells this template carries and refuses to
render if the numbers it computed and the cells this file asks for are not
the same set. The frozen candidate, its parameters, its code and production's
sampler are unchanged; nothing is refitted; production is untouched.

**Verdict. {{verdict}}**

Read only as far as the co-primaries go. A positive result means the slot
allocation and the salary spending transferred to a new slate. It does not
validate chalk modelling, concentration, duplication or money EV, and the
week-1 in-sample run already showed why: the whole cross-entropy gain was
the FLEX group while the tight-end and defense slots and the chalk ordering
stayed where the current family had them.

## 1. The two co-primaries (the only pass/fail metrics)

Both arms on the same pre-lock pool at the real field's size, graded by the
frozen protocol's functions, on the frozen realisation seed 20260913.

| Co-primary | Current family | Slot-allocation v1 | Candidate minus current |
|---|---|---|---|
| Slot cross-entropy, total | {{cur_ce}} (KL {{cur_kl}}) | {{cand_ce}} (KL {{cand_kl}}) | {{d_ce}} |
| Salary Wasserstein-1 | ${{cur_w1}} | ${{cand_w1}} | ${{d_w1}} |

The real field's slot entropy, the floor cross-entropy cannot go below, is
{{real_entropy}}.

### Slot cross-entropy by roster-slot group

| Group | Current family | Slot-allocation v1 | Real entropy | Players with real mass |
|---|---|---|---|---|
{{slot_rows}}

### Salary used

| | Current family | Slot-allocation v1 | Real |
|---|---|---|---|
| Mean | {{cur_sal_mean}} | {{cand_sal_mean}} | {{real_sal_mean}} |
| p10 / p25 / median / p75 / p90 | {{cur_sal_q}} | {{cand_sal_q}} | {{real_sal_q}} |
| At the cap / >= $49,500 / <= $48,000 | {{cur_sal_shares}} | {{cand_sal_shares}} | {{real_sal_shares}} |

## 2. The interpretation rules, fixed before the answer existed

The verdict above is mechanical from the two co-primaries on the primary
seed:

- candidate lower cross-entropy AND lower salary Wasserstein-1: the slot and
  spend mechanisms transferred on this slate;
- cross-entropy lower, Wasserstein-1 worse: the allocation transferred, the
  spend penalty did not;
- cross-entropy worse, Wasserstein-1 lower: the spend behaviour transferred,
  the week-1 slot allocation did not;
- both worse: the candidate failed to transfer.

An exact tie counts as not lower. There is no combined score, no winner
chosen on an out-of-objective metric, no rescue by reseeding, and no week-2
refit of any kind. The secondary seeds and the timing arm below never decide
the verdict.

## 3. The input, selected before lock and pinned

| | |
|---|---|
| Primary input | {{primary_kind}} |
| Board built | {{built_utc}} ({{minutes_before_lock}} minutes before lock) |
| Fetched (committed to the data branch) | {{fetched_utc}} |
| Board sha256 | {{board_sha}} |
| Source commit or capture | {{board_commit}} |
| Pool after the served-pool reader | {{pool_n}} players |
| Frozen file sha256 | {{frozen_sha}} |
| Candidate module sha256 | {{cl_slot_sha}} |
| Production classic_sample sha256 | {{classic_sample_sha}} |

Selection rule, as pre-registered: {{selection_rule}}

The receipt `research/data/cl_week2/week2_input_receipt.json` carries every
candidate snapshot and why each one lost. The post-game command refuses to
run at all if any of the three hashes above differs from the tree it runs
in, so a later change to production's sampler cannot silently rescore the
candidate.

## 4. Support: what the co-primaries condition on

Pre-registered definition, unchanged after seeing week 2: the co-primaries
condition on FULLY REPRESENTABLE entries. A lineup containing any player
outside the served pool is omitted, and no projection is invented for a
missing player.

| | |
|---|---|
| Contest rows (excluding the header) | {{rows_total}} |
| Blank entries | {{rows_blank}} |
| Malformed lineups | {{rows_malformed}} |
| Non-blank active entries | {{rows_active}} |
| Fully representable entries (used by the co-primaries) | {{rows_representable}} |
| Excluded entries | {{rows_excluded}} ({{rows_excluded_pct}}% of active) |
| Week 1 representable share, for comparison | {{week1_representable_pct}}% |

Unsupported players behind the exclusions (entries, share of active):
{{unsupported_players}}

Roster-slot mass outside support: {{slot_mass_outside}}

If coverage is materially worse than week 1, every reading above is
correspondingly weaker, and this section is where that is visible.

## 5. Stochastic sensitivity (never a validation)

The primary result is the frozen seed alone. These four seeds re-run both
arms through the same frozen functions so a small difference on the
co-primaries is not read as structural when it is field-sampler noise.

| Seed | Current CE | Candidate CE | Delta CE | Current W1 | Candidate W1 | Delta W1 |
|---|---|---|---|---|---|---|
{{seed_rows}}

Candidate minus current across the four secondary seeds, mean / SD / range:
cross-entropy {{dce_stats}}; salary Wasserstein-1 {{dw1_stats}}.

The wrapper's own arms reproduce the frozen scorer's co-primaries exactly on
the primary seed: {{crosscheck}}.

## 6. Input-timing sensitivity (never decides)

The early pre-lock capture, kept as a second input arm rather than thrown
away. {{timing_pool_note}}.

| | Current family | Slot-allocation v1 |
|---|---|---|
| Slot cross-entropy | {{t_cur_ce}} | {{t_cand_ce}} |
| Salary Wasserstein-1 | ${{t_cur_w1}} | ${{t_cand_w1}} |

Pool differences against the primary: {{timing_support_diff}}.

Representable entries on this pool: {{timing_representable}}. When the
supports differ the two arms are not on identical universes, and that is
stated here rather than papered over by presenting the numbers as
like-for-like.

## 7. Out of objective (diagnostics only, never pass/fail)

Current family | Slot-allocation v1 (and Real where it applies).

| | Values |
|---|---|
| Top-20 MAE / bias (pp) | {{top20}} |
| Top-50 Spearman | {{top50_rho}} |
| Top-10 / 20 / 50 overlap with the real top set | {{overlap}} |
| Real mass captured by the sampler's top 10 / 20 / 50 | {{mass}} |
| FLEX is RB / WR / TE (%) | {{flex}} |
| Punts under $3,000: 0 / 1 / 2+ (%) | {{punts}} |
| Stacks 0 / 1 / 2 / 3+ (%) | {{stacks}} |
| Bring-back (%) | {{bring}} |
| Defense against own QB / own back (%) | {{dst}} |
| Unique-lineup share; most-copied | {{unique}} |
| Distinct-entry collision | {{collision}} |
| Plug-in HHI; effective lineups | {{hhi}} |
| Duplicate buckets 1 / 2 / 3-5 / 6-10 / 11-50 / 51+ (entry share) | {{dups}} |

## 8. Per-player ownership, the real top 50

| # | Player | Pos | Salary | Real % | Current family % | Slot-allocation v1 % |
|---|---|---|---|---|---|---|
{{top50_rows}}

Nothing in production changed. Nothing was refitted. The frozen file, the
candidate's producing functions and production's sampler were verified
identical to the freeze before a single lineup was drawn.
