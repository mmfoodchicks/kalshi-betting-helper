"""The football look-back's second half: after a slate is over, what was the
best lineup, how close did ours come, where would it have finished, which
projections missed, and do two box-score feeds agree on the points.

dfslog grades every logged big-event lineup off Sleeper's box scores. The
owner asked for more (2026-10-18): take every player's real yards and
touchdowns, score them with DraftKings' rules, and build the top lineup from
that. For each graded NFL classic draft group this computes, once, after the
slate settles:

  best      the best lineup under the cap on the REAL points, built by the
            sheet's own optimizer (nfl_dfs.optimize) with the real points as
            the projection. It keeps the sheet's rules (no tight end at FLEX,
            no defense facing your own players), so it is the best lineup the
            builder could have built, not necessarily DraftKings' true
            optimum, and the card says so.
  ours      every logged lineup of that slate (the builder's, and the
            tournament board's top lineups, captured at lock -- objective
            tourney:<list>): its real points and its share of the best.
  finish    where each would have placed in the real contest, read off the
            owner's standings export (dk_standings) when one is in:
            interpolated between the export's rank/points knots, and whether
            it cashed.
  misses    stat by stat and position by position, Sleeper's projection as
            the board had it (frozen before kickoff by nfl_props_log) against
            the real box score: bias and mean absolute error.
  cross     the players in our lineups and the best one, scored a second time
            from ESPN's box scores; a disagreement over 0.1 points is listed
            (two-point conversions and return touchdowns are the usual cause:
            ESPN's box does not carry the first).

Stored in the dfs_actuals table under sport "nfl_hindsight", one row per
(event, draft group). The optimizer is the sheet's (a builder click's
worth of CPU, measured in the report); one slate per recorder pass.
Showdown slates are graded but get no hindsight lineup yet.
"""
import datetime
import json
import os
import re
import time

import errlog

TOURNEY_LISTS = ("top_win", "top_money", "top_top1")
TOURNEY_TOP = 3                         # lineups per list per contest
MISS_STATS = ("pass_yd", "pass_td", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td")
MISS_MIN_PTS = 5.0                      # only players projected for 5+ PPR enter the misses
CROSS_TOL = 0.1
_ESPN_WEEK = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&week={week}&dates={season}"
_ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={eid}"
_BOARD = re.compile(r"^(cl|sd)_tourney_nfl_(\d+)\.pkl$")


# ---- the tournament board's lineups, logged at lock ----------------------------
def _season_of(date_iso):
    y, m = int(date_iso[:4]), int(date_iso[5:7])
    return y if m >= 3 else y - 1


def board_rows(payload):
    """[(contest dict, list name, [row])] of a tournament board, classic or
    showdown."""
    out = []
    if payload.get("kind") == "classic":
        for _cid, r in (payload.get("results") or {}).items():
            for lst in TOURNEY_LISTS:
                out.append((r.get("contest") or {}, lst, (r.get(lst) or [])[:TOURNEY_TOP]))
    else:
        for lst in TOURNEY_LISTS:
            out.append((payload.get("contest") or {}, lst, (payload.get(lst) or [])[:TOURNEY_TOP]))
    return out


def capture_tourney(boards_dir=None, now=None, get=None):
    """Log the top lineups of every NFL tournament board whose contest has
    started -- the board then on disk is the last one built before lock, and
    boardshare keeps a board a day, so the recorder's half-hourly pass sees
    it -- as dfs_builds rows (objective tourney:<list>), once each. Returns
    how many rows went in."""
    import boardshare
    import dfslog
    import nfl_dfs
    d = boards_dir or boardshare._DIR
    get = get or boardshare.get
    now = time.time() if now is None else now
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return 0
    n = 0
    for fn in names:
        m = _BOARD.match(fn)
        if not m:
            continue
        payload, _age = get(fn[:-4], None)
        if not payload or payload.get("sport", "nfl") != "nfl":
            continue
        week = (payload.get("slate") or {}).get("week")
        for contest, lst, rows in board_rows(payload):
            starts = nfl_dfs.iso_ts(contest.get("starts"))     # DraftKings' contest clock is UTC with a Z
            if not starts or starts > now or not rows or not week:
                continue
            date = datetime.datetime.utcfromtimestamp(starts).date().isoformat()
            event_key = f"nfl:{_season_of(date)}:w{int(week)}"
            lineups = []
            for r in rows:
                players = [{"name": p.get("name"), "salary": p.get("salary"), "proj": p.get("proj"),
                            "pos": p.get("pos"), "slot": p.get("slot"), "team": p.get("team"),
                            "captain": p.get("slot") == "CPT"} for p in (r.get("lineup") or []) if p.get("name")]
                if players:
                    lineups.append((players, r.get("salary") or sum(int(p["salary"] or 0) for p in players),
                                    r.get("proj")))
            n += dfslog.log_lineups("nfl", event_key, date, int(week), int(payload.get("draft_group_id") or m.group(2)),
                                    contest, f"tourney:{lst}", payload.get("kind") or "", lineups,
                                    note=f"board built {payload.get('built_ts')}, {payload.get('mins_before_lock')} min before lock")
    return n


# ---- the best lineup on the real points -----------------------------------------
def real_pool(pool_rows, actuals):
    """The slate's players with their real DraftKings points as the
    projection, in the optimizer's shape; and the names the feed did not
    know (a player who did not play scores 0 on DraftKings too, but a name
    the feed cannot match is counted, never silently zeroed)."""
    import dfslog
    import nfl_dfs
    out, unknown = [], []
    for r in pool_rows or []:
        pos = (r.get("pos") or r.get("position") or "").upper().split("/")[0]
        pos = "RB" if pos == "FB" else pos
        elig = nfl_dfs._elig(pos)
        if not elig:
            continue
        v = dfslog._lookup("nfl", actuals, {"name": r.get("name"), "pos": pos, "team": r.get("team")})
        if v is None:
            unknown.append(r.get("name"))
            v = 0.0
        out.append({"name": r.get("name"), "pos": pos, "team": r.get("team"),
                    "opp": nfl_dfs._opp_of(r.get("team"), r.get("game")), "salary": int(float(r.get("salary") or 0)),
                    "proj": float(v), "ceiling": float(v), "floor": float(v), "own": 0.0, "elig": elig})
    return out, unknown


def best_lineup(players):
    """(points, lineup, seconds) -- the sheet's own optimizer on the real
    points."""
    import nfl_dfs
    t = time.time()
    lu = nfl_dfs.optimize(players, nfl_dfs.CAP, "projection")
    secs = round(time.time() - t, 1)
    if not lu:
        return None, None, secs
    return round(sum(p["proj"] for p in lu), 2), lu, secs


# ---- where a score would have finished ---------------------------------------------
def finish(points, field):
    """{"rank", "of", "pct", "cashed"} for a score in a field summary's
    rank/points knots (rank 1 = first place), or None without one."""
    knots = (field or {}).get("rank_points") or []
    n = int((field or {}).get("entries") or 0)
    if not knots or not n:
        return None
    if points >= knots[0][1]:
        rank = 1.0
    elif points <= knots[-1][1]:
        rank = float(knots[-1][0])
    else:
        rank = None
        for (r0, p0), (r1, p1) in zip(knots, knots[1:]):
            if p1 <= points <= p0:
                rank = r0 + (r1 - r0) * ((p0 - points) / (p0 - p1) if p0 > p1 else 0.0)
                break
    cash = ((field or {}).get("points") or {}).get("cash_line")
    return {"rank": int(round(rank)), "of": n, "top_pct": round(100.0 * rank / n, 3),
            "cashed": (points >= cash) if cash is not None else None}


# ---- which projections missed --------------------------------------------------------
def misses(projections, stats_rows):
    """Per position and stat: n, mean projected, mean real, bias (real minus
    projected) and mean absolute error, over the players Sleeper projected
    for MISS_MIN_PTS+ PPR who played. `projections` is nfl_props_log's
    frozen store["sleeper"]; `stats_rows` Sleeper's weekly box scores."""
    import kalshi_nfl
    act = {}
    for r in stats_rows or []:
        pl = r.get("player") or {}
        st = r.get("stats") or {}
        if float(st.get("gp") or 0) > 0:
            act[kalshi_nfl._norm(f"{pl.get('first_name', '')} {pl.get('last_name', '')}")] = st
    cells = {}
    for nm, p in (projections or {}).items():
        st = p.get("stats") or {}
        if float(st.get("pts_ppr") or 0) < MISS_MIN_PTS or nm not in act:
            continue
        pos = (p.get("pos") or "").upper()
        for s in MISS_STATS:
            if s not in st:
                continue
            c = cells.setdefault((pos, s), [0, 0.0, 0.0, 0.0])
            a = float(act[nm].get(s) or 0.0)
            c[0] += 1
            c[1] += float(st[s])
            c[2] += a
            c[3] += abs(a - float(st[s]))
    return [{"pos": pos, "stat": s, "n": c[0], "proj": round(c[1] / c[0], 2), "real": round(c[2] / c[0], 2),
             "bias": round((c[2] - c[1]) / c[0], 2), "mae": round(c[3] / c[0], 2)}
            for (pos, s), c in sorted(cells.items()) if c[0]]


# ---- a second feed: ESPN's box scores --------------------------------------------
_ESPN_KEYS = {"passingYards": "pass_yd", "passingTouchdowns": "pass_td", "interceptions": "pass_int",
              "rushingYards": "rush_yd", "rushingTouchdowns": "rush_td", "receptions": "rec",
              "receivingYards": "rec_yd", "receivingTouchdowns": "rec_td", "fumblesLost": "fum_lost",
              "kickReturnTouchdowns": "kr_td", "puntReturnTouchdowns": "pr_td"}
_ESPN_CATS = {"passing", "rushing", "receiving", "fumbles", "kickReturns", "puntReturns"}


def espn_box(summary):
    """{norm name: {stat: value}} for every skill player in one ESPN game
    summary (categories passing, rushing, receiving, fumbles, returns)."""
    import dfslog
    out = {}
    for team in ((summary or {}).get("boxscore") or {}).get("players") or []:
        for cat in team.get("statistics") or []:
            if cat.get("name") not in _ESPN_CATS:
                continue
            keys = cat.get("keys") or []
            for a in cat.get("athletes") or []:
                nm = dfslog._norm((a.get("athlete") or {}).get("displayName"))
                if not nm:
                    continue
                rec = out.setdefault(nm, {})
                for k, v in zip(keys, a.get("stats") or []):
                    s = _ESPN_KEYS.get(k)
                    if s is None:
                        continue
                    try:
                        rec[s] = rec.get(s, 0.0) + float(str(v).split("/")[0])
                    except ValueError:
                        continue
    return out


def cross_check(names, sleeper_pts, espn):
    """[{name, sleeper, espn, diff}] for every name both feeds carry whose
    DraftKings points disagree by more than CROSS_TOL; and how many were
    compared."""
    import dfslog
    bad, n = [], 0
    for nm in sorted(set(names)):
        k = dfslog._norm(nm)
        if k not in espn or k not in sleeper_pts:
            continue
        e = dfslog._nfl_points("WR", espn[k])
        n += 1
        if abs(e - sleeper_pts[k]) > CROSS_TOL:
            bad.append({"name": nm, "sleeper": sleeper_pts[k], "espn": round(e, 2), "diff": round(sleeper_pts[k] - e, 2)})
    return bad, n


def espn_week(season, week, get=None):
    """{norm name: stats} over every game of the week (ESPN's scoreboard,
    then each game's summary)."""
    import racing
    get = get or racing._get_json
    sb = get(_ESPN_WEEK.format(season=season, week=week)) or {}
    out = {}
    for e in sb.get("events") or []:
        try:
            out.update(espn_box(get(_ESPN_SUMMARY.format(eid=e.get("id")))))
        except Exception as ex:
            errlog.note("HIND-espn", ex, path=str(e.get("id")))
    return out


# ---- one slate, end to end --------------------------------------------------------------
def hindsight(event_key, dg, pool_rows=None, actuals=None, stats_rows=None, projections=None,
              espn=None, fields=None, lineups=None):
    """The whole look-back for one classic slate. Every input defaults to
    production's source; tests pass them."""
    import dfslog
    _, season, wk = event_key.split(":")[:3]
    season, week = int(season), int(wk.lstrip("w"))
    if actuals is None:
        actuals = dfslog._actuals("nfl", event_key) or {}
    if pool_rows is None:
        import dk
        pool_rows = dk.players(dg) or []
    players, unknown = real_pool(pool_rows, actuals)
    pts, lu, secs = best_lineup(players)
    out = {"event_key": event_key, "draft_group_id": int(dg), "ts": int(time.time()),
           "pool": len(players), "unknown_names": unknown[:30], "n_unknown": len(unknown),
           "best": ({"points": pts, "salary": sum(p["salary"] for p in lu),
                     "lineup": [{"name": p["name"], "pos": p["pos"], "team": p["team"], "salary": p["salary"],
                                 "points": p["proj"]} for p in lu]} if lu else None),
           "optimizer_s": secs,
           "rules": "the sheet's: no tight end at FLEX, no defense facing your own players"}
    if lineups is None:
        lineups = _logged(event_key, dg)
    if fields is None:
        fields = _fields_for(dg)
    field = max(fields, key=lambda f: f.get("entries") or 0) if fields else None
    ours, seen = [], set()
    for r in lineups:
        # the board's lists overlap (a lineup can top both win% and top-1%,
        # for two contests): one row per objective and roster
        names = frozenset(p.get("name") for p in json.loads(r.get("players") or "[]"))
        if (r["objective"], names) in seen:
            continue
        seen.add((r["objective"], names))
        row = {"objective": r["objective"], "actual": r["actual"], "proj": r["total_proj"],
               "pct_of_best": round(100.0 * r["actual"] / pts, 1) if pts else None,
               "players": sorted(names)}
        if field and r["actual"] is not None:
            row["finish"] = finish(r["actual"], field)
        ours.append(row)
    out["ours"] = sorted(ours, key=lambda x: -(x["actual"] or 0))
    if field:
        out["field"] = {"contest_id": field.get("contest_id"), "name": (field.get("contest") or {}).get("name"),
                        "entries": field.get("entries"), "first": (field.get("points") or {}).get("first"),
                        "cash_line": (field.get("points") or {}).get("cash_line")}
        if pts:
            out["best"]["finish"] = finish(pts, field)
    if projections is None:
        import deep_cache
        import nfl_props_log
        log, _ts = deep_cache.load(nfl_props_log.KEY.format(season=season, week=week))
        projections = (log or {}).get("sleeper")
    if stats_rows is None:
        import racing
        stats_rows = racing._get_json(dfslog._SLEEPER_STATS.format(season=season, week=week)
                                      + "".join(f"&position[]={p}" for p in ("QB", "RB", "WR", "TE"))) or []
    out["misses"] = misses(projections, stats_rows) if projections else []
    out["misses_source"] = ("Sleeper's projection as logged before kickoff (nfl_props_log)" if projections
                            else "no pre-kickoff projection log for this week")
    names = [p["name"] for r in lineups for p in json.loads(r.get("players") or "[]")] + \
            [p["name"] for p in (lu or [])]
    if espn is None:
        try:
            espn = espn_week(season, week)
        except Exception as e:
            errlog.note("HIND-espn-week", e, path=event_key)
            espn = {}
    sleeper_pts = {k: v for k, v in actuals.items() if not str(k).startswith("dst:")}
    bad, n = cross_check(names, sleeper_pts, espn)
    out["cross"] = {"compared": n, "disagree": bad, "tolerance": CROSS_TOL}
    return out


def _logged(event_key, dg):
    import dfslog
    import store
    dfslog._ensure()
    with store._lock, store._conn() as c:
        rows = c.execute("SELECT objective, actual, total_proj, players FROM dfs_builds WHERE sport='nfl' "
                         "AND event_key=? AND draft_group_id=? AND graded=1 AND backtest=0",
                         (event_key, int(dg))).fetchall()
    return [dict(r) for r in rows]


def _fields_for(dg):
    """Field summaries (dk_standings) whose contest ran on this draft group."""
    import dk_standings
    out = []
    for r in dk_standings.listing().get("contests") or []:
        if int(r.get("draft_group_id") or 0) == int(dg):
            s, _ts = dk_standings.summary(r["contest_id"])
            if s:
                out.append(s)
    return out


GIVE_UP_DAYS = 4        # a pool DraftKings will not serve is retried this long, then stored as such


def due():
    """(event_key, dg, slate date) for graded classic slates with no
    hindsight row."""
    import dfslog
    import store
    dfslog._ensure()
    with store._lock, store._conn() as c:
        rows = c.execute("SELECT event_key, draft_group_id, MIN(event_date) d, MAX(mode) mode FROM dfs_builds "
                         "WHERE sport='nfl' AND graded=1 AND backtest=0 AND draft_group_id IS NOT NULL "
                         "GROUP BY event_key, draft_group_id").fetchall()
        have = {r["event_key"] for r in c.execute("SELECT event_key FROM dfs_actuals WHERE sport='nfl_hindsight'")}
    return [(r["event_key"], int(r["draft_group_id"]), r["d"] or "") for r in rows
            if f"{r['event_key']}:{r['draft_group_id']}" not in have and (r["mode"] or "") != "showdown"]


def tick(limit=1):
    """The recorder's hook (after dfslog grades): capture the boards at lock,
    then at most `limit` slate's hindsight. Each failure ledgered under its
    own code."""
    import store
    try:
        capture_tourney()
    except Exception as e:
        errlog.note("HIND-capture", e)
    import clock
    done = 0
    today = clock.today_et()
    for event_key, dg, day in due()[:limit]:
        try:
            res = hindsight(event_key, dg)
        except Exception as e:
            errlog.note("HIND-slate", e, path=f"{event_key}:{dg}")
            continue
        if not res.get("pool"):
            # DraftKings served no pool (a blip, or the draft group is gone):
            # try again next pass, and only after GIVE_UP_DAYS store the
            # slate as having none, so it is not retried forever
            try:
                old = (today - datetime.date.fromisoformat(day)).days >= GIVE_UP_DAYS
            except ValueError:
                old = True
            if not old:
                errlog.note("HIND-nopool", msg="DraftKings served no pool", path=f"{event_key}:{dg}")
                continue
            res["why"] = f"DraftKings no longer served this draft group's pool {GIVE_UP_DAYS} days on"
        with store._lock, store._conn() as c:
            c.execute("INSERT OR REPLACE INTO dfs_actuals (sport, event_key, payload, ts) VALUES (?,?,?,?)",
                      ("nfl_hindsight", f"{event_key}:{dg}", json.dumps(res), int(time.time())))
        done += 1
    return done


def report(limit=6):
    """The newest hindsight rows for the look-back card."""
    import dfslog
    import store
    dfslog._ensure()
    with store._lock, store._conn() as c:
        rows = c.execute("SELECT payload FROM dfs_actuals WHERE sport='nfl_hindsight' ORDER BY ts DESC LIMIT ?",
                         (int(limit),)).fetchall()
    return [json.loads(r["payload"]) for r in rows]
