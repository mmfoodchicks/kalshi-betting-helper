"""The mechanisms behind DraftKings duplication (2026-11-26, Phase A).
Report: research/reports/dk_mech.md. Aggregates: research/data/dk_mech/.

research/reports/dk_duplication.md documented the gap (the real classic
fields are 340-520x more concentrated than the board's field, showdown 2.4x).
The owner: "Proceed beyond documenting the discrepancy." This module MEASURES
what the real fields do, contest by contest, under one set of definitions,
and runs the SAME code over the board's own model field, so every "real vs
model" line compares like with like. Nothing here changes a board.

PRIVACY. A standings export names every entrant. Handles are held in memory
only, as integer codes, to group one entrant's entries. No handle, EntryId or
per-entrant row is written anywhere: every figure this writes is a count, a
share or a lineup as player names with its number of copies.

Definitions (each is repeated beside its figure in the report):

  lineup      classic: the SET of nine players (slots do not matter);
              showdown: the captain plus the SET of five flex. Exactly
              dk_standings.summarize's.
  C           distinct_pair_collision = sum n(n-1) / (N(N-1)) over distinct
              lineups, N the entries with a lineup: the chance two different
              entries drawn at random hold the same lineup.
  overlap     classic: players two lineups share, of nine. Showdown: slot
              tokens two lineups share, of six, the captain's token being
              (CPT, player) -- so "5 of 6" is the same captain and four of
              five flex, or the same five flex under another captain.
  pair shares P(overlap = k) over all N(N-1)/2 entry pairs, exact, by
              inclusion-exclusion over subset hashes (64-bit sums of a
              random word per player; a clash is ~1e-9 and would only ADD
              pairs).
  same user   two entries of one handle. A duplicate pair is "within-user"
              when both entries are one entrant's, "cross-user" otherwise.
  projection  the board's (Sleeper's line as served). The public may be
              using other projections; proximity to OUR optimum is a lower
              bound on proximity to theirs.

    python3 -m research.dk_mech <export.csv|.csv.gz|dir> [...] [--boards DIR] [--only CID]
"""
import argparse
import collections
import gzip
import itertools
import json
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")
os.environ.setdefault("VIGIL_POOL_CACHE_DIR", "")
import dk_standings as DS      # noqa: E402

OUT = os.path.join(ROOT, "research", "data", "dk_mech")
S7_DIR = os.path.join(ROOT, "research", "data", "dk_standings")
CAP, CPT_MULT, SEED = 50000, 1.5, 20261126
#: every contest with a raw export this study may read, and the split FIXED in
#: research/reports/dk_mech.md section B before any fit.
CONTESTS = {
    197645153: {"kind": "classic", "dg": 156416, "label": "week 10 $2.5M Millionaire", "role": "development"},
    197882014: {"kind": "classic", "dg": 156561, "label": "week 11 $2.5M Millionaire", "role": "validation"},
    193391013: {"kind": "showdown", "dg": 151820, "label": "NE @ SEA (S7, week 1)", "role": "development", "s7": True},
    195526287: {"kind": "showdown", "dg": 153086, "label": "DEN @ KC (S7, week 1)", "role": "development", "s7": True},
    195677825: {"kind": "showdown", "dg": 153434, "label": "DET @ BUF (S7, week 2)", "role": "development", "s7": True},
    197912905: {"kind": "showdown", "dg": 156781, "label": "IND @ HOU (week 11)", "role": "development"},
    198005475: {"kind": "showdown", "dg": 156793, "label": "CIN @ WAS (week 11)", "role": "validation",
                "note": "the board on this draft group was built for contest 198005473 (the $444 Field General); "
                        "its pool and projections are the slate's, its field stamp is that contest's"},
    198150093: {"kind": "showdown", "dg": 157072, "label": "GB @ LAR (week 12)", "role": "validation"},
}
COPY_BUCKETS = ((1, 1), (2, 5), (6, 20), (21, 100), (101, 10 ** 9))
USER_CLASSES = ((1, 1), (2, 19), (20, 149), (150, 10 ** 9))
TOPK = (1, 10, 100, 1000, 10000, 100000)


def _bname(lo, hi):
    return str(lo) if lo == hi else (f"{lo}+" if hi > 10 ** 8 else f"{lo}-{hi}")


# ---- loading ---------------------------------------------------------------
def export_text(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rb") as f:
            return f.read().decode("utf-8-sig")
    return DS.read_text(path)[0]


def find_exports(paths):
    out = {}
    for p in list(paths) + [S7_DIR]:
        fs = [os.path.join(p, f) for f in sorted(os.listdir(p))] if os.path.isdir(p) else [p]
        for f in fs:
            cid = DS.contest_id(f)
            if cid and f.endswith((".csv", ".csv.gz", ".zip")):
                out.setdefault(cid, f)
    return out


def board(dg, boards_dir=None):
    """The board Vigil served (the data branch's export), or None."""
    if boards_dir and os.path.exists(os.path.join(boards_dir, f"tourney-{dg}.json")):
        with open(os.path.join(boards_dir, f"tourney-{dg}.json")) as f:
            return json.load(f)
    try:
        t = subprocess.run(["git", "show", f"origin/sim-history:errors/tourney-{dg}.json"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout
        return json.loads(t)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def dk_pool(dg):
    """DraftKings' whole pool for a draft group {name: {pos, team, opp, salary,
    cpt_salary}}: public salary data, cached in the aggregates folder so a
    re-run needs no network."""
    path = os.path.join(OUT, f"pool_{dg}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    import dk
    rows = dk.players(dg) or []
    pool = DS.pool_from(rows)
    for r in rows:
        nm = DS._norm(r.get("name"))
        if nm in pool and r.get("cpt_salary"):
            pool[nm]["cpt_salary"] = int(r["cpt_salary"])
    if not pool:
        raise RuntimeError(f"no DraftKings pool for draft group {dg}")
    os.makedirs(OUT, exist_ok=True)
    with open(path, "w") as f:
        json.dump(pool, f, indent=0, sort_keys=True)
    return pool


class Ctx:
    """One contest in arrays. Player ids index `names`; `A` is (N, k): classic
    nine ids sorted, showdown the captain then five flex sorted."""


def load(cid, path, boards_dir=None, log=print):
    spec = CONTESTS[cid]
    parsed = DS.parse(export_text(path))
    c = Ctx()
    c.cid, c.spec, c.kind = cid, spec, spec["kind"]
    c.board, c.proj_players = None, None
    if spec.get("s7"):
        from research import s6_capture, s7_beta as SB, s7_field as F
        slate, _details = s6_capture.replay(spec["dg"], capdir=s6_capture.CAP)
        pool = {DS._norm(k): dict(v) for k, v in F.dk_pool(slate).items()}
        s7 = SB.CONTESTS[cid]
        players, _dropped, _stamp = SB.lifted_pool(slate, s7["week"], s7["roster"], log=lambda *_a: None)
        c.proj_players = [dict(p, name=DS._norm(p["name"])) for p in players]
        c.proj_source = ("S7's lifted pool: every DraftKings pool player with a Sleeper projection under the game's teams "
                         "(research/s7_beta.lifted_pool); " + SB.INTEGRITY[cid])
    else:
        pool = dk_pool(spec["dg"])
        c.board = board(spec["dg"], boards_dir)
        if c.board:
            c.proj_players = [dict(p, name=DS._norm(p["name"])) for p in c.board["players"]]
            c.proj_source = (f"the board served for draft group {spec['dg']} ({c.board.get('mins_before_lock', '?')} min before lock)"
                             if c.kind == "showdown" else f"the board served for draft group {spec['dg']}")
    E = [e for e in parsed["entries"] if e["lineup"]]
    names = sorted(set(pool) | {nm for e in E for _s, nm in e["lineup"]})
    nid = {nm: i for i, nm in enumerate(names)}
    P = len(names)
    c.names, c.nid, c.P = names, nid, P
    c.pos = np.asarray([pool.get(nm, {}).get("pos") or "" for nm in names])
    c.team = np.asarray([pool.get(nm, {}).get("team") or "" for nm in names])
    teams = sorted(set(c.team) - {""})
    if c.kind == "showdown" and len(teams) == 2:
        c.opp = np.asarray([teams[1] if t == teams[0] else teams[0] if t == teams[1] else "" for t in c.team])
    else:
        c.opp = np.asarray([pool.get(nm, {}).get("opp") or "" for nm in names])
    c.sal = np.asarray([int(pool.get(nm, {}).get("salary") or 0) for nm in names], dtype=np.int64)
    c.csal = np.asarray([int(pool.get(nm, {}).get("cpt_salary") or round(1.5 * (pool.get(nm, {}).get("salary") or 0)))
                         for nm in names], dtype=np.int64)
    c.in_pool = np.asarray([nm in pool for nm in names])
    c.proj = np.full(P, np.nan)
    for p in c.proj_players or []:
        if p["name"] in nid:
            c.proj[nid[p["name"]]] = float(p.get("proj") or 0.0)
    c.fpts = np.asarray([float(parsed["fpts"].get(nm, 0.0)) for nm in names])
    k = 9 if c.kind == "classic" else 6
    A = np.zeros((len(E), k), dtype=np.int32)
    flex = np.zeros(len(E), dtype=np.int32)
    for i, e in enumerate(E):
        if c.kind == "classic":
            A[i] = sorted(nid[nm] for _s, nm in e["lineup"])
            flex[i] = next(nid[nm] for s, nm in e["lineup"] if s == "FLEX")
        else:
            A[i, 0] = next(nid[nm] for s, nm in e["lineup"] if s == "CPT")
            A[i, 1:] = sorted(nid[nm] for s, nm in e["lineup"] if s != "CPT")
    c.A, c.flex = A, flex
    ucode = {}
    c.users = np.asarray([ucode.setdefault(e["user"], len(ucode)) for e in E], dtype=np.int64)   # memory only
    c.points = np.asarray([e["points"] for e in E])
    c.rank = np.asarray([e["rank"] for e in E])
    c.n_rows, c.N = len(parsed["entries"]), len(E)
    c.dk_own = parsed["dk_own"]
    return c


# ---- the measures (real fields and model samples alike) --------------------
def _hash_words(P, seed=SEED):
    rng = np.random.default_rng(seed)
    return (rng.integers(1, 2 ** 63, size=P, dtype=np.int64).astype(np.uint64),
            rng.integers(1, 2 ** 63, size=P, dtype=np.int64).astype(np.uint64))


def lineup_hash(A, kind, P):
    rf, rc = _hash_words(P)
    if kind == "classic":
        return rf[A].sum(axis=1, dtype=np.uint64)
    return rc[A[:, 0]] + rf[A[:, 1:]].sum(axis=1, dtype=np.uint64)


def _pairs(counts):
    c = counts.astype(np.float64)
    return float((c * (c - 1)).sum() / 2.0)


def duplication(A, kind, P, names=None, top=20):
    """C, the copy-count histogram, the share of entries in duplicated
    lineups, and the most-copied builds."""
    N = len(A)
    h = lineup_hash(A, kind, P)
    _u, first, inv, counts = np.unique(h, return_index=True, return_inverse=True, return_counts=True)
    per_entry = counts[inv]
    hist = collections.Counter(counts.tolist())
    out = {"entries": int(N), "distinct_lineups": int(len(counts)), "max_copies": int(counts.max()),
           "distinct_pair_collision": 2.0 * _pairs(counts) / (N * (N - 1.0)),
           "entries_in_duplicated_lineups_pct": round(100.0 * float((per_entry > 1).mean()), 3),
           "copies_histogram": {str(k): int(v) for k, v in sorted(hist.items())},
           "entries_by_copies_pct": {_bname(lo, hi): round(100.0 * float(((per_entry >= lo) & (per_entry <= hi)).mean()), 3)
                                     for lo, hi in COPY_BUCKETS},
           "effective_lineups_1_over_C": None}
    if out["distinct_pair_collision"] > 0:
        out["effective_lineups_1_over_C"] = round(1.0 / out["distinct_pair_collision"])
    order = np.argsort(-counts, kind="stable")[:top]
    out["_top_rows"] = [(A[first[j]].tolist(), int(counts[j])) for j in order if counts[j] > 1]
    return out, per_entry, inv


def near(A, kind, P, users=None):
    """How near the field's lineups sit to each other. Pair shares by overlap
    (exact), and per ENTRY whether another entry sits within one / two swaps
    of it -- from any entrant, and from a DIFFERENT entrant."""
    N, k = A.shape
    rf, rc = _hash_words(P)
    w = rf[A] if kind == "classic" else np.concatenate([rc[A[:, :1]], rf[A[:, 1:]]], axis=1)
    h = w.sum(axis=1, dtype=np.uint64)
    tot = N * (N - 1.0) / 2.0
    if users is None:
        users = np.arange(N, dtype=np.int64)

    def level(keys):
        """sum of C(n, 2) over the keys; per entry: any key shared, any key
        shared with another user."""
        flat = keys.ravel()
        _u, inv, cnt = np.unique(flat, return_inverse=True, return_counts=True)
        urep = np.repeat(users, keys.shape[1])
        umin = np.full(len(cnt), np.iinfo(np.int64).max, dtype=np.int64)
        umax = np.full(len(cnt), -1, dtype=np.int64)
        np.minimum.at(umin, inv, urep)
        np.maximum.at(umax, inv, urep)
        shared = (cnt[inv] > 1).reshape(keys.shape).any(axis=1)
        cross = (umax[inv] > umin[inv]).reshape(keys.shape).any(axis=1)
        return _pairs(cnt), shared, cross
    s0, sh0, cr0 = level(h[:, None])
    s1, sh1, cr1 = level(h[:, None] - w)
    ij = list(itertools.combinations(range(k), 2))
    k2 = np.stack([h - w[:, i] - w[:, j] for i, j in ij], axis=1)
    s2, sh2, cr2 = level(k2)
    del k2
    p0 = s0
    p1 = s1 - k * p0
    p2 = s2 - (k - 1) * p1 - (k * (k - 1) / 2) * p0
    out = {"pair_share_overlap": {f"{k}_of_{k}": p0 / tot, f"{k - 1}_of_{k}": p1 / tot, f"{k - 2}_of_{k}": p2 / tot},
           "pairs": {"exact": p0, "one_swap": p1, "two_swaps": p2},
           "near_to_exact_pair_ratio": {"one_swap": (p1 / p0) if p0 else None, "two_swaps": (p2 / p0) if p0 else None},
           "entries_with_another_entry_pct": {"exact": round(100 * float(sh0.mean()), 3),
                                              "within_one_swap": round(100 * float(sh1.mean()), 3),
                                              "within_two_swaps": round(100 * float(sh2.mean()), 3)},
           "entries_with_another_USERS_entry_pct": {"exact": round(100 * float(cr0.mean()), 3),
                                                    "within_one_swap": round(100 * float(cr1.mean()), 3),
                                                    "within_two_swaps": round(100 * float(cr2.mean()), 3)}}
    if kind == "showdown":
        hs = rf[A].sum(axis=1, dtype=np.uint64)
        _u, cnt = np.unique(hs, return_counts=True)
        out["same_six_players_any_captain_pair_share"] = _pairs(cnt) / tot
    return out


def mean_overlap(A, kind, P):
    """Mean tokens shared by two different entries: sum over tokens of
    c(c-1) / (N(N-1))."""
    N = len(A)
    if N < 2:
        return None
    if kind == "classic":
        cnt = np.bincount(A.ravel(), minlength=P).astype(np.float64)
        return float((cnt * (cnt - 1)).sum() / (N * (N - 1.0)))
    cc = np.bincount(A[:, 0], minlength=P).astype(np.float64)
    cf = np.bincount(A[:, 1:].ravel(), minlength=P).astype(np.float64)
    return float(((cc * (cc - 1)).sum() + (cf * (cf - 1)).sum()) / (N * (N - 1.0)))


def user_structure(c, per_entry, inv):
    """Multi-entry against convergence. Handles are grouped in memory; only
    class totals leave this function."""
    N = c.N
    ucount = np.bincount(c.users)
    m = ucount[c.users]                                    # the entrant's entries in THIS contest
    pair = inv.astype(np.int64) * (int(c.users.max()) + 1) + c.users
    _u, pinv, pc = np.unique(pair, return_inverse=True, return_counts=True)
    total_pairs = _pairs(np.bincount(inv))
    same_pairs = _pairs(pc)
    users_on_lineup = np.bincount(inv[np.unique(pinv, return_index=True)[1]])    # distinct entrants per lineup
    cross = users_on_lineup[inv] > 1
    own_copies = pc[pinv]
    out = {"entrants": int(len(ucount)), "entries_per_entrant_mean": round(N / len(ucount), 2),
           "duplicate_pairs": total_pairs, "same_user_duplicate_pairs": same_pairs,
           "same_user_share_of_duplicate_pairs_pct": round(100.0 * same_pairs / total_pairs, 4) if total_pairs else None,
           "collision_cross_user_only": 2.0 * (total_pairs - same_pairs) / (N * (N - 1.0)),
           "entries_whose_lineup_another_entrant_holds_pct": round(100.0 * float(cross.mean()), 3),
           "entries_repeating_the_entrants_own_lineup_pct": round(100.0 * float((own_copies > 1).mean()), 3),
           "field_mean_overlap": mean_overlap(c.A, c.kind, c.P), "classes": {}}
    order = np.argsort(c.users, kind="stable")
    starts = np.r_[0, np.cumsum(ucount)]
    for lo, hi in USER_CLASSES:
        sel = (m >= lo) & (m <= hi)
        if not sel.any():
            continue
        us = np.where((ucount >= lo) & (ucount <= hi))[0]
        ov, dr = [], []
        for u in us if lo > 1 else []:
            rows = order[starts[u]:starts[u + 1]]
            ov.append(mean_overlap(c.A[rows], c.kind, c.P))
            dr.append(len(np.unique(inv[rows])) / len(rows))
        out["classes"][_bname(lo, hi)] = {
            "entrants": int(len(us)), "entries": int(sel.sum()), "entries_pct": round(100.0 * float(sel.mean()), 2),
            "share_of_entries_in_duplicated_lineups_pct": round(100.0 * float((sel & (per_entry > 1)).sum() / max(1, (per_entry > 1).sum())), 2),
            "their_entries_in_a_lineup_another_entrant_holds_pct": round(100.0 * float(cross[sel].mean()), 2),
            "their_entries_mean_copies": round(float(per_entry[sel].mean()), 2),
            "their_entries_median_copies": float(np.median(per_entry[sel])),
            "distinct_lineups_per_entry_mean": round(float(np.mean(dr)), 4) if dr else None,
            "within_entrant_mean_overlap": round(float(np.mean(ov)), 3) if ov else None}
    return out, m, cross


def ownership(A, kind, P):
    N = len(A)
    anyslot = np.bincount(A.ravel(), minlength=P) / N
    cpt = np.bincount(A[:, 0], minlength=P) / N if kind == "showdown" else None
    return anyslot, cpt


def joint(A, P, ids):
    """Any-slot joint ownership of `ids`: (K, K) share of entries with both."""
    col = np.full(P, -1)
    col[np.asarray(ids)] = np.arange(len(ids))
    M = np.zeros((len(A), len(ids)), dtype=np.float32)
    for j in range(A.shape[1]):
        ok = col[A[:, j]] >= 0
        M[np.where(ok)[0], col[A[ok, j]]] = 1.0
    return (M.T @ M).astype(np.float64) / len(A)


def pair_table(J, ids, names, top=12):
    o = np.diag(J)
    rows = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            e = o[i] * o[j]
            rows.append((J[i, j], e, i, j))
    def fmt(r):
        return {"a": names[ids[r[2]]], "b": names[ids[r[3]]], "joint_pct": round(100 * r[0], 2),
                "independent_pct": round(100 * r[1], 2), "lift": round(r[0] / r[1], 3) if r[1] > 0 else None}
    big = [r for r in rows if r[1] >= 0.005]
    wsum = sum(r[1] for r in big) or 1.0
    return {"players": len(ids), "pairs_with_independent_share_0_5pct_plus": len(big),
            "mean_abs_log_lift_weighted": round(sum(r[1] * abs(np.log(max(r[0], 1e-9) / r[1])) for r in big) / wsum, 4),
            "top_by_joint": [fmt(r) for r in sorted(rows, key=lambda r: -r[0])[:top]],
            "top_lift": [fmt(r) for r in sorted(big, key=lambda r: -r[0] / r[1])[:top]],
            "lowest_lift": [fmt(r) for r in sorted(big, key=lambda r: r[0] / r[1])[:top]]}


def _quant(v, qs=(0.001, 0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99, 0.999)):
    v = np.asarray(v, dtype=np.float64)
    return {str(q): round(float(np.quantile(v, q)), 2) for q in qs} if len(v) else {}


def structure(c, A, flex=None):
    """Salary and construction. Classic under dk_standings._shape's
    definitions (a stack is the quarterback's WR/TE teammates; a bring-back a
    WR or TE from his opponent), over the entries whose players are all in
    DraftKings' pool."""
    ok = c.in_pool[A].all(axis=1)
    B = A[ok]
    N = len(B)
    out = {"resolved_entries": int(N), "unresolved_entries": int(len(A) - N)}
    if not N:
        return out
    if c.kind == "classic":
        sal = c.sal[B].sum(axis=1)
        isqb = c.pos[B] == "QB"
        qb = B[np.arange(N), isqb.argmax(axis=1)]
        pc = np.isin(c.pos[B], ("WR", "TE"))
        same = pc & (c.team[B] == c.team[qb][:, None])
        opp = pc & (c.team[B] == c.opp[qb][:, None]) & (c.opp[qb][:, None] != "")
        stack = np.minimum(same.sum(axis=1), 3)
        bring = opp.any(axis=1)
        rb_same = ((c.pos[B] == "RB") & (c.team[B] == c.team[qb][:, None])).any(axis=1)
        dst = B[np.arange(N), (c.pos[B] == "DST").argmax(axis=1)]
        tcode = {t: i for i, t in enumerate(sorted(set(c.team)))}
        tc = np.vectorize(tcode.get)(c.team[B])
        maxteam = np.asarray([np.bincount(r).max() for r in tc])
        tmpl = collections.Counter((int(s), bool(b)) for s, b in zip(stack, bring))
        out.update({"salary_mean": round(float(sal.mean()), 1), "salary_median": float(np.median(sal)),
                    "salary_at_cap_pct": round(100 * float((sal == CAP).mean()), 2),
                    "salary_left_100_or_less_pct": round(100 * float((sal >= CAP - 100).mean()), 2),
                    "salary_49500_plus_pct": round(100 * float((sal >= 49500).mean()), 2),
                    "salary_48000_or_less_pct": round(100 * float((sal <= 48000).mean()), 2),
                    "salary_quantiles": _quant(sal, (0.01, 0.05, 0.25, 0.5, 0.75)),
                    "stack_pct": [round(100 * float((stack == k).mean()), 2) for k in (0, 1, 2, 3)],
                    "bring_back_pct": round(100 * float(bring.mean()), 2),
                    "qb_with_own_rb_pct": round(100 * float(rb_same.mean()), 2),
                    "dst_vs_own_qb_pct": round(100 * float((c.opp[dst] == c.team[qb]).mean()), 3),
                    "max_from_one_team_pct": {str(k): round(100 * float((maxteam == k).mean()), 2) for k in (1, 2, 3, 4, 5)},
                    "templates_pct": {f"QB+{s}{' +bring-back' if b else ''}": round(100 * v / N, 2)
                                      for (s, b), v in sorted(tmpl.items())}})
        if flex is not None:
            fp = c.pos[flex[ok]]
            out["flex_position_pct"] = {p: round(100 * float((fp == p).mean()), 2) for p in ("RB", "WR", "TE")}
    else:
        sal = c.csal[B[:, 0]] + c.sal[B[:, 1:]].sum(axis=1)
        left = CAP - sal
        teams = sorted(set(c.team[c.in_pool]) - {""})
        n0 = (c.team[B] == teams[0]).sum(axis=1)
        big = np.maximum(n0, 6 - n0)
        cp = c.pos[B[:, 0]]
        kd = np.isin(c.pos[B[:, 1:]], ("K", "DST")).sum(axis=1)
        isqb = c.pos[B[:, 1:]] == "QB"
        own_qb = (isqb & (c.team[B[:, 1:]] == c.team[B[:, 0]][:, None])).any(axis=1)
        qbcap = cp == "QB"
        catch = (np.isin(c.pos[B[:, 1:]], ("WR", "TE")) & (c.team[B[:, 1:]] == c.team[B[:, 0]][:, None])).sum(axis=1)
        out.update({"salary_mean": round(float(sal.mean()), 1), "salary_median": float(np.median(sal)),
                    "salary_left_pct": {"0": round(100 * float((left == 0).mean()), 2),
                                        "100-200": round(100 * float(((left > 0) & (left <= 200)).mean()), 2),
                                        "300-500": round(100 * float(((left > 200) & (left <= 500)).mean()), 2),
                                        "600-1000": round(100 * float(((left > 500) & (left <= 1000)).mean()), 2),
                                        "1100-2000": round(100 * float(((left > 1000) & (left <= 2000)).mean()), 2),
                                        "2100+": round(100 * float((left > 2000).mean()), 2)},
                    "team_split_pct": {k: round(100 * float((big == v).mean()), 2) for k, v in (("3-3", 3), ("4-2", 4), ("5-1", 5))},
                    "captain_position_pct": {p: round(100 * float((cp == p).mean()), 2) for p in ("QB", "RB", "WR", "TE", "K", "DST")},
                    "flex_kickers_and_defenses_pct": {str(k): round(100 * float((kd == k).mean()), 2) for k in range(5)},
                    "both_quarterbacks_pct": round(100 * float(((c.pos[B] == "QB").sum(axis=1) == 2).mean()), 2),
                    "non_qb_captain_with_his_qb_in_flex_pct": round(100 * float(own_qb[~qbcap].mean()), 2) if (~qbcap).any() else None,
                    "qb_captain_own_catchers_pct": ({str(k): round(100 * float((np.minimum(catch[qbcap], 3) == k).mean()), 2)
                                                     for k in range(4)} if qbcap.any() else None)})
    return out


def scores(c, A):
    """The lineup's real DraftKings points rebuilt from the export's FPTS
    column (1.5x the captain)."""
    if c.kind == "classic":
        return c.fpts[A].sum(axis=1)
    return CPT_MULT * c.fpts[A[:, 0]] + c.fpts[A[:, 1:]].sum(axis=1)


# ---- the projection optimum -------------------------------------------------
def classic_optimum(c):
    """The exact projection-optimal classic lineup under DraftKings' rules
    only (1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX of RB/WR/TE, 1 DST, the cap): a
    knapsack over $100 units per position, the three FLEX cases joined."""
    def table(pos, kmax):
        dp = [dict() for _ in range(kmax + 1)]
        dp[0][0] = (0.0, ())
        for i in np.where((c.pos == pos) & ~np.isnan(c.proj) & (c.sal > 0))[0]:
            s, v = int(c.sal[i]) // 100, float(c.proj[i])
            for k in range(kmax, 0, -1):
                for s0, (v0, ids) in list(dp[k - 1].items()):
                    if s0 + s <= CAP // 100 and (s0 + s not in dp[k] or dp[k][s0 + s][0] < v0 + v):
                        dp[k][s0 + s] = (v0 + v, ids + (int(i),))
        return dp

    def join(a, b):
        out = {}
        for sa, (va, ia) in a.items():
            for sb, (vb, ib) in b.items():
                if sa + sb <= CAP // 100 and (sa + sb not in out or out[sa + sb][0] < va + vb):
                    out[sa + sb] = (va + vb, ia + ib)
        return out
    qb, rb, wr, te, dst = table("QB", 1), table("RB", 3), table("WR", 4), table("TE", 2), table("DST", 1)
    base = join(qb[1], dst[1])
    best = (-1.0, ())
    for nr, nw, nt in ((3, 3, 1), (2, 4, 1), (2, 3, 2)):
        t = join(join(join(base, rb[nr]), wr[nw]), te[nt])
        if t:
            best = max(best, max(t.values()))
    return best


def enumerate_universe(players):
    """Every legal showdown lineup on the projected pool (production's
    dfs_tourney.enumerate_showdown: under the cap with the captain at 1.5x
    salary, both teams; no depth gate, no owner rule), its projection x and
    salary, and the 1-based projection rank (ties by enumeration order)."""
    import dfs_tourney as T
    idx, W, _ok = T.enumerate_showdown(players, CAP, cpt_mult=CPT_MULT)
    del W
    proj = np.asarray([float(p.get("proj") or 0.0) for p in players])
    sal = np.asarray([int(p["salary"]) for p in players])
    csal = np.asarray([int(p["cpt_salary"]) for p in players])
    x = proj[idx[:, 0]] * CPT_MULT + proj[idx[:, 1:]].sum(axis=1)
    s = csal[idx[:, 0]] + sal[idx[:, 1:]].sum(axis=1)
    rank = np.empty(len(idx), dtype=np.int64)
    rank[np.argsort(-x, kind="stable")] = np.arange(1, len(idx) + 1)
    return idx, x, s, rank


def universe_keys(idx, P):
    key = idx[:, 0].astype(np.int64)
    for k in range(1, 6):
        key = key * P + idx[:, k].astype(np.int64)
    return key


def to_universe(c, players, idx):
    """Each entry's row in the universe (-1: it holds a player outside the
    projected pool, or the lineup is not in the enumeration)."""
    pid = {p["name"]: i for i, p in enumerate(players)}
    m = np.asarray([pid.get(nm, -1) for nm in c.names])
    L = m[c.A]
    ok = (L >= 0).all(axis=1)
    L[:, 1:] = np.sort(L[:, 1:], axis=1)
    P = len(players)
    key = universe_keys(idx, P)
    o = np.argsort(key)
    ek = universe_keys(np.where(ok[:, None], L, 0), P)
    pos = np.searchsorted(key[o], ek)
    pos = np.minimum(pos, len(o) - 1)
    hit = ok & (key[o][pos] == ek)
    return np.where(hit, o[pos], -1)


def production_field(players, idx, top_share=None):
    import dfs_tourney as T
    f, beta = T.field_weights(players, idx, cpt_mult=CPT_MULT, **({"top_share": top_share} if top_share else {}))
    return f, beta


def sample_universe(c, players, idx, f, n, seed=SEED):
    """n entries drawn independently from the lineup distribution f, as a
    field in the contest's own player ids -- so the measures above run on a
    model exactly as they run on the standings."""
    rng = np.random.default_rng(seed)
    cdf = np.cumsum(f)
    rows = np.minimum(np.searchsorted(cdf, rng.random(n) * cdf[-1]), len(f) - 1)
    m = np.asarray([c.nid[p["name"]] for p in players], dtype=np.int32)
    A = m[idx[rows]]
    A[:, 1:] = np.sort(A[:, 1:], axis=1)
    return A, rows


def classic_replica(c, n, seed=SEED, workers=4, targets=None):
    """The served classic field, rebuilt: production's own sampler
    (dfs_tourney.FieldSampler over classic_sample, the board's beta and
    kappa) with its per-player pulls re-steered (steer_field) onto the
    ownership the board stamped (field_pct). The served pulls were not
    stored, so this is a replica of the served field's distribution, not its
    rows; the board's stamped collision is printed beside the replica's.
    With `targets` (percent per board player) the pulls are steered there
    instead: the ORACLE-MARGINALS diagnostic hands the sampler the real
    field's own per-player ownership, so whatever duplication is still
    missing is not a per-player ownership error."""
    import dfs_tourney as T
    b = c.board
    P = [dict(p, name=DS._norm(p["name"])) for p in b["players"]]
    fm = b["field_model"]
    rng = np.random.default_rng(seed)
    draw = T.FieldSampler(P, workers)
    try:
        tg = [float(p["field_pct"]) for p in P] if targets is None else [float(t) for t in targets]
        pulls, steer = T.steer_field(draw, P, rng, float(fm["beta"]), float(fm["kappa"]), tg)
        idx = draw(int(n), rng, 1.0, 0.0, pulls=pulls)
    finally:
        draw.close()
    m = np.asarray([c.nid[p["name"]] for p in P], dtype=np.int32)
    A = np.sort(m[idx], axis=1)
    own = 100.0 * np.bincount(idx.ravel(), minlength=len(P)) / len(idx)
    return A, m[idx[:, 7]], {"rows": int(len(idx)), "steer_mae_by_pass": steer["mae_to_targets_by_pass"],
                             "mae_to_targets_pct_points": round(float(np.abs(own - np.asarray(tg)).mean()), 3)}


# ---- one field, every measure ------------------------------------------------
def panel(c, A, flex=None, users=None, joint_ids=None):
    """Everything measurable on a field without knowing who entered it."""
    dup, per_entry, inv = duplication(A, c.kind, c.P)
    top_rows = dup.pop("_top_rows")
    anyslot, cpt = ownership(A, c.kind, c.P)
    sc = scores(c, A)
    out = {"duplication": dup, "near": near(A, c.kind, c.P, users), "mean_overlap": mean_overlap(A, c.kind, c.P),
           "structure": structure(c, A, flex), "score_quantiles": _quant(sc), "score_mean": round(float(sc.mean()), 2),
           "score_sd": round(float(sc.std()), 2)}
    if joint_ids is not None:
        out["_J"] = joint(A, c.P, joint_ids)
    return out, {"per_entry": per_entry, "inv": inv, "top_rows": top_rows, "anyslot": anyslot, "cpt": cpt, "scores": sc}


def _lineup_names(c, row):
    return ([c.names[row[0]] + " (CPT)"] + [c.names[i] for i in row[1:]]) if c.kind == "showdown" else [c.names[i] for i in row]


def by_copies(per_entry, values, fn):
    return {_bname(lo, hi): fn(values[(per_entry >= lo) & (per_entry <= hi)])
            for lo, hi in COPY_BUCKETS if ((per_entry >= lo) & (per_entry <= hi)).any()}


def analyse(cid, path, boards_dir=None, log=print):
    c = load(cid, path, boards_dir, log)
    spec = c.spec
    log(f"[mech] {cid} {spec['label']}: {c.N:,} entries with a lineup of {c.n_rows:,} rows")
    own_real, cpt_real = ownership(c.A, c.kind, c.P)
    jid = np.argsort(-own_real, kind="stable")[:30 if c.kind == "classic" else 20].tolist()
    real, R = panel(c, c.A, c.flex if c.kind == "classic" else None, c.users, jid)
    J_real = real.pop("_J")
    per_entry = R["per_entry"]
    out = {"contest_id": cid, "label": spec["label"], "kind": c.kind, "draft_group_id": spec["dg"], "role": spec["role"],
           "note": spec.get("note"), "rows": c.n_rows, "entries_with_lineup": c.N,
           "projection_source": getattr(c, "proj_source", None), "real": real}
    # reconciliation: the rebuilt score against DraftKings' Points column
    out["real"]["points_rebuild_max_abs_diff"] = round(float(np.abs(R["scores"] - c.points).max()), 3)
    us, m, cross = user_structure(c, per_entry, R["inv"])
    out["real"]["users"] = us
    out["real"]["pairs"] = pair_table(J_real, jid, c.names)
    # where the duplicated lineups finish (one slate's outcome: descriptive)
    pct = 100.0 * (np.argsort(np.argsort(-c.points, kind="stable"), kind="stable") + 1) / c.N
    out["real"]["finish_by_copies"] = by_copies(per_entry, np.arange(c.N), lambda ix: {
        "entries": int(len(ix)), "mean_points": round(float(c.points[ix].mean()), 2),
        "top_1pct_share_pct": round(100 * float((pct[ix] <= 1).mean()), 2),
        "top_10pct_share_pct": round(100 * float((pct[ix] <= 10).mean()), 2),
        "top_22pct_share_pct": round(100 * float((pct[ix] <= 22).mean()), 2)})
    model = None
    tops = []
    if c.kind == "showdown" and c.proj_players:
        players = c.proj_players
        idx, x, s, rank = enumerate_universe(players)
        L = len(idx)
        u = to_universe(c, players, idx)
        rep = u >= 0
        f, beta = production_field(players, idx)
        cumf = np.zeros(L + 1)
        cumf[1:] = np.cumsum(f[np.argsort(rank)])
        prox = {"universe_players": len(players), "legal_lineups": int(L),
                "representable_entries_pct": round(100 * float(rep.mean()), 2),
                "optimum": {"proj": round(float(x.max()), 2), "salary": int(s[np.argmax(x)]),
                            "real_copies": int((u == int(np.argmax(x))).sum())},
                "note": "rank 1 is the highest projected lineup; shares are of ALL entries with a lineup (an unrepresentable "
                        "entry is in no top-K)",
                "entries_in_projection_top_K_pct": {}, "by_copies": {}, "by_user_class": {}}
        r_e = np.where(rep, rank[np.maximum(u, 0)], L + 1)
        gap = np.where(rep, x.max() - x[np.maximum(u, 0)], np.nan)
        for K in TOPK:
            prox["entries_in_projection_top_K_pct"][str(K)] = {
                "real": round(100 * float((r_e <= K).mean()), 3), "production_field": round(100 * float(cumf[min(K, L)]), 3),
                "uniform": round(100 * min(K, L) / L, 4)}
        prox["by_copies"] = by_copies(per_entry, np.arange(c.N), lambda ix: {
            "entries": int(len(ix)), "representable_pct": round(100 * float(rep[ix].mean()), 2),
            **{f"top_{K}_pct": round(100 * float((r_e[ix] <= K).mean()), 2) for K in (100, 1000, 10000)},
            "median_rank_pctile": round(100 * float(np.median(r_e[ix][rep[ix]])) / L, 3) if rep[ix].any() else None,
            "median_gap_pts": round(float(np.nanmedian(gap[ix])), 2) if rep[ix].any() else None,
            "salary_left_0_pct": round(100 * float((s[np.maximum(u[ix], 0)][rep[ix]] == CAP).mean()), 2) if rep[ix].any() else None})
        for lo, hi in USER_CLASSES:
            ix = np.where((m >= lo) & (m <= hi))[0]
            if len(ix):
                prox["by_user_class"][_bname(lo, hi)] = {
                    "entries": int(len(ix)), **{f"top_{K}_pct": round(100 * float((r_e[ix] <= K).mean()), 2) for K in (100, 1000, 10000)},
                    "median_gap_pts": round(float(np.nanmedian(gap[ix])), 2),
                    "salary_left_0_pct": round(100 * float((s[np.maximum(u[ix], 0)][rep[ix]] == CAP).mean()), 2)}
        prox["rank_pctile_quantiles_representable"] = _quant(100.0 * r_e[rep] / L, (0.1, 0.25, 0.5, 0.75, 0.9))
        prox["gap_pts_quantiles_representable"] = _quant(gap[rep], (0.1, 0.25, 0.5, 0.75, 0.9))
        out["real"]["optimizer_proximity"] = prox
        first = {}
        for i, h in enumerate(R["inv"]):
            first.setdefault(int(h), i)
        for row, copies in R["top_rows"]:
            j = int(np.where((c.A == np.asarray(row)).all(axis=1))[0][0])
            tops.append({"lineup": _lineup_names(c, row), "copies": copies, "entrants": int(len(set(c.users[(c.A == np.asarray(row)).all(axis=1)].tolist()))),
                         "proj_rank": int(r_e[j]) if rep[j] else None, "proj_gap": round(float(gap[j]), 2) if rep[j] else None,
                         "salary": int(c.csal[row[0]] + c.sal[row[1:]].sum()),
                         "production_expected_copies": round(float(c.N * f[u[j]]), 2) if rep[j] else None,
                         "points": round(float(c.points[j]), 2)})
        # the production family's field on this universe, through the same measures
        n_rep = int(rep.sum())
        Am, _rows = sample_universe(c, players, idx, f, n_rep)
        mp, MR = panel(c, Am, joint_ids=jid)
        J_m = mp.pop("_J")
        Ar = c.A[rep]
        rp, RR = panel(c, Ar, joint_ids=jid, users=c.users[rep])
        J_r = rp.pop("_J")
        o_r, c_r = ownership(Ar, c.kind, c.P)
        o_m, c_m = MR["anyslot"], MR["cpt"]
        pl = np.asarray([c.nid[p["name"]] for p in players])
        iu = np.triu_indices(len(jid), 1)
        model = {"what": ("softmax(beta x projection) over the universe with the most popular lineup at 0.2% of the field "
                          "(dfs_tourney.field_weights, FIELD_TOP_SHARE): production's rule on this universe"),
                 "beta": round(beta, 4), "exact_pair_collision_sum_p2": float((f ** 2).sum()),
                 "top_expected_copies_at_N": round(float(c.N * f.max()), 1),
                 "board_stamp": ({k: (c.board.get("field_model") or {}).get(k) for k in ("beta", "top_share_pct", "top_copies")}
                                 | {"lineups_legal": c.board.get("lineups_legal"), "field_duplication": c.board.get("field_duplication")})
                 if c.board else None,
                 "sample_rows": n_rep, "sampled": mp, "real_on_the_same_support": rp,
                 "ownership_mae_pct_points": {
                     "captain": round(100 * float(np.abs(c_m[pl] - c_r[pl]).mean()), 3),
                     "flex": round(100 * float(np.abs((o_m - c_m)[pl] - (o_r - c_r)[pl]).mean()), 3)},
                 "joint_ownership_mae_pct_points": round(100 * float(np.abs(J_m[iu] - J_r[iu]).mean()), 3),
                 "pairs": pair_table(J_m, jid, c.names, top=6)}
        if c.board:
            bp = {p["name"]: p for p in players}
            def mae(key, real_v):
                return round(float(np.mean([abs(float(bp[nm].get(key) or 0.0) - 100 * real_v[c.nid[nm]]) for nm in bp])), 3)
            model["board_ownership_mae_all_entries"] = {
                "field_cpt_pct": mae("field_cpt_pct", cpt_real), "opt_cpt_pct": mae("opt_cpt_pct", cpt_real),
                "field_flex_pct": mae("field_flex_pct", own_real - cpt_real), "opt_flex_pct": mae("opt_flex_pct", own_real - cpt_real)}
    elif c.kind == "classic" and c.board:
        val, ids = classic_optimum(c)
        ids = sorted(ids)
        projd = ~np.isnan(c.proj[c.A]).any(axis=1)
        tot = np.where(projd, np.nan_to_num(c.proj)[c.A].sum(axis=1), np.nan)
        gap = val - tot
        isopt = np.zeros(c.P, dtype=bool)
        isopt[ids] = True
        ov = isopt[c.A].sum(axis=1)
        sal = c.sal[c.A].sum(axis=1)
        prox = {"optimum": {"lineup": [c.names[i] for i in ids], "proj": round(val, 2), "salary": int(c.sal[ids].sum()),
                            "real_copies": int((c.A == np.asarray(ids)).all(axis=1).sum()),
                            "rules": "DraftKings' only (positions, the cap): no house rule"},
                "entries_with_every_player_projected_pct": round(100 * float(projd.mean()), 2),
                "gap_pts_quantiles": _quant(gap[projd], (0.01, 0.1, 0.25, 0.5, 0.75, 0.9)),
                "entries_within_pts_pct": {str(k): round(100 * float((gap[projd] <= k).mean()), 3) for k in (1, 2, 3, 5, 8, 12, 20)},
                "players_shared_with_optimum_pct": {str(k): round(100 * float((ov == k).mean()), 3) for k in range(10)},
                "by_copies": by_copies(per_entry, np.arange(c.N), lambda ix: {
                    "entries": int(len(ix)), "median_gap_pts": round(float(np.nanmedian(gap[ix])), 2),
                    "within_5_pts_pct": round(100 * float((gap[ix] <= 5).mean()), 2),
                    "within_10_pts_pct": round(100 * float((gap[ix] <= 10).mean()), 2),
                    "mean_players_shared_with_optimum": round(float(ov[ix].mean()), 2),
                    "salary_at_cap_pct": round(100 * float((sal[ix] == CAP).mean()), 2),
                    "salary_mean": round(float(sal[ix].mean()))}),
                "by_user_class": {}}
        for lo, hi in USER_CLASSES:
            ix = np.where((m >= lo) & (m <= hi))[0]
            if len(ix):
                prox["by_user_class"][_bname(lo, hi)] = {
                    "entries": int(len(ix)), "median_gap_pts": round(float(np.nanmedian(gap[ix])), 2),
                    "within_10_pts_pct": round(100 * float((gap[ix] <= 10).mean()), 2),
                    "salary_at_cap_pct": round(100 * float((sal[ix] == CAP).mean()), 2),
                    "mean_players_shared_with_optimum": round(float(ov[ix].mean()), 2)}
        # the board's own listed lineups in the real field
        listed = {}
        for cidk, res in (c.board.get("results") or {}).items():
            for lst in ("top_ev", "top_money", "top_top1", "top_top01", "top_win"):
                rows = [sorted(c.nid.get(DS._norm(p["name"]), -1) for p in e["lineup"]) for e in (res.get(lst) or [])]
                hits = [int((c.A == np.asarray(r)).all(axis=1).sum()) for r in rows if min(r) >= 0]
                listed[f"{cidk}:{lst}"] = {"lineups": len(rows), "found_in_real_field": int(sum(1 for h in hits if h)),
                                           "real_copies_total": int(sum(hits))}
        prox["board_listed_lineups_in_real_field"] = listed
        out["real"]["optimizer_proximity"] = prox
        for row, copies in R["top_rows"]:
            sel = (c.A == np.asarray(row)).all(axis=1)
            j = int(np.where(sel)[0][0])
            tops.append({"lineup": _lineup_names(c, row), "copies": copies, "entrants": int(len(set(c.users[sel].tolist()))),
                         "proj": round(float(tot[j]), 2) if projd[j] else None, "proj_gap": round(float(gap[j]), 2) if projd[j] else None,
                         "players_shared_with_optimum": int(ov[j]), "salary": int(sal[j]), "points": round(float(c.points[j]), 2)})
        Am, fm_flex, rep_info = classic_replica(c, c.N)
        mp, MR = panel(c, Am, fm_flex, joint_ids=jid)
        J_m = mp.pop("_J")
        tm = np.where(~np.isnan(c.proj[Am]).any(axis=1), np.nan_to_num(c.proj)[Am].sum(axis=1), np.nan)
        bp = {DS._norm(p["name"]): p for p in c.board["players"]}
        top40 = [c.names[i] for i in np.argsort(-own_real, kind="stable")[:40]]
        def mae(key, over):
            return round(float(np.mean([abs(float((bp.get(nm) or {}).get(key) or 0.0) - 100 * own_real[c.nid[nm]]) for nm in over])), 3)
        iu = np.triu_indices(len(jid), 1)
        fm = c.board["field_model"]
        Ao, fo, orep = classic_replica(c, c.N, targets=[100.0 * own_real[c.nid[DS._norm(p["name"])]] for p in c.board["players"]])
        op, _OR = panel(c, Ao, fo, joint_ids=jid)
        J_o = op.pop("_J")
        to = np.where(~np.isnan(c.proj[Ao]).any(axis=1), np.nan_to_num(c.proj)[Ao].sum(axis=1), np.nan)
        oracle = {"what": ("DIAGNOSTIC, not a model: production's sampler steered onto the REAL field's per-player ownership "
                           "(the answer, read after the contest). What it still misses is not a per-player ownership error."),
                  "replica": orep, "sampled": op,
                  "gap_pts_quantiles": _quant(val - to[~np.isnan(to)], (0.01, 0.1, 0.25, 0.5, 0.75, 0.9)),
                  "joint_ownership_mae_pct_points_top30": round(100 * float(np.abs(J_o[iu] - J_real[iu]).mean()), 3),
                  "pairs": pair_table(J_o, jid, c.names, top=4)}
        model = {"what": "the served optimizer field (FIELD_MODEL 'optimizer', ENGINE 10), rebuilt by production's own sampler; see classic_replica",
                 "replica": rep_info,
                 "board_stamp": {k: fm.get(k) for k in ("model", "beta", "kappa", "collision", "n", "mean_salary", "max_own_pct", "top_share_pct", "achieved")},
                 "sampled": mp,
                 "replica_gap_pts_quantiles": _quant(val - tm[~np.isnan(tm)], (0.01, 0.1, 0.25, 0.5, 0.75, 0.9)),
                 "replica_entries_within_pts_pct": {str(k): round(100 * float((val - tm[~np.isnan(tm)] <= k).mean()), 3) for k in (1, 2, 3, 5, 8, 12, 20)},
                 "ownership_mae_pct_points": {
                     "board_players": {k: mae(k, list(bp)) for k in ("field_pct", "opt_pct", "casual_pct")},
                     "real_top_40": {k: mae(k, top40) for k in ("field_pct", "opt_pct", "casual_pct")},
                     "real_top_40_not_on_board": [nm for nm in top40 if nm not in bp]},
                 "joint_ownership_mae_pct_points_top30": round(100 * float(np.abs(J_m[iu] - J_real[iu]).mean()), 3),
                 "joint_real_mean_pct_top30": round(100 * float(J_real[iu].mean()), 3),
                 "pairs": pair_table(J_m, jid, c.names, top=6)}
    out["real"]["most_copied"] = tops
    bpp = {p["name"]: p for p in (c.proj_players or [])}
    out["real"]["ownership_top"] = [
        {"name": c.names[i], "pos": str(c.pos[i]), "team": str(c.team[i]), "salary": int(c.sal[i]),
         "proj": bpp.get(c.names[i], {}).get("proj"), "real_pct": round(100 * float(own_real[i]), 2),
         **({"real_cpt_pct": round(100 * float(cpt_real[i]), 2)} if cpt_real is not None else {}),
         **{k: bpp.get(c.names[i], {}).get(k) for k in ("field_pct", "opt_pct", "casual_pct", "field_cpt_pct", "field_flex_pct",
                                                         "opt_cpt_pct", "opt_flex_pct") if k in bpp.get(c.names[i], {})}}
        for i in np.argsort(-own_real, kind="stable")[:25]]
    out["real"]["ownership_concentration_sum_of_squares"] = round(float((own_real ** 2).sum()), 4)
    out["model_field"] = model
    if c.kind == "classic" and c.board:
        out["oracle_marginals"] = oracle
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="standings exports, or folders of them")
    ap.add_argument("--boards", default=None, help="a folder of tourney-<dg>.json board exports (else the data branch)")
    ap.add_argument("--only", type=int, action="append")
    args = ap.parse_args()
    ex = find_exports(args.paths)
    os.makedirs(OUT, exist_ok=True)
    for cid in CONTESTS:
        if args.only and cid not in args.only:
            continue
        if cid not in ex:
            print(f"[mech] {cid}: no export given; skipped")
            continue
        out = analyse(cid, ex[cid], args.boards)
        with open(os.path.join(OUT, f"{cid}.json"), "w") as f:
            json.dump(out, f, indent=1)
        d = out["real"]["duplication"]
        print(f"[mech] {cid}: C {d['distinct_pair_collision']:.3e}, {d['entries_in_duplicated_lineups_pct']}% of entries duplicated, "
              f"same-user share of duplicate pairs {out['real']['users']['same_user_share_of_duplicate_pairs_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
