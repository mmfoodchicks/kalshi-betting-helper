"""The historical player-week table: Sleeper's weekly projections joined to
Sleeper's weekly box scores by player id, 2022 to 2025, with pregame roles,
DraftKings points derived from the components, and every questionable row
flagged, counted and excluded with a reason rather than fixed. 2025 is the
held-out validation season: it is loaded and coverage-counted here and its
outcomes are not to be read by any fitting step (research.stage2b marks it).

Raw feeds are not committed (175 MB); the builder reads a raw directory
of proj_<season>_<week>.json and stats_<season>_<week>.json and writes the
compressed table plus a coverage report. Numpy only: the app's environment
has no pandas."""
import csv
import gzip
import io
import json
import os
import collections

POS = ("QB", "RB", "WR", "TE")
SEASONS = (2022, 2023, 2024, 2025)
HOLDOUT = 2025
COMPONENTS = ("pass_att", "pass_yd", "pass_td", "pass_int", "rush_att", "rush_yd", "rush_td",
              "rec_tgt", "rec", "rec_yd", "rec_td", "fum_lost")
ACT_EXTRA = ("pass_2pt", "rush_2pt", "rec_2pt", "st_td", "fum_rec_td", "pts_ppr", "gp", "off_snp")
COLUMNS = (["season", "week", "player_id", "name", "pos", "team", "opp", "game_key", "role",
            "has_proj", "has_act", "keep", "flags", "proj_dk", "act_dk"]
           + [f"proj_{c}" for c in COMPONENTS] + [f"act_{c}" for c in COMPONENTS] + [f"act_{c}" for c in ACT_EXTRA])


def dk_points(st, bonuses=True):
    """DraftKings NFL Classic points from a component dict (missing = 0)."""
    g = lambda k: float(st.get(k) or 0.0)
    pts = (0.04 * g("pass_yd") + 4.0 * g("pass_td") - g("pass_int") + 0.1 * g("rush_yd") + 6.0 * g("rush_td")
           + g("rec") + 0.1 * g("rec_yd") + 6.0 * g("rec_td") - g("fum_lost")
           + 2.0 * (g("pass_2pt") + g("rush_2pt") + g("rec_2pt")) + 6.0 * (g("st_td") + g("fum_rec_td")))
    if bonuses:
        pts += 3.0 * (g("pass_yd") >= 300) + 3.0 * (g("rush_yd") >= 100) + 3.0 * (g("rec_yd") >= 100)
    return pts


def _load(raw_dir, kind, season, week):
    p = os.path.join(raw_dir, f"{kind}_{season}_{week}.json")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def _rows_of(payload, season, week, kind, flags_out):
    """{player_id: record} for one feed file; duplicates flagged, wrong season/week flagged."""
    out = {}
    for r in payload or []:
        pid = str(r.get("player_id") or "")
        pl = r.get("player") or {}
        st = r.get("stats") or {}
        rec = {"pid": pid, "name": f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip(),
               "pos": (pl.get("position") or "").upper(), "team": r.get("team") or pl.get("team"),
               "opp": r.get("opponent"), "game_id": r.get("game_id"), "stats": st,
               "season": r.get("season"), "week": r.get("week")}
        if not pid:
            flags_out[f"{kind}:no_player_id"] += 1
            continue
        if str(rec["season"]) != str(season) or str(rec["week"]) != str(week):
            flags_out[f"{kind}:wrong_season_week"] += 1
            continue
        if pid in out:
            flags_out[f"{kind}:duplicate_player_week"] += 1
            continue
        out[pid] = rec
    return out


def _roles(team_rows):
    """Pregame roles for one team-week from PROJECTIONS only: QB1 by projected
    pass attempts; RB1/RB2 by projected carries plus targets; WR1-3 and TE1 by
    projected targets (receiving yards as the tie-break). Nothing from the
    box score touches this, so a receiver is WR1 because he was projected to
    be, not because he led the team that week."""
    roles = {}
    def top(pos, key, k):
        cands = [r for r in team_rows if r["pos"] == pos and r["has_proj"] and r["proj_dk"] > 0]
        cands.sort(key=lambda r: (-key(r), -r["proj_dk"], r["pid"]))
        return cands[:k]
    for i, r in enumerate(top("QB", lambda r: (r["p"].get("pass_att") or r["p"].get("pass_yd") or 0.0), 1)):
        roles[r["pid"]] = "QB1"
    for i, r in enumerate(top("RB", lambda r: (r["p"].get("rush_att") or 0.0) + (r["p"].get("rec_tgt") or 0.0), 3)):
        roles[r["pid"]] = f"RB{i + 1}"
    for i, r in enumerate(top("WR", lambda r: (r["p"].get("rec_tgt") or 0.0) + 0.01 * (r["p"].get("rec_yd") or 0.0), 4)):
        roles[r["pid"]] = f"WR{i + 1}"
    for i, r in enumerate(top("TE", lambda r: (r["p"].get("rec_tgt") or 0.0) + 0.01 * (r["p"].get("rec_yd") or 0.0), 2)):
        roles[r["pid"]] = f"TE{i + 1}"
    return roles


def build(raw_dir, seasons=SEASONS, weeks=range(1, 19)):
    """(rows, coverage): the table as a list of dicts (COLUMNS) and the
    coverage report per season with every exclusion counted."""
    rows = []
    coverage = {}
    for season in seasons:
        cov = {"weeks_with_projections": 0, "weeks_with_actuals": 0, "games": 0, "empty_rows": 0,
               "projected_player_weeks": 0, "actual_player_weeks": 0, "matched": 0,
               "projection_without_actual": 0, "actual_without_projection": 0,
               "kept": 0, "excluded": 0, "exclusion_reasons": collections.Counter(),
               "feed_flags": collections.Counter(), "flag_counts": collections.Counter()}
        for week in weeks:
            proj = _load(raw_dir, "proj", season, week)
            act = _load(raw_dir, "stats", season, week)
            if proj is None and act is None:
                continue
            pr = _rows_of(proj, season, week, "proj", cov["feed_flags"]) if proj else {}
            ar = _rows_of(act, season, week, "stats", cov["feed_flags"]) if act else {}
            if pr:
                cov["weeks_with_projections"] += 1
            if any((r["stats"].get("gp") or 0) for r in ar.values()):
                cov["weeks_with_actuals"] += 1
            # opponent pairs this week, from whichever feed names them
            opp_of = {}
            for src in (pr, ar):
                for r in src.values():
                    if r["team"] and r["opp"]:
                        opp_of.setdefault(r["team"], collections.Counter())[r["opp"]] += 1
            opp_main = {t: c.most_common(1)[0][0] for t, c in opp_of.items()}
            games = {tuple(sorted((t, o))) for t, o in opp_main.items()}
            cov["games"] += len(games)
            week_rows = []
            for pid in set(pr) | set(ar):
                p, a = pr.get(pid), ar.get(pid)
                base = p or a
                flags = []
                pos = (p or a)["pos"]
                ps0 = p["stats"] if p else {}
                p_real = bool(p) and dk_points(ps0, bonuses=False) > 0.0
                gp0 = (a["stats"].get("gp") if a else None)
                if not p_real and not (a and gp0):
                    cov["empty_rows"] = cov.get("empty_rows", 0) + 1     # a feed placeholder: no projection, no game played
                    continue
                # the box score's team is the team that week; the projection
                # row's team can be the player's CURRENT team on an empty row
                team = (a["team"] if a and a.get("team") else None) or (p["team"] if p and p.get("team") else None)
                opp = (a["opp"] if a and a.get("opp") else None) or (p["opp"] if p and p.get("opp") else None)
                if p_real and a and p.get("team") and a.get("team") and p["team"] != a["team"]:
                    flags.append("team_conflict")
                if pos not in POS:
                    flags.append("pos_out_of_scope")
                if not team:
                    flags.append("no_team")
                if not opp:
                    flags.append("no_opp")
                elif opp_main.get(opp) and opp_main.get(opp) != team:
                    flags.append("opp_pair_inconsistent")
                ps = p["stats"] if p else {}
                as_ = a["stats"] if a else {}
                gp = as_.get("gp")
                if a and not gp:
                    flags.append("act_did_not_play")
                if a and gp:
                    if (as_.get("rec") or 0) > (as_.get("rec_tgt") or 0) and as_.get("rec_tgt") is not None:
                        flags.append("rec_gt_tgt")
                    if any((as_.get(k) or 0) < 0 for k in ("pass_yd", "pass_td", "rush_att", "rush_td", "rec_tgt", "rec", "rec_td")):
                        flags.append("negative_stat")
                proj_dk = dk_points(ps, bonuses=False) if p else None
                act_dk = dk_points(as_, bonuses=True) if (a and gp) else None
                if p and (proj_dk or 0.0) <= 0.0:
                    flags.append("proj_zero")
                game_key = (p or a).get("game_id") or ("-".join(sorted((team or "?", opp or "?"))))
                rec = {"season": season, "week": week, "pid": pid, "name": base["name"], "pos": pos, "team": team,
                       "opp": opp, "game_key": str(game_key), "has_proj": bool(p), "has_act": bool(a and gp),
                       "p": ps, "a": as_, "proj_dk": proj_dk, "act_dk": act_dk, "flags": flags, "gp": gp}
                week_rows.append(rec)
            # roles per team-week
            by_team = collections.defaultdict(list)
            for r in week_rows:
                if r["team"]:
                    by_team[r["team"]].append(r)
            for team, trs in by_team.items():
                roles = _roles(trs)
                for r in trs:
                    r["role"] = roles.get(r["pid"], "")
            for r in week_rows:
                r.setdefault("role", "")
                hard = [f for f in r["flags"] if f in ("pos_out_of_scope", "no_team", "no_opp", "team_conflict",
                                                        "opp_pair_inconsistent", "rec_gt_tgt", "negative_stat")]
                r["keep"] = not hard
                real = r["has_proj"] and (r["proj_dk"] or 0.0) > 0.0
                cov["projected_player_weeks"] += int(real)
                cov["actual_player_weeks"] += int(r["has_act"])
                cov["matched"] += int(real and r["has_act"])
                cov["projection_without_actual"] += int(real and not r["has_act"])
                cov["actual_without_projection"] += int(r["has_act"] and not real)
                for f in r["flags"]:
                    cov["flag_counts"][f] += 1
                if r["keep"]:
                    cov["kept"] += 1
                else:
                    cov["excluded"] += 1
                    for f in hard:
                        cov["exclusion_reasons"][f] += 1
                rows.append(r)
        cov["exclusion_reasons"] = dict(cov["exclusion_reasons"])
        cov["feed_flags"] = dict(cov["feed_flags"])
        cov["flag_counts"] = dict(cov["flag_counts"])
        cov["holdout"] = (season == HOLDOUT)
        coverage[str(season)] = cov
    return rows, coverage


def to_csv_gz(rows, path):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    for r in rows:
        line = [r["season"], r["week"], r["pid"], r["name"], r["pos"], r["team"] or "", r["opp"] or "", r["game_key"], r["role"],
                int(r["has_proj"]), int(r["has_act"]), int(r["keep"]), "|".join(r["flags"]),
                "" if r["proj_dk"] is None else round(r["proj_dk"], 3), "" if r["act_dk"] is None else round(r["act_dk"], 3)]
        line += ["" if not r["has_proj"] or r["p"].get(c) is None else r["p"].get(c) for c in COMPONENTS]
        line += ["" if not r["has_act"] or r["a"].get(c) is None else r["a"].get(c) for c in COMPONENTS]
        line += ["" if not r["has_act"] or r["a"].get(c) is None else r["a"].get(c) for c in ACT_EXTRA]
        w.writerow(line)
    with gzip.open(path, "wt") as fh:
        fh.write(buf.getvalue())


def read_csv_gz(path):
    """The committed table back as a list of dicts with numbers parsed."""
    out = []
    with gzip.open(path, "rt") as fh:
        for row in csv.DictReader(fh):
            r = dict(row)
            for k, v in row.items():
                if k in ("name", "pos", "team", "opp", "game_key", "role", "flags", "player_id"):
                    continue
                r[k] = float(v) if v not in ("", None) else None
            r["season"] = int(r["season"]); r["week"] = int(r["week"])
            r["has_proj"] = bool(r["has_proj"]); r["has_act"] = bool(r["has_act"]); r["keep"] = bool(r["keep"])
            out.append(r)
    return out
