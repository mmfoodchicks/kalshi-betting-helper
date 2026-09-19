"""S7, first artifact: the Showdown field as it actually entered, FIT-FREE.

Every money number in the Showdown work rests on one assumption about the
public: that the field's lineup frequencies follow softmax(beta x projection)
over the legal universe, with beta solved so the most popular lineup holds
FIELD_TOP_SHARE (0.2%) of entries. That was a guess. DraftKings' full standings
export for a finished contest is the only place the actual entries exist, and
the reviewer's order of 2026-09-19 was: freeze the raw file, reconcile it,
report the raw empirical table with the placeholder model beside every
comparable quantity, and fit NOTHING in this artifact.

Inputs, all pinned (three contests, see CONTESTS):
  research/data/dk_standings/contest-standings-<cid>.csv.gz   the frozen exports (manifest hashes them)
  research/data/dk_capture  each contest's draft group and detail
  research/data/feeds       Sleeper projections and rosters per week

Two caveats the reader must carry. The DraftKings pool and the Sleeper feeds
for this board were captured AFTER the game (the contest was exported after it
was played and the lobby no longer listed it); salaries do not change after
lock but Sleeper's projections are not versioned, so the projection column
cannot be proven equal to what was served at lock. And the roster capture is
post-game, so injury statuses are what Sleeper shows now, not at lock: the
export itself shows DJ Moore in 43% of entries, and the post-game roster lists
him Out. For the model universe the injury statuses are therefore CLEARED and
the depth-chart rank rule kept, which reconstructs the lock-time universe the
served builder would have enumerated; the clearing is stamped on the artifact.

Three reconciliation checks come first (rows, lineups, ownership), then the
raw table, then the placeholder model beside it, then observed frequency by
model-probability bin, then the winning lineup as a diagnostic row only.

    python3 -m research.s7_field
"""
import collections
import csv
import gzip
import json
import math
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
FEEDS = os.path.join(DATA, "feeds")
STANDINGS = os.path.join(DATA, "dk_standings")

#: the Showdown contests with a frozen standings export, oldest first. `captured`
#: says what is and is not a pre-lock capture, per contest; the reader carries it.
CONTESTS = {
    193391013: {"dg": 151820, "week": 1, "roster": "players_2026_1_nesea.json", "label": "NE @ SEA, Wednesday Kickoff Millionaire",
                "captured": ("pool and roster captured post-game (2026-09-19); the week-1 Sleeper feeds were captured "
                             "2026-09-12, AFTER this game (2026-09-10)")},
    195526287: {"dg": 153086, "week": 1, "roster": None, "label": "DEN @ KC, Monday Night Showdown (the S5 / A/B primary)",
                "captured": ("pool, contest and week-1 feeds captured PRE-LOCK by S6 on 2026-09-12 (the game was 2026-09-14); "
                             "roster captured 2026-09-18, post-game")},
    195677825: {"dg": 153434, "week": 2, "roster": None, "label": "DET @ BUF, Thursday Night Showdown",
                "captured": "pool, week-2 feeds and roster all captured post-game (2026-09-19)"},
}
SEED = 20260912
CPT_MULT = 1.5
CAP = 50000
DUP_BUCKETS = ((1, 1), (2, 2), (3, 5), (6, 10), (11, 50), (51, 10 ** 9))


def _norm(name):
    return " ".join(str(name or "").split())


def _norm_key(name):
    """The pool matcher's key: lower case, suffixes and punctuation out."""
    import nfl_adp
    return nfl_adp._norm(name)


def parse_lineup(text):
    """'CPT A FLEX B FLEX C ...' -> (cpt, [b, c, d, e, f]) or None for an empty
    entry; raises on any other shape (an entry is never silently reshaped)."""
    t = _norm(text)
    if not t:
        return None
    parts = [p.strip() for p in re.split(r"\s(?=(?:CPT|FLEX)\s)", " " + t) if p.strip()]
    slots = [(p.split(" ", 1)[0], _norm(p.split(" ", 1)[1]) if " " in p else "") for p in parts]
    cpt = [n for s, n in slots if s == "CPT"]
    flex = [n for s, n in slots if s == "FLEX"]
    if len(cpt) != 1 or len(flex) != 5 or len(slots) != 6 or not all(n for _, n in slots):
        raise ValueError(f"lineup does not resolve to 1 CPT + 5 FLEX: {text!r}")
    return cpt[0], flex


def load_standings(path):
    rows = list(csv.reader(gzip.open(path, "rt", encoding="utf-8-sig")))
    header, body = rows[0], rows[1:]
    assert header[:6] == ["Rank", "EntryId", "EntryName", "TimeRemaining", "Points", "Lineup"], header
    entries, dk_own, fpts, malformed = [], {}, {}, []
    for r in body:
        m = re.search(r"\((\d+)/(\d+)\)\s*$", r[2])
        try:
            lu = parse_lineup(r[5])
        except ValueError as e:
            malformed.append(str(e))
            lu = None
        entries.append({"rank": int(r[0]), "entry_id": r[1], "user": (r[2][:m.start()].strip() if m else r[2].strip()),
                        "user_entries": (int(m.group(2)) if m else 1), "points": float(r[4] or 0.0),
                        "lineup": lu, "raw": r[5]})
        if len(r) > 10 and r[7].strip():
            key = (_norm(r[7]), r[8].strip().upper())
            dk_own[key] = float(r[9].rstrip("%")) / 100.0
            fpts[_norm(r[7])] = float(r[10] or 0.0)
    return {"header": header, "entries": entries, "dk_ownership": dk_own, "fpts": fpts, "malformed": malformed}


def dk_pool(slate):
    """The whole DraftKings pool for the draft group, with and without a
    projection: {name: {pos, team, salary, cpt_salary}}."""
    import nfl_dfs
    import simulate
    return {e["name"]: {"pos": e["pos"], "team": e.get("team"), "salary": int(e["salary"]),
                        "cpt_salary": int(e["cpt_salary"])}
            for e in nfl_dfs.showdown_pool(simulate.parse_dk_csv(slate["csv"]))}


def reconcile(std, pool, detail):
    """The three checks the reviewer named. Nothing is dropped silently: every
    row is classified and the classes sum to the row count. A contest that did
    not fill has fewer rows than its capacity; the export then holds every
    entry that exists, and the ownership check (whose denominator is the row
    count) is what confirms that the rows ARE the field."""
    E = std["entries"]
    n = len(E)
    empty = sum(1 for e in E if e["lineup"] is None and not _norm(e["raw"]))
    malformed = len(std["malformed"])
    ids = {e["entry_id"] for e in E}
    unresolved = collections.Counter()
    resolved = 0
    for e in E:
        if e["lineup"] is None:
            continue
        names = [e["lineup"][0]] + e["lineup"][1]
        miss = [nm for nm in names if nm not in pool]
        if miss:
            for nm in miss:
                unresolved[nm] += 1
        else:
            resolved += 1
    # ownership recomputed from the lineups, over ALL entries (the denominator
    # DraftKings uses: its CPT column sums to 100% minus the empty share)
    cpt = collections.Counter()
    flx = collections.Counter()
    for e in E:
        if e["lineup"] is None:
            continue
        cpt[e["lineup"][0]] += 1
        for nm in e["lineup"][1]:
            flx[nm] += 1
    diffs = []
    for (nm, slot), pct in std["dk_ownership"].items():
        ours = (cpt if slot == "CPT" else flx)[nm] / n
        diffs.append({"name": nm, "slot": slot, "dk_pct": round(100 * pct, 2), "ours_pct": round(100 * ours, 2),
                      "diff_pp": round(100 * (ours - pct), 3)})
    worst = max(diffs, key=lambda d: abs(d["diff_pp"])) if diffs else None
    C = int(detail.get("max_entries") or 0)
    out = {"rows": {"parsed": n, "capacity": C, "reconciles": n == C, "overlay_entries": max(0, C - n),
                    "field_size_used": n,
                    "distinct_entry_ids": len(ids), "ids_unique": len(ids) == n,
                    "empty_lineup_rows": empty, "malformed_lineup_rows": malformed,
                    "with_lineup": n - empty - malformed,
                    "note": "an empty lineup is an entry that never set one (points 0, rank last); counted, not dropped"},
           "lineups": {"resolved_to_pool": resolved, "unresolved_entries": sum(1 for e in E if e["lineup"] and any(nm not in pool for nm in [e["lineup"][0]] + e["lineup"][1])),
                       "unresolved_names": dict(unresolved), "pool_size": len(pool),
                       "note": "every entry with a lineup has exactly 1 CPT and 5 FLEX (parse_lineup raises otherwise) and every name resolves to one DraftKings pool player"},
           "ownership": {"dk_rows": len(std["dk_ownership"]), "max_abs_diff_pp": round(max(abs(d["diff_pp"]) for d in diffs), 3) if diffs else None,
                         "worst": worst, "denominator": "all entries, including empty ones",
                         "reconciles_within_rounding": bool(diffs) and max(abs(d["diff_pp"]) for d in diffs) <= 0.01,
                         "note": "DraftKings' %Drafted per CPT and FLEX slot recomputed from the raw lineups; rounding is 0.01 pp"}}
    # rows reconcile either to the capacity (a full contest) or, for one that did
    # not fill, to DraftKings' own ownership denominator (the check just above)
    out["rows"]["reconciles"] = bool(n == C or (n < C and out["ownership"]["reconciles_within_rounding"]))
    out["rows"]["note_capacity"] = ("filled to capacity" if n == C else
                                    f"{n:,} entries against a capacity of {C:,}: the contest did not fill; the ownership "
                                    f"recomputation over {n:,} rows reproduces DraftKings' percentages, so the rows are the field")
    out["all_checks_pass"] = bool(out["rows"]["reconciles"] and out["rows"]["ids_unique"] and malformed == 0
                                  and out["lineups"]["unresolved_entries"] == 0 and out["ownership"]["reconciles_within_rounding"])
    return out


def points_check(path, std):
    """Every active entry's Points recomputed from DraftKings' own role-specific
    FPTS table (the CPT row already carries the 1.5x): the parser's lineup and
    the export's score must agree, entry by entry. The right-hand table is
    keyed by (player, slot); keying it by player alone would overwrite the CPT
    row with the FLEX row and the check would fail by tens of points -- which
    is how a first version of this check failed."""
    fp = {}
    for r in csv.reader(gzip.open(path, "rt", encoding="utf-8-sig")):
        if len(r) > 10 and r[7].strip() and r[8].strip().upper() in ("CPT", "FLEX"):
            fp[(_norm(r[7]), r[8].strip().upper())] = float(r[10] or 0.0)
    maxd, n = 0.0, 0
    for e in std["entries"]:
        if e["lineup"] is None:
            continue
        c, fl = e["lineup"]
        if (c, "CPT") in fp and all((x, "FLEX") in fp for x in fl):
            maxd = max(maxd, abs(fp[(c, "CPT")] + sum(fp[(x, "FLEX")] for x in fl) - e["points"]))
            n += 1
    return {"entries_checked": n, "max_abs_diff_points": round(maxd, 6), "reconciles": n > 0 and maxd < 0.001}


def field_rules(std, pool):
    """The four owner rules that can be read off the standings alone (the
    independent S7 study's screen, reproduced with production's definitions:
    nfl_dfs._sd_allowed), per entry, for the whole field and by finishing
    group; plus each winner's duplicate rank among the unique lineups. The
    salary, punt and depth-chart rules need the pool and the roster and are
    not part of this screen (the artifact's `winner.rules_failed` applies the
    full set to the winner and the chalk).

    Definitions, so a reader can reproduce the independent study's slightly
    different figures: DST-OPP here bars the opposing defense beside ANY
    non-DST captain (production), the independent screen beside an offensive
    captain only; the tight-end count here comes from the DraftKings pool's
    positions, so an unprojected tight end (Jackson Hawes) counts."""
    import nfl_dfs
    E = [e for e in std["entries"] if e["lineup"]]
    n_all = len(std["entries"])

    def fails(e):
        c, fl = e["lineup"]
        cp, picks = pool[c], [pool[x] for x in fl]
        r = []
        if cp["pos"] in ("K", "DST"):
            r.append(nfl_dfs._SD_RULES[1])
        if sum(1 for p in [cp] + picks if p["pos"] in ("K", "DST")) > nfl_dfs._SD_MAX_KDST:
            r.append(nfl_dfs._SD_RULES[2])
        if cp["pos"] != "DST" and any(p["pos"] == "DST" and p["team"] != cp["team"] for p in picks):
            r.append(nfl_dfs._SD_RULES[0])
        if any(v > 1 for v in collections.Counter(p["team"] for p in [cp] + picks if p["pos"] == "TE").values()):
            r.append(nfl_dfs._SD_RULES[3])
        return r
    F_ = {e["entry_id"]: fails(e) for e in E}
    k1, k01 = max(1, int(0.01 * n_all)), max(1, int(0.001 * n_all))

    def group(sel):
        cnt = collections.Counter(r for e in sel for r in F_[e["entry_id"]])
        return {"entries": len(sel), "failing_any_pct": round(100 * sum(1 for e in sel if F_[e["entry_id"]]) / max(1, len(sel)), 2),
                "by_rule": {r: v for r, v in cnt.most_common()}}
    lc = collections.Counter((e["lineup"][0], tuple(sorted(e["lineup"][1]))) for e in E)
    best = max(e["points"] for e in E)
    w = next(e for e in E if e["points"] == best)
    wc = lc[(w["lineup"][0], tuple(sorted(w["lineup"][1])))]
    return {"rules": [nfl_dfs._SD_RULES[i] for i in (1, 2, 0, 3)],
            "field": {"failing_any_pct_of_active": round(100 * sum(1 for e in E if F_[e["entry_id"]]) / len(E), 2),
                      "failing_any_pct_of_all_entries": round(100 * sum(1 for e in E if F_[e["entry_id"]]) / n_all, 2),
                      "by_rule_pct_of_all_entries": {r: round(100 * sum(1 for e in E if r in F_[e["entry_id"]]) / n_all, 2)
                                                     for r in [nfl_dfs._SD_RULES[i] for i in (1, 2, 0, 3)]}},
            "top_1pct_by_rank": group([e for e in E if e["rank"] <= k1]),
            "top_01pct_by_rank": group([e for e in E if e["rank"] <= k01]),
            "winner_duplicate_rank": {"copies": wc, "rank_among_unique_lineups": 1 + sum(1 for v in lc.values() if v > wc),
                                      "lineups_with_the_same_copy_count": sum(1 for v in lc.values() if v == wc)}}


def _structure(names, pool):
    c = collections.Counter(pool[nm]["team"] for nm in names)
    return "-".join(str(x) for x in sorted(c.values(), reverse=True))


def _wq(values, weights, qs):
    order = np.argsort(values)
    v, w = np.asarray(values)[order], np.asarray(weights)[order]
    cw = np.cumsum(w) / w.sum()
    return [float(v[min(len(v) - 1, int(np.searchsorted(cw, q)))]) for q in qs]


def empirical(std, pool, detail):
    E = [e for e in std["entries"] if e["lineup"] is not None
         and all(nm in pool for nm in [e["lineup"][0]] + e["lineup"][1])]
    n_all = len(std["entries"])
    keys = [(e["lineup"][0], tuple(sorted(e["lineup"][1]))) for e in E]
    lc = collections.Counter(keys)
    counts = np.asarray(sorted(lc.values(), reverse=True), dtype=np.float64)
    share = counts / n_all
    dup = {}
    for lo, hi in DUP_BUCKETS:
        m = (counts >= lo) & (counts <= hi)
        dup[f"{lo}-{hi if hi < 10 ** 9 else 'plus'}"] = {"lineups": int(m.sum()), "entries": int(counts[m].sum()),
                                                        "entry_share_pct": round(100 * counts[m].sum() / n_all, 2)}
    cpt = collections.Counter(); flx = collections.Counter(); comb = collections.Counter()
    cpt_pos = collections.Counter(); struct = collections.Counter(); kd = collections.Counter()
    sal_used = []
    for e in E:
        c, fl = e["lineup"]
        cpt[c] += 1; comb[c] += 1
        for nm in fl:
            flx[nm] += 1; comb[nm] += 1
        cpt_pos[pool[c]["pos"]] += 1
        struct[_structure([c] + fl, pool)] += 1
        kd[sum(1 for nm in [c] + fl if pool[nm]["pos"] in ("K", "DST"))] += 1
        sal_used.append(pool[c]["cpt_salary"] + sum(pool[nm]["salary"] for nm in fl))
    sal_left = CAP - np.asarray(sal_used, dtype=np.float64)
    users = collections.Counter(e["user_entries"] for e in std["entries"])
    top = lambda cnt, k=15: [{"name": nm, "pct": round(100 * v / n_all, 2)} for nm, v in cnt.most_common(k)]
    return {"entries_with_resolved_lineup": len(E), "distinct_lineups": len(lc),
            "max_lineup_share_pct": round(100 * float(share[0]), 3), "max_lineup_copies": int(counts[0]),
            "top_lineups": [{"copies": int(v), "share_pct": round(100 * v / n_all, 3), "cpt": k[0], "flex": list(k[1]),
                             "structure": _structure([k[0]] + list(k[1]), pool)} for k, v in lc.most_common(5)],
            "lineup_mass_pct": {"top_10": round(100 * float(share[:10].sum()), 2), "top_100": round(100 * float(share[:100].sum()), 2),
                                "top_1pct_of_lineups": round(100 * float(share[:max(1, len(share) // 100)].sum()), 2)},
            "effective_lineups": {"inverse_sum_p2": round(float(1.0 / np.sum(share ** 2)), 1),
                                  "exp_entropy": round(float(np.exp(-np.sum(share * np.log(share)))), 1)},
            "duplicates": dup,
            "cpt_ownership": top(cpt), "flex_ownership": top(flx), "combined_ownership": top(comb),
            "cpt_position_mix_pct": {p: round(100 * v / len(E), 2) for p, v in cpt_pos.most_common()},
            "structure_pct": {s: round(100 * v / len(E), 2) for s, v in sorted(struct.items())},
            "k_dst_slots_pct": {str(k): round(100 * v / len(E), 2) for k, v in sorted(kd.items())},
            "salary_left": {"mean": round(float(sal_left.mean()), 1), "median": float(np.median(sal_left)),
                            "p10_p25_p75_p90": [float(np.percentile(sal_left, q)) for q in (10, 25, 75, 90)],
                            "share_at_zero_pct": round(100 * float((sal_left == 0).mean()), 2),
                            "share_ge_1000_pct": round(100 * float((sal_left >= 1000).mean()), 2)},
            "user_max_entry_brackets": {str(k): {"entries": v, "share_pct": round(100 * v / n_all, 2)}
                                        for k, v in sorted(users.items(), key=lambda kv: -kv[1])[:8]},
            "share_from_150_max_users_pct": round(100 * users.get(150, 0) / n_all, 2)}


def model_field(slate, pool, detail, week, roster_name=None, n_entries=None, log=print):
    """The placeholder field as production would enumerate it at lock,
    reconstructed: the pinned pool and feeds, the depth-chart RANK rule, injury
    statuses cleared (see the module docstring), softmax(beta x projection)
    with beta solved for FIELD_TOP_SHARE. `n_entries` is the field the
    duplicate expectation is drawn for (the entries that exist, not the
    capacity, when a contest did not fill)."""
    import dfs_tourney as T
    import nfl_adp
    from research import sd_board
    S = sd_board.feeds(FEEDS, week=week, roster_name=roster_name)
    S._cache.clear()
    recs, stamp = nfl_adp._PINNED
    cleared = {k: dict(v, injury=None, injury_return=False) for k, v in recs.items()}
    nfl_adp._PINNED = (cleared, dict(stamp, injury_statuses="CLEARED for the lock-time reconstruction: every roster "
                                                          "capture here is post-game and the exports show players the "
                                                          "post-game roster lists Out (DJ Moore, 43% owned in DET @ BUF; "
                                                          "Marvin Mims Jr. in DEN @ KC)"))
    ents, _, _ = sd_board.ents_for(slate, week, True, n_sims=300, seed=SEED, log=lambda *a, **k: None, model="legacy")
    idx, W, allowed = sd_board.universe(ents)
    f, beta = T.field_weights(ents, idx, cpt_mult=CPT_MULT)
    C = int(n_entries or detail["max_entries"])
    own_c, own_f = T.field_ownership(ents, idx, f)
    name_of = [e["name"] for e in ents]
    pos_of = np.asarray([e["pos"] for e in ents])
    team_of = np.asarray([e["team"] for e in ents])
    proj = np.asarray([float(e.get("proj") or 0.0) for e in ents])
    sal = np.asarray([int(e["salary"]) for e in ents]); csal = np.asarray([int(e["cpt_salary"]) for e in ents])
    used = csal[idx[:, 0]] + sal[idx[:, 1:]].sum(axis=1)
    left = CAP - used
    struct = collections.defaultdict(float)
    teams = sorted(set(team_of))
    a = (team_of[idx] == teams[0]).sum(axis=1)
    for s_ in range(0, 7):
        m = a == s_
        if m.any():
            struct["-".join(str(x) for x in sorted((s_, 6 - s_), reverse=True))] += float(f[m].sum())
    kd = np.isin(pos_of[idx], ("K", "DST")).sum(axis=1)
    order = np.argsort(-f)
    cum = np.cumsum(f[order])
    lam = C * f
    exp_dup = {}
    # expected number of lineups whose copy count lands in each bucket, and the
    # entries those copies account for, under Poisson(C f) per lineup
    for lo, hi in DUP_BUCKETS:
        hi_ = min(hi, 400)
        n_lineups, n_entries = 0.0, 0.0
        for k in range(lo, hi_ + 1):
            pk = np.exp(-lam + k * np.log(np.maximum(lam, 1e-300)) - math.lgamma(k + 1))
            n_lineups += float(pk.sum())
            n_entries += float(k * pk.sum())
        exp_dup[f"{lo}-{hi if hi < 10 ** 9 else 'plus'}"] = {"lineups": round(n_lineups, 1), "entries": round(n_entries, 1),
                                                            "entry_share_pct": round(100 * n_entries / C, 2)}
    proj_lineup = proj[idx[:, 0]] * CPT_MULT + proj[idx[:, 1:]].sum(axis=1)
    raw = json.load(open(os.path.join(FEEDS, f"proj_2026_{week}.json")))
    proj_rows = {}
    for r in raw:
        pl = r.get("player") or {}
        key = _norm_key(pl.get("full_name") or f"{pl.get('first_name', '')} {pl.get('last_name', '')}")
        proj_rows.setdefault(key, []).append((r.get("team"), (r.get("stats") or {}).get("pts_ppr")))
    return {"ents": ents, "idx": idx, "allowed": allowed, "f": f, "beta": float(beta), "C": C,
            "roster_stamp": nfl_adp._PINNED[1], "proj_rows": proj_rows, "teams": set(e["team"] for e in ents),
            "proj_lineup": proj_lineup, "name_of": name_of, "pool_names": [e["name"] for e in ents if not e.get("_field_only")],
            "field_only": [e["name"] for e in ents if e.get("_field_only")],
            "summary": {"universe_lineups": int(len(idx)), "enterable_under_our_rules": int(allowed.sum()),
                        "players_in_universe": len(ents), "beta": round(float(beta), 4),
                        "max_lineup_share_pct": round(100 * float(f.max()), 3), "top_share_setting": T.FIELD_TOP_SHARE,
                        "lineup_mass_pct": {"top_10": round(100 * float(cum[9]), 2), "top_100": round(100 * float(cum[99]), 2),
                                            "top_1pct_of_lineups": round(100 * float(cum[max(0, len(f) // 100 - 1)]), 2)},
                        "effective_lineups": {"inverse_sum_p2": round(float(1.0 / np.sum(f ** 2)), 1),
                                              "exp_entropy": round(float(np.exp(-np.sum(f * np.log(np.maximum(f, 1e-300))))), 1)},
                        "expected_duplicates": exp_dup,
                        "cpt_ownership": [{"name": name_of[i], "pct": round(float(own_c[i]), 2)} for i in np.argsort(-own_c)[:15]],
                        "flex_ownership": [{"name": name_of[i], "pct": round(float(own_f[i]), 2)} for i in np.argsort(-own_f)[:15]],
                        "combined_ownership": [{"name": name_of[i], "pct": round(float(own_c[i] + own_f[i]), 2)} for i in np.argsort(-(own_c + own_f))[:15]],
                        "cpt_position_mix_pct": {p: round(100 * float(f[pos_of[idx[:, 0]] == p].sum()), 2) for p in sorted(set(pos_of))},
                        "structure_pct": {k: round(100 * v, 2) for k, v in sorted(struct.items())},
                        "k_dst_slots_pct": {str(k): round(100 * float(f[kd == k].sum()), 2) for k in sorted(set(kd.tolist()))},
                        "salary_left": {"mean": round(float((f * left).sum()), 1),
                                        "median": _wq(left, f, [0.5])[0],
                                        "p10_p25_p75_p90": _wq(left, f, [0.1, 0.25, 0.75, 0.9]),
                                        "share_at_zero_pct": round(100 * float(f[left == 0].sum()), 2),
                                        "share_ge_1000_pct": round(100 * float(f[left >= 1000].sum()), 2)}}}


def by_bin(std, pool, M):
    """Observed entry share against model probability, lineup by lineup, in
    log10 bins of the model probability; plus the entries outside the model's
    universe and who put them there."""
    name_idx = {nm: i for i, nm in enumerate(M["name_of"])}
    key_row = {}
    idx = M["idx"]
    for r in range(len(idx)):
        key_row[(int(idx[r, 0]), tuple(sorted(int(x) for x in idx[r, 1:])))] = r
    n_all = len(std["entries"])
    lc = collections.Counter()
    outside = collections.Counter(); outside_entries = 0
    for e in std["entries"]:
        if e["lineup"] is None:
            continue
        c, fl = e["lineup"]
        names = [c] + fl
        if any(nm not in name_idx for nm in names):
            outside_entries += 1
            for nm in names:
                if nm not in name_idx:
                    outside[nm] += 1
            continue
        key = (name_idx[c], tuple(sorted(name_idx[nm] for nm in fl)))
        row = key_row.get(key)
        if row is None:
            outside_entries += 1
            outside["(legal lineup not in the enumerated universe)"] += 1
            continue
        lc[row] += 1
    f = M["f"]
    rows = np.asarray(list(lc.keys()), dtype=np.int64)
    obs = np.asarray([lc[r] for r in rows], dtype=np.float64)
    logp = np.log10(np.maximum(f, 1e-300))
    edges = np.arange(-9, 0, 1)      # 1e-9 .. 1e-1
    bins = []
    for lo in edges:
        m_all = (logp >= lo) & (logp < lo + 1)
        m_obs = (logp[rows] >= lo) & (logp[rows] < lo + 1)
        if not m_all.any():
            continue
        bins.append({"log10_p_from": int(lo), "log10_p_to": int(lo + 1), "universe_lineups": int(m_all.sum()),
                     "model_entry_share_pct": round(100 * float(f[m_all].sum()), 3),
                     "observed_entry_share_pct": round(100 * float(obs[m_obs].sum() / n_all), 3),
                     "observed_distinct_lineups": int(m_obs.sum()),
                     "observed_over_model": (round(float(obs[m_obs].sum() / n_all / f[m_all].sum()), 3) if f[m_all].sum() > 0 else None)})
    # WHY each outside player is outside: no Sleeper projection row at all, a
    # projection filed under another team (the pool builder filters by the
    # game's teams, so a man traded in during the week has no line), or a
    # projection that the depth-chart gate dropped beyond the field-only extras
    reasons = {}
    for nm in list(outside):
        if nm.startswith("("):
            continue
        prj = M["proj_rows"].get(_norm_key(nm))
        if not prj or all(pts is None for _, pts in prj):
            reasons[nm] = "no Sleeper projection"
        elif not any(t in M["teams"] for t, _ in prj):
            reasons[nm] = f"projected under another team ({', '.join(sorted(set(t or '?' for t, _ in prj)))})"
        else:
            reasons[nm] = "projected; dropped by the depth-chart gate (post-game roster capture) beyond the field-only extras"
    # the irreducible hole: entries containing a player Sleeper never projected.
    # The gate's share can be lifted for a fit (the field is not bound by our
    # rules, and every roster capture here is post-game); this one cannot.
    unprojected = {nm for nm, why in reasons.items() if why == "no Sleeper projection"}
    outside_unprojected = sum(1 for e in std["entries"] if e["lineup"]
                              and any(nm in unprojected for nm in [e["lineup"][0]] + e["lineup"][1]))
    # the chalk: is the most duplicated real lineup the model's most probable?
    top_obs = rows[np.argmax(obs)]
    model_rank_of_top_obs = int((f > f[top_obs]).sum()) + 1
    top_obs_names = (M["name_of"][int(idx[top_obs, 0])], [M["name_of"][int(x)] for x in idx[top_obs, 1:]])
    top_model = int(np.argmax(f))
    return {"entries_inside_universe": int(obs.sum()), "entries_outside_universe": outside_entries,
            "outside_share_pct": round(100 * outside_entries / n_all, 2),
            "outside_by_player": [{"name": nm, "entries": v, "share_pct": round(100 * v / n_all, 2), "why": reasons.get(nm)}
                                  for nm, v in outside.most_common(12)],
            "outside_by_reason_pct": {why: round(100 * sum(v for nm, v in outside.items() if reasons.get(nm) == why) / n_all, 2)
                                      for why in sorted(set(reasons.values()))},
            "outside_by_reason_note": "share of entries containing such a player; an entry with two outside players counts under both reasons",
            "outside_if_gate_lifted_pct": round(100 * outside_unprojected / n_all, 2),
            "bins": bins,
            "chalk": {"most_duplicated_real_lineup": {"copies": int(obs.max()), "model_prob_pct": round(100 * float(f[top_obs]), 4),
                                                      "model_rank": model_rank_of_top_obs,
                                                      "cpt": M["name_of"][int(idx[top_obs, 0])], "flex": sorted(M["name_of"][int(x)] for x in idx[top_obs, 1:]),
                                                      "admitted_by_our_rules": bool(M["allowed"][top_obs]),
                                                      "rules_failed": rules_failed(top_obs_names[0], top_obs_names[1], M)},
                      "model_most_probable_lineup": {"model_prob_pct": round(100 * float(f[top_model]), 4),
                                                     "observed_copies": int(lc.get(top_model, 0)),
                                                     "cpt": M["name_of"][int(idx[top_model, 0])], "flex": sorted(M["name_of"][int(x)] for x in idx[top_model, 1:])}},
            "rank_correlation_observed_vs_model_over_observed_lineups": (
                round(float(np.corrcoef(np.argsort(np.argsort(-obs)), np.argsort(np.argsort(-f[rows])))[0, 1]), 3) if len(rows) > 2 else None),
            "observed_lineups_matched": int(len(rows))}


def rules_failed(cpt, flex, M):
    """Which of the app's showdown rules (nfl_dfs._SD_RULES, in that order) a
    lineup breaks, by name, plus the roster gate: the answer to 'would our
    optimizer have admitted it', spelled out rather than a bare False."""
    import nfl_dfs
    name_idx = {nm: i for i, nm in enumerate(M["name_of"])}
    ents = M["ents"]
    if cpt not in name_idx or any(nm not in name_idx for nm in flex):
        return ["outside the model universe (a player with no Sleeper projection)"]
    cap = ents[name_idx[cpt]]
    picks = [ents[name_idx[nm]] for nm in flex]
    out = []
    if cap.get("pos") in ("K", "DST"):
        out.append(nfl_dfs._SD_RULES[1])
    if any(p["pos"] == "DST" and cap.get("pos") != "DST" and p.get("team") != cap.get("team") for p in picks):
        out.append(nfl_dfs._SD_RULES[0])
    kd = (1 if cap.get("pos") in ("K", "DST") else 0) + sum(1 for p in picks if p["pos"] in ("K", "DST"))
    punts = (1 if cap.get("salary", 0) <= nfl_dfs._SD_PUNT_SALARY else 0) + sum(1 for p in picks if p["salary"] <= nfl_dfs._SD_PUNT_SALARY)
    if kd > nfl_dfs._SD_MAX_KDST or punts > nfl_dfs._SD_MAX_PUNTS:
        out.append(nfl_dfs._SD_RULES[2])
    tes = collections.Counter(p.get("team") for p in [cap] + picks if p.get("pos") == "TE")
    if any(v > 1 for v in tes.values()):
        out.append(nfl_dfs._SD_RULES[3])
    for p in picks:
        if p["salary"] <= nfl_dfs._SD_PUNT_SALARY:
            tag = (p.get("depth") or "").split("\u00b7")[0]
            if tag and tag not in nfl_dfs._SD_PUNT_ROLES:
                out.append(nfl_dfs._SD_RULES[4])
                break
    fo = [p["name"] for p in [cap] + picks if p.get("_field_only")]
    if fo:
        out.append(f"{nfl_dfs._SD_RULES[5]} (field-only here: {', '.join(fo)})")
    return out


def winner(std, pool, M):
    """The winning lineup as a diagnostic row, never a fitting target."""
    E = std["entries"]
    best = max(e["points"] for e in E)
    winners = [e for e in E if e["points"] == best and e["lineup"]]
    e = winners[0]
    c, fl = e["lineup"]
    names = [c] + fl
    name_idx = {nm: i for i, nm in enumerate(M["name_of"])}
    row = None
    if all(nm in name_idx for nm in names):
        key = (name_idx[c], tuple(sorted(name_idx[nm] for nm in fl)))
        idx = M["idx"]
        for r in np.where(idx[:, 0] == key[0])[0]:
            if tuple(sorted(int(x) for x in idx[r, 1:])) == key[1]:
                row = int(r)
                break
    copies = sum(1 for x in E if x["lineup"] and (x["lineup"][0], tuple(sorted(x["lineup"][1]))) == (c, tuple(sorted(fl))))
    out = {"points": best, "tied_entries_at_top": len(winners), "cpt": c, "flex": fl,
           "structure": _structure(names, pool), "salary_left": CAP - (pool[c]["cpt_salary"] + sum(pool[nm]["salary"] for nm in fl)),
           "k_dst_slots": sum(1 for nm in names if pool[nm]["pos"] in ("K", "DST")),
           "exact_lineup_copies": copies, "exact_lineup_share_pct": round(100 * copies / len(E), 3),
           "in_model_universe": row is not None}
    if row is not None:
        f = M["f"]
        out.update({"model_prob_pct": round(100 * float(f[row]), 5), "model_rank_of_lineup": int((f > f[row]).sum()) + 1,
                    "projected_points": round(float(M["proj_lineup"][row]), 2),
                    "projection_rank_in_universe": int((M["proj_lineup"] > M["proj_lineup"][row]).sum()) + 1,
                    "admitted_by_our_rules": bool(M["allowed"][row]),
                    "rules_failed": rules_failed(c, fl, M)})
    return out


def one(cid, spec, man, log=print):
    from research import s6_capture, sd_board
    path = os.path.join(STANDINGS, f"contest-standings-{cid}.csv.gz")
    std = load_standings(path)
    slate, details = s6_capture.replay(spec["dg"])
    detail = details[str(cid)]
    pool = dk_pool(slate)
    rec = reconcile(std, pool, detail)
    rec["points"] = points_check(path, std)
    rec["all_checks_pass"] = bool(rec["all_checks_pass"] and rec["points"]["reconciles"])
    log(f"[S7] {cid} {spec['label']}: rows {rec['rows']['parsed']:,} vs capacity {rec['rows']['capacity']:,}; "
        f"empty {rec['rows']['empty_lineup_rows']}, malformed {rec['rows']['malformed_lineup_rows']}, unresolved {rec['lineups']['unresolved_entries']}; "
        f"ownership max |diff| {rec['ownership']['max_abs_diff_pp']} pp; all checks {'PASS' if rec['all_checks_pass'] else 'FAIL'}")
    emp = empirical(std, pool, detail)
    M = model_field(slate, pool, detail, spec["week"], spec["roster"], n_entries=len(std["entries"]), log=log)
    bins = by_bin(std, pool, M)
    win = winner(std, pool, M)
    log(f"[S7] {cid}: real {emp['distinct_lineups']:,} distinct, chalk {emp['max_lineup_share_pct']}% ({emp['max_lineup_copies']} copies), "
        f"structures {emp['structure_pct']}, salary left ${emp['salary_left']['mean']}; model chalk {M['summary']['max_lineup_share_pct']}%, "
        f"structures {M['summary']['structure_pct']}, salary left ${M['summary']['salary_left']['mean']}; "
        f"outside the model's universe {bins['outside_share_pct']}%; winner admitted {win['admitted_by_our_rules']}")
    return {"label": spec["label"], "captured": spec["captured"], "week": spec["week"],
            "contest": {k: detail.get(k) for k in ("id", "name", "entry_fee", "max_entries", "prize_pool", "first_prize", "places_paid", "draft_group_id", "starts")},
            "standings_file": {"file": os.path.basename(path), **{k: v for k, v in man["files"][os.path.basename(path)].items()
                                                                 if k in ("csv_sha256", "gz_sha256", "rows_excluding_header", "csv_mtime_in_zip", "note")}},
            "inputs": {"draftkings": s6_capture.dk_hashes(spec["dg"]),
                       "roster": {**sd_board.roster_stamp(FEEDS, week=spec["week"], name=spec["roster"]),
                                  "injury_statuses": M["roster_stamp"]["injury_statuses"]}},
            "reconciliation": rec, "empirical": emp, "field_rules": field_rules(std, pool),
            "model": {**M["summary"], "field_only_players": M["field_only"], "universe_players": M["name_of"]},
            "observed_vs_model": bins, "winner": win,
            "pool_players_without_projection": sorted(set(pool) - set(M["name_of"]))}


def summary_row(cid, r):
    e, m, b = r["empirical"], r["model"], r["observed_vs_model"]
    return {"contest": cid, "label": r["label"], "entries": r["reconciliation"]["rows"]["parsed"],
            "capacity": r["reconciliation"]["rows"]["capacity"], "checks_pass": r["reconciliation"]["all_checks_pass"],
            "distinct_lineups": e["distinct_lineups"],
            "chalk_share_pct": {"real": e["max_lineup_share_pct"], "model": m["max_lineup_share_pct"]},
            "effective_lineups": {"real": e["effective_lineups"]["inverse_sum_p2"], "model": m["effective_lineups"]["inverse_sum_p2"]},
            "outside_universe_pct": b["outside_share_pct"],
            "outside_if_gate_lifted_pct": b["outside_if_gate_lifted_pct"],
            "cpt_qb_pct": {"real": e["cpt_position_mix_pct"].get("QB", 0.0), "model": m["cpt_position_mix_pct"].get("QB", 0.0)},
            "cpt_rb_pct": {"real": e["cpt_position_mix_pct"].get("RB", 0.0), "model": m["cpt_position_mix_pct"].get("RB", 0.0)},
            "five_one_pct": {"real": e["structure_pct"].get("5-1", 0.0), "model": m["structure_pct"].get("5-1", 0.0)},
            "no_k_dst_pct": {"real": e["k_dst_slots_pct"].get("0", 0.0), "model": m["k_dst_slots_pct"].get("0", 0.0)},
            "salary_left_mean": {"real": e["salary_left"]["mean"], "model": m["salary_left"]["mean"]},
            "top_band_observed_over_model": {f"{x['log10_p_from']}..{x['log10_p_to']}": x["observed_over_model"] for x in b["bins"] if x["log10_p_from"] >= -4},
            "real_chalk_model_rank": b["chalk"]["most_duplicated_real_lineup"]["model_rank"],
            "model_chalk_observed_copies": b["chalk"]["model_most_probable_lineup"]["observed_copies"],
            "rank_corr_obs_vs_model": b["rank_correlation_observed_vs_model_over_observed_lineups"],
            "field_failing_any_of_four_rules_pct": r["field_rules"]["field"]["failing_any_pct_of_active"],
            "top_01pct_failing_pct": r["field_rules"]["top_01pct_by_rank"]["failing_any_pct"],
            "winner_duplicate_rank": r["field_rules"]["winner_duplicate_rank"]["rank_among_unique_lineups"],
            "winner": {"points": r["winner"]["points"], "tied": r["winner"]["tied_entries_at_top"], "copies": r["winner"]["exact_lineup_copies"],
                       "model_rank": r["winner"].get("model_rank_of_lineup"), "admitted": r["winner"]["admitted_by_our_rules"],
                       "rules_failed": r["winner"].get("rules_failed")}}


def run(log=print):
    from research import provenance, s6_capture
    man = json.load(open(os.path.join(STANDINGS, "manifest.json")))
    results = {str(cid): one(cid, spec, man, log) for cid, spec in CONTESTS.items()}
    out = {"meta": {"stage": "S7 first artifact: the Showdown field as it actually entered, fit-free, three contests",
                    "contests": {str(cid): spec["label"] for cid, spec in CONTESTS.items()},
                    "inputs": {"sleeper_feeds": s6_capture.sleeper_hashes(FEEDS)},
                    "caveats": ["captures are per contest (see each contest's `captured`): only DEN @ KC has a pre-lock pool "
                                "and feed capture; NE @ SEA and DET @ BUF pools were captured after their games, and Sleeper's "
                                "projections are not versioned, so their projection columns are not proven equal to what was "
                                "served at lock",
                                "every roster capture is post-game; for the model universe injury statuses were CLEARED and "
                                "the depth-chart rank rule kept (DJ Moore: 43% owned in DET @ BUF, listed Out post-game)",
                                "nothing here is fitted; beta is the placeholder solved for FIELD_TOP_SHARE = 0.002 on each board",
                                "the expected-duplicate column is drawn for the entries that exist, not the capacity, where a "
                                "contest did not fill (NE @ SEA: 126,020 of 132,352)"],
                    "fit_free": True,
                    "provenance": provenance.stamp(worlds=None, model="placeholder softmax field, unfitted", seed=SEED)},
           "summary": [summary_row(int(cid), r) for cid, r in results.items()],
           "contests": results}
    with open(os.path.join(DATA, "s7_field.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    run()
    print("S7 field artifact written")
