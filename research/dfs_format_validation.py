"""Does Vigil do better on smaller slates? The first real-results read
(backlog 2.1, 2026-11-26).

The owner's working hypothesis: Vigil may perform better on smaller slates,
because a showdown's legal lineups can be enumerated exhaustively while a
classic slate's must be searched. This script grades every tournament board
exported to the data branch (origin/sim-history errors/tourney-<dg>.json, the
last board built before each lock) on the REAL DraftKings points of its slate,
and splits the result by format.

Per board, on the real points (Sleeper's box scores through dfslog's own
DraftKings scorer, `dfslog.actuals_nfl`):
- best: the best lineup that could have been entered. Showdown: EXACT, by
  enumeration (every captain x every five-man flex under the cap, over the
  players who scored plus the cheapest zero-scorers a cap may force in).
  Classic: the lineup sheet's optimizer (`dfs_hindsight.best_lineup`, the
  sheet's rules -- no tight end at FLEX, no defense facing your own men -- so
  not necessarily DraftKings' unrestricted optimum).
- vigil: each list's lineups (top_win, top_money, top_top1; the first three
  of each, as `dfs_hindsight` logs them), real points and % of best.
- baseline: the projection-optimal lineup on the board's OWN projections
  (the same pool and projections Vigil used, optimized for mean points alone)
  -- the naive alternative Vigil's tournament objectives have to beat.
- finish: where a lineup would have placed in the real field, for the
  contests whose DraftKings standings were summarized (research/data/dk_fields).

What this can and cannot say: % of best and "beat the projection lineup" are
real-points measures, but a tournament lineup is built to finish first, not to
average well, so a lower mean can be the right choice. The decisive measure,
finish in the real field, exists for three contests. The by-format split says
how far the evidence goes and where it is thin; it does not settle the
hypothesis.

    python3 research/dfs_format_validation.py BOARDS_DIR [OUT_JSON [FIELDS_DIR]]

BOARDS_DIR holds the tourney-<dg>.json exports; FIELDS_DIR, optionally, the
data branch's dkfield-<cid>.json summaries (the PC's standings intake,
exported since 2026-11-26) beside the committed research/data/dk_fields.
"""
import itertools
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("VIGIL_NO_BG", "1")

import dfs_hindsight  # noqa: E402
import dfslog  # noqa: E402
import dk  # noqa: E402
import nfl_dfs  # noqa: E402

CAP = 50000
LISTS = dfs_hindsight.TOURNEY_LISTS
TOP = dfs_hindsight.TOURNEY_TOP


def fmt_bucket(kind, n_games):
    if kind == "showdown":
        return "showdown"
    n = int(n_games or 0)
    return ("classic 2" if n == 2 else "classic 3" if n == 3 else "classic 4-6" if 4 <= n <= 6
            else "classic 7-10" if 7 <= n <= 10 else "classic 11+" if n >= 11 else "classic ?")


def _pts(actuals, row):
    return dfslog._lookup("nfl", actuals, row)


def lineup_points(actuals, lineup):
    """Real DraftKings points of a board lineup (the captain at 1.5x), and the
    names the feed did not know (None when any is unknown)."""
    tot, unknown = 0.0, []
    for p in lineup:
        v = _pts(actuals, {"name": p.get("name"), "pos": p.get("pos"), "team": p.get("team")})
        if v is None:
            unknown.append(p.get("name"))
            continue
        tot += v * (1.5 if p.get("slot") == "CPT" else 1.0)
    return (round(tot, 2) if not unknown else None), unknown


def showdown_best(pool, actuals, value_key="real"):
    """The exact best showdown lineup by enumeration. `pool` rows carry name,
    pos, team, salary, cpt_salary and `value_key`; a showdown lineup must hold
    both teams (DraftKings' rule)."""
    rows = [dict(r) for r in pool if r.get("salary")]
    scored = [r for r in rows if (r.get(value_key) or 0) > 0]
    zero = sorted((r for r in rows if not (r.get(value_key) or 0) > 0), key=lambda r: r["salary"])[:6]
    cand = scored + zero
    best = (-1e9, None)
    for i, cpt in enumerate(cand):
        budget = CAP - cpt["cpt_salary"]
        if budget < 0:
            continue
        rest = [r for j, r in enumerate(cand) if j != i and r["salary"] <= budget]
        rest.sort(key=lambda r: -(r.get(value_key) or 0))
        for combo in itertools.combinations(rest, 5):
            sal = sum(r["salary"] for r in combo)
            if sal > budget:
                continue
            teams = {cpt["team"]} | {r["team"] for r in combo}
            if len(teams) < 2:
                continue
            v = 1.5 * (cpt.get(value_key) or 0) + sum(r.get(value_key) or 0 for r in combo)
            if v > best[0]:
                best = (v, [dict(cpt, slot="CPT")] + [dict(r, slot="FLEX") for r in combo])
    return (round(best[0], 2), best[1]) if best[1] else (None, None)


def _sd_pool(dg_rows, value_of):
    out = []
    for r in dg_rows:
        pos = (r.get("position") or r.get("pos") or "").upper().split("/")[0]
        pos = "RB" if pos == "FB" else pos
        if pos not in nfl_dfs.SHOWDOWN_POS:
            continue
        sal = int(float(r.get("salary") or 0))
        out.append({"name": r.get("name"), "pos": pos, "team": r.get("team"), "salary": sal,
                    "cpt_salary": int(r.get("cpt_salary") or round(sal * 1.5)), "real": value_of(r)})
    return out


def grade_board(b, actuals, dg_rows, fields):
    kind = b.get("kind")
    sl = b.get("slate") or {}
    n_games = 1 if kind == "showdown" else sl.get("games")
    out = {"dg": b.get("draft_group_id"), "kind": kind, "n_games": n_games, "bucket": fmt_bucket(kind, n_games),
           "week": sl.get("week"), "engine": b.get("engine"), "built_ts": b.get("built_ts")}
    # best on the real points
    if kind == "showdown":
        pool = _sd_pool(dg_rows, lambda r: _pts(actuals, {"name": r.get("name"),
                                                         "pos": (r.get("position") or "").upper().split("/")[0],
                                                         "team": r.get("team")}) or 0.0)
        best, best_lu = showdown_best(pool, actuals)
    else:
        players, unknown = dfs_hindsight.real_pool(dg_rows, actuals)
        best, best_lu, _secs = dfs_hindsight.best_lineup(players)
        out["n_unknown_pool"] = len(unknown)
    out["best"] = best
    # the baseline: projection-optimal on the board's own projections, graded on real points
    projs = {p.get("name"): float(p.get("proj") or 0) for p in (b.get("players") or [])}
    if kind == "showdown":
        pool_p = [dict(r, real=projs.get(r["name"], 0.0)) for r in
                  _sd_pool(dg_rows, lambda r: 0.0) if r["name"] in projs]
        _v, base_lu = showdown_best(pool_p, actuals)
    else:
        rows = [r for r in dg_rows if r.get("name") in projs]
        ppl, _u = dfs_hindsight.real_pool(rows, {})
        for p in ppl:
            p["proj"] = p["ceiling"] = p["floor"] = projs.get(p["name"], 0.0)
        base_lu = nfl_dfs.optimize(ppl, nfl_dfs.CAP, "projection") if ppl else None
    if base_lu:
        bp, bu = lineup_points(actuals, [{"name": p["name"], "pos": p["pos"], "team": p["team"],
                                          "slot": p.get("slot")} for p in base_lu])
        out["baseline"] = {"actual": bp, "unknown": bu, "proj": round(sum(
            projs.get(p["name"], 0) * (1.5 if p.get("slot") == "CPT" else 1) for p in base_lu), 2)}
    # Vigil's lists, every contest, one row per distinct roster per list
    seen, ours = set(), []
    for contest, lst, rows in dfs_hindsight.board_rows(b):
        for rank, r in enumerate(rows[:TOP]):
            lu = r.get("lineup") or []
            key = (lst, frozenset((p.get("name"), p.get("slot") == "CPT") for p in lu))
            if key in seen:
                continue
            seen.add(key)
            pts, unk = lineup_points(actuals, lu)
            row = {"list": lst, "rank": rank + 1, "actual": pts, "proj": r.get("proj"), "unknown": unk,
                   "contest_id": contest.get("id")}
            if pts is not None and best:
                row["pct_of_best"] = round(100.0 * pts / best, 1)
            f = next((f for f in fields if int(f.get("draft_group_id") or 0) == int(b.get("draft_group_id") or -1)), None)
            if f and pts is not None:
                row["finish"] = dfs_hindsight.finish(pts, f)
            ours.append(row)
    out["ours"] = ours
    if out.get("baseline") and out["baseline"]["actual"] is not None and best:
        out["baseline"]["pct_of_best"] = round(100.0 * out["baseline"]["actual"] / best, 1)
    return out


def summarize(graded):
    """By format: boards, Vigil's first lineup per list and the best of its
    top three against the projection baseline on the same slate (paired by
    board), with standard errors across boards."""
    def mse(xs):
        xs = [x for x in xs if x is not None]
        n = len(xs)
        if not n:
            return {"n": 0}
        m = sum(xs) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else None
        return {"n": n, "mean": round(m, 2), "se": round(sd / math.sqrt(n), 2) if sd is not None else None}
    out = {}
    for g in graded:
        out.setdefault(g["bucket"], []).append(g)
    res = {}
    for bucket, gs in sorted(out.items()):
        row = {"boards": len(gs), "weeks": sorted({g["week"] for g in gs})}
        base = [g.get("baseline", {}).get("pct_of_best") for g in gs]
        row["baseline_pct_of_best"] = mse(base)
        for lst in LISTS:
            first = []
            diff = []
            for g in gs:
                r1 = next((r for r in g["ours"] if r["list"] == lst and r["rank"] == 1 and r.get("pct_of_best") is not None), None)
                if r1:
                    first.append(r1["pct_of_best"])
                    bp = g.get("baseline", {}).get("pct_of_best")
                    if bp is not None:
                        diff.append(r1["pct_of_best"] - bp)
            row[lst] = {"first_pct_of_best": mse(first), "first_minus_baseline_pts": mse(diff),
                        "first_beat_baseline": (sum(1 for d in diff if d > 0), len(diff))}
        fin = [r["finish"] for g in gs for r in g["ours"] if r.get("finish")]
        if fin:
            row["real_field"] = {"lineups": len(fin), "contests": len({g["dg"] for g in gs if any(r.get("finish") for r in g["ours"])}),
                                 "cashed": sum(1 for f in fin if f.get("cashed")),
                                 "best_top_pct": min(f["top_pct"] for f in fin),
                                 "median_top_pct": sorted(f["top_pct"] for f in fin)[len(fin) // 2]}
        res[bucket] = row
    return res


def load_fields(extra_dir=None):
    """Every real-field summary: the committed ones, then the data branch's
    (one per contest; the committed copy wins)."""
    fields, seen = [], set()
    dirs = [(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dk_fields"), "dk_field_")]
    if extra_dir:
        dirs.append((extra_dir, "dkfield-"))
    for fdir, prefix in dirs:
        if not os.path.isdir(fdir):
            continue
        for fn in sorted(os.listdir(fdir)):
            if fn.startswith(prefix) and fn.endswith(".json"):
                f = json.load(open(os.path.join(fdir, fn)))
                cid = f.get("contest_id")
                if cid in seen:
                    continue
                seen.add(cid)
                f["draft_group_id"] = f.get("draft_group_id") or (f.get("contest") or {}).get("draft_group_id")
                fields.append(f)
    return fields


def main(boards_dir, out_path=None, now=None, fields_dir=None):
    now = now or time.time()
    fields = load_fields(fields_dir)
    graded, skipped, actual_cache = [], [], {}
    for fn in sorted(os.listdir(boards_dir)):
        if not (fn.startswith("tourney-") and fn.endswith(".json")) or "-pick" in fn:
            continue
        b = json.load(open(os.path.join(boards_dir, fn)))
        starts = None
        cons = [b.get("contest")] if b.get("kind") == "showdown" else (b.get("contests") or [])
        for c in cons:
            t = nfl_dfs.iso_ts((c or {}).get("starts"))
            starts = t if starts is None or (t and t < starts) else starts
        week = (b.get("slate") or {}).get("week")
        if not starts or not week or starts > now - 2 * 86400:
            skipped.append((fn, "not settled (contest starts within two days or unknown)"))
            continue
        season = time.gmtime(starts).tm_year if time.gmtime(starts).tm_mon >= 3 else time.gmtime(starts).tm_year - 1
        key = f"nfl:{season}:w{int(week)}"
        if key not in actual_cache:
            actual_cache[key] = dfslog.actuals_nfl(key) or {}
        actuals = actual_cache[key]
        if not actuals:
            skipped.append((fn, "no Sleeper actuals"))
            continue
        rows = dk.players(int(b["draft_group_id"])) or []
        if not rows:
            skipped.append((fn, "DraftKings pool unavailable"))
            continue
        graded.append(grade_board(b, actuals, rows, fields))
        print(fn, graded[-1]["bucket"], "best", graded[-1]["best"], flush=True)
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now)), "boards": graded,
           "skipped": skipped, "by_format": summarize(graded)}
    if out_path:
        json.dump(out, open(out_path, "w"), indent=1)
    return out


if __name__ == "__main__":
    r = main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None,
             fields_dir=sys.argv[3] if len(sys.argv) > 3 else None)
    print(json.dumps(r["by_format"], indent=1))
    print("skipped:", r["skipped"])
