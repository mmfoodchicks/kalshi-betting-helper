"""Classic opponent-field modelling, second pass (2026-11-26).
Report: research/reports/cl_field2.md. Aggregates: research/data/cl_field2/.

research/reports/dk_mech.md left four things open about the classic field:
why the served field scores 15-29 real DraftKings points under the public's,
why its per-player ownership is half as concentrated, whose projection the
public's optimizers run on, and how the public builds at the cap. This module
MEASURES those on the two contests with a raw export (weeks 10 and 11 -- both
DEVELOPMENT data here, n = 2 slates) and builds the candidate fields the
prospective protocol (research/reports/cl_field2_protocol.md) registers.
Nothing here changes a board: dfs_tourney is only called, never edited.

PRIVACY. As research/dk_mech.py: entrant handles are integer codes in memory,
used only to class an entry by its entrant's entry count. Everything written
is a count, a share, a per-player figure or a lineup as player names.

Definitions (repeated beside each figure in the report):

  o_i, s_i    a player's any-slot ownership in the real field and in the
              served field's replica (dk_mech.classic_replica: production's
              sampler re-steered onto the ownership the board stamped).
  D           mean real DraftKings points of the real field minus the served
              field's. A lineup's score is a SUM over its players, so
              D = sum_i (o_i - s_i) x fpts_i EXACTLY: the mean deficit is a
              per-player ownership quantity, and no stacking or correlation
              term can enter it (those move the spread, not the mean).
  D_proj      sum_i (o_i - s_i) x q_i, q the board's projection (0 for a
              player the board did not project): how much better the public's
              field is ON OUR OWN PROJECTION.
  D_resid     sum_i (o_i - s_i) x (fpts_i - q_i): the public's players beat
              our projection by more than the served field's did. Information
              we lack, or the week's luck; the permutation p-value beside it
              shuffles the residuals within position.
  D_excluded  sum over players the board did not project of o_i x fpts_i.
  allocation / salary / selection
              D_proj (and D) split by position g, with n_g the mean number
              of that position in a lineup, m_g the mean salary of a pick and
              p_g the mean value of a pick:
                allocation = sum_g (n_g^R - n_g^S) x p_g^S   (the FLEX mix)
                salary     = sum_g n_g^R x b_g x (m_g^R - m_g^S), b_g the
                             slope of value on salary within the position
                             (weighted by the two fields' mean ownership)
                selection  = the rest: better players at the same price.
  source      a projection for every board player. `board` is the served
              board's own (Sleeper's week through Vigil's usage blend and
              engine). `sleeper_raw` and `espn_raw` are the two public lines
              as nfl_props_log froze them before each kickoff, scored by the
              same DraftKings weights from the components both logs keep,
              each position's level set to the board's (the logs leave out
              interceptions, fumbles and bonuses: a position-level offset,
              not an opinion about a player); a player missing from either
              log, and every defense, keeps the board's figure. `consensus`
              is their midpoint. `kalshi` moves sleeper_raw by Kalshi's
              frozen prop-implied means where a ladder exists and reads
              within a factor 2 of Sleeper's. `salary` is DraftKings' own
              price list: the projection a position's salary implies (a
              per-position line through the board). `fppg` is the average
              DraftKings points a game its draft screen shows.
  noisy-optimizer share (sigma, tau)
              the share of N_SHARE exact optimizer lineups (DraftKings' rules
              only, production's dfs_tourney.optimal_lineups) holding the
              player, each lineup solved on source x (1 + sigma e_i) x
              (1 + tau h_team) -- e per player per lineup, h per team per
              lineup and applied to that team's QB, WR and TE only (a shared
              passing-game draw: what makes an optimizer stack without a
              stack rule). tau = 0 is dk_mech's cluster.
  one-swap optimal
              no single same-position replacement that fits the salary left
              raises the lineup's projection (under a named source). An
              optimizer's output is; a hand build seldom is.

    python3 -m research.cl_field2 diagnose <exports dir> [--boards DIR] [--props DIR]
    python3 -m research.cl_field2 targets  <exports dir> ...
    (the candidate fields and the prospective harness: research/cl_field2_fields.py)
"""
import argparse
import json
import os
import pickle
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")
os.environ.setdefault("VIGIL_POOL_CACHE_DIR", "")
from research import dk_mech as M      # noqa: E402

OUT = os.path.join(ROOT, "research", "data", "cl_field2")
#: week -> (contest, draft group). BOTH are development data in this study:
#: dk_mech read week 11 as its validation contest, so neither can validate
#: anything again.
WEEKS = {10: (197645153, 156416), 11: (197882014, 156561)}
SEED = 20261127
POS = ("QB", "RB", "WR", "TE", "DST")
#: 10,000 lineups a cell: a share's standard error is under half a point.
#: sigma 0.05 was run once at 20,000 and dropped: on the board's projection
#: it put the sum of squared ownership at 5.08 (real 1.53), top-40 MAE 16.1.
#: sigma 0.10 was run on the board, sleeper_raw and espn_raw sources (and on
#: consensus at tau 0) before the grid was cut: top-40 MAE 8.5-14.8, sum of
#: squared ownership 2.5-3.8. Those lines are kept in
#: research/data/cl_field2/targets_first_run.log.
N_SHARE = 10000
SIGMAS = (0.15, 0.25, 0.40)
TAUS = (0.0, 0.15)
SOURCES = ("board", "sleeper_raw", "espn_raw", "consensus")
#: DraftKings' offensive weights for the components both logs carry. The same
#: weights score both sources, so a missing component (interceptions, fumbles,
#: the yardage bonuses) cancels in the DIFFERENCE, which is all that is used.
DKW = {"pass_yd": 0.04, "pass_td": 4.0, "rush_yd": 0.1, "rush_td": 6.0, "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0}
USER_CLASSES = M.USER_CLASSES
COPY_CLASSES = ((1, 1), (2, 5), (6, 20), (21, 10 ** 9))


def _knorm(s):
    import kalshi_nfl
    return kalshi_nfl._norm(s)


def props(week, props_dir=None):
    """The frozen prop log of a week (nfl_props_log's store, as the error-log
    workflow commits it under props/ on the data branch)."""
    name = f"nfl_props_2026_w{int(week):02d}.json"
    if props_dir and os.path.exists(os.path.join(props_dir, name)):
        with open(os.path.join(props_dir, name)) as f:
            d = json.load(f)
    else:
        d = json.loads(subprocess.run(["git", "show", f"origin/sim-history:props/{name}"], cwd=ROOT,
                                      capture_output=True, text=True, check=True).stdout)
    return d.get("payload", d)


def _dk(stats):
    return sum(w * float(stats.get(k) or 0.0) for k, w in DKW.items())


def dk_shown(dg):
    """What DraftKings' own draft screen shows beside each player of a draft
    group: his average DraftKings points a game (FPPG) and the opponent's rank
    against his position. DraftKings still serves a past group's pool, with
    the figures as they stood for that slate (week 10's and week 11's rows for
    one back read 11.3 and 12.9, a week apart). Public pool data, cached."""
    path = os.path.join(OUT, f"dk_shown_{dg}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    import dk
    d = dk._get(dk._DRAFTABLES.format(dg=dg), timeout=30)
    out = {}
    for x in d.get("draftables") or []:
        at = {a.get("id"): a for a in (x.get("draftStatAttributes") or [])}
        nm = M.DS._norm(x.get("displayName"))
        if not nm or nm in out:
            continue
        try:
            fppg = float((at.get(90) or {}).get("value"))
        except (TypeError, ValueError):
            fppg = None
        try:
            oprk = int((at.get(-2) or {}).get("sortValue"))
        except (TypeError, ValueError):
            oprk = None
        out[nm] = {"fppg": fppg, "oprk": oprk}
    if not out:
        raise RuntimeError(f"no DraftKings pool for draft group {dg}")
    os.makedirs(OUT, exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=0, sort_keys=True)
    return out


def sources(c, pr, shown=None):
    """{name: projection per contest player (nan where the board has none)}
    and the coverage of each. See the module docstring."""
    key = [_knorm(n) for n in c.names]
    has = ~np.isnan(c.proj)
    sl, es = pr.get("sleeper") or {}, pr.get("espn") or {}
    raw = {"sleeper_raw": np.full(c.P, np.nan), "espn_raw": np.full(c.P, np.nan)}
    for i in np.where(has)[0]:
        if c.pos[i] == "DST":
            continue
        for nm, log in (("sleeper_raw", sl), ("espn_raw", es)):
            a = log.get(key[i])
            if a and a.get("stats") and _dk(a["stats"]) > 0:
                raw[nm][i] = _dk(a["stats"])
    both = ~np.isnan(raw["sleeper_raw"]) & ~np.isnan(raw["espn_raw"])
    S = {"board": c.proj.copy()}
    level = {}
    for nm in raw:
        x = raw[nm].copy()
        for g in ("QB", "RB", "WR", "TE"):
            m = both & (c.pos == g)
            # the level per position is the board's: the components both logs keep
            # leave out interceptions, fumbles and the yardage bonuses, which is a
            # position-level offset and not an opinion about a player
            off = float(c.proj[m].mean() - x[m].mean()) if m.any() else 0.0
            level[f"{nm}|{g}"] = round(off, 3)
            x[c.pos == g] += off
        S[nm] = np.where(has, np.where(both, np.maximum(x, 0.0), c.proj), np.nan)
    S["consensus"] = (S["sleeper_raw"] + S["espn_raw"]) / 2.0
    # Kalshi: the frozen ladders' implied means, stat by stat, against Sleeper's
    # frozen mean of the same stat ("td" is rushing + receiving touchdowns). A
    # ladder's implied mean is used only within a factor 2 of Sleeper's: week
    # 10's log holds a 301-yard rushing mean for one back (a thin ladder).
    d_k = np.zeros(c.P)
    kcov = np.zeros(c.P, dtype=bool)
    kal, refused = {}, 0
    for g in (pr.get("games") or {}).values():
        kal.update(g.get("players") or {})
    for i in np.where(has)[0]:
        a, b = kal.get(key[i]), sl.get(key[i])
        if not a or not b or not b.get("stats"):
            continue
        st, bs = a.get("stats") or {}, b["stats"]
        d = 0.0
        for k, w in (("pass_yd", 0.04), ("rush_yd", 0.1), ("rec_yd", 0.1), ("rec", 1.0), ("td", 6.0)):
            if k not in st or st[k].get("mean") is None:
                continue
            ref = (float(bs.get("rush_td") or 0.0) + float(bs.get("rec_td") or 0.0)) if k == "td" else float(bs.get(k) or 0.0)
            km = float(st[k]["mean"])
            if ref <= 0 or not 0.5 <= km / ref <= 2.0:
                refused += 1
                continue
            d += w * (km - ref)
            kcov[i] = True
        d_k[i] = d
    S["kalshi"] = np.where(has, np.maximum(S["sleeper_raw"] + d_k, 0.0), np.nan)
    sal_line = np.full(c.P, np.nan)
    for g in POS:
        m = has & (c.pos == g) & (c.sal > 0)
        if m.sum() >= 3:
            b1, b0 = np.polyfit(c.sal[m], c.proj[m], 1)
            sal_line[m] = np.maximum(b0 + b1 * c.sal[m], 0.0)
    S["salary"] = sal_line
    # DraftKings' displayed average: what a casual entrant sorts by. A player it
    # shows no average for (or zero: he has not played) reads 0, as on the screen.
    S["fppg"] = np.asarray([float(((shown or {}).get(n) or {}).get("fppg") or 0.0) if has[i] else np.nan
                            for i, n in enumerate(c.names)])

    def dd(x, y):
        return round(float(np.abs(S[x][both] - S[y][both]).mean()), 3)
    info = {"board_players": int(has.sum()), "covered_by_both_logs": int(both.sum()), "kalshi_covered": int(kcov.sum()),
            "kalshi_means_refused": int(refused), "level_offsets": level,
            "mean_abs_difference": {"board_vs_sleeper_raw": dd("board", "sleeper_raw"), "board_vs_espn_raw": dd("board", "espn_raw"),
                                    "sleeper_raw_vs_espn_raw": dd("sleeper_raw", "espn_raw")}}
    return S, info


def load(week, args):
    cid, _dg = WEEKS[week]
    ex = M.find_exports(args.paths)
    c = M.load(cid, ex[cid], args.boards, log=lambda *_a: None)
    S, info = sources(c, props(week, args.props), dk_shown(WEEKS[week][1]))
    return c, S, info


# ---- 1. the score deficit ----------------------------------------------------
def _own(A, P):
    return np.bincount(A.ravel(), minlength=P) / len(A)


def _by_pos(c, w, v):
    """Per position: mean count in a lineup, mean salary and mean value of a
    pick, under ownership w."""
    out = {}
    for g in POS:
        m = c.pos == g
        n = float(w[m].sum())
        out[g] = (n, float((w[m] * c.sal[m]).sum() / n) if n else 0.0, float((w[m] * v[m]).sum() / n) if n else 0.0)
    return out


def split(c, o, s, v):
    """allocation / salary / selection of sum (o - s) v over the players of the
    five positions (see the module docstring)."""
    R, Sv = _by_pos(c, o, v), _by_pos(c, s, v)
    alloc = sal = sel = 0.0
    rows = {}
    for g in POS:
        nR, mR, pR = R[g]
        nS, mS, pS = Sv[g]
        m = (c.pos == g) & ((o + s) > 0)
        w = (o[m] + s[m]) / 2.0
        x = c.sal[m].astype(float)
        xm = float((w * x).sum() / w.sum())
        b = float((w * (x - xm) * v[m]).sum() / max(float((w * (x - xm) ** 2).sum()), 1e-9))
        a_g, s_g = (nR - nS) * pS, nR * b * (mR - mS)
        e_g = nR * (pR - pS) - s_g
        alloc, sal, sel = alloc + a_g, sal + s_g, sel + e_g
        rows[g] = {"count_real": round(nR, 3), "count_served": round(nS, 3), "salary_real": round(mR), "salary_served": round(mS),
                   "value_real": round(pR, 2), "value_served": round(pS, 2), "points_per_1000": round(1000 * b, 3),
                   "allocation": round(a_g, 2), "salary": round(s_g, 2), "selection": round(e_g, 2)}
    return {"allocation": round(alloc, 2), "salary": round(sal, 2), "selection": round(sel, 2),
            "total": round(alloc + sal + sel, 2), "by_position": rows}


def deficit(c, A_s, S, perms=20000):
    o, s = _own(c.A, c.P), _own(A_s, c.P)
    f = c.fpts
    has = ~np.isnan(c.proj)
    q = np.where(has, c.proj, 0.0)
    r = f - q
    d = o - s
    D = float((d * f).sum())
    D_ex = float((o[~has] * f[~has]).sum())
    D_proj = float((d[has] * q[has]).sum())
    D_res = float((d[has] * r[has]).sum())
    # the week's luck: shuffle the residuals within position among the players
    # either field plays, and ask how often the public does this well by chance
    rng = np.random.default_rng(SEED)
    act = has & ((o + s) > 0)
    null = np.zeros(perms)
    for g in POS:
        ix = np.where(act & (c.pos == g))[0]
        if len(ix) > 1:
            rr = r[ix]
            for k in range(perms):
                null[k] += float(d[ix] @ rng.permutation(rr))
    sc_r, sc_s = M.scores(c, c.A), M.scores(c, A_s)
    order = np.argsort(-np.abs(d * f))[:15]
    top10 = np.argsort(-o)[:10]
    out = {"mean_real_points": {"real_field": round(float(sc_r.mean()), 2), "served_field": round(float(sc_s.mean()), 2)},
           "D": round(D, 2), "D_check_sum_over_players": round(float(sc_r.mean() - sc_s.mean()), 2),
           "D_proj": round(D_proj, 2), "D_resid": round(D_res, 2), "D_excluded": round(D_ex, 2),
           "D_resid_permutation": {"null_sd": round(float(null.std()), 2), "null_mean": round(float(null.mean()), 2),
                                   "p_at_least_this_large": round(float((null >= D_res).mean()), 4), "perms": perms},
           "split_real_points": split(c, o, s, f), "split_projection": split(c, o, s, q),
           "chalk": {"real_top10_owned_contribution": round(float((d[top10] * f[top10]).sum()), 2),
                     "public_overweights_contribution": round(float((d[d > 0] * f[d > 0]).sum()), 2),
                     "public_underweights_contribution": round(float((d[d < 0] * f[d < 0]).sum()), 2),
                     "sum_own_sq": {"real": round(float((o ** 2).sum()), 4), "served": round(float((s ** 2).sum()), 4)}},
           "quantile_gap_real_minus_served": {k: round(float(np.quantile(sc_r, x) - np.quantile(sc_s, x)), 2)
                                              for k, x in (("P10", 0.1), ("P50", 0.5), ("P90", 0.9), ("P99", 0.99))},
           "score_sd": {"real": round(float(sc_r.std()), 2), "served": round(float(sc_s.std()), 2)},
           "players_largest_contribution": [
               {"name": c.names[i], "pos": str(c.pos[i]), "salary": int(c.sal[i]), "own_real_pct": round(100 * o[i], 1),
                "own_served_pct": round(100 * s[i], 1), "proj": (round(float(c.proj[i]), 1) if has[i] else None),
                "sleeper_raw": (round(float(S["sleeper_raw"][i]), 1) if has[i] else None),
                "espn_raw": (round(float(S["espn_raw"][i]), 1) if has[i] else None),
                "fpts": round(float(f[i]), 1), "contribution": round(float(d[i] * f[i]), 2)} for i in order]}
    # does another source already like the public's players more than Sleeper does?
    out["source_shift"] = {k: round(float((d[has] * (S[k][has] - q[has])).sum()), 2) for k in ("sleeper_raw", "espn_raw", "consensus", "kalshi", "salary", "fppg")}
    live = has & (q >= 3.0)
    out["source_accuracy_proj_3_plus"] = {k: {"mae": round(float(np.abs(S[k][live] - f[live]).mean()), 3),
                                              "corr": round(float(np.corrcoef(S[k][live], f[live])[0, 1]), 4),
                                              "bias": round(float((S[k][live] - f[live]).mean()), 3), "n": int(live.sum())}
                                          for k in S}
    return out


# ---- 4. salary efficiency and optimizer behaviour ---------------------------
def one_swap_gain(c, A, x, chunk=20000):
    """Per entry: the most projection one same-position swap that fits the
    salary left would add (0 = one-swap optimal). Entries holding a player
    with no projection under x are nan."""
    ok = ~np.isnan(x[A]).any(axis=1) & c.in_pool[A].all(axis=1)
    gain = np.full(len(A), np.nan)
    cand = {g: np.where((c.pos == g) & ~np.isnan(x) & (c.sal > 0))[0] for g in POS}
    rows = np.where(ok)[0]
    for a in range(0, len(rows), chunk):
        ix = rows[a:a + chunk]
        B = A[ix]
        left = M.CAP - c.sal[B].sum(axis=1)
        best = np.zeros(len(B))
        for j in range(9):
            pj = B[:, j]
            for g in POS:
                m = c.pos[pj] == g
                if not m.any():
                    continue
                cg = cand[g]
                room = (c.sal[cg][None, :] <= (c.sal[pj[m]] + left[m])[:, None])
                room &= ~(cg[None, None, :] == B[m][:, :, None]).any(axis=1)
                gn = np.where(room, x[cg][None, :] - x[pj[m]][:, None], -np.inf).max(axis=1)
                best[m] = np.maximum(best[m], np.maximum(gn, 0.0))
        gain[ix] = best
    return gain


def behaviour(c, A, S, users=None, per_entry=None, opt=None):
    """How a field builds: the cap, the stack shapes, and how often a lineup
    is one-swap optimal under each source, overall and (real field) by the
    entrant's size and by the lineup's copies."""
    st = M.structure(c, A)
    ok = c.in_pool[A].all(axis=1)
    sal = c.sal[A].sum(axis=1)
    isqb = c.pos[A] == "QB"
    qb = A[np.arange(len(A)), isqb.argmax(axis=1)]
    catch = (np.isin(c.pos[A], ("WR", "TE")) & (c.team[A] == c.team[qb][:, None])).sum(axis=1)
    gains = {k: one_swap_gain(c, A, S[k]) for k in SOURCES}
    gap = {}
    for k in SOURCES:
        x = S[k][A].sum(axis=1)
        gap[k] = (opt[k] - x) if opt else None

    def block(m):
        m = m & ok
        out = {"entries": int(m.sum()), "at_cap_pct": round(100 * float((sal[m] == M.CAP).mean()), 2),
               "left_100_300_pct": round(100 * float(((sal[m] < M.CAP) & (sal[m] >= M.CAP - 300)).mean()), 2),
               "left_400_1000_pct": round(100 * float(((sal[m] < M.CAP - 300) & (sal[m] >= M.CAP - 1000)).mean()), 2),
               "left_1100_plus_pct": round(100 * float((sal[m] < M.CAP - 1000).mean()), 2),
               "salary_mean": round(float(sal[m].mean()), 1),
               "naked_qb_pct": round(100 * float((catch[m] == 0).mean()), 2),
               "double_stack_pct": round(100 * float((catch[m] >= 2).mean()), 2)}
        for k, g in gains.items():
            gg = g[m]
            gg = gg[~np.isnan(gg)]
            out[f"one_swap_optimal_{k}_pct"] = round(100 * float((gg <= 1e-9).mean()), 2) if len(gg) else None
            out[f"one_swap_gain_{k}_mean"] = round(float(gg.mean()), 3) if len(gg) else None
        for k, g in gap.items():
            if g is not None:
                gg = g[m]
                gg = gg[~np.isnan(gg)]
                out[f"gap_to_optimum_{k}_median"] = round(float(np.median(gg)), 2) if len(gg) else None
        return out
    out = {"all": block(np.ones(len(A), dtype=bool)), "structure": st}
    if users is not None:
        cnt = np.bincount(users)[users]
        out["by_entrant_entries"] = {M._bname(lo, hi): block((cnt >= lo) & (cnt <= hi)) for lo, hi in USER_CLASSES}
    if per_entry is not None:
        out["by_copies"] = {M._bname(lo, hi): block((per_entry >= lo) & (per_entry <= hi)) for lo, hi in COPY_CLASSES}
    return out


# ---- 2/3. the noisy optimizer on a source ------------------------------------
def optimizer_pool(c, x, sigma, tau, n, seed, workers=4):
    """n exact optimizer lineups on source x at randomness sigma and passing
    shock tau, in the contest's player ids (sorted nine)."""
    import dfs_tourney as T
    ix = np.where(~np.isnan(x) & (x > 0) & (c.sal > 0) & np.isin(c.pos, POS))[0]
    rng = np.random.default_rng((SEED, int(round(1000 * sigma)), int(round(1000 * tau)), int(seed)))
    X = x[ix][:, None] * (1.0 + sigma * rng.standard_normal((len(ix), n)))
    if tau > 0:
        teams = sorted(set(c.team[ix]))
        tcode = np.asarray([teams.index(t) for t in c.team[ix]])
        H = 1.0 + tau * rng.standard_normal((len(teams), n))
        passing = np.isin(c.pos[ix], ("QB", "WR", "TE"))
        X = np.where(passing[:, None], X * H[tcode], X)
    lineups, _v = T.optimal_lineups(X.astype(np.float32), [int(v) for v in c.sal[ix]], [str(p) for p in c.pos[ix]],
                                    workers=workers)
    return np.sort(ix[np.asarray([lu for lu in lineups if lu], dtype=np.int64)].astype(np.int32), axis=1)


def target_scores(c, t, o, f):
    """A per-player ownership target against the real field's."""
    top40 = np.argsort(-o, kind="stable")[:40]
    return {"top40_mae": round(100 * float(np.abs(t[top40] - o[top40]).mean()), 3),
            "all_mae": round(100 * float(np.abs(t - o)[(t + o) > 0].mean()), 3),
            "sum_own_sq": round(float((t ** 2).sum()), 4), "top_owned_pct": round(100 * float(t.max()), 1),
            "mean_real_points": round(float((t * f).sum()), 2),
            "corr": round(float(np.corrcoef(t, o)[0, 1]), 4)}


def lineup_tests(c, B, real_rows, real_hash_counts):
    """A pool of optimizer lineups against the real field's exact builds: how
    many of the real 20 most-copied are among the pool's 200 most frequent,
    and how many real entries hold one of the pool's 200 most frequent."""
    rf, _rc = M._hash_words(c.P)
    h = rf[B].sum(axis=1, dtype=np.uint64)
    u, cnt = np.unique(h, return_counts=True)
    top = u[np.argsort(-cnt, kind="stable")[:200]]
    tops = set(top.tolist())
    found = sum(1 for row, _n in real_rows if int(rf[np.asarray(row)].sum(dtype=np.uint64)) in tops)
    anyc = sum(1 for row, _n in real_rows if int(rf[np.asarray(row)].sum(dtype=np.uint64)) in set(u.tolist()))
    real_in_top = sum(real_hash_counts.get(int(k), 0) for k in top.tolist())
    most = int(u[np.argmax(cnt)])
    return {"real_top20_in_pool_top200": int(found), "real_top20_anywhere_in_pool": int(anyc),
            "real_entries_holding_a_pool_top200_lineup": int(real_in_top),
            "pool_distinct": int(len(u)), "pool_most_frequent_share_pct": round(100 * float(cnt.max()) / len(B), 3),
            "real_copies_of_pool_most_frequent": int(real_hash_counts.get(most, 0))}


def run_diagnose(args, log=print):
    os.makedirs(OUT, exist_ok=True)
    for week in args.weeks:
        t0 = time.time()
        c, S, info = load(week, args)
        A_s, _fx, rep = M.classic_replica(c, c.N)
        log(f"[cl2] week {week}: {c.N:,} entries, replica in {time.time() - t0:.0f}s")
        dup, per_entry, _inv = M.duplication(c.A, "classic", c.P)
        opt = {}
        for k in SOURCES:
            cc = M.Ctx()
            cc.pos, cc.sal, cc.proj = c.pos, c.sal, S[k]
            v, ids = M.classic_optimum(cc)
            opt[k] = float(v)
        out = {"week": week, "contest": WEEKS[week][0], "draft_group": WEEKS[week][1], "entries": int(c.N),
               "role": "development (both weeks; n = 2 slates)", "sources": info, "replica": rep,
               "optimum_projection": {k: round(v, 2) for k, v in opt.items()},
               "deficit": deficit(c, A_s, S),
               "behaviour": {"real": behaviour(c, c.A, S, users=c.users, per_entry=per_entry, opt=opt),
                             "served": behaviour(c, A_s, S, opt=opt)}}
        top = dup["_top_rows"]
        out["real_top20_gap_to_optimum_median"] = {
            k: round(float(np.median([opt[k] - np.nansum(S[k][np.asarray(r)]) for r, _n in top])), 2) for k in opt}
        with open(os.path.join(OUT, f"diagnose_w{week}.json"), "w") as f:
            json.dump(out, f, indent=1)
        log(f"[cl2] week {week}: D {out['deficit']['D']} = proj {out['deficit']['D_proj']} + resid "
            f"{out['deficit']['D_resid']} + excluded {out['deficit']['D_excluded']} ({time.time() - t0:.0f}s)")
    return 0


def run_targets(args, log=print):
    """Every (source, sigma, tau) noisy-optimizer share against the real
    ownership, and each pool against the real exact builds. DEVELOPMENT."""
    os.makedirs(OUT, exist_ok=True)
    for week in args.weeks:
        t0 = time.time()
        c, S, _info = load(week, args)
        o, f = _own(c.A, c.P), c.fpts
        b = {p["name"]: p for p in c.proj_players}
        base = {k: np.asarray([float((b.get(n) or {}).get(k) or 0.0) / 100.0 for n in c.names])
                for k in ("field_pct", "opt_pct", "casual_pct")}
        dup, _per, _inv = M.duplication(c.A, "classic", c.P)
        rf, _rc = M._hash_words(c.P)
        hu, hc = np.unique(rf[c.A].sum(axis=1, dtype=np.uint64), return_counts=True)
        rhc = dict(zip(hu.tolist(), hc.tolist()))
        # a cell is ~45 s: each is checkpointed, so a stopped run resumes
        ck_path = os.path.join(args.scratch, f"cl2_targets_w{week}.pkl")
        cells, shares = [], {}
        if os.path.exists(ck_path):
            with open(ck_path, "rb") as fh:
                cells, shares = pickle.load(fh)
        for src in SOURCES + (("kalshi", "salary", "fppg") if args.all_sources else ()):
            for tau in TAUS:
                for sg in SIGMAS:
                    if src in ("kalshi", "salary", "fppg") and (tau > 0 or sg != 0.25):
                        continue
                    if f"{src}|{sg}|{tau}" in shares:
                        continue
                    B = optimizer_pool(c, S[src], sg, tau, N_SHARE, week, workers=args.workers)
                    t = _own(B, c.P)
                    shares[f"{src}|{sg}|{tau}"] = t
                    st = M.structure(c, B)
                    cells.append({"source": src, "sigma": sg, "tau": tau, **target_scores(c, t, o, f),
                                  **lineup_tests(c, B, dup["_top_rows"], rhc), "stack_pct": st["stack_pct"],
                                  "bring_back_pct": st["bring_back_pct"], "salary_at_cap_pct": st["salary_at_cap_pct"]})
                    with open(ck_path + ".tmp", "wb") as fh:
                        pickle.dump((cells, shares), fh)
                    os.replace(ck_path + ".tmp", ck_path)
                    log(f"[cl2] week {week} {src} sigma {sg} tau {tau}: top40 MAE {cells[-1]['top40_mae']} "
                        f"sum own^2 {cells[-1]['sum_own_sq']} ({time.time() - t0:.0f}s)")
        out = {"week": week, "role": "development", "n_share": N_SHARE,
               "real": {"sum_own_sq": round(float((o ** 2).sum()), 4), "top_owned_pct": round(100 * float(o.max()), 1),
                        "mean_real_points": round(float((o * f).sum()), 2)},
               "board": {k: target_scores(c, v, o, f) for k, v in base.items()}, "cells": cells}
        with open(os.path.join(OUT, f"targets_w{week}.json"), "w") as fh:
            json.dump(out, fh, indent=1)
        np.savez_compressed(os.path.join(args.scratch, f"cl2_shares_w{week}.npz"), names=np.asarray(c.names),
                            real=o, **{f"base_{k}": v for k, v in base.items()},
                            **{"t_" + k.replace("|", "_").replace(".", "p"): v for k, v in shares.items()})
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=("diagnose", "targets"))
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--boards", default=None)
    ap.add_argument("--props", default=None)
    ap.add_argument("--weeks", type=int, nargs="*", default=sorted(WEEKS))
    ap.add_argument("--all-sources", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scratch", default=os.environ.get("TMPDIR", "/tmp"))
    args = ap.parse_args()
    return (run_diagnose if args.mode == "diagnose" else run_targets)(args)


if __name__ == "__main__":
    sys.exit(main())
