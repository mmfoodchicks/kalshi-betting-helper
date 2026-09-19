"""Task 8: the classic Millionaire field as it actually entered, FIT-FREE.

The classic field model (dfs_tourney.classic_sample / calibrate_field) is a
sampler with six hand-set constants: the most-owned player's share
(CL_FIELD_MAX_OWN 0.38), the mean salary spent (CL_SALARY_USED $49,400), the
stack-size distribution (CL_STACK_DIST, Levitan's Milly Maker trends), the
bring-back rate (CL_BRING_BACK 0.35, "assumed, not published"), the share
that plays a defense against its own quarterback (CL_DST_VS_OWN_QB 0.08),
and the collision rate it reports (CL_FIELD_COLLISION 1.2e-7, "reported, not
fitted"). Every one of them is a statement about the public that the
week-1 $3.5M Millionaire's full standings can check directly, with the
model's own definitions:

  stack     the number of WR/TE teammates of the lineup's quarterback (a
            back does not count, as in the sampler)
  bring-back a WR or TE from the quarterback's opponent, any stack
  DST vs own QB  the defense's opponent is the quarterback's team
  max own   the most-rostered player's share of lineups, any slot (the
            sampler's own.max() counts a player wherever he sits; DraftKings'
            %Drafted column is per roster position, so Gibbs reads 38.58% at
            RB in the export and 42.75% across RB and FLEX)
  collision sum over lineups of (copies / entries)^2, the probability two
            random entries hold the same lineup; 1 / that is the effective
            number of lineups

Inputs: the owner's export (167 MB, uncommitted; pinned by sha256 in
research/data/dk_standings/manifest.json and verified here before anything is
read), the DraftKings draft group 151307 captured after the game (salaries,
teams and games do not change after lock; research/data/dk_classic), and
nothing from Sleeper: this artifact fits nothing and needs no projection.

Reconciliation first (rows against capacity, every lineup 1 QB + 2 RB + 3 WR
+ 1 TE + 1 FLEX + 1 DST resolved to the pool, ownership per roster position
against DraftKings' column, every entry's points recomputed from the FPTS
table), then the raw table, then each constant beside its measured value.

    python3 -m research.cl_field <path to contest-standings-193028206.csv>
"""
import collections
import csv
import hashlib
import json
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
CAPDIR = os.path.join(DATA, "dk_classic")
CONTEST = 193028206
DG = 151307
CAP = 50000
SLOTS = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST")
SLOT_NEED = collections.Counter({"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "DST": 1})
DUP_BUCKETS = ((1, 1), (2, 2), (3, 5), (6, 10), (11, 50), (51, 10 ** 9))
_SPLIT = re.compile(r"\s(?=(?:QB|RB|WR|TE|FLEX|DST)\s)")


def _norm(s):
    return " ".join(str(s or "").split())


def parse_lineup(text):
    """'DST Steelers FLEX A QB B RB C RB D TE E WR F WR G WR H' -> [(slot, name)] * 9
    in export order, or None for a blank; raises on any other shape."""
    t = _norm(text)
    if not t:
        return None
    parts = [p.strip() for p in _SPLIT.split(" " + t) if p.strip()]
    out = []
    for p in parts:
        s, _, n = p.partition(" ")
        out.append((s, _norm(n)))
    if collections.Counter(s for s, _ in out) != SLOT_NEED or any(not n for _, n in out):
        raise ValueError(f"lineup does not resolve to QB/2RB/3WR/TE/FLEX/DST: {text!r}")
    return out


def load_standings(path, expect_sha):
    raw = open(path, "rb").read()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != expect_sha:
        raise SystemExit(f"{path}: sha256 {sha} is not the pinned {expect_sha}; refusing to read an unpinned export")
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    header, body = rows[0], rows[1:]
    assert header[:6] == ["Rank", "EntryId", "EntryName", "TimeRemaining", "Points", "Lineup"], header
    entries, own, fpts, malformed = [], {}, {}, 0
    for r in body:
        m = re.search(r"\((\d+)/(\d+)\)\s*$", r[2])
        try:
            lu = parse_lineup(r[5])
        except ValueError:
            malformed += 1
            lu = None
        entries.append({"rank": int(r[0]), "entry_id": r[1], "user": (r[2][:m.start()].strip() if m else r[2].strip()),
                        "user_entries": (int(m.group(2)) if m else 1), "points": float(r[4] or 0.0), "lineup": lu, "blank": not _norm(r[5])})
        if len(r) > 10 and r[7].strip():
            own[(_norm(r[7]), r[8].strip().upper())] = float(r[9].rstrip("%")) / 100.0
            fpts[_norm(r[7])] = float(r[10] or 0.0)
    return {"sha256": sha, "bytes": len(raw), "entries": entries, "dk_ownership": own, "fpts": fpts, "malformed": malformed}


def pool_rows(slate):
    """{name: {pos, team, opp, salary, game}} for every playable pool row."""
    import simulate
    out = {}
    for c in simulate.parse_dk_csv(slate["csv"]):
        pos = (c.get("pos") or "").upper().split("/")[0]
        pos = "RB" if pos == "FB" else pos
        g = _norm(c.get("game"))
        away, _, home = g.partition(" @ ")
        team = (c.get("team") or "").strip()
        opp = home if team == away else away if team == home else ""
        out[_norm(c["name"])] = {"pos": pos, "team": team, "opp": opp, "salary": int(c["salary"]), "game": g}
    return out


def reconcile(std, pool, detail):
    E = std["entries"]
    n = len(E)
    blank = sum(1 for e in E if e["blank"])
    unresolved = collections.Counter()
    resolved = 0
    for e in E:
        if not e["lineup"]:
            continue
        miss = [nm for _, nm in e["lineup"] if nm not in pool]
        if miss:
            for nm in miss:
                unresolved[nm] += 1
        else:
            resolved += 1
    # ownership per (player, roster position), DraftKings' own denominator (all rows)
    slot_cnt = collections.Counter()
    for e in E:
        if e["lineup"]:
            for s, nm in e["lineup"]:
                slot_cnt[(nm, s)] += 1
    diffs = [{"name": nm, "slot": s, "dk_pct": round(100 * pct, 2), "ours_pct": round(100 * slot_cnt[(nm, s)] / n, 2),
              "diff_pp": round(100 * (slot_cnt[(nm, s)] / n - pct), 3)} for (nm, s), pct in std["dk_ownership"].items()]
    worst = max(diffs, key=lambda d: abs(d["diff_pp"]))
    # points from the FPTS table, every active entry
    fp = std["fpts"]
    maxd, checked = 0.0, 0
    for e in E:
        if e["lineup"] and all(nm in fp for _, nm in e["lineup"]):
            maxd = max(maxd, abs(sum(fp[nm] for _, nm in e["lineup"]) - e["points"])); checked += 1
    C = int(detail.get("max_entries") or 0)
    return {"rows": {"parsed": n, "capacity": C, "reconciles": n == C, "distinct_entry_ids": len({e["entry_id"] for e in E}),
                     "blank_lineup_rows": blank, "malformed_lineup_rows": std["malformed"], "with_lineup": n - blank - std["malformed"]},
            "lineups": {"resolved_to_pool": resolved, "unresolved_entries": sum(1 for e in E if e["lineup"] and any(nm not in pool for _, nm in e["lineup"])),
                        "unresolved_names": dict(unresolved.most_common(10)), "pool_size": len(pool)},
            "ownership": {"dk_rows": len(diffs), "max_abs_diff_pp": round(max(abs(d["diff_pp"]) for d in diffs), 3), "worst": worst,
                          "reconciles_within_rounding": max(abs(d["diff_pp"]) for d in diffs) <= 0.01,
                          "note": "DraftKings' %Drafted is per ROSTER POSITION (a back at RB and the same back at FLEX are two rows); recomputed per slot over all entries"},
            "points": {"entries_checked": checked, "max_abs_diff_points": round(maxd, 6), "reconciles": checked > 0 and maxd < 0.001},
            "all_checks_pass": bool(n == C and std["malformed"] == 0 and not unresolved and max(abs(d["diff_pp"]) for d in diffs) <= 0.01
                                    and checked > 0 and maxd < 0.001)}


def _wq(values, qs):
    return [float(np.percentile(values, q)) for q in qs]


def empirical(std, pool):
    import dfs_tourney as T
    E = [e for e in std["entries"] if e["lineup"] and all(nm in pool for _, nm in e["lineup"])]
    n_all = len(std["entries"])
    N = len(E)
    lc = collections.Counter(tuple(sorted(nm for _, nm in e["lineup"])) for e in E)
    counts = np.asarray(sorted(lc.values(), reverse=True), dtype=np.float64)
    p_act = counts / N
    collision = float((p_act ** 2).sum())
    dup = {}
    for lo, hi in DUP_BUCKETS:
        m = (counts >= lo) & (counts <= hi)
        dup[f"{lo}-{hi if hi < 10 ** 9 else 'plus'}"] = {"lineups": int(m.sum()), "entries": int(counts[m].sum()),
                                                        "entry_share_pct": round(100 * counts[m].sum() / N, 2)}
    own_any = collections.Counter(); own_slot = collections.Counter()
    stacks = collections.Counter(); stacks_rb = collections.Counter(); bring = 0; bring_stacked = 0; stacked = 0
    dst_vs_qb = 0; dst_vs_rb = 0; same_team_max = collections.Counter(); same_game_max = collections.Counter()
    sal_used = []; punts = collections.Counter(); flex_pos = collections.Counter(); qb_game_count = collections.Counter()
    users = collections.Counter(e["user_entries"] for e in std["entries"])
    for e in E:
        lu = e["lineup"]
        names = [nm for _, nm in lu]
        rows = [pool[nm] for nm in names]
        qb = next(pool[nm] for s, nm in lu if s == "QB")
        dst = next(pool[nm] for s, nm in lu if s == "DST")
        for nm in names:
            own_any[nm] += 1
        for s, nm in lu:
            own_slot[(nm, s)] += 1
        pc = [r for s, r in zip([s for s, _ in lu], rows) if s != "QB" and r["pos"] in ("WR", "TE")]
        k = sum(1 for r in pc if r["team"] == qb["team"])
        stacks[min(k, 3)] += 1
        k_rb = k + sum(1 for r in rows if r["pos"] == "RB" and r["team"] == qb["team"])
        stacks_rb[min(k_rb, 3)] += 1
        bb = any(r["team"] == qb["opp"] for r in pc)
        bring += bb
        if k >= 1:
            stacked += 1; bring_stacked += bb
        dst_vs_qb += (dst["opp"] == qb["team"])
        dst_vs_rb += any(r["pos"] == "RB" and dst["opp"] == r["team"] for r in rows)
        tc = collections.Counter(r["team"] for r in rows if r["pos"] != "DST")
        same_team_max[max(tc.values())] += 1
        gc = collections.Counter(r["game"] for r in rows)
        same_game_max[max(gc.values())] += 1
        qb_game_count[gc[qb["game"]]] += 1
        sal_used.append(sum(r["salary"] for r in rows))
        punts[sum(1 for r in rows if r["salary"] < 3000)] += 1
        flex_pos[next(pool[nm]["pos"] for s, nm in lu if s == "FLEX")] += 1
    sal = np.asarray(sal_used, dtype=np.float64)
    top_any = own_any.most_common(1)[0]
    return {"active_entries": N, "distinct_lineups": len(lc), "max_lineup_copies": int(counts[0]),
            "max_lineup_share_pct": round(100 * float(p_act[0]), 4),
            "top_lineups": [{"copies": int(v), "lineup": list(k)} for k, v in lc.most_common(3)],
            "collision": {"sum_p2": collision, "effective_lineups": round(1.0 / collision, 1),
                          "model_reported": T.CL_FIELD_COLLISION, "ratio_real_over_model": round(collision / T.CL_FIELD_COLLISION, 2)},
            "duplicates": dup,
            "max_ownership": {"player": top_any[0], "any_slot_pct": round(100 * top_any[1] / N, 2),
                              "any_slot_pct_of_all_rows": round(100 * top_any[1] / n_all, 2),
                              "by_slot_pct": {s: round(100 * own_slot[(top_any[0], s)] / n_all, 2) for s in ("RB", "FLEX", "WR", "TE", "QB", "DST") if own_slot[(top_any[0], s)]},
                              "model_target": T.CL_FIELD_MAX_OWN},
            "ownership_top": [{"name": nm, "pos": pool[nm]["pos"], "team": pool[nm]["team"], "any_slot_pct": round(100 * v / N, 2)} for nm, v in own_any.most_common(20)],
            "players_used": len(own_any), "pool_players": len(pool),
            "stack_pct": {"definition": "WR/TE teammates of the quarterback, a back not counted (the sampler's definition)",
                          "0_1_2_3plus": [round(100 * stacks[k] / N, 2) for k in (0, 1, 2, 3)], "model": [round(100 * x, 1) for x in T.CL_STACK_DIST],
                          "with_backs_counted": [round(100 * stacks_rb[k] / N, 2) for k in (0, 1, 2, 3)]},
            "bring_back_pct": {"definition": "a WR or TE from the quarterback's opponent", "all_entries": round(100 * bring / N, 2),
                               "among_stacked": round(100 * bring_stacked / max(1, stacked), 2), "model": round(100 * T.CL_BRING_BACK, 1)},
            "dst_vs_own_qb_pct": {"definition": "the defense's opponent is the quarterback's team", "real": round(100 * dst_vs_qb / N, 3), "model": round(100 * T.CL_DST_VS_OWN_QB, 1)},
            "dst_vs_own_rb_pct": round(100 * dst_vs_rb / N, 3),
            "salary_used": {"mean": round(float(sal.mean()), 1), "median": float(np.median(sal)), "p10_p25_p75_p90": _wq(sal, (10, 25, 75, 90)),
                            "share_at_cap_pct": round(100 * float((sal == CAP).mean()), 2), "share_ge_49500_pct": round(100 * float((sal >= 49500).mean()), 2),
                            "share_le_48000_pct": round(100 * float((sal <= 48000).mean()), 2), "model": T.CL_SALARY_USED},
            "players_from_one_team_max_pct": {str(k): round(100 * v / N, 2) for k, v in sorted(same_team_max.items())},
            "players_from_one_game_max_pct": {str(k): round(100 * v / N, 2) for k, v in sorted(same_game_max.items())},
            "players_from_qb_game_pct": {str(k): round(100 * v / N, 2) for k, v in sorted(qb_game_count.items())},
            "punts_under_3000_pct": {str(k): round(100 * v / N, 2) for k, v in sorted(punts.items())},
            "flex_position_pct": {k: round(100 * v / N, 2) for k, v in flex_pos.most_common()},
            "user_max_entry_brackets": {str(k): {"entries": v, "share_pct": round(100 * v / n_all, 2)} for k, v in sorted(users.items(), key=lambda kv: -kv[1])[:8]},
            "share_from_150_max_users_pct": round(100 * users.get(150, 0) / n_all, 2)}


def constants_table(emp):
    import dfs_tourney as T
    return [{"constant": "CL_FIELD_MAX_OWN", "model": T.CL_FIELD_MAX_OWN, "real": round(emp["max_ownership"]["any_slot_pct"] / 100, 4),
             "reads": f"{emp['max_ownership']['player']}, any slot; DraftKings' per-position column shows {emp['max_ownership']['by_slot_pct']}"},
            {"constant": "CL_SALARY_USED", "model": T.CL_SALARY_USED, "real": emp["salary_used"]["mean"],
             "reads": f"median ${emp['salary_used']['median']:,.0f}; {emp['salary_used']['share_at_cap_pct']}% spend the full $50,000"},
            {"constant": "CL_STACK_DIST", "model": list(T.CL_STACK_DIST), "real": [round(x / 100, 4) for x in emp["stack_pct"]["0_1_2_3plus"]],
             "reads": "naked QB / one / two / three-plus WR-TE teammates"},
            {"constant": "CL_BRING_BACK", "model": T.CL_BRING_BACK, "real": round(emp["bring_back_pct"]["all_entries"] / 100, 4),
             "reads": f"any entry; among stacked entries {emp['bring_back_pct']['among_stacked']}%"},
            {"constant": "CL_DST_VS_OWN_QB", "model": T.CL_DST_VS_OWN_QB, "real": round(emp["dst_vs_own_qb_pct"]["real"] / 100, 5),
             "reads": "share of the field whose defense faces its own quarterback"},
            {"constant": "CL_FIELD_COLLISION", "model": T.CL_FIELD_COLLISION, "real": emp["collision"]["sum_p2"],
             "reads": f"effective lineups {emp['collision']['effective_lineups']:,.0f}; the most-copied lineup has {emp['max_lineup_copies']} copies"}]


def run(path, log=print):
    from research import provenance, s6_capture
    man = json.load(open(os.path.join(DATA, "dk_standings", "manifest.json")))["files"][f"contest-standings-{CONTEST}.csv.gz"]
    std = load_standings(path, man["csv_sha256"])
    slate, details = s6_capture.replay(DG, capdir=CAPDIR)
    detail = details[str(CONTEST)]
    pool = pool_rows(slate)
    rec = reconcile(std, pool, detail)
    log(f"[CL] rows {rec['rows']['parsed']:,} vs capacity {rec['rows']['capacity']:,}; blank {rec['rows']['blank_lineup_rows']}, malformed {rec['rows']['malformed_lineup_rows']}, "
        f"unresolved {rec['lineups']['unresolved_entries']} {rec['lineups']['unresolved_names']}; ownership max |diff| {rec['ownership']['max_abs_diff_pp']} pp; "
        f"points max |diff| {rec['points']['max_abs_diff_points']}; all checks {'PASS' if rec['all_checks_pass'] else 'FAIL'}")
    emp = empirical(std, pool)
    table = constants_table(emp)
    for row in table:
        log(f"[CL] {row['constant']}: model {row['model']} real {row['real']} ({row['reads']})")
    out = {"meta": {"stage": "Task 8: the classic week-1 $3.5M Millionaire field as it entered, fit-free, each placeholder constant beside its measured value",
                    "contest": {k: detail.get(k) for k in ("id", "name", "entry_fee", "max_entries", "prize_pool", "first_prize", "places_paid", "draft_group_id", "starts")},
                    "standings_file": {"file": os.path.basename(path), "sha256": std["sha256"], "bytes": std["bytes"], "pinned_in": "research/data/dk_standings/manifest.json",
                                       "committed": False, "why": man.get("why_not_committed")},
                    "inputs": {"draftkings": s6_capture.dk_hashes(DG, capdir=CAPDIR),
                               "captured": "draft group 151307 captured after the game (2026-09-19); salaries, teams and games do not change after lock; no Sleeper input is used"},
                    "fit_free": True,
                    "provenance": provenance.stamp(worlds=None, model="none: raw standings against the classic field model's hand-set constants", seed="UNSEEDED")},
           "reconciliation": rec, "empirical": emp, "constants": table}
    with open(os.path.join(DATA, "cl_field.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python3 -m research.cl_field <path to contest-standings-193028206.csv>")
    run(sys.argv[1])
    print("classic field artifact written")
