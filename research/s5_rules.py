"""S5.0/S5.1 -- the owner strategy rule inventory, and a reason-aware shadow
classifier proved equivalent to production over the WHOLE legal universe.

Production answers one boolean per lineup ("may we enter it?") and stops at the
first failure, so it can never say WHY a lineup is forbidden or what else was
also wrong with it. S5 needs the reason set, because a rule that rejects 400,000
lineups every one of which another rule also rejects costs nothing to remove.

Three categories, and keeping them apart is the whole point of S5.0:

  DK LEGALITY      enforced inside dfs_tourney.enumerate_showdown. One captain
                   and five distinct flex, salary under the cap with the
                   captain at his captain price, at least two teams. Not
                   studied here and never ablated -- a lineup breaking these
                   cannot be submitted to DraftKings at all.

  ENTRY POOL       the depth-chart gate. nfl_dfs._apply_depth drops players
                   from OUR pool; dfs_tourney puts the ones with a real
                   projection back as `_field_only` so the public can roster
                   them and their boom worlds count against us honestly. The
                   code says it outright: "The depth gate is OUR rule, not the
                   public's." So it IS an owner heuristic and S5 studies it --
                   but it is a POOL rule, not a lineup rule, and it is the only
                   one that changes which players exist rather than which
                   combinations are allowed.

  OWNER STRATEGY   everything in nfl_dfs._sd_allowed plus the two captain gates
                   in the builder. These are the rules S5 is about.

Equivalence is asserted over every enumerated lineup on both studied boards,
not sampled: production_allowed(L) iff shadow_reasons(L) is empty. If that ever
fails, S5 stops -- an attribution built on a classifier that does not reproduce
production is attribution of something else.

Nothing here is imported by the app, the PC worker or the guard suite's
production paths.
"""
import json
import os
import sys

# numpy is imported INSIDE the functions that need it. The rule inventory, the
# category split and the S4-rerun condition are pure data and pure logic, and
# the guard suite has to be able to read them on the server's no-numpy tree --
# the same contract every other module here follows.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATA = os.path.join(ROOT, "research", "data")
DGS = (153086, 153085)
BOARD_SEED = 20260912     # named so the provenance stamp cannot drift from it
WORLDS = 400              # the classifier is world-independent; this is the
                          # cheapest pool that still produces a real board


def _prov(**extra):
    from research import provenance
    return provenance.stamp(**extra)


# ---- S5.0: the inventory ---------------------------------------------------
#
# `predicate` is prose for the report; the executable form is in
# `shadow_reasons` below and the two are held together by the equivalence proof,
# not by anyone reading both. `structural` means the rule bans a SHAPE (a
# position, an opponent relationship) and cannot be relaxed by moving a number;
# a threshold rule has a constant that could be retuned instead of removed.
# `slate_dependent` means the rule's behaviour changes with the pool it is
# applied to, which matters because a rule that switches itself off on some
# boards cannot be audited from one board.
RULES = (
    {"id": "CPT-POOL",
     "name": "captain must be in our entry pool",
     "predicate": "captain is a _field_only player (depth-gate dropped, kept for the field)",
     "kind": "entry_pool", "structural": True, "slate_dependent": True,
     "source": "dfs_tourney.build_nfl_showdown.cpt_ok / research.sd_board.universe",
     "why": "we cannot submit a player our own depth gate removed from the pool"},
    {"id": "CPT-POS",
     "name": "no kicker or defense as captain",
     "predicate": "captain pos not in nfl_dfs._SD_GPP_CPT_POS = {QB, RB, WR, TE}",
     "kind": "owner_strategy", "structural": True, "slate_dependent": False,
     "source": "nfl_dfs._SD_GPP_CPT_POS",
     "why": "ETR: outlier D/ST games are unpredictable and draw high ownership when "
            "targeted; WRs captained 31.4% of top-1% lineups"},
    {"id": "CPT-SALARY",
     "name": "captain salary floor",
     "predicate": ("captain cpt_salary < nfl_dfs._SD_MIN_CPT_SALARY = 11000, applied "
                   "only when the pool offers >= 3 eligible captains at that price"),
     "kind": "owner_strategy", "structural": False, "slate_dependent": True,
     "source": "nfl_dfs._SD_MIN_CPT_SALARY + the `rich` gate in the builder",
     "why": "the winning captain paid >= $11,400 in 17 of 19 ETR-reviewed slates; the "
            "cap arithmetic likes a cheap captain and the record says it does not win"},
    {"id": "FLEX-POOL",
     "name": "every flex must be in our entry pool",
     "predicate": "any flex is a _field_only player",
     "kind": "entry_pool", "structural": True, "slate_dependent": True,
     "source": "dfs_tourney.build_nfl_showdown.entry_ok / research.sd_board.universe",
     "why": "same gate as CPT-POOL, at the other five slots"},
    {"id": "DST-OPP",
     "name": "no defense facing our captain",
     "predicate": ("a flex DST whose team differs from the captain's, when the captain "
                   "is not himself a DST"),
     "kind": "owner_strategy", "structural": True, "slate_dependent": False,
     "source": "nfl_dfs._sd_allowed, first branch",
     "why": "the lineup would need one offense to score and to stall at once"},
    {"id": "MAX-KDST",
     "name": "at most two kickers/defenses",
     "predicate": "count of K or DST over all six roster slots >= 3",
     "kind": "owner_strategy", "structural": False, "slate_dependent": False,
     "source": "nfl_dfs._SD_MAX_KDST = 2",
     "why": "measured: lineups with more than two finished top-1% at a 1.1% rate"},
    {"id": "MAX-PUNT",
     "name": "at most two punt-priced players",
     "predicate": ("count of slots whose FLEX salary <= nfl_dfs._SD_PUNT_SALARY = 2000 "
                   ">= 3 (the captain counts at his flex salary, not his captain price)"),
     "kind": "owner_strategy", "structural": False, "slate_dependent": False,
     "source": "nfl_dfs._SD_MAX_PUNTS = 2, _SD_PUNT_SALARY = 2000",
     "why": "no measured top finisher carried more than two $200-$2,000 punts"},
    {"id": "PUNT-ROLE",
     "name": "a punt must have a job",
     "predicate": ("a FLEX player at salary <= 2000 whose depth tag is set and is not in "
                   "nfl_dfs._SD_PUNT_ROLES = {RB1, RB2, WR1, WR2, WR3, TE1}; the captain "
                   "slot is NOT checked by this rule in production"),
     "kind": "owner_strategy", "structural": True, "slate_dependent": True,
     "source": "nfl_dfs._SD_PUNT_ROLES",
     "why": "a second-string tight end at punt price is a blocker with a lottery ticket"},
    {"id": "MAX-TE-TEAM",
     "name": "one tight end per team",
     "predicate": ("some team supplies >= 2 tight ends across all six slots "
                   "(nfl_dfs._SD_MAX_TE_PER_TEAM = 1)"),
     "kind": "owner_strategy", "structural": False, "slate_dependent": False,
     "source": "nfl_dfs._SD_MAX_TE_PER_TEAM = 1",
     "why": "the owner: two tight ends from one team split one role, unlike two "
            "receivers who can each be a first read on different drives"},
)

RULE_IDS = tuple(r["id"] for r in RULES)
OWNER_RULES = tuple(r["id"] for r in RULES if r["kind"] == "owner_strategy")
POOL_RULES = tuple(r["id"] for r in RULES if r["kind"] == "entry_pool")

# DK legality, listed so the report can show what was deliberately NOT studied.
# These are enforced by the enumerator, so no enumerated lineup can violate one
# and none of them is ever ablated.
LEGALITY = (
    {"id": "DK-ROSTER", "predicate": "exactly 1 captain + 5 distinct flex"},
    {"id": "DK-CAP", "predicate": "cpt_salary + sum(flex salary) <= nfl_dfs.CAP = 50000"},
    {"id": "DK-TEAMS", "predicate": "at least min_teams = 2 distinct teams"},
)


def rich_gate(ents):
    """Production's `rich` switch: CPT-SALARY applies only when the pool offers
    three captains at the floor, so a flat-priced exhibition slate is not
    emptied. Slate-dependent by design, which is why the artifact records it."""
    import nfl_dfs
    return sum(1 for p in ents if p.get("pos") in nfl_dfs._SD_GPP_CPT_POS
               and not p.get("_field_only")
               and p["cpt_salary"] >= nfl_dfs._SD_MIN_CPT_SALARY) >= 3


# ---- S5.1: the reason-aware shadow classifier ------------------------------
def reason_masks(ents, idx, rich=None):
    """{rule_id: bool mask over idx} -- which lineups violate which rule.

    Vectorised over the whole universe, and INDEPENDENT per rule: production
    short-circuits at the first failure, which is exactly the attribution error
    S5.2 forbids. A lineup violating four rules sets four masks.

    The count rules are evaluated as set predicates over all six slots. That is
    equivalent to production's cumulative form -- production fails the k-th
    qualifying player when k-1 already sit in the lineup, so it rejects exactly
    the lineups whose total exceeds the max -- and the equivalence proof below
    is what makes that claim checkable rather than argued.
    """
    import numpy as np

    import nfl_dfs
    if rich is None:
        rich = rich_gate(ents)
    pos = np.array([e.get("pos") or "" for e in ents])
    team = np.array([e.get("team") or "" for e in ents])
    sal = np.array([int(e["salary"]) for e in ents], dtype=np.int32)
    csal = np.array([int(e["cpt_salary"]) for e in ents], dtype=np.int32)
    fld = np.array([bool(e.get("_field_only")) for e in ents])
    tag = np.array([(e.get("depth") or "").split("·")[0] for e in ents])

    is_kdst = np.isin(pos, ["K", "DST"])
    is_te = pos == "TE"
    is_dst = pos == "DST"
    is_punt = sal <= nfl_dfs._SD_PUNT_SALARY
    # a tagged punt whose tag is not a real job; an untagged row passes, exactly
    # as production does ("one the gate did not tag passes")
    bad_punt_role = is_punt & (tag != "") & ~np.isin(tag, sorted(nfl_dfs._SD_PUNT_ROLES))
    cpt_pos_ok = np.isin(pos, sorted(nfl_dfs._SD_GPP_CPT_POS))

    c = idx[:, 0]
    flex = idx[:, 1:]
    m = {}

    m["CPT-POOL"] = fld[c]
    m["CPT-POS"] = ~cpt_pos_ok[c]
    m["CPT-SALARY"] = ((csal[c] < nfl_dfs._SD_MIN_CPT_SALARY) if rich
                       else np.zeros(len(idx), dtype=bool))
    m["FLEX-POOL"] = fld[flex].any(axis=1)
    # the captain's opponents' defense, at FLEX only -- a DST captain is CPT-POS
    m["DST-OPP"] = (is_dst[flex] & (~is_dst[c])[:, None]
                    & (team[flex] != "") & (team[flex] != team[c][:, None])).any(axis=1)
    m["MAX-KDST"] = (is_kdst[c].astype(np.int8)
                     + is_kdst[flex].sum(axis=1)) > nfl_dfs._SD_MAX_KDST
    # the captain counts at his FLEX salary here, as production does
    m["MAX-PUNT"] = (is_punt[c].astype(np.int8)
                     + is_punt[flex].sum(axis=1)) > nfl_dfs._SD_MAX_PUNTS
    m["PUNT-ROLE"] = bad_punt_role[flex].any(axis=1)

    te_slot = np.concatenate([is_te[c][:, None], is_te[flex]], axis=1)
    tm_slot = np.concatenate([team[c][:, None], team[flex]], axis=1)
    worst = np.zeros(len(idx), dtype=np.int8)
    for a in range(6):
        for b in range(a + 1, 6):
            worst = np.maximum(worst, (te_slot[:, a] & te_slot[:, b]
                                       & (tm_slot[:, a] == tm_slot[:, b])).astype(np.int8))
    m["MAX-TE-TEAM"] = worst.astype(bool)
    assert set(m) == set(RULE_IDS), set(m) ^ set(RULE_IDS)
    return m


def shadow_allowed(masks, disabled=()):
    """The owner-allowed mask with `disabled` rules switched off and every other
    rule still active. S5.7's marginal admissions are exactly
    shadow_allowed(masks, (R,)) & ~shadow_allowed(masks, ())."""
    import numpy as np
    off = set(disabled)
    bad = None
    for rid in RULE_IDS:
        if rid in off:
            continue
        bad = masks[rid] if bad is None else (bad | masks[rid])
    n = len(next(iter(masks.values())))
    return np.ones(n, dtype=bool) if bad is None else ~bad


# ---- S5.12: the binding S4 condition ---------------------------------------
def s4_rerun_required(allowed_before, allowed_after, removed):
    """Does a proposed rule change invalidate S4's Top1-vs-Top0.1 conclusion?

    S4 measured the objective comparison INSIDE the existing allowed universe,
    shortlisting 1,200 candidates out of ~23,820 enterable. A rule change that
    materially reshapes that universe leaves S4 describing a candidate space
    that no longer exists, so the conclusion has to be re-measured before it is
    quoted again.

    Two triggers, and either is enough:
      - the enterable count moves by more than 10% in either direction, or
      - a STRUCTURAL rule is removed (one that bans a shape rather than setting
        a number) -- because relaxing a threshold moves the boundary while
        removing a structural rule admits lineup SHAPES the comparison never
        saw at any threshold.

    Executable rather than prose so a guard can test it, and so the answer for a
    given proposal is a computation rather than a judgement call.
    """
    by_id = {r["id"]: r for r in RULES}
    unknown = [r for r in removed if r not in by_id]
    if unknown:
        raise KeyError(f"unknown rule id(s): {unknown}")
    before = max(1, int(allowed_before))
    change = (int(allowed_after) - before) / before
    structural = sorted(r for r in removed if by_id[r]["structural"])
    return {"allowed_before": int(allowed_before), "allowed_after": int(allowed_after),
            "relative_change": round(change, 6),
            "count_trigger": bool(abs(change) > 0.10),
            "structural_removed": structural,
            "structural_trigger": bool(structural),
            "rerun_required": bool(abs(change) > 0.10 or structural)}


# ---- the board ------------------------------------------------------------
def board(feed_dir, dg, worlds=WORLDS, seed=BOARD_SEED, log=print):
    """Enumerate a live board and prove the classifier equals production on it."""
    import numpy as np

    from research import sd_board
    sd_board.feeds(feed_dir)
    slate, contest = sd_board.slate_and_contest(dg)
    if not slate:
        raise SystemExit(f"draft group {dg} no longer resolves")
    ents, _secs, _rss = sd_board.ents_for(slate, sd_board.WEEK, nfl_dfs_discrete(),
                                          n_sims=worlds, seed=seed, log=log)
    idx, _W, allowed = sd_board.universe(ents)
    rich = rich_gate(ents)
    masks = reason_masks(ents, idx, rich=rich)
    shadow = shadow_allowed(masks)

    # S5.1's gate. Exhaustive, not sampled: every enumerated lineup on this
    # board. A single disagreement stops the stage.
    mism = int((shadow != allowed).sum())
    log(f"[S5] dg {dg}: {len(idx):,} legal, {int(allowed.sum()):,} allowed "
        f"({100.0 * allowed.mean():.2f}%), classifier mismatches {mism}")
    if mism:
        bad = np.where(shadow != allowed)[0][:5]
        raise SystemExit(f"shadow classifier disagrees with production on {mism} "
                         f"lineups, first {bad.tolist()} -- STOP")

    n = len(idx)
    hits = {r: int(masks[r].sum()) for r in RULE_IDS}
    only = {}
    for r in RULE_IDS:
        others = np.zeros(n, dtype=bool)
        for q in RULE_IDS:
            if q != r:
                others |= masks[q]
        only[r] = int((masks[r] & ~others).sum())
    marginal = {r: int((shadow_allowed(masks, (r,)) & ~shadow).sum()) for r in RULE_IDS}
    nviol = np.zeros(n, dtype=np.int16)
    for r in RULE_IDS:
        nviol += masks[r].astype(np.int16)
    hist = {int(k): int(v) for k, v in zip(*np.unique(nviol, return_counts=True))}

    # the rule-overlap matrix: how often each pair fires on the same lineup
    overlap = {a: {b: int((masks[a] & masks[b]).sum()) for b in RULE_IDS} for a in RULE_IDS}

    # the commonest violated sets, so the report can show what a forbidden
    # lineup actually looks like rather than a marginal count
    combos = {}
    forb = np.where(~shadow)[0]
    key = np.zeros(n, dtype=np.int32)
    for i, r in enumerate(RULE_IDS):
        key |= (masks[r].astype(np.int32) << i)
    ks, cs = np.unique(key[forb], return_counts=True)
    for k, cnt in sorted(zip(ks.tolist(), cs.tolist()), key=lambda kv: -kv[1])[:15]:
        names = "+".join(RULE_IDS[i] for i in range(len(RULE_IDS)) if k >> i & 1)
        combos[names] = int(cnt)

    return {"draft_group_id": int(dg), "contest": contest.get("name"),
            "entries": int(contest.get("max_entries") or contest.get("entered") or 0),
            "players": len(ents),
            "field_only_players": sum(1 for e in ents if e.get("_field_only")),
            "rich_gate": bool(rich),
            "legal": n, "allowed": int(allowed.sum()),
            "forbidden": int(n - allowed.sum()),
            "survival_pct": round(100.0 * float(allowed.mean()), 4),
            "classifier_mismatches": mism,
            "hits": hits, "exclusive_hits": only, "marginal_admissions": marginal,
            "violations_histogram": hist, "overlap": overlap,
            "top_combinations": combos}


def nfl_dfs_discrete():
    import nfl_dfs_sim
    return nfl_dfs_sim.SD_DISCRETE


def run(feed_dir, dgs=DGS, log=print):
    out = {"meta": {"stage": "S5.0/S5.1 rule inventory + exhaustive shadow classifier",
                    "boards": list(dgs), "worlds": WORLDS,
                    "money": "CLOSED -- no payout metric appears in this stage",
                    "rules": list(RULES), "legality": list(LEGALITY),
                    "owner_strategy_rules": list(OWNER_RULES),
                    "entry_pool_rules": list(POOL_RULES),
                    "provenance": _prov(worlds=WORLDS, model="legacy-latent-discrete",
                                        seed=BOARD_SEED)},
           "boards": {}}
    for dg in dgs:
        out["boards"][str(dg)] = board(feed_dir, dg, log=log)
    with open(os.path.join(DATA, "s5_rules.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "feeds")
    dl = tuple(int(x) for x in sys.argv[2:]) or DGS
    run(fd, dl)
    print("S5 rule inventory written")
