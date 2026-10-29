"""The matchup model's modelling frame: one row per player-game, 2023-2026,
with every feature computed by the PRODUCTION code (nfl_matchup.features, as
of the game's week), Sleeper's line (the post-week REVISED one for every
season, plus the FROZEN pre-kickoff capture for 2026 wherever one exists),
the official box score the markets settle on, the game's closing line, and
the production DFS baseline (Sleeper + nfl_usage, replayed with the shipped
nfl_usage.adjust). RESEARCH (numpy is not needed here; nothing in production
reads the frame).

    export DEEP_CACHE_DIR=<dir with nfl_matchup_<season>.pkl 2019-2026>  VIGIL_NO_BG=1
    python3 -m research.nfl_matchup_frame --out <work>/frame.csv.gz --defhist <work>/def_history.json

Inputs (all read-only): research/data/nfl_matchup/dataset_2023_2026.csv.gz
(the row universe, keys and Sleeper's revised line), the nflverse stores in
the deep store, research/data/player_weeks_2022_2025.csv.gz (Sleeper's box
2022-2025), <sleeper dir>/stats_2026_<w>.json and proj_2026_<w>.json (the
2026 box and the revised line's names), and the frozen captures listed in
FROZEN_SOURCES plus the sim-history props logs (props_w06.json).

Why the outcome is Sleeper's box, not nflverse pbp: Kalshi settles on the
official stat line, which counts QB kneels as carries (the pbp store drops
them: carries 0.19 a game lower, rushing yards 0.20 higher,
research/reports/nfl_matchup_data.md 4.3). pbp is the fallback only for a
player who played and has no box row.

Frozen baselines (the addendum's rule C): a 2026 row's frozen line is the
latest capture whose CONTENT time (Sleeper's updated_at, or the props log's
own pass time) is before his game's kickoff; anything stamped at or after
kickoff is never used. Rows exist for every frozen-projected player even
when the revised feed dropped him (a scratch the revision erased is exactly
the bias a frozen baseline must keep).
"""
import argparse
import collections
import csv
import datetime
import glob
import gzip
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("VIGIL_NO_BG", "1")

import kalshi_nfl                        # noqa: E402
import nfl_matchup as M                  # noqa: E402
import nfl_matchup_data as D             # noqa: E402
import nfl_matchup_profiles as P         # noqa: E402
import nfl_usage                         # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATASET = os.path.join(HERE, "data", "nfl_matchup", "dataset_2023_2026.csv.gz")
PLAYER_WEEKS = os.path.join(HERE, "data", "player_weeks_2022_2025.csv.gz")
FIELDS = ("rec_tgt", "rec", "rec_yd", "rec_td", "rush_att", "rush_yd", "rush_td", "pass_att", "pass_cmp",
          "pass_yd", "pass_td", "pass_int", "fum_lost")
SEASONS = (2023, 2024, 2025, 2026)
TEST_MAX_WEEK = 6          # 2026 weeks 1-6 are every completed week in the stores (week 7 not played)
FROZEN_SOURCES = [os.path.join(REPO, "research", "data", "feeds", "proj_2026_1.json"),
                  os.path.join(REPO, "research", "data", "feeds", "proj_2026_2.json")] + \
    sorted(glob.glob(os.path.join(REPO, "research", "data", "prospective", "*", "*", "proj_2026_[0-9].json")))
SL2NV = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX"}     # Sleeper / Kalshi -> nflverse team codes


_SLEEPER_GSIS = {}


def log(msg):
    print(msg, flush=True)


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def kick_ts(g):
    from zoneinfo import ZoneInfo
    try:
        dt = datetime.datetime.strptime(g.get("kick") or "", "%m/%d/%y, %H:%M:%S")
    except ValueError:
        return None
    return int(dt.replace(tzinfo=ZoneInfo("America/New_York")).timestamp())


# ---- outcomes --------------------------------------------------------------------
def sleeper_box(sleeper_dir):
    """{(season, week, sleeper id): official line} 2023-2026 (Sleeper's box)."""
    out = {}
    with gzip.open(PLAYER_WEEKS, "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            s = int(r["season"])
            if s not in SEASONS or r["has_act"] != "1":
                continue
            ln = {k: _f(r.get(f"act_{k}")) or 0.0 for k in FIELDS}
            ln["two_pt"] = sum(_f(r.get(f"act_{k}")) or 0.0 for k in ("pass_2pt", "rush_2pt", "rec_2pt"))
            ln["ret_td"] = (_f(r.get("act_st_td")) or 0.0) + (_f(r.get("act_fum_rec_td")) or 0.0)
            ln["gp"] = _f(r.get("act_gp")) or 0.0
            ln["team"] = r["team"]
            out[(s, int(r["week"]), r["player_id"])] = ln
    for w in range(1, TEST_MAX_WEEK + 1):
        p = os.path.join(sleeper_dir, f"stats_2026_{w}.json")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as fh:
            rows = json.load(fh)
        for r in rows:
            st = r.get("stats") or {}
            pid = str(r.get("player_id") or "")
            if not pid:
                continue
            ln = {k: float(st.get(k) or 0.0) for k in FIELDS}
            ln["two_pt"] = sum(float(st.get(k) or 0.0) for k in ("pass_2pt", "rush_2pt", "rec_2pt"))
            ln["ret_td"] = float(st.get("st_td") or 0.0) + float(st.get("fum_rec_td") or 0.0) + \
                float(st.get("kr_td") or 0.0) + float(st.get("pr_td") or 0.0)
            ln["gp"] = float(st.get("gp") or 0.0)
            ln["team"] = r.get("team")
            out[(2026, w, pid)] = ln
    return out


def usage_history(box):
    """{(season, week): nfl_usage.compact-shaped week} from Sleeper's box."""
    out = collections.defaultdict(lambda: {"pl": {}, "tot": {}})
    for (s, w, pid), ln in box.items():
        team = ln.get("team")
        if not team:
            continue
        wk = out[(s, w)]
        t = wk["tot"].setdefault(team, [0.0, 0.0])
        t[0] += ln["rec_tgt"]
        t[1] += ln["rush_att"]
        if ln["gp"] > 0:
            wk["pl"][pid] = [team, ln["rec_tgt"], ln["rush_att"]]
    return out


# ---- frozen pre-kickoff Sleeper lines (2026) ------------------------------------
def frozen_lines(props_paths, sleeper_dir):
    """{(week, sleeper id): [(content ts, {field: v}, source, team, opp)]}."""
    out = collections.defaultdict(list)
    for p in FROZEN_SOURCES:
        with open(p, encoding="utf-8") as fh:
            rows = json.load(fh)
        rel = os.path.relpath(p, REPO)
        for r in rows:
            pl = r.get("player") or {}
            if (pl.get("position") or "") not in M.POS:
                continue
            st = r.get("stats") or {}
            proj = {k: float(st[k]) for k in FIELDS if st.get(k) is not None}
            if not proj or not r.get("updated_at"):
                continue
            out[(int(r["week"]), str(r["player_id"]))].append(
                (r["updated_at"] / 1000.0, proj, rel, r.get("team"), r.get("opponent")))
    # the props logs: names -> Sleeper ids through the revised feed's names
    name_id = {}
    for w in range(1, TEST_MAX_WEEK + 2):
        pp = os.path.join(sleeper_dir, f"proj_2026_{w}.json")
        if not os.path.exists(pp):
            continue
        with open(pp, encoding="utf-8") as fh:
            for r in json.load(fh):
                pl = r.get("player") or {}
                nm = kalshi_nfl._norm(f"{pl.get('first_name', '')} {pl.get('last_name', '')}")
                name_id[(nm, r.get("team"))] = str(r.get("player_id"))
    for p in props_paths:
        with open(p, encoding="utf-8") as fh:
            lg = json.load(fh)
        wk = int(lg["week"])
        for nm, rec in (lg.get("sleeper") or {}).items():
            st = rec.get("stats") or {}
            pid = name_id.get((nm, rec.get("team")))
            if not pid or not st or (rec.get("pos") not in M.POS and rec.get("pos") != "FB"):
                continue
            proj = {k: float(st[k]) for k in FIELDS if st.get(k) is not None}
            if proj:
                out[(wk, pid)].append((float(rec.get("ts") or 0.0), proj, os.path.basename(p) + " (props log)",
                                       rec.get("team"), None))
    return out


def choose_frozen(cands, kick):
    """The latest capture whose content time is before kickoff, or None."""
    ok = [c for c in cands if kick and c[0] < kick]
    return max(ok, key=lambda c: c[0]) if ok else None


# ---- the frame -------------------------------------------------------------------
def last_starter(ctx, season, week, team):
    """The team's quarterback with the most pass attempts in its last game
    before the cutoff (this season, else the latest prior season)."""
    for T in [ctx.current(season, week)] + [ctx.full_tallies(s) for s in (season - 1, season - 2)]:
        if T is None:
            continue
        games = [r for side, r in (T.team_games.get(team) or []) if side == "off"]
        if T.season == season:
            games = [r for r in games if r["week"] < week]
        if games:
            lr = max(games, key=lambda r: (r["season"], r["week"]))
            cand = max(lr["players"].items(), key=lambda kv: kv[1][P.G_PATT])
            if cand[1][P.G_PATT] > 0:
                return cand[0]
    return None


def build(out_path, defhist_path, sleeper_dir, props_paths, seasons=SEASONS, max_week=None):
    t0 = time.time()
    ids = D.load_ids()
    pos_map = P.positions_from_ids(ids)
    idx = D.ids_index(ids["ids"])
    _SLEEPER_GSIS.clear()
    _SLEEPER_GSIS.update(idx["sleeper"])
    stores = {s: D.load_season(s) for s in range(2019, 2027)}
    stores = {s: st for s, st in stores.items() if st}
    ctx = M.Ctx(stores, pos_map)
    box = sleeper_box(sleeper_dir)
    uh = usage_history(box)
    frozen = frozen_lines(props_paths, sleeper_dir)
    games = {}
    for s, st in stores.items():
        for g in st["games"]:
            games[g["id"]] = g
    team_kick = {}
    for g in stores[2026]["games"]:
        for t in (g["home"], g["away"]):
            team_kick[(g["week"], t)] = kick_ts(g)
    # the dataset's rows, grouped by (season, week)
    rows = collections.defaultdict(list)
    with gzip.open(DATASET, "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            s, w = int(r["season"]), int(r["week"])
            if s not in seasons or (s == 2026 and w > TEST_MAX_WEEK):
                continue
            rows[(s, w)].append(r)
    # frozen-only 2026 rows: a frozen-projected player the revised feed dropped
    have = {(int(r["week"]), r["sleeper_id"]) for (s, w), rs in rows.items() if s == 2026 for r in rs}
    gsis_of = idx["sleeper"]
    added = 0
    for (wk, spid), cands in frozen.items():
        if wk > TEST_MAX_WEEK or (wk, spid) in have:
            continue
        g = gsis_of.get(spid)
        if g is None or pos_map.get(g) not in ("QB", "RB", "WR", "TE", "FB"):
            continue
        team = SL2NV.get(cands[0][3], cands[0][3])
        gm = next((x for x in stores[2026]["games"] if x["week"] == wk and team in (x["home"], x["away"])), None)
        if gm is None:
            continue
        opp = gm["away"] if gm["home"] == team else gm["home"]
        rows[(2026, wk)].append({"season": "2026", "week": str(wk), "game_id": gm["id"], "team": team, "opp": opp,
                                 "home": "1" if gm["home"] == team else "0", "gsis": D.gsis_str(g), "sleeper_id": spid,
                                 "name": (ids["ids"].get(g) or {}).get("name", ""), "pos": "RB" if pos_map[g] == "FB" else pos_map[g],
                                 "played": "0", "in_sleeper": "0", "_frozen_only": "1"})
        added += 1
    log(f"[frame] {sum(len(v) for v in rows.values())} rows ({added} frozen-only 2026 rows), {time.time() - t0:.0f}s")
    defhist = {}
    feat_names = None
    fh = gzip.open(out_path, "wt", newline="")
    wr = None
    n_out = 0
    for s in range(min(seasons) - 2, max(seasons) + 1):
        if s not in stores:
            continue
        weeks = sorted({g["week"] for g in stores[s]["games"]})
        teams = sorted(stores[s]["teams"])
        for w in weeks:
            if (s == 2026 and w > TEST_MAX_WEEK) or (max_week and w > max_week):
                break
            vec = M.def_vectors(ctx, s, w, teams)
            for t, v in vec.items():
                defhist[(s, w, t)] = v
            if s in seasons:
                hist = {wk: uh[(s, wk)] for wk in range(1, w) if (s, wk) in uh}
                su = usage_lines(rows.get((s, w), []), hist)
                fz_su = usage_lines(rows.get((s, w), []), hist, frozen=frozen, team_kick=team_kick) if s == 2026 else {}
                for r in rows.get((s, w), []):
                    rec = frame_row(ctx, s, w, r, games, box, frozen, team_kick, defhist, su, fz_su)
                    if rec is None:
                        continue
                    if wr is None:
                        feat_names = list(rec.keys())
                        wr = csv.DictWriter(fh, fieldnames=feat_names)     # extrasaction 'raise': no column dropped silently
                        wr.writeheader()
                    wr.writerow(rec)
                    n_out += 1
            ctx.drop(s, w)
            log(f"[frame] {s} week {w}: {n_out} rows written, {time.time() - t0:.0f}s")
    fh.close()
    with open(defhist_path, "w") as fo:
        json.dump({"schema": M.SCHEMA, "dims": list(M.DEF_VEC), "built": time.time(), "k_mode": M.K_MODE, "k_source": M.K_SOURCE,
                   "rows": {f"{s}|{w}|{t}": v for (s, w, t), v in defhist.items()}}, fo, separators=(",", ":"))
    log(f"[frame] done: {n_out} rows, {len(defhist)} defense vectors, {time.time() - t0:.0f}s")


def _sl_line(r):
    out = {}
    for k in FIELDS:
        v = _f(r.get(f"sl_proj_{k}"))
        if v is not None:
            out[k] = v
    return out


def usage_lines(rs, hist, frozen=None, team_kick=None):
    """{sleeper id: the seven nfl_usage components after nfl_usage.adjust} for
    one week's Sleeper lines (revised, or frozen when `frozen`)."""
    pairs = []
    for r in rs:
        if frozen is not None:
            team = r["team"]
            c = choose_frozen(frozen.get((int(r["week"]), r["sleeper_id"]), []), team_kick.get((int(r["week"]), team)))
            line = dict(c[1]) if c else None
            sl_team = c[3] if c else None
        else:
            line = _sl_line(r) if r.get("in_sleeper") == "1" else None
            sl_team = None
        if not line:
            continue
        team = sl_team or {"LA": "LAR"}.get(r["team"], r["team"])
        means = {k: float(line.get(k) or 0.0) for k in ("rec_tgt", "rec", "rec_yd", "rec_td", "rush_att", "rush_yd", "rush_td")}
        pairs.append((r["sleeper_id"], {"team": team, "means": means, "proj_pts": None}))
    if hist and len(hist) >= 2:
        nfl_usage.adjust(pairs, hist)
    return {pid: p["means"] for pid, p in pairs}


def frame_row(ctx, s, w, r, games, box, frozen, team_kick, defhist, su, fz_su):
    pos = r["pos"]
    if pos not in M.POS:
        return None
    g = games.get(r["game_id"])
    if g is None:
        return None
    team, opp = r["team"], r["opp"]
    gid = D.gsis_int(r["gsis"])
    home = g["home"] == team
    spread = g.get("spread")
    margin = (spread if home else -spread) if spread is not None else None
    rec = {"season": s, "week": w, "game_id": r["game_id"], "team": team, "opp": opp, "home": 1 if home else 0,
           "gsis": gid, "sleeper_id": r.get("sleeper_id") or "", "pos": pos, "name": r.get("name") or "",
           "played": r.get("played") or "0", "in_sleeper": r.get("in_sleeper") or "0",
           "frozen_only": r.get("_frozen_only") or "0", "kick_ts": kick_ts(g), "margin": margin, "total": g.get("total")}
    sl = _sl_line(r) if r.get("in_sleeper") == "1" else {}
    for k in FIELDS:
        rec[f"sl_{k}"] = sl.get(k)
    su_line = su.get(r.get("sleeper_id") or "")
    for k in ("rec_tgt", "rec", "rec_yd", "rec_td", "rush_att", "rush_yd", "rush_td"):
        rec[f"su_{k}"] = None if su_line is None else su_line.get(k)
    # every row carries the frozen columns (empty before 2026) so the header
    # written from the first row holds them
    rec["fz_src"], rec["fz_lead_h"] = "", None
    for k in FIELDS:
        rec[f"fz_{k}"] = None
    for k in ("rec_tgt", "rec", "rec_yd", "rec_td", "rush_att", "rush_yd", "rush_td"):
        rec[f"fzsu_{k}"] = None
    if s == 2026:
        c = choose_frozen(frozen.get((w, r.get("sleeper_id") or ""), []), team_kick.get((w, team)))
        rec["fz_src"] = c[2] if c else ""
        rec["fz_lead_h"] = round((rec["kick_ts"] - c[0]) / 3600.0, 2) if c and rec["kick_ts"] else None
        for k in FIELDS:
            rec[f"fz_{k}"] = (c[1].get(k) if c else None)
        fz = fz_su.get(r.get("sleeper_id") or "")
        for k in ("rec_tgt", "rec", "rec_yd", "rec_td", "rush_att", "rush_yd", "rush_td"):
            rec[f"fzsu_{k}"] = None if fz is None else fz.get(k)
    # the official outcome
    bx = box.get((s, w, r.get("sleeper_id") or ""))
    if bx is not None:
        src = "sleeper_box"
        y = dict(bx)
    elif r.get("played") == "1" and r.get("act_tgt") not in (None, ""):
        src = "pbp"
        y = {"rec_tgt": _f(r["act_tgt"]), "rec": _f(r["act_rec"]), "rec_yd": _f(r["act_rec_yds"]),
             "rec_td": _f(r["act_rec_td"]), "rush_att": _f(r["act_car"]), "rush_yd": _f(r["act_rush_yds"]),
             "rush_td": _f(r["act_rush_td"]), "pass_att": _f(r["act_pass_att"]), "pass_cmp": _f(r["act_pass_cmp"]),
             "pass_yd": _f(r["act_pass_yds"]), "pass_td": _f(r["act_pass_td"]), "pass_int": _f(r["act_int"]),
             "fum_lost": _f(r["act_fuml"]), "two_pt": 0.0, "ret_td": 0.0}
    else:
        src = "did_not_play"
        y = {k: 0.0 for k in FIELDS}
        y.update({"two_pt": 0.0, "ret_td": 0.0})
    if src == "sleeper_box" and (bx.get("gp") or 0) <= 0 and r.get("played") != "1":
        src = "did_not_play"
    rec["y_src"] = src
    for k in FIELDS + ("two_pt", "ret_td"):
        rec[f"y_{k}"] = y.get(k) or 0.0
    # the quarterbacks the passing features describe
    q_last = last_starter(ctx, s, w, team)
    q_sl = D.gsis_int(r.get("q_gsis") or "") or None
    q_fz = None
    if s == 2026:
        q_fz = frozen_qb1(ctx, frozen, w, team, team_kick)
    q_use = q_fz or q_last
    rec["qb_last"], rec["qb_sleeper_rev"], rec["qb_frozen"], rec["qb_used"] = q_last or "", q_sl or "", q_fz or "", q_use or ""
    # comparables: this opponent's vector as of this week, his games before it
    opp_vec = defhist.get((s, w, opp))
    comps, _sel = M.comparables(ctx, s, w, gid, opp_vec, defhist)
    f = M.features(ctx, s, w, gid, team, opp, pos, margin=margin, total=g.get("total"), qb=q_use, comps=comps)
    ev = f.pop("_ev")
    f.pop("_def")
    pb = f.pop("_pb")
    for k, v in f.items():
        rec["f_" + k] = round(v, 6)
    for k in ("cov_mz", "cov_shell", "tgt_rate_pos", "press_qb", "pass_epa", "run_ypc", "man", "pressure"):
        rec["rel_" + k] = (ev.get(k) or {}).get("rel")
        rec["n_" + k] = (ev.get(k) or {}).get("n")
    for m in M.MARKETS:
        c = comps.get(m)
        rec["neff_" + m] = round(c["n_eff"], 2) if c else None
    rec["p_tshare"] = (pb or {}).get("tshare")
    rec["p_cshare"] = (pb or {}).get("cshare")
    rec["p_targets"] = (pb or {}).get("targets")
    rec["p_games"] = ((pb or {}).get("games") or {}).get("weighted")
    # O1 sensitivity: the same passing features with the revised Sleeper QB1
    if q_sl and q_sl != q_use:
        f2 = M.features(ctx, s, w, gid, team, opp, pos, margin=margin, total=g.get("total"), qb=q_sl, comps=None)
        for k in ("press_qb", "blitz_qb", "press_catch"):
            rec["fs_" + k] = round(f2[k], 6)
    else:
        for k in ("press_qb", "blitz_qb", "press_catch"):
            rec["fs_" + k] = rec["f_" + k]
    return rec


def frozen_qb1(ctx, frozen, week, team, team_kick):
    """Sleeper's QB1 by projected pass attempts in the frozen captures of the
    team-week (the quarterback production would have had), as a gsis int."""
    best = None
    kick = team_kick.get((week, team))
    for (wk, spid), cands in frozen.items():
        if wk != week:
            continue
        c = choose_frozen(cands, kick)
        if not c or SL2NV.get(c[3], c[3]) != team:
            continue
        att = c[1].get("pass_att") or 0.0
        if att > 5 and (best is None or att > best[0]):
            best = (att, spid)
    if not best:
        return None
    return _SLEEPER_GSIS.get(best[1])


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--defhist", required=True)
    ap.add_argument("--sleeper", required=True, help="dir with proj_2026_<w>.json / stats_2026_<w>.json")
    ap.add_argument("--props", nargs="*", default=[], help="sim-history props logs (props/nfl_props_2026_wNN.json)")
    ap.add_argument("--seasons", nargs="*", type=int, default=list(SEASONS))
    ap.add_argument("--max-week", type=int, default=None, help="smoke runs only")
    a = ap.parse_args(argv)
    build(a.out, a.defhist, a.sleeper, a.props, seasons=tuple(a.seasons), max_week=a.max_week)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
