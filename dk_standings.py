"""DraftKings contest standings, read on the owner's PC: the field that
actually entered, beside the one the tournament board models.

DraftKings lets an entrant download a contest's full standings once it
locks: every entry's lineup and score, plus each player's %Drafted and
points. That export is the only record of what the public really played, and
it is how the classic field model's constants were checked once
(research/cl_field.py, the week-1 Millionaire: CL_FIELD_COLLISION was ~70x
too low). This makes that check routine. The owner drops the export (the
.zip DraftKings serves, or the .csv inside it) into the PC's drop folder
(pc-standings/, gitignored, beside pc-simcache/); pc_worker reads each new
file once, boils it down to a compact summary here, and stores it in the deep
store as dk_field_<contest id>, where the cycle's deep sync uploads it. The
server never reads an export (they run to 170 MB) -- only the summaries, on
the DFS tab.

A summary is read under the classic sampler's own definitions
(research/cl_field.py's, kept identical so the two can be compared): a stack
is the quarterback's WR/TE teammates (a back does not count); a bring-back a
WR or TE from his opponent; the collision the distinct-entry statistic
(dfs_tourney._collision's), never the plug-in HHI, whose floor 1/N sits above
the constant. Showdown exports are read too (captain ownership, team splits).
Pure Python: the summary is small and the guard suite runs without numpy.

Nothing here changes a board. Refitting the field model on these is a
change with its own rule, written before the exports it reads
(research/field_refit.py).
"""
import collections
import csv
import hashlib
import io
import os
import re
import zipfile

import errlog

DROP_DIR = os.environ.get("VIGIL_STANDINGS_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "pc-standings")
KEY = "dk_field_{cid}"
INDEX = "dk_field_index"
BAD = "dk_field_bad"        # files that could not be read: reported once, not every cycle
CAP = 50000
CLASSIC_NEED = collections.Counter({"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "DST": 1})
SHOWDOWN_NEED = collections.Counter({"CPT": 1, "FLEX": 5})
_SPLIT = re.compile(r"\s(?=(?:QB|RB|WR|TE|FLEX|DST|CPT)\s)")
_HEADER = ["Rank", "EntryId", "EntryName", "TimeRemaining", "Points", "Lineup"]
_CID = re.compile(r"contest-standings-(\d+)")
TOP_OWN = 40


def _norm(s):
    return " ".join(str(s or "").split())


def parse_lineup(text):
    """'QB A RB B ...' or 'CPT A FLEX B ...' -> [(slot, name)], None for a
    blank; ValueError for any other shape."""
    t = _norm(text)
    if not t:
        return None
    out = []
    for p in (q.strip() for q in _SPLIT.split(" " + t)):
        if p:
            s, _, n = p.partition(" ")
            out.append((s, _norm(n)))
    c = collections.Counter(s for s, _ in out)
    if c not in (CLASSIC_NEED, SHOWDOWN_NEED) or any(not n for _, n in out):
        raise ValueError(f"lineup is neither classic nor showdown: {text[:80]!r}")
    return out


def read_text(path):
    """The export's CSV text from a .csv or DraftKings' .zip, and its sha256."""
    raw = open(path, "rb").read()
    sha = hashlib.sha256(raw).hexdigest()
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"{os.path.basename(path)}: expected one CSV inside, found {names}")
            raw_csv = z.read(names[0])
    else:
        raw_csv = raw
    return raw_csv.decode("utf-8-sig"), sha


def parse(text):
    """{"entries": [{rank, points, user, user_entries, lineup}], "dk_own":
    {(name, roster position): share}, "fpts": {name: points}, "malformed"}."""
    rows = list(csv.reader(text.splitlines()))
    if not rows or rows[0][:6] != _HEADER:
        raise ValueError(f"not a DraftKings standings export (header {rows[0][:6] if rows else None})")
    entries, own, fpts, bad = [], {}, {}, 0
    for r in rows[1:]:
        if len(r) < 6:
            continue
        m = re.search(r"\((\d+)/(\d+)\)\s*$", r[2])
        try:
            lu = parse_lineup(r[5])
        except ValueError:
            bad += 1
            lu = None
        try:
            rank, pts = int(r[0]), float(r[4] or 0.0)
        except ValueError:
            bad += 1
            continue
        entries.append({"rank": rank, "points": pts, "user": (r[2][:m.start()] if m else r[2]).strip(),
                        "user_entries": int(m.group(2)) if m else 1, "lineup": lu})
        if len(r) > 10 and r[7].strip():
            try:
                own[(_norm(r[7]), r[8].strip().upper())] = float(r[9].rstrip("%")) / 100.0
                fpts[_norm(r[7])] = float(r[10] or 0.0)
            except ValueError:
                bad += 1
    return {"entries": entries, "dk_own": own, "fpts": fpts, "malformed": bad}


def pool_from(players):
    """{name: {pos, team, opp, salary, game}} from dk.players rows."""
    out = {}
    for p in players or []:
        g = _norm(p.get("game")).replace("@", " @ ")
        g = _norm(g)
        away, _, home = g.partition(" @ ")
        team = (p.get("team") or "").strip()
        pos = (p.get("position") or "").upper().split("/")[0]
        out[_norm(p.get("name"))] = {"pos": "RB" if pos == "FB" else pos, "team": team,
                                     "opp": home if team == away else away if team == home else "",
                                     "salary": int(p.get("salary") or 0), "game": g}
    return out


def _q(sorted_vals, q):
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def summarize(parsed, pool=None, detail=None, cid=None, sha=None):
    """The compact field summary the server shows. `pool` (pool_from) adds the
    shape statistics; `detail` (dk.contest_detail) the contest's name, fee and
    the cash line."""
    E = parsed["entries"]
    n = len(E)
    with_lu = [e for e in E if e["lineup"]]
    kind = ("showdown" if with_lu and any(s == "CPT" for s, _ in with_lu[0]["lineup"]) else "classic")
    pts = sorted((e["points"] for e in E), reverse=True)
    # rank/points knots, geometric in rank, so any score can be placed in the
    # field afterwards (dfs_hindsight.finish): 1, 2, 5, 10, 20, 50, ... and
    # the last entry
    ranks, k = [], 1
    while k < len(pts):
        for m in (1, 2, 5):
            if m * k < len(pts):
                ranks.append(m * k)
        k *= 10
    ranks = sorted(set(ranks + ([len(pts)] if pts else [])))
    out = {"contest_id": cid, "sha256": sha, "kind": kind, "entries": n, "with_lineup": len(with_lu),
           "malformed": parsed["malformed"],
           "points": {"first": pts[0] if pts else None,
                      "top_0_1pct": _q(pts, 0.001), "top_1pct": _q(pts, 0.01), "top_10pct": _q(pts, 0.10),
                      "median": _q(pts, 0.5)},
           "rank_points": [[r, pts[r - 1]] for r in ranks]}
    if detail:
        out["contest"] = {k: detail.get(k) for k in ("name", "entry_fee", "prize_pool", "first_prize",
                                                       "places_paid", "max_entries", "draft_group_id", "starts")}
        paid = int(detail.get("places_paid") or 0)
        by_rank = sorted(E, key=lambda e: e["rank"])
        if paid and by_rank:
            out["points"]["cash_line"] = by_rank[min(paid, len(by_rank)) - 1]["points"]
    # ownership, any slot, over the entries with a lineup (DraftKings' column
    # is per roster position over every row; both kept)
    any_slot = collections.Counter()
    cpt = collections.Counter()
    for e in with_lu:
        for s, nm in e["lineup"]:
            any_slot[nm] += 1
            if s == "CPT":
                cpt[nm] += 1
    N = max(1, len(with_lu))
    out["ownership"] = [{"name": nm, "pct": round(100 * c / N, 2), "fpts": parsed["fpts"].get(nm),
                         "dk_pct": {s: round(100 * v, 2) for (pn, s), v in parsed["dk_own"].items() if pn == nm},
                         "pos": (pool or {}).get(nm, {}).get("pos"), "team": (pool or {}).get(nm, {}).get("team"),
                         "salary": (pool or {}).get(nm, {}).get("salary"),
                         **({"cpt_pct": round(100 * cpt[nm] / N, 2)} if kind == "showdown" else {})}
                        for nm, c in any_slot.most_common(TOP_OWN)]
    lc = collections.Counter(tuple(sorted(nm for _, nm in e["lineup"])) if kind == "classic"
                             else tuple([nm for s, nm in e["lineup"] if s == "CPT"] + sorted(nm for s, nm in e["lineup"] if s != "CPT"))
                             for e in with_lu)
    counts = sorted(lc.values(), reverse=True)
    pairs = (sum(c * (c - 1) for c in counts) / (N * (N - 1))) if N > 1 else None
    out["duplicates"] = {"distinct_lineups": len(lc), "max_copies": counts[0] if counts else 0,
                         "entries_in_duplicated_lineups_pct": round(100 * sum(c for c in counts if c > 1) / N, 2),
                         "distinct_pair_collision": pairs}
    users = collections.Counter(e["user_entries"] for e in E)
    out["max_entry_users_pct"] = round(100 * users.get(150, 0) / max(1, n), 2)
    out["top_lineups"] = [{"rank": e["rank"], "points": e["points"], "lineup": e["lineup"]}
                          for e in sorted(with_lu, key=lambda e: e["rank"])[:5]]
    if pool:
        out["shape"] = _shape(with_lu, pool, kind)
    if kind == "classic":
        out["model"] = _model_constants()
    return out


def _shape(with_lu, pool, kind):
    """The field's shape under the sampler's definitions (classic), or the
    captain formats' team splits (showdown). Entries with a player outside the
    pool are counted and skipped."""
    E = [e["lineup"] for e in with_lu if all(nm in pool for _, nm in e["lineup"])]
    N = len(E)
    if not N:
        return {"resolved": 0}
    if kind == "showdown":
        split = collections.Counter()
        for lu in E:
            tc = collections.Counter(pool[nm]["team"] for _, nm in lu)
            split["-".join(str(v) for v in sorted(tc.values(), reverse=True))] += 1
        sal = sorted(sum(pool[nm]["salary"] * (1.5 if s == "CPT" else 1) for s, nm in lu) for lu in E)
        return {"resolved": N, "unresolved": len(with_lu) - N,
                "team_split_pct": {k: round(100 * v / N, 2) for k, v in split.most_common()},
                "salary_mean": round(sum(sal) / N, 1), "salary_median": _q(sal, 0.5)}
    stacks = collections.Counter()
    bring = dst_vs_qb = 0
    sal = []
    for lu in E:
        rows = [(s, pool[nm]) for s, nm in lu]
        qb = next(r for s, r in rows if s == "QB")
        dst = next(r for s, r in rows if s == "DST")
        pc = [r for s, r in rows if s != "QB" and r["pos"] in ("WR", "TE")]
        stacks[min(3, sum(1 for r in pc if r["team"] == qb["team"]))] += 1
        bring += any(r["team"] == qb["opp"] for r in pc)
        dst_vs_qb += dst["opp"] == qb["team"]
        sal.append(sum(r["salary"] for _, r in rows))
    sal.sort()
    return {"resolved": N, "unresolved": len(with_lu) - N,
            "stack_pct": [round(100 * stacks[k] / N, 2) for k in (0, 1, 2, 3)],
            "bring_back_pct": round(100 * bring / N, 2), "dst_vs_own_qb_pct": round(100 * dst_vs_qb / N, 3),
            "salary_mean": round(sum(sal) / N, 1), "salary_median": _q(sal, 0.5),
            "salary_at_cap_pct": round(100 * sum(1 for x in sal if x == CAP) / N, 2)}


def _model_constants():
    """The classic field model's constants, to read beside the real field."""
    import dfs_tourney as T
    return {"max_own": T.CL_FIELD_MAX_OWN, "stack_dist": list(T.CL_STACK_DIST), "bring_back": T.CL_BRING_BACK,
            "salary_used": T.CL_SALARY_USED, "dst_vs_own_qb": T.CL_DST_VS_OWN_QB, "collision": T.CL_FIELD_COLLISION}


def contest_id(path):
    m = _CID.search(os.path.basename(path))
    return int(m.group(1)) if m else None


def intake(drop_dir=None, detail_fn=None, players_fn=None, log=print):
    """The PC's pass: every export in the drop folder not summarized yet (by
    sha256) is read, summarized and saved as dk_field_<cid>, and the index
    updated. Returns the contest ids written. One bad file is ledgered and
    skipped; the rest still land."""
    import deep_cache
    drop_dir = drop_dir or DROP_DIR
    try:
        os.makedirs(drop_dir, exist_ok=True)      # the folder exists for the owner to drop into
    except OSError as e:
        errlog.note("STAND-dir", e, path=drop_dir)
        return []
    if detail_fn is None or players_fn is None:
        import dk
        detail_fn = detail_fn or dk.contest_detail
        players_fn = players_fn or dk.players
    index, _ts = deep_cache.load(INDEX)
    index = dict(index or {})
    done = {v.get("sha256") for v in index.values()}
    # a file already read is skipped by name, size and mtime before it is
    # hashed (a 170 MB export costs a second to hash, every cycle)
    seen = {(v.get("file"), v.get("size"), v.get("mtime")) for v in index.values()}
    bad, _ts = deep_cache.load(BAD)
    bad = dict(bad or {})
    wrote = []
    for fn in sorted(os.listdir(drop_dir)):
        path = os.path.join(drop_dir, fn)
        cid = contest_id(fn)
        if not cid or not fn.lower().endswith((".csv", ".zip")):
            continue
        try:
            st = os.stat(path)
            if (fn, st.st_size, int(st.st_mtime)) in seen or bad.get(fn) == [st.st_size, int(st.st_mtime)]:
                continue
            text, sha = read_text(path)
            if sha in done:
                continue
            parsed = parse(text)
            detail = detail_fn(cid) or {}
            dg = detail.get("draft_group_id")
            pool = pool_from(players_fn(dg)) if dg else None
            summ = summarize(parsed, pool=pool, detail=detail, cid=cid, sha=sha)
            deep_cache.save(KEY.format(cid=cid), summ)
            index[str(cid)] = {"sha256": sha, "name": (detail or {}).get("name"), "kind": summ["kind"],
                               "draft_group_id": (detail or {}).get("draft_group_id"),
                               "entries": summ["entries"], "starts": (detail or {}).get("starts"),
                               "first": summ["points"]["first"], "cash_line": summ["points"].get("cash_line"),
                               "file": fn, "size": st.st_size, "mtime": int(st.st_mtime)}
            deep_cache.save(INDEX, index)
            wrote.append(cid)
            log(f"[vigil-pc] standings: {fn} -> {summ['entries']:,} entries ({summ['kind']})")
        except Exception as e:
            errlog.note("STAND-intake", e, path=fn)
            log(f"[vigil-pc] standings: {fn} unreadable ({type(e).__name__}: {e})")
            try:
                st = os.stat(path)
                bad[fn] = [st.st_size, int(st.st_mtime)]
                deep_cache.save(BAD, bad)
            except OSError as e2:
                errlog.note("STAND-bad", e2, path=fn)
    return wrote


def listing():
    """The server's list of summarized contests, newest start first."""
    import deep_cache
    index, ts = deep_cache.load(INDEX)
    rows = [dict(v, contest_id=int(k)) for k, v in (index or {}).items()]
    rows.sort(key=lambda r: str(r.get("starts") or ""), reverse=True)
    return {"contests": rows, "ts": ts}


def summary(cid):
    import deep_cache
    s, ts = deep_cache.load(KEY.format(cid=int(cid)))
    return s, ts
