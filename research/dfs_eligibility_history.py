"""The eligibility modes and the house rules on REAL results: every past board on sim-history, its real hindsight
optimum (DESCRIPTIVE; 2026-11-27, research/reports/dfs_eligibility.md).

Production boards are unseeded and their worlds are not kept, so no past board can be rebuilt with identical inputs
and no mode's CHOSEN lineups can be replayed. What the record does allow, exactly: each board's own pool (its
players, salaries, depth tags, field extras and the gate's exclusions, as built), DraftKings' pool for the draft
group (salaries of the gated players a board does not list), and Sleeper's box score of the week (DraftKings points:
dfslog._nfl_points for offense and defense; kickers 3 / 4 / 5 a field goal by distance and 1 an extra point). Per
slate: the best lineup on the REAL points that each variant may enter, against the best DraftKings itself allows over
its whole pool. One world per slate, the one that happened.

Showdown (DraftKings' showdown rules; every lineup enumerated): the variants of research/dfs_eligibility_compare.py
(conservative, adaptive, unrestricted, each house rule lifted alone), the reference being every available DK player.
Classic (DraftKings' classic rules, production's exact DP dfs_tourney.optimal_lineups on one world): conservative (the
board's own players less its field extras), adaptive (+ the extras dfs_eligibility admits), unrestricted (+ every
extra), DK (every available player in DraftKings' pool); the classic house rules (stack, team cap, punts, defense)
are then checked on the unrestricted optimum.

Adaptive's verdict per slate uses only what was known before it: Sleeper's projection for that week (the board's own
for the players it lists, else Sleeper's projection rows for the week, read now -- Sleeper serves a past week's last
pre-game projection) and the box history of strictly earlier weeks (nfl_usage.history).

    python3 research/dfs_eligibility_history.py      # writes research/data/dfs_eligibility/history.json
"""
import json
import os
import sys
import time

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("VIGIL_NO_BG", "1")

import numpy as np  # noqa: E402

SEASON = 2026
OUT = os.path.join(_ROOT, "research", "data", "dfs_eligibility", "history.json")
# the slate this study's case is: its game is never read here (the board's lock is 2026-11-27 20:00 UTC)
EXCLUDE = {157076}


def k_points(st):
    g = lambda k: float(st.get(k, 0) or 0)  # noqa: E731
    return 3 * (g("fgm_0_19") + g("fgm_20_29") + g("fgm_30_39")) + 4 * g("fgm_40_49") + 5 * g("fgm_50p") + g("xpm")


_ACT = {}


def actuals(week):
    """{norm name: DK points} for the week, offense, defenses (dfslog's keys) and kickers."""
    if week in _ACT:
        return _ACT[week]
    import dfslog
    import nfl_dfs_sim
    url = dfslog._SLEEPER_STATS.format(season=SEASON, week=week)
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        url += f"&position[]={pos}"
    rows = nfl_dfs_sim._get(url + "&order_by=pts_ppr")
    out = {}
    for r in rows or []:
        p = r.get("player") or {}
        st = r.get("stats") or {}
        pos = p.get("position")
        if pos == "DEF":
            v = dfslog._nfl_points("DEF", st)
            out[f"dst:{dfslog._norm(p.get('last_name'))}"] = v
            out[f"dst:{(r.get('team') or '').lower()}"] = v
            continue
        nm = dfslog._norm(f"{p.get('first_name', '')} {p.get('last_name', '')}")
        if nm:
            out[nm] = round(k_points(st), 2) if pos == "K" else dfslog._nfl_points(pos, st)
    _ACT[week] = out
    return out


def real(acts, name, pos, team):
    import dfslog
    v = dfslog._lookup("nfl", acts, {"name": name, "pos": pos, "team": team})
    return None if v is None else float(v)


_PROJ = {}


def week_projections(week):
    """{norm name: (DK-ish projected points from Sleeper's PPR row, player id, team)} -- Sleeper's projection rows."""
    if week in _PROJ:
        return _PROJ[week]
    import dfs_eligibility as E
    ctx = E.week_context(SEASON, week)
    _PROJ[week] = ctx
    return ctx


def verdict(board, week, kept, gated, mode):
    import dfs_eligibility as E
    ctx = week_projections(week)
    return E.assess(kept, gated, mode, ctx, E.load_model())


def best_showdown(ents, pts, ours, active, role_names=()):
    """The best lineup on ONE world's points that the variant may enter: captain by captain, a branch-and-bound
    over FLEX sorted by points (the bound: the next best points, constraints ignored), every house rule in `active`
    checked as the lineup is built (the counts of nfl_dfs._sd_allowed and the board's cpt_ok). Exact; the slate's
    whole DraftKings pool is too big to enumerate (55 players, tens of millions of lineups)."""
    import nfl_dfs
    n = len(ents)
    pos = [e["pos"] for e in ents]
    team = [e["team"] for e in ents]
    sal = [int(e["salary"]) for e in ents]
    csal = [int(e["cpt_salary"]) for e in ents]
    tag = [(e.get("depth") or "").split("·")[0] for e in ents]
    roles = set(role_names or ())
    role = [(not tag[j]) or tag[j] in nfl_dfs._SD_PUNT_ROLES or ents[j]["name"] in roles for j in range(n)]
    ok = [ents[j]["name"] in ours for j in range(n)]
    rich = sum(1 for j in range(n) if ok[j] and pos[j] in nfl_dfs._SD_GPP_CPT_POS
               and csal[j] >= nfl_dfs._SD_MIN_CPT_SALARY) >= 3
    best = [-1e9, None]
    order = sorted((j for j in range(n) if ok[j]), key=lambda j: -pts[j])
    for c in order:
        if "kdst_cpt" in active and pos[c] not in nfl_dfs._SD_GPP_CPT_POS:
            continue
        if "cpt_salary" in active and rich and csal[c] < nfl_dfs._SD_MIN_CPT_SALARY:
            continue
        budget = nfl_dfs.CAP - csal[c]
        cands = [j for j in order if j != c and sal[j] <= budget]
        P = [pts[j] for j in cands]
        suf = [0.0] * (len(cands) + 6)
        base = 1.5 * pts[c]
        if base + sum(P[:5]) <= best[0] + 1e-9:
            continue
        kd0 = 1 if pos[c] in ("K", "DST") else 0
        pu0 = 1 if sal[c] <= nfl_dfs._SD_PUNT_SALARY else 0
        del suf

        def dfs(start, picked, used, tot, kd, pu, te):
            k = len(picked)
            if k == 5:
                if len({team[c]} | {team[j] for j in picked}) >= 2 and tot > best[0] + 1e-9:
                    best[0], best[1] = tot, (c, tuple(picked))
                return
            need = 5 - k
            for i in range(start, len(cands) - need + 1):
                if tot + sum(P[i:i + need]) <= best[0] + 1e-9:
                    return                              # sorted: no later start can do better
                j = cands[i]
                if used + sal[j] > budget:
                    continue
                if pos[j] == "DST" and "dst_vs_cpt" in active and pos[c] != "DST" and team[j] != team[c]:
                    continue
                kd2 = kd + (1 if pos[j] in ("K", "DST") else 0)
                if pos[j] in ("K", "DST") and "kdst_max" in active and kd2 > nfl_dfs._SD_MAX_KDST:
                    continue
                punt = sal[j] <= nfl_dfs._SD_PUNT_SALARY
                pu2 = pu + (1 if punt else 0)
                if punt and "punt_max" in active and pu2 > nfl_dfs._SD_MAX_PUNTS:
                    continue
                if punt and "punt_role" in active and not role[j]:
                    continue
                te2 = te
                if pos[j] == "TE":
                    te2 = te + (team[j],)
                    if "te_team" in active and te2.count(team[j]) > nfl_dfs._SD_MAX_TE_PER_TEAM:
                        continue
                dfs(i + 1, picked + [j], used + sal[j], tot + P[i], kd2, pu2, te2)

        dfs(0, [], 0, base, kd0, pu0, (team[c],) if pos[c] == "TE" else ())
    return best


def showdown(board, acts, dkp):
    import dfs_eligibility_compare as CMP
    import dfs_eligibility as E
    dk_by = {p["name"]: p for p in dkp}
    ents, have = [], set()
    for p in board["players"]:
        ents.append({"name": p["name"], "pos": p["pos"], "team": p["team"], "salary": int(p["salary"]),
                     "cpt_salary": int(p["cpt_salary"]), "proj": float(p["proj"]), "depth": p.get("depth"),
                     "gated": bool(p.get("field_only")), "kind": "board"})
        have.add(p["name"])
    for d in board.get("excluded") or []:
        if "by depth chart" in (d.get("why") or "") and d["name"] not in have and d["name"] in dk_by:
            k = dk_by[d["name"]]
            ents.append({"name": d["name"], "pos": d["pos"], "team": d["team"], "salary": int(k["salary"]),
                         "cpt_salary": int(k["cpt_salary"]), "proj": None, "depth": d["why"].split(" ")[0],
                         "gated": True, "kind": "gated"})
            have.add(d["name"])
    for k in dkp:
        if k["name"] in have or not k.get("available", True):
            continue
        pos = (k.get("position") or "").upper().split("/")[0]
        ents.append({"name": k["name"], "pos": pos, "team": k.get("team"), "salary": int(k["salary"]),
                     "cpt_salary": int(k["cpt_salary"]), "proj": None, "depth": None, "gated": True,
                     "kind": "dk_only"})
        have.add(k["name"])
    pts = [real(acts, e["name"], e["pos"], e["team"]) for e in ents]
    unknown = [e["name"] for e, v in zip(ents, pts) if v is None]
    pts = [0.0 if v is None else float(v) for v in pts]
    week = board["slate"]["week"]
    kept = [e for e in ents if e["kind"] == "board" and not e["gated"]]
    proj_of = {e["name"]: e["proj"] for e in ents if e["proj"] is not None}
    ctx = week_projections(week)
    gated_rows = []
    for d in board.get("excluded") or []:
        if "by depth chart" in (d.get("why") or ""):
            pj = proj_of.get(d["name"])
            if pj is None:
                pj = _sleeper_proj(week, d["name"])
            gated_rows.append(dict(d, proj=pj))
    v = E.assess(kept, gated_rows, "adaptive", ctx, E.load_model())
    adm = set(v["admitted"])
    names = [e["name"] for e in ents]
    base = {e["name"] for e in kept}
    extras = {e["name"] for e in ents if e["kind"] == "board" and e["gated"]}
    gated_all = {e["name"] for e in ents if e["kind"] in ("board", "gated") and e["gated"]}
    allr = set(CMP.RULES)
    variants = {"conservative": (base, allr, ()), "adaptive": (base | (adm & extras), allr, adm & extras),
                "adaptive_all": (base | adm, allr, adm), "unrestricted": (base | extras, allr - {"punt_role"}, ()),
                "unrestricted_all": (base | gated_all, allr - {"punt_role"}, ())}
    for r in CMP.RULES:
        variants[f"lift:{r}"] = (base, allr - {r}, ())
    variants["lift:all"] = (base, set(), ())
    variants["dk_all"] = (set(names), set(), ())
    best = {}
    for vn, (ours, active, role) in variants.items():
        val, lu = best_showdown(ents, pts, ours, active, role)
        best[vn] = None if lu is None else {"points": round(val, 2), "captain": names[lu[0]],
                                            "flex": [names[j] for j in lu[1]]}
    # the search against production's enumeration and rule masks, on the conservative pool (exactness check)
    import dfs_tourney
    import nfl_dfs
    sub = [e for e in ents if e["name"] in base]
    idx, W, _ = dfs_tourney.enumerate_showdown(sub, nfl_dfs.CAP, cpt_mult=nfl_dfs.CPT_MULT)
    S = W.astype(np.float64) @ np.asarray([pts[names.index(e["name"])] for e in sub])
    m = CMP.allowed(CMP.rule_masks(sub, idx, base), ents=sub, idx=idx)
    enum_best = round(float(S[m].max()), 2) if m.any() else None
    return {"best": best, "admitted": sorted(adm), "extras": sorted(extras),
            "enumeration_check": {"search": (best["conservative"] or {}).get("points"), "enumeration": enum_best},
            "unknown": unknown[:12], "n_unknown": len(unknown), "players": len(ents),
            "flags": v["flags"], "info": {k: v["info"][k] for k in sorted(adm | extras) if k in v["info"]}}


_SPROJ = {}


def _sleeper_proj(week, name):
    """Sleeper's projected DraftKings points for a name in a past week (pts_ppr less half a point a reception, the
    offense-only DK/PPR difference: DK is full PPR, so it is pts_ppr plus the yardage bonuses, read as pts_ppr)."""
    import nfl_adp
    if week not in _SPROJ:
        import nfl_dfs_sim
        q = "&".join(f"position[]={p}" for p in ("QB", "RB", "WR", "TE"))
        rows = nfl_dfs_sim._get(f"{nfl_dfs_sim._PROJ.format(season=SEASON, week=week)}?season_type=regular&{q}")
        m = {}
        for r in rows or []:
            p = r.get("player") or {}
            nm = nfl_adp._norm(f"{p.get('first_name', '')} {p.get('last_name', '')}")
            m.setdefault(nm, float((r.get("stats") or {}).get("pts_ppr") or 0.0))
        _SPROJ[week] = m
    return _SPROJ[week].get(nfl_adp._norm(name))


def classic(board, acts, dkp):
    import dfs_tourney
    import dfs_eligibility as E
    pl = [p for p in board["players"] if p["pos"] in ("QB", "RB", "WR", "TE", "DST")]
    have = {p["name"] for p in pl}
    rows = [dict(p, kind="board") for p in pl]
    for k in dkp:
        pos = (k.get("position") or "").upper().split("/")[0]
        if k["name"] in have or not k.get("available", True) or pos not in ("QB", "RB", "WR", "TE", "DST"):
            continue
        rows.append({"name": k["name"], "pos": pos, "team": k.get("team"), "salary": int(k["salary"]),
                     "field_only": True, "kind": "dk_only", "game": k.get("game")})
        have.add(k["name"])
    pts = np.asarray([real(acts, r["name"], r["pos"], r["team"]) or 0.0 for r in rows], dtype=np.float32)
    unknown = sum(1 for r in rows if real(acts, r["name"], r["pos"], r["team"]) is None)
    sal = np.asarray([int(r["salary"]) for r in rows])
    pos = [r["pos"] for r in rows]
    week = board["slate"]["week"]
    kept = [r for r in rows if r["kind"] == "board" and not r.get("field_only")]
    extras = [r for r in rows if r["kind"] == "board" and r.get("field_only")]
    gated_rows = [{"name": r["name"], "pos": r["pos"], "team": r["team"], "proj": r.get("proj"),
                   "why": f"{r.get('depth') or r['pos']} by depth chart"} for r in extras]
    v = E.assess(kept, gated_rows, "adaptive", week_projections(week), E.load_model())
    adm = set(v["admitted"])
    pools = {"conservative": [i for i, r in enumerate(rows) if r["kind"] == "board" and not r.get("field_only")],
             "adaptive": [i for i, r in enumerate(rows) if r["kind"] == "board" and (not r.get("field_only")
                                                                                       or r["name"] in adm)],
             "unrestricted": [i for i, r in enumerate(rows) if r["kind"] == "board"],
             "dk_all": list(range(len(rows)))}
    best = {}
    for vn, ii in pools.items():
        ii = np.asarray(ii)
        L, val = dfs_tourney.optimal_lineups(pts[ii][:, None], sal[ii], [pos[i] for i in ii], prune=False)
        if not L or not L[0]:
            best[vn] = None
            continue
        lu = [int(ii[j]) for j in L[0]]
        best[vn] = {"points": round(float(val[0]), 2), "names": [rows[j]["name"] for j in lu],
                    "gated_in": [rows[j]["name"] for j in lu if rows[j].get("field_only")]}
    # the classic house rules on the unrestricted optimum (dfs_tourney.classic_allowed's checks)
    rules = None
    if best.get("unrestricted"):
        lu = [next(r for r in rows if r["name"] == n) for n in best["unrestricted"]["names"]]
        teams = [r["team"] for r in lu]
        qb = next(r for r in lu if r["pos"] == "QB")
        dst = next(r for r in lu if r["pos"] == "DST")
        dst_opp = [r for r in lu if r["pos"] != "DST" and r["team"] != dst["team"]
                   and (r.get("game") or "") == (dst.get("game") or "") and r["pos"] in ("QB", "RB")]
        rules = {"team_cap_3": max(teams.count(t) for t in set(teams)) <= dfs_tourney.CL_MAX_PER_TEAM,
                 "qb_stack": any(r["team"] == qb["team"] and r["pos"] in ("WR", "TE") for r in lu),
                 "dst_not_vs_qb_or_backs": not dst_opp,
                 "punts": sum(1 for r in lu if r["pos"] != "DST" and r["salary"] < dfs_tourney._CL_PUNT_SALARY) <= 1}
    return {"best": best, "admitted": sorted(adm), "extras": len(extras), "unknown": unknown, "players": len(rows),
            "rules_on_unrestricted_optimum": rules,
            "flags": {k: v["flags"][k] for k in sorted(adm) if k in v["flags"]}}


def main():
    import dk
    bdir = sys.argv[1]
    out = {"showdown": {}, "classic": {}, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for fn in sorted(os.listdir(bdir)):
        if not fn.startswith("tourney-1") or fn.endswith("-pick.json"):
            continue
        b = json.load(open(os.path.join(bdir, fn)))
        dg = int(b["draft_group_id"])
        ko = b.get("kickoff_ts") or min((dk_iso(c.get("starts")) for c in (b.get("contests") or [])
                                         if c.get("starts")), default=None)
        if dg in EXCLUDE or ko is None or ko > time.time() - 6 * 3600:
            continue                                  # not played (or not final), or the case study's own game
        acts = actuals(int(b["slate"]["week"]))
        dkp = dk.players(dg) or []
        try:
            got = showdown(b, acts, dkp) if b.get("kind") == "showdown" else classic(b, acts, dkp)
        except Exception as e:                        # one slate's failure is reported, never the whole study's
            got = {"error": f"{type(e).__name__}: {e}"}
        got.update(week=b["slate"]["week"], teams=b["slate"].get("teams") if b.get("kind") == "showdown" else None)
        out["showdown" if b.get("kind") == "showdown" else "classic"][str(dg)] = got
        print(dg, b.get("kind"), {k: (v or {}).get("points") for k, v in (got.get("best") or {}).items()},
              got.get("admitted"), got.get("error"), flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print("wrote", OUT)


def dk_iso(s):
    import nfl_dfs
    return nfl_dfs.iso_ts(s)


if __name__ == "__main__":
    main()
